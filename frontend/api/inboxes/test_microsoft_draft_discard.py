"""No-network exact Microsoft Drafts soft-discard and safety release tests."""
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import URLError, HTTPError

from api.inboxes import microsoft_draft_discard as discard
from api.inboxes import microsoft_http as http
from api.inboxes import microsoft_mailbox_access as access
from api.inboxes import microsoft_production_pilot as policy
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes.test_microsoft_http import _request, MAILBOX, MESSAGE_ID

TOKEN = "unit-test-access-token-1234567"


class Response:
    def __init__(self, payload, status=200):
        self.status = status
        self.data = json.dumps(payload).encode()
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self, count):
        return self.data[:count]


class Transport:
    def __init__(self, *, fail=None, invalid_step=None, source_draft=True):
        self.calls = []
        self.fail = fail
        self.invalid_step = invalid_step
        self.source_draft = source_draft
    def __call__(self, req, *, timeout):
        self.calls.append(req)
        n = len(self.calls)
        if n == self.fail:
            raise URLError("no-network")
        if n == 1:
            return Response({"id": "deleted-folder"})
        if n == 2:
            return Response({"id": MESSAGE_ID, "parentFolderId":
                             "different-folder" if self.invalid_step == "source_folder" else "drafts-folder",
                             "isDraft": self.source_draft})
        if n == 3:
            return Response({"id": "drafts-folder"})
        if n == 4:
            return Response({"id": "unexpected" if self.invalid_step == "moved_id" else MESSAGE_ID,
                             "parentFolderId": "deleted-folder"}, status=201)
        if n == 5:
            return Response({"id": "unexpected" if self.invalid_step == "confirmed_id" else MESSAGE_ID,
                             "parentFolderId": "deleted-folder"})
        raise AssertionError("extra Graph call")


class DraftDiscardGraphTests(unittest.TestCase):
    def test_exact_draft_single_graph_post_and_deleted_confirmation(self):
        t = Transport()
        evidence = []
        result = discard.discard_exact_microsoft_draft_to_deleted(
            TOKEN, MESSAGE_ID, opener=t, before_write=lambda: evidence.append(len(t.calls)),
        )
        self.assertEqual(result, {"providerMessageId": MESSAGE_ID, "discarded": True})
        self.assertEqual(evidence, [3])
        self.assertEqual([r.get_method() for r in t.calls], ["GET", "GET", "GET", "POST", "GET"])
        self.assertEqual(t.calls[3].data, b'{"destinationId":"deleteditems"}')
        self.assertTrue(all(r.get_header("Prefer") == 'IdType="ImmutableId"' for r in t.calls))
        self.assertIn("/mailFolders/drafts/messages/", t.calls[1].full_url)
        self.assertIn("/mailFolders/drafts/messages/", t.calls[3].full_url)
        self.assertIn("/mailFolders/deleteditems/messages/", t.calls[4].full_url)
        self.assertNotIn("/me/messages/", " ".join(r.full_url for r in t.calls))

    def test_invalid_id_does_not_contact_graph(self):
        t = Transport()
        for source in ("", ".", "../other", "abc?#", "two words", None):
            with self.subTest(source=source), self.assertRaises(MicrosoftGraphError):
                discard.discard_exact_microsoft_draft_to_deleted(TOKEN, source, opener=t)
        self.assertFalse(t.calls)

    def test_exact_folder_and_draft_proofs_before_move(self):
        for invalid_step, source_draft in (("source_folder", True), (None, False)):
            t = Transport(invalid_step=invalid_step, source_draft=source_draft)
            with self.subTest(invalid_step=invalid_step), self.assertRaises(MicrosoftGraphError):
                discard.discard_exact_microsoft_draft_to_deleted(TOKEN, MESSAGE_ID, opener=t)
            self.assertEqual(sum(r.get_method() == "POST" for r in t.calls), 0)

    def test_owner_revoked_before_write(self):
        t = Transport()
        with self.assertRaises(RuntimeError):
            discard.discard_exact_microsoft_draft_to_deleted(
                TOKEN, MESSAGE_ID, opener=t,
                before_write=lambda: (_ for _ in ()).throw(RuntimeError("owner")),
            )
        self.assertEqual(len(t.calls), 3)

    def test_lost_post_ack_is_resolved_by_exact_deleted_read_without_retry(self):
        t = Transport(fail=4)
        result = discard.discard_exact_microsoft_draft_to_deleted(TOKEN, MESSAGE_ID, opener=t)
        self.assertTrue(result["discarded"])
        self.assertEqual([r.get_method() for r in t.calls], ["GET", "GET", "GET", "POST", "GET"])

    def test_unreadable_confirmation_stays_uncertain_and_never_retries(self):
        for lost_ack in (False, True):
            for failure in (URLError("offline"), HTTPError("https://graph.invalid", 404, "gone", {}, None)):
                t = Transport(fail=4 if lost_ack else None)
                def transport(request, *, timeout):
                    if len(t.calls) == 4:
                        t.calls.append(request)
                        raise failure
                    return t(request, timeout=timeout)
                with self.subTest(lost_ack=lost_ack, failure=type(failure).__name__), self.assertRaises(MicrosoftGraphError) as caught:
                    discard.discard_exact_microsoft_draft_to_deleted(TOKEN, MESSAGE_ID, opener=transport)
                self.assertEqual(caught.exception.code, "microsoft_mutation_unconfirmed")
                self.assertEqual(sum(r.get_method() == "POST" for r in t.calls), 1)

    def test_explicit_post_rejection_confirms_not_performed(self):
        for status in (401, 403, 404, 409, 412, 429):
            t = Transport()
            def transport(request, *, timeout):
                if request.get_method() == "POST":
                    t.calls.append(request)
                    raise HTTPError(request.full_url, status, "rejected", {}, None)
                return t(request, timeout=timeout)
            with self.subTest(status=status), self.assertRaises(MicrosoftGraphError) as caught:
                discard.discard_exact_microsoft_draft_to_deleted(TOKEN, MESSAGE_ID, opener=transport)
            self.assertEqual(caught.exception.code, "microsoft_draft_discard_not_performed")
            self.assertEqual([r.get_method() for r in t.calls], ["GET", "GET", "GET", "POST"])

    def test_server_error_or_malformed_post_requires_independent_proof(self):
        for error in ("microsoft_graph_unavailable", "microsoft_graph_invalid_response"):
            replies = [{"id": "deleted-folder"},
                       {"id": MESSAGE_ID, "parentFolderId": "drafts-folder", "isDraft": True},
                       {"id": "drafts-folder"}, MicrosoftGraphError(error),
                       {"id": "wrong", "parentFolderId": "deleted-folder"}]
            with patch.object(discard, "_json_graph", side_effect=replies) as graph_call:
                with self.assertRaises(MicrosoftGraphError) as caught:
                    discard.discard_exact_microsoft_draft_to_deleted(TOKEN, MESSAGE_ID)
                self.assertEqual(caught.exception.code, "microsoft_mutation_unconfirmed")
                self.assertEqual(sum(c.kwargs.get("method") == "POST" for c in graph_call.call_args_list), 1)

    def test_moved_or_confirmed_mismatch_fails_closed(self):
        for mismatch in ("moved_id", "confirmed_id"):
            t = Transport(invalid_step=mismatch)
            with self.subTest(mismatch=mismatch), self.assertRaises(MicrosoftGraphError) as context:
                discard.discard_exact_microsoft_draft_to_deleted(TOKEN, MESSAGE_ID, opener=t)
            self.assertEqual(context.exception.code, "microsoft_mutation_unconfirmed")
            self.assertEqual(sum(r.get_method() == "POST" for r in t.calls), 1)


