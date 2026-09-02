from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from . import guest_session
from .authorization import resolve_internal_collaboration_context
from .models import hash_v2_secret

SESSION_ID = "s" * 43
CSRF_TOKEN = "c" * 43
INVITE_TOKEN = "i" * 43
SEC = 1_800_000_000
MS = SEC * 1000
WORKSPACE_ID = "wsp_" + "W" * 22


def thread_record() -> dict:
    return {
        "v": 2, "collaborationId": "A" * 22,
        "ownerEmail": "owner@example.com", "workspaceId": WORKSPACE_ID,
        "mailboxId": "mailbox-1",
        "sourceRef": {"provider": "google", "providerMessageId": "gmail-1"},
        "sourceMessage": {
            "subject": "Review", "senderDisplay": "Sender",
            "fromDisplay": "sender@example.com", "timestamp": "today", "bodyText": "Body",
        },
        "state": "needs_review", "messages": [], "createdAt": MS + 100, "updatedAt": MS + 100,
    }


def invite_record(*, status="active", expires_at=SEC + 100 + 86_400) -> dict:
    record = {
        "v": 2, "inviteId": "B" * 22, "tokenHash": hash_v2_secret(INVITE_TOKEN),
        "ownerEmail": "owner@example.com", "workspaceId": WORKSPACE_ID,
        "mailboxId": "mailbox-1", "collaborationId": "A" * 22,
        "identityAssurance": "link_possession", "allowedActions": ["read", "reply"],
        "visibility": "shared_only",
        "createdBy": {"ownerEmail": "owner@example.com", "displayName": "Owner"},
        "createdAt": SEC + 100, "expiresAt": expires_at, "status": status,
        "exchangedAt": SEC + 101 if status == "exchanged" else None,
        "exchangeCount": 1 if status == "exchanged" else 0,
        "revokedAt": SEC + 102 if status == "revoked" else None,
        "revokedBy": "owner@example.com" if status == "revoked" else None,
    }
    if status == "exchanged":
        record["activeSessionHash"] = hash_v2_secret(SESSION_ID)
    return record


def session_record() -> dict:
    return {
        "v": 2, "sessionHash": hash_v2_secret(SESSION_ID), "inviteId": "B" * 22,
        "ownerEmail": "owner@example.com", "workspaceId": WORKSPACE_ID,
        "mailboxId": "mailbox-1", "collaborationId": "A" * 22,
        "allowedActions": ["read", "reply"], "visibility": "shared_only",
        "identityAssurance": "link_possession", "guestDisplayName": "Reviewer",
        "createdAt": SEC + 101, "lastUsedAt": SEC + 101, "expiresAt": SEC + 28_900,
        "status": "active", "csrfTokenHash": hash_v2_secret(CSRF_TOKEN),
        "revokedAt": None, "loggedOutAt": None,
    }


def internal_capability(action: str, *, display_name: str = "Owner"):
    result = resolve_internal_collaboration_context(
        [],
        "mailbox-1",
        collaboration_id="A" * 22,
        required_action=action,
        user_resolver=lambda _headers: (
            {"email": "owner@example.com", "name": display_name},
            None,
        ),
        mailbox_resolver=lambda _headers, mailbox_id: {
            "status": "ok",
            "user": {"email": "owner@example.com"},
            "inbox": {"id": mailbox_id, "provider": "google"},
        },
        thread_loader=lambda _collaboration_id: {
            "status": "ok",
            "record": {**thread_record(), "workspaceId": "owner@example.com"},
        },
    )
    if result["status"] != "ok":
        raise AssertionError(result)
    return replace(result["context"], workspace_id=WORKSPACE_ID)


