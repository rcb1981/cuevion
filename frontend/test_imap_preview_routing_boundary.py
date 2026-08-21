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
    html_body: str | None = None,
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to
    for name, value in headers:
        message[name] = value
    message.set_content(body)
    if html_body is not None:
        message.add_alternative(html_body, subtype="html")
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
    def test_save_up_to_image_campaign_keeps_update_category_but_routes_quiet(self):
        message = make_message(
            "Studio rooms update",
            "View this message online.",
            sender="Studio Rooms <mail@rooms.example>",
            to="info@hysteriarecs.com",
            headers=(("List-Unsubscribe", "<mailto:leave@rooms.example>"),),
            html_body="""
                <html><body>
                  <a href="https://rooms.example/sale">
                    <img src="https://cdn.rooms.example/hero.jpg" alt="Save up to 60%">
                  </a>
                  <a href="https://rooms.example/shop">
                    <img src="https://cdn.rooms.example/shop.jpg" alt="Shop Now">
                  </a>
                  <img src="https://cdn.rooms.example/east.jpg" alt="East room">
                  <img src="https://cdn.rooms.example/west.jpg" alt="Sale ends August 31">
                </body></html>
            """,
        )

        preview = make_preview(message, internal_role=None)

        self.assertEqual(preview["category"], "workflow_update")
        self.assertEqual(preview["internalClassification"], "workflow_update")
        self.assertEqual(preview["noiseDisposition"], "bulk_marketing")
        self.assertEqual(preview["v7_final_priority"], "LOW")
        self.assertEqual(preview["final_visibility"], "show_low")
        self.assertEqual(preview["action"], "show_in_quiet_view")

    def test_existing_save_percentage_image_campaign_still_routes_quiet(self):
        message = make_message(
            "Studio rooms update",
            "View this message online.",
            sender="Studio Rooms <mail@rooms.example>",
            to="info@hysteriarecs.com",
            headers=(("List-Unsubscribe", "<mailto:leave@rooms.example>"),),
            html_body="""
                <html><body>
                  <a href="https://rooms.example/sale">
                    <img src="https://cdn.rooms.example/hero.jpg" alt="Save 60%">
                  </a>
                  <a href="https://rooms.example/shop">
                    <img src="https://cdn.rooms.example/shop.jpg" alt="Shop Now">
                  </a>
                  <img src="https://cdn.rooms.example/east.jpg" alt="East room">
                  <img src="https://cdn.rooms.example/west.jpg" alt="Sale ends August 31">
                </body></html>
            """,
        )

        preview = make_preview(message, internal_role=None)

        self.assertEqual(preview["noiseDisposition"], "bulk_marketing")
        self.assertEqual(preview["v7_final_priority"], "LOW")
        self.assertEqual(preview["final_visibility"], "show_low")
        self.assertEqual(preview["action"], "show_in_quiet_view")

    def test_generic_legal_footer_does_not_protect_commercial_campaign(self):
        message = make_message(
            "Studio rooms update",
            "Save up to 60% on studio software. Shop Now. Unsubscribe. Legal",
            sender="Studio Rooms <mail@rooms.example>",
            to="info@hysteriarecs.com",
            headers=(
                ("List-Unsubscribe", "<mailto:leave@rooms.example>"),
                ("List-Unsubscribe-Post", "List-Unsubscribe=One-Click"),
            ),
            html_body="""
                <html><body>
                  <a href="https://rooms.example/sale">
                    <img src="https://cdn.rooms.example/hero.jpg" alt="Save up to 60%">
                  </a>
                  <a href="https://rooms.example/shop">
                    <img src="https://cdn.rooms.example/shop.jpg" alt="Shop Now">
                  </a>
                  <img src="https://cdn.rooms.example/logo.jpg" alt="Studio products">
                  <footer>Unsubscribe | Legal</footer>
                </body></html>
            """,
        )

        preview = make_preview(message, internal_role=None)

        self.assertEqual(preview["category"], "workflow_update")
        self.assertEqual(preview["internalClassification"], "workflow_update")
        self.assertEqual(preview["noiseDisposition"], "bulk_marketing")
        self.assertEqual(preview["v7_final_priority"], "LOW")
        self.assertEqual(preview["final_visibility"], "show_low")
        self.assertEqual(preview["action"], "show_in_quiet_view")

    def test_actionable_rights_and_legal_mail_stays_protected(self):
        bulk_headers = (
            ("List-Unsubscribe", "<mailto:leave@notifications.example>"),
            ("List-Unsubscribe-Post", "List-Unsubscribe=One-Click"),
        )
        cases = (
            (
                "master rights",
                "Master rights confirmation",
                "Hi Rutger,\nCan you confirm who owns the master rights for this release?",
            ),
            (
                "publishing rights",
                "Publishing rights split",
                "Please review the publishing rights split before we sign the agreement.",
            ),
            (
                "copyright clearance",
                "Copyright clearance",
                "We still need your approval on the copyright clearance.",
            ),
            (
                "legal approval",
                "Legal approval required",
                "Legal approval is required before release.",
            ),
            (
                "rights issue",
                "Rights issue",
                "There is a rights issue with this master.",
            ),
            (
                "legal review",
                "Release legal review",
                "This release is pending legal review.",
            ),
        )

        for name, subject, body in cases:
            with self.subTest(case=name):
                message = make_message(
                    subject,
                    body,
                    sender="Rights Team <notifications@example.com>",
                    to="info@hysteriarecs.com",
                    headers=bulk_headers,
                    html_body=f"""
                        <html><body>
                          <p>Workflow update</p>
                          <main>{body}</main>
                          <a href="https://example.com/catalog">
                            <img src="https://cdn.example.com/catalog.jpg" alt="Product collection">
                          </a>
                          <a href="https://example.com/offer">
                            <img src="https://cdn.example.com/offer.jpg" alt="Shop Now">
                          </a>
                          <img src="https://cdn.example.com/sale.jpg" alt="Save up to 60%">
                        </body></html>
                    """,
                )

                preview = make_preview(message, internal_role=None)

                self.assertEqual(preview["category"], "workflow_update")
                self.assertEqual(preview["internalClassification"], "workflow_update")
                self.assertEqual(preview["noiseDisposition"], "bulk_marketing")
                self.assertNotEqual(preview["v7_final_priority"], "LOW")
                self.assertNotEqual(preview["action"], "show_in_quiet_view")

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

    def test_bulk_template_with_one_commercial_word_stays_useful(self):
        message = make_message(
            "Monthly account update",
            "Your account settings summary is available.",
            sender="Account Notifications <notifications@example.com>",
            to="info@hysteriarecs.com",
            headers=(("List-Unsubscribe", "<mailto:leave@example.com>"),),
            html_body="""
                <html><body>
                  <p>Your account settings summary is available.</p>
                  <a href="https://example.com/summary">
                    <img src="https://cdn.example.com/summary.jpg" alt="Account summary">
                  </a>
                  <a href="https://example.com/details">
                    <img src="https://cdn.example.com/details.jpg" alt="Sale">
                  </a>
                  <img src="https://cdn.example.com/logo.jpg" alt="Company logo">
                </body></html>
            """,
        )

        preview = make_preview(message, internal_role=None)

        self.assertEqual(preview["noiseDisposition"], "bulk_marketing")
        self.assertNotEqual(preview["v7_final_priority"], "LOW")

    def test_save_up_to_price_signal_alone_stays_useful(self):
        message = make_message(
            "Studio rooms update",
            "Save up to 60%",
            sender="Studio Rooms <mail@rooms.example>",
            to="info@hysteriarecs.com",
            headers=(("List-Unsubscribe", "<mailto:leave@rooms.example>"),),
        )

        preview = make_preview(message, internal_role=None)

        self.assertEqual(preview["noiseDisposition"], "bulk_marketing")
        self.assertNotEqual(preview["v7_final_priority"], "LOW")
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

    def test_marketing_footer_does_not_quiet_transactional_or_operational_mail(self):
        bulk_headers = (
            ("List-Unsubscribe", "<mailto:leave@notifications.example>"),
            ("Precedence", "bulk"),
        )
        marketing_footer = """
            <footer>
              <a href="https://example.com/sale">
                <img src="https://cdn.example.com/sale.jpg" alt="Save up to 60%">
              </a>
              <a href="https://example.com/shop">
                <img src="https://cdn.example.com/shop.jpg" alt="Shop Now">
              </a>
              <img src="https://cdn.example.com/products.jpg" alt="Product collection">
            </footer>
        """
        cases = (
            (
                "receipt",
                "Payment receipt for order #12345",
                "Your payment was received. Order #12345 is confirmed.",
                (),
            ),
            (
                "invoice",
                "Invoice attached",
                "Invoice attached. Payment due September 1.",
                (),
            ),
            (
                "security",
                "Security alert",
                "New sign-in detected. Review activity at accounts.google.com.",
                (),
            ),
            (
                "account",
                "Subscription renewal notice",
                "Your subscription will renew tomorrow. Action required to update billing.",
                (),
            ),
            (
                "service incident",
                "Service outage update",
                "The service incident is resolved. Review the outage timeline.",
                (),
            ),
            (
                "returned reply",
                "Re: Project discount",
                "Thanks for your note. The discount works for the project.",
                (
                    ("In-Reply-To", "<parent@example.com>"),
                    ("References", "<parent@example.com>"),
                ),
            ),
        )

        for name, subject, body, conversation_headers in cases:
            with self.subTest(case=name):
                message = make_message(
                    subject,
                    body,
                    sender="Notifications <notifications@example.com>",
                    to="info@hysteriarecs.com",
                    headers=(*bulk_headers, *conversation_headers),
                    html_body=f"<main><p>{body}</p></main>{marketing_footer}",
                )

                preview = make_preview(message, internal_role=None)

                self.assertNotEqual(preview["v7_final_priority"], "LOW")

    def test_human_discount_discussion_is_not_bulk_filtered(self):
        message = make_message(
            "Collaboration project pricing",
            "Hi Rutger,\nWe may be able to save up to 60% on the project.\n"
            "Can we discuss the options?",
            sender="Alex <alex@example.com>",
            to="info@hysteriarecs.com",
        )

        preview = make_preview(message, internal_role=None)

        self.assertEqual(preview["noiseDisposition"], "none")
        self.assertNotEqual(preview["v7_final_priority"], "LOW")
        self.assertNotEqual(preview["action"], "show_in_quiet_view")

    def test_dynamic_mailbox_noncommercial_messages_do_not_gain_v7_weighting(self):
        dynamic_recipient = "artistoffice@example.com"
        cases = (
            (
                "ordinary update",
                "Project workflow update",
                "The project status was updated after today's planning meeting.",
                (),
            ),
            (
                "finance",
                "Finance report",
                "The quarterly finance report and budget summary are attached.",
                (),
            ),
            (
                "business",
                "Partnership meeting",
                "Let's discuss the partnership agenda at tomorrow's meeting.",
                (),
            ),
            (
                "human conversation",
                "Studio planning",
                "Hi Rutger, are you free to discuss the studio plan tomorrow?",
                (),
            ),
            (
                "transactional",
                "Payment receipt",
                "Your payment was received. Receipt 2026-0811 is attached.",
                (),
            ),
            (
                "security",
                "Security alert",
                "A new sign-in was detected on your account.",
                (),
            ),
            (
                "reply",
                "Re: Studio planning",
                "Thanks for your earlier message. Tomorrow works for me.",
                (
                    ("In-Reply-To", "<parent@example.com>"),
                    ("References", "<parent@example.com>"),
                ),
            ),
            (
                "legal rights",
                "Master rights confirmation",
                "Can you confirm who owns the master rights for this release?",
                (),
            ),
        )

        for name, subject, body, headers in cases:
            with self.subTest(case=name):
                message = make_message(
                    subject,
                    body,
                    sender="Project Contact <contact@example.net>",
                    to=dynamic_recipient,
                    headers=headers,
                )

                preview = make_preview(message, internal_role=None)

                self.assertIsNone(preview.get("v7_final_priority"))
                self.assertIsNone(preview.get("final_visibility"))
                self.assertIsNone(preview.get("action"))

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
