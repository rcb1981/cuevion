from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Callable, Mapping, TypedDict

from api.auth.email_address import normalize_auth_email
from api.auth.session_store import (
    CommandTransport,
    SessionConfigurationError,
    SessionStoreUnavailable,
    build_kv_command_transport,
)

OUT_OF_OFFICE_SCHEMA_VERSION = 1
OUT_OF_OFFICE_CONFIG_KEY_PREFIX = "cuevion:out-of-office:v1:config:"
OUT_OF_OFFICE_ACTIVE_INDEX_KEY = "cuevion:out-of-office:v1:active"
OUT_OF_OFFICE_CURSOR_KEY_PREFIX = "cuevion:out-of-office:v1:cursor:"
OUT_OF_OFFICE_WORKER_LEASE_KEY_PREFIX = "cuevion:out-of-office:v1:worker-lease:"
OUT_OF_OFFICE_SUPPRESSION_KEY_PREFIX = "cuevion:out-of-office:v1:suppression:"
OUT_OF_OFFICE_WORKER_LEASE_TTL_SECONDS = 5 * 60
OUT_OF_OFFICE_SUPPRESSION_TTL_SECONDS = 24 * 60 * 60
MAX_OUT_OF_OFFICE_SUBJECT_CHARACTERS = 200
MAX_OUT_OF_OFFICE_MESSAGE_CHARACTERS = 10_000
MAX_ACTIVE_TARGETS = 2_000

_UPSERT_CONFIG = """
redis.call('SET', KEYS[1], ARGV[1])
if ARGV[2] == '1' then
  redis.call('SADD', KEYS[2], ARGV[3])
else
  redis.call('SREM', KEYS[2], ARGV[3])
end
return 1
"""

_UPSERT_CONFIG_WITH_CURSOR = """
redis.call('SET', KEYS[1], ARGV[1])
redis.call('SET', KEYS[2], ARGV[2])
redis.call('SADD', KEYS[3], ARGV[3])
return 1
"""

_RELEASE_TOKEN = """
local current = redis.call('GET', KEYS[1])
if not current then return 0 end
if current ~= ARGV[1] then return 0 end
return redis.call('DEL', KEYS[1])
"""




class OutOfOfficeSettings(TypedDict):
    schemaVersion: int
    enabled: bool
    startsAt: str | None
    endsAt: str | None
    activatedAt: str | None
    subject: str
    message: str
    updatedAt: str


class ActiveOutOfOfficeTarget(TypedDict):
    ownerEmail: str
    mailboxId: str


class GmailOutOfOfficeCursor(TypedDict):
    schemaVersion: int
    provider: str
    historyId: str
    updatedAt: str


class ImapOutOfOfficeCursor(TypedDict):
    schemaVersion: int
    provider: str
    uidValidity: str
    lastUid: str
    updatedAt: str


OutOfOfficeCursor = GmailOutOfOfficeCursor | ImapOutOfOfficeCursor


class OutOfOfficeStoreUnavailable(Exception):
    __slots__ = ()


