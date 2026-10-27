"""Existing-draft send safety: fake Graph and isolated durable CAS only."""
import copy
import base64
from urllib.parse import unquote, urlsplit
from api.inboxes import microsoft_draft_send_manifest as manifest
from dataclasses import replace
from io import BytesIO
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from unittest.mock import patch
import unittest
from api.inboxes import test_microsoft_compose_draft as compose_fixture
from api.inboxes import microsoft_draft_send_policy as policy
from api.inboxes import microsoft_draft_send_service as service
from api.inboxes import microsoft_draft_send_index as index
from api.inboxes import microsoft_draft_send_http as http
from api.inboxes import microsoft_outbound_ledger as outbound
from api.inboxes import microsoft_sent_copy_proof as proof
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes import microsoft_compose_draft_service as compose_service
from api.inboxes import microsoft_compose_draft_ledger as compose_ledger
from api.inboxes import microsoft_draft_attachment_requests as attachment_requests
from api.inboxes import microsoft_draft_attachment_executor as attachments
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes.test_microsoft_draft_attachment_foundation import AUTH,OP
from api.inboxes.test_microsoft_draft_attachment_http import HOST,ORIGIN
from api.inboxes.test_microsoft_graph import TOKEN
from api.inboxes.test_microsoft_http import _request,_body

REAL_PROOF=proof.verify_microsoft_sent_copy

class SendGraph(compose_fixture.FakeGraph):
    def __init__(self):
        super().__init__();self.sends=[];self.sent={};self.visible=True;self.lose_send=False
        self.copy_on_send=True;self.send_status=202;self.on_get=None;self.message_reads=0
        self.draft_id="draft-1"
        self.bytes={"ordinary":b"song"};self.sent_bytes=None;self.sent_rows=None;self.on_send=None
        self.me={"id":AUTH.account_id,"mail":AUTH.email}
    def __call__(self,req,*,timeout):
        url=req.full_url
        if req.get_method()=='POST' and url.endswith('/send'):
            self.calls.append(req);self.sends.append(req)
            assert req.data==b'' and 'IdType="ImmutableId"'==req.get_header('Prefer')
            assert '/me/messages/'+self.draft_id+'/send' in url
            if self.on_send:self.on_send(self)
            if self.send_status==202 and self.copy_on_send:
                self.sent_bytes=copy.deepcopy(self.bytes);self.sent_rows=copy.deepcopy(self.rows[self.draft_id])
                self.sent[self.draft_id]={**copy.deepcopy(self.messages[self.draft_id]),'isDraft':False,'parentFolderId':'sent-folder','sentDateTime':'2026-10-24T12:00:00Z'}
            if self.lose_send:raise TimeoutError('private lost send acknowledgement')
            return compose_fixture.response(b'',self.send_status)
        path=unquote(urlsplit(url).path)
        if '/me?' in url:
            self.calls.append(req);assert req.get_method()=='GET'
            return compose_fixture.response(self.me)
        if '/attachments' in path:
            self.calls.append(req);assert req.get_method()=='GET'
            is_sent='/sentitems/' in path
            rows=(self.sent_rows if self.sent_rows is not None else self.rows[self.draft_id]) if is_sent else self.rows[self.draft_id]
            data=(self.sent_bytes if self.sent_bytes is not None else self.bytes) if is_sent else self.bytes
            if path.endswith('/attachments'):return compose_fixture.response({'value':copy.deepcopy(rows)})
            aid=path.rsplit('/',1)[-1]
            row=next(r for r in rows if r['id']==aid)
            return compose_fixture.response({**copy.deepcopy(row),'contentBytes':base64.b64encode(data.get(aid,b'song')).decode()})
        if '/sentitems' in url:
            self.calls.append(req);assert req.get_method()=='GET'
            if '/messages/' in url:
                if not self.visible or self.draft_id not in self.sent:
                    raise HTTPError(url,404,'pending',{},BytesIO(b''))
                return compose_fixture.body_response(req, self.sent[self.draft_id])
            return compose_fixture.response({'id':'sent-folder'})
        if '/messages/' in url and '/attachments' not in url and not url.endswith('/$value') and 'outlook.body-content-type' not in req.get_header('Prefer') and req.get_method()=='GET':
            self.message_reads+=1
            if self.on_get:self.on_get(self)
        return super().__call__(req,timeout=timeout)

