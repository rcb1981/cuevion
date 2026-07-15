"""Inactive authenticated-session contracts for Cuevion accounts.

This module contains no resolver, storage, provider, HTTP, or credential-parsing
implementation.  It only defines the process-internal capability that a future
trusted resolver may mint after validating persistent account records.
"""

import sys as _sys

if __name__ != "api.auth.session_contract" or __package__ != "api.auth":
    raise ImportError("session contract requires its canonical import identity")
if (
    getattr(_sys.modules.get("api.auth.session_contract"), "__dict__", None)
    is not globals()
):
    raise ImportError("session contract requires its canonical module object")
if "_AUTH_A_SESSION_CONTRACT_INITIALIZED" in globals():
    raise ImportError("session contract cannot be initialized more than once")
_AUTH_A_SESSION_CONTRACT_INITIALIZED = True

from enum import Enum as _Enum
from enum import EnumMeta as _EnumMeta
from typing import NoReturn as _NoReturn
from typing import Protocol as _Protocol

from . import models as _models


if _models is not _sys.modules.get("api.auth.models"):
    raise ImportError("models require their canonical import identity")


__all__ = (
    "SessionResolutionReason",
    "SessionResolutionError",
    "raise_session_resolution_error",
    "get_session_resolution_reason",
    "AuthenticatedAccountSession",
    "AccountRecordRepository",
    "SessionRecordRepository",
    "AuthenticatedSessionResolver",
)


_ENUM_VALUE_MISSING = object()


class _StrictStringEnumMeta(_EnumMeta):
    """Accept exact strings or exact members without reflecting bad values."""

    def __call__(
        cls,
        value: object = _ENUM_VALUE_MISSING,
        *arguments: object,
        **keywords: object,
    ) -> object:
        if arguments or keywords:
            raise ValueError("invalid session resolution reason")
        if type(value) is cls:
            return value
        if type(value) is str:
            for member in cls:
                if str.__eq__(member.value, value):
                    return member
        raise ValueError("invalid session resolution reason")


class SessionResolutionReason(str, _Enum, metaclass=_StrictStringEnumMeta):
    """Closed, non-sensitive reasons a session resolver may report."""

    AUTHENTICATION_REQUIRED = "authentication_required"
    AUTHENTICATION_UNAVAILABLE = "authentication_unavailable"
    INTERNAL_ERROR = "internal_error"


_SESSION_RESOLUTION_ERROR_CONSTRUCTION_SENTINEL = object()
_SESSION_RESOLUTION_ERROR_CONSTRUCTION_FAILURE = (
    "session resolution errors require the supported raising function"
)
_SESSION_RESOLUTION_REASON_FAILURE = "session resolution reason must be exact"


class SessionResolutionError(Exception):
    """A fixed, value-free authenticated-session resolution failure."""

    __slots__ = ("reason",)

    def __new__(
        cls,
        construction_sentinel: object = _ENUM_VALUE_MISSING,
        *_arguments: object,
        **_keywords: object,
    ) -> "SessionResolutionError":
        if (
            cls is not SessionResolutionError
            or construction_sentinel
            is not _SESSION_RESOLUTION_ERROR_CONSTRUCTION_SENTINEL
        ):
            raise TypeError(_SESSION_RESOLUTION_ERROR_CONSTRUCTION_FAILURE)
        return Exception.__new__(cls)

    def __init__(
        self,
        construction_sentinel: object,
        reason: object = _ENUM_VALUE_MISSING,
        *_arguments: object,
        **_keywords: object,
    ) -> None:
        object.__setattr__(
            self,
            "reason",
            reason
            if (
                construction_sentinel
                is _SESSION_RESOLUTION_ERROR_CONSTRUCTION_SENTINEL
                and type(reason) is SessionResolutionReason
            )
            else SessionResolutionReason.INTERNAL_ERROR,
        )
        Exception.__init__(self)

    def __str__(self) -> str:
        return "authenticated session resolution failed"

    def __repr__(self) -> str:
        return "SessionResolutionError()"


