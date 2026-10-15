"""Microsoft Production policy: only confirmed Inbox mark-as-read is allowed.

Production cannot send, reply, create drafts, move, delete, or mark unread.
The authorized exception is one owner-scoped Inbox unread-to-read change.
This source-only permission is NOT overridable through an environment
variable or browser input. Any broader mutation needs another review.

This policy affects only Microsoft message mutations, not OAuth token/config
persistence required to connect and read a mailbox; it never affects Gmail,
IMAP, Auth0, or the existing Cuevion production host restrictions.
"""
from __future__ import annotations

import os

# Source-only exception approved for Inbox mark-as-read.
# This never authorizes sending or any other Microsoft mutation.
_PRODUCTION_READ_FLAGS_RELEASED = True


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
