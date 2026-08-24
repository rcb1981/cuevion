from __future__ import annotations

import base64
import importlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch
from urllib.error import URLError


CURRENT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = CURRENT_DIR.parent.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))


fetch_gmail = importlib.import_module("api.inboxes.fetch-gmail")
gmail_snapshot = importlib.import_module("api.inboxes.gmail_snapshot")


MAILBOX_ID = "server-mailbox"
MAILBOX_EMAIL = "owned@gmail.test"
ACCESS_TOKEN = "test-only-access-token"


def gmail_context(*, refresh_attempted: bool = False) -> dict:
    return {
        "mailbox_id": MAILBOX_ID,
        "mailbox_email": MAILBOX_EMAIL,
        "owner_email": "owner@example.test",
        "access_token": ACCESS_TOKEN,
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
        "refresh_attempted": refresh_attempted,
    }


def gmail_detail(message_id: str) -> dict:
    raw = (
        f"Message-Id: <{message_id}@example.test>\r\n"
        "From: sender@example.test\r\n"
        f"To: {MAILBOX_EMAIL}\r\n"
        f"Subject: Message {message_id}\r\n"
        "\r\n"
        "Body"
    ).encode("utf-8")
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "labelIds": ["INBOX"],
        "raw": base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii"),
    }


class FakeResponse:
    def __init__(self, payload: object):
        self.body = json.dumps(payload).encode("utf-8")
        self.headers: dict[str, str] = {}
        self.stream = io.BytesIO(self.body)

    def read(self, amount: int = -1):
        return self.stream.read(amount)

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False


def provider_paths(provider_transport: Mock) -> list[str]:
    return [
        provider_call.args[0].full_url.removeprefix(
            fetch_gmail.GMAIL_API_BASE_URL,
        )
        for provider_call in provider_transport.call_args_list
    ]


def snapshot_request(
    responses: list[tuple[object | None, dict | None]],
    paths: list[str],
):
    def request(context: dict, path: str):
        paths.append(path)
        payload, error = responses.pop(0)
        return payload, error, context, None

    return request


