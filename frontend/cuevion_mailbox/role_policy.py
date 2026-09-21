"""Role-neutral least-privilege policy for mailbox runtime database roles.

Creation statements are deliberately password-free. Credentials must be added
through a secret-aware provisioning path that does not grant parent-role
membership or broaden role attributes. A mailbox runtime login is invalid if it
inherits or can SET ROLE into a more privileged database role.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_ROLE = re.compile(r"^[a-z][a-z0-9_]{2,62}$")
_READER_TABLES = (
    "mailbox_sync_state",
    "mailbox_sync_cursor",
    "mailbox_messages",
    "mailbox_message_bodies",
)
_WRITER_TABLES = _READER_TABLES + ("mailbox_change_outbox",)


@dataclass(frozen=True, slots=True)
class MailboxRolePlan:
    reader_role: str
    writer_role: str
    creation_statements: tuple[str, ...]
    grant_statements: tuple[str, ...]


def _role(value: str) -> str:
    if type(value) is not str or _ROLE.fullmatch(value) is None:
        raise ValueError("invalid mailbox database role")
    return value


def build_mailbox_role_plan(reader_role: str, writer_role: str) -> MailboxRolePlan:
    reader = _role(reader_role)
    writer = _role(writer_role)
    if reader == writer:
        raise ValueError("mailbox database roles must be distinct")

    reader_tables = ", ".join(
        f"cuevion_mailbox.{name}" for name in _READER_TABLES
    )
    writer_tables = ", ".join(
        f"cuevion_mailbox.{name}" for name in _WRITER_TABLES
    )
    creation_statements = (
        f"CREATE ROLE {reader} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS",
        f"CREATE ROLE {writer} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS",
    )
    grant_statements = (
        f"REVOKE ALL ON SCHEMA cuevion_mailbox FROM {reader}",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA cuevion_mailbox FROM {reader}",
        f"GRANT USAGE ON SCHEMA cuevion_mailbox TO {reader}",
        f"GRANT SELECT ON {reader_tables} TO {reader}",
        f"REVOKE ALL ON SCHEMA cuevion_mailbox FROM {writer}",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA cuevion_mailbox FROM {writer}",
        f"GRANT USAGE ON SCHEMA cuevion_mailbox TO {writer}",
        f"GRANT SELECT, INSERT, UPDATE ON {writer_tables} TO {writer}",
    )
    return MailboxRolePlan(
        reader,
        writer,
        creation_statements,
        grant_statements,
    )


__all__ = ("MailboxRolePlan", "build_mailbox_role_plan")
