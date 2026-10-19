"""Bounded read-only Microsoft attachment downloads and security regressions."""
from __future__ import annotations

import base64
import binascii
import struct
import zlib
import unittest
from unittest.mock import patch

from api.inboxes import microsoft_attachment_download as download
from api.inboxes import microsoft_graph, microsoft_http, microsoft_mailbox_access
from api.inboxes.test_microsoft_graph import FakeOpener, FakeResponse, TOKEN
from api.inboxes.test_microsoft_http import _body, _request

BOX = "microsoft-0123456789abcdefabcd12345678"
MESSAGE = "AAMkAGVm.987/"
ATTACHMENT = "AAMkAttachment.1/"
CONTENT = b"Test safe audio content."


def payload(**overrides):
    return {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "id": ATTACHMENT,
        "name": "demo.wav",
        "size": len(CONTENT),
        "contentBytes": base64.b64encode(CONTENT).decode("ascii"),
        "contentType": "audio/wav",
        "isInline": False,
        **overrides,
    }


class ThreeStepGraphOpener:
    """Simulate exact Graph detail -> safe metadata -> raw bytes, no real account."""

    def __init__(self, detail: object, metadata: dict, raw: bytes):
        self.responses = [detail, metadata, raw]
        self.calls = []

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        if len(self.calls) > len(self.responses):
            raise AssertionError("Unexpected Graph request")
        return FakeResponse(self.responses[len(self.calls) - 1])


def complete_png_fixture() -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + kind + body +
            struct.pack(">I", binascii.crc32(kind + body) & 0xffffffff)
        )
    return (
        bytes.fromhex("89504e470d0a1a0a")
        + chunk(b"IHDR", struct.pack(">2I5B", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes.fromhex("00000000")))
        + chunk(b"IEND", b"")
    )


