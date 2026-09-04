from __future__ import annotations

import json
import unittest

from . import store as store_module
from .authority import PriorityMessageIdentity
from .store import (
    WORKFLOW_CONFIRMATION_MAX_IDENTITIES,
    WORKFLOW_CONFIRMATION_MGET_GROUP_SIZE,
    WORKFLOW_CLEARED_TTL_SECONDS,
    WORKFLOW_MANUAL_TTL_SECONDS,
    WORKFLOW_MAX_SAFE_INTEGER,
    WORKFLOW_PHYSICAL_TTL_SECONDS,
    WORKFLOW_REDIS_READ_BATCH_SIZE,
    WORKFLOW_WAITING_TTL_SECONDS,
    PriorityWorkflowScope,
    PriorityWorkflowStore,
    WorkflowStoreUnavailable,
)


SECRET = "priority-workflow-test-secret-more-than-thirty-two-bytes"


class WorkflowMemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}
        self.physical_expires_at: dict[str, int] = {}
        self.wrong_types: dict[str, str] = {}
        self.commands: list[list[object]] = []
        self.clock_ms = 1_700_000_000_000
        self.unavailable = False

    def __call__(self, command: list[object]) -> dict[str, object]:
        if self.unavailable:
            raise OSError("fixed unavailable")
        self.commands.append(list(command))
        if command[0] == "MGET":
            return {
                "result": [
                    None if key in self.wrong_types else self.values.get(key)
                    for key in command[1:]
                ]
            }
        if command[0] == "EXISTS":
            return {
                "result": sum(
                    key in self.values or key in self.wrong_types
                    for key in command[1:]
                )
            }
        if command[0] == "TYPE":
            key = command[1]
            return {
                "result": (
                    self.wrong_types[key]
                    if key in self.wrong_types
                    else "string"
                    if key in self.values
                    else "none"
                )
            }
        if command[0] != "EVAL":
            raise AssertionError(command)
        key_count = int(command[2])
        keys = command[3 : 3 + key_count]
        args = command[3 + key_count :]
        if command[1] == store_module._READ_WORKFLOW_RECORDS_SCRIPT:
            missing_sentinel = args[0]
            return {
                "result": [
                    self.clock_ms,
                    *(
                        self.values.get(key, missing_sentinel)
                        for key in keys
                    ),
                ]
            }
        if command[1] != store_module._WRITE_WORKFLOW_RECORD_SCRIPT:
            raise AssertionError(command)
        key = keys[0]
        (
            schema_version,
            scope_digest,
            identity_digest,
            field,
            value,
            manual_ttl,
            cleared_ttl,
            waiting_ttl,
            physical_ttl,
            max_bytes,
            max_safe_integer,
            corrupt_sentinel,
        ) = args
        raw = self.values.get(key)
        if raw is None:
            record = {
                "schemaVersion": schema_version,
                "scopeDigest": scope_digest,
                "identityDigest": identity_digest,
                "manualPriority": "none",
                "manualExpiresAt": 0,
                "cleared": "active",
                "clearedExpiresAt": 0,
                "waiting": "absent",
                "waitingExpiresAt": 0,
                "version": 0,
                "updatedAt": 0,
            }
        else:
            try:
                record = json.loads(raw)
                if (
                    type(record) is not dict
                    or set(record)
                    != {
                        "schemaVersion",
                        "scopeDigest",
                        "identityDigest",
                        "manualPriority",
                        "manualExpiresAt",
                        "cleared",
                        "clearedExpiresAt",
                        "waiting",
                        "waitingExpiresAt",
                        "version",
                        "updatedAt",
                    }
                    or record["schemaVersion"] != schema_version
                    or record["scopeDigest"] != scope_digest
                    or record["identityDigest"] != identity_digest
                    or type(record["version"]) is not int
                    or not 1 <= record["version"] < int(max_safe_integer)
                ):
                    raise ValueError
            except Exception:
                return {"result": corrupt_sentinel}
        record[field] = value
        expiry_field, ttl = {
            "manualPriority": ("manualExpiresAt", int(manual_ttl)),
            "cleared": ("clearedExpiresAt", int(cleared_ttl)),
            "waiting": ("waitingExpiresAt", int(waiting_ttl)),
        }[field]
        record[expiry_field] = self.clock_ms + ttl * 1_000
        record["version"] += 1
        record["updatedAt"] = self.clock_ms
        self.clock_ms += 1
        encoded = json.dumps(record, separators=(",", ":"), sort_keys=True)
        if len(encoded.encode("utf-8")) > int(max_bytes):
            return {"result": corrupt_sentinel}
        self.values[key] = encoded
        self.expirations[key] = int(physical_ttl)
        self.physical_expires_at[key] = (
            record["updatedAt"] + int(physical_ttl) * 1_000
        )
        return {"result": encoded}


