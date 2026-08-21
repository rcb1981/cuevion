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
from .semantic_config import SemanticMode, SemanticRuntimeConfig
from .semantic_config import load_semantic_runtime_config
from .semantic_errors import SemanticProviderTimeoutError
from .semantic_route import process_semantic_request
from .semantic_types import (
    SEMANTIC_SCHEMA_VERSION,
    SemanticAssessment,
    SemanticReasonCode,
    SemanticState,
)
from .store import SemanticAssessmentStore, SemanticCacheScope
from .test_store import MemoryRedis


def _account(prefix: str, byte: int) -> str:
    suffix = base64.urlsafe_b64encode(bytes([byte]) * 16).rstrip(b"=").decode("ascii")
    return prefix + suffix


SECRET = "priority-test-secret-with-more-than-thirty-two-bytes"
WORKSPACE_ID = _account("wsp_", 1)
USER_ID = _account("usr_", 2)
AUTHORED_TEXT = "Everything is complete from my side."
CONFIG = SemanticRuntimeConfig(mode=SemanticMode.SHADOW, model="test-model")


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

    def assess(self, _window):
        self.calls += 1
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

    def process(self, payload: dict, adapter, *, now: int = 11) -> object:
        return process_semantic_request(
            [],
            payload,
            config=CONFIG,
            hmac_secret=SECRET,
            store=self.store,
            adapter=adapter,
            now=now,
        )

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
            },
        )
        self.assertEqual(
            set(first.payload["identity"]),
            {"mailboxId", "conversationId", "latestTurnId", "semanticVersion"},
        )
        self.assertEqual(first.payload["assessment"], ASSESSMENT.to_wire_dict())
        self.assertEqual(first.payload["effectiveSemanticState"], "resolved")
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
        for reference in forged_references:
            with self.subTest(reference=reference[:12]):
                self.redis.commands.clear()
                self.provider_proof.reset_mock()
                adapter = FixedAdapter()
                response = self.process(request(reference), adapter)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.payload["error"]["code"], "event_scope_mismatch")
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

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.payload["status"], "assessed")
        self.assertEqual(cached_response.status_code, 200)
        self.assertEqual(cached_response.payload["status"], "cached")
        self.assertNotIn("activeEventRef", response.payload)
        self.assertNotIn("activeEventRef", cached_response.payload)
        self.assertEqual(loader.call_count, 2)
        loader.assert_called_with(
            [], current, provider_folder="INBOX", uid_validity="7", imap_uid="9"
        )
        self.assertEqual(self.provider_proof.call_count, 2)
        self.provider_proof.assert_called_with([], source, None)
        self.assertEqual(adapter.calls, 1)

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
