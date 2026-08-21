"""Privacy-preserving, provider-neutral conversation text normalization."""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from ipaddress import IPv6Address

from .semantic_errors import SemanticInputError
from .semantic_types import SemanticTurn, SpeakerRole, TurnDirection


MAX_SEMANTIC_TURNS = 3
MAX_SEMANTIC_TOTAL_CHARS = 8_000
MAX_SEMANTIC_TURN_CHARS = 4_000
MAX_RAW_TURN_CHARS = 20_000
MAX_STRUCTURAL_INPUT_CHARS = 256_000
MIN_CONTEXT_CHARS_PER_TURN = 600
SEMANTIC_SECRET_MARKER = "<SECRET>"

_MAX_CREDENTIAL_KEY_CHARS = 96
_MAX_ENCODED_CREDENTIAL_KEY_CHARS = _MAX_CREDENTIAL_KEY_CHARS * 6
_MAX_CREDENTIAL_KEY_PARTS = 8
_MAX_CREDENTIAL_CONTINUATION_LINES = 64
_MAX_CREDENTIAL_CONTINUATION_CHARS = 64 * 1_024
_MAX_PRIVATE_KEY_BLOCKS = 8
_MAX_PRIVATE_KEY_BLOCK_CHARS = 64 * 1_024
_DEFAULT_IGNORABLE_RANGES = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)

_VOID_HTML_ELEMENTS = frozenset(
    {
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
)
_BLOCK_HTML_ELEMENTS = frozenset(
    {
        "address",
        "article",
        "aside",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
)
_UNSAFE_HTML_ELEMENTS = frozenset(
    {
        "applet",
        "canvas",
        "head",
        "iframe",
        "noscript",
        "object",
        "script",
        "style",
        "svg",
        "template",
        "title",
    }
)
_QUOTE_CLASS_TOKENS = frozenset(
    {
        "gmail_quote",
        "gmail_extra",
        "protonmail_quote",
        "yahoo_quoted",
    }
)
_SIGNATURE_CLASS_TOKENS = frozenset(
    {
        "email-signature",
        "gmail_signature",
        "moz-signature",
        "signature",
    }
)
_SIGNATURE_ID_TOKENS = frozenset(
    {
        "applemailsignature",
        "signature",
    }
)

# Redaction markers are deliberately not treated as HTML on a second pass.
_HTML_MARKUP_PATTERN = re.compile(r"<(?!URL>|SECRET>)[/!?A-Za-z][^>]*>")
_URL_PATTERN = re.compile(
    r"(?i)\b(?:[a-z][a-z0-9+.-]{1,31}://|www\.)[^\s<>\"']+"
)
_OPAQUE_URL_PATTERN = re.compile(r"(?i)\b(?:data|cid|mailto):[^\s<>\"']+")
_DOMAIN_URL_PATTERN = re.compile(
    r"(?iu)(?<![@\w])(?:[^\W_][\w-]*\.)+[^\W_][\w-]*"
    r"(?::\d{1,5})?(?:[/?#][^\s<>\"']*)?"
)
_IPV4_URL_PATTERN = re.compile(
    r"(?<![\w@])(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?"
    r"(?:[/?#][^\s<>\"']*)?"
)
_BRACKETED_IPV6_URL_PATTERN = re.compile(
    r"(?i)(?<![\w@])\[[0-9a-f:.]+\](?::\d{1,5})?"
    r"(?:[/?#][^\s<>\"']*)?"
)
_UNBRACKETED_IPV6_CANDIDATE_PATTERN = re.compile(
    r"(?i)(?<![A-Z0-9:.])(?P<candidate>[0-9A-F:.]{2,64})(?![A-Z0-9:.])"
)
_EMAIL_PATTERN = re.compile(
    r"(?i)(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}"
    r"(?![A-Z0-9_%+-]|\.[A-Z0-9])"
)
_ADDRESS_LIKE_PATTERN = re.compile(
    r"(?u)[^\s<>\"']{0,128}@[^\s<>\"']{1,255}"
)
_INLINE_AUTHORIZATION_SCHEME_PATTERN = re.compile(
    r"(?i)(?<![A-Z0-9])"
    r"(?P<key>(?:proxy(?:[-_. \t]*)authorization|authorization))"
    r"(?P<delimiter>[ \t]*:[ \t]*)"
    r"(?:basic|bearer|digest)\b[^\r\n]*"
)
_BEARER_PATTERN = re.compile(
    r"(?i)\b(?P<scheme>bearer)[ \t]+(?P<value>[^\s,;]+)"
)
_BASIC_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(?<![A-Z0-9])(?P<scheme>basic)[ \t]+"
    r"(?P<value>[A-Z0-9+/]{8,}={0,2})(?![A-Z0-9+/=])"
)
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_PEM_PRIVATE_KEY_BEGIN_PATTERN = re.compile(
    r"^[ \t]*-----BEGIN (?P<label>(?:(?:(?:ENCRYPTED|RSA|EC|DSA|OPENSSH) )?"
    r"PRIVATE KEY|PGP PRIVATE KEY BLOCK))-----[^\r\n]*$",
    re.IGNORECASE | re.MULTILINE,
)
_STANDALONE_CREDENTIAL_PATTERNS = (
    re.compile(
        r"(?<![A-Za-z0-9_-])sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,}"
        r"(?![A-Za-z0-9_-])"
    ),
    re.compile(
        r"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9_]{20,}|"
        r"github_pat_[A-Za-z0-9_]{20,})(?![A-Za-z0-9_])"
    ),
    re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])"),
    re.compile(r"(?<![A-Za-z0-9_-])ya29\.[A-Za-z0-9_-]{16,}(?![A-Za-z0-9_-])"),
    re.compile(r"(?<![A-Za-z0-9-])xox[baprs]-[A-Za-z0-9-]{10,}(?![A-Za-z0-9-])"),
    re.compile(
        r"(?<![A-Za-z0-9_])(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{12,}"
        r"(?![A-Za-z0-9_])"
    ),
    re.compile(r"(?<![A-Za-z0-9_-])AIza[A-Za-z0-9_-]{30,}(?![A-Za-z0-9_-])"),
    re.compile(
        r"(?<![A-Za-z0-9_.-])SG\.[A-Za-z0-9_-]{16,}\."
        r"[A-Za-z0-9_-]{16,}(?![A-Za-z0-9_.-])"
    ),
)
_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SIGNATURE_DELIMITER_PATTERN = re.compile(r"^\s*-- $")
_ORIGINAL_MESSAGE_SEPARATOR_PATTERN = re.compile(
    r"^\s*-{3,}\s*(?:original|forwarded)\s+message\s*-{3,}\s*$",
    re.IGNORECASE,
)
_OUTLOOK_SEPARATOR_PATTERN = re.compile(r"^\s*_{8,}\s*$")
_LOCALIZED_MESSAGE_SEPARATOR_PATTERN = re.compile(
    r"^\s*-{3,}\s*[^\r\n-]{1,80}\s*-{3,}\s*$"
)
_MAX_HISTORY_HEADER_SCAN_LINES = 12
_MAX_HISTORY_HEADER_CONTINUATION_LINES = 4
_MAX_HISTORY_HEADER_CONTINUATION_CHARS = 2_048
_CAMEL_ACRONYM_BOUNDARY_PATTERN = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_CAMEL_WORD_BOUNDARY_PATTERN = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_CREDENTIAL_KEY_SEPARATOR_PATTERN = re.compile(r"[._\-\s]+")
_ISO_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})$"
)