class SendFixture(compose_fixture.ComposeFixture):
    def setUp(self):
        super().setUp();self.graph=SendGraph()
        for p in (
            patch.object(policy,'_DRAFT_SEND_RELEASED',True),
            patch.object(service.local_test,'authorize_send'),
            patch.object(service.local_test,'authorize_status'),
            patch.object(proof,'verify_microsoft_sent_copy',side_effect=lambda token,id,**kw:REAL_PROOF(token,id,opener=self.graph)),
            patch.object(manifest._GRAPH_OPENER,'open',side_effect=lambda *args,**kw:self.graph(*args,**kw)),
        ):
            p.start();self.addCleanup(p.stop)
        current=service.drafts.read_current(TOKEN,AUTH,'draft-1',opener=self.graph)
        self.etag=current.snapshot.etag;self.fingerprint=current.snapshot.fingerprint
        self.graph.calls.clear();self.graph.message_reads=0
    def send(self,attempt=OP,**kw):
        return service.send_owned({},AUTH.mailbox_id,'draft-1',attempt,etag=kw.get('etag',self.etag),snapshot_hash=kw.get('fingerprint',self.fingerprint),opener=self.graph)
    def call_send(self,data=None,**kwargs):
        req=_request(data or {'action':'send','mailboxId':AUTH.mailbox_id,'providerMessageId':'draft-1','attemptId':OP,'etag':self.etag,'snapshotHash':self.fingerprint},header_pairs=ORIGIN,**kwargs)
        req.headers.replace_header('Host',HOST)
        with patch.object(service.drafts._GRAPH_OPENER,'open',side_effect=self.graph):
            return http.microsoft_draft_send_response(req)

