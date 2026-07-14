from __future__ import annotations

import unittest
from collections.abc import Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch
import os

from . import guest_session, mutations
from .authorization import resolve_internal_collaboration_context
from .models import hash_v2_secret
from .mutations import append_guest_v2_reply, append_internal_v2_message

SEC = 1_800_000_000
MS = SEC * 1000


def thread_record() -> dict:
    return {
        "v": 2, "collaborationId": "A" * 22,
        "ownerEmail": "owner@example.com", "workspaceId": "owner@example.com",
        "mailboxId": "mailbox-1",
        "sourceRef": {"provider": "google", "providerMessageId": "gmail-1"},
        "sourceMessage": {"subject": "Review", "senderDisplay": "Sender", "fromDisplay": "sender@example.com", "timestamp": "today", "bodyText": "Body"},
        "state": "needs_review", "messages": [], "createdAt": MS + 100, "updatedAt": MS + 100,
    }


def internal_capability(action: str):
    result = resolve_internal_collaboration_context(
        [], "mailbox-1", collaboration_id="A" * 22, required_action=action,
        user_resolver=lambda _headers: ({"email": "owner@example.com", "name": "Owner"}, None),
        mailbox_resolver=lambda _headers, mailbox_id: {
            "status": "ok", "user": {"email": "owner@example.com"},
            "inbox": {"id": mailbox_id, "provider": "google"},
        },
        thread_loader=lambda _id: {"status": "ok", "record": thread_record()},
    )
    return result["context"]


def guest_mutation_capability():
    session = {
        "v": 2, "sessionHash": hash_v2_secret("s" * 43), "inviteId": "I" * 22,
        "ownerEmail": "owner@example.com", "workspaceId": "owner@example.com",
        "mailboxId": "mailbox-1", "collaborationId": "A" * 22,
        "allowedActions": ["read", "reply"], "visibility": "shared_only",
        "identityAssurance": "link_possession", "guestDisplayName": "Guest",
        "createdAt": SEC + 100, "lastUsedAt": SEC + 100, "expiresAt": SEC + 500,
        "status": "active", "csrfTokenHash": hash_v2_secret("c" * 43),
        "revokedAt": None, "loggedOutAt": None,
    }
    invite = {
        "v": 2, "inviteId": "I" * 22, "tokenHash": "a" * 64,
        "ownerEmail": "owner@example.com", "workspaceId": "owner@example.com",
        "mailboxId": "mailbox-1", "collaborationId": "A" * 22,
        "identityAssurance": "link_possession", "allowedActions": ["read", "reply"],
        "visibility": "shared_only",
        "createdBy": {"ownerEmail": "owner@example.com", "displayName": "Owner"},
        "createdAt": SEC + 50, "expiresAt": SEC + 500, "status": "exchanged", "exchangedAt": SEC + 100,
        "exchangeCount": 1, "revokedAt": None, "revokedBy": None,
        "activeSessionHash": session["sessionHash"],
    }
    headers = [
        ("Origin", "https://app.cuevion.com"), ("Content-Type", "application/json"),
        ("X-Cuevion-CSRF", "c" * 43),
        ("Cookie", f"{guest_session.GUEST_SESSION_COOKIE_NAME}={'s' * 43}"),
    ]
    with patch.dict(os.environ, {"VERCEL_ENV": "production", "CUEVION_APP_ORIGIN": "https://app.cuevion.com"}, clear=True), patch.object(guest_session, "_load_v2_guest_session_record", return_value={"status": "ok", "record": session}), patch.object(guest_session, "_load_v2_invite_by_id", return_value={"status": "ok", "record": invite}):
        return guest_session.resolve_guest_v2_mutation_context("POST", headers, now=SEC + 101)["context"]


