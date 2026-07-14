from __future__ import annotations

if __name__ != "api.collaboration.source_message":
    raise ImportError(
        "Collaboration helpers must be imported as "
        "api.collaboration.source_message"
    )

import base64
import importlib
import re
import unicodedata
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from urllib.parse import quote

from api.inboxes.imap_uid_validity import read_selected_mailbox_uid_validity

from .authorization import _is_internal_capability, resolve_internal_collaboration_context
from .models import MAX_V2_SOURCE_BODY


MAX_SOURCE_MESSAGE_BYTES = 2_097_152
MAX_PROVIDER_MESSAGE_BYTES = MAX_SOURCE_MESSAGE_BYTES
MAX_PROVIDER_IDENTIFIER = 512

_CANONICAL_GMAIL_AUTH_MODULE = "api.inboxes.authenticated_gmail"
_CANONICAL_GMAIL_FETCH_MODULE = "api.inboxes.fetch-gmail"
_GMAIL_MODULE_IDENTITIES = (
    ("authenticated_gmail", _CANONICAL_GMAIL_AUTH_MODULE),
    ("fetch-gmail", _CANONICAL_GMAIL_FETCH_MODULE),
)
_FORBIDDEN_GMAIL_MODULE_NAMES = ("cuevion_collaboration_fetch_gmail",)
_CANONICAL_GMAIL_BINDINGS = (
    "error_payload",
    "gmail_http_error_code",
    "read_bounded_response",
    "read_json_body",
    "refresh_gmail_context",
    "reject_unknown_fields",
    "resolve_authenticated_gmail",
    "send_json",
    "send_method_not_allowed",
    "validate_focus_preferences",
    "valid_identifier",
)
_CANONICAL_IMAP_AUTH_MODULE = "api.inboxes.authenticated_imap"
_CANONICAL_IMAP_PREVIEW_MODULE = "imap_connect_preview"
_IMAP_MODULE_IDENTITIES = (
    ("authenticated_imap", _CANONICAL_IMAP_AUTH_MODULE),
)

_MIME_BODY = "body"
_MIME_ATTACHMENT = "attachment"
_MIME_SUSPICIOUS = "suspicious"


def _failure(status: str, code: str) -> dict:
    return {"status": status, "source": None, "error": {"code": code}}


def _load_fetch_gmail_module():
    """Load provider helpers only through their canonical package identities."""
    import sys

    def has_conflicting_identity() -> bool:
        return any(name in sys.modules for name in _FORBIDDEN_GMAIL_MODULE_NAMES) or any(
            alias in sys.modules
            and sys.modules[alias] is not sys.modules.get(canonical)
            for alias, canonical in _GMAIL_MODULE_IDENTITIES
        )

    if has_conflicting_identity():
        raise RuntimeError("non-canonical gmail provider module already loaded")
    canonical_auth = importlib.import_module(_CANONICAL_GMAIL_AUTH_MODULE)
    fetch_module = importlib.import_module(_CANONICAL_GMAIL_FETCH_MODULE)
    if has_conflicting_identity() or any(
        sys.modules.get(alias) is not sys.modules.get(canonical)
        for alias, canonical in _GMAIL_MODULE_IDENTITIES
    ):
        raise RuntimeError("non-canonical gmail provider module retained")
    if getattr(fetch_module, "__name__", None) != _CANONICAL_GMAIL_FETCH_MODULE:
        raise RuntimeError("gmail helper module identity mismatch")
    for binding in _CANONICAL_GMAIL_BINDINGS:
        if getattr(fetch_module, binding, None) is not getattr(
            canonical_auth, binding, None
        ):
            raise RuntimeError("gmail helper retained non-canonical provider binding")
    return fetch_module