def _attribute_map(
    attributes: list[tuple[str, str | None]],
) -> dict[str, str] | None:
    # HTMLParser decodes character references in attribute values. Reapply the
    # same bounded Unicode privacy canonicalization used for authored text so
    # entities cannot split quote/signature/hidden markers.
    values: dict[str, str] = {}
    for name, value in attributes:
        decoded_value = value or ""
        if any(
            ord(character) < 32
            or ord(character) == 127
            or character == "\ufffd"
            for character in name + decoded_value
        ):
            # C0/DEL and replacement characters make structural attribute
            # meaning ambiguous and can otherwise split privacy markers.
            return None
        normalized_name = name.casefold()
        if normalized_name in values:
            # Duplicate attributes have parser/browser-dependent precedence.
            # Ignore their entire element rather than choosing a permissive one.
            return None
        values[normalized_name] = _canonicalize_privacy_unicode(decoded_value)
    return values


def _attribute_tokens(value: str) -> set[str]:
    return {token.lower() for token in re.split(r"\s+", value.strip()) if token}


class _SemanticHTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        # A matching-tag stack is intentionally conservative. Stray or
        # mismatched closing tags must never make quoted/hidden content visible.
        self._ignored_tags: list[str] = []

    def _is_ignored_root(
        self,
        tag: str,
        attributes: list[tuple[str, str | None]],
    ) -> bool:
        if tag in _UNSAFE_HTML_ELEMENTS or tag == "blockquote":
            return True

        values = _attribute_map(attributes)
        if values is None:
            return True
        class_tokens = _attribute_tokens(values.get("class", ""))
        if class_tokens.intersection(_QUOTE_CLASS_TOKENS | _SIGNATURE_CLASS_TOKENS):
            return True
        if "data-compose-quote" in values:
            return True
        if "data-email-quote" in values:
            return True
        if "data-compose-signature" in values:
            return True
        if "data-signature" in values:
            return True
        if values.get("type", "").lower() == "cite":
            return True
        if "hidden" in values or values.get("aria-hidden", "").lower() == "true":
            return True
        compact_style = re.sub(r"\s+", "", values.get("style", "").lower())
        if "display:none" in compact_style or "visibility:hidden" in compact_style:
            return True
        element_id = values.get("id", "").lower()
        if element_id in _SIGNATURE_ID_TOKENS:
            return True
        return element_id in {
            "appendonsend",
            "divrplyfwdmsg",
            "replyforwardmessage",
        }

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        normalized_tag = tag.lower()
        if self._ignored_tags:
            if normalized_tag not in _VOID_HTML_ELEMENTS:
                self._ignored_tags.append(normalized_tag)
            return
        if self._is_ignored_root(normalized_tag, attrs):
            if normalized_tag not in _VOID_HTML_ELEMENTS:
                self._ignored_tags.append(normalized_tag)
            return
        if normalized_tag == "br" or normalized_tag in _BLOCK_HTML_ELEMENTS:
            self._parts.append("\n")

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        normalized_tag = tag.lower()
        if normalized_tag not in _VOID_HTML_ELEMENTS:
            # In HTML, a trailing slash does not make ordinary elements void.
            # Route through normal start-tag handling so ignored containers stay
            # ignored until their matching close tag.
            self.handle_starttag(normalized_tag, attrs)
            return
        if not self._ignored_tags and not self._is_ignored_root(normalized_tag, attrs):
            if normalized_tag == "br":
                self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if self._ignored_tags:
            if normalized_tag == self._ignored_tags[-1]:
                self._ignored_tags.pop()
            return
        if normalized_tag in _BLOCK_HTML_ELEMENTS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_tags:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def html_to_semantic_text(value: str) -> str:
    parser = _SemanticHTMLTextExtractor()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        # Parser details are deliberately suppressed at this privacy boundary.
        raise SemanticInputError("Message HTML could not be normalized.") from None
    return parser.text()


