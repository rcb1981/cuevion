"""Disabled, origin/owner-scoped attachment load/mutate/status boundary."""
import base64
import json
import re
from api.auth.http import HttpBoundaryError, get_unique_header, json_response, snapshot_request_headers
from api.inboxes.microsoft_preview_origin import require_microsoft_request_origin
from api.inboxes import microsoft_draft_attachment_policy as policy
from api.inboxes import microsoft_draft_attachment_executor as executor
from api.inboxes import microsoft_draft_attachment_ledger as ledger
from api.inboxes import microsoft_draft_attachment_snapshot as snapshots
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes.microsoft_graph import MicrosoftGraphError, _required_message_id

MAX_REQUEST_BYTES = 12 * 1024 * 1024
_FIELDS = {
    'load': {'action', 'mailboxId', 'providerMessageId'},
    'draft_status': {'action', 'mailboxId', 'providerMessageId'},
    'status': {'action', 'mailboxId', 'providerMessageId', 'requestId'},
    'add': {'action', 'mailboxId', 'providerMessageId', 'requestId', 'snapshotHash', 'files'},
    'remove': {'action', 'mailboxId', 'providerMessageId', 'requestId', 'snapshotHash', 'attachmentId'},
}


def _error(status, code):
    messages = {
        'microsoft_attachment_disabled': 'Microsoft draft attachment changes are not enabled.',
        'microsoft_attachment_selection_invalid': 'Select at most 10 files, 2 MiB each and 8 MiB in total.',
        'microsoft_attachment_intent_invalid': 'Check the attachment name, type and size.',
        'microsoft_attachment_attachment_protected': 'This attachment cannot safely be removed. Inline images are protected.',
        'microsoft_attachment_ledger_full': 'Attachment safety storage is full. No new operation can start.',
        'microsoft_attachment_draft_blocked': 'An earlier attachment operation needs verification. Do not upload or remove it again.',
        'microsoft_attachment_snapshot_conflict': 'The draft changed. Reload it before choosing a new operation.',
        'microsoft_attachment_inventory_incomplete': 'The complete attachment inventory could not be verified.',
        'microsoft_attachment_operation_conflict': 'This operation ID is already bound to another request or version.',
        'microsoft_reconnect_required': 'Reconnect the Microsoft mailbox before continuing.',
        'microsoft_unauthorized': 'Sign in again before continuing.',
        'request_too_large': 'This attachment request is too large.',
        'invalid_request': 'Invalid Microsoft attachment request.',
        'method_not_allowed': 'Use POST for Microsoft draft attachments.',
    }
    safe = code if code in messages else 'microsoft_attachment_outcome_uncertain'
    return json_response(status, {'ok': False, 'error': {'code': safe, 'message': messages.get(safe,
        'The attachment outcome could not be confirmed. Check its status; do not repeat the operation.')},
        'outcome': 'uncertain', 'blocked': True})