class SendTests(SendFixture):
    def test_normal_send_existing_draft_with_attachments_and_exact_sent_proof(self):
        result=self.send()
        self.assertEqual(result['outcome'],'confirmed_performed');self.assertTrue(result['providerAccepted']);self.assertTrue(result['sentCopyConfirmed']);self.assertFalse(result['deliveryConfirmed'])
        self.assertEqual(len(self.graph.sends),1);self.assertEqual(self.graph.created,0)
        self.assertEqual(self.graph.writes,[])
        self.assertEqual(len(self.graph.rows['draft-1']),1)
        self.assertEqual(outbound.inspect_outbound_attempt(AUTH,OP)['phase'],'sent_copy_confirmed')
    def test_multiple_to_cc_bcc_are_sent_from_the_saved_graph_draft(self):
        edit=copy.deepcopy(compose_fixture.EDIT);edit['to'].append('second@example.com')
        self.graph.messages['draft-1'].update({'subject':edit['subject'],'body':{'contentType':'text','content':edit['body']},**{k+'Recipients':[{'emailAddress':{'address':x}} for x in edit[k]] for k in ('to','cc','bcc')}})
        current=service.drafts.read_current(TOKEN,AUTH,'draft-1',opener=self.graph)
        result=self.send(fingerprint=current.snapshot.fingerprint)
        self.assertEqual(result['outcome'],'confirmed_performed')
        self.assertEqual(self.graph.sent['draft-1']['bccRecipients'][0]['emailAddress']['address'],'hidden@example.com')
        self.assertEqual(self.graph.sends[0].data,b'')
    def test_multiple_saved_attachments_are_sent_without_upload_or_rewrite(self):
        self.graph.rows['draft-1'].append(compose_fixture.row('second', name='second.txt'))
        current = service.drafts.read_current(TOKEN, AUTH, 'draft-1', opener=self.graph)
        result = self.send(fingerprint=current.snapshot.fingerprint)
        self.assertEqual(result['outcome'], 'confirmed_performed')
        self.assertEqual(len(self.graph.rows['draft-1']), 2)
        self.assertEqual(len(self.graph.sends), 1)
        self.assertEqual(self.graph.writes, [])

    def test_removed_attachment_after_review_is_not_sent(self):
        self.graph.rows['draft-1'] = []
        self.graph.messages['draft-1']['hasAttachments'] = False
        self.assertEqual(self.send()['outcome'], 'confirmed_not_started')
        self.assertEqual(self.graph.sends, [])

    def test_202_without_sent_copy_stays_uncertain_across_reload(self):
        self.graph.visible=False
        result=self.send();self.assertEqual(result['outcome'],'uncertain');self.assertTrue(result['providerAccepted'])
        self.assertEqual(service.inspect_owned({},AUTH.mailbox_id),[result])
        self.assertEqual(service.check_owned({},AUTH.mailbox_id,OP),result)
        self.assertEqual(len(self.graph.sends),1)
    def test_missing_copy_later_becomes_positive_proof_without_another_send(self):
        self.graph.visible=False;self.send();self.graph.visible=True
        self.assertEqual(service.check_owned({},AUTH.mailbox_id,OP)['outcome'],'confirmed_performed')
        self.assertEqual(service.inspect_owned({},AUTH.mailbox_id)[0]['outcome'],'confirmed_performed')
        self.assertEqual(len(self.graph.sends),1)
    def test_duplicate_identical_request_is_receipt_only(self):
        result=self.send();before=len(self.graph.calls)
        self.assertEqual(self.send(),result);self.assertEqual(len(self.graph.calls),before);self.assertEqual(len(self.graph.sends),1)
    def test_new_attempt_id_cannot_bypass_the_existing_draft_send_barrier(self):
        self.send()
        with self.assertRaises(MicrosoftGraphError):self.send('n'*43)
        self.assertEqual(len(self.graph.sends),1)
    def test_lost_graph_ack_with_and_without_positive_sent_evidence(self):
        self.graph.lose_send=True;self.graph.copy_on_send=False
        result=self.send();self.assertEqual(result['outcome'],'uncertain')
        self.assertEqual(self.send(),result)
        self.graph.sent['draft-1']={**self.graph.messages['draft-1'],'isDraft':False,'parentFolderId':'sent-folder','sentDateTime':'2026-10-24T12:00:00Z'}
        self.assertEqual(service.check_owned({},AUTH.mailbox_id,OP)['outcome'],'confirmed_performed')
        self.assertEqual(len(self.graph.sends),1)
    def test_lost_graph_ack_can_be_confirmed_only_by_exact_sent_copy(self):
        self.graph.lose_send=True
        self.assertEqual(self.send()['outcome'],'confirmed_performed');self.assertEqual(len(self.graph.sends),1)
    def test_invalid_recipient_subject_or_body_is_durably_not_started(self):
        for field,value in [('toRecipients',[]),('subject',''),('body',{'contentType':'text','content':''})]:
            self.cas.records.clear();self.graph=SendGraph();self.graph.messages['draft-1'][field]=value
            current=service.drafts.read_current(TOKEN,AUTH,'draft-1',opener=self.graph)
            result=self.send(fingerprint=current.snapshot.fingerprint)
            self.assertEqual(result['outcome'],'confirmed_not_started');self.assertEqual(self.graph.sends,[])
    def test_stale_etag_and_fingerprint_are_not_started(self):
        self.graph.messages['draft-1']['subject']='Outlook changed this'
        result=self.send();self.assertEqual(result['outcome'],'confirmed_not_started');self.assertEqual(result['reason'],'microsoft_draft_send_conflict');self.assertEqual(self.graph.sends,[])
    def test_change_between_preflight_and_final_observation_is_not_sent(self):
        def drift(graph):
            if graph.message_reads==5:graph.messages['draft-1']['subject']='Concurrent Outlook change'
        self.graph.on_get=drift
        self.assertEqual(self.send()['outcome'],'confirmed_not_started');self.assertEqual(self.graph.sends,[])
    def test_wrong_folder_or_non_draft_is_never_sent(self):
        for change in ({'parentFolderId':'inbox'},{'isDraft':False}):
            self.cas.records.clear();self.graph=SendGraph();self.graph.messages['draft-1'].update(change)
            self.assertEqual(self.send()['outcome'],'confirmed_not_started');self.assertEqual(self.graph.sends,[])
    def test_unfinished_compose_and_attachment_operations_block_send(self):
        compose_ledger.reserve(AUTH,'c'*43,'update','draft-1','a'*64)
        self.assertEqual(self.send()['outcome'],'confirmed_not_started');self.assertEqual(self.graph.sends,[])
        self.cas.records.clear();attachment_requests.reserve(AUTH,'a'*43,'draft-1','a'*64,1)
        self.assertEqual(self.send()['outcome'],'confirmed_not_started');self.assertEqual(self.graph.sends,[])
    def test_unfinished_per_file_attachment_and_incomplete_inventory_block_send(self):
        snapshot=service.drafts.read_current(TOKEN,AUTH,'draft-1',opener=self.graph).snapshot
        compose_ledger.base.reserve_attachment_operation(snapshot,'a'*43,'remove',expected_fingerprint=snapshot.fingerprint,attachment_id='ordinary')
        self.assertEqual(self.send()['outcome'],'confirmed_not_started');self.assertEqual(self.graph.sends,[])
        self.cas.records.clear();self.graph.rows['draft-1']=[]
        self.assertEqual(self.send()['outcome'],'confirmed_not_started');self.assertEqual(self.graph.sends,[])
    def test_expired_credentials_and_missing_mail_send_are_definitely_not_started(self):
        for code in ('microsoft_reconnect_required','microsoft_send_permission_required','microsoft_write_permission_required'):
            self.cas.records.clear()
            with patch.object(owned,'_owned_token',side_effect=owned.MicrosoftMailboxAccessError(code)):
                result=self.send()
            self.assertEqual(result['outcome'],'confirmed_not_started');self.assertEqual(result['reason'],code);self.assertEqual(self.graph.sends,[])
    def test_credentials_require_readwrite_and_mail_send(self):
        with patch.object(owned,'_owned_token',return_value=(TOKEN,AUTH)) as token:self.send()
        self.assertTrue(any(c.kwargs=={'require_read_write':True,'require_mail_send':True} for c in token.call_args_list))
    def test_wrong_mailbox_and_swapped_token_authority_do_not_send(self):
        with patch.object(owned,'resolve_owned_microsoft_mailbox',side_effect=owned.MicrosoftMailboxAccessError('microsoft_mailbox_not_found')):
            with self.assertRaises(owned.MicrosoftMailboxAccessError):self.send()
        with patch.object(owned,'_owned_token',return_value=(TOKEN,replace(AUTH,credential_generation='z'*43))):
            self.assertEqual(self.send()['outcome'],'confirmed_not_started')
        self.assertEqual(self.graph.sends,[])
    def test_reconnect_keeps_a_previous_attempt_visible_and_blocked(self):
        self.graph.visible=False;self.send();changed=replace(AUTH,credential_generation='z'*43)
        with patch.object(owned,'resolve_owned_microsoft_mailbox',return_value=changed):
            self.assertEqual(service.inspect_owned({},AUTH.mailbox_id)[0]['outcome'],'uncertain')
            with self.assertRaises(MicrosoftGraphError):self.send('n'*43)
        self.assertEqual(len(self.graph.sends),1)
    def test_lost_index_ack_never_sends_and_keeps_reload_barrier(self):
        self.cas.lost_ack=True
        with self.assertRaises(MicrosoftGraphError):self.send()
        self.cas.lost_ack=False
        self.assertEqual(service.inspect_owned({},AUTH.mailbox_id)[0]['outcome'],'uncertain');self.assertEqual(self.graph.sends,[])
    def test_lost_durable_start_ack_is_uncertain_even_when_transport_never_ran(self):
        write=self.cas.write
        def lose(config,key,expected,value):
            result=write(config,key,expected,value)
            return (None,{'code':'lost'}) if value.get('phase')=='send_started' else result
        with patch.object(outbound.store,'_write_google_durable_record_if_unchanged',side_effect=lose):
            result=self.send()
        self.assertEqual(result['outcome'],'uncertain');self.assertEqual(self.graph.sends,[]);self.assertEqual(self.send(),result)
    def test_lease_loss_before_and_after_durable_start(self):
        for at in (1,2):
            self.cas.records.clear()
            with patch.object(attachments,'_assert_lease',side_effect=[None]*(at-1)+[MicrosoftGraphError('lease_lost')]):result=self.send()
            self.assertEqual(result['outcome'],'confirmed_not_started' if at==1 else 'uncertain');self.assertEqual(self.graph.sends,[])
    def test_provider_error_after_start_is_never_automatically_retried(self):
        self.graph.send_status=503
        result=self.send();self.assertEqual(result['outcome'],'uncertain');self.assertEqual(self.send(),result);self.assertEqual(len(self.graph.sends),1)
    def test_wrong_sent_identity_or_parent_never_proves_success(self):
        self.graph.visible=False;self.send();self.graph.visible=True;self.graph.sent['draft-1']['parentFolderId']='other'
        with self.assertRaises(MicrosoftGraphError):service.check_owned({},AUTH.mailbox_id,OP)
        self.assertEqual(service.inspect_owned({},AUTH.mailbox_id)[0]['outcome'],'uncertain')
    def test_send_barrier_blocks_compose_attachment_and_legacy_edits(self):
        self.graph.visible=False;self.send()
        with self.assertRaises(MicrosoftGraphError):compose_service.save_owned({},AUTH.mailbox_id,'c'*43,compose_fixture.EDIT,message_id='draft-1',etag=self.etag,opener=self.graph)
        with self.assertRaises(MicrosoftGraphError):attachments.execute_owned_attachment_request({},AUTH.mailbox_id,'draft-1','a'*43,'remove',expected_fingerprint=self.fingerprint,attachment_id='ordinary',opener=self.graph)
        with self.assertRaises(MicrosoftGraphError):owned.save_owned_microsoft_editable_draft({},AUTH.mailbox_id,'draft-1',etag=self.etag,edit={k:compose_fixture.EDIT[k] for k in ('subject','to','cc','body')})
        self.assertEqual(len(self.graph.sends),1)
    def test_no_credentials_or_message_content_stored_in_send_index(self):
        self.send();raw=json.dumps(self.cas.records)
        for private in (TOKEN,'receiver@example.com','Keep this','Old body','song.mp3'):
            self.assertNotIn(private,raw)
    def test_concurrent_new_attempt_ids_cannot_send_a_draft_twice(self):
        self.cas.race=threading.Barrier(2)
        def run(id):
            try:return self.send(id)
            except (MicrosoftGraphError,outbound.OutboundLedgerError):return None
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(run,['x'*43,'y'*43]))
        self.assertEqual(len(self.graph.sends),1)

