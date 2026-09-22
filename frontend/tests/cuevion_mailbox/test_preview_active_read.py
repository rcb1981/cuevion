"""Tests for the first bounded Preview mailbox route read."""

from __future__ import annotations

from pathlib import Path
import unittest

from cuevion_mailbox.preview_active_read import (
    preview_active_read_enabled,
    run_preview_gmail_active_read,
)
from cuevion_mailbox.repository_contract import (
    MailboxProvider,
    MailboxScope,
)


_FRONTEND = Path(__file__).resolve().parents[2]
_GMAIL_ROUTE = _FRONTEND / "api" / "inboxes" / "fetch-gmail.py"
_IMAP_ROUTE = _FRONTEND / "api" / "inboxes" / "connect-imap.py"
_HELPER = _FRONTEND / "cuevion_mailbox" / "preview_active_read.py"


class _Reader:
    def __init__(self, scope, projections=()) -> None:
        self.scope = scope
        self.projections = tuple(projections)
        self.authorities = []
        self.list_calls = []

    def resolve_current_scope(self, authority):
        self.authorities.append(authority)
        return self.scope

    def list_messages(
        self,
        scope,
        *,
        limit,
        before_timestamp_millis=None,
    ):
        self.list_calls.append((scope, limit, before_timestamp_millis))
        return self.projections


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


class PreviewActiveReadStaticTests(unittest.TestCase):
    def test_first_route_integration_is_gmail_only(self):
        gmail = _GMAIL_ROUTE.read_text(encoding="utf-8")
        imap = _IMAP_ROUTE.read_text(encoding="utf-8")
        self.assertIn("run_preview_gmail_active_read", gmail)
        self.assertIn("preview_active_read_enabled", gmail)
        self.assertNotIn("run_preview_gmail_active_read", imap)
        self.assertNotIn("preview_active_read_enabled", imap)

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
