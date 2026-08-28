from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from . import candidate_store as candidate_store_module
from .candidate_projection import (
    PriorityCandidatePopulationAuthority,
    populate_priority_candidates,
    populate_runtime_priority_candidates,
    project_priority_candidate,
)
from .candidate_store import PriorityCandidateStore
from .test_candidate_store import MemoryRedis, SECRET


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

    def test_rejected_row_does_not_block_a_valid_row(self):
        result = populate_priority_candidates(
            self.authority,
            [gmail_source(providerTimestampMillis=None, rfcDate=None), gmail_source()],
            store=self.store,
        )
        self.assertEqual(result.written, 1)
        self.assertEqual(result.skipped, 1)
        self.assertTrue(result.incomplete)
        self.assertEqual(result.reason_codes, ("candidate_invalid",))

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
        self.assertEqual(unavailable_report.reason_codes, ("store_unavailable",))

        scope, _ = project_priority_candidate(self.authority, gmail_source())
        keys = self.store._scope_keys(scope)
        self.redis.values[keys["record"]] = "not-json"
        corrupt_report = populate_priority_candidates(
            self.authority,
            [gmail_source()],
            store=self.store,
        )
        self.assertEqual(corrupt_report.reason_codes, ("store_unavailable",))

    def test_mailbox_and_user_caps_mark_incomplete_without_eviction(self):
        scope, _ = project_priority_candidate(self.authority, gmail_source())
        with patch.object(candidate_store_module, "CANDIDATE_MAX_MAILBOX_RECORDS", 0):
            mailbox_report = populate_priority_candidates(
                self.authority,
                [gmail_source()],
                store=self.store,
            )
        self.assertEqual(mailbox_report.reason_codes, ("capacity_exceeded",))
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
        self.assertEqual(user_report.reason_codes, ("capacity_exceeded",))
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
            )
        output = "\n".join(captured.output)
        self.assertEqual(report.reason_codes, ("candidate_invalid",))
        for sensitive in (
            "sensitive-provider-id",
            "Sensitive Sender",
            "sensitive@example.test",
            "Sensitive Subject",
            "Sensitive Snippet",
            "mailbox-1",
        ):
            self.assertNotIn(sensitive, output)


if __name__ == "__main__":
    unittest.main()
