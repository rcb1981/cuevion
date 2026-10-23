"""Read-only Microsoft attachment metadata and security-boundary tests."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from api.inboxes import microsoft_attachments_graph as attachments
from api.inboxes import microsoft_graph, microsoft_http, microsoft_mailbox_access
from api.inboxes.test_microsoft_graph import FakeOpener, TOKEN


MAILBOX = "microsoft-0123456789abcdefabcd12345678"
MSG = "AAMkAGVm.987/"
FILE_ID = "AAMkAttachment.1/"


def file_row(**changes):
    return {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "id": FILE_ID,
        "name": "mixdown.wav",
        "size": 123456,
        "contentType": "audio/wav",
        "isInline": False,
        **changes,
    }


class AttachmentGraphTests(unittest.TestCase):
    def test_inbox_metadata_stays_in_fixed_graph_folder(self):
        opener = FakeOpener({"value": [file_row(contentBytes="PRIVATE")]})
        result = attachments.list_microsoft_attachments(
            TOKEN, MSG, folder="Inbox", opener=opener,
        )
        self.assertEqual(result["attachments"][0]["name"], "mixdown.wav")
        self.assertEqual(result["attachments"][0]["kind"], "file")
        self.assertNotIn("contentBytes", str(result))
        self.assertEqual(result["hasMore"], False)
        request, timeout = opener.calls[0]
        self.assertTrue(request.full_url.startswith(
            microsoft_graph.GRAPH_ROOT + "/me/mailFolders/inbox/messages/"
        ))
        self.assertIn("/attachments?", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertEqual(timeout, 20)

    def test_exact_file_cid_even_when_inline_flag_is_unreliable(self):
        inline = attachments._attachment_summary(file_row(
            name="image001.png", contentType="image/png", isInline=True,
            contentId="<image001.png@message>",
        ))
        self.assertEqual(inline["contentId"], "image001.png@message")
        self.assertEqual(inline["id"], FILE_ID)
        self.assertEqual(attachments._attachment_summary(
            file_row(contentId="<unused@message>")
        )["contentId"], "unused@message")
        self.assertEqual(attachments._attachment_summary(
            file_row(**{"@odata.type": "microsoft.graph.fileAttachment",
                        "isInline": False, "contentId": "<LOGO@message>",
                        "contentType": "application/octet-stream"})
        )["contentId"], "LOGO@message")
        # No untrusted URL-based CID resolver; metadata alone cannot download.
        self.assertIsNone(attachments._attachment_summary(
            file_row(**{"@odata.type": "microsoft.graph.referenceAttachment",
                        "isInline": True, "contentId": "logo@message"})
        )["contentId"])
        self.assertEqual(attachments._attachment_summary(
            file_row(**{"@odata.type": None, "contentId": "logo@message",
                        "isInline": True, "contentType": "image/png"})
        )["kind"], "file")
        self.assertIsNone(attachments._attachment_summary(
            file_row(isInline=True, contentId="https://tracker.example/pixel")
        )["contentId"])
        with self.assertRaises(microsoft_graph.MicrosoftGraphError):
            attachments._attachment_summary(file_row(isInline=True, contentId=123))

    def test_graph_selects_cid_without_attachment_content(self):
        opener = FakeOpener({"value": [file_row(
            name="inline.png", contentType="image/png",
            isInline=True, contentId="logo@message",
        )]})
        result = attachments.list_microsoft_attachments(
            TOKEN, MSG, folder="Inbox", opener=opener,
        )
        self.assertEqual(result["attachments"][0]["contentId"], "logo@message")
        url = opener.calls[0][0].full_url
        self.assertIn("microsoft.graph.fileAttachment%2FcontentId", url)
        self.assertNotIn("contentBytes", url)

    def test_sent_uses_fixed_sentitems_folder(self):
        opener = FakeOpener({"value": []})
        attachments.list_microsoft_attachments(
            TOKEN, MSG, folder="Sent", opener=opener,
        )
        self.assertIn("/mailFolders/sentitems/messages/", opener.calls[0][0].full_url)

    def test_bad_folder_and_id_do_not_reach_graph(self):
        opener = FakeOpener({"value": []})
        for folder in ("Outbox", "Trash", "inbox", "../inbox", ""):
            with self.subTest(folder=folder), self.assertRaises(microsoft_graph.MicrosoftGraphError):
                attachments.list_microsoft_attachments(TOKEN, MSG, folder=folder, opener=opener)
        self.assertEqual(len(opener.calls), 0)

    def test_bad_attachment_records_fail_closed(self):
        invalid = (
            file_row(size=True),
            file_row(size=-1),
            file_row(name=""),
            file_row(isInline="false"),
            file_row(id="bad?#"),
            file_row(contentType=123),
            file_row(**{"@odata.type": 123}),
        )
        for row in invalid:
            with self.subTest(row=str(row)[:50]), self.assertRaises(microsoft_graph.MicrosoftGraphError):
                attachments.list_microsoft_attachments(
                    TOKEN, MSG, folder="Inbox", opener=FakeOpener({"value": [row]}),
                )
        with self.assertRaises(microsoft_graph.MicrosoftGraphError):
            attachments.list_microsoft_attachments(
                TOKEN, MSG, folder="Inbox",
                opener=FakeOpener({"value": [file_row(), file_row()]}),
            )

    def test_unscoped_continuation_is_rejected_before_following(self):
        link = microsoft_graph.GRAPH_ROOT + "/me/messages/id/attachments?$skiptoken=opaque"
        opener = FakeOpener({"value": [file_row()], "@odata.nextLink": link})
        with self.assertRaises(microsoft_graph.MicrosoftGraphError):
            attachments.list_microsoft_attachments(TOKEN, MSG, folder="Inbox", opener=opener)
        self.assertEqual(len(opener.calls), 1)


class AttachmentBoundaryTests(unittest.TestCase):
    def test_exact_folder_only_and_no_custom_graph_url(self):
        valid = {"mailboxId": MAILBOX, "providerMessageId": MSG, "folder": "Inbox"}
        self.assertEqual(
            microsoft_http._validate_payload(valid, mode="attachments"),
            (MAILBOX, {"providerMessageId": MSG, "folder": "Inbox"}),
        )
        for altered in (
            {**valid, "folder": "Outbox"},
            {**valid, "folder": "INBOX"},
            {**valid, "page": 2},
            {**valid, "nextLink": "arbitrary"},
            {**valid, "providerMessageId": "../other"},
        ):
            with self.subTest(keys=altered), self.assertRaises(ValueError):
                microsoft_http._validate_payload(altered, mode="attachments")

    def test_mailbox_owner_rechecked_after_graph_result(self):
        captured = object()
        with patch.object(
            microsoft_mailbox_access, "_owned_token", return_value=(TOKEN, captured)
        ), patch.object(
            attachments, "list_microsoft_attachments",
            return_value={"attachments": [], "hasMore": False},
        ) as graph, patch.object(
            microsoft_mailbox_access, "_verify_unchanged_owned_mailbox",
        ) as verify:
            result = microsoft_mailbox_access.list_owned_microsoft_attachments(
                (), MAILBOX, MSG, folder="Sent",
            )
            self.assertEqual(result["attachments"], [])
            graph.assert_called_once_with(TOKEN, MSG, folder="Sent")
            verify.assert_called_once_with((), MAILBOX, captured)

    def test_http_metadata_response_is_read_only(self):
        from api.inboxes.test_microsoft_http import _request, _body
        payload = {"mailboxId": MAILBOX, "providerMessageId": MSG, "folder": "Inbox"}
        listing = {"attachments": [attachments._attachment_summary(file_row())], "hasMore": False}
        with patch.object(
            microsoft_mailbox_access, "list_owned_microsoft_attachments", return_value=listing
        ) as owner:
            response = microsoft_http.microsoft_inbox_response(
                _request(payload), mode="attachments",
            )
        self.assertEqual(response.status, 200)
        self.assertTrue(_body(response)["ok"])
        self.assertEqual(_body(response)["attachments"][0]["name"], "mixdown.wav")
        self.assertNotIn("access_token", str(_body(response)))
        self.assertEqual(owner.call_count, 1)
        self.assertEqual(owner.call_args.args[1:], (MAILBOX, MSG))
        self.assertEqual(owner.call_args.kwargs["folder"], "Inbox")

    def test_http_rejects_non_post_and_unsupported_folder(self):
        from api.inboxes.test_microsoft_http import _request
        payload = {"mailboxId": MAILBOX, "providerMessageId": MSG, "folder": "Inbox"}
        with patch.object(
            microsoft_mailbox_access, "list_owned_microsoft_attachments"
        ) as owner:
            self.assertEqual(
                microsoft_http.microsoft_inbox_response(
                    _request(payload, command="GET"), mode="attachments",
                ).status,
                405,
            )
            self.assertEqual(
                microsoft_http.microsoft_inbox_response(
                    _request({**payload, "folder": "Outbox"}), mode="attachments",
                ).status,
                400,
            )
            owner.assert_not_called()