def _default_google_fetcher(headers, mailbox_id: str, source_ref: dict) -> dict:
    # Imports and network-capable calls occur only when this provider boundary is
    # invoked.  Merely importing the inactive v2 module performs no provider I/O.
    try:
        fetch_module = _load_fetch_gmail_module()
        resolve_authenticated_gmail = importlib.import_module(
            _CANONICAL_GMAIL_AUTH_MODULE
        ).resolve_authenticated_gmail
    except Exception:
        return _failure("unavailable", "provider_unavailable")

    authenticated = resolve_authenticated_gmail(headers, mailbox_id)
    if authenticated.get("status") != "ok" or not authenticated.get("context"):
        status = authenticated.get("status")
        if status in {"not_found"}:
            return _failure("not_found", "source_not_found")
        return _failure("unavailable", "provider_unavailable")
    try:
        payload, error, _context, refresh_error = fetch_module._request_with_one_refresh(
            authenticated["context"],
            f"/messages/{quote(source_ref['providerMessageId'], safe='')}?format=raw",
        )
    except Exception:
        return _failure("unavailable", "provider_unavailable")
    if error or refresh_error:
        code = (error or {}).get("code") if isinstance(error, dict) else None
        if code in {"gmail_message_not_found", "gmail_not_found"}:
            return _failure("not_found", "source_not_found")
        return _failure("unavailable", "provider_unavailable")
    allowed_fields = {"id", "threadId", "labelIds", "snippet", "historyId", "internalDate", "sizeEstimate", "raw"}
    if not isinstance(payload, dict) or not set(payload) <= allowed_fields:
        return _failure("unavailable", "provider_unavailable")
    encoded = payload.get("raw")
    if not isinstance(encoded, str) or len(encoded) > MAX_PROVIDER_MESSAGE_BYTES * 2:
        return _failure("unavailable", "provider_unavailable")
    try:
        padding = "=" * (-len(encoded) % 4)
        raw_message = base64.b64decode(
            f"{encoded}{padding}".encode("ascii"), altchars=b"-_", validate=True
        )
    except (ValueError, UnicodeEncodeError):
        return _failure("unavailable", "provider_unavailable")
    if len(raw_message) > MAX_SOURCE_MESSAGE_BYTES:
        return _failure("unavailable", "provider_unavailable")
    return {"status": "ok", "rawMessage": raw_message}


def _imap_uid_validity(mailbox) -> str | None:
    return read_selected_mailbox_uid_validity(mailbox)


def _default_imap_fetcher(headers, mailbox_id: str, source_ref: dict) -> dict:
    import sys

    def has_conflicting_legacy_identity() -> bool:
        return any(
            legacy in sys.modules
            and sys.modules[legacy] is not sys.modules.get(canonical)
            for legacy, canonical in _IMAP_MODULE_IDENTITIES
        )

    try:
        if has_conflicting_legacy_identity():
            raise RuntimeError("non-canonical IMAP provider module already loaded")
        authenticated_module = importlib.import_module(_CANONICAL_IMAP_AUTH_MODULE)
        preview_module = importlib.import_module(_CANONICAL_IMAP_PREVIEW_MODULE)
        if has_conflicting_legacy_identity() or any(
            sys.modules.get(legacy) is not sys.modules.get(canonical)
            for legacy, canonical in _IMAP_MODULE_IDENTITIES
        ):
            raise RuntimeError("non-canonical IMAP provider module retained")
        if (
            getattr(authenticated_module, "__name__", None)
            != _CANONICAL_IMAP_AUTH_MODULE
            or getattr(preview_module, "__name__", None)
            != _CANONICAL_IMAP_PREVIEW_MODULE
        ):
            raise RuntimeError("IMAP helper module identity mismatch")
    except Exception:
        return _failure("unavailable", "provider_unavailable")
    resolve_authenticated_imap_mailbox = authenticated_module.resolve_authenticated_imap_mailbox
    connect_mailbox_with_settings = preview_module.connect_mailbox_with_settings

    authenticated = resolve_authenticated_imap_mailbox(headers, mailbox_id)
    if authenticated.get("status") != "ok" or not authenticated.get("mailbox"):
        if authenticated.get("status") == "not_found":
            return _failure("not_found", "source_not_found")
        return _failure("unavailable", "provider_unavailable")
    settings = authenticated["mailbox"].get("imap")
    if not isinstance(settings, dict):
        return _failure("unavailable", "provider_unavailable")
    mailbox = None
    try:
        mailbox = connect_mailbox_with_settings(
            settings["host"], settings["port"], settings["username"],
            settings["password"], settings["ssl"],
        )
        selected, _ = mailbox.select("INBOX", readonly=True)
        if selected != "OK":
            return _failure("not_found", "source_not_found")
        uid_validity = _imap_uid_validity(mailbox)
        if uid_validity is None:
            return _failure("unavailable", "provider_unavailable")
        if uid_validity != source_ref["uidValidity"]:
            return _failure("conflict", "source_changed")
        status, rows = mailbox.uid(
            "fetch",
            source_ref["imapUid"],
            f"(UID BODY.PEEK[]<0.{MAX_SOURCE_MESSAGE_BYTES + 1}>)",
        )
        if status != "OK" or not isinstance(rows, list):
            return _failure("not_found", "source_not_found")
        raw_message = next(
            (
                row[1]
                for row in rows
                if isinstance(row, tuple)
                and len(row) > 1
                and isinstance(row[1], bytes)
            ),
            None,
        )
        if raw_message is None or len(raw_message) > MAX_SOURCE_MESSAGE_BYTES:
            return _failure("not_found", "source_not_found")
        return {"status": "ok", "rawMessage": raw_message, "uidValidity": uid_validity}
    except Exception:
        return _failure("unavailable", "provider_unavailable")
    finally:
        if mailbox is not None:
            try:
                mailbox.logout()
            except Exception:
                pass


