"""Archive is independently owned, bounded, and never projected as Inbox."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from api.inboxes import microsoft_graph, microsoft_graph_paging
from api.inboxes import microsoft_inbox_projection as projection
from api.inboxes import microsoft_mailbox_access as access
from api.inboxes import microsoft_http
from api.inboxes.test_microsoft_graph import (
    FakeOpener, TOKEN, graph_message,
)
from api.inboxes.test_microsoft_graph_paging import SequenceGraphOpener
from api.inboxes.test_microsoft_http import _body, _request

MAILBOX = "microsoft-0123456789abcdefabcd12345678"
MESSAGE_ID = "AAMkArchiveMessage.1/"
LINK = microsoft_graph.GRAPH_ROOT + "/me/mailFolders/archive/messages?$skiptoken=private"

def archive_message(**override):
    return graph_message(**{"id": MESSAGE_ID, **override})


class GraphArchiveTests(unittest.TestCase):
    def test_archive_page_uses_well_known_folder_and_correct_projection(self):
        opener = FakeOpener({"value": [archive_message()]})
        result = microsoft_graph.list_microsoft_archive(TOKEN, page_size=5, opener=opener)
        self.assertEqual(result["messages"][0]["id"], MESSAGE_ID)
        url = opener.calls[0][0].full_url
        self.assertTrue(url.startswith(
            microsoft_graph.GRAPH_ROOT + "/me/mailFolders/archive/messages?"
        ))
        self.assertEqual(opener.calls[0][0].get_header("Authorization"), "Bearer " + TOKEN)
        projected = projection.project_archive(result, MAILBOX)
        item = projected["messages"][0]
        self.assertEqual(item["providerFolder"], "ARCHIVE")
        self.assertEqual(item["labelIds"], ["ARCHIVE", "UNREAD"])
        self.assertNotIn("INBOX", item["labelIds"])
        self.assertEqual(projected["archiveMessageIds"], [MESSAGE_ID])
        self.assertNotIn("inboxUidSet", projected)

    def test_archive_continuation_never_fetches_inbox(self):
        opener = SequenceGraphOpener(
            {"value": [archive_message()], "@odata.nextLink": LINK},
            {"value": [archive_message(id="AAMkSecondArchive")]},
        )
        result = microsoft_graph.list_microsoft_archive(
            TOKEN, page_size=1, page=2, opener=opener,
        )
        self.assertEqual(result["messages"][0]["id"], "AAMkSecondArchive")
        self.assertEqual(len(opener.calls), 2)
        self.assertEqual(opener.calls[1].full_url, LINK)
        self.assertNotIn("skiptoken", str(result))

    def test_archive_cannot_follow_inbox_or_sent_continuation(self):
        for folder in ("inbox", "sentitems", "drafts"):
            bad = microsoft_graph.GRAPH_ROOT + (
                f"/me/mailFolders/{folder}/messages?$skiptoken=private"
            )
            opener = FakeOpener({
                "value": [archive_message()], "@odata.nextLink": bad,
            })
            with self.subTest(folder=folder), self.assertRaises(microsoft_graph.MicrosoftGraphError):
                microsoft_graph.list_microsoft_archive(
                    TOKEN, page_size=1, page=2, opener=opener,
                )
            self.assertEqual(len(opener.calls), 1)

    def test_exact_archive_message_scoped_to_archive_and_full_body(self):
        row = archive_message(body={
            "contentType": "html", "content": "<p>Archived body.</p>",
        })
        opener = FakeOpener(row)
        result = microsoft_graph.get_microsoft_archive_message(
            TOKEN, MESSAGE_ID, opener=opener,
        )
        self.assertEqual(result["id"], MESSAGE_ID)
        self.assertIn("archive/messages/", opener.calls[0][0].full_url)
        projected = projection.project_message(result, MAILBOX, exact_body=True, folder="ARCHIVE")
        self.assertEqual(projected["providerFolder"], "ARCHIVE")
        self.assertIn("Archived body.", projected["body"][0])
        self.assertIn("<p>Archived body.</p>", projected["bodyHtml"])

    def test_invalid_archive_limits_and_exact_ids_make_no_network_calls(self):
        opener = FakeOpener({"value": []})
        for page in (0, 5, "2", True):
            with self.subTest(page=page), self.assertRaises(microsoft_graph.MicrosoftGraphError):
                microsoft_graph.list_microsoft_archive(TOKEN, page=page, opener=opener)
        for mid in ("../other", "bad?#", ""):
            with self.subTest(id=mid), self.assertRaises(microsoft_graph.MicrosoftGraphError):
                microsoft_graph.get_microsoft_archive_message(TOKEN, mid, opener=opener)
        self.assertEqual(len(opener.calls), 0)


class OwnedArchiveTests(unittest.TestCase):
    def test_owner_rechecked_after_page_and_exact_read(self):
        captured = object()
        with patch.object(access, "_owned_token", return_value=(TOKEN, captured)), patch.object(
            access, "_verify_unchanged_owned_mailbox"
        ) as verify, patch.object(
            microsoft_graph, "list_microsoft_archive",
            return_value={"messages": [], "hasMore": False}
        ) as listing, patch.object(
            microsoft_graph, "get_microsoft_archive_message",
            return_value=archive_message()
        ) as exact:
            access.list_owned_microsoft_archive((), MAILBOX, page_size=25, page=2)
            access.read_owned_microsoft_archive_message((), MAILBOX, MESSAGE_ID)
            listing.assert_called_once_with(TOKEN, page_size=25, page=2)
            exact.assert_called_once_with(TOKEN, MESSAGE_ID)
            self.assertEqual(verify.call_count, 2)
            self.assertEqual(verify.call_args.args, ((), MAILBOX, captured))


class HttpArchiveTests(unittest.TestCase):
    def test_list_and_exact_owner_scoped_paths(self):
        data = {"messages": [microsoft_graph._message_summary(archive_message())], "hasMore": False}
        with patch.object(access, "list_owned_microsoft_archive", return_value=data):
            res = microsoft_http.microsoft_inbox_response(
                _request({"mailboxId": MAILBOX, "limit": 5}), mode="archive",
            )
        self.assertEqual(res.status, 200)
        self.assertEqual(_body(res)["messages"][0]["providerFolder"], "ARCHIVE")
        self.assertEqual(_body(res)["archiveMessageIds"], [MESSAGE_ID])

        exact = {
            **microsoft_graph._message_summary(archive_message()),
            "body": {"contentType": "text", "content": "Archived"},
        }
        with patch.object(access, "read_owned_microsoft_archive_message", return_value=exact):
            res = microsoft_http.microsoft_inbox_response(
                _request({"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID}),
                mode="archive_exact",
            )
        self.assertEqual(res.status, 200)
        self.assertEqual(_body(res)["message"]["providerFolder"], "ARCHIVE")

    def test_unknown_request_keys_or_wrong_methods_do_not_reach_graph(self):
        with patch.object(access, "list_owned_microsoft_archive") as listing:
            for data in (
                {"mailboxId": MAILBOX, "folder": "Inbox"},
                {"mailboxId": MAILBOX, "page": 5},
                {"mailboxId": MAILBOX, "nextLink": LINK},
            ):
                self.assertEqual(microsoft_http.microsoft_inbox_response(
                    _request(data), mode="archive",
                ).status, 400)
            self.assertEqual(microsoft_http.microsoft_inbox_response(
                _request({"mailboxId": MAILBOX}, command="GET"),
                mode="archive",
            ).status, 405)
            listing.assert_not_called()

    def test_archive_attachments_also_remain_fixed_folder(self):
        from api.inboxes import microsoft_attachments_graph
        from api.inboxes import microsoft_attachment_download
        from api.inboxes.test_microsoft_attachment_download import payload
        from api.inboxes.test_microsoft_attachments import file_row
        opener = FakeOpener({"value": [file_row()]})
        microsoft_attachments_graph.list_microsoft_attachments(
            TOKEN, MESSAGE_ID, folder="Archive", opener=opener,
        )
        self.assertIn("/mailFolders/archive/messages/", opener.calls[0][0].full_url)
        opener = FakeOpener(payload())
        microsoft_attachment_download.read_microsoft_file_attachment(
            TOKEN, MESSAGE_ID, payload()["id"], folder="Archive", opener=opener,
        )
        self.assertIn("/mailFolders/archive/messages/", opener.calls[0][0].full_url)
