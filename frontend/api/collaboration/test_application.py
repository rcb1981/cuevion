from __future__ import annotations

import base64
import inspect
import json
import sys
import unittest
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

from api.collaboration import (
    application,
    authorization,
    guest_session,
    models,
    mutations,
    redis_store,
    source_message,
)
from api.collaboration.v2_stateful_test_store import StatefulV2Store


COLLABORATION_ID = "A" * 22
OTHER_COLLABORATION_ID = "B" * 22
INVITE_ID = "I" * 22
MAILBOX_ID = "mailbox-1"
OWNER_EMAIL = "owner@example.com"
OTHER_OWNER_EMAIL = "other@example.com"
GUEST_WORKSPACE_ID = "wsp_" + "G" * 22
OTHER_GUEST_WORKSPACE_ID = "wsp_" + "H" * 22
NOW = 1_800_000_000
NOW_MILLISECONDS = NOW * 1000
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
        "workspaceId": GUEST_WORKSPACE_ID,
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
        "workspaceId": GUEST_WORKSPACE_ID,
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


def _owner_capability(thread: dict | None = None, *, action: str = "read"):
    record = _thread_record() if thread is None else thread
    result = authorization.resolve_internal_collaboration_context(
        [("Authorization", "private-request-marker")],
        collaboration_id=COLLABORATION_ID,
        required_action=action,
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
        result["context"], actions={action}
    )
    return result["context"]


def _create_capability(
    provider: str = "google",
    *,
    mailbox_id: str = MAILBOX_ID,
    owner_email: str = OWNER_EMAIL,
):
    result = authorization.resolve_internal_collaboration_context(
        [("Authorization", "private-request-marker")],
        mailbox_id,
        required_action="create",
        user_resolver=lambda _headers: (
            {"email": owner_email, "name": "Owner Person"},
            None,
        ),
        mailbox_resolver=lambda _headers, received_mailbox_id: {
            "status": "ok",
            "user": {"email": owner_email, "name": "Owner Person"},
            "inbox": {"id": received_mailbox_id, "provider": provider},
        },
    )
    assert result["status"] == "ok"
    assert authorization._is_internal_capability(
        result["context"], actions={"create"}
    )
    return result["context"]


def _mailbox_read_capability(provider: str = "google"):
    result = authorization.resolve_internal_collaboration_context(
        [("Authorization", "private-request-marker")],
        MAILBOX_ID,
        required_action="read",
        user_resolver=lambda _headers: (
            {"email": OWNER_EMAIL, "name": "Owner Person"},
            None,
        ),
        mailbox_resolver=lambda _headers, mailbox_id: {
            "status": "ok",
            "user": {"email": OWNER_EMAIL, "name": "Owner Person"},
            "inbox": {"id": mailbox_id, "provider": provider},
        },
    )
    assert result["status"] == "ok"
    assert authorization._is_internal_capability(
        result["context"], actions={"read"}
    )
    return result["context"]


def _create_payload(provider: str = "google", *, state: str = "needs_review") -> dict:
    source_ref = (
        {"providerMessageId": PRIVATE_SOURCE_MARKER}
        if provider == "google"
        else {"folder": "INBOX", "uidValidity": "123", "imapUid": "456"}
    )
    return {
        "mailboxId": MAILBOX_ID,
        "sourceRef": source_ref,
        "state": state,
    }


def _raw_source_message() -> bytes:
    return (
        b"From: Alex Sender <alex@example.net>\r\n"
        b"Subject: Quarterly launch review\r\n"
        b"Date: Tue, 02 Jan 2024 10:30:00 +0000\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Please review the launch details."
    )


def _source_resolver_with_fake_providers(
    events: list[tuple[str, object]] | None = None,
):
    def resolve(
        headers: object,
        payload: object,
        *,
        authorization_resolver,
    ) -> dict:
        def google_fetcher(
            received_headers: object,
            mailbox_id: str,
            source_ref: dict,
        ) -> dict:
            if events is not None:
                events.append(("provider_fetch", "google"))
            return {"status": "ok", "rawMessage": _raw_source_message()}

        def imap_fetcher(
            received_headers: object,
            mailbox_id: str,
            source_ref: dict,
        ) -> dict:
            if events is not None:
                events.append(("provider_fetch", "custom_imap"))
            return {
                "status": "ok",
                "rawMessage": _raw_source_message(),
                "uidValidity": source_ref["uidValidity"],
            }

        return source_message.resolve_source_message(
            headers,
            payload,
            authorization_resolver=authorization_resolver,
            google_fetcher=google_fetcher,
            imap_fetcher=imap_fetcher,
        )

    return resolve


def _guest_store(
    *,
    thread: dict | None = None,
    session: dict | None = None,
    invite: dict | None = None,
) -> StatefulV2Store:
    store = StatefulV2Store()
    thread_record = (
        {
            **_thread_record(),
            "workspaceId": GUEST_WORKSPACE_ID,
            "ownerUserId": "usr_" + "A" * 22,
            "ownerDisplayName": "Owner Person",
            "participants": [
                {
                    "userId": "usr_" + "B" * 21 + "A",
                    "membershipRef": "tinv_guest_read_fixture",
                    "displayName": "Internal Teammate",
                }
            ],
        }
        if thread is None
        else thread
    )
    session_record = (
        {**_session_record(), "workspaceId": GUEST_WORKSPACE_ID}
        if session is None
        else session
    )
    invite_record = (
        {**_invite_record(), "workspaceId": GUEST_WORKSPACE_ID}
        if invite is None
        else invite
    )
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


