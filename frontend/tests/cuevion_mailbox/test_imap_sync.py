"""Focused IMAP core tests, including the real shared PostgreSQL adapter.

Integration tests accept ONLY the dedicated loopback fixture DSN below. Run a
fresh local PostgreSQL/PGlite instance there; never supply runtime Neon URLs.
The schema is compiled from the existing mailbox definition, not a migration.
"""
from __future__ import annotations

import os
import importlib.util
import threading
import socket
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from email import message_from_bytes
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

import psycopg
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from cuevion_mailbox import postgresql_repository as storage
from cuevion_mailbox.imap_projection import derive_imap_message_id, project_imap_message
from cuevion_mailbox.imap_refresh import (
    _recover_selected_uid, imap_durable_write_enabled, prepare_authenticated_imap_refresh,
)
from cuevion_mailbox.imap_sync import sync_imap_snapshot
from cuevion_mailbox.imap_deadline import (
    ImapDeadlineExceeded, ImapDurableBudget, DeadlineImapFile, bounded_imap_connect,
)
from cuevion_mailbox.repository_contract import (
    BodyState, BootstrapState, DeltaCommitOutcome, MailboxProvider, MailboxReadAuthority,
    MailboxScope, OutboxEventType,
)
from cuevion_mailbox.schema import MAILBOX_TABLES


LOCAL_FIXTURE_DSN = "postgresql://postgres@127.0.0.1:55439/postgres?sslmode=disable"
TEST_DSN = os.environ.get("CUEVION_MAILBOX_IMAP_TEST_DSN")
WORKSPACE = "wsp_" + "a" * 22
OWNER = "usr_" + "b" * 22
RAW = (
    b"From: Sender <sender@example.test>\r\n"
    b"To: Owner <owner@example.test>\r\nCc: Copy <copy@example.test>\r\n"
    b"Date: Tue, 06 Oct 2026 10:00:00 +0000\r\n"
    b"Message-ID: <same-rfc@example.test>\r\n"
    b"Subject: Durable IMAP\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nBody.\r\n"
)


def authority(**changes):
    values = dict(workspace_id=WORKSPACE, owner_user_id=OWNER, mailbox_id="imap-1",
                  provider=MailboxProvider.CUSTOM_IMAP, provider_account_identity="owner@example.test")
    values.update(changes)
    return MailboxReadAuthority(**values)


def scope(**changes):
    values = dict(workspace_id=WORKSPACE, owner_user_id=OWNER, mailbox_id="imap-1",
                  provider=MailboxProvider.CUSTOM_IMAP, provider_account_identity="owner@example.test",
                  source_generation=1)
    values.update(changes)
    return MailboxScope(**values)


def item(uid="1", unread=True, starred=False, raw=RAW):
    return message_from_bytes(raw), unread, str(uid), starred


class ReadOnlyFixtureMailbox:
    """One provider connection; recorded calls make any mutation observable."""
    def __init__(self, uids=("1",), validity="456"):
        self.uids = list(uids)
        self.validity = validity
        self.calls = []
        self.sock = Mock()
        self.sock.gettimeout.return_value = 30
        self.sock.fileno.return_value = 1
        self.file = Mock()

    def login(self, username, password):
        self.calls.append(("login", username))
        return "OK", []

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [str(len(self.uids)).encode()]

    def search(self, charset, criteria):
        self.calls.append(("search", criteria))
        return "OK", [" ".join(str(index + 1) for index in range(len(self.uids))).encode()]

    def fetch(self, sequence, query):
        self.calls.append(("fetch", sequence, query))
        uid = self.uids[int(sequence) - 1]
        return "OK", [(f"{sequence.decode()} (UID {uid} FLAGS () BODY[] {{{len(RAW)}}}".encode(), RAW), b")"]

    def uid(self, command, *args):
        self.calls.append(("uid", command, *args))
        if command.lower() == "search":
            return "OK", [" ".join(self.uids).encode()]
        if command == "FETCH":
            uid = args[0]
            return "OK", [(f"1 (UID {uid} FLAGS () BODY[] {{{len(RAW)}}}".encode(), RAW), b")"]
        raise AssertionError("unexpected provider operation")

    def response(self, name):
        self.calls.append(("response", name))
        return name, [self.validity.encode() if name == "UIDVALIDITY" else str(max(map(int, self.uids), default=0) + 1).encode()]

    def logout(self):
        self.calls.append(("logout",))


class ImapProjectionTests(unittest.TestCase):
    def test_canonical_record_contains_provider_metadata_and_body(self):
        record, body = project_imap_message(scope(), folder="INBOX", uid_validity="456", item=item())
        self.assertIsNone(record.identity.provider_message_id)
        self.assertEqual(record.rfc_message_id, "same-rfc@example.test")
        self.assertEqual(record.sender_address, "sender@example.test")
        self.assertEqual(record.to_recipients, ("owner@example.test",))
        self.assertEqual(record.cc_recipients, ("copy@example.test",))
        self.assertEqual(record.provider_timestamp_millis, 1791280800000)
        self.assertTrue(record.unread)
        self.assertEqual(record.body_state, BodyState.CACHED)
        self.assertIn("Body.", body.body_text)

    def test_rfc_message_id_is_never_copy_identity(self):
        first, _ = project_imap_message(scope(), folder="INBOX", uid_validity="456", item=item(1))
        second, _ = project_imap_message(scope(), folder="INBOX", uid_validity="456", item=item(2))
        self.assertEqual(first.rfc_message_id, second.rfc_message_id)
        self.assertNotEqual(first.identity.message_id, second.identity.message_id)

    def test_every_authority_and_provider_locator_component_isolated(self):
        original = derive_imap_message_id(scope(), "INBOX", "456", 1)
        variants = [
            (scope(workspace_id="wsp_" + "c" * 22), "INBOX", "456", 1),
            (scope(owner_user_id="usr_" + "c" * 22), "INBOX", "456", 1),
            (scope(mailbox_id="imap-2"), "INBOX", "456", 1),
            (scope(provider_account_identity="other@example.test"), "INBOX", "456", 1),
            (scope(source_generation=2), "INBOX", "456", 1),
            (scope(), "Archive", "456", 1), (scope(), "INBOX", "457", 1),
            (scope(), "INBOX", "456", 2),
        ]
        ids = [derive_imap_message_id(*variant) for variant in variants]
        self.assertNotIn(original, ids)
        self.assertEqual(len(ids), len(set(ids)))

    def test_uidvalidity_can_exceed_bigint_without_becoming_source_generation(self):
        result = derive_imap_message_id(scope(), "INBOX", "99999999999999999999", 1)
        self.assertEqual(len(result), 26)

    def test_invalid_provider_uid_and_folder_fail_closed(self):
        for folder, validity, uid in [("INBOX\x00", "456", 1), ("INBOX", "0456", 1),
                                      ("INBOX", "456", 0), ("INBOX", "456", True),
                                      ("INBOX", "456", 4_294_967_296)]:
            with self.subTest(folder=folder, validity=validity, uid=uid), self.assertRaises(ValueError):
                derive_imap_message_id(scope(), folder, validity, uid)

    def test_invalid_date_does_not_change_on_each_poll(self):
        bad_date = RAW.replace(b"Tue, 06 Oct 2026 10:00:00 +0000", b"invalid")
        first, _ = project_imap_message(scope(), folder="INBOX", uid_validity="456", item=item(raw=bad_date))
        second, _ = project_imap_message(scope(), folder="INBOX", uid_validity="456", item=item(raw=bad_date))
        self.assertEqual(first, second)
        self.assertEqual(first.provider_timestamp_millis, 0)

    def test_storage_text_is_nul_safe(self):
        record, body = project_imap_message(scope(), folder="INBOX", uid_validity="456",
                                           item=item(raw=RAW.replace(b"Body.", b"Body.\x00")))
        self.assertNotIn("\x00", body.body_text)
        self.assertNotIn("\x00", record.snippet)


