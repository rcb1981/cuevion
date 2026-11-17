"""Synthetic-only Sent contracts; no credentials or real provider traffic."""
import importlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_gmail_snapshot import gmail_context, gmail_detail, FakeResponse, snapshot_request
from test_fetch_archive import FakeHandler
from api.inboxes import gmail_snapshot
sent = importlib.import_module("api.inboxes.fetch-gmail-sent")


def detail(mid="external", labels=None):
    return {**gmail_detail(mid), "labelIds": ["SENT"] if labels is None else labels,
            "threadId": "shared-thread", "internalDate": "1700000000000"}


class SentReaderTests(unittest.TestCase):
    def read(self, refs, details, token=None):
        paths = []
        result = gmail_snapshot.read_gmail_folder_snapshot(
            gmail_context(), provider_folder="Sent", page_token=token, strict=True,
            request_with_one_refresh=snapshot_request([(refs, None), *details], paths))
        return result, paths

    def test_external_local_self_and_thread_messages_are_distinct(self):
        result, paths = self.read({"messages": [{"id": x} for x in ["external", "cuevion", "self"]], "nextPageToken": "opaque+/="},
                                 [(detail("external"), None), (detail("cuevion"), None), (detail("self", ["SENT", "INBOX", "UNREAD"]), None)])
        self.assertEqual(result["status"], "ok")
        rows = result["snapshot"]["messages"]
        self.assertEqual([m["providerMessageId"] for m in rows], ["external", "cuevion", "self"])
        self.assertTrue(rows[2]["unread"])
        self.assertEqual(rows[0]["createdAt"], "2023-11-14T22:13:20+00:00")
        self.assertEqual(result["_priorityCandidateSources"], [])
        self.assertEqual(parse_qs(urlsplit(paths[0]).query), {"labelIds": ["SENT"], "maxResults": ["50"]})
        self.assertTrue(all(p.endswith("?format=raw") for p in paths[1:]))

    def test_sent_does_not_classify_or_build_priority_sources(self):
        import imap_connect_preview
        with patch.object(imap_connect_preview, "to_message_preview", side_effect=AssertionError("classification")), \
             patch.object(imap_connect_preview, "build_priority_candidate_render_source", side_effect=AssertionError("priority")):
            result, _ = self.read({"messages": [{"id": "external"}]}, [(detail(), None)])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["_priorityCandidateSources"], [])

    def test_pagination_token_roundtrip_and_invalid_response(self):
        token = "opaque+/=&other=anything"
        result, paths = self.read({"messages": []}, [], token)
        self.assertEqual(parse_qs(urlsplit(paths[0]).query)["pageToken"], [token])
        self.assertIsNone(result["snapshot"]["nextPageToken"])
        for next_token in ["", 42, "bad\n", "x" * 4097, token]:
            result, _ = self.read({"nextPageToken": next_token}, [], token)
            self.assertEqual(result["status"], "error")

    def test_removed_deleted_and_wrong_folder_messages_not_displayed(self):
        details = [(detail(str(i), labels), None) for i, labels in enumerate([
            ["INBOX"], ["SENT", "TRASH"], ["SENT", "SPAM"], ["SENT", "DRAFT"]])]
        details += [(None, {"code": "gmail_message_not_found"})]
        result, _ = self.read({"messages": [{"id": str(i)} for i in range(5)], "nextPageToken": "next"}, details)
        self.assertEqual(result["snapshot"]["messages"], [])
        self.assertEqual(result["snapshot"]["nextPageToken"], "next")

    def test_malformed_identity_labels_mime_and_duplicate_refs_fail_closed(self):
        for bad in [{**detail(), "id": "wrong"}, {**detail(), "threadId": None},
                    {**detail(), "labelIds": ["SENT", "SENT"]}, {**detail(), "raw": "!"}]:
            result, _ = self.read({"messages": [{"id": "external"}]}, [(bad, None)])
            self.assertEqual(result["status"], "error")
        result, _ = self.read({"messages": [{"id": "external"}, {"id": "external"}]}, [])
        self.assertEqual(result["status"], "error")

    def test_existing_folders_reject_sent_pagination(self):
        for folder in ["Inbox", "Archive", "Trash"]:
            result = gmail_snapshot.read_gmail_folder_snapshot(gmail_context(), provider_folder=folder,
                page_token="next", request_with_one_refresh=lambda *_: self.fail("transport called"))
            self.assertEqual(result["status"], "error")


