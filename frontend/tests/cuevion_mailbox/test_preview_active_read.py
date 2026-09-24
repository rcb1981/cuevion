"""Tests for bounded Preview mailbox read authority."""

from __future__ import annotations

from pathlib import Path
import unittest

from cuevion_mailbox.preview_active_read import (
    plan_preview_gmail_authoritative_read,
    preview_active_read_enabled,
    run_preview_gmail_active_read,
)
from cuevion_mailbox.repository_contract import (
    BackfillState,
    BodyState,
    BootstrapState,
    MailboxProvider,
    MailboxScope,
    MailboxStateSnapshot,
    MessageIdentity,
    MessageProjection,
    SyncCursor,
)


_FRONTEND = Path(__file__).resolve().parents[2]
_GMAIL_ROUTE = _FRONTEND / "api" / "inboxes" / "fetch-gmail.py"
_IMAP_ROUTE = _FRONTEND / "api" / "inboxes" / "connect-imap.py"
_HELPER = _FRONTEND / "cuevion_mailbox" / "preview_active_read.py"


class _Reader:
    def __init__(
        self,
        scope,
        projections=(),
        *,
        state=None,
        cursor=None,
    ) -> None:
        self.scope = scope
        self.projections = tuple(projections)
        self.state = state
        self.cursor = cursor
        self.authorities = []
        self.state_authorities = []
        self.cursor_calls = []
        self.list_calls = []

    def resolve_current_scope(self, authority):
        self.authorities.append(authority)
        return self.scope

    def resolve_current_state(self, authority):
        self.state_authorities.append(authority)
        return self.state

    def read_cursor(self, scope, scope_key):
        self.cursor_calls.append((scope, scope_key))
        return self.cursor

    def list_messages(
        self,
        scope,
        *,
        limit,
        before_timestamp_millis=None,
    ):
        self.list_calls.append((scope, limit, before_timestamp_millis))
        return self.projections[:limit]


