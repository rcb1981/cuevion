"""No-network Microsoft Archive move and server policy tests."""
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import URLError

from api.inboxes import microsoft_archive_move as archive
from api.inboxes import microsoft_http as http
from api.inboxes import microsoft_mailbox_access as access
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes.test_microsoft_http import _request, MAILBOX, MESSAGE_ID

TOKEN = "unit-test-access-token-1234567"


class Response:
    def __init__(self, payload, status=200):
        self.data = json.dumps(payload).encode()
        self.status = status
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self, count):
        return self.data[:count]


class Transport:
    def __init__(self, *, fail=None, mismatch=False):
        self.calls = []
        self.fail = fail
        self.mismatch = mismatch
    def __call__(self, req, *, timeout):
        self.calls.append(req)
        n = len(self.calls)
        if n == self.fail:
            raise URLError("unavailable")
        if n == 1:
            return Response({"id": "archive-folder"})
        if n == 2:
            return Response({"id": MESSAGE_ID, "parentFolderId": "inbox-folder"})
        if n == 3:
            return Response({"id": MESSAGE_ID, "parentFolderId": "archive-folder"}, status=201)
        if n == 4:
            return Response({"id": "other" if self.mismatch else MESSAGE_ID,
                             "parentFolderId": "archive-folder"})
        raise AssertionError("unexpected network call")


