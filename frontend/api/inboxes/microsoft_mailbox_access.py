"""Server-only Microsoft mailbox authority and owner-scoped Graph reads.

A browser-supplied mailbox ID is a selector, never authority. The exact
currently authenticated owner and stored mailbox record must match before
loading OAuth credentials or making any Microsoft Graph request.

No network endpoints / HTTP handlers are exported by this module.
"""
from __future__ import annotations

import re

from api.auth.email_address import is_valid_auth_email, normalize_auth_email
from api.auth.runtime import AuthenticatedMemberContext
from api.inboxes.microsoft_token_authority import (
    MicrosoftMailboxAuthority,
    MicrosoftTokenError,
    ensure_owned_microsoft_access_token,
)
from api.user_config_store import resolve_owned_managed_inbox_record

MAILBOX_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,159}$")
GENERATION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


class MicrosoftMailboxAccessError(Exception):
    """A fixed code only. Never include identity or credential data."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def resolve_owned_microsoft_mailbox(headers, mailbox_id: object) -> MicrosoftMailboxAuthority:
    """Resolve an active, uniquely owned Microsoft mailbox from Auth0 authority.

    A second resolution of the actual server-owned managed mailbox is required
    even if the OAuth callback previously validated and connected the account.
    """
    if (
        type(mailbox_id) is not str
        or MAILBOX_ID_PATTERN.fullmatch(mailbox_id) is None
    ):
        raise MicrosoftMailboxAccessError("microsoft_mailbox_id_invalid")

    try:
        resolved = resolve_owned_managed_inbox_record(
            headers, mailbox_id, include_member_authority=True
        )
    except Exception:
        raise MicrosoftMailboxAccessError("microsoft_mailbox_authority_unavailable") from None

    if not isinstance(resolved, dict):
        raise MicrosoftMailboxAccessError("microsoft_mailbox_authority_unavailable")

    status = resolved.get("status")
    if status != "ok":
        if status == "unauthorized":
            raise MicrosoftMailboxAccessError("microsoft_unauthorized")
        if status == "not_found":
            raise MicrosoftMailboxAccessError("microsoft_mailbox_not_found")
        raise MicrosoftMailboxAccessError("microsoft_mailbox_authority_unavailable")

    user = resolved.get("user")
    member = resolved.get("memberAuthority")
    config = resolved.get("config")
    mailbox = resolved.get("inbox")
    if (
        type(member) is not AuthenticatedMemberContext
        or member.membership_role != "owner"
        or member.auth_source != "auth0"
        or member.user_type != "member"
        or type(user) is not dict
        or type(config) is not dict
        or type(mailbox) is not dict
    ):
        raise MicrosoftMailboxAccessError("microsoft_mailbox_authority_unavailable")

    owner = normalize_auth_email(member.email)
    if not is_valid_auth_email(owner):
        raise MicrosoftMailboxAccessError("microsoft_mailbox_authority_unavailable")
    if (
        user.get("email") != owner
        or config.get("email") != owner
        or mailbox.get("id") != mailbox_id
    ):
        raise MicrosoftMailboxAccessError("microsoft_mailbox_authority_unavailable")

    managed = config.get("managedInboxes")
    if type(managed) is not list or any(type(entry) is not dict for entry in managed):
        raise MicrosoftMailboxAccessError("microsoft_mailbox_authority_unavailable")
    exact = [v for v in managed if v.get("id") == mailbox_id]
    folded = [
        v for v in managed
        if type(v.get("id")) is str and v["id"].casefold() == mailbox_id.casefold()
    ]
    if len(exact) != 1 or len(folded) != 1 or exact[0] != mailbox:
        raise MicrosoftMailboxAccessError("microsoft_mailbox_authority_unavailable")

    mailbox_email = mailbox.get("email")
    if type(mailbox_email) is not str:
        raise MicrosoftMailboxAccessError("microsoft_mailbox_authority_unavailable")
    email = normalize_auth_email(mailbox_email)
    duplicate_emails = [
        v for v in managed
        if type(v.get("email")) is str and normalize_auth_email(v["email"]) == email
    ]
    if (
        not is_valid_auth_email(email)
        or email != mailbox_email
        or len(duplicate_emails) != 1
        or mailbox.get("provider") != "microsoft"
        or mailbox.get("connected") is not True
        or mailbox.get("connectionStatus") != "connected"
        or mailbox.get("connectionMethod") != "oauth"
        or mailbox.get("oauthOwnerEmail") != owner
        or mailbox.get("oauthOwnerUserId") != member.user_id
        or mailbox.get("oauthWorkspaceId") != member.workspace_id
        or mailbox.get("oauthReconnectGeneration") is not None
    ):
        raise MicrosoftMailboxAccessError("microsoft_reconnect_required")

    account_id = mailbox.get("oauthAccountId")
    generation = mailbox.get("oauthCredentialGeneration")
    if (
        type(account_id) is not str
        or not 1 <= len(account_id) <= 256
        or account_id != account_id.strip()
        or any(not 33 <= ord(c) <= 126 for c in account_id)
        or type(generation) is not str
        or GENERATION_PATTERN.fullmatch(generation) is None
    ):
        raise MicrosoftMailboxAccessError("microsoft_reconnect_required")

    return MicrosoftMailboxAuthority(
        owner_email=owner,
        mailbox_id=mailbox_id,
        email=email,
        account_id=account_id,
        credential_generation=generation,
    )


def _owned_token(headers, mailbox_id: object) -> tuple[str, MicrosoftMailboxAuthority]:
    authority = resolve_owned_microsoft_mailbox(headers, mailbox_id)
    try:
        record = ensure_owned_microsoft_access_token(authority)
    except MicrosoftTokenError as error:
        # An operational caller may map only these safe, finite codes.
        raise MicrosoftMailboxAccessError(error.code) from None
    if not isinstance(record, dict) or record.get("provider") != "microsoft":
        raise MicrosoftMailboxAccessError("microsoft_reconnect_required")
    token = record.get("access_token")
    if not isinstance(token, str) or not token:
        raise MicrosoftMailboxAccessError("microsoft_reconnect_required")
    return token, authority


def _verify_unchanged_owned_mailbox(
    headers, mailbox_id: object, captured: MicrosoftMailboxAuthority,
) -> None:
    """Never publish Graph data after concurrent reconnect or owner changes."""
    verified = resolve_owned_microsoft_mailbox(headers, mailbox_id)
    if verified != captured:
        raise MicrosoftMailboxAccessError("microsoft_reconnect_required")


def list_owned_microsoft_inbox(headers, mailbox_id: object, *, page_size: int = 20) -> dict:
    """Owner-scoped Graph read; exact authority must survive network I/O."""
    if type(page_size) is not int or not 1 <= page_size <= 25:
        raise MicrosoftMailboxAccessError("microsoft_page_size_invalid")
    from api.inboxes import microsoft_graph
    token, captured = _owned_token(headers, mailbox_id)
    snapshot = microsoft_graph.list_microsoft_inbox(token, page_size=page_size)
    _verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return snapshot


def read_owned_microsoft_message(headers, mailbox_id: object, message_id: str) -> dict:
    """Authorized exact-ID read with post-network authority readback."""
    from api.inboxes import microsoft_graph
    token, captured = _owned_token(headers, mailbox_id)
    message = microsoft_graph.get_microsoft_message(token, message_id)
    _verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return message
