"""No-network Microsoft Archive move and server policy tests."""
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import URLError

from api.inboxes import microsoft_deleted_move as deleted
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
            return Response({"id": "deleted-folder"})
        if n == 2:
            return Response({"id": MESSAGE_ID, "parentFolderId": "inbox-folder"})
        if n == 3:
            return Response({"id": MESSAGE_ID, "parentFolderId": "deleted-folder"}, status=201)
        if n == 4:
            return Response({"id": "other" if self.mismatch else MESSAGE_ID,
                             "parentFolderId": "deleted-folder"})
        raise AssertionError("unexpected network call")


class DeleteMoveTests(unittest.TestCase):
    def test_exact_graph_move_once_then_verify(self):
        t = Transport()
        seen = []
        value = deleted.move_exact_microsoft_inbox_to_deleted(
            TOKEN, MESSAGE_ID, opener=t,
            before_write=lambda: seen.append(len(t.calls)),
        )
        self.assertEqual(value, {"providerMessageId": MESSAGE_ID, "deleted": True})
        self.assertEqual(seen, [2])
        self.assertEqual([x.get_method() for x in t.calls], ["GET", "GET", "POST", "GET"])
        self.assertEqual(t.calls[2].data, b'{"destinationId":"deleteditems"}')
        self.assertTrue(all(x.get_header("Prefer") == 'IdType="ImmutableId"' for x in t.calls))
    def test_no_retry_after_uncertain_graph_outcome(self):
        for n in (3, 4):
            t = Transport(fail=n)
            with self.assertRaises(MicrosoftGraphError):
                deleted.move_exact_microsoft_inbox_to_deleted(TOKEN, MESSAGE_ID, opener=t)
            self.assertEqual(sum(c.get_method() == "POST" for c in t.calls), 1)
    def test_no_write_before_preflight_or_owner_check(self):
        for n in (1, 2):
            t = Transport(fail=n)
            with self.assertRaises(MicrosoftGraphError):
                deleted.move_exact_microsoft_inbox_to_deleted(TOKEN, MESSAGE_ID, opener=t)
            self.assertLessEqual(len(t.calls), 2)
        t = Transport()
        with self.assertRaises(RuntimeError):
            deleted.move_exact_microsoft_inbox_to_deleted(
                TOKEN, MESSAGE_ID, opener=t,
                before_write=lambda: (_ for _ in ()).throw(RuntimeError("owner")),
            )
        self.assertEqual(len(t.calls), 2)
    def test_incorrect_deleted_membership_never_confirms(self):
        t = Transport(mismatch=True)
        with self.assertRaises(MicrosoftGraphError) as error:
            deleted.move_exact_microsoft_inbox_to_deleted(TOKEN, MESSAGE_ID, opener=t)
        self.assertEqual(error.exception.code, "microsoft_mutation_unconfirmed")
        self.assertEqual(sum(c.get_method() == "POST" for c in t.calls), 1)
    def test_production_allows_exact_soft_delete_with_canonical_origin(self):
        with patch.dict(os.environ, {"VERCEL_ENV":"production"}, clear=True), patch.object(
            access, "delete_owned_microsoft_inbox_message",
            return_value={"providerMessageId": MESSAGE_ID, "deleted": True},
        ) as writer:
            response = http.microsoft_inbox_response(
                _request({"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID},
                         header_pairs=(("Origin", "https://app.cuevion.com"),)),
                mode="delete_move",
            )
            self.assertEqual(response.status, 200)
            self.assertTrue(json.loads(response.body)["deleted"])
            writer.assert_called_once()

    def test_production_rejects_invalid_origin_and_extra_destination(self):
        with patch.dict(os.environ, {"VERCEL_ENV":"production"}, clear=True), patch.object(
            access, "delete_owned_microsoft_inbox_message",
        ) as writer:
            for headers,status in (
                ((),400),
                ((("Origin","https://evil.example"),),403),
                ((("Origin","https://app.cuevion.com"),("X-Forwarded-Host","evil.example")),403),
            ):
                res = http.microsoft_inbox_response(
                    _request({"mailboxId":MAILBOX,"providerMessageId":MESSAGE_ID},
                             header_pairs=headers),
                    mode="delete_move",
                )
                self.assertEqual(res.status,status)
            for item in (
                {"mailboxId":MAILBOX,"providerMessageId":MESSAGE_ID,"destinationId":"junk"},
                {"mailboxId":MAILBOX,"providerMessageId":MESSAGE_ID,"folder":"Sent"},
                {"mailboxId":MAILBOX,"providerMessageId":"invalid message id"},
            ):
                res = http.microsoft_inbox_response(
                    _request(item,header_pairs=(("Origin","https://app.cuevion.com"),)),
                    mode="delete_move",
                )
                self.assertEqual(res.status,400)
            writer.assert_not_called()

    def test_source_revocation_does_not_use_env_override(self):
        from api.inboxes import microsoft_production_pilot as policy
        with patch.dict(os.environ, {
            "VERCEL_ENV":"production","MICROSOFT_DELETE_ENABLED":"1",
            "MICROSOFT_PRODUCTION_PILOT_WRITES_ENABLED":"1",
        },clear=True),patch.object(
            policy,"_PRODUCTION_INBOX_SOFT_DELETE_RELEASED",False,
        ),patch.object(access,"delete_owned_microsoft_inbox_message") as writer:
            self.assertTrue(policy.production_pilot_delete_blocked())
            response=http.microsoft_inbox_response(
                _request({"mailboxId":MAILBOX,"providerMessageId":MESSAGE_ID}),
                mode="delete_move",
            )
            self.assertEqual(response.status,403)
            self.assertEqual(json.loads(response.body)["error"]["code"],"microsoft_pilot_read_only")
            writer.assert_not_called()

    def test_preview_exact_selector_and_confirmed_only(self):
        with patch.dict(os.environ, {"VERCEL_ENV":"preview"}, clear=False), patch.object(
            access, "delete_owned_microsoft_inbox_message",
            return_value={"providerMessageId": MESSAGE_ID, "deleted": True},
        ) as move:
            good = http.microsoft_inbox_response(
                _request({"mailboxId":MAILBOX,"providerMessageId":MESSAGE_ID}),
                mode="delete_move",
            )
            self.assertEqual(good.status, 200)
            move.assert_called_once()
            bad = http.microsoft_inbox_response(
                _request({"mailboxId":MAILBOX,"providerMessageId":MESSAGE_ID,"folder":"Sent"}),
                mode="delete_move",
            )
            self.assertEqual(bad.status, 400)

    def test_no_retry_when_graph_move_response_has_wrong_id(self):
        t = Transport()
        def wrong_reply(req, *, timeout):
            response = t(req, timeout=timeout)
            if len(t.calls) == 3:
                return Response({"id": "wrong-id", "parentFolderId": "deleted-folder"}, status=201)
            return response
        with self.assertRaises(MicrosoftGraphError) as error:
            deleted.move_exact_microsoft_inbox_to_deleted(TOKEN, MESSAGE_ID, opener=wrong_reply)
        self.assertEqual(error.exception.code, "microsoft_mutation_unconfirmed")
        self.assertEqual(sum(x.get_method() == "POST" for x in t.calls), 1)

    def test_fail_closed_on_incorrect_confirmation_shape(self):
        with patch.dict(os.environ, {"VERCEL_ENV": "preview"}, clear=False), patch.object(
            access, "delete_owned_microsoft_inbox_message",
            return_value={"providerMessageId": MESSAGE_ID, "deleted": False},
        ):
            response = http.microsoft_inbox_response(
                _request({"mailboxId":MAILBOX,"providerMessageId":MESSAGE_ID}),
                mode="delete_move",
            )
            self.assertEqual(response.status, 503)

class DeleteMoveAuthorityTests(unittest.TestCase):
    def test_owner_bound_lease_and_postwrite_confirmation(self):
        from api.inboxes.test_microsoft_flag_authority import AUTHORITY, HEADERS
        from api.inboxes import microsoft_mailbox_access as access
        checks = []
        def graph(token, message_id, *, before_write):
            checks.append((token, message_id))
            before_write()
            return {"providerMessageId": message_id, "deleted": True}
        with patch.object(access, "resolve_owned_microsoft_mailbox", return_value=AUTHORITY) as resolve, patch.object(
            access, "_owned_token", return_value=(TOKEN, AUTHORITY),
        ) as token, patch.object(
            access, "acquire_mailbox_mutation_lease",
            return_value={"status": "acquired", "token": "l" * 43},
        ) as acquire, patch.object(
            access, "release_mailbox_mutation_lease",
            return_value={"status": "released"},
        ) as release, patch.object(
            deleted, "move_exact_microsoft_inbox_to_deleted", side_effect=graph,
        ):
            value = access.delete_owned_microsoft_inbox_message(HEADERS, AUTHORITY.mailbox_id, MESSAGE_ID)
            self.assertTrue(value["deleted"])
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
        ), patch.object(deleted, "move_exact_microsoft_inbox_to_deleted") as graph:
            with self.assertRaises(access.MicrosoftMailboxAccessError) as error:
                access.delete_owned_microsoft_inbox_message(HEADERS, AUTHORITY.mailbox_id, MESSAGE_ID)
            self.assertEqual(error.exception.code, "microsoft_mutation_in_progress")
            graph.assert_not_called()