def raise_session_resolution_error(
    reason: SessionResolutionReason,
) -> _NoReturn:
    """Raise one exact, value-free error without retaining caller context."""

    if type(reason) is not SessionResolutionReason:
        raise TypeError(_SESSION_RESOLUTION_REASON_FAILURE)
    error = SessionResolutionError(
        _SESSION_RESOLUTION_ERROR_CONSTRUCTION_SENTINEL,
        reason,
    )
    try:
        raise error
    finally:
        object.__setattr__(error, "__context__", None)
        object.__setattr__(error, "__cause__", None)


def get_session_resolution_reason(error: object) -> SessionResolutionReason:
    """Return one exact safe reason, normalizing every malformed input."""

    if type(error) is not SessionResolutionError:
        return SessionResolutionReason.INTERNAL_ERROR
    candidate: object = SessionResolutionReason.INTERNAL_ERROR
    try:
        candidate = object.__getattribute__(error, "reason")
        arguments = object.__getattribute__(error, "args")
        cause = object.__getattribute__(error, "__cause__")
        context = object.__getattribute__(error, "__context__")
    except Exception:
        return SessionResolutionReason.INTERNAL_ERROR
    return (
        candidate
        if (
            type(candidate) is SessionResolutionReason
            and type(arguments) is tuple
            and len(arguments) == 0
            and cause is None
            and context is None
        )
        else SessionResolutionReason.INTERNAL_ERROR
    )


def _safe_session_resolution_reason(error: object) -> SessionResolutionReason:
    """Package-local spelling of the fixed safe reason extractor."""

    return get_session_resolution_reason(error)


_CAPABILITY_FACTORY_SENTINEL = object()
_CAPABILITY_CONSTRUCTION_ERROR = "authenticated session construction is unavailable"
_CAPABILITY_MUTATION_ERROR = "authenticated sessions are immutable"
_CAPABILITY_PICKLE_ERROR = "authenticated sessions cannot be serialized"


class AuthenticatedAccountSession:
    """Opaque, immutable evidence of a validated authenticated account session.

    The capability is deliberately process-internal.  Its opacity prevents
    accidental construction and serialization by ordinary adapters; it is not
    a boundary against arbitrary hostile Python code already executing in the
    same process.
    """

    __slots__ = (
        "_user_id",
        "_owner_email",
        "_display_name",
        "_authentication_issuer",
        "_authentication_subject",
        "_authentication_method",
        "_session_id",
        "_credential_binding_digest",
        "_authenticated_at",
        "_issued_at",
        "_expires_at",
        "_security_epoch",
    )

    def __new__(
        cls, factory_sentinel: object = None, **_values: object
    ) -> "AuthenticatedAccountSession":
        if (
            cls is not AuthenticatedAccountSession
            or factory_sentinel is not _CAPABILITY_FACTORY_SENTINEL
        ):
            raise TypeError(_CAPABILITY_CONSTRUCTION_ERROR)
        return object.__new__(cls)

    def __init__(
        self,
        factory_sentinel: object,
        *,
        user_id: str,
        owner_email: str,
        display_name: str,
        authentication_issuer: str,
        authentication_subject: str,
        authentication_method: _models.AuthenticationMethod,
        session_id: str,
        credential_binding_digest: str,
        authenticated_at: int,
        issued_at: int,
        expires_at: int,
        security_epoch: int,
    ) -> None:
        if factory_sentinel is not _CAPABILITY_FACTORY_SENTINEL:
            raise TypeError(_CAPABILITY_CONSTRUCTION_ERROR)
        object.__setattr__(self, "_user_id", user_id)
        object.__setattr__(self, "_owner_email", owner_email)
        object.__setattr__(self, "_display_name", display_name)
        object.__setattr__(self, "_authentication_issuer", authentication_issuer)
        object.__setattr__(self, "_authentication_subject", authentication_subject)
        object.__setattr__(self, "_authentication_method", authentication_method)
        object.__setattr__(self, "_session_id", session_id)
        object.__setattr__(
            self, "_credential_binding_digest", credential_binding_digest
        )
        object.__setattr__(self, "_authenticated_at", authenticated_at)
        object.__setattr__(self, "_issued_at", issued_at)
        object.__setattr__(self, "_expires_at", expires_at)
        object.__setattr__(self, "_security_epoch", security_epoch)

    def __init_subclass__(cls, **_keywords: object) -> None:
        raise TypeError(_CAPABILITY_CONSTRUCTION_ERROR)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError(_CAPABILITY_MUTATION_ERROR)

    def __delattr__(self, _name: str) -> None:
        raise AttributeError(_CAPABILITY_MUTATION_ERROR)

    def __repr__(self) -> str:
        return "<AuthenticatedAccountSession>"

    def __str__(self) -> str:
        return "AuthenticatedAccountSession"

    def __copy__(self) -> "AuthenticatedAccountSession":
        return self

    def __deepcopy__(self, _memo: object) -> "AuthenticatedAccountSession":
        return self

    def __reduce__(self) -> object:
        raise TypeError(_CAPABILITY_PICKLE_ERROR)

    def __reduce_ex__(self, _protocol: object) -> object:
        raise TypeError(_CAPABILITY_PICKLE_ERROR)

    def __getstate__(self) -> object:
        raise TypeError(_CAPABILITY_PICKLE_ERROR)

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def owner_email(self) -> str:
        return self._owner_email

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def authentication_issuer(self) -> str:
        return self._authentication_issuer

    @property
    def authentication_subject(self) -> str:
        return self._authentication_subject

    @property
    def authentication_method(self) -> _models.AuthenticationMethod:
        return self._authentication_method

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def credential_binding_digest(self) -> str:
        return self._credential_binding_digest

    @property
    def authenticated_at(self) -> int:
        return self._authenticated_at

    @property
    def issued_at(self) -> int:
        return self._issued_at

    @property
    def expires_at(self) -> int:
        return self._expires_at

    @property
    def security_epoch(self) -> int:
        return self._security_epoch


