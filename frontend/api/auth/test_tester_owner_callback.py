from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from api.auth import auth0_flow, runtime, session_store
from api.auth.test_auth_routes import (
    ENVIRONMENT,
    NOW,
    EMAIL,
    FakeAuthority,
    FixedSessionRandom,
    FixedTransactionRandom,
    MemoryCommands,
    _header,
    _transaction_headers,
    _validated_identity,
)
from api.tester import owner_provisioning
from cuevion_auth import current_account_repository_contract as contract


TOKEN = "tsti_" + "A" * 22 + "." + "B" * 43


class OwnerRandom:
    def __init__(self):
        self.counter = 1

    def __call__(self, size):
        value = bytes([self.counter]) * size
        self.counter += 1
        return value


INVITATION = SimpleNamespace(
    invitation_id="tsti_" + "A" * 22,
    email=EMAIL,
    display_name="Tester Owner",
    token_digest="e" * 64,
    status="invited",
    expires_at=(NOW + 3600) * 1000,
    source_request_id=None,
    provisioned_user_id=None,
    provisioned_workspace_id=None,
)


class TesterAuthority:
    def __init__(self):
        self.read_calls = []
        self.mark_calls = []

    def read_provisioning_invitation(self, token, *, allow_provisioned=False):
        self.read_calls.append((token, allow_provisioned))
        if token != TOKEN:
            raise ValueError("invalid")
        return INVITATION

    def mark_provisioned(self, *, raw_token, user_id, workspace_id):
        self.mark_calls.append((raw_token, user_id, workspace_id))
        return SimpleNamespace(
            status="provisioned",
            provisioned_user_id=user_id,
            provisioned_workspace_id=workspace_id,
        )


def not_authorized():
    return contract.CurrentAccountAuthorityResult(
        contract.CurrentAccountReadOutcome.NOT_AUTHORIZED,
        None,
    )


def owner_result(identity):
    request = owner_provisioning.build_initial_owner_request(
        identity,
        INVITATION,
        NOW,
        random_bytes=OwnerRandom(),
    )
    return contract.CurrentAccountAuthorityResult(
        contract.CurrentAccountReadOutcome.FOUND,
        contract.CurrentAccountAuthority(
            request.user,
            request.verified_email,
            request.authentication_identity,
            request.workspace,
            request.workspace_membership,
        ),
    )


class TesterOwnerCallbackTests(unittest.TestCase):
    def _request(self):
        return auth0_flow.build_authorization_request(
            auth0_flow.parse_auth0_configuration(ENVIRONMENT),
            NOW,
            random_bytes=FixedTransactionRandom(),
            tester_invite_token=TOKEN,
        )

    def _callback(self, *, identity=None, provisioner=None):
        request = self._request()
        commands = MemoryCommands()
        tester = TesterAuthority()
        selected_identity = _validated_identity() if identity is None else identity
        authority = FakeAuthority(identity_result=not_authorized())
        selected_provisioner = provisioner
        if selected_provisioner is None:
            exact = owner_result(selected_identity)

            def selected_provisioner(
                incoming_identity,
                invitation,
                **kwargs,
            ):
                self.assertIs(incoming_identity, selected_identity)
                self.assertIs(invitation, INVITATION)
                self.assertIs(
                    kwargs["current_result"].outcome,
                    contract.CurrentAccountReadOutcome.NOT_AUTHORIZED,
                )
                return exact

        with (
            mock.patch.object(
                runtime.auth0_flow,
                "exchange_authorization_code",
                return_value=SimpleNamespace(id_token="synthetic-id-token"),
            ),
            mock.patch.object(
                runtime.auth0_flow,
                "validate_id_token_with_jwks",
                return_value=selected_identity,
            ),
            mock.patch.object(
                runtime,
                "_team_authority",
                side_effect=AssertionError("Tester callback must not use Team authority"),
            ),
        ):
            response = runtime.callback_response(
                "GET",
                _transaction_headers(request),
                "/api/auth/callback?code=auth-code&state=" + request.transaction.state,
                environment=ENVIRONMENT,
                now=NOW,
                token_transport=lambda _request: None,
                jwks_transport=lambda _request: None,
                session_store_factory=lambda _environment: session_store.AuthSessionStore(commands),
                authority_factory=lambda _environment: authority,
                random_bytes=FixedSessionRandom(),
                tester_authority_factory=lambda _environment: tester,
                tester_owner_provisioner=selected_provisioner,
            )
        return response, commands, tester

    def test_tester_callback_marks_exact_owner_before_session_publication(self):
        response, commands, tester = self._callback()
        self.assertEqual(response.status, 303)
        self.assertEqual(_header(response, "location"), ["/"])
        cookies = _header(response, "set-cookie")
        self.assertTrue(
            any(value.startswith("__Host-cuevion_session=") for value in cookies)
        )
        self.assertEqual(tester.read_calls, [(TOKEN, True)])
        self.assertEqual(len(tester.mark_calls), 1)
        token, user_id, workspace_id = tester.mark_calls[0]
        self.assertEqual(token, TOKEN)
        self.assertTrue(user_id.startswith("usr_"))
        self.assertTrue(workspace_id.startswith("wsp_"))

        session_writes = [
            command
            for command in commands.commands
            if len(command) > 1
            and str(command[1]).startswith(session_store.SESSION_KEY_PREFIX)
        ]
        self.assertEqual(len(session_writes), 1)
        self.assertIn('"workspaceRole":"owner"', str(session_writes[0]))

    def test_wrong_verified_email_never_runs_owner_provisioner(self):
        wrong_identity = _validated_identity(email="other@example.com")
        provisioner = mock.Mock()
        response, commands, tester = self._callback(
            identity=wrong_identity,
            provisioner=provisioner,
        )
        self.assertEqual(response.status, 303)
        self.assertEqual(
            _header(response, "location"),
            ["/login?error=authentication_failed"],
        )
        provisioner.assert_not_called()
        self.assertEqual(tester.mark_calls, [])
        self.assertFalse(
            any(
                len(command) > 1
                and str(command[1]).startswith(session_store.SESSION_KEY_PREFIX)
                for command in commands.commands
            )
        )

    def test_owner_provisioning_failure_never_publishes_session(self):
        def fail(*_args, **_kwargs):
            raise owner_provisioning.TesterOwnerProvisioningError(
                "tester_owner_conflict"
            )

        response, commands, tester = self._callback(provisioner=fail)
        self.assertEqual(response.status, 303)
        self.assertEqual(
            _header(response, "location"),
            ["/login?error=authentication_failed"],
        )
        self.assertEqual(tester.mark_calls, [])
        self.assertFalse(
            any(
                len(command) > 1
                and str(command[1]).startswith(session_store.SESSION_KEY_PREFIX)
                for command in commands.commands
            )
        )


if __name__ == "__main__":
    unittest.main()
