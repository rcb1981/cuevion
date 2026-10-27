"""Offline HTTP integration for Microsoft Drafts; no server, sockets or live account.

The real route handlers parse HTTP bytes in memory. Only external authority,
lease storage, token retrieval and Graph transport are replaced with test data.
The real origin policy, owner checks, payload validation and draft operations run.
"""
from __future__ import annotations

import copy
import importlib
import io
import json
import os
import socket
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit

from api.inboxes import microsoft_graph as graph
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes import microsoft_production_pilot as policy
from api.inboxes.test_microsoft_mailbox_access import MAILBOX_ID, OWNER, resolved

HOST = "cuevion-offline-test.vercel.app"
ORIGIN = "https://" + HOST
TOKEN = "offline-fake-token-never-valid-for-graph"
MESSAGE_ID = "offline-draft.1/"
EDIT_ROUTE = "/api/inboxes/edit-microsoft-draft"
DISCARD_ROUTE = "/api/inboxes/discard-microsoft-draft"
HTML = "<html><body>Original <b>formatted</b> text.</body></html>"
ENVIRONMENT = {
    "VERCEL_ENV": "preview",
    "VERCEL_URL": HOST,
    "CUEVION_MICROSOFT_PREVIEW_ORIGIN": ORIGIN,
    "CUEVION_APP_URL": ORIGIN,
    "MICROSOFT_OAUTH_REDIRECT_URI": ORIGIN + "/api/inboxes/oauth-callback",
}


class _MemorySocket:
    """Only the file-like interface BaseHTTPRequestHandler needs, never a socket."""

    def __init__(self, request: bytes):
        self.input = io.BytesIO(request)
        self.output = io.BytesIO()

    def makefile(self, mode, *_):
        if mode != "rb":
            raise AssertionError("Unexpected HTTP file mode")
        return self.input

    def sendall(self, data):
        self.output.write(data)


class _GraphResponse(io.BytesIO):
    def __init__(self, record, status=200):
        super().__init__(json.dumps(record).encode("utf-8"))
        self.status = status


class _DraftGraph:
    """Stateful fake Graph with conditional writes and a simulated lost ACK."""

    def __init__(self, *, rich=False):
        self.record = {
            "id": MESSAGE_ID, "parentFolderId": "drafts-folder", "isDraft": True,
            "hasAttachments": False, "bccRecipients": [],
            "subject": "Before", "toRecipients": [], "ccRecipients": [],
            "body": {"contentType": "html" if rich else "text",
                     "content": HTML if rich else "Original text"},
            "@odata.etag": 'W/"version-1"',
        }
        self.calls = []
        self.lose_write_ack = False
        self.conflict_on_patch = False
        self.discard_rejected = False
        self.deleted_read_unavailable = False

    @property
    def writes(self):
        return [request for request in self.calls if request.get_method() != "GET"]

    def __call__(self, request, *, timeout):
        self.calls.append(request)
        uri = urlsplit(request.full_url)
        if uri.scheme != "https" or uri.netloc != "graph.microsoft.com":
            raise AssertionError("Unexpected Graph host")
        if request.get_header("Authorization") != "Bearer " + TOKEN:
            raise AssertionError("Only the inert test token is accepted")
        if request.get_header("Prefer") != 'IdType="ImmutableId"':
            raise AssertionError("Immutable IDs are required")
        method = request.get_method()
        path = uri.path.removeprefix("/v1.0")
        draft_path = "/me/mailFolders/drafts/messages/" + quote(MESSAGE_ID, safe="")
        deleted_path = "/me/mailFolders/deleteditems/messages/" + quote(MESSAGE_ID, safe="")
        if method == "GET":
            if path == deleted_path and self.deleted_read_unavailable:
                raise HTTPError(request.full_url, 404, "No confirmation", {}, None)
            if path == "/me/mailFolders/drafts":
                return _GraphResponse({"id": "drafts-folder"})
            if path == "/me/mailFolders/deleteditems":
                return _GraphResponse({"id": "deleted-folder"})
            if ((path == draft_path and self.record["parentFolderId"] == "drafts-folder")
                    or (path == deleted_path and self.record["parentFolderId"] == "deleted-folder")):
                return _GraphResponse(self.record)
        elif method == "PATCH" and path == draft_path:
            if self.conflict_on_patch or request.get_header("If-match") != self.record["@odata.etag"]:
                raise HTTPError(request.full_url, 412, "Fake concurrent edit", {}, None)
            payload = json.loads(request.data)
            if not set(payload) <= {"subject", "toRecipients", "ccRecipients", "body"}:
                raise AssertionError("Unexpected mutable Graph field")
            self.record.update(payload)
            self.record["@odata.etag"] = 'W/"version-2"'
            if self.lose_write_ack:
                raise URLError("Fake lost acknowledgement after successful PATCH")
            return _GraphResponse(self.record)
        elif method == "POST" and path == draft_path + "/move":
            if self.discard_rejected:
                raise HTTPError(request.full_url, 409, "Rejected", {}, None)
            if json.loads(request.data) != {"destinationId": "deleteditems"}:
                raise AssertionError("Only Deleted Items is permitted")
            self.record["parentFolderId"] = "deleted-folder"
            if self.lose_write_ack:
                raise URLError("Fake lost acknowledgement after successful move")
            return _GraphResponse(self.record, status=201)
        raise AssertionError("Unexpected Graph operation: " + method + " " + path)


