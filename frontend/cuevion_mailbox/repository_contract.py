"""Provider-neutral contracts for the durable mailbox repository.

The contracts define data and atomicity boundaries only. They do not open
connections, read environment variables, call providers, enqueue jobs, or
activate routes.
"""

from __future__ import annotations

import base64
import hashlib
import json
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


class GenerationActivationOutcome(str, Enum):
    CREATED = "created"
    CURRENT = "current"
    CONFLICT = "conflict"


class BodyCacheOutcome(str, Enum):
    STORED = "stored"
    CONFLICT = "conflict"
    STALE_GENERATION = "stale_generation"
    NOT_FOUND = "not_found"


_HEX = frozenset("0123456789abcdef")
_BASE64URL = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)
_MAX_LOCATOR_BYTES = 16_384
_MAX_TEXT_BYTES = 1_048_576
_MAX_HEADER_BYTES = 131_072
_MAX_PARTIES = 2_048
_MAX_LABELS = 1_024
_MAX_REFERENCES = 2_048
_MAX_LABEL_BYTES = 4_096
_MAX_ADDRESS_BYTES = 320
_MAX_DISPLAY_BYTES = 4_096
_MAX_PROVIDER_ID_BYTES = 1_024
_MAX_RFC_ID_BYTES = 8_192
_MAX_MAILBOX_ID_BYTES = 160


def _utf8_bytes(value: str) -> bytes:
    if type(value) is not str:
        raise ValueError("invalid mailbox text")
    try:
        return value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise ValueError("invalid mailbox text") from None


def _bounded_text(
    value: object,
    *,
    minimum: int = 0,
    maximum: int,
) -> str:
    if type(value) is not str:
        raise ValueError("invalid mailbox text")
    encoded = _utf8_bytes(value)
    if not minimum <= len(encoded) <= maximum:
        raise ValueError("invalid mailbox text")
    return value


def _canonical_hash(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in _HEX for character in value)
    ):
        raise ValueError("invalid mailbox hash")
    return value


def _canonical_internal_id(value: object, prefix: str) -> str:
    if (
        type(value) is not str
        or len(value) != 26
        or not value.startswith(prefix)
    ):
        raise ValueError("invalid mailbox record identifier")
    suffix = value[len(prefix):]
    if (
        len(suffix) != 22
        or any(character not in _BASE64URL for character in suffix)
    ):
        raise ValueError("invalid mailbox record identifier")
    try:
        decoded = base64.b64decode(
            suffix.encode("ascii") + b"==",
            altchars=b"-_",
            validate=True,
        )
    except Exception:
        raise ValueError("invalid mailbox record identifier") from None
    if len(decoded) != 16:
        raise ValueError("invalid mailbox record identifier")
    return value


