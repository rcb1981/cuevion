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


def gmail_context(*, refresh_attempted: bool = False) -> dict:
    return {
        "mailbox_id": MAILBOX_ID,
        "mailbox_email": "owned@gmail.test",
        "owner_email": "owner@example.test",
        "access_token": ACCESS_TOKEN,
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
        "refresh_attempted": refresh_attempted,
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
    result: dict | None = None,
):
    target = FakeHandler({"mailboxId": MAILBOX_ID} if payload is None else payload)
    authority = Mock(
        return_value=resolution
        or {"status": "ok", "context": gmail_context()}
    )
    snapshot_read = Mock(return_value=result or snapshot_result())
    with patch.object(
        fetch_trash,
        "resolve_authenticated_gmail",
        authority,
    ), patch.object(
        fetch_trash,
        "read_gmail_folder_snapshot",
        snapshot_read,
    ):
        fetch_trash.handler.do_POST(target)
    return target, authority, snapshot_read


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
