"""Dormant, tenant-scoped storage primitives for Priority candidates.

This module deliberately has no producer, route, startup hook, or dependency on
workflow/semantic values.  A later provider-authoritative producer may call the
store with an already-derived minimal snapshot.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass
from typing import Callable, Literal

from .authority import PriorityMessageIdentity, parse_priority_message_identity
from .event_reference import derive_priority_hmac_key


CANDIDATE_STORE_SCHEMA_VERSION = 2
CANDIDATE_BASE_TTL_SECONDS = 30 * 24 * 60 * 60
CANDIDATE_PROVIDER_FAILURE_GRACE_SECONDS = 7 * 24 * 60 * 60
CANDIDATE_ABSOLUTE_TTL_SECONDS = 180 * 24 * 60 * 60
CANDIDATE_INDEX_TTL_SECONDS = CANDIDATE_ABSOLUTE_TTL_SECONDS
CANDIDATE_MAX_SERIALIZED_RECORD_BYTES = 4 * 1_024
CANDIDATE_MAX_SNIPPET_BYTES = 512
CANDIDATE_MAX_MAILBOX_RECORDS = 512
CANDIDATE_MAX_USER_RECORDS = 2_048
CANDIDATE_MAX_PAGE_RECORDS = 100
CANDIDATE_MAX_PAGE_OFFSET = CANDIDATE_MAX_USER_RECORDS
CANDIDATE_MAX_SAFE_INTEGER = 9_007_199_254_740_991

POSITIVE_REFERENCE_MAX_SECONDS = {
    "manual_priority": CANDIDATE_ABSOLUTE_TTL_SECONDS,
    "waiting": 14 * 24 * 60 * 60,
    "returned_reply": 14 * 24 * 60 * 60,
    "semantic_promotion": 30 * 24 * 60 * 60,
    "collaboration_priority": CANDIDATE_ABSOLUTE_TTL_SECONDS,
    "assigned_review": CANDIDATE_ABSOLUTE_TTL_SECONDS,
}

_POSITIVE_REFERENCE_KINDS = tuple(POSITIVE_REFERENCE_MAX_SECONDS)
_CANDIDATE_KEY_PREFIX = "cuevion:priority:candidate:v2:"
_SCOPE_HMAC_INFO = b"cuevion/priority/candidate-scope/v1\x00"
_MAILBOX_SCOPE_HMAC_INFO = b"cuevion/priority/candidate-mailbox-scope/v1\x00"
_USER_SCOPE_HMAC_INFO = b"cuevion/priority/candidate-user-scope/v1\x00"
_IDENTITY_HMAC_INFO = b"cuevion/priority/candidate-identity/v1\x00"
_NAMESPACE_HMAC_INFO = b"cuevion/priority/candidate-namespace/v1\x00"
_HEX_DIGEST_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
_INCOMPLETE_VALUE = "1"
_MISSING_SENTINEL = "__cuevion_priority_candidate_missing__"
_CORRUPT_SENTINEL = "__cuevion_priority_candidate_corrupt__"
_CONFLICT_SENTINEL = "__cuevion_priority_candidate_conflict__"
_MAILBOX_OVERFLOW_SENTINEL = "__cuevion_priority_candidate_mailbox_overflow__"
_USER_OVERFLOW_SENTINEL = "__cuevion_priority_candidate_user_overflow__"
_NAMESPACE_INVALIDATED_SENTINEL = (
    "__cuevion_priority_candidate_namespace_invalidated__"
)

PriorityCandidateRoutingState = Literal["unresolved", "ready"]

_ROUTING_SIGNAL_VALUES = frozenset({"Priority", "For review"})
_ROUTING_UI_SIGNAL_VALUES = frozenset(
    {"PROMO", "DEMO", "REPLY", "UPDATE", "BUSINESS", "FINANCE", "INFO", "NEW"}
)
_ROUTING_INTERNAL_CLASSIFICATION_VALUES = frozenset(
    {
        "promo",
        "promo_reminder",
        "workflow_update",
        "distributor_update",
        "labelradar_update",
        "trackstack_submission",
        "business_reminder",
        "royalty_statement",
        "finance",
        "info",
        "reply",
        "business",
        "demo",
        "high_priority_demo",
        "incomplete_demo",
        "unknown",
    }
)
_ROUTING_CATEGORY_VALUES = frozenset(
    {
        *_ROUTING_INTERNAL_CLASSIFICATION_VALUES,
        "bulk_demo",
        "weak_demo",
    }
)
_ROUTING_FINAL_VISIBILITY_VALUES = frozenset(
    {"show_priority", "show_normal", "show_low", "hide", "delete"}
)
_ROUTING_ACTION_VALUES = frozenset(
    {
        "show_in_priority",
        "show_in_main_feed",
        "show_in_quiet_view",
        "archive_candidate",
        "delete_or_archive",
    }
)
_ROUTING_V7_FINAL_PRIORITY_VALUES = frozenset(
    {"PRIORITY", "REVIEW", "NORMAL", "LOW"}
)
_ROUTING_NOISE_DISPOSITION_VALUES = frozenset(
    {"none", "bulk_marketing", "unsolicited_low_value", "strong_spam"}
)
_ROUTING_NOISE_CONFIDENCE_VALUES = frozenset({"low", "medium", "high"})
_ROUTING_NOISE_REASON_VALUES = (
    "provider_spam_evidence",
    "authentication_failure_evidence",
    "phishing_credential_request",
    "unsolicited_financial_solicitation",
    "unsolicited_investment_solicitation",
    "cold_sales_outreach",
    "cold_recruitment_outreach",
    "cold_call_to_action",
    "bulk_mail_evidence",
    "mailbox_relevance_mismatch",
    "no_conversation_evidence",
    "automated_sender_evidence",
)
_ROUTING_NOISE_REASON_VALUE_SET = frozenset(_ROUTING_NOISE_REASON_VALUES)
_VERSION_PLACEHOLDER_RE = re.compile(
    r"(?:^|[^a-z0-9])(?:unknown|fake|fallback|unset|none|null|n/?a)(?:$|[^a-z0-9])",
    re.IGNORECASE,
)

CommandTransport = Callable[[list[object]], dict[str, object]]


class CandidateStoreUnavailable(Exception):
    """Value-free failure for unavailable or malformed candidate storage."""

    __slots__ = ()

    def __str__(self) -> str:
        return "Priority candidate storage is unavailable"


class CandidateVersionConflict(Exception):
    """Value-free optimistic-version conflict."""

    __slots__ = ()

    def __str__(self) -> str:
        return "Priority candidate version conflict"


class CandidateCapacityExceeded(Exception):
    """Bounded capacity failure that reveals no tenant or message content."""

    __slots__ = ("scope_kind",)

    def __init__(self, scope_kind: str) -> None:
        self.scope_kind = scope_kind if scope_kind in {"mailbox", "user"} else "user"
        Exception.__init__(self)

    def __str__(self) -> str:
        return "Priority candidate capacity is exceeded"


class CandidateNamespaceInvalidated(Exception):
    """A provider namespace was invalidated while a producer was writing it."""

    __slots__ = ()

    def __str__(self) -> str:
        return "Priority candidate namespace is invalidated"


class CandidateReferenceRejected(Exception):
    """A positive reference could not safely retain candidate content."""

    __slots__ = ()

    def __str__(self) -> str:
        return "Priority candidate positive reference is rejected"


def _valid_identifier(value: object, maximum_bytes: int) -> bool:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or "\x00" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= maximum_bytes
    except UnicodeEncodeError:
        return False


def _valid_content_text(value: object, maximum_bytes: int) -> bool:
    if type(value) is not str or "\x00" in value or "\r" in value:
        return False
    if any(
        (ord(character) < 32 and character not in {"\n", "\t"})
        or ord(character) == 127
        for character in value
    ):
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= maximum_bytes
    except UnicodeEncodeError:
        return False


def _valid_routing_version(value: object) -> bool:
    return _valid_identifier(value, 128) and _VERSION_PLACEHOLDER_RE.search(value) is None


@dataclass(frozen=True, slots=True)
class PriorityCandidateMailboxScope:
    workspace_id: str
    user_id: str
    mailbox_id: str
    mailbox_account_identity: str
    provider: str

    def canonical_bytes(self) -> bytes:
        values = (
            self.workspace_id,
            self.user_id,
            self.mailbox_id,
            self.mailbox_account_identity,
            self.provider,
        )
        if (
            any(not _valid_identifier(value, 1_024) for value in values)
            or self.provider not in {"google", "custom_imap"}
        ):
            raise ValueError("invalid Priority candidate mailbox scope")
        return "\x00".join(values).encode("utf-8", errors="strict")

    def user_canonical_bytes(self) -> bytes:
        if not isinstance(self, PriorityCandidateMailboxScope):
            raise ValueError("invalid Priority candidate mailbox scope")
        self.canonical_bytes()
        return "\x00".join((self.workspace_id, self.user_id)).encode(
            "utf-8", errors="strict"
        )


@dataclass(frozen=True, slots=True)
class PriorityCandidateScope:
    workspace_id: str
    user_id: str
    mailbox_id: str
    mailbox_account_identity: str
    provider: str
    identity: PriorityMessageIdentity

    def mailbox_scope(self) -> PriorityCandidateMailboxScope:
        return PriorityCandidateMailboxScope(
            workspace_id=self.workspace_id,
            user_id=self.user_id,
            mailbox_id=self.mailbox_id,
            mailbox_account_identity=self.mailbox_account_identity,
            provider=self.provider,
        )

    def canonical_bytes(self) -> bytes:
        mailbox_bytes = self.mailbox_scope().canonical_bytes()
        try:
            identity_bytes = self.identity.canonical_bytes()
        except Exception:
            raise ValueError("invalid Priority candidate scope") from None
        if self.identity.provider != self.provider or not 1 <= len(identity_bytes) <= 2_048:
            raise ValueError("invalid Priority candidate scope")
        return mailbox_bytes + b"\x00" + identity_bytes

    def namespace_canonical_bytes(self) -> bytes:
        mailbox_bytes = self.mailbox_scope().canonical_bytes()
        if self.provider == "google":
            return mailbox_bytes + b"\x00google-api"
        if (
            self.identity.provider_folder is None
            or self.identity.uid_validity is None
        ):
            raise ValueError("invalid Priority candidate scope")
        return (
            mailbox_bytes
            + b"\x00imap\x00"
            + self.identity.provider_folder.encode("utf-8", errors="strict")
            + b"\x00"
            + self.identity.uid_validity.encode("ascii", errors="strict")
        )


@dataclass(frozen=True, slots=True)
class PriorityCandidateConversation:
    conversation_id: str
    authority_kind: str
    provider_thread_id: str | None = None
    rfc_root_message_id: str | None = None
    rfc_message_id: str | None = None

    def __post_init__(self) -> None:
        if (
            not _valid_identifier(self.conversation_id, 1_024)
            or not _valid_identifier(self.authority_kind, 64)
            or any(
                value is not None and not _valid_identifier(value, maximum)
                for value, maximum in (
                    (self.provider_thread_id, 256),
                    (self.rfc_root_message_id, 1_024),
                    (self.rfc_message_id, 1_024),
                )
            )
        ):
            raise ValueError("invalid Priority candidate conversation")


@dataclass(frozen=True, slots=True)
class PriorityCandidateRender:
    sender_display: str
    sender_address: str
    subject: str
    snippet: str
    created_at: str
    unread: bool
    flagged: bool

    def __post_init__(self) -> None:
        if (
            not _valid_content_text(self.sender_display, 256)
            or not _valid_content_text(self.sender_address, 320)
            or not _valid_content_text(self.subject, 998)
            or not _valid_content_text(self.snippet, CANDIDATE_MAX_SNIPPET_BYTES)
            or not _valid_identifier(self.created_at, 64)
            or type(self.unread) is not bool
            or type(self.flagged) is not bool
        ):
            raise ValueError("invalid Priority candidate render snapshot")


@dataclass(frozen=True, slots=True)
class PriorityCandidateRouting:
    signal: str | None
    ui_signal: str
    internal_classification: str
    category: str
    final_visibility: str | None
    action: str | None
    v7_final_priority: str | None
    noise_disposition: str
    noise_confidence: str
    noise_reasons: tuple[str, ...]
    classifier_version: str
    routing_version: str

    def __post_init__(self) -> None:
        if (
            (self.signal is not None and self.signal not in _ROUTING_SIGNAL_VALUES)
            or self.ui_signal not in _ROUTING_UI_SIGNAL_VALUES
            or self.internal_classification
            not in _ROUTING_INTERNAL_CLASSIFICATION_VALUES
            or self.category not in _ROUTING_CATEGORY_VALUES
            or (
                self.final_visibility is not None
                and self.final_visibility not in _ROUTING_FINAL_VISIBILITY_VALUES
            )
            or (
                self.action is not None
                and self.action not in _ROUTING_ACTION_VALUES
            )
            or (
                self.v7_final_priority is not None
                and self.v7_final_priority not in _ROUTING_V7_FINAL_PRIORITY_VALUES
            )
            or self.noise_disposition not in _ROUTING_NOISE_DISPOSITION_VALUES
            or self.noise_confidence not in _ROUTING_NOISE_CONFIDENCE_VALUES
            or type(self.noise_reasons) is not tuple
            or len(self.noise_reasons) > len(_ROUTING_NOISE_REASON_VALUES)
            or len(set(self.noise_reasons)) != len(self.noise_reasons)
            or any(
                reason not in _ROUTING_NOISE_REASON_VALUE_SET
                for reason in self.noise_reasons
            )
            or not _valid_routing_version(self.classifier_version)
            or not _valid_routing_version(self.routing_version)
        ):
            raise ValueError("invalid Priority candidate routing projection")


@dataclass(frozen=True, slots=True)
class PriorityCandidateProviderAuthority:
    folder: str
    labels: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not _valid_identifier(self.folder, 1_024)
            or type(self.labels) is not tuple
            or len(self.labels) > 64
            or len(set(self.labels)) != len(self.labels)
            or any(not _valid_identifier(label, 256) for label in self.labels)
        ):
            raise ValueError("invalid Priority candidate provider authority")


@dataclass(frozen=True, slots=True)
class PriorityCandidateSnapshot:
    conversation: PriorityCandidateConversation
    render: PriorityCandidateRender
    routing_state: PriorityCandidateRoutingState
    routing: PriorityCandidateRouting | None
    provider_authority: PriorityCandidateProviderAuthority

    def __post_init__(self) -> None:
        if not (
            (self.routing_state == "unresolved" and self.routing is None)
            or (
                self.routing_state == "ready"
                and isinstance(self.routing, PriorityCandidateRouting)
            )
        ):
            raise ValueError("invalid Priority candidate routing state")

    def validate_for_scope(self, scope: PriorityCandidateScope) -> None:
        if (
            not isinstance(scope, PriorityCandidateScope)
            or not isinstance(self.conversation, PriorityCandidateConversation)
            or not isinstance(self.render, PriorityCandidateRender)
            or not isinstance(
                self.provider_authority, PriorityCandidateProviderAuthority
            )
        ):
            raise ValueError("invalid Priority candidate snapshot")
        scope.canonical_bytes()
        if scope.provider == "google":
            valid = (
                self.provider_authority.folder == "INBOX"
                and "INBOX" in self.provider_authority.labels
                and not {"SENT", "SPAM", "TRASH", "DRAFT"}.intersection(
                    self.provider_authority.labels
                )
                and self.conversation.provider_thread_id is not None
                and self.conversation.rfc_root_message_id is None
                and self.conversation.rfc_message_id is None
            )
        else:
            valid = (
                self.provider_authority.folder == scope.identity.provider_folder
                and self.provider_authority.folder.casefold() == "inbox"
                and not self.provider_authority.labels
                and self.conversation.provider_thread_id is None
            )
        if not valid:
            raise ValueError("invalid Priority candidate snapshot")


@dataclass(frozen=True, slots=True)
class PriorityCandidatePositiveReference:
    kind: str
    expires_at: int

    def __post_init__(self) -> None:
        if (
            self.kind not in POSITIVE_REFERENCE_MAX_SECONDS
            or type(self.expires_at) is not int
            or not 0 <= self.expires_at <= CANDIDATE_MAX_SAFE_INTEGER
        ):
            raise ValueError("invalid Priority candidate positive reference")


@dataclass(frozen=True, slots=True)
class PriorityCandidateRecord:
    scope: PriorityCandidateScope
    snapshot: PriorityCandidateSnapshot
    provider_observed_at: int
    provider_validated_at: int
    base_expires_at: int
    absolute_expires_at: int
    grace_expires_at: int
    positive_references: tuple[PriorityCandidatePositiveReference, ...]
    state: str
    version: int
    updated_at: int

    def __post_init__(self) -> None:
        times = (
            self.provider_observed_at,
            self.provider_validated_at,
            self.base_expires_at,
            self.absolute_expires_at,
            self.grace_expires_at,
            self.version,
            self.updated_at,
        )
        if (
            not isinstance(self.scope, PriorityCandidateScope)
            or not isinstance(self.snapshot, PriorityCandidateSnapshot)
            or any(
                type(value) is not int
                or not 0 <= value <= CANDIDATE_MAX_SAFE_INTEGER
                for value in times
            )
            or self.version < 1
            or self.state not in {"provider_confirmed", "provider_validation_grace"}
            or type(self.positive_references) is not tuple
            or len(self.positive_references) != len(POSITIVE_REFERENCE_MAX_SECONDS)
            or tuple(reference.kind for reference in self.positive_references)
            != _POSITIVE_REFERENCE_KINDS
            or self.provider_observed_at != self.provider_validated_at
            or self.updated_at < self.provider_observed_at
            or self.base_expires_at
            != self.provider_observed_at + CANDIDATE_BASE_TTL_SECONDS * 1_000
            or self.absolute_expires_at
            != self.provider_observed_at + CANDIDATE_ABSOLUTE_TTL_SECONDS * 1_000
            or self.base_expires_at > self.absolute_expires_at
            or any(
                reference.expires_at > self.absolute_expires_at
                for reference in self.positive_references
            )
            or (
                self.state == "provider_confirmed" and self.grace_expires_at != 0
            )
            or (
                self.state == "provider_validation_grace"
                and not (
                    self.updated_at < self.grace_expires_at
                    <= min(
                        self.provider_validated_at
                        + CANDIDATE_PROVIDER_FAILURE_GRACE_SECONDS * 1_000,
                        self.absolute_expires_at,
                    )
                )
            )
        ):
            raise ValueError("invalid Priority candidate record")
        self.snapshot.validate_for_scope(self.scope)

    def positive_reference_expires_at(self, kind: str) -> int:
        if kind not in POSITIVE_REFERENCE_MAX_SECONDS:
            raise ValueError("invalid Priority candidate positive reference")
        return self.positive_references[_POSITIVE_REFERENCE_KINDS.index(kind)].expires_at

    def logical_expires_at(self) -> int:
        positive_expires_at = max(
            reference.expires_at for reference in self.positive_references
        )
        confirmed_expires_at = min(
            max(self.base_expires_at, positive_expires_at),
            self.absolute_expires_at,
        )
        if self.state == "provider_validation_grace":
            return min(confirmed_expires_at, self.grace_expires_at)
        return confirmed_expires_at

    def authority_state_at(self, current: int) -> str:
        if (
            type(current) is not int
            or not 0 <= current <= CANDIDATE_MAX_SAFE_INTEGER
        ):
            raise ValueError("invalid Priority candidate server time")
        if current >= self.logical_expires_at():
            return "expired"
        return self.state


@dataclass(frozen=True, slots=True)
class PriorityCandidatePage:
    records: tuple[PriorityCandidateRecord, ...]
    total: int
    offset: int
    next_offset: int | None
    mailbox_incomplete: bool
    user_incomplete: bool
    degraded: bool

    @property
    def incomplete(self) -> bool:
        return self.mailbox_incomplete or self.user_incomplete or self.degraded


def _digest(secret: str, info: bytes, value: bytes) -> str:
    key = derive_priority_hmac_key(secret, info)
    return hmac.new(key, value, hashlib.sha256).hexdigest()


def derive_candidate_scope_digest(secret: str, scope: PriorityCandidateScope) -> str:
    if not isinstance(scope, PriorityCandidateScope):
        raise ValueError("invalid Priority candidate scope")
    return _digest(secret, _SCOPE_HMAC_INFO, scope.canonical_bytes())


def derive_candidate_mailbox_scope_digest(
    secret: str, scope: PriorityCandidateMailboxScope
) -> str:
    if not isinstance(scope, PriorityCandidateMailboxScope):
        raise ValueError("invalid Priority candidate mailbox scope")
    return _digest(secret, _MAILBOX_SCOPE_HMAC_INFO, scope.canonical_bytes())


def derive_candidate_user_scope_digest(
    secret: str, scope: PriorityCandidateMailboxScope
) -> str:
    if not isinstance(scope, PriorityCandidateMailboxScope):
        raise ValueError("invalid Priority candidate mailbox scope")
    return _digest(secret, _USER_SCOPE_HMAC_INFO, scope.user_canonical_bytes())


def derive_candidate_identity_digest(secret: str, scope: PriorityCandidateScope) -> str:
    if not isinstance(scope, PriorityCandidateScope):
        raise ValueError("invalid Priority candidate scope")
    return _digest(secret, _IDENTITY_HMAC_INFO, scope.canonical_bytes())


def derive_candidate_namespace_scope_digest(
    secret: str, scope: PriorityCandidateScope
) -> str:
    if not isinstance(scope, PriorityCandidateScope):
        raise ValueError("invalid Priority candidate scope")
    return _digest(secret, _NAMESPACE_HMAC_INFO, scope.namespace_canonical_bytes())


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str):
    raise ValueError("invalid JSON constant")


def _conversation_to_wire(value: PriorityCandidateConversation) -> dict[str, object]:
    return {
        "conversationId": value.conversation_id,
        "authorityKind": value.authority_kind,
        "providerThreadId": value.provider_thread_id,
        "rfcRootMessageId": value.rfc_root_message_id,
        "rfcMessageId": value.rfc_message_id,
    }


def _render_to_wire(value: PriorityCandidateRender) -> dict[str, object]:
    return {
        "senderDisplay": value.sender_display,
        "senderAddress": value.sender_address,
        "subject": value.subject,
        "snippet": value.snippet,
        "createdAt": value.created_at,
        "unread": value.unread,
        "flagged": value.flagged,
    }


def _routing_to_wire(
    value: PriorityCandidateRouting | None,
) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "signal": value.signal,
        "uiSignal": value.ui_signal,
        "internalClassification": value.internal_classification,
        "category": value.category,
        "finalVisibility": value.final_visibility,
        "action": value.action,
        "v7FinalPriority": value.v7_final_priority,
        "noiseDisposition": value.noise_disposition,
        "noiseConfidence": value.noise_confidence,
        "noiseReasons": list(value.noise_reasons),
        "classifierVersion": value.classifier_version,
        "routingVersion": value.routing_version,
    }


def _record_to_wire(
    secret: str,
    record: PriorityCandidateRecord,
) -> dict[str, object]:
    scope = record.scope
    return {
        "schemaVersion": CANDIDATE_STORE_SCHEMA_VERSION,
        "scopeDigest": derive_candidate_scope_digest(secret, scope),
        "identityDigest": derive_candidate_identity_digest(secret, scope),
        "mailboxId": scope.mailbox_id,
        "mailboxAccountIdentity": scope.mailbox_account_identity,
        "provider": scope.provider,
        "identity": scope.identity.to_wire_dict(),
        "conversation": _conversation_to_wire(record.snapshot.conversation),
        "render": _render_to_wire(record.snapshot.render),
        "routingState": record.snapshot.routing_state,
        "routing": _routing_to_wire(record.snapshot.routing),
        "providerAuthority": {
            "folder": record.snapshot.provider_authority.folder,
            "labels": (
                list(record.snapshot.provider_authority.labels)
                if record.snapshot.provider_authority.labels
                else None
            ),
        },
        "providerObservedAt": record.provider_observed_at,
        "providerValidatedAt": record.provider_validated_at,
        "baseExpiresAt": record.base_expires_at,
        "absoluteExpiresAt": record.absolute_expires_at,
        "graceExpiresAt": record.grace_expires_at,
        "positiveReferences": {
            reference.kind: reference.expires_at
            for reference in record.positive_references
        },
        "state": record.state,
        "version": record.version,
        "updatedAt": record.updated_at,
    }


def _encode_wire(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(encoded.encode("ascii")) > CANDIDATE_MAX_SERIALIZED_RECORD_BYTES:
        raise ValueError("invalid Priority candidate record")
    return encoded


def _empty_references() -> tuple[PriorityCandidatePositiveReference, ...]:
    return tuple(
        PriorityCandidatePositiveReference(kind=kind, expires_at=0)
        for kind in _POSITIVE_REFERENCE_KINDS
    )


_ROOT_FIELDS = frozenset(
    {
        "schemaVersion",
        "scopeDigest",
        "identityDigest",
        "mailboxId",
        "mailboxAccountIdentity",
        "provider",
        "identity",
        "conversation",
        "render",
        "routingState",
        "routing",
        "providerAuthority",
        "providerObservedAt",
        "providerValidatedAt",
        "baseExpiresAt",
        "absoluteExpiresAt",
        "graceExpiresAt",
        "positiveReferences",
        "state",
        "version",
        "updatedAt",
    }
)
_CONVERSATION_FIELDS = frozenset(
    {
        "conversationId",
        "authorityKind",
        "providerThreadId",
        "rfcRootMessageId",
        "rfcMessageId",
    }
)
_RENDER_FIELDS = frozenset(
    {"senderDisplay", "senderAddress", "subject", "snippet", "createdAt", "unread", "flagged"}
)
_ROUTING_FIELDS = frozenset(
    {
        "signal",
        "uiSignal",
        "internalClassification",
        "category",
        "finalVisibility",
        "action",
        "v7FinalPriority",
        "noiseDisposition",
        "noiseConfidence",
        "noiseReasons",
        "classifierVersion",
        "routingVersion",
    }
)
_PROVIDER_AUTHORITY_FIELDS = frozenset({"folder", "labels"})


def _decode_candidate_record(
    value: object,
    *,
    secret: str,
    expected_mailbox_scope: PriorityCandidateMailboxScope,
    expected_member_digest: str | None = None,
) -> PriorityCandidateRecord | None:
    try:
        if (
            type(value) is not str
            or len(value.encode("utf-8"))
            > CANDIDATE_MAX_SERIALIZED_RECORD_BYTES
        ):
            return None
        payload = json.loads(
            value,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
        if type(payload) is not dict or set(payload) != _ROOT_FIELDS:
            return None
        conversation = payload["conversation"]
        render = payload["render"]
        routing_state = payload["routingState"]
        routing = payload["routing"]
        provider_authority = payload["providerAuthority"]
        references = payload["positiveReferences"]
        routing_valid = (
            routing_state == "unresolved" and routing is None
        ) or (
            routing_state == "ready"
            and type(routing) is dict
            and set(routing) == _ROUTING_FIELDS
            and type(routing["noiseReasons"]) is list
        )
        if (
            type(conversation) is not dict
            or set(conversation) != _CONVERSATION_FIELDS
            or type(render) is not dict
            or set(render) != _RENDER_FIELDS
            or not routing_valid
            or type(provider_authority) is not dict
            or set(provider_authority) != _PROVIDER_AUTHORITY_FIELDS
            or type(references) is not dict
            or set(references) != set(_POSITIVE_REFERENCE_KINDS)
            or (
                provider_authority["labels"] is not None
                and type(provider_authority["labels"]) is not list
            )
            or payload["schemaVersion"] != CANDIDATE_STORE_SCHEMA_VERSION
            or payload["mailboxId"] != expected_mailbox_scope.mailbox_id
            or payload["mailboxAccountIdentity"]
            != expected_mailbox_scope.mailbox_account_identity
            or payload["provider"] != expected_mailbox_scope.provider
        ):
            return None
        identity = parse_priority_message_identity(
            payload["identity"], expected_provider=expected_mailbox_scope.provider
        )
        scope = PriorityCandidateScope(
            workspace_id=expected_mailbox_scope.workspace_id,
            user_id=expected_mailbox_scope.user_id,
            mailbox_id=payload["mailboxId"],
            mailbox_account_identity=payload["mailboxAccountIdentity"],
            provider=payload["provider"],
            identity=identity,
        )
        snapshot = PriorityCandidateSnapshot(
            conversation=PriorityCandidateConversation(
                conversation_id=conversation["conversationId"],
                authority_kind=conversation["authorityKind"],
                provider_thread_id=conversation["providerThreadId"],
                rfc_root_message_id=conversation["rfcRootMessageId"],
                rfc_message_id=conversation["rfcMessageId"],
            ),
            render=PriorityCandidateRender(
                sender_display=render["senderDisplay"],
                sender_address=render["senderAddress"],
                subject=render["subject"],
                snippet=render["snippet"],
                created_at=render["createdAt"],
                unread=render["unread"],
                flagged=render["flagged"],
            ),
            routing_state=routing_state,
            routing=(
                PriorityCandidateRouting(
                    signal=routing["signal"],
                    ui_signal=routing["uiSignal"],
                    internal_classification=routing["internalClassification"],
                    category=routing["category"],
                    final_visibility=routing["finalVisibility"],
                    action=routing["action"],
                    v7_final_priority=routing["v7FinalPriority"],
                    noise_disposition=routing["noiseDisposition"],
                    noise_confidence=routing["noiseConfidence"],
                    noise_reasons=tuple(routing["noiseReasons"]),
                    classifier_version=routing["classifierVersion"],
                    routing_version=routing["routingVersion"],
                )
                if routing_state == "ready"
                else None
            ),
            provider_authority=PriorityCandidateProviderAuthority(
                folder=provider_authority["folder"],
                labels=tuple(provider_authority["labels"] or ()),
            ),
        )
        record = PriorityCandidateRecord(
            scope=scope,
            snapshot=snapshot,
            provider_observed_at=payload["providerObservedAt"],
            provider_validated_at=payload["providerValidatedAt"],
            base_expires_at=payload["baseExpiresAt"],
            absolute_expires_at=payload["absoluteExpiresAt"],
            grace_expires_at=payload["graceExpiresAt"],
            positive_references=tuple(
                PriorityCandidatePositiveReference(kind=kind, expires_at=references[kind])
                for kind in _POSITIVE_REFERENCE_KINDS
            ),
            state=payload["state"],
            version=payload["version"],
            updated_at=payload["updatedAt"],
        )
        scope_digest = derive_candidate_scope_digest(secret, scope)
        identity_digest = derive_candidate_identity_digest(secret, scope)
        member_digest = scope_digest
        if (
            type(payload["scopeDigest"]) is not str
            or _HEX_DIGEST_RE.fullmatch(payload["scopeDigest"]) is None
            or not hmac.compare_digest(payload["scopeDigest"], scope_digest)
            or type(payload["identityDigest"]) is not str
            or _HEX_DIGEST_RE.fullmatch(payload["identityDigest"]) is None
            or not hmac.compare_digest(payload["identityDigest"], identity_digest)
            or (
                expected_member_digest is not None
                and not hmac.compare_digest(expected_member_digest, member_digest)
            )
        ):
            return None
        return record
    except Exception:
        return None


_REFERENCE_REJECTED_SENTINEL = (
    "__cuevion_priority_candidate_reference_rejected__"
)

_UPSERT_CONFIRMED_SCRIPT = r"""
local function keyType(key)
  local value=redis.call('TYPE',key)
  if type(value)=='table' then return value['ok'] end
  return value
