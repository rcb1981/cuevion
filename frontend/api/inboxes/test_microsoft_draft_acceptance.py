"""Production-shaped acceptance, fake Graph and isolated CAS only."""
import copy
import json
import os
import time
from dataclasses import replace
from unittest.mock import patch
from urllib.parse import urlsplit
from api.inboxes import microsoft_draft_acceptance as acceptance
from api.inboxes import microsoft_compose_draft_service as compose
from api.inboxes import microsoft_compose_draft_policy as policy
from api.inboxes import microsoft_response_drafts as responses
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes import microsoft_compose_draft_ledger as ledger
from api.inboxes import microsoft_draft_send_policy as send_policy
from api.inboxes import microsoft_draft_attachment_policy as attachment_policy
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes.test_microsoft_response_drafts import ResponseFixture, ResponseGraph, response
from api.inboxes.test_microsoft_compose_draft import AUTH, OP, EDIT, TOKEN
from api.inboxes.test_microsoft_mailbox_access import valid_member, mailbox, resolved
from api.inboxes.test_microsoft_http import _body

HEADERS=(('Host','app.cuevion.com'),('Origin','https://app.cuevion.com'))
REAL_OWNER=owned.resolve_owned_microsoft_mailbox
REAL_TOKEN=owned._owned_token
REAL_VERIFY=owned._verify_unchanged_owned_mailbox


class TrialGraph(ResponseGraph):
    def __call__(self,request,*,timeout):
        if urlsplit(request.full_url).path.endswith('/me'):
            self.calls.append(request);return response({'id':AUTH.account_id,'mail':acceptance.APPROVED_ADDRESS})
        return super().__call__(request,timeout=timeout)


class AcceptanceFixture(ResponseFixture):
    def setUp(self):
        super().setUp();self.graph=TrialGraph()
        self.active=replace(AUTH,email=acceptance.APPROVED_ADDRESS)
        self.member=valid_member(email=AUTH.owner_email)
        self.box=mailbox(id=AUTH.mailbox_id,email=self.active.email,oauthAccountId=AUTH.account_id,
            oauthOwnerEmail=AUTH.owner_email,oauthCredentialGeneration=AUTH.credential_generation)
        self.cfg={'v':1,'ownerUserId':self.member.user_id,'workspaceId':self.member.workspace_id,
            'ownerEmail':AUTH.owner_email,'accountId':AUTH.account_id,'mailboxId':AUTH.mailbox_id,'mailboxAddress':self.active.email}
        now=int(time.time())
        self.window={'v':1,'policyHash':ledger.digest(self.cfg),'stopped':False,'notBefore':now-1,'expiresAt':now+600,
            'baselineCount':0,'maxOperations':12,'sourceIds':['source-1']}
        self.record={'provider':'microsoft','owner_email':AUTH.owner_email,'mailbox_id':AUTH.mailbox_id,'email':self.active.email,
            'account_id':AUTH.account_id,'credential_generation':AUTH.credential_generation,'access_token':TOKEN,
            'refresh_token':'fake-refresh-token-long','token_type':'Bearer','scope':'User.Read Mail.Read Mail.ReadWrite offline_access',
            'expires_at':'2099-01-01T00:00:00+00:00','created_at':'2026-01-01T00:00:00+00:00','updated_at':'2026-01-01T00:00:00+00:00'}
        for p in (patch.dict(os.environ,{'VERCEL_ENV':'production','CUEVION_APP_URL':'https://app.cuevion.com','MICROSOFT_OAUTH_REDIRECT_URI':'https://app.cuevion.com/api/inboxes/oauth-callback'},clear=True),
            patch.object(policy,'_COMPOSE_DRAFTS_RELEASED',False),patch.object(responses,'_RESPONSE_DRAFTS_RELEASED',False),
            patch.object(attachment_policy,'_DRAFT_ATTACHMENTS_RELEASED',False),
            patch.object(owned,'resolve_owned_managed_inbox_record',side_effect=lambda *a,**k:resolved(member=self.member,box=self.box,
                config={'email':self.member.email,'managedInboxes':[copy.deepcopy(self.box)]},user={'email':self.member.email})),
            patch.object(owned,'resolve_owned_microsoft_mailbox',REAL_OWNER),patch.object(owned,'_owned_token',REAL_TOKEN),
            patch.object(owned,'_verify_unchanged_owned_mailbox',REAL_VERIFY),
            patch.object(owned,'ensure_owned_microsoft_access_token',side_effect=lambda a:copy.deepcopy(self.record))):
            p.start();self.addCleanup(p.stop)
        self.put(acceptance.CONFIG_KEY,self.cfg);self.put(self.control_key(),self.window)
        self.put(acceptance.tokens._key(self.active),self.record)
    def put(self,key,value):self.cas.records[key]=json.dumps(value,sort_keys=True,separators=(',',':'))
    def control_key(self):return acceptance.CONTROL_PREFIX+ledger.digest(self.cfg)
    def save_trial(self,request=OP,edit=None,identifier=None,etag=None):
        return compose.save_owned(HEADERS,AUTH.mailbox_id,request,edit or EDIT,message_id=identifier,etag=etag,opener=self.graph)
    def stop(self):self.window['stopped']=True;self.put(self.control_key(),self.window)
    def blocked(self,**kwargs):
        writes=len(self.graph.writes)
        with self.assertRaises((MicrosoftGraphError,owned.MicrosoftMailboxAccessError)):self.save_trial(**kwargs)
        self.assertEqual(len(self.graph.writes),writes)


