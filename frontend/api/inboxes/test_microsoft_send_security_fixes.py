"""Security regressions: fake Graph, shared in-memory CAS, no live credentials."""
import copy
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch
import unittest

from api.inboxes.test_microsoft_draft_send import SendFixture
from api.inboxes.test_microsoft_draft_attachment_foundation import AUTH, OP
from api.inboxes.test_microsoft_draft_attachment_executor import new_file
from api.inboxes.test_microsoft_compose_draft import EDIT
from api.inboxes.test_microsoft_graph import TOKEN
from api.inboxes.test_microsoft_http import _body
from api.inboxes import microsoft_draft_send_service as send
from api.inboxes import microsoft_draft_send_index as index
from api.inboxes import microsoft_draft_send_policy as policy
from api.inboxes import microsoft_compose_draft_service as compose
from api.inboxes import microsoft_compose_draft_policy as compose_policy
from api.inboxes import microsoft_draft_attachment_executor as attachments
from api.inboxes import microsoft_draft_attachment_service as foundation
from api.inboxes import microsoft_draft_attachment_policy as attachment_policy
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes import microsoft_draft_editor as editor
from api.inboxes import microsoft_rich_draft as rich
from api.inboxes import microsoft_draft_discard as discard
from api.inboxes import microsoft_outbound_ledger as ledger
from api.inboxes import microsoft_legacy_send_policy as legacy_policy
from api.inboxes import microsoft_outbound_http as legacy_http
from api.inboxes import microsoft_outbound_service as legacy
from api.inboxes.test_microsoft_outbound_http import request, COMPOSE
from api.inboxes.test_microsoft_preview_http import _on_preview
from api.inboxes.test_microsoft_preview_origin import PREVIEW
from api.inboxes.microsoft_graph import MicrosoftGraphError


