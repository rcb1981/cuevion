"""Microsoft Graph production pilot policy: mail content is read-only.

Production cannot send, reply, create drafts, or change read flags in this
release. The guard is deliberately NOT overridable through an environment
variable or a browser-provided field. Enabling writes later requires a
separately reviewed code change and a new deployment.

This policy affects only Microsoft message mutations, not OAuth token/config
persistence required to connect and read a mailbox; it never affects Gmail,
IMAP, Auth0, or the existing Cuevion production host restrictions.
"""
from __future__ import annotations

import os

# Release switch is intentionally SOURCE-CODE-ONLY and starts disabled.
# Separate approval, code review and Production deployment are needed to
# activate Microsoft Inbox mark-read. This never authorizes sending.
_PRODUCTION_READ_FLAGS_RELEASED = False


def production_pilot_read_flags_blocked() -> bool:
    """A separate fail-closed gate for Microsoft Inbox read-state changes."""
    return (
        os.getenv("VERCEL_ENV", "").strip().lower() == "production"
        and not _PRODUCTION_READ_FLAGS_RELEASED
    )


def production_pilot_mark_unread_blocked() -> bool:
    """Even a later mark-read release never permits setting unread=True."""
    return os.getenv("VERCEL_ENV", "").strip().lower() == "production"


def production_pilot_writes_blocked() -> bool:
    """Fail closed for Microsoft mail mutations in Vercel Production."""
    return os.getenv("VERCEL_ENV", "").strip().lower() == "production"
