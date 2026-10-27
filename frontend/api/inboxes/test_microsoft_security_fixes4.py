"""Security Fixes 4: fake Graph, real isolated Send index, no external I/O."""
import copy
import json
from dataclasses import replace
from email.message import Message
from unittest.mock import patch
from api.inboxes.test_microsoft_draft_send import SendFixture, SendGraph, AUTH, OP, TOKEN, service, index, policy
from api.inboxes.test_microsoft_compose_draft import response, body_response
from api.inboxes import microsoft_compose_body_contract as bodies
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes import microsoft_message_flags as flags
from api.inboxes import microsoft_archive_move as archive
from api.inboxes import microsoft_outbound_ledger as outbound
from api.inboxes import microsoft_draft_send_manifest as manifest
from api.inboxes.microsoft_graph import MicrosoftGraphError

REAL_FLAG = flags.set_exact_microsoft_inbox_read

class InboxGraph:
    def __init__(self, unread=False, on_read=None):
        self.calls=[]; self.read=unread; self.folder='inbox-folder'; self.on_read=on_read
    def __call__(self, req, **kw):
        self.calls.append(req); url=req.full_url; method=req.get_method()
        if '/messages/' not in url:
            return response({'id':'archive-folder' if '/archive?' in url else 'deleted-folder'})
        if method=='POST':
            self.folder='archive-folder' if b'archive' in req.data else 'deleted-folder'
        if method=='PATCH':self.read=json.loads(req.data)['isRead']
        if method=='GET' and '/inbox/' in url and self.on_read:
            fn,self.on_read=self.on_read,None;fn()
        return response({'id':'draft-1','parentFolderId':self.folder,'isRead':self.read},201 if method=='POST' else 200)


class InboxBarrierTests(SendFixture):
    def action(self, kind, graph):
        with patch.object(archive._GRAPH_OPENER, 'open', side_effect=graph), patch.object(
            flags,'set_exact_microsoft_inbox_read',side_effect=lambda *a,**kw:REAL_FLAG(*a,**kw,opener=graph)):
            if kind in ('read','unread'):
                return owned.mark_owned_microsoft_inbox_read({},AUTH.mailbox_id,'draft-1',unread=kind=='unread')
            return getattr(owned,kind+'_owned_microsoft_inbox_message')({},AUTH.mailbox_id,'draft-1')

    def test_external_inbox_move_unknown_and_mismatch_block_all_mutations_after_reconnect_and_shutdown(self):
        for outcome in ('uncertain','performed_content_mismatch'):
            self.cas.records.clear();self.graph=SendGraph()
            self.graph.visible=outcome!='uncertain'
            if outcome!='uncertain':self.graph.on_send=lambda g:g.messages['draft-1'].update({'subject':'changed'})
            self.assertEqual(self.send()['outcome'],outcome)
            fresh=replace(AUTH,credential_generation='z'*43)
            # Same immutable ID now exists in Inbox; fresh credentials never erase its barrier.
            with patch.object(policy,'_DRAFT_SEND_RELEASED',False), patch.object(owned,'resolve_owned_microsoft_mailbox',return_value=fresh), patch.object(owned,'_owned_token',return_value=(TOKEN,fresh)):
                for kind in ('read','unread','archive','delete'):
                    graph=InboxGraph(unread=kind=='unread')
                    with self.assertRaises(MicrosoftGraphError) as error:self.action(kind,graph)
                    self.assertEqual(error.exception.code,'microsoft_draft_send_blocked')
                    self.assertEqual(graph.calls,[])
                self.assertEqual(service.history_owned({},AUTH.mailbox_id)['entries'][0]['receipt']['outcome'],outcome)

    def test_normal_inbox_mutations_still_execute_exactly_once(self):
        for kind in ('read','unread','archive','delete'):
            graph=InboxGraph(unread=kind=='unread')
            self.action(kind,graph)
            self.assertEqual(len([c for c in graph.calls if c.get_method()!='GET']),1)

    def test_new_barrier_during_graph_preflight_blocks_every_write(self):
        for kind in ('read','unread','archive','delete'):
            self.cas.records.clear()
            graph=InboxGraph(unread=kind=='unread',on_read=lambda:index.reserve(AUTH,OP,'draft-1','a'*64))
            with self.assertRaises(MicrosoftGraphError):self.action(kind,graph)
            self.assertFalse(any(c.get_method()!='GET' for c in graph.calls))

    def test_unreadable_index_blocks_inbox_mutations_before_graph(self):
        for kind in ('read','unread','archive','delete'):
            graph=InboxGraph()
            with patch.object(outbound.store,'_read_durable_record_snapshot',return_value=(None,None,{'code':'unavailable'})):
                with self.assertRaises(MicrosoftGraphError):self.action(kind,graph)
            self.assertEqual(graph.calls,[])

    def test_lost_lease_after_graph_preflight_blocks_mutation(self):
        from api.inboxes import microsoft_draft_attachment_executor as executor
        for kind in ('read','unread','archive','delete'):
            graph=InboxGraph(unread=kind=='unread')
            with patch.object(executor,'_assert_lease',side_effect=[None,RuntimeError('expired')]):
                with self.assertRaises(owned.MicrosoftMailboxAccessError):self.action(kind,graph)
            self.assertFalse(any(c.get_method()!='GET' for c in graph.calls))

    def test_other_mailbox_identity_cannot_treat_history_as_empty(self):
        self.graph.visible=False;self.send()
        wrong=replace(AUTH,account_id='different-account')
        with patch.object(owned,'resolve_owned_microsoft_mailbox',return_value=wrong), patch.object(owned,'_owned_token',return_value=(TOKEN,wrong)):
            with self.assertRaises(MicrosoftGraphError):self.action('delete',InboxGraph())


