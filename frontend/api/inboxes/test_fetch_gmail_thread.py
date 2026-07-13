import base64
import importlib.util
import io
import json
import sys
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import gmail_thread_parser

ROUTE_PATH = CURRENT_DIR / "fetch-gmail-thread.py"


def load_route(name="fetch_gmail_thread_route_test"):
    spec = importlib.util.spec_from_file_location(name, ROUTE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


route = load_route()


class FakeHandler:
    def __init__(self, payload=None, raw_body=None, headers=None):
        body = raw_body if raw_body is not None else json.dumps(payload or {}).encode()
        self.headers = {"content-length": str(len(body)), **(headers or {})}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status = None
        self.response_headers = []

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        pass

    def response(self):
        return json.loads(self.wfile.getvalue())


class FakeProviderResponse:
    def __init__(self, payload, headers=None):
        self.payload = payload if isinstance(payload, bytes) else payload.encode()
        self.headers = headers or {}

    def read(self, limit=-1):
        return self.payload if limit < 0 else self.payload[:limit]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


def encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def gmail_message(message_id="message-1", thread_id="thread-1", internal_date="1000"):
    return {
        "id": message_id,
        "threadId": thread_id,
        "internalDate": internal_date,
        "labelIds": ["INBOX"],
        "snippet": "Snippet",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Message-ID", "value": f"<{message_id}@example.com>"},
                {"name": "From", "value": "Sender Name <sender@example.com>"},
                {"name": "To", "value": "recipient@example.com"},
                {"name": "Subject", "value": "Subject"},
            ],
            "body": {"data": encode(b"Body"), "size": 4},
        },
    }


def thread_payload(messages=None, thread_id="thread-1"):
    return {"id": thread_id, "messages": messages or [gmail_message(thread_id=thread_id)]}


def nested_part_payload(depth):
    part = {
        "partId": f"part-{depth}",
        "mimeType": "text/plain",
        "body": {"data": encode(b"Deep body")},
    }
    for current_depth in range(depth - 1, -1, -1):
        part = {
            "partId": f"part-{current_depth}",
            "mimeType": "multipart/mixed",
            "parts": [part],
        }
    return part


def owned_context(**overrides):
    return {
        "status": "ok",
        "context": {
            "mailbox_id": "mailbox-1",
            "mailbox_email": "owner@gmail.com",
            "owner_email": "owner@example.com",
            "access_token": "token",
            "refresh_attempted": False,
            **overrides,
        },
    }


def dispatch_request(method):
    request_handler = object.__new__(route.handler)
    raw_request = (
        f"{method} /api/inboxes/fetch-gmail-thread HTTP/1.1\r\n"
        "Host: localhost\r\n"
        "Content-Length: 0\r\n"
        "\r\n"
    )
    request_handler.rfile = io.BytesIO(raw_request.encode("ascii"))
    request_handler.wfile = io.BytesIO()
    request_handler.client_address = ("127.0.0.1", 0)
    request_handler.close_connection = True
    request_handler.handle_one_request()
    return request_handler, request_handler.wfile.getvalue()


def parse_http_response(raw_response):
    raw_headers, body = raw_response.split(b"\r\n\r\n", 1)
    header_lines = raw_headers.decode("iso-8859-1").split("\r\n")
    status = int(header_lines[0].split(" ", 2)[1])
    headers = {}
    for line in header_lines[1:]:
        name, value = line.split(":", 1)
        headers[name.lower()] = value.strip()
    return status, headers, body