def _raw_mime_headers(part, header_name: str) -> list[tuple[str, object]] | None:
    """Return raw/parsed MIME headers only when their bytes are strict ASCII."""
    try:
        raw_values = [
            raw_value
            for raw_name, raw_value in part.raw_items()
            if isinstance(raw_name, str) and raw_name.casefold() == header_name.casefold()
        ]
        parsed_values = part.get_all(header_name, [])
    except Exception:
        return None
    if not isinstance(parsed_values, list) or len(raw_values) != len(parsed_values):
        return None
    result: list[tuple[str, object]] = []
    for raw_value, parsed_value in zip(raw_values, parsed_values):
        if not isinstance(raw_value, str):
            return None
        try:
            raw_value.encode("ascii", errors="strict")
        except UnicodeEncodeError:
            return None
        # The parser exposes undecodable bytes through surrogate escapes and may
        # render them as U+FFFD on parsed access.  Neither representation is safe
        # to normalize into attachment metadata.
        if "\ufffd" in raw_value or any(
            0xD800 <= ord(character) <= 0xDFFF for character in raw_value
        ):
            return None
        result.append((raw_value, parsed_value))
    return result


def _mime_parameter_present(raw_value: str, parsed_header: object, name: str) -> bool:
    try:
        parameters = parsed_header.params
        if not hasattr(parameters, "keys"):
            return True
        for parameter_name in parameters.keys():
            if not isinstance(parameter_name, str):
                return True
            lowered = parameter_name.casefold()
            if lowered == name or lowered.startswith(f"{name}*"):
                return True
    except Exception:
        return True
    # Inspect the raw form too: malformed/empty parameters can disappear from
    # the parsed mapping, but their presence still attachment-classifies the part.
    return re.search(
        rf"(?:^|;)[\t\r\n ]*{re.escape(name)}(?:\*[0-9]+)?\*?"
        rf"[\t\r\n ]*(?:=|;|$)",
        raw_value,
        flags=re.IGNORECASE,
    ) is not None


