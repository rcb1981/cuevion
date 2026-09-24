"""Tests for pure mailbox outbox to Priority action planning."""

from __future__ import annotations

import unittest

from api.priority.candidate_projection import PriorityCandidatePopulationAuthority
from api.priority.mailbox_outbox import (
    PriorityMailboxOutboxActionKind,
    plan_priority_mailbox_outbox_event,
)
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


def _authority():
    return PriorityCandidatePopulationAuthority(
        workspace_id="wsp_" + ("a" * 22),
        user_id="usr_" + ("b" * 22),
        mailbox_id="gmail-1",
        mailbox_account_identity="verified@gmail.com",
        provider="google",
    )


def _scope(*, generation=3):
    return MailboxScope(
        workspace_id="wsp_" + ("a" * 22),
        owner_user_id="usr_" + ("b" * 22),
        mailbox_id="gmail-1",
        source_generation=generation,
        provider=MailboxProvider.GOOGLE,
        provider_account_identity="verified@gmail.com",
    )


def _record(
    *,
    labels=("INBOX", "UNREAD"),
    subject="Subject",
    sender_address="sender@example.test",
):
    return MessageRecord(
        identity=MessageIdentity(
            message_id="mbm_" + ("c" * 22),
            provider_message_id="gmail-message-1",
            provider_folder="Inbox",
            imap_uid_validity=None,
            imap_uid=None,
        ),
        provider_thread_id="thread-1",
        provider_labels=tuple(labels),
        rfc_message_id="rfc-1@example.test",
        in_reply_to=None,
        references=(),
        sender_address=sender_address,
        sender_display="Sender",
        to_recipients=("owner@example.test",),
        cc_recipients=(),
        subject=subject,
        snippet="Snippet",
        provider_timestamp_millis=1_790_236_800_000,
        unread=True,
        starred=False,
        body_state=BodyState.CACHED,
        metadata_hash="1" * 64,
    )


def _snapshot(
    *,
    row_version=4,
    deleted=False,
    labels=("INBOX", "UNREAD"),
    generation=3,
):
    return OutboxMessageSnapshot(
        scope=_scope(generation=generation),
        record=_record(labels=labels),
        provider_deleted=deleted,
        row_version=row_version,
    )


def _event(
    event_type=OutboxEventType.MESSAGE_CHANGED,
    *,
    row_version=4,
    generation=3,
):
    return OutboxEvent(
        event_id="mbe_" + ("d" * 22),
        scope=OutboxStorageScope(
            workspace_id="wsp_" + ("a" * 22),
            owner_user_id="usr_" + ("b" * 22),
            mailbox_id="gmail-1",
            source_generation=generation,
        ),
        message_id="mbm_" + ("c" * 22),
        message_row_version=row_version,
        event_type=event_type,
        attempt_count=1,
        claim_token="claim-token",
    )


class PriorityMailboxOutboxPlanTests(unittest.TestCase):
    def test_added_or_changed_current_row_plans_canonical_upsert(self):
        for event_type in (
            OutboxEventType.MESSAGE_ADDED,
            OutboxEventType.MESSAGE_CHANGED,
        ):
            with self.subTest(event_type=event_type):
                action = plan_priority_mailbox_outbox_event(
                    _authority(),
                    _event(event_type),
                    _snapshot(),
                )

                self.assertIs(
                    action.kind,
                    PriorityMailboxOutboxActionKind.UPSERT,
                )
                self.assertEqual(
                    action.candidate_scope.identity.provider_message_id,
                    "gmail-message-1",
                )
                self.assertEqual(action.source["provider"], "google")
                self.assertEqual(action.source["providerFolder"], "INBOX")
                self.assertEqual(action.source["labels"], ["INBOX", "UNREAD"])
                self.assertEqual(
                    action.source["providerTimestampMillis"],
                    "1790236800000",
                )
                self.assertEqual(action.source["subject"], "Subject")
                self.assertTrue(action.source["unread"])
                self.assertFalse(action.source["flagged"])

    def test_exact_deleted_row_plans_candidate_transport_removal(self):
        action = plan_priority_mailbox_outbox_event(
            _authority(),
            _event(
                OutboxEventType.MESSAGE_DELETED,
                row_version=5,
            ),
            _snapshot(row_version=5, deleted=True),
        )

        self.assertIs(action.kind, PriorityMailboxOutboxActionKind.REMOVE)
        self.assertEqual(
            action.candidate_scope.identity.provider_message_id,
            "gmail-message-1",
        )
        self.assertIsNone(action.source)

    def test_newer_durable_row_supersedes_delayed_event_without_side_effect(self):
        cases = (
            (
                _event(OutboxEventType.MESSAGE_CHANGED, row_version=4),
                _snapshot(row_version=5, deleted=True),
            ),
            (
                _event(OutboxEventType.MESSAGE_DELETED, row_version=5),
                _snapshot(row_version=6, deleted=False),
            ),
        )
        for event, snapshot in cases:
            with self.subTest(event_type=event.event_type):
                action = plan_priority_mailbox_outbox_event(
                    _authority(),
                    event,
                    snapshot,
                )
                self.assertIs(
                    action.kind,
                    PriorityMailboxOutboxActionKind.SUPERSEDED,
                )
                self.assertIsNone(action.candidate_scope)
                self.assertIsNone(action.source)

    def test_stale_generation_is_terminal_noop(self):
        action = plan_priority_mailbox_outbox_event(
            _authority(),
            _event(),
            None,
        )
        self.assertIs(
            action.kind,
            PriorityMailboxOutboxActionKind.STALE_GENERATION,
        )

    def test_current_but_non_priority_eligible_row_is_terminal_ineligible(self):
        action = plan_priority_mailbox_outbox_event(
            _authority(),
            _event(),
            _snapshot(labels=("INBOX", "SENT")),
        )
        self.assertIs(
            action.kind,
            PriorityMailboxOutboxActionKind.INELIGIBLE,
        )
        self.assertIsNone(action.candidate_scope)
        self.assertIsNone(action.source)

    def test_event_ahead_of_durable_row_fails_closed(self):
        with self.assertRaises(ValueError):
            plan_priority_mailbox_outbox_event(
                _authority(),
                _event(row_version=5),
                _snapshot(row_version=4),
            )

    def test_exact_event_type_must_match_deleted_state(self):
        with self.assertRaises(ValueError):
            plan_priority_mailbox_outbox_event(
                _authority(),
                _event(OutboxEventType.MESSAGE_DELETED),
                _snapshot(deleted=False),
            )
        with self.assertRaises(ValueError):
            plan_priority_mailbox_outbox_event(
                _authority(),
                _event(OutboxEventType.MESSAGE_CHANGED),
                _snapshot(deleted=True),
            )

    def test_authority_and_generation_mismatch_fail_closed(self):
        wrong_authority = PriorityCandidatePopulationAuthority(
            workspace_id="wsp_" + ("e" * 22),
            user_id="usr_" + ("b" * 22),
            mailbox_id="gmail-1",
            mailbox_account_identity="verified@gmail.com",
            provider="google",
        )
        with self.assertRaises(ValueError):
            plan_priority_mailbox_outbox_event(
                wrong_authority,
                _event(),
                _snapshot(),
            )

        with self.assertRaises(ValueError):
            plan_priority_mailbox_outbox_event(
                _authority(),
                _event(generation=3),
                _snapshot(generation=4),
            )


if __name__ == "__main__":
    unittest.main()