class RequestValidationTests(unittest.TestCase):
    def invoke(self, payload=None, raw_body=None, headers=None):
        handler = FakeHandler(payload, raw_body, headers)
        with patch.object(route, "resolve_authenticated_gmail") as ownership:
            route.handler.do_POST(handler)
        return handler, ownership

    def test_unsupported_methods_and_options_contract(self):
        for method in ("GET", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                method_handler = FakeHandler()
                getattr(route.handler, f"do_{method}")(method_handler)
                self.assertEqual(method_handler.status, 405)
                self.assertEqual(
                    method_handler.response()["error"]["code"],
                    "method_not_allowed",
                )
                self.assertIn(
                    ("Content-Type", "application/json"),
                    method_handler.response_headers,
                )
                self.assertIn(
                    ("Cache-Control", "no-store"),
                    method_handler.response_headers,
                )

        head_handler = FakeHandler()
        route.handler.do_HEAD(head_handler)
        self.assertEqual(head_handler.status, 405)
        self.assertEqual(head_handler.wfile.getvalue(), b"")
        self.assertIn(("Content-Type", "application/json"), head_handler.response_headers)
        self.assertIn(("Cache-Control", "no-store"), head_handler.response_headers)
        self.assertTrue(any(name == "Content-Length" for name, _ in head_handler.response_headers))

        options_handler = FakeHandler()
        route.handler.do_OPTIONS(options_handler)
        self.assertEqual(options_handler.status, 200)
        self.assertEqual(options_handler.response(), {"ok": True})

    def test_unknown_methods_use_json_405_via_real_dispatch(self):
        with patch.object(route, "resolve_authenticated_gmail") as ownership, patch.object(
            route, "refresh_gmail_context"
        ) as refresh, patch.object(
            route, "_gmail_thread_request"
        ) as gmail_request, patch.object(
            route, "parse_gmail_thread"
        ) as parser:
            for method in ("TRACE", "CONNECT", "BREW"):
                with self.subTest(method=method):
                    request_handler, raw_response = dispatch_request(method)
                    status, headers, body = parse_http_response(raw_response)
                    payload = json.loads(body)

                    self.assertEqual(request_handler.command, method)
                    self.assertEqual(status, 405)
                    self.assertEqual(payload["error"]["code"], "method_not_allowed")
                    self.assertEqual(headers["content-type"], "application/json")
                    self.assertEqual(headers["cache-control"], "no-store")
                    self.assertEqual(int(headers["content-length"]), len(body))
                    self.assertNotIn(b"<html", body.lower())

        ownership.assert_not_called()
        refresh.assert_not_called()
        gmail_request.assert_not_called()
        parser.assert_not_called()

    def test_head_post_and_options_real_dispatch(self):
        _, raw_response = dispatch_request("HEAD")
        status, headers, body = parse_http_response(raw_response)
        self.assertEqual(status, 405)
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertGreater(int(headers["content-length"]), 0)
        self.assertEqual(body, b"")

        with patch.object(route.handler, "do_POST") as post:
            dispatch_request("POST")
        post.assert_called_once_with()

        with patch.object(route.handler, "do_OPTIONS") as options:
            dispatch_request("OPTIONS")
        options.assert_called_once_with()

    def test_non_501_send_error_delegates_to_base_handler(self):
        request_handler = object.__new__(route.handler)
        request_handler.command = "GET"
        with patch.object(BaseHTTPRequestHandler, "send_error") as base_send_error:
            route.handler.send_error(request_handler, 400, "Bad request", "Explanation")
        base_send_error.assert_called_once_with(400, "Bad request", "Explanation")

    def test_invalid_json_non_object_and_oversized_body(self):
        for raw_body, headers in (
            (b"{", None),
            (b"[]", None),
            (b"{}", {"content-length": str(route.MAX_REQUEST_BODY_BYTES + 1)}),
        ):
            with self.subTest(raw_body=raw_body, headers=headers):
                handler, ownership = self.invoke(raw_body=raw_body, headers=headers)
                self.assertEqual(handler.status, 400)
                self.assertEqual(handler.response()["error"]["code"], "invalid_request")
                ownership.assert_not_called()

    def test_invalid_identifiers_and_sensitive_fields(self):
        invalid_payloads = [
            {},
            {"mailboxId": "mailbox-1"},
            {"providerThreadId": "thread-1"},
            {"mailboxId": 1, "providerThreadId": "thread-1"},
            {"mailboxId": "", "providerThreadId": "thread-1"},
            {"mailboxId": " mailbox-1", "providerThreadId": "thread-1"},
            {"mailboxId": "mailbox-1", "providerThreadId": "thread-1\n"},
            {"mailboxId": "x" * 257, "providerThreadId": "thread-1"},
            {"mailboxId": "mailbox-1", "providerThreadId": "thread-1", "email": "x"},
            {"mailboxId": "mailbox-1", "providerThreadId": "thread-1", "access_token": "x"},
            {"mailboxId": "mailbox-1", "providerThreadId": "thread-1", "ownerEmail": "x"},
            {"mailboxId": "mailbox-1", "providerThreadId": "thread-1", "user_id": "x"},
            {"mailboxId": "mailbox-1", "providerThreadId": "thread-1", "owner_email": "x"},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                handler, ownership = self.invoke(payload)
                self.assertEqual(handler.status, 400)
                self.assertEqual(handler.response()["error"]["code"], "invalid_request")
                ownership.assert_not_called()


class OwnershipAndTokenTests(unittest.TestCase):
    request = {"mailboxId": "mailbox-1", "providerThreadId": "thread-1"}

    def invoke(self, resolution, gmail=None, refresh=None):
        handler = FakeHandler(self.request)
        with patch.object(route, "resolve_authenticated_gmail", return_value=resolution) as resolver, patch.object(
            route,
            "refresh_gmail_context",
            return_value=refresh or {
                "status": "error",
                "status_code": 401,
                "error": route._error("reconnect_required", "Gmail authorization must be renewed."),
            },
        ) as refresh_mock, patch.object(
            route,
            "_gmail_thread_request",
            side_effect=gmail,
        ) as gmail_mock:
            route.handler.do_POST(handler)
        return handler, resolver, refresh_mock, gmail_mock

    def test_strict_ownership_status_mapping(self):
        cases = [(401, "unauthorized"), (404, "gmail_connection_not_found"), (503, "user_config_store_unavailable")]
        for status, code in cases:
            with self.subTest(status=status):
                resolution = {"status": "error", "status_code": status, "error": route._error(code, "Safe error")}
                handler, resolver, _, _ = self.invoke(resolution)
                self.assertEqual(handler.status, status)
                self.assertEqual(handler.response()["error"]["code"], code)
                resolver.assert_called_once_with(handler.headers, "mailbox-1")

    def test_missing_and_refresh_token_errors(self):
        resolution = {"status": "error", "status_code": 401, "error": route._error("reconnect_required", "Reconnect Gmail.")}
        handler, resolver, _, gmail = self.invoke(resolution)
        self.assertEqual(handler.response()["error"]["code"], "reconnect_required")
        resolver.assert_called_once()
        gmail.assert_not_called()

        handler, _, refresh, gmail = self.invoke(
            owned_context(),
            gmail=[(None, {"code": "gmail_token_invalid"})],
        )
        self.assertEqual(handler.response()["error"]["code"], "reconnect_required")
        refresh.assert_called_once()

    def test_normal_success_one_request_and_stale_token_one_retry(self):
        handler, _, refresh, gmail = self.invoke(
            owned_context(),
            gmail=[(thread_payload(), None)],
        )
        self.assertEqual(handler.status, 200)
        self.assertEqual(gmail.call_count, 1)
        gmail.assert_called_once_with("token", "thread-1")
        refresh.assert_not_called()

        handler, _, refresh, gmail = self.invoke(
            owned_context(),
            gmail=[
                (None, {"code": "gmail_token_invalid"}),
                (thread_payload(), None),
            ],
            refresh={"status": "ok", "context": owned_context(access_token="new")["context"]},
        )
        self.assertEqual(handler.status, 200)
        self.assertEqual(gmail.call_count, 2)
        refresh.assert_called_once()

    def test_revoked_after_retry_and_provider_errors(self):
        handler, _, refresh, gmail = self.invoke(
            owned_context(),
            gmail=[
                (None, {"code": "gmail_token_invalid"}),
                (None, {"code": "gmail_token_invalid"}),
            ],
            refresh={"status": "ok", "context": owned_context(access_token="new")["context"]},
        )
        self.assertEqual(handler.status, 401)
        self.assertEqual(gmail.call_count, 2)
        self.assertEqual(refresh.call_count, 1)

        for error, status in (
            ({"code": "gmail_thread_not_found"}, 404),
            ({"code": "gmail_unavailable"}, 502),
            ({"code": "gmail_thread_fetch_failed"}, 502),
            ({"code": "gmail_response_invalid"}, 502),
            ({"code": "gmail_thread_too_large"}, 502),
        ):
            with self.subTest(error=error):
                handler, _, _, _ = self.invoke(owned_context(), gmail=[(None, error)])
                self.assertEqual(handler.status, status)
                self.assertNotIn("token", json.dumps(handler.response()).lower())

    def test_parser_depth_and_cycle_errors_map_to_safe_response(self):
        deep_message = gmail_message()
        deep_message["payload"] = nested_part_payload(
            gmail_thread_parser.MAX_MESSAGE_PART_DEPTH + 1
        )

        cyclic_message = gmail_message()
        cyclic_payload = {"mimeType": "multipart/mixed", "parts": []}
        cyclic_payload["parts"].append(cyclic_payload)
        cyclic_message["payload"] = cyclic_payload

        for case, payload in (
            ("depth", thread_payload([deep_message])),
            ("cycle", thread_payload([cyclic_message])),
        ):
            with self.subTest(case=case):
                handler, _, _, _ = self.invoke(
                    owned_context(),
                    gmail=[(payload, None)],
                )
                self.assertEqual(handler.status, 502)
                self.assertEqual(
                    handler.response()["error"]["code"],
                    "gmail_response_invalid",
                )
                self.assertNotIn("cycle", json.dumps(handler.response()).lower())
                self.assertNotIn("depth", json.dumps(handler.response()).lower())


class TransportTests(unittest.TestCase):
    def test_exact_url_bounded_read_and_no_message_or_attachment_paths(self):
        captured = []

        def fake_urlopen(request, timeout):
            captured.append((request, timeout))
            return FakeProviderResponse(json.dumps(thread_payload()))

        with patch.object(route, "urlopen", side_effect=fake_urlopen):
            payload, error = route._gmail_thread_request("token", "thread /opaque")
        self.assertIsNone(error)
        self.assertIsInstance(payload, dict)
        self.assertEqual(len(captured), 1)
        request, timeout = captured[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(
            request.full_url,
            "https://gmail.googleapis.com/gmail/v1/users/me/threads/thread%20%2Fopaque?format=full",
        )
        self.assertNotIn("messages", request.full_url)
        self.assertNotIn("attachments", request.full_url)
        self.assertEqual(timeout, 20)

    def test_response_size_json_http_and_network_errors(self):
        cases = [
            (
                FakeProviderResponse(b"{}", {"Content-Length": str(route.MAX_GMAIL_RESPONSE_BYTES + 1)}),
                None,
                "gmail_thread_too_large",
            ),
            (FakeProviderResponse(b"x" * (route.MAX_GMAIL_RESPONSE_BYTES + 1)), None, "gmail_thread_too_large"),
            (FakeProviderResponse(b"not-json"), None, "gmail_response_invalid"),
            (None, HTTPError("url", 404, "", {}, io.BytesIO(b"secret")), "gmail_thread_not_found"),
            (None, HTTPError("url", 401, "", {}, io.BytesIO(b"secret")), "gmail_token_invalid"),
            (None, HTTPError("url", 403, "", {}, io.BytesIO(b"secret")), "gmail_permission_denied"),
            (None, HTTPError("url", 429, "", {}, io.BytesIO(b"secret")), "gmail_rate_limited"),
            (None, HTTPError("url", 500, "", {}, io.BytesIO(b"secret")), "gmail_thread_fetch_failed"),
            (None, URLError("offline"), "gmail_unavailable"),
        ]
        for response, failure, code in cases:
            with self.subTest(code=code), patch.object(
                route,
                "urlopen",
                return_value=response,
                side_effect=failure,
            ):
                _, error = route._gmail_thread_request("token", "thread-1")
            self.assertEqual(error["code"], code)


class ParserTests(unittest.TestCase):
    def test_headers_dates_labels_and_provider_fields(self):
        message = gmail_message(internal_date="1000")
        message["labelIds"] = ["INBOX", "UNREAD", "STARRED"]
        message["payload"]["headers"] = [
            {"name": "mEsSaGe-Id", "value": "<rfc@example.com>"},
            {"name": "dAtE", "value": "Thu, 1 Jan 1970 00:00:01 +0000"},
            {"name": "fRoM", "value": "Sender Name <sender@example.com>"},
            {"name": "tO", "value": "to@example.com"},
            {"name": "cC", "value": "cc@example.com"},
            {"name": "sUbJeCt", "value": "Subject"},
            {"name": "X-Ignored", "value": "=?unknown-charset?b?VGVzdA==?="},
        ]
        parsed = gmail_thread_parser.parse_gmail_thread(thread_payload([message]), "thread-1")[0]
        self.assertEqual(parsed["providerMessageId"], "message-1")
        self.assertEqual(parsed["providerThreadId"], "thread-1")
        self.assertEqual(parsed["rfcMessageId"], "rfc@example.com")
        self.assertEqual(parsed["createdAt"], "1970-01-01T00:00:01.000Z")
        self.assertEqual(parsed["sender"], "Sender Name")
        self.assertTrue(parsed["unread"])
        self.assertTrue(parsed["flagged"])
        forbidden = {"category", "signal", "ui_signal", "priority", "action", "final_visibility"}
        self.assertFalse(forbidden.intersection(parsed))

    def test_nested_plain_html_utf8_and_attachment_metadata(self):
        message = gmail_message()
        message["payload"] = {
            "mimeType": "multipart/mixed",
            "headers": [{"name": "From", "value": "sender@example.com"}],
            "parts": [
                {
                    "partId": "1",
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"partId": "1.1", "mimeType": "text/plain", "body": {"data": encode(b"Plain \xff")}},
                        {"partId": "1.2", "mimeType": "text/html", "body": {"data": encode(b"<p>HTML</p>")}},
                    ],
                },
                {
                    "partId": "2",
                    "mimeType": "application/pdf",
                    "filename": "file.pdf",
                    "headers": [
                        {"name": "Content-Disposition", "value": "attachment"},
                        {"name": "Content-ID", "value": "<cid-1>"},
                    ],
                    "body": {"attachmentId": "attachment-1", "size": 42, "data": encode(b"must-not-return")},
                },
            ],
        }
        parsed = gmail_thread_parser.parse_gmail_thread(thread_payload([message]), "thread-1")[0]
        self.assertEqual(parsed["bodyText"], "Plain �")
        self.assertEqual(parsed["bodyHtml"], "<p>HTML</p>")
        self.assertEqual(parsed["attachments"][0]["providerAttachmentId"], "attachment-1")
        self.assertEqual(parsed["attachments"][0]["contentId"], "cid-1")
        self.assertNotIn("must-not-return", json.dumps(parsed))

    def test_attachment_descendants_do_not_enter_normal_body(self):
        message = gmail_message()
        message["payload"] = {
            "mimeType": "multipart/mixed",
            "headers": [],
            "parts": [
                {
                    "partId": "body",
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "partId": "body.plain",
                            "mimeType": "text/plain",
                            "body": {"data": encode(b"Normal plain")},
                        },
                        {
                            "partId": "body.html",
                            "mimeType": "text/html",
                            "body": {"data": encode(b"<p>Normal HTML</p>")},
                        },
                    ],
                },
                {
                    "partId": "attachment",
                    "mimeType": "message/rfc822",
                    "filename": "attached.eml",
                    "headers": [
                        {"name": "Content-Disposition", "value": "attachment"}
                    ],
                    "body": {"attachmentId": "attachment-1", "size": 100},
                    "parts": [
                        {
                            "partId": "attachment.plain",
                            "mimeType": "text/plain",
                            "body": {"data": encode(b"Attached plain")},
                        },
                        {
                            "partId": "attachment.html",
                            "mimeType": "text/html",
                            "body": {"data": encode(b"<p>Attached HTML</p>")},
                        },
                    ],
                },
            ],
        }

        parsed = gmail_thread_parser.parse_gmail_thread(
            thread_payload([message]), "thread-1"
        )[0]
        self.assertEqual(parsed["bodyText"], "Normal plain")
        self.assertEqual(parsed["bodyHtml"], "<p>Normal HTML</p>")
        self.assertEqual(len(parsed["attachments"]), 1)
        self.assertEqual(parsed["attachments"][0]["name"], "attached.eml")
        self.assertEqual(
            parsed["attachments"][0]["providerAttachmentId"], "attachment-1"
        )
        self.assertNotIn("Attached plain", json.dumps(parsed))
        self.assertNotIn("Attached HTML", json.dumps(parsed))

    def test_message_part_depth_limit_and_cycle_detection(self):
        supported = gmail_message()
        supported["payload"] = nested_part_payload(
            gmail_thread_parser.MAX_MESSAGE_PART_DEPTH
        )
        parsed = gmail_thread_parser.parse_gmail_thread(
            thread_payload([supported]), "thread-1"
        )[0]
        self.assertEqual(parsed["bodyText"], "Deep body")

        too_deep = gmail_message()
        too_deep["payload"] = nested_part_payload(
            gmail_thread_parser.MAX_MESSAGE_PART_DEPTH + 1
        )
        with self.assertRaises(gmail_thread_parser.GmailThreadParseError):
            gmail_thread_parser.parse_gmail_thread(
                thread_payload([too_deep]), "thread-1"
            )

        cyclic = gmail_message()
        cyclic_payload = {"mimeType": "multipart/mixed", "parts": []}
        cyclic_payload["parts"].append(cyclic_payload)
        cyclic["payload"] = cyclic_payload
        with self.assertRaises(gmail_thread_parser.GmailThreadParseError):
            gmail_thread_parser.parse_gmail_thread(
                thread_payload([cyclic]), "thread-1"
            )

        shared_part = {
            "partId": "shared",
            "mimeType": "text/plain",
            "body": {"data": encode(b"Shared body")},
        }
        repeated = gmail_message()
        repeated["payload"] = {
            "mimeType": "multipart/mixed",
            "parts": [shared_part, shared_part],
        }
        parsed = gmail_thread_parser.parse_gmail_thread(
            thread_payload([repeated]), "thread-1"
        )[0]
        self.assertEqual(parsed["bodyText"], "Shared body\nShared body")

    def test_html_only_empty_invalid_date_dedup_and_ordering(self):
        html_message = gmail_message("b", internal_date="bad")
        html_message["payload"] = {
            "mimeType": "text/html",
            "headers": [],
            "body": {"data": encode(b"<p>Hello<br>World</p>")},
        }
        first = gmail_message("a", internal_date="2")
        tied = gmail_message("c", internal_date="2")
        duplicate = gmail_message("a", internal_date="1")
        parsed = gmail_thread_parser.parse_gmail_thread(
            thread_payload([html_message, tied, duplicate, first]), "thread-1"
        )
        self.assertEqual([message["providerMessageId"] for message in parsed], ["a", "c", "b"])
        self.assertEqual(parsed[-1]["createdAt"], gmail_thread_parser.INVALID_CREATED_AT)
        self.assertEqual(parsed[-1]["internalDate"], "bad")
        self.assertEqual(parsed[-1]["bodyText"], "Hello\nWorld")

        empty = gmail_message("empty", internal_date="")
        empty["payload"] = {"mimeType": "text/plain", "headers": [], "body": {}}
        self.assertEqual(
            gmail_thread_parser.parse_gmail_thread(thread_payload([empty]), "thread-1")[0]["bodyText"],
            "",
        )

    def test_invalid_thread_membership_messages_and_limit(self):
        invalid_payloads = [
            {},
            {"id": "other", "messages": []},
            {"id": "thread-1"},
            thread_payload([{"threadId": "thread-1"}]),
            thread_payload([gmail_message(thread_id="other")]),
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(gmail_thread_parser.GmailThreadParseError):
                    gmail_thread_parser.parse_gmail_thread(payload, "thread-1")

        too_many = [gmail_message(str(index)) for index in range(501)]
        with self.assertRaises(OverflowError):
            gmail_thread_parser.parse_gmail_thread(thread_payload(too_many), "thread-1")


class ImportSafetyTests(unittest.TestCase):
    def test_imports_perform_no_external_calls_or_writes(self):
        with patch("urllib.request.urlopen") as network, patch(
            "user_config_store.resolve_owned_managed_inbox"
        ) as ownership, patch(
            "oauth_token_store.get_google_token_record_with_metadata"
        ) as token_lookup, patch(
            "oauth_token_store.refresh_google_token_record"
        ) as refresh:
            load_route("fetch_gmail_thread_import_safety_test")
        network.assert_not_called()
        ownership.assert_not_called()
        token_lookup.assert_not_called()
        refresh.assert_not_called()

        with patch("urllib.request.urlopen") as network:
            spec = importlib.util.spec_from_file_location(
                "gmail_thread_parser_import_safety_test",
                CURRENT_DIR / "gmail_thread_parser.py",
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