def _raw_mime_parameter_syntax_is_defective(raw_value: str) -> bool:
    """Reject raw parameter separators that the email parser silently discards."""
    in_quoted_string = False
    comment_depth = 0
    escaped = False
    after_separator = False
    parameter_has_syntax = False

    for character in raw_value:
        if escaped:
            escaped = False
            continue
        if in_quoted_string:
            if character == "\\":
                escaped = True
            elif character == '"':
                in_quoted_string = False
            continue
        if comment_depth:
            if character == "\\":
                escaped = True
            elif character == "(":
                comment_depth += 1
            elif character == ")":
                comment_depth -= 1
            continue
        if character == "(":
            comment_depth = 1
            continue
        if character == '"':
            in_quoted_string = True
            if after_separator:
                parameter_has_syntax = True
            continue
        if character == ";":
            if after_separator and not parameter_has_syntax:
                return True
            after_separator = True
            parameter_has_syntax = False
            continue
        if after_separator and character not in " \t\r\n":
            parameter_has_syntax = True

    return (
        in_quoted_string
        or comment_depth != 0
        or escaped
        or (after_separator and not parameter_has_syntax)
    )


def _mime_header_is_defective(parsed_header: object) -> bool:
    try:
        defects = parsed_header.defects
        parsed_value = str(parsed_header)
    except Exception:
        return True
    return (
        not isinstance(defects, tuple)
        or bool(defects)
        or "\ufffd" in parsed_value
        or any(0xD800 <= ord(character) <= 0xDFFF for character in parsed_value)
    )


def _classify_mime_part(part) -> str:
    """Classify one MIME node without reading or descending into its payload."""
    try:
        if part.defects:
            return _MIME_SUSPICIOUS
    except Exception:
        return _MIME_SUSPICIOUS

    disposition_headers = _raw_mime_headers(part, "Content-Disposition")
    type_headers = _raw_mime_headers(part, "Content-Type")
    if disposition_headers is None or type_headers is None:
        return _MIME_SUSPICIOUS
    if len(disposition_headers) > 1 or len(type_headers) > 1:
        return _MIME_SUSPICIOUS

    attachment_classified = False

    if disposition_headers:
        raw_disposition, parsed_disposition = disposition_headers[0]
        if _mime_header_is_defective(
            parsed_disposition
        ) or _raw_mime_parameter_syntax_is_defective(raw_disposition):
            return _MIME_SUSPICIOUS
        try:
            disposition = part.get_content_disposition()
        except Exception:
            return _MIME_SUSPICIOUS
        # A present disposition must be an unambiguous standard body disposition.
        # Unknown/empty dispositions are treated as suspicious rather than body.
        if disposition not in {"inline", "attachment"}:
            return _MIME_SUSPICIOUS
        if disposition == "attachment" or _mime_parameter_present(
            raw_disposition, parsed_disposition, "filename"
        ):
            attachment_classified = True

    if type_headers:
        raw_content_type, parsed_content_type = type_headers[0]
        if _mime_header_is_defective(
            parsed_content_type
        ) or _raw_mime_parameter_syntax_is_defective(raw_content_type):
            return _MIME_SUSPICIOUS
        if _mime_parameter_present(
            raw_content_type, parsed_content_type, "name"
        ) or _mime_parameter_present(
            raw_content_type, parsed_content_type, "filename"
        ):
            attachment_classified = True

    try:
        # get_filename also covers RFC 2231 continuations and Content-Type name;
        # testing against None preserves the significance of empty values.
        if part.get_filename() is not None:
            attachment_classified = True
        content_type = part.get_content_type()
    except Exception:
        return _MIME_SUSPICIOUS
    if content_type == "message/rfc822" or content_type.startswith("message/"):
        attachment_classified = True
    return _MIME_ATTACHMENT if attachment_classified else _MIME_BODY


def _mime_part_is_attachment(part) -> bool:
    """Treat both attached and suspicious nodes as non-body subtrees."""
    return _classify_mime_part(part) != _MIME_BODY


def _mime_tree_is_suspicious(part, *, attachment_ancestor: bool = False) -> bool:
    """Preflight safe MIME structure without entering attachment-classified nodes."""
    classification = _classify_mime_part(part)
    if classification == _MIME_SUSPICIOUS:
        return True
    if attachment_ancestor or classification == _MIME_ATTACHMENT:
        return False
    try:
        is_multipart = part.is_multipart()
        content_type = part.get_content_type()
    except Exception:
        return True
    if not is_multipart:
        return content_type.startswith("multipart/")
    if not content_type.startswith("multipart/"):
        return True
    try:
        children = part.get_payload()
    except Exception:
        return True
    if not isinstance(children, list):
        return True
    return any(
        _mime_tree_is_suspicious(child, attachment_ancestor=False)
        for child in children
    )


