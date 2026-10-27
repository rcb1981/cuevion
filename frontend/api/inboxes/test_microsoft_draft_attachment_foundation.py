"""Offline owner/snapshot/atomic-ledger regressions; all storage is isolated."""
import copy
from dataclasses import replace
import hashlib
import importlib
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

from api.inboxes import microsoft_draft_attachment_snapshot as snap
from api.inboxes import microsoft_draft_attachment_ledger as ledger
from api.inboxes import microsoft_draft_attachment_service as service
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes.microsoft_token_authority import MicrosoftMailboxAuthority
from api.inboxes.test_microsoft_graph import FakeResponse, TOKEN

AUTH = MicrosoftMailboxAuthority('owner@example.com', 'microsoft-box', 'music@example.com', 'account-1', 'g'*43)
OP = 'o'*43

def row(identifier='ordinary', **kw):
    return {'id': identifier, '@odata.type': '#microsoft.graph.fileAttachment', 'name': 'song.mp3',
            'size': 4, 'contentType': 'audio/mpeg', 'isInline': False, **kw}

def message(**kw):
    return {'id': 'draft-1', 'parentFolderId': 'drafts-folder', 'isDraft': True, 'hasAttachments': True,
            '@odata.etag': 'W/"version-1"', 'changeKey': 'version-1', 'subject': 'Keep this',
            'toRecipients': [{'emailAddress': {'address': 'receiver@example.com'}}],
            'from': {'emailAddress': {'address': AUTH.email}}, 'sender': {'emailAddress': {'address': AUTH.email}}, 'replyTo': [],
            'ccRecipients': [], 'bccRecipients': [], 'body': {'contentType': 'html', 'content': '<p>Original</p>'}, **kw}

class Graph:
    def __init__(self, rows=None, *, msg=None, alter=None, tail=False, broken=False):
        self.rows = [row()] if rows is None else rows
        self.msg = msg or message(hasAttachments=any(not r.get('isInline', False) for r in self.rows))
        self.alter, self.tail, self.broken = alter, tail, broken
        self.calls, self.message_reads, self.inventory_reads = [], 0, 0
    def __call__(self, request, *, timeout):
        self.calls.append(request)
        if request.get_method() != 'GET':
            raise AssertionError('Graph mutations forbidden')
        self_test = request.get_header('Prefer')
        if self_test != 'IdType="ImmutableId"':
            raise AssertionError('Immutable IDs required')
        parts = urlsplit(request.full_url)
        if parts.path.endswith('/mailFolders/drafts'):
            result = {'id': 'drafts-folder'}
        elif parts.path.endswith('/attachments'):
            skip = int(parse_qs(parts.query).get('$skip', ['0'])[0])
            if skip == 0:
                self.inventory_reads += 1
            rows = copy.deepcopy(self.rows)
            if self.alter == 'inventory' and self.inventory_reads == 2:
                rows[0]['name'] = 'external-change.mp3'
            result = {} if self.broken else {'value': rows[skip:skip+25]}
            if skip+25 < len(rows) or self.tail:
                result['@odata.nextLink'] = 'https://graph.microsoft.com' + parts.path + '?$skip=' + str(skip+25)
        else:
            self.message_reads += 1
            result = copy.deepcopy(self.msg)
            if self.alter and self.alter != 'inventory' and self.message_reads >= 2:
                result.update(self.alter)
        response = FakeResponse(result)
        response.status = 200
        return response


def snapshot(rows=None, **kw):
    graph = Graph(rows, **kw)
    value = snap.read_draft_attachment_snapshot(TOKEN, AUTH, 'draft-1', opener=graph)
    return value

