"""Authenticated read-only status for one durable Microsoft outbound attempt.

No Graph send, draft creation, updates, or automatic retries are ever made.
A confirmed Sent copy is distinct from Graph's 202 and from actual delivery.
"""
from __future__ import annotations

from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes import microsoft_outbound_ledger as ledger
from api.inboxes import microsoft_sent_copy_proof as graph


class MicrosoftSentStatusError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def verify_owned_sent_copy(headers, mailbox_id, captured, draft_id):
    """Current credentials authorize only exact-ID GETs, never historical sends."""
    token, checked = owned._owned_token(headers, mailbox_id)
    if checked != captured:
        raise owned.MicrosoftMailboxAccessError("microsoft_reconnect_required")
    found = graph.verify_microsoft_sent_copy(token, draft_id)
    owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
    return found


def check_owned_microsoft_sent_status(
    headers, mailbox_id: str, *, attempt_id: str,
) -> dict:
    """Owner/generation-bound read-only proof, without resending."""
    if type(attempt_id) is not str or ledger.ATTEMPT_ID.fullmatch(attempt_id) is None:
        raise MicrosoftSentStatusError("microsoft_send_attempt_invalid")
    captured = owned.resolve_owned_microsoft_mailbox(headers, mailbox_id)
    try:
        record = ledger.inspect_outbound_attempt(captured, attempt_id)
    except ledger.OutboundLedgerError as error:
        raise MicrosoftSentStatusError(error.code) from None
    if record is None:
        raise MicrosoftSentStatusError("microsoft_send_attempt_not_found")

    phase = record["phase"]
    draft_id = record["draftId"]
    if phase == "sent_copy_confirmed":
        # Persisted only after the same immutable-ID/Sent-folder proof below.
        owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
        return {"attemptId": attempt_id, "status": "sent_copy_confirmed",
                "sentCopyConfirmed": True, "deliveryConfirmed": False}
    if phase not in {"send_started", "accepted_by_microsoft"}:
        owned._verify_unchanged_owned_mailbox(headers, mailbox_id, captured)
        return {
            "attemptId": attempt_id,
            "status": "send_not_started",
            "sentCopyConfirmed": False,
            "deliveryConfirmed": False,
        }
    if not isinstance(draft_id, str) or not draft_id:
        raise MicrosoftSentStatusError("microsoft_send_attempt_conflict")

    found = verify_owned_sent_copy(headers, mailbox_id, captured, draft_id)
    return {
        "attemptId": attempt_id,
        "status": "sent_copy_confirmed" if found else "sent_copy_pending",
        "sentCopyConfirmed": found,
        "deliveryConfirmed": False,
    }