class OwnerCreateApplicationTests(unittest.TestCase):
    def _assert_exact_payload_rejected_without_side_effects(
        self,
        payload: object,
    ) -> None:
        with patch.object(
            application,
            "resolve_internal_collaboration_context",
        ) as authorize, patch.object(
            authorization,
            "_shared_config_helper",
        ) as shared_config_helper, patch.object(
            application,
            "resolve_source_message",
        ) as resolve_source, patch.object(
            application,
            "_create_v2_thread",
        ) as creator, patch.object(
            application,
            "_load_v2_thread",
        ) as loader:
            result = application.create_v2_collaboration_for_owner([], payload)

        self.assertEqual(
            result,
            {
                "status": "malformed",
                "collaboration": None,
                "error": {"code": "invalid_request"},
            },
        )
        authorize.assert_not_called()
        shared_config_helper.assert_not_called()
        resolve_source.assert_not_called()
        creator.assert_not_called()
        loader.assert_not_called()

    def test_public_create_uses_real_authorization_source_and_exact_storage_paths(self):
        headers = [("Authorization", "private-request-marker")]
        payload = _create_payload("google")
        stored_thread = _thread_record()
        store = StatefulV2Store()
        events: list[tuple[str, object]] = []

        with patch.object(
            redis_store,
            "resolve_v2_index_hmac_keys",
            return_value=(b"k" * 32, None),
        ):
            prepared = redis_store._create_v2_thread(
                stored_thread,
                command_transport=store,
            )
        self.assertIs(type(prepared), redis_store._V2RecordResult)
        self.assertTrue(prepared.created)
        store.commands.clear()

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

        def resolve_authenticated_gmail(
            received_headers: object,
            mailbox_id: str,
        ) -> dict:
            self.assertIs(received_headers, headers)
            self.assertEqual(mailbox_id, MAILBOX_ID)
            events.append(("provider_authorization", "google"))
            return {
                "status": "ok",
                "context": {
                    "owner_email": OWNER_EMAIL,
                    "mailbox_id": MAILBOX_ID,
                    "refresh_attempted": False,
                },
            }

        encoded = base64.urlsafe_b64encode(_raw_source_message()).decode(
            "ascii"
        ).rstrip("=")

        def request_with_one_refresh(context: dict, path: str):
            self.assertEqual(context["owner_email"], OWNER_EMAIL)
            self.assertEqual(
                path,
                f"/messages/{PRIVATE_SOURCE_MARKER}?format=raw",
            )
            events.append(("provider_fetch", "google"))
            return {"raw": encoded}, None, context, None

        authenticated_gmail = SimpleNamespace(
            __name__="api.inboxes.authenticated_gmail",
            resolve_authenticated_gmail=resolve_authenticated_gmail,
        )
        fetch_module = SimpleNamespace(
            _request_with_one_refresh=request_with_one_refresh,
        )

        genuine_capability_check = authorization._is_internal_capability

        def check_source_capability(value: object, *, actions=None) -> bool:
            self.assertTrue(
                genuine_capability_check(value, actions={"create"})
            )
            self.assertEqual(value.mailbox_provider, "google")
            events.append(("source_capability", value.mailbox_provider))
            return genuine_capability_check(value, actions=actions)

        def atomic_create(record: dict) -> object:
            events.append(("atomic_create", record["collaborationId"]))
            return redis_store._create_v2_thread(record)

        def exact_reload(collaboration_id: str) -> object:
            events.append(("exact_reload", collaboration_id))
            return redis_store._load_v2_thread(collaboration_id)

        with patch.dict(
            sys.modules,
            {
                "api.inboxes.authenticated_gmail": authenticated_gmail,
                "authenticated_gmail": authenticated_gmail,
            },
        ), patch.object(
            authorization,
            "_shared_config_helper",
            side_effect=shared_config_helper,
        ), patch.object(
            source_message,
            "_load_fetch_gmail_module",
            return_value=fetch_module,
        ), patch.object(
            source_message,
            "_is_internal_capability",
            side_effect=check_source_capability,
        ) as source_capability_check, patch.object(
            redis_store,
            "resolve_v2_index_hmac_keys",
            return_value=(b"k" * 32, None),
        ), _stateful_backend(store, events), patch.object(
            application,
            "generate_v2_opaque_id",
            return_value=OTHER_COLLABORATION_ID,
        ), patch.object(
            application.time,
            "time_ns",
            return_value=NOW_MILLISECONDS * 1_000_000,
        ), patch.object(
            application,
            "_create_v2_thread",
            side_effect=atomic_create,
        ) as creator, patch.object(
            application,
            "_load_v2_thread",
            side_effect=exact_reload,
        ) as loader:
            result = application.create_v2_collaboration_for_owner(
                headers,
                payload,
            )

        self.assertEqual(
            [event for event in events if event[0] != "storage"],
            [
                ("authentication", None),
                ("mailbox_authorization", MAILBOX_ID),
                ("source_capability", "google"),
                ("provider_authorization", "google"),
                ("provider_fetch", "google"),
                ("atomic_create", OTHER_COLLABORATION_ID),
                ("exact_reload", COLLABORATION_ID),
            ],
        )
        first_storage_event = next(
            index for index, event in enumerate(events) if event[0] == "storage"
        )
        self.assertGreater(
            first_storage_event,
            events.index(("atomic_create", OTHER_COLLABORATION_ID)),
        )
        source_capability_check.assert_called_once()
        checked_capability = source_capability_check.call_args.args[0]
        self.assertTrue(
            authorization._is_internal_capability(
                checked_capability,
                actions={"create"},
            )
        )
        self.assertIsNone(checked_capability.collaboration_id)
        creator.assert_called_once()
        loader.assert_called_once_with(COLLABORATION_ID)

        self.assertEqual(
            result,
            {
                "created": False,
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
            },
        )
        self.assertNotIn(PRIVATE_SOURCE_MARKER, _public_text(result))
        self.assertEqual(
            [command[0] for command in store.commands],
            ["EVAL", "EVAL", "GET", "GET"],
        )
        for command in store.commands:
            self.assertIn(command[0], {"EVAL", "GET"})
            if command[0] == "GET":
                keys = command[1:2]
            else:
                key_count = command[2]
                keys = command[3 : 3 + key_count]
            self.assertTrue(keys)
            self.assertTrue(
                all(
                    "collab:v2" in key
                    and "collab:v1" not in key
                    and not any(character in key for character in "*?[]")
                    for key in keys
                )
            )

    def test_authorized_google_and_imap_creation_use_owner_boundary_and_exact_dto(self):
        for provider in ("google", "custom_imap"):
            for state in ("needs_review", "needs_action", "note_only"):
                with self.subTest(provider=provider, state=state):
                    headers = [("Authorization", "private-request-marker")]
                    payload = _create_payload(provider, state=state)
                    capability = _create_capability(provider)
                    authorization_result = {
                        "status": "ok",
                        "context": capability,
                        "error": None,
                    }
                    events: list[tuple[str, object]] = []
                    stored: list[dict] = []

                    def authorize(
                        received_headers: object,
                        mailbox_id: object,
                        *,
                        required_action: str,
                    ) -> dict:
                        self.assertIs(received_headers, headers)
                        self.assertEqual(mailbox_id, MAILBOX_ID)
                        self.assertEqual(required_action, "create")
                        events.append(("owner_mailbox_authorization", provider))
                        return authorization_result

                    def create(record: dict) -> object:
                        events.append(("atomic_create", provider))
                        stored.append(record)
                        return redis_store._V2RecordResult(record, created=True)

                    with patch.object(
                        application,
                        "resolve_internal_collaboration_context",
                        side_effect=authorize,
                    ) as authorize_mock, patch.object(
                        application,
                        "resolve_source_message",
                        side_effect=_source_resolver_with_fake_providers(events),
                    ), patch.object(
                        application,
                        "generate_v2_opaque_id",
                        return_value=COLLABORATION_ID,
                    ) as id_generator, patch.object(
                        application.time,
                        "time_ns",
                        return_value=NOW_MILLISECONDS * 1_000_000,
                    ), patch.object(
                        application,
                        "_create_v2_thread",
                        side_effect=create,
                    ) as creator:
                        result = application.create_v2_collaboration_for_owner(
                            headers,
                            payload,
                        )

                    self.assertEqual(
                        events,
                        [
                            ("owner_mailbox_authorization", provider),
                            ("provider_fetch", provider),
                            ("atomic_create", provider),
                        ],
                    )
                    authorize_mock.assert_called_once_with(
                        headers,
                        MAILBOX_ID,
                        required_action="create",
                    )
                    id_generator.assert_called_once_with()
                    creator.assert_called_once_with(stored[0])
                    self.assertEqual(
                        stored[0],
                        {
                            "v": 2,
                            "collaborationId": COLLABORATION_ID,
                            "ownerEmail": OWNER_EMAIL,
                            "workspaceId": OWNER_EMAIL,
                            "mailboxId": MAILBOX_ID,
                            "sourceRef": {
                                "provider": provider,
                                **payload["sourceRef"],
                            },
                            "sourceMessage": {
                                "subject": "Quarterly launch review",
                                "senderDisplay": "Alex Sender",
                                "fromDisplay": "Alex Sender <alex@example.net>",
                                "timestamp": "Tue, 02 Jan 2024 10:30:00 +0000",
                                "bodyText": "Please review the launch details.",
                            },
                            "state": state,
                            "messages": [],
                            "createdAt": NOW_MILLISECONDS,
                            "updatedAt": NOW_MILLISECONDS,
                        },
                    )
                    self.assertEqual(
                        result,
                        {
                            "created": True,
                            "collaboration": {
                                "collaborationId": COLLABORATION_ID,
                                "mailboxId": MAILBOX_ID,
                                "state": state,
                                "createdAt": NOW_MILLISECONDS,
                                "updatedAt": NOW_MILLISECONDS,
                                "source": {
                                    "subject": "Quarterly launch review",
                                    "senderDisplay": "Alex Sender",
                                    "fromDisplay": "Alex Sender <alex@example.net>",
                                    "timestamp": "Tue, 02 Jan 2024 10:30:00 +0000",
                                    "bodyText": "Please review the launch details.",
                                },
                                "messages": [],
                            },
                        },
                    )
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
                    self.assertNotIn(PRIVATE_SOURCE_MARKER, _public_text(result))
                    self.assertFalse(
                        any(
                            type(value) is redis_store._V2RecordResult
                            for value in _walk(result)
                        )
                    )

    def test_payload_is_exact_and_caller_cannot_supply_server_owned_fields(self):
        class PayloadDictSubclass(dict):
            pass

        class CustomPayloadMapping(Mapping):
            def __init__(self, value: dict):
                self._value = value

            def __getitem__(self, key: object) -> object:
                return self._value[key]

            def __iter__(self):
                return iter(self._value)

            def __len__(self) -> int:
                return len(self._value)

        class DuckTypedPayload:
            def __init__(self, value: dict):
                self._value = value

            def get(self, key: object, default=None):
                return self._value.get(key, default)

            def keys(self):
                return self._value.keys()

            def __iter__(self):
                return iter(self._value)

            def __len__(self) -> int:
                return len(self._value)

        for missing_field in ("mailboxId", "sourceRef", "state"):
            payload = _create_payload()
            payload.pop(missing_field)
            with self.subTest(missing=missing_field):
                self._assert_exact_payload_rejected_without_side_effects(payload)

        non_exact_payloads = (
            ("none", None),
            ("list", list(_create_payload().items())),
            ("tuple", tuple(_create_payload().items())),
            ("string", PRIVATE_SOURCE_MARKER),
            ("integer", 42),
            ("boolean", True),
            ("dict-subclass", PayloadDictSubclass(_create_payload())),
            ("custom-mapping", CustomPayloadMapping(_create_payload())),
            ("duck-typed-mapping", DuckTypedPayload(_create_payload())),
        )
        for label, payload in non_exact_payloads:
            with self.subTest(payload_type=label):
                self._assert_exact_payload_rejected_without_side_effects(payload)

        forbidden_fields = (
            "collaborationId",
            "ownerEmail",
            "workspaceId",
            "sourceMessage",
            "subject",
            "sender",
            "body",
            "provider",
            "createdAt",
            "updatedAt",
            "messages",
            "participants",
            "invitations",
            "guest",
            "actor",
            "visibility",
            "credentials",
        )
        for field in forbidden_fields:
            payload = {**_create_payload(), field: PRIVATE_SOURCE_MARKER}
            with self.subTest(field=field):
                self._assert_exact_payload_rejected_without_side_effects(payload)

        for state in ("resolved", "unknown", "", None, 1):
            payload = _create_payload()
            payload["state"] = state
            with self.subTest(state=state):
                self._assert_exact_payload_rejected_without_side_effects(payload)

    def test_repeated_source_creation_is_atomic_and_reloads_exact_duplicate(self):
        headers = [("Authorization", "private-request-marker")]
        payload = _create_payload()
        capability = _create_capability("google")
        authorization_result = {
            "status": "ok",
            "context": capability,
            "error": None,
        }
        store = StatefulV2Store()

        with _stateful_backend(store), patch.object(
            redis_store,
            "resolve_v2_index_hmac_keys",
            return_value=(b"k" * 32, None),
        ), patch.object(
            application,
            "resolve_internal_collaboration_context",
            return_value=authorization_result,
        ), patch.object(
            application,
            "resolve_source_message",
            side_effect=_source_resolver_with_fake_providers(),
        ), patch.object(
            application,
            "generate_v2_opaque_id",
            side_effect=(COLLABORATION_ID, OTHER_COLLABORATION_ID),
        ), patch.object(
            application.time,
            "time_ns",
            return_value=NOW_MILLISECONDS * 1_000_000,
        ):
            first = application.create_v2_collaboration_for_owner(headers, payload)
            second = application.create_v2_collaboration_for_owner(headers, payload)

        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(second["collaboration"], first["collaboration"])
        self.assertEqual(first["collaboration"]["collaborationId"], COLLABORATION_ID)
        self.assertNotIn(
            redis_store.build_v2_thread_key(OTHER_COLLABORATION_ID),
            store.values,
        )
        thread_keys = [
            key
            for key in store.values
            if key.startswith(redis_store.V2_THREAD_KEY_PREFIX)
        ]
        self.assertEqual(
            thread_keys,
            [redis_store.build_v2_thread_key(COLLABORATION_ID)],
        )
        self.assertEqual(
            [command[0] for command in store.commands],
            ["EVAL", "EVAL", "EVAL", "GET", "GET"],
        )
        self.assertEqual(
            store.commands[-1],
            ["GET", redis_store.build_v2_thread_key(COLLABORATION_ID)],
        )
        self.assertTrue(
            all(command[0] not in {"SCAN", "KEYS", "SET"} for command in store.commands)
        )
        self.assertTrue(
            all(
                "collab:v1" not in str(command) and "collab:v2" in str(command)
                for command in store.commands
            )
        )
        self.assertFalse(
            any(type(value) is redis_store._V2RecordResult for value in _walk(second))
        )

    def test_unauthorized_invalid_and_mismatched_sources_fail_before_provider_or_storage(self):
        unauthorized = {
            "status": "unauthorized",
            "context": None,
            "error": {"code": "auth_required"},
        }
        with patch.object(
            application,
            "resolve_internal_collaboration_context",
            return_value=unauthorized,
        ), patch.object(
            application,
            "resolve_source_message",
        ) as resolve_source, patch.object(
            application,
            "_create_v2_thread",
        ) as creator, patch.object(
            application,
            "_load_v2_thread",
        ) as loader:
            result = application.create_v2_collaboration_for_owner(
                [], _create_payload()
            )
        self.assertEqual(result["error"], {"code": "auth_required"})
        resolve_source.assert_not_called()
        creator.assert_not_called()
        loader.assert_not_called()

        invalid_sources = (
            (
                "noncanonical-uidvalidity",
                "custom_imap",
                {"folder": "INBOX", "uidValidity": "01", "imapUid": "456"},
            ),
            (
                "noncanonical-uid",
                "custom_imap",
                {"folder": "INBOX", "uidValidity": "123", "imapUid": "0"},
            ),
            (
                "provider-mismatch",
                "custom_imap",
                {"providerMessageId": "gmail-id"},
            ),
        )
        for label, provider, source_ref in invalid_sources:
            payload = _create_payload(provider)
            payload["sourceRef"] = source_ref
            capability = _create_capability(provider)
            events: list[tuple[str, object]] = []
            with self.subTest(label=label), patch.object(
                application,
                "resolve_internal_collaboration_context",
                return_value={"status": "ok", "context": capability, "error": None},
            ), patch.object(
                application,
                "resolve_source_message",
                side_effect=_source_resolver_with_fake_providers(events),
            ), patch.object(
                application,
                "_create_v2_thread",
            ) as creator:
                result = application.create_v2_collaboration_for_owner([], payload)
            self.assertEqual(result["error"], {"code": "invalid_request"})
            self.assertEqual(events, [])
            creator.assert_not_called()

    def test_real_custom_imap_create_uses_authorized_provider_and_safe_dto(self):
        headers = [("Authorization", "private-request-marker")]
        payload = _create_payload("custom_imap")
        events: list[tuple[str, object]] = []
        stored: list[dict] = []

        class Mailbox:
            def select(self, folder: str, readonly: bool):
                events.append(("imap_select", (folder, readonly)))
                return "OK", []

            def response(self, name: str):
                events.append(("imap_uidvalidity", name))
                return "UIDVALIDITY", [b"123"]

            def uid(self, *args):
                events.append(("imap_fetch", args))
                return "OK", [(b"bounded-message", _raw_source_message())]

            def logout(self):
                events.append(("imap_logout", None))

        mailbox = Mailbox()

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
                        "inbox": {
                            "id": mailbox_id,
                            "provider": "custom_imap",
                        },
                    }

                return resolve_owned_mailbox
            self.fail(f"unexpected shared configuration helper: {name}")

        def resolve_authenticated_imap_mailbox(
            received_headers: object,
            mailbox_id: str,
        ) -> dict:
            self.assertIs(received_headers, headers)
            self.assertEqual(mailbox_id, MAILBOX_ID)
            events.append(("provider_authorization", "custom_imap"))
            return {
                "status": "ok",
                "mailbox": {
                    "imap": {
                        "host": "imap.test.invalid",
                        "port": 993,
                        "username": "owner",
                        "password": "private-test-password",
                        "ssl": True,
                    },
                },
            }

        def connect_mailbox_with_settings(*settings):
            self.assertEqual(
                settings,
                (
                    "imap.test.invalid",
                    993,
                    "owner",
                    "private-test-password",
                    True,
                ),
            )
            events.append(("provider_connect", "custom_imap"))
            return mailbox

        authenticated_imap = SimpleNamespace(
            __name__="api.inboxes.authenticated_imap",
            resolve_authenticated_imap_mailbox=resolve_authenticated_imap_mailbox,
        )
        imap_connect_preview = SimpleNamespace(
            __name__="imap_connect_preview",
            connect_mailbox_with_settings=connect_mailbox_with_settings,
        )

        def create(record: dict) -> object:
            events.append(("atomic_create", "custom_imap"))
            stored.append(record)
            return redis_store._V2RecordResult(record, created=True)

        with patch.dict(
            sys.modules,
            {
                "api.inboxes.authenticated_imap": authenticated_imap,
                "authenticated_imap": authenticated_imap,
                "imap_connect_preview": imap_connect_preview,
            },
        ), patch.object(
            authorization,
            "_shared_config_helper",
            side_effect=shared_config_helper,
        ), patch.object(
            source_message,
            "_load_fetch_gmail_module",
        ) as google_loader, patch.object(
            application,
            "generate_v2_opaque_id",
            return_value=COLLABORATION_ID,
        ), patch.object(
            application.time,
            "time_ns",
            return_value=NOW_MILLISECONDS * 1_000_000,
        ), patch.object(
            application,
            "_create_v2_thread",
            side_effect=create,
        ) as creator, patch.object(
            application,
            "_load_v2_thread",
        ) as loader:
            result = application.create_v2_collaboration_for_owner(
                headers,
                payload,
            )

        self.assertEqual(
            events,
            [
                ("authentication", None),
                ("mailbox_authorization", MAILBOX_ID),
                ("provider_authorization", "custom_imap"),
                ("provider_connect", "custom_imap"),
                ("imap_select", ("INBOX", True)),
                ("imap_uidvalidity", "UIDVALIDITY"),
                (
                    "imap_fetch",
                    (
                        "fetch",
                        "456",
                        f"(UID BODY.PEEK[]<0.{source_message.MAX_SOURCE_MESSAGE_BYTES + 1}>)",
                    ),
                ),
                ("imap_logout", None),
                ("atomic_create", "custom_imap"),
            ],
        )
        self.assertNotIn("sourceMessage", payload)
        self.assertNotIn("provider", payload["sourceRef"])
        self.assertEqual(
            stored[0]["sourceRef"],
            {
                "provider": "custom_imap",
                "folder": "INBOX",
                "uidValidity": "123",
                "imapUid": "456",
            },
        )
        self.assertEqual(
            stored[0]["sourceMessage"],
            {
                "subject": "Quarterly launch review",
                "senderDisplay": "Alex Sender",
                "fromDisplay": "Alex Sender <alex@example.net>",
                "timestamp": "Tue, 02 Jan 2024 10:30:00 +0000",
                "bodyText": "Please review the launch details.",
            },
        )
        self.assertEqual(result["created"], True)
        self.assertEqual(
            result["collaboration"]["source"],
            stored[0]["sourceMessage"],
        )
        self.assertTrue(
            {
                "folder",
                "uidValidity",
                "imapUid",
                "sourceRef",
                "provider",
                "providerMessageId",
            }.isdisjoint(_all_keys(result))
        )
        self.assertNotIn("private-test-password", _public_text(result))
        google_loader.assert_not_called()
        creator.assert_called_once_with(stored[0])
        loader.assert_not_called()

    def test_real_custom_imap_create_rejects_changed_and_noncanonical_uids(self):
        headers = [("Authorization", "private-request-marker")]
        cases = (
            (
                "uidvalidity-mismatch",
                {"folder": "INBOX", "uidValidity": "123", "imapUid": "456"},
                "124",
                {
                    "status": "error",
                    "collaboration": None,
                    "error": {"code": "source_changed"},
                },
                True,
            ),
            (
                "noncanonical-uidvalidity",
                {"folder": "INBOX", "uidValidity": "0123", "imapUid": "456"},
                "123",
                {
                    "status": "malformed",
                    "collaboration": None,
                    "error": {"code": "invalid_request"},
                },
                False,
            ),
            (
                "noncanonical-imap-uid",
                {"folder": "INBOX", "uidValidity": "123", "imapUid": "0456"},
                "123",
                {
                    "status": "malformed",
                    "collaboration": None,
                    "error": {"code": "invalid_request"},
                },
                False,
            ),
        )

        for label, source_ref, selected_uidvalidity, expected, provider_called in cases:
            events: list[tuple[str, object]] = []

            class Mailbox:
                def select(self, folder: str, readonly: bool):
                    events.append(("imap_select", (folder, readonly)))
                    return "OK", []

                def response(self, name: str):
                    events.append(("imap_uidvalidity", name))
                    return "UIDVALIDITY", [selected_uidvalidity.encode("ascii")]

                def uid(self, *args):
                    events.append(("imap_fetch", args))
                    return "OK", [(b"bounded-message", _raw_source_message())]

                def logout(self):
                    events.append(("imap_logout", None))

            mailbox = Mailbox()

            def shared_config_helper(name: str):
                if name == "resolve_authenticated_user":
                    def resolve_authenticated_user(received_headers: object):
                        self.assertIs(received_headers, headers)
                        events.append(("authentication", None))
                        return {
                            "email": OWNER_EMAIL,
                            "name": "Owner Person",
                        }, None

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
                            "user": {
                                "email": OWNER_EMAIL,
                                "name": "Owner Person",
                            },
                            "inbox": {
                                "id": mailbox_id,
                                "provider": "custom_imap",
                            },
                        }

                    return resolve_owned_mailbox
                self.fail(f"unexpected shared configuration helper: {name}")

            def resolve_authenticated_imap_mailbox(
                received_headers: object,
                mailbox_id: str,
            ) -> dict:
                self.assertIs(received_headers, headers)
                self.assertEqual(mailbox_id, MAILBOX_ID)
                events.append(("provider_authorization", "custom_imap"))
                return {
                    "status": "ok",
                    "mailbox": {
                        "imap": {
                            "host": "imap.test.invalid",
                            "port": 993,
                            "username": "owner",
                            "password": "private-test-password",
                            "ssl": True,
                        },
                    },
                }

            def connect_mailbox_with_settings(*_settings):
                events.append(("provider_connect", "custom_imap"))
                return mailbox

            authenticated_imap = SimpleNamespace(
                __name__="api.inboxes.authenticated_imap",
                resolve_authenticated_imap_mailbox=resolve_authenticated_imap_mailbox,
            )
            imap_connect_preview = SimpleNamespace(
                __name__="imap_connect_preview",
                connect_mailbox_with_settings=connect_mailbox_with_settings,
            )
            payload = {
                "mailboxId": MAILBOX_ID,
                "sourceRef": source_ref,
                "state": "needs_review",
            }

            with self.subTest(label=label), patch.dict(
                sys.modules,
                {
                    "api.inboxes.authenticated_imap": authenticated_imap,
                    "authenticated_imap": authenticated_imap,
                    "imap_connect_preview": imap_connect_preview,
                },
            ), patch.object(
                authorization,
                "_shared_config_helper",
                side_effect=shared_config_helper,
            ), patch.object(
                source_message,
                "_load_fetch_gmail_module",
            ) as google_loader, patch.object(
                application,
                "_create_v2_thread",
            ) as creator, patch.object(
                application,
                "_load_v2_thread",
            ) as loader:
                result = application.create_v2_collaboration_for_owner(
                    headers,
                    payload,
                )

            self.assertEqual(result, expected)
            self.assertEqual(
                events[:2],
                [
                    ("authentication", None),
                    ("mailbox_authorization", MAILBOX_ID),
                ],
            )
            self.assertEqual(
                ("provider_authorization", "custom_imap") in events,
                provider_called,
            )
            if provider_called:
                self.assertIn(("imap_uidvalidity", "UIDVALIDITY"), events)
                self.assertNotIn("imap_fetch", [event[0] for event in events])
                self.assertEqual(events[-1], ("imap_logout", None))
            else:
                self.assertEqual(len(events), 2)
            self.assertNotIn("private-test-password", _public_text(result))
            google_loader.assert_not_called()
            creator.assert_not_called()
            loader.assert_not_called()

    def test_source_failures_are_allowlisted_and_do_not_expose_provider_data(self):
        capability = _create_capability("google")
        authorization_result = {
            "status": "ok",
            "context": capability,
            "error": None,
        }
        cases = (
            ("stale-gmail", "not_found", "source_not_found"),
            ("imap-uidvalidity", "conflict", "source_changed"),
            ("provider-outage", "unavailable", "provider_unavailable"),
        )
        for label, status, code in cases:
            with self.subTest(label=label), patch.object(
                application,
                "resolve_internal_collaboration_context",
                return_value=authorization_result,
            ), patch.object(
                application,
                "resolve_source_message",
                return_value={
                    "status": status,
                    "source": None,
                    "error": {
                        "code": code,
                        "private": PRIVATE_SOURCE_MARKER,
                    },
                },
            ), patch.object(
                application,
                "_create_v2_thread",
            ) as creator:
                result = application.create_v2_collaboration_for_owner(
                    [], _create_payload()
                )
            self.assertEqual(result["error"], {"code": code})
            self.assertNotIn(PRIVATE_SOURCE_MARKER, _public_text(result))
            self.assertFalse(
                any(isinstance(value, BaseException) for value in _walk(result))
            )
            creator.assert_not_called()

    def test_duplicate_scope_source_and_record_mismatches_fail_closed(self):
        capability = _create_capability("google")
        authorization_result = {
            "status": "ok",
            "context": capability,
            "error": None,
        }
        candidate = _thread_record()
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

        wrong_source = _thread_record()
        wrong_source["sourceRef"] = {
            "provider": "google",
            "providerMessageId": "different-source",
        }
        cases.append(("source-mismatch", wrong_source, "forbidden"))

        wrong_collaboration = _thread_record()
        wrong_collaboration["collaborationId"] = OTHER_COLLABORATION_ID
        cases.append(("collaboration-id-mismatch", wrong_collaboration, "forbidden"))

        malformed = _thread_record()
        malformed.pop("sourceMessage")
        cases.append(("malformed", malformed, "storage_protocol_error"))

        for label, loaded, expected_code in cases:
            with self.subTest(label=label), patch.object(
                application,
                "resolve_internal_collaboration_context",
                return_value=authorization_result,
            ), patch.object(
                application,
                "resolve_source_message",
                side_effect=_source_resolver_with_fake_providers(),
            ), patch.object(
                application,
                "generate_v2_opaque_id",
                return_value=OTHER_COLLABORATION_ID,
            ), patch.object(
                application.time,
                "time_ns",
                return_value=NOW_MILLISECONDS * 1_000_000,
            ), patch.object(
                application,
                "_create_v2_thread",
                return_value=redis_store._V2RecordResult(
                    candidate,
                    created=False,
                ),
            ), patch.object(
                application,
                "_load_v2_thread",
                return_value=redis_store._V2RecordResult(loaded),
            ) as loader:
                result = application.create_v2_collaboration_for_owner(
                    [], _create_payload()
                )
            self.assertEqual(result["error"], {"code": expected_code})
            self.assertIsNone(result["collaboration"])
            loader.assert_called_once_with(COLLABORATION_ID)

    def test_duplicate_final_reload_failures_are_strict_and_private(self):
        capability = _create_capability("google")
        authorization_result = {
            "status": "ok",
            "context": capability,
            "error": None,
        }
        candidate = redis_store._V2RecordResult(
            _thread_record(),
            created=False,
        )
        cases = (
            (
                "missing",
                {"status": "missing"},
                {
                    "status": "not_found",
                    "collaboration": None,
                    "error": {"code": "collaboration_not_found"},
                },
            ),
            (
                "malformed-thread",
                {"status": "malformed"},
                {
                    "status": "malformed",
                    "collaboration": None,
                    "error": {"code": "storage_protocol_error"},
                },
            ),
            (
                "storage-unavailable",
                {
                    "status": "unavailable",
                    "error": {"code": "storage_unavailable"},
                },
                {
                    "status": "unavailable",
                    "collaboration": None,
                    "error": {"code": "storage_unavailable"},
                },
            ),
            (
                "storage-protocol-error",
                {
                    "status": "unavailable",
                    "error": {"code": "storage_protocol_error"},
                },
                {
                    "status": "unavailable",
                    "collaboration": None,
                    "error": {"code": "storage_protocol_error"},
                },
            ),
            (
                "unknown-storage-error",
                {
                    "status": "unavailable",
                    "error": {
                        "code": "private-" + PRIVATE_EXCEPTION_MARKER,
                    },
                },
                {
                    "status": "unavailable",
                    "collaboration": None,
                    "error": {"code": "storage_protocol_error"},
                },
            ),
            (
                "malformed-storage-error",
                {
                    "status": "unavailable",
                    "error": {
                        "code": "storage_unavailable",
                        "privateRawKey": PRIVATE_EXCEPTION_MARKER,
                    },
                },
                {
                    "status": "unavailable",
                    "collaboration": None,
                    "error": {"code": "storage_protocol_error"},
                },
            ),
        )

        for label, loaded, expected in cases:
            with self.subTest(label=label), patch.object(
                application,
                "resolve_internal_collaboration_context",
                return_value=authorization_result,
            ), patch.object(
                application,
                "resolve_source_message",
                side_effect=_source_resolver_with_fake_providers(),
            ), patch.object(
                application,
                "generate_v2_opaque_id",
                return_value=OTHER_COLLABORATION_ID,
            ), patch.object(
                application.time,
                "time_ns",
                return_value=NOW_MILLISECONDS * 1_000_000,
            ), patch.object(
                application,
                "_create_v2_thread",
                return_value=candidate,
            ) as creator, patch.object(
                application,
                "_load_v2_thread",
                return_value=loaded,
            ) as loader:
                result = application.create_v2_collaboration_for_owner(
                    [],
                    _create_payload(),
                )

            self.assertEqual(result, expected)
            self.assertIsNone(result["collaboration"])
            self.assertNotIn(PRIVATE_EXCEPTION_MARKER, _public_text(result))
            self.assertNotIn("privateRawKey", _public_text(result))
            self.assertFalse(
                any(
                    type(value) is redis_store._V2RecordResult
                    for value in _walk(result)
                )
            )
            creator.assert_called_once()
            loader.assert_called_once_with(COLLABORATION_ID)

        trusted_defect = RuntimeError(PRIVATE_EXCEPTION_MARKER)
        with patch.object(
            application,
            "resolve_internal_collaboration_context",
            return_value=authorization_result,
        ), patch.object(
            application,
            "resolve_source_message",
            side_effect=_source_resolver_with_fake_providers(),
        ), patch.object(
            application,
            "generate_v2_opaque_id",
            return_value=OTHER_COLLABORATION_ID,
        ), patch.object(
            application.time,
            "time_ns",
            return_value=NOW_MILLISECONDS * 1_000_000,
        ), patch.object(
            application,
            "_create_v2_thread",
            return_value=candidate,
        ) as creator, patch.object(
            application,
            "_load_v2_thread",
            side_effect=trusted_defect,
        ) as loader:
            with self.assertRaises(RuntimeError) as raised:
                application.create_v2_collaboration_for_owner(
                    [],
                    _create_payload(),
                )

        self.assertIs(raised.exception, trusted_defect)
        creator.assert_called_once()
        loader.assert_called_once_with(COLLABORATION_ID)

    def test_storage_errors_are_strict_and_private_results_never_escape(self):
        capability = _create_capability("google")
        authorization_result = {
            "status": "ok",
            "context": capability,
            "error": None,
        }
        cases = (
            (
                "protocol",
                {"status": "unavailable", "error": {"code": "storage_protocol_error"}},
                "storage_protocol_error",
            ),
            (
                "outage",
                {"status": "unavailable", "error": {"code": "storage_unavailable"}},
                "storage_unavailable",
            ),
            (
                "unknown",
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
                        "rawKey": "cuevion:collab:v2:private",
                    },
                },
                "storage_protocol_error",
            ),
        )
        for label, storage_result, expected_code in cases:
            with self.subTest(label=label), patch.object(
                application,
                "resolve_internal_collaboration_context",
                return_value=authorization_result,
            ), patch.object(
                application,
                "resolve_source_message",
                side_effect=_source_resolver_with_fake_providers(),
            ), patch.object(
                application,
                "generate_v2_opaque_id",
                return_value=COLLABORATION_ID,
            ), patch.object(
                application.time,
                "time_ns",
                return_value=NOW_MILLISECONDS * 1_000_000,
            ), patch.object(
                application,
                "_create_v2_thread",
                return_value=storage_result,
            ):
                result = application.create_v2_collaboration_for_owner(
                    [], _create_payload()
                )
            self.assertEqual(result["error"], {"code": expected_code})
            text = _public_text(result)
            self.assertNotIn(PRIVATE_EXCEPTION_MARKER, text)
            self.assertNotIn("rawKey", text)
            self.assertNotIn("source-thread", text)
            self.assertFalse(
                any(type(value) is redis_store._V2RecordResult for value in _walk(result))
            )

    def test_forged_capabilities_and_unexpected_helper_defects_are_not_hidden(self):
        @dataclass(frozen=True)
        class ForgedCreateCapability:
            owner_email: str = OWNER_EMAIL
            workspace_id: str = OWNER_EMAIL
            mailbox_id: str = MAILBOX_ID
            mailbox_provider: str = "google"
            collaboration_id: None = None
            action: str = "create"

        forged_values = (
            {
                "owner_email": OWNER_EMAIL,
                "workspace_id": OWNER_EMAIL,
                "mailbox_id": MAILBOX_ID,
                "mailbox_provider": "google",
                "collaboration_id": None,
                "action": "create",
            },
            ForgedCreateCapability(),
        )
        for forged in forged_values:
            with self.subTest(kind=type(forged).__name__), patch.object(
                application,
                "resolve_internal_collaboration_context",
                return_value={"status": "ok", "context": forged, "error": None},
            ), patch.object(
                application,
                "resolve_source_message",
            ) as resolve_source, patch.object(
                application,
                "_create_v2_thread",
            ) as creator:
                result = application.create_v2_collaboration_for_owner(
                    [], _create_payload()
                )
            self.assertEqual(result["error"], {"code": "forbidden"})
            resolve_source.assert_not_called()
            creator.assert_not_called()

        with patch.object(
            application,
            "resolve_internal_collaboration_context",
            side_effect=AssertionError,
        ):
            with self.assertRaises(AssertionError):
                application.create_v2_collaboration_for_owner([], _create_payload())

        capability = _create_capability("google")
        authorization_result = {
            "status": "ok",
            "context": capability,
            "error": None,
        }
        with patch.object(
            application,
            "resolve_internal_collaboration_context",
            return_value=authorization_result,
        ), patch.object(
            application,
            "resolve_source_message",
            side_effect=TypeError,
        ):
            with self.assertRaises(TypeError):
                application.create_v2_collaboration_for_owner([], _create_payload())

        with patch.object(
            application,
            "resolve_internal_collaboration_context",
            return_value=authorization_result,
        ), patch.object(
            application,
            "resolve_source_message",
            side_effect=_source_resolver_with_fake_providers(),
        ), patch.object(
            application,
            "generate_v2_opaque_id",
            return_value=COLLABORATION_ID,
        ), patch.object(
            application.time,
            "time_ns",
            return_value=NOW_MILLISECONDS * 1_000_000,
        ), patch.object(
            application,
            "_create_v2_thread",
            side_effect=AttributeError,
        ):
            with self.assertRaises(AttributeError):
                application.create_v2_collaboration_for_owner([], _create_payload())