end
local expectedTypes={{KEYS[1],'string'},{KEYS[2],'zset'},{KEYS[3],'zset'},
  {KEYS[4],'zset'},{KEYS[5],'string'},{KEYS[6],'string'},{KEYS[7],'string'}}
for _,item in ipairs(expectedTypes) do
  local actual=keyType(item[1])
  if actual~='none' and actual~=item[2] then return ARGV[15] end
end
local invalidated=redis.call('GET',KEYS[7])
if invalidated then
  if invalidated~=ARGV[14] then return ARGV[15] end
  return ARGV[19]
end
for index=5,6 do
  local marker=redis.call('GET',KEYS[index])
  if marker and marker~=ARGV[14] then return ARGV[15] end
end
local current=redis.call('GET',KEYS[1])
if ARGV[1]==ARGV[2] then
  if current then return ARGV[16] end
else
  if current~=ARGV[1] then return ARGV[16] end
end
local clock=redis.call('TIME')
local seconds=tonumber(clock[1]);local micros=tonumber(clock[2])
if not seconds or not micros then return ARGV[15] end
local now=seconds*1000+math.floor(micros/1000)
if now<0 or now>tonumber(ARGV[13]) then return ARGV[15] end
local scores={redis.call('ZSCORE',KEYS[2],ARGV[5]),
  redis.call('ZSCORE',KEYS[3],ARGV[5]),redis.call('ZSCORE',KEYS[4],ARGV[5])}
