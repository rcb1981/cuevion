"""Create the inactive PostgreSQL account authority schema one."""

from alembic import op


revision = "0001_account_schema_1"
down_revision = None
branch_labels = None
depends_on = None


# Revision-owned DDL is intentionally frozen: historical migrations must not
# import mutable application metadata or contract manifests.
_ACCOUNT_TABLE_NAMES = (
    "users",
    "verified_emails",
    "authentication_identities",
    "workspaces",
    "workspace_memberships",
    "initial_account_operations",
    "security_events",
)
_SCHEMA_DDL = r"""CREATE SCHEMA cuevion_account"""
_SEQUENCE_DDL = r"""CREATE SEQUENCE cuevion_account.security_event_stream_position_seq INCREMENT BY 1 START WITH 1 MINVALUE 1 NO CYCLE"""
_TABLE_DDL = (
    r"""CREATE TABLE cuevion_account.users (
	schema_version SMALLINT NOT NULL,
	user_id VARCHAR(26) COLLATE "C" NOT NULL,
	status TEXT COLLATE "C" NOT NULL,
	primary_verified_email_id VARCHAR(26) COLLATE "C",
	display_name TEXT COLLATE "C" NOT NULL,
	security_epoch BIGINT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	row_version BIGINT NOT NULL,
	CONSTRAINT pk_users PRIMARY KEY (user_id),
	CONSTRAINT ck_users_schema_version_schema_one CHECK (schema_version = 1),
	CONSTRAINT ck_users_user_id_canonical CHECK (user_id ~ '^usr_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_users_status_closed CHECK (status IN ('active', 'suspended', 'disabled')),
	CONSTRAINT ck_users_primary_verified_email_id_canonical CHECK (primary_verified_email_id ~ '^vem_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_users_display_name_utf8_length CHECK (octet_length(display_name) > 0 AND octet_length(display_name) <= 256),
	CONSTRAINT ck_users_security_epoch_positive CHECK (security_epoch > 0),
	CONSTRAINT ck_users_created_at_timestamp CHECK (isfinite(created_at) AND created_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND created_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND created_at = date_trunc('second', created_at)),
	CONSTRAINT ck_users_updated_at_timestamp CHECK (isfinite(updated_at) AND updated_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND updated_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND updated_at = date_trunc('second', updated_at)),
	CONSTRAINT ck_users_row_version_positive CHECK (row_version > 0),
	CONSTRAINT ck_users_timestamp_order CHECK (created_at <= updated_at),
	CONSTRAINT ck_users_active_primary_email CHECK (status != 'active' OR primary_verified_email_id IS NOT NULL)
)""",
    r"""CREATE TABLE cuevion_account.verified_emails (
	schema_version SMALLINT NOT NULL,
	email_id VARCHAR(26) COLLATE "C" NOT NULL,
	user_id VARCHAR(26) COLLATE "C" NOT NULL,
	canonical_email VARCHAR(320) COLLATE "C" NOT NULL,
	status TEXT COLLATE "C" NOT NULL,
	verification_source VARCHAR(128) COLLATE "C" NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	verified_at TIMESTAMP WITH TIME ZONE,
	retired_at TIMESTAMP WITH TIME ZONE,
	row_version BIGINT NOT NULL,
	CONSTRAINT pk_verified_emails PRIMARY KEY (email_id),
	CONSTRAINT ck_verified_emails_schema_version_schema_one CHECK (schema_version = 1),
	CONSTRAINT ck_verified_emails_email_id_canonical CHECK (email_id ~ '^vem_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_verified_emails_user_id_canonical CHECK (user_id ~ '^usr_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_verified_emails_canonical_email_canonical CHECK ((canonical_email ~ '^[a-z0-9!#$%&''*+/=?^_`{|}~-]+(?:[.][a-z0-9!#$%&''*+/=?^_`{|}~-]+)*@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:[.][a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$') AND char_length(split_part(canonical_email, '@', 1)) <= 64 AND char_length(split_part(canonical_email, '@', 2)) <= 253),
	CONSTRAINT ck_verified_emails_status_closed CHECK (status IN ('pending', 'verified', 'retired')),
	CONSTRAINT ck_verified_emails_verification_source_ascii CHECK (verification_source ~ '^[!-~]{1,128}$'),
	CONSTRAINT ck_verified_emails_created_at_timestamp CHECK (isfinite(created_at) AND created_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND created_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND created_at = date_trunc('second', created_at)),
	CONSTRAINT ck_verified_emails_verified_at_timestamp CHECK (verified_at IS NULL OR (isfinite(verified_at) AND verified_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND verified_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND verified_at = date_trunc('second', verified_at))),
	CONSTRAINT ck_verified_emails_retired_at_timestamp CHECK (retired_at IS NULL OR (isfinite(retired_at) AND retired_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND retired_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND retired_at = date_trunc('second', retired_at))),
	CONSTRAINT ck_verified_emails_row_version_positive CHECK (row_version > 0),
	CONSTRAINT uq_verified_emails_id_user UNIQUE (email_id, user_id),
	CONSTRAINT fk_verified_emails_user FOREIGN KEY(user_id) REFERENCES cuevion_account.users (user_id) MATCH SIMPLE ON DELETE NO ACTION ON UPDATE NO ACTION,
	CONSTRAINT ck_verified_emails_lifecycle CHECK (status = 'pending' AND verified_at IS NULL AND retired_at IS NULL OR status = 'verified' AND verified_at IS NOT NULL AND retired_at IS NULL AND created_at <= verified_at OR status = 'retired' AND verified_at IS NOT NULL AND retired_at IS NOT NULL AND created_at <= verified_at AND verified_at <= retired_at)
)""",
    r"""CREATE TABLE cuevion_account.authentication_identities (
	schema_version SMALLINT NOT NULL,
	identity_id VARCHAR(26) COLLATE "C" NOT NULL,
	user_id VARCHAR(26) COLLATE "C" NOT NULL,
	issuer VARCHAR(512) COLLATE "C" NOT NULL,
	subject VARCHAR(512) COLLATE "C" NOT NULL,
	authentication_method TEXT COLLATE "C" NOT NULL,
	status TEXT COLLATE "C" NOT NULL,
	verified_email_id VARCHAR(26) COLLATE "C",
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	last_used_at TIMESTAMP WITH TIME ZONE,
	row_version BIGINT NOT NULL,
	CONSTRAINT pk_auth_identities PRIMARY KEY (identity_id),
	CONSTRAINT ck_authentication_identities_schema_version_schema_one CHECK (schema_version = 1),
	CONSTRAINT ck_authentication_identities_identity_id_canonical CHECK (identity_id ~ '^aid_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_authentication_identities_user_id_canonical CHECK (user_id ~ '^usr_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_authentication_identities_issuer_ascii CHECK (issuer ~ '^[!-~]{1,512}$'),
	CONSTRAINT ck_authentication_identities_subject_ascii CHECK (subject ~ '^[!-~]{1,512}$'),
	CONSTRAINT ck_authentication_identities_authentication_method_closed CHECK (authentication_method IN ('email_otp', 'oidc', 'webauthn')),
	CONSTRAINT ck_authentication_identities_status_closed CHECK (status IN ('active', 'disabled', 'revoked')),
	CONSTRAINT ck_authentication_identities_verified_email_id_canonical CHECK (verified_email_id ~ '^vem_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_authentication_identities_created_at_timestamp CHECK (isfinite(created_at) AND created_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND created_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND created_at = date_trunc('second', created_at)),
	CONSTRAINT ck_authentication_identities_last_used_at_timestamp CHECK (last_used_at IS NULL OR (isfinite(last_used_at) AND last_used_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND last_used_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND last_used_at = date_trunc('second', last_used_at))),
	CONSTRAINT ck_authentication_identities_row_version_positive CHECK (row_version > 0),
	CONSTRAINT uq_auth_identities_id_user UNIQUE (identity_id, user_id),
	CONSTRAINT uq_auth_identities_issuer_subject UNIQUE (issuer, subject),
	CONSTRAINT fk_auth_identities_user FOREIGN KEY(user_id) REFERENCES cuevion_account.users (user_id) MATCH SIMPLE ON DELETE NO ACTION ON UPDATE NO ACTION,
	CONSTRAINT fk_auth_identities_verified_email_same_user FOREIGN KEY(verified_email_id, user_id) REFERENCES cuevion_account.verified_emails (email_id, user_id) MATCH SIMPLE ON DELETE NO ACTION ON UPDATE NO ACTION,
	CONSTRAINT ck_authentication_identities_timestamp_order CHECK (last_used_at IS NULL OR created_at <= last_used_at)
)""",
    r"""CREATE TABLE cuevion_account.workspaces (
	schema_version SMALLINT NOT NULL,
	workspace_id VARCHAR(26) COLLATE "C" NOT NULL,
	status TEXT COLLATE "C" NOT NULL,
	created_by_user_id VARCHAR(26) COLLATE "C" NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	row_version BIGINT NOT NULL,
	CONSTRAINT pk_workspaces PRIMARY KEY (workspace_id),
	CONSTRAINT ck_workspaces_schema_version_schema_one CHECK (schema_version = 1),
	CONSTRAINT ck_workspaces_workspace_id_canonical CHECK (workspace_id ~ '^wsp_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_workspaces_status_closed CHECK (status IN ('active', 'suspended', 'archived')),
	CONSTRAINT ck_workspaces_created_by_user_id_canonical CHECK (created_by_user_id ~ '^usr_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_workspaces_created_at_timestamp CHECK (isfinite(created_at) AND created_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND created_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND created_at = date_trunc('second', created_at)),
	CONSTRAINT ck_workspaces_updated_at_timestamp CHECK (isfinite(updated_at) AND updated_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND updated_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND updated_at = date_trunc('second', updated_at)),
	CONSTRAINT ck_workspaces_row_version_positive CHECK (row_version > 0),
	CONSTRAINT uq_workspaces_id_creator UNIQUE (workspace_id, created_by_user_id),
	CONSTRAINT fk_workspaces_creator FOREIGN KEY(created_by_user_id) REFERENCES cuevion_account.users (user_id) MATCH SIMPLE ON DELETE NO ACTION ON UPDATE NO ACTION,
	CONSTRAINT ck_workspaces_timestamp_order CHECK (created_at <= updated_at)
)""",
    r"""CREATE TABLE cuevion_account.workspace_memberships (
	schema_version SMALLINT NOT NULL,
	workspace_id VARCHAR(26) COLLATE "C" NOT NULL,
	user_id VARCHAR(26) COLLATE "C" NOT NULL,
	role TEXT COLLATE "C" NOT NULL,
	status TEXT COLLATE "C" NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	row_version BIGINT NOT NULL,
	CONSTRAINT pk_workspace_memberships PRIMARY KEY (workspace_id, user_id),
	CONSTRAINT ck_workspace_memberships_schema_version_schema_one CHECK (schema_version = 1),
	CONSTRAINT ck_workspace_memberships_workspace_id_canonical CHECK (workspace_id ~ '^wsp_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_workspace_memberships_user_id_canonical CHECK (user_id ~ '^usr_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_workspace_memberships_role_closed CHECK (role IN ('owner', 'admin', 'member')),
	CONSTRAINT ck_workspace_memberships_status_closed CHECK (status IN ('active', 'suspended', 'removed')),
	CONSTRAINT ck_workspace_memberships_created_at_timestamp CHECK (isfinite(created_at) AND created_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND created_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND created_at = date_trunc('second', created_at)),
	CONSTRAINT ck_workspace_memberships_updated_at_timestamp CHECK (isfinite(updated_at) AND updated_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND updated_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND updated_at = date_trunc('second', updated_at)),
	CONSTRAINT ck_workspace_memberships_row_version_positive CHECK (row_version > 0),
	CONSTRAINT fk_workspace_memberships_workspace FOREIGN KEY(workspace_id) REFERENCES cuevion_account.workspaces (workspace_id) MATCH SIMPLE ON DELETE NO ACTION ON UPDATE NO ACTION,
	CONSTRAINT fk_workspace_memberships_user FOREIGN KEY(user_id) REFERENCES cuevion_account.users (user_id) MATCH SIMPLE ON DELETE NO ACTION ON UPDATE NO ACTION,
	CONSTRAINT ck_workspace_memberships_timestamp_order CHECK (created_at <= updated_at)
)""",
    r"""CREATE TABLE cuevion_account.initial_account_operations (
	operation_record_version SMALLINT NOT NULL,
	reference_schema_version SMALLINT NOT NULL,
	derivation_key_epoch BIGINT NOT NULL,
	operation_digest BYTEA NOT NULL,
	request_snapshot_version SMALLINT NOT NULL,
	request_version SMALLINT NOT NULL,
	snapshot_user_schema_version SMALLINT NOT NULL,
	snapshot_user_user_id VARCHAR(26) COLLATE "C" NOT NULL,
	snapshot_user_status TEXT COLLATE "C" NOT NULL,
	snapshot_user_primary_verified_email_id VARCHAR(26) COLLATE "C" NOT NULL,
	snapshot_user_display_name TEXT COLLATE "C" NOT NULL,
	snapshot_user_security_epoch BIGINT NOT NULL,
	snapshot_user_created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	snapshot_user_updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	snapshot_user_row_version BIGINT NOT NULL,
	snapshot_verified_email_schema_version SMALLINT NOT NULL,
	snapshot_verified_email_email_id VARCHAR(26) COLLATE "C" NOT NULL,
	snapshot_verified_email_user_id VARCHAR(26) COLLATE "C" NOT NULL,
	snapshot_verified_email_canonical_email VARCHAR(320) COLLATE "C" NOT NULL,
	snapshot_verified_email_status TEXT COLLATE "C" NOT NULL,
	snapshot_verified_email_verification_source VARCHAR(128) COLLATE "C" NOT NULL,
	snapshot_verified_email_created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	snapshot_verified_email_verified_at TIMESTAMP WITH TIME ZONE NOT NULL,
	snapshot_verified_email_retired_at TIMESTAMP WITH TIME ZONE,
	snapshot_verified_email_row_version BIGINT NOT NULL,
	snapshot_authentication_identity_schema_version SMALLINT NOT NULL,
	snapshot_authentication_identity_identity_id VARCHAR(26) COLLATE "C" NOT NULL,
	snapshot_authentication_identity_user_id VARCHAR(26) COLLATE "C" NOT NULL,
	snapshot_authentication_identity_issuer VARCHAR(512) COLLATE "C" NOT NULL,
	snapshot_authentication_identity_subject VARCHAR(512) COLLATE "C" NOT NULL,
	snapshot_authentication_identity_authentication_method TEXT COLLATE "C" NOT NULL,
	snapshot_authentication_identity_status TEXT COLLATE "C" NOT NULL,
	snapshot_authentication_identity_verified_email_id VARCHAR(26) COLLATE "C" NOT NULL,
	snapshot_authentication_identity_created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	snapshot_authentication_identity_last_used_at TIMESTAMP WITH TIME ZONE,
	snapshot_authentication_identity_row_version BIGINT NOT NULL,
	snapshot_workspace_schema_version SMALLINT NOT NULL,
	snapshot_workspace_workspace_id VARCHAR(26) COLLATE "C" NOT NULL,
	snapshot_workspace_status TEXT COLLATE "C" NOT NULL,
	snapshot_workspace_created_by_user_id VARCHAR(26) COLLATE "C" NOT NULL,
	snapshot_workspace_created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	snapshot_workspace_updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	snapshot_workspace_row_version BIGINT NOT NULL,
	snapshot_workspace_membership_schema_version SMALLINT NOT NULL,
	snapshot_workspace_membership_workspace_id VARCHAR(26) COLLATE "C" NOT NULL,
	snapshot_workspace_membership_user_id VARCHAR(26) COLLATE "C" NOT NULL,
	snapshot_workspace_membership_role TEXT COLLATE "C" NOT NULL,
	snapshot_workspace_membership_status TEXT COLLATE "C" NOT NULL,
	snapshot_workspace_membership_created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	snapshot_workspace_membership_updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	snapshot_workspace_membership_row_version BIGINT NOT NULL,
	snapshot_authentication_evidence_schema_version SMALLINT NOT NULL,
	snapshot_authentication_evidence_trust_domain VARCHAR(128) COLLATE "C" NOT NULL,
	snapshot_authentication_evidence_verification_coordinator_id VARCHAR(128) COLLATE "C" NOT NULL,
	snapshot_authentication_evidence_assertion_id BYTEA NOT NULL,
	snapshot_authentication_evidence_issuer VARCHAR(512) COLLATE "C" NOT NULL,
	snapshot_authentication_evidence_subject VARCHAR(512) COLLATE "C" NOT NULL,
	snapshot_authentication_evidence_authentication_method TEXT COLLATE "C" NOT NULL,
	snapshot_authentication_evidence_canonical_verified_email VARCHAR(320) COLLATE "C" NOT NULL,
	snapshot_authentication_evidence_verified_at TIMESTAMP WITH TIME ZONE NOT NULL,
	snapshot_authentication_evidence_issued_at TIMESTAMP WITH TIME ZONE NOT NULL,
	snapshot_authentication_evidence_expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	snapshot_security_event_schema_version SMALLINT NOT NULL,
	snapshot_security_event_event_id VARCHAR(26) COLLATE "C" NOT NULL,
	snapshot_security_event_event_type TEXT COLLATE "C" NOT NULL,
	receipt_version SMALLINT NOT NULL,
	receipt_user_id VARCHAR(26) COLLATE "C" NOT NULL,
	receipt_verified_email_id VARCHAR(26) COLLATE "C" NOT NULL,
	receipt_authentication_identity_id VARCHAR(26) COLLATE "C" NOT NULL,
	receipt_workspace_id VARCHAR(26) COLLATE "C" NOT NULL,
	receipt_security_event_id VARCHAR(26) COLLATE "C" NOT NULL,
	committed_at TIMESTAMP WITH TIME ZONE NOT NULL,
	row_version BIGINT NOT NULL,
	CONSTRAINT pk_initial_account_operations PRIMARY KEY (reference_schema_version, derivation_key_epoch, operation_digest),
	CONSTRAINT ck_initial_account_operations_operation_record_version_717c860b CHECK (operation_record_version = 1),
	CONSTRAINT ck_initial_account_operations_reference_schema_version_58bfbb0c CHECK (reference_schema_version = 1),
	CONSTRAINT ck_initial_account_operations_derivation_key_epoch_range CHECK (derivation_key_epoch BETWEEN 1 AND 4294967295),
	CONSTRAINT ck_initial_account_operations_operation_digest_32_bytes CHECK (octet_length(operation_digest) = 32),
	CONSTRAINT ck_initial_account_operations_request_snapshot_version_2dead7f1 CHECK (request_snapshot_version = 1),
	CONSTRAINT ck_initial_account_operations_request_version_schema_one CHECK (request_version = 1),
	CONSTRAINT ck_initial_account_operations_snapshot_user_schema_ver_dd642baf CHECK (snapshot_user_schema_version = 1),
	CONSTRAINT ck_initial_account_operations_snapshot_user_user_id_canonical CHECK (snapshot_user_user_id ~ '^usr_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_initial_account_operations_snapshot_user_status_closed CHECK (snapshot_user_status IN ('active', 'suspended', 'disabled')),
	CONSTRAINT ck_initial_account_operations_snapshot_user_primary_ve_19a6ef52 CHECK (snapshot_user_primary_verified_email_id ~ '^vem_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_initial_account_operations_snapshot_user_display_na_85f67a65 CHECK (octet_length(snapshot_user_display_name) > 0 AND octet_length(snapshot_user_display_name) <= 256),
	CONSTRAINT ck_initial_account_operations_snapshot_user_security_e_f77bd85b CHECK (snapshot_user_security_epoch > 0),
	CONSTRAINT ck_initial_account_operations_snapshot_user_created_at_48d820c3 CHECK (isfinite(snapshot_user_created_at) AND snapshot_user_created_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND snapshot_user_created_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND snapshot_user_created_at = date_trunc('second', snapshot_user_created_at)),
	CONSTRAINT ck_initial_account_operations_snapshot_user_updated_at_e91a817b CHECK (isfinite(snapshot_user_updated_at) AND snapshot_user_updated_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND snapshot_user_updated_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND snapshot_user_updated_at = date_trunc('second', snapshot_user_updated_at)),
	CONSTRAINT ck_initial_account_operations_snapshot_user_row_versio_c3e16972 CHECK (snapshot_user_row_version > 0),
	CONSTRAINT ck_initial_account_operations_snapshot_verified_email__896234ca CHECK (snapshot_verified_email_schema_version = 1),
	CONSTRAINT ck_initial_account_operations_snapshot_verified_email__ec633247 CHECK (snapshot_verified_email_email_id ~ '^vem_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_initial_account_operations_snapshot_verified_email__3b35dea2 CHECK (snapshot_verified_email_user_id ~ '^usr_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_initial_account_operations_snapshot_verified_email__48e09ecb CHECK ((snapshot_verified_email_canonical_email ~ '^[a-z0-9!#$%&''*+/=?^_`{|}~-]+(?:[.][a-z0-9!#$%&''*+/=?^_`{|}~-]+)*@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:[.][a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$') AND char_length(split_part(snapshot_verified_email_canonical_email, '@', 1)) <= 64 AND char_length(split_part(snapshot_verified_email_canonical_email, '@', 2)) <= 253),
	CONSTRAINT ck_initial_account_operations_snapshot_verified_email__31cd66be CHECK (snapshot_verified_email_status IN ('pending', 'verified', 'retired')),
	CONSTRAINT ck_initial_account_operations_snapshot_verified_email__a95325a3 CHECK (snapshot_verified_email_verification_source ~ '^[!-~]{1,128}$'),
	CONSTRAINT ck_initial_account_operations_snapshot_verified_email__7b6932de CHECK (isfinite(snapshot_verified_email_created_at) AND snapshot_verified_email_created_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND snapshot_verified_email_created_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND snapshot_verified_email_created_at = date_trunc('second', snapshot_verified_email_created_at)),
	CONSTRAINT ck_initial_account_operations_snapshot_verified_email__53a41d3f CHECK (isfinite(snapshot_verified_email_verified_at) AND snapshot_verified_email_verified_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND snapshot_verified_email_verified_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND snapshot_verified_email_verified_at = date_trunc('second', snapshot_verified_email_verified_at)),
	CONSTRAINT ck_initial_account_operations_snapshot_verified_email__889a5621 CHECK (snapshot_verified_email_retired_at IS NULL OR (isfinite(snapshot_verified_email_retired_at) AND snapshot_verified_email_retired_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND snapshot_verified_email_retired_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND snapshot_verified_email_retired_at = date_trunc('second', snapshot_verified_email_retired_at))),
	CONSTRAINT ck_initial_account_operations_snapshot_verified_email__d98fad96 CHECK (snapshot_verified_email_row_version > 0),
	CONSTRAINT ck_initial_account_operations_snapshot_authentication__70b93be6 CHECK (snapshot_authentication_identity_schema_version = 1),
	CONSTRAINT ck_initial_account_operations_snapshot_authentication__26840c19 CHECK (snapshot_authentication_identity_identity_id ~ '^aid_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_initial_account_operations_snapshot_authentication__b6f25eec CHECK (snapshot_authentication_identity_user_id ~ '^usr_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_initial_account_operations_snapshot_authentication__04890265 CHECK (snapshot_authentication_identity_issuer ~ '^[!-~]{1,512}$'),
	CONSTRAINT ck_initial_account_operations_snapshot_authentication__b9d2e30f CHECK (snapshot_authentication_identity_subject ~ '^[!-~]{1,512}$'),
	CONSTRAINT ck_initial_account_operations_snapshot_authentication__39f7b050 CHECK (snapshot_authentication_identity_authentication_method IN ('email_otp', 'oidc', 'webauthn')),
	CONSTRAINT ck_initial_account_operations_snapshot_authentication__4e7c4c93 CHECK (snapshot_authentication_identity_status IN ('active', 'disabled', 'revoked')),
	CONSTRAINT ck_initial_account_operations_snapshot_authentication__8881744a CHECK (snapshot_authentication_identity_verified_email_id ~ '^vem_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_initial_account_operations_snapshot_authentication__cbe842e9 CHECK (isfinite(snapshot_authentication_identity_created_at) AND snapshot_authentication_identity_created_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND snapshot_authentication_identity_created_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND snapshot_authentication_identity_created_at = date_trunc('second', snapshot_authentication_identity_created_at)),
	CONSTRAINT ck_initial_account_operations_snapshot_authentication__b60d01cc CHECK (snapshot_authentication_identity_last_used_at IS NULL OR (isfinite(snapshot_authentication_identity_last_used_at) AND snapshot_authentication_identity_last_used_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND snapshot_authentication_identity_last_used_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND snapshot_authentication_identity_last_used_at = date_trunc('second', snapshot_authentication_identity_last_used_at))),
	CONSTRAINT ck_initial_account_operations_snapshot_authentication__be407d1a CHECK (snapshot_authentication_identity_row_version > 0),
	CONSTRAINT ck_initial_account_operations_snapshot_workspace_schem_05a09e5f CHECK (snapshot_workspace_schema_version = 1),
	CONSTRAINT ck_initial_account_operations_snapshot_workspace_works_5c608029 CHECK (snapshot_workspace_workspace_id ~ '^wsp_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_initial_account_operations_snapshot_workspace_status_closed CHECK (snapshot_workspace_status IN ('active', 'suspended', 'archived')),
	CONSTRAINT ck_initial_account_operations_snapshot_workspace_creat_63908cb1 CHECK (snapshot_workspace_created_by_user_id ~ '^usr_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_initial_account_operations_snapshot_workspace_creat_f5479908 CHECK (isfinite(snapshot_workspace_created_at) AND snapshot_workspace_created_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND snapshot_workspace_created_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND snapshot_workspace_created_at = date_trunc('second', snapshot_workspace_created_at)),
	CONSTRAINT ck_initial_account_operations_snapshot_workspace_updat_90d99b6f CHECK (isfinite(snapshot_workspace_updated_at) AND snapshot_workspace_updated_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND snapshot_workspace_updated_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND snapshot_workspace_updated_at = date_trunc('second', snapshot_workspace_updated_at)),
	CONSTRAINT ck_initial_account_operations_snapshot_workspace_row_v_080ea77d CHECK (snapshot_workspace_row_version > 0),
	CONSTRAINT ck_initial_account_operations_snapshot_workspace_membe_6fda33ce CHECK (snapshot_workspace_membership_schema_version = 1),
	CONSTRAINT ck_initial_account_operations_snapshot_workspace_membe_2acc29ab CHECK (snapshot_workspace_membership_workspace_id ~ '^wsp_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_initial_account_operations_snapshot_workspace_membe_6b3196ad CHECK (snapshot_workspace_membership_user_id ~ '^usr_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_initial_account_operations_snapshot_workspace_membe_7da77d48 CHECK (snapshot_workspace_membership_role IN ('owner', 'admin', 'member')),
	CONSTRAINT ck_initial_account_operations_snapshot_workspace_membe_d60051a7 CHECK (snapshot_workspace_membership_status IN ('active', 'suspended', 'removed')),
	CONSTRAINT ck_initial_account_operations_snapshot_workspace_membe_5c1fef8f CHECK (isfinite(snapshot_workspace_membership_created_at) AND snapshot_workspace_membership_created_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND snapshot_workspace_membership_created_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND snapshot_workspace_membership_created_at = date_trunc('second', snapshot_workspace_membership_created_at)),
	CONSTRAINT ck_initial_account_operations_snapshot_workspace_membe_f506b382 CHECK (isfinite(snapshot_workspace_membership_updated_at) AND snapshot_workspace_membership_updated_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND snapshot_workspace_membership_updated_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND snapshot_workspace_membership_updated_at = date_trunc('second', snapshot_workspace_membership_updated_at)),
	CONSTRAINT ck_initial_account_operations_snapshot_workspace_membe_2b6b0fc7 CHECK (snapshot_workspace_membership_row_version > 0),
	CONSTRAINT ck_initial_account_operations_snapshot_authentication__4e5b11de CHECK (snapshot_authentication_evidence_schema_version = 1),
	CONSTRAINT ck_initial_account_operations_snapshot_authentication__791f98f2 CHECK (snapshot_authentication_evidence_trust_domain ~ '^[A-Za-z0-9._:-]{1,128}$'),
	CONSTRAINT ck_initial_account_operations_snapshot_authentication__af70db7a CHECK (snapshot_authentication_evidence_verification_coordinator_id ~ '^[A-Za-z0-9._:-]{1,128}$'),
	CONSTRAINT ck_initial_account_operations_snapshot_authentication__5b8ccdf4 CHECK (octet_length(snapshot_authentication_evidence_assertion_id) = 32),
	CONSTRAINT ck_initial_account_operations_snapshot_authentication__0c556b23 CHECK (snapshot_authentication_evidence_issuer ~ '^[!-~]{1,512}$'),
	CONSTRAINT ck_initial_account_operations_snapshot_authentication__525f7f77 CHECK (snapshot_authentication_evidence_subject ~ '^[!-~]{1,512}$'),
	CONSTRAINT ck_initial_account_operations_snapshot_authentication__32720722 CHECK (snapshot_authentication_evidence_authentication_method IN ('email_otp', 'oidc', 'webauthn')),
	CONSTRAINT ck_initial_account_operations_snapshot_authentication__90799ed9 CHECK ((snapshot_authentication_evidence_canonical_verified_email ~ '^[a-z0-9!#$%&''*+/=?^_`{|}~-]+(?:[.][a-z0-9!#$%&''*+/=?^_`{|}~-]+)*@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:[.][a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$') AND char_length(split_part(snapshot_authentication_evidence_canonical_verified_email, '@', 1)) <= 64 AND char_length(split_part(snapshot_authentication_evidence_canonical_verified_email, '@', 2)) <= 253),
	CONSTRAINT ck_initial_account_operations_snapshot_authentication__efd795de CHECK (isfinite(snapshot_authentication_evidence_verified_at) AND snapshot_authentication_evidence_verified_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND snapshot_authentication_evidence_verified_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND snapshot_authentication_evidence_verified_at = date_trunc('second', snapshot_authentication_evidence_verified_at)),
	CONSTRAINT ck_initial_account_operations_snapshot_authentication__2746c6e2 CHECK (isfinite(snapshot_authentication_evidence_issued_at) AND snapshot_authentication_evidence_issued_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND snapshot_authentication_evidence_issued_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND snapshot_authentication_evidence_issued_at = date_trunc('second', snapshot_authentication_evidence_issued_at)),
	CONSTRAINT ck_initial_account_operations_snapshot_authentication__c5f896be CHECK (isfinite(snapshot_authentication_evidence_expires_at) AND snapshot_authentication_evidence_expires_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND snapshot_authentication_evidence_expires_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND snapshot_authentication_evidence_expires_at = date_trunc('second', snapshot_authentication_evidence_expires_at)),
	CONSTRAINT ck_initial_account_operations_snapshot_security_event__f3243318 CHECK (snapshot_security_event_schema_version = 1),
	CONSTRAINT ck_initial_account_operations_snapshot_security_event__ca5fddc9 CHECK (snapshot_security_event_event_id ~ '^sev_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_initial_account_operations_snapshot_security_event__55191972 CHECK (snapshot_security_event_event_type IN ('initial_account_created')),
	CONSTRAINT ck_initial_account_operations_receipt_version_schema_one CHECK (receipt_version = 1),
	CONSTRAINT ck_initial_account_operations_receipt_user_id_canonical CHECK (receipt_user_id ~ '^usr_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_initial_account_operations_receipt_verified_email_i_3ea619c8 CHECK (receipt_verified_email_id ~ '^vem_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_initial_account_operations_receipt_authentication_i_6d692d22 CHECK (receipt_authentication_identity_id ~ '^aid_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_initial_account_operations_receipt_workspace_id_canonical CHECK (receipt_workspace_id ~ '^wsp_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_initial_account_operations_receipt_security_event_i_46e21c74 CHECK (receipt_security_event_id ~ '^sev_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_initial_account_operations_committed_at_timestamp CHECK (isfinite(committed_at) AND committed_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND committed_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND committed_at = date_trunc('second', committed_at)),
	CONSTRAINT ck_initial_account_operations_row_version_exact_one CHECK (row_version = 1),
	CONSTRAINT uq_initial_ops_evidence_assertion UNIQUE (snapshot_authentication_evidence_trust_domain, snapshot_authentication_evidence_verification_coordinator_id, snapshot_authentication_evidence_assertion_id),
	CONSTRAINT uq_initial_ops_receipt_event UNIQUE (receipt_security_event_id),
	CONSTRAINT uq_initial_ops_event_binding UNIQUE (reference_schema_version, derivation_key_epoch, operation_digest, snapshot_security_event_schema_version, snapshot_security_event_event_id, snapshot_security_event_event_type, snapshot_authentication_evidence_trust_domain, snapshot_authentication_evidence_verification_coordinator_id, snapshot_user_user_id, snapshot_verified_email_email_id, snapshot_authentication_identity_identity_id, snapshot_workspace_workspace_id, snapshot_workspace_membership_workspace_id, snapshot_workspace_membership_user_id, snapshot_user_security_epoch),
	CONSTRAINT fk_initial_ops_receipt_user FOREIGN KEY(receipt_user_id) REFERENCES cuevion_account.users (user_id) MATCH SIMPLE ON DELETE NO ACTION ON UPDATE NO ACTION,
	CONSTRAINT fk_initial_ops_receipt_email_user FOREIGN KEY(receipt_verified_email_id, receipt_user_id) REFERENCES cuevion_account.verified_emails (email_id, user_id) MATCH SIMPLE ON DELETE NO ACTION ON UPDATE NO ACTION,
	CONSTRAINT fk_initial_ops_receipt_identity_user FOREIGN KEY(receipt_authentication_identity_id, receipt_user_id) REFERENCES cuevion_account.authentication_identities (identity_id, user_id) MATCH SIMPLE ON DELETE NO ACTION ON UPDATE NO ACTION,
	CONSTRAINT fk_initial_ops_receipt_workspace_creator FOREIGN KEY(receipt_workspace_id, receipt_user_id) REFERENCES cuevion_account.workspaces (workspace_id, created_by_user_id) MATCH SIMPLE ON DELETE NO ACTION ON UPDATE NO ACTION,
	CONSTRAINT fk_initial_ops_receipt_membership FOREIGN KEY(receipt_workspace_id, receipt_user_id) REFERENCES cuevion_account.workspace_memberships (workspace_id, user_id) MATCH SIMPLE ON DELETE NO ACTION ON UPDATE NO ACTION,
	CONSTRAINT ck_initial_account_operations_timestamp_order CHECK (snapshot_user_created_at <= snapshot_user_updated_at AND snapshot_verified_email_created_at <= snapshot_verified_email_verified_at AND snapshot_verified_email_retired_at IS NULL AND (snapshot_authentication_identity_last_used_at IS NULL OR snapshot_authentication_identity_created_at <= snapshot_authentication_identity_last_used_at) AND snapshot_workspace_created_at <= snapshot_workspace_updated_at AND snapshot_workspace_membership_created_at <= snapshot_workspace_membership_updated_at AND snapshot_authentication_evidence_verified_at <= snapshot_authentication_evidence_issued_at AND snapshot_authentication_evidence_issued_at < snapshot_authentication_evidence_expires_at),
	CONSTRAINT ck_initial_account_operations_initial_state CHECK (snapshot_user_status = 'active' AND snapshot_user_security_epoch = 1 AND snapshot_user_row_version = 1 AND snapshot_verified_email_status = 'verified' AND snapshot_verified_email_verified_at IS NOT NULL AND snapshot_verified_email_retired_at IS NULL AND snapshot_verified_email_row_version = 1 AND snapshot_authentication_identity_status = 'active' AND snapshot_authentication_identity_row_version = 1 AND snapshot_workspace_status = 'active' AND snapshot_workspace_row_version = 1 AND snapshot_workspace_membership_role = 'owner' AND snapshot_workspace_membership_status = 'active' AND snapshot_workspace_membership_row_version = 1 AND snapshot_security_event_event_type = 'initial_account_created'),
	CONSTRAINT ck_initial_account_operations_snapshot_graph CHECK (snapshot_user_primary_verified_email_id = snapshot_verified_email_email_id AND snapshot_verified_email_user_id = snapshot_user_user_id AND snapshot_authentication_identity_user_id = snapshot_user_user_id AND snapshot_authentication_identity_verified_email_id = snapshot_verified_email_email_id AND snapshot_authentication_identity_issuer = snapshot_authentication_evidence_issuer AND snapshot_authentication_identity_subject = snapshot_authentication_evidence_subject AND snapshot_authentication_identity_authentication_method = snapshot_authentication_evidence_authentication_method AND snapshot_authentication_evidence_canonical_verified_email = snapshot_verified_email_canonical_email AND snapshot_authentication_evidence_verified_at = snapshot_verified_email_verified_at AND snapshot_workspace_created_by_user_id = snapshot_user_user_id AND snapshot_workspace_membership_workspace_id = snapshot_workspace_workspace_id AND snapshot_workspace_membership_user_id = snapshot_user_user_id),
	CONSTRAINT ck_initial_account_operations_receipt_binding CHECK (receipt_user_id = snapshot_user_user_id AND receipt_verified_email_id = snapshot_verified_email_email_id AND receipt_authentication_identity_id = snapshot_authentication_identity_identity_id AND receipt_workspace_id = snapshot_workspace_workspace_id AND receipt_security_event_id = snapshot_security_event_event_id)
)""",
    r"""CREATE TABLE cuevion_account.security_events (
	event_record_version SMALLINT NOT NULL,
	event_payload_version SMALLINT NOT NULL,
	event_id VARCHAR(26) COLLATE "C" NOT NULL,
	event_type TEXT COLLATE "C" NOT NULL,
	reference_schema_version SMALLINT NOT NULL,
	derivation_key_epoch BIGINT NOT NULL,
	operation_digest BYTEA NOT NULL,
	actor_trust_domain VARCHAR(128) COLLATE "C" NOT NULL,
	actor_verification_coordinator_id VARCHAR(128) COLLATE "C" NOT NULL,
	user_id VARCHAR(26) COLLATE "C" NOT NULL,
	verified_email_id VARCHAR(26) COLLATE "C" NOT NULL,
	authentication_identity_id VARCHAR(26) COLLATE "C" NOT NULL,
	workspace_id VARCHAR(26) COLLATE "C" NOT NULL,
	membership_workspace_id VARCHAR(26) COLLATE "C" NOT NULL,
	membership_user_id VARCHAR(26) COLLATE "C" NOT NULL,
	security_epoch BIGINT NOT NULL,
	event_at TIMESTAMP WITH TIME ZONE NOT NULL,
	recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
	event_stream_name VARCHAR(24) COLLATE "C" NOT NULL,
	event_stream_position BIGINT NOT NULL,
	row_version BIGINT NOT NULL,
	CONSTRAINT pk_security_events PRIMARY KEY (event_id),
	CONSTRAINT ck_security_events_event_record_version_schema_one CHECK (event_record_version = 1),
	CONSTRAINT ck_security_events_event_payload_version_schema_one CHECK (event_payload_version = 1),
	CONSTRAINT ck_security_events_event_id_canonical CHECK (event_id ~ '^sev_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_security_events_event_type_closed CHECK (event_type IN ('initial_account_created')),
	CONSTRAINT ck_security_events_reference_schema_version_schema_one CHECK (reference_schema_version = 1),
	CONSTRAINT ck_security_events_derivation_key_epoch_range CHECK (derivation_key_epoch BETWEEN 1 AND 4294967295),
	CONSTRAINT ck_security_events_operation_digest_32_bytes CHECK (octet_length(operation_digest) = 32),
	CONSTRAINT ck_security_events_actor_trust_domain_opaque CHECK (actor_trust_domain ~ '^[A-Za-z0-9._:-]{1,128}$'),
	CONSTRAINT ck_security_events_actor_verification_coordinator_id_opaque CHECK (actor_verification_coordinator_id ~ '^[A-Za-z0-9._:-]{1,128}$'),
	CONSTRAINT ck_security_events_user_id_canonical CHECK (user_id ~ '^usr_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_security_events_verified_email_id_canonical CHECK (verified_email_id ~ '^vem_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_security_events_authentication_identity_id_canonical CHECK (authentication_identity_id ~ '^aid_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_security_events_workspace_id_canonical CHECK (workspace_id ~ '^wsp_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_security_events_membership_workspace_id_canonical CHECK (membership_workspace_id ~ '^wsp_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_security_events_membership_user_id_canonical CHECK (membership_user_id ~ '^usr_[A-Za-z0-9_-]{21}[AQgw]$'),
	CONSTRAINT ck_security_events_security_epoch_positive CHECK (security_epoch > 0),
	CONSTRAINT ck_security_events_event_at_timestamp CHECK (isfinite(event_at) AND event_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND event_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND event_at = date_trunc('second', event_at)),
	CONSTRAINT ck_security_events_recorded_at_timestamp CHECK (isfinite(recorded_at) AND recorded_at >= TIMESTAMPTZ '1970-01-01 00:00:00+00' AND recorded_at <= TIMESTAMPTZ '9999-12-31 23:59:59+00' AND recorded_at = date_trunc('second', recorded_at)),
	CONSTRAINT ck_security_events_event_stream_position_positive CHECK (event_stream_position > 0),
	CONSTRAINT ck_security_events_row_version_exact_one CHECK (row_version = 1),
	CONSTRAINT uq_security_events_operation_ref UNIQUE (reference_schema_version, derivation_key_epoch, operation_digest),
	CONSTRAINT uq_security_events_stream_position UNIQUE (event_stream_name, event_stream_position),
	CONSTRAINT fk_security_events_operation_binding FOREIGN KEY(reference_schema_version, derivation_key_epoch, operation_digest, event_payload_version, event_id, event_type, actor_trust_domain, actor_verification_coordinator_id, user_id, verified_email_id, authentication_identity_id, workspace_id, membership_workspace_id, membership_user_id, security_epoch) REFERENCES cuevion_account.initial_account_operations (reference_schema_version, derivation_key_epoch, operation_digest, snapshot_security_event_schema_version, snapshot_security_event_event_id, snapshot_security_event_event_type, snapshot_authentication_evidence_trust_domain, snapshot_authentication_evidence_verification_coordinator_id, snapshot_user_user_id, snapshot_verified_email_email_id, snapshot_authentication_identity_identity_id, snapshot_workspace_workspace_id, snapshot_workspace_membership_workspace_id, snapshot_workspace_membership_user_id, snapshot_user_security_epoch) MATCH SIMPLE ON DELETE NO ACTION ON UPDATE NO ACTION,
	CONSTRAINT fk_security_events_user FOREIGN KEY(user_id) REFERENCES cuevion_account.users (user_id) MATCH SIMPLE ON DELETE NO ACTION ON UPDATE NO ACTION,
	CONSTRAINT fk_security_events_email_user FOREIGN KEY(verified_email_id, user_id) REFERENCES cuevion_account.verified_emails (email_id, user_id) MATCH SIMPLE ON DELETE NO ACTION ON UPDATE NO ACTION,
	CONSTRAINT fk_security_events_identity_user FOREIGN KEY(authentication_identity_id, user_id) REFERENCES cuevion_account.authentication_identities (identity_id, user_id) MATCH SIMPLE ON DELETE NO ACTION ON UPDATE NO ACTION,
	CONSTRAINT fk_security_events_workspace_creator FOREIGN KEY(workspace_id, user_id) REFERENCES cuevion_account.workspaces (workspace_id, created_by_user_id) MATCH SIMPLE ON DELETE NO ACTION ON UPDATE NO ACTION,
	CONSTRAINT fk_security_events_membership FOREIGN KEY(membership_workspace_id, membership_user_id) REFERENCES cuevion_account.workspace_memberships (workspace_id, user_id) MATCH SIMPLE ON DELETE NO ACTION ON UPDATE NO ACTION,
	CONSTRAINT ck_security_events_timestamp_order CHECK (event_at <= recorded_at),
	CONSTRAINT ck_security_events_membership_binding CHECK (membership_workspace_id = workspace_id AND membership_user_id = user_id),
	CONSTRAINT ck_security_events_stream_name CHECK (event_stream_name = 'cuevion.account.security')
)""",
)
_INDEX_DDL = (r"""CREATE UNIQUE INDEX ux_verified_emails_current_claim ON cuevion_account.verified_emails (canonical_email) WHERE status = 'verified' AND retired_at IS NULL""",)
_DEFERRED_FOREIGN_KEY_DDL = (
    r"""ALTER TABLE cuevion_account.users ADD CONSTRAINT fk_users_primary_email_same_user FOREIGN KEY(primary_verified_email_id, user_id) REFERENCES cuevion_account.verified_emails (email_id, user_id) MATCH SIMPLE ON DELETE NO ACTION ON UPDATE NO ACTION DEFERRABLE INITIALLY DEFERRED""",
    r"""ALTER TABLE cuevion_account.initial_account_operations ADD CONSTRAINT fk_initial_ops_receipt_event FOREIGN KEY(receipt_security_event_id) REFERENCES cuevion_account.security_events (event_id) MATCH SIMPLE ON DELETE NO ACTION ON UPDATE NO ACTION DEFERRABLE INITIALLY DEFERRED""",
)


