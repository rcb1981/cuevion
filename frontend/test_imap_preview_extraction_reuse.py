"""Complete MIME preview parity against the pre-optimization extraction path.

The reference functions below preserve the implementation at 60913326. Routing
still calls the unchanged classifier through its standalone extraction path.
Fixtures are synthetic, use fixed dates/boundaries, and require no network.
"""

import hashlib
import unittest
from email import message_from_bytes
from email.message import EmailMessage, Message
from typing import Any
from unittest.mock import patch

import imap_connect_preview as preview
from imap_connect_preview import (
    assess_message_noise,
    clean_text,
    decode_mime_words,
    format_timestamp,
    get_message_attachments,
    html_to_text,
    parseaddr,
)

def reference_get_message_body(message: Message) -> str:
    html_body = preview.get_html_body(message)

    if html_body:
        return html_to_text(html_body)

    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "")

            if "attachment" in disposition.lower():
                continue

            if content_type == "text/plain":
                payload = part.get_payload(decode=True)

                if payload is None:
                    continue

                charset = part.get_content_charset() or "utf-8"
                return clean_text(payload.decode(charset, errors="ignore"))

        for part in message.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)

                if payload is None:
                    continue

                charset = part.get_content_charset() or "utf-8"
                return clean_text(payload.decode(charset, errors="ignore"))

        return ""

    payload = message.get_payload(decode=True)

    if payload is None:
        return clean_text(str(message.get_payload() or ""))

    charset = message.get_content_charset() or "utf-8"
    return clean_text(payload.decode(charset, errors="ignore"))

def reference_get_html_body(message: Message) -> str:
    """Extract the raw HTML body from an email message.
    Returns empty string if no HTML part exists.
    Does NOT run clean_text() — HTML must be returned as-is."""
    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "")

            if "attachment" in disposition.lower():
                continue

            if content_type == "text/html":
                payload = part.get_payload(decode=True)

                if payload is None:
                    continue

                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="ignore").replace("\r\n", "\n").strip()
    else:
        if message.get_content_type() == "text/html":
            payload = message.get_payload(decode=True)

            if payload is not None:
                charset = message.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="ignore").replace("\r\n", "\n").strip()

    return ""

def reference_to_message_preview(
    message: Message,
    index: int,
    email_address: str,
    unread: bool,
    imap_uid: str | None,
    flagged: bool = False,
    internal_role: str | None = None,
    focus_preferences: dict[str, Any] | None = None,
) -> dict[str, Any]:
    subject = decode_mime_words(message.get("Subject", "Untitled message"))
    from_header = decode_mime_words(message.get("From", "Unknown sender"))
    to_header = decode_mime_words(message.get("To", ""))
    cc_header = decode_mime_words(message.get("Cc", ""))
    sender_name, sender_email = parseaddr(from_header)
    body = preview.get_message_body(message)
    html_body = preview.get_html_body(message)
    attachments = get_message_attachments(message)
    snippet = clean_text(body.replace("\n", " "))[:220]
    created_at, display_timestamp = format_timestamp(message.get("Date", ""))
    stable_id_source = f"{subject}|{from_header}|{display_timestamp}"
    message_id = (
        message.get("Message-Id")
        or (f"imap-uid-{imap_uid}" if imap_uid else None)
        or hashlib.sha1(stable_id_source.encode("utf-8")).hexdigest()
    )
    preview_routing = preview.resolve_preview_routing(
        message,
        email_address,
        internal_role=internal_role,
        focus_preferences=focus_preferences,
    )
    if all(
        key in preview_routing
        for key in ("noiseDisposition", "noiseConfidence", "noiseReasons")
    ):
        noise_assessment = {
            "noiseDisposition": preview_routing["noiseDisposition"],
            "noiseConfidence": preview_routing["noiseConfidence"],
            "noiseReasons": preview_routing["noiseReasons"],
        }
    else:
        noise_assessment = assess_message_noise(
            message=message,
            subject=subject,
            sender_name=sender_name,
            sender_email=sender_email,
            recipient_email=email_address,
            body=body,
            semantic_classification=preview_routing.get("internalClassification"),
        )

    return {
      "id": message_id.strip("<>"),
      "sender": sender_name or sender_email or from_header,
      "subject": subject,
      "snippet": snippet,
      "from": from_header,
      "to": to_header,
      "cc": cc_header,
      "timestamp": display_timestamp,
      "createdAt": created_at,
      "body": body.split("\n\n") if body else [snippet or "No message preview available."],
      "attachments": attachments,
      "unread": unread,
      "flagged": flagged,
      "imapUid": imap_uid,
      "signal": preview_routing.get("signal"),
      "ui_signal": preview_routing.get("ui_signal"),
      "internalClassification": preview_routing.get("internalClassification"),
      "final_visibility": preview_routing.get("final_visibility"),
      "action": preview_routing.get("action"),
      "v7_final_priority": preview_routing.get("v7_final_priority"),
      "category": preview_routing.get("category"),
      "classifierVersion": preview_routing.get("classifierVersion"),
      **noise_assessment,
      **({"bodyHtml": html_body} if html_body else {}),
    }


