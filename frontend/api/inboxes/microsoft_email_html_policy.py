"""Strict, inert HTML projection for a future Microsoft exact-message renderer.

This local WIP is wired only to the exact Graph Inbox message projection,
not to list snapshots or the approved Production code. The allowlist prevents
scripts, automatic remote resource loads and CSS URL fetches before the
independent client DOM sanitizer and sandboxed iframe.

Do not use the policy as the only security boundary. Browser and live-mailbox
verification have not yet been completed.
"""
from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser
import ipaddress
from urllib.parse import urlsplit, urlunsplit

MAX_INPUT_CHARACTERS = 262_144
MAX_OUTPUT_CHARACTERS = 294_912
MAX_IMAGES = 128
MAX_LINKS = 128

_SAFE_TAGS = frozenset((
    "p", "div", "span", "br", "b", "strong", "em", "i", "u", "s",
    "small", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "code",
    "blockquote", "ul", "ol", "li", "table", "thead", "tbody",
    "tfoot", "tr", "td", "th", "hr", "a", "img", "center",
))
_VOID_TAGS = frozenset(("br", "hr", "img"))
_DROP_CONTENT = frozenset((
    "script", "style", "noscript", "template", "svg", "math",
    "iframe", "object", "form", "video", "audio", "canvas",
))
_SAFE_PRESENTATION = frozenset((
    "color", "background-color", "font-size", "font-weight",
    "font-style", "font-family", "line-height", "text-align",
    "text-decoration", "vertical-align", "padding", "padding-top",
    "padding-right", "padding-bottom", "padding-left", "margin",
    "margin-top", "margin-right", "margin-bottom", "margin-left",
    "border", "border-color", "border-width", "border-style",
    "border-collapse", "width", "max-width", "height",
    "min-width", "min-height", "max-height", "border-radius",
    "border-top", "border-right", "border-bottom", "border-left",
    "letter-spacing", "text-transform", "white-space", "word-break",
    "overflow-wrap", "table-layout", "box-sizing", "display",
))
_SAFE_CSS_VALUE = re.compile(r"^[a-zA-Z0-9\s#.,%_+/\-]{1,160}$")
_SAFE_RGB_COLOR = re.compile(r"^(?:rgb\(\s*\d{1,3}(?:\s*,\s*\d{1,3}){2}\s*\)|rgba\(\s*\d{1,3}(?:\s*,\s*\d{1,3}){2}\s*,\s*(?:0(?:\.\d{1,3})?|1(?:\.0{1,3})?)\s*\))$")
_COLOR_STYLES = frozenset(("color", "background-color", "border-color"))
_SAFE_CID = re.compile(r"^[a-zA-Z0-9_.@+%\-]{1,200}$")
_SAFE_SIZE = re.compile(r"^\d{1,4}$")
_SAFE_TABLE_SPAN = re.compile(r"^\d{1,2}$")
_SAFE_ALIGN = frozenset(("left", "right", "center", "justify"))
_SAFE_VALIGN = frozenset(("top", "middle", "bottom", "baseline"))
_SAFE_BG_COLOR = re.compile(r"^#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?$")
_CONTROL_OR_SPACE = re.compile(r"[\x00-\x20\x7f]")


def _safe_https_url(value: object) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        return None
    if value != value.strip() or _CONTROL_OR_SPACE.search(value):
        return None
    try:
        url = urlsplit(value)
        if (
            url.scheme.lower() != "https"
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.fragment
            or url.port is not None and url.port not in (443,)
        ):
            return None
    except ValueError:
        return None
    return value


def _safe_legacy_https_candidate(value: object) -> str | None:
    """Prepare HTTPS-only candidate for a legacy HTTP image; never fetch HTTP.

    The candidate remains inert until the reader opts in and the browser is
    restricted by a HTTPS-only iframe CSP. No server-side network requests.
    """
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        return None
    if value != value.strip() or _CONTROL_OR_SPACE.search(value) or "\\" in value:
        return None
    try:
        url = urlsplit(value)
        hostname = url.hostname
        if (
            url.scheme.lower() != "http" or not hostname
            or url.username is not None or url.password is not None
            or url.fragment or url.port not in (None, 80)
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", hostname)
            or not re.search(r"\.[a-z]{2,63}$", hostname)
        ):
            return None
        if ".." in hostname or any(
            not item or item[0] == "-" or item[-1] == "-"
            for item in hostname.split(".")
        ):
            return None
        try:
            ipaddress.ip_address(hostname)
            return None
        except ValueError:
            pass
        if hostname.endswith((".local", ".localhost", ".internal", ".invalid", ".test", ".example", ".onion", ".lan")):
            return None
        upgraded = urlunsplit(("https", hostname, url.path or "/", url.query, ""))
        return upgraded if _safe_https_url(upgraded) else None
    except (ValueError, UnicodeError):
        return None


def _safe_inline_style(style: object) -> str:
    """Small presentational subset; no CSS functions or external resources."""
    if not isinstance(style, str) or len(style) > 2000:
        return ""
    if any(x in style for x in ("/*", "*/", "\\", "@", "{", "}", "<", ">")):
        return ""
    clean: list[str] = []
    for declaration in style.split(";"):
        prop, sep, val = declaration.partition(":")
        name = prop.strip().lower()
        value = val.strip()
        if (
            not sep or name not in _SAFE_PRESENTATION
            or not (
                _SAFE_CSS_VALUE.fullmatch(value)
                or name in _COLOR_STYLES and _SAFE_RGB_COLOR.fullmatch(value)
            )
            or any(
                token in value.lower()
                for token in ("url", "expression", "javascript", "vbscript", "import", "behavior")
            )
        ):
            continue
        clean.append(f"{name}: {value}")
        if len(clean) >= 24:
            break
    return "; ".join(clean)


