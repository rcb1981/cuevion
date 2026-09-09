from __future__ import annotations

import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from api.auth import runtime
from api.collaboration.owner_rate_limit import OwnerRateLimitDecision
from . import exact_message_http as http


MEMBER = runtime.AuthenticatedMemberContext(
    "usr_" + "A" * 22, "owner@example.com", "Owner", "wsp_" + "W" * 22, "owner",
)
GOOGLE = {"provider": "google", "providerMessageId": "exact-message"}
IMAP = {"provider": "custom_imap", "folder": "INBOX", "uidValidity": "456", "imapUid": "123"}
REAL_AUTH = runtime.resolve_authenticated_member


def request(source=None, *, raw=None, headers=None, path=http.ROUTE, method="POST"):
    body = json.dumps({"v": 1, "mailboxId": "mailbox-1", "sourceRef": source or GOOGLE}).encode() if raw is None else raw
    pairs = headers if headers is not None else [
        ("Host", "app.cuevion.com"), ("Origin", "https://app.cuevion.com"),
        ("Content-Type", "application/json"), ("Content-Length", str(len(body))),
    ]
    return SimpleNamespace(command=method, path=path, rfile=io.BytesIO(body),
                           headers=SimpleNamespace(raw_items=lambda: iter(pairs)))


def owned(provider="google", member=MEMBER):
    return {"status": "ok", "memberAuthority": member,
            "user": {"email": member.email}, "inbox": {"id": "mailbox-1", "provider": provider}}


