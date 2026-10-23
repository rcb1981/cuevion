"""Read-only verified composition + one-shot Graph draft submission.

Not an HTTP endpoint. The caller must resolve owner, scopes, mailbox lease,
and persistent idempotency before calling this module. Never retry a Graph
draft creation or send after an ambiguous transport outcome.
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
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
_MODES = frozenset(("new", "reply", "reply_all", "forward"))

def _forward_source_excerpt(token: str, source_id: str, *, opener=urlopen) -> str:
    exact_id = _required_message_id(source_id)
    source = _graph_http(
        token, "/me/mailFolders/inbox/messages/" + quote(exact_id, safe="")
        + "?$select=id,conversationId,bodyPreview,hasAttachments",
        method="GET", expected_status=200, opener=opener,
    )
    if (type(source) is not dict or source.get("id") != exact_id or
            source.get("hasAttachments") is not False or
            type(source.get("bodyPreview")) is not str):
        raise MicrosoftGraphError("microsoft_reply_source_invalid")
    excerpt = " ".join(source["bodyPreview"].split())[:96]
    if len(excerpt) < 24:
        raise MicrosoftGraphError("microsoft_reply_source_invalid")
    return excerpt


_HEADERS = frozenset(("Authorization", "Content-Type", "Accept", "Prefer"))


def _validated_compose(value: object) -> dict:
    if type(value) is not dict or value.get("mode") not in _MODES:
        raise MicrosoftGraphError("microsoft_compose_invalid")
    mode = value["mode"]
    required = {"mode", "body"}
    if mode == "new":
        required |= {"to", "subject"}
        allowed = required | {"cc"}
    elif mode == "forward":
        required |= {"sourceProviderMessageId", "to"}
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
    if mode in _REPLY_MODES:
        return {"mode": mode, "sourceProviderMessageId": value["sourceProviderMessageId"], "body": body}
    if mode == "new":
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
    if mode == "forward":
        return {"mode": "forward", "sourceProviderMessageId": value["sourceProviderMessageId"],
                "body": body, **normalized}
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


class _VisibleReplyText(HTMLParser):
    """Text-only projection for draft verification, never render Graph HTML."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden_depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "head", "template", "svg"}:
            self.hidden_depth += 1
        elif not self.hidden_depth and tag in {"p", "br", "div", "li", "tr"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "head", "template", "svg"}:
            self.hidden_depth = max(0, self.hidden_depth - 1)
        elif not self.hidden_depth and tag in {"p", "div", "li", "tr"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden_depth:
            self.parts.append(data)


def _normalized_draft_visible_text(body: object) -> str:
    if type(body) is not dict or type(body.get("content")) is not str:
        raise MicrosoftGraphError("microsoft_draft_unconfirmed")
    content = body["content"]
    if len(content) > MAX_BODY_LENGTH * 4:
        raise MicrosoftGraphError("microsoft_draft_unconfirmed")
    kind = str(body.get("contentType", "")).lower()
    if kind == "text":
        return " ".join(content.split()).casefold()
    if kind != "html":
        raise MicrosoftGraphError("microsoft_draft_unconfirmed")
    parser = _VisibleReplyText()
    try:
        parser.feed(content)
        parser.close()
    except (AssertionError, ValueError):
        raise MicrosoftGraphError("microsoft_draft_unconfirmed") from None
    return " ".join(" ".join(parser.parts).split()).casefold()


def _source_reply_context(token: str, source_id: str, *, opener=urlopen) -> dict:
    """Verify the exact Inbox source, conversation and an original body witness."""
    exact_source = _required_message_id(source_id)
    payload = _graph_http(
        token,
        "/me/mailFolders/inbox/messages/" + quote(exact_source, safe="")
        + "?$select=id,conversationId,bodyPreview",
        method="GET", expected_status=200, opener=opener,
    )
    if type(payload) is not dict or payload.get("id") != source_id:
        raise MicrosoftGraphError("microsoft_reply_source_invalid")
    conversation = payload.get("conversationId")
    try:
        _required_message_id(conversation)
    except MicrosoftGraphError:
        raise MicrosoftGraphError("microsoft_reply_source_invalid") from None
    preview = payload.get("bodyPreview")
    if type(preview) is not str:
        raise MicrosoftGraphError("microsoft_reply_source_invalid")
    # A short original message may be valid. Require at least a distinctive
    # portion to verify that the quoted content remains in the reply draft.
    excerpt = " ".join(preview.split())[:96].casefold()
    if len(excerpt) < 12:
        raise MicrosoftGraphError("microsoft_reply_source_invalid")
    return {"conversationId": conversation, "excerpt": excerpt}


def _recipients(value: list[str]) -> list[dict]:
    return [{"emailAddress": {"address": address}} for address in value]


def _verify_draft(
    token: str, draft: dict, compose: dict, *, original_conversation: str | None = None,
    forward_original_excerpt: str | None = None,
    reply_original_excerpt: str | None = None,
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
    if compose["mode"] in _REPLY_MODES:
        if not reply_original_excerpt:
            raise MicrosoftGraphError("microsoft_draft_unconfirmed")
        visible = _normalized_draft_visible_text(body)
        # Graph created a reply with our comment. Neither the comment nor the
        # old message may disappear when the reply draft is created.
        comment = " ".join(compose["body"].split()).casefold()
        if comment not in visible or reply_original_excerpt not in visible:
            raise MicrosoftGraphError("microsoft_draft_unconfirmed")
    elif compose["mode"] == "forward":
        if not forward_original_excerpt:
            raise MicrosoftGraphError("microsoft_draft_unconfirmed")
        visible = _normalized_draft_visible_text(body)
        if (" ".join(compose["body"].split()).casefold() not in visible or
                " ".join(forward_original_excerpt.split()).casefold() not in visible):
            raise MicrosoftGraphError("microsoft_draft_unconfirmed")
    elif (
        type(body) is not dict
        or str(body.get("contentType", "")).lower() != "text"
        or body.get("content") != compose["body"]
    ):
        raise MicrosoftGraphError("microsoft_draft_unconfirmed")
    recipients = payload.get("toRecipients")
    if type(recipients) is not list or not recipients:
        raise MicrosoftGraphError("microsoft_draft_unconfirmed")
    if compose["mode"] in {"new", "forward"}:
        if compose["mode"] == "new" and payload.get("subject") != compose["subject"]:
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
    forward_original_excerpt = None
    reply_original_excerpt = None
    if compose["mode"] == "new":
        path = "/me/messages"
        payload = {
            "subject": compose["subject"],
            "body": {"contentType": "Text", "content": compose["body"]},
            "toRecipients": _recipients(compose["to"]),
            "ccRecipients": _recipients(compose["cc"]),
        }
    elif compose["mode"] == "forward":
        # Exact original Inbox membership is independently confirmed. Forward
        # recipients are explicitly selected by the user, never inferred.
        forward_original_excerpt = _forward_source_excerpt(
            access, compose["sourceProviderMessageId"], opener=opener,
        )
        # A user comment repeating the original preview cannot itself satisfy
        # the proof that the Graph draft retained the original message.
        if forward_original_excerpt.casefold() in " ".join(compose["body"].split()).casefold():
            raise MicrosoftGraphError("microsoft_compose_invalid")
        path = "/me/mailFolders/inbox/messages/" + quote(
            compose["sourceProviderMessageId"], safe=""
        ) + "/createForward"
        payload = {
            "message": {
                "toRecipients": _recipients(compose["to"]),
                "ccRecipients": _recipients(compose["cc"]),
            },
            "comment": compose["body"],
        }
    else:
        source_context = _source_reply_context(
            access, compose["sourceProviderMessageId"], opener=opener,
        )
        original_conversation = source_context["conversationId"]
        reply_original_excerpt = source_context["excerpt"]
        # Graph's comment field retains the original quoted message. Patching
        # the draft body would replace that quote and break conversation parity.
        if reply_original_excerpt in " ".join(compose["body"].split()).casefold():
            raise MicrosoftGraphError("microsoft_compose_invalid")
        operation = "createReply" if compose["mode"] == "reply" else "createReplyAll"
        path = "/me/mailFolders/inbox/messages/" + quote(
            compose["sourceProviderMessageId"], safe=""
        ) + "/" + operation
        payload = {"comment": compose["body"]}

    created = _graph_http(
        access, path, method="POST", payload=payload, expected_status=201, opener=opener,
    )
    draft = _required_draft(created, original_conversation=original_conversation)
    _verify_draft(
        access, draft, compose, original_conversation=original_conversation,
        forward_original_excerpt=forward_original_excerpt,
        reply_original_excerpt=reply_original_excerpt, opener=opener,
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