class CollaborationV2GuestSessionTests(unittest.TestCase):
    def test_session_lifetime_boundaries_and_corruption(self):
        for delta, accepted in ((28_799, True), (28_800, True), (28_801, False)):
            record = session_record()
            record["expiresAt"] = record["createdAt"] + delta
            self.assertEqual(guest_session.normalize_v2_guest_session_record(record) is not None, accepted)
        record = session_record()
        record["visibility"] = "internal"
        self.assertIsNone(guest_session.normalize_v2_guest_session_record(record))
        for malformed_workspace in (
            "owner@example.com",
            "wsp_short",
            "wsp_" + "W" * 21 + ".",
        ):
            record = session_record()
            record["workspaceId"] = malformed_workspace
            self.assertIsNone(guest_session.normalize_v2_guest_session_record(record))

    def test_session_terminal_state_matrix_requires_one_immutable_audit(self):
        active = session_record()
        self.assertIsNotNone(guest_session.normalize_v2_guest_session_record(active))

        equal_logout = {**active, "status": "logged_out", "loggedOutAt": active["lastUsedAt"]}
        self.assertIsNone(guest_session.normalize_v2_guest_session_record(equal_logout))

        logged_out = {**active, "status": "logged_out", "loggedOutAt": SEC + 102}
        normalized_logout = guest_session.normalize_v2_guest_session_record(logged_out)
        self.assertEqual(normalized_logout["status"], "logged_out")
        self.assertEqual(normalized_logout["loggedOutAt"], SEC + 102)
        self.assertIsNone(normalized_logout["revokedAt"])

        revoked = {**active, "status": "revoked", "revokedAt": SEC + 102}
        normalized_revoked = guest_session.normalize_v2_guest_session_record(revoked)
        self.assertEqual(normalized_revoked["status"], "revoked")
        self.assertEqual(normalized_revoked["revokedAt"], SEC + 102)
        self.assertIsNone(normalized_revoked["loggedOutAt"])

        for malformed in (
            {**active, "status": "active", "loggedOutAt": SEC + 101},
            {**logged_out, "revokedAt": SEC + 102},
            {**revoked, "loggedOutAt": SEC + 101},
            {**active, "status": "expired", "revokedAt": SEC + 102},
        ):
            self.assertIsNone(guest_session.normalize_v2_guest_session_record(malformed))

        advanced = {**active, "lastUsedAt": SEC + 150}
        self.assertIsNone(guest_session.normalize_v2_guest_session_record({
            **advanced, "status": "logged_out", "loggedOutAt": SEC + 149,
        }))
        self.assertIsNone(guest_session.normalize_v2_guest_session_record({
            **advanced, "status": "logged_out", "loggedOutAt": SEC + 150,
        }))
        self.assertIsNotNone(guest_session.normalize_v2_guest_session_record({
            **advanced, "status": "logged_out", "loggedOutAt": SEC + 151,
        }))
        self.assertIsNone(guest_session.normalize_v2_guest_session_record({
            **advanced, "status": "revoked", "revokedAt": SEC + 149,
        }))
        self.assertIsNone(guest_session.normalize_v2_guest_session_record({
            **advanced, "status": "revoked", "revokedAt": SEC + 150,
        }))
        self.assertIsNotNone(guest_session.normalize_v2_guest_session_record({
            **advanced, "status": "revoked", "revokedAt": SEC + 151,
        }))

    def test_session_schema_uses_canonical_opaque_ids_and_utf8_byte_limits(self):
        for field in ("inviteId", "collaborationId"):
            for malformed in ("A" * 21, "A" * 129, "A" * 21 + " ", "A" * 21 + ".", "é" * 22):
                with self.subTest(field=field, value=malformed):
                    self.assertIsNone(guest_session.normalize_v2_guest_session_record({
                        **session_record(), field: malformed,
                    }))
        self.assertIsNotNone(guest_session.normalize_v2_guest_session_record({
            **session_record(), "guestDisplayName": "é" * 128,
        }))
        self.assertIsNone(guest_session.normalize_v2_guest_session_record({
            **session_record(), "guestDisplayName": "é" * 129,
        }))

    def test_invitation_is_exactly_24_hours_and_raw_token_is_returned_once(self):
        captured = []

        def store(record, *, now, command_transport):
            captured.append(record)
            return {"status": "ok", "record": record, "created": True}

        with patch.object(guest_session, "_create_v2_invite", side_effect=store):
            result = guest_session.issue_v2_invitation(
                internal_capability("issue_invite"),
                "A" * 22,
                invited_email="Reviewer@Example.com", now=SEC + 100,
                thread_loader=lambda *_args, **_kwargs: {"status": "ok", "record": thread_record()},
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(captured[0]["expiresAt"] - captured[0]["createdAt"], 86_400)
        self.assertEqual(captured[0]["invitedEmail"], "reviewer@example.com")
        self.assertEqual(captured[0]["workspaceId"], WORKSPACE_ID)
        self.assertNotEqual(captured[0]["ownerEmail"], captured[0]["workspaceId"])
        self.assertNotIn(result["token"], repr(captured[0]))
        self.assertEqual(captured[0]["tokenHash"], hash_v2_secret(result["token"]))

    def test_duplicate_invitation_never_reveals_unpersisted_token(self):
        with patch.object(
            guest_session, "_create_v2_invite",
            return_value={"status": "ok", "record": invite_record(), "created": False},
        ):
            result = guest_session.issue_v2_invitation(
                internal_capability("issue_invite"),
                "A" * 22,
                now=SEC + 100,
                thread_loader=lambda *_args, **_kwargs: {"status": "ok", "record": thread_record()},
            )
        self.assertEqual(result["status"], "duplicate")
        self.assertNotIn("token", result)

    def test_invitation_issuance_uses_only_resolved_owner_context(self):
        captured = []
        context = internal_capability("issue_invite", display_name="Signed Owner")
        with patch.object(guest_session, "_create_v2_invite", side_effect=lambda record, **_kwargs: captured.append(record) or {"status": "ok", "record": record, "created": True}):
            result = guest_session.issue_v2_invitation(
                context, "A" * 22, now=SEC + 100,
                thread_loader=lambda *_args, **_kwargs: {"status": "ok", "record": thread_record()},
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(captured[0]["createdBy"], {"ownerEmail": "owner@example.com", "displayName": "Signed Owner"})
        forged = {"copied": context, "action": "issue_invite"}
        denied = guest_session.issue_v2_invitation(
            forged, "A" * 22, now=SEC + 100,
            thread_loader=lambda *_args, **_kwargs: self.fail("forged owner must fail before storage"),
        )
        self.assertEqual(denied["error"]["code"], "invalid_request")

        for label, changed_context, changed_thread, expected_code in (
            (
                "malformed-capability-workspace",
                replace(context, workspace_id="owner@example.com"),
                thread_record(),
                "invalid_request",
            ),
            (
                "capability-thread-workspace-mismatch",
                context,
                {**thread_record(), "workspaceId": "wsp_" + "X" * 22},
                "forbidden",
            ),
        ):
            with self.subTest(label=label), patch.object(
                guest_session, "_create_v2_invite"
            ) as invite_store:
                rejected = guest_session.issue_v2_invitation(
                    changed_context,
                    "A" * 22,
                    now=SEC + 100,
                    thread_loader=lambda *_args, record=changed_thread, **_kwargs: {
                        "status": "ok",
                        "record": record,
                    },
                )
            self.assertEqual(rejected["error"]["code"], expected_code)
            invite_store.assert_not_called()

    def test_invitation_revocation_derives_actor_from_resolved_context(self):
        thread = thread_record()
        context = internal_capability("revoke_invite")
        with patch.object(guest_session, "_revoke_v2_invite", return_value={"status": "ok"}) as revoke:
            result = guest_session.revoke_invitation_for_owner(context, "B" * 22, now=SEC + 200, thread_loader=lambda *_args, **_kwargs: {"status": "ok", "record": thread})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(revoke.call_args.kwargs["owner_email"], "owner@example.com")
        self.assertEqual(revoke.call_args.kwargs["revoked_by"], "owner@example.com")
        forged = {"copied": context, "action": "revoke_invite"}
        self.assertEqual(guest_session.revoke_invitation_for_owner(forged, "B" * 22, now=SEC + 200, thread_loader=lambda *_args, **_kwargs: self.fail("forged actor must fail before storage"))["error"]["code"], "invalid_request")

    def test_exchange_creates_at_most_eight_hour_session_and_returns_raw_values_once(self):
        captured = []
        with patch.object(
            guest_session, "_load_v2_invite_by_token",
            return_value={"status": "ok", "record": invite_record()},
        ), patch.object(
            guest_session, "_atomic_exchange_v2_invite",
            side_effect=lambda **kwargs: captured.append(kwargs) or {"status": "ok"},
        ):
            result = guest_session.exchange_v2_invitation(
                INVITE_TOKEN, guest_display_name="Reviewer", now=SEC + 100
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["session"]["expiresAt"], SEC + 100 + 28_800)
        self.assertEqual(captured[0]["session_ttl"], 28_800)
        persisted = captured[0]["session_record"]
        self.assertEqual(persisted["workspaceId"], WORKSPACE_ID)
        self.assertNotEqual(persisted["ownerEmail"], persisted["workspaceId"])
        self.assertNotIn(result["sessionId"], repr(persisted))
        self.assertNotIn(result["csrfToken"], repr(persisted))
        self.assertEqual(persisted["sessionHash"], hash_v2_secret(result["sessionId"]))
        self.assertEqual(persisted["csrfTokenHash"], hash_v2_secret(result["csrfToken"]))

        historical = {**invite_record(), "workspaceId": "owner@example.com"}
        with patch.object(
            guest_session,
            "_load_v2_invite_by_token",
            return_value={"status": "ok", "record": historical},
        ), patch.object(guest_session, "_atomic_exchange_v2_invite") as exchange:
            rejected = guest_session.exchange_v2_invitation(
                INVITE_TOKEN,
                guest_display_name="Reviewer",
                now=SEC + 100,
            )
        self.assertEqual(rejected["error"]["code"], "invalid_request")
        self.assertNotIn("sessionId", rejected)
        self.assertNotIn("csrfToken", rejected)
        exchange.assert_not_called()

    def test_exchange_is_single_use_under_racing_attempts(self):
        outcomes = [{"status": "ok"}, {"status": "exchanged", "error": {"code": "invite_already_exchanged"}}]
        with patch.object(
            guest_session, "_load_v2_invite_by_token",
            return_value={"status": "ok", "record": invite_record()},
        ), patch.object(guest_session, "_atomic_exchange_v2_invite", side_effect=outcomes):
            first = guest_session.exchange_v2_invitation(INVITE_TOKEN, guest_display_name="One", now=SEC + 100)
            second = guest_session.exchange_v2_invitation(INVITE_TOKEN, guest_display_name="Two", now=SEC + 100)
        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["error"]["code"], "invite_already_exchanged")
        self.assertNotIn("sessionId", second)

    def test_atomic_unavailability_has_no_raw_session_or_csrf_result(self):
        with patch.object(
            guest_session, "_load_v2_invite_by_token",
            return_value={"status": "ok", "record": invite_record()},
        ), patch.object(
            guest_session, "_atomic_exchange_v2_invite",
            return_value={"status": "unavailable", "error": {"code": "atomic_exchange_unavailable"}},
        ):
            result = guest_session.exchange_v2_invitation(
                INVITE_TOKEN, guest_display_name="Reviewer", now=SEC + 100
            )
        self.assertEqual(result["error"]["code"], "atomic_exchange_unavailable")
        self.assertNotIn("sessionId", result)
        self.assertNotIn("csrfToken", result)

    def test_private_bootstrap_revalidates_invite_without_mutating_session(self):
        session = session_record()
        invite = invite_record(status="exchanged")
        invite["activeSessionHash"] = session["sessionHash"]
        original = dict(session)
        with patch.object(
            guest_session, "_load_v2_guest_session_record",
            return_value={"status": "ok", "record": session},
        ), patch.object(
            guest_session, "_load_v2_invite_by_id",
            return_value={"status": "ok", "record": invite},
        ):
            result = guest_session._bootstrap_v2_guest_session_read_only(
                SESSION_ID, now=SEC + 200
            )
        self.assertEqual(result["status"], "ok")
        self.assertTrue(callable(guest_session.bootstrap_v2_guest_session))
        self.assertNotIn("csrfToken", result)
        self.assertNotIn("sessionHash", repr(result))
        self.assertEqual(session, original)

    def test_read_only_bootstrap_rejects_backward_clocks_before_invite_lookup(self):
        invite = invite_record(status="exchanged")
        base = session_record()
        invite["activeSessionHash"] = base["sessionHash"]
        cases = (
            ({**base}, base["createdAt"] - 1),
            ({**base, "lastUsedAt": base["createdAt"] + 50}, base["createdAt"] + 49),
        )
        for session, current_time in cases:
            with self.subTest(now=current_time), patch.object(
                guest_session, "_load_v2_guest_session_record",
                return_value={"status": "ok", "record": session},
            ), patch.object(guest_session, "_load_v2_invite_by_id") as invite_loader:
                result = guest_session._bootstrap_v2_guest_session_read_only(
                    SESSION_ID, now=current_time
                )
                self.assertEqual(result["error"]["code"], "invalid_request")
                invite_loader.assert_not_called()

        equal = {**base, "lastUsedAt": base["createdAt"]}
        with patch.object(
            guest_session, "_load_v2_guest_session_record",
            return_value={"status": "ok", "record": equal},
        ), patch.object(
            guest_session, "_load_v2_invite_by_id",
            return_value={"status": "ok", "record": invite},
        ):
            self.assertEqual(
                guest_session._bootstrap_v2_guest_session_read_only(
                    SESSION_ID, now=equal["lastUsedAt"]
                )["status"],
                "ok",
            )

        with patch.object(
            guest_session, "_load_v2_guest_session_record",
            return_value={"status": "ok", "record": base},
        ), patch.object(guest_session, "_load_v2_invite_by_id") as invite_loader:
            expired = guest_session._bootstrap_v2_guest_session_read_only(
                SESSION_ID, now=base["expiresAt"]
            )
            self.assertEqual(expired["error"]["code"], "session_expired")
            invite_loader.assert_not_called()

    def test_invite_revocation_invalidates_session_on_every_load(self):
        with patch.object(
            guest_session, "_load_v2_guest_session_record",
            return_value={"status": "ok", "record": session_record()},
        ), patch.object(
            guest_session, "_load_v2_invite_by_id",
            return_value={"status": "revoked", "error": {"code": "invite_revoked"}},
        ):
            result = guest_session._bootstrap_v2_guest_session_read_only(
                SESSION_ID, now=SEC + 200
            )
        self.assertEqual(result["error"]["code"], "session_revoked")

    def test_bootstrap_rejects_active_session_linkage_or_scope_mismatch(self):
        session = session_record()
        invite = invite_record(status="exchanged")
        invite["activeSessionHash"] = "f" * 64
        with patch.object(guest_session, "_load_v2_guest_session_record", return_value={"status": "ok", "record": session}), patch.object(guest_session, "_load_v2_invite_by_id", return_value={"status": "ok", "record": invite}):
            result = guest_session._bootstrap_v2_guest_session_read_only(
                SESSION_ID, now=SEC + 200
            )
        self.assertEqual(result["error"]["code"], "session_revoked")
        invite = invite_record(status="exchanged")
        invite["activeSessionHash"] = session["sessionHash"]
        invite["workspaceId"] = "wsp_" + "X" * 22
        with patch.object(guest_session, "_load_v2_guest_session_record", return_value={"status": "ok", "record": session}), patch.object(guest_session, "_load_v2_invite_by_id", return_value={"status": "ok", "record": invite}):
            result = guest_session._bootstrap_v2_guest_session_read_only(
                SESSION_ID, now=SEC + 200
            )
        self.assertEqual(result["error"]["code"], "session_revoked")
        invite["activeSessionHash"] = session["sessionHash"]
        invite["mailboxId"] = "mailbox-other"
        with patch.object(guest_session, "_load_v2_guest_session_record", return_value={"status": "ok", "record": session}), patch.object(guest_session, "_load_v2_invite_by_id", return_value={"status": "ok", "record": invite}):
            result = guest_session._bootstrap_v2_guest_session_read_only(
                SESSION_ID, now=SEC + 200
            )
        self.assertEqual(result["error"]["code"], "session_revoked")

    def test_cookie_attributes_are_host_only_bounded_and_clear_symmetrically(self):
        cookie = guest_session.build_guest_session_cookie(
            SESSION_ID, expires_at=SEC + 100 + 99_999, now=SEC + 100
        )
        self.assertIn("Path=/api/collaboration/guest", cookie)
        self.assertIn("Max-Age=28800", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Secure", cookie)
        self.assertNotIn("Domain=", cookie)
        clear = guest_session.clear_guest_session_cookie()
        self.assertIn("Max-Age=0", clear)
        for attribute in ("Path=/api/collaboration/guest", "HttpOnly", "SameSite=Lax", "Secure"):
            self.assertIn(attribute, clear)

    def test_origin_content_type_and_csrf_are_exact_and_fail_closed(self):
        session = session_record()
        invite = invite_record(status="exchanged")
        headers = [
            ("Origin", "https://app.cuevion.com"),
            ("Content-Type", "application/json; charset=utf-8"),
            ("X-Cuevion-CSRF", CSRF_TOKEN),
            ("Cookie", f"{guest_session.GUEST_SESSION_COOKIE_NAME}={SESSION_ID}"),
        ]
        with patch.dict(
            guest_session.os.environ,
            {"VERCEL_ENV": "production", "CUEVION_APP_ORIGIN": "https://app.cuevion.com"},
            clear=True,
        ), patch.object(
            guest_session, "_load_v2_guest_session_record",
            return_value={"status": "ok", "record": session},
        ), patch.object(
            guest_session, "_load_v2_invite_by_id",
            return_value={"status": "ok", "record": invite},
        ):
            self.assertEqual(
                guest_session.resolve_guest_v2_mutation_context("POST", headers, now=SEC + 200)["status"],
                "ok",
            )
            for index, value, code in (
                (0, "https://app.cuevion.com.attacker.test", "origin_rejected"),
                (1, "text/plain", "invalid_request"),
                (2, "wrong", "invalid_request"),
                (2, "x" * 43, "csrf_failed"),
            ):
                changed = list(headers)
                changed[index] = (changed[index][0], value)
                result = guest_session.resolve_guest_v2_mutation_context("POST", changed, now=SEC + 200)
                self.assertEqual(result["error"]["code"], code)
        with patch.dict(
            guest_session.os.environ, {"VERCEL_ENV": "production"}, clear=True
        ):
            no_config = guest_session.validate_guest_request_origin(
                [("Origin", "https://app.cuevion.com")]
            )
            self.assertEqual(no_config["error"]["code"], "origin_rejected")

    def test_guest_failures_are_new_allowlisted_objects_without_internal_records(self):
        forbidden = {"ownerEmail", "workspaceId", "mailboxId", "tokenHash", "sessionHash", "csrfTokenHash", "record"}
        leaked_invite = {**invite_record(status="revoked"), "tokenHash": "a" * 64}
        with patch.object(guest_session, "_load_v2_invite_by_token", return_value={"status": "revoked", "record": leaked_invite, "error": {"code": "invite_revoked"}}):
            result = guest_session.exchange_v2_invitation(INVITE_TOKEN, guest_display_name="Guest", now=SEC + 200)
        self.assertEqual(result, {"status": "error", "error": {"code": "invite_revoked"}})
        self.assertTrue(forbidden.isdisjoint(result))
        with patch.object(guest_session, "_load_v2_guest_session_record", return_value={"status": "expired", "record": session_record(), "error": {"code": "session_expired"}}):
            result = guest_session._bootstrap_v2_guest_session_read_only(
                SESSION_ID, now=SEC + 200
            )
        self.assertEqual(result, {"status": "error", "error": {"code": "session_expired"}})
        self.assertNotIn("record", result)
        for storage_result in (
            {"status": "malformed", "record": {"ownerEmail": "secret"}},
            {"status": "unexpected", "raw": "redis response"},
            {"status": "exchanged", "record": leaked_invite, "error": {"code": "invite_already_exchanged"}},
        ):
            with patch.object(guest_session, "_load_v2_invite_by_token", return_value=storage_result):
                result = guest_session.exchange_v2_invitation(INVITE_TOKEN, guest_display_name="Guest", now=SEC + 200)
            expected = "invite_already_exchanged" if storage_result["status"] == "exchanged" else "storage_protocol_error"
            self.assertEqual(result, {"status": "error", "error": {"code": expected}})
            self.assertNotIn("record", result)

    def test_cookie_origin_content_type_and_csrf_reject_injection(self):
        self.assertIsNone(guest_session.build_guest_session_cookie("bad; token\r\n", expires_at=SEC + 200, now=SEC + 100))
        with patch.dict(guest_session.os.environ, {"VERCEL_ENV": "development"}, clear=True):
            self.assertNotIn("Secure", guest_session.build_guest_session_cookie(SESSION_ID, expires_at=SEC + 200, now=SEC + 100))
            for origin in ("https://evil.test", "null", "https://localhost.attacker.test"):
                self.assertEqual(guest_session.validate_guest_request_origin([("Origin", origin)])["error"]["code"], "origin_rejected")
        headers = [
            ("Origin", "https://app.cuevion.com"),
            ("Content-Type", "application/json; boundary=x"),
            ("X-Cuevion-CSRF", CSRF_TOKEN),
            ("Cookie", f"{guest_session.GUEST_SESSION_COOKIE_NAME}={SESSION_ID}"),
        ]
        with patch.dict(guest_session.os.environ, {"VERCEL_ENV": "production", "CUEVION_APP_ORIGIN": "https://app.cuevion.com"}, clear=True), patch.object(guest_session, "_load_v2_guest_session_record", return_value={"status": "ok", "record": session_record()}), patch.object(guest_session, "_load_v2_invite_by_id", return_value={"status": "ok", "record": invite_record(status="exchanged")}):
            self.assertEqual(guest_session.resolve_guest_v2_mutation_context("POST", headers, now=SEC + 200)["error"]["code"], "invalid_request")
            headers[1] = ("Content-Type", "application/json")
            for malformed_csrf in ("bad token", f" {CSRF_TOKEN}", f"{CSRF_TOKEN}\r\n"):
                headers[2] = ("X-Cuevion-CSRF", malformed_csrf)
                result = guest_session.resolve_guest_v2_mutation_context("POST", headers, now=SEC + 200)
                self.assertEqual(result["error"]["code"], "invalid_request")

    def test_request_semantics_are_rejected_before_every_storage_resolver(self):
        headers = [
            ("Origin", "https://app.cuevion.com"),
            ("Content-Type", "application/json"),
            ("X-Cuevion-CSRF", CSRF_TOKEN),
            ("Cookie", f"{guest_session.GUEST_SESSION_COOKIE_NAME}={SESSION_ID}"),
        ]
        cases = {
            "method": ("post", headers),
            "origin": (
                "POST",
                [("Origin", "https://evil.test"), *headers[1:]],
            ),
            "origin_uppercase_host": (
                "POST",
                [("Origin", "https://APP.cuevion.com"), *headers[1:]],
            ),
            "origin_explicit_default_port": (
                "POST",
                [("Origin", "https://app.cuevion.com:443"), *headers[1:]],
            ),
            "origin_trailing_slash": (
                "POST",
                [("Origin", "https://app.cuevion.com/"), *headers[1:]],
            ),
            "content_type": (
                "POST",
                [headers[0], ("Content-Type", "Application/JSON"), *headers[2:]],
            ),
            "cookie": (
                "POST",
                [*headers[:3], ("Cookie", f"{headers[3][1]}, other=value")],
            ),
            "csrf_syntax": (
                "POST",
                [*headers[:2], ("X-Cuevion-CSRF", "bad token"), headers[3]],
            ),
        }
        with patch.dict(
            guest_session.os.environ,
            {"VERCEL_ENV": "production", "CUEVION_APP_ORIGIN": "https://app.cuevion.com"},
            clear=True,
        ):
            for case, (method, changed) in cases.items():
                with self.subTest(case=case), patch.object(
                    guest_session, "_load_v2_guest_session_record"
                ) as session_loader, patch.object(
                    guest_session, "_load_v2_invite_by_id"
                ) as invite_loader:
                    rejected = guest_session.resolve_guest_v2_mutation_context(
                        method, changed, now=SEC + 200
                    )
                    self.assertNotEqual(rejected["status"], "ok")
                    session_loader.assert_not_called()
                    invite_loader.assert_not_called()

        for configured_origin in (
            " https://app.cuevion.com",
            "https://app.cuevion.com ",
            "https://APP.cuevion.com",
            "https://app.cuevion.com:443",
            "https://app.cuevion.com/",
        ):
            with self.subTest(configured_origin=configured_origin), patch.dict(
                guest_session.os.environ,
                {"VERCEL_ENV": "production", "CUEVION_APP_ORIGIN": configured_origin},
                clear=True,
            ), patch.object(
                guest_session, "_load_v2_guest_session_record"
            ) as session_loader, patch.object(
                guest_session, "_load_v2_invite_by_id"
            ) as invite_loader:
                rejected = guest_session.resolve_guest_v2_mutation_context(
                    "POST", headers, now=SEC + 200
                )
                self.assertEqual(rejected["error"]["code"], "origin_rejected")
                session_loader.assert_not_called()
                invite_loader.assert_not_called()

        localhost_headers = [
            ("Origin", "http://localhost:5173"),
            *headers[1:],
        ]
        for configured_origin in ("not-an-origin", " http://localhost:5173"):
            with self.subTest(development_configured_origin=configured_origin), patch.dict(
                guest_session.os.environ,
                {"VERCEL_ENV": "development", "CUEVION_APP_ORIGIN": configured_origin},
                clear=True,
            ), patch.object(
                guest_session, "_load_v2_guest_session_record"
            ) as session_loader, patch.object(
                guest_session, "_load_v2_invite_by_id"
            ) as invite_loader:
                rejected = guest_session.resolve_guest_v2_mutation_context(
                    "POST", localhost_headers, now=SEC + 200
                )
                self.assertEqual(rejected["error"]["code"], "origin_rejected")
                session_loader.assert_not_called()
                invite_loader.assert_not_called()

    def test_mutation_resolver_requires_every_raw_request_check_and_rejects_duplicates(self):
        headers = [
            ("Origin", "https://app.cuevion.com"),
            ("Content-Type", "application/json"),
            ("X-Cuevion-CSRF", CSRF_TOKEN),
            ("Cookie", f"{guest_session.GUEST_SESSION_COOKIE_NAME}={SESSION_ID}"),
        ]
        with patch.object(guest_session, "_load_v2_guest_session_record") as loader:
            for index in range(len(headers)):
                result = guest_session.resolve_guest_v2_mutation_context(
                    "POST", headers[:index] + headers[index + 1 :], now=SEC + 200
                )
                self.assertNotEqual(result["status"], "ok")
            self.assertNotEqual(
                guest_session.resolve_guest_v2_mutation_context("GET", headers, now=SEC + 200)["status"],
                "ok",
            )
            self.assertFalse(loader.called)

        for index, duplicate_name in enumerate(("origin", "CONTENT-TYPE", "x-cuevion-csrf", "COOKIE")):
            duplicated = [*headers, (duplicate_name, headers[index][1])]
            self.assertNotEqual(
                guest_session.resolve_guest_v2_mutation_context("POST", duplicated, now=SEC + 200)["status"],
                "ok",
            )
        for malformed in (
            [("Origin", "https://app.\ncuevion.com"), *headers[1:]],
            [("Origin", "https://app.cuevion.com\u202e"), *headers[1:]],
            {name: value for name, value in headers},
        ):
            self.assertNotEqual(
                guest_session.resolve_guest_v2_mutation_context("POST", malformed, now=SEC + 200)["status"],
                "ok",
            )

        invalid_requests = (
            ("GET", headers),
            ("POST", headers[:-1]),
            ("POST", [*headers, ("Origin", headers[0][1])]),
            ("POST", [*headers, ("X-Cuevion-CSRF", headers[2][1])]),
            ("POST", [headers[0], ("Content-Type", "text/plain"), *headers[2:]]),
            ("POST", [headers[0], headers[1], ("X-Cuevion-CSRF", "x" * 43), headers[3]]),
        )
        with patch.dict(
            guest_session.os.environ,
            {"VERCEL_ENV": "production", "CUEVION_APP_ORIGIN": "https://app.cuevion.com"},
            clear=True,
        ), patch.object(
            guest_session, "_load_v2_guest_session_record",
            return_value={"status": "ok", "record": session_record()},
        ), patch.object(
            guest_session, "_load_v2_invite_by_id",
            return_value={"status": "ok", "record": invite_record(status="exchanged")},
        ), patch.object(guest_session, "_revoke_v2_guest_session") as storage_logout:
            for invalid_method, invalid_headers in invalid_requests:
                result = guest_session.resolve_guest_v2_mutation_context(
                    invalid_method, invalid_headers, now=SEC + 200
                )
                self.assertNotEqual(result["status"], "ok")
            storage_logout.assert_not_called()

        for linkage_case, linked_session, linked_invite in (
            (
                "session",
                {**session_record(), "collaborationId": "B" * 22},
                invite_record(status="exchanged"),
            ),
            (
                "invitation",
                session_record(),
                {**invite_record(status="exchanged"), "activeSessionHash": "f" * 64},
            ),
        ):
            with self.subTest(linkage=linkage_case), patch.dict(
                guest_session.os.environ,
                {"VERCEL_ENV": "production", "CUEVION_APP_ORIGIN": "https://app.cuevion.com"},
                clear=True,
            ), patch.object(
                guest_session, "_load_v2_guest_session_record",
                return_value={"status": "ok", "record": linked_session},
            ), patch.object(
                guest_session, "_load_v2_invite_by_id",
                return_value={"status": "ok", "record": linked_invite},
            ), patch.object(guest_session, "_revoke_v2_guest_session") as storage_logout:
                result = guest_session.resolve_guest_v2_mutation_context(
                    "POST", headers, now=SEC + 200
                )
                self.assertNotEqual(result["status"], "ok")
                storage_logout.assert_not_called()

        with patch.dict(guest_session.os.environ, {"VERCEL_ENV": "production", "CUEVION_APP_ORIGIN": "https://app.cuevion.com"}, clear=True), patch.object(guest_session, "_load_v2_guest_session_record", return_value={"status": "ok", "record": session_record()}), patch.object(guest_session, "_load_v2_invite_by_id", return_value={"status": "ok", "record": invite_record(status="exchanged")}):
            resolved = guest_session.resolve_guest_v2_mutation_context("POST", headers, now=SEC + 200)
        self.assertEqual(resolved["status"], "ok")
        self.assertTrue(guest_session._is_guest_mutation_capability(resolved["context"]))
        self.assertFalse(guest_session._is_guest_read_capability(resolved["context"]))

        with patch.object(guest_session, "_revoke_v2_guest_session", return_value={"status": "ok"}) as revoke:
            logged_out = guest_session.logout_v2_guest_session(
                resolved["context"], now=SEC + 201
            )
        self.assertEqual(logged_out, {"status": "ok", "error": None})
        revoke.assert_called_once_with(
            session_record()["sessionHash"],
            invite_id=session_record()["inviteId"],
            owner_email=session_record()["ownerEmail"],
            workspace_id=session_record()["workspaceId"],
            mailbox_id=session_record()["mailboxId"],
            collaboration_id=session_record()["collaborationId"],
            now=SEC + 201,
            command_transport=None,
        )

        with patch.object(guest_session, "_revoke_v2_guest_session") as revoke:
            forged_fields = {
                name: getattr(resolved["context"], name)
                for name in (
                    "session_hash", "invite_id", "owner_email", "workspace_id",
                    "mailbox_id", "collaboration_id", "guest_display_name", "expires_at",
                )
            }
            read_context = guest_session._GuestReadCapability(
                guest_session._GUEST_READ_SENTINEL,
                session_record()["sessionHash"], session_record()["inviteId"],
                session_record()["ownerEmail"], session_record()["workspaceId"],
                session_record()["mailboxId"], session_record()["collaborationId"],
                session_record()["guestDisplayName"], session_record()["expiresAt"],
            )
            for forbidden_context in (
                SESSION_ID,
                session_record(),
                forged_fields,
                SimpleNamespace(**forged_fields),
                read_context,
                internal_capability("reply"),
                replace(resolved["context"], _sentinel=object()),
            ):
                rejected = guest_session.logout_v2_guest_session(
                    forbidden_context, now=SEC + 201
                )
                self.assertEqual(rejected["error"]["code"], "invalid_request")
            revoke.assert_not_called()

        with patch.object(
            guest_session,
            "_revoke_v2_guest_session",
            return_value={"status": "malformed", "record": session_record()},
        ):
            stale_scope = replace(resolved["context"], mailbox_id="mailbox-other")
            rejected = guest_session.logout_v2_guest_session(stale_scope, now=SEC + 201)
        self.assertEqual(
            rejected, {"status": "error", "error": {"code": "storage_protocol_error"}}
        )
        self.assertNotIn("record", rejected)

    def test_comma_combined_security_headers_fail_before_session_resolution(self):
        headers = [
            ("Origin", "https://app.cuevion.com"),
            ("Content-Type", "application/json"),
            ("X-Cuevion-CSRF", CSRF_TOKEN),
            ("Cookie", f"{guest_session.GUEST_SESSION_COOKIE_NAME}={SESSION_ID}"),
        ]
        combined = (
            "https://app.cuevion.com, https://evil.test",
            "application/json, text/plain",
            f"{CSRF_TOKEN}, {'x' * 43}",
            f"{guest_session.GUEST_SESSION_COOKIE_NAME}={SESSION_ID}, other=value",
        )
        for index, value in enumerate(combined):
            changed = list(headers)
            changed[index] = (changed[index][0], value)
            with self.subTest(header=changed[index][0]), patch.object(
                guest_session, "_load_v2_guest_session_record"
            ) as session_loader, patch.object(
                guest_session, "_load_v2_invite_by_id"
            ) as invite_loader:
                result = guest_session.resolve_guest_v2_mutation_context(
                    "POST", changed, now=SEC + 200
                )
                self.assertEqual(result["error"]["code"], "invalid_request")
                session_loader.assert_not_called()
                invite_loader.assert_not_called()

        raw_cookie = f"{guest_session.GUEST_SESSION_COOKIE_NAME}={SESSION_ID}"
        malformed_cookies = (
            f"{raw_cookie}, other=value",
            f" {raw_cookie}",
            f"{raw_cookie} ",
            f"other=value;  {raw_cookie}",
            f'other="unterminated; {raw_cookie}',
            f"other=bad\\value; {raw_cookie}",
            f"{guest_session.GUEST_SESSION_COOKIE_NAME}= {SESSION_ID}",
            f"{guest_session.GUEST_SESSION_COOKIE_NAME}=\t{SESSION_ID}",
            f"{guest_session.GUEST_SESSION_COOKIE_NAME} ={SESSION_ID}",
            (
                f"{guest_session.GUEST_SESSION_COOKIE_NAME} ={'x' * 43}; "
                f"{raw_cookie}"
            ),
            (
                f"{raw_cookie}; "
                f"{guest_session.GUEST_SESSION_COOKIE_NAME} ={'x' * 43}"
            ),
        )
        for malformed in malformed_cookies:
            with self.subTest(cookie=malformed):
                self.assertIsNone(guest_session.read_guest_session_cookie([
                    ("Cookie", malformed)
                ]))
            changed = [*headers[:3], ("Cookie", malformed)]
            with patch.dict(
                guest_session.os.environ,
                {"VERCEL_ENV": "production", "CUEVION_APP_ORIGIN": "https://app.cuevion.com"},
                clear=True,
            ), patch.object(
                guest_session, "_load_v2_guest_session_record"
            ) as session_loader:
                result = guest_session.resolve_guest_v2_mutation_context(
                    "POST", changed, now=SEC + 200
                )
                self.assertEqual(result["error"]["code"], "invalid_request")
                session_loader.assert_not_called()

    def test_mutation_resolver_rejects_backward_session_clock_before_invite_lookup(self):
        headers = [
            ("Origin", "https://app.cuevion.com"),
            ("Content-Type", "application/json"),
            ("X-Cuevion-CSRF", CSRF_TOKEN),
            ("Cookie", f"{guest_session.GUEST_SESSION_COOKIE_NAME}={SESSION_ID}"),
        ]
        future_session = {**session_record(), "lastUsedAt": SEC + 250}
        with patch.dict(
            guest_session.os.environ,
            {"VERCEL_ENV": "production", "CUEVION_APP_ORIGIN": "https://app.cuevion.com"},
            clear=True,
        ), patch.object(
            guest_session, "_load_v2_guest_session_record",
            return_value={"status": "ok", "record": future_session},
        ), patch.object(guest_session, "_load_v2_invite_by_id") as invite_loader:
            result = guest_session.resolve_guest_v2_mutation_context(
                "POST", headers, now=SEC + 200
            )
        self.assertEqual(result["error"]["code"], "invalid_request")
        invite_loader.assert_not_called()

    def test_logout_python_chronology_rejects_backward_and_equal_first_transition(self):
        record = session_record()
        capability = guest_session._GuestMutationCapability(
            _sentinel=guest_session._GUEST_MUTATION_SENTINEL,
            session_hash=record["sessionHash"],
            invite_id=record["inviteId"],
            owner_email=record["ownerEmail"],
            workspace_id=record["workspaceId"],
            mailbox_id=record["mailboxId"],
            collaboration_id=record["collaborationId"],
            guest_display_name=record["guestDisplayName"],
            expires_at=record["expiresAt"],
            created_at=record["createdAt"],
            last_used_at=record["lastUsedAt"],
        )
        with patch.object(guest_session, "_revoke_v2_guest_session") as revoke:
            result = guest_session.logout_v2_guest_session(
                capability, now=record["lastUsedAt"] - 1
            )
            self.assertEqual(result["error"]["code"], "invalid_request")
            revoke.assert_not_called()
        with patch.object(
            guest_session, "_revoke_v2_guest_session", return_value={"status": "ok"}
        ) as revoke:
            result = guest_session.logout_v2_guest_session(
                capability, now=record["lastUsedAt"]
            )
            self.assertEqual(result["error"]["code"], "invalid_request")
            revoke.assert_not_called()


if __name__ == "__main__":
    unittest.main()
