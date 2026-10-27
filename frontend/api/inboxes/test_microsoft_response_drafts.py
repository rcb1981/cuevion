"""Response drafts: isolated CAS and fake Graph only. No send transport exists."""
import copy
import json
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote,urlsplit
from unittest.mock import patch
from api.inboxes import microsoft_response_drafts as service
from api.inboxes import microsoft_compose_draft_service as compose
from api.inboxes import microsoft_compose_draft_ledger as ledger
from api.inboxes import microsoft_compose_draft_http as http
from api.inboxes import microsoft_draft_send_index as send_index
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes.test_microsoft_compose_draft import ComposeFixture,FakeGraph,response,TOKEN
from api.inboxes.test_microsoft_draft_attachment_foundation import AUTH,OP,message,row
from api.inboxes.test_microsoft_http import _body


def recipients(*addresses):return [{'emailAddress':{'address':a}} for a in addresses]


class ResponseGraph(FakeGraph):
    def __init__(self):
        super().__init__()
        self.original=message(id='source-1',isDraft=False,parentFolderId='inbox-folder',conversationId='conversation-1',
            subject='Discussion',from_ignored=None,**{'from':recipients('artist@example.com')[0]},
            toRecipients=recipients(AUTH.email,'other@example.com'),ccRecipients=recipients('manager@example.com','other@example.com'),
            replyTo=recipients('reply@example.com'),hasAttachments=False,
            body={'contentType':'html','content':'<p>Complete original message, including its final paragraph.</p>'})
        self.source_rows=[];self.native_actions=[];self.native_loss=False;self.native_hook=None
    def __call__(self,req,*,timeout):
        method,path=req.get_method(),unquote(urlsplit(req.full_url).path)
        assert '/send' not in path and '/replyAll' not in path and not path.endswith('/reply')
        if path.endswith('/me'):
            self.calls.append(req);return response({'id':AUTH.account_id,'mail':AUTH.email})
        if method=='POST' and path.rsplit('/',1)[-1] in service.MODES.values():
            self.calls.append(req);self.writes.append(req);self.native_actions.append(path.rsplit('/',1)[-1])
            assert json.loads(req.data)=={};assert req.get_header('Prefer')=='IdType="ImmutableId"'
            self.created+=1;identifier='response-'+str(self.created)
            quote='<html><body><blockquote>'+self.original['body']['content']+'</blockquote></body></html>'
            self.messages[identifier]=message(id=identifier,conversationId=self.original['conversationId'],
                body={'contentType':'html','content':quote},hasAttachments=bool(self.source_rows),subject='Re: Discussion')
            self.rows[identifier]=copy.deepcopy(self.source_rows if path.endswith('createForward') else [])
            self.messages[identifier]['hasAttachments']=bool(self.rows[identifier])
            if self.native_hook:self.native_hook(self,identifier)
            if self.native_loss:raise TimeoutError('lost native creation response')
            return response({'id':identifier,'isDraft':True},201)
        if path.endswith('/messages/source-1'):
            self.calls.append(req);return response(copy.deepcopy(self.original))
        if '/attachments' in path:
            identifier=path.split('/messages/')[1].split('/')[0]
            rows=self.source_rows if identifier=='source-1' else self.rows[identifier]
            self.calls.append(req)
            if path.endswith('/attachments'):return response({'value':copy.deepcopy(rows)})
            return response(copy.deepcopy(next(r for r in rows if r['id']==path.rsplit('/',1)[-1])))
        return super().__call__(req,timeout=timeout)


class ResponseFixture(ComposeFixture):
    def setUp(self):
        super().setUp();self.graph=ResponseGraph()
        p=patch.object(service,'_RESPONSE_DRAFTS_RELEASED',True);p.start();self.addCleanup(p.stop)
    def prepare(self,mode='reply'):
        return service.prepare_owned({},AUTH.mailbox_id,'source-1',mode,opener=self.graph)
    def create_response(self,mode='reply',request=OP,edit=None,source_hash=None):
        prepared=self.prepare(mode)
        edit=edit or {**prepared['edit'],'body':'My response <literal> & text'}
        if mode=='forward' and not edit['to']:edit={**edit,'to':['recipient@example.com']}
        return service.save_owned({},AUTH.mailbox_id,request,edit,source_id='source-1',mode=mode,
            source_hash=source_hash or prepared['sourceHash'],opener=self.graph)


