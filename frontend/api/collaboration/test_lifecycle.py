from __future__ import annotations

import base64
import copy
import json
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

from . import application, authorization, guest_session, models, mutations, owner_request_security, redis_store
from . import test_lua_redis_integration as redis_tests
from . import test_owner_http as http_tests

OWNER_ID = "usr_" + "A" * 22
PARTICIPANT_ID = "usr_" + "B" * 21 + "A"
MS = redis_tests.MS + 100


def thread_record(*, legacy=False, state="needs_review", participants=False):
    thread = redis_tests.thread_record()
    thread["state"] = state
    thread["messages"] = [
        redis_tests.message_record(index=1, created_at=MS, text="Internal history"),
        {**redis_tests.message_record(index=2, created_at=MS, text="Shared history"),
         "authorKind": "guest", "visibility": "shared"},
    ]
    if not legacy:
        thread.update(ownerUserId=OWNER_ID, ownerDisplayName="Owner", participants=[])
        if participants:
            thread["participants"] = [{
                "userId": PARTICIPANT_ID, "displayName": "Participant",
                "membershipRef": "tinv_original",
            }]
    return thread


def capability(operation="resolve", **updates):
    values = dict(
        _sentinel=authorization._INTERNAL_CAPABILITY_SENTINEL,
        owner_email="owner@example.com", workspace_id=redis_tests.WORKSPACE_ID,
        mailbox_id="mailbox-1", mailbox_provider="google", collaboration_id="A" * 22,
        action=operation, actor_kind="owner", actor_display_name="Owner",
        actor_user_id=OWNER_ID, viewer_access="owner", owner_user_id=OWNER_ID,
        owner_display_name="Owner",
    )
    values.update(updates)
    return authorization._InternalCollaborationCapability(**values)


