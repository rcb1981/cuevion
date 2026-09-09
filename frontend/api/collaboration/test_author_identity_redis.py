"""Canonical author invariants on disposable local Redis, never hosted Redis."""
from __future__ import annotations

import json
import unittest

from . import application, models, redis_store
from . import test_lua_redis_integration as harness
from . import test_notifications as fixture
from api.notification_service import store


def author_null_semantics_script(script: str, mode: str) -> str:
    """Exercise hosted decoders that omit an explicit nullable message field."""
    if mode == "normal":
        return script
    omit_all_nulls = mode == "hosted_without_null_sentinel"
    sentinel = "nil" if omit_all_nulls else "actualCjson.null"
    removal = (
        "  for key, child in pairs(value) do\n"
        "    if child == actualCjson.null then value[key] = nil end\n"
        "  end\n"
        if omit_all_nulls else
        "  if value.authorUserId == actualCjson.null then value.authorUserId = nil end\n"
    )
    return (
        "local actualCjson = cjson\n"
        "local cjson = {encode=actualCjson.encode, null=" + sentinel + "}\n"
        "local function removeAuthorNull(value)\n"
        "  if type(value) ~= 'table' then return end\n"
        + removal +
        "  for _, child in pairs(value) do removeAuthorNull(child) end\n"
        "end\n"
        "cjson.decode = function(raw)\n"
        "  local value = actualCjson.decode(raw)\n"
        "  removeAuthorNull(value)\n"
        "  return value\n"
        "end\n" + script
    )