FIXED_DATE = "Tue, 15 Sep 2026 10:30:00 +0200"
PREVIEW_ARGS = (7, "promo@hysteriarecs.com", True, "1962")
PREVIEW_KWARGS = {
    "flagged": True,
    "internal_role": "label_ar_manager",
    "focus_preferences": {"demos": "medium", "promo": "low", "business": "high"},
}


def make_message(subject="Release update"):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = "Artist Example <artist@example.com>"
    message["To"] = "Promo Team <promo@hysteriarecs.com>"
    message["Cc"] = "Label Team <label@example.com>"
    message["Date"] = FIXED_DATE
    message["Message-ID"] = "<synthetic-message@example.com>"
    message["In-Reply-To"] = "<synthetic-parent@example.com>"
    message["References"] = "<synthetic-root@example.com> <synthetic-parent@example.com>"
    return message


def parsed_fixture(name, message):
    # Boundaries are fixed before serializing, so message serialization itself
    # cannot introduce randomness or mutate a fixture during assertions.
    for index, part in enumerate(message.walk()):
        if part.is_multipart():
            part.set_boundary(f"synthetic-{name}-{index}")
    return message_from_bytes(message.as_bytes())


def mime_fixtures():
    fixtures = {}

    message = make_message()
    message.set_content("Please review the release.\n\nSecond paragraph with https://example.com/music")
    fixtures["plain_text_only"] = message

    message = make_message()
    message.set_content("<html><body><p>Please <b>review</b> this release.</p><p>Second paragraph</p></body></html>", subtype="html")
    fixtures["html_only"] = message

    message = make_message()
    message.set_content("Plain alternative must not override HTML.")
    message.add_alternative("<p>HTML wins</p><a href=\"https://example.com/demo\">Listen</a>", subtype="html")
    fixtures["multipart_alternative"] = message

    message = make_message()
    message.set_content("First mixed body")
    message.make_mixed()
    second_body = EmailMessage()
    second_body.set_content("Second mixed body")
    message.attach(second_body)
    fixtures["multipart_mixed"] = message

    message = make_message()
    message.set_content("Nested plain body")
    message.add_alternative("<p>Nested HTML body</p>", subtype="html")
    message.make_mixed()
    nested = EmailMessage()
    nested.set_content("Nested inner plain")
    nested.add_alternative("<p>Later nested HTML</p>", subtype="html")
    message.attach(nested)
    fixtures["nested_multipart"] = message

    message = make_message()
    message.set_content("Body with invoice attachment")
    message.add_attachment(b"%PDF-1.7 synthetic", maintype="application", subtype="pdf", filename="invoice.pdf")
    fixtures["attachment"] = message

    message = make_message()
    message.set_content("Body with multiple attachments")
    message.add_attachment(b"first\x00\xff", maintype="application", subtype="octet-stream", filename="first.bin")
    message.add_attachment("Attachment text must remain an attachment", subtype="plain", filename="r\u00e9sum\u00e9 \U0001f3b5.txt")
    message.add_attachment(b"<p>HTML attachment</p>", maintype="text", subtype="html", filename="details.html")
    fixtures["multiple_attachments"] = message

    message = make_message()
    message.set_content("CID plain alternative")
    message.add_alternative('<html><body><p>Inline image</p><img src="cid:hero@example.com"></body></html>', subtype="html")
    message.get_payload()[-1].add_related(b"\x89PNG\r\n\x1a\nsynthetic", maintype="image", subtype="png", cid="<hero@example.com>", filename="hero.png", disposition="inline")
    fixtures["inline_cid_image"] = message

    message = make_message("=?utf-8?b?UsOpc3Vtw6kg8J+Otg==?=")
    message.replace_header("From", "=?utf-8?q?Bj=C3=B6rk?= <artist@example.com>")
    message.set_content("Caf\u00e9 encore\nA bient\u00f4t", charset="iso-8859-1", cte="quoted-printable")
    fixtures["encoded_subject_body"] = message

    message = make_message("\u97f3\u697d \u2014 M\u00fasica \U0001f3b6")
    message.set_content("H\u00e9llo \u65e5\u672c\u8a9e \u0627\u0644\u0639\u0631\u0628\u064a\u0629 \U0001f680\n\nCafe\u0301")
    message.add_alternative("<p>H\u00e9llo \u65e5\u672c\u8a9e \U0001f3b5 &amp; caf\u00e9</p>", subtype="html")
    fixtures["unicode"] = message

    message = make_message()
    message["Content-Type"] = "text/plain; charset=utf-8"
    message["Content-Transfer-Encoding"] = "8bit"
    message.set_payload(b"Malformed UTF-8: \xff\xfe retained text\xc3(")
    fixtures["malformed_encoding"] = message

    fixtures["missing_body"] = make_message()

    message = make_message()
    message.set_content("")
    fixtures["empty_body"] = message

    message = make_message("Re: Release approval")
    message.set_content("Approved, please send it.\n\nOn Monday, Artist wrote:\n> Can you approve?\n> Previous details\n\n-- \nSignature")
    message.add_alternative("<p>Approved, please send it.</p><blockquote><p>Can you approve?</p><p>Previous details</p></blockquote><div>Signature</div>", subtype="html")
    fixtures["body_plus_quoted_content"] = message

    message = make_message()
    message.make_mixed()
    message.add_attachment(b"<p>Fallback HTML attachment</p>", maintype="text", subtype="html", filename="fallback.html")
    fixtures["html_attachment_fallback"] = message

    message = make_message()
    message.set_content("Plain survives empty preferred HTML")
    message.add_alternative(" \r\n ", subtype="html")
    message.add_alternative("<p>Later HTML must not replace the empty first part</p>", subtype="html")
    fixtures["empty_first_html"] = message

    message = make_message()
    message.make_mixed()
    missing = Message()
    missing["Content-Type"] = "text/plain"
    message.attach(missing)
    body = EmailMessage()
    body.set_content("Later text body")
    message.attach(body)
    fixtures["missing_first_part_payload"] = message

    message = make_message()
    message.make_mixed()
    message.add_attachment(b"\x00\x01\xff", maintype="application", subtype="octet-stream")
    fixtures["binary_attachment_only"] = message

    message = make_message()
    del message["Message-ID"]
    message.set_content("Fallback provider ID")
    fixtures["missing_message_id"] = message

    message = make_message("Sales update")
    message["List-Unsubscribe"] = "<mailto:leave@example.com>"
    message.set_content("View online")
    message.add_alternative('<p>Save up to 60%</p><img src="https://example.com/sale.png" alt="Shop Now"><footer>Unsubscribe | Legal</footer>', subtype="html")
    fixtures["html_marketing_classification"] = message

    result = {name: parsed_fixture(name, message) for name, message in fixtures.items()}
    result["malformed_multipart"] = message_from_bytes(
        f"Date: {FIXED_DATE}\r\nSubject: Missing MIME boundary\r\nContent-Type: multipart/mixed; boundary=absent\r\n\r\nBody with no boundary\r\n".encode()
    )
    result["malformed_base64"] = message_from_bytes(
        f"Date: {FIXED_DATE}\r\nSubject: Invalid base64 padding\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: base64\r\n\r\nSGVsbG8g8J+OtQ!\r\n".encode()
    )
    return result


