import base64
import json
import os
import sys
import unittest
from email import message_from_bytes
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch


os.environ.setdefault("OPENAI_API_KEY", "test-key")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from imap_connect_preview import (  # noqa: E402
    build_connect_preview_response,
    to_message_preview,
)


def _valid_gmail_identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 256
        and value == value.strip()
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


_authenticated_gmail_module_name = "api.inboxes.authenticated_gmail"
_existing_authenticated_gmail = sys.modules.get(_authenticated_gmail_module_name)
if _existing_authenticated_gmail is None:
    _authenticated_gmail_stub = ModuleType(_authenticated_gmail_module_name)
    _authenticated_gmail_stub.MAX_GMAIL_RESPONSE_BYTES = 10 * 1024 * 1024
    _authenticated_gmail_stub.valid_identifier = _valid_gmail_identifier
    sys.modules[_authenticated_gmail_module_name] = _authenticated_gmail_stub
try:
    from api.inboxes.gmail_snapshot import parse_gmail_message_detail  # noqa: E402
finally:
    if _existing_authenticated_gmail is None:
        sys.modules.pop(_authenticated_gmail_module_name, None)

from message_noise_assessment import (  # noqa: E402
    NOISE_CONFIDENCE_VALUES,
    NOISE_DISPOSITION_VALUES,
    NOISE_REASON_VALUES,
    assess_message_noise,
)


LOAN_SUBJECT = "Apply for a Loan Today – Fast Processing"
LOAN_SENDER = "Arabian Investment Group <george.harry@wh.commufra.jp>"
LOAN_RECIPIENT = "promo@hysteriarecs.com"
LOAN_BODY = """Need a personal or business loan?
Arabian Investment Group offers a simple application process and flexible
loan options.
Send us a message today to get started.
Approval is subject to eligibility and terms.
Best wishes,
Mr.George Harry
Senior Consultant"""


