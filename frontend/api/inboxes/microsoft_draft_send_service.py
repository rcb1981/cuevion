"""Send an existing exact draft once; never create/rewrite a message or retry.

Final snapshot checks detect known changes. Graph's send action has no proven
atomic If-Match contract: the interval after the final read remains a release
review limitation. A 202 alone is never classified as confirmed sent/delivered.
"""
import time
from api.inboxes import microsoft_local_test_policy as local_test
from api.inboxes import microsoft_draft_send_policy as policy
from api.inboxes import microsoft_draft_send_index as index
from api.inboxes import microsoft_draft_send_manifest as manifest
from api.inboxes import microsoft_outbound_ledger as outbound
from api.inboxes import microsoft_outbound_graph as transport
from api.inboxes import microsoft_sent_status_service as sent
from api.inboxes import microsoft_compose_draft_graph as drafts
from api.inboxes import microsoft_compose_draft_ledger as compose
from api.inboxes import microsoft_draft_attachment_requests as attachments
from api.inboxes import microsoft_draft_attachment_executor as executor
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes.microsoft_draft_attachment_snapshot import digest, authority_digest
from api.inboxes.microsoft_graph import MicrosoftGraphError, _required_message_id, _GRAPH_OPENER
from api import user_config_store as config_store


def assert_draft_settled(authority, draft_id):
    compose.assert_unblocked(authority, draft_id)
    if any(r['blocked'] for r in attachments.inspect_draft(authority, draft_id)):
        index.fail('blocked')
    _a, key, config = compose.base._context(authority)
    root, _raw = compose.base._read(config, key, authority_digest(authority, generation=False))
    if any(r['draftId'] == draft_id and r['phase'] != 'finished' for r in root['operations'].values()):
        index.fail('blocked')


def inspect_owned(headers, mailbox_id):
    policy.require_status()
    captured = owned.resolve_owned_microsoft_mailbox(headers, mailbox_id)
    local_test.authorize_status(headers, captured)
    root, _ = index.history.read_root(captured)
    if root['v'] == 2 and root['count'] > index.MAX_ATTEMPTS:
        index.fail('unavailable')  # Legacy clients must not receive truncated safety history.
    receipts = index.inspect(captured)
    owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return receipts


def history_owned(headers, mailbox_id, *, before=None, draft_id=None):
    policy.require_status()
    if draft_id is not None:
        _required_message_id(draft_id)
    captured = owned.resolve_owned_microsoft_mailbox(headers, mailbox_id)
    local_test.authorize_status(headers, captured)
    result = index.inspect_page(captured, before, draft_id)
    owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return result


def check_owned(headers, mailbox_id, attempt_id):
    policy.require_status()
    compose.base._id(attempt_id)
    captured = owned.resolve_owned_microsoft_mailbox(headers, mailbox_id)
    local_test.authorize_status(headers, captured)
    item = index.get_item(captured, attempt_id)
    if item is None:
        index.fail('unavailable')
    record = index.state(captured, attempt_id, item)
    if record and record['phase'] in {'send_started', 'accepted_by_microsoft', 'sent_copy_confirmed'}:
        evidence, _ = manifest.read(captured, attempt_id, item)
        # Historical identity-only receipts lack approved content. They remain
        # uncertain and blocked; never manufacture an approval after sending.
        if evidence is not None:
            if evidence['result'] == 'pending':
                token, checked = owned._owned_token(headers, mailbox_id)
                if checked != captured:
                    raise owned.MicrosoftMailboxAccessError('microsoft_reconnect_required')
                try:
                    manifest.verify_account(token, captured)
                    result = manifest.compare_sent(token, captured, item['draftId'], evidence['approved'])
                finally:
                    owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
                if result is not None:
                    manifest.settle(captured, attempt_id, item, result)
                    index.confirm_sent_copy(captured, attempt_id)
            elif record['phase'] != 'sent_copy_confirmed':
                # Recover a crash between the content-evidence CAS and the
                # identity-proof CAS. No Graph write or new send claim.
                owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
                index.confirm_sent_copy(captured, attempt_id)
    result = index.receipt(captured, attempt_id, item)
    owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return result


