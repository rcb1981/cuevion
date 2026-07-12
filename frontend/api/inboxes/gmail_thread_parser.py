from __future__ import annotations

import base64
import html
import re
from datetime import datetime, timezone
from email.header import decode_header
from email.utils import parseaddr

MAX_GMAIL_THREAD_MESSAGES = 500
MAX_MESSAGE_PART_DEPTH = 32
INVALID_CREATED_AT = "1970-01-01T00:00:00.000Z"


class GmailThreadParseError(ValueError):
    pass


def _decode_header(value: object) -> str:
    if not isinstance(value, str):
        return ""
    decoded = []
    for part, encoding in decode_header(value):
        if not isinstance(part, bytes):
            decoded.append(part)
            continue
        try:
            decoded.append(part.decode(encoding or "utf-8", errors="replace"))
        except LookupError:
            decoded.append(part.decode("utf-8", errors="replace"))
    return "".join(decoded)


def _headers(payload: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    values = payload.get("headers")
    if not isinstance(values, list):
        return result
    for entry in values:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        value = entry.get("value")
        if isinstance(name, str) and isinstance(value, str):
            result[name.strip().lower()] = _decode_header(value)
    return result


def _decode_body_data(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))
        return decoded.decode("utf-8", errors="replace")
    except (ValueError, UnicodeEncodeError):
        return ""


def _html_to_text(value: str) -> str:
    without_unsafe_blocks = re.sub(
        r"(?is)<(script|style)\b.*?>.*?</\1>",
        " ",
        value,
    )
    with_lines = re.sub(r"(?i)<br\s*/?>", "\n", without_unsafe_blocks)
    with_lines = re.sub(
        r"(?i)</(p|div|li|tr|table|h[1-6])>",
        "\n",
        with_lines,
    )
    plain = re.sub(r"(?s)<[^>]+>", " ", with_lines)
    plain = html.unescape(plain).replace("\r\n", "\n")
    return "\n".join(line.strip() for line in plain.splitlines() if line.strip())


def _created_at(internal_date: str) -> tuple[str, int | None]:
    try:
        milliseconds = int(internal_date)
        value = datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
        return value.isoformat(timespec="milliseconds").replace("+00:00", "Z"), milliseconds
    except (ValueError, OverflowError, OSError):
        return INVALID_CREATED_AT, None


def _disposition(headers: dict[str, str]) -> str | None:
    value = headers.get("content-disposition", "").strip()
    return value.split(";", 1)[0].strip().lower() or None


def _content_id(headers: dict[str, str]) -> str | None:
    value = headers.get("content-id", "").strip()
    return value.strip("<>") or None


def _walk_parts(
    part: dict,
    plain_parts: list[str],
    html_parts: list[str],
    attachments: list[dict],
    *,
    depth: int = 0,
    active_path: set[int] | None = None,
    inside_attachment: bool = False,
):
    if depth > MAX_MESSAGE_PART_DEPTH:
        raise GmailThreadParseError("Gmail message part nesting is too deep.")

    current_path = active_path if active_path is not None else set()
    part_identity = id(part)
    if part_identity in current_path:
        raise GmailThreadParseError("Gmail message parts contain a cycle.")
    current_path.add(part_identity)

    try:
        headers = _headers(part)
        body = part.get("body") if isinstance(part.get("body"), dict) else {}
        mime_type = part.get("mimeType") if isinstance(part.get("mimeType"), str) else ""
        filename = part.get("filename") if isinstance(part.get("filename"), str) else ""
        part_id = part.get("partId") if isinstance(part.get("partId"), str) else ""
        attachment_id = (
            body.get("attachmentId") if isinstance(body.get("attachmentId"), str) else None
        )
        disposition = _disposition(headers)
        is_attachment = bool(
            filename
            or attachment_id
            or disposition in {"attachment", "inline"}
        )
        next_inside_attachment = inside_attachment or is_attachment

        if is_attachment:
            size = body.get("size")
            attachments.append(
                {
                    "partId": part_id,
                    "providerAttachmentId": attachment_id,
                    "name": filename or "Attachment",
                    "mimeType": mime_type or None,
                    "size": size if isinstance(size, int) and not isinstance(size, bool) else None,
                    "contentId": _content_id(headers),
                    "disposition": disposition,
                }
            )
        elif not next_inside_attachment:
            decoded = _decode_body_data(body.get("data"))
            if decoded:
                if mime_type.lower() == "text/plain":
                    plain_parts.append(decoded)
                elif mime_type.lower() == "text/html":
                    html_parts.append(decoded)

        child_parts = part.get("parts")
        if isinstance(child_parts, list):
            parts_identity = id(child_parts)
            if parts_identity in current_path:
                raise GmailThreadParseError("Gmail message parts contain a cycle.")
            current_path.add(parts_identity)
            try:
                for child in child_parts:
                    if isinstance(child, dict):
                        _walk_parts(
                            child,
                            plain_parts,
                            html_parts,
                            attachments,
                            depth=depth + 1,
                            active_path=current_path,
                            inside_attachment=next_inside_attachment,
                        )
            finally:
                current_path.remove(parts_identity)
    finally:
        current_path.remove(part_identity)