class PreviewActiveReadTests(unittest.TestCase):
    def _environment(self):
        return {
            "VERCEL_ENV": "preview",
            "CUEVION_MAILBOX_POSTGRES_MODE": "active_read",
        }

    def _scope(self):
        return MailboxScope(
            workspace_id="wsp_" + ("a" * 22),
            owner_user_id="usr_" + ("b" * 22),
            mailbox_id="gmail-1",
            source_generation=3,
            provider=MailboxProvider.GOOGLE,
            provider_account_identity="verified@gmail.com",
        )

    def _state(self, bootstrap_state=BootstrapState.RECENT_READY):
        return MailboxStateSnapshot(
            scope=self._scope(),
            bootstrap_state=bootstrap_state,
            row_version=4,
        )

    def _cursor(self, history_id="9000"):
        return SyncCursor(
            scope_key="gmail-account",
            cursor_generation=1,
            provider=MailboxProvider.GOOGLE,
            gmail_history_id=history_id,
            imap_uid_validity=None,
            imap_highest_uid=None,
            imap_uidnext_observed=None,
            backfill_state=BackfillState.NOT_STARTED,
            backfill_cursor=None,
            row_version=3,
        )

    def _projection(self, suffix: str, *, folder="Inbox", deleted=False):
        return MessageProjection(
            identity=MessageIdentity(
                message_id="mbm_" + (suffix * 22),
                provider_message_id="gmail-message-" + suffix,
                provider_folder=folder,
                imap_uid_validity=None,
                imap_uid=None,
            ),
            provider_thread_id="thread-" + suffix,
            metadata_hash=suffix * 64,
            body_state=BodyState.CACHED,
            unread=True,
            starred=False,
            provider_deleted=deleted,
            row_version=1,
        )

    def _run(self, reader, *, environment=None, limit=50):
        return run_preview_gmail_active_read(
            environment=(self._environment() if environment is None else environment),
            workspace_id="wsp_" + ("a" * 22),
            owner_user_id="usr_" + ("b" * 22),
            mailbox_id="gmail-1",
            mailbox_account_identity="Verified@Gmail.com",
            limit=limit,
            reader=reader,
        )

    def _plan(self, reader, *, history_id="9000", limit=2, environment=None):
        return plan_preview_gmail_authoritative_read(
            environment=(self._environment() if environment is None else environment),
            workspace_id="wsp_" + ("a" * 22),
            owner_user_id="usr_" + ("b" * 22),
            mailbox_id="gmail-1",
            mailbox_account_identity="Verified@Gmail.com",
            provider_history_id=history_id,
            limit=limit,
            reader=reader,
        )

    def test_activation_requires_exact_preview_active_read(self):
        self.assertTrue(preview_active_read_enabled(self._environment()))
        for environment in (
            {},
            {
                "VERCEL_ENV": "preview",
                "CUEVION_MAILBOX_POSTGRES_MODE": "shadow",
            },
            {
                "VERCEL_ENV": "production",
                "CUEVION_MAILBOX_POSTGRES_MODE": "active_read",
            },
        ):
            with self.subTest(environment=environment):
                self.assertFalse(preview_active_read_enabled(environment))
                with self.assertRaises(RuntimeError):
                    self._run(_Reader(None), environment=environment)
                with self.assertRaises(RuntimeError):
                    self._plan(_Reader(None), environment=environment)

    def test_missing_current_scope_is_safe_cache_miss(self):
        reader = _Reader(None)
        result = self._run(reader)
        self.assertEqual(result.status, "no_scope")
        self.assertEqual(result.projected_count, 0)
        self.assertEqual(len(reader.authorities), 1)
        self.assertEqual(
            reader.authorities[0].provider_account_identity,
            "verified@gmail.com",
        )
        self.assertEqual(reader.list_calls, [])

    def test_current_scope_performs_one_bounded_projection_read(self):
        scope = self._scope()
        reader = _Reader(scope, projections=(object(), object()))
        result = self._run(reader, limit=100)
        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.projected_count, 2)
        self.assertEqual(reader.list_calls, [(scope, 100, None)])

    def test_invalid_limit_fails_before_repository_read(self):
        reader = _Reader(self._scope())
        with self.assertRaises(ValueError):
            self._run(reader, limit=101)
        self.assertEqual(reader.authorities, [])
        self.assertEqual(reader.list_calls, [])

    def test_matching_history_and_full_recent_window_becomes_authoritative(self):
        projections = (self._projection("a"), self._projection("b"))
        reader = _Reader(
            self._scope(),
            projections,
            state=self._state(),
            cursor=self._cursor(),
        )
        plan = self._plan(reader, limit=2)

        self.assertEqual(plan.status, "cache_authoritative")
        self.assertEqual(
            plan.provider_message_ids,
            ("gmail-message-a", "gmail-message-b"),
        )
        self.assertEqual(plan.history_id, "9000")
        self.assertEqual(plan.source_generation, 3)
        self.assertEqual(reader.cursor_calls, [(self._scope(), "gmail-account")])
        self.assertEqual(reader.list_calls, [(self._scope(), 2, None)])

    def test_history_mismatch_requires_provider_before_listing_messages(self):
        reader = _Reader(
            self._scope(),
            (self._projection("a"),),
            state=self._state(),
            cursor=self._cursor("8999"),
        )
        plan = self._plan(reader, history_id="9000", limit=1)

        self.assertEqual(plan.status, "provider_required")
        self.assertEqual(plan.provider_message_ids, ())
        self.assertEqual(reader.list_calls, [])

    def test_missing_or_unready_state_requires_provider(self):
        for state in (
            None,
            self._state(BootstrapState.NOT_STARTED),
            self._state(BootstrapState.RECENT_SYNC),
            self._state(BootstrapState.RECOVERING),
            self._state(BootstrapState.BLOCKED),
        ):
            with self.subTest(state=state):
                reader = _Reader(
                    self._scope(),
                    state=state,
                    cursor=self._cursor(),
                )
                plan = self._plan(reader, limit=1)
                self.assertEqual(plan.status, "provider_required")
                self.assertEqual(reader.list_calls, [])

    def test_short_recent_window_requires_provider_but_ready_allows_it(self):
        projection = self._projection("a")

        recent_reader = _Reader(
            self._scope(),
            (projection,),
            state=self._state(BootstrapState.RECENT_READY),
            cursor=self._cursor(),
        )
        recent_plan = self._plan(recent_reader, limit=2)
        self.assertEqual(recent_plan.status, "provider_required")

        ready_reader = _Reader(
            self._scope(),
            (projection,),
            state=self._state(BootstrapState.READY),
            cursor=self._cursor(),
        )
        ready_plan = self._plan(ready_reader, limit=2)
        self.assertEqual(ready_plan.status, "cache_authoritative")
        self.assertEqual(
            ready_plan.provider_message_ids,
            ("gmail-message-a",),
        )

    def test_invalid_cached_projection_fails_closed(self):
        for projection in (
            self._projection("a", folder="Trash"),
            self._projection("a", deleted=True),
        ):
            with self.subTest(projection=projection):
                reader = _Reader(
                    self._scope(),
                    (projection,),
                    state=self._state(BootstrapState.READY),
                    cursor=self._cursor(),
                )
                with self.assertRaises(RuntimeError):
                    self._plan(reader, limit=1)

    def test_invalid_provider_history_id_fails_before_repository_read(self):
        reader = _Reader(
            self._scope(),
            state=self._state(),
            cursor=self._cursor(),
        )
        with self.assertRaises(ValueError):
            self._plan(reader, history_id="not-digits")
        self.assertEqual(reader.state_authorities, [])


class PreviewActiveReadStaticTests(unittest.TestCase):
    def test_route_integration_is_gmail_only_and_uses_authoritative_planner(self):
        gmail = _GMAIL_ROUTE.read_text(encoding="utf-8")
        imap = _IMAP_ROUTE.read_text(encoding="utf-8")
        self.assertIn("plan_preview_gmail_authoritative_read", gmail)
        self.assertIn("preview_active_read_enabled", gmail)
        self.assertIn("cache_authority_confirmed", gmail)
        self.assertNotIn("plan_preview_gmail_authoritative_read", imap)
        self.assertNotIn("preview_active_read_enabled", imap)

    def test_route_requires_two_history_observations_around_cached_details(self):
        gmail = _GMAIL_ROUTE.read_text(encoding="utf-8")
        planner = gmail.index("plan_preview_gmail_authoritative_read")
        exact_details = gmail.index("authoritative_message_ids=")
        confirmed = gmail.index("cache_authority_confirmed")
        self.assertLess(planner, exact_details)
        self.assertLess(exact_details, confirmed)
        self.assertGreaterEqual(
            gmail.count("read_gmail_account_history("),
            4,
        )

    def test_helper_has_no_writer_or_outbox_surface(self):
        source = _HELPER.read_text(encoding="utf-8")
        for forbidden in (
            "commit_provider_delta",
            "claim_outbox_batch",
            "mark_outbox_processed",
            "mark_outbox_retry",
            "build_shadow_mailbox_repositories",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
