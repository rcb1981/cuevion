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
from email.headerregistry import Address
from email.utils import parsedate_to_datetime
from typing import Literal

from .authority import PriorityMessageIdentity, canonical_conversation_id
from .candidate_store import (
    CANDIDATE_ABSOLUTE_TTL_SECONDS,
    CANDIDATE_MAX_SNIPPET_BYTES,
    CANDIDATE_STORE_FAILURE_STAGES,
    POSITIVE_REFERENCE_MAX_SECONDS,
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
    PriorityCandidateUnchangedConfirmation,
    build_runtime_candidate_store,
)
from .candidate_reference_reconciliation import (
    RECONCILIATION_FAILURE_RESULTS,
    reconcile_candidate_from_workflow_store,
    workflow_reference_expiries,
)
from .event_reference import resolve_priority_hmac_secret
from .store import (
    PriorityWorkflowScope,
    PriorityWorkflowStore,
    build_runtime_workflow_store,
)


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
        "candidate_conversation_invalid",
        "candidate_duplicate",
        "candidate_identity_invalid",
        "candidate_render_invalid",
        "candidate_snapshot_invalid",
        "candidate_timestamp_invalid",
        "configuration_unavailable",
        "mailbox_capacity",
        "namespace_invalidated",
        "not_processed_after_store_failure",
        *CANDIDATE_STORE_FAILURE_STAGES,
        "user_capacity",
        "version_conflict",
        "window_invalid",
    }
)
logger = logging.getLogger(__name__)


class CandidateProjectionInvalid(ValueError):
    """Content-free, stage-bounded candidate adapter rejection."""

    __slots__ = ("reason_code",)

    def __init__(self, reason_code: str) -> None:
        self.reason_code = (
            reason_code
            if reason_code
            in {
                "candidate_conversation_invalid",
                "candidate_identity_invalid",
                "candidate_render_invalid",
                "candidate_snapshot_invalid",
                "candidate_timestamp_invalid",
            }
            else "candidate_snapshot_invalid"
        )
        ValueError.__init__(self, "invalid Priority candidate projection")


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
    processed: int
    written: int
    skipped: int
    incomplete: bool
    reason_counts: tuple[tuple[str, int], ...]

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(code for code, _count in self.reason_counts)

    def __post_init__(self) -> None:
        if (
            type(self.attempted) is not int
            or type(self.processed) is not int
            or type(self.written) is not int
            or type(self.skipped) is not int
            or not 0 <= self.written <= self.processed <= self.attempted
            <= MAX_CURRENT_WINDOW_CANDIDATES
            or self.skipped != self.attempted - self.written
            or type(self.incomplete) is not bool
            or type(self.reason_counts) is not tuple
            or len(self.reason_counts) > len(_POPULATION_REASON_CODES)
            or tuple(sorted(self.reason_counts)) != self.reason_counts
            or len({code for code, _count in self.reason_counts})
            != len(self.reason_counts)
            or any(
                code not in _POPULATION_REASON_CODES
                or type(count) is not int
                or not 1 <= count <= MAX_CURRENT_WINDOW_CANDIDATES + 1
                for code, count in self.reason_counts
            )
            or self.incomplete != bool(self.reason_counts)
        ):
            raise ValueError("invalid Priority candidate population report")