class ResponseDraftTests(ResponseFixture):
    def test_reply_reply_all_forward_each_use_native_draft_action_and_reopen(self):
        for n,mode in enumerate(service.MODES):
            result=self.create_response(mode,request=str(n)*43)
            self.assertEqual(result['outcome'],'confirmed_performed')
            draft=compose.load_owned({},AUTH.mailbox_id,result['providerMessageId'],opener=self.graph)
            self.assertEqual(draft['edit']['body'],'My response <literal> & text')
            self.assertEqual(draft['response']['mode'],mode)
            self.assertIn('final paragraph',draft['response']['quotePreview'])
            self.assertEqual(self.graph.messages[result['providerMessageId']]['conversationId'],'conversation-1')
        self.assertEqual(self.graph.native_actions,list(service.MODES.values()))
        self.assertEqual([r.get_method() for r in self.graph.writes],['POST','PATCH']*3)

    def test_recipients_reply_to_all_deduplicate_self_and_never_inherit_bcc(self):
        self.graph.original['bccRecipients']=recipients('secret@example.com')
        self.assertEqual(self.prepare()['edit']['to'],['reply@example.com'])
        self.assertEqual(self.prepare()['edit']['cc'],[])
        all_reply=self.prepare('reply_all')['edit']
        self.assertEqual(all_reply['to'],['reply@example.com'])
        self.assertEqual(all_reply['cc'],['other@example.com','manager@example.com'])
        self.assertEqual(all_reply['bcc'],[])
        forward=self.prepare('forward')['edit'];self.assertEqual(forward['to'],[]);self.assertEqual(forward['cc'],[])

    def test_from_fallback_and_reply_to_own_sent_message(self):
        self.graph.original['replyTo']=[]
        self.assertEqual(self.prepare()['edit']['to'],['artist@example.com'])
        self.graph.original['from']=recipients(AUTH.email)[0]
        self.assertEqual(self.prepare()['edit']['to'],['other@example.com'])

    def test_prepare_is_read_only_and_no_send_permission_requested(self):
        with patch.object(owned,'_owned_token',return_value=(TOKEN,AUTH)) as token:
            self.prepare('reply_all');self.assertEqual(self.graph.writes,[])
            self.create_response();self.assertNotIn('require_mail_send',token.call_args.kwargs)
            self.assertTrue(token.call_args.kwargs.get('require_read_write'))

    def test_update_reopened_comment_and_headers_keeps_entire_quote(self):
        result=self.create_response();identifier=result['providerMessageId']
        first=compose.load_owned({},AUTH.mailbox_id,identifier,opener=self.graph)
        meta=service.provenance(AUTH,identifier)
        before=self.graph.messages[identifier]['body']['content']
        edit={**first['edit'],'subject':'Updated','body':'Changed\n<comment>','cc':['added@example.com']}
        saved=compose.save_owned({},AUTH.mailbox_id,'u'*43,edit,message_id=identifier,etag=first['etag'],opener=self.graph)
        self.assertEqual(saved['outcome'],'confirmed_performed')
        after=compose.load_owned({},AUTH.mailbox_id,identifier,opener=self.graph)
        self.assertEqual(after['edit'],edit)
        self.assertEqual(service.provenance(AUTH,identifier),meta)
        self.assertEqual(self.graph.messages[identifier]['body']['content'][-meta['suffixLength']:],before[-meta['suffixLength']:])

    def test_source_changed_since_preview_never_creates(self):
        prepared=self.prepare();self.graph.original['subject']='Externally changed'
        with self.assertRaises(MicrosoftGraphError):self.create_response(source_hash=prepared['sourceHash'])
        self.assertEqual(self.graph.writes,[])

    def test_source_changes_during_creation_remains_uncertain_no_patch(self):
        self.graph.native_hook=lambda g,i:g.original.update(subject='Outlook race')
        result=self.create_response();self.assertEqual(result['outcome'],'uncertain')
        self.assertEqual(len(self.graph.writes),1)

    def test_lost_creation_reply_never_retries_same_or_new_id_after_reconnect(self):
        self.graph.native_loss=True;result=self.create_response();self.assertEqual(result['outcome'],'uncertain')
        self.graph.native_loss=False
        rotated=replace(AUTH,credential_generation='r'*43)
        with patch.object(owned,'resolve_owned_microsoft_mailbox',return_value=rotated),patch.object(owned,'_owned_token',return_value=(TOKEN,rotated)):
            self.assertEqual(self.create_response()['outcome'],'uncertain')
            with self.assertRaises(MicrosoftGraphError):self.create_response(request='n'*43)
        self.assertEqual(len(self.graph.native_actions),1)

    def test_confirmed_request_replay_and_reconnect_do_not_create_or_patch_again(self):
        first=self.create_response();writes=len(self.graph.writes)
        rotated=replace(AUTH,credential_generation='r'*43)
        with patch.object(owned,'resolve_owned_microsoft_mailbox',return_value=rotated),patch.object(owned,'_owned_token',return_value=(TOKEN,rotated)):
            self.assertEqual(self.create_response(),first)
            self.assertEqual(compose.load_owned({},AUTH.mailbox_id,first['providerMessageId'],opener=self.graph)['response']['mode'],'reply')
        self.assertEqual(len(self.graph.writes),writes)

    def test_lost_patch_and_html_normalization_block_without_sending(self):
        self.graph.lose=True;result=self.create_response();self.assertEqual(result['outcome'],'uncertain')
        writes=len(self.graph.writes)
        self.assertEqual(self.create_response()['outcome'],'uncertain')
        self.assertEqual(len(self.graph.writes),writes)

    def test_changed_protected_quote_is_not_silently_overwritten(self):
        result=self.create_response();identifier=result['providerMessageId']
        loaded=compose.load_owned({},AUTH.mailbox_id,identifier,opener=self.graph)
        self.graph.messages[identifier]['body']['content']=self.graph.messages[identifier]['body']['content'].replace('paragraph','HTML changed')
        with self.assertRaises(MicrosoftGraphError):compose.save_owned({},AUTH.mailbox_id,'u'*43,loaded['edit'],message_id=identifier,etag=loaded['etag'],opener=self.graph)
        self.assertEqual(len(self.graph.writes),2)

    def test_forward_verifies_complete_file_bytes_and_preserves_attachment(self):
        self.graph.source_rows=[row(contentBytes='ZGF0YQ==')];self.graph.original['hasAttachments']=True
        result=self.create_response('forward');self.assertEqual(result['outcome'],'confirmed_performed')
        self.assertEqual(self.graph.rows[result['providerMessageId']],self.graph.source_rows)

    def test_forward_unknown_inline_and_reference_attachments_block_before_create(self):
        for changes in ({'@odata.type':'#microsoft.graph.itemAttachment'},{'@odata.type':'#microsoft.graph.referenceAttachment'}, {'isInline':True}):
            self.graph.source_rows=[row(contentBytes='ZGF0YQ==',**changes)]
            with self.assertRaises(MicrosoftGraphError):self.create_response('forward')
        self.assertEqual(self.graph.writes,[])

    def test_forward_replaced_same_name_same_size_bytes_remains_uncertain(self):
        self.graph.source_rows=[row(contentBytes='ZGF0YQ==')]
        def replace_bytes(g,i):g.rows[i][0]['contentBytes']='ZXZpbA=='
        self.graph.native_hook=replace_bytes
        self.assertEqual(self.create_response('forward')['outcome'],'uncertain')
        self.assertEqual(len(self.graph.writes),1)

    def test_wrong_conversation_is_blocked_before_patch(self):
        self.graph.native_hook=lambda g,i:g.messages[i].update(conversationId='other-conversation')
        self.assertEqual(self.create_response()['outcome'],'uncertain')
        self.assertEqual(len(self.graph.writes),1)

    def test_existing_send_barrier_blocks_source_and_saved_response_edits(self):
        send_index.reserve(AUTH,'s'*43,'source-1','b'*64)
        with self.assertRaises(MicrosoftGraphError):self.create_response()
        self.assertEqual(self.graph.writes,[])

    def test_source_flags_off_and_production_deny_without_graph(self):
        import os
        for env in ({'VERCEL_ENV':'production'},):
            with patch.dict(os.environ,env):
                with self.assertRaises(MicrosoftGraphError):self.create_response()
        with patch.object(service,'_RESPONSE_DRAFTS_RELEASED',False):
            with self.assertRaises(MicrosoftGraphError):self.prepare()
        self.assertEqual(self.graph.calls,[])

    def test_storage_failure_and_private_content_not_in_ledger(self):
        self.create_response()
        raw=json.dumps(self.cas.records)
        for private in ('My response','final paragraph','artist@example.com','reply@example.com','fake-token'):
            self.assertNotIn(private,raw)
        self.cas.bad_read=True
        with self.assertRaises(MicrosoftGraphError):self.create_response(request='n'*43)
        self.assertEqual(len(self.graph.writes),2)

    def test_two_concurrent_same_request_cannot_repeat_native_creation(self):
        prepared=self.prepare();edit={**prepared['edit'],'body':'Response'}
        def run(_):
            try:return service.save_owned({},AUTH.mailbox_id,OP,edit,source_id='source-1',mode='reply',source_hash=prepared['sourceHash'],opener=self.graph)
            except MicrosoftGraphError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(run,[1,2]))
        self.assertEqual(len(self.graph.native_actions),1)
        self.assertEqual(len(self.graph.writes),2)

    def test_http_prepare_create_load_and_strict_payload(self):
        initial={'action':'prepare_response','mailboxId':AUTH.mailbox_id,'mode':'reply','sourceProviderMessageId':'source-1'}
        prepared=_body(self.call(initial));self.assertTrue(prepared['ok'])
        created=_body(self.call({**initial,'action':'create_response','sourceHash':prepared['sourceHash'],
            'requestId':OP,'edit':{**prepared['edit'],'body':'My HTTP reply'}}))
        self.assertTrue(created['ok']);self.assertEqual(created['receipt']['outcome'],'confirmed_performed')
        loaded=_body(self.call({'action':'load','mailboxId':AUTH.mailbox_id,'providerMessageId':created['receipt']['providerMessageId']}))
        self.assertEqual(loaded['edit']['body'],'My HTTP reply')
        self.assertEqual(loaded['response']['mode'],'reply')
        for invalid in ({**initial,'mode':'send'}, {**initial,'accessToken':'forbidden'}):
            self.assertFalse(_body(self.call(invalid))['ok'])

    def test_native_quote_missing_final_paragraph_stays_uncertain(self):
        self.graph.native_hook=lambda g,i:g.messages[i].update(body={'contentType':'html','content':'<p>Truncated original</p>'})
        self.assertEqual(self.create_response('forward')['outcome'],'uncertain')
        self.assertEqual(len(self.graph.writes),1)

    def test_graph_html_normalization_is_not_assumed_equivalent(self):
        def normalize(g,i):g.messages[i]['body']['content']=g.messages[i]['body']['content'].replace('<pre>','<pre style="white-space:pre-wrap">')
        self.graph.on_write=normalize
        self.assertEqual(self.create_response()['outcome'],'uncertain')
        self.assertEqual(len(self.graph.writes),2)
        with self.assertRaises(MicrosoftGraphError):self.create_response(request='n'*43)

    def test_saved_response_send_block_is_checked_before_edit(self):
        result=self.create_response();identifier=result['providerMessageId']
        loaded=compose.load_owned({},AUTH.mailbox_id,identifier,opener=self.graph)
        send_index.reserve(AUTH,'s'*43,identifier,'b'*64)
        with self.assertRaises(MicrosoftGraphError):compose.save_owned({},AUTH.mailbox_id,'u'*43,loaded['edit'],message_id=identifier,etag=loaded['etag'],opener=self.graph)
        self.assertEqual(len(self.graph.writes),2)

    def test_provenance_store_failure_after_create_never_patches_or_recreates(self):
        write=self.cas.write
        def fail_provenance(config,key,raw,value):
            if key.startswith(service.PREFIX):return None,{'code':'offline-store-failure'}
            return write(config,key,raw,value)
        with patch.object(ledger.base.store,'_write_google_durable_record_if_unchanged',side_effect=fail_provenance):
            self.assertEqual(self.create_response()['outcome'],'uncertain')
        self.assertEqual(self.create_response()['outcome'],'uncertain')
        self.assertEqual(len(self.graph.writes),1)

    def test_changed_account_and_lost_lease_prevent_creation(self):
        changed=replace(AUTH,account_id='wrong-account')
        with patch.object(owned,'resolve_owned_microsoft_mailbox',return_value=changed),patch.object(owned,'_owned_token',return_value=(TOKEN,changed)):
            with self.assertRaises(MicrosoftGraphError):self.prepare()
        with patch.object(service.executor,'_assert_lease',side_effect=MicrosoftGraphError('microsoft_mutation_in_progress')):
            with self.assertRaises(MicrosoftGraphError):self.create_response()
        self.assertEqual(self.graph.writes,[])

    def test_reply_and_forward_subject_prefixes_are_mode_specific(self):
        self.graph.original['subject']='Re: Discussion'
        self.assertEqual(self.prepare()['edit']['subject'],'Re: Discussion')
        self.assertEqual(self.prepare('forward')['edit']['subject'],'Fw: Re: Discussion')

    def test_forward_bytes_changed_during_header_patch_are_not_confirmed(self):
        self.graph.source_rows=[row(contentBytes='ZGF0YQ==')]
        def replace_bytes(g,i):g.rows[i][0]['contentBytes']='ZXZpbA=='
        self.graph.on_write=replace_bytes
        self.assertEqual(self.create_response('forward')['outcome'],'uncertain')
        self.assertEqual(len(self.graph.writes),2)

    def test_native_html_quoted_attribute_does_not_move_comment_into_an_attribute(self):
        def quoted_body(g,i):
            g.messages[i]['body']['content']=g.messages[i]['body']['content'].replace('<body>','<body title="a > b">')
        self.graph.native_hook=quoted_body
        result=self.create_response();self.assertEqual(result['outcome'],'confirmed_performed')
        body=self.graph.messages[result['providerMessageId']]['body']['content']
        self.assertIn('<body title="a > b"><div><pre>My response &lt;literal&gt;',body)
        loaded=compose.load_owned({},AUTH.mailbox_id,result['providerMessageId'],opener=self.graph)
        self.assertEqual(loaded['edit']['body'],'My response <literal> & text')
