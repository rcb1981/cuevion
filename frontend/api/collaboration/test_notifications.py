"""C3C atomic emission tests against disposable local Redis, never hosted Redis."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from . import authorization, guest_session, models, mutations, redis_store
from . import test_lua_redis_integration as harness
from api.notifications import store

OWNER = 'usr_' + 'A' * 22
FIRST = 'usr_' + 'B' * 21 + 'A'
SECOND = 'usr_' + 'C' * 21 + 'A'
WORKSPACE = harness.WORKSPACE_ID


def logical_key(value):
    return base64.urlsafe_b64encode(hashlib.sha256(str(value).encode()).digest()).rstrip(b'=').decode()


class NotificationMutationRedisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        harness.ProductionLuaRedisIntegrationTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        harness.ProductionLuaRedisIntegrationTests.tearDownClass.__func__(cls)

    def setUp(self):
        self.client.command(['FLUSHALL'])
        self.environment = patch.dict(os.environ, {
            redis_store.V2_INDEX_HMAC_ENV: logical_key('index'),
            redis_store.V2_INDEX_HMAC_PREVIOUS_ENV: '',
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.transport = self.client.transport
        self.now = int(time.time())
        self.ms = self.now * 1000
        self.membership = patch.object(authorization, '_resolve_active_team_member', side_effect=lambda _workspace, user: (
            {'memberUserId': user, 'sourceInvitationId': 'tinv_' + user[-22:]}, None))
        self.membership.start()
        self.addCleanup(self.membership.stop)

    def thread(self, participants=(FIRST,)):
        return {**harness.thread_record(), 'createdAt': self.ms, 'updatedAt': self.ms,
                'ownerUserId': OWNER, 'ownerDisplayName': 'Owner',
                'participants': [{'userId': user, 'displayName': 'Teammate',
                                  'membershipRef': 'tinv_' + user[-22:]} for user in participants]}

    def create(self, participants=(FIRST,)):
        thread = self.thread(participants)
        result = redis_store._create_v2_thread(thread, command_transport=self.transport)
        self.assertEqual(result.get('status'), 'ok', result)
        return thread

    def capability(self, actor=OWNER, action='reply'):
        return authorization._InternalCollaborationCapability(
            authorization._INTERNAL_CAPABILITY_SENTINEL, 'owner@example.com', WORKSPACE,
            'mailbox-1', 'google', 'A' * 22, action,
            'owner' if actor == OWNER else 'internal', 'Owner' if actor == OWNER else 'Teammate',
            actor, 'owner' if actor == OWNER else 'participant', OWNER, 'Owner')

    def append(self, actor=OWNER, visibility='shared', key='one', transport=None):
        return mutations.append_owner_v2_message_idempotently(
            self.capability(actor, 'reply' if visibility == 'shared' else 'internal_note'),
            'private body @name is ordinary text', visibility=visibility,
            idempotency_key=logical_key(key), command_transport=transport or self.transport)

    def notifications(self, user):
        result = store.list_notifications(WORKSPACE, user, command_transport=self.transport)
        self.assertEqual(result.get('status'), 'ok', result)
        return result['notifications']

    def guest(self):
        session = {**harness.session_record('g' * 43), 'createdAt': self.now - 10,
                   'lastUsedAt': self.now - 10, 'expiresAt': self.now + 200}
        invite = {**harness.invite_record(), 'createdAt': self.now - 20,
                  'expiresAt': self.now + 300, 'exchangedAt': self.now - 10,
                  'exchangeCount': 1, 'status': 'exchanged',
                  'activeSessionHash': session['sessionHash']}
        for key, record, kind in (
            (redis_store.build_v2_invite_key(invite['inviteId']), invite, 'invite'),
            (redis_store.build_v2_guest_session_key(session['sessionHash']), session, 'session'),
        ):
            self.client.command(['SET', key, harness.wire_json(record, kind), 'EX', 180])
        return guest_session._GuestMutationCapability(
            guest_session._GUEST_MUTATION_SENTINEL, session['sessionHash'], session['inviteId'],
            session['ownerEmail'], WORKSPACE, session['mailboxId'], session['collaborationId'],
            session['guestDisplayName'], session['expiresAt'], session['createdAt'], session['lastUsedAt'])

    def guest_reply(self, guest, key='guest-one', text='Guest private body', transport=None):
        return mutations.append_guest_v2_reply(guest, text, idempotency_key=logical_key(key),
                                               command_transport=transport or self.transport)

    def test_create_start_owner_suppression_and_replay(self):
        thread = self.create()
        rows = self.notifications(FIRST)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kind'], 'collaboration_started')
        self.assertIsNone(rows[0]['activityId'])
        self.assertEqual(self.notifications(OWNER), [])
        result = redis_store._create_v2_thread(thread, command_transport=self.transport)
        self.assertFalse(result.created)
        self.assertEqual(self.notifications(FIRST), rows)

    def test_owner_only_create_has_zero_notifications(self):
        self.create(())
        self.assertEqual(self.notifications(OWNER), [])

    def test_imap_notification_routing_is_exact(self):
        source = {'provider': 'custom_imap', 'folder': 'INBOX', 'uidValidity': '77', 'imapUid': '99'}
        result = redis_store._create_v2_thread({**self.thread(), 'sourceRef': source}, command_transport=self.transport)
        self.assertEqual(result.get('status'), 'ok', result)
        row = self.notifications(FIRST)[0]
        self.assertEqual(row['sourceRef'], source)
        self.assertEqual((row['workspaceId'], row['mailboxId'], row['collaborationId']),
                         (WORKSPACE, 'mailbox-1', 'A'*22))

    def test_guest_only_create_has_zero_notifications(self):
        thread = self.thread(())
        invite = {**harness.invite_record(), 'createdAt': self.now,
                  'expiresAt': self.now + 86400}
        result = redis_store._create_v2_thread_with_guest(thread, invite, now=self.now,
                                                         command_transport=self.transport)
        self.assertEqual(result.get('status'), 'ok', result)
        self.assertEqual(self.notifications(OWNER), [])

    def test_owner_and_participant_shared_internal_matrix(self):
        self.create((FIRST, SECOND))
        for actor, visibility in ((OWNER, 'shared'), (FIRST, 'shared'), (OWNER, 'internal'), (FIRST, 'internal')):
            before = {u: len(self.notifications(u)) for u in (OWNER, FIRST, SECOND)}
            result = self.append(actor, visibility, key=actor + visibility)
            self.assertEqual(result['status'], 'ok', result)
            for user in (OWNER, FIRST, SECOND):
                rows = self.notifications(user)
                self.assertEqual(len(rows), before[user] + int(user != actor))
                if user != actor:
                    row = next(row for row in rows if row['activityId'] == result['message']['id'])
                    self.assertEqual(row['kind'], 'shared_message' if visibility == 'shared' else 'internal_note')
                    self.assertEqual(row['actor']['userId'], actor)
                    self.assertNotIn('recipientUserId', row)
                    self.assertNotIn('private body', json.dumps(row))
                    self.assertNotIn('mention', row['kind'])

    def test_owner_retry_preserves_message_notifications_read_state_and_expiry(self):
        thread = self.create()
        thread_key = redis_store.build_v2_thread_key(thread['collaborationId'])
        source_key = redis_store.build_v2_source_thread_key(thread['ownerEmail'], thread['mailboxId'], thread['sourceRef'])
        for key in (thread_key, source_key):
            self.client.command(['PEXPIRE', key, 40000])
        before = [self.client.command(['PTTL', key]) for key in (thread_key, source_key)]
        first = self.append()
        self.assertEqual(first['status'], 'ok', first)
        row = self.notifications(FIRST)[0]
        marked = store.mark_read(WORKSPACE, FIRST, row['notificationId'], command_transport=self.transport)
        self.assertEqual(marked['status'], 'ok')
        rows = self.notifications(FIRST)
        retry = self.append()
        self.assertEqual(first, retry)
        self.assertEqual(self.notifications(FIRST), rows)
        for key, prior in zip((thread_key, source_key), before):
            self.assertLessEqual(self.client.command(['PTTL', key]), prior)
        self.assertLessEqual(row['expiresAt'], int(time.time() * 1000) + 40000)
        self.assertLessEqual(row['expiresAt'], row['createdAt'] + 180 * 86400000)

    def test_participant_add_single_recipient_and_repeat(self):
        self.create(())
        participant = self.thread()['participants'][0]
        first = mutations.add_v2_participant(self.capability(action='manage_participants'), participant,
                                             command_transport=self.transport)
        self.assertTrue(first['changed'], first)
        rows = self.notifications(FIRST)
        self.assertEqual([row['kind'] for row in rows], ['participant_added'])
        self.assertEqual(self.notifications(OWNER), [])
        retry = mutations.add_v2_participant(self.capability(action='manage_participants'), participant,
                                             command_transport=self.transport)
        self.assertFalse(retry['changed'])
        self.assertEqual(self.notifications(FIRST), rows)

    def test_guest_and_participant_add_preserve_exact_expiry(self):
        self.create(())
        guest = self.guest()
        thread = self.thread(())
        keys = [redis_store.build_v2_thread_key('A'*22), redis_store.build_v2_source_thread_key(
            thread['ownerEmail'], thread['mailboxId'], thread['sourceRef'])]
        for key in keys:
            self.client.command(['PEXPIRE', key, 60000])
        expiry = [self.client.command(['PEXPIRETIME', key]) for key in keys]
        self.assertEqual(self.guest_reply(guest)['status'], 'ok')
        self.assertEqual(self.guest_reply(guest)['status'], 'ok')
        added = mutations.add_v2_participant(self.capability(action='manage_participants'),
            self.thread()['participants'][0], command_transport=self.transport)
        self.assertEqual(added['status'], 'ok')
        self.assertEqual([self.client.command(['PEXPIRETIME', key]) for key in keys], expiry)
        for user in (OWNER, FIRST):
            for row in self.notifications(user):
                self.assertLessEqual(row['expiresAt'], expiry[0])

    def test_resolve_reopen_and_guest_session_records_emit_nothing(self):
        thread = self.create()
        before = self.notifications(FIRST)
        self.guest()
        for operation in ('resolve', 'reopen'):
            result = mutations.transition_v2_lifecycle(self.capability(action=operation),
                operation=operation, expected_state=thread['state'], expected_updated_at=thread['updatedAt'],
                command_transport=self.transport)
            self.assertEqual(result['status'], 'ok', result)
            thread = result['record']
        self.assertEqual(self.notifications(FIRST), before)
        self.assertEqual(self.notifications(OWNER), [])

    def test_guest_reply_replay_new_send_and_internal_privacy(self):
        self.create((FIRST, SECOND))
        guest = self.guest()
        first = self.guest_reply(guest)
        self.assertEqual(first['status'], 'ok', first)
        for user in (OWNER, FIRST, SECOND):
            row = self.notifications(user)[0]
            self.assertEqual(row['actor'], {'type': 'external_guest', 'displayName': 'Guest'})
            self.assertEqual(row['activityId'], first['message']['id'])
        before = {user: self.notifications(user) for user in (OWNER, FIRST, SECOND)}
        self.assertEqual(self.guest_reply(guest), first)
        for user, rows in before.items():
            self.assertEqual(self.notifications(user), rows)
        other = self.guest_reply(guest, key='guest-two')
        self.assertNotEqual(other['message']['id'], first['message']['id'])
        changed = self.guest_reply(guest, text='changed same logical key')
        self.assertEqual(changed['error']['code'], 'idempotency_conflict')
        before_view = models.build_v2_guest_thread_dto(redis_store._load_v2_thread('A'*22, command_transport=self.transport).record)
        self.assertEqual(self.append(visibility='internal')['status'], 'ok')
        after_view = models.build_v2_guest_thread_dto(redis_store._load_v2_thread('A'*22, command_transport=self.transport).record)
        self.assertEqual(before_view['messages'], after_view['messages'])
        self.assertNotIn('notification', json.dumps(after_view))

    def test_guest_invalid_key_and_revoked_retry_fail_closed(self):
        self.create()
        guest = self.guest()
        for invalid in (None, '', 'x', 'B'*43, 'A'*44):
            self.assertEqual(mutations.append_guest_v2_reply(guest, 'text', idempotency_key=invalid,
                command_transport=self.transport)['error']['code'], 'invalid_request')
        self.assertEqual(self.guest_reply(guest)['status'], 'ok')
        self.client.command(['DEL', redis_store.build_v2_guest_session_key(guest.session_hash)])
        self.assertEqual(self.guest_reply(guest)['error']['code'], 'session_revoked')

    def test_guest_concurrent_same_key_has_one_message_and_notification_per_recipient(self):
        self.create()
        guest = self.guest()
        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(lambda _: self.guest_reply(guest), range(4)))
        self.assertTrue(all(result['status'] == 'ok' for result in outcomes), outcomes)
        self.assertEqual(len({result['message']['id'] for result in outcomes}), 1)
        canonical = redis_store._load_v2_thread('A'*22, command_transport=self.transport).record
        self.assertEqual(len(canonical['messages']), 1)
        self.assertEqual(len(self.notifications(OWNER)), 1)
        self.assertEqual(len(self.notifications(FIRST)), 2)

    def test_transport_failure_never_reports_false_success(self):
        self.create()
        result = self.append(transport=lambda _cmd: (_ for _ in ()).throw(ConnectionError('private Redis details')))
        self.assertNotEqual(result['status'], 'ok')
        self.assertNotIn('private Redis details', json.dumps(result))
        self.assertEqual(redis_store._load_v2_thread('A'*22, command_transport=self.transport).record['messages'], [])

    def test_stale_team_grants_receive_no_new_shared_internal_or_guest_events(self):
        self.create((FIRST, SECOND))
        guest = self.guest()
        before = self.notifications(FIRST)
        def membership(_workspace, user):
            return ({'memberUserId': user, 'sourceInvitationId':
                     'tinv_reinvited' if user == FIRST else 'tinv_' + user[-22:]}, None)
        with patch.object(authorization, '_resolve_active_team_member', side_effect=membership):
            self.assertEqual(self.append()['status'], 'ok')
            self.assertEqual(self.append(visibility='internal', key='note')['status'], 'ok')
            self.assertEqual(self.guest_reply(guest)['status'], 'ok')
        self.assertEqual(self.notifications(FIRST), before)
        self.assertEqual(len(self.notifications(SECOND)), 4)
        self.assertEqual(len(self.notifications(OWNER)), 1)

    def test_team_authority_outage_cannot_commit_event_without_notifications(self):
        self.create()
        with patch.object(authorization, '_resolve_active_team_member', return_value=(None, 'unavailable')):
            result = self.append()
        self.assertEqual(result['error']['code'], 'storage_unavailable')
        self.assertEqual(redis_store._load_v2_thread('A'*22, command_transport=self.transport).record['messages'], [])

    def test_create_atomic_wrong_notification_key_type(self):
        keys = store.build_notification_keys(WORKSPACE, FIRST)
        self.client.command(['SET', keys[0], 'corrupt', 'EX', 100])
        result = redis_store._create_v2_thread(self.thread(), command_transport=self.transport)
        self.assertNotEqual(result.get('status'), 'ok')
        self.assertIsNone(self.client.command(['GET', redis_store.build_v2_thread_key('A'*22)]))

    def test_shared_internal_guest_and_add_are_atomic_on_notification_corruption(self):
        for event in ('shared', 'internal', 'guest', 'add'):
            with self.subTest(event=event):
                self.client.command(['FLUSHALL'])
                self.create(() if event == 'add' else (FIRST,))
                guest = self.guest() if event == 'guest' else None
                key = redis_store.build_v2_thread_key('A'*22)
                before = self.client.command(['GET', key])
                keys = store.build_notification_keys(WORKSPACE, FIRST)
                self.client.command(['DEL', *keys])
                self.client.command(['SET', keys[0], 'wrong type', 'EX', 100])
                if event == 'add':
                    result = mutations.add_v2_participant(self.capability(action='manage_participants'),
                        self.thread()['participants'][0], command_transport=self.transport)
                elif event == 'guest':
                    result = self.guest_reply(guest)
                else:
                    result = self.append(visibility=event)
                self.assertNotEqual(result.get('status'), 'ok', result)
                self.assertEqual(self.client.command(['GET', key]), before)


if __name__ == '__main__':
    unittest.main()
