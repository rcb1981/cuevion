from __future__ import annotations

import io
import json
import unittest
from urllib.error import HTTPError, URLError

from api.inboxes.microsoft_graph import (
    GRAPH_ROOT,
    MicrosoftGraphError,
    fetch_verified_microsoft_identity,
    get_microsoft_message,
    list_microsoft_inbox,
    verify_microsoft_identity_for_hint,
)

TOKEN = "secure-microsoft-test-access-token-123"


class FakeResponse:
    def __init__(self, payload):
        self.body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def read(self, count):
        return self.body[:count]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeOpener:
    def __init__(self, value):
        self.value = value
        self.calls = []

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        if isinstance(self.value, Exception):
            raise self.value
        return FakeResponse(self.value)


def graph_profile(**overrides):
    return {
        "id": "7e22bd91-fd78-40f9-a6be-9cb5c12a3327",
        "displayName": "Valid Sender",
        "mail": "artist@example.com",
        "userPrincipalName": "artist@example.com",
        **overrides,
    }


def graph_message(**overrides):
    return {
        "id": "AAMkAGVm.987/",
        "conversationId": "AAQkABc123",
        "subject": "Demo submission",
        "from": {"emailAddress": {"address": "sender@example.net", "name": "Sender"}},
        "bodyPreview": "Please listen.",
        "receivedDateTime": "2026-10-09T09:15:00Z",
        "isRead": False,
        "hasAttachments": True,
        "body": {"contentType": "text", "content": "Please listen to my demo."},
        **overrides,
    }


class MicrosoftGraphIdentityTests(unittest.TestCase):
    def test_verified_mail_address_is_canonical(self):
        opener = FakeOpener(graph_profile(mail="ARTIST@EXAMPLE.COM"))
        result = fetch_verified_microsoft_identity(TOKEN, opener=opener)
        self.assertEqual(result["email"], "artist@example.com")
        self.assertEqual(result["verifiedEmails"], ("artist@example.com",))
        self.assertEqual(len(opener.calls), 1)
        request, timeout = opener.calls[0]
        self.assertEqual(timeout, 20)
        self.assertEqual(request.full_url, GRAPH_ROOT + "/me?$select=id,displayName,mail,userPrincipalName")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.headers["Authorization"], "Bearer " + TOKEN)
        self.assertEqual(request.headers["Prefer"], 'IdType="ImmutableId"')

    def test_hint_must_match_graph_identity(self):
        opener = FakeOpener(graph_profile())
        self.assertEqual(
            verify_microsoft_identity_for_hint(TOKEN, "ARTIST@example.com", opener=opener)["email"],
            "artist@example.com",
        )
        with self.assertRaises(MicrosoftGraphError) as mismatch:
            verify_microsoft_identity_for_hint(TOKEN, "someone@example.com", opener=opener)
        self.assertEqual(mismatch.exception.code, "microsoft_identity_mismatch")

    def test_verified_upn_can_match_alternate_selected_alias(self):
        opener = FakeOpener(graph_profile(mail="alias@example.com", userPrincipalName="login@example.com"))
        result = verify_microsoft_identity_for_hint(TOKEN, "LOGIN@EXAMPLE.COM", opener=opener)
        self.assertEqual(result["email"], "login@example.com")
        self.assertEqual(result["verifiedEmails"], ("alias@example.com", "login@example.com"))

    def test_missing_mail_falls_back_to_upn(self):
        opener = FakeOpener(graph_profile(mail=None))
        result = verify_microsoft_identity_for_hint(TOKEN, "artist@example.com", opener=opener)
        self.assertEqual(result["email"], "artist@example.com")

    def test_missing_both_email_addresses_fails_closed(self):
        opener = FakeOpener(graph_profile(mail=None, userPrincipalName="not an email"))
        with self.assertRaises(MicrosoftGraphError) as caught:
            fetch_verified_microsoft_identity(TOKEN, opener=opener)
        self.assertEqual(caught.exception.code, "microsoft_identity_invalid")

    def test_malformed_account_id_fails_closed(self):
        opener = FakeOpener(graph_profile(id="line\nbreak"))
        with self.assertRaises(MicrosoftGraphError):
            fetch_verified_microsoft_identity(TOKEN, opener=opener)

    def test_invalid_hint_does_not_make_network_request(self):
        opener = FakeOpener(graph_profile())
        with self.assertRaises(MicrosoftGraphError):
            verify_microsoft_identity_for_hint(TOKEN, "not-an-email", opener=opener)
        self.assertFalse(opener.calls)

    def test_invalid_token_does_not_make_network_request(self):
        opener = FakeOpener(graph_profile())
        with self.assertRaises(MicrosoftGraphError) as caught:
            fetch_verified_microsoft_identity("short", opener=opener)
        self.assertEqual(caught.exception.code, "microsoft_reconnect_required")
        self.assertFalse(opener.calls)

    def test_non_ascii_authorization_header_fails_without_network(self):
        opener = FakeOpener(graph_profile())
        with self.assertRaises(MicrosoftGraphError):
            fetch_verified_microsoft_identity(TOKEN + "é", opener=opener)
        self.assertFalse(opener.calls)

    def test_unauthorized_and_forbidden_are_safe_errors(self):
        for status in (401, 403):
            with self.subTest(status=status):
                opener = FakeOpener(HTTPError(GRAPH_ROOT, status, "sensitive", {}, io.BytesIO(b"secret")))
                with self.assertRaises(MicrosoftGraphError) as caught:
                    fetch_verified_microsoft_identity(TOKEN, opener=opener)
                self.assertEqual(caught.exception.code, "microsoft_reconnect_required")
                self.assertNotIn("secret", str(caught.exception))

    def test_rate_limit_and_network_fail_without_private_payload(self):
        opener = FakeOpener(HTTPError(GRAPH_ROOT, 429, "request id private", {}, None))
        with self.assertRaises(MicrosoftGraphError) as caught:
            fetch_verified_microsoft_identity(TOKEN, opener=opener)
        self.assertEqual(caught.exception.code, "microsoft_graph_rate_limited")
        opener = FakeOpener(URLError("private network error"))
        with self.assertRaises(MicrosoftGraphError) as caught:
            fetch_verified_microsoft_identity(TOKEN, opener=opener)
        self.assertEqual(caught.exception.code, "microsoft_graph_unavailable")

    def test_bounded_identity_response(self):
        opener = FakeOpener(b"x" * (64 * 1024 + 1))
        with self.assertRaises(MicrosoftGraphError) as caught:
            fetch_verified_microsoft_identity(TOKEN, opener=opener)
        self.assertEqual(caught.exception.code, "microsoft_graph_invalid_response")

    def test_non_json_identity_response_fails_closed(self):
        opener = FakeOpener(b"<html>not-json</html>")
        with self.assertRaises(MicrosoftGraphError):
            fetch_verified_microsoft_identity(TOKEN, opener=opener)


