from __future__ import annotations

import base64
import unittest
from unittest.mock import patch

from . import store as store_module
from .authority import (
    AuthorizedSemanticSource,
    PriorityAuthority,
    SemanticAuthorityError,
    canonical_conversation_id,
    gmail_thread_id,
)
from .event_reference import issue_outgoing_event_reference
from .semantic_config import (
    NewInboundSemanticMode,
    SemanticMode,
    SemanticRuntimeConfig,
)
from .semantic_config import load_semantic_runtime_config
from .semantic_errors import SemanticProviderTimeoutError
from .semantic_route import process_semantic_request
from .semantic_types import (
    SEMANTIC_SCHEMA_VERSION,
    SemanticAssessment,
    SemanticReasonCode,
    SemanticState,
    SpeakerRole,
    TurnDirection,
)
from .store import LEASE_TTL_SECONDS, SemanticAssessmentStore, SemanticCacheScope
from .test_store import MemoryRedis


def _account(prefix: str, byte: int) -> str:
    suffix = base64.urlsafe_b64encode(bytes([byte]) * 16).rstrip(b"=").decode("ascii")
    return prefix + suffix


SECRET = "priority-test-secret-with-more-than-thirty-two-bytes"
WORKSPACE_ID = _account("wsp_", 1)
USER_ID = _account("usr_", 2)
AUTHORED_TEXT = "Everything is complete from my side."
CONFIG = SemanticRuntimeConfig(mode=SemanticMode.SHADOW, model="test-model")
ACTIVE_CONFIG = SemanticRuntimeConfig(mode=SemanticMode.ACTIVE, model="test-model")
NEW_INBOUND_CONFIG = SemanticRuntimeConfig(
    mode=SemanticMode.ACTIVE,
    model="test-model",
    new_inbound_mode=NewInboundSemanticMode.SHADOW,
)
NEW_INBOUND_ONLY_CONFIG = SemanticRuntimeConfig(
    mode=SemanticMode.OFF,
    model="test-model",
    new_inbound_mode=NewInboundSemanticMode.SHADOW,
)


def authority() -> PriorityAuthority:
    inbox = {
        "id": "mailbox-1",
        "provider": "google",
        "email": "primary@example.com",
        "connected": True,
        "connectionStatus": "connected",
    }
    return PriorityAuthority(
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        member_email="owner@example.com",
        mailbox_id="mailbox-1",
        provider="google",
        mailbox_email="primary@example.com",
        owned_emails=frozenset({"owner@example.com", "primary@example.com"}),
        user_record={"email": "owner@example.com"},
        inbox_record=inbox,
    )


def custom_authority() -> PriorityAuthority:
    current = authority()
    inbox = {**current.inbox_record, "provider": "custom_imap"}
    return PriorityAuthority(
        workspace_id=current.workspace_id,
        user_id=current.user_id,
        member_email=current.member_email,
        mailbox_id=current.mailbox_id,
        provider="custom_imap",
        mailbox_email=current.mailbox_email,
        owned_emails=current.owned_emails,
        user_record=current.user_record,
        inbox_record=inbox,
    )


def event_reference(
    *,
    latest_turn_id: str = "sent-1",
    occurred_at: int = 10_000,
    workspace_id: str = WORKSPACE_ID,
    user_id: str = USER_ID,
    mailbox_id: str = "mailbox-1",
    issued_at: int = 10,
) -> str:
    thread_id = gmail_thread_id(mailbox_id, "thread-1")
    return issue_outgoing_event_reference(
        secret=SECRET,
        workspace_id=workspace_id,
        user_id=user_id,
        mailbox_id=mailbox_id,
        provider="google",
        conversation_id=canonical_conversation_id(mailbox_id, thread_id),
        provider_conversation_id="thread-1",
        latest_turn_id=latest_turn_id,
        authored_text=AUTHORED_TEXT,
        occurred_at=occurred_at,
        semantic_version=SEMANTIC_SCHEMA_VERSION,
        now=issued_at,
    )


def request(reference: str | None = None) -> dict:
    return {
        "mailboxId": "mailbox-1",
        "trigger": "outgoing_reply",
        "eventRef": reference or event_reference(),
        "authoredText": AUTHORED_TEXT,
    }


def lookup_request(reference: str | None = None) -> dict:
    return {
        "mailboxId": "mailbox-1",
        "operation": "lookup_current",
        "trigger": "outgoing_reply",
        "eventRef": reference or event_reference(),
    }


def custom_incoming_request() -> dict:
    return {
        "mailboxId": "mailbox-1",
        "trigger": "incoming_reply",
        "incomingLocator": {
            "provider": "custom_imap",
            "providerFolder": "INBOX",
            "uidValidity": "7",
            "imapUid": "9",
        },
    }


def gmail_incoming_request(
    reference: str | None = None,
    *,
    lookup_current: bool = False,
) -> dict:
    return {
        "mailboxId": "mailbox-1",
        **({"operation": "lookup_current"} if lookup_current else {}),
        "trigger": "incoming_reply",
        "activeEventRef": reference or event_reference(),
        "incomingLocator": {
            "provider": "google",
            "providerMessageId": "incoming-2",
        },
    }


def gmail_new_inbound_request(
    *,
    provider_message_id: str = "new-inbound-1",
) -> dict:
    return {
        "mailboxId": "mailbox-1",
        "trigger": "new_inbound",
        "incomingLocator": {
            "provider": "google",
            "providerMessageId": provider_message_id,
        },
    }


def custom_new_inbound_request() -> dict:
    return {
        "mailboxId": "mailbox-1",
        "trigger": "new_inbound",
        "incomingLocator": {
            "provider": "custom_imap",
            "providerFolder": "INBOX",
            "uidValidity": "7",
            "imapUid": "11",
        },
    }


def new_inbound_source(
    current: PriorityAuthority,
    *,
    latest_turn_id: str = "new-inbound-1",
    text: str = "Can you confirm the release date tomorrow?",
    provider_conversation_id: str | None = None,
) -> AuthorizedSemanticSource:
    provider_thread_id = provider_conversation_id or (
        "new-thread-1" if current.provider == "google" else "new-root@example.net"
    )
    return AuthorizedSemanticSource(
        authority=current,
        conversation_id=canonical_conversation_id(
            current.mailbox_id,
            (
                gmail_thread_id(current.mailbox_id, provider_thread_id)
                if current.provider == "google"
                else "imap:rfc:mailbox-1:new-root"
            ),
        ),
        provider_conversation_id=provider_thread_id,
        latest_turn_id=latest_turn_id,
        occurred_at=30_000,
        turns=(
            {
                "turnId": latest_turn_id,
                "speaker": "external",
                "direction": "incoming",
                "text": text,
                "timestamp": "2026-01-01T00:00:30Z",
            },
        ),
        revalidation_locator=(
            {
                "provider": "google",
                "providerMessageId": latest_turn_id,
            }
            if current.provider == "google"
            else {
                "provider": "custom_imap",
                "providerFolder": "INBOX",
                "uidValidity": "7",
                "imapUid": "11",
                "rfcMessageId": latest_turn_id,
            }
        ),
    )


