"""Owner/lease/generation/ledger-scoped draft-only service. Never call send APIs."""
import time
from api.inboxes import microsoft_compose_draft_policy as policy
from api.inboxes import microsoft_compose_draft_ledger as ledger
from api.inboxes import microsoft_compose_draft_graph as graph
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes import microsoft_draft_attachment_requests as attachment_requests
from api.inboxes import microsoft_draft_attachment_executor as attachment_executor
from api.inboxes.microsoft_draft_attachment_snapshot import digest, authority_digest
from api.inboxes.microsoft_graph import MicrosoftGraphError, _required_message_id
from api import user_config_store as config_store


def inspect_owned(headers, mailbox_id):
    policy.require_release()
    captured = owned.resolve_owned_microsoft_mailbox(headers, mailbox_id)
    receipts = ledger.inspect(captured)
    owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return receipts


def load_owned(headers, mailbox_id, message_id, *, opener=None):
    policy.require_release()
    token, captured = owned._owned_token(headers, mailbox_id)
    current = graph.read_current(token, captured, message_id, opener=opener)
    owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return current.public()


def save_owned(headers, mailbox_id, request_id, edit, *, message_id=None, etag=None, opener=None):
    policy.require_release()
    edit = policy.validate_edit(edit)
    ledger.base._id(request_id)
    if message_id is not None:
        _required_message_id(message_id)
        if type(etag) is not str or not graph.plain._ETAG.fullmatch(etag):
            raise MicrosoftGraphError('microsoft_compose_invalid')
    elif etag is not None:
        raise MicrosoftGraphError('microsoft_compose_invalid')
    captured = owned.resolve_owned_microsoft_mailbox(headers, mailbox_id)
    action = 'create' if message_id is None else 'update'
    binding = digest([authority_digest(captured), action, message_id, etag, edit])
    replay = ledger.lookup(captured, request_id, binding)
    if replay is not None:
        owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
        return replay
    started = time.monotonic()
    lease = owned.acquire_mailbox_mutation_lease(captured.owner_email, captured.mailbox_id)
    if type(lease) is not dict or lease.get('status') != 'acquired' or type(lease.get('token')) is not str or not lease['token']:
        raise MicrosoftGraphError('microsoft_compose_busy')
    deadline = started + config_store.MAILBOX_MUTATION_LEASE_TTL_MILLISECONDS / 1000
    reservation = None
    may_have_started = False
    try:
        token, checked = owned._owned_token(headers, mailbox_id, require_read_write=True)
        if checked != captured:
            raise MicrosoftGraphError('microsoft_reconnect_required')
        ledger.assert_unblocked(captured, message_id)
        before = None
        if message_id is not None:
            if any(item['blocked'] for item in attachment_requests.inspect_draft(captured, message_id)):
                raise MicrosoftGraphError('microsoft_compose_blocked')
            # Retain barriers from the earlier per-file ledger too, including
            # reservations made before the batch request adapter existed.
            _a, key, config = ledger.base._context(captured)
            child_root, _raw = ledger.base._read(config, key, authority_digest(captured, generation=False))
            if any(item['draftId'] == message_id and item['phase'] != 'finished'
                   for item in child_root['operations'].values()):
                raise MicrosoftGraphError('microsoft_compose_blocked')
            before = graph.read_current(token, captured, message_id, opener=opener)
            if before.snapshot.etag != etag:
                raise MicrosoftGraphError('microsoft_compose_conflict')
        owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
        reservation = ledger.reserve(captured, request_id, action, message_id, binding)
        if not reservation['new']:
            return {k: v for k, v in reservation.items() if k != 'new'}
        if before is not None and before.edit == edit:
            result = ledger.advance(captured, request_id, reservation['claim'], 'confirmed_performed')
            owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
            return result
        def before_write():
            nonlocal may_have_started
            attachment_executor._assert_lease(captured, lease['token'], deadline)
            owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
            may_have_started = True
            ledger.advance(captured, request_id, reservation['claim'], 'started')
            owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
            attachment_executor._assert_lease(captured, lease['token'], deadline)
        result = graph.write_once(token, edit, message_id=message_id, etag=etag, before_write=before_write, opener=opener)
        if not result.acknowledged:
            if result.rejected:
                value = ledger.advance(captured, request_id, reservation['claim'], 'confirmed_not_performed')
            else:
                value = next(x for x in ledger.inspect(captured) if x['requestId'] == request_id)
            owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
            return value
        if action == 'create':
            ledger.advance(captured, request_id, reservation['claim'], 'started', draft_id=result.draft_id)
        after = graph.read_current(token, captured, result.draft_id, opener=opener)
        if (after.edit != edit or before is None and after.snapshot.attachments
                or before is not None and (before.snapshot.etag == after.snapshot.etag
                    or before.snapshot.inventory_hash != after.snapshot.inventory_hash)):
            raise MicrosoftGraphError('microsoft_compose_uncertain')
        owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
        value = ledger.advance(captured, request_id, reservation['claim'], 'confirmed_performed')
        owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
        return value
    except Exception:
        if reservation is not None and reservation.get('new'):
            if not may_have_started:
                value = ledger.advance(captured, request_id, reservation['claim'], 'confirmed_not_performed')
            else:
                value = next(x for x in ledger.inspect(captured) if x['requestId'] == request_id)
            owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
            return value
        raise
    finally:
        try:
            released = owned.release_mailbox_mutation_lease(captured.owner_email, captured.mailbox_id, lease['token'])
        except Exception:
            raise MicrosoftGraphError('microsoft_compose_uncertain') from None
        if type(released) is not dict or released.get('status') != 'released':
            raise MicrosoftGraphError('microsoft_compose_uncertain')
