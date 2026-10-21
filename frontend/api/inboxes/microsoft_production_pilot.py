"""Microsoft Production policy: only owner-scoped, verified Inbox read flags.

Production cannot send, reply, create drafts, delete or move to arbitrary
folders. Only exact-message Inbox read/unread and Inbox -> Archive moves are
authorized. These narrow source-only permissions cannot be enabled through
an environment variable or browser input. Any broader mutation needs review.

This policy affects only Microsoft message mutations, not OAuth token/config
persistence required to connect and read a mailbox; it never affects Gmail,
IMAP, Auth0, or the existing Cuevion production host restrictions.
"""
from __future__ import annotations

import os

# Narrow source-only exemptions for the exact owned Inbox message flag route.
# Neither toggle authorizes any other Microsoft Graph mailbox mutation.
_PRODUCTION_READ_FLAGS_RELEASED = True
_PRODUCTION_MANUAL_INBOX_MARK_UNREAD_RELEASED = True
# Exactly one Inbox -> Archive action, never a generic move or other write.
_PRODUCTION_INBOX_ARCHIVE_RELEASED = True


def production_pilot_read_flags_blocked() -> bool:
    """A separate fail-closed gate for Microsoft Inbox read-state changes."""
    return (
        os.getenv("VERCEL_ENV", "").strip().lower() == "production"
        and not _PRODUCTION_READ_FLAGS_RELEASED
    )


def production_pilot_mark_unread_blocked() -> bool:
    """Fail closed on unread=True unless the exact Inbox flag source is released."""
    return (
        os.getenv("VERCEL_ENV", "").strip().lower() == "production"
        and not _PRODUCTION_MANUAL_INBOX_MARK_UNREAD_RELEASED
    )


def production_pilot_archive_blocked() -> bool:
    """Deny all Production Archive moves unless this source-only exception is enabled."""
    return (
        os.getenv("VERCEL_ENV", "").strip().lower() == "production"
        and not _PRODUCTION_INBOX_ARCHIVE_RELEASED
    )


def production_pilot_writes_blocked() -> bool:
    """Fail closed for Microsoft mail mutations in Vercel Production."""
    return os.getenv("VERCEL_ENV", "").strip().lower() == "production"
