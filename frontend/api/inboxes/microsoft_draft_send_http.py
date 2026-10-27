"""Same-origin draft send/status boundary; shutdown preserves historical reads."""
import json
import re
from api.auth.http import HttpBoundaryError, get_unique_header, json_response, snapshot_request_headers
from api.inboxes.microsoft_preview_origin import require_microsoft_request_origin
from api.inboxes import microsoft_draft_send_policy as policy
from api.inboxes import microsoft_draft_send_service as service
from api.inboxes import microsoft_draft_send_index as index
from api.inboxes.microsoft_mailbox_access import MicrosoftMailboxAccessError
from api.inboxes.microsoft_graph import MicrosoftGraphError, _required_message_id
from api.inboxes.microsoft_outbound_ledger import OutboundLedgerError

FIELDS = {'history': {'action', 'mailboxId', 'before', 'providerMessageId'}, 'status': {'action', 'mailboxId'}, 'check': {'action', 'mailboxId', 'attemptId'},
          'send': {'action', 'mailboxId', 'attemptId', 'providerMessageId', 'etag', 'snapshotHash'}}
MESSAGES = {
    'microsoft_compose_body_unverifiable': 'The original message body cannot be verified as plain text. HTML projections, unconfirmed preferences and unsupported MIME remain protected; saving or sending is blocked.',
    'microsoft_draft_send_disabled': 'Sending existing Microsoft drafts is not enabled.',
    'microsoft_draft_send_invalid': 'Save valid recipients, a subject and message before sending.',
    'microsoft_draft_send_conflict': 'The saved draft changed. Reopen and review it before a new send request.',
    'microsoft_draft_send_blocked': 'An earlier send, draft or attachment operation requires verification. Do not send again.',
    'microsoft_draft_send_busy': 'Another mailbox action is in progress. Check status before continuing.',
    'microsoft_draft_send_unavailable': 'The send result could not be verified. Check status; do not send again.',
    'microsoft_send_permission_required': 'Reconnect Microsoft with Mail.Send authorization before sending.',
    'microsoft_write_permission_required': 'Reconnect Microsoft to allow draft access.',
    'microsoft_reconnect_required': 'Reconnect this Microsoft mailbox before continuing.',
    'microsoft_unauthorized': 'Sign in again before continuing.',
}


def error(status, code):
    code = code if code in MESSAGES else 'microsoft_draft_send_unavailable'
    return json_response(status, {'ok': False, 'outcome': 'uncertain', 'error': {'code': code, 'message': MESSAGES[code]}})


def payload(request, headers):
    if get_unique_header(headers, 'transfer-encoding') is not None:
        raise ValueError
    if get_unique_header(headers, 'content-type', required=True) not in {'application/json', 'application/json; charset=utf-8'}:
        raise ValueError
    length = get_unique_header(headers, 'content-length', required=True)
    if type(length) is not str or not re.fullmatch(r'[1-9][0-9]{0,3}', length):
        raise ValueError
    if int(length) > 4096:
        raise OverflowError
    raw = request.rfile.read(int(length))
    if type(raw) is not bytes or len(raw) != int(length):
        raise ValueError
    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError
            result[k] = v
        return result
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique)
    if (type(value) is not dict or type(value.get('action')) is not str or value['action'] not in FIELDS
            or set(value) != FIELDS[value['action']] or type(value.get('mailboxId')) is not str
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9:_-]{0,159}', value['mailboxId'])):
        raise ValueError
    if value['action'] == 'history':
        if value['before'] is not None and (type(value['before']) is not int or not 1 <= value['before'] <= index.history.MAX_SEQUENCE):
            raise ValueError
        if value['providerMessageId'] is not None:
            _required_message_id(value['providerMessageId'])
    if 'attemptId' in value:
        service.compose.base._id(value['attemptId'])
    if value['action'] == 'send':
        _required_message_id(value['providerMessageId'])
        if (type(value['etag']) is not str or not service.drafts.plain._ETAG.fullmatch(value['etag'])
                or type(value['snapshotHash']) is not str or not re.fullmatch(r'[a-f0-9]{64}', value['snapshotHash'])):
            raise ValueError
    return value


