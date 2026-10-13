"""POST-only bounded Microsoft Inbox message read-state mutation HTTP tests."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from api.inboxes import microsoft_http
from api.inboxes import microsoft_mailbox_access as access
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes.test_microsoft_http import _request, _body, MAILBOX, MESSAGE_ID


class MicrosoftFlagHTTPTests(unittest.TestCase):
    def test_exact_read_flag_is_forwarded_to_owner_server(self):
        for unread in (True, False):
            with self.subTest(unread=unread), patch.object(
                access,
                "mark_owned_microsoft_inbox_read",
                return_value={
                    "providerMessageId": MESSAGE_ID, "unread": unread, "changed": True,
                },
            ) as action:
                response = microsoft_http.microsoft_inbox_response(
                    _request({
                        "mailboxId": MAILBOX,
                        "providerMessageId": MESSAGE_ID,
                        "unread": unread,
                    }),
                    mode="flag",
                )
                self.assertEqual(response.status, 200)
                self.assertEqual(_body(response), {
                    "ok": True,
                    "providerMessageId": MESSAGE_ID,
                    "unread": unread,
                    "changed": True,
                })
                self.assertEqual(action.call_args.args[1:], (MAILBOX, MESSAGE_ID))
                self.assertEqual(action.call_args.kwargs, {"unread": unread})
                self.assertIn(("Cache-Control", "no-store"), response.headers)

    def test_no_change_still_returns_valid_exact_proof(self):
        with patch.object(
            access, "mark_owned_microsoft_inbox_read",
            return_value={
                "providerMessageId": MESSAGE_ID, "unread": False, "changed": False,
            },
        ):
            res = microsoft_http.microsoft_inbox_response(
                _request({
                    "mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID, "unread": False,
                }), mode="flag",
            )
        self.assertEqual(res.status, 200)
        self.assertFalse(_body(res)["changed"])

    def test_non_boolean_and_extra_fields_never_reach_mutation(self):
        requests = [
            {"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID, "unread": "false"},
            {"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID, "unread": 1},
            {"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID, "unread": None},
            {"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID},
            {"mailboxId": MAILBOX, "unread": True},
            {"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID, "unread": True, "ownerEmail": "attacker@example.com"},
            {"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID, "unread": True, "graphUrl": "https://hostile.test"},
            {"mailboxId": MAILBOX, "providerMessageId": "../other", "unread": False},
        ]
        for value in requests:
            with self.subTest(value=value), patch.object(
                access, "mark_owned_microsoft_inbox_read",
            ) as action:
                res = microsoft_http.microsoft_inbox_response(
                    _request(value), mode="flag",
                )
                self.assertEqual(res.status, 400)
                action.assert_not_called()

    def test_incomplete_graph_ack_cannot_return_success(self):
        for invalid in (
            {"providerMessageId": "different", "unread": True, "changed": True},
            {"providerMessageId": MESSAGE_ID, "unread": False, "changed": True},
            {"providerMessageId": MESSAGE_ID, "unread": True, "changed": "true"},
            {"providerMessageId": MESSAGE_ID, "unread": True},
            None,
        ):
            with self.subTest(value=invalid), patch.object(
                access, "mark_owned_microsoft_inbox_read", return_value=invalid,
            ):
                res = microsoft_http.microsoft_inbox_response(
                    _request({"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID, "unread": True}),
                    mode="flag",
                )
                self.assertEqual(res.status, 503)
                self.assertEqual(_body(res)["error"]["code"], "microsoft_mutation_unconfirmed")

    def test_permission_and_lease_denials_do_not_claim_success(self):
        for error, status in (
            ("microsoft_write_permission_required", 403),
            ("microsoft_mutation_in_progress", 409),
            ("microsoft_mutation_unconfirmed", 503),
            ("microsoft_unauthorized", 401),
            ("microsoft_reconnect_required", 409),
        ):
            with self.subTest(error=error), patch.object(
                access, "mark_owned_microsoft_inbox_read",
                side_effect=access.MicrosoftMailboxAccessError(error),
            ):
                res = microsoft_http.microsoft_inbox_response(
                    _request({"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID, "unread": False}),
                    mode="flag",
                )
                self.assertEqual(res.status, status)
                self.assertFalse(_body(res)["ok"])

    def test_graph_404_conflict_and_unconfirmed_are_classified(self):
        for error, status in (
            ("microsoft_message_not_found", 404),
            ("microsoft_mutation_conflict", 409),
            ("microsoft_graph_rate_limited", 429),
            ("microsoft_mutation_unconfirmed", 503),
        ):
            with self.subTest(error=error), patch.object(
                access, "mark_owned_microsoft_inbox_read",
                side_effect=MicrosoftGraphError(error),
            ):
                res = microsoft_http.microsoft_inbox_response(
                    _request({"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID, "unread": True}),
                    mode="flag",
                )
                self.assertEqual(res.status, status)

    def test_other_methods_do_not_change_mail(self):
        for method in ("GET", "HEAD", "PUT", "DELETE", "PATCH", "OPTIONS"):
            with self.subTest(method=method), patch.object(
                access, "mark_owned_microsoft_inbox_read",
            ) as action:
                res = microsoft_http.microsoft_inbox_response(
                    _request(command=method), mode="flag",
                )
                self.assertEqual(res.status, 405)
                action.assert_not_called()

    def test_route_module_is_bound_to_exact_flag_operation(self):
        path = Path(__file__).with_name("mark-microsoft-read.py")
        spec = importlib.util.spec_from_file_location("cuevion_test_mark_microsoft_read", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertIs(module.handler.do_GET, module.handler.do_POST)
        with patch.object(
            module, "microsoft_inbox_response", return_value=object(),
        ) as dispatch, patch.object(module, "send_public_response"):
            req = module.handler.__new__(module.handler)
            req._respond()
            self.assertEqual(dispatch.call_args.kwargs, {"mode": "flag"})


if __name__ == "__main__":
    unittest.main()
