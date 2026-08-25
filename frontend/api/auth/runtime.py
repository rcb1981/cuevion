"""HTTP-independent composition for the parallel Auth0 authentication lane."""

from __future__ import annotations

import os
import re
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from urllib.parse import parse_qsl, urlsplit

from api.auth import account_authority, auth0_flow, http, session_store
from api.auth import models
from cuevion_auth.current_account_repository_contract import (
    AuthenticationIdentityLookupKey,
    CurrentAccountReadOutcome,
)


_CALLBACK_PATH = "/api/auth/callback"
_CALLBACK_QUERY_MAX_BYTES = 4096
_AUTH_CODE_RE = re.compile(r"[!-~]{1,2048}")
_LOGIN_ERROR_LOCATION = "/login?error=authentication_failed"
_APP_LOCATION = "/"


class MemberResolutionOutcome(str, Enum):
    """Closed outcomes for the ordinary-member authentication boundary."""

    AUTHENTICATED = "authenticated"
    UNAUTHENTICATED = "unauthenticated"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class AuthenticatedMemberContext:
    """Canonical, non-secret member context from the current account graph."""

    user_id: str
    email: str
    name: str
    workspace_id: str
    membership_role: str
    user_type: str = "member"
    auth_source: str = "auth0"

    def __post_init__(self) -> None:
        if (
            type(self.user_id) is not str
            or not self.user_id
            or type(self.email) is not str
            or not self.email
            or type(self.name) is not str
            or not self.name
            or type(self.workspace_id) is not str
            or not self.workspace_id
            or type(self.membership_role) is not str
            or not self.membership_role
            or self.user_type != "member"
            or self.auth_source != "auth0"
        ):
            raise ValueError("invalid authenticated member context")


@dataclass(frozen=True, slots=True, repr=False)
class AuthenticatedMemberSessionContext:
    """Revalidated server-session facts retained for trusted server adapters."""

    member: AuthenticatedMemberContext
    authentication_version: int
    issuer: str
    subject: str
    session_id: str
    credential_digest: str
    issued_at: int
    expires_at: int

    def __post_init__(self) -> None:
        if (
            type(self.member) is not AuthenticatedMemberContext
            or type(self.authentication_version) is not int
            or self.authentication_version != session_store.SESSION_SCHEMA_VERSION
            or type(self.issuer) is not str
            or not self.issuer
            or type(self.subject) is not str
            or not self.subject
            or type(self.session_id) is not str
            or not self.session_id
            or type(self.credential_digest) is not str
            or not self.credential_digest
            or type(self.issued_at) is not int
            or type(self.expires_at) is not int
            or not 0 <= self.issued_at < self.expires_at
        ):
            raise ValueError("invalid authenticated member session context")

    def __repr__(self) -> str:
        return "<AuthenticatedMemberSessionContext>"

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class AuthenticatedMemberResolution:
    """Fail-closed ordinary-member resolution plus any cookie invalidation."""

    outcome: MemberResolutionOutcome
    member: AuthenticatedMemberContext | None
    set_cookies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        authenticated = self.outcome is MemberResolutionOutcome.AUTHENTICATED
        if (
            type(self.outcome) is not MemberResolutionOutcome
            or authenticated != (type(self.member) is AuthenticatedMemberContext)
            or type(self.set_cookies) is not tuple
            or any(type(cookie) is not str or not cookie for cookie in self.set_cookies)
        ):
            raise ValueError("invalid authenticated member resolution")


@dataclass(frozen=True, slots=True)
class AuthenticatedMemberSessionResolution:
    """Fail-closed resolution retaining non-secret server-session bindings."""

    outcome: MemberResolutionOutcome
    session: AuthenticatedMemberSessionContext | None
    set_cookies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        authenticated = self.outcome is MemberResolutionOutcome.AUTHENTICATED
        if (
            type(self.outcome) is not MemberResolutionOutcome
            or authenticated
            != (type(self.session) is AuthenticatedMemberSessionContext)
            or type(self.set_cookies) is not tuple
            or any(type(cookie) is not str or not cookie for cookie in self.set_cookies)
        ):
            raise ValueError("invalid authenticated member session resolution")


