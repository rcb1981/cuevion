"""Transactional standalone owner provisioning for Tester Invite authentication."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from collections.abc import Callable, Mapping

from api.auth import account_authority, auth0_flow, models
from cuevion_auth import account_repository_contract as account_contract
from cuevion_auth.current_account_repository_contract import (
    AuthenticationIdentityLookupKey,
    CurrentAccountAuthority,
    CurrentAccountAuthorityResult,
    CurrentAccountReadOutcome,
)
from cuevion_db import postgresql_initial_account_repository as initial_repository


_SCHEMA_VERSION = 1
_INITIAL_SECURITY_EPOCH = 1
_INITIAL_ROW_VERSION = 1
_TRUST_DOMAIN = "cuevion.auth0.tester-invite"
_VERIFICATION_COORDINATOR = "cuevion.auth0.callback"
_VERIFICATION_SOURCE_PREFIX = "tester-invite-oidc:v1:"

RepositoryFactory = Callable[
    [account_contract.InitialAccountCreationRequest, int],
    object,
]
RandomBytes = Callable[[int], bytes]


class TesterOwnerProvisioningError(Exception):
    __slots__ = ("code",)
    _CODES = frozenset({
        "tester_owner_conflict",
        "tester_owner_unavailable",
        "tester_owner_internal",
    })

    def __init__(self, code: str = "tester_owner_internal") -> None:
        self.code = code if code in self._CODES else "tester_owner_internal"
        Exception.__init__(self)

    def __str__(self) -> str:
        return "tester owner provisioning failed"

    __repr__ = __str__


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _random_exact(random_bytes: RandomBytes, size: int) -> bytes:
    value = random_bytes(size)
    if type(value) is not bytes or len(value) != size:
        raise TesterOwnerProvisioningError()
    return value


def _record_id(prefix: str, random_bytes: RandomBytes) -> str:
    return prefix + _b64url(_random_exact(random_bytes, 16))


def derive_tester_provenance(identity, invitation) -> str:
    try:
        payload = json.dumps(
            [
                "cuevion.tester-owner-provenance.v1",
                invitation.invitation_id,
                invitation.token_digest,
                identity.issuer,
                identity.subject,
                identity.email,
            ],
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
    except Exception:
        raise TesterOwnerProvisioningError("tester_owner_conflict") from None
    return _VERIFICATION_SOURCE_PREFIX + hashlib.sha256(payload).hexdigest()


def _validate_inputs(identity, invitation, now: int) -> None:
    try:
        valid = (
            type(identity) is auth0_flow.ValidatedIdentityEvidence
            and type(now) is int
            and 0 <= now < identity.expires_at
            and identity.issued_at <= now + 60
            and type(invitation.invitation_id) is str
            and invitation.invitation_id.startswith("tsti_")
            and type(invitation.token_digest) is str
            and len(invitation.token_digest) == 64
            and type(invitation.email) is str
            and invitation.email == identity.email
            and type(invitation.display_name) is str
            and bool(invitation.display_name)
            and invitation.status in {"invited", "provisioned"}
            and type(invitation.expires_at) is int
            and now < invitation.expires_at // 1000
        )
    except Exception:
        valid = False
    if not valid:
        raise TesterOwnerProvisioningError("tester_owner_conflict")


def build_initial_owner_request(
    identity: auth0_flow.ValidatedIdentityEvidence,
    invitation,
    now: int,
    *,
    random_bytes: RandomBytes = secrets.token_bytes,
) -> account_contract.InitialAccountCreationRequest:
    _validate_inputs(identity, invitation, now)

    user_id = _record_id("usr_", random_bytes)
    email_id = _record_id("vem_", random_bytes)
    identity_id = _record_id("aid_", random_bytes)
    workspace_id = _record_id("wsp_", random_bytes)
    operation_digest = _b64url(_random_exact(random_bytes, 32))
    assertion_id = _b64url(_random_exact(random_bytes, 32))
    event_id = _record_id("sev_", random_bytes)

    user = models.CuevionUser(
        schema_version=_SCHEMA_VERSION,
        user_id=user_id,
        status=models.UserStatus.ACTIVE,
        primary_verified_email_id=email_id,
        display_name=invitation.display_name,
        security_epoch=_INITIAL_SECURITY_EPOCH,
        created_at=now,
        updated_at=now,
        row_version=_INITIAL_ROW_VERSION,
    )
    email = models.VerifiedEmail(
        schema_version=_SCHEMA_VERSION,
        email_id=email_id,
        user_id=user_id,
        canonical_email=identity.email,
        status=models.VerifiedEmailStatus.VERIFIED,
        verification_source=derive_tester_provenance(identity, invitation),
        created_at=now,
        verified_at=now,
        retired_at=None,
        row_version=_INITIAL_ROW_VERSION,
    )
    authentication_identity = models.AuthenticationIdentity(
        schema_version=_SCHEMA_VERSION,
        identity_id=identity_id,
        user_id=user_id,
        issuer=identity.issuer,
        subject=identity.subject,
        method=models.AuthenticationMethod.OIDC,
        status=models.AuthenticationIdentityStatus.ACTIVE,
        verified_email_id=email_id,
        created_at=now,
        last_used_at=None,
        row_version=_INITIAL_ROW_VERSION,
    )
    workspace = models.Workspace(
        schema_version=_SCHEMA_VERSION,
        workspace_id=workspace_id,
        status=models.WorkspaceStatus.ACTIVE,
        created_by_user_id=user_id,
        created_at=now,
        updated_at=now,
        row_version=_INITIAL_ROW_VERSION,
    )
    membership = models.WorkspaceMembership(
        schema_version=_SCHEMA_VERSION,
        workspace_id=workspace_id,
        user_id=user_id,
        role=models.WorkspaceRole.OWNER,
        status=models.WorkspaceMembershipStatus.ACTIVE,
        created_at=now,
        updated_at=now,
        row_version=_INITIAL_ROW_VERSION,
    )
    evidence = account_contract.VerifiedAuthenticationEvidence(
        schema_version=_SCHEMA_VERSION,
        trust_domain=_TRUST_DOMAIN,
        verification_coordinator_id=_VERIFICATION_COORDINATOR,
        assertion_id=assertion_id,
        issuer=identity.issuer,
        subject=identity.subject,
        authentication_method=models.AuthenticationMethod.OIDC,
        canonical_verified_email=identity.email,
        verified_at=now,
        issued_at=now,
        expires_at=min(identity.expires_at, now + 10 * 60),
    )
    security_event = account_contract.InitialSecurityEventRequest(
        schema_version=_SCHEMA_VERSION,
        event_id=event_id,
        event_type=account_contract.InitialSecurityEventType.INITIAL_ACCOUNT_CREATED,
    )
    request = account_contract.InitialAccountCreationRequest(
        request_version=_SCHEMA_VERSION,
        operation_reference=account_contract.InitialAccountOperationReference(
            schema_version=_SCHEMA_VERSION,
            derivation_key_epoch=1,
            operation_digest=operation_digest,
        ),
        user=user,
        verified_email=email,
        authentication_identity=authentication_identity,
        workspace=workspace,
        workspace_membership=membership,
        authentication_evidence=evidence,
        security_event=security_event,
    )
    account_contract.validate_initial_account_creation_request(request)
    return request


class _BoundAuthorizer:
    __slots__ = ("_request", "_now")

    def __init__(self, request, now: int) -> None:
        self._request = request
        self._now = now

    def authorize_new_operation(self, request):
        try:
            if not account_contract.initial_account_creation_requests_are_replay_equivalent(
                request, self._request
            ):
                return None
        except Exception:
            return None
        evidence = request.authentication_evidence
        return initial_repository.InitialAccountWriteContext(
            context_version=1,
            trusted_now=self._now,
            operation_reference=request.operation_reference,
            evidence_assertion_id=evidence.assertion_id,
            trust_domain=evidence.trust_domain,
            verification_coordinator_id=evidence.verification_coordinator_id,
        )


def build_runtime_repository(
    environment: Mapping[str, str],
    request: account_contract.InitialAccountCreationRequest,
    now: int,
):
    try:
        writer = environment["CUEVION_AUTH_ACCOUNT_WRITER_DATABASE_URL"]
        reader = environment.get("CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL")
        if type(writer) is not str or writer == reader:
            raise ValueError
        parsed = account_authority.parse_account_reader_database_url(
            {"CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL": writer}
        )
        connection_factory = account_authority.AccountReaderConnectionFactory(parsed)
    except Exception:
        raise TesterOwnerProvisioningError("tester_owner_unavailable") from None

    return initial_repository.PostgreSQLInitialAccountRepository(
        connection_factory,
        _BoundAuthorizer(request, now),
    )


def authority_matches_tester(
    result: object,
    identity: auth0_flow.ValidatedIdentityEvidence,
    invitation,
) -> bool:
    key = AuthenticationIdentityLookupKey(identity.issuer, identity.subject)
    if not account_authority.auth0_authority_matches(result, key, identity.email):
        return False
    try:
        if type(result) is not CurrentAccountAuthorityResult:
            return False
        authority = result.authority
        if type(authority) is not CurrentAccountAuthority:
            return False
        expected_provenance = derive_tester_provenance(identity, invitation)
        valid = (
            authority.user.status is models.UserStatus.ACTIVE
            and authority.primary_verified_email.status
            is models.VerifiedEmailStatus.VERIFIED
            and authority.primary_verified_email.verification_source
            == expected_provenance
            and authority.authentication_identity.method
            is models.AuthenticationMethod.OIDC
            and authority.workspace.status is models.WorkspaceStatus.ACTIVE
            and authority.workspace.created_by_user_id == authority.user.user_id
            and authority.workspace_membership.user_id == authority.user.user_id
            and authority.workspace_membership.workspace_id
            == authority.workspace.workspace_id
            and authority.workspace_membership.role is models.WorkspaceRole.OWNER
            and authority.workspace_membership.status
            is models.WorkspaceMembershipStatus.ACTIVE
        )
        if not valid:
            return False
        if invitation.status == "provisioned":
            return (
                invitation.provisioned_user_id == authority.user.user_id
                and invitation.provisioned_workspace_id
                == authority.workspace.workspace_id
            )
        return True
    except Exception:
        return False


def _read_exact(reader, identity, invitation):
    key = AuthenticationIdentityLookupKey(identity.issuer, identity.subject)
    try:
        result = reader.resolve_current_account_by_identity(key)
    except Exception:
        raise TesterOwnerProvisioningError("tester_owner_unavailable") from None
    if type(result) is not CurrentAccountAuthorityResult:
        raise TesterOwnerProvisioningError()
    if result.outcome in {
        CurrentAccountReadOutcome.UNAVAILABLE,
        CurrentAccountReadOutcome.INTERNAL_ERROR,
    }:
        raise TesterOwnerProvisioningError("tester_owner_unavailable")
    return result


def provision_or_recover_tester_owner(
    identity: auth0_flow.ValidatedIdentityEvidence,
    invitation,
    *,
    environment: Mapping[str, str],
    authority_reader,
    now: int,
    current_result: CurrentAccountAuthorityResult | None = None,
    repository_factory: RepositoryFactory | None = None,
    random_bytes: RandomBytes = secrets.token_bytes,
) -> CurrentAccountAuthorityResult:
    _validate_inputs(identity, invitation, now)

    result = current_result
    if result is None:
        result = _read_exact(authority_reader, identity, invitation)

    if (
        type(result) is CurrentAccountAuthorityResult
        and result.outcome is CurrentAccountReadOutcome.FOUND
    ):
        if authority_matches_tester(result, identity, invitation):
            return result
        raise TesterOwnerProvisioningError("tester_owner_conflict")

    if (
        type(result) is not CurrentAccountAuthorityResult
        or result.outcome is not CurrentAccountReadOutcome.NOT_AUTHORIZED
        or result.authority is not None
    ):
        raise TesterOwnerProvisioningError("tester_owner_conflict")

    request = build_initial_owner_request(
        identity,
        invitation,
        now,
        random_bytes=random_bytes,
    )
    factory = repository_factory
    if factory is None:
        factory = lambda req, trusted_now: build_runtime_repository(
            environment, req, trusted_now
        )
    try:
        repository = factory(request, now)
        creation = repository.create_initial_account(request)
    except TesterOwnerProvisioningError:
        raise
    except Exception:
        creation = None

    # A write result never grants authority. Always resolve the current canonical
    # graph; this also recovers a commit whose acknowledgement or callback was lost.
    current = _read_exact(authority_reader, identity, invitation)
    if (
        current.outcome is CurrentAccountReadOutcome.FOUND
        and authority_matches_tester(current, identity, invitation)
    ):
        if (
            creation is not None
            and getattr(creation, "receipt", None) is not None
        ):
            receipt = creation.receipt
            authority = current.authority
            if (
                authority is None
                or receipt.user_id != authority.user.user_id
                or receipt.workspace_id != authority.workspace.workspace_id
                or receipt.verified_email_id
                != authority.primary_verified_email.email_id
                or receipt.authentication_identity_id
                != authority.authentication_identity.identity_id
            ):
                raise TesterOwnerProvisioningError("tester_owner_internal")
        return current

    if current.outcome in {
        CurrentAccountReadOutcome.UNAVAILABLE,
        CurrentAccountReadOutcome.INTERNAL_ERROR,
    }:
        raise TesterOwnerProvisioningError("tester_owner_unavailable")
    if creation is not None and creation.outcome in {
        account_contract.InitialAccountCreationOutcome.AMBIGUOUS,
        account_contract.InitialAccountCreationOutcome.UNAVAILABLE,
    }:
        raise TesterOwnerProvisioningError("tester_owner_unavailable")
    raise TesterOwnerProvisioningError("tester_owner_conflict")
