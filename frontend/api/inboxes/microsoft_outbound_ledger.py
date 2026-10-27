"""Durable single-attempt ledger for Microsoft outbound messages.

Every logical send must reserve one unpredictable attempt ID in durable CAS
storage BEFORE any Graph write. A repeated ID never calls Graph again.
Send intent is durably marked *before* the send API call; ambiguous ACKs
never lead to automated retransmission. No subject/body/recipients/tokens
are stored in this ledger.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from api.inboxes import oauth_token_store as store
from api.inboxes.microsoft_token_authority import MicrosoftMailboxAuthority, _canonical_authority

PREFIX = "cuevion:microsoft:outbound-attempt:v1:"
ATTEMPT_ID = re.compile(r"^[A-Za-z0-9_-]{43}$")
MAX_RECORD_BYTES = 4096
_STATES = frozenset(("reserved", "draft_prepared", "send_started", "accepted_by_microsoft", "send_not_started", "sent_copy_confirmed"))


class OutboundLedgerError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _scope_hash(authority: MicrosoftMailboxAuthority) -> str:
    a = _canonical_authority(authority)
    material = "\0".join((
        a.owner_email, a.mailbox_id, a.email, a.account_id, a.credential_generation
    )).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _key(authority: MicrosoftMailboxAuthority, attempt_id: str) -> str:
    if type(attempt_id) is not str or ATTEMPT_ID.fullmatch(attempt_id) is None:
        raise OutboundLedgerError("microsoft_send_attempt_invalid")
    a = _canonical_authority(authority)
    identity = "\0".join((a.owner_email, a.mailbox_id, attempt_id)).encode()
    return PREFIX + hashlib.sha256(identity).hexdigest()


def _digest(payload: dict) -> str:
    try:
        canonical = json.dumps(payload, ensure_ascii=True, allow_nan=False,
                               separators=(",", ":"), sort_keys=True).encode()
    except (TypeError, ValueError):
        raise OutboundLedgerError("microsoft_compose_invalid") from None
    return hashlib.sha256(canonical).hexdigest()


def _storage() -> dict:
    config = store._resolve_durable_store_config()
    if not isinstance(config, dict) or not config:
        raise OutboundLedgerError("microsoft_send_store_unavailable")
    return config


def _read(config: dict, key: str) -> tuple[dict | None, str | None]:
    try:
        obj, raw, error = store._read_durable_record_snapshot(config, key)
    except Exception:
        raise OutboundLedgerError("microsoft_send_store_unavailable") from None
    if error or obj is not None and (type(obj) is not dict or type(raw) is not str):
        raise OutboundLedgerError("microsoft_send_store_unavailable")
    if type(raw) is str and len(raw) > MAX_RECORD_BYTES:
        raise OutboundLedgerError("microsoft_send_store_unavailable")
    return obj, raw


def _valid_record(value: object, *, scope: str, digest: str, attempt_id: str) -> dict:
    if (
        type(value) is not dict
        or set(value) != {"v", "scope", "digest", "attemptId", "phase", "draftId", "updatedAt"}
        or value.get("v") != 1 or type(value.get("scope")) is not str
        or value["scope"] != scope or value.get("digest") != digest
        or value.get("attemptId") != attempt_id
        or value.get("phase") not in _STATES
        or type(value.get("updatedAt")) is not str
        or len(value["updatedAt"]) > 64
        or (value["draftId"] is not None
            and (type(value["draftId"]) is not str or len(value["draftId"]) > 512))
    ):
        raise OutboundLedgerError("microsoft_send_attempt_conflict")
    if value["phase"] in {"draft_prepared","send_started","accepted_by_microsoft","sent_copy_confirmed","send_not_started"} and not value["draftId"]:
        raise OutboundLedgerError("microsoft_send_attempt_conflict")
    return value


def _write(config: dict, key: str, expected: str | None, value: dict) -> None:
    try:
        stored, error = store._write_google_durable_record_if_unchanged(
            config, key, expected, value
        )
    except Exception:
        raise OutboundLedgerError("microsoft_send_store_unavailable") from None
    if error:
        if type(error) is dict and error.get("code") == "gmail_token_write_conflict":
            raise OutboundLedgerError("microsoft_send_attempt_conflict")
        raise OutboundLedgerError("microsoft_send_store_unavailable")
    if stored != value:
        raise OutboundLedgerError("microsoft_send_store_unavailable")
    actual, _ = _read(config, key)
    if actual != value:
        raise OutboundLedgerError("microsoft_send_store_unavailable")


def _updated() -> str:
    return datetime.now(timezone.utc).isoformat()


def reserve_outbound_attempt(
    authority: MicrosoftMailboxAuthority, attempt_id: str, compose: dict,
) -> dict:
    """One immutable attempt ID; even duplicate identical content cannot resend."""
    key = _key(authority, attempt_id)
    scope = _scope_hash(authority)
    digest = _digest(compose)
    config = _storage()
    current, raw = _read(config, key)
    if current is not None:
        existing = _valid_record(current, scope=scope, digest=digest, attempt_id=attempt_id)
        return {"new": False, "phase": existing["phase"]}
    record = {
        "v": 1, "scope": scope, "digest": digest, "attemptId": attempt_id,
        "phase": "reserved", "draftId": None, "updatedAt": _updated(),
    }
    _write(config, key, raw, record)
    return {"new": True, "phase": "reserved"}


def advance_outbound_attempt(
    authority: MicrosoftMailboxAuthority, attempt_id: str, compose: dict,
    *, from_phase: str, to_phase: str, draft_id: str | None = None,
) -> dict:
    if (
        from_phase not in _STATES
        or (from_phase, to_phase) not in (
            ("reserved", "draft_prepared"),
            ("draft_prepared", "send_started"),
            ("send_started", "accepted_by_microsoft"),
        )
    ):
        raise OutboundLedgerError("microsoft_send_attempt_invalid")
    key = _key(authority, attempt_id)
    scope = _scope_hash(authority)
    digest = _digest(compose)
    config = _storage()
    current, raw = _read(config, key)
    current = _valid_record(current, scope=scope, digest=digest, attempt_id=attempt_id)
    if current["phase"] != from_phase:
        raise OutboundLedgerError("microsoft_send_attempt_conflict")
    if from_phase == "reserved":
        if type(draft_id) is not str or not draft_id or len(draft_id) > 512:
            raise OutboundLedgerError("microsoft_send_attempt_invalid")
    elif draft_id is not None and draft_id != current["draftId"]:
        raise OutboundLedgerError("microsoft_send_attempt_conflict")
    updated = {
        **current, "phase": to_phase,
        "draftId": draft_id if from_phase == "reserved" else current["draftId"],
        "updatedAt": _updated(),
    }
    _write(config, key, raw, updated)
    return {"phase": to_phase}


def inspect_outbound_attempt(
    authority: MicrosoftMailboxAuthority, attempt_id: str,
) -> dict | None:
    """Read exact owner/generation-bound ledger record without changing it.

    No subject, recipient, body, or token is requested from the browser.
    Missing attempt is distinct from unavailable durable storage.
    """
    key = _key(authority, attempt_id)
    record, _raw = _read(_storage(), key)
    if record is None:
        return None
    digest = record.get("digest") if type(record) is dict else None
    if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise OutboundLedgerError("microsoft_send_attempt_conflict")
    record = _valid_record(
        record, scope=_scope_hash(authority),
        digest=digest, attempt_id=attempt_id,
    )
    draft_id = record["draftId"]
    if draft_id is not None:
        from api.inboxes.microsoft_graph import _required_message_id, MicrosoftGraphError
        try:
            _required_message_id(draft_id)
        except MicrosoftGraphError:
            raise OutboundLedgerError("microsoft_send_attempt_conflict") from None
    return {"phase": record["phase"], "draftId": draft_id}


def _mark_existing_draft_attempt(authority, attempt_id, draft_id, phase):
    """Terminal evidence for an existing-draft attempt; no transport or retries."""
    config = _storage()
    key = _key(authority, attempt_id)
    value, raw = _read(config, key)
    digest = value.get('digest') if type(value) is dict else None
    if type(digest) is not str or re.fullmatch(r'[a-f0-9]{64}', digest) is None:
        raise OutboundLedgerError('microsoft_send_attempt_conflict')
    value = _valid_record(value, scope=_scope_hash(authority), digest=digest, attempt_id=attempt_id)
    permitted = {'reserved', 'draft_prepared'} if phase == 'send_not_started' else {'send_started', 'accepted_by_microsoft'}
    if phase not in {'send_not_started', 'sent_copy_confirmed'} or value['phase'] not in permitted or value['draftId'] not in {None, draft_id}:
        raise OutboundLedgerError('microsoft_send_attempt_conflict')
    from api.inboxes.microsoft_graph import _required_message_id
    _required_message_id(draft_id)
    _write(config, key, raw, {**value, 'phase': phase, 'draftId': draft_id, 'updatedAt': _updated()})