def _member_resolution(
    outcome: MemberResolutionOutcome,
    member: AuthenticatedMemberContext | None = None,
    *,
    clear_session: bool = False,
) -> AuthenticatedMemberResolution:
    return AuthenticatedMemberResolution(
        outcome,
        member,
        (session_store.clear_session_cookie(),) if clear_session else (),
    )


def _member_session_resolution(
    outcome: MemberResolutionOutcome,
    session: AuthenticatedMemberSessionContext | None = None,
    *,
    set_cookies: tuple[str, ...] = (),
) -> AuthenticatedMemberSessionResolution:
    return AuthenticatedMemberSessionResolution(outcome, session, set_cookies)


def _authentication_unavailable_response(
    *, set_cookies: tuple[str, ...] = ()
) -> http.PublicResponse:
    return http.json_response(
        503,
        {
            "authenticated": False,
            "error": {
                "code": "authentication_unavailable",
                "message": "Sign-in is temporarily unavailable.",
            },
        },
        set_cookies=set_cookies,
    )


def _unauthenticated_response(
    *, set_cookies: tuple[str, ...] = ()
) -> http.PublicResponse:
    return http.json_response(
        401,
        {"authenticated": False},
        set_cookies=set_cookies,
    )


def _boundary_error_response(error: http.HttpBoundaryError) -> http.PublicResponse:
    return http.json_response(
        error.status,
        {
            "error": {
                "code": error.code,
                "message": "The authentication request was rejected.",
            }
        },
    )


def _parse_callback_query(raw_path: str) -> tuple[str, str]:
    if type(raw_path) is not str or len(raw_path.encode("utf-8")) > 8192:
        raise ValueError("invalid callback")
    parsed = urlsplit(raw_path)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or parsed.path != _CALLBACK_PATH
        or len(parsed.query.encode("utf-8")) > _CALLBACK_QUERY_MAX_BYTES
    ):
        raise ValueError("invalid callback")
    try:
        pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=4,
            encoding="utf-8",
            errors="strict",
        )
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("invalid callback") from None
    if len(pairs) != 2 or {name for name, _value in pairs} != {"code", "state"}:
        raise ValueError("invalid callback")
    values = {name: value for name, value in pairs}
    code = values["code"]
    state = values["state"]
    if (
        _AUTH_CODE_RE.fullmatch(code) is None
        or _AUTH_CODE_RE.fullmatch(state) is None
    ):
        raise ValueError("invalid callback")
    return code, state


def login_response(
    method: str,
    raw_headers: tuple[tuple[str, str], ...],
    *,
    environment: Mapping[str, str] | None = None,
    now: int | None = None,
    random_bytes: Callable[[int], bytes] = secrets.token_bytes,
) -> http.PublicResponse:
    try:
        http.require_method(method, "GET")
        headers = http.validate_header_pairs(raw_headers)
        http.require_canonical_host(headers)
        config = auth0_flow.parse_auth0_configuration(
            os.environ if environment is None else environment
        )
        request = auth0_flow.build_authorization_request(
            config,
            int(time.time()) if now is None else now,
            random_bytes=random_bytes,
        )
        return http.redirect_response(
            request.authorization_url,
            set_cookies=(request.transaction_cookie,),
        )
    except http.HttpBoundaryError as error:
        return _boundary_error_response(error)
    except Exception:
        return _authentication_unavailable_response()


