"""Least-privilege PostgreSQL role manifest for Cuevion mailbox runtime.

Important: create these roles with the SQL returned here. Neon Console/API role
creation is intentionally not supported because those roles inherit Neon's
administrative role by default. Password provisioning is a separate secret
operation after the SQL-created LOGIN role exists.
"""

from __future__ import annotations

import re


MAILBOX_READER_TABLES = (
    "mailbox_sync_state",
    "mailbox_sync_cursor",
    "mailbox_messages",
    "mailbox_message_bodies",
)
MAILBOX_WRITER_TABLES = (
    *MAILBOX_READER_TABLES,
    "mailbox_change_outbox",
)

_ROLE = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")


def _role(value: object) -> str:
    if type(value) is not str or _ROLE.fullmatch(value) is None:
        raise ValueError("invalid mailbox database role")
    return value


def role_creation_statements(
    reader_role: str,
    writer_role: str,
) -> tuple[str, ...]:
    reader = _role(reader_role)
    writer = _role(writer_role)
    if reader == writer:
        raise ValueError("mailbox database roles must be distinct")
    attributes = (
        "LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
        "NOREPLICATION NOBYPASSRLS"
    )
    return (
        f"CREATE ROLE {reader} {attributes}",
        f"CREATE ROLE {writer} {attributes}",
    )


def role_grant_statements(
    reader_role: str,
    writer_role: str,
) -> tuple[str, ...]:
    reader = _role(reader_role)
    writer = _role(writer_role)
    if reader == writer:
        raise ValueError("mailbox database roles must be distinct")

    reader_tables = ", ".join(
        f"cuevion_mailbox.{name}" for name in MAILBOX_READER_TABLES
    )
    writer_tables = ", ".join(
        f"cuevion_mailbox.{name}" for name in MAILBOX_WRITER_TABLES
    )
    return (
        f"REVOKE ALL ON SCHEMA cuevion_mailbox FROM {reader}",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA cuevion_mailbox FROM {reader}",
        f"GRANT USAGE ON SCHEMA cuevion_mailbox TO {reader}",
        f"GRANT SELECT ON {reader_tables} TO {reader}",
        f"REVOKE ALL ON SCHEMA cuevion_mailbox FROM {writer}",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA cuevion_mailbox FROM {writer}",
        f"GRANT USAGE ON SCHEMA cuevion_mailbox TO {writer}",
        f"GRANT SELECT, INSERT, UPDATE ON {writer_tables} TO {writer}",
    )


def role_provisioning_statements(
    reader_role: str,
    writer_role: str,
) -> tuple[str, ...]:
    return (
        *role_creation_statements(reader_role, writer_role),
        *role_grant_statements(reader_role, writer_role),
    )


__all__ = (
    "MAILBOX_READER_TABLES",
    "MAILBOX_WRITER_TABLES",
    "role_creation_statements",
    "role_grant_statements",
    "role_provisioning_statements",
)
