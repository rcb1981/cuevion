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
from api.user_config_store import (
    acquire_mailbox_mutation_lease,
    release_mailbox_mutation_lease,
    resolve_owned_managed_inbox_record,
)

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


def _owned_token(
    headers, mailbox_id: object, *, require_read_write: bool = False,
    require_mail_send: bool = False,
) -> tuple[str, MicrosoftMailboxAuthority]:
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
    if require_read_write:
        scope = record.get("scope")
        if (
            type(scope) is not str
            or not (
                {"Mail.ReadWrite", "https://graph.microsoft.com/Mail.ReadWrite"}
                & set(scope.split())
            )
        ):
            raise MicrosoftMailboxAccessError("microsoft_write_permission_required")
    if require_mail_send:
        scope = record.get("scope")
        if (
            type(scope) is not str
            or not ({"Mail.Send", "https://graph.microsoft.com/Mail.Send"} & set(scope.split()))
        ):
            raise MicrosoftMailboxAccessError("microsoft_send_permission_required")
    return token, authority


def _verify_unchanged_owned_mailbox(
    headers, mailbox_id: object, captured: MicrosoftMailboxAuthority,
) -> None:
    """Never publish Graph data after concurrent reconnect or owner changes."""
    verified = resolve_owned_microsoft_mailbox(headers, mailbox_id)
    if verified != captured:
        raise MicrosoftMailboxAccessError("microsoft_reconnect_required")


def list_owned_microsoft_inbox(
    headers, mailbox_id: object, *, page_size: int = 20, page: int = 1,
) -> dict:
    """Owner-scoped bounded Graph read; exact authority survives all page reads."""
    from api.inboxes import microsoft_graph
    if type(page_size) is not int or not 1 <= page_size <= 25:
        raise MicrosoftMailboxAccessError("microsoft_page_size_invalid")
    if type(page) is not int or not 1 <= page <= microsoft_graph.MAX_UI_PAGES:
        raise MicrosoftMailboxAccessError("microsoft_page_invalid")
    token, captured = _owned_token(headers, mailbox_id)
    snapshot = microsoft_graph.list_microsoft_inbox(
        token, page_size=page_size, **({"page": page} if page != 1 else {}),
    )
    _verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return snapshot


def read_owned_microsoft_message(headers, mailbox_id: object, message_id: str) -> dict:
    """Authorized exact-ID read with post-network authority readback."""
    from api.inboxes import microsoft_graph
    token, captured = _owned_token(headers, mailbox_id)
    message = microsoft_graph.get_microsoft_message(token, message_id)
    _verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return message


def mark_owned_microsoft_inbox_read(
    headers,
    mailbox_id: object,
    message_id: str,
    *,
    unread: bool,
) -> dict:
    """Serialize one exact Microsoft Inbox flag mutation under owner authority.

    The shared mailbox mutation lease prevents independent in-flight operations
    from silently racing each other. We do not retry a PATCH if its result is
    unknown. The Graph writer reads the Inbox-scoped message before and after.
    """
    from api.inboxes import microsoft_graph, microsoft_message_flags

    if type(unread) is not bool:
        raise MicrosoftMailboxAccessError("microsoft_mutation_invalid")
    microsoft_graph._required_message_id(message_id)
    captured = resolve_owned_microsoft_mailbox(headers, mailbox_id)
    try:
        lease = acquire_mailbox_mutation_lease(
            captured.owner_email, captured.mailbox_id,
        )
    except Exception:
        raise MicrosoftMailboxAccessError("microsoft_mailbox_unavailable") from None
    if not isinstance(lease, dict):
        raise MicrosoftMailboxAccessError("microsoft_mailbox_unavailable")
    if lease.get("status") != "acquired":
        if lease.get("status") == "held":
            raise MicrosoftMailboxAccessError("microsoft_mutation_in_progress")
        raise MicrosoftMailboxAccessError("microsoft_mailbox_unavailable")
    lease_token = lease.get("token")
    if type(lease_token) is not str or not lease_token:
        raise MicrosoftMailboxAccessError("microsoft_mailbox_unavailable")

    succeeded = False
    try:
        token, checked = _owned_token(
            headers, mailbox_id, require_read_write=True,
        )
        if checked != captured:
            raise MicrosoftMailboxAccessError("microsoft_reconnect_required")
        result = microsoft_message_flags.set_exact_microsoft_inbox_read(
            token, message_id, unread=unread,
            before_write=lambda: _verify_unchanged_owned_mailbox(
                headers, mailbox_id, captured,
            ),
        )
        _verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
        succeeded = True
        return result
    finally:
        # A failed release never authorizes another mutation or triggers a
        # second PATCH. If the write had been confirmed, fail visibly so
        # the client refreshes rather than claiming an unverified operation.
        released = False
        try:
            release = release_mailbox_mutation_lease(
                captured.owner_email, captured.mailbox_id, lease_token,
            )
            released = isinstance(release, dict) and release.get("status") == "released"
        except Exception:
            pass
        if succeeded and not released:
            raise MicrosoftMailboxAccessError("microsoft_mutation_unconfirmed")