class SentRouteTests(unittest.TestCase):
    def invoke(self, payload, resolution=None, responses=None):
        request = FakeHandler(payload)
        with patch.object(sent, "resolve_authenticated_gmail", return_value=resolution or {"status": "ok", "context": gmail_context()}) as auth, \
             patch.object(sent, "urlopen", side_effect=responses or [FakeResponse({"messages": []})]) as transport:
            sent.handler._handle_post(request)
        return request, auth, transport

    def test_authenticated_no_store_get_only(self):
        request, auth, transport = self.invoke({"mailboxId": "server-mailbox"}, responses=[
            FakeResponse({"messages": [{"id": "external"}]}), FakeResponse(detail())])
        self.assertEqual(request.status, 200)
        auth.assert_called_once_with(request.headers, "server-mailbox")
        self.assertIn(("Cache-Control", "no-store"), request.response_headers)
        for call in transport.call_args_list:
            self.assertEqual(call.args[0].method, "GET")
            self.assertEqual(call.kwargs["timeout"], 20)
            self.assertTrue(call.args[0].full_url.startswith(sent.GMAIL_API_BASE_URL + "/messages"))
        self.assertNotIn("test-only-access-token", json.dumps(request.response()))

    def test_invalid_fields_and_page_parameters_do_not_resolve_or_fetch(self):
        for extra in [{"email": "other@test"}, {"accessToken": "secret"}, {"q": "in:anywhere"},
                      {"provider": "google"}, {"limit": True}, {"limit": 0}, {"limit": 101},
                      {"pageToken": ""}, {"pageToken": None}, {"pageToken": "bad\n"}, {"pageToken": "a"*4097}]:
            request, auth, transport = self.invoke({"mailboxId": "server-mailbox", **extra})
            self.assertEqual(request.status, 400)
            auth.assert_not_called(); transport.assert_not_called()

    def test_ownership_and_connection_failures_never_call_gmail(self):
        for status in [401, 404, 409, 503]:
            request, _, transport = self.invoke({"mailboxId": "server-mailbox"}, {
                "status": "error", "status_code": status, "error": {"ok": False, "error": {"code": "denied"}}})
            self.assertEqual(request.status, status)
            transport.assert_not_called()

    def test_provider_token_error_timeout_and_size(self):
        for code in [400, 403, 429]:
            request, _, _ = self.invoke({"mailboxId": "server-mailbox", "pageToken": "next"}, responses=[HTTPError("", code, "", {}, None)])
            self.assertNotEqual(request.status, 200)
        request, _, transport = self.invoke({"mailboxId": "server-mailbox"}, responses=[TimeoutError(), TimeoutError()])
        self.assertEqual(request.status, 502)
        self.assertEqual(transport.call_count, 2)
        with patch.object(sent, "MAX_GMAIL_RESPONSE_BYTES", 1):
            request, _, _ = self.invoke({"mailboxId": "server-mailbox"})
        self.assertEqual(request.status, 502)

    def test_one_controlled_refresh(self):
        with patch.object(sent, "refresh_gmail_context", return_value={"status": "ok", "context": {**gmail_context(), "refresh_attempted": True}}) as refresh:
            request, _, _ = self.invoke({"mailboxId": "server-mailbox"}, responses=[HTTPError("", 401, "", {}, None), FakeResponse({"messages": []})])
        self.assertEqual(request.status, 200)
        refresh.assert_called_once()


if __name__ == "__main__":
    unittest.main()
