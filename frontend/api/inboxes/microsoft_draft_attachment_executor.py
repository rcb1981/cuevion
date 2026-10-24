"""Owner-bound small-file executor. All entry points are source-gated OFF.

One shared mailbox lease covers validation, durable request/child ledgers,
one Graph write per child and independent readback. No atomic attachment
If-Match guarantee against Outlook is assumed: any drift or missing proof stays blocked.
"""
import time

from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes import microsoft_draft_attachment_snapshot as snapshots
from api.inboxes import microsoft_draft_attachment_ledger as ledger
from api.inboxes import microsoft_draft_attachment_requests as requests
from api.inboxes import microsoft_draft_attachment_graph as graph
from api.inboxes import microsoft_draft_attachment_policy as policy
from api import user_config_store as config_store


def _assert_lease(authority, token, deadline):
    if time.monotonic() + 2 * graph.GRAPH_TIMEOUT_SECONDS + 5 >= deadline:
        ledger.fail('lease_unavailable')
    store, error = config_store.resolve_user_config_store()
    if error or not store:
        ledger.fail('lease_unavailable')
    status, current = config_store._read_mailbox_mutation_lease(store, authority.owner_email, authority.mailbox_id)
    if status != 'present' or current != token:
        ledger.fail('lease_unavailable')


def _fresh_token(headers, mailbox_id, captured):
    token, current = owned._owned_token(headers, mailbox_id, require_read_write=True)
    if current != captured:
        ledger.fail('mailbox_conflict')
    return token