if current then
  if not scores[1] or not scores[2] or not scores[3] or
    tonumber(scores[1])~=tonumber(scores[2]) or
    tonumber(scores[1])~=tonumber(scores[3]) or
    tonumber(scores[1])~=tonumber(ARGV[20]) then return ARGV[15] end
else
  redis.call('ZREMRANGEBYSCORE',KEYS[2],'-inf',now)
  redis.call('ZREMRANGEBYSCORE',KEYS[3],'-inf',now)
  redis.call('ZREMRANGEBYSCORE',KEYS[4],'-inf',now)
  if redis.call('ZSCORE',KEYS[2],ARGV[5]) or
    redis.call('ZSCORE',KEYS[3],ARGV[5]) or
    redis.call('ZSCORE',KEYS[4],ARGV[5]) then return ARGV[15] end
  if redis.call('ZCARD',KEYS[2])>=tonumber(ARGV[10]) then
    redis.call('SET',KEYS[5],ARGV[14],'EX',ARGV[9]);return ARGV[17]
  end
  if redis.call('ZCARD',KEYS[3])>=tonumber(ARGV[11]) then
    redis.call('SET',KEYS[6],ARGV[14],'EX',ARGV[9]);return ARGV[18]
  end
end
local ok,record=pcall(cjson.decode,ARGV[4])
if not ok or type(record)~='table' or
  record['schemaVersion']~=tonumber(ARGV[6]) then return ARGV[15] end