class LifecycleContractTests(unittest.TestCase):
    def test_owner_only_and_legacy_schema_and_guest_projection(self):
        for legacy in (False, True):
            thread = thread_record(legacy=legacy)
            self.assertEqual(models.normalize_v2_thread_record(thread), thread)
            guest = models.build_v2_guest_thread_dto(thread)
            self.assertIsNotNone(guest)
            for field in ("ownerUserId", "ownerDisplayName", "ownerEmail", "participants", "workspaceId", "mailboxId"):
                self.assertNotIn(field, guest)
            self.assertEqual(len(guest["messages"]), 1)
        for field in ("ownerUserId", "ownerDisplayName", "participants"):
            malformed = thread_record()
            del malformed[field]
            self.assertIsNone(models.normalize_v2_thread_record(malformed))
        for changes in ({"participants": {}}, {"ownerUserId": "guest@example.com"}, {"unknown": True}):
            self.assertIsNone(models.normalize_v2_thread_record({**thread_record(), **changes}))

    def test_untrusted_actors_bindings_and_payloads_never_reach_saver(self):
        for operation in ("resolve", "reopen"):
            actors = [
                object(), {}, capability(operation, actor_kind="guest"),
                capability(operation, actor_kind="internal", viewer_access="participant", actor_user_id=PARTICIPANT_ID),
                capability(operation, actor_user_id=None, owner_user_id=None),
                capability(operation, actor_user_id=PARTICIPANT_ID),
                capability("read"), capability(operation, owner_display_name=" "),
                capability(operation, collaboration_id="B" * 22),
                capability(operation, workspace_id=redis_tests.OTHER_WORKSPACE_ID),
                capability(operation, mailbox_id="other-mailbox"),
                capability(operation, mailbox_provider="custom_imap"),
                capability(operation, owner_email="other@example.com"),
            ]
            for actor in actors:
                with self.subTest(operation=operation, actor=actor):
                    saver = Mock()
                    result = mutations.transition_v2_lifecycle(
                        actor, operation=operation, expected_state="needs_review", expected_updated_at=MS,
                        thread_loader=lambda *_a, **_k: redis_store._V2RecordResult(thread_record()),
                        thread_saver=saver,
                    )
                    self.assertEqual(result["error"]["code"], "forbidden")
                    saver.assert_not_called()
        for state, stamp in (([], MS), ("unknown", MS), ("resolved", True), ("resolved", str(MS))):
            loader = Mock()
            result = mutations.transition_v2_lifecycle(
                capability(), operation="resolve", expected_state=state, expected_updated_at=stamp,
                thread_loader=loader,
            )
            self.assertEqual(result["error"]["code"], "invalid_request")
            loader.assert_not_called()

    def test_application_requires_exact_cas_payload_and_projects_verified_owner(self):
        for operation in ("resolve", "reopen"):
            target = "resolved" if operation == "resolve" else "note_only"
            prior = "needs_review" if operation == "resolve" else "resolved"
            thread = thread_record(state=target)
            with patch.object(application, "resolve_verified_owner_collaboration_context", return_value={
                "status": "ok", "context": capability(operation), "error": None,
            }) as auth, patch.object(mutations, "transition_v2_lifecycle", return_value={
                "status": "ok", "record": thread, "changed": True, "error": None,
            }), patch.object(application, "_load_v2_external_guest_records", return_value={"status": "ok", "records": []}):
                result = application.transition_v2_lifecycle_for_verified_owner(
                    object(), (), "A" * 22, {"expectedState": prior, "expectedUpdatedAt": MS},
                    operation=operation, owner_security_configuration=object(),
                )
            self.assertTrue(result["changed"])
            self.assertEqual(auth.call_args.kwargs["required_action"], operation)
            self.assertEqual(result["collaboration"]["state"], target)
            self.assertEqual(result["collaboration"]["participants"][0]["userId"], OWNER_ID)
            self.assertEqual(len(result["collaboration"]["messages"]), 2)
        for payload in ({}, {"expectedState": "resolved", "expectedUpdatedAt": True},
                        {"expectedState": [], "expectedUpdatedAt": MS},
                        {"expectedState": "note_only", "expectedUpdatedAt": MS, "ownerUserId": OWNER_ID}):
            with patch.object(application, "resolve_verified_owner_collaboration_context") as auth:
                result = application.transition_v2_lifecycle_for_verified_owner(
                    object(), (), "A" * 22, payload, operation="resolve", owner_security_configuration=object(),
                )
            self.assertEqual(result["error"]["code"], "invalid_request")
            auth.assert_not_called()

    def test_new_authenticated_create_authority_rejects_missing_or_forged_identity(self):
        self.assertEqual(application._canonical_owner_authority(capability("create", collaboration_id=None)), {
            "ownerUserId": OWNER_ID, "ownerDisplayName": "Owner", "participants": [],
        })
        for changes in ({"actor_user_id": None}, {"owner_user_id": PARTICIPANT_ID},
                        {"viewer_access": "participant"}, {"actor_kind": "guest"}):
            self.assertIsNone(application._canonical_owner_authority(capability("create", **changes)))

    def test_storage_result_contract_rejects_forged_success_and_maps_ambiguous_failure(self):
        original = thread_record()
        loader = lambda *_a, **_k: redis_store._V2RecordResult(original)
        for outcome in (
            {"status": "ok", "record": original, "changed": True},
            redis_store._V2LifecycleResult(original, False),
            redis_store._V2LifecycleResult({**original, "state": "resolved", "ownerUserId": PARTICIPANT_ID}, False),
            redis_store._V2LifecycleResult({**original, "state": "resolved"}, "true"),
        ):
            result = mutations.transition_v2_lifecycle(
                capability(), operation="resolve", expected_state=original["state"], expected_updated_at=MS,
                thread_loader=loader, thread_saver=Mock(return_value=outcome),
            )
            self.assertEqual(result["error"]["code"], "storage_protocol_error")
        saver = Mock(side_effect=TimeoutError("ambiguous response"))
        result = mutations.transition_v2_lifecycle(
            capability(), operation="resolve", expected_state=original["state"], expected_updated_at=MS,
            thread_loader=loader, thread_saver=saver,
        )
        self.assertEqual(result["error"]["code"], "storage_unavailable")
        saver.assert_called_once()

    def test_authorization_keeps_owner_only_mapping_and_revalidates_authority(self):
        class Member:
            pass

        member = Member()
        member.__dict__.update(
            email=http_tests.OWNER_EMAIL, workspace_id=http_tests.WORKSPACE_ID,
            name="Owner Person", user_id=OWNER_ID, auth_source="auth0", user_type="member",
        )
        thread = {**thread_record(), "workspaceId": http_tests.WORKSPACE_ID, "mailboxId": http_tests.MAILBOX_ID}
        configuration = http_tests.parse_owner_security_configuration(
            http_tests.owner_http._trusted_security_snapshot(http_tests._environment()),
        )
        context = http_tests._context()
        mailbox = {"status": "ok", "memberAuthority": member,
                   "user": {"email": member.email},
                   "inbox": {"id": http_tests.MAILBOX_ID, "provider": "google"}}
        with patch.object(authorization.importlib, "import_module", side_effect=lambda name:
                          owner_request_security if name == "api.collaboration.owner_request_security"
                          else SimpleNamespace(AuthenticatedMemberContext=Member)):
            for operation in ("resolve", "reopen"):
                self.assertIn(operation, authorization.OWNER_ONLY_ACTIONS)
                self.assertNotIn(operation, authorization.PARTICIPANT_ACTIONS)
                def resolve(record=thread, actor=context, mailbox_result=mailbox, mailbox_id=None):
                    return authorization.resolve_verified_owner_collaboration_context(
                        actor, (), mailbox_id, collaboration_id=thread["collaborationId"], required_action=operation,
                        owner_security_configuration=configuration,
                        mailbox_resolver=lambda *_a: mailbox_result,
                        thread_loader=lambda *_a: redis_store._V2RecordResult(record),
                        member_resolver=Mock(side_effect=AssertionError("lifecycle is owner-only")),
                        team_member_resolver=Mock(side_effect=AssertionError("lifecycle is owner-only")),
                    )
                self.assertEqual(resolve()["context"].actor_user_id, OWNER_ID)
                legacy = {key: value for key, value in thread.items() if key not in {"ownerUserId", "ownerDisplayName", "participants"}}
                self.assertEqual(resolve(legacy)["context"].owner_user_id, OWNER_ID)
                for changes in ({"ownerUserId": PARTICIPANT_ID}, {"workspaceId": redis_tests.OTHER_WORKSPACE_ID},
                                {"ownerEmail": "another@example.com"}, {"participants": {}}):
                    self.assertNotEqual(resolve({**thread, **changes})["status"], "ok")
                self.assertNotEqual(resolve(mailbox_id="wrong-mailbox")["status"], "ok")
                self.assertNotEqual(resolve(actor=object())["status"], "ok")
                self.assertNotEqual(resolve(mailbox_result={"status": "unauthorized"})["status"], "ok")
                for field, value in (("user_type", "guest"), ("user_id", PARTICIPANT_ID), ("workspace_id", redis_tests.OTHER_WORKSPACE_ID)):
                    prior = getattr(member, field)
                    setattr(member, field, value)
                    self.assertNotEqual(resolve()["status"], "ok")
                    setattr(member, field, prior)
                participant_thread = {**thread, "ownerEmail": "other-owner@example.com", "participants": [{
                    "userId": PARTICIPANT_ID, "displayName": "Participant", "membershipRef": "tinv_revoked",
                }]}
                # Team members (active, stale, or nonparticipants) have no lifecycle grant.
                self.assertNotEqual(resolve(participant_thread)["status"], "ok")


