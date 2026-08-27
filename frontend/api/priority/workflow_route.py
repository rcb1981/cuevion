"""Dormant authenticated Priority workflow-authority contract.

This ledger is intentionally not a rendered Priority projection. Later slices
may migrate manual/removed, Done, and waiting writers (P2), hydrate these
authorities and retire browser-local membership authority (P3), establish a
canonical mailbox candidate projection (P4), and only then switch every
Priority surface to that one projection (P5).

The field-specific retention policy is approved only for the current
single-user/private-beta phase and must be re-reviewed before external testers
or any multi-user rollout.
"""

from __future__ import annotations

from dataclasses import dataclass

from .authority import (
    PriorityMessageIdentity,
    SemanticAuthorityError,
    parse_priority_message_identity,
    resolve_priority_authority,
)
from .event_reference import EventReferenceError, resolve_priority_hmac_secret
from .store import (
    WORKFLOW_MAX_BATCH_IDENTITIES,
    PriorityWorkflowScope,
    PriorityWorkflowStore,
    WorkflowStoreUnavailable,
    build_runtime_workflow_store,
)


WORKFLOW_READ_OPERATION = "read"
WORKFLOW_MANUAL_PRIORITY_OPERATION = "set_manual_priority"
WORKFLOW_CLEARED_OPERATION = "set_cleared"
WORKFLOW_WAITING_OPERATION = "set_waiting"


@dataclass(frozen=True, slots=True)
class WorkflowRouteResponse:
    status_code: int
    payload: dict


@dataclass(frozen=True, slots=True)
class _WorkflowRequest:
    operation: str
    mailbox_id: str
    identities: tuple[PriorityMessageIdentity, ...]
    field: str | None = None
    value: str | None = None


def _error(status: int, code: str, message: str) -> WorkflowRouteResponse:
    return WorkflowRouteResponse(
        status,
        {"ok": False, "error": {"code": code, "message": message}},
    )


def _authority_error(error: SemanticAuthorityError) -> WorkflowRouteResponse:
    messages = {
        "unauthorized": "A valid member session is required.",
        "invalid_mailbox_id": "Mailbox id is invalid.",
        "mailbox_not_found": "Mailbox connection was not found.",
        "unsupported_provider": "This mailbox provider is not supported.",
        "mailbox_not_ready": "The mailbox connection is not ready.",
    }
    public_code = error.code if error.code in messages else "workflow_authority_unavailable"
    return _error(
        error.status,
        public_code,
        messages.get(
            public_code,
            "Priority workflow authority is temporarily unavailable.",
        ),
    )


def _invalid_request(message: str = "Request is invalid.") -> WorkflowRouteResponse:
    return _error(400, "invalid_request", message)


def _parse_identity(value: object) -> PriorityMessageIdentity | None:
    try:
        return parse_priority_message_identity(value)
    except (TypeError, ValueError, UnicodeError):
        return None


def _validate_payload(payload: object) -> _WorkflowRequest | WorkflowRouteResponse:
    if type(payload) is not dict:
        return _invalid_request("Request body must be a JSON object.")
    operation = payload.get("operation")
    mailbox_id = payload.get("mailboxId")
    if type(mailbox_id) is not str:
        return _invalid_request("Mailbox id is invalid.")

    if operation == WORKFLOW_READ_OPERATION:
        if set(payload) != {"operation", "mailboxId", "identities"}:
            return _invalid_request("Request contains unsupported fields.")
        raw_identities = payload.get("identities")
        if (
            type(raw_identities) is not list
            or not 1 <= len(raw_identities) <= WORKFLOW_MAX_BATCH_IDENTITIES
        ):
            return _invalid_request("Message identities are invalid.")
        identities: list[PriorityMessageIdentity] = []
        canonical_identities: set[bytes] = set()
        for raw_identity in raw_identities:
            identity = _parse_identity(raw_identity)
            if identity is None:
                return _invalid_request("Message identity is invalid.")
            canonical = identity.canonical_bytes()
            if canonical in canonical_identities:
                return _invalid_request("Message identities must be unique.")
            canonical_identities.add(canonical)
            identities.append(identity)
        return _WorkflowRequest(operation, mailbox_id, tuple(identities))

    writes = {
        WORKFLOW_MANUAL_PRIORITY_OPERATION: (
            "manualPriority",
            frozenset({"none", "priority", "removed"}),
        ),
        WORKFLOW_CLEARED_OPERATION: (
            "cleared",
            frozenset({"active", "cleared"}),
        ),
        WORKFLOW_WAITING_OPERATION: (
            "waiting",
            frozenset({"absent", "waiting_on_other", "returned_reply"}),
        ),
    }
    write = writes.get(operation)
    if write is None:
        return _invalid_request("Priority workflow operation is invalid.")
    if set(payload) != {"operation", "mailboxId", "identity", "value"}:
        return _invalid_request("Request contains unsupported fields.")
    field, allowed_values = write
    identity = _parse_identity(payload.get("identity"))
    value = payload.get("value")
    if identity is None or type(value) is not str or value not in allowed_values:
        return _invalid_request("Priority workflow write is invalid.")
    return _WorkflowRequest(
        operation,
        mailbox_id,
        (identity,),
        field=field,
        value=value,
    )


def process_priority_workflow_request(
    headers,
    payload: object,
    *,
    hmac_secret: str | None = None,
    store: PriorityWorkflowStore | None = None,
) -> WorkflowRouteResponse:
    request = _validate_payload(payload)
    if isinstance(request, WorkflowRouteResponse):
        return request
    try:
        authority = resolve_priority_authority(headers, request.mailbox_id)
    except SemanticAuthorityError as error:
        return _authority_error(error)

    if any(identity.provider != authority.provider for identity in request.identities):
        return _error(
            400,
            "invalid_message_identity",
            "Message identity does not match this mailbox provider.",
        )
    scopes = tuple(
        PriorityWorkflowScope(
            workspace_id=authority.workspace_id,
            user_id=authority.user_id,
            mailbox_id=authority.mailbox_id,
            identity=identity,
        )
        for identity in request.identities
    )
    try:
        workflow_store = store
        if workflow_store is None:
            secret = hmac_secret or resolve_priority_hmac_secret()
            workflow_store = build_runtime_workflow_store(hmac_secret=secret)
        if request.operation == WORKFLOW_READ_OPERATION:
            records = workflow_store.read_records(scopes)
            return WorkflowRouteResponse(
                200,
                {
                    "ok": True,
                    "status": "hydrated",
                    "records": [
                        record.to_wire_dict(scope)
                        for scope, record in zip(scopes, records, strict=True)
                    ],
                },
            )
        if request.field is None or request.value is None:
            return _invalid_request("Priority workflow write is invalid.")
        record = workflow_store.write_field(
            scopes[0],
            field=request.field,
            value=request.value,
        )
        return WorkflowRouteResponse(
            200,
            {
                "ok": True,
                "status": "updated",
                "record": record.to_wire_dict(scopes[0]),
            },
        )
    except EventReferenceError:
        return _error(
            503,
            "workflow_storage_unavailable",
            "Priority workflow storage is temporarily unavailable.",
        )
    except WorkflowStoreUnavailable:
        return _error(
            503,
            "workflow_storage_unavailable",
            "Priority workflow storage is temporarily unavailable.",
        )
    except (TypeError, ValueError, OverflowError, UnicodeError, OSError):
        return _error(
            503,
            "workflow_storage_unavailable",
            "Priority workflow storage is temporarily unavailable.",
        )