def callback_response(
    method: str,
    raw_headers: tuple[tuple[str, str], ...],
    raw_path: str,
    *,
    environment: Mapping[str, str] | None = None,
    now: int | None = None,
    token_transport: Callable[[auth0_flow.OutboundRequest], auth0_flow.OutboundResponse] | None = None,
    jwks_transport: Callable[[auth0_flow.OutboundRequest], auth0_flow.OutboundResponse] | None = None,
    session_store_factory: Callable[[Mapping[str, str]], session_store.AuthSessionStore] = session_store.build_runtime_session_store,
    authority_factory: Callable[[Mapping[str, str]], object] = account_authority.build_runtime_account_authority,
    random_bytes: Callable[[int], bytes] = secrets.token_bytes,
) -> http.PublicResponse:
    clear_transaction = auth0_flow.clear_transaction_cookie()
    source = os.environ if environment is None else environment
    timestamp = int(time.time()) if now is None else now
    headers: tuple[tuple[str, str], ...] = ()
    try:
        http.require_method(method, "GET")
        headers = http.validate_header_pairs(raw_headers)
        http.require_canonical_host(headers)
        code, returned_state = _parse_callback_query(raw_path)
        config = auth0_flow.parse_auth0_configuration(source)
        transaction_cookie = http.read_cookie(
            headers, auth0_flow.AUTH_TRANSACTION_COOKIE_NAME
        )
        if transaction_cookie is None:
            raise ValueError("invalid callback")
        transaction = auth0_flow.consume_transaction_cookie(
            transaction_cookie,
            returned_state,
            config,
            timestamp,
        )
        secret = session_store.resolve_session_secret(source)
        store = session_store_factory(source)
        remaining_ttl = transaction.expires_at - timestamp
        if not store.consume_transaction(
            transaction.state, secret, remaining_ttl
        ):
            raise ValueError("invalid callback")
        if token_transport is None:
            token_response = auth0_flow.exchange_authorization_code(
                config, code, transaction.code_verifier
            )
        else:
            token_response = auth0_flow.exchange_authorization_code(
                config,
                code,
                transaction.code_verifier,
                transport=token_transport,
            )
        if jwks_transport is None:
            identity = auth0_flow.validate_id_token_with_jwks(
                token_response.id_token,
                config,
                transaction.nonce,
                timestamp,
            )
        else:
            identity = auth0_flow.validate_id_token_with_jwks(
                token_response.id_token,
                config,
                transaction.nonce,
                timestamp,
                transport=jwks_transport,
            )
        identity_key = AuthenticationIdentityLookupKey(
            issuer=identity.issuer,
            subject=identity.subject,
        )
        authority_reader = authority_factory(source)
        authority_result = authority_reader.resolve_current_account_by_identity(
            identity_key
        )
        if authority_result.outcome is CurrentAccountReadOutcome.UNAVAILABLE:
            raise account_authority.AccountAuthorityUnavailableError()
        if not account_authority.auth0_authority_matches(
            authority_result, identity_key, identity.email
        ):
            raise ValueError("not authorized")
        authority = authority_result.authority
        if authority is None:
            raise ValueError("not authorized")

        # Rotation is fail-closed: an existing credential must be revoked before
        # the new server-side session is published.
        session_store.revoke_request_session(
            store, headers=headers, secret=secret
        )
        _record, session_cookie = session_store.create_server_session(
            store,
            secret=secret,
            user_id=authority.user.user_id,
            workspace_id=authority.workspace.workspace_id,
            security_epoch=authority.user.security_epoch,
            issuer=identity.issuer,
            subject=identity.subject,
            now=timestamp,
            random_bytes=random_bytes,
        )
        return http.redirect_response(
            _APP_LOCATION,
            set_cookies=(clear_transaction, session_cookie),
        )
    except http.HttpBoundaryError as error:
        response = _boundary_error_response(error)
        return http.PublicResponse(
            status=response.status,
            headers=response.headers + (("Set-Cookie", clear_transaction),),
            body=response.body,
        )
    except Exception:
        return http.redirect_response(
            _LOGIN_ERROR_LOCATION,
            set_cookies=(clear_transaction,),
        )


def _revalidation_failed(
    store: session_store.AuthSessionStore,
    lookup_digest: str | None,
) -> AuthenticatedMemberResolution:
    try:
        if lookup_digest is not None:
            store.delete(lookup_digest)
    except session_store.SessionStoreUnavailable:
        return _member_resolution(
            MemberResolutionOutcome.UNAVAILABLE,
            clear_session=True,
        )
    return _member_resolution(
        MemberResolutionOutcome.UNAUTHENTICATED,
        clear_session=True,
    )