def _report(
    attempted: int,
    processed: int,
    written: int,
    reason_counts: dict[str, int],
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
    safe_processed = (
        processed
        if type(processed) is int
        and safe_written <= processed <= safe_attempted
        else safe_written
    )
    safe_reason_counts = tuple(
        sorted(
            (code, count)
            for code, count in reason_counts.items()
            if code in _POPULATION_REASON_CODES
            and type(count) is int
            and 1 <= count <= MAX_CURRENT_WINDOW_CANDIDATES + 1
        )
    )
    return PriorityCandidatePopulationReport(
        attempted=safe_attempted,
        processed=safe_processed,
        written=safe_written,
        skipped=safe_attempted - safe_written,
        incomplete=bool(safe_reason_counts),
        reason_counts=safe_reason_counts,
    )


def _operational_report(
    report: PriorityCandidatePopulationReport,
) -> PriorityCandidatePopulationReport:
    if report.incomplete:
        logger.warning(
            "Priority candidate population incomplete attempted=%s processed=%s written=%s skipped=%s incomplete=%s reason_counts=%s",
            report.attempted,
            report.processed,
            report.written,
            report.skipped,
            report.incomplete,
            ",".join(
                f"{code}:{count}" for code, count in report.reason_counts
            ),
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


def _valid_sender_address(value: object) -> bool:
    if (
        not _valid_text(value, 320, content=True)
        or not value
        or value != value.strip()
    ):
        return False
    try:
        parsed = Address(addr_spec=value)
    except Exception:
        return False
    return bool(
        parsed.username
        and parsed.domain
        and parsed.addr_spec == value
    )


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
        or not _valid_sender_address(source.get("senderAddress"))
        or not _valid_text(source.get("subject"), 998, content=True)
        or type(source.get("unread")) is not bool
        or type(source.get("flagged")) is not bool
    ):
        raise CandidateProjectionInvalid("candidate_render_invalid")
    try:
        return PriorityCandidateRender(
            sender_display=source["senderDisplay"],
            sender_address=source["senderAddress"],
            subject=source["subject"],
            snippet=snippet,
            created_at=created_at,
            unread=source["unread"],
            flagged=source["flagged"],
        )
    except Exception:
        raise CandidateProjectionInvalid("candidate_render_invalid") from None


def _gmail_projection(
    authority: PriorityCandidatePopulationAuthority,
    source: object,
) -> tuple[PriorityCandidateScope, PriorityCandidateSnapshot]:
    if type(source) is not dict or set(source) != _GMAIL_SOURCE_FIELDS:
        raise CandidateProjectionInvalid("candidate_identity_invalid")
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
    ):
        raise CandidateProjectionInvalid("candidate_identity_invalid")
    if not _valid_text(provider_thread_id, 256):
        raise CandidateProjectionInvalid("candidate_conversation_invalid")
    created_at = _provider_millisecond_timestamp(
        source.get("providerTimestampMillis")
    ) or _rfc_timestamp(source.get("rfcDate"))
    if created_at is None:
        raise CandidateProjectionInvalid("candidate_timestamp_invalid")
    try:
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
        scope.canonical_bytes()
    except Exception:
        raise CandidateProjectionInvalid("candidate_identity_invalid") from None
    try:
        conversation = PriorityCandidateConversation(
            conversation_id=canonical_conversation_id(
                authority.mailbox_id,
                provider_thread_id,
            ),
            authority_kind="gmail",
            provider_thread_id=provider_thread_id,
        )
    except Exception:
        raise CandidateProjectionInvalid(
            "candidate_conversation_invalid"
        ) from None
    render = _render(source, created_at)
    try:
        snapshot = PriorityCandidateSnapshot(
            conversation=conversation,
            render=render,
            routing_state="unresolved",
            routing=None,
            provider_authority=PriorityCandidateProviderAuthority(
                folder="INBOX",
                labels=tuple(labels),
            ),
        )
        snapshot.validate_for_scope(scope)
    except Exception:
        raise CandidateProjectionInvalid("candidate_snapshot_invalid") from None
    return scope, snapshot


def _optional_identifier(value: object, maximum_bytes: int) -> str | None:
    if value is None:
        return None
    if not _valid_text(value, maximum_bytes):
        raise CandidateProjectionInvalid("candidate_conversation_invalid")
    return value


def _imap_projection(
    authority: PriorityCandidatePopulationAuthority,
    source: object,
) -> tuple[PriorityCandidateScope, PriorityCandidateSnapshot]:
    if type(source) is not dict or set(source) != _IMAP_SOURCE_FIELDS:
        raise CandidateProjectionInvalid("candidate_identity_invalid")
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
    ):
        raise CandidateProjectionInvalid("candidate_identity_invalid")
    if (
        not _valid_text(conversation_id, 1_024)
        or authority_kind not in {"rfc", "imap_uid"}
    ):
        raise CandidateProjectionInvalid("candidate_conversation_invalid")
    created_at = _rfc_timestamp(source.get("rfcDate"))
    if created_at is None:
        raise CandidateProjectionInvalid("candidate_timestamp_invalid")
    rfc_root_message_id = _optional_identifier(
        source.get("rfcRootMessageId"),
        1_024,
    )
    rfc_message_id = _optional_identifier(source.get("rfcMessageId"), 1_024)
    if authority_kind == "rfc" and rfc_root_message_id is None:
        raise CandidateProjectionInvalid("candidate_conversation_invalid")
    if authority_kind == "imap_uid" and rfc_root_message_id is not None:
        raise CandidateProjectionInvalid("candidate_conversation_invalid")
    try:
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
        scope.canonical_bytes()
    except Exception:
        raise CandidateProjectionInvalid("candidate_identity_invalid") from None
    try:
        conversation = PriorityCandidateConversation(
            conversation_id=conversation_id,
            authority_kind=authority_kind,
            rfc_root_message_id=rfc_root_message_id,
            rfc_message_id=rfc_message_id,
        )
    except Exception:
        raise CandidateProjectionInvalid(
            "candidate_conversation_invalid"
        ) from None
    render = _render(source, created_at)
    try:
        snapshot = PriorityCandidateSnapshot(
            conversation=conversation,
            render=render,
            routing_state="unresolved",
            routing=None,
            provider_authority=PriorityCandidateProviderAuthority(
                folder=folder,
                labels=(),
            ),
        )
        snapshot.validate_for_scope(scope)
    except Exception:
        raise CandidateProjectionInvalid("candidate_snapshot_invalid") from None
    return scope, snapshot