class SendHTTPTests(SendFixture):
    def test_real_http_send_and_status_do_not_create_or_patch(self):
        result=_body(self.call_send());self.assertEqual(result['receipt']['outcome'],'confirmed_performed')
        status=_body(self.call_send({'action':'status','mailboxId':AUTH.mailbox_id}));self.assertEqual(status['attempts'],[result['receipt']])
        self.assertEqual(len(self.graph.sends),1);self.assertEqual(self.graph.writes,[])
    def test_disabled_and_production_precede_body_owner_and_storage(self):
        for env,flag in [('preview',False),('production',True)]:
            with patch.dict(os.environ,{'VERCEL_ENV':env}),patch.object(policy,'_DRAFT_SEND_RELEASED',flag),patch.object(owned,'resolve_owned_microsoft_mailbox') as owner:
                if env == 'production':
                    req=_request({'action':'send'},header_pairs=ORIGIN);req.rfile=None
                    self.assertEqual(http.microsoft_draft_send_response(req).status,403)
                else:
                    self.assertEqual(self.call_send().status,403)
                owner.assert_not_called()
        self.assertEqual(self.graph.sends,[])
    def test_strict_schemas_origin_methods_and_duplicate_keys(self):
        for data in ({'action':'send','mailboxId':AUTH.mailbox_id,'attemptId':OP}, {'action':'reply','mailboxId':AUTH.mailbox_id}, {'action':'status','mailboxId':AUTH.mailbox_id,'url':'https://evil.example'}):
            self.assertEqual(self.call_send(data).status,400)
        self.assertEqual(self.call_send(command='GET').status,405)
        self.assertEqual(self.call_send(raw=b'{"action":"status","action":"send"}').status,400)
        req=_request({'action':'status','mailboxId':AUTH.mailbox_id},header_pairs=(('Origin','https://evil.example'),));req.headers.replace_header('Host',HOST)
        self.assertGreaterEqual(http.microsoft_draft_send_response(req).status,400);self.assertEqual(self.graph.sends,[])
    def test_output_validation_rejects_cross_draft_and_false_delivery_claim(self):
        receipt={'attemptId':OP,'providerMessageId':'draft-1','outcome':'confirmed_performed','providerAccepted':True,'sentCopyConfirmed':True,'deliveryConfirmed':False,'reason':None}
        for change in ({'providerMessageId':'other'},{'deliveryConfirmed':True},{'secret':'token'}):
            with patch.object(service,'send_owned',return_value={**receipt,**change}):
                result=_body(self.call_send());self.assertFalse(result['ok']);self.assertNotIn('token',json.dumps(result))
