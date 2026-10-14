"""Microsoft message links survive projection without rendering untrusted HTML."""
import unittest

from api.inboxes.microsoft_inbox_projection import _PlainTextHTML, _safe_message_href


def plain_markup(value):
    parser = _PlainTextHTML()
    parser.feed(value)
    parser.close()
    return parser.result()


class MicrosoftMessageLinkTests(unittest.TestCase):
    def test_hidden_anchor_target_becomes_text(self):
        html = '<p>Listen <a href="https://soundcloud.com/artist/track?s=abc&amp;t=3">here</a>.</p>'
        self.assertIn(
            "here (https://soundcloud.com/artist/track?s=abc&t=3)",
            plain_markup(html),
        )

    def test_visible_url_is_not_duplicated(self):
        url = "https://disco.ac/s/record"
        self.assertEqual(plain_markup(f'<a href="{url}">{url}</a>'), url)

    def test_unsafe_schemes_are_excluded(self):
        for candidate in (
            "/local", "mailto:person@example.com", "data:payload",
            "javascript:action", "https://user:password@example.com/",
        ):
            self.assertIsNone(_safe_message_href(candidate))

    def test_only_plain_text_is_emitted(self):
        rendered = plain_markup('<b>Bold</b><a href="https://example.com/">Open</a>')
        self.assertNotIn("<b>", rendered)
        self.assertIn("Open (https://example.com/)", rendered)