def _mint_authenticated_account_session(
    user: _models.CuevionUser,
    primary_email: _models.VerifiedEmail,
    identity: _models.AuthenticationIdentity,
    session: _models.StoredSessionSnapshot,
    now: int,
) -> AuthenticatedAccountSession:
    """Mint a capability from exact, mutually consistent active records."""

    exact_types = (
        type(user) is _models.CuevionUser
        and type(primary_email) is _models.VerifiedEmail
        and type(identity) is _models.AuthenticationIdentity
        and type(session) is _models.StoredSessionSnapshot
        and type(now) is int
    )
    values: tuple[object, ...] | None = None
    failure_reason: SessionResolutionReason | None = (
        None if exact_types else SessionResolutionReason.AUTHENTICATION_REQUIRED
    )
    if exact_types:
        try:
            _models.validate_user_primary_email(user, primary_email)
            _models.validate_identity_for_user(identity, user, primary_email)
            _models.validate_session_snapshot(session, user, identity, now)
            values = (
                user.user_id,
                primary_email.canonical_email,
                user.display_name,
                identity.issuer,
                identity.subject,
                identity.method,
                session.session_id,
                session.credential_binding_digest,
                session.authenticated_at,
                session.issued_at,
                session.absolute_expires_at,
                session.security_epoch,
            )
        except _models.ModelValidationError:
            failure_reason = SessionResolutionReason.AUTHENTICATION_REQUIRED
        except Exception:
            failure_reason = SessionResolutionReason.INTERNAL_ERROR
    if failure_reason is not None or values is None:
        raise_session_resolution_error(
            failure_reason or SessionResolutionReason.INTERNAL_ERROR
        )

    return AuthenticatedAccountSession(
        _CAPABILITY_FACTORY_SENTINEL,
        user_id=values[0],
        owner_email=values[1],
        display_name=values[2],
        authentication_issuer=values[3],
        authentication_subject=values[4],
        authentication_method=values[5],
        session_id=values[6],
        credential_binding_digest=values[7],
        authenticated_at=values[8],
        issued_at=values[9],
        expires_at=values[10],
        security_epoch=values[11],
    )


