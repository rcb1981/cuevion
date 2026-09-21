"""Create the inactive durable mailbox synchronization schema one."""

from alembic import op


revision = "0002_mailbox_schema_1"
down_revision = "0001_account_schema_1"
branch_labels = None
depends_on = None


_SCHEMA_DDL = r"""CREATE SCHEMA cuevion_mailbox"""

_TABLE_DDL = (
r"""CREATE TABLE cuevion_mailbox.mailbox_sync_state (
    schema_version SMALLINT NOT NULL,
    workspace_id VARCHAR(26) COLLATE "C" NOT NULL,
    owner_user_id VARCHAR(26) COLLATE "C" NOT NULL,
    mailbox_id VARCHAR(160) COLLATE "C" NOT NULL,
    provider TEXT COLLATE "C" NOT NULL,
    provider_account_identity VARCHAR(320) COLLATE "C" NOT NULL,
    source_generation BIGINT NOT NULL,
    is_current BOOLEAN NOT NULL,
    bootstrap_state TEXT COLLATE "C" NOT NULL,
    backfill_cutoff_at TIMESTAMP WITH TIME ZONE,
    backfill_oldest_indexed_at TIMESTAMP WITH TIME ZONE,
    last_successful_sync_at TIMESTAMP WITH TIME ZONE,
    last_error_code VARCHAR(128) COLLATE "C",
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    row_version BIGINT NOT NULL,
    CONSTRAINT pk_mailbox_sync_state PRIMARY KEY (workspace_id, owner_user_id, mailbox_id, source_generation),
    CONSTRAINT uq_mailbox_sync_state_generation_provider UNIQUE (workspace_id, owner_user_id, mailbox_id, source_generation, provider),
    CONSTRAINT fk_mailbox_sync_state_active_membership_scope FOREIGN KEY(workspace_id, owner_user_id) REFERENCES cuevion_account.workspace_memberships (workspace_id, user_id),
    CONSTRAINT ck_mailbox_sync_state_schema_version_one CHECK (schema_version = 1),
    CONSTRAINT ck_mailbox_sync_state_provider_supported CHECK (provider IN ('google', 'custom_imap')),
    CONSTRAINT ck_mailbox_sync_state_bootstrap_state_supported CHECK (bootstrap_state IN ('not_started','recent_sync','recent_ready','backfilling','ready','recovering','blocked')),
    CONSTRAINT ck_mailbox_sync_state_source_generation_positive CHECK (source_generation > 0),
    CONSTRAINT ck_mailbox_sync_state_row_version_positive CHECK (row_version > 0),
    CONSTRAINT ck_mailbox_sync_state_provider_identity_lowercase CHECK (provider_account_identity = lower(provider_account_identity)),
    CONSTRAINT ck_mailbox_sync_state_provider_identity_bounded CHECK (octet_length(provider_account_identity) BETWEEN 3 AND 320),
    CONSTRAINT ck_mailbox_sync_state_last_error_code_bounded CHECK (last_error_code IS NULL OR octet_length(last_error_code) BETWEEN 1 AND 128)
)""",
r"""CREATE TABLE cuevion_mailbox.mailbox_sync_cursor (
    schema_version SMALLINT NOT NULL,
    workspace_id VARCHAR(26) COLLATE "C" NOT NULL,
    owner_user_id VARCHAR(26) COLLATE "C" NOT NULL,
    mailbox_id VARCHAR(160) COLLATE "C" NOT NULL,
    source_generation BIGINT NOT NULL,
    provider TEXT COLLATE "C" NOT NULL,
    scope_key TEXT COLLATE "C" NOT NULL,
    scope_key_digest VARCHAR(64) COLLATE "C" NOT NULL,
    cursor_generation BIGINT NOT NULL,
    gmail_history_id VARCHAR(128) COLLATE "C",
    imap_uid_validity VARCHAR(20) COLLATE "C",
    imap_highest_uid BIGINT,
    imap_uidnext_observed BIGINT,
    backfill_state TEXT COLLATE "C" NOT NULL,
    backfill_cursor TEXT COLLATE "C",
    last_successful_sync_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    row_version BIGINT NOT NULL,
    CONSTRAINT pk_mailbox_sync_cursor PRIMARY KEY (workspace_id, owner_user_id, mailbox_id, source_generation, scope_key_digest),
    CONSTRAINT fk_mailbox_sync_cursor_state_generation FOREIGN KEY(workspace_id, owner_user_id, mailbox_id, source_generation, provider) REFERENCES cuevion_mailbox.mailbox_sync_state (workspace_id, owner_user_id, mailbox_id, source_generation, provider) ON DELETE CASCADE,
    CONSTRAINT ck_mailbox_sync_cursor_schema_version_one CHECK (schema_version = 1),
    CONSTRAINT ck_mailbox_sync_cursor_provider_supported CHECK (provider IN ('google', 'custom_imap')),
    CONSTRAINT ck_mailbox_sync_cursor_cursor_generation_positive CHECK (cursor_generation > 0),
    CONSTRAINT ck_mailbox_sync_cursor_row_version_positive CHECK (row_version > 0),
    CONSTRAINT ck_mailbox_sync_cursor_scope_key_bounded CHECK (octet_length(scope_key) BETWEEN 1 AND 16384),
    CONSTRAINT ck_mailbox_sync_cursor_scope_key_digest_canonical CHECK (scope_key_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_mailbox_sync_cursor_backfill_state_supported CHECK (backfill_state IN ('not_started','running','complete','recovering','blocked')),
    CONSTRAINT ck_mailbox_sync_cursor_provider_cursor_shape CHECK ((provider = 'google' AND scope_key = 'gmail-account' AND gmail_history_id IS NOT NULL AND imap_uid_validity IS NULL AND imap_highest_uid IS NULL AND imap_uidnext_observed IS NULL) OR (provider = 'custom_imap' AND gmail_history_id IS NULL AND imap_uid_validity IS NOT NULL AND imap_highest_uid IS NOT NULL)),
    CONSTRAINT ck_mailbox_sync_cursor_gmail_history_id_digits CHECK (gmail_history_id IS NULL OR gmail_history_id ~ '^[0-9]+$'),
    CONSTRAINT ck_mailbox_sync_cursor_imap_uid_validity_canonical CHECK (imap_uid_validity IS NULL OR imap_uid_validity ~ '^[1-9][0-9]{0,19}$'),
    CONSTRAINT ck_mailbox_sync_cursor_imap_highest_uid_bounded CHECK (imap_highest_uid IS NULL OR imap_highest_uid BETWEEN 0 AND 4294967295),
    CONSTRAINT ck_mailbox_sync_cursor_imap_uidnext_bounded CHECK (imap_uidnext_observed IS NULL OR imap_uidnext_observed BETWEEN 1 AND 4294967296),
    CONSTRAINT ck_mailbox_sync_cursor_backfill_cursor_bounded CHECK (backfill_cursor IS NULL OR octet_length(backfill_cursor) BETWEEN 1 AND 16384)
)""",
r"""CREATE TABLE cuevion_mailbox.mailbox_messages (
    schema_version SMALLINT NOT NULL,
    message_id VARCHAR(26) COLLATE "C" NOT NULL,
    workspace_id VARCHAR(26) COLLATE "C" NOT NULL,
    owner_user_id VARCHAR(26) COLLATE "C" NOT NULL,
    mailbox_id VARCHAR(160) COLLATE "C" NOT NULL,
    source_generation BIGINT NOT NULL,
    provider TEXT COLLATE "C" NOT NULL,
    provider_message_id TEXT COLLATE "C",
    provider_thread_id TEXT COLLATE "C",
    provider_folder TEXT COLLATE "C" NOT NULL,
    provider_folder_digest VARCHAR(64) COLLATE "C" NOT NULL,
    provider_labels JSONB NOT NULL,
    imap_uid_validity VARCHAR(20) COLLATE "C",
    imap_uid BIGINT,
    rfc_message_id TEXT COLLATE "C",
    in_reply_to TEXT COLLATE "C",
    references_json JSONB NOT NULL,
    sender_address VARCHAR(320) COLLATE "C",
    sender_display TEXT COLLATE "C",
    to_json JSONB NOT NULL,
    cc_json JSONB NOT NULL,
    subject TEXT COLLATE "C" NOT NULL,
    snippet TEXT COLLATE "C" NOT NULL,
    provider_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    unread BOOLEAN NOT NULL,
    starred BOOLEAN NOT NULL,
    provider_deleted BOOLEAN NOT NULL,
    body_state TEXT COLLATE "C" NOT NULL,
    metadata_hash VARCHAR(64) COLLATE "C" NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    row_version BIGINT NOT NULL,
    CONSTRAINT pk_mailbox_messages PRIMARY KEY (workspace_id, owner_user_id, mailbox_id, message_id),
    CONSTRAINT uq_mailbox_messages_generation_id UNIQUE (workspace_id, owner_user_id, mailbox_id, source_generation, message_id),
    CONSTRAINT fk_mailbox_messages_state_generation FOREIGN KEY(workspace_id, owner_user_id, mailbox_id, source_generation, provider) REFERENCES cuevion_mailbox.mailbox_sync_state (workspace_id, owner_user_id, mailbox_id, source_generation, provider) ON DELETE CASCADE,
    CONSTRAINT ck_mailbox_messages_schema_version_one CHECK (schema_version = 1),
    CONSTRAINT ck_mailbox_messages_provider_supported CHECK (provider IN ('google', 'custom_imap')),
    CONSTRAINT ck_mailbox_messages_source_generation_positive CHECK (source_generation > 0),
    CONSTRAINT ck_mailbox_messages_row_version_positive CHECK (row_version > 0),
    CONSTRAINT ck_mailbox_messages_body_state_supported CHECK (body_state IN ('not_cached','cached','stale','unavailable')),
    CONSTRAINT ck_mailbox_messages_metadata_hash_canonical CHECK (metadata_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_mailbox_messages_provider_folder_bounded CHECK (octet_length(provider_folder) BETWEEN 1 AND 16384),
    CONSTRAINT ck_mailbox_messages_provider_folder_digest_canonical CHECK (provider_folder_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_mailbox_messages_provider_identity_shape CHECK ((provider = 'google' AND provider_message_id IS NOT NULL AND imap_uid_validity IS NULL AND imap_uid IS NULL) OR (provider = 'custom_imap' AND provider_message_id IS NULL AND imap_uid_validity IS NOT NULL AND imap_uid IS NOT NULL)),
    CONSTRAINT ck_mailbox_messages_provider_message_id_bounded CHECK (provider_message_id IS NULL OR octet_length(provider_message_id) BETWEEN 1 AND 1024),
    CONSTRAINT ck_mailbox_messages_provider_thread_id_bounded CHECK (provider_thread_id IS NULL OR octet_length(provider_thread_id) BETWEEN 1 AND 1024),
    CONSTRAINT ck_mailbox_messages_imap_uid_validity_canonical CHECK (imap_uid_validity IS NULL OR imap_uid_validity ~ '^[1-9][0-9]{0,19}$'),
    CONSTRAINT ck_mailbox_messages_imap_uid_bounded CHECK (imap_uid IS NULL OR imap_uid BETWEEN 1 AND 4294967295)
)""",
r"""CREATE TABLE cuevion_mailbox.mailbox_message_bodies (
    schema_version SMALLINT NOT NULL,
    workspace_id VARCHAR(26) COLLATE "C" NOT NULL,
    owner_user_id VARCHAR(26) COLLATE "C" NOT NULL,
    mailbox_id VARCHAR(160) COLLATE "C" NOT NULL,
    message_id VARCHAR(26) COLLATE "C" NOT NULL,
    body_text TEXT COLLATE "C",
    body_html TEXT COLLATE "C",
    content_hash VARCHAR(64) COLLATE "C" NOT NULL,
    body_version BIGINT NOT NULL,
    fetched_at TIMESTAMP WITH TIME ZONE NOT NULL,
    row_version BIGINT NOT NULL,
    CONSTRAINT pk_mailbox_message_bodies PRIMARY KEY (workspace_id, owner_user_id, mailbox_id, message_id),
    CONSTRAINT fk_mailbox_message_bodies_message FOREIGN KEY(workspace_id, owner_user_id, mailbox_id, message_id) REFERENCES cuevion_mailbox.mailbox_messages (workspace_id, owner_user_id, mailbox_id, message_id) ON DELETE CASCADE,
    CONSTRAINT ck_mailbox_message_bodies_schema_version_one CHECK (schema_version = 1),
    CONSTRAINT ck_mailbox_message_bodies_body_version_positive CHECK (body_version > 0),
    CONSTRAINT ck_mailbox_message_bodies_row_version_positive CHECK (row_version > 0),
    CONSTRAINT ck_mailbox_message_bodies_content_hash_canonical CHECK (content_hash ~ '^[0-9a-f]{64}$')
)""",
r"""CREATE TABLE cuevion_mailbox.mailbox_change_outbox (
    schema_version SMALLINT NOT NULL,
    event_id VARCHAR(26) COLLATE "C" NOT NULL,
    workspace_id VARCHAR(26) COLLATE "C" NOT NULL,
    owner_user_id VARCHAR(26) COLLATE "C" NOT NULL,
    mailbox_id VARCHAR(160) COLLATE "C" NOT NULL,
    source_generation BIGINT NOT NULL,
    message_id VARCHAR(26) COLLATE "C" NOT NULL,
    message_row_version BIGINT NOT NULL,
    event_type TEXT COLLATE "C" NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    attempt_count INTEGER NOT NULL,
    next_attempt_at TIMESTAMP WITH TIME ZONE,
    claim_token VARCHAR(64) COLLATE "C",
    claim_expires_at TIMESTAMP WITH TIME ZONE,
    processed_at TIMESTAMP WITH TIME ZONE,
    last_error_code VARCHAR(128) COLLATE "C",
    CONSTRAINT pk_mailbox_change_outbox PRIMARY KEY (event_id),
    CONSTRAINT uq_mailbox_change_outbox_message_version_event UNIQUE (workspace_id, owner_user_id, mailbox_id, source_generation, message_id, message_row_version, event_type),
    CONSTRAINT fk_mailbox_change_outbox_message_generation FOREIGN KEY(workspace_id, owner_user_id, mailbox_id, source_generation, message_id) REFERENCES cuevion_mailbox.mailbox_messages (workspace_id, owner_user_id, mailbox_id, source_generation, message_id) ON DELETE CASCADE,
    CONSTRAINT ck_mailbox_change_outbox_schema_version_one CHECK (schema_version = 1),
    CONSTRAINT ck_mailbox_change_outbox_source_generation_positive CHECK (source_generation > 0),
    CONSTRAINT ck_mailbox_change_outbox_message_row_version_positive CHECK (message_row_version > 0),
    CONSTRAINT ck_mailbox_change_outbox_attempt_count_nonnegative CHECK (attempt_count >= 0),
    CONSTRAINT ck_mailbox_change_outbox_claim_shape CHECK ((claim_token IS NULL AND claim_expires_at IS NULL) OR (claim_token IS NOT NULL AND claim_expires_at IS NOT NULL)),
    CONSTRAINT ck_mailbox_change_outbox_claim_token_bounded CHECK (claim_token IS NULL OR octet_length(claim_token) BETWEEN 16 AND 64),
    CONSTRAINT ck_mailbox_change_outbox_event_type_supported CHECK (event_type IN ('message_added','message_changed','message_deleted')),
    CONSTRAINT ck_mailbox_change_outbox_last_error_code_bounded CHECK (last_error_code IS NULL OR octet_length(last_error_code) BETWEEN 1 AND 128)
)""",
)

