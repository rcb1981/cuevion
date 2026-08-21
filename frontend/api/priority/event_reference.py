"""Integrity-protected references for successful semantic reply events.

The reference is intentionally stateless.  It contains provider identity and a
digest of the bounded authored text, but never provider credentials, session
cookies, or message text.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Mapping


EVENT_REFERENCE_PREFIX = "pse1"
EVENT_REFERENCE_SCHEMA_VERSION = 1
EVENT_REFERENCE_TTL_SECONDS = 14 * 24 * 60 * 60
MAX_EVENT_REFERENCE_CHARACTERS = 8_192
MAX_AUTHORED_TEXT_CHARACTERS = 12_000
PRIORITY_HMAC_SECRET_ENV = "CUEVION_PRIORITY_HMAC_SECRET"

_ACCOUNT_ID_RE = re.compile(r"(?:usr|wsp)_[A-Za-z0-9_-]{22}")
_HEX_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_HMAC_INFO = b"cuevion/priority/event-reference/v1\x00"
_CLAIM_FIELDS = frozenset(
    {
        "schemaVersion",
        "workspaceId",
        "userId",
        "mailboxId",
        "provider",
        "conversationId",
        "providerConversationId",
        "latestTurnId",
        "authoredTextDigest",
        "occurredAt",
        "issuedAt",
        "expiresAt",
        "semanticVersion",
    }
)


class EventReferenceError(Exception):
    """A fixed, value-free event-reference failure."""

    __slots__ = ("code",)

    def __init__(self, code: str = "invalid_event_ref") -> None:
        self.code = code if code in {
            "configuration_invalid",
            "invalid_event_ref",
            "stale_event_ref",
        } else "invalid_event_ref"
        Exception.__init__(self, self.code)


@dataclass(frozen=True, slots=True)
class OutgoingEventClaims:
    workspace_id: str
    user_id: str
    mailbox_id: str
    provider: str
    conversation_id: str
    provider_conversation_id: str
    latest_turn_id: str
    authored_text_digest: str
    occurred_at: int
    issued_at: int
    expires_at: int
    semantic_version: str


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    if (
        type(value) is not str
        or not value
        or len(value) > MAX_EVENT_REFERENCE_CHARACTERS
        or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None
    ):
        raise EventReferenceError()
    try:
        return base64.urlsafe_b64decode(
            (value + ("=" * (-len(value) % 4))).encode("ascii")
        )
    except (ValueError, UnicodeEncodeError):
        raise EventReferenceError() from None


def resolve_priority_hmac_secret(
    environment: Mapping[str, str] | None = None,
) -> str:
    source = os.environ if environment is None else environment
    try:
        secret = source[PRIORITY_HMAC_SECRET_ENV]
        encoded = secret.encode("utf-8", errors="strict")
    except Exception:
        raise EventReferenceError("configuration_invalid") from None
    if (
        type(secret) is not str
        or secret != secret.strip()
        or not 32 <= len(encoded) <= 4_096
    ):
        raise EventReferenceError("configuration_invalid")
    return secret


def derive_priority_hmac_key(secret: str, purpose: bytes) -> bytes:
    if type(purpose) is not bytes or not purpose or len(purpose) > 128:
        raise ValueError("invalid trusted HMAC purpose")
    try:
        encoded = secret.encode("utf-8", errors="strict")
    except Exception:
        raise EventReferenceError("configuration_invalid") from None
    if (
        type(secret) is not str
        or secret != secret.strip()
        or not 32 <= len(encoded) <= 4_096
    ):
        raise EventReferenceError("configuration_invalid")
    return hmac.new(encoded, purpose, hashlib.sha256).digest()


def canonicalize_authored_text(value: object) -> str:
    """Return the exact bounded text covered by an outgoing event reference."""
    if type(value) is not str:
        return ""
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n").strip()
    return normalized if len(normalized) <= MAX_AUTHORED_TEXT_CHARACTERS else ""


def authored_text_digest(value: object) -> str:
    normalized = canonicalize_authored_text(value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def authored_text_matches(claims: OutgoingEventClaims, value: object) -> bool:
    return bool(canonicalize_authored_text(value)) and hmac.compare_digest(
        claims.authored_text_digest,
        authored_text_digest(value),
    )


def _valid_text(value: object, maximum: int) -> bool:
    return (
        type(value) is str
        and value == value.strip()
        and 1 <= len(value) <= maximum
        and not any(
            ord(character) < 32
            or ord(character) == 127
            or unicodedata.category(character) in {"Cc", "Cf", "Cs"}
            for character in value
        )
    )


def _validate_claims(payload: object) -> OutgoingEventClaims:
    if type(payload) is not dict or set(payload) != _CLAIM_FIELDS:
        raise EventReferenceError()
    provider = payload.get("provider")
    if (
        payload.get("schemaVersion") != EVENT_REFERENCE_SCHEMA_VERSION
        or type(payload.get("workspaceId")) is not str
        or _ACCOUNT_ID_RE.fullmatch(payload["workspaceId"]) is None
        or not payload["workspaceId"].startswith("wsp_")
        or type(payload.get("userId")) is not str
        or _ACCOUNT_ID_RE.fullmatch(payload["userId"]) is None
        or not payload["userId"].startswith("usr_")
        or not _valid_text(payload.get("mailboxId"), 256)
        or provider != "google"
        or not _valid_text(payload.get("conversationId"), 2_048)
        or not _valid_text(payload.get("providerConversationId"), 1_024)
        or not _valid_text(payload.get("latestTurnId"), 1_024)
        or type(payload.get("authoredTextDigest")) is not str
        or _HEX_DIGEST_RE.fullmatch(payload["authoredTextDigest"]) is None
        or type(payload.get("occurredAt")) is not int
        or type(payload.get("issuedAt")) is not int
        or type(payload.get("expiresAt")) is not int
        or payload["occurredAt"] < 0
        or payload["issuedAt"] < 0
        or payload["expiresAt"] - payload["issuedAt"]
        != EVENT_REFERENCE_TTL_SECONDS
        or payload["occurredAt"] > payload["issuedAt"] * 1_000 + 1_000
        or not _valid_text(payload.get("semanticVersion"), 128)
    ):
        raise EventReferenceError()
    return OutgoingEventClaims(
        workspace_id=payload["workspaceId"],
        user_id=payload["userId"],
        mailbox_id=payload["mailboxId"],
        provider=payload["provider"],
        conversation_id=payload["conversationId"],
        provider_conversation_id=payload["providerConversationId"],
        latest_turn_id=payload["latestTurnId"],
        authored_text_digest=payload["authoredTextDigest"],
        occurred_at=payload["occurredAt"],
        issued_at=payload["issuedAt"],
        expires_at=payload["expiresAt"],
        semantic_version=payload["semanticVersion"],
    )


def issue_outgoing_event_reference(
    *,
    secret: str,
    workspace_id: str,
    user_id: str,
    mailbox_id: str,
    provider: str,
    conversation_id: str,
    provider_conversation_id: str,
    latest_turn_id: str,
    authored_text: object,
    occurred_at: int,
    semantic_version: str,
    now: int | None = None,
) -> str:
    issued_at = int(time.time()) if now is None else now
    normalized_text = canonicalize_authored_text(authored_text)
    if not normalized_text:
        raise EventReferenceError()
    claims = _validate_claims(
        {
            "schemaVersion": EVENT_REFERENCE_SCHEMA_VERSION,
            "workspaceId": workspace_id,
            "userId": user_id,
            "mailboxId": mailbox_id,
            "provider": provider,
            "conversationId": conversation_id,
            "providerConversationId": provider_conversation_id,
            "latestTurnId": latest_turn_id,
            "authoredTextDigest": authored_text_digest(normalized_text),
            "occurredAt": occurred_at,
            "issuedAt": issued_at,
            "expiresAt": issued_at + EVENT_REFERENCE_TTL_SECONDS,
            "semanticVersion": semantic_version,
        }
    )
    payload = {
        "schemaVersion": EVENT_REFERENCE_SCHEMA_VERSION,
        "workspaceId": claims.workspace_id,
        "userId": claims.user_id,
        "mailboxId": claims.mailbox_id,
        "provider": claims.provider,
        "conversationId": claims.conversation_id,
        "providerConversationId": claims.provider_conversation_id,
        "latestTurnId": claims.latest_turn_id,
        "authoredTextDigest": claims.authored_text_digest,
        "occurredAt": claims.occurred_at,
        "issuedAt": claims.issued_at,
        "expiresAt": claims.expires_at,
        "semanticVersion": claims.semantic_version,
    }
    encoded_payload = _base64url_encode(
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    signing_key = derive_priority_hmac_key(secret, _HMAC_INFO)
    signing_input = f"{EVENT_REFERENCE_PREFIX}.{encoded_payload}".encode("ascii")
    signature = _base64url_encode(
        hmac.new(signing_key, signing_input, hashlib.sha256).digest()
    )
    reference = f"{EVENT_REFERENCE_PREFIX}.{encoded_payload}.{signature}"
    if len(reference) > MAX_EVENT_REFERENCE_CHARACTERS:
        raise EventReferenceError()
    return reference


def verify_outgoing_event_reference(
    reference: object,
    *,
    secret: str,
    now: int | None = None,
) -> OutgoingEventClaims:
    if type(reference) is not str or len(reference) > MAX_EVENT_REFERENCE_CHARACTERS:
        raise EventReferenceError()
    parts = reference.split(".")
    if len(parts) != 3 or parts[0] != EVENT_REFERENCE_PREFIX:
        raise EventReferenceError()
    encoded_payload, encoded_signature = parts[1], parts[2]
    supplied_signature = _base64url_decode(encoded_signature)
    signing_key = derive_priority_hmac_key(secret, _HMAC_INFO)
    expected_signature = hmac.new(
        signing_key,
        f"{EVENT_REFERENCE_PREFIX}.{encoded_payload}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise EventReferenceError()
    try:
        payload = json.loads(
            _base64url_decode(encoded_payload).decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except EventReferenceError:
        raise
    except Exception:
        raise EventReferenceError() from None
    claims = _validate_claims(payload)
    current = int(time.time()) if now is None else now
    if type(current) is not int or current < 0:
        raise ValueError("invalid trusted clock")
    if claims.issued_at > current + 60 or current >= claims.expires_at:
        raise EventReferenceError("stale_event_ref")
    return claims


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EventReferenceError()
        result[key] = value
    return result


def _reject_constant(_value: str):
    raise EventReferenceError()