_APPEND_ONLY_FUNCTION_SQL = """
CREATE FUNCTION cuevion_account.fn_reject_append_only_change()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $function$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '23514',
        MESSAGE = 'cuevion account append-only relation cannot be changed',
        CONSTRAINT = 'trg_account_append_only';
END
$function$
"""


_MUTATION_GUARD_FUNCTION_SQL = """
CREATE FUNCTION cuevion_account.fn_enforce_mutable_account_update()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $function$
BEGIN
    IF TG_TABLE_SCHEMA <> 'cuevion_account' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'cuevion account mutation target is invalid',
            CONSTRAINT = 'trg_account_mutation_target';
    END IF;

    IF TG_TABLE_NAME = 'users' THEN
        IF NEW.schema_version IS DISTINCT FROM OLD.schema_version
           OR NEW.user_id IS DISTINCT FROM OLD.user_id
           OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                MESSAGE = 'cuevion account immutable fields cannot change',
                CONSTRAINT = 'trg_account_immutable_fields';
        END IF;
        IF NEW.security_epoch < OLD.security_epoch THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                MESSAGE = 'cuevion account security epoch transition is invalid',
                CONSTRAINT = 'trg_users_security_epoch';
        END IF;
    ELSIF TG_TABLE_NAME = 'verified_emails' THEN
        IF NEW.schema_version IS DISTINCT FROM OLD.schema_version
           OR NEW.email_id IS DISTINCT FROM OLD.email_id
           OR NEW.user_id IS DISTINCT FROM OLD.user_id
           OR NEW.canonical_email IS DISTINCT FROM OLD.canonical_email
           OR NEW.verification_source IS DISTINCT FROM OLD.verification_source
           OR NEW.created_at IS DISTINCT FROM OLD.created_at
           OR NEW.verified_at IS DISTINCT FROM OLD.verified_at THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                MESSAGE = 'cuevion account immutable fields cannot change',
                CONSTRAINT = 'trg_account_immutable_fields';
        END IF;
    ELSIF TG_TABLE_NAME = 'authentication_identities' THEN
        IF NEW.schema_version IS DISTINCT FROM OLD.schema_version
           OR NEW.identity_id IS DISTINCT FROM OLD.identity_id
           OR NEW.user_id IS DISTINCT FROM OLD.user_id
           OR NEW.issuer IS DISTINCT FROM OLD.issuer
           OR NEW.subject IS DISTINCT FROM OLD.subject
           OR NEW.authentication_method IS DISTINCT FROM OLD.authentication_method
           OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                MESSAGE = 'cuevion account immutable fields cannot change',
                CONSTRAINT = 'trg_account_immutable_fields';
        END IF;
    ELSIF TG_TABLE_NAME = 'workspaces' THEN
        IF NEW.schema_version IS DISTINCT FROM OLD.schema_version
           OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
           OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
           OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                MESSAGE = 'cuevion account immutable fields cannot change',
                CONSTRAINT = 'trg_account_immutable_fields';
        END IF;
    ELSIF TG_TABLE_NAME = 'workspace_memberships' THEN
        IF NEW.schema_version IS DISTINCT FROM OLD.schema_version
           OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
           OR NEW.user_id IS DISTINCT FROM OLD.user_id
           OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                MESSAGE = 'cuevion account immutable fields cannot change',
                CONSTRAINT = 'trg_account_immutable_fields';
        END IF;
    ELSE
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'cuevion account mutation target is invalid',
            CONSTRAINT = 'trg_account_mutation_target';
    END IF;

    IF OLD.row_version = 9223372036854775807
       OR NEW.row_version <> OLD.row_version + 1 THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'cuevion account row version transition is invalid',
            CONSTRAINT = 'trg_account_row_version';
    END IF;
    RETURN NEW;
END
$function$
"""


