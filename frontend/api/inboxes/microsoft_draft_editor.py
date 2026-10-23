"""Exact-folder, conditional, single-PATCH editor for plain-text Microsoft Drafts.

No HTML rewrites, no attachments, no sending, no automatic retry. The browser
never supplies a Graph URL or authoritative folder, recipient identity or token.
"""
from __future__ import annotations

import json
import re
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request

from api.auth.email_address import is_valid_auth_email, normalize_auth_email
from api.inboxes.microsoft_graph import (
    GRAPH_ROOT, GRAPH_TIMEOUT_SECONDS, MAX_GRAPH_JSON_BYTES,
    MicrosoftGraphError, _GRAPH_OPENER, _GRAPH_PREFER,
    _get_json, _required_access_token, _required_message_id,
)
from api.inboxes.microsoft_message_flags import _error_from_http
from api.inboxes.microsoft_archive_move import _json_graph

MAX_TEXT = 32000
MAX_RECIPIENTS = 20
_MAX_SUBJECT = 250
_ETAG = re.compile(r'^(?:W/)?"[\x21\x23-\x7e]{1,256}"$')
_SELECT = 'id,parentFolderId,isDraft,hasAttachments,subject,toRecipients,ccRecipients,body,changeKey'


def _addresses(value: object) -> list[str]:
    if type(value) is not list or len(value) > MAX_RECIPIENTS:
        raise MicrosoftGraphError('microsoft_draft_edit_invalid')
    result = []
    for entry in value:
        if type(entry) is not str or len(entry) > 320:
            raise MicrosoftGraphError('microsoft_draft_edit_invalid')
        email = normalize_auth_email(entry)
        if not is_valid_auth_email(email) or email != entry:
            raise MicrosoftGraphError('microsoft_draft_edit_invalid')
        result.append(email)
    if len(result) != len(set(result)):
        raise MicrosoftGraphError('microsoft_draft_edit_invalid')
    return result


def validate_edit(value: object) -> dict:
    if type(value) is not dict or set(value) != {'subject','to','cc','body'}:
        raise MicrosoftGraphError('microsoft_draft_edit_invalid')
    subject, body = value['subject'], value['body']
    if (
        type(subject) is not str or len(subject) > _MAX_SUBJECT
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in subject)
        or type(body) is not str or len(body) > MAX_TEXT
        or '\x00' in body or len(body.encode('utf-8')) > MAX_TEXT * 4
    ):
        raise MicrosoftGraphError('microsoft_draft_edit_invalid')
    to = _addresses(value['to'])
    cc = _addresses(value['cc'])
    if len(to)+len(cc) > MAX_RECIPIENTS or set(to) & set(cc):
        raise MicrosoftGraphError('microsoft_draft_edit_invalid')
    return {'subject':subject,'to':to,'cc':cc,'body':body}


def _parse_graph_addresses(value: object) -> list[str]:
    if type(value) is not list or len(value) > MAX_RECIPIENTS:
        raise MicrosoftGraphError('microsoft_draft_edit_unsupported')
    entries=[]
    for item in value:
        address=(item.get('emailAddress') or {}).get('address') if type(item) is dict else None
        if type(address) is not str:
            raise MicrosoftGraphError('microsoft_draft_edit_unsupported')
        entries.append(normalize_auth_email(address))
    try:
        return _addresses(entries)
    except MicrosoftGraphError:
        raise MicrosoftGraphError('microsoft_draft_edit_unsupported') from None