def public_receipt(value):
    fields = {'attemptId', 'providerMessageId', 'outcome', 'providerAccepted', 'sentCopyConfirmed', 'deliveryConfirmed', 'reason'}
    if (type(value) is not dict or set(value) != fields or value['outcome'] not in {'confirmed_performed','performed_content_mismatch','confirmed_not_started','uncertain'}
            or type(value['providerAccepted']) is not bool or value['deliveryConfirmed'] is not False
            or type(value['sentCopyConfirmed']) is not bool
            or value['outcome'] in {'confirmed_performed', 'performed_content_mismatch'} and not value['sentCopyConfirmed']
            or value['sentCopyConfirmed'] and not value['providerAccepted']
            or value['outcome'] == 'confirmed_not_started' and value['providerAccepted']
            or value['reason'] is not None and (value['outcome'] != 'confirmed_not_started' or value['reason'] not in index.REASONS)):
        raise ValueError
    service.compose.base._id(value['attemptId']); _required_message_id(value['providerMessageId'])
    return value


def microsoft_draft_send_response(request):
    if getattr(request,'command','') != 'POST':
        return error(405,'microsoft_draft_send_invalid')
    if policy.status_blocked():
        return error(403,'microsoft_draft_send_disabled')
    try:
        headers = snapshot_request_headers(request)
        require_microsoft_request_origin(headers)
        data = payload(request,headers)
    except HttpBoundaryError as e:
        return error(e.status,'microsoft_draft_send_invalid')
    except OverflowError:
        return error(413,'microsoft_draft_send_invalid')
    except Exception:
        return error(400,'microsoft_draft_send_invalid')
    if data['action'] == 'send' and policy.blocked():
        return error(403, 'microsoft_draft_send_disabled')
    try:
        args = (headers,data['mailboxId'])
        if data['action'] == 'history':
            result = service.history_owned(*args, before=data['before'], draft_id=data['providerMessageId'])
            if (type(result) is not dict or set(result) != {'entries', 'nextCursor', 'currentDraft'}
                    or type(result['entries']) is not list or len(result['entries']) > index.history.PAGE_SIZE):
                raise ValueError
            def history_entry(entry):
                if (type(entry) is not dict or set(entry) != {'receipt', 'sequence', 'createdAt'}
                        or type(entry['sequence']) is not int or not 1 <= entry['sequence'] <= index.history.MAX_SEQUENCE):
                    raise ValueError
                public_receipt(entry['receipt'])
                if entry['createdAt'] is not None:
                    from datetime import datetime
                    if type(entry['createdAt']) is not str or len(entry['createdAt']) > 64 or datetime.fromisoformat(entry['createdAt']).tzinfo is None:
                        raise ValueError
            for entry in result['entries']:
                history_entry(entry)
            sequences = [e['sequence'] for e in result['entries']]
            if (len({e['receipt']['attemptId'] for e in result['entries']}) != len(sequences)
                    or any(a - b != 1 for a, b in zip(sequences, sequences[1:]))
                    or sequences and data['before'] is not None and sequences[0] != data['before'] - 1
                    or result['nextCursor'] is not None and (type(result['nextCursor']) is not int
                        or not sequences or result['nextCursor'] != sequences[-1] or result['nextCursor'] <= 1)):
                raise ValueError
            expected_next = sequences[-1] if sequences and sequences[-1] > 1 else None
            if (result['nextCursor'] != expected_next
                    or expected_next is not None and len(sequences) != index.history.PAGE_SIZE
                    or not sequences and data['before'] not in {None, 1}):
                raise ValueError
            if result['currentDraft'] is not None:
                history_entry(result['currentDraft'])
                if result['currentDraft']['receipt']['providerMessageId'] != data['providerMessageId']:
                    raise ValueError
        elif data['action'] == 'status':
            values = service.inspect_owned(*args)
            if type(values) is not list or len(values) > index.MAX_ATTEMPTS:
                raise ValueError
            values = [public_receipt(x) for x in values]
            if len({x['attemptId'] for x in values}) != len(values):
                raise ValueError
            result = {'attempts':values}
        else:
            value = service.check_owned(*args,data['attemptId']) if data['action'] == 'check' else service.send_owned(
                *args,data['providerMessageId'],data['attemptId'],etag=data['etag'],snapshot_hash=data['snapshotHash'])
            public_receipt(value)
            if value['attemptId'] != data['attemptId'] or data['action'] == 'send' and value['providerMessageId'] != data['providerMessageId']:
                raise ValueError
            result = {'receipt':value}
        return json_response(200,{'ok':True,'mailboxId':data['mailboxId'],**result})
    except MicrosoftMailboxAccessError as e:
        return error(401 if e.code == 'microsoft_unauthorized' else 409,e.code)
    except (MicrosoftGraphError,OutboundLedgerError) as e:
        return error(409,e.code)
    except Exception:
        return error(503,'microsoft_draft_send_unavailable')