def guest_read_capability():
    mutation = guest_mutation_capability()
    session = {
        "v": 2, "sessionHash": mutation.session_hash, "inviteId": mutation.invite_id,
        "ownerEmail": mutation.owner_email, "workspaceId": mutation.workspace_id,
        "mailboxId": mutation.mailbox_id, "collaborationId": mutation.collaboration_id,
        "allowedActions": ["read", "reply"], "visibility": "shared_only",
        "identityAssurance": "link_possession", "guestDisplayName": mutation.guest_display_name,
        "createdAt": SEC + 100, "lastUsedAt": SEC + 100, "expiresAt": mutation.expires_at,
        "status": "active", "csrfTokenHash": hash_v2_secret("c" * 43),
        "revokedAt": None, "loggedOutAt": None,
    }
    invite = {
        "v": 2, "inviteId": mutation.invite_id, "tokenHash": "a" * 64,
        "ownerEmail": mutation.owner_email, "workspaceId": mutation.workspace_id,
        "mailboxId": mutation.mailbox_id, "collaborationId": mutation.collaboration_id,
        "identityAssurance": "link_possession", "allowedActions": ["read", "reply"],
        "visibility": "shared_only", "createdBy": {"ownerEmail": mutation.owner_email, "displayName": "Owner"},
        "createdAt": SEC + 50, "expiresAt": mutation.expires_at, "status": "exchanged",
        "exchangedAt": SEC + 100, "exchangeCount": 1, "revokedAt": None,
        "revokedBy": None, "activeSessionHash": mutation.session_hash,
    }
    with patch.object(guest_session, "_load_v2_guest_session_record", return_value={"status": "ok", "record": session}), patch.object(guest_session, "_load_v2_invite_by_id", return_value={"status": "ok", "record": invite}):
        read, _, error = guest_session._resolve_guest_read_access("s" * 43, now=SEC + 101)
    if error:
        raise AssertionError(error)
    return read


