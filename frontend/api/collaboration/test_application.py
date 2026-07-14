from __future__ import annotations

import inspect
import json
import unittest
from contextlib import contextmanager
from dataclasses import dataclass
from unittest.mock import patch

from api.collaboration import application, authorization, guest_session, models, redis_store
from api.collaboration.v2_stateful_test_store import StatefulV2Store


COLLABORATION_ID = "A" * 22
OTHER_COLLABORATION_ID = "B" * 22
INVITE_ID = "I" * 22
MAILBOX_ID = "mailbox-1"
OWNER_EMAIL = "owner@example.com"
OTHER_OWNER_EMAIL = "other@example.com"
NOW = 1_800_000_000
RAW_SESSION_ID = "RawGuestSessionPrivateMarker" + ("x" * 15)
PRIVATE_SOURCE_MARKER = "PrivateProviderMessageMarker_42"
PRIVATE_EXCEPTION_MARKER = "PrivateRedisExceptionMarker_42"
CSRF_HASH = "c" * 64


def _thread_record() -> dict:
    return {
        "v": 2,
        "collaborationId": COLLABORATION_ID,
        "ownerEmail": OWNER_EMAIL,
        "workspaceId": OWNER_EMAIL,
        "mailboxId": MAILBOX_ID,
        "sourceRef": {
            "provider": "google",
            "providerMessageId": PRIVATE_SOURCE_MARKER,
        },
        "sourceMessage": {
            "subject": "Quarterly launch review",
            "senderDisplay": "Alex Sender",
            "fromDisplay": "Alex Sender <alex@example.net>",
            "timestamp": "1712345678901",
            "bodyText": "Please review the launch details.",
        },
        "state": "needs_review",
        "messages": [
            {
                "id": "S" * 22,
                "authorKind": "owner",
                "authorDisplayName": "Owner Person",
                "text": "Shared owner reply",
                "visibility": "shared",
                "createdAt": (NOW * 1000) - 300,
            },
            {
                "id": "N" * 22,
                "authorKind": "internal",
                "authorDisplayName": "Internal Teammate",
                "text": "Internal-only note",
                "visibility": "internal",
                "createdAt": (NOW * 1000) - 200,
            },
            {
                "id": "G" * 22,
                "authorKind": "guest",
                "authorDisplayName": "Guest Reviewer",
                "text": "Shared guest reply",
                "visibility": "shared",
                "createdAt": (NOW * 1000) - 100,
            },
        ],
        "createdAt": (NOW * 1000) - 1000,
        "updatedAt": NOW * 1000,
    }


def _session_record() -> dict:
    session_hash = models.hash_v2_secret(RAW_SESSION_ID)
    assert session_hash is not None
    return {
        "v": 2,
        "sessionHash": session_hash,
        "inviteId": INVITE_ID,
        "ownerEmail": OWNER_EMAIL,
        "workspaceId": OWNER_EMAIL,
        "mailboxId": MAILBOX_ID,
        "collaborationId": COLLABORATION_ID,
        "allowedActions": ["read", "reply"],
        "visibility": "shared_only",
        "identityAssurance": "link_possession",
        "guestDisplayName": "Guest Reviewer",
        "createdAt": NOW - 50,
        "lastUsedAt": NOW - 10,
        "expiresAt": NOW + 1800,
        "status": "active",
        "csrfTokenHash": CSRF_HASH,
        "revokedAt": None,
        "loggedOutAt": None,
    }


def _invite_record() -> dict:
    session_hash = models.hash_v2_secret(RAW_SESSION_ID)
    assert session_hash is not None
    return {
        "v": 2,
        "inviteId": INVITE_ID,
        "tokenHash": "a" * 64,
        "ownerEmail": OWNER_EMAIL,
        "workspaceId": OWNER_EMAIL,
        "mailboxId": MAILBOX_ID,
        "collaborationId": COLLABORATION_ID,
        "identityAssurance": "link_possession",
        "allowedActions": ["read", "reply"],
        "visibility": "shared_only",
        "createdBy": {
            "ownerEmail": OWNER_EMAIL,
            "displayName": "Owner Person",
        },
        "createdAt": NOW - 100,
        "expiresAt": NOW + 3600,
        "status": "exchanged",
        "exchangedAt": NOW - 50,
        "exchangeCount": 1,
        "revokedAt": None,
        "revokedBy": None,
        "activeSessionHash": session_hash,
    }