def project_priority_candidate(
    authority: PriorityCandidatePopulationAuthority,
    source: object,
) -> tuple[PriorityCandidateScope, PriorityCandidateSnapshot]:
    if not isinstance(authority, PriorityCandidatePopulationAuthority):
        raise CandidateProjectionInvalid("candidate_identity_invalid")
    if authority.provider == "google":
        return _gmail_projection(authority, source)
    return _imap_projection(authority, source)


def _provider_snapshot_unchanged(
    existing: PriorityCandidateSnapshot,
    observed: PriorityCandidateSnapshot,
) -> bool:
    return (
        existing.conversation == observed.conversation
        and existing.render == observed.render
        and existing.provider_authority == observed.provider_authority
    )


def _upsert_once(
    store: PriorityCandidateStore,
    scope: PriorityCandidateScope,
    snapshot: PriorityCandidateSnapshot,
) -> None:
    try:
        existing = store.read_candidate(scope)
    except CandidateStoreUnavailable as error:
        if error.stage != "store_existing_record_invalid":
            raise
        store.replace_malformed_confirmed(scope, snapshot)
        return
    confirmed_snapshot = snapshot
    if (
        existing is not None
        and _provider_snapshot_unchanged(existing.snapshot, snapshot)
    ):
        confirmed_snapshot = existing.snapshot
    store.upsert_confirmed(
        scope,
        confirmed_snapshot,
        expected_version=existing.version if existing is not None else 0,
    )


def _log_reconciliation_outcome(outcome: str, *, failed: bool) -> None:
    log = logger.warning if failed else logger.info
    log(
        "Priority candidate workflow reference reconciliation outcome=%s",
        outcome,
    )


def _write_projected_candidate(
    store: PriorityCandidateStore,
    workflow_store: PriorityWorkflowStore | None,
    scope: PriorityCandidateScope,
    snapshot: PriorityCandidateSnapshot,
) -> tuple[bool, str | None, bool]:
    for attempt in range(2):
        try:
            _upsert_once(store, scope, snapshot)
            break
        except CandidateVersionConflict:
            if attempt == 0:
                continue
            return False, "version_conflict", False
        except CandidateCapacityExceeded as error:
            return (
                False,
                "mailbox_capacity"
                if error.scope_kind == "mailbox"
                else "user_capacity",
                False,
            )
        except CandidateNamespaceInvalidated:
            return False, "namespace_invalidated", False
        except CandidateStoreUnavailable as error:
            return False, error.stage, True
        except Exception:
            return False, "candidate_snapshot_invalid", False

    if workflow_store is not None:
        reconciliation = reconcile_candidate_from_workflow_store(
            store,
            workflow_store,
            scope,
        )
        _log_reconciliation_outcome(
            reconciliation.value,
            failed=reconciliation in RECONCILIATION_FAILURE_RESULTS,
        )
    return True, None, False


