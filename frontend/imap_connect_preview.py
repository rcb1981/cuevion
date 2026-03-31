import hashlib
import imaplib
import logging
import re
import time
from datetime import datetime, timezone
from email import message_from_bytes
from email.header import decode_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any


DEFAULT_GMAIL_HOST = "imap.gmail.com"
DEFAULT_GMAIL_PORT = 993
DEFAULT_FETCH_LIMIT = 20
MAX_FETCH_LIMIT = 25
logger = logging.getLogger(__name__)


def map_to_ui_signal(result: dict[str, Any]) -> str:
    priority = result.get("v7_final_priority")
    category = result.get("category")

    if priority == "PRIORITY":
        return "PRIORITY"

    if category in ["promo", "promo_reminder"]:
        return "PROMO"

    if category in ["demo", "high_priority_demo"]:
        return "DEMO"

    if category == "reply":
        return "REPLY"

    if category in [
        "workflow_update",
        "distributor_update",
        "labelradar_update",
        "trackstack_submission",
    ]:
        return "UPDATE"

    if category in [
        "business",
        "business_reminder",
    ]:
        return "BUSINESS"

    if category in [
        "finance",
        "royalty_statement",
    ]:
        return "FINANCE"

    if category == "info":
        return "INFO"

    if category == "unknown":
        return "NEW"

    return "NEW"


def map_to_stable_signal(result: dict[str, Any]) -> str | None:
    category = result.get("category")
    final_priority = result.get("v7_final_priority") or result.get("priority")

    if category not in ["finance", "royalty_statement"]:
        return None

    if final_priority == "PRIORITY":
        return "Priority"

    if final_priority == "REVIEW":
        return "For review"

    return None


def normalize_internal_classification(category: Any) -> str:
    normalized_category = str(category or "").strip().lower()

    if normalized_category in {
        "demo",
        "high_priority_demo",
        "promo",
        "promo_reminder",
        "reply",
        "workflow_update",
        "business",
        "business_reminder",
        "royalty_statement",
        "distributor_update",
        "finance",
        "info",
    }:
        return normalized_category

    return "unknown"


def decode_mime_words(value: str | None) -> str:
    if not value:
        return ""

    parts = decode_header(value)
    decoded: list[str] = []

    for part, encoding in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(encoding or "utf-8", errors="ignore"))
        else:
            decoded.append(part)

    return "".join(decoded)


def clean_text(value: str | None) -> str:
    if not value:
        return ""

    normalized = value.replace("\r\n", "\n")

    while "\n\n\n" in normalized:
        normalized = normalized.replace("\n\n\n", "\n\n")

    return normalized.strip()


def connect_mailbox_with_settings(
    host: str,
    port: int,
    username: str,
    password: str,
    ssl_enabled: bool,
):
    if ssl_enabled:
        mailbox = imaplib.IMAP4_SSL(host, port)
    else:
        mailbox = imaplib.IMAP4(host, port)

    mailbox.login(username, password)
    return mailbox