class OwnerMutationApplicationTests(unittest.TestCase):
    SERVICES = (
        (
            "shared",
            application.append_v2_shared_message_for_owner,
            "reply",
            "shared",
        ),
        (
            "internal",
            application.append_v2_internal_note_for_owner,
            "internal_note",
            "internal",
        ),
    )

    @staticmethod
    def _authorization_result(action: str) -> dict:
        return {
            "status": "ok",
            "context": _owner_capability(action=action),
            "error": None,
        }

    @staticmethod
    def _mutation_success(
        capability: object,
        text: str,
        visibility: str,
        *,
        message_id: str = "M" * 22,
        timestamp: int = NOW_MILLISECONDS + 1,
    ) -> dict:
        return {
            "status": "ok",
            "message": {
                "id": message_id,
                "authorDisplayName": capability.actor_display_name,
                "authorRole": "Cuevion user",
                "text": text,
                "timestamp": timestamp,
                "visibility": visibility,
            },
            "updatedAt": timestamp,
            "error": None,
        }

    def _assert_payload_rejected_without_side_effects(
        self,
        service,
        payload: object,
    ) -> None:
        with patch.object(
            application,
            "resolve_internal_collaboration_context",
        ) as authorize, patch.object(
            authorization,
            "_shared_config_helper",
        ) as authenticate, patch.object(
            application,
            "_append_internal_v2_message",
        ) as mutate, patch.object(
            application,
            "_load_v2_thread",
        ) as load:
            result = service([], COLLABORATION_ID, payload)

        self.assertEqual(
            result,
            {
                "status": "malformed",
                "collaboration": None,
                "error": {"code": "invalid_request"},
            },
        )
        authorize.assert_not_called()
        authenticate.assert_not_called()
        mutate.assert_not_called()
        load.assert_not_called()

    def test_public_signatures_inventory_and_explicit_service_separation(self):
        for service in (
            application.append_v2_shared_message_for_owner,
            application.append_v2_internal_note_for_owner,
        ):
            self.assertEqual(
                list(inspect.signature(service).parameters),
                ["headers", "collaboration_id", "payload"],
            )
        self.assertEqual(
            application.__all__,
            [
                "append_v2_internal_note_for_owner",
                "append_v2_shared_message_for_owner",
                "create_v2_collaboration_for_owner",
                "read_v2_collaboration_for_guest",
                "read_v2_collaboration_for_owner",
            ],
        )
        for prohibited in (
            "append_v2_message",
            "append_v2_guest_reply",
            "append_v2_owner_message",
            "handle_append_v2_message",
            "handler",
        ):
            self.assertFalse(hasattr(application, prohibited))

    def test_payload_must_be_one_exact_text_field_before_authentication(self):
        class DictSubclass(dict):
            pass

        class CustomMapping(Mapping):
            def __init__(self):
                self._value = {"text": "mapping text"}

            def __getitem__(self, key):
                return self._value[key]

            def __iter__(self):
                return iter(self._value)

            def __len__(self):
                return len(self._value)

        class DuckMapping:
            def get(self, key, default=None):
                return "duck text" if key == "text" else default

            def keys(self):
                return ("text",)

        invalid_payloads = (
            ("missing-text", {}),
            ("none", None),
            ("list", ["text"]),
            ("tuple", ("text",)),
            ("string", "text"),
            ("integer", 1),
            ("boolean", True),
            ("dict-subclass", DictSubclass(text="subclass")),
            ("custom-mapping", CustomMapping()),
            ("duck-mapping", DuckMapping()),
            ("none-text", {"text": None}),
            ("list-text", {"text": ["text"]}),
            ("tuple-text", {"text": ("text",)}),
            ("integer-text", {"text": 1}),
            ("boolean-text", {"text": False}),
            ("dict-text", {"text": {"body": "text"}}),
            ("hidden-control", {"text": "private\x00text"}),
            (
                "oversized-utf8",
                {"text": "\N{LATIN SMALL LETTER E WITH ACUTE}" * 8193},
            ),
        )
        forbidden_fields = (
            "visibility",
            "action",
            "authorId",
            "authorDisplayName",
            "authorRole",
            "timestamp",
            "messageId",
            "createdAt",
            "updatedAt",
            "ownerEmail",
            "workspaceId",
            "mailboxId",
            "state",
            "mentions",
            "participants",
            "attachments",
            "bodyHtml",
            "sourceRef",
            "expectedUpdatedAt",
            "capability",
        )
        invalid_payloads += tuple(
            (
                "forbidden-" + field,
                {"text": "valid text", field: PRIVATE_EXCEPTION_MARKER},
            )
            for field in forbidden_fields
        )

        for service_label, service, _action, _visibility in self.SERVICES:
            for payload_label, payload in invalid_payloads:
                with self.subTest(service=service_label, payload=payload_label):
                    self._assert_payload_rejected_without_side_effects(
                        service,
                        payload,
                    )

    def test_empty_and_whitespace_text_follow_the_existing_canonical_foundation(self):
        # The current canonical v2 free-text validator deliberately preserves both.
        # This slice must not silently replace that foundation contract.
        for text in ("", " \t\r\n "):
            self.assertEqual(
                models._v2_free_text(text, max_length=models.MAX_V2_MESSAGE_TEXT),
                text,
            )
            authorization_result = self._authorization_result("reply")
            capability = authorization_result["context"]
            mutation_result = self._mutation_success(capability, text, "shared")
            with self.subTest(text=repr(text)), patch.object(
                application,
                "resolve_internal_collaboration_context",
                return_value=authorization_result,
            ) as authorize, patch.object(
                application,
                "_append_internal_v2_message",
                return_value=mutation_result,
            ) as mutate:
                result = application.append_v2_shared_message_for_owner(
                    [], COLLABORATION_ID, {"text": text}
                )

            self.assertEqual(result["message"]["text"], text)
            authorize.assert_called_once_with(
                [],
                collaboration_id=COLLABORATION_ID,
                required_action="reply",
            )
            mutate.assert_called_once_with(
                capability,
                text,
            )

    def test_services_request_exact_actions_and_hard_code_visibility(self):
        headers = [("Authorization", "private-request-marker")]
        for label, service, action, visibility in self.SERVICES:
            authorization_result = self._authorization_result(action)
            capability = authorization_result["context"]
            text = label + " owner message"
            mutation_result = self._mutation_success(
                capability,
                text,
                visibility,
            )
            with self.subTest(label=label), patch.object(
                application,
                "resolve_internal_collaboration_context",
                return_value=authorization_result,
            ) as authorize, patch.object(
                application,
                "_append_internal_v2_message",
                return_value=mutation_result,
            ) as mutate:
                result = service(headers, COLLABORATION_ID, {"text": text})

            authorize.assert_called_once_with(
                headers,
                collaboration_id=COLLABORATION_ID,
                required_action=action,
            )
            mutate.assert_called_once_with(
                capability,
                text,
            )
            self.assertEqual(
                result,
                {
                    "message": {
                        "id": "M" * 22,
                        "authorDisplayName": "Owner Person",
                        "authorRole": "Cuevion user",
                        "text": text,
                        "timestamp": NOW_MILLISECONDS + 1,
                        "visibility": visibility,
                    },
                    "updatedAt": NOW_MILLISECONDS + 1,
                },
            )

    def test_lazy_foundation_adapter_binds_capability_action_to_visibility(self):
        for action, visibility in (("reply", "shared"), ("internal_note", "internal")):
            capability = _owner_capability(action=action)
            foundation_result = {
                "status": "error",
                "error": {"code": "stale_thread"},
            }
            with self.subTest(action=action), patch.object(
                mutations,
                "append_internal_v2_message",
                return_value=foundation_result,
            ) as foundation:
                result = application._append_internal_v2_message(
                    capability,
                    "message",
                )

            self.assertIs(result, foundation_result)
            foundation.assert_called_once_with(
                capability,
                "message",
                visibility=visibility,
            )

        create_capability = _create_capability()
        with patch.object(mutations, "append_internal_v2_message") as foundation:
            result = application._append_internal_v2_message(
                create_capability,
                "message",
            )
        self.assertEqual(result["error"], {"code": "forbidden"})
        foundation.assert_not_called()

    def test_real_authorization_and_atomic_mutation_return_only_safe_dto(self):
        headers = [("Authorization", "private-request-marker")]
        for label, service, _action, visibility in self.SERVICES:
            store = StatefulV2Store()
            events: list[tuple[str, object]] = []
            with patch.object(
                redis_store,
                "resolve_v2_index_hmac_keys",
                return_value=(b"k" * 32, None),
            ):
                prepared = redis_store._create_v2_thread(
                    _thread_record(),
                    command_transport=store,
                )
            self.assertIs(type(prepared), redis_store._V2RecordResult)
            self.assertTrue(prepared.created)
            store.commands.clear()
            text = label + " message from authenticated owner"

            def shared_config_helper(name: str):
                if name == "resolve_authenticated_user":
                    def resolve_authenticated_user(received_headers: object):
                        self.assertIs(received_headers, headers)
                        events.append(("authentication", None))
                        return {
                            "email": OWNER_EMAIL,
                            "name": "Owner Person",
                        }, None

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
                            "user": {
                                "email": OWNER_EMAIL,
                                "name": "Owner Person",
                            },
                            "inbox": {
                                "id": mailbox_id,
                                "provider": "google",
                            },
                        }

                    return resolve_owned_mailbox
                self.fail(f"unexpected shared helper: {name}")

            with self.subTest(label=label), patch.object(
                authorization,
                "_shared_config_helper",
                side_effect=shared_config_helper,
            ), patch.object(
                redis_store,
                "resolve_v2_index_hmac_keys",
                return_value=(b"k" * 32, None),
            ), patch.object(
                mutations.time,
                "time_ns",
                return_value=(NOW_MILLISECONDS + 1) * 1_000_000,
            ), _stateful_backend(store, events):
                result = service(headers, COLLABORATION_ID, {"text": text})

            self.assertEqual(set(result), {"message", "updatedAt"})
            self.assertEqual(
                set(result["message"]),
                {
                    "id",
                    "authorDisplayName",
                    "authorRole",
                    "text",
                    "timestamp",
                    "visibility",
                },
            )
            self.assertRegex(result["message"]["id"], r"^[A-Za-z0-9_-]{22,128}$")
            self.assertEqual(result["message"]["authorDisplayName"], "Owner Person")
            self.assertEqual(result["message"]["authorRole"], "Cuevion user")
            self.assertEqual(result["message"]["text"], text)
            self.assertEqual(result["message"]["visibility"], visibility)
            self.assertEqual(result["message"]["timestamp"], NOW_MILLISECONDS + 1)
            self.assertEqual(result["updatedAt"], NOW_MILLISECONDS + 1)
            self.assertFalse(
                any(type(value) is redis_store._V2RecordResult for value in _walk(result))
            )
            self.assertFalse(
                any(authorization._is_internal_capability(value) for value in _walk(result))
            )
            self.assertTrue(
                {
                    "status",
                    "error",
                    "v",
                    "ownerEmail",
                    "workspaceId",
                    "mailboxId",
                    "collaborationId",
                    "sourceRef",
                    "sourceMessage",
                    "provider",
                    "participants",
                    "mentions",
                    "invitations",
                    "sessionHash",
                    "csrfTokenHash",
                    "bodyHtml",
                    "attachments",
                }.isdisjoint(_all_keys(result))
            )
            self.assertEqual(
                [command[0] for command in store.commands],
                ["GET", "GET", "EVAL"],
            )
            self.assertTrue(
                all(
                    command[0] not in {"SCAN", "KEYS"}
                    and not any(
                        isinstance(part, str) and any(marker in part for marker in "*?[]")
                        for part in command[2:]
                        if command[0] == "GET"
                    )
                    for command in store.commands
                )
            )
            stored = store.get_json(redis_store.build_v2_thread_key(COLLABORATION_ID))
            self.assertIsNotNone(stored)
            self.assertEqual(stored["messages"][-1]["text"], text)
            self.assertEqual(stored["messages"][-1]["visibility"], visibility)
            self.assertEqual(stored["messages"][-1]["id"], result["message"]["id"])
            self.assertEqual(
                [event for event in events if event[0] != "storage"][:2],
                [
                    ("authentication", None),
                    ("mailbox_authorization", MAILBOX_ID),
                ],
            )

    def test_cross_action_and_wrong_collaboration_capabilities_fail_before_mutation(self):
        reply = self._authorization_result("reply")
        internal_note = self._authorization_result("internal_note")
        cases = (
            (
                "reply-for-internal-note",
                application.append_v2_internal_note_for_owner,
                reply,
                COLLABORATION_ID,
            ),
            (
                "internal-note-for-shared",
                application.append_v2_shared_message_for_owner,
                internal_note,
                COLLABORATION_ID,
            ),
            (
                "wrong-collaboration",
                application.append_v2_shared_message_for_owner,
                reply,
                OTHER_COLLABORATION_ID,
            ),
        )
        for label, service, authorization_result, collaboration_id in cases:
            with self.subTest(label=label), patch.object(
                application,
                "resolve_internal_collaboration_context",
                return_value=authorization_result,
            ), patch.object(
                application,
                "_append_internal_v2_message",
            ) as mutate:
                result = service([], collaboration_id, {"text": "message"})

            self.assertEqual(result["error"], {"code": "forbidden"})
            self.assertIsNone(result["collaboration"])
            mutate.assert_not_called()

    def test_real_foundation_rejects_scope_and_malformed_records_before_write(self):
        authorization_result = self._authorization_result("reply")
        real_mutation = mutations.append_internal_v2_message
        cases: list[tuple[str, dict]] = []

        cross_owner = _thread_record()
        cross_owner["ownerEmail"] = OTHER_OWNER_EMAIL
        cross_owner["workspaceId"] = OTHER_OWNER_EMAIL
        cases.append(("cross-owner", cross_owner))

        cross_workspace = _thread_record()
        cross_workspace["workspaceId"] = OTHER_OWNER_EMAIL
        cases.append(("cross-workspace", cross_workspace))

        cross_mailbox = _thread_record()
        cross_mailbox["mailboxId"] = "mailbox-2"
        cases.append(("cross-mailbox", cross_mailbox))

        wrong_collaboration = _thread_record()
        wrong_collaboration["collaborationId"] = OTHER_COLLABORATION_ID
        cases.append(("wrong-collaboration", wrong_collaboration))

        malformed = _thread_record()
        malformed.pop("sourceMessage")
        cases.append(("malformed", malformed))

        for label, record in cases:
            loads: list[str] = []
            writes: list[object] = []

            def invoke_foundation(capability, text, *, visibility):
                return real_mutation(
                    capability,
                    text,
                    visibility=visibility,
                    thread_loader=lambda collaboration_id, **_kwargs: (
                        loads.append(collaboration_id)
                        or {"status": "ok", "record": record}
                    ),
                    thread_saver=lambda *_args, **_kwargs: writes.append(_args),
                )

            with self.subTest(label=label), patch.object(
                application,
                "resolve_internal_collaboration_context",
                return_value=authorization_result,
            ), patch.object(
                mutations,
                "append_internal_v2_message",
                side_effect=invoke_foundation,
            ):
                result = application.append_v2_shared_message_for_owner(
                    [], COLLABORATION_ID, {"text": "message"}
                )

            self.assertEqual(
                result["error"],
                {
                    "code": (
                        "storage_protocol_error"
                        if label in {"cross-workspace", "malformed"}
                        else "forbidden"
                    )
                },
            )
            self.assertEqual(loads, [COLLABORATION_ID])
            self.assertEqual(writes, [])

    def test_unauthenticated_and_scope_authorization_failures_precede_mutation(self):
        cases = (
            (
                "unauthenticated",
                {
                    "status": "unauthorized",
                    "context": None,
                    "error": {"code": "auth_required"},
                },
                "unauthorized",
                "auth_required",
            ),
            (
                "cross-owner",
                {
                    "status": "forbidden",
                    "context": None,
                    "error": {"code": "forbidden"},
                },
                "forbidden",
                "forbidden",
            ),
            (
                "cross-workspace",
                {
                    "status": "forbidden",
                    "context": None,
                    "error": {"code": "forbidden"},
                },
                "forbidden",
                "forbidden",
            ),
            (
                "cross-mailbox",
                {
                    "status": "forbidden",
                    "context": None,
                    "error": {"code": "forbidden"},
                },
                "forbidden",
                "forbidden",
            ),
            (
                "not-found",
                {
                    "status": "not_found",
                    "context": None,
                    "error": {"code": "collaboration_not_found"},
                },
                "not_found",
                "collaboration_not_found",
            ),
            (
                "malformed-id",
                {
                    "status": "malformed",
                    "context": None,
                    "error": {"code": "invalid_request"},
                },
                "malformed",
                "invalid_request",
            ),
        )
        for label, authorized, expected_status, expected_code in cases:
            with self.subTest(label=label), patch.object(
                application,
                "resolve_internal_collaboration_context",
                return_value=authorized,
            ), patch.object(
                application,
                "_append_internal_v2_message",
            ) as mutate:
                result = application.append_v2_shared_message_for_owner(
                    [], COLLABORATION_ID, {"text": "message"}
                )

            self.assertEqual(result["status"], expected_status)
            self.assertEqual(result["error"], {"code": expected_code})
            self.assertIsNone(result["collaboration"])
            mutate.assert_not_called()

    def test_forged_capability_shapes_never_reach_mutation(self):
        @dataclass(frozen=True)
        class ForgedCapability:
            collaboration_id: str = COLLABORATION_ID
            action: str = "reply"
            actor_display_name: str = "Owner Person"

        class DuckCapability:
            collaboration_id = COLLABORATION_ID
            action = "reply"
            actor_display_name = "Owner Person"

        for forged in (
            {
                "collaboration_id": COLLABORATION_ID,
                "action": "reply",
                "actor_display_name": "Owner Person",
            },
            ForgedCapability(),
            DuckCapability(),
            SimpleNamespace(
                collaboration_id=COLLABORATION_ID,
                action="reply",
                actor_display_name="Owner Person",
            ),
        ):
            with self.subTest(kind=type(forged).__name__), patch.object(
                application,
                "resolve_internal_collaboration_context",
                return_value={"status": "ok", "context": forged, "error": None},
            ), patch.object(
                application,
                "_append_internal_v2_message",
            ) as mutate:
                result = application.append_v2_shared_message_for_owner(
                    [], COLLABORATION_ID, {"text": "message"}
                )

            self.assertEqual(result["error"], {"code": "forbidden"})
            mutate.assert_not_called()

    def test_mutation_errors_are_allowlisted_preserved_and_never_retried(self):
        for _name, service, action, _visibility in self.SERVICES:
            authorization_result = self._authorization_result(action)
            for code in (
                "collaboration_not_found",
                "forbidden",
                "invalid_request",
                "stale_thread",
                "storage_unavailable",
                "storage_protocol_error",
            ):
                source_code = bytearray(code, "ascii").decode("ascii")
                with self.subTest(service=service.__name__, code=code), patch.object(
                    application,
                    "resolve_internal_collaboration_context",
                    return_value=authorization_result,
                ), patch.object(
                    application,
                    "_append_internal_v2_message",
                    return_value={
                        "status": "error",
                        "error": {"code": source_code},
                    },
                ) as mutate:
                    result = service([], COLLABORATION_ID, {"text": "message"})

                self.assertEqual(
                    result,
                    {
                        "status": "error",
                        "collaboration": None,
                        "error": {"code": code},
                    },
                )
                self.assertIs(type(result["error"]["code"]), str)
                self.assertIs(
                    result["error"]["code"],
                    application._CANONICAL_OWNER_MUTATION_ERROR_CODES[code],
                )
                mutate.assert_called_once()

    def test_malformed_and_private_mutation_results_fail_closed(self):
        class StringSubclass(str):
            pass

        class HashableEqualityCode:
            def __hash__(self):
                return hash("forbidden")

            def __eq__(self, other):
                return other == "forbidden"

            def __str__(self):
                return PRIVATE_EXCEPTION_MARKER

            def __repr__(self):
                return PRIVATE_EXCEPTION_MARKER

        malformed_codes = (
            ["storage_unavailable"],
            {"code": "storage_unavailable"},
            7,
            True,
            None,
            StringSubclass("storage_unavailable"),
            HashableEqualityCode(),
        )

        for _name, service, action, visibility in self.SERVICES:
            authorization_result = self._authorization_result(action)
            capability = authorization_result["context"]
            safe_success = self._mutation_success(
                capability, "message", visibility
            )
            wrong_author = self._mutation_success(
                capability, "message", visibility
            )
            wrong_author["message"]["authorDisplayName"] = PRIVATE_EXCEPTION_MARKER
            wrong_time = self._mutation_success(
                capability, "message", visibility
            )
            wrong_time["updatedAt"] += 1
            malformed_results = [
                None,
                redis_store._V2RecordResult(_thread_record()),
                *(
                    {"status": "error", "error": {"code": code}}
                    for code in malformed_codes
                ),
                {"status": "error", "error": {}},
                {"status": "error", "error": {"code": PRIVATE_EXCEPTION_MARKER}},
                {
                    "status": "error",
                    "error": {
                        "code": "storage_unavailable",
                        "details": PRIVATE_EXCEPTION_MARKER,
                    },
                },
                {
                    "status": "error",
                    "error": {"code": "storage_unavailable"},
                    "private": PRIVATE_EXCEPTION_MARKER,
                },
                {**safe_success, "record": _thread_record()},
                wrong_author,
                wrong_time,
            ]
            for index, value in enumerate(malformed_results):
                with self.subTest(
                    service=service.__name__, case=index
                ), patch.object(
                    application,
                    "resolve_internal_collaboration_context",
                    return_value=authorization_result,
                ), patch.object(
                    application,
                    "_append_internal_v2_message",
                    return_value=value,
                ) as mutate:
                    result = service(
                        [("Authorization", PRIVATE_EXCEPTION_MARKER)],
                        COLLABORATION_ID,
                        {"text": "message"},
                    )

                self.assertEqual(
                    result,
                    {
                        "status": "malformed",
                        "collaboration": None,
                        "error": {"code": "storage_protocol_error"},
                    },
                )
                self.assertIs(type(result["error"]["code"]), str)
                mutate.assert_called_once()
                for rendered in (
                    _public_text(result),
                    str(result),
                    repr(result),
                    str(result["error"]),
                    repr(result["error"]),
                    repr(getattr(result["error"]["code"], "args", ())),
                ):
                    self.assertNotIn(PRIVATE_EXCEPTION_MARKER, rendered)
                    self.assertNotIn(PRIVATE_SOURCE_MARKER, rendered)
                    self.assertNotIn(OWNER_EMAIL, rendered)
                self.assertFalse(
                    any(
                        type(item) is redis_store._V2RecordResult
                        for item in _walk(result)
                    )
                )
                self.assertFalse(
                    any(
                        authorization._is_internal_capability(item)
                        for item in _walk(result)
                    )
                )

    def test_services_fail_closed_on_inverted_foundation_visibility(self):
        for _name, service, action, visibility in self.SERVICES:
            authorization_result = self._authorization_result(action)
            capability = authorization_result["context"]
            wrong_visibility = "internal" if visibility == "shared" else "shared"
            with self.subTest(service=service.__name__), patch.object(
                application,
                "resolve_internal_collaboration_context",
                return_value=authorization_result,
            ), patch.object(
                application,
                "_append_internal_v2_message",
                return_value=self._mutation_success(
                    capability,
                    "message",
                    wrong_visibility,
                ),
            ) as mutate:
                result = service([], COLLABORATION_ID, {"text": "message"})

            self.assertEqual(
                result,
                {
                    "status": "malformed",
                    "collaboration": None,
                    "error": {"code": "storage_protocol_error"},
                },
            )
            mutate.assert_called_once()

    def test_unexpected_trusted_helper_exceptions_propagate(self):
        with patch.object(
            application,
            "resolve_internal_collaboration_context",
            side_effect=AssertionError(PRIVATE_EXCEPTION_MARKER),
        ):
            with self.assertRaisesRegex(AssertionError, PRIVATE_EXCEPTION_MARKER):
                application.append_v2_shared_message_for_owner(
                    [], COLLABORATION_ID, {"text": "message"}
                )

        authorization_result = self._authorization_result("internal_note")
        with patch.object(
            application,
            "resolve_internal_collaboration_context",
            return_value=authorization_result,
        ), patch.object(
            application,
            "_append_internal_v2_message",
            side_effect=TypeError(PRIVATE_EXCEPTION_MARKER),
        ):
            with self.assertRaisesRegex(TypeError, PRIVATE_EXCEPTION_MARKER):
                application.append_v2_internal_note_for_owner(
                    [], COLLABORATION_ID, {"text": "message"}
                )