class AuthorIdentityRedisTests(unittest.TestCase):
    # Reuse the local fixture's setup and helpers without inheriting its tests.
    setUp = fixture.NotificationMutationRedisTests.setUp
    thread = fixture.NotificationMutationRedisTests.thread
    create = fixture.NotificationMutationRedisTests.create
    capability = fixture.NotificationMutationRedisTests.capability
    append = fixture.NotificationMutationRedisTests.append
    notifications = fixture.NotificationMutationRedisTests.notifications
    guest = fixture.NotificationMutationRedisTests.guest
    guest_reply = fixture.NotificationMutationRedisTests.guest_reply

    @classmethod
    def setUpClass(cls):
        harness.ProductionLuaRedisIntegrationTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        harness.ProductionLuaRedisIntegrationTests.tearDownClass.__func__(cls)

    def canonical(self):
        loaded = redis_store._load_v2_thread("A" * 22, command_transport=self.transport)
        self.assertEqual(loaded.get("status"), "ok", loaded)
        return loaded.record

    def project(self, thread, viewer):
        dto, error = application._build_verified_thread_dto(
            thread,
            self.capability(viewer, "read"),
            team_member_resolver=lambda _workspace, user: (
                {"memberUserId": user, "sourceInvitationId": "tinv_" + user[-22:]},
                None,
            ),
            external_guests=[] if viewer == fixture.OWNER else None,
        )
        self.assertIsNone(error)
        self.assertIsNotNone(dto)
        return dto

    def seed_legacy_message(self, *, state="needs_review"):
        thread = self.create((fixture.FIRST, fixture.SECOND))
        key = redis_store.build_v2_thread_key(thread["collaborationId"])
        wire = json.loads(self.client.command(["GET", key]))
        wire["messages"] = [{
            "id": "L" * 22,
            "authorKind": "owner",
            "authorDisplayName": "Owner",
            "text": "Historical message, same display name as owner",
            "visibility": "shared",
            "createdAt": str(self.ms),
        }]
        wire["state"] = state
        self.client.command(["SET", key, json.dumps(wire), "KEEPTTL"])
        return key

    def test_owner_and_team_shared_and_internal_identity_is_canonical_everywhere(self):
        self.create((fixture.FIRST, fixture.SECOND))
        for actor, visibility in (
            (fixture.OWNER, "shared"), (fixture.FIRST, "shared"),
            (fixture.OWNER, "internal"), (fixture.FIRST, "internal"),
        ):
            with self.subTest(actor=actor, visibility=visibility):
                before = {user: len(self.notifications(user))
                          for user in (fixture.OWNER, fixture.FIRST, fixture.SECOND)}
                sent = self.append(actor, visibility, key=actor + visibility)
                self.assertEqual(sent["status"], "ok", sent)
                self.assertEqual(sent["message"]["authorUserId"], actor)
                self.assertEqual(sent["message"]["authorRole"], "Cuevion user")
                canonical = self.canonical()
                message = canonical["messages"][-1]
                self.assertEqual(message["id"], sent["message"]["id"])
                self.assertEqual(message["authorUserId"], actor)
                self.assertEqual(message["authorKind"], "owner" if actor == fixture.OWNER else "internal")
                raw = json.loads(self.client.command([
                    "GET", redis_store.build_v2_thread_key(canonical["collaborationId"])]))
                self.assertEqual(raw["messages"][-1]["authorUserId"], actor)
                for viewer in (fixture.OWNER, fixture.FIRST):
                    projected = self.project(canonical, viewer)["messages"][-1]
                    self.assertEqual(projected["authorUserId"], actor)
                    self.assertEqual(projected, sent["message"])
                for recipient in (fixture.OWNER, fixture.FIRST, fixture.SECOND):
                    rows = self.notifications(recipient)
                    self.assertEqual(len(rows), before[recipient] + int(recipient != actor))
                    if recipient != actor:
                        row = next(row for row in rows if row["activityId"] == message["id"])
                        self.assertEqual(row["actor"]["userId"], message["authorUserId"])
                        self.assertEqual(row["actor"]["type"], "cuevion_user")
                        self.assertEqual(row["kind"], "shared_message" if visibility == "shared" else "internal_note")

    def test_owner_and_team_retries_preserve_message_identity_and_exact_retention(self):
        thread = self.create((fixture.FIRST, fixture.SECOND))
        keys = [redis_store.build_v2_thread_key(thread["collaborationId"]),
                redis_store.build_v2_source_thread_key(
                    thread["ownerEmail"], thread["mailboxId"], thread["sourceRef"])]
        for key in keys:
            self.client.command(["PEXPIRE", key, 60000])
        expiry = [self.client.command(["PEXPIRETIME", key]) for key in keys]
        for actor, visibility in ((fixture.OWNER, "shared"), (fixture.FIRST, "internal")):
            key = actor + visibility
            first = self.append(actor, visibility, key=key)
            self.assertEqual(first["status"], "ok", first)
            before = self.canonical()
            notifications = {user: self.notifications(user)
                             for user in (fixture.OWNER, fixture.FIRST, fixture.SECOND)}
            retry = self.append(actor, visibility, key=key)
            self.assertEqual(retry, first)
            self.assertEqual(retry["message"]["authorUserId"], actor)
            self.assertEqual(self.canonical(), before)
            self.assertEqual([self.client.command(["PEXPIRETIME", key]) for key in keys], expiry)
            for user, rows in notifications.items():
                self.assertEqual(self.notifications(user), rows)

    def test_guest_identity_stays_null_and_guest_contract_and_retry_stay_unchanged(self):
        self.create((fixture.FIRST, fixture.SECOND))
        self.assertEqual(self.append()["status"], "ok")
        guest = self.guest()
        sent = self.guest_reply(guest)
        self.assertEqual(sent["status"], "ok", sent)
        self.assertNotIn("authorUserId", sent["message"])
        canonical = self.canonical()
        message = canonical["messages"][-1]
        self.assertEqual(message["authorKind"], "guest")
        self.assertIsNone(message["authorUserId"])
        self.assertEqual(message["id"], sent["message"]["id"])
        projected = models.build_v2_guest_thread_dto(canonical)
        for row in projected["messages"]:
            self.assertEqual(set(row), {"id", "authorDisplayName", "authorRole", "text", "timestamp"})
        self.assertNotIn("usr_", json.dumps(projected))
        self.assertNotIn("authorUserId", json.dumps(projected))
        for user in (fixture.OWNER, fixture.FIRST, fixture.SECOND):
            row = next(row for row in self.notifications(user) if row["activityId"] == message["id"])
            self.assertEqual(row["actor"], {"type": "external_guest", "displayName": "Guest"})
        self.assertEqual(self.guest_reply(guest), sent)
        self.assertEqual(self.canonical(), canonical)
        self.assertEqual(models.build_v2_guest_thread_dto(self.canonical()), projected)

    def test_preexisting_idempotent_send_replays_without_backfilling_identity(self):
        self.create()
        first = self.append()
        self.assertEqual(first["status"], "ok", first)
        key = redis_store.build_v2_thread_key("A" * 22)
        wire = json.loads(self.client.command(["GET", key]))
        wire["messages"][0].pop("authorUserId")
        self.client.command(["SET", key, json.dumps(wire), "KEEPTTL"])
        raw = self.client.command(["GET", key])
        notifications = self.notifications(fixture.FIRST)
        retry = self.append()
        self.assertEqual(retry["status"], "ok", retry)
        self.assertEqual(retry["message"], {**first["message"], "authorUserId": None})
        self.assertEqual(retry["updatedAt"], first["updatedAt"])
        self.assertEqual(self.client.command(["GET", key]), raw)
        self.assertEqual(self.notifications(fixture.FIRST), notifications)

    def test_recovered_nonnull_identity_cannot_change_to_another_actor(self):
        self.create()
        self.assertEqual(self.append()["status"], "ok")
        key = redis_store.build_v2_thread_key("A" * 22)
        wire = json.loads(self.client.command(["GET", key]))
        wire["messages"][0]["authorUserId"] = fixture.FIRST
        self.client.command(["SET", key, json.dumps(wire), "KEEPTTL"])
        raw = self.client.command(["GET", key])
        retry = self.append()
        self.assertNotEqual(retry["status"], "ok", retry)
        self.assertEqual(self.client.command(["GET", key]), raw)

    def test_mixed_legacy_and_new_timeline_never_infers_historical_author(self):
        key = self.seed_legacy_message()
        before_read = self.client.command(["GET", key])
        legacy = self.canonical()
        self.assertIsNone(legacy["messages"][0]["authorUserId"])
        for viewer in (fixture.OWNER, fixture.FIRST):
            self.assertIsNone(self.project(legacy, viewer)["messages"][0]["authorUserId"])
        self.assertEqual(self.client.command(["GET", key]), before_read)
        self.assertEqual(self.append()["status"], "ok")
        self.assertEqual(self.append(fixture.FIRST, "internal", key="team-note")["status"], "ok")
        self.assertEqual(self.guest_reply(self.guest())["status"], "ok")
        mixed = self.canonical()
        expected = [None, fixture.OWNER, fixture.FIRST, None]
        self.assertEqual([message["authorUserId"] for message in mixed["messages"]], expected)
        for viewer in (fixture.OWNER, fixture.FIRST):
            self.assertEqual([message["authorUserId"] for message in self.project(mixed, viewer)["messages"]], expected)

    def test_historical_resolved_thread_still_reads_without_author_backfill(self):
        key = self.seed_legacy_message(state="resolved")
        raw = self.client.command(["GET", key])
        canonical = self.canonical()
        for viewer in (fixture.OWNER, fixture.FIRST):
            dto = self.project(canonical, viewer)
            self.assertEqual(dto["state"], "resolved")
            self.assertIsNone(dto["messages"][0]["authorUserId"])
        self.assertEqual(self.client.command(["GET", key]), raw)

    def test_atomic_failure_commits_neither_message_nor_author_patch(self):
        for visibility in ("shared", "internal", "guest"):
            with self.subTest(visibility=visibility):
                self.client.command(["FLUSHALL"])
                thread = self.create()
                guest = self.guest() if visibility == "guest" else None
                key = redis_store.build_v2_thread_key(thread["collaborationId"])
                before = self.client.command(["GET", key])
                notification_keys = store.build_notification_keys(fixture.WORKSPACE, fixture.FIRST)
                self.client.command(["DEL", *notification_keys])
                self.client.command(["SET", notification_keys[0], "wrong type", "EX", 100])
                outcome = (self.guest_reply(guest) if guest else self.append(visibility=visibility))
                self.assertNotEqual(outcome["status"], "ok", outcome)
                self.assertEqual(self.client.command(["GET", key]), before)

    def test_lua_rejects_forged_appended_author_atomically(self):
        self.create((fixture.FIRST, fixture.SECOND))
        thread_key = redis_store.build_v2_thread_key("A" * 22)
        for actor, forged in ((fixture.FIRST, fixture.OWNER), (fixture.FIRST, fixture.SECOND),
                              (fixture.OWNER, fixture.FIRST), (fixture.OWNER, None),
                              (fixture.OWNER, "missing")):
            with self.subTest(actor=actor, forged=forged):
                before = self.client.command(["GET", thread_key])
                commands = []
                def transport(command):
                    command = list(command)
                    commands.append(command[0])
                    if command[0] == "EVAL":
                        payload_index = 3 + int(command[2]) + 1
                        replacement = json.loads(command[payload_index])
                        if forged == "missing":
                            replacement["messages"][-1].pop("authorUserId")
                        else:
                            replacement["messages"][-1]["authorUserId"] = forged
                        command[payload_index] = json.dumps(replacement)
                    return self.client.transport(command)
                outcome = self.append(actor, key=actor + str(forged), transport=transport)
                self.assertNotEqual(outcome["status"], "ok", outcome)
                self.assertIn("EVAL", commands)
                self.assertEqual(self.client.command(["GET", thread_key]), before)

    def test_lua_rejects_rewriting_existing_author_atomically(self):
        for legacy, forged in ((False, fixture.FIRST), (False, None),
                               (False, "missing"), (True, fixture.OWNER)):
            with self.subTest(legacy=legacy, forged=forged):
                self.client.command(["FLUSHALL"])
                if legacy:
                    self.seed_legacy_message()
                else:
                    self.create((fixture.FIRST, fixture.SECOND))
                    self.assertEqual(self.append()["status"], "ok")
                thread_key = redis_store.build_v2_thread_key("A" * 22)
                before = self.client.command(["GET", thread_key])
                def transport(command):
                    command = list(command)
                    if command[0] == "EVAL":
                        payload_index = 3 + int(command[2]) + 1
                        replacement = json.loads(command[payload_index])
                        if forged == "missing":
                            replacement["messages"][0].pop("authorUserId")
                        else:
                            replacement["messages"][0]["authorUserId"] = forged
                        command[payload_index] = json.dumps(replacement)
                    return self.client.transport(command)
                result = self.append(fixture.FIRST, key="history-rewrite", transport=transport)
                self.assertNotEqual(result["status"], "ok", result)
                self.assertEqual(self.client.command(["GET", thread_key]), before)

    def test_hosted_author_null_omission_preserves_mixed_history_and_guest_replay(self):
        for mode in ("normal", "hosted", "hosted_without_null_sentinel"):
            with self.subTest(mode=mode):
                self.client.command(["FLUSHALL"])
                self.seed_legacy_message()
                def transport(command):
                    command = list(command)
                    if command[0] == "EVAL":
                        command[1] = author_null_semantics_script(command[1], mode)
                    return self.client.transport(command)
                owner = self.append(transport=transport)
                self.assertEqual(owner["status"], "ok", owner)
                guest = self.guest()
                first = self.guest_reply(guest, transport=transport)
                self.assertEqual(first["status"], "ok", first)
                self.assertEqual(self.guest_reply(guest, transport=transport), first)
                note = self.append(fixture.FIRST, "internal", key="hosted-note", transport=transport)
                self.assertEqual(note["status"], "ok", note)
                self.assertEqual([message["authorUserId"] for message in self.canonical()["messages"]],
                                 [None, fixture.OWNER, None, fixture.FIRST])

    def test_outer_redis_command_counts_match_measured_baseline(self):
        def measure(action, expected):
            commands = []
            def transport(command):
                commands.append(command[0])
                return self.client.transport(command)
            result = action(transport)
            self.assertEqual(result.get("status"), "ok", result)
            self.assertEqual(commands, expected)
        for visibility in ("shared", "internal"):
            self.client.command(["FLUSHALL"])
            self.create()
            for _ in range(2):
                measure(lambda t: self.append(visibility=visibility, transport=t), ["GET", "EVAL"])
        self.client.command(["FLUSHALL"])
        self.create()
        guest = self.guest()
        for _ in range(2):
            measure(lambda t: self.guest_reply(guest, transport=t), ["GET", "EVAL"])
        measure(lambda t: redis_store._load_v2_thread("A" * 22, command_transport=t), ["GET"])


if __name__ == "__main__":
    unittest.main()