class SendSecurityTests(SendFixture):
    def setUp(self):
        super().setUp()
        self.active = AUTH
        self.active_token = TOKEN
        for operation in (
            patch.object(owned, 'resolve_owned_microsoft_mailbox', side_effect=lambda *_: self.active),
            patch.object(owned, '_owned_token', side_effect=lambda *_, **kw: (self.active_token, self.active)),
            patch.object(owned, '_verify_unchanged_owned_mailbox', side_effect=self.verify),
        ):
            operation.start(); self.addCleanup(operation.stop)

    def verify(self, headers, mailbox_id, captured):
        if self.active != captured:
            raise owned.MicrosoftMailboxAccessError('microsoft_reconnect_required')

    def pending_send(self):
        self.graph.visible = False
        self.graph.copy_on_send = False
        self.assertEqual(self.send()['outcome'], 'uncertain')

    def reconnect(self):
        self.active = replace(AUTH, credential_generation='z' * 43)
        self.active_token = 'new-offline-token-after-reconnect'

    def operations(self):
        return {
            'compose': lambda: compose.save_owned({}, AUTH.mailbox_id, 'c' * 43, EDIT,
                message_id='draft-1', etag=self.etag, opener=self.graph),
            'text': lambda: owned.save_owned_microsoft_editable_draft({}, AUTH.mailbox_id,
                'draft-1', etag=self.etag, edit={k: EDIT[k] for k in ('subject','to','cc','body')}),
            'html': lambda: owned.save_owned_microsoft_editable_draft({}, AUTH.mailbox_id,
                'draft-1', etag=self.etag, edit={k: EDIT[k] for k in ('subject','to','cc')}, metadata_only=True),
            'add': lambda: attachments.execute_owned_attachment_request({}, AUTH.mailbox_id,
                'draft-1', 'a' * 43, 'add', expected_fingerprint=self.fingerprint,
                files=[new_file()], opener=self.graph),
            'remove': lambda: attachments.execute_owned_attachment_request({}, AUTH.mailbox_id,
                'draft-1', 'a' * 43, 'remove', expected_fingerprint=self.fingerprint,
                attachment_id='ordinary', opener=self.graph),
            'discard': lambda: owned.discard_owned_microsoft_draft({}, AUTH.mailbox_id, 'draft-1'),
            'attachment_prepare': lambda: foundation.prepare_owned_attachment_operation({},
                AUTH.mailbox_id, 'draft-1', 'p' * 43, 'remove', expected_fingerprint=self.fingerprint,
                attachment_id='ordinary', opener=self.graph),
            'attachment_start': lambda: foundation.start_owned_attachment_operation({},
                AUTH.mailbox_id, 'draft-1', 'p' * 43, 'k' * 43, opener=self.graph),
        }

    def test_every_mutator_blocked_with_send_off_before_and_after_reconnect(self):
        self.pending_send()
        saved = copy.deepcopy(self.cas.records)
        calls = len(self.graph.calls)
        with patch.object(policy, '_DRAFT_SEND_RELEASED', False):
            for reconnected in (False, True):
                if reconnected:
                    self.reconnect()
                for name, operation in self.operations().items():
                    with self.subTest(reconnected=reconnected, mutation=name), self.assertRaises(MicrosoftGraphError) as error:
                        operation()
                    self.assertEqual(error.exception.code, 'microsoft_draft_send_blocked')
        self.assertEqual(self.cas.records, saved)
        self.assertEqual(len(self.graph.calls), calls)
        self.assertEqual(self.graph.writes, [])

    def test_all_source_flags_off_cannot_restore_mutability(self):
        self.pending_send()
        with patch.object(policy, '_DRAFT_SEND_RELEASED', False), patch.object(
                compose_policy, '_COMPOSE_DRAFTS_RELEASED', False), patch.object(
                attachment_policy, '_DRAFT_ATTACHMENTS_RELEASED', False):
            for name, operation in self.operations().items():
                with self.subTest(mutation=name), self.assertRaises((MicrosoftGraphError, attachments.ledger.AttachmentLedgerError)):
                    operation()
        self.assertEqual(self.graph.writes, [])

    def test_missing_outbound_record_retains_index_barrier_after_reconnect(self):
        index.reserve(AUTH, OP, 'draft-1', 'a' * 64)
        self.reconnect()
        with patch.object(policy, '_DRAFT_SEND_RELEASED', False):
            self.assertEqual(send.check_owned({}, AUTH.mailbox_id, OP)['outcome'], 'uncertain')
            with self.assertRaises(MicrosoftGraphError):
                self.operations()['discard']()
        self.assertEqual(self.graph.sends, [])

    def test_unreadable_index_never_authorizes_mutation(self):
        read = self.cas.read
        def broken_index(config, key):
            return (None, None, {'code':'unavailable'}) if key.startswith(index.PREFIX) else read(config, key)
        with patch.object(ledger.store, '_read_durable_record_snapshot', side_effect=broken_index), patch.object(policy, '_DRAFT_SEND_RELEASED', False):
            for name, operation in self.operations().items():
                with self.subTest(mutation=name), self.assertRaises((MicrosoftGraphError, attachments.ledger.AttachmentLedgerError)):
                    operation()
        self.assertEqual(self.graph.sends, [])
        self.assertEqual(self.graph.writes, [])

    def test_legacy_editors_and_discard_recheck_barrier_at_write_boundary(self):
        for name, module, function in [('text', editor, 'save_exact_microsoft_draft'),
                ('html', rich, 'save_rich_draft_metadata'),
                ('discard', discard, 'discard_exact_microsoft_draft_to_deleted')]:
            self.cas.records.clear()
            def concurrent_start(*args, **kwargs):
                index.reserve(AUTH, OP, 'draft-1', 'a' * 64)
                kwargs['before_write']()
                self.fail('Mutation crossed a newly observed send barrier')
            with self.subTest(mutation=name), patch.object(module, function, side_effect=concurrent_start), self.assertRaises(MicrosoftGraphError):
                self.operations()[name]()

    def test_compose_rechecks_barrier_before_provider_patch(self):
        write = compose.graph.write_once
        def concurrent_start(*args, **kwargs):
            index.reserve(AUTH, OP, 'draft-1', 'a' * 64)
            return write(*args, **kwargs)
        with patch.object(compose.graph, 'write_once', side_effect=concurrent_start):
            result = self.operations()['compose']()
        self.assertEqual(result['outcome'], 'confirmed_not_performed')
        self.assertEqual(self.graph.writes, [])

    def test_attachments_recheck_barrier_before_provider_mutation(self):
        for name in ('add', 'remove'):
            self.cas.records.clear()
            def concurrent_start(*args, **kwargs):
                index.reserve(AUTH, OP, 'draft-1', 'a' * 64)
                kwargs['before_write']()
                self.fail('Attachment write crossed send barrier')
            with self.subTest(mutation=name), patch.object(attachments.graph, 'mutate_attachment', side_effect=concurrent_start):
                result = self.operations()[name]()
                self.assertNotEqual(result['outcome'], 'confirmed_performed')
        self.assertEqual(self.graph.writes, [])

    def test_live_send_lease_then_persisted_barrier_block_concurrent_mutators(self):
        lock = threading.Lock()
        entered, finish = threading.Event(), threading.Event()
        submit = send.transport.submit_prepared_microsoft_draft
        def acquire(*args):
            return {'status':'acquired','token':'lease-token'} if lock.acquire(False) else {'status':'held'}
        def release(*args):
            lock.release(); return {'status':'released'}
        def paused(*args, **kwargs):
            entered.set()
            if not finish.wait(5):
                raise AssertionError('test send timed out')
            return submit(*args, **kwargs)
        self.graph.visible = False
        with patch.object(owned, 'acquire_mailbox_mutation_lease', side_effect=acquire), patch.object(
                owned, 'release_mailbox_mutation_lease', side_effect=release), patch.object(
                send.transport, 'submit_prepared_microsoft_draft', side_effect=paused), ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(self.send)
            try:
                self.assertTrue(entered.wait(5))
                for name, operation in self.operations().items():
                    with self.subTest(mutation=name), self.assertRaises((MicrosoftGraphError, attachments.ledger.AttachmentLedgerError, owned.MicrosoftMailboxAccessError)):
                        operation()
            finally:
                finish.set()
            self.assertEqual(future.result(timeout=5)['outcome'], 'uncertain')
            with self.assertRaises(MicrosoftGraphError):
                self.operations()['discard']()
        self.assertEqual(len(self.graph.sends), 1)
        self.assertEqual(self.graph.writes, [])

    def test_confirmed_sent_history_survives_reconnect_without_graph_or_writes(self):
        original = self.send()
        self.assertEqual(original['outcome'], 'confirmed_performed')
        before = copy.deepcopy(self.cas.records), len(self.graph.calls)
        self.reconnect()
        with patch.object(policy, '_DRAFT_SEND_RELEASED', False):
            self.assertEqual(send.inspect_owned({}, AUTH.mailbox_id), [original])
            self.assertEqual(send.check_owned({}, AUTH.mailbox_id, OP), original)
        self.assertEqual((self.cas.records, len(self.graph.calls)), before)

    def test_confirmed_not_started_history_survives_reconnect(self):
        original = self.send(fingerprint='0' * 64)
        self.assertEqual(original['outcome'], 'confirmed_not_started')
        saved = copy.deepcopy(self.cas.records)
        self.reconnect()
        with patch.object(policy, '_DRAFT_SEND_RELEASED', False):
            self.assertEqual(send.check_owned({}, AUTH.mailbox_id, OP), original)
            index.assert_mutable(self.active, 'draft-1')
        self.assertEqual(self.cas.records, saved)
        self.assertEqual(self.graph.sends, [])

    def test_new_credentials_only_read_exact_sent_copy_and_keep_original_scope(self):
        self.graph.visible = False
        self.send()
        original = json.loads(self.cas.records[ledger._key(AUTH, OP)])
        self.reconnect()
        self.graph.visible = True
        self.graph.calls.clear()
        with patch.object(policy, '_DRAFT_SEND_RELEASED', False):
            result = send.check_owned({}, AUTH.mailbox_id, OP)
        self.assertEqual(result['outcome'], 'confirmed_performed')
        self.assertTrue(self.graph.calls)
        for req in self.graph.calls:
            self.assertEqual(req.get_method(), 'GET')
            self.assertTrue('/sentitems' in req.full_url or '/me?' in req.full_url)
            self.assertEqual(req.get_header('Authorization'), 'Bearer ' + self.active_token)
        record = json.loads(self.cas.records[ledger._key(AUTH, OP)])
        self.assertEqual(record['scope'], original['scope'])
        self.assertEqual(record['digest'], original['digest'])
        self.assertEqual(len(index.inspect(self.active)), 1)
        self.assertEqual(len(self.graph.sends), 1)
        # The original generation-bound execution API remains strict.
        with self.assertRaises(ledger.OutboundLedgerError):
            ledger.inspect_outbound_attempt(self.active, OP)

    def test_pending_after_reconnect_does_not_write_or_resend(self):
        self.pending_send()
        before = copy.deepcopy(self.cas.records)
        self.reconnect()
        with patch.object(policy, '_DRAFT_SEND_RELEASED', False):
            for _ in range(3):
                self.assertEqual(send.check_owned({}, AUTH.mailbox_id, OP)['outcome'], 'uncertain')
        self.assertEqual(self.cas.records, before)
        self.assertEqual(len(self.graph.sends), 1)

    def test_different_attempt_id_cannot_bypass_reconnected_pending_send(self):
        self.pending_send(); self.reconnect()
        before = copy.deepcopy(self.cas.records)
        with self.assertRaises(MicrosoftGraphError):
            self.send('n' * 43)
        self.assertEqual(self.cas.records, before)
        self.assertEqual(len(self.graph.sends), 1)

    def test_historical_prepared_attempt_cannot_be_confirmed_or_restarted_by_check(self):
        descriptor = {'example':'prepared'}
        index.reserve(AUTH, OP, 'draft-1', 'a' * 64)
        ledger.reserve_outbound_attempt(AUTH, OP, descriptor)
        ledger.advance_outbound_attempt(AUTH, OP, descriptor, from_phase='reserved', to_phase='draft_prepared', draft_id='draft-1')
        saved = copy.deepcopy(self.cas.records)
        self.reconnect()
        self.assertEqual(send.check_owned({}, AUTH.mailbox_id, OP)['outcome'], 'uncertain')
        with self.assertRaises(MicrosoftGraphError):
            index.confirm_sent_copy(self.active, OP)
        self.assertEqual(self.cas.records, saved)
        self.assertEqual(self.graph.calls, [])

    def test_wrong_current_owner_mailbox_email_or_account_cannot_read_old_proof(self):
        self.send()
        before = len(self.graph.calls)
        for changes in ({'owner_email':'other@example.com'}, {'mailbox_id':'other-box'},
                {'email':'other@example.com'}, {'account_id':'other-account'}):
            self.active = replace(AUTH, **changes)
            with self.subTest(changes=changes), self.assertRaises((MicrosoftGraphError, attachments.ledger.AttachmentLedgerError, owned.MicrosoftMailboxAccessError)):
                send.check_owned({}, AUTH.mailbox_id, OP)
        self.assertEqual(len(self.graph.calls), before)

    def test_swapped_fresh_credentials_cannot_probe_old_mailbox(self):
        self.pending_send(); self.reconnect()
        before = len(self.graph.calls)
        with patch.object(owned, '_owned_token', return_value=(TOKEN, AUTH)), self.assertRaises(owned.MicrosoftMailboxAccessError):
            send.check_owned({}, AUTH.mailbox_id, OP)
        self.assertEqual(len(self.graph.calls), before)

    def test_reconnect_or_logout_during_get_never_persists_proof(self):
        self.graph.visible = False; self.send(); self.reconnect()
        before = copy.deepcopy(self.cas.records)
        def changed(*args, **kwargs):
            self.active = replace(self.active, credential_generation='y' * 43)
            return True
        with patch.object(send.sent.graph, 'verify_microsoft_sent_copy', side_effect=changed), self.assertRaises(owned.MicrosoftMailboxAccessError):
            send.check_owned({}, AUTH.mailbox_id, OP)
        self.assertEqual(self.cas.records, before)

    def test_wrong_sent_identity_after_reconnect_never_confirms(self):
        self.graph.visible = False; self.send(); self.reconnect(); self.graph.visible = True
        self.graph.sent['draft-1']['id'] = 'other-message'
        with self.assertRaises(MicrosoftGraphError):
            send.check_owned({}, AUTH.mailbox_id, OP)
        self.assertEqual(send.inspect_owned({}, AUTH.mailbox_id)[0]['outcome'], 'uncertain')

    def test_tampered_historical_scope_or_draft_is_fail_closed(self):
        self.pending_send(); self.reconnect()
        key = ledger._key(AUTH, OP)
        original = self.cas.records[key]
        for field, value in [('scope','0' * 64), ('draftId','other-message')]:
            record = json.loads(original); record[field] = value
            self.cas.records[key] = json.dumps(record)
            with self.subTest(field=field), self.assertRaises((MicrosoftGraphError, ledger.OutboundLedgerError)):
                send.check_owned({}, AUTH.mailbox_id, OP)
        self.cas.records[key] = original

    def test_parallel_positive_checks_are_monotonic_without_send(self):
        self.graph.visible = False; self.send(); self.reconnect(); self.graph.visible = True
        barrier = threading.Barrier(2)
        write = ledger._write
        def simultaneous_commit(config, key, expected, value):
            if value.get('phase') == 'sent_copy_confirmed':
                barrier.wait(timeout=5)
            return write(config, key, expected, value)
        with patch.object(ledger, '_write', side_effect=simultaneous_commit), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: send.check_owned({}, AUTH.mailbox_id, OP), range(2)))
        self.assertTrue(all(x['outcome'] == 'confirmed_performed' for x in results))
        self.assertEqual(len(self.graph.sends), 1)

    def test_lost_positive_proof_write_ack_recovers_without_resending(self):
        self.graph.visible = False; self.send(); self.reconnect(); self.graph.visible = True
        self.cas.lost_ack = True
        self.assertEqual(send.check_owned({}, AUTH.mailbox_id, OP)['outcome'], 'confirmed_performed')
        self.assertEqual(len(self.graph.sends), 1)

    def test_http_status_and_check_survive_send_shutdown_and_reconnect(self):
        self.pending_send(); self.reconnect()
        with patch.object(policy, '_DRAFT_SEND_RELEASED', False):
            status = self.call_send({'action':'status','mailboxId':AUTH.mailbox_id})
            check = self.call_send({'action':'check','mailboxId':AUTH.mailbox_id,'attemptId':OP})
            self.assertEqual(status.status, 200); self.assertEqual(check.status, 200)
            self.assertEqual(_body(check)['receipt']['outcome'], 'uncertain')
            self.assertEqual(self.call_send().status, 403)
        self.assertEqual(len(self.graph.sends), 1)

    def test_production_status_boundary_remains_disabled(self):
        for action in ('status', 'check'):
            data = {'action':action,'mailboxId':AUTH.mailbox_id}
            if action == 'check': data['attemptId'] = OP
            with patch.dict(os.environ, {'VERCEL_ENV':'production'}):
                self.assertEqual(self.call_send(data).status, 403)
        self.assertEqual(self.graph.sends, [])