class SnapshotTests(unittest.TestCase):
    def test_complete_identity_version_and_inventory(self):
        graph = Graph()
        s = snap.read_draft_attachment_snapshot(TOKEN, AUTH, 'draft-1', opener=graph)
        self.assertEqual(s.authority, AUTH)
        self.assertEqual(s.provider_message_id, 'draft-1')
        self.assertEqual(s.drafts_folder_id, 'drafts-folder')
        self.assertTrue(s.is_draft and s.inventory_complete)
        self.assertEqual(s.etag, 'W/"version-1"')
        self.assertEqual(len(graph.calls), 6)
        self.assertEqual(len(s.attachments), 1)
        self.assertEqual(len(s.fingerprint), 64)

    def test_inline_cid_and_false_hasattachments_are_preserved(self):
        html = '<img src="cid:logo%40image"><p>Unchanged &amp; safe</p>'
        s = snapshot([row(), row('inline', isInline=True, contentId='<logo@image>', size=8388608)],
                     msg=message(hasAttachments=False, body={'contentType': 'html', 'content': html}))
        self.assertEqual(s.cid_links, (('logo@image', ('inline',)),))
        self.assertEqual(s.body_hash, snap.digest({'contentType': 'html', 'content': html}))
        self.assertEqual(s.attachments[0].size, 8388608)

    def test_large_existing_file_is_not_limited_by_future_upload_cap(self):
        s = snapshot([row(size=100*1024*1024)])
        self.assertEqual(s.attachments[0].size, 100*1024*1024)

    def test_26_and_100_attachments_complete_but_101_never_partial(self):
        for count in (26, 100):
            self.assertEqual(len(snapshot([row(str(i)) for i in range(count)]).attachments), count)
        with self.assertRaises(MicrosoftGraphError):
            snapshot([row(str(i)) for i in range(101)])

    def test_truncated_and_duplicate_inventory_refused(self):
        for graph in (Graph(broken=True), Graph([row(), row()]), Graph(tail=True)):
            with self.subTest(graph=graph), self.assertRaises(MicrosoftGraphError):
                snap.read_draft_attachment_snapshot(TOKEN, AUTH, 'draft-1', opener=graph)

    def test_missing_invalid_metadata_or_version_refused(self):
        for update in ({'isDraft': False}, {'parentFolderId': 'other'}, {'id': 'other'},
                       {'@odata.etag': None}, {'@odata.etag': 'bad'}, {'changeKey': None},
                       {'body': {}}, {'bccRecipients': None}, {'hasAttachments': None}):
            with self.subTest(update=update), self.assertRaises(MicrosoftGraphError):
                snapshot(msg=message(**update))
        for update in ({'size': True}, {'id': '../bad'}, {'isInline': 'false'}, {'contentId': '<bad cid>'}):
            with self.subTest(update=update), self.assertRaises(MicrosoftGraphError):
                snapshot([row(**update)])

    def test_external_message_and_inventory_changes_refused(self):
        for alter in ({'@odata.etag': 'W/"version-2"'}, {'changeKey': 'v2'},
                      {'body': {'contentType': 'html', 'content': 'External'}},
                      {'parentFolderId': 'other'}, {'isDraft': False}, 'inventory'):
            with self.subTest(alter=alter), self.assertRaises(MicrosoftGraphError):
                snapshot(alter=alter)

    def test_missing_concrete_type_never_infers_mutation_authority_from_cid(self):
        r = row(contentId='logo@image')
        del r['@odata.type']
        s = snapshot([r])
        self.assertEqual(s.attachments[0].kind, 'other')
        self.assertEqual(s.attachments[0].content_id, 'logo@image')

    def test_claimed_attachments_with_empty_inventory_is_not_complete(self):
        with self.assertRaises(MicrosoftGraphError):
            snapshot([], msg=message(hasAttachments=True))

    def test_owner_rechecked_after_all_graph_reads(self):
        with patch.object(owned, '_owned_token', return_value=(TOKEN, AUTH)), patch.object(
            owned, '_verify_unchanged_owned_mailbox', side_effect=owned.MicrosoftMailboxAccessError('microsoft_reconnect_required')):
            with self.assertRaises(owned.MicrosoftMailboxAccessError):
                snap.read_owned_snapshot({}, AUTH.mailbox_id, 'draft-1', opener=Graph())

    def test_wrong_owner_never_reads_graph(self):
        graph = Graph()
        with patch.object(owned, '_owned_token', side_effect=owned.MicrosoftMailboxAccessError('microsoft_mailbox_not_found')):
            with self.assertRaises(owned.MicrosoftMailboxAccessError):
                snap.read_owned_snapshot({}, 'wrong-box', 'draft-1', opener=graph)
        self.assertEqual(graph.calls, [])

