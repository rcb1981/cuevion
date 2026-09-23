from __future__ import annotations

import base64
import importlib
import io
import json
import random
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch
from urllib.error import HTTPError, URLError


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

    def test_current_window_emits_private_candidate_source_without_raw_content(self):
        message_id = "message-with-time"
        raw = (
            "Message-Id: <message-with-time@example.test>\r\n"
            "Date: Tue, 01 Jul 2025 12:00:00 +0200\r\n"
            "From: Sender Name <sender@example.test>\r\n"
            f"To: {MAILBOX_EMAIL}\r\n"
            "Subject: Candidate source\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "\r\n"
            "<p>Private body marker</p>"
        ).encode("utf-8")
        detail = {
            "id": message_id,
            "threadId": "provider-thread-exact",
            "labelIds": ["INBOX", "UNREAD", "STARRED"],
            "internalDate": "1751364000123",
            "raw": base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii"),
        }
        paths: list[str] = []
        result = gmail_snapshot.read_gmail_folder_snapshot(
            gmail_context(),
            provider_folder="Inbox",
            request_with_one_refresh=snapshot_request(
                [
                    ({"messages": [{"id": message_id}]}, None),
                    (detail, None),
                ],
                paths,
            ),
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(paths), 2)
        self.assertEqual(
            set(result["snapshot"]),
            {"providerFolder", "serverMailboxId", "messages", "uidValidity"},
        )
        source = result["_priorityCandidateSources"][0]
        self.assertEqual(source["providerMessageId"], message_id)
        self.assertEqual(source["providerThreadId"], "provider-thread-exact")
        self.assertEqual(source["providerFolder"], "INBOX")
        self.assertEqual(source["labels"], ["INBOX", "UNREAD", "STARRED"])
        self.assertEqual(source["providerTimestampMillis"], "1751364000123")
        self.assertEqual(source["rfcDate"], "Tue, 01 Jul 2025 12:00:00 +0200")
        self.assertEqual(source["senderAddress"], "sender@example.test")
        for forbidden in ("raw", "body", "bodyHtml", "attachments"):
            self.assertNotIn(forbidden, source)

    def test_missing_true_time_is_not_replaced_by_preview_created_at(self):
        message_id = "message-without-time"
        paths: list[str] = []
        result = gmail_snapshot.read_gmail_folder_snapshot(
            gmail_context(),
            provider_folder="Inbox",
            request_with_one_refresh=snapshot_request(
                [
                    ({"messages": [{"id": message_id}]}, None),
                    (gmail_detail(message_id), None),
                ],
                paths,
            ),
        )

        preview = result["snapshot"]["messages"][0]
        source = result["_priorityCandidateSources"][0]
        self.assertTrue(preview["createdAt"])
        self.assertIsNone(source["providerTimestampMillis"])
        self.assertIsNone(source["rfcDate"])

    def test_route_response_ignores_private_sidecar_and_population_failure(self):
        preview = {
            "providerMessageId": "message-1",
            "providerThreadId": "thread-1",
            "labelIds": ["INBOX"],
        }
        snapshot_result = {
            "status": "ok",
            "context": gmail_context(),
            "snapshot": {
                "messages": [preview],
                "uidValidity": "gmail-api",
            },
            "error": None,
            "refresh_failure": None,
            "_priorityCandidateSources": [{"private": "source"}],
        }
        request_handler = SimpleNamespace(headers={})
        member = object()
        sent: list[tuple[int, dict]] = []
        with patch.object(
            fetch_gmail,
            "read_json_body",
            return_value=({"mailboxId": MAILBOX_ID}, None),
        ), patch.object(
            fetch_gmail,
            "resolve_authenticated_gmail",
            return_value={
                "status": "ok",
                "context": gmail_context(),
                "memberAuthority": member,
            },
        ), patch.object(
            fetch_gmail,
            "read_gmail_folder_snapshot",
            return_value=snapshot_result,
        ) as snapshot_read, patch.object(
            fetch_gmail,
            "populate_runtime_priority_candidates",
            side_effect=RuntimeError("candidate store offline"),
        ) as populate, patch.object(
            fetch_gmail,
            "_run_gmail_priority_candidate_recovery",
        ) as recovery, patch.object(
            fetch_gmail,
            "read_new_inbound_client_mode",
            return_value="off",
        ), patch.object(
            fetch_gmail,
            "send_json",
            side_effect=lambda _handler, status, payload: sent.append(
                (status, payload)
            ),
        ):
            fetch_gmail.handler._handle_post(request_handler)

        snapshot_read.assert_called_once_with(
            gmail_context(),
            provider_folder="Inbox",
            request_with_one_refresh=fetch_gmail._request_with_one_refresh,
            gmail_request=fetch_gmail._gmail_request,
            refresh_context=fetch_gmail.refresh_gmail_context,
            limit=50,
            focus_preferences=None,
            strict=False,
            message_parser=fetch_gmail.message_from_bytes,
        )
        populate.assert_called_once_with(
            member=member,
            mailbox_id=MAILBOX_ID,
            mailbox_account_identity=MAILBOX_EMAIL,
            provider="google",
            sources=snapshot_result["_priorityCandidateSources"],
        )
        recovery.assert_called_once_with(
            member=member,
            context=gmail_context(),
            focus_preferences=None,
        )
        self.assertEqual(
            sent,
            [
                (
                    200,
                    {
                        "ok": True,
                        "messages": [preview],
                        "inboxUidSet": ["message-1"],
                        "uidValidity": "gmail-api",
                        "prioritySemanticNewInboundMode": "off",
                    },
                )
            ],
        )

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


