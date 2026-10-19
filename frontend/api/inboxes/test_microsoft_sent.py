"""Isolated Microsoft Graph Sent Items read-only transport and projection tests."""
from __future__ import annotations

import io
import json
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from api.inboxes import microsoft_sent_graph as sent
from api.inboxes import microsoft_inbox_projection as projection
from api.inboxes import microsoft_mailbox_access as access
from api.inboxes import microsoft_http
from api.inboxes.microsoft_graph import GRAPH_ROOT, MicrosoftGraphError
from api.inboxes.test_microsoft_http import MAILBOX, _request, _body
from api.inboxes.test_microsoft_graph import FakeOpener

TOKEN = "secure-microsoft-test-access-token-123"
MID = "AAMkAGVm.987/"


def row(**changes):
    return {
        "id": MID,
        "conversationId": "AAQkABc123",
        "subject": "Confirmed release",
        "from": {"emailAddress": {"name": "DJ", "address": "dj@example.com"}},
        "toRecipients": [{"emailAddress": {"name": "Manager", "address": "manager@example.com"}}],
        "bodyPreview": "Thanks for the feedback.",
        "sentDateTime": "2026-10-09T15:00:00Z",
        "hasAttachments": False,
        "body": {"contentType": "html", "content": "<p>Sent message!</p><script>evil()</script>"},
        **changes,
    }