def _plain_text_body(message) -> str | None:
    chunks: list[str] = []
    total = 0

    # Structural defects can make a parser reparent attachment descendants as
    # apparently clean siblings.  If preflight finds any ambiguous node, omit
    # body extraction for the message rather than trust that lossy parse tree.
    if _mime_tree_is_suspicious(message):
        return ""

    def visit(part, *, attachment_ancestor: bool = False) -> bool:
        nonlocal total
        # Inspect the current node before accessing its payload.  An unsafe
        # ancestor or node terminates traversal of the complete subtree.
        subtree_is_attachment = attachment_ancestor or _mime_part_is_attachment(part)
        if subtree_is_attachment:
            return True
        try:
            is_multipart = part.is_multipart()
            content_type = part.get_content_type()
        except Exception:
            return True
        if is_multipart:
            try:
                children = part.get_payload()
            except Exception:
                return True
            if not isinstance(children, list):
                return True
            for child in children:
                if not visit(child, attachment_ancestor=subtree_is_attachment):
                    return False
            return True
        if content_type != "text/plain":
            return True
        try:
            payload = part.get_payload(decode=True)
            charset = part.get_content_charset() or "utf-8"
        except Exception:
            return True
        try:
            if part.defects:
                return True
        except Exception:
            return True
        if payload is None:
            return True
        if not isinstance(payload, bytes):
            return True
        total += len(payload)
        if total > MAX_V2_SOURCE_BODY * 4:
            return False
        try:
            decoded = payload.decode(charset, errors="strict")
        except (LookupError, UnicodeDecodeError):
            return True
        chunks.append(decoded)
        return True

    if not visit(message):
        return None
    body = "\n\n".join(chunks).replace("\r\n", "\n").replace("\r", "\n").strip()
    body = "".join(" " if ord(character) < 32 or ord(character) == 127 else character for character in body)
    return body if len(body.encode("utf-8")) <= MAX_V2_SOURCE_BODY else None


def _snapshot_from_raw(raw_message: object) -> dict | None:
    if not isinstance(raw_message, bytes) or len(raw_message) > MAX_PROVIDER_MESSAGE_BYTES:
        return None
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw_message)
        from_display = str(message.get("From") or "").strip()
        sender_name, sender_address = parseaddr(from_display)
        body_text = _plain_text_body(message)
        values = {
            "subject": str(message.get("Subject") or "").strip(),
            "senderDisplay": (sender_name or sender_address).strip(),
            "fromDisplay": from_display,
            "timestamp": str(message.get("Date") or "").strip(),
            "bodyText": body_text,
        }
    except Exception:
        return None
    if body_text is None:
        return None
    limits = {
        "subject": 998, "senderDisplay": 512, "fromDisplay": 512,
        "timestamp": 128, "bodyText": MAX_V2_SOURCE_BODY,
    }
    for key, limit in limits.items():
        try:
            encoded_length = len(values[key].encode("utf-8", errors="strict"))
        except UnicodeEncodeError:
            return None
        if encoded_length > limit:
            return None
        if key != "bodyText" and "\ufffd" in values[key]:
            return None
        for character in values[key]:
            category = unicodedata.category(character)
            allowed_text_control = key == "bodyText" and character in {"\n", "\r", "\t"}
            if category in {"Cf", "Cs"} or (category == "Cc" and not allowed_text_control):
                return None
    return values


def _positive_decimal_identifier(value: object) -> str | None:
    if isinstance(value, str) and 1 <= len(value) <= 20 and value.isascii() and value.isdigit() and value[0] != "0":
        return value
    return None


