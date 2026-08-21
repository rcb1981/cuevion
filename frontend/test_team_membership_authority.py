from __future__ import annotations

import base64
import importlib
import io
import json
import os
import re
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from api.auth.runtime import (
    AuthenticatedMemberContext,
    AuthenticatedMemberResolution,
    MemberResolutionOutcome,
)
from api.team import invite as team_invite
from api.team import members as team_members


NOW_MS = 1_800_000_000_000
SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000
RAW_TOKEN = "tinv_test.AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE"
TOKEN_DIGEST = "4" * 64

PENDING_INVITATION = {
    "id": "tinv_test",
    "inviteeEmail": "recipient@example.test",
    "displayName": "Recipient",
    "accessLevel": "Limited",
    "status": "invited",
    "expiresAt": NOW_MS + SEVEN_DAYS_MS,
}
PUBLIC_INVITATION = {
    "displayName": "Recipient",
    "accessLevel": "Limited",
    "status": "invited",
    "expiresAt": NOW_MS + SEVEN_DAYS_MS,
}
ACTIVE_MEMBER = {
    "email": "recipient@example.test",
    "displayName": "Recipient",
    "accessLevel": "Limited",
    "status": "active",
}


class HeaderMap(dict):
    def raw_items(self):
        return iter(list(self.items()))


class FakeHandler:
    def __init__(
        self,
        path: str,
        body: bytes = b"",
        *,
        headers: dict[str, str | None] | None = None,
    ):
        self.path = path
        request_headers: dict[str, str] = {
            "content-length": str(len(body)),
            "content-type": "application/json",
            "host": "app.cuevion.com",
            "origin": "https://app.cuevion.com",
        }
        for header_name, header_value in (headers or {}).items():
            for existing_name in tuple(request_headers):
                if existing_name.lower() == header_name.lower():
                    del request_headers[existing_name]
            if header_value is not None:
                request_headers[header_name] = header_value
        self.headers = HeaderMap(request_headers)
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status: int | None = None
        self.response_headers: list[tuple[str, str]] = []

    def send_response(self, status: int, _message=None):
        self.status = status

    def send_response_only(self, status: int, _message=None):
        self.status = status

    def send_header(self, name: str, value: str):
        self.response_headers.append((name, value))

    def end_headers(self):
        return None

    def payload(self) -> dict:
        return json.loads(self.wfile.getvalue() or b"{}")


def owner(
    workspace_id: str = "workspace-a",
    *,
    email: str = "owner@example.test",
) -> AuthenticatedMemberContext:
    return AuthenticatedMemberContext(
        user_id="user-owner",
        email=email,
        name="Owner",
        workspace_id=workspace_id,
        membership_role="owner",
    )


def ordinary_member(
    workspace_id: str = "workspace-a",
    *,
    email: str = "member@example.test",
) -> AuthenticatedMemberContext:
    return AuthenticatedMemberContext(
        user_id="user-member",
        email=email,
        name="Member",
        workspace_id=workspace_id,
        membership_role="member",
    )


def authenticated(member: AuthenticatedMemberContext) -> AuthenticatedMemberResolution:
    return AuthenticatedMemberResolution(
        MemberResolutionOutcome.AUTHENTICATED,
        member,
    )


def unauthenticated() -> AuthenticatedMemberResolution:
    return AuthenticatedMemberResolution(
        MemberResolutionOutcome.UNAUTHENTICATED,
        None,
    )


def authority_error(code: str, message: str = "Rejected by Team authority.") -> dict:
    return {"code": code, "message": message}