class _MicrosoftInertHtml(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.tags: list[str] = []
        self.drop_depth = 0
        self.remote_image_count = 0
        self.legacy_upgrade_count = 0
        self.cid_image_count = 0
        self.invalid_image_count = 0
        self.link_count = 0
        self.output_size = 0

    def _append(self, value: str) -> None:
        self.output_size += len(value)
        if self.output_size > MAX_OUTPUT_CHARACTERS:
            raise ValueError("microsoft_safe_html_output_limit")
        self.parts.append(value)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.drop_depth:
            if tag in _DROP_CONTENT:
                self.drop_depth += 1
            return
        if tag in _DROP_CONTENT:
            self.drop_depth = 1
            return
        if tag not in _SAFE_TAGS:
            return

        raw = {k.lower(): v for k, v in attrs if k and v is not None}
        attr_parts: list[str] = []
        style = _safe_inline_style(raw.get("style"))
        if style:
            attr_parts.append(f' style="{escape(style, quote=True)}"')
        if tag == "a":
            href = _safe_https_url(raw.get("href")) if self.link_count < MAX_LINKS else None
            self.link_count += 1
            if href:
                attr_parts.extend((
                    f' href="{escape(href, quote=True)}"',
                    ' target="_blank"',
                    ' rel="noopener noreferrer"',
                    ' referrerpolicy="no-referrer"',
                ))
        if tag == "img":
            image = raw.get("src", "")
            alt = escape((raw.get("alt") or "Image")[:180], quote=True)
            attr_parts.append(f' alt="{alt}"')
            if self.remote_image_count + self.cid_image_count < MAX_IMAGES:
                remote = _safe_https_url(image)
                if remote:
                    self.remote_image_count += 1
                    # NO src attribute: image stays inert until explicit consent.
                    attr_parts.append(f' data-microsoft-remote-src="{escape(remote, quote=True)}"')
                elif (candidate := _safe_legacy_https_candidate(image)):
                    self.remote_image_count += 1
                    self.legacy_upgrade_count += 1
                    # Only a candidate: no http requests and no live src.
                    attr_parts.append(f' data-microsoft-legacy-https-src="{escape(candidate, quote=True)}"')
                elif image.lower().startswith("cid:") and _SAFE_CID.fullmatch(image[4:]):
                    self.cid_image_count += 1
                    attr_parts.append(f' data-microsoft-cid="{escape(image[4:], quote=True)}"')
                else:
                    self.invalid_image_count += 1
            else:
                self.invalid_image_count += 1

        for key in ("width", "height"):
            if tag in ("table", "td", "th", "img") and key in raw:
                if _SAFE_SIZE.fullmatch(raw[key]):
                    attr_parts.append(f' {key}="{raw[key]}"')
        for key in ("colspan", "rowspan"):
            if tag in ("td", "th") and key in raw and _SAFE_TABLE_SPAN.fullmatch(raw[key]):
                attr_parts.append(f' {key}="{raw[key]}"')
        if tag in ("td", "th", "div", "p"):
            alignment = (raw.get("align") or "").lower()
            if alignment in _SAFE_ALIGN:
                attr_parts.append(f' align="{alignment}"')
            valign = (raw.get("valign") or "").lower()
            if tag in ("td", "th") and valign in _SAFE_VALIGN:
                attr_parts.append(f' valign="{valign}"')
        bg = raw.get("bgcolor")
        if tag in ("td", "th", "table") and bg and _SAFE_BG_COLOR.fullmatch(bg):
            attr_parts.append(f' bgcolor="{bg}"')

        self._append(f"<{tag}{''.join(attr_parts)}>")
        if tag not in _VOID_TAGS:
            self.tags.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.drop_depth:
            if tag in _DROP_CONTENT:
                self.drop_depth -= 1
            return
        if tag not in _SAFE_TAGS or tag in _VOID_TAGS or tag not in self.tags:
            return
        # Close malformed-but-allowed nesting, never construct executable tags.
        while self.tags:
            last = self.tags.pop()
            self._append(f"</{last}>")
            if last == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self.drop_depth:
            self._append(escape(data, quote=False))

    def handle_comment(self, data: str) -> None:
        return

    def finish(self) -> dict[str, object]:
        while self.tags:
            self._append(f"</{self.tags.pop()}>")
        return {
            "html": "".join(self.parts),
            "remoteImageCount": self.remote_image_count,
            "legacyUpgradeCount": self.legacy_upgrade_count,
            "cidImageCount": self.cid_image_count,
            "invalidImageCount": self.invalid_image_count,
        }


def project_inert_microsoft_html(source: object) -> dict[str, object]:
    """Create bounded inert HTML; NEVER pass through to Graph API until audited."""
    if type(source) is not str or len(source) > MAX_INPUT_CHARACTERS:
        raise ValueError("microsoft_safe_html_invalid_input")
    p = _MicrosoftInertHtml()
    try:
        p.feed(source)
        p.close()
        return p.finish()
    except (AssertionError, OverflowError, RecursionError) as exc:
        raise ValueError("microsoft_safe_html_invalid_input") from exc
