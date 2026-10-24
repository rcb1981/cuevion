"""One-shot create/PATCH of exact Drafts. No sending, generic paths or redirects.

https://learn.microsoft.com/en-us/graph/api/user-post-messages
https://learn.microsoft.com/en-us/graph/api/message-update
Attachments are deliberately absent from message payloads and independently
preserved; phase 3's executor remains the only attachment writer.
"""
import json
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request
from api.inboxes import microsoft_compose_draft_policy as policy
from api.inboxes import microsoft_draft_editor as plain
from api.inboxes import microsoft_draft_attachment_snapshot as snapshots
from api.inboxes.microsoft_graph import (
    GRAPH_ROOT, GRAPH_TIMEOUT_SECONDS, MAX_GRAPH_JSON_BYTES, MicrosoftGraphError,
    _GRAPH_OPENER, _GRAPH_PREFER, _get_json, _required_access_token, _required_message_id,
)


@dataclass(frozen=True)
class CurrentDraft:
    snapshot: snapshots.DraftAttachmentSnapshot
    edit: dict

    def public(self):
        return {'providerMessageId': self.snapshot.provider_message_id, 'etag': self.snapshot.etag,
                'snapshotHash': self.snapshot.fingerprint, 'edit': self.edit}


def read_current(token, authority, message_id, *, opener=None):
    opener = opener or _GRAPH_OPENER.open
    snapshot = snapshots.read_draft_attachment_snapshot(token, authority, message_id, opener=opener)
    data = _get_json('/me/mailFolders/drafts/messages/' + quote(_required_message_id(message_id), safe='')
        + '?$select=' + snapshots.SELECT, token, maximum_bytes=MAX_GRAPH_JSON_BYTES, opener=opener)
    protected = {k: data.get(k) for k in ('subject', 'toRecipients', 'ccRecipients', 'bccRecipients', 'body')}
    if (data.get('id') != message_id or data.get('isDraft') is not True
            or data.get('parentFolderId') != snapshot.drafts_folder_id
            or data.get('@odata.etag') != snapshot.etag or data.get('changeKey') != snapshot.change_key
            or snapshots.digest(protected) != snapshot.protected_content_hash):
        raise MicrosoftGraphError('microsoft_compose_conflict')
    if snapshot.body_type != 'text':
        raise MicrosoftGraphError('microsoft_compose_unsupported')
    edit = policy.validate_edit({'subject': data['subject'], 'body': data['body']['content'],
        'to': plain._parse_graph_addresses(data['toRecipients']),
        'cc': plain._parse_graph_addresses(data['ccRecipients']),
        'bcc': plain._parse_graph_addresses(data['bccRecipients'])})
    return CurrentDraft(snapshot, edit)


@dataclass(frozen=True)
class WriteResult:
    acknowledged: bool
    rejected: bool
    draft_id: str | None = None


def write_once(token, edit, *, before_write, message_id=None, etag=None, opener=None):
    policy.require_release()
    edit = policy.validate_edit(edit)
    path = '/me/mailFolders/drafts/messages'
    if message_id is not None:
        path += '/' + quote(_required_message_id(message_id), safe='')
        if type(etag) is not str or not plain._ETAG.fullmatch(etag):
            raise MicrosoftGraphError('microsoft_compose_invalid')
    elif etag is not None:
        raise MicrosoftGraphError('microsoft_compose_invalid')
    payload = {'subject': edit['subject'], 'body': {'contentType': 'Text', 'content': edit['body']},
               **{k + 'Recipients': [{'emailAddress': {'address': address}} for address in edit[k]] for k in ('to', 'cc', 'bcc')}}
    request = Request(GRAPH_ROOT + path, method='PATCH' if message_id is not None else 'POST',
        data=json.dumps(payload, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode(),
        headers={'Authorization': 'Bearer ' + _required_access_token(token), 'Content-Type': 'application/json',
            'Accept': 'application/json', 'Prefer': _GRAPH_PREFER, **({'If-Match': etag} if message_id is not None else {})})
    if not callable(before_write):
        raise MicrosoftGraphError('microsoft_compose_invalid')
    before_write()
    try:
        with (opener or _GRAPH_OPENER.open)(request, timeout=GRAPH_TIMEOUT_SECONDS) as response:
            status = getattr(response, 'status', None)
            raw = response.read(MAX_GRAPH_JSON_BYTES + 1)
        if status != (200 if message_id is not None else 201) or type(raw) is not bytes or len(raw) > MAX_GRAPH_JSON_BYTES:
            return WriteResult(False, status in {400, 401, 403, 404, 409, 412, 429})
        value = json.loads(raw.decode('utf-8'))
        identifier = _required_message_id(value.get('id'))
        if value.get('isDraft') is not True or message_id is not None and identifier != message_id:
            return WriteResult(False, False)
        return WriteResult(True, False, identifier)
    except HTTPError as error:
        status = error.code
        try:
            error.close()
        except Exception:
            pass
        return WriteResult(False, status in {400, 401, 403, 404, 409, 412, 429})
    except Exception:
        return WriteResult(False, False)
