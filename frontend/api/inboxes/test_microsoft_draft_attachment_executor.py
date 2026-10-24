"""Fake Graph only; source gate patched in process and durable CAS isolated."""
import base64
import copy
from dataclasses import replace
import importlib
import json
import os
import threading
import unittest
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

from api.inboxes import microsoft_draft_attachment_executor as executor
from api.inboxes import microsoft_draft_attachment_graph as adapter
from api.inboxes import microsoft_draft_attachment_policy as policy
from api.inboxes import microsoft_draft_attachment_requests as requests
from api.inboxes import microsoft_draft_attachment_ledger as ledger
from api.inboxes import microsoft_draft_attachment_snapshot as snapshots
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes.test_microsoft_draft_attachment_foundation import LedgerFixture, Graph, row, message, AUTH, OP
from api.inboxes.test_microsoft_graph import TOKEN, FakeResponse
from api.inboxes.microsoft_graph import MicrosoftGraphError


def new_file(name='new.txt', content=b'new!', content_type='text/plain'):
    return {'name': name, 'content_type': content_type, 'content': content}


def response(data, status=200):
    r = FakeResponse(data)
    r.status = status
    return r


class MutableGraph(Graph):
    def __init__(self, rows=None, **kw):
        super().__init__(rows, **kw)
        self.rows = copy.deepcopy(self.rows)
        self.msg = copy.deepcopy(self.msg)
        self.writes, self.contents, self.version = [], {}, 1
        self.reject = None
        self.lose = False
        self.bad_ack = False
        self.read_failure = False
        self.bad_bytes = False
        self.metadata_size_offset = 0
        self.on_write = None
        self.on_read = None
        self.fail_after_writes = None
    def __call__(self, request, *, timeout):
        method = request.get_method()
        if method in ('POST', 'DELETE'):
            self.writes.append(request)
            if self.reject:
                return response({}, self.reject)
            if method == 'POST':
                data = json.loads(request.data)
                identifier = 'new-' + str(len(self.writes))
                self.contents[identifier] = base64.b64decode(data['contentBytes'])
                self.rows.append(row(identifier, name=data['name'], contentType=data['contentType'],
                                     size=len(self.contents[identifier])+self.metadata_size_offset))
                result = response({'id': identifier}, 201)
            else:
                identifier = unquote(urlsplit(request.full_url).path.rsplit('/', 1)[-1])
                self.rows = [r for r in self.rows if r['id'] != identifier]
                result = response(b'', 204)
            self.version += 1
            self.msg.update({'@odata.etag': 'W/"v'+str(self.version)+'"', 'changeKey': 'v'+str(self.version),
                             'hasAttachments': any(not r['isInline'] for r in self.rows)})
            if self.on_write:
                self.on_write(self)
            if self.lose or self.fail_after_writes == len(self.writes):
                raise TimeoutError('private lost response')
            return response(b'broken', 201) if self.bad_ack else result
        if self.writes and self.read_failure:
            raise TimeoutError('private lost read')
        if self.on_read:
            self.on_read(self)
        parts = urlsplit(request.full_url)
        if '/attachments/' in parts.path:
            self.calls.append(request)
            identifier = unquote(parts.path.rsplit('/', 1)[-1])
            content = b'evil' if self.bad_bytes else self.contents[identifier]
            return response({'id': identifier, '@odata.type': '#microsoft.graph.fileAttachment',
                             'contentBytes': base64.b64encode(content).decode()})
        return super().__call__(request, timeout=timeout)


class ExecutorFixture(LedgerFixture):
    def setUp(self):
        super().setUp()
        mocks = (
            patch.object(policy, '_DRAFT_ATTACHMENTS_RELEASED', True),
            patch.object(owned, 'resolve_owned_microsoft_mailbox', return_value=AUTH),
            patch.object(owned, '_owned_token', return_value=(TOKEN, AUTH)),
            patch.object(owned, '_verify_unchanged_owned_mailbox'),
            patch.object(owned, 'acquire_mailbox_mutation_lease', return_value={'status': 'acquired', 'token': 'l'*43}),
            patch.object(owned, 'release_mailbox_mutation_lease', return_value={'status': 'released'}),
            patch.object(executor, '_assert_lease'),
        )
        for p in mocks:
            p.start(); self.addCleanup(p.stop)
        self.graph = MutableGraph()
        self.before = snapshots.read_draft_attachment_snapshot(TOKEN, AUTH, 'draft-1', opener=self.graph)
    def execute(self, *, graph=None, request=OP, files=None, action='add', target='ordinary', expected=None):
        return executor.execute_owned_attachment_request({}, AUTH.mailbox_id, 'draft-1', request, action,
            expected_fingerprint=expected or self.before.fingerprint,
            **({'files': files or [new_file()]} if action == 'add' else {'attachment_id': target}),
            opener=graph or self.graph)