class OutOfOfficeValidationError(ValueError):
    __slots__ = ()


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso_timestamp(value: object, *, field: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise OutOfOfficeValidationError(f"{field} must be an ISO timestamp or null.")
    text = value.strip()
    if not text or len(text) > 64:
        raise OutOfOfficeValidationError(f"{field} must be a valid ISO timestamp.")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise OutOfOfficeValidationError(f"{field} must be a valid ISO timestamp.") from None
    if parsed.tzinfo is None:
        raise OutOfOfficeValidationError(f"{field} must include a timezone.")
    return _utc_iso(parsed)


def normalize_out_of_office_settings(
    value: object,
    *,
    now: datetime | None = None,
) -> OutOfOfficeSettings:
    if not isinstance(value, dict):
        raise OutOfOfficeValidationError("settings must be a JSON object.")
    allowed = {
        "schemaVersion",
        "enabled",
        "startsAt",
        "endsAt",
        "activatedAt",
        "subject",
        "message",
        "updatedAt",
    }
    if set(value) - allowed:
        raise OutOfOfficeValidationError("settings contains unsupported fields.")
    if "schemaVersion" in value and value.get("schemaVersion") != OUT_OF_OFFICE_SCHEMA_VERSION:
        raise OutOfOfficeValidationError("schemaVersion is invalid.")

    enabled = value.get("enabled", False)
    subject = value.get("subject", "")
    message = value.get("message", "")
    if not isinstance(enabled, bool):
        raise OutOfOfficeValidationError("enabled must be a boolean.")
    if not isinstance(subject, str) or len(subject) > MAX_OUT_OF_OFFICE_SUBJECT_CHARACTERS:
        raise OutOfOfficeValidationError("subject is invalid.")
    if not isinstance(message, str) or len(message) > MAX_OUT_OF_OFFICE_MESSAGE_CHARACTERS:
        raise OutOfOfficeValidationError("message is invalid.")

    subject = subject.strip()
    message = message.strip()
    starts_at = _parse_iso_timestamp(value.get("startsAt"), field="startsAt")
    ends_at = _parse_iso_timestamp(value.get("endsAt"), field="endsAt")
    activated_at = _parse_iso_timestamp(value.get("activatedAt"), field="activatedAt")

    if starts_at and ends_at:
        starts = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
        ends = datetime.fromisoformat(ends_at.replace("Z", "+00:00"))
        if ends <= starts:
            raise OutOfOfficeValidationError("endsAt must be later than startsAt.")

    if enabled and not subject:
        raise OutOfOfficeValidationError("subject is required when out of office is enabled.")
    if enabled and not message:
        raise OutOfOfficeValidationError("message is required when out of office is enabled.")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    return {
        "schemaVersion": OUT_OF_OFFICE_SCHEMA_VERSION,
        "enabled": enabled,
        "startsAt": starts_at,
        "endsAt": ends_at,
        "activatedAt": activated_at,
        "subject": subject,
        "message": message,
        "updatedAt": _utc_iso(current),
    }


def default_out_of_office_settings(*, now: datetime | None = None) -> OutOfOfficeSettings:
    return normalize_out_of_office_settings(
        {
            "enabled": False,
            "startsAt": None,
            "endsAt": None,
            "activatedAt": None,
            "subject": "",
            "message": "",
        },
        now=now,
    )


def is_out_of_office_active(
    settings: Mapping[str, object],
    *,
    now: datetime | None = None,
) -> bool:
    try:
        normalized = normalize_out_of_office_settings(dict(settings), now=now)
    except OutOfOfficeValidationError:
        return False
    if not normalized["enabled"]:
        return False

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)

    if normalized["startsAt"]:
        starts = datetime.fromisoformat(normalized["startsAt"].replace("Z", "+00:00"))
        if current < starts:
            return False
    if normalized["endsAt"]:
        ends = datetime.fromisoformat(normalized["endsAt"].replace("Z", "+00:00"))
        if current >= ends:
            return False
    return True


def _owner_digest(owner_email: str) -> str:
    normalized = normalize_auth_email(owner_email)
    if not normalized:
        raise OutOfOfficeValidationError("owner email is invalid.")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _mailbox_digest(mailbox_id: str) -> str:
    if not isinstance(mailbox_id, str):
        raise OutOfOfficeValidationError("mailbox id is invalid.")
    normalized = mailbox_id.strip()
    if not normalized or len(normalized) > 256:
        raise OutOfOfficeValidationError("mailbox id is invalid.")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_out_of_office_config_key(owner_email: str, mailbox_id: str) -> str:
    return (
        OUT_OF_OFFICE_CONFIG_KEY_PREFIX
        + _owner_digest(owner_email)
        + ":"
        + _mailbox_digest(mailbox_id)
    )


def _target_digest(owner_email: str, mailbox_id: str) -> str:
    normalized_owner = normalize_auth_email(owner_email)
    normalized_mailbox = mailbox_id.strip() if isinstance(mailbox_id, str) else ""
    if not normalized_owner or not normalized_mailbox or len(normalized_mailbox) > 256:
        raise OutOfOfficeValidationError("target is invalid.")
    return hashlib.sha256(
        (normalized_owner + "\x00" + normalized_mailbox).encode("utf-8")
    ).hexdigest()


def build_out_of_office_cursor_key(owner_email: str, mailbox_id: str) -> str:
    return OUT_OF_OFFICE_CURSOR_KEY_PREFIX + _target_digest(owner_email, mailbox_id)


def build_out_of_office_worker_lease_key(owner_email: str, mailbox_id: str) -> str:
    return OUT_OF_OFFICE_WORKER_LEASE_KEY_PREFIX + _target_digest(owner_email, mailbox_id)


def _sender_digest(sender_email: str) -> str:
    normalized_sender = normalize_auth_email(sender_email)
    if not normalized_sender:
        raise OutOfOfficeValidationError("sender email is invalid.")
    return hashlib.sha256(normalized_sender.encode("utf-8")).hexdigest()


