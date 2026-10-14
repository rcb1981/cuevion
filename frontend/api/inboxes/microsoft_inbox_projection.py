"""Project bounded Microsoft Graph read payloads into Cuevion inbox DTOs.

Never treat a Graph bodyPreview as the complete message body. HTML returned by
Graph is projected as inert plain text for the exact-message read response.
"""
from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit

from api.inboxes.microsoft_graph import MicrosoftGraphError

_MAX_BODY_CHARACTERS = 262_144
_IGNORED_HTML = frozenset(("script", "style", "noscript", "template", "svg"))


def _safe_message_href(value: object) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        return None
    if value != value.strip() or any(ord(c) < 33 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in ("http", "https"):
            return None
        if not parsed.hostname or parsed.username or parsed.password:
            return None
    except ValueError:
        return None
    return value


class _PlainTextHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fragments: list[str] = []
        self.ignore_depth = 0
        self.emitted = 0
        self.anchors: list[tuple[int, str | None]] = []
        self.link_count = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _IGNORED_HTML:
            self.ignore_depth += 1
        elif not self.ignore_depth and tag == "a":
            raw_href = next((v for k, v in attrs if k == "href"), None)
            href = _safe_message_href(raw_href) if self.link_count < 128 else None
            self.anchors.append((len(self.fragments), href))
            self.link_count += 1
        elif not self.ignore_depth and tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.fragments.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _IGNORED_HTML and self.ignore_depth:
            self.ignore_depth -= 1
        elif not self.ignore_depth and tag == "a" and self.anchors:
            start, href = self.anchors.pop()
            label = "".join(self.fragments[start:]).strip()
            if href and href not in label and self.emitted + len(href) + 3 < _MAX_BODY_CHARACTERS:
                self.fragments.append((" " if label else "") + "(" + href + ")")
                self.emitted += len(href) + 3
        elif not self.ignore_depth and tag in {"p", "div", "li", "tr"}:
            self.fragments.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignore_depth and self.emitted < _MAX_BODY_CHARACTERS:
            truncated = data[:_MAX_BODY_CHARACTERS - self.emitted]
            self.fragments.append(truncated)
            self.emitted += len(truncated)

    def result(self) -> str:
        return "\n".join(
            piece.strip() for piece in "".join(self.fragments).splitlines() if piece.strip()
        )[:_MAX_BODY_CHARACTERS]


def _timestamp(value: object) -> tuple[str, str]:
    if type(value) is not str:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise MicrosoftGraphError("microsoft_graph_invalid_response") from None
    if parsed.tzinfo is None:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    return parsed.isoformat(), parsed.strftime("%B %-d at %H:%M")


def project_message(message: object, mailbox_id: str, *, exact_body: bool = False, folder: str = "INBOX") -> dict:
    if folder not in {"INBOX", "ARCHIVE"}:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    if not isinstance(message, dict):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    required = (
        "id", "subject", "senderEmail", "senderName", "preview",
        "receivedAt", "unread", "hasAttachments",
    )
    if any(key not in message for key in required):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    mid, subject, sender, name, snippet = (
        message["id"], message["subject"], message["senderEmail"],
        message["senderName"], message["preview"],
    )
    if any(type(value) is not str for value in (mid, subject, sender, name, snippet)):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    if type(mailbox_id) is not str or not mailbox_id:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    if type(message["unread"]) is not bool or type(message["hasAttachments"]) is not bool:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    created_at, display_time = _timestamp(message["receivedAt"])
    result = {
        "id": mid,
        "providerMessageId": mid,
        "serverMailboxId": mailbox_id,
        "providerFolder": folder,
        "labelIds": [folder, *(["UNREAD"] if message["unread"] else [])],
        "sender": name or sender,
        "subject": subject,
        "snippet": snippet[:220],
        "from": f"{name} <{sender}>" if name and name != sender else sender,
        "to": "",
        "cc": "",
        "timestamp": display_time,
        "createdAt": created_at,
        "body": [],
        "unread": message["unread"],
        "flagged": False,
        "attachments": [],
        "hasAttachments": message["hasAttachments"],
    }
    thread_id = message.get("conversationId")
    if type(thread_id) is str and thread_id:
        result["providerThreadId"] = thread_id
    if not exact_body:
        # This data is a listing, not a verified complete message body.
        return result

    body = message.get("body")
    if not isinstance(body, dict):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    kind, content = body.get("contentType"), body.get("content")
    if kind not in {"text", "html"} or type(content) is not str:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    if kind == "html":
        parser = _PlainTextHTML()
        try:
            parser.feed(content[:_MAX_BODY_CHARACTERS])
            parser.close()
            text = parser.result()
        except (ValueError, AssertionError):
            raise MicrosoftGraphError("microsoft_graph_invalid_response") from None
    else:
        text = content[:_MAX_BODY_CHARACTERS]
    result["body"] = [section for section in text.split("\n\n") if section] or [""]
    # Deliberately do not include untrusted Graph HTML, tracked image links,
    # attachments without authoritative metadata or raw Graph tokens.
    return result


def project_inbox(snapshot: object, mailbox_id: str) -> dict:
    if (
        not isinstance(snapshot, dict)
        or type(snapshot.get("messages")) is not list
        or type(snapshot.get("hasMore")) is not bool
        or len(snapshot["messages"]) > 25
    ):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    projected = [project_message(m, mailbox_id) for m in snapshot["messages"]]
    ids = [m["providerMessageId"] for m in projected]
    if len(ids) != len(set(ids)):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    return {
        "ok": True,
        "messages": projected,
        "inboxUidSet": ids,
        "uidValidity": "microsoft-graph-immutable",
        "hasMore": snapshot["hasMore"],
    }



def project_archive(snapshot: object, mailbox_id: str) -> dict:
    """Bounded Archive listing; never report archived mail as Inbox content."""
    if (
        type(snapshot) is not dict
        or type(snapshot.get("messages")) is not list
        or type(snapshot.get("hasMore")) is not bool
        or len(snapshot["messages"]) > 25
    ):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    projected = [project_message(message, mailbox_id, folder="ARCHIVE")
                 for message in snapshot["messages"]]
    identifiers = [entry["providerMessageId"] for entry in projected]
    if len(identifiers) != len(set(identifiers)):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    return {
        "ok": True, "messages": projected,
        "archiveMessageIds": identifiers, "hasMore": snapshot["hasMore"],
    }


def project_sent_message(message: object, mailbox_id: str, *, exact_body: bool = False) -> dict:
    """Project a Graph SentItems record without inventing Inbox membership."""
    if type(message) is not dict or type(mailbox_id) is not str or not mailbox_id:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    needed = ("id", "subject", "preview", "sentAt", "toDisplay", "fromDisplay", "hasAttachments")
    if any(field not in message for field in needed):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    mid, subject, snippet, to_display, from_display = (
        message["id"], message["subject"], message["preview"],
        message["toDisplay"], message["fromDisplay"],
    )
    if any(type(value) is not str for value in (mid, subject, snippet, to_display, from_display)):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    if not mid or type(message["hasAttachments"]) is not bool:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    created_at, display_time = _timestamp(message["sentAt"])
    result = {
        "id": mid,
        "providerMessageId": mid,
        "serverMailboxId": mailbox_id,
        "providerFolder": "SENT",
        "labelIds": ["SENT"],
        "sender": "To: " + to_display,
        "subject": subject,
        "snippet": snippet[:220],
        "from": from_display,
        "to": to_display,
        "cc": "",
        "timestamp": display_time,
        "createdAt": created_at,
        "body": [],
        "unread": False,
        "flagged": False,
        "attachments": [],
        "hasAttachments": message["hasAttachments"],
    }
    conversation_id = message.get("conversationId")
    if type(conversation_id) is str and conversation_id:
        result["providerThreadId"] = conversation_id
    if not exact_body:
        return result

    body = message.get("body")
    if type(body) is not dict:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    content_type = body.get("contentType")
    content = body.get("content")
    if content_type not in ("html", "text") or type(content) is not str:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    if content_type == "html":
        parser = _PlainTextHTML()
        try:
            parser.feed(content[:_MAX_BODY_CHARACTERS])
            parser.close()
            plain = parser.result()
        except (ValueError, AssertionError):
            raise MicrosoftGraphError("microsoft_graph_invalid_response") from None
    else:
        plain = content[:_MAX_BODY_CHARACTERS]
    result["body"] = [part for part in plain.split("\n\n") if part] or [""]
    return result


def project_sent(snapshot: object, mailbox_id: str) -> dict:
    if (
        type(snapshot) is not dict
        or type(snapshot.get("messages")) is not list
        or type(snapshot.get("hasMore")) is not bool
        or len(snapshot["messages"]) > 25
    ):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    messages = [project_sent_message(item, mailbox_id) for item in snapshot["messages"]]
    ids = [entry["providerMessageId"] for entry in messages]
    if len(set(ids)) != len(ids):
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    return {
        "ok": True,
        "messages": messages,
        "sentMessageIds": ids,
        "hasMore": snapshot["hasMore"],
    }
