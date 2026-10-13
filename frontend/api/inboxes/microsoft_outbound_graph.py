"""Read-only verified composition + one-shot Graph draft submission.

Not an HTTP endpoint. The caller must resolve owner, scopes, mailbox lease,
and persistent idempotency before calling this module. Never retry a Graph
draft creation or send after an ambiguous transport outcome.
"""
from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from http.client import IncompleteRead
from urllib.parse import quote
from urllib.request import Request, urlopen

from api.auth.email_address import is_valid_auth_email, normalize_auth_email
from api.inboxes.microsoft_graph import (
    GRAPH_ROOT, GRAPH_TIMEOUT_SECONDS, MicrosoftGraphError,
    _GRAPH_PREFER, _required_access_token, _required_message_id,
)

MAX_RECIPIENTS = 20
MAX_SUBJECT_LENGTH = 250
MAX_BODY_LENGTH = 64_000
MAX_GRAPH_DRAFT_BYTES = 64 * 1024
_REPLY_MODES = frozenset(("reply", "reply_all"))
_MODES = frozenset(("new", "reply", "reply_all"))
_HEADERS = frozenset(("Authorization", "Content-Type", "Accept", "Prefer"))


def _validated_compose(value: object) -> dict:
    if type(value) is not dict or value.get("mode") not in _MODES:
        raise MicrosoftGraphError("microsoft_compose_invalid")
    mode = value["mode"]
    required = {"mode", "body"}
    if mode == "new":
        required |= {"to", "subject"}
        allowed = required | {"cc"}
    else:
        required |= {"sourceProviderMessageId"}
        allowed = required
    if not required <= set(value) or not set(value) <= allowed:
        raise MicrosoftGraphError("microsoft_compose_invalid")
    body = value["body"]
    if (
        type(body) is not str
        or not 1 <= len(body.strip()) <= MAX_BODY_LENGTH
        or len(body.encode("utf-8")) > MAX_BODY_LENGTH * 4
        or "\x00" in body
    ):
        raise MicrosoftGraphError("microsoft_compose_invalid")
    if mode != "new":
        _required_message_id(value["sourceProviderMessageId"])
        return {"mode": mode, "sourceProviderMessageId": value["sourceProviderMessageId"], "body": body}
    subject = value["subject"]
    if (
        type(subject) is not str or not 1 <= len(subject.strip()) <= MAX_SUBJECT_LENGTH
        or "\r" in subject or "\n" in subject
        or any(ord(c) < 32 or ord(c) == 127 for c in subject)
    ):
        raise MicrosoftGraphError("microsoft_compose_invalid")
    normalized = {}
    for key in ("to", "cc"):
        addresses = value.get(key, [] if key == "cc" else None)
        if type(addresses) is not list or len(addresses) > MAX_RECIPIENTS:
            raise MicrosoftGraphError("microsoft_compose_invalid")
        selected = []
        for address in addresses:
            if type(address) is not str:
                raise MicrosoftGraphError("microsoft_compose_invalid")
            canonical = normalize_auth_email(address)
            if not is_valid_auth_email(canonical) or canonical != address:
                raise MicrosoftGraphError("microsoft_compose_invalid")
            selected.append(address)
        if len(set(selected)) != len(selected):
            raise MicrosoftGraphError("microsoft_compose_invalid")
        normalized[key] = selected
    if not normalized["to"] or len(normalized["to"]) + len(normalized["cc"]) > MAX_RECIPIENTS:
        raise MicrosoftGraphError("microsoft_compose_invalid")
    if set(normalized["to"]) & set(normalized["cc"]):
        raise MicrosoftGraphError("microsoft_compose_invalid")
    return {"mode": "new", "subject": subject, "body": body, **normalized}