def _populate_priority_candidates_slow(
    authority: PriorityCandidatePopulationAuthority,
    sources: object,
    *,
    store: PriorityCandidateStore,
    workflow_store: PriorityWorkflowStore | None = None,
) -> PriorityCandidatePopulationReport:
    """Best-effort reconcile only rows observed in one current provider window."""

    if (
        not isinstance(authority, PriorityCandidatePopulationAuthority)
        or not isinstance(store, PriorityCandidateStore)
    ):
        return _report(0, 0, 0, {"authority_invalid": 1})
    if (
        type(sources) is not list
        or len(sources) > MAX_CURRENT_WINDOW_CANDIDATES
    ):
        return _report(0, 0, 0, {"window_invalid": 1})

    attempted = len(sources)
    processed = 0
    written = 0
    reason_counts: dict[str, int] = {}
    seen_scopes: set[bytes] = set()

    def count(reason_code: str, amount: int = 1) -> None:
        reason_counts[reason_code] = reason_counts.get(reason_code, 0) + amount

    def abort_after_store_failure(error: CandidateStoreUnavailable) -> None:
        count(error.stage)
        remaining = attempted - processed
        if remaining:
            count("not_processed_after_store_failure", remaining)

    for source in sources:
        processed += 1
        try:
            scope, snapshot = project_priority_candidate(authority, source)
            canonical_scope = scope.canonical_bytes()
            if canonical_scope in seen_scopes:
                count("candidate_duplicate")
                continue
            seen_scopes.add(canonical_scope)
        except CandidateProjectionInvalid as error:
            count(error.reason_code)
            continue
        except Exception:
            count("candidate_snapshot_invalid")
            continue

        row_written, reason_code, fatal = _write_projected_candidate(
            store,
            workflow_store,
            scope,
            snapshot,
        )
        if row_written:
            written += 1
            continue
        assert reason_code is not None
        if fatal:
            abort_after_store_failure(CandidateStoreUnavailable(reason_code))
            break
        count(reason_code)

    return _report(attempted, processed, written, reason_counts)


def _workflow_scope_for_candidate(
    scope: PriorityCandidateScope,
) -> PriorityWorkflowScope:
    return PriorityWorkflowScope(
        workspace_id=scope.workspace_id,
        user_id=scope.user_id,
        mailbox_id=scope.mailbox_id,
        identity=scope.identity,
    )


def _candidate_unchanged_evidence(
    candidate_evidence: object,
    scope: PriorityCandidateScope,
    snapshot: PriorityCandidateSnapshot,
    *,
    observed_at: int,
) -> bool:
    try:
        record = candidate_evidence.record
        return bool(
            candidate_evidence.scope == scope
            and record is not None
            and record.scope == scope
            and candidate_evidence.raw is not None
            and candidate_evidence.storage_valid
            and candidate_evidence.indexes_valid
            and type(candidate_evidence.marker_values) is tuple
            and len(candidate_evidence.marker_values) == 3
            and candidate_evidence.marker_values[2] is None
            and record.state == "provider_confirmed"
            and record.authority_state_at(observed_at) == "provider_confirmed"
            and _provider_snapshot_unchanged(record.snapshot, snapshot)
        )
    except Exception:
        return False


