from __future__ import annotations

import json
import base64
import importlib
import unittest
from email.message import Message
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import unquote

import imap_connect_preview

from . import candidate_store as candidate_store_module
from .authority import PriorityMessageIdentity
from .candidate_recovery import CandidateRecoveryConsumerResult
from .candidate_recovery_store import (
    PriorityCandidateRecoveryClaim,
    PriorityCandidateRecoveryMailboxScope,
    PriorityCandidateRecoveryRecord,
    PriorityCandidateRecoveryScope,
)
from .candidate_projection import (
    PriorityCandidatePopulationAuthority,
    populate_priority_candidates,
    populate_runtime_priority_candidates,
    project_priority_candidate,
)
from .candidate_reference_reconciliation import (
    CandidateReferenceReconciliationResult,
)
from .candidate_store import PriorityCandidateScope, PriorityCandidateStore
from .store import PriorityWorkflowScope, PriorityWorkflowStore
from .test_candidate_store import MemoryRedis, SECRET
from .test_candidate_recovery import FakeRecoveryStore
from .test_workflow_store import WorkflowMemoryRedis


fetch_gmail = importlib.import_module("api.inboxes.fetch-gmail")


def gmail_authority(**overrides) -> PriorityCandidatePopulationAuthority:
    values = {
        "workspace_id": "workspace-1",
        "user_id": "user-1",
        "mailbox_id": "mailbox-1",
        "mailbox_account_identity": "owner@gmail.test",
        "provider": "google",
    }
    values.update(overrides)
    return PriorityCandidatePopulationAuthority(**values)


def gmail_source(**overrides) -> dict:
    values = {
        "provider": "google",
        "providerMessageId": "gmail-message-1",
        "providerThreadId": "gmail-thread-1",
        "providerFolder": "INBOX",
        "labels": ["INBOX", "UNREAD", "STARRED"],
        "senderDisplay": "Sender Name",
        "senderAddress": "sender@example.test",
        "subject": "Provider subject",
        "snippet": "Provider snippet",
        "unread": True,
        "flagged": True,
        "providerTimestampMillis": "1751364000123",
        "rfcDate": "Tue, 01 Jul 2025 10:00:00 +0000",
    }
    values.update(overrides)
    return values


def imap_authority(**overrides) -> PriorityCandidatePopulationAuthority:
    values = {
        "workspace_id": "workspace-1",
        "user_id": "user-1",
        "mailbox_id": "mailbox-1",
        "mailbox_account_identity": "owner@imap.test",
        "provider": "custom_imap",
    }
    values.update(overrides)
    return PriorityCandidatePopulationAuthority(**values)


def imap_source(**overrides) -> dict:
    values = {
        "provider": "custom_imap",
        "providerFolder": "INBOX",
        "uidValidity": "456",
        "imapUid": "123",
        "conversationId": "imap:rfc:mailbox-1:root%40example.test",
        "authorityKind": "rfc",
        "rfcRootMessageId": "root@example.test",
        "rfcMessageId": "message@example.test",
        "senderDisplay": "IMAP Sender",
        "senderAddress": "sender@imap.test",
        "subject": "IMAP subject",
        "snippet": "IMAP snippet",
        "unread": False,
        "flagged": True,
        "rfcDate": "Tue, 01 Jul 2025 10:00:00 +0000",
    }
    values.update(overrides)
    return values


def gmail_recovery_context(*, refresh_attempted: bool = False) -> dict:
    return {
        "mailbox_id": "mailbox-1",
        "mailbox_email": "owner@gmail.test",
        "owner_email": "owner@example.test",
        "access_token": "recovery-access-token",
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
        "refresh_attempted": refresh_attempted,
    }


def gmail_recovery_detail(
    message_id: str,
    *,
    thread_id: str | None = None,
    labels: list[str] | None = None,
) -> dict:
    raw = (
        f"Message-Id: <{message_id}@example.test>\r\n"
        "Date: Tue, 01 Jul 2025 10:00:00 +0000\r\n"
        "From: Sender Name <sender@example.test>\r\n"
        "To: owner@gmail.test\r\n"
        "Subject: Provider subject\r\n"
        "\r\n"
        "Provider snippet"
    ).encode("utf-8")
    return {
        "id": message_id,
        "threadId": thread_id or f"thread-{message_id}",
        "labelIds": labels if labels is not None else ["INBOX", "UNREAD", "STARRED"],
        "internalDate": "1751364000123",
        "raw": base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii"),
    }


class ScopedFakeRecoveryStore(FakeRecoveryStore):
    def claim_due(self, mailbox_scope, *, limit=8):
        if self.unavailable:
            return super().claim_due(mailbox_scope, limit=limit)
        return tuple(
            claim
            for claim in self.claims
            if claim.record.scope.mailbox_scope == mailbox_scope
        )[:limit]


