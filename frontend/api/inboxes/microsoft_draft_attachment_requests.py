"""Durable request barrier around Phase 2's per-file operation ledger.

A selection is immutable and never resumed automatically. Only the request
which durably created a claim may execute its children, while holding the
shared mailbox lease. Lost ACKs or process death leave a durable barrier.
No file names, content, addresses, tokens or claims are persisted in cleartext.
"""
import copy
import hashlib
import json
import secrets

from api.inboxes import microsoft_draft_attachment_ledger as ledger
from api.inboxes.microsoft_draft_attachment_snapshot import authority_digest, digest
from api.inboxes.microsoft_token_authority import _canonical_authority

PREFIX = 'cuevion:microsoft:draft-attachment-request:v1:'
MAX_REQUESTS = 32
MAX_BYTES = 64 * 1024


def child_id(request_id, index):
    import base64
    return base64.urlsafe_b64encode(hashlib.sha256((request_id + ':' + str(index)).encode()).digest()).decode().rstrip('=')


def _context(authority):
    a = _canonical_authority(authority)
    config = ledger.store._resolve_durable_store_config()
    if type(config) is not dict or not config:
        ledger.fail('store_unavailable')
    return config, PREFIX + digest([a.owner_email, a.mailbox_id]), authority_digest(a, generation=False)


def _read(config, key, scope):
    try:
        value, raw, error = ledger.store._read_durable_record_snapshot(config, key)
        if error or (value is None) != (raw is None):
            ledger.fail('store_unavailable')
        if value is None:
            return {'v': 1, 'scope': scope, 'requests': {}}, None
        if (type(raw) is not str or len(raw.encode()) > MAX_BYTES or json.loads(raw) != value
                or type(value) is not dict or set(value) != {'v', 'scope', 'requests'}
                or value['v'] != 1 or value['scope'] != scope or type(value['requests']) is not dict
                or len(value['requests']) > MAX_REQUESTS):
            ledger.fail('store_invalid')
        for identifier, item in value['requests'].items():
            ledger._id(identifier)
            fields = {'draftId', 'authority', 'binding', 'claim', 'count', 'completed', 'outcome', 'finished'}
            if (type(item) is not dict or set(item) != fields
                    or any(type(item[k]) is not str or not ledger.HASH.fullmatch(item[k]) for k in ('authority', 'binding', 'claim'))
                    or type(item['count']) is not int or not 1 <= item['count'] <= 10
                    or type(item['completed']) is not int or not 0 <= item['completed'] <= item['count']
                    or type(item['finished']) is not bool or item['outcome'] not in ledger.OUTCOMES
                    or (item['finished'] != (item['outcome'] != 'uncertain'))
                    or item['outcome'] == 'confirmed_performed' and item['completed'] != item['count']
                    or item['outcome'] == 'confirmed_not_performed' and item['completed'] != 0):
                ledger.fail('store_invalid')
            ledger._required_message_id(item['draftId'])
        return copy.deepcopy(value), raw
    except ledger.AttachmentLedgerError:
        raise
    except Exception:
        ledger.fail('store_unavailable')


def _write(config, key, raw, root):
    if len(json.dumps(root, ensure_ascii=True).encode()) > MAX_BYTES:
        ledger.fail('ledger_full')
    try:
        stored, error = ledger.store._write_google_durable_record_if_unchanged(config, key, raw, root)
        if error:
            ledger.fail('operation_conflict' if type(error) is dict and error.get('code') == 'gmail_token_write_conflict' else 'store_unavailable')
        if stored != root or _read(config, key, root['scope'])[0] != root:
            ledger.fail('store_unavailable')
    except ledger.AttachmentLedgerError:
        raise
    except Exception:
        ledger.fail('store_unavailable')


def receipt(request_id, item):
    return {'requestId': request_id, 'providerMessageId': item['draftId'], 'outcome': item['outcome'],
            'completed': item['completed'], 'count': item['count'], 'blocked': not item['finished']}


