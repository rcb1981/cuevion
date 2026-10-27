"""Compose completion: isolated fake Graph and CAS, including legacy migration."""
import copy
import json
import threading
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from api.inboxes.test_microsoft_compose_draft import ComposeFixture, AUTH, OP, EDIT, TAG, TOKEN
from api.inboxes.test_microsoft_response_drafts import ResponseFixture
from api.inboxes import microsoft_compose_draft_ledger as ledger
from api.inboxes import microsoft_compose_draft_service as service
from api.inboxes import microsoft_compose_draft_graph as graph
from api.inboxes import microsoft_compose_html as rich
from api.inboxes import microsoft_response_drafts as responses
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes.microsoft_draft_attachment_snapshot import authority_digest, digest


def rid(n):return ('operation_'+str(n)).ljust(43,'_')
HTML={**EDIT,'body':'<div>Hello <strong>world</strong><br><em>Music</em></div><ul><li>One</li></ul>','bodyType':'html'}


class SegmentedComposeTests(ComposeFixture):
    def legacy(self, pending=True):
        config,key,scope=ledger.context(AUTH)
        operations={rid(n):{'action':'update','draftId':'legacy-'+str(n),'binding':digest(n),
            'authority':authority_digest(AUTH),'claim':digest('claim-'+str(n)),
            'phase':'started' if n==0 and pending else 'confirmed_performed'} for n in range(32)}
        root={'v':1,'scope':scope,'operations':operations}
        self.cas.records[key]=json.dumps(root,sort_keys=True,separators=(',',':'))
        return copy.deepcopy(root)

    def test_120_sequential_service_saves_paginate_and_replay_first_after_restart(self):
        first=None
        for n in range(120):
            result=self.save(request=rid(n))
            self.assertEqual(result['outcome'],'confirmed_performed')
            first=first or result
        self.assertEqual(self.graph.created,120)
        self.assertEqual(ledger.read(AUTH)[0]['count'],120)
        pages=[];cursor=None
        while True:
            page=ledger.page(AUTH,cursor);self.assertLessEqual(len(page['operations']),32)
            pages.insert(0,[x['requestId'] for x in page['operations']]);cursor=page['nextCursor']
            if cursor is None:break
        self.assertEqual(sum(pages,[]),[rid(n) for n in range(120)])
        # New storage snapshot / no in-process cache or claim required.
        self.cas.records=copy.deepcopy(self.cas.records)
        calls=len(self.graph.calls)
        self.assertEqual(self.save(request=rid(0)),first)
        self.assertEqual(len(self.graph.calls),calls)
        self.assertLess(len(self.cas.records[ledger.context(AUTH)[1]]),1024)

    def test_migration_preserves_all_records_archive_and_old_unknown_barrier(self):
        original=self.legacy()
        result=self.save();self.assertEqual(result['outcome'],'confirmed_performed')
        root=ledger.read(AUTH)[0]
        self.assertEqual(ledger.segments.segment(AUTH,root['legacy'])['original'],original)
        for identifier,item in original['operations'].items():self.assertEqual(ledger.entry(AUTH,identifier)['item'],item)
        for n in range(50):self.save(request=rid(100+n))
        with self.assertRaises(MicrosoftGraphError):ledger.assert_unblocked(AUTH,'legacy-0')
        page=ledger.page(AUTH,draft_id='legacy-0')
        self.assertTrue(any(x['requestId']==rid(0) and x['blocked'] for x in page['operations']))
        self.assertEqual(ledger.lookup(AUTH,rid(1),digest(1))['outcome'],'confirmed_performed')
        with self.assertRaises(MicrosoftGraphError):ledger.reserve(AUTH,rid(1),'create',None,digest('different'))

    def test_interrupted_migration_keeps_old_root_then_can_resume(self):
        original=self.legacy();key=ledger.context(AUTH)[1];raw=self.cas.records[key]
        real=self.cas.write
        def fail_publish(config,k,expected,value):
            if k==key:return None,{'code':'unavailable'}
            return real(config,k,expected,value)
        with patch.object(ledger.base.store,'_write_google_durable_record_if_unchanged',side_effect=fail_publish):
            with self.assertRaises(MicrosoftGraphError):self.save()
        self.assertEqual(self.cas.records[key],raw);self.assertEqual(self.graph.writes,[])
        self.assertEqual(ledger.read(AUTH)[0],original)
        self.assertEqual(self.save()['outcome'],'confirmed_performed')
        with self.assertRaises(MicrosoftGraphError):ledger.assert_unblocked(AUTH,'legacy-0')

    def test_segment_failure_before_publication_keeps_legacy(self):
        original=self.legacy();real=self.cas.write;count=0
        def fail_midway(config,key,expected,value):
            nonlocal count
            count+=1
            if count==8:return None,{'code':'offline'}
            return real(config,key,expected,value)
        with patch.object(ledger.base.store,'_write_google_durable_record_if_unchanged',side_effect=fail_midway):
            with self.assertRaises(MicrosoftGraphError):self.save()
        self.assertEqual(ledger.read(AUTH)[0],original);self.assertEqual(self.graph.writes,[])
        self.assertEqual(self.save()['outcome'],'confirmed_performed')

    def test_lost_root_reserve_ack_never_authorizes_graph_or_replay(self):
        real=self.cas.write;key=ledger.context(AUTH)[1]
        def lose(config,k,expected,value):
            result=real(config,k,expected,value)
            return (None,{'code':'lost'}) if k==key else result
        with patch.object(ledger.base.store,'_write_google_durable_record_if_unchanged',side_effect=lose):
            with self.assertRaises(MicrosoftGraphError):self.save()
        self.assertEqual(self.graph.writes,[])
        self.assertTrue(self.save()['blocked']);self.assertEqual(self.graph.writes,[])

    def test_missing_segment_or_root_fails_closed(self):
        self.save();snapshot=copy.deepcopy(self.cas.records);root,key=ledger.read(AUTH)[0],ledger.context(AUTH)[1]
        for missing in (key,ledger.segments._segment_key(root['scope'],root['operations']),ledger.segments._segment_key(root['scope'],root['barriers'])):
            self.cas.records=copy.deepcopy(snapshot);del self.cas.records[missing]
            with self.assertRaises(MicrosoftGraphError):self.save(request=rid(2))
        self.assertEqual(self.graph.created,1)

    def test_concurrent_migration_cas_publishes_only_one_claim(self):
        self.legacy();self.cas.race=threading.Barrier(2)
        def attempt(n):
            try:return ledger.reserve(AUTH,rid(100+n),'create',None,digest(n))
            except MicrosoftGraphError:return None
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,[0,1]))
        self.cas.race=None
        self.assertEqual(sum(bool(x and x.get('new')) for x in results),1)
        self.assertEqual(ledger.read(AUTH)[0]['count'],33)
        with self.assertRaises(MicrosoftGraphError):ledger.assert_unblocked(AUTH,'legacy-0')

    def test_reconnect_retains_unknown_and_confirmed_history(self):
        self.legacy();self.save();reconnected=replace(AUTH,credential_generation='n'*43)
        self.assertEqual(ledger.operation(reconnected,OP)['outcome'],'confirmed_performed')
        self.assertTrue(ledger.operation(reconnected,rid(0))['blocked'])
        with self.assertRaises(MicrosoftGraphError):ledger.assert_unblocked(reconnected,'legacy-0')
        with self.assertRaises(MicrosoftGraphError):ledger.read(replace(AUTH,account_id='other-account'))

    def test_legacy_unknown_create_still_blocks_all_creates(self):
        original=self.legacy();original['operations'][rid(0)].update(action='create',draftId=None)
        self.cas.records[ledger.context(AUTH)[1]]=json.dumps(original)
        with self.assertRaises(MicrosoftGraphError):self.save()
        self.assertEqual(self.graph.writes,[])