class ArchiveTests(unittest.TestCase):
    def test_exact_graph_move_once_then_verify(self):
        t = Transport()
        seen = []
        value = archive.move_exact_microsoft_inbox_to_archive(
            TOKEN, MESSAGE_ID, opener=t,
            before_write=lambda: seen.append(len(t.calls)),
        )
        self.assertEqual(value, {"providerMessageId": MESSAGE_ID, "archived": True})
        self.assertEqual(seen, [2])
        self.assertEqual([x.get_method() for x in t.calls], ["GET", "GET", "POST", "GET"])
        self.assertEqual(t.calls[2].data, b'{"destinationId":"archive"}')
        self.assertTrue(all(x.get_header("Prefer") == 'IdType="ImmutableId"' for x in t.calls))
    def test_no_retry_after_uncertain_graph_outcome(self):
        for n in (3, 4):
            t = Transport(fail=n)
            with self.assertRaises(MicrosoftGraphError):
                archive.move_exact_microsoft_inbox_to_archive(TOKEN, MESSAGE_ID, opener=t)
            self.assertEqual(sum(c.get_method() == "POST" for c in t.calls), 1)
    def test_no_write_before_preflight_or_owner_check(self):
        for n in (1, 2):
            t = Transport(fail=n)
            with self.assertRaises(MicrosoftGraphError):
                archive.move_exact_microsoft_inbox_to_archive(TOKEN, MESSAGE_ID, opener=t)
            self.assertLessEqual(len(t.calls), 2)
        t = Transport()
        with self.assertRaises(RuntimeError):
            archive.move_exact_microsoft_inbox_to_archive(
                TOKEN, MESSAGE_ID, opener=t,
                before_write=lambda: (_ for _ in ()).throw(RuntimeError("owner")),
            )
        self.assertEqual(len(t.calls), 2)
    def test_incorrect_archive_membership_never_confirms(self):
        t = Transport(mismatch=True)
        with self.assertRaises(MicrosoftGraphError) as error:
            archive.move_exact_microsoft_inbox_to_archive(TOKEN, MESSAGE_ID, opener=t)
        self.assertEqual(error.exception.code, "microsoft_mutation_unconfirmed")
        self.assertEqual(sum(c.get_method() == "POST" for c in t.calls), 1)
    def test_production_allows_confirmed_exact_archive_with_canonical_origin(self):
        with patch.dict(os.environ, {"VERCEL_ENV": "production"}, clear=True), patch.object(
            access, "archive_owned_microsoft_inbox_message",
            return_value={"providerMessageId": MESSAGE_ID, "archived": True},
        ) as move:
            response = http.microsoft_inbox_response(
                _request(
                    {"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID},
                    header_pairs=(("Origin", "https://app.cuevion.com"),),
                ),
                mode="archive_move",
            )
            self.assertEqual(response.status, 200)
            self.assertTrue(json.loads(response.body)["archived"])
            move.assert_called_once()

    def test_production_archive_rejects_foreign_origins_and_extra_payload(self):
        with patch.dict(os.environ, {"VERCEL_ENV": "production"}, clear=True), patch.object(
            access, "archive_owned_microsoft_inbox_message",
        ) as move:
            for origin, status in (
                ("https://evil.example", 403),
                (None, 400),
            ):
                headers = (("Origin", origin),) if origin is not None else ()
                response = http.microsoft_inbox_response(
                    _request(
                        {"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID},
                        header_pairs=headers,
                    ),
                    mode="archive_move",
                )
                self.assertEqual(response.status, status)
            for illegal in (
                {"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID, "destinationId": "junk"},
                {"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID, "folder": "Sent"},
                {"mailboxId": MAILBOX, "providerMessageId": "bad id"},
            ):
                response = http.microsoft_inbox_response(
                    _request(illegal, header_pairs=(("Origin", "https://app.cuevion.com"),)),
                    mode="archive_move",
                )
                self.assertEqual(response.status, 400)
            move.assert_not_called()

    def test_production_archive_source_revocation_cannot_be_overridden_by_env(self):
        from api.inboxes import microsoft_production_pilot as policy
        with patch.dict(os.environ, {
            "VERCEL_ENV": "production",
            "MICROSOFT_ARCHIVE_ENABLED": "1",
            "MICROSOFT_PRODUCTION_PILOT_WRITES_ENABLED": "1",
        }, clear=True), patch.object(
            policy, "_PRODUCTION_INBOX_ARCHIVE_RELEASED", False,
        ), patch.object(
            access, "archive_owned_microsoft_inbox_message",
        ) as move:
            self.assertTrue(policy.production_pilot_archive_blocked())
            response = http.microsoft_inbox_response(
                _request({"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID}),
                mode="archive_move",
            )
            self.assertEqual(response.status, 403)
            self.assertEqual(json.loads(response.body)["error"]["code"], "microsoft_pilot_read_only")
            move.assert_not_called()

    def test_preview_exact_selector_and_confirmed_only(self):
        with patch.dict(os.environ, {"VERCEL_ENV":"preview"}, clear=False), patch.object(
            access, "archive_owned_microsoft_inbox_message",
            return_value={"providerMessageId": MESSAGE_ID, "archived": True},
        ) as move:
            good = http.microsoft_inbox_response(
                _request({"mailboxId":MAILBOX,"providerMessageId":MESSAGE_ID}),
                mode="archive_move",
            )
            self.assertEqual(good.status, 200)
            move.assert_called_once()
            bad = http.microsoft_inbox_response(
                _request({"mailboxId":MAILBOX,"providerMessageId":MESSAGE_ID,"folder":"Sent"}),
                mode="archive_move",
            )
            self.assertEqual(bad.status, 400)

    def test_no_retry_when_graph_move_response_has_wrong_id(self):
        t = Transport()
        def wrong_reply(req, *, timeout):
            response = t(req, timeout=timeout)
            if len(t.calls) == 3:
                return Response({"id": "wrong-id", "parentFolderId": "archive-folder"}, status=201)
            return response
        with self.assertRaises(MicrosoftGraphError) as error:
            archive.move_exact_microsoft_inbox_to_archive(TOKEN, MESSAGE_ID, opener=wrong_reply)
        self.assertEqual(error.exception.code, "microsoft_mutation_unconfirmed")
        self.assertEqual(sum(x.get_method() == "POST" for x in t.calls), 1)

    def test_fail_closed_on_incorrect_confirmation_shape(self):
        with patch.dict(os.environ, {"VERCEL_ENV": "preview"}, clear=False), patch.object(
            access, "archive_owned_microsoft_inbox_message",
            return_value={"providerMessageId": MESSAGE_ID, "archived": False},
        ):
            response = http.microsoft_inbox_response(
                _request({"mailboxId":MAILBOX,"providerMessageId":MESSAGE_ID}),
                mode="archive_move",
            )
            self.assertEqual(response.status, 503)

class ArchiveAuthorityTests(unittest.TestCase):
    def test_owner_bound_lease_and_postwrite_confirmation(self):
        from api.inboxes.test_microsoft_flag_authority import AUTHORITY, HEADERS
        from api.inboxes import microsoft_mailbox_access as access
        checks = []
        def graph(token, message_id, *, before_write):
            checks.append((token, message_id))
            before_write()
            return {"providerMessageId": message_id, "archived": True}
        with patch.object(access, "resolve_owned_microsoft_mailbox", return_value=AUTHORITY) as resolve, patch.object(
            access, "_owned_token", return_value=(TOKEN, AUTHORITY),
        ) as token, patch.object(
            access, "acquire_mailbox_mutation_lease",
            return_value={"status": "acquired", "token": "l" * 43},
        ) as acquire, patch.object(
            access, "release_mailbox_mutation_lease",
            return_value={"status": "released"},
        ) as release, patch.object(
            archive, "move_exact_microsoft_inbox_to_archive", side_effect=graph,
        ):
            value = access.archive_owned_microsoft_inbox_message(HEADERS, AUTHORITY.mailbox_id, MESSAGE_ID)
            self.assertTrue(value["archived"])
            self.assertEqual(checks, [(TOKEN, MESSAGE_ID)])
            self.assertGreaterEqual(resolve.call_count, 3)
            token.assert_called_once()
            acquire.assert_called_once_with(AUTHORITY.owner_email, AUTHORITY.mailbox_id)
            release.assert_called_once_with(AUTHORITY.owner_email, AUTHORITY.mailbox_id, "l" * 43)

    def test_held_lease_prevents_graph_request(self):
        from api.inboxes.test_microsoft_flag_authority import AUTHORITY, HEADERS
        from api.inboxes import microsoft_mailbox_access as access
        with patch.object(access, "resolve_owned_microsoft_mailbox", return_value=AUTHORITY), patch.object(
            access, "acquire_mailbox_mutation_lease", return_value={"status": "held"},
        ), patch.object(archive, "move_exact_microsoft_inbox_to_archive") as graph:
            with self.assertRaises(access.MicrosoftMailboxAccessError) as error:
                access.archive_owned_microsoft_inbox_message(HEADERS, AUTHORITY.mailbox_id, MESSAGE_ID)
            self.assertEqual(error.exception.code, "microsoft_mutation_in_progress")
            graph.assert_not_called()