class ExecutorTests(ExecutorFixture):
    def test_successful_upload_uses_one_post_and_independent_complete_reads_and_hash(self):
        result = self.execute()
        self.assertEqual(result['outcome'], 'confirmed_performed')
        self.assertEqual(result['completed'], 1)
        self.assertEqual(len(self.graph.writes), 1)
        req = self.graph.writes[0]
        self.assertTrue(req.full_url.endswith('/mailFolders/drafts/messages/draft-1/attachments'))
        self.assertEqual(req.get_method(), 'POST')
        data = json.loads(req.data)
        self.assertEqual(set(data), {'@odata.type', 'name', 'contentType', 'isInline', 'contentBytes'})
        self.assertEqual(base64.b64decode(data['contentBytes']), b'new!')
        self.assertNotIn('If-match', req.headers)
        self.assertGreaterEqual(self.graph.inventory_reads, 8)
        self.assertTrue(any('/attachments/new-1' in r.full_url for r in self.graph.calls))
        self.assertFalse(result['blocked'])
    def test_successful_delete_targets_exact_id_preserves_all_other_content(self):
        rows = [row('ordinary'), row('same-name'), row('inline', isInline=True, contentId='logo')]
        msg = message(body={'contentType': 'html', 'content': '<p>Exact &amp; HTML</p><img src="cid:logo">'})
        self.graph = MutableGraph(rows, msg=msg)
        self.before = snapshots.read_draft_attachment_snapshot(TOKEN, AUTH, 'draft-1', opener=self.graph)
        result = self.execute(action='remove')
        self.assertEqual(result['outcome'], 'confirmed_performed')
        self.assertEqual(len(self.graph.writes), 1)
        req = self.graph.writes[0]
        self.assertEqual(req.get_method(), 'DELETE')
        self.assertTrue(req.full_url.endswith('/attachments/ordinary'))
        self.assertIsNone(req.data)
        self.assertEqual(self.graph.msg['body'], msg['body'])
        self.assertEqual(self.graph.rows, rows[1:])
        self.assertEqual(self.graph.msg['toRecipients'], msg['toRecipients'])
    def test_duplicate_names_are_separate_exact_ids(self):
        result = self.execute(files=[new_file('song.mp3'), new_file('song.mp3', b'different')])
        self.assertEqual(result['completed'], 2)
        self.assertEqual(result['outcome'], 'confirmed_performed')
        self.assertEqual([x['name'] for x in self.graph.rows], ['song.mp3']*3)
        self.assertEqual(len({x['id'] for x in self.graph.rows}), 3)
    def test_identical_request_never_uploads_again_even_after_success(self):
        result = self.execute()
        reads, writes = len(self.graph.calls), len(self.graph.writes)
        self.assertEqual(self.execute(), result)
        self.assertEqual((len(self.graph.calls), len(self.graph.writes)), (reads, writes))
    def test_changed_batch_manifest_same_request_id_is_rejected(self):
        self.execute(files=[new_file(), new_file('second.txt')])
        with self.assertRaises(ledger.AttachmentLedgerError):
            self.execute(files=[new_file(), new_file('changed.txt')])
        self.assertEqual(len(self.graph.writes), 2)
    def test_lost_upload_or_delete_answer_is_sticky_across_reload_and_new_request(self):
        for action in ('add', 'remove'):
            with self.subTest(action=action):
                self.cas.records.clear()
                self.graph = MutableGraph()
                self.graph.lose = True
                self.before = snapshots.read_draft_attachment_snapshot(TOKEN, AUTH, 'draft-1', opener=self.graph)
                result = self.execute(action=action)
                self.assertEqual(result['outcome'], 'uncertain')
                importlib.reload(requests)
                self.assertEqual(self.execute(action=action), result)
                latest = snapshots.read_draft_attachment_snapshot(TOKEN, AUTH, 'draft-1', opener=self.graph)
                with self.assertRaises(ledger.AttachmentLedgerError):
                    self.execute(action=action, request='p'*43, expected=latest.fingerprint)
                self.assertEqual(len(self.graph.writes), 1)
    def test_missing_or_malformed_ack_or_failed_read_never_retries(self):
        for attr in ('bad_ack', 'read_failure', 'bad_bytes'):
            with self.subTest(attr=attr):
                self.cas.records.clear()
                self.graph = MutableGraph(); setattr(self.graph, attr, True)
                result = self.execute()
                self.assertEqual(result['outcome'], 'uncertain')
                self.assertEqual(len(self.graph.writes), 1)
                self.assertEqual(self.execute(), result)
    def test_definite_graph_rejection_confirmed_not_performed_with_unchanged_readback(self):
        for status in (400, 401, 403, 404, 409, 412, 413, 415, 429):
            with self.subTest(status=status):
                self.cas.records.clear(); self.graph = MutableGraph(); self.graph.reject = status
                result = self.execute()
                self.assertEqual(result['outcome'], 'confirmed_not_performed')
                self.assertEqual(self.execute(), result)
                self.assertEqual(len(self.graph.writes), 1)
    def test_5xx_or_unexpected_success_status_stays_uncertain(self):
        for status in (200, 202, 408, 500, 503):
            with self.subTest(status=status):
                self.cas.records.clear(); self.graph = MutableGraph(); self.graph.reject = status
                self.assertEqual(self.execute()['outcome'], 'uncertain')
                self.assertEqual(len(self.graph.writes), 1)
    def test_outlook_drift_before_start_cancels_without_graph_write(self):
        def drift(graph):
            if graph.message_reads == 3:
                graph.msg['changeKey'] = 'external'
                graph.msg['@odata.etag'] = 'W/"external"'
        self.graph.message_reads = 0
        self.graph.on_read = drift
        result = self.execute()
        self.assertEqual(result['outcome'], 'confirmed_not_performed')
        self.assertEqual(self.graph.writes, [])
    def test_outlook_edit_during_write_preserves_it_and_leaves_uncertain_without_rollback(self):
        def external(graph):
            graph.msg['body'] = {'contentType': 'html', 'content': '<p>Outlook edited this</p>'}
            graph.msg['ccRecipients'] = [{'emailAddress': {'address': 'external@example.com'}}]
        self.graph.on_write = external
        self.assertEqual(self.execute()['outcome'], 'uncertain')
        self.assertEqual(self.graph.msg['body']['content'], '<p>Outlook edited this</p>')
        self.assertEqual(len(self.graph.writes), 1)
    def test_outlook_changes_other_attachment_stays_uncertain(self):
        self.graph.on_write = lambda g: g.rows[0].update(name='external.mp3')
        self.assertEqual(self.execute()['outcome'], 'uncertain')
        self.assertEqual(len(self.graph.writes), 1)
    def test_wrong_owner_and_wrong_token_authority_never_write(self):
        owned.resolve_owned_microsoft_mailbox.side_effect = owned.MicrosoftMailboxAccessError('microsoft_mailbox_not_found')
        with self.assertRaises(owned.MicrosoftMailboxAccessError):
            self.execute()
        owned.resolve_owned_microsoft_mailbox.side_effect = None
        owned._owned_token.return_value = (TOKEN, replace(AUTH, account_id='wrong'))
        with self.assertRaises(ledger.AttachmentLedgerError):
            self.execute()
        self.assertEqual(self.graph.writes, [])
    def test_expired_credentials_before_execution_never_write(self):
        owned._owned_token.side_effect = owned.MicrosoftMailboxAccessError('microsoft_reconnect_required')
        with self.assertRaises(owned.MicrosoftMailboxAccessError):
            self.execute()
        self.assertEqual(self.graph.writes, [])
    def test_generation_changes_during_preparation_never_write(self):
        owned._owned_token.side_effect = [(TOKEN, AUTH), (TOKEN, AUTH), (TOKEN, replace(AUTH, credential_generation='h'*43))]
        self.assertEqual(self.execute()['outcome'], 'confirmed_not_performed')
        self.assertEqual(self.graph.writes, [])
    def test_generation_changes_after_start_never_write_or_auto_recover(self):
        def verify(*args):
            child = ledger.inspect_attachment_operation(AUTH, requests.child_id(OP, 0)) if any(
                k.startswith(ledger.PREFIX) for k in self.cas.records) else None
            if child and child['phase'] == 'started':
                raise owned.MicrosoftMailboxAccessError('microsoft_reconnect_required')
        owned._verify_unchanged_owned_mailbox.side_effect = verify
        with self.assertRaises(owned.MicrosoftMailboxAccessError):
            self.execute()
        self.assertEqual(requests.lookup(AUTH, OP)['outcome'], 'uncertain')
        self.assertEqual(self.graph.writes, [])
    def test_invalid_attachment_ids_and_inline_cid_targets_refused(self):
        for target in ('missing', '../bad', '?query', '', True):
            with self.subTest(target=target), self.assertRaises((ledger.AttachmentLedgerError, MicrosoftGraphError)):
                self.execute(action='remove', target=target)
        for target in (row(isInline=True), row(contentId='logo'), row(**{'@odata.type': '#microsoft.graph.itemAttachment'})):
            self.graph = MutableGraph([target])
            self.before = snapshots.read_draft_attachment_snapshot(TOKEN, AUTH, 'draft-1', opener=self.graph)
            with self.assertRaises(ledger.AttachmentLedgerError):
                self.execute(action='remove')
            self.assertEqual(self.graph.writes, [])
    def test_incomplete_inventory_wrong_folder_or_sent_message_never_write(self):
        for graph in (MutableGraph([row(str(i)) for i in range(101)]), MutableGraph(broken=True),
                      MutableGraph(msg=message(isDraft=False)), MutableGraph(msg=message(parentFolderId='other'))):
            with self.subTest(graph=graph), self.assertRaises(MicrosoftGraphError):
                self.execute(graph=graph)
            self.assertEqual(graph.writes, [])
    def test_large_existing_files_and_html_cids_survive_upload(self):
        rows = [row(size=99*1024*1024), row('inline', isInline=True, contentId='logo', size=4*1024*1024)]
        msg = message(body={'contentType': 'html', 'content': '<img src="cid:logo"><p>Exact</p>'})
        self.graph = MutableGraph(rows, msg=msg)
        self.before = snapshots.read_draft_attachment_snapshot(TOKEN, AUTH, 'draft-1', opener=self.graph)
        self.assertEqual(self.execute()['outcome'], 'confirmed_performed')
        self.assertEqual(self.graph.rows[:2], rows)
        self.assertEqual(self.graph.msg['body'], msg['body'])
    def test_new_file_size_metadata_is_not_integrity_proof_bytes_hash_is(self):
        self.graph.metadata_size_offset = 31
        self.assertEqual(self.execute()['outcome'], 'confirmed_performed')
        self.assertEqual(len(self.graph.writes), 1)
    def test_partial_batch_stops_on_first_uncertain_file_and_never_resumes(self):
        self.graph.fail_after_writes = 2
        files = [new_file(str(i)+'.txt') for i in range(3)]
        result = self.execute(files=files)
        self.assertEqual(result['outcome'], 'uncertain')
        self.assertEqual(result['completed'], 1)
        self.assertEqual(len(self.graph.writes), 2)
        self.assertEqual(self.execute(files=files), result)
        self.assertEqual(len(self.graph.writes), 2)
    def test_ten_new_files_and_exact_eight_mib_allowed(self):
        files = [new_file(str(i)+'.bin', b'x'*(2*1024*1024)) for i in range(4)] + [new_file(str(i)+'.txt', b'') for i in range(4,10)]
        self.assertEqual(self.execute(files=files)['completed'], 10)
        self.assertEqual(len(self.graph.writes), 10)
    def test_server_rejects_over_file_count_size_and_total_before_any_graph_write(self):
        cases = [[new_file(content=b'x'*(2*1024*1024+1))], [new_file()]*11,
                 [new_file(content=b'x'*(2*1024*1024))]*4+[new_file(content=b'x')]]
        for files in cases:
            with self.subTest(count=len(files)), self.assertRaises(ledger.AttachmentLedgerError):
                self.execute(files=files)
        self.assertEqual(self.graph.writes, [])
    def test_full_child_ledger_or_record_budget_blocks_before_first_post(self):
        for name, limit in (('MAX_OPERATIONS', 0), ('MAX_RECORD_BYTES', 100)):
            with self.subTest(name=name), patch.object(ledger, name, limit), self.assertRaises(ledger.AttachmentLedgerError):
                self.execute()
        self.assertEqual(self.graph.writes, [])
    def test_full_request_ledger_blocks_before_first_post(self):
        with patch.object(requests, 'MAX_REQUESTS', 0), self.assertRaises(ledger.AttachmentLedgerError):
            self.execute()
        self.assertEqual(self.graph.writes, [])
    def test_lost_request_or_child_ledger_ack_never_grants_graph_execution(self):
        self.cas.lost_ack = True
        with self.assertRaises(ledger.AttachmentLedgerError):
            self.execute()
        self.cas.lost_ack = False
        self.assertEqual(self.execute()['outcome'], 'uncertain')
        self.assertEqual(self.graph.writes, [])
    def test_lease_loss_before_or_after_start_never_writes(self):
        for after in (False, True):
            with self.subTest(after=after):
                self.cas.records.clear()
                executor._assert_lease.side_effect = [None, ledger.AttachmentLedgerError('lease_unavailable')] if after else ledger.AttachmentLedgerError('lease_unavailable')
                result = self.execute()
                self.assertEqual(result['outcome'], 'uncertain' if after else 'confirmed_not_performed')
                self.assertEqual(self.graph.writes, [])
    def test_release_failure_after_success_cannot_authorise_retry(self):
        owned.release_mailbox_mutation_lease.return_value = {'status': 'unavailable'}
        with self.assertRaises(ledger.AttachmentLedgerError):
            self.execute()
        self.assertEqual(self.execute()['outcome'], 'confirmed_performed')
        self.assertEqual(len(self.graph.writes), 1)
    def test_two_sessions_same_request_only_one_can_post(self):
        # Force both sessions past the initial read of the request barrier.
        original = requests.lookup
        barrier = threading.Barrier(2)
        def lookup(*args, **kwargs):
            result = original(*args, **kwargs)
            barrier.wait(timeout=5)
            return result
        def execute(_):
            try:
                return self.execute()['outcome']
            except (ledger.AttachmentLedgerError, MicrosoftGraphError):
                return 'blocked'
        with patch.object(requests, 'lookup', side_effect=lookup), ThreadPoolExecutor(2) as pool:
            results = list(pool.map(execute, (1,2)))
        self.assertEqual(len(self.graph.writes), 1)
        self.assertIn('confirmed_performed', results)
    def test_new_requests_cannot_bypass_uncertain_after_reconnect(self):
        self.graph.lose = True
        self.execute()
        reconnected = replace(AUTH, credential_generation='h'*43)
        owned.resolve_owned_microsoft_mailbox.return_value = reconnected
        owned._owned_token.return_value = (TOKEN, reconnected)
        latest = snapshots.read_draft_attachment_snapshot(TOKEN, reconnected, 'draft-1', opener=self.graph)
        with self.assertRaises(ledger.AttachmentLedgerError):
            self.execute(request='p'*43, expected=latest.fingerprint)
        result = executor.inspect_owned_attachment_request({}, AUTH.mailbox_id, 'draft-1', OP)
        self.assertEqual(result['outcome'], 'uncertain')
        self.assertEqual(len(self.graph.writes), 1)
    def test_durable_ledgers_do_not_store_bytes_names_body_or_credentials(self):
        self.execute()
        raw = '\n'.join(self.cas.records.values())
        for private in ('new.txt', 'Original', TOKEN, AUTH.owner_email, AUTH.email, 'bmV3IQ=='):
            self.assertNotIn(private, raw)
    def test_source_gate_blocks_executor_adapter_even_with_environment_flags(self):
        with patch.object(policy, '_DRAFT_ATTACHMENTS_RELEASED', False), patch.dict(os.environ, {'MICROSOFT_DRAFT_ATTACHMENTS_RELEASED': 'true'}):
            with self.assertRaises(ledger.AttachmentLedgerError):
                self.execute()
            with self.assertRaises(ledger.AttachmentLedgerError):
                adapter.mutate_attachment(TOKEN, self.before, 'remove', attachment_id='ordinary', before_write=lambda: None, opener=self.graph)
        with patch.dict(os.environ, {'VERCEL_ENV': 'production'}), self.assertRaises(ledger.AttachmentLedgerError):
            self.execute()
        self.assertEqual(self.graph.writes, [])

    def test_lost_child_start_storage_ack_never_reaches_graph_or_retries(self):
        original = self.cas.write
        def lose_start(config, key, expected, value):
            result = original(config, key, expected, value)
            if key.startswith(ledger.PREFIX) and any(x['phase'] == 'started' for x in value['operations'].values()):
                return None, {'code': 'unavailable'}
            return result
        with patch.object(ledger.store, '_write_google_durable_record_if_unchanged', side_effect=lose_start):
            result = self.execute()
        self.assertEqual(result['outcome'], 'uncertain')
        self.assertEqual(self.execute(), result)
        self.assertEqual(self.graph.writes, [])

    def test_distinct_requests_in_concurrent_sessions_cannot_bypass_same_draft_barrier(self):
        original = requests.reserve
        barrier = threading.Barrier(2)
        def reserve(*args):
            barrier.wait(timeout=5)
            return original(*args)
        def execute(request):
            try:
                return self.execute(request=request)['outcome']
            except (ledger.AttachmentLedgerError, MicrosoftGraphError):
                return 'blocked'
        with patch.object(requests, 'reserve', side_effect=reserve), ThreadPoolExecutor(2) as pool:
            results = list(pool.map(execute, (OP, 'p'*43)))
        self.assertEqual(len(self.graph.writes), 1)
        # If the winner finishes before the other session reserves, the stale
        # snapshot is definitively rejected before POST instead of remaining held.
        self.assertEqual(sum(x in {'blocked', 'confirmed_not_performed'} for x in results), 1)
        self.assertEqual(results.count('confirmed_performed'), 1)

    def test_request_record_byte_limit_and_corruption_fail_closed(self):
        with patch.object(requests, 'MAX_BYTES', 100), self.assertRaises(ledger.AttachmentLedgerError):
            self.execute()
        self.assertEqual(self.graph.writes, [])
        self.graph.lose = True
        self.execute()
        key = next(key for key in self.cas.records if key.startswith(requests.PREFIX))
        self.cas.records[key] = '{}'
        with self.assertRaises(ledger.AttachmentLedgerError):
            self.execute()
        self.assertEqual(len(self.graph.writes), 1)

    def test_missing_durable_store_never_uses_local_fallback(self):
        with patch.object(ledger.store, '_resolve_durable_store_config', return_value=None), self.assertRaises(ledger.AttachmentLedgerError):
            self.execute()
        self.assertEqual(self.graph.writes, [])


