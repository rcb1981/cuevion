"""Provider-neutral contracts for the inactive durable mailbox repository.

The contracts define data and atomicity boundaries only. They do not open
connections, read environment variables, call providers, enqueue jobs, or
activate routes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Sequence


class MailboxProvider(str, Enum):
    GOOGLE = "google"
    CUSTOM_IMAP = "custom_imap"


class BootstrapState(str, Enum):
    NOT_STARTED = "not_started"
    RECENT_SYNC = "recent_sync"
    RECENT_READY = "recent_ready"
    BACKFILLING = "backfilling"
    READY = "ready"
    RECOVERING = "recovering"
    BLOCKED = "blocked"


class BackfillState(str, Enum):
    NOT_STARTED = "not_started"
    RUNNING = "running"
    COMPLETE = "complete"
    RECOVERING = "recovering"
    BLOCKED = "blocked"


class BodyState(str, Enum):
    NOT_CACHED = "not_cached"
    CACHED = "cached"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class MessageMutationKind(str, Enum):
    UPSERT = "upsert"
    TOMBSTONE = "tombstone"


class OutboxEventType(str, Enum):
    MESSAGE_ADDED = "message_added"
    MESSAGE_CHANGED = "message_changed"
    MESSAGE_DELETED = "message_deleted"


class DeltaCommitOutcome(str, Enum):
    APPLIED = "applied"
    CONFLICT = "conflict"
    STALE_GENERATION = "stale_generation"
    NOT_FOUND = "not_found"


class CurrentStateInitializationOutcome(str, Enum):
    CREATED = "created"
    EXISTING = "existing"
    CONFLICT = "conflict"


def derive_locator_digest(value: str) -> str:
    if type(value) is not str or not value:
        raise ValueError("invalid mailbox locator")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise ValueError("invalid mailbox locator") from None
    if not 1 <= len(encoded) <= 16_384:
        raise ValueError("invalid mailbox locator")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class MailboxReadAuthority:
    workspace_id: str
    owner_user_id: str
    mailbox_id: str
    provider: MailboxProvider
    provider_account_identity: str

    def __post_init__(self) -> None:
        if (
            type(self.workspace_id) is not str
            or len(self.workspace_id) != 26
            or not self.workspace_id.startswith("wsp_")
            or type(self.owner_user_id) is not str
            or len(self.owner_user_id) != 26
            or not self.owner_user_id.startswith("usr_")
            or type(self.mailbox_id) is not str
            or not 1 <= len(self.mailbox_id.encode("utf-8")) <= 160
            or type(self.provider) is not MailboxProvider
            or type(self.provider_account_identity) is not str
            or not 3 <= len(
                self.provider_account_identity.encode("utf-8")
            ) <= 320
            or self.provider_account_identity
            != self.provider_account_identity.casefold()
        ):
            raise ValueError("invalid mailbox read authority")


@dataclass(frozen=True, slots=True)
class MailboxScope:
    workspace_id: str
    owner_user_id: str
    mailbox_id: str
    source_generation: int
    provider: MailboxProvider
    provider_account_identity: str

    def __post_init__(self) -> None:
        if (
            type(self.workspace_id) is not str
            or len(self.workspace_id) != 26
            or not self.workspace_id.startswith("wsp_")
            or type(self.owner_user_id) is not str
            or len(self.owner_user_id) != 26
            or not self.owner_user_id.startswith("usr_")
            or type(self.mailbox_id) is not str
            or not 1 <= len(self.mailbox_id.encode("utf-8")) <= 160
            or type(self.provider_account_identity) is not str
            or not self.provider_account_identity
            or type(self.source_generation) is not int
            or self.source_generation < 1
            or self.provider_account_identity
            != self.provider_account_identity.casefold()
        ):
            raise ValueError("invalid mailbox scope")


@dataclass(frozen=True, slots=True)
class MailboxStateSnapshot:
    scope: MailboxScope
    bootstrap_state: BootstrapState
    row_version: int

    def __post_init__(self) -> None:
        if (
            type(self.scope) is not MailboxScope
            or type(self.bootstrap_state) is not BootstrapState
            or type(self.row_version) is not int
            or self.row_version < 1
        ):
            raise ValueError("invalid mailbox state snapshot")


@dataclass(frozen=True, slots=True)
class CurrentStateInitializationResult:
    outcome: CurrentStateInitializationOutcome
    state: MailboxStateSnapshot | None

    def __post_init__(self) -> None:
        if type(self.outcome) is not CurrentStateInitializationOutcome:
            raise ValueError("invalid mailbox state initialization result")
        if self.outcome is CurrentStateInitializationOutcome.CONFLICT:
            if self.state is not None:
                raise ValueError("invalid mailbox state initialization result")
        elif type(self.state) is not MailboxStateSnapshot:
            raise ValueError("invalid mailbox state initialization result")


@dataclass(frozen=True, slots=True)
class SyncCursor:
    scope_key: str
    cursor_generation: int
    provider: MailboxProvider
    gmail_history_id: str | None
    imap_uid_validity: str | None
    imap_highest_uid: int | None
    imap_uidnext_observed: int | None
    backfill_state: BackfillState
    backfill_cursor: str | None
    row_version: int

    def __post_init__(self) -> None:
        google_shape = (
            self.provider is MailboxProvider.GOOGLE
            and self.scope_key == "gmail-account"
            and isinstance(self.gmail_history_id, str)
            and self.gmail_history_id.isdigit()
            and self.imap_uid_validity is None
            and self.imap_highest_uid is None
            and self.imap_uidnext_observed is None
        )
        imap_shape = (
            self.provider is MailboxProvider.CUSTOM_IMAP
            and type(self.scope_key) is str
            and bool(self.scope_key)
            and self.gmail_history_id is None
            and isinstance(self.imap_uid_validity, str)
            and self.imap_uid_validity.isdigit()
            and not self.imap_uid_validity.startswith("0")
            and type(self.imap_highest_uid) is int
            and 0 <= self.imap_highest_uid <= 4_294_967_295
            and (
                self.imap_uidnext_observed is None
                or (
                    type(self.imap_uidnext_observed) is int
                    and 1 <= self.imap_uidnext_observed <= 4_294_967_296
                )
            )
        )
        if (
            not (google_shape or imap_shape)
            or type(self.cursor_generation) is not int
            or self.cursor_generation < 1
            or type(self.row_version) is not int
            or self.row_version < 1
            or (
                self.backfill_cursor is not None
                and (
                    type(self.backfill_cursor) is not str
                    or not self.backfill_cursor
                )
            )
        ):
            raise ValueError("invalid sync cursor")


@dataclass(frozen=True, slots=True)
class MessageIdentity:
    message_id: str
    provider_message_id: str | None
    provider_folder: str
    imap_uid_validity: str | None
    imap_uid: int | None

    def validate_for(self, provider: MailboxProvider) -> None:
        if (
            type(self.message_id) is not str
            or len(self.message_id) != 26
            or not self.message_id.startswith("mbm_")
            or type(self.provider_folder) is not str
            or not self.provider_folder
        ):
            raise ValueError("invalid message identity")

        if provider is MailboxProvider.GOOGLE:
            if (
                type(self.provider_message_id) is not str
                or not self.provider_message_id
                or self.imap_uid_validity is not None
                or self.imap_uid is not None
            ):
                raise ValueError("invalid Gmail message identity")
            return

        if (
            self.provider_message_id is not None
            or type(self.imap_uid_validity) is not str
            or not self.imap_uid_validity.isdigit()
            or self.imap_uid_validity.startswith("0")
            or type(self.imap_uid) is not int
            or not 1 <= self.imap_uid <= 4_294_967_295
        ):
            raise ValueError("invalid IMAP message identity")


@dataclass(frozen=True, slots=True)
class MessageRecord:
    identity: MessageIdentity
    provider_thread_id: str | None
    provider_labels: tuple[str, ...]
    rfc_message_id: str | None
    in_reply_to: str | None
    references: tuple[str, ...]
    sender_address: str | None
    sender_display: str | None
    to_recipients: tuple[str, ...]
    cc_recipients: tuple[str, ...]
    subject: str
    snippet: str
    provider_timestamp_millis: int
    unread: bool
    starred: bool
    body_state: BodyState
    metadata_hash: str

    def validate_for(self, provider: MailboxProvider) -> None:
        self.identity.validate_for(provider)
        if (
            self.provider_thread_id is not None
            and (type(self.provider_thread_id) is not str or not self.provider_thread_id)
        ):
            raise ValueError("invalid message record")
        for values in (
            self.provider_labels,
            self.references,
            self.to_recipients,
            self.cc_recipients,
        ):
            if (
                type(values) is not tuple
                or any(type(value) is not str or not value for value in values)
            ):
                raise ValueError("invalid message record")
        if (
            type(self.subject) is not str
            or type(self.snippet) is not str
            or type(self.provider_timestamp_millis) is not int
            or self.provider_timestamp_millis < 0
            or type(self.unread) is not bool
            or type(self.starred) is not bool
            or type(self.metadata_hash) is not str
            or len(self.metadata_hash) != 64
            or any(character not in "0123456789abcdef" for character in self.metadata_hash)
        ):
            raise ValueError("invalid message record")


@dataclass(frozen=True, slots=True)
class MessageProjection:
    identity: MessageIdentity
    provider_thread_id: str | None
    metadata_hash: str
    body_state: BodyState
    unread: bool
    starred: bool
    provider_deleted: bool
    row_version: int

    def validate_for(self, provider: MailboxProvider) -> None:
        self.identity.validate_for(provider)
        if (
            type(self.metadata_hash) is not str
            or len(self.metadata_hash) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.metadata_hash
            )
            or type(self.unread) is not bool
            or type(self.starred) is not bool
            or type(self.provider_deleted) is not bool
            or type(self.row_version) is not int
            or self.row_version < 1
        ):
            raise ValueError("invalid message projection")


@dataclass(frozen=True, slots=True)
class MessageMutation:
    kind: MessageMutationKind
    identity: MessageIdentity
    record: MessageRecord | None
    expected_row_version: int | None
    event_id: str
    outbox_event_type: OutboxEventType

    def validate_for(self, provider: MailboxProvider) -> None:
        self.identity.validate_for(provider)
        if (
            type(self.event_id) is not str
            or len(self.event_id) != 26
            or not self.event_id.startswith("mbe_")
        ):
            raise ValueError("invalid message mutation")
        if self.kind is MessageMutationKind.UPSERT:
            if self.record is None:
                raise ValueError("invalid upsert mutation")
            self.record.validate_for(provider)
            if self.record.identity != self.identity:
                raise ValueError("invalid upsert mutation")
            if self.expected_row_version is None:
                if self.outbox_event_type is not OutboxEventType.MESSAGE_ADDED:
                    raise ValueError("invalid upsert mutation")
            elif (
                type(self.expected_row_version) is not int
                or self.expected_row_version < 1
                or self.outbox_event_type is not OutboxEventType.MESSAGE_CHANGED
            ):
                raise ValueError("invalid upsert mutation")
            return
        if (
            self.kind is not MessageMutationKind.TOMBSTONE
            or self.record is not None
            or type(self.expected_row_version) is not int
            or self.expected_row_version < 1
            or self.outbox_event_type is not OutboxEventType.MESSAGE_DELETED
        ):
            raise ValueError("invalid tombstone mutation")


@dataclass(frozen=True, slots=True)
class ProviderDeltaCommit:
    scope: MailboxScope
    scope_key: str
    expected_state_row_version: int
    expected_cursor_row_version: int | None
    expected_cursor_generation: int
    committed_at_millis: int
    mutations: tuple[MessageMutation, ...]
    next_cursor: SyncCursor
    next_bootstrap_state: BootstrapState

    def __post_init__(self) -> None:
        if (
            type(self.scope_key) is not str
            or not self.scope_key
            or type(self.expected_state_row_version) is not int
            or self.expected_state_row_version < 1
            or (
                self.expected_cursor_row_version is not None
                and (
                    type(self.expected_cursor_row_version) is not int
                    or self.expected_cursor_row_version < 1
                )
            )
            or type(self.expected_cursor_generation) is not int
            or self.expected_cursor_generation < 1
            or type(self.committed_at_millis) is not int
            or self.committed_at_millis < 0
            or self.next_cursor.scope_key != self.scope_key
            or self.next_cursor.provider is not self.scope.provider
            or self.next_cursor.cursor_generation
            != self.expected_cursor_generation
        ):
            raise ValueError("invalid provider delta commit")
        if self.expected_cursor_row_version is None:
            if self.next_cursor.row_version != 1:
                raise ValueError("invalid provider delta commit")
        elif self.next_cursor.row_version != self.expected_cursor_row_version + 1:
            raise ValueError("invalid provider delta commit")

        event_ids: set[str] = set()
        for mutation in self.mutations:
            mutation.validate_for(self.scope.provider)
            if mutation.event_id in event_ids:
                raise ValueError("invalid provider delta commit")
            event_ids.add(mutation.event_id)


@dataclass(frozen=True, slots=True)
class CachedBody:
    message_id: str
    body_text: str | None
    body_html: str | None
    content_hash: str
    body_version: int
    row_version: int


@dataclass(frozen=True, slots=True)
class OutboxStorageScope:
    workspace_id: str
    owner_user_id: str
    mailbox_id: str
    source_generation: int


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    event_id: str
    scope: OutboxStorageScope
    message_id: str
    message_row_version: int
    event_type: OutboxEventType
    attempt_count: int
    claim_token: str


class MailboxReaderRepository(Protocol):
    """Read-only durable mailbox boundary for cache/UI consumers."""

    def resolve_current_state(
        self,
        authority: MailboxReadAuthority,
    ) -> MailboxStateSnapshot | None:
        """Resolve exact authenticated current state including CAS row version."""
        ...

    def resolve_current_scope(
        self,
        authority: MailboxReadAuthority,
    ) -> MailboxScope | None:
        """Resolve only the current generation for exact authenticated authority."""
        ...

    def read_cursor(
        self,
        scope: MailboxScope,
        scope_key: str,
    ) -> SyncCursor | None:
        ...

    def list_messages(
        self,
        scope: MailboxScope,
        *,
        limit: int,
        before_timestamp_millis: int | None = None,
    ) -> Sequence[MessageProjection]:
        ...

    def read_cached_body(
        self,
        scope: MailboxScope,
        message_id: str,
    ) -> CachedBody | None:
        ...


class MailboxRepository(MailboxReaderRepository, Protocol):
    """Read/write durable mailbox boundary for sync and outbox workers.

    commit_provider_delta MUST use one PostgreSQL transaction. It must compare
    expected state/cursor versions and generations, apply every message
    mutation, insert the idempotent outbox rows, and advance the cursor before
    commit. A conflict or exception must publish none of those changes.
    """

    def initialize_current_state(
        self,
        authority: MailboxReadAuthority,
        *,
        initialized_at_millis: int,
    ) -> CurrentStateInitializationResult:
        """Create generation 1 only when no safe current state already exists."""
        ...

    def commit_provider_delta(
        self,
        commit: ProviderDeltaCommit,
    ) -> DeltaCommitOutcome:
        ...

    def claim_outbox_batch(
        self,
        *,
        limit: int,
        now_millis: int,
        lease_millis: int,
    ) -> Sequence[OutboxEvent]:
        """Atomically claim due events and return their persisted claim tokens."""
        ...

    def mark_outbox_processed(
        self,
        event_id: str,
        *,
        claim_token: str,
        processed_at_millis: int,
    ) -> bool:
        """Complete only when the current persisted claim token still matches."""
        ...

    def mark_outbox_retry(
        self,
        event_id: str,
        *,
        claim_token: str,
        now_millis: int,
        next_attempt_at_millis: int,
        safe_error_code: str,
    ) -> bool:
        """Release only the caller's claim and schedule the next bounded retry."""
        ...


__all__ = (
    "BackfillState",
    "BodyState",
    "BootstrapState",
    "CachedBody",
    "CurrentStateInitializationOutcome",
    "CurrentStateInitializationResult",
    "DeltaCommitOutcome",
    "derive_locator_digest",
    "MailboxProvider",
    "MailboxReadAuthority",
    "MailboxReaderRepository",
    "MailboxStateSnapshot",
    "MailboxRepository",
    "MailboxScope",
    "MessageIdentity",
    "MessageMutation",
    "MessageRecord",
    "MessageMutationKind",
    "MessageProjection",
    "OutboxEvent",
    "OutboxStorageScope",
    "OutboxEventType",
    "ProviderDeltaCommit",
    "SyncCursor",
)