def _current(token: str, message_id: str, *, opener=None) -> dict:
    exact = _required_message_id(message_id)
    if opener is None:
        opener = _GRAPH_OPENER.open
    encoded=quote(exact, safe='')
    data=_get_json(
        '/me/mailFolders/drafts/messages/' + encoded + '?$select=' + _SELECT,
        _required_access_token(token), maximum_bytes=MAX_GRAPH_JSON_BYTES, opener=opener,
    )
    if type(data) is not dict or data.get('id') != exact or data.get('isDraft') is not True:
        raise MicrosoftGraphError('microsoft_draft_edit_unsupported')
    if data.get('hasAttachments') is not False:
        raise MicrosoftGraphError('microsoft_draft_edit_unsupported')
    folder=_json_graph(token, '/me/mailFolders/drafts?$select=id', opener=opener)
    folder_id=_required_message_id(folder.get('id'))
    if data.get('parentFolderId') != folder_id:
        raise MicrosoftGraphError('microsoft_draft_edit_conflict')
    body=data.get('body')
    if type(body) is not dict or str(body.get('contentType','')).lower() != 'text' or type(body.get('content')) is not str:
        raise MicrosoftGraphError('microsoft_draft_edit_unsupported')
    etag=data.get('@odata.etag')
    if type(etag) is not str or _ETAG.fullmatch(etag) is None:
        raise MicrosoftGraphError('microsoft_draft_edit_unsupported')
    subject=data.get('subject')
    if subject is None:subject=''
    if type(subject) is not str:
        raise MicrosoftGraphError('microsoft_draft_edit_unsupported')
    raw={'subject':subject,
         'to':_parse_graph_addresses(data.get('toRecipients')),
         'cc':_parse_graph_addresses(data.get('ccRecipients')),
         'body':body['content']}
    try:
        edit=validate_edit(raw)
    except MicrosoftGraphError:
        raise MicrosoftGraphError('microsoft_draft_edit_unsupported') from None
    return {'providerMessageId':exact,'etag':etag,'edit':edit}


def read_editable_microsoft_draft(token: str, message_id: str, *, opener=None) -> dict:
    """Strict read. Returns unsupported for rich-content/attachment drafts."""
    return _current(token,message_id,opener=opener)


def save_exact_microsoft_draft(token: str, message_id: str, *, etag: str,
                               edit: object, before_write=None, opener=None) -> dict:
    """One conditional PATCH. If uncertain, client MUST refresh, never retry."""
    exact=_required_message_id(message_id)
    if type(etag) is not str or _ETAG.fullmatch(etag) is None:
        raise MicrosoftGraphError('microsoft_draft_edit_invalid')
    proposed=validate_edit(edit)
    if opener is None:
        opener=_GRAPH_OPENER.open
    prior=_current(token,exact,opener=opener)
    if prior['etag'] != etag:
        raise MicrosoftGraphError('microsoft_draft_edit_conflict')
    if proposed == prior['edit']:
        if before_write is not None: before_write()
        return {**prior,'changed':False}
    if before_write is not None: before_write()
    path='/me/mailFolders/drafts/messages/' + quote(exact,safe='')
    def mapped(items):return [{'emailAddress':{'address':v}} for v in items]
    payload={'subject':proposed['subject'],
             'toRecipients':mapped(proposed['to']),
             'ccRecipients':mapped(proposed['cc']),
             'body':{'contentType':'Text','content':proposed['body']}}
    body=json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
    request=Request(
        GRAPH_ROOT+path,data=body,method='PATCH',headers={
            'Authorization':'Bearer '+_required_access_token(token),
            'Accept':'application/json','Content-Type':'application/json',
            'Prefer':_GRAPH_PREFER,'If-Match':etag,
        },
    )
    try:
        with opener(request,timeout=GRAPH_TIMEOUT_SECONDS) as response:
            if getattr(response,'status',None)!=200:
                raise MicrosoftGraphError('microsoft_draft_edit_unconfirmed')
            response.read(MAX_GRAPH_JSON_BYTES+1)
    except HTTPError as exc:
        mapped=_error_from_http(exc)
        if mapped.code=='microsoft_mutation_conflict':
            raise MicrosoftGraphError('microsoft_draft_edit_conflict') from None
        raise MicrosoftGraphError('microsoft_draft_edit_unconfirmed') from None
    except (URLError,TimeoutError,OSError,IncompleteRead):
        raise MicrosoftGraphError('microsoft_draft_edit_unconfirmed') from None
    try:
        confirmed=_current(token,exact,opener=opener)
    except MicrosoftGraphError:
        raise MicrosoftGraphError('microsoft_draft_edit_unconfirmed') from None
    if confirmed['edit'] != proposed or confirmed['etag']==etag:
        raise MicrosoftGraphError('microsoft_draft_edit_unconfirmed')
    return {**confirmed,'changed':True}
