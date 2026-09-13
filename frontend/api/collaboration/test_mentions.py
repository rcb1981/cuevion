"""Authoritative mention persistence through production Lua on private local Redis."""

from __future__ import annotations

import json
import unittest
from concurrent.futures import ThreadPoolExecutor

from . import application, models, mutations, redis_store
from . import test_resolved_write_closure as closure
from api.notification_service import store as notification_store


class AuthoritativeMentionLuaTests(unittest.TestCase):
    # Reuse the existing port-zero, nonpersistent UNIX-socket fixture and its
    # explicit known-key snapshots; no environment Redis or key discovery.
    command = closure.ResolvedWriteClosureLuaTests.command
    put = closure.ResolvedWriteClosureLuaTests.put
    transport = closure.ResolvedWriteClosureLuaTests.transport
    current = closure.ResolvedWriteClosureLuaTests.current
    key = closure.ResolvedWriteClosureLuaTests.key
    capability = closure.ResolvedWriteClosureLuaTests.capability
    guest_capability = closure.ResolvedWriteClosureLuaTests.guest_capability
    lifecycle = closure.ResolvedWriteClosureLuaTests.lifecycle
    snapshot = closure.ResolvedWriteClosureLuaTests.snapshot
    notifications = closure.ResolvedWriteClosureLuaTests.notifications

    @classmethod
    def setUpClass(cls):
        closure.ResolvedWriteClosureLuaTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        closure.ResolvedWriteClosureLuaTests.tearDownClass.__func__(cls)

    def setUp(self):
        closure.ResolvedWriteClosureLuaTests.setUp(self)
        self.members = {closure.TEAM_ID: {
            "memberUserId": closure.TEAM_ID, "sourceInvitationId": "tinv_closure",
            "displayName": "Team Person", "workspaceId": closure.WORKSPACE_ID,
        }}
        self.authority_calls = []

        def resolve(workspace_id, user_id):
            self.authority_calls.append((workspace_id, user_id))
            member = self.members.get(user_id)
            if member is None or member["workspaceId"] != workspace_id:
                return None, "not_active"
            return dict(member), None

        # The base fixture already patches this exact authority boundary.
        from . import authorization
        authorization._resolve_active_team_member.side_effect = resolve

    def spans(self, users, *, prefix="", suffix=""):
        labels = {closure.OWNER_ID: "Owner Person", **{
            user_id: member["displayName"] for user_id, member in self.members.items()
        }}
        text = prefix
        mentions = []
        for user_id in users:
            if mentions:
                text += " "
            label = "@" + labels[user_id]
            start = len(text.encode("utf-16-le")) // 2
            text += label
            mentions.append({"userId": user_id, "start": start,
                             "end": len(text.encode("utf-16-le")) // 2, "displayText": label})
        return text + suffix, mentions

    def append(self, actor="owner", visibility="shared", *, text=None, mentions=None, key=None):
        if text is None:
            text, mentions = self.spans([closure.TEAM_ID])
        return mutations.append_owner_v2_message_idempotently(
            self.capability(actor, "reply" if visibility == "shared" else "internal_note"),
            text, visibility=visibility, mentions=mentions if mentions is not None else [],
            idempotency_key=self.key() if key is None else key, command_transport=self.transport,
        )

    def assert_no_commit(self, text, mentions, *, expected=None):
        key = self.key()
        before = self.snapshot()
        result = self.append(text=text, mentions=mentions, key=key)
        self.assertEqual(result["status"], "error", result)
        if expected:
            self.assertEqual(result["error"]["code"], expected)
        self.assertEqual(self.snapshot(), before)

    def test_owner_team_shared_internal_attention_generic_self_and_exact_atomic_eval(self):
        for actor, target in (("owner", closure.TEAM_ID), ("team", closure.OWNER_ID)):
            for visibility in ("shared", "internal"):
                with self.subTest(actor=actor, visibility=visibility):
                    text, mentions = self.spans([target, target])
                    start = len(self.commands)
                    result = self.append(actor, visibility, text=text, mentions=mentions)
                    self.assertEqual(result["status"], "ok", result)
                    self.assertEqual(result["message"]["mentions"], mentions)
                    self.assertEqual(self.current()["messages"][-1]["mentions"], mentions)
                    self.assertEqual([command[0] for command in self.commands[start:]], ["GET", "EVAL"])
                    activity_id = result["message"]["id"]
                    rows = [row for row in self.notifications(target) if row["activityId"] == activity_id]
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]["attention"], "mention")
                    self.assertEqual(rows[0]["kind"], "shared_message" if visibility == "shared" else "internal_note")
                    self.assertEqual(rows[0]["workspaceId"], closure.WORKSPACE_ID)
                    self.assertEqual(rows[0]["mailboxId"], self.thread["mailboxId"])
                    self.assertEqual(rows[0]["sourceRef"], self.thread["sourceRef"])
                    self.assertEqual(rows[0]["collaborationId"], closure.COLLABORATION_ID)
                    author = closure.OWNER_ID if actor == "owner" else closure.TEAM_ID
                    self.assertFalse([row for row in self.notifications(author) if row["activityId"] == activity_id])

    def test_owner_and_team_self_mentions_store_but_only_other_recipient_gets_generic(self):
        for actor, target in (("owner", closure.OWNER_ID), ("team", closure.TEAM_ID)):
            with self.subTest(actor=actor):
                text, mentions = self.spans([target])
                result = self.append(actor, text=text, mentions=mentions)
                self.assertEqual(result["status"], "ok", result)
                self.assertEqual(result["message"]["mentions"], mentions)
                self.assertFalse([row for row in self.notifications(target) if row["activityId"] == result["message"]["id"]])
                other = closure.TEAM_ID if actor == "owner" else closure.OWNER_ID
                rows = [row for row in self.notifications(other) if row["activityId"] == result["message"]["id"]]
                self.assertEqual(len(rows), 1)
                self.assertNotIn("attention", rows[0])

    def test_utf16_emoji_before_between_and_after_mentions_survives_lua_wire_and_projection(self):
        text, mentions = self.spans([closure.TEAM_ID], prefix="😀 Before ", suffix=" 🚀 ordinary supplementary text")
        result = self.append(text=text, mentions=mentions)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["message"]["mentions"], mentions)
        raw = json.loads(self.command("GET", self.thread_key))
        self.assertEqual(raw["messages"][-1]["mentions"], [{**mentions[0], "start": str(mentions[0]["start"]), "end": str(mentions[0]["end"])}])
        self.assertEqual(application._build_owner_thread_dto(self.current())["messages"][-1]["mentions"], mentions)

    def test_invalid_spans_and_mixed_valid_invalid_targets_leave_every_key_and_expiry_unchanged(self):
        text, mentions = self.spans([closure.TEAM_ID], prefix="😀 ")
        for change in ({"start": -1}, {"end": mentions[0]["start"]}, {"end": 999},
                       {"start": 1}, {"start": 0, "end": 1}, {"displayText": "@Other Person"},
                       {"displayText": ""}, {"start": True}, {"end": 3.5}):
            with self.subTest(change=change):
                self.assert_no_commit(text, [{**mentions[0], **change}])
        self.assert_no_commit(text, [mentions[0], mentions[0]])
        text, mentions = self.spans([closure.TEAM_ID, closure.OWNER_ID])
        for target in ("guest@example.test", "guest-id", "usr_" + "Z" * 21 + "A"):
            with self.subTest(target=target):
                self.assert_no_commit(text, [mentions[0], {**mentions[1], "userId": target}])

    def test_workspace_member_without_explicit_grant_cross_workspace_removed_and_reinvite_reject(self):
        text, mentions = self.spans([closure.TEAM_ID])
        original = dict(self.members[closure.TEAM_ID])
        outside = "usr_" + "C" * 21 + "A"
        self.members[outside] = {**original, "memberUserId": outside}
        self.assert_no_commit(text, [{**mentions[0], "userId": outside}])
        for state in (None, {**original, "workspaceId": "wsp_" + "Z" * 22},
                      {**original, "sourceInvitationId": "tinv_reinvited"}):
            with self.subTest(state=state):
                self.members.pop(closure.TEAM_ID, None)
                if state:
                    self.members[closure.TEAM_ID] = state
                self.assert_no_commit(text, mentions)
        self.thread["participants"][0]["membershipRef"] = "tinv_reinvited"
        self.put(self.thread_key, self.thread, "thread")
        self.assertEqual(self.append(text=text, mentions=mentions)["status"], "ok")

    def test_live_team_label_is_required_even_when_stored_participant_snapshot_is_old(self):
        text, mentions = self.spans([closure.TEAM_ID])
        self.members[closure.TEAM_ID]["displayName"] = "Renamed Person"
        self.assert_no_commit(text, mentions)
        text, mentions = self.spans([closure.TEAM_ID])
        self.assertEqual(self.append(text=text, mentions=mentions)["status"], "ok")

    def test_32_occurrences_and_16_distinct_entitled_targets_commit_without_recipient_duplicates(self):
        for index in range(2, 16):
            user_id = "usr_" + chr(65 + index) * 21 + "A"
            ref = f"tinv_person{index}"
            label = f"Person {index}"
            self.thread["participants"].append({"userId": user_id, "membershipRef": ref, "displayName": label})
            self.members[user_id] = {"memberUserId": user_id, "sourceInvitationId": ref,
                                     "displayName": label, "workspaceId": closure.WORKSPACE_ID}
            self.known_keys.add(redis_store.build_v2_discovery_key(closure.WORKSPACE_ID, user_id))
            self.known_keys.update(notification_store.build_notification_keys(closure.WORKSPACE_ID, user_id))
        self.put(self.thread_key, self.thread, "thread")
        users = [closure.OWNER_ID, *self.members]
        text, mentions = self.spans(users * 2)
        self.assertEqual(len(mentions), 32)
        result = self.append(text=text, mentions=mentions)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["message"]["mentions"], mentions)
        self.assertEqual(len(self.authority_calls), 15)
        self.assertEqual(len(set(self.authority_calls)), 15)
        for user_id in self.members:
            rows = self.notifications(user_id)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["attention"], "mention")
        self.assertEqual(self.notifications(closure.OWNER_ID), [])
        text, mentions = self.spans(users * 2 + [closure.OWNER_ID])
        self.assert_no_commit(text, mentions)

    def test_same_key_reordered_metadata_recovers_and_every_changed_field_conflicts(self):
        text, mentions = self.spans([closure.TEAM_ID, closure.OWNER_ID])
        key = self.key()
        first = self.append(text=text, mentions=list(reversed(mentions)), key=key)
        self.assertEqual(first["status"], "ok", first)
        self.assertEqual(first["message"]["mentions"], mentions)
        before = self.snapshot()
        self.assertEqual(self.append(text=text, mentions=mentions, key=key), first)
        variants = [(text + "!", mentions), (text, [])]
        for change in ({"userId": closure.OWNER_ID}, {"start": 1}, {"start": -1},
                       {"end": mentions[0]["end"] - 1}, {"displayText": "@Changed Person"}, {"displayText": ""}):
            variants.append((text, [{**mentions[0], **change}, mentions[1]]))
        for changed_text, changed_mentions in variants:
            with self.subTest(text=changed_text, mentions=changed_mentions):
                result = self.append(text=changed_text, mentions=changed_mentions, key=key)
                self.assertEqual(result, {"status": "error", "error": {"code": "idempotency_conflict"}})
                self.assertEqual(self.snapshot(), before)

    def test_committed_retry_after_removal_rename_resolve_and_mark_read_preserves_snapshot(self):
        text, mentions = self.spans([closure.TEAM_ID])
        key = self.key()
        first = self.append(text=text, mentions=mentions, key=key)
        self.assertEqual(first["status"], "ok", first)
        row = self.notifications(closure.TEAM_ID)[0]
        self.assertEqual(notification_store.mark_read(closure.WORKSPACE_ID, closure.TEAM_ID, row["notificationId"], command_transport=self.transport)["status"], "ok")
        self.members.pop(closure.TEAM_ID)
        self.lifecycle("resolve")
        before = self.snapshot()
        self.assertEqual(self.append(text=text, mentions=mentions, key=key), first)
        self.assertEqual(self.snapshot(), before)
        self.lifecycle("reopen")
        self.assert_no_commit(text, mentions, expected="forbidden")

    def test_concurrent_matching_mention_retries_commit_one_activity_and_notification(self):
        text, mentions = self.spans([closure.TEAM_ID, closure.TEAM_ID])
        key = self.key()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _index: self.append(text=text, mentions=mentions, key=key), range(8)))
        self.assertEqual(results[0]["status"], "ok", results)
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(len(self.current()["messages"]), 1)
        self.assertEqual(self.current()["messages"][0]["mentions"], mentions)
        self.assertEqual(len(self.notifications(closure.TEAM_ID)), 1)
        self.assertEqual(self.notifications(closure.TEAM_ID)[0]["attention"], "mention")
        before = self.snapshot()
        self.assertEqual(self.append(text=text, mentions=mentions, key=key), results[0])
        self.assertEqual(self.snapshot(), before)

    def test_resolved_owner_team_shared_internal_reject_and_reopen_restores(self):
        for actor in ("owner", "team"):
            for visibility in ("shared", "internal"):
                with self.subTest(actor=actor, visibility=visibility):
                    self.lifecycle("resolve")
                    key = self.key()
                    before = self.snapshot()
                    result = self.append(actor, visibility, key=key)
                    self.assertEqual(result, {"status": "error", "error": {"code": "collaboration_resolved"}})
                    self.assertEqual(self.snapshot(), before)
                    self.lifecycle("reopen")
                    self.assertEqual(self.append(actor, visibility, key=key)["status"], "ok")

    def test_guest_shared_projection_hides_mentions_and_internal_and_guest_reply_is_plain(self):
        text, mentions = self.spans([closure.TEAM_ID])
        shared = self.append(text=text, mentions=mentions)
        internal = self.append(visibility="internal", text=text, mentions=mentions)
        self.assertEqual(shared["status"], "ok", shared)
        self.assertEqual(internal["status"], "ok", internal)
        guest = models.build_v2_guest_thread_dto(self.current())
        self.assertIsNotNone(guest)
        encoded = json.dumps(guest)
        self.assertIn(text, encoded)
        for secret in ("mentions", closure.TEAM_ID, "membershipRef", "sourceInvitationId", internal["message"]["id"]):
            self.assertNotIn(secret, encoded)
        result = mutations.append_guest_v2_reply(self.guest_capability(), text, idempotency_key=self.key(), command_transport=self.transport)
        self.assertEqual(result["status"], "ok", result)
        last = self.current()["messages"][-1]
        self.assertNotIn("mentions", last)
        self.assertEqual(self.current()["messages"][0]["mentions"], mentions)
        for user_id in (closure.OWNER_ID, closure.TEAM_ID):
            rows = [row for row in self.notifications(user_id) if row["activityId"] == last["id"]]
            self.assertEqual(len(rows), 1)
            self.assertNotIn("attention", rows[0])

    def test_removal_after_successful_validation_keeps_class_b_history_but_no_access_grant(self):
        text, mentions = self.spans([closure.TEAM_ID])
        self.before_eval = lambda _command: self.members.pop(closure.TEAM_ID)
        result = self.append(text=text, mentions=mentions)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(self.notifications(closure.TEAM_ID)[0]["attention"], "mention")
        self.assert_no_commit(text, mentions, expected="forbidden")

    def test_lua_rejects_nested_object_numbers_bad_spans_and_changed_historical_mentions_atomically(self):
        text, mentions = self.spans([closure.TEAM_ID], prefix="😀 ")
        first = self.append(text=text, mentions=mentions)
        self.assertEqual(first["status"], "ok", first)
        for mode in ("object", "number", "surrogate", "history"):
            with self.subTest(mode=mode):
                key = self.key()
                before = self.snapshot()

                def tamper(command):
                    index = 3 + command[2] + 1
                    replacement = json.loads(command[index])
                    appended = replacement["messages"][-1]
                    if mode == "object": appended["mentions"] = {}
                    elif mode == "number": appended["mentions"][0]["start"] = 3
                    elif mode == "surrogate": appended["mentions"][0]["start"] = "1"
                    else: replacement["messages"][0]["mentions"][0]["userId"] = closure.OWNER_ID
                    command[index] = json.dumps(replacement, ensure_ascii=False, separators=(",", ":"))

                self.before_eval = tamper
                result = self.append(text=text, mentions=mentions, key=key)
                self.assertEqual(result["status"], "error", result)
                self.assertEqual(self.snapshot(), before)

    def test_notification_storage_failure_does_not_commit_activity_mentions(self):
        notification_key = notification_store.build_notification_keys(closure.WORKSPACE_ID, closure.TEAM_ID)[0]
        self.command("SET", notification_key, "wrong-type", "PX", closure.RETENTION_MS)
        text, mentions = self.spans([closure.TEAM_ID])
        self.assert_no_commit(text, mentions)

    def test_python_lua_metadata_cap_escape_and_empty_array_wire_parity(self):
        def metadata(body):
            return [{"userId": closure.TEAM_ID, "start": 0,
                     "end": len(body.encode("utf-16-le")) // 2, "displayText": body}]

        def metadata_size(body):
            return len(json.dumps(metadata(body), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"))

        body = '@é/\\"\n\t😀' + "a" * (models.MAX_V2_MENTION_BYTES - 150)
        body += "a" * (models.MAX_V2_MENTION_BYTES - metadata_size(body))
        self.assertEqual(metadata_size(body), models.MAX_V2_MENTION_BYTES)
        before = self.snapshot()
        for text, expected in ((body, "valid"), (body + "a", "malformed")):
            with self.subTest(bytes=metadata_size(text)):
                mentions = metadata(text)
                message = {"id": "M" * 22, "authorKind": "owner", "authorDisplayName": "Owner Person",
                           "authorUserId": closure.OWNER_ID, "text": text, "visibility": "shared",
                           "createdAt": self.thread["updatedAt"], "mentions": mentions}
                self.assertEqual(models.normalize_v2_message_record(message) is not None, expected == "valid")
                wire = models.encode_v2_wire_record(self.thread, "thread")
                wire["messages"] = [{**message, "createdAt": str(message["createdAt"]), "mentions": [
                    {**mention, "start": str(mention["start"]), "end": str(mention["end"])} for mention in mentions
                ]}]
                raw = json.dumps(wire, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                self.assertEqual(json.loads(self.command("EVAL", redis_store._VALIDATE_V2_WIRE_RECORD_LUA, 0, "thread", raw))["status"], expected)
        for mention_value, expected in (([], "valid"), ({}, "malformed"), (None, "malformed")):
            with self.subTest(mentions=mention_value):
                wire = models.encode_v2_wire_record(self.thread, "thread")
                wire["messages"] = [{"id": "M" * 22, "authorKind": "owner", "authorDisplayName": "Owner Person",
                                     "text": "old body", "visibility": "shared", "createdAt": str(self.thread["updatedAt"]),
                                     "mentions": mention_value}]
                raw = json.dumps(wire, separators=(",", ":"))
                self.assertEqual(json.loads(self.command("EVAL", redis_store._VALIDATE_V2_WIRE_RECORD_LUA, 0, "thread", raw))["status"], expected)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