local existingVersion=0
if current then
  local currentOk,existing=pcall(cjson.decode,current)
  if not currentOk or type(existing)~='table' or
    type(existing['version'])~='number' or existing['version']%1~=0 or
    existing['version']<1 or existing['version']>tonumber(ARGV[13]) then
    return ARGV[15]
  end
  existingVersion=existing['version']
end
if existingVersion~=tonumber(ARGV[3]) then return ARGV[16] end
local base=now+tonumber(ARGV[7])*1000
local absolute=now+tonumber(ARGV[8])*1000
if base>tonumber(ARGV[13]) or absolute>tonumber(ARGV[13]) then return ARGV[15] end
local references=record['positiveReferences']
if type(references)~='table' then return ARGV[15] end
local positive=0
for _,kind in ipairs({'manual_priority','waiting','returned_reply',
  'semantic_promotion','collaboration_priority','assigned_review'}) do
  local expires=references[kind]
  if type(expires)~='number' or expires%1~=0 or expires<0 or
    expires>tonumber(ARGV[13]) then return ARGV[15] end
  if expires<=now then expires=0 elseif expires>absolute then expires=absolute end
  references[kind]=expires
  if expires>positive then positive=expires end
end
record['providerObservedAt']=now;record['providerValidatedAt']=now
record['baseExpiresAt']=base;record['absoluteExpiresAt']=absolute
record['graceExpiresAt']=0;record['state']='provider_confirmed'
record['version']=existingVersion+1;record['updatedAt']=now
local expires=math.max(base,positive)
if expires>absolute then expires=absolute end
local encoded=cjson.encode(record)
if string.len(encoded)>tonumber(ARGV[12]) then return ARGV[15] end
local ttl=math.ceil((expires-now)/1000)
if ttl<1 then return ARGV[15] end
redis.call('SET',KEYS[1],encoded,'EX',ttl)
for index=2,4 do
  redis.call('ZADD',KEYS[index],expires,ARGV[5])
  redis.call('EXPIRE',KEYS[index],ARGV[9])
