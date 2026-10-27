"""Durable mailbox/draft discovery and duplicate barrier around outbound v1.

The existing outbound ledger is the authority on execution and Sent evidence.
Segmented permanent indexes retain attempt IDs across reloads and reconnects. No TTL,
eviction, credentials, recipient addresses or attachment content are stored.
"""
from api.inboxes import microsoft_outbound_ledger as outbound
from api.inboxes import microsoft_draft_attachment_ledger as base
from api.inboxes.microsoft_draft_attachment_snapshot import digest, authority_digest
from api.inboxes.microsoft_graph import MicrosoftGraphError, _required_message_id

from api.inboxes import microsoft_draft_send_history as history

PREFIX = history.PREFIX
MAX_ATTEMPTS = history.PAGE_SIZE  # Legacy status response limit, NOT storage capacity.
MAX_BYTES = history.MAX_BYTES
REASONS = history.REASONS
fail = history.fail
context = history.context


def get_item(authority, identifier):
    entry = history.entry(authority, identifier)
    return entry['item'] if entry else None


def read(authority):
    """Compatibility/export view. Operational lookups use bounded tree paths."""
    root, raw = history.read_root(authority)
    if root['v'] == 1:
        return root, raw
    attempts, before = {}, None
    while True:
        entries, before = history.page(authority, before)
        attempts.update((e['attemptId'], e['item']) for e in entries)
        if before is None:
            return {'v': 1, 'scope': root['scope'], 'attempts': attempts}, raw


def _historical_snapshot(authority, attempt_id, item):
    # Never accept a scope supplied by the client: the current identity must
    # match the index root, and this exact item must still be in that index.
    if get_item(authority, attempt_id) != item:
        fail('unavailable')
    record, raw = outbound._read(outbound._storage(), outbound._key(authority, attempt_id))
    if record is None:
        return None, raw
    payload_hash = record.get('digest')
    if type(payload_hash) is not str or not base.HASH.fullmatch(payload_hash):
        fail('unavailable')
    record = outbound._valid_record(record, scope=item['scope'], digest=payload_hash, attempt_id=attempt_id)
    if record['draftId'] not in {None, item['draftId']}:
        fail('unavailable')
    return record, raw


def state(authority, attempt_id, item):
    record, _ = _historical_snapshot(authority, attempt_id, item)
    return None if record is None else {'phase': record['phase'], 'draftId': record['draftId']}


def confirm_sent_copy(authority, attempt_id):
    """Persist positive GET evidence only; cannot reserve, start or reopen a send."""
    item = get_item(authority, attempt_id)
    if item is None:
        fail('unavailable')
    record, raw = _historical_snapshot(authority, attempt_id, item)
    if record is not None and record['phase'] == 'sent_copy_confirmed':
        return
    if record is None or record['phase'] not in {'send_started', 'accepted_by_microsoft'}:
        fail('unavailable')
    try:
        outbound._write(outbound._storage(), outbound._key(authority, attempt_id), raw,
                        {**record, 'phase': 'sent_copy_confirmed', 'updatedAt': outbound._updated()})
    except outbound.OutboundLedgerError:
        # Concurrent positive probes or a lost write ACK can already have
        # persisted the same terminal evidence. Never downgrade or retry send.
        current = state(authority, attempt_id, item)
        if current is None or current['phase'] != 'sent_copy_confirmed':
            raise


def receipt(authority, attempt_id, item):
    record = state(authority, attempt_id, item)
    phase = record['phase'] if record else None
    outcome = 'confirmed_not_started' if phase == 'send_not_started' else 'uncertain'
    if phase == 'sent_copy_confirmed':
        from api.inboxes import microsoft_draft_send_manifest as manifest
        evidence, _ = manifest.read(authority, attempt_id, item)
        if evidence is not None:
            outcome = {'matched': 'confirmed_performed', 'mismatch': 'performed_content_mismatch',
                       'pending': 'uncertain'}[evidence['result']]
    return {'attemptId': attempt_id, 'providerMessageId': item['draftId'], 'outcome': outcome,
            'providerAccepted': phase in {'accepted_by_microsoft', 'sent_copy_confirmed'},
            'sentCopyConfirmed': phase == 'sent_copy_confirmed', 'deliveryConfirmed': False,
            'reason': item['reason'] if outcome == 'confirmed_not_started' else None}


def inspect(authority):
    root, _raw = read(authority)
    return [receipt(authority, identifier, item) for identifier, item in root['attempts'].items()]


def _blocked(authority, entry):
    return receipt(authority, entry['attemptId'], entry['item'])['outcome'] != 'confirmed_not_started'


def assert_mutable(authority, draft_id):
    root, _ = history.read_root(authority)
    if any(_blocked(authority, e) for e in history.anchor(authority, draft_id, root)):
        fail('blocked')


def lookup(authority, identifier, binding):
    item = get_item(authority, identifier)
    if item is None:
        return None
    if item['binding'] != binding:
        fail('conflict')
    return receipt(authority, identifier, item)


def reserve(authority, identifier, draft_id, binding):
    base._id(identifier); _required_message_id(draft_id)
    root, raw = history.read_root(authority)
    prior = history.entry(authority, identifier, root)
    if prior is not None:
        if prior['item']['binding'] != binding:
            fail('conflict')
        return False
    if any(_blocked(authority, e) for e in history.anchor(authority, draft_id, root)):
        fail('blocked')
    item = {'draftId': draft_id, 'scope': outbound._scope_hash(authority), 'binding': binding, 'reason': None}
    history.validate_item(item)
    history.append(authority, root, raw, identifier, item, lambda e: _blocked(authority, e))
    return True


def reason(authority, identifier, code):
    root, raw = history.read_root(authority)
    # New claims always initialize v2 before execution; never rewrite legacy data.
    if root['v'] != 2:
        fail('unavailable')
    entry = history.entry(authority, identifier, root)
    if entry is None:
        fail('unavailable')
    entry['item']['reason'] = code if code in REASONS else 'microsoft_draft_send_unavailable'
    root['attempts'] = history.put(authority, root['attempts'], 'attempts', identifier, entry)
    history.publish(authority, raw, root)


def inspect_page(authority, before=None, draft_id=None):
    entries, next_cursor = history.page(authority, before)
    def public(entry):
        return {'receipt': receipt(authority, entry['attemptId'], entry['item']),
                'sequence': entry['sequence'], 'createdAt': entry['createdAt']}
    current = None
    if draft_id is not None:
        root, _ = history.read_root(authority)
        anchors = history.anchor(authority, draft_id, root)
        if anchors:
            selected = next((e for e in anchors if _blocked(authority, e)), anchors[-1])
            current = public(selected)
    values = [public(e) for e in entries]
    if current is not None:
        current = next((e for e in values if e['receipt']['attemptId'] == current['receipt']['attemptId']), current)
    return {'entries': values, 'nextCursor': next_cursor, 'currentDraft': current}
