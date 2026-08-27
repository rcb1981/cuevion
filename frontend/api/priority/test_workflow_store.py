from __future__ import annotations

import json
import unittest

from . import store as store_module
from .authority import PriorityMessageIdentity
from .store import (
    WORKFLOW_REDIS_READ_BATCH_SIZE,
    WORKFLOW_TTL_SECONDS,
    PriorityWorkflowScope,
    PriorityWorkflowStore,
    WorkflowStoreUnavailable,
)


SECRET = "priority-workflow-test-secret-more-than-thirty-two-bytes"


class WorkflowMemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}
        self.commands: list[list[object]] = []
        self.clock_ms = 1_700_000_000_000
        self.unavailable = False

    def __call__(self, command: list[object]) -> dict[str, object]:
        if self.unavailable:
            raise OSError("fixed unavailable")
        self.commands.append(list(command))
        if command[0] == "MGET":
            return {"result": [self.values.get(key) for key in command[1:]]}
        if command[0] != "EVAL" or command[1] != store_module._WRITE_WORKFLOW_RECORD_SCRIPT:
            raise AssertionError(command)
        key_count = int(command[2])
        keys = command[3 : 3 + key_count]
        args = command[3 + key_count :]
        key = keys[0]
        (
            schema_version,
            scope_digest,
            identity_digest,
            field,
            value,
            ttl,
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
                "cleared": "active",
                "waiting": "absent",
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
                        "cleared",
                        "waiting",
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
        record["version"] += 1
        record["updatedAt"] = self.clock_ms
        self.clock_ms += 1
        encoded = json.dumps(record, separators=(",", ":"), sort_keys=True)
        if len(encoded.encode("utf-8")) > int(max_bytes):
            return {"result": corrupt_sentinel}
        self.values[key] = encoded
        self.expirations[key] = int(ttl)
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
        self.assertEqual([command[0] for command in self.redis.commands], ["MGET", "MGET"])

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

    def test_every_write_applies_the_bounded_workflow_ttl(self):
        scope = gmail_scope()
        self.store.write_field(scope, field="cleared", value="cleared")
        key = next(iter(self.redis.values))
        self.assertEqual(self.redis.expirations[key], WORKFLOW_TTL_SECONDS)
        eval_command = self.redis.commands[-1]
        self.assertEqual(eval_command[-4], WORKFLOW_TTL_SECONDS)

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