def _unchanged_confirmation(
    candidate_evidence: object,
    workflow_evidence: object,
    scope: PriorityCandidateScope,
    snapshot: PriorityCandidateSnapshot,
    *,
    observed_at: int,
) -> tuple[PriorityCandidateUnchangedConfirmation, str] | None:
    try:
        record = candidate_evidence.record
        if not _candidate_unchanged_evidence(
            candidate_evidence,
            scope,
            snapshot,
            observed_at=observed_at,
        ):
            return None
        assert record is not None

        workflow_scope = _workflow_scope_for_candidate(scope)
        if (
            workflow_evidence.scope != workflow_scope
            or not workflow_evidence.storage_valid
            or type(workflow_evidence.key) is not str
            or not workflow_evidence.key
        ):
            return None

        workflow_record = workflow_evidence.record
        workflow_raw = workflow_evidence.raw
        if workflow_raw is None:
            if workflow_record is None or workflow_record.version != 0:
                return None
            return (
                PriorityCandidateUnchangedConfirmation(
                    evidence=candidate_evidence,
                    workflow_key=workflow_evidence.key,
                    workflow_raw=None,
                    workflow_persisted=False,
                    workflow_valid_until=0,
                ),
                "workflow_record_absent",
            )
        if (
            type(workflow_raw) is not str
            or not workflow_raw
            or workflow_record is None
        ):
            return None

        requested = workflow_reference_expiries(workflow_record)
        if (
            type(requested) is not tuple
            or len(requested) != 3
            or any(type(value) is not int or value < 0 for value in requested)
        ):
            return None
        absolute_expires_at = (
            observed_at + CANDIDATE_ABSOLUTE_TTL_SECONDS * 1_000
        )
        kinds = ("manual_priority", "waiting", "returned_reply")
        post_provider = tuple(
            0
            if record.positive_reference_expires_at(kind) <= observed_at
            else min(
                record.positive_reference_expires_at(kind),
                absolute_expires_at,
            )
            for kind in kinds
        )
        normalized_workflow = tuple(
            0
            if expires_at <= observed_at
            else min(
                expires_at,
                absolute_expires_at,
                observed_at + POSITIVE_REFERENCE_MAX_SECONDS[kind] * 1_000,
            )
            for kind, expires_at in zip(kinds, requested, strict=True)
        )
        if post_provider != normalized_workflow:
            return None

        active_boundaries = tuple(
            expires_at
            for active, expires_at in (
                (
                    workflow_record.manual_priority != "none",
                    workflow_record.manual_expires_at,
                ),
                (
                    workflow_record.cleared != "active",
                    workflow_record.cleared_expires_at,
                ),
                (
                    workflow_record.waiting != "absent",
                    workflow_record.waiting_expires_at,
                ),
            )
            if active
        )
        if any(
            type(expires_at) is not int or expires_at <= observed_at
            for expires_at in active_boundaries
        ):
            return None
        workflow_valid_until = min(active_boundaries, default=0)
        return (
            PriorityCandidateUnchangedConfirmation(
                evidence=candidate_evidence,
                workflow_key=workflow_evidence.key,
                workflow_raw=workflow_raw,
                workflow_persisted=True,
                workflow_valid_until=workflow_valid_until,
            ),
            "candidate_reference_reconciled",
        )
    except Exception:
        return None