class DraftAcceptanceTests(AcceptanceFixture):
    def test_correct_identity_create_reopen_update_with_all_release_flags_off(self):
        self.assertFalse(policy._COMPOSE_DRAFTS_RELEASED);self.assertFalse(responses._RESPONSE_DRAFTS_RELEASED)
        self.assertFalse(send_policy._DRAFT_SEND_RELEASED)
        result=self.save_trial();self.assertEqual(result['outcome'],'confirmed_performed')
        draft=compose.load_owned(HEADERS,AUTH.mailbox_id,result['providerMessageId'],opener=self.graph)
        updated={**EDIT,'body':'Updated body'}
        self.assertEqual(self.save_trial('p'*43,updated,draft['providerMessageId'],draft['etag'])['outcome'],'confirmed_performed')
        self.assertEqual(len(self.graph.writes),2)
        self.assertTrue(all('/send' not in r.full_url for r in self.graph.calls))

    def test_reply_and_forward_use_only_approved_source_and_cannot_send(self):
        for n,mode in enumerate(('reply','forward')):
            prepared=responses.prepare_owned(HEADERS,AUTH.mailbox_id,'source-1',mode,opener=self.graph)
            edit={**prepared['edit'],'body':'Test comment'}
            result=responses.save_owned(HEADERS,AUTH.mailbox_id,str(n)*43,edit,source_id='source-1',mode=mode,
                source_hash=prepared['sourceHash'],opener=self.graph)
            self.assertEqual(result['outcome'],'confirmed_performed')
        self.assertEqual(self.graph.native_actions,['createReply','createForward'])

    def test_unlisted_source_reply_all_and_existing_personal_drafts_blocked(self):
        for source,mode in [('source-1','reply_all'),('different-source','reply')]:
            with self.assertRaises(MicrosoftGraphError):responses.prepare_owned(HEADERS,AUTH.mailbox_id,source,mode,opener=self.graph)
        self.blocked(identifier='draft-1',etag='W/"v1"')
        self.assertEqual(self.graph.writes,[])

    def test_owner_workspace_account_address_and_mailbox_all_bound(self):
        for key in ('ownerUserId','workspaceId','ownerEmail','accountId','mailboxId','mailboxAddress'):
            original=self.cfg[key];self.cfg[key]='wrong@example.com' if 'Email' in key or 'Address' in key else 'wrong-id'
            self.put(acceptance.CONFIG_KEY,self.cfg);self.blocked();self.cfg[key]=original
        self.assertEqual(self.graph.writes,[])

    def test_actual_graph_account_mismatch_blocks_before_claim(self):
        real=self.graph
        def wrong(request,**kwargs):
            if urlsplit(request.full_url).path.endswith('/me'):return response({'id':'other-account','mail':self.active.email})
            return real(request,**kwargs)
        self.graph=wrong
        with self.assertRaises(MicrosoftGraphError):self.save_trial()
        self.assertEqual(real.writes,[])
        self.assertEqual(ledger.page(self.active)['operations'],[])

    def test_missing_mail_read_write_never_mutates_and_diagnosis_requires_no_refresh(self):
        self.record['scope']='User.Read Mail.Read offline_access';self.put(acceptance.tokens._key(self.active),self.record)
        self.blocked()
        with patch.object(owned,'ensure_owned_microsoft_access_token',side_effect=AssertionError('NO_REFRESH')):
            report=acceptance.capabilities(HEADERS,AUTH.mailbox_id)
        self.assertFalse(report['mailReadWrite']);self.assertFalse(report['canWrite']);self.assertTrue(report['canInspect'])
        self.assertNotIn(TOKEN,json.dumps(report));self.assertEqual(report['identity']['accountId'],AUTH.account_id)

    def test_capabilities_stored_grant_and_expiration_are_separate_facts(self):
        self.record['expires_at']='2020-01-01T00:00:00+00:00';self.put(acceptance.tokens._key(self.active),self.record)
        before=copy.deepcopy(self.cas.records)
        with patch.object(owned,'ensure_owned_microsoft_access_token',side_effect=AssertionError('NO_REFRESH')):
            report=acceptance.capabilities(HEADERS,AUTH.mailbox_id)
        self.assertTrue(report['mailReadWrite']);self.assertTrue(report['tokenExpired'])
        self.assertEqual(before,self.cas.records);self.assertEqual(self.graph.calls,[])

    def test_no_policy_no_control_stopped_expired_and_bad_storage_fail_closed(self):
        records=copy.deepcopy(self.cas.records)
        for key in (acceptance.CONFIG_KEY,self.control_key()):
            self.cas.records=copy.deepcopy(records);del self.cas.records[key];self.blocked()
        self.cas.records=copy.deepcopy(records);self.stop();self.blocked()
        self.window.update(stopped=False,notBefore=1,expiresAt=100);self.put(self.control_key(),self.window);self.blocked()
        self.cas.bad_read=True;self.blocked()

    def test_stop_after_start_claim_never_writes_and_keeps_unknown_blocker(self):
        real=ledger.advance
        def advance(*args,**kwargs):
            result=real(*args,**kwargs)
            if args[3]=='started':self.stop()
            return result
        with patch.object(ledger,'advance',side_effect=advance):result=self.save_trial()
        self.assertTrue(result['blocked']);self.assertEqual(self.graph.writes,[])
        status=compose.status_owned(HEADERS,AUTH.mailbox_id)
        self.assertTrue(status['operations'][0]['blocked'])
        self.blocked(request='p'*43)

    def test_stop_between_native_reply_creation_and_patch_keeps_unknown_no_retry(self):
        prepared=responses.prepare_owned(HEADERS,AUTH.mailbox_id,'source-1','reply',opener=self.graph)
        self.graph.native_hook=lambda g,i:self.stop()
        result=responses.save_owned(HEADERS,AUTH.mailbox_id,OP,prepared['edit'],source_id='source-1',mode='reply',source_hash=prepared['sourceHash'],opener=self.graph)
        self.assertTrue(result['blocked']);self.assertEqual(len(self.graph.writes),1)
        self.assertEqual(self.graph.writes[0].get_method(),'POST')
        self.assertTrue(compose.status_owned(HEADERS,AUTH.mailbox_id)['operations'][0]['blocked'])

    def test_quota_consumed_by_durable_claims_survives_restart(self):
        self.window['maxOperations']=1;self.put(self.control_key(),self.window)
        self.assertEqual(self.save_trial()['outcome'],'confirmed_performed')
        self.cas.records=copy.deepcopy(self.cas.records);self.blocked(request='p'*43)
        self.assertEqual(len(self.graph.writes),1)
        self.assertEqual(compose.status_owned(HEADERS,AUTH.mailbox_id)['operations'][0]['outcome'],'confirmed_performed')

    def test_reconnect_to_other_account_cannot_reuse_policy(self):
        self.box['oauthAccountId']='another-account';self.blocked()

    def test_wrong_host_and_nonproduction_denied(self):
        with self.assertRaises(Exception):compose.save_owned((('Host','evil.example'),('Origin','https://evil.example')),AUTH.mailbox_id,OP,EDIT,opener=self.graph)
        for environment in ('preview','development',''):
            with patch.dict(os.environ,{'VERCEL_ENV':environment}):self.blocked()
        self.assertEqual(self.graph.writes,[])

    def test_attachment_creation_routes_remain_off(self):
        with self.assertRaises(ledger.base.AttachmentLedgerError):attachment_policy.require_release()
        from api.inboxes import microsoft_legacy_send_policy
        self.assertTrue(send_policy.blocked())

    def test_lost_create_response_blocks_fresh_id_after_reload(self):
        self.graph.lose=True
        result=self.save_trial();self.assertTrue(result['blocked'])
        writes=len(self.graph.writes);self.cas.records=copy.deepcopy(self.cas.records)
        self.blocked(request='p'*43)
        self.assertTrue(compose.status_owned(HEADERS,AUTH.mailbox_id)['operations'][0]['blocked'])
        self.assertEqual(len(self.graph.writes),writes)

    def test_stop_still_allows_reopen_of_confirmed_test_draft(self):
        identifier=self.save_trial()['providerMessageId'];self.stop()
        draft=compose.load_owned(HEADERS,AUTH.mailbox_id,identifier,opener=self.graph)
        self.assertEqual(draft['edit'],EDIT)
        report=acceptance.capabilities(HEADERS,AUTH.mailbox_id)
        self.assertTrue(report['canInspect']);self.assertFalse(report['canWrite'])

    def test_http_capabilities_never_exposes_credentials_or_accepts_client_identity(self):
        from api.inboxes.microsoft_compose_draft_http import microsoft_compose_draft_response
        from io import BytesIO
        from email.message import Message
        from types import SimpleNamespace
        def request(payload):
            raw=json.dumps(payload).encode();headers=Message()
            for k,v in HEADERS:headers[k]=v
            headers['Content-Type']='application/json';headers['Content-Length']=str(len(raw))
            return SimpleNamespace(command='POST',headers=headers,rfile=BytesIO(raw))
        before=copy.deepcopy(self.cas.records)
        result=microsoft_compose_draft_response(request({'action':'capabilities','mailboxId':AUTH.mailbox_id}))
        body=_body(result);self.assertTrue(body['ok']);self.assertTrue(body['mailReadWrite'])
        self.assertNotIn(TOKEN,json.dumps(body));self.assertNotIn('refresh_token',json.dumps(body))
        self.assertEqual(before,self.cas.records);self.assertEqual(self.graph.calls,[])
        result=microsoft_compose_draft_response(request({'action':'create','mailboxId':AUTH.mailbox_id,'requestId':OP,'edit':EDIT,'acceptance':True}))
        self.assertFalse(_body(result)['ok']);self.assertEqual(self.graph.writes,[])