class SentGraphTests(unittest.TestCase):
    def test_sent_listing_uses_well_known_fixed_folder(self):
        opener = FakeOpener({"value": [row()], "@odata.nextLink": GRAPH_ROOT + sent.SENT_FOLDER_PATH + "?$skiptoken=next"})
        result = sent.list_microsoft_sent(TOKEN, page_size=5, opener=opener)
        self.assertEqual(len(result["messages"]), 1)
        self.assertTrue(result["hasMore"])
        self.assertEqual(result["messages"][0]["toDisplay"], "Manager <manager@example.com>")
        self.assertEqual(result["messages"][0]["fromDisplay"], "DJ <dj@example.com>")
        self.assertEqual(result["messages"][0]["sentAt"], "2026-10-09T15:00:00+00:00")
        request, timeout = opener.calls[0]
        self.assertEqual(timeout, 20)
        self.assertEqual(request.get_method(), "GET")
        self.assertTrue(request.full_url.startswith(GRAPH_ROOT + sent.SENT_FOLDER_PATH + "?"))
        self.assertIn("sentDateTime+desc", request.full_url)
        self.assertIn("toRecipients", request.full_url)
        self.assertNotIn("access_token", request.full_url)
        self.assertEqual(request.headers["Prefer"], 'IdType="ImmutableId"')

    def test_sent_exact_is_folder_scoped_and_id_encoded(self):
        opener = FakeOpener(row())
        result = sent.get_microsoft_sent_message(TOKEN, MID, opener=opener)
        self.assertEqual(result["id"], MID)
        self.assertEqual(result["body"]["contentType"], "html")
        self.assertTrue(opener.calls[0][0].full_url.startswith(
            GRAPH_ROOT + sent.SENT_FOLDER_PATH + "/AAMkAGVm.987%2F?"
        ))
        self.assertNotIn("/me/messages/", opener.calls[0][0].full_url)

    def test_undisclosed_recipient_sent_record_is_supported(self):
        result = sent.list_microsoft_sent(TOKEN, opener=FakeOpener({"value": [row(toRecipients=[])]}))
        self.assertEqual(result["messages"][0]["toDisplay"], "Undisclosed recipients")

    def test_sent_multiple_recipients_are_bounded(self):
        recipients = [
            {"emailAddress": {"address": f"person{i}@example.com", "name": f"Person {i}"}}
            for i in range(23)
        ]
        result = sent.list_microsoft_sent(TOKEN, opener=FakeOpener({"value": [row(toRecipients=recipients)]}))
        display = result["messages"][0]["toDisplay"]
        self.assertIn("(+15 more)", display)
        self.assertLessEqual(len(display), 2048)

    def test_invalid_sent_row_fails_closed(self):
        invalid = (
            row(sentDateTime="invalid"), row(sentDateTime=None),
            row(toRecipients=None), row(toRecipients="wrong"),
            row(toRecipients=[{"emailAddress": {"address": "invalid"}}]),
            row(toRecipients=[None]), row(**{"from": None}),
            row(hasAttachments="false"), row(subject=None),
            row(conversationId="with?#"), row(toRecipients=[{}]),
        )
        for data in invalid:
            with self.subTest(keys=str(list(data)[:3])), self.assertRaises(MicrosoftGraphError):
                sent.list_microsoft_sent(TOKEN, opener=FakeOpener({"value": [data]}))

    def test_cross_origin_and_other_folder_nextlink_rejected_without_fetching(self):
        for link in (
            "https://evil.example/me/mailFolders/sentitems/messages?$skiptoken=x",
            GRAPH_ROOT + "/me/mailFolders/inbox/messages?$skiptoken=x",
            GRAPH_ROOT + "/me/messages?$skiptoken=x",
        ):
            with self.subTest(link=link), self.assertRaises(MicrosoftGraphError):
                sent.list_microsoft_sent(
                    TOKEN, opener=FakeOpener({"value": [], "@odata.nextLink": link})
                )

    def test_invalid_limit_no_network(self):
        for limit in (0, 26, True, "20", None):
            opener = FakeOpener({"value": []})
            with self.subTest(limit=limit), self.assertRaises(MicrosoftGraphError):
                sent.list_microsoft_sent(TOKEN, page_size=limit, opener=opener)
            self.assertFalse(opener.calls)

    def test_duplicate_graph_ids_rejected(self):
        with self.assertRaises(MicrosoftGraphError):
            sent.list_microsoft_sent(TOKEN, opener=FakeOpener({"value": [row(), row()]}))

    def test_missing_body_and_mismatched_exact_id_rejected(self):
        for data in (row(body=None), row(id="different"), row(body={"contentType": "image", "content": "?"})):
            with self.subTest(data=data.get("id")), self.assertRaises(MicrosoftGraphError):
                sent.get_microsoft_sent_message(TOKEN, MID, opener=FakeOpener(data))

    def test_traversal_id_no_network(self):
        for mid in ("", "../messages", "bad?query=1", "bad#fragment", "bad space", "A"*513):
            opener = FakeOpener(row())
            with self.subTest(mid=mid), self.assertRaises(MicrosoftGraphError):
                sent.get_microsoft_sent_message(TOKEN, mid, opener=opener)
            self.assertFalse(opener.calls)

    def test_provider_failure_sanitized(self):
        for code, expected in ((401, "microsoft_reconnect_required"), (429, "microsoft_graph_rate_limited")):
            opener = FakeOpener(HTTPError(GRAPH_ROOT, code, "private", {}, io.BytesIO(b"private-data")))
            with self.subTest(code=code), self.assertRaises(MicrosoftGraphError) as caught:
                sent.list_microsoft_sent(TOKEN, opener=opener)
            self.assertEqual(caught.exception.code, expected)
            self.assertNotIn("private", str(caught.exception))


