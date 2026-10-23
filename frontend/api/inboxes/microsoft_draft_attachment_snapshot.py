"""Complete, observational Drafts snapshots. GET only; never rewrite message content.

Two inventories and three message observations detect concurrent edits, but are
not a transaction with Outlook. Graph attachment APIs have no proven equivalent
of the message PATCH If-Match guarantee. A later executor must remain single-shot.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from html import unescape
from urllib.parse import quote, urlencode, unquote

from api.inboxes.microsoft_graph import (
    MicrosoftGraphError, _get_json, _required_message_id, _GRAPH_OPENER,
    MAX_GRAPH_JSON_BYTES,
)
from api.inboxes.microsoft_archive_move import _json_graph
from api.inboxes.microsoft_draft_editor import _ETAG
from api.inboxes.microsoft_attachments_graph import (
    _attachment_summary, _continuation_path, _ATTACHMENT_SELECT,
    MAX_ATTACHMENT_ROWS, MAX_ATTACHMENT_PAGES, MAX_ATTACHMENT_BYTES,
)
from api.inboxes.microsoft_token_authority import MicrosoftMailboxAuthority, _canonical_authority

SELECT = 'id,parentFolderId,isDraft,hasAttachments,changeKey,subject,toRecipients,ccRecipients,bccRecipients,body'


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def authority_digest(authority: MicrosoftMailboxAuthority, *, generation=True) -> str:
    a = _canonical_authority(authority)
    parts = [a.owner_email, a.mailbox_id, a.email, a.account_id]
    if generation:
        parts.append(a.credential_generation)
    return digest(parts)


def invalid(code='snapshot_invalid'):
    raise MicrosoftGraphError('microsoft_attachment_' + code)


@dataclass(frozen=True)
class DraftAttachment:
    id: str
    name: str
    size: int
    content_type: str
    is_inline: bool
    kind: str
    content_id: str | None
    metadata_hash: str


@dataclass(frozen=True)
class DraftAttachmentSnapshot:
    authority: MicrosoftMailboxAuthority
    provider_message_id: str
    drafts_folder_id: str
    is_draft: bool
    etag: str
    change_key: str
    attachments: tuple[DraftAttachment, ...]
    inventory_complete: bool
    body_type: str
    body_hash: str
    protected_content_hash: str
    cid_links: tuple[tuple[str, tuple[str, ...]], ...]
    inventory_hash: str
    fingerprint: str


def _message(token, message_id, folder_id, opener):
    value = _get_json('/me/mailFolders/drafts/messages/' + quote(message_id, safe='') +
                      '?$select=' + SELECT, token, maximum_bytes=MAX_GRAPH_JSON_BYTES, opener=opener)
    body, etag, change = value.get('body'), value.get('@odata.etag'), value.get('changeKey')
    if (value.get('id') != message_id or value.get('parentFolderId') != folder_id
            or value.get('isDraft') is not True or type(value.get('hasAttachments')) is not bool
            or type(etag) is not str or not _ETAG.fullmatch(etag)
            or type(change) is not str or not 1 <= len(change) <= 512
            or any(ord(c) < 33 or ord(c) == 127 for c in change)
            or type(body) is not dict or set(body) != {'contentType', 'content'}
            or type(body.get('contentType')) is not str
            or body['contentType'].lower() not in {'html', 'text'}
            or type(body.get('content')) is not str or len(body['content'].encode()) > 512 * 1024
            or type(value.get('subject')) is not str):
        invalid()
    for field in ('toRecipients', 'ccRecipients', 'bccRecipients'):
        entries = value.get(field)
        if type(entries) is not list or len(entries) > 500:
            invalid()
        for recipient in entries:
            if (type(recipient) is not dict or type(recipient.get('emailAddress')) is not dict
                    or type(recipient['emailAddress'].get('address')) is not str
                    or not 1 <= len(recipient['emailAddress']['address']) <= 320):
                invalid()
    # Hash original, unnormalised metadata and HTML; never write it back.
    return {field: value[field] for field in (
        'id', 'parentFolderId', 'isDraft', 'hasAttachments', '@odata.etag', 'changeKey',
        'subject', 'toRecipients', 'ccRecipients', 'bccRecipients', 'body')}


def _inventory(token, message_id, opener):
    collection = '/me/mailFolders/drafts/messages/' + quote(message_id, safe='') + '/attachments'
    path = collection + '?' + urlencode({'$top': MAX_ATTACHMENT_ROWS, '$select': _ATTACHMENT_SELECT})
    items, seen, visited = [], set(), set()
    for _page in range(MAX_ATTACHMENT_PAGES):
        if path in visited:
            invalid('inventory_incomplete')
        visited.add(path)
        response = _get_json(path, token, maximum_bytes=MAX_ATTACHMENT_BYTES, opener=opener)
        rows = response.get('value')
        if type(rows) is not list or len(rows) > MAX_ATTACHMENT_ROWS:
            invalid('inventory_incomplete')
        for row in rows:
            item = _attachment_summary(row)
            if item['id'] in seen:
                invalid('inventory_incomplete')
            seen.add(item['id'])
            # A display-only CID hint must never grant mutation authority.
            kind = {'#microsoft.graph.fileAttachment': 'file', 'microsoft.graph.fileAttachment': 'file',
                    '#microsoft.graph.itemAttachment': 'item', 'microsoft.graph.itemAttachment': 'item',
                    '#microsoft.graph.referenceAttachment': 'reference', 'microsoft.graph.referenceAttachment': 'reference'
                    }.get(row.get('@odata.type'), 'other')
            cid = row.get('contentId')
            if cid is not None:
                cid = cid.strip('<>')
                if not re.fullmatch(r'[a-zA-Z0-9_.@+%\-]{1,200}', cid):
                    invalid()
            metadata = {**item, 'kind': kind, 'contentId': cid}
            items.append(DraftAttachment(item['id'], item['name'], item['size'], item['contentType'],
                                         item['isInline'], kind, cid, digest(metadata)))
        link = response.get('@odata.nextLink')
        if link is None:
            return tuple(sorted(items, key=lambda x: x.id))
        path = _continuation_path(link, collection_path=collection)
        if not rows:
            invalid('inventory_incomplete')
    invalid('inventory_incomplete')


def read_draft_attachment_snapshot(token, authority, message_id, *, opener=None):
    """Internal trusted-token API. Use read_owned_snapshot at an owner boundary."""
    a = _canonical_authority(authority)
    identifier = _required_message_id(message_id)
    opener = opener or _GRAPH_OPENER.open
    folder = _json_graph(token, '/me/mailFolders/drafts?$select=id', opener=opener)
    folder_id = _required_message_id(folder.get('id'))
    first = _message(token, identifier, folder_id, opener)
    inventory = _inventory(token, identifier, opener)
    middle = _message(token, identifier, folder_id, opener)
    second_inventory = _inventory(token, identifier, opener)
    last = _message(token, identifier, folder_id, opener)
    if first != middle or first != last or inventory != second_inventory:
        invalid('snapshot_conflict')
    if first['hasAttachments'] and not inventory:
        invalid('inventory_incomplete')
    body = first['body']
    # Conservative superset: CID mentions in HTML/CSS/comments are protected too.
    refs = set(unquote(unescape(cid)) for cid in re.findall(
        r'(?i)cid:([^\s\"\'<>\)]+)', unescape(body['content']))) if body['contentType'].lower() == 'html' else set()
    if len(refs) > 100 or any(not re.fullmatch(r'[a-zA-Z0-9_.@+%\-]{1,200}', cid) for cid in refs):
        invalid('cid_unsupported')
    links = tuple((cid, tuple(x.id for x in inventory if x.content_id == cid)) for cid in sorted(refs))
    protected = digest({k: first[k] for k in ('subject', 'toRecipients', 'ccRecipients', 'bccRecipients', 'body')})
    inventory_hash = digest([(x.id, x.metadata_hash) for x in inventory])
    fingerprint = digest([authority_digest(a), first, inventory_hash, links])
    return DraftAttachmentSnapshot(a, identifier, folder_id, True, first['@odata.etag'], first['changeKey'],
                                   inventory, True, body['contentType'].lower(), digest(body), protected,
                                   links, inventory_hash, fingerprint)


def read_owned_snapshot(headers, mailbox_id, message_id, *, opener=None):
    from api.inboxes import microsoft_mailbox_access as owned
    token, authority = owned._owned_token(headers, mailbox_id)
    snapshot = read_draft_attachment_snapshot(token, authority, message_id, opener=opener)
    owned._verify_unchanged_owned_mailbox(headers, mailbox_id, authority)
    return snapshot
