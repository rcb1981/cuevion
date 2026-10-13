"""No-network tests for scoped Microsoft Graph Inbox read/unread mutation."""
from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from api.inboxes import microsoft_message_flags as flags
from api.inboxes.microsoft_graph import GRAPH_ROOT, MicrosoftGraphError

TOKEN = "unit-test-access-token-1234567"
ID = "AAMkAGVm.987/"


class FakeResponse:
    def __init__(self, payload=None, *, status=200):
        self.status = status
        self.body = (
            payload if isinstance(payload, bytes)
            else json.dumps(payload if payload is not None else {}).encode("utf-8")
        )

    def __enter__(self):
        return self

    def __exit__(self, *_unused):
        return False

    def read(self, limit):
        return self.body[:limit]


class MutationGraphTransport:
    def __init__(self, *, initial_read=False, after_read=True, patch_status=200):
        self.calls = []
        self.initial_read = initial_read
        self.after_read = after_read
        self.patch_status = patch_status
        self.fail_at = None
        self.payload_at = {}

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        call = len(self.calls)
        if self.fail_at == call:
            raise URLError("private Graph connection failure")
        if call in self.payload_at:
            return FakeResponse(self.payload_at[call])
        if request.get_method() == "PATCH":
            return FakeResponse({"isRead": self.after_read, "id": ID}, status=self.patch_status)
        return FakeResponse({
            "id": ID,
            "isRead": self.initial_read if call == 1 else self.after_read,
        })