class GraphDownloadTests(unittest.TestCase):
    def test_exact_folder_and_id_return_canonical_base64(self):
        opener = FakeOpener(payload())
        result = download.read_microsoft_file_attachment(
            TOKEN, MESSAGE, ATTACHMENT, folder="Inbox", opener=opener,
        )
        self.assertEqual(result["attachmentId"], ATTACHMENT)
        self.assertEqual(result["fileName"], "demo.wav")
        self.assertEqual(base64.b64decode(result["base64"]), CONTENT)
        self.assertEqual(set(result), {"attachmentId", "fileName", "size", "base64"})
        request, timeout = opener.calls[0]
        self.assertIn("/me/mailFolders/inbox/messages/", request.full_url)
        self.assertIn("/attachments/", request.full_url)
        self.assertNotIn(TOKEN, request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertEqual(timeout, microsoft_graph.GRAPH_TIMEOUT_SECONDS)

    def test_download_accepts_valid_unprefixed_graph_file_attachment_type(self):
        # Microsoft Graph list examples use the unprefixed OData spelling.
        item = download.read_microsoft_file_attachment(
            TOKEN, MESSAGE, ATTACHMENT, folder="Inbox",
            opener=FakeOpener(payload(**{"@odata.type": "microsoft.graph.fileAttachment"})),
        )
        self.assertEqual(base64.b64decode(item["base64"]), CONTENT)
        with self.assertRaises(microsoft_graph.MicrosoftGraphError):
            download.read_microsoft_file_attachment(
                TOKEN, MESSAGE, ATTACHMENT, folder="Inbox",
                opener=FakeOpener(payload(**{"@odata.type": "microsoft.graph.itemAttachment"})),
            )

    def test_missing_contentbytes_uses_exact_file_metadata_and_bounded_raw_bytes(self):
        # Matches the user's 805650-byte PNG metadata, without exposing mail.
        image = bytes.fromhex("89504e470d0a1a0a") + b"x" * (805650 - 8)
        self.assertEqual(len(image), 805650)
        attachment = {
            "id": ATTACHMENT, "@odata.type": "#microsoft.graph.fileAttachment",
            "name": "ChatGPT Image Apr 29, 2026, 06_04_40 PM.png",
            "size": len(image), "contentType": "image/png",
            "isInline": True, "contentId": "324575DD-BAA1-4FAF-8E9B-0255E8CE94E5",
        }
        opener = ThreeStepGraphOpener(
            payload(**{"contentBytes": None, "size": len(image)}),
            {"value": [attachment]},
            image,
        )
        result = download.read_microsoft_file_attachment(
            TOKEN, MESSAGE, ATTACHMENT, folder="Inbox", opener=opener,
        )
        self.assertEqual(result["size"], 805650)
        self.assertEqual(result["fileName"], attachment["name"])
        self.assertEqual(base64.b64decode(result["base64"]), image)
        self.assertEqual(len(opener.calls), 3)
        self.assertIn("/mailFolders/inbox/messages/", opener.calls[0][0].full_url)
        self.assertIn("/mailFolders/inbox/messages/", opener.calls[1][0].full_url)
        self.assertTrue(opener.calls[2][0].full_url.endswith("/$value"))
        self.assertEqual(opener.calls[2][0].get_method(), "GET")
        self.assertNotIn(TOKEN, opener.calls[2][0].full_url)
        self.assertEqual(opener.calls[2][0].get_header("Authorization"), "Bearer " + TOKEN)

    def test_corrupted_detail_json_falls_back_only_to_verified_file(self):
        opener = ThreeStepGraphOpener(
            b"{corrupt-non-json", {"value": [{
                "id": ATTACHMENT, "@odata.type": "#microsoft.graph.fileAttachment",
                "name": "image.png", "size": len(CONTENT),
                "contentType": "image/png", "isInline": True, "contentId": "x@y",
            }]}, CONTENT,
        )
        recovered = download.read_microsoft_file_attachment(
            TOKEN, MESSAGE, ATTACHMENT, folder="Inbox", opener=opener,
        )
        self.assertEqual(base64.b64decode(recovered["base64"]), CONTENT)

    def test_raw_png_complete_crc_can_differ_from_graph_reported_size(self):
        image = complete_png_fixture()
        self.assertTrue(download._complete_png(image))
        inflated = len(image) + 31
        opener = ThreeStepGraphOpener(
            payload(contentBytes=None, size=inflated),
            {"value": [{
                "id": ATTACHMENT, "@odata.type": "#microsoft.graph.fileAttachment",
                "name": "logo.png", "size": inflated,
                "contentType": "image/png", "isInline": True,
                "contentId": "LOGO@MESSAGE",
            }]},
            image,
        )
        decoded = download.read_microsoft_file_attachment(
            TOKEN, MESSAGE, ATTACHMENT, folder="Inbox", opener=opener,
        )
        self.assertEqual(decoded["size"], len(image))
        self.assertEqual(base64.b64decode(decoded["base64"]), image)
        self.assertEqual(len(opener.calls), 3)

    def test_mismatched_corrupt_png_and_nonimage_stay_blocked(self):
        image = complete_png_fixture()
        corrupted = bytearray(image)
        corrupted[35] ^= 1  # invalid CRC: a signature alone cannot authorize data
        self.assertFalse(download._complete_png(bytes(corrupted)))
        self.assertFalse(download._complete_png(image[:-5]))
        self.assertFalse(download._complete_png(b"PNG" + image))
        for raw, content_type in (
            (bytes(corrupted), "image/png"),
            (image[:-5], "image/png"),
            (b"<svg onload='evil()'/>", "image/png"),
            (image, "application/pdf"),
        ):
            with self.subTest(raw_length=len(raw), content_type=content_type):
                opener = ThreeStepGraphOpener(
                    payload(contentBytes=None),
                    {"value": [{
                        "id": ATTACHMENT, "@odata.type": "#microsoft.graph.fileAttachment",
                        "name": "test.file", "size": len(raw) + 1,
                        "contentType": content_type, "isInline": True,
                        "contentId": "x@y",
                    }]},
                    raw,
                )
                with self.assertRaises(microsoft_graph.MicrosoftGraphError):
                    download.read_microsoft_file_attachment(
                        TOKEN, MESSAGE, ATTACHMENT, folder="Inbox", opener=opener,
                    )

    def test_raw_fallback_rejects_item_reference_wrong_id_size_and_oversized_data(self):
        known = {
            "id": ATTACHMENT, "@odata.type": "#microsoft.graph.fileAttachment",
            "name": "image.png", "size": len(CONTENT),
            "contentType": "image/png", "isInline": True, "contentId": "x@y",
        }
        for change in (
            {"@odata.type": "#microsoft.graph.itemAttachment"},
            {"@odata.type": "#microsoft.graph.referenceAttachment"},
            {"id": "different"},
            {"size": len(CONTENT) + 1},
            {"size": download.MAX_FILE_BYTES + 1},
        ):
            with self.subTest(change=change):
                opener = ThreeStepGraphOpener(
                    payload(contentBytes=None),
                    {"value": [{**known, **change}]}, CONTENT,
                )
                with self.assertRaises(microsoft_graph.MicrosoftGraphError):
                    download.read_microsoft_file_attachment(
                        TOKEN, MESSAGE, ATTACHMENT, folder="Inbox", opener=opener,
                    )
                if change.get("@odata.type") or change.get("id") or change.get("size") == download.MAX_FILE_BYTES + 1:
                    self.assertEqual(len(opener.calls), 2)  # no raw fetch
                else:
                    self.assertEqual(len(opener.calls), 3)  # length mismatch

    def test_sent_download_is_folder_scoped(self):
        opener = FakeOpener(payload())
        download.read_microsoft_file_attachment(
            TOKEN, MESSAGE, ATTACHMENT, folder="Sent", opener=opener,
        )
        self.assertIn("/mailFolders/sentitems/messages/", opener.calls[0][0].full_url)

    def test_no_arbitrary_folders_or_untrusted_provider_ids(self):
        opener = FakeOpener(payload())
        for folder in ("Junk", "archive", "INBOX", "../Sent", "", None):
            with self.subTest(folder=folder), self.assertRaises(microsoft_graph.MicrosoftGraphError):
                download.read_microsoft_file_attachment(
                    TOKEN, MESSAGE, ATTACHMENT, folder=folder, opener=opener,
                )
        for invalid in ("..", "bad?x", "bad#x", "x/y/../a", " ", ""):
            with self.subTest(invalid=invalid), self.assertRaises(microsoft_graph.MicrosoftGraphError):
                download.read_microsoft_file_attachment(
                    TOKEN, MESSAGE, invalid, folder="Inbox", opener=opener,
                )
        self.assertEqual(len(opener.calls), 0)

    def test_reject_invalid_file_records_and_base64(self):
        changes = (
            {"id": "wrong"},
            {"@odata.type": "#microsoft.graph.itemAttachment"},
            {"size": True},
            {"size": -1},
            {"size": len(CONTENT) + 1},
            {"contentBytes": "%%invalid%%"},
            {"contentBytes": None},
        )
        for override in changes:
            with self.subTest(change=override), self.assertRaises(microsoft_graph.MicrosoftGraphError):
                download.read_microsoft_file_attachment(
                    TOKEN, MESSAGE, ATTACHMENT, folder="Inbox",
                    opener=FakeOpener(payload(**override)),
                )

    def test_size_ceiling_and_safe_filename(self):
        with self.assertRaises(microsoft_graph.MicrosoftGraphError) as error:
            download.read_microsoft_file_attachment(
                TOKEN, MESSAGE, ATTACHMENT, folder="Inbox",
                opener=FakeOpener(payload(size=download.MAX_FILE_BYTES + 1)),
            )
        self.assertEqual(error.exception.code, "microsoft_attachment_too_large")
        for unsafe in ("../evil.html", "C:\\temp\\mix.wav", "bad\nname.pdf", "...", ""):
            with self.subTest(name=unsafe):
                name = download._download_filename(unsafe)
                self.assertNotIn("/", name)
                self.assertNotIn("\\", name)
                self.assertNotIn("\n", name)
                self.assertTrue(name)


class AttachmentDownloadBoundaryTests(unittest.TestCase):
    def test_http_rejects_invalid_selector_and_extra_authority(self):
        valid = {
            "mailboxId": BOX,
            "providerMessageId": MESSAGE,
            "attachmentId": ATTACHMENT,
            "folder": "Inbox",
        }
        self.assertEqual(
            microsoft_http._validate_payload(valid, mode="download"),
            (BOX, {
                "providerMessageId": MESSAGE,
                "attachmentId": ATTACHMENT,
                "folder": "Inbox",
            }),
        )
        invalid = (
            {**valid, "folder": "Junk"},
            {**valid, "attachmentId": "bad?#"},
            {**valid, "attachmentId": True},
            {**valid, "providerMessageId": "../evil"},
            {**valid, "nextLink": "https://evil.example/"},
            {**valid, "access_token": TOKEN},
        )
        for item in invalid:
            with self.subTest(fields=item), self.assertRaises(ValueError):
                microsoft_http._validate_payload(item, mode="download")

    def test_http_success_does_not_expose_credentials_or_graph_metadata(self):
        body = {
            "mailboxId": BOX, "providerMessageId": MESSAGE,
            "attachmentId": ATTACHMENT, "folder": "Sent",
        }
        record = {
            "attachmentId": ATTACHMENT,
            "fileName": "demo.wav",
            "size": len(CONTENT),
            "base64": base64.b64encode(CONTENT).decode("ascii"),
            "privateGraphField": "not-for-browser",
        }
        with patch.object(
            microsoft_mailbox_access, "download_owned_microsoft_attachment",
            return_value=record,
        ) as owner:
            result = microsoft_http.microsoft_inbox_response(
                _request(body), mode="download",
            )
        self.assertEqual(result.status, 200)
        decoded = _body(result)
        self.assertEqual(set(decoded), {
            "ok", "attachmentId", "fileName", "size", "base64",
        })
        self.assertNotIn("privateGraphField", decoded)
        self.assertEqual(dict(result.headers).get("Cache-Control"), "no-store")
        self.assertEqual(dict(result.headers).get("X-Content-Type-Options"), "nosniff")
        owner.assert_called_once()
        self.assertEqual(owner.call_args.kwargs, {"folder": "Sent"})

    def test_http_methods_and_oversize_fail_closed(self):
        body = {
            "mailboxId": BOX, "providerMessageId": MESSAGE,
            "attachmentId": ATTACHMENT, "folder": "Inbox",
        }
        with patch.object(
            microsoft_mailbox_access, "download_owned_microsoft_attachment"
        ) as owner:
            self.assertEqual(
                microsoft_http.microsoft_inbox_response(
                    _request(body, command="GET"), mode="download",
                ).status, 405,
            )
            owner.assert_not_called()
        with patch.object(
            microsoft_mailbox_access, "download_owned_microsoft_attachment",
            side_effect=microsoft_graph.MicrosoftGraphError("microsoft_attachment_too_large"),
        ):
            result = microsoft_http.microsoft_inbox_response(
                _request(body), mode="download",
            )
            self.assertEqual(result.status, 413)
            self.assertEqual(_body(result)["error"]["code"], "microsoft_attachment_too_large")

    def test_owner_authority_rechecked_after_file_read(self):
        authority = object()
        with patch.object(
            microsoft_mailbox_access, "_owned_token", return_value=(TOKEN, authority),
        ), patch.object(
            download, "read_microsoft_file_attachment",
            return_value={"attachmentId": ATTACHMENT, "fileName": "demo.wav",
                          "size": 3, "base64": "YWJj"},
        ) as graph, patch.object(
            microsoft_mailbox_access, "_verify_unchanged_owned_mailbox",
        ) as verify:
            result = microsoft_mailbox_access.download_owned_microsoft_attachment(
                (), BOX, MESSAGE, ATTACHMENT, folder="Inbox",
            )
            self.assertEqual(result["fileName"], "demo.wav")
            graph.assert_called_once_with(
                TOKEN, MESSAGE, ATTACHMENT, folder="Inbox",
            )
            verify.assert_called_once_with((), BOX, authority)
