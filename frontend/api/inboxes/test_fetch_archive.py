from __future__ import annotations

import base64
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch


CURRENT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = CURRENT_DIR.parent.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))


def _load_route():
    spec = importlib.util.spec_from_file_location(
        "fetch_archive_route_test",
        CURRENT_DIR / "fetch-archive.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load fetch-archive route")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fetch_archive = _load_route()


MAILBOX_ID = "server-mailbox"
OWNER_EMAIL = "owner@example.com"
MAILBOX_EMAIL = "artist@example.com"
ACCESS_TOKEN = "test-access-token-never-return"
REFRESH_TOKEN = "test-refresh-token-never-return"
IMAP_HOST = "imap.provider.invalid"
IMAP_USERNAME = "server-owned-user"
IMAP_PASSWORD = "server-owned-password-never-return"
ARCHIVE_FOLDER = 'Stored "Archive"\\2026'


class FakeHandler:
    def __init__(
        self,
        payload: object,
        *,
        headers: dict | None = None,
    ):
        body = json.dumps(payload).encode("utf-8")
        self.headers = {
            "content-length": str(len(body)),
            **(headers or {}),
        }
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status = None
        self.response_headers: list[tuple[str, str]] = []
        self.command = "POST"
        self.close_connection = False

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        pass

    def response(self) -> dict:
        return json.loads(self.wfile.getvalue())

    def do_GET(self):
        fetch_archive.handler.do_GET(self)


class FakeResponse:
    def __init__(self, body: bytes, *, headers: dict | None = None):
        self.body = body
        self.headers = headers or {}
        self.stream = io.BytesIO(body)

    def read(self, amount=-1):
        return self.stream.read(amount)

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False


class RecordingMailbox:
    def __init__(
        self,
        *,
        list_response=None,
        logout_error: Exception | None = None,
    ):
        self.list_response = (
            (
                "OK",
                [
                    (
                        rf'(\HasNoChildren \Archive) "/" '
                        f'"{ARCHIVE_FOLDER.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
                    ).encode("utf-8")
                ],
            )
            if list_response is None
            else list_response
        )
        self.logout_error = logout_error
        self.list_count = 0
        self.logout_count = 0
        self.shutdown_count = 0
        self.unsafe_calls: list[tuple] = []

    def list(self):
        self.list_count += 1
        if isinstance(self.list_response, BaseException):
            raise self.list_response
        return self.list_response

    def logout(self):
        self.logout_count += 1
        if self.logout_error is not None:
            raise self.logout_error
        return "BYE", []

    def shutdown(self):
        self.shutdown_count += 1

    def select(self, *arguments, **kwargs):
        self.unsafe_calls.append(("select", arguments, kwargs))
        raise AssertionError("route discovery must not select directly")

    def uid(self, *arguments):
        self.unsafe_calls.append(("uid", *arguments))
        raise AssertionError("UID mutation must not be used")

    def copy(self, *arguments):
        self.unsafe_calls.append(("copy", *arguments))
        raise AssertionError("COPY must not be used")

    def store(self, *arguments):
        self.unsafe_calls.append(("store", *arguments))
        raise AssertionError("STORE must not be used")

    def expunge(self, *arguments):
        self.unsafe_calls.append(("expunge", *arguments))
        raise AssertionError("EXPUNGE must not be used")


def google_owned() -> dict:
    return {
        "status": "ok",
        "user": {"email": OWNER_EMAIL},
        "inbox": {
            "id": MAILBOX_ID,
            "provider": "google",
            "email": MAILBOX_EMAIL,
        },
    }


def custom_imap_owned() -> dict:
    return {
        "status": "ok",
        "user": {"email": OWNER_EMAIL},
        "inbox": {
            "id": MAILBOX_ID,
            "provider": "custom_imap",
            "email": MAILBOX_EMAIL,
        },
    }


def gmail_context(*, refresh_attempted: bool = False) -> dict:
    return {
        "mailbox_id": MAILBOX_ID,
        "mailbox_email": MAILBOX_EMAIL,
        "owner_email": OWNER_EMAIL,
        "access_token": ACCESS_TOKEN,
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
        "refresh_attempted": refresh_attempted,
    }