class BodyContractTests(SendFixture):
    def load(self, opener=None):
        return service.drafts.read_current(TOKEN,AUTH,'draft-1',opener=opener or self.graph)

    def test_default_html_with_confirmed_text_projection_and_native_mime_can_send(self):
        current=self.load()
        self.assertEqual(current.snapshot.body_type,'html')
        self.assertEqual(current.edit['body'],'Old body')
        self.assertTrue(any('outlook.body-content-type="text"' in r.get_header('Prefer') for r in self.graph.calls))
        self.assertTrue(any(r.full_url.endswith('/$value') for r in self.graph.calls))
        self.assertEqual(self.send()['outcome'],'confirmed_performed')
        entry=index.get_item(AUTH,OP);evidence,_=manifest.read(AUTH,OP,entry)
        self.assertEqual(evidence['approved']['nativeTextHash'],manifest.snapshots.digest('Old body'))
        self.assertNotIn('Old body',json.dumps(self.cas.records))

    def test_missing_ignored_or_conflicting_text_preference_blocks_with_explicit_reason(self):
        for mode in ('missing','ignored','conflicting','wrong-type'):
            def opener(req,**kw):
                result=self.graph(req,**kw)
                if 'outlook.body-content-type' in req.get_header('Prefer'):
                    if mode=='missing':result.headers=Message()
                    if mode=='conflicting':result.headers['Preference-Applied']='outlook.body-content-type="html"'
                    if mode in ('ignored','wrong-type'):
                        data=json.loads(result.body);data['body']['contentType']='html';result=response(data)
                        if mode=='wrong-type':
                            result.headers=Message();result.headers['Preference-Applied']='outlook.body-content-type="text"'
                return result
            with self.subTest(mode=mode),self.assertRaises(MicrosoftGraphError) as error:self.load(opener)
            self.assertEqual(error.exception.code,bodies.CODE)
        self.assertEqual(self.graph.sends,[])

    def test_native_html_even_with_same_visible_text_is_not_plain_text_proof(self):
        for content in ('<p>Old body</p>','<a href="https://different.invalid">Old body</a>'):
            self.graph.messages['draft-1']['body']={'contentType':'html','content':content}
            with self.assertRaises(MicrosoftGraphError) as error:self.load()
            self.assertEqual(error.exception.code,bodies.CODE)
        self.assertEqual(self.graph.writes,[])

    def test_body_rejection_is_durable_not_started_and_safe_after_reload_reconnect(self):
        self.graph.messages['draft-1']['body']={'contentType':'html','content':'<p>Old body</p>'}
        result=self.send()
        self.assertEqual(result['outcome'],'confirmed_not_started')
        self.assertEqual(result['reason'],bodies.CODE)
        self.assertEqual(self.graph.sends,[])
        fresh=replace(AUTH,credential_generation='z'*43)
        with patch.object(owned,'resolve_owned_microsoft_mailbox',return_value=fresh),patch.object(policy,'_DRAFT_SEND_RELEASED',False):
            self.assertEqual(service.check_owned({},AUTH.mailbox_id,OP),result)
        # No automatic retry or new claim during history/status reads.
        self.assertEqual(self.graph.sends,[])

    def test_create_normalized_to_native_html_keeps_uncertain_claim_and_explains_body(self):
        from api.inboxes import microsoft_compose_draft_service as compose
        from api.inboxes.test_microsoft_compose_draft import EDIT
        # Use ordinary Compose fake: SendGraph only models the existing draft ID.
        from api.inboxes.test_microsoft_compose_draft import FakeGraph
        graph=FakeGraph()
        graph.on_write=lambda g,i:g.messages[i].update({'body':{'contentType':'html','content':'<p>normalized</p>'}})
        with self.assertRaises(MicrosoftGraphError) as error:
            compose.save_owned({},AUTH.mailbox_id,'c'*43,EDIT,opener=graph)
        self.assertEqual(error.exception.code,'microsoft_compose_body_unconfirmed')
        self.assertEqual(compose.inspect_owned({},AUTH.mailbox_id)[0]['outcome'],'uncertain')
        self.assertEqual(compose.save_owned({},AUTH.mailbox_id,'c'*43,EDIT,opener=graph)['outcome'],'uncertain')
        self.assertEqual(graph.created,1)

    def test_false_text_projection_does_not_override_native_mime(self):
        def opener(req,**kw):
            result=self.graph(req,**kw)
            if 'outlook.body-content-type' in req.get_header('Prefer'):
                data=json.loads(result.body);data['body']['content']='Different text';result.body=json.dumps(data).encode()
            return result
        with self.assertRaises(MicrosoftGraphError):self.load(opener)

    def test_full_html_change_with_identical_projection_invalidates_review(self):
        original=body_response
        def rewrite(req,value):
            result=original(req,value)
            if not req.full_url.endswith('/$value') and 'outlook.body-content-type' not in req.get_header('Prefer'):
                data=json.loads(result.body);data['body']['content']=data['body']['content'].replace('<pre>','<pre data-changed="true">');result.body=json.dumps(data).encode()
            return result
        from api.inboxes import test_microsoft_compose_draft as fixture
        with patch.object(fixture,'body_response',side_effect=rewrite):
            self.assertEqual(self.send()['outcome'],'confirmed_not_started')
        self.assertEqual(self.graph.sends,[])

    def test_sent_html_normalization_is_mismatch_not_silent_equivalence_after_reload_reconnect(self):
        original=body_response
        def rewrite(req,value):
            result=original(req,value)
            if '/sentitems/messages/' in req.full_url and not req.full_url.endswith('/$value') and 'outlook.body-content-type' not in req.get_header('Prefer'):
                data=json.loads(result.body);data['body']['content']=data['body']['content'].replace('<pre>','<pre data-normalized="true">');result.body=json.dumps(data).encode()
            return result
        from api.inboxes import test_microsoft_compose_draft as fixture
        with patch.object(fixture,'body_response',side_effect=rewrite):
            self.assertEqual(self.send()['outcome'],'performed_content_mismatch')
        fresh=replace(AUTH,credential_generation='z'*43)
        with patch.object(owned,'resolve_owned_microsoft_mailbox',return_value=fresh),patch.object(policy,'_DRAFT_SEND_RELEASED',False):
            self.assertEqual(service.check_owned({},AUTH.mailbox_id,OP)['outcome'],'performed_content_mismatch')
            with self.assertRaises(MicrosoftGraphError):index.assert_mutable(fresh,'draft-1')
        self.assertEqual(len(self.graph.sends),1)

    def test_unconfirmed_sent_projection_is_uncertain_then_recovers_without_send(self):
        self.graph.visible=False;self.assertEqual(self.send()['outcome'],'uncertain');self.graph.visible=True
        original=body_response
        def missing(req,value):
            result=original(req,value)
            if '/sentitems/messages/' in req.full_url and 'outlook.body-content-type' in req.get_header('Prefer'):result.headers=Message()
            return result
        from api.inboxes import test_microsoft_compose_draft as fixture
        with patch.object(fixture,'body_response',side_effect=missing):
            self.assertEqual(service.check_owned({},AUTH.mailbox_id,OP)['outcome'],'uncertain')
        self.assertEqual(service.check_owned({},AUTH.mailbox_id,OP)['outcome'],'confirmed_performed')
        self.assertEqual(len(self.graph.sends),1)

    def test_old_pending_manifest_is_readable_but_never_retroactively_proved(self):
        self.graph.visible=False;self.send();self.graph.visible=True
        key=manifest.PREFIX+manifest.snapshots.digest([AUTH.owner_email,AUTH.mailbox_id,OP])
        old=json.loads(self.cas.records[key]);del old['approved']['nativeTextHash'];self.cas.records[key]=json.dumps(old)
        self.assertEqual(service.check_owned({},AUTH.mailbox_id,OP)['outcome'],'uncertain')
        with self.assertRaises(MicrosoftGraphError):index.assert_mutable(AUTH,'draft-1')

    def test_mime_parser_refuses_alternatives_nested_malformed_oversized_and_duplicate_headers(self):
        samples=[b'Content-Type: text/html\r\n\r\ntext',b'Content-Type: multipart/alternative; boundary=x\r\n\r\n--x--',
            b'Content-Type: text/plain\r\nContent-Type: text/html\r\n\r\ntext',
            b'Content-Type: text/plain\r\nContent-Transfer-Encoding: base64\r\n\r\n!!!!!',
            b'Content-Type: text/plain\r\nContent-Transfer-Encoding: quoted-printable\r\n\r\n=ZZ',
            b'Content-Type: multipart/mixed; boundary=x\r\n\r\n'+b'--x\r\n\r\n'*14+b'--x--',
            b'x'*(bodies.MAX_MIME_BYTES+1)]
        for raw in samples:
            with self.subTest(sample=raw[:60]),self.assertRaises(MicrosoftGraphError):bodies.native_text(raw)

    def test_mime_plain_body_and_flat_file_parts_are_accepted_without_flattening_html(self):
        raw=b'Content-Type: multipart/mixed; boundary=x\r\n\r\n--x\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nHello\r\nworld\r\n--x\r\nContent-Type: application/octet-stream\r\nContent-Disposition: attachment; filename=x.bin\r\n\r\nbytes\r\n--x--\r\n'
        self.assertEqual(bodies.native_text(raw),'Hello\nworld')