def _has_bounded_message_header_block(lines: list[str], index: int) -> bool:
    header_names: set[str] = set()
    nonempty_header_names: set[str] = set()
    last_header_name: str | None = None
    continuation_lines = 0
    continuation_chars = 0
    for candidate in lines[
        index + 1 : index + 1 + _MAX_HISTORY_HEADER_SCAN_LINES
    ]:
        if not candidate.strip():
            if header_names:
                break
            continue
        if candidate[:1] in {" ", "\t"}:
            continuation_lines += 1
            continuation_chars += len(candidate)
            if (
                last_header_name is None
                or continuation_lines > _MAX_HISTORY_HEADER_CONTINUATION_LINES
                or continuation_chars > _MAX_HISTORY_HEADER_CONTINUATION_CHARS
            ):
                break
            nonempty_header_names.add(last_header_name)
            continue
        header_name, separator, header_value = candidate.partition(":")
        normalized_header_name = re.sub(
            r"\s+",
            " ",
            header_name.strip().casefold(),
        )
        if (
            not separator
            or not 1 <= len(normalized_header_name) <= 32
            or not any(character.isalpha() for character in normalized_header_name)
            or any(
                not (character.isalpha() or character in " -_")
                for character in normalized_header_name
            )
        ):
            break
        header_names.add(normalized_header_name)
        last_header_name = normalized_header_name
        if header_value.strip():
            nonempty_header_names.add(normalized_header_name)
        if len(header_names) >= 4 and len(nonempty_header_names) >= 2:
            return True
    return len(header_names) >= 4 and len(nonempty_header_names) >= 2


def _strip_plain_quoted_history(value: str) -> str:
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [line for line in lines if not line.lstrip().startswith(">")]

    for index, line in enumerate(lines):
        if _SIGNATURE_DELIMITER_PATTERN.match(line):
            lines = lines[:index]
            break
        if _ORIGINAL_MESSAGE_SEPARATOR_PATTERN.match(line):
            lines = lines[:index]
            break
        if (
            _OUTLOOK_SEPARATOR_PATTERN.match(line)
            or _LOCALIZED_MESSAGE_SEPARATOR_PATTERN.match(line)
        ) and _has_bounded_message_header_block(lines, index):
            lines = lines[:index]
            break

    return "\n".join(lines)


def _bounded_raw_text(value: str) -> str:
    if len(value) <= MAX_RAW_TURN_CHARS:
        return value
    half = (MAX_RAW_TURN_CHARS - 7) // 2
    return f"{value[:half]}\n[…]\n{value[-half:]}"


def _redact_domain_url(match: re.Match[str]) -> str:
    candidate = match.group(0)
    host = re.split(r"[/:?#]", candidate, maxsplit=1)[0]
    final_label = host.rsplit(".", 1)[-1]
    # Preserve decimal versions and dotted dates; a URL-like terminal label
    # must contain at least two alphabetic characters (Unicode included).
    if sum(character.isalpha() for character in final_label) < 2:
        return candidate
    return "<URL>"


