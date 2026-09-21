"""Inactive SQLAlchemy Core foundation for durable mailbox synchronization."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg


MAILBOX_SCHEMA = "cuevion_mailbox"
MAILBOX_SCHEMA_VERSION = 1

metadata = sa.MetaData(
    schema=MAILBOX_SCHEMA,
    naming_convention={
        "pk": "pk_%(table_name)s",
        "fk": "fk_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    },
)

# External authority stub: resolves the cross-schema membership FK for
# SQLAlchemy compilation only. It is intentionally excluded from MAILBOX_TABLES.
_account_workspace_memberships = sa.Table(
    "workspace_memberships",
    metadata,
    sa.Column("workspace_id", sa.String(26, collation="C"), primary_key=True),
    sa.Column("user_id", sa.String(26, collation="C"), primary_key=True),
    schema="cuevion_account",
    info={"external_authority_stub": True},
)

_ID = sa.String(26, collation="C")
_MAILBOX_ID = sa.String(160, collation="C")
_EMAIL = sa.String(320, collation="C")
_CODE = sa.String(128, collation="C")
_HASH = sa.String(64, collation="C")
_UIDVALIDITY = sa.String(20, collation="C")
_TIMESTAMP = sa.DateTime(timezone=True)


mailbox_sync_state = sa.Table(
    "mailbox_sync_state",
    metadata,
    sa.Column("schema_version", sa.SmallInteger(), nullable=False),
    sa.Column("workspace_id", _ID, nullable=False),
    sa.Column("owner_user_id", _ID, nullable=False),
    sa.Column("mailbox_id", _MAILBOX_ID, nullable=False),
    sa.Column("provider", sa.Text(collation="C"), nullable=False),
    sa.Column("provider_account_identity", _EMAIL, nullable=False),
    sa.Column("source_generation", sa.BigInteger(), nullable=False),
    sa.Column("is_current", sa.Boolean(), nullable=False),
    sa.Column("bootstrap_state", sa.Text(collation="C"), nullable=False),
    sa.Column("backfill_cutoff_at", _TIMESTAMP, nullable=True),
    sa.Column("backfill_oldest_indexed_at", _TIMESTAMP, nullable=True),
    sa.Column("last_successful_sync_at", _TIMESTAMP, nullable=True),
    sa.Column("last_error_code", _CODE, nullable=True),
    sa.Column("created_at", _TIMESTAMP, nullable=False),
    sa.Column("updated_at", _TIMESTAMP, nullable=False),
    sa.Column("row_version", sa.BigInteger(), nullable=False),
    sa.PrimaryKeyConstraint(
        "workspace_id", "owner_user_id", "mailbox_id", "source_generation",
        name="pk_mailbox_sync_state",
    ),
    sa.UniqueConstraint(
        "workspace_id", "owner_user_id", "mailbox_id",
        "source_generation", "provider",
        name="uq_mailbox_sync_state_generation_provider",
    ),
    sa.ForeignKeyConstraint(
        ("workspace_id", "owner_user_id"),
        (
            "cuevion_account.workspace_memberships.workspace_id",
            "cuevion_account.workspace_memberships.user_id",
        ),
        name="fk_mailbox_sync_state_active_membership_scope",
    ),
    sa.CheckConstraint("schema_version = 1", name="schema_version_one"),
    sa.CheckConstraint(
        "provider IN ('google', 'custom_imap')",
        name="provider_supported",
    ),
    sa.CheckConstraint(
        "bootstrap_state IN "
        "('not_started','recent_sync','recent_ready','backfilling','ready','recovering','blocked')",
        name="bootstrap_state_supported",
    ),
    sa.CheckConstraint("source_generation > 0", name="source_generation_positive"),
    sa.CheckConstraint("row_version > 0", name="row_version_positive"),
    sa.CheckConstraint(
        "provider_account_identity = lower(provider_account_identity)",
        name="provider_identity_lowercase",
    ),
    sa.CheckConstraint(
        "octet_length(provider_account_identity) BETWEEN 3 AND 320",
        name="provider_identity_bounded",
    ),
    sa.CheckConstraint(
        "last_error_code IS NULL OR octet_length(last_error_code) BETWEEN 1 AND 128",
        name="last_error_code_bounded",
    ),
)

sa.Index(
    "ux_mailbox_sync_state_current",
    mailbox_sync_state.c.workspace_id,
    mailbox_sync_state.c.owner_user_id,
    mailbox_sync_state.c.mailbox_id,
    unique=True,
    postgresql_where=mailbox_sync_state.c.is_current.is_(True),
)


mailbox_sync_cursor = sa.Table(
    "mailbox_sync_cursor",
    metadata,
    sa.Column("schema_version", sa.SmallInteger(), nullable=False),
    sa.Column("workspace_id", _ID, nullable=False),
    sa.Column("owner_user_id", _ID, nullable=False),
    sa.Column("mailbox_id", _MAILBOX_ID, nullable=False),
    sa.Column("source_generation", sa.BigInteger(), nullable=False),
    sa.Column("provider", sa.Text(collation="C"), nullable=False),
    sa.Column("scope_key", sa.Text(collation="C"), nullable=False),
    sa.Column("cursor_generation", sa.BigInteger(), nullable=False),
    sa.Column("gmail_history_id", sa.String(128, collation="C"), nullable=True),
    sa.Column("imap_uid_validity", _UIDVALIDITY, nullable=True),
    sa.Column("imap_highest_uid", sa.BigInteger(), nullable=True),
    sa.Column("imap_uidnext_observed", sa.BigInteger(), nullable=True),
    sa.Column("backfill_state", sa.Text(collation="C"), nullable=False),
    sa.Column("backfill_cursor", sa.Text(collation="C"), nullable=True),
    sa.Column("last_successful_sync_at", _TIMESTAMP, nullable=True),
    sa.Column("created_at", _TIMESTAMP, nullable=False),
    sa.Column("updated_at", _TIMESTAMP, nullable=False),
    sa.Column("row_version", sa.BigInteger(), nullable=False),
    sa.PrimaryKeyConstraint(
        "workspace_id", "owner_user_id", "mailbox_id",
        "source_generation", "scope_key",
        name="pk_mailbox_sync_cursor",
    ),
    sa.ForeignKeyConstraint(
        (
            "workspace_id", "owner_user_id", "mailbox_id",
            "source_generation", "provider",
        ),
        (
            "cuevion_mailbox.mailbox_sync_state.workspace_id",
            "cuevion_mailbox.mailbox_sync_state.owner_user_id",
            "cuevion_mailbox.mailbox_sync_state.mailbox_id",
            "cuevion_mailbox.mailbox_sync_state.source_generation",
            "cuevion_mailbox.mailbox_sync_state.provider",
        ),
        name="fk_mailbox_sync_cursor_state_generation",
        ondelete="CASCADE",
    ),
    sa.CheckConstraint("schema_version = 1", name="schema_version_one"),
    sa.CheckConstraint(
        "provider IN ('google', 'custom_imap')",
        name="provider_supported",
    ),
    sa.CheckConstraint("cursor_generation > 0", name="cursor_generation_positive"),
    sa.CheckConstraint("row_version > 0", name="row_version_positive"),
    sa.CheckConstraint(
        "octet_length(scope_key) BETWEEN 1 AND 16384",
        name="scope_key_bounded",
    ),
    sa.CheckConstraint(
        "backfill_state IN ('not_started','running','complete','recovering','blocked')",
        name="backfill_state_supported",
    ),
    sa.CheckConstraint(
        "("
        "provider = 'google' AND scope_key = 'gmail-account' "
        "AND gmail_history_id IS NOT NULL "
        "AND imap_uid_validity IS NULL AND imap_highest_uid IS NULL "
        "AND imap_uidnext_observed IS NULL"
        ") OR ("
        "provider = 'custom_imap' AND gmail_history_id IS NULL "
        "AND imap_uid_validity IS NOT NULL AND imap_highest_uid IS NOT NULL"
        ")",
        name="provider_cursor_shape",
    ),
    sa.CheckConstraint(
        "gmail_history_id IS NULL OR gmail_history_id ~ '^[0-9]+$'",
        name="gmail_history_id_digits",
    ),
    sa.CheckConstraint(
        "imap_uid_validity IS NULL OR imap_uid_validity ~ '^[1-9][0-9]{0,19}$'",
        name="imap_uid_validity_canonical",
    ),
    sa.CheckConstraint(
        "imap_highest_uid IS NULL OR imap_highest_uid BETWEEN 0 AND 4294967295",
        name="imap_highest_uid_bounded",
    ),
    sa.CheckConstraint(
        "imap_uidnext_observed IS NULL OR "
        "imap_uidnext_observed BETWEEN 1 AND 4294967296",
        name="imap_uidnext_bounded",
    ),
    sa.CheckConstraint(
        "backfill_cursor IS NULL OR octet_length(backfill_cursor) BETWEEN 1 AND 16384",
        name="backfill_cursor_bounded",
    ),
)


mailbox_messages = sa.Table(
    "mailbox_messages",
    metadata,
    sa.Column("schema_version", sa.SmallInteger(), nullable=False),
    sa.Column("message_id", _ID, nullable=False),
    sa.Column("workspace_id", _ID, nullable=False),
    sa.Column("owner_user_id", _ID, nullable=False),
    sa.Column("mailbox_id", _MAILBOX_ID, nullable=False),
    sa.Column("source_generation", sa.BigInteger(), nullable=False),
    sa.Column("provider", sa.Text(collation="C"), nullable=False),
    sa.Column("provider_message_id", sa.Text(collation="C"), nullable=True),
    sa.Column("provider_thread_id", sa.Text(collation="C"), nullable=True),
    sa.Column("provider_folder", sa.Text(collation="C"), nullable=False),
    sa.Column("provider_labels", pg.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column("imap_uid_validity", _UIDVALIDITY, nullable=True),
    sa.Column("imap_uid", sa.BigInteger(), nullable=True),
    sa.Column("rfc_message_id", sa.Text(collation="C"), nullable=True),
    sa.Column("in_reply_to", sa.Text(collation="C"), nullable=True),
    sa.Column("references_json", pg.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column("sender_address", _EMAIL, nullable=True),
    sa.Column("sender_display", sa.Text(collation="C"), nullable=True),
    sa.Column("to_json", pg.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column("cc_json", pg.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column("subject", sa.Text(collation="C"), nullable=False),
    sa.Column("snippet", sa.Text(collation="C"), nullable=False),
    sa.Column("provider_timestamp", _TIMESTAMP, nullable=False),
    sa.Column("unread", sa.Boolean(), nullable=False),
    sa.Column("starred", sa.Boolean(), nullable=False),
    sa.Column("provider_deleted", sa.Boolean(), nullable=False),
    sa.Column("body_state", sa.Text(collation="C"), nullable=False),
    sa.Column("metadata_hash", _HASH, nullable=False),
    sa.Column("created_at", _TIMESTAMP, nullable=False),
    sa.Column("updated_at", _TIMESTAMP, nullable=False),
    sa.Column("row_version", sa.BigInteger(), nullable=False),
    sa.PrimaryKeyConstraint(
        "workspace_id", "owner_user_id", "mailbox_id", "message_id",
        name="pk_mailbox_messages",
    ),
    sa.UniqueConstraint(
        "workspace_id", "owner_user_id", "mailbox_id",
        "source_generation", "message_id",
        name="uq_mailbox_messages_generation_id",
    ),
    sa.ForeignKeyConstraint(
        (
            "workspace_id", "owner_user_id", "mailbox_id",
            "source_generation", "provider",
        ),
        (
            "cuevion_mailbox.mailbox_sync_state.workspace_id",
            "cuevion_mailbox.mailbox_sync_state.owner_user_id",
            "cuevion_mailbox.mailbox_sync_state.mailbox_id",
            "cuevion_mailbox.mailbox_sync_state.source_generation",
            "cuevion_mailbox.mailbox_sync_state.provider",
        ),
        name="fk_mailbox_messages_state_generation",
        ondelete="CASCADE",
    ),
    sa.CheckConstraint("schema_version = 1", name="schema_version_one"),
    sa.CheckConstraint(
        "provider IN ('google', 'custom_imap')",
        name="provider_supported",
    ),
    sa.CheckConstraint("source_generation > 0", name="source_generation_positive"),
    sa.CheckConstraint("row_version > 0", name="row_version_positive"),
    sa.CheckConstraint(
        "body_state IN ('not_cached','cached','stale','unavailable')",
        name="body_state_supported",
    ),
    sa.CheckConstraint(
        "metadata_hash ~ '^[0-9a-f]{64}$'",
        name="metadata_hash_canonical",
    ),
    sa.CheckConstraint(
        "octet_length(provider_folder) BETWEEN 1 AND 16384",
        name="provider_folder_bounded",
    ),
    sa.CheckConstraint(
        "("
        "provider = 'google' AND provider_message_id IS NOT NULL "
        "AND imap_uid_validity IS NULL AND imap_uid IS NULL"
        ") OR ("
        "provider = 'custom_imap' AND provider_message_id IS NULL "
        "AND imap_uid_validity IS NOT NULL AND imap_uid IS NOT NULL"
        ")",
        name="provider_identity_shape",
    ),
    sa.CheckConstraint(
        "provider_message_id IS NULL OR octet_length(provider_message_id) BETWEEN 1 AND 1024",
        name="provider_message_id_bounded",
    ),
    sa.CheckConstraint(
        "provider_thread_id IS NULL OR octet_length(provider_thread_id) BETWEEN 1 AND 1024",
        name="provider_thread_id_bounded",
    ),
    sa.CheckConstraint(
        "imap_uid_validity IS NULL OR imap_uid_validity ~ '^[1-9][0-9]{0,19}$'",
        name="imap_uid_validity_canonical",
    ),
    sa.CheckConstraint(
        "imap_uid IS NULL OR imap_uid BETWEEN 1 AND 4294967295",
        name="imap_uid_bounded",
    ),
)

sa.Index(
    "ux_mailbox_messages_gmail_identity",
    mailbox_messages.c.workspace_id,
    mailbox_messages.c.owner_user_id,
    mailbox_messages.c.mailbox_id,
    mailbox_messages.c.source_generation,
    mailbox_messages.c.provider_message_id,
    unique=True,
    postgresql_where=sa.and_(
        mailbox_messages.c.provider == "google",
        mailbox_messages.c.provider_message_id.is_not(None),
    ),
)
sa.Index(
    "ux_mailbox_messages_imap_identity",
    mailbox_messages.c.workspace_id,
    mailbox_messages.c.owner_user_id,
    mailbox_messages.c.mailbox_id,
    mailbox_messages.c.source_generation,
    mailbox_messages.c.provider_folder,
    mailbox_messages.c.imap_uid_validity,
    mailbox_messages.c.imap_uid,
    unique=True,
    postgresql_where=sa.and_(
        mailbox_messages.c.provider == "custom_imap",
        mailbox_messages.c.imap_uid_validity.is_not(None),
        mailbox_messages.c.imap_uid.is_not(None),
    ),
)
sa.Index(
    "ix_mailbox_messages_visible_order",
    mailbox_messages.c.workspace_id,
    mailbox_messages.c.owner_user_id,
    mailbox_messages.c.mailbox_id,
    mailbox_messages.c.source_generation,
    mailbox_messages.c.provider_deleted,
    mailbox_messages.c.provider_timestamp,
)


mailbox_message_bodies = sa.Table(
    "mailbox_message_bodies",
    metadata,
    sa.Column("schema_version", sa.SmallInteger(), nullable=False),
    sa.Column("workspace_id", _ID, nullable=False),
    sa.Column("owner_user_id", _ID, nullable=False),
    sa.Column("mailbox_id", _MAILBOX_ID, nullable=False),
    sa.Column("message_id", _ID, nullable=False),
    sa.Column("body_text", sa.Text(collation="C"), nullable=True),
    sa.Column("body_html", sa.Text(collation="C"), nullable=True),
    sa.Column("content_hash", _HASH, nullable=False),
    sa.Column("body_version", sa.BigInteger(), nullable=False),
    sa.Column("fetched_at", _TIMESTAMP, nullable=False),
    sa.Column("row_version", sa.BigInteger(), nullable=False),
    sa.PrimaryKeyConstraint(
        "workspace_id", "owner_user_id", "mailbox_id", "message_id",
        name="pk_mailbox_message_bodies",
    ),
    sa.ForeignKeyConstraint(
        ("workspace_id", "owner_user_id", "mailbox_id", "message_id"),
        (
            "cuevion_mailbox.mailbox_messages.workspace_id",
            "cuevion_mailbox.mailbox_messages.owner_user_id",
            "cuevion_mailbox.mailbox_messages.mailbox_id",
            "cuevion_mailbox.mailbox_messages.message_id",
        ),
        name="fk_mailbox_message_bodies_message",
        ondelete="CASCADE",
    ),
    sa.CheckConstraint("schema_version = 1", name="schema_version_one"),
    sa.CheckConstraint("body_version > 0", name="body_version_positive"),
    sa.CheckConstraint("row_version > 0", name="row_version_positive"),
    sa.CheckConstraint(
        "content_hash ~ '^[0-9a-f]{64}$'",
        name="content_hash_canonical",
    ),
)


mailbox_change_outbox = sa.Table(
    "mailbox_change_outbox",
    metadata,
    sa.Column("schema_version", sa.SmallInteger(), nullable=False),
    sa.Column("event_id", _ID, nullable=False),
    sa.Column("workspace_id", _ID, nullable=False),
    sa.Column("owner_user_id", _ID, nullable=False),
    sa.Column("mailbox_id", _MAILBOX_ID, nullable=False),
    sa.Column("source_generation", sa.BigInteger(), nullable=False),
    sa.Column("message_id", _ID, nullable=False),
    sa.Column("message_row_version", sa.BigInteger(), nullable=False),
    sa.Column("event_type", sa.Text(collation="C"), nullable=False),
    sa.Column("created_at", _TIMESTAMP, nullable=False),
    sa.Column("attempt_count", sa.Integer(), nullable=False),
    sa.Column("next_attempt_at", _TIMESTAMP, nullable=True),
    sa.Column("claim_token", sa.String(64, collation="C"), nullable=True),
    sa.Column("claim_expires_at", _TIMESTAMP, nullable=True),
    sa.Column("processed_at", _TIMESTAMP, nullable=True),
    sa.Column("last_error_code", _CODE, nullable=True),
    sa.PrimaryKeyConstraint("event_id", name="pk_mailbox_change_outbox"),
    sa.UniqueConstraint(
        "workspace_id", "owner_user_id", "mailbox_id",
        "source_generation", "message_id", "message_row_version", "event_type",
        name="uq_mailbox_change_outbox_message_version_event",
    ),
    sa.ForeignKeyConstraint(
        (
            "workspace_id", "owner_user_id", "mailbox_id",
            "source_generation", "message_id",
        ),
        (
            "cuevion_mailbox.mailbox_messages.workspace_id",
            "cuevion_mailbox.mailbox_messages.owner_user_id",
            "cuevion_mailbox.mailbox_messages.mailbox_id",
            "cuevion_mailbox.mailbox_messages.source_generation",
            "cuevion_mailbox.mailbox_messages.message_id",
        ),
        name="fk_mailbox_change_outbox_message_generation",
        ondelete="CASCADE",
    ),
    sa.CheckConstraint("schema_version = 1", name="schema_version_one"),
    sa.CheckConstraint("source_generation > 0", name="source_generation_positive"),
    sa.CheckConstraint("message_row_version > 0", name="message_row_version_positive"),
    sa.CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
    sa.CheckConstraint(
        "(claim_token IS NULL AND claim_expires_at IS NULL) OR "
        "(claim_token IS NOT NULL AND claim_expires_at IS NOT NULL)",
        name="claim_shape",
    ),
    sa.CheckConstraint(
        "claim_token IS NULL OR octet_length(claim_token) BETWEEN 16 AND 64",
        name="claim_token_bounded",
    ),
    sa.CheckConstraint(
        "event_type IN ('message_added','message_changed','message_deleted')",
        name="event_type_supported",
    ),
    sa.CheckConstraint(
        "last_error_code IS NULL OR octet_length(last_error_code) BETWEEN 1 AND 128",
        name="last_error_code_bounded",
    ),
)

sa.Index(
    "ix_mailbox_change_outbox_ready",
    mailbox_change_outbox.c.processed_at,
    mailbox_change_outbox.c.next_attempt_at,
    mailbox_change_outbox.c.claim_expires_at,
    mailbox_change_outbox.c.created_at,
)


MAILBOX_TABLES = (
    mailbox_sync_state,
    mailbox_sync_cursor,
    mailbox_messages,
    mailbox_message_bodies,
    mailbox_change_outbox,
)

__all__ = (
    "MAILBOX_SCHEMA",
    "MAILBOX_SCHEMA_VERSION",
    "MAILBOX_TABLES",
    "metadata",
    "mailbox_sync_state",
    "mailbox_sync_cursor",
    "mailbox_messages",
    "mailbox_message_bodies",
    "mailbox_change_outbox",
)
