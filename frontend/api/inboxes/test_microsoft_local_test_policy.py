"""Local acceptance policy integration: real guards, fake Graph, isolated CAS.

No actual owner's identity or recipient approval is present in these fixtures.
The only real mailbox string is the user's requested, hard-pinned address.
"""
import copy
import importlib
import json
import os
import time
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from api.inboxes import microsoft_local_test_policy as local
from api.inboxes import microsoft_draft_send_service as service
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes import microsoft_outbound_ledger as outbound
from api.inboxes import microsoft_draft_send_index as index
from api.inboxes import microsoft_draft_send_policy as release
from api.inboxes import microsoft_draft_send_manifest as manifest
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes.test_microsoft_draft_send import SendFixture, SendGraph, AUTH, OP, TOKEN
from api.inboxes.test_microsoft_mailbox_access import valid_member, mailbox, resolved

REAL_SEND = local.authorize_send
REAL_STATUS = local.authorize_status
REAL_OWNER = owned.resolve_owned_microsoft_mailbox
REAL_TOKEN = owned._owned_token
REAL_VERIFY = owned._verify_unchanged_owned_mailbox
HEADERS = (('Host','localhost:8443'),('Origin','https://localhost:8443'))


class LocalFixture(SendFixture):
    def setUp(self):
        super().setUp()
        self.active=replace(AUTH,email=local.APPROVED_MAILBOX)
        self.member=valid_member()
        self.box=mailbox(id=AUTH.mailbox_id,email=local.APPROVED_MAILBOX,oauthAccountId=AUTH.account_id,
                         oauthCredentialGeneration=AUTH.credential_generation)
        self.resolution_calls=[]
        def resolve(*a,**kw):
            self.resolution_calls.append(a)
            return resolved(member=self.member,box=self.box)
        now=int(time.time())
        self.cfg={'v':1,'origin':'https://localhost:8443','storeUrl':'http://127.0.0.1:8799',
                  'storeId':'a'*64,'ownerUserId':self.member.user_id,'workspaceId':self.member.workspace_id,
                  'ownerEmail':AUTH.owner_email,'accountId':AUTH.account_id,'mailboxId':AUTH.mailbox_id,
                  'mailboxAddress':local.APPROVED_MAILBOX,'authorization':{'draftId':'draft-1',
                  'recipients':['receiver@example.com'],'notBefore':now-1,'expiresAt':now+300}}
        for p in (patch.dict(os.environ,{'VERCEL_ENV':'development','CUEVION_APP_URL':self.cfg['origin'],
                       'KV_REST_API_URL':self.cfg['storeUrl'],'KV_REST_API_TOKEN':'isolated-fake-store-token',
                       local.STOP_ENV:'0'},clear=True),
                  patch.object(outbound.store,'_resolve_durable_store_config',side_effect=lambda:{'rest_url':os.environ.get('KV_REST_API_URL'),'rest_token':'isolated-fake-store-token'}),
                  patch.object(owned,'resolve_owned_managed_inbox_record',side_effect=resolve),
                  patch.object(owned,'resolve_owned_microsoft_mailbox',REAL_OWNER),
                  patch.object(owned,'_owned_token',REAL_TOKEN),
                  patch.object(owned,'_verify_unchanged_owned_mailbox',REAL_VERIFY),
                  patch.object(owned,'ensure_owned_microsoft_access_token',return_value={'provider':'microsoft','access_token':TOKEN,'scope':'User.Read Mail.Read Mail.ReadWrite Mail.Send'}),
                  patch.object(local,'authorize_send',REAL_SEND),patch.object(local,'authorize_status',REAL_STATUS)):
            p.start();self.addCleanup(p.stop)
        self.cas.records.clear()
        self.put(local.MARKER_KEY,{'v':1,'purpose':'microsoft-local-test-only','storeId':self.cfg['storeId']})
        self.configure()
        self.graph=SendGraph();self.graph.me={'id':AUTH.account_id,'mail':local.APPROVED_MAILBOX}
        self.graph.messages['draft-1'].update({'from':{'emailAddress':{'address':local.APPROVED_MAILBOX}},
            'sender':{'emailAddress':{'address':local.APPROVED_MAILBOX}},'hasAttachments':False})
        self.graph.rows['draft-1']=[]
        self.review()
        self.graph.calls.clear();self.resolution_calls.clear()

    def put(self,key,value):self.cas.records[key]=json.dumps(value,sort_keys=True,separators=(',',':'))
    def configure(self):
        os.environ[local.CONFIG_ENV]=json.dumps(self.cfg)
        self.put(local.CONTROL_KEY,{'v':1,'storeId':self.cfg['storeId'],'policyHash':local.digest(self.cfg),'stopped':False})
    def review(self):
        current=service.drafts.read_current(TOKEN,self.active,'draft-1',opener=self.graph)
        self.etag=current.snapshot.etag;self.fingerprint=current.snapshot.fingerprint
    def send(self,attempt=OP,*,draft_id='draft-1',headers=HEADERS):
        return service.send_owned(headers,self.active.mailbox_id,draft_id,attempt,etag=self.etag,snapshot_hash=self.fingerprint,opener=self.graph)
    def blocked(self,**kw):
        with self.assertRaises((MicrosoftGraphError,owned.MicrosoftMailboxAccessError)):self.send(**kw)
        self.assertEqual(self.graph.sends,[])
    def stop(self):
        control=json.loads(self.cas.records[local.CONTROL_KEY]);control['stopped']=True;self.put(local.CONTROL_KEY,control)


