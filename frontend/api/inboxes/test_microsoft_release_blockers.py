"""Release blocker regressions: fake Graph, isolated CAS, no live requests."""
import copy
import json
import time
from dataclasses import replace
from unittest.mock import patch
from api.inboxes.test_microsoft_draft_acceptance import (
    AcceptanceFixture, acceptance, compose, responses, ledger, owned, HEADERS, AUTH, OP, EDIT,
)
from api.inboxes import microsoft_compose_draft_graph as drafts
from api.inboxes import microsoft_draft_attachment_executor as executor
from api.inboxes import microsoft_message_flags as flags
from api.inboxes import microsoft_archive_move as archive
from api.inboxes.test_microsoft_compose_draft import response
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api import user_config_store

REAL_LEASE = executor._assert_lease
REAL_FLAG = flags.set_exact_microsoft_inbox_read


class MovedInboxGraph:
    """Same immutable ID in Inbox after an external move; no real Graph I/O."""
    def __init__(self, identifier, unread=False, on_read=None):
        self.identifier=identifier;self.is_read=unread;self.folder='inbox-folder'
        self.calls=[];self.on_read=on_read
    def __call__(self, req, **kwargs):
        self.calls.append(req);method=req.get_method()
        if '/messages/' not in req.full_url:
            return response({'id':'archive-folder' if '/archive?' in req.full_url else 'deleted-folder'})
        if method=='GET' and self.on_read:
            fn,self.on_read=self.on_read,None;fn()
        if method=='PATCH':self.is_read=json.loads(req.data)['isRead']
        if method=='POST':self.folder='archive-folder' if b'archive' in req.data else 'deleted-folder'
        return response({'id':self.identifier,'parentFolderId':self.folder,'isRead':self.is_read},201 if method=='POST' else 200)
    @property
    def writes(self):return [r for r in self.calls if r.get_method()!='GET']


def inbox_action(kind, graph):
    with patch.object(archive._GRAPH_OPENER,'open',side_effect=graph), patch.object(
        flags,'set_exact_microsoft_inbox_read',side_effect=lambda *a,**kw:REAL_FLAG(*a,**kw,opener=graph)):
        if kind in ('read','unread'):
            return owned.mark_owned_microsoft_inbox_read(HEADERS,AUTH.mailbox_id,graph.identifier,unread=kind=='unread')
        return getattr(owned,kind+'_owned_microsoft_inbox_message')(HEADERS,AUTH.mailbox_id,graph.identifier)


