"""Offline fake Graph/CAS tests of the draft-only Compose flow."""
import copy
from dataclasses import replace
from io import BytesIO
import json
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote, urlsplit
from unittest.mock import patch
from api.inboxes import microsoft_compose_draft_graph as graph
from api.inboxes import microsoft_compose_draft_ledger as ledger
from api.inboxes import microsoft_compose_draft_service as service
from api.inboxes import microsoft_compose_draft_policy as policy
from api.inboxes import microsoft_compose_draft_http as http
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes import microsoft_draft_attachment_executor as attachments
from api.inboxes import microsoft_draft_attachment_policy as attachment_policy
from api.inboxes import microsoft_draft_attachment_snapshot as snapshots
from api.inboxes import microsoft_draft_editor as legacy
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes.test_microsoft_draft_attachment_foundation import CAS, AUTH, OP, row, message
from api.inboxes.test_microsoft_draft_attachment_http import HOST, ORIGIN, ENVIRONMENT
from api.inboxes.test_microsoft_graph import TOKEN, FakeResponse
from api.inboxes.test_microsoft_http import _request, _body

EDIT = {'subject':'Hello', 'to':['artist@example.com'], 'cc':['manager@example.com'], 'bcc':['hidden@example.com'], 'body':'Text\nwith <literal> characters'}
TAG = 'W/"version-1"'


def response(data, status=200):
    result = FakeResponse(data); result.status = status
    return result


class FakeGraph:
    def __init__(self):
        self.messages = {'draft-1':message(body={'contentType':'text','content':'Old body'})}
        self.rows = {'draft-1':[row()]}
        self.calls, self.writes = [], []
        self.lose = False
        self.reject = None
        self.read_fail = False
        self.drift = False
        self.on_write = None
        self.version = 1
        self.created = 0
    def __call__(self, req, *, timeout):
        self.calls.append(req)
        assert req.get_header('Prefer') == 'IdType="ImmutableId"'
        method, path = req.get_method(), unquote(urlsplit(req.full_url).path)
        assert '/send' not in path and '/inbox/' not in path
        if method in {'POST','PATCH'}:
            self.writes.append(req)
            if self.reject:
                return response({},self.reject)
            data=json.loads(req.data)
            assert set(data)=={'subject','body','toRecipients','ccRecipients','bccRecipients'}
            if method=='POST':
                assert path.endswith('/mailFolders/drafts/messages')
                self.created+=1; identifier='created-'+str(self.created)
                self.messages[identifier]=message(id=identifier,hasAttachments=False,**data)
                self.rows[identifier]=[]
            else:
                identifier=path.rsplit('/',1)[-1]
                assert req.get_header('If-match')==self.messages[identifier]['@odata.etag']
                self.messages[identifier].update(data)
            self.version+=1
            self.messages[identifier].update({'@odata.etag':'W/"v'+str(self.version)+'"','changeKey':'v'+str(self.version)})
            if self.drift and self.rows[identifier]:self.rows[identifier][0]['name']='external-change.txt'
            if self.on_write:self.on_write(self,identifier)
            if self.lose:raise TimeoutError('private lost Graph ACK')
            return response({'id':identifier,'isDraft':True},201 if method=='POST' else 200)
        assert method=='GET'
        if self.writes and self.read_fail:raise TimeoutError('lost readback')
        if path.endswith('/mailFolders/drafts'):return response({'id':'drafts-folder'})
        if path.endswith('/attachments'):
            identifier=path.split('/messages/')[1].split('/')[0]
            return response({'value':copy.deepcopy(self.rows[identifier])})
        assert '/mailFolders/drafts/messages/' in path
        identifier=path.rsplit('/',1)[-1]
        return response(copy.deepcopy(self.messages[identifier]))


