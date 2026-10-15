"""Regression: an owner-scoped Microsoft callback must never use Gmail preflight.

The production incident was oauth_reconnect_stale from the unconditional
_prepare_gmail_reconnect_target on a Microsoft mailbox. The provider-specific
boundary is tested structurally; existing Microsoft activation tests exercise
the generation lease, readback, owner and account identity.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

CALLBACK = Path(__file__).with_name("oauth-callback.py")


def is_compare(node, name: str, value: str) -> bool:
    return (
        isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Name)
        and node.left.id == name
        and len(node.ops) == 1
        and isinstance(node.ops[0], ast.Eq)
        and len(node.comparators) == 1
        and isinstance(node.comparators[0], ast.Constant)
        and node.comparators[0].value == value
    )


def calls(node, function_name: str) -> bool:
    return any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Name)
        and child.func.id == function_name
        for child in ast.walk(node)
    )


class MicrosoftCallbackReconnectIsolationTests(unittest.TestCase):
    def test_only_gmail_preflight_runs_after_shared_lease(self):
        tree = ast.parse(CALLBACK.read_text())
        handler = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "handler"
        )
        method = next(
            node for node in handler.body
            if isinstance(node, ast.FunctionDef) and node.name == "_handle_get"
        )
        lease_blocks = [
            node for node in ast.walk(method)
            if isinstance(node, ast.If)
            and is_compare(node.test, "mode", "reconnect")
            and any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "acquire_mailbox_mutation_lease"
                for child in ast.walk(node)
            )
        ]
        self.assertEqual(len(lease_blocks), 1)
        lease = lease_blocks[0]
        provider_guards = [
            child for child in lease.body
            if isinstance(child, ast.If)
            and is_compare(child.test, "provider", "google")
        ]
        self.assertEqual(len(provider_guards), 1)
        gmail_guard = provider_guards[0]
        self.assertTrue(calls(gmail_guard, "_prepare_gmail_reconnect_target"))
        self.assertFalse(calls(ast.Module(body=gmail_guard.orelse, type_ignores=[]),
                               "_prepare_gmail_reconnect_target"))
        # All other sibling statements must not call Gmail validation, which
        # would silently reject every Microsoft reconnect before token exchange.
        for statement in lease.body:
            if statement is not gmail_guard:
                self.assertFalse(calls(statement, "_prepare_gmail_reconnect_target"))
        self.assertTrue(any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "activate_reconnected_microsoft_mailbox"
            for child in ast.walk(method)
        ))

    def test_microsoft_failure_payload_keeps_provider_identity(self):
        source = CALLBACK.read_text()
        lease = source[
            source.index('        if mode == "reconnect":\n            lease_result = '):
            source.index('        if provider == "google":\n            google_client_id',)
        ]
        self.assertIn('provider=provider', lease)
        self.assertIn('if provider == "google":', lease)
        self.assertNotIn('provider="google"', lease.split('if provider == "google":', 1)[0])


if __name__ == "__main__":
    unittest.main()
