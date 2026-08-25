from __future__ import annotations

if __name__ != "api.collaboration.owner_authentication":
    raise ImportError(
        "Collaboration helpers must be imported as "
        "api.collaboration.owner_authentication"
    )

import importlib
from collections.abc import Callable, Mapping

from .owner_request_security import OwnerSecurityError, VerifiedOwnerAuthentication


def resolve_verified_auth0_owner(
    raw_headers: tuple[tuple[str, str], ...],
    *,
    environment: Mapping[str, str],
    now: int,
    session_store_factory: Callable[[Mapping[str, str]], object] | None = None,
    authority_factory: Callable[[Mapping[str, str]], object] | None = None,
) -> VerifiedOwnerAuthentication:
    """Mint v2 owner claims only from one revalidated Auth0 server session."""

    if (
        type(raw_headers) is not tuple
        or not isinstance(environment, Mapping)
        or type(now) is not int
        or now < 0
    ):
        raise OwnerSecurityError("authentication_unavailable")
    try:
        auth_http = importlib.import_module("api.auth.http")
        runtime = importlib.import_module("api.auth.runtime")
        session_store = importlib.import_module("api.auth.session_store")
        account_authority = importlib.import_module("api.auth.account_authority")
        resolution = runtime.resolve_authenticated_member_session(
            raw_headers,
            environment=environment,
            now=now,
            session_store_factory=(
                session_store.build_runtime_session_store
                if session_store_factory is None
                else session_store_factory
            ),
            authority_factory=(
                account_authority.build_runtime_account_authority
                if authority_factory is None
                else authority_factory
            ),
        )
    except Exception as error:
        try:
            boundary_failure = type(error) is auth_http.HttpBoundaryError
        except Exception:
            boundary_failure = False
        raise OwnerSecurityError(
            "authentication_required"
            if boundary_failure
            else "authentication_unavailable"
        ) from None

    if resolution.outcome is runtime.MemberResolutionOutcome.UNAUTHENTICATED:
        raise OwnerSecurityError("authentication_required")
    if resolution.outcome is not runtime.MemberResolutionOutcome.AUTHENTICATED:
        raise OwnerSecurityError("authentication_unavailable")

    trusted = resolution.session
    if type(trusted) is not runtime.AuthenticatedMemberSessionContext:
        raise OwnerSecurityError("authentication_unavailable")
    member = trusted.member
    if (
        type(member) is not runtime.AuthenticatedMemberContext
        or member.auth_source != "auth0"
        or member.user_type != "member"
    ):
        raise OwnerSecurityError("authentication_required")
    try:
        return VerifiedOwnerAuthentication(
            issuer=trusted.issuer,
            authentication_version=trusted.authentication_version,
            subject=trusted.subject,
            owner_email=member.email,
            workspace_id=member.workspace_id,
            display_name=member.name,
            session_id=trusted.session_id,
            credential_digest=trusted.credential_digest,
            issued_at=trusted.issued_at,
            expires_at=trusted.expires_at,
        )
    except (TypeError, ValueError):
        raise OwnerSecurityError("authentication_unavailable") from None


__all__ = ("resolve_verified_auth0_owner",)