class MicrosoftDraftLocalHttpTests(unittest.TestCase):
    def setUp(self):
        from api.inboxes.test_microsoft_draft_attachment_foundation import CAS
        from api.inboxes import oauth_token_store
        self.send_cas = CAS()
        for operation in (
            patch.object(oauth_token_store, '_resolve_durable_store_config', return_value={'isolated': True}),
            patch.object(oauth_token_store, '_read_durable_record_snapshot', side_effect=self.send_cas.read),
            patch.object(oauth_token_store, '_write_google_durable_record_if_unchanged', side_effect=self.send_cas.write),
        ):
            operation.start(); self.addCleanup(operation.stop)
        self.graph = _DraftGraph()
        self.authority = resolved()
        self.resolutions = 0
        self.revoke_at_resolution = None
        self.lease_held = False
        self.lease_events = []
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, ENVIRONMENT, clear=True).start()
        # Standalone execution also cannot create sockets or perform DNS lookup.
        patch.object(socket, "socket", side_effect=AssertionError("Offline HTTP harness")).start()
        patch.object(socket, "getaddrinfo", side_effect=AssertionError("Offline HTTP harness")).start()
        patch.object(owned, "resolve_owned_managed_inbox_record", side_effect=self.resolve).start()
        patch.object(owned, "ensure_owned_microsoft_access_token", return_value={
            "provider": "microsoft", "access_token": TOKEN, "scope": "Mail.ReadWrite",
        }).start()
        patch.object(owned, "acquire_mailbox_mutation_lease", side_effect=self.acquire).start()
        patch.object(owned, "release_mailbox_mutation_lease", side_effect=self.release).start()
        patch.object(graph._GRAPH_OPENER, "open", side_effect=lambda *args, **kwargs: self.graph(*args, **kwargs)).start()

    def resolve(self, headers, mailbox_id, **options):
        self.resolutions += 1
        self.assertEqual(options, {"include_member_authority": True})
        self.assertEqual(mailbox_id, MAILBOX_ID)
        if self.resolutions == self.revoke_at_resolution:
            return {"status": "unauthorized"}
        return copy.deepcopy(self.authority)

    def acquire(self, owner, mailbox_id):
        self.assertEqual((owner, mailbox_id), (OWNER, MAILBOX_ID))
        self.lease_events.append("acquire")
        return {"status": "held"} if self.lease_held else {"status": "acquired", "token": "offline-lease"}

    def release(self, owner, mailbox_id, token):
        self.assertEqual((owner, mailbox_id, token), (OWNER, MAILBOX_ID, "offline-lease"))
        self.lease_events.append("release")
        return {"status": "released"}

    def request(self, payload, *, route=EDIT_ROUTE, host=HOST, origin=ORIGIN, raw=None):
        body = json.dumps(payload).encode("utf-8") if raw is None else raw
        headers = ["POST " + route + " HTTP/1.1", "Host: " + host,
                   "Content-Type: application/json", "Content-Length: " + str(len(body)),
                   "Connection: close"]
        if origin is not None:
            headers.append("Origin: " + origin)
        stream = _MemorySocket(("\r\n".join(headers) + "\r\n\r\n").encode("ascii") + body)
        module_name = "edit-microsoft-draft" if route == EDIT_ROUTE else "discard-microsoft-draft"
        handler = importlib.import_module("api.inboxes." + module_name).handler
        handler(stream, ("127.0.0.1", 0), None)
        response_head, response_body = stream.output.getvalue().split(b"\r\n\r\n", 1)
        self.assertIn(b"Cache-Control: no-store", response_head)
        self.assertNotIn(TOKEN.encode(), response_body)
        return int(response_head.split(b" ", 2)[1]), json.loads(response_body)

    def edit_request(self, action, **extra):
        return {"action": action, "mailboxId": MAILBOX_ID, "providerMessageId": MESSAGE_ID, **extra}

    def test_text_load_save_uses_real_http_boundary_and_conditional_graph_write(self):
        status, loaded = self.request(self.edit_request("load"))
        self.assertEqual(status, 200)
        self.assertFalse(self.graph.writes)
        edit = {**loaded["edit"], "subject": "Updated", "body": "New text",
                "to": ["artist@example.com"], "cc": ["manager@example.com"]}
        status, saved = self.request(self.edit_request("save", etag=loaded["etag"], edit=edit))
        self.assertEqual(status, 200)
        self.assertTrue(saved["changed"])
        self.assertEqual(saved["edit"], edit)
        self.assertNotEqual(saved["etag"], loaded["etag"])
        self.assertEqual([request.get_method() for request in self.graph.writes], ["PATCH"])
        self.assertEqual(self.lease_events, ["acquire", "release"])
        self.assertEqual(self.resolutions, 11)  # Send and Compose barrier authority rechecks.

    def test_html_metadata_save_keeps_exact_original_body_and_never_returns_html(self):
        self.graph = _DraftGraph(rich=True)
        status, loaded = self.request(self.edit_request("load_rich"))
        self.assertEqual(status, 200)
        self.assertNotIn(HTML, json.dumps(loaded))
        self.assertNotIn("_bodyHash", loaded)
        edit = {"subject": "Updated rich draft", "to": ["artist@example.com"], "cc": []}
        status, saved = self.request(self.edit_request("save_rich", etag=loaded["etag"], edit=edit))
        self.assertEqual(status, 200)
        self.assertEqual(saved["metadata"], edit)
        self.assertEqual(saved["bodyPreview"], "Original formatted text.")
        self.assertEqual(self.graph.record["body"], {"contentType": "html", "content": HTML})
        self.assertEqual(len(self.graph.writes), 1)
        self.assertNotIn("body", json.loads(self.graph.writes[0].data))
        self.assertEqual(self.lease_events, ["acquire", "release"])

    def test_conflict_between_preflight_and_patch_cannot_be_reported_as_saved(self):
        for rich in (False, True):
            with self.subTest(rich=rich):
                self.graph = _DraftGraph(rich=rich)
                self.graph.conflict_on_patch = True
                edit = {"subject": "Concurrent edit", "to": [], "cc": []}
                if not rich:
                    edit["body"] = "New text"
                status, response = self.request(self.edit_request(
                    "save_rich" if rich else "save", etag='W/"version-1"', edit=edit))
                self.assertEqual(status, 409)
                self.assertEqual(response["error"]["code"], "microsoft_draft_edit_conflict")
                self.assertEqual(len(self.graph.writes), 1)
                self.assertEqual(self.graph.record["subject"], "Before")

    def test_lost_patch_ack_is_unconfirmed_and_reopen_only_reads_actual_result(self):
        for rich in (False, True):
            with self.subTest(rich=rich):
                self.graph = _DraftGraph(rich=rich)
                self.graph.lose_write_ack = True
                edit = {"subject": "Saved with lost response", "to": [], "cc": []}
                if not rich:
                    edit["body"] = "Saved text"
                status, response = self.request(self.edit_request(
                    "save_rich" if rich else "save", etag='W/"version-1"', edit=edit))
                self.assertEqual(status, 503)
                self.assertEqual(response["error"]["code"], "microsoft_draft_edit_unconfirmed")
                status, reopened = self.request(self.edit_request("load_rich" if rich else "load"))
                self.assertEqual(status, 200)
                self.assertEqual(reopened["metadata" if rich else "edit"], edit)
                self.assertEqual(len(self.graph.writes), 1)

    def test_discard_moves_one_exact_draft_and_checks_deleted_items(self):
        status, response = self.request(
            {"mailboxId": MAILBOX_ID, "providerMessageId": MESSAGE_ID}, route=DISCARD_ROUTE)
        self.assertEqual(status, 200)
        self.assertTrue(response["discarded"])
        self.assertEqual(self.graph.record["parentFolderId"], "deleted-folder")
        self.assertEqual([request.get_method() for request in self.graph.calls],
                         ["GET", "GET", "GET", "POST", "GET"])
        self.assertEqual(self.lease_events, ["acquire", "release"])

    def test_discard_lost_ack_never_repeats_the_move(self):
        self.graph.lose_write_ack = True
        status, response = self.request(
            {"mailboxId": MAILBOX_ID, "providerMessageId": MESSAGE_ID}, route=DISCARD_ROUTE)
        self.assertEqual(status, 200)
        self.assertTrue(response["discarded"])
        self.assertEqual(self.graph.record["parentFolderId"], "deleted-folder")
        self.assertEqual(len(self.graph.writes), 1)
        self.assertEqual(self.lease_events, ["acquire", "release"])

    def test_discard_lost_ack_and_unreadable_destination_is_uncertain(self):
        self.graph.lose_write_ack = True
        self.graph.deleted_read_unavailable = True
        status, response = self.request(
            {"mailboxId": MAILBOX_ID, "providerMessageId": MESSAGE_ID}, route=DISCARD_ROUTE)
        self.assertEqual(status, 503)
        self.assertEqual(response["error"]["code"], "microsoft_mutation_unconfirmed")
        self.assertEqual(self.graph.record["parentFolderId"], "deleted-folder")
        self.assertEqual(len(self.graph.writes), 1)

    def test_discard_rejected_is_confirmed_not_performed(self):
        self.graph.discard_rejected = True
        status, response = self.request(
            {"mailboxId": MAILBOX_ID, "providerMessageId": MESSAGE_ID}, route=DISCARD_ROUTE)
        self.assertEqual(status, 409)
        self.assertEqual(response["error"]["code"], "microsoft_draft_discard_not_performed")
        self.assertEqual(self.graph.record["parentFolderId"], "drafts-folder")
        self.assertEqual(len(self.graph.writes), 1)

    def test_discard_authority_revoked_after_move_is_uncertain(self):
        def verify_after_move(*args):
            if self.graph.writes:
                raise owned.MicrosoftMailboxAccessError("microsoft_unauthorized")
        with patch.object(owned, "_verify_unchanged_owned_mailbox", side_effect=verify_after_move):
            status, response = self.request(
                {"mailboxId": MAILBOX_ID, "providerMessageId": MESSAGE_ID}, route=DISCARD_ROUTE)
        self.assertEqual(status, 503)
        self.assertEqual(response["error"]["code"], "microsoft_mutation_unconfirmed")
        self.assertEqual(self.graph.record["parentFolderId"], "deleted-folder")
        self.assertEqual(len(self.graph.writes), 1)

    def test_production_gates_reject_invalid_body_before_authority_or_graph(self):
        with patch.dict(os.environ, {"VERCEL_ENV": "production"}, clear=True):
            for route in (EDIT_ROUTE, DISCARD_ROUTE):
                with self.subTest(route=route):
                    status, response = self.request(None, route=route, raw=b"invalid-json")
                    self.assertEqual(status, 403)
                    self.assertEqual(response["error"]["code"], "microsoft_pilot_read_only")
        self.assertEqual(self.resolutions, 0)
        self.assertFalse(self.graph.calls)
        self.assertFalse(self.lease_events)
        self.assertFalse(policy._PRODUCTION_DRAFT_EDIT_RELEASED)
        self.assertFalse(policy._PRODUCTION_DRAFT_DISCARD_RELEASED)

    def test_real_origin_policy_rejects_hostile_headers_before_authority(self):
        for host, origin, expected in ((HOST, "https://attacker.example", 403),
                                       ("attacker.example", ORIGIN, 403),
                                       (HOST, None, 400)):
            with self.subTest(host=host, origin=origin):
                status, _ = self.request(self.edit_request("load"), host=host, origin=origin)
                self.assertEqual(status, expected)
        self.assertEqual(self.resolutions, 0)
        self.assertFalse(self.graph.calls)

    def test_live_localhost_origin_remains_disabled(self):
        with patch.dict(os.environ, {"VERCEL_ENV": "development"}, clear=True):
            status, _ = self.request(self.edit_request("load"), host="localhost:5173", origin="http://localhost:5173")
        self.assertEqual(status, 503)
        self.assertEqual(self.resolutions, 0)
        self.assertFalse(self.graph.calls)

    def test_session_revoked_before_save_prevents_patch_and_releases_lease(self):
        self.revoke_at_resolution = 3
        status, response = self.request(self.edit_request(
            "save", etag='W/"version-1"',
            edit={"subject": "Updated", "to": [], "cc": [], "body": "New text"}))
        self.assertEqual(status, 401)
        self.assertEqual(response["error"]["code"], "microsoft_unauthorized")
        self.assertFalse(self.graph.writes)
        self.assertEqual(self.lease_events, ["acquire", "release"])

    def test_held_mailbox_lease_prevents_graph_reads_and_writes(self):
        self.lease_held = True
        status, response = self.request(self.edit_request(
            "save", etag='W/"version-1"',
            edit={"subject": "Updated", "to": [], "cc": [], "body": "New text"}))
        self.assertEqual(status, 409)
        self.assertEqual(response["error"]["code"], "microsoft_mutation_in_progress")
        self.assertFalse(self.graph.calls)
        self.assertEqual(self.lease_events, ["acquire"])


if __name__ == "__main__":
    unittest.main()