class LegacyReleaseSecurityTests(unittest.TestCase):
    def test_mail_send_token_and_environment_flags_cannot_enable_legacy_route_or_service(self):
        self.assertFalse(legacy_policy._LEGACY_SEND_RELEASED)
        for env in ('preview','development','production',''):
            with self.subTest(env=env), patch.dict(os.environ, {**PREVIEW, 'VERCEL_ENV':env,
                    'MICROSOFT_LEGACY_SEND_RELEASED':'true','MICROSOFT_DRAFT_SEND_RELEASED':'true',
                    'MICROSOFT_OAUTH_SCOPES':'Mail.ReadWrite Mail.Send'}, clear=True), patch.object(
                    owned, '_owned_token', return_value=(TOKEN, AUTH)) as token, patch.object(
                    owned, 'resolve_owned_microsoft_mailbox', return_value=AUTH) as owner, patch.object(
                    legacy, 'acquire_mailbox_mutation_lease') as lease, patch.object(
                    legacy.graph, 'prepare_microsoft_draft') as prepare, patch.object(
                    legacy.graph, 'submit_prepared_microsoft_draft') as submit, patch.object(
                    ledger, 'reserve_outbound_attempt') as reserve:
                self.assertEqual(legacy_http.microsoft_send_response(_on_preview(request())).status, 403)
                with self.assertRaises(MicrosoftGraphError) as error:
                    legacy.send_owned_microsoft_message({}, AUTH.mailbox_id, attempt_id=OP, compose=COMPOSE)
                self.assertEqual(error.exception.code, 'microsoft_legacy_send_disabled')
                for operation in (token, owner, lease, prepare, submit, reserve): operation.assert_not_called()

    def test_new_send_flag_does_not_enable_legacy_service(self):
        with patch.dict(os.environ, {'VERCEL_ENV':'preview'}, clear=True), patch.object(policy, '_DRAFT_SEND_RELEASED', True):
            self.assertTrue(legacy_policy.blocked())
            with self.assertRaises(MicrosoftGraphError):
                legacy.send_owned_microsoft_message({}, AUTH.mailbox_id, attempt_id=OP, compose=COMPOSE)

    def test_production_denial_survives_test_override_of_legacy_source_gate(self):
        with patch.dict(os.environ, {'VERCEL_ENV':'production'}, clear=True), patch.object(legacy_policy, '_LEGACY_SEND_RELEASED', True):
            with self.assertRaises(MicrosoftGraphError):
                legacy.send_owned_microsoft_message({}, AUTH.mailbox_id, attempt_id=OP, compose=COMPOSE)


if __name__ == '__main__':
    unittest.main()