_INDEX_DDL = (
    r"""CREATE UNIQUE INDEX ux_mailbox_sync_state_current ON cuevion_mailbox.mailbox_sync_state (workspace_id, owner_user_id, mailbox_id) WHERE is_current IS true""",
    r"""CREATE INDEX ix_mailbox_messages_visible_order ON cuevion_mailbox.mailbox_messages (workspace_id, owner_user_id, mailbox_id, source_generation, provider_deleted, provider_timestamp)""",
    r"""CREATE UNIQUE INDEX ux_mailbox_messages_gmail_identity ON cuevion_mailbox.mailbox_messages (workspace_id, owner_user_id, mailbox_id, source_generation, provider_message_id) WHERE provider = 'google' AND provider_message_id IS NOT NULL""",
    r"""CREATE UNIQUE INDEX ux_mailbox_messages_imap_identity ON cuevion_mailbox.mailbox_messages (workspace_id, owner_user_id, mailbox_id, source_generation, provider_folder_digest, imap_uid_validity, imap_uid) WHERE provider = 'custom_imap' AND imap_uid_validity IS NOT NULL AND imap_uid IS NOT NULL""",
    r"""CREATE INDEX ix_mailbox_change_outbox_ready ON cuevion_mailbox.mailbox_change_outbox (processed_at, next_attempt_at, claim_expires_at, created_at)""",
)

_PUBLIC_ACL_DDL = (
    r"""REVOKE ALL ON SCHEMA cuevion_mailbox FROM PUBLIC""",
    r"""REVOKE ALL ON ALL TABLES IN SCHEMA cuevion_mailbox FROM PUBLIC""",
)


def upgrade() -> None:
    op.execute(_SCHEMA_DDL)
    for statement in _TABLE_DDL:
        op.execute(statement)
    for statement in _INDEX_DDL:
        op.execute(statement)
    for statement in _PUBLIC_ACL_DDL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("cuevion mailbox migrations are forward-only")
