"""On-demand IMAP Sent reads only. No sends or sync ingestion; only the chosen folder preference is saved."""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parents[2]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

from api.inboxes.authenticated_gmail import read_json_body, send_json, error_payload, valid_identifier, send_method_not_allowed
from api.inboxes.authenticated_imap import resolve_authenticated_imap_mailbox
from api.user_config_store import resolve_owned_managed_inbox_record
from api.inboxes.imap_sent import SentError, discover_sent, quote_folder
from api.inboxes.imap_sent_snapshot import connection_scope, decode_cursor, read_page, ReadBudget, MAX_RESPONSE, MAX_TOKEN
from api.inboxes.imap_sent_preferences import preference_id, remembered_folder, remember_folder
from imap_connect_preview import connect_mailbox_with_settings


def resolve_context(headers, mailbox_id):
    owned = resolve_owned_managed_inbox_record(headers, mailbox_id, include_member_authority=True)
    resolved = resolve_authenticated_imap_mailbox(headers, mailbox_id)
    if resolved['status'] != 'ok':
        error = resolved['error']
        raise SentError(error['code'], error['message'], error['status_code'])
    if owned.get('status') != 'ok' or owned['user']['email'] != resolved['mailbox']['ownerEmail']:
        raise SentError('mailbox_connection_changed', 'The mailbox connection changed. Refresh Sent.', 409)
    scope, key = connection_scope(resolved['mailbox'], owned['inbox']['credentialVersion'])
    member = owned.get('memberAuthority')
    if member is None or member.email != resolved['mailbox']['ownerEmail']:
        raise SentError('mailbox_connection_changed', 'The mailbox connection changed. Refresh Sent.', 409)
    return resolved['mailbox'], scope, key, owned


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            self._handle_post()
        except SentError as error:
            send_json(self, error.status, error_payload(error.code, error.message))
        except Exception:
            send_json(self, 502, error_payload('imap_sent_unavailable', 'IMAP Sent could not be loaded. Try again.'))

    def _handle_post(self):
        deadline = time.monotonic() + 25
        payload, error = read_json_body(self, max_bytes=65536)
        if error:
            send_json(self, 413 if error['error']['code'] == 'request_too_large' else 400, error)
            return
        if (set(payload) - {'mailboxId', 'limit', 'pageToken', 'folderChoice'}
                or not valid_identifier(payload.get('mailboxId'))
                or type(payload.get('limit', 20)) is not int or not 1 <= payload.get('limit', 20) <= 20
                or ('pageToken' in payload and (type(payload['pageToken']) is not str or not 1 <= len(payload['pageToken']) <= MAX_TOKEN))):
            raise SentError('invalid_request', 'Invalid IMAP Sent request.', 400)
        choice = payload.get('folderChoice')
        if 'folderChoice' in payload:
            if type(choice) is not dict or set(choice) != {'providerFolder', 'scope'} or type(choice['scope']) is not str:
                raise SentError('invalid_request', 'Invalid Sent folder choice.', 400)
            quote_folder(choice['providerFolder'])
        mailbox_id = payload['mailboxId']
        resolved, scope, key, owned = resolve_context(self.headers, mailbox_id)
        if choice is not None:
            if choice['scope'] != scope:
                raise SentError('mailbox_connection_changed', 'Choose Sent again for the current connection.', 409)
            choice = choice['providerFolder']
        cursor = decode_cursor(payload['pageToken'], key, scope) if 'pageToken' in payload else None
        if cursor and choice is not None and cursor['folder'] != choice:
            raise SentError('invalid_page_token', 'Sent folder changed. Refresh Sent.', 409)
        mailbox = None
        try:
            imap = resolved['imap']
            mailbox = connect_mailbox_with_settings(host=imap['host'], port=imap['port'],
                username=imap['username'], password=imap['password'], ssl_enabled=imap['ssl'], timeout=5)
            budget = ReadBudget(mailbox, deadline)
            budget.check()
            discovery = discover_sent(mailbox, cursor['folder'] if cursor else choice,
                remembered=remembered_folder(owned, mailbox_id, scope), check=budget.check)
            page = (read_page(mailbox, mailbox_id, discovery['providerFolder'], scope, key,
                              payload.get('limit', 20), cursor, budget.check)
                    if discovery['mode'] == 'ready' else None)
            _, final_scope, _, final_owned = resolve_context(self.headers, mailbox_id)
            if final_scope != scope or preference_id(final_owned, mailbox_id) != preference_id(owned, mailbox_id):
                raise SentError('mailbox_connection_changed', 'The mailbox connection changed. Refresh Sent.', 409)
            budget.check()
            response = {'ok': True, 'mailboxId': mailbox_id, 'scope': scope, 'discovery': discovery, 'page': page}
        finally:
            if mailbox is not None:
                try:
                    mailbox.sock.settimeout(1)
                    mailbox.logout()
                except Exception:
                    try:
                        mailbox.shutdown()
                    except Exception:
                        pass

        # Release the IMAP connection before optional configuration I/O.
        if discovery['mode'] == 'ready' and cursor is None:
            try:
                response['preferenceSaved'] = remember_folder(owned, mailbox_id, scope, discovery['providerFolder'])
            except Exception:
                response['preferenceSaved'] = False
        if len(json.dumps(response).encode()) > MAX_RESPONSE:
            raise SentError('sent_response_too_large', 'The Sent page is too large to display safely.')
        send_json(self, 200, response)

    def do_GET(self):
        send_method_not_allowed(self, 'Use POST for IMAP Sent reads.', write_body=self.command != 'HEAD')

    do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = do_GET

    def log_message(self, *args):
        pass
