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


if __name__ == "__main__":
    unittest.main()
