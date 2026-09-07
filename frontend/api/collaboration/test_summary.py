from __future__ import annotations

import base64
import copy
import json
import os
import time
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from api.auth.runtime import AuthenticatedMemberContext
from . import application, authorization, models, mutations, owner_http, owner_rate_limit, redis_store as store
from . import test_lifecycle as life
from . import test_lua_redis_integration as fixtures
from . import test_owner_http as http

OWNER = life.OWNER_ID
PARTICIPANT = life.PARTICIPANT_ID
OTHER = 'usr_' + 'C' * 21 + 'A'
WORKSPACE = http.WORKSPACE_ID


def thread(number=None, **updates):
    value = life.thread_record()
    value.update(workspaceId=WORKSPACE, mailboxId=http.MAILBOX_ID, ownerDisplayName='Owner Person')
    if number is not None:
        value.update(collaborationId=f'{number:022d}', sourceRef={'provider': 'google', 'providerMessageId': f'google-{number}'})
    value.update(updates)
    return value


def capability(value, **updates):
    return life.capability('read', workspace_id=value['workspaceId'], mailbox_id=value['mailboxId'],
                           collaboration_id=value['collaborationId'], mailbox_provider=value['sourceRef']['provider'],
                           owner_display_name='Owner Person', **updates)


class SummaryRedisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ProductionLuaRedisIntegrationTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        fixtures.ProductionLuaRedisIntegrationTests.tearDownClass.__func__(cls)

    def setUp(self):
        self.client.command(['FLUSHALL'])
        env = patch.dict(os.environ, {store.V2_INDEX_HMAC_ENV: base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip('='),
                                      store.V2_INDEX_HMAC_PREVIOUS_ENV: ''})
        env.start()
        self.addCleanup(env.stop)
        self.commands = []

    def transport(self, command):
        self.commands.append(command)
        return self.client.transport(command)

    def create(self, value):
        result = store._create_v2_thread(value, command_transport=self.transport)
        self.assertEqual(result.get('status'), 'ok', result)
        return result

    def index(self, user=OWNER, workspace=WORKSPACE):
        return store.build_v2_discovery_key(workspace, user)

    def page(self, user=OWNER, *, email=None, membership_ref=None, cursor=None, workspace=WORKSPACE):
        result = store._list_v2_summaries(workspace, user, email or ('owner@example.com' if user == OWNER else 'participant@example.com'),
                                         membership_ref=membership_ref, cursor=cursor, command_transport=self.transport)
        self.assertEqual(result.get('status'), 'ok', result)
        return result['page']

    def snapshot(self, keys):
        # Exact keys only; no key enumeration or production transport.
        return [self.client.command(['EVAL', "local raw = redis.call('DUMP', KEYS[1]); return raw and redis.sha1hex(raw) or false", 1, key]) for key in keys]

    def test_owner_only_all_states_exact_sources_and_no_content_or_guests(self):
        for index, state in enumerate(('needs_review', 'needs_action', 'note_only', 'resolved')):
            source = {'provider': 'custom_imap', 'folder': 'INBOX', 'uidValidity': '18446744073709551615', 'imapUid': str(42 + index)} if index % 2 else {
                'provider': 'google', 'providerMessageId': f'Google-Exact-ID_{index}'}
            value = thread(index, state=state, sourceRef=source)
            self.create(value)
        self.commands.clear()
        result = self.page()
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(self.commands[0][0], 'EVAL')
        self.assertIn("redis.call('MGET', unpack(keys))", self.commands[0][1])
        self.assertEqual([entry['state'] for entry in result['summaries']], ['needs_review', 'needs_action', 'note_only', 'resolved'])
        for index, entry in enumerate(result['summaries']):
            self.assertEqual(set(entry), {'collaborationId', 'workspaceId', 'mailboxId', 'sourceRef', 'state', 'updatedAt', 'viewerAccess'})
            self.assertEqual(entry['viewerAccess'], 'owner')
            canonical = store._load_v2_thread(entry['collaborationId'], command_transport=self.transport).record
            self.assertEqual(entry['sourceRef'], canonical['sourceRef'])
        encoded = json.dumps(result)
        for private in ('Internal history', 'Shared history', 'bodyText', 'participants', 'ownerEmail', 'externalGuests', 'token', 'session'):
            self.assertNotIn(private, encoded)

    def test_create_with_guest_empty_participants_owner_discovery(self):
        value = thread()
        invite = {**fixtures.invite_record(), 'workspaceId': WORKSPACE, 'mailboxId': http.MAILBOX_ID}
        result = store._create_v2_thread_with_guest(value, invite, now=fixtures.SEC + 100, command_transport=self.transport)
        self.assertEqual(result.get('status'), 'ok', result)
        self.assertTrue(result.thread_created)
        self.commands.clear()
        self.assertEqual(self.page()['summaries'][0]['collaborationId'], value['collaborationId'])
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(self.page(PARTICIPANT, membership_ref='tinv_original')['summaries'], [])

    def test_participant_add_duplicate_and_live_team_denials(self):
        value = thread()
        self.create(value)
        person = {'userId': PARTICIPANT, 'displayName': 'Participant', 'membershipRef': 'tinv_original'}
        cap = replace(capability(value), action='manage_participants')
        for _ in range(2):
            result = mutations.add_v2_participant(cap, person, command_transport=self.transport)
            self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(self.client.command(['HLEN', self.index(PARTICIPANT)]), 1)
        self.assertEqual(self.page()['summaries'][0]['viewerAccess'], 'owner')
        self.assertEqual(self.page(PARTICIPANT, membership_ref='tinv_original')['summaries'][0]['viewerAccess'], 'participant')
        for user, ref in ((OTHER, 'tinv_original'), (PARTICIPANT, None), (PARTICIPANT, 'tinv_reinvited')):
            self.assertEqual(self.page(user, membership_ref=ref)['summaries'], [])
        # A copied index is never authorization, even with current Team membership.
        raw = self.client.command(['HGET', self.index(PARTICIPANT), value['collaborationId']])
        for index in (self.index(OTHER), self.index(PARTICIPANT, fixtures.OTHER_WORKSPACE_ID)):
            self.client.command(['HSET', index, value['collaborationId'], raw])
            self.client.command(['EXPIRE', index, 1000])
        self.assertEqual(self.page(OTHER, membership_ref='tinv_original')['summaries'], [])
        self.assertEqual(self.page(PARTICIPANT, workspace=fixtures.OTHER_WORKSPACE_ID, membership_ref='tinv_original')['summaries'], [])
        self.assertEqual(self.page(email='wrong@example.com')['summaries'], [])

    def test_resolve_reopen_and_cas_keep_one_reference_and_canonical_state(self):
        value = thread()
        self.create(value)
        keys = [store.build_v2_thread_key(value['collaborationId']), self.index()]
        stale_original = copy.deepcopy(value)
        for operation, expected in (('resolve', 'resolved'), ('reopen', 'note_only'), ('resolve', 'resolved')):
            result = mutations.transition_v2_lifecycle(replace(capability(value), action=operation), operation=operation,
                expected_state=value['state'], expected_updated_at=value['updatedAt'], command_transport=self.transport)
            self.assertEqual(result['status'], 'ok', result)
            self.assertTrue(result['changed'])
            self.assertEqual(self.page()['summaries'][0]['state'], expected)
            value = result['record']
            before = self.snapshot(keys)
            again = mutations.transition_v2_lifecycle(replace(capability(value), action=operation), operation=operation,
                expected_state=value['state'], expected_updated_at=value['updatedAt'], command_transport=self.transport)
            self.assertFalse(again['changed'])
            self.assertEqual(self.snapshot(keys), before)
            self.assertEqual(self.client.command(['HLEN', self.index()]), 1)
        before = self.snapshot(keys)
        rejected = store._transition_v2_lifecycle_if_expected({**value, 'state': 'note_only', 'updatedAt': value['updatedAt'] + 1},
            stale_original['updatedAt'], expected_state='resolved', operation='reopen', owner_user_id=OWNER, command_transport=self.transport)
        self.assertEqual(rejected['error']['code'], 'stale_thread')
        self.assertEqual(self.snapshot(keys), before)

    def test_pages_are_bounded_complete_and_stable_across_lifecycle_changes(self):
        for index in reversed(range(57)):
            self.create(thread(index))
        first = self.page()
        self.assertEqual(len(first['summaries']), 50)
        self.assertEqual(first['nextCursor'], f'{49:022d}')
        filtered = self.page(email='wrong@example.com')
        self.assertEqual(filtered['summaries'], [])
        self.assertEqual(filtered['nextCursor'], first['nextCursor'])
        second = self.page(cursor=first['nextCursor'])
        self.assertEqual(len(second['summaries']), 7)
        self.assertIsNone(second['nextCursor'])
        self.assertEqual([v['collaborationId'] for v in first['summaries'] + second['summaries']], [f'{i:022d}' for i in range(57)])
        self.assertEqual(self.page(cursor=f'{999:022d}')['summaries'], [])

    def test_full_page_large_threads_is_one_bounded_content_free_response(self):
        for index in range(models.MAX_V2_SUMMARY_PAGE_SIZE):
            value = thread(index)
            value['sourceMessage']['bodyText'] = 'b' * models.MAX_V2_SOURCE_BODY
            value['messages'] = [fixtures.message_record(index=i, created_at=value['updatedAt'], text='m' * 16_000)
                                 for i in range(7)]
            self.create(value)
        self.commands.clear()
        started = time.monotonic()
        page = self.page()
        self.assertEqual(len(page['summaries']), models.MAX_V2_SUMMARY_PAGE_SIZE)
        self.assertEqual(len(self.commands), 1)
        self.assertLess(len(json.dumps(page)), 131_072)
        print(f'Local Redis large-page check: {time.monotonic() - started:.3f}s, {len(json.dumps(page))} response bytes')

    def fill_live_capacity(self, user):
        binding = json.dumps({'ownerUserId': OWNER, 'mailboxId': http.MAILBOX_ID,
                              'sourceRef': {'provider': 'google', 'providerMessageId': 'fixture'}, 'threadHash': 'f' * 40})
        fields, records = [], []
        for index in range(models.MAX_V2_DISCOVERY_ENTRIES):
            id = f'{index:022d}'
            fields.extend((id, binding))
            records.extend((store.build_v2_thread_key(id), 'live-fixture'))
        self.client.command(['MSET', *records])
        self.client.command(['HSET', self.index(user), *fields])
        self.client.command(['EXPIRE', self.index(user), 1000])

    def test_capacity_never_evicts_live_entries_and_create_has_zero_writes(self):
        self.fill_live_capacity(OWNER)
        value = thread()
        keys = [self.index(), store.build_v2_thread_key(value['collaborationId']),
                store.build_v2_source_thread_key(value['ownerEmail'], value['mailboxId'], value['sourceRef'])]
        before = self.snapshot(keys)
        result = store._create_v2_thread(value, command_transport=self.transport)
        self.assertEqual(result, {'status': 'conflict', 'error': {'code': 'discovery_capacity_reached'}})
        self.assertEqual(self.snapshot(keys), before)
        self.assertEqual(self.client.command(['HLEN', self.index()]), 1000)
        # One authoritative missing key permits bounded capacity repair.
        self.client.command(['DEL', store.build_v2_thread_key(f'{0:022d}')])
        self.create(value)
        self.assertEqual(self.client.command(['HLEN', self.index()]), 1000)
        self.assertIsNone(self.client.command(['HGET', self.index(), f'{0:022d}']))

    def test_participant_capacity_failure_has_no_partial_owner_or_participant_write(self):
        value = thread()
        self.create(value)
        self.fill_live_capacity(PARTICIPANT)
        keys = [self.index(), self.index(PARTICIPANT), store.build_v2_thread_key(value['collaborationId'])]
        before = self.snapshot(keys)
        result = mutations.add_v2_participant(replace(capability(value), action='manage_participants'),
            {'userId': PARTICIPANT, 'displayName': 'Participant', 'membershipRef': 'tinv_original'}, command_transport=self.transport)
        self.assertEqual(result['error']['code'], 'discovery_capacity_reached')
        self.assertEqual(self.snapshot(keys), before)

    def test_lazy_capacity_repair_that_removes_entire_hash_restores_bounded_ttl(self):
        value = {k: v for k, v in thread().items() if k not in {'ownerUserId', 'ownerDisplayName', 'participants'}}
        self.create(value)
        self.fill_live_capacity(OWNER)
        self.client.command(['DEL', *[store.build_v2_thread_key(f'{i:022d}') for i in range(1000)]])
        self.client.command(['PEXPIRE', store.build_v2_thread_key(value['collaborationId']), 60_000])
        result = store._enrich_v2_discovery(value, capability(value), command_transport=self.transport)
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(self.client.command(['HLEN', self.index()]), 1)
        self.assertGreater(self.client.command(['PTTL', self.index()]), 0)
        self.assertLessEqual(self.client.command(['PTTL', self.index()]), 60_000)
        self.assertEqual(len(self.page()['summaries']), 1)

    def test_corrupt_secondary_index_aborts_create_with_guest_before_any_write(self):
        value = thread()
        self.client.command(['SET', self.index(), 'wrong-type', 'EX', 1000])
        invite = {**fixtures.invite_record(), 'workspaceId': WORKSPACE, 'mailboxId': value['mailboxId']}
        keys = [self.index(), store.build_v2_thread_key(value['collaborationId']), store.build_v2_invite_key(invite['inviteId']),
                store.build_v2_source_thread_key(value['ownerEmail'], value['mailboxId'], value['sourceRef']),
                store.build_v2_external_guest_index_key(value['collaborationId'])]
        before = self.snapshot(keys)
        result = store._create_v2_thread_with_guest(value, invite, now=fixtures.SEC + 100, command_transport=self.transport)
        self.assertNotEqual(result.get('status'), 'ok')
        self.assertEqual(self.snapshot(keys), before)

    def test_source_scope_corruption_and_malformed_threads_fail_closed_without_index_writes(self):
        value = thread()
        self.create(value)
        key = store.build_v2_thread_key(value['collaborationId'])
        cases = [{'mailboxId': 'wrong-mailbox'}, {'sourceRef': {'provider': 'google', 'providerMessageId': 'wrong'}},
                 {'workspaceId': fixtures.OTHER_WORKSPACE_ID}, {'collaborationId': 'B' * 22}, {'ownerUserId': OTHER},
                 {'sourceRef': {'provider': 'custom_imap', 'folder': 'Trash', 'uidValidity': '1', 'imapUid': '2'}}]
        for changes in cases:
            raw = models.encode_v2_wire_record({**value, **changes}, 'thread')
            if raw is None:
                raw = models.encode_v2_wire_record(value, 'thread')
                raw.update(changes)
            self.client.command(['SET', key, json.dumps(raw), 'EX', 1000])
            before = self.snapshot([self.index()])
            self.assertEqual(self.page()['summaries'], [])
            self.assertEqual(self.snapshot([self.index()]), before)
        self.client.command(['SET', key, fixtures.wire_json(value, 'thread').replace('"participants":[]', '"participants":{}'), 'EX', 1000])
        self.assertEqual(self.page()['summaries'], [])
        self.client.command(['DEL', key])
        self.client.command(['HSET', key, 'corrupt', 'value'])
        before = self.snapshot([self.index()])
        self.assertEqual(self.page()['summaries'], [])
        self.assertEqual(self.snapshot([self.index()]), before)

    def test_expiry_prunes_only_missing_authority_and_never_extends_thread_ttl(self):
        value = thread()
        self.create(value)
        key = store.build_v2_thread_key(value['collaborationId'])
        self.assertLessEqual(self.client.command(['PTTL', self.index()]), store.V2_THREAD_RETENTION_SECONDS * 1000)
        self.client.command(['PEXPIRE', key, 1000])
        before = self.client.command(['PTTL', key])
        self.page()
        self.assertLessEqual(self.client.command(['PTTL', key]), before)
        self.client.command(['PEXPIRE', key, 1])
        time.sleep(.005)
        self.assertEqual(self.page()['summaries'], [])
        self.assertEqual(self.client.command(['EXISTS', self.index()]), 0)

    def test_exact_legacy_enrichment_preserves_content_and_expiration_and_is_idempotent(self):
        value = {k: v for k, v in thread().items() if k not in {'ownerUserId', 'ownerDisplayName', 'participants'}}
        self.create(value)
        key = store.build_v2_thread_key(value['collaborationId'])
        self.assertEqual(self.page()['summaries'], [])
        self.assertEqual(store._load_v2_thread(value['collaborationId'], command_transport=self.transport).record, value)
        self.client.command(['PEXPIRE', key, 100_000])
        before = self.client.command(['PTTL', key])
        result = store._enrich_v2_discovery(value, capability(value), command_transport=self.transport)
        self.assertEqual(result, {'status': 'ok', 'error': None})
        self.assertLessEqual(self.client.command(['PTTL', key]), before)
        self.assertLessEqual(self.client.command(['PTTL', self.index()]), before)
        current = store._load_v2_thread(value['collaborationId'], command_transport=self.transport).record
        self.assertEqual({k: current[k] for k in value}, value)
        self.assertEqual(current['ownerUserId'], OWNER)
        self.assertEqual(current['participants'], [])
        self.assertEqual(len(self.page()['summaries']), 1)
        snapshots = self.snapshot([key, self.index()])
        expiry = self.client.command(['PTTL', self.index()])
        self.assertEqual(store._enrich_v2_discovery(current, capability(value), command_transport=self.transport)['status'], 'ok')
        self.assertEqual(self.snapshot([key, self.index()]), snapshots)
        self.assertLessEqual(self.client.command(['PTTL', self.index()]), expiry)

    def test_enrichment_wrong_owner_scope_corruption_and_stale_cas_have_zero_writes(self):
        value = thread()
        self.create(value)
        self.client.command(['DEL', self.index()])
        key = store.build_v2_thread_key(value['collaborationId'])
        before = self.snapshot([key, self.index()])
        for changes in ({'owner_user_id': OTHER, 'actor_user_id': OTHER}, {'owner_email': 'wrong@example.com'},
                        {'viewer_access': 'participant'}, {'workspace_id': fixtures.OTHER_WORKSPACE_ID}):
            result = store._enrich_v2_discovery(value, replace(capability(value), **changes), command_transport=self.transport)
            self.assertNotEqual(result['status'], 'ok')
            self.assertEqual(self.snapshot([key, self.index()]), before)
        stale = {**value, 'updatedAt': value['updatedAt'] - 1, 'createdAt': value['createdAt'] - 1}
        self.assertEqual(store._enrich_v2_discovery(stale, capability(value), command_transport=self.transport)['error']['code'], 'stale_thread')
        self.assertEqual(self.snapshot([key, self.index()]), before)
        raw = fixtures.wire_json(value, 'thread').replace('"participants":[]', '"participants":{}')
        self.client.command(['SET', key, raw, 'EX', 1000])
        before = self.snapshot([key, self.index()])
        self.assertNotEqual(store._enrich_v2_discovery(value, capability(value), command_transport=self.transport)['status'], 'ok')
        self.assertEqual(self.snapshot([key, self.index()]), before)

    def test_http_application_authorization_and_real_redis_batch(self):
        for index in range(4):
            self.create(thread(index))
        participant_thread = thread(4, ownerUserId=OTHER, ownerEmail='other@example.com', participants=[
            {'userId': OWNER, 'membershipRef': 'tinv_original', 'displayName': 'Owner Person'}])
        self.create(participant_thread)
        context, environment = http._context(), http._environment()
        configuration = http.parse_owner_security_configuration(owner_http._trusted_security_snapshot(environment))
        csrf = http.issue_owner_csrf_token(context, configuration, now=http.NOW)[0]
        member = AuthenticatedMemberContext(OWNER, http.OWNER_EMAIL, 'Owner Person', WORKSPACE, 'owner')
        team = Mock(return_value=({'memberUserId': OWNER, 'sourceInvitationId': 'tinv_original'}, None))
        account = Mock(return_value=(member, None))
        real_list = store._list_v2_summaries
        with (patch.object(owner_http, '_resolve_context', return_value=context), patch.object(
            owner_rate_limit, 'consume_owner_rate_limit', return_value=owner_rate_limit.OwnerRateLimitDecision('allowed')),
            patch.object(authorization, '_resolve_current_authenticated_member', account), patch.object(authorization, '_resolve_active_team_member', team),
            patch.object(application, '_list_v2_summaries', side_effect=lambda *a, **kw: real_list(*a, **kw, command_transport=self.transport)),
            patch.object(application, '_load_v2_external_guest_records', side_effect=AssertionError('no guests')),
            patch.object(application, '_build_verified_owner_thread_dto', side_effect=AssertionError('no DTOs'))):
            self.commands.clear()
            response = http._invoke(http._request({'operation': 'list_summaries', 'cursor': None}, csrf=csrf))
        self.assertEqual(response.status, 200, response.body)
        data = http._json(response)['data']
        self.assertEqual(len(data['summaries']), 5)
        self.assertEqual(data['summaries'][-1]['viewerAccess'], 'participant')
        self.assertEqual(len(self.commands), 1)
        account.assert_called_once()
        team.assert_called_once_with(WORKSPACE, OWNER)

    def test_owner_appends_advance_digest_and_summary_but_retries_do_not_rewrite(self):
        value = thread()
        self.create(value)
        for action, visibility in (('reply', 'shared'), ('internal_note', 'internal')):
            cap = replace(capability(value), action=action)
            token = base64.urlsafe_b64encode((b's' if visibility == 'shared' else b'i') * 32).decode().rstrip('=')
            before = self.snapshot([self.index()])
            result = mutations.append_owner_v2_message_idempotently(cap, 'Private ' + visibility,
                visibility=visibility, idempotency_key=token, command_transport=self.transport)
            self.assertEqual(result['status'], 'ok', result)
            self.assertNotEqual(self.snapshot([self.index()]), before)
            current = store._load_v2_thread(value['collaborationId'], command_transport=self.transport).record
            self.assertEqual(self.page()['summaries'][0]['updatedAt'], current['updatedAt'])
            before = self.snapshot([self.index()])
            retry = mutations.append_owner_v2_message_idempotently(cap, 'Private ' + visibility,
                visibility=visibility, idempotency_key=token, command_transport=self.transport)
            self.assertEqual(retry['status'], 'ok', retry)
            self.assertEqual(self.snapshot([self.index()]), before)

    def test_authenticated_exact_source_lookup_enriches_google_and_imap_legacy_threads(self):
        for index, source in enumerate(({'provider': 'google', 'providerMessageId': 'exact'},
                                       {'provider': 'custom_imap', 'folder': 'INBOX', 'uidValidity': '91', 'imapUid': '17'})):
            value = {k: v for k, v in thread(index, sourceRef=source).items()
                     if k not in {'ownerUserId', 'ownerDisplayName', 'participants'}}
            self.create(value)
            cap = replace(capability(value), collaboration_id=None)
            with (patch.object(application, 'resolve_verified_owner_collaboration_context', return_value={'status': 'ok', 'context': cap, 'error': None}),
                  patch.object(application, '_load_v2_thread_by_source', side_effect=lambda *a, **kw: store._load_v2_thread_by_source(*a, **kw, command_transport=self.transport)),
                  patch.object(application, '_enrich_v2_discovery', side_effect=lambda *a: store._enrich_v2_discovery(*a, command_transport=self.transport))):
                result = application.lookup_v2_collaboration_for_verified_owner(object(), (), value['mailboxId'],
                    {k: v for k, v in source.items() if k != 'provider'}, owner_security_configuration=object())
            self.assertEqual(result, {'status': 'ok', 'collaborationId': value['collaborationId'], 'error': None})
        self.assertEqual([s['sourceRef'] for s in self.page()['summaries']], [
            {'provider': 'google', 'providerMessageId': 'exact'},
            {'provider': 'custom_imap', 'folder': 'INBOX', 'uidValidity': '91', 'imapUid': '17'}])


