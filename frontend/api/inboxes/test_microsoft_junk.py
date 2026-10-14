"""Read-only Junk Email: fixed Graph folder, owner checks, and safe projection."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from api.inboxes import microsoft_graph as graph
from api.inboxes import microsoft_mailbox_access as access
from api.inboxes import microsoft_http
from api.inboxes import microsoft_inbox_projection as projection
from api.inboxes.test_microsoft_graph import FakeOpener, TOKEN, graph_message
from api.inboxes.test_microsoft_graph_paging import SequenceGraphOpener
from api.inboxes.test_microsoft_http import _request, _body

BOX = "microsoft-0123456789abcdefabcd12345678"
MID = "AAMkJunkMessage.123/"
LINK = graph.GRAPH_ROOT + "/me/mailFolders/junkemail/messages?$skiptoken=private"


def junk_message(**changes):
    return graph_message(**{"id": MID, **changes})


class JunkGraphTests(unittest.TestCase):
    def test_fixed_junk_folder_projection_not_inbox(self):
        opener = FakeOpener({"value": [junk_message()]})
        result = graph.list_microsoft_junk(TOKEN, page_size=5, opener=opener)
        request, timeout = opener.calls[0]
        self.assertTrue(request.full_url.startswith(
            graph.GRAPH_ROOT + "/me/mailFolders/junkemail/messages?"
        ))
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(timeout, graph.GRAPH_TIMEOUT_SECONDS)
        projected = projection.project_junk(result, BOX)
        msg = projected["messages"][0]
        self.assertEqual(msg["providerFolder"], "JUNK")
        self.assertIn("JUNK", msg["labelIds"])
        self.assertNotIn("INBOX", msg["labelIds"])
        self.assertNotIn("SENT", msg["labelIds"])
        self.assertEqual(projected["junkMessageIds"], [MID])
        self.assertNotIn("inboxUidSet", projected)
        self.assertNotIn("nextLink", str(projected))

    def test_junk_next_link_remains_private_and_folder_scoped(self):
        opener = SequenceGraphOpener(
            {"value": [junk_message()], "@odata.nextLink": LINK},
            {"value": [junk_message(id="AAMkNextJunk")]},
        )
        result = graph.list_microsoft_junk(TOKEN, page=2, page_size=1, opener=opener)
        self.assertEqual(result["messages"][0]["id"], "AAMkNextJunk")
        self.assertEqual(opener.calls[1].full_url, LINK)
        self.assertEqual(len(opener.calls), 2)
        self.assertNotIn("skiptoken", str(result))

    def test_inbox_archive_drafts_sent_and_deleted_next_links_rejected(self):
        for folder in ("inbox", "archive", "drafts", "sentitems", "deleteditems"):
            link = graph.GRAPH_ROOT + f"/me/mailFolders/{folder}/messages?$skiptoken=private"
            opener = FakeOpener({"value": [junk_message()], "@odata.nextLink": link})
            with self.subTest(folder=folder), self.assertRaises(graph.MicrosoftGraphError):
                graph.list_microsoft_junk(TOKEN, page=2, page_size=1, opener=opener)
            self.assertEqual(len(opener.calls), 1)

    def test_exact_junk_message_uses_folder_and_sanitizes_body(self):
        raw = junk_message(
            body={"contentType": "html", "content": "<p>Claim prize</p><script>secret()</script>"},
        )
        opener = FakeOpener(raw)
        result = graph.get_microsoft_junk_message(TOKEN, MID, opener=opener)
        self.assertIn("/mailFolders/junkemail/messages/", opener.calls[0][0].full_url)
        output = projection.project_message(result, BOX, exact_body=True, folder="JUNK")
        self.assertIn("Claim prize", " ".join(output["body"]))
        self.assertNotIn("secret()", " ".join(output["body"]))
        self.assertEqual(output["providerFolder"], "JUNK")

    def test_nonmatching_ids_and_pagination_never_leave_junk(self):
        opener = FakeOpener({"value": []})
        for page in (0, -1, 5, "2", True):
            with self.subTest(page=page), self.assertRaises(graph.MicrosoftGraphError):
                graph.list_microsoft_junk(TOKEN, page=page, opener=opener)
        for msgid in ("", "bad?#", "../other"):
            with self.subTest(msgid=msgid), self.assertRaises(graph.MicrosoftGraphError):
                graph.get_microsoft_junk_message(TOKEN, msgid, opener=opener)
        self.assertEqual(len(opener.calls), 0)
        with self.assertRaises(graph.MicrosoftGraphError):
            graph.get_microsoft_junk_message(
                TOKEN, MID, opener=FakeOpener(junk_message(id="wrong")),
            )


class JunkOwnerTests(unittest.TestCase):
    def test_owner_rechecked_after_bounded_list_and_exact_message(self):
        owner = object()
        with patch.object(access, "_owned_token", return_value=(TOKEN, owner)), patch.object(
            access, "_verify_unchanged_owned_mailbox",
        ) as verify, patch.object(
            graph, "list_microsoft_junk", return_value={"messages": [], "hasMore": False},
        ) as listing, patch.object(
            graph, "get_microsoft_junk_message", return_value={"id": MID},
        ) as exact:
            access.list_owned_microsoft_junk((), BOX, page_size=25, page=2)
            access.read_owned_microsoft_junk_message((), BOX, MID)
            listing.assert_called_once_with(TOKEN, page_size=25, page=2)
            exact.assert_called_once_with(TOKEN, MID)
            self.assertEqual(verify.call_count, 2)
            self.assertEqual(verify.call_args.args, ((), BOX, owner))


class JunkHttpTests(unittest.TestCase):
    def test_exact_and_listing_do_not_invent_inbox_membership(self):
        data = {"messages": [graph._message_summary(junk_message())], "hasMore": False}
        with patch.object(access, "list_owned_microsoft_junk", return_value=data):
            result = microsoft_http.microsoft_inbox_response(
                _request({"mailboxId": BOX, "limit": 5}), mode="junk",
            )
        self.assertEqual(result.status, 200)
        self.assertEqual(_body(result)["messages"][0]["providerFolder"], "JUNK")
        self.assertEqual(_body(result)["junkMessageIds"], [MID])

        exact = {**graph._message_summary(junk_message()), "body": {
            "contentType": "text", "content": "Junk content",
        }}
        with patch.object(access, "read_owned_microsoft_junk_message", return_value=exact):
            result = microsoft_http.microsoft_inbox_response(
                _request({"mailboxId": BOX, "providerMessageId": MID}), mode="junk_exact",
            )
        self.assertEqual(result.status, 200)
        self.assertEqual(_body(result)["message"]["providerFolder"], "JUNK")

    def test_invalid_http_inputs_cannot_fetch_graph_or_write(self):
        with patch.object(access, "list_owned_microsoft_junk") as listing:
            for body in (
                {"mailboxId": BOX, "folder": "Inbox"},
                {"mailboxId": BOX, "page": 5},
                {"mailboxId": BOX, "nextLink": LINK},
                {"mailboxId": BOX, "access_token": TOKEN},
            ):
                self.assertEqual(microsoft_http.microsoft_inbox_response(
                    _request(body), mode="junk",
                ).status, 400)
            self.assertEqual(microsoft_http.microsoft_inbox_response(
                _request({"mailboxId": BOX}, command="GET"), mode="junk",
            ).status, 405)
            listing.assert_not_called()

    def test_junk_attachment_metadata_and_download_stay_in_folder(self):
        from api.inboxes import microsoft_attachments_graph, microsoft_attachment_download
        from api.inboxes.test_microsoft_attachments import file_row
        from api.inboxes.test_microsoft_attachment_download import payload
        meta = FakeOpener({"value": [file_row()]})
        microsoft_attachments_graph.list_microsoft_attachments(
            TOKEN, MID, folder="Junk", opener=meta,
        )
        self.assertIn("/mailFolders/junkemail/messages/", meta.calls[0][0].full_url)
        download = FakeOpener(payload())
        with self.assertRaises(graph.MicrosoftGraphError):
            microsoft_attachment_download.read_microsoft_file_attachment(
                TOKEN, MID, payload()["id"], folder="Junk", opener=download,
            )
        self.assertEqual(download.calls, [])
        with self.assertRaises(ValueError):
            microsoft_http._validate_payload({
                "mailboxId": BOX, "providerMessageId": MID,
                "attachmentId": payload()["id"], "folder": "Junk",
            }, mode="download")