class FakeTeamAuthority:
    """Route-facing fake for the proposed narrow api.team.authority boundary."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.errors: dict[str, dict] = {}

    def _result(self, operation: str, value: object, kwargs: dict):
        self.calls.append((operation, kwargs))
        error = self.errors.get(operation)
        return (None, error) if error else (value, None)

    def issue_invitation(self, **kwargs):
        return self._result(
            "issue",
            {"invite": PENDING_INVITATION, "rawToken": RAW_TOKEN},
            kwargs,
        )

    def lookup_invitation(self, **kwargs):
        return self._result("lookup", PUBLIC_INVITATION, kwargs)

    def list_pending_invitations(self, **kwargs):
        return self._result("pending", [PENDING_INVITATION], kwargs)

    def accept_invitation(self, **kwargs):
        return self._result(
            "accept",
            {"invite": {**PUBLIC_INVITATION, "status": "accepted"}, "member": ACTIVE_MEMBER},
            kwargs,
        )

    def decline_invitation(self, **kwargs):
        return self._result(
            "decline",
            {"invite": {**PUBLIC_INVITATION, "status": "declined"}},
            kwargs,
        )

    def cancel_invitation(self, **kwargs):
        return self._result(
            "cancel",
            {"invite": {**PENDING_INVITATION, "status": "cancelled"}},
            kwargs,
        )

    def remove_member(self, **kwargs):
        return self._result(
            "remove",
            {
                "email": kwargs.get("member_email", "recipient@example.test"),
                "status": "removed",
                "removedAt": NOW_MS,
            },
            kwargs,
        )

    def update_member_access(self, **kwargs):
        return self._result(
            "update_access",
            {**ACTIVE_MEMBER, "accessLevel": kwargs.get("access_level", "Shared")},
            kwargs,
        )


def invoke_invite(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    resolution: AuthenticatedMemberResolution | None = None,
    authority: FakeTeamAuthority | None = None,
    headers: dict[str, str | None] | None = None,
    environment: dict[str, str] | None = None,
):
    encoded_body = json.dumps(body).encode("utf-8") if body is not None else b""
    request = FakeHandler(path, encoded_body, headers=headers)
    resolver = Mock(return_value=resolution or unauthenticated())
    runtime_authority = authority or FakeTeamAuthority()
    authority_factory = Mock(return_value=runtime_authority)

    with patch.dict(os.environ, environment or {}, clear=True), patch.object(
        team_invite,
        "resolve_authenticated_member",
        resolver,
        create=True,
    ), patch.object(
        team_invite,
        "build_runtime_team_authority",
        authority_factory,
        create=True,
    ):
        getattr(team_invite.handler, f"do_{method}")(request)

    return request, resolver, authority_factory, runtime_authority


def invoke_members(
    path: str,
    *,
    body: dict,
    resolution: AuthenticatedMemberResolution,
    authority: FakeTeamAuthority | None = None,
    headers: dict[str, str | None] | None = None,
):
    request = FakeHandler(
        path,
        json.dumps(body).encode("utf-8"),
        headers=headers,
    )
    resolver = Mock(return_value=resolution)
    runtime_authority = authority or FakeTeamAuthority()
    authority_factory = Mock(return_value=runtime_authority)

    with patch.dict(os.environ, {}, clear=True), patch.object(
        team_members,
        "resolve_authenticated_member",
        resolver,
    ), patch.object(
        team_members,
        "build_runtime_team_authority",
        authority_factory,
        create=True,
    ):
        team_members.handler.do_POST(request)

    return request, resolver, authority_factory, runtime_authority


class TeamMutationHttpBoundaryTests(unittest.TestCase):
    REJECTED_HEADERS = (
        ("simple text body", {"content-type": "text/plain"}, 415),
        ("missing content type", {"content-type": None}, 400),
        ("foreign origin", {"origin": "https://attacker.example"}, 403),
        ("missing origin", {"origin": None}, 400),
        ("foreign host", {"host": "attacker.example"}, 403),
    )

    def test_invitation_mutations_require_authenticated_same_origin_json_before_body(self):
        cases = (
            (
                "issue",
                {"inviteeEmail": "recipient@example.test", "inviteeName": "Recipient", "accessLevel": "Limited"},
                authenticated(owner()),
            ),
            (
                "action",
                {"action": {"type": "accept"}},
                authenticated(ordinary_member("recipient-home", email="recipient@example.test")),
            ),
            (
                "cancel",
                {"invitationId": "tinv_test"},
                authenticated(owner()),
            ),
        )
        for operation, body, resolution in cases:
            for label, headers, expected_status in self.REJECTED_HEADERS:
                with self.subTest(operation=operation, boundary=label):
                    request, resolver, factory, authority = invoke_invite(
                        "POST",
                        f"/api/team/invite?op={operation}&token={RAW_TOKEN}",
                        body=body,
                        resolution=resolution,
                        headers=headers,
                    )
                    self.assertEqual(request.status, expected_status)
                    self.assertFalse(request.payload()["ok"])
                    self.assertEqual(request.rfile.tell(), 0)
                    resolver.assert_called_once()
                    factory.assert_not_called()
                    self.assertEqual(authority.calls, [])

    def test_member_mutations_require_authenticated_same_origin_json_before_body(self):
        cases = (
            ("remove", {"memberEmail": "recipient@example.test"}),
            ("revoke", {"memberEmail": "recipient@example.test"}),
            (
                "update-access",
                {"memberEmail": "recipient@example.test", "accessLevel": "Shared"},
            ),
        )
        for operation, body in cases:
            for label, headers, expected_status in self.REJECTED_HEADERS:
                with self.subTest(operation=operation, boundary=label):
                    request, resolver, factory, authority = invoke_members(
                        f"/api/team/members?op={operation}",
                        body=body,
                        resolution=authenticated(owner()),
                        headers=headers,
                    )
                    self.assertEqual(request.status, expected_status)
                    self.assertFalse(request.payload()["ok"])
                    self.assertEqual(request.rfile.tell(), 0)
                    resolver.assert_called_once()
                    factory.assert_not_called()
                    self.assertEqual(authority.calls, [])


class TeamInvitationIssueRouteTests(unittest.TestCase):
    def valid_body(self, **overrides) -> dict:
        body = {
            "inviteeEmail": "recipient@example.test",
            "inviteeName": "Recipient",
            "accessLevel": "Limited",
        }
        body.update(overrides)
        return body

    def test_unauthenticated_issue_is_rejected_before_authority_storage(self):
        request, resolver, factory, authority = invoke_invite(
            "POST",
            "/api/team/invite?op=issue",
            body=self.valid_body(),
            resolution=unauthenticated(),
        )

        self.assertEqual(request.status, 401)
        self.assertEqual(request.payload()["error"]["code"], "unauthorized")
        resolver.assert_called_once()
        factory.assert_not_called()
        self.assertEqual(authority.calls, [])

    def test_unauthenticated_mutation_is_rejected_before_body_parsing(self):
        for operation in ("issue", "action", "cancel"):
            with self.subTest(operation=operation):
                request = FakeHandler(
                    f"/api/team/invite?op={operation}",
                    b"not-json-and-must-remain-unread",
                )
                with patch.object(
                    team_invite,
                    "resolve_authenticated_member",
                    return_value=unauthenticated(),
                ), patch.object(
                    team_invite,
                    "_read_json_body",
                    side_effect=AssertionError("unauthenticated request parsed its body"),
                ):
                    team_invite.handler.do_POST(request)

                self.assertEqual(request.status, 401)
                self.assertEqual(
                    request.rfile.read(),
                    b"not-json-and-must-remain-unread",
                )

    def test_non_owner_cannot_issue(self):
        request, _resolver, factory, authority = invoke_invite(
            "POST",
            "/api/team/invite?op=issue",
            body=self.valid_body(),
            resolution=authenticated(ordinary_member()),
        )

        self.assertEqual(request.status, 403)
        self.assertEqual(request.payload()["error"]["code"], "forbidden")
        factory.assert_not_called()
        self.assertEqual(authority.calls, [])

    def test_owner_issue_uses_only_canonical_session_actor_and_returns_token_once(self):
        canonical_owner = owner("workspace-a")
        body = self.valid_body()
        request, _resolver, _factory, authority = invoke_invite(
            "POST",
            "/api/team/invite?op=issue",
            body=body,
            resolution=authenticated(canonical_owner),
        )

        self.assertEqual(request.status, 200)
        self.assertEqual(authority.calls[0][0], "issue")
        call = authority.calls[0][1]
        self.assertIs(call["actor"], canonical_owner)
        self.assertEqual(call["invitee_email"], body["inviteeEmail"])
        self.assertEqual(call["invitee_name"], body["inviteeName"])
        self.assertEqual(call["access_level"], body["accessLevel"])
        self.assertNotIn("workspace_id", call)
        self.assertNotIn("created_by_user_id", call)

        payload = request.payload()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["invite"], PENDING_INVITATION)
        self.assertIn(RAW_TOKEN, payload["inviteUrl"])
        self.assertNotIn("token", json.dumps(payload["invite"]).lower())
        self.assertEqual(json.dumps(payload).count(RAW_TOKEN), 1)

    def test_production_invite_url_ignores_request_host_authority(self):
        request, _resolver, _factory, _authority = invoke_invite(
            "POST",
            "/api/team/invite?op=issue",
            body=self.valid_body(),
            resolution=authenticated(owner()),
        )
        request.headers["host"] = "attacker.example"
        request.headers["x-forwarded-host"] = "attacker.example"

        with patch.dict(os.environ, {"VERCEL_ENV": "production"}, clear=True):
            self.assertEqual(
                team_invite._build_invite_url(request, token=RAW_TOKEN),
                f"https://app.cuevion.com/?team_invite={RAW_TOKEN}",
            )

    def test_preview_issue_uses_validated_same_origin_url_without_configuration(self):
        preview_origin = "https://cuevion-preview-123.vercel.app"
        request, _resolver, _factory, authority = invoke_invite(
            "POST",
            "/api/team/invite?op=issue",
            body=self.valid_body(),
            resolution=authenticated(owner()),
            headers={
                "host": "cuevion-preview-123.vercel.app",
                "origin": preview_origin,
                "x-forwarded-host": "cuevion-preview-123.vercel.app",
                "x-forwarded-proto": "https",
            },
            environment={"VERCEL_ENV": "preview"},
        )

        self.assertEqual(request.status, 200)
        self.assertEqual(
            request.payload()["inviteUrl"],
            f"{preview_origin}/?team_invite={RAW_TOKEN}",
        )
        self.assertEqual(authority.calls[0][0], "issue")

    def test_development_issue_allows_only_loopback_cleartext_origins(self):
        local_origin = "http://localhost:3000"
        local, _resolver, _factory, local_authority = invoke_invite(
            "POST",
            "/api/team/invite?op=issue",
            body=self.valid_body(),
            resolution=authenticated(owner()),
            headers={"host": "localhost:3000", "origin": local_origin},
            environment={"VERCEL_ENV": "development"},
        )
        self.assertEqual(local.status, 200)
        self.assertEqual(
            local.payload()["inviteUrl"],
            f"{local_origin}/?team_invite={RAW_TOKEN}",
        )
        self.assertEqual(local_authority.calls[0][0], "issue")

        remote_origin = "http://dev.example"
        rejected_cases = (
            ({"VERCEL_ENV": "development"}, 403),
            (
                {
                    "VERCEL_ENV": "development",
                    "CUEVION_APP_URL": remote_origin,
                },
                400,
            ),
        )
        for environment, expected_status in rejected_cases:
            with self.subTest(environment=environment):
                request, _resolver, factory, authority = invoke_invite(
                    "POST",
                    "/api/team/invite?op=issue",
                    body=self.valid_body(),
                    resolution=authenticated(owner()),
                    headers={"host": "dev.example", "origin": remote_origin},
                    environment=environment,
                )
                self.assertEqual(request.status, expected_status)
                self.assertEqual(request.rfile.tell(), 0)
                factory.assert_not_called()
                self.assertEqual(authority.calls, [])

    def test_client_cannot_select_foreign_workspace_or_forge_inviter(self):
        body = self.valid_body(
            workspaceId="workspace-b",
            createdByUserId="forged-user",
            createdByUserName="Forged Owner",
        )
        request, _resolver, factory, authority = invoke_invite(
            "POST",
            "/api/team/invite?op=issue",
            body=body,
            resolution=authenticated(owner("workspace-a")),
        )

        self.assertEqual(request.status, 403)
        self.assertEqual(request.payload()["error"]["code"], "forbidden")
        factory.assert_not_called()
        self.assertEqual(authority.calls, [])

    def test_invalid_email_role_and_self_invite_are_rejected_before_storage(self):
        cases = (
            (self.valid_body(inviteeEmail="not-an-email"), 400),
            (self.valid_body(inviteeEmail={"address": "recipient@example.test"}), 400),
            (self.valid_body(inviteeName={"name": "Recipient"}), 400),
            (self.valid_body(inviteeName=["Recipient"]), 400),
            (self.valid_body(inviteeName=123), 400),
            (self.valid_body(inviteeName=True), 400),
            (self.valid_body(accessLevel="Admin"), 400),
            (self.valid_body(accessLevel="Review"), 400),
            (self.valid_body(inviteeEmail="OWNER@EXAMPLE.TEST"), 409),
        )
        for body, expected_status in cases:
            with self.subTest(body=body):
                request, _resolver, factory, authority = invoke_invite(
                    "POST",
                    "/api/team/invite?op=issue",
                    body=body,
                    resolution=authenticated(owner()),
                )
                self.assertEqual(request.status, expected_status)
                factory.assert_not_called()
                self.assertEqual(authority.calls, [])

    def test_existing_member_and_duplicate_live_invitation_are_conflicts_without_old_token(self):
        for error_code in ("team_member_exists", "live_invitation_exists"):
            with self.subTest(error_code=error_code):
                authority = FakeTeamAuthority()
                authority.errors["issue"] = authority_error(error_code)
                request, _resolver, _factory, called_authority = invoke_invite(
                    "POST",
                    "/api/team/invite?op=issue",
                    body=self.valid_body(),
                    resolution=authenticated(owner()),
                    authority=authority,
                )

                self.assertEqual(request.status, 409)
                self.assertFalse(request.payload()["ok"])
                self.assertEqual(request.payload()["error"]["code"], error_code)
                self.assertNotIn("token", json.dumps(request.payload()).lower())
                self.assertEqual(called_authority.calls[0][0], "issue")


class TeamInvitationReadRouteTests(unittest.TestCase):
    def test_public_lookup_is_redacted_and_does_not_require_workspace_authority(self):
        request, _resolver, _factory, authority = invoke_invite(
            "GET",
            f"/api/team/invite?op=lookup&token={RAW_TOKEN}",
            resolution=unauthenticated(),
        )

        self.assertEqual(request.status, 200)
        self.assertEqual(request.payload(), {"ok": True, "invite": PUBLIC_INVITATION})
        self.assertEqual(authority.calls[0], ("lookup", {"token": RAW_TOKEN}))
        serialized = json.dumps(request.payload())
        for secret_field in (
            "token",
            "digest",
            "workspaceId",
            "createdByUserId",
            "session",
            "storageKey",
        ):
            self.assertNotIn(secret_field.lower(), serialized.lower())

    def test_pending_projection_is_owner_only_and_session_workspace_scoped(self):
        request, _resolver, _factory, authority = invoke_invite(
            "GET",
            "/api/team/invite?op=pending",
            resolution=authenticated(owner("workspace-a")),
        )

        self.assertEqual(request.status, 200)
        self.assertEqual(request.payload(), {"ok": True, "invitations": [PENDING_INVITATION]})
        self.assertEqual(authority.calls[0][1]["actor"].workspace_id, "workspace-a")
        serialized = json.dumps(request.payload())
        self.assertNotIn("token", serialized.lower())
        self.assertNotIn("workspace", serialized.lower())

        forbidden, _resolver, factory, forbidden_authority = invoke_invite(
            "GET",
            "/api/team/invite?op=pending",
            resolution=authenticated(ordinary_member()),
        )
        self.assertEqual(forbidden.status, 403)
        factory.assert_not_called()
        self.assertEqual(forbidden_authority.calls, [])

    def test_foreign_workspace_query_cannot_select_another_pending_roster(self):
        request, _resolver, factory, authority = invoke_invite(
            "GET",
            "/api/team/invite?op=pending&workspaceId=workspace-b",
            resolution=authenticated(owner("workspace-a")),
        )
        self.assertEqual(request.status, 403)
        factory.assert_not_called()
        self.assertEqual(authority.calls, [])


class TeamInvitationRecipientTransitionRouteTests(unittest.TestCase):
    def invoke_action(
        self,
        action: str,
        *,
        member: AuthenticatedMemberContext,
        authority: FakeTeamAuthority | None = None,
        extra_action: dict | None = None,
    ):
        action_payload = {"type": action}
        if extra_action:
            action_payload.update(extra_action)
        return invoke_invite(
            "POST",
            f"/api/team/invite?op=action&token={RAW_TOKEN}",
            body={"action": action_payload},
            resolution=authenticated(member),
            authority=authority,
        )

    def test_accept_uses_verified_session_recipient_and_ignores_forged_body_identity(self):
        recipient = ordinary_member("recipient-home", email="recipient@example.test")
        request, _resolver, _factory, authority = invoke_invite(
            "POST",
            f"/api/team/invite?op=action&token={RAW_TOKEN}",
            body={
                "action": {"type": "accept"},
                "recipientEmail": "attacker@example.test",
                "workspaceId": "workspace-a",
            },
            resolution=authenticated(recipient),
        )

        self.assertEqual(request.status, 200)
        self.assertEqual(authority.calls[0][0], "accept")
        self.assertIs(authority.calls[0][1]["actor"], recipient)
        self.assertEqual(authority.calls[0][1]["token"], RAW_TOKEN)
        self.assertNotIn("recipient_email", authority.calls[0][1])
        self.assertNotIn("workspace_id", authority.calls[0][1])
        self.assertEqual(request.payload()["member"], ACTIVE_MEMBER)

    def test_accept_and_decline_require_authentication(self):
        for action in ("accept", "decline"):
            with self.subTest(action=action):
                request, _resolver, factory, authority = invoke_invite(
                    "POST",
                    f"/api/team/invite?op=action&token={RAW_TOKEN}",
                    body={"action": {"type": action}},
                    resolution=unauthenticated(),
                )
                self.assertEqual(request.status, 401)
                factory.assert_not_called()
                self.assertEqual(authority.calls, [])

    def test_decline_uses_verified_session_recipient(self):
        recipient = ordinary_member("recipient-home", email="recipient@example.test")
        request, _resolver, _factory, authority = self.invoke_action(
            "decline",
            member=recipient,
        )
        self.assertEqual(request.status, 200)
        self.assertEqual(authority.calls[0][0], "decline")
        self.assertIs(authority.calls[0][1]["actor"], recipient)
        self.assertEqual(request.payload()["invite"]["status"], "declined")

    def test_wrong_recipient_expiry_and_terminal_states_fail_closed(self):
        cases = (
            ("wrong_recipient", 403),
            ("expired_invite", 410),
            ("cancelled_invite", 409),
            ("declined_invite", 409),
            ("used_invite", 409),
        )
        recipient = ordinary_member("recipient-home", email="recipient@example.test")
        for error_code, expected_status in cases:
            with self.subTest(error_code=error_code):
                authority = FakeTeamAuthority()
                authority.errors["accept"] = authority_error(error_code)
                request, _resolver, _factory, _authority = self.invoke_action(
                    "accept",
                    member=recipient,
                    authority=authority,
                )
                self.assertEqual(request.status, expected_status)
                self.assertEqual(request.payload()["error"]["code"], error_code)
                self.assertNotIn("member", request.payload())

    def test_cancel_is_owner_only_same_workspace_and_uses_safe_invitation_id(self):
        action = {"type": "cancel", "invitationId": "tinv_test"}
        request, _resolver, _factory, authority = invoke_invite(
            "POST",
            "/api/team/invite?op=action",
            body={"action": action},
            resolution=authenticated(owner("workspace-a")),
        )
        self.assertEqual(request.status, 200)
        self.assertEqual(authority.calls[0][0], "cancel")
        self.assertEqual(authority.calls[0][1]["invitation_id"], "tinv_test")
        self.assertNotIn("token", authority.calls[0][1])

        non_owner, _resolver, factory, non_owner_authority = invoke_invite(
            "POST",
            "/api/team/invite?op=action",
            body={"action": action},
            resolution=authenticated(ordinary_member("workspace-a")),
        )
        self.assertEqual(non_owner.status, 403)
        factory.assert_not_called()
        self.assertEqual(non_owner_authority.calls, [])

        foreign, _resolver, factory, foreign_authority = invoke_invite(
            "POST",
            "/api/team/invite?op=action",
            body={"workspaceId": "workspace-b", "action": action},
            resolution=authenticated(owner("workspace-a")),
        )
        self.assertEqual(foreign.status, 403)
        factory.assert_not_called()
        self.assertEqual(foreign_authority.calls, [])


class TeamMemberMutationRouteTests(unittest.TestCase):
    def test_owner_remove_uses_session_workspace_without_client_authority(self):
        canonical_owner = owner("workspace-a")
        request, _resolver, _factory, authority = invoke_members(
            "/api/team/members?op=remove",
            body={"memberEmail": "recipient@example.test"},
            resolution=authenticated(canonical_owner),
        )

        self.assertEqual(request.status, 200)
        self.assertEqual(authority.calls[0][0], "remove")
        self.assertIs(authority.calls[0][1]["actor"], canonical_owner)
        self.assertEqual(authority.calls[0][1]["member_email"], "recipient@example.test")
        self.assertNotIn("workspace_id", authority.calls[0][1])

    def test_owner_can_target_historical_v1_non_email_identifier(self):
        remove, _resolver, _factory, remove_authority = invoke_members(
            "/api/team/members?op=remove",
            body={"memberEmail": " Legacy-Recipient "},
            resolution=authenticated(owner()),
        )
        update, _resolver, _factory, update_authority = invoke_members(
            "/api/team/members?op=update-access",
            body={
                "memberEmail": " Legacy-Recipient ",
                "accessLevel": "Shared",
            },
            resolution=authenticated(owner()),
        )

        self.assertEqual(remove.status, 200)
        self.assertEqual(
            remove_authority.calls[0][1]["member_email"],
            "legacy-recipient",
        )
        self.assertEqual(update.status, 200)
        self.assertEqual(
            update_authority.calls[0][1]["member_email"],
            "legacy-recipient",
        )

        for member_identifier in ("", "   ", [], {}):
            with self.subTest(member_identifier=member_identifier):
                request, _resolver, factory, authority = invoke_members(
                    "/api/team/members?op=remove",
                    body={"memberEmail": member_identifier},
                    resolution=authenticated(owner()),
                )
                self.assertEqual(request.status, 400)
                factory.assert_not_called()
                self.assertEqual(authority.calls, [])

    def test_remove_rejects_non_owner_and_foreign_workspace_before_storage(self):
        cases = (
            (
                "/api/team/members?op=remove",
                {"memberEmail": "recipient@example.test"},
                authenticated(ordinary_member("workspace-a")),
                403,
            ),
            (
                "/api/team/members?op=remove",
                {"workspaceId": "workspace-b", "memberEmail": "recipient@example.test"},
                authenticated(owner("workspace-a")),
                403,
            ),
            (
                "/api/team/members?op=remove&workspaceId=workspace-b",
                {"memberEmail": "recipient@example.test"},
                authenticated(owner("workspace-a")),
                403,
            ),
            (
                "/api/team/members?op=remove",
                {"workspaceId": "", "memberEmail": "recipient@example.test"},
                authenticated(owner("workspace-a")),
                400,
            ),
        )
        for path, body, resolution, expected_status in cases:
            with self.subTest(path=path, body=body):
                request, _resolver, factory, authority = invoke_members(
                    path,
                    body=body,
                    resolution=resolution,
                )
                self.assertEqual(request.status, expected_status)
                factory.assert_not_called()
                self.assertEqual(authority.calls, [])

    def test_access_update_is_owner_only_and_allows_only_shared_or_limited(self):
        for access_level in ("Shared", "Limited"):
            with self.subTest(access_level=access_level):
                request, _resolver, _factory, authority = invoke_members(
                    "/api/team/members?op=update-access",
                    body={
                        "memberEmail": "recipient@example.test",
                        "accessLevel": access_level,
                    },
                    resolution=authenticated(owner()),
                )
                self.assertEqual(request.status, 200)
                self.assertEqual(authority.calls[0][0], "update_access")
                self.assertEqual(authority.calls[0][1]["access_level"], access_level)

        for access_level in ("Admin", "Review", "Editor", ""):
            with self.subTest(access_level=access_level):
                request, _resolver, factory, authority = invoke_members(
                    "/api/team/members?op=update-access",
                    body={
                        "memberEmail": "recipient@example.test",
                        "accessLevel": access_level,
                    },
                    resolution=authenticated(owner()),
                )
                self.assertEqual(request.status, 400)
                factory.assert_not_called()
                self.assertEqual(authority.calls, [])

        forbidden, _resolver, factory, authority = invoke_members(
            "/api/team/members?op=update-access",
            body={"memberEmail": "recipient@example.test", "accessLevel": "Shared"},
            resolution=authenticated(ordinary_member()),
        )
        self.assertEqual(forbidden.status, 403)
        factory.assert_not_called()
        self.assertEqual(authority.calls, [])

    def test_access_update_rejects_foreign_or_empty_workspace_before_storage(self):
        cases = (
            (
                "/api/team/members?op=update-access",
                "workspace-b",
                403,
            ),
            (
                "/api/team/members?op=update-access&workspaceId=workspace-b",
                None,
                403,
            ),
            (
                "/api/team/members?op=update-access",
                "",
                400,
            ),
        )
        for path, workspace_id, expected_status in cases:
            body = {
                "memberEmail": "recipient@example.test",
                "accessLevel": "Shared",
            }
            if workspace_id is not None:
                body["workspaceId"] = workspace_id
            with self.subTest(path=path, workspace_id=workspace_id):
                request, _resolver, factory, authority = invoke_members(
                    path,
                    body=body,
                    resolution=authenticated(owner("workspace-a")),
                )
                self.assertEqual(request.status, expected_status)
                factory.assert_not_called()
                self.assertEqual(authority.calls, [])


class TeamAuthorityStorageContractTests(unittest.TestCase):
    @staticmethod
    def load_authority_module():
        try:
            return importlib.import_module("api.team.authority")
        except ModuleNotFoundError as error:
            raise AssertionError(
                "api.team.authority must provide the production Team authority boundary"
            ) from error

    def test_token_has_32_random_bytes_and_only_digest_is_persistable(self):
        authority = self.load_authority_module()
        self.assertEqual(authority.TEAM_INVITE_TOKEN_BYTES, 32)

        requested_sizes: list[int] = []

        def fixed_random_bytes(size: int) -> bytes:
            requested_sizes.append(size)
            return b"\x01" * size

        raw_token, token_digest = authority.generate_invitation_token(
            "tinv_test",
            random_bytes=fixed_random_bytes,
        )
        self.assertEqual(requested_sizes, [32])
        invitation_id, encoded_secret = raw_token.split(".", 1)
        self.assertEqual(invitation_id, "tinv_test")
        decoded_secret = base64.urlsafe_b64decode(
            encoded_secret + "=" * (-len(encoded_secret) % 4)
        )
        self.assertEqual(decoded_secret, b"\x01" * 32)
        self.assertRegex(token_digest, r"\A[0-9a-f]{64}\Z")
        self.assertNotIn(encoded_secret, token_digest)
        self.assertTrue(authority.verify_invitation_token(raw_token, token_digest))
        self.assertFalse(
            authority.verify_invitation_token(
                f"tinv_test.{token_digest}",
                token_digest,
            ),
            "stored verification material must not work directly as the bearer secret",
        )

        record = authority.build_invitation_record(
            invitation_id="tinv_test",
            actor=owner("workspace-a"),
            invitee_email="recipient@example.test",
            invitee_name="Recipient",
            access_level="Limited",
            token_digest=token_digest,
            now_ms=NOW_MS,
        )
        serialized = json.dumps(record)
        self.assertNotIn(raw_token, serialized)
        self.assertNotIn(encoded_secret, serialized)
        self.assertEqual(record["tokenDigest"], token_digest)

    def test_invitation_lifetime_is_exactly_seven_days(self):
        authority = self.load_authority_module()
        self.assertEqual(authority.TEAM_INVITE_TTL_MS, SEVEN_DAYS_MS)
        record = authority.build_invitation_record(
            invitation_id="tinv_test",
            actor=owner("workspace-a"),
            invitee_email="recipient@example.test",
            invitee_name="Recipient",
            access_level="Shared",
            token_digest=TOKEN_DIGEST,
            now_ms=NOW_MS,
        )
        self.assertEqual(record["createdAt"], NOW_MS)
        self.assertEqual(record["expiresAt"], NOW_MS + SEVEN_DAYS_MS)
        self.assertEqual(record["status"], "invited")

    def test_public_pending_and_member_projections_are_explicitly_redacted(self):
        authority = self.load_authority_module()
        raw_record = {
            "v": 2,
            "id": "tinv_test",
            "workspaceId": "workspace-a",
            "inviteeEmail": "recipient@example.test",
            "inviteeName": "Recipient",
            "displayName": "Recipient",
            "accessLevel": "Limited",
            "status": "invited",
            "createdAt": NOW_MS,
            "updatedAt": NOW_MS,
            "expiresAt": NOW_MS + SEVEN_DAYS_MS,
            "createdByUserId": "user-owner",
            "createdByUserName": "Owner",
            "tokenDigest": TOKEN_DIGEST,
            "rawToken": RAW_TOKEN,
            "sessionData": "session-secret",
            "storageKey": "internal-storage-key",
        }

        public = authority.project_public_invitation(raw_record)
        pending = authority.project_pending_invitation(raw_record)
        self.assertIsInstance(public, dict)
        self.assertIsInstance(pending, dict)
        self.assertEqual(
            set(pending),
            {"id", "inviteeEmail", "displayName", "accessLevel", "status", "expiresAt"},
        )

        serialized = json.dumps({"public": public, "pending": pending})
        for secret in (
            RAW_TOKEN,
            TOKEN_DIGEST,
            "workspace-a",
            "user-owner",
            "session-secret",
            "internal-storage-key",
        ):
            self.assertNotIn(secret, serialized)
        self.assertNotRegex(serialized.lower(), r'"(?:raw)?token(?:digest)?"')

    def test_accepted_team_record_binds_user_and_email_without_account_membership(self):
        authority = self.load_authority_module()
        invitation_record = authority.build_invitation_record(
            invitation_id="tinv_test",
            actor=owner("workspace-a"),
            invitee_email="recipient@example.test",
            invitee_name="Recipient",
            access_level="Limited",
            token_digest=TOKEN_DIGEST,
            now_ms=NOW_MS,
        )
        recipient = ordinary_member("recipient-home", email="recipient@example.test")
        record = authority.build_membership_record(
            invitation_record=invitation_record,
            recipient=recipient,
            accepted_at=NOW_MS + 1,
        )

        self.assertEqual(record["workspaceId"], "workspace-a")
        self.assertEqual(record["memberUserId"], recipient.user_id)
        self.assertEqual(record["verifiedRecipientEmail"], recipient.email)
        self.assertEqual(record["status"], "active")
        self.assertEqual(record["acceptedAt"], NOW_MS + 1)
        self.assertNotIn("inviteToken", record)
        self.assertNotIn("tokenDigest", record)

        source = Path(authority.__file__).read_text(encoding="utf-8")
        self.assertNotRegex(source, r"WorkspaceMembership\s*\(")
        self.assertNotRegex(
            source,
            r"(?:create|insert|provision|save)_workspace_membership",
        )

    def test_every_state_mutation_has_an_atomic_redis_script(self):
        authority = self.load_authority_module()
        scripts = authority.ATOMIC_MUTATION_SCRIPTS
        self.assertGreaterEqual(
            set(scripts),
            {"issue", "accept", "decline", "cancel", "remove", "update_access"},
        )
        for operation, script in scripts.items():
            with self.subTest(operation=operation):
                self.assertIsInstance(script, str)
                self.assertIn("redis.call", script)
                self.assertRegex(script, re.compile(r"redis\.call\(['\"]GET['\"]"))
                self.assertRegex(script, re.compile(r"redis\.call\(['\"]SET['\"]"))

        issue_script = scripts["issue"].lower()
        for marker in ("active", "invited", "member", "workspace"):
            self.assertIn(marker, issue_script)

        for operation in ("accept", "decline", "cancel"):
            transition_script = scripts[operation].lower()
            for marker in ("invited", "expires", "status"):
                self.assertIn(marker, transition_script)
            for terminal in ("accepted", "declined", "cancelled"):
                self.assertIn(terminal, transition_script)

        source = Path(authority.__file__).read_text(encoding="utf-8")
        self.assertIn('"EVAL"', source)
        self.assertNotRegex(
            source,
            r"def (?:issue_invitation|accept_invitation|decline_invitation|cancel_invitation|remove_member|update_member_access)[\s\S]{0,2400}/set/",
            "Team state transitions must not be implemented as independent REST SET sagas",
        )


if __name__ == "__main__":
    unittest.main()