class CAS:
    def __init__(self):
        self.records = {}
        self.lock = threading.Lock()
        self.lost_ack = False
        self.bad_read = False
        self.bad_write = False
        self.race = None
        self.local = threading.local()
    def read(self, config, key):
        if self.bad_read:
            return None, None, {'code': 'unavailable'}
        with self.lock:
            raw = self.records.get(key)
            result = (json.loads(raw), raw, None) if raw is not None else (None, None, None)
        if self.race and not getattr(self.local, 'waited', False):
            self.local.waited = True
            self.race.wait(timeout=5)
        return result
    def write(self, config, key, expected, value):
        with self.lock:
            if self.bad_write:
                return None, {'code': 'unavailable'}
            if self.records.get(key) != expected:
                return None, {'code': 'gmail_token_write_conflict'}
            self.records[key] = json.dumps(value, sort_keys=True, separators=(',', ':'))
            if self.lost_ack:
                return None, {'code': 'unavailable'}
            return copy.deepcopy(value), None

class LedgerFixture(unittest.TestCase):
    def setUp(self):
        self.cas = CAS()
        self.s = snapshot()
        self.patches = [patch.object(ledger.store, '_resolve_durable_store_config', return_value={'isolated': True}),
                        patch.object(ledger.store, '_read_durable_record_snapshot', side_effect=self.cas.read),
                        patch.object(ledger.store, '_write_google_durable_record_if_unchanged', side_effect=self.cas.write)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
    def reserve(self, s=None, op=OP, action='remove', **kw):
        s = s or self.s
        args = {'attachment_id': 'ordinary'} if action == 'remove' else {'name': 'new.txt', 'content_type': 'text/plain', 'content': b'new!'}
        args.update(kw)
        return ledger.reserve_attachment_operation(s, op, action, expected_fingerprint=s.fingerprint, **args)
    def started(self, **kw):
        r = self.reserve(**kw)
        ledger.start_attachment_operation(kw.get('s') or self.s, OP, r['claim'])
        return r
    def post(self, rows, **kw):
        return snapshot(rows, msg=message(**{'hasAttachments': any(not r.get('isInline', False) for r in rows), '@odata.etag': 'W/"version-2"', 'changeKey': 'version-2', **kw}))

class LedgerTests(LedgerFixture):
    def test_preparation_has_one_claim_and_replays_never_return_it(self):
        r = self.reserve()
        self.assertTrue(r['new'] and r['blocked'])
        again = self.reserve()
        self.assertFalse(again['new'])
        self.assertNotIn('claim', again)
        self.assertEqual(again['phase'], 'prepared')
    def test_same_operation_id_is_bound_to_content_action_draft_and_authority(self):
        self.reserve(action='add')
        for changes in ({'content': b'new?'}, {'name': 'other.txt'}, {'content_type': 'application/pdf'}):
            with self.subTest(changes=changes), self.assertRaises(ledger.AttachmentLedgerError):
                self.reserve(action='add', **changes)
        with self.assertRaises(ledger.AttachmentLedgerError):
            self.reserve()
        with self.assertRaises(ledger.AttachmentLedgerError):
            self.reserve(s=replace(self.s, provider_message_id='draft-2'))
        with self.assertRaises(ledger.AttachmentLedgerError):
            self.reserve(s=replace(self.s, authority=replace(AUTH, credential_generation='h'*43)), action='add')
    def test_operation_ids_are_unique_strict_and_non_guessable_shape(self):
        ids = {ledger.new_operation_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)
        self.assertTrue(all(ledger.ID.fullmatch(x) for x in ids))
        for bad in ('', 'x'*42, '../bad', True, None):
            with self.subTest(bad=bad), self.assertRaises(ledger.AttachmentLedgerError):
                self.reserve(op=bad)
    def test_stale_snapshot_and_forged_partial_inventory_refused(self):
        with self.assertRaises(ledger.AttachmentLedgerError):
            ledger.reserve_attachment_operation(self.s, OP, 'remove', expected_fingerprint='0'*64, attachment_id='ordinary')
        for s in (replace(self.s, inventory_complete=False), replace(self.s, inventory_hash='0'*64),
                  replace(self.s, fingerprint=None), replace(self.s, attachments=None),
                  replace(self.s, cid_links=(None,)), replace(self.s, protected_content_hash=None)):
            with self.assertRaises(ledger.AttachmentLedgerError):
                self.reserve(s=s)
    def test_prepare_blocks_other_session_operation_on_same_draft(self):
        self.reserve()
        with self.assertRaises(ledger.AttachmentLedgerError):
            self.reserve(op='p'*43)
    def test_start_is_once_only_and_durably_uncertain(self):
        r = self.started()
        self.assertEqual(ledger.inspect_attachment_operation(AUTH, OP)['outcome'], 'uncertain')
        with self.assertRaises(ledger.AttachmentLedgerError):
            ledger.start_attachment_operation(self.s, OP, r['claim'])
        with self.assertRaises(ledger.AttachmentLedgerError):
            ledger.cancel_prepared_operation(AUTH, OP, r['claim'])
    def test_external_version_inventory_and_body_changes_block_start(self):
        r = self.reserve()
        for s in (self.post([row()]), self.post([row('other')]), self.post([row()], body={'contentType': 'html', 'content': 'external'})):
            with self.subTest(snapshot=s.fingerprint), self.assertRaises(ledger.AttachmentLedgerError):
                ledger.start_attachment_operation(s, OP, r['claim'])
        self.assertEqual(ledger.inspect_attachment_operation(AUTH, OP)['phase'], 'prepared')
    def test_wrong_claim_or_mailbox_cannot_start(self):
        self.reserve()
        for s, claim in ((self.s, 'x'*43), (replace(self.s, authority=replace(AUTH, mailbox_id='other')), 'x'*43)):
            with self.assertRaises(ledger.AttachmentLedgerError):
                ledger.start_attachment_operation(s, OP, claim)
    def test_lost_provider_response_survives_reload_and_reconnect(self):
        self.started()
        importlib.reload(ledger)
        self.assertEqual(ledger.inspect_attachment_operation(AUTH, OP)['outcome'], 'uncertain')
        reconnect = replace(AUTH, credential_generation='h'*43)
        self.assertEqual(ledger.inspect_attachment_operation(reconnect, OP)['outcome'], 'uncertain')
        with self.assertRaises(ledger.AttachmentLedgerError):
            self.reserve(s=replace(self.s, authority=reconnect), op='p'*43)
    def test_rebound_mailbox_account_cannot_escape_old_ledger(self):
        self.started()
        with self.assertRaises(ledger.AttachmentLedgerError):
            self.reserve(s=replace(self.s, authority=replace(AUTH, account_id='other-account')), op='p'*43)
    def test_unchanged_inventory_never_proves_not_performed(self):
        self.started()
        r = ledger.reconcile_attachment_operation(self.s, OP)
        self.assertEqual(r['outcome'], 'uncertain')
        with self.assertRaises(ledger.AttachmentLedgerError):
            self.reserve(op='p'*43)
    def test_explicit_pre_execution_cancel_confirms_not_performed(self):
        r = self.reserve()
        self.assertEqual(ledger.cancel_prepared_operation(AUTH, OP, r['claim'])['outcome'], 'confirmed_not_performed')
        self.assertNotIn('claim', self.reserve())
        self.assertTrue(self.reserve(op='p'*43)['new'])
    def test_definite_write_rejection_requires_claim_unchanged_snapshot_and_finite_status(self):
        r = self.started()
        for code in (None, True, 200, 202, 408, 500, 503):
            with self.subTest(code=code), self.assertRaises(ledger.AttachmentLedgerError):
                ledger.confirm_provider_rejection(self.s, OP, r['claim'], http_status=code)
        with self.assertRaises(ledger.AttachmentLedgerError):
            ledger.confirm_provider_rejection(self.post([]), OP, r['claim'], http_status=403)
        self.assertEqual(ledger.confirm_provider_rejection(self.s, OP, r['claim'], http_status=403)['outcome'], 'confirmed_not_performed')
    def test_remove_confirmed_only_with_exact_id_delta_and_protected_content(self):
        s = snapshot([row(), row('retained', size=99*1024*1024), row('inline', isInline=True, contentId='logo')])
        self.started(s=s)
        for rows, kw in (([row('retained', size=1), row('inline', isInline=True, contentId='logo')], {}),
                         ([], {}), ([row('retained', size=99*1024*1024), row('inline', isInline=True, contentId='logo')],
                                   {'body': {'contentType': 'html', 'content': 'overwritten'}})):
            self.assertEqual(ledger.reconcile_attachment_operation(self.post(rows, **kw), OP)['outcome'], 'uncertain')
        post = self.post([row('retained', size=99*1024*1024), row('inline', isInline=True, contentId='logo')])
        self.assertEqual(ledger.reconcile_attachment_operation(post, OP)['outcome'], 'confirmed_performed')
    def test_remove_not_confirmed_without_version_change(self):
        self.started()
        self.assertEqual(ledger.reconcile_attachment_operation(snapshot([]), OP)['outcome'], 'uncertain')
    def test_identical_filename_and_size_do_not_identify_attachment(self):
        s = snapshot([row('ordinary'), row('same-name-size')])
        self.started(s=s)
        self.assertEqual(ledger.reconcile_attachment_operation(self.post([row('ordinary')]), OP)['outcome'], 'uncertain')
        self.assertEqual(ledger.reconcile_attachment_operation(self.post([row('same-name-size')]), OP)['outcome'], 'confirmed_performed')
    def test_add_requires_exact_new_id_and_downloaded_content_digest(self):
        self.started(action='add')
        post = self.post([row(), row('new-id', name='new.txt', contentType='text/plain')])
        for identifier, data in ((None, b'new!'), ('wrong-id', b'new!'), ('new-id', None), ('new-id', b'evil')):
            self.assertEqual(ledger.reconcile_attachment_operation(post, OP, new_attachment_id=identifier, downloaded_content=data)['outcome'], 'uncertain')
        self.assertEqual(ledger.reconcile_attachment_operation(post, OP, new_attachment_id='new-id', downloaded_content=b'new!')['outcome'], 'confirmed_performed')
    def test_add_extra_deleted_or_modified_existing_files_stay_uncertain(self):
        self.started(action='add')
        new = row('new-id', name='new.txt', contentType='text/plain')
        for rows in ([new], [row(name='changed'), new], [row(), new, row('extra')]):
            self.assertEqual(ledger.reconcile_attachment_operation(self.post(rows), OP, new_attachment_id='new-id', downloaded_content=b'new!')['outcome'], 'uncertain')
    def test_inline_cid_unknown_item_reference_and_invalid_ids_cannot_be_deleted(self):
        for target in (row(isInline=True), row(contentId='logo'), row(**{'@odata.type': None}),
                       row(**{'@odata.type': '#microsoft.graph.itemAttachment'}), row(**{'@odata.type': '#microsoft.graph.referenceAttachment'})):
            with self.subTest(target=target), self.assertRaises(ledger.AttachmentLedgerError):
                self.reserve(s=snapshot([target]))
        for identifier in ('missing', '../bad', None):
            with self.subTest(identifier=identifier), self.assertRaises((ledger.AttachmentLedgerError, MicrosoftGraphError)):
                self.reserve(attachment_id=identifier)
    def test_unresolved_or_ambiguous_html_cids_block_all_preparation(self):
        body = {'contentType': 'html', 'content': '<img src="cid:logo">'}
        for rows in ([row()], [row(), row('i1', contentId='logo'), row('i2', contentId='logo')]):
            with self.assertRaises(ledger.AttachmentLedgerError):
                self.reserve(s=snapshot(rows, msg=message(body=body)), action='add')
    def test_untrusted_filenames_metadata_and_small_upload_limit(self):
        for name in ('../secret', 'a\\b.txt', 'bad\r\n.txt', '\u202eevil.exe', '', '..'):
            with self.subTest(name=name), self.assertRaises(ledger.AttachmentLedgerError):
                self.reserve(action='add', name=name)
        for kw in ({'content_type': 'text/plain\r\nX: bad'}, {'content': 'not bytes'}, {'content': b'x'*(ledger.SMALL_UPLOAD_BYTES+1)}):
            with self.assertRaises(ledger.AttachmentLedgerError):
                self.reserve(action='add', **kw)
        self.assertTrue(self.reserve(action='add', content=b'x'*ledger.SMALL_UPLOAD_BYTES)['new'])
    def test_large_existing_file_can_be_explicitly_selected_for_removal(self):
        self.assertTrue(self.reserve(s=snapshot([row(size=100*1024*1024)]))['new'])
    def test_unavailable_or_corrupt_storage_fails_closed(self):
        self.cas.bad_read = True
        with self.assertRaises(ledger.AttachmentLedgerError):
            self.reserve()
        self.cas.bad_read = False
        self.cas.bad_write = True
        with self.assertRaises(ledger.AttachmentLedgerError):
            self.reserve()
        self.cas.bad_write = False
        self.reserve()
        key = next(iter(self.cas.records))
        self.cas.records[key] = '{}'
        with self.assertRaises(ledger.AttachmentLedgerError):
            ledger.inspect_attachment_operation(AUTH, OP)
    def test_lost_prepare_ack_grants_no_claim_and_replay_cannot_start(self):
        self.cas.lost_ack = True
        with self.assertRaises(ledger.AttachmentLedgerError):
            self.reserve()
        self.cas.lost_ack = False
        replay = self.reserve()
        self.assertFalse(replay['new'])
        self.assertNotIn('claim', replay)
        self.assertTrue(replay['blocked'])
    def test_lost_start_ack_stays_uncertain_and_never_restarts(self):
        r = self.reserve()
        self.cas.lost_ack = True
        with self.assertRaises(ledger.AttachmentLedgerError):
            ledger.start_attachment_operation(self.s, OP, r['claim'])
        self.cas.lost_ack = False
        self.assertEqual(ledger.inspect_attachment_operation(AUTH, OP)['outcome'], 'uncertain')
        with self.assertRaises(ledger.AttachmentLedgerError):
            ledger.start_attachment_operation(self.s, OP, r['claim'])
    def test_concurrent_reservations_only_one_session_wins(self):
        self.cas.race = threading.Barrier(2)
        def reserve(op):
            try:
                return self.reserve(op=op)['new']
            except ledger.AttachmentLedgerError:
                return False
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(reserve, (OP, 'p'*43)))
        self.assertEqual(results.count(True), 1)
    def test_concurrent_start_only_one_session_wins(self):
        r = self.reserve()
        self.cas.race = threading.Barrier(2)
        def start(_):
            try:
                return ledger.start_attachment_operation(self.s, OP, r['claim'])['outcome']
            except ledger.AttachmentLedgerError:
                return 'blocked'
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(start, (1, 2)))
        self.assertEqual(results.count('uncertain'), 1)
        self.assertEqual(results.count('blocked'), 1)
    def test_ledger_stores_no_tokens_body_filenames_addresses_or_bytes(self):
        self.reserve(action='add')
        raw = '\n'.join(self.cas.records.values())
        for secret in (TOKEN, 'Original', 'Keep this', 'song.mp3', 'new.txt', 'receiver@example.com', AUTH.email, AUTH.owner_email, 'new!'):
            self.assertNotIn(secret, raw)
    def test_no_ram_fallback_when_durable_store_missing(self):
        with patch.object(ledger.store, '_resolve_durable_store_config', return_value=None), self.assertRaises(ledger.AttachmentLedgerError):
            self.reserve()
    def test_ledger_capacity_fails_closed_without_eviction(self):
        with patch.object(ledger, 'MAX_OPERATIONS', 1):
            r = self.reserve()
            ledger.cancel_prepared_operation(AUTH, OP, r['claim'])
            with self.assertRaises(ledger.AttachmentLedgerError):
                self.reserve(op='p'*43)
        self.assertFalse(self.reserve()['new'])

    def test_forged_store_ack_or_failed_readback_never_grants_claim(self):
        with patch.object(ledger.store, '_write_google_durable_record_if_unchanged', return_value=({'forged': True}, None)):
            with self.assertRaises(ledger.AttachmentLedgerError):
                self.reserve()
        original = self.cas.write
        def lost_readback(*args):
            result = original(*args)
            self.cas.bad_read = True
            return result
        with patch.object(ledger.store, '_write_google_durable_record_if_unchanged', side_effect=lost_readback):
            with self.assertRaises(ledger.AttachmentLedgerError):
                self.reserve()
        self.cas.bad_read = False
        self.assertNotIn('claim', self.reserve())

    def test_corrupted_phase_binding_or_inventory_does_not_release_barrier(self):
        self.started()
        key = next(iter(self.cas.records))
        original = self.cas.records[key]
        for field, value in (('phase', 'finished'), ('binding', '0'*64), ('before', {'ordinary': 'invalid'})):
            data = json.loads(original)
            data['operations'][OP][field] = value
            self.cas.records[key] = json.dumps(data)
            with self.subTest(field=field), self.assertRaises(ledger.AttachmentLedgerError):
                ledger.inspect_attachment_operation(AUTH, OP)
        self.cas.records[key] = original
        self.assertTrue(ledger.inspect_attachment_operation(AUTH, OP)['blocked'])

    def test_cancel_racing_start_has_exactly_one_winner(self):
        r = self.reserve()
        self.cas.race = threading.Barrier(2)
        def act(name):
            try:
                result = (ledger.start_attachment_operation(self.s, OP, r['claim']) if name == 'start'
                          else ledger.cancel_prepared_operation(AUTH, OP, r['claim']))
                return result['outcome']
            except ledger.AttachmentLedgerError:
                return 'blocked'
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(act, ('start', 'cancel')))
        self.assertEqual(results.count('blocked'), 1)
        self.assertEqual(sum(x in {'uncertain', 'confirmed_not_performed'} for x in results), 1)

    def test_inventory_ceiling_prevents_add_without_truncating_existing_files(self):
        s = snapshot([row(str(i), size=20*1024*1024) for i in range(100)])
        with self.assertRaises(ledger.AttachmentLedgerError):
            self.reserve(s=s, action='add')
        self.assertEqual(len(s.attachments), 100)

    def test_wrong_draft_postcondition_cannot_finish_operation(self):
        self.started()
        with self.assertRaises(ledger.AttachmentLedgerError):
            ledger.reconcile_attachment_operation(replace(self.post([]), provider_message_id='other-draft'), OP)
        self.assertTrue(ledger.inspect_attachment_operation(AUTH, OP)['blocked'])

    def test_size_ceiling_on_ledger_never_evicts_previous_operations(self):
        r = self.reserve()
        ledger.cancel_prepared_operation(AUTH, OP, r['claim'])
        original = copy.deepcopy(self.cas.records)
        with patch.object(ledger, 'MAX_RECORD_BYTES', len(next(iter(original.values())).encode())+5):
            with self.assertRaises(ledger.AttachmentLedgerError):
                self.reserve(op='p'*43)
        self.assertEqual(self.cas.records, original)

