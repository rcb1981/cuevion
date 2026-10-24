"""Bounded durable CAS barriers. No credentials, addresses, subject or body stored.

Reservations are retained without TTL/eviction. Unknown create results block
new creates for the mailbox; unknown updates block edits of that exact draft.
A replay returns a receipt and never executes Graph again, even after reconnect.
"""
import copy
import hmac
import json
import secrets
from api.inboxes import microsoft_draft_attachment_ledger as base
from api.inboxes.microsoft_draft_attachment_snapshot import digest, authority_digest
from api.inboxes.microsoft_token_authority import _canonical_authority
from api.inboxes.microsoft_graph import MicrosoftGraphError

PREFIX = 'cuevion:microsoft:compose-draft:v1:'
MAX_OPERATIONS = 32
MAX_BYTES = 64 * 1024
PHASES = {'reserved', 'started', 'confirmed_performed', 'confirmed_not_performed'}


class ComposeLedgerError(MicrosoftGraphError):
    pass


def fail(code):
    raise ComposeLedgerError('microsoft_compose_' + code)


def context(authority):
    a = _canonical_authority(authority)
    config = base.store._resolve_durable_store_config()
    if type(config) is not dict or not config:
        fail('store_unavailable')
    return config, PREFIX + digest([a.owner_email, a.mailbox_id]), authority_digest(a, generation=False)


def read(authority):
    config, key, scope = context(authority)
    try:
        value, raw, error = base.store._read_durable_record_snapshot(config, key)
        if error or (value is None) != (raw is None):
            fail('store_unavailable')
        if value is None:
            return {'v': 1, 'scope': scope, 'operations': {}}, None
        if (type(value) is not dict or type(raw) is not str or len(raw.encode()) > MAX_BYTES
                or json.loads(raw) != value or set(value) != {'v', 'scope', 'operations'}
                or type(value['v']) is not int or value['v'] != 1 or value['scope'] != scope or type(value['operations']) is not dict
                or len(value['operations']) > MAX_OPERATIONS):
            fail('store_invalid')
        for identifier, item in value['operations'].items():
            base._id(identifier)
            if (type(item) is not dict or set(item) != {'action', 'draftId', 'binding', 'authority', 'claim', 'phase'}
                    or item['action'] not in {'create', 'update'} or item['phase'] not in PHASES
                    or any(type(item[k]) is not str or not base.HASH.fullmatch(item[k]) for k in ('binding', 'authority', 'claim'))
                    or item['draftId'] is None and (item['action'] != 'create' or item['phase'] == 'confirmed_performed')):
                fail('store_invalid')
            if item['draftId'] is not None:
                base._required_message_id(item['draftId'])
        return copy.deepcopy(value), raw
    except (base.AttachmentLedgerError, ComposeLedgerError):
        raise
    except Exception:
        fail('store_unavailable')


def write(authority, raw, root):
    config, key, _scope = context(authority)
    if len(json.dumps(root, ensure_ascii=True).encode()) > MAX_BYTES:
        fail('ledger_full')
    try:
        stored, error = base.store._write_google_durable_record_if_unchanged(config, key, raw, root)
        if error or stored != root or read(authority)[0] != root:
            fail('store_unavailable')
    except (base.AttachmentLedgerError, ComposeLedgerError):
        raise
    except Exception:
        fail('store_unavailable')


def receipt(identifier, item):
    uncertain = item['phase'] in {'reserved', 'started'}
    return {'requestId': identifier, 'action': item['action'], 'providerMessageId': item['draftId'],
            'outcome': 'uncertain' if uncertain else item['phase'], 'blocked': uncertain}


def inspect(authority):
    root, _raw = read(authority)
    return [receipt(identifier, item) for identifier, item in root['operations'].items()]


def lookup(authority, identifier, binding):
    base._id(identifier)
    root, _raw = read(authority)
    item = root['operations'].get(identifier)
    if item is None:
        return None
    if item['binding'] != binding:
        fail('operation_conflict')
    return receipt(identifier, item)


def assert_unblocked(authority, draft_id):
    for value in inspect(authority):
        if value['blocked'] and (draft_id is None and value['action'] == 'create' or draft_id is not None and value['providerMessageId'] == draft_id):
            fail('operation_blocked')


def reserve(authority, identifier, action, draft_id, binding):
    base._id(identifier)
    if action not in {'create', 'update'} or action == 'create' and draft_id is not None:
        fail('operation_invalid')
    if action == 'update':
        base._required_message_id(draft_id)
    # Check identity and barriers against exactly the snapshot used for CAS.
    # A fresh read after checking barriers could otherwise overwrite a winner.
    root, raw = read(authority)
    existing = root['operations'].get(identifier)
    if existing is not None:
        if existing['binding'] != binding:
            fail('operation_conflict')
        return {**receipt(identifier, existing), 'new': False}
    for item in root['operations'].values():
        if item['phase'] in {'reserved', 'started'} and (
                draft_id is None and item['action'] == 'create'
                or draft_id is not None and item['draftId'] == draft_id):
            fail('operation_blocked')
    if len(root['operations']) >= MAX_OPERATIONS:
        fail('ledger_full')
    claim = secrets.token_urlsafe(32)
    root['operations'][identifier] = {'action': action, 'draftId': draft_id, 'binding': binding,
        'authority': authority_digest(authority), 'claim': digest(claim), 'phase': 'reserved'}
    write(authority, raw, root)
    return {**receipt(identifier, root['operations'][identifier]), 'new': True, 'claim': claim}


def advance(authority, identifier, claim, phase, *, draft_id=None):
    root, raw = read(authority)
    item = root['operations'].get(identifier)
    if (item is None or item['authority'] != authority_digest(authority)
            or type(claim) is not str or not hmac.compare_digest(item['claim'], digest(claim))
            or item['phase'] not in {'reserved', 'started'}
            or phase not in PHASES or phase == 'reserved'
            or item['phase'] == 'started' and phase == 'started' and draft_id is None):
        fail('operation_conflict')
    if draft_id is not None:
        base._required_message_id(draft_id)
        if item['draftId'] is not None and item['draftId'] != draft_id:
            fail('operation_conflict')
        item['draftId'] = draft_id
    if phase == 'confirmed_performed' and item['draftId'] is None:
        fail('operation_conflict')
    item['phase'] = phase
    write(authority, raw, root)
    return receipt(identifier, item)
