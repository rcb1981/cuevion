"""Role-neutral least-privilege policy for mailbox runtime database roles."""

from __future__ import annotations

import re
from dataclasses import dataclass


_ROLE = re.compile(r"^[a-z][a-z0-9_]{2,62}$")
_TABLES = (
    "mailbox_sync_state",
    "mailbox_sync_cursor",
    "mailbox_messages",
    "mailbox_message_bodies",
    "mailbox_change_outbox",
)


@dataclass(frozen=True, slots=True)
class MailboxRolePlan:
    reader_role: str
    writer_role: str
    statements: tuple[str, ...]


def _role(value: str) -> str:
    if type(value) is not str or _ROLE.fullmatch(value) is None:
        raise ValueError("invalid mailbox database role")
    return value


def build_mailbox_role_plan(reader_role: str, writer_role: str) -> MailboxRolePlan:
    reader = _role(reader_role)
    writer = _role(writer_role)
    if reader == writer:
        raise ValueError("mailbox database roles must be distinct")

    tables = ", ".join(f"cuevion_mailbox.{name}" for name in _TABLES)
    statements = (
        f"REVOKE ALL ON SCHEMA cuevion_mailbox FROM {reader}",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA cuevion_mailbox FROM {reader}",
        f"GRANT USAGE ON SCHEMA cuevion_mailbox TO {reader}",
        f"GRANT SELECT ON {tables} TO {reader}",
        f"REVOKE ALL ON SCHEMA cuevion_mailbox FROM {writer}",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA cuevion_mailbox FROM {writer}",
        f"GRANT USAGE ON SCHEMA cuevion_mailbox TO {writer}",
        f"GRANT SELECT, INSERT, UPDATE ON {tables} TO {writer}",
    )
    return MailboxRolePlan(reader, writer, statements)


__all__ = ("MailboxRolePlan", "build_mailbox_role_plan")
