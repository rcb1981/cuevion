"""Read-only Microsoft Deleted Items authorization, projection and isolation."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from api.inboxes import microsoft_deleted_graph as deleted
from api.inboxes import microsoft_graph, microsoft_inbox_projection as projection
from api.inboxes import microsoft_mailbox_access as access, microsoft_http
from api.inboxes.test_microsoft_graph import FakeOpener, TOKEN
from api.inboxes.test_microsoft_graph_paging import SequenceGraphOpener
from api.inboxes.test_microsoft_http import _request, _body

BOX = "microsoft-0123456789abcdefabcd12345678"
MID = "AAMkDeletedMessage.1/"
LINK = microsoft_graph.GRAPH_ROOT + "/me/mailFolders/deleteditems/messages?$skiptoken=private"


def message(**updates):
    return {
        "id": MID,
        "conversationId": "AAQkThread",
        "subject": "Old demo",
        "bodyPreview": "Archived content awaiting review.",
        "lastModifiedDateTime": "2026-10-14T22:00:00Z",
        "from": {"emailAddress": {"address": "sender@example.com", "name": "Artist"}},
        "toRecipients": [{"emailAddress": {"address": "manager@example.com", "name": "Manager"}}],
        "isRead": False,
        "isDraft": False,
        "hasAttachments": True,
        "body": {"contentType": "html", "content": "<p>Private mix</p><script>danger()</script>"},
        **updates,
    }


class DeletedGraphTests(unittest.TestCase):
    def test_deleted_page_is_fixed_folder_and_bounded(self):
        opener = FakeOpener({"value": [message()]})
        result = deleted.list_microsoft_deleted(TOKEN, page_size=5, opener=opener)
        self.assertEqual(result["messages"][0]["id"], MID)
        self.assertTrue(result["messages"][0]["unread"])
        request, timeout = opener.calls[0]
        self.assertTrue(request.full_url.startswith(
            microsoft_graph.GRAPH_ROOT + "/me/mailFolders/deleteditems/messages?"
        ))
        self.assertIn("lastModifiedDateTime", request.full_url)
        self.assertEqual(timeout, 20)
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertEqual(request.get_method(), "GET")
        projected = projection.project_deleted(result, BOX)
        row = projected["messages"][0]
        self.assertEqual(row["providerFolder"], "DELETED")
        self.assertEqual(row["labelIds"], ["DELETED", "UNREAD"])
        self.assertEqual(row["from"], "Artist <sender@example.com>")
        self.assertNotIn("INBOX", row["labelIds"])
        self.assertNotIn("SENT", row["labelIds"])
        self.assertEqual(projected["deletedMessageIds"], [MID])
        self.assertNotIn("nextLink", str(projected))

    def test_mixed_outgoing_and_unsent_deleted_messages(self):
        sent = deleted._normalize_deleted(message(
            **{"from": None}, isRead=True, toRecipients=[
                {"emailAddress": {"address": "to@example.com", "name": "To"}}
            ],
        ))
        item = projection.project_deleted_message(sent, BOX)
        self.assertEqual(item["sender"], "To: To <to@example.com>")
        self.assertEqual(item["from"], "")
        self.assertEqual(item["to"], "To <to@example.com>")
        self.assertFalse(item["unread"])
        draft = deleted._normalize_deleted(message(
            **{"from": None}, isDraft=True, isRead=False,
            toRecipients=[], subject=None, bodyPreview=None,
        ))
        row = projection.project_deleted_message(draft, BOX)
        self.assertEqual(row["sender"], "(Unknown sender)")
        self.assertEqual(row["labelIds"], ["DELETED"])
        self.assertFalse(row["unread"])
        self.assertEqual(row["subject"], "")

    def test_continuation_is_only_deleteditems_and_never_returned(self):
        opener = SequenceGraphOpener(
            {"value": [message()], "@odata.nextLink": LINK},
            {"value": [message(id="AAMkOlderDeleted")]},
        )
        result = deleted.list_microsoft_deleted(TOKEN, page_size=1, page=2, opener=opener)
        self.assertEqual(result["messages"][0]["id"], "AAMkOlderDeleted")
        self.assertEqual(opener.calls[1].full_url, LINK)
        self.assertEqual(len(opener.calls), 2)
        self.assertNotIn("skiptoken", str(result))

    def test_cross_folder_and_cross_origin_nextlinks_rejected_before_fetch(self):
        for link in (
            microsoft_graph.GRAPH_ROOT + "/me/mailFolders/inbox/messages?$skiptoken=private",
            microsoft_graph.GRAPH_ROOT + "/me/mailFolders/archive/messages?$skiptoken=private",
            microsoft_graph.GRAPH_ROOT + "/me/mailFolders/drafts/messages?$skiptoken=private",
            microsoft_graph.GRAPH_ROOT + "/me/mailFolders/sentitems/messages?$skiptoken=private",
            "https://evil.example/v1.0/me/mailFolders/deleteditems/messages?$skiptoken=private",
        ):
            opener = FakeOpener({"value": [message()], "@odata.nextLink": link})
            with self.subTest(link=link), self.assertRaises(microsoft_graph.MicrosoftGraphError):
                deleted.list_microsoft_deleted(TOKEN, page_size=1, page=2, opener=opener)
            self.assertEqual(len(opener.calls), 1)

    def test_exact_read_requires_folder_and_returns_safe_body(self):
        opener = FakeOpener(message())
        result = deleted.get_microsoft_deleted_message(TOKEN, MID, opener=opener)
        self.assertEqual(result["id"], MID)
        self.assertIn("/mailFolders/deleteditems/messages/", opener.calls[0][0].full_url)
        projected = projection.project_deleted_message(result, BOX, exact_body=True)
        self.assertIn("Private mix", "".join(projected["body"]))
        self.assertNotIn("danger()", "".join(projected["body"]))
        self.assertEqual(projected["providerFolder"], "DELETED")
        with self.assertRaises(microsoft_graph.MicrosoftGraphError):
            deleted.get_microsoft_deleted_message(
                TOKEN, MID, opener=FakeOpener(message(id="wrong")),
            )

    def test_invalid_graph_data_rejected_without_partial_results(self):
        for bad in (
            message(isRead="false"),
            message(isDraft="false"),
            message(hasAttachments=None),
            message(lastModifiedDateTime="invalid"),
            message(lastModifiedDateTime="2026-10-14T22:00:00"),
            message(id="../other"),
            message(**{"from": {"emailAddress": {"address": "invalid"}}}),
            message(toRecipients=[None]),
            message(toRecipients="not a list"),
            message(subject=12),
        ):
            with self.subTest(fields=str(bad)[:60]), self.assertRaises(microsoft_graph.MicrosoftGraphError):
                deleted.list_microsoft_deleted(TOKEN, opener=FakeOpener({"value": [bad]}))

    def test_invalid_page_and_message_id_make_no_graph_calls(self):
        opener = FakeOpener({"value": []})
        for bad in (0, 5, "2", True):
            with self.subTest(page=bad), self.assertRaises(microsoft_graph.MicrosoftGraphError):
                deleted.list_microsoft_deleted(TOKEN, page=bad, opener=opener)
        for bad in ("", "../other", "bad?#"):
            with self.subTest(id=bad), self.assertRaises(microsoft_graph.MicrosoftGraphError):
                deleted.get_microsoft_deleted_message(TOKEN, bad, opener=opener)
        self.assertEqual(len(opener.calls), 0)


class DeletedOwnerTests(unittest.TestCase):
    def test_owner_rechecked_after_list_and_exact_reads(self):
        captured = object()
        with patch.object(access, "_owned_token", return_value=(TOKEN, captured)), patch.object(
            access, "_verify_unchanged_owned_mailbox"
        ) as verify, patch.object(
            deleted, "list_microsoft_deleted", return_value={"messages": [], "hasMore": False}
        ) as listing, patch.object(
            deleted, "get_microsoft_deleted_message", return_value={"id": MID}
        ) as exact:
            access.list_owned_microsoft_deleted((), BOX, page_size=25, page=2)
            access.read_owned_microsoft_deleted((), BOX, MID)
            listing.assert_called_once_with(TOKEN, page_size=25, page=2)
            exact.assert_called_once_with(TOKEN, MID)
            self.assertEqual(verify.call_count, 2)
            self.assertEqual(verify.call_args.args, ((), BOX, captured))


class DeletedHttpTests(unittest.TestCase):
    def test_list_and_exact_are_authorized_owner_scoped(self):
        summary = deleted._normalize_deleted(message())
        with patch.object(
            access, "list_owned_microsoft_deleted",
            return_value={"messages": [summary], "hasMore": False},
        ):
            response = microsoft_http.microsoft_inbox_response(
                _request({"mailboxId": BOX, "limit": 5}), mode="deleted",
            )
        self.assertEqual(response.status, 200)
        self.assertEqual(_body(response)["messages"][0]["providerFolder"], "DELETED")
        self.assertEqual(_body(response)["deletedMessageIds"], [MID])
        exact = {**summary, "body": {"contentType": "text", "content": "Deleted body"}}
        with patch.object(access, "read_owned_microsoft_deleted", return_value=exact):
            response = microsoft_http.microsoft_inbox_response(
                _request({"mailboxId": BOX, "providerMessageId": MID}),
                mode="deleted_exact",
            )
        self.assertEqual(response.status, 200)
        self.assertEqual(_body(response)["message"]["providerFolder"], "DELETED")

    def test_reject_unsupported_methods_or_arbitrary_provider_urls(self):
        with patch.object(access, "list_owned_microsoft_deleted") as listing:
            for body in (
                {"mailboxId": BOX, "folder": "Inbox"},
                {"mailboxId": BOX, "page": 5},
                {"mailboxId": BOX, "nextLink": LINK},
                {"mailboxId": BOX, "access_token": TOKEN},
            ):
                self.assertEqual(microsoft_http.microsoft_inbox_response(
                    _request(body), mode="deleted",
                ).status, 400)
            self.assertEqual(microsoft_http.microsoft_inbox_response(
                _request({"mailboxId": BOX}, command="GET"), mode="deleted",
            ).status, 405)
            listing.assert_not_called()

    def test_deleted_item_attachments_remain_in_exact_folder(self):
        from api.inboxes import microsoft_attachments_graph, microsoft_attachment_download
        from api.inboxes.test_microsoft_attachments import file_row
        from api.inboxes.test_microsoft_attachment_download import payload
        metadata = FakeOpener({"value": [file_row()]})
        microsoft_attachments_graph.list_microsoft_attachments(
            TOKEN, MID, folder="Deleted", opener=metadata,
        )
        self.assertIn("/mailFolders/deleteditems/messages/", metadata.calls[0][0].full_url)
        file_opener = FakeOpener(payload())
        microsoft_attachment_download.read_microsoft_file_attachment(
            TOKEN, MID, payload()["id"], folder="Deleted", opener=file_opener,
        )
        self.assertIn("/mailFolders/deleteditems/messages/", file_opener.calls[0][0].full_url)
