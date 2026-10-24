"""Strict metadata-only edit of an existing rich-HTML Microsoft Draft.

This module is NOT a sending or HTML editing route. It changes only subject,
To and Cc, and proves the original HTML body remained byte-identical after
one conditional Graph PATCH. Bcc and CID body editing remain deliberately unsupported.
"""
from __future__ import annotations

import hashlib
import json
from html.parser import HTMLParser
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request

from api.inboxes import microsoft_draft_editor as plain
from api.inboxes.microsoft_graph import (
    GRAPH_ROOT, GRAPH_TIMEOUT_SECONDS, MAX_GRAPH_JSON_BYTES,
    MicrosoftGraphError, _GRAPH_OPENER, _GRAPH_PREFER,
    _get_json, _required_access_token, _required_message_id,
)
from api.inboxes.microsoft_archive_move import _json_graph
from api.inboxes.microsoft_message_flags import _error_from_http

_SELECT = 'id,parentFolderId,isDraft,hasAttachments,subject,toRecipients,ccRecipients,bccRecipients,body'
_MAX_HTML_BYTES = 96_000


class _TextPreview(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.size = 0
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'svg', 'noscript'):
            self.hidden += 1
        elif tag in ('br', 'p', 'div', 'li', 'tr') and not self.hidden:
            self.handle_data('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'svg', 'noscript') and self.hidden:
            self.hidden -= 1
        elif tag in ('p', 'div', 'li') and not self.hidden:
            self.handle_data('\n')

    def handle_data(self, data):
        if not self.hidden and self.size < 4000:
            text = data[:4000 - self.size]
            self.parts.append(text)
            self.size += len(text)

    def preview(self):
        return ' '.join(''.join(self.parts).split())[:4000]


def validate_metadata(value: object) -> dict:
    """Only subject, To and Cc. A browser cannot submit edited HTML/body."""
    if type(value) is not dict or set(value) != {'subject', 'to', 'cc'}:
        raise MicrosoftGraphError('microsoft_draft_edit_invalid')
    candidate = {**value, 'body': ''}
    accepted = plain.validate_edit(candidate)
    return {key: accepted[key] for key in ('subject', 'to', 'cc')}


def _current(token: str, message_id: str, *, opener=None) -> dict:
    exact = _required_message_id(message_id)
    access = _required_access_token(token)
    if opener is None:
        opener = _GRAPH_OPENER.open
    data = _get_json(
        '/me/mailFolders/drafts/messages/' + quote(exact, safe='') + '?$select=' + _SELECT,
        access, maximum_bytes=MAX_GRAPH_JSON_BYTES, opener=opener,
    )
    if type(data) is not dict or data.get('id') != exact or data.get('isDraft') is not True:
        raise MicrosoftGraphError('microsoft_draft_edit_unsupported')
    from api.inboxes import microsoft_draft_attachment_policy as attachment_policy
    attachment_support = not attachment_policy.writes_blocked()
    if (type(data.get('hasAttachments')) is not bool or data['hasAttachments'] and not attachment_support
            or data.get('bccRecipients') != []):
        raise MicrosoftGraphError('microsoft_draft_edit_unsupported')
    folder = _json_graph(access, '/me/mailFolders/drafts?$select=id', opener=opener)
    folder_id = _required_message_id(folder.get('id'))
    if data.get('parentFolderId') != folder_id:
        raise MicrosoftGraphError('microsoft_draft_edit_conflict')
    body = data.get('body')
    if type(body) is not dict or str(body.get('contentType', '')).lower() != 'html':
        raise MicrosoftGraphError('microsoft_draft_edit_unsupported')
    html = body.get('content')
    if (type(html) is not str or len(html.encode('utf-8')) > _MAX_HTML_BYTES
            or 'cid:' in html.lower()):
        # Graph hasAttachments may exclude inline CID attachments. Their
        # preservation cannot be independently proven here, so fail closed.
        raise MicrosoftGraphError('microsoft_draft_edit_unsupported')
    etag = data.get('@odata.etag')
    if type(etag) is not str or plain._ETAG.fullmatch(etag) is None:
        raise MicrosoftGraphError('microsoft_draft_edit_unsupported')
    raw = {
        'subject': data.get('subject') or '',
        'to': plain._parse_graph_addresses(data.get('toRecipients')),
        'cc': plain._parse_graph_addresses(data.get('ccRecipients')),
    }
    try:
        metadata = validate_metadata(raw)
    except MicrosoftGraphError:
        raise MicrosoftGraphError('microsoft_draft_edit_unsupported') from None
    preview = _TextPreview()
    preview.feed(html)
    preview.close()
    return {
        'providerMessageId': exact,
        'etag': etag,
        'metadata': metadata,
        'bodyPreview': preview.preview(),
        '_bodyHash': hashlib.sha256(html.encode('utf-8')).hexdigest(),
        '_attachments': plain._attachment_proof(token, exact, opener=opener, expected_attachments=data['hasAttachments']) if attachment_support else None,
    }


def read_rich_draft_metadata(token: str, message_id: str, *, opener=None) -> dict:
    record = _current(token, message_id, opener=opener)
    return {k: v for k, v in record.items() if not k.startswith('_')}


def save_rich_draft_metadata(token: str, message_id: str, *, etag: str,
                             metadata: object, before_write=None, opener=None) -> dict:
    """One If-Match PATCH, no body key, no retry, independent body proof."""
    exact = _required_message_id(message_id)
    if type(etag) is not str or plain._ETAG.fullmatch(etag) is None:
        raise MicrosoftGraphError('microsoft_draft_edit_invalid')
    edit = validate_metadata(metadata)
    if opener is None:
        opener = _GRAPH_OPENER.open
    prior = _current(token, exact, opener=opener)
    if prior['etag'] != etag:
        raise MicrosoftGraphError('microsoft_draft_edit_conflict')
    if edit == prior['metadata']:
        if before_write is not None:
            before_write()
        return {**{k: v for k, v in prior.items() if not k.startswith('_')}, 'changed': False}
    if before_write is not None:
        before_write()
    def map_recipients(values):
        return [{'emailAddress': {'address': addr}} for addr in values]
    payload = {
        'subject': edit['subject'],
        'toRecipients': map_recipients(edit['to']),
        'ccRecipients': map_recipients(edit['cc']),
    }
    request = Request(
        GRAPH_ROOT + '/me/mailFolders/drafts/messages/' + quote(exact, safe=''),
        data=json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8'),
        method='PATCH',
        headers={
            'Authorization': 'Bearer ' + _required_access_token(token),
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Prefer': _GRAPH_PREFER,
            'If-Match': etag,
        },
    )
    try:
        with opener(request, timeout=GRAPH_TIMEOUT_SECONDS) as response:
            if getattr(response, 'status', None) != 200:
                raise MicrosoftGraphError('microsoft_draft_edit_unconfirmed')
            response.read(MAX_GRAPH_JSON_BYTES + 1)
    except HTTPError as exc:
        mapped = _error_from_http(exc)
        if mapped.code == 'microsoft_mutation_conflict':
            raise MicrosoftGraphError('microsoft_draft_edit_conflict') from None
        raise MicrosoftGraphError('microsoft_draft_edit_unconfirmed') from None
    except (URLError, TimeoutError, OSError, IncompleteRead):
        raise MicrosoftGraphError('microsoft_draft_edit_unconfirmed') from None
    try:
        confirmed = _current(token, exact, opener=opener)
    except MicrosoftGraphError:
        raise MicrosoftGraphError('microsoft_draft_edit_unconfirmed') from None
    if (confirmed['metadata'] != edit or confirmed['_bodyHash'] != prior['_bodyHash']
            or confirmed['etag'] == prior['etag'] or confirmed['_attachments'] != prior['_attachments']):
        raise MicrosoftGraphError('microsoft_draft_edit_unconfirmed')
    return {**{k: v for k, v in confirmed.items() if not k.startswith('_')}, 'changed': True}
