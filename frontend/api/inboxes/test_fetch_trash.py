from __future__ import annotations

import base64
import importlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch
from urllib.parse import parse_qs, urlsplit


CURRENT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = CURRENT_DIR.parent.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))


fetch_trash = importlib.import_module("api.inboxes.fetch-trash")
gmail_snapshot = importlib.import_module("api.inboxes.gmail_snapshot")


MAILBOX_ID = "server-mailbox"
MESSAGE_ID = "18f-provider-message"
THREAD_ID = "18f-provider-thread"
ACCESS_TOKEN = "test-only-trash-token-never-return"
IMAP_HOST = "imap.test.invalid"
IMAP_USERNAME = "server-imap-user"
IMAP_PASSWORD = "test-only-imap-password-never-return"
IMAP_EMAIL = "owned@imap.test"
TRASH_FOLDER = 'Deleted "Items"\\2024'


class FakeHandler:
    def __init__(self, payload: object = None, *, raw_body: bytes | None = None):
        body = (
            raw_body
            if raw_body is not None
            else json.dumps({} if payload is None else payload).encode("utf-8")
        )
        self.headers = {"content-length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status = None
        self.response_headers: list[tuple[str, str]] = []
        self.command = "POST"

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        pass

    def do_GET(self):
        fetch_trash.handler.do_GET(self)

    def response(self) -> dict:
        return json.loads(self.wfile.getvalue())


class FakeResponse:
    def __init__(self, body: bytes | str):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.headers: dict[str, str] = {}
        self._stream = io.BytesIO(self.body)

    def read(self, amount=-1):
        return self._stream.read(amount)

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False


class RecordingMailbox:
    def __init__(self, *, logout_error: Exception | None = None):
        self.logout_error = logout_error
        self.logout_count = 0
        self.shutdown_count = 0
        self.unsafe_calls: list[tuple] = []

    def logout(self):
        self.logout_count += 1
        if self.logout_error is not None:
            raise self.logout_error
        return "BYE", []

    def shutdown(self):
        self.shutdown_count += 1

    def uid(self, *arguments):
        self.unsafe_calls.append(("uid", *arguments))
        raise AssertionError("the route must delegate read-only snapshot work")

    def copy(self, *arguments):
        self.unsafe_calls.append(("copy", *arguments))
        raise AssertionError("Trash fetch must not COPY")

    def store(self, *arguments):
        self.unsafe_calls.append(("store", *arguments))
        raise AssertionError("Trash fetch must not STORE")

    def expunge(self, *arguments):
        self.unsafe_calls.append(("expunge", *arguments))
        raise AssertionError("Trash fetch must not EXPUNGE")


def gmail_context(*, refresh_attempted: bool = False) -> dict:
    return {
        "mailbox_id": MAILBOX_ID,
        "mailbox_email": "owned@gmail.test",
        "owner_email": "owner@example.test",
        "access_token": ACCESS_TOKEN,
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
        "refresh_attempted": refresh_attempted,
    }


def google_owned() -> dict:
    return {
        "status": "ok",
        "user": {"email": "owner@example.test"},
        "inbox": {
            "id": MAILBOX_ID,
            "email": "owned@gmail.test",
            "provider": "google",
        },
    }


def custom_imap_owned() -> dict:
    return {
        "status": "ok",
        "user": {"email": "owner@example.test"},
        "inbox": {
            "id": MAILBOX_ID,
            "email": IMAP_EMAIL,
            "provider": "custom_imap",
        },
    }


def resolved_imap_mailbox() -> dict:
    return {
        "status": "ok",
        "mailbox": {
            "mailboxId": MAILBOX_ID,
            "ownerEmail": "owner@example.test",
            "email": IMAP_EMAIL,
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


def raw_message() -> str:
    raw = (
        b"Message-Id: <trash-message@example.test>\r\n"
        b"From: sender@example.test\r\n"
        b"To: owned@gmail.test\r\n"
        b"Subject: Trashed message\r\n"
        b"\r\n"
        b"Trash body"
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def gmail_detail(*, label_ids=None, **overrides) -> dict:
    return {
        "id": MESSAGE_ID,
        "threadId": THREAD_ID,
        "labelIds": ["TRASH", "STARRED"] if label_ids is None else label_ids,
        "raw": raw_message(),
        **overrides,
    }


def trash_message(message_id: str = MESSAGE_ID, **overrides) -> dict:
    return {
        "id": "trash-message@example.test",
        "sender": "Sender",
        "subject": "Trashed message",
        "serverMailboxId": MAILBOX_ID,
        "providerFolder": "Trash",
        "providerMessageId": message_id,
        "providerThreadId": THREAD_ID,
        "labelIds": ["TRASH", "STARRED"],
        **overrides,
    }


def trash_snapshot(*, messages: list[dict] | None = None, **overrides) -> dict:
    return {
        "serverMailboxId": MAILBOX_ID,
        "providerFolder": "Trash",
        "messages": [trash_message()] if messages is None else messages,
        "uidValidity": "gmail-api",
        **overrides,
    }


def imap_trash_message(uid: str = "7", **overrides) -> dict:
    return {
        "id": "trash-message@example.test",
        "sender": "Sender",
        "subject": "Trashed message",
        "threadId": "imap:rfc:trash-message@example.test",
        "serverMailboxId": MAILBOX_ID,
        "providerFolder": TRASH_FOLDER,
        "uidValidity": "91",
        "imapUid": uid,
        "rfcMessageId": "trash-message@example.test",
        **overrides,
    }


def imap_trash_snapshot(
    *,
    messages: list[dict] | None = None,
    **overrides,
) -> dict:
    return {
        "serverMailboxId": MAILBOX_ID,
        "providerFolder": TRASH_FOLDER,
        "uidValidity": "91",
        "imapUidSet": ["7"],
        "messages": (
            [imap_trash_message()]
            if messages is None
            else messages
        ),
        **overrides,
    }


def imap_snapshot_result(snapshot: object = None) -> dict:
    effective_snapshot = (
        imap_trash_snapshot()
        if snapshot is None
        else snapshot
    )
    return {
        "ok": True,
        "status": "ok",
        "snapshot": effective_snapshot,
        "identities": {
            "7": {
                "providerFolder": TRASH_FOLDER,
                "imapUid": "7",
                "uidValidity": "91",
                "rfcMessageId": "trash-message@example.test",
                "fingerprint": "internal-fingerprint-never-return",
            }
        },
        "error": None,
    }


_DEFAULT_SNAPSHOT = object()


def snapshot_result(snapshot: object = _DEFAULT_SNAPSHOT) -> dict:
    return {
        "status": "ok",
        "context": gmail_context(),
        "snapshot": (
            trash_snapshot()
            if snapshot is _DEFAULT_SNAPSHOT
            else snapshot
        ),
        "error": None,
        "refresh_failure": None,
    }


def invoke_route(
    payload: object = None,
    *,
    resolution: dict | None = None,
    gmail_resolution_result: dict | None = None,
    result: dict | None = None,
):
    target = FakeHandler({"mailboxId": MAILBOX_ID} if payload is None else payload)
    authority = Mock(
        return_value=resolution
        or google_owned()
    )
    gmail_resolution = Mock(
        return_value=(
            gmail_resolution_result
            or {"status": "ok", "context": gmail_context()}
        )
    )
    snapshot_read = Mock(return_value=result or snapshot_result())
    imap_resolution = Mock(
        side_effect=AssertionError("Gmail must not resolve custom IMAP")
    )
    imap_connect = Mock(
        side_effect=AssertionError("Gmail must not connect custom IMAP")
    )
    with patch.object(
        fetch_trash,
        "resolve_owned_mailbox",
        authority,
    ), patch.object(
        fetch_trash,
        "resolve_gmail_context",
        gmail_resolution,
    ), patch.object(
        fetch_trash,
        "read_gmail_folder_snapshot",
        snapshot_read,
    ), patch.object(
        fetch_trash,
        "resolve_authenticated_imap_mailbox",
        imap_resolution,
    ), patch.object(
        fetch_trash,
        "connect_mailbox_with_settings",
        imap_connect,
    ):
        fetch_trash.handler.do_POST(target)
    authority.gmail_resolution = gmail_resolution
    authority.imap_resolution = imap_resolution
    authority.imap_connect = imap_connect
    return target, authority, snapshot_read


_DEFAULT_IMAP_RESULT = object()


def invoke_imap_route(
    *,
    payload: object = None,
    owned_result: dict | None = None,
    resolution: dict | None = None,
    mailbox: RecordingMailbox | None = None,
    discovery_result: tuple[str | None, str | None] = (
        TRASH_FOLDER,
        None,
    ),
    result: object = _DEFAULT_IMAP_RESULT,
    connect_error: Exception | None = None,
    discovery_error: Exception | None = None,
    snapshot_error: Exception | None = None,
):
    target = FakeHandler(
        {"mailboxId": MAILBOX_ID}
        if payload is None
        else payload
    )
    mailbox = mailbox or RecordingMailbox()
    authority = Mock(return_value=owned_result or custom_imap_owned())
    imap_resolution = Mock(
        return_value=resolution or resolved_imap_mailbox()
    )
    connection = (
        Mock(side_effect=connect_error)
        if connect_error is not None
        else Mock(return_value=mailbox)
    )
    discovery = Mock(
        side_effect=discovery_error,
        return_value=discovery_result,
    )
    snapshot_read = Mock(
        side_effect=snapshot_error,
        return_value=(
            imap_snapshot_result()
            if result is _DEFAULT_IMAP_RESULT
            else result
        ),
    )
    gmail_resolution = Mock(
        side_effect=AssertionError("custom IMAP must not resolve Gmail")
    )
    gmail_snapshot_read = Mock(
        side_effect=AssertionError("custom IMAP must not read Gmail")
    )
    with patch.object(
        fetch_trash,
        "resolve_owned_mailbox",
        authority,
    ), patch.object(
        fetch_trash,
        "resolve_authenticated_imap_mailbox",
        imap_resolution,
    ), patch.object(
        fetch_trash,
        "connect_mailbox_with_settings",
        connection,
    ), patch.object(
        fetch_trash.imap_trash,
        "discover_trash_folder",
        discovery,
    ), patch.object(
        fetch_trash,
        "read_imap_folder_snapshot",
        snapshot_read,
    ), patch.object(
        fetch_trash,
        "resolve_gmail_context",
        gmail_resolution,
    ), patch.object(
        fetch_trash,
        "read_gmail_folder_snapshot",
        gmail_snapshot_read,
    ):
        fetch_trash.handler.do_POST(target)
    return {
        "handler": target,
        "mailbox": mailbox,
        "authority": authority,
        "imap_resolution": imap_resolution,
        "connect": connection,
        "discovery": discovery,
        "snapshot": snapshot_read,
        "gmail_resolution": gmail_resolution,
        "gmail_snapshot": gmail_snapshot_read,
    }


class GmailTrashSnapshotPrimitiveTests(unittest.TestCase):
    def test_uses_trash_label_include_spam_trash_and_strict_detail(self):
        paths: list[str] = []
        responses = [
            ({"messages": [{"id": MESSAGE_ID}]}, None),
            (gmail_detail(), None),
        ]

        def request(context, path):
            paths.append(path)
            payload, error = responses.pop(0)
            return payload, error, context, None

        result = gmail_snapshot.read_gmail_folder_snapshot(
            gmail_context(),
            provider_folder="Trash",
            request_with_one_refresh=request,
            limit=100,
            focus_preferences=None,
            strict=True,
        )

        self.assertEqual(result["status"], "ok")
        snapshot = result["snapshot"]
        self.assertEqual(snapshot["providerFolder"], "Trash")
        self.assertEqual(snapshot["serverMailboxId"], MAILBOX_ID)
        self.assertEqual(snapshot["uidValidity"], "gmail-api")
        self.assertEqual(len(snapshot["messages"]), 1)
        message = snapshot["messages"][0]
        self.assertEqual(message["providerMessageId"], MESSAGE_ID)
        self.assertEqual(message["providerThreadId"], THREAD_ID)
        self.assertEqual(message["providerFolder"], "Trash")
        self.assertNotIn("imapUid", message)

        list_query = parse_qs(urlsplit(paths[0]).query)
        self.assertEqual(
            list_query,
            {
                "labelIds": ["TRASH"],
                "includeSpamTrash": ["true"],
                "maxResults": ["100"],
            },
        )
        self.assertNotIn("q", list_query)
        self.assertEqual(
            paths[1],
            f"/messages/{MESSAGE_ID}?format=raw",
        )

    def test_strict_trash_rejects_missing_malformed_or_source_labels(self):
        invalid_details = (
            {
                "id": MESSAGE_ID,
                "threadId": THREAD_ID,
                "raw": raw_message(),
            },
            gmail_detail(label_ids=[]),
            gmail_detail(label_ids=["INBOX"]),
            gmail_detail(label_ids=["INBOX", "TRASH"]),
            gmail_detail(label_ids="TRASH"),
            gmail_detail(label_ids=["TRASH", "TRASH"]),
            gmail_detail(label_ids=["TRASH", 1]),
        )
        for detail in invalid_details:
            with self.subTest(label_ids=detail.get("labelIds")):
                responses = [
                    ({"messages": [{"id": MESSAGE_ID}]}, None),
                    (detail, None),
                ]

                def request(context, _path):
                    payload, error = responses.pop(0)
                    return payload, error, context, None

                result = gmail_snapshot.read_gmail_folder_snapshot(
                    gmail_context(),
                    provider_folder="Trash",
                    request_with_one_refresh=request,
                    strict=True,
                )
                self.assertEqual(result["status"], "error")
                self.assertEqual(
                    result["error"]["code"],
                    "gmail_response_invalid",
                )

    def test_trash_does_not_gain_archive_targeted_read_semantics(self):
        request = Mock()
        result = gmail_snapshot.read_gmail_folder_snapshot(
            gmail_context(),
            provider_folder="Trash",
            request_with_one_refresh=request,
            required_message_id=MESSAGE_ID,
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(
            result["error"]["code"],
            "gmail_snapshot_invalid_request",
        )
        request.assert_not_called()

    def test_existing_inbox_and_archive_list_paths_are_unchanged(self):
        inbox_query = parse_qs(
            urlsplit(gmail_snapshot._list_path("Inbox", 17)).query
        )
        archive_query = parse_qs(
            urlsplit(gmail_snapshot._list_path("Archive", 19)).query
        )

        self.assertEqual(
            inbox_query,
            {"labelIds": ["INBOX"], "maxResults": ["17"]},
        )
        self.assertEqual(
            archive_query,
            {
                "q": [gmail_snapshot.GMAIL_ARCHIVE_QUERY],
                "maxResults": ["19"],
            },
        )


class FetchTrashRouteTests(unittest.TestCase):
    def test_exact_request_snapshot_call_and_response_contract(self):
        target, authority, snapshot_read = invoke_route()

        self.assertEqual(target.status, 200)
        authority.assert_called_once_with(target.headers, MAILBOX_ID)
        authority.gmail_resolution.assert_called_once_with(google_owned())
        authority.imap_resolution.assert_not_called()
        authority.imap_connect.assert_not_called()
        snapshot_read.assert_called_once_with(
            gmail_context(),
            provider_folder="Trash",
            request_with_one_refresh=fetch_trash._request_with_one_refresh,
            limit=100,
            focus_preferences=None,
            strict=True,
            message_parser=fetch_trash.message_from_bytes,
        )
        payload = target.response()
        self.assertEqual(
            set(payload),
            {"ok", "status", "mailboxId", "folder"},
        )
        self.assertEqual(
            payload,
            {
                "ok": True,
                "status": "ok",
                "mailboxId": MAILBOX_ID,
                "folder": trash_snapshot(),
            },
        )
        self.assertIn(
            ("Cache-Control", "no-store"),
            target.response_headers,
        )
        serialized = json.dumps(payload)
        self.assertNotIn(ACCESS_TOKEN, serialized)
        self.assertNotIn("owner@example.test", serialized)

    def test_request_is_mailbox_id_only_and_fails_before_authority(self):
        invalid_payloads = (
            {},
            {"mailboxId": ""},
            {"mailboxId": MAILBOX_ID, "limit": 1},
            {"mailboxId": MAILBOX_ID, "accessToken": "private"},
            ["not", "an", "object"],
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                target, authority, snapshot_read = invoke_route(payload)
                self.assertEqual(target.status, 400)
                self.assertEqual(
                    target.response()["error"]["code"],
                    "invalid_request",
                )
                authority.assert_not_called()
                snapshot_read.assert_not_called()

    def test_authority_failure_wins_before_snapshot_read(self):
        error = fetch_trash.error_payload(
            "unsupported_provider",
            "This mailbox is not a Gmail connection.",
        )
        target, authority, snapshot_read = invoke_route(
            resolution={
                "status": "error",
                "status_code": 400,
                "error": error,
            }
        )

        self.assertEqual(target.status, 400)
        self.assertEqual(target.response(), error)
        authority.assert_called_once()
        snapshot_read.assert_not_called()

    def test_gmail_context_and_unsupported_provider_errors_remain_exact(self):
        gmail_error = fetch_trash.error_payload(
            "gmail_connection_not_ready",
            "Gmail connection is not ready.",
        )
        target, authority, snapshot_read = invoke_route(
            gmail_resolution_result={
                "status": "error",
                "status_code": 409,
                "error": gmail_error,
            }
        )
        self.assertEqual(target.status, 409)
        self.assertEqual(target.response(), gmail_error)
        authority.gmail_resolution.assert_called_once_with(google_owned())
        snapshot_read.assert_not_called()

        unsupported_owned = google_owned()
        unsupported_owned["inbox"]["provider"] = "outlook"
        target, authority, snapshot_read = invoke_route(
            resolution=unsupported_owned,
        )
        self.assertEqual(target.status, 400)
        self.assertEqual(
            target.response(),
            fetch_trash.error_payload(
                "unsupported_provider",
                "This mailbox is not a Gmail connection.",
            ),
        )
        authority.gmail_resolution.assert_not_called()
        authority.imap_resolution.assert_not_called()
        snapshot_read.assert_not_called()

    def test_provider_and_refresh_failures_use_safe_errors(self):
        provider_failures = (
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
            ("raw-provider-error", 502, "gmail_fetch_failed"),
        )
        for internal_code, status, public_code in provider_failures:
            with self.subTest(internal_code=internal_code):
                target, _, _ = invoke_route(
                    result={
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
                    "raw-provider-error",
                    json.dumps(target.response()),
                )

        refresh_error = fetch_trash.error_payload(
            "gmail_token_store_unavailable",
            "Gmail authorization storage is temporarily unavailable.",
        )
        target, _, _ = invoke_route(
            result={
                "status": "error",
                "context": gmail_context(),
                "snapshot": None,
                "error": {"code": "gmail_token_invalid"},
                "refresh_failure": {
                    "status_code": 503,
                    "error": refresh_error,
                },
            }
        )
        self.assertEqual(target.status, 503)
        self.assertEqual(target.response(), refresh_error)

    def test_malformed_or_private_snapshot_fails_closed(self):
        valid = trash_snapshot()
        invalid_snapshots = (
            None,
            {},
            {**valid, "serverMailboxId": "other-mailbox"},
            {**valid, "providerFolder": "Inbox"},
            {**valid, "uidValidity": "other-validity"},
            {**valid, "messages": "not-a-list"},
            {
                **valid,
                "messages": [trash_message(), trash_message()],
            },
            {
                **valid,
                "messages": [trash_message(labelIds=["INBOX", "TRASH"])],
            },
            {
                **valid,
                "messages": [trash_message(imapUid="7")],
            },
            {
                **valid,
                "messages": [trash_message(raw="private-provider-body")],
            },
            {
                **valid,
                "messages": [trash_message(accessToken="private-token")],
            },
        )
        for snapshot in invalid_snapshots:
            with self.subTest(snapshot_type=type(snapshot).__name__):
                target, _, _ = invoke_route(result=snapshot_result(snapshot))
                self.assertEqual(target.status, 502)
                self.assertEqual(
                    target.response()["error"]["code"],
                    "trash_snapshot_failed",
                )
                response_text = json.dumps(target.response())
                self.assertNotIn("private-provider-body", response_text)
                self.assertNotIn("private-token", response_text)

    def test_outer_exception_is_sanitized(self):
        target = FakeHandler({"mailboxId": MAILBOX_ID})
        with patch.object(
            fetch_trash.handler,
            "_handle_post",
            side_effect=RuntimeError(f"raw {ACCESS_TOKEN}"),
        ):
            fetch_trash.handler.do_POST(target)

        self.assertEqual(target.status, 500)
        self.assertEqual(
            target.response()["error"]["code"],
            "internal_error",
        )
        self.assertNotIn(ACCESS_TOKEN, json.dumps(target.response()))


class FetchTrashCustomImapTests(unittest.TestCase):
    def test_owned_server_credentials_exact_folder_and_readonly_snapshot(self):
        result = invoke_imap_route()

        target = result["handler"]
        self.assertEqual(target.status, 200)
        result["authority"].assert_called_once_with(
            target.headers,
            MAILBOX_ID,
        )
        result["imap_resolution"].assert_called_once_with(
            target.headers,
            MAILBOX_ID,
        )
        result["connect"].assert_called_once_with(
            host=IMAP_HOST,
            port=993,
            username=IMAP_USERNAME,
            password=IMAP_PASSWORD,
            ssl_enabled=True,
        )
        result["discovery"].assert_called_once_with(result["mailbox"])
        result["snapshot"].assert_called_once_with(
            result["mailbox"],
            folder=TRASH_FOLDER,
            mailbox_key=MAILBOX_ID,
            email_address=IMAP_EMAIL,
            limit=100,
            readonly=True,
        )
        result["gmail_resolution"].assert_not_called()
        result["gmail_snapshot"].assert_not_called()
        self.assertEqual(result["mailbox"].logout_count, 1)
        self.assertEqual(result["mailbox"].shutdown_count, 0)
        self.assertEqual(result["mailbox"].unsafe_calls, [])

        payload = target.response()
        self.assertEqual(
            set(payload),
            {"ok", "status", "provider", "mailboxId", "folder"},
        )
        self.assertEqual(
            payload,
            {
                "ok": True,
                "status": "ok",
                "provider": "custom_imap",
                "mailboxId": MAILBOX_ID,
                "folder": imap_trash_snapshot(),
            },
        )
        self.assertIn(
            ("Cache-Control", "no-store"),
            target.response_headers,
        )
        serialized = json.dumps(payload)
        for private_value in (
            IMAP_HOST,
            IMAP_USERNAME,
            IMAP_PASSWORD,
            "owner@example.test",
            "internal-fingerprint-never-return",
            "identities",
        ):
            self.assertNotIn(private_value, serialized)

    def test_rfc_message_id_is_optional_but_provider_row_scope_is_required(self):
        without_rfc = imap_trash_message()
        without_rfc.pop("rfcMessageId")
        result = invoke_imap_route(
            result=imap_snapshot_result(
                imap_trash_snapshot(messages=[without_rfc])
            )
        )

        self.assertEqual(result["handler"].status, 200)
        public_message = result["handler"].response()["folder"]["messages"][0]
        self.assertNotIn("rfcMessageId", public_message)
        for key in (
            "serverMailboxId",
            "providerFolder",
            "uidValidity",
            "imapUid",
        ):
            self.assertIn(key, public_message)

    def test_custom_imap_rows_reject_gmail_provider_fields(self):
        gmail_fields = {
            "provider": "gmail",
            "providerMessageId": MESSAGE_ID,
            "providerThreadId": THREAD_ID,
            "labelIds": ["TRASH"],
        }
        for field, value in gmail_fields.items():
            with self.subTest(field=field):
                result = invoke_imap_route(
                    result=imap_snapshot_result(
                        imap_trash_snapshot(
                            messages=[imap_trash_message(**{field: value})]
                        )
                    )
                )

                self.assertEqual(result["handler"].status, 502)
                self.assertEqual(
                    result["handler"].response()["error"]["code"],
                    "trash_snapshot_failed",
                )

    def test_custom_imap_rows_match_complete_latest_uid_window_in_order(self):
        imap_uid_set = [
            str(uid)
            for uid in range(1, fetch_trash.TRASH_FETCH_LIMIT + 2)
        ]
        expected_message_uids = list(
            reversed(imap_uid_set[-fetch_trash.TRASH_FETCH_LIMIT:])
        )
        valid_snapshot = imap_trash_snapshot(
            imapUidSet=imap_uid_set,
            messages=[
                imap_trash_message(uid=uid)
                for uid in expected_message_uids
            ],
        )

        accepted = invoke_imap_route(
            result=imap_snapshot_result(valid_snapshot)
        )
        self.assertEqual(accepted["handler"].status, 200)

        reordered_message_uids = expected_message_uids.copy()
        reordered_message_uids[0], reordered_message_uids[1] = (
            reordered_message_uids[1],
            reordered_message_uids[0],
        )
        invalid_message_uid_lists = (
            expected_message_uids[:-1],
            reordered_message_uids,
        )
        for message_uids in invalid_message_uid_lists:
            with self.subTest(message_uids=message_uids[:3]):
                rejected = invoke_imap_route(
                    result=imap_snapshot_result(
                        imap_trash_snapshot(
                            imapUidSet=imap_uid_set,
                            messages=[
                                imap_trash_message(uid=uid)
                                for uid in message_uids
                            ],
                        )
                    )
                )
                self.assertEqual(rejected["handler"].status, 502)
                self.assertEqual(
                    rejected["handler"].response()["error"]["code"],
                    "trash_snapshot_failed",
                )

    def test_unavailable_and_ambiguous_trash_roles_keep_exact_wire_codes(self):
        for code in (
            "trash_folder_unavailable",
            "trash_folder_ambiguous",
        ):
            with self.subTest(code=code):
                result = invoke_imap_route(
                    discovery_result=(None, code),
                )
                target = result["handler"]
                self.assertEqual(target.status, 409)
                self.assertEqual(target.response()["error"]["code"], code)
                result["snapshot"].assert_not_called()
                self.assertEqual(result["mailbox"].logout_count, 1)
                serialized = json.dumps(target.response())
                self.assertNotIn(IMAP_PASSWORD, serialized)
                self.assertNotIn(IMAP_HOST, serialized)

    def test_authority_and_imap_resolution_fail_before_provider_connection(self):
        authority_failures = (
            (401, "unauthorized"),
            (404, "gmail_connection_not_found"),
        )
        for status_code, code in authority_failures:
            with self.subTest(stage="ownership", code=code):
                result = invoke_imap_route(
                    owned_result={
                        "status": "error",
                        "status_code": status_code,
                        "error": fetch_trash.error_payload(
                            code,
                            "safe ownership failure",
                        ),
                    }
                )
                self.assertEqual(result["handler"].status, status_code)
                result["imap_resolution"].assert_not_called()
                result["connect"].assert_not_called()
                result["discovery"].assert_not_called()
                result["snapshot"].assert_not_called()

        result = invoke_imap_route(
            resolution={
                "status": "reconnect_required",
                "mailbox": None,
                "error": {
                    "code": "reconnect_required",
                    "message": f"raw resolver {IMAP_PASSWORD}",
                    "status_code": 409,
                },
            }
        )
        self.assertEqual(result["handler"].status, 409)
        self.assertEqual(
            result["handler"].response()["error"]["code"],
            "reconnect_required",
        )
        result["connect"].assert_not_called()
        self.assertNotIn(
            IMAP_PASSWORD,
            json.dumps(result["handler"].response()),
        )

    def test_mismatched_or_malformed_resolved_mailbox_never_connects(self):
        base = resolved_imap_mailbox()["mailbox"]
        invalid_mailboxes = (
            {**base, "mailboxId": "other-mailbox"},
            {**base, "email": "not-an-email"},
            {**base, "email": "a" * 4_097 + "@example.test"},
            {**base, "imap": None},
            {
                **base,
                "imap": {**base["imap"], "host": "bad host.invalid"},
            },
            {
                **base,
                "imap": {**base["imap"], "host": "imap\x80.invalid"},
            },
            {
                **base,
                "imap": {**base["imap"], "port": "993"},
            },
            {
                **base,
                "imap": {**base["imap"], "ssl": False},
            },
            {
                **base,
                "imap": {
                    **base["imap"],
                    "credentialVersion": "private-version",
                },
            },
            {
                **base,
                "imap": {**base["imap"], "username": ""},
            },
            {
                **base,
                "imap": {
                    **base["imap"],
                    "password": "private-password\ncontrol",
                },
            },
        )
        for resolved_mailbox in invalid_mailboxes:
            with self.subTest(
                mailbox_id=resolved_mailbox.get("mailboxId"),
                email=resolved_mailbox.get("email"),
            ):
                result = invoke_imap_route(
                    resolution={
                        "status": "ok",
                        "mailbox": resolved_mailbox,
                        "error": None,
                    }
                )
                target = result["handler"]
                self.assertEqual(target.status, 500)
                self.assertEqual(
                    target.response()["error"]["code"],
                    "mailbox_configuration_malformed",
                )
                result["connect"].assert_not_called()
                result["discovery"].assert_not_called()
                result["snapshot"].assert_not_called()
                serialized = json.dumps(target.response())
                self.assertNotIn("other-mailbox", serialized)
                self.assertNotIn("bad host.invalid", serialized)
                self.assertNotIn("private-password", serialized)

    def test_malformed_mismatched_or_private_imap_snapshots_fail_closed(self):
        valid = imap_trash_snapshot()
        missing_mailbox_id = imap_trash_message()
        missing_mailbox_id.pop("serverMailboxId")
        missing_folder = imap_trash_message()
        missing_folder.pop("providerFolder")
        missing_uid_validity = imap_trash_message()
        missing_uid_validity.pop("uidValidity")
        missing_uid = imap_trash_message()
        missing_uid.pop("imapUid")
        invalid_snapshots = (
            None,
            {},
            {**valid, "serverMailboxId": "other-mailbox"},
            {**valid, "providerFolder": "Trash"},
            {**valid, "uidValidity": "091"},
            {**valid, "imapUidSet": "7"},
            {**valid, "imapUidSet": ["07"]},
            {**valid, "imapUidSet": ["9", "7"]},
            {**valid, "imapUidSet": ["7", "7"]},
            {**valid, "identities": {}},
            {**valid, "messages": [missing_mailbox_id]},
            {**valid, "messages": [missing_folder]},
            {**valid, "messages": [missing_uid_validity]},
            {**valid, "messages": [missing_uid]},
            {
                **valid,
                "messages": [
                    imap_trash_message(providerFolder="Trash")
                ],
            },
            {
                **valid,
                "messages": [imap_trash_message(uidValidity="92")],
            },
            {
                **valid,
                "messages": [imap_trash_message(imapUid="8")],
            },
            {
                **valid,
                "messages": [
                    imap_trash_message(),
                    imap_trash_message(),
                ],
            },
            {
                **valid,
                "messages": [
                    imap_trash_message(
                        fingerprint="private-fingerprint"
                    )
                ],
            },
            {
                **valid,
                "messages": [
                    imap_trash_message(password="private-password")
                ],
            },
            {
                **valid,
                "messages": [
                    imap_trash_message(host="private-host.invalid")
                ],
            },
            {
                **valid,
                "messages": [imap_trash_message(port=65_534)],
            },
            {
                **valid,
                "messages": [imap_trash_message(ssl=False)],
            },
            {
                **valid,
                "messages": [
                    imap_trash_message(authMode="private-auth-mode")
                ],
            },
            {
                **valid,
                "messages": [
                    imap_trash_message(bodyHash="private-hash-value")
                ],
            },
            {
                **valid,
                "messages": [imap_trash_message(rfcMessageId="")],
            },
            {
                **valid,
                "messages": [
                    imap_trash_message(rfcMessageId="bad\nmessage-id")
                ],
            },
        )
        for snapshot in invalid_snapshots:
            with self.subTest(snapshot_type=type(snapshot).__name__):
                result = invoke_imap_route(
                    result={
                        "ok": True,
                        "status": "ok",
                        "snapshot": snapshot,
                        "identities": {
                            "7": {
                                "fingerprint": "internal-only"
                            }
                        },
                        "error": None,
                    }
                )
                target = result["handler"]
                self.assertEqual(target.status, 502)
                self.assertEqual(
                    target.response()["error"]["code"],
                    "trash_snapshot_failed",
                )
                serialized = json.dumps(target.response())
                self.assertNotIn("private-fingerprint", serialized)
                self.assertNotIn("private-password", serialized)
                self.assertNotIn("internal-only", serialized)
                self.assertNotIn("private-host.invalid", serialized)
                self.assertNotIn("private-auth-mode", serialized)
                self.assertNotIn("private-hash-value", serialized)

    def test_connection_discovery_and_snapshot_failures_are_sanitized(self):
        cases = (
            (
                "credentials",
                {
                    "connect_error": fetch_trash.imaplib.IMAP4.error(
                        f"bad {IMAP_PASSWORD}"
                    )
                },
                401,
                "invalid_credentials",
            ),
            (
                "connection",
                {
                    "connect_error": RuntimeError(
                        f"{IMAP_HOST} {IMAP_USERNAME} {IMAP_PASSWORD}"
                    )
                },
                502,
                "imap_connection_failed",
            ),
            (
                "discovery",
                {
                    "discovery_error": RuntimeError(
                        f"raw LIST {IMAP_PASSWORD}"
                    )
                },
                502,
                "trash_snapshot_failed",
            ),
            (
                "snapshot_result",
                {
                    "result": {
                        "ok": False,
                        "status": "error",
                        "snapshot": None,
                        "identities": {},
                        "error": {
                            "code": "snapshot_fetch_failed",
                            "detail": IMAP_PASSWORD,
                        },
                    }
                },
                502,
                "trash_snapshot_failed",
            ),
            (
                "snapshot_exception",
                {
                    "snapshot_error": RuntimeError(
                        f"raw snapshot {IMAP_PASSWORD}"
                    )
                },
                502,
                "trash_snapshot_failed",
            ),
            (
                "non_dict_snapshot_result",
                {"result": []},
                502,
                "trash_snapshot_failed",
            ),
            (
                "contradictory_snapshot_result",
                {
                    "result": {
                        **imap_snapshot_result(),
                        "ok": False,
                    }
                },
                502,
                "trash_snapshot_failed",
            ),
            (
                "snapshot_result_with_error",
                {
                    "result": {
                        **imap_snapshot_result(),
                        "error": {"code": "private-provider-error"},
                    }
                },
                502,
                "trash_snapshot_failed",
            ),
            (
                "snapshot_result_missing_identities",
                {
                    "result": {
                        key: value
                        for key, value in imap_snapshot_result().items()
                        if key != "identities"
                    }
                },
                502,
                "trash_snapshot_failed",
            ),
        )
        for name, kwargs, status_code, public_code in cases:
            with self.subTest(name=name):
                result = invoke_imap_route(**kwargs)
                target = result["handler"]
                self.assertEqual(target.status, status_code)
                self.assertEqual(
                    target.response()["error"]["code"],
                    public_code,
                )
                serialized = json.dumps(target.response())
                self.assertNotIn(IMAP_HOST, serialized)
                self.assertNotIn(IMAP_USERNAME, serialized)
                self.assertNotIn(IMAP_PASSWORD, serialized)

    def test_logout_failure_uses_shutdown_without_changing_success(self):
        mailbox = RecordingMailbox(
            logout_error=RuntimeError(f"logout {IMAP_PASSWORD}")
        )
        result = invoke_imap_route(mailbox=mailbox)

        self.assertEqual(result["handler"].status, 200)
        self.assertEqual(mailbox.logout_count, 1)
        self.assertEqual(mailbox.shutdown_count, 1)
        self.assertNotIn(
            IMAP_PASSWORD,
            json.dumps(result["handler"].response()),
        )


class FetchTrashTransportAndMethodTests(unittest.TestCase):
    def test_empty_non_json_and_non_object_provider_responses_are_invalid(self):
        for raw_response in (b"", b"not-json", b"[]"):
            with self.subTest(raw_response=raw_response), patch.object(
                fetch_trash,
                "urlopen",
                return_value=FakeResponse(raw_response),
            ):
                payload, error = fetch_trash._gmail_request(
                    ACCESS_TOKEN,
                    "/messages",
                )
            self.assertIsNone(payload)
            self.assertEqual(error, {"code": "gmail_response_invalid"})

    def test_read_only_request_refreshes_one_401_once(self):
        original = gmail_context()
        refreshed = {
            **original,
            "access_token": "refreshed-access-token",
            "refresh_attempted": True,
        }
        with patch.object(
            fetch_trash,
            "_gmail_request",
            side_effect=[
                (None, {"code": "gmail_token_invalid"}),
                ({"messages": []}, None),
            ],
        ) as request, patch.object(
            fetch_trash,
            "refresh_gmail_context",
            return_value={"status": "ok", "context": refreshed},
        ) as refresh:
            payload, error, context, refresh_failure = (
                fetch_trash._request_with_one_refresh(
                    original,
                    "/messages?labelIds=TRASH",
                )
            )

        self.assertEqual(payload, {"messages": []})
        self.assertIsNone(error)
        self.assertEqual(context, refreshed)
        self.assertIsNone(refresh_failure)
        self.assertEqual(
            request.call_args_list,
            [
                call(ACCESS_TOKEN, "/messages?labelIds=TRASH"),
                call(
                    "refreshed-access-token",
                    "/messages?labelIds=TRASH",
                ),
            ],
        )
        refresh.assert_called_once_with(original)

        already_refreshed = gmail_context(refresh_attempted=True)
        with patch.object(
            fetch_trash,
            "_gmail_request",
            return_value=(None, {"code": "gmail_token_invalid"}),
        ) as request, patch.object(
            fetch_trash,
            "refresh_gmail_context",
        ) as refresh:
            fetch_trash._request_with_one_refresh(
                already_refreshed,
                "/messages",
            )
        request.assert_called_once()
        refresh.assert_not_called()

    def test_methods_are_post_only_and_never_cached(self):
        for method in ("do_GET", "do_PUT", "do_PATCH", "do_DELETE"):
            with self.subTest(method=method):
                target = FakeHandler({})
                target.command = method.removeprefix("do_")
                getattr(fetch_trash.handler, method)(target)
                self.assertEqual(target.status, 405)
                self.assertEqual(
                    target.response()["error"]["code"],
                    "method_not_allowed",
                )
                self.assertIn(
                    ("Cache-Control", "no-store"),
                    target.response_headers,
                )

        head = FakeHandler({})
        head.command = "HEAD"
        fetch_trash.handler.do_HEAD(head)
        self.assertEqual(head.status, 405)
        self.assertEqual(head.wfile.getvalue(), b"")
        self.assertIn(("Cache-Control", "no-store"), head.response_headers)

        options = FakeHandler({})
        options.command = "OPTIONS"
        fetch_trash.handler.do_OPTIONS(options)
        self.assertEqual(options.status, 200)
        self.assertEqual(options.response(), {"ok": True})
        self.assertIn(
            ("Cache-Control", "no-store"),
            options.response_headers,
        )


if __name__ == "__main__":
    unittest.main()
