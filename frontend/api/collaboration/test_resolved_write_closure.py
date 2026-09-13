"""Lifecycle closure tests against production Lua in an isolated local Redis.

No Redis URL/environment is consulted. The server has no TCP listener, uses a
private temporary UNIX socket, and cannot persist data. These tests deliberately
interpose Resolve at the EVAL boundary after the caller loaded an active thread.
"""

from __future__ import annotations

import base64
import json
import time
import unittest
from unittest.mock import patch

from api.collaboration import application, authorization, guest_http, guest_session, models, mutations, owner_http, redis_store
from api.notification_service import store as notification_store
from api.collaboration import test_lua_redis_integration as harness


COLLABORATION_ID = "A" * 22
WORKSPACE_ID = "wsp_" + "A" * 22
OWNER_ID = "usr_" + "A" * 22
TEAM_ID = "usr_" + "B" * 21 + "A"
INVITE_ID = "I" * 22
HMAC_KEY = b"resolved-closure-local-hmac-key!!"
SESSION_HASH = "c" * 64
RETENTION_MS = 7_200_000


class ResolvedWriteClosureLuaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        harness.ProductionLuaRedisIntegrationTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        harness.ProductionLuaRedisIntegrationTests.tearDownClass.__func__(cls)

    def command(self, *parts):
        return self.client.command(list(parts))

    def setUp(self):
        self.command("FLUSHDB")  # Only the private server created above.
        self.commands = []
        self.before_eval = None
        self.sequence = 0
        self.hmac_patch = patch.object(redis_store, "resolve_v2_index_hmac_keys", return_value=(HMAC_KEY, None))
        self.hmac_patch.start()
        self.addCleanup(self.hmac_patch.stop)
        self.membership_patch = patch.object(
            authorization, "_resolve_active_team_member",
            return_value=({"memberUserId": TEAM_ID, "sourceInvitationId": "tinv_closure"}, None),
        )
        self.membership_patch.start()
        self.addCleanup(self.membership_patch.stop)
        now = int(time.time())
        self.thread = {
            "v": 2, "collaborationId": COLLABORATION_ID, "ownerEmail": "owner@example.com",
            "workspaceId": WORKSPACE_ID, "mailboxId": "mailbox-1",
            "sourceRef": {"provider": "google", "providerMessageId": "closure-source"},
            "sourceMessage": {"subject": "Closure", "senderDisplay": "Sender",
                              "fromDisplay": "sender@example.com", "timestamp": "1712345678901",
                              "bodyText": "Shared source"},
            "ownerUserId": OWNER_ID, "ownerDisplayName": "Owner Person",
            "participants": [{"userId": TEAM_ID, "displayName": "Team Person", "membershipRef": "tinv_closure"}],
            "state": "needs_review", "messages": [], "createdAt": now * 1000 - 1000,
            "updatedAt": now * 1000,
        }
        self.invite = {
            "v": 2, "inviteId": INVITE_ID, "tokenHash": "a" * 64,
            "ownerEmail": self.thread["ownerEmail"], "workspaceId": WORKSPACE_ID,
            "mailboxId": self.thread["mailboxId"], "collaborationId": COLLABORATION_ID,
            "identityAssurance": "link_possession", "allowedActions": ["read", "reply"],
            "visibility": "shared_only", "createdBy": {"ownerEmail": self.thread["ownerEmail"], "displayName": "Owner Person"},
            "createdAt": now - 100, "expiresAt": now + 3600, "status": "exchanged",
            "exchangedAt": now - 50, "exchangeCount": 1, "revokedAt": None,
            "revokedBy": None, "activeSessionHash": SESSION_HASH,
        }
        self.session = {
            "v": 2, "sessionHash": SESSION_HASH, "inviteId": INVITE_ID,
            "ownerEmail": self.thread["ownerEmail"], "workspaceId": WORKSPACE_ID,
            "mailboxId": self.thread["mailboxId"], "collaborationId": COLLABORATION_ID,
            "allowedActions": ["read", "reply"], "visibility": "shared_only",
            "identityAssurance": "link_possession", "guestDisplayName": "Guest Person",
            "createdAt": now - 50, "lastUsedAt": now - 10, "expiresAt": now + 1800,
            "status": "active", "csrfTokenHash": "d" * 64,
            "revokedAt": None, "loggedOutAt": None,
        }
        self.thread_key = redis_store.build_v2_thread_key(COLLABORATION_ID)
        self.source_key = redis_store.build_v2_source_thread_key(
            self.thread["ownerEmail"], self.thread["mailboxId"], self.thread["sourceRef"], hmac_key=HMAC_KEY,
        )
        self.invite_key = redis_store.build_v2_invite_key(INVITE_ID)
        self.session_key = redis_store.build_v2_guest_session_key(SESSION_HASH)
        self.known_keys = {self.thread_key, self.source_key, self.invite_key, self.session_key}
        for user_id in (OWNER_ID, TEAM_ID):
            self.known_keys.add(redis_store.build_v2_discovery_key(WORKSPACE_ID, user_id))
            self.known_keys.update(notification_store.build_notification_keys(WORKSPACE_ID, user_id))
        self.put(self.thread_key, self.thread, "thread")
        self.put(self.invite_key, self.invite, "invite")
        self.put(self.session_key, self.session, "session")
        self.command("SET", self.source_key, COLLABORATION_ID, "PX", RETENTION_MS)

    def put(self, key, value, kind):
        wire = redis_store._v2_wire_json(value, kind)
        self.assertIsNotNone(wire, (kind, value))
        self.command("SET", key, wire, "PX", RETENTION_MS)

    def transport(self, command):
        self.commands.append(command)
        if command[0] == "EVAL" and self.before_eval is not None:
            callback, self.before_eval = self.before_eval, None
            callback(command)
        result = self.command(*command)
        return {"result": result.decode() if isinstance(result, bytes) else result}

    def current(self):
        return redis_store._v2_json_from_wire(self.command("GET", self.thread_key), "thread")

    def key(self):
        self.sequence += 1
        key = base64.urlsafe_b64encode(self.sequence.to_bytes(32, "big")).rstrip(b"=").decode()
        self.known_keys.add(redis_store.build_v2_owner_idempotency_key(key, hmac_key=HMAC_KEY))
        return key

    def capability(self, actor="owner", action="reply"):
        return authorization._InternalCollaborationCapability(
            authorization._INTERNAL_CAPABILITY_SENTINEL, self.thread["ownerEmail"], WORKSPACE_ID,
            self.thread["mailboxId"], "google", COLLABORATION_ID, action,
            "owner" if actor == "owner" else "internal",
            "Owner Person" if actor == "owner" else "Team Person",
            OWNER_ID if actor == "owner" else TEAM_ID, "owner" if actor == "owner" else "participant",
            OWNER_ID, "Owner Person",
        )

    def guest_capability(self):
        return guest_session._GuestMutationCapability(
            guest_session._GUEST_MUTATION_SENTINEL, SESSION_HASH, INVITE_ID,
            self.thread["ownerEmail"], WORKSPACE_ID, self.thread["mailboxId"], COLLABORATION_ID,
            "Guest Person", self.session["expiresAt"], self.session["createdAt"], self.session["lastUsedAt"],
        )

    def append(self, actor, visibility="shared", *, key=None, text="A fresh reply"):
        key = self.key() if key is None else key
        if actor == "guest":
            return mutations.append_guest_v2_reply(
                self.guest_capability(), text, idempotency_key=key, command_transport=self.transport,
            )
        return mutations.append_owner_v2_message_idempotently(
            self.capability(actor, "reply" if visibility == "shared" else "internal_note"),
            text, visibility=visibility, idempotency_key=key, command_transport=self.transport,
        )

    def lifecycle(self, operation):
        current = self.current()
        result = mutations.transition_v2_lifecycle(
            self.capability(action=operation), operation=operation,
            expected_state=current["state"], expected_updated_at=current["updatedAt"],
            command_transport=self.transport,
        )
        self.assertEqual(result["status"], "ok", result)
        return result

    def snapshot(self):
        """Exact known keys plus database size; never SCAN/KEYS."""
        result = {"count": self.command("DBSIZE")}
        for key in sorted(self.known_keys):
            kind = self.command("TYPE", key)
            if kind == "string":
                value = self.command("GET", key)
            elif kind == "hash":
                entries = self.command("HGETALL", key)
                value = sorted(zip(entries[::2], entries[1::2]))
            elif kind == "zset":
                value = self.command("ZRANGE", key, 0, -1, "WITHSCORES")
            else:
                self.assertEqual(kind, "none")
                value = None
            result[key] = (kind, value, self.command("PEXPIRETIME", key))
        return result

    def notifications(self, user_id):
        key = notification_store.build_notification_keys(WORKSPACE_ID, user_id)[0]
        raw = self.command("HGETALL", key)
        return [json.loads(value) for value in raw[1::2]]

    def assert_closed(self, result, before):
        self.assertEqual(result, {"status": "error", "error": {"code": "collaboration_resolved"}})
        self.assertEqual(self.snapshot(), before)

    def test_active_owner_and_team_shared_and_internal_and_guest_append(self):
        for actor, visibility in (("owner", "shared"), ("owner", "internal"),
                                  ("team", "shared"), ("team", "internal"), ("guest", "shared")):
            with self.subTest(actor=actor, visibility=visibility):
                count = len(self.current()["messages"])
                start = len(self.commands)
                result = self.append(actor, visibility)
                self.assertEqual(result["status"], "ok", result)
                current = self.current()
                self.assertEqual(len(current["messages"]), count + 1)
                self.assertEqual(current["state"], "needs_review")
                self.assertEqual(current["messages"][-1]["visibility"], visibility)
                self.assertEqual([command[0] for command in self.commands[start:]], ["GET", "EVAL"])
        self.assertEqual(len(self.notifications(OWNER_ID)), 3)
        self.assertEqual(len(self.notifications(TEAM_ID)), 3)

    def test_resolved_fresh_appends_have_no_activity_notification_discovery_idempotency_or_expiry_effect(self):
        self.lifecycle("resolve")
        for actor, visibility in (("owner", "shared"), ("owner", "internal"),
                                  ("team", "shared"), ("team", "internal"), ("guest", "shared")):
            with self.subTest(actor=actor, visibility=visibility):
                key = self.key()
                before = self.snapshot()
                start = len(self.commands)
                self.assert_closed(self.append(actor, visibility, key=key), before)
                self.assertEqual([command[0] for command in self.commands[start:]], ["GET", "EVAL"])

    def test_committed_owner_team_guest_retry_after_resolve_preserves_message_notifications_read_at_and_expiry(self):
        operations = []
        for actor, visibility in (("owner", "shared"), ("owner", "internal"),
                                  ("team", "shared"), ("team", "internal"), ("guest", "shared")):
            key = self.key()
            result = self.append(actor, visibility, key=key)
            self.assertEqual(result["status"], "ok", result)
            operations.append((actor, visibility, key, result))
        for user_id in (OWNER_ID, TEAM_ID):
            for notification in self.notifications(user_id):
                result = notification_store.mark_read(
                    WORKSPACE_ID, user_id, notification["notificationId"], command_transport=self.transport,
                )
                self.assertEqual(result["status"], "ok", result)
        self.lifecycle("resolve")
        before = self.snapshot()
        for actor, visibility, key, original in operations:
            with self.subTest(actor=actor, visibility=visibility):
                self.assertEqual(self.append(actor, visibility, key=key), original)
                self.assertEqual(self.snapshot(), before)
                changed = self.append(actor, visibility, key=key, text="Changed payload")
                self.assertEqual(changed, {"status": "error", "error": {"code": "idempotency_conflict"}})
                self.assertEqual(self.snapshot(), before)

    def test_resolve_wins_inside_eval_after_active_preparation_for_every_author_and_visibility(self):
        for actor, visibility in (("owner", "shared"), ("owner", "internal"),
                                  ("team", "shared"), ("team", "internal"), ("guest", "shared")):
            with self.subTest(actor=actor, visibility=visibility):
                self.lifecycle("reopen")
                key = self.key()
                captured = {}

                def resolve_first(command):
                    self.assertIn(command[1], (redis_store._APPEND_V2_OWNER_IDEMPOTENT_LUA, redis_store._APPEND_V2_GUEST_REPLY_LUA))
                    self.lifecycle("resolve")
                    captured["snapshot"] = self.snapshot()

                self.before_eval = resolve_first
                result = self.append(actor, visibility, key=key)
                self.assert_closed(result, captured["snapshot"])
                self.assertEqual(self.current()["state"], "resolved")

    def test_append_wins_before_resolve_then_reopen_restores_all_existing_authorities(self):
        for actor, visibility in (("owner", "shared"), ("owner", "internal"),
                                  ("team", "shared"), ("team", "internal"), ("guest", "shared")):
            with self.subTest(actor=actor, visibility=visibility):
                self.lifecycle("reopen")
                before_count = len(self.current()["messages"])
                result = self.append(actor, visibility)
                self.assertEqual(result["status"], "ok", result)
                self.lifecycle("resolve")
                current = self.current()
                self.assertEqual(current["state"], "resolved")
                self.assertEqual(len(current["messages"]), before_count + 1)
                self.assertEqual(sum(message["id"] == result["message"]["id"] for message in current["messages"]), 1)

    def test_stale_active_prepared_guest_retry_recovers_after_resolve_at_eval_boundary(self):
        key = self.key()
        original = self.append("guest", key=key)
        self.assertEqual(original["status"], "ok", original)
        captured = {}

        def resolve_first(command):
            self.assertEqual(command[1], redis_store._APPEND_V2_GUEST_REPLY_LUA)
            self.lifecycle("resolve")
            captured["snapshot"] = self.snapshot()

        self.before_eval = resolve_first
        self.assertEqual(self.append("guest", key=key), original)
        self.assertEqual(self.snapshot(), captured["snapshot"])

    def test_append_winning_after_resolve_preparation_preserves_cas_and_history(self):
        for actor, visibility in (("owner", "shared"), ("owner", "internal"),
                                  ("team", "shared"), ("team", "internal"), ("guest", "shared")):
            with self.subTest(actor=actor, visibility=visibility):
                self.lifecycle("reopen")
                current = self.current()
                captured = {}

                def append_first(command):
                    self.assertEqual(command[1], redis_store._TRANSITION_V2_LIFECYCLE_LUA)
                    captured["append"] = self.append(actor, visibility)
                    self.assertEqual(captured["append"]["status"], "ok", captured["append"])
                    captured["snapshot"] = self.snapshot()

                self.before_eval = append_first
                result = mutations.transition_v2_lifecycle(
                    self.capability(action="resolve"), operation="resolve",
                    expected_state=current["state"], expected_updated_at=current["updatedAt"],
                    command_transport=self.transport,
                )
                self.assertEqual(result, {"status": "error", "error": {"code": "stale_thread"}})
                self.assertEqual(self.snapshot(), captured["snapshot"])
                self.lifecycle("resolve")
                current = self.current()
                self.assertEqual(current["state"], "resolved")
                self.assertEqual(sum(message["id"] == captured["append"]["message"]["id"]
                                     for message in current["messages"]), 1)

    def test_append_payload_cannot_change_lifecycle(self):
        for actor in ("owner", "team", "guest"):
            with self.subTest(actor=actor):
                key = self.key()
                before = self.snapshot()

                def tamper(command):
                    wire_index = 3 + command[2] + 1
                    replacement = json.loads(command[wire_index])
                    replacement["state"] = "resolved"
                    command[wire_index] = json.dumps(replacement, separators=(",", ":"))

                self.before_eval = tamper
                result = self.append(actor, key=key)
                self.assertEqual(result["status"], "error", result)
                self.assertEqual(self.snapshot(), before)

    def test_legacy_append_primitive_cannot_write_resolved_or_change_lifecycle(self):
        for resolved in (False, True):
            with self.subTest(resolved=resolved):
                if resolved:
                    self.lifecycle("resolve")
                thread = self.current()
                message = models._build_v2_context_message(
                    self.capability(), "Legacy reply", author_kind="owner", visibility="shared",
                    created_at=thread["updatedAt"] + 1,
                )
                replacement = {**thread, "messages": [*thread["messages"], message],
                               "updatedAt": message["createdAt"], "state": "needs_action"}
                before = self.snapshot()
                result = redis_store._save_v2_thread_if_expected(
                    replacement, thread["updatedAt"], command_transport=self.transport,
                )
                self.assertIsInstance(result, dict)
                self.assertIn(result["status"], {"conflict", "malformed"}, result)
                if resolved:
                    self.assertEqual(result["error"], {"code": "collaboration_resolved"})
                self.assertEqual(self.snapshot(), before)

    def test_guest_recovery_does_not_bypass_revoked_invite_or_session_or_expiry(self):
        key = self.key()
        self.assertEqual(self.append("guest", key=key)["status"], "ok")
        self.lifecycle("resolve")
        for kind in ("invite_missing", "session_missing", "invite_revoked", "session_revoked", "session_expired"):
            with self.subTest(kind=kind):
                self.put(self.invite_key, self.invite, "invite")
                self.put(self.session_key, self.session, "session")
                if kind == "invite_missing":
                    self.command("DEL", self.invite_key)
                elif kind == "session_missing":
                    self.command("DEL", self.session_key)
                elif kind == "invite_revoked":
                    invite = {**self.invite, "status": "revoked", "revokedAt": int(time.time()),
                              "revokedBy": self.thread["ownerEmail"]}
                    self.put(self.invite_key, invite, "invite")
                elif kind == "session_revoked":
                    self.put(self.session_key, {**self.session, "status": "revoked", "revokedAt": int(time.time())}, "session")
                before = self.snapshot()
                if kind == "session_expired":
                    with patch.object(mutations.time, "time", return_value=self.session["expiresAt"]):
                        result = self.append("guest", key=key)
                else:
                    result = self.append("guest", key=key)
                expected = "session_expired" if kind == "session_expired" else "session_revoked"
                self.assertEqual(result, {"status": "error", "error": {"code": expected}})
                self.assertEqual(self.snapshot(), before)

    def test_guest_expiry_checked_again_inside_lua_before_resolved_recovery(self):
        key = self.key()
        self.assertEqual(self.append("guest", key=key)["status"], "ok")
        self.lifecycle("resolve")
        before = self.snapshot()

        for expires_at in (self.session["expiresAt"], self.invite["expiresAt"]):
            with self.subTest(expires_at=expires_at):
                def expire_at_commit(command):
                    self.assertEqual(command[1], redis_store._APPEND_V2_GUEST_REPLY_LUA)
                    command[3 + command[2] + 3] = str(expires_at)

                self.before_eval = expire_at_commit
                result = self.append("guest", key=key)
                self.assertEqual(result, {"status": "error", "error": {"code": "session_expired"}})
                self.assertEqual(self.snapshot(), before)

    def test_public_resolved_and_idempotency_errors_remain_generic_conflicts(self):
        for code in ("collaboration_resolved", "idempotency_conflict"):
            internal = {"status": "error", "error": {"code": code}}
            owner_failure = application._owner_mutation_failure(internal)
            guest_failure = application._failure_from_result(
                internal, default_status="error", default_code="storage_protocol_error",
            )
            self.assertEqual(owner_failure, {**internal, "collaboration": None})
            self.assertEqual(guest_failure, {**internal, "collaboration": None})
            with patch.object(owner_http, "_emit_application_failure_event"):
                responses = [owner_http._application_failure(owner_failure, operation="append_shared"),
                             guest_http._guest_failure(guest_failure)]
            for response in responses:
                self.assertEqual(response.status, 409)
                self.assertEqual(json.loads(response.body), {"ok": False, "error": {"code": "conflict"}})


if __name__ == "__main__":
    unittest.main()