def _payload(request, headers):
    if get_unique_header(headers, 'transfer-encoding') is not None:
        raise ValueError
    if get_unique_header(headers, 'content-type', required=True) not in ('application/json', 'application/json; charset=utf-8'):
        raise ValueError
    length = get_unique_header(headers, 'content-length', required=True)
    if type(length) is not str or not re.fullmatch(r'[1-9][0-9]{0,7}', length):
        raise ValueError
    if int(length) > MAX_REQUEST_BYTES:
        raise OverflowError
    raw = request.rfile.read(int(length))
    if type(raw) is not bytes or len(raw) != int(length):
        raise ValueError
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    data = json.loads(raw.decode('utf-8'), object_pairs_hook=unique)
    if type(data) is not dict or type(data.get('action')) is not str:
        raise ValueError
    fields = _FIELDS.get(data['action'])
    if fields is None or set(data) != fields or type(data.get('mailboxId')) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9:_-]{0,159}', data['mailboxId']):
        raise ValueError
    _required_message_id(data.get('providerMessageId'))
    if 'requestId' in fields:
        ledger._id(data['requestId'])
    if 'snapshotHash' in fields and (type(data['snapshotHash']) is not str or not ledger.HASH.fullmatch(data['snapshotHash'])):
        raise ValueError
    if data['action'] == 'remove':
        _required_message_id(data['attachmentId'])
    if data['action'] == 'add':
        files = data['files']
        if type(files) is not list or not 1 <= len(files) <= policy.MAX_NEW_FILES:
            ledger.fail('selection_invalid')
        decoded = []
        total = 0
        for f in files:
            if (type(f) is not dict or set(f) != {'name', 'contentType', 'base64'}
                    or type(f['base64']) is not str or len(f['base64']) > 4 * ((policy.MAX_FILE_BYTES+2)//3)):
                ledger.fail('selection_invalid')
            content = base64.b64decode(f['base64'], validate=True)
            if base64.b64encode(content).decode('ascii') != f['base64']:
                ledger.fail('selection_invalid')
            total += len(content)
            if total > policy.MAX_NEW_BYTES:
                ledger.fail('selection_invalid')
            decoded.append({'name': f['name'], 'content_type': f['contentType'], 'content': content})
        policy.validate_files(decoded)
        data['files'] = decoded
    return data


def _public_receipt(value, data):
    fields = {'requestId', 'providerMessageId', 'outcome', 'count', 'completed', 'blocked'}
    if (type(value) is not dict or set(value) != fields
            or value['requestId'] != data['requestId'] or value['providerMessageId'] != data['providerMessageId']
            or value['outcome'] not in ledger.OUTCOMES
            or type(value['count']) is not int or not 1 <= value['count'] <= 10
            or type(value['completed']) is not int or not 0 <= value['completed'] <= value['count']
            or value['blocked'] is not (value['outcome'] == 'uncertain')
            or value['outcome'] == 'confirmed_performed' and value['completed'] != value['count']
            or value['outcome'] == 'confirmed_not_performed' and value['completed'] != 0):
        ledger.fail('outcome_uncertain')
    return {key: value[key] for key in fields}


def microsoft_draft_attachment_response(request):
    if getattr(request, 'command', '') != 'POST':
        return _error(405, 'method_not_allowed')
    if policy.writes_blocked():
        return _error(403, 'microsoft_attachment_disabled')
    try:
        headers = snapshot_request_headers(request)
        require_microsoft_request_origin(headers)
        data = _payload(request, headers)
    except HttpBoundaryError as error:
        return _error(error.status, 'invalid_request')
    except OverflowError:
        return _error(413, 'request_too_large')
    except ledger.AttachmentLedgerError as error:
        return _error(400, error.code)
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, MicrosoftGraphError):
        return _error(400, 'invalid_request')
    try:
        action = data['action']
        args = (headers, data['mailboxId'], data['providerMessageId'])
        if action == 'draft_status':
            values = executor.inspect_owned_draft_attachment_requests(*args)
            if type(values) is not list or len(values) > 32:
                ledger.fail('outcome_uncertain')
            receipts = []
            for value in values:
                if type(value) is not dict:
                    ledger.fail('outcome_uncertain')
                ledger._id(value.get('requestId'))
                receipts.append(_public_receipt(value, {**data, 'requestId': value['requestId']}))
            if len({x['requestId'] for x in receipts}) != len(receipts):
                ledger.fail('outcome_uncertain')
            return json_response(200, {'ok': True, 'mailboxId': data['mailboxId'],
                'providerMessageId': data['providerMessageId'], 'requests': receipts})
        if action == 'load':
            value = snapshots.read_owned_snapshot(*args)
            attachments = []
            for x in value.attachments:
                removable = True
                try:
                    ledger._snapshot(value)
                    ledger._intent(value, 'remove', attachment_id=x.id)
                except (ledger.AttachmentLedgerError, MicrosoftGraphError):
                    removable = False
                attachments.append({'id': x.id, 'name': x.name, 'size': x.size, 'contentType': x.content_type,
                                    'isInline': x.is_inline, 'kind': x.kind, 'contentId': x.content_id,
                                    'removable': removable})
            return json_response(200, {'ok': True, 'mailboxId': data['mailboxId'], 'providerMessageId': value.provider_message_id,
                                      'snapshotHash': value.fingerprint, 'attachments': attachments, 'complete': True})
        if action == 'status':
            result = executor.inspect_owned_attachment_request(*args, data['requestId'])
        else:
            result = executor.execute_owned_attachment_request(*args, data['requestId'], action,
                expected_fingerprint=data['snapshotHash'],
                **({'files': data['files']} if action == 'add' else {'attachment_id': data['attachmentId']}))
        return json_response(200, {'ok': True, 'mailboxId': data['mailboxId'], **_public_receipt(result, data)})
    except owned.MicrosoftMailboxAccessError as error:
        return _error(401 if error.code == 'microsoft_unauthorized' else 409, error.code)
    except (ledger.AttachmentLedgerError, MicrosoftGraphError) as error:
        return _error(409, error.code)
    except Exception:
        return _error(503, 'microsoft_attachment_outcome_uncertain')