def _redact_unbracketed_ipv6(match: re.Match[str]) -> str:
    candidate = match.group("candidate")
    if candidate.count(":") < 2:
        return candidate
    try:
        IPv6Address(candidate)
    except ValueError:
        return candidate
    return "<URL>"


def _redact_standalone_basic_credential(match: re.Match[str]) -> str:
    token = match.group("value")
    if len(token) > 8_192:
        raise SemanticInputError("Basic credential exceeded its privacy bound.")
    try:
        padded = token + ("=" * (-len(token) % 4))
        decoded = base64.b64decode(padded.encode("ascii"), validate=True)
        is_canonical = (
            base64.b64encode(decoded).decode("ascii").rstrip("=")
            == token.rstrip("=")
        )
    except (binascii.Error, UnicodeEncodeError, ValueError):
        return match.group(0)
    if not is_canonical or b":" not in decoded:
        return match.group(0)
    return f"{match.group('scheme')} {SEMANTIC_SECRET_MARKER}"


def _decode_credential_key_escapes(value: str) -> str | None:
    decoded: list[str] = []
    simple_escapes = {
        '"': '"',
        "'": "'",
        "/": "/",
        "\\": "\\",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }
    index = 0
    while index < len(value):
        if value[index] != "\\":
            decoded.append(value[index])
            index += 1
            continue
        if index + 1 >= len(value):
            return None
        escape = value[index + 1]
        if escape == "u":
            digits = value[index + 2 : index + 6]
            if len(digits) != 4 or any(
                digit not in "0123456789abcdefABCDEF" for digit in digits
            ):
                return None
            decoded.append(chr(int(digits, 16)))
            index += 6
            continue
        replacement = simple_escapes.get(escape)
        if replacement is None:
            return None
        decoded.append(replacement)
        index += 2
    return "".join(decoded)


def _canonical_credential_key(value: str) -> tuple[str, ...]:
    """Canonicalize only a fixed-size key suffix; never normalize its value."""

    key = value.strip(" \t")
    if len(key) >= 2 and key[0] in {'"', "'"} and key[-1] == key[0]:
        key = key[1:-1]
    elif key[:1] in {'"', "'"} or key[-1:] in {'"', "'"}:
        return ()

    # A very long or escaped identifier is not normalized wholesale. Its
    # bounded encoded suffix still catches terminals such as ...AccessToken.
    key = key[-_MAX_ENCODED_CREDENTIAL_KEY_CHARS:]
    decoded_key = _decode_credential_key_escapes(key)
    if decoded_key is None:
        return ()
    key = unicodedata.normalize("NFKC", decoded_key[-_MAX_CREDENTIAL_KEY_CHARS:])
    key = key[-_MAX_CREDENTIAL_KEY_CHARS:]
    key = _CAMEL_ACRONYM_BOUNDARY_PATTERN.sub(" ", key)
    key = _CAMEL_WORD_BOUNDARY_PATTERN.sub(" ", key)
    key = _CREDENTIAL_KEY_SEPARATOR_PATTERN.sub(" ", key).strip()
    if not key or any(not character.isalnum() and character != " " for character in key):
        return ()
    return tuple(part.casefold() for part in key.split())[-_MAX_CREDENTIAL_KEY_PARTS:]


def _is_sensitive_credential_key(value: str) -> bool:
    parts = _canonical_credential_key(value)
    if not parts:
        return False
    compact = "".join(parts)
    if any(
        compact.endswith(terminal)
        for terminal in {
            "authorization",
            "credential",
            "credentials",
            "passwd",
            "password",
            "secret",
            "token",
        }
    ):
        return True
    return any(
        compact.endswith(terminal)
        for terminal in {
            "accesskey",
            "apikey",
            "privatekey",
            "secretkey",
        }
    )


def _is_authorization_key(value: str) -> bool:
    compact = "".join(_canonical_credential_key(value))
    return compact in {
        "authorization",
        "proxyauthorization",
    }


def _redact_authorization_headers(value: str) -> str:
    """Redact complete RFC-style auth header values and every folded line."""

    lines = value.split("\n")
    redacted_lines: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        colon_index = line.find(":")
        if colon_index < 0 or not _is_authorization_key(line[:colon_index]):
            redacted_lines.append(line)
            index += 1
            continue

        continuation_end = index + 1
        while continuation_end < len(lines) and lines[continuation_end].startswith(
            (" ", "\t")
        ):
            continuation_end += 1

        current_value = line[colon_index + 1 :].strip(" \t")
        if current_value == SEMANTIC_SECRET_MARKER and continuation_end == index + 1:
            redacted_lines.append(line)
        else:
            redacted_lines.append(
                f"{line[: colon_index + 1]} {SEMANTIC_SECRET_MARKER}"
            )
        index = continuation_end
    return "\n".join(redacted_lines)