class ComposeFixture(unittest.TestCase):
    def setUp(self):
        self.cas=CAS();self.graph=FakeGraph()
        for p in (
            patch.dict(os.environ,ENVIRONMENT,clear=True),
            patch.object(policy,'_COMPOSE_DRAFTS_RELEASED',True),
            patch.object(attachment_policy,'_DRAFT_ATTACHMENTS_RELEASED',True),
            patch.object(ledger.base.store,'_resolve_durable_store_config',return_value={'isolated':True}),
            patch.object(ledger.base.store,'_read_durable_record_snapshot',side_effect=self.cas.read),
            patch.object(ledger.base.store,'_write_google_durable_record_if_unchanged',side_effect=self.cas.write),
            patch.object(owned,'resolve_owned_microsoft_mailbox',return_value=AUTH),
            patch.object(owned,'_owned_token',return_value=(TOKEN,AUTH)),
            patch.object(owned,'_verify_unchanged_owned_mailbox'),
            patch.object(owned,'acquire_mailbox_mutation_lease',return_value={'status':'acquired','token':'lease-token'}),
            patch.object(owned,'release_mailbox_mutation_lease',return_value={'status':'released'}),
            patch.object(attachments,'_assert_lease'),
        ):
            p.start();self.addCleanup(p.stop)
    def save(self, edit=None, identifier=None, request=OP, etag=None):
        return service.save_owned({},AUTH.mailbox_id,request,edit or EDIT,message_id=identifier,etag=etag,opener=self.graph)
    def call(self, data, **kwargs):
        req=_request(data,header_pairs=ORIGIN,**kwargs);req.headers.replace_header('Host',HOST)
        with patch.object(graph._GRAPH_OPENER,'open',side_effect=self.graph):
            return http.microsoft_compose_draft_response(req)