end
return encoded
"""

_SET_POSITIVE_REFERENCE_SCRIPT = r"""
local current=redis.call('GET',KEYS[1])
if not current then return ARGV[12] end
if current~=ARGV[1] then return ARGV[11] end
local scores={redis.call('ZSCORE',KEYS[2],ARGV[6]),
  redis.call('ZSCORE',KEYS[3],ARGV[6]),redis.call('ZSCORE',KEYS[4],ARGV[6])}
if not scores[1] or not scores[2] or not scores[3] or
  tonumber(scores[1])~=tonumber(scores[2]) or
  tonumber(scores[1])~=tonumber(scores[3]) or
  tonumber(scores[1])~=tonumber(ARGV[14]) then return ARGV[10] end
local ok,record=pcall(cjson.decode,current)
if not ok or type(record)~='table' or type(record['version'])~='number' or
  record['version']~=tonumber(ARGV[2]) or
  type(record['positiveReferences'])~='table' then return ARGV[10] end
local remaining=tonumber(ARGV[4]);local maximum=tonumber(ARGV[5])
if not remaining or remaining<0 or remaining%1~=0 or remaining>maximum then
  return ARGV[10]
end
local clock=redis.call('TIME');local seconds=tonumber(clock[1])
local micros=tonumber(clock[2])
if not seconds or not micros then return ARGV[10] end
local now=seconds*1000+math.floor(micros/1000)
if now<0 or now>tonumber(ARGV[9]) then return ARGV[10] end
if remaining>0 and (record['state']~='provider_confirmed' or
  type(record['baseExpiresAt'])~='number' or now>=record['baseExpiresAt']) then
  return ARGV[13]
end
local references=record['positiveReferences']
if references[ARGV[3]]==nil then return ARGV[10] end
local absolute=record['absoluteExpiresAt']
if type(absolute)~='number' or absolute%1~=0 or absolute<=now or
  absolute>tonumber(ARGV[9]) then return ARGV[13] end
if remaining==0 then references[ARGV[3]]=0
else
  local requested=now+remaining*1000
  if requested>absolute then requested=absolute end
  references[ARGV[3]]=requested
end
local positive=0
for _,kind in ipairs({'manual_priority','waiting','returned_reply',
  'semantic_promotion','collaboration_priority','assigned_review'}) do
  local expires=references[kind]
  if type(expires)~='number' or expires%1~=0 or expires<0 or expires>absolute then
    return ARGV[10]
  end
  if expires<=now then expires=0;references[kind]=0 end
  if expires>positive then positive=expires end
end
local base=record['baseExpiresAt']
if type(base)~='number' or base%1~=0 then return ARGV[10] end
local expires=math.max(base,positive)
if expires>absolute then expires=absolute end
if record['state']=='provider_validation_grace' then
  local grace=record['graceExpiresAt']
  if type(grace)~='number' or grace%1~=0 then return ARGV[10] end
  if grace<expires then expires=grace end
elseif record['state']~='provider_confirmed' then return ARGV[10] end
if expires<=now then
  redis.call('DEL',KEYS[1])
  for index=2,4 do redis.call('ZREM',KEYS[index],ARGV[6]) end
  return ARGV[12]
end
record['version']=record['version']+1;record['updatedAt']=now
local encoded=cjson.encode(record)
if string.len(encoded)>tonumber(ARGV[8]) then return ARGV[10] end
local ttl=math.ceil((expires-now)/1000)
redis.call('SET',KEYS[1],encoded,'EX',ttl)
for index=2,4 do
  redis.call('ZADD',KEYS[index],expires,ARGV[6])
  redis.call('EXPIRE',KEYS[index],ARGV[7])
end
return encoded
"""

_MARK_VALIDATION_FAILURE_SCRIPT = r"""
local current=redis.call('GET',KEYS[1])
if not current then return ARGV[10] end
if current~=ARGV[1] then return ARGV[9] end
local scores={redis.call('ZSCORE',KEYS[2],ARGV[3]),
  redis.call('ZSCORE',KEYS[3],ARGV[3]),redis.call('ZSCORE',KEYS[4],ARGV[3])}
if not scores[1] or not scores[2] or not scores[3] or
  tonumber(scores[1])~=tonumber(scores[2]) or
  tonumber(scores[1])~=tonumber(scores[3]) or
  tonumber(scores[1])~=tonumber(ARGV[11]) then return ARGV[8] end
local ok,record=pcall(cjson.decode,current)
if not ok or type(record)~='table' or type(record['version'])~='number' or
  record['version']~=tonumber(ARGV[2]) or
  type(record['positiveReferences'])~='table' then return ARGV[8] end
local clock=redis.call('TIME');local seconds=tonumber(clock[1])
local micros=tonumber(clock[2])
if not seconds or not micros then return ARGV[8] end
local now=seconds*1000+math.floor(micros/1000)
if now<0 or now>tonumber(ARGV[7]) then return ARGV[8] end
local validated=record['providerValidatedAt'];local absolute=record['absoluteExpiresAt']
local base=record['baseExpiresAt']
if type(validated)~='number' or validated%1~=0 or
  type(absolute)~='number' or absolute%1~=0 or
  type(base)~='number' or base%1~=0 then return ARGV[8] end
local positive=0
for _,kind in ipairs({'manual_priority','waiting','returned_reply',
  'semantic_promotion','collaboration_priority','assigned_review'}) do
  local expires=record['positiveReferences'][kind]
  if type(expires)~='number' or expires%1~=0 or expires<0 or expires>absolute then
    return ARGV[8]
  end
  if expires>positive then positive=expires end
end
local grace=validated+tonumber(ARGV[4])*1000
local expires=math.min(math.max(base,positive),absolute,grace)
if expires<=now then
  redis.call('DEL',KEYS[1])
  for index=2,4 do redis.call('ZREM',KEYS[index],ARGV[3]) end
  return ARGV[10]
end
record['state']='provider_validation_grace';record['graceExpiresAt']=grace
record['version']=record['version']+1;record['updatedAt']=now
local encoded=cjson.encode(record)
if string.len(encoded)>tonumber(ARGV[6]) then return ARGV[8] end
local ttl=math.ceil((expires-now)/1000)
redis.call('SET',KEYS[1],encoded,'EX',ttl)
for index=2,4 do
  redis.call('ZADD',KEYS[index],expires,ARGV[3])
  redis.call('EXPIRE',KEYS[index],ARGV[5])
end
return encoded
"""

_READ_ONE_SCRIPT = r"""
local function keyType(key)
  local value=redis.call('TYPE',key)
  if type(value)=='table' then return value['ok'] end
  return value
end
if (keyType(KEYS[1])~='none' and keyType(KEYS[1])~='string') or
  (keyType(KEYS[2])~='none' and keyType(KEYS[2])~='zset') or
  (keyType(KEYS[3])~='none' and keyType(KEYS[3])~='zset') or
  (keyType(KEYS[4])~='none' and keyType(KEYS[4])~='zset') then return {ARGV[2]} end
local clock=redis.call('TIME');local seconds=tonumber(clock[1])
local micros=tonumber(clock[2])
if not seconds or not micros then return {ARGV[2]} end
local now=seconds*1000+math.floor(micros/1000)
local value=redis.call('GET',KEYS[1])
local scores={redis.call('ZSCORE',KEYS[2],ARGV[1]),
  redis.call('ZSCORE',KEYS[3],ARGV[1]),redis.call('ZSCORE',KEYS[4],ARGV[1])}
if not value then
  if not scores[1] and not scores[2] and not scores[3] then return {now,ARGV[3]} end
  if scores[1] and scores[2] and scores[3] and tonumber(scores[1])<=now and
    tonumber(scores[2])<=now and tonumber(scores[3])<=now then return {now,ARGV[3]} end
  return {ARGV[2]}