def lookup(authority, request_id, *, binding=None, message_id=None):
    ledger._id(request_id)
    config, key, scope = _context(authority)
    root, _raw = _read(config, key, scope)
    item = root['requests'].get(request_id)
    if item is None:
        return None
    if ((binding is not None and item['binding'] != binding)
            or message_id is not None and item['draftId'] != message_id):
        ledger.fail('operation_conflict')
    return receipt(request_id, item)


def reserve(authority, request_id, message_id, binding, count):
    ledger._id(request_id)
    config, key, scope = _context(authority)
    root, raw = _read(config, key, scope)
    existing = root['requests'].get(request_id)
    if existing is not None:
        if existing['binding'] != binding:
            ledger.fail('operation_conflict')
        return {**receipt(request_id, existing), 'new': False}
    if any(x['draftId'] == message_id and not x['finished'] for x in root['requests'].values()):
        ledger.fail('draft_blocked')
    if len(root['requests']) >= MAX_REQUESTS:
        ledger.fail('ledger_full')
    claim = secrets.token_urlsafe(32)
    item = {'draftId': message_id, 'authority': authority_digest(authority), 'binding': binding,
            'claim': digest(claim), 'count': count, 'completed': 0, 'outcome': 'uncertain', 'finished': False}
    root['requests'][request_id] = item
    _write(config, key, raw, root)
    return {**receipt(request_id, item), 'claim': claim, 'new': True}


def progress(authority, request_id, claim, *, completed, outcome='uncertain'):
    import hmac
    config, key, scope = _context(authority)
    root, raw = _read(config, key, scope)
    item = root['requests'].get(request_id)
    if (item is None or item['finished'] or item['authority'] != authority_digest(authority)
            or type(claim) is not str or not hmac.compare_digest(item['claim'], digest(claim))
            or type(completed) is not int or not item['completed'] <= completed <= item['count']
            or outcome not in ledger.OUTCOMES
            or outcome == 'confirmed_performed' and completed != item['count']
            or outcome == 'confirmed_not_performed' and completed != 0):
        ledger.fail('operation_conflict')
    item.update(completed=completed, outcome=outcome, finished=outcome != 'uncertain')
    _write(config, key, raw, root)
    return receipt(request_id, item)


def preflight_capacity(snapshot, request_id, intents):
    """Conservative upper bound before the FIRST write; never evict old IDs."""
    s = ledger._snapshot(snapshot)
    _a, key, config = ledger._context(s.authority)
    root, _raw = ledger._read(config, key, authority_digest(s.authority, generation=False))
    if any(x['draftId'] == s.provider_message_id and x['phase'] != 'finished' for x in root['operations'].values()):
        ledger.fail('draft_blocked')
    if len(root['operations']) + len(intents) > ledger.MAX_OPERATIONS:
        ledger.fail('ledger_full')
    if intents[0]['action'] == 'add' and len(s.attachments) + len(intents) > 100:
        ledger.fail('inventory_incomplete')
    before = {x.id: x.metadata_hash for x in s.attachments}
    for i, intent in enumerate(intents):
        identifier = child_id(request_id, i)
        if identifier in root['operations']:
            ledger.fail('operation_conflict')
        root['operations'][identifier] = {
            'binding': 'f'*64, 'authority': 'f'*64, 'draftId': s.provider_message_id,
            'snapshot': 'f'*64, 'version': 'f'*64, 'protected': 'f'*64,
            'before': dict(before), 'intent': intent, 'claim': 'f'*64,
            'phase': 'prepared', 'outcome': None,
        }
        if intent['action'] == 'add':
            # Future IDs can be 512 bytes; budget for them, not guessed names.
            before[str(i) + 'X'*510] = 'f'*64
    if len(json.dumps(root, ensure_ascii=True).encode()) > ledger.MAX_RECORD_BYTES:
        ledger.fail('ledger_full')


def inspect_draft(authority, message_id):
    """Read durable receipts after navigation/reload; never execute or resume work."""
    ledger._required_message_id(message_id)
    config, key, scope = _context(authority)
    root, _raw = _read(config, key, scope)
    return [receipt(identifier, item) for identifier, item in root['requests'].items()
            if item['draftId'] == message_id]