def populate_priority_candidates(
    authority: PriorityCandidatePopulationAuthority,
    sources: object,
    *,
    store: PriorityCandidateStore,
    workflow_store: PriorityWorkflowStore | None = None,
) -> PriorityCandidatePopulationReport:
    """Best-effort reconcile only rows observed in one current provider window."""

    if (
        not isinstance(authority, PriorityCandidatePopulationAuthority)
        or not isinstance(store, PriorityCandidateStore)
    ):
        return _report(0, 0, 0, {"authority_invalid": 1})
    if (
        type(sources) is not list
        or len(sources) > MAX_CURRENT_WINDOW_CANDIDATES
    ):
        return _report(0, 0, 0, {"window_invalid": 1})
    if authority.provider != "google" or workflow_store is None:
        return _populate_priority_candidates_slow(
            authority,
            sources,
            store=store,
            workflow_store=workflow_store,
        )

    staged: list[
        tuple[PriorityCandidateScope, PriorityCandidateSnapshot] | str
    ] = []
    valid_rows: list[tuple[PriorityCandidateScope, PriorityCandidateSnapshot]] = []
    seen_scopes: set[bytes] = set()
    for source in sources:
        try:
            scope, snapshot = project_priority_candidate(authority, source)
            canonical_scope = scope.canonical_bytes()
            if canonical_scope in seen_scopes:
                staged.append("candidate_duplicate")
                continue
            seen_scopes.add(canonical_scope)
        except CandidateProjectionInvalid as error:
            staged.append(error.reason_code)
            continue
        except Exception:
            staged.append("candidate_snapshot_invalid")
            continue
        row = (scope, snapshot)
        staged.append(row)
        valid_rows.append(row)

    if not valid_rows:
        reason_counts: dict[str, int] = {}
        for reason_code in staged:
            assert isinstance(reason_code, str)
            reason_counts[reason_code] = reason_counts.get(reason_code, 0) + 1
        return _report(len(sources), len(sources), 0, reason_counts)

    try:
        preflight = store.preflight_unchanged_confirmations(
            tuple(scope for scope, _snapshot in valid_rows)
        )
        if len(preflight.evidence) != len(valid_rows):
            raise ValueError("invalid Priority candidate confirmation preflight")
        potential_rows = tuple(
            index
            for index, ((scope, snapshot), candidate_evidence) in enumerate(
                zip(valid_rows, preflight.evidence, strict=True)
            )
            if _candidate_unchanged_evidence(
                candidate_evidence,
                scope,
                snapshot,
                observed_at=preflight.observed_at,
            )
        )
        if not potential_rows:
            return _populate_priority_candidates_slow(
                authority,
                sources,
                store=store,
                workflow_store=workflow_store,
            )
        workflow_scopes = tuple(
            _workflow_scope_for_candidate(valid_rows[index][0])
            for index in potential_rows
        )
        workflow_evidence = workflow_store.read_confirmation_evidence(
            workflow_scopes,
            observed_at=preflight.observed_at,
        )
        if (
            type(workflow_evidence) is not tuple
            or len(workflow_evidence) != len(potential_rows)
        ):
            raise ValueError("invalid Priority workflow confirmation preflight")
    except Exception:
        return _populate_priority_candidates_slow(
            authority,
            sources,
            store=store,
            workflow_store=workflow_store,
        )

    confirmations: list[PriorityCandidateUnchangedConfirmation] = []
    confirmation_rows: list[int] = []
    confirmation_outcomes: list[str] = []
    for index, current_workflow in zip(
        potential_rows,
        workflow_evidence,
        strict=True,
    ):
        scope, snapshot = valid_rows[index]
        candidate_evidence = preflight.evidence[index]
        confirmation = _unchanged_confirmation(
            candidate_evidence,
            current_workflow,
            scope,
            snapshot,
            observed_at=preflight.observed_at,
        )
        if confirmation is None:
            continue
        item, outcome = confirmation
        confirmations.append(item)
        confirmation_rows.append(index)
        confirmation_outcomes.append(outcome)

    committed_by_valid_row: dict[int, tuple[object, str]] = {}
    if confirmations:
        try:
            committed = store.confirm_unchanged_batch(
                preflight,
                tuple(confirmations),
            )
            if type(committed) is not tuple or len(committed) != len(confirmations):
                committed = (None,) * len(confirmations)
        except Exception:
            committed = (None,) * len(confirmations)
        for row_index, outcome, record in zip(
            confirmation_rows,
            confirmation_outcomes,
            committed,
            strict=True,
        ):
            if record is not None:
                committed_by_valid_row[row_index] = (record, outcome)

    attempted = len(sources)
    processed = 0
    written = 0
    reason_counts: dict[str, int] = {}
    valid_index = 0
    store_failed = False

    def count(reason_code: str) -> None:
        reason_counts[reason_code] = reason_counts.get(reason_code, 0) + 1

    for staged_row in staged:
        if isinstance(staged_row, str):
            if store_failed:
                count("not_processed_after_store_failure")
            else:
                processed += 1
                count(staged_row)
            continue

        scope, snapshot = staged_row
        committed_row = committed_by_valid_row.get(valid_index)
        valid_index += 1
        if committed_row is not None:
            processed += 1
            written += 1
            _record, outcome = committed_row
            _log_reconciliation_outcome(outcome, failed=False)
            continue
        if store_failed:
            count("not_processed_after_store_failure")
            continue

        processed += 1
        row_written, reason_code, fatal = _write_projected_candidate(
            store,
            workflow_store,
            scope,
            snapshot,
        )
        if row_written:
            written += 1
            continue
        assert reason_code is not None
        count(reason_code)
        store_failed = fatal

    return _report(attempted, processed, written, reason_counts)


def populate_runtime_priority_candidates(
    *,
    member: object,
    mailbox_id: object,
    mailbox_account_identity: object,
    provider: object,
    sources: object,
    store: PriorityCandidateStore | None = None,
    workflow_store: PriorityWorkflowStore | None = None,
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
            _report(attempted, 0, 0, {"authority_invalid": 1})
        )

    runtime_store = store
    runtime_workflow_store = workflow_store
    if runtime_store is None or runtime_workflow_store is None:
        try:
            secret = hmac_secret or resolve_priority_hmac_secret()
            if runtime_store is None:
                runtime_store = build_runtime_candidate_store(hmac_secret=secret)
            if runtime_workflow_store is None:
                runtime_workflow_store = build_runtime_workflow_store(
                    hmac_secret=secret
                )
        except Exception:
            return _operational_report(
                _report(
                    attempted,
                    0,
                    0,
                    {"configuration_unavailable": 1},
                )
            )
    try:
        return _operational_report(
            populate_priority_candidates(
                authority,
                sources,
                store=runtime_store,
                workflow_store=runtime_workflow_store,
            )
        )
    except Exception:
        return _operational_report(
            _report(attempted, 0, 0, {"store_unexpected": 1})
        )