class SentProjectionTests(unittest.TestCase):
    def test_sent_projection_has_no_inbox_labels_or_unread_state(self):
        data = sent.list_microsoft_sent(TOKEN, opener=FakeOpener({"value": [row()]}))
        projected = projection.project_sent(data, MAILBOX)
        entry = projected["messages"][0]
        self.assertEqual(entry["providerFolder"], "SENT")
        self.assertEqual(entry["labelIds"], ["SENT"])
        self.assertFalse(entry["unread"])
        self.assertEqual(entry["sender"], "To: Manager <manager@example.com>")
        self.assertEqual(entry["to"], "Manager <manager@example.com>")
        self.assertEqual(entry["from"], "DJ <dj@example.com>")
        self.assertEqual(entry["createdAt"], "2026-10-09T15:00:00+00:00")
        self.assertEqual(entry["body"], [])
        self.assertEqual(projected["sentMessageIds"], [MID])
        self.assertNotIn("inboxUidSet", projected)

    def test_sent_html_exact_is_inert(self):
        detail = sent.get_microsoft_sent_message(TOKEN, MID, opener=FakeOpener(row()))
        projected = projection.project_sent_message(detail, MAILBOX, exact_body=True)
        text = "\n".join(projected["body"])
        self.assertIn("Sent message!", text)
        self.assertNotIn("evil()", text)
        self.assertIn("<p>Sent message!</p>", projected["bodyHtml"])
        self.assertNotIn("evil()", projected["bodyHtml"])
        self.assertNotIn("<script", projected["bodyHtml"])

    def test_malformed_projection_not_published(self):
        for invalid in (
            None, {"messages": "bad", "hasMore": False},
            {"messages": [], "hasMore": "false"},
            {"messages": [dict(id="x")], "hasMore": False},
        ):
            with self.assertRaises(MicrosoftGraphError):
                projection.project_sent(invalid, MAILBOX)