end
if not scores[1] or not scores[2] or not scores[3] or
  tonumber(scores[1])~=tonumber(scores[2]) or
  tonumber(scores[1])~=tonumber(scores[3]) then return {ARGV[2]} end
return {now,value,scores[1]}
"""

_READ_MAILBOX_PAGE_SCRIPT = r"""
local function keyType(key)
  local value=redis.call('TYPE',key)
  if type(value)=='table' then return value['ok'] end
  return value
end
if (keyType(KEYS[1])~='none' and keyType(KEYS[1])~='zset') or
  (keyType(KEYS[2])~='none' and keyType(KEYS[2])~='string') or
  (keyType(KEYS[3])~='none' and keyType(KEYS[3])~='string') then
  return {ARGV[4]}
end
local mailboxMarker=redis.call('GET',KEYS[2])
local userMarker=redis.call('GET',KEYS[3])
if (mailboxMarker and mailboxMarker~=ARGV[3]) or
  (userMarker and userMarker~=ARGV[3]) then return {ARGV[4]} end
local clock=redis.call('TIME');local seconds=tonumber(clock[1])
local micros=tonumber(clock[2])
if not seconds or not micros then return {ARGV[4]} end
local now=seconds*1000+math.floor(micros/1000)
local minimum=now+1
local total=redis.call('ZCOUNT',KEYS[1],minimum,'+inf')
local values=redis.call('ZRANGEBYSCORE',KEYS[1],minimum,'+inf','WITHSCORES',
  'LIMIT',ARGV[1],ARGV[2])