class RealLeaseGuardTests(unittest.TestCase):
    def test_lease_must_match_exact_authority_and_leave_time_for_provider_call(self):
        with patch.object(executor.config_store, 'resolve_user_config_store', return_value=({'isolated': True}, None)), patch.object(
                executor.config_store, '_read_mailbox_mutation_lease', return_value=('present', 'l'*43)) as read, patch.object(executor.time, 'monotonic', return_value=100):
            executor._assert_lease(AUTH, 'l'*43, 200)
            read.assert_called_once_with({'isolated': True}, AUTH.owner_email, AUTH.mailbox_id)
            for status in (('missing', None), ('present', 'other'), ('unavailable', None)):
                read.return_value = status
                with self.subTest(status=status), self.assertRaises(ledger.AttachmentLedgerError):
                    executor._assert_lease(AUTH, 'l'*43, 200)
            with self.assertRaises(ledger.AttachmentLedgerError):
                executor._assert_lease(AUTH, 'l'*43, 140)
    def test_lease_store_unavailable_fails_closed(self):
        with patch.object(executor.config_store, 'resolve_user_config_store', return_value=(None, {'code': 'unavailable'})), self.assertRaises(ledger.AttachmentLedgerError):
            executor._assert_lease(AUTH, 'l'*43, time.monotonic()+300)