def build_out_of_office_suppression_key(
    owner_email: str, mailbox_id: str, sender_email: str
) -> str:
    return (
        OUT_OF_OFFICE_SUPPRESSION_KEY_PREFIX
        + _target_digest(owner_email, mailbox_id)
        + ":"
        + _sender_digest(sender_email)
    )


def _valid_decimal_identifier(value: object, *, maximum_length: int = 64) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum_length
        and value.isascii()
        and value.isdigit()
        and int(value) >= 0
    )


def normalize_out_of_office_cursor(value: object) -> OutOfOfficeCursor | None:
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        return None
    provider = value.get("provider")
    updated_at = value.get("updatedAt")
    if not isinstance(updated_at, str):
        return None
    try:
        parsed_updated_at = _parse_iso_timestamp(updated_at, field="updatedAt")
    except OutOfOfficeValidationError:
        return None
    if parsed_updated_at is None:
        return None

    if provider == "google":
        if set(value) != {"schemaVersion", "provider", "historyId", "updatedAt"}:
            return None
        history_id = value.get("historyId")
        if not _valid_decimal_identifier(history_id):
            return None
        return {
            "schemaVersion": 1,
            "provider": "google",
            "historyId": history_id,
            "updatedAt": parsed_updated_at,
        }

    if provider == "custom_imap":
        if set(value) != {
            "schemaVersion",
            "provider",
            "uidValidity",
            "lastUid",
            "updatedAt",
        }:
            return None
        uid_validity = value.get("uidValidity")
        last_uid = value.get("lastUid")
        if not _valid_decimal_identifier(uid_validity) or not _valid_decimal_identifier(last_uid):
            return None
        return {
            "schemaVersion": 1,
            "provider": "custom_imap",
            "uidValidity": uid_validity,
            "lastUid": last_uid,
            "updatedAt": parsed_updated_at,
        }

    return None


def build_gmail_out_of_office_cursor(history_id: str, *, now: datetime | None = None) -> GmailOutOfOfficeCursor:
    if not _valid_decimal_identifier(history_id):
        raise OutOfOfficeValidationError("Gmail history id is invalid.")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return {
        "schemaVersion": 1,
        "provider": "google",
        "historyId": history_id,
        "updatedAt": _utc_iso(current),
    }


def build_imap_out_of_office_cursor(
    uid_validity: str, last_uid: str, *, now: datetime | None = None
) -> ImapOutOfOfficeCursor:
    if not _valid_decimal_identifier(uid_validity) or not _valid_decimal_identifier(last_uid):
        raise OutOfOfficeValidationError("IMAP cursor is invalid.")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return {
        "schemaVersion": 1,
        "provider": "custom_imap",
        "uidValidity": uid_validity,
        "lastUid": last_uid,
        "updatedAt": _utc_iso(current),
    }


def encode_active_target(owner_email: str, mailbox_id: str) -> str:
    normalized_owner = normalize_auth_email(owner_email)
    normalized_mailbox = mailbox_id.strip() if isinstance(mailbox_id, str) else ""
    if not normalized_owner or not normalized_mailbox or len(normalized_mailbox) > 256:
        raise OutOfOfficeValidationError("active target is invalid.")
    return json.dumps(
        {"ownerEmail": normalized_owner, "mailboxId": normalized_mailbox},
        sort_keys=True,
        separators=(",", ":"),
    )


def decode_active_target(value: object) -> ActiveOutOfOfficeTarget | None:
    if not isinstance(value, str) or len(value) > 1_024:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict) or set(parsed) != {"ownerEmail", "mailboxId"}:
        return None
    owner = parsed.get("ownerEmail")
    mailbox_id = parsed.get("mailboxId")
    if not isinstance(owner, str) or normalize_auth_email(owner) != owner:
        return None
    if not isinstance(mailbox_id, str) or not mailbox_id.strip() or len(mailbox_id) > 256:
        return None
    return {"ownerEmail": owner, "mailboxId": mailbox_id}