def _prevalidate_locator(payload: object) -> tuple[str, str, dict] | None:
    if not isinstance(payload, dict) or set(payload) != {"mailboxId", "sourceRef"}:
        return None
    mailbox_id = payload.get("mailboxId")
    locator = payload.get("sourceRef")
    if (
        not isinstance(mailbox_id, str)
        or not mailbox_id
        or mailbox_id != mailbox_id.strip()
        or len(mailbox_id) > 256
        or not mailbox_id.isascii()
        or not re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,255}", mailbox_id)
        or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in mailbox_id)
        or not isinstance(locator, dict)
    ):
        return None
    if set(locator) == {"providerMessageId"}:
        identifier = locator.get("providerMessageId")
        if (
            not isinstance(identifier, str)
            or not identifier
            or identifier != identifier.strip()
            or len(identifier) > MAX_PROVIDER_IDENTIFIER
            or not identifier.isascii()
            or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in identifier)
        ):
            return None
        return mailbox_id, "google", {"provider": "google", "providerMessageId": identifier}
    if set(locator) == {"folder", "uidValidity", "imapUid"}:
        uid_validity = _positive_decimal_identifier(locator.get("uidValidity"))
        imap_uid = _positive_decimal_identifier(locator.get("imapUid"))
        if locator.get("folder") != "INBOX" or uid_validity is None or imap_uid is None:
            return None
        return mailbox_id, "custom_imap", {
            "provider": "custom_imap", "folder": "INBOX",
            "uidValidity": uid_validity, "imapUid": imap_uid,
        }
    return None


def resolve_source_message(
    headers,
    payload: object,
    *,
    authorization_resolver=resolve_internal_collaboration_context,
    google_fetcher=_default_google_fetcher,
    imap_fetcher=_default_imap_fetcher,
) -> dict:
    """Resolve an immutable source snapshot from a minimal browser locator."""
    prevalidated = _prevalidate_locator(payload)
    if prevalidated is None:
        return _failure("malformed", "invalid_request")
    mailbox_id, locator_provider, source_ref = prevalidated

    try:
        authorized = authorization_resolver(
            headers, mailbox_id, required_action="create"
        )
    except Exception:
        return _failure("unavailable", "storage_unavailable")
    if not isinstance(authorized, dict) or authorized.get("status") != "ok":
        if isinstance(authorized, dict):
            code = (authorized.get("error") or {}).get("code")
            if code not in {"auth_required", "forbidden", "mailbox_not_found", "storage_unavailable", "invalid_request"}:
                code = "storage_protocol_error"
            return {
                "status": authorized.get("status", "unavailable"),
                "source": None,
                "error": {"code": code},
            }
        return _failure("unavailable", "internal_error")
    context = authorized.get("context")
    if not _is_internal_capability(context, actions={"create"}):
        return _failure("unavailable", "storage_protocol_error")
    provider = context.mailbox_provider

    if provider != locator_provider:
        return _failure("malformed", "invalid_request")
    if provider == "google":
        fetcher = google_fetcher
    elif provider == "custom_imap":
        fetcher = imap_fetcher
    else:
        return _failure("malformed", "invalid_request")

    try:
        fetched = fetcher(headers, mailbox_id, source_ref)
    except Exception:
        return _failure("unavailable", "provider_unavailable")
    if not isinstance(fetched, dict) or fetched.get("status") != "ok":
        code = (fetched.get("error") or {}).get("code") if isinstance(fetched, dict) else None
        if code not in {"source_not_found", "source_changed", "provider_unavailable"}:
            code = "provider_unavailable"
        status = fetched.get("status", "unavailable") if isinstance(fetched, dict) else "unavailable"
        return _failure(status, code)
    if provider == "custom_imap" and fetched.get("uidValidity") != source_ref["uidValidity"]:
        return _failure("conflict", "source_changed")
    snapshot = _snapshot_from_raw(fetched.get("rawMessage"))
    if snapshot is None:
        return _failure("unavailable", "provider_unavailable")
    return {"status": "ok", "source": {"sourceRef": source_ref, "sourceMessage": snapshot}, "error": None}
