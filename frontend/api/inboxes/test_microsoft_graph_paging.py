"""Read-only Microsoft paging: server-side continuation, folder isolation, bounded replay."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from api.inboxes import microsoft_graph, microsoft_graph_paging, microsoft_http
from api.inboxes import microsoft_sent_graph, microsoft_mailbox_access
from api.inboxes.test_microsoft_graph import FakeResponse, TOKEN, graph_message
from api.inboxes.test_microsoft_sent import row as sent_row


GRAPH = microsoft_graph.GRAPH_ROOT
INBOX_LINK = GRAPH + "/me/mailFolders/inbox/messages?$skiptoken=private-skip"
SENT_LINK = GRAPH + "/me/mailFolders/sentitems/messages?$skiptoken=private-skip"


class SequenceGraphOpener:
    def __init__(self, *payloads):
        self.payloads = payloads
        self.calls = []

    def __call__(self, request, *, timeout):
        self.calls.append(request)
        assert timeout == microsoft_graph.GRAPH_TIMEOUT_SECONDS
        if len(self.calls) > len(self.payloads):
            raise AssertionError("Unexpected extra Graph request")
        return FakeResponse(self.payloads[len(self.calls) - 1])


class MicrosoftContinuationSafetyTests(unittest.TestCase):
    def test_same_graph_host_and_fixed_folder_are_required(self):
        for expected_folder, accepted in (
            ("inbox", INBOX_LINK),
            ("sentitems", SENT_LINK),
            ("inbox", GRAPH + "/me/mailFolders('AAMkFolder')/messages?$skip=25"),
        ):
            result = microsoft_graph_paging.validated_microsoft_continuation_path(
                accepted, expected_folder=expected_folder,
            )
            self.assertTrue(result.startswith("/me/mailFolders") or result.startswith("/me/mailfolders"))

        for link in (
            "http://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$skip=25",
            "https://graph.microsoft.com.evil.example/v1.0/me/mailFolders/inbox/messages?$skip=25",
            "https://graph.microsoft.com:443/v1.0/me/mailFolders/inbox/messages?$skip=25",
            "https://user@graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$skip=25",
            "https://graph.microsoft.com/v1.0/me/mailFolders/sentitems/messages?$skip=25",
            "https://graph.microsoft.com/v1.0/me/mailFolders/drafts/messages?$skip=25",
            "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?limit=25",
            "https://graph.microsoft.com/v1.0/me/messages?$skip=25",
            "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages%2fextra?$skip=25",
            "https://graph.microsoft.com/v1.0/me/mailFolders/inbox%252Fextra/messages?$skip=25",
            "https://graph.microsoft.com/v1.0/me/mailFolders/../sentitems/messages?$skip=25",
            "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$skip=25#fragment",
        ):
            with self.subTest(link=link), self.assertRaises(
                microsoft_graph_paging.InvalidMicrosoftContinuation
            ):
                microsoft_graph_paging.validated_microsoft_continuation_path(
                    link, expected_folder="inbox",
                )

    def test_no_redirect_bearer_token_forward(self):
        handler = microsoft_graph._NoGraphRedirects()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Moved", {}, "https://evil.example"))

    def test_read_only_http_boundary_never_accepts_next_link(self):
        mailbox = "microsoft-0123456789abcdefabcd12345678"
        for payload in (
            {"mailboxId": mailbox, "limit": 25, "page": True},
            {"mailboxId": mailbox, "limit": 25, "page": 0},
            {"mailboxId": mailbox, "limit": 25, "page": 5},
            {"mailboxId": mailbox, "limit": 25, "page": "2"},
            {"mailboxId": mailbox, "nextLink": INBOX_LINK},
            {"mailboxId": mailbox, "page": 2, "skiptoken": "private-skip"},
        ):
            with self.subTest(payload=list(payload)), self.assertRaises(ValueError):
                microsoft_http._validate_payload(payload, mode="list")
        self.assertEqual(
            microsoft_http._validate_payload({"mailboxId": mailbox, "page": 2}, mode="list"),
            (mailbox, {"limit": 20, "page": 2}),
        )

    def test_outbound_graph_host_and_token_stay_server_owned(self):
        first = {"value": [graph_message()], "@odata.nextLink": INBOX_LINK}
        second = {"value": [graph_message(id="AAMkOther123", isRead=True)]}
        opener = SequenceGraphOpener(first, second)
        response = microsoft_graph.list_microsoft_inbox(TOKEN, page_size=1, page=2, opener=opener)
        self.assertEqual([entry["id"] for entry in response["messages"]], ["AAMkOther123"])
        self.assertFalse(response["hasMore"])
        self.assertEqual(len(opener.calls), 2)
        self.assertEqual(opener.calls[1].full_url, INBOX_LINK)
        self.assertEqual(opener.calls[1].get_header("Authorization"), "Bearer " + TOKEN)
        self.assertNotIn("nextLink", response)
        self.assertNotIn("$skiptoken", str(response))

    def test_exhaustion_before_requested_page_returns_empty(self):
        opener = SequenceGraphOpener({"value": [graph_message()]})
        response = microsoft_graph.list_microsoft_inbox(TOKEN, page_size=1, page=4, opener=opener)
        self.assertEqual(response, {"messages": [], "hasMore": False})
        self.assertEqual(len(opener.calls), 1)

    def test_sent_folder_replay_never_fetches_inbox(self):
        opener = SequenceGraphOpener(
            {"value": [sent_row()], "@odata.nextLink": SENT_LINK},
            {"value": [sent_row(id="AAMkOlderMessage")]},
        )
        response = microsoft_sent_graph.list_microsoft_sent(TOKEN, page_size=1, page=2, opener=opener)
        self.assertEqual(response["messages"][0]["id"], "AAMkOlderMessage")
        self.assertEqual(opener.calls[1].full_url, SENT_LINK)
        self.assertNotIn("$skiptoken", str(response))

    def test_malicious_page_two_link_fails_before_second_request(self):
        opener = SequenceGraphOpener({
            "value": [graph_message()],
            "@odata.nextLink": SENT_LINK,
        })
        with self.assertRaises(microsoft_graph.MicrosoftGraphError):
            microsoft_graph.list_microsoft_inbox(TOKEN, page_size=1, page=2, opener=opener)
        self.assertEqual(len(opener.calls), 1)

    def test_ownership_is_rechecked_after_paged_fetch(self):
        captured = object()
        with patch.object(microsoft_mailbox_access, "_owned_token", return_value=(TOKEN, captured)), patch.object(
            microsoft_mailbox_access, "_verify_unchanged_owned_mailbox"
        ) as check, patch.object(
            microsoft_graph, "list_microsoft_inbox", return_value={"messages": [], "hasMore": False}
        ) as graph:
            result = microsoft_mailbox_access.list_owned_microsoft_inbox((), "microsoft-box", page_size=25, page=2)
            self.assertFalse(result["hasMore"])
            graph.assert_called_once_with(TOKEN, page_size=25, page=2)
            check.assert_called_once_with((), "microsoft-box", captured)


if __name__ == "__main__":
    unittest.main()
