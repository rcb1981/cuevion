"""Exact-boundary discovery repairs against an isolated local Redis server."""
from __future__ import annotations

import copy
import base64
import json
import os
import unittest
from contextlib import ExitStack
from dataclasses import replace
from unittest.mock import patch

from . import application, authorization, models, mutations, redis_store as store
from . import test_lua_redis_integration as fixtures
from . import test_summary as summaries


class DiscoveryRepairRedisTests(unittest.TestCase):
    setUpClass = classmethod(summaries.SummaryRedisTests.setUpClass.__func__)
    tearDownClass = classmethod(summaries.SummaryRedisTests.tearDownClass.__func__)
    setUp = summaries.SummaryRedisTests.setUp
    transport = summaries.SummaryRedisTests.transport
    create = summaries.SummaryRedisTests.create
    index = summaries.SummaryRedisTests.index
    page = summaries.SummaryRedisTests.page
    snapshot = summaries.SummaryRedisTests.snapshot

    def value(self, number=40, *, provider="custom_imap", team=False, legacy=False, state="needs_review"):
        source = ({"provider": "custom_imap", "folder": "INBOX", "uidValidity": "91", "imapUid": str(number)}
                  if provider == "custom_imap" else {"provider": "google", "providerMessageId": f"exact-{number}"})
        value = summaries.thread(number, sourceRef=source, state=state)
        if team:
            value["participants"] = [{"userId": summaries.PARTICIPANT, "displayName": "Participant", "membershipRef": "tinv_original"}]
        if legacy:
            for field in ("ownerUserId", "ownerDisplayName", "participants"):
                del value[field]
        return value

    def keys(self, value):
        return [store.build_v2_thread_key(value["collaborationId"]),
                store.build_v2_source_thread_key(value["ownerEmail"], value["mailboxId"], value["sourceRef"])]

    def expiry(self, keys):
        return [self.client.command(["PEXPIRETIME", key]) for key in keys]

    def read(self, value, *, participant=False, membership_ref="tinv_original"):
        cap = summaries.capability(value)
        if participant:
            cap = replace(cap, viewer_access="participant", actor_kind="internal", actor_user_id=summaries.PARTICIPANT)
        with ExitStack() as stack:
            stack.enter_context(patch.object(application, "resolve_verified_owner_collaboration_context", return_value={"status": "ok", "context": cap, "error": None}))
            stack.enter_context(patch.object(application, "_load_v2_thread", side_effect=lambda *a, **kw: store._load_v2_thread(*a, **kw, command_transport=self.transport)))
            stack.enter_context(patch.object(application, "_enrich_v2_discovery", side_effect=lambda *a, **kw: store._enrich_v2_discovery(*a, **kw, command_transport=self.transport)))
            stack.enter_context(patch.object(application, "_load_v2_external_guest_records", return_value={"status": "ok", "records": []}))
            stack.enter_context(patch.object(application, "_resolve_active_team_member", side_effect=lambda _workspace, user: ({"memberUserId": user, "sourceInvitationId": "tinv_original"}, None)))
            stack.enter_context(patch.object(authorization, "_resolve_active_team_member", side_effect=lambda _workspace, user: (
                ({"memberUserId": user, "sourceInvitationId": membership_ref}, None) if membership_ref else (None, "not_active"))))
            return application.read_v2_collaboration_for_verified_owner(object(), (), value["collaborationId"], owner_security_configuration=object())

    def guest_create(self, value):
        cap = replace(summaries.capability(value), action="create", collaboration_id=None)
        with ExitStack() as stack:
            stack.enter_context(patch.object(application, "resolve_verified_owner_collaboration_context", return_value={"status": "ok", "context": cap, "error": None}))
            stack.enter_context(patch.object(application, "resolve_source_message", return_value={"status": "ok", "source": {"sourceRef": value["sourceRef"], "sourceMessage": value["sourceMessage"]}, "error": None}))
            stack.enter_context(patch.object(application, "_create_v2_thread_with_guest", side_effect=lambda *a, **kw: store._create_v2_thread_with_guest(*a, **kw, command_transport=self.transport)))
            stack.enter_context(patch.object(application, "_enrich_v2_discovery", side_effect=lambda *a, **kw: store._enrich_v2_discovery(*a, **kw, command_transport=self.transport)))
            stack.enter_context(patch.object(application, "_load_v2_external_guest_records", return_value={"status": "ok", "records": []}))
            stack.enter_context(patch.object(application.time, "time_ns", return_value=(fixtures.SEC + 110) * 1_000_000_000))
            return application.create_v2_collaboration_with_guest_for_verified_owner(object(), (), {
                "mailboxId": value["mailboxId"], "sourceRef": {k: v for k, v in value["sourceRef"].items() if k != "provider"}, "state": "needs_review",
            }, owner_security_configuration=object())

    def ordinary_create(self, value):
        cap = replace(summaries.capability(value), action="create", collaboration_id=None)
        with ExitStack() as stack:
            stack.enter_context(patch.object(application, "resolve_verified_owner_collaboration_context", return_value={"status": "ok", "context": cap, "error": None}))
            stack.enter_context(patch.object(application, "resolve_source_message", return_value={"status": "ok", "source": {"sourceRef": value["sourceRef"], "sourceMessage": value["sourceMessage"]}, "error": None}))
            stack.enter_context(patch.object(application, "_resolve_active_team_member", side_effect=lambda _workspace, user: ({"memberUserId": user, "sourceInvitationId": "tinv_original", "displayName": "Participant"}, None)))
            stack.enter_context(patch.object(application, "_create_v2_thread", side_effect=lambda *a, **kw: store._create_v2_thread(*a, **kw, command_transport=self.transport)))
            stack.enter_context(patch.object(application, "_load_v2_thread", side_effect=lambda *a, **kw: store._load_v2_thread(*a, **kw, command_transport=self.transport)))
            stack.enter_context(patch.object(application, "_enrich_v2_discovery", side_effect=lambda *a, **kw: store._enrich_v2_discovery(*a, **kw, command_transport=self.transport)))
            stack.enter_context(patch.object(application, "_load_v2_external_guest_records", return_value={"status": "ok", "records": []}))
            stack.enter_context(patch.object(application.time, "time_ns", return_value=(fixtures.SEC + 110) * 1_000_000_000))
            return application.create_v2_collaboration_for_verified_owner(object(), (), {
                "mailboxId": value["mailboxId"], "sourceRef": {k: v for k, v in value["sourceRef"].items() if k != "provider"},
                "state": "needs_review", "participantUserId": summaries.PARTICIPANT,
            }, owner_security_configuration=object())

    def command_counts(self):
        raw = self.client.command(["INFO", "commandstats"])
        if isinstance(raw, bytes):
            raw = raw.decode()
        counts = {}
        for row in raw.splitlines():
            if not row.startswith("cmdstat_"):
                continue
            name, details = row.split(":", 1)
            counts[name.removeprefix("cmdstat_")] = int(dict(part.split("=", 1) for part in details.split(","))["calls"])
        return counts

    def writes(self):
        counts = self.command_counts()
        return {name: counts.get(name, 0) for name in ("set", "psetex", "hset", "hdel", "expire", "pexpire", "persist", "del")}

    def test_production_three_google_summaries_and_openable_imap_repairs_on_exact_read(self):
        unrelated = [self.value(i, provider="google") for i in (1, 2, 3)]
        for value in unrelated:
            self.create(value)
        target = self.value()
        self.create(target)
        self.client.command(["HDEL", self.index(), target["collaborationId"]])
        before = self.page()
        self.assertEqual(len(before["summaries"]), 3)
        self.assertIsNone(before["nextCursor"])
        self.assertTrue(all(entry["sourceRef"]["provider"] == "google" for entry in before["summaries"]))
        keys = self.keys(target)
        self.client.command(["PEXPIRE", keys[0], 100_000])
        self.client.command(["PEXPIRE", keys[1], 100_000])
        expiry, canonical = self.expiry(keys), self.snapshot(keys)
        unrelated_fields = [self.client.command(["HGET", self.index(), item["collaborationId"]]) for item in unrelated]
        result = self.read(target)
        self.assertEqual(result.get("status"), "ok", result)
        self.assertEqual(result["collaboration"]["collaborationId"], target["collaborationId"])
        after = self.page()["summaries"]
        self.assertEqual([entry["sourceRef"] for entry in after if entry["collaborationId"] == target["collaborationId"]], [target["sourceRef"]])
        self.assertEqual(self.expiry(keys), expiry)
        self.assertEqual(self.snapshot(keys), canonical)
        self.assertEqual([self.client.command(["HGET", self.index(), item["collaborationId"]]) for item in unrelated], unrelated_fields)

    def test_existing_guest_create_repairs_missing_discovery_for_both_providers(self):
        for index, provider in enumerate(("google", "custom_imap"), 50):
            with self.subTest(provider=provider):
                value = self.value(index, provider=provider)
                first = self.guest_create(value)
                self.assertTrue(first.get("created"), first)
                actual = store._load_v2_thread(first["collaboration"]["collaborationId"], command_transport=self.transport).record
                keys = self.keys(actual)
                self.client.command(["HDEL", self.index(), actual["collaborationId"]])
                expiry, canonical = self.expiry(keys), self.snapshot(keys)
                repeated = self.guest_create(value)
                self.assertFalse(repeated.get("created"), repeated)
                self.assertFalse(repeated.get("invitationCreated"), repeated)
                self.assertEqual(repeated["collaboration"]["collaborationId"], actual["collaborationId"])
                self.assertIn(actual["collaborationId"], [entry["collaborationId"] for entry in self.page()["summaries"]])
                self.assertEqual(self.expiry(keys), expiry)
                self.assertEqual(self.snapshot(keys), canonical)

    def test_ordinary_repeated_create_never_refreshes_canonical_source_or_discovery_expiry(self):
        for index, provider in enumerate(("google", "custom_imap"), 60):
            with self.subTest(provider=provider):
                value = self.value(index, provider=provider, team=True)
                self.create(value)
                keys = self.keys(value) + [self.index(), self.index(summaries.PARTICIPANT)]
                for key in keys:
                    self.client.command(["PEXPIRE", key, 100_000])
                expiry, records = self.expiry(keys), self.snapshot(keys)
                repeated = self.create({**value, "collaborationId": f"{index + 100:022d}"})
                self.assertFalse(repeated.created)
                self.assertEqual(repeated.record, value)
                self.assertEqual(self.expiry(keys), expiry)
                self.assertEqual(self.snapshot(keys), records)

    def test_exact_read_repairs_missing_malformed_and_wrong_digest_values(self):
        for number, (provider, corrupt) in enumerate(((p, c) for p in ("google", "custom_imap") for c in ("missing", "malformed", "digest")), 70):
            with self.subTest(provider=provider, corrupt=corrupt):
                self.client.command(["FLUSHALL"])
                value = self.value(number, provider=provider)
                self.create(value)
                if corrupt == "missing":
                    self.client.command(["HDEL", self.index(), value["collaborationId"]])
                else:
                    raw = self.client.command(["HGET", self.index(), value["collaborationId"]])
                    if corrupt == "digest":
                        payload = json.loads(raw)
                        payload["threadHash"] = "0" * 40
                        raw = json.dumps(payload)
                    else:
                        raw = "malformed"
                    self.client.command(["HSET", self.index(), value["collaborationId"], raw])
                expiry, canonical = self.expiry(self.keys(value)), self.snapshot(self.keys(value))
                result = self.read(value)
                self.assertEqual(result.get("status"), "ok", result)
                self.assertIn(value["collaborationId"], [entry["collaborationId"] for entry in self.page()["summaries"]])
                self.assertEqual(self.expiry(self.keys(value)), expiry)
                self.assertEqual(self.snapshot(self.keys(value)), canonical)

    def test_new_google_and_imap_owner_only_team_and_guest_enroll_atomically(self):
        for number, (provider, audience) in enumerate(((p, a) for p in ("google", "custom_imap") for a in ("owner", "team", "guest")), 100):
            with self.subTest(provider=provider, audience=audience):
                value = self.value(number, provider=provider, team=audience == "team")
                if audience == "owner":
                    result = self.create(value)
                    self.assertTrue(result.created)
                    collaboration_id = result.record["collaborationId"]
                else:
                    result = self.ordinary_create(value) if audience == "team" else self.guest_create(value)
                    self.assertTrue(result.get("created"), result)
                    collaboration_id = result["collaboration"]["collaborationId"]
                owner = next(entry for entry in self.page()["summaries"] if entry["collaborationId"] == collaboration_id)
                self.assertEqual(owner["sourceRef"], value["sourceRef"])
                self.assertEqual(owner["mailboxId"], value["mailboxId"])
                if audience == "team":
                    participant = self.page(summaries.PARTICIPANT, membership_ref="tinv_original")["summaries"]
                    self.assertIn(collaboration_id, [entry["collaborationId"] for entry in participant])

    def test_existing_team_create_repairs_owner_and_participant_without_canonical_mutation(self):
        for number, provider in enumerate(("google", "custom_imap"), 120):
            with self.subTest(provider=provider):
                value = self.value(number, provider=provider, team=True)
                result = self.ordinary_create(value)
                self.assertTrue(result.get("created"), result)
                current = store._load_v2_thread(result["collaboration"]["collaborationId"], command_transport=self.transport).record
                for user in (summaries.OWNER, summaries.PARTICIPANT):
                    self.client.command(["HDEL", self.index(user), current["collaborationId"]])
                keys = self.keys(current)
                for key in keys:
                    self.client.command(["PEXPIRE", key, 100_000])
                expiry, original = self.expiry(keys), self.snapshot(keys)
                repeated = self.ordinary_create(value)
                self.assertIs(repeated.get("created"), False, repeated)
                self.assertEqual(repeated["collaboration"]["collaborationId"], current["collaborationId"])
                self.assertIn(current["collaborationId"], [entry["collaborationId"] for entry in self.page()["summaries"]])
                self.assertIn(current["collaborationId"], [entry["collaborationId"] for entry in self.page(summaries.PARTICIPANT, membership_ref="tinv_original")["summaries"]])
                self.assertEqual(self.expiry(keys), expiry)
                self.assertEqual(self.snapshot(keys), original)

    def test_participant_exact_read_repairs_only_current_viewer_discovery(self):
        value = self.value(team=True)
        self.create(value)
        for user in (summaries.OWNER, summaries.PARTICIPANT):
            self.client.command(["HDEL", self.index(user), value["collaborationId"]])
        keys = self.keys(value)
        expiry, original = self.expiry(keys), self.snapshot(keys)
        result = self.read(value, participant=True)
        self.assertEqual(result.get("status"), "ok", result)
        self.assertEqual(self.page()["summaries"], [])
        page = self.page(summaries.PARTICIPANT, membership_ref="tinv_original")["summaries"]
        self.assertEqual([entry["collaborationId"] for entry in page], [value["collaborationId"]])
        self.assertEqual(page[0]["viewerAccess"], "participant")
        self.assertEqual(self.expiry(keys), expiry)
        self.assertEqual(self.snapshot(keys), original)

    def test_stale_or_rebound_team_membership_is_not_enrolled(self):
        value = self.value(team=True)
        self.create(value)
        self.client.command(["HDEL", self.index(summaries.PARTICIPANT), value["collaborationId"]])
        keys = self.keys(value) + [self.index(), self.index(summaries.PARTICIPANT)]
        original = self.snapshot(keys)
        for ref in (None, "tinv_reinvited"):
            with self.subTest(ref=ref):
                result = self.read(value, participant=True, membership_ref=ref)
                self.assertNotEqual(result.get("status"), "ok", result)
                self.assertEqual(self.snapshot(keys), original)

    def test_owner_read_does_not_enroll_unrelated_participant(self):
        value = self.value(team=True)
        self.create(value)
        for user in (summaries.OWNER, summaries.PARTICIPANT):
            self.client.command(["HDEL", self.index(user), value["collaborationId"]])
        self.assertEqual(self.read(value).get("status"), "ok")
        self.assertEqual(len(self.page()["summaries"]), 1)
        self.assertEqual(self.client.command(["HGET", self.index(summaries.PARTICIPANT), value["collaborationId"]]), None)

    def test_legacy_owner_read_enrichment_preserves_expiry_and_content(self):
        for number, provider in enumerate(("google", "custom_imap"), 130):
            with self.subTest(provider=provider):
                value = self.value(number, provider=provider, legacy=True)
                self.create(value)
                keys = self.keys(value)
                self.client.command(["PEXPIRE", keys[0], 100_000])
                self.client.command(["PEXPIRE", keys[1], 100_000])
                expiry = self.expiry(keys)
                result = self.read(value)
                self.assertEqual(result.get("status"), "ok", result)
                current = store._load_v2_thread(value["collaborationId"], command_transport=self.transport).record
                self.assertEqual({field: current[field] for field in value}, value)
                self.assertEqual(current["ownerUserId"], summaries.OWNER)
                self.assertEqual(current["participants"], [])
                self.assertEqual(self.expiry(keys), expiry)
                self.assertIn(value["collaborationId"], [entry["collaborationId"] for entry in self.page()["summaries"]])

    def test_healthy_read_and_guest_retry_do_not_write_or_refresh_discovery_authority(self):
        value = self.value()
        first = self.guest_create(value)
        actual = store._load_v2_thread(first["collaboration"]["collaborationId"], command_transport=self.transport).record
        keys = self.keys(actual) + [self.index()]
        expiry, original, counts = self.expiry(keys), self.snapshot(keys), self.writes()
        self.assertEqual(self.read(actual).get("status"), "ok")
        self.assertEqual(self.writes(), counts)
        self.assertIs(self.guest_create(value).get("created"), False)
        # The frozen guest invitation path rewrites its separate guest-history
        # index once. Discovery repair adds no writes; canonical/source/index
        # bytes and absolute expiration stay identical.
        self.assertEqual(self.writes(), {**counts, "set": counts["set"] + 1})
        self.assertEqual(self.expiry(keys), expiry)
        self.assertEqual(self.snapshot(keys), original)

    def test_repair_bounded_discovery_retention_never_extends_authority(self):
        value = self.value()
        self.create(value)
        self.client.command(["DEL", self.index()])
        keys = self.keys(value)
        self.client.command(["PEXPIRE", keys[0], 100_000])
        self.client.command(["PEXPIRE", keys[1], 100_000])
        expiry = self.expiry(keys)
        self.assertEqual(self.read(value).get("status"), "ok")
        self.assertEqual(self.expiry(keys), expiry)
        self.assertGreater(self.client.command(["PTTL", self.index()]), 0)
        self.assertLessEqual(self.expiry([self.index()])[0], expiry[0])

    def test_resolved_repair_and_reopen_remain_authoritative(self):
        value = self.value(state="resolved")
        self.create(value)
        self.client.command(["HDEL", self.index(), value["collaborationId"]])
        self.assertEqual(self.read(value).get("status"), "ok")
        self.assertEqual(self.page()["summaries"][0]["state"], "resolved")
        for operation, expected in (("reopen", "note_only"), ("resolve", "resolved")):
            result = mutations.transition_v2_lifecycle(replace(summaries.capability(value), action=operation), operation=operation,
                expected_state=value["state"], expected_updated_at=value["updatedAt"], command_transport=self.transport)
            self.assertEqual(result.get("status"), "ok", result)
            value = result["record"]
            self.assertEqual(self.page()["summaries"][0]["state"], expected)

    def test_race_immediately_before_repair_never_enrolls_stale_authority(self):
        for scenario in ("resolve", "digest", "mailbox", "owner", "workspace", "source", "expired", "malformed"):
            with self.subTest(scenario=scenario):
                self.client.command(["FLUSHALL"])
                value = self.value()
                self.create(value)
                self.client.command(["DEL", self.index()])
                keys = self.keys(value)
                snapshots = []
                def race(command):
                    current = copy.deepcopy(value)
                    if scenario == "source":
                        self.client.command(["SET", keys[1], "Z" * 22, "KEEPTTL"])
                    elif scenario == "expired":
                        self.client.command(["PEXPIREAT", keys[0], 1])
                    elif scenario == "malformed":
                        self.client.command(["SET", keys[0], '{"v":"2"}', "KEEPTTL"])
                    else:
                        updates = {"resolve": {"state": "resolved"}, "digest": {"updatedAt": value["updatedAt"] + 1},
                                   "mailbox": {"mailboxId": "other-mailbox"}, "owner": {"ownerUserId": summaries.OTHER},
                                   "workspace": {"workspaceId": fixtures.OTHER_WORKSPACE_ID}}
                        current.update(updates[scenario])
                        self.client.command(["SET", keys[0], fixtures.wire_json(current, "thread"), "KEEPTTL"])
                    snapshots.append(self.snapshot(keys + [self.index()]))
                    return self.transport(command)
                result = store._enrich_v2_discovery(value, summaries.capability(value), command_transport=race)
                self.assertNotEqual(result.get("status"), "ok", result)
                self.assertEqual(len(snapshots), 1)
                self.assertEqual(self.snapshot(keys + [self.index()]), snapshots[0])

    def test_wrong_owner_workspace_mailbox_and_provider_are_denied_before_repair(self):
        value = self.value()
        self.create(value)
        self.client.command(["DEL", self.index()])
        keys = self.keys(value) + [self.index()]
        original = self.snapshot(keys)
        for changes in ({"workspace_id": fixtures.OTHER_WORKSPACE_ID}, {"mailbox_id": "other-mailbox"},
                        {"mailbox_provider": "google"}, {"owner_email": "other@example.com"},
                        {"actor_user_id": summaries.OTHER, "owner_user_id": summaries.OTHER}):
            with self.subTest(changes=changes):
                result = store._enrich_v2_discovery(value, replace(summaries.capability(value), **changes), command_transport=self.transport)
                self.assertNotEqual(result.get("status"), "ok", result)
                self.assertEqual(self.snapshot(keys), original)

    def test_idempotent_team_and_guest_create_repair_invalid_discovery(self):
        for number, (provider, audience, corrupt) in enumerate(
            ((p, a, c) for p in ("google", "custom_imap") for a in ("team", "guest") for c in ("malformed", "digest", "binding")), 150
        ):
            with self.subTest(provider=provider, audience=audience, corrupt=corrupt):
                self.client.command(["FLUSHALL"])
                value = self.value(number, provider=provider, team=audience == "team")
                create = self.ordinary_create if audience == "team" else self.guest_create
                first = create(value)
                self.assertTrue(first.get("created"), first)
                current = store._load_v2_thread(first["collaboration"]["collaborationId"], command_transport=self.transport).record
                raw = self.client.command(["HGET", self.index(), current["collaborationId"]])
                if corrupt == "malformed":
                    raw = "malformed"
                else:
                    record = json.loads(raw)
                    record.update({"threadHash": "0" * 40} if corrupt == "digest" else {"mailboxId": "wrong-mailbox"})
                    raw = json.dumps(record)
                self.client.command(["HSET", self.index(), current["collaborationId"], raw])
                keys = self.keys(current)
                expiry, original = self.expiry(keys), self.snapshot(keys)
                repeated = create(value)
                self.assertIs(repeated.get("created"), False, repeated)
                self.assertEqual(repeated["collaboration"]["collaborationId"], current["collaborationId"])
                self.assertEqual(self.page()["summaries"][0]["sourceRef"], value["sourceRef"])
                self.assertEqual(self.expiry(keys), expiry)
                self.assertEqual(self.snapshot(keys), original)

    def test_guest_create_reuses_and_enriches_preexisting_legacy_owner_thread(self):
        for number, provider in enumerate(("google", "custom_imap"), 180):
            with self.subTest(provider=provider):
                value = self.value(number, provider=provider, legacy=True)
                self.create(value)
                keys = self.keys(value)
                for key in keys:
                    self.client.command(["PEXPIRE", key, 100_000])
                expiry = self.expiry(keys)
                result = self.guest_create(value)
                self.assertIs(result.get("created"), False, result)
                self.assertEqual(result["collaboration"]["collaborationId"], value["collaborationId"])
                current = store._load_v2_thread(value["collaborationId"], command_transport=self.transport).record
                self.assertEqual({key: current[key] for key in value}, value)
                self.assertEqual(current["ownerUserId"], summaries.OWNER)
                self.assertEqual(current["participants"], [])
                self.assertEqual(self.expiry(keys), expiry)
                self.assertIn(value["collaborationId"], [entry["collaborationId"] for entry in self.page()["summaries"]])

    def test_previous_hmac_pointer_read_repairs_without_migrating_or_extending_pointer(self):
        for number, provider in enumerate(("google", "custom_imap"), 190):
            with self.subTest(provider=provider):
                value = self.value(number, provider=provider)
                self.create(value)
                self.client.command(["HDEL", self.index(), value["collaborationId"]])
                original_keys = self.keys(value)
                for key in original_keys:
                    self.client.command(["PEXPIRE", key, 100_000])
                expiry, original = self.expiry(original_keys), self.snapshot(original_keys)
                old_hmac = os.environ[store.V2_INDEX_HMAC_ENV]
                with patch.dict(os.environ, {store.V2_INDEX_HMAC_PREVIOUS_ENV: old_hmac,
                    store.V2_INDEX_HMAC_ENV: base64.urlsafe_b64encode(bytes(reversed(range(32)))).decode().rstrip("=")}):
                    current_pointer = self.keys(value)[1]
                    self.assertNotEqual(current_pointer, original_keys[1])
                    self.assertEqual(self.client.command(["EXISTS", current_pointer]), 0)
                    result = self.read(value)
                    self.assertEqual(result.get("status"), "ok", result)
                    self.assertEqual(self.client.command(["EXISTS", current_pointer]), 0)
                self.assertEqual(self.expiry(original_keys), expiry)
                self.assertEqual(self.snapshot(original_keys), original)
                self.assertIn(value["collaborationId"], [entry["collaborationId"] for entry in self.page()["summaries"]])

    def test_conflicting_current_previous_source_pointers_fail_without_writes(self):
        value = self.value()
        self.create(value)
        self.client.command(["DEL", self.index()])
        original_keys = self.keys(value)
        old_hmac = os.environ[store.V2_INDEX_HMAC_ENV]
        with patch.dict(os.environ, {store.V2_INDEX_HMAC_PREVIOUS_ENV: old_hmac,
            store.V2_INDEX_HMAC_ENV: base64.urlsafe_b64encode(bytes(reversed(range(32)))).decode().rstrip("=")}):
            current_pointer = self.keys(value)[1]
            self.client.command(["SET", current_pointer, "Z" * 22, "EX", 1000])
            keys = original_keys + [current_pointer, self.index()]
            expiry, original = self.expiry(keys), self.snapshot(keys)
            result = self.read(value)
            self.assertNotEqual(result.get("status"), "ok", result)
            self.assertEqual(self.expiry(keys), expiry)
            self.assertEqual(self.snapshot(keys), original)

    def test_exact_boundary_redis_command_budgets(self):
        write_commands = {"set", "psetex", "hset", "hdel", "expire", "pexpire", "persist", "del"}
        reports = []
        for audience in ("owner", "team", "guest"):
            for condition in ("new", "healthy", "missing_existing_hash", "missing_absent_hash"):
                with self.subTest(audience=audience, condition=condition):
                    self.client.command(["FLUSHALL"])
                    value = self.value(210, team=audience == "team")
                    invoke = (lambda target: self.create(target)) if audience == "owner" else (
                        self.ordinary_create if audience == "team" else self.guest_create)
                    users = (summaries.OWNER, summaries.PARTICIPANT) if audience == "team" else (summaries.OWNER,)
                    keys, expiry, original = [], [], []
                    if condition != "new":
                        first = invoke(value)
                        current = (first.record if audience == "owner" else store._load_v2_thread(
                            first["collaboration"]["collaborationId"], command_transport=self.transport).record)
                        keys = self.keys(current)
                        for key in keys:
                            self.client.command(["PEXPIRE", key, 100_000])
                        if condition == "missing_existing_hash":
                            self.create(self.value(211, team=audience == "team"))
                        if condition.startswith("missing_"):
                            for user in users:
                                self.client.command(["HDEL", self.index(user), current["collaborationId"]])
                        else:
                            keys += [self.index(user) for user in users]
                        expiry, original = self.expiry(keys), self.snapshot(keys)
                    before = self.command_counts()
                    self.commands.clear()
                    result = invoke(value)
                    network = len(self.commands)
                    network_commands = [command[0] for command in self.commands]
                    after = self.command_counts()
                    delta = {name: after.get(name, 0) - before.get(name, 0) for name in set(before) | set(after)}
                    delta = {name: count for name, count in delta.items() if count and name != "info"}
                    writes = {name: count for name, count in delta.items() if name in write_commands}
                    reads = {name: count for name, count in delta.items() if name not in write_commands and name != "eval"}
                    if condition == "new":
                        self.assertTrue(result.created if audience == "owner" else result.get("created"), result)
                    else:
                        self.assertFalse(result.created if audience == "owner" else result.get("created"), result)
                        expected = {"set": 1} if audience == "guest" else {}
                        if condition.startswith("missing_"):
                            expected["hset"] = len(users)
                            if condition == "missing_absent_hash":
                                expected["pexpire"] = len(users)
                        self.assertEqual(writes, expected)
                        self.assertEqual(self.expiry(keys), expiry)
                        self.assertEqual(self.snapshot(keys), original)
                    self.assertEqual(network, 1 if condition == "new" else {"owner": 3, "team": 4, "guest": 6}[audience])
                    reports.append({"audience": audience, "condition": condition, "network": network_commands,
                                    "reads": dict(sorted(reads.items())), "writes": dict(sorted(writes.items()))})
        print("C3B2.1 Redis command budgets: " + json.dumps(reports, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