class CandidateProjectionTests(unittest.TestCase):
    def test_gmail_projection_is_exact_unresolved_and_bounded(self):
        source = gmail_source(snippet="é" * 400)
        scope, snapshot = project_priority_candidate(gmail_authority(), source)

        self.assertEqual(scope.identity.provider_message_id, "gmail-message-1")
        self.assertEqual(
            snapshot.conversation.conversation_id,
            "thread:mailbox-1|gmail-thread-1",
        )
        self.assertEqual(
            snapshot.conversation.provider_thread_id,
            "gmail-thread-1",
        )
        self.assertEqual(snapshot.routing_state, "unresolved")
        self.assertIsNone(snapshot.routing)
        self.assertEqual(snapshot.provider_authority.folder, "INBOX")
        self.assertEqual(
            snapshot.provider_authority.labels,
            ("INBOX", "UNREAD", "STARRED"),
        )
        self.assertEqual(snapshot.render.sender_display, "Sender Name")
        self.assertEqual(snapshot.render.sender_address, "sender@example.test")
        self.assertEqual(snapshot.render.subject, "Provider subject")
        self.assertEqual(snapshot.render.created_at, "2025-07-01T10:00:00.123Z")
        self.assertEqual(len(snapshot.render.snippet.encode("utf-8")), 512)
        self.assertEqual(snapshot.render.snippet, "é" * 256)
        self.assertTrue(snapshot.render.unread)
        self.assertTrue(snapshot.render.flagged)

    def test_gmail_requires_inbox_labels_and_a_true_message_time(self):
        rejected = (
            gmail_source(labels=["TRASH"]),
            gmail_source(labels=["INBOX", "TRASH"]),
            gmail_source(
                providerTimestampMillis=None,
                rfcDate=None,
            ),
            gmail_source(
                providerTimestampMillis="invalid",
                rfcDate="not a timestamp",
            ),
            gmail_source(
                providerTimestampMillis=None,
                rfcDate="Tue, 01 Jul 2025 10:00:00",
            ),
        )
        for source in rejected:
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    project_priority_candidate(gmail_authority(), source)

        _, snapshot = project_priority_candidate(
            gmail_authority(),
            gmail_source(
                providerTimestampMillis=None,
                rfcDate="Tue, 01 Jul 2025 12:00:00 +0200",
            ),
        )
        self.assertEqual(snapshot.render.created_at, "2025-07-01T10:00:00.000Z")

    def test_imap_projection_uses_folder_uidvalidity_uid_and_rfc_authority(self):
        scope, snapshot = project_priority_candidate(imap_authority(), imap_source())

        self.assertIsNone(scope.identity.provider_message_id)
        self.assertEqual(scope.identity.provider_folder, "INBOX")
        self.assertEqual(scope.identity.uid_validity, "456")
        self.assertEqual(scope.identity.imap_uid, "123")
        self.assertEqual(snapshot.conversation.authority_kind, "rfc")
        self.assertEqual(
            snapshot.conversation.rfc_root_message_id,
            "root@example.test",
        )
        self.assertEqual(
            snapshot.conversation.rfc_message_id,
            "message@example.test",
        )
        self.assertEqual(snapshot.routing_state, "unresolved")
        self.assertIsNone(snapshot.routing)
        self.assertEqual(snapshot.render.created_at, "2025-07-01T10:00:00.000Z")

    def test_render_accepts_empty_display_and_subject_with_exact_address(self):
        for authority, source in (
            (
                gmail_authority(),
                gmail_source(
                    senderDisplay="",
                    senderAddress="artist@example.com",
                    subject="",
                ),
            ),
            (
                imap_authority(),
                imap_source(
                    senderDisplay="",
                    senderAddress="artist@example.com",
                    subject="",
                ),
            ),
        ):
            with self.subTest(provider=authority.provider):
                _scope, candidate = project_priority_candidate(authority, source)
                self.assertEqual(candidate.render.sender_display, "")
                self.assertEqual(candidate.render.sender_address, "artist@example.com")
                self.assertEqual(candidate.render.subject, "")

    def test_render_rejects_missing_malformed_and_permissive_parse_addresses(self):
        for sender_address in (
            "",
            "Unknown",
            "Display",
            "two@@example.com",
            "a..b@example.com",
            "sender address@example.com",
        ):
            with self.subTest(sender_address=sender_address):
                report = populate_priority_candidates(
                    gmail_authority(),
                    [gmail_source(senderAddress=sender_address)],
                    store=PriorityCandidateStore(MemoryRedis(), hmac_secret=SECRET),
                )
                self.assertEqual(
                    report.reason_counts,
                    (("candidate_render_invalid", 1),),
                )

    def test_candidate_render_source_ignores_ui_sender_and_subject_fallbacks(self):
        message = Message()
        message["From"] = "artist@example.com"
        message["Date"] = "Tue, 01 Jul 2025 10:00:00 +0000"
        source = imap_connect_preview.build_priority_candidate_render_source(
            message,
            {
                "sender": "Unknown sender",
                "subject": "Untitled message",
                "snippet": "Provider snippet",
                "unread": True,
                "flagged": False,
            },
        )
        self.assertEqual(source["senderDisplay"], "")
        self.assertEqual(source["senderAddress"], "artist@example.com")
        self.assertEqual(source["subject"], "")
        self.assertNotIn("Unknown sender", source.values())
        self.assertNotIn("Untitled message", source.values())

        _scope, candidate = project_priority_candidate(
            imap_authority(),
            {
                **imap_source(),
                **source,
            },
        )
        self.assertEqual(candidate.render.sender_display, "")
        self.assertEqual(candidate.render.subject, "")

    def test_candidate_render_source_preserves_decoded_utf8_display_and_subject(self):
        message = Message()
        message["From"] = "Artíst <artist@example.com>"
        message["Subject"] = "Café subject"
        message["Date"] = "Tue, 01 Jul 2025 10:00:00 +0000"
        source = imap_connect_preview.build_priority_candidate_render_source(
            message,
            {
                "sender": "UI sender",
                "subject": "UI subject",
                "snippet": "Provider snippet",
                "unread": False,
                "flagged": True,
            },
        )
        self.assertEqual(source["senderDisplay"], "Artíst")
        self.assertEqual(source["senderAddress"], "artist@example.com")
        self.assertEqual(source["subject"], "Café subject")
        _scope, candidate = project_priority_candidate(
            imap_authority(),
            {
                **imap_source(),
                **source,
            },
        )
        self.assertEqual(candidate.render.sender_display, "Artíst")
        self.assertEqual(candidate.render.subject, "Café subject")

    def test_imap_rejects_sequence_numbers_malformed_identity_and_synthesized_time(self):
        rejected = (
            imap_source(imapUid=None),
            imap_source(imapUid="0"),
            imap_source(uidValidity=None),
            imap_source(uidValidity="not-valid"),
            imap_source(rfcDate=None),
            imap_source(rfcDate="invalid"),
            {
                **imap_source(),
                "sequenceNumber": "999",
            },
        )
        for source in rejected:
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    project_priority_candidate(imap_authority(), source)

    def test_scope_binds_every_tenant_and_connection_dimension(self):
        authorities = (
            gmail_authority(),
            gmail_authority(workspace_id="workspace-2"),
            gmail_authority(user_id="user-2"),
            gmail_authority(mailbox_id="mailbox-2"),
            gmail_authority(mailbox_account_identity="replacement@gmail.test"),
        )
        canonical_scopes = {
            project_priority_candidate(authority, gmail_source())[0].canonical_bytes()
            for authority in authorities
        }
        imap_scope, _ = project_priority_candidate(imap_authority(), imap_source())
        canonical_scopes.add(imap_scope.canonical_bytes())
        self.assertEqual(len(canonical_scopes), 6)


