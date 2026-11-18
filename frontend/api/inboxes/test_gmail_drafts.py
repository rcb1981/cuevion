"""Synthetic Drafts API and MIME contracts; all provider transports are mocked."""
import base64
import copy
import importlib
import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from api.inboxes import gmail_drafts as drafts
from api.inboxes import authenticated_gmail as auth
route = importlib.import_module("api.inboxes.fetch-gmail-drafts")
OWNER = SimpleNamespace(user_id="user-a", workspace_id="workspace-a")
CONTEXT = {"mailbox_id": "mailbox-a", "mailbox_email": "box@example.test", "owner_email": "owner@example.test",
           "access_token": "synthetic-secret", "scope": "https://www.googleapis.com/auth/gmail.readonly", "refresh_attempted": False}
TOKEN = {"provider": "google", "email": CONTEXT["mailbox_email"], "owner_email": CONTEXT["owner_email"],
         "credential_generation": "a"*43, "_storage_durable": True, "access_token": CONTEXT["access_token"]}
OWNED = {"status": "ok", "user": {"email": CONTEXT["owner_email"]},
         "inbox": {"id": "mailbox-a", "email": CONTEXT["mailbox_email"], "provider": "google", "connected": True, "connectionStatus": "connected"},
         "memberAuthority": OWNER, "config": {}}


def part(pid="", mime="text/plain", content=b"Hello from an external client", **extra):
    return {"partId": pid, "mimeType": mime, "filename": "", "headers": [],
            "body": {"size": len(content), "data": base64.urlsafe_b64encode(content).decode()}, **extra}


def value(did="draft-a", mid="message-a", payload=None):
    payload = copy.deepcopy(payload or part())
    payload["headers"] = [
        {"name": "From", "value": "author@example.test"}, {"name": "To", "value": "to@example.test"},
        {"name": "Cc", "value": "cc@example.test"}, {"name": "Bcc", "value": "private@example.test"},
        {"name": "Subject", "value": "External Gmail draft"}, *payload["headers"]]
    return {"id": did, "message": {"id": mid, "threadId": "same-thread", "labelIds": ["DRAFT"],
                                    "internalDate": "1700000000000", "payload": payload}}


def refs(*ids, **extra):
    return {"drafts": [{"id": did, "message": {"id": "message-"+did, "threadId": "same-thread"}} for did in ids], **extra}


class Response:
    def __init__(self, value):
        self.raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.headers = {"Content-Length": str(len(self.raw))}
    def read(self, size): return self.raw[:size]
    def __enter__(self): return self
    def __exit__(self, *args): return False