class GmailInboxSnapshotRouteContractTests(unittest.TestCase):
    def call_route(self, snapshot_result, *, request_fields=None):
        target = SimpleNamespace(headers={})
        sent = []
        with patch.object(
            fetch_gmail,
            "read_json_body",
            return_value=({"mailboxId": MAILBOX_ID, **(request_fields or {})}, None),
        ), patch.object(
            fetch_gmail,
            "resolve_authenticated_gmail",
            return_value={
                "status": "ok",
                "context": gmail_context(),
                "memberAuthority": object(),
            },
        ), patch.object(
            fetch_gmail,
            "read_gmail_folder_snapshot",
            return_value=snapshot_result,
        ) as snapshot_read, patch.object(
            fetch_gmail,
            "populate_runtime_priority_candidates",
        ) as populate, patch.object(
            fetch_gmail,
            "_run_gmail_priority_candidate_recovery",
        ) as recovery, patch.object(
            fetch_gmail,
            "send_json",
            side_effect=lambda _target, status, payload: sent.append((status, payload)),
        ):
            fetch_gmail.handler._handle_post(target)
        return sent, snapshot_read, populate, recovery

    def test_inbox_limit_and_focus_contract_is_unchanged(self):
        for request_fields, expected_limit in (
            ({}, 50),
            ({"limit": None}, 50),
            ({"limit": -1}, 1),
            ({"limit": 1}, 1),
            ({"limit": 73}, 73),
            ({"limit": 101}, 100),
        ):
            with self.subTest(request_fields=request_fields):
                _, snapshot_read, _, _ = self.call_route(
                    {"error": {"code": "gmail_permission_denied"}},
                    request_fields={
                        **request_fields,
                        "focusPreferences": {"finance": "high"},
                    },
                )
                snapshot_read.assert_called_once_with(
                    gmail_context(),
                    provider_folder="Inbox",
                    request_with_one_refresh=fetch_gmail._request_with_one_refresh,
                    gmail_request=fetch_gmail._gmail_request,
                    refresh_context=fetch_gmail.refresh_gmail_context,
                    limit=expected_limit,
                    focus_preferences={"finance": "high"},
                    strict=False,
                    message_parser=fetch_gmail.message_from_bytes,
                )

    def test_inbox_provider_and_refresh_failures_keep_exact_public_contract(self):
        for internal_code, status, public_code, message in (
            ("gmail_token_invalid", 401, "reconnect_required", "Reconnect this Gmail inbox to continue."),
            ("gmail_permission_denied", 403, "gmail_permission_denied", "Gmail did not permit this operation."),
            ("gmail_rate_limited", 502, "gmail_rate_limited", "Gmail is temporarily rate limited."),
            ("gmail_unavailable", 502, "gmail_unavailable", "Gmail is temporarily unavailable."),
            ("gmail_response_invalid", 502, "gmail_response_invalid", "Gmail returned an invalid response."),
            ("gmail_response_too_large", 502, "gmail_response_too_large", "Gmail returned a response that is too large."),
            ("gmail_message_not_found", 502, "gmail_fetch_failed", "Gmail inbox could not be loaded."),
        ):
            with self.subTest(internal_code=internal_code):
                sent, _, populate, recovery = self.call_route({
                    "error": {"code": internal_code},
                    "_priorityCandidateSources": [{"private": "unused"}],
                })
                self.assertEqual(sent, [(status, fetch_gmail.error_payload(public_code, message))])
                populate.assert_not_called()
                recovery.assert_not_called()

        refresh_failure = {
            "status": "error",
            "status_code": 503,
            "error": fetch_gmail.error_payload(
                "gmail_token_store_unavailable",
                "Gmail authorization storage is temporarily unavailable.",
            ),
        }
        sent, _, populate, recovery = self.call_route({
            "error": {"code": "gmail_token_invalid"},
            "refresh_failure": refresh_failure,
            "_priorityCandidateSources": [{"private": "unused"}],
        })
        self.assertEqual(sent, [(503, refresh_failure["error"])])
        populate.assert_not_called()
        recovery.assert_not_called()