_GRAPH_FUNCTION_SQL = """
CREATE FUNCTION cuevion_account.fn_validate_initial_account_graph()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $function$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM cuevion_account.users AS u
        JOIN cuevion_account.verified_emails AS ve
          ON ve.email_id = NEW.receipt_verified_email_id
         AND ve.user_id = u.user_id
        JOIN cuevion_account.authentication_identities AS ai
          ON ai.identity_id = NEW.receipt_authentication_identity_id
         AND ai.user_id = u.user_id
        JOIN cuevion_account.workspaces AS w
          ON w.workspace_id = NEW.receipt_workspace_id
         AND w.created_by_user_id = u.user_id
        JOIN cuevion_account.workspace_memberships AS wm
          ON wm.workspace_id = w.workspace_id
         AND wm.user_id = u.user_id
        JOIN cuevion_account.security_events AS se
          ON se.event_id = NEW.receipt_security_event_id
        WHERE u.user_id = NEW.receipt_user_id
          AND ROW(
              u.schema_version, u.user_id, u.status,
              u.primary_verified_email_id, u.display_name, u.security_epoch,
              u.created_at, u.updated_at, u.row_version
          ) IS NOT DISTINCT FROM ROW(
              NEW.snapshot_user_schema_version, NEW.snapshot_user_user_id,
              NEW.snapshot_user_status,
              NEW.snapshot_user_primary_verified_email_id,
              NEW.snapshot_user_display_name, NEW.snapshot_user_security_epoch,
              NEW.snapshot_user_created_at, NEW.snapshot_user_updated_at,
              NEW.snapshot_user_row_version
          )
          AND ROW(
              ve.schema_version, ve.email_id, ve.user_id, ve.canonical_email,
              ve.status, ve.verification_source, ve.created_at, ve.verified_at,
              ve.retired_at, ve.row_version
          ) IS NOT DISTINCT FROM ROW(
              NEW.snapshot_verified_email_schema_version,
              NEW.snapshot_verified_email_email_id,
              NEW.snapshot_verified_email_user_id,
              NEW.snapshot_verified_email_canonical_email,
              NEW.snapshot_verified_email_status,
              NEW.snapshot_verified_email_verification_source,
              NEW.snapshot_verified_email_created_at,
              NEW.snapshot_verified_email_verified_at,
              NEW.snapshot_verified_email_retired_at,
              NEW.snapshot_verified_email_row_version
          )
          AND ROW(
              ai.schema_version, ai.identity_id, ai.user_id, ai.issuer,
              ai.subject, ai.authentication_method, ai.status,
              ai.verified_email_id, ai.created_at, ai.last_used_at,
              ai.row_version
          ) IS NOT DISTINCT FROM ROW(
              NEW.snapshot_authentication_identity_schema_version,
              NEW.snapshot_authentication_identity_identity_id,
              NEW.snapshot_authentication_identity_user_id,
              NEW.snapshot_authentication_identity_issuer,
              NEW.snapshot_authentication_identity_subject,
              NEW.snapshot_authentication_identity_authentication_method,
              NEW.snapshot_authentication_identity_status,
              NEW.snapshot_authentication_identity_verified_email_id,
              NEW.snapshot_authentication_identity_created_at,
              NEW.snapshot_authentication_identity_last_used_at,
              NEW.snapshot_authentication_identity_row_version
          )
          AND ROW(
              w.schema_version, w.workspace_id, w.status,
              w.created_by_user_id, w.created_at, w.updated_at, w.row_version
          ) IS NOT DISTINCT FROM ROW(
              NEW.snapshot_workspace_schema_version,
              NEW.snapshot_workspace_workspace_id,
              NEW.snapshot_workspace_status,
              NEW.snapshot_workspace_created_by_user_id,
              NEW.snapshot_workspace_created_at,
              NEW.snapshot_workspace_updated_at,
              NEW.snapshot_workspace_row_version
          )
          AND ROW(
              wm.schema_version, wm.workspace_id, wm.user_id, wm.role,
              wm.status, wm.created_at, wm.updated_at, wm.row_version
          ) IS NOT DISTINCT FROM ROW(
              NEW.snapshot_workspace_membership_schema_version,
              NEW.snapshot_workspace_membership_workspace_id,
              NEW.snapshot_workspace_membership_user_id,
              NEW.snapshot_workspace_membership_role,
              NEW.snapshot_workspace_membership_status,
              NEW.snapshot_workspace_membership_created_at,
              NEW.snapshot_workspace_membership_updated_at,
              NEW.snapshot_workspace_membership_row_version
          )
          AND ROW(
              se.event_payload_version, se.event_id, se.event_type,
              se.reference_schema_version, se.derivation_key_epoch,
              se.operation_digest, se.actor_trust_domain,
              se.actor_verification_coordinator_id, se.user_id,
              se.verified_email_id, se.authentication_identity_id,
              se.workspace_id, se.membership_workspace_id,
              se.membership_user_id, se.security_epoch
          ) IS NOT DISTINCT FROM ROW(
              NEW.snapshot_security_event_schema_version,
              NEW.snapshot_security_event_event_id,
              NEW.snapshot_security_event_event_type,
              NEW.reference_schema_version, NEW.derivation_key_epoch,
              NEW.operation_digest,
              NEW.snapshot_authentication_evidence_trust_domain,
              NEW.snapshot_authentication_evidence_verification_coordinator_id,
              NEW.snapshot_user_user_id,
              NEW.snapshot_verified_email_email_id,
              NEW.snapshot_authentication_identity_identity_id,
              NEW.snapshot_workspace_workspace_id,
              NEW.snapshot_workspace_membership_workspace_id,
              NEW.snapshot_workspace_membership_user_id,
              NEW.snapshot_user_security_epoch
          )
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'cuevion initial account graph is inconsistent',
            CONSTRAINT = 'ct_initial_account_graph_consistent';
    END IF;
    RETURN NEW;
END
$function$
"""