class GmailSnapshotTransportRetryTests(unittest.TestCase):
    def test_list_transport_failure_retries_once_and_succeeds(self):
        with patch.object(
            fetch_gmail,
            "urlopen",
            side_effect=[
                URLError("offline"),
                FakeResponse({"messages": []}),
            ],
        ) as provider_transport:
            result = gmail_snapshot.read_gmail_folder_snapshot(
                gmail_context(),
                provider_folder="Inbox",
                request_with_one_refresh=fetch_gmail._request_with_one_refresh,
            )

        self.assertEqual(result["status"], "ok")
        paths = provider_paths(provider_transport)
        self.assertEqual(len(paths), 2)
        self.assertEqual(paths[0], paths[1])

    def test_detail_transport_failure_retries_only_failed_detail(self):
        first_id = "message-1"
        second_id = "message-2"
        with patch.object(
            fetch_gmail,
            "urlopen",
            side_effect=[
                FakeResponse(
                    {"messages": [{"id": first_id}, {"id": second_id}]},
                ),
                FakeResponse(gmail_detail(first_id)),
                TimeoutError("timed out"),
                FakeResponse(gmail_detail(second_id)),
            ],
        ) as provider_transport:
            result = gmail_snapshot.read_gmail_folder_snapshot(
                gmail_context(),
                provider_folder="Inbox",
                request_with_one_refresh=fetch_gmail._request_with_one_refresh,
            )

        self.assertEqual(result["status"], "ok")
        paths = provider_paths(provider_transport)
        self.assertEqual(
            paths,
            [
                paths[0],
                f"/messages/{first_id}?format=raw",
                f"/messages/{second_id}?format=raw",
                f"/messages/{second_id}?format=raw",
            ],
        )
        self.assertEqual(
            [message["providerMessageId"] for message in result["snapshot"]["messages"]],
            [first_id, second_id],
        )

    def test_retry_budget_is_global_per_snapshot(self):
        message_id = "message-after-list-retry"
        paths: list[str] = []
        result = gmail_snapshot.read_gmail_folder_snapshot(
            gmail_context(),
            provider_folder="Inbox",
            request_with_one_refresh=snapshot_request(
                [
                    (None, {"code": "gmail_unavailable"}),
                    ({"messages": [{"id": message_id}]}, None),
                    (None, {"code": "gmail_unavailable"}),
                ],
                paths,
            ),
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "gmail_unavailable")
        self.assertEqual(len(paths), 3)
        self.assertEqual(paths[0], paths[1])
        self.assertNotEqual(paths[1], paths[2])

    def test_repeated_transport_failure_returns_gmail_unavailable(self):
        with patch.object(
            fetch_gmail,
            "urlopen",
            side_effect=[
                URLError("offline"),
                TimeoutError("timed out"),
            ],
        ) as provider_transport:
            result = gmail_snapshot.read_gmail_folder_snapshot(
                gmail_context(),
                provider_folder="Inbox",
                request_with_one_refresh=fetch_gmail._request_with_one_refresh,
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "gmail_unavailable")
        paths = provider_paths(provider_transport)
        self.assertEqual(len(paths), 2)
        self.assertEqual(paths[0], paths[1])

    def test_existing_401_refresh_path_is_unchanged(self):
        refreshed_context = gmail_context(refresh_attempted=True)
        refreshed_context["access_token"] = "refreshed-access-token"
        refresh_result = {"status": "ok", "context": refreshed_context}

        with patch.object(
            fetch_gmail,
            "_gmail_request",
            side_effect=[
                (None, {"code": "gmail_token_invalid"}),
                ({"messages": []}, None),
            ],
        ) as gmail_request, patch.object(
            fetch_gmail,
            "refresh_gmail_context",
            return_value=refresh_result,
        ) as refresh_context:
            payload, error, context, refresh_failure = (
                fetch_gmail._request_with_one_refresh(
                    gmail_context(),
                    "/messages?labelIds=INBOX",
                )
            )

        self.assertEqual(payload, {"messages": []})
        self.assertIsNone(error)
        self.assertEqual(context, refreshed_context)
        self.assertIsNone(refresh_failure)
        refresh_context.assert_called_once()
        self.assertEqual(
            gmail_request.call_args_list,
            [
                call(ACCESS_TOKEN, "/messages?labelIds=INBOX"),
                call("refreshed-access-token", "/messages?labelIds=INBOX"),
            ],
        )

    def test_403_is_not_transport_retried(self):
        self._assert_provider_error_is_not_retried("gmail_permission_denied")

    def test_429_is_not_transport_retried(self):
        self._assert_provider_error_is_not_retried("gmail_rate_limited")

    def test_invalid_provider_response_is_not_transport_retried(self):
        paths: list[str] = []
        result = gmail_snapshot.read_gmail_folder_snapshot(
            gmail_context(),
            provider_folder="Inbox",
            request_with_one_refresh=snapshot_request(
                [([], None)],
                paths,
            ),
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "gmail_response_invalid")
        self.assertEqual(len(paths), 1)

    def test_response_too_large_is_not_transport_retried(self):
        self._assert_provider_error_is_not_retried("gmail_response_too_large")

    def test_successful_snapshot_makes_no_extra_provider_call(self):
        paths: list[str] = []
        result = gmail_snapshot.read_gmail_folder_snapshot(
            gmail_context(),
            provider_folder="Inbox",
            request_with_one_refresh=snapshot_request(
                [({"messages": []}, None)],
                paths,
            ),
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(paths), 1)

    def test_refresh_failure_is_not_transport_retried(self):
        request = Mock(
            return_value=(
                None,
                {"code": "gmail_unavailable"},
                gmail_context(),
                {
                    "status": "error",
                    "status_code": 503,
                    "error": {"code": "oauth_token_store_unavailable"},
                },
            )
        )
        result = gmail_snapshot.read_gmail_folder_snapshot(
            gmail_context(),
            provider_folder="Inbox",
            request_with_one_refresh=request,
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(
            result["refresh_failure"]["error"]["code"],
            "oauth_token_store_unavailable",
        )
        request.assert_called_once()

    def _assert_provider_error_is_not_retried(self, error_code: str):
        paths: list[str] = []
        result = gmail_snapshot.read_gmail_folder_snapshot(
            gmail_context(),
            provider_folder="Inbox",
            request_with_one_refresh=snapshot_request(
                [(None, {"code": error_code})],
                paths,
            ),
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], error_code)
        self.assertEqual(len(paths), 1)


if __name__ == "__main__":
    unittest.main()