def parse_gmail_thread_message(message: dict) -> tuple[dict, int | None]:
    provider_message_id = message.get("id")
    provider_thread_id = message.get("threadId")
    if not isinstance(provider_message_id, str) or not provider_message_id:
        raise GmailThreadParseError("Gmail message id is missing.")
    if not isinstance(provider_thread_id, str) or not provider_thread_id:
        raise GmailThreadParseError("Gmail thread id is missing.")

    payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
    headers = _headers(payload)
    plain_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict] = []
    _walk_parts(payload, plain_parts, html_parts, attachments)

    body_html = "\n".join(part for part in html_parts if part.strip()) or None
    body_text = "\n".join(part for part in plain_parts if part.strip())
    if not body_text and body_html:
        body_text = _html_to_text(body_html)

    raw_internal_date = message.get("internalDate")
    internal_date = raw_internal_date if isinstance(raw_internal_date, str) else ""
    created_at, numeric_internal_date = _created_at(internal_date)
    from_header = headers.get("from", "")
    sender_name, sender_email = parseaddr(from_header)
    label_ids = message.get("labelIds")
    normalized_labels = (
        [value for value in label_ids if isinstance(value, str)]
        if isinstance(label_ids, list)
        else []
    )
    rfc_message_id = headers.get("message-id", "").strip().strip("<>") or None

    return {
        "providerMessageId": provider_message_id,
        "providerThreadId": provider_thread_id,
        "rfcMessageId": rfc_message_id,
        "internalDate": internal_date,
        "createdAt": created_at,
        "dateHeader": headers.get("date") or None,
        "sender": sender_name or sender_email or from_header or "Unknown sender",
        "from": from_header,
        "to": headers.get("to", ""),
        "cc": headers.get("cc", ""),
        "subject": headers.get("subject", ""),
        "snippet": message.get("snippet") if isinstance(message.get("snippet"), str) else "",
        "bodyText": body_text,
        "bodyHtml": body_html,
        "labelIds": normalized_labels,
        "unread": "UNREAD" in normalized_labels,
        "flagged": "STARRED" in normalized_labels,
        "attachments": attachments,
    }, numeric_internal_date


def parse_gmail_thread(payload: object, expected_thread_id: str) -> list[dict]:
    if not isinstance(payload, dict) or payload.get("id") != expected_thread_id:
        raise GmailThreadParseError("Gmail thread response is invalid.")
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise GmailThreadParseError("Gmail thread messages are missing.")

    unique: dict[str, tuple[dict, int | None]] = {}
    for raw_message in messages:
        if not isinstance(raw_message, dict):
            raise GmailThreadParseError("Gmail thread contains an invalid message.")
        parsed, numeric_date = parse_gmail_thread_message(raw_message)
        if parsed["providerThreadId"] != expected_thread_id:
            raise GmailThreadParseError("Gmail message belongs to another thread.")
        unique.setdefault(parsed["providerMessageId"], (parsed, numeric_date))

    if len(unique) > MAX_GMAIL_THREAD_MESSAGES:
        raise OverflowError("Gmail thread contains too many messages.")

    ordered = sorted(
        unique.values(),
        key=lambda item: (
            0 if item[1] is not None else 1,
            item[1] if item[1] is not None else 0,
            item[0]["providerMessageId"],
        ),
    )
    return [message for message, _ in ordered]