class GmailSnapshotSizeAccountingTests(unittest.TestCase):
    LIST_PATHS = {
        "Inbox": "/messages?labelIds=INBOX&maxResults=50",
        "Archive": (
            "/messages?q=-label%3Ainbox+-label%3Atrash+-label%3Aspam"
            "+-label%3Adrafts+-label%3Asent&maxResults=50"
        ),
        "Trash": "/messages?labelIds=TRASH&includeSpamTrash=true&maxResults=50",
    }

    @staticmethod
    def _snapshot(folder, rows, mailbox_id=MAILBOX_ID):
        return {
            "providerFolder": folder,
            "serverMailboxId": mailbox_id,
            "messages": rows,
            "uidValidity": "gmail-api",
        }

    @staticmethod
    def _source(index):
        return {"providerMessageId": f"message-{index}", "private": "sidecar"}

    def _reference(self, rows, folder, strict, size_limit):
        """The previous complete candidate-wrapper serialization algorithm."""
        messages = []
        sources = []
        snapshot = self._snapshot(folder, messages)
        events = [("request", self.LIST_PATHS[folder])]
        error = None
        overflow = False
        for index, preview in enumerate(rows):
            message_id = f"message-{index}"
            events.extend([
                ("request", f"/messages/{message_id}?format=raw"),
                ("parse", message_id),
            ])
            if preview is None:
                if strict:
                    error = {"code": "gmail_response_invalid"}
                    break
                continue
            candidate_snapshot = {**snapshot, "messages": [*messages, preview]}
            candidate_size = len(json.dumps(candidate_snapshot).encode("utf-8"))
            if candidate_size > size_limit:
                overflow = True
                if strict:
                    error = {"code": "gmail_response_too_large"}
                break
            messages.append(preview)
            if folder == "Inbox":
                sources.append(self._source(index))
        result = {
            "status": "error" if error else "ok",
            "context": gmail_context(),
            "snapshot": None if error else snapshot,
            "error": error,
            "refresh_failure": None,
        }
        if error is None:
            result["_priorityCandidateSources"] = sources
        return result, events, len(messages), overflow

    def _optimized(self, rows, folder, strict, size_limit, *, context=None):
        events = []
        parsed_rows = []

        def request(request_context, path):
            events.append(("request", path))
            if path.startswith("/messages?"):
                payload = {
                    "messages": [{"id": f"message-{i}"} for i in range(len(rows))]
                }
            else:
                payload = {"id": path.split("/")[-1].split("?")[0]}
            return payload, None, request_context, None

        def parse(_detail, *, requested_message_id, index, **_kwargs):
            events.append(("parse", requested_message_id))
            preview = rows[index]
            if preview is None:
                return None
            parsed_rows.append(preview)
            return preview, self._source(index)

        with patch.object(
            gmail_snapshot, "MAX_GMAIL_RESPONSE_BYTES", size_limit
        ), patch.object(
            gmail_snapshot,
            "_parse_gmail_message_detail_with_candidate_source",
            side_effect=parse,
        ):
            result = gmail_snapshot.read_gmail_folder_snapshot(
                gmail_context() if context is None else context,
                provider_folder=folder,
                strict=strict,
                request_with_one_refresh=request,
            )
        overflow = result["error"] == {"code": "gmail_response_too_large"}
        if result["snapshot"] is not None:
            accepted_count = len(result["snapshot"]["messages"])
            overflow = len(parsed_rows) > accepted_count
        else:
            accepted_count = len(parsed_rows) - int(overflow)
        return result, events, accepted_count, overflow

    def test_generated_reference_parity_at_every_prefix_boundary(self):
        randomizer = random.Random(902103)
        fragments = [
            "plain ASCII", "café naïve 中文 Ελληνικά", "😀🚀🧑🏽‍💻",
            'quotes: "double" and \'single\'', "back\\slash\\", "line1\nline2\r\n\t",
            '<div class="mail">Hello &amp; <b>world</b></div>', "", "\x00\b\f",
            "isolated surrogate: \ud800", "combining: e\u0301", "slash / end",
        ]
        comparisons = 0
        for case in range(48):
            rows = []
            for index in range(1 + case % 7):
                body = fragments[(case + index) % len(fragments)]
                body += "".join(randomizer.choices(fragments, k=4))
                if case % 12 == 0:
                    body *= 128
                rows.append({
                    "id": f"message-{index}",
                    "subject": randomizer.choice(fragments),
                    "body": body,
                    "bodyHtml": None if index % 2 else f"<p>{body}</p>",
                    "attachments": [
                        {
                            "id": f"attachment-{index}-{attachment}",
                            "filename": randomizer.choice(fragments) + ".txt",
                            "contentType": "text/plain",
                            "size": randomizer.randrange(100000),
                            "contentId": None,
                            "inline": bool(attachment % 2),
                        }
                        for attachment in range(case % 3)
                    ],
                    "empty": {"string": "", "list": [], "object": {}, "null": None},
                    "flags": [True, False, 0, -1, 1.25, -0.0],
                })
            for folder, strict in (("Inbox", False), ("Archive", True), ("Trash", True)):
                sizes = [
                    len(json.dumps(self._snapshot(folder, rows[:count])).encode("utf-8"))
                    for count in range(len(rows) + 1)
                ]
                limits = {0, gmail_snapshot.MAX_GMAIL_RESPONSE_BYTES}
                limits.update(size + offset for size in sizes for offset in (-1, 0, 1))
                for size_limit in sorted(limits):
                    with self.subTest(case=case, folder=folder, size_limit=size_limit):
                        expected = self._reference(rows, folder, strict, size_limit)
                        actual = self._optimized(rows, folder, strict, size_limit)
                        # Includes complete fields, accepted count, overflow,
                        # list/detail count/order and sequential parse events.
                        self.assertEqual(actual, expected)
                        self.assertEqual(
                            json.dumps(actual[0]).encode("utf-8"),
                            json.dumps(expected[0]).encode("utf-8"),
                        )
                        comparisons += 1
        self.assertEqual(comparisons, 2421)

    def test_skipped_invalid_rows_keep_the_same_comma_and_truncation_boundary(self):
        rows = [None, {"body": "é😀\n"}, None, {"body": '"\\'}, None]
        for folder in self.LIST_PATHS:
            valid_rows = [row for row in rows if row is not None]
            boundary = len(json.dumps(self._snapshot(folder, valid_rows)).encode("utf-8"))
            for strict in (False, True):
                for size_limit in (boundary - 1, boundary, boundary + 1):
                    with self.subTest(folder=folder, strict=strict, size_limit=size_limit):
                        self.assertEqual(
                            self._optimized(rows, folder, strict, size_limit),
                            self._reference(rows, folder, strict, size_limit),
                        )

    def test_empty_or_invalid_rows_do_not_serialize_wrapper_metadata(self):
        context = {**gmail_context(), "mailbox_id": object()}
        for folder in self.LIST_PATHS:
            for rows in ([], [None, None]):
                for strict in (False, True):
                    with self.subTest(folder=folder, rows=rows, strict=strict), patch.object(
                        gmail_snapshot.json, "dumps", side_effect=AssertionError("serialized")
                    ) as serialize:
                        result, _events, count, overflow = self._optimized(
                            rows, folder, strict, 0, context=context
                        )
                        serialize.assert_not_called()
                        self.assertEqual(count, 0)
                        self.assertFalse(overflow)
                        if strict and rows:
                            self.assertEqual(result["error"], {"code": "gmail_response_invalid"})
                        else:
                            self.assertEqual(result["snapshot"]["messages"], [])
                            self.assertIs(result["snapshot"]["serverMailboxId"], context["mailbox_id"])

    def test_provider_error_precedes_wrapper_serialization(self):
        context = {**gmail_context(), "mailbox_id": object()}
        for before_detail in (False, True):
            responses = [(None, {"code": "gmail_permission_denied"})]
            if before_detail:
                responses.insert(0, ({"messages": [{"id": "message-1"}]}, None))
            paths = []
            with self.subTest(before_detail=before_detail), patch.object(
                gmail_snapshot.json, "dumps", side_effect=AssertionError("serialized")
            ) as serialize:
                result = gmail_snapshot.read_gmail_folder_snapshot(
                    context,
                    provider_folder="Inbox",
                    request_with_one_refresh=snapshot_request(responses, paths),
                )
                serialize.assert_not_called()
                self.assertEqual(result["error"], {"code": "gmail_permission_denied"})
                self.assertEqual(len(paths), 1 + int(before_detail))

    def test_serialization_errors_still_propagate_at_first_valid_row(self):
        circular = {}
        circular["self"] = circular
        for preview in ({"invalid": object()}, circular, {("invalid",): 1}):
            for folder, strict in (("Inbox", False), ("Archive", True), ("Trash", True)):
                with self.subTest(preview_type=type(preview), folder=folder):
                    try:
                        self._reference([preview], folder, strict, 10**9)
                    except (TypeError, ValueError) as error:
                        error_type, error_message = type(error), str(error)
                    else:
                        self.fail("reference should fail serialization")
                    with self.assertRaises(error_type) as actual:
                        self._optimized([preview], folder, strict, 10**9)
                    self.assertEqual(str(actual.exception), error_message)

        context = {**gmail_context(), "mailbox_id": object()}
        with self.assertRaises(TypeError):
            self._optimized([None, {}], "Inbox", False, 10**9, context=context)

    def test_each_preview_serializes_once_and_wrapper_stays_empty_for_accounting(self):
        rows = [{"id": f"row-{index}", "body": "payload" * 100} for index in range(12)]
        serialize = json.dumps
        serialized_shapes = []

        def record_serialization(value, *args, **kwargs):
            if "messages" in value:
                serialized_shapes.append(("wrapper", len(value["messages"])))
            else:
                serialized_shapes.append(("preview", value["id"]))
            return serialize(value, *args, **kwargs)

        with patch.object(gmail_snapshot.json, "dumps", side_effect=record_serialization):
            result, _events, count, overflow = self._optimized(rows, "Inbox", False, 10**9)
        self.assertEqual(count, len(rows))
        self.assertFalse(overflow)
        self.assertEqual(result["snapshot"]["messages"], rows)
        self.assertEqual(
            serialized_shapes,
            [("wrapper", 0)] + [("preview", row["id"]) for row in rows],
        )

    def test_private_priority_sidecar_is_not_part_of_bounded_snapshot(self):
        rows = [{"body": "small"}]
        size_limit = len(json.dumps(self._snapshot("Inbox", rows)).encode("utf-8"))
        with patch.object(self, "_source", return_value={"private": "x" * (size_limit * 10)}):
            result, _events, count, overflow = self._optimized(rows, "Inbox", False, size_limit)
        self.assertEqual(count, 1)
        self.assertFalse(overflow)
        self.assertGreater(len(json.dumps(result).encode("utf-8")), size_limit)


