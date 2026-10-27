"""Permanent Send history in immutable, content-addressed radix-tree segments.

Three indexes (attempt, draft barrier, sequence) publish together in ONE root
CAS. No TTL, deletion, pruning or in-memory authority. Unpublished segments after
a crash are harmless and retained. Required missing/corrupt segments fail closed.
The original v1 index is archived verbatim before its root can be replaced.
"""
import copy
import json
import re
from datetime import datetime, timezone
from api.inboxes import microsoft_outbound_ledger as outbound
from api.inboxes import microsoft_draft_attachment_ledger as base
from api.inboxes.microsoft_draft_attachment_snapshot import digest, authority_digest
from api.inboxes.microsoft_graph import MicrosoftGraphError, _required_message_id

PREFIX = 'cuevion:microsoft:existing-draft-send:v1:'  # Keep the original root key.
SEGMENTS = 'cuevion:microsoft:send-history-segment:v2:'
PAGE_SIZE = 32
MAX_BYTES = 64 * 1024  # Per immutable segment, never a mailbox-history limit.
MAX_SEQUENCE = 9007199254740991
REASONS = {'microsoft_compose_body_unverifiable', 'microsoft_draft_send_invalid', 'microsoft_draft_send_conflict', 'microsoft_draft_send_blocked',
           'microsoft_reconnect_required', 'microsoft_send_permission_required', 'microsoft_write_permission_required',
           'microsoft_unauthorized', 'microsoft_draft_send_unavailable',
           'microsoft_draft_send_sender_unsupported', 'microsoft_draft_send_unverifiable'}


def fail(code='unavailable'):
    raise MicrosoftGraphError('microsoft_draft_send_' + code)


def context(authority):
    a = outbound._canonical_authority(authority)
    return outbound._storage(), PREFIX + digest([a.owner_email, a.mailbox_id]), authority_digest(a, generation=False)


def _hash(value):
    return type(value) is str and base.HASH.fullmatch(value) is not None


def _snapshot(config, key):
    try:
        value, raw, error = outbound.store._read_durable_record_snapshot(config, key)
        if (error or (value is None) != (raw is None)
                or raw is not None and (type(raw) is not str or len(raw.encode()) > MAX_BYTES
                    or type(value) is not dict or json.loads(raw) != value)):
            fail()
        return copy.deepcopy(value), raw
    except Exception:
        fail()


def validate_item(item):
    if (type(item) is not dict or set(item) != {'draftId', 'scope', 'binding', 'reason'}
            or not _hash(item['scope']) or not _hash(item['binding'])
            or item['reason'] is not None and item['reason'] not in REASONS):
        fail()
    _required_message_id(item['draftId'])


def _legacy(value, scope):
    if (type(value) is not dict or set(value) != {'v', 'scope', 'attempts'}
            or type(value['v']) is not int or value['v'] != 1 or value['scope'] != scope
            or type(value['attempts']) is not dict or len(value['attempts']) > 32):
        fail()
    for identifier, item in value['attempts'].items():
        base._id(identifier); validate_item(item)


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
        outbound.store._write_google_durable_record_if_unchanged(config, key, raw, value)
        # Concurrent identical immutable writes and lost ACKs are safe only when
        # the exact content-addressed segment is durably readable.
        if segment(authority, pointer) != value:
            fail()
    except Exception:
        fail()
    return pointer


def read_root(authority):
    config, key, scope = context(authority)
    value, raw = _snapshot(config, key)
    if value is None:
        # A permanent initialized marker detects a missing root after v2 use.
        marker, _ = _snapshot(config, key + ':initialized')
        if marker is not None:
            fail()
        return {'v': 1, 'scope': scope, 'attempts': {}}, None
    if value.get('v') == 1:
        _legacy(value, scope)
    elif (set(value) != {'v', 'scope', 'attempts', 'drafts', 'order', 'count', 'legacy'}
          or type(value['v']) is not int or value['v'] != 2 or value['scope'] != scope
          or type(value['count']) is not int or not 1 <= value['count'] <= MAX_SEQUENCE
          or any(not _hash(value[k]) for k in ('attempts', 'drafts', 'order'))
          or value['legacy'] is not None and not _hash(value['legacy'])):
        fail()
    elif value['legacy'] is not None:
        archive = segment(authority, value['legacy'])
        if set(archive) != {'v', 'scope', 'kind', 'original'} or archive['kind'] != 'legacy' or archive['v'] != 2:
            fail()
        _legacy(archive['original'], scope)
    if value['v'] == 2:
        marker, _ = _snapshot(config, key + ':initialized')
        if marker != {'v': 2, 'scope': scope}:
            fail()
    return value, raw


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


