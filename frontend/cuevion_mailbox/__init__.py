"""Inactive durable-mailbox synchronization package.

Nothing in this package opens a database connection, reads runtime secrets,
calls a mail provider, or activates an API route.
"""

from .repository_contract import (
    BackfillState,
    BodyState,
    BootstrapState,
    DeltaCommitOutcome,
    MailboxProvider,
    MailboxRepository,
    MailboxScope,
    MessageIdentity,
    MessageMutation,
    MessageMutationKind,
    MessageProjection,
    OutboxEvent,
    OutboxEventType,
    ProviderDeltaCommit,
    SyncCursor,
)
from .schema import (
    MAILBOX_SCHEMA,
    MAILBOX_SCHEMA_VERSION,
    MAILBOX_TABLES,
    mailbox_change_outbox,
    mailbox_message_bodies,
    mailbox_messages,
    mailbox_sync_cursor,
    mailbox_sync_state,
    metadata,
)

__all__ = (
    "BackfillState",
    "BodyState",
    "BootstrapState",
    "DeltaCommitOutcome",
    "MAILBOX_SCHEMA",
    "MAILBOX_SCHEMA_VERSION",
    "MAILBOX_TABLES",
    "MailboxProvider",
    "MailboxRepository",
    "MailboxScope",
    "MessageIdentity",
    "MessageMutation",
    "MessageMutationKind",
    "MessageProjection",
    "OutboxEvent",
    "OutboxEventType",
    "ProviderDeltaCommit",
    "SyncCursor",
    "mailbox_change_outbox",
    "mailbox_message_bodies",
    "mailbox_messages",
    "mailbox_sync_cursor",
    "mailbox_sync_state",
    "metadata",
)
