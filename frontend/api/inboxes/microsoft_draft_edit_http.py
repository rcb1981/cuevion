"""Fail-closed origin/owner-gated Drafts load and conditional update endpoint."""
from __future__ import annotations

import json
import re

from api.auth.http import HttpBoundaryError, get_unique_header, json_response, snapshot_request_headers
from api.inboxes.microsoft_preview_origin import require_microsoft_request_origin
from api.inboxes.microsoft_production_pilot import production_pilot_draft_edit_blocked
from api.inboxes.microsoft_graph import MicrosoftGraphError, _required_message_id
from api.inboxes import microsoft_draft_editor as graph, microsoft_rich_draft as rich
from api.inboxes import microsoft_mailbox_access as owned

MAX_REQUEST_BYTES = 150_000
_MAILBOX = re.compile(r'^[A-Za-z0-9][A-Za-z0-9:_-]{0,159}$')
_DESCRIPTIONS={
 'invalid_request':'Invalid Microsoft Drafts edit request.',
 'request_too_large':'Microsoft Draft edit is too large.',
 'method_not_allowed':'Use POST for Microsoft Drafts.',
 'microsoft_pilot_read_only':'Microsoft Drafts editing is not yet enabled.',
 'microsoft_draft_edit_invalid':'Check your draft subject, recipients and message.',
 'microsoft_draft_edit_unsupported':'This draft contains formatting or attachments that cannot safely be edited yet.',
 'microsoft_draft_edit_conflict':'The draft changed in Outlook. Reopen it before editing.',
 'microsoft_draft_edit_unconfirmed':'Save status is uncertain. Reopen Drafts before saving again.',
 'microsoft_unauthorized':'Your Cuevion session expired.',
 'microsoft_mailbox_not_found':'The Microsoft mailbox was not found.',
 'microsoft_reconnect_required':'Reconnect your Microsoft mailbox.',
 'microsoft_write_permission_required':'Reconnect Microsoft to allow draft editing.',
 'microsoft_mutation_in_progress':'Another mailbox operation is in progress.',
 'microsoft_graph_rate_limited':'Microsoft is temporarily limiting requests.',
}

def _error(status, code):
    return json_response(status,{'ok':False,'error':{'code':code,'message':_DESCRIPTIONS.get(code,'Microsoft Drafts are temporarily unavailable.')}},extra_headers=(('Allow','POST'),) if status==405 else ())

def _payload(request, headers):
    if get_unique_header(headers,'transfer-encoding') is not None: raise ValueError('invalid')
    if get_unique_header(headers,'content-type',required=True) not in ('application/json','application/json; charset=utf-8'):
        raise ValueError('invalid')
    length=get_unique_header(headers,'content-length',required=True)
    if type(length) is not str or not re.fullmatch(r'[1-9][0-9]{0,5}',length):raise ValueError('invalid')
    n=int(length)
    if n>MAX_REQUEST_BYTES:raise OverflowError
    raw=request.rfile.read(n)
    if type(raw) is not bytes or len(raw)!=n:raise ValueError('invalid')
    obj=json.loads(raw.decode('utf-8'))
    if type(obj) is not dict: raise ValueError('invalid')
    action=obj.get('action')
    fields=({'action','mailboxId','providerMessageId'} if action in ('load','load_rich') else
            {'action','mailboxId','providerMessageId','etag','edit'} if action in ('save','save_rich') else set())
    if not fields or set(obj)!=fields or type(obj.get('mailboxId')) is not str or not _MAILBOX.fullmatch(obj['mailboxId']):
        raise ValueError('invalid')
    _required_message_id(obj.get('providerMessageId'))
    if action in ('save','save_rich'):
        if type(obj.get('etag')) is not str or graph._ETAG.fullmatch(obj['etag']) is None:
            raise ValueError('invalid')
        (rich.validate_metadata if action=='save_rich' else graph.validate_edit)(obj.get('edit'))
    return obj

def microsoft_draft_edit_response(request):
    if getattr(request,'command','') != 'POST':return _error(405,'method_not_allowed')
    if production_pilot_draft_edit_blocked():
        # NO body read, session lookup, or Graph work in Production.
        return _error(403,'microsoft_pilot_read_only')
    try:
        headers=snapshot_request_headers(request)
        require_microsoft_request_origin(headers)
        obj=_payload(request, headers)
    except HttpBoundaryError as error:return _error(error.status,'invalid_request')
    except OverflowError:return _error(413,'request_too_large')
    except (ValueError,TypeError,KeyError,UnicodeError,json.JSONDecodeError,MicrosoftGraphError):
        return _error(400,'invalid_request')
    try:
        if obj['action']=='load':
            record=owned.read_owned_microsoft_editable_draft(
                headers,obj['mailboxId'],obj['providerMessageId'])
        elif obj['action']=='load_rich':
            record=owned.read_owned_microsoft_rich_draft_metadata(
                headers,obj['mailboxId'],obj['providerMessageId'])
        else:
            record=owned.save_owned_microsoft_editable_draft(
                headers,obj['mailboxId'],obj['providerMessageId'],etag=obj['etag'],edit=obj['edit'],
                **({'metadata_only':True} if obj['action']=='save_rich' else {}))
        if type(record) is not dict or record.get('providerMessageId')!=obj['providerMessageId'] or type(record.get('etag')) is not str:
            raise MicrosoftGraphError('microsoft_draft_edit_unconfirmed')
        return json_response(200,{'ok':True,**record})
    except owned.MicrosoftMailboxAccessError as error:
        code=error.code
        return _error(401 if code=='microsoft_unauthorized' else
                      404 if code=='microsoft_mailbox_not_found' else
                      403 if code=='microsoft_write_permission_required' else
                      409 if code in ('microsoft_reconnect_required','microsoft_mutation_in_progress') else
                      503,code)
    except MicrosoftGraphError as error:
        code=error.code
        return _error(400 if code=='microsoft_draft_edit_invalid' else
                      409 if code=='microsoft_draft_edit_conflict' else
                      422 if code=='microsoft_draft_edit_unsupported' else
                      429 if code=='microsoft_graph_rate_limited' else 503,code)
    except Exception:
        return _error(503,'microsoft_draft_edit_unconfirmed')