def archive_owned_microsoft_inbox_message(headers, mailbox_id: object, message_id: str) -> dict:
    """One owner-bound Inbox -> Archive move under the shared mutation lease."""
    from api.inboxes import microsoft_graph, microsoft_archive_move

    microsoft_graph._required_message_id(message_id)
    captured = resolve_owned_microsoft_mailbox(headers, mailbox_id)
    try:
        lease = acquire_mailbox_mutation_lease(
            captured.owner_email, captured.mailbox_id,
        )
    except Exception:
        raise MicrosoftMailboxAccessError("microsoft_mailbox_unavailable") from None
    if not isinstance(lease, dict):
        raise MicrosoftMailboxAccessError("microsoft_mailbox_unavailable")
    if lease.get("status") != "acquired":
        if lease.get("status") == "held":
            raise MicrosoftMailboxAccessError("microsoft_mutation_in_progress")
        raise MicrosoftMailboxAccessError("microsoft_mailbox_unavailable")
    lease_token = lease.get("token")
    if type(lease_token) is not str or not lease_token:
        raise MicrosoftMailboxAccessError("microsoft_mailbox_unavailable")
    succeeded = False
    try:
        token, checked = _owned_token(headers, mailbox_id, require_read_write=True)
        if checked != captured:
            raise MicrosoftMailboxAccessError("microsoft_reconnect_required")
        result = microsoft_archive_move.move_exact_microsoft_inbox_to_archive(
            token, message_id,
            before_write=lambda: _verify_unchanged_owned_mailbox(
                headers, mailbox_id, captured,
            ),
        )
        _verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
        succeeded = True
        return result
    finally:
        released = False
        try:
            release = release_mailbox_mutation_lease(
                captured.owner_email, captured.mailbox_id, lease_token,
            )
            released = isinstance(release, dict) and release.get("status") == "released"
        except Exception:
            pass
        if succeeded and not released:
            raise MicrosoftMailboxAccessError("microsoft_mutation_unconfirmed")


def delete_owned_microsoft_inbox_message(headers, mailbox_id: object, message_id: str) -> dict:
    """One owner-bound Inbox -> Deleted Items move under the shared mutation lease."""
    from api.inboxes import microsoft_graph, microsoft_deleted_move

    microsoft_graph._required_message_id(message_id)
    captured = resolve_owned_microsoft_mailbox(headers, mailbox_id)
    try:
        lease = acquire_mailbox_mutation_lease(
            captured.owner_email, captured.mailbox_id,
        )
    except Exception:
        raise MicrosoftMailboxAccessError("microsoft_mailbox_unavailable") from None
    if not isinstance(lease, dict):
        raise MicrosoftMailboxAccessError("microsoft_mailbox_unavailable")
    if lease.get("status") != "acquired":
        if lease.get("status") == "held":
            raise MicrosoftMailboxAccessError("microsoft_mutation_in_progress")
        raise MicrosoftMailboxAccessError("microsoft_mailbox_unavailable")
    lease_token = lease.get("token")
    if type(lease_token) is not str or not lease_token:
        raise MicrosoftMailboxAccessError("microsoft_mailbox_unavailable")
    succeeded = False
    try:
        token, checked = _owned_token(headers, mailbox_id, require_read_write=True)
        if checked != captured:
            raise MicrosoftMailboxAccessError("microsoft_reconnect_required")
        result = microsoft_deleted_move.move_exact_microsoft_inbox_to_deleted(
            token, message_id,
            before_write=lambda: _verify_unchanged_owned_mailbox(
                headers, mailbox_id, captured,
            ),
        )
        _verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
        succeeded = True
        return result
    finally:
        released = False
        try:
            release = release_mailbox_mutation_lease(
                captured.owner_email, captured.mailbox_id, lease_token,
            )
            released = isinstance(release, dict) and release.get("status") == "released"
        except Exception:
            pass
        if succeeded and not released:
            raise MicrosoftMailboxAccessError("microsoft_mutation_unconfirmed")