class LocalPolicyTests(LocalFixture):
    def test_correct_actual_account_sends_once_and_records_permanent_fuse(self):
        self.assertEqual(self.send()['outcome'],'confirmed_performed')
        self.assertEqual(self.send()['outcome'],'confirmed_performed')
        self.assertEqual(len(self.graph.sends),1)
        fuse=json.loads(self.cas.records[local.FUSE_KEY]);self.assertEqual(fuse['attemptId'],OP)
        self.assertNotIn('receiver@example.com',json.dumps(self.cas.records))
        self.assertNotIn(local.APPROVED_MAILBOX,json.dumps(self.cas.records))

    def test_actual_me_must_match_id_and_mail_not_only_address(self):
        for field,value in [('id','different-account'),('mail','other@live.nl')]:
            saved=copy.deepcopy(self.cas.records);self.graph.me[field]=value
            self.assertEqual(self.send()['outcome'],'confirmed_not_started')
            self.assertEqual(self.graph.sends,[])
            self.cas.records=saved;self.graph.me={'id':AUTH.account_id,'mail':local.APPROVED_MAILBOX}

    def test_wrong_owner_user_workspace_or_role_is_denied_before_graph(self):
        original=self.member
        for changes in ({'user_id':'other'},{'workspace_id':'other'},{'email':'other@example.com'},{'membership_role':'member'}):
            self.member=replace(original,**changes);self.blocked()
        self.assertEqual(self.graph.calls,[])

    def test_wrong_mailbox_id_address_and_account_configuration_are_denied(self):
        original=copy.deepcopy(self.cfg)
        for field,value in [('mailboxId','another-box'),('mailboxAddress','another@live.nl'),('accountId','another-account')]:
            self.cfg=copy.deepcopy(original);self.cfg[field]=value;self.configure();self.blocked()
        self.assertEqual(self.graph.calls,[])

    def test_reconnect_same_account_preserves_status_different_account_is_denied(self):
        self.graph.visible=False;self.assertEqual(self.send()['outcome'],'uncertain')
        self.box['oauthCredentialGeneration']='z'*43
        self.stop();os.environ[local.STOP_ENV]='1'
        self.assertEqual(service.history_owned(HEADERS,AUTH.mailbox_id)['entries'][0]['receipt']['outcome'],'uncertain')
        self.graph.visible=True
        self.assertEqual(service.check_owned(HEADERS,AUTH.mailbox_id,OP)['outcome'],'confirmed_performed')
        self.box['oauthAccountId']='different'
        with self.assertRaises(MicrosoftGraphError):service.check_owned(HEADERS,AUTH.mailbox_id,OP)
        self.assertEqual(len(self.graph.sends),1)

    def test_recipient_allowlist_is_empty_by_default_and_must_be_explicit(self):
        self.cfg['authorization']['recipients']=[];self.configure();self.blocked()
        self.assertNotIn(local.FUSE_KEY,self.cas.records)
        self.assertEqual(self.graph.calls,[])

    def test_graph_recipients_cc_bcc_and_attachments_are_checked(self):
        original=copy.deepcopy(self.graph.messages['draft-1']);records=copy.deepcopy(self.cas.records)
        changes=[{'toRecipients':[{'emailAddress':{'address':'wrong@example.com'}}]},
                 {'toRecipients':[{'emailAddress':{'address':'Receiver@example.com'}}]},
                 {'ccRecipients':[{'emailAddress':{'address':'copy@example.com'}}]},
                 {'bccRecipients':[{'emailAddress':{'address':'hidden@example.com'}}]}]
        for change in changes:
            self.cas.records=copy.deepcopy(records);self.graph.messages['draft-1']={**copy.deepcopy(original),**change};self.review()
            self.assertEqual(self.send()['outcome'],'confirmed_not_started');self.assertEqual(self.graph.sends,[])
        from api.inboxes.test_microsoft_compose_draft import row
        self.cas.records=records;self.graph.messages['draft-1']=original;self.graph.messages['draft-1']['hasAttachments']=True
        self.graph.rows['draft-1']=[row()];self.review()
        self.assertEqual(self.send()['outcome'],'confirmed_not_started');self.assertEqual(self.graph.sends,[])

    def test_second_draft_and_second_attempt_are_denied_even_after_no_send(self):
        self.blocked(draft_id='draft-2')
        self.graph.me['id']='wrong'
        self.assertEqual(self.send()['outcome'],'confirmed_not_started')
        self.graph.me['id']=AUTH.account_id
        self.blocked(attempt='n'*43)
        self.cfg['authorization']['draftId']='draft-2';self.configure()
        self.blocked(attempt='n'*43,draft_id='draft-2')

    def test_reimport_and_reload_do_not_reset_fuse(self):
        result=self.send();saved=copy.deepcopy(self.cas.records)
        importlib.reload(local)
        self.assertEqual(self.send(),result)
        with self.assertRaises(MicrosoftGraphError):self.send(attempt='n'*43)
        self.assertEqual(self.cas.records,saved);self.assertEqual(len(self.graph.sends),1)

    def test_default_stop_env_and_shared_control_both_block(self):
        os.environ.pop(local.STOP_ENV);self.blocked();os.environ[local.STOP_ENV]='0'
        self.stop();self.blocked();self.assertEqual(self.graph.calls,[])

    def test_stop_after_send_started_cas_prevents_graph_and_preserves_uncertainty(self):
        write=self.cas.write
        def stopping(config,key,expected,value):
            result=write(config,key,expected,value)
            if value.get('phase')=='send_started':self.stop()
            return result
        with patch.object(outbound.store,'_write_google_durable_record_if_unchanged',side_effect=stopping):
            result=self.send()
        self.assertEqual(result['outcome'],'uncertain');self.assertEqual(self.graph.sends,[])
        self.assertEqual(service.check_owned(HEADERS,AUTH.mailbox_id,OP)['outcome'],'uncertain')
        self.assertIn(local.FUSE_KEY,self.cas.records)

    def test_stop_during_preflight_prevents_graph_but_consumes_authorization(self):
        original=manifest.capture
        def stopping(*a,**kw):
            result=original(*a,**kw);self.stop();return result
        with patch.object(manifest,'capture',side_effect=stopping):
            self.assertEqual(self.send()['outcome'],'confirmed_not_started')
        self.assertEqual(self.graph.sends,[]);self.assertIn(local.FUSE_KEY,self.cas.records)

    def test_expired_future_long_or_missing_window_blocks(self):
        original=copy.deepcopy(self.cfg['authorization']);now=int(time.time())
        for start,end in [(now-20,now-1),(now+20,now+100),(now-1,now+local.MAX_AUTH_SECONDS+1),(None,None),(True,now+10)]:
            self.cfg['authorization']={**original,'notBefore':start,'expiresAt':end};self.configure();self.blocked()

    def test_status_survives_expiry_empty_recipient_and_source_flag_shutdown(self):
        self.graph.visible=False;self.send();self.stop()
        self.cfg['authorization']['expiresAt']=int(time.time())-1
        self.cfg['authorization']['recipients']=[];os.environ[local.CONFIG_ENV]=json.dumps(self.cfg)
        with patch.object(release,'_DRAFT_SEND_RELEASED',False):
            self.assertEqual(service.history_owned(HEADERS,AUTH.mailbox_id)['entries'][0]['receipt']['outcome'],'uncertain')
            self.graph.visible=True;self.assertEqual(service.check_owned(HEADERS,AUTH.mailbox_id,OP)['outcome'],'confirmed_performed')
        self.assertEqual(len(self.graph.sends),1)

    def test_remote_or_mismatched_store_never_reaches_owner_storage(self):
        for url in ['https://production.example.com','http://localhost:8799','http://127.0.0.1:8799/proxy','http://127.0.0.1:8799@production.example.com','http://127.0.0.1:8800']:
            os.environ['KV_REST_API_URL']=url;self.blocked()
        self.assertEqual(self.resolution_calls,[]);self.assertEqual(self.graph.calls,[])

    def test_missing_mismatched_or_invalid_marker_blocks_before_owner_storage(self):
        for marker in [None,{'v':1,'purpose':'production','storeId':self.cfg['storeId']},{'v':True,'purpose':'microsoft-local-test-only','storeId':self.cfg['storeId']}]:
            if marker is None:self.cas.records.pop(local.MARKER_KEY,None)
            else:self.put(local.MARKER_KEY,marker)
            self.blocked()
        self.assertEqual(self.resolution_calls,[])

    def test_storage_errors_never_authorize_send(self):
        self.cas.bad_read=True;self.blocked();self.cas.bad_read=False
        self.cas.bad_write=True;self.blocked();self.assertEqual(self.graph.sends,[])

    def test_lost_fuse_ack_and_restart_never_repeat_execution(self):
        write=self.cas.write
        def lost(config,key,expected,value):
            result=write(config,key,expected,value)
            return (None,{'code':'lost'}) if key==local.FUSE_KEY else result
        with patch.object(outbound.store,'_write_google_durable_record_if_unchanged',side_effect=lost):self.blocked()
        self.assertIn(local.FUSE_KEY,self.cas.records)
        self.assertEqual(self.send()['outcome'],'uncertain')
        self.blocked(attempt='n'*43)

    def test_production_preview_hosts_and_spoofed_forwarding_are_denied(self):
        for headers in [(('Host','app.cuevion.com'),('Origin','https://app.cuevion.com')),
                        HEADERS+(('X-Forwarded-Host','app.cuevion.com'),),
                        (('Host','localhost:8443'),('Origin','https://evil.example'))]:
            self.blocked(headers=headers)
        for env in ('production','preview',''):
            with patch.dict(os.environ,{'VERCEL_ENV':env}):self.blocked()
        with patch.dict(os.environ,{'VERCEL':'1'}):self.blocked()
        self.assertEqual(self.resolution_calls,[])

    def test_missing_config_fields_duplicate_json_and_unsafe_configuration_block(self):
        for key in list(self.cfg):
            invalid=copy.deepcopy(self.cfg);del invalid[key];os.environ[local.CONFIG_ENV]=json.dumps(invalid);self.blocked()
        os.environ[local.CONFIG_ENV]='{"v":1,"v":1}';self.blocked()
        os.environ.pop(local.CONFIG_ENV);self.blocked()
        self.assertEqual(self.graph.calls,[])

    def test_concurrent_attempts_share_one_durable_fuse(self):
        def run(attempt):
            try:return self.send(attempt=attempt)['outcome']
            except Exception:return 'blocked'
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,[OP,'n'*43]))
        self.assertEqual(len(self.graph.sends),1)
        self.assertIn('confirmed_performed',results);self.assertIn('blocked',results)

    def test_invalid_shared_control_never_arms_the_test(self):
        valid=json.loads(self.cas.records[local.CONTROL_KEY])
        for value in [None,{**valid,'stopped':0},{**valid,'policyHash':'b'*64},{**valid,'v':True}]:
            if value is None:self.cas.records.pop(local.CONTROL_KEY,None)
            else:self.put(local.CONTROL_KEY,value)
            self.blocked()
        self.assertEqual(self.graph.calls,[])

    def test_slow_control_read_cannot_extend_authorization_expiry(self):
        read=self.cas.read;now=time.time();expired=[False];control_reads=[0]
        def slow(config,key):
            result=read(config,key)
            if key==local.CONTROL_KEY:
                control_reads[0]+=1
                if control_reads[0]>=2:expired[0]=True
            return result
        with patch.object(outbound.store,'_read_durable_record_snapshot',side_effect=slow),patch.object(
                local.time,'time',side_effect=lambda:now+3600 if expired[0] else now):
            self.blocked()
        self.assertEqual(self.graph.sends,[])

    def test_proxy_environment_blocks_before_owner_storage(self):
        for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
            with patch.dict(os.environ,{name:'http://proxy.example.com'}):self.blocked()
        self.assertEqual(self.resolution_calls,[])

    def test_local_policy_does_not_override_disabled_release(self):
        with patch.object(release,'_DRAFT_SEND_RELEASED',False):self.blocked()
        self.assertNotIn(local.FUSE_KEY,self.cas.records)
        self.assertEqual(self.graph.calls,[])
