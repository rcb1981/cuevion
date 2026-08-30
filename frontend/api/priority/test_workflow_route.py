from __future__ import annotations

import unittest
from unittest.mock import patch

from .authority import PriorityAuthority, SemanticAuthorityError
from .candidate_reference_reconciliation import (
    CandidateReferenceReconciliationResult,
)
from .candidate_store import PriorityCandidateStore
from .store import PriorityWorkflowStore
from .test_candidate_store import MemoryRedis, google_scope, snapshot
from .test_workflow_store import SECRET, WorkflowMemoryRedis
from .workflow_route import process_priority_workflow_request


def authority(
    *,
    workspace_id: str = "workspace-1",
    user_id: str = "user-1",
    mailbox_id: str = "mailbox-1",
    provider: str = "google",
) -> PriorityAuthority:
    inbox = {
        "id": mailbox_id,
        "provider": provider,
        "email": "primary@example.com",
        "connected": True,
        "connectionStatus": "connected",
    }
    return PriorityAuthority(
        workspace_id=workspace_id,
        user_id=user_id,
        member_email="owner@example.com",
        mailbox_id=mailbox_id,
        provider=provider,
        mailbox_email="primary@example.com",
        owned_emails=frozenset({"owner@example.com", "primary@example.com"}),
        user_record={"email": "owner@example.com"},
        inbox_record=inbox,
    )


GMAIL_IDENTITY = {"provider": "google", "providerMessageId": "gmail-message-1"}
IMAP_IDENTITY = {
    "provider": "custom_imap",
    "providerFolder": "INBOX",
    "uidValidity": "77",
    "imapUid": "91",
}


def read_payload(*identities: dict, mailbox_id: str = "mailbox-1") -> dict:
    return {
        "operation": "read",
        "mailboxId": mailbox_id,
        "identities": list(identities or (GMAIL_IDENTITY,)),
    }


def write_payload(operation: str, value: str, *, identity: dict | None = None) -> dict:
    return {
        "operation": operation,
        "mailboxId": "mailbox-1",
        "identity": identity or GMAIL_IDENTITY,
        "value": value,
    }


class PriorityWorkflowRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current = authority()
        self.redis = WorkflowMemoryRedis()
        self.store = PriorityWorkflowStore(self.redis, hmac_secret=SECRET)
        self.candidate_redis = MemoryRedis(current_ms=self.redis.clock_ms)
        self.candidate_store = PriorityCandidateStore(
            self.candidate_redis,
            hmac_secret=SECRET,
        )
        self.authority_patch = patch(
            "api.priority.workflow_route.resolve_priority_authority",
            return_value=self.current,
        )
        self.authority_resolver = self.authority_patch.start()
        self.addCleanup(self.authority_patch.stop)

    def process(self, payload: dict, *, store=None, candidate_store=None):
        return process_priority_workflow_request(
            (("Cookie", "session=opaque"),),
            payload,
            store=self.store if store is None else store,
            candidate_store=(
                self.candidate_store
                if candidate_store is None
                else candidate_store
            ),
        )

    def test_unauthenticated_request_is_rejected_before_storage(self):
        self.authority_resolver.side_effect = SemanticAuthorityError("unauthorized", 401)
        result = self.process(read_payload())
        self.assertEqual(result.status_code, 401)
        self.assertEqual(result.payload["error"]["code"], "unauthorized")
        self.assertEqual(self.redis.commands, [])

    def test_server_derived_user_workspace_and_mailbox_scope_isolates_sessions(self):
        write = self.process(write_payload("set_manual_priority", "priority"))
        self.assertEqual(write.status_code, 200)
        self.authority_resolver.return_value = authority(
            workspace_id="workspace-2",
            user_id="user-2",
        )
        isolated = self.process(read_payload())
        record = isolated.payload["records"][0]
        self.assertEqual(record["manualPriority"], "none")
        self.assertEqual(record["version"], 0)
        self.assertIsNone(record["updatedAt"])
        self.assertNotIn("workspaceId", record)
        self.assertNotIn("userId", record)

    def test_unowned_mailbox_is_rejected_before_storage(self):
        self.authority_resolver.side_effect = SemanticAuthorityError(
            "mailbox_not_found",
            404,
        )
        result = self.process(read_payload(mailbox_id="mailbox-other"))
        self.assertEqual(result.status_code, 404)
        self.assertEqual(result.payload["error"]["code"], "mailbox_not_found")
        self.assertEqual(self.redis.commands, [])

    def test_empty_records_and_multi_identity_batch_have_exact_minimal_shape(self):
        second = {"provider": "google", "providerMessageId": "gmail-message-2"}
        result = self.process(read_payload(GMAIL_IDENTITY, second))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(
            result.payload,
            {
                "ok": True,
                "status": "hydrated",
                "records": [
                    {
                        "mailboxId": "mailbox-1",
                        "identity": GMAIL_IDENTITY,
                        "manualPriority": "none",
                        "cleared": "active",
                        "waiting": "absent",
                        "version": 0,
                        "updatedAt": None,
                    },
                    {
                        "mailboxId": "mailbox-1",
                        "identity": second,
                        "manualPriority": "none",
                        "cleared": "active",
                        "waiting": "absent",
                        "version": 0,
                        "updatedAt": None,
                    },
                ],
            },
        )
        self.assertEqual({command[0] for command in self.redis.commands}, {"EVAL"})

    def test_manual_cleared_and_waiting_operations_return_canonical_record(self):
        operations = (
            ("set_manual_priority", "priority", "manualPriority", "priority"),
            ("set_manual_priority", "removed", "manualPriority", "removed"),
            ("set_manual_priority", "none", "manualPriority", "none"),
            ("set_cleared", "cleared", "cleared", "cleared"),
            ("set_cleared", "active", "cleared", "active"),
            ("set_waiting", "waiting_on_other", "waiting", "waiting_on_other"),
            ("set_waiting", "returned_reply", "waiting", "returned_reply"),
            ("set_waiting", "absent", "waiting", "absent"),
        )
        for expected_version, values in enumerate(operations, start=1):
            operation, value, field, expected = values
            result = self.process(write_payload(operation, value))
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.payload["record"][field], expected)
            self.assertEqual(result.payload["record"]["version"], expected_version)
            self.assertIsInstance(result.payload["record"]["updatedAt"], int)
        observed = self.process(read_payload()).payload["records"][0]
        self.assertEqual(observed["version"], len(operations))
        self.assertEqual(observed["manualPriority"], "none")
        self.assertEqual(observed["cleared"], "active")
        self.assertEqual(observed["waiting"], "absent")

    def test_successful_workflow_write_reconciles_existing_exact_candidate(self):
        candidate_scope = google_scope(
            message_id="gmail-message-1",
            workspace_id="workspace-1",
            user_id="user-1",
            mailbox_id="mailbox-1",
            account="primary@example.com",
        )
        candidate = self.candidate_store.upsert_confirmed(
            candidate_scope,
            snapshot(),
            expected_version=0,
        )
        result = self.process(write_payload("set_manual_priority", "priority"))
        self.assertEqual(result.status_code, 200)
        reconciled = self.candidate_store.read_candidate(candidate_scope)
        assert reconciled is not None
        self.assertEqual(reconciled.version, candidate.version + 1)
        self.assertEqual(
            reconciled.positive_reference_expires_at("manual_priority"),
            reconciled.absolute_expires_at,
        )

    def test_missing_candidate_is_noop_and_workflow_write_stays_successful(self):
        candidate_scope = google_scope(
            message_id="gmail-message-1",
            workspace_id="workspace-1",
            user_id="user-1",
            mailbox_id="mailbox-1",
            account="primary@example.com",
        )
        result = self.process(write_payload("set_waiting", "waiting_on_other"))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.payload["record"]["waiting"], "waiting_on_other")
        self.assertIsNone(self.candidate_store.read_candidate(candidate_scope))
        self.assertFalse(
            any(command[0] == "SCAN" for command in self.candidate_redis.commands)
        )

    def test_candidate_store_unavailable_does_not_fail_accepted_workflow_write(
        self,
    ):
        unavailable = PriorityCandidateStore(
            lambda _command: (_ for _ in ()).throw(
                RuntimeError("sensitive-candidate-storage-detail")
            ),
            hmac_secret=SECRET,
        )
        with self.assertLogs(
            "api.priority.workflow_route",
            level="WARNING",
        ) as captured:
            result = self.process(
                write_payload("set_cleared", "cleared"),
                candidate_store=unavailable,
            )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.payload["record"]["cleared"], "cleared")
        self.assertIn("outcome=store_unavailable", "\n".join(captured.output))
        self.assertNotIn(
            "sensitive-candidate-storage-detail",
            "\n".join(captured.output),
        )
        observed = self.process(read_payload()).payload["records"][0]
        self.assertEqual(observed, result.payload["record"])

    def test_exhausted_candidate_cas_does_not_fail_accepted_workflow_write(self):
        with patch(
            "api.priority.workflow_route.reconcile_workflow_candidate_references",
            return_value=(
                CandidateReferenceReconciliationResult.CAS_CONFLICT_EXHAUSTED
            ),
        ), self.assertLogs(
            "api.priority.workflow_route",
            level="WARNING",
        ) as captured:
            result = self.process(write_payload("set_manual_priority", "priority"))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.payload["record"]["manualPriority"], "priority")
        self.assertIn(
            "outcome=cas_conflict_exhausted",
            "\n".join(captured.output),
        )
        observed = self.process(read_payload()).payload["records"][0]
        self.assertEqual(observed, result.payload["record"])

    def test_imap_mailbox_accepts_only_exact_imap_identity(self):
        self.authority_resolver.return_value = authority(provider="custom_imap")
        valid = self.process(read_payload(IMAP_IDENTITY))
        self.assertEqual(valid.status_code, 200)
        mismatch = self.process(read_payload(GMAIL_IDENTITY))
        self.assertEqual(mismatch.status_code, 400)
        self.assertEqual(mismatch.payload["error"]["code"], "invalid_message_identity")

    def test_unknown_fields_client_time_version_and_oversized_batch_are_rejected(self):
        cases = (
            {**write_payload("set_cleared", "cleared"), "updatedAt": 1},
            {**write_payload("set_cleared", "cleared"), "version": 99},
            {**write_payload("set_cleared", "cleared"), "expiresAt": 99},
            {**write_payload("set_cleared", "cleared"), "clearedExpiresAt": 99},
            {**write_payload("set_cleared", "cleared"), "userId": "user-2"},
            write_payload("set_cleared", "yes"),
            read_payload(GMAIL_IDENTITY, GMAIL_IDENTITY),
            read_payload(
                *(
                    {"provider": "google", "providerMessageId": f"message-{index}"}
                    for index in range(65)
                )
            ),
        )
        for payload in cases:
            with self.subTest(payload=payload):
                result = self.process(payload)
                self.assertEqual(result.status_code, 400)
                self.assertEqual(result.payload["error"]["code"], "invalid_request")
        self.assertEqual(self.redis.commands, [])

    def test_malformed_identifiers_are_rejected_before_authority_or_storage(self):
        malformed = read_payload(
            {
                "provider": "custom_imap",
                "providerFolder": "INBOX\nOther",
                "uidValidity": "7",
                "imapUid": "0",
            }
        )
        result = self.process(malformed)
        self.assertEqual(result.status_code, 400)
        self.assertEqual(self.authority_resolver.call_count, 0)
        self.assertEqual(self.redis.commands, [])

    def test_storage_unavailable_returns_one_bounded_public_error(self):
        self.redis.unavailable = True
        result = self.process(read_payload())
        self.assertEqual(result.status_code, 503)
        self.assertEqual(
            result.payload,
            {
                "ok": False,
                "error": {
                    "code": "workflow_storage_unavailable",
                    "message": "Priority workflow storage is temporarily unavailable.",
                },
            },
        )

    def test_two_sequential_sessions_converge_on_latest_canonical_value(self):
        first_store = self.store
        second_store = PriorityWorkflowStore(self.redis, hmac_secret=SECRET)
        first = self.process(
            write_payload("set_manual_priority", "priority"),
            store=first_store,
        )
        second = self.process(
            write_payload("set_manual_priority", "removed"),
            store=second_store,
        )
        observed = self.process(read_payload(), store=first_store)
        self.assertEqual(first.payload["record"]["version"], 1)
        self.assertEqual(second.payload["record"]["version"], 2)
        self.assertEqual(observed.payload["records"][0], second.payload["record"])


if __name__ == "__main__":
    unittest.main()
