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


def production_pilot_writes_blocked() -> bool:
    """Fail closed for Microsoft mail mutations in Vercel Production."""
    return os.getenv("VERCEL_ENV", "").strip().lower() == "production"
