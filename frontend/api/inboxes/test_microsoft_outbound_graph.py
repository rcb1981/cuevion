"""No-network Graph draft and send transport contracts."""
from __future__ import annotations

import io
import json
import unittest
from urllib.error import HTTPError, URLError

from api.inboxes import microsoft_outbound_graph as graph
from api.inboxes.microsoft_graph import MicrosoftGraphError, GRAPH_ROOT

TOKEN = "microsoft-test-access-token-12345"
DRAFT = "draft-ABC"
SOURCE = "source-XYZ"
THREAD = "conversation-123"


class FakeResponse:
    def __init__(self, content=None, status=200):
        self.status = status
        self.content = json.dumps(content if content is not None else {}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, count):
        return self.content[:count]


class StubGraph:
    def __init__(self, *, mode="new", body="Hello from Cuevion"):
        self.calls = []
        self.mode = mode
        self.body = body
        self.error_at = None
        self.return_bad = None

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        step = len(self.calls)
        if self.error_at == step:
            raise URLError("private bearer token leak")
        if self.return_bad == step:
            return FakeResponse({"id":"wrong", "isDraft":False})
        path = request.full_url.removeprefix(GRAPH_ROOT)
        method = request.get_method()
        if path.startswith("/me/mailFolders/inbox/messages/") and method == "GET":
            return FakeResponse({"id": SOURCE, "conversationId": THREAD})
        if method == "POST" and (path == "/me/messages" or path.endswith("/createReply") or path.endswith("/createReplyAll")):
            return FakeResponse(
                {"id": DRAFT, "isDraft": True, "conversationId": THREAD}, status=201
            )
        if method == "PATCH" and path == "/me/messages/" + DRAFT:
            return FakeResponse({"id": DRAFT, "isDraft": True, "conversationId": THREAD})
        if method == "GET" and path.startswith("/me/messages/" + DRAFT):
            return FakeResponse({
                "id": DRAFT, "isDraft": True, "conversationId": THREAD,
                "subject": "My subject", "toRecipients": [{"emailAddress":{"address":"test@example.com"}}],
                "ccRecipients": [],
                "body": {"contentType":"text", "content":self.body},
            })
        if method == "POST" and path == "/me/messages/" + DRAFT + "/send":
            return FakeResponse(None, status=202)
        raise AssertionError((method, path))


def new_message():
    return {
        "mode": "new", "to": ["test@example.com"], "subject": "My subject",
        "body": "Hello from Cuevion",
    }


