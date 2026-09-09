from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from api.auth import runtime
from api.collaboration.owner_rate_limit import OwnerRateLimitDecision
from . import http


WORKSPACE = "wsp_" + "W" * 22
USER = "usr_" + "A" * 22
OTHER_USER = "usr_" + "B" * 21 + "A"
NOTIFICATION = "ntf_" + "a" * 40
NOW = 1_800_000_000
ENV = {"VERCEL_ENV": "production"}
REAL_MEMBER_RESOLVER = runtime.resolve_authenticated_member


def member(*, user_id=USER, workspace_id=WORKSPACE, role="owner"):
    return runtime.AuthenticatedMemberContext(
        user_id, "private@example.com", "Canonical Person", workspace_id, role,
    )


def authenticated(**kwargs):
    return runtime.AuthenticatedMemberResolution(
        runtime.MemberResolutionOutcome.AUTHENTICATED, member(**kwargs),
    )


def dto(**updates):
    value = {
        "v": 1, "notificationId": NOTIFICATION, "workspaceId": WORKSPACE,
        "kind": "shared_message", "collaborationId": "C" * 22,
        "mailboxId": "mailbox-1",
        "sourceRef": {"provider": "google", "providerMessageId": "exact-message"},
        "activityId": "M" * 22,
        "actor": {"type": "cuevion_user", "userId": OTHER_USER, "displayName": "Other"},
        "createdAt": NOW * 1000, "expiresAt": (NOW + 3600) * 1000,
        "readAt": None,
    }
    value.update(updates)
    return value


class Headers:
    def __init__(self, pairs):
        self.pairs = pairs

    def raw_items(self):
        return iter(self.pairs)


def request(payload=None, *, raw=None, method="POST", headers=None, path="/api/notifications"):
    body = json.dumps(payload or {"operation": "summary"}).encode() if raw is None else raw
    return SimpleNamespace(
        command=method, path=path,
        headers=Headers(headers if headers is not None else [
            ("Host", "app.cuevion.com"), ("Origin", "https://app.cuevion.com"),
            ("Content-Type", "application/json"), ("Content-Length", str(len(body))),
        ]),
        rfile=io.BytesIO(body),
    )


