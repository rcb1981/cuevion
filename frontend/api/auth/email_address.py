"""Neutral email helpers shared by authenticated member routes."""

from __future__ import annotations

import re


def is_valid_auth_email(value: str) -> bool:
    """Preserve the established syntactic email-validation contract."""

    return (
        re.match(
            r"^[^\s@]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$",
            value.strip(),
        )
        is not None
    )


def normalize_auth_email(value: str) -> str:
    """Return the established storage-key normalization for an email address."""

    return value.strip().lower()


__all__ = ("is_valid_auth_email", "normalize_auth_email")
