from __future__ import annotations

import io
import json
import os
import unittest
from unittest.mock import patch

from api.auth.runtime import (
    AuthenticatedMemberContext,
    AuthenticatedMemberResolution,
    MemberResolutionOutcome,
)
from api.team import members as team_members


class HeaderMap(dict):
    def raw_items(self):
        return iter(list(self.items()))


class FakeHandler:
    def __init__(self, path: str, body: bytes = b""):
        self.path = path
        self.headers = HeaderMap({"content-length": str(len(body))})
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status = None
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
        return json.loads(self.wfile.getvalue())


def authenticated(workspace_id: str = "workspace-a") -> AuthenticatedMemberResolution:
    return AuthenticatedMemberResolution(
        MemberResolutionOutcome.AUTHENTICATED,
        AuthenticatedMemberContext(
            user_id="user-a",
            email="member-a@example.test",
            name="Member A",
            workspace_id=workspace_id,
            membership_role="member",
        ),
    )


def unauthenticated() -> AuthenticatedMemberResolution:
    return AuthenticatedMemberResolution(
        MemberResolutionOutcome.UNAUTHENTICATED,
        None,
    )


class TeamRosterReadTests(unittest.TestCase):
    def invoke_get(
        self,
        path: str,
        *,
        resolution: AuthenticatedMemberResolution,
        roster=([], None),
    ) -> tuple[FakeHandler, object]:
        request = FakeHandler(path)
        with patch.dict(os.environ, {}, clear=True), patch.object(
            team_members,
            "resolve_authenticated_member",
            return_value=resolution,
        ), patch.object(
            team_members,
            "_list_team_members",
            return_value=roster,
        ) as list_members:
            team_members.handler.do_GET(request)
        return request, list_members

    def test_unauthenticated_read_is_rejected_before_storage(self):
        for path in (
            "/api/team/members?op=list",
            "/api/team/members?op=list&workspaceId=workspace-a",
            "/api/team/members?op=list&workspaceId=workspace-b",
        ):
            with self.subTest(path=path):
                request, list_members = self.invoke_get(
                    path,
                    resolution=unauthenticated(),
                )

                self.assertEqual(request.status, 401)
                self.assertEqual(request.payload()["error"]["code"], "unauthorized")
                list_members.assert_not_called()

    def test_session_workspace_is_the_only_roster_authority(self):
        projected_member = {
            "email": "teammate@example.test",
            "displayName": "Team Mate",
            "accessLevel": "Limited",
            "status": "active",
        }
        request, list_members = self.invoke_get(
            "/api/team/members?op=list",
            resolution=authenticated("workspace-a"),
            roster=([projected_member], None),
        )

        self.assertEqual(request.status, 200)
        self.assertEqual(
            request.payload(),
            {"ok": True, "members": [projected_member]},
        )
        list_members.assert_called_once_with("workspace-a")

    def test_foreign_client_workspace_is_rejected_before_storage(self):
        request, list_members = self.invoke_get(
            "/api/team/members?op=list&workspaceId=workspace-b",
            resolution=authenticated("workspace-a"),
        )

        self.assertEqual(request.status, 403)
        self.assertEqual(request.payload()["error"]["code"], "forbidden")
        list_members.assert_not_called()

    def test_workspace_ids_are_opaque_and_case_sensitive(self):
        canonical_workspace_id = "wsp_AaZz09_-"
        accepted, accepted_store = self.invoke_get(
            f"/api/team/members?op=list&workspaceId={canonical_workspace_id}",
            resolution=authenticated(canonical_workspace_id),
        )
        rejected, rejected_store = self.invoke_get(
            "/api/team/members?op=list&workspaceId=wsp_aazz09_-",
            resolution=authenticated(canonical_workspace_id),
        )

        self.assertEqual(accepted.status, 200)
        accepted_store.assert_called_once_with(canonical_workspace_id)
        self.assertEqual(rejected.status, 403)
        rejected_store.assert_not_called()
        self.assertEqual(
            team_members._build_members_index_key(canonical_workspace_id),
            f"cuevion:team:v1:members-index:{canonical_workspace_id}",
        )

    def test_cross_workspace_storage_record_is_not_projected(self):
        record = self.stored_member(workspaceId="workspace-b")

        self.assertIsNone(
            team_members._normalize_member_record(
                record,
                "workspace-a",
                "teammate@example.test",
            )
        )

    def test_roster_projection_is_explicit_and_token_redacted(self):
        raw_token = "raw-invitation-bearer-secret"
        record = self.stored_member(inviteToken=raw_token)

        projected = team_members._normalize_member_record(
            record,
            "workspace-a",
            "teammate@example.test",
        )

        self.assertEqual(
            projected,
            {
                "email": "teammate@example.test",
                "displayName": "Team Mate",
                "accessLevel": "Limited",
                "status": "active",
            },
        )
        serialized = json.dumps(projected)
        self.assertNotIn(raw_token, serialized)
        self.assertNotIn("token", serialized.lower())
        self.assertNotIn("credential", serialized.lower())
        self.assertNotIn("workspaceId", projected)
        self.assertNotIn("invitedByUserId", projected)

    def test_historical_v1_non_email_identifier_remains_visible_and_redacted(self):
        record = self.stored_member(email="legacy-recipient")

        self.assertEqual(
            team_members._normalize_member_record(
                record,
                "workspace-a",
                "legacy-recipient",
            ),
            {
                "email": "legacy-recipient",
                "displayName": "Team Mate",
                "accessLevel": "Limited",
                "status": "active",
            },
        )

    def test_secure_v2_membership_is_roster_visible_without_token_fields(self):
        record = {
            "v": 2,
            "workspaceId": "wsp_AaZz09_-",
            "email": "teammate@example.test",
            "verifiedRecipientEmail": "teammate@example.test",
            "memberUserId": "usr_recipient",
            "displayName": "Team Mate",
            "accessLevel": "Shared",
            "status": "active",
            "sourceInvitationId": "tinv_test",
            "createdAt": 1_800_000_000_000,
            "acceptedAt": 1_800_000_000_100,
            "updatedAt": 1_800_000_000_100,
        }

        self.assertEqual(
            team_members._normalize_member_record(
                record,
                "wsp_AaZz09_-",
                "teammate@example.test",
            ),
            {
                "email": "teammate@example.test",
                "displayName": "Team Mate",
                "accessLevel": "Shared",
                "status": "active",
            },
        )
        self.assertIsNone(
            team_members._normalize_member_record(
                {**record, "sourceInvitationId": "invalid"},
                "wsp_AaZz09_-",
                "teammate@example.test",
            )
        )

    def test_unknown_member_schema_is_not_roster_visible(self):
        for schema_version in (999, [], {}):
            with self.subTest(schema_version=schema_version):
                record = self.stored_member(v=schema_version)
                self.assertIsNone(
                    team_members._normalize_member_record(
                        record,
                        "workspace-a",
                        "teammate@example.test",
                    )
                )

    def test_empty_success_is_distinct_from_store_failure(self):
        success, success_store = self.invoke_get(
            "/api/team/members?op=list",
            resolution=authenticated(),
            roster=([], None),
        )
        failure, failure_store = self.invoke_get(
            "/api/team/members?op=list",
            resolution=authenticated(),
            roster=(
                None,
                {
                    "code": "team_members_store_unavailable",
                    "message": "Bearer internal-store-secret must not escape.",
                },
            ),
        )

        self.assertEqual(success.status, 200)
        self.assertEqual(success.payload(), {"ok": True, "members": []})
        self.assertEqual(failure.status, 503)
        self.assertEqual(
            failure.payload(),
            {
                "ok": False,
                "error": {
                    "code": "team_members_unavailable",
                    "message": "Team members are temporarily unavailable.",
                },
            },
        )
        self.assertNotIn("internal-store-secret", failure.wfile.getvalue().decode("utf-8"))
        success_store.assert_called_once_with("workspace-a")
        failure_store.assert_called_once_with("workspace-a")

    def test_unknown_team_writes_remain_disabled_without_body_auth_or_storage(self):
        request_body = b'not-json-and-must-remain-unread'
        request = FakeHandler(
            "/api/team/members?op=unknown",
            request_body,
        )
        with patch.dict(os.environ, {}, clear=True), patch.object(
            team_members,
            "resolve_authenticated_member",
            side_effect=AssertionError("disabled write reached authentication"),
        ), patch.object(
            team_members,
            "_read_json_body",
            side_effect=AssertionError("disabled write parsed its body"),
        ), patch.object(
            team_members,
            "build_runtime_team_authority",
            side_effect=AssertionError("disabled write reached storage"),
            create=True,
        ):
            team_members.handler.do_POST(request)

        self.assertEqual(request.status, 404)
        self.assertEqual(request.payload()["error"]["code"], "not_found")
        self.assertEqual(request.rfile.read(), request_body)

    def test_unknown_team_get_operations_remain_legacy_disabled(self):
        request = FakeHandler("/api/team/members?op=unknown")
        with patch.dict(os.environ, {}, clear=True), patch.object(
            team_members,
            "resolve_authenticated_member",
            side_effect=AssertionError("disabled operation reached authentication"),
        ), patch.object(
            team_members,
            "build_runtime_team_authority",
            side_effect=AssertionError("disabled operation reached storage"),
            create=True,
        ):
            team_members.handler.do_GET(request)

        self.assertEqual(request.status, 404)
        self.assertEqual(request.payload()["error"]["code"], "not_found")

    @staticmethod
    def stored_member(**overrides) -> dict:
        record = {
            "v": 1,
            "workspaceId": "workspace-a",
            "email": "teammate@example.test",
            "displayName": "Team Mate",
            "accessLevel": "Limited",
            "status": "active",
            "inviteToken": "raw-token",
            "invitedByUserId": "owner-user-id",
            "invitedByUserName": "Owner",
            "createdAt": 1_800_000_000_000,
            "updatedAt": 1_800_000_000_100,
            "acceptedAt": 1_800_000_000_100,
            "mailboxCredential": "must-never-leak",
        }
        record.update(overrides)
        return record


if __name__ == "__main__":
    unittest.main()