class AcceptanceDeadlineTests(AcceptanceFixture):
    def started(self):
        item=ledger.entry(self.active,OP)
        return item is not None and item['item']['phase']=='started'

    def test_deadline_expires_during_final_ledger_read(self):
        real_read=ledger.read;real_checkpoint=acceptance.Permit.checkpoint
        clock=[time.time()];inside=[False];delayed=[]
        def checkpoint(permit,**kw):
            inside[0]=True
            try:return real_checkpoint(permit,**kw)
            finally:inside[0]=False
        def read(authority):
            result=real_read(authority)
            if inside[0] and not delayed and result[0]['v']==2:
                item=ledger.entry(authority,OP,result[0])
                if item and item['item']['phase']=='started':
                    delayed.append(True);clock[0]=self.window['expiresAt']
            return result
        with patch.object(acceptance.Permit,'checkpoint',checkpoint),patch.object(ledger,'read',read),patch.object(time,'time',side_effect=lambda:clock[0]):
            result=self.save_trial()
        self.assertTrue(delayed);self.assertEqual(self.graph.writes,[]);self.assertTrue(result['blocked'])
        self.assertTrue(compose.status_owned(HEADERS,AUTH.mailbox_id)['operations'][0]['blocked'])
        # A fresh ID cannot retry the locally uncertain claim after reload.
        self.cas.records=copy.deepcopy(self.cas.records);self.blocked(request='p'*43)

    def test_deadline_expires_during_lease_check(self):
        clock=[time.time()];delayed=[]
        def lease(*args):
            if self.started():delayed.append(True);clock[0]=self.window['expiresAt']
        with patch.object(executor,'_assert_lease',side_effect=lease),patch.object(time,'time',side_effect=lambda:clock[0]):
            result=self.save_trial()
        self.assertTrue(delayed);self.assertEqual(self.graph.writes,[]);self.assertTrue(result['blocked'])

    def test_deadline_rechecked_at_graph_dispatch_after_callback(self):
        clock=[time.time()];real=drafts.write_once
        def delayed(*args,**kw):
            before=kw['before_write']
            def callback():before();clock[0]=self.window['expiresAt']
            kw['before_write']=callback
            return real(*args,**kw)
        with patch.object(drafts,'write_once',side_effect=delayed),patch.object(time,'time',side_effect=lambda:clock[0]):
            result=self.save_trial()
        self.assertEqual(self.graph.writes,[]);self.assertTrue(result['blocked'])

    def response_expiry(self, mode, method):
        prepared=responses.prepare_owned(HEADERS,AUTH.mailbox_id,'source-1',mode,opener=self.graph)
        clock=[time.time()];real=responses.Request;delayed=[]
        def request(*args,**kw):
            result=real(*args,**kw)
            if kw.get('method')==method:delayed.append(True);clock[0]=self.window['expiresAt']
            return result
        with patch.object(responses,'Request',side_effect=request),patch.object(time,'time',side_effect=lambda:clock[0]):
            result=responses.save_owned(HEADERS,AUTH.mailbox_id,OP,prepared['edit'],source_id='source-1',mode=mode,
                source_hash=prepared['sourceHash'],opener=self.graph)
        self.assertTrue(delayed);self.assertTrue(result['blocked'])
        self.assertEqual(len(self.graph.writes),0 if method=='POST' else 1)
        self.assertFalse(any(r.get_method()=='PATCH' for r in self.graph.writes))
    def test_reply_deadline_before_native_graph_dispatch(self):self.response_expiry('reply','POST')
    def test_forward_deadline_before_native_graph_dispatch(self):self.response_expiry('forward','POST')
    def test_reply_deadline_before_second_graph_dispatch(self):self.response_expiry('reply','PATCH')
    def test_forward_deadline_before_second_graph_dispatch(self):self.response_expiry('forward','PATCH')

    def test_lost_lease_after_started_claim_blocks_without_retry(self):
        def lease(*args):
            if self.started():ledger.fail('store_unavailable')
        with patch.object(executor,'_assert_lease',side_effect=lease):result=self.save_trial()
        self.assertEqual(self.graph.writes,[]);self.assertTrue(result['blocked'])

    def test_unreadable_final_authorization_storage_blocks_without_retry(self):
        real=ledger.read
        def read(authority):
            root,raw=real(authority);item=ledger.entry(authority,OP,root)
            if item and item['item']['phase']=='started':ledger.fail('store_unavailable')
            return root,raw
        with patch.object(ledger,'read',side_effect=read),self.assertRaises(MicrosoftGraphError):self.save_trial()
        self.assertEqual(self.graph.writes,[]);self.assertTrue(ledger.operation(self.active,OP)['blocked'])

    def test_lease_lost_during_final_authorization_read_blocks_dispatch(self):
        real_read=ledger.read;real_checkpoint=acceptance.Permit.checkpoint
        inside=[False];lost=[False]
        def checkpoint(permit,**kw):
            inside[0]=True
            try:return real_checkpoint(permit,**kw)
            finally:inside[0]=False
        def read(authority):
            result=real_read(authority)
            if inside[0] and result[0]['v']==2:
                item=ledger.entry(authority,OP,result[0])
                if item and item['item']['phase']=='started':lost[0]=True
            return result
        def lease(*args):
            if lost[0]:ledger.fail('store_unavailable')
        with patch.object(acceptance.Permit,'checkpoint',checkpoint),patch.object(ledger,'read',read),patch.object(executor,'_assert_lease',side_effect=lease):
            result=self.save_trial()
        self.assertTrue(lost[0]);self.assertEqual(self.graph.writes,[]);self.assertTrue(result['blocked'])

    def test_lease_expiration_during_storage_read_is_rechecked(self):
        monotonic=[100.0]
        def read(*args):monotonic[0]=10000.0;return 'present','lease-token'
        with patch.object(time,'monotonic',side_effect=lambda:monotonic[0]),patch.object(
            user_config_store,'resolve_user_config_store',return_value=({'isolated':True},None)),patch.object(
            user_config_store,'_read_mailbox_mutation_lease',side_effect=read):
            with self.assertRaises(ledger.base.AttachmentLedgerError):REAL_LEASE(self.active,'lease-token',1000.0)


