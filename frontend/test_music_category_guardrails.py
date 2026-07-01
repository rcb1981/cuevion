import os
import sys
import unittest
from email.message import EmailMessage
from pathlib import Path


os.environ.setdefault("OPENAI_API_KEY", "test-key")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from imap_connect_preview import resolve_preview_routing
from imap_live_v6_5_5_stable import (  # noqa: E402
    USER_LINK_SETTINGS,
    apply_deterministic_music_category_guardrails,
    extract_all_links,
)


def make_message(subject, body, sender="Sender <sender@example.com>", to="promo@hysteriarecs.com", **headers):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to

    for key, value in headers.items():
        message[key.replace("_", "-")] = value

    message.set_content(body)
    return message


class MusicCategoryGuardrailTests(unittest.TestCase):
    def preview(self, subject, body, to="promo@hysteriarecs.com", sender="Sender <sender@example.com>", **headers):
        message = make_message(subject, body, sender=sender, to=to, **headers)
        return resolve_preview_routing(message, to)

    def test_promo_submission_for_hysteria_radio_is_promo(self):
        result = self.preview(
            "Promo Submission for Hysteria Radio",
            "Hi, I send you a promo for my upcoming release and your radio show. "
            "It will be released on July 3rd. Here's the download link: "
            "https://www.dropbox.com/s/example/hysteria-radio.wav?dl=0",
        )

        self.assertEqual(result["category"], "promo")
        self.assertEqual(result["ui_signal"], "PROMO")
        self.assertNotEqual(result["category"], "demo")

    def test_dutch_short_promo_subject_with_download_link_is_promo(self):
        result = self.preview(
            "Promo nieuw vuur!",
            "Hier echt een dikke promo!! https://www.dropbox.com/s/example/nieuw-vuur.wav?dl=0",
            sender="Rutger <rutger@example.com>",
            to="carltricksmusic@gmail.com",
        )

        self.assertEqual(result["category"], "promo")
        self.assertEqual(result["ui_signal"], "PROMO")
        self.assertEqual(
            result.get("deterministic_category_reason"),
            "standalone_promo_subject_with_music_context",
        )
        self.assertNotEqual(result["category"], "demo")
        self.assertEqual(result.get("usable_demo_links"), [])

    def test_nieuwe_promo_support_language_is_promo(self):
        result = self.preview(
            "Nieuwe promo",
            "Hierbij de promo voor support: https://www.dropbox.com/s/example/support.wav?dl=0",
            to="musiclover@example.com",
        )

        self.assertEqual(result["category"], "promo")
        self.assertEqual(result["ui_signal"], "PROMO")

    def test_dikke_promo_voor_je_set_is_promo(self):
        result = self.preview(
            "Dikke promo voor je set",
            "Luister hier: https://soundcloud.com/example/private-promo/s-abc123 "
            "Download: https://www.dropbox.com/s/example/set.wav?dl=0",
            to="dj@example.com",
        )

        self.assertEqual(result["category"], "promo")
        self.assertEqual(result["ui_signal"], "PROMO")

    def test_universal_promo_subject_tokens_are_promo_with_music_context(self):
        cases = [
            (
                "Neue Promo für dich",
                "Listen and download: https://www.dropbox.com/s/example/neue-promo.wav?dl=0",
                "standalone_promo_subject_with_music_context",
            ),
            (
                "Nuova promo per radio",
                "Radio support link: https://soundcloud.com/example/nuova-promo/s-abc123",
                "standalone_promo_subject_with_music_context",
            ),
            (
                "Promo 新歌",
                "DJ support: https://drive.google.com/file/d/example/view",
                "standalone_promo_subject_with_music_context",
            ),
            (
                "Promos for your set",
                "Download for your sets: https://www.dropbox.com/s/example/promos.wav?dl=0",
                "standalone_promos_subject_with_music_context",
            ),
        ]

        for subject, body, reason in cases:
            with self.subTest(subject=subject):
                result = self.preview(subject, body, to="dj@example.com")

                self.assertEqual(result["category"], "promo")
                self.assertEqual(result["ui_signal"], "PROMO")
                self.assertEqual(result.get("deterministic_category_reason"), reason)
                self.assertEqual(result.get("usable_demo_links"), [])

    def test_promo_for_label_with_release_support_link_is_promo(self):
        result = self.preview(
            "Promo For Hysteria Records",
            "New release out soon. Would love DJ support and radio support. "
            "Download link: https://www.dropbox.com/s/example/release.wav?dl=0",
        )

        self.assertEqual(result["category"], "promo")
        self.assertEqual(result["ui_signal"], "PROMO")

    def test_demo_submission_with_music_link_is_demo(self):
        result = self.preview(
            "Demo Submission",
            "Demo submission for your label. Please consider this track and I would love your feedback: "
            "https://soundcloud.com/example/private-demo/s-abc123",
            to="demo@hysteriarecs.com",
        )

        self.assertIn(result["category"], ["demo", "high_priority_demo"])
        self.assertEqual(result["ui_signal"], "DEMO")
        self.assertNotEqual(result["category"], "promo")

    def test_universal_demo_subject_tokens_are_demo_with_music_context(self):
        cases = [
            (
                "Demos for Hysteria",
                "Please consider these tracks: https://www.dropbox.com/s/example/demos.wav?dl=0",
                "standalone_demos_subject_with_music_context",
            ),
            (
                "Neue Demo",
                "Unreleased track for your label: https://soundcloud.com/example/neue-demo/s-abc123",
                "standalone_demo_subject_with_music_context",
            ),
            (
                "Nuova demo",
                "Producer submission for label consideration: https://drive.google.com/file/d/example/view",
                "standalone_demo_subject_with_music_context",
            ),
        ]

        for subject, body, reason in cases:
            with self.subTest(subject=subject):
                result = self.preview(subject, body, to="demo@hysteriarecs.com")

                self.assertIn(result["category"], ["demo", "high_priority_demo"])
                self.assertEqual(result["ui_signal"], "DEMO")
                self.assertEqual(result.get("deterministic_category_reason"), reason)
                self.assertNotEqual(result["category"], "promo")

    def test_software_demo_without_music_context_is_not_music_demo(self):
        result = self.preview(
            "Product Demo",
            "Book a software demo to see campaign analytics and collaboration features.",
            sender="SaaS <hello@example.com>",
            to="info@hysteriarecs.com",
        )

        self.assertNotIn(result["category"], ["demo", "high_priority_demo", "incomplete_demo"])
        self.assertNotEqual(result["ui_signal"], "DEMO")

    def test_website_demo_form_with_soundcloud_tracking_is_demo_not_finance(self):
        result = self.preview(
            "Demo Submission via website",
            "Name: Test Producer\n"
            "Email: producer@example.com\n"
            "SoundCloud: https://soundcloud.com/example/private-demo/s-abc123"
            "?si=123&utm_source=clipboard&utm_medium=text&utm_campaign=social_sharing\n"
            "Description: Please consider this track for Hysteria Records.",
            sender='"Hysteriarecs.com" <demo@hysteriarecs.com>',
            to="demo@hysteriarecs.com",
        )

        self.assertIn(result["category"], ["demo", "high_priority_demo"])
        self.assertEqual(result["ui_signal"], "DEMO")
        self.assertNotEqual(result["category"], "finance")
        self.assertEqual(
            result.get("deterministic_category_reason"),
            "demo_subject_website_form_context",
        )

    def test_finance_result_is_overridden_by_website_demo_form_context(self):
        subject = "Demo Submission via website"
        body = (
            "Name: Test Producer\n"
            "Email: producer@example.com\n"
            "SoundCloud: https://soundcloud.com/example/private-demo/s-abc123"
            "?si=123&utm_source=clipboard&utm_medium=text&utm_campaign=social_sharing\n"
            "Description: Please consider this track for Hysteria Records."
        )
        extracted_links = extract_all_links(body, "", subject=subject, artist_name="Test Producer")
        result = apply_deterministic_music_category_guardrails(
            result={"category": "finance", "usable_demo_links": []},
            subject=subject,
            body=body,
            sender_email="demo@hysteriarecs.com",
            to_header="demo@hysteriarecs.com",
            inbox_profile="demo_first",
            extracted_links=extracted_links,
            user_link_settings=USER_LINK_SETTINGS,
        )

        self.assertEqual(result["category"], "demo")
        self.assertEqual(
            result.get("deterministic_category_reason"),
            "demo_subject_website_form_context",
        )
        self.assertNotEqual(result["category"], "finance")

    def test_website_demo_form_with_dropbox_link_is_demo_not_finance(self):
        result = self.preview(
            "Demo Submission via website",
            "Name: Test Producer\n"
            "Email: producer@example.com\n"
            "SoundCloud: \n"
            "Description: Demo submission for the label.\n"
            "Dropbox: https://www.dropbox.com/s/example/demo.wav?dl=0&utm_campaign=social_sharing",
            sender='"Hysteriarecs.com" <demo@hysteriarecs.com>',
            to="demo@hysteriarecs.com",
        )

        self.assertIn(result["category"], ["demo", "high_priority_demo"])
        self.assertEqual(result["ui_signal"], "DEMO")
        self.assertNotEqual(result["category"], "finance")

    def test_website_demo_form_without_music_link_is_incomplete_demo_not_finance(self):
        result = self.preview(
            "Demo Submission via website",
            "Name: Test Producer\n"
            "Email: producer@example.com\n"
            "SoundCloud: \n"
            "Description: I would like to submit a demo for your label.",
            sender='"Hysteriarecs.com" <demo@hysteriarecs.com>',
            to="demo@hysteriarecs.com",
        )

        self.assertEqual(result["category"], "incomplete_demo")
        self.assertEqual(result["ui_signal"], "DEMO")
        self.assertNotEqual(result["category"], "finance")

    def test_demo_without_link_is_incomplete_demo(self):
        result = self.preview(
            "Demo",
            "Submitting my demo for your label. I would love your feedback and label consideration.",
            to="demo@hysteriarecs.com",
        )

        self.assertEqual(result["category"], "incomplete_demo")
        self.assertEqual(result["ui_signal"], "DEMO")

    def test_meta_ads_promotion_approved_is_not_music_promo(self):
        result = self.preview(
            "Meta Ads promotion approved",
            "Your campaign approved notification for Meta Ads. Billing and advertising account status update.",
            sender="Meta <no-reply@facebookmail.com>",
            to="info@hysteriarecs.com",
        )

        self.assertNotIn(result["category"], ["promo", "demo", "high_priority_demo", "incomplete_demo"])
        self.assertIn(result["category"], ["finance", "workflow_update", "info", "unknown"])

    def test_promotion_is_not_standalone_promo_token(self):
        result = self.preview(
            "Promotion approved",
            "Your advertising promotion was approved for campaign delivery.",
            sender="Platform <notifications@example.com>",
            to="info@hysteriarecs.com",
        )

        self.assertNotIn(result["category"], ["promo", "demo", "high_priority_demo", "incomplete_demo"])
        self.assertNotEqual(result["ui_signal"], "PROMO")

    def test_meta_ads_receipt_with_sender_context_stays_finance(self):
        result = self.preview(
            "Meta Ads payment receipt",
            "Receipt for your Meta Ads campaign. Ad account ID 123. Billing payment received.",
            sender="Meta Ads <billing@facebookmail.com>",
            to="info@hysteriarecs.com",
        )

        self.assertEqual(result["category"], "finance")
        self.assertEqual(result["ui_signal"], "FINANCE")

    def test_invoice_payment_mail_stays_finance(self):
        result = self.preview(
            "Invoice payment receipt",
            "Invoice 123 was paid. This receipt confirms payment for your billing statement.",
            sender="Accounts <billing@example.com>",
            to="info@hysteriarecs.com",
        )

        self.assertEqual(result["category"], "finance")
        self.assertEqual(result["ui_signal"], "FINANCE")

    def test_soundcloud_tracking_campaign_does_not_trigger_finance(self):
        result = self.preview(
            "New music",
            "Sharing this link: https://soundcloud.com/example/new-track/s-abc123"
            "?utm_campaign=social_sharing&utm_source=clipboard&si=abc",
            sender="Artist <artist@example.com>",
            to="info@hysteriarecs.com",
        )

        self.assertNotEqual(result["category"], "finance")
        self.assertNotEqual(result["ui_signal"], "FINANCE")

    def test_dutch_demo_with_label_intent_and_link_is_demo(self):
        result = self.preview(
            "Demo nieuw vuur",
            "Demo submission voor jullie label. Please consider this track: "
            "https://www.dropbox.com/s/example/demo-nieuw-vuur.wav?dl=0",
            to="demo@hysteriarecs.com",
        )

        self.assertIn(result["category"], ["demo", "high_priority_demo"])
        self.assertEqual(result["ui_signal"], "DEMO")
        self.assertNotEqual(result["category"], "promo")

    def test_reply_context_wins_before_music_intent(self):
        result = self.preview(
            "Re: Promo Submission for Hysteria Radio",
            "Following up with a Dropbox link: https://www.dropbox.com/s/example/followup.wav?dl=0",
            In_Reply_To="<previous@example.com>",
        )

        self.assertEqual(result["category"], "reply")
        self.assertEqual(result["ui_signal"], "REPLY")

    def test_platform_categories_win_before_music_intent(self):
        trackstack = self.preview(
            "Trackstack demo promo submission",
            "A demo and promo update is available in Trackstack.",
            sender="Trackstack <notifications@trackstack.com>",
            to="demo@hysteriarecs.com",
        )
        labelradar = self.preview(
            "LabelRadar promo demo update",
            "Your LabelRadar demo update mentions promo support.",
            sender="LabelRadar <mail@labelradar.com>",
            to="demo@hysteriarecs.com",
        )

        self.assertEqual(trackstack["category"], "trackstack_submission")
        self.assertEqual(labelradar["category"], "labelradar_update")

    def test_promo_reminder_stays_promo_reminder(self):
        result = self.preview(
            "Reminder: support this release",
            "Friendly reminder to support the promo. Listen and download for radio support.",
        )

        self.assertEqual(result["category"], "promo_reminder")
        self.assertEqual(result["ui_signal"], "PROMO")

    def test_link_without_intent_does_not_auto_demo(self):
        subject = "New music"
        body = "Sharing a link: https://www.dropbox.com/s/example/new-music.wav?dl=0"
        extracted_links = extract_all_links(body, "", subject=subject, artist_name="Sender")
        result = apply_deterministic_music_category_guardrails(
            result={"category": "unknown", "usable_demo_links": []},
            subject=subject,
            body=body,
            sender_email="sender@example.com",
            to_header="promo@hysteriarecs.com",
            inbox_profile="promo_first",
            extracted_links=extracted_links,
            user_link_settings=USER_LINK_SETTINGS,
        )

        self.assertEqual(result["category"], "unknown")
        self.assertEqual(result["usable_demo_links"], [])

    def test_for_hysteria_with_link_only_does_not_auto_demo(self):
        result = self.preview(
            "For Hysteria",
            "https://www.dropbox.com/s/example/for-hysteria.wav?dl=0",
            to="info@hysteriarecs.com",
        )

        self.assertNotIn(result["category"], ["demo", "high_priority_demo", "incomplete_demo"])
        self.assertNotEqual(result["ui_signal"], "DEMO")

    def test_existing_demo_with_short_promo_subject_is_overridden_to_promo(self):
        subject = "Promo nieuw vuur!"
        body = "Hier echt een dikke promo!! https://www.dropbox.com/s/example/nieuw-vuur.wav?dl=0"
        extracted_links = extract_all_links(body, "", subject=subject, artist_name="Sender")
        result = apply_deterministic_music_category_guardrails(
            result={
                "category": "demo",
                "usable_demo_links": ["https://www.dropbox.com/s/example/nieuw-vuur.wav?dl=0"],
            },
            subject=subject,
            body=body,
            sender_email="sender@example.com",
            to_header="dj@example.com",
            inbox_profile="",
            extracted_links=extracted_links,
            user_link_settings=USER_LINK_SETTINGS,
        )

        self.assertEqual(result["category"], "promo")
        self.assertEqual(
            result.get("deterministic_category_reason"),
            "standalone_promo_subject_with_music_context",
        )
        self.assertEqual(result.get("usable_demo_links"), [])


if __name__ == "__main__":
    unittest.main()