def validate_entry(entry, identifier=None):
    if (type(entry) is not dict or set(entry) != {'attemptId', 'item', 'sequence', 'createdAt'}
            or type(entry['sequence']) is not int or not 1 <= entry['sequence'] <= MAX_SEQUENCE):
        fail()
    base._id(entry['attemptId']); validate_item(entry['item'])
    if identifier is not None and entry['attemptId'] != identifier:
        fail()
    if entry['createdAt'] is not None:
        try:
            if type(entry['createdAt']) is not str or len(entry['createdAt']) > 64 or datetime.fromisoformat(entry['createdAt']).tzinfo is None:
                fail()
        except (ValueError, TypeError):
            fail()
    return entry


def entry(authority, identifier, root=None):
    base._id(identifier)
    root = root if root is not None else read_root(authority)[0]
    if root['v'] == 1:
        item = root['attempts'].get(identifier)
        if item is None:
            return None
        # v1 never stored creation order/time. Preserve it as explicitly unknown.
        return {'attemptId': identifier, 'item': item,
                'sequence': sorted(root['attempts']).index(identifier) + 1, 'createdAt': None}
    value = get(authority, root['attempts'], 'attempts', identifier)
    if value is None:
        return None
    result = validate_entry(value, identifier)
    if result['sequence'] > root['count']:
        fail()
    return result


def anchor(authority, draft_id, root):
    if root['v'] == 1:
        return [entry(authority, k, root) for k, v in root['attempts'].items() if v['draftId'] == draft_id]
    identifier = get(authority, root['drafts'], 'drafts', draft_id)
    if identifier is None:
        return []
    result = entry(authority, identifier, root)
    if result is None or result['item']['draftId'] != draft_id:
        fail()
    return [result]


def upgrade(authority, root, blocked):
    if root['v'] == 2:
        return copy.deepcopy(root)
    migrated = {'v': 2, 'scope': root['scope'], 'attempts': None, 'drafts': None,
                'order': None, 'count': 0, 'legacy': None}
    if root['attempts']:
        migrated['legacy'] = store_segment(authority, {'v': 2, 'scope': root['scope'], 'kind': 'legacy', 'original': root})
    anchors = {}
    for identifier in sorted(root['attempts']):
        value = entry(authority, identifier, root)
        migrated['attempts'] = put(authority, migrated['attempts'], 'attempts', identifier, value)
        migrated['order'] = put(authority, migrated['order'], 'order', value['sequence'], identifier)
        migrated['count'] += 1
        draft = value['item']['draftId']
        prior = anchors.get(draft)
        # Keep ANY historical blocker, even if malformed legacy ordering put a
        # later definite-no-start attempt after it. Never infer release by age.
        if prior is None or not blocked(prior):
            anchors[draft] = value
    for draft, value in anchors.items():
        migrated['drafts'] = put(authority, migrated['drafts'], 'drafts', draft, value['attemptId'])
    return migrated


def publish(authority, raw, root):
    config, key, scope = context(authority)
    # Persist the initialized marker before root publication; interruption of a
    # first initialization may block, but can never be mistaken for empty history.
    marker = {'v': 2, 'scope': scope}
    previous, marker_raw = _snapshot(config, key + ':initialized')
    if previous is not None and previous != marker:
        fail()
    if previous is None:
        outbound.store._write_google_durable_record_if_unchanged(config, key + ':initialized', marker_raw, marker)
        if _snapshot(config, key + ':initialized')[0] != marker:
            fail()
    try:
        stored, error = outbound.store._write_google_durable_record_if_unchanged(config, key, raw, root)
        # Unlike immutable segment writes, a lost root-claim ACK MUST NOT grant
        # execution to a worker. Replay observes the already published barrier.
        if error or stored != root or read_root(authority)[0] != root:
            fail()
    except Exception:
        fail()


def append(authority, root, raw, identifier, item, blocked):
    value = upgrade(authority, root, blocked)
    if value['count'] >= MAX_SEQUENCE:
        fail()
    sequence = value['count'] + 1
    new = {'attemptId': identifier, 'item': item, 'sequence': sequence,
           'createdAt': datetime.now(timezone.utc).isoformat()}
    value['attempts'] = put(authority, value['attempts'], 'attempts', identifier, new)
    value['drafts'] = put(authority, value['drafts'], 'drafts', item['draftId'], identifier)
    value['order'] = put(authority, value['order'], 'order', sequence, identifier)
    value['count'] = sequence
    publish(authority, raw, value)


def page(authority, before=None):
    root, _ = read_root(authority)
    count = len(root['attempts']) if root['v'] == 1 else root['count']
    if before is not None and (type(before) is not int or not 1 <= before <= count + 1):
        fail('invalid')
    end = count if before is None else before - 1
    start = max(1, end - PAGE_SIZE + 1)
    entries = []
    for seq in range(end, start - 1, -1):
        identifier = (sorted(root['attempts'])[seq - 1] if root['v'] == 1
                      else get(authority, root['order'], 'order', seq))
        result = entry(authority, identifier, root)
        if result is None or result['sequence'] != seq:
            fail()
        entries.append(result)
    return entries, start if start > 1 else None