class HtmlComposeTests(ComposeFixture):
    def test_html_create_save_reopen_with_safe_wrapper_and_aliases(self):
        result=self.save(HTML);self.assertEqual(result['outcome'],'confirmed_performed')
        identifier=result['providerMessageId']
        draft=service.load_owned({},AUTH.mailbox_id,identifier,opener=self.graph)
        self.assertEqual(draft['edit'],HTML)
        self.graph.messages[identifier]['body']['content']='<html><head><meta http-equiv="Content-Type" content="text/html; charset=utf-8"></head><body>'+HTML['body'].replace('strong','b')+'</body></html>'
        self.assertEqual(service.load_owned({},AUTH.mailbox_id,identifier,opener=self.graph)['edit'],HTML)
        edit={**HTML,'body':'<p><u>Updated</u> paragraph</p>'}
        self.assertEqual(self.save(edit,identifier,rid(2),draft['etag'])['outcome'],'confirmed_performed')
        self.assertEqual(service.load_owned({},AUTH.mailbox_id,identifier,opener=self.graph)['edit'],edit)

    def test_legacy_plain_draft_converts_without_losing_attachments_or_conversation(self):
        self.graph.messages['draft-1']['conversationId']='conversation-original'
        before=copy.deepcopy(self.graph.rows['draft-1'])
        self.assertNotIn('bodyType',service.load_owned({},AUTH.mailbox_id,'draft-1',opener=self.graph)['edit'])
        result=self.save(HTML,'draft-1',etag=TAG)
        self.assertEqual(result['outcome'],'confirmed_performed')
        self.assertEqual(self.graph.rows['draft-1'],before)
        self.assertEqual(self.graph.messages['draft-1']['conversationId'],'conversation-original')
        self.assertEqual(service.load_owned({},AUTH.mailbox_id,'draft-1',opener=self.graph)['edit'],HTML)
        self.assertNotIn(HTML['body'],json.dumps(self.cas.records))

    def test_unsafe_html_rejected_before_storage_or_graph(self):
        for body in ('<script>alert(1)</script>','<img src="cid:x">','<svg><script>x</script></svg>',
            '<div onclick="x">x</div>','<a href="javascript:alert(1)">x</a>','<a href="&#106;avascript:x">x</a>',
            '<div style="display:none">x</div>','<iframe src="https://example.com"></iframe>',
            '<!--hidden-->','<div><p>x</div>','<div data-x="a">x</div>'):
            with self.subTest(body=body),self.assertRaises(MicrosoftGraphError):self.save({**HTML,'body':body})
        self.assertEqual(self.graph.calls,[]);self.assertEqual(self.cas.records,{})

    def test_send_reader_remains_strict_and_external_html_not_silently_adopted(self):
        identifier=self.save(HTML)['providerMessageId']
        with self.assertRaises(MicrosoftGraphError):graph.read_current(TOKEN,AUTH,identifier,opener=self.graph)
        self.graph.messages['draft-1']['body']={'contentType':'html','content':HTML['body']}
        with self.assertRaises(MicrosoftGraphError):service.load_owned({},AUTH.mailbox_id,'draft-1',opener=self.graph)

    def test_unknown_provider_normalization_stays_uncertain_and_blocks_retry(self):
        self.graph.on_write=lambda g,i:g.messages[i]['body'].update(content='<div style="color:red">changed</div>')
        result=self.save(HTML);self.assertTrue(result['blocked'])
        with self.assertRaises(MicrosoftGraphError):self.save(HTML,request=rid(2))
        self.assertEqual(len(self.graph.writes),1)

    def test_plain_text_projection_cannot_prove_changed_html(self):
        self.graph.on_write=lambda g,i:g.messages[i]['body'].update(content=HTML['body'].replace('strong','em'))
        self.assertTrue(self.save(HTML)['blocked'])
        self.assertEqual(len(self.graph.writes),1)

    def test_html_reconnect_and_storage_loss_never_repeats_save(self):
        result=self.save(HTML);identifier=result['providerMessageId'];writes=len(self.graph.writes)
        new=replace(AUTH,credential_generation='n'*43)
        with patch.object(owned,'_owned_token',return_value=(TOKEN,new)):
            self.assertEqual(service.load_owned({},AUTH.mailbox_id,identifier,opener=self.graph)['edit'],HTML)
        self.cas.bad_read=True
        with self.assertRaises(MicrosoftGraphError):service.load_owned({},AUTH.mailbox_id,identifier,opener=self.graph)
        self.assertEqual(len(self.graph.writes),writes)

    def test_mutated_attachment_or_conversation_never_confirmed(self):
        for field in ('attachment','conversation'):
            self.cas.records.clear();self.graph.messages['draft-1']['@odata.etag']=TAG
            self.graph.messages['draft-1']['body']={'contentType':'text','content':'Old body'}
            self.graph.messages['draft-1']['conversationId']='original'
            def alter(g,i):
                if field=='attachment':g.rows[i][0]['name']='changed'
                else:g.messages[i]['conversationId']='changed'
            self.graph.on_write=alter
            self.assertTrue(self.save(HTML,'draft-1',etag=TAG)['blocked'])


