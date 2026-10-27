"""Compose-only immutable radix segments, using the reviewed Send storage pattern.

Separate namespace; no Send code or data changed. No eviction, TTL or mutable
segment updates. Only the owning Compose root CAS publishes a new generation.
"""
import copy
import json
import re
from api.inboxes import microsoft_draft_attachment_ledger as base
from api.inboxes.microsoft_draft_attachment_snapshot import digest
SEGMENTS = 'cuevion:microsoft:compose-history-segment:v2:'
MAX_BYTES = 64 * 1024

def context(authority):
    from api.inboxes import microsoft_compose_draft_ledger as ledger
    return ledger.context(authority)

def fail():
    from api.inboxes import microsoft_compose_draft_ledger as ledger
    ledger.fail('store_unavailable')

def _hash(value):
    return type(value) is str and base.HASH.fullmatch(value) is not None


def _snapshot(config, key):
    try:
        value, raw, error = base.store._read_durable_record_snapshot(config, key)
        if (error or (value is None) != (raw is None)
                or raw is not None and (type(raw) is not str or len(raw.encode()) > MAX_BYTES
                    or type(value) is not dict or json.loads(raw) != value)):
            fail()
        return copy.deepcopy(value), raw
    except Exception:
        fail()


def _segment_key(scope, pointer):
    if not _hash(pointer):
        fail()
    return SEGMENTS + scope + ':' + pointer


def segment(authority, pointer):
    config, _, scope = context(authority)
    value, _ = _snapshot(config, _segment_key(scope, pointer))
    if value is None or digest(value) != pointer or value.get('scope') != scope:
        fail()
    return value


def store_segment(authority, value):
    config, _, scope = context(authority)
    pointer = digest(value)
    if value.get('scope') != scope or len(json.dumps(value, ensure_ascii=True).encode()) > MAX_BYTES:
        fail()
    key = _segment_key(scope, pointer)
    previous, raw = _snapshot(config, key)
    if previous is not None:
        if previous != value:
            fail()
        return pointer
    try:
        base.store._write_google_durable_record_if_unchanged(config, key, raw, value)
        # Concurrent identical immutable writes and lost ACKs are safe only when
        # the exact content-addressed segment is durably readable.
        if segment(authority, pointer) != value:
            fail()
    except Exception:
        fail()
    return pointer


def _node(authority, pointer, name, prefix):
    value = segment(authority, pointer)
    if (value.get('v') != 2 or type(value.get('v')) is not int or value.get('map') != name):
        fail()
    if value.get('kind') == 'leaf':
        if (set(value) != {'v', 'scope', 'map', 'kind', 'key', 'value'}
                or not _hash(value['key']) or not value['key'].startswith(prefix)):
            fail()
    elif value.get('kind') == 'branch':
        children = value.get('children')
        if (set(value) != {'v', 'scope', 'map', 'kind', 'children'} or len(prefix) >= 64
                or type(children) is not dict or not 1 <= len(children) <= 16
                or any(type(k) is not str or re.fullmatch('[0-9a-f]', k) is None or not _hash(v)
                       for k, v in children.items())):
            fail()
    else:
        fail()
    return value


def get(authority, pointer, name, key):
    hashed, prefix = digest(key), ''
    while pointer is not None:
        node = _node(authority, pointer, name, prefix)
        if node['kind'] == 'leaf':
            return node['value'] if node['key'] == hashed else None
        prefix += hashed[len(prefix)]
        pointer = node['children'].get(prefix[-1])
    return None


def put(authority, pointer, name, key, value):
    hashed = digest(key)
    _, _, scope = context(authority)
    common = {'v': 2, 'scope': scope, 'map': name}
    leaf = {**common, 'kind': 'leaf', 'key': hashed, 'value': value}
    def insert(ref, prefix):
        if ref is None:
            return store_segment(authority, leaf)
        node = _node(authority, ref, name, prefix)
        if node['kind'] == 'leaf':
            if node['key'] == hashed:
                return store_segment(authority, leaf)
            digit, old_digit = hashed[len(prefix)], node['key'][len(prefix)]
            children = ({digit: insert(ref, prefix + digit)} if digit == old_digit
                        else {old_digit: ref, digit: store_segment(authority, leaf)})
        else:
            digit = hashed[len(prefix)]
            children = {**node['children'], digit: insert(node['children'].get(digit), prefix + digit)}
        return store_segment(authority, {**common, 'kind': 'branch', 'children': children})
    return insert(pointer, '')