def _graph_http(
    token: str, path: str, *, method: str, payload: object = None,
    expected_status: int, opener=urlopen, response_limit: int = MAX_GRAPH_DRAFT_BYTES,
) -> dict | None:
    # The path must originate only from fixed internal caller construction.
    if (
        not path.startswith("/me/")
        or "//" in path or ".." in path
        or "#" in path
        or ("?" in path and (path.count("?") != 1 or "?$select=" not in path))
        or method not in {"GET", "POST", "PATCH"}
        or type(expected_status) is not int
    ):
        raise MicrosoftGraphError("microsoft_graph_path_invalid")
    headers = {
        "Authorization": "Bearer " + _required_access_token(token),
        "Accept": "application/json",
        "Prefer": _GRAPH_PREFER,
    }
    data = None
    if method in {"POST", "PATCH"}:
        data = b"" if payload is None else json.dumps(
            payload, allow_nan=False, separators=(",", ":"), ensure_ascii=True,
        ).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(GRAPH_ROOT + path, data=data, method=method, headers=headers)
    try:
        with opener(req, timeout=GRAPH_TIMEOUT_SECONDS) as response:
            if getattr(response, "status", None) != expected_status:
                raise MicrosoftGraphError(
                    "microsoft_send_unconfirmed" if path.endswith("/send")
                    else "microsoft_draft_unconfirmed"
                )
            if expected_status == 202:
                # 202 is an acceptance, NOT a delivery/Sent copy confirmation.
                return None
            body = response.read(response_limit + 1)
    except HTTPError as error:
        status = error.code
        try:
            error.close()
        except Exception:
            pass
        if status in (401, 403):
            raise MicrosoftGraphError("microsoft_reconnect_required") from None
        if status in (404, 409, 412):
            raise MicrosoftGraphError("microsoft_draft_conflict") from None
        if status == 429:
            raise MicrosoftGraphError("microsoft_graph_rate_limited") from None
        raise MicrosoftGraphError("microsoft_draft_unconfirmed") from None
    except (URLError, TimeoutError, OSError, IncompleteRead):
        raise MicrosoftGraphError(
            "microsoft_send_unconfirmed" if path.endswith("/send")
            else "microsoft_draft_unconfirmed"
        ) from None
    if type(body) is not bytes or len(body) > response_limit:
        raise MicrosoftGraphError("microsoft_draft_unconfirmed")
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MicrosoftGraphError("microsoft_draft_unconfirmed") from None
    if type(parsed) is not dict:
        raise MicrosoftGraphError("microsoft_draft_unconfirmed")
    return parsed


def _required_draft(payload: object, *, original_conversation: str | None = None) -> dict:
    if type(payload) is not dict:
        raise MicrosoftGraphError("microsoft_draft_unconfirmed")
    draft_id = payload.get("id")
    try:
        _required_message_id(draft_id)
    except MicrosoftGraphError:
        raise MicrosoftGraphError("microsoft_draft_unconfirmed") from None
    if payload.get("isDraft") is not True:
        raise MicrosoftGraphError("microsoft_draft_unconfirmed")
    conversation = payload.get("conversationId")
    if original_conversation is not None and conversation != original_conversation:
        raise MicrosoftGraphError("microsoft_draft_unconfirmed")
    if conversation is not None:
        try:
            _required_message_id(conversation)
        except MicrosoftGraphError:
            raise MicrosoftGraphError("microsoft_draft_unconfirmed") from None
    return {"draftId": draft_id, "conversationId": conversation}


def _source_conversation(token: str, source_id: str, *, opener=urlopen) -> str:
    exact_source = _required_message_id(source_id)
    payload = _graph_http(
        token,
        "/me/mailFolders/inbox/messages/" + quote(exact_source, safe="")
        + "?$select=id,conversationId",
        method="GET", expected_status=200, opener=opener,
    )
    if type(payload) is not dict or payload.get("id") != source_id:
        raise MicrosoftGraphError("microsoft_reply_source_invalid")
    conversation = payload.get("conversationId")
    try:
        _required_message_id(conversation)
    except MicrosoftGraphError:
        raise MicrosoftGraphError("microsoft_reply_source_invalid") from None
    return conversation


def _recipients(value: list[str]) -> list[dict]:
    return [{"emailAddress": {"address": address}} for address in value]


