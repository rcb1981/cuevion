"""Owner-authorized, one-shot Microsoft Graph outbound orchestration.

A durable request ledger and per-mailbox lease encompass the entire
create/verify/send saga. No retry of Graph POST/PATCH is allowed after
ambiguous results. 202 means accepted by Graph, never delivered.
"""
from __future__ import annotations

from api.inboxes import microsoft_legacy_send_policy as policy
from api.inboxes import microsoft_outbound_graph as graph
from api.inboxes import microsoft_outbound_ledger as ledger
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes.microsoft_mailbox_access import MicrosoftMailboxAccessError
from api.user_config_store import acquire_mailbox_mutation_lease, release_mailbox_mutation_lease


class MicrosoftOutboundError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def send_owned_microsoft_message(
    headers, mailbox_id: object, *,
    attempt_id: str, compose: dict,
) -> dict:
    """One server-side operation per durable attempt ID, even on HTTP retries."""
    policy.require_release()
    # Validate everything except owner authority before acquiring a write lease.
    validated = graph._validated_compose(compose)
    if type(attempt_id) is not str or ledger.ATTEMPT_ID.fullmatch(attempt_id) is None:
        raise MicrosoftOutboundError("microsoft_send_attempt_invalid")

    captured = owned.resolve_owned_microsoft_mailbox(headers, mailbox_id)
    try:
        lease = acquire_mailbox_mutation_lease(captured.owner_email, captured.mailbox_id)
    except Exception:
        raise MicrosoftOutboundError("microsoft_mailbox_unavailable") from None
    if type(lease) is not dict or lease.get("status") != "acquired":
        if type(lease) is dict and lease.get("status") == "held":
            raise MicrosoftOutboundError("microsoft_mutation_in_progress")
        raise MicrosoftOutboundError("microsoft_mailbox_unavailable")
    lease_token = lease.get("token")
    if type(lease_token) is not str or not lease_token:
        raise MicrosoftOutboundError("microsoft_mailbox_unavailable")
    accepted = False
    result = None
    try:
        token, checked = owned._owned_token(
            headers, mailbox_id, require_read_write=True, require_mail_send=True,
        )
        if checked != captured:
            raise MicrosoftMailboxAccessError("microsoft_reconnect_required")

        # Reserve the irreversible request ID before touching Microsoft Graph.
        reservation = ledger.reserve_outbound_attempt(captured, attempt_id, validated)
        if not reservation["new"]:
            if reservation["phase"] == "accepted_by_microsoft":
                accepted = True
                result = {
                    "attemptId": attempt_id,
                    "status": "accepted_by_microsoft",
                    "deliveryConfirmed": False,
                    "sentCopyConfirmed": False,
                    "replayed": True,
                }
            else:
                raise MicrosoftOutboundError("microsoft_send_attempt_already_started")
        else:
            owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
            draft = graph.prepare_microsoft_draft(token, validated)
            ledger.advance_outbound_attempt(
                captured, attempt_id, validated,
                from_phase="reserved", to_phase="draft_prepared",
                draft_id=draft["draftId"],
            )
            owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
            # Mark this send irrevocably started BEFORE issuing Graph POST.
            ledger.advance_outbound_attempt(
                captured, attempt_id, validated,
                from_phase="draft_prepared", to_phase="send_started",
            )
            owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
            confirmation = graph.submit_prepared_microsoft_draft(token, draft["draftId"])
            if confirmation.get("status") != "accepted_by_microsoft":
                raise MicrosoftOutboundError("microsoft_send_unconfirmed")
            owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
            ledger.advance_outbound_attempt(
                captured, attempt_id, validated,
                from_phase="send_started", to_phase="accepted_by_microsoft",
            )
            accepted = True
            result = {
                "attemptId": attempt_id,
                "status": "accepted_by_microsoft",
                "deliveryConfirmed": False,
                "sentCopyConfirmed": False,
                "replayed": False,
            }
    except ledger.OutboundLedgerError as error:
        raise MicrosoftOutboundError(error.code) from None
    finally:
        released = False
        try:
            state = release_mailbox_mutation_lease(
                captured.owner_email, captured.mailbox_id, lease_token,
            )
            released = type(state) is dict and state.get("status") == "released"
        except Exception:
            pass
        if accepted and not released:
            raise MicrosoftOutboundError("microsoft_send_unconfirmed")
    assert result is not None
    return result
