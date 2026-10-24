"""Single-attempt Graph fileAttachment adapter; never PATCH a message.

https://learn.microsoft.com/en-us/graph/api/message-post-attachments
https://learn.microsoft.com/en-us/graph/api/attachment-delete
No retry, redirect, upload session, inline creation or generic URL is accepted.
"""
import base64
import json
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request

from api.inboxes import microsoft_draft_attachment_ledger as ledger
from api.inboxes import microsoft_draft_attachment_policy as policy
from api.inboxes.microsoft_graph import (
    GRAPH_ROOT, GRAPH_TIMEOUT_SECONDS, _GRAPH_OPENER, _GRAPH_PREFER,
    _required_access_token, _required_message_id, _get_json,
)

MAX_RESPONSE_BYTES = 3 * 1024 * 1024


@dataclass(frozen=True)
class AttachmentWriteResult:
    acknowledged: bool
    status: int | None
    attachment_id: str | None = None


def mutate_attachment(token, snapshot, action, *, before_write, opener=None, **file):
    policy.require_release()
    s = ledger._snapshot(snapshot)
    ledger._intent(s, action, **file)
    path = '/me/mailFolders/drafts/messages/' + quote(s.provider_message_id, safe='') + '/attachments'
    body = None
    if action == 'add':
        body = json.dumps({'@odata.type': '#microsoft.graph.fileAttachment',
                           'name': file['name'], 'contentType': file['content_type'], 'isInline': False,
                           'contentBytes': base64.b64encode(file['content']).decode('ascii')},
                          ensure_ascii=True, separators=(',', ':')).encode()
    else:
        path += '/' + quote(_required_message_id(file.get('attachment_id')), safe='')
    request = Request(GRAPH_ROOT + path, data=body, method='POST' if action == 'add' else 'DELETE',
                      headers={'Authorization': 'Bearer ' + _required_access_token(token),
                               'Accept': 'application/json', 'Prefer': _GRAPH_PREFER,
                               **({'Content-Type': 'application/json'} if body is not None else {})})
    if not callable(before_write):
        ledger.fail('operation_invalid')
    before_write()  # Durable start + last owner/lease check, outside transport catch.
    try:
        with (opener or _GRAPH_OPENER.open)(request, timeout=GRAPH_TIMEOUT_SECONDS) as response:
            status = getattr(response, 'status', None)
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if action == 'remove':
            return AttachmentWriteResult(status == 204 and raw == b'', status)
        if status != 201 or type(raw) is not bytes or len(raw) > MAX_RESPONSE_BYTES:
            return AttachmentWriteResult(False, status)
        data = json.loads(raw.decode('utf-8'))
        identifier = _required_message_id(data.get('id'))
        if identifier in {a.id for a in s.attachments}:
            return AttachmentWriteResult(False, status)
        return AttachmentWriteResult(True, status, identifier)
    except HTTPError as error:
        status = error.code
        try:
            error.close()
        except Exception:
            pass
        return AttachmentWriteResult(False, status)
    except Exception:
        # A transport error, lost body or malformed 201 can follow a write.
        # Never retry and never expose provider text or credentials.
        return AttachmentWriteResult(False, None)


def read_added_file_bytes(token, message_id, attachment_id, *, opener=None):
    path = '/me/mailFolders/drafts/messages/' + quote(_required_message_id(message_id), safe='') + '/attachments/' + quote(_required_message_id(attachment_id), safe='')
    payload = _get_json(path, token, maximum_bytes=MAX_RESPONSE_BYTES, opener=opener or _GRAPH_OPENER.open)
    raw = payload.get('contentBytes')
    if (payload.get('id') != attachment_id or payload.get('@odata.type') not in
            {'#microsoft.graph.fileAttachment', 'microsoft.graph.fileAttachment'}
            or type(raw) is not str or len(raw) > 4 * ((policy.MAX_FILE_BYTES + 2)//3)):
        ledger.fail('outcome_uncertain')
    try:
        data = base64.b64decode(raw, validate=True)
    except Exception:
        ledger.fail('outcome_uncertain')
    if len(data) > policy.MAX_FILE_BYTES:
        ledger.fail('outcome_uncertain')
    # The ledger compares the SHA-256 against the original upload bytes. Graph's
    # size metadata is not a byte-integrity proof (notably for PNG attachments).
    return data