def gmail_snapshot(*, message_count: int = 1) -> dict:
    return {
        "serverMailboxId": MAILBOX_ID,
        "providerFolder": "Archive",
        "uidValidity": "gmail-api",
        "messages": [
            {
                "id": f"preview-{index}",
                "serverMailboxId": MAILBOX_ID,
                "providerFolder": "Archive",
                "providerMessageId": f"provider-message-{index}",
                "providerThreadId": f"provider-thread-{index}",
                "labelIds": ["STARRED"],
                "subject": "Archived message",
            }
            for index in range(message_count)
        ],
    }


def gmail_snapshot_result(snapshot: object | None = None) -> dict:
    return {
        "status": "ok",
        "context": gmail_context(),
        "snapshot": gmail_snapshot() if snapshot is None else snapshot,
        "error": None,
        "refresh_failure": None,
    }


def gmail_raw_message() -> str:
    raw = (
        b"Message-Id: <archive-message@example.test>\r\n"
        b"From: sender@example.test\r\n"
        b"To: artist@example.com\r\n"
        b"Subject: Archived provider message\r\n"
        b"\r\n"
        b"Archived body"
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def resolved_imap_mailbox() -> dict:
    return {
        "status": "ok",
        "mailbox": {
            "mailboxId": MAILBOX_ID,
            "email": MAILBOX_EMAIL,
            "imap": {
                "host": IMAP_HOST,
                "port": 993,
                "ssl": True,
                "username": IMAP_USERNAME,
                "password": IMAP_PASSWORD,
            },
        },
        "error": None,
    }


def imap_snapshot(*, folder: str = ARCHIVE_FOLDER) -> dict:
    return {
        "serverMailboxId": MAILBOX_ID,
        "providerFolder": folder,
        "uidValidity": "91",
        "imapUidSet": ["7", "9"],
        "messages": [
            {
                "id": "preview-9",
                "serverMailboxId": MAILBOX_ID,
                "providerFolder": folder,
                "uidValidity": "91",
                "imapUid": "9",
                "threadId": "imap:uid:server-mailbox:Stored%20Archive:91:9",
            }
        ],
    }


def imap_snapshot_result(snapshot: object | None = None) -> dict:
    return {
        "ok": True,
        "status": "ok",
        "snapshot": imap_snapshot() if snapshot is None else snapshot,
        "identities": {
            "9": {
                "fingerprint": "internal-fingerprint-never-return",
            }
        },
        "error": None,
    }


def invoke(payload: object) -> FakeHandler:
    target = FakeHandler(payload)
    fetch_archive.handler._handle_post(target)
    return target


class FetchArchiveAuthorityTests(unittest.TestCase):
    def test_session_owner_and_unknown_mailbox_stop_before_provider_resolution(self):
        authority_failures = (
            (
                401,
                "unauthorized",
                "A valid member session is required.",
            ),
            (
                404,
                "gmail_connection_not_found",
                "Mailbox connection was not found.",
            ),
            (
                404,
                "gmail_connection_not_found",
                "Mailbox connection was not found.",
            ),
        )
        for status_code, code, message in authority_failures:
            with self.subTest(code=code), patch.object(
                fetch_archive,
                "resolve_owned_mailbox",
                return_value={
                    "status": "error",
                    "status_code": status_code,
                    "error": {
                        "ok": False,
                        "error": {
                            "code": code,
                            "message": message,
                        },
                    },
                },
            ) as authority, patch.object(
                fetch_archive,
                "resolve_gmail_context",
            ) as gmail_resolution, patch.object(
                fetch_archive,
                "resolve_authenticated_imap_mailbox",
            ) as imap_resolution, patch.object(
                fetch_archive,
                "connect_mailbox_with_settings",
            ) as connect:
                target = invoke({"mailboxId": MAILBOX_ID})

            self.assertEqual(target.status, status_code)
            self.assertEqual(target.response()["error"]["code"], code)
            authority.assert_called_once()
            gmail_resolution.assert_not_called()
            imap_resolution.assert_not_called()
            connect.assert_not_called()

    def test_unsupported_server_owned_provider_stops_before_provider_calls(self):
        owned = google_owned()
        owned["inbox"]["provider"] = "outlook"
        with patch.object(
            fetch_archive,
            "resolve_owned_mailbox",
            return_value=owned,
        ), patch.object(
            fetch_archive,
            "resolve_gmail_context",
        ) as gmail_resolution, patch.object(
            fetch_archive,
            "resolve_authenticated_imap_mailbox",
        ) as imap_resolution, patch.object(
            fetch_archive,
            "connect_mailbox_with_settings",
        ) as connect:
            target = invoke({"mailboxId": MAILBOX_ID})

        self.assertEqual(target.status, 400)
        self.assertEqual(
            target.response()["error"]["code"],
            "unsupported_provider",
        )
        gmail_resolution.assert_not_called()
        imap_resolution.assert_not_called()
        connect.assert_not_called()

    def test_missing_or_invalid_mailbox_id_stops_before_authority(self):
        invalid_values = (
            None,
            "",
            " mailbox",
            "mailbox\n",
            1,
            True,
            {},
        )
        for mailbox_id in invalid_values:
            payload = {} if mailbox_id is None else {"mailboxId": mailbox_id}
            with self.subTest(mailbox_id=mailbox_id), patch.object(
                fetch_archive,
                "resolve_owned_mailbox",
            ) as authority:
                target = invoke(payload)
            self.assertEqual(target.status, 400)
            self.assertEqual(
                target.response()["error"]["code"],
                "invalid_request",
            )
            authority.assert_not_called()

    def test_provider_credential_folder_target_and_bulk_fields_are_rejected(self):
        forbidden_fields = {
            "provider": "google",
            "email": "forged@example.com",
            "folder": "Archive",
            "archiveFolder": "Archive",
            "targetFolder": "Archive",
            "host": "evil.invalid",
            "port": 993,
            "ssl": True,
            "username": "attacker",
            "password": "secret",
            "accessToken": "access",
            "refreshToken": "refresh",
            "credentialGeneration": 5,
            "connection": {"host": "evil.invalid"},
            "messageId": "provider-message",
            "uid": "7",
            "uidValidity": "91",
            "messageIds": ["one"],
            "uids": ["7"],
            "bulk": True,
            "limit": 10,
        }
        for field, value in forbidden_fields.items():
            with self.subTest(field=field), patch.object(
                fetch_archive,
                "resolve_owned_mailbox",
            ) as authority:
                target = invoke(
                    {
                        "mailboxId": MAILBOX_ID,
                        field: value,
                    }
                )
            self.assertEqual(target.status, 400)
            self.assertEqual(
                target.response()["error"]["code"],
                "invalid_request",
            )
            self.assertNotIn(str(value), json.dumps(target.response()))
            authority.assert_not_called()


class FetchArchiveGmailTests(unittest.TestCase):
    def call_route(self, result: dict | None = None):
        owned = google_owned()
        context = gmail_context()
        with patch.object(
            fetch_archive,
            "resolve_owned_mailbox",
            return_value=owned,
        ) as authority, patch.object(
            fetch_archive,
            "resolve_gmail_context",
            return_value={"status": "ok", "context": context},
        ) as gmail_resolution, patch.object(
            fetch_archive,
            "read_gmail_folder_snapshot",
            return_value=result or gmail_snapshot_result(),
        ) as snapshot_read, patch.object(
            fetch_archive,
            "resolve_authenticated_imap_mailbox",
        ) as imap_resolution:
            target = invoke({"mailboxId": MAILBOX_ID})
        return (
            target,
            owned,
            context,
            authority,
            gmail_resolution,
            snapshot_read,
            imap_resolution,
        )

    def test_uses_server_context_and_exact_strict_archive_snapshot_contract(self):
        (
            target,
            owned,
            context,
            authority,
            gmail_resolution,
            snapshot_read,
            imap_resolution,
        ) = self.call_route()

        self.assertEqual(target.status, 200)
        authority.assert_called_once_with(target.headers, MAILBOX_ID)
        gmail_resolution.assert_called_once_with(owned)
        snapshot_read.assert_called_once_with(
            context,
            provider_folder="Archive",
            request_with_one_refresh=fetch_archive._request_with_one_refresh,
            limit=100,
            focus_preferences=None,
            strict=True,
            required_message_id=None,
        )
        imap_resolution.assert_not_called()
        payload = target.response()
        self.assertEqual(
            set(payload),
            {"ok", "status", "mailboxId", "folder"},
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mailboxId"], MAILBOX_ID)
        self.assertEqual(payload["folder"], gmail_snapshot())
        message = payload["folder"]["messages"][0]
        self.assertEqual(
            message["providerMessageId"],
            "provider-message-0",
        )
        self.assertEqual(
            message["providerThreadId"],
            "provider-thread-0",
        )
        self.assertNotIn("imapUid", message)
        serialized = json.dumps(payload)
        self.assertNotIn(ACCESS_TOKEN, serialized)
        self.assertNotIn(REFRESH_TOKEN, serialized)
        self.assertNotIn(OWNER_EMAIL, serialized)

    def test_missing_label_ids_returns_public_empty_labels(self):
        provider_message_id = "provider-message-without-labels"
        provider_thread_id = "provider-thread-without-labels"
        encoded_raw = gmail_raw_message()
        responses = [
            (
                {"messages": [{"id": provider_message_id}]},
                None,
            ),
            (
                {
                    "id": provider_message_id,
                    "threadId": provider_thread_id,
                    "raw": encoded_raw,
                },
                None,
            ),
        ]

        with patch.object(
            fetch_archive,
            "resolve_owned_mailbox",
            return_value=google_owned(),
        ), patch.object(
            fetch_archive,
            "resolve_gmail_context",
            return_value={
                "status": "ok",
                "context": gmail_context(),
            },
        ), patch.object(
            fetch_archive,
            "_gmail_request",
            side_effect=responses,
        ) as gmail_request, patch.object(
            fetch_archive,
            "refresh_gmail_context",
        ) as refresh, patch.object(
            fetch_archive,
            "resolve_authenticated_imap_mailbox",
        ) as imap_resolution:
            target = invoke({"mailboxId": MAILBOX_ID})

        self.assertEqual(target.status, 200)
        payload = target.response()
        message = payload["folder"]["messages"][0]
        self.assertEqual(message["labelIds"], [])
        self.assertEqual(
            message["providerMessageId"],
            provider_message_id,
        )
        self.assertEqual(
            message["providerThreadId"],
            provider_thread_id,
        )
        self.assertNotIn("raw", message)
        serialized = json.dumps(payload)
        self.assertNotIn(encoded_raw, serialized)
        self.assertNotIn(ACCESS_TOKEN, serialized)
        self.assertNotIn(REFRESH_TOKEN, serialized)
        self.assertNotIn(IMAP_USERNAME, serialized)
        self.assertNotIn(IMAP_PASSWORD, serialized)
        self.assertFalse(
            fetch_archive._contains_forbidden_public_fields(payload)
        )
        self.assertEqual(gmail_request.call_count, 2)
        refresh.assert_not_called()
        imap_resolution.assert_not_called()

    def test_empty_archive_snapshot_is_valid_but_empty_http_body_is_not(self):
        empty_snapshot = gmail_snapshot(message_count=0)
        target, *_ = self.call_route(
            gmail_snapshot_result(empty_snapshot)
        )
        self.assertEqual(target.status, 200)
        self.assertEqual(target.response()["folder"]["messages"], [])

        with patch.object(
            fetch_archive,
            "urlopen",
            return_value=FakeResponse(b""),
        ):
            payload, error = fetch_archive._gmail_request(
                ACCESS_TOKEN,
                "/messages",
            )
        self.assertIsNone(payload)
        self.assertEqual(error, {"code": "gmail_response_invalid"})

        success_payload = fetch_archive._archive_success_payload(
            mailbox_id=MAILBOX_ID,
            snapshot=empty_snapshot,
        )
        exact_size = len(json.dumps(success_payload).encode("utf-8"))
        with patch.object(
            fetch_archive,
            "MAX_ARCHIVE_RESPONSE_BYTES",
            exact_size,
        ):
            target, *_ = self.call_route(
                gmail_snapshot_result(empty_snapshot)
            )
        self.assertEqual(target.status, 200)
        self.assertIn(
            ("Content-Length", str(exact_size)),
            target.response_headers,
        )

        with patch.object(
            fetch_archive,
            "MAX_ARCHIVE_RESPONSE_BYTES",
            exact_size - 1,
        ):
            target, *_ = self.call_route(
                gmail_snapshot_result(empty_snapshot)
            )
        self.assertEqual(target.status, 502)
        self.assertEqual(
            target.response()["error"]["code"],
            "archive_snapshot_failed",
        )

    def test_one_refresh_is_used_once_and_updated_context_is_returned(self):
        original = gmail_context()
        refreshed = {
            **original,
            "access_token": "new-access-token",
            "refresh_attempted": True,
        }
        with patch.object(
            fetch_archive,
            "_gmail_request",
            side_effect=[
                (None, {"code": "gmail_token_invalid"}),
                ({"messages": []}, None),
            ],
        ) as request, patch.object(
            fetch_archive,
            "refresh_gmail_context",
            return_value={"status": "ok", "context": refreshed},
        ) as refresh:
            payload, error, context, refresh_failure = (
                fetch_archive._request_with_one_refresh(
                    original,
                    "/messages",
                )
            )

        self.assertEqual(payload, {"messages": []})
        self.assertIsNone(error)
        self.assertEqual(context, refreshed)
        self.assertIsNone(refresh_failure)
        self.assertEqual(
            request.call_args_list,
            [
                call(ACCESS_TOKEN, "/messages"),
                call("new-access-token", "/messages"),
            ],
        )
        refresh.assert_called_once_with(original)

        already_refreshed = gmail_context(refresh_attempted=True)
        with patch.object(
            fetch_archive,
            "_gmail_request",
            return_value=(None, {"code": "gmail_token_invalid"}),
        ) as request, patch.object(
            fetch_archive,
            "refresh_gmail_context",
        ) as refresh:
            fetch_archive._request_with_one_refresh(
                already_refreshed,
                "/messages",
            )
        request.assert_called_once()
        refresh.assert_not_called()

    def test_provider_and_refresh_failures_use_safe_existing_mapping(self):
        provider_errors = (
            ("gmail_token_invalid", 401, "reconnect_required"),
            ("gmail_permission_denied", 403, "gmail_permission_denied"),
            ("gmail_rate_limited", 502, "gmail_rate_limited"),
            ("gmail_unavailable", 502, "gmail_unavailable"),
            ("gmail_response_invalid", 502, "gmail_response_invalid"),
            (
                "gmail_response_too_large",
                502,
                "gmail_response_too_large",
            ),
            ("provider-secret-error", 502, "gmail_fetch_failed"),
        )
        for internal_code, status, public_code in provider_errors:
            with self.subTest(internal_code=internal_code):
                target, *_ = self.call_route(
                    {
                        "status": "error",
                        "context": gmail_context(),
                        "snapshot": None,
                        "error": {"code": internal_code},
                        "refresh_failure": None,
                    }
                )
                self.assertEqual(target.status, status)
                self.assertEqual(
                    target.response()["error"]["code"],
                    public_code,
                )
                self.assertNotIn(
                    "provider-secret-error",
                    json.dumps(target.response()),
                )

        refresh_failure = {
            "status": "error",
            "context": gmail_context(),
            "snapshot": None,
            "error": {"code": "gmail_token_invalid"},
            "refresh_failure": {
                "status_code": 503,
                "error": {
                    "ok": False,
                    "error": {
                        "code": "gmail_token_store_unavailable",
                        "message": "Gmail authorization storage is temporarily unavailable.",
                    },
                },
            },
        }
        target, *_ = self.call_route(refresh_failure)
        self.assertEqual(target.status, 503)
        self.assertEqual(
            target.response()["error"]["code"],
            "gmail_token_store_unavailable",
        )

    def test_malformed_mismatched_oversized_or_identity_unsafe_snapshot_fails(self):
        valid = gmail_snapshot()
        cases = [
            None,
            {},
            {**valid, "serverMailboxId": "other-mailbox"},
            {**valid, "providerFolder": "Inbox"},
            {**valid, "uidValidity": "wrong"},
            {**valid, "messages": "not-a-list"},
            gmail_snapshot(message_count=101),
            {
                **valid,
                "messages": [
                    {
                        **valid["messages"][0],
                        "imapUid": "7",
                    }
                ],
            },
            {
                **valid,
                "messages": [
                    valid["messages"][0],
                    valid["messages"][0],
                ],
            },
            {
                **valid,
                "messages": [
                    {
                        **valid["messages"][0],
                        "providerThreadId": "",
                    }
                ],
            },
            {
                **valid,
                "messages": [
                    {
                        **valid["messages"][0],
                        "fingerprint": "private",
                    }
                ],
            },
            {
                **valid,
                "messages": [
                    {
                        **valid["messages"][0],
                        "credentialRecord": {
                            "oauthToken": "private",
                        },
                    }
                ],
            },
        ]
        for snapshot in cases:
            with self.subTest(snapshot_type=type(snapshot).__name__):
                target, *_ = self.call_route(
                    {
                        "status": "ok",
                        "context": gmail_context(),
                        "snapshot": snapshot,
                        "error": None,
                        "refresh_failure": None,
                    }
                )
                self.assertEqual(target.status, 502)
                self.assertEqual(
                    target.response()["error"]["code"],
                    "archive_snapshot_failed",
                )
                self.assertNotIn("private", json.dumps(target.response()))

        with patch.object(
            fetch_archive,
            "MAX_ARCHIVE_RESPONSE_BYTES",
            64,
        ):
            target, *_ = self.call_route()
        self.assertEqual(target.status, 502)
        self.assertEqual(
            target.response()["error"]["code"],
            "archive_snapshot_failed",
        )


class FetchArchiveImapTests(unittest.TestCase):
    def call_route(
        self,
        *,
        mailbox: RecordingMailbox | None = None,
        snapshot_result: dict | None = None,
        resolution: dict | None = None,
        connect_error: Exception | None = None,
    ):
        mailbox = mailbox or RecordingMailbox()
        connection = (
            Mock(side_effect=connect_error)
            if connect_error is not None
            else Mock(return_value=mailbox)
        )
        with patch.object(
            fetch_archive,
            "resolve_owned_mailbox",
            return_value=custom_imap_owned(),
        ) as authority, patch.object(
            fetch_archive,
            "resolve_authenticated_imap_mailbox",
            return_value=resolution or resolved_imap_mailbox(),
        ) as imap_resolution, patch.object(
            fetch_archive,
            "connect_mailbox_with_settings",
            connection,
        ) as connect, patch.object(
            fetch_archive,
            "read_imap_folder_snapshot",
            return_value=snapshot_result or imap_snapshot_result(),
        ) as snapshot_read, patch.object(
            fetch_archive,
            "resolve_gmail_context",
        ) as gmail_resolution:
            target = invoke({"mailboxId": MAILBOX_ID})
        return (
            target,
            mailbox,
            authority,
            imap_resolution,
            connect,
            snapshot_read,
            gmail_resolution,
        )

    def test_server_credentials_unique_special_use_and_readonly_snapshot(self):
        (
            target,
            mailbox,
            authority,
            imap_resolution,
            connect,
            snapshot_read,
            gmail_resolution,
        ) = self.call_route()

        self.assertEqual(target.status, 200)
        authority.assert_called_once_with(target.headers, MAILBOX_ID)
        imap_resolution.assert_called_once_with(target.headers, MAILBOX_ID)
        gmail_resolution.assert_not_called()
        connect.assert_called_once_with(
            host=IMAP_HOST,
            port=993,
            username=IMAP_USERNAME,
            password=IMAP_PASSWORD,
            ssl_enabled=True,
        )
        self.assertEqual(mailbox.list_count, 1)
        snapshot_read.assert_called_once_with(
            mailbox,
            folder=ARCHIVE_FOLDER,
            mailbox_key=MAILBOX_ID,
            email_address=MAILBOX_EMAIL,
            limit=100,
            readonly=True,
        )
        self.assertEqual(mailbox.unsafe_calls, [])
        self.assertEqual(mailbox.logout_count, 1)
        self.assertEqual(mailbox.shutdown_count, 0)

        payload = target.response()
        self.assertEqual(
            set(payload),
            {"ok", "status", "mailboxId", "folder"},
        )
        self.assertEqual(payload["folder"], imap_snapshot())
        self.assertNotIn("identities", payload["folder"])
        serialized = json.dumps(payload)
        for secret in (
            IMAP_HOST,
            IMAP_USERNAME,
            IMAP_PASSWORD,
            "internal-fingerprint-never-return",
        ):
            self.assertNotIn(secret, serialized)

    def test_name_only_fallback_none_and_multiple_archive_roles_fail_closed(self):
        discovery_cases = (
            (
                (
                    "OK",
                    [b'(\\HasNoChildren) "/" "Archive"'],
                ),
                "archive_folder_unavailable",
            ),
            (
                (
                    "OK",
                    [],
                ),
                "archive_folder_unavailable",
            ),
            (
                (
                    "OK",
                    [
                        b'(\\Archive) "/" "First"',
                        b'(\\Archive) "/" "Second"',
                    ],
                ),
                "archive_folder_ambiguous",
            ),
        )
        for list_response, expected_code in discovery_cases:
            mailbox = RecordingMailbox(list_response=list_response)
            with self.subTest(expected_code=expected_code):
                (
                    target,
                    mailbox,
                    _authority,
                    _imap_resolution,
                    _connect,
                    snapshot_read,
                    _gmail_resolution,
                ) = self.call_route(mailbox=mailbox)
                self.assertEqual(target.status, 409)
                self.assertEqual(
                    target.response()["error"]["code"],
                    expected_code,
                )
                snapshot_read.assert_not_called()
                self.assertEqual(mailbox.list_count, 1)
                self.assertEqual(mailbox.unsafe_calls, [])
                self.assertEqual(mailbox.logout_count, 1)

    def test_logout_failure_uses_shutdown_without_changing_response(self):
        mailbox = RecordingMailbox(
            logout_error=RuntimeError("logout provider detail"),
        )
        target, mailbox, *_ = self.call_route(mailbox=mailbox)
        self.assertEqual(target.status, 200)
        self.assertEqual(mailbox.logout_count, 1)
        self.assertEqual(mailbox.shutdown_count, 1)
        self.assertNotIn(
            "logout provider detail",
            json.dumps(target.response()),
        )

    def test_connection_and_authentication_failures_are_safe(self):
        for error, expected_status, expected_code in (
            (
                fetch_archive.imaplib.IMAP4.error(
                    f"bad {IMAP_PASSWORD}"
                ),
                401,
                "invalid_credentials",
            ),
            (
                RuntimeError(
                    f"{IMAP_HOST} {IMAP_USERNAME} {IMAP_PASSWORD}"
                ),
                502,
                "imap_connection_failed",
            ),
        ):
            with self.subTest(expected_code=expected_code):
                target, mailbox, *_ = self.call_route(
                    connect_error=error,
                )
                self.assertEqual(target.status, expected_status)
                self.assertEqual(
                    target.response()["error"]["code"],
                    expected_code,
                )
                serialized = json.dumps(target.response())
                self.assertNotIn(IMAP_HOST, serialized)
                self.assertNotIn(IMAP_USERNAME, serialized)
                self.assertNotIn(IMAP_PASSWORD, serialized)
                self.assertEqual(mailbox.logout_count, 0)

    def test_resolver_failure_stops_before_connection(self):
        resolution = {
            "status": "reconnect_required",
            "mailbox": None,
            "error": {
                "code": "reconnect_required",
                "message": "Reconnect this mailbox to continue.",
                "status_code": 409,
            },
        }
        (
            target,
            mailbox,
            _authority,
            _imap_resolution,
            connect,
            snapshot_read,
            _gmail_resolution,
        ) = self.call_route(
            resolution=resolution,
        )
        self.assertEqual(target.status, 409)
        self.assertEqual(
            target.response()["error"]["code"],
            "reconnect_required",
        )
        connect.assert_not_called()
        snapshot_read.assert_not_called()
        self.assertEqual(mailbox.list_count, 0)

    def test_snapshot_error_mismatch_malformed_and_oversize_fail_safely(self):
        valid = imap_snapshot()
        cases = [
            {
                "ok": False,
                "status": "error",
                "snapshot": None,
                "identities": {},
                "error": {
                    "code": "provider-secret",
                    "message": IMAP_PASSWORD,
                },
            },
            imap_snapshot_result({}),
            imap_snapshot_result(
                {**valid, "serverMailboxId": "other-mailbox"}
            ),
            imap_snapshot_result(
                {**valid, "providerFolder": "Archive"}
            ),
            imap_snapshot_result({**valid, "uidValidity": "091"}),
            imap_snapshot_result({**valid, "imapUidSet": ["07"]}),
            imap_snapshot_result({**valid, "imapUidSet": ["9", "7"]}),
            imap_snapshot_result(
                {
                    **valid,
                    "messages": [
                        {
                            **valid["messages"][0],
                            "providerFolder": "Archive",
                        }
                    ],
                }
            ),
            imap_snapshot_result(
                {
                    **valid,
                    "messages": [
                        {
                            **valid["messages"][0],
                            "rawProviderResponse": {
                                "imapPassword": "private",
                            },
                        }
                    ],
                }
            ),
            imap_snapshot_result(
                {
                    **valid,
                    "messages": [
                        {
                            **valid["messages"][0],
                            "fingerprint": "private",
                        }
                    ],
                }
            ),
            imap_snapshot_result(
                {
                    **valid,
                    "messages": [
                        valid["messages"][0]
                        for _ in range(101)
                    ],
                }
            ),
            imap_snapshot_result(
                {
                    **valid,
                    "messages": [
                        valid["messages"][0],
                        valid["messages"][0],
                    ],
                }
            ),
        ]
        for result in cases:
            with self.subTest(result_status=result.get("status")):
                target, mailbox, *_ = self.call_route(
                    snapshot_result=result,
                )
                self.assertEqual(target.status, 502)
                self.assertEqual(
                    target.response()["error"]["code"],
                    "archive_snapshot_failed",
                )
                serialized = json.dumps(target.response())
                self.assertNotIn(IMAP_PASSWORD, serialized)
                self.assertNotIn("provider-secret", serialized)
                self.assertNotIn("private", serialized)
                self.assertEqual(mailbox.logout_count, 1)

        with patch.object(
            fetch_archive,
            "MAX_ARCHIVE_RESPONSE_BYTES",
            64,
        ):
            target, mailbox, *_ = self.call_route()
        self.assertEqual(target.status, 502)
        self.assertEqual(
            target.response()["error"]["code"],
            "archive_snapshot_failed",
        )
        self.assertEqual(mailbox.logout_count, 1)

    def test_route_source_has_no_archive_mutator_or_unsafe_imap_commands(self):
        source = (CURRENT_DIR / "fetch-archive.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "archive_imap_message",
            '"MOVE"',
            '"COPY"',
            '"STORE"',
            '"EXPUNGE"',
        ):
            self.assertNotIn(forbidden, source)


class FetchArchiveContractTests(unittest.TestCase):
    def test_methods_are_post_only_and_cache_is_never_stored(self):
        for method in ("do_GET", "do_PUT", "do_PATCH", "do_DELETE"):
            with self.subTest(method=method):
                target = FakeHandler({})
                target.command = method.removeprefix("do_")
                getattr(fetch_archive.handler, method)(target)
                self.assertEqual(target.status, 405)
                self.assertEqual(
                    target.response()["error"]["code"],
                    "method_not_allowed",
                )
                self.assertIn(
                    ("Cache-Control", "no-store"),
                    target.response_headers,
                )

        target = FakeHandler({})
        target.command = "HEAD"
        fetch_archive.handler.do_HEAD(target)
        self.assertEqual(target.status, 405)
        self.assertEqual(target.wfile.getvalue(), b"")

    def test_outer_exception_is_sanitized_and_has_no_mutation_status(self):
        target = FakeHandler({"mailboxId": MAILBOX_ID})
        with patch.object(
            fetch_archive.handler,
            "_handle_post",
            side_effect=RuntimeError(
                f"{ACCESS_TOKEN} {IMAP_PASSWORD}"
            ),
        ):
            fetch_archive.handler.do_POST(target)
        self.assertEqual(target.status, 500)
        payload = target.response()
        self.assertEqual(payload["error"]["code"], "internal_error")
        serialized = json.dumps(payload)
        self.assertNotIn(ACCESS_TOKEN, serialized)
        self.assertNotIn(IMAP_PASSWORD, serialized)
        self.assertNotIn(
            "mutation_confirmed_readback_failed",
            serialized,
        )


if __name__ == "__main__":
    unittest.main()