class Handler:
    def __init__(self, payload):
        raw = json.dumps(payload).encode(); self.rfile = io.BytesIO(raw); self.wfile = io.BytesIO()
        self.headers = {"content-length": str(len(raw)), "cookie": "synthetic-session"}
        self.response_headers = []; self.status = None
    def send_response(self, code): self.status = code
    def send_header(self, key, value): self.response_headers.append((key, value))
    def end_headers(self): pass
    def _handle_post(self): route.handler._handle_post(self)
    def response(self): return json.loads(self.wfile.getvalue())


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.owned = patch.object(drafts, "resolve_owned_mailbox", return_value=copy.deepcopy(OWNED)).start()
        self.context = patch.object(drafts, "resolve_gmail_context", return_value={"status": "ok", "context": dict(CONTEXT)}).start()
        self.token = patch.object(drafts, "load_google_token_record_with_metadata", return_value=(dict(TOKEN), None)).start()
        self.network = patch.object(drafts, "urlopen", side_effect=AssertionError("Unexpected provider access")).start()
        self.addCleanup(patch.stopall)
        self.scope = drafts.resolve_context({}, "mailbox-a")["draft_scope"]

    def read(self, responses, **kwargs):
        self.network.side_effect = [Response(v) if not isinstance(v, Exception) else v for v in responses]
        return drafts.read_drafts({}, "mailbox-a", **kwargs)

    def test_empty(self):
        result = self.read([{}]); self.assertEqual(result["drafts"], []); self.assertIsNone(result["nextPageToken"])
        self.assertEqual(self.network.call_args.args[0].method, "GET")

    def test_external_drafts_same_thread_distinct_and_duplicate_refs(self):
        result = self.read([refs("draft-a", "draft-b", "draft-a"), value(), value("draft-b", "message-b")])
        self.assertEqual([d["draftId"] for d in result["drafts"]], ["draft-a", "draft-b"])
        self.assertEqual([d["messageId"] for d in result["drafts"]], ["message-a", "message-b"])
        self.assertEqual(result["drafts"][0]["threadId"], result["drafts"][1]["threadId"])
        self.assertEqual(result["drafts"][0]["bcc"], "private@example.test")
        for call in self.network.call_args_list:
            req = call.args[0]; self.assertEqual(req.method, "GET"); self.assertIn("/drafts", req.full_url)
            self.assertGreater(call.kwargs["timeout"], 0); self.assertLessEqual(call.kwargs["timeout"], 20)
        self.assertNotIn("synthetic-secret", json.dumps(result))
        self.assertNotIn("priority", json.dumps(result).lower())

    def test_pagination_is_bounded_and_encoded(self):
        opaque = "opaque+/=&q=all"
        result = self.read([refs(nextPageToken="next")], token=opaque, scope=self.scope, limit=7)
        query = parse_qs(urlsplit(self.network.call_args.args[0].full_url).query)
        self.assertEqual(query, {"maxResults": ["7"], "pageToken": [opaque]})
        self.assertEqual(result["nextPageToken"], "next")
        for invalid in [refs(nextPageToken=opaque), {"drafts": None}, {"drafts": [None]}, refs(nextPageToken="bad\n"), {"resultSizeEstimate": True}]:
            with self.assertRaises(drafts.DraftReadError): self.read([invalid], token=opaque, scope=self.scope)

    def test_full_text_html_mime_and_attachment_inventory(self):
        tree = part(mime="multipart/alternative", content=b"", parts=[part("0"), part("1", "text/html", b"<p><b>HTML body</b></p>"),
            part("2", "application/pdf", b"", filename="file.pdf", headers=[{"name":"Content-Disposition","value":"attachment"}], body={"size": 400, "attachmentId": "attachment-id"})])
        result = self.read([value(payload=tree)], draft_id="draft-a", scope=self.scope)["draft"]
        self.assertEqual(result["previewStatus"], "available")
        self.assertEqual(result["bodyText"], "Hello from an external client")
        self.assertEqual(result["bodyHtml"], "<p><b>HTML body</b></p>")
        self.assertEqual(result["attachments"][0]["availability"], "metadata_only")
        self.assertEqual(result["attachments"][0]["attachmentId"], "attachment-id")
        self.assertEqual(len(result["mimeParts"]), 4)
        self.assertTrue(self.network.call_args.args[0].full_url.endswith("/drafts/draft-a?format=full"))
        self.assertEqual(self.network.call_count, 1, "no attachment downloads")

    def test_unparsed_nested_mime_never_claims_complete_inventory(self):
        for tree in [part(mime="multipart/signed", content=b"", parts=[part("0")]),
                     part(mime="message/rfc822", filename="attached.eml", parts=[part("0")])]:
            result = self.read([value(payload=tree)], draft_id="draft-a", scope=self.scope)["draft"]
            self.assertEqual(result["previewStatus"], "unavailable")
            self.assertFalse(result["inventoryComplete"])
            self.assertIsNone(result["bodyText"])
            self.assertIsNone(result["bodyHtml"])

    def test_changed_message_id_retains_draft_identity(self):
        result = self.read([value(mid="new-message-id")], draft_id="draft-a", scope=self.scope)["draft"]
        self.assertEqual(result["draftId"], "draft-a"); self.assertEqual(result["messageId"], "new-message-id")

    def test_unsupported_missing_and_malformed_content_never_fabricated(self):
        examples = [part(mime="application/pkcs7-mime"), part(body={"size":4,"attachmentId":"body-id"}),
                    part(body={"size":9,"data":"!"}), part(headers=[{"name":"Content-Type","value":"text/plain; charset=unknown-charset"}]),
                    part(mime="multipart/mixed",content=b"",parts=[part("0"),part("1")])]
        for payload in examples:
            result = self.read([value(payload=payload)], draft_id="draft-a", scope=self.scope)["draft"]
            self.assertEqual(result["previewStatus"], "unavailable")
            self.assertIsNone(result["bodyText"]); self.assertIsNone(result["bodyHtml"])

    def test_empty_body_is_not_missing_content(self):
        result=self.read([value(payload=part(content=b""))],draft_id="draft-a",scope=self.scope)["draft"]
        self.assertEqual(result["previewStatus"],"available"); self.assertEqual(result["bodyText"], "")

    def test_oversized_content_and_transport_have_explicit_unavailable_preview(self):
        result = self.read([value(payload=part(content=b"x"*(drafts.MAX_BODY_BYTES+1)))], draft_id="draft-a", scope=self.scope)["draft"]
        self.assertEqual(result["unavailableReason"], "too_large")
        result = self.read([b"x"*(drafts.MAX_GMAIL_RESPONSE_BYTES+1), value()], draft_id="draft-a", scope=self.scope)["draft"]
        self.assertEqual(result["unavailableReason"], "too_large"); self.assertFalse(result["inventoryComplete"])
        with self.assertRaises(drafts.DraftReadError): self.read([b"x"*(drafts.MAX_GMAIL_RESPONSE_BYTES+1)])

    def test_invalid_provider_identity_headers_labels_and_json(self):
        for bad in [{**value(),"id":"wrong"}, {**value(),"message":None}, b'{"drafts":[],"drafts":[]}', b'[]']:
            with self.assertRaises(drafts.DraftReadError): self.read([bad],draft_id="draft-a",scope=self.scope)
        for key,bad in [("labelIds",["SENT"]),("threadId",None)]:
            data=value(); data["message"][key]=bad
            with self.assertRaises(drafts.DraftReadError): self.read([data],draft_id="draft-a",scope=self.scope)

    def test_removed_between_list_and_get_is_not_substituted(self):
        result=self.read([refs("draft-a"),HTTPError("",404,"",{},None)])
        self.assertEqual(result["drafts"],[])

    def test_foreign_or_unverifiable_credentials_never_reach_gmail(self):
        for updates in [{"owner_email":"other@example.test"},{"credential_generation":None},{"_storage_durable":False},{"email":"other@example.test"}]:
            self.token.return_value=({**TOKEN,**updates},None)
            with self.assertRaises(drafts.DraftReadError): drafts.read_drafts({},"mailbox-a")
        self.network.assert_not_called()

    def test_owned_mailbox_failures_disconnected_and_wrong_provider(self):
        for status in [401,404,409,503]:
            self.owned.return_value={"status":"error","status_code":status,"error":{"error":{"code":"denied"}}}
            with self.assertRaises(drafts.DraftReadError) as caught: drafts.read_drafts({},"mailbox-a")
            self.assertEqual(caught.exception.status,status)
        self.network.assert_not_called()
        self.owned.assert_called_with({},"mailbox-a",include_member_authority=True)
        # Exercise the unchanged authenticated Gmail resolver for disconnected/non-Google records.
        for changed in [{"connected":False},{"connectionStatus":"disconnected"},{"provider":"microsoft"}]:
            result=auth.resolve_gmail_context({**OWNED,"inbox":{**OWNED["inbox"],**changed}})
            self.assertNotEqual(result["status"],"ok")

    def test_stale_generation_workspace_account_and_logout_fail_at_publication(self):
        for change in ["generation","workspace","user","logout"]:
            self.owned.return_value=copy.deepcopy(OWNED); self.owned.side_effect=None; self.token.side_effect=None
            if change=="generation": self.token.side_effect=[(dict(TOKEN),None),({**TOKEN,"credential_generation":"b"*43},None)]
            else:
                other=copy.deepcopy(OWNED)
                if change=="workspace": other["memberAuthority"].workspace_id="workspace-b"
                if change=="user": other["memberAuthority"].user_id="user-b"
                if change=="logout": other={"status":"error","status_code":401,"error":{"error":{"code":"unauthorized"}}}
                self.owned.side_effect=[OWNED,other]
            with self.assertRaises(drafts.DraftReadError): self.read([{}])
        self.owned.side_effect=None; self.owned.return_value=OWNED; self.token.side_effect=None
        with self.assertRaises(drafts.DraftReadError): drafts.read_drafts({},"mailbox-a",scope="0"*64)

    def test_reconnect_between_context_and_generation_resolution_fails_closed(self):
        self.token.return_value = ({**TOKEN, "credential_generation": "b"*43,
                                    "access_token": "new-connection-token"}, None)
        with self.assertRaises(drafts.DraftReadError): drafts.read_drafts({}, "mailbox-a")
        self.network.assert_not_called()

    def test_one_controlled_refresh_and_safe_errors(self):
        with patch.object(drafts,"refresh_gmail_context",return_value={"status":"ok","context":{**CONTEXT,"refresh_attempted":True}}) as refresh:
            self.read([HTTPError("",401,"private body must not appear",{},None),{}]); refresh.assert_called_once()
        for error in [TimeoutError(), HTTPError("",403,"private body",{},None), HTTPError("",429,"private body",{},None)]:
            with self.assertRaises(drafts.DraftReadError) as caught: self.read([error])
            self.assertNotIn("private body",str(caught.exception))

    def test_route_rejects_credentials_queries_invalid_limits_and_mixed_operations(self):
        for extra in [{"accessToken":"private"},{"userId":"user-b"},{"workspaceId":"other"},{"q":"all"},{"limit":True},{"limit":21},{"limit":0},{"pageToken":""},{"draftId":"draft-a"},{"scope":None},{"draftId":"draft-a","scope":self.scope,"pageToken":"next"}]:
            req=Handler({"mailboxId":"mailbox-a",**extra}); route.handler.do_POST(req)
            self.assertEqual(req.status,400)
        self.network.assert_not_called()

    def test_route_no_store_and_no_content_logging(self):
        self.network.side_effect=[Response({})]
        req=Handler({"mailboxId":"mailbox-a"}); route.handler.do_POST(req)
        self.assertEqual(req.status,200); self.assertIn(("Cache-Control","no-store"),req.response_headers)
        self.assertNotIn(CONTEXT["access_token"],req.wfile.getvalue().decode())
        source=Path(drafts.__file__).read_text()
        for forbidden in ["to_message_preview", "build_priority_candidate", "gmail_snapshot", "print(", "logger", "drafts.send", 'method="PATCH"', 'method="DELETE"']:
            self.assertNotIn(forbidden,source)

if __name__ == "__main__": unittest.main()
