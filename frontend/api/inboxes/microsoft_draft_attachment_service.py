"""Owner/lease boundary for the future attachment executor. No HTTP route, UI,
upload session, POST, PATCH or DELETE is installed by this stage.
"""
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes import microsoft_draft_attachment_snapshot as snapshots
from api.inboxes import microsoft_draft_attachment_ledger as ledger


def _with_snapshot(headers, mailbox_id, message_id, callback, *, opener=None):
    captured = owned.resolve_owned_microsoft_mailbox(headers, mailbox_id)
    try:
        lease = owned.acquire_mailbox_mutation_lease(captured.owner_email, captured.mailbox_id)
    except Exception:
        ledger.fail('lease_unavailable')
    if type(lease) is not dict or lease.get('status') != 'acquired' or type(lease.get('token')) is not str or not lease['token']:
        ledger.fail('lease_unavailable')
    try:
        token, checked = owned._owned_token(headers, mailbox_id, require_read_write=True)
        if checked != captured:
            ledger.fail('mailbox_conflict')
        snapshot = snapshots.read_draft_attachment_snapshot(token, checked, message_id, opener=opener)
        owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
        result = callback(snapshot)
        # A reconnect racing the ledger CAS must not return an execution claim.
        owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
        return result
    finally:
        try:
            released = owned.release_mailbox_mutation_lease(captured.owner_email, captured.mailbox_id, lease['token'])
        except Exception:
            ledger.fail('lease_unavailable')
        if type(released) is not dict or released.get('status') != 'released':
            ledger.fail('lease_unavailable')


def prepare_owned_attachment_operation(headers, mailbox_id, message_id, operation_id, action,
                                        *, expected_fingerprint, opener=None, **file):
    return _with_snapshot(headers, mailbox_id, message_id, lambda fresh:
        ledger.reserve_attachment_operation(fresh, operation_id, action,
                                             expected_fingerprint=expected_fingerprint, **file), opener=opener)


def start_owned_attachment_operation(headers, mailbox_id, message_id, operation_id, claim, *, opener=None):
    """Reserved for future trusted executor. Does not perform the operation.
    A future executor must retain its mailbox lease across start, one provider
    call, and readback; this Phase 2 helper intentionally stops before execution.
    """
    return _with_snapshot(headers, mailbox_id, message_id,
                          lambda fresh: ledger.start_attachment_operation(fresh, operation_id, claim), opener=opener)