class ExactMessageHttpTests(unittest.TestCase):
    def setUp(self):
        self.owned = self.enterContext(patch.object(http, "resolve_owned_managed_inbox_record", return_value=owned()))
        self.rate = self.enterContext(patch.object(http, "consume_exact_message_rate_limit", return_value=OwnerRateLimitDecision("allowed")))
        self.gmail = self.enterContext(patch.object(http, "resolve_gmail_context", return_value={"status": "ok", "context": {"mailbox_id": "mailbox-1"}}))
        self.imap = self.enterContext(patch.object(http, "resolve_authenticated_imap_mailbox", return_value={
            "status": "ok", "memberAuthority": MEMBER,
            "mailbox": {"mailboxId": "mailbox-1", "ownerEmail": MEMBER.email},
        }))
        self.google_fetch = self.enterContext(patch.object(http.provider, "fetch_google_message", return_value={"status": "ok", "message": {"id": "canonical"}}))
        self.imap_fetch = self.enterContext(patch.object(http.provider, "fetch_imap_message", return_value={"status": "ok", "message": {"id": "canonical"}}))
        self.project = self.enterContext(patch.object(http.provider, "normalize_exact_message", return_value={"id": "canonical"}))

    def invoke(self, req=None):
        return http.exact_message_response(req or request(), environment={"VERCEL_ENV": "production"})

    def error(self, response, code):
        self.assertEqual(response.status, http.ERROR_STATUSES[code])
        self.assertEqual(json.loads(response.body), {"error": {"code": code, "message": "The exact message could not be loaded."}})

    def test_google_success_uses_current_owned_context_and_exact_source(self):
        result = self.invoke()
        self.assertEqual(result.status, 200)
        self.assertEqual(json.loads(result.body), {"v": 1, "mailboxId": "mailbox-1", "sourceRef": GOOGLE, "message": {"id": "canonical"}})
        self.owned.assert_called_once()
        self.assertEqual(self.owned.call_args.kwargs, {"include_member_authority": True})
        self.gmail.assert_called_once_with(self.owned.return_value)
        self.google_fetch.assert_called_once_with({"mailbox_id": "mailbox-1"}, GOOGLE)
        self.imap.assert_not_called()
        self.rate.assert_called_once_with(MEMBER, environment={"VERCEL_ENV": "production"})

    def test_imap_success_revalidates_current_member_before_provider(self):
        self.owned.return_value = owned("custom_imap")
        result = self.invoke(request(IMAP))
        self.assertEqual(result.status, 200)
        self.imap_fetch.assert_called_once_with(self.imap.return_value["mailbox"], IMAP)
        self.gmail.assert_not_called()

    def test_wrong_mailbox_is_denied_before_credentials_or_provider(self):
        self.owned.return_value = {"status": "not_found"}
        self.error(self.invoke(), "mailbox_not_found")
        self.gmail.assert_not_called()
        self.google_fetch.assert_not_called()
        self.rate.assert_not_called()

    def test_unauthenticated_is_denied(self):
        self.owned.return_value = {"status": "unauthorized"}
        self.error(self.invoke(), "authentication_required")
        self.gmail.assert_not_called()

    def test_guest_cookie_alone_is_denied_by_real_generic_authority(self):
        from api import user_config_store
        from api.auth import session_store
        req = request()
        base = list(req.headers.raw_items())
        base.append(("Cookie", "cuevion_guest_session=guest-only"))
        req.headers = SimpleNamespace(raw_items=lambda: iter(base))
        with patch.object(http, "resolve_owned_managed_inbox_record", user_config_store.resolve_owned_managed_inbox_record), patch.object(
            runtime, "resolve_authenticated_member", REAL_AUTH,
        ), patch.object(session_store, "build_runtime_session_store") as store:
            self.error(self.invoke(req), "authentication_required")
            store.assert_not_called()
        self.google_fetch.assert_not_called()

    def test_provider_mismatch_both_directions_prevents_credentials(self):
        for configured, source in (("google", IMAP), ("custom_imap", GOOGLE)):
            self.owned.return_value = owned(configured)
            self.error(self.invoke(request(source)), "provider_mismatch")
        self.gmail.assert_not_called()
        self.imap.assert_not_called()
        self.rate.assert_not_called()

    def test_bad_authority_shapes_and_owner_identity_fail_closed(self):
        for value in (None, {}, {**owned(), "memberAuthority": None},
                      {**owned(), "inbox": {"id": "foreign", "provider": "google"}},
                      {**owned(), "user": {"email": "other@example.com"}}):
            self.owned.return_value = value
            self.error(self.invoke(), "service_unavailable")
        self.google_fetch.assert_not_called()

    def test_scope_or_owner_change_during_imap_resolution_is_fenced(self):
        self.owned.return_value = owned("custom_imap")
        for member in (
            runtime.AuthenticatedMemberContext(MEMBER.user_id, MEMBER.email, MEMBER.name, "wsp_" + "X" * 22, "owner"),
            runtime.AuthenticatedMemberContext("usr_" + "B" * 21 + "A", MEMBER.email, MEMBER.name, MEMBER.workspace_id, "owner"),
        ):
            self.imap.return_value["memberAuthority"] = member
            self.error(self.invoke(request(IMAP)), "forbidden")
        self.imap_fetch.assert_not_called()

    def test_stale_ownership_resolution_rejects_before_fetch(self):
        self.owned.return_value = owned("custom_imap")
        self.imap.return_value = {"status": "not_found"}
        self.error(self.invoke(request(IMAP)), "service_unavailable")
        self.imap_fetch.assert_not_called()

    def test_query_string_and_other_paths_fail_before_authority(self):
        for path in (http.ROUTE + "?x=1", http.ROUTE + "/", "/api/notifications"):
            self.error(self.invoke(request(path=path)), "source_invalid")
        self.owned.assert_not_called()

    def test_non_post_methods_are_denied(self):
        for method in ("GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"):
            result = self.invoke(request(method=method))
            self.error(result, "method_not_allowed")
            self.assertIn(("Allow", "POST"), result.headers)
        self.owned.assert_not_called()

    def test_host_origin_json_and_duplicate_headers_are_checked(self):
        pairs = list(request().headers.raw_items())
        invalid = [
            [(k, v) for k, v in pairs if k != "Origin"],
            [(k, "https://wrong.example" if k == "Origin" else v) for k, v in pairs],
            [(k, "wrong.example" if k == "Host" else v) for k, v in pairs],
            pairs + [("X-Forwarded-Host", "wrong.example")],
            pairs + [("Origin", "https://app.cuevion.com")],
        ]
        for headers in invalid:
            self.assertNotEqual(self.invoke(request(headers=headers)).status, 200)
        self.error(self.invoke(request(headers=[(k, "text/plain" if k == "Content-Type" else v) for k, v in pairs])), "unsupported_media_type")
        self.owned.assert_not_called()

    def test_body_is_strict_bounded_json(self):
        for body in (b"[]", b"null", b"{", b"\xff", b'{"v":1,"v":1}',
                     b'{"v":NaN}', b'{"v":1,"mailboxId":"mailbox-1","sourceRef":{"provider":"google","provider":"google","providerMessageId":"x"}}'):
            self.error(self.invoke(request(raw=body)), "source_invalid")
        self.error(self.invoke(request(raw=b" " * (http.MAX_REQUEST_BYTES + 1))), "payload_too_large")
        req = request(headers=[("Host", "app.cuevion.com"), ("Origin", "https://app.cuevion.com"), ("Content-Type", "application/json"), ("Content-Length", "2049")])
        req.rfile = Mock()
        self.error(self.invoke(req), "payload_too_large")
        req.rfile.read.assert_not_called()
        self.owned.assert_not_called()

    def test_extra_fields_and_invalid_versions_rejected(self):
        base = {"v": 1, "mailboxId": "mailbox-1", "sourceRef": GOOGLE}
        for key in ("userId", "workspaceId", "email", "credentials", "subject", "sender", "timestamp", "providerThreadId", "host"):
            self.error(self.invoke(request(raw=json.dumps({**base, key: "private"}).encode())), "source_invalid")
        for version in (True, 1.0, "1", 2, None):
            self.error(self.invoke(request(raw=json.dumps({**base, "v": version}).encode())), "source_invalid")
        self.owned.assert_not_called()

    def test_invalid_source_fields_and_identifiers_rejected(self):
        sources = [{**GOOGLE, "threadId": "x"}, {**GOOGLE, "providerMessageId": ""},
                   {**GOOGLE, "providerMessageId": " x"}, {**GOOGLE, "providerMessageId": "x\n"},
                   {**GOOGLE, "providerMessageId": "x" * 513}, {"provider": "other"}]
        for field in ("uidValidity", "imapUid"):
            sources.extend({**IMAP, field: value} for value in ("0", "01", "-1", "1.0", 1, "1\n", "9" * 21))
        sources += [{**IMAP, "folder": "Archive"}, {**IMAP, "imapUid": "4294967296"}]
        for source in sources:
            self.error(self.invoke(request(source)), "source_invalid")
        self.owned.assert_not_called()

    def test_rate_limit_prevents_provider_and_reports_retry_after(self):
        self.rate.return_value = OwnerRateLimitDecision("limited", 2)
        result = self.invoke()
        self.error(result, "rate_limited")
        self.assertIn(("Retry-After", "2"), result.headers)
        self.gmail.assert_not_called()

    def test_unavailable_limiter_fails_closed(self):
        self.rate.return_value = OwnerRateLimitDecision("unavailable")
        self.error(self.invoke(), "service_unavailable")
        self.gmail.assert_not_called()

    def test_safe_provider_errors_and_exceptions(self):
        for code in ("message_not_found", "source_changed", "invalid_response", "service_unavailable"):
            self.google_fetch.return_value = {"status": code, "error": "private-token-imap-host"}
            self.error(self.invoke(), code)
        self.google_fetch.return_value = {"status": "raw-private-error"}
        self.error(self.invoke(), "service_unavailable")
        self.google_fetch.side_effect = RuntimeError("private-token-imap-host")
        self.error(self.invoke(), "service_unavailable")

    def test_invalid_provider_public_projection_fails_closed(self):
        self.project.return_value = None
        self.error(self.invoke(), "invalid_response")

    def test_success_and_errors_are_no_store_nosniff(self):
        responses = [self.invoke()]
        self.owned.return_value = {"status": "not_found"}
        responses.append(self.invoke())
        for response in responses:
            headers = dict(response.headers)
            self.assertIn("no-store", headers["Cache-Control"])
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(int(headers["Content-Length"]), len(response.body))

    def test_oversized_public_result_is_rejected(self):
        self.project.return_value = {"body": "a" * http.MAX_RESPONSE_BYTES}
        self.error(self.invoke(), "invalid_response")