def _walk(value: object):
    yield value
    if type(value) is dict:
        for key, entry in value.items():
            yield key
            yield from _walk(entry)
    elif type(value) in (list, tuple):
        for entry in value:
            yield from _walk(entry)


def _all_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if type(value) is dict:
        keys.update(value)
        for entry in value.values():
            keys.update(_all_keys(entry))
    elif type(value) in (list, tuple):
        for entry in value:
            keys.update(_all_keys(entry))
    return keys


def _public_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _owner_capability(thread: dict | None = None):
    record = _thread_record() if thread is None else thread
    result = authorization.resolve_internal_collaboration_context(
        [("Authorization", "private-request-marker")],
        collaboration_id=COLLABORATION_ID,
        required_action="read",
        user_resolver=lambda _headers: (
            {"email": OWNER_EMAIL, "name": "Owner Person"},
            None,
        ),
        mailbox_resolver=lambda _headers, mailbox_id: {
            "status": "ok",
            "user": {"email": OWNER_EMAIL, "name": "Owner Person"},
            "inbox": {"id": mailbox_id, "provider": "google"},
        },
        thread_loader=lambda _collaboration_id: redis_store._V2RecordResult(record),
    )
    assert result["status"] == "ok"
    assert authorization._is_internal_capability(
        result["context"], actions={"read"}
    )
    return result["context"]


def _guest_store(
    *,
    thread: dict | None = None,
    session: dict | None = None,
    invite: dict | None = None,
) -> StatefulV2Store:
    store = StatefulV2Store()
    thread_record = _thread_record() if thread is None else thread
    session_record = _session_record() if session is None else session
    invite_record = _invite_record() if invite is None else invite
    store.put_json(
        redis_store.build_v2_thread_key(COLLABORATION_ID),
        thread_record,
    )
    store.put_json(
        redis_store.build_v2_guest_session_key(session_record["sessionHash"]),
        session_record,
    )
    store.put_json(
        redis_store.build_v2_invite_key(invite_record["inviteId"]),
        invite_record,
    )
    return store


def _guest_headers() -> list[tuple[str, str]]:
    return [
        ("X-Ignored", "kept-outside-the-security-boundary"),
        (
            "Cookie",
            f"theme=dark; {guest_session.GUEST_SESSION_COOKIE_NAME}={RAW_SESSION_ID}",
        ),
    ]


@contextmanager
def _stateful_backend(
    store: StatefulV2Store,
    events: list[tuple[str, object]] | None = None,
):
    def perform(_config: dict, command: list) -> dict:
        if events is not None:
            events.append(("storage", tuple(command)))
        return store(command)

    with patch.object(
        redis_store,
        "_resolve_durable_store_config",
        return_value={"rest_url": "unused", "rest_token": "unused"},
    ), patch.object(
        redis_store,
        "_perform_v2_rest_command",
        side_effect=perform,
    ):
        yield


def _read_guest(store: StatefulV2Store) -> dict:
    with _stateful_backend(store), patch.object(
        application.time, "time", return_value=NOW
    ):
        return application.read_v2_collaboration_for_guest(_guest_headers())


def _guest_read_capability_and_session() -> tuple[object, dict]:
    store = _guest_store()
    capability, session, access_error = guest_session._resolve_guest_read_access(
        RAW_SESSION_ID,
        now=NOW,
        command_transport=store,
    )
    assert access_error is None
    assert guest_session._is_guest_read_capability(capability)
    assert type(session) is dict
    return capability, session