class CandidatePopulationTests(unittest.TestCase):
    def setUp(self):
        self.redis = MemoryRedis()
        self.store = PriorityCandidateStore(self.redis, hmac_secret=SECRET)
        self.workflow_redis = WorkflowMemoryRedis()
        self.workflow_store = PriorityWorkflowStore(
            self.workflow_redis,
            hmac_secret=SECRET,
        )
        self.authority = gmail_authority()

    def test_repeated_refresh_updates_one_record_and_absence_removes_nothing(self):
        first = gmail_source()
        second = gmail_source(
            providerMessageId="gmail-message-2",
            providerThreadId="gmail-thread-2",
        )
        initial = populate_priority_candidates(
            self.authority,
            [first, second],
            store=self.store,
        )
        self.assertEqual((initial.written, initial.skipped), (2, 0))

        first_scope, _ = project_priority_candidate(self.authority, first)
        second_scope, _ = project_priority_candidate(self.authority, second)
        first_record = self.store.read_candidate(first_scope)
        second_record = self.store.read_candidate(second_scope)
        self.assertEqual(first_record.version, 1)
        self.assertEqual(second_record.version, 1)

        self.redis.advance(1)
        refreshed = populate_priority_candidates(
            self.authority,
            [first],
            store=self.store,
        )
        self.assertEqual((refreshed.written, refreshed.skipped), (1, 0))
        self.assertEqual(self.store.read_candidate(first_scope).version, 2)
        self.assertEqual(self.store.read_candidate(second_scope).version, 1)
        page = self.store.read_mailbox_page(first_scope.mailbox_scope())
        self.assertEqual(page.total, 2)

    def test_workflow_first_candidate_later_reconciles_exact_gmail_and_imap_identity(
        self,
    ):
        for authority, source in (
            (gmail_authority(), gmail_source()),
            (imap_authority(), imap_source()),
        ):
            with self.subTest(provider=authority.provider):
                candidate_redis = MemoryRedis(current_ms=1_700_000_000_000)
                candidate_store = PriorityCandidateStore(
                    candidate_redis,
                    hmac_secret=SECRET,
                )
                workflow_redis = WorkflowMemoryRedis()
                workflow_store = PriorityWorkflowStore(
                    workflow_redis,
                    hmac_secret=SECRET,
                )
                candidate_scope, _snapshot = project_priority_candidate(
                    authority,
                    source,
                )
                workflow_scope = PriorityWorkflowScope(
                    workspace_id=candidate_scope.workspace_id,
                    user_id=candidate_scope.user_id,
                    mailbox_id=candidate_scope.mailbox_id,
                    identity=candidate_scope.identity,
                )
                accepted = workflow_store.write_field(
                    workflow_scope,
                    field="waiting",
                    value="returned_reply",
                )
                self.assertIsNone(candidate_store.read_candidate(candidate_scope))
                candidate_commands = len(candidate_redis.commands)
                workflow_commands = len(workflow_redis.commands)
                with self.assertLogs(
                    "api.priority.candidate_projection",
                    level="INFO",
                ) as captured:
                    report = populate_priority_candidates(
                        authority,
                        [source],
                        store=candidate_store,
                        workflow_store=workflow_store,
                    )
                self.assertEqual((report.written, report.incomplete), (1, False))
                self.assertEqual(
                    (report.attempted, report.processed, report.skipped),
                    (1, 1, 0),
                )
                self.assertEqual(
                    captured.output,
                    [
                        "INFO:api.priority.candidate_projection:"
                        "Priority candidate workflow reference reconciliation "
                        "outcome=candidate_reference_reconciled"
                    ],
                )
                self.assertGreater(len(candidate_redis.commands), candidate_commands)
                self.assertEqual(
                    len(workflow_redis.commands) - workflow_commands,
                    1,
                )
                candidate = candidate_store.read_candidate(candidate_scope)
                assert candidate is not None
                self.assertEqual(
                    candidate.positive_reference_expires_at("returned_reply"),
                    accepted.waiting_expires_at,
                )
                self.assertEqual(
                    candidate.positive_reference_expires_at("waiting"),
                    0,
                )
                self.assertEqual(len(workflow_redis.commands), 2)
                exact_read = workflow_redis.commands[-1]
                self.assertEqual(exact_read[0], "EVAL")
                self.assertEqual(exact_read[2], 1)
                self.assertFalse(
                    any(
                        command[0] == "SCAN"
                        for command in workflow_redis.commands
                    )
                )

    def test_workflow_record_absent_logs_info_without_population_change(self):
        candidate_redis = MemoryRedis(current_ms=1_700_000_000_000)
        candidate_store = PriorityCandidateStore(
            candidate_redis,
            hmac_secret=SECRET,
        )
        workflow_redis = WorkflowMemoryRedis()
        workflow_store = PriorityWorkflowStore(
            workflow_redis,
            hmac_secret=SECRET,
        )
        with self.assertLogs(
            "api.priority.candidate_projection",
            level="INFO",
        ) as captured:
            report = populate_priority_candidates(
                gmail_authority(),
                [gmail_source()],
                store=candidate_store,
                workflow_store=workflow_store,
            )
        self.assertEqual(
            (
                report.attempted,
                report.processed,
                report.written,
                report.skipped,
                report.incomplete,
                report.reason_counts,
            ),
            (1, 1, 1, 0, False, ()),
        )
        self.assertEqual(
            captured.output,
            [
                "INFO:api.priority.candidate_projection:"
                "Priority candidate workflow reference reconciliation "
                "outcome=workflow_record_absent"
            ],
        )
        self.assertEqual(len(workflow_redis.commands), 1)
        self.assertEqual(workflow_redis.commands[0][2], 1)
        scope, _snapshot = project_priority_candidate(
            gmail_authority(),
            gmail_source(),
        )
        candidate = candidate_store.read_candidate(scope)
        assert candidate is not None
        self.assertEqual(candidate.version, 1)

    def test_workflow_reconciliation_failure_does_not_stop_provider_population(
        self,
    ):
        workflow_redis = WorkflowMemoryRedis()
        workflow_redis.unavailable = True
        workflow_store = PriorityWorkflowStore(
            workflow_redis,
            hmac_secret=SECRET,
        )
        sources = [
            gmail_source(),
            gmail_source(
                providerMessageId="gmail-message-2",
                providerThreadId="gmail-thread-2",
            ),
        ]
        with self.assertLogs(
            "api.priority.candidate_projection",
            level="WARNING",
        ) as captured:
            report = populate_priority_candidates(
                self.authority,
                sources,
                store=self.store,
                workflow_store=workflow_store,
            )
        self.assertEqual((report.processed, report.written), (2, 2))
        self.assertFalse(report.incomplete)
        self.assertEqual(
            sum("outcome=store_unavailable" in line for line in captured.output),
            2,
        )

    def test_reconciliation_logs_exclude_provider_content_scope_and_storage(self):
        gmail = gmail_authority(
            mailbox_id="privacy-gmail-mailbox-id",
            mailbox_account_identity="privacy-gmail-account@example.test",
        )
        gmail_row = gmail_source(
            providerMessageId="privacy-gmail-provider-message-id",
            providerThreadId="privacy-gmail-conversation-id",
            senderDisplay="Privacy Gmail Sender",
            senderAddress="privacy-gmail-sender@example.test",
            subject="Privacy Gmail Subject",
            snippet="Privacy Gmail Snippet",
        )
        imap = imap_authority(
            mailbox_id="privacy-imap-mailbox-id",
            mailbox_account_identity="privacy-imap-account@example.test",
        )
        imap_row = imap_source(
            uidValidity="87654321",
            imapUid="12345678",
            conversationId="privacy-imap-conversation-id",
            rfcRootMessageId="privacy-root@example.test",
            rfcMessageId="privacy-message@example.test",
            senderDisplay="Privacy IMAP Sender",
            senderAddress="privacy-imap-sender@example.test",
            subject="Privacy IMAP Subject",
            snippet="Privacy IMAP Snippet",
        )
        workflow_commands = len(self.workflow_redis.commands)
        with patch(
            "api.priority.candidate_projection.reconcile_candidate_from_workflow_store",
            return_value=CandidateReferenceReconciliationResult.RECONCILED,
        ), self.assertLogs(
            "api.priority.candidate_projection",
            level="INFO",
        ) as captured:
            gmail_report = populate_priority_candidates(
                gmail,
                [gmail_row],
                store=self.store,
                workflow_store=self.workflow_store,
            )
            imap_report = populate_priority_candidates(
                imap,
                [imap_row],
                store=self.store,
                workflow_store=self.workflow_store,
            )
        self.assertEqual((gmail_report.written, imap_report.written), (1, 1))
        self.assertEqual(
            captured.output,
            [
                "INFO:api.priority.candidate_projection:"
                "Priority candidate workflow reference reconciliation "
                "outcome=candidate_reference_reconciled",
                "INFO:api.priority.candidate_projection:"
                "Priority candidate workflow reference reconciliation "
                "outcome=candidate_reference_reconciled",
            ],
        )
        self.assertEqual(len(self.workflow_redis.commands), workflow_commands)
        output = "\n".join(captured.output)
        redis_keys = tuple(
            key for key in self.redis.values if ":record:" in key
        )
        hmac_digests = tuple(key.rsplit(":", 1)[-1] for key in redis_keys)
        for sensitive in (
            gmail.mailbox_id,
            gmail.mailbox_account_identity,
            gmail_row["providerMessageId"],
            gmail_row["providerThreadId"],
            gmail_row["senderDisplay"],
            gmail_row["senderAddress"],
            gmail_row["subject"],
            gmail_row["snippet"],
            imap.mailbox_id,
            imap.mailbox_account_identity,
            imap_row["uidValidity"],
            imap_row["imapUid"],
            imap_row["conversationId"],
            imap_row["senderDisplay"],
            imap_row["senderAddress"],
            imap_row["subject"],
            imap_row["snippet"],
            *redis_keys,
            *hmac_digests,
            SECRET,
        ):
            self.assertNotIn(sensitive, output)