def discard_owned_microsoft_draft(headers, mailbox_id: object, message_id: str) -> dict:
    """Owner-bound, leased exact Drafts -> Deleted Items soft move."""
    from api.inboxes import microsoft_graph, microsoft_draft_discard

    microsoft_graph._required_message_id(message_id)
    captured = resolve_owned_microsoft_mailbox(headers, mailbox_id)
    try:
        lease = acquire_mailbox_mutation_lease(
            captured.owner_email, captured.mailbox_id,
        )
    except Exception:
        raise MicrosoftMailboxAccessError("microsoft_mailbox_unavailable") from None
    if not isinstance(lease, dict):
        raise MicrosoftMailboxAccessError("microsoft_mailbox_unavailable")
    if lease.get("status") != "acquired":
        if lease.get("status") == "held":
            raise MicrosoftMailboxAccessError("microsoft_mutation_in_progress")
        raise MicrosoftMailboxAccessError("microsoft_mailbox_unavailable")
    lease_token = lease.get("token")
    if type(lease_token) is not str or not lease_token:
        raise MicrosoftMailboxAccessError("microsoft_mailbox_unavailable")
    succeeded = False
    try:
        token, checked = _owned_token(headers, mailbox_id, require_read_write=True)
        if checked != captured:
            raise MicrosoftMailboxAccessError("microsoft_reconnect_required")
        result = microsoft_draft_discard.discard_exact_microsoft_draft_to_deleted(
            token, message_id,
            before_write=lambda: _verify_unchanged_owned_mailbox(
                headers, mailbox_id, captured,
            ),
        )
        try:
            _verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
        except Exception:
            # The move has already happened; a revoked session is not proof
            # that it was rejected. Do not invite another move.
            raise MicrosoftMailboxAccessError("microsoft_mutation_unconfirmed") from None
        succeeded = True
        return result
    finally:
        released = False
        try:
            release = release_mailbox_mutation_lease(
                captured.owner_email, captured.mailbox_id, lease_token,
            )
            released = isinstance(release, dict) and release.get("status") == "released"
        except Exception:
            pass
        if succeeded and not released:
            raise MicrosoftMailboxAccessError("microsoft_mutation_unconfirmed")


def list_owned_microsoft_sent(
    headers, mailbox_id: object, *, page_size: int = 20, page: int = 1,
) -> dict:
    """Read one bounded Sent Items page with post-Graph authority proof."""
    from api.inboxes import microsoft_sent_graph, microsoft_graph
    if type(page_size) is not int or not 1 <= page_size <= 25:
        raise MicrosoftMailboxAccessError("microsoft_page_size_invalid")
    if type(page) is not int or not 1 <= page <= microsoft_graph.MAX_UI_PAGES:
        raise MicrosoftMailboxAccessError("microsoft_page_invalid")
    token, captured = _owned_token(headers, mailbox_id)
    snapshot = microsoft_sent_graph.list_microsoft_sent(
        token, page_size=page_size, **({"page": page} if page != 1 else {}),
    )
    _verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return snapshot


def read_owned_microsoft_sent_message(
    headers, mailbox_id: object, message_id: str,
) -> dict:
    """Read one immutable message ID only from the owned Sent Items folder."""
    from api.inboxes import microsoft_sent_graph
    token, captured = _owned_token(headers, mailbox_id)
    message = microsoft_sent_graph.get_microsoft_sent_message(token, message_id)
    _verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return message


def list_owned_microsoft_attachments(
    headers, mailbox_id: object, message_id: str, *, folder: str,
) -> dict:
    """Read attached file names only after owner, folder, and token checks."""
    if folder not in {"Inbox", "Sent", "Archive", "Drafts", "Deleted", "Junk"}:
        raise MicrosoftMailboxAccessError("microsoft_mailbox_unavailable")
    from api.inboxes import microsoft_attachments_graph
    token, authority = _owned_token(headers, mailbox_id)
    result = microsoft_attachments_graph.list_microsoft_attachments(
        token, message_id, folder=folder,
    )
    _verify_unchanged_owned_mailbox(headers, mailbox_id, authority)
    return result


