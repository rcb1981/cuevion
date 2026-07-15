"""Inactive generation of unpersisted Cuevion account-record ID candidates.

This provider-independent module generates canonical candidate strings only.  It
performs no persistence, uniqueness check, authorization, session work, logging,
environment access, clock access, network access, or feature activation.
"""

import sys as _sys


if (
    __name__ != "cuevion_auth.account_record_ids"
    or __package__ != "cuevion_auth"
):
    raise ImportError("account record IDs require their canonical import identity")
if (
    getattr(
        _sys.modules.get("cuevion_auth.account_record_ids"),
        "__dict__",
        None,
    )
    is not globals()
):
    raise ImportError("account record IDs require their canonical module object")
if "_AUTH_B1B_ACCOUNT_RECORD_IDS_INITIALIZED" in globals():
    raise ImportError("account record IDs cannot be initialized more than once")
_AUTH_B1B_ACCOUNT_RECORD_IDS_INITIALIZED = True

import base64 as _base64
import secrets as _secrets


__all__ = (
    "RecordIdentifierGenerationError",
    "generate_user_id_candidate",
    "generate_verified_email_id_candidate",
    "generate_authentication_identity_id_candidate",
    "generate_workspace_id_candidate",
)


_USER_ID_PREFIX = "usr_"
_VERIFIED_EMAIL_ID_PREFIX = "vem_"
_AUTHENTICATION_IDENTITY_ID_PREFIX = "aid_"
_WORKSPACE_ID_PREFIX = "wsp_"
_ENTROPY_BYTE_LENGTH = 16
_ENCODED_SUFFIX_LENGTH = 22
_BASE64URL_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789-_"
)

_token_bytes = _secrets.token_bytes

_GENERATION_FAILURE = object()
_ERROR_CONSTRUCTION_SENTINEL = object()
_ERROR_CONSTRUCTION_FAILURE = (
    "record identifier generation errors require the supported raising function"
)


class RecordIdentifierGenerationError(RuntimeError):
    """A fixed, value-free failure for account-record ID candidate generation."""

    __slots__ = ()

    def __new__(
        cls,
        construction_sentinel: object = None,
        *_arguments: object,
        **_keywords: object,
    ) -> "RecordIdentifierGenerationError":
        if (
            cls is not RecordIdentifierGenerationError
            or construction_sentinel is not _ERROR_CONSTRUCTION_SENTINEL
        ):
            raise TypeError(_ERROR_CONSTRUCTION_FAILURE)
        return RuntimeError.__new__(cls)

    def __init__(
        self,
        construction_sentinel: object,
        *_arguments: object,
        **_keywords: object,
    ) -> None:
        if construction_sentinel is not _ERROR_CONSTRUCTION_SENTINEL:
            raise TypeError(_ERROR_CONSTRUCTION_FAILURE)
        RuntimeError.__init__(self)

    def __init_subclass__(cls, **_keywords: object) -> None:
        raise TypeError(_ERROR_CONSTRUCTION_FAILURE)

    @property
    def args(self) -> tuple[object, ...]:
        return ()

    @args.setter
    def args(self, _value: object) -> None:
        return None

    def __str__(self) -> str:
        return "account record identifier generation failed"

    def __repr__(self) -> str:
        return "RecordIdentifierGenerationError()"


def _raise_generation_error() -> None:
    """Raise one fresh fixed error without retaining an underlying exception."""

    error = RecordIdentifierGenerationError(_ERROR_CONSTRUCTION_SENTINEL)
    try:
        raise error
    finally:
        object.__setattr__(error, "__context__", None)
        object.__setattr__(error, "__cause__", None)


def _encode_base64url(value: bytes) -> str:
    encoded = _base64.urlsafe_b64encode(value)
    if type(encoded) is not bytes:
        raise TypeError("internal account record ID encoding failed")
    return encoded.rstrip(b"=").decode("ascii")


def _is_canonical_candidate(value: object, prefix: str) -> bool:
    if (
        type(value) is not str
        or len(value) != len(prefix) + _ENCODED_SUFFIX_LENGTH
        or not value.startswith(prefix)
    ):
        return False
    suffix = value[len(prefix) :]
    if (
        len(suffix) != _ENCODED_SUFFIX_LENGTH
        or not suffix.isascii()
        or any(character not in _BASE64URL_CHARACTERS for character in suffix)
    ):
        return False
    decoded = _base64.b64decode(
        suffix.encode("ascii") + b"==",
        altchars=b"-_",
        validate=True,
    )
    return (
        type(decoded) is bytes
        and len(decoded) == _ENTROPY_BYTE_LENGTH
        and _encode_base64url(decoded) == suffix
    )


def _generate_candidate_worker(prefix: str) -> object:
    """Return one exact candidate or a fixed non-sensitive failure sentinel."""

    try:
        entropy = _token_bytes(_ENTROPY_BYTE_LENGTH)
        if type(entropy) is not bytes or len(entropy) != _ENTROPY_BYTE_LENGTH:
            return _GENERATION_FAILURE
        suffix = _encode_base64url(entropy)
        if type(suffix) is not str or len(suffix) != _ENCODED_SUFFIX_LENGTH:
            return _GENERATION_FAILURE
        candidate = prefix + suffix
        if not _is_canonical_candidate(candidate, prefix):
            return _GENERATION_FAILURE
    except Exception:
        return _GENERATION_FAILURE
    return candidate


def generate_user_id_candidate() -> str:
    """Return one unpersisted canonical Cuevion user ID candidate."""

    candidate = _generate_candidate_worker(_USER_ID_PREFIX)
    if type(candidate) is str:
        return candidate
    del candidate
    _raise_generation_error()


def generate_verified_email_id_candidate() -> str:
    """Return one unpersisted canonical verified-email ID candidate."""

    candidate = _generate_candidate_worker(_VERIFIED_EMAIL_ID_PREFIX)
    if type(candidate) is str:
        return candidate
    del candidate
    _raise_generation_error()


def generate_authentication_identity_id_candidate() -> str:
    """Return one unpersisted canonical authentication-identity ID candidate."""

    candidate = _generate_candidate_worker(_AUTHENTICATION_IDENTITY_ID_PREFIX)
    if type(candidate) is str:
        return candidate
    del candidate
    _raise_generation_error()


def generate_workspace_id_candidate() -> str:
    """Return one unpersisted canonical workspace ID candidate."""

    candidate = _generate_candidate_worker(_WORKSPACE_ID_PREFIX)
    if type(candidate) is str:
        return candidate
    del candidate
    _raise_generation_error()
