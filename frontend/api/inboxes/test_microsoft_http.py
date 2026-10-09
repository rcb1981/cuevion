from __future__ import annotations

import io
import json
import unittest
from email.message import Message
from types import SimpleNamespace
from unittest.mock import patch

from api.inboxes import microsoft_http
from api.inboxes import microsoft_mailbox_access as access
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes.microsoft_inbox_projection import project_inbox, project_message

MAILBOX = "microsoft-0123456789abcdefabcd12345678"
MESSAGE_ID = "AAMkAGVm.987/"
GRAPH_BODY = {
    "id": MESSAGE_ID,
    "conversationId": "AAQkABc123",
    "subject": "Demo submission",
    "senderEmail": "artist@example.net",
    "senderName": "Demo Artist",
    "preview": "Please listen to my private link.",
    "receivedAt": "2026-10-09T09:15:00+00:00",
    "unread": True,
    "hasAttachments": True,
}
GRAPH_LIST = {"messages": [GRAPH_BODY], "hasMore": False}
GRAPH_EXACT = {
    **GRAPH_BODY,
    "body": {
        "contentType": "html",
        "content": "<p>Hi there.</p><script>secret()</script><style>.x{}</style><p>Listen to this track.</p>",
    },
}


def _request(payload=None, *, command="POST", raw=None, header_pairs=()):
    data = (
        raw if raw is not None
        else json.dumps(payload if payload is not None else {"mailboxId": MAILBOX}).encode("utf-8")
    )
    headers = Message()
    for name, value in (
        ("Host", "app.cuevion.com"),
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(data))),
        ("Cookie", "session=placeholder"),
        *header_pairs,
    ):
        headers[name] = value
    return SimpleNamespace(command=command, headers=headers, rfile=io.BytesIO(data))


def _body(response):
    return json.loads(response.body.decode("utf-8"))


class MicrosoftProjectionTests(unittest.TestCase):
    def test_project_inbox_is_preview_only_with_provider_identity(self):
        result = project_inbox(GRAPH_LIST, MAILBOX)
        self.assertTrue(result["ok"])
        self.assertEqual(result["uidValidity"], "microsoft-graph-immutable")
        self.assertEqual(result["inboxUidSet"], [MESSAGE_ID])
        self.assertFalse(result["hasMore"])
        message = result["messages"][0]
        self.assertEqual(message["id"], MESSAGE_ID)
        self.assertEqual(message["providerMessageId"], MESSAGE_ID)
        self.assertEqual(message["providerThreadId"], "AAQkABc123")
        self.assertEqual(message["serverMailboxId"], MAILBOX)
        self.assertEqual(message["providerFolder"], "INBOX")
        self.assertEqual(message["labelIds"], ["INBOX", "UNREAD"])
        self.assertEqual(message["sender"], "Demo Artist")
        self.assertEqual(message["from"], "Demo Artist <artist@example.net>")
        self.assertEqual(message["subject"], "Demo submission")
        self.assertTrue(message["unread"])
        self.assertEqual(message["createdAt"], GRAPH_BODY["receivedAt"])
        self.assertEqual(message["body"], [])
        self.assertNotIn("bodyHtml", message)
        self.assertEqual(message["attachments"], [])
        self.assertNotIn("access_token", json.dumps(result))

    def test_marked_read_removes_unread_label(self):
        item = project_message({**GRAPH_BODY, "unread": False}, MAILBOX)
        self.assertEqual(item["labelIds"], ["INBOX"])
        self.assertFalse(item["unread"])

    def test_exact_message_projects_html_to_inert_plain_text(self):
        result = project_message(GRAPH_EXACT, MAILBOX, exact_body=True)
        displayed = "\n\n".join(result["body"])
        self.assertIn("Hi there.", displayed)
        self.assertIn("Listen to this track.", displayed)
        self.assertNotIn("secret()", displayed)
        self.assertNotIn("<script", displayed)
        self.assertNotIn(".x{}", displayed)
        self.assertNotIn("bodyHtml", result)
        self.assertEqual(result["providerMessageId"], MESSAGE_ID)

    def test_exact_plain_text_body_is_complete_not_preview(self):
        result = project_message(
            {**GRAPH_BODY, "body": {"contentType": "text", "content": "First.\n\nSecond."}},
            MAILBOX, exact_body=True,
        )
        self.assertEqual(result["body"], ["First.", "Second."])

    def test_provider_folder_and_mailbox_never_from_graph_payload(self):
        evil = {**GRAPH_BODY, "serverMailboxId": "other", "providerFolder": "Trash", "body": "untrusted"}
        projected = project_message(evil, MAILBOX)
        self.assertEqual(projected["serverMailboxId"], MAILBOX)
        self.assertEqual(projected["providerFolder"], "INBOX")
        self.assertEqual(projected["body"], [])

    def test_duplicate_graph_ids_are_rejected(self):
        with self.assertRaises(MicrosoftGraphError):
            project_inbox({"messages": [GRAPH_BODY, GRAPH_BODY], "hasMore": False}, MAILBOX)

    def test_invalid_graph_payload_shape_fails_closed(self):
        cases = (
            None, {"messages": "bad", "hasMore": False},
            {"messages": [], "hasMore": "false"},
            {"messages": [GRAPH_BODY], "hasMore": None},
            {"messages": [{**GRAPH_BODY, "unread": "false"}], "hasMore": False},
            {"messages": [{**GRAPH_BODY, "senderEmail": None}], "hasMore": False},
        )
        for data in cases:
            with self.subTest(data=data), self.assertRaises(MicrosoftGraphError):
                project_inbox(data, MAILBOX)

    def test_graph_error_never_contains_secret(self):
        with self.assertRaises(MicrosoftGraphError) as caught:
            project_message({**GRAPH_BODY, "receivedAt": "not a time"}, MAILBOX)
        self.assertEqual(caught.exception.code, "microsoft_graph_invalid_response")


