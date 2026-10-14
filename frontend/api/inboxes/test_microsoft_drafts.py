"""Owner-scoped read-only Microsoft Drafts, isolated from Sent/Inbox."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from api.inboxes import microsoft_drafts_graph as drafts
from api.inboxes import microsoft_graph, microsoft_mailbox_access as owned
from api.inboxes import microsoft_inbox_projection as projection, microsoft_http
from api.inboxes.test_microsoft_graph import FakeOpener, TOKEN
from api.inboxes.test_microsoft_graph_paging import SequenceGraphOpener
from api.inboxes.test_microsoft_http import _request, _body

BOX = "microsoft-0123456789abcdefabcd12345678"
ID = "AAMkDraftMessage.1/"
LINK = microsoft_graph.GRAPH_ROOT + "/me/mailFolders/drafts/messages?$skiptoken=private"


def message(**override):
    return {
        "id": ID,
        "conversationId": "AAQkThread",
        "subject": "Unsent draft",
        "bodyPreview": "Please review...",
        "lastModifiedDateTime": "2026-10-14T20:00:00Z",
        "toRecipients": [
            {"emailAddress": {"address": "artist@example.com", "name": "Artist"}},
        ],
        "hasAttachments": True,
        "isDraft": True,
        "body": {"contentType": "html", "content": "<p>Unsent <b>message</b></p>"},
        **override,
    }


class DraftGraphTests(unittest.TestCase):
    def test_fixed_drafts_folder_and_stable_summary(self):
        opener = FakeOpener({"value": [message()]})
        data = drafts.list_microsoft_drafts(TOKEN, page_size=5, opener=opener)
        self.assertEqual(data["messages"][0]["id"], ID)
        self.assertEqual(data["messages"][0]["toDisplay"], "Artist <artist@example.com>")
        self.assertEqual(data["messages"][0]["modifiedAt"], "2026-10-14T20:00:00+00:00")
        request, timeout = opener.calls[0]
        self.assertTrue(request.full_url.startswith(
            microsoft_graph.GRAPH_ROOT + "/me/mailFolders/drafts/messages?"
        ))
        self.assertIn("lastModifiedDateTime", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertEqual(timeout, 20)
        projected = projection.project_drafts(data, BOX)
        first = projected["messages"][0]
        self.assertEqual(first["providerFolder"], "DRAFTS")
        self.assertEqual(first["labelIds"], ["DRAFTS"])
        self.assertFalse(first["unread"])
        self.assertEqual(first["from"], "")
        self.assertEqual(projected["draftMessageIds"], [ID])
        self.assertNotIn("inboxUidSet", projected)

    def test_unaddressed_draft_and_empty_subject_are_supported(self):
        data = drafts.list_microsoft_drafts(
            TOKEN,
            opener=FakeOpener({"value": [message(
                subject=None, bodyPreview=None, toRecipients=[],
            )]}),
        )
        entry = data["messages"][0]
        self.assertEqual(entry["subject"], "")
        self.assertEqual(entry["preview"], "")
        self.assertEqual(entry["toDisplay"], "(No recipients)")

    def test_graph_continuation_follows_only_drafts(self):
        opener = SequenceGraphOpener(
            {"value": [message()], "@odata.nextLink": LINK},
            {"value": [message(id="AAMkOlderDraft")]},
        )
        data = drafts.list_microsoft_drafts(TOKEN, page=2, page_size=1, opener=opener)
        self.assertEqual(data["messages"][0]["id"], "AAMkOlderDraft")
        self.assertEqual(len(opener.calls), 2)
        self.assertEqual(opener.calls[1].full_url, LINK)
        self.assertNotIn("skiptoken", str(data))
        for folder in ("inbox", "archive", "sentitems", "deleteditems"):
            other = microsoft_graph.GRAPH_ROOT + (
                f"/me/mailFolders/{folder}/messages?$skiptoken=private"
            )
            wrong = FakeOpener({"value": [message()], "@odata.nextLink": other})
            with self.subTest(folder=folder), self.assertRaises(microsoft_graph.MicrosoftGraphError):
                drafts.list_microsoft_drafts(TOKEN, page=2, page_size=1, opener=wrong)
            self.assertEqual(len(wrong.calls), 1)

    def test_other_folder_or_non_draft_data_fails_closed(self):
        opener = FakeOpener({"value": [message(isDraft=False)]})
        with self.assertRaises(microsoft_graph.MicrosoftGraphError):
            drafts.list_microsoft_drafts(TOKEN, opener=opener)
        invalid = (
            message(isDraft=None), message(isDraft="true"), message(id="bad?#"),
            message(lastModifiedDateTime="wrong"),
            message(toRecipients=[None]),
            message(toRecipients=[{"emailAddress": {"address": "wrong"}}]),
            message(hasAttachments="false"),
        )
        for value in invalid:
            with self.subTest(value=str(value)[:50]), self.assertRaises(microsoft_graph.MicrosoftGraphError):
                drafts.list_microsoft_drafts(
                    TOKEN, opener=FakeOpener({"value": [value]})
                )

    def test_exact_draft_read_and_no_mailbox_mutation(self):
        opener = FakeOpener(message())
        result = drafts.get_microsoft_draft(TOKEN, ID, opener=opener)
        self.assertEqual(result["body"]["contentType"], "html")
        self.assertIn("Unsent", result["body"]["content"])
        self.assertEqual(opener.calls[0][0].get_method(), "GET")
        self.assertIn("/mailFolders/drafts/messages/", opener.calls[0][0].full_url)
        projected = projection.project_draft_message(result, BOX, exact_body=True)
        self.assertEqual(projected["providerFolder"], "DRAFTS")
        self.assertIn("Unsent", "".join(projected["body"]))
        with self.assertRaises(microsoft_graph.MicrosoftGraphError):
            drafts.get_microsoft_draft(TOKEN, ID, opener=FakeOpener(message(isDraft=False)))

    def test_invalid_pages_and_message_ids_do_not_make_graph_requests(self):
        opener = FakeOpener({"value": []})
        for bad in (0, 5, "2", True):
            with self.assertRaises(microsoft_graph.MicrosoftGraphError):
                drafts.list_microsoft_drafts(TOKEN, page=bad, opener=opener)
        for bad in ("", "../other", "bad?#"):
            with self.assertRaises(microsoft_graph.MicrosoftGraphError):
                drafts.get_microsoft_draft(TOKEN, bad, opener=opener)
        self.assertEqual(len(opener.calls), 0)


class OwnedDraftTests(unittest.TestCase):
    def test_owner_rechecked_after_pagination_and_exact_read(self):
        captured = object()
        with patch.object(owned, "_owned_token", return_value=(TOKEN, captured)), patch.object(
            owned, "_verify_unchanged_owned_mailbox"
        ) as verify, patch.object(
            drafts, "list_microsoft_drafts",
            return_value={"messages": [], "hasMore": False}
        ) as listing, patch.object(
            drafts, "get_microsoft_draft", return_value={"id": ID}
        ) as exact:
            owned.list_owned_microsoft_drafts((), BOX, page_size=25, page=2)
            owned.read_owned_microsoft_draft((), BOX, ID)
            listing.assert_called_once_with(TOKEN, page_size=25, page=2)
            exact.assert_called_once_with(TOKEN, ID)
            self.assertEqual(verify.call_count, 2)
            self.assertEqual(verify.call_args.args, ((), BOX, captured))


class DraftHttpTests(unittest.TestCase):
    def test_list_and_exact_use_owner_authority(self):
        summary = drafts._normalize_draft(message())
        with patch.object(
            owned, "list_owned_microsoft_drafts",
            return_value={"messages": [summary], "hasMore": False},
        ):
            result = microsoft_http.microsoft_inbox_response(
                _request({"mailboxId": BOX, "page": 1}), mode="drafts",
            )
        self.assertEqual(result.status, 200)
        self.assertEqual(_body(result)["messages"][0]["providerFolder"], "DRAFTS")
        self.assertEqual(_body(result)["draftMessageIds"], [ID])
        exact = {**summary, "body": {"contentType": "text", "content": "Unsent"}}
        with patch.object(owned, "read_owned_microsoft_draft", return_value=exact):
            result = microsoft_http.microsoft_inbox_response(
                _request({"mailboxId": BOX, "providerMessageId": ID}), mode="draft_exact",
            )
        self.assertEqual(result.status, 200)
        self.assertEqual(_body(result)["message"]["providerFolder"], "DRAFTS")

    def test_wrong_methods_and_arbitrary_graph_paths_are_rejected(self):
        with patch.object(owned, "list_owned_microsoft_drafts") as fetch:
            for invalid in (
                {"mailboxId": BOX, "folder": "Inbox"},
                {"mailboxId": BOX, "page": 5},
                {"mailboxId": BOX, "nextLink": LINK},
                {"mailboxId": BOX, "access_token": TOKEN},
            ):
                self.assertEqual(microsoft_http.microsoft_inbox_response(
                    _request(invalid), mode="drafts",
                ).status, 400)
            self.assertEqual(microsoft_http.microsoft_inbox_response(
                _request({"mailboxId": BOX}, command="GET"), mode="drafts",
            ).status, 405)
            fetch.assert_not_called()

    def test_draft_attachments_stay_in_folder(self):
        from api.inboxes import microsoft_attachments_graph, microsoft_attachment_download
        from api.inboxes.test_microsoft_attachments import file_row
        from api.inboxes.test_microsoft_attachment_download import payload
        metadata = FakeOpener({"value": [file_row()]})
        microsoft_attachments_graph.list_microsoft_attachments(
            TOKEN, ID, folder="Drafts", opener=metadata,
        )
        self.assertIn("/mailFolders/drafts/messages/", metadata.calls[0][0].full_url)
        file_opener = FakeOpener(payload())
        microsoft_attachment_download.read_microsoft_file_attachment(
            TOKEN, ID, payload()["id"], folder="Drafts", opener=file_opener,
        )
        self.assertIn("/mailFolders/drafts/messages/", file_opener.calls[0][0].full_url)
