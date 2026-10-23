"""Real Graph module integration tests for draft-only Microsoft Forward, never send.

Uses the production-boundary Graph code, with an in-memory fake HTTP opener.
"""
from __future__ import annotations

import json
import unittest
from api.inboxes import microsoft_outbound_graph as graph
from api.inboxes.microsoft_graph import GRAPH_ROOT, MicrosoftGraphError
from api.inboxes.test_microsoft_outbound_graph import FakeResponse, TOKEN, SOURCE, DRAFT, THREAD

COMMENT = "Would you mind reviewing this release?"
ORIGINAL = "This original release submission contains the artist bio and metadata."
TO = "artist@example.com"
CC = "manager@example.com"


def compose():
    return {
        "mode": "forward", "sourceProviderMessageId": SOURCE,
        "body": COMMENT, "to": [TO], "cc": [CC],
    }


class ForwardGraph:
    def __init__(self):
        self.calls = []
        self.source_id = SOURCE
        self.has_attachments = False
        self.preview = ORIGINAL
        self.draft_body = f"<div>{COMMENT}</div><blockquote>{ORIGINAL}</blockquote>"
        self.actual_to = TO
        self.actual_cc = CC
        self.draft_id = DRAFT

    def __call__(self, request, *, timeout):
        self.calls.append(request)
        assert timeout == 20
        assert request.headers["Authorization"] == "Bearer " + TOKEN
        assert request.headers["Prefer"] == 'IdType="ImmutableId"'
        url = request.full_url.removeprefix(GRAPH_ROOT)
        method = request.get_method()
        if method == "GET" and url.startswith("/me/mailFolders/inbox/messages/"):
            return FakeResponse({"id": self.source_id, "conversationId": THREAD,
                                 "bodyPreview": self.preview, "hasAttachments": self.has_attachments})
        if method == "POST" and url.endswith("/createForward"):
            return FakeResponse({"id": self.draft_id, "isDraft": True,
                                 "conversationId": THREAD}, status=201)
        if method == "GET" and url.startswith("/me/messages/" + DRAFT):
            return FakeResponse({"id": DRAFT, "isDraft": True, "conversationId": THREAD,
                                 "body": {"contentType": "html", "content": self.draft_body},
                                 "toRecipients": [{"emailAddress": {"address": self.actual_to}}],
                                 "ccRecipients": [{"emailAddress": {"address": self.actual_cc}}]})
        raise AssertionError((method, url))


class RealForwardGraphTests(unittest.TestCase):
    def test_valid_forward_verifies_source_then_graph_draft_without_sending(self):
        fake = ForwardGraph()
        draft = graph.prepare_microsoft_draft(TOKEN, compose(), opener=fake)
        self.assertEqual(draft, {"draftId": DRAFT, "conversationId": THREAD})
        self.assertEqual([req.get_method() for req in fake.calls], ["GET", "POST", "GET"])
        self.assertEqual(fake.calls[0].full_url, GRAPH_ROOT +
                         "/me/mailFolders/inbox/messages/" + SOURCE +
                         "?$select=id,conversationId,bodyPreview,hasAttachments")
        self.assertEqual(fake.calls[1].full_url, GRAPH_ROOT +
                         "/me/mailFolders/inbox/messages/" + SOURCE + "/createForward")
        self.assertEqual(json.loads(fake.calls[1].data), {
            "message": {"toRecipients": [{"emailAddress": {"address": TO}}],
                        "ccRecipients": [{"emailAddress": {"address": CC}}]},
            "comment": COMMENT,
        })
        self.assertFalse(any(req.full_url.endswith("/send") for req in fake.calls))

    def test_attachments_block_forward_before_draft_creation(self):
        fake = ForwardGraph()
        fake.has_attachments = True
        with self.assertRaises(MicrosoftGraphError):
            graph.prepare_microsoft_draft(TOKEN, compose(), opener=fake)
        self.assertEqual([req.get_method() for req in fake.calls], ["GET"])

    def test_wrong_id_blocks_forward_before_draft_creation(self):
        fake = ForwardGraph()
        fake.source_id = "different-owner-or-folder"
        with self.assertRaises(MicrosoftGraphError):
            graph.prepare_microsoft_draft(TOKEN, compose(), opener=fake)
        self.assertEqual(len(fake.calls), 1)

    def test_short_or_missing_original_preview_blocks_forward(self):
        for preview in ("", "Too short", None):
            with self.subTest(preview=preview):
                fake = ForwardGraph()
                fake.preview = preview
                with self.assertRaises(MicrosoftGraphError):
                    graph.prepare_microsoft_draft(TOKEN, compose(), opener=fake)
                self.assertEqual(len(fake.calls), 1)

    def test_forward_body_must_contain_comment_and_original(self):
        for draft_body in (COMMENT, ORIGINAL, "Unrelated draft content", ""):
            with self.subTest(draft_body=draft_body):
                fake = ForwardGraph()
                fake.draft_body = draft_body
                with self.assertRaises(MicrosoftGraphError) as caught:
                    graph.prepare_microsoft_draft(TOKEN, compose(), opener=fake)
                self.assertEqual(caught.exception.code, "microsoft_draft_unconfirmed")
                self.assertEqual([req.get_method() for req in fake.calls], ["GET", "POST", "GET"])

    def test_forward_rejects_mismatched_to_or_cc(self):
        for field in ("actual_to", "actual_cc"):
            with self.subTest(field=field):
                fake = ForwardGraph()
                setattr(fake, field, "wrong@example.com")
                with self.assertRaises(MicrosoftGraphError) as caught:
                    graph.prepare_microsoft_draft(TOKEN, compose(), opener=fake)
                self.assertEqual(caught.exception.code, "microsoft_draft_unconfirmed")

    def test_comment_repeating_original_is_rejected_before_creating_draft(self):
        fake = ForwardGraph()
        payload = compose()
        payload["body"] = "Please review. " + ORIGINAL
        with self.assertRaises(MicrosoftGraphError):
            graph.prepare_microsoft_draft(TOKEN, payload, opener=fake)
        self.assertEqual([req.get_method() for req in fake.calls], ["GET"])

    def test_unexpected_form_fields_fail_before_network(self):
        for extra in ({"from": "attacker@example.com"},
                      {"subject": "Override"}, {"bcc": ["hidden@example.com"]},
                      {"to": ["invalid"]}, {"cc": [TO]}, {"to": []}):
            with self.subTest(extra=extra):
                fake = ForwardGraph()
                with self.assertRaises(MicrosoftGraphError):
                    graph.prepare_microsoft_draft(TOKEN, compose() | extra, opener=fake)
                self.assertFalse(fake.calls)


if __name__ == "__main__":
    unittest.main()
