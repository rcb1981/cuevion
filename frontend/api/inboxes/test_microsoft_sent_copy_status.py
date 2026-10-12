"""No-network tests for immutable-ID Sent-copy proof and owner status checks."""
from __future__ import annotations

import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from api.inboxes import microsoft_sent_copy_proof as graph
from api.inboxes import microsoft_sent_status_service as service
from api.inboxes import microsoft_outbound_ledger as ledger
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes.microsoft_graph import MicrosoftGraphError, GRAPH_ROOT
from api.inboxes.microsoft_token_authority import MicrosoftMailboxAuthority

TOKEN = "valid-test-microsoft-access-token-123"
DRAFT = "AAMkAGVm.987/"
ATTEMPT = "x" * 43
MAILBOX = "microsoft-0123456789abcdefabcd12345678"
OWNER = MicrosoftMailboxAuthority(
    "owner@example.com", MAILBOX, "mail@example.com", "graph-account", "g" * 43,
)
HEADERS = (("Cookie", "testing"),)


class Reply:
    def __init__(self, payload, status=200):
        self.status = status
        self.body = json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload

    def __enter__(self):
        return self

    def __exit__(self, *_unused):
        return False

    def read(self, count):
        return self.body[:count]


class GraphOpener:
    def __init__(self, *, found=True, parent="folder-sent", is_draft=False):
        self.found = found
        self.parent = parent
        self.is_draft = is_draft
        self.calls = []
        self.override_message = None
        self.override_folder = None

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        if request.full_url.startswith(GRAPH_ROOT + graph._SENT_FOLDER + "/messages/"):
            if not self.found:
                raise HTTPError(request.full_url, 404, "pending", {}, io.BytesIO(b"not yet"))
            payload = self.override_message if self.override_message is not None else {
                "id": DRAFT, "isDraft": self.is_draft,
                "sentDateTime": "2026-10-12T09:00:00Z",
                "parentFolderId": self.parent,
            }
            return Reply(payload)
        if request.full_url == GRAPH_ROOT + graph._SENT_FOLDER + "?$select=id":
            return Reply(
                self.override_folder if self.override_folder is not None else {"id":"folder-sent"}
            )
        raise AssertionError("Unexpected URL")


class SentCopyProofTests(unittest.TestCase):
    def test_exact_sent_copy_requires_immutable_id_and_real_sent_folder(self):
        opener = GraphOpener()
        self.assertTrue(graph.verify_microsoft_sent_copy(TOKEN, DRAFT, opener=opener))
        self.assertEqual(len(opener.calls), 2)
        for request, timeout in opener.calls:
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(timeout, 20)
            self.assertEqual(request.headers["Prefer"], 'IdType="ImmutableId"')
            self.assertEqual(request.headers["Authorization"], "Bearer " + TOKEN)
            self.assertTrue(request.full_url.startswith(GRAPH_ROOT))
            self.assertNotIn("access_token=", request.full_url)
        self.assertIn("/sentitems/messages/AAMkAGVm.987%2F", opener.calls[0][0].full_url)
        self.assertIn("sentDateTime,parentFolderId", opener.calls[0][0].full_url)

    def test_sent_copy_missing_returns_pending_not_failed(self):
        opener = GraphOpener(found=False)
        self.assertFalse(graph.verify_microsoft_sent_copy(TOKEN, DRAFT, opener=opener))
        self.assertEqual(len(opener.calls), 1)

    def test_draft_is_not_sent(self):
        opener = GraphOpener(is_draft=True)
        with self.assertRaises(MicrosoftGraphError) as caught:
            graph.verify_microsoft_sent_copy(TOKEN, DRAFT, opener=opener)
        self.assertEqual(caught.exception.code, "microsoft_sent_copy_unavailable")
        self.assertEqual(len(opener.calls), 1)

    def test_exact_id_or_metadata_mismatch_fails_closed(self):
        for changes in (
            {"id": "different"},
            {"sentDateTime": None},
            {"sentDateTime": "bad-date"},
            {"parentFolderId": None},
            {"isDraft": "false"},
        ):
            opener = GraphOpener()
            opener.override_message = {
                "id": DRAFT, "isDraft": False,
                "sentDateTime": "2026-10-12T09:00:00Z",
                "parentFolderId": "folder-sent",
                **changes,
            }
            with self.subTest(changes=changes), self.assertRaises(MicrosoftGraphError):
                graph.verify_microsoft_sent_copy(TOKEN, DRAFT, opener=opener)

    def test_folder_mismatch_is_not_sent_copy_proof(self):
        opener = GraphOpener(parent="other-folder")
        with self.assertRaises(MicrosoftGraphError):
            graph.verify_microsoft_sent_copy(TOKEN, DRAFT, opener=opener)
        self.assertEqual(len(opener.calls), 2)

    def test_invalid_folder_metadata_fails_closed(self):
        for value in ({}, {"id": ""}, {"id": None}):
            opener = GraphOpener()
            opener.override_folder = value
            with self.subTest(value=value), self.assertRaises(MicrosoftGraphError):
                graph.verify_microsoft_sent_copy(TOKEN, DRAFT, opener=opener)

    def test_invalid_id_no_network(self):
        for invalid in ("", ".", "..", "../outside", "bad?q", "\n", "x"*513):
            opener = GraphOpener()
            with self.subTest(id=invalid), self.assertRaises(MicrosoftGraphError):
                graph.verify_microsoft_sent_copy(TOKEN, invalid, opener=opener)
            self.assertFalse(opener.calls)

    def test_graph_unauthorized_or_rate_limited_is_not_pending(self):
        for status, expected in ((401,"microsoft_reconnect_required"),(403,"microsoft_reconnect_required"),(429,"microsoft_graph_rate_limited")):
            calls = []
            def opener(request, *, timeout):
                calls.append(request)
                raise HTTPError(request.full_url, status, "private", {}, io.BytesIO(b"secret"))
            with self.subTest(status=status), self.assertRaises(MicrosoftGraphError) as caught:
                graph.verify_microsoft_sent_copy(TOKEN, DRAFT, opener=opener)
            self.assertEqual(caught.exception.code, expected)
            self.assertNotIn("secret", str(caught.exception))
            self.assertEqual(len(calls),1)

    def test_graph_network_or_oversized_body_not_confirmed(self):
        def broken(_request, *, timeout):
            raise URLError("secret")
        with self.assertRaises(MicrosoftGraphError) as caught:
            graph.verify_microsoft_sent_copy(TOKEN, DRAFT, opener=broken)
        self.assertEqual(caught.exception.code, "microsoft_sent_copy_unavailable")
        opener = GraphOpener()
        opener.override_message = b"x" * (graph.MAX_PROOF_RESPONSE_BYTES + 1)
        with self.assertRaises(MicrosoftGraphError):
            graph.verify_microsoft_sent_copy(TOKEN, DRAFT, opener=opener)