class SummaryContractTests(unittest.TestCase):
    def test_http_summary_bounds_security_read_mode_and_fail_closed_response(self):
        context = http._context()
        config = http.parse_owner_security_configuration(owner_http._trusted_security_snapshot(http._environment()))
        csrf = http.issue_owner_csrf_token(context, config, now=http.NOW)[0]
        page = {'v': 1, 'workspaceId': WORKSPACE, 'summaries': [], 'nextCursor': None}
        with (patch.object(owner_http, '_resolve_context', return_value=context),
              patch.object(owner_rate_limit, 'consume_owner_rate_limit', return_value=owner_rate_limit.OwnerRateLimitDecision('allowed')),
              patch.object(application, 'list_v2_summaries_for_verified_owner', return_value={'status': 'ok', 'page': page, 'error': None}) as listing):
            response = http._invoke(http._request({'operation': 'list_summaries', 'cursor': None}, csrf=csrf), mode='owner_read')
            self.assertEqual(response.status, 200, response.body)
            self.assertEqual(http._json(response)['data'], page)
            listing.reset_mock()
            for payload in ({'operation': 'list_summaries'}, {'operation': 'list_summaries', 'cursor': 'bad'},
                            {'operation': 'list_summaries', 'cursor': None, 'workspaceId': WORKSPACE},
                            {'operation': 'list_summaries', 'cursor': None, 'limit': '999'}):
                self.assertEqual(http._invoke(http._request(payload, csrf=csrf)).status, 400)
            listing.assert_not_called()
            self.assertNotEqual(http._invoke(http._request({'operation': 'list_summaries', 'cursor': None})).status, 200)
            listing.assert_not_called()
            listing.return_value = {'status': 'ok', 'page': {**page, 'workspaceId': fixtures.OTHER_WORKSPACE_ID}, 'error': None}
            self.assertNotEqual(http._invoke(http._request({'operation': 'list_summaries', 'cursor': None}, csrf=csrf)).status, 200)
        with patch.object(owner_http, '_resolve_context', side_effect=http.OwnerSecurityError('authentication_required')):
            self.assertEqual(http._invoke(http._request({'operation': 'list_summaries', 'cursor': None}, csrf=csrf)).status, 401)

    def test_actual_team_snapshot_uses_two_exact_reads_and_rejects_removed_or_rebound_members(self):
        from api.team import authority as team
        member = AuthenticatedMemberContext(OWNER, http.OWNER_EMAIL, 'Owner Person', WORKSPACE, 'owner')
        context = http._context()
        config = http.parse_owner_security_configuration(owner_http._trusted_security_snapshot(http._environment()))
        record = {'v': team.TEAM_AUTHORITY_SCHEMA_VERSION, 'workspaceId': WORKSPACE, 'email': member.email,
                  'verifiedRecipientEmail': member.email, 'memberUserId': OWNER, 'displayName': member.name,
                  'accessLevel': 'Shared', 'status': 'active', 'sourceInvitationId': 'tinv_' + 'I' * 22,
                  'createdAt': 100, 'updatedAt': 101, 'acceptedAt': 101}
        pointer = team._build_member_user_pointer(record)
        self.assertIsNotNone(pointer)
        for changes, expected in (({}, 'ok'), ({'status': 'removed'}, 'ok'),
                                  ({'memberUserId': OTHER}, 'unavailable'), ({'workspaceId': fixtures.OTHER_WORKSPACE_ID}, 'unavailable')):
            records = {team._member_user_pointer_key(WORKSPACE, OWNER): json.dumps(pointer),
                       team._member_key(WORKSPACE, member.email): json.dumps({**record, **changes})}
            transport = Mock(side_effect=lambda command: {'result': records[command[1]]})
            authority = team.RuntimeTeamAuthority(transport, environment={})
            with patch.object(team, 'build_runtime_team_authority', return_value=authority):
                result = authorization.resolve_verified_summary_viewer(context, (), owner_security_configuration=config,
                    member_resolver=lambda _: (member, None))
            self.assertEqual(result['status'], expected, result)
            self.assertEqual([call.args[0] for call in transport.call_args_list], [['GET', key] for key in records])
            if changes.get('status') == 'removed': self.assertIsNone(result['membershipRef'])

    def test_model_rejects_unknown_fields_states_ids_sources_workspace_and_coercion(self):
        value = {k: v for k, v in thread().items() if k in {'collaborationId', 'workspaceId', 'mailboxId', 'sourceRef', 'state', 'updatedAt'}}
        value['viewerAccess'] = 'owner'
        self.assertEqual(models.normalize_v2_summary(value, workspace_id=WORKSPACE), value)
        for changes in ({'state': []}, {'state': 'active'}, {'collaborationId': 'short'}, {'mailboxId': 'UPPER'},
                        {'workspaceId': fixtures.OTHER_WORKSPACE_ID}, {'updatedAt': str(value['updatedAt'])},
                        {'updatedAt': True}, {'sourceRef': {'provider': 'google', 'providerMessageId': 'x', 'extra': 'y'}},
                        {'sourceRef': {'provider': 'custom_imap', 'folder': 'INBOX', 'uidValidity': '01', 'imapUid': '2'}},
                        {'messages': []}, {'viewerAccess': 'guest'}):
            self.assertIsNone(models.normalize_v2_summary({**value, **changes}, workspace_id=WORKSPACE))
        page = {'v': 1, 'workspaceId': WORKSPACE, 'summaries': [value], 'nextCursor': None}
        self.assertEqual(models.normalize_v2_summary_page(page, workspace_id=WORKSPACE), page)
        for changes in ({'v': True}, {'v': '1'}, {'summaries': [value, value]}, {'summaries': [value] * 51}, {'nextCursor': 'bad'}):
            self.assertIsNone(models.normalize_v2_summary_page({**page, **changes}, workspace_id=WORKSPACE))

    def test_summary_request_rejects_invalid_cursor_before_transport(self):
        for cursor in ('short', [], 1, True, 'A' * 22 + '\n'):
            transport = Mock()
            self.assertNotEqual(store._list_v2_summaries(WORKSPACE, OWNER, http.OWNER_EMAIL, membership_ref=None,
                                                       cursor=cursor, command_transport=transport)['status'], 'ok')
            transport.assert_not_called()

    def test_verified_viewer_rejects_cross_workspace_user_and_stale_membership(self):
        context = http._context()
        config = http.parse_owner_security_configuration(owner_http._trusted_security_snapshot(http._environment()))
        member = AuthenticatedMemberContext(OWNER, http.OWNER_EMAIL, 'Owner Person', WORKSPACE, 'owner')
        for changes in ({'workspace_id': fixtures.OTHER_WORKSPACE_ID}, {'email': 'wrong@example.com'}, {'auth_source': 'legacy'}, {'user_type': 'guest'}):
            team = Mock()
            result = authorization.resolve_verified_summary_viewer(context, (), owner_security_configuration=config,
                member_resolver=lambda _: (replace(member, **changes), None), team_member_resolver=team)
            self.assertNotEqual(result['status'], 'ok')
            team.assert_not_called()
        for membership, error, expected in ((None, 'not_active', 'ok'), (None, 'unavailable', 'unavailable'),
                                            ({'memberUserId': OTHER, 'sourceInvitationId': 'tinv_original'}, None, 'unavailable')):
            result = authorization.resolve_verified_summary_viewer(context, (), owner_security_configuration=config,
                member_resolver=lambda _: (member, None), team_member_resolver=lambda *_: (membership, error))
            self.assertEqual(result['status'], expected)
            if expected == 'ok': self.assertIsNone(result['membershipRef'])