ASSESSMENT = SemanticAssessment(
    state=SemanticState.RESOLVED,
    confidence=0.98,
    reason_code=SemanticReasonCode.COMPLETED_CONFIRMATION,
)


class FixedAdapter:
    model = "test-model"

    def __init__(self, assessment=ASSESSMENT) -> None:
        self.assessment = assessment
        self.calls = 0
        self.windows = []

    def assess(self, window):
        self.calls += 1
        self.windows.append(window)
        return self.assessment


class TimeoutAdapter:
    model = "test-model"

    def __init__(self) -> None:
        self.calls = 0

    def assess(self, _window):
        self.calls += 1
        raise SemanticProviderTimeoutError("fixed")


class LeaseLosingTimeoutAdapter:
    model = "test-model"

    def __init__(self, redis: MemoryRedis) -> None:
        self.redis = redis

    def assess(self, _window):
        lease_key = next(key for key in self.redis.values if ":lease:" in key)
        self.redis.values[lease_key] = "new-owner-token"
        raise SemanticProviderTimeoutError("fixed")


class LeaseLosingSuccessAdapter(FixedAdapter):
    def __init__(self, redis: MemoryRedis, assessment=ASSESSMENT) -> None:
        super().__init__(assessment)
        self.redis = redis

    def assess(self, window):
        result = super().assess(window)
        lease_key = next(key for key in self.redis.values if ":lease:" in key)
        self.redis.values[lease_key] = "new-owner-token"
        return result


class StaleDuringCallAdapter:
    model = "test-model"

    def __init__(self, store: SemanticAssessmentStore, current: PriorityAuthority) -> None:
        self.store = store
        self.current = current

    def assess(self, _window):
        newer = SemanticCacheScope(
            workspace_id=self.current.workspace_id,
            user_id=self.current.user_id,
            mailbox_id=self.current.mailbox_id,
            provider=self.current.provider,
            conversation_id=canonical_conversation_id(
                self.current.mailbox_id,
                gmail_thread_id(self.current.mailbox_id, "thread-1"),
            ),
            latest_turn_id="incoming-newer",
            semantic_version=SEMANTIC_SCHEMA_VERSION,
            model_version="test-model",
        )
        self.store.mark_current_if_newer(newer, occurred_at=12_000)
        return ASSESSMENT


class SemanticRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current = authority()
        self.redis = MemoryRedis()
        self.store = SemanticAssessmentStore(self.redis, hmac_secret=SECRET)
        self.authority_patch = patch(
            "api.priority.semantic_route.resolve_priority_authority",
            return_value=self.current,
        )
        self.authority_resolver = self.authority_patch.start()
        self.addCleanup(self.authority_patch.stop)
        self.provider_proof_patch = patch(
            "api.priority.semantic_route.prove_authorized_source_current",
            return_value=None,
        )
        self.provider_proof = self.provider_proof_patch.start()
        self.addCleanup(self.provider_proof_patch.stop)

    def process(
        self,
        payload: dict,
        adapter,
        *,
        now: int = 11,
        config: SemanticRuntimeConfig = CONFIG,
    ) -> object:
        return process_semantic_request(
            [],
            payload,
            config=config,
            hmac_secret=SECRET,
            store=self.store,
            adapter=adapter,
            now=now,
        )

    def process_new_inbound_fixture(
        self,
        *,
        fixture_id: str,
        text: str,
        assessment: SemanticAssessment,
    ):
        source = new_inbound_source(
            self.current,
            latest_turn_id=fixture_id,
            text=text,
            provider_conversation_id=f"thread-{fixture_id}",
        )
        adapter = FixedAdapter(assessment)
        with (
            patch(
                "api.priority.semantic_route.load_authorized_gmail_new_inbound",
                return_value=source,
            ),
            patch(
                "api.priority.semantic_route.prove_authorized_new_inbound_source_current",
                return_value=None,
            ),
        ):
            response = self.process(
                gmail_new_inbound_request(provider_message_id=fixture_id),
                adapter,
                config=NEW_INBOUND_CONFIG,
            )
        return response, adapter

    def test_assessed_then_cached_response_matches_strict_client_shape(self):
        adapter = FixedAdapter()
        first = self.process(request(), adapter)
        second = self.process(request(), adapter)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.payload["status"], "assessed")
        self.assertEqual(second.payload["status"], "cached")
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(
            set(first.payload),
            {
                "ok",
                "status",
                "assessment",
                "effectiveSemanticState",
                "assessedAt",
                "identity",
                "activeEventRef",
                "semanticMode",
                "priorityEffect",
            },
        )
        self.assertEqual(
            set(first.payload["identity"]),
            {"mailboxId", "conversationId", "latestTurnId", "semanticVersion"},
        )
        self.assertEqual(first.payload["assessment"], ASSESSMENT.to_wire_dict())
        self.assertEqual(first.payload["effectiveSemanticState"], "resolved")
        self.assertEqual(first.payload["semanticMode"], "shadow")
        self.assertEqual(first.payload["priorityEffect"], "observe_only")
        self.assertEqual(second.payload["semanticMode"], "shadow")
        self.assertEqual(second.payload["priorityEffect"], "observe_only")
        self.assertNotIn("modelVersion", first.payload["identity"])

    def test_authored_text_mismatch_is_rejected_before_kv_or_model(self):
        adapter = FixedAdapter()
        payload = request()
        payload["authoredText"] = "tampered"
        response = self.process(payload, adapter)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.payload["error"]["code"], "authored_text_mismatch")
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(self.redis.commands, [])

    def test_provider_timeout_is_negative_cached_for_five_minutes(self):
        adapter = TimeoutAdapter()
        first = self.process(request(), adapter)
        second = self.process(request(), adapter)

        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.payload["status"], "deferred")
        self.assertEqual(first.payload["retryAfterSeconds"], 300)
        self.assertEqual(first.payload["semanticMode"], "shadow")
        self.assertEqual(first.payload["priorityEffect"], "observe_only")
        self.assertEqual(second.payload["status"], "deferred")
        self.assertEqual(adapter.calls, 1)
        negative_commands = [
            command
            for command in self.redis.commands
            if command[0] == "EVAL"
            and command[1] == store_module._COMMIT_NEGATIVE_SCRIPT
        ]
        self.assertEqual(negative_commands[0][-1], 300)

    def test_newer_turn_during_model_call_prevents_stale_commit_and_projection(self):
        response = self.process(
            request(),
            StaleDuringCallAdapter(self.store, self.current),
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.payload["status"], "pending")
        self.assertNotIn("assessment", response.payload)
        self.assertFalse(any(":result:" in key for key in self.redis.values))

    def test_low_confidence_raw_result_returns_uncertain_effective_state(self):
        adapter = FixedAdapter(
            SemanticAssessment(
                state=SemanticState.RESOLVED,
                confidence=0.96,
                reason_code=SemanticReasonCode.COMPLETED_CONFIRMATION,
            )
        )
        response = self.process(request(), adapter)
        self.assertEqual(response.payload["assessment"]["state"], "resolved")
        self.assertEqual(response.payload["effectiveSemanticState"], "uncertain")

    def test_active_policy_suppresses_only_resolved_at_exact_threshold(self):
        cases = (
            (
                "resolved-below",
                SemanticAssessment(
                    state=SemanticState.RESOLVED,
                    confidence=0.969,
                    reason_code=SemanticReasonCode.COMPLETED_CONFIRMATION,
                ),
                "uncertain",
                "observe_only",
            ),
            (
                "resolved-at",
                SemanticAssessment(
                    state=SemanticState.RESOLVED,
                    confidence=0.970,
                    reason_code=SemanticReasonCode.COMPLETED_CONFIRMATION,
                ),
                "resolved",
                "suppress_automatic_open_loop",
            ),
            (
                "needs-action",
                SemanticAssessment(
                    state=SemanticState.NEEDS_USER_ACTION,
                    confidence=0.99,
                    reason_code=SemanticReasonCode.EXPLICIT_REQUEST,
                ),
                "needs_user_action",
                "observe_only",
            ),
            (
                "waiting",
                SemanticAssessment(
                    state=SemanticState.WAITING_ON_OTHER,
                    confidence=0.99,
                    reason_code=SemanticReasonCode.AWAITING_CONFIRMATION,
                ),
                "waiting_on_other",
                "observe_only",
            ),
            (
                "informational",
                SemanticAssessment(
                    state=SemanticState.INFORMATIONAL,
                    confidence=1.0,
                    reason_code=SemanticReasonCode.INFORMATIONAL_UPDATE,
                ),
                "informational",
                "observe_only",
            ),
            (
                "uncertain",
                SemanticAssessment(
                    state=SemanticState.UNCERTAIN,
                    confidence=1.0,
                    reason_code=SemanticReasonCode.AMBIGUOUS_CONTEXT,
                ),
                "uncertain",
                "observe_only",
            ),
        )
        for index, (name, assessment, effective, effect) in enumerate(cases):
            with self.subTest(name=name):
                reference = event_reference(
                    latest_turn_id=f"sent-{index + 10}",
                    occurred_at=10_000 + index,
                )
                response = self.process(
                    request(reference),
                    FixedAdapter(assessment),
                    config=ACTIVE_CONFIG,
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.payload["semanticMode"], "active")
                self.assertEqual(
                    response.payload["effectiveSemanticState"],
                    effective,
                )
                self.assertEqual(response.payload["priorityEffect"], effect)

    def test_cached_semantics_rederive_policy_across_shadow_active_switches(self):
        adapter = FixedAdapter()
        payload = request()

        shadow = self.process(payload, adapter, config=CONFIG)
        active = self.process(payload, adapter, config=ACTIVE_CONFIG)
        rolled_back = self.process(payload, adapter, config=CONFIG)

        self.assertEqual(adapter.calls, 1)
        self.assertEqual(shadow.payload["status"], "assessed")
        self.assertEqual(shadow.payload["semanticMode"], "shadow")
        self.assertEqual(shadow.payload["priorityEffect"], "observe_only")
        self.assertEqual(active.payload["status"], "cached")
        self.assertEqual(active.payload["semanticMode"], "active")
        self.assertEqual(
            active.payload["priorityEffect"],
            "suppress_automatic_open_loop",
        )
        self.assertEqual(rolled_back.payload["status"], "cached")
        self.assertEqual(rolled_back.payload["semanticMode"], "shadow")
        self.assertEqual(rolled_back.payload["priorityEffect"], "observe_only")

    def test_lookup_current_uses_only_exact_result_gets_and_never_calls_model(self):
        adapter = FixedAdapter()
        self.assertEqual(
            self.process(request(), adapter, config=CONFIG).payload["status"],
            "assessed",
        )
        self.redis.commands.clear()
        self.provider_proof.reset_mock()

        response = self.process(
            lookup_request(),
            adapter,
            config=ACTIVE_CONFIG,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.payload["status"], "cached")
        self.assertEqual(response.payload["semanticMode"], "active")
        self.assertEqual(
            response.payload["priorityEffect"],
            "suppress_automatic_open_loop",
        )
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(self.provider_proof.call_count, 2)
        self.assertEqual(len(self.redis.commands), 2)
        self.assertTrue(all(command[0] == "GET" for command in self.redis.commands))
        self.assertTrue(
            all(":result:" in command[1] for command in self.redis.commands)
        )

    def test_lookup_current_cache_miss_is_observe_only_without_model_or_writes(self):
        adapter = FixedAdapter()
        missing_reference = event_reference(
            latest_turn_id="sent-cache-miss",
            occurred_at=10_001,
        )

        response = self.process(
            lookup_request(missing_reference),
            adapter,
            config=ACTIVE_CONFIG,
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.payload["status"], "deferred")
        self.assertEqual(response.payload["semanticMode"], "active")
        self.assertEqual(response.payload["priorityEffect"], "observe_only")
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(len(self.redis.commands), 1)
        self.assertEqual(self.redis.commands[0][0], "GET")
        self.assertIn(":result:", self.redis.commands[0][1])

    def test_lookup_current_has_exact_distinct_request_shapes(self):
        invalid_payloads = (
            {**lookup_request(), "authoredText": AUTHORED_TEXT},
            {**request(), "operation": "assess"},
            {
                "mailboxId": "mailbox-1",
                "operation": "lookup_current",
                "trigger": "outgoing_reply",
            },
        )
        for payload in invalid_payloads:
            with self.subTest(fields=tuple(sorted(payload))):
                self.authority_resolver.reset_mock()
                response = self.process(payload, FixedAdapter())
                self.assertEqual(response.status_code, 400)
                self.authority_resolver.assert_not_called()

    def test_lookup_current_stale_provider_identity_returns_no_cached_policy(self):
        adapter = FixedAdapter()
        self.assertEqual(
            self.process(request(), adapter, config=CONFIG).payload["status"],
            "assessed",
        )
        self.redis.commands.clear()
        self.provider_proof.reset_mock()
        self.provider_proof.side_effect = SemanticAuthorityError(
            "incoming_message_stale",
            409,
        )

        response = self.process(
            lookup_request(),
            adapter,
            config=ACTIVE_CONFIG,
        )
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("priorityEffect", response.payload)
        self.assertEqual(self.redis.commands, [])
        self.assertEqual(adapter.calls, 1)

    def test_provider_change_during_model_call_without_competing_route_is_rejected(self):
        self.provider_proof.side_effect = [
            None,
            SemanticAuthorityError("incoming_message_stale", 409),
        ]
        response = self.process(request(), FixedAdapter())

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.payload["error"]["code"], "incoming_message_stale")
        self.assertNotIn("assessment", response.payload)
        self.assertFalse(any(":result:" in key for key in self.redis.values))

    def test_stale_cached_outgoing_ref_is_not_projected(self):
        adapter = FixedAdapter()
        self.assertEqual(self.process(request(), adapter).payload["status"], "assessed")
        self.provider_proof.reset_mock()
        self.provider_proof.side_effect = SemanticAuthorityError(
            "incoming_message_stale",
            409,
        )

        response = self.process(request(), adapter)
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("assessment", response.payload)
        self.assertEqual(adapter.calls, 1)

    def test_lost_lease_cannot_poison_negative_cache(self):
        response = self.process(
            request(),
            LeaseLosingTimeoutAdapter(self.redis),
        )
        self.assertEqual(response.status_code, 202)
        self.assertFalse(any(":negative:" in key for key in self.redis.values))
        self.assertTrue(any(":lease:" in key for key in self.redis.values))

    def test_scope_rejection_happens_before_kv_and_model(self):
        adapter = FixedAdapter()
        self.authority_resolver.side_effect = SemanticAuthorityError(
            "unauthorized",
            401,
        )
        response = self.process(request(), adapter)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.redis.commands, [])
        self.assertEqual(adapter.calls, 0)

    def test_forged_workspace_user_and_cross_mailbox_refs_fail_before_provider_kv_model(self):
        forged_references = (
            event_reference(workspace_id=_account("wsp_", 9)),
            event_reference(user_id=_account("usr_", 9)),
            event_reference(mailbox_id="mailbox-2"),
        )
        for request_builder in (request, lookup_request):
            for reference in forged_references:
                with self.subTest(
                    operation=request_builder.__name__,
                    reference=reference[:12],
                ):
                    self.redis.commands.clear()
                    self.provider_proof.reset_mock()
                    adapter = FixedAdapter()
                    response = self.process(request_builder(reference), adapter)
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(
                        response.payload["error"]["code"],
                        "event_scope_mismatch",
                    )
                    self.provider_proof.assert_not_called()
                    self.assertEqual(self.redis.commands, [])
                    self.assertEqual(adapter.calls, 0)

    def test_invalid_and_expired_refs_fail_before_provider_kv_model(self):
        cases = (
            (request("not-a-signed-reference"), 11, "invalid_event_ref"),
            (
                request(event_reference(occurred_at=0, issued_at=0)),
                14 * 24 * 60 * 60,
                "stale_event_ref",
            ),
        )
        for payload, now, expected_code in cases:
            with self.subTest(code=expected_code):
                self.redis.commands.clear()
                self.provider_proof.reset_mock()
                adapter = FixedAdapter()
                response = self.process(payload, adapter, now=now)
                self.assertEqual(response.payload["error"]["code"], expected_code)
                self.provider_proof.assert_not_called()
                self.assertEqual(self.redis.commands, [])
                self.assertEqual(adapter.calls, 0)

    def test_client_supplied_authority_fields_are_rejected_before_auth_or_side_effects(self):
        payload = request()
        payload.update({"workspaceId": WORKSPACE_ID, "userId": USER_ID})
        self.authority_resolver.reset_mock()
        adapter = FixedAdapter()
        response = self.process(payload, adapter)
        self.assertEqual(response.status_code, 400)
        self.authority_resolver.assert_not_called()
        self.provider_proof.assert_not_called()
        self.assertEqual(self.redis.commands, [])
        self.assertEqual(adapter.calls, 0)

    def test_off_and_invalid_mode_have_zero_provider_hmac_kv_or_model_effects(self):
        configurations = (
            lambda: SemanticRuntimeConfig(mode=SemanticMode.OFF, model=None),
            lambda: load_semantic_runtime_config(
                {
                    "PRIORITY_SEMANTIC_MODE": "invalid-mode",
                    "PRIORITY_SEMANTIC_MODEL": "test-model",
                }
            ),
        )
        for loader in configurations:
            with self.subTest(loader=loader):
                self.redis.commands.clear()
                self.provider_proof.reset_mock()
                adapter = FixedAdapter()
                with patch(
                    "api.priority.semantic_route.resolve_priority_hmac_secret",
                    side_effect=AssertionError("HMAC resolution must stay off"),
                ):
                    response = process_semantic_request(
                        [],
                        request(),
                        config_loader=loader,
                        store=self.store,
                        adapter=adapter,
                        now=11,
                    )
                self.assertEqual(response.status_code, 202)
                self.assertEqual(response.payload["status"], "deferred")
                self.assertNotIn("semanticMode", response.payload)
                self.assertNotIn("priorityEffect", response.payload)
                self.provider_proof.assert_not_called()
                self.assertEqual(self.redis.commands, [])
                self.assertEqual(adapter.calls, 0)

    def test_custom_outgoing_is_unsupported_before_hmac_provider_kv_or_model(self):
        self.authority_resolver.return_value = custom_authority()
        adapter = FixedAdapter()
        with patch(
            "api.priority.semantic_route.resolve_priority_hmac_secret",
            side_effect=AssertionError("custom outgoing must not resolve HMAC"),
        ):
            response = process_semantic_request(
                [],
                request("legacy-custom-outgoing-reference"),
                config=CONFIG,
                store=self.store,
                adapter=adapter,
                now=22,
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.payload["error"]["code"],
            "outgoing_semantic_unsupported",
        )
        self.provider_proof.assert_not_called()
        self.assertEqual(self.redis.commands, [])
        self.assertEqual(adapter.calls, 0)

    def test_custom_outgoing_lookup_remains_unsupported_before_hmac_kv_or_model(self):
        self.authority_resolver.return_value = custom_authority()
        adapter = FixedAdapter()
        with patch(
            "api.priority.semantic_route.resolve_priority_hmac_secret",
            side_effect=AssertionError("custom outgoing must not resolve HMAC"),
        ):
            response = process_semantic_request(
                [],
                lookup_request("legacy-custom-outgoing-reference"),
                config=ACTIVE_CONFIG,
                store=self.store,
                adapter=adapter,
                now=22,
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.payload["error"]["code"],
            "outgoing_semantic_unsupported",
        )
        self.provider_proof.assert_not_called()
        self.assertEqual(self.redis.commands, [])
        self.assertEqual(adapter.calls, 0)

    def test_gmail_incoming_lookup_rehydrates_exact_cached_returned_turn(self):
        current = authority()
        source = AuthorizedSemanticSource(
            authority=current,
            conversation_id=canonical_conversation_id(
                "mailbox-1",
                gmail_thread_id("mailbox-1", "thread-1"),
            ),
            provider_conversation_id="thread-1",
            latest_turn_id="incoming-2",
            occurred_at=12_000,
            turns=(
                {
                    "turnId": "incoming-2",
                    "speaker": "external",
                    "direction": "incoming",
                    "text": "Thanks, everything is sorted.",
                    "timestamp": "2026-01-01T00:00:20Z",
                },
            ),
            revalidation_locator={
                "provider": "google",
                "providerMessageId": "incoming-2",
            },
        )
        adapter = FixedAdapter()
        with patch(
            "api.priority.semantic_route.load_authorized_gmail_incoming",
            return_value=source,
        ) as loader:
            assessed = self.process(
                gmail_incoming_request(),
                adapter,
                config=CONFIG,
            )
            self.redis.commands.clear()
            self.provider_proof.reset_mock()
            lookup = self.process(
                gmail_incoming_request(lookup_current=True),
                adapter,
                config=ACTIVE_CONFIG,
            )

        self.assertEqual(assessed.payload["status"], "assessed")
        self.assertEqual(assessed.payload["priorityEffect"], "observe_only")
        self.assertEqual(lookup.payload["status"], "cached")
        self.assertEqual(lookup.payload["semanticMode"], "active")
        self.assertEqual(
            lookup.payload["activeEventRef"],
            gmail_incoming_request()["activeEventRef"],
        )
        self.assertEqual(
            lookup.payload["priorityEffect"],
            "suppress_automatic_open_loop",
        )
        self.assertEqual(loader.call_count, 2)
        self.assertEqual(self.provider_proof.call_count, 2)
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(len(self.redis.commands), 2)
        self.assertTrue(all(command[0] == "GET" for command in self.redis.commands))

    def test_ref_less_custom_incoming_assesses_and_returns_no_active_ref(self):
        current = custom_authority()
        self.authority_resolver.return_value = current
        source = AuthorizedSemanticSource(
            authority=current,
            conversation_id=canonical_conversation_id(
                "mailbox-1",
                "imap:rfc:mailbox-1:root",
            ),
            provider_conversation_id="root@example.net",
            latest_turn_id="incoming@example.net",
            occurred_at=20_000,
            turns=(
                {
                    "turnId": "incoming@example.net",
                    "speaker": "external",
                    "direction": "incoming",
                    "text": "Could you confirm this?",
                    "timestamp": "2026-01-01T00:00:20Z",
                },
            ),
            revalidation_locator={
                "provider": "custom_imap",
                "providerFolder": "INBOX",
                "uidValidity": "7",
                "imapUid": "9",
                "rfcMessageId": "incoming@example.net",
            },
        )
        adapter = FixedAdapter()
        with patch(
            "api.priority.semantic_route.load_authorized_imap_incoming",
            return_value=source,
        ) as loader:
            response = self.process(custom_incoming_request(), adapter)
            cached_response = self.process(custom_incoming_request(), adapter)
            self.redis.commands.clear()
            lookup_response = self.process(
                {
                    **custom_incoming_request(),
                    "operation": "lookup_current",
                },
                adapter,
                config=ACTIVE_CONFIG,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.payload["status"], "assessed")
        self.assertEqual(cached_response.status_code, 200)
        self.assertEqual(cached_response.payload["status"], "cached")
        self.assertEqual(lookup_response.status_code, 200)
        self.assertEqual(lookup_response.payload["status"], "cached")
        self.assertEqual(lookup_response.payload["semanticMode"], "active")
        self.assertEqual(
            lookup_response.payload["priorityEffect"],
            "suppress_automatic_open_loop",
        )
        self.assertNotIn("activeEventRef", response.payload)
        self.assertNotIn("activeEventRef", cached_response.payload)
        self.assertNotIn("activeEventRef", lookup_response.payload)
        self.assertEqual(loader.call_count, 3)
        loader.assert_called_with(
            [], current, provider_folder="INBOX", uid_validity="7", imap_uid="9"
        )
        self.assertEqual(self.provider_proof.call_count, 4)
        self.provider_proof.assert_called_with([], source, None)
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(len(self.redis.commands), 2)
        self.assertTrue(all(command[0] == "GET" for command in self.redis.commands))

    def test_new_inbound_gmail_is_strict_shadow_even_with_open_loop_active(self):
        source = new_inbound_source(self.current)
        actionable = SemanticAssessment(
            state=SemanticState.NEEDS_USER_ACTION,
            confidence=0.99,
            reason_code=SemanticReasonCode.EXPLICIT_REQUEST,
        )
        adapter = FixedAdapter(actionable)
        with (
            patch(
                "api.priority.semantic_route.load_authorized_gmail_new_inbound",
                return_value=source,
            ) as loader,
            patch(
                "api.priority.semantic_route.prove_authorized_new_inbound_source_current",
                return_value=None,
            ) as proof,
        ):
            assessed = self.process(
                gmail_new_inbound_request(),
                adapter,
                config=NEW_INBOUND_CONFIG,
            )
            cached = self.process(
                gmail_new_inbound_request(),
                adapter,
                config=NEW_INBOUND_CONFIG,
            )

        self.assertTrue(NEW_INBOUND_CONFIG.can_mutate_priority)
        self.assertEqual(assessed.status_code, 200)
        self.assertEqual(assessed.payload["status"], "assessed")
        self.assertEqual(cached.payload["status"], "cached")
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(loader.call_count, 2)
        loader.assert_called_with(
            self.current,
            provider_message_id="new-inbound-1",
        )
        self.assertEqual(proof.call_count, 2)
        self.provider_proof.assert_not_called()
        self.assertEqual(
            set(assessed.payload),
            {
                "ok",
                "status",
                "assessment",
                "effectiveSemanticState",
                "assessedAt",
                "identity",
                "semanticTrigger",
                "newInboundMode",
                "priorityEffect",
            },
        )
        self.assertEqual(assessed.payload["semanticTrigger"], "new_inbound")
        self.assertEqual(assessed.payload["newInboundMode"], "shadow")
        self.assertEqual(assessed.payload["priorityEffect"], "observe_only")
        self.assertEqual(
            assessed.payload["assessment"],
            actionable.to_wire_dict(),
        )
        self.assertEqual(
            assessed.payload["effectiveSemanticState"],
            "needs_user_action",
        )
        self.assertNotIn("semanticMode", assessed.payload)
        self.assertNotIn("activeEventRef", assessed.payload)

    def test_new_inbound_multilingual_actionable_and_informational_fixtures(self):
        actionable = (
            "Can you confirm the release date tomorrow?",
            "Kun je de artwork vandaag nog sturen?",
            "Kannst du bitte die Rechnung schicken?",
            "Peux-tu confirmer la date de sortie ?",
            "¿Puedes enviarme el contrato?",
            "Puoi confermare la data?",
            "Pode enviar o contrato?",
        )
        informational = (
            "FYI, the release is now live.",
            "Ter info: de release staat nu live.",
            "Zur Information: Die Veröffentlichung ist jetzt live.",
            "Pour information, la sortie est maintenant en ligne.",
            "Para tu información, el lanzamiento ya está disponible.",
            "Per informazione, la pubblicazione è ora online.",
            "Para sua informação, o lançamento já está no ar.",
        )
        cases = (
            *(
                (
                    f"actionable-{index}",
                    text,
                    SemanticAssessment(
                        state=SemanticState.NEEDS_USER_ACTION,
                        confidence=0.99,
                        reason_code=SemanticReasonCode.EXPLICIT_REQUEST,
                    ),
                )
                for index, text in enumerate(actionable)
            ),
            *(
                (
                    f"informational-{index}",
                    text,
                    SemanticAssessment(
                        state=SemanticState.INFORMATIONAL,
                        confidence=0.99,
                        reason_code=SemanticReasonCode.INFORMATIONAL_UPDATE,
                    ),
                )
                for index, text in enumerate(informational)
            ),
        )
        for fixture_id, text, assessment in cases:
            with self.subTest(fixture_id=fixture_id, text=text):
                response, adapter = self.process_new_inbound_fixture(
                    fixture_id=fixture_id,
                    text=text,
                    assessment=assessment,
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.payload["status"], "assessed")
                self.assertEqual(response.payload["semanticTrigger"], "new_inbound")
                self.assertEqual(response.payload["newInboundMode"], "shadow")
                self.assertEqual(response.payload["priorityEffect"], "observe_only")
                self.assertEqual(
                    response.payload["assessment"],
                    assessment.to_wire_dict(),
                )
                self.assertEqual(adapter.calls, 1)
                self.assertEqual(len(adapter.windows[0].turns), 1)
                self.assertEqual(
                    adapter.windows[0].turns[0].speaker,
                    SpeakerRole.EXTERNAL,
                )
                self.assertEqual(
                    adapter.windows[0].turns[0].direction,
                    TurnDirection.INCOMING,
                )
                self.assertEqual(adapter.windows[0].turns[0].text, text)

    def test_new_inbound_ambiguity_controls_remain_observational(self):
        cases = (
            (
                "sounds-good",
                "Sounds good.",
                SemanticState.UNCERTAIN,
                SemanticReasonCode.AMBIGUOUS_CONTEXT,
            ),
            (
                "yes",
                "Yes.",
                SemanticState.UNCERTAIN,
                SemanticReasonCode.AMBIGUOUS_CONTEXT,
            ),
            (
                "thanks",
                "Thanks.",
                SemanticState.INFORMATIONAL,
                SemanticReasonCode.INFORMATIONAL_UPDATE,
            ),
            (
                "contextless-can-you",
                "Can you?",
                SemanticState.UNCERTAIN,
                SemanticReasonCode.AMBIGUOUS_CONTEXT,
            ),
        )
        for fixture_id, text, state, reason in cases:
            with self.subTest(fixture_id=fixture_id):
                assessment = SemanticAssessment(
                    state=state,
                    confidence=0.99,
                    reason_code=reason,
                )
                response, adapter = self.process_new_inbound_fixture(
                    fixture_id=fixture_id,
                    text=text,
                    assessment=assessment,
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.payload["assessment"],
                    assessment.to_wire_dict(),
                )
                self.assertEqual(response.payload["priorityEffect"], "observe_only")
                self.assertEqual(adapter.calls, 1)
                self.assertEqual(adapter.windows[0].turns[0].text, text)

    def test_new_inbound_timeout_is_negative_cached_without_a_second_model_call(self):
        source = new_inbound_source(
            self.current,
            latest_turn_id="new-inbound-timeout",
        )
        adapter = TimeoutAdapter()
        with (
            patch(
                "api.priority.semantic_route.load_authorized_gmail_new_inbound",
                return_value=source,
            ) as loader,
            patch(
                "api.priority.semantic_route.prove_authorized_new_inbound_source_current",
                return_value=None,
            ) as proof,
        ):
            payload = gmail_new_inbound_request(
                provider_message_id="new-inbound-timeout"
            )
            first = self.process(payload, adapter, config=NEW_INBOUND_CONFIG)
            second = self.process(payload, adapter, config=NEW_INBOUND_CONFIG)

        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.payload["status"], "deferred")
        self.assertEqual(first.payload["retryAfterSeconds"], 300)
        self.assertEqual(first.payload["semanticTrigger"], "new_inbound")
        self.assertEqual(first.payload["newInboundMode"], "shadow")
        self.assertEqual(first.payload["priorityEffect"], "observe_only")
        self.assertEqual(second.payload["status"], "deferred")
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(loader.call_count, 2)
        proof.assert_not_called()
        self.assertEqual(
            len(
                [
                    command
                    for command in self.redis.commands
                    if command[0] == "EVAL"
                    and command[1] == store_module._COMMIT_NEGATIVE_SCRIPT
                ]
            ),
            1,
        )

    def test_new_inbound_lease_contention_returns_pending_without_model_work(self):
        source = new_inbound_source(
            self.current,
            latest_turn_id="new-inbound-contended",
        )
        scope = SemanticCacheScope(
            workspace_id=self.current.workspace_id,
            user_id=self.current.user_id,
            mailbox_id=self.current.mailbox_id,
            provider=self.current.provider,
            conversation_id=source.conversation_id,
            latest_turn_id=source.latest_turn_id,
            semantic_version=NEW_INBOUND_CONFIG.schema_version,
            model_version=NEW_INBOUND_CONFIG.model,
        )
        self.assertIsNotNone(self.store.try_acquire_lease(scope))
        adapter = FixedAdapter()
        with (
            patch(
                "api.priority.semantic_route.load_authorized_gmail_new_inbound",
                return_value=source,
            ),
            patch(
                "api.priority.semantic_route.prove_authorized_new_inbound_source_current",
                return_value=None,
            ) as proof,
        ):
            response = self.process(
                gmail_new_inbound_request(
                    provider_message_id="new-inbound-contended"
                ),
                adapter,
                config=NEW_INBOUND_CONFIG,
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.payload["status"], "pending")
        self.assertEqual(response.payload["retryAfterSeconds"], LEASE_TTL_SECONDS)
        self.assertEqual(response.payload["priorityEffect"], "observe_only")
        self.assertEqual(adapter.calls, 0)
        proof.assert_not_called()

    def test_new_inbound_lost_lease_cannot_commit_model_result(self):
        source = new_inbound_source(
            self.current,
            latest_turn_id="new-inbound-lost-lease",
        )
        adapter = LeaseLosingSuccessAdapter(self.redis)
        with (
            patch(
                "api.priority.semantic_route.load_authorized_gmail_new_inbound",
                return_value=source,
            ),
            patch(
                "api.priority.semantic_route.prove_authorized_new_inbound_source_current",
                return_value=None,
            ) as proof,
        ):
            response = self.process(
                gmail_new_inbound_request(
                    provider_message_id="new-inbound-lost-lease"
                ),
                adapter,
                config=NEW_INBOUND_CONFIG,
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.payload["status"], "pending")
        self.assertEqual(response.payload["priorityEffect"], "observe_only")
        self.assertEqual(adapter.calls, 1)
        proof.assert_called_once_with([], source)
        self.assertFalse(any(":result:" in key for key in self.redis.values))
        self.assertTrue(
            any(
                ":lease:" in key and value == "new-owner-token"
                for key, value in self.redis.values.items()
            )
        )

    def test_new_inbound_post_model_provider_staleness_rejects_commit(self):
        source = new_inbound_source(
            self.current,
            latest_turn_id="new-inbound-stale-after-model",
        )
        adapter = FixedAdapter()
        with (
            patch(
                "api.priority.semantic_route.load_authorized_gmail_new_inbound",
                return_value=source,
            ),
            patch(
                "api.priority.semantic_route.prove_authorized_new_inbound_source_current",
                side_effect=SemanticAuthorityError("incoming_message_stale", 409),
            ) as proof,
        ):
            response = self.process(
                gmail_new_inbound_request(
                    provider_message_id="new-inbound-stale-after-model"
                ),
                adapter,
                config=NEW_INBOUND_CONFIG,
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.payload["error"]["code"], "incoming_message_stale")
        self.assertNotIn("assessment", response.payload)
        self.assertEqual(adapter.calls, 1)
        proof.assert_called_once_with([], source)
        self.assertFalse(any(":result:" in key for key in self.redis.values))
        self.assertFalse(any(":lease:" in key for key in self.redis.values))

    def test_open_loop_outgoing_and_incoming_keep_active_resolved_suppression_when_new_inbound_is_shadow(self):
        outgoing_adapter = FixedAdapter(ASSESSMENT)
        outgoing = self.process(
            request(),
            outgoing_adapter,
            config=NEW_INBOUND_CONFIG,
        )

        incoming_source = AuthorizedSemanticSource(
            authority=self.current,
            conversation_id=canonical_conversation_id(
                "mailbox-1",
                gmail_thread_id("mailbox-1", "thread-1"),
            ),
            provider_conversation_id="thread-1",
            latest_turn_id="incoming-2",
            occurred_at=12_000,
            turns=(
                {
                    "turnId": "incoming-2",
                    "speaker": "external",
                    "direction": "incoming",
                    "text": "Thanks, everything is sorted.",
                    "timestamp": "2026-01-01T00:00:20Z",
                },
            ),
            revalidation_locator={
                "provider": "google",
                "providerMessageId": "incoming-2",
            },
        )
        incoming_adapter = FixedAdapter(ASSESSMENT)
        with patch(
            "api.priority.semantic_route.load_authorized_gmail_incoming",
            return_value=incoming_source,
        ) as loader:
            incoming = self.process(
                gmail_incoming_request(),
                incoming_adapter,
                config=NEW_INBOUND_CONFIG,
            )

        self.assertTrue(NEW_INBOUND_CONFIG.can_mutate_priority)
        self.assertTrue(NEW_INBOUND_CONFIG.new_inbound_enabled)
        for name, response in (("outgoing", outgoing), ("incoming", incoming)):
            with self.subTest(name=name):
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.payload["status"], "assessed")
                self.assertEqual(response.payload["semanticMode"], "active")
                self.assertEqual(
                    response.payload["priorityEffect"],
                    "suppress_automatic_open_loop",
                )
                self.assertNotIn("semanticTrigger", response.payload)
                self.assertNotIn("newInboundMode", response.payload)
        self.assertEqual(outgoing_adapter.calls, 1)
        self.assertEqual(incoming_adapter.calls, 1)
        loader.assert_called_once()

    def test_new_inbound_unauthenticated_fails_before_provider_kv_or_model(self):
        self.authority_resolver.side_effect = SemanticAuthorityError(
            "unauthorized",
            401,
        )
        adapter = FixedAdapter()
        with patch(
            "api.priority.semantic_route.load_authorized_gmail_new_inbound",
            side_effect=AssertionError("provider load must not run"),
        ) as loader:
            response = self.process(
                gmail_new_inbound_request(),
                adapter,
                config=NEW_INBOUND_CONFIG,
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.payload["error"]["code"], "unauthorized")
        loader.assert_not_called()
        self.assertEqual(self.redis.commands, [])
        self.assertEqual(adapter.calls, 0)

    def test_new_inbound_provider_mismatch_fails_before_provider_kv_or_model(self):
        adapter = FixedAdapter()
        with (
            patch(
                "api.priority.semantic_route.load_authorized_gmail_new_inbound",
                side_effect=AssertionError("Gmail provider load must not run"),
            ) as gmail_loader,
            patch(
                "api.priority.semantic_route.load_authorized_imap_new_inbound",
                side_effect=AssertionError("IMAP provider load must not run"),
            ) as imap_loader,
        ):
            response = self.process(
                custom_new_inbound_request(),
                adapter,
                config=NEW_INBOUND_CONFIG,
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.payload["error"]["code"], "provider_mismatch")
        gmail_loader.assert_not_called()
        imap_loader.assert_not_called()
        self.assertEqual(self.redis.commands, [])
        self.assertEqual(adapter.calls, 0)

    def test_new_inbound_cross_mailbox_request_fails_before_provider_kv_or_model(self):
        self.authority_resolver.side_effect = SemanticAuthorityError(
            "mailbox_not_found",
            404,
        )
        payload = gmail_new_inbound_request()
        payload["mailboxId"] = "mailbox-2"
        adapter = FixedAdapter()
        with patch(
            "api.priority.semantic_route.load_authorized_gmail_new_inbound",
            side_effect=AssertionError("provider load must not run"),
        ) as loader:
            response = self.process(
                payload,
                adapter,
                config=NEW_INBOUND_CONFIG,
            )

        self.authority_resolver.assert_called_once_with([], "mailbox-2")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.payload["error"]["code"], "mailbox_not_found")
        loader.assert_not_called()
        self.assertEqual(self.redis.commands, [])
        self.assertEqual(adapter.calls, 0)

    def test_new_inbound_cross_mailbox_provider_id_is_rejected_before_kv_or_model(self):
        adapter = FixedAdapter()
        with patch(
            "api.priority.semantic_route.load_authorized_gmail_new_inbound",
            side_effect=SemanticAuthorityError("event_scope_mismatch", 403),
        ) as loader:
            response = self.process(
                gmail_new_inbound_request(
                    provider_message_id="belongs-to-another-mailbox"
                ),
                adapter,
                config=NEW_INBOUND_CONFIG,
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.payload["error"]["code"], "event_scope_mismatch")
        loader.assert_called_once_with(
            self.current,
            provider_message_id="belongs-to-another-mailbox",
        )
        self.assertEqual(self.redis.commands, [])
        self.assertEqual(adapter.calls, 0)

    def test_new_inbound_routing_exclusion_uses_bounded_public_error_before_kv_or_model(self):
        adapter = FixedAdapter()
        with patch(
            "api.priority.semantic_route.load_authorized_gmail_new_inbound",
            side_effect=SemanticAuthorityError(
                "incoming_message_routing_excluded",
                409,
            ),
        ) as loader:
            response = self.process(
                gmail_new_inbound_request(
                    provider_message_id="routing-excluded-message"
                ),
                adapter,
                config=NEW_INBOUND_CONFIG,
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.payload["error"]["code"],
            "incoming_message_routing_excluded",
        )
        self.assertEqual(
            set(response.payload["error"]),
            {"code", "message"},
        )
        loader.assert_called_once()
        self.assertEqual(self.redis.commands, [])
        self.assertEqual(adapter.calls, 0)

    def test_new_inbound_resolved_never_inherits_active_suppression_effect(self):
        source = new_inbound_source(
            self.current,
            latest_turn_id="new-inbound-resolved",
            text="Everything has been approved. No further action needed.",
        )
        with (
            patch(
                "api.priority.semantic_route.load_authorized_gmail_new_inbound",
                return_value=source,
            ),
            patch(
                "api.priority.semantic_route.prove_authorized_new_inbound_source_current",
                return_value=None,
            ),
        ):
            response = self.process(
                gmail_new_inbound_request(
                    provider_message_id="new-inbound-resolved"
                ),
                FixedAdapter(ASSESSMENT),
                config=NEW_INBOUND_CONFIG,
            )

        self.assertEqual(response.payload["assessment"]["state"], "resolved")
        self.assertEqual(response.payload["effectiveSemanticState"], "resolved")
        self.assertEqual(response.payload["priorityEffect"], "observe_only")
        self.assertNotIn("semanticMode", response.payload)

    def test_new_inbound_shadow_can_run_while_open_loop_semantics_are_off(self):
        source = new_inbound_source(
            self.current,
            latest_turn_id="new-inbound-only",
        )
        adapter = FixedAdapter(
            SemanticAssessment(
                state=SemanticState.NEEDS_USER_ACTION,
                confidence=0.95,
                reason_code=SemanticReasonCode.EXPLICIT_REQUEST,
            )
        )
        with (
            patch(
                "api.priority.semantic_route.load_authorized_gmail_new_inbound",
                return_value=source,
            ),
            patch(
                "api.priority.semantic_route.prove_authorized_new_inbound_source_current",
                return_value=None,
            ),
        ):
            response = self.process(
                gmail_new_inbound_request(
                    provider_message_id="new-inbound-only"
                ),
                adapter,
                config=NEW_INBOUND_ONLY_CONFIG,
            )

        self.assertFalse(NEW_INBOUND_ONLY_CONFIG.enabled)
        self.assertTrue(NEW_INBOUND_ONLY_CONFIG.new_inbound_enabled)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.payload["newInboundMode"], "shadow")
        self.assertEqual(response.payload["priorityEffect"], "observe_only")
        self.assertEqual(adapter.calls, 1)

    def test_new_inbound_off_is_independent_from_open_loop_active_and_does_no_work(self):
        adapter = FixedAdapter()
        with (
            patch(
                "api.priority.semantic_route.load_authorized_gmail_new_inbound",
                side_effect=AssertionError("provider authority must stay off"),
            ) as loader,
            patch(
                "api.priority.semantic_route.resolve_priority_hmac_secret",
                side_effect=AssertionError("HMAC resolution must stay off"),
            ),
        ):
            response = process_semantic_request(
                [],
                gmail_new_inbound_request(),
                config=ACTIVE_CONFIG,
                store=self.store,
                adapter=adapter,
                now=11,
            )

        self.assertTrue(ACTIVE_CONFIG.enabled)
        self.assertTrue(ACTIVE_CONFIG.can_mutate_priority)
        self.assertFalse(ACTIVE_CONFIG.new_inbound_enabled)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.payload["status"], "deferred")
        self.assertEqual(response.payload["semanticTrigger"], "new_inbound")
        self.assertEqual(response.payload["newInboundMode"], "off")
        self.assertEqual(response.payload["priorityEffect"], "observe_only")
        self.assertNotIn("semanticMode", response.payload)
        loader.assert_not_called()
        self.provider_proof.assert_not_called()
        self.assertEqual(self.redis.commands, [])
        self.assertEqual(adapter.calls, 0)

    def test_new_inbound_custom_imap_uses_exact_ref_less_locator(self):
        current = custom_authority()
        self.authority_resolver.return_value = current
        source = new_inbound_source(
            current,
            latest_turn_id="new-message@example.net",
        )
        adapter = FixedAdapter(
            SemanticAssessment(
                state=SemanticState.NEEDS_USER_ACTION,
                confidence=0.91,
                reason_code=SemanticReasonCode.EXPLICIT_REQUEST,
            )
        )
        with (
            patch(
                "api.priority.semantic_route.load_authorized_imap_new_inbound",
                return_value=source,
            ) as loader,
            patch(
                "api.priority.semantic_route.prove_authorized_new_inbound_source_current",
                return_value=None,
            ) as proof,
        ):
            response = self.process(
                custom_new_inbound_request(),
                adapter,
                config=NEW_INBOUND_CONFIG,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.payload["newInboundMode"], "shadow")
        self.assertEqual(response.payload["priorityEffect"], "observe_only")
        loader.assert_called_once_with(
            [],
            current,
            provider_folder="INBOX",
            uid_validity="7",
            imap_uid="11",
        )
        proof.assert_called_once_with([], source)
        self.provider_proof.assert_not_called()

    def test_new_inbound_rejects_lookup_text_refs_and_authority_extras(self):
        forbidden_fields = {
            "operation": "lookup_current",
            "activeEventRef": "signed-but-not-allowed",
            "authoredText": "client text",
            "subject": "client subject",
            "sender": "external@example.net",
            "workspaceId": WORKSPACE_ID,
            "userId": USER_ID,
        }
        for key, value in forbidden_fields.items():
            with self.subTest(key=key):
                payload = gmail_new_inbound_request()
                payload[key] = value
                self.authority_resolver.reset_mock()
                response = self.process(
                    payload,
                    FixedAdapter(),
                    config=NEW_INBOUND_CONFIG,
                )
                self.assertEqual(response.status_code, 400)
                self.authority_resolver.assert_not_called()

    def test_custom_incoming_rejects_refs_text_roots_and_authority_extras(self):
        forbidden_fields = {
            "activeEventRef": "legacy-ref",
            "authoredText": "client text",
            "conversationRoot": "root@example.net",
            "workspaceId": WORKSPACE_ID,
            "userId": USER_ID,
        }
        for key, value in forbidden_fields.items():
            with self.subTest(key=key):
                payload = custom_incoming_request()
                payload[key] = value
                self.authority_resolver.reset_mock()
                response = self.process(payload, FixedAdapter())
                self.assertEqual(response.status_code, 400)
                self.authority_resolver.assert_not_called()


if __name__ == "__main__":
    unittest.main()