def _derived_id(prefix: str, domain: str, values: tuple[object, ...]) -> str:
    payload = json.dumps(
        [domain, *values],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    digest = hashlib.sha256(payload).digest()[:16]
    suffix = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    value = prefix + suffix
    return _canonical_internal_id(value, prefix)


def derive_locator_digest(value: str) -> str:
    encoded = _utf8_bytes(value)
    if not 1 <= len(encoded) <= _MAX_LOCATOR_BYTES:
        raise ValueError("invalid mailbox locator")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class MailboxBinding:
    workspace_id: str
    owner_user_id: str
    mailbox_id: str
    provider: MailboxProvider
    provider_account_identity: str

    def __post_init__(self) -> None:
        _canonical_internal_id(self.workspace_id, "wsp_")
        _canonical_internal_id(self.owner_user_id, "usr_")
        _bounded_text(
            self.mailbox_id,
            minimum=1,
            maximum=_MAX_MAILBOX_ID_BYTES,
        )
        _bounded_text(
            self.provider_account_identity,
            minimum=1,
            maximum=320,
        )
        if (
            type(self.provider) is not MailboxProvider
            or self.provider_account_identity
            != self.provider_account_identity.casefold()
        ):
            raise ValueError("invalid mailbox binding")


@dataclass(frozen=True, slots=True)
class MailboxScope:
    workspace_id: str
    owner_user_id: str
    mailbox_id: str
    source_generation: int
    provider: MailboxProvider
    provider_account_identity: str

    def __post_init__(self) -> None:
        MailboxBinding(
            workspace_id=self.workspace_id,
            owner_user_id=self.owner_user_id,
            mailbox_id=self.mailbox_id,
            provider=self.provider,
            provider_account_identity=self.provider_account_identity,
        )
        if type(self.source_generation) is not int or self.source_generation < 1:
            raise ValueError("invalid mailbox scope")

    @property
    def binding(self) -> MailboxBinding:
        return MailboxBinding(
            workspace_id=self.workspace_id,
            owner_user_id=self.owner_user_id,
            mailbox_id=self.mailbox_id,
            provider=self.provider,
            provider_account_identity=self.provider_account_identity,
        )


@dataclass(frozen=True, slots=True)
class GenerationActivation:
    outcome: GenerationActivationOutcome
    scope: MailboxScope | None
    state_row_version: int | None


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
        derive_locator_digest(self.scope_key)
        google_shape = (
            self.provider is MailboxProvider.GOOGLE
            and self.scope_key == "gmail-account"
            and type(self.gmail_history_id) is str
            and bool(self.gmail_history_id)
            and self.gmail_history_id.isdigit()
            and self.imap_uid_validity is None
            and self.imap_highest_uid is None
            and self.imap_uidnext_observed is None
        )
        imap_shape = (
            self.provider is MailboxProvider.CUSTOM_IMAP
            and self.gmail_history_id is None
            and type(self.imap_uid_validity) is str
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
            type(self.provider) is not MailboxProvider
            or not (google_shape or imap_shape)
            or type(self.cursor_generation) is not int
            or self.cursor_generation < 1
            or type(self.row_version) is not int
            or self.row_version < 1
        ):
            raise ValueError("invalid sync cursor")
        if self.backfill_cursor is not None:
            _bounded_text(
                self.backfill_cursor,
                minimum=1,
                maximum=_MAX_LOCATOR_BYTES,
            )


@dataclass(frozen=True, slots=True)
class MailboxParty:
    address: str
    display_name: str | None = None

    def __post_init__(self) -> None:
        _bounded_text(
            self.address,
            minimum=1,
            maximum=_MAX_ADDRESS_BYTES,
        )
        if self.display_name is not None:
            _bounded_text(
                self.display_name,
                maximum=_MAX_DISPLAY_BYTES,
            )


@dataclass(frozen=True, slots=True)
class MessageIdentity:
    message_id: str
    provider_message_id: str | None
    provider_folder: str
    imap_uid_validity: str | None
    imap_uid: int | None

    def validate_for(
        self,
        provider: MailboxProvider,
        scope: MailboxScope | None = None,
    ) -> None:
        _canonical_internal_id(self.message_id, "mbm_")
        derive_locator_digest(self.provider_folder)

        if provider is MailboxProvider.GOOGLE:
            if (
                type(self.provider_message_id) is not str
                or not self.provider_message_id
                or len(_utf8_bytes(self.provider_message_id))
                > _MAX_PROVIDER_ID_BYTES
                or self.imap_uid_validity is not None
                or self.imap_uid is not None
            ):
                raise ValueError("invalid Gmail message identity")
        elif provider is MailboxProvider.CUSTOM_IMAP:
            if (
                self.provider_message_id is not None
                or type(self.imap_uid_validity) is not str
                or not self.imap_uid_validity.isdigit()
                or self.imap_uid_validity.startswith("0")
                or type(self.imap_uid) is not int
                or not 1 <= self.imap_uid <= 4_294_967_295
            ):
                raise ValueError("invalid IMAP message identity")
        else:
            raise ValueError("invalid message provider")

        if scope is not None:
            expected = derive_message_id(
                scope,
                provider_message_id=self.provider_message_id,
                provider_folder=self.provider_folder,
                imap_uid_validity=self.imap_uid_validity,
                imap_uid=self.imap_uid,
            )
            if self.message_id != expected:
                raise ValueError("invalid derived message identity")


def derive_message_id(
    scope: MailboxScope,
    *,
    provider_message_id: str | None,
    provider_folder: str,
    imap_uid_validity: str | None,
    imap_uid: int | None,
) -> str:
    derive_locator_digest(provider_folder)
    if scope.provider is MailboxProvider.GOOGLE:
        if (
            type(provider_message_id) is not str
            or not provider_message_id
            or imap_uid_validity is not None
            or imap_uid is not None
        ):
            raise ValueError("invalid Gmail message identity")
        provider_identity = ("gmail", provider_message_id)
    else:
        if (
            provider_message_id is not None
            or type(imap_uid_validity) is not str
            or not imap_uid_validity.isdigit()
            or imap_uid_validity.startswith("0")
            or type(imap_uid) is not int
            or not 1 <= imap_uid <= 4_294_967_295
        ):
            raise ValueError("invalid IMAP message identity")
        provider_identity = (
            "imap",
            derive_locator_digest(provider_folder),
            imap_uid_validity,
            imap_uid,
        )
    return _derived_id(
        "mbm_",
        "cuevion.mailbox-message.v1",
        (
            scope.workspace_id,
            scope.owner_user_id,
            scope.mailbox_id,
            scope.source_generation,
            scope.provider.value,
            *provider_identity,
        ),
    )


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

    def validate_for(
        self,
        provider: MailboxProvider,
        scope: MailboxScope | None = None,
    ) -> None:
        self.identity.validate_for(provider, scope)
        _canonical_hash(self.metadata_hash)
        if self.provider_thread_id is not None:
            _bounded_text(
                self.provider_thread_id,
                minimum=1,
                maximum=_MAX_PROVIDER_ID_BYTES,
            )
        if (
            type(self.body_state) is not BodyState
            or type(self.unread) is not bool
            or type(self.starred) is not bool
            or type(self.provider_deleted) is not bool
            or type(self.row_version) is not int
            or self.row_version < 1
        ):
            raise ValueError("invalid message projection")


@dataclass(frozen=True, slots=True)
class MessageWrite:
    projection: MessageProjection
    provider_labels: tuple[str, ...]
    rfc_message_id: str | None
    in_reply_to: str | None
    references: tuple[str, ...]
    sender: MailboxParty | None
    to: tuple[MailboxParty, ...]
    cc: tuple[MailboxParty, ...]
    subject: str
    snippet: str
    provider_timestamp_millis: int

    def validate_for(
        self,
        provider: MailboxProvider,
        scope: MailboxScope,
    ) -> None:
        self.projection.validate_for(provider, scope)
        if self.projection.provider_deleted:
            raise ValueError("invalid live message write")
        if (
            type(self.provider_labels) is not tuple
            or len(self.provider_labels) > _MAX_LABELS
            or type(self.references) is not tuple
            or len(self.references) > _MAX_REFERENCES
            or type(self.to) is not tuple
            or len(self.to) > _MAX_PARTIES
            or type(self.cc) is not tuple
            or len(self.cc) > _MAX_PARTIES
        ):
            raise ValueError("invalid message collection")
        for label in self.provider_labels:
            _bounded_text(label, minimum=1, maximum=_MAX_LABEL_BYTES)
        for value in self.references:
            _bounded_text(value, minimum=1, maximum=_MAX_RFC_ID_BYTES)
        for party in (*self.to, *self.cc):
            if type(party) is not MailboxParty:
                raise ValueError("invalid message party")
        if self.sender is not None and type(self.sender) is not MailboxParty:
            raise ValueError("invalid message sender")
        for value in (self.rfc_message_id, self.in_reply_to):
            if value is not None:
                _bounded_text(
                    value,
                    minimum=1,
                    maximum=_MAX_RFC_ID_BYTES,
                )
        _bounded_text(self.subject, maximum=_MAX_HEADER_BYTES)
        _bounded_text(self.snippet, maximum=_MAX_TEXT_BYTES)
        if (
            type(self.provider_timestamp_millis) is not int
            or self.provider_timestamp_millis < 0
            or self.provider_timestamp_millis > 253_402_300_799_999
        ):
            raise ValueError("invalid provider timestamp")


@dataclass(frozen=True, slots=True)
class MessageRecord:
    projection: MessageProjection
    provider_labels: tuple[str, ...]
    rfc_message_id: str | None
    in_reply_to: str | None
    references: tuple[str, ...]
    sender: MailboxParty | None
    to: tuple[MailboxParty, ...]
    cc: tuple[MailboxParty, ...]
    subject: str
    snippet: str
    provider_timestamp_millis: int

    def validate_for(
        self,
        provider: MailboxProvider,
        scope: MailboxScope,
    ) -> None:
        MessageWrite(
            projection=self.projection,
            provider_labels=self.provider_labels,
            rfc_message_id=self.rfc_message_id,
            in_reply_to=self.in_reply_to,
            references=self.references,
            sender=self.sender,
            to=self.to,
            cc=self.cc,
            subject=self.subject,
            snippet=self.snippet,
            provider_timestamp_millis=self.provider_timestamp_millis,
        ).validate_for(provider, scope)


@dataclass(frozen=True, slots=True)
class MessageMutation:
    kind: MessageMutationKind
    projection: MessageProjection
    write: MessageWrite | None
    outbox_event_type: OutboxEventType

    def validate_for(
        self,
        provider: MailboxProvider,
        scope: MailboxScope,
    ) -> None:
        self.projection.validate_for(provider, scope)
        if self.kind is MessageMutationKind.TOMBSTONE:
            if (
                self.write is not None
                or self.outbox_event_type is not OutboxEventType.MESSAGE_DELETED
                or self.projection.provider_deleted is not True
            ):
                raise ValueError("invalid tombstone mutation")
            return
        if (
            self.kind is not MessageMutationKind.UPSERT
            or type(self.write) is not MessageWrite
            or self.write.projection != self.projection
            or self.outbox_event_type
            not in {
                OutboxEventType.MESSAGE_ADDED,
                OutboxEventType.MESSAGE_CHANGED,
            }
            or self.projection.provider_deleted is not False
        ):
            raise ValueError("invalid upsert mutation")
        self.write.validate_for(provider, scope)


@dataclass(frozen=True, slots=True)
class ProviderDeltaCommit:
    scope: MailboxScope
    scope_key: str
    expected_state_row_version: int
    expected_cursor_row_version: int | None
    expected_cursor_generation: int
    mutations: tuple[MessageMutation, ...]
    next_cursor: SyncCursor
    next_bootstrap_state: BootstrapState
    committed_at_millis: int

    def __post_init__(self) -> None:
        derive_locator_digest(self.scope_key)
        if (
            type(self.expected_state_row_version) is not int
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
            or self.next_cursor.scope_key != self.scope_key
            or self.next_cursor.provider is not self.scope.provider
            or self.next_cursor.cursor_generation
            != self.expected_cursor_generation
            or type(self.mutations) is not tuple
            or type(self.next_bootstrap_state) is not BootstrapState
            or type(self.committed_at_millis) is not int
            or self.committed_at_millis < 0
        ):
            raise ValueError("invalid provider delta commit")

        for mutation in self.mutations:
            mutation.validate_for(self.scope.provider, self.scope)


@dataclass(frozen=True, slots=True)
class CachedBody:
    message_id: str
    body_text: str | None
    body_html: str | None
    content_hash: str
    body_version: int
    row_version: int

    def __post_init__(self) -> None:
        _canonical_internal_id(self.message_id, "mbm_")
        _canonical_hash(self.content_hash)
        if (
            self.body_text is None
            and self.body_html is None
        ):
            raise ValueError("invalid cached body")
        for value in (self.body_text, self.body_html):
            if value is not None:
                _bounded_text(value, maximum=16_777_216)
        if (
            type(self.body_version) is not int
            or self.body_version < 1
            or type(self.row_version) is not int
            or self.row_version < 1
        ):
            raise ValueError("invalid cached body")


@dataclass(frozen=True, slots=True)
class BodyWrite:
    body_text: str | None
    body_html: str | None
    content_hash: str
    body_version: int
    fetched_at_millis: int

    def __post_init__(self) -> None:
        CachedBody(
            message_id="mbm_AAAAAAAAAAAAAAAAAAAAAA",
            body_text=self.body_text,
            body_html=self.body_html,
            content_hash=self.content_hash,
            body_version=self.body_version,
            row_version=1,
        )
        if type(self.fetched_at_millis) is not int or self.fetched_at_millis < 0:
            raise ValueError("invalid body fetch time")


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    event_id: str
    scope: MailboxScope
    message_id: str
    message_row_version: int
    event_type: OutboxEventType
    attempt_count: int
    claim_token: str

    def __post_init__(self) -> None:
        _canonical_internal_id(self.event_id, "mbe_")
        _canonical_internal_id(self.message_id, "mbm_")
        if type(self.scope) is not MailboxScope:
            raise ValueError("invalid outbox event")
        if (
            type(self.message_row_version) is not int
            or self.message_row_version < 1
            or type(self.attempt_count) is not int
            or self.attempt_count < 0
            or type(self.claim_token) is not str
            or not 16 <= len(self.claim_token) <= 64
        ):
            raise ValueError("invalid outbox event")


def derive_outbox_event_id(
    scope: MailboxScope,
    *,
    message_id: str,
    message_row_version: int,
    event_type: OutboxEventType,
) -> str:
    _canonical_internal_id(message_id, "mbm_")
    if type(message_row_version) is not int or message_row_version < 1:
        raise ValueError("invalid outbox row version")
    return _derived_id(
        "mbe_",
        "cuevion.mailbox-outbox-event.v1",
        (
            scope.workspace_id,
            scope.owner_user_id,
            scope.mailbox_id,
            scope.source_generation,
            message_id,
            message_row_version,
            event_type.value,
        ),
    )


class MailboxRepository(Protocol):
    """Durable repository boundary."""

    def activate_generation(
        self,
        binding: MailboxBinding,
        *,
        bootstrap_state: BootstrapState,
        activated_at_millis: int,
        backfill_cutoff_millis: int | None,
    ) -> GenerationActivation:
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
    ) -> Sequence[MessageRecord]:
        ...

    def read_cached_body(
        self,
        scope: MailboxScope,
        message_id: str,
    ) -> CachedBody | None:
        ...

    def cache_message_body(
        self,
        scope: MailboxScope,
        message_id: str,
        *,
        expected_message_row_version: int,
        body: BodyWrite,
    ) -> BodyCacheOutcome:
        ...

    def commit_provider_delta(
        self,
        commit: ProviderDeltaCommit,
    ) -> DeltaCommitOutcome:
        """Atomically persist messages, outbox events and the next cursor."""
        ...

    def claim_outbox_batch(
        self,
        *,
        limit: int,
        now_millis: int,
        lease_millis: int,
    ) -> Sequence[OutboxEvent]:
        ...

    def mark_outbox_processed(
        self,
        event_id: str,
        *,
        claim_token: str,
        processed_at_millis: int,
    ) -> bool:
        ...

    def mark_outbox_retry(
        self,
        event_id: str,
        *,
        claim_token: str,
        next_attempt_at_millis: int,
        safe_error_code: str,
    ) -> bool:
        ...


__all__ = (
    "BackfillState",
    "BodyCacheOutcome",
    "BodyState",
    "BodyWrite",
    "BootstrapState",
    "CachedBody",
    "DeltaCommitOutcome",
    "derive_locator_digest",
    "derive_message_id",
    "derive_outbox_event_id",
    "GenerationActivation",
    "GenerationActivationOutcome",
    "MailboxBinding",
    "MailboxParty",
    "MailboxProvider",
    "MailboxRepository",
    "MailboxScope",
    "MessageIdentity",
    "MessageMutation",
    "MessageMutationKind",
    "MessageProjection",
    "MessageRecord",
    "MessageWrite",
    "OutboxEvent",
    "OutboxEventType",
    "ProviderDeltaCommit",
    "SyncCursor",
)
