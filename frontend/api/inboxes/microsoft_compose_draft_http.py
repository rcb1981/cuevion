"""Strict, disabled, same-origin draft-only HTTP boundary; no sending action."""
import json
import re
from api.auth.http import HttpBoundaryError, get_unique_header, json_response, snapshot_request_headers
from api.inboxes.microsoft_preview_origin import require_microsoft_request_origin
from api.inboxes.microsoft_graph import MicrosoftGraphError, _required_message_id
from api.inboxes import microsoft_compose_draft_policy as policy
from api.inboxes import microsoft_compose_draft_service as service
from api.inboxes import microsoft_draft_attachment_ledger as attachment_ledger
from api.inboxes.microsoft_mailbox_access import MicrosoftMailboxAccessError

MAX_REQUEST_BYTES = 256 * 1024
FIELDS = {
    'status': {'action', 'mailboxId'},
    'load': {'action', 'mailboxId', 'providerMessageId'},
    'create': {'action', 'mailboxId', 'requestId', 'edit'},
    'update': {'action', 'mailboxId', 'requestId', 'providerMessageId', 'etag', 'edit'},
}
MESSAGES = {
    'microsoft_compose_disabled': 'Microsoft draft composition is not enabled.',
    'microsoft_compose_invalid': 'Check the recipients, subject and message. Use at most 20 distinct recipients and 32000 message characters.',
    'microsoft_compose_conflict': 'This draft changed in Outlook. Reopen it before saving. No overwrite was attempted.',
    'microsoft_compose_unsupported': 'Only plain-text drafts can be edited here. HTML and CID content remain protected.',
    'microsoft_compose_busy': 'Another mailbox operation is in progress. Check status before continuing.',
    'microsoft_compose_blocked': 'An attachment operation needs verification. Do not repeat it.',
    'microsoft_compose_operation_blocked': 'An earlier draft operation is uncertain. Do not create or save it again.',
    'microsoft_compose_operation_conflict': 'This operation identifier cannot be reused for a different draft or message.',
    'microsoft_compose_store_unavailable': 'Secure draft tracking could not be verified. Check status before continuing.',
    'microsoft_compose_store_invalid': 'Secure draft tracking could not be verified. Check status before continuing.',
    'microsoft_compose_ledger_full': 'Secure draft tracking is full. New operations are blocked.',
    'microsoft_reconnect_required': 'Reconnect this Microsoft mailbox before continuing.',
    'microsoft_write_permission_required': 'Reconnect Microsoft to allow draft saving.',
    'microsoft_unauthorized': 'Sign in again before continuing.',
    'microsoft_mailbox_not_found': 'The selected Microsoft mailbox could not be verified.',
    'microsoft_compose_uncertain': 'The draft result is uncertain. Check status and Microsoft Drafts before repeating any action.',
}


def error(status, code):
    code = code if code in MESSAGES else 'microsoft_compose_uncertain'
    return json_response(status, {'ok': False, 'blocked': True, 'error': {'code': code, 'message': MESSAGES[code]}})


def payload(request, headers):
    if get_unique_header(headers, 'transfer-encoding') is not None:
        raise ValueError
    if get_unique_header(headers, 'content-type', required=True) not in {'application/json', 'application/json; charset=utf-8'}:
        raise ValueError
    length = get_unique_header(headers, 'content-length', required=True)
    if type(length) is not str or not re.fullmatch(r'[1-9][0-9]{0,6}', length):
        raise ValueError
    if int(length) > MAX_REQUEST_BYTES:
        raise OverflowError
    raw = request.rfile.read(int(length))
    if type(raw) is not bytes or len(raw) != int(length):
        raise ValueError
    def unique(pairs):
        value = {}
        for k, v in pairs:
            if k in value:
                raise ValueError
            value[k] = v
        return value
    data = json.loads(raw.decode('utf-8'), object_pairs_hook=unique)
    if (type(data) is not dict or type(data.get('action')) is not str
            or data['action'] not in FIELDS or set(data) != FIELDS[data['action']]
            or type(data.get('mailboxId')) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9:_-]{0,159}', data['mailboxId'])):
        raise ValueError
    if 'providerMessageId' in data:
        _required_message_id(data['providerMessageId'])
    if 'requestId' in data:
        attachment_ledger._id(data['requestId'])
        data['edit'] = policy.validate_edit(data['edit'])
    if 'etag' in data and (type(data['etag']) is not str or not service.graph.plain._ETAG.fullmatch(data['etag'])):
        raise ValueError
    return data


def public_receipt(value):
    if (type(value) is not dict or set(value) != {'requestId', 'action', 'providerMessageId', 'outcome', 'blocked'}
            or value['action'] not in {'create', 'update'}
            or value['outcome'] not in {'confirmed_performed', 'confirmed_not_performed', 'uncertain'}
            or value['blocked'] is not (value['outcome'] == 'uncertain')):
        raise ValueError
    attachment_ledger._id(value['requestId'])
    if value['providerMessageId'] is not None:
        _required_message_id(value['providerMessageId'])
    elif value['action'] != 'create' or value['outcome'] == 'confirmed_performed':
        raise ValueError
    return value


def microsoft_compose_draft_response(request):
    if getattr(request, 'command', '') != 'POST':
        return error(405, 'microsoft_compose_invalid')
    if policy.blocked():
        return error(403, 'microsoft_compose_disabled')
    try:
        headers = snapshot_request_headers(request)
        require_microsoft_request_origin(headers)
        data = payload(request, headers)
    except HttpBoundaryError as e:
        return error(e.status, 'microsoft_compose_invalid')
    except OverflowError:
        return error(413, 'microsoft_compose_invalid')
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, MicrosoftGraphError, attachment_ledger.AttachmentLedgerError):
        return error(400, 'microsoft_compose_invalid')
    try:
        args = (headers, data['mailboxId'])
        if data['action'] == 'status':
            values = service.inspect_owned(*args)
            if type(values) is not list or len(values) > 32:
                raise ValueError
            values = [public_receipt(x) for x in values]
            if len({x['requestId'] for x in values}) != len(values):
                raise ValueError
            result = {'operations': values}
        elif data['action'] == 'load':
            value = service.load_owned(*args, data['providerMessageId'])
            if (type(value) is not dict or set(value) != {'providerMessageId', 'etag', 'edit', 'snapshotHash'}
                    or value['providerMessageId'] != data['providerMessageId']
                    or type(value['etag']) is not str or not service.graph.plain._ETAG.fullmatch(value['etag'])
                    or type(value['snapshotHash']) is not str or not re.fullmatch(r'[a-f0-9]{64}', value['snapshotHash'])):
                raise ValueError
            policy.validate_edit(value['edit'])
            result = value
        else:
            value = service.save_owned(*args, data['requestId'], data['edit'], **(
                {'message_id': data['providerMessageId'], 'etag': data['etag']} if data['action'] == 'update' else {}))
            public_receipt(value)
            if (value['requestId'] != data['requestId'] or value['action'] != data['action']
                    or data['action'] == 'update' and value['providerMessageId'] != data['providerMessageId']):
                raise ValueError
            result = {'receipt': value}
        return json_response(200, {'ok': True, 'mailboxId': data['mailboxId'], **result})
    except MicrosoftMailboxAccessError as e:
        return error(401 if e.code == 'microsoft_unauthorized' else 409, e.code)
    except (MicrosoftGraphError, attachment_ledger.AttachmentLedgerError) as e:
        return error(409, e.code)
    except Exception:
        return error(503, 'microsoft_compose_uncertain')
