"""Authenticated semantic-assessment and cache-lookup orchestration."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .authority import (
    AuthorizedSemanticSource,
    PriorityAuthority,
    SemanticAuthorityError,
    load_authorized_gmail_incoming,
    load_authorized_gmail_new_inbound,
    load_authorized_imap_incoming,
    load_authorized_imap_new_inbound,
    prove_authorized_new_inbound_source_current,
    prove_authorized_source_current,
    resolve_priority_authority,
    verify_claim_scope,
)
from .event_reference import (
    MAX_AUTHORED_TEXT_CHARACTERS,
    EventReferenceError,
    OutgoingEventClaims,
    authored_text_matches,
    canonicalize_authored_text,
    resolve_priority_hmac_secret,
    verify_outgoing_event_reference,
)
from .openai_responses_adapter import build_openai_semantic_adapter
from .semantic_config import (
    SemanticRuntimeConfig,
    load_semantic_runtime_config,
)
from .semantic_core import assess_semantic_conversation
from .semantic_errors import SemanticCoreError, SemanticInputError
from .semantic_text import build_semantic_text_window
from .semantic_thresholds import (
    confidence_threshold_for,
    evaluate_semantic_confidence,
)
from .semantic_types import (
    SemanticAssessment,
    SemanticAssessmentRequest,
    SemanticState,
    SemanticTurn,
    SpeakerRole,
    TurnDirection,
)
from .store import (
    LEASE_TTL_SECONDS,
    NEGATIVE_TTL_SECONDS,
    CachedSemanticAssessment,
    SemanticAssessmentStore,
    SemanticCacheScope,
    SemanticStoreUnavailable,
    build_runtime_semantic_store,
)


MAX_ROUTE_AUTHORED_TEXT_CHARACTERS = MAX_AUTHORED_TEXT_CHARACTERS
SEMANTIC_LOOKUP_OPERATION = "lookup_current"
SEMANTIC_NEW_INBOUND_TRIGGER = "new_inbound"
PRIORITY_EFFECT_OBSERVE_ONLY = "observe_only"
PRIORITY_EFFECT_SUPPRESS_AUTOMATIC_OPEN_LOOP = (
    "suppress_automatic_open_loop"
)


@dataclass(frozen=True, slots=True)
class SemanticRouteResponse:
    status_code: int
    payload: dict
    retry_after: int | None = None


def _error(status: int, code: str, message: str) -> SemanticRouteResponse:
    return SemanticRouteResponse(
        status,
        {"ok": False, "error": {"code": code, "message": message}},
    )


def _authority_error(error: SemanticAuthorityError) -> SemanticRouteResponse:
    messages = {
        "unauthorized": "A valid member session is required.",
        "invalid_mailbox_id": "Mailbox id is invalid.",
        "mailbox_not_found": "Mailbox connection was not found.",
        "unsupported_provider": "This mailbox provider is not supported.",
        "mailbox_not_ready": "The mailbox connection is not ready.",
        "event_scope_mismatch": "The semantic event does not belong to this mailbox.",
        "provider_mismatch": "The incoming locator does not match this mailbox provider.",
        "invalid_incoming_locator": "The incoming message locator is invalid.",
        "incoming_message_not_found": "The incoming message was not found.",
        "incoming_message_stale": "The incoming message is not newer than the active event.",
        "incoming_message_not_external": "The incoming message is not from an external sender.",
        "incoming_message_not_in_inbox": "The incoming message is not in the Inbox.",
        "incoming_message_noise_excluded": "The incoming message is not eligible for semantic assessment.",
        "incoming_message_routing_excluded": "The incoming message is not eligible for semantic assessment.",
        "incoming_message_identity_unconfirmed": "The incoming message identity could not be confirmed.",
        "conversation_mismatch": "The incoming message belongs to another conversation.",
        "incoming_message_text_unavailable": "No bounded message text is available for assessment.",
        "reconnect_required": "Reconnect this mailbox to continue.",
    }
    public_code = error.code if error.code in messages else "semantic_authority_unavailable"
    return _error(
        error.status,
        public_code,
        messages.get(public_code, "Semantic message authority is temporarily unavailable."),
    )


def _validate_payload_shape(
    payload: object,
) -> tuple[str, str, bool] | SemanticRouteResponse:
    if type(payload) is not dict:
        return _error(400, "invalid_request", "Request body must be a JSON object.")
    operation = payload.get("operation")
    lookup_current = operation == SEMANTIC_LOOKUP_OPERATION
    if "operation" in payload and not lookup_current:
        return _error(400, "invalid_request", "Semantic operation is invalid.")
    trigger = payload.get("trigger")
    if trigger == "outgoing_reply":
        expected_fields = (
            {"mailboxId", "operation", "trigger", "eventRef"}
            if lookup_current
            else {"mailboxId", "trigger", "eventRef", "authoredText"}
        )
        if set(payload) != expected_fields:
            return _error(400, "invalid_request", "Request contains unsupported fields.")
        if (
            type(payload.get("eventRef")) is not str
            or (
                not lookup_current
                and (
                    type(payload.get("authoredText")) is not str
                    or not canonicalize_authored_text(payload["authoredText"])
                    or len(payload["authoredText"])
                    > MAX_ROUTE_AUTHORED_TEXT_CHARACTERS
                )
            )
        ):
            return _error(400, "invalid_request", "Outgoing semantic input is invalid.")
    elif trigger in {"incoming_reply", SEMANTIC_NEW_INBOUND_TRIGGER}:
        if trigger == SEMANTIC_NEW_INBOUND_TRIGGER and lookup_current:
            return _error(400, "invalid_request", "Semantic operation is invalid.")
        locator = payload.get("incomingLocator")
        if type(locator) is not dict:
            return _error(400, "invalid_request", "Incoming locator is invalid.")
        provider = locator.get("provider")
        if provider == "google":
            expected_fields = {"mailboxId", "trigger", "incomingLocator"}
            if trigger == "incoming_reply":
                expected_fields.add("activeEventRef")
            if lookup_current:
                expected_fields.add("operation")
            if (
                set(payload) != expected_fields
                or (
                    trigger == "incoming_reply"
                    and type(payload.get("activeEventRef")) is not str
                )
                or set(locator) != {"provider", "providerMessageId"}
            ):
                return _error(400, "invalid_request", "Incoming locator is invalid.")
        elif provider == "custom_imap":
            expected_fields = {"mailboxId", "trigger", "incomingLocator"}
            if lookup_current:
                expected_fields.add("operation")
            if (
                set(payload) != expected_fields
                or set(locator)
                != {
                    "provider",
                    "providerFolder",
                    "uidValidity",
                    "imapUid",
                }
            ):
                return _error(400, "invalid_request", "Incoming locator is invalid.")
        else:
            return _error(400, "invalid_request", "Incoming locator provider is invalid.")
    else:
        return _error(400, "invalid_request", "Semantic trigger is invalid.")
    mailbox_id = payload.get("mailboxId")
    if type(mailbox_id) is not str:
        return _error(400, "invalid_request", "Mailbox id is invalid.")
    return trigger, mailbox_id, lookup_current


def _verify_event(
    reference: str,
    *,
    hmac_secret: str,
    now: int,
) -> OutgoingEventClaims | SemanticRouteResponse:
    try:
        return verify_outgoing_event_reference(
            reference,
            secret=hmac_secret,
            now=now,
        )
    except EventReferenceError as error:
        code = "stale_event_ref" if error.code == "stale_event_ref" else "invalid_event_ref"
        message = (
            "The active semantic event has expired."
            if code == "stale_event_ref"
            else "The semantic event reference is invalid."
        )
        return _error(409 if code == "stale_event_ref" else 400, code, message)


def _outgoing_source(
    authority: PriorityAuthority,
    claims: OutgoingEventClaims,
    authored_text: str,
) -> AuthorizedSemanticSource:
    timestamp = datetime.fromtimestamp(
        claims.occurred_at / 1_000,
        tz=timezone.utc,
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return AuthorizedSemanticSource(
        authority=authority,
        conversation_id=claims.conversation_id,
        provider_conversation_id=claims.provider_conversation_id,
        latest_turn_id=claims.latest_turn_id,
        occurred_at=claims.occurred_at,
        turns=(
            {
                "turnId": claims.latest_turn_id,
                "speaker": "user",
                "direction": "outgoing",
                "text": canonicalize_authored_text(authored_text),
                "timestamp": timestamp,
            },
        ),
        revalidation_locator={
            "provider": "google",
            "providerMessageId": claims.latest_turn_id,
        },
    )


def _outgoing_lookup_source(
    authority: PriorityAuthority,
    claims: OutgoingEventClaims,
) -> AuthorizedSemanticSource:
    """Reconstruct only the signed identity needed for a cache-only lookup."""
    return AuthorizedSemanticSource(
        authority=authority,
        conversation_id=claims.conversation_id,
        provider_conversation_id=claims.provider_conversation_id,
        latest_turn_id=claims.latest_turn_id,
        occurred_at=claims.occurred_at,
        turns=(),
        revalidation_locator={
            "provider": "google",
            "providerMessageId": claims.latest_turn_id,
        },
    )


def _typed_turns(source: AuthorizedSemanticSource) -> tuple[SemanticTurn, ...]:
    turns: list[SemanticTurn] = []
    for turn in source.turns:
        speaker = turn.get("speaker")
        direction = turn.get("direction")
        if speaker not in {"user", "external"} or direction not in {
            "incoming",
            "outgoing",
        }:
            raise SemanticInputError("Authorized semantic turn metadata is invalid.")
        turns.append(
            SemanticTurn(
                turn_id=turn["turnId"],
                speaker=(
                    SpeakerRole.USER
                    if speaker == "user"
                    else SpeakerRole.EXTERNAL
                ),
                direction=(
                    TurnDirection.OUTGOING
                    if direction == "outgoing"
                    else TurnDirection.INCOMING
                ),
                text=turn["text"],
                timestamp=turn.get("timestamp"),
            )
        )
    return tuple(turns)


def _input_hash(turns: tuple[SemanticTurn, ...]) -> tuple[str, str]:
    window = build_semantic_text_window(turns)
    encoded = json.dumps(
        {"turns": window.to_model_turns()},
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), window.latest_turn_id


def _identity(source: AuthorizedSemanticSource, config: SemanticRuntimeConfig) -> dict:
    return {
        "mailboxId": source.authority.mailbox_id,
        "conversationId": source.conversation_id,
        "latestTurnId": source.latest_turn_id,
        "semanticVersion": config.schema_version,
    }


def _priority_effect(
    config: SemanticRuntimeConfig,
    cached: CachedSemanticAssessment | None,
) -> str:
    if (
        config.can_mutate_priority
        and cached is not None
        and cached.assessment.state is SemanticState.RESOLVED
        and cached.effective_state is SemanticState.RESOLVED
        and cached.assessment.confidence
        >= confidence_threshold_for(SemanticState.RESOLVED)
    ):
        return PRIORITY_EFFECT_SUPPRESS_AUTOMATIC_OPEN_LOOP
    return PRIORITY_EFFECT_OBSERVE_ONLY


def _policy_fields(
    config: SemanticRuntimeConfig,
    cached: CachedSemanticAssessment | None = None,
    *,
    semantic_trigger: str | None = None,
) -> dict[str, str]:
    if semantic_trigger == SEMANTIC_NEW_INBOUND_TRIGGER:
        return {
            "semanticTrigger": SEMANTIC_NEW_INBOUND_TRIGGER,
            "newInboundMode": config.new_inbound_mode.value,
            "priorityEffect": PRIORITY_EFFECT_OBSERVE_ONLY,
        }
    if not config.enabled:
        return {}
    return {
        "semanticMode": config.mode.value,
        "priorityEffect": _priority_effect(config, cached),
    }


def _assessed_response(
    source: AuthorizedSemanticSource,
    config: SemanticRuntimeConfig,
    cached: CachedSemanticAssessment,
    *,
    status: str,
    active_event_ref: str | None,
    semantic_trigger: str | None = None,
) -> SemanticRouteResponse:
    payload = {
        "ok": True,
        "status": status,
        "assessment": cached.assessment.to_wire_dict(),
        "effectiveSemanticState": cached.effective_state.value,
        "assessedAt": datetime.fromtimestamp(
            cached.assessed_at,
            tz=timezone.utc,
        ).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "identity": _identity(source, config),
        **_policy_fields(
            config,
            cached,
            semantic_trigger=semantic_trigger,
        ),
    }
    if active_event_ref is not None:
        payload["activeEventRef"] = active_event_ref
    return SemanticRouteResponse(200, payload)


def _deferred_response(
    source: AuthorizedSemanticSource,
    config: SemanticRuntimeConfig,
    *,
    status: str,
    retry_after: int,
    semantic_trigger: str | None = None,
) -> SemanticRouteResponse:
    return SemanticRouteResponse(
        202,
        {
            "ok": True,
            "status": status,
            "identity": _identity(source, config),
            "retryAfterSeconds": retry_after,
            **_policy_fields(config, semantic_trigger=semantic_trigger),
        },
        retry_after=retry_after,
    )


def process_semantic_request(
    headers,
    payload: object,
    *,
    config: SemanticRuntimeConfig | None = None,
    hmac_secret: str | None = None,
    store: SemanticAssessmentStore | None = None,
    adapter=None,
    now: int | None = None,
    config_loader: Callable[[], SemanticRuntimeConfig] = load_semantic_runtime_config,
) -> SemanticRouteResponse:
    shape = _validate_payload_shape(payload)
    if isinstance(shape, SemanticRouteResponse):
        return shape
    trigger, mailbox_id, lookup_current = shape
    current = int(time.time()) if now is None else now
    try:
        authority = resolve_priority_authority(headers, mailbox_id)
    except SemanticAuthorityError as error:
        return _authority_error(error)

    try:
        runtime_config = config if config is not None else config_loader()
    except SemanticCoreError:
        return _error(503, "semantic_unavailable", "Semantic analysis is unavailable.")
    capability_enabled = (
        runtime_config.new_inbound_enabled
        if trigger == SEMANTIC_NEW_INBOUND_TRIGGER
        else runtime_config.enabled
    )
    if not capability_enabled or not runtime_config.model:
        # Mode OFF deliberately performs no provider, KV, or HMAC work.
        placeholder_source = AuthorizedSemanticSource(
            authority=authority,
            conversation_id="unavailable",
            provider_conversation_id="unavailable",
            latest_turn_id="unavailable",
            occurred_at=0,
            turns=(),
            revalidation_locator=None,
        )
        return _deferred_response(
            placeholder_source,
            runtime_config,
            status="deferred",
            retry_after=NEGATIVE_TTL_SECONDS,
            semantic_trigger=trigger,
        )

    if trigger == "outgoing_reply" and authority.provider != "google":
        return _error(
            409,
            "outgoing_semantic_unsupported",
            "Outgoing semantic analysis is not supported for this mailbox provider.",
        )
    locator = (
        payload.get("incomingLocator")
        if trigger in {"incoming_reply", SEMANTIC_NEW_INBOUND_TRIGGER}
        else None
    )
    if (
        type(locator) is dict
        and locator.get("provider") != authority.provider
    ):
        return _authority_error(SemanticAuthorityError("provider_mismatch", 400))

    try:
        secret = hmac_secret or resolve_priority_hmac_secret()
    except EventReferenceError:
        return _error(503, "semantic_unavailable", "Semantic analysis is unavailable.")

    claims: OutgoingEventClaims | None = None
    if trigger == "outgoing_reply" or (
        trigger == "incoming_reply" and authority.provider == "google"
    ):
        event_field = "eventRef" if trigger == "outgoing_reply" else "activeEventRef"
        verified = _verify_event(payload[event_field], hmac_secret=secret, now=current)
        if isinstance(verified, SemanticRouteResponse):
            return verified
        claims = verified
        try:
            verify_claim_scope(
                authority,
                claims,
                semantic_version=runtime_config.schema_version,
            )
        except SemanticAuthorityError as error:
            return _authority_error(error)
    if not lookup_current and trigger == "outgoing_reply" and (
        claims is None
        or not authored_text_matches(claims, payload["authoredText"])
    ):
        return _error(
            409,
            "authored_text_mismatch",
            "Authored text does not match the successful send event.",
        )

    if trigger == "outgoing_reply":
        assert claims is not None
        source = (
            _outgoing_lookup_source(authority, claims)
            if lookup_current
            else _outgoing_source(authority, claims, payload["authoredText"])
        )
        active_event_ref = payload["eventRef"]
        if not lookup_current:
            try:
                # Replayed outgoing refs may only use cached/model results while the
                # signed provider conversation still has this exact latest turn.
                prove_authorized_source_current(headers, source, claims)
            except SemanticAuthorityError as error:
                return _authority_error(error)
    elif trigger == "incoming_reply":
        locator = payload["incomingLocator"]
        try:
            if locator["provider"] == "google":
                if claims is None:
                    raise SemanticAuthorityError("event_scope_mismatch", 403)
                source = load_authorized_gmail_incoming(
                    authority,
                    claims,
                    provider_message_id=locator.get("providerMessageId"),
                )
            else:
                source = load_authorized_imap_incoming(
                    headers,
                    authority,
                    provider_folder=locator.get("providerFolder"),
                    uid_validity=locator.get("uidValidity"),
                    imap_uid=locator.get("imapUid"),
                )
        except SemanticAuthorityError as error:
            return _authority_error(error)
        active_event_ref = (
            payload["activeEventRef"]
            if locator["provider"] == "google"
            else None
        )
    else:
        locator = payload["incomingLocator"]
        try:
            if locator["provider"] == "google":
                source = load_authorized_gmail_new_inbound(
                    authority,
                    provider_message_id=locator.get("providerMessageId"),
                )
            else:
                source = load_authorized_imap_new_inbound(
                    headers,
                    authority,
                    provider_folder=locator.get("providerFolder"),
                    uid_validity=locator.get("uidValidity"),
                    imap_uid=locator.get("imapUid"),
                )
        except SemanticAuthorityError as error:
            return _authority_error(error)
        active_event_ref = None

    scope = SemanticCacheScope(
        workspace_id=authority.workspace_id,
        user_id=authority.user_id,
        mailbox_id=authority.mailbox_id,
        provider=authority.provider,
        conversation_id=source.conversation_id,
        latest_turn_id=source.latest_turn_id,
        semantic_version=runtime_config.schema_version,
        model_version=runtime_config.model,
    )
    if lookup_current:
        try:
            # Lookup is permitted only around two current-provider proofs.  The
            # store operation between them is an exact result GET and nothing
            # else: no pointer write, negative cache, lease, attempt, or model.
            prove_authorized_source_current(headers, source, claims)
            semantic_store = store or build_runtime_semantic_store(
                hmac_secret=secret
            )
            cached = semantic_store.get_result_for_exact_scope(scope)
        except SemanticAuthorityError as error:
            return _authority_error(error)
        except SemanticStoreUnavailable:
            return _deferred_response(
                source,
                runtime_config,
                status="deferred",
                retry_after=NEGATIVE_TTL_SECONDS,
                semantic_trigger=trigger,
            )
        if cached is None:
            return _deferred_response(
                source,
                runtime_config,
                status="deferred",
                retry_after=NEGATIVE_TTL_SECONDS,
                semantic_trigger=trigger,
            )
        try:
            prove_authorized_source_current(headers, source, claims)
            cached = semantic_store.get_result_for_exact_scope(scope)
        except SemanticAuthorityError as error:
            return _authority_error(error)
        except SemanticStoreUnavailable:
            return _deferred_response(
                source,
                runtime_config,
                status="deferred",
                retry_after=NEGATIVE_TTL_SECONDS,
                semantic_trigger=trigger,
            )
        if cached is None:
            return _deferred_response(
                source,
                runtime_config,
                status="deferred",
                retry_after=NEGATIVE_TTL_SECONDS,
                semantic_trigger=trigger,
            )
        return _assessed_response(
            source,
            runtime_config,
            cached,
            status="cached",
            active_event_ref=active_event_ref,
            semantic_trigger=trigger,
        )

    try:
        typed_turns = _typed_turns(source)
        input_hash, normalized_latest_turn_id = _input_hash(typed_turns)
    except SemanticCoreError:
        return _error(422, "input_invalid", "No bounded semantic input is available.")
    if normalized_latest_turn_id != source.latest_turn_id:
        return _error(409, "incoming_message_stale", "Semantic input is stale.")

    try:
        semantic_store = store or build_runtime_semantic_store(hmac_secret=secret)
        semantic_store.set_current_exact(
            scope,
            occurred_at=source.occurred_at,
        )
        cache_is_current, cached = semantic_store.get_result_if_current(
            scope,
            input_hash=input_hash,
            occurred_at=source.occurred_at,
        )
        if not cache_is_current:
            return _error(409, "incoming_message_stale", "Semantic input is stale.")
        if cached is not None:
            try:
                if trigger == SEMANTIC_NEW_INBOUND_TRIGGER:
                    prove_authorized_new_inbound_source_current(headers, source)
                else:
                    prove_authorized_source_current(headers, source, claims)
            except SemanticAuthorityError as error:
                return _authority_error(error)
            cache_is_current, cached = semantic_store.get_result_if_current(
                scope,
                input_hash=input_hash,
                occurred_at=source.occurred_at,
            )
            if not cache_is_current:
                return _error(409, "incoming_message_stale", "Semantic input is stale.")
            if cached is None:
                return _deferred_response(
                    source,
                    runtime_config,
                    status="pending",
                    retry_after=LEASE_TTL_SECONDS,
                    semantic_trigger=trigger,
                )
            return _assessed_response(
                source,
                runtime_config,
                cached,
                status="cached",
                active_event_ref=active_event_ref,
                semantic_trigger=trigger,
            )
        if semantic_store.get_negative(scope) is not None:
            return _deferred_response(
                source,
                runtime_config,
                status="deferred",
                retry_after=NEGATIVE_TTL_SECONDS,
                semantic_trigger=trigger,
            )
        lease_token = semantic_store.try_acquire_lease(scope)
        if lease_token is None:
            return _deferred_response(
                source,
                runtime_config,
                status="pending",
                retry_after=LEASE_TTL_SECONDS,
                semantic_trigger=trigger,
            )
        if not semantic_store.consume_attempt(scope):
            semantic_store.release_lease(scope, lease_token)
            return _deferred_response(
                source,
                runtime_config,
                status="deferred",
                retry_after=NEGATIVE_TTL_SECONDS,
                semantic_trigger=trigger,
            )
    except SemanticStoreUnavailable:
        return _deferred_response(
            source,
            runtime_config,
            status="deferred",
            retry_after=NEGATIVE_TTL_SECONDS,
            semantic_trigger=trigger,
        )

    try:
        semantic_adapter = adapter or build_openai_semantic_adapter(runtime_config)
        assessment = assess_semantic_conversation(
            SemanticAssessmentRequest(turns=typed_turns),
            adapter=semantic_adapter,
        )
    except SemanticCoreError as error:
        safe_code = error.code
        try:
            semantic_store.commit_negative_if_lease_owned(
                scope,
                lease_token=lease_token,
                code=safe_code,
            )
        except SemanticStoreUnavailable:
            pass
        return _deferred_response(
            source,
            runtime_config,
            status="deferred",
            retry_after=NEGATIVE_TTL_SECONDS,
            semantic_trigger=trigger,
        )
    except Exception:
        try:
            semantic_store.commit_negative_if_lease_owned(
                scope,
                lease_token=lease_token,
                code="semantic_unavailable",
            )
        except SemanticStoreUnavailable:
            pass
        return _deferred_response(
            source,
            runtime_config,
            status="deferred",
            retry_after=NEGATIVE_TTL_SECONDS,
            semantic_trigger=trigger,
        )

    try:
        # Provider truth is checked again after the model call.  This catches a
        # newer delivery even when no competing semantic request advanced Redis.
        if trigger == SEMANTIC_NEW_INBOUND_TRIGGER:
            prove_authorized_new_inbound_source_current(headers, source)
        else:
            prove_authorized_source_current(headers, source, claims)
    except SemanticAuthorityError as error:
        try:
            semantic_store.release_lease(scope, lease_token)
        except SemanticStoreUnavailable:
            pass
        return _authority_error(error)

    assessed_at = current
    try:
        committed = semantic_store.commit_result_if_lease_owned(
            scope,
            lease_token=lease_token,
            assessment=assessment,
            input_hash=input_hash,
            occurred_at=source.occurred_at,
            assessed_at=assessed_at,
        )
    except SemanticStoreUnavailable:
        committed = False
    if not committed:
        return _deferred_response(
            source,
            runtime_config,
            status="pending",
            retry_after=LEASE_TTL_SECONDS,
            semantic_trigger=trigger,
        )
    confidence = evaluate_semantic_confidence(assessment)
    fresh = CachedSemanticAssessment(
        assessment=assessment,
        effective_state=confidence.effective_state,
        assessed_at=assessed_at,
        input_hash=input_hash,
    )
    return _assessed_response(
        source,
        runtime_config,
        fresh,
        status="assessed",
        active_event_ref=active_event_ref,
        semantic_trigger=trigger,
    )
