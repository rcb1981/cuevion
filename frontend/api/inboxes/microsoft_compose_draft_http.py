"""Strict, disabled, same-origin draft-only HTTP boundary; no sending action."""
import json
import re
from api.auth.http import HttpBoundaryError, get_unique_header, json_response, snapshot_request_headers
from api.inboxes.microsoft_preview_origin import require_microsoft_request_origin
from api.inboxes.microsoft_graph import MicrosoftGraphError, _required_message_id
from api.inboxes import microsoft_compose_draft_policy as policy
from api.inboxes import microsoft_draft_acceptance as acceptance
from api.inboxes import microsoft_compose_draft_service as service
from api.inboxes import microsoft_response_drafts as responses
from api.inboxes import microsoft_draft_attachment_ledger as attachment_ledger
from api.inboxes.microsoft_mailbox_access import MicrosoftMailboxAccessError

MAX_REQUEST_BYTES = 256 * 1024
FIELDS = {
    'capabilities': {'action','mailboxId'},
    'prepare_response': {'action', 'mailboxId', 'mode', 'sourceProviderMessageId'},
    'create_response': {'action', 'mailboxId', 'mode', 'sourceProviderMessageId', 'sourceHash', 'requestId', 'edit'},
    'status': {'action', 'mailboxId'},
    'load': {'action', 'mailboxId', 'providerMessageId'},
    'create': {'action', 'mailboxId', 'requestId', 'edit'},
    'update': {'action', 'mailboxId', 'requestId', 'providerMessageId', 'etag', 'edit'},
}
MESSAGES = {
    'microsoft_acceptance_blocked': 'This draft test is not authorized for the current owner and Microsoft account.',
    'microsoft_acceptance_stopped': 'Microsoft draft testing is stopped or expired. Check status; do not repeat an uncertain action.',
    'microsoft_acceptance_unavailable': 'The draft test authorization cannot be verified. Writes remain blocked.',
    'microsoft_acceptance_limit': 'The authorized draft test operation limit has been reached.',

    'microsoft_compose_html_unsupported': 'This HTML formatting cannot be safely edited. The original draft is preserved; do not retry an uncertain save.',
    'microsoft_response_busy': 'Another mailbox operation is in progress. Check status before continuing.',
    'microsoft_response_blocked': 'An earlier draft or attachment operation needs verification. Do not repeat it.',
    'microsoft_response_disabled': 'Microsoft reply and forward drafts are not enabled.',
    'microsoft_response_invalid': 'Check the selected message and response details.',
    'microsoft_response_unsupported': 'This message cannot be safely used for a reply or forward draft.',
    'microsoft_response_conflict': 'The original message or protected quote changed. Reopen it before continuing.',
    'microsoft_response_uncertain': 'The response draft may have been saved. Check Drafts and status; do not repeat the operation.',
    'microsoft_response_store_unavailable': 'Secure response draft tracking is unavailable. No retry is allowed.',
    'microsoft_compose_body_unconfirmed': 'Microsoft may have saved this draft, but its original body cannot be verified as plain text. Do not save or send again. Review Drafts and check status.',
    'microsoft_compose_body_unverifiable': 'The original message body cannot be verified as plain text. HTML projections, unconfirmed preferences and unsupported MIME remain protected; saving or sending is blocked.',
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
            or data['action'] not in FIELDS
            or (not FIELDS[data['action']] <= set(data) or not set(data) <= FIELDS[data['action']] | ({'before','providerMessageId','requestId'} if data['action']=='status' else set()))
            or type(data.get('mailboxId')) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9:_-]{0,159}', data['mailboxId'])):
        raise ValueError
    if 'mode' in data and data['mode'] not in responses.MODES:
        raise ValueError
    if 'sourceProviderMessageId' in data:
        _required_message_id(data['sourceProviderMessageId'])
    if 'sourceHash' in data and (type(data['sourceHash']) is not str or not re.fullmatch('[a-f0-9]{64}',data['sourceHash'])):
        raise ValueError
    if 'providerMessageId' in data:
        _required_message_id(data['providerMessageId'])
    if 'requestId' in data:
        attachment_ledger._id(data['requestId'])
        if data['action']!='status':data['edit'] = policy.validate_edit(data['edit'])
    if 'before' in data and (type(data['before']) is not int or not 1<=data['before']<=service.ledger.MAX_SEQUENCE):raise ValueError
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


def public_response(value):
    if (type(value) is not dict or set(value) != {'mode','quotePreview'} or value['mode'] not in responses.MODES
            or type(value['quotePreview']) is not str or len(value['quotePreview'])>6000):
        raise ValueError
    return value


def microsoft_compose_draft_response(request):
    if getattr(request, 'command', '') != 'POST':
        return error(405, 'microsoft_compose_invalid')
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
        if data['action']=='capabilities':
            return json_response(200, {'ok':True, **acceptance.capabilities(*args)})
        if policy.blocked() and not acceptance.configured():
            return error(403,'microsoft_compose_disabled')
        if data['action'] == 'prepare_response':
            result = responses.prepare_owned(*args, data['sourceProviderMessageId'], data['mode'])
            if (type(result) is not dict or set(result) != {'sourceHash', 'edit', 'response'}
                    or type(result['sourceHash']) is not str or not re.fullmatch('[a-f0-9]{64}',result['sourceHash'])):
                raise ValueError
            policy.validate_edit(result['edit'])
            public_response(result['response'])
        elif data['action'] == 'create_response':
            value = responses.save_owned(*args, data['requestId'], data['edit'], source_id=data['sourceProviderMessageId'],
                mode=data['mode'], source_hash=data['sourceHash'])
            public_receipt(value)
            if value['requestId'] != data['requestId'] or value['action'] != 'create':raise ValueError
            result = {'receipt': value}
        elif data['action'] == 'status':
            result = service.status_owned(*args, before=data.get('before'), draft_id=data.get('providerMessageId'), request_id=data.get('requestId'))
            if type(result) is not dict or set(result)!={'operations','nextCursor'}:raise ValueError
            values=result['operations']
            if type(values) is not list or len(values)>97:raise ValueError
            result['operations']=[public_receipt(x) for x in values]
            if len({x['requestId'] for x in values})!=len(values):raise ValueError
            if result['nextCursor'] is not None and (type(result['nextCursor']) is not int or result['nextCursor']<1):raise ValueError
        elif data['action'] == 'load':
            value = service.load_owned(*args, data['providerMessageId'])
            if (type(value) is not dict or set(value) not in ({'providerMessageId', 'etag', 'edit', 'snapshotHash'}, {'providerMessageId', 'etag', 'edit', 'snapshotHash', 'response'})
                    or value['providerMessageId'] != data['providerMessageId']
                    or type(value['etag']) is not str or not service.graph.plain._ETAG.fullmatch(value['etag'])
                    or type(value['snapshotHash']) is not str or not re.fullmatch(r'[a-f0-9]{64}', value['snapshotHash'])):
                raise ValueError
            policy.validate_edit(value['edit'])
            if 'response' in value:public_response(value['response'])
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