def gmail_scope(
    *,
    workspace_id: str = "workspace-1",
    user_id: str = "user-1",
    mailbox_id: str = "mailbox-1",
    provider_message_id: str = "gmail-message-1",
) -> PriorityWorkflowScope:
    return PriorityWorkflowScope(
        workspace_id=workspace_id,
        user_id=user_id,
        mailbox_id=mailbox_id,
        identity=PriorityMessageIdentity(
            provider="google",
            provider_message_id=provider_message_id,
        ),
    )


def imap_scope() -> PriorityWorkflowScope:
    return PriorityWorkflowScope(
        workspace_id="workspace-1",
        user_id="user-1",
        mailbox_id="mailbox-imap",
        identity=PriorityMessageIdentity(
            provider="custom_imap",
            provider_folder="INBOX",
            uid_validity="77",
            imap_uid="91",
        ),
    )


class PriorityWorkflowStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.redis = WorkflowMemoryRedis()
        self.store = PriorityWorkflowStore(self.redis, hmac_secret=SECRET)

    def test_private_beta_retention_constants_match_approved_policy(self):
        day = 24 * 60 * 60
        self.assertEqual(WORKFLOW_MANUAL_TTL_SECONDS, 180 * day)
        self.assertEqual(WORKFLOW_CLEARED_TTL_SECONDS, 180 * day)
        self.assertEqual(WORKFLOW_WAITING_TTL_SECONDS, 14 * day)
        self.assertEqual(WORKFLOW_PHYSICAL_TTL_SECONDS, 180 * day)

    def test_missing_batch_records_are_neutral_and_reads_use_no_scans(self):
        scopes = tuple(
            gmail_scope(provider_message_id=f"message-{index}")
            for index in range(WORKFLOW_REDIS_READ_BATCH_SIZE + 1)
        )
        records = self.store.read_records(scopes)
        self.assertEqual(len(records), len(scopes))
        self.assertTrue(all(record.version == 0 for record in records))
        self.assertTrue(all(record.updated_at is None for record in records))
        self.assertTrue(all(record.manual_priority == "none" for record in records))
        self.assertTrue(all(record.cleared == "active" for record in records))
        self.assertTrue(all(record.waiting == "absent" for record in records))
        self.assertEqual([command[0] for command in self.redis.commands], ["EVAL", "EVAL"])
        self.assertTrue(
            all(
                command[1] == store_module._READ_WORKFLOW_RECORDS_SCRIPT
                for command in self.redis.commands
            )
        )

    def test_confirmation_evidence_preserves_mixed_rows_and_exact_storage(self):
        valid_scope = gmail_scope(provider_message_id="evidence-valid")
        missing_scope = gmail_scope(provider_message_id="evidence-missing")
        malformed_scope = gmail_scope(provider_message_id="evidence-malformed")
        wrong_type_scope = gmail_scope(provider_message_id="evidence-wrong-type")
        valid = self.store.write_field(
            valid_scope,
            field="waiting",
            value="returned_reply",
        )
        valid_key = self.store._key(valid_scope)
        missing_key = self.store._key(missing_scope)
        malformed_key = self.store._key(malformed_scope)
        wrong_type_key = self.store._key(wrong_type_scope)
        exact_valid_raw = self.redis.values[valid_key]
        self.redis.values[malformed_key] = "not-json"
        self.redis.wrong_types[wrong_type_key] = "hash"
        self.redis.commands.clear()

        observed_at = valid.updated_at
        evidence = self.store.read_confirmation_evidence(
            (
                valid_scope,
                missing_scope,
                malformed_scope,
                wrong_type_scope,
            ),
            observed_at=observed_at,
        )

        self.assertEqual(
            tuple(item.scope for item in evidence),
            (
                valid_scope,
                missing_scope,
                malformed_scope,
                wrong_type_scope,
            ),
        )
        self.assertEqual(
            tuple(item.key for item in evidence),
            (valid_key, missing_key, malformed_key, wrong_type_key),
        )
        self.assertEqual(evidence[0].raw, exact_valid_raw)
        self.assertEqual(evidence[0].record, valid)
        self.assertTrue(evidence[0].storage_valid)
        self.assertIsNone(evidence[1].raw)
        self.assertEqual(
            evidence[1].record,
            store_module.PriorityWorkflowRecord(),
        )
        self.assertTrue(evidence[1].storage_valid)
        self.assertEqual(evidence[2].raw, "not-json")
        self.assertIsNone(evidence[2].record)
        self.assertFalse(evidence[2].storage_valid)
        self.assertIsNone(evidence[3].raw)
        self.assertIsNone(evidence[3].record)
        self.assertFalse(evidence[3].storage_valid)
        self.assertEqual(
            [command[0] for command in self.redis.commands],
            ["MGET", "EXISTS", "TYPE", "TYPE"],
        )
        self.assertEqual(
            self.redis.commands[1],
            ["EXISTS", missing_key, wrong_type_key],
        )
        self.assertEqual(
            [command[1] for command in self.redis.commands[2:]],
            [missing_key, wrong_type_key],
        )

    def test_confirmation_evidence_groups_mget_and_uses_no_server_time(self):
        scopes = tuple(
            gmail_scope(provider_message_id=f"confirmation-{index}")
            for index in range(WORKFLOW_CONFIRMATION_MGET_GROUP_SIZE * 2 + 1)
        )
        evidence = self.store.read_confirmation_evidence(
            scopes,
            observed_at=1_800_000_000_000,
        )
        self.assertEqual(len(evidence), len(scopes))
        self.assertTrue(all(item.storage_valid for item in evidence))
        self.assertTrue(all(item.raw is None for item in evidence))
        self.assertTrue(
            all(
                item.record == store_module.PriorityWorkflowRecord()
                for item in evidence
            )
        )
        self.assertEqual(
            [command[0] for command in self.redis.commands],
            ["MGET", "MGET", "MGET", "EXISTS"],
        )
        self.assertEqual(
            [len(command) - 1 for command in self.redis.commands[:3]],
            [
                WORKFLOW_CONFIRMATION_MGET_GROUP_SIZE,
                WORKFLOW_CONFIRMATION_MGET_GROUP_SIZE,
                1,
            ],
        )
        self.assertEqual(len(self.redis.commands[-1]) - 1, len(scopes))
        self.assertFalse(any("TIME" in command for command in self.redis.commands))

    def test_confirmation_evidence_normalizes_at_caller_observation(self):
        scope = gmail_scope(provider_message_id="confirmation-observed-at")
        written = self.store.write_field(
            scope,
            field="waiting",
            value="returned_reply",
        )
        key = self.store._key(scope)
        exact_raw = self.redis.values[key]
        self.redis.commands.clear()

        evidence = self.store.read_confirmation_evidence(
            (scope,),
            observed_at=written.waiting_expires_at,
        )[0]

        self.assertEqual(evidence.raw, exact_raw)
        self.assertTrue(evidence.storage_valid)
        self.assertEqual(evidence.record.waiting, "absent")
        self.assertEqual(evidence.record.version, written.version)
        self.assertEqual([command[0] for command in self.redis.commands], ["MGET"])

    def test_confirmation_evidence_rejects_invalid_batches_before_io(self):
        scope = gmail_scope(provider_message_id="confirmation-validation")
        too_many = tuple(
            gmail_scope(provider_message_id=f"confirmation-overflow-{index}")
            for index in range(WORKFLOW_CONFIRMATION_MAX_IDENTITIES + 1)
        )
        invalid_calls = (
            lambda: self.store.read_confirmation_evidence(
                [scope],
                observed_at=1_700_000_000_000,
            ),
            lambda: self.store.read_confirmation_evidence(
                too_many,
                observed_at=1_700_000_000_000,
            ),
            lambda: self.store.read_confirmation_evidence(
                (
                    scope,
                    gmail_scope(provider_message_id="confirmation-validation"),
                ),
                observed_at=1_700_000_000_000,
            ),
            lambda: self.store.read_confirmation_evidence(
                (None,),
                observed_at=1_700_000_000_000,
            ),
            lambda: self.store.read_confirmation_evidence(
                (scope,),
                observed_at=-1,
            ),
            lambda: self.store.read_confirmation_evidence(
                (scope,),
                observed_at=WORKFLOW_MAX_SAFE_INTEGER + 1,
            ),
            lambda: self.store.read_confirmation_evidence(
                (scope,),
                observed_at=True,
            ),
            lambda: self.store.read_confirmation_evidence(
                (scope,),
                observed_at=1_700_000_000_000.0,
            ),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises(ValueError):
                    call()
        self.assertEqual(self.redis.commands, [])

    def test_manual_priority_exact_states_round_trip(self):
        scope = gmail_scope()
        expected_versions = (1, 2, 3)
        for expected, value in zip(expected_versions, ("priority", "removed", "none"), strict=True):
            record = self.store.write_field(
                scope,
                field="manualPriority",
                value=value,
            )
            self.assertEqual(record.manual_priority, value)
            self.assertEqual(record.version, expected)
        self.assertEqual(self.store.read_records((scope,))[0].manual_priority, "none")

    def test_cleared_waiting_and_returned_reply_states_round_trip(self):
        scope = imap_scope()
        cleared = self.store.write_field(scope, field="cleared", value="cleared")
        waiting = self.store.write_field(
            scope,
            field="waiting",
            value="waiting_on_other",
        )
        returned = self.store.write_field(
            scope,
            field="waiting",
            value="returned_reply",
        )
        reopened = self.store.write_field(scope, field="cleared", value="active")
        absent = self.store.write_field(scope, field="waiting", value="absent")
        self.assertEqual(cleared.cleared, "cleared")
        self.assertEqual(waiting.waiting, "waiting_on_other")
        self.assertEqual(returned.waiting, "returned_reply")
        self.assertEqual(reopened.cleared, "active")
        self.assertEqual(absent.waiting, "absent")
        self.assertEqual(absent.version, 5)

    def test_two_sessions_observe_redis_serialized_latest_version_and_time(self):
        first_session = self.store
        second_session = PriorityWorkflowStore(self.redis, hmac_secret=SECRET)
        scope = gmail_scope()
        first = first_session.write_field(
            scope,
            field="manualPriority",
            value="priority",
        )
        second = second_session.write_field(
            scope,
            field="manualPriority",
            value="removed",
        )
        observed = first_session.read_records((scope,))[0]
        self.assertEqual(first.updated_at, 1_700_000_000_000)
        self.assertEqual(second.updated_at, 1_700_000_000_001)
        self.assertEqual((first.version, second.version), (1, 2))
        self.assertEqual(observed, second)

    def test_workspace_user_and_mailbox_scope_are_isolated_and_keys_are_opaque(self):
        owned = gmail_scope()
        self.store.write_field(owned, field="manualPriority", value="priority")
        others = (
            gmail_scope(workspace_id="workspace-2"),
            gmail_scope(user_id="user-2"),
            gmail_scope(mailbox_id="mailbox-2"),
        )
        self.assertTrue(all(record.version == 0 for record in self.store.read_records(others)))
        key = next(iter(self.redis.values))
        self.assertNotIn("workspace-1", key)
        self.assertNotIn("user-1", key)
        self.assertNotIn("mailbox-1", key)
        self.assertNotIn("gmail-message-1", key)

    def test_every_write_applies_the_bounded_physical_workflow_ttl(self):
        scope = gmail_scope()
        self.store.write_field(scope, field="cleared", value="cleared")
        key = next(iter(self.redis.values))
        self.assertEqual(
            self.redis.expirations[key],
            WORKFLOW_PHYSICAL_TTL_SECONDS,
        )
        eval_command = self.redis.commands[-1]
        self.assertEqual(eval_command[-4], WORKFLOW_PHYSICAL_TTL_SECONDS)

    def test_manual_priority_and_removed_expire_independently_after_180_days(self):
        for index, value in enumerate(("priority", "removed"), start=1):
            with self.subTest(value=value):
                scope = gmail_scope(provider_message_id=f"manual-{index}")
                written = self.store.write_field(
                    scope,
                    field="manualPriority",
                    value=value,
                )
                self.assertEqual(written.manual_priority, value)
                self.assertIsNotNone(written.manual_expires_at)
                self.assertEqual(
                    written.manual_expires_at - written.updated_at,
                    WORKFLOW_MANUAL_TTL_SECONDS * 1_000,
                )
                expiry = written.manual_expires_at
                self.redis.clock_ms = expiry - 1
                self.assertEqual(
                    self.store.read_records((scope,))[0].manual_priority,
                    value,
                )
                self.redis.clock_ms = expiry
                self.assertEqual(
                    self.store.read_records((scope,))[0].manual_priority,
                    "none",
                )

    def test_cleared_expires_to_active_after_180_days(self):
        scope = gmail_scope(provider_message_id="cleared-retention")
        written = self.store.write_field(scope, field="cleared", value="cleared")
        self.assertEqual(
            written.cleared_expires_at - written.updated_at,
            WORKFLOW_CLEARED_TTL_SECONDS * 1_000,
        )
        self.redis.clock_ms = written.cleared_expires_at - 1
        self.assertEqual(self.store.read_records((scope,))[0].cleared, "cleared")
        self.redis.clock_ms = written.cleared_expires_at
        self.assertEqual(self.store.read_records((scope,))[0].cleared, "active")

    def test_waiting_states_expire_to_absent_after_14_days(self):
        for index, value in enumerate(
            ("waiting_on_other", "returned_reply"),
            start=1,
        ):
            with self.subTest(value=value):
                scope = gmail_scope(provider_message_id=f"waiting-{index}")
                written = self.store.write_field(
                    scope,
                    field="waiting",
                    value=value,
                )
                self.assertEqual(
                    written.waiting_expires_at - written.updated_at,
                    WORKFLOW_WAITING_TTL_SECONDS * 1_000,
                )
                self.redis.clock_ms = written.waiting_expires_at - 1
                self.assertEqual(
                    self.store.read_records((scope,))[0].waiting,
                    value,
                )
                self.redis.clock_ms = written.waiting_expires_at
                self.assertEqual(
                    self.store.read_records((scope,))[0].waiting,
                    "absent",
                )

    def test_reads_do_not_refresh_logical_or_physical_retention(self):
        scope = gmail_scope(provider_message_id="read-retention")
        written = self.store.write_field(
            scope,
            field="manualPriority",
            value="priority",
        )
        key = self.store._key(scope)
        raw_before = self.redis.values[key]
        physical_before = self.redis.physical_expires_at[key]
        logical_before = written.manual_expires_at
        self.redis.clock_ms += 30 * 24 * 60 * 60 * 1_000
        observed = self.store.read_records((scope,))[0]
        self.assertEqual(observed.manual_expires_at, logical_before)
        self.assertEqual(self.redis.values[key], raw_before)
        self.assertEqual(self.redis.physical_expires_at[key], physical_before)
        self.assertEqual(
            self.redis.commands[-1][1],
            store_module._READ_WORKFLOW_RECORDS_SCRIPT,
        )

    def test_unrelated_writes_preserve_logical_expiries_while_physical_ttl_refreshes(self):
        scope = gmail_scope(provider_message_id="independent-retention")
        manual = self.store.write_field(
            scope,
            field="manualPriority",
            value="priority",
        )
        key = self.store._key(scope)
        manual_expiry = manual.manual_expires_at
        first_physical_expiry = self.redis.physical_expires_at[key]

        self.redis.clock_ms = manual.updated_at + 170 * 24 * 60 * 60 * 1_000
        waiting = self.store.write_field(
            scope,
            field="waiting",
            value="waiting_on_other",
        )
        self.assertEqual(waiting.manual_expires_at, manual_expiry)
        waiting_expiry = waiting.waiting_expires_at
        self.assertGreater(self.redis.physical_expires_at[key], first_physical_expiry)

        self.redis.clock_ms += 24 * 60 * 60 * 1_000
        cleared = self.store.write_field(scope, field="cleared", value="cleared")
        self.assertEqual(cleared.manual_expires_at, manual_expiry)
        self.assertEqual(cleared.waiting_expires_at, waiting_expiry)
        cleared_expiry = cleared.cleared_expires_at

        self.redis.clock_ms += 24 * 60 * 60 * 1_000
        manual_rewrite = self.store.write_field(
            scope,
            field="manualPriority",
            value="removed",
        )
        self.assertGreater(manual_rewrite.manual_expires_at, manual_expiry)
        self.assertEqual(manual_rewrite.waiting_expires_at, waiting_expiry)
        self.assertEqual(manual_rewrite.cleared_expires_at, cleared_expiry)

    def test_batch_read_normalizes_each_field_from_one_server_time_snapshot(self):
        manual_scope = gmail_scope(provider_message_id="batch-manual")
        waiting_scope = gmail_scope(provider_message_id="batch-waiting")
        manual = self.store.write_field(
            manual_scope,
            field="manualPriority",
            value="priority",
        )
        waiting = self.store.write_field(
            waiting_scope,
            field="waiting",
            value="returned_reply",
        )
        self.redis.clock_ms = waiting.waiting_expires_at
        records = self.store.read_records((manual_scope, waiting_scope))
        self.assertEqual(records[0].manual_priority, "priority")
        self.assertEqual(records[1].waiting, "absent")
        self.assertLess(self.redis.clock_ms, manual.manual_expires_at)

    def test_expiry_is_derived_only_from_redis_server_time(self):
        scope = gmail_scope(provider_message_id="server-time")
        self.redis.clock_ms = 2_000_000_000_000
        written = self.store.write_field(
            scope,
            field="manualPriority",
            value="priority",
        )
        self.assertEqual(written.updated_at, 2_000_000_000_000)
        self.assertEqual(
            written.manual_expires_at,
            2_000_000_000_000 + WORKFLOW_MANUAL_TTL_SECONDS * 1_000,
        )

    def test_corrupt_or_unavailable_storage_fails_closed(self):
        scope = gmail_scope()
        key = self.store._key(scope)
        self.redis.values[key] = "not-json"
        with self.assertRaises(WorkflowStoreUnavailable):
            self.store.read_records((scope,))
        with self.assertRaises(WorkflowStoreUnavailable):
            self.store.write_field(scope, field="cleared", value="cleared")
        self.redis.unavailable = True
        with self.assertRaises(WorkflowStoreUnavailable):
            self.store.read_records((gmail_scope(provider_message_id="other"),))

    def test_invalid_fields_values_and_batch_shape_are_rejected_before_io(self):
        scope = gmail_scope()
        with self.assertRaises(ValueError):
            self.store.write_field(scope, field="unknown", value="priority")
        with self.assertRaises(ValueError):
            self.store.write_field(scope, field="cleared", value="yes")
        with self.assertRaises(ValueError):
            self.store.read_records([scope])
        self.assertEqual(self.redis.commands, [])


if __name__ == "__main__":
    unittest.main()
