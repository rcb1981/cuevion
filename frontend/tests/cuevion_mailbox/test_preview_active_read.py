"""Tests for bounded Preview Gmail durable read authority."""

from __future__ import annotations

from pathlib import Path
import unittest

from cuevion_mailbox.preview_active_read import (
    gmail_cache_authority_enabled,
    plan_gmail_authoritative_read,
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
    def __init__(self, state, *, cursor=None, projections=()) -> None:
        self.state = state
        self.cursor = cursor
        self.projections = tuple(projections)
        self.authorities = []
        self.cursor_calls = []
        self.list_calls = []

    def resolve_current_state(self, authority):
        self.authorities.append(authority)
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

    def _production_environment(self):
        return {
            "VERCEL_ENV": "production",
            "CUEVION_MAILBOX_POSTGRES_MODE": "production_read",
            "CUEVION_MAILBOX_PRODUCTION_READ_AUTHORITY": "enabled",
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

    def _state(self, bootstrap_state=BootstrapState.READY):
        return MailboxStateSnapshot(
            scope=self._scope(),
            bootstrap_state=bootstrap_state,
            row_version=4,
        )

    def _cursor(
        self,
        history_id="9000",
        *,
        backfill_state=BackfillState.COMPLETE,
        backfill_cursor=None,
    ):
        return SyncCursor(
            scope_key="gmail-account",
            cursor_generation=1,
            provider=MailboxProvider.GOOGLE,
            gmail_history_id=history_id,
            imap_uid_validity=None,
            imap_highest_uid=None,
            imap_uidnext_observed=None,
            backfill_state=backfill_state,
            backfill_cursor=backfill_cursor,
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

    def _generic_plan(
        self,
        reader,
        *,
        history_id="9000",
        limit=2,
        environment=None,
    ):
        return plan_gmail_authoritative_read(
            environment=(
                self._production_environment()
                if environment is None
                else environment
            ),
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

    def test_production_reader_diagnostic_never_enables_cache_authority(self):
        environment = {
            "VERCEL_ENV": "production",
            "CUEVION_MAILBOX_POSTGRES_MODE": "production_read",
            "CUEVION_MAILBOX_PRODUCTION_READER_DIAGNOSTIC": "enabled",
        }
        self.assertFalse(gmail_cache_authority_enabled(environment))
        with self.assertRaises(RuntimeError):
            self._generic_plan(
                _Reader(None),
                environment=environment,
            )

    def test_production_cache_authority_requires_exact_double_gate(self):
        enabled = self._production_environment()
        self.assertTrue(gmail_cache_authority_enabled(enabled))

        reader = _Reader(
            self._state(),
            cursor=self._cursor(),
            projections=(self._projection("a"),),
        )
        plan = self._generic_plan(reader, limit=1)
        self.assertEqual(plan.status, "cache_authoritative")
        self.assertEqual(plan.provider_message_ids, ("gmail-message-a",))

        for environment in (
            {
                **enabled,
                "CUEVION_MAILBOX_PRODUCTION_READ_AUTHORITY": "true",
            },
            {
                **enabled,
                "CUEVION_MAILBOX_POSTGRES_MODE": "active_read",
            },
            {
                **enabled,
                "VERCEL_ENV": "preview",
            },
        ):
            with self.subTest(environment=environment):
                self.assertFalse(gmail_cache_authority_enabled(environment))
                with self.assertRaises(RuntimeError):
                    self._generic_plan(
                        _Reader(None),
                        environment=environment,
                    )

    def test_missing_current_scope_is_safe_cache_miss(self):
        reader = _Reader(None)
        result = self._run(reader)
        self.assertEqual(result.status, "no_scope")
        self.assertEqual(result.projected_count, 0)
        self.assertEqual(len(reader.authorities), 1)
        self.assertEqual(reader.cursor_calls, [])
        self.assertEqual(reader.list_calls, [])

    def test_recent_ready_is_never_cache_authority(self):
        reader = _Reader(
            self._state(BootstrapState.RECENT_READY),
            cursor=self._cursor(),
            projections=(self._projection("a"), self._projection("b")),
        )
        self.assertEqual(self._run(reader, limit=2).status, "not_ready")
        plan = self._plan(reader, limit=2)
        self.assertEqual(plan.status, "provider_required")
        self.assertEqual(reader.list_calls, [])

    def test_ready_requires_complete_gmail_cursor(self):
        for cursor in (
            None,
            self._cursor(backfill_state=BackfillState.NOT_STARTED),
            self._cursor(backfill_state=BackfillState.RUNNING),
        ):
            with self.subTest(cursor=cursor):
                reader = _Reader(self._state(), cursor=cursor)
                self.assertEqual(self._run(reader).status, "not_ready")
                self.assertEqual(self._plan(reader).status, "provider_required")
                self.assertEqual(reader.list_calls, [])

    def test_complete_ready_scope_performs_bounded_projection_read(self):
        projections = (self._projection("a"), self._projection("b"))
        reader = _Reader(
            self._state(),
            cursor=self._cursor(),
            projections=projections,
        )
        result = self._run(reader, limit=100)
        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.projected_count, 2)
        self.assertEqual(reader.list_calls, [(self._scope(), 100, None)])

    def test_matching_history_on_complete_ready_cache_is_authoritative(self):
        projections = (self._projection("a"), self._projection("b"))
        reader = _Reader(
            self._state(),
            cursor=self._cursor("9000"),
            projections=projections,
        )
        plan = self._plan(reader, history_id="9000", limit=2)
        self.assertEqual(plan.status, "cache_authoritative")
        self.assertEqual(
            plan.provider_message_ids,
            ("gmail-message-a", "gmail-message-b"),
        )
        self.assertEqual(plan.history_id, "9000")
        self.assertEqual(plan.source_generation, 3)

    def test_history_mismatch_requires_provider_before_listing_messages(self):
        reader = _Reader(
            self._state(),
            cursor=self._cursor("8999"),
            projections=(self._projection("a"),),
        )
        plan = self._plan(reader, history_id="9000", limit=1)
        self.assertEqual(plan.status, "provider_required")
        self.assertEqual(plan.provider_message_ids, ())
        self.assertEqual(reader.list_calls, [])

    def test_ready_complete_cache_may_authoritatively_return_short_page(self):
        reader = _Reader(
            self._state(),
            cursor=self._cursor(),
            projections=(self._projection("a"),),
        )
        plan = self._plan(reader, limit=50)
        self.assertEqual(plan.status, "cache_authoritative")
        self.assertEqual(plan.provider_message_ids, ("gmail-message-a",))

    def test_invalid_cached_projection_fails_closed(self):
        for projection in (
            self._projection("a", folder="Trash"),
            self._projection("a", deleted=True),
        ):
            with self.subTest(projection=projection):
                reader = _Reader(
                    self._state(),
                    cursor=self._cursor(),
                    projections=(projection,),
                )
                with self.assertRaises(RuntimeError):
                    self._plan(reader, limit=1)

    def test_invalid_limit_and_history_fail_before_repository_read(self):
        reader = _Reader(self._state(), cursor=self._cursor())
        with self.assertRaises(ValueError):
            self._run(reader, limit=101)
        with self.assertRaises(ValueError):
            self._plan(reader, history_id="not-digits")
        self.assertEqual(reader.authorities, [])


class PreviewActiveReadStaticTests(unittest.TestCase):
    def test_route_integration_is_gmail_only_and_uses_authoritative_planner(self):
        gmail = _GMAIL_ROUTE.read_text(encoding="utf-8")
        imap = _IMAP_ROUTE.read_text(encoding="utf-8")
        self.assertIn("plan_gmail_authoritative_read", gmail)
        self.assertIn("gmail_cache_authority_enabled", gmail)
        self.assertIn("cache_authority_confirmed", gmail)
        self.assertNotIn("plan_gmail_authoritative_read", imap)
        self.assertNotIn("gmail_cache_authority_enabled", imap)

    def test_route_requires_two_history_observations_around_cached_details(self):
        gmail = _GMAIL_ROUTE.read_text(encoding="utf-8")
        planner = gmail.index("plan_gmail_authoritative_read")
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