class ComposeInboxBarrierTests(AcceptanceFixture):
    def uncertain_draft(self):
        identifier=self.save_trial()['providerMessageId']
        current=compose.load_owned(HEADERS,AUTH.mailbox_id,identifier,opener=self.graph)
        self.graph.lose=True
        result=self.save_trial('p'*43,{**EDIT,'body':'Save ACK lost'},identifier,current['etag'])
        self.assertTrue(result['blocked']);self.stop();return identifier
    def assert_all_blocked(self, identifier, on_read=None):
        for kind in ('read','unread','archive','delete'):
            graph=MovedInboxGraph(identifier,unread=kind=='unread',on_read=on_read)
            with self.subTest(kind=kind),self.assertRaises((MicrosoftGraphError,owned.MicrosoftMailboxAccessError)):
                inbox_action(kind,graph)
            self.assertEqual(graph.writes,[])
    def test_uncertain_compose_moved_to_inbox_blocks_all_with_flags_off_and_stop(self):
        self.assert_all_blocked(self.uncertain_draft())
    def test_reload_and_same_account_reconnect_keep_all_inbox_barriers(self):
        identifier=self.uncertain_draft();self.cas.records=copy.deepcopy(self.cas.records)
        self.active=replace(self.active,credential_generation='z'*43)
        self.box['oauthCredentialGeneration']=self.active.credential_generation
        self.record['credential_generation']=self.active.credential_generation
        self.put(acceptance.tokens._key(self.active),self.record)
        self.assertTrue(next(x for x in compose.status_owned(HEADERS,AUTH.mailbox_id)['operations'] if x['requestId']=='p'*43)['blocked'])
        self.assert_all_blocked(identifier)
    def test_legacy_compose_record_remains_blocked(self):
        identifier=self.uncertain_draft();root=ledger.read(self.active)[0]
        self.put(ledger.context(self.active)[1],{'v':1,'scope':root['scope'],'operations':{
            'p'*43:ledger.entry(self.active,'p'*43,root)['item']}})
        self.assert_all_blocked(identifier)
    def test_normal_and_different_exact_id_mutations_remain_available(self):
        self.uncertain_draft()
        for kind in ('read','unread','archive','delete'):
            graph=MovedInboxGraph('normal-message',unread=kind=='unread')
            inbox_action(kind,graph);self.assertEqual(len(graph.writes),1)
    def test_new_compose_barrier_during_preflight_blocks_all_dispatches(self):
        for n,kind in enumerate(('read','unread','archive','delete')):
            identifier='moved-'+kind
            graph=MovedInboxGraph(identifier,unread=kind=='unread',on_read=lambda:ledger.reserve(
                self.active,str(n)*43,'update',identifier,'a'*64))
            with self.subTest(kind=kind),self.assertRaises(MicrosoftGraphError):inbox_action(kind,graph)
            self.assertEqual(graph.writes,[])
    def test_corrupt_compose_root_blocks_all_inbox_mutations(self):
        self.put(ledger.context(self.active)[1],{'v':2,'broken':True})
        self.assert_all_blocked('normal-message')
    def test_missing_required_compose_segment_blocks_all_inbox_mutations(self):
        identifier=self.uncertain_draft();root=ledger.read(self.active)[0]
        key=ledger.segments._segment_key(root['scope'],root['barriers'])
        del self.cas.records[key];self.assert_all_blocked(identifier)
    def test_unknown_compose_storage_read_fails_closed_before_graph(self):
        with patch.object(ledger,'read',side_effect=ledger.ComposeLedgerError('microsoft_compose_store_unavailable')):
            self.assert_all_blocked('normal-message')
    def test_changed_account_cannot_treat_old_history_as_empty(self):
        identifier=self.uncertain_draft();wrong=replace(self.active,account_id='different-account')
        with patch.object(owned,'resolve_owned_microsoft_mailbox',return_value=wrong),patch.object(
            owned,'_owned_token',return_value=('fake-token',wrong)):
            self.assert_all_blocked(identifier)
    def test_checks_run_under_lease_and_again_before_write(self):
        real=ledger.assert_unblocked;checks=[]
        def check(*args):
            self.assertTrue(owned.acquire_mailbox_mutation_lease.called)
            checks.append(args);return real(*args)
        with patch.object(ledger,'assert_unblocked',side_effect=check):
            graph=MovedInboxGraph('normal-message');inbox_action('read',graph)
        self.assertEqual(len(checks),2);self.assertEqual(len(graph.writes),1)
    def test_held_lease_does_not_consult_ledger_or_graph(self):
        with patch.object(owned,'acquire_mailbox_mutation_lease',return_value={'status':'held'}),patch.object(ledger,'assert_unblocked') as check:
            self.assert_all_blocked('normal-message');check.assert_not_called()

    def test_alternative_draft_edit_rich_edit_and_discard_respect_compose_barrier(self):
        from api.inboxes import microsoft_draft_discard, microsoft_draft_editor, microsoft_rich_draft
        identifier=self.uncertain_draft()
        for module,name,call in (
            (microsoft_draft_discard,'discard_exact_microsoft_draft_to_deleted',lambda:owned.discard_owned_microsoft_draft(HEADERS,AUTH.mailbox_id,identifier)),
            (microsoft_draft_editor,'save_exact_microsoft_draft',lambda:owned.save_owned_microsoft_editable_draft(HEADERS,AUTH.mailbox_id,identifier,etag='W/"v2"',edit={k:EDIT[k] for k in ('subject','to','cc','body')})),
            (microsoft_rich_draft,'save_rich_draft_metadata',lambda:owned.save_owned_microsoft_editable_draft(HEADERS,AUTH.mailbox_id,identifier,etag='W/"v2"',edit={k:EDIT[k] for k in ('subject','to','cc')},metadata_only=True)),
        ):
            with self.subTest(route=name),patch.object(module,name) as transport:
                with self.assertRaises(MicrosoftGraphError) as error:call()
                self.assertEqual(error.exception.code,'microsoft_compose_operation_blocked')
                transport.assert_not_called()

    def test_attachment_add_remove_respect_compose_barrier_with_compose_flag_off(self):
        identifier=self.uncertain_draft()
        for action,kwargs in (('add',{'files':[{'name':'a.txt','content_type':'text/plain','content':b'a'}]}),('remove',{'attachment_id':'a-1'})):
            # Exercise the normally disabled route offline without enabling any
            # source/config flag; its independent Compose barrier must hold.
            with self.subTest(action=action),patch.object(executor.policy,'require_release'),patch.object(executor.snapshots,'read_draft_attachment_snapshot') as reader:
                with self.assertRaises(MicrosoftGraphError) as error:
                    executor.execute_owned_attachment_request(HEADERS,AUTH.mailbox_id,identifier,'q'*43,action,expected_fingerprint='a'*64,**kwargs)
                self.assertEqual(error.exception.code,'microsoft_compose_operation_blocked');reader.assert_not_called()