class GraphFlagsTests(unittest.TestCase):
    def test_mark_as_read_updates_once_and_confirms(self):
        transport = MutationGraphTransport(initial_read=False, after_read=True)
        calls = []
        result = flags.set_exact_microsoft_inbox_read(
            TOKEN, ID, unread=False,
            before_write=lambda: calls.append("checked"),
            opener=transport,
        )
        self.assertEqual(result, {"providerMessageId": ID, "unread": False, "changed": True})
        self.assertEqual([req.get_method() for req, _ in transport.calls], ["GET", "PATCH", "GET"])
        self.assertEqual(calls, ["checked"])
        for req, timeout in transport.calls:
            self.assertEqual(timeout, 20)
            self.assertTrue(req.full_url.startswith(GRAPH_ROOT + "/me/mailFolders/inbox/messages/AAMkAGVm.987%2F"))
            self.assertNotIn("access_token=", req.full_url)
            self.assertEqual(req.headers["Prefer"], 'IdType="ImmutableId"')
            self.assertEqual(req.headers["Authorization"], "Bearer " + TOKEN)
        patch = transport.calls[1][0]
        self.assertEqual(patch.headers["Content-type"], "application/json")
        self.assertEqual(json.loads(patch.data), {"isRead": True})

    def test_mark_unread_only_flips_one_boolean(self):
        transport = MutationGraphTransport(initial_read=True, after_read=False)
        result = flags.set_exact_microsoft_inbox_read(
            TOKEN, ID, unread=True, before_write=lambda: None, opener=transport,
        )
        self.assertEqual(result["unread"], True)
        self.assertEqual(json.loads(transport.calls[1][0].data), {"isRead": False})
        self.assertTrue(result["changed"])

    def test_already_read_is_idempotent_without_patch(self):
        transport = MutationGraphTransport(initial_read=True, after_read=True)
        checked = []
        result = flags.set_exact_microsoft_inbox_read(
            TOKEN, ID, unread=False,
            before_write=lambda: checked.append(True),
            opener=transport,
        )
        self.assertEqual(result["changed"], False)
        self.assertEqual([req.get_method() for req, _ in transport.calls], ["GET"])
        self.assertEqual(checked, [True])

    def test_prewrite_authority_loss_blocks_patch(self):
        transport = MutationGraphTransport()
        def deny():
            raise ValueError("Owner changed")
        with self.assertRaises(ValueError):
            flags.set_exact_microsoft_inbox_read(
                TOKEN, ID, unread=False, before_write=deny, opener=transport,
            )
        self.assertEqual([req.get_method() for req, _ in transport.calls], ["GET"])

    def test_malformed_message_id_never_requests_graph(self):
        for mid in ("", ".", "..", "../secret", "bad?id=1", "bad#hash", "space x", "\n", "A"*513):
            transport = MutationGraphTransport()
            with self.subTest(mid=mid), self.assertRaises(MicrosoftGraphError):
                flags.set_exact_microsoft_inbox_read(TOKEN, mid, unread=False, opener=transport)
            self.assertFalse(transport.calls)

    def test_non_boolean_flag_never_requests_graph(self):
        for desired in (0, 1, None, "false"):
            transport = MutationGraphTransport()
            with self.subTest(desired=desired), self.assertRaises(MicrosoftGraphError):
                flags.set_exact_microsoft_inbox_read(TOKEN, ID, unread=desired, opener=transport)
            self.assertFalse(transport.calls)

    def test_preflight_404_denies_patch(self):
        for status, expected in ((404, "microsoft_message_not_found"), (401, "microsoft_reconnect_required"), (429, "microsoft_graph_rate_limited")):
            transport = MutationGraphTransport()
            def response(_request, *, timeout):
                transport.calls.append((_request, timeout))
                raise HTTPError(GRAPH_ROOT, status, "secret error", {}, io.BytesIO(b"private"))
            with self.subTest(status=status), self.assertRaises(MicrosoftGraphError) as caught:
                flags.set_exact_microsoft_inbox_read(TOKEN, ID, unread=False, opener=response)
            self.assertEqual(caught.exception.code, expected)
            self.assertEqual(len(transport.calls), 1)

    def test_invalid_graph_preflight_id_or_flag_blocks_mutation(self):
        for payload in ({"id": "wrong", "isRead": False}, {"id": ID, "isRead": "false"}, {"id": ID}, []):
            transport = MutationGraphTransport()
            transport.payload_at[1] = payload
            with self.subTest(payload=payload), self.assertRaises(MicrosoftGraphError) as caught:
                flags.set_exact_microsoft_inbox_read(TOKEN, ID, unread=False, opener=transport)
            self.assertEqual(caught.exception.code, "microsoft_graph_invalid_response")
            self.assertEqual(len(transport.calls), 1)

    def test_oversized_preflight_is_rejected_without_patch(self):
        transport = MutationGraphTransport()
        transport.payload_at[1] = b"x" * (flags.MAX_FLAG_RESPONSE_BYTES + 1)
        with self.assertRaises(MicrosoftGraphError):
            flags.set_exact_microsoft_inbox_read(TOKEN, ID, unread=False, opener=transport)
        self.assertEqual(len(transport.calls), 1)

    def test_patch_lost_ack_never_retries_patch(self):
        transport = MutationGraphTransport()
        transport.fail_at = 2
        with self.assertRaises(MicrosoftGraphError) as caught:
            flags.set_exact_microsoft_inbox_read(TOKEN, ID, unread=False, opener=transport)
        self.assertEqual(caught.exception.code, "microsoft_mutation_unconfirmed")
        self.assertEqual([req.get_method() for req, _ in transport.calls], ["GET", "PATCH"])

    def test_patch_server_conflict_requires_refresh(self):
        for status, code in ((404, "microsoft_message_not_found"), (409, "microsoft_mutation_conflict"), (412, "microsoft_mutation_conflict"), (429, "microsoft_graph_rate_limited")):
            transport = MutationGraphTransport()
            def response(request, *, timeout):
                transport.calls.append((request, timeout))
                if request.get_method() == "PATCH":
                    raise HTTPError(GRAPH_ROOT, status, "not for UI", {}, io.BytesIO(b"private"))
                return FakeResponse({"id": ID, "isRead": False})
            with self.subTest(status=status), self.assertRaises(MicrosoftGraphError) as caught:
                flags.set_exact_microsoft_inbox_read(TOKEN, ID, unread=False, opener=response)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(len(transport.calls), 2)

    def test_post_write_readback_must_confirm_actual_result(self):
        transport = MutationGraphTransport(initial_read=False, after_read=False)
        with self.assertRaises(MicrosoftGraphError) as caught:
            flags.set_exact_microsoft_inbox_read(TOKEN, ID, unread=False, opener=transport)
        self.assertEqual(caught.exception.code, "microsoft_mutation_unconfirmed")
        self.assertEqual(len(transport.calls), 3)

    def test_post_write_readback_wrong_id_is_rejected(self):
        transport = MutationGraphTransport()
        transport.payload_at[3] = {"id": "not-same", "isRead": True}
        with self.assertRaises(MicrosoftGraphError):
            flags.set_exact_microsoft_inbox_read(TOKEN, ID, unread=False, opener=transport)
        self.assertEqual(len(transport.calls), 3)

    def test_patch_unexpected_status_is_unconfirmed(self):
        transport = MutationGraphTransport(patch_status=204)
        with self.assertRaises(MicrosoftGraphError) as caught:
            flags.set_exact_microsoft_inbox_read(TOKEN, ID, unread=False, opener=transport)
        self.assertEqual(caught.exception.code, "microsoft_mutation_unconfirmed")
        self.assertEqual(len(transport.calls), 2)


if __name__ == "__main__":
    unittest.main()
