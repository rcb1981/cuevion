from __future__ import annotations

import unittest
from unittest.mock import patch

from .authority import PriorityAuthority, SemanticAuthorityError
from .candidate_reference_reconciliation import (
    CandidateReferenceReconciliationResult,
    workflow_reference_expiries,
)
from .candidate_store import PriorityCandidateStore
from .store import PriorityWorkflowScope, PriorityWorkflowStore
from .test_candidate_recovery import FakeRecoveryStore, recovery_scope
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
        self.recovery_store = FakeRecoveryStore()
        self.authority_patch = patch(
            "api.priority.workflow_route.resolve_priority_authority",
            return_value=self.current,
        )
        self.authority_resolver = self.authority_patch.start()
        self.addCleanup(self.authority_patch.stop)

    def process(
        self,
        payload: dict,
        *,
        store=None,
        candidate_store=None,
        recovery_store=None,
    ):
        return process_priority_workflow_request(
            (("Cookie", "session=opaque"),),
            payload,
            store=self.store if store is None else store,
            candidate_store=(
                self.candidate_store
                if candidate_store is None
                else candidate_store
            ),
            recovery_store=(
                self.recovery_store
                if recovery_store is None
                else recovery_store
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
        candidate_commands = len(self.candidate_redis.commands)
        workflow_commands = len(self.redis.commands)
        with self.assertLogs(
            "api.priority.workflow_route",
            level="INFO",
        ) as captured:
            result = self.process(
                write_payload("set_manual_priority", "priority")
            )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(
            captured.output,
            [
                "INFO:api.priority.workflow_route:"
                "Priority workflow candidate reference reconciliation "
                "outcome=candidate_reference_reconciled"
            ],
        )
        self.assertEqual(len(self.redis.commands) - workflow_commands, 1)
        self.assertEqual(
            len(self.candidate_redis.commands) - candidate_commands,
            4,
        )
        reconciled = self.candidate_store.read_candidate(candidate_scope)
        assert reconciled is not None
        self.assertEqual(reconciled.version, candidate.version + 1)
        self.assertEqual(
            reconciled.positive_reference_expires_at("manual_priority"),
            reconciled.absolute_expires_at,
        )

    def test_missing_candidate_queues_waiting_with_authoritative_expiry(self):
        candidate_scope = google_scope(
            message_id="gmail-message-1",
            workspace_id="workspace-1",
            user_id="user-1",
            mailbox_id="mailbox-1",
            account="primary@example.com",
        )
        candidate_commands = len(self.candidate_redis.commands)
        workflow_commands = len(self.redis.commands)
        with self.assertLogs(
            "api.priority.workflow_route",
            level="INFO",
        ) as captured, self.assertLogs(
            "api.priority.candidate_recovery",
            level="INFO",
        ) as recovery_captured:
            result = self.process(
                write_payload("set_waiting", "waiting_on_other")
            )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(
            captured.output,
            [
                "INFO:api.priority.workflow_route:"
                "Priority workflow candidate reference reconciliation "
                "outcome=candidate_missing"
            ],
        )
        self.assertEqual(
            recovery_captured.output,
            [
                "INFO:api.priority.candidate_recovery:"
                "Priority workflow recovery queue synchronization "
                "outcome=recovery_queued"
            ],
        )
        self.assertEqual(len(self.redis.commands) - workflow_commands, 1)
        self.assertEqual(
            len(self.candidate_redis.commands) - candidate_commands,
            1,
        )
        self.assertEqual(result.payload["record"]["waiting"], "waiting_on_other")
        self.assertIsNone(self.candidate_store.read_candidate(candidate_scope))
        queued = self.recovery_store.records[recovery_scope()]
        workflow_record = self.store.read_records(
            (
                PriorityWorkflowScope(
                    workspace_id="workspace-1",
                    user_id="user-1",
                    mailbox_id="mailbox-1",
                    identity=recovery_scope().identity,
                ),
            )
        )[0]
        self.assertEqual(
            queued.authority_expires_at,
            max(workflow_reference_expiries(workflow_record)),
        )
        self.assertFalse(
            any(command[0] == "SCAN" for command in self.candidate_redis.commands)
        )

    def test_missing_candidate_queues_manual_and_returned_but_not_neutral(self):
        manual_identity = {
            "provider": "google",
            "providerMessageId": "manual",
        }
        returned_identity = {
            "provider": "google",
            "providerMessageId": "returned",
        }
        neutral_identity = {
            "provider": "google",
            "providerMessageId": "neutral",
        }
        manual = self.process(
            write_payload(
                "set_manual_priority",
                "priority",
                identity=manual_identity,
            )
        )
        returned = self.process(
            write_payload(
                "set_waiting",
                "returned_reply",
                identity=returned_identity,
            )
        )
        neutral = self.process(
            write_payload(
                "set_manual_priority",
                "removed",
                identity=neutral_identity,
            )
        )
        self.assertEqual(
            (manual.status_code, returned.status_code, neutral.status_code),
            (200, 200, 200),
        )
        self.assertIn(recovery_scope("manual"), self.recovery_store.records)
        self.assertIn(recovery_scope("returned"), self.recovery_store.records)
        self.assertNotIn(recovery_scope("neutral"), self.recovery_store.records)

    def test_workflow_writes_do_not_invoke_provider_network_adapters(self):
        with patch("urllib.request.urlopen") as gmail_network, patch(
            "imaplib.IMAP4_SSL"
        ) as imap_network:
            gmail = self.process(
                write_payload("set_manual_priority", "priority")
            )
            self.authority_resolver.return_value = authority(
                provider="custom_imap"
            )
            imap = self.process(
                write_payload(
                    "set_waiting",
                    "waiting_on_other",
                    identity=IMAP_IDENTITY,
                )
            )

        self.assertEqual((gmail.status_code, imap.status_code), (200, 200))
        gmail_network.assert_not_called()
        imap_network.assert_not_called()

    def test_neutral_writes_cancel_queued_items_but_remaining_manual_updates(self):
        removed_identity = {
            "provider": "google",
            "providerMessageId": "removed",
        }
        cleared_identity = {
            "provider": "google",
            "providerMessageId": "cleared",
        }
        retained_identity = {
            "provider": "google",
            "providerMessageId": "retained",
        }
        for identity in (removed_identity, cleared_identity, retained_identity):
            self.process(
                write_payload(
                    "set_manual_priority",
                    "priority",
                    identity=identity,
                )
            )

        self.process(
            write_payload(
                "set_manual_priority",
                "removed",
                identity=removed_identity,
            )
        )
        self.process(
            write_payload("set_cleared", "cleared", identity=cleared_identity)
        )
        self.process(
            write_payload(
                "set_waiting",
                "waiting_on_other",
                identity=retained_identity,
            )
        )
        retained_before = self.recovery_store.records[recovery_scope("retained")]
        self.process(
            write_payload("set_waiting", "absent", identity=retained_identity)
        )
        retained_after = self.recovery_store.records[recovery_scope("retained")]

        self.assertNotIn(recovery_scope("removed"), self.recovery_store.records)
        self.assertNotIn(recovery_scope("cleared"), self.recovery_store.records)
        self.assertIn(recovery_scope("retained"), self.recovery_store.records)
        self.assertGreater(retained_after.generation, retained_before.generation)
        self.assertGreater(
            retained_after.authority_expires_at,
            retained_after.updated_at,
        )

    def test_reconciled_candidate_cancels_stale_recovery_item(self):
        first = self.process(write_payload("set_manual_priority", "priority"))
        self.assertEqual(first.status_code, 200)
        self.assertIn(recovery_scope(), self.recovery_store.records)
        self.candidate_redis.current_ms = first.payload["record"]["updatedAt"]
        self.candidate_store.upsert_confirmed(
            google_scope(
                workspace_id="workspace-1",
                user_id="user-1",
                mailbox_id="mailbox-1",
                account="primary@example.com",
            ),
            snapshot(),
            expected_version=0,
        )
        second = self.process(write_payload("set_waiting", "waiting_on_other"))
        self.assertEqual(second.status_code, 200)
        self.assertNotIn(recovery_scope(), self.recovery_store.records)
        self.assertIn(recovery_scope(), self.recovery_store.cancelled)

    def test_queue_unavailable_and_capacity_do_not_change_workflow_success(self):
        unavailable = FakeRecoveryStore()
        unavailable.unavailable = True
        with self.assertLogs(
            "api.priority.candidate_recovery",
            level="WARNING",
        ) as unavailable_logs:
            unavailable_result = self.process(
                write_payload("set_manual_priority", "priority"),
                recovery_store=unavailable,
            )

        capacity = FakeRecoveryStore()
        capacity.capacity = "mailbox"
        with self.assertLogs(
            "api.priority.candidate_recovery",
            level="WARNING",
        ) as capacity_logs:
            capacity_result = self.process(
                write_payload(
                    "set_waiting",
                    "waiting_on_other",
                    identity={
                        "provider": "google",
                        "providerMessageId": "capacity",
                    },
                ),
                recovery_store=capacity,
            )

        self.assertEqual(unavailable_result.status_code, 200)
        self.assertEqual(capacity_result.status_code, 200)
        self.assertEqual(
            set(unavailable_result.payload),
            {"ok", "status", "record"},
        )
        self.assertEqual(
            set(capacity_result.payload),
            {"ok", "status", "record"},
        )
        self.assertIn(
            "outcome=queue_unavailable",
            "\n".join(unavailable_logs.output),
        )
        self.assertIn(
            "outcome=queue_capacity",
            "\n".join(capacity_logs.output),
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

    def test_every_fixed_workflow_reconciliation_outcome_uses_bounded_level(
        self,
    ):
        cases = (
            (
                CandidateReferenceReconciliationResult.RECONCILED,
                "INFO",
            ),
            (
                CandidateReferenceReconciliationResult.CANDIDATE_MISSING,
                "INFO",
            ),
            (
                CandidateReferenceReconciliationResult.CANDIDATE_INELIGIBLE,
                "WARNING",
            ),
            (
                CandidateReferenceReconciliationResult.CAS_CONFLICT_EXHAUSTED,
                "WARNING",
            ),
            (
                CandidateReferenceReconciliationResult.STORE_UNAVAILABLE,
                "WARNING",
            ),
        )
        for outcome, level in cases:
            with self.subTest(outcome=outcome.value):
                candidate_commands = len(self.candidate_redis.commands)
                workflow_commands = len(self.redis.commands)
                with patch(
                    "api.priority.workflow_route.reconcile_workflow_candidate_references",
                    return_value=outcome,
                ) as reconcile, self.assertLogs(
                    "api.priority.workflow_route",
                    level=level,
                ) as captured:
                    result = self.process(
                        write_payload("set_manual_priority", "priority")
                    )
                self.assertEqual(result.status_code, 200)
                self.assertEqual(
                    set(result.payload),
                    {"ok", "status", "record"},
                )
                self.assertNotIn("reconciliation", result.payload)
                self.assertEqual(
                    captured.output,
                    [
                        f"{level}:api.priority.workflow_route:"
                        "Priority workflow candidate reference reconciliation "
                        f"outcome={outcome.value}"
                    ],
                )
                self.assertEqual(reconcile.call_count, 1)
                self.assertEqual(len(self.redis.commands) - workflow_commands, 1)
                self.assertEqual(
                    len(self.candidate_redis.commands) - candidate_commands,
                    0,
                )

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

    def test_reconciliation_log_excludes_exact_workflow_scope_and_storage_data(
        self,
    ):
        mailbox_id = "privacy-mailbox-id"
        provider_folder = "privacy-provider-folder"
        uid_validity = "87654321"
        imap_uid = "12345678"
        self.authority_resolver.return_value = authority(
            mailbox_id=mailbox_id,
            provider="custom_imap",
        )
        payload = {
            "operation": "set_waiting",
            "mailboxId": mailbox_id,
            "identity": {
                "provider": "custom_imap",
                "providerFolder": provider_folder,
                "uidValidity": uid_validity,
                "imapUid": imap_uid,
            },
            "value": "waiting_on_other",
        }
        with patch(
            "api.priority.workflow_route.reconcile_workflow_candidate_references",
            return_value=(
                CandidateReferenceReconciliationResult.CANDIDATE_INELIGIBLE
            ),
        ), self.assertLogs(
            "api.priority.workflow_route",
            level="WARNING",
        ) as captured, self.assertLogs(
            "api.priority.candidate_recovery",
            level="WARNING",
        ) as recovery_captured:
            result = self.process(payload)
        self.assertEqual(result.status_code, 200)
        output = "\n".join(captured.output)
        self.assertEqual(
            output,
            "WARNING:api.priority.workflow_route:"
            "Priority workflow candidate reference reconciliation "
            "outcome=candidate_ineligible",
        )
        recovery_output = "\n".join(recovery_captured.output)
        self.assertEqual(
            recovery_output,
            "WARNING:api.priority.candidate_recovery:"
            "Priority workflow recovery queue synchronization "
            "outcome=recovery_not_synchronized",
        )
        redis_key = next(iter(self.redis.values))
        hmac_digest = redis_key.rsplit(":", 1)[-1]
        for sensitive in (
            "privacy-sender@example.com",
            mailbox_id,
            provider_folder,
            uid_validity,
            imap_uid,
            redis_key,
            hmac_digest,
            SECRET,
        ):
            self.assertNotIn(sensitive, output)
            self.assertNotIn(sensitive, recovery_output)

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