class HtmlResponsesTests(ResponseFixture):
    def test_rich_reply_reply_all_forward_preserve_exact_quote_and_context(self):
        for n,mode in enumerate(('reply','reply_all','forward')):
            prepared=self.prepare(mode);edit={**prepared['edit'],'body':HTML['body'],'bodyType':'html'}
            if mode=='forward':edit['to']=['recipient@example.com']
            result=self.create_response(mode,rid(n),edit);self.assertEqual(result['outcome'],'confirmed_performed')
            identifier=result['providerMessageId'];draft=service.load_owned({},AUTH.mailbox_id,identifier,opener=self.graph)
            self.assertEqual(draft['edit'],edit)
            meta=responses.provenance(AUTH,identifier)
            _,prefix,suffix,_=responses._split(self.graph.messages[identifier],meta)
            updated={**edit,'body':'<p>Second <em>comment</em></p>'}
            self.assertEqual(self.save(updated,identifier,rid(n+10),draft['etag'])['outcome'],'confirmed_performed')
            _,left,right,_=responses._split(self.graph.messages[identifier],meta)
            self.assertEqual((left,right),(prefix,suffix))
            self.assertEqual(self.graph.messages[identifier]['conversationId'],meta['conversationId'])

    def test_older_plain_response_can_reopen_then_gain_formatting(self):
        identifier=self.create_response()['providerMessageId']
        before=service.load_owned({},AUTH.mailbox_id,identifier,opener=self.graph)
        self.assertNotIn('bodyType',before['edit'])
        edit={**before['edit'],'body':'<p>Formatted <strong>reply</strong></p>','bodyType':'html'}
        self.assertEqual(self.save(edit,identifier,rid(2),before['etag'])['outcome'],'confirmed_performed')
        self.assertEqual(service.load_owned({},AUTH.mailbox_id,identifier,opener=self.graph)['edit'],edit)

    def test_quote_tampering_after_rich_save_blocks_following_edits(self):
        prepared=self.prepare();edit={**prepared['edit'],'body':HTML['body'],'bodyType':'html'}
        identifier=self.create_response(edit=edit)['providerMessageId']
        draft=service.load_owned({},AUTH.mailbox_id,identifier,opener=self.graph)
        self.graph.messages[identifier]['body']['content']=self.graph.messages[identifier]['body']['content'].replace('final paragraph','changed paragraph')
        writes=len(self.graph.writes)
        with self.assertRaises(MicrosoftGraphError):self.save(edit,identifier,rid(3),draft['etag'])
        self.assertEqual(len(self.graph.writes),writes)