class CollaborationV2MutationTests(unittest.TestCase):
    def test_internal_message_loads_scope_constructs_server_fields_and_uses_cas(self):
        saved = []
        context = internal_capability("internal_note")
        with patch.object(mutations.time, "time_ns", return_value=(MS + 101) * 1_000_000):
            result = append_internal_v2_message(
                context, "Internal note", visibility="internal",
                thread_loader=lambda *_args, **_kwargs: {"status": "ok", "record": thread_record()},
                thread_saver=lambda record, expected, **_kwargs: saved.append((record, expected)) or {"status": "ok", "record": record},
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(saved[0][1], MS + 100)
        message = saved[0][0]["messages"][0]
        self.assertEqual((message["authorKind"], message["visibility"], message["createdAt"]), ("owner", "internal", MS + 101))
        self.assertRegex(message["id"], r"^[A-Za-z0-9_-]{22,128}$")

    def test_guest_reply_is_always_shared_and_scope_checked(self):
        saved = []
        session = guest_mutation_capability()
        with patch.object(mutations.time, "time_ns", return_value=(MS + 101) * 1_000_000), patch.object(mutations.time, "time", return_value=SEC + 101):
            result = append_guest_v2_reply(
                session, "Shared reply",
                thread_loader=lambda *_args, **_kwargs: {"status": "ok", "record": thread_record()},
                thread_saver=lambda record, expected, **kwargs: saved.append((record, expected, kwargs)) or {"status": "ok", "record": record},
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(saved[0][0]["messages"][0]["visibility"], "shared")
        self.assertIs(saved[0][2]["session_context"], session)
        self.assertEqual(saved[0][2]["now"], SEC + 101)
        forged = {"copied": session, "mailbox_id": "mailbox-other"}
        with patch.object(mutations.time, "time_ns", return_value=(MS + 101) * 1_000_000), patch.object(mutations.time, "time", return_value=SEC + 101):
            denied = append_guest_v2_reply(
                forged, "Shared reply",
                thread_loader=lambda *_args, **_kwargs: {"status": "ok", "record": thread_record()},
                thread_saver=lambda *_args, **_kwargs: self.fail("scope failure must precede write"),
            )
        self.assertEqual(denied["error"]["code"], "session_revoked")

    def test_guest_reply_never_falls_back_to_a_simple_thread_cas(self):
        session = guest_mutation_capability()
        calls = []

        def ordinary_cas_only(record, expected):
            calls.append((record, expected))
            return {"status": "ok", "record": record}

        with patch.object(mutations.time, "time_ns", return_value=(MS + 101) * 1_000_000), patch.object(mutations.time, "time", return_value=SEC + 101):
            result = append_guest_v2_reply(
                session,
                "Shared reply",
                thread_loader=lambda *_args, **_kwargs: {"status": "ok", "record": thread_record()},
                thread_saver=ordinary_cas_only,
            )
        self.assertEqual(result["error"]["code"], "storage_unavailable")
        self.assertEqual(calls, [])

    def test_guest_reply_rejects_backward_session_clock_before_storage(self):
        session = guest_mutation_capability()
        with patch.object(
            mutations.time, "time", return_value=session.last_used_at - 1
        ):
            result = append_guest_v2_reply(
                session,
                "Shared reply",
                thread_loader=lambda *_args, **_kwargs: self.fail(
                    "backward chronology must fail before thread storage"
                ),
                thread_saver=lambda *_args, **_kwargs: self.fail(
                    "backward chronology must fail before mutation"
                ),
            )
        self.assertEqual(result["error"]["code"], "invalid_request")
        with patch.object(mutations.time, "time", return_value=session.expires_at):
            result = append_guest_v2_reply(
                session,
                "Shared reply",
                thread_loader=lambda *_args, **_kwargs: self.fail(
                    "expired session must fail before thread storage"
                ),
                thread_saver=lambda *_args, **_kwargs: self.fail(
                    "expired session must fail before mutation"
                ),
            )
        self.assertEqual(result["error"]["code"], "session_expired")

    def test_guest_atomic_saver_invalidation_errors_are_allowlisted(self):
        session = guest_mutation_capability()
        for code in ("session_revoked", "session_expired"):
            with self.subTest(code=code), patch.object(mutations.time, "time_ns", return_value=(MS + 101) * 1_000_000), patch.object(mutations.time, "time", return_value=SEC + 101):
                result = append_guest_v2_reply(
                    session,
                    "Shared reply",
                    thread_loader=lambda *_args, **_kwargs: {"status": "ok", "record": thread_record()},
                    thread_saver=lambda *_args, **_kwargs: {"status": "revoked", "error": {"code": code}},
                )
            self.assertEqual(result, {"status": "error", "error": {"code": code}})

    def test_mutation_rejects_untrusted_roles_timestamps_and_extra_content(self):
        context = internal_capability("reply")
        with patch.object(mutations.time, "time_ns", return_value=(MS + 101) * 1_000_000):
            result = append_internal_v2_message(
                context, {"text": "x", "authorKind": "guest", "bodyHtml": "<b>x</b>"},
                visibility="shared",
                thread_loader=lambda *_args, **_kwargs: {"status": "ok", "record": thread_record()},
                thread_saver=lambda *_args, **_kwargs: self.fail("invalid input must not write"),
            )
        self.assertEqual(result["error"]["code"], "invalid_request")

    def test_exact_capability_types_reject_all_mapping_and_duck_typed_forgeries(self):
        internal = internal_capability("reply")

        class MappingForgery(dict):
            pass

        @dataclass(frozen=True)
        class CapabilityLookalike:
            owner_email: str
            workspace_id: str
            mailbox_id: str
            collaboration_id: str
            action: str
            actor_kind: str
            actor_display_name: str

        copied = {
            "owner_email": internal.owner_email, "workspace_id": internal.workspace_id,
            "mailbox_id": internal.mailbox_id, "collaboration_id": internal.collaboration_id,
            "action": internal.action, "actor_kind": internal.actor_kind,
            "actor_display_name": internal.actor_display_name,
        }
        for forged in (
            dict(copied), MappingForgery(copied), SimpleNamespace(**copied),
            CapabilityLookalike(**copied), guest_mutation_capability(), guest_read_capability(),
        ):
            denied = append_internal_v2_message(
                forged, "message", visibility="shared",
                thread_loader=lambda *_args, **_kwargs: self.fail("forgery must fail before read"),
                thread_saver=lambda *_args, **_kwargs: self.fail("forgery must fail before write"),
            )
            self.assertEqual(denied["error"]["code"], "forbidden")

        for forged in (
            dict(copied), MappingForgery(copied), SimpleNamespace(**copied),
            internal, guest_read_capability(), {"status": "active"}, "s" * 43,
        ):
            denied = append_guest_v2_reply(
                forged, "message",
                thread_loader=lambda *_args, **_kwargs: self.fail("forgery must fail before read"),
                thread_saver=lambda *_args, **_kwargs: self.fail("forgery must fail before write"),
            )
            self.assertEqual(denied["error"]["code"], "session_revoked")

    def test_two_messages_in_one_original_clock_tick_still_advance_monotonically(self):
        capability = internal_capability("reply")
        current = thread_record()

        def load(*_args, **_kwargs):
            return {"status": "ok", "record": current}

        def save(record, expected, **_kwargs):
            nonlocal current
            self.assertEqual(expected, current["updatedAt"])
            current = record
            return {"status": "ok", "record": record}

        with patch.object(mutations.time, "time_ns", return_value=(MS + 101) * 1_000_000):
            first = append_internal_v2_message(
                capability, "first", visibility="shared", thread_loader=load, thread_saver=save
            )
            second = append_internal_v2_message(
                capability, "second", visibility="shared", thread_loader=load, thread_saver=save
            )
        self.assertEqual(first["updatedAt"], MS + 101)
        self.assertEqual(second["updatedAt"], MS + 102)
        self.assertEqual([message["createdAt"] for message in current["messages"]], [MS + 101, MS + 102])


if __name__ == "__main__":
    unittest.main()