def execute_owned_attachment_request(headers, mailbox_id, message_id, request_id, action,
                                       *, expected_fingerprint, files=None, attachment_id=None, opener=None):
    policy.require_release()
    ledger._id(request_id)
    ledger._required_message_id(message_id)
    if type(expected_fingerprint) is not str or not ledger.HASH.fullmatch(expected_fingerprint):
        ledger.fail('snapshot_invalid')
    if action == 'add' and attachment_id is None:
        checked = policy.validate_files(files)
    elif action == 'remove' and files is None:
        ledger._required_message_id(attachment_id)
        checked = [({'attachment_id': attachment_id}, {'action': 'remove', 'targetId': attachment_id, 'contentHash': None, 'fileHash': None})]
    else:
        ledger.fail('selection_invalid')
    captured = owned.resolve_owned_microsoft_mailbox(headers, mailbox_id)
    binding = snapshots.digest([snapshots.authority_digest(captured), message_id, expected_fingerprint,
                                action, [intent for _file, intent in checked]])
    replay = requests.lookup(captured, request_id, binding=binding, message_id=message_id)
    if replay is not None:
        owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
        return replay  # No token refresh, Graph read or write on a replay.
    lease_started = time.monotonic()
    lease = owned.acquire_mailbox_mutation_lease(captured.owner_email, captured.mailbox_id)
    if type(lease) is not dict or lease.get('status') != 'acquired' or type(lease.get('token')) is not str or not lease['token']:
        ledger.fail('lease_unavailable')
    deadline = lease_started + config_store.MAILBOX_MUTATION_LEASE_TTL_MILLISECONDS / 1000
    reserved, completed, child, may_have_started = None, 0, None, False
    try:
        from api.inboxes import microsoft_compose_draft_policy as compose_policy
        if not compose_policy.blocked():
            from api.inboxes import microsoft_compose_draft_ledger as compose_ledger
            compose_ledger.assert_unblocked(captured, message_id)
        token = _fresh_token(headers, mailbox_id, captured)
        current = snapshots.read_draft_attachment_snapshot(token, captured, message_id, opener=opener)
        owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
        if current.fingerprint != expected_fingerprint:
            ledger.fail('snapshot_conflict')
        for file, _intent in checked:
            ledger._intent(current, action, **file)
        requests.preflight_capacity(current, request_id, [intent for _file, intent in checked])
        reserved = requests.reserve(captured, request_id, message_id, binding, len(checked))
        if not reserved['new']:
            owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
            return {k: v for k, v in reserved.items() if k != 'new'}
        for index, (file, _intent) in enumerate(checked):
            child, may_have_started = None, False
            op_id = requests.child_id(request_id, index)
            child = ledger.reserve_attachment_operation(current, op_id, action,
                         expected_fingerprint=current.fingerprint, **file)
            if not child['new']:
                ledger.fail('operation_conflict')
            # A new complete observation, rather than a cached pre-batch version.
            token = _fresh_token(headers, mailbox_id, captured)
            fresh = snapshots.read_draft_attachment_snapshot(token, captured, message_id, opener=opener)
            if fresh.fingerprint != current.fingerprint:
                ledger.fail('snapshot_conflict')
            token = _fresh_token(headers, mailbox_id, captured)
            def before_write():
                nonlocal may_have_started
                _assert_lease(captured, lease['token'], deadline)
                owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
                may_have_started = True  # A lost CAS ACK may have persisted start.
                ledger.start_attachment_operation(fresh, op_id, child['claim'])
                owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
                _assert_lease(captured, lease['token'], deadline)
            result = graph.mutate_attachment(token, fresh, action, before_write=before_write, opener=opener, **file)
            after = snapshots.read_draft_attachment_snapshot(token, captured, message_id, opener=opener)
            owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
            if result.acknowledged:
                content = None
                if action == 'add':
                    # Only a new ID present in this complete inventory can be read.
                    if result.attachment_id not in {x.id for x in after.attachments}:
                        ledger.fail('outcome_uncertain')
                    content = graph.read_added_file_bytes(token, message_id, result.attachment_id, opener=opener)
                    verified = snapshots.read_draft_attachment_snapshot(token, captured, message_id, opener=opener)
                    if after.fingerprint != verified.fingerprint:
                        ledger.fail('outcome_uncertain')
                    after = verified
                owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
                receipt = ledger.reconcile_attachment_operation(after, op_id,
                    new_attachment_id=result.attachment_id, downloaded_content=content)
            else:
                # Missing/ambiguous ACK is never repaired by another mutation.
                receipt = ledger.confirm_provider_rejection(after, op_id, child['claim'], http_status=result.status)
            if receipt['outcome'] != 'confirmed_performed':
                outcome = 'confirmed_not_performed' if completed == 0 and receipt['outcome'] == 'confirmed_not_performed' else 'uncertain'
                status = requests.progress(captured, request_id, reserved['claim'], completed=completed, outcome=outcome)
                owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
                return status
            completed += 1
            current = after
            owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
            progress = requests.progress(captured, request_id, reserved['claim'], completed=completed,
                         outcome='confirmed_performed' if completed == len(checked) else 'uncertain')
        owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
        return progress
    except Exception:
        if reserved is None or not reserved.get('new'):
            raise
        # Before durable start we can prove this child made no provider call.
        # After a possibly persisted start, retain the uncertain barrier.
        definite = False
        if not may_have_started:
            if child is not None and child.get('new'):
                ledger.cancel_prepared_operation(captured, child['operationId'], child['claim'])
            definite = completed == 0
        status = requests.progress(captured, request_id, reserved['claim'], completed=completed,
                                   outcome='confirmed_not_performed' if definite else 'uncertain')
        owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
        return status
    finally:
        try:
            release = owned.release_mailbox_mutation_lease(captured.owner_email, captured.mailbox_id, lease['token'])
        except Exception:
            ledger.fail('lease_unavailable')
        if type(release) is not dict or release.get('status') != 'released':
            # Ledger remains authoritative; retry cannot execute the request again.
            ledger.fail('lease_unavailable')


def inspect_owned_attachment_request(headers, mailbox_id, message_id, request_id):
    policy.require_release()
    captured = owned.resolve_owned_microsoft_mailbox(headers, mailbox_id)
    result = requests.lookup(captured, request_id, message_id=message_id)
    owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    if result is None:
        ledger.fail('operation_missing')
    return result


def inspect_owned_draft_attachment_requests(headers, mailbox_id, message_id):
    policy.require_release()
    captured = owned.resolve_owned_microsoft_mailbox(headers, mailbox_id)
    result = requests.inspect_draft(captured, message_id)
    owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return result