class DraftDiscardHttpTests(unittest.TestCase):
    def test_production_policy_denies_before_parsing_or_owner_resolution(self):
        for enabled in ("0", "1"):
            with patch.dict(os.environ, {"VERCEL_ENV": "production", "MICROSOFT_DRAFT_DISCARD_ENABLED": enabled}), patch.object(
                access, "discard_owned_microsoft_draft",
            ) as owner:
                response = http.microsoft_inbox_response(
                    _request({"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID}),
                    mode="draft_discard",
                )
                self.assertEqual(response.status, 403)
                self.assertEqual(json.loads(response.body)["error"]["code"], "microsoft_pilot_read_only")
                owner.assert_not_called()

    def test_nonproduction_owner_guard_and_exact_payload(self):
        with patch.dict(os.environ, {"VERCEL_ENV": "development"}), patch.object(
            access, "discard_owned_microsoft_draft",
            return_value={"providerMessageId": MESSAGE_ID, "discarded": True},
        ) as owner:
            response = http.microsoft_inbox_response(
                _request({"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID}), mode="draft_discard",
            )
            self.assertEqual(response.status, 200)
            self.assertTrue(json.loads(response.body)["discarded"])
            owner.assert_called_once()
            for added in ({"folder": "Inbox"}, {"destinationId": "junk"}, {"accessToken": TOKEN}):
                data = {"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID, **added}
                self.assertEqual(http.microsoft_inbox_response(_request(data), mode="draft_discard").status, 400)
            owner.assert_called_once()

    def test_mismatched_confirmation_never_returns_success(self):
        with patch.dict(os.environ, {"VERCEL_ENV": "development"}), patch.object(
            access, "discard_owned_microsoft_draft",
            return_value={"providerMessageId": MESSAGE_ID, "discarded": False},
        ):
            response = http.microsoft_inbox_response(
                _request({"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID}), mode="draft_discard",
            )
            self.assertEqual(response.status, 503)

    def test_http_preserves_not_performed_and_uncertain_outcomes(self):
        for code, status in (("microsoft_draft_discard_not_performed", 409),
                             ("microsoft_mutation_unconfirmed", 503)):
            with self.subTest(code=code), patch.dict(os.environ, {"VERCEL_ENV": "development"}), patch.object(
                access, "discard_owned_microsoft_draft", side_effect=MicrosoftGraphError(code),
            ):
                response = http.microsoft_inbox_response(
                    _request({"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID}), mode="draft_discard",
                )
                self.assertEqual(response.status, status)
                self.assertEqual(json.loads(response.body)["error"]["code"], code)

    def test_production_gate_cannot_be_changed_by_environment(self):
        with patch.dict(os.environ, {"VERCEL_ENV": "production", "MICROSOFT_DRAFT_DISCARD_ENABLED": "1"}):
            self.assertTrue(policy.production_pilot_draft_discard_blocked())

    def test_future_production_release_still_requires_canonical_origin(self):
        # Exercise the boundary behind the gate without changing its source value.
        with patch.dict(os.environ, {"VERCEL_ENV": "production"}, clear=True), patch.object(
            policy, "_PRODUCTION_DRAFT_DISCARD_RELEASED", True,
        ), patch.object(
            access, "discard_owned_microsoft_draft",
            return_value={"providerMessageId": MESSAGE_ID, "discarded": True},
        ) as owner:
            response = http.microsoft_inbox_response(
                _request(
                    {"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID},
                    header_pairs=(("Origin", "https://app.cuevion.com"),),
                ),
                mode="draft_discard",
            )
            self.assertEqual(response.status, 200)
            self.assertTrue(json.loads(response.body)["discarded"])
            owner.assert_called_once()
        self.assertFalse(policy._PRODUCTION_DRAFT_DISCARD_RELEASED)

    def test_future_production_release_rejects_bad_origin_and_host_before_body(self):
        cases = (
            (None, "app.cuevion.com", (), 400),
            ("https://attacker.example", "app.cuevion.com", (), 403),
            ("https://app.cuevion.com", "attacker.example", (), 403),
            ("https://app.cuevion.com", "app.cuevion.com",
             (("X-Forwarded-Host", "attacker.example"),), 403),
            ("https://app.cuevion.com", "app.cuevion.com",
             (("Origin", "https://app.cuevion.com"),), 400),
        )
        with patch.dict(os.environ, {"VERCEL_ENV": "production"}, clear=True), patch.object(
            policy, "_PRODUCTION_DRAFT_DISCARD_RELEASED", True,
        ), patch.object(access, "discard_owned_microsoft_draft") as owner, patch.object(
            http, "_parse_body", side_effect=AssertionError("Boundary must precede body parsing"),
        ) as parse:
            for origin, host, extra, expected in cases:
                with self.subTest(origin=origin, host=host, extra=extra):
                    headers = ((("Origin", origin),) if origin is not None else ()) + extra
                    request = _request(
                        {"mailboxId": MAILBOX, "providerMessageId": MESSAGE_ID},
                        header_pairs=headers,
                    )
                    request.headers.replace_header("Host", host)
                    response = http.microsoft_inbox_response(request, mode="draft_discard")
                    self.assertEqual(response.status, expected)
            parse.assert_not_called()
            owner.assert_not_called()
        self.assertFalse(policy._PRODUCTION_DRAFT_DISCARD_RELEASED)


class DraftDiscardOwnerTests(unittest.TestCase):
    def test_authorized_lease_and_recheck(self):
        authority = type("FakeAuthority", (), {"owner_email": "user@example.com", "mailbox_id": MAILBOX})()
        with patch.object(access, "resolve_owned_microsoft_mailbox", return_value=authority), patch.object(
            access, "acquire_mailbox_mutation_lease", return_value={"status": "acquired", "token": "lease"},
        ) as acquire, patch.object(
            access, "release_mailbox_mutation_lease", return_value={"status": "released"},
        ) as release, patch.object(
            access, "_owned_token", return_value=(TOKEN, authority),
        ) as token, patch.object(
            access, "_verify_unchanged_owned_mailbox",
        ) as check, patch.object(
            discard, "discard_exact_microsoft_draft_to_deleted",
            return_value={"providerMessageId": MESSAGE_ID, "discarded": True},
        ) as move:
            result = access.discard_owned_microsoft_draft((), MAILBOX, MESSAGE_ID)
            self.assertTrue(result["discarded"])
            acquire.assert_called_once()
            token.assert_called_once_with((), MAILBOX, require_read_write=True)
            move.assert_called_once()
            move.call_args.kwargs["before_write"]()
            self.assertEqual(check.call_count, 2)
            release.assert_called_once()

    def test_lease_held_prevents_graph(self):
        authority = type("FakeAuthority", (), {"owner_email": "user@example.com", "mailbox_id": MAILBOX})()
        with patch.object(access, "resolve_owned_microsoft_mailbox", return_value=authority), patch.object(
            access, "acquire_mailbox_mutation_lease", return_value={"status": "held"},
        ), patch.object(discard, "discard_exact_microsoft_draft_to_deleted") as mover:
            with self.assertRaises(access.MicrosoftMailboxAccessError) as ctx:
                access.discard_owned_microsoft_draft((), MAILBOX, MESSAGE_ID)
            self.assertEqual(ctx.exception.code, "microsoft_mutation_in_progress")
            mover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
