from __future__ import annotations

import unittest
from types import SimpleNamespace

from api.auth import auth0_flow, models
from api.tester import owner_provisioning
from cuevion_auth import account_repository_contract as account_contract
from cuevion_auth import current_account_repository_contract as current_contract


NOW = 1_800_000_000
IDENTITY = auth0_flow.ValidatedIdentityEvidence(
    issuer=auth0_flow.AUTH0_ISSUER,
    subject="auth0|tester-subject",
    email="tester@example.com",
    issued_at=NOW - 10,
    expires_at=NOW + 3600,
)


class FixedRandom:
    def __init__(self):
        self.counter = 1

    def __call__(self, size):
        value = bytes([self.counter]) * size
        self.counter += 1
        return value


def invitation(**changes):
    values = {
        "invitation_id": "tsti_" + "A" * 22,
        "email": IDENTITY.email,
        "display_name": "Tester Owner",
        "token_digest": "d" * 64,
        "status": "invited",
        "expires_at": (NOW + 3600) * 1000,
        "provisioned_user_id": None,
        "provisioned_workspace_id": None,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def found_from_request(request):
    authority = current_contract.CurrentAccountAuthority(
        request.user,
        request.verified_email,
        request.authentication_identity,
        request.workspace,
        request.workspace_membership,
    )
    return current_contract.CurrentAccountAuthorityResult(
        current_contract.CurrentAccountReadOutcome.FOUND,
        authority,
    )


NOT_AUTHORIZED = current_contract.CurrentAccountAuthorityResult(
    current_contract.CurrentAccountReadOutcome.NOT_AUTHORIZED,
    None,
)


class Reader:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    def resolve_current_account_by_identity(self, _key):
        self.calls += 1
        if not self.results:
            raise AssertionError("unexpected read")
        return self.results.pop(0)


class Repository:
    def __init__(self, outcome=account_contract.InitialAccountCreationOutcome.CREATED):
        self.outcome = outcome
        self.requests = []

    def create_initial_account(self, request):
        self.requests.append(request)
        return SimpleNamespace(outcome=self.outcome, receipt=None)


class TesterOwnerProvisioningTests(unittest.TestCase):
    def test_request_is_a_standalone_active_owner_oidc_aggregate(self):
        invite = invitation()
        request = owner_provisioning.build_initial_owner_request(
            IDENTITY,
            invite,
            NOW,
            random_bytes=FixedRandom(),
        )
        self.assertEqual(request.user.display_name, "Tester Owner")
        self.assertEqual(request.verified_email.canonical_email, IDENTITY.email)
        self.assertEqual(
            request.verified_email.verification_source,
            owner_provisioning.derive_tester_provenance(IDENTITY, invite),
        )
        self.assertIs(
            request.authentication_identity.method,
            models.AuthenticationMethod.OIDC,
        )
        self.assertEqual(
            request.workspace.created_by_user_id,
            request.user.user_id,
        )
        self.assertIs(
            request.workspace_membership.role,
            models.WorkspaceRole.OWNER,
        )
        self.assertIs(
            request.workspace_membership.status,
            models.WorkspaceMembershipStatus.ACTIVE,
        )
        self.assertIs(
            request.security_event.event_type,
            account_contract.InitialSecurityEventType.INITIAL_ACCOUNT_CREATED,
        )

    def test_existing_exact_tester_owner_recovers_without_writer(self):
        invite = invitation()
        request = owner_provisioning.build_initial_owner_request(
            IDENTITY,
            invite,
            NOW,
            random_bytes=FixedRandom(),
        )
        exact = found_from_request(request)
        reader = Reader(exact)

        def forbidden_repository(_request, _now):
            raise AssertionError("writer must not run")

        result = owner_provisioning.provision_or_recover_tester_owner(
            IDENTITY,
            invite,
            environment={},
            authority_reader=reader,
            now=NOW,
            current_result=exact,
            repository_factory=forbidden_repository,
        )
        self.assertIs(result, exact)

    def test_new_account_writes_once_then_requires_exact_canonical_readback(self):
        invite = invitation()
        produced = {}
        repository = Repository()

        def repository_factory(request, _now):
            produced["exact"] = found_from_request(request)
            return repository

        class DynamicReader:
            calls = 0
            def resolve_current_account_by_identity(self, _key):
                self.calls += 1
                if self.calls == 1:
                    return produced["exact"]
                raise AssertionError("unexpected extra read")

        result = owner_provisioning.provision_or_recover_tester_owner(
            IDENTITY,
            invite,
            environment={},
            authority_reader=DynamicReader(),
            now=NOW,
            current_result=NOT_AUTHORIZED,
            repository_factory=repository_factory,
            random_bytes=FixedRandom(),
        )
        self.assertEqual(len(repository.requests), 1)
        self.assertIs(result, produced["exact"])

    def test_concurrent_conflict_can_recover_only_exact_tester_provenance(self):
        invite = invitation()
        produced = {}
        repository = Repository(
            account_contract.InitialAccountCreationOutcome.CONFLICT
        )

        def repository_factory(request, _now):
            produced["exact"] = found_from_request(request)
            return repository

        reader = SimpleNamespace(
            resolve_current_account_by_identity=lambda _key: produced["exact"]
        )
        result = owner_provisioning.provision_or_recover_tester_owner(
            IDENTITY,
            invite,
            environment={},
            authority_reader=reader,
            now=NOW,
            current_result=NOT_AUTHORIZED,
            repository_factory=repository_factory,
            random_bytes=FixedRandom(),
        )
        self.assertIs(result, produced["exact"])

    def test_existing_non_tester_account_cannot_gain_second_workspace(self):
        invite = invitation()
        request = owner_provisioning.build_initial_owner_request(
            IDENTITY,
            invite,
            NOW,
            random_bytes=FixedRandom(),
        )
        bad_email = models.VerifiedEmail(
            request.verified_email.schema_version,
            request.verified_email.email_id,
            request.verified_email.user_id,
            request.verified_email.canonical_email,
            request.verified_email.status,
            "different-provenance",
            request.verified_email.created_at,
            request.verified_email.verified_at,
            None,
            request.verified_email.row_version,
        )
        bad = current_contract.CurrentAccountAuthorityResult(
            current_contract.CurrentAccountReadOutcome.FOUND,
            current_contract.CurrentAccountAuthority(
                request.user,
                bad_email,
                request.authentication_identity,
                request.workspace,
                request.workspace_membership,
            ),
        )
        with self.assertRaises(
            owner_provisioning.TesterOwnerProvisioningError
        ) as error:
            owner_provisioning.provision_or_recover_tester_owner(
                IDENTITY,
                invite,
                environment={},
                authority_reader=Reader(bad),
                now=NOW,
                current_result=bad,
                repository_factory=lambda *_: (_ for _ in ()).throw(
                    AssertionError("writer must not run")
                ),
            )
        self.assertEqual(error.exception.code, "tester_owner_conflict")

    def test_provisioned_invite_must_match_exact_user_and_workspace(self):
        request = owner_provisioning.build_initial_owner_request(
            IDENTITY,
            invitation(),
            NOW,
            random_bytes=FixedRandom(),
        )
        exact = found_from_request(request)
        authority = exact.authority
        invite = invitation(
            status="provisioned",
            provisioned_user_id=authority.user.user_id,
            provisioned_workspace_id=authority.workspace.workspace_id,
        )
        # Provenance changes with invitation data only, not status/record IDs.
        self.assertTrue(
            owner_provisioning.authority_matches_tester(
                exact,
                IDENTITY,
                invite,
            )
        )
        wrong = invitation(
            status="provisioned",
            provisioned_user_id="usr_" + "Z" * 21 + "A",
            provisioned_workspace_id=authority.workspace.workspace_id,
        )
        self.assertFalse(
            owner_provisioning.authority_matches_tester(
                exact,
                IDENTITY,
                wrong,
            )
        )


if __name__ == "__main__":
    unittest.main()
