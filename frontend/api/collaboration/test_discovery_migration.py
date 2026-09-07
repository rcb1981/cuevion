from __future__ import annotations

import base64
from dataclasses import replace
import json
import hashlib
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from api.auth.runtime import AuthenticatedMemberContext
from . import authorization as auth, discovery_migration as migration, models, owner_http, redis_store as store
from . import test_summary as summary
from . import test_lua_redis_integration as fixtures
from . import test_owner_http as http


class DiscoveryMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ProductionLuaRedisIntegrationTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        fixtures.ProductionLuaRedisIntegrationTests.tearDownClass.__func__(cls)

    def setUp(self):
        self.client.command(['FLUSHALL'])
        self.directory = tempfile.TemporaryDirectory(prefix='collab-migration-test-')
        self.addCleanup(self.directory.cleanup)
        self.commands = []
        self.keys = []
        self.run_number = 0
        self.scan_responses = None
        self.provider = 'google'
        self.member = AuthenticatedMemberContext(summary.OWNER, http.OWNER_EMAIL, 'Owner Person', summary.WORKSPACE, 'owner')
        self.context = http._context()
        self.config = http.parse_owner_security_configuration(owner_http._trusted_security_snapshot(http._environment()))
        self.membership = {'memberUserId': self.member.user_id, 'sourceInvitationId': 'tinv_original'}
        self.team = Mock(side_effect=lambda *args: (self.membership, None))
        self.mailbox = Mock(side_effect=lambda *args, **kwargs: {
            'status': 'ok', 'user': {'email': self.member.email}, 'memberAuthority': self.member,
            'inbox': {'id': http.MAILBOX_ID, 'provider': self.provider}})
        for context in (
            patch.dict(os.environ, {store.V2_INDEX_HMAC_ENV: base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip('='),
                                    store.V2_INDEX_HMAC_PREVIOUS_ENV: ''}),
            patch.object(auth, '_resolve_current_authenticated_member', side_effect=lambda _: (self.member, None)),
            patch.object(auth, '_resolve_active_team_member', self.team),
            patch.object(auth, '_shared_config_helper', return_value=self.mailbox),
        ):
            context.start()
            self.addCleanup(context.stop)

    def transport(self, command):
        self.commands.append(command)
        if command[0] == 'SCAN' and self.scan_responses is not None:
            return {'result': self.scan_responses.pop(0)}
        return self.client.transport(command)

    def seed(self, number=1, *, legacy=False, ttl=300_000, **updates):
        value = summary.thread(number, **updates)
        if legacy:
            for field in ('ownerUserId', 'ownerDisplayName', 'participants'):
                value.pop(field)
        key = store.build_v2_thread_key(value['collaborationId'])
        pointer = store.build_v2_source_thread_key(value['ownerEmail'], value['mailboxId'], value['sourceRef'])
        self.client.command(['SET', key, fixtures.wire_json(value, 'thread'), 'PX', ttl])
        self.client.command(['SET', pointer, value['collaborationId'], 'PX', ttl])
        self.keys.append(key)
        return value

    def index(self, user=None):
        return store.build_v2_discovery_key(summary.WORKSPACE, user or self.member.user_id)

    def new_path(self):
        self.run_number += 1
        return Path(self.directory.name) / f'run-{self.run_number}.json'

    def run_page(self, path=None, *, cursor=None, dry_run=False, **kwargs):
        return migration.run_page(self.context, (), enabled=True, checkpoint_path=path or self.new_path(),
            cursor=cursor, dry_run=dry_run, owner_mailbox_id=kwargs.pop('owner_mailbox_id', http.MAILBOX_ID),
            owner_security_configuration=self.config, command_transport=self.transport, **kwargs)

    def one(self, *, dry_run=False, **kwargs):
        self.scan_responses = [['0', list(self.keys)]]
        result = self.run_page(dry_run=dry_run, **kwargs)
        self.assertNotIn(result.get('error', {}).get('code') if result.get('error') else None,
                         ('storage_unavailable', 'invalid_index', 'storage_protocol_error', 'checkpoint_unavailable'))
        return result

    def snapshot(self, keys):
        return [self.client.command(['EVAL', "local d=redis.call('DUMP',KEYS[1]); return {d and redis.sha1hex(d) or '',redis.call('PEXPIRETIME',KEYS[1])}", 1, k]) for k in keys]

    def summaries(self, *, membership_ref=None):
        result = store._list_v2_summaries(summary.WORKSPACE, self.member.user_id, self.member.email,
                                         membership_ref=membership_ref, command_transport=self.client.transport)
        self.assertEqual(result['status'], 'ok', result)
        return result['page']['summaries']

    def participant(self, *, user=summary.PARTICIPANT, ref='tinv_original'):
        self.member = AuthenticatedMemberContext(user, 'participant@example.com', 'Participant', summary.WORKSPACE, 'member')
        self.context = http._context(owner_email=self.member.email, display_name=self.member.name)
        self.membership = {'memberUserId': user, 'sourceInvitationId': ref}

    def test_historical_owner_dry_run_apply_rerun_no_rewrite(self):
        value = self.seed()
        before = self.snapshot([self.keys[0], self.index()])
        dry = self.one(dry_run=True)
        self.assertEqual((dry['wouldEnroll'], dry['enrolled'], dry['done']), (1, 0, True), dry)
        self.assertEqual(self.snapshot([self.keys[0], self.index()]), before)
        applied = self.one()
        self.assertEqual((applied['enrolled'], applied['done']), (1, True), applied)
        self.assertEqual(self.summaries()[0]['collaborationId'], value['collaborationId'])
        after = self.snapshot([self.keys[0], self.index()])
        rerun = self.one()
        self.assertEqual(rerun['alreadyPresent'], 1, rerun)
        self.assertEqual(self.snapshot([self.keys[0], self.index()]), after)

    def test_guest_only_legacy_owner_enrichment_preserves_every_value_and_expiry(self):
        value = self.seed(legacy=True)
        pointer = store.build_v2_source_thread_key(value['ownerEmail'], value['mailboxId'], value['sourceRef'])
        before = self.snapshot([self.keys[0], pointer])
        self.assertEqual(self.one(dry_run=True)['wouldEnroll'], 1)
        self.assertEqual(self.snapshot([self.keys[0], pointer]), before)
        self.assertEqual(self.one()['enrolled'], 1)
        current = store._load_v2_thread(value['collaborationId'], command_transport=self.client.transport).record
        self.assertEqual({k: current[k] for k in value}, value)
        self.assertEqual(current['ownerUserId'], summary.OWNER)
        self.assertEqual(current['participants'], [])
        after = self.snapshot([self.keys[0], pointer])
        self.assertEqual(after[0][1], before[0][1])
        self.assertEqual(after[1], before[1])

    def test_unprovable_legacy_owner_and_other_owner_cannot_be_claimed(self):
        self.seed(legacy=True)
        self.seed(2, legacy=True, ownerEmail='another@example.com')
        before = self.snapshot(self.keys + [self.index()])
        result = self.one(owner_mailbox_id=None)
        self.assertEqual((result['unresolvedIdentity'], result['skippedUnauthorized']), (1, 1), result)
        self.assertEqual(self.snapshot(self.keys + [self.index()]), before)

    def test_explicit_participant_only_enrolls_verified_viewer(self):
        self.seed(participants=[{'userId': summary.PARTICIPANT, 'displayName': 'Participant', 'membershipRef': 'tinv_original'}])
        self.participant()
        self.assertEqual(self.one(owner_mailbox_id=None)['enrolled'], 1)
        self.assertEqual(self.summaries(membership_ref='tinv_original')[0]['viewerAccess'], 'participant')
        self.assertEqual(self.client.command(['EXISTS', self.index(summary.OWNER)]), 0)
        self.assertEqual(self.team.call_count, 1)
        self.mailbox.assert_not_called()

    def test_stale_rebound_and_unrelated_members_do_not_enroll(self):
        self.seed(participants=[{'userId': summary.PARTICIPANT, 'displayName': 'Participant', 'membershipRef': 'tinv_original'}])
        for user, ref, expected in ((summary.PARTICIPANT, 'tinv_rebound', 'staleEntitlement'),
                                    (summary.OTHER, 'tinv_original', 'skippedUnauthorized')):
            with self.subTest(user=user):
                self.participant(user=user, ref=ref)
                before = self.snapshot(self.keys + [self.index()])
                result = self.one(owner_mailbox_id=None)
                self.assertEqual(result[expected], 1, result)
                self.assertEqual(self.snapshot(self.keys + [self.index()]), before)
        self.participant()
        self.team.side_effect = lambda *args: (None, 'not_active')
        self.assertEqual(self.one(owner_mailbox_id=None)['staleEntitlement'], 1)

    def test_all_lifecycle_states_and_exact_sources(self):
        for provider in ('google', 'custom_imap'):
            self.client.command(['FLUSHALL'])
            self.keys = []
            self.provider = provider
            for i, state in enumerate(('needs_review', 'needs_action', 'note_only', 'resolved')):
                source = ({'provider': 'google', 'providerMessageId': f'Exact-ID_{i}'} if provider == 'google' else
                          {'provider': 'custom_imap', 'folder': 'INBOX', 'uidValidity': '18446744073709551615', 'imapUid': str(i+1)})
                self.seed(i, state=state, sourceRef=source)
            before = self.snapshot(self.keys)
            self.assertEqual(self.one()['enrolled'], 4)
            self.assertEqual(self.snapshot(self.keys), before)
            results = self.summaries()
            self.assertEqual([r['state'] for r in results], ['needs_review', 'needs_action', 'note_only', 'resolved'])
            for i, row in enumerate(results):
                canonical = store._load_v2_thread(row['collaborationId'], command_transport=self.client.transport).record
                self.assertEqual(row['sourceRef'], canonical['sourceRef'])

    def test_real_scan_full_pass_page_bound_and_content_free_transport(self):
        for i in range(13):
            self.seed(i)
        self.client.command(['SET', store.build_v2_guest_session_key('a'*64), 'MIGRATION_GUEST_BEARER_DO_NOT_RETURN', 'EX', 100])
        self.client.command(['SET', store.build_v2_invite_token_key('b'*64), 'MIGRATION_INVITE_SECRET_DO_NOT_RETURN', 'EX', 100])
        path, cursor, examined = self.new_path(), None, 0
        for _ in range(20):
            self.commands.clear()
            result = self.run_page(path, cursor=cursor)
            self.assertEqual(result['status'], 'ok', result)
            self.assertLessEqual(result['examined'], 5)
            self.assertLessEqual(sum(c[0] == 'SCAN' for c in self.commands), 1)
            self.assertLessEqual(sum(c[0] == 'EVAL' for c in self.commands), 2)
            self.assertEqual(self.team.call_count, self.mailbox.call_count)
            for command in self.commands:
                if command[0] == 'SCAN':
                    self.assertEqual(command[2:], ['MATCH', migration.SCAN_PATTERN, 'COUNT', 100])
                serialized = json.dumps(command[3:] if command[0] == 'EVAL' else command)
                for secret in ('Internal history', 'Shared history', 'bodyText', 'guest-session', 'invite-token'):
                    self.assertNotIn(secret, serialized)
            encoded = json.dumps(result)
            for secret in ('owner@example.com', 'sourceRef', 'participants', 'bodyText', 'token', self.keys[0],
                           'MIGRATION_GUEST_BEARER_DO_NOT_RETURN', 'MIGRATION_INVITE_SECRET_DO_NOT_RETURN'):
                self.assertNotIn(secret, encoded)
            examined += result['examined']
            if result['done']:
                break
            cursor = result['nextCursor']
        else:
            self.fail('bounded real SCAN did not finish')
        self.assertGreaterEqual(examined, 13)
        self.assertEqual(len(self.summaries()), 13)

    def test_completed_checkpoint_and_lost_response_replay_are_read_only(self):
        self.seed()
        self.scan_responses = [['0', self.keys]]
        path = self.new_path()
        first = self.run_page(path)
        self.assertTrue(first['done'], first)
        before = self.snapshot(self.keys + [self.index()])
        self.commands.clear()
        self.assertEqual(self.run_page(path), first)
        self.assertEqual(self.commands, [])
        self.assertEqual(self.snapshot(self.keys + [self.index()]), before)
        sealed = self.run_page(path, cursor='x'*32)
        self.assertEqual(sealed['error']['code'], 'migration_complete')

    def fill_index(self, count, *, present=True, ttl=500_000):
        fields = []
        for i in range(10_000, 10_000 + count):
            value = summary.thread(i)
            raw = fixtures.wire_json(value, 'thread')
            if present:
                self.client.command(['SET', store.build_v2_thread_key(value['collaborationId']), raw, 'PX', ttl])
            binding = {k: value[k] for k in ('ownerUserId', 'mailboxId', 'sourceRef')}
            binding['threadHash'] = hashlib.sha1(raw.encode()).hexdigest()
            fields.extend([value['collaborationId'], json.dumps(binding)])
        self.client.command(['HSET', self.index(), *fields])
        self.client.command(['PEXPIRE', self.index(), ttl])

    def test_capacity_near_limit_enrolls_safely_and_retains_blocked_candidate(self):
        self.fill_index(999)
        self.seed(1)
        self.seed(2)
        original = self.client.command(['HGET', self.index(), f'{10000:022d}'])
        before = self.snapshot([self.index(), *self.keys])
        dry = self.one(dry_run=True)
        self.assertEqual((dry['wouldEnroll'], dry['capacity'], dry['done']), (1, 1, False), dry)
        self.assertEqual(self.snapshot([self.index(), *self.keys]), before)
        self.scan_responses = [['0', self.keys]]
        path = self.new_path()
        applied = self.run_page(path)
        self.assertEqual((applied['enrolled'], applied['capacity'], applied['done']), (1, 1, False), applied)
        self.assertEqual(self.client.command(['HLEN', self.index()]), 1000)
        self.assertEqual(self.client.command(['HGET', self.index(), f'{10000:022d}']), original)
        before = self.snapshot([self.index(), *self.keys])
        resumed = self.run_page(path, cursor=applied['nextCursor'])
        self.assertEqual((resumed['examined'], resumed['capacity']), (1, 1), resumed)
        self.assertEqual(self.snapshot([self.index(), *self.keys]), before)

    def test_dry_run_reserves_capacity_across_pages_and_duplicate_scan_results(self):
        self.fill_index(995)
        for i in range(6):
            self.seed(i)
        self.scan_responses = [['17', self.keys[:5]], ['0', [self.keys[0], self.keys[5]]]]
        path = self.new_path()
        before = self.snapshot([self.index(), *self.keys])
        first = self.run_page(path, dry_run=True)
        self.assertEqual(first['wouldEnroll'], 5, first)
        second = self.run_page(path, cursor=first['nextCursor'], dry_run=True)
        self.assertEqual((second['alreadyPlanned'], second['wouldEnroll'], second['capacity']), (1, 0, 1), second)
        self.assertFalse(second['done'])
        self.assertEqual(self.snapshot([self.index(), *self.keys]), before)

    def test_full_live_index_fails_zero_write_and_missing_only_pruning_is_dry_read_only(self):
        self.seed(legacy=True)
        self.fill_index(1000)
        before = self.snapshot([self.index(), *self.keys])
        result = self.one()
        self.assertEqual(result['capacity'], 1, result)
        self.assertEqual(self.snapshot([self.index(), *self.keys]), before)
        self.client.command(['DEL', self.index()])
        self.fill_index(1000, present=False)
        # Remove these exact canonical keys; no keyspace deletion/enumeration.
        self.client.command(['DEL', *[store.build_v2_thread_key(f'{i:022d}') for i in range(10000,11000)]])
        before = self.snapshot([self.index(), *self.keys])
        dry = self.one(dry_run=True)
        self.assertEqual(dry['wouldEnroll'], 1, dry)
        self.assertEqual(self.snapshot([self.index(), *self.keys]), before)
        self.assertEqual(self.one()['enrolled'], 1)
        self.assertEqual(self.client.command(['HLEN', self.index()]), 1)
        self.assertGreater(self.client.command(['PTTL', self.index()]), 0)
        self.assertLessEqual(self.client.command(['PEXPIRETIME', self.index()]), self.client.command(['PEXPIRETIME', self.keys[0]]))

    def test_multiple_candidates_never_shorten_shared_index_expiry(self):
        for i, ttl in enumerate((400_000, 100_000, 250_000)):
            self.seed(i, ttl=ttl)
        before = self.snapshot(self.keys)
        self.assertEqual(self.one()['enrolled'], 3)
        self.assertEqual(self.snapshot(self.keys), before)
        self.assertEqual(self.client.command(['PEXPIRETIME', self.index()]), max(v[1] for v in before))
        # A pre-existing longer index must retain its own expiry as well.
        self.client.command(['PEXPIRE', self.index(), 600_000])
        before = self.snapshot([self.index(), *self.keys])
        self.assertEqual(self.one()['alreadyPresent'], 3)
        self.assertEqual(self.snapshot([self.index(), *self.keys]), before)

    def test_expired_immortal_wrong_type_oversized_and_invalid_ids_are_zero_write(self):
        for i in range(5):
            self.seed(i)
        self.client.command(['DEL', self.keys[0]])
        self.client.command(['PERSIST', self.keys[1]])  # corrupt local fixture only
        self.client.command(['DEL', self.keys[2]])
        self.client.command(['HSET', self.keys[2], 'wrong', 'type'])
        self.client.command(['SET', self.keys[3], 'x' * (models.MAX_V2_THREAD_BYTES + 1), 'EX', 100])
        self.keys[4] = store.V2_THREAD_KEY_PREFIX + 'invalid-id'
        before = self.snapshot(self.keys + [self.index()])
        result = self.one()
        self.assertEqual((result['missing'], result['skippedInvalid']), (1, 4), result)
        self.assertEqual(self.snapshot(self.keys + [self.index()]), before)

    def test_malformed_canonical_json_schema_source_and_owner_groups_are_zero_write(self):
        value = self.seed()
        base = fixtures.wire_json(value, 'thread')
        corruptions = [
            base[:-1] + ',"state":"resolved"}',
            base.replace('"participants":[]', '"participants":{}'),
            base.replace('"v":"2"', '"v":2'),
            base.replace('"providerMessageId":"google-1"', '"providerMessageId":""'),
            base.replace('"ownerUserId":"' + summary.OWNER + '"', '"ownerUserId":"bad"'),
            base.replace('"messages":[', '"messages":[' + '{"unexpected":"content"},'),
            base.replace('"collaborationId":"' + value['collaborationId'] + '"', '"collaborationId":{}'),
            base.replace('"ownerDisplayName":"Owner Person",', ''),
        ]
        for raw in corruptions:
            with self.subTest(raw=raw[:30]):
                self.client.command(['SET', self.keys[0], raw, 'EX', 100])
                before = self.snapshot([self.keys[0], self.index()])
                result = self.one()
                self.assertEqual(result['skippedInvalid'], 1, result)
                self.assertEqual(self.snapshot([self.keys[0], self.index()]), before)

    def test_missing_conflicting_wrong_type_and_rebound_source_pointers_fail_closed(self):
        value = self.seed()
        pointer = store.build_v2_source_thread_key(value['ownerEmail'], value['mailboxId'], value['sourceRef'])
        variants = (["DEL", pointer], ['SET', pointer, 'Z'*22, 'EX', 100], ['SET', pointer, value['collaborationId']],
                    ['HSET', pointer, 'bad', 'value'])
        for command in variants:
            self.client.command(['DEL', pointer])
            self.client.command(command)
            before = self.snapshot([self.keys[0], pointer, self.index()])
            result = self.one()
            self.assertEqual(result['skippedInvalid'], 1, result)
            self.assertEqual(self.snapshot([self.keys[0], pointer, self.index()]), before)

    def test_previous_hmac_pointer_is_validated_without_rotation_or_ttl_refresh(self):
        value = self.seed(legacy=True)
        pointer = store.build_v2_source_thread_key(value['ownerEmail'], value['mailboxId'], value['sourceRef'])
        old = os.environ[store.V2_INDEX_HMAC_ENV]
        with patch.dict(os.environ, {store.V2_INDEX_HMAC_PREVIOUS_ENV: old,
                                    store.V2_INDEX_HMAC_ENV: base64.urlsafe_b64encode(b'z'*32).decode().rstrip('=')}):
            new_pointer = store.build_v2_source_thread_key(value['ownerEmail'], value['mailboxId'], value['sourceRef'])
            before = self.snapshot([pointer, new_pointer, self.keys[0], self.index()])
            self.assertEqual(self.one(dry_run=True)['wouldEnroll'], 1)
            self.assertEqual(self.snapshot([pointer, new_pointer, self.keys[0], self.index()]), before)
            self.assertEqual(self.one()['enrolled'], 1)
            self.assertEqual(self.snapshot([pointer, new_pointer]), before[:2])
            self.client.command(['SET', new_pointer, 'Z'*22, 'EX', 100])
            before = self.snapshot([pointer, new_pointer, self.keys[0], self.index()])
            self.assertEqual(self.one()['skippedInvalid'], 1)
            self.assertEqual(self.snapshot([pointer, new_pointer, self.keys[0], self.index()]), before)

    def test_corrupt_or_foreign_discovery_binding_is_never_overwritten(self):
        value = self.seed()
        self.assertEqual(self.one()['enrolled'], 1)
        raw = self.client.command(['HGET', self.index(), value['collaborationId']])
        for changed in ('bad json', raw.replace(summary.OWNER, summary.OTHER)):
            self.client.command(['HSET', self.index(), value['collaborationId'], changed])
            before = self.snapshot([self.index(), self.keys[0]])
            self.assertEqual(self.one()['skippedInvalid'], 1)
            self.assertEqual(self.snapshot([self.index(), self.keys[0]]), before)

    def test_canonical_race_retains_candidate_and_revalidates_on_resume(self):
        value = self.seed()
        original = self.transport
        raced = False
        def transport(command):
            nonlocal raced
            if command[0] == 'EVAL' and command[1] == migration._ENROLL_LUA and not raced:
                raced = True
                replacement = {**value, 'state': 'resolved', 'updatedAt': value['updatedAt'] + 1}
                self.client.command(['SET', self.keys[0], fixtures.wire_json(replacement, 'thread'), 'KEEPTTL'])
            return original(command)
        self.transport = transport
        self.scan_responses = [['0', self.keys]]
        path = self.new_path()
        first = self.run_page(path)
        self.assertEqual((first['retry'], first['enrolled'], first['done']), (1, 0, False), first)
        self.assertEqual(self.client.command(['EXISTS', self.index()]), 0)
        second = self.run_page(path, cursor=first['nextCursor'])
        self.assertEqual(second['enrolled'], 1, second)
        self.assertEqual(self.summaries()[0]['state'], 'resolved')

    def test_interruption_after_scan_preserves_all_pending_keys_without_rescanning(self):
        for i in range(7):
            self.seed(i)
        self.scan_responses = [['0', self.keys]]
        path = self.new_path()
        with patch.object(migration, '_process', side_effect=RuntimeError('simulated process loss')):
            with self.assertRaises(RuntimeError):
                self.run_page(path)
        saved = json.loads(path.read_text())
        self.assertEqual(saved['pending'], sorted(self.keys))
        self.assertEqual(saved['scanCursor'], '0')
        self.commands.clear()
        first = self.run_page(path)
        self.assertEqual((first['enrolled'], first['done']), (5, False), first)
        second = self.run_page(path, cursor=first['nextCursor'])
        self.assertEqual((second['enrolled'], second['done']), (2, True), second)
        self.assertFalse(any(c[0] == 'SCAN' for c in self.commands))
        self.assertEqual(len(self.summaries()), 7)

    def test_interruption_after_commit_before_checkpoint_is_idempotent(self):
        self.seed(legacy=True)
        self.scan_responses = [['0', self.keys]]
        path = self.new_path()
        real_save = migration._save
        def fail_final(path, state):
            if state['previousResult'] is not None:
                raise OSError('simulated checkpoint write failure')
            return real_save(path, state)
        with patch.object(migration, '_save', side_effect=fail_final):
            first = self.run_page(path)
        self.assertEqual(first['error']['code'], 'checkpoint_unavailable', first)
        before = self.snapshot([self.keys[0], self.index()])
        resumed = self.run_page(path)
        self.assertEqual((resumed['alreadyPresent'], resumed['done']), (1, True), resumed)
        self.assertEqual(self.snapshot([self.keys[0], self.index()]), before)

    def test_scan_empty_batches_duplicates_and_count_overshoot_are_not_dropped(self):
        value = self.seed()
        self.scan_responses = [['17', []], ['18', [self.keys[0]]*2], ['0', self.keys]]
        path = self.new_path()
        first = self.run_page(path)
        self.assertEqual((first['examined'], first['done']), (0, False), first)
        second = self.run_page(path, cursor=first['nextCursor'])
        self.assertEqual(second['enrolled'], 1, second)
        before = self.snapshot([self.index(), self.keys[0]])
        third = self.run_page(path, cursor=second['nextCursor'])
        self.assertEqual((third['alreadyPresent'], third['done']), (1, True), third)
        self.assertEqual(self.snapshot([self.index(), self.keys[0]]), before)
        overshoot = [store.build_v2_thread_key(f'{i:022d}') for i in range(200,301)]
        self.scan_responses = [['0', overshoot]]
        path = self.new_path()
        result = self.run_page(path)
        self.assertEqual(result['missing'], 5, result)
        self.assertEqual(len(json.loads(path.read_text())['pending']), 96)

    def test_scan_protocol_overflow_and_quota_exhaustion_never_advance_unsafely(self):
        for value in ([17, []], ['-1', []], ['18446744073709551616', []],
                      ['0', ['unrelated:key']], ['0', [store.V2_THREAD_KEY_PREFIX + 'A'*22] * 1001]):
            self.scan_responses = [value]
            path = self.new_path()
            result = self.run_page(path)
            self.assertEqual(result['error']['code'], 'scan_response_limit_or_protocol', result)
            saved = json.loads(path.read_text())
            self.assertEqual(saved['scanCalls'], 1)
            self.assertEqual(saved['scanCursor'], '0')
        self.scan_responses = [['17', []]]
        path = self.new_path()
        first = self.run_page(path, scan_budget=1)
        self.commands.clear()
        second = self.run_page(path, cursor=first['nextCursor'], scan_budget=1)
        self.assertEqual(second['error']['code'], 'scan_budget_exhausted')
        self.assertFalse(second['done'])
        self.assertEqual(self.commands, [])

    def test_disabled_guests_unverified_context_and_auth_failures_cannot_invoke(self):
        self.seed()
        path = self.new_path()
        result = migration.run_page(self.context, (), checkpoint_path=path,
            owner_security_configuration=self.config, command_transport=self.transport)
        self.assertEqual(result['error']['code'], 'migration_disabled')
        self.assertFalse(path.exists())
        for context in (None, object(), {'userId': summary.OWNER, 'workspaceId': summary.WORKSPACE}):
            self.context = context
            result = self.run_page(path)
            self.assertEqual(result['error']['code'], 'forbidden', result)
        self.context = http._context()
        with patch.object(auth, '_resolve_current_authenticated_member', return_value=(None, 'unauthorized')):
            self.assertEqual(self.run_page(path)['error']['code'], 'forbidden')
        self.assertEqual(self.commands, [])
        self.assertFalse(path.exists())

    def test_wrong_workspace_wrong_owner_and_mailbox_visibility_do_not_grant_ownership(self):
        self.seed(workspaceId=fixtures.OTHER_WORKSPACE_ID)
        self.seed(2, ownerUserId=summary.OTHER)
        self.seed(3, mailboxId='another.mailbox')
        before = self.snapshot([self.index(), *self.keys])
        result = self.one()
        self.assertEqual(result['skippedUnauthorized'], 3, result)
        self.assertEqual(self.snapshot([self.index(), *self.keys]), before)
        self.mailbox.side_effect = lambda *a, **kw: {'status': 'not_found'}
        self.commands.clear()
        result = self.run_page()
        self.assertEqual(result['error']['code'], 'forbidden')
        self.assertEqual(self.commands, [])

    def test_checkpoint_user_workspace_mode_scope_and_cursor_are_bound(self):
        self.seed()
        self.scan_responses = [['17', self.keys]]
        path = self.new_path()
        first = self.run_page(path, dry_run=True)
        self.commands.clear()
        changed_mode = self.run_page(path, cursor=first['nextCursor'])
        self.assertEqual(changed_mode['error']['code'], 'invalid_checkpoint')
        changed_scope = self.run_page(path, cursor=first['nextCursor'], dry_run=True, owner_mailbox_id=None)
        self.assertEqual(changed_scope['error']['code'], 'invalid_checkpoint')
        wrong_cursor = self.run_page(path, cursor='x'*32, dry_run=True)
        self.assertEqual(wrong_cursor['error']['code'], 'invalid_cursor')
        self.participant()
        other_user = self.run_page(path, cursor=first['nextCursor'], dry_run=True)
        self.assertEqual(other_user['error']['code'], 'invalid_checkpoint')
        self.assertEqual(self.commands, [])

    def test_each_page_rechecks_team_and_rebound_canonical_authority(self):
        for i in range(6):
            self.seed(i, participants=[{'userId': summary.PARTICIPANT, 'displayName': 'Participant', 'membershipRef': 'tinv_original'}])
        self.participant()
        self.scan_responses = [['0', self.keys]]
        path = self.new_path()
        first = self.run_page(path, owner_mailbox_id=None)
        self.assertEqual(first['enrolled'], 5)
        self.membership['sourceInvitationId'] = 'tinv_rebound'
        second = self.run_page(path, cursor=first['nextCursor'], owner_mailbox_id=None)
        self.assertEqual(second['staleEntitlement'], 1, second)
        self.assertEqual(self.team.call_count, 2)
        self.assertEqual(self.summaries(membership_ref='tinv_rebound'), [])
        last = store._load_v2_thread(f'{5:022d}', command_transport=self.client.transport).record
        last['participants'][0]['membershipRef'] = 'tinv_rebound'
        self.client.command(['SET', self.keys[5], fixtures.wire_json(last, 'thread'), 'KEEPTTL'])
        self.keys = self.keys[5:]
        self.assertEqual(self.one(owner_mailbox_id=None)['enrolled'], 1)

    def test_team_unavailable_fails_closed_and_owner_independent_of_team_membership(self):
        self.seed()
        self.team.side_effect = lambda *a: (None, 'unavailable')
        self.assertEqual(self.run_page()['error']['code'], 'auth_unavailable')
        self.assertEqual(self.commands, [])
        self.team.side_effect = lambda *a: (None, 'not_active')
        self.assertEqual(self.one()['enrolled'], 1)

    def test_large_canonical_batch_keeps_content_inside_redis(self):
        for i in range(5):
            self.seed(i, sourceMessage={**summary.thread()['sourceMessage'], 'bodyText': 'b'*models.MAX_V2_SOURCE_BODY},
                      messages=[fixtures.message_record(index=j, created_at=summary.thread()['updatedAt'], text='m'*16000) for j in range(7)])
        returned_bytes = []
        real = self.transport
        def capture(command):
            result = real(command)
            if command[0] == 'EVAL':
                encoded = json.dumps(result)
                returned_bytes.append(len(encoded))
                self.assertNotIn('bodyText', encoded)
                self.assertNotIn('m'*100, encoded)
            return result
        self.transport = capture
        self.scan_responses = [['0', self.keys]]
        path, cursor, total, durations = self.new_path(), None, 0, []
        for i in range(5):
            started = time.monotonic()
            result = self.run_page(path, cursor=cursor)
            durations.append(time.monotonic() - started)
            self.assertEqual(result['status'], 'ok', result)
            self.assertEqual(result['enrolled'], 1, result)
            self.assertEqual(result['deferred'], 4-i, result)
            total += result['enrolled']
            cursor = result['nextCursor']
        self.assertTrue(result['done'])
        self.assertEqual(total, 5)
        self.assertEqual(len(returned_bytes), 10)
        self.assertLess(max(returned_bytes), 5000)
        print(f'Local migration large-record steps: max {max(durations):.3f}s; max {max(returned_bytes)} response bytes')

    def test_large_legacy_owner_enrichment_also_fits_strict_byte_budget(self):
        for i in range(2):
            self.seed(i, legacy=True, sourceMessage={**summary.thread()['sourceMessage'], 'bodyText': 'b'*models.MAX_V2_SOURCE_BODY},
                      messages=[fixtures.message_record(index=j, created_at=summary.thread()['updatedAt'], text='m'*16000) for j in range(7)])
        before = self.snapshot(self.keys)
        self.scan_responses = [['0', self.keys]]
        path = self.new_path()
        first = self.run_page(path)
        self.assertEqual((first['enrolled'], first['deferred']), (1, 1), first)
        second = self.run_page(path, cursor=first['nextCursor'])
        self.assertEqual((second['enrolled'], second['done']), (1, True), second)
        self.assertEqual([value[1] for value in self.snapshot(self.keys)], [value[1] for value in before])

    def test_private_checkpoint_permissions_tampering_and_locks_fail_closed(self):
        self.scan_responses = [['17', []]]
        path = self.new_path()
        first = self.run_page(path)
        self.commands.clear()
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        os.chmod(path, 0o644)
        self.assertEqual(self.run_page(path, cursor=first['nextCursor'])['error']['code'], 'checkpoint_permissions')
        os.chmod(path, 0o600)
        original = path.read_text()
        corrupted = json.loads(original)
        corrupted['pending'] = ['outside:namespace']
        path.write_text(json.dumps(corrupted))
        self.assertEqual(self.run_page(path, cursor=first['nextCursor'])['error']['code'], 'invalid_checkpoint')
        path.write_text(original)
        fd = os.open(str(path)+'.lock', os.O_RDWR)
        try:
            migration.fcntl.flock(fd, migration.fcntl.LOCK_EX | migration.fcntl.LOCK_NB)
            self.assertEqual(self.run_page(path, cursor=first['nextCursor'])['error']['code'], 'checkpoint_busy')
        finally:
            os.close(fd)
        self.assertEqual(self.commands, [])

    def command_counts(self, names):
        raw = self.client.command(['INFO', 'commandstats'])
        return {name: int(match.group(1)) if (match := re.search(r'^cmdstat_' + name + r':calls=(\d+)', raw, re.M)) else 0
                for name in names}

    def test_redis_command_counters_prove_dry_run_and_unchanged_enrollment_zero_writes(self):
        self.seed(legacy=True)
        writes = ('hset', 'hdel', 'set', 'del', 'pexpire', 'expire', 'persist', 'psetex')
        before = self.command_counts(writes)
        self.assertEqual(self.one(dry_run=True)['wouldEnroll'], 1)
        self.assertEqual(self.command_counts(writes), before)
        self.assertEqual(self.one()['enrolled'], 1)
        before = self.command_counts(writes)
        self.assertEqual(self.one()['alreadyPresent'], 1)
        self.assertEqual(self.command_counts(writes), before)

    def test_capacity_probe_runs_once_per_page_not_once_per_candidate(self):
        self.fill_index(1000)
        for i in range(5):
            self.seed(i)
        before = self.command_counts(('exists', 'hkeys', 'hset', 'hdel'))
        result = self.one(dry_run=True)
        self.assertEqual(result['capacity'], 5, result)
        after = self.command_counts(before)
        self.assertEqual(after['exists'] - before['exists'], 1000)
        self.assertEqual(after['hkeys'] - before['hkeys'], 2)
        self.assertEqual((after['hset'], after['hdel']), (before['hset'], before['hdel']))

    def test_normal_owner_entrypoint_does_not_import_or_expose_migration(self):
        check = subprocess.run([sys.executable, '-c',
            "import sys; import api.collaboration.owner_http; assert 'api.collaboration.discovery_migration' not in sys.modules"],
            capture_output=True, text=True, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        self.assertEqual(check.returncode, 0, check.stderr)
        csrf = http.issue_owner_csrf_token(self.context, self.config, now=http.NOW)[0]
        with patch.object(owner_http, '_resolve_context', return_value=self.context):
            response = http._invoke(http._request({'operation': 'discovery_migration'}, csrf=csrf))
        self.assertEqual(response.status, 400)
        self.assertEqual(self.commands, [])

    def test_index_wrong_type_immortal_and_over_capacity_reject_without_writes(self):
        self.seed()
        self.client.command(['SET', self.index(), 'bad', 'EX', 100])
        before = self.snapshot([self.index(), *self.keys])
        self.scan_responses = [['0', self.keys]]
        self.assertEqual(self.run_page()['error']['code'], 'invalid_index')
        self.assertEqual(self.snapshot([self.index(), *self.keys]), before)
        self.client.command(['DEL', self.index()])
        self.fill_index(1001, present=False)
        before = self.snapshot([self.index(), *self.keys])
        self.scan_responses = [['0', self.keys]]
        self.assertEqual(self.run_page()['error']['code'], 'invalid_index')
        self.assertEqual(self.snapshot([self.index(), *self.keys]), before)
        self.client.command(['DEL', self.index()])
        self.fill_index(1, present=False)
        self.client.command(['PERSIST', self.index()])
        before = self.snapshot([self.index(), *self.keys])
        self.assertEqual(self.one()['skippedInvalid'], 1)
        self.assertEqual(self.snapshot([self.index(), *self.keys]), before)

    def test_expiry_between_probe_and_enrollment_never_publishes(self):
        self.seed()
        real = self.transport
        def expire(command):
            if command[0] == 'EVAL' and command[1] == migration._ENROLL_LUA:
                self.client.command(['DEL', self.keys[0]])
            return real(command)
        self.transport = expire
        result = self.one()
        self.assertEqual((result['missing'], result['enrolled']), (1, 0), result)
        self.assertEqual(self.client.command(['EXISTS', self.index()]), 0)

    def test_concurrent_independent_runs_enroll_one_canonical_reference(self):
        self.seed(legacy=True)
        self.scan_responses = [['0', self.keys], ['0', self.keys]]
        paths = [self.new_path(), self.new_path()]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(self.run_page, paths))
        self.assertEqual(sum(r['enrolled'] for r in results), 1, results)
        for path, result in zip(paths, results):
            if result['retry']:
                result = self.run_page(path, cursor=result['nextCursor'])
            self.assertTrue(result['done'], result)
        self.assertEqual(self.client.command(['HLEN', self.index()]), 1)
        self.assertEqual(len(self.summaries()), 1)


if __name__ == '__main__':
    unittest.main()