def send_owned(headers, mailbox_id, draft_id, attempt_id, *, etag, snapshot_hash, opener=None):
    policy.require_release()
    _required_message_id(draft_id); compose.base._id(attempt_id)
    if (type(etag) is not str or not drafts.plain._ETAG.fullmatch(etag)
            or type(snapshot_hash) is not str or not compose.base.HASH.fullmatch(snapshot_hash)):
        index.fail('invalid')
    captured = owned.resolve_owned_microsoft_mailbox(headers, mailbox_id)
    descriptor = {'mode': 'existing_draft', 'providerMessageId': draft_id, 'etag': etag, 'snapshotHash': snapshot_hash}
    binding = digest([authority_digest(captured), descriptor])
    permit = local_test.authorize_send(headers, captured, draft_id, attempt_id, binding)
    replay = index.lookup(captured, attempt_id, binding)
    if replay is not None:
        owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
        return replay  # No Graph writes/reads on any send replay.
    started = time.monotonic()
    lease = owned.acquire_mailbox_mutation_lease(captured.owner_email, captured.mailbox_id)
    if type(lease) is not dict or lease.get('status') != 'acquired' or type(lease.get('token')) is not str or not lease['token']:
        index.fail('busy')
    deadline = started + config_store.MAILBOX_MUTATION_LEASE_TTL_MILLISECONDS / 1000
    reserved = False
    started_possible = False
    try:
        if not index.reserve(captured, attempt_id, draft_id, binding):
            return index.lookup(captured, attempt_id, binding)
        permit.claim_once()
        reservation = outbound.reserve_outbound_attempt(captured, attempt_id, descriptor)
        if not reservation['new']:
            index.fail('blocked')
        reserved = True
        outbound.advance_outbound_attempt(captured, attempt_id, descriptor,
            from_phase='reserved', to_phase='draft_prepared', draft_id=draft_id)
        token, checked = owned._owned_token(headers, mailbox_id, require_read_write=True, require_mail_send=True)
        if checked != captured:
            raise MicrosoftGraphError('microsoft_reconnect_required')
        manifest.verify_account(token, captured, opener=opener)
        assert_draft_settled(captured, draft_id)
        current = drafts.read_current(token, captured, draft_id, opener=opener)
        policy.validate_send(current.edit)
        permit.content(current)
        if current.snapshot.etag != etag or current.snapshot.fingerprint != snapshot_hash:
            index.fail('conflict')
        approved = manifest.capture(token, captured, current, opener=opener)
        manifest.prepare(captured, attempt_id, approved)
        # Fresh complete observation after approval is durably stored. This is
        # not an atomic Graph send precondition: Outlook can still race it.
        final = drafts.read_current(token, captured, draft_id, opener=opener)
        if (final.snapshot.fingerprint != current.snapshot.fingerprint
                or manifest.capture(token, captured, final, opener=opener) != approved):
            index.fail('conflict')
        permit.content(final)
        assert_draft_settled(captured, draft_id)
        owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
        executor._assert_lease(captured, lease['token'], deadline)
        started_possible = True  # Lost start-CAS ACK must never be treated as no-send proof.
        outbound.advance_outbound_attempt(captured, attempt_id, descriptor,
            from_phase='draft_prepared', to_phase='send_started')
        owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
        executor._assert_lease(captured, lease['token'], deadline)
        # Fixed existing-draft action, no body/PATCH/upload/create and no redirects.
        permit.checkpoint()  # Shared emergency control checked immediately before transport.
        executor._assert_lease(captured, lease['token'], deadline)
        permit.check_deadline()
        transport.submit_prepared_microsoft_draft(token, draft_id, opener=opener or _GRAPH_OPENER.open)
        owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
        outbound.advance_outbound_attempt(captured, attempt_id, descriptor,
            from_phase='send_started', to_phase='accepted_by_microsoft')
    except Exception as error:
        if not reserved:
            raise
        if not started_possible:
            # Terminal CAS prevents this worker or any replay from ever sending.
            outbound._mark_existing_draft_attempt(captured, attempt_id, draft_id, 'send_not_started')
            index.reason(captured, attempt_id, getattr(error, 'code', 'microsoft_draft_send_unavailable'))
        owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
        # Even after a lost Graph ACK, a positive exact-ID Sent proof may settle
        # the attempt. A missing copy never permits a second send.
    finally:
        try:
            released = owned.release_mailbox_mutation_lease(captured.owner_email, captured.mailbox_id, lease['token'])
        except Exception:
            index.fail('unavailable')
        if type(released) is not dict or released.get('status') != 'released':
            index.fail('unavailable')
    try:
        return check_owned(headers, mailbox_id, attempt_id)
    except (sent.MicrosoftSentStatusError, MicrosoftGraphError, owned.MicrosoftMailboxAccessError, outbound.OutboundLedgerError):
        # Proof unavailable: return durable uncertainty, not another mutation.
        result = index.lookup(captured, attempt_id, binding)
        owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
        return result
