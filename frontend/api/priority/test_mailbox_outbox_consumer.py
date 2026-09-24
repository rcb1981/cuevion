"""Tests for Preview-only mailbox outbox -> Priority consumption."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from api.priority.candidate_projection import PriorityCandidatePopulationAuthority
from api.priority.candidate_store import PriorityCandidateStore
from api.priority.mailbox_outbox import PriorityMailboxOutboxActionKind
from api.priority.mailbox_outbox_consumer import (
    PriorityMailboxOutboxConsumerResult,
    apply_priority_mailbox_outbox_action,
    consume_priority_mailbox_outbox,
    run_preview_priority_mailbox_outbox_consumer,
)
from api.priority.store import PriorityWorkflowStore
from cuevion_mailbox.repository_contract import (
    BodyState,
    MailboxProvider,
    MailboxScope,
    MessageIdentity,
    MessageRecord,
    OutboxEvent,
    OutboxEventType,
    OutboxMessageSnapshot,
    OutboxStorageScope,
)


SECRET = "s" * 64
NOW = 1_790_250_000_000


def authority():
    return PriorityCandidatePopulationAuthority(
        workspace_id="wsp_" + ("a" * 22),
        user_id="usr_" + ("b" * 22),
        mailbox_id="gmail-1",
        mailbox_account_identity="verified@gmail.com",
        provider="google",
    )


def event(
    event_type=OutboxEventType.MESSAGE_CHANGED,
    *,
    row_version=1,
    attempt_count=1,
    suffix="a",
    generation=3,
):
    return OutboxEvent(
        event_id="mbe_" + (suffix * 22),
        scope=OutboxStorageScope(
            workspace_id="wsp_" + ("a" * 22),
            owner_user_id="usr_" + ("b" * 22),
            mailbox_id="gmail-1",
            source_generation=generation,
        ),
        message_id="mbm_" + ("c" * 22),
        message_row_version=row_version,
        event_type=event_type,
        attempt_count=attempt_count,
        claim_token="claim-" + suffix,
    )


def snapshot(
    *,
    row_version=1,
    deleted=False,
    labels=("INBOX", "UNREAD"),
    generation=3,
):
    return OutboxMessageSnapshot(
        scope=MailboxScope(
            workspace_id="wsp_" + ("a" * 22),
            owner_user_id="usr_" + ("b" * 22),
            mailbox_id="gmail-1",
            source_generation=generation,
            provider=MailboxProvider.GOOGLE,
            provider_account_identity="verified@gmail.com",
        ),
        record=MessageRecord(
            identity=MessageIdentity(
                message_id="mbm_" + ("c" * 22),
                provider_message_id="gmail-message-1",
                provider_folder="Inbox",
                imap_uid_validity=None,
                imap_uid=None,
            ),
            provider_thread_id="thread-1",
            provider_labels=tuple(labels),
            rfc_message_id="rfc@example.test",
            in_reply_to=None,
            references=(),
            sender_address="sender@example.test",
            sender_display="Sender",
            to_recipients=("owner@example.test",),
            cc_recipients=(),
            subject="Subject",
            snippet="Snippet",
            provider_timestamp_millis=1_790_236_800_000,
            unread=True,
            starred=False,
            body_state=BodyState.STALE if deleted else BodyState.CACHED,
            metadata_hash="1" * 64,
        ),
        provider_deleted=deleted,
        row_version=row_version,
    )


class FakeReader:
    def __init__(self, snapshots):
        self.snapshots = dict(snapshots)
        self.calls = []

    def resolve_outbox_message(self, claimed):
        self.calls.append(claimed.event_id)
        value = self.snapshots[claimed.event_id]
        if isinstance(value, Exception):
            raise value
        return value


class FakeWriter:
    def __init__(self, claims):
        self.claims = tuple(claims)
        self.claim_scopes = []
        self.processed = []
        self.retried = []
        self.process_result = True
        self.retry_result = True
        self.process_error = None
        self.retry_error = None

    def claim_outbox_batch_for_mailbox(
        self,
        mailbox_scope,
        *,
        limit,
        now_millis,
        lease_millis,
    ):
        self.claim_scopes.append((mailbox_scope, limit, now_millis, lease_millis))
        return self.claims

    def mark_outbox_processed(
        self,
        event_id,
        *,
        claim_token,
        processed_at_millis,
    ):
        self.processed.append((event_id, claim_token, processed_at_millis))
        if self.process_error is not None:
            raise self.process_error
        return self.process_result

    def mark_outbox_retry(
        self,
        event_id,
        *,
        claim_token,
        now_millis,
        next_attempt_at_millis,
        safe_error_code,
    ):
        self.retried.append(
            (
                event_id,
                claim_token,
                now_millis,
                next_attempt_at_millis,
                safe_error_code,
            )
        )
        if self.retry_error is not None:
            raise self.retry_error
        return self.retry_result


class PriorityMailboxOutboxConsumerTests(unittest.TestCase):
    def test_upsert_remove_and_terminal_noops_ack_only_after_apply(self):
        claims = (
            event(OutboxEventType.MESSAGE_CHANGED, suffix="a"),
            event(OutboxEventType.MESSAGE_DELETED, suffix="b"),
            event(OutboxEventType.MESSAGE_CHANGED, suffix="d", row_version=1),
            event(OutboxEventType.MESSAGE_CHANGED, suffix="e", generation=2),
            event(OutboxEventType.MESSAGE_CHANGED, suffix="f"),
        )
        reader = FakeReader(
            {
                claims[0].event_id: snapshot(),
                claims[1].event_id: snapshot(deleted=True),
                claims[2].event_id: snapshot(row_version=2, deleted=True),
                claims[3].event_id: None,
                claims[4].event_id: snapshot(labels=("INBOX", "SENT")),
            }
        )
        writer = FakeWriter(claims)
        applied = []

        def apply_action(action):
            applied.append(action.kind)
            return True

        report = consume_priority_mailbox_outbox(
            authority(),
            reader=reader,
            writer=writer,
            apply_action=apply_action,
            now_millis=NOW,
        )

        self.assertEqual(report.claimed, 5)
        self.assertEqual(report.processed, 5)
        self.assertEqual(report.retried, 0)
        self.assertEqual(
            report.result_counts,
            (
                (PriorityMailboxOutboxConsumerResult.INELIGIBLE.value, 1),
                (PriorityMailboxOutboxConsumerResult.REMOVED.value, 1),
                (PriorityMailboxOutboxConsumerResult.STALE_GENERATION.value, 1),
                (PriorityMailboxOutboxConsumerResult.SUPERSEDED.value, 1),
                (PriorityMailboxOutboxConsumerResult.UPSERTED.value, 1),
            ),
        )
        self.assertEqual(
            applied,
            [
                PriorityMailboxOutboxActionKind.UPSERT,
                PriorityMailboxOutboxActionKind.REMOVE,
                PriorityMailboxOutboxActionKind.SUPERSEDED,
                PriorityMailboxOutboxActionKind.STALE_GENERATION,
                PriorityMailboxOutboxActionKind.INELIGIBLE,
            ],
        )
        self.assertEqual(len(writer.processed), 5)
        self.assertFalse(writer.retried)
        scope = writer.claim_scopes[0][0]
        self.assertEqual(scope.workspace_id, authority().workspace_id)
        self.assertEqual(scope.owner_user_id, authority().user_id)
        self.assertEqual(scope.mailbox_id, authority().mailbox_id)

    def test_side_effect_failure_retries_with_bounded_backoff(self):
        claimed = event(attempt_count=3)
        reader = FakeReader({claimed.event_id: snapshot()})
        writer = FakeWriter((claimed,))

        report = consume_priority_mailbox_outbox(
            authority(),
            reader=reader,
            writer=writer,
            apply_action=lambda _action: False,
            now_millis=NOW,
        )

        self.assertEqual(report.processed, 0)
        self.assertEqual(report.retried, 1)
        self.assertEqual(
            report.result_counts,
            ((PriorityMailboxOutboxConsumerResult.RETRIED.value, 1),),
        )
        self.assertFalse(writer.processed)
        retry = writer.retried[0]
        self.assertEqual(retry[2], NOW)
        self.assertEqual(retry[3], NOW + 20_000)
        self.assertEqual(retry[4], "priority_processing_failed")

    def test_resolver_or_planner_uncertainty_retries_without_ack(self):
        claimed = event()
        reader = FakeReader({claimed.event_id: RuntimeError("storage")})
        writer = FakeWriter((claimed,))

        report = consume_priority_mailbox_outbox(
            authority(),
            reader=reader,
            writer=writer,
            apply_action=lambda _action: self.fail("must not apply"),
            now_millis=NOW,
        )

        self.assertEqual(report.retried, 1)
        self.assertFalse(writer.processed)

    def test_claim_loss_after_side_effect_is_not_retried_in_same_lease(self):
        claimed = event()
        reader = FakeReader({claimed.event_id: snapshot()})
        writer = FakeWriter((claimed,))
        writer.process_result = False
        calls = []

        report = consume_priority_mailbox_outbox(
            authority(),
            reader=reader,
            writer=writer,
            apply_action=lambda action: calls.append(action.kind) or True,
            now_millis=NOW,
        )

        self.assertEqual(calls, [PriorityMailboxOutboxActionKind.UPSERT])
        self.assertEqual(report.processed, 0)
        self.assertEqual(report.retried, 0)
        self.assertEqual(
            report.result_counts,
            ((PriorityMailboxOutboxConsumerResult.CLAIM_LOST.value, 1),),
        )
        self.assertFalse(writer.retried)

    def test_ack_exception_waits_for_lease_expiry_instead_of_double_write(self):
        claimed = event()
        reader = FakeReader({claimed.event_id: snapshot()})
        writer = FakeWriter((claimed,))
        writer.process_error = TimeoutError("uncertain")

        report = consume_priority_mailbox_outbox(
            authority(),
            reader=reader,
            writer=writer,
            apply_action=lambda _action: True,
            now_millis=NOW,
        )

        self.assertEqual(
            report.result_counts,
            ((PriorityMailboxOutboxConsumerResult.ACK_UNAVAILABLE.value, 1),),
        )
        self.assertFalse(writer.retried)

    def test_retry_claim_loss_and_retry_exception_are_observational(self):
        claimed = event()
        for mode in ("lost", "error"):
            with self.subTest(mode=mode):
                reader = FakeReader({claimed.event_id: snapshot()})
                writer = FakeWriter((claimed,))
                if mode == "lost":
                    writer.retry_result = False
                else:
                    writer.retry_error = TimeoutError("uncertain")
                report = consume_priority_mailbox_outbox(
                    authority(),
                    reader=reader,
                    writer=writer,
                    apply_action=lambda _action: False,
                    now_millis=NOW,
                )
                expected = (
                    PriorityMailboxOutboxConsumerResult.CLAIM_LOST
                    if mode == "lost"
                    else PriorityMailboxOutboxConsumerResult.RETRY_UNAVAILABLE
                )
                self.assertEqual(
                    report.result_counts,
                    ((expected.value, 1),),
                )
                self.assertEqual(report.retried, 0)

    def test_duplicate_or_cross_tenant_claim_batch_fails_closed(self):
        first = event()
        duplicate = event()
        writer = FakeWriter((first, duplicate))
        reader = FakeReader({first.event_id: snapshot()})
        with self.assertRaises(RuntimeError):
            consume_priority_mailbox_outbox(
                authority(),
                reader=reader,
                writer=writer,
                apply_action=lambda _action: True,
                now_millis=NOW,
            )

        foreign = OutboxEvent(
            event_id="mbe_" + ("z" * 22),
            scope=OutboxStorageScope(
                workspace_id="wsp_" + ("z" * 22),
                owner_user_id=authority().user_id,
                mailbox_id=authority().mailbox_id,
                source_generation=1,
            ),
            message_id="mbm_" + ("z" * 22),
            message_row_version=1,
            event_type=OutboxEventType.MESSAGE_ADDED,
            attempt_count=1,
            claim_token="foreign",
        )
        with self.assertRaises(RuntimeError):
            consume_priority_mailbox_outbox(
                authority(),
                reader=FakeReader({}),
                writer=FakeWriter((foreign,)),
                apply_action=lambda _action: True,
                now_millis=NOW,
            )

    def test_runtime_boundary_rejects_non_preview_active_write(self):
        common = dict(
            workspace_id=authority().workspace_id,
            owner_user_id=authority().user_id,
            mailbox_id=authority().mailbox_id,
            mailbox_account_identity=authority().mailbox_account_identity,
            now_millis=NOW,
        )
        for environment in (
            {"VERCEL_ENV": "production", "CUEVION_MAILBOX_POSTGRES_MODE": "active_write"},
            {"VERCEL_ENV": "preview", "CUEVION_MAILBOX_POSTGRES_MODE": "active_read"},
        ):
            with self.subTest(environment=environment):
                with self.assertRaises(RuntimeError):
                    run_preview_priority_mailbox_outbox_consumer(
                        environment=environment,
                        **common,
                    )


class PriorityMailboxOutboxRouteWiringTests(unittest.TestCase):
    def test_gmail_route_runs_consumer_only_inside_preview_active_write_and_before_recovery(self):
        route = (
            Path(__file__).resolve().parents[1]
            / "inboxes"
            / "fetch-gmail.py"
        ).read_text(encoding="utf-8")
        gate = "        if preview_active_write_enabled(os.environ):\n"
        consumer = "run_preview_priority_mailbox_outbox_consumer("
        recovery = "_run_gmail_priority_candidate_recovery("
        consumer_index = route.rfind(consumer)
        self.assertGreaterEqual(consumer_index, 0)
        gate_index = route.rfind(gate, 0, consumer_index)
        self.assertGreaterEqual(gate_index, 0)
        recovery_index = route.rfind(recovery)
        self.assertGreater(recovery_index, consumer_index)
        self.assertIn(
            'print("cuevion_mailbox_active_write priority_outbox_failed")',
            route,
        )


class PriorityMailboxOutboxActionApplicationTests(unittest.TestCase):
    def setUp(self):
        transport = lambda _command: {"result": None}
        self.candidate_store = PriorityCandidateStore(
            transport,
            hmac_secret=SECRET,
        )
        self.workflow_store = PriorityWorkflowStore(
            transport,
            hmac_secret=SECRET,
        )

    def test_remove_is_idempotent_even_when_candidate_is_already_absent(self):
        action = __import__(
            "api.priority.mailbox_outbox",
            fromlist=["plan_priority_mailbox_outbox_event"],
        ).plan_priority_mailbox_outbox_event(
            authority(),
            event(OutboxEventType.MESSAGE_DELETED),
            snapshot(deleted=True),
        )
        with patch.object(
            PriorityCandidateStore,
            "remove_candidate",
            return_value=False,
        ) as remove:
            self.assertTrue(
                apply_priority_mailbox_outbox_action(
                    authority(),
                    action,
                    candidate_store=self.candidate_store,
                    workflow_store=self.workflow_store,
                )
            )
        remove.assert_called_once_with(action.candidate_scope)

    def test_upsert_requires_complete_successful_population(self):
        action = __import__(
            "api.priority.mailbox_outbox",
            fromlist=["plan_priority_mailbox_outbox_event"],
        ).plan_priority_mailbox_outbox_event(
            authority(),
            event(),
            snapshot(),
        )

        class Report:
            attempted = 1
            processed = 1
            written = 1
            incomplete = False

        with patch(
            "api.priority.mailbox_outbox_consumer.populate_priority_candidates",
            return_value=Report(),
        ):
            self.assertTrue(
                apply_priority_mailbox_outbox_action(
                    authority(),
                    action,
                    candidate_store=self.candidate_store,
                    workflow_store=self.workflow_store,
                )
            )

        Report.incomplete = True
        with patch(
            "api.priority.mailbox_outbox_consumer.populate_priority_candidates",
            return_value=Report(),
        ):
            self.assertFalse(
                apply_priority_mailbox_outbox_action(
                    authority(),
                    action,
                    candidate_store=self.candidate_store,
                    workflow_store=self.workflow_store,
                )
            )


if __name__ == "__main__":
    unittest.main()