def fetch_recent_messages(mailbox, folder: str = "INBOX", limit: int = DEFAULT_FETCH_LIMIT):
    fetch_start = time.perf_counter()
    select_start = time.perf_counter()
    mailbox.select(folder)
    select_duration_ms = (time.perf_counter() - select_start) * 1000
    search_start = time.perf_counter()
    status, messages = mailbox.search(None, "ALL")
    search_duration_ms = (time.perf_counter() - search_start) * 1000

    if status != "OK":
        logger.warning(
            "IMAP preview fetch_recent_messages failed status=%s folder=%s select_ms=%.1f search_ms=%.1f",
            status,
            folder,
            select_duration_ms,
            search_duration_ms,
        )
        return []

    message_ids = messages[0].split()
    latest_ids = message_ids[-limit:]
    results: list[tuple[Message, bool, str | None]] = []

    for index, message_id in enumerate(reversed(latest_ids)):
        per_message_start = time.perf_counter()
        status, message_data = mailbox.fetch(message_id, "(UID FLAGS BODY.PEEK[])")
        fetch_duration_ms = (time.perf_counter() - per_message_start) * 1000

        if status != "OK":
            logger.warning(
                "IMAP preview per-message fetch failed idx=%s status=%s fetch_ms=%.1f",
                index,
                status,
                fetch_duration_ms,
            )
            continue

        metadata_parts: list[str] = []
        raw_email = None
        for item in message_data:
            if isinstance(item, tuple):
                response_meta = item[0]
                if isinstance(response_meta, bytes):
                    response_meta = response_meta.decode("utf-8", errors="ignore")
                else:
                    response_meta = str(response_meta)
                metadata_parts.append(response_meta)
                if len(item) > 1 and isinstance(item[1], bytes):
                    raw_email = item[1]
            elif isinstance(item, bytes):
                metadata_parts.append(item.decode("utf-8", errors="ignore"))
            elif item is not None:
                metadata_parts.append(str(item))

        combined_metadata = " ".join(metadata_parts)
        flags_match = re.search(r"FLAGS\s*\((.*?)\)", combined_metadata)
        flags_content = flags_match.group(1) if flags_match else ""
        uid_match = re.search(r"UID\s+(\d+)", combined_metadata)
        imap_uid = uid_match.group(1) if uid_match else None
        if imap_uid is None:
            uid_fetch_start = time.perf_counter()
            uid_status, uid_data = mailbox.fetch(message_id, "(UID)")
            uid_fetch_duration_ms = (time.perf_counter() - uid_fetch_start) * 1000
            if uid_status == "OK":
                uid_metadata_parts: list[str] = []
                for item in uid_data:
                    if isinstance(item, tuple):
                        response_meta = item[0]
                        if isinstance(response_meta, bytes):
                            response_meta = response_meta.decode("utf-8", errors="ignore")
                        else:
                            response_meta = str(response_meta)
                        uid_metadata_parts.append(response_meta)
                    elif isinstance(item, bytes):
                        uid_metadata_parts.append(item.decode("utf-8", errors="ignore"))
                    elif item is not None:
                        uid_metadata_parts.append(str(item))

                uid_combined_metadata = " ".join(uid_metadata_parts)
                uid_match = re.search(r"UID\s+(\d+)", uid_combined_metadata)
                if uid_match:
                    imap_uid = uid_match.group(1)
            logger.info(
                "IMAP preview UID fallback idx=%s uid_status=%s uid_fetch_ms=%.1f",
                index,
                uid_status,
                uid_fetch_duration_ms,
            )
        if not flags_match:
            logger.info(
                "IMAP fetch missing FLAGS for %s | meta=%s",
                message_id.decode("utf-8", errors="ignore")
                if isinstance(message_id, bytes)
                else str(message_id),
                combined_metadata[:160],
            )

        if raw_email is None:
            continue
        unread = "\\Seen" not in flags_content
        results.append((message_from_bytes(raw_email), unread, imap_uid))
        logger.info(
            "IMAP preview per-message fetch idx=%s fetch_ms=%.1f total_ms=%.1f bytes=%s uid_present=%s unread=%s",
            index,
            fetch_duration_ms,
            (time.perf_counter() - per_message_start) * 1000,
            len(raw_email),
            bool(imap_uid),
            unread,
        )

    logger.warning(
        "IMAP preview fetch_recent_messages complete folder=%s limit=%s returned=%s select_ms=%.1f search_ms=%.1f total_ms=%.1f",
        folder,
        limit,
        len(results),
        select_duration_ms,
        search_duration_ms,
        (time.perf_counter() - fetch_start) * 1000,
    )

    return results


def get_message_body(message: Message) -> str:
    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "")

            if "attachment" in disposition.lower():
                continue

            if content_type == "text/plain":
                payload = part.get_payload(decode=True)

                if payload is None:
                    continue

                charset = part.get_content_charset() or "utf-8"
                return clean_text(payload.decode(charset, errors="ignore"))

        for part in message.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)

                if payload is None:
                    continue

                charset = part.get_content_charset() or "utf-8"
                return clean_text(payload.decode(charset, errors="ignore"))

        return ""

    payload = message.get_payload(decode=True)

    if payload is None:
        return clean_text(str(message.get_payload() or ""))

    charset = message.get_content_charset() or "utf-8"
    return clean_text(payload.decode(charset, errors="ignore"))


def format_timestamp(date_header: str) -> tuple[str, str]:
    if not date_header:
        fallback_timestamp = datetime.now(timezone.utc).isoformat()
        return fallback_timestamp, fallback_timestamp

    try:
        parsed = parsedate_to_datetime(date_header)

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        iso_timestamp = parsed.astimezone(timezone.utc).isoformat()
        display_timestamp = parsed.strftime("%B %-d at %H:%M")
        return iso_timestamp, display_timestamp
    except Exception:
        fallback_timestamp = datetime.now(timezone.utc).isoformat()
        return fallback_timestamp, fallback_timestamp


