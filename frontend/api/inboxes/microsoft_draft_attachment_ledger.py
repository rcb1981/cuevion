"""Durable attachment intent contract; deliberately contains NO Graph writer.

No HTTP route exposes this internal API. Callers must obtain fresh owner-checked
snapshots, never deserialise browser snapshots. Preparing/starting is not an
If-Match guarantee against Outlook. An executor is intentionally deferred.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import secrets
import unicodedata

from api.inboxes import oauth_token_store as store
from api.inboxes.microsoft_draft_attachment_snapshot import (
    DraftAttachment, DraftAttachmentSnapshot, authority_digest, digest,
)
from api.inboxes.microsoft_graph import _required_message_id
from api.inboxes.microsoft_token_authority import _canonical_authority

PREFIX = 'cuevion:microsoft:draft-attachment:v1:'
MAX_RECORD_BYTES = 64 * 1024
# No TTL or eviction: removing old IDs would reopen replay opportunities.
# Controlled next-stage use only; production needs a separately reviewed
# durable archival/partitioning policy before either bound is reached.
MAX_OPERATIONS = 32
SMALL_UPLOAD_BYTES = 2 * 1024 * 1024
ID = re.compile(r'^[A-Za-z0-9_-]{43}$')
HASH = re.compile(r'^[a-f0-9]{64}$')
OUTCOMES = frozenset({'confirmed_performed', 'confirmed_not_performed', 'uncertain'})


class AttachmentLedgerError(Exception):
    def __init__(self, code):
        self.code = 'microsoft_attachment_' + code
        super().__init__(self.code)


def fail(code):
    raise AttachmentLedgerError(code)


def new_operation_id():
    return secrets.token_urlsafe(32)


def _id(value):
    if type(value) is not str or not ID.fullmatch(value):
        fail('operation_invalid')
    return value


def _snapshot(value):
    if (type(value) is not DraftAttachmentSnapshot or value.inventory_complete is not True
            or value.is_draft is not True or type(value.fingerprint) is not str
            or not HASH.fullmatch(value.fingerprint) or type(value.attachments) is not tuple
            or any(type(x) is not DraftAttachment for x in value.attachments)
            or type(value.cid_links) is not tuple
            or any(type(link) is not tuple or len(link) != 2 or type(link[0]) is not str
                   or type(link[1]) is not tuple for link in value.cid_links)
            or len(value.attachments) > 100
            or len({x.id for x in value.attachments}) != len(value.attachments)
            or any(len(ids) != 1 for _cid, ids in value.cid_links)):
        fail('snapshot_invalid')
    if any(type(field) is not str or not HASH.fullmatch(field) for field in (
            value.body_hash, value.protected_content_hash, value.inventory_hash)):
        fail('snapshot_invalid')
    _canonical_authority(value.authority)
    _required_message_id(value.provider_message_id)
    if digest([(x.id, x.metadata_hash) for x in value.attachments]) != value.inventory_hash:
        fail('snapshot_invalid')
    return value


def _intent(snapshot, action, *, attachment_id=None, name=None, content_type=None, content=None):
    if action == 'remove':
        _required_message_id(attachment_id)
        if any(x is not None for x in (name, content_type, content)):
            fail('intent_invalid')
        targets = [x for x in snapshot.attachments if x.id == attachment_id]
        if (len(targets) != 1 or targets[0].kind != 'file' or targets[0].is_inline
                or targets[0].content_id or any(attachment_id in ids for _cid, ids in snapshot.cid_links)):
            fail('attachment_protected')
        return {'action': action, 'targetId': attachment_id, 'contentHash': None, 'fileHash': None}
    if action != 'add' or attachment_id is not None:
        fail('intent_invalid')
    if (type(name) is not str or not 1 <= len(name) <= 255
            or name in {'.', '..'} or any(unicodedata.category(c) in {'Cc', 'Cf', 'Cs'} or c in '/\\' for c in name)
            or any('\u202a' <= c <= '\u202e' or '\u2066' <= c <= '\u2069' for c in name)
            or type(content_type) is not str or not re.fullmatch(r'[a-z0-9][a-z0-9!#$&^_.+\-]*/[a-z0-9][a-z0-9!#$&^_.+\-]*', content_type)
            or len(content_type) > 128 or type(content) is not bytes
            or len(content) > SMALL_UPLOAD_BYTES or len(snapshot.attachments) >= 100):
        fail('intent_invalid')
    return {'action': action, 'targetId': None, 'contentHash': hashlib.sha256(content).hexdigest(),
            'fileHash': digest([name, content_type, len(content)])}


def _context(authority):
    a = _canonical_authority(authority)
    # Stable mailbox key survives reconnects: uncertain work must not disappear
    # when credential_generation or the account bound to a mailbox changes.
    key = PREFIX + digest([a.owner_email, a.mailbox_id])
    config = store._resolve_durable_store_config()
    if type(config) is not dict or not config:
        fail('store_unavailable')
    return a, key, config


def _read(config, key, scope):
    try:
        obj, raw, error = store._read_durable_record_snapshot(config, key)
        if error or (obj is None) != (raw is None):
            fail('store_unavailable')
        if obj is None:
            return {'v': 1, 'scope': scope, 'operations': {}}, raw
        if (type(raw) is not str or len(raw.encode()) > MAX_RECORD_BYTES
                or type(obj) is not dict or set(obj) != {'v', 'scope', 'operations'}
                or obj['v'] != 1 or obj['scope'] != scope or type(obj['operations']) is not dict
                or len(obj['operations']) > MAX_OPERATIONS):
            fail('store_invalid')
        if json.loads(raw) != obj:
            fail('store_invalid')
        for op_id, op in obj['operations'].items():
            _id(op_id)
            _validate_record(op)
        return copy.deepcopy(obj), raw
    except AttachmentLedgerError:
        raise
    except Exception:
        fail('store_unavailable')


def _validate_record(op):
    fields = {'binding', 'authority', 'draftId', 'snapshot', 'version', 'protected', 'before', 'intent', 'claim', 'phase', 'outcome'}
    if (type(op) is not dict or set(op) != fields
            or any(type(op[k]) is not str or not HASH.fullmatch(op[k]) for k in ('binding', 'authority', 'snapshot', 'version', 'protected', 'claim'))
            or type(op['before']) is not dict or len(op['before']) > 100
            or type(op['intent']) is not dict or set(op['intent']) != {'action', 'targetId', 'contentHash', 'fileHash'}
            or op['intent']['action'] not in {'add', 'remove'}
            or (op['phase'], op['outcome']) not in {
                ('prepared', None), ('started', 'uncertain'),
                ('finished', 'confirmed_performed'), ('finished', 'confirmed_not_performed')}):
        fail('store_invalid')
    _required_message_id(op['draftId'])
    for identifier, metadata_hash in op['before'].items():
        _required_message_id(identifier)
        if type(metadata_hash) is not str or not HASH.fullmatch(metadata_hash):
            fail('store_invalid')
    intent = op['intent']
    if op['binding'] != digest([op['authority'], op['draftId'], op['snapshot'], intent]):
        fail('store_invalid')
    if intent['action'] == 'remove':
        _required_message_id(intent['targetId'])
        if intent['targetId'] not in op['before'] or intent['contentHash'] is not None or intent['fileHash'] is not None:
            fail('store_invalid')
    elif intent['targetId'] is not None or any(type(intent[k]) is not str or not HASH.fullmatch(intent[k]) for k in ('contentHash', 'fileHash')):
        fail('store_invalid')


def _write(config, key, raw, value):
    if len(json.dumps(value, ensure_ascii=True).encode()) > MAX_RECORD_BYTES:
        fail('ledger_full')
    try:
        stored, error = store._write_google_durable_record_if_unchanged(config, key, raw, value)
        if error:
            fail('operation_conflict' if type(error) is dict and error.get('code') == 'gmail_token_write_conflict' else 'store_unavailable')
        if stored != value:
            fail('store_unavailable')
        actual, _ = _read(config, key, value['scope'])
        if actual != value:
            fail('store_unavailable')
    except AttachmentLedgerError:
        raise
    except Exception:
        fail('store_unavailable')


def _receipt(op_id, op):
    return {'operationId': op_id, 'providerMessageId': op['draftId'],
            'phase': op['phase'], 'outcome': op['outcome'],
            'blocked': op['phase'] != 'finished'}


def reserve_attachment_operation(snapshot, operation_id, action, *, expected_fingerprint, **file):
    """Persist intent and single-use claim. Replays never return another claim."""
    s = _snapshot(snapshot)
    _id(operation_id)
    if type(expected_fingerprint) is not str or expected_fingerprint != s.fingerprint:
        fail('snapshot_conflict')
    intent = _intent(s, action, **file)
    a, key, config = _context(s.authority)
    scope = authority_digest(a, generation=False)
    record, raw = _read(config, key, scope)
    binding = digest([authority_digest(a), s.provider_message_id, s.fingerprint, intent])
    existing = record['operations'].get(operation_id)
    if existing:
        if existing['binding'] != binding:
            fail('operation_conflict')
        return {**_receipt(operation_id, existing), 'new': False}
    if any(op['draftId'] == s.provider_message_id and op['phase'] != 'finished' for op in record['operations'].values()):
        fail('draft_blocked')
    if len(record['operations']) >= MAX_OPERATIONS:
        fail('ledger_full')
    claim = secrets.token_urlsafe(32)
    op = {'binding': binding, 'authority': authority_digest(a), 'draftId': s.provider_message_id,
          'snapshot': s.fingerprint, 'version': digest([s.etag, s.change_key]), 'protected': s.protected_content_hash,
          'before': {x.id: x.metadata_hash for x in s.attachments}, 'intent': intent,
          'claim': digest(claim), 'phase': 'prepared', 'outcome': None}
    record['operations'][operation_id] = op
    _write(config, key, raw, record)
    return {**_receipt(operation_id, op), 'new': True, 'claim': claim}


def _operation(authority, operation_id):
    _id(operation_id)
    a, key, config = _context(authority)
    root, raw = _read(config, key, authority_digest(a, generation=False))
    op = root['operations'].get(operation_id)
    if op is None:
        fail('operation_missing')
    return a, key, config, root, raw, op


def _claim(op, authority, claim):
    _id(claim)
    if op['authority'] != authority_digest(authority) or not hmac.compare_digest(op['claim'], digest(claim)):
        fail('operation_conflict')


def start_attachment_operation(snapshot, operation_id, claim):
    """CAS to uncertain BEFORE a future provider call. This does not call Graph.

    Exactly one invocation can succeed. A lost storage ACK never grants a claim
    to retry. Caller must freshly owner-read the complete snapshot immediately
    before calling this, under Cuevion's existing mailbox mutation lease.
    """
    s = _snapshot(snapshot)
    a, key, config, root, raw, op = _operation(s.authority, operation_id)
    _claim(op, a, claim)
    if op['phase'] != 'prepared' or op['snapshot'] != s.fingerprint or op['draftId'] != s.provider_message_id:
        fail('snapshot_conflict')
    op.update(phase='started', outcome='uncertain')
    _write(config, key, raw, root)
    return _receipt(operation_id, op)


def inspect_attachment_operation(authority, operation_id):
    # Reconnect may inspect old work, but cannot start it or bypass its barrier.
    _a, _key, _config, _root, _raw, op = _operation(authority, operation_id)
    return _receipt(operation_id, op)


def cancel_prepared_operation(authority, operation_id, claim):
    a, key, config, root, raw, op = _operation(authority, operation_id)
    _claim(op, a, claim)
    if op['phase'] != 'prepared':
        fail('operation_conflict')
    op.update(phase='finished', outcome='confirmed_not_performed')
    _write(config, key, raw, root)
    return _receipt(operation_id, op)


def confirm_provider_rejection(snapshot, operation_id, claim, *, http_status):
    """Trusted executor only: an actual definite Graph write rejection, not a
    read error, timeout, missing response, 5xx, or unchanged inventory. No such
    executor exists in Phase 2. Require an unchanged post-read as extra evidence.
    """
    s = _snapshot(snapshot)
    a, key, config, root, raw, op = _operation(s.authority, operation_id)
    _claim(op, a, claim)
    if (op['phase'] != 'started' or type(http_status) is not int
            or http_status not in {400, 401, 403, 404, 409, 412, 413, 415, 429}
            or s.fingerprint != op['snapshot']):
        fail('outcome_uncertain')
    op.update(phase='finished', outcome='confirmed_not_performed')
    _write(config, key, raw, root)
    return _receipt(operation_id, op)


def reconcile_attachment_operation(snapshot, operation_id, *, new_attachment_id=None, downloaded_content=None):
    """Read-only evidence from an independently owned snapshot. For add, require
    exact new provider ID AND bytes downloaded server-side from that ID. Filename
    or size alone can never confirm an operation. Never authorises another write.
    """
    s = _snapshot(snapshot)
    a, key, config, root, raw, op = _operation(s.authority, operation_id)
    if op['authority'] != authority_digest(a) or op['draftId'] != s.provider_message_id or op['phase'] != 'started':
        fail('operation_conflict')
    after = {x.id: x.metadata_hash for x in s.attachments}
    before, intent = op['before'], op['intent']
    preserved = op['protected'] == s.protected_content_hash and op['version'] != digest([s.etag, s.change_key])
    if intent['action'] == 'remove':
        expected = {k: v for k, v in before.items() if k != intent['targetId']}
        proven = preserved and after == expected and new_attachment_id is None and downloaded_content is None
    else:
        candidates = [x for x in s.attachments if x.id == new_attachment_id and x.id not in before]
        item = candidates[0] if len(candidates) == 1 else None
        proven = (preserved and item is not None and item.kind == 'file' and not item.is_inline and not item.content_id
                  and set(after) == set(before) | {item.id} and all(after.get(k) == v for k, v in before.items())
                  and type(downloaded_content) is bytes and len(downloaded_content) <= SMALL_UPLOAD_BYTES
                  and digest([item.name, item.content_type, len(downloaded_content)]) == intent['fileHash']
                  # Exact SHA-256 of independently fetched bytes is authoritative;
                  # Graph size metadata may include provider-specific overhead.
                  and hashlib.sha256(downloaded_content).hexdigest() == intent['contentHash'])
    if not proven:
        return _receipt(operation_id, op)  # uncertain remains durable and blocked
    op.update(phase='finished', outcome='confirmed_performed')
    _write(config, key, raw, root)
    return _receipt(operation_id, op)
