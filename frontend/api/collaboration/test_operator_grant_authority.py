"""Isolated current-authority checks using real account records and signed grants."""

from __future__ import annotations

import unittest
from unittest import mock

from api.auth import models, runtime
from cuevion_auth import current_account_repository_contract as contract

from . import authorization, operator_grant, owner_http, owner_request_security
from . import test_owner_http as fixtures


NOW = fixtures.NOW
USER_ID = fixtures.OWNER_USER_ID
WORKSPACE_ID = "wsp_" + "A" * 22
OTHER_USER_ID = "usr_" + "B" * 21 + "A"
OTHER_WORKSPACE_ID = "wsp_" + "B" * 21 + "A"
EMAIL_ID = "vem_" + "A" * 22
MAILBOX_ID = fixtures.MAILBOX_ID
TEAM_REF = "tinv_current_membership"


def _member():
    return runtime.AuthenticatedMemberContext(
        USER_ID, fixtures.OWNER_EMAIL, "Owner Person", WORKSPACE_ID, "owner"
    )


def _account_result(*, user_id=USER_ID, workspace_id=WORKSPACE_ID,
                    email=fixtures.OWNER_EMAIL):
    user = models.CuevionUser(
        1, user_id, models.UserStatus.ACTIVE, EMAIL_ID, "Current Owner Name",
        1, NOW - 100, NOW - 10, 1,
    )
    primary_email = models.VerifiedEmail(
        1, EMAIL_ID, user_id, email, models.VerifiedEmailStatus.VERIFIED,
        "auth0", NOW - 100, NOW - 90, None, 1,
    )
    workspace = models.Workspace(
        1, workspace_id, models.WorkspaceStatus.ACTIVE, user_id,
        NOW - 100, NOW - 10, 1,
    )
    membership = models.WorkspaceMembership(
        1, workspace_id, user_id, models.WorkspaceRole.OWNER,
        models.WorkspaceMembershipStatus.ACTIVE, NOW - 100, NOW - 10, 1,
    )
    account = contract.CurrentAccountByUserAuthority(
        user, primary_email, workspace, membership
    )
    return contract.CurrentAccountByUserAuthorityResult(
        contract.CurrentAccountReadOutcome.FOUND, account
    )


def _mailboxes(*, mailbox_id=MAILBOX_ID, provider="google",
               email=fixtures.OWNER_EMAIL):
    return {
        "status": "ok",
        "config": {
            "email": email,
            "managedInboxes": [{
                "id": mailbox_id,
                "email": "inbox@example.com",
                "provider": provider,
                "connected": True,
                "connectionStatus": "connected",
                "oauthRefreshToken": "fake-mailbox-token-never-exported",
            }],
        },
    }


class OperatorGrantCurrentAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.security = owner_request_security.parse_owner_security_configuration(
            owner_http._trusted_security_snapshot(fixtures._environment())
        )
        self.clock = self.enterContext(mock.patch.object(operator_grant.time, "time", return_value=NOW))
        self.account_read = self.enterContext(mock.patch.object(
            operator_grant, "_read_current_account", return_value=_account_result()
        ))
        self.mailbox_read = self.enterContext(mock.patch.object(
            operator_grant, "_read_current_mailboxes", return_value=_mailboxes()
        ))
        self.team_read = self.enterContext(mock.patch.object(
            authorization, "_resolve_active_team_member", return_value=(None, "not_active")
        ))
        issued = operator_grant._issue_operator_grant(
            _member(), [{"mailboxId": MAILBOX_ID, "provider": "google"}],
            owner_security_configuration=self.security, now=NOW,
        )
        self.context = operator_grant.verify_operator_grant(
            issued["grant"], owner_security_configuration=self.security, now=NOW
        )

    def resolve(self, mailbox_id=MAILBOX_ID, *, security=None, context=None):
        return operator_grant.resolve_current_operator_config(
            self.context if context is None else context, mailbox_id,
            owner_security_configuration=self.security if security is None else security,
        )

    def assert_denied(self, code, **kwargs):
        with self.assertRaises(operator_grant.OperatorGrantError) as caught:
            self.resolve(**kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def test_signed_grant_resolves_current_account_and_selected_owned_mailbox(self):
        result = self.resolve()
        self.assertEqual(result, {
            "workspaceId": WORKSPACE_ID,
            "userId": USER_ID,
            "email": fixtures.OWNER_EMAIL,
            "membershipRef": "",
            "ownerMailboxId": MAILBOX_ID,
            "ownerProvider": "google",
            "ownerDisplayName": "Current Owner Name",
        })
        self.account_read.assert_called_once_with(USER_ID, WORKSPACE_ID)
        self.mailbox_read.assert_called_once_with(fixtures.OWNER_EMAIL)
        self.team_read.assert_called_once_with(WORKSPACE_ID, USER_ID)
        self.assertNotIn("fake-mailbox-token-never-exported", repr(result))

    def test_current_verified_email_drives_mailbox_lookup_after_canonical_id_check(self):
        current_email = "new-address@example.com"
        self.account_read.return_value = _account_result(email=current_email)
        self.mailbox_read.return_value = _mailboxes(email=current_email)
        self.assertEqual(self.resolve()["email"], current_email)
        self.account_read.assert_called_once_with(USER_ID, WORKSPACE_ID)
        self.mailbox_read.assert_called_once_with(current_email)

    def test_alternative_valid_user_graph_does_not_authorize_signed_user(self):
        self.account_read.return_value = _account_result(user_id=OTHER_USER_ID)
        self.assert_denied("owner_not_authorized")
        self.mailbox_read.assert_not_called()
        self.team_read.assert_not_called()

    def test_alternative_valid_workspace_graph_does_not_authorize_signed_workspace(self):
        self.account_read.return_value = _account_result(workspace_id=OTHER_WORKSPACE_ID)
        self.assert_denied("owner_not_authorized")
        self.mailbox_read.assert_not_called()
        self.team_read.assert_not_called()

    def test_changed_or_inactive_account_graph_denied_before_mailbox_read(self):
        # Real contracts cannot be constructed with an inactive graph. Inject a
        # changed slot only after constructing each complete valid authority so
        # this exercises the consumer's defensive revalidation of returned data.
        cases = [
            ("user", "status", models.UserStatus.SUSPENDED),
            ("user", "status", models.UserStatus.DISABLED),
            ("primary_verified_email", "status", models.VerifiedEmailStatus.RETIRED),
            ("primary_verified_email", "status", models.VerifiedEmailStatus.PENDING),
            ("primary_verified_email", "user_id", OTHER_USER_ID),
            ("primary_verified_email", "email_id", "vem_" + "B" * 21 + "A"),
            ("workspace", "status", models.WorkspaceStatus.SUSPENDED),
            ("workspace", "status", models.WorkspaceStatus.ARCHIVED),
            ("workspace_membership", "status", models.WorkspaceMembershipStatus.SUSPENDED),
            ("workspace_membership", "status", models.WorkspaceMembershipStatus.REMOVED),
            ("workspace_membership", "workspace_id", OTHER_WORKSPACE_ID),
            ("workspace_membership", "user_id", OTHER_USER_ID),
            ("workspace_membership", "role", "owner"),
        ]
        for record_name, field_name, value in cases:
            with self.subTest(record=record_name, field=field_name, value=value):
                result = _account_result()
                object.__setattr__(getattr(result.authority, record_name), field_name, value)
                self.account_read.return_value = result
                self.assert_denied("owner_not_authorized")
        self.mailbox_read.assert_not_called()
        self.team_read.assert_not_called()

    def test_non_found_or_malformed_current_account_denied(self):
        results = [
            contract.CurrentAccountByUserAuthorityResult(outcome, None)
            for outcome in (
                contract.CurrentAccountReadOutcome.NOT_AUTHORIZED,
                contract.CurrentAccountReadOutcome.UNAVAILABLE,
                contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
            )
        ] + [None, {}, {"outcome": "found", "authority": _account_result().authority}]
        for result in results:
            with self.subTest(result_type=type(result).__name__):
                self.account_read.return_value = result
                self.assert_denied("owner_not_authorized")
        self.mailbox_read.assert_not_called()
        self.team_read.assert_not_called()

    def test_mailbox_outside_grant_scope_rejected_before_authority_reads(self):
        self.assert_denied("grant_scope_invalid", mailbox_id="other.mailbox")
        self.account_read.assert_not_called()
        self.mailbox_read.assert_not_called()
        self.team_read.assert_not_called()

    def test_invalid_mailbox_selection_rejected_before_authority_reads(self):
        for selected in ("", None, [], " primary.mailbox", "primary.mailbox "):
            with self.subTest(selected=selected):
                self.assert_denied("grant_scope_invalid", mailbox_id=selected)
        self.account_read.assert_not_called()
        self.mailbox_read.assert_not_called()
        self.team_read.assert_not_called()

    def test_current_mailbox_provider_must_equal_signed_scope(self):
        for provider in ("custom_imap", "unknown", None):
            with self.subTest(provider=provider):
                self.mailbox_read.return_value = _mailboxes(provider=provider)
                self.assert_denied("grant_scope_invalid")
        self.team_read.assert_not_called()

    def test_mailbox_removed_from_current_owner_configuration_denied(self):
        self.mailbox_read.return_value = _mailboxes(mailbox_id="different.mailbox")
        self.assert_denied("grant_scope_invalid")
        self.team_read.assert_not_called()

    def test_duplicate_or_malformed_owned_mailbox_configuration_denied(self):
        duplicate = _mailboxes()
        duplicate["config"]["managedInboxes"] *= 2
        malformed = _mailboxes()
        malformed["config"]["managedInboxes"][0]["connected"] = "true"
        for result in (duplicate, malformed):
            with self.subTest(kind="duplicate" if result is duplicate else "malformed"):
                self.mailbox_read.return_value = result
                self.assert_denied("grant_scope_invalid")
        self.team_read.assert_not_called()

    def test_config_email_mismatch_cannot_change_canonical_owner(self):
        self.mailbox_read.return_value = _mailboxes(email="other@example.com")
        self.assert_denied("owner_not_authorized")
        self.team_read.assert_not_called()

    def test_unavailable_or_malformed_mailbox_authority_denied(self):
        for result in (None, {}, {"status": "unavailable", "config": None},
                       {"status": "ok", "config": []}):
            with self.subTest(result=result):
                self.mailbox_read.return_value = result
                self.assert_denied("authority_unavailable")
        self.team_read.assert_not_called()

    def test_active_team_membership_uses_current_membership_reference(self):
        for reference in (TEAM_REF, "tinv_rejoined_membership"):
            with self.subTest(reference=reference):
                self.team_read.return_value = ({
                    "memberUserId": USER_ID,
                    "displayName": "Current Owner Name",
                    "accessLevel": "member",
                    "sourceInvitationId": reference,
                }, None)
                self.assertEqual(self.resolve()["membershipRef"], reference)

    def test_missing_team_membership_has_no_participant_reference(self):
        self.team_read.return_value = (None, "not_active")
        self.assertEqual(self.resolve()["membershipRef"], "")

    def test_stale_malformed_or_unavailable_team_authority_denied(self):
        cases = [
            (None, "unavailable"),
            (None, None),
            ({"memberUserId": OTHER_USER_ID, "sourceInvitationId": TEAM_REF}, None),
            ({"memberUserId": USER_ID, "sourceInvitationId": TEAM_REF}, "not_active"),
            ({"memberUserId": USER_ID, "sourceInvitationId": TEAM_REF}, "unavailable"),
            ({"memberUserId": USER_ID}, None),
            ({"memberUserId": USER_ID, "sourceInvitationId": ""}, None),
            ({"memberUserId": USER_ID, "sourceInvitationId": "invalid"}, None),
            ({"memberUserId": USER_ID, "sourceInvitationId": 3}, None),
            ([], None),
        ]
        for index, result in enumerate(cases):
            with self.subTest(case=index):
                self.team_read.return_value = result
                self.assert_denied("authority_unavailable")

    def test_rollout_configuration_change_after_verification_denied_before_reads(self):
        environment = fixtures._environment(mailbox_id="different.mailbox")
        changed = owner_request_security.parse_owner_security_configuration(
            owner_http._trusted_security_snapshot(environment)
        )
        self.assert_denied("owner_not_authorized", security=changed)
        self.account_read.assert_not_called()
        self.mailbox_read.assert_not_called()
        self.team_read.assert_not_called()

    def test_allowlist_key_rotation_invalidates_already_verified_context(self):
        environment = fixtures._environment()
        environment["CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY"] = fixtures._b64(
            b"rotated-allowlist-key-material-32-bytes"
        )
        changed = owner_request_security.parse_owner_security_configuration(
            owner_http._trusted_security_snapshot(environment)
        )
        self.assert_denied("owner_not_authorized", security=changed)
        self.account_read.assert_not_called()
        self.mailbox_read.assert_not_called()
        self.team_read.assert_not_called()

    def test_expiry_after_verification_denied_before_authority_reads(self):
        for timestamp in (NOW + operator_grant.LIFETIME_SECONDS,
                          NOW + operator_grant.LIFETIME_SECONDS + 1, NOW - 1):
            with self.subTest(timestamp=timestamp):
                self.clock.return_value = timestamp
                self.assert_denied("grant_expired")
        self.account_read.assert_not_called()
        self.mailbox_read.assert_not_called()
        self.team_read.assert_not_called()

    def test_ordinary_owner_session_cannot_substitute_for_verified_grant_context(self):
        ordinary_owner = fixtures._context(workspace_id=WORKSPACE_ID)
        with self.assertRaises(operator_grant.OperatorGrantError):
            self.resolve(context=ordinary_owner)
        self.account_read.assert_not_called()
        self.mailbox_read.assert_not_called()
        self.team_read.assert_not_called()

    def test_authority_exceptions_are_safe_and_fail_closed(self):
        for reader in (self.account_read, self.mailbox_read, self.team_read):
            with self.subTest(reader=reader._mock_name):
                reader.side_effect = RuntimeError("sensitive-provider-detail")
                self.assert_denied("authority_unavailable")
                reader.side_effect = None


if __name__ == "__main__":
    unittest.main()