class GmailExactRecoveryDrainTests(unittest.TestCase):
    def setUp(self):
        self.workflow_redis = WorkflowMemoryRedis()
        self.workflow_store = PriorityWorkflowStore(
            self.workflow_redis,
            hmac_secret=SECRET,
        )
        self.candidate_redis = MemoryRedis(
            current_ms=self.workflow_redis.clock_ms,
        )
        self.candidate_store = PriorityCandidateStore(
            self.candidate_redis,
            hmac_secret=SECRET,
        )
        self.recovery_store = ScopedFakeRecoveryStore()
        self.member = SimpleNamespace(
            workspace_id="workspace-1",
            user_id="user-1",
        )
        self.context = gmail_recovery_context()

    def _mailbox_scope(
        self,
        *,
        mailbox_id: str = "mailbox-1",
        mailbox_account_identity: str = "owner@gmail.test",
        provider: str = "google",
    ) -> PriorityCandidateRecoveryMailboxScope:
        return PriorityCandidateRecoveryMailboxScope(
            workspace_id="workspace-1",
            user_id="user-1",
            mailbox_id=mailbox_id,
            mailbox_account_identity=mailbox_account_identity,
            provider=provider,
        )

    def _queue_google(
        self,
        message_id: str,
        *,
        mailbox_scope: PriorityCandidateRecoveryMailboxScope | None = None,
    ) -> PriorityWorkflowScope:
        mailbox_scope = mailbox_scope or self._mailbox_scope()
        identity = PriorityMessageIdentity(
            provider="google",
            provider_message_id=message_id,
        )
        workflow_scope = PriorityWorkflowScope(
            workspace_id=mailbox_scope.workspace_id,
            user_id=mailbox_scope.user_id,
            mailbox_id=mailbox_scope.mailbox_id,
            identity=identity,
        )
        written = self.workflow_store.write_field(
            workflow_scope,
            field="manualPriority",
            value="priority",
        )
        record = PriorityCandidateRecoveryRecord(
            scope=PriorityCandidateRecoveryScope(mailbox_scope, identity),
            workflow_version=written.version,
            authority_expires_at=written.manual_expires_at,
            enqueued_at=written.updated_at,
            updated_at=written.updated_at,
            attempt_count=0,
            generation=1,
        )
        claim_number = len(self.recovery_store.claims) + 1
        claim = PriorityCandidateRecoveryClaim(
            record=record,
            identity_digest=f"{claim_number:064x}",
            lease_token=f"{claim_number + 100:064x}",
            claimed_at=record.updated_at,
            lease_expires_at=min(
                record.updated_at + 90_000,
                record.authority_expires_at,
            ),
            raw_record=f"opaque-record-{claim_number}",
        )
        self.recovery_store.claims = (*self.recovery_store.claims, claim)
        return workflow_scope

    def _run(self, *, request_with_one_refresh=None):
        return fetch_gmail._run_gmail_priority_candidate_recovery(
            member=self.member,
            context=self.context,
            focus_preferences=None,
            recovery_store=self.recovery_store,
            candidate_store=self.candidate_store,
            workflow_store=self.workflow_store,
            request_with_one_refresh=request_with_one_refresh,
        )

    def test_successful_route_populates_window_then_runs_isolated_drain(self):
        preview = {
            "providerMessageId": "window-message",
            "providerThreadId": "window-thread",
            "labelIds": ["INBOX"],
        }
        snapshot_result = {
            "status": "ok",
            "context": self.context,
            "snapshot": {
                "messages": [preview],
                "uidValidity": "gmail-api",
            },
            "error": None,
            "refresh_failure": None,
            "_priorityCandidateSources": [gmail_source()],
        }
        request_handler = SimpleNamespace(headers={})
        order: list[str] = []
        sent: list[tuple[int, dict]] = []

        def populate(**_kwargs):
            order.append("ordinary-population")
            return SimpleNamespace()

        def drain(**_kwargs):
            order.append("recovery-drain")
            raise RuntimeError("recovery unavailable")

        with patch.object(
            fetch_gmail,
            "read_json_body",
            return_value=({"mailboxId": "mailbox-1"}, None),
        ), patch.object(
            fetch_gmail,
            "resolve_authenticated_gmail",
            return_value={
                "status": "ok",
                "context": self.context,
                "memberAuthority": self.member,
            },
        ), patch.object(
            fetch_gmail,
            "read_gmail_folder_snapshot",
            return_value=snapshot_result,
        ) as full_refresh, patch.object(
            fetch_gmail,
            "populate_runtime_priority_candidates",
            side_effect=populate,
        ), patch.object(
            fetch_gmail,
            "_run_gmail_priority_candidate_recovery",
            side_effect=drain,
        ) as recovery_drain, patch.object(
            fetch_gmail,
            "read_new_inbound_client_mode",
            return_value="off",
        ), patch.object(
            fetch_gmail,
            "send_json",
            side_effect=lambda _handler, status, payload: sent.append(
                (status, payload)
            ),
        ):
            fetch_gmail.handler._handle_post(request_handler)

        self.assertEqual(order, ["ordinary-population", "recovery-drain"])
        full_refresh.assert_called_once()
        recovery_drain.assert_called_once()
        self.assertEqual(sent[0][0], 200)
        self.assertTrue(sent[0][1]["ok"])

    def test_failed_mailbox_snapshot_never_runs_recovery_drain(self):
        request_handler = SimpleNamespace(headers={})
        with patch.object(
            fetch_gmail,
            "read_json_body",
            return_value=({"mailboxId": "mailbox-1"}, None),
        ), patch.object(
            fetch_gmail,
            "resolve_authenticated_gmail",
            return_value={
                "status": "ok",
                "context": self.context,
                "memberAuthority": self.member,
            },
        ), patch.object(
            fetch_gmail,
            "read_gmail_folder_snapshot",
            return_value={
                "status": "error",
                "context": self.context,
                "snapshot": None,
                "error": {"code": "gmail_rate_limited"},
                "refresh_failure": None,
            },
        ), patch.object(
            fetch_gmail,
            "populate_runtime_priority_candidates",
        ) as population, patch.object(
            fetch_gmail,
            "_run_gmail_priority_candidate_recovery",
        ) as recovery_drain, patch.object(fetch_gmail, "send_json"):
            fetch_gmail.handler._handle_post(request_handler)

        population.assert_not_called()
        recovery_drain.assert_not_called()

    def test_google_scope_and_eight_claim_bound_without_list_or_recursion(self):
        other_mailbox = self._mailbox_scope(mailbox_id="mailbox-2")
        other_account = self._mailbox_scope(
            mailbox_account_identity="other@gmail.test"
        )
        self._queue_google("other-mailbox", mailbox_scope=other_mailbox)
        self._queue_google("other-account", mailbox_scope=other_account)
        imap_identity = PriorityMessageIdentity(
            provider="custom_imap",
            provider_folder="INBOX",
            uid_validity="456",
            imap_uid="123",
        )
        imap_record = PriorityCandidateRecoveryRecord(
            scope=PriorityCandidateRecoveryScope(
                self._mailbox_scope(
                    mailbox_account_identity="owner@imap.test",
                    provider="custom_imap",
                ),
                imap_identity,
            ),
            workflow_version=1,
            authority_expires_at=self.workflow_redis.clock_ms + 60_000,
            enqueued_at=self.workflow_redis.clock_ms,
            updated_at=self.workflow_redis.clock_ms,
            attempt_count=0,
            generation=1,
        )
        self.recovery_store.claims = (
            *self.recovery_store.claims,
            PriorityCandidateRecoveryClaim(
                record=imap_record,
                identity_digest="f" * 64,
                lease_token="e" * 64,
                claimed_at=imap_record.updated_at,
                lease_expires_at=imap_record.updated_at + 60_000,
                raw_record="opaque-imap-record",
            ),
        )
        empty_request = Mock()
        empty = self._run(request_with_one_refresh=empty_request)
        self.assertEqual(empty.claimed, 0)
        empty_request.assert_not_called()

        self.recovery_store.claims = ()
        message_ids = [f"bounded-{index}" for index in range(10)]
        for message_id in message_ids:
            self._queue_google(message_id)
        requested_paths: list[str] = []

        def request(context: dict, request_path: str):
            requested_paths.append(request_path)
            encoded_id = request_path.split("/messages/", 1)[1].split("?", 1)[0]
            message_id = unquote(encoded_id)
            return gmail_recovery_detail(message_id), None, context, None

        with patch.object(fetch_gmail, "read_gmail_folder_snapshot") as refresh:
            report = self._run(request_with_one_refresh=request)
        self.assertEqual(report.claimed, 8)
        self.assertEqual(report.completed, 8)
        self.assertEqual(len(requested_paths), 8)
        self.assertTrue(
            all(path.endswith("?format=raw") for path in requested_paths)
        )
        self.assertFalse(any(path.startswith("/messages?") for path in requested_paths))
        refresh.assert_not_called()

    def test_one_authentication_refresh_caps_eight_claims_at_nine_gets(self):
        for index in range(8):
            self._queue_google(f"auth-{index}")
        provider_calls: list[tuple[str, str]] = []

        def gmail_request(access_token: str, request_path: str):
            provider_calls.append((access_token, request_path))
            if len(provider_calls) == 1:
                return None, {"code": "gmail_token_invalid"}
            encoded_id = request_path.split("/messages/", 1)[1].split("?", 1)[0]
            return gmail_recovery_detail(unquote(encoded_id)), None

        refreshed_context = {
            **self.context,
            "access_token": "refreshed-recovery-token",
            "refresh_attempted": True,
        }
        with patch.object(
            fetch_gmail,
            "_gmail_request",
            side_effect=gmail_request,
        ), patch.object(
            fetch_gmail,
            "refresh_gmail_context",
            return_value={"status": "ok", "context": refreshed_context},
        ) as refresh:
            report = self._run()

        self.assertEqual(report.completed, 8)
        self.assertEqual(len(provider_calls), 9)
        refresh.assert_called_once()
        self.assertEqual(
            {token for token, _path in provider_calls[2:]},
            {"refreshed-recovery-token"},
        )

    def test_existing_candidate_avoids_get_and_recovery_matches_window_shape(self):
        self._queue_google("gmail-message-1")
        expected_scope, expected_snapshot = project_priority_candidate(
            gmail_authority(),
            gmail_source(),
        )
        self.candidate_store.upsert_confirmed(
            expected_scope,
            expected_snapshot,
            expected_version=0,
        )
        request = Mock()
        present = self._run(request_with_one_refresh=request)
        self.assertEqual(present.completed, 1)
        request.assert_not_called()

        self.setUp()
        self._queue_google("gmail-message-1")

        def exact_request(context: dict, _request_path: str):
            return (
                gmail_recovery_detail(
                    "gmail-message-1",
                    thread_id="gmail-thread-1",
                ),
                None,
                context,
                None,
            )

        recovered = self._run(request_with_one_refresh=exact_request)
        self.assertEqual(
            recovered.result_counts,
            ((CandidateRecoveryConsumerResult.PROVIDER_RECOVERED.value, 1),),
        )
        record = self.candidate_store.read_candidate(expected_scope)
        assert record is not None
        self.assertEqual(record.snapshot, expected_snapshot)
        self.assertEqual(
            record.snapshot.conversation.provider_thread_id,
            "gmail-thread-1",
        )

    def test_terminal_absence_leaves_workflow_authority_untouched(self):
        for message_id, response in (
            (
                "deleted-message",
                (
                    None,
                    {"code": "gmail_message_not_found"},
                ),
            ),
            (
                "archived-message",
                (
                    gmail_recovery_detail(
                        "archived-message",
                        labels=["STARRED"],
                    ),
                    None,
                ),
            ),
        ):
            with self.subTest(message_id=message_id):
                self.setUp()
                workflow_scope = self._queue_google(message_id)

                def request(context: dict, _request_path: str):
                    payload, error = response
                    return payload, error, context, None

                report = self._run(request_with_one_refresh=request)
                self.assertEqual(report.completed, 1)
                self.assertEqual(
                    report.result_counts,
                    ((
                        CandidateRecoveryConsumerResult.PROVIDER_TERMINAL_ABSENT.value,
                        1,
                    ),),
                )
                self.assertEqual(len(self.recovery_store.acked), 1)
                durable = self.workflow_store.read_records((workflow_scope,))[0]
                self.assertEqual(durable.manual_priority, "priority")
                scope = PriorityCandidateScope(
                    workspace_id="workspace-1",
                    user_id="user-1",
                    mailbox_id="mailbox-1",
                    mailbox_account_identity="owner@gmail.test",
                    provider="google",
                    identity=workflow_scope.identity,
                )
                self.assertIsNone(self.candidate_store.read_candidate(scope))

    def test_persistence_and_reconciliation_failures_retry_without_ack(self):
        self._queue_google("persistence-failure")

        def request(context: dict, _request_path: str):
            return (
                gmail_recovery_detail("persistence-failure"),
                None,
                context,
                None,
            )

        with patch.object(
            fetch_gmail,
            "populate_priority_candidates",
            return_value=SimpleNamespace(written=0, incomplete=True),
        ):
            persistence = self._run(request_with_one_refresh=request)
        self.assertEqual(persistence.completed, 0)
        self.assertEqual(persistence.rescheduled, 1)
        self.assertFalse(self.recovery_store.acked)

        self.setUp()
        self._queue_google("reconciliation-failure")

        def reconciliation_request(context: dict, _request_path: str):
            return (
                gmail_recovery_detail("reconciliation-failure"),
                None,
                context,
                None,
            )

        with patch(
            "api.priority.candidate_recovery.reconcile_workflow_candidate_references",
            return_value=CandidateReferenceReconciliationResult.STORE_UNAVAILABLE,
        ):
            reconciliation = self._run(
                request_with_one_refresh=reconciliation_request
            )
        self.assertEqual(reconciliation.completed, 0)
        self.assertEqual(reconciliation.rescheduled, 1)
        self.assertFalse(self.recovery_store.acked)