class MicrosoftGraphInboxTests(unittest.TestCase):
    def test_list_inbox_is_scoped_bounded_and_normalized(self):
        opener = FakeOpener({"value": [graph_message()], "@odata.nextLink": GRAPH_ROOT + "/me/mailFolders/inbox/messages?$skiptoken=next"})
        result = list_microsoft_inbox(TOKEN, page_size=5, opener=opener)
        self.assertTrue(result["hasMore"])
        self.assertEqual(len(result["messages"]), 1)
        item = result["messages"][0]
        self.assertTrue(item["unread"])
        self.assertTrue(item["hasAttachments"])
        self.assertEqual(item["senderEmail"], "sender@example.net")
        self.assertEqual(item["receivedAt"], "2026-10-09T09:15:00+00:00")
        request, timeout = opener.calls[0]
        self.assertEqual(timeout, 20)
        self.assertTrue(request.full_url.startswith(GRAPH_ROOT + "/me/mailFolders/inbox/messages?"))
        self.assertIn("%24top=5", request.full_url)
        self.assertIn("%24orderby=receivedDateTime+desc", request.full_url)
        self.assertNotIn("access_token", request.full_url)

    def test_graph_owned_nextlink_folder_variants_are_opaque(self):
        # Graph may return a canonical folder ID instead of the original
        # well-known "inbox" path. Cuevion never dereferences a nextLink.
        for next_link in (
            GRAPH_ROOT + "/me/mailFolders('AAMkFolder')/messages?$skip=1",
            GRAPH_ROOT + "/users('account-id')/mailFolders/AAMkFolder/messages?$skiptoken=more",
            GRAPH_ROOT + "/me/mailfolders/inbox/messages?$skip=1",
        ):
            with self.subTest(link=next_link):
                opener = FakeOpener({
                    "value": [graph_message()],
                    "@odata.nextLink": next_link,
                })
                result = list_microsoft_inbox(TOKEN, page_size=1, opener=opener)
                self.assertTrue(result["hasMore"])
                self.assertEqual(len(result["messages"]), 1)
                self.assertEqual(len(opener.calls), 1)

    def test_invalid_response_stages_reveal_no_message_or_token_content(self):
        for payload, stage in (
            ({"value": [graph_message(**{"from": None})]}, "message"),
            ({"value": "not-a-list"}, "collection"),
            ({"value": [graph_message()], "@odata.nextLink": "https://evil.example/attack"}, "pagination"),
            (b"<html>untrusted graph response</html>", "transport"),
        ):
            with self.subTest(stage=stage):
                with self.assertRaises(MicrosoftGraphError) as error:
                    list_microsoft_inbox(TOKEN, page_size=1, opener=FakeOpener(payload))
                self.assertEqual(error.exception.code, "microsoft_graph_invalid_response")
                self.assertEqual(error.exception.safe_stage, stage)
                self.assertNotIn("sender@example.net", str(error.exception))
                self.assertNotIn(TOKEN, str(error.exception))

    def test_page_size_is_strictly_limited(self):
        for bad in (-1, 0, 26, "10", True, 1.5):
            with self.subTest(size=bad):
                opener = FakeOpener({"value": []})
                with self.assertRaises(MicrosoftGraphError):
                    list_microsoft_inbox(TOKEN, page_size=bad, opener=opener)
                self.assertFalse(opener.calls)

    def test_malformed_message_and_missing_fields_are_rejected(self):
        for bad in (graph_message(isRead="false"), graph_message(receivedDateTime="invalid"), graph_message(**{"from": None}), graph_message(subject=None)):
            with self.subTest(bad=bad.get("subject")):
                opener = FakeOpener({"value": [bad]})
                with self.assertRaises(MicrosoftGraphError):
                    list_microsoft_inbox(TOKEN, opener=opener)

    def test_external_nextlink_is_never_accepted(self):
        for url in ("http://evil.example/steal", GRAPH_ROOT + "/me/messages?skiptoken=test", "https://evil.example/v1.0/me/mailFolders/inbox/messages?x=1"):
            with self.subTest(url=url):
                opener = FakeOpener({"value": [], "@odata.nextLink": url})
                with self.assertRaises(MicrosoftGraphError):
                    list_microsoft_inbox(TOKEN, opener=opener)
                self.assertEqual(len(opener.calls), 1)

    def test_more_messages_than_requested_fails_closed(self):
        opener = FakeOpener({"value": [graph_message()] * 3})
        with self.assertRaises(MicrosoftGraphError):
            list_microsoft_inbox(TOKEN, page_size=2, opener=opener)

    def test_read_single_exact_id_url_encodes_slashes(self):
        entry = graph_message()
        opener = FakeOpener(entry)
        result = get_microsoft_message(TOKEN, entry["id"], opener=opener)
        self.assertEqual(result["id"], entry["id"])
        self.assertEqual(result["body"]["contentType"], "text")
        self.assertTrue(opener.calls[0][0].full_url.startswith(GRAPH_ROOT + "/me/messages/AAMkAGVm.987%2F?"))

    def test_cross_message_id_mismatch_rejected(self):
        opener = FakeOpener(graph_message(id="another-id"))
        with self.assertRaises(MicrosoftGraphError):
            get_microsoft_message(TOKEN, "wanted-id", opener=opener)

    def test_invalid_message_id_never_makes_request(self):
        for message_id in ("", ".", "..", "../me", "bad?q=evil", "bad#fragment", "\n", "A"*513):
            opener = FakeOpener(graph_message())
            with self.subTest(message_id=message_id), self.assertRaises(MicrosoftGraphError):
                get_microsoft_message(TOKEN, message_id, opener=opener)
            self.assertFalse(opener.calls)

    def test_unexpected_body_shape_is_rejected(self):
        opener = FakeOpener(graph_message(body={"contentType": "unknown", "content": "x"}))
        with self.assertRaises(MicrosoftGraphError):
            get_microsoft_message(TOKEN, "AAMkAGVm.987/", opener=opener)

    def test_empty_inbox(self):
        self.assertEqual(list_microsoft_inbox(TOKEN, opener=FakeOpener({"value": []})), {"messages": [], "hasMore": False})


if __name__ == "__main__":
    unittest.main()