class OwnedSentStatusTests(unittest.TestCase):
    def setUp(self):
        self.phase = "accepted_by_microsoft"
        self.found = False
        self.owner = OWNER
        self.checks = []
        self.calls = []
        patches = [
            patch.object(owned,"resolve_owned_microsoft_mailbox",side_effect=self.resolve),
            patch.object(owned,"_owned_token",side_effect=self.token),
            patch.object(owned,"_verify_unchanged_owned_mailbox",side_effect=self.verify),
            patch.object(ledger,"inspect_outbound_attempt",side_effect=self.inspect),
            patch.object(graph,"verify_microsoft_sent_copy",side_effect=self.probe),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def resolve(self, headers, mailbox):
        self.calls.append("resolve")
        if self.owner is None:
            raise owned.MicrosoftMailboxAccessError("microsoft_unauthorized")
        return self.owner

    def token(self, headers, mailbox):
        self.calls.append("token")
        return TOKEN, OWNER

    def verify(self, headers, mailbox, authority):
        self.calls.append("recheck")
        if authority != self.owner:
            raise owned.MicrosoftMailboxAccessError("microsoft_reconnect_required")

    def inspect(self, authority, attempt):
        self.calls.append("ledger")
        if self.phase == "missing":
            return None
        return {"phase":self.phase,"draftId":None if self.phase=="reserved" else DRAFT}

    def probe(self, token, message_id):
        self.calls.append("graph")
        self.assertEqual(token,TOKEN)
        self.assertEqual(message_id,DRAFT)
        return self.found

    def check(self):
        return service.check_owned_microsoft_sent_status(HEADERS, MAILBOX, attempt_id=ATTEMPT)

    def test_accepted_with_sent_copy_is_confirmed_not_delivered(self):
        self.found = True
        self.assertEqual(self.check(),{
            "attemptId": ATTEMPT, "status":"sent_copy_confirmed",
            "sentCopyConfirmed":True, "deliveryConfirmed":False,
        })
        self.assertEqual(self.calls, ["resolve","ledger","token","graph","recheck"])

    def test_accepted_without_visible_copy_remains_pending(self):
        self.assertEqual(self.check()["status"],"sent_copy_pending")
        self.assertEqual(self.calls.count("graph"),1)

    def test_started_without_202_may_still_have_sent_copy(self):
        self.phase="send_started"
        self.found=True
        self.assertTrue(self.check()["sentCopyConfirmed"])

    def test_unstarted_never_calls_graph(self):
        for state in ("reserved","draft_prepared"):
            self.calls.clear()
            self.phase=state
            self.assertEqual(self.check()["status"],"send_not_started")
            self.assertNotIn("graph",self.calls)
            self.assertNotIn("token",self.calls)

    def test_missing_attempt_fails_without_token_access(self):
        self.phase="missing"
        with self.assertRaises(service.MicrosoftSentStatusError) as caught:
            self.check()
        self.assertEqual(caught.exception.code,"microsoft_send_attempt_not_found")
        self.assertNotIn("token",self.calls)

    def test_revoked_owner_never_reads_ledger_or_graph(self):
        self.owner=None
        with self.assertRaises(owned.MicrosoftMailboxAccessError):
            self.check()
        self.assertEqual(self.calls,["resolve"])

    def test_owner_changes_during_probe_blocks_status(self):
        def revoke(_token, _mid):
            self.owner=None
            return True
        with patch.object(graph,"verify_microsoft_sent_copy",side_effect=revoke):
            with self.assertRaises(owned.MicrosoftMailboxAccessError):
                self.check()
        self.assertEqual(self.calls[-1],"recheck")

    def test_invalid_attempt_id_prevents_owner_and_ledger_lookup(self):
        with self.assertRaises(service.MicrosoftSentStatusError):
            service.check_owned_microsoft_sent_status(HEADERS, MAILBOX, attempt_id="../bad")
        self.assertFalse(self.calls)


if __name__ == "__main__":
    unittest.main()