class OwnerReadApplicationTests(unittest.TestCase):
    def _read_with_store(self, thread: dict):
        capability = _owner_capability()
        store = StatefulV2Store()
        store.put_json(
            redis_store.build_v2_thread_key(COLLABORATION_ID),
            thread,
        )
        headers = [("Authorization", "private-request-marker")]
        authorization_result = {
            "status": "ok",
            "context": capability,
            "error": None,
        }
        with _stateful_backend(store), patch.object(
            application,
            "resolve_internal_collaboration_context",
            return_value=authorization_result,
        ) as resolver:
            result = application.read_v2_collaboration_for_owner(
                headers,
                COLLABORATION_ID,
            )
        resolver.assert_called_once_with(
            headers,
            collaboration_id=COLLABORATION_ID,
            required_action="read",
        )
        return result, store

    def test_authorized_owner_receives_exact_projection_with_shared_and_internal_messages(self):
        result, store = self._read_with_store(_thread_record())

        self.assertEqual(
            result,
            {
                "status": "ok",
                "collaboration": {
                    "collaborationId": COLLABORATION_ID,
                    "mailboxId": MAILBOX_ID,
                    "state": "needs_review",
                    "createdAt": (NOW * 1000) - 1000,
                    "updatedAt": NOW * 1000,
                    "source": {
                        "subject": "Quarterly launch review",
                        "senderDisplay": "Alex Sender",
                        "fromDisplay": "Alex Sender <alex@example.net>",
                        "timestamp": "1712345678901",
                        "bodyText": "Please review the launch details.",
                    },
                    "messages": [
                        {
                            "id": "S" * 22,
                            "authorDisplayName": "Owner Person",
                            "authorRole": "Cuevion user",
                            "text": "Shared owner reply",
                            "visibility": "shared",
                            "timestamp": (NOW * 1000) - 300,
                        },
                        {
                            "id": "N" * 22,
                            "authorDisplayName": "Internal Teammate",
                            "authorRole": "Cuevion user",
                            "text": "Internal-only note",
                            "visibility": "internal",
                            "timestamp": (NOW * 1000) - 200,
                        },
                        {
                            "id": "G" * 22,
                            "authorDisplayName": "Guest Reviewer",
                            "authorRole": "Guest reviewer",
                            "text": "Shared guest reply",
                            "visibility": "shared",
                            "timestamp": (NOW * 1000) - 100,
                        },
                    ],
                },
                "error": None,
            },
        )
        self.assertIsInstance(result["collaboration"]["source"]["timestamp"], str)
        self.assertEqual(
            set(result["collaboration"]),
            {
                "collaborationId",
                "mailboxId",
                "state",
                "createdAt",
                "updatedAt",
                "source",
                "messages",
            },
        )
        self.assertEqual(
            set(result["collaboration"]["source"]),
            {"subject", "senderDisplay", "fromDisplay", "timestamp", "bodyText"},
        )
        self.assertTrue(
            all(
                set(message)
                == {
                    "id",
                    "authorDisplayName",
                    "authorRole",
                    "text",
                    "visibility",
                    "timestamp",
                }
                for message in result["collaboration"]["messages"]
            )
        )

        forbidden = {
            "v",
            "ownerEmail",
            "workspaceId",
            "sourceRef",
            "provider",
            "providerMessageId",
            "folder",
            "uidValidity",
            "imapUid",
            "participants",
            "invitations",
            "inviteToken",
            "sessionHash",
            "csrfTokenHash",
            "attachments",
            "html",
            "rawHeaders",
            "credentials",
            "oauth",
        }
        self.assertTrue(forbidden.isdisjoint(_all_keys(result)))
        self.assertNotIn(PRIVATE_SOURCE_MARKER, _public_text(result))
        self.assertFalse(
            any(type(value) is redis_store._V2RecordResult for value in _walk(result))
        )
        self.assertEqual(
            store.commands,
            [["GET", redis_store.build_v2_thread_key(COLLABORATION_ID)]],
        )

    def test_public_owner_path_authenticates_mints_and_revalidates_before_projection(self):
        store = StatefulV2Store()
        exact_thread_key = redis_store.build_v2_thread_key(COLLABORATION_ID)
        store.put_json(exact_thread_key, _thread_record())
        events: list[tuple[str, object]] = []
        headers = [("Authorization", "private-request-marker")]

        def shared_config_helper(name: str):
            if name == "resolve_authenticated_user":
                def resolve_authenticated_user(received_headers: object):
                    self.assertIs(received_headers, headers)
                    events.append(("authentication", None))
                    return {"email": OWNER_EMAIL, "name": "Owner Person"}, None

                return resolve_authenticated_user
            if name == "resolve_owned_managed_inbox_record":
                def resolve_owned_mailbox(
                    received_headers: object,
                    mailbox_id: str,
                ) -> dict:
                    self.assertIs(received_headers, headers)
                    events.append(("mailbox_authorization", mailbox_id))
                    return {
                        "status": "ok",
                        "user": {"email": OWNER_EMAIL, "name": "Owner Person"},
                        "inbox": {"id": mailbox_id, "provider": "google"},
                    }

                return resolve_owned_mailbox
            self.fail(f"unexpected shared configuration helper: {name}")

        with patch.object(
            authorization,
            "_shared_config_helper",
            side_effect=shared_config_helper,
        ), _stateful_backend(store, events):
            result = application.read_v2_collaboration_for_owner(
                headers,
                COLLABORATION_ID,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["error"], None)
        self.assertEqual(
            set(result["collaboration"]),
            {
                "collaborationId",
                "mailboxId",
                "state",
                "createdAt",
                "updatedAt",
                "source",
                "messages",
            },
        )
        self.assertEqual(result["collaboration"]["collaborationId"], COLLABORATION_ID)
        self.assertEqual(result["collaboration"]["mailboxId"], MAILBOX_ID)
        self.assertEqual(
            set(result["collaboration"]["source"]),
            {"subject", "senderDisplay", "fromDisplay", "timestamp", "bodyText"},
        )
        self.assertTrue(
            all(
                set(message)
                == {
                    "id",
                    "authorDisplayName",
                    "authorRole",
                    "text",
                    "visibility",
                    "timestamp",
                }
                for message in result["collaboration"]["messages"]
            )
        )
        self.assertEqual(
            [message["visibility"] for message in result["collaboration"]["messages"]],
            ["shared", "internal", "shared"],
        )
        self.assertNotIn(PRIVATE_SOURCE_MARKER, _public_text(result))
        self.assertEqual(
            events,
            [
                ("authentication", None),
                ("storage", ("GET", exact_thread_key)),
                ("mailbox_authorization", MAILBOX_ID),
                ("storage", ("GET", exact_thread_key)),
            ],
        )
        self.assertEqual(
            store.commands,
            [["GET", exact_thread_key], ["GET", exact_thread_key]],
        )
        self.assertTrue(
            all(
                command[0] == "GET"
                and command[1] == exact_thread_key
                and not any(character in command[1] for character in "*?[]")
                and "collab:v2" in command[1]
                for command in store.commands
            )
        )

    def test_owner_scope_mismatches_and_malformed_records_fail_closed(self):
        capability = _owner_capability()
        authorization_result = {
            "status": "ok",
            "context": capability,
            "error": None,
        }
        cases: list[tuple[str, dict, str]] = []

        cross_owner = _thread_record()
        cross_owner["ownerEmail"] = OTHER_OWNER_EMAIL
        cross_owner["workspaceId"] = OTHER_OWNER_EMAIL
        cases.append(("cross-owner", cross_owner, "forbidden"))

        cross_workspace = _thread_record()
        cross_workspace["workspaceId"] = OTHER_OWNER_EMAIL
        cases.append(("cross-workspace", cross_workspace, "storage_protocol_error"))

        cross_mailbox = _thread_record()
        cross_mailbox["mailboxId"] = "mailbox-2"
        cases.append(("cross-mailbox", cross_mailbox, "forbidden"))

        wrong_collaboration = _thread_record()
        wrong_collaboration["collaborationId"] = OTHER_COLLABORATION_ID
        cases.append(("wrong-collaboration", wrong_collaboration, "forbidden"))

        malformed = _thread_record()
        malformed.pop("sourceMessage")
        cases.append(("malformed", malformed, "storage_protocol_error"))

        for label, record, expected_code in cases:
            with self.subTest(label=label), patch.object(
                application,
                "resolve_internal_collaboration_context",
                return_value=authorization_result,
            ), patch.object(
                application,
                "_load_v2_thread",
                return_value=redis_store._V2RecordResult(record),
            ):
                result = application.read_v2_collaboration_for_owner(
                    [], COLLABORATION_ID
                )
            self.assertNotEqual(result["status"], "ok")
            self.assertIsNone(result["collaboration"])
            self.assertEqual(result["error"], {"code": expected_code})

    def test_forged_mapping_and_dataclass_cannot_replace_internal_capability(self):
        @dataclass(frozen=True)
        class ForgedCapability:
            collaboration_id: str = COLLABORATION_ID
            owner_email: str = OWNER_EMAIL
            workspace_id: str = OWNER_EMAIL
            mailbox_id: str = MAILBOX_ID
            action: str = "read"

        forged_values = [
            {
                "collaboration_id": COLLABORATION_ID,
                "owner_email": OWNER_EMAIL,
                "workspace_id": OWNER_EMAIL,
                "mailbox_id": MAILBOX_ID,
                "action": "read",
            },
            ForgedCapability(),
        ]
        for forged in forged_values:
            with self.subTest(kind=type(forged).__name__), patch.object(
                application,
                "resolve_internal_collaboration_context",
                return_value={"status": "ok", "context": forged, "error": None},
            ), patch.object(application, "_load_v2_thread") as loader:
                result = application.read_v2_collaboration_for_owner(
                    [], COLLABORATION_ID
                )
            self.assertEqual(result["error"], {"code": "forbidden"})
            self.assertIsNone(result["collaboration"])
            loader.assert_not_called()

    def test_owner_authorization_failure_is_preserved_and_never_becomes_empty_success(self):
        with patch.object(
            application,
            "resolve_internal_collaboration_context",
            return_value={
                "status": "unauthorized",
                "context": None,
                "error": {"code": "auth_required"},
            },
        ), patch.object(application, "_load_v2_thread") as loader:
            first = application.read_v2_collaboration_for_owner([], COLLABORATION_ID)
            second = application.read_v2_collaboration_for_owner([], COLLABORATION_ID)

        self.assertEqual(first, second)
        self.assertEqual(
            first,
            {
                "status": "unauthorized",
                "collaboration": None,
                "error": {"code": "auth_required"},
            },
        )
        loader.assert_not_called()


