"""Private, read-only Gmail drafts. No sync, classification or shared snapshots."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import time
from email.message import Message
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .authenticated_gmail import (
    MAX_GMAIL_RESPONSE_BYTES, gmail_http_error_code, read_bounded_response,
    resolve_owned_mailbox, resolve_gmail_context, refresh_gmail_context,
)
from .oauth_token_store import load_google_token_record_with_metadata

MAX_PAGE = 20
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_PARTS = 100
GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


class DraftReadError(Exception):
    def __init__(self, code="gmail_drafts_unavailable", status=502):
        self.code, self.status = code, status
        super().__init__(code)  # Never include provider content or identifiers.


def identifier(value):
    return type(value) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value) is not None


def page_token(value):
    return type(value) is str and re.fullmatch(r"[\x21-\x7e]{1,4096}", value) is not None


def unique_object(pairs):
    value = {}
    for key, entry in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = entry
    return value


def resolve_context(headers, mailbox_id):
    owned = resolve_owned_mailbox(headers, mailbox_id, include_member_authority=True)
    if owned["status"] != "ok":
        raise DraftReadError(owned["error"]["error"]["code"], owned["status_code"])
    resolved = resolve_gmail_context(owned)
    if resolved["status"] != "ok":
        raise DraftReadError(resolved["error"]["error"]["code"], resolved["status_code"])
    context = resolved["context"]
    member = owned["memberAuthority"]
    record, error = load_google_token_record_with_metadata(context["mailbox_email"], owner_email=context["owner_email"])
    if (error or type(record) is not dict or record.get("_storage_durable") is not True
            or record.get("provider") != "google" or record.get("email") != context["mailbox_email"]
            or record.get("owner_email") != context["owner_email"]
            or type(record.get("access_token")) is not str
            or record["access_token"].strip() != context["access_token"]
            or type(record.get("credential_generation")) is not str
            or re.fullmatch(r"[A-Za-z0-9_-]{43}", record["credential_generation"]) is None):
        raise DraftReadError("gmail_drafts_connection_changed", 409)
    # Authority comes from the server member resolver, never from request fields.
    binding = [member.user_id, member.workspace_id, context["mailbox_id"],
               context["mailbox_email"], record["credential_generation"]]
    return {**context, "draft_scope": hashlib.sha256(json.dumps(binding).encode()).hexdigest()}


def request_json(context, path, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DraftReadError()
    request = Request(GMAIL_BASE + path, headers={"Authorization": "Bearer " + context["access_token"],
                                                  "Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=min(20, remaining)) as response:
            raw = read_bounded_response(response, MAX_GMAIL_RESPONSE_BYTES)
        if raw is None:
            raise DraftReadError("gmail_response_too_large")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
        if type(value) is not dict:
            raise ValueError()
        return value
    except HTTPError as error:
        if error.code == 404:
            raise DraftReadError("gmail_draft_not_found", 404) from None
        code = gmail_http_error_code(error.code, "gmail_drafts_unavailable")
        raise DraftReadError(code, 401 if error.code == 401 else 403 if error.code == 403 else 502) from None
    except (ValueError, UnicodeError):
        raise DraftReadError("gmail_response_invalid") from None
    except (URLError, TimeoutError, OSError):
        raise DraftReadError() from None


def headers_of(part):
    headers = part.get("headers", [])
    if type(headers) is not list or len(headers) > 200:
        raise ValueError()
    total = 0
    for header in headers:
        if (type(header) is not dict or set(header) != {"name", "value"}
                or type(header["name"]) is not str or not re.fullmatch(r"[!-9;-~]{1,128}", header["name"])
                or type(header["value"]) is not str or len(header["value"].encode()) > 16384
                or "\x00" in header["value"]):
            raise ValueError()
        total += len(header["value"].encode())
    if total > 65536:
        raise ValueError()
    return headers


def metadata(value, requested_id):
    message = value.get("message")
    if (value.get("id") != requested_id or type(message) is not dict
            or not identifier(message.get("id")) or not identifier(message.get("threadId"))
            or message.get("labelIds") != ["DRAFT"] or type(message.get("payload")) is not dict):
        raise DraftReadError("gmail_response_invalid")
    try:
        headers = headers_of(message["payload"])
        date = message.get("internalDate")
        if date is not None and (type(date) is not str or not re.fullmatch(r"[0-9]{1,16}", date)):
            raise ValueError()
        get = lambda name: ", ".join(h["value"] for h in headers if h["name"].lower() == name)
        return {"draftId": requested_id, "messageId": message["id"], "threadId": message["threadId"],
                "from": get("from"), "to": get("to"), "cc": get("cc"), "bcc": get("bcc"),
                "subject": get("subject"), "internalDate": date, "headers": headers}
    except (ValueError, UnicodeError):
        raise DraftReadError("gmail_response_invalid") from None


def preview(meta, payload):
    result = {**meta, "previewStatus": "available", "unavailableReason": None,
              "bodyText": None, "bodyHtml": None, "attachments": [], "mimeParts": [], "inventoryComplete": True}
    bodies = {"text/plain": [], "text/html": []}
    seen = set()
    total = 0
    reason = None

    def walk(part, parent=None, depth=0):
        nonlocal total, reason
        if type(part) is not dict or depth > 12 or len(seen) >= MAX_PARTS:
            raise ValueError()
        part_id = part.get("partId", "")
        mime = part.get("mimeType")
        filename = part.get("filename", "")
        body = part.get("body", {})
        parts = part.get("parts", [])
        if (type(part_id) is not str or len(part_id) > 128 or part_id in seen
                or type(mime) is not str or not re.fullmatch(r"[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+", mime)
                or type(filename) is not str or len(filename.encode()) > 4096
                or type(body) is not dict or type(parts) is not list
                or type(body.get("size")) is not int or not 0 <= body["size"] <= 2**31):
            raise ValueError()
        seen.add(part_id)
        headers = headers_of(part)
        info = Message()
        for header in headers:
            info[header["name"]] = header["value"]
        attachment_id = body.get("attachmentId")
        if attachment_id is not None and not page_token(attachment_id):
            raise ValueError()
        mime = mime.lower()
        disposition = info.get_content_disposition()
        item = {"partId": part_id, "parentPartId": parent, "mimeType": mime,
                "filename": filename, "size": body["size"], "headers": headers,
                "attachmentId": attachment_id}
        result["mimeParts"].append(item)
        is_attachment = bool(filename or disposition == "attachment" or attachment_id and mime not in bodies)
        data = body.get("data")
        decoded = None
        if data is not None:
            if type(data) is not str or not re.fullmatch(r"[A-Za-z0-9_-]*={0,2}", data):
                raise ValueError()
            decoded = base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)
            if len(decoded) != body["size"]:
                raise ValueError()
        if is_attachment:
            result["attachments"].append({**item, "availability": "metadata_only"})
            if parts:
                reason = reason or "unsupported_mime"
                result["inventoryComplete"] = False
            return
        if mime in {"multipart/mixed", "multipart/alternative", "multipart/related"}:
            if not parts or data or body["size"] != 0:
                raise ValueError()
            for child in parts:
                walk(child, part_id, depth + 1)
        elif mime in bodies and not parts:
            if decoded is None:
                if body["size"] != 0 or attachment_id:
                    reason = reason or "body_unavailable"
                    return
                decoded = b""
            total += len(decoded)
            if total > MAX_BODY_BYTES:
                reason = "too_large"
                return
            bodies[mime].append(decoded.decode(info.get_content_charset() or "utf-8", errors="strict"))
        else:
            reason = reason or "unsupported_mime"
            if parts:
                result["inventoryComplete"] = False

    try:
        walk(payload)
        if any(len(values) > 1 for values in bodies.values()):
            reason = reason or "unsupported_mime"
        if reason:
            result.update(previewStatus="unavailable", unavailableReason=reason)
        else:
            result["bodyText"] = next(iter(bodies["text/plain"]), None)
            result["bodyHtml"] = next(iter(bodies["text/html"]), None)
    except (ValueError, TypeError, LookupError, UnicodeError, binascii.Error, RecursionError):
        result.update(previewStatus="unavailable", unavailableReason="malformed", inventoryComplete=False,
                      attachments=[], mimeParts=[])
    return result


def read_drafts(headers, mailbox_id, *, limit=MAX_PAGE, token=None, draft_id=None, scope=None):
    context = resolve_context(headers, mailbox_id)
    expected = context["draft_scope"]
    if scope is not None and scope != expected:
        raise DraftReadError("gmail_drafts_connection_changed", 409)
    deadline = time.monotonic() + 45

    def current():
        if resolve_context(headers, mailbox_id)["draft_scope"] != expected:
            raise DraftReadError("gmail_drafts_connection_changed", 409)

    def get(path):
        nonlocal context
        try:
            return request_json(context, path, deadline)
        except DraftReadError as error:
            if error.code != "gmail_token_invalid" or context["refresh_attempted"]:
                raise
            current()
            refreshed = refresh_gmail_context(context)
            if refreshed["status"] != "ok":
                raise DraftReadError("reconnect_required", 401) from None
            context = {**refreshed["context"], "draft_scope": expected}
            current()
            return request_json(context, path, deadline)

    if draft_id is not None:
        path = "/drafts/" + quote(draft_id, safe="")
        try:
            value = get(path + "?format=full")
            detail = preview(metadata(value, draft_id), value["message"]["payload"])
        except DraftReadError as error:
            if error.code != "gmail_response_too_large":
                raise
            detail = {**metadata(get(path + "?format=metadata"), draft_id), "previewStatus": "unavailable",
                      "unavailableReason": "too_large", "bodyText": None, "bodyHtml": None,
                      "attachments": [], "mimeParts": [], "inventoryComplete": False}
        result = {"draft": detail}
    else:
        value = get("/drafts?" + urlencode({"maxResults": limit, **({"pageToken": token} if token else {})}))
        refs = value.get("drafts", [])
        next_token = value.get("nextPageToken")
        if (type(refs) is not list or len(refs) > limit
                or (next_token is not None and (not page_token(next_token) or next_token == token))
                or ("resultSizeEstimate" in value and (type(value["resultSizeEstimate"]) is not int or value["resultSizeEstimate"] < 0))):
            raise DraftReadError("gmail_response_invalid")
        rows, seen = [], set()
        for ref in refs:
            if (type(ref) is not dict or not identifier(ref.get("id")) or type(ref.get("message")) is not dict
                    or not identifier(ref["message"].get("id")) or not identifier(ref["message"].get("threadId"))):
                raise DraftReadError("gmail_response_invalid")
            if ref["id"] in seen:
                continue
            seen.add(ref["id"])
            try:
                rows.append(metadata(get("/drafts/" + quote(ref["id"], safe="") + "?format=metadata"), ref["id"]))
            except DraftReadError as error:
                if error.code != "gmail_draft_not_found":
                    raise
                # Removed externally between list/get; never substitute a message/thread.
        result = {"drafts": rows, "nextPageToken": next_token}
    current()  # Revalidate server session, ownership, connection and credential generation before publication.
    response = {"ok": True, "mailboxId": mailbox_id, "scope": expected, **result}
    if len(json.dumps(response).encode()) > MAX_GMAIL_RESPONSE_BYTES:
        raise DraftReadError("gmail_response_too_large")
    return response
