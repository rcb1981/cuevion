"""Production pilot safety proof: no Microsoft mail writes, least privileges.

All real Production write endpoints must deny before parsing a request or
accessing any owner, token, attempt ledger, or Microsoft Graph mutation.
"""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from api.inboxes import microsoft_production_pilot as policy
from api.inboxes import microsoft_outbound_http as outbound
from api.inboxes import microsoft_outbound_service as send_service
from api.inboxes import microsoft_http as inbox
from api.inboxes import microsoft_mailbox_access as access
from api.inboxes.test_microsoft_outbound_http import request as send_request
from api.inboxes.test_microsoft_http import (
    _request as inbox_request, GRAPH_LIST, MAILBOX, MESSAGE_ID,
)


def error_code(response):
    return json.loads(response.body.decode("utf-8"))["error"]["code"]


class MicrosoftReadOnlyProductionPilotTests(unittest.TestCase):
    def test_production_mutation_gate_is_unconditional_and_not_configurable(self):
        for options in (
            {},
            {"MICROSOFT_PRODUCTION_PILOT_WRITES_ENABLED": "1"},
            {"MICROSOFT_READ_ONLY": "false"},
            {"MICROSOFT_OAUTH_SCOPES": "https://graph.microsoft.com/Mail.Send"},
            {"CUEVION_MICROSOFT_PREVIEW_ORIGIN": "https://mail-preview.cuevion.com"},
        ):
            with self.subTest(options=options), patch.dict(
                os.environ, {"VERCEL_ENV": "production", **options}, clear=True,
            ):
                self.assertTrue(policy.production_pilot_writes_blocked())
                self.assertFalse(policy.production_pilot_delete_blocked())
                self.assertFalse(policy.production_pilot_archive_blocked())

    def test_send_denied_before_input_read_or_any_service(self):
        with patch.dict(os.environ, {"VERCEL_ENV": "production"}, clear=True):
            for req in (
                send_request(),
                send_request(raw=b"invalid-json"),
                send_request(headers=(("X-Forwarded-Host", "evil.example"),)),
            ):
                with self.subTest(headers=str(req.headers)), patch.object(
                    outbound, "_read_body",
                    side_effect=AssertionError("No Production request should be read"),
                ) as parse, patch.object(
                    send_service, "send_owned_microsoft_message",
                ) as send:
                    response = outbound.microsoft_send_response(req)
                    self.assertEqual(response.status, 403)
                    self.assertEqual(error_code(response), "microsoft_pilot_read_only")
                    self.assertIn(("Cache-Control", "no-store"), response.headers)
                    parse.assert_not_called()
                    send.assert_not_called()

    def test_production_unread_release_requires_exact_owner_route_and_origin(self):
        with patch.dict(os.environ, {"VERCEL_ENV": "production"}, clear=True), patch.object(
            access, "mark_owned_microsoft_inbox_read",
            return_value={"providerMessageId": MESSAGE_ID, "unread": True, "changed": True},
        ) as mutate:
            request = {
                "mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID,
                "unread": True,
            }
            response = inbox.microsoft_inbox_response(
                inbox_request(request, header_pairs=(("Origin", "https://app.cuevion.com"),)),
                mode="flag",
            )
            self.assertEqual(response.status, 200)
            data = json.loads(response.body)
            self.assertTrue(data["ok"])
            self.assertEqual(data["providerMessageId"], MESSAGE_ID)
            self.assertTrue(data["unread"])
            mutate.assert_called_once()
            self.assertEqual(mutate.call_args.kwargs, {"unread": True})

            mutate.reset_mock()
            denied = inbox.microsoft_inbox_response(
                inbox_request(request, header_pairs=(("Origin", "https://attacker.example"),)),
                mode="flag",
            )
            self.assertEqual(denied.status, 403)
            mutate.assert_not_called()

    def test_production_unread_flag_can_be_source_revoked_without_env_override(self):
        with patch.dict(os.environ, {
            "VERCEL_ENV": "production",
            "MICROSOFT_PRODUCTION_PILOT_WRITES_ENABLED": "1",
            "MICROSOFT_MARK_UNREAD_ENABLED": "1",
        }, clear=True), patch.object(
            policy, "_PRODUCTION_MANUAL_INBOX_MARK_UNREAD_RELEASED", False,
        ), patch.object(
            access, "mark_owned_microsoft_inbox_read",
        ) as mutate:
            self.assertTrue(policy.production_pilot_mark_unread_blocked())
            response = inbox.microsoft_inbox_response(
                inbox_request({
                    "mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID,
                    "unread": True,
                }, header_pairs=(("Origin", "https://app.cuevion.com"),)),
                mode="flag",
            )
            self.assertEqual(response.status, 403)
            self.assertEqual(error_code(response), "microsoft_pilot_read_only")
            mutate.assert_not_called()

    def test_read_flag_release_is_source_only_and_not_env_configurable(self):
        self.assertTrue(policy._PRODUCTION_READ_FLAGS_RELEASED)
        self.assertTrue(policy._PRODUCTION_MANUAL_INBOX_MARK_UNREAD_RELEASED)
        for options in (
            {},
            {"MICROSOFT_PRODUCTION_PILOT_WRITES_ENABLED": "0"},
            {"MICROSOFT_AUTO_MARK_READ_RELEASED": "false"},
            {"MICROSOFT_READ_ONLY": "true"},
            {"MICROSOFT_OAUTH_SCOPES": "Mail.ReadWrite Mail.Send"},
        ):
            with self.subTest(options=options), patch.dict(
                os.environ, {"VERCEL_ENV": "production", **options}, clear=True,
            ):
                self.assertFalse(policy.production_pilot_read_flags_blocked())
                self.assertFalse(policy.production_pilot_mark_unread_blocked())
                self.assertTrue(policy.production_pilot_writes_blocked())

    def test_future_flag_only_source_release_never_enables_mail_send(self):
        with patch.dict(os.environ, {"VERCEL_ENV": "production"}, clear=True), patch.object(
            policy, "_PRODUCTION_READ_FLAGS_RELEASED", True,
        ):
            self.assertFalse(policy.production_pilot_read_flags_blocked())
            self.assertTrue(policy.production_pilot_writes_blocked())
            with patch.object(access, "mark_owned_microsoft_inbox_read",
                              side_effect=lambda *args, unread: {
                                  "providerMessageId": MESSAGE_ID,
                                  "unread": unread, "changed": True,
                              }) as mark_read, patch.object(
                send_service, "send_owned_microsoft_message",
            ) as send:
                allowed = inbox.microsoft_inbox_response(
                    inbox_request({
                        "mailboxId": MAILBOX,
                        "providerMessageId": MESSAGE_ID,
                        "unread": False,
                    }, header_pairs=(
                        ("Origin", "https://app.cuevion.com"),
                    )), mode="flag",
                )
                self.assertEqual(allowed.status, 200)
                self.assertTrue(json.loads(allowed.body)["ok"])
                mark_read.assert_called_once()
                self.assertEqual(mark_read.call_args.kwargs, {"unread": False})
                mark_unread = inbox.microsoft_inbox_response(
                    inbox_request({
                        "mailboxId": MAILBOX,
                        "providerMessageId": MESSAGE_ID,
                        "unread": True,
                    }, header_pairs=(
                        ("Origin", "https://app.cuevion.com"),
                    )), mode="flag",
                )
                # Confirm both boolean flags use the same exact, owner-scoped
                # message route; no message send permission is enabled.
                self.assertEqual(mark_unread.status, 200)
                self.assertEqual(mark_read.call_count, 2)
                self.assertEqual(mark_read.call_args.kwargs, {"unread": True})
                denied = outbound.microsoft_send_response(send_request())
                self.assertEqual(denied.status, 403)
                self.assertEqual(error_code(denied), "microsoft_pilot_read_only")
                send.assert_not_called()

    def test_future_production_flag_release_requires_canonical_origin(self):
        payload = {
            "mailboxId": MAILBOX,
            "providerMessageId": MESSAGE_ID,
            "unread": False,
        }
        with patch.dict(os.environ, {"VERCEL_ENV": "production"}, clear=True), patch.object(
            policy, "_PRODUCTION_READ_FLAGS_RELEASED", True,
        ), patch.object(
            access, "mark_owned_microsoft_inbox_read",
        ) as mutate:
            for header_pairs, expected_status in (
                ((), 400),  # No Origin
                ((("Origin", "https://evil.example"),), 403),
                ((("Origin", "https://app.cuevion.com"), (
                    "X-Forwarded-Host", "evil.example",
                )), 403),
                ((("Origin", "https://app.cuevion.com"), (
                    "Origin", "https://app.cuevion.com",
                )), 400),  # Ambiguous Origin
            ):
                with self.subTest(headers=header_pairs):
                    response = inbox.microsoft_inbox_response(
                        inbox_request(payload, header_pairs=header_pairs),
                        mode="flag",
                    )
                    self.assertEqual(response.status, expected_status)
                    mutate.assert_not_called()

    def test_production_pilot_still_allows_owner_bound_inbox_read(self):
        with patch.dict(os.environ, {"VERCEL_ENV": "production"}, clear=True):
            with patch.object(
                access, "list_owned_microsoft_inbox", return_value=GRAPH_LIST,
            ) as read, patch.object(
                access, "mark_owned_microsoft_inbox_read",
            ) as change, patch.object(
                send_service, "send_owned_microsoft_message",
            ) as send:
                response = inbox.microsoft_inbox_response(
                    inbox_request({"mailboxId": MAILBOX, "limit": 5}), mode="list",
                )
                self.assertEqual(response.status, 200)
                self.assertTrue(json.loads(response.body.decode())["ok"])
                read.assert_called_once()
                change.assert_not_called()
                send.assert_not_called()

    def test_unsupported_methods_remain_method_not_allowed(self):
        with patch.dict(os.environ, {"VERCEL_ENV": "production"}, clear=True):
            self.assertEqual(outbound.microsoft_send_response(
                send_request(method="GET"),
            ).status, 405)
            get_request = inbox_request({"mailboxId": MAILBOX})
            get_request.command = "GET"
            self.assertEqual(inbox.microsoft_inbox_response(
                get_request, mode="flag",
            ).status, 405)

    def test_preview_and_production_boundaries_remain_separate(self):
        for value in ("preview", "development", ""):
            with self.subTest(value=value), patch.dict(
                os.environ, {"VERCEL_ENV": value}, clear=True,
            ):
                self.assertFalse(policy.production_pilot_writes_blocked())


if __name__ == "__main__":
    unittest.main()