def _key_before_delimiter(
    value: str,
    delimiter_index: int,
) -> tuple[int, int, str, bool] | None:
    key_end = delimiter_index
    while key_end > 0 and value[key_end - 1] in " \t":
        key_end -= 1
    line_start = value.rfind("\n", 0, key_end) + 1
    if key_end <= line_start:
        return None

    final_character = value[key_end - 1]
    if final_character in {'"', "'"}:
        quote = final_character
        key_start = key_end - 2
        while key_start >= line_start:
            if value[key_start] == quote:
                backslashes = 0
                probe = key_start - 1
                while probe >= line_start and value[probe] == "\\":
                    backslashes += 1
                    probe -= 1
                if backslashes % 2 == 0:
                    return key_start, key_end, value[key_start:key_end], True
            key_start -= 1
        return None

    key_start = key_end
    while key_start > line_start:
        character = value[key_start - 1]
        if not (character.isalnum() or character in "._- \t"):
            break
        key_start -= 1
    raw_key = value[key_start:key_end]
    if not raw_key.strip(" \t"):
        return None
    return key_start, key_end, raw_key, False


def _colon_is_assignment_structure(
    value: str,
    key_start: int,
    *,
    quoted_key: bool,
) -> bool:
    if quoted_key:
        return True
    line_start = value.rfind("\n", 0, key_start) + 1
    prefix = value[line_start:key_start].rstrip(" \t")
    return not prefix or prefix[-1:] in {"{", "[", ","}


def _quoted_value_end(value: str, start: int, line_end: int) -> int:
    quote = value[start]
    index = start + 1
    while index < line_end:
        character = value[index]
        if character == "\\":
            index = min(index + 2, line_end)
            continue
        if character == quote:
            return index + 1
        index += 1
    # A recognized key with malformed quoting is redacted to the line end.
    return line_end


def _leading_horizontal_whitespace(value: str) -> int:
    index = 0
    while index < len(value) and value[index] in " \t":
        index += 1
    return index


def _credential_continuation_end(
    value: str,
    *,
    key_start: int,
    first_line_end: int,
) -> int:
    """Bound and consume an indented credential-value continuation.

    A recognized empty, block-scalar, or container value makes the following
    indented lines credential data. Equal-indent prose is never consumed. If a
    non-empty continuation is ambiguous or exceeds the fixed work bound, fail
    the whole turn closed rather than expose a partial value.
    """

    key_line_start = value.rfind("\n", 0, key_start) + 1
    key_indent = _leading_horizontal_whitespace(
        value[key_line_start:key_start]
    )
    cursor = first_line_end + 1
    continuation_end = first_line_end
    continuation_lines = 0
    continuation_chars = 0
    saw_value_line = False

    while cursor <= len(value):
        line_end = value.find("\n", cursor)
        if line_end < 0:
            line_end = len(value)
        line = value[cursor:line_end]
        stripped = line.strip(" \t")
        indent = _leading_horizontal_whitespace(line)
        is_indented_value = bool(stripped) and indent > key_indent
        is_blank_inside_value = not stripped and saw_value_line
        is_container_close = (
            saw_value_line
            and bool(stripped)
            and re.fullmatch(r"[\]\[}{(),;]+", stripped) is not None
        )

        if not (is_indented_value or is_blank_inside_value or is_container_close):
            if not saw_value_line and stripped:
                raise SemanticInputError(
                    "Credential continuation could not be normalized safely."
                )
            break

        continuation_lines += 1
        continuation_chars += line_end - cursor
        if (
            continuation_lines > _MAX_CREDENTIAL_CONTINUATION_LINES
            or continuation_chars > _MAX_CREDENTIAL_CONTINUATION_CHARS
        ):
            raise SemanticInputError(
                "Credential continuation exceeded its privacy bound."
            )
        continuation_end = line_end
        saw_value_line = saw_value_line or is_indented_value
        if line_end == len(value):
            break
        cursor = line_end + 1

    return continuation_end


def _next_line_is_indented_value(
    value: str,
    *,
    key_start: int,
    first_line_end: int,
) -> bool:
    if first_line_end >= len(value):
        return False
    key_line_start = value.rfind("\n", 0, key_start) + 1
    key_indent = _leading_horizontal_whitespace(
        value[key_line_start:key_start]
    )
    next_start = first_line_end + 1
    next_end = value.find("\n", next_start)
    if next_end < 0:
        next_end = len(value)
    next_line = value[next_start:next_end]
    return (
        bool(next_line.strip(" \t"))
        and _leading_horizontal_whitespace(next_line) > key_indent
    )