class OwnerLookupApplicationTests(unittest.TestCase):
    def test_verified_owner_lookup_derives_provider_and_returns_only_opaque_id(self):
        headers = [("Authorization", "private-request-marker")]
        owner_context = object()
        security_configuration = object()
        cases = (
            (
                "google",
                {"providerMessageId": PRIVATE_SOURCE_MARKER},
                {
                    "provider": "google",
                    "providerMessageId": PRIVATE_SOURCE_MARKER,
                },
            ),
            (
                "custom_imap",
                {"folder": "INBOX", "uidValidity": "123", "imapUid": "456"},
                {
                    "provider": "custom_imap",
                    "folder": "INBOX",
                    "uidValidity": "123",
                    "imapUid": "456",
                },
            ),
        )

        for provider, locator, canonical_source_ref in cases:
            with self.subTest(provider=provider):
                capability = _mailbox_read_capability(provider)
                authorized = {
                    "status": "ok",
                    "context": capability,
                    "error": None,
                }
                thread = {
                    **_thread_record(),
                    "sourceRef": canonical_source_ref,
                }
                with patch.object(
                    application,
                    "resolve_verified_owner_collaboration_context",
                    return_value=authorized,
                ) as resolver, patch.object(
                    application,
                    "_load_v2_thread_by_source",
                    return_value=redis_store._V2RecordResult(thread),
                ) as loader, patch.object(
                    application,
                    "resolve_source_message",
                    side_effect=AssertionError("lookup must not fetch provider source"),
                ) as source_resolver:
                    result = (
                        application.lookup_v2_collaboration_for_verified_owner(
                            owner_context,
                            headers,
                            MAILBOX_ID,
                            locator,
                            owner_security_configuration=security_configuration,
                        )
                    )

                self.assertEqual(
                    result,
                    {
                        "status": "ok",
                        "collaborationId": COLLABORATION_ID,
                        "error": None,
                    },
                )
                resolver.assert_called_once_with(
                    owner_context,
                    headers,
                    MAILBOX_ID,
                    required_action="read",
                    owner_security_configuration=security_configuration,
                )
                loader.assert_called_once_with(
                    OWNER_EMAIL,
                    MAILBOX_ID,
                    canonical_source_ref,
                    workspace_id=OWNER_EMAIL,
                )
                source_resolver.assert_not_called()

    def test_lookup_requires_exact_canonical_provider_specific_locator(self):
        owner_context = object()
        security_configuration = object()
        cases = (
            ("google", None),
            ("google", {}),
            ("google", {"provider": "google", "providerMessageId": "message-1"}),
            ("google", {"providerMessageId": " message-1"}),
            ("google", {"providerMessageId": "message-1", "threadId": "thread-1"}),
            ("custom_imap", {"folder": "Archive", "uidValidity": "1", "imapUid": "2"}),
            ("custom_imap", {"folder": "INBOX", "uidValidity": 1, "imapUid": "2"}),
            ("custom_imap", {"folder": "INBOX", "uidValidity": "01", "imapUid": "2"}),
            ("custom_imap", {"folder": "INBOX", "uidValidity": "1", "imapUid": "0"}),
            (
                "custom_imap",
                {
                    "provider": "custom_imap",
                    "folder": "INBOX",
                    "uidValidity": "1",
                    "imapUid": "2",
                },
            ),
        )

        for provider, locator in cases:
            capability = _mailbox_read_capability(provider)
            with self.subTest(provider=provider, locator=locator), patch.object(
                application,
                "resolve_verified_owner_collaboration_context",
                return_value={"status": "ok", "context": capability, "error": None},
            ), patch.object(
                application,
                "_load_v2_thread_by_source",
                side_effect=AssertionError("invalid locator must not reach storage"),
            ) as loader:
                result = application.lookup_v2_collaboration_for_verified_owner(
                    owner_context,
                    [],
                    MAILBOX_ID,
                    locator,
                    owner_security_configuration=security_configuration,
                )
            self.assertEqual(result["error"], {"code": "invalid_request"})
            loader.assert_not_called()

    def test_lookup_masks_scope_mismatches_and_fails_closed_on_index_errors(self):
        capability = _mailbox_read_capability("google")
        authorized = {"status": "ok", "context": capability, "error": None}
        locator = {"providerMessageId": PRIVATE_SOURCE_MARKER}
        wrong_source = {
            **_thread_record(),
            "sourceRef": {
                "provider": "google",
                "providerMessageId": "different-message",
            },
        }
        cases = (
            (
                "missing",
                {"status": "missing", "error": {"code": "collaboration_not_found"}},
                "not_found",
                "collaboration_not_found",
            ),
            (
                "missing-hmac",
                {"status": "unavailable", "error": {"code": "index_hmac_unavailable"}},
                "unavailable",
                "index_hmac_unavailable",
            ),
            (
                "storage-unavailable",
                {"status": "unavailable", "error": {"code": "storage_unavailable"}},
                "unavailable",
                "storage_unavailable",
            ),
            (
                "conflicting-pointer",
                {"status": "malformed", "error": {"code": "source_pointer_conflict"}},
                "unavailable",
                "storage_protocol_error",
            ),
            (
                "wrong-source",
                redis_store._V2RecordResult(wrong_source),
                "forbidden",
                "forbidden",
            ),
        )

        for label, loaded, expected_status, expected_code in cases:
            with self.subTest(label=label), patch.object(
                application,
                "resolve_verified_owner_collaboration_context",
                return_value=authorized,
            ), patch.object(
                application,
                "_load_v2_thread_by_source",
                return_value=loaded,
            ):
                result = application.lookup_v2_collaboration_for_verified_owner(
                    object(),
                    [],
                    MAILBOX_ID,
                    locator,
                    owner_security_configuration=object(),
                )
            self.assertEqual(result["status"], expected_status)
            self.assertEqual(result["error"], {"code": expected_code})
            self.assertIsNone(result["collaboration"])

    def test_lookup_preserves_mailbox_authorization_failures_before_storage(self):
        failures = (
            ("malformed", "invalid_request"),
            ("unauthorized", "auth_required"),
            ("not_found", "mailbox_not_found"),
            ("forbidden", "forbidden"),
        )
        for status, code in failures:
            with self.subTest(status=status), patch.object(
                application,
                "resolve_verified_owner_collaboration_context",
                return_value={
                    "status": status,
                    "context": None,
                    "error": {"code": code},
                },
            ), patch.object(
                application,
                "_load_v2_thread_by_source",
                side_effect=AssertionError("failed authority must not reach storage"),
            ) as loader:
                result = application.lookup_v2_collaboration_for_verified_owner(
                    object(),
                    [],
                    "INVALID MAILBOX",
                    {"providerMessageId": "message-1"},
                    owner_security_configuration=object(),
                )
            self.assertEqual(result["error"], {"code": code})
            loader.assert_not_called()


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
            list(
                inspect.signature(
                    application.create_v2_collaboration_for_owner
                ).parameters
            ),
            ["headers", "payload"],
        )
        self.assertEqual(
            application.__all__,
            [
                "append_v2_internal_note_for_owner",
                "append_v2_shared_message_for_owner",
                "create_v2_collaboration_for_owner",
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
        mismatched_thread["workspaceId"] = OTHER_GUEST_WORKSPACE_ID
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


class ParticipantAuthorityApplicationTests(unittest.TestCase):
    workspace_id = "wsp_" + "W" * 22
    owner_user_id = "usr_" + "A" * 22
    participant_user_id = "usr_" + "B" * 21 + "A"

    def capability(self, action: str, *, viewer_access: str = "owner"):
        return authorization._InternalCollaborationCapability(
            authorization._INTERNAL_CAPABILITY_SENTINEL,
            OWNER_EMAIL,
            self.workspace_id,
            MAILBOX_ID,
            "google",
            None if action == "create" else COLLABORATION_ID,
            action,
            "owner" if viewer_access == "owner" else "internal",
            "Owner Person" if viewer_access == "owner" else "Participant",
            self.owner_user_id if viewer_access == "owner" else self.participant_user_id,
            viewer_access,
            self.owner_user_id,
            "Owner Person",
        )

    def participant_membership(self, provenance: str = "tinv_original"):
        return {
            "memberUserId": self.participant_user_id,
            "displayName": "Participant",
            "accessLevel": "Shared",
            "sourceInvitationId": provenance,
        }

    def modern_thread(self) -> dict:
        return {
            **_thread_record(),
            "workspaceId": self.workspace_id,
            "ownerUserId": self.owner_user_id,
            "ownerDisplayName": "Owner Person",
            "participants": [
                {
                    "userId": self.participant_user_id,
                    "membershipRef": "tinv_original",
                    "displayName": "Participant",
                }
            ],
        }

    def test_create_resolves_and_persists_initial_participant_atomically(self):
        capability = self.capability("create")
        stored: list[dict] = []

        def create(record):
            stored.append(record)
            return redis_store._V2RecordResult(record, created=True)

        source = {
            "status": "ok",
            "source": {
                "sourceRef": {
                    "provider": "google",
                    "providerMessageId": "provider-1",
                },
                "sourceMessage": self.modern_thread()["sourceMessage"],
            },
            "error": None,
        }
        with patch.object(
            application,
            "resolve_verified_owner_collaboration_context",
            return_value={"status": "ok", "context": capability, "error": None},
        ), patch.object(
            application,
            "_resolve_active_team_member",
            return_value=(self.participant_membership(), None),
        ) as team_resolver, patch.object(
            application,
            "resolve_source_message",
            return_value=source,
        ), patch.object(
            application,
            "_create_v2_thread",
            side_effect=create,
        ), patch.object(application.time, "time_ns", return_value=NOW_MILLISECONDS * 1_000_000):
            result = application.create_v2_collaboration_for_verified_owner(
                object(),
                object(),
                {
                    "mailboxId": MAILBOX_ID,
                    "sourceRef": {"providerMessageId": "provider-1"},
                    "state": "needs_review",
                    "participantUserId": self.participant_user_id,
                },
                owner_security_configuration=object(),
            )
        self.assertTrue(result["created"])
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["ownerUserId"], self.owner_user_id)
        self.assertEqual(
            stored[0]["participants"],
            [
                {
                    "userId": self.participant_user_id,
                    "membershipRef": "tinv_original",
                    "displayName": "Participant",
                }
            ],
        )
        self.assertEqual(result["collaboration"]["viewerAccess"], "owner")
        self.assertEqual(
            [person["access"] for person in result["collaboration"]["participants"]],
            ["owner", "participant"],
        )
        self.assertNotIn("membershipRef", repr(result["collaboration"]))
        self.assertGreaterEqual(team_resolver.call_count, 2)

    def test_team_failure_and_missing_or_self_participant_never_create_partial_state(self):
        capability = self.capability("create")
        create_calls: list[dict] = []
        source_calls: list[object] = []
        authorized = {"status": "ok", "context": capability, "error": None}
        with patch.object(
            application,
            "resolve_verified_owner_collaboration_context",
            return_value=authorized,
        ), patch.object(
            application,
            "_resolve_active_team_member",
            return_value=(None, "not_active"),
        ), patch.object(
            application,
            "resolve_source_message",
            side_effect=lambda *_args, **_kwargs: source_calls.append(object()),
        ), patch.object(
            application,
            "_create_v2_thread",
            side_effect=lambda record: create_calls.append(record),
        ):
            base = {
                "mailboxId": MAILBOX_ID,
                "sourceRef": {"providerMessageId": "provider-1"},
                "state": "needs_review",
            }
            missing = application.create_v2_collaboration_for_verified_owner(
                object(), object(), base,
                owner_security_configuration=object(),
            )
            removed = application.create_v2_collaboration_for_verified_owner(
                object(), object(), {**base, "participantUserId": self.participant_user_id},
                owner_security_configuration=object(),
            )
            self_target = application.create_v2_collaboration_for_verified_owner(
                object(), object(), {**base, "participantUserId": self.owner_user_id},
                owner_security_configuration=object(),
            )
        self.assertEqual(missing["error"], {"code": "invalid_request"})
        self.assertEqual(removed["error"], {"code": "forbidden"})
        self.assertEqual(self_target["error"], {"code": "invalid_request"})
        self.assertEqual(source_calls, [])
        self.assertEqual(create_calls, [])

    def test_old_owner_record_projects_new_dto_and_inactive_history_is_omitted(self):
        old = {**_thread_record(), "workspaceId": self.workspace_id}
        capability = self.capability("read")
        authorized = {"status": "ok", "context": capability, "error": None}
        with patch.object(
            application,
            "resolve_verified_owner_collaboration_context",
            return_value=authorized,
        ), patch.object(
            application,
            "_load_exact_thread",
            return_value=(old, None),
        ):
            result = application.read_v2_collaboration_for_verified_owner(
                object(), object(), COLLABORATION_ID,
                owner_security_configuration=object(),
            )
        self.assertEqual(result["collaboration"]["viewerAccess"], "owner")
        self.assertEqual(
            result["collaboration"]["participants"],
            [{"userId": self.owner_user_id, "displayName": "Owner Person", "access": "owner"}],
        )

        with patch.object(
            application,
            "resolve_verified_owner_collaboration_context",
            return_value=authorized,
        ), patch.object(
            application,
            "_load_exact_thread",
            return_value=(self.modern_thread(), None),
        ), patch.object(
            application,
            "_resolve_active_team_member",
            return_value=(None, "not_active"),
        ):
            inactive = application.read_v2_collaboration_for_verified_owner(
                object(), object(), COLLABORATION_ID,
                owner_security_configuration=object(),
            )
        self.assertEqual(inactive["status"], "ok")
        self.assertEqual(len(inactive["collaboration"]["participants"]), 1)

    def test_owner_add_uses_current_team_provenance_and_participant_add_is_denied(self):
        owner_capability = self.capability("manage_participants")
        modern = self.modern_thread()
        modern["participants"][0]["membershipRef"] = "tinv_new"
        authorized = {"status": "ok", "context": owner_capability, "error": None}
        with patch.object(
            application,
            "resolve_verified_owner_collaboration_context",
            return_value=authorized,
        ), patch.object(
            application,
            "_resolve_active_team_member",
            return_value=(self.participant_membership("tinv_new"), None),
        ), patch.object(
            mutations,
            "add_v2_participant",
            return_value={"status": "ok", "record": modern, "changed": True, "error": None},
        ) as add:
            result = application.add_v2_participant_for_verified_owner(
                object(), object(), COLLABORATION_ID,
                {"participantUserId": self.participant_user_id},
                owner_security_configuration=object(),
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(add.call_args.args[1]["membershipRef"], "tinv_new")

        participant = self.capability("manage_participants", viewer_access="participant")
        with patch.object(
            application,
            "resolve_verified_owner_collaboration_context",
            return_value={"status": "ok", "context": participant, "error": None},
        ):
            denied = application.add_v2_participant_for_verified_owner(
                object(), object(), COLLABORATION_ID,
                {"participantUserId": "usr_" + "C" * 21 + "A"},
                owner_security_configuration=object(),
            )
        self.assertEqual(denied["error"], {"code": "forbidden"})


if __name__ == "__main__":
    unittest.main()