def _current_authority_member_context(
    record: session_store.ServerSessionRecord,
    authority: object,
) -> AuthenticatedMemberContext | None:
    try:
        user = authority.user  # type: ignore[attr-defined]
        email = authority.primary_verified_email  # type: ignore[attr-defined]
        workspace = authority.workspace  # type: ignore[attr-defined]
        membership = authority.workspace_membership  # type: ignore[attr-defined]
        valid = (
            type(user) is models.CuevionUser
            and type(email) is models.VerifiedEmail
            and type(workspace) is models.Workspace
            and type(membership) is models.WorkspaceMembership
            and user.user_id == record.user_id
            and workspace.workspace_id == record.workspace_id
            and user.security_epoch == record.security_epoch
            and user.status is models.UserStatus.ACTIVE
            and email.status is models.VerifiedEmailStatus.VERIFIED
            and user.primary_verified_email_id == email.email_id
            and email.user_id == user.user_id
            and workspace.status is models.WorkspaceStatus.ACTIVE
            and membership.user_id == user.user_id
            and membership.workspace_id == workspace.workspace_id
            and membership.status is models.WorkspaceMembershipStatus.ACTIVE
            and type(membership.role) is models.WorkspaceRole
        )
        if not valid:
            return None
        return AuthenticatedMemberContext(
            user_id=user.user_id,
            email=email.canonical_email,
            name=user.display_name,
            workspace_id=workspace.workspace_id,
            membership_role=membership.role.value,
        )
    except Exception:
        return None


def resolve_authenticated_member_session(
    raw_headers: tuple[tuple[str, str], ...],
    *,
    environment: Mapping[str, str] | None = None,
    now: int | None = None,
    session_store_factory: Callable[[Mapping[str, str]], session_store.AuthSessionStore] = session_store.build_runtime_session_store,
    authority_factory: Callable[[Mapping[str, str]], object] = account_authority.build_runtime_account_authority,
) -> AuthenticatedMemberSessionResolution:
    """Resolve one ordinary member and its trusted Auth0 server-session facts.

    HTTP boundary failures remain explicit ``HttpBoundaryError`` instances for
    route adapters. All trusted-runtime, session-store, and account-authority
    failures collapse to a fixed unavailable outcome without exposing details.
    """

    source = os.environ if environment is None else environment
    headers = http.validate_header_pairs(raw_headers)
    session_cookie = http.read_cookie(headers, session_store.SESSION_COOKIE_NAME)
    if session_cookie is None:
        return _member_session_resolution(MemberResolutionOutcome.UNAUTHENTICATED)

    try:
        timestamp = int(time.time()) if now is None else now
        secret = session_store.resolve_session_secret(source)
        store = session_store_factory(source)
        record, lookup_digest = session_store.load_server_session(
            store,
            headers=headers,
            secret=secret,
            now=timestamp,
        )
        if record is None:
            failed = _revalidation_failed(store, lookup_digest)
            return _member_session_resolution(
                failed.outcome,
                set_cookies=failed.set_cookies,
            )
        authority_reader = authority_factory(source)
        result = authority_reader.read_current_account_by_user(
            record.user_id, record.workspace_id
        )
        if result.outcome in (
            CurrentAccountReadOutcome.UNAVAILABLE,
            CurrentAccountReadOutcome.INTERNAL_ERROR,
        ):
            return _member_session_resolution(MemberResolutionOutcome.UNAVAILABLE)
        authority = result.authority
        if (
            result.outcome is not CurrentAccountReadOutcome.FOUND
            or authority is None
        ):
            failed = _revalidation_failed(store, lookup_digest)
            return _member_session_resolution(
                failed.outcome,
                set_cookies=failed.set_cookies,
            )
        member = _current_authority_member_context(record, authority)
        if member is None:
            failed = _revalidation_failed(store, lookup_digest)
            return _member_session_resolution(
                failed.outcome,
                set_cookies=failed.set_cookies,
            )
        return _member_session_resolution(
            MemberResolutionOutcome.AUTHENTICATED,
            AuthenticatedMemberSessionContext(
                member=member,
                authentication_version=record.schema_version,
                issuer=record.issuer,
                subject=record.subject,
                session_id=record.session_id,
                credential_digest=record.binding_digest,
                issued_at=record.created_at,
                expires_at=record.expires_at,
            ),
        )
    except (session_store.SessionStoreUnavailable, session_store.SessionConfigurationError):
        return _member_session_resolution(MemberResolutionOutcome.UNAVAILABLE)
    except Exception:
        return _member_session_resolution(MemberResolutionOutcome.UNAVAILABLE)