def download_owned_microsoft_attachment(
    headers, mailbox_id: object, message_id: str, attachment_id: str,
    *, folder: str,
) -> dict:
    """Owner-scoped read-only file fetch with post-network authority recheck."""
    if folder not in {"Inbox", "Sent", "Archive", "Drafts", "Deleted"}:
        raise MicrosoftMailboxAccessError("microsoft_mailbox_unavailable")
    from api.inboxes import microsoft_attachment_download

    token, captured = _owned_token(headers, mailbox_id)
    result = microsoft_attachment_download.read_microsoft_file_attachment(
        token, message_id, attachment_id, folder=folder,
    )
    _verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return result


def list_owned_microsoft_archive(
    headers, mailbox_id: object, *, page_size: int = 20, page: int = 1,
) -> dict:
    """Owner-scoped, bounded Archive read with authority checked after all pages."""
    from api.inboxes import microsoft_graph
    if type(page_size) is not int or not 1 <= page_size <= 25:
        raise MicrosoftMailboxAccessError("microsoft_page_size_invalid")
    if type(page) is not int or not 1 <= page <= microsoft_graph.MAX_UI_PAGES:
        raise MicrosoftMailboxAccessError("microsoft_page_invalid")
    token, captured = _owned_token(headers, mailbox_id)
    snapshot = microsoft_graph.list_microsoft_archive(
        token, page_size=page_size, **({"page": page} if page != 1 else {}),
    )
    _verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return snapshot


def read_owned_microsoft_archive_message(headers, mailbox_id: object, message_id: str) -> dict:
    """Only Archive contents may be returned to the authenticated owner."""
    from api.inboxes import microsoft_graph
    token, captured = _owned_token(headers, mailbox_id)
    message = microsoft_graph.get_microsoft_archive_message(token, message_id)
    _verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return message


def list_owned_microsoft_drafts(
    headers, mailbox_id: object, *, page_size: int = 20, page: int = 1,
) -> dict:
    """Owner-scoped read-only drafts with a post-Graph authority recheck."""
    from api.inboxes import microsoft_drafts_graph, microsoft_graph
    if type(page_size) is not int or not 1 <= page_size <= 25:
        raise MicrosoftMailboxAccessError("microsoft_page_size_invalid")
    if type(page) is not int or not 1 <= page <= microsoft_graph.MAX_UI_PAGES:
        raise MicrosoftMailboxAccessError("microsoft_page_invalid")
    token, captured = _owned_token(headers, mailbox_id)
    result = microsoft_drafts_graph.list_microsoft_drafts(
        token, page_size=page_size, **({"page": page} if page != 1 else {}),
    )
    _verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return result


def read_owned_microsoft_draft(headers, mailbox_id: object, message_id: str) -> dict:
    """Read one exact draft, never a message from another folder."""
    from api.inboxes import microsoft_drafts_graph
    token, captured = _owned_token(headers, mailbox_id)
    item = microsoft_drafts_graph.get_microsoft_draft(token, message_id)
    _verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return item


def list_owned_microsoft_deleted(
    headers, mailbox_id: object, *, page_size: int = 20, page: int = 1,
) -> dict:
    """Owner-scoped Deleted Items, with a fresh authority check after all Graph pages."""
    from api.inboxes import microsoft_deleted_graph, microsoft_graph
    if type(page_size) is not int or not 1 <= page_size <= 25:
        raise MicrosoftMailboxAccessError("microsoft_page_size_invalid")
    if type(page) is not int or not 1 <= page <= microsoft_graph.MAX_UI_PAGES:
        raise MicrosoftMailboxAccessError("microsoft_page_invalid")
    token, captured = _owned_token(headers, mailbox_id)
    result = microsoft_deleted_graph.list_microsoft_deleted(
        token, page_size=page_size, **({"page": page} if page != 1 else {}),
    )
    _verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return result


def read_owned_microsoft_deleted(headers, mailbox_id: object, message_id: str) -> dict:
    """Exact read from Deleted Items with unchanged owner and token authority."""
    from api.inboxes import microsoft_deleted_graph
    token, captured = _owned_token(headers, mailbox_id)
    item = microsoft_deleted_graph.get_microsoft_deleted_message(token, message_id)
    _verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return item


def list_owned_microsoft_junk(
    headers, mailbox_id: object, *, page_size: int = 20, page: int = 1,
) -> dict:
    """Owner-scoped Junk Email listing; recheck authority after all Graph calls."""
    from api.inboxes import microsoft_graph
    if type(page_size) is not int or not 1 <= page_size <= 25:
        raise MicrosoftMailboxAccessError("microsoft_page_size_invalid")
    if type(page) is not int or not 1 <= page <= microsoft_graph.MAX_UI_PAGES:
        raise MicrosoftMailboxAccessError("microsoft_page_invalid")
    token, captured = _owned_token(headers, mailbox_id)
    snapshot = microsoft_graph.list_microsoft_junk(
        token, page_size=page_size, **({"page": page} if page != 1 else {}),
    )
    _verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return snapshot


