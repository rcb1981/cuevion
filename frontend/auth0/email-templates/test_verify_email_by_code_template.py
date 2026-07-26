"""Offline contract tests for the Cuevion verification-code email foundation."""

from __future__ import annotations

import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

import generate_verify_email_by_code_preview as preview_generator


DIRECTORY = Path(__file__).resolve().parent
REPOSITORY_ROOT = DIRECTORY.parents[2]
CANONICAL_PATH = DIRECTORY / "verify_email_by_code.html"
MANIFEST_PATH = DIRECTORY / "verify_email_by_code.manifest.json"
PREVIEW_PATH = DIRECTORY / "verify_email_by_code.preview.html"
RUNBOOK_PATH = DIRECTORY.parent / "AUTH0_EMAIL_BRANDING_RUNBOOK.md"

VOID_ELEMENTS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
URL_ATTRIBUTES = {"action", "background", "formaction", "href", "poster", "src", "srcset"}
EMAIL_ADDRESS_PATTERN = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)


class StrictEmailHTMLParser(HTMLParser):
    """Collect email structure and reject unbalanced explicit markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.declarations: list[str] = []
        self.errors: list[str] = []
        self.stack: list[str] = []
        self.tags: list[str] = []
        self.elements: list[tuple[str, dict[str, str], tuple[str, ...]]] = []
        self.text_nodes: list[tuple[str, tuple[str, ...]]] = []

    def handle_decl(self, decl: str) -> None:
        self.declarations.append(decl.lower())

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        normalized_tag = tag.lower()
        normalized_attrs: dict[str, str] = {}
        for name, value in attrs:
            normalized_name = name.lower()
            if normalized_name in normalized_attrs:
                self.errors.append(
                    f"duplicate {normalized_name!r} attribute on <{normalized_tag}>"
                )
            normalized_attrs[normalized_name] = "" if value is None else value
        self.tags.append(normalized_tag)
        self.elements.append((normalized_tag, normalized_attrs, tuple(self.stack)))
        if normalized_tag not in VOID_ELEMENTS:
            self.stack.append(normalized_tag)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        normalized_tag = tag.lower()
        if normalized_tag not in VOID_ELEMENTS:
            self.errors.append(f"non-void <{normalized_tag}> used as a void element")
        self.handle_starttag(normalized_tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in VOID_ELEMENTS:
            self.errors.append(f"void <{normalized_tag}> has an end tag")
            return
        if not self.stack:
            self.errors.append(f"unexpected </{normalized_tag}>")
            return
        expected = self.stack[-1]
        if expected != normalized_tag:
            self.errors.append(
                f"unexpected </{normalized_tag}> while <{expected}> is open"
            )
            return
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        self.text_nodes.append((data, tuple(self.stack)))

    def finish(self) -> None:
        self.close()
        if self.stack:
            self.errors.append(f"unclosed tags: {', '.join(self.stack)}")


def parse_document(source: str) -> StrictEmailHTMLParser:
    parser = StrictEmailHTMLParser()
    parser.feed(source)
    parser.finish()
    return parser


def body_plaintext(parser: StrictEmailHTMLParser) -> str:
    excluded = {"head", "script", "style"}
    values = [
        data
        for data, ancestors in parser.text_nodes
        if "body" in ancestors and not excluded.intersection(ancestors)
    ]
    return " ".join(" ".join(values).split())


def elements_named(
    parser: StrictEmailHTMLParser,
    tag_name: str,
) -> list[tuple[dict[str, str], tuple[str, ...]]]:
    return [
        (attrs, ancestors)
        for tag, attrs, ancestors in parser.elements
        if tag == tag_name
    ]


class VerificationEmailTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.canonical_bytes = CANONICAL_PATH.read_bytes()
        cls.preview_bytes = PREVIEW_PATH.read_bytes()
        cls.canonical = cls.canonical_bytes.decode("utf-8", errors="strict")
        cls.preview = cls.preview_bytes.decode("utf-8", errors="strict")
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
        cls.canonical_parser = parse_document(cls.canonical)
        cls.preview_parser = parse_document(cls.preview)
        cls.canonical_text = body_plaintext(cls.canonical_parser)
        cls.preview_text = body_plaintext(cls.preview_parser)
        cls.deployable_artifacts = "\n".join(
            (
                cls.canonical,
                cls.preview,
                json.dumps(cls.manifest, sort_keys=True),
            )
        )

    def test_01_template_id_is_exact(self) -> None:
        self.assertEqual(
            self.manifest["auth0_template_id"],
            "verify_email_by_code",
        )

    def test_02_subject_is_exact(self) -> None:
        self.assertEqual(
            self.manifest["subject"],
            "Your Cuevion verification code",
        )

    def test_03_from_display_name_is_exact(self) -> None:
        self.assertEqual(self.manifest["expected_from_display_name"], "Cuevion")

    def test_04_canonical_has_only_the_approved_liquid_placeholder(self) -> None:
        self.assertEqual(self.canonical.count("{{ code }}"), 1)
        interpolations = re.findall(r"{{\s*([^{}]+?)\s*}}", self.canonical)
        self.assertEqual(interpolations, ["code"])
        self.assertNotIn("{%", self.canonical)
        self.assertNotIn("%}", self.canonical)

    def test_05_canonical_has_no_hardcoded_six_digit_code(self) -> None:
        self.assertEqual(re.findall(r"\b\d{6}\b", self.canonical_text), [])

    def test_06_preview_has_only_the_sample_code(self) -> None:
        sample_code = self.manifest["preview"]["sample_code"]
        self.assertEqual(sample_code, "547293")
        self.assertEqual(re.findall(r"\b\d{6}\b", self.preview_text), [sample_code])
        self.assertEqual(self.preview.count(sample_code), 1)
        self.assertNotIn("{{", self.preview)
        self.assertNotIn("}}", self.preview)
        titles = elements_named(self.preview_parser, "title")
        self.assertEqual(len(titles), 1)
        title_texts = [
            data.strip()
            for data, ancestors in self.preview_parser.text_nodes
            if ancestors and ancestors[-1] == "title" and data.strip()
        ]
        self.assertEqual(
            title_texts,
            ["LOCAL PREVIEW — Cuevion verification email"],
        )

    def test_07_preview_is_an_exact_canonical_derivative(self) -> None:
        sample_code = self.manifest["preview"]["sample_code"]
        expected = self.canonical.replace("{{ code }}", sample_code, 1)
        self.assertEqual(preview_generator.render_preview(self.canonical), expected)
        self.assertEqual(self.preview, expected)

    def test_08_visible_copy_has_no_default_provider_branding(self) -> None:
        lowered = self.canonical_text.lower()
        self.assertNotIn("auth0", lowered)
        for exact_copy in (
            "Your Cuevion verification code is ready.",
            "Verify your email",
            "Use the verification code below to continue setting up your Cuevion workspace.",
            "This code expires shortly. If you did not request this email, you can safely ignore it.",
            "Secure email workflows for modern music teams.",
        ):
            self.assertIn(exact_copy, self.canonical_text)

    def test_09_template_has_no_environment_identifier(self) -> None:
        self.assertNotIn("cuevion-dev", self.canonical.lower())

    def test_10_template_has_no_development_copy(self) -> None:
        self.assertNotIn("development", self.canonical.lower())

    def test_11_template_has_no_remote_image_reference(self) -> None:
        for tag, attrs, _ancestors in self.canonical_parser.elements:
            if tag in {"image", "img", "source"}:
                for attribute in URL_ATTRIBUTES:
                    self.assertFalse(attrs.get(attribute, "").strip())
        self.assertNotRegex(self.canonical.lower(), r"background(?:-image)?\s*:\s*url\(")

    def test_12_template_has_no_img_element(self) -> None:
        self.assertNotIn("img", self.canonical_parser.tags)

    def test_13_template_has_no_script(self) -> None:
        self.assertNotIn("script", self.canonical_parser.tags)
        self.assertNotRegex(self.canonical.lower(), r"\bon[a-z]+\s*=")

    def test_14_template_has_no_iframe(self) -> None:
        self.assertNotIn("iframe", self.canonical_parser.tags)

    def test_15_template_has_no_form(self) -> None:
        self.assertFalse({"form", "input", "button"}.intersection(self.canonical_parser.tags))

    def test_16_template_has_no_external_stylesheet(self) -> None:
        stylesheet_links = [
            attrs
            for attrs, _ancestors in elements_named(self.canonical_parser, "link")
            if "stylesheet" in attrs.get("rel", "").lower().split()
        ]
        self.assertEqual(stylesheet_links, [])

    def test_17_template_has_no_external_font(self) -> None:
        lowered = self.canonical.lower()
        self.assertNotIn("@font-face", lowered)
        self.assertNotIn("fonts.", lowered)
        self.assertNotRegex(lowered, r"font-family\s*:\s*[^;]*url\(")

    def test_18_template_has_no_tracking_pixel(self) -> None:
        self.assertNotIn("tracking-pixel", self.canonical.lower())
        for _tag, attrs, _ancestors in self.canonical_parser.elements:
            style = re.sub(r"\s+", "", attrs.get("style", "").lower())
            one_by_one_attrs = (
                attrs.get("width") == "1" and attrs.get("height") == "1"
            )
            one_by_one_style = "width:1px" in style and "height:1px" in style
            self.assertFalse(one_by_one_attrs or one_by_one_style)

    def test_19_template_makes_no_external_http_request(self) -> None:
        lowered = self.canonical.lower()
        for marker in ("http://", "https://", "url(", "@import", "data:image"):
            self.assertNotIn(marker, lowered)
        for _tag, attrs, _ancestors in self.canonical_parser.elements:
            for attribute in URL_ATTRIBUTES:
                value = attrs.get(attribute, "").strip()
                self.assertFalse(
                    value.startswith(("http:", "https:", "//", "data:")),
                    f"external resource in {attribute}: {value}",
                )
        refresh_values = [
            attrs.get("http-equiv", "").lower()
            for attrs, _ancestors in elements_named(self.canonical_parser, "meta")
        ]
        self.assertNotIn("refresh", refresh_values)

    def test_20_artifacts_have_no_email_address_or_personal_data(self) -> None:
        self.assertIsNone(EMAIL_ADDRESS_PATTERN.search(self.deployable_artifacts))
        for forbidden in ("rutger", "hysteria", "recipient_name", "user.email"):
            self.assertNotIn(forbidden, self.deployable_artifacts.lower())

    def test_21_artifacts_have_no_mailbox_credentials(self) -> None:
        self.assertNotRegex(
            self.deployable_artifacts,
            r"(?i)\b(?:gmail|imap|smtp|mailbox)[_-]?"
            r"(?:password|secret|token|credential)s?\b\s*[:=]",
        )

    def test_22_artifacts_have_no_client_secret(self) -> None:
        self.assertNotRegex(
            self.deployable_artifacts,
            r"(?i)\bclient[_ -]?secret\b\s*[:=]",
        )

    def test_23_artifacts_have_no_access_token(self) -> None:
        self.assertNotRegex(
            self.deployable_artifacts,
            r"(?i)\b(?:access[_ -]?token|bearer)\b\s*[:= ]",
        )
        self.assertNotRegex(self.deployable_artifacts, r"\beyJ[A-Za-z0-9_-]{20,}\b")

    def test_24_template_has_no_application_name_dependency(self) -> None:
        self.assertNotIn("application.name", self.canonical.lower())

    def test_25_template_has_no_tenant_dependency(self) -> None:
        self.assertNotIn("tenant", self.canonical.lower())

    def test_26_template_is_strictly_parseable_html(self) -> None:
        self.assertEqual(self.canonical_parser.errors, [])
        self.assertEqual(self.canonical_parser.declarations, ["doctype html"])
        self.assertEqual(self.canonical_parser.tags.count("html"), 1)
        self.assertEqual(self.canonical_parser.tags.count("head"), 1)
        self.assertEqual(self.canonical_parser.tags.count("body"), 1)
        html_elements = elements_named(self.canonical_parser, "html")
        self.assertEqual(html_elements[0][0].get("lang"), "en")

    def test_27_template_has_mobile_viewport_metadata(self) -> None:
        viewport_values = [
            attrs.get("content", "").replace(" ", "").lower()
            for attrs, _ancestors in elements_named(self.canonical_parser, "meta")
            if attrs.get("name", "").lower() == "viewport"
        ]
        self.assertEqual(viewport_values, ["width=device-width,initial-scale=1"])

    def test_28_template_is_utf8_and_declares_it(self) -> None:
        self.assertEqual(
            self.canonical_bytes.decode("utf-8", errors="strict"),
            self.canonical,
        )
        charset_values = [
            attrs.get("charset", "").lower()
            for attrs, _ancestors in elements_named(self.canonical_parser, "meta")
            if "charset" in attrs
        ]
        self.assertEqual(charset_values, ["utf-8"])

    def test_29_main_layout_uses_email_safe_tables(self) -> None:
        tables = elements_named(self.canonical_parser, "table")
        self.assertGreaterEqual(len(tables), 4)
        self.assertTrue(all(attrs.get("role") == "presentation" for attrs, _ in tables))
        full_width_tables = [
            attrs
            for attrs, _ancestors in tables
            if attrs.get("width") == "100%"
        ]
        self.assertGreaterEqual(len(full_width_tables), 3)
        card_styles = [
            attrs.get("style", "")
            for attrs, _ancestors in tables
            if "email-card" in attrs.get("class", "").split()
        ]
        self.assertEqual(len(card_styles), 1)
        match = re.search(r"max-width:\s*(\d+)px", card_styles[0])
        self.assertIsNotNone(match)
        self.assertGreaterEqual(int(match.group(1)), 560)
        self.assertLessEqual(int(match.group(1)), 620)

    def test_30_code_survives_plaintext_extraction(self) -> None:
        canonical_nodes = [
            ancestors
            for data, ancestors in self.canonical_parser.text_nodes
            if data.strip() == "{{ code }}"
        ]
        preview_nodes = [
            ancestors
            for data, ancestors in self.preview_parser.text_nodes
            if data.strip() == "547293"
        ]
        self.assertEqual(len(canonical_nodes), 1)
        self.assertEqual(len(preview_nodes), 1)
        for ancestors in canonical_nodes + preview_nodes:
            self.assertFalse({"a", "head", "script", "style"}.intersection(ancestors))
        self.assertIn("{{ code }}", self.canonical_text)
        self.assertIn("547293", self.preview_text)
        self.assertNotIn("user-select: none", self.canonical.lower())
        code_elements = [
            attrs
            for attrs, _ancestors in elements_named(self.canonical_parser, "p")
            if "code-text" in attrs.get("class", "").split()
        ]
        self.assertEqual(len(code_elements), 1)
        self.assertIn("text-align: center", code_elements[0].get("style", ""))

    def test_31_template_size_is_reasonable(self) -> None:
        self.assertLess(len(self.canonical_bytes), 32 * 1024)
        self.assertLess(len(self.preview_bytes), 32 * 1024)

    def test_32_manifest_and_files_are_consistent(self) -> None:
        self.assertEqual(self.manifest["schema_version"], 1)
        self.assertRegex(self.manifest["template_version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(self.manifest["syntax"], "liquid")
        self.assertIs(self.manifest["enabled"], True)
        from_policy = self.manifest["from_address_policy"]
        self.assertIs(from_policy["operator_supplied"], True)
        self.assertIs(from_policy["dedicated_sender_required"], True)
        self.assertIs(from_policy["must_be_externally_verified"], True)
        self.assertIsNone(from_policy["value"])
        canonical_from_manifest = REPOSITORY_ROOT / self.manifest["canonical_template"]
        preview_from_manifest = REPOSITORY_ROOT / self.manifest["preview"]["file"]
        self.assertEqual(canonical_from_manifest.resolve(), CANONICAL_PATH.resolve())
        self.assertEqual(preview_from_manifest.resolve(), PREVIEW_PATH.resolve())
        self.assertTrue(canonical_from_manifest.is_file())
        self.assertTrue(preview_from_manifest.is_file())
        self.assertEqual(
            self.manifest["preview"]["derivation"],
            "replace_exact_liquid_placeholder",
        )
        self.assertIs(self.manifest["preview"]["deployable"], False)
        self.assertEqual(
            self.manifest["preview"]["sample_code"],
            preview_generator.SAMPLE_CODE,
        )

    def test_33_runbook_covers_provider_security_test_and_rollback(self) -> None:
        normalized = " ".join(self.runbook.lower().split())
        required_phrases = (
            "branding → email provider",
            "built-in provider",
            "external provider is required",
            "dedicated smtp provider",
            "supported email integration",
            "custom provider",
            "spf",
            "dkim",
            "dmarc",
            "branding → email templates → verification email (code)",
            "**from**",
            "**subject**",
            "**message**",
            "**enabled**",
            "real cuevion login/email-verification flow",
            "desktop and mobile",
            "light and dark",
            "received message headers",
            "export the current verification email (code)",
            "restore the previous",
            "do not remove or disconnect the provider configuration",
            "do not disable the verification flow",
        )
        for phrase in required_phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, normalized)


if __name__ == "__main__":
    unittest.main()