def _credential_value_span(
    value: str,
    delimiter_index: int,
    *,
    colon_structure: bool,
    key_start: int,
    query_structure: bool,
) -> tuple[int, int] | None:
    start = delimiter_index + 1
    while start < len(value) and value[start] in " \t":
        start += 1
    line_end = value.find("\n", start)
    if line_end < 0:
        line_end = len(value)
    if start >= line_end:
        if line_end == len(value):
            return None
        continuation_end = _credential_continuation_end(
            value,
            key_start=key_start,
            first_line_end=line_end,
        )
        return (
            (start, continuation_end)
            if continuation_end > start
            else None
        )

    marker_end = start + len(SEMANTIC_SECRET_MARKER)
    marker_is_exact = False
    if value.startswith(SEMANTIC_SECRET_MARKER, start):
        marker_tail = marker_end
        while marker_tail < line_end and value[marker_tail] in " \t":
            marker_tail += 1
        if colon_structure:
            marker_is_exact = marker_tail == line_end or value[marker_tail] in ",}]"
        elif query_structure:
            marker_is_exact = (
                marker_end == line_end
                or value[marker_end].isspace()
                or value[marker_end] in "&#"
            )
        else:
            marker_is_exact = (
                marker_tail == line_end
                or value[marker_tail] == "&"
            )

    if marker_is_exact:
        end = marker_end
    elif value[start] in {'"', "'"}:
        end = _quoted_value_end(value, start, line_end)
    elif colon_structure or value[start] in "{[":
        # YAML/header-like colon values may contain spaces. Redact the whole
        # bounded line rather than guessing a prefix and leaking its tail.
        end = line_end
    elif query_structure:
        end = start
        while (
            end < line_end
            and not value[end].isspace()
            and value[end] not in "&#"
        ):
            end += 1
    else:
        # Unquoted credential assignments can be passphrases or token lists.
        # Redact the bounded line, stopping only at a query-style separator.
        query_separator = value.find("&", start, line_end)
        end = line_end if query_separator < 0 else query_separator
    first_line_value = value[start:line_end].strip(" \t")
    needs_continuation = (
        line_end < len(value)
        and (
            first_line_value[:1] in {"|", ">"}
            or first_line_value[:1] in {"{", "["}
            or first_line_value.endswith("\\")
            or _next_line_is_indented_value(
                value,
                key_start=key_start,
                first_line_end=line_end,
            )
        )
    )
    if needs_continuation:
        continuation_end = _credential_continuation_end(
            value,
            key_start=key_start,
            first_line_end=line_end,
        )
        end = max(end, continuation_end)
    return (start, end) if end > start else None


def _redact_credential_assignments(value: str) -> str:
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(value):
        if value[index] not in ":=":
            index += 1
            continue

        key = _key_before_delimiter(value, index)
        if key is None:
            index += 1
            continue
        key_start, _key_end, raw_key, quoted_key = key
        if not _is_sensitive_credential_key(raw_key):
            index += 1
            continue

        colon_structure = value[index] == ":" and _colon_is_assignment_structure(
            value,
            key_start,
            quoted_key=quoted_key,
        )
        if value[index] == ":" and not colon_structure:
            index += 1
            continue
        line_start = value.rfind("\n", 0, key_start) + 1
        query_structure = (
            value[index] == "="
            and key_start > line_start
            and value[key_start - 1] in "?&"
        )

        span = _credential_value_span(
            value,
            index,
            colon_structure=colon_structure,
            key_start=key_start,
            query_structure=query_structure,
        )
        if span is None:
            index += 1
            continue
        start, end = span
        if value[start:end] == SEMANTIC_SECRET_MARKER:
            index = end
            continue
        spans.append(span)
        index = end

    if not spans:
        return value
    parts: list[str] = []
    cursor = 0
    for start, end in spans:
        if start < cursor:
            continue
        parts.append(value[cursor:start])
        parts.append(SEMANTIC_SECRET_MARKER)
        cursor = end
    parts.append(value[cursor:])
    return "".join(parts)


def _redact_private_key_blocks(value: str) -> str:
    spans: list[tuple[int, int]] = []
    cursor = 0
    while True:
        begin = _PEM_PRIVATE_KEY_BEGIN_PATTERN.search(value, cursor)
        if begin is None:
            break
        if len(spans) >= _MAX_PRIVATE_KEY_BLOCKS:
            raise SemanticInputError("Private key material exceeded its privacy bound.")
        label = begin.group("label")
        end_pattern = re.compile(
            rf"^[ \t]*-----END {re.escape(label)}-----[^\r\n]*$",
            re.IGNORECASE | re.MULTILINE,
        )
        search_end = min(
            len(value),
            begin.start() + _MAX_PRIVATE_KEY_BLOCK_CHARS + 1,
        )
        end = end_pattern.search(value, begin.end(), search_end)
        if (
            end is None
            or end.end() - begin.start() > _MAX_PRIVATE_KEY_BLOCK_CHARS
        ):
            raise SemanticInputError("Private key material could not be normalized safely.")
        spans.append((begin.start(), end.end()))
        cursor = end.end()

    if not spans:
        return value
    parts: list[str] = []
    cursor = 0
    for start, end in spans:
        parts.append(value[cursor:start])
        parts.append(SEMANTIC_SECRET_MARKER)
        cursor = end
    parts.append(value[cursor:])
    return "".join(parts)