def make_message(
    subject: str,
    body: str,
    *,
    sender: str = "Sender <sender@example.com>",
    recipient: str = LOAN_RECIPIENT,
    headers: tuple[tuple[str, str], ...] = (),
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message["Date"] = "Tue, 11 Aug 2026 12:00:00 +0000"
    message["Message-ID"] = "<message@example.com>"
    for name, value in headers:
        message[name] = value
    message.set_content(body)
    return message


def assess(
    subject: str,
    body: str,
    *,
    sender: str = "Sender <sender@example.com>",
    recipient: str = LOAN_RECIPIENT,
    headers: tuple[tuple[str, str], ...] = (),
    semantic_classification: str | None = None,
):
    message = make_message(
        subject,
        body,
        sender=sender,
        recipient=recipient,
        headers=headers,
    )
    sender_name, sender_email = parseaddr(sender)
    return assess_message_noise(
        message=message,
        subject=subject,
        sender_name=sender_name,
        sender_email=sender_email,
        recipient_email=recipient,
        body=body,
        semantic_classification=semantic_classification,
    )


class MessageNoiseAssessmentTests(unittest.TestCase):
    def assert_closed_contract(self, result):
        self.assertEqual(
            set(result),
            {"noiseDisposition", "noiseConfidence", "noiseReasons"},
        )
        self.assertIn(result["noiseDisposition"], NOISE_DISPOSITION_VALUES)
        self.assertIn(result["noiseConfidence"], NOISE_CONFIDENCE_VALUES)
        self.assertEqual(
            len(result["noiseReasons"]),
            len(set(result["noiseReasons"])),
        )
        self.assertLessEqual(len(result["noiseReasons"]), len(NOISE_REASON_VALUES))
        self.assertTrue(
            all(reason in NOISE_REASON_VALUES for reason in result["noiseReasons"])
        )

    def assert_legitimate(self, result):
        self.assertNotEqual(result["noiseDisposition"], "strong_spam")
        self.assertNotEqual(result["noiseDisposition"], "unsolicited_low_value")
        self.assert_closed_contract(result)

    def test_exact_loan_fixture_is_strong_spam_with_bounded_reasons(self):
        result = assess(
            LOAN_SUBJECT,
            LOAN_BODY,
            sender=LOAN_SENDER,
        )

        self.assertEqual(result["noiseDisposition"], "strong_spam")
        self.assertEqual(result["noiseConfidence"], "medium")
        self.assertTrue(
            {
                "unsolicited_financial_solicitation",
                "cold_call_to_action",
                "no_conversation_evidence",
                "mailbox_relevance_mismatch",
            }.issubset(result["noiseReasons"])
        )
        self.assert_closed_contract(result)

    def test_crypto_investment_pitch_is_strong_spam(self):
        result = assess(
            "Guaranteed crypto investment opportunity",
            "Our platform offers guaranteed weekly returns on crypto investment. "
            "Contact us today to get started.",
            sender="Investment Desk <advisor@random.example>",
        )

        self.assertEqual(result["noiseDisposition"], "strong_spam")
        self.assertIn("unsolicited_investment_solicitation", result["noiseReasons"])
        self.assertIn("cold_call_to_action", result["noiseReasons"])

    def test_seo_agency_outreach_is_unsolicited_low_value(self):
        result = assess(
            "SEO services for your website",
            "We provide SEO services and help your business increase traffic. "
            "Contact us for a free audit.",
            sender="SEO Team <sales@agency.example>",
        )

        self.assertEqual(result["noiseDisposition"], "unsolicited_low_value")
        self.assertIn("cold_sales_outreach", result["noiseReasons"])

    def test_lead_generation_outreach_is_unsolicited_low_value(self):
        result = assess(
            "Qualified leads for your company",
            "We provide lead-generation services and generate qualified leads. "
            "Book a call today.",
            sender="Growth Team <outreach@leadgen.example>",
        )

        self.assertEqual(result["noiseDisposition"], "unsolicited_low_value")
        self.assertIn("cold_sales_outreach", result["noiseReasons"])

    def test_generic_marketing_agency_is_unsolicited_low_value(self):
        result = assess(
            "Digital marketing services",
            "Our digital marketing agency can boost your sales and visibility. "
            "Contact us to schedule a consultation.",
            sender="Agency <hello@marketing.example>",
        )

        self.assertEqual(result["noiseDisposition"], "unsolicited_low_value")

    def test_unrelated_recruiter_is_unsolicited_low_value(self):
        result = assess(
            "Job opportunity for you",
            "I am a recruiter and I came across your profile. Your background "
            "looks like a strong fit for this role. Schedule an interview.",
            sender="Recruiter <jobs@talent.example>",
        )

        self.assertEqual(result["noiseDisposition"], "unsolicited_low_value")
        self.assertIn("cold_recruitment_outreach", result["noiseReasons"])

    def test_newsletter_with_list_unsubscribe_is_bulk_marketing(self):
        result = assess(
            "August industry newsletter",
            "This monthly newsletter contains our latest company updates.",
            sender="News <newsletter@publisher.example>",
            recipient="info@hysteriarecs.com",
            headers=(("List-Unsubscribe", "<mailto:leave@publisher.example>"),),
        )

        self.assertEqual(result["noiseDisposition"], "bulk_marketing")
        self.assertEqual(result["noiseConfidence"], "medium")
        self.assertIn("bulk_mail_evidence", result["noiseReasons"])

    def test_fake_invoice_credential_phishing_is_strong_spam(self):
        result = assess(
            "Urgent invoice account verification",
            "Your mailbox has been suspended after an invoice payment failure. "
            "Verify your account credentials immediately. Click here to restore your account.",
            sender="Billing Security <no-reply@billing-alert.example>",
        )

        self.assertEqual(result["noiseDisposition"], "strong_spam")
        self.assertIn("phishing_credential_request", result["noiseReasons"])
        self.assertIn("cold_call_to_action", result["noiseReasons"])

    def test_legitimate_supplier_invoice_is_not_noise(self):
        result = assess(
            "Invoice 2026-0811",
            "Please find attached invoice 2026-0811 for the agreed mastering work. "
            "Payment is due within 30 days.",
            sender="Supplier Accounts <accounts@supplier.example>",
            recipient="info@hysteriarecs.com",
        )

        self.assert_legitimate(result)

    def test_legitimate_bank_payment_notification_is_not_noise(self):
        result = assess(
            "Payment received",
            "Your bank transfer of EUR 1,250 was completed successfully. "
            "This is a transaction notification; no action is required.",
            sender="Bank <no-reply@bank.example>",
            recipient="finance@hysteriarecs.com",
        )

        self.assert_legitimate(result)

    def test_royalty_statement_is_not_noise(self):
        result = assess(
            "Royalty statement Q2 2026",
            "Attached is the royalty statement for your label catalogue, releases, "
            "streams and earnings for Q2.",
            sender="Royalties <royalties@publisher.example>",
        )

        self.assert_legitimate(result)

    def test_labelworx_distribution_payment_mail_is_not_noise(self):
        result = assess(
            "LabelWorx distribution payment report",
            "Your distribution sales and royalty payment report for the latest "
            "label releases is ready.",
            sender="LabelWorx <notifications@labelworx.com>",
        )

        self.assert_legitimate(result)

    def test_contract_and_rights_mail_is_not_noise(self):
        result = assess(
            "Recording agreement and master rights",
            "Please review the attached agreement covering the master rights and "
            "licence terms for the artist release.",
            sender="Legal <legal@partner.example>",
        )

        self.assert_legitimate(result)

    def test_artist_manager_payment_discussion_is_not_noise(self):
        result = assess(
            "Artist fee payment",
            "The artist manager confirms the agreed performance fee and payment "
            "schedule for the release event.",
            sender="Artist Manager <manager@artist.example>",
        )

        self.assert_legitimate(result)

    def test_release_budget_mail_is_not_noise(self):
        result = assess(
            "Release campaign budget",
            "Here is the agreed release budget for mastering, artwork, radio and "
            "playlist promotion. Please review the attached plan.",
            sender="Project Manager <manager@label.example>",
        )

        self.assert_legitimate(result)

    def test_distributor_payment_report_is_not_noise(self):
        result = assess(
            "Distributor payment report",
            "The attached report lists payment, sales and streaming totals for "
            "your distributed music catalogue.",
            sender="Distributor <reports@distributor.example>",
        )

        self.assert_legitimate(result)

    def test_real_reply_headers_protect_legitimate_business_reply(self):
        result = assess(
            "Re: Business financing for the release",
            "Thanks for your earlier message. We offer flexible financing options "
            "for the agreed release budget. Send me a message after your review.",
            sender="Business Partner <partner@example.com>",
            headers=(
                ("In-Reply-To", "<earlier@example.com>"),
                ("References", "<root@example.com> <earlier@example.com>"),
            ),
        )

        self.assert_legitimate(result)
        self.assertNotIn("no_conversation_evidence", result["noiseReasons"])

    def test_re_subject_without_reply_headers_does_not_bypass_noise(self):
        result = assess(
            f"Re: {LOAN_SUBJECT}",
            LOAN_BODY,
            sender=LOAN_SENDER,
            semantic_classification="reply",
        )

        self.assertEqual(result["noiseDisposition"], "strong_spam")
        self.assertIn("no_conversation_evidence", result["noiseReasons"])

    def test_provider_spam_header_is_supporting_not_absolute(self):
        result = assess(
            "Project notes",
            "Here are the notes from our project meeting.",
            recipient="info@hysteriarecs.com",
            headers=(("X-Spam-Flag", "YES"),),
        )

        self.assertEqual(result["noiseDisposition"], "none")
        self.assertIn("provider_spam_evidence", result["noiseReasons"])

    def test_authentication_failure_header_is_supporting_not_absolute(self):
        result = assess(
            "Project notes",
            "Here are the notes from our project meeting.",
            recipient="info@hysteriarecs.com",
            headers=(
                ("Authentication-Results", "mx.example; spf=fail smtp.mailfrom=sender.example"),
            ),
        )

        self.assertEqual(result["noiseDisposition"], "none")
        self.assertIn("authentication_failure_evidence", result["noiseReasons"])

    def test_music_promo_context_is_not_noise(self):
        result = assess(
            "New remix out now – DJ promo",
            "New track and remix for your radio show and playlist. DJ promo servicing link attached.",
            sender="Artist <artist@example.com>",
        )

        self.assertEqual(result["noiseDisposition"], "none")

    def test_single_keyword_never_decides_a_noise_disposition(self):
        for keyword in (
            "loan",
            "invoice",
            "investment",
            "crypto",
            "SEO",
            "recruiter",
            "offer",
            "password",
            "newsletter",
        ):
            with self.subTest(keyword=keyword):
                result = assess(
                    keyword,
                    keyword,
                    recipient="info@hysteriarecs.com",
                )
                self.assertEqual(result["noiseDisposition"], "none")

    def test_assessment_does_not_leak_raw_header_values(self):
        secret_token = "secret-auth-token-8472"
        secret_reputation = "secret-sender-reputation"
        result = assess(
            LOAN_SUBJECT,
            LOAN_BODY,
            sender=LOAN_SENDER,
            headers=(
                (
                    "Authentication-Results",
                    f"mx.example; dmarc=fail header.from={secret_token}.example",
                ),
                (
                    "X-Spam-Status",
                    f"Yes, score=99 reputation={secret_reputation}",
                ),
            ),
        )

        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn(secret_token, serialized)
        self.assertNotIn(secret_reputation, serialized)
        self.assertNotIn("score=99", serialized)
        self.assert_closed_contract(result)

    def test_over_instance_limit_headers_are_ignored(self):
        headers = tuple(
            (
                "Authentication-Results",
                f"mx{index}.example; spf=fail smtp.mailfrom=sender.example",
            )
            for index in range(17)
        )
        result = assess(
            LOAN_SUBJECT,
            LOAN_BODY,
            sender=LOAN_SENDER,
            headers=headers,
        )

        self.assertEqual(result["noiseDisposition"], "strong_spam")
        self.assertNotIn("authentication_failure_evidence", result["noiseReasons"])

    def test_assessment_is_deterministic(self):
        first = assess(LOAN_SUBJECT, LOAN_BODY, sender=LOAN_SENDER)
        second = assess(LOAN_SUBJECT, LOAN_BODY, sender=LOAN_SENDER)

        self.assertEqual(first, second)

    def test_oauth_gmail_and_custom_imap_preview_paths_have_noise_parity(self):
        source_message = make_message(
            LOAN_SUBJECT,
            LOAN_BODY,
            sender=LOAN_SENDER,
        )
        raw_message = source_message.as_bytes()
        custom_message = message_from_bytes(raw_message)
        fetch_result = {
            "messages": [(custom_message, True, "42", False)],
            "warnings": [],
            "error": None,
        }
        custom_mailbox = MagicMock()
        custom_mailbox.uid.return_value = ("OK", [b"42"])
        routing = {
            "signal": None,
            "ui_signal": "NEW",
            "internalClassification": "unknown",
            "category": "unknown",
        }

        with (
            patch(
                "imap_connect_preview.open_mailbox_connection",
                return_value=custom_mailbox,
            ),
            patch(
                "imap_connect_preview.fetch_recent_messages",
                return_value=fetch_result,
            ),
            patch(
                "imap_connect_preview.resolve_preview_routing",
                return_value=routing,
            ),
            patch(
                "imap_connect_preview.read_selected_mailbox_uid_validity",
                return_value="123",
            ),
            patch(
                "imap_connect_preview.resolve_custom_imap_thread_ids",
                return_value=["imap:rfc:test"],
            ),
        ):
            gmail_message = parse_gmail_message_detail(
                {
                    "id": "gmail-message-42",
                    "threadId": "gmail-thread-42",
                    "labelIds": ["INBOX", "UNREAD"],
                    "raw": base64.urlsafe_b64encode(raw_message)
                    .decode("ascii")
                    .rstrip("="),
                },
                context={
                    "mailbox_email": LOAN_RECIPIENT,
                    "mailbox_id": "gmail-mailbox",
                },
                provider_folder="Inbox",
                requested_message_id="gmail-message-42",
                index=0,
                strict=True,
            )
            custom_status, custom_response = build_connect_preview_response(
                {
                    "provider": "custom_imap",
                    "email": LOAN_RECIPIENT,
                    "password": "test-password",
                    "host": "mail.example.com",
                    "port": 993,
                    "mailboxId": "mailbox-test",
                    "limit": 1,
                }
            )

        self.assertIsNotNone(gmail_message)
        self.assertEqual(custom_status, 200)
        custom_preview = custom_response["messages"][0]
        for field in ("noiseDisposition", "noiseConfidence", "noiseReasons"):
            self.assertEqual(gmail_message[field], custom_preview[field])
        self.assertEqual(gmail_message["noiseDisposition"], "strong_spam")

    def test_oauth_gmail_and_custom_imap_image_campaigns_route_quiet_with_parity(self):
        source_message = make_message(
            "Studio collection update: iconic rooms",
            "View this message online.",
            sender="Audio Studio <mail@studio.example>",
            recipient="info@hysteriarecs.com",
            headers=(("List-Unsubscribe", "<mailto:leave@studio.example>"),),
        )
        source_message.add_alternative(
            """
            <html><body>
              <a href="https://studio.example/sale">
                <img src="https://cdn.studio.example/hero.jpg" alt="Save up to 60%">
              </a>
              <a href="https://studio.example/shop">
                <img src="https://cdn.studio.example/shop.jpg" alt="Shop Now">
              </a>
              <img src="https://cdn.studio.example/rooms.jpg" alt="Iconic Rooms">
            </body></html>
            """,
            subtype="html",
        )
        raw_message = source_message.as_bytes()

        gmail_message = parse_gmail_message_detail(
            {
                "id": "gmail-commercial-42",
                "threadId": "gmail-commercial-thread-42",
                "labelIds": ["INBOX", "UNREAD"],
                "raw": base64.urlsafe_b64encode(raw_message)
                .decode("ascii")
                .rstrip("="),
            },
            context={
                "mailbox_email": "info@hysteriarecs.com",
                "mailbox_id": "gmail-mailbox",
            },
            provider_folder="Inbox",
            requested_message_id="gmail-commercial-42",
            index=0,
            strict=True,
        )
        custom_message = to_message_preview(
            message_from_bytes(raw_message),
            0,
            "info@hysteriarecs.com",
            True,
            "42",
        )

        self.assertIsNotNone(gmail_message)
        for field in (
            "internalClassification",
            "noiseDisposition",
            "noiseConfidence",
            "noiseReasons",
            "v7_final_priority",
            "final_visibility",
            "action",
        ):
            self.assertEqual(gmail_message[field], custom_message[field])
        self.assertEqual(gmail_message["internalClassification"], "workflow_update")
        self.assertEqual(gmail_message["noiseDisposition"], "bulk_marketing")
        self.assertEqual(gmail_message["v7_final_priority"], "LOW")
        self.assertEqual(gmail_message["final_visibility"], "show_low")
        self.assertEqual(gmail_message["action"], "show_in_quiet_view")


if __name__ == "__main__":
    unittest.main()
