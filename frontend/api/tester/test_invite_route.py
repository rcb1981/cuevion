from __future__ import annotations

import io
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from api.auth import http
from api.tester import invite


TOKEN = "tsti_" + "A" * 22 + "." + "B" * 43
INVITATION_ID = "tsti_" + "A" * 22
ADMIN = "usr_" + "A" * 21 + "A"


class Headers:
    def __init__(self, pairs):
        self._pairs = list(pairs)

    def raw_items(self):
        return iter(self._pairs)

    def get(self, name, default=None):
        values = [
            value for key, value in self._pairs
            if key.casefold() == name.casefold()
        ]
        return values[-1] if values else default


class FakeHttp:
    def __init__(self, path: str, payload: dict, *, origin=http.CANONICAL_APP_ORIGIN):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.command = "POST"
        self.path = path
        self.headers = Headers((
            ("host", "app.cuevion.com"),
            ("origin", origin),
            ("sec-fetch-site", "same-origin"),
            ("content-type", "application/json"),
            ("content-length", str(len(body))),
        ))
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status = None
        self.response_headers = []

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        pass

    def json(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


class FakeAuthority:
    def __init__(self, *, admin=True):
        self.admin = admin
        self.issue_calls = []
        self.cancel_calls = []
        self.lookup_calls = []

    def has_admin_authority(self, actor_user_id):
        return self.admin and actor_user_id == ADMIN

    def read_provisioning_invitation(self, token, *, allow_provisioned=False):
        self.lookup_calls.append((token, allow_provisioned))
        if token != TOKEN:
            raise AssertionError("unexpected token")
        return SimpleNamespace(
            display_name="Tester",
            status="invited",
            expires_at=1_900_000_000_000,
        )

    def issue_invitation(self, **kwargs):
        self.issue_calls.append(kwargs)
        return {
            "invite": {
                "invitationId": INVITATION_ID,
                "inviteeEmail": "tester@example.com",
                "inviteeName": "Tester",
                "status": "invited",
                "expiresAt": 1_900_000_000_000,
            },
            "rawToken": TOKEN,
        }

    def cancel_invitation(self, **kwargs):
        self.cancel_calls.append(kwargs)
        return {
            "invitationId": INVITATION_ID,
            "inviteeName": "Tester",
            "status": "cancelled",
            "expiresAt": 1_900_000_000_000,
        }


class TesterInviteRouteTests(unittest.TestCase):
    def _run(self, request, authority, *, actor=ADMIN):
        with (
            mock.patch.object(
                invite,
                "build_runtime_tester_invite_authority",
                return_value=authority,
            ),
            mock.patch.object(
                invite,
                "_authenticated_actor",
                return_value=actor,
            ) as authenticated,
        ):
            invite.handler.do_POST(request)
        return authenticated

    def test_public_lookup_uses_post_body_and_projects_no_secret_or_email(self):
        authority = FakeAuthority()
        request = FakeHttp("/api/tester/invite?op=lookup", {"token": TOKEN})
        authenticated = self._run(request, authority)

        self.assertEqual(request.status, 200)
        payload = request.json()
        self.assertEqual(
            payload,
            {
                "ok": True,
                "invite": {
                    "inviteeName": "Tester",
                    "status": "invited",
                    "expiresAt": 1_900_000_000_000,
                },
            },
        )
        self.assertNotIn(TOKEN, request.wfile.getvalue().decode("utf-8"))
        self.assertNotIn("tester@example.com", request.wfile.getvalue().decode("utf-8"))
        self.assertEqual(authority.lookup_calls, [(TOKEN, True)])
        authenticated.assert_not_called()

    def test_capability_is_authenticated_and_server_authoritative(self):
        authority = FakeAuthority()
        request = FakeHttp("/api/tester/invite?op=capability", {})
        authenticated = self._run(request, authority)

        self.assertEqual(request.status, 200)
        self.assertEqual(
            request.json(),
            {"ok": True, "canManageTesterAccess": True},
        )
        authenticated.assert_called_once()

        denied = FakeHttp("/api/tester/invite?op=capability", {})
        self._run(denied, FakeAuthority(admin=False))
        self.assertEqual(denied.status, 403)
        self.assertEqual(denied.json()["error"]["code"], "forbidden")

    def test_capability_requires_authenticated_session(self):
        authority = FakeAuthority()
        request = FakeHttp("/api/tester/invite?op=capability", {})
        self._run(request, authority, actor=None)
        self.assertNotEqual(request.status, 200)

    def test_issue_returns_fragment_url_and_uses_authenticated_admin_id(self):
        authority = FakeAuthority()
        request = FakeHttp(
            "/api/tester/invite?op=issue",
            {
                "inviteeEmail": "tester@example.com",
                "inviteeName": "Tester",
            },
        )
        authenticated = self._run(request, authority)

        self.assertEqual(request.status, 200)
        payload = request.json()
        self.assertEqual(
            payload["inviteUrl"],
            "https://app.cuevion.com/#tester_invite=" + TOKEN,
        )
        self.assertNotIn("?tester_invite=", payload["inviteUrl"])
        authenticated.assert_called_once()
        self.assertEqual(authority.issue_calls[0]["actor_user_id"], ADMIN)

    def test_cancel_uses_authenticated_admin_id(self):
        authority = FakeAuthority()
        request = FakeHttp(
            "/api/tester/invite?op=cancel",
            {"invitationId": INVITATION_ID},
        )
        self._run(request, authority)
        self.assertEqual(request.status, 200)
        self.assertEqual(
            authority.cancel_calls,
            [{"actor_user_id": ADMIN, "invitation_id": INVITATION_ID}],
        )

    def test_lookup_rejects_cross_site_before_authority(self):
        authority = FakeAuthority()
        request = FakeHttp(
            "/api/tester/invite?op=lookup",
            {"token": TOKEN},
            origin="https://evil.example",
        )
        self._run(request, authority)
        self.assertEqual(request.status, 403)
        self.assertEqual(authority.lookup_calls, [])

    def test_get_and_token_in_query_are_rejected(self):
        get_request = FakeHttp("/api/tester/invite?op=lookup", {"token": TOKEN})
        invite.handler.do_GET(get_request)
        self.assertEqual(get_request.status, 405)

        authority = FakeAuthority()
        query_request = FakeHttp(
            "/api/tester/invite?op=lookup&token=" + TOKEN,
            {"token": TOKEN},
        )
        self._run(query_request, authority)
        self.assertEqual(query_request.status, 400)
        self.assertEqual(authority.lookup_calls, [])


if __name__ == "__main__":
    unittest.main()