def _redact_standalone_credentials(value: str) -> str:
    redacted = value
    for pattern in _STANDALONE_CREDENTIAL_PATTERNS:
        redacted = pattern.sub(SEMANTIC_SECRET_MARKER, redacted)
    return redacted


def _redact_credential_content(value: str) -> str:
    redacted = _redact_private_key_blocks(value)
    redacted = _redact_authorization_headers(redacted)
    redacted = _INLINE_AUTHORIZATION_SCHEME_PATTERN.sub(
        lambda match: (
            f"{match.group('key')}{match.group('delimiter')}"
            f"{SEMANTIC_SECRET_MARKER}"
        ),
        redacted,
    )
    redacted = _redact_credential_assignments(redacted)
    redacted = _BEARER_PATTERN.sub(
        lambda match: f"{match.group('scheme')} {SEMANTIC_SECRET_MARKER}",
        redacted,
    )
    redacted = _BASIC_CREDENTIAL_PATTERN.sub(
        _redact_standalone_basic_credential,
        redacted,
    )
    redacted = _JWT_PATTERN.sub(SEMANTIC_SECRET_MARKER, redacted)
    return _redact_standalone_credentials(redacted)


def _redact_nonsemantic_content(value: str) -> str:
    redacted = _redact_credential_content(value)
    redacted = _EMAIL_PATTERN.sub("[email]", redacted)
    redacted = _ADDRESS_LIKE_PATTERN.sub("[email]", redacted)
    redacted = _BRACKETED_IPV6_URL_PATTERN.sub("<URL>", redacted)
    redacted = _UNBRACKETED_IPV6_CANDIDATE_PATTERN.sub(
        _redact_unbracketed_ipv6,
        redacted,
    )
    redacted = _URL_PATTERN.sub("<URL>", redacted)
    redacted = _OPAQUE_URL_PATTERN.sub("<URL>", redacted)
    redacted = _DOMAIN_URL_PATTERN.sub(_redact_domain_url, redacted)
    redacted = _IPV4_URL_PATTERN.sub("<URL>", redacted)
    return redacted


def _normalize_whitespace(value: str) -> str:
    value = _CONTROL_PATTERN.sub("", value)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.split("\n")]
    normalized: list[str] = []
    previous_blank = True
    for line in lines:
        is_blank = not line
        if is_blank and previous_blank:
            continue
        normalized.append(line)
        previous_blank = is_blank
    while normalized and not normalized[-1]:
        normalized.pop()
    return "\n".join(normalized).strip()


def _is_default_ignorable(character: str) -> bool:
    code_point = ord(character)
    return unicodedata.category(character) == "Cf" or any(
        start <= code_point <= end
        for start, end in _DEFAULT_IGNORABLE_RANGES
    )


def _canonicalize_privacy_unicode(value: str) -> str:
    if len(value) > MAX_STRUCTURAL_INPUT_CHARS:
        raise SemanticInputError("Turn text exceeded its structural privacy bound.")
    normalized = unicodedata.normalize("NFKC", value)
    if len(normalized) > MAX_STRUCTURAL_INPUT_CHARS:
        raise SemanticInputError("Turn text exceeded its structural privacy bound.")
    # Unicode Default_Ignorable controls (including variation selectors and
    # combining grapheme joiners outside category Cf), replacement characters,
    # and non-whitespace C0/DEL controls must not split privacy markers,
    # credential keys, or provider-token prefixes.
    return "".join(
        character
        for character in normalized
        if not _is_default_ignorable(character)
        and character != "\ufffd"
        and not (
            (ord(character) < 32 and character not in "\t\n\r")
            or ord(character) == 127
        )
    )


def normalize_semantic_turn_text(value: str) -> str:
    """Return bounded-purpose text without nested history or common secrets."""

    if type(value) is not str:
        raise SemanticInputError("Turn text must be a string.")
    normalized = _canonicalize_privacy_unicode(value)
    if _HTML_MARKUP_PATTERN.search(normalized):
        normalized = html_to_semantic_text(normalized)
        # Entity decoding happens inside HTMLParser and can reintroduce Unicode
        # controls or compatibility characters into text nodes. Canonicalize
        # again before any privacy-sensitive matching.
        normalized = _canonicalize_privacy_unicode(normalized)
    normalized = _strip_plain_quoted_history(normalized)
    normalized = _redact_nonsemantic_content(normalized)
    normalized = _normalize_whitespace(normalized)
    # Remove structure and redact sensitive tokens before head/tail bounding so
    # a cut cannot detach private text from its quote container or split a token.
    return _bounded_raw_text(normalized)