class MicrosoftHTTPTests(unittest.TestCase):
    def test_inbox_route_uses_header_auth_selector_and_projects(self):
        req = _request({"mailboxId": MAILBOX, "limit": 7})
        with patch.object(
            access, "list_owned_microsoft_inbox", return_value=GRAPH_LIST,
        ) as fetch:
            response = microsoft_http.microsoft_inbox_response(req, mode="list")
        self.assertEqual(response.status, 200)
        fetch.assert_called_once()
        args, kwargs = fetch.call_args
        self.assertEqual(args[1], MAILBOX)
        self.assertEqual(kwargs, {"page_size": 7})
        self.assertIn(("Cookie", "session=placeholder"), args[0])
        self.assertEqual(_body(response)["messages"][0]["providerMessageId"], MESSAGE_ID)
        self.assertIn(("Cache-Control", "no-store"), response.headers)

    def test_exact_read_requires_authorized_message_id_and_projects(self):
        req = _request({"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID})
        with patch.object(
            access, "read_owned_microsoft_message", return_value=GRAPH_EXACT,
        ) as fetch:
            response = microsoft_http.microsoft_inbox_response(req, mode="exact")
        self.assertEqual(response.status, 200)
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[1:], (MAILBOX, MESSAGE_ID))
        self.assertIn("Listen to this track.", "".join(_body(response)["message"]["body"]))

    def test_exact_id_must_match_graph_response_id(self):
        req = _request({"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID})
        with patch.object(
            access, "read_owned_microsoft_message",
            return_value={**GRAPH_EXACT, "id": "other"},
        ):
            response = microsoft_http.microsoft_inbox_response(req, mode="exact")
        self.assertEqual(response.status, 502)
        self.assertNotIn("other", json.dumps(_body(response)))

    def test_end_to_end_owner_token_transport_projection_chain(self):
        from api.inboxes import microsoft_graph
        from api.inboxes.microsoft_token_authority import MicrosoftMailboxAuthority
        authority = MicrosoftMailboxAuthority(
            "owner@example.com", MAILBOX, "artist@example.com",
            "account-1", "x"*43,
        )
        request = _request({"mailboxId": MAILBOX, "limit": 3})
        with patch.object(
            access, "resolve_owned_microsoft_mailbox",
            return_value=authority,
        ) as resolve, patch.object(
            access, "ensure_owned_microsoft_access_token",
            return_value={"provider": "microsoft", "access_token": "server-only-token"},
        ) as credentials, patch.object(
            microsoft_graph, "list_microsoft_inbox",
            return_value=GRAPH_LIST,
        ) as graph:
            response = microsoft_http.microsoft_inbox_response(request, mode="list")
        self.assertEqual(response.status, 200)
        self.assertEqual(_body(response)["messages"][0]["providerMessageId"], MESSAGE_ID)
        self.assertNotIn("server-only-token", response.body.decode())
        self.assertEqual(resolve.call_count, 1)
        credentials.assert_called_once_with(authority)
        graph.assert_called_once_with("server-only-token", page_size=3)

    def test_no_graph_or_token_access_when_owner_resolution_rejects(self):
        from api.inboxes import microsoft_graph
        with patch.object(
            access, "resolve_owned_microsoft_mailbox",
            side_effect=access.MicrosoftMailboxAccessError("microsoft_unauthorized"),
        ), patch.object(
            access, "ensure_owned_microsoft_access_token",
        ) as credentials, patch.object(
            microsoft_graph, "list_microsoft_inbox",
        ) as graph:
            response = microsoft_http.microsoft_inbox_response(_request(), mode="list")
        self.assertEqual(response.status, 401)
        credentials.assert_not_called()
        graph.assert_not_called()

    def test_non_post_methods_are_denied_without_auth_or_network(self):
        for method in ("GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"):
            req = _request(command=method)
            with patch.object(access, "list_owned_microsoft_inbox") as fetch:
                response = microsoft_http.microsoft_inbox_response(req, mode="list")
            self.assertEqual(response.status, 405)
            self.assertEqual(dict(response.headers)["Allow"], "POST")
            fetch.assert_not_called()

    def test_unknown_request_fields_block_graph(self):
        bad = [
            {"mailboxId": MAILBOX, "access_token": "do-not-accept"},
            {"mailboxId": MAILBOX, "url": "https://evil.example"},
            {"mailboxId": MAILBOX, "limit": 20, "pageToken": "rawGraphLink"},
            {"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID},
        ]
        for value in bad:
            with self.subTest(value=value), patch.object(
                access, "list_owned_microsoft_inbox",
            ) as fetch:
                response = microsoft_http.microsoft_inbox_response(_request(value), mode="list")
                self.assertEqual(response.status, 400)
                fetch.assert_not_called()

    def test_invalid_page_limits_never_fetch(self):
        for limit in (0, 26, True, None, "20", 1.25, -1):
            req = _request({"mailboxId": MAILBOX, "limit": limit})
            with self.subTest(limit=limit), patch.object(access, "list_owned_microsoft_inbox") as fetch:
                response = microsoft_http.microsoft_inbox_response(req, mode="list")
                self.assertEqual(response.status, 400)
                fetch.assert_not_called()

    def test_invalid_mailbox_selector_never_fetch(self):
        for mailbox in ("", "../other", "mailbox?token=x", None, 42, "x"*161):
            req = _request({"mailboxId": mailbox})
            with self.subTest(mailbox=mailbox), patch.object(access, "list_owned_microsoft_inbox") as fetch:
                response = microsoft_http.microsoft_inbox_response(req, mode="list")
                self.assertEqual(response.status, 400)
                fetch.assert_not_called()

    def test_bad_exact_id_rejected_before_mailbox_or_token_resolution(self):
        for message_id in ("../other", "bad?q=steal", "bad#fragment", ".", "a"*513, ""):
            with self.subTest(message_id=message_id), patch.object(
                access, "read_owned_microsoft_message",
            ) as fetch:
                response = microsoft_http.microsoft_inbox_response(
                    _request({"mailboxId": MAILBOX, "providerMessageId": message_id}),
                    mode="exact",
                )
                self.assertEqual(response.status, 400)
                fetch.assert_not_called()

    def test_duplicate_content_length_is_denied(self):
        req = _request(header_pairs=(("Content-Length", "99"),))
        with patch.object(access, "list_owned_microsoft_inbox") as fetch:
            response = microsoft_http.microsoft_inbox_response(req, mode="list")
        self.assertEqual(response.status, 400)
        fetch.assert_not_called()

    def test_transfer_encoding_is_denied(self):
        req = _request(header_pairs=(("Transfer-Encoding", "chunked"),))
        with patch.object(access, "list_owned_microsoft_inbox") as fetch:
            response = microsoft_http.microsoft_inbox_response(req, mode="list")
        self.assertEqual(response.status, 400)
        fetch.assert_not_called()

    def test_oversized_request_is_denied(self):
        req = _request(raw=b" " * (microsoft_http.MAX_REQUEST_BYTES + 1))
        with patch.object(access, "list_owned_microsoft_inbox") as fetch:
            response = microsoft_http.microsoft_inbox_response(req, mode="list")
        self.assertEqual(response.status, 413)
        fetch.assert_not_called()

    def test_invalid_json_and_short_body_are_denied(self):
        for content in (b"not-json", b"[]", b"null", b""):
            request = _request(raw=content)
            with self.subTest(content=content), patch.object(access, "list_owned_microsoft_inbox") as fetch:
                response = microsoft_http.microsoft_inbox_response(request, mode="list")
                self.assertEqual(response.status, 400)
                fetch.assert_not_called()

    def test_cross_owner_access_never_projected(self):
        for code, status in (
            ("microsoft_unauthorized", 401),
            ("microsoft_mailbox_not_found", 404),
            ("microsoft_reconnect_required", 409),
            ("microsoft_mailbox_authority_unavailable", 503),
        ):
            with self.subTest(code=code), patch.object(
                access, "list_owned_microsoft_inbox",
                side_effect=access.MicrosoftMailboxAccessError(code),
            ):
                response = microsoft_http.microsoft_inbox_response(_request(), mode="list")
                self.assertEqual(response.status, status)
                self.assertNotIn(MAILBOX, json.dumps(_body(response)))

    def test_graph_provider_errors_are_safe(self):
        for code, status in (
            ("microsoft_graph_rate_limited", 429),
            ("microsoft_reconnect_required", 409),
            ("microsoft_graph_invalid_response", 502),
            ("microsoft_graph_unavailable", 503),
        ):
            with self.subTest(code=code), patch.object(
                access, "list_owned_microsoft_inbox",
                side_effect=MicrosoftGraphError(code),
            ):
                response = microsoft_http.microsoft_inbox_response(_request(), mode="list")
                self.assertEqual(response.status, status)

    def test_unknown_provider_exception_is_not_exposed(self):
        with patch.object(
            access, "list_owned_microsoft_inbox",
            side_effect=RuntimeError("private refresh token and recipient"),
        ):
            response = microsoft_http.microsoft_inbox_response(_request(), mode="list")
        self.assertEqual(response.status, 503)
        self.assertNotIn("private", response.body.decode())
        self.assertNotIn("recipient", response.body.decode())


if __name__ == "__main__":
    unittest.main()
