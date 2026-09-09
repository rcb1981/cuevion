from __future__ import annotations

import base64
import json
import os
import time
import unittest
from unittest.mock import patch

from api.collaboration import redis_store
from api.collaboration.models import encode_v2_wire_record
from api.collaboration import test_lua_redis_integration as redis_fixture
from . import models, store

WORKSPACE = "wsp_" + "W" * 22
OWNER = "usr_" + "A" * 22
PARTICIPANT = "usr_" + "B" * 21 + "A"
SECOND = "usr_" + "C" * 21 + "A"


def record_fixture(**changes):
    value = {
        "v": 1, "notificationId": "ntf_" + "a" * 40, "workspaceId": WORKSPACE,
        "recipientUserId": PARTICIPANT, "kind": "shared_message", "collaborationId": "A" * 22,
        "mailboxId": "mailbox-1", "sourceRef": {"provider": "google", "providerMessageId": "mail-1"},
        "activityId": "B" * 22, "actor": {"type": "cuevion_user", "userId": OWNER, "displayName": "Owner"},
        "createdAt": 1_800_000_000_000, "expiresAt": 1_800_000_060_000, "readAt": None,
    }
    return {**value, **changes}


class NotificationModelTests(unittest.TestCase):
    def test_all_supported_kinds_and_exact_routing(self):
        for kind in models.NOTIFICATION_KINDS:
            value = record_fixture(kind=kind, activityId="B" * 22 if kind in {"shared_message", "internal_note"} else None)
            with self.subTest(kind=kind):
                self.assertEqual(models.normalize_notification_record(value), value)
                dto = models.notification_dto(value)
                self.assertNotIn("recipientUserId", dto)
                self.assertEqual(models.normalize_notification_dto(dto), dto)

    def test_exact_imap_source_and_strict_source(self):
        source = {"provider": "custom_imap", "folder": "INBOX", "uidValidity": "77", "imapUid": "11"}
        self.assertIsNotNone(models.normalize_notification_record(record_fixture(sourceRef=source)))
        for changed in ({"folder": "Sent"}, {"imapUid": 11}, {"uidValidity": "077"}, {"providerMessageId": "fallback"}):
            self.assertIsNone(models.normalize_notification_record(record_fixture(sourceRef={**source, **changed})))

    def test_malformed_kind_actor_and_leakage_rejected(self):
        invalid = [record_fixture(kind="mention"), record_fixture(kind=[]), record_fixture(actor={}),
                   record_fixture(actor={"type": [], "displayName": "Guest"}),
                   record_fixture(actor={"type": "external_guest", "displayName": "Guest", "email": "guest@example.com"}),
                   record_fixture(actor={"type": "cuevion_user", "displayName": "Owner", "userId": "wrong"})]
        for name in ("text", "body", "email", "bearer", "sourceMessage", "csrfToken"):
            invalid.append(record_fixture(**{name: "sensitive"}))
        for value in invalid:
            self.assertIsNone(models.normalize_notification_record(value))

    def test_self_and_guest_internal_suppressed_by_schema(self):
        self.assertIsNone(models.normalize_notification_record(record_fixture(recipientUserId=OWNER)))
        guest = {"type": "external_guest", "displayName": "Guest"}
        self.assertIsNone(models.normalize_notification_record(record_fixture(kind="internal_note", actor=guest)))
        self.assertIsNotNone(models.normalize_notification_record(record_fixture(actor=guest)))

    def test_expiry_and_read_state_are_strict(self):
        value = record_fixture()
        for field, entry in (("v", True), ("createdAt", 1.5), ("expiresAt", value["createdAt"]),
                             ("expiresAt", value["createdAt"] + models.NOTIFICATION_RETENTION_MS + 1),
                             ("readAt", value["createdAt"] - 1), ("readAt", value["expiresAt"])):
            with self.subTest(field=field, value=entry):
                self.assertIsNone(models.normalize_notification_record({**value, field: entry}))

    def test_strict_wire_rejects_numbers_missing_null_and_duplicates(self):
        value = record_fixture()
        for field in ("v", "createdAt", "expiresAt"):
            value[field] = str(value[field])
        wire = json.dumps(value)
        self.assertIsNotNone(models.decode_notification_wire(wire))
        self.assertIsNone(models.decode_notification_wire(json.dumps(record_fixture())))
        self.assertIsNone(models.decode_notification_wire(wire[:-1] + ',"v":"1"}'))
        del value["readAt"]
        self.assertIsNone(models.decode_notification_wire(json.dumps(value)))


class NotificationStoreRedisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        redis_fixture.ProductionLuaRedisIntegrationTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        redis_fixture.ProductionLuaRedisIntegrationTests.tearDownClass.__func__(cls)

    def setUp(self):
        self.client.command(["FLUSHALL"])
        self.environment = patch.dict(os.environ, {redis_store.V2_INDEX_HMAC_ENV: base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        now = int(time.time() * 1000) - 1000
        self.thread = {
            "v": 2, "collaborationId": "A" * 22, "workspaceId": WORKSPACE,
            "mailboxId": "mailbox-1", "ownerEmail": "owner@example.com",
            "ownerUserId": OWNER, "ownerDisplayName": "Owner",
            "participants": [{"userId": PARTICIPANT, "displayName": "Participant", "membershipRef": "tinv_participant"},
                             {"userId": SECOND, "displayName": "Second", "membershipRef": "tinv_second"}],
            "sourceRef": {"provider": "google", "providerMessageId": "mail-1"},
            "sourceMessage": {"subject": "Review", "senderDisplay": "Sender", "fromDisplay": "Sender", "timestamp": "today", "bodyText": "secret source"},
            "state": "needs_review", "createdAt": now, "updatedAt": now, "messages": [],
        }
        self.actor = {"type": "cuevion_user", "userId": OWNER, "displayName": "Owner"}

    def emit(self, *, kind="shared_message", actor=None, recipients=None, activity="B" * 22,
             ttl=600_000, created=None, event_identity=None, fail_after_prepare=False):
        thread = json.dumps(encode_v2_wire_record(self.thread, "thread"), separators=(",", ":"))
        actor = self.actor if actor is None else actor
        script = redis_store._V2_LUA_COMMON + store.NOTIFICATION_LUA_HELPERS + r"""
local ok, thread = decodeWire(ARGV[1])
if not ok then return cjson.encode({status='malformed'}) end
local actor = cjson.decode(ARGV[3])
local recipients = ARGV[5] == '' and notificationRecipients(thread, actor.userId) or cjson.decode(ARGV[5])
local plan, err = notificationPrepare(thread, ARGV[2], actor, ARGV[4] ~= '' and ARGV[4] or nil,
  recipients, tonumber(ARGV[6]), ARGV[7], ARGV[8] ~= '' and ARGV[8] or nil)
if err then return cjson.encode({status=err}) end
if ARGV[9] == 'fail' then return cjson.encode({status='aborted'}) end
redis.call('SET', KEYS[1], 'canonical-commit')
notificationCommit(plan)
return cjson.encode({status='ok',recipients=#plan})
"""
        return json.loads(self.client.command(["EVAL", script, 1, "{cuevion-collab-v2}:test-canonical",
            thread, kind, json.dumps(actor), activity or "", json.dumps(recipients) if recipients is not None else "",
            str(ttl), str(created if created is not None else self.thread["createdAt"]), event_identity or "",
            "fail" if fail_after_prepare else ""]))

    def listing(self, user=PARTICIPANT, **options):
        return store.list_notifications(WORKSPACE, user, command_transport=self.client.transport, **options)

    def test_owner_actor_suppressed_and_participants_receive(self):
        self.assertEqual(self.emit(), {"status": "ok", "recipients": 2})
        self.assertEqual(self.listing(OWNER)["notifications"], [])
        for user in (PARTICIPANT, SECOND):
            result = self.listing(user)
            self.assertEqual(result["unreadCount"], 1)
            self.assertEqual(result["notifications"][0]["activityId"], "B" * 22)
            self.assertNotIn("recipientUserId", result["notifications"][0])

    def test_participant_actor_and_duplicate_recipient_suppression(self):
        actor = {"type": "cuevion_user", "userId": PARTICIPANT, "displayName": "Participant"}
        result = self.emit(actor=actor, recipients=[OWNER, OWNER, PARTICIPANT, SECOND])
        self.assertEqual(result["recipients"], 2)
        self.assertEqual(self.listing(PARTICIPANT)["unreadCount"], 0)
        self.assertEqual(self.listing(OWNER)["unreadCount"], 1)

    def test_guest_reply_notifies_owner_and_team_with_body_free_actor(self):
        actor = {"type": "external_guest", "displayName": "Guest reviewer"}
        self.assertEqual(self.emit(actor=actor)["recipients"], 3)
        for user in (OWNER, PARTICIPANT, SECOND):
            row = self.listing(user)["notifications"][0]
            self.assertEqual(row["actor"], actor)
            encoded = json.dumps(row)
            for forbidden in ("secret source", "owner@example.com", "bearer", "guest@example.com"):
                self.assertNotIn(forbidden, encoded)

    def test_start_owner_only_and_participant_add_identity(self):
        self.assertEqual(self.emit(kind="collaboration_started", activity=None, recipients=[OWNER, PARTICIPANT])["recipients"], 1)
        self.assertEqual(self.emit(kind="collaboration_started", activity=None, recipients=[OWNER])["recipients"], 0)
        self.emit(kind="participant_added", activity=None, recipients=[SECOND], event_identity="tinv_second")
        self.emit(kind="participant_added", activity=None, recipients=[SECOND], event_identity="tinv_second")
        self.assertEqual(self.listing(SECOND)["unreadCount"], 1)
        self.emit(kind="participant_added", activity=None, recipients=[SECOND], event_identity="tinv_rejoined")
        self.assertEqual(self.listing(SECOND)["unreadCount"], 2)

    def test_internal_note_no_text_and_external_actor_rejected(self):
        self.assertEqual(self.emit(kind="internal_note")["status"], "ok")
        row = self.listing()["notifications"][0]
        self.assertEqual(row["kind"], "internal_note")
        self.assertNotIn("text", row)
        self.assertEqual(self.emit(kind="internal_note", actor={"type": "external_guest", "displayName": "Guest"})["status"], "malformed")

    def test_deterministic_replay_preserves_id_read_and_expiry(self):
        self.emit()
        first = self.listing()["notifications"][0]
        marked = store.mark_read(WORKSPACE, PARTICIPANT, first["notificationId"], command_transport=self.client.transport)
        before = self.client.command(["PTTL", store.build_notification_keys(WORKSPACE, PARTICIPANT)[0]])
        self.emit(ttl=900_000)
        after = self.listing()["notifications"][0]
        self.assertEqual(after, marked["notification"])
        self.assertEqual(after["expiresAt"], first["expiresAt"])
        self.assertLessEqual(self.client.command(["PTTL", store.build_notification_keys(WORKSPACE, PARTICIPANT)[0]]), before)

    def test_notification_expiry_obeys_actual_ttl_and_180_days(self):
        before = int(time.time() * 1000)
        self.emit(ttl=60_000)
        after = int(time.time() * 1000)
        record = self.listing()["notifications"][0]
        self.assertGreaterEqual(record["expiresAt"], before + 60_000)
        self.assertLessEqual(record["expiresAt"], after + 60_000)
        self.assertLessEqual(record["expiresAt"], record["createdAt"] + models.NOTIFICATION_RETENTION_MS)

    def test_preflight_failure_has_no_partial_canonical_or_recipient_write(self):
        keys = store.build_notification_keys(WORKSPACE, SECOND)
        self.client.command(["SET", keys[1], "wrong type"])
        self.assertEqual(self.emit()["status"], "malformed")
        self.assertIsNone(self.client.command(["GET", "{cuevion-collab-v2}:test-canonical"]))
        self.assertEqual(self.listing(PARTICIPANT)["notifications"], [])

    def test_prepare_does_not_write_and_store_corruption_is_unavailable(self):
        self.assertEqual(self.emit(fail_after_prepare=True)["status"], "aborted")
        self.assertEqual(self.listing()["notifications"], [])
        self.emit()
        keys = store.build_notification_keys(WORKSPACE, PARTICIPANT)
        notification_id = self.listing()["notifications"][0]["notificationId"]
        self.client.command(["HSET", keys[0], notification_id, '{"malformed":true}'])
        self.assertEqual(self.listing()["status"], "unavailable")
        self.client.command(["DEL", "{cuevion-collab-v2}:test-canonical"])
        self.assertEqual(self.emit(activity="C" * 22)["status"], "malformed")
        self.assertIsNone(self.client.command(["GET", "{cuevion-collab-v2}:test-canonical"]))

    def test_unread_orphan_fails_full_preflight(self):
        self.emit()
        row = self.listing()["notifications"][0]
        store.mark_read(WORKSPACE, PARTICIPANT, row["notificationId"], command_transport=self.client.transport)
        keys = store.build_notification_keys(WORKSPACE, PARTICIPANT)
        self.client.command(["ZADD", keys[2], row["expiresAt"], "ntf_" + "f" * 40])
        self.client.command(["PEXPIREAT", keys[2], row["expiresAt"]])
        self.assertEqual(self.listing()["status"], "unavailable")
        self.client.command(["DEL", "{cuevion-collab-v2}:test-canonical"])
        self.assertEqual(self.emit(activity="C" * 22)["status"], "malformed")
        self.assertIsNone(self.client.command(["GET", "{cuevion-collab-v2}:test-canonical"]))

    def test_list_stable_cursor_and_new_arrivals(self):
        for index in range(4):
            self.emit(activity=f"{index:022d}", created=self.thread["createdAt"] + index)
        first = self.listing(limit=2)
        self.assertEqual(len(first["notifications"]), 2)
        self.assertIsInstance(first["nextCursor"], str)
        self.emit(activity="Z" * 22, created=self.thread["createdAt"] + 10)
        second = self.listing(limit=2, cursor=first["nextCursor"])
        self.assertEqual(len(second["notifications"]), 2)
        self.assertIsNone(second["nextCursor"])
        self.assertTrue(set(row["notificationId"] for row in first["notifications"]).isdisjoint(row["notificationId"] for row in second["notifications"]))
        self.assertEqual(self.listing(SECOND, cursor=first["nextCursor"])["status"], "malformed")
        self.assertEqual(self.listing(cursor=first["nextCursor"] + "x")["status"], "malformed")
        self.assertEqual(self.listing(limit=51)["status"], "malformed")

    def test_exact_mark_read_foreign_id_idempotence_and_no_collaboration_mutation(self):
        self.emit()
        row = self.listing()["notifications"][0]
        notification_id = row["notificationId"]
        foreign = store.mark_read(WORKSPACE, SECOND, notification_id, command_transport=self.client.transport)
        self.assertEqual(foreign["status"], "not_found")
        self.client.command(["SET", "{cuevion-collab-v2}:test-canonical", "unchanged"])
        first = store.mark_read(WORKSPACE, PARTICIPANT, notification_id, command_transport=self.client.transport)
        repeat = store.mark_read(WORKSPACE, PARTICIPANT, notification_id, command_transport=self.client.transport)
        self.assertEqual(first, repeat)
        self.assertEqual(first["unreadCount"], 0)
        self.assertIsInstance(first["notification"]["readAt"], int)
        self.assertEqual(self.client.command(["GET", "{cuevion-collab-v2}:test-canonical"]), "unchanged")
        self.assertEqual(store.summary(WORKSPACE, PARTICIPANT, command_transport=self.client.transport)["unreadCount"], 0)

    def test_expired_records_never_listed_counted_or_revived(self):
        self.emit(ttl=50)
        row = self.listing()["notifications"][0]
        self.emit(activity="C" * 22, ttl=60_000)
        time.sleep(0.06)
        self.assertEqual(self.listing()["unreadCount"], 1)
        self.assertEqual(len(self.listing()["notifications"]), 1)
        self.assertEqual(store.summary(WORKSPACE, PARTICIPANT, command_transport=self.client.transport)["unreadCount"], 1)
        self.assertEqual(store.mark_read(WORKSPACE, PARTICIPANT, row["notificationId"], command_transport=self.client.transport)["status"], "not_found")

    def test_bound_prunes_deterministic_oldest_without_failure(self):
        # Seed a valid maximum-size fixture without 1,000 quadratic preparations.
        self.emit(recipients=[PARTICIPANT])
        keys = store.build_notification_keys(WORKSPACE, PARTICIPANT)
        value = self.listing()["notifications"][0]
        value["recipientUserId"] = PARTICIPANT
        self.client.command(["DEL", *keys])
        args = []
        for index in range(1_000):
            row = {**value, "notificationId": "ntf_" + f"{index:040x}", "activityId": f"{index:022d}", "createdAt": value["createdAt"] + index}
            for field in ("v", "createdAt", "expiresAt"):
                row[field] = str(row[field])
            args.extend([row["notificationId"], json.dumps(row), row["createdAt"], row["expiresAt"]])
        script = "for i=1,#ARGV,4 do redis.call('HSET',KEYS[1],ARGV[i],ARGV[i+1]); redis.call('ZADD',KEYS[2],ARGV[i+2],ARGV[i]); redis.call('ZADD',KEYS[3],ARGV[i+3],ARGV[i]); end; for _,key in ipairs(KEYS) do redis.call('PEXPIRE',key,600000) end; return 1"
        self.client.command(["EVAL", script, 3, *keys, *args])
        self.assertEqual(self.emit(activity="Z" * 22, recipients=[PARTICIPANT], created=value["createdAt"] + 1_001)["status"], "ok")
        self.assertEqual(self.client.command(["HLEN", keys[0]]), 1_000)
        self.assertIsNone(self.client.command(["HGET", keys[0], "ntf_" + "0" * 40]))
        self.assertEqual(store.summary(WORKSPACE, PARTICIPANT, command_transport=self.client.transport)["unreadCount"], 1_000)
        self.assertEqual(self.emit(activity="Y" * 22, recipients=[PARTICIPANT], created=value["createdAt"] - 1)["status"], "ok")
        self.assertEqual(self.client.command(["HLEN", keys[0]]), 1_000)
        self.assertIsNone(self.client.command(["HGET", keys[0], "ntf_" + f"{1:040x}"]))

    def test_summary_uses_no_record_fetch_and_empty_payload(self):
        self.emit()
        self.client.command(["CONFIG", "RESETSTAT"])
        result = store.summary(WORKSPACE, PARTICIPANT, command_transport=self.client.transport)
        stats = self.client.command(["INFO", "commandstats"])
        self.assertEqual(result, {"status": "ok", "v": 1, "unreadCount": 1})
        self.assertNotIn("cmdstat_hgetall:", stats)
        self.assertNotIn("cmdstat_zrange:", stats)
        self.assertIn("cmdstat_zcount:calls=1", stats)
        self.assertIn("cmdstat_eval:calls=1", stats)

    def test_hosted_null_loss_keeps_nullable_record_contract(self):
        original = self.client.command
        for exported in (True, False):
            with self.subTest(null_sentinel=exported):
                original(["FLUSHALL"])
                def hosted(command):
                    if command[0] == "EVAL":
                        command = list(command)
                        prefix = (
                            "local originalCjson = cjson\n"
                            "local cjson = {encode=originalCjson.encode,null=" + ("originalCjson.null" if exported else "nil") + "}\n"
                            "cjson.decode=function(raw) local value=originalCjson.decode(raw); "
                            "if type(value)=='table' and value.notificationId then "
                            "for _,key in ipairs({'activityId','readAt'}) do if value[key]==originalCjson.null then value[key]=nil end end end; return value end\n"
                        )
                        command[1] = prefix + command[1]
                    return original(command)
                with patch.object(self.client, "command", side_effect=hosted):
                    self.assertEqual(self.emit(kind="collaboration_started", activity=None)["status"], "ok")
                    row = self.listing()["notifications"][0]
                    self.assertIsNone(row["activityId"])
                    self.assertIsNone(row["readAt"])
                    result = store.mark_read(WORKSPACE, PARTICIPANT, row["notificationId"], command_transport=self.client.transport)
                    self.assertIsInstance(result["notification"]["readAt"], int)
                    self.assertIsNone(result["notification"]["activityId"])
                    self.assertEqual(self.emit(kind="collaboration_started", activity=None)["status"], "ok")
                    self.assertEqual(self.listing()["notifications"][0], result["notification"])

    def test_absolute_canonical_expiry_is_never_extended(self):
        thread_key = redis_store.build_v2_thread_key(self.thread["collaborationId"])
        self.client.command(["SET", thread_key, "canonical", "PX", 60_000])
        absolute = self.client.command(["PEXPIRETIME", thread_key])
        thread = json.dumps(encode_v2_wire_record(self.thread, "thread"))
        script = redis_store._V2_LUA_COMMON + store.NOTIFICATION_LUA_HELPERS + r"""
local _, thread = decodeWire(ARGV[1])
local actor = cjson.decode(ARGV[2])
local ttl = redis.call('PTTL', KEYS[1])
local ignored = 0; for index=1,100000 do ignored=ignored+index end
local plan, err = notificationPrepare(thread,'shared_message',actor,'BBBBBBBBBBBBBBBBBBBBBB',
  notificationRecipients(thread,actor.userId),ttl,thread.createdAt,nil,KEYS[1])
if err then return cjson.encode({status=err}) end
notificationCommit(plan)
return cjson.encode({status='ok'})
"""
        result = json.loads(self.client.command(["EVAL", script, 1, thread_key, thread, json.dumps(self.actor)]))
        self.assertEqual(result["status"], "ok")
        for user in (PARTICIPANT, SECOND):
            row = self.listing(user)["notifications"][0]
            self.assertLessEqual(row["expiresAt"], absolute)
        self.assertEqual(self.client.command(["PEXPIRETIME", thread_key]), absolute)

    def test_future_activity_clock_uses_commit_time_and_can_mark_read_immediately(self):
        before = int(time.time() * 1000)
        self.assertEqual(self.emit(created=before + 60_000)["status"], "ok")
        row = self.listing()["notifications"][0]
        self.assertGreaterEqual(row["createdAt"], before)
        self.assertLessEqual(row["createdAt"], int(time.time() * 1000))
        marked = store.mark_read(WORKSPACE, PARTICIPANT, row["notificationId"], command_transport=self.client.transport)
        self.assertEqual(marked["status"], "ok")
        self.assertGreaterEqual(marked["notification"]["readAt"], row["createdAt"])

    def test_no_scan_or_background_cleanup_in_new_store(self):
        source = store.NOTIFICATION_LUA_HELPERS + store._LIST + store._SUMMARY + store._MARK_READ
        for forbidden in ("redis.call('SCAN'", "redis.call('KEYS'", "PERSIST"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