class GmailExactMessageRecoveryTests(unittest.TestCase):
    def test_exact_helper_uses_one_raw_get_no_list_and_threads_context(self):
        message_id = "exact-message-1"
        detail = gmail_detail(message_id)
        detail["threadId"] = "gmail-authoritative-thread"
        paths: list[str] = []
        updated_context = {
            **gmail_context(),
            "access_token": "updated-test-token",
            "refresh_attempted": True,
        }

        def request(_context: dict, request_path: str):
            paths.append(request_path)
            return detail, None, updated_context, None

        with patch.object(
            gmail_snapshot,
            "_parse_gmail_message_detail_with_candidate_source",
            wraps=gmail_snapshot._parse_gmail_message_detail_with_candidate_source,
        ) as strict_parser:
            recovered = gmail_snapshot.recover_exact_gmail_inbox_message(
                gmail_context(),
                provider_message_id=message_id,
                request_with_one_refresh=request,
            )

        self.assertIs(
            recovered.result,
            gmail_snapshot.GmailExactMessageRecoveryResult.RECOVERED,
        )
        self.assertIs(recovered.context, updated_context)
        self.assertEqual(
            paths,
            ["/messages/exact-message-1?format=raw"],
        )
        self.assertFalse(any(path.startswith("/messages?") for path in paths))
        assert recovered.preview is not None
        self.assertEqual(recovered.preview["providerMessageId"], message_id)
        self.assertEqual(
            recovered.preview["providerThreadId"],
            "gmail-authoritative-thread",
        )
        assert recovered.candidate_source is not None
        self.assertEqual(
            recovered.candidate_source["providerThreadId"],
            "gmail-authoritative-thread",
        )
        self.assertTrue(strict_parser.call_args.kwargs["strict"])
        self.assertEqual(
            strict_parser.call_args.kwargs["requested_message_id"],
            message_id,
        )

    def test_exact_helper_retries_identity_thread_and_malformed_failures(self):
        message_id = "exact-message-1"
        cases = (
            {**gmail_detail("different-message"), "id": "different-message"},
            {**gmail_detail(message_id), "threadId": None},
            {**gmail_detail(message_id), "threadId": ""},
            {**gmail_detail(message_id), "threadId": ["invalid"]},
            {**gmail_detail(message_id), "labelIds": "INBOX"},
            {**gmail_detail(message_id), "raw": "not-valid-base64!"},
            [],
        )
        for payload in cases:
            with self.subTest(payload=payload):
                request = Mock(
                    return_value=(payload, None, gmail_context(), None)
                )
                recovered = gmail_snapshot.recover_exact_gmail_inbox_message(
                    gmail_context(),
                    provider_message_id=message_id,
                    request_with_one_refresh=request,
                )
                self.assertIs(
                    recovered.result,
                    gmail_snapshot.GmailExactMessageRecoveryResult.RETRY,
                )
                self.assertIsNone(recovered.preview)
                self.assertIsNone(recovered.candidate_source)
                request.assert_called_once()

    def test_exact_helper_terminally_classifies_non_inbox_and_404(self):
        message_id = "exact-message-1"
        for labels in (
            ["TRASH"],
            ["INBOX", "TRASH"],
            ["INBOX", "SPAM"],
            ["INBOX", "SENT"],
            ["INBOX", "DRAFT"],
        ):
            with self.subTest(labels=labels):
                detail = {**gmail_detail(message_id), "labelIds": labels}
                recovered = gmail_snapshot.recover_exact_gmail_inbox_message(
                    gmail_context(),
                    provider_message_id=message_id,
                    request_with_one_refresh=Mock(
                        return_value=(detail, None, gmail_context(), None)
                    ),
                )
                self.assertIs(
                    recovered.result,
                    gmail_snapshot.GmailExactMessageRecoveryResult.TERMINAL_ABSENT,
                )
                self.assertIsNone(recovered.preview)
                self.assertIsNone(recovered.candidate_source)

        not_found = gmail_snapshot.recover_exact_gmail_inbox_message(
            gmail_context(),
            provider_message_id=message_id,
            request_with_one_refresh=Mock(
                return_value=(
                    None,
                    {"code": "gmail_message_not_found"},
                    gmail_context(),
                    None,
                )
            ),
        )
        self.assertIs(
            not_found.result,
            gmail_snapshot.GmailExactMessageRecoveryResult.TERMINAL_ABSENT,
        )

        with patch.object(
            fetch_gmail,
            "urlopen",
            side_effect=HTTPError(
                "https://gmail.test/message",
                404,
                "not found",
                {},
                None,
            ),
        ):
            _payload, error = fetch_gmail._gmail_request(
                ACCESS_TOKEN,
                "/messages/exact-message-1?format=raw",
            )
        self.assertEqual(error, {"code": "gmail_message_not_found"})

    def test_exact_helper_keeps_provider_and_refresh_failures_retryable(self):
        message_id = "exact-message-1"
        cases = (
            ({"code": "gmail_token_invalid"}, None),
            ({"code": "gmail_permission_denied"}, None),
            ({"code": "gmail_rate_limited"}, None),
            ({"code": "gmail_fetch_failed"}, None),
            ({"code": "gmail_unavailable"}, None),
            (
                {"code": "gmail_token_invalid"},
                {
                    "status": "error",
                    "status_code": 503,
                    "error": {"code": "oauth_token_store_unavailable"},
                },
            ),
        )
        for error, refresh_failure in cases:
            with self.subTest(error=error, refresh_failure=refresh_failure):
                recovered = gmail_snapshot.recover_exact_gmail_inbox_message(
                    gmail_context(),
                    provider_message_id=message_id,
                    request_with_one_refresh=Mock(
                        return_value=(
                            None,
                            error,
                            gmail_context(refresh_attempted=True),
                            refresh_failure,
                        )
                    ),
                )
                self.assertIs(
                    recovered.result,
                    gmail_snapshot.GmailExactMessageRecoveryResult.RETRY,
                )
                self.assertTrue(recovered.context["refresh_attempted"])


if __name__ == "__main__":
    unittest.main()