class NotificationHttpTests(unittest.TestCase):
    def setUp(self):
        self.auth = self.enterContext(patch.object(
            http.runtime, "resolve_authenticated_member", return_value=authenticated(),
        ))
        self.rate = self.enterContext(patch.object(
            http.rate_limit, "consume_notification_rate_limit",
            return_value=OwnerRateLimitDecision("allowed"),
        ))
        self.summary = self.enterContext(patch.object(
            http.store, "summary", return_value={"status": "ok", "v": 1, "unreadCount": 1},
        ))
        self.list = self.enterContext(patch.object(
            http.store, "list_notifications", return_value={
                "status": "ok", "v": 1, "unreadCount": 1,
                "notifications": [dto()], "nextCursor": None,
            },
        ))
        self.mark = self.enterContext(patch.object(
            http.store, "mark_read", return_value={
                "status": "ok", "v": 1, "unreadCount": 0,
                "notification": dto(readAt=NOW * 1000 + 100),
            },
        ))
        self.team = self.enterContext(patch.object(
            http.team_authority, "build_runtime_team_authority",
        ))
        self.team.return_value.resolve_active_member_by_user_id.return_value = (
            {"memberUserId": USER}, None,
        )

    def invoke(self, req=None):
        return http.notifications_response(req or request(), environment=ENV, now=NOW)

    def test_owner_has_neutral_summary_without_owner_allowlist_configuration(self):
        response = self.invoke()
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.body), {"v": 1, "unreadCount": 1})
        self.summary.assert_called_once_with(WORKSPACE, USER)
        self.team.assert_not_called()
        self.auth.assert_called_once_with(
            tuple(request().headers.pairs), environment=ENV, now=NOW,
        )

    def test_current_team_member_and_admin_can_read_their_own_notifications(self):
        for role in ("member", "admin"):
            with self.subTest(role=role):
                self.auth.return_value = authenticated(role=role)
                response = self.invoke()
                self.assertEqual(response.status, 200)
                self.team.return_value.resolve_active_member_by_user_id.assert_called_with(
                    workspace_id=WORKSPACE, member_user_id=USER,
                )

    def test_stale_removed_or_unavailable_team_members_cannot_read(self):
        self.auth.return_value = authenticated(role="member")
        for membership, error, status in (
            (None, {"code": "team_member_not_active"}, 403),
            (None, {"code": "team_store_unavailable"}, 503),
            ({"memberUserId": OTHER_USER}, None, 503),
        ):
            with self.subTest(status=status, membership=membership):
                self.team.return_value.resolve_active_member_by_user_id.return_value = membership, error
                self.assertEqual(self.invoke().status, status)
        self.summary.assert_not_called()
        self.rate.assert_not_called()

    def test_invalid_canonical_account_workspace_or_role_fails_closed(self):
        for kwargs, status in (
            ({"user_id": "external_guest"}, 503),
            ({"workspace_id": "email@example.com"}, 503),
            ({"role": "guest"}, 403),
        ):
            with self.subTest(kwargs=kwargs):
                self.auth.return_value = authenticated(**kwargs)
                self.assertEqual(self.invoke().status, status)
        self.summary.assert_not_called()

    def test_missing_revoked_account_and_auth_failure_do_not_read_body_or_store(self):
        for outcome, status in (
            (runtime.MemberResolutionOutcome.UNAUTHENTICATED, 401),
            (runtime.MemberResolutionOutcome.UNAVAILABLE, 503),
        ):
            self.auth.return_value = runtime.AuthenticatedMemberResolution(outcome, None)
            req = request()
            req.rfile = Mock()
            self.assertEqual(self.invoke(req).status, status)
            req.rfile.read.assert_not_called()
        self.summary.assert_not_called()

    def test_guest_cookie_alone_is_denied_by_the_real_generic_runtime(self):
        # Bypass our injected member resolver only; the real resolver sees no
        # Cuevion Auth0 cookie and never constructs an account/Redis adapter.
        from api.auth import session_store
        guest_req = request()
        guest_req.headers.pairs.append(("Cookie", "cuevion_guest_session=guest-bearer"))
        with patch.object(runtime, "resolve_authenticated_member", REAL_MEMBER_RESOLVER), patch.object(
                session_store, "build_runtime_session_store") as session_builder:
            self.assertEqual(self.invoke(guest_req).status, 401)
            session_builder.assert_not_called()
        self.summary.assert_not_called()

    def test_exact_host_origin_and_json_csrf_contract(self):
        base = request().headers.pairs
        cases = [
            [(name, value) for name, value in base if name != "Origin"],
            [(name, "https://evil.example" if name == "Origin" else value) for name, value in base],
            [(name, "evil.example" if name == "Host" else value) for name, value in base],
            base + [("X-Forwarded-Host", "evil.example")],
            base + [("X-Forwarded-Proto", "http")],
            [(name, "text/plain" if name == "Content-Type" else value) for name, value in base],
        ]
        for headers in cases:
            with self.subTest(headers=headers):
                self.assertIn(self.invoke(request(headers=headers)).status, {400, 403, 415})
        self.auth.assert_not_called()
        self.summary.assert_not_called()

    def test_ambiguous_security_headers_and_transfer_encoding_are_denied(self):
        base = request().headers.pairs
        for extra in (("Origin", "https://app.cuevion.com"), ("Host", "app.cuevion.com"),
                      ("Content-Length", "24"), ("Transfer-Encoding", "chunked"),
                      ("Cookie", "a=1")):
            headers = base + [extra]
            if extra[0] == "Cookie":
                headers += [("Cookie", "b=2")]
            with self.subTest(extra=extra):
                # The injected resolver skips cookie parsing; the shared strict
                # body boundary still rejects duplicate security cookie fields.
                self.assertEqual(self.invoke(request(headers=headers)).status, 400)
        self.summary.assert_not_called()

    def test_strict_body_rejects_authority_fields_on_every_operation(self):
        for operation in ("summary", "list", "mark_read"):
            for field in ("userId", "recipientUserId", "workspaceId", "mailboxId", "sourceRef",
                          "actor", "email", "key", "redisKey"):
                payload = {"operation": operation, field: "forged"}
                if operation == "mark_read":
                    payload["notificationId"] = NOTIFICATION
                with self.subTest(operation=operation, field=field):
                    self.assertEqual(self.invoke(request(payload)).status, 400)
        for backend in (self.summary, self.list, self.mark):
            backend.assert_not_called()

    def test_strict_json_duplicates_unicode_framing_and_size(self):
        for raw in (b'{"operation":"summary","operation":"list"}', b'[]',
                    b'{"operation":NaN}', b'\xef\xbb\xbf{}', b'\xff',
                    b'{"operation":"\\ud800"}', b'{' + b' ' * 2048 + b'}'):
            with self.subTest(raw=raw[:60]):
                self.assertIn(self.invoke(request(raw=raw)).status, {400, 413})
        req = request()
        req.headers.pairs = [(n, "2049" if n == "Content-Length" else v) for n, v in req.headers.pairs]
        req.rfile = Mock()
        self.assertEqual(self.invoke(req).status, 413)
        req.rfile.read.assert_not_called()
        self.summary.assert_not_called()

    def test_method_and_query_authority_injection_rejected(self):
        for method in ("GET", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "post"):
            self.assertEqual(self.invoke(request(method=method)).status, 405)
        self.assertEqual(self.invoke(request(path="/api/notifications?userId=forged")).status, 400)
        self.auth.assert_not_called()

    def test_list_and_mark_read_use_only_the_current_recipient_and_exact_id(self):
        response = self.invoke(request({"operation": "list", "limit": 20, "cursor": "opaque"}))
        self.assertEqual(response.status, 200)
        self.list.assert_called_once_with(WORKSPACE, USER, limit=20, cursor="opaque")
        body = json.loads(response.body)
        self.assertEqual(body["notifications"][0], dto())
        for field in ("recipientUserId", "body", "text", "email", "token"):
            self.assertNotIn(field, response.body.decode())
        response = self.invoke(request({"operation": "mark_read", "notificationId": NOTIFICATION}))
        self.assertEqual(response.status, 200)
        self.mark.assert_called_once_with(WORKSPACE, USER, NOTIFICATION)

    def test_page_limit_and_cursor_strictness(self):
        for limit in (0, 51, True, "50", 1.5, None):
            with self.subTest(limit=limit):
                self.assertEqual(self.invoke(request({"operation": "list", "limit": limit})).status, 400)
        for cursor in (1, False, {}, "", "x" * 1025, "é"):
            with self.subTest(cursor=str(cursor)[:20]):
                self.assertEqual(self.invoke(request({"operation": "list", "cursor": cursor})).status, 400)
        self.assertEqual(self.invoke(request({"operation": "list"})).status, 200)
        self.list.assert_called_once_with(WORKSPACE, USER, limit=50, cursor=None)

    def test_foreign_id_and_expired_id_are_not_found_without_payload(self):
        self.mark.return_value = {"status": "not_found"}
        response = self.invoke(request({"operation": "mark_read", "notificationId": NOTIFICATION}))
        self.assertEqual(response.status, 404)
        self.assertNotIn("notification", json.loads(response.body))

    def test_rate_denial_and_storage_failure_are_safe_and_do_not_log(self):
        self.rate.return_value = OwnerRateLimitDecision("limited", 2)
        response = self.invoke()
        self.assertEqual(response.status, 429)
        self.assertIn(("Retry-After", "2"), response.headers)
        self.summary.assert_not_called()
        self.rate.return_value = OwnerRateLimitDecision("unavailable")
        self.assertEqual(self.invoke().status, 503)
        self.rate.return_value = OwnerRateLimitDecision("allowed")
        self.summary.side_effect = RuntimeError("raw redis key / session / secret")
        response = self.invoke()
        self.assertEqual(response.status, 503)
        self.assertNotIn(b"secret", response.body)

    def test_store_cannot_leak_recipient_or_bad_output_or_nonadvancing_cursor(self):
        for rows, next_cursor in (
            ([{**dto(), "recipientUserId": USER}], None),
            ([dto(workspaceId="wsp_" + "X" * 22)], None),
            ([dto(), dto()], None),
            ([dto()], "same"), ([], "next"),
        ):
            self.list.return_value = {
                "status": "ok", "v": 1, "unreadCount": 1,
                "notifications": rows, "nextCursor": next_cursor,
            }
            self.assertEqual(self.invoke(request({"operation": "list", "cursor": "same"})).status, 503)

    def test_every_response_is_no_store_and_nosniff(self):
        for req in (request(), request(method="GET"), request(raw=b"bad")):
            response = self.invoke(req)
            self.assertIn(("Cache-Control", "no-store"), response.headers)
            self.assertIn(("X-Content-Type-Options", "nosniff"), response.headers)

    def test_handler_has_no_logging_and_routes_every_method_through_security(self):
        path = Path(__file__).resolve().parents[1] / "notifications.py"
        spec = importlib.util.spec_from_file_location("notification_http_route_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        req = request()
        req.send_response_only = Mock()
        req.send_header = Mock()
        req.end_headers = Mock()
        req.wfile = io.BytesIO()
        with patch.object(module, "notifications_response", return_value=self.invoke()):
            module.handler.do_POST(req)
        req.send_response_only.assert_called_once_with(200)
        self.assertEqual(module.handler.log_message(req, "secret %s", "private"), None)