class AccountRecordRepository(_Protocol):
    """Read-only account-record access required by a future resolver.

    Returning ``None`` means an authoritative, successful lookup found no
    record.  A future adapter must never represent storage unavailability as
    ``None``: outages must use ``raise_session_resolution_error`` with
    ``SessionResolutionReason.AUTHENTICATION_UNAVAILABLE``.  Persisted
    invariant corruption or an unexpected internal failure must use the same
    function with ``SessionResolutionReason.INTERNAL_ERROR``.

    Repository failures must remain fixed and value-free.  They must not
    include user, email, identity, workspace, or session IDs, credential
    lookup digests, or private storage details.  The
    ``AUTHENTICATION_REQUIRED`` reason is reserved for authoritative absence
    or invalid, revoked, or expired authentication, never infrastructure
    outage.
    """

    def get_user(self, user_id: str) -> _models.CuevionUser | None:
        ...

    def get_verified_email(self, email_id: str) -> _models.VerifiedEmail | None:
        ...

    def get_authentication_identity(
        self, identity_id: str
    ) -> _models.AuthenticationIdentity | None:
        ...

    def get_workspace(self, workspace_id: str) -> _models.Workspace | None:
        ...

    def get_workspace_membership(
        self, workspace_id: str, user_id: str
    ) -> _models.WorkspaceMembership | None:
        ...


class SessionRecordRepository(_Protocol):
    """Read-only session-record access required by a future resolver.

    The repository receives only a canonical lookup digest derived by the
    trusted resolver from the raw request credential with a server-only lookup
    key and dedicated lookup domain.  It must not parse headers or cookies, and
    no raw cookie or header value may reach it.  There is no binding-digest
    lookup operation.

    Returning ``None`` means an authoritative, successful lookup found no
    record.  Storage unavailability must never be returned as ``None``; a
    future adapter must call ``raise_session_resolution_error`` with
    ``SessionResolutionReason.AUTHENTICATION_UNAVAILABLE``.  Persisted
    invariant corruption or an unexpected internal failure must instead use
    ``SessionResolutionReason.INTERNAL_ERROR`` through that same function.

    Repository failures must remain fixed and value-free.  They must not
    contain user, email, identity, workspace, or session IDs, credential lookup
    digests, or private storage details.  ``AUTHENTICATION_REQUIRED`` means
    authoritative absence or invalid, revoked, or expired authentication,
    never infrastructure outage.
    """

    def get_session_by_lookup_digest(
        self, credential_lookup_digest: str
    ) -> _models.StoredSessionSnapshot | None:
        ...


class AuthenticatedSessionResolver(_Protocol):
    """Trusted future boundary for untrusted request credential input.

    ``raw_headers`` is the original ordered tuple of header-name/value pairs
    received from the reviewed HTTP boundary.  It preserves header order and
    duplicate occurrences so the resolver can reject ambiguous input.  The
    tuple conveys no trust: a future implementation must validate the exact
    container and element types before using them, and ``now`` must be an exact
    integer.

    The resolver is the trusted parsing and derivation boundary.  It must parse
    the production session credential itself and reject missing, malformed,
    duplicate, ambiguous, oversized, or otherwise noncanonical credential or
    header representations.  It must not accept a lookup digest or binding
    digest supplied by browser or request data.  It derives the lookup digest
    with a server-only lookup key and dedicated lookup domain, and independently
    derives or verifies the expected binding digest with a different
    server-only binding key and distinct binding domain.  Only the
    resolver-derived canonical lookup digest may be passed to
    ``SessionRecordRepository.get_session_by_lookup_digest``.  The future
    reviewed session implementation must compare the authoritative stored
    binding digest with the independently derived expected value in constant
    time.

    The resolver must never expose or return the raw session cookie, complete
    Cookie header values, lookup keys, binding keys, raw provider credentials,
    or raw mailbox credentials.  It must never log or persist raw request
    credentials.

    Missing, malformed, ambiguous, expired, revoked, or authoritatively absent
    authentication maps to ``AUTHENTICATION_REQUIRED``.  A session or account
    authority outage maps to ``AUTHENTICATION_UNAVAILABLE``.  Persisted
    invariant corruption or an unexpected internal failure maps to
    ``INTERNAL_ERROR``.  Fixed failures must use
    ``raise_session_resolution_error``.

    There is no beta-session, mailbox OAuth, IMAP, localStorage, stateless, or
    workspace-selection fallback.  The resolver authenticates only; workspace
    membership and authorization remain separate future boundaries.  This
    contract provides no resolver, parser, digest derivation, key access,
    repository, or storage implementation.
    """

    def resolve_authenticated_session(
        self,
        raw_headers: tuple[tuple[str, str], ...],
        now: int,
    ) -> AuthenticatedAccountSession:
        """Resolve untrusted request headers or raise a fixed failure."""

        ...