local result={now,total,mailboxMarker and 1 or 0,userMarker and 1 or 0}
for _,value in ipairs(values) do result[#result+1]=value end
return result
"""

_REMOVE_CANDIDATE_SCRIPT = r"""
local function keyType(key)
  local value=redis.call('TYPE',key)
  if type(value)=='table' then return value['ok'] end
  return value
end
if (keyType(KEYS[1])~='none' and keyType(KEYS[1])~='string') or
  (keyType(KEYS[2])~='none' and keyType(KEYS[2])~='zset') or
  (keyType(KEYS[3])~='none' and keyType(KEYS[3])~='zset') or
  (keyType(KEYS[4])~='none' and keyType(KEYS[4])~='zset') then return -1 end
local clock=redis.call('TIME');local seconds=tonumber(clock[1])
local micros=tonumber(clock[2])
if not seconds or not micros then return -1 end
local now=seconds*1000+math.floor(micros/1000)
local value=redis.call('GET',KEYS[1])
local scores={redis.call('ZSCORE',KEYS[2],ARGV[1]),
  redis.call('ZSCORE',KEYS[3],ARGV[1]),redis.call('ZSCORE',KEYS[4],ARGV[1])}
if not value then
  if not scores[1] and not scores[2] and not scores[3] then return 0 end
  if scores[1] and scores[2] and scores[3] and tonumber(scores[1])<=now and
    tonumber(scores[2])<=now and tonumber(scores[3])<=now then
    for index=2,4 do redis.call('ZREM',KEYS[index],ARGV[1]) end
    return 0
  end
  return -1
end
if not scores[1] or not scores[2] or not scores[3] or
  tonumber(scores[1])~=tonumber(scores[2]) or
  tonumber(scores[1])~=tonumber(scores[3]) then return -1 end
redis.call('DEL',KEYS[1])
for index=2,4 do redis.call('ZREM',KEYS[index],ARGV[1]) end
return 1
"""

_INVALIDATE_IMAP_NAMESPACE_SCRIPT = r"""
local function keyType(key)
  local value=redis.call('TYPE',key)
  if type(value)=='table' then return value['ok'] end
  return value
end
if (keyType(KEYS[1])~='none' and keyType(KEYS[1])~='zset') or
  (keyType(KEYS[2])~='none' and keyType(KEYS[2])~='zset') or
  (keyType(KEYS[3])~='none' and keyType(KEYS[3])~='zset') or
  (keyType(KEYS[4])~='none' and keyType(KEYS[4])~='string') or
  (keyType(KEYS[5])~='none' and keyType(KEYS[5])~='string') or
  (keyType(KEYS[6])~='none' and keyType(KEYS[6])~='string') then return -1 end
for index=4,6 do
  local marker=redis.call('GET',KEYS[index])
  if marker and marker~=ARGV[3] then return -1 end
end
local count=redis.call('ZCARD',KEYS[3])
if count>tonumber(ARGV[2]) then
  redis.call('SET',KEYS[5],ARGV[3],'EX',ARGV[4])
  redis.call('SET',KEYS[6],ARGV[3],'EX',ARGV[4])
  return -1
end
local members=redis.call('ZRANGE',KEYS[3],0,-1)
local clock=redis.call('TIME');local seconds=tonumber(clock[1])
local micros=tonumber(clock[2])
if not seconds or not micros then return -1 end
local now=seconds*1000+math.floor(micros/1000)
for _,member in ipairs(members) do
  if string.len(member)~=64 or not string.match(member,'^[0-9a-f]+$') then return -1 end
  local value=redis.call('GET',ARGV[1]..member)
  local mailboxScore=redis.call('ZSCORE',KEYS[1],member)
  local userScore=redis.call('ZSCORE',KEYS[2],member)
  local namespaceScore=redis.call('ZSCORE',KEYS[3],member)
  if value and (not mailboxScore or not userScore or not namespaceScore or
    tonumber(mailboxScore)~=tonumber(userScore) or
    tonumber(mailboxScore)~=tonumber(namespaceScore)) then return -1 end
  if not value and ((mailboxScore and tonumber(mailboxScore)>now) or
    (userScore and tonumber(userScore)>now) or
    (namespaceScore and tonumber(namespaceScore)>now)) then return -1 end
end
for _,member in ipairs(members) do
  redis.call('DEL',ARGV[1]..member)
  redis.call('ZREM',KEYS[1],member);redis.call('ZREM',KEYS[2],member)
end
redis.call('DEL',KEYS[3])
redis.call('SET',KEYS[4],ARGV[3],'EX',ARGV[4])
return count
"""

_CLEAR_INCOMPLETE_SCRIPT = r"""
local value=redis.call('GET',KEYS[1])
if not value then return 0 end
if value~=ARGV[1] then return -1 end
return redis.call('DEL',KEYS[1])
"""


def _safe_redis_integer(value: object) -> int | None:
    if type(value) is int:
        parsed = value
    elif type(value) is float and math.isfinite(value) and value.is_integer():
        parsed = int(value)
    elif type(value) is str and re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.0+)?", value):
        try:
            parsed = int(value.split(".", 1)[0])
        except ValueError:
            return None
    else:
        return None
    return parsed if 0 <= parsed <= CANDIDATE_MAX_SAFE_INTEGER else None


def _template_payload(
    secret: str,
    scope: PriorityCandidateScope,
    snapshot: PriorityCandidateSnapshot,
    references: tuple[PriorityCandidatePositiveReference, ...],
) -> dict[str, object]:
    snapshot.validate_for_scope(scope)
    if (
        type(references) is not tuple
        or tuple(reference.kind for reference in references)
        != _POSITIVE_REFERENCE_KINDS
    ):
        raise ValueError("invalid Priority candidate positive references")
    return {
        "schemaVersion": CANDIDATE_STORE_SCHEMA_VERSION,
        "scopeDigest": derive_candidate_scope_digest(secret, scope),
        "identityDigest": derive_candidate_identity_digest(secret, scope),
        "mailboxId": scope.mailbox_id,
        "mailboxAccountIdentity": scope.mailbox_account_identity,
        "provider": scope.provider,
        "identity": scope.identity.to_wire_dict(),
        "conversation": _conversation_to_wire(snapshot.conversation),
        "render": _render_to_wire(snapshot.render),
        "routingState": snapshot.routing_state,
        "routing": _routing_to_wire(snapshot.routing),
        "providerAuthority": {
            "folder": snapshot.provider_authority.folder,
            "labels": (
                list(snapshot.provider_authority.labels)
                if snapshot.provider_authority.labels
                else None
            ),
        },
        "providerObservedAt": 0,
        "providerValidatedAt": 0,
        "baseExpiresAt": 0,
        "absoluteExpiresAt": 0,
        "graceExpiresAt": 0,
        "positiveReferences": {
            reference.kind: reference.expires_at for reference in references
        },
        "state": "provider_confirmed",
        "version": 0,
        "updatedAt": 0,
    }


class PriorityCandidateStore:
    """Strict candidate records plus bounded mailbox/user/namespace indexes."""

    __slots__ = ("_transport", "_hmac_secret")

    def __init__(self, command_transport: CommandTransport, *, hmac_secret: str) -> None:
        if not callable(command_transport):
            raise ValueError("invalid Priority candidate command transport")
        derive_priority_hmac_key(hmac_secret, _SCOPE_HMAC_INFO)
        derive_priority_hmac_key(hmac_secret, _MAILBOX_SCOPE_HMAC_INFO)
        derive_priority_hmac_key(hmac_secret, _USER_SCOPE_HMAC_INFO)
        derive_priority_hmac_key(hmac_secret, _IDENTITY_HMAC_INFO)
        derive_priority_hmac_key(hmac_secret, _NAMESPACE_HMAC_INFO)
        self._transport = command_transport
        self._hmac_secret = hmac_secret

    def _command(self, command: list[object]) -> object:
        try:
            payload = self._transport(command)
        except Exception:
            raise CandidateStoreUnavailable() from None
        if type(payload) is not dict or set(payload) != {"result"}:
            raise CandidateStoreUnavailable()
        return payload["result"]

    def _mailbox_keys(
        self, scope: PriorityCandidateMailboxScope
    ) -> dict[str, str]:
        mailbox_digest = derive_candidate_mailbox_scope_digest(
            self._hmac_secret, scope
        )
        user_digest = derive_candidate_user_scope_digest(self._hmac_secret, scope)
        return {
            "mailbox_index": f"{_CANDIDATE_KEY_PREFIX}index:mailbox:{mailbox_digest}",
            "user_index": f"{_CANDIDATE_KEY_PREFIX}index:user:{user_digest}",
            "mailbox_incomplete": (
                f"{_CANDIDATE_KEY_PREFIX}incomplete:mailbox:{mailbox_digest}"
            ),
            "user_incomplete": (
                f"{_CANDIDATE_KEY_PREFIX}incomplete:user:{user_digest}"
            ),
        }

    def _scope_keys(self, scope: PriorityCandidateScope) -> dict[str, str]:
        scope_digest = derive_candidate_scope_digest(self._hmac_secret, scope)
        namespace_digest = derive_candidate_namespace_scope_digest(
            self._hmac_secret, scope
        )
        keys = self._mailbox_keys(scope.mailbox_scope())
        keys.update(
            {
                "member": scope_digest,
                "record": f"{_CANDIDATE_KEY_PREFIX}record:{scope_digest}",
                "namespace_index": (
                    f"{_CANDIDATE_KEY_PREFIX}index:namespace:{namespace_digest}"
                ),
                "namespace_invalid": (
                    f"{_CANDIDATE_KEY_PREFIX}invalid:namespace:{namespace_digest}"
                ),
            }
        )
        return keys

    def _decode_exact(
        self,
        value: object,
        scope: PriorityCandidateScope,
    ) -> PriorityCandidateRecord:
        record = _decode_candidate_record(
            value,
            secret=self._hmac_secret,
            expected_mailbox_scope=scope.mailbox_scope(),
            expected_member_digest=derive_candidate_scope_digest(
                self._hmac_secret, scope
            ),
        )
        if record is None or record.scope != scope:
            raise CandidateStoreUnavailable()
        return record

    def upsert_confirmed(
        self,
        scope: PriorityCandidateScope,
        snapshot: PriorityCandidateSnapshot,
        *,
        expected_version: int,
    ) -> PriorityCandidateRecord:
        if (
            not isinstance(scope, PriorityCandidateScope)
            or not isinstance(snapshot, PriorityCandidateSnapshot)
            or type(expected_version) is not int
            or not 0 <= expected_version < CANDIDATE_MAX_SAFE_INTEGER
        ):
            raise ValueError("invalid Priority candidate write")
        snapshot.validate_for_scope(scope)
        keys = self._scope_keys(scope)
        current = self._command(["GET", keys["record"]])
        if current is None:
            if expected_version != 0:
                raise CandidateVersionConflict()
            references = _empty_references()
            expected_raw = _MISSING_SENTINEL
            expected_existing_expiry = 0
        else:
            existing = self._decode_exact(current, scope)
            if existing.version != expected_version:
                raise CandidateVersionConflict()
            references = existing.positive_references
            expected_raw = current
            expected_existing_expiry = existing.logical_expires_at()
        template = _encode_wire(
            _template_payload(self._hmac_secret, scope, snapshot, references)
        )
        result = self._command(
            [
                "EVAL",
                _UPSERT_CONFIRMED_SCRIPT,
                7,
                keys["record"],
                keys["mailbox_index"],
                keys["user_index"],
                keys["namespace_index"],
                keys["mailbox_incomplete"],
                keys["user_incomplete"],
                keys["namespace_invalid"],
                expected_raw,
                _MISSING_SENTINEL,
                expected_version,
                template,
                keys["member"],
                CANDIDATE_STORE_SCHEMA_VERSION,
                CANDIDATE_BASE_TTL_SECONDS,
                CANDIDATE_ABSOLUTE_TTL_SECONDS,
                CANDIDATE_INDEX_TTL_SECONDS,
                CANDIDATE_MAX_MAILBOX_RECORDS,
                CANDIDATE_MAX_USER_RECORDS,
                CANDIDATE_MAX_SERIALIZED_RECORD_BYTES,
                CANDIDATE_MAX_SAFE_INTEGER,
                _INCOMPLETE_VALUE,
                _CORRUPT_SENTINEL,
                _CONFLICT_SENTINEL,
                _MAILBOX_OVERFLOW_SENTINEL,
                _USER_OVERFLOW_SENTINEL,
                _NAMESPACE_INVALIDATED_SENTINEL,
                expected_existing_expiry,
            ]
        )
        if result == _CONFLICT_SENTINEL:
            raise CandidateVersionConflict()
        if result == _MAILBOX_OVERFLOW_SENTINEL:
            raise CandidateCapacityExceeded("mailbox")
        if result == _USER_OVERFLOW_SENTINEL:
            raise CandidateCapacityExceeded("user")
        if result == _NAMESPACE_INVALIDATED_SENTINEL:
            raise CandidateNamespaceInvalidated()
        if result == _CORRUPT_SENTINEL:
            raise CandidateStoreUnavailable()
        record = self._decode_exact(result, scope)
        if (
            record.version != expected_version + 1
            or record.state != "provider_confirmed"
            or record.provider_observed_at != record.updated_at
        ):
            raise CandidateStoreUnavailable()
        return record

    def read_candidate(
        self, scope: PriorityCandidateScope
    ) -> PriorityCandidateRecord | None:
        if not isinstance(scope, PriorityCandidateScope):
            raise ValueError("invalid Priority candidate scope")
        keys = self._scope_keys(scope)
        result = self._command(
            [
                "EVAL",
                _READ_ONE_SCRIPT,
                4,
                keys["record"],
                keys["mailbox_index"],
                keys["user_index"],
                keys["namespace_index"],
                keys["member"],
                _CORRUPT_SENTINEL,
                _MISSING_SENTINEL,
            ]
        )
        if type(result) is not list or not result or result == [_CORRUPT_SENTINEL]:
            raise CandidateStoreUnavailable()
        if len(result) == 2 and result[1] == _MISSING_SENTINEL:
            current = _safe_redis_integer(result[0])
            if current is None:
                raise CandidateStoreUnavailable()
            return None
        if len(result) != 3:
            raise CandidateStoreUnavailable()
        current = _safe_redis_integer(result[0])
        score = _safe_redis_integer(result[2])
        if current is None or score is None:
            raise CandidateStoreUnavailable()
        record = self._decode_exact(result[1], scope)
        if score != record.logical_expires_at():
            raise CandidateStoreUnavailable()
        return None if record.authority_state_at(current) == "expired" else record

    def read_mailbox_page(
        self,
        scope: PriorityCandidateMailboxScope,
        *,
        offset: int = 0,
        limit: int = CANDIDATE_MAX_PAGE_RECORDS,
    ) -> PriorityCandidatePage:
        if (
            not isinstance(scope, PriorityCandidateMailboxScope)
            or type(offset) is not int
            or not 0 <= offset <= CANDIDATE_MAX_PAGE_OFFSET
            or type(limit) is not int
            or not 1 <= limit <= CANDIDATE_MAX_PAGE_RECORDS
        ):
            raise ValueError("invalid Priority candidate page")
        keys = self._mailbox_keys(scope)
        result = self._command(
            [
                "EVAL",
                _READ_MAILBOX_PAGE_SCRIPT,
                3,
                keys["mailbox_index"],
                keys["mailbox_incomplete"],
                keys["user_incomplete"],
                offset,
                limit,
                _INCOMPLETE_VALUE,
                _CORRUPT_SENTINEL,
            ]
        )
        if (
            type(result) is not list
            or not result
            or result == [_CORRUPT_SENTINEL]
            or len(result) < 4
            or (len(result) - 4) % 2 != 0
        ):
            raise CandidateStoreUnavailable()
        current = _safe_redis_integer(result[0])
        total = _safe_redis_integer(result[1])
        if (
            current is None
            or total is None
            or result[2] not in {0, 1}
            or result[3] not in {0, 1}
        ):
            raise CandidateStoreUnavailable()
        members = result[4::2]
        scores = result[5::2]
        if (
            len(members) > limit
            or any(
                type(member) is not str or _HEX_DIGEST_RE.fullmatch(member) is None
                for member in members
            )
            or len(set(members)) != len(members)
        ):
            raise CandidateStoreUnavailable()
        values = self._command(
            [
                "MGET",
                *(f"{_CANDIDATE_KEY_PREFIX}record:{member}" for member in members),
            ]
        ) if members else []
        if type(values) is not list or len(values) != len(members):
            raise CandidateStoreUnavailable()
        records: list[PriorityCandidateRecord] = []
        for member, score_value, value in zip(members, scores, values, strict=True):
            score = _safe_redis_integer(score_value)
            record = _decode_candidate_record(
                value,
                secret=self._hmac_secret,
                expected_mailbox_scope=scope,
                expected_member_digest=member,
            )
            if (
                score is None
                or record is None
                or score != record.logical_expires_at()
                or record.authority_state_at(current) == "expired"
            ):
                raise CandidateStoreUnavailable()
            records.append(record)
        next_offset = offset + len(records)
        return PriorityCandidatePage(
            records=tuple(records),
            total=total,
            offset=offset,
            next_offset=next_offset if next_offset < total else None,
            mailbox_incomplete=result[2] == 1,
            user_incomplete=result[3] == 1,
            degraded=any(
                record.state == "provider_validation_grace" for record in records
            ),
        )

    def set_positive_reference(
        self,
        scope: PriorityCandidateScope,
        *,
        reference_kind: str,
        remaining_lifetime_seconds: int,
        expected_version: int,
    ) -> PriorityCandidateRecord | None:
        maximum = POSITIVE_REFERENCE_MAX_SECONDS.get(reference_kind)
        if (
            not isinstance(scope, PriorityCandidateScope)
            or maximum is None
            or type(remaining_lifetime_seconds) is not int
            or not 0 <= remaining_lifetime_seconds <= maximum
            or type(expected_version) is not int
            or not 1 <= expected_version < CANDIDATE_MAX_SAFE_INTEGER
        ):
            raise ValueError("invalid Priority candidate positive reference")
        keys = self._scope_keys(scope)
        current = self._command(["GET", keys["record"]])
        if current is None:
            raise CandidateReferenceRejected()
        existing = self._decode_exact(current, scope)
        if existing.version != expected_version:
            raise CandidateVersionConflict()
        result = self._command(
            [
                "EVAL",
                _SET_POSITIVE_REFERENCE_SCRIPT,
                4,
                keys["record"],
                keys["mailbox_index"],
                keys["user_index"],
                keys["namespace_index"],
                current,
                expected_version,
                reference_kind,
                remaining_lifetime_seconds,
                maximum,
                keys["member"],
                CANDIDATE_INDEX_TTL_SECONDS,
                CANDIDATE_MAX_SERIALIZED_RECORD_BYTES,
                CANDIDATE_MAX_SAFE_INTEGER,
                _CORRUPT_SENTINEL,
                _CONFLICT_SENTINEL,
                _MISSING_SENTINEL,
                _REFERENCE_REJECTED_SENTINEL,
                existing.logical_expires_at(),
            ]
        )
        if result == _CONFLICT_SENTINEL:
            raise CandidateVersionConflict()
        if result == _REFERENCE_REJECTED_SENTINEL:
            raise CandidateReferenceRejected()
        if result == _CORRUPT_SENTINEL:
            raise CandidateStoreUnavailable()
        if result == _MISSING_SENTINEL:
            return None
        record = self._decode_exact(result, scope)
        if record.version != expected_version + 1:
            raise CandidateStoreUnavailable()
        return record

    def mark_provider_validation_failure(
        self,
        scope: PriorityCandidateScope,
        *,
        expected_version: int,
    ) -> PriorityCandidateRecord | None:
        if (
            not isinstance(scope, PriorityCandidateScope)
            or type(expected_version) is not int
            or not 1 <= expected_version < CANDIDATE_MAX_SAFE_INTEGER
        ):
            raise ValueError("invalid Priority candidate validation failure")
        keys = self._scope_keys(scope)
        current = self._command(["GET", keys["record"]])
        if current is None:
            return None
        existing = self._decode_exact(current, scope)
        if existing.version != expected_version:
            raise CandidateVersionConflict()
        result = self._command(
            [
                "EVAL",
                _MARK_VALIDATION_FAILURE_SCRIPT,
                4,
                keys["record"],
                keys["mailbox_index"],
                keys["user_index"],
                keys["namespace_index"],
                current,
                expected_version,
                keys["member"],
                CANDIDATE_PROVIDER_FAILURE_GRACE_SECONDS,
                CANDIDATE_INDEX_TTL_SECONDS,
                CANDIDATE_MAX_SERIALIZED_RECORD_BYTES,
                CANDIDATE_MAX_SAFE_INTEGER,
                _CORRUPT_SENTINEL,
                _CONFLICT_SENTINEL,
                _MISSING_SENTINEL,
                existing.logical_expires_at(),
            ]
        )
        if result == _CONFLICT_SENTINEL:
            raise CandidateVersionConflict()
        if result == _CORRUPT_SENTINEL:
            raise CandidateStoreUnavailable()
        if result == _MISSING_SENTINEL:
            return None
        record = self._decode_exact(result, scope)
        if (
            record.version != expected_version + 1
            or record.state != "provider_validation_grace"
        ):
            raise CandidateStoreUnavailable()
        return record

    def remove_candidate(self, scope: PriorityCandidateScope) -> bool:
        if not isinstance(scope, PriorityCandidateScope):
            raise ValueError("invalid Priority candidate scope")
        keys = self._scope_keys(scope)
        result = self._command(
            [
                "EVAL",
                _REMOVE_CANDIDATE_SCRIPT,
                4,
                keys["record"],
                keys["mailbox_index"],
                keys["user_index"],
                keys["namespace_index"],
                keys["member"],
            ]
        )
        if type(result) is not int or type(result) is bool or result not in {0, 1}:
            raise CandidateStoreUnavailable()
        return result == 1

    def invalidate_imap_namespace(
        self,
        scope: PriorityCandidateMailboxScope,
        *,
        provider_folder: str,
        uid_validity: str,
    ) -> int:
        if not isinstance(scope, PriorityCandidateMailboxScope) or scope.provider != "custom_imap":
            raise ValueError("invalid Priority candidate IMAP namespace")
        identity = PriorityMessageIdentity(
            provider="custom_imap",
            provider_folder=provider_folder,
            uid_validity=uid_validity,
            imap_uid="1",
        )
        candidate_scope = PriorityCandidateScope(
            workspace_id=scope.workspace_id,
            user_id=scope.user_id,
            mailbox_id=scope.mailbox_id,
            mailbox_account_identity=scope.mailbox_account_identity,
            provider=scope.provider,
            identity=identity,
        )
        keys = self._scope_keys(candidate_scope)
        result = self._command(
            [
                "EVAL",
                _INVALIDATE_IMAP_NAMESPACE_SCRIPT,
                6,
                keys["mailbox_index"],
                keys["user_index"],
                keys["namespace_index"],
                keys["namespace_invalid"],
                keys["mailbox_incomplete"],
                keys["user_incomplete"],
                f"{_CANDIDATE_KEY_PREFIX}record:",
                CANDIDATE_MAX_MAILBOX_RECORDS,
                _INCOMPLETE_VALUE,
                CANDIDATE_INDEX_TTL_SECONDS,
            ]
        )
        if (
            type(result) is not int
            or type(result) is bool
            or not 0 <= result <= CANDIDATE_MAX_MAILBOX_RECORDS
        ):
            raise CandidateStoreUnavailable()
        return result

    def clear_mailbox_incomplete(
        self, scope: PriorityCandidateMailboxScope
    ) -> bool:
        if not isinstance(scope, PriorityCandidateMailboxScope):
            raise ValueError("invalid Priority candidate mailbox scope")
        key = self._mailbox_keys(scope)["mailbox_incomplete"]
        return self._clear_incomplete_key(key)

    def clear_user_incomplete(self, scope: PriorityCandidateMailboxScope) -> bool:
        if not isinstance(scope, PriorityCandidateMailboxScope):
            raise ValueError("invalid Priority candidate mailbox scope")
        key = self._mailbox_keys(scope)["user_incomplete"]
        return self._clear_incomplete_key(key)

    def _clear_incomplete_key(self, key: str) -> bool:
        result = self._command(
            ["EVAL", _CLEAR_INCOMPLETE_SCRIPT, 1, key, _INCOMPLETE_VALUE]
        )
        if type(result) is not int or type(result) is bool or result not in {0, 1}:
            raise CandidateStoreUnavailable()
        return result == 1


def build_runtime_candidate_store(*, hmac_secret: str) -> PriorityCandidateStore:
    from api.auth.session_store import build_kv_command_transport

    return PriorityCandidateStore(
        build_kv_command_transport(),
        hmac_secret=hmac_secret,
    )