class OwnerBoundaryTests(LedgerFixture):
    def boundary(self, **overrides):
        defaults = {'resolve_owned_microsoft_mailbox': AUTH,
                    '_owned_token': (TOKEN, AUTH), '_verify_unchanged_owned_mailbox': None,
                    'acquire_mailbox_mutation_lease': {'status': 'acquired', 'token': 'l'*43},
                    'release_mailbox_mutation_lease': {'status': 'released'}}
        defaults.update(overrides)
        patches = []
        for name, value in defaults.items():
            p = patch.object(owned, name, side_effect=value) if isinstance(value, Exception) else patch.object(owned, name, return_value=value)
            p.start(); patches.append(p)
        for p in patches:
            self.addCleanup(p.stop)
    def prepare(self):
        return service.prepare_owned_attachment_operation({}, AUTH.mailbox_id, 'draft-1', OP, 'remove',
                    expected_fingerprint=self.s.fingerprint, attachment_id='ordinary', opener=Graph())
    def test_owned_service_only_reads_graph_and_requires_rw_and_shared_lease(self):
        self.boundary()
        result = self.prepare()
        owned._owned_token.assert_called_once_with({}, AUTH.mailbox_id, require_read_write=True)
        owned.release_mailbox_mutation_lease.assert_called_once()
        self.assertTrue(result['new'])
    def test_different_authority_or_held_lease_prevents_reservation(self):
        self.boundary(_owned_token=(TOKEN, replace(AUTH, account_id='wrong')))
        with self.assertRaises(ledger.AttachmentLedgerError):
            self.prepare()
        self.assertEqual(self.cas.records, {})
    def test_held_lease_never_fetches_token(self):
        self.boundary(acquire_mailbox_mutation_lease={'status': 'held'})
        with self.assertRaises(ledger.AttachmentLedgerError):
            self.prepare()
        owned._owned_token.assert_not_called()
    def test_identity_change_before_reserve_prevents_storage(self):
        self.boundary(_verify_unchanged_owned_mailbox=owned.MicrosoftMailboxAccessError('microsoft_reconnect_required'))
        with self.assertRaises(owned.MicrosoftMailboxAccessError):
            self.prepare()
        self.assertEqual(self.cas.records, {})
    def test_release_failure_returns_no_claim_but_keeps_prepared_barrier(self):
        self.boundary(release_mailbox_mutation_lease={'status': 'unavailable'})
        with self.assertRaises(ledger.AttachmentLedgerError):
            self.prepare()
        self.assertEqual(ledger.inspect_attachment_operation(AUTH, OP)['phase'], 'prepared')
        self.assertNotIn('claim', self.reserve())


    def test_owner_changes_after_cas_never_returns_claim(self):
        self.boundary()
        # Two barrier rechecks surround the snapshot; revoke after ledger CAS.
        owned._verify_unchanged_owned_mailbox.side_effect = [None] * 5 + [owned.MicrosoftMailboxAccessError('microsoft_reconnect_required')]
        with self.assertRaises(owned.MicrosoftMailboxAccessError):
            self.prepare()
        self.assertNotIn('claim', self.reserve())

    def test_start_boundary_rereads_external_version_under_lease(self):
        prepared = self.reserve()
        self.boundary()
        graph = Graph(msg=message(**{'@odata.etag': 'W/"external"', 'changeKey': 'external'}))
        with self.assertRaises(ledger.AttachmentLedgerError):
            service.start_owned_attachment_operation({}, AUTH.mailbox_id, 'draft-1', OP, prepared['claim'], opener=graph)
        self.assertTrue(all(r.get_method() == 'GET' for r in graph.calls))
        self.assertEqual(ledger.inspect_attachment_operation(AUTH, OP)['phase'], 'prepared')