def _verify_draft(
    token: str, draft: dict, compose: dict, *, original_conversation: str | None = None,
    opener=urlopen,
) -> None:
    payload = _graph_http(
        token,
        "/me/messages/" + quote(draft["draftId"], safe="")
        + "?$select=id,isDraft,body,subject,toRecipients,ccRecipients,conversationId",
        method="GET", expected_status=200, opener=opener,
    )
    _required_draft(payload, original_conversation=original_conversation)
    if payload["id"] != draft["draftId"]:
        raise MicrosoftGraphError("microsoft_draft_unconfirmed")
    body = payload.get("body")
    if (
        type(body) is not dict
        or str(body.get("contentType", "")).lower() != "text"
        or body.get("content") != compose["body"]
    ):
        raise MicrosoftGraphError("microsoft_draft_unconfirmed")
    recipients = payload.get("toRecipients")
    if type(recipients) is not list or not recipients:
        raise MicrosoftGraphError("microsoft_draft_unconfirmed")
    if compose["mode"] == "new":
        if payload.get("subject") != compose["subject"]:
            raise MicrosoftGraphError("microsoft_draft_unconfirmed")
        actual = [
            (item.get("emailAddress") or {}).get("address")
            for item in recipients if type(item) is dict
        ]
        if actual != compose["to"]:
            raise MicrosoftGraphError("microsoft_draft_unconfirmed")
        cc_records = payload.get("ccRecipients")
        if type(cc_records) is not list:
            raise MicrosoftGraphError("microsoft_draft_unconfirmed")
        actual_cc = [
            (entry.get("emailAddress") or {}).get("address")
            for entry in cc_records if type(entry) is dict
        ]
        if actual_cc != compose["cc"]:
            raise MicrosoftGraphError("microsoft_draft_unconfirmed")


def prepare_microsoft_draft(
    token: str, value: dict, *, opener=urlopen,
) -> dict:
    """Create one draft and verify it. NEVER retry on an ambiguous failure."""
    compose = _validated_compose(value)
    access = _required_access_token(token)
    original_conversation = None
    if compose["mode"] == "new":
        path = "/me/messages"
        payload = {
            "subject": compose["subject"],
            "body": {"contentType": "Text", "content": compose["body"]},
            "toRecipients": _recipients(compose["to"]),
            "ccRecipients": _recipients(compose["cc"]),
        }
    else:
        original_conversation = _source_conversation(
            access, compose["sourceProviderMessageId"], opener=opener,
        )
        operation = "createReply" if compose["mode"] == "reply" else "createReplyAll"
        path = "/me/mailFolders/inbox/messages/" + quote(
            compose["sourceProviderMessageId"], safe=""
        ) + "/" + operation
        payload = None

    created = _graph_http(
        access, path, method="POST", payload=payload, expected_status=201, opener=opener,
    )
    draft = _required_draft(created, original_conversation=original_conversation)
    if compose["mode"] != "new":
        updated = _graph_http(
            access,
            "/me/messages/" + quote(draft["draftId"], safe=""),
            method="PATCH",
            payload={"body": {"contentType": "Text", "content": compose["body"]}},
            expected_status=200, opener=opener,
        )
        _required_draft(updated, original_conversation=original_conversation)
        if updated["id"] != draft["draftId"]:
            raise MicrosoftGraphError("microsoft_draft_unconfirmed")
    _verify_draft(
        access, draft, compose, original_conversation=original_conversation,
        opener=opener,
    )
    return draft


def submit_prepared_microsoft_draft(
    token: str, draft_id: str, *, opener=urlopen,
) -> dict:
    """Issue ONE send to an already persisted draft; 202 != delivered."""
    exact_id = _required_message_id(draft_id)
    _graph_http(
        token, "/me/messages/" + quote(exact_id, safe="") + "/send",
        method="POST", expected_status=202, opener=opener,
    )
    return {"status": "accepted_by_microsoft", "draftId": exact_id}