class GuestReadApplicationTests(unittest.TestCase):
    def test_valid_guest_reads_one_bound_collaboration_and_exact_shared_projection(self):
        store = _guest_store()
        result = _read_guest(store)

        self.assertEqual(
            result,
            {
                "status": "ok",
                "collaboration": {
                    "collaborationId": COLLABORATION_ID,
                    "state": "needs_review",
                    "updatedAt": NOW * 1000,
                    "allowedActions": ["read", "reply"],
                    "sharedSource": {
                        "subject": "Quarterly launch review",
                        "senderDisplay": "Alex Sender",
                        "fromDisplay": "Alex Sender <alex@example.net>",
                        "timestamp": "1712345678901",
                        "bodyText": "Please review the launch details.",
                    },
                    "messages": [
                        {
                            "id": "S" * 22,
                            "authorDisplayName": "Owner Person",
                            "authorRole": "Cuevion user",
                            "text": "Shared owner reply",
                            "timestamp": (NOW * 1000) - 300,
                        },
                        {
                            "id": "G" * 22,
                            "authorDisplayName": "Guest Reviewer",
                            "authorRole": "Guest reviewer",
                            "text": "Shared guest reply",
                            "timestamp": (NOW * 1000) - 100,
                        },
                    ],
                },
                "error": None,
            },
        )
        self.assertNotIn("Internal-only note", _public_text(result))
        self.assertIsInstance(
            result["collaboration"]["sharedSource"]["timestamp"], str
        )
        forbidden = {
            "ownerEmail",
            "workspaceId",
            "mailboxId",
            "sourceRef",
            "provider",
            "providerMessageId",
            "visibility",
            "participants",
            "invitations",
            "inviteToken",
            "sessionHash",
            "csrfTokenHash",
            "attachments",
            "html",
            "rawHeaders",
        }
        self.assertTrue(forbidden.isdisjoint(_all_keys(result)))
        public_text = _public_text(result)
        for private_value in (
            OWNER_EMAIL,
            RAW_SESSION_ID,
            CSRF_HASH,
            PRIVATE_SOURCE_MARKER,
        ):
            self.assertNotIn(private_value, public_text)
        self.assertFalse(
            any(type(value) is redis_store._V2RecordResult for value in _walk(result))
        )

        expected_keys = {
            redis_store.build_v2_guest_session_key(
                models.hash_v2_secret(RAW_SESSION_ID)
            ),
            redis_store.build_v2_invite_key(INVITE_ID),
            redis_store.build_v2_thread_key(COLLABORATION_ID),
        }
        self.assertEqual(len(store.commands), 3)
        self.assertEqual({command[1] for command in store.commands}, expected_keys)
        self.assertTrue(all(command[0] == "GET" for command in store.commands))
        self.assertEqual(
            sum(
                command[1] == redis_store.build_v2_thread_key(COLLABORATION_ID)
                for command in store.commands
            ),
            1,
        )
        self.assertFalse(
            any(command[0] in {"SCAN", "KEYS"} for command in store.commands)
        )

    def test_guest_cannot_choose_a_collaboration_id(self):
        guest_signature = inspect.signature(
            application.read_v2_collaboration_for_guest
        )
        self.assertEqual(
            list(guest_signature.parameters),
            ["raw_headers"],
        )
        self.assertNotIn("collaboration_id", guest_signature.parameters)
        self.assertEqual(
            list(
                inspect.signature(
                    application.read_v2_collaboration_for_owner
                ).parameters
            ),
            ["headers", "collaboration_id"],
        )
        self.assertEqual(
            application.__all__,
            [
                "read_v2_collaboration_for_guest",
                "read_v2_collaboration_for_owner",
            ],
        )
        for prohibited in (
            "create_v2_collaboration",
            "list_v2_collaborations",
            "append_v2_message",
            "resolve_v2_collaboration",
            "reopen_v2_collaboration",
        ):
            self.assertFalse(hasattr(application, prohibited))

    def test_revoked_logged_out_and_expired_sessions_fail_before_thread_load(self):
        revoked = _session_record()
        revoked.update(status="revoked", revokedAt=NOW - 5)

        logged_out = _session_record()
        logged_out.update(status="logged_out", loggedOutAt=NOW - 5)

        expired = _session_record()
        expired.update(status="expired", expiresAt=NOW - 1)

        for label, session, expected_status, expected_code in (
            ("revoked", revoked, "error", "session_revoked"),
            ("logged-out", logged_out, "error", "session_revoked"),
            ("expired", expired, "error", "session_expired"),
        ):
            store = _guest_store(session=session)
            with self.subTest(label=label):
                result = _read_guest(store)
            self.assertEqual(result["status"], expected_status)
            self.assertEqual(result["error"], {"code": expected_code})
            self.assertIsNone(result["collaboration"])
            self.assertEqual(len(store.commands), 1)
            self.assertEqual(
                store.commands[0],
                [
                    "GET",
                    redis_store.build_v2_guest_session_key(
                        models.hash_v2_secret(RAW_SESSION_ID)
                    ),
                ],
            )

    def test_invitation_thread_scope_mismatch_and_cross_thread_attempts_fail(self):
        mismatched_thread = _thread_record()
        mismatched_thread["ownerEmail"] = OTHER_OWNER_EMAIL
        mismatched_thread["workspaceId"] = OTHER_OWNER_EMAIL
        store = _guest_store(thread=mismatched_thread)
        result = _read_guest(store)
        self.assertEqual(result["error"], {"code": "forbidden"})
        self.assertIsNone(result["collaboration"])

        wrong_thread = _thread_record()
        wrong_thread["collaborationId"] = OTHER_COLLABORATION_ID
        store = _guest_store()
        with _stateful_backend(store), patch.object(
            application.time,
            "time",
            return_value=NOW,
        ), patch.object(
            application,
            "_load_v2_thread",
            return_value=redis_store._V2RecordResult(wrong_thread),
        ):
            result = application.read_v2_collaboration_for_guest(_guest_headers())
        self.assertEqual(result["error"], {"code": "forbidden"})
        self.assertIsNone(result["collaboration"])

    def test_session_invitation_graph_mismatch_fails_before_thread_load(self):
        invite = _invite_record()
        invite["collaborationId"] = OTHER_COLLABORATION_ID
        store = _guest_store(invite=invite)
        result = _read_guest(store)
        self.assertEqual(result["error"], {"code": "session_revoked"})
        self.assertIsNone(result["collaboration"])
        self.assertEqual(len(store.commands), 2)

    def test_forged_mapping_and_dataclass_cannot_replace_guest_read_capability(self):
        @dataclass(frozen=True)
        class ForgedGuestCapability:
            session_hash: str = "d" * 64
            invite_id: str = INVITE_ID
            owner_email: str = OWNER_EMAIL
            workspace_id: str = OWNER_EMAIL
            mailbox_id: str = MAILBOX_ID
            collaboration_id: str = COLLABORATION_ID
            guest_display_name: str = "Guest Reviewer"
            expires_at: int = NOW + 1800

        forged_values = [
            {
                "session_hash": "d" * 64,
                "invite_id": INVITE_ID,
                "owner_email": OWNER_EMAIL,
                "workspace_id": OWNER_EMAIL,
                "mailbox_id": MAILBOX_ID,
                "collaboration_id": COLLABORATION_ID,
            },
            ForgedGuestCapability(),
        ]
        for forged in forged_values:
            with self.subTest(kind=type(forged).__name__), patch.object(
                application.time,
                "time",
                return_value=NOW,
            ), patch.object(
                application,
                "read_guest_session_cookie",
                return_value=RAW_SESSION_ID,
            ), patch.object(
                application,
                "_resolve_guest_read_access",
                return_value=(forged, _session_record(), None),
            ), patch.object(application, "_load_v2_thread") as loader:
                result = application.read_v2_collaboration_for_guest([])
            self.assertEqual(result["error"], {"code": "session_revoked"})
            self.assertIsNone(result["collaboration"])
            loader.assert_not_called()


