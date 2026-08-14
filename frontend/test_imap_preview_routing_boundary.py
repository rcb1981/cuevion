import unittest
from email.message import EmailMessage

from imap_connect_preview import to_message_preview


FOCUS_PREFERENCES = {
    "demos": "medium",
    "promo": "medium",
    "finance": "medium",
    "legal": "medium",
    "business": "medium",
    "updates": "medium",
    "distribution": "medium",
    "royalties": "medium",
    "promoReminders": "low",
    "paymentReminders": "medium",
}


def make_message(
    subject: str,
    body: str,
    *,
    sender: str,
    to: str = "promo@hysteriarecs.com",
    headers: tuple[tuple[str, str], ...] = (),
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to
    for name, value in headers:
        message[name] = value
    message.set_content(body)
    return message


def make_preview(
    message: EmailMessage,
    *,
    internal_role: str | None = "label_ar_manager",
    focus_preferences: dict | None = None,
) -> dict:
    return to_message_preview(
        message,
        0,
        message["To"],
        True,
        "1962",
        internal_role=internal_role,
        focus_preferences=focus_preferences or FOCUS_PREFERENCES,
    )


class ImapPreviewRoutingBoundaryTests(unittest.TestCase):
    def test_generic_commercial_newsletter_is_quiet_before_projection(self):
        message = make_message(
            "Improve your mixes this weekend",
            "Our premium effects update: now $29. Shop the offer while it is available.",
            sender="Studio Tools <campaigns@studio-tools.example>",
            to="info@hysteriarecs.com",
            headers=(
                ("List-Unsubscribe", "<mailto:leave@studio-tools.example>"),
                ("Precedence", "bulk"),
            ),
        )

        preview = make_preview(message, internal_role=None)

        self.assertEqual(preview["noiseDisposition"], "bulk_marketing")
        self.assertEqual(preview["v7_final_priority"], "LOW")
        self.assertEqual(preview["final_visibility"], "show_low")
        self.assertEqual(preview["action"], "show_in_quiet_view")
        self.assertEqual(preview["imapUid"], "1962")

    def test_structured_bulk_campaign_is_quiet_without_plaintext_cta_terms(self):
        message = make_message(
            "Studio tools collection update",
            "Explore the latest premium effects collection.",
            sender="Studio Campaigns <campaigns@studio-tools.example>",
            to="info@hysteriarecs.com",
            headers=(
                ("List-Unsubscribe", "<mailto:leave@studio-tools.example>"),
                ("Precedence", "bulk"),
                ("Auto-Submitted", "auto-generated"),
            ),
        )

        preview = make_preview(message, internal_role=None)

        self.assertNotIn("sale", preview["snippet"].lower())
        self.assertNotIn("shop", preview["snippet"].lower())
        self.assertNotIn("unsubscribe", preview["snippet"].lower())
        self.assertEqual(preview["noiseDisposition"], "bulk_marketing")
        self.assertEqual(preview["v7_final_priority"], "LOW")
        self.assertEqual(preview["final_visibility"], "show_low")
        self.assertEqual(preview["action"], "show_in_quiet_view")
        self.assertEqual(preview["imapUid"], "1962")

    def test_bulk_marketing_without_commercial_support_stays_useful(self):
        message = make_message(
            "Monthly account update",
            "Your account settings summary is available.",
            sender="Account Notifications <notifications@example.com>",
            to="info@hysteriarecs.com",
            headers=(
                ("List-Unsubscribe", "<mailto:leave@example.com>"),
                ("Precedence", "bulk"),
                ("Auto-Submitted", "auto-generated"),
            ),
        )

        preview = make_preview(message, internal_role=None)

        self.assertEqual(preview["noiseDisposition"], "bulk_marketing")
        self.assertEqual(preview["internalClassification"], "workflow_update")
        self.assertEqual(preview["v7_final_priority"], "NORMAL")
        self.assertNotEqual(preview["action"], "show_in_quiet_view")

    def test_bulk_headers_do_not_demote_protected_useful_mail(self):
        bulk_headers = (
            ("List-Unsubscribe", "<mailto:leave@notifications.example>"),
            ("Precedence", "bulk"),
        )
        cases = (
            (
                "distributor",
                make_message(
                    "Release delivery completed",
                    "Your release delivery completed and the store delivery status is ready.",
                    sender="Label Worx <delivery@label-worx.com>",
                    to="info@hysteriarecs.com",
                    headers=bulk_headers,
                ),
                "distributor_update",
                "PRIORITY",
            ),
            (
                "payment",
                make_message(
                    "Invoice overdue - payment due",
                    "Please pay invoice 2026-0811 by the due date.",
                    sender="Billing <billing@example.com>",
                    to="info@hysteriarecs.com",
                    headers=bulk_headers,
                ),
                "business_reminder",
                "PRIORITY",
            ),
            (
                "royalty",
                make_message(
                    "Royalty statement available",
                    "Your royalty statement and earnings payout report is ready.",
                    sender="Royalties <royalties@publisher.example>",
                    to="info@hysteriarecs.com",
                    headers=bulk_headers,
                ),
                "royalty_statement",
                "PRIORITY",
            ),
            (
                "reply",
                make_message(
                    "Re: Contract question",
                    "Thanks for your note. Here is the answer.",
                    sender="Alex <alex@example.com>",
                    to="info@hysteriarecs.com",
                    headers=(
                        *bulk_headers,
                        ("In-Reply-To", "<parent@example.com>"),
                        ("References", "<parent@example.com>"),
                    ),
                ),
                "reply",
                "PRIORITY",
            ),
        )

        for name, message, classification, priority in cases:
            with self.subTest(case=name):
                preview = make_preview(message, internal_role=None)
                self.assertEqual(preview["noiseDisposition"], "bulk_marketing")
                self.assertEqual(preview["internalClassification"], classification)
                self.assertEqual(preview["v7_final_priority"], priority)
                self.assertNotEqual(preview["action"], "show_in_quiet_view")

    def test_alxb_promo_reminder_respects_low_and_normal_focus(self):
        message = make_message(
            "(Reminder) Promo Invite from ALXB Records",
            "Hello,\n"
            "ALXB Records personally invites you to receive digital promos via Inflyte.\n"
            "Your promo invite is still available.",
            sender="ALXB Records <promos@inflyteapp.com>",
        )
        cases = (
            ("low", "label_ar_manager", "LOW", "show_low", "show_in_quiet_view"),
            ("low", None, "LOW", "show_low", "show_in_quiet_view"),
            ("medium", "label_ar_manager", "NORMAL", "show_normal", "show_in_main_feed"),
        )

        for preference, internal_role, priority, visibility, action in cases:
            with self.subTest(
                preference=preference,
                internal_role=internal_role,
            ):
                focus_preferences = {
                    **FOCUS_PREFERENCES,
                    "promoReminders": preference,
                }
                preview = make_preview(
                    message,
                    internal_role=internal_role,
                    focus_preferences=focus_preferences,
                )

                self.assertEqual(preview["category"], "promo_reminder")
                self.assertEqual(
                    preview["internalClassification"],
                    "promo_reminder",
                )
                self.assertEqual(preview["ui_signal"], "PROMO")
                self.assertEqual(preview["v7_final_priority"], priority)
                self.assertEqual(preview["final_visibility"], visibility)
                self.assertEqual(preview["action"], action)
                self.assertNotEqual(
                    (
                        preview["internalClassification"],
                        preview["ui_signal"],
                        preview["v7_final_priority"],
                        preview["final_visibility"],
                        preview["action"],
                    ),
                    ("unknown", "NEW", None, None, None),
                )

    def test_non_promo_reminders_do_not_cross_provider_boundary_as_promo(self):
        cases = (
            (
                "payment",
                make_message(
                    "Payment reminder for invoice 2026-0811",
                    "Please pay the outstanding invoice by the due date.",
                    sender="Accounts <billing@example.com>",
                    to="info@hysteriarecs.com",
                ),
            ),
            (
                "contract",
                make_message(
                    "Reminder: contract approval needed",
                    "Please review and approve the contract.",
                    sender="Legal <legal@example.com>",
                    to="info@hysteriarecs.com",
                ),
            ),
            (
                "security",
                make_message(
                    "Security reminder",
                    "Review your account security settings.",
                    sender="Security <security@example.com>",
                    to="info@hysteriarecs.com",
                ),
            ),
        )

        for name, message in cases:
            with self.subTest(case=name):
                preview = make_preview(message)
                self.assertNotEqual(preview["category"], "promo_reminder")
                self.assertNotEqual(
                    preview["internalClassification"],
                    "promo_reminder",
                )
                self.assertNotEqual(
                    (
                        preview["category"],
                        preview["internalClassification"],
                        preview["ui_signal"],
                        preview["v7_final_priority"],
                        preview["final_visibility"],
                        preview["action"],
                    ),
                    (None, "unknown", "NEW", None, None, None),
                )

    def test_ordinary_promo_stays_promo_at_provider_boundary(self):
        message = make_message(
            "New DJ promo available",
            "Listen and download the new release.",
            sender="Promos <promos@example.com>",
        )

        preview = make_preview(message)

        self.assertEqual(preview["category"], "promo")
        self.assertEqual(preview["internalClassification"], "promo")
        self.assertNotEqual(preview["category"], "promo_reminder")


if __name__ == "__main__":
    unittest.main()