class SentHTTPTests(unittest.TestCase):
    def test_list_route_uses_scoped_owner_then_projects(self):
        normalized = sent.list_microsoft_sent(TOKEN, opener=FakeOpener({"value": [row()]}))
        with patch.object(access, "list_owned_microsoft_sent", return_value=normalized) as fetch:
            resp = microsoft_http.microsoft_inbox_response(
                _request({"mailboxId": MAILBOX, "limit": 4}), mode="sent",
            )
        self.assertEqual(resp.status, 200)
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[1], MAILBOX)
        self.assertEqual(fetch.call_args.kwargs, {"page_size": 4})
        self.assertEqual(_body(resp)["messages"][0]["providerFolder"], "SENT")
        self.assertIn(("Cache-Control", "no-store"), resp.headers)

    def test_exact_route_uses_scoped_owner(self):
        detail = sent.get_microsoft_sent_message(TOKEN, MID, opener=FakeOpener(row()))
        with patch.object(access, "read_owned_microsoft_sent_message", return_value=detail) as fetch:
            resp = microsoft_http.microsoft_inbox_response(
                _request({"mailboxId": MAILBOX, "providerMessageId": MID}), mode="sent_exact",
            )
        self.assertEqual(resp.status, 200)
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[1:], (MAILBOX, MID))
        self.assertEqual(_body(resp)["message"]["providerFolder"], "SENT")

    def test_unknown_fields_and_malformed_ids_never_contact_graph(self):
        for mode, body in (
            ("sent", {"mailboxId": MAILBOX, "limit": 5, "url": "https://evil.example"}),
            ("sent_exact", {"mailboxId": MAILBOX, "providerMessageId": "../inbox"}),
            ("sent_exact", {"mailboxId": MAILBOX, "providerMessageId": MID, "folder": "inbox"}),
            ("sent", {"mailboxId": MAILBOX, "limit": 30}),
        ):
            with self.subTest(mode=mode), patch.object(access, "list_owned_microsoft_sent") as list_fetch, patch.object(access, "read_owned_microsoft_sent_message") as detail_fetch:
                resp = microsoft_http.microsoft_inbox_response(_request(body), mode=mode)
                self.assertEqual(resp.status, 400)
                list_fetch.assert_not_called()
                detail_fetch.assert_not_called()

    def test_invalid_exact_identity_is_never_published(self):
        detail = sent.get_microsoft_sent_message(TOKEN, MID, opener=FakeOpener(row()))
        with patch.object(access, "read_owned_microsoft_sent_message", return_value={**detail, "id": "wrong"}):
            resp = microsoft_http.microsoft_inbox_response(
                _request({"mailboxId": MAILBOX, "providerMessageId": MID}), mode="sent_exact",
            )
        self.assertEqual(resp.status, 502)

    def test_sent_list_checks_same_owner_before_and_after_graph(self):
        from api.inboxes import microsoft_sent_graph
        from api.inboxes.microsoft_token_authority import MicrosoftMailboxAuthority
        authority = MicrosoftMailboxAuthority(
            "owner@example.com", MAILBOX, "artist@example.com",
            "graph-account", "z"*43,
        )
        normalized = {"messages": [], "hasMore": False}
        with patch.object(
            access, "resolve_owned_microsoft_mailbox", return_value=authority,
        ) as auth, patch.object(
            access, "ensure_owned_microsoft_access_token",
            return_value={"provider": "microsoft", "access_token": TOKEN},
        ) as token, patch.object(
            microsoft_sent_graph, "list_microsoft_sent", return_value=normalized,
        ) as graph:
            result = access.list_owned_microsoft_sent((("Cookie", "test"),), MAILBOX, page_size=3)
        self.assertEqual(result, normalized)
        self.assertEqual(auth.call_count, 2)
        token.assert_called_once_with(authority)
        graph.assert_called_once_with(TOKEN, page_size=3)

    def test_sent_exact_refuses_stale_reconnect_during_graph_fetch(self):
        from api.inboxes import microsoft_sent_graph
        from api.inboxes.microsoft_token_authority import MicrosoftMailboxAuthority
        authority = MicrosoftMailboxAuthority(
            "owner@example.com", MAILBOX, "artist@example.com",
            "graph-account", "z"*43,
        )
        with patch.object(
            access, "resolve_owned_microsoft_mailbox",
            side_effect=[authority, access.MicrosoftMailboxAccessError("microsoft_reconnect_required")],
        ) as auth, patch.object(
            access, "ensure_owned_microsoft_access_token",
            return_value={"provider": "microsoft", "access_token": TOKEN},
        ), patch.object(
            microsoft_sent_graph, "get_microsoft_sent_message",
            return_value={"id": MID, "body": {"contentType": "text", "content": "PRIVATE"}},
        ) as graph:
            with self.assertRaises(access.MicrosoftMailboxAccessError) as caught:
                access.read_owned_microsoft_sent_message((("Cookie", "test"),), MAILBOX, MID)
        self.assertEqual(caught.exception.code, "microsoft_reconnect_required")
        self.assertEqual(auth.call_count, 2)
        graph.assert_called_once_with(TOKEN, MID)

    def test_sent_unauthorized_does_not_request_token_or_graph(self):
        from api.inboxes import microsoft_sent_graph
        with patch.object(
            access, "resolve_owned_microsoft_mailbox",
            side_effect=access.MicrosoftMailboxAccessError("microsoft_unauthorized"),
        ), patch.object(
            access, "ensure_owned_microsoft_access_token",
        ) as credentials, patch.object(
            microsoft_sent_graph, "list_microsoft_sent",
        ) as graph:
            with self.assertRaises(access.MicrosoftMailboxAccessError):
                access.list_owned_microsoft_sent((("Cookie", "test"),), MAILBOX)
        credentials.assert_not_called()
        graph.assert_not_called()

    def test_sent_http_get_head_patch_delete_denied(self):
        for mode in ("sent", "sent_exact"):
            for method in ("GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"):
                with self.subTest(mode=mode, method=method), patch.object(
                    access, "list_owned_microsoft_sent",
                ) as list_read, patch.object(
                    access, "read_owned_microsoft_sent_message",
                ) as detail_read:
                    resp = microsoft_http.microsoft_inbox_response(
                        _request(command=method), mode=mode,
                    )
                    self.assertEqual(resp.status, 405)
                    list_read.assert_not_called()
                    detail_read.assert_not_called()

    def test_sent_response_owner_denial_safe(self):
        with patch.object(access, "list_owned_microsoft_sent", side_effect=access.MicrosoftMailboxAccessError("microsoft_unauthorized")):
            resp = microsoft_http.microsoft_inbox_response(
                _request({"mailboxId": MAILBOX}), mode="sent",
            )
        self.assertEqual(resp.status, 401)
        self.assertNotIn(MAILBOX, json.dumps(_body(resp)))


if __name__ == "__main__":
    unittest.main()