class ImapRefreshAdapterTests(unittest.TestCase):
    def test_gates_reuse_existing_runtime_configuration(self):
        self.assertFalse(imap_durable_write_enabled({}))
        self.assertFalse(imap_durable_write_enabled({"VERCEL_ENV": "production",
                                                    "CUEVION_MAILBOX_POSTGRES_MODE": "active_write"}))
        self.assertTrue(imap_durable_write_enabled({"VERCEL_ENV": "preview",
                                                   "CUEVION_MAILBOX_POSTGRES_MODE": "active_write"}))

    def test_disabled_gate_does_not_resolve_or_connect(self):
        repositories = Mock()
        self.assertIsNone(prepare_authenticated_imap_refresh(environment={}, member=None,
                                                            mailbox={}, repositories=repositories))
        self.assertEqual(repositories.mock_calls, [])

    def test_no_member_authority_never_writes(self):
        repositories = Mock()
        callback = prepare_authenticated_imap_refresh(
            environment={"VERCEL_ENV": "preview", "CUEVION_MAILBOX_POSTGRES_MODE": "active_write"},
            member=None, mailbox={"mailboxId": "imap-1", "email": "owner@example.test"},
            repositories=repositories,
        )
        self.assertIsNone(callback)
        self.assertEqual(repositories.mock_calls, [])

    def test_exact_recovery_is_uid_scoped_peek_with_provider_flags(self):
        mailbox = Mock()
        mailbox.uid.return_value = ("OK", [(f"1 (UID 9 FLAGS (\\Seen \\Flagged) BODY[] {{{len(RAW)}}}".encode(), RAW), b")"])
        result = _recover_selected_uid(mailbox, "9")
        self.assertEqual(result[1:], (False, "9", True))
        self.assertEqual(mailbox.mock_calls, [unittest.mock.call.uid("FETCH", "9", "(UID FLAGS BODY.PEEK[])")])

    def test_recovery_rejects_other_uid_or_incomplete_literal(self):
        mailbox = Mock()
        mailbox.uid.return_value = ("OK", [(f"1 (UID 10 FLAGS () BODY[] {{{len(RAW)}}}".encode(), RAW), b")"])
        self.assertIsNone(_recover_selected_uid(mailbox, "9"))
        mailbox.uid.return_value = ("OK", [(b"1 (UID 9 FLAGS () BODY[] {999}", RAW), b")"])
        self.assertIsNone(_recover_selected_uid(mailbox, "9"))

    def test_recovery_accepts_uid_and_flags_after_literal_without_relaxing_identity(self):
        mailbox = Mock()
        for before, after in [("", b" UID 9 FLAGS (\\Seen))"),
                              ("FLAGS (\\Seen) ", b" UID 9)"),
                              ("UID 9 ", b" FLAGS (\\Seen))")]:
            with self.subTest(before=before, after=after):
                mailbox.uid.return_value = ("OK", [(f"1 ({before}BODY[] {{{len(RAW)}}}".encode(), RAW), after])
                self.assertEqual(_recover_selected_uid(mailbox, "9")[1:], (False, "9", False))
        for after in [b" UID 8 FLAGS ())", b" UID 9 UID 9 FLAGS ())", b" UID 9 FLAGS ()) extra", b" UID 9)"]:
            with self.subTest(after=after):
                mailbox.uid.return_value = ("OK", [(f"1 (BODY[] {{{len(RAW)}}}".encode(), RAW), after])
                self.assertIsNone(_recover_selected_uid(mailbox, "9"))

    def test_connection_acquisition_stall_obeys_remaining_application_budget(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        release = threading.Event()

        def hold_connection():
            connection, _ = listener.accept()
            with connection:
                release.wait(timeout=3)

        thread = threading.Thread(target=hold_connection)
        thread.start()
        budget = ImapDurableBudget(0.15)
        started = time.monotonic()
        try:
            with self.assertRaises(TimeoutError):
                bounded_imap_connect(budget)(
                    f"postgresql://fixture@127.0.0.1:{listener.getsockname()[1]}/fixture?sslmode=disable",
                )
            self.assertLess(time.monotonic() - started, 0.8)
        finally:
            release.set()
            thread.join(timeout=3)
            listener.close()
        self.assertFalse(thread.is_alive())

    def test_trickle_literal_cannot_extend_absolute_recovery_deadline(self):
        client, server = socket.socketpair()
        stopped = threading.Event()

        def trickle():
            with server:
                while not stopped.wait(0.03):
                    try:
                        server.sendall(b"x")
                    except OSError:
                        break

        thread = threading.Thread(target=trickle)
        thread.start()
        original = client.makefile("rb")
        budget = ImapDurableBudget(0.15)
        reader = DeadlineImapFile(original, client, budget)
        started = time.monotonic()
        try:
            with self.assertRaises(TimeoutError):
                reader.read(100)
            self.assertLess(time.monotonic() - started, 0.8)
        finally:
            stopped.set()
            original.close()
            client.close()
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())

    def test_recovery_timeout_closes_private_connection_and_restores_file_reference(self):
        mailbox = ReadOnlyFixtureMailbox()
        original = mailbox.file
        mailbox.uid = Mock(side_effect=TimeoutError)
        with self.assertRaises(ImapDeadlineExceeded):
            _recover_selected_uid(mailbox, "1", ImapDurableBudget())
        mailbox.sock.close.assert_called_once()
        original.close.assert_called_once()
        self.assertIs(mailbox.file, original)

    def test_budget_excludes_existing_provider_read_but_consumes_side_path_work(self):
        with patch("cuevion_mailbox.imap_deadline.time.monotonic", side_effect=[10, 11, 100, 100, 102]):
            budget = ImapDurableBudget()
            budget.pause()
            budget.resume()
            self.assertEqual(budget.remaining(), 2)

    def test_oversized_recovery_literal_is_rejected_before_allocation(self):
        original, sock = Mock(), Mock()
        reader = DeadlineImapFile(original, sock, ImapDurableBudget())
        with self.assertRaises(ImapDeadlineExceeded):
            reader.read(25 * 1024 * 1024 + 1)
        original.read1.assert_not_called()


