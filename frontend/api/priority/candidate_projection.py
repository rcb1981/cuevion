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
    CANDIDATE_MAX_SNIPPET_BYTES,
    CANDIDATE_STORE_FAILURE_STAGES,
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
from .candidate_reference_reconciliation import (
    RECONCILIATION_FAILURE_RESULTS,
    reconcile_candidate_from_workflow_store,
)
from .event_reference import resolve_priority_hmac_secret
from .store import PriorityWorkflowStore, build_runtime_workflow_store


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

        try:
            _upsert_once(store, scope, snapshot)
        except CandidateVersionConflict:
            try:
                _upsert_once(store, scope, snapshot)
            except CandidateVersionConflict:
                count("version_conflict")
                continue
            except CandidateCapacityExceeded as error:
                count(
                    "mailbox_capacity"
                    if error.scope_kind == "mailbox"
                    else "user_capacity"
                )
                continue
            except CandidateNamespaceInvalidated:
                count("namespace_invalidated")
                continue
            except CandidateStoreUnavailable as error:
                abort_after_store_failure(error)
                break
            except Exception:
                count("candidate_snapshot_invalid")
                continue
        except CandidateCapacityExceeded as error:
            count(
                "mailbox_capacity"
                if error.scope_kind == "mailbox"
                else "user_capacity"
            )
            continue
        except CandidateNamespaceInvalidated:
            count("namespace_invalidated")
            continue
        except CandidateStoreUnavailable as error:
            abort_after_store_failure(error)
            break
        except Exception:
            count("candidate_snapshot_invalid")
            continue
        written += 1
        if workflow_store is not None:
            reconciliation = reconcile_candidate_from_workflow_store(
                store,
                workflow_store,
                scope,
            )
            if reconciliation in RECONCILIATION_FAILURE_RESULTS:
                logger.warning(
                    "Priority candidate workflow reference reconciliation outcome=%s",
                    reconciliation.value,
                )
            else:
                logger.info(
                    "Priority candidate workflow reference reconciliation outcome=%s",
                    reconciliation.value,
                )

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