class ApplicationErrorSafetyTests(unittest.TestCase):
    def test_owner_final_thread_load_preserves_only_exact_documented_storage_errors(self):
        capability = _owner_capability()
        authorization_result = {
            "status": "ok",
            "context": capability,
            "error": None,
        }

        cases = (
            (
                "protocol",
                {
                    "status": "unavailable",
                    "error": {"code": "storage_protocol_error"},
                },
                "storage_protocol_error",
            ),
            (
                "outage",
                {
                    "status": "unavailable",
                    "error": {"code": "storage_unavailable"},
                },
                "storage_unavailable",
            ),
            (
                "unknown-code",
                {
                    "status": "unavailable",
                    "error": {"code": "private-" + PRIVATE_EXCEPTION_MARKER},
                },
                "storage_protocol_error",
            ),
            (
                "malformed-envelope",
                {
                    "status": "unavailable",
                    "error": {
                        "code": "storage_unavailable",
                        "details": PRIVATE_EXCEPTION_MARKER,
                    },
                },
                "storage_protocol_error",
            ),
        )
        for label, loaded, expected_code in cases:
            with self.subTest(label=label), patch.object(
                application,
                "resolve_internal_collaboration_context",
                return_value=authorization_result,
            ), patch.object(
                application,
                "_load_v2_thread",
                return_value=loaded,
            ):
                result = application.read_v2_collaboration_for_owner(
                    [("Authorization", PRIVATE_EXCEPTION_MARKER)],
                    COLLABORATION_ID,
                )

            self.assertEqual(
                result,
                {
                    "status": "unavailable",
                    "collaboration": None,
                    "error": {"code": expected_code},
                },
            )
            self.assertNotIn(PRIVATE_EXCEPTION_MARKER, _public_text(result))
            self.assertFalse(
                any(isinstance(value, BaseException) for value in _walk(result))
            )

    def test_guest_final_thread_load_preserves_only_exact_documented_storage_errors(self):
        capability, session = _guest_read_capability_and_session()
        resolved = (capability, session, None)
        cases = (
            (
                "protocol",
                {
                    "status": "unavailable",
                    "error": {"code": "storage_protocol_error"},
                },
                "storage_protocol_error",
            ),
            (
                "outage",
                {
                    "status": "unavailable",
                    "error": {"code": "storage_unavailable"},
                },
                "storage_unavailable",
            ),
            (
                "unknown-code",
                {
                    "status": "unavailable",
                    "error": {"code": "private-" + PRIVATE_EXCEPTION_MARKER},
                },
                "storage_protocol_error",
            ),
            (
                "malformed-envelope",
                {
                    "status": "unavailable",
                    "error": {
                        "code": "storage_protocol_error",
                        "response": PRIVATE_EXCEPTION_MARKER,
                    },
                },
                "storage_protocol_error",
            ),
        )
        for label, loaded, expected_code in cases:
            with self.subTest(label=label), patch.object(
                application.time,
                "time",
                return_value=NOW,
            ), patch.object(
                application,
                "read_guest_session_cookie",
                return_value=RAW_SESSION_ID,
            ), patch.object(
                application,
                "_resolve_guest_read_access",
                return_value=resolved,
            ), patch.object(
                application,
                "_load_v2_thread",
                return_value=loaded,
            ):
                result = application.read_v2_collaboration_for_guest(_guest_headers())

            self.assertEqual(
                result,
                {
                    "status": "unavailable",
                    "collaboration": None,
                    "error": {"code": expected_code},
                },
            )
            public_text = _public_text(result)
            self.assertNotIn(RAW_SESSION_ID, public_text)
            self.assertNotIn(PRIVATE_EXCEPTION_MARKER, public_text)
            self.assertFalse(
                any(isinstance(value, BaseException) for value in _walk(result))
            )

    def test_unexpected_owner_helper_exceptions_are_not_rewritten_as_outages(self):
        with patch.object(
            application,
            "resolve_internal_collaboration_context",
            side_effect=AssertionError,
        ):
            with self.assertRaises(AssertionError):
                application.read_v2_collaboration_for_owner([], COLLABORATION_ID)

        authorization_result = {
            "status": "ok",
            "context": _owner_capability(),
            "error": None,
        }
        with patch.object(
            application,
            "resolve_internal_collaboration_context",
            return_value=authorization_result,
        ), patch.object(
            application,
            "_load_v2_thread",
            side_effect=AssertionError,
        ):
            with self.assertRaises(AssertionError):
                application.read_v2_collaboration_for_owner([], COLLABORATION_ID)

    def test_unexpected_guest_helper_exceptions_are_not_rewritten_as_outages(self):
        with patch.object(
            application.time,
            "time",
            return_value=NOW,
        ), patch.object(
            application,
            "read_guest_session_cookie",
            side_effect=TypeError,
        ):
            with self.assertRaises(TypeError):
                application.read_v2_collaboration_for_guest([])

        with patch.object(
            application.time,
            "time",
            return_value=NOW,
        ), patch.object(
            application,
            "read_guest_session_cookie",
            return_value=RAW_SESSION_ID,
        ), patch.object(
            application,
            "_resolve_guest_read_access",
            side_effect=AssertionError,
        ):
            with self.assertRaises(AssertionError):
                application.read_v2_collaboration_for_guest([])

        capability, session = _guest_read_capability_and_session()
        with patch.object(
            application.time,
            "time",
            return_value=NOW,
        ), patch.object(
            application,
            "read_guest_session_cookie",
            return_value=RAW_SESSION_ID,
        ), patch.object(
            application,
            "_resolve_guest_read_access",
            return_value=(capability, session, None),
        ), patch.object(
            application,
            "_load_v2_thread",
            side_effect=AssertionError,
        ):
            with self.assertRaises(AssertionError):
                application.read_v2_collaboration_for_guest([])

    def test_unknown_foundation_errors_are_not_reflected(self):
        private_code = "private-storage-code-" + PRIVATE_EXCEPTION_MARKER
        with patch.object(
            application,
            "resolve_internal_collaboration_context",
            return_value={
                "status": "private-status",
                "context": None,
                "error": {"code": private_code, "details": OWNER_EMAIL},
            },
        ):
            result = application.read_v2_collaboration_for_owner(
                [("Authorization", PRIVATE_EXCEPTION_MARKER)],
                COLLABORATION_ID,
            )

        self.assertEqual(
            result,
            {
                "status": "error",
                "collaboration": None,
                "error": {"code": "storage_protocol_error"},
            },
        )
        text = _public_text(result)
        self.assertNotIn(private_code, text)
        self.assertNotIn(OWNER_EMAIL, text)
        self.assertNotIn(PRIVATE_EXCEPTION_MARKER, text)


if __name__ == "__main__":
    unittest.main()