@unittest.skipUnless(TEST_DSN, "dedicated local IMAP test database not enabled")
class ImapPostgreSQLSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if TEST_DSN != LOCAL_FIXTURE_DSN:
            raise RuntimeError("IMAP tests require the exact dedicated loopback fixture DSN")
        with psycopg.connect(LOCAL_FIXTURE_DSN, autocommit=True, prepare_threshold=None) as conn:
            conn.execute("CREATE SCHEMA cuevion_account")
            conn.execute("CREATE TABLE cuevion_account.workspace_memberships (workspace_id varchar(26), user_id varchar(26), PRIMARY KEY (workspace_id, user_id))")
            conn.execute("CREATE SCHEMA cuevion_mailbox")
            dialect = postgresql.dialect()
            for table in MAILBOX_TABLES:
                conn.execute(str(CreateTable(table).compile(dialect=dialect)))
                for index in table.indexes:
                    conn.execute(str(CreateIndex(index).compile(dialect=dialect)))

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(LOCAL_FIXTURE_DSN, autocommit=True, prepare_threshold=None) as conn:
            conn.execute("DROP SCHEMA cuevion_mailbox CASCADE")
            conn.execute("DROP SCHEMA cuevion_account CASCADE")

    def setUp(self):
        with psycopg.connect(LOCAL_FIXTURE_DSN, autocommit=True, prepare_threshold=None) as conn:
            conn.execute("TRUNCATE cuevion_account.workspace_memberships CASCADE")
            for workspace in [WORKSPACE, "wsp_" + "c" * 22]:
                for owner in [OWNER, "usr_" + "c" * 22]:
                    conn.execute("INSERT INTO cuevion_account.workspace_memberships VALUES (%s, %s)", (workspace, owner))
        factory = lambda: psycopg.connect(LOCAL_FIXTURE_DSN, prepare_threshold=None)
        self.repositories = SimpleNamespace(reader=storage.PostgreSQLMailboxReaderRepository(factory),
                                            writer=storage.PostgreSQLMailboxRepository(factory))
        self.clock = 1791280800000

    def sync(self, *, account=None, folder="INBOX", validity="456", uids=None,
             messages=None, uidnext=2, recover_uid=None, prepared_state=None):
        self.clock += 1
        return sync_imap_snapshot(
            repositories=self.repositories, authority=account or authority(),
            folder=folder, uid_validity=validity, uid_set=["1"] if uids is None else uids,
            uidnext_observed=uidnext, messages=[item()] if messages is None else messages,
            committed_at_millis=self.clock, recover_uid=recover_uid, prepared_state=prepared_state,
        )

    def rows(self, table, columns="*"):
        allowed = {"mailbox_sync_state", "mailbox_sync_cursor", "mailbox_messages",
                   "mailbox_message_bodies", "mailbox_change_outbox"}
        if table not in allowed:
            raise AssertionError("unknown fixture table")
        with psycopg.connect(LOCAL_FIXTURE_DSN, prepare_threshold=None) as conn:
            return conn.execute(f"SELECT {columns} FROM cuevion_mailbox.{table}").fetchall()

    def state(self, account=None):
        return self.repositories.reader.resolve_current_state(account or authority())

    def bounded_consumer(self, budget=None):
        budget = ImapDurableBudget() if budget is None else budget
        connect = bounded_imap_connect(budget)
        factory = lambda: connect(LOCAL_FIXTURE_DSN)
        repositories = SimpleNamespace(reader=storage.PostgreSQLMailboxReaderRepository(factory),
                                       writer=storage.PostgreSQLMailboxRepository(factory))
        return prepare_authenticated_imap_refresh(
            environment={"VERCEL_ENV": "preview", "CUEVION_MAILBOX_POSTGRES_MODE": "active_write"},
            member=SimpleNamespace(workspace_id=WORKSPACE, user_id=OWNER),
            mailbox={"mailboxId": "imap-1", "email": "owner@example.test"},
            repositories=repositories, budget=budget,
        )

    def provider_response(self, provider_mailbox, consumer=None, limit=2):
        import imap_connect_preview as provider
        with patch.object(provider, "open_mailbox_connection", return_value=provider_mailbox), \
             patch.object(provider, "resolve_preview_routing", return_value={}):
            return provider.build_connect_preview_response(
                dict(provider="custom_imap", mailboxId="imap-1", email="owner@example.test",
                     host="imap.example.test", port=993, ssl=True, password="fixture", limit=limit),
                durable_snapshot_consumer=consumer,
            )

    def test_lock_timeout_preserves_http_response_rolls_back_and_retries(self):
        self.sync()
        expected = self.provider_response(ReadOnlyFixtureMailbox(uids=("1", "2")))
        consumer = self.bounded_consumer()
        before = {name: self.rows(name) for name in ["mailbox_sync_state", "mailbox_sync_cursor", "mailbox_messages", "mailbox_message_bodies", "mailbox_change_outbox"]}
        with psycopg.connect(LOCAL_FIXTURE_DSN) as lock:
            lock.execute("SELECT 1 FROM cuevion_mailbox.mailbox_sync_state FOR UPDATE")
            started = time.monotonic()
            with patch("builtins.print") as diagnostic:
                actual = self.provider_response(ReadOnlyFixtureMailbox(uids=("1", "2")), consumer)
            elapsed = time.monotonic() - started
            self.assertEqual(actual, expected)
            self.assertEqual(actual[0], 200)
            self.assertLess(elapsed, 1.5)
            diagnostic.assert_any_call("cuevion_mailbox_active_write custom_imap database_timeout")
        for name, rows in before.items():
            self.assertEqual(self.rows(name), rows, name)
        self.provider_response(ReadOnlyFixtureMailbox(uids=("1", "2", "3", "4")), self.bounded_consumer())
        self.assertCountEqual(self.rows("mailbox_messages", "imap_uid"), [(1,), (2,), (3,), (4,)])
        self.assertEqual(self.repositories.reader.read_cursor(self.state().scope, "INBOX").imap_highest_uid, 4)

    def test_statement_timeout_first_bootstrap_commits_zero_state_then_shifted_retry_converges(self):
        expected = self.provider_response(ReadOnlyFixtureMailbox(uids=("1", "2")))
        consumer = self.bounded_consumer()
        started = time.monotonic()
        with patch.object(storage, "_UPSERT_BODY_SQL", "SELECT pg_sleep(2), %s, %s, %s, %s, %s, %s, %s, %s"), \
             patch("builtins.print") as diagnostic:
            actual = self.provider_response(ReadOnlyFixtureMailbox(uids=("1", "2")), consumer)
        self.assertEqual(actual, expected)
        self.assertLess(time.monotonic() - started, 1.8)
        diagnostic.assert_any_call("cuevion_mailbox_active_write custom_imap database_timeout")
        for name in ["mailbox_sync_state", "mailbox_sync_cursor", "mailbox_messages", "mailbox_message_bodies", "mailbox_change_outbox"]:
            self.assertEqual(self.rows(name), [], name)
        self.provider_response(ReadOnlyFixtureMailbox(uids=("1", "2", "3", "4")), self.bounded_consumer())
        self.assertCountEqual(self.rows("mailbox_messages", "imap_uid"), [(1,), (2,), (3,), (4,)])
        self.provider_response(ReadOnlyFixtureMailbox(uids=("1", "2", "3", "4")), self.bounded_consumer())
        self.assertEqual(len(self.rows("mailbox_change_outbox")), 4)

    def test_statement_timeout_replacement_preserves_epoch_and_shifted_retry_converges(self):
        self.sync()
        before = {name: self.rows(name) for name in ["mailbox_sync_state", "mailbox_sync_cursor", "mailbox_messages", "mailbox_message_bodies", "mailbox_change_outbox"]}
        consumer = self.bounded_consumer()
        expected = self.provider_response(ReadOnlyFixtureMailbox(uids=("1", "2"), validity="457"))
        with patch.object(storage, "_UPSERT_BODY_SQL", "SELECT pg_sleep(2), %s, %s, %s, %s, %s, %s, %s, %s"):
            actual = self.provider_response(ReadOnlyFixtureMailbox(uids=("1", "2"), validity="457"), consumer)
        self.assertEqual(actual, expected)
        for name, rows in before.items():
            self.assertEqual(self.rows(name), rows, name)
        self.provider_response(ReadOnlyFixtureMailbox(uids=("1", "2", "3", "4"), validity="457"), self.bounded_consumer())
        self.assertCountEqual(
            [uid for uid, generation in self.rows("mailbox_messages", "imap_uid, source_generation") if generation == 2],
            [1, 2, 3, 4],
        )

    def test_application_deadline_shorter_than_statement_timeout_aborts_all_progress(self):
        self.sync()
        consumer = self.bounded_consumer(ImapDurableBudget(0.3))
        before = {name: self.rows(name) for name in ["mailbox_sync_state", "mailbox_sync_cursor", "mailbox_messages", "mailbox_message_bodies", "mailbox_change_outbox"]}
        expected = self.provider_response(ReadOnlyFixtureMailbox(uids=("1", "2")))
        started = time.monotonic()
        with patch.object(storage, "_UPSERT_BODY_SQL", "SELECT pg_sleep(2), %s, %s, %s, %s, %s, %s, %s, %s"), \
             patch("builtins.print") as diagnostic:
            actual = self.provider_response(ReadOnlyFixtureMailbox(uids=("1", "2")), consumer)
        self.assertEqual(actual, expected)
        self.assertLess(time.monotonic() - started, 0.9)
        diagnostic.assert_any_call("cuevion_mailbox_active_write custom_imap timeout")
        for name, rows in before.items():
            self.assertEqual(self.rows(name), rows, name)

    def test_runtime_recovery_and_mutation_pages_converge_without_skipping_inventory(self):
        consumer = self.bounded_consumer()
        provider = ReadOnlyFixtureMailbox(uids=tuple(str(uid) for uid in range(1, 36)))
        self.provider_response(provider, consumer, limit=20)
        self.assertLessEqual(len([call for call in provider.calls if call[:2] == ("uid", "FETCH")]), 10)
        self.assertLessEqual(len(self.rows("mailbox_messages")), 20)
        self.assertEqual(self.repositories.reader.read_cursor(self.state().scope, "INBOX").imap_highest_uid, 10)
        for _ in range(5):
            self.provider_response(ReadOnlyFixtureMailbox(uids=tuple(str(uid) for uid in range(1, 36))), self.bounded_consumer(), limit=20)
        self.assertCountEqual(self.rows("mailbox_messages", "imap_uid"), [(uid,) for uid in range(1, 36)])
        self.assertEqual(len(self.rows("mailbox_change_outbox")), 35)
        cursor = self.repositories.reader.read_cursor(self.state().scope, "INBOX")
        self.assertEqual(cursor.imap_highest_uid, 35)
        self.assertEqual(cursor.backfill_state.value, "complete")
        self.assertIsNone(cursor.backfill_cursor)

    def test_provider_recovery_timeout_preserves_http_and_later_poll_recovers_failed_uid(self):
        self.sync()
        before = {name: self.rows(name) for name in ["mailbox_sync_state", "mailbox_sync_cursor", "mailbox_messages", "mailbox_message_bodies", "mailbox_change_outbox"]}
        provider = ReadOnlyFixtureMailbox(uids=("1", "2", "3"))
        original_uid = provider.uid
        def timeout_recovery(command, *args):
            if command == "FETCH":
                provider.calls.append(("uid", command, *args))
                raise TimeoutError()
            return original_uid(command, *args)
        provider.uid = timeout_recovery
        consumer = self.bounded_consumer()
        expected = self.provider_response(ReadOnlyFixtureMailbox(uids=("1", "2", "3")), limit=1)
        with patch("builtins.print") as diagnostic:
            actual = self.provider_response(provider, consumer, limit=1)
        self.assertEqual(actual, expected)
        diagnostic.assert_any_call("cuevion_mailbox_active_write custom_imap timeout")
        provider.sock.close.assert_called_once()
        for name, rows in before.items():
            self.assertEqual(self.rows(name), rows, name)
        self.provider_response(ReadOnlyFixtureMailbox(uids=("1", "2", "3", "4")), self.bounded_consumer(), limit=1)
        self.assertCountEqual(self.rows("mailbox_messages", "imap_uid"), [(1,), (2,), (3,), (4,)])
        self.assertTrue(all(call[1].lower() in {"search", "fetch"} for call in provider.calls if call[0] == "uid"))

    def test_failed_bootstrap_reconstruction_uses_only_uids_still_confirmed_present(self):
        with patch.object(storage, "_UPSERT_BODY_SQL", "SELECT nonexistent_test_column, %s, %s, %s, %s, %s, %s, %s, %s"):
            with self.assertRaises(psycopg.errors.UndefinedColumn):
                self.sync(uids=["1", "2"], messages=[item(2), item(1)], uidnext=3)
        recover = Mock(side_effect=lambda uid: item(uid))
        self.sync(uids=["1", "3", "4"], messages=[item(4), item(3)], uidnext=5, recover_uid=recover)
        self.assertCountEqual(self.rows("mailbox_messages", "imap_uid, provider_deleted"), [(1, False), (3, False), (4, False)])
        self.assertEqual([call.args[0] for call in recover.call_args_list], ["1"])

    def test_prepare_timeout_writes_nothing_and_future_bootstrap_reconstructs_inventory(self):
        with patch.object(storage, "_SELECT_CURRENT_STATE_SQL", "SELECT pg_sleep(2), %s, %s, %s, %s, %s"), \
             patch("builtins.print") as diagnostic:
            consumer = self.bounded_consumer(ImapDurableBudget(0.15))
        self.assertIsNone(consumer)
        diagnostic.assert_any_call("cuevion_mailbox_active_write custom_imap prepare_timeout")
        self.assertEqual(self.rows("mailbox_sync_state"), [])
        self.provider_response(ReadOnlyFixtureMailbox(uids=("1", "2")), consumer)
        self.provider_response(ReadOnlyFixtureMailbox(uids=("1", "2", "3", "4")), self.bounded_consumer())
        self.assertCountEqual(self.rows("mailbox_messages", "imap_uid"), [(1,), (2,), (3,), (4,)])

    def test_success_commits_all_five_shared_tables(self):
        self.assertEqual(self.sync().status, "applied")
        for table in ["mailbox_sync_state", "mailbox_sync_cursor", "mailbox_messages",
                      "mailbox_message_bodies", "mailbox_change_outbox"]:
            self.assertEqual(len(self.rows(table)), 1, table)
        state = self.state()
        self.assertEqual(state.scope.provider, MailboxProvider.CUSTOM_IMAP)
        self.assertEqual(state.bootstrap_state, BootstrapState.RECENT_READY)
        self.assertIsNotNone(self.rows("mailbox_sync_state", "last_successful_sync_at")[0][0])
        cursor = self.repositories.reader.read_cursor(state.scope, "INBOX")
        self.assertEqual((cursor.imap_uid_validity, cursor.imap_highest_uid, cursor.imap_uidnext_observed), ("456", 1, 2))
        self.assertEqual(self.rows("mailbox_change_outbox", "event_type"), [("message_added",)])

    def test_repeated_poll_is_idempotent_for_rows_bodies_and_outbox(self):
        self.sync()
        self.assertEqual(self.sync().mutation_count, 0)
        self.assertEqual(len(self.rows("mailbox_messages")), 1)
        self.assertEqual(self.rows("mailbox_messages", "row_version"), [(1,)])
        self.assertEqual(self.rows("mailbox_message_bodies", "body_version, row_version"), [(1, 1)])
        self.assertEqual(len(self.rows("mailbox_change_outbox")), 1)

    def test_duplicate_provider_input_creates_one_copy(self):
        self.sync(messages=[item(), item()])
        self.assertEqual(len(self.rows("mailbox_messages")), 1)
        self.assertEqual(len(self.rows("mailbox_change_outbox")), 1)

    def test_conflicting_duplicate_input_never_advances_cursor(self):
        with self.assertRaises(ValueError):
            self.sync(messages=[item(), item(unread=False)])
        self.assertEqual(self.rows("mailbox_sync_cursor"), [])
        self.assertEqual(self.rows("mailbox_messages"), [])

    def test_metadata_refresh_targets_existing_copy_and_emits_one_change(self):
        self.sync()
        message_id = self.rows("mailbox_messages", "message_id")[0][0]
        self.sync(messages=[item(unread=False, starred=True)])
        self.assertEqual(self.rows("mailbox_messages", "message_id, unread, starred, row_version"), [(message_id, False, True, 2)])
        self.assertCountEqual(self.rows("mailbox_change_outbox", "event_type"), [("message_added",), ("message_changed",)])
        self.assertEqual(self.rows("mailbox_message_bodies", "body_version"), [(1,)])

    def test_body_refresh_updates_body_atomically(self):
        self.sync()
        self.sync(messages=[item(raw=RAW.replace(b"Body.", b"Updated body."))])
        self.assertEqual(self.rows("mailbox_message_bodies", "body_version, row_version"), [(2, 2)])
        self.assertIn("Updated body.", self.rows("mailbox_message_bodies", "body_text")[0][0])

    def test_uidvalidity_replacement_does_not_alias_old_copy(self):
        self.sync()
        first_id = self.rows("mailbox_messages", "message_id")[0][0]
        self.sync(validity="457")
        self.assertEqual(self.state().scope.source_generation, 2)
        self.assertCountEqual(self.rows("mailbox_sync_state", "source_generation, is_current"), [(1, False), (2, True)])
        self.assertEqual(len(self.rows("mailbox_messages")), 2)
        self.assertEqual(len({row[0] for row in self.rows("mailbox_messages", "message_id")}), 2)
        self.assertNotEqual(first_id, self.repositories.reader.list_messages(self.state().scope, limit=100)[0].identity.message_id)
        self.assertEqual(self.sync(validity="457").mutation_count, 0)

    def test_empty_folder_commits_zero_cursor_and_uidnext(self):
        self.sync(uids=[], messages=[], uidnext=1)
        cursor = self.repositories.reader.read_cursor(self.state().scope, "INBOX")
        self.assertEqual((cursor.imap_highest_uid, cursor.imap_uidnext_observed), (0, 1))
        self.assertEqual(self.rows("mailbox_messages"), [])

    def test_repeated_raw_uidvalidity_still_creates_distinct_observed_epochs(self):
        self.sync()
        first_rows = self.rows("mailbox_messages")
        self.sync(validity="457")
        self.sync(validity="456")
        self.assertEqual(self.state().scope.source_generation, 3)
        self.assertEqual(len({row[0] for row in self.rows("mailbox_messages", "message_id")}), 3)
        self.assertIn(first_rows[0], self.rows("mailbox_messages"))
        self.assertCountEqual(self.rows("mailbox_sync_cursor", "source_generation, imap_uid_validity"),
                              [(1, "456"), (2, "457"), (3, "456")])

    def test_missing_uidnext_is_supported(self):
        self.sync(uidnext=None)
        self.assertIsNone(self.repositories.reader.read_cursor(self.state().scope, "INBOX").imap_uidnext_observed)

    def test_recent_bootstrap_holds_progress_until_authoritative_inventory_recovery(self):
        self.sync(uids=[str(uid) for uid in range(1, 11)], messages=[item(10)], uidnext=11)
        self.assertEqual(self.rows("mailbox_messages", "imap_uid"), [(10,)])
        cursor = self.repositories.reader.read_cursor(self.state().scope, "INBOX")
        self.assertEqual(cursor.imap_highest_uid, 0)
        self.assertEqual(cursor.backfill_state.value, "recovering")
        self.assertEqual(cursor.backfill_cursor, '{"imap_after_uid":0}')
        self.sync(uids=[str(uid) for uid in range(1, 11)], messages=[item(10)], uidnext=11,
                  recover_uid=lambda uid: item(uid))
        self.assertCountEqual(self.rows("mailbox_messages", "imap_uid"), [(uid,) for uid in range(1, 11)])

    def test_cursor_recovers_arrivals_outside_ui_window_without_gaps(self):
        self.sync()
        recover = Mock(side_effect=lambda uid: item(uid))
        self.sync(uids=["1", "2", "3", "4"], messages=[item(4)], uidnext=5, recover_uid=recover)
        self.assertEqual([call.args[0] for call in recover.call_args_list], ["2", "3"])
        self.assertEqual(self.repositories.reader.read_cursor(self.state().scope, "INBOX").imap_highest_uid, 4)
        self.assertEqual(len(self.rows("mailbox_messages")), 4)

    def test_backlog_is_bounded_and_converges_on_next_poll(self):
        self.sync()
        uids = [str(uid) for uid in range(1, 151)]
        recover = Mock(side_effect=lambda uid: item(uid))
        self.sync(uids=uids, messages=[item(150)], uidnext=151, recover_uid=recover)
        self.assertEqual(recover.call_count, 100)
        self.assertEqual(self.state().bootstrap_state, BootstrapState.RECOVERING)
        self.assertEqual(self.repositories.reader.read_cursor(self.state().scope, "INBOX").imap_highest_uid, 101)
        self.sync(uids=uids, messages=[item(150)], uidnext=151, recover_uid=recover)
        self.assertEqual(self.state().bootstrap_state, BootstrapState.RECENT_READY)
        self.assertEqual(len(self.rows("mailbox_messages")), 150)

    def test_recovery_failure_leaves_cursor_messages_bodies_and_outbox_unchanged(self):
        self.sync()
        before = {name: self.rows(name) for name in ["mailbox_sync_cursor", "mailbox_messages", "mailbox_message_bodies", "mailbox_change_outbox"]}
        result = self.sync(uids=["1", "2", "3"], messages=[item(3)], uidnext=4, recover_uid=lambda uid: None)
        self.assertEqual(result.status, "provider_retry")
        for name, rows in before.items():
            self.assertEqual(self.rows(name), rows)
        self.sync(uids=["1", "2", "3"], messages=[item(3)], uidnext=4, recover_uid=lambda uid: item(uid))
        self.assertEqual(len(self.rows("mailbox_messages")), 3)

    def test_deletion_uses_complete_folder_inventory_and_existing_tombstone_contract(self):
        self.sync(uids=["1", "2"], messages=[item(2), item(1)], uidnext=3)
        self.sync(uids=["2"], messages=[item(2)], uidnext=3)
        self.assertCountEqual(self.rows("mailbox_messages", "imap_uid, provider_deleted"), [(1, True), (2, False)])
        self.assertEqual(len(self.rows("mailbox_change_outbox")), 3)
        self.sync(uids=["2"], messages=[item(2)], uidnext=3)
        self.assertEqual(len(self.rows("mailbox_change_outbox")), 3)

    def test_same_uid_in_another_folder_isolated_from_deletion(self):
        self.sync()
        self.sync(folder="Archive")
        self.sync(uids=[], messages=[], uidnext=2)
        self.assertCountEqual(self.rows("mailbox_messages", "provider_folder, provider_deleted"), [("INBOX", True), ("Archive", False)])
        self.assertEqual(len(self.rows("mailbox_sync_cursor")), 2)

    def test_uidvalidity_rollover_cannot_retire_an_unrelated_folder(self):
        self.sync()
        self.sync(folder="Archive")
        before = {name: self.rows(name) for name in ["mailbox_sync_state", "mailbox_sync_cursor", "mailbox_messages", "mailbox_message_bodies", "mailbox_change_outbox"]}
        self.assertEqual(self.sync(validity="457").status, "conflict")
        for name, rows in before.items():
            self.assertEqual(self.rows(name), rows, name)
        self.assertCountEqual(
            [row.identity.provider_folder for row in self.repositories.reader.list_messages(self.state().scope, limit=100)],
            ["INBOX", "Archive"],
        )

    def test_recent_window_does_not_tombstone_older_present_uid(self):
        self.sync(uids=["1", "2"], messages=[item(2), item(1)], uidnext=3)
        self.sync(uids=["1", "2", "3"], messages=[item(3)], uidnext=4,
                  recover_uid=lambda uid: item(uid))
        self.assertCountEqual(self.rows("mailbox_messages", "imap_uid, provider_deleted"),
                              [(1, False), (2, False), (3, False)])

    def test_workspace_owner_mailbox_and_account_isolation(self):
        accounts = [authority(), authority(workspace_id="wsp_" + "c" * 22),
                    authority(owner_user_id="usr_" + "c" * 22), authority(mailbox_id="imap-2")]
        for account in accounts:
            self.sync(account=account)
            projections = self.repositories.reader.list_messages(self.state(account).scope, limit=100)
            self.assertEqual(len(projections), 1)
        self.assertEqual(len({row[0] for row in self.rows("mailbox_messages", "message_id")}), 4)
        self.assertIsNone(self.state(authority(provider_account_identity="other@example.test")))
        self.assertEqual(self.sync(account=authority(provider_account_identity="other@example.test")).status, "state_conflict")

    def test_stale_prepared_snapshot_conflicts_without_overwriting_new_metadata(self):
        self.sync()
        old_state = self.state()
        self.sync(messages=[item(unread=False)])
        result = self.sync(prepared_state=old_state)
        self.assertEqual(result.status, "conflict")
        self.assertEqual(self.rows("mailbox_messages", "unread"), [(False,)])

    def test_old_generation_snapshot_cannot_restore_old_uidvalidity(self):
        self.sync()
        old_state = self.state()
        self.sync(validity="457")
        result = self.sync(prepared_state=old_state)
        self.assertEqual(result.status, "stale_generation")
        self.assertEqual(self.state().scope.source_generation, 2)

    def test_sql_failure_rolls_back_bodies_outbox_cursor_and_generation_then_retry_converges(self):
        self.sync()
        before = {name: self.rows(name) for name in ["mailbox_sync_state", "mailbox_sync_cursor", "mailbox_messages", "mailbox_message_bodies", "mailbox_change_outbox"]}
        with patch.object(storage, "_UPSERT_BODY_SQL", "SELECT nonexistent_test_column, %s, %s, %s, %s, %s, %s, %s, %s"):
            with self.assertRaises(psycopg.errors.UndefinedColumn):
                self.sync(validity="457")
        for name, rows in before.items():
            self.assertEqual(self.rows(name), rows, name)
        self.sync(validity="457")
        self.assertEqual(self.state().scope.source_generation, 2)
        self.assertEqual(len(self.rows("mailbox_messages")), 2)

    def test_failed_incremental_write_recovers_after_recent_window_moves(self):
        self.sync()
        before = {name: self.rows(name) for name in ["mailbox_sync_state", "mailbox_sync_cursor", "mailbox_messages", "mailbox_message_bodies", "mailbox_change_outbox"]}
        with patch.object(storage, "_UPSERT_BODY_SQL", "SELECT nonexistent_test_column, %s, %s, %s, %s, %s, %s, %s, %s"):
            with self.assertRaises(psycopg.errors.UndefinedColumn):
                self.sync(uids=["1", "2"], messages=[item(2)], uidnext=3)
        for name, rows in before.items():
            self.assertEqual(self.rows(name), rows, name)
        recover = Mock(side_effect=lambda uid: item(uid))
        self.sync(uids=["1", "2", "3", "4"], messages=[item(4)], uidnext=5,
                  recover_uid=recover)
        self.assertEqual([call.args[0] for call in recover.call_args_list], ["2", "3"])
        self.assertCountEqual(self.rows("mailbox_messages", "imap_uid"), [(1,), (2,), (3,), (4,)])
        self.assertEqual(self.repositories.reader.read_cursor(self.state().scope, "INBOX").imap_highest_uid, 4)

    def test_failed_first_bootstrap_recovers_original_recent_window(self):
        with patch.object(storage, "_UPSERT_BODY_SQL", "SELECT nonexistent_test_column, %s, %s, %s, %s, %s, %s, %s, %s"):
            with self.assertRaises(psycopg.errors.UndefinedColumn):
                self.sync(uids=["1", "2"], messages=[item(2), item(1)], uidnext=3)
        self.assertEqual(self.rows("mailbox_sync_cursor"), [])
        self.sync(uids=["1", "2", "3", "4"], messages=[item(4), item(3)], uidnext=5,
                  recover_uid=lambda uid: item(uid))
        self.assertCountEqual(self.rows("mailbox_messages", "imap_uid"), [(1,), (2,), (3,), (4,)])

    def test_failed_generation_bootstrap_recovers_original_recent_window(self):
        self.sync()
        with patch.object(storage, "_UPSERT_BODY_SQL", "SELECT nonexistent_test_column, %s, %s, %s, %s, %s, %s, %s, %s"):
            with self.assertRaises(psycopg.errors.UndefinedColumn):
                self.sync(validity="457", uids=["1", "2"], messages=[item(2), item(1)], uidnext=3)
        self.assertEqual(self.state().scope.source_generation, 1)
        self.sync(validity="457", uids=["1", "2", "3", "4"], messages=[item(4), item(3)], uidnext=5,
                  recover_uid=lambda uid: item(uid))
        self.assertCountEqual(
            [uid for uid, generation in self.rows("mailbox_messages", "imap_uid, source_generation") if generation == 2],
            [1, 2, 3, 4],
        )

    def test_no_cursor_advance_for_invalid_uid_inventory_or_uidnext(self):
        for uids, uidnext in [(["2", "1"], 3), (["1", "1"], 2), (["01"], 2), (["1"], 1)]:
            with self.subTest(uids=uids), self.assertRaises(ValueError):
                self.sync(uids=uids, uidnext=uidnext)
        self.assertEqual(self.rows("mailbox_sync_state"), [])

    def test_cached_body_lookup_respects_scope(self):
        self.sync()
        current = self.state().scope
        message_id = self.rows("mailbox_messages", "message_id")[0][0]
        self.assertIsNotNone(self.repositories.reader.read_cached_body(current, message_id))
        self.assertIsNone(self.repositories.reader.read_cached_body(replace(current, owner_user_id="usr_" + "c" * 22), message_id))

    def test_stale_outbox_generation_is_not_consumer_authority(self):
        self.sync()
        events = self.repositories.writer.claim_outbox_batch(limit=10, now_millis=self.clock + 100, lease_millis=1000)
        self.assertEqual(len(events), 1)
        self.sync(validity="457")
        self.assertIsNone(self.repositories.reader.resolve_outbox_message(events[0]))

    def test_provider_adapter_keeps_visible_response_identical_and_only_reads_mail(self):
        import imap_connect_preview as provider
        request = dict(provider="custom_imap", mailboxId="imap-1", email="owner@example.test",
                       host="imap.example.test", port=993, ssl=True, username="owner@example.test",
                       password="fixture", limit=20)
        consumer = prepare_authenticated_imap_refresh(
            environment={"VERCEL_ENV": "preview", "CUEVION_MAILBOX_POSTGRES_MODE": "active_write"},
            member=SimpleNamespace(workspace_id=WORKSPACE, user_id=OWNER),
            mailbox={"mailboxId": "imap-1", "email": "owner@example.test"},
            repositories=self.repositories,
        )
        disabled = ReadOnlyFixtureMailbox()
        enabled = ReadOnlyFixtureMailbox()
        with patch.object(provider, "open_mailbox_connection", side_effect=[disabled, enabled]), \
             patch.object(provider, "resolve_preview_routing", return_value={}):
            old_status, old_response = provider.build_connect_preview_response(request)
            new_status, new_response = provider.build_connect_preview_response(request, durable_snapshot_consumer=consumer)
        self.assertEqual((new_status, new_response), (old_status, old_response))
        self.assertEqual(new_status, 200)
        self.assertIn(("select", "INBOX", True), enabled.calls)
        self.assertTrue(all("BODY.PEEK[]" in call[2] for call in enabled.calls if call[0] == "fetch"))
        self.assertTrue(all(call[1].lower() in {"search", "fetch"} for call in enabled.calls if call[0] == "uid"))
        self.assertEqual(len(self.rows("mailbox_messages")), 1)
        self.assertEqual(len(self.rows("mailbox_message_bodies")), 1)

    def test_database_failure_preserves_exact_provider_response_and_durable_cursor(self):
        import imap_connect_preview as provider
        self.sync()
        request = dict(provider="custom_imap", mailboxId="imap-1", email="owner@example.test",
                       host="imap.example.test", port=993, ssl=True, username="owner@example.test",
                       password="fixture", limit=1)
        consumer = prepare_authenticated_imap_refresh(
            environment={"VERCEL_ENV": "preview", "CUEVION_MAILBOX_POSTGRES_MODE": "active_write"},
            member=SimpleNamespace(workspace_id=WORKSPACE, user_id=OWNER),
            mailbox={"mailboxId": "imap-1", "email": "owner@example.test"},
            repositories=self.repositories,
        )
        before = {name: self.rows(name) for name in ["mailbox_sync_state", "mailbox_sync_cursor", "mailbox_messages", "mailbox_message_bodies", "mailbox_change_outbox"]}
        with patch.object(provider, "open_mailbox_connection", side_effect=[
            ReadOnlyFixtureMailbox(uids=("1", "2")), ReadOnlyFixtureMailbox(uids=("1", "2")),
        ]), patch.object(provider, "resolve_preview_routing", return_value={}):
            expected = provider.build_connect_preview_response(request)
            with patch.object(storage, "_UPSERT_BODY_SQL", "SELECT nonexistent_test_column, %s, %s, %s, %s, %s, %s, %s, %s"), \
                 patch("builtins.print") as diagnostic:
                actual = provider.build_connect_preview_response(request, durable_snapshot_consumer=consumer)
        self.assertEqual(actual, expected)
        self.assertEqual(actual[0], 200)
        diagnostic.assert_any_call("cuevion_mailbox_active_write custom_imap failed")
        for name, rows in before.items():
            self.assertEqual(self.rows(name), rows, name)

    def test_authenticated_refresh_route_commits_the_same_shared_core(self):
        import imap_connect_preview as provider
        route_path = Path(__file__).resolve().parents[2] / "api/inboxes/connect-imap.py"
        spec = importlib.util.spec_from_file_location("imap_core_refresh_route_test", route_path)
        route = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(route)
        resolved_mailbox = {
            "mailboxId": "imap-1", "email": "owner@example.test",
            "imap": {"host": "imap.example.test", "port": 993, "ssl": True,
                     "username": "owner@example.test", "password": "fixture"},
        }
        member = SimpleNamespace(workspace_id=WORKSPACE, user_id=OWNER)
        responses = []
        handler = SimpleNamespace(headers={}, _send_json=lambda code, payload: responses.append((code, payload)))
        prepare = lambda **kwargs: prepare_authenticated_imap_refresh(**kwargs, repositories=self.repositories)
        with patch.object(route, "resolve_authenticated_imap_mailbox", return_value={
            "status": "ok", "mailbox": resolved_mailbox, "memberAuthority": member,
        }), patch.object(route, "prepare_authenticated_imap_refresh", side_effect=prepare), \
             patch.object(provider, "open_mailbox_connection", return_value=ReadOnlyFixtureMailbox()), \
             patch.object(provider, "resolve_preview_routing", return_value={}), \
             patch.object(route, "populate_runtime_priority_candidates"), \
             patch.dict(os.environ, {"VERCEL_ENV": "preview", "CUEVION_MAILBOX_POSTGRES_MODE": "active_write"}, clear=True):
            route.handler._handle_refresh(handler, {"mode": "refresh", "mailboxId": "imap-1"})
        self.assertEqual(responses[0][0], 200)
        self.assertEqual(set(responses[0][1]), {"ok", "messages", "inboxUidSet", "uidValidity", "prioritySemanticNewInboundMode"})
        self.assertEqual(len(self.rows("mailbox_messages")), 1)
        self.assertEqual(len(self.rows("mailbox_change_outbox")), 1)

    def test_concurrent_refresh_plans_publish_one_metadata_change(self):
        self.sync()
        prepared = self.state()
        writer = self.repositories.writer
        barrier = threading.Barrier(2)

        def commit(delta):
            barrier.wait(timeout=10)
            return writer.commit_provider_delta(delta)

        self.repositories.writer = SimpleNamespace(commit_provider_delta=commit)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                lambda _: self.sync(messages=[item(unread=False)], prepared_state=prepared),
                range(2),
            ))
        self.assertCountEqual([result.status for result in results], ["applied", "conflict"])
        self.assertEqual(self.rows("mailbox_messages", "row_version, unread"), [(2, False)])
        self.assertEqual(len(self.rows("mailbox_change_outbox")), 2)

    def test_provider_snapshot_warning_and_partial_fetch_do_not_commit(self):
        consumer = prepare_authenticated_imap_refresh(
            environment={"VERCEL_ENV": "preview", "CUEVION_MAILBOX_POSTGRES_MODE": "active_write"},
            member=SimpleNamespace(workspace_id=WORKSPACE, user_id=OWNER),
            mailbox={"mailboxId": "imap-1", "email": "owner@example.test"},
            repositories=self.repositories,
        )
        provider = ReadOnlyFixtureMailbox()
        for messages, warnings in [([item()], [{"code": "quota_exceeded"}]), ([], [])]:
            consumer(mailbox=provider, folder="INBOX", uid_validity="456",
                     uid_search_response=("OK", [b"1"]), messages=messages,
                     warnings=warnings, limit=20)
        self.assertEqual(self.rows("mailbox_sync_cursor"), [])
        self.assertEqual(self.rows("mailbox_messages"), [])

    def test_empty_provider_snapshot_persists_without_changing_existing_empty_response(self):
        import imap_connect_preview as provider
        consumer = prepare_authenticated_imap_refresh(
            environment={"VERCEL_ENV": "preview", "CUEVION_MAILBOX_POSTGRES_MODE": "active_write"},
            member=SimpleNamespace(workspace_id=WORKSPACE, user_id=OWNER),
            mailbox={"mailboxId": "imap-1", "email": "owner@example.test"},
            repositories=self.repositories,
        )
        with patch.object(provider, "open_mailbox_connection", return_value=ReadOnlyFixtureMailbox(uids=())):
            status, response = provider.build_connect_preview_response(
                dict(provider="custom_imap", mailboxId="imap-1", email="owner@example.test",
                     host="imap.example.test", port=993, ssl=True, password="fixture"),
                durable_snapshot_consumer=consumer,
            )
        self.assertEqual(status, 200)
        self.assertEqual(response["messages"], [])
        self.assertNotIn("inboxUidSet", response)
        self.assertEqual(self.repositories.reader.read_cursor(self.state().scope, "INBOX").imap_highest_uid, 0)


if __name__ == "__main__":
    unittest.main()