class ImapPreviewExtractionReuseTests(unittest.TestCase):
    def test_body_and_html_extractors_run_once_per_preview_including_empty_body(self):
        for name, message in mime_fixtures().items():
            with self.subTest(case=name):
                with patch.object(preview, "get_message_body", wraps=reference_get_message_body) as old_body, patch.object(
                    preview, "get_html_body", wraps=reference_get_html_body
                ) as old_html:
                    reference_to_message_preview(message, *PREVIEW_ARGS, **PREVIEW_KWARGS)
                self.assertEqual(old_body.call_count, 2)
                self.assertEqual(old_html.call_count, 3)

                with patch.object(preview, "get_message_body", wraps=preview.get_message_body) as new_body, patch.object(
                    preview, "get_html_body", wraps=preview.get_html_body
                ) as new_html:
                    preview.to_message_preview(message, *PREVIEW_ARGS, **PREVIEW_KWARGS)
                self.assertEqual(new_body.call_count, 1)
                self.assertEqual(new_html.call_count, 1)

    def test_standalone_extractors_and_routing_keep_existing_call_contract(self):
        for name, message in mime_fixtures().items():
            with self.subTest(case=name):
                expected_html = reference_get_html_body(message)
                expected_body = reference_get_message_body(message)
                self.assertEqual(preview.get_html_body(message), expected_html)
                self.assertEqual(preview.get_message_body(message), expected_body)
                self.assertEqual(preview.get_message_body(message, html_body=expected_html), expected_body)
                with patch.object(preview, "get_message_body", wraps=preview.get_message_body) as body:
                    standalone = preview.resolve_preview_routing(message, PREVIEW_ARGS[1], "label_ar_manager", {})
                self.assertEqual(body.call_count, 1)
                with patch.object(preview, "get_message_body", side_effect=AssertionError("re-extraction")):
                    reused = preview.resolve_preview_routing(message, PREVIEW_ARGS[1], "label_ar_manager", {}, body=expected_body)
                self.assertEqual(reused, standalone)

    def test_unknown_charset_preserves_preview_error_and_standalone_routing_fallback(self):
        message = message_from_bytes(
            f"Date: {FIXED_DATE}\r\nContent-Type: text/plain; charset=unknown-synthetic-charset\r\n\r\nBody".encode()
        )
        with self.assertRaises(LookupError):
            reference_to_message_preview(message, *PREVIEW_ARGS, **PREVIEW_KWARGS)
        with self.assertRaises(LookupError):
            preview.to_message_preview(message, *PREVIEW_ARGS, **PREVIEW_KWARGS)
        with self.assertLogs(preview.logger.name, level="ERROR"):
            routing = preview.resolve_preview_routing(message, PREVIEW_ARGS[1])
        self.assertEqual(routing, {"ui_signal": "NEW", "internalClassification": "unknown"})

    def test_inline_cid_attachment_ids_metadata_and_download_payload(self):
        message = mime_fixtures()["inline_cid_image"]
        result = preview.to_message_preview(message, *PREVIEW_ARGS, **PREVIEW_KWARGS)
        self.assertEqual(result["attachments"], [{
            "id": "attachment-4",
            "name": "hero.png",
            "mimeType": "image/png",
            "size": 17,
            "contentId": "hero@example.com",
            "disposition": "inline",
            "inlineSrc": "data:image/png;base64,iVBORw0KGgpzeW50aGV0aWM=",
        }])
        self.assertIn('src="cid:hero@example.com"', result["bodyHtml"])
        self.assertEqual(preview.get_message_attachment_payload(message, "attachment-4"), {
            "content": b"\x89PNG\r\n\x1a\nsynthetic",
            "filename": "hero.png",
            "mimeType": "image/png",
            "size": 17,
        })

    def test_multiple_attachment_order_and_encoded_filename(self):
        result = preview.to_message_preview(mime_fixtures()["multiple_attachments"], *PREVIEW_ARGS, **PREVIEW_KWARGS)
        self.assertEqual([part["id"] for part in result["attachments"]], ["attachment-2", "attachment-3", "attachment-4"])
        self.assertEqual([part["name"] for part in result["attachments"]], ["first.bin", "r\u00e9sum\u00e9 \U0001f3b5.txt", "details.html"])
        self.assertEqual(result["body"], ["Body with multiple attachments"])

    def test_existing_empty_and_attachment_html_fallbacks_are_preserved(self):
        fixtures = mime_fixtures()
        self.assertEqual(preview.get_message_body(fixtures["empty_first_html"]), "Plain survives empty preferred HTML")
        self.assertEqual(preview.get_message_body(fixtures["html_attachment_fallback"]), "<p>Fallback HTML attachment</p>")
        self.assertEqual(preview.get_html_body(fixtures["html_attachment_fallback"]), "")


def complete_preview_parity_case(name):
    def test(self):
        message = mime_fixtures()[name]
        original_bytes = message.as_bytes()
        with patch.object(preview, "get_message_body", reference_get_message_body), patch.object(
            preview, "get_html_body", reference_get_html_body
        ):
            expected = reference_to_message_preview(message, *PREVIEW_ARGS, **PREVIEW_KWARGS)
        actual = preview.to_message_preview(message, *PREVIEW_ARGS, **PREVIEW_KWARGS)
        # Includes body/snippet, all identity/header/date/flag fields, every
        # attachment/CID field, classification, noise, and Priority projection.
        self.assertEqual(actual, expected)
        self.assertEqual(message.as_bytes(), original_bytes)
        self.assertEqual(actual["createdAt"], "2026-09-15T08:30:00+00:00")
    return test


for fixture_name in mime_fixtures():
    setattr(ImapPreviewExtractionReuseTests, f"test_complete_preview_parity_{fixture_name}", complete_preview_parity_case(fixture_name))


if __name__ == "__main__":
    unittest.main()