class ExactMessageAuthorityIntegrationTests(unittest.TestCase):
    """Exercise real mailbox/token/secret resolvers with local durable-store fakes."""

    def setUp(self):
        import base64
        from api import user_config_store as config
        from . import authenticated_gmail as gmail, authenticated_imap as imap
        self.config, self.gmail, self.imap = config, gmail, imap
        self.auth = self.enterContext(patch.object(runtime, "resolve_authenticated_member", return_value=runtime.AuthenticatedMemberResolution(runtime.MemberResolutionOutcome.AUTHENTICATED, MEMBER)))
        self.enterContext(patch.object(config, "resolve_user_config_store", return_value=({"rest_url": "local-fixture", "rest_token": "fixture"}, None)))
        self.mailbox = {"id": "mailbox-1", "provider": "google", "email": "mailbox@example.com", "connected": True, "connectionStatus": "connected"}
        self.read = self.enterContext(patch.object(config, "read_user_config_record", side_effect=lambda *_a: {"status": "ok", "config": {"email": MEMBER.email, "managedInboxes": [self.mailbox]}, "error": None}))
        self.token = self.enterContext(patch.object(gmail, "load_google_token_record_with_metadata", return_value=({"provider": "google", "email": "mailbox@example.com", "owner_email": MEMBER.email, "_storage_durable": True, "access_token": "fixture-token"}, None)))
        self.refresh = self.enterContext(patch.object(gmail, "refresh_google_token_record"))
        self.version = base64.urlsafe_b64encode(b"a" * 32).rstrip(b"=").decode()
        self.secret = self.enterContext(patch.object(imap, "read_mailbox_secret", return_value={"status": "present", "record": {"credentialVersion": self.version, "imapPassword": "fixture-password"}}))
        self.rate = self.enterContext(patch.object(http, "consume_exact_message_rate_limit", return_value=OwnerRateLimitDecision("allowed")))
        self.google_fetch = self.enterContext(patch.object(http.provider, "fetch_google_message", return_value={"status": "ok", "message": {"id": "canonical"}}))
        self.imap_fetch = self.enterContext(patch.object(http.provider, "fetch_imap_message", return_value={"status": "ok", "message": {"id": "canonical"}}))
        self.enterContext(patch.object(http.provider, "normalize_exact_message", return_value={"id": "canonical"}))

    def invoke(self, source=GOOGLE):
        return http.exact_message_response(request(source), environment={"VERCEL_ENV": "production"})

    def configure_imap(self):
        self.mailbox.update(provider="custom_imap", credentialVersion=self.version,
                            customImap={"host": "imap.example.com", "port": "993", "ssl": True, "username": "mailbox@example.com"},
                            customSmtp={"host": "smtp.example.com", "port": "465", "security": "ssl", "useSameCredentials": True})

    def test_google_auxiliary_counts_and_owner_bound_token(self):
        self.assertEqual(self.invoke().status, 200)
        self.assertEqual(self.auth.call_count, 1)
        self.assertEqual(self.read.call_count, 1)
        self.assertEqual(self.token.call_count, 1)
        self.token.assert_called_once_with("mailbox@example.com", owner_email=MEMBER.email)
        self.refresh.assert_not_called()
        self.secret.assert_not_called()
        self.google_fetch.assert_called_once()

    def test_imap_auxiliary_counts_and_current_secret_version(self):
        self.configure_imap()
        self.assertEqual(self.invoke(IMAP).status, 200)
        self.assertEqual(self.auth.call_count, 2)
        self.assertEqual(self.read.call_count, 2)
        self.secret.assert_called_once_with(MEMBER.email, "mailbox-1")
        self.token.assert_not_called()
        self.imap_fetch.assert_called_once()

    def test_foreign_account_mailbox_and_duplicate_records_fail_before_provider(self):
        for config_value in ({"email": "foreign@example.com", "managedInboxes": [self.mailbox]},
                             {"email": MEMBER.email, "managedInboxes": []},
                             {"email": MEMBER.email, "managedInboxes": [self.mailbox, self.mailbox]}):
            self.read.side_effect = None
            self.read.return_value = {"status": "ok", "config": config_value, "error": None}
            self.assertIn(self.invoke().status, (404, 503))
        self.token.assert_not_called()
        self.google_fetch.assert_not_called()

    def test_disconnected_mailbox_or_stale_credentials_fail_before_provider(self):
        self.mailbox["connected"] = False
        self.assertEqual(self.invoke().status, 503)
        self.token.assert_not_called()
        self.mailbox["connected"] = True
        self.configure_imap()
        self.secret.return_value["record"]["credentialVersion"] = "b" * 43
        self.assertEqual(self.invoke(IMAP).status, 503)
        self.imap_fetch.assert_not_called()

    def test_current_account_workspace_revalidated_before_config_access(self):
        self.auth.return_value = runtime.AuthenticatedMemberResolution(runtime.MemberResolutionOutcome.UNAUTHENTICATED, None)
        self.assertEqual(self.invoke().status, 401)
        self.read.assert_not_called()
        self.token.assert_not_called()
        self.google_fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