def assert_semantic_model_turns_safe(turns: object) -> None:
    """Fail closed if exact provider-bound turns bypassed normalization."""

    error = "Semantic model input failed privacy validation."
    if type(turns) is not list or not 1 <= len(turns) <= MAX_SEMANTIC_TURNS:
        raise SemanticInputError(error)

    required_fields = {"sequence", "speaker", "direction", "text"}
    allowed_fields = required_fields | {"timestamp"}
    total_chars = 0
    for sequence, turn in enumerate(turns, start=1):
        if type(turn) is not dict or not required_fields.issubset(turn):
            raise SemanticInputError(error)
        if not set(turn).issubset(allowed_fields) or turn.get("sequence") != sequence:
            raise SemanticInputError(error)
        if turn.get("speaker") not in {"USER", "EXTERNAL"}:
            raise SemanticInputError(error)
        if turn.get("direction") not in {"INCOMING", "OUTGOING"}:
            raise SemanticInputError(error)
        timestamp = turn.get("timestamp")
        if timestamp is not None and (
            type(timestamp) is not str
            or _safe_timestamp(timestamp) != timestamp
        ):
            raise SemanticInputError(error)

        text = turn.get("text")
        if type(text) is not str or not text or len(text) > MAX_SEMANTIC_TURN_CHARS:
            raise SemanticInputError(error)
        total_chars += len(text)
        # This is an assertion, not a second mutating normalization pass. Any
        # difference proves that unsafe/raw content reached the final boundary.
        if normalize_semantic_turn_text(text) != text:
            raise SemanticInputError(error)

    if total_chars > MAX_SEMANTIC_TOTAL_CHARS:
        raise SemanticInputError(error)


def _truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    if limit <= 8:
        return value[:limit]
    separator = "\n[…]\n"
    available = limit - len(separator)
    head_length = (available * 3) // 5
    tail_length = available - head_length
    return f"{value[:head_length]}{separator}{value[-tail_length:]}"


def _safe_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    return normalized if _ISO_TIMESTAMP_PATTERN.fullmatch(normalized) else None


@dataclass(frozen=True, slots=True)
class SemanticTextTurn:
    turn_id: str
    speaker: SpeakerRole
    direction: TurnDirection
    text: str
    timestamp: str | None

    def to_model_dict(self, sequence: int) -> dict[str, object]:
        # turn_id is intentionally excluded from provider input.
        result: dict[str, object] = {
            "sequence": sequence,
            "speaker": self.speaker.value,
            "direction": self.direction.value,
            "text": self.text,
        }
        if self.timestamp:
            result["timestamp"] = self.timestamp
        return result


@dataclass(frozen=True, slots=True)
class SemanticTextWindow:
    turns: tuple[SemanticTextTurn, ...]
    latest_turn_id: str
    total_chars: int

    def to_model_turns(self) -> list[dict[str, object]]:
        return [turn.to_model_dict(index + 1) for index, turn in enumerate(self.turns)]


def _allocate_turn_budgets(texts: list[str], total_limit: int) -> list[int]:
    budgets = [min(len(text), MIN_CONTEXT_CHARS_PER_TURN) for text in texts]
    remaining = total_limit - sum(budgets)
    for index in range(len(texts) - 1, -1, -1):
        if remaining <= 0:
            break
        maximum = min(len(texts[index]), MAX_SEMANTIC_TURN_CHARS)
        extra = min(maximum - budgets[index], remaining)
        budgets[index] += extra
        remaining -= extra
    return budgets


def build_semantic_text_window(
    turns: tuple[SemanticTurn, ...],
) -> SemanticTextWindow:
    """Build the latest three meaningful, chronological, privacy-bounded turns."""

    candidates_reversed: list[tuple[SemanticTurn, str]] = []
    for turn in reversed(turns):
        normalized_text = normalize_semantic_turn_text(turn.text)
        if not normalized_text:
            continue
        candidates_reversed.append((turn, normalized_text))
        if len(candidates_reversed) == MAX_SEMANTIC_TURNS:
            break

    if not candidates_reversed:
        raise SemanticInputError("No meaningful conversation text remains after normalization.")

    candidates = list(reversed(candidates_reversed))
    texts = [text for _, text in candidates]
    budgets = _allocate_turn_budgets(texts, MAX_SEMANTIC_TOTAL_CHARS)
    normalized_turns = tuple(
        SemanticTextTurn(
            turn_id=turn.turn_id,
            speaker=turn.speaker,
            direction=turn.direction,
            text=_truncate_text(text, budget),
            timestamp=_safe_timestamp(turn.timestamp),
        )
        for (turn, text), budget in zip(candidates, budgets)
        if budget > 0
    )
    total_chars = sum(len(turn.text) for turn in normalized_turns)
    if total_chars > MAX_SEMANTIC_TOTAL_CHARS:
        raise SemanticInputError("Semantic text window exceeded its privacy bound.")

    return SemanticTextWindow(
        turns=normalized_turns,
        latest_turn_id=normalized_turns[-1].turn_id,
        total_chars=total_chars,
    )
