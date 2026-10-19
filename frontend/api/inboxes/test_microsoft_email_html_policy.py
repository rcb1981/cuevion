"""Microsoft inert HTML boundary: markup and images must be safe before UI.

This sanitizer is locally wired to exact Graph message responses, not to
list snapshots. These tests lock down the server boundary of the HTML viewer.
"""
import re
import unittest

from api.inboxes.microsoft_email_html_policy import (
    _safe_https_url,
    _safe_inline_style,
    project_inert_microsoft_html,
    MAX_INPUT_CHARACTERS,
)


class MicrosoftInertHtmlPolicyTests(unittest.TestCase):
    def test_newsletter_table_and_safe_formatting(self):
        result = project_inert_microsoft_html(
            '<div style="font-size: 14px; color: #fff; background-image: url(https://tracker.example/x)">'
            '<h2>News</h2><table width="600"><tr><td bgcolor="#ffffff" align="center">'
            '<a href="https://example.org/offer?x=1&amp;y=2">See offer</a>'
            '</td></tr></table></div>'
        )
        html = result["html"]
        self.assertIn('<table width="600">', html)
        self.assertIn('bgcolor="#ffffff"', html)
        self.assertIn('align="center"', html)
        self.assertIn('style="font-size: 14px; color: #fff"', html)
        self.assertIn("See offer", html)
        self.assertIn('href="https://example.org/offer?x=1&amp;y=2"', html)
        self.assertNotIn("background-image", html)
        self.assertNotIn("tracker.example", html)
        self.assertIn('referrerpolicy="no-referrer"', html)

    def test_common_newsletter_inline_css_stays_network_inert(self):
        html = project_inert_microsoft_html(
            '<table style="width: 600px; min-width: 320px; border-collapse: collapse">'
            '<tr><td style="background-color: rgb(10, 20, 30);'
            'border-radius: 12px; padding: 16px; letter-spacing: 1px;'
            'background-image: url(https://tracker.example/pixel)">Hello'
            '</td></tr></table>'
        )["html"]
        self.assertIn("background-color: rgb(10, 20, 30)", html)
        self.assertIn("border-radius: 12px", html)
        self.assertIn("min-width: 320px", html)
        self.assertNotIn("tracker.example", html)
        self.assertNotIn("background-image", html)
        self.assertEqual(_safe_inline_style("color: rgba(1, 2, 3, 0.4)"),
                         "color: rgba(1, 2, 3, 0.4)")
        self.assertEqual(_safe_inline_style("color: url(https://bad.test/image)"), "")

    def test_removes_scripts_external_resources_and_event_handlers(self):
        source = (
            '<meta http-equiv="refresh" content="0;url=https://tracker.example">'
            '<base href="https://tracker.example/">'
            '<style>@import url(https://tracker.example/style.css)</style>'
            '<svg onload="alert(1)"><foreignObject>Bad</foreignObject></svg>'
            '<p onclick="alert(2)" style="color: red; background: url(https://tracker.example/bg)">Text</p>'
            '<script>fetch("https://tracker.example/visit")</script>'
            '<iframe src="https://tracker.example/frame"></iframe>'
            '<object data="https://tracker.example/object">Bad object</object>'
            '<a href="javascript:alert(4)">Click</a>'
        )
        html = project_inert_microsoft_html(source)["html"]
        for unsafe in (
            "tracker.example", "<script", "<iframe", "<svg", "foreignObject",
            "<style", "javascript:", "onclick", "<base", "<meta", "<object",
            "@import", "Bad object", "alert(", "fetch(",
        ):
            self.assertNotIn(unsafe.lower(), html.lower(), unsafe)
        self.assertIn("Text", html)
        self.assertIn("<a>Click</a>", html)

    def test_remote_images_are_inert_until_user_allows(self):
        result = project_inert_microsoft_html(
            '<p>Hello<img src="https://cdn.example.com/pic.jpg?campaign=123" '
            'alt="Artwork"></p>'
        )
        html = result["html"]
        self.assertEqual(result["remoteImageCount"], 1)
        self.assertIn('data-microsoft-remote-src=', html)
        self.assertNotRegex(html, r"<img\b[^>]*\ssrc=")
        self.assertIn('alt="Artwork"', html)
        self.assertNotIn("onerror", html)
        self.assertEqual(result["cidImageCount"], 0)

    def test_cid_is_inert_and_bad_image_schemes_are_rejected(self):
        result = project_inert_microsoft_html(
            '<img src="cid:cover.42@example.com" alt="Cover">'
            '<img src="data:image/svg+xml;base64,PHN2Zz4=">'
            '<img src="javascript:alert(1)">'
            '<img src="//tracker.example/open.gif">'
            '<img src="http://insecure.example/open.gif">'
        )
        self.assertEqual(result["cidImageCount"], 1)
        self.assertEqual(result["invalidImageCount"], 4)
        self.assertIn('data-microsoft-cid="cover.42@example.com"', result["html"])
        self.assertNotIn("data:image", result["html"])
        self.assertNotIn("javascript:", result["html"])
        self.assertNotIn("src=", result["html"])

    def test_rejects_credentials_control_chars_relative_and_script_links(self):
        for url in (
            "//example.com/pixel", "/photo", "javascript:alert(1)",
            "https://user:pass@example.com/a", "https://example.com/path\n",
            "https://example.com/?hi=a b", "file:///tmp/image.png",
            "data:image/png;base64,ABC", "http://example.com", "https://example.com#fragment",
        ):
            self.assertIsNone(_safe_https_url(url), url)
        self.assertEqual(_safe_https_url("https://cdn.example.com/image.png"), "https://cdn.example.com/image.png")

    def test_css_does_not_admit_urls_expression_or_at_rules(self):
        result = _safe_inline_style(
            "color: red; font-weight: bold; background: url(https://evil.example/pixel);"
            "border: 1px solid #ccc; font-family: Arial; behavior: expression(alert(1));"
        )
        self.assertIn("color: red", result)
        self.assertIn("font-weight: bold", result)
        self.assertIn("border: 1px solid #ccc", result)
        self.assertNotIn("evil.example", result)
        self.assertNotIn("expression", result)

    def test_bad_markup_does_not_escape_allowlist(self):
        res = project_inert_microsoft_html(
            '</div><div><p>Hi</div><form><input name=x>hidden</form>'
            '<img src="https://safe.example/img" onerror="alert(1)">'
        )
        html = res["html"]
        self.assertEqual(html.count("<div>"), 1)
        self.assertEqual(html.count("</div>"), 1)
        self.assertEqual(html.count("<p>"), 1)
        self.assertEqual(html.count("</p>"), 1)
        self.assertNotIn("<input", html)
        self.assertNotIn("hidden", html)
        self.assertEqual(res["remoteImageCount"], 1)

    def test_large_input_fails_closed(self):
        with self.assertRaises(ValueError):
            project_inert_microsoft_html("a" * (MAX_INPUT_CHARACTERS + 1))
        with self.assertRaises(ValueError):
            project_inert_microsoft_html(None)
        with self.assertRaises(ValueError):
            project_inert_microsoft_html(123)

    def test_only_exact_messages_get_server_sanitized_html(self):
        from pathlib import Path
        parent = Path(__file__).parent
        projection = (parent / "microsoft_inbox_projection.py").read_text()
        self.assertIn("project_inert_microsoft_html", projection)
        self.assertIn('if not exact_body:', projection)
        self.assertIn('result["bodyHtml"] = safe_html["html"]', projection)
        client = (parent.parent.parent / "src/lib/inboxConnectionApi.ts").read_text()
        self.assertIn("isTrustedMicrosoftInertHtmlTransport", client)
        self.assertIn("!options.exact", client)


if __name__ == "__main__":
    unittest.main()