class CandidatePopulationContinuationTests(unittest.TestCase):
    def setUp(self):
        self.redis = MemoryRedis()
        self.store = PriorityCandidateStore(self.redis, hmac_secret=SECRET)
        self.workflow_redis = WorkflowMemoryRedis()
        self.workflow_store = PriorityWorkflowStore(
            self.workflow_redis,
            hmac_secret=SECRET,
        )
        self.authority = gmail_authority()

    def test_rejected_row_does_not_block_a_valid_row(self):
        result = populate_priority_candidates(
            self.authority,
            [gmail_source(providerTimestampMillis=None, rfcDate=None), gmail_source()],
            store=self.store,
        )
        self.assertEqual(result.processed, 2)
        self.assertEqual(result.written, 1)
        self.assertEqual(result.skipped, 1)
        self.assertTrue(result.incomplete)
        self.assertEqual(result.reason_codes, ("candidate_timestamp_invalid",))
        self.assertEqual(
            result.reason_counts,
            (("candidate_timestamp_invalid", 1),),
        )

    def test_adapter_failures_are_stage_bounded_and_row_local(self):
        cases = (
            (
                gmail_source(providerMessageId=""),
                "candidate_identity_invalid",
            ),
            (
                gmail_source(providerTimestampMillis=None, rfcDate=None),
                "candidate_timestamp_invalid",
            ),
            (
                gmail_source(
                    providerTimestampMillis=None,
                    rfcDate="Tue, 01 Jul 2025 10:00:00",
                ),
                "candidate_timestamp_invalid",
            ),
            (
                gmail_source(providerThreadId=""),
                "candidate_conversation_invalid",
            ),
            (
                gmail_source(subject="invalid\rsubject"),
                "candidate_render_invalid",
            ),
            (
                gmail_source(senderAddress=""),
                "candidate_render_invalid",
            ),
            (
                gmail_source(senderAddress="Unknown"),
                "candidate_render_invalid",
            ),
            (
                gmail_source(senderDisplay="d" * 257),
                "candidate_render_invalid",
            ),
            (
                gmail_source(subject="s" * 999),
                "candidate_render_invalid",
            ),
            (
                gmail_source(subject="invalid\x01subject"),
                "candidate_render_invalid",
            ),
            (
                gmail_source(senderDisplay="\ud800"),
                "candidate_render_invalid",
            ),
            (
                gmail_source(unread=1),
                "candidate_render_invalid",
            ),
            (
                gmail_source(flagged=0),
                "candidate_render_invalid",
            ),
        )
        for source, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                report = populate_priority_candidates(
                    self.authority,
                    [source],
                    store=self.store,
                )
                self.assertEqual(report.processed, 1)
                self.assertEqual(report.written, 0)
                self.assertEqual(report.reason_counts, ((expected_reason, 1),))

        with patch.object(
            candidate_store_module.PriorityCandidateSnapshot,
            "validate_for_scope",
            side_effect=ValueError("content-must-not-escape"),
        ):
            snapshot_report = populate_priority_candidates(
                self.authority,
                [gmail_source()],
                store=self.store,
            )
        self.assertEqual(
            snapshot_report.reason_counts,
            (("candidate_snapshot_invalid", 1),),
        )

        duplicate_report = populate_priority_candidates(
            self.authority,
            [gmail_source(), gmail_source()],
            store=self.store,
        )
        self.assertEqual(duplicate_report.processed, 2)
        self.assertEqual(duplicate_report.written, 1)
        self.assertEqual(
            duplicate_report.reason_counts,
            (("candidate_duplicate", 1),),
        )

        aggregate_report = populate_priority_candidates(
            self.authority,
            [
                gmail_source(providerTimestampMillis=None, rfcDate=None),
                gmail_source(
                    providerMessageId="gmail-message-render-invalid",
                    providerThreadId="gmail-thread-render-invalid",
                    subject="invalid\rsubject",
                ),
                gmail_source(
                    providerMessageId="gmail-message-valid",
                    providerThreadId="gmail-thread-valid",
                ),
            ],
            store=self.store,
        )
        self.assertEqual(aggregate_report.processed, 3)
        self.assertEqual(aggregate_report.written, 1)
        self.assertEqual(
            aggregate_report.reason_counts,
            (
                ("candidate_render_invalid", 1),
                ("candidate_timestamp_invalid", 1),
            ),
        )

    def test_imap_repeat_dedupes_and_new_uidvalidity_keeps_old_namespace(self):
        authority = imap_authority()
        source = imap_source()
        first = populate_priority_candidates(authority, [source], store=self.store)
        second = populate_priority_candidates(authority, [source], store=self.store)
        self.assertEqual((first.written, second.written), (1, 1))
        old_scope, _ = project_priority_candidate(authority, source)
        self.assertEqual(self.store.read_candidate(old_scope).version, 2)

        replacement_namespace = imap_source(uidValidity="457")
        third = populate_priority_candidates(
            authority,
            [replacement_namespace],
            store=self.store,
        )
        new_scope, _ = project_priority_candidate(
            authority,
            replacement_namespace,
        )
        self.assertEqual(third.written, 1)
        self.assertEqual(self.store.read_candidate(old_scope).version, 2)
        self.assertEqual(self.store.read_candidate(new_scope).version, 1)
        page = self.store.read_mailbox_page(old_scope.mailbox_scope())
        self.assertEqual(page.total, 2)

    def test_store_unavailable_and_corrupt_store_are_isolated(self):
        unavailable = PriorityCandidateStore(
            lambda _command: (_ for _ in ()).throw(RuntimeError("offline")),
            hmac_secret=SECRET,
        )
        unavailable_report = populate_priority_candidates(
            self.authority,
            [gmail_source()],
            store=unavailable,
        )
        self.assertEqual(
            unavailable_report.reason_counts,
            (("store_read_transport", 1),),
        )
        self.assertEqual(unavailable_report.processed, 1)

        scope, _ = project_priority_candidate(self.authority, gmail_source())
        keys = self.store._scope_keys(scope)
        self.redis.values[keys["record"]] = "not-json"
        corrupt_report = populate_priority_candidates(
            self.authority,
            [gmail_source()],
            store=self.store,
        )
        self.assertEqual(
            corrupt_report.reason_counts,
            (("store_read_postcondition_invalid", 1),),
        )

    def test_provider_refresh_repairs_only_exact_malformed_v2_without_references(self):
        source = gmail_source()
        other_source = gmail_source(
            providerMessageId="gmail-message-unrelated",
            providerThreadId="gmail-thread-unrelated",
        )
        initial = populate_priority_candidates(
            self.authority,
            [source, other_source],
            store=self.store,
        )
        self.assertEqual(initial.written, 2)
        scope, _snapshot = project_priority_candidate(self.authority, source)
        other_scope, _ = project_priority_candidate(self.authority, other_source)
        keys = self.store._scope_keys(scope)
        other_before = self.store.read_candidate(other_scope)

        malformed = json.loads(self.redis.values[keys["record"]])
        malformed["routingState"] = "ready"
        malformed["routing"] = {
            "signal": None,
            "uiSignal": "REPLY",
            "internalClassification": "reply",
            "category": "reply",
            "finalVisibility": None,
            "action": None,
            "v7FinalPriority": None,
            "noiseDisposition": "none",
            "noiseConfidence": "low",
            "noiseReasons": {},
            "classifierVersion": "test-classifier-v1",
            "routingVersion": "test-routing-v1",
        }
        self.redis.values[keys["record"]] = self.redis._encode(malformed)

        repaired = populate_priority_candidates(
            self.authority,
            [source],
            store=self.store,
        )
        self.assertEqual((repaired.written, repaired.skipped), (1, 0))
        self.assertFalse(repaired.incomplete)
        record = self.store.read_candidate(scope)
        self.assertEqual(record.version, 1)
        self.assertEqual(record.snapshot.routing_state, "unresolved")
        self.assertIsNone(record.snapshot.routing)
        self.assertTrue(
            all(reference.expires_at == 0 for reference in record.positive_references)
        )
        for index_key in (
            keys["mailbox_index"],
            keys["user_index"],
            keys["namespace_index"],
        ):
            self.assertEqual(
                self.redis.sorted_sets[index_key][keys["member"]],
                record.logical_expires_at(),
            )
        self.assertEqual(self.store.read_candidate(other_scope), other_before)
        self.assertFalse(any(command[0] == "SCAN" for command in self.redis.commands))

    def test_malformed_v2_with_nonzero_reference_is_not_repaired(self):
        source = gmail_source()
        populate_priority_candidates(self.authority, [source], store=self.store)
        scope, _snapshot = project_priority_candidate(self.authority, source)
        keys = self.store._scope_keys(scope)
        malformed = json.loads(self.redis.values[keys["record"]])
        malformed["routingState"] = "ready"
        malformed["routing"] = {
            "signal": None,
            "uiSignal": "REPLY",
            "internalClassification": "reply",
            "category": "reply",
            "finalVisibility": None,
            "action": None,
            "v7FinalPriority": None,
            "noiseDisposition": "none",
            "noiseConfidence": "low",
            "noiseReasons": {},
            "classifierVersion": "test-classifier-v1",
            "routingVersion": "test-routing-v1",
        }
        malformed["positiveReferences"]["manual_priority"] = (
            malformed["absoluteExpiresAt"]
        )
        encoded = self.redis._encode(malformed)
        self.redis.values[keys["record"]] = encoded

        refused = populate_priority_candidates(
            self.authority,
            [source],
            store=self.store,
        )
        self.assertEqual(refused.written, 0)
        self.assertEqual(
            refused.reason_counts,
            (("store_repair_reference_proof_invalid", 1),),
        )
        self.assertEqual(self.redis.values[keys["record"]], encoded)

    def test_fatal_store_failure_counts_unprocessed_rows_without_more_work(self):
        commands: list[list[object]] = []

        def unavailable(command: list[object]) -> dict[str, object]:
            commands.append(command)
            raise TimeoutError("content-free-test-timeout")

        store = PriorityCandidateStore(unavailable, hmac_secret=SECRET)
        sources = [
            gmail_source(
                providerMessageId=f"gmail-message-{index}",
                providerThreadId=f"gmail-thread-{index}",
            )
            for index in range(50)
        ]
        report = populate_priority_candidates(
            self.authority,
            sources,
            store=store,
        )
        self.assertEqual(report.attempted, 50)
        self.assertEqual(report.processed, 1)
        self.assertEqual(report.written, 0)
        self.assertEqual(report.skipped, 50)
        self.assertEqual(
            report.reason_counts,
            (
                ("not_processed_after_store_failure", 49),
                ("store_read_transport", 1),
            ),
        )
        self.assertEqual(len(commands), 1)

    def test_prepare_diagnostic_stage_propagates_without_content(self):
        class PrepareFailure(MemoryRedis):
            def __init__(self, sentinel: str) -> None:
                super().__init__()
                self.sentinel = sentinel

            def __call__(self, command: list[object]) -> dict[str, object]:
                if (
                    command[0] == "EVAL"
                    and command[1]
                    == candidate_store_module._PREPARE_CONFIRMED_SCRIPT
                ):
                    return {"result": self.sentinel}
                return super().__call__(command)

        sensitive = gmail_source(
            providerMessageId="sensitive-provider-id",
            providerThreadId="sensitive-thread-id",
            senderDisplay="Sensitive Sender",
            senderAddress="sensitive-sender@example.test",
            subject="Sensitive Subject",
            snippet="Sensitive Snippet",
        )
        sources = [
            sensitive,
            *(
                gmail_source(
                    providerMessageId=f"remaining-message-{index}",
                    providerThreadId=f"remaining-thread-{index}",
                )
                for index in range(49)
            ),
        ]
        diagnostics = (
            (
                candidate_store_module._PREPARE_REFERENCE_INVALID_SENTINEL,
                "store_prepare_reference_invalid",
            ),
            (
                candidate_store_module._PREPARE_TEMPORAL_INVALID_SENTINEL,
                "store_prepare_temporal_invalid",
            ),
        )
        for sentinel, expected_stage in diagnostics:
            with self.subTest(expected_stage=expected_stage):
                report = populate_priority_candidates(
                    self.authority,
                    sources,
                    store=PriorityCandidateStore(
                        PrepareFailure(sentinel),
                        hmac_secret=SECRET,
                    ),
                )
                self.assertEqual(
                    (
                        report.attempted,
                        report.processed,
                        report.written,
                        report.skipped,
                        report.incomplete,
                    ),
                    (50, 1, 0, 50, True),
                )
                self.assertEqual(
                    report.reason_counts,
                    (
                        ("not_processed_after_store_failure", 49),
                        (expected_stage, 1),
                    ),
                )
                output = repr(report)
                for private in (
                    "sensitive-provider-id",
                    "sensitive-thread-id",
                    "Sensitive Sender",
                    "sensitive-sender@example.test",
                    "Sensitive Subject",
                    "Sensitive Snippet",
                ):
                    self.assertNotIn(private, output)

    def test_mailbox_and_user_caps_mark_incomplete_without_eviction(self):
        scope, _ = project_priority_candidate(self.authority, gmail_source())
        with patch.object(candidate_store_module, "CANDIDATE_MAX_MAILBOX_RECORDS", 0):
            mailbox_report = populate_priority_candidates(
                self.authority,
                [gmail_source()],
                store=self.store,
            )
        self.assertEqual(mailbox_report.reason_codes, ("mailbox_capacity",))
        mailbox_page = self.store.read_mailbox_page(scope.mailbox_scope())
        self.assertTrue(mailbox_page.mailbox_incomplete)
        self.assertEqual(mailbox_page.total, 0)

        other_redis = MemoryRedis()
        other_store = PriorityCandidateStore(other_redis, hmac_secret=SECRET)
        with patch.object(candidate_store_module, "CANDIDATE_MAX_USER_RECORDS", 0):
            user_report = populate_priority_candidates(
                self.authority,
                [gmail_source()],
                store=other_store,
            )
        self.assertEqual(user_report.reason_codes, ("user_capacity",))
        user_page = other_store.read_mailbox_page(scope.mailbox_scope())
        self.assertTrue(user_page.user_incomplete)
        self.assertEqual(user_page.total, 0)

    def test_serialized_record_contains_no_body_or_ready_routing(self):
        source = gmail_source(snippet="private snippet marker")
        result = populate_priority_candidates(
            self.authority,
            [source],
            store=self.store,
        )
        self.assertFalse(result.incomplete)
        records = [
            json.loads(value)
            for key, value in self.redis.values.items()
            if ":record:" in key
        ]
        self.assertEqual(len(records), 1)
        serialized = records[0]
        self.assertEqual(serialized["schemaVersion"], 2)
        self.assertEqual(serialized["routingState"], "unresolved")
        self.assertIsNone(serialized["routing"])
        encoded = json.dumps(serialized)
        for forbidden in (
            '"body"',
            '"bodyText"',
            '"bodyHtml"',
            '"attachment"',
            '"raw"',
            '"html"',
        ):
            self.assertNotIn(forbidden, encoded)

    def test_runtime_boundary_rejects_bad_authority_without_throwing(self):
        with self.assertLogs(
            "api.priority.candidate_projection",
            level="WARNING",
        ):
            report = populate_runtime_priority_candidates(
                member=SimpleNamespace(workspace_id="workspace-1"),
                mailbox_id="mailbox-1",
                mailbox_account_identity="owner@gmail.test",
                provider="google",
                sources=[gmail_source()],
                store=self.store,
                workflow_store=self.workflow_store,
            )
        self.assertEqual(report.reason_codes, ("authority_invalid",))

    def test_operational_failure_log_is_bounded_and_content_free(self):
        sensitive_source = gmail_source(
            providerMessageId="sensitive-provider-id",
            senderDisplay="Sensitive Sender",
            senderAddress="sensitive@example.test",
            subject="Sensitive Subject",
            snippet="Sensitive Snippet",
            providerTimestampMillis=None,
            rfcDate=None,
        )
        sensitive_imap_source = imap_source(
            providerFolder="Sensitive Folder",
            imapUid="987654-sensitive-uid",
            rfcRootMessageId="sensitive-root@message-id.test",
            rfcMessageId="sensitive-message@message-id.test",
            senderDisplay="Sensitive IMAP Sender",
            senderAddress="sensitive-imap@example.test",
            subject="Sensitive IMAP Subject",
            snippet="Sensitive IMAP Snippet",
        )
        sensitive_store = PriorityCandidateStore(
            lambda _command: (_ for _ in ()).throw(
                RuntimeError(
                    "https://sensitive-redis.invalid sensitive-redis-token"
                )
            ),
            hmac_secret=SECRET,
        )

        class DiagnosticFailure(MemoryRedis):
            def __call__(self, command: list[object]) -> dict[str, object]:
                if (
                    command[0] == "EVAL"
                    and command[1]
                    == candidate_store_module._PREPARE_CONFIRMED_SCRIPT
                ):
                    return {"result": ["malformed-sensitive-metadata"]}
                return super().__call__(command)

        diagnostic_source = gmail_source(
            providerMessageId="sensitive-diagnostic-provider-id",
            providerThreadId="sensitive-diagnostic-thread-id",
            senderDisplay="Sensitive Diagnostic Sender",
            senderAddress="sensitive-diagnostic@example.test",
            subject="Sensitive Diagnostic Subject",
            snippet="Sensitive Diagnostic Snippet",
        )
        with self.assertRaises(ValueError) as projection_error:
            project_priority_candidate(self.authority, sensitive_source)
        self.assertEqual(
            str(projection_error.exception),
            "invalid Priority candidate projection",
        )
        with self.assertLogs(
            "api.priority.candidate_projection",
            level="WARNING",
        ) as captured:
            report = populate_runtime_priority_candidates(
                member=SimpleNamespace(
                    workspace_id="workspace-1",
                    user_id="user-1",
                ),
                mailbox_id="mailbox-1",
                mailbox_account_identity="owner@gmail.test",
                provider="google",
                sources=[sensitive_source],
                store=self.store,
                workflow_store=self.workflow_store,
            )
            store_report = populate_runtime_priority_candidates(
                member=SimpleNamespace(
                    workspace_id="workspace-1",
                    user_id="user-1",
                ),
                mailbox_id="mailbox-1",
                mailbox_account_identity="owner@gmail.test",
                provider="google",
                sources=[gmail_source()],
                store=sensitive_store,
                workflow_store=self.workflow_store,
            )
            imap_report = populate_runtime_priority_candidates(
                member=SimpleNamespace(
                    workspace_id="workspace-1",
                    user_id="user-1",
                ),
                mailbox_id="mailbox-1",
                mailbox_account_identity="owner@imap.test",
                provider="custom_imap",
                sources=[sensitive_imap_source],
                store=self.store,
                workflow_store=self.workflow_store,
            )
            diagnostic_report = populate_runtime_priority_candidates(
                member=SimpleNamespace(
                    workspace_id="workspace-1",
                    user_id="user-1",
                ),
                mailbox_id="mailbox-1",
                mailbox_account_identity="owner@gmail.test",
                provider="google",
                sources=[diagnostic_source],
                store=PriorityCandidateStore(
                    DiagnosticFailure(),
                    hmac_secret=SECRET,
                ),
                workflow_store=self.workflow_store,
            )
        output = "\n".join(captured.output)
        self.assertEqual(report.reason_codes, ("candidate_timestamp_invalid",))
        self.assertEqual(store_report.reason_codes, ("store_read_transport",))
        self.assertEqual(imap_report.reason_codes, ("candidate_identity_invalid",))
        self.assertEqual(
            diagnostic_report.reason_codes,
            ("store_prepare_metadata_invalid",),
        )
        self.assertIn("attempted=1 processed=1 written=0 skipped=1", output)
        self.assertIn("incomplete=True", output)
        self.assertIn("store_prepare_metadata_invalid:1", output)
        for sensitive in (
            "sensitive-provider-id",
            "Sensitive Sender",
            "sensitive@example.test",
            "Sensitive Subject",
            "Sensitive Snippet",
            "Sensitive Folder",
            "987654-sensitive-uid",
            "sensitive-root@message-id.test",
            "sensitive-message@message-id.test",
            "Sensitive IMAP Sender",
            "sensitive-imap@example.test",
            "Sensitive IMAP Subject",
            "Sensitive IMAP Snippet",
            "sensitive-redis.invalid",
            "sensitive-redis-token",
            "sensitive-diagnostic-provider-id",
            "sensitive-diagnostic-thread-id",
            "Sensitive Diagnostic Sender",
            "sensitive-diagnostic@example.test",
            "Sensitive Diagnostic Subject",
            "Sensitive Diagnostic Snippet",
            "malformed-sensitive-metadata",
            "mailbox-1",
        ):
            self.assertNotIn(sensitive, output)


if __name__ == "__main__":
    unittest.main()