class OutboundGraphTests(unittest.TestCase):
    def test_new_creates_exact_draft_and_verifies_before_send(self):
        fake = StubGraph()
        draft = graph.prepare_microsoft_draft(TOKEN, new_message(), opener=fake)
        self.assertEqual(draft, {"draftId": DRAFT, "conversationId": THREAD})
        self.assertEqual([x.get_method() for x, _ in fake.calls], ["POST", "GET"])
        first, timeout = fake.calls[0]
        self.assertEqual(timeout, 20)
        self.assertEqual(first.full_url, GRAPH_ROOT + "/me/messages")
        self.assertEqual(first.headers["Authorization"], "Bearer " + TOKEN)
        self.assertEqual(first.headers["Prefer"], 'IdType="ImmutableId"')
        self.assertEqual(json.loads(first.data), {
            "subject":"My subject",
            "body":{"contentType":"Text", "content":"Hello from Cuevion"},
            "toRecipients":[{"emailAddress":{"address":"test@example.com"}}],
            "ccRecipients":[],
        })
        self.assertIn("?$select=", fake.calls[1][0].full_url)
        self.assertEqual(len(fake.calls), 2, "preparation never sends automatically")

    def test_reply_and_reply_all_use_folder_scoped_source_and_thread(self):
        for mode, endpoint in (("reply", "createReply"), ("reply_all", "createReplyAll")):
            fake = StubGraph(mode=mode)
            payload = {"mode":mode, "sourceProviderMessageId":SOURCE, "body":"Hello from Cuevion"}
            draft = graph.prepare_microsoft_draft(TOKEN, payload, opener=fake)
            self.assertEqual(draft["conversationId"], THREAD)
            self.assertEqual([x.get_method() for x, _ in fake.calls], ["GET", "POST", "PATCH", "GET"])
            self.assertTrue(fake.calls[0][0].full_url.startswith(
                GRAPH_ROOT + "/me/mailFolders/inbox/messages/" + SOURCE
            ))
            self.assertEqual(
                fake.calls[1][0].full_url,
                GRAPH_ROOT + "/me/mailFolders/inbox/messages/" + SOURCE + "/" + endpoint,
            )
            self.assertEqual(json.loads(fake.calls[2][0].data), {
                "body":{"contentType":"Text", "content":"Hello from Cuevion"}
            })
            self.assertNotIn("toRecipients", json.loads(fake.calls[2][0].data))
            self.assertFalse(any(x.get_method() == "POST" and x.full_url.endswith("/send")
                                 for x, _ in fake.calls))

    def test_submit_once_returns_accepted_not_delivery_claim(self):
        fake = StubGraph()
        result = graph.submit_prepared_microsoft_draft(TOKEN, DRAFT, opener=fake)
        self.assertEqual(result, {"status":"accepted_by_microsoft", "draftId":DRAFT})
        self.assertEqual(len(fake.calls), 1)
        req, timeout = fake.calls[0]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.full_url, GRAPH_ROOT + "/me/messages/" + DRAFT + "/send")
        self.assertEqual(req.data, b"")
        self.assertEqual(timeout, 20)

    def test_outbound_validation_no_network(self):
        invalid = [
            {"mode":"new", "to":[], "subject":"abc", "body":"Hi"},
            {"mode":"new", "to":["invalid"], "subject":"abc", "body":"Hi"},
            {"mode":"new", "to":["test@example.com"], "cc":["test@example.com"], "subject":"x", "body":"Hi"},
            {"mode":"new", "to":["test@example.com"], "subject":"x\r\nBCC:evil", "body":"Hi"},
            {"mode":"new", "to":["test@example.com"], "subject":"ok", "body":" "},
            {"mode":"new", "to":["test@example.com"], "subject":"ok", "body":"Hi", "from":"attacker@example.com"},
            {"mode":"reply", "sourceProviderMessageId":"../other", "body":"hi"},
            {"mode":"reply", "sourceProviderMessageId":SOURCE, "body":"hi", "to":["attacker@example.com"]},
            {"mode":"forward", "sourceProviderMessageId":SOURCE, "body":"hi"},
        ]
        for payload in invalid:
            fake = StubGraph()
            with self.subTest(payload=payload), self.assertRaises(MicrosoftGraphError):
                graph.prepare_microsoft_draft(TOKEN, payload, opener=fake)
            self.assertFalse(fake.calls)

    def test_mismatched_or_unconfirmed_draft_is_never_sent(self):
        for step in (1, 2):
            fake = StubGraph()
            fake.return_bad = step
            with self.subTest(step=step), self.assertRaises(MicrosoftGraphError):
                graph.prepare_microsoft_draft(TOKEN, new_message(), opener=fake)
            self.assertNotIn("send", [r.full_url.rsplit("/",1)[-1] for r, _ in fake.calls])

    def test_mismatched_cc_after_draft_prevents_send(self):
        fake = StubGraph()
        compose = {**new_message(), "cc": ["cc@example.com"]}
        with self.assertRaises(MicrosoftGraphError) as caught:
            graph.prepare_microsoft_draft(TOKEN, compose, opener=fake)
        self.assertEqual(caught.exception.code, "microsoft_draft_unconfirmed")
        self.assertEqual([req.get_method() for req,_ in fake.calls],["POST","GET"])
        self.assertFalse(any(req.full_url.endswith("/send") for req,_ in fake.calls))

    def test_reply_preflight_mismatch_prevents_draft(self):
        class Bad:
            calls=0
            def __call__(self, req, *, timeout):
                self.calls+=1
                return FakeResponse({"id":"wrong", "conversationId": THREAD})
        fake=Bad()
        with self.assertRaises(MicrosoftGraphError):
            graph.prepare_microsoft_draft(
                TOKEN, {"mode":"reply", "sourceProviderMessageId":SOURCE,"body":"hi"}, opener=fake
            )
        self.assertEqual(fake.calls,1)

    def test_lost_draft_creation_ack_never_retries(self):
        fake=StubGraph()
        fake.error_at=1
        with self.assertRaises(MicrosoftGraphError) as caught:
            graph.prepare_microsoft_draft(TOKEN,new_message(),opener=fake)
        self.assertEqual(caught.exception.code, "microsoft_draft_unconfirmed")
        self.assertEqual(len(fake.calls),1)
        self.assertNotIn("private",str(caught.exception))

    def test_lost_send_ack_never_retries(self):
        fake=StubGraph()
        fake.error_at=1
        with self.assertRaises(MicrosoftGraphError) as caught:
            graph.submit_prepared_microsoft_draft(TOKEN,DRAFT,opener=fake)
        self.assertEqual(caught.exception.code, "microsoft_send_unconfirmed")
        self.assertEqual(len(fake.calls),1)
        self.assertNotIn("private",str(caught.exception))

    def test_invalid_send_draft_id_no_network(self):
        for message_id in ("", "../bad", "bad?query", "A"*513, ".", "bad space"):
            fake=StubGraph()
            with self.assertRaises(MicrosoftGraphError):
                graph.submit_prepared_microsoft_draft(TOKEN,message_id,opener=fake)
            self.assertFalse(fake.calls)

    def test_graph_status_error_is_sanitized(self):
        for status, code in (
            (401,"microsoft_reconnect_required"),
            (403,"microsoft_reconnect_required"),
            (429,"microsoft_graph_rate_limited"),
            (412,"microsoft_draft_conflict"),
        ):
            def opener(req, *, timeout):
                raise HTTPError(req.full_url,status,"private",{},io.BytesIO(b"token secret"))
            with self.subTest(status=status), self.assertRaises(MicrosoftGraphError) as caught:
                graph.submit_prepared_microsoft_draft(TOKEN,DRAFT,opener=opener)
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("private",str(caught.exception))


if __name__ == "__main__":
    unittest.main()