def read_owned_microsoft_junk_message(
    headers, mailbox_id: object, message_id: str,
) -> dict:
    """Only messages from Junk Email may be returned for the selected owner."""
    from api.inboxes import microsoft_graph
    token, captured = _owned_token(headers, mailbox_id)
    message = microsoft_graph.get_microsoft_junk_message(token, message_id)
    _verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return message


def read_owned_microsoft_editable_draft(headers, mailbox_id: object, message_id: str) -> dict:
    """Only a strict, source-folder-qualified plain-text Drafts read."""
    from api.inboxes import microsoft_draft_editor
    token, captured = _owned_token(headers, mailbox_id)
    result = microsoft_draft_editor.read_editable_microsoft_draft(token, message_id)
    _verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return result


def read_owned_microsoft_rich_draft_metadata(headers, mailbox_id: object, message_id: str) -> dict:
    """Exact Drafts HTML metadata read; never returns raw HTML to this editor."""
    from api.inboxes import microsoft_rich_draft
    token, captured = _owned_token(headers, mailbox_id)
    result = microsoft_rich_draft.read_rich_draft_metadata(token, message_id)
    _verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return result


def save_owned_microsoft_editable_draft(
    headers, mailbox_id: object, message_id: str, *, etag: str, edit: object,
    metadata_only: bool = False,
) -> dict:
    """Authorize one optimistic, exact-Drafts edit under mailbox mutation lease."""
    from api.inboxes import microsoft_draft_editor, microsoft_rich_draft
    from api.inboxes.microsoft_graph import MicrosoftGraphError, _required_message_id
    _required_message_id(message_id)
    if type(metadata_only) is not bool:
        raise MicrosoftGraphError('microsoft_draft_edit_invalid')
    if metadata_only:
        microsoft_rich_draft.validate_metadata(edit)
    else:
        microsoft_draft_editor.validate_edit(edit)
    captured = resolve_owned_microsoft_mailbox(headers, mailbox_id)
    try:
        lease = acquire_mailbox_mutation_lease(captured.owner_email, captured.mailbox_id)
    except Exception:
        raise MicrosoftMailboxAccessError('microsoft_mailbox_unavailable') from None
    if type(lease) is not dict:
        raise MicrosoftMailboxAccessError('microsoft_mailbox_unavailable')
    if lease.get('status') != 'acquired':
        raise MicrosoftMailboxAccessError(
            'microsoft_mutation_in_progress' if lease.get('status') == 'held'
            else 'microsoft_mailbox_unavailable'
        )
    lease_token = lease.get('token')
    if type(lease_token) is not str or not lease_token:
        raise MicrosoftMailboxAccessError('microsoft_mailbox_unavailable')
    succeeded=False
    try:
        token, checked = _owned_token(headers, mailbox_id, require_read_write=True)
        if checked != captured:
            raise MicrosoftMailboxAccessError('microsoft_reconnect_required')
        from api.inboxes import microsoft_draft_attachment_policy as attachment_policy
        if not attachment_policy.writes_blocked():
            from api.inboxes import microsoft_draft_attachment_requests as attachment_requests
            if any(item['blocked'] for item in attachment_requests.inspect_draft(captured, message_id)):
                raise MicrosoftGraphError('microsoft_draft_edit_unconfirmed')
        save_operation = (
            microsoft_rich_draft.save_rich_draft_metadata if metadata_only
            else microsoft_draft_editor.save_exact_microsoft_draft
        )
        result = save_operation(
            token, message_id, etag=etag,
            **({'metadata': edit} if metadata_only else {'edit': edit}),
            before_write=lambda: _verify_unchanged_owned_mailbox(
                headers, mailbox_id, captured,
            ),
        )
        _verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
        succeeded=True
        return result
    finally:
        released=False
        try:
            release=release_mailbox_mutation_lease(
                captured.owner_email, captured.mailbox_id, lease_token,
            )
            released=type(release) is dict and release.get('status') == 'released'
        except Exception:
            pass
        if succeeded and not released:
            raise MicrosoftMailboxAccessError('microsoft_mutation_unconfirmed')