_TRIGGER_SQL = (
    """
CREATE TRIGGER trg_initial_ops_append_only
BEFORE UPDATE OR DELETE ON cuevion_account.initial_account_operations
FOR EACH ROW EXECUTE FUNCTION cuevion_account.fn_reject_append_only_change()
""",
    """
CREATE TRIGGER trg_initial_ops_no_truncate
BEFORE TRUNCATE ON cuevion_account.initial_account_operations
FOR EACH STATEMENT EXECUTE FUNCTION cuevion_account.fn_reject_append_only_change()
""",
    """
CREATE TRIGGER trg_security_events_append_only
BEFORE UPDATE OR DELETE ON cuevion_account.security_events
FOR EACH ROW EXECUTE FUNCTION cuevion_account.fn_reject_append_only_change()
""",
    """
CREATE TRIGGER trg_security_events_no_truncate
BEFORE TRUNCATE ON cuevion_account.security_events
FOR EACH STATEMENT EXECUTE FUNCTION cuevion_account.fn_reject_append_only_change()
""",
    """
CREATE TRIGGER trg_users_mutation_guard
BEFORE UPDATE ON cuevion_account.users
FOR EACH ROW EXECUTE FUNCTION cuevion_account.fn_enforce_mutable_account_update()
""",
    """
CREATE TRIGGER trg_verified_emails_mutation_guard
BEFORE UPDATE ON cuevion_account.verified_emails
FOR EACH ROW EXECUTE FUNCTION cuevion_account.fn_enforce_mutable_account_update()
""",
    """
CREATE TRIGGER trg_auth_identities_mutation_guard
BEFORE UPDATE ON cuevion_account.authentication_identities
FOR EACH ROW EXECUTE FUNCTION cuevion_account.fn_enforce_mutable_account_update()
""",
    """
CREATE TRIGGER trg_workspaces_mutation_guard
BEFORE UPDATE ON cuevion_account.workspaces
FOR EACH ROW EXECUTE FUNCTION cuevion_account.fn_enforce_mutable_account_update()
""",
    """
CREATE TRIGGER trg_workspace_memberships_mutation_guard
BEFORE UPDATE ON cuevion_account.workspace_memberships
FOR EACH ROW EXECUTE FUNCTION cuevion_account.fn_enforce_mutable_account_update()
""",
    """
CREATE CONSTRAINT TRIGGER ct_initial_account_graph_consistent
AFTER INSERT ON cuevion_account.initial_account_operations
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION cuevion_account.fn_validate_initial_account_graph()
""",
)


def upgrade() -> None:
    op.execute(_SCHEMA_DDL)
    op.execute(_SEQUENCE_DDL)
    for statement in _TABLE_DDL:
        op.execute(statement)
    for statement in _INDEX_DDL:
        op.execute(statement)
    for statement in _DEFERRED_FOREIGN_KEY_DDL:
        op.execute(statement)
    op.execute(_APPEND_ONLY_FUNCTION_SQL)
    op.execute(_MUTATION_GUARD_FUNCTION_SQL)
    op.execute(_GRAPH_FUNCTION_SQL)
    for statement in _TRIGGER_SQL:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("cuevion account authority migrations are forward-only")