def resolve_authenticated_member(
    raw_headers: tuple[tuple[str, str], ...],
    *,
    environment: Mapping[str, str] | None = None,
    now: int | None = None,
    session_store_factory: Callable[[Mapping[str, str]], session_store.AuthSessionStore] = session_store.build_runtime_session_store,
    authority_factory: Callable[[Mapping[str, str]], object] = account_authority.build_runtime_account_authority,
) -> AuthenticatedMemberResolution:
    """Resolve the existing frontend-safe member view from the trusted session."""

    resolution = resolve_authenticated_member_session(
        raw_headers,
        environment=environment,
        now=now,
        session_store_factory=session_store_factory,
        authority_factory=authority_factory,
    )
    return AuthenticatedMemberResolution(
        resolution.outcome,
        resolution.session.member if resolution.session is not None else None,
        resolution.set_cookies,
    )


def session_response(
    method: str,
    raw_headers: tuple[tuple[str, str], ...],
    *,
    environment: Mapping[str, str] | None = None,
    now: int | None = None,
    session_store_factory: Callable[[Mapping[str, str]], session_store.AuthSessionStore] = session_store.build_runtime_session_store,
    authority_factory: Callable[[Mapping[str, str]], object] = account_authority.build_runtime_account_authority,
) -> http.PublicResponse:
    try:
        http.require_method(method, "GET")
        headers = http.validate_header_pairs(raw_headers)
        http.require_canonical_host(headers)
        resolution = resolve_authenticated_member(
            headers,
            environment=environment,
            now=now,
            session_store_factory=session_store_factory,
            authority_factory=authority_factory,
        )
    except http.HttpBoundaryError as error:
        return _boundary_error_response(error)
    except Exception:
        return _authentication_unavailable_response()

    if resolution.outcome is MemberResolutionOutcome.UNAUTHENTICATED:
        return _unauthenticated_response(set_cookies=resolution.set_cookies)
    if resolution.outcome is MemberResolutionOutcome.UNAVAILABLE:
        return _authentication_unavailable_response(set_cookies=resolution.set_cookies)
    member = resolution.member
    if member is None:
        return _authentication_unavailable_response()
    return http.json_response(
        200,
        {
            "authenticated": True,
            "authSource": member.auth_source,
            "userId": member.user_id,
            "workspaceId": member.workspace_id,
            "email": member.email,
            "name": member.name,
            "userType": member.user_type,
        },
    )


def logout_response(
    method: str,
    raw_headers: tuple[tuple[str, str], ...],
    *,
    environment: Mapping[str, str] | None = None,
    session_store_factory: Callable[[Mapping[str, str]], session_store.AuthSessionStore] = session_store.build_runtime_session_store,
) -> http.PublicResponse:
    source = os.environ if environment is None else environment
    try:
        http.require_method(method, "POST")
        headers = http.validate_header_pairs(raw_headers)
        http.require_canonical_host(headers)
        http.require_same_origin(headers)
        session_cookie = http.read_cookie(
            headers, session_store.SESSION_COOKIE_NAME
        )
    except http.HttpBoundaryError as error:
        return _boundary_error_response(error)

    clear_cookies = (
        session_store.clear_session_cookie(),
        auth0_flow.clear_transaction_cookie(),
    )
    try:
        if session_cookie is not None:
            secret = session_store.resolve_session_secret(source)
            store = session_store_factory(source)
            session_store.revoke_request_session(
                store, headers=headers, secret=secret
            )
        config = auth0_flow.parse_auth0_configuration(source)
        logout_url = auth0_flow.build_logout_url(config)
        return http.json_response(
            200,
            {"ok": True, "logoutUrl": logout_url},
            set_cookies=clear_cookies,
        )
    except Exception:
        return http.json_response(
            503,
            {
                "error": {
                    "code": "authentication_unavailable",
                    "message": "Sign-out could not be completed.",
                }
            },
            set_cookies=clear_cookies,
        )


__all__ = (
    "AuthenticatedMemberContext",
    "AuthenticatedMemberResolution",
    "AuthenticatedMemberSessionContext",
    "AuthenticatedMemberSessionResolution",
    "MemberResolutionOutcome",
    "resolve_authenticated_member",
    "resolve_authenticated_member_session",
    "login_response",
    "callback_response",
    "session_response",
    "logout_response",
)
