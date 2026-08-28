"""Trusted current-window projection into dormant Priority candidates.

The producer is deliberately write-only and best-effort.  Provider refreshes
remain authoritative for their normal mailbox response even when candidate
storage is unavailable or an individual provider row cannot be projected.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Literal

from .authority import PriorityMessageIdentity, canonical_conversation_id
from .candidate_store import (
    CANDIDATE_MAX_SNIPPET_BYTES,
    CandidateCapacityExceeded,
    CandidateNamespaceInvalidated,
    CandidateStoreUnavailable,
    CandidateVersionConflict,
    PriorityCandidateConversation,
    PriorityCandidateProviderAuthority,
    PriorityCandidateRender,
    PriorityCandidateScope,
    PriorityCandidateSnapshot,
    PriorityCandidateStore,
    build_runtime_candidate_store,
)
from .event_reference import resolve_priority_hmac_secret


MAX_CURRENT_WINDOW_CANDIDATES = 100
_MAX_PROVIDER_MILLISECONDS = 253_402_300_799_999
_IMAP_NUMBER_RE = re.compile(r"[1-9][0-9]*", re.ASCII)
_GMAIL_SOURCE_FIELDS = frozenset(
    {
        "provider",
        "providerMessageId",
        "providerThreadId",
        "providerFolder",
        "labels",
        "senderDisplay",
        "senderAddress",
        "subject",
        "snippet",
        "unread",
        "flagged",
        "providerTimestampMillis",
        "rfcDate",
    }
)
_IMAP_SOURCE_FIELDS = frozenset(
    {
        "provider",
        "providerFolder",
        "uidValidity",
        "imapUid",
        "conversationId",
        "authorityKind",
        "rfcRootMessageId",
        "rfcMessageId",
        "senderDisplay",
        "senderAddress",
        "subject",
        "snippet",
        "unread",
        "flagged",
        "rfcDate",
    }
)
_NON_INBOX_GMAIL_LABELS = frozenset({"SENT", "SPAM", "TRASH", "DRAFT"})
_POPULATION_REASON_CODES = frozenset(
    {
        "authority_invalid",
        "candidate_invalid",
        "capacity_exceeded",
        "configuration_unavailable",
        "namespace_invalidated",
        "store_unavailable",
        "version_conflict",
        "window_invalid",
    }
)
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PriorityCandidatePopulationAuthority:
    workspace_id: str
    user_id: str
    mailbox_id: str
    mailbox_account_identity: str
    provider: Literal["google", "custom_imap"]

    def __post_init__(self) -> None:
        probe = PriorityCandidateScope(
            workspace_id=self.workspace_id,
            user_id=self.user_id,
            mailbox_id=self.mailbox_id,
            mailbox_account_identity=self.mailbox_account_identity,
            provider=self.provider,
            identity=(
                PriorityMessageIdentity(
                    provider="google",
                    provider_message_id="authority-probe",
                )
                if self.provider == "google"
                else PriorityMessageIdentity(
                    provider="custom_imap",
                    provider_folder="INBOX",
                    uid_validity="1",
                    imap_uid="1",
                )
            ),
        )
        probe.canonical_bytes()


@dataclass(frozen=True, slots=True)
class PriorityCandidatePopulationReport:
    attempted: int
    written: int
    skipped: int
    incomplete: bool
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            type(self.attempted) is not int
            or type(self.written) is not int
            or type(self.skipped) is not int
            or not 0 <= self.written <= self.attempted <= MAX_CURRENT_WINDOW_CANDIDATES
            or self.skipped != self.attempted - self.written
            or type(self.incomplete) is not bool
            or type(self.reason_codes) is not tuple
            or len(self.reason_codes) > len(_POPULATION_REASON_CODES)
            or len(set(self.reason_codes)) != len(self.reason_codes)
            or any(code not in _POPULATION_REASON_CODES for code in self.reason_codes)
            or self.incomplete != bool(self.reason_codes)
        ):
            raise ValueError("invalid Priority candidate population report")


def _report(
    attempted: int,
    written: int,
    reasons: set[str],
) -> PriorityCandidatePopulationReport:
    safe_attempted = (
        attempted
        if type(attempted) is int and 0 <= attempted <= MAX_CURRENT_WINDOW_CANDIDATES
        else 0
    )
    safe_written = (
        written
        if type(written) is int and 0 <= written <= safe_attempted
        else 0
    )
    safe_reasons = tuple(sorted(reasons.intersection(_POPULATION_REASON_CODES)))
    return PriorityCandidatePopulationReport(
        attempted=safe_attempted,
        written=safe_written,
        skipped=safe_attempted - safe_written,
        incomplete=bool(safe_reasons),
        reason_codes=safe_reasons,
    )


def _operational_report(
    report: PriorityCandidatePopulationReport,
) -> PriorityCandidatePopulationReport:
    if report.incomplete:
        logger.warning(
            "Priority candidate population incomplete attempted=%s written=%s skipped=%s reason_codes=%s",
            report.attempted,
            report.written,
            report.skipped,
            ",".join(report.reason_codes),
        )
    return report


def _valid_text(value: object, maximum_bytes: int, *, content: bool = False) -> bool:
    if type(value) is not str or "\x00" in value or "\r" in value:
        return False
    if not content and (not value or value != value.strip()):
        return False
    if any(
        (ord(character) < 32 and (not content or character not in {"\n", "\t"}))
        or ord(character) == 127
        for character in value
    ):
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= maximum_bytes
    except UnicodeEncodeError:
        return False


def _bounded_snippet(value: object) -> str | None:
    if type(value) is not str or "\x00" in value or "\r" in value:
        return None
    if any(
        (ord(character) < 32 and character not in {"\n", "\t"})
        or ord(character) == 127
        for character in value
    ):
        return None
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return None
    return encoded[:CANDIDATE_MAX_SNIPPET_BYTES].decode("utf-8", errors="ignore")


def _rfc_timestamp(value: object) -> str | None:
    if (
        type(value) is not str
        or not value
        or len(value.encode("utf-8", errors="ignore")) > 998
        or "\r" in value
        or "\n" in value
        or any(ord(character) < 32 and character != "\t" for character in value)
    ):
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        normalized = parsed.astimezone(timezone.utc)
    except Exception:
        return None
    return normalized.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _provider_millisecond_timestamp(value: object) -> str | None:
    if type(value) is not str or re.fullmatch(r"(?:0|[1-9][0-9]*)", value) is None:
        return None
    try:
        milliseconds = int(value)
        if milliseconds > _MAX_PROVIDER_MILLISECONDS:
            return None
        parsed = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
            milliseconds=milliseconds
        )
    except (OverflowError, ValueError):
        return None
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _render(source: dict, created_at: str) -> PriorityCandidateRender:
    snippet = _bounded_snippet(source.get("snippet"))
    if (
        snippet is None
        or not _valid_text(source.get("senderDisplay"), 256, content=True)
        or not _valid_text(source.get("senderAddress"), 320, content=True)
        or not _valid_text(source.get("subject"), 998, content=True)
        or type(source.get("unread")) is not bool
        or type(source.get("flagged")) is not bool
    ):
        raise ValueError("invalid candidate render source")
    return PriorityCandidateRender(
        sender_display=source["senderDisplay"],
        sender_address=source["senderAddress"],
        subject=source["subject"],
        snippet=snippet,
        created_at=created_at,
        unread=source["unread"],
        flagged=source["flagged"],
    )


def _gmail_projection(
    authority: PriorityCandidatePopulationAuthority,
    source: object,
) -> tuple[PriorityCandidateScope, PriorityCandidateSnapshot]:
    if type(source) is not dict or set(source) != _GMAIL_SOURCE_FIELDS:
        raise ValueError("invalid Gmail candidate source")
    labels = source.get("labels")
    provider_message_id = source.get("providerMessageId")
    provider_thread_id = source.get("providerThreadId")
    if (
        authority.provider != "google"
        or source.get("provider") != "google"
        or source.get("providerFolder") != "INBOX"
        or type(labels) is not list
        or not labels
        or any(not _valid_text(label, 256) for label in labels)
        or len(set(labels)) != len(labels)
        or "INBOX" not in labels
        or _NON_INBOX_GMAIL_LABELS.intersection(labels)
        or not _valid_text(provider_message_id, 256)
        or not _valid_text(provider_thread_id, 256)
    ):
        raise ValueError("invalid Gmail candidate source")
    created_at = _provider_millisecond_timestamp(
        source.get("providerTimestampMillis")
    ) or _rfc_timestamp(source.get("rfcDate"))
    if created_at is None:
        raise ValueError("invalid Gmail candidate message time")
    scope = PriorityCandidateScope(
        workspace_id=authority.workspace_id,
        user_id=authority.user_id,
        mailbox_id=authority.mailbox_id,
        mailbox_account_identity=authority.mailbox_account_identity,
        provider="google",
        identity=PriorityMessageIdentity(
            provider="google",
            provider_message_id=provider_message_id,
        ),
    )
    snapshot = PriorityCandidateSnapshot(
        conversation=PriorityCandidateConversation(
            conversation_id=canonical_conversation_id(
                authority.mailbox_id,
                provider_thread_id,
            ),
            authority_kind="gmail",
            provider_thread_id=provider_thread_id,
        ),
        render=_render(source, created_at),
        routing_state="unresolved",
        routing=None,
        provider_authority=PriorityCandidateProviderAuthority(
            folder="INBOX",
            labels=tuple(labels),
        ),
    )
    snapshot.validate_for_scope(scope)
    return scope, snapshot


def _optional_identifier(value: object, maximum_bytes: int) -> str | None:
    if value is None:
        return None
    if not _valid_text(value, maximum_bytes):
        raise ValueError("invalid optional candidate identifier")
    return value


def _imap_projection(
    authority: PriorityCandidatePopulationAuthority,
    source: object,
) -> tuple[PriorityCandidateScope, PriorityCandidateSnapshot]:
    if type(source) is not dict or set(source) != _IMAP_SOURCE_FIELDS:
        raise ValueError("invalid IMAP candidate source")
    folder = source.get("providerFolder")
    uid_validity = source.get("uidValidity")
    imap_uid = source.get("imapUid")
    conversation_id = source.get("conversationId")
    authority_kind = source.get("authorityKind")
    if (
        authority.provider != "custom_imap"
        or source.get("provider") != "custom_imap"
        or not _valid_text(folder, 1_024)
        or folder.casefold() != "inbox"
        or type(uid_validity) is not str
        or _IMAP_NUMBER_RE.fullmatch(uid_validity) is None
        or type(imap_uid) is not str
        or _IMAP_NUMBER_RE.fullmatch(imap_uid) is None
        or not _valid_text(conversation_id, 1_024)
        or authority_kind not in {"rfc", "imap_uid"}
    ):
        raise ValueError("invalid IMAP candidate source")
    created_at = _rfc_timestamp(source.get("rfcDate"))
    if created_at is None:
        raise ValueError("invalid IMAP candidate message time")
    rfc_root_message_id = _optional_identifier(
        source.get("rfcRootMessageId"),
        1_024,
    )
    rfc_message_id = _optional_identifier(source.get("rfcMessageId"), 1_024)
    if authority_kind == "rfc" and rfc_root_message_id is None:
        raise ValueError("invalid IMAP RFC conversation source")
    if authority_kind == "imap_uid" and rfc_root_message_id is not None:
        raise ValueError("invalid IMAP UID conversation source")
    scope = PriorityCandidateScope(
        workspace_id=authority.workspace_id,
        user_id=authority.user_id,
        mailbox_id=authority.mailbox_id,
        mailbox_account_identity=authority.mailbox_account_identity,
        provider="custom_imap",
        identity=PriorityMessageIdentity(
            provider="custom_imap",
            provider_folder=folder,
            uid_validity=uid_validity,
            imap_uid=imap_uid,
        ),
    )
    snapshot = PriorityCandidateSnapshot(
        conversation=PriorityCandidateConversation(
            conversation_id=conversation_id,
            authority_kind=authority_kind,
            rfc_root_message_id=rfc_root_message_id,
            rfc_message_id=rfc_message_id,
        ),
        render=_render(source, created_at),
        routing_state="unresolved",
        routing=None,
        provider_authority=PriorityCandidateProviderAuthority(
            folder=folder,
            labels=(),
        ),
    )
    snapshot.validate_for_scope(scope)
    return scope, snapshot


def project_priority_candidate(
    authority: PriorityCandidatePopulationAuthority,
    source: object,
) -> tuple[PriorityCandidateScope, PriorityCandidateSnapshot]:
    if not isinstance(authority, PriorityCandidatePopulationAuthority):
        raise ValueError("invalid Priority candidate authority")
    if authority.provider == "google":
        return _gmail_projection(authority, source)
    return _imap_projection(authority, source)


def _upsert_once(
    store: PriorityCandidateStore,
    scope: PriorityCandidateScope,
    snapshot: PriorityCandidateSnapshot,
) -> None:
    existing = store.read_candidate(scope)
    store.upsert_confirmed(
        scope,
        snapshot,
        expected_version=existing.version if existing is not None else 0,
    )


def populate_priority_candidates(
    authority: PriorityCandidatePopulationAuthority,
    sources: object,
    *,
    store: PriorityCandidateStore,
) -> PriorityCandidatePopulationReport:
    """Best-effort reconcile only rows observed in one current provider window."""

    if (
        not isinstance(authority, PriorityCandidatePopulationAuthority)
        or not isinstance(store, PriorityCandidateStore)
    ):
        return _report(0, 0, {"authority_invalid"})
    if (
        type(sources) is not list
        or len(sources) > MAX_CURRENT_WINDOW_CANDIDATES
    ):
        return _report(0, 0, {"window_invalid"})

    attempted = len(sources)
    written = 0
    reasons: set[str] = set()
    seen_scopes: set[bytes] = set()
    for source in sources:
        try:
            scope, snapshot = project_priority_candidate(authority, source)
            canonical_scope = scope.canonical_bytes()
            if canonical_scope in seen_scopes:
                reasons.add("candidate_invalid")
                continue
            seen_scopes.add(canonical_scope)
        except Exception:
            reasons.add("candidate_invalid")
            continue

        try:
            _upsert_once(store, scope, snapshot)
        except CandidateVersionConflict:
            try:
                _upsert_once(store, scope, snapshot)
            except CandidateVersionConflict:
                reasons.add("version_conflict")
                continue
            except CandidateCapacityExceeded:
                reasons.add("capacity_exceeded")
                continue
            except CandidateNamespaceInvalidated:
                reasons.add("namespace_invalidated")
                continue
            except CandidateStoreUnavailable:
                reasons.add("store_unavailable")
                break
            except Exception:
                reasons.add("candidate_invalid")
                continue
        except CandidateCapacityExceeded:
            reasons.add("capacity_exceeded")
            continue
        except CandidateNamespaceInvalidated:
            reasons.add("namespace_invalidated")
            continue
        except CandidateStoreUnavailable:
            reasons.add("store_unavailable")
            break
        except Exception:
            reasons.add("candidate_invalid")
            continue
        written += 1

    return _report(attempted, written, reasons)


def populate_runtime_priority_candidates(
    *,
    member: object,
    mailbox_id: object,
    mailbox_account_identity: object,
    provider: object,
    sources: object,
    store: PriorityCandidateStore | None = None,
    hmac_secret: str | None = None,
) -> PriorityCandidatePopulationReport:
    """Total server boundary used by Inbox routes; this function never raises."""

    attempted = (
        len(sources)
        if type(sources) is list and len(sources) <= MAX_CURRENT_WINDOW_CANDIDATES
        else 0
    )
    try:
        authority = PriorityCandidatePopulationAuthority(
            workspace_id=getattr(member, "workspace_id"),
            user_id=getattr(member, "user_id"),
            mailbox_id=mailbox_id,
            mailbox_account_identity=mailbox_account_identity,
            provider=provider,
        )
    except Exception:
        return _operational_report(
            _report(attempted, 0, {"authority_invalid"})
        )

    runtime_store = store
    if runtime_store is None:
        try:
            secret = hmac_secret or resolve_priority_hmac_secret()
            runtime_store = build_runtime_candidate_store(hmac_secret=secret)
        except Exception:
            return _operational_report(
                _report(attempted, 0, {"configuration_unavailable"})
            )
    try:
        return _operational_report(
            populate_priority_candidates(
                authority,
                sources,
                store=runtime_store,
            )
        )
    except Exception:
        return _operational_report(
            _report(attempted, 0, {"store_unavailable"})
        )