class OutOfOfficeStore:
    __slots__ = ("_transport",)

    def __init__(self, command_transport: CommandTransport) -> None:
        if not callable(command_transport):
            raise OutOfOfficeStoreUnavailable()
        self._transport = command_transport

    def _command(self, command: list[object]) -> object:
        try:
            payload = self._transport(command)
        except Exception:
            raise OutOfOfficeStoreUnavailable() from None
        if type(payload) is not dict or set(payload) != {"result"}:
            raise OutOfOfficeStoreUnavailable()
        return payload["result"]

    def get(self, owner_email: str, mailbox_id: str) -> OutOfOfficeSettings | None:
        result = self._command(
            ["GET", build_out_of_office_config_key(owner_email, mailbox_id)]
        )
        if result is None:
            return None
        if not isinstance(result, str) or len(result) > 32_768:
            raise OutOfOfficeStoreUnavailable()
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            raise OutOfOfficeStoreUnavailable() from None
        if not isinstance(parsed, dict) or parsed.get("schemaVersion") != OUT_OF_OFFICE_SCHEMA_VERSION:
            raise OutOfOfficeStoreUnavailable()
        try:
            normalized = normalize_out_of_office_settings(parsed)
        except OutOfOfficeValidationError:
            raise OutOfOfficeStoreUnavailable() from None
        updated_at = parsed.get("updatedAt")
        if not isinstance(updated_at, str) or len(updated_at) > 64:
            raise OutOfOfficeStoreUnavailable()
        try:
            _parse_iso_timestamp(updated_at, field="updatedAt")
        except OutOfOfficeValidationError:
            raise OutOfOfficeStoreUnavailable() from None
        normalized["updatedAt"] = updated_at
        return normalized

    def put(
        self,
        owner_email: str,
        mailbox_id: str,
        settings: OutOfOfficeSettings,
    ) -> None:
        normalized = normalize_out_of_office_settings(settings)
        stored_updated_at = settings.get("updatedAt") if isinstance(settings, dict) else None
        if isinstance(stored_updated_at, str):
            validated_updated_at = _parse_iso_timestamp(stored_updated_at, field="updatedAt")
            if validated_updated_at is None:
                raise OutOfOfficeStoreUnavailable()
            normalized["updatedAt"] = validated_updated_at
        stored_activated_at = settings.get("activatedAt") if isinstance(settings, dict) else None
        if stored_activated_at is not None:
            validated_activated_at = _parse_iso_timestamp(stored_activated_at, field="activatedAt")
            if validated_activated_at is None:
                raise OutOfOfficeStoreUnavailable()
            normalized["activatedAt"] = validated_activated_at
        encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        target = encode_active_target(owner_email, mailbox_id)
        result = self._command(
            [
                "EVAL",
                _UPSERT_CONFIG,
                2,
                build_out_of_office_config_key(owner_email, mailbox_id),
                OUT_OF_OFFICE_ACTIVE_INDEX_KEY,
                encoded,
                "1" if normalized["enabled"] else "0",
                target,
            ]
        )
        if result != 1:
            raise OutOfOfficeStoreUnavailable()

    def put_with_cursor(
        self,
        owner_email: str,
        mailbox_id: str,
        settings: OutOfOfficeSettings,
        cursor: OutOfOfficeCursor,
    ) -> None:
        normalized_settings = normalize_out_of_office_settings(settings)
        normalized_cursor = normalize_out_of_office_cursor(cursor)
        if not normalized_settings["enabled"] or normalized_cursor is None:
            raise OutOfOfficeStoreUnavailable()
        for field in ("updatedAt", "activatedAt"):
            stored_value = settings.get(field) if isinstance(settings, dict) else None
            if stored_value is None and field == "activatedAt":
                continue
            validated = _parse_iso_timestamp(stored_value, field=field)
            if validated is None:
                raise OutOfOfficeStoreUnavailable()
            normalized_settings[field] = validated
        encoded_settings = json.dumps(
            normalized_settings, sort_keys=True, separators=(",", ":")
        )
        encoded_cursor = json.dumps(
            normalized_cursor, sort_keys=True, separators=(",", ":")
        )
        target = encode_active_target(owner_email, mailbox_id)
        result = self._command(
            [
                "EVAL",
                _UPSERT_CONFIG_WITH_CURSOR,
                3,
                build_out_of_office_config_key(owner_email, mailbox_id),
                build_out_of_office_cursor_key(owner_email, mailbox_id),
                OUT_OF_OFFICE_ACTIVE_INDEX_KEY,
                encoded_settings,
                encoded_cursor,
                target,
            ]
        )
        if result != 1:
            raise OutOfOfficeStoreUnavailable()

    def list_active_targets(self) -> list[ActiveOutOfOfficeTarget]:
        result = self._command(["SMEMBERS", OUT_OF_OFFICE_ACTIVE_INDEX_KEY])
        if result is None:
            return []
        if not isinstance(result, list) or len(result) > MAX_ACTIVE_TARGETS:
            raise OutOfOfficeStoreUnavailable()
        targets: list[ActiveOutOfOfficeTarget] = []
        seen: set[tuple[str, str]] = set()
        for raw in result:
            target = decode_active_target(raw)
            if target is None:
                raise OutOfOfficeStoreUnavailable()
            identity = (target["ownerEmail"], target["mailboxId"])
            if identity in seen:
                continue
            seen.add(identity)
            targets.append(target)
        return targets

    def get_cursor(self, owner_email: str, mailbox_id: str) -> OutOfOfficeCursor | None:
        result = self._command(["GET", build_out_of_office_cursor_key(owner_email, mailbox_id)])
        if result is None:
            return None
        if not isinstance(result, str) or len(result) > 4_096:
            raise OutOfOfficeStoreUnavailable()
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            raise OutOfOfficeStoreUnavailable() from None
        cursor = normalize_out_of_office_cursor(parsed)
        if cursor is None:
            raise OutOfOfficeStoreUnavailable()
        return cursor

    def put_cursor(
        self, owner_email: str, mailbox_id: str, cursor: OutOfOfficeCursor
    ) -> None:
        normalized = normalize_out_of_office_cursor(cursor)
        if normalized is None:
            raise OutOfOfficeStoreUnavailable()
        result = self._command(
            [
                "SET",
                build_out_of_office_cursor_key(owner_email, mailbox_id),
                json.dumps(normalized, sort_keys=True, separators=(",", ":")),
            ]
        )
        if result != "OK":
            raise OutOfOfficeStoreUnavailable()

    def acquire_worker_lease(self, owner_email: str, mailbox_id: str) -> str | None:
        token = secrets.token_urlsafe(32)
        result = self._command(
            [
                "SET",
                build_out_of_office_worker_lease_key(owner_email, mailbox_id),
                token,
                "EX",
                OUT_OF_OFFICE_WORKER_LEASE_TTL_SECONDS,
                "NX",
            ]
        )
        if result == "OK":
            return token
        if result is None:
            return None
        raise OutOfOfficeStoreUnavailable()

    def release_worker_lease(
        self, owner_email: str, mailbox_id: str, token: str
    ) -> bool:
        if not isinstance(token, str) or not token:
            return False
        result = self._command(
            [
                "EVAL",
                _RELEASE_TOKEN,
                1,
                build_out_of_office_worker_lease_key(owner_email, mailbox_id),
                token,
            ]
        )
        if result in (0, 1):
            return result == 1
        raise OutOfOfficeStoreUnavailable()

    def reserve_sender_reply(
        self, owner_email: str, mailbox_id: str, sender_email: str
    ) -> str | None:
        token = secrets.token_urlsafe(32)
        result = self._command(
            [
                "SET",
                build_out_of_office_suppression_key(owner_email, mailbox_id, sender_email),
                token,
                "EX",
                OUT_OF_OFFICE_SUPPRESSION_TTL_SECONDS,
                "NX",
            ]
        )
        if result == "OK":
            return token
        if result is None:
            return None
        raise OutOfOfficeStoreUnavailable()

    def complete_sender_reply(
        self, owner_email: str, mailbox_id: str, sender_email: str, token: str
    ) -> bool:
        if not isinstance(token, str) or not token:
            return False
        result = self._command(
            ["GET", build_out_of_office_suppression_key(owner_email, mailbox_id, sender_email)]
        )
        if result is None:
            return False
        if not isinstance(result, str):
            raise OutOfOfficeStoreUnavailable()
        return result == token

    def release_sender_reply(
        self, owner_email: str, mailbox_id: str, sender_email: str, token: str
    ) -> bool:
        if not isinstance(token, str) or not token:
            return False
        result = self._command(
            [
                "EVAL",
                _RELEASE_TOKEN,
                1,
                build_out_of_office_suppression_key(owner_email, mailbox_id, sender_email),
                token,
            ]
        )
        if result in (0, 1):
            return result == 1
        raise OutOfOfficeStoreUnavailable()


def build_out_of_office_store(
    environment: Mapping[str, str] | None = None,
    *,
    command_transport: Callable[[list[object]], dict[str, object]] | None = None,
) -> OutOfOfficeStore:
    if command_transport is not None:
        return OutOfOfficeStore(command_transport)
    try:
        transport = build_kv_command_transport(environment)
    except (SessionConfigurationError, SessionStoreUnavailable):
        raise OutOfOfficeStoreUnavailable() from None
    return OutOfOfficeStore(transport)