class LifecycleRedisTests(unittest.TestCase):
    """Real Lua against an isolated, network-disabled ephemeral Redis process."""

    @classmethod
    def setUpClass(cls):
        redis_tests.ProductionLuaRedisIntegrationTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        redis_tests.ProductionLuaRedisIntegrationTests.tearDownClass.__func__(cls)

    def setUp(self):
        self.client.command(["FLUSHALL"])
        environment = patch.dict(os.environ, {
            redis_store.V2_INDEX_HMAC_ENV: base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("="),
            redis_store.V2_INDEX_HMAC_PREVIOUS_ENV: "",
        })
        environment.start()
        self.addCleanup(environment.stop)

    def transport(self, command):
        return {"result": self.client.command(command)}

    def seed(self, thread):
        result = redis_store._create_v2_thread(thread, command_transport=self.transport)
        self.assertEqual(result.get("status"), "ok", result)
        self.thread_key = redis_store.build_v2_thread_key(thread["collaborationId"])
        self.source_key = redis_store.build_v2_source_thread_key(thread["ownerEmail"], thread["mailboxId"], thread["sourceRef"])
        self.keys = [self.thread_key, self.source_key]

    def stored(self):
        return redis_store._load_v2_thread("A" * 22, command_transport=self.transport).record

    def snapshot(self):
        return [(self.client.command(["EVAL", "return redis.sha1hex(redis.call('DUMP', KEYS[1]))", 1, key]),
                 self.client.command(["PTTL", key])) for key in self.keys]

    def assert_unchanged(self, before):
        after = self.snapshot()
        for (old, old_ttl), (new, new_ttl) in zip(before, after):
            self.assertEqual(old, new)
            self.assertLessEqual(new_ttl, old_ttl)
            self.assertGreaterEqual(new_ttl, old_ttl - 2000)

    def transition(self, expected, operation="resolve", **kwargs):
        return mutations.transition_v2_lifecycle(
            capability(operation), operation=operation, expected_state=expected["state"],
            expected_updated_at=expected["updatedAt"], command_transport=self.transport, **kwargs,
        )

    def test_all_active_states_sequence_noops_history_and_stale_opposite_retry(self):
        for state in ("needs_review", "needs_action", "note_only"):
            for participants in (False, True):
                with self.subTest(state=state, participants=participants):
                    self.client.command(["FLUSHALL"])
                    original = thread_record(state=state, participants=participants)
                    self.seed(original)
                    for key in self.keys:
                        self.client.command(["PEXPIRE", key, 600_000])
                    before = self.snapshot()
                    self.assertFalse(self.transition(original, "reopen")["changed"])
                    self.assertEqual(self.stored()["state"], state)
                    self.assert_unchanged(before)
                    # Seed real invitation/session records; lifecycle must never alter them.
                    invite = redis_tests.invite_record()
                    session = redis_tests.session_record("s" * 43)
                    for key, value, kind in (
                        (redis_store.build_v2_invite_key(invite["inviteId"]), invite, "invite"),
                        (redis_store.build_v2_guest_session_key(session["sessionHash"]), session, "session"),
                    ):
                        self.client.command(["SET", key, redis_tests.wire_json(value, kind), "EX", 1000])
                        self.keys.append(key)
                    guests = self.snapshot()[2:]
                    resolved = self.transition(original)
                    self.assertTrue(resolved["changed"], resolved)
                    current = self.stored()
                    self.assertEqual(current["state"], "resolved")
                    self.assertGreater(current["updatedAt"], original["updatedAt"])
                    for field in set(original) - {"state", "updatedAt"}:
                        self.assertEqual(current[field], original[field])
                    for key in self.keys[:2]:
                        self.client.command(["PEXPIRE", key, 600_000])
                    before = self.snapshot()
                    self.assertFalse(self.transition(original)["changed"])
                    self.assert_unchanged(before)
                    reopened = self.transition(current, "reopen")
                    self.assertTrue(reopened["changed"], reopened)
                    self.assertEqual(reopened["record"]["state"], "note_only")
                    self.assertGreater(reopened["record"]["updatedAt"], current["updatedAt"])
                    before = self.snapshot()
                    self.assertFalse(self.transition(current, "reopen")["changed"])
                    self.assertEqual(self.transition(original)["error"]["code"], "stale_thread")
                    self.assert_unchanged(before)
                    resolved_again = self.transition(reopened["record"])
                    self.assertTrue(resolved_again["changed"])
                    before = self.snapshot()
                    self.assertEqual(self.transition(current, "reopen")["error"]["code"], "stale_thread")
                    self.assert_unchanged(before)
                    self.assertEqual([row[0] for row in self.snapshot()[2:]], [row[0] for row in guests])

    def test_legacy_read_noop_and_exact_verified_enrichment(self):
        original = thread_record(legacy=True)
        self.seed(original)
        before = self.snapshot()
        self.assertEqual(self.stored(), original)
        self.assertFalse(self.transition(original, "reopen")["changed"])
        self.assert_unchanged(before)
        result = self.transition(original)
        self.assertTrue(result["changed"], result)
        self.assertEqual(self.stored()["ownerUserId"], OWNER_ID)
        self.assertEqual(self.stored()["participants"], [])
        for field in set(original) - {"state", "updatedAt"}:
            self.assertEqual(self.stored()[field], original[field])

    def test_expected_state_timestamp_malformed_and_scope_rejections_are_zero_write(self):
        original = thread_record(legacy=True)
        self.seed(original)
        replacement = {**original, "state": "resolved", "updatedAt": MS + 1,
                       "ownerUserId": OWNER_ID, "ownerDisplayName": "Owner", "participants": []}
        base = ["EVAL", redis_store._TRANSITION_V2_LIFECYCLE_LUA, 2, self.thread_key, self.source_key,
                str(MS), redis_tests.wire_json(replacement, "thread"), str(redis_store.V2_THREAD_RETENTION_SECONDS),
                OWNER_ID, "resolve", "needs_review"]
        for index, value in ((5, str(MS - 1)), (10, "needs_action")):
            command = list(base)
            command[index] = value
            before = self.snapshot()
            self.assertEqual(json.loads(self.client.command(command))["status"], "stale")
            self.assert_unchanged(before)
        for changes in (
            {"collaborationId": "B" * 22}, {"workspaceId": redis_tests.OTHER_WORKSPACE_ID},
            {"mailboxId": "other"}, {"ownerEmail": "other@example.com"}, {"ownerUserId": PARTICIPANT_ID},
            {"sourceRef": {"provider": "google", "providerMessageId": "other"}},
            {"createdAt": MS - 1}, {"sourceMessage": {**original["sourceMessage"], "subject": "Changed"}},
            {"messages": []}, {"participants": [{"userId": PARTICIPANT_ID, "displayName": "P", "membershipRef": "tinv_other"}]},
            {"unknown": "field"}, {"participants": {}}, {"ownerDisplayName": " "}, {"updatedAt": MS},
        ):
            with self.subTest(changes=changes):
                wire = models.encode_v2_wire_record({**replacement, **changes}, "thread")
                # Unknown fields must be rejected in Lua as well as Python.
                if wire is None:
                    wire = json.loads(base[6])
                    wire.update(changes)
                command = list(base)
                command[6] = json.dumps(wire)
                before = self.snapshot()
                self.assertIn(json.loads(self.client.command(command))["status"], {"malformed", "invalid_scope"})
                self.assert_unchanged(before)
        for corrupt in (
            redis_tests.wire_json(thread_record(), "thread").replace('"participants":[]', '"participants":{}'),
            redis_tests.wire_json(thread_record(), "thread").replace(OWNER_ID, PARTICIPANT_ID),
            '{"v":"2"}',
        ):
            self.client.command(["SET", self.thread_key, corrupt, "EX", 1000])
            before = self.snapshot()
            self.assertIn(json.loads(self.client.command(base))["status"], {"malformed", "invalid_scope"})
            self.assert_unchanged(before)

    def test_concurrent_resolve_converges_and_message_race_conflicts(self):
        original = thread_record()
        self.seed(original)
        barrier = threading.Barrier(2)
        def loader(*_a, **_k):
            barrier.wait(timeout=5)
            return redis_store._V2RecordResult(copy.deepcopy(original))
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.transition(original, thread_loader=loader), range(2)))
        self.assertEqual(sorted(result["changed"] for result in results), [False, True])
        resolved = self.stored()
        advanced = {**resolved, "updatedAt": resolved["updatedAt"] + 1,
                    "messages": [*resolved["messages"], redis_tests.message_record(index=3, created_at=resolved["updatedAt"] + 1, text="New note")]}
        saved = redis_store._save_v2_thread_if_expected(advanced, resolved["updatedAt"], command_transport=self.transport)
        self.assertEqual(saved.get("status"), "ok", saved)
        before = self.snapshot()
        result = self.transition(resolved, "reopen", thread_loader=lambda *_a, **_k: redis_store._V2RecordResult(resolved))
        self.assertEqual(result["error"]["code"], "stale_thread")
        self.assert_unchanged(before)
        self.assertEqual(self.stored()["messages"], advanced["messages"])

    def test_owner_appends_and_participant_add_preserve_guest_only_owner_identity(self):
        self.seed(thread_record())
        for action, visibility, token in (("reply", "shared", "i"), ("internal_note", "internal", "j")):
            key = base64.urlsafe_b64encode(token.encode() * 32).decode().rstrip("=")
            result = mutations.append_owner_v2_message_idempotently(
                capability(action), "Owner " + visibility, visibility=visibility, idempotency_key=key,
                command_transport=self.transport,
            )
            self.assertEqual(result["status"], "ok", result)
            self.assertEqual(self.stored()["ownerUserId"], OWNER_ID)
            self.assertEqual(self.stored()["participants"], [])
        result = mutations.add_v2_participant(capability("manage_participants"), {
            "userId": PARTICIPANT_ID, "displayName": "Participant", "membershipRef": "tinv_original",
        }, command_transport=self.transport)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(self.stored()["ownerUserId"], OWNER_ID)
        self.assertEqual(len(self.stored()["messages"]), 4)
        self.assertEqual(len(self.stored()["participants"]), 1)

    def test_guest_only_invite_reply_revoke_and_replacement_keep_canonical_owner(self):
        thread, invite, session = thread_record(), redis_tests.invite_record(), redis_tests.session_record("s" * 43)
        created = redis_store._create_v2_thread_with_guest(
            thread, invite, now=redis_tests.SEC + 100, command_transport=self.transport,
        )
        self.assertTrue(created.thread_created)
        self.assertEqual(self.stored()["ownerUserId"], OWNER_ID)
        exchanged = redis_store._atomic_exchange_v2_invite(
            raw_token="t" * 43, invite_id=invite["inviteId"], session_record=session,
            now=redis_tests.SEC + 101, session_ttl=49, command_transport=self.transport,
        )
        self.assertEqual(exchanged, {"status": "ok"})
        context = guest_session.resolve_guest_v2_mutation_context(
            "POST", [("Origin", "https://app.cuevion.test"), ("Content-Type", "application/json"),
                     (guest_session.CSRF_HEADER_NAME, "c" * 43),
                     ("Cookie", f"{guest_session.GUEST_SESSION_COOKIE_NAME}={'s' * 43}")],
            now=redis_tests.SEC + 102, command_transport=self.transport,
            environment={"VERCEL_ENV": "production", "CUEVION_APP_ORIGIN": "https://app.cuevion.test"},
        )
        self.assertEqual(context["status"], "ok", context)
        guest = context["context"]
        for operation in ("resolve", "reopen"):
            saver = Mock()
            denied = mutations.transition_v2_lifecycle(
                guest, operation=operation, expected_state=thread["state"], expected_updated_at=MS,
                thread_saver=saver,
            )
            self.assertEqual(denied["error"]["code"], "forbidden")
            saver.assert_not_called()
        with patch.object(mutations.time, "time", return_value=redis_tests.SEC + 102), patch.object(
            mutations.time, "time_ns", return_value=(redis_tests.SEC + 102) * 1_000_000_000,
        ):
            reply = mutations.append_guest_v2_reply(guest, "Guest shared reply", idempotency_key="A" * 43, command_transport=self.transport)
        self.assertEqual(reply["status"], "ok", reply)
        after_reply = self.stored()
        self.assertEqual(after_reply["ownerUserId"], OWNER_ID)
        self.assertEqual(after_reply["participants"], [])
        self.assertEqual(after_reply["messages"][-1]["visibility"], "shared")
        revoked = redis_store._revoke_v2_invite(
            invite["inviteId"], owner_email=thread["ownerEmail"], workspace_id=thread["workspaceId"],
            mailbox_id=thread["mailboxId"], collaboration_id=thread["collaborationId"],
            revoked_by=thread["ownerEmail"], now=redis_tests.SEC + 103, command_transport=self.transport,
        )
        self.assertEqual(revoked, {"status": "ok"})
        self.assertEqual(self.stored(), after_reply)
        # Match the fixture's advanced logical second; do not sleep or change
        # production expiry checks to compensate for synthetic test time.
        for key, ceiling in (
            (redis_store.build_v2_invite_key(invite["inviteId"]), 96_000),
            (redis_store.build_v2_invite_token_key(invite["tokenHash"]), 96_000),
            (redis_store.build_v2_thread_invite_key(thread["ownerEmail"], thread["collaborationId"], None), 96_000),
            (redis_store.build_v2_guest_session_key(session["sessionHash"]), 46_000),
        ):
            self.client.command(["PEXPIRE", key, min(self.client.command(["PTTL", key]), ceiling)])
        next_invite = {**invite, "inviteId": "J" * 22, "tokenHash": models.hash_v2_secret("u" * 43),
                       "createdAt": redis_tests.SEC + 104, "expiresAt": redis_tests.SEC + 204}
        replacement = redis_store._create_v2_invite(next_invite, now=redis_tests.SEC + 104, command_transport=self.transport)
        self.assertEqual(replacement.get("status"), "ok", replacement)
        self.assertEqual(self.stored(), after_reply)
        old = redis_tests.typed_wire_json(self.client.command(["GET", redis_store.build_v2_invite_key(invite["inviteId"])]), "invite")
        self.assertEqual(old["status"], "revoked")

    def test_owner_http_through_application_and_lua_returns_committed_state(self):
        thread = thread_record(legacy=True)
        self.seed(thread)
        configuration = http_tests.parse_owner_security_configuration(
            http_tests.owner_http._trusted_security_snapshot(http_tests._environment()),
        )
        owner = http_tests._context()
        csrf = http_tests.issue_owner_csrf_token(owner, configuration, now=http_tests.NOW)[0]
        real_transition = mutations.transition_v2_lifecycle
        with patch.object(http_tests.owner_http, "_resolve_context", return_value=owner), patch.object(
            http_tests.owner_rate_limit, "consume_owner_rate_limit",
            return_value=http_tests.owner_rate_limit.OwnerRateLimitDecision("allowed"),
        ), patch.object(application, "resolve_verified_owner_collaboration_context", side_effect=lambda *_a, **kw: {
            "status": "ok", "context": capability(kw["required_action"]), "error": None,
        }), patch.object(mutations, "transition_v2_lifecycle", side_effect=lambda *a, **kw:
                        real_transition(*a, **kw, command_transport=self.transport)), patch.object(
            application, "_load_v2_external_guest_records", return_value={"status": "ok", "records": []},
        ):
            for operation, target in (("resolve", "resolved"), ("reopen", "note_only")):
                before = self.stored()
                payload = {"operation": operation, "collaborationId": thread["collaborationId"],
                           "expectedState": before["state"], "expectedUpdatedAt": str(before["updatedAt"])}
                response = http_tests._invoke(http_tests._request(payload, csrf=csrf))
                self.assertEqual(response.status, 200, response.body)
                data = http_tests._json(response)["data"]
                self.assertTrue(data["changed"])
                self.assertEqual(data["collaboration"]["state"], target)
                self.assertEqual(data["collaboration"]["updatedAt"], self.stored()["updatedAt"])
                self.assertEqual(data["collaboration"]["participants"][0]["userId"], OWNER_ID)
                retry = http_tests._invoke(http_tests._request(payload, csrf=csrf))
                self.assertEqual(retry.status, 200)
                self.assertFalse(http_tests._json(retry)["data"]["changed"])
