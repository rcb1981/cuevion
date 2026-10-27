"""Adversarial content/sender tests: fake Graph and isolated CAS; no live I/O."""
import copy
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch
from api.inboxes import test_microsoft_compose_draft as compose_fixture
from api.inboxes.test_microsoft_draft_send import SendFixture, SendGraph
from api.inboxes.test_microsoft_draft_attachment_foundation import AUTH, OP, row
from api.inboxes.test_microsoft_graph import TOKEN
from api.inboxes import microsoft_draft_send_service as send
from api.inboxes import microsoft_draft_send_manifest as manifest
from api.inboxes import microsoft_draft_send_index as index
from api.inboxes import microsoft_draft_send_policy as policy
from api.inboxes import microsoft_outbound_ledger as outbound
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes import microsoft_draft_attachment_snapshot as snapshot
from api.inboxes.microsoft_graph import MicrosoftGraphError


class ContentSecurityTests(SendFixture):
    def setUp(self):
        super().setUp()
        self.active = AUTH
        def verify(_headers, _mailbox, captured):
            if self.active != captured:
                raise owned.MicrosoftMailboxAccessError('microsoft_reconnect_required')
        for p in (patch.object(owned, 'resolve_owned_microsoft_mailbox', side_effect=lambda *_:self.active),
                  patch.object(owned, '_owned_token', side_effect=lambda *_, **kw:(TOKEN, self.active)),
                  patch.object(owned, '_verify_unchanged_owned_mailbox', side_effect=verify)):
            p.start(); self.addCleanup(p.stop)

    def reset(self):
        self.cas.records.clear(); self.graph = SendGraph(); self.review()

    def review(self):
        current = send.drafts.read_current(TOKEN, self.active, 'draft-1', opener=self.graph)
        self.etag = current.snapshot.etag; self.fingerprint = current.snapshot.fingerprint
        return current

    def evidence(self):
        root, _ = index.read(self.active)
        return manifest.read(self.active, OP, root['attempts'][OP])[0]

    def test_sender_fields_are_in_snapshot_and_protected_hash(self):
        before = self.review().snapshot
        for field, value in [('from', {'emailAddress':{'address':'other@example.com'}}),
                             ('sender', {'emailAddress':{'address':'delegate@example.com'}}),
                             ('replyTo', [{'emailAddress':{'address':AUTH.email}}])]:
            self.reset(); self.graph.messages['draft-1'][field] = value
            after = self.review().snapshot
            self.assertNotEqual(before.fingerprint, after.fingerprint)
            self.assertNotEqual(before.protected_content_hash, after.protected_content_hash)

    def test_reviewed_delegate_send_as_unknown_and_foreign_reply_to_are_blocked(self):
        for field, value in [('from', {'emailAddress':{'address':'shared@example.com'}}),
                             ('sender', {'emailAddress':{'address':'delegate@example.com'}}),
                             ('from', None), ('sender', None), ('replyTo', None),
                             ('replyTo', [{'emailAddress':{'address':'other@example.com'}}])]:
            with self.subTest(field=field, value=value):
                self.reset(); self.graph.messages['draft-1'][field] = value; self.review()
                before = copy.deepcopy(self.graph.messages)
                self.assertEqual(self.send()['outcome'], 'confirmed_not_started')
                self.assertEqual(self.graph.sends, []); self.assertEqual(self.graph.writes, [])
                self.assertEqual(self.graph.messages, before)

    def test_explicit_own_reply_to_is_supported_and_hashed(self):
        self.graph.messages['draft-1']['replyTo'] = [{'emailAddress':{'address':AUTH.email}}]
        self.review()
        self.assertEqual(self.send()['outcome'], 'confirmed_performed')
        self.assertEqual(self.evidence()['approved']['fields']['replyTo'], snapshot.digest(self.graph.sent['draft-1']['replyTo']))

    def test_current_token_me_must_match_account_and_mailbox(self):
        for identity in ({'id':'other-account','mail':AUTH.email}, {'id':AUTH.account_id,'mail':'other@example.com'},
                         {'id':AUTH.account_id,'userPrincipalName':AUTH.email}):
            self.reset(); self.graph.me=identity
            self.assertEqual(self.send()['outcome'], 'confirmed_not_started')
            self.assertEqual(self.graph.sends, [])

    def test_manifest_is_durable_before_send_and_contains_only_hashes(self):
        def before_send(graph):
            evidence = self.evidence()
            self.assertEqual(evidence['result'], 'pending')
            approved = evidence['approved']
            self.assertEqual(set(approved['fields']), set(manifest.FIELDS))
            self.assertEqual(approved['attachments'][0]['sha256'], hashlib.sha256(b'song').hexdigest())
            self.assertEqual(approved['attachments'][0]['byteLength'], 4)
        self.graph.on_send = before_send
        self.assertEqual(self.send()['outcome'], 'confirmed_performed')
        raw=json.dumps(self.cas.records)
        for value in (AUTH.email, TOKEN, 'Old body', 'Keep this', 'receiver@example.com', 'song.mp3', 'c29uZw=='):
            self.assertNotIn(value, raw)

    def test_failed_manifest_write_prevents_send(self):
        write = self.cas.write
        def unavailable(config, key, expected, value):
            return (None, {'code':'unavailable'}) if key.startswith(manifest.PREFIX) else write(config,key,expected,value)
        with patch.object(outbound.store, '_write_google_durable_record_if_unchanged', side_effect=unavailable):
            self.assertEqual(self.send()['outcome'], 'confirmed_not_started')
        self.assertEqual(self.graph.sends, [])

    def test_lost_manifest_ack_requires_exact_durable_readback(self):
        write = self.cas.write
        def lost(config, key, expected, value):
            result = write(config,key,expected,value)
            return (None, {'code':'lost'}) if key.startswith(manifest.PREFIX) else result
        with patch.object(outbound.store, '_write_google_durable_record_if_unchanged', side_effect=lost):
            self.assertEqual(self.send()['outcome'], 'confirmed_performed')
        self.assertEqual(len(self.graph.sends), 1)

    def test_byte_replacement_between_preflight_and_final_check_is_blocked(self):
        capture = manifest.capture
        calls = 0
        def swap(*args, **kwargs):
            nonlocal calls
            result = capture(*args, **kwargs); calls += 1
            if calls == 1:self.graph.bytes['ordinary'] = b'evil'
            return result
        with patch.object(manifest, 'capture', side_effect=swap):
            self.assertEqual(self.send()['outcome'], 'confirmed_not_started')
        self.assertEqual(self.graph.sends, [])

    def test_unknown_inline_item_reference_large_or_incomplete_inventory_blocks(self):
        for change in ({'@odata.type':'#microsoft.graph.itemAttachment'},
                       {'@odata.type':'#microsoft.graph.referenceAttachment'}, {'@odata.type':'unknown'},
                       {'@odata.type':None}, {'isInline':True}, {'contentId':'cid@example.com'},
                       {'size':manifest.MAX_FILE_BYTES+1}):
            with self.subTest(change=change):
                self.reset(); self.graph.rows['draft-1'][0].update(change); self.review()
                original = copy.deepcopy(self.graph.rows)
                self.assertEqual(self.send()['outcome'], 'confirmed_not_started')
                self.assertEqual(self.graph.rows, original); self.assertEqual(self.graph.sends, [])
                self.assertEqual(self.graph.writes, [])

    def test_inventory_limit_and_byte_limit_are_fail_closed(self):
        for oversized in (False,True):
            self.reset()
            if oversized:self.graph.bytes['ordinary']=b'x'*(manifest.MAX_FILE_BYTES+1)
            else:self.graph.rows['draft-1']=[row(str(i)) for i in range(11)]
            self.review()
            self.assertEqual(self.send()['outcome'], 'confirmed_not_started'); self.assertEqual(self.graph.sends, [])

    def test_external_outlook_race_is_detected_after_send_not_prevented(self):
        changes = [('toRecipients', [{'emailAddress':{'address':'changed@example.com'}}]),
                   ('ccRecipients', [{'emailAddress':{'address':'cc@example.com'}}]),
                   ('bccRecipients', [{'emailAddress':{'address':'bcc@example.com'}}]),
                   ('subject', 'Externally changed subject'),
                   ('body', {'contentType':'text','content':'Externally changed body'}),
                   ('from', {'emailAddress':{'address':'shared@example.com'}}),
                   ('sender', {'emailAddress':{'address':'delegate@example.com'}}),
                   ('replyTo', [{'emailAddress':{'address':'reply@example.com'}}])]
        for field,value in changes:
            with self.subTest(field=field):
                self.reset()
                self.graph.on_send = lambda g:g.messages['draft-1'].update({field:value})
                result = self.send()
                self.assertEqual(result['outcome'], 'performed_content_mismatch')
                self.assertTrue(result['sentCopyConfirmed']); self.assertFalse(result['deliveryConfirmed'])
                self.assertEqual(len(self.graph.sends), 1)
                before = len(self.graph.calls)
                self.assertEqual(self.send(), result); self.assertEqual(len(self.graph.calls), before)
                with self.assertRaises(MicrosoftGraphError):self.send('n'*43)
                with self.assertRaises(MicrosoftGraphError):index.assert_mutable(AUTH,'draft-1')

    def test_same_name_same_size_replaced_attachment_is_mismatch(self):
        self.graph.on_send = lambda g:g.bytes.update({'ordinary':b'evil'})
        self.assertEqual(self.send()['outcome'], 'performed_content_mismatch')
        self.assertEqual(len(self.graph.sends), 1)
        self.assertEqual(self.graph.writes, [])

    def test_extra_missing_and_changed_attachment_metadata_are_mismatch(self):
        for mode in ('extra','missing','rename','type'):
            self.reset()
            def change(g):
                if mode == 'extra':g.rows['draft-1'].append(row('extra'))
                elif mode == 'missing':g.rows['draft-1']=[];g.messages['draft-1']['hasAttachments']=False
                else:g.rows['draft-1'][0]['name' if mode=='rename' else 'contentType']='changed'
            self.graph.on_send=change
            self.assertEqual(self.send()['outcome'], 'performed_content_mismatch')

    def test_unsupported_new_sent_attachment_cannot_confirm_content(self):
        self.graph.on_send=lambda g:g.rows['draft-1'][0].update({'@odata.type':'#microsoft.graph.itemAttachment'})
        self.assertEqual(self.send()['outcome'], 'uncertain')
        self.assertEqual(len(self.graph.sends),1)

    def test_body_whitespace_html_and_display_names_are_not_assumed_equivalent(self):
        for field,value in [('body',{'contentType':'text','content':'Old body\n'}),
                            ('body',{'contentType':'html','content':'Old body'}),
                            ('toRecipients',[{'emailAddress':{'address':'receiver@example.com','name':'New Name'}}])]:
            self.reset();self.graph.on_send=lambda g:g.messages['draft-1'].update({field:value})
            self.assertEqual(self.send()['outcome'], 'performed_content_mismatch')

    def test_body_type_casing_is_not_assumed_equivalent(self):
        original = compose_fixture.body_response
        def changed(req, value):
            result = original(req, value)
            if '/sentitems/messages/' in req.full_url and not req.full_url.endswith('/$value') and 'outlook.body-content-type' not in req.get_header('Prefer'):
                data = json.loads(result.body); data['body']['contentType'] = 'HTML'
                return compose_fixture.response(data)
            return result
        with patch.object(compose_fixture, 'body_response', side_effect=changed):
            self.assertEqual(self.send()['outcome'], 'performed_content_mismatch')

    def test_missing_then_delayed_mismatching_copy_never_resends(self):
        self.graph.visible=False;self.graph.on_send=lambda g:g.bytes.update({'ordinary':b'evil'})
        self.assertEqual(self.send()['outcome'], 'uncertain')
        self.assertEqual(send.check_owned({},AUTH.mailbox_id,OP)['outcome'], 'uncertain')
        self.graph.visible=True
        self.assertEqual(send.check_owned({},AUTH.mailbox_id,OP)['outcome'], 'performed_content_mismatch')
        self.assertEqual(len(self.graph.sends),1)

    def test_mismatch_survives_reload_reconnect_and_disabled_flags(self):
        self.graph.on_send=lambda g:g.bytes.update({'ordinary':b'evil'})
        result=self.send();before=copy.deepcopy(self.cas.records),len(self.graph.calls)
        self.active=replace(AUTH,credential_generation='z'*43)
        with patch.object(policy,'_DRAFT_SEND_RELEASED',False):
            self.assertEqual(send.inspect_owned({},AUTH.mailbox_id),[result])
            self.assertEqual(send.check_owned({},AUTH.mailbox_id,OP),result)
            with self.assertRaises(MicrosoftGraphError):index.assert_mutable(self.active,'draft-1')
        self.assertEqual((self.cas.records,len(self.graph.calls)),before)

    def test_reconnect_pending_probe_revalidates_actual_token_identity(self):
        self.graph.visible=False;self.send();self.active=replace(AUTH,credential_generation='z'*43)
        self.graph.me['id']='other-account';before=copy.deepcopy(self.cas.records)
        with self.assertRaises(MicrosoftGraphError):send.check_owned({},AUTH.mailbox_id,OP)
        self.assertEqual(self.cas.records,before);self.assertEqual(len(self.graph.sends),1)

    def test_concurrent_mismatch_checks_are_monotonic(self):
        self.graph.visible=False;self.graph.on_send=lambda g:g.bytes.update({'ordinary':b'evil'});self.send()
        self.graph.visible=True;barrier=threading.Barrier(2);compare=manifest.compare_sent
        def concurrent(*args,**kwargs):
            result=compare(*args,**kwargs);barrier.wait(timeout=5);return result
        with patch.object(manifest,'compare_sent',side_effect=concurrent),ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:send.check_owned({},AUTH.mailbox_id,OP),range(2)))
        self.assertTrue(all(r['outcome']=='performed_content_mismatch' for r in results))
        self.assertEqual(len(self.graph.sends),1)

    def test_conflicting_simultaneous_probes_keep_mismatch_absorbing(self):
        self.graph.visible=False;self.send()
        item=index.read(AUTH)[0]['attempts'][OP];barrier=threading.Barrier(2)
        def commit(result):
            barrier.wait(timeout=5);manifest.settle(AUTH,OP,item,result)
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(commit,['matched','mismatch']))
        index.confirm_sent_copy(AUTH,OP)
        self.assertEqual(send.inspect_owned({},AUTH.mailbox_id)[0]['outcome'],'performed_content_mismatch')
        manifest.settle(AUTH,OP,item,'matched')
        self.assertEqual(self.evidence()['result'],'mismatch')

    def test_lost_result_ack_and_crash_before_identity_commit_recover(self):
        self.graph.visible=False;self.send();self.graph.visible=True
        with patch.object(index,'confirm_sent_copy',side_effect=MicrosoftGraphError('microsoft_draft_send_unavailable')):
            with self.assertRaises(MicrosoftGraphError):send.check_owned({},AUTH.mailbox_id,OP)
        self.assertEqual(self.evidence()['result'],'matched')
        before=len(self.graph.calls)
        self.assertEqual(send.check_owned({},AUTH.mailbox_id,OP)['outcome'],'confirmed_performed')
        self.assertEqual(len(self.graph.calls),before);self.assertEqual(len(self.graph.sends),1)

    def test_historical_identity_only_receipt_is_never_content_confirmed(self):
        self.send()
        for key in list(self.cas.records):
            if key.startswith(manifest.PREFIX):del self.cas.records[key]
        before=len(self.graph.calls)
        result=send.check_owned({},AUTH.mailbox_id,OP)
        self.assertEqual(result['outcome'],'uncertain');self.assertTrue(result['sentCopyConfirmed'])
        self.assertEqual(len(self.graph.calls),before)
        with self.assertRaises(MicrosoftGraphError):index.assert_mutable(AUTH,'draft-1')

    def test_manifest_wrong_scope_binding_and_malformed_hash_fail_closed(self):
        self.send()
        key=next(k for k in self.cas.records if k.startswith(manifest.PREFIX));original=self.cas.records[key]
        for field,value in [('scope','0'*64),('binding','0'*64),('draftId','other'),('result','unlocked'),('approved',{})]:
            record=json.loads(original);record[field]=value;self.cas.records[key]=json.dumps(record)
            with self.assertRaises(MicrosoftGraphError):send.inspect_owned({},AUTH.mailbox_id)
        self.cas.records[key]=original

    def test_missing_malformed_or_wrong_id_attachment_bytes_never_send(self):
        get = manifest._get_json
        for change in ({'contentBytes':None}, {'contentBytes':'not base64!'}, {'id':'other-attachment'},
                       {'size':5}, {'@odata.type':'#microsoft.graph.referenceAttachment'}):
            self.reset()
            def bad(path, *args, **kwargs):
                value=get(path,*args,**kwargs)
                return {**value,**change} if '/attachments/ordinary' in path else value
            with patch.object(manifest,'_get_json',side_effect=bad):
                self.assertEqual(self.send()['outcome'],'confirmed_not_started')
            self.assertEqual(self.graph.sends,[])

    def test_continuation_or_duplicate_attachment_inventory_never_sends(self):
        get=manifest._get_json
        for mode in ('continuation','duplicate'):
            self.reset()
            def bad(path,*args,**kwargs):
                value=get(path,*args,**kwargs)
                if '/attachments?' in path:
                    if mode=='continuation':value['@odata.nextLink']='https://evil.example/attachments'
                    else:value['value']*=2
                return value
            with patch.object(manifest,'_get_json',side_effect=bad):
                self.assertEqual(self.send()['outcome'],'confirmed_not_started')
            self.assertEqual(self.graph.sends,[])

    def test_changed_sent_content_during_observation_stays_uncertain(self):
        self.graph.visible=False;self.send();self.graph.visible=True
        get=manifest._get_json;count=0
        def drift(path,*args,**kwargs):
            nonlocal count
            if '/sentitems/messages/' in path and '/attachments' not in path:
                count+=1
                if count==2:self.graph.sent['draft-1']['body']['content']='during read'
            return get(path,*args,**kwargs)
        with patch.object(manifest,'_get_json',side_effect=drift),self.assertRaises(MicrosoftGraphError):
            send.check_owned({},AUTH.mailbox_id,OP)
        self.assertEqual(send.inspect_owned({},AUTH.mailbox_id)[0]['outcome'],'uncertain')
        self.assertEqual(len(self.graph.sends),1)

    def test_stable_attachment_multiset_allows_new_ids_but_preserves_duplicates(self):
        self.graph.rows['draft-1'].append(row('duplicate'));self.review()
        def renamed_ids(g):
            g.rows['draft-1'][0]['id']='sent-ordinary';g.rows['draft-1'][1]['id']='sent-duplicate'
        self.graph.on_send=renamed_ids
        self.assertEqual(self.send()['outcome'],'confirmed_performed')
        self.assertEqual(len(self.evidence()['approved']['attachments']),2)

    def test_no_attachment_manifest_and_sent_copy_are_verified(self):
        self.graph.rows['draft-1']=[];self.graph.messages['draft-1']['hasAttachments']=False;self.review()
        self.assertEqual(self.send()['outcome'],'confirmed_performed')
        self.assertEqual(self.evidence()['approved']['attachments'],[])

    def test_http_exposes_mismatch_without_delivery_or_content_confirmation(self):
        from api.inboxes.test_microsoft_http import _body
        self.graph.on_send=lambda g:g.bytes.update({'ordinary':b'evil'})
        result=_body(self.call_send())
        self.assertTrue(result['ok']);self.assertEqual(result['receipt']['outcome'],'performed_content_mismatch')
        self.assertTrue(result['receipt']['sentCopyConfirmed']);self.assertFalse(result['receipt']['deliveryConfirmed'])