class ComposeServiceTests(ComposeFixture):
    def test_create_verifies_exact_owner_drafts_and_never_requires_send_scope(self):
        with patch.object(owned,'_owned_token',return_value=(TOKEN,AUTH)) as token:
            result=self.save()
            self.assertEqual(result['outcome'],'confirmed_performed')
            self.assertEqual(result['providerMessageId'],'created-1')
            self.assertEqual(self.graph.created,1)
            self.assertEqual(token.call_args.kwargs,{'require_read_write':True})
        reopened=service.load_owned({},AUTH.mailbox_id,'created-1',opener=self.graph)
        self.assertEqual(reopened['edit'],EDIT)
        self.assertNotIn('attachments',json.loads(self.graph.writes[0].data))
        self.assertTrue(all('/mailFolders/drafts' in req.full_url for req in self.graph.calls))
    def test_ledger_never_stores_message_content_or_credentials(self):
        self.save()
        raw = json.dumps(self.cas.records)
        for private in (EDIT['subject'], EDIT['body'], *EDIT['to'], *EDIT['cc'], *EDIT['bcc'], TOKEN):
            self.assertNotIn(private, raw)

    def test_lost_confirmation_storage_ack_recovers_by_status_without_graph_replay(self):
        real_write = self.cas.write
        def lose_final(config, key, expected, value):
            result = real_write(config, key, expected, value)
            if any(x['phase'] == 'confirmed_performed' for x in value.get('operations', {}).values()):
                return None, {'code': 'lost-ack'}
            return result
        with patch.object(ledger.base.store, '_write_google_durable_record_if_unchanged', side_effect=lose_final):
            result = self.save()
        self.assertEqual(result['outcome'], 'confirmed_performed')
        before = len(self.graph.calls)
        self.assertEqual(self.save(), result)
        self.assertEqual(len(self.graph.calls), before)
        self.assertEqual(self.graph.created, 1)

    def test_empty_draft_is_valid_and_does_not_use_send_validation(self):
        edit={'subject':'','body':'','to':[],'cc':[],'bcc':[]}
        result=self.save(edit)
        self.assertEqual(result['outcome'],'confirmed_performed')
    def test_reopen_edit_bcc_and_body_preserves_existing_attachments(self):
        original=copy.deepcopy(self.graph.rows['draft-1'])
        loaded=service.load_owned({},AUTH.mailbox_id,'draft-1',opener=self.graph)
        result=self.save(identifier='draft-1',etag=loaded['etag'])
        self.assertEqual(result['outcome'],'confirmed_performed')
        self.assertEqual(self.graph.rows['draft-1'],original)
        self.assertEqual(service.load_owned({},AUTH.mailbox_id,'draft-1',opener=self.graph)['edit'],EDIT)
        self.assertEqual(len(self.graph.writes),1)
    def test_identical_update_needs_no_graph_write(self):
        current=service.load_owned({},AUTH.mailbox_id,'draft-1',opener=self.graph)
        self.assertEqual(self.save(current['edit'],'draft-1',etag=current['etag'])['outcome'],'confirmed_performed')
        self.assertEqual(self.graph.writes,[])
    def test_duplicate_create_and_update_are_receipt_only_replays(self):
        for identifier,etag in ((None,None),('draft-1',TAG)):
            request=('a' if identifier else 'b')*43
            first=self.save(identifier=identifier,etag=etag,request=request)
            before=len(self.graph.calls)
            self.assertEqual(self.save(identifier=identifier,etag=etag,request=request),first)
            self.assertEqual(len(self.graph.calls),before)
        self.assertEqual(len(self.graph.writes),2)
    def test_reused_request_with_changed_content_never_writes_again(self):
        self.save()
        with self.assertRaises(ledger.ComposeLedgerError):self.save({**EDIT,'body':'Different'})
        self.assertEqual(self.graph.created,1)
    def test_lost_create_ack_blocks_new_ids_after_reload_and_never_creates_twice(self):
        self.graph.lose=True
        first=self.save()
        self.assertTrue(first['blocked']);self.assertIsNone(first['providerMessageId'])
        self.assertEqual(service.inspect_owned({},AUTH.mailbox_id),[first])
        self.assertEqual(self.save(),first)
        with self.assertRaises(ledger.ComposeLedgerError):self.save(request='x'*43)
        self.assertEqual(self.graph.created,1)
    def test_lost_update_ack_blocks_same_draft_and_other_mutation_adapters(self):
        self.graph.lose=True
        first=self.save(identifier='draft-1',etag=TAG)
        self.assertTrue(first['blocked'])
        with self.assertRaises(ledger.ComposeLedgerError):self.save(identifier='draft-1',etag='W/"v2"',request='x'*43)
        snapshot=snapshots.read_draft_attachment_snapshot(TOKEN,AUTH,'draft-1',opener=self.graph)
        with self.assertRaises(ledger.ComposeLedgerError):
            attachments.execute_owned_attachment_request({},AUTH.mailbox_id,'draft-1','x'*43,'remove',expected_fingerprint=snapshot.fingerprint,attachment_id='ordinary',opener=self.graph)
        with self.assertRaises(ledger.ComposeLedgerError):
            owned.save_owned_microsoft_editable_draft({},AUTH.mailbox_id,'draft-1',etag='W/"v2"',edit={k:EDIT[k] for k in ('subject','body','to','cc')})
        self.assertEqual(len(self.graph.writes),1)
    def test_lost_readback_and_inventory_drift_remain_uncertain(self):
        for drift in (False,True):
            self.graph.read_fail=not drift;self.graph.drift=drift
            result=self.save(identifier='draft-1',etag=TAG,request=('d' if drift else 'f')*43)
            self.assertTrue(result['blocked'])
            if not drift:
                # Separate isolated operation for the second case.
                self.cas.records.clear();self.graph=FakeGraph()
        self.assertEqual(len(self.graph.writes),1)
    def test_stale_etag_rejects_before_any_patch(self):
        self.graph.messages['draft-1']['@odata.etag']='W/"Outlook-change"'
        with self.assertRaises(MicrosoftGraphError) as e:self.save(identifier='draft-1',etag=TAG)
        self.assertEqual(e.exception.code,'microsoft_compose_conflict');self.assertEqual(self.graph.writes,[])
    def test_outlook_race_412_is_definite_no_overwrite_and_no_retry(self):
        self.graph.reject=412
        result=self.save(identifier='draft-1',etag=TAG)
        self.assertEqual(result['outcome'],'confirmed_not_performed')
        self.assertEqual(self.graph.messages['draft-1']['subject'],'Keep this')
        self.assertEqual(self.save(identifier='draft-1',etag=TAG),result)
        self.assertEqual(len(self.graph.writes),1)
    def test_wrong_mailbox_and_generation_are_rejected_before_graph(self):
        with patch.object(owned,'resolve_owned_microsoft_mailbox',side_effect=owned.MicrosoftMailboxAccessError('microsoft_mailbox_not_found')):
            with self.assertRaises(owned.MicrosoftMailboxAccessError):self.save()
        with patch.object(owned,'_owned_token',return_value=(TOKEN,replace(AUTH,credential_generation='r'*43))):
            with self.assertRaises(MicrosoftGraphError):self.save()
        self.assertEqual(self.graph.calls,[])
    def test_reconnect_does_not_clear_an_uncertain_create(self):
        self.graph.lose=True;self.save()
        changed=replace(AUTH,credential_generation='r'*43)
        with patch.object(owned,'resolve_owned_microsoft_mailbox',return_value=changed),patch.object(owned,'_owned_token',return_value=(TOKEN,changed)):
            self.assertTrue(service.inspect_owned({},AUTH.mailbox_id)[0]['blocked'])
            with self.assertRaises(ledger.ComposeLedgerError):self.save(request='x'*43)
        self.assertEqual(self.graph.created,1)
    def test_storage_failure_or_lost_reservation_ack_never_touches_graph(self):
        for lost in (False,True):
            self.cas.bad_write=not lost;self.cas.lost_ack=lost
            with self.assertRaises(ledger.ComposeLedgerError):self.save(request=('l' if lost else 'w')*43)
            self.assertEqual(self.graph.writes,[])
        self.cas.bad_write=False;self.cas.lost_ack=False
        self.assertTrue(service.inspect_owned({},AUTH.mailbox_id)[0]['blocked'])
    def test_concurrent_duplicate_reservations_only_one_can_create(self):
        self.cas.race=threading.Barrier(2)
        def run():
            try:return self.save()
            except ledger.ComposeLedgerError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:run(),range(2)))
        self.assertEqual(self.graph.created,1)
    def test_html_cid_other_folder_and_nondraft_never_get_patched(self):
        for change in ({'body':{'contentType':'html','content':'<img src="cid:logo">'}},{'isDraft':False},{'parentFolderId':'inbox'}):
            self.graph=FakeGraph();self.graph.messages['draft-1'].update(change)
            with self.assertRaises(MicrosoftGraphError):self.save(identifier='draft-1',etag=TAG)
            self.assertEqual(self.graph.writes,[])
    def test_earlier_prepared_attachment_intents_also_block_compose_updates(self):
        snapshot = snapshots.read_draft_attachment_snapshot(TOKEN, AUTH, 'draft-1', opener=self.graph)
        ledger.base.reserve_attachment_operation(snapshot, 'p'*43, 'remove',
            expected_fingerprint=snapshot.fingerprint, attachment_id='ordinary')
        with self.assertRaises(MicrosoftGraphError) as caught:
            self.save(identifier='draft-1', etag=TAG)
        self.assertEqual(caught.exception.code, 'microsoft_compose_blocked')
        self.assertEqual(self.graph.writes, [])

    def test_ledger_saturation_fails_closed_without_eviction(self):
        with patch.object(ledger,'MAX_OPERATIONS',0):
            with self.assertRaises(ledger.ComposeLedgerError):self.save()
        self.assertEqual(self.graph.created,0)
    def test_lease_loss_before_start_and_before_write_never_calls_graph(self):
        for at in (1,2):
            self.cas.records.clear()
            with patch.object(attachments,'_assert_lease',side_effect=[None]*(at-1)+[MicrosoftGraphError('lease_lost')]):
                result=self.save()
            self.assertEqual(result['outcome'],'confirmed_not_performed' if at==1 else 'uncertain')
            self.assertEqual(self.graph.created,0)