def resolve_preview_routing(
    message: Message,
    email_address: str,
    internal_role: str | None = None,
    focus_preferences: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolve_start = time.perf_counter()
    try:
        from imap_live_v6_5_5_stable import (
            INBOX_CONFIG,
            USER_LINK_SETTINGS,
            USER_REMINDER_SETTINGS,
            V7_USER_CONFIG,
            extract_all_links,
            get_usable_demo_links,
            is_business_reminder_email,
            is_distributor_update_email,
            is_promo_reminder_email,
            is_royalty_statement_email,
            normalize_priority,
        )
        from v7_config import EngineResult
        from v7_decision_layer import decide_message_behavior
    except Exception:
        logger.exception("Could not load ui_signal dependencies for message preview")
        return {
            "ui_signal": "NEW",
            "internalClassification": "unknown",
        }

    local_part = email_address.split("@")[0].strip().lower()
    mailbox_label = f"{local_part}@"
    inbox_profile = next(
        (
            mailbox_config.get("profile", "")
            for mailbox_config in INBOX_CONFIG
            if mailbox_config.get("label", "").replace("@", "").lower() == local_part
        ),
        "",
    )

    try:
        subject = decode_mime_words(message.get("Subject", ""))
        from_header = decode_mime_words(message.get("From", ""))
        sender_name, sender_email = parseaddr(from_header)
        sender_name = decode_mime_words(sender_name)
        body = get_message_body(message)
        subject_lower = subject.lower()
        body_lower = body.lower()
        sender_lower = sender_email.lower()
        extracted_links = extract_all_links(
            body,
            "",
            subject=subject,
            artist_name=sender_name or result.get("artist"),
        )
        usable_demo_links = get_usable_demo_links(
            extracted_links=extracted_links,
            category="demo",
            user_link_settings=USER_LINK_SETTINGS,
        )
        promo_keywords = [
            "out now",
            "out soon",
            "new release",
            "new track",
            "check out my track",
            "check this release",
            "remix",
            "bootleg",
            "radio support",
            "dj support",
            "playlist support",
            "support this track",
            "support the release",
            "play in your sets",
        ]
        business_keywords = [
            "collaboration",
            "samenwerken",
            "proposal",
            "partnership",
            "business call",
            "let's work together",
            "work together",
            "discuss a proposal",
            "schedule a call",
            "meeting",
            "opportunity",
        ]
        demo_intent_keywords = [
            "demo",
            "demo submission",
            "please consider this track",
            "for your label",
            "would love your feedback",
            "submission",
            "listen to this track",
            "check my track",
            "track for your label",
        ]
        finance_keywords = [
            "invoice",
            "receipt",
            "payment",
            "billing",
            "paid",
            "payout",
            "statement",
            "royalty",
            "royalties",
            "earnings",
            "ad receipt",
            "ads receipt",
            "payment confirmation",
            "factuur",
            "betaling",
            "betalingsoverzicht",
            "declaratie",
        ]
        meta_ads_keywords = [
            "advertentie",
            "advertenties",
            "ad approved",
            "campaign approved",
            "ad account",
            "ads account",
            "campaign",
            "meta ads",
            "facebook ads",
            "advertising account",
        ]
        google_security_keywords = [
            "security alert",
            "security notification",
            "new sign-in",
            "sign-in attempt",
            "login attempt",
            "log in",
            "password",
            "verification code",
            "2-step verification",
            "two-step verification",
            "suspicious activity",
        ]
        classification_text = f"{subject_lower} {body_lower}"
        result = {
            "category": "info",
            "priority": "LOW",
            "reason": "Preview fast-path rules",
            "workflow_links": [
                link_name
                for link_name in ["soundcloud", "dropbox", "wetransfer", "disco", "gdrive", "onedrive"]
                if extracted_links.get(link_name)
            ],
            "usable_demo_links": [],
        }

        if (
            message.get("In-Reply-To")
            or message.get("References")
            or subject_lower.startswith("re:")
        ):
            result["category"] = "reply"
        elif "trackstack" in sender_lower or "trackstack" in subject_lower or "trackstack" in body_lower:
            result["category"] = "trackstack_submission"
        elif "labelradar" in sender_lower or "labelradar" in subject_lower or "labelradar" in body_lower:
            result["category"] = "labelradar_update"
        elif is_distributor_update_email(subject, body, sender_email):
            result["category"] = "distributor_update"
        elif is_business_reminder_email(subject, body, sender_email):
            result["category"] = "business_reminder"
        elif is_royalty_statement_email(subject, body, sender_email):
            result["category"] = "royalty_statement"
        elif is_promo_reminder_email(subject, body, sender_email):
            result["category"] = "promo_reminder"
        elif (
            ("google" in sender_lower or "accounts.google.com" in classification_text)
            and any(keyword in classification_text for keyword in google_security_keywords)
        ):
            result["category"] = "info"
        elif any(keyword in classification_text for keyword in finance_keywords):
            result["category"] = "finance"
        elif usable_demo_links:
            result["category"] = "demo"
            result["usable_demo_links"] = usable_demo_links
        elif any(keyword in classification_text for keyword in demo_intent_keywords):
            result["category"] = "demo"
        elif any(keyword in classification_text for keyword in meta_ads_keywords):
            result["category"] = "finance"
        elif any(keyword in classification_text for keyword in promo_keywords):
            result["category"] = "promo"
        elif any(keyword in classification_text for keyword in business_keywords):
            result["category"] = "business"

        result = normalize_priority(
            result,
            inbox_profile=inbox_profile,
            user_reminder_settings=USER_REMINDER_SETTINGS,
        )

        mailbox_match = next(
            (
                mailbox
                for mailbox in V7_USER_CONFIG.mailboxes
                if mailbox.email_address.split("@")[0].lower() == local_part
            ),
            None,
        )

        if mailbox_match:
            engine_result = EngineResult(
                inbox_name=mailbox_label,
                category=result.get("category", "unknown"),
                priority=result.get("priority", "NORMAL"),
                workflow_links=result.get("workflow_links", []),
                usable_demo_links=result.get("usable_demo_links", []),
                reason=result.get("reason", ""),
            )

            v7_decision = decide_message_behavior(
                engine_result=engine_result,
                user_config=V7_USER_CONFIG,
                mailbox_config=mailbox_match,
                internal_role=internal_role,
                focus_preferences=focus_preferences,
            )

            result["v7_final_priority"] = v7_decision.final_priority
            result["final_visibility"] = v7_decision.final_visibility
            result["action"] = v7_decision.action

        result["internalClassification"] = normalize_internal_classification(
            result.get("category"),
        )
        result["ui_signal"] = result.get("ui_signal") or map_to_ui_signal(result)
        result["signal"] = map_to_stable_signal(result)

        logger.warning(
            "Preview ui_signal resolved category=%s priority=%s workflow_links=%s usable_demo_links=%s analyze_ms=%.1f total_ms=%.1f subject=%s",
            result.get("category"),
            result.get("priority"),
            result.get("workflow_links"),
            result.get("usable_demo_links"),
            0.0,
            (time.perf_counter() - resolve_start) * 1000,
            decode_mime_words(message.get("Subject", "")),
        )
        return result
    except Exception:
        logger.exception(
            "Could not resolve ui_signal for message preview after %.1f ms",
            (time.perf_counter() - resolve_start) * 1000,
        )
        return {
            "ui_signal": "NEW",
            "internalClassification": "unknown",
        }


def resolve_ui_signal(
    message: Message,
    email_address: str,
    internal_role: str | None = None,
    focus_preferences: dict[str, Any] | None = None,
) -> str:
    return str(
        resolve_preview_routing(
            message,
            email_address,
            internal_role=internal_role,
            focus_preferences=focus_preferences,
        ).get("ui_signal")
        or "NEW"
    )


def to_message_preview(
    message: Message,
    index: int,
    email_address: str,
    unread: bool,
    imap_uid: str | None,
    internal_role: str | None = None,
    focus_preferences: dict[str, Any] | None = None,
) -> dict[str, Any]:
    subject = decode_mime_words(message.get("Subject", "Untitled message"))
    from_header = decode_mime_words(message.get("From", "Unknown sender"))
    to_header = decode_mime_words(message.get("To", ""))
    cc_header = decode_mime_words(message.get("Cc", ""))
    sender_name, sender_email = parseaddr(from_header)
    body = get_message_body(message)
    snippet = clean_text(body.replace("\n", " "))[:220]
    created_at, display_timestamp = format_timestamp(message.get("Date", ""))
    stable_id_source = f"{subject}|{from_header}|{display_timestamp}"
    message_id = (
        message.get("Message-Id")
        or (f"imap-uid-{imap_uid}" if imap_uid else None)
        or hashlib.sha1(stable_id_source.encode("utf-8")).hexdigest()
    )
    preview_routing = resolve_preview_routing(
        message,
        email_address,
        internal_role=internal_role,
        focus_preferences=focus_preferences,
    )

    return {
      "id": message_id.strip("<>"),
      "sender": sender_name or sender_email or from_header,
      "subject": subject,
      "snippet": snippet,
      "from": from_header,
      "to": to_header,
      "cc": cc_header,
      "timestamp": display_timestamp,
      "createdAt": created_at,
      "body": body.split("\n\n") if body else [snippet or "No message preview available."],
      "unread": unread,
      "imapUid": imap_uid,
      "signal": preview_routing.get("signal"),
      "ui_signal": preview_routing.get("ui_signal"),
      "internalClassification": preview_routing.get("internalClassification"),
      "final_visibility": preview_routing.get("final_visibility"),
      "action": preview_routing.get("action"),
      "v7_final_priority": preview_routing.get("v7_final_priority"),
      "category": preview_routing.get("category"),
    }


def build_connect_preview_response(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request_start = time.perf_counter()
    provider = payload.get("provider")
    email_address = str(payload.get("email") or "").strip()
    password = str(payload.get("password") or "")
    host = str(payload.get("host") or "").strip()
    port = int(payload.get("port") or 0)
    ssl_enabled = bool(payload.get("ssl", True))
    username = str(payload.get("username") or "").strip() or email_address
    internal_role = payload.get("internalRole", None)
    focus_preferences = payload.get("focusPreferences", None)
    folder = str(payload.get("folder") or "INBOX").strip() or "INBOX"
    limit = max(1, min(int(payload.get("limit") or DEFAULT_FETCH_LIMIT), MAX_FETCH_LIMIT))

    if provider == "google":
        host = host or DEFAULT_GMAIL_HOST
        port = port or DEFAULT_GMAIL_PORT
        ssl_enabled = True
        username = email_address

    if not email_address or not password:
        return 400, {
            "ok": False,
            "error": {
                "code": "invalid_request",
                "message": "Email and password are required",
            },
        }

    if not host or port <= 0:
        return 400, {
            "ok": False,
            "error": {
                "code": "invalid_request",
                "message": "Host and port are required",
            },
        }

    mailbox = None

    try:
        connect_start = time.perf_counter()
        mailbox = connect_mailbox_with_settings(
            host=host,
            port=port,
            username=username,
            password=password,
            ssl_enabled=ssl_enabled,
        )
        connect_duration_ms = (time.perf_counter() - connect_start) * 1000
        fetch_start = time.perf_counter()
        messages = fetch_recent_messages(mailbox, folder=folder, limit=limit)
        fetch_duration_ms = (time.perf_counter() - fetch_start) * 1000
        preview_build_start = time.perf_counter()
        previews = [
            to_message_preview(
                message,
                index,
                email_address,
                unread,
                imap_uid,
                internal_role=internal_role,
                focus_preferences=focus_preferences,
            )
            for index, (message, unread, imap_uid) in enumerate(messages)
        ]
        preview_build_duration_ms = (time.perf_counter() - preview_build_start) * 1000
        logger.warning(
            "IMAP preview request complete email=%s folder=%s limit=%s connect_ms=%.1f fetch_ms=%.1f preview_build_ms=%.1f total_ms=%.1f messages=%s",
            email_address,
            folder,
            limit,
            connect_duration_ms,
            fetch_duration_ms,
            preview_build_duration_ms,
            (time.perf_counter() - request_start) * 1000,
            len(previews),
        )

        return 200, {
            "ok": True,
            "messages": previews,
        }
    except imaplib.IMAP4.error as exc:
        logger.exception(
            "IMAP connection failed with IMAP4 error",
            extra={
                "imap_host": host,
                "imap_port": port,
                "imap_ssl_enabled": ssl_enabled,
                "imap_error_message": str(exc),
            },
        )
        return 400, {
            "ok": False,
            "error": {
                "code": "invalid_credentials",
                "message": str(exc),
            },
        }
    except Exception as exc:
        logger.exception(
            "IMAP connection failed with unexpected error",
            extra={
                "imap_host": host,
                "imap_port": port,
                "imap_ssl_enabled": ssl_enabled,
                "imap_error_message": str(exc),
            },
        )
        return 400, {
            "ok": False,
            "error": {
                "code": "connection_failed",
                "message": str(exc),
            },
        }
    finally:
        if mailbox is not None:
            try:
                mailbox.logout()
            except Exception:
                pass
