from __future__ import annotations

import importlib as _identity_importlib
import sys as _identity_sys

_CANONICAL_MODULE_NAME = "api.collaboration.models"
_LEGACY_MODULE_NAME = "models"
_FORWARD_MARKER = "_cuevion_forward_to_canonical_module"

if __name__ == _LEGACY_MODULE_NAME:
    _identity_sys.modules[__name__].__dict__[_FORWARD_MARKER] = (
        _CANONICAL_MODULE_NAME
    )
    _canonical_module = _identity_importlib.import_module(_CANONICAL_MODULE_NAME)
    _identity_sys.modules[_LEGACY_MODULE_NAME] = _canonical_module
elif __name__ != _CANONICAL_MODULE_NAME:
    raise ImportError(
        "Collaboration helpers must be imported as " + _CANONICAL_MODULE_NAME
    )
else:
    _legacy_module = _identity_sys.modules.get(_LEGACY_MODULE_NAME)
    if (
        _legacy_module is not None
        and _legacy_module is not _identity_sys.modules[__name__]
        and getattr(_legacy_module, _FORWARD_MARKER, None)
        != _CANONICAL_MODULE_NAME
    ):
        raise ImportError("canonical and legacy model identities cannot coexist")
    _identity_sys.modules[_LEGACY_MODULE_NAME] = _identity_sys.modules[__name__]

    import hashlib
    import json
    import re
    import secrets
    import time
    import unicodedata
    from typing import Any

    COLLABORATION_THREAD_SCHEMA_VERSION = 1

    # Collaboration v2 is intentionally separate from the active v1 route model.  These
    # limits are also enforced after JSON serialization so a collection of individually
    # valid fields cannot produce an unbounded record.
    COLLABORATION_V2_THREAD_SCHEMA_VERSION = 2
    COLLABORATION_V2_INVITE_SCHEMA_VERSION = 2
    MAX_V2_THREAD_BYTES = 262_144
    MAX_V2_INVITE_BYTES = 16_384
    MAX_V2_MESSAGES = 500
    MAX_V2_EXPLICIT_PARTICIPANTS = 15
    MAX_V2_MESSAGE_TEXT = 16_384
    MAX_V2_SOURCE_BODY = 131_072
    MAX_V2_INVITE_LIFETIME_SECONDS = 24 * 60 * 60
    MAX_V2_GUEST_SESSION_LIFETIME_SECONDS = 8 * 60 * 60
    MAX_V2_SAFE_INTEGER = (2**53) - 1
    MIN_V2_TIMESTAMP_SECONDS = 1_577_836_800  # 2020-01-01T00:00:00Z
    MAX_V2_TIMESTAMP_SECONDS = 4_102_444_800  # 2100-01-01T00:00:00Z
    MIN_V2_TIMESTAMP_MILLISECONDS = MIN_V2_TIMESTAMP_SECONDS * 1000
    MAX_V2_TIMESTAMP_MILLISECONDS = (MAX_V2_TIMESTAMP_SECONDS * 1000) + 999
    _V2_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$")
    _V2_WORKSPACE_ID_RE = re.compile(r"^wsp_[A-Za-z0-9_-]{22}$")
    _V2_USER_ID_RE = re.compile(r"^usr_[A-Za-z0-9_-]{21}[AQgw]$")
    _V2_TEAM_MEMBERSHIP_REF_RE = re.compile(r"^tinv_[A-Za-z0-9_-]{1,64}$")
    _V2_EMAIL_RE = re.compile(
        r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
        r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
    )
    COLLABORATION_V2_SAFE_ERROR_CODES = frozenset(
        {
            "auth_required",
            "forbidden",
            "mailbox_not_found",
            "collaboration_not_found",
            "source_not_found",
            "source_changed",
            "invite_not_found",
            "invite_expired",
            "invite_revoked",
            "invite_already_exchanged",
            "session_not_found",
            "session_expired",
            "session_revoked",
            "csrf_failed",
            "origin_rejected",
            "invalid_request",
            "stale_thread",
            "idempotency_conflict",
            "stale_invitation",
            "storage_unavailable",
            "storage_protocol_error",
            "index_hmac_unavailable",
            "provider_unavailable",
            "atomic_exchange_unavailable",
            "already_revoked",
            "already_logged_out",
            "internal_error",
        }
    )
    _V2_OWNER_IDEMPOTENCY_KEY_RE = re.compile(
        r"^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$"
    )


    def _normalize_string(value: Any) -> str | None:
        if not isinstance(value, str):
            return None

        normalized = value.strip()
        return normalized or None


    def _normalize_email(value: Any) -> str | None:
        normalized = _normalize_string(value)
        return normalized.lower() if normalized else None


    def _normalize_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None

        if isinstance(value, int):
            return value

        if isinstance(value, float) and value.is_integer():
            return int(value)

        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                return int(stripped)
            except ValueError:
                return None

        return None


    def _normalize_bool(value: Any) -> bool:
        return value is True


    def _normalize_string_list(value: Any) -> list[str] | None:
        if not isinstance(value, list):
            return None

        normalized_values: list[str] = []
        for entry in value:
            normalized_entry = _normalize_string(entry)
            if normalized_entry is None:
                return None
            normalized_values.append(normalized_entry)

        return normalized_values


    def normalize_collaboration_mention_record(value: Any) -> dict | None:
        if not isinstance(value, dict):
            return None

        mention_id = _normalize_string(value.get("id"))
        name = _normalize_string(value.get("name"))
        email = _normalize_email(value.get("email"))
        handle = _normalize_string(value.get("handle"))

        if not mention_id or not name or not email or not handle:
            return None

        return {
            "id": mention_id,
            "name": name,
            "email": email,
            "handle": handle,
            "notify": _normalize_bool(value.get("notify")),
        }


    def normalize_collaboration_message_record(value: Any) -> dict | None:
        if not isinstance(value, dict):
            return None

        message_id = _normalize_string(value.get("id"))
        author_id = _normalize_string(value.get("authorId"))
        author_name = _normalize_string(value.get("authorName"))
        text = _normalize_string(value.get("text"))
        timestamp = _normalize_int(value.get("timestamp"))

        if not message_id or not author_id or not author_name or text is None or timestamp is None:
            return None

        visibility = _normalize_string(value.get("visibility"))
        if visibility not in {"internal", "shared", None}:
            visibility = None

        mentions = value.get("mentions")
        normalized_mentions: list[dict] = []
        if isinstance(mentions, list):
            for mention in mentions:
                normalized_mention = normalize_collaboration_mention_record(mention)
                if normalized_mention:
                    normalized_mentions.append(normalized_mention)

        normalized_message = {
            "id": message_id,
            "authorId": author_id,
            "authorName": author_name,
            "text": text,
            "timestamp": timestamp,
        }

        if visibility:
            normalized_message["visibility"] = visibility

        if normalized_mentions:
            normalized_message["mentions"] = normalized_mentions

        return normalized_message


    def normalize_collaboration_participant_record(value: Any) -> dict | None:
        if not isinstance(value, dict):
            return None

        participant_id = _normalize_string(value.get("id"))
        name = _normalize_string(value.get("name"))
        email = _normalize_email(value.get("email"))
        kind = _normalize_string(value.get("kind"))
        status = _normalize_string(value.get("status"))

        if (
            not participant_id
            or not name
            or not email
            or kind not in {"internal", "external"}
            or status not in {"active", "invited", "declined"}
        ):
            return None

        normalized_participant = {
            "id": participant_id,
            "name": name,
            "email": email,
            "kind": kind,
            "status": status,
        }

        external_review_token = _normalize_string(value.get("externalReviewToken"))
        if external_review_token:
            normalized_participant["externalReviewToken"] = external_review_token

        return normalized_participant


    def normalize_collaboration_record(value: Any) -> dict | None:
        if not isinstance(value, dict):
            return None

        state = _normalize_string(value.get("state"))
        requested_by = _normalize_string(value.get("requestedBy"))
        requested_user_id = _normalize_string(value.get("requestedUserId"))
        requested_user_name = _normalize_string(value.get("requestedUserName"))
        created_at = _normalize_int(value.get("createdAt"))
        updated_at = _normalize_int(value.get("updatedAt"))
        messages = value.get("messages")

        if (
            state not in {"needs_review", "needs_action", "note_only", "resolved"}
            or not requested_by
            or not requested_user_id
            or not requested_user_name
            or created_at is None
            or updated_at is None
            or not isinstance(messages, list)
        ):
            return None

        normalized_messages: list[dict] = []
        for message in messages:
            normalized_message = normalize_collaboration_message_record(message)
            if normalized_message:
                normalized_messages.append(normalized_message)

        if len(normalized_messages) != len(messages):
            return None

        participants = value.get("participants")
        normalized_participants: list[dict] = []
        if isinstance(participants, list):
            for participant in participants:
                normalized_participant = normalize_collaboration_participant_record(participant)
                if normalized_participant:
                    normalized_participants.append(normalized_participant)

        normalized_collaboration = {
            "state": state,
            "requestedBy": requested_by,
            "requestedUserId": requested_user_id,
            "requestedUserName": requested_user_name,
            "createdAt": created_at,
            "updatedAt": updated_at,
            "participants": normalized_participants,
            "messages": normalized_messages,
        }

        resolved_at = _normalize_int(value.get("resolvedAt"))
        if resolved_at is not None:
            normalized_collaboration["resolvedAt"] = resolved_at

        resolved_by_user_id = _normalize_string(value.get("resolvedByUserId"))
        if resolved_by_user_id:
            normalized_collaboration["resolvedByUserId"] = resolved_by_user_id

        resolved_by_user_name = _normalize_string(value.get("resolvedByUserName"))
        if resolved_by_user_name:
            normalized_collaboration["resolvedByUserName"] = resolved_by_user_name

        preview_text = _normalize_string(value.get("previewText"))
        if preview_text:
            normalized_collaboration["previewText"] = preview_text

        return normalized_collaboration


    def normalize_source_message_snapshot(value: Any) -> dict | None:
        if not isinstance(value, dict):
            return None

        message_id = _normalize_string(value.get("id"))
        subject = _normalize_string(value.get("subject"))
        sender = _normalize_string(value.get("sender"))
        from_value = _normalize_string(value.get("from"))
        timestamp = _normalize_string(value.get("timestamp"))
        snippet = _normalize_string(value.get("snippet"))
        body = _normalize_string_list(value.get("body"))

        if (
            not message_id
            or subject is None
            or sender is None
            or from_value is None
            or timestamp is None
            or snippet is None
            or body is None
        ):
            return None

        normalized_source_message = {
            "id": message_id,
            "subject": subject,
            "sender": sender,
            "from": from_value,
            "timestamp": timestamp,
            "snippet": snippet,
            "body": body,
        }

        body_html = _normalize_string(value.get("bodyHtml"))
        if body_html:
            normalized_source_message["bodyHtml"] = body_html

        return normalized_source_message


    def normalize_collaboration_thread_record(value: Any) -> dict | None:
        if not isinstance(value, dict):
            return None

        version = _normalize_int(value.get("v"))
        workspace_id = _normalize_string(value.get("workspaceId"))
        mailbox_id = _normalize_string(value.get("mailboxId"))
        message_id = _normalize_string(value.get("messageId"))
        source_message = normalize_source_message_snapshot(value.get("sourceMessage"))
        collaboration = normalize_collaboration_record(value.get("collaboration"))

        if (
            version != COLLABORATION_THREAD_SCHEMA_VERSION
            or not workspace_id
            or not mailbox_id
            or not message_id
            or source_message is None
            or collaboration is None
        ):
            return None

        return {
            "v": COLLABORATION_THREAD_SCHEMA_VERSION,
            "workspaceId": workspace_id,
            "mailboxId": mailbox_id,
            "messageId": message_id,
            "sourceMessage": source_message,
            "isShared": _normalize_bool(value.get("isShared")),
            "collaboration": collaboration,
        }


    def normalize_collaboration_invite_record(value: Any) -> dict | None:
        if not isinstance(value, dict):
            return None

        version = _normalize_int(value.get("v"))
        token = _normalize_string(value.get("token"))
        workspace_id = _normalize_string(value.get("workspaceId"))
        mailbox_id = _normalize_string(value.get("mailboxId"))
        message_id = _normalize_string(value.get("messageId"))
        invitee_email = _normalize_email(value.get("inviteeEmail"))
        participant_id = _normalize_string(value.get("participantId"))
        status = _normalize_string(value.get("status"))
        created_at = _normalize_int(value.get("createdAt"))
        updated_at = _normalize_int(value.get("updatedAt"))
        created_by_user_id = _normalize_string(value.get("createdByUserId"))
        created_by_user_name = _normalize_string(value.get("createdByUserName"))

        if (
            version != COLLABORATION_THREAD_SCHEMA_VERSION
            or not token
            or not workspace_id
            or not mailbox_id
            or not message_id
            or not invitee_email
            or not participant_id
            or status not in {"active", "revoked", "expired"}
            or created_at is None
            or updated_at is None
            or not created_by_user_id
            or not created_by_user_name
        ):
            return None

        normalized_invite = {
            "v": COLLABORATION_THREAD_SCHEMA_VERSION,
            "token": token,
            "workspaceId": workspace_id,
            "mailboxId": mailbox_id,
            "messageId": message_id,
            "inviteeEmail": invitee_email,
            "participantId": participant_id,
            "status": status,
            "createdAt": created_at,
            "updatedAt": updated_at,
            "createdByUserId": created_by_user_id,
            "createdByUserName": created_by_user_name,
        }

        expires_at = _normalize_int(value.get("expiresAt"))
        if expires_at is not None:
            normalized_invite["expiresAt"] = expires_at

        return normalized_invite


    def is_active_collaboration_invite_record(value: Any) -> bool:
        normalized_invite = normalize_collaboration_invite_record(value)
        return bool(normalized_invite and normalized_invite["status"] == "active")


    def build_external_collaboration_thread_view(value: Any) -> dict | None:
        normalized_thread = normalize_collaboration_thread_record(value)
        if not normalized_thread:
            return None

        external_participants: list[dict] = []
        for participant in normalized_thread["collaboration"]["participants"]:
            external_participants.append(
                {
                    key: participant_value
                    for key, participant_value in participant.items()
                    if key != "externalReviewToken"
                }
            )

        external_messages = [
            message
            for message in normalized_thread["collaboration"]["messages"]
            if message.get("visibility") in {None, "shared"}
        ]

        return {
            **normalized_thread,
            "collaboration": {
                **normalized_thread["collaboration"],
                "participants": external_participants,
                "messages": external_messages,
            },
        }


    # --- Inactive Collaboration v2 model -------------------------------------


    def _v2_bounded_string(
        value: Any,
        *,
        max_length: int,
        allow_empty: bool = False,
    ) -> str | None:
        if (
            not isinstance(value, str)
            or value != value.strip()
            or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value)
        ):
            return None
        if (not value and not allow_empty) or len(value.encode("utf-8")) > max_length:
            return None
        return value


    def _v2_free_text(value: Any, *, max_length: int) -> str | None:
        """Preserve ordinary message line breaks while rejecting hidden controls."""
        if not isinstance(value, str):
            return None
        for character in value:
            category = unicodedata.category(character)
            if category in {"Cf", "Cs"} or (category == "Cc" and character not in {"\n", "\r", "\t"}):
                return None
        return value if len(value.encode("utf-8")) <= max_length else None


    def _v2_ascii_identifier(value: Any, *, max_length: int) -> str | None:
        normalized = _v2_bounded_string(value, max_length=max_length)
        return normalized if normalized is not None and normalized.isascii() else None


    def _v2_mailbox_id(value: Any) -> str | None:
        normalized = _v2_ascii_identifier(value, max_length=256)
        if normalized is None or not re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,255}", normalized):
            return None
        return normalized


    def _v2_exact_keys(value: dict, required: set[str], optional: set[str] | None = None) -> bool:
        keys = set(value)
        return required <= keys and keys <= required | (optional or set())


    _V2_CANONICAL_WIRE_UINT_RE = re.compile(r"(?:0|[1-9][0-9]*)\Z")
    _V2_WIRE_INTEGER_FIELDS = {
        "thread": ("v", "createdAt", "updatedAt"),
        "invite": (
            "v",
            "createdAt",
            "expiresAt",
            "exchangeCount",
            "exchangedAt",
            "revokedAt",
        ),
        "session": (
            "v",
            "createdAt",
            "lastUsedAt",
            "expiresAt",
            "revokedAt",
            "loggedOutAt",
        ),
    }


    def _v2_copy_json_value(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: _v2_copy_json_value(entry) for key, entry in value.items()}
        if isinstance(value, list):
            return [_v2_copy_json_value(entry) for entry in value]
        return value


    def _v2_wire_uint_to_int(value: Any) -> int | None:
        if (
            not isinstance(value, str)
            or len(value) > 16
            or not _V2_CANONICAL_WIRE_UINT_RE.fullmatch(value)
        ):
            return None
        parsed = int(value)
        return parsed if parsed <= MAX_V2_SAFE_INTEGER else None


    def encode_v2_wire_record(value: Any, record_kind: str) -> dict | None:
        """Encode typed v2 records for Redis without losing integer token identity.

        Application records use exact Python ``int`` values. Redis wire records use
        canonical unsigned decimal strings for every schema integer, so Lua never
        relies on cjson's lossy JSON-number decoding for security decisions.
        """
        fields = _V2_WIRE_INTEGER_FIELDS.get(record_kind)
        if not isinstance(value, dict) or fields is None:
            return None
        encoded = _v2_copy_json_value(value)
        for field in fields:
            if field not in encoded:
                return None
            entry = encoded[field]
            if entry is None and field in {"exchangedAt", "revokedAt", "loggedOutAt"}:
                continue
            if type(entry) is not int or not 0 <= entry <= MAX_V2_SAFE_INTEGER:
                return None
            encoded[field] = str(entry)
        if record_kind == "thread":
            messages = encoded.get("messages")
            if not isinstance(messages, list):
                return None
            for message in messages:
                if not isinstance(message, dict) or type(message.get("createdAt")) is not int:
                    return None
                created_at = message["createdAt"]
                if not 0 <= created_at <= MAX_V2_SAFE_INTEGER:
                    return None
                message["createdAt"] = str(created_at)
        return encoded


    def decode_v2_wire_record(value: Any, record_kind: str) -> dict | None:
        """Decode only the canonical Redis integer representation.

        Raw JSON numbers, floats/exponents, booleans, signed zero, whitespace,
        leading zeroes, and values above JavaScript's safe-integer ceiling all fail.
        """
        fields = _V2_WIRE_INTEGER_FIELDS.get(record_kind)
        if not isinstance(value, dict) or fields is None:
            return None
        decoded = _v2_copy_json_value(value)
        for field in fields:
            if field not in decoded:
                return None
            entry = decoded[field]
            if entry is None and field in {"exchangedAt", "revokedAt", "loggedOutAt"}:
                continue
            parsed = _v2_wire_uint_to_int(entry)
            if parsed is None:
                return None
            decoded[field] = parsed
        if record_kind == "thread":
            messages = decoded.get("messages")
            if not isinstance(messages, list):
                return None
            for message in messages:
                if not isinstance(message, dict):
                    return None
                parsed = _v2_wire_uint_to_int(message.get("createdAt"))
                if parsed is None:
                    return None
                message["createdAt"] = parsed
        return decoded


    def _v2_nonnegative_int(value: Any) -> int | None:
        return value if type(value) is int and 0 <= value <= MAX_V2_SAFE_INTEGER else None


    def _v2_timestamp_seconds(value: Any) -> int | None:
        return value if type(value) is int and MIN_V2_TIMESTAMP_SECONDS <= value <= MAX_V2_TIMESTAMP_SECONDS else None


    def _v2_timestamp_milliseconds(value: Any) -> int | None:
        return value if type(value) is int and MIN_V2_TIMESTAMP_MILLISECONDS <= value <= MAX_V2_TIMESTAMP_MILLISECONDS else None


    def _v2_positive_decimal_string(value: Any) -> str | None:
        if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value):
            return None
        return value


    def _v2_json_is_bounded(value: dict, max_bytes: int, *, record_kind: str) -> bool:
        try:
            wire_value = encode_v2_wire_record(value, record_kind)
            if wire_value is None:
                return False
            return len(
                json.dumps(
                    wire_value,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ) <= max_bytes
        except (TypeError, ValueError, OverflowError, UnicodeEncodeError, RecursionError):
            return False


    def normalize_v2_email(value: Any) -> str | None:
        normalized = _v2_bounded_string(value, max_length=320)
        if (
            not normalized
            or not normalized.isascii()
            or not _V2_EMAIL_RE.fullmatch(normalized)
            or len(normalized.rsplit("@", 1)[0]) > 64
            or len(normalized.rsplit("@", 1)[1]) > 253
        ):
            return None
        return normalized.lower()


    def normalize_v2_workspace_id(value: Any) -> str | None:
        """Accept only the canonical Auth0 account-authority workspace identity."""

        return (
            value
            if isinstance(value, str)
            and value.isascii()
            and _V2_WORKSPACE_ID_RE.fullmatch(value) is not None
            else None
        )


    def normalize_v2_user_id(value: Any) -> str | None:
        """Accept one canonical unpadded base64url Cuevion user identifier."""

        return (
            value
            if type(value) is str
            and value.isascii()
            and _V2_USER_ID_RE.fullmatch(value) is not None
            else None
        )


    def normalize_v2_team_membership_ref(value: Any) -> str | None:
        return (
            value
            if type(value) is str
            and value.isascii()
            and _V2_TEAM_MEMBERSHIP_REF_RE.fullmatch(value) is not None
            else None
        )


    def normalize_v2_participant_authority(value: Any) -> dict | None:
        required = {"userId", "membershipRef", "displayName"}
        if not isinstance(value, dict) or not _v2_exact_keys(value, required):
            return None
        user_id = normalize_v2_user_id(value.get("userId"))
        membership_ref = normalize_v2_team_membership_ref(
            value.get("membershipRef")
        )
        display_name = _v2_bounded_string(value.get("displayName"), max_length=256)
        if user_id is None or membership_ref is None or display_name is None:
            return None
        return {
            "userId": user_id,
            "membershipRef": membership_ref,
            "displayName": display_name,
        }


    def is_v2_opaque_id(value: Any) -> bool:
        return isinstance(value, str) and bool(_V2_OPAQUE_ID_RE.fullmatch(value))


    def generate_v2_opaque_id(byte_count: int = 16) -> str:
        if byte_count < 16:
            raise ValueError("v2 opaque identifiers require at least 128 bits")
        return secrets.token_urlsafe(byte_count)


    def normalize_v2_owner_idempotency_key(value: Any) -> str | None:
        """Accept only canonical unpadded base64url encoding of 256 bits."""

        return (
            value
            if type(value) is str
            and _V2_OWNER_IDEMPOTENCY_KEY_RE.fullmatch(value) is not None
            else None
        )


    def generate_v2_bearer_secret(byte_count: int = 32) -> str:
        if byte_count < 32:
            raise ValueError("v2 bearer secrets require at least 256 bits")
        return secrets.token_urlsafe(byte_count)


    def hash_v2_secret(raw_secret: Any) -> str | None:
        if not isinstance(raw_secret, str) or not raw_secret or len(raw_secret) > 1024:
            return None
        return hashlib.sha256(raw_secret.encode("utf-8")).hexdigest()


    def normalize_v2_source_ref(value: Any) -> dict | None:
        if not isinstance(value, dict):
            return None
        provider = _v2_bounded_string(value.get("provider"), max_length=32)
        if provider == "google":
            if not _v2_exact_keys(value, {"provider", "providerMessageId"}):
                return None
            provider_message_id = _v2_ascii_identifier(
                value.get("providerMessageId"), max_length=512
            )
            return (
                {"provider": "google", "providerMessageId": provider_message_id}
                if provider_message_id
                else None
            )
        if provider == "custom_imap":
            if not _v2_exact_keys(
                value, {"provider", "folder", "uidValidity", "imapUid"}
            ):
                return None
            folder = _v2_bounded_string(value.get("folder"), max_length=255)
            uid_validity = _v2_positive_decimal_string(value.get("uidValidity"))
            imap_uid = _v2_positive_decimal_string(value.get("imapUid"))
            if folder != "INBOX" or uid_validity is None or imap_uid is None:
                return None
            return {
                "provider": "custom_imap",
                "folder": "INBOX",
                "uidValidity": uid_validity,
                "imapUid": imap_uid,
            }
        return None


    def normalize_v2_source_message(value: Any) -> dict | None:
        required = {"subject", "senderDisplay", "fromDisplay", "timestamp", "bodyText"}
        if not isinstance(value, dict) or not _v2_exact_keys(value, required):
            return None
        normalized = {
            "subject": _v2_bounded_string(value.get("subject"), max_length=998, allow_empty=True),
            "senderDisplay": _v2_bounded_string(
                value.get("senderDisplay"), max_length=512, allow_empty=True
            ),
            "fromDisplay": _v2_bounded_string(
                value.get("fromDisplay"), max_length=512, allow_empty=True
            ),
            "timestamp": _v2_bounded_string(
                value.get("timestamp"), max_length=128, allow_empty=True
            ),
            "bodyText": _v2_free_text(value.get("bodyText"), max_length=MAX_V2_SOURCE_BODY),
        }
        return normalized if all(entry is not None for entry in normalized.values()) else None


    def normalize_v2_message_record(value: Any) -> dict | None:
        required = {"id", "authorKind", "authorDisplayName", "text", "visibility", "createdAt"}
        if not isinstance(value, dict) or not _v2_exact_keys(value, required):
            return None
        message_id = value.get("id")
        author_kind = _v2_bounded_string(value.get("authorKind"), max_length=16)
        author_display_name = _v2_bounded_string(
            value.get("authorDisplayName"), max_length=256
        )
        text = _v2_free_text(value.get("text"), max_length=MAX_V2_MESSAGE_TEXT)
        created_at = _v2_timestamp_milliseconds(value.get("createdAt"))
        visibility = _v2_bounded_string(value.get("visibility"), max_length=16)
        if (
            not is_v2_opaque_id(message_id)
            or author_kind not in {"owner", "internal", "guest", "system"}
            or not author_display_name
            or text is None
            or created_at is None
            or visibility not in {"internal", "shared"}
        ):
            return None
        return {
            "id": message_id,
            "authorKind": author_kind,
            "authorDisplayName": author_display_name,
            "text": text,
            "visibility": visibility,
            "createdAt": created_at,
        }


    def normalize_v2_thread_record(value: Any) -> dict | None:
        required = {
            "v",
            "collaborationId",
            "ownerEmail",
            "workspaceId",
            "mailboxId",
            "sourceRef",
            "sourceMessage",
            "state",
            "messages",
            "createdAt",
            "updatedAt",
        }
        participant_fields = {"ownerUserId", "ownerDisplayName", "participants"}
        if (
            not isinstance(value, dict)
            or not _v2_exact_keys(value, required, participant_fields)
            or bool(set(value) & participant_fields)
            != participant_fields.issubset(value)
        ):
            return None
        if type(value.get("v")) is not int or value.get("v") != COLLABORATION_V2_THREAD_SCHEMA_VERSION:
            return None
        collaboration_id = value.get("collaborationId")
        owner_email = normalize_v2_email(value.get("ownerEmail"))
        canonical_workspace_id = normalize_v2_workspace_id(value.get("workspaceId"))
        # Email-as-workspace records were produced only by the inactive foundation.
        # They remain decodeable for fail-closed inspection, but the active owner
        # resolver can mint only canonical ``wsp_`` authority and therefore cannot
        # authorize or create one of these historical records.
        historical_workspace_id = normalize_v2_email(value.get("workspaceId"))
        workspace_id = canonical_workspace_id or historical_workspace_id
        mailbox_id = _v2_mailbox_id(value.get("mailboxId"))
        source_ref = normalize_v2_source_ref(value.get("sourceRef"))
        source_message = normalize_v2_source_message(value.get("sourceMessage"))
        state = _v2_bounded_string(value.get("state"), max_length=32)
        created_at = _v2_timestamp_milliseconds(value.get("createdAt"))
        updated_at = _v2_timestamp_milliseconds(value.get("updatedAt"))
        messages = value.get("messages")
        has_participant_authority = participant_fields.issubset(value)
        if (
            not is_v2_opaque_id(collaboration_id)
            or not owner_email
            or value.get("ownerEmail") != owner_email
            or value.get("workspaceId") != workspace_id
            or (
                canonical_workspace_id is None
                and historical_workspace_id != owner_email
            )
            or not mailbox_id
            or source_ref is None
            or source_message is None
            or state not in {"needs_review", "needs_action", "note_only", "resolved"}
            or created_at is None
            or updated_at is None
            or updated_at < created_at
            or not isinstance(messages, list)
            or len(messages) > MAX_V2_MESSAGES
        ):
            return None
        normalized_messages = [normalize_v2_message_record(message) for message in messages]
        if any(message is None for message in normalized_messages):
            return None
        normalized_participants: list[dict] | None = None
        owner_user_id: str | None = None
        owner_display_name: str | None = None
        if has_participant_authority:
            owner_user_id = normalize_v2_user_id(value.get("ownerUserId"))
            owner_display_name = _v2_bounded_string(
                value.get("ownerDisplayName"), max_length=256
            )
            participants = value.get("participants")
            if (
                canonical_workspace_id is None
                or owner_user_id is None
                or owner_display_name is None
                or not isinstance(participants, list)
                or not 1 <= len(participants) <= MAX_V2_EXPLICIT_PARTICIPANTS
            ):
                return None
            normalized_participants = [
                normalize_v2_participant_authority(participant)
                for participant in participants
            ]
            if any(participant is None for participant in normalized_participants):
                return None
            normalized_participants = sorted(
                normalized_participants, key=lambda participant: participant["userId"]
            )
            participant_user_ids = [
                participant["userId"] for participant in normalized_participants
            ]
            if (
                owner_user_id in participant_user_ids
                or len(set(participant_user_ids)) != len(participant_user_ids)
            ):
                return None
        normalized = {
            "v": COLLABORATION_V2_THREAD_SCHEMA_VERSION,
            "collaborationId": collaboration_id,
            "ownerEmail": owner_email,
            "workspaceId": workspace_id,
            "mailboxId": mailbox_id,
            "sourceRef": source_ref,
            "sourceMessage": source_message,
            "state": state,
            "messages": normalized_messages,
            "createdAt": created_at,
            "updatedAt": updated_at,
        }
        if normalized_participants is not None:
            normalized.update(
                {
                    "ownerUserId": owner_user_id,
                    "ownerDisplayName": owner_display_name,
                    "participants": normalized_participants,
                }
            )
        return (
            normalized
            if _v2_json_is_bounded(
                normalized, MAX_V2_THREAD_BYTES, record_kind="thread"
            )
            else None
        )


    def normalize_v2_invite_record(value: Any) -> dict | None:
        required = {
            "v", "inviteId", "tokenHash", "ownerEmail", "workspaceId", "mailboxId",
            "collaborationId", "identityAssurance", "allowedActions", "visibility",
            "createdBy", "createdAt", "expiresAt", "status", "exchangedAt",
            "exchangeCount", "revokedAt", "revokedBy",
        }
        optional = {"invitedEmail", "activeSessionHash"}
        if not isinstance(value, dict) or not _v2_exact_keys(value, required, optional):
            return None
        invite_id = value.get("inviteId")
        token_hash = value.get("tokenHash")
        owner_email = normalize_v2_email(value.get("ownerEmail"))
        workspace_id = normalize_v2_email(value.get("workspaceId"))
        mailbox_id = _v2_mailbox_id(value.get("mailboxId"))
        collaboration_id = value.get("collaborationId")
        invited_email = (
            normalize_v2_email(value.get("invitedEmail"))
            if "invitedEmail" in value
            else None
        )
        created_by = value.get("createdBy")
        created_at = _v2_timestamp_seconds(value.get("createdAt"))
        expires_at = _v2_timestamp_seconds(value.get("expiresAt"))
        exchanged_at = value.get("exchangedAt")
        revoked_at = value.get("revokedAt")
        exchange_count = _v2_nonnegative_int(value.get("exchangeCount"))
        status = _v2_bounded_string(value.get("status"), max_length=16)
        if (
            type(value.get("v")) is not int
            or value.get("v") != COLLABORATION_V2_INVITE_SCHEMA_VERSION
            or not is_v2_opaque_id(invite_id)
            or not isinstance(token_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", token_hash)
            or not owner_email
            or value.get("ownerEmail") != owner_email
            or value.get("workspaceId") != workspace_id
            or workspace_id != owner_email
            or not mailbox_id
            or not is_v2_opaque_id(collaboration_id)
            or (
                "invitedEmail" in value
                and (
                    invited_email is None
                    or value.get("invitedEmail") != invited_email
                )
            )
            or ("activeSessionHash" in value and value.get("activeSessionHash") is None)
            or value.get("identityAssurance") != "link_possession"
            or value.get("allowedActions") != ["read", "reply"]
            or value.get("visibility") != "shared_only"
            or not isinstance(created_by, dict)
            or not _v2_exact_keys(created_by, {"ownerEmail", "displayName"})
            or created_by.get("ownerEmail") != owner_email
            or not _v2_bounded_string(created_by.get("displayName"), max_length=256)
            or created_at is None
            or expires_at is None
            or expires_at <= created_at
            or expires_at - created_at > MAX_V2_INVITE_LIFETIME_SECONDS
            or status not in {"active", "exchanged", "revoked", "expired"}
            or exchange_count not in {0, 1}
            or (exchanged_at is not None and _v2_timestamp_seconds(exchanged_at) is None)
            or (revoked_at is not None and _v2_timestamp_seconds(revoked_at) is None)
            or (
                value.get("revokedBy") is not None
                and (
                    value.get("revokedBy") != owner_email
                    or normalize_v2_email(value.get("revokedBy")) != owner_email
                )
            )
        ):
            return None
        if status == "active" and (
            exchange_count != 0
            or exchanged_at is not None
            or revoked_at is not None
            or value.get("revokedBy") is not None
            or value.get("activeSessionHash") is not None
        ):
            return None
        if status == "exchanged" and (
            exchange_count != 1
            or exchanged_at is None
            or exchanged_at < created_at
            or exchanged_at >= expires_at
            or revoked_at is not None
            or value.get("revokedBy") is not None
            or value.get("activeSessionHash") is None
        ):
            return None
        if status == "revoked" and (
            revoked_at is None
            or revoked_at <= created_at
            or revoked_at >= expires_at
            or value.get("revokedBy") != owner_email
            or (
                exchange_count == 0
                and (exchanged_at is not None or value.get("activeSessionHash") is not None)
            )
            or (
                exchange_count == 1
                and (
                    exchanged_at is None
                    or exchanged_at < created_at
                    or exchanged_at >= expires_at
                    or revoked_at <= exchanged_at
                    or value.get("activeSessionHash") is None
                )
            )
        ):
            return None
        if status == "expired" and (
            exchange_count != 0
            or exchanged_at is not None
            or revoked_at is not None
            or value.get("revokedBy") is not None
            or value.get("activeSessionHash") is not None
        ):
            return None
        active_session_hash = value.get("activeSessionHash")
        if active_session_hash is not None and (
            not isinstance(active_session_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", active_session_hash)
        ):
            return None
        normalized = {
            "v": COLLABORATION_V2_INVITE_SCHEMA_VERSION,
            "inviteId": invite_id,
            "tokenHash": token_hash,
            "ownerEmail": owner_email,
            "workspaceId": workspace_id,
            "mailboxId": mailbox_id,
            "collaborationId": collaboration_id,
            "identityAssurance": "link_possession",
            "allowedActions": ["read", "reply"],
            "visibility": "shared_only",
            "createdBy": {
                "ownerEmail": owner_email,
                "displayName": _v2_bounded_string(created_by.get("displayName"), max_length=256),
            },
            "createdAt": created_at,
            "expiresAt": expires_at,
            "status": status,
            "exchangedAt": _v2_timestamp_seconds(exchanged_at) if exchanged_at is not None else None,
            "exchangeCount": exchange_count,
            "revokedAt": _v2_timestamp_seconds(revoked_at) if revoked_at is not None else None,
            "revokedBy": normalize_v2_email(value.get("revokedBy")) if value.get("revokedBy") is not None else None,
        }
        if invited_email is not None:
            normalized["invitedEmail"] = invited_email
        if active_session_hash is not None:
            normalized["activeSessionHash"] = active_session_hash
        return (
            normalized
            if _v2_json_is_bounded(
                normalized, MAX_V2_INVITE_BYTES, record_kind="invite"
            )
            else None
        )


    def build_v2_guest_thread_dto(value: Any) -> dict | None:
        thread = normalize_v2_thread_record(value)
        if thread is None:
            return None
        role_by_kind = {
            "owner": "Cuevion user",
            "internal": "Cuevion user",
            "guest": "Guest reviewer",
            "system": "System",
        }
        # Only explicitly shared messages cross the guest boundary.
        shared_messages = [
            {
                "id": message["id"],
                "authorDisplayName": message["authorDisplayName"],
                "authorRole": role_by_kind[message["authorKind"]],
                "text": message["text"],
                "timestamp": message["createdAt"],
            }
            for message in thread["messages"]
            if message["visibility"] == "shared"
        ]
        source = thread["sourceMessage"]
        return {
            "collaborationId": thread["collaborationId"],
            "state": thread["state"],
            "updatedAt": thread["updatedAt"],
            "allowedActions": ["read", "reply"],
            "sharedSource": {
                "subject": source["subject"],
                "senderDisplay": source["senderDisplay"],
                "fromDisplay": source["fromDisplay"],
                "timestamp": source["timestamp"],
                "bodyText": source["bodyText"],
            },
            "messages": shared_messages,
        }


    def _build_v2_server_message(
        text: Any,
        *,
        author_kind: str,
        author_display_name: Any,
        visibility: str,
        created_at: int | None = None,
    ) -> dict | None:
        normalized_text = _v2_free_text(text, max_length=MAX_V2_MESSAGE_TEXT)
        display_name = _v2_bounded_string(author_display_name, max_length=256)
        normalized_author_kind = _v2_bounded_string(author_kind, max_length=16)
        normalized_visibility = _v2_bounded_string(visibility, max_length=16)
        timestamp = _v2_timestamp_milliseconds(
            time.time_ns() // 1_000_000 if created_at is None else created_at
        )
        if (
            normalized_text is None
            or display_name is None
            or timestamp is None
            or normalized_author_kind not in {"owner", "internal", "guest"}
            or normalized_visibility not in {"internal", "shared"}
        ):
            return None
        return normalize_v2_message_record(
            {
                "id": generate_v2_opaque_id(),
                "authorKind": normalized_author_kind,
                "authorDisplayName": display_name,
                "text": normalized_text,
                "visibility": normalized_visibility,
                "createdAt": timestamp,
            }
        )


    def build_v2_owner_shared_message(context: Any, text: Any) -> dict | None:
        return _build_v2_context_message(context, text, author_kind="owner", visibility="shared")


    def build_v2_owner_internal_message(context: Any, text: Any) -> dict | None:
        return _build_v2_context_message(context, text, author_kind="owner", visibility="internal")


    def build_v2_internal_shared_message(context: Any, text: Any) -> dict | None:
        return _build_v2_context_message(context, text, author_kind="internal", visibility="shared")


    def build_v2_internal_internal_message(context: Any, text: Any) -> dict | None:
        return _build_v2_context_message(context, text, author_kind="internal", visibility="internal")


    def _build_v2_context_message(
        context: Any,
        text: Any,
        *,
        author_kind: str,
        visibility: str,
        created_at: int | None = None,
    ) -> dict | None:
        from .authorization import _is_internal_capability
        if not _is_internal_capability(context, actions={"reply", "internal_note"}):
            return None
        return _build_v2_server_message(
            text,
            author_kind=author_kind,
            author_display_name=context.actor_display_name,
            visibility=visibility,
            created_at=created_at,
        )


    def build_v2_guest_shared_reply(
        session_context: Any,
        text: Any,
        *,
        _created_at: int | None = None,
    ) -> dict | None:
        from .guest_session import _is_guest_mutation_capability
        if not _is_guest_mutation_capability(session_context):
            return None
        return _build_v2_server_message(
            text,
            author_kind="guest",
            author_display_name=session_context.guest_display_name,
            visibility="shared",
            created_at=_created_at,
        )