class ComposeHTTPTests(ComposeFixture):
    def create_payload(self,**kw):return {'action':'create','mailboxId':AUTH.mailbox_id,'requestId':OP,'edit':EDIT,**kw}
    def test_raw_http_create_load_update_and_status_use_real_service_fake_graph(self):
        created=_body(self.call(self.create_payload()))
        self.assertEqual(created['receipt']['outcome'],'confirmed_performed')
        id=created['receipt']['providerMessageId']
        loaded=_body(self.call({'action':'load','mailboxId':AUTH.mailbox_id,'providerMessageId':id}))
        self.assertEqual(loaded['edit']['bcc'],EDIT['bcc'])
        update={'action':'update','mailboxId':AUTH.mailbox_id,'requestId':'u'*43,'providerMessageId':id,'etag':loaded['etag'],'edit':{**EDIT,'body':'Continued'}}
        self.assertEqual(_body(self.call(update))['receipt']['outcome'],'confirmed_performed')
        self.assertEqual(len(_body(self.call({'action':'status','mailboxId':AUTH.mailbox_id}))['operations']),2)
        self.assertEqual(self.graph.created,1)
    def test_disabled_and_production_gate_before_body_owner_store_and_graph(self):
        for env,gate in (('preview',False),('production',True)):
            with patch.dict(os.environ,{'VERCEL_ENV':env}),patch.object(policy,'_COMPOSE_DRAFTS_RELEASED',gate),patch.object(owned,'resolve_owned_microsoft_mailbox') as owner:
                req=_request(self.create_payload(),header_pairs=ORIGIN);req.rfile=None
                self.assertEqual(http.microsoft_compose_draft_response(req).status,403);owner.assert_not_called()
        self.assertEqual(self.graph.calls,[])
    def test_invalid_recipients_bcc_payload_and_extra_authority_are_rejected(self):
        for data in (self.create_payload(edit={**EDIT,'bcc':['invalid']}),self.create_payload(edit={**EDIT,'bcc':EDIT['to']}),
                     self.create_payload(edit={**EDIT,'bcc':['x'+str(n)+'@example.com' for n in range(21)]}),
                     self.create_payload(edit={**EDIT,'body':'\0'}),self.create_payload(edit={**EDIT,'from':'other@example.com'}),
                     self.create_payload(action='send'),self.create_payload(action='reply'),self.create_payload(url='https://evil.example'),
                     self.create_payload(mailboxId='../box'),self.create_payload(requestId='short')):
            with self.subTest(data=str(data)[:100]):self.assertEqual(self.call(data).status,400)
        self.assertEqual(self.graph.calls,[])
    def test_origin_and_methods_duplicate_keys_and_size_limits_fail_before_graph(self):
        for method in ('GET','HEAD','PATCH','DELETE'):
            self.assertEqual(self.call(self.create_payload(),command=method).status,405)
        self.assertEqual(self.call(self.create_payload(),raw=b'{"action":"create","action":"status"}').status,400)
        req=_request(self.create_payload(),header_pairs=ORIGIN);req.headers.replace_header('Host',HOST)
        req.headers.replace_header('Content-Length',str(http.MAX_REQUEST_BYTES+1));req.rfile=None
        self.assertEqual(http.microsoft_compose_draft_response(req).status,413)
        req=_request(self.create_payload(),header_pairs=(('Origin','https://evil.example'),));req.headers.replace_header('Host',HOST)
        self.assertGreaterEqual(http.microsoft_compose_draft_response(req).status,400)
        self.assertEqual(self.graph.calls,[])
    def test_untrusted_provider_or_ledger_details_are_not_exposed(self):
        with patch.object(service,'save_owned',side_effect=RuntimeError('private-token contents')):
            result=_body(self.call(self.create_payload()));self.assertTrue(result['blocked']);self.assertNotIn('private-token',json.dumps(result))
        with patch.object(service,'inspect_owned',return_value=[{'claim':'secret'}]):
            result=_body(self.call({'action':'status','mailboxId':AUTH.mailbox_id}));self.assertFalse(result['ok']);self.assertNotIn('secret',json.dumps(result))
    def test_session_and_connection_expiry_remain_blocked(self):
        for code in ('microsoft_unauthorized','microsoft_reconnect_required'):
            with patch.object(owned,'resolve_owned_microsoft_mailbox',side_effect=owned.MicrosoftMailboxAccessError(code)):
                result=_body(self.call(self.create_payload()));self.assertTrue(result['blocked']);self.assertEqual(result['error']['code'],code)
        self.assertEqual(self.graph.writes,[])
