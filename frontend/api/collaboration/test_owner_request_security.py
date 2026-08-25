from __future__ import annotations

import base64
import copy
import dataclasses
import hashlib
import hmac
import json
import pickle
import subprocess
import sys
import unittest
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from . import owner_request_security as security
from .owner_request_security import (
    OwnerRequestContext,
    OwnerSecurityConfiguration,
    OwnerSecurityError,
    VerifiedOwnerAuthentication,
    derive_mailbox_allowlist_entry,
    derive_owner_allowlist_entry,
    issue_owner_csrf_token,
    mailbox_is_allowlisted,
    normalize_owner_security_failure,
    owner_is_allowlisted,
    parse_owner_csrf_header,
    parse_owner_security_configuration,
    parse_trusted_owner_origin,
    resolve_owner_request_context,
    validate_owner_mutation_origin,
    verify_owner_csrf_token,
)


NOW = 1_800_000_000
ORIGIN = "https://app.cuevion.com"
CURRENT_KEY = b"current-owner-csrf-key-material-0001"
PREVIOUS_KEY = b"previous-owner-csrf-key-material-01"
ALLOWLIST_KEY = b"separate-allowlist-key-material-0001"
MAILBOX_ID = "primary.mailbox"
WORKSPACE_ID = "wsp_" + ("w" * 22)

_SIGNING_LABEL = b"cuevion/collaboration-v2/owner-csrf/signing/v1"
_OWNER_DOMAIN = b"cuevion/collaboration-v2/owner-allowlist/v1\x00"
_MAILBOX_DOMAIN = b"cuevion/collaboration-v2/mailbox-allowlist/v1\x00"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + ("=" * ((-len(value)) % 4)))


def _noncanonical_base64url_alias(value: str) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    last_index = alphabet.index(value[-1])
    alias = value[:-1] + alphabet[last_index + 1]
    if _unb64(alias) != _unb64(value) or alias == value:
        raise AssertionError("test alias must preserve decoded bytes")
    return alias


def _claims(**updates: object) -> VerifiedOwnerAuthentication:
    values: dict[str, object] = {
        "issuer": "cuevion-auth-v1",
        "authentication_version": 1,
        "subject": "provider:user_0123456789",
        "owner_email": "owner@example.com",
        "workspace_id": WORKSPACE_ID,
        "display_name": "Owner Example",
        "session_id": _b64(b"session-id-entropy"),
        "credential_digest": _b64(hashlib.sha256(b"credential").digest()),
        "issued_at": NOW - 60,
        "expires_at": NOW + 3_600,
    }
    values.update(updates)
    return VerifiedOwnerAuthentication(**values)  # type: ignore[arg-type]


def _context(**updates: object) -> OwnerRequestContext:
    claims = _claims(**updates)
    return resolve_owner_request_context(
        [("Authorization", "provider-owned")],
        authentication_resolver=lambda _headers: claims,
        now=NOW,
    )


def _framed(domain: bytes, values: tuple[str, ...]) -> bytes:
    result = bytearray(domain)
    for value in values:
        encoded = value.encode("ascii")
        result.extend(len(encoded).to_bytes(4, "big"))
        result.extend(encoded)
    return bytes(result)


def _allowlist_entry(domain: bytes, values: tuple[str, ...]) -> str:
    digest = hmac.new(ALLOWLIST_KEY, _framed(domain, values), hashlib.sha256).digest()
    return "v1_" + _b64(digest)


def _owner_entry(
    issuer: str = "cuevion-auth-v1",
    version: int = 1,
    subject: str = "provider:user_0123456789",
) -> str:
    return _allowlist_entry(_OWNER_DOMAIN, (issuer, str(version), subject))


def _mailbox_entry(
    mailbox_id: str = MAILBOX_ID,
    issuer: str = "cuevion-auth-v1",
    version: int = 1,
    subject: str = "provider:user_0123456789",
) -> str:
    return _allowlist_entry(
        _MAILBOX_DOMAIN,
        (issuer, str(version), subject, mailbox_id),
    )


def _trusted_configuration(
    *,
    current_key: bytes = CURRENT_KEY,
    previous_key: bytes | None = None,
    owner_entries: tuple[str, ...] | None = None,
    mailbox_entries: tuple[str, ...] | None = None,
) -> dict[str, str]:
    values = {
        "CUEVION_APP_ORIGIN": ORIGIN,
        "CUEVION_COLLAB_V2_OWNER_CSRF_KEY": _b64(current_key),
        "CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY": _b64(ALLOWLIST_KEY),
        "CUEVION_COLLAB_V2_OWNER_ALLOWLIST": ",".join(
            owner_entries or ("v1_" + _b64(b"o" * 32),)
        ),
        "CUEVION_COLLAB_V2_MAILBOX_ALLOWLIST": ",".join(
            mailbox_entries or ("v1_" + _b64(b"m" * 32),)
        ),
    }
    if previous_key is not None:
        values["CUEVION_COLLAB_V2_OWNER_CSRF_KEY_PREVIOUS"] = _b64(previous_key)
    return values


def _configuration(**kwargs: object) -> OwnerSecurityConfiguration:
    return parse_owner_security_configuration(_trusted_configuration(**kwargs))


def _payload(token: str) -> dict[str, object]:
    return json.loads(_unb64(token.split(".")[1]).decode("utf-8"))


def _signed_token_from_text(text: bytes, key: bytes = CURRENT_KEY) -> str:
    payload_segment = _b64(text)
    signing_key = hmac.new(key, _SIGNING_LABEL, hashlib.sha256).digest()
    signature = hmac.new(
        signing_key,
        ("oc1." + payload_segment).encode("ascii"),
        hashlib.sha256,
    ).digest()
    return "oc1." + payload_segment + "." + _b64(signature)


def _signed_payload(payload: dict[str, object], key: bytes = CURRENT_KEY) -> str:
    text = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _signed_token_from_text(text, key)


def _assert_security_failure(
    testcase: unittest.TestCase,
    callback: object,
    reason: str,
) -> OwnerSecurityError:
    with testcase.assertRaises(OwnerSecurityError) as raised:
        callback()  # type: ignore[operator]
    testcase.assertEqual(raised.exception.reason, reason)
    testcase.assertEqual(raised.exception.args, ())
    return raised.exception


class _StringSubclass(str):
    pass


class _IntSubclass(int):
    pass


class _FatalProviderFailure(BaseException):
    pass


class _ExplosiveObject:
    def _explode(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("custom behavior executed")

    __str__ = _explode
    __repr__ = _explode
    __eq__ = _explode
    __hash__ = _explode
    __getattr__ = _explode


class _ExplosiveDict(dict):
    calls = 0

    def _explode(self, *_args: object, **_kwargs: object) -> object:
        type(self).calls += 1
        raise AssertionError("dict subclass behavior executed")

    get = _explode
    keys = _explode
    items = _explode
    __iter__ = _explode
    __getitem__ = _explode
    __contains__ = _explode


class _ExplosiveMapping(Mapping[object, object]):
    calls = 0

    def _explode(self, *_args: object, **_kwargs: object) -> object:
        type(self).calls += 1
        raise AssertionError("mapping behavior executed")

    __getitem__ = _explode
    __iter__ = _explode
    __len__ = _explode


class _ExplosiveStringKey(str):
    equality_calls = 0

    def __eq__(self, _other: object) -> bool:
        type(self).equality_calls += 1
        raise AssertionError("string-subclass equality executed")

    __hash__ = str.__hash__


class PublicSurfaceAndClaimsTests(unittest.TestCase):
    def test_public_surface_is_exact_and_has_no_handler(self):
        self.assertEqual(
            security.__all__,
            (
                "VerifiedOwnerAuthentication",
                "OwnerRequestContext",
                "OwnerSecurityConfiguration",
                "OwnerSecurityError",
                "parse_owner_security_configuration",
                "parse_allowlist_hmac_key",
                "derive_owner_allowlist_entry",
                "derive_mailbox_allowlist_entry",
                "valid_allowlist_owner_identity",
                "valid_allowlist_mailbox_id",
                "parse_trusted_owner_origin",
                "validate_owner_mutation_origin",
                "parse_owner_csrf_header",
                "resolve_owner_request_context",
                "issue_owner_csrf_token",
                "verify_owner_csrf_token",
                "owner_is_allowlisted",
                "mailbox_is_allowlisted",
                "normalize_owner_security_failure",
            ),
        )
        self.assertEqual(security.__name__, "api.collaboration.owner_request_security")
        self.assertFalse(any(name.lower() == "handler" for name in vars(security)))

    def test_top_level_and_alternate_module_identities_fail(self):
        current_dir = Path(__file__).resolve().parent
        frontend_root = current_dir.parents[1]
        script = f"""
import importlib
import importlib.util
import sys
sys.path.insert(0, {str(current_dir)!r})
try:
    importlib.import_module('owner_request_security')
except ImportError:
    pass
else:
    raise AssertionError('top-level identity accepted')
alias = 'api.collaboration.owner_request_security_copy'
spec = importlib.util.spec_from_file_location(alias, 'api/collaboration/owner_request_security.py')
module = importlib.util.module_from_spec(spec)
sys.modules[alias] = module
try:
    spec.loader.exec_module(module)
except ImportError:
    pass
else:
    raise AssertionError('alternate identity accepted')
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=frontend_root,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_valid_claims_and_resolved_context_are_frozen_slotted_and_exact(self):
        claims = _claims(display_name="Öwner")
        self.assertFalse(hasattr(claims, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            claims.subject = "changed"  # type: ignore[misc]

        context = resolve_owner_request_context(
            (), authentication_resolver=lambda _headers: claims, now=NOW
        )
        self.assertIs(type(context), OwnerRequestContext)
        self.assertFalse(hasattr(context, "__dict__"))
        self.assertEqual(context.owner_email, "owner@example.com")
        self.assertEqual(context.workspace_id, WORKSPACE_ID)
        self.assertEqual(context.subject, claims.subject)
        self.assertEqual(context.session_id, claims.session_id)
        self.assertEqual(context.credential_digest, claims.credential_digest)
        self.assertEqual((context.issued_at, context.expires_at), (claims.issued_at, claims.expires_at))
        with self.assertRaises(FrozenInstanceError):
            context.workspace_id = "changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            OwnerRequestContext()
        forged = object.__new__(OwnerRequestContext)
        self.assertFalse(owner_is_allowlisted(forged, _configuration()))

    def test_owner_context_blocks_generic_serialization_copy_and_value_repr(self):
        context = _context()
        duplicate = _context()
        private_values = (
            context.issuer,
            context.subject,
            context.owner_email,
            context.session_id,
            context.credential_digest,
        )

        self.assertFalse(dataclasses.is_dataclass(context))
        self.assertFalse(hasattr(context, "__dict__"))
        self.assertIsNot(context, duplicate)
        self.assertNotEqual(context, duplicate)
        self.assertEqual(hash(context), object.__hash__(context))
        self.assertIs(copy.copy(context), context)
        self.assertIs(copy.deepcopy(context), context)
        self.assertEqual(repr(context), "<OwnerRequestContext>")
        self.assertEqual(str(context), "<OwnerRequestContext>")

        for operation in (
            lambda: dataclasses.asdict(context),
            lambda: pickle.dumps(context),
            context.__getstate__,
        ):
            with self.assertRaises(TypeError) as raised:
                operation()
            serialized_failure = repr(raised.exception.args)
            for private_value in private_values:
                self.assertNotIn(private_value, serialized_failure)

        rendered = repr(context) + str(context)
        for private_value in private_values:
            self.assertNotIn(private_value, rendered)

    def test_private_owner_context_factory_requires_revalidated_exact_claims(self):
        claims = _claims()
        context = security._new_owner_context(claims)
        self.assertIs(type(context), OwnerRequestContext)
        self.assertEqual(context.workspace_id, claims.workspace_id)

        class ClaimsSubclass(VerifiedOwnerAuthentication):
            pass

        subclass = object.__new__(ClaimsSubclass)
        partial = object.__new__(VerifiedOwnerAuthentication)
        object.__setattr__(partial, "issuer", "private-partial-value")
        corrupted = _claims()
        object.__setattr__(
            corrupted,
            "owner_email",
            _StringSubclass("private@example.com"),
        )
        invalid = (
            subclass,
            SimpleNamespace(owner_email="private@example.com"),
            {"owner_email": "private@example.com"},
            _ExplosiveObject(),
            partial,
            corrupted,
        )
        for value in invalid:
            with self.subTest(value_type=type(value).__name__):
                error = _assert_security_failure(
                    self,
                    lambda value=value: security._new_owner_context(value),
                    "authentication_required",
                )
                self.assertEqual(error.args, ())
                self.assertNotIn("private", repr(error))

        forged = object.__new__(OwnerRequestContext)
        object.__setattr__(forged, "_sentinel", _ExplosiveObject())
        self.assertIs(owner_is_allowlisted(forged, _configuration()), False)

    def test_claims_reject_noncanonical_or_subclassed_fields(self):
        invalid_cases = (
            {"issuer": "bad issuer"},
            {"issuer": "é"},
            {"subject": "bad subject!"},
            {"authentication_version": 0},
            {"authentication_version": True},
            {"authentication_version": _IntSubclass(1)},
            {"owner_email": "Owner@example.com"},
            {"owner_email": "ownér@example.com"},
            {"owner_email": "owner@localhost"},
            {"owner_email": _StringSubclass("owner@example.com")},
            {"workspace_id": "owner@example.com"},
            {"workspace_id": "wsp_short"},
            {"workspace_id": _StringSubclass(WORKSPACE_ID)},
            {"display_name": ""},
            {"display_name": "Owner\x00"},
            {"display_name": _StringSubclass("Owner")},
            {"session_id": "short"},
            {"session_id": "x" * 21},
            {"session_id": "x" * 22 + "!"},
            {"credential_digest": "x" * 42},
            {"credential_digest": _b64(b"short")},
            {"credential_digest": _b64(b"c" * 32) + "="},
            {"issued_at": True},
            {"issued_at": _IntSubclass(NOW - 1)},
            {"issued_at": NOW + 1, "expires_at": NOW + 1},
            {"expires_at": True},
            {"expires_at": _IntSubclass(NOW + 1)},
        )
        for updates in invalid_cases:
            with self.subTest(updates=updates):
                with self.assertRaises(ValueError):
                    _claims(**updates)

    def test_claims_subclass_and_beta_dictionary_cannot_mint_context(self):
        class ClaimsSubclass(VerifiedOwnerAuthentication):
            pass

        values = {
            "issuer": "cuevion-auth-v1",
            "authentication_version": 1,
            "subject": "provider:user_0123456789",
            "owner_email": "owner@example.com",
            "workspace_id": WORKSPACE_ID,
            "display_name": "Owner",
            "session_id": _b64(b"session-id-entropy"),
            "credential_digest": _b64(hashlib.sha256(b"credential").digest()),
            "issued_at": NOW - 1,
            "expires_at": NOW + 1,
        }
        with self.assertRaises(ValueError):
            ClaimsSubclass(**values)

        beta_user = {"email": "owner@example.com", "name": "Owner"}
        _assert_security_failure(
            self,
            lambda: resolve_owner_request_context(
                (), authentication_resolver=lambda _headers: beta_user, now=NOW
            ),
            "authentication_required",
        )

    def test_resolver_is_invoked_once_without_retry_and_preserves_safe_failures(self):
        calls: list[object] = []
        headers = [("X-Test", "one")]

        def resolver(received: object) -> VerifiedOwnerAuthentication:
            calls.append(received)
            return _claims()

        context = resolve_owner_request_context(
            headers, authentication_resolver=resolver, now=NOW
        )
        self.assertEqual(context.owner_email, "owner@example.com")
        self.assertEqual(calls, [headers])

        for reason in ("authentication_required", "authentication_unavailable"):
            calls.clear()

            def failing(_headers: object, reason: str = reason) -> object:
                calls.append(True)
                raise OwnerSecurityError(reason)

            error = _assert_security_failure(
                self,
                lambda: resolve_owner_request_context(
                    (), authentication_resolver=failing, now=NOW
                ),
                reason,
            )
            self.assertEqual(calls, [True])
            self.assertEqual(str(error), "")
            self.assertIsNone(error.__context__)
            self.assertIsNone(error.__cause__)

    def test_resolver_normalizes_malformed_exact_security_errors_without_context(self):
        absent = OwnerSecurityError.__new__(OwnerSecurityError)
        deleted = OwnerSecurityError("authentication_required")
        object.__delattr__(deleted, "reason")
        malformed = OwnerSecurityError("authentication_required")
        object.__setattr__(malformed, "reason", _ExplosiveObject())

        for resolver_error in (absent, deleted, malformed):
            def failing(
                _headers: object,
                resolver_error: OwnerSecurityError = resolver_error,
            ) -> object:
                raise resolver_error

            normalized = _assert_security_failure(
                self,
                lambda: resolve_owner_request_context(
                    (), authentication_resolver=failing, now=NOW
                ),
                "internal_error",
            )
            self.assertIsNone(normalized.__context__)
            self.assertIsNone(normalized.__cause__)
            self.assertEqual(normalized.args, ())

    def test_resolver_rejects_expired_future_and_invalid_configuration(self):
        for claims in (
            _claims(expires_at=NOW),
            _claims(issued_at=NOW + 1, expires_at=NOW + 100),
        ):
            _assert_security_failure(
                self,
                lambda claims=claims: resolve_owner_request_context(
                    (), authentication_resolver=lambda _headers: claims, now=NOW
                ),
                "authentication_required",
            )

        calls: list[bool] = []
        _assert_security_failure(
            self,
            lambda: resolve_owner_request_context(
                (), authentication_resolver=None, now=NOW
            ),
            "invalid_configuration",
        )
        _assert_security_failure(
            self,
            lambda: resolve_owner_request_context(
                (),
                authentication_resolver=lambda _headers: calls.append(True),
                now=True,
            ),
            "invalid_configuration",
        )
        self.assertEqual(calls, [])

    def test_resolver_private_exception_is_fixed_and_baseexception_propagates(self):
        marker = "private provider credential marker"

        def ordinary_failure(_headers: object) -> object:
            raise RuntimeError(marker)

        error = _assert_security_failure(
            self,
            lambda: resolve_owner_request_context(
                (), authentication_resolver=ordinary_failure, now=NOW
            ),
            "internal_error",
        )
        self.assertNotIn(marker, str(error))
        self.assertNotIn(marker, repr(error.args))
        self.assertNotIn(marker, repr(error))
        self.assertIsNone(error.__context__)
        self.assertIsNone(error.__cause__)
        self.assertEqual(normalize_owner_security_failure(error), (500, "internal_error"))

        def fatal_failure(_headers: object) -> object:
            raise _FatalProviderFailure(marker)

        with self.assertRaises(_FatalProviderFailure):
            resolve_owner_request_context(
                (), authentication_resolver=fatal_failure, now=NOW
            )


class ConfigurationAndOriginTests(unittest.TestCase):
    def test_valid_current_and_rotation_configuration_is_opaque_frozen_and_slotted(self):
        current = _configuration()
        rotated = _configuration(previous_key=PREVIOUS_KEY)
        self.assertIs(type(current), OwnerSecurityConfiguration)
        self.assertEqual(current.app_origin, ORIGIN)
        self.assertFalse(hasattr(current, "__dict__"))
        self.assertIsNone(current._csrf_previous_key)
        self.assertEqual(rotated._csrf_previous_key, PREVIOUS_KEY)
        self.assertFalse(dataclasses.is_dataclass(current))
        with self.assertRaises(FrozenInstanceError):
            current.app_origin = "changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            OwnerSecurityConfiguration()
        forged = object.__new__(OwnerSecurityConfiguration)
        _assert_security_failure(
            self,
            lambda: validate_owner_mutation_origin((("Origin", ORIGIN),), forged),
            "invalid_configuration",
        )
        self.assertNotIn(_b64(CURRENT_KEY), repr(current))

    def test_configuration_blocks_generic_serialization_copy_and_secret_repr(self):
        configuration = _configuration(
            previous_key=PREVIOUS_KEY,
            owner_entries=(_owner_entry(),),
            mailbox_entries=(_mailbox_entry(),),
        )
        other = _configuration(
            previous_key=PREVIOUS_KEY,
            owner_entries=(_owner_entry(),),
            mailbox_entries=(_mailbox_entry(),),
        )
        private_values = (
            CURRENT_KEY.decode("ascii"),
            PREVIOUS_KEY.decode("ascii"),
            ALLOWLIST_KEY.decode("ascii"),
            _b64(CURRENT_KEY),
            _b64(PREVIOUS_KEY),
            _b64(ALLOWLIST_KEY),
            _owner_entry(),
            _mailbox_entry(),
        )

        self.assertFalse(dataclasses.is_dataclass(configuration))
        self.assertFalse(hasattr(configuration, "__dict__"))
        self.assertIsNot(configuration, other)
        self.assertNotEqual(configuration, other)
        self.assertEqual(hash(configuration), object.__hash__(configuration))
        self.assertIs(copy.copy(configuration), configuration)
        self.assertIs(copy.deepcopy(configuration), configuration)
        self.assertEqual(repr(configuration), "<OwnerSecurityConfiguration>")
        self.assertEqual(str(configuration), "<OwnerSecurityConfiguration>")

        for operation in (
            lambda: dataclasses.asdict(configuration),
            lambda: pickle.dumps(configuration),
            configuration.__getstate__,
        ):
            with self.assertRaises(TypeError) as raised:
                operation()
            rendered_failure = repr(raised.exception.args)
            for private_value in private_values:
                self.assertNotIn(private_value, rendered_failure)

        rendered = repr(configuration) + str(configuration)
        for private_value in private_values:
            self.assertNotIn(private_value, rendered)

    def test_configuration_accepts_only_exact_dict_with_exact_known_keys(self):
        exact = _trusted_configuration()
        self.assertIs(
            type(parse_owner_security_configuration(exact)),
            OwnerSecurityConfiguration,
        )

        dict_subclass = _ExplosiveDict()
        dict.update(dict_subclass, exact)
        _ExplosiveDict.calls = 0
        _assert_security_failure(
            self,
            lambda: parse_owner_security_configuration(dict_subclass),
            "invalid_configuration",
        )
        self.assertEqual(_ExplosiveDict.calls, 0)

        mapping = _ExplosiveMapping()
        _ExplosiveMapping.calls = 0
        _assert_security_failure(
            self,
            lambda: parse_owner_security_configuration(mapping),
            "invalid_configuration",
        )
        self.assertEqual(_ExplosiveMapping.calls, 0)

        subclass_key_values = _trusted_configuration()
        origin_value = subclass_key_values.pop("CUEVION_APP_ORIGIN")
        subclass_key = _ExplosiveStringKey("CUEVION_APP_ORIGIN")
        dict.__setitem__(subclass_key_values, subclass_key, origin_value)
        _ExplosiveStringKey.equality_calls = 0
        _assert_security_failure(
            self,
            lambda: parse_owner_security_configuration(subclass_key_values),
            "invalid_configuration",
        )
        self.assertEqual(_ExplosiveStringKey.equality_calls, 0)

        marker = "private-unknown-configuration-value"
        unknown = _trusted_configuration()
        unknown["CUEVION_COLLAB_V2_UNKNOWN"] = marker
        error = _assert_security_failure(
            self,
            lambda: parse_owner_security_configuration(unknown),
            "invalid_configuration",
        )
        self.assertNotIn(marker, repr(error))
        self.assertNotIn(marker, repr(error.args))

    def test_configuration_and_origin_parser_detach_private_exceptions(self):
        marker = "private-configuration-parser-marker"
        with patch.object(
            security,
            "_parse_secret_key",
            side_effect=RuntimeError(marker),
        ):
            error = _assert_security_failure(
                self,
                lambda: parse_owner_security_configuration(
                    _trusted_configuration()
                ),
                "invalid_configuration",
            )
        self.assertIsNone(error.__context__)
        self.assertIsNone(error.__cause__)
        self.assertNotIn(marker, repr(error))
        self.assertNotIn(marker, repr(error.args))

        with patch.object(security, "_urlsplit", side_effect=RuntimeError(marker)):
            origin_error = _assert_security_failure(
                self,
                lambda: parse_trusted_owner_origin(ORIGIN),
                "invalid_configuration",
            )
        self.assertIsNone(origin_error.__context__)
        self.assertIsNone(origin_error.__cause__)
        self.assertNotIn(marker, repr(origin_error))

    def test_missing_and_malformed_configuration_fail_closed_without_values(self):
        required = (
            "CUEVION_APP_ORIGIN",
            "CUEVION_COLLAB_V2_OWNER_CSRF_KEY",
            "CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY",
            "CUEVION_COLLAB_V2_OWNER_ALLOWLIST",
            "CUEVION_COLLAB_V2_MAILBOX_ALLOWLIST",
        )
        for key in required:
            values = _trusted_configuration()
            values.pop(key)
            with self.subTest(missing=key):
                _assert_security_failure(
                    self,
                    lambda values=values: parse_owner_security_configuration(values),
                    "invalid_configuration",
                )

        marker = "private-configuration-value"
        invalid_updates = (
            {"CUEVION_COLLAB_V2_OWNER_CSRF_KEY": marker},
            {"CUEVION_COLLAB_V2_OWNER_CSRF_KEY": _b64(b"short")},
            {"CUEVION_COLLAB_V2_OWNER_CSRF_KEY": _b64(CURRENT_KEY) + "="},
            {"CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY": "!" * 43},
            {"CUEVION_COLLAB_V2_OWNER_CSRF_KEY_PREVIOUS": _b64(CURRENT_KEY)},
            {"CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY": _b64(CURRENT_KEY)},
            {
                "CUEVION_COLLAB_V2_OWNER_CSRF_KEY_PREVIOUS": _b64(PREVIOUS_KEY),
                "CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY": _b64(PREVIOUS_KEY),
            },
            {"CUEVION_COLLAB_V2_OWNER_ALLOWLIST": ""},
            {"CUEVION_COLLAB_V2_OWNER_ALLOWLIST": "v1_" + _b64(b"a" * 32) + ","},
            {"CUEVION_COLLAB_V2_OWNER_ALLOWLIST": "v1_" + _b64(b"a" * 32) + ", v1_" + _b64(b"b" * 32)},
            {"CUEVION_COLLAB_V2_OWNER_ALLOWLIST": "v1_" + _b64(b"a" * 32) + ",v1_" + _b64(b"a" * 32)},
            {"CUEVION_COLLAB_V2_MAILBOX_ALLOWLIST": "v2_" + _b64(b"a" * 32)},
            {"CUEVION_COLLAB_V2_MAILBOX_ALLOWLIST": "v1_" + _b64(b"short")},
            {"CUEVION_COLLAB_V2_MAILBOX_ALLOWLIST": "v1_" + _b64(b"a" * 32) + "="},
        )
        for updates in invalid_updates:
            values = _trusted_configuration()
            values.update(updates)
            with self.subTest(updates=updates):
                error = _assert_security_failure(
                    self,
                    lambda values=values: parse_owner_security_configuration(values),
                    "invalid_configuration",
                )
                self.assertNotIn(marker, str(error))
                self.assertNotIn(marker, repr(error.args))

    def test_configuration_rejects_string_subclasses_and_non_mappings(self):
        for key in (
            "CUEVION_APP_ORIGIN",
            "CUEVION_COLLAB_V2_OWNER_CSRF_KEY",
            "CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY",
            "CUEVION_COLLAB_V2_OWNER_ALLOWLIST",
            "CUEVION_COLLAB_V2_MAILBOX_ALLOWLIST",
        ):
            values = _trusted_configuration()
            values[key] = _StringSubclass(values[key])
            with self.subTest(key=key):
                _assert_security_failure(
                    self,
                    lambda values=values: parse_owner_security_configuration(values),
                    "invalid_configuration",
                )
        _assert_security_failure(
            self,
            lambda: parse_owner_security_configuration([]),
            "invalid_configuration",
        )

    def test_origin_parser_accepts_only_exact_production_origin(self):
        self.assertEqual(parse_trusted_owner_origin(ORIGIN), ORIGIN)
        invalid = (
            None,
            "http://app.cuevion.com",
            "HTTPS://app.cuevion.com",
            "https://APP.cuevion.com",
            "https://app.cuevion.com/",
            "https://app.cuevion.com:443",
            "https://user@app.cuevion.com",
            "https://app.cuevion.com/path",
            "https://app.cuevion.com?query",
            "https://app.cuevion.com#fragment",
            " https://app.cuevion.com",
            "https://app.cuevion.com ",
            "https://app.cuevion.com,https://evil.example",
            "*",
            "null",
            "https://äpp.cuevion.com",
            "https://xn--pp-cla.cuevion.com",
            "https://app.cuevion.com:notaport",
            "https://evil.example",
            _StringSubclass(ORIGIN),
        )
        for value in invalid:
            with self.subTest(value=value):
                _assert_security_failure(
                    self,
                    lambda value=value: parse_trusted_owner_origin(value),
                    "invalid_configuration",
                )

    def test_request_origin_requires_one_byte_exact_header(self):
        configuration = _configuration()
        self.assertEqual(
            validate_owner_mutation_origin([("Origin", ORIGIN)], configuration),
            ORIGIN,
        )
        invalid_headers = (
            (),
            (("Origin", "https://evil.example"),),
            (("Origin", ORIGIN), ("origin", ORIGIN)),
            (("Origin", ORIGIN + ",https://evil.example"),),
            (("Origin", "null"),),
            (("Origin", "HTTPS://app.cuevion.com"),),
            (("Origin", "https://APP.cuevion.com"),),
            (("Origin", ORIGIN + "/"),),
        )
        for headers in invalid_headers:
            with self.subTest(headers=headers):
                _assert_security_failure(
                    self,
                    lambda headers=headers: validate_owner_mutation_origin(
                        headers, configuration
                    ),
                    "forbidden_origin",
                )

    def test_host_forwarded_and_referer_never_influence_origin(self):
        configuration = _configuration()
        untrusted = [
            ("Host", "evil.example"),
            ("X-Forwarded-Host", "evil.example"),
            ("X-Forwarded-Proto", "http"),
            ("Referer", "https://evil.example/path"),
            ("Origin", ORIGIN),
        ]
        self.assertEqual(validate_owner_mutation_origin(untrusted, configuration), ORIGIN)
        reflected = [
            ("Host", "app.cuevion.com"),
            ("X-Forwarded-Host", "app.cuevion.com"),
            ("X-Forwarded-Proto", "https"),
            ("Referer", ORIGIN),
            ("Origin", "https://evil.example"),
        ]
        _assert_security_failure(
            self,
            lambda: validate_owner_mutation_origin(reflected, configuration),
            "forbidden_origin",
        )


class CsrfIssuanceAndHeaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = _context()
        self.configuration = _configuration()

    def test_header_parser_requires_one_strict_canonical_token(self):
        token, _expiry = issue_owner_csrf_token(
            self.context, self.configuration, now=NOW
        )
        self.assertEqual(parse_owner_csrf_header([("X-Cuevion-CSRF", token)]), token)
        invalid = (
            (),
            (("X-Cuevion-CSRF", token), ("x-cuevion-csrf", token)),
            (("X-Cuevion-CSRF", token + ",other"),),
            (("X-Cuevion-CSRF", "oc1.." + ("a" * 43)),),
            (("X-Cuevion-CSRF", "oc1.a." + ("a" * 43)),),
            (("X-Cuevion-CSRF", "oc1.a=." + ("a" * 43)),),
            (("X-Cuevion-CSRF", "oc1.a." + ("a" * 42)),),
            (("X-Cuevion-CSRF", "oc1.a." + ("!" * 43)),),
            (("X-Cuevion-CSRF", "oc1.a." + ("a" * 43) + " "),),
            (("X-Cuevion-CSRF", "oc1." + ("a" * 470) + "." + ("a" * 43)),),
        )
        for headers in invalid:
            with self.subTest(headers=headers):
                _assert_security_failure(
                    self,
                    lambda headers=headers: parse_owner_csrf_header(headers),
                    "invalid_csrf",
                )

    def test_origin_and_header_parsers_detach_private_exceptions(self):
        marker = "private-request-parser-marker"
        for operation, reason in (
            (
                lambda: validate_owner_mutation_origin((), self.configuration),
                "forbidden_origin",
            ),
            (lambda: parse_owner_csrf_header(()), "invalid_csrf"),
        ):
            with patch.object(
                security,
                "_get_security_header",
                side_effect=RuntimeError(marker),
            ):
                error = _assert_security_failure(self, operation, reason)
            self.assertIsNone(error.__context__)
            self.assertIsNone(error.__cause__)
            self.assertEqual(error.args, ())
            self.assertNotIn(marker, repr(error))
            self.assertNotIn(marker, str(error))

        with patch.object(
            security,
            "_get_security_header",
            side_effect=_FatalProviderFailure(marker),
        ):
            with self.assertRaises(_FatalProviderFailure):
                parse_owner_csrf_header(())

    def test_issuance_has_exact_canonical_shape_and_fifteen_minute_lifetime(self):
        with patch.object(security._secrets, "token_bytes", return_value=b"n" * 16) as nonce:
            token, expires_at = issue_owner_csrf_token(
                self.context, self.configuration, now=NOW
            )
        nonce.assert_called_once_with(16)
        prefix, encoded_payload, signature = token.split(".")
        self.assertEqual(prefix, "oc1")
        self.assertNotIn("=", token)
        self.assertEqual(len(signature), 43)
        payload_text = _unb64(encoded_payload).decode("utf-8")
        payload = json.loads(payload_text)
        self.assertEqual(
            payload_text,
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
        )
        self.assertEqual(
            set(payload), {"aud", "exp", "iat", "n", "o", "p", "s", "v"}
        )
        self.assertEqual(payload["aud"], "cuevion-collaboration-v2-owner")
        self.assertEqual(payload["p"], "owner_csrf")
        self.assertEqual(payload["v"], 1)
        self.assertEqual(payload["iat"], NOW)
        self.assertEqual(payload["exp"], NOW + 900)
        self.assertEqual(expires_at, NOW + 900)
        self.assertEqual(_unb64(payload["n"]), b"n" * 16)
        self.assertEqual(len(_unb64(payload["o"])), 32)
        self.assertEqual(len(_unb64(payload["s"])), 32)

    def test_issuance_caps_at_authentication_expiry_and_requires_lifetime(self):
        context = _context(expires_at=NOW + 120)
        _token, expiry = issue_owner_csrf_token(context, self.configuration, now=NOW)
        self.assertEqual(expiry, NOW + 120)
        _assert_security_failure(
            self,
            lambda: issue_owner_csrf_token(context, self.configuration, now=NOW + 120),
            "authentication_required",
        )
        _assert_security_failure(
            self,
            lambda: issue_owner_csrf_token(self.context, self.configuration, now=True),
            "authentication_required",
        )

    def test_tokens_are_independent_coexisting_and_contain_no_raw_trusted_values(self):
        with patch.object(
            security._secrets,
            "token_bytes",
            side_effect=(b"a" * 16, b"b" * 16),
        ):
            first, _ = issue_owner_csrf_token(
                self.context, self.configuration, now=NOW
            )
            second, _ = issue_owner_csrf_token(
                self.context, self.configuration, now=NOW
            )
        self.assertNotEqual(first, second)
        self.assertTrue(
            verify_owner_csrf_token(
                first, self.context, self.configuration, now=NOW + 1
            )
        )
        self.assertTrue(
            verify_owner_csrf_token(
                second, self.context, self.configuration, now=NOW + 1
            )
        )
        decoded = _unb64(first.split(".")[1]).decode("utf-8")
        for private_value in (
            self.context.issuer,
            self.context.subject,
            self.context.owner_email,
            self.context.workspace_id,
            self.context.session_id,
            self.context.credential_digest,
            ORIGIN,
        ):
            self.assertNotIn(private_value, first)
            self.assertNotIn(private_value, decoded)

    def test_issuance_uses_current_key_only(self):
        rotating = _configuration(previous_key=PREVIOUS_KEY)
        token, _ = issue_owner_csrf_token(self.context, rotating, now=NOW)
        previous_only = _configuration(current_key=PREVIOUS_KEY)
        _assert_security_failure(
            self,
            lambda: verify_owner_csrf_token(
                token, self.context, previous_only, now=NOW + 1
            ),
            "invalid_csrf",
        )


class CsrfVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = _context()
        self.configuration = _configuration()
        self.token, _ = issue_owner_csrf_token(
            self.context, self.configuration, now=NOW
        )

    def assert_invalid(self, token: object, *, context: object | None = None, now: object = NOW) -> None:
        error = _assert_security_failure(
            self,
            lambda: verify_owner_csrf_token(
                token,
                self.context if context is None else context,
                self.configuration,
                now=now,
            ),
            "invalid_csrf",
        )
        self.assertEqual(normalize_owner_security_failure(error), (403, "forbidden"))

    def test_current_and_previous_keys_verify_and_both_rotation_paths_are_compared(self):
        old_configuration = _configuration(current_key=PREVIOUS_KEY)
        old_token, _ = issue_owner_csrf_token(
            self.context, old_configuration, now=NOW
        )
        rotating = _configuration(previous_key=PREVIOUS_KEY)
        original_signature = security._token_signature
        original_binding = security._session_binding
        original_compare = hmac.compare_digest

        for label, token in (("current", self.token), ("previous", old_token)):
            signature_keys: list[bytes] = []
            binding_keys: list[bytes] = []
            comparisons: list[tuple[object, object]] = []

            def token_signature(key: bytes, payload_segment: str) -> bytes:
                signature_keys.append(key)
                return original_signature(key, payload_segment)

            def session_binding(
                key: bytes, context: OwnerRequestContext
            ) -> bytes:
                binding_keys.append(key)
                return original_binding(key, context)

            def compared(left: object, right: object) -> bool:
                comparisons.append((left, right))
                return original_compare(left, right)  # type: ignore[arg-type]

            with self.subTest(token_key=label), patch.object(
                security, "_token_signature", side_effect=token_signature
            ), patch.object(
                security, "_session_binding", side_effect=session_binding
            ), patch.object(
                security._hmac, "compare_digest", side_effect=compared
            ):
                self.assertTrue(
                    verify_owner_csrf_token(
                        token, self.context, rotating, now=NOW + 1
                    )
                )
            self.assertEqual(signature_keys, [CURRENT_KEY, PREVIOUS_KEY])
            self.assertEqual(binding_keys, [CURRENT_KEY, PREVIOUS_KEY])
            self.assertEqual(len(comparisons), 5)

    def test_session_binding_rejects_every_identity_or_session_swap(self):
        swapped_contexts = (
            _context(issued_at=NOW - 61),
            _context(expires_at=NOW + 3_601),
            _context(owner_email="other@example.com"),
            _context(session_id=_b64(b"different-session!")),
            _context(credential_digest=_b64(hashlib.sha256(b"changed").digest())),
            _context(subject="provider:other_0123456789"),
            _context(issuer="different-auth-v1"),
            _context(authentication_version=2),
        )
        for context in swapped_contexts:
            with self.subTest(context=context.subject):
                self.assert_invalid(self.token, context=context, now=NOW + 1)

    def test_time_boundaries_and_origin_binding_fail_closed(self):
        base = _payload(self.token)
        cases = (
            ({**base, "exp": NOW}, NOW),
            ({**base, "iat": NOW + 31, "exp": NOW + 100}, NOW),
            ({**base, "iat": NOW, "exp": NOW + 901}, NOW),
            ({**base, "iat": NOW + 1, "exp": NOW + 1}, NOW),
            ({**base, "exp": self.context.expires_at + 1}, NOW),
            ({**base, "iat": self.context.issued_at - 1}, NOW),
            ({**base, "o": _b64(hashlib.sha256(b"https://evil.example").digest())}, NOW),
        )
        for payload, verification_now in cases:
            with self.subTest(payload=payload):
                self.assert_invalid(_signed_payload(payload), now=verification_now)
        self.assert_invalid(self.token, now=NOW + 900)
        self.assertTrue(
            verify_owner_csrf_token(
                self.token, self.context, self.configuration, now=NOW + 899
            )
        )

    def test_iat_at_positive_clock_skew_boundary_is_accepted(self):
        payload = {**_payload(self.token), "iat": NOW + 30}
        self.assertTrue(
            verify_owner_csrf_token(
                _signed_payload(payload),
                self.context,
                self.configuration,
                now=NOW,
            )
        )

    def test_iat_beyond_positive_clock_skew_boundary_is_rejected(self):
        payload = {**_payload(self.token), "iat": NOW + 31}
        self.assert_invalid(_signed_payload(payload), now=NOW)

    def test_now_one_second_before_expiry_is_accepted(self):
        self.assertTrue(
            verify_owner_csrf_token(
                self.token,
                self.context,
                self.configuration,
                now=NOW + 899,
            )
        )

    def test_now_at_expiry_is_rejected(self):
        self.assert_invalid(self.token, now=NOW + 900)

    def test_expiry_equal_to_authentication_expiry_is_accepted(self):
        context = _context(expires_at=NOW + 900)
        token, expiry = issue_owner_csrf_token(
            context, self.configuration, now=NOW
        )
        self.assertEqual(expiry, context.expires_at)
        self.assertTrue(
            verify_owner_csrf_token(
                token, context, self.configuration, now=NOW
            )
        )

    def test_expiry_beyond_authentication_expiry_is_independently_rejected(self):
        context = _context(expires_at=NOW + 100)
        token, _expiry = issue_owner_csrf_token(
            context, self.configuration, now=NOW
        )
        payload = {**_payload(token), "exp": context.expires_at + 1}
        self.assert_invalid(
            _signed_payload(payload),
            context=context,
            now=NOW,
        )

    def test_outer_syntax_and_confused_token_families_are_rejected(self):
        payload_segment = self.token.split(".")[1]
        signature = self.token.split(".")[2]
        malformed = (
            "",
            "oc1",
            "oc1.." + signature,
            "oc1." + payload_segment,
            "oc1." + payload_segment + "." + signature + ".extra",
            "oc1." + payload_segment + "=." + signature,
            "oc1." + payload_segment + "." + signature + "=",
            "oc1." + payload_segment + "." + ("!" * 43),
            "oc1." + payload_segment + "." + ("a" * 42),
            "oc1." + payload_segment + "." + signature + " ",
            "gc1." + payload_segment + "." + signature,
            "beta." + payload_segment + "." + signature,
            "invitation." + payload_segment + "." + signature,
        )
        for token in malformed:
            with self.subTest(token=token[:24]):
                self.assert_invalid(token)
        self.assert_invalid(_StringSubclass(self.token))

    def test_realistic_cross_family_token_shapes_share_one_csrf_failure(self):
        beta_payload = _b64(
            json.dumps(
                {"email": "owner@example.com", "exp": NOW + 3_600},
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        confused_tokens = {
            "current_beta_session": beta_payload + "." + _b64(b"b" * 32),
            "opaque_guest_session": _b64(b"g" * 32),
            "invitation": _b64(b"i" * 32),
            "guest_csrf": _b64(b"c" * 32),
            "random_bearer": "Bearer 7f44d0b1882444b1b25d5ab05c9d837f",
        }
        for label, token in confused_tokens.items():
            with self.subTest(token_family=label):
                self.assert_invalid(token)

        payload = _payload(self.token)
        confused_owner_payloads = (
            {**payload, "aud": "cuevion-collaboration-v2-guest"},
            {**payload, "p": "invitation_exchange"},
            {**payload, "v": 2},
        )
        for confused_payload in confused_owner_payloads:
            self.assert_invalid(_signed_payload(confused_payload))

    def test_utf8_duplicate_json_schema_and_canonical_encoding_are_strict(self):
        self.assert_invalid(_signed_token_from_text(b"\xff"))
        duplicate = (
            b'{"aud":"cuevion-collaboration-v2-owner","aud":"duplicate",'
            b'"exp":1800000900,"iat":1800000000,"n":"aaaaaaaaaaaaaaaaaaaaaa",'
            b'"o":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
            b'"p":"owner_csrf","s":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","v":1}'
        )
        self.assert_invalid(_signed_token_from_text(duplicate))
        self.assert_invalid(_signed_token_from_text(b"{}"))

        base = _payload(self.token)
        unknown = dict(base)
        unknown["unknown"] = "value"
        missing = dict(base)
        missing.pop("aud")
        for payload in (unknown, missing):
            self.assert_invalid(_signed_payload(payload))

        noncanonical = json.dumps(base, sort_keys=False, indent=1).encode("utf-8")
        self.assert_invalid(_signed_token_from_text(noncanonical))

    def test_exact_payload_types_constants_nonce_binding_and_signature_are_required(self):
        base = _payload(self.token)
        cases = (
            {**base, "aud": "guest"},
            {**base, "p": "invitation"},
            {**base, "v": 2},
            {**base, "v": True},
            {**base, "iat": True},
            {**base, "exp": float(NOW + 1)},
            {**base, "n": _b64(b"short")},
            {**base, "n": _b64(b"n" * 16) + "="},
            {**base, "n": "!" * 22},
            {**base, "s": _b64(b"short")},
            {**base, "s": _b64(b"s" * 32) + "="},
            {**base, "s": "!" * 43},
            {**base, "o": _b64(b"short")},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                self.assert_invalid(_signed_payload(payload))
        self.assert_invalid(self.token[:-1] + ("A" if self.token[-1] != "A" else "B"))
        self.assert_invalid(self.token, now=_IntSubclass(NOW))

    def test_noncanonical_base64url_pad_bit_aliases_are_rejected(self):
        payload = _payload(self.token)
        for field in ("o", "s"):
            canonical = payload[field]
            self.assertIs(type(canonical), str)
            alias = _noncanonical_base64url_alias(canonical)  # type: ignore[arg-type]
            self.assertEqual(_unb64(alias), _unb64(canonical))  # type: ignore[arg-type]
            self.assert_invalid(_signed_payload({**payload, field: alias}))

        prefix, encoded_payload, signature = self.token.split(".")
        signature_alias = _noncanonical_base64url_alias(signature)
        self.assertEqual(_unb64(signature_alias), _unb64(signature))
        self.assert_invalid(".".join((prefix, encoded_payload, signature_alias)))

    def test_digest_comparisons_use_exact_decoded_bytes_only(self):
        calls: list[tuple[object, object]] = []
        original = hmac.compare_digest

        def compared(left: object, right: object) -> bool:
            calls.append((left, right))
            return original(left, right)  # type: ignore[arg-type]

        with patch.object(security._hmac, "compare_digest", side_effect=compared):
            self.assertTrue(
                verify_owner_csrf_token(
                    self.token,
                    self.context,
                    self.configuration,
                    now=NOW,
                )
            )

        self.assertEqual(len(calls), 3)
        for left, right in calls:
            self.assertIs(type(left), bytes)
            self.assertIs(type(right), bytes)
            self.assertEqual(len(left), 32)
            self.assertEqual(len(right), 32)

        malformed = {**_payload(self.token), "o": _b64(b"short")}
        calls.clear()
        with patch.object(security._hmac, "compare_digest", side_effect=compared):
            self.assert_invalid(_signed_payload(malformed))
        self.assertEqual(calls, [])

    def test_every_failure_is_publicly_indistinguishable_and_contains_no_token(self):
        marker = "private-token-marker"
        failures = (
            marker,
            self.token + "," + marker,
            _signed_token_from_text(marker.encode("ascii")),
        )
        for token in failures:
            error = _assert_security_failure(
                self,
                lambda token=token: verify_owner_csrf_token(
                    token, self.context, self.configuration, now=NOW
                ),
                "invalid_csrf",
            )
            self.assertEqual(error.args, ())
            self.assertNotIn(marker, str(error))
            self.assertEqual(normalize_owner_security_failure(error), (403, "forbidden"))

    def test_verifier_detaches_private_parser_exception_and_propagates_baseexception(self):
        marker = "private-token-parser-marker"
        with patch.object(
            security,
            "_parse_token_payload",
            side_effect=RuntimeError(marker),
        ):
            error = _assert_security_failure(
                self,
                lambda: verify_owner_csrf_token(
                    self.token,
                    self.context,
                    self.configuration,
                    now=NOW,
                ),
                "invalid_csrf",
            )
        self.assertIsNone(error.__context__)
        self.assertIsNone(error.__cause__)
        self.assertEqual(error.args, ())
        self.assertNotIn(marker, repr(error))
        self.assertNotIn(marker, str(error))
        self.assertNotIn(marker, repr(normalize_owner_security_failure(error)))

        with patch.object(
            security,
            "_parse_token_payload",
            side_effect=_FatalProviderFailure(marker),
        ):
            with self.assertRaises(_FatalProviderFailure):
                verify_owner_csrf_token(
                    self.token,
                    self.context,
                    self.configuration,
                    now=NOW,
                )


class AllowlistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = _context()
        self.owner_match = _owner_entry()
        self.mailbox_match = _mailbox_entry()

    def test_owner_and_mailbox_match_and_miss_return_exact_bool(self):
        configuration = _configuration(
            owner_entries=("v1_" + _b64(b"1" * 32), self.owner_match),
            mailbox_entries=("v1_" + _b64(b"2" * 32), self.mailbox_match),
        )
        owner_result = owner_is_allowlisted(self.context, configuration)
        mailbox_result = mailbox_is_allowlisted(
            self.context, MAILBOX_ID, configuration
        )
        self.assertIs(type(owner_result), bool)
        self.assertIs(type(mailbox_result), bool)
        self.assertTrue(owner_result)
        self.assertTrue(mailbox_result)

        miss = _configuration()
        self.assertIs(owner_is_allowlisted(self.context, miss), False)
        self.assertIs(mailbox_is_allowlisted(self.context, MAILBOX_ID, miss), False)

    def test_public_derivation_helpers_match_existing_independent_vectors(self):
        self.assertEqual(
            derive_owner_allowlist_entry(
                ALLOWLIST_KEY,
                self.context.issuer,
                self.context.authentication_version,
                self.context.subject,
            ),
            self.owner_match,
        )
        self.assertEqual(
            derive_mailbox_allowlist_entry(
                ALLOWLIST_KEY,
                self.context.issuer,
                self.context.authentication_version,
                self.context.subject,
                MAILBOX_ID,
            ),
            self.mailbox_match,
        )

    def test_full_allowlist_is_compared_without_early_return(self):
        entries = (
            self.owner_match,
            "v1_" + _b64(b"2" * 32),
            "v1_" + _b64(b"3" * 32),
        )
        configuration = _configuration(owner_entries=entries)
        calls: list[tuple[object, object]] = []
        original = hmac.compare_digest

        def compared(left: object, right: object) -> bool:
            calls.append((left, right))
            return original(left, right)

        with patch.object(security._hmac, "compare_digest", side_effect=compared):
            self.assertTrue(owner_is_allowlisted(self.context, configuration))
        self.assertEqual(len(calls), len(entries))
        self.assertEqual([right for _left, right in calls], list(entries))

    def test_owner_and_mailbox_allowlists_traverse_every_position_and_miss(self):
        decoys = tuple("v1_" + _b64(bytes([value]) * 32) for value in (1, 2, 3))
        owner_cases = {
            "first": (self.owner_match, decoys[1], decoys[2]),
            "middle": (decoys[0], self.owner_match, decoys[2]),
            "last": (decoys[0], decoys[1], self.owner_match),
            "miss": decoys,
        }
        mailbox_cases = {
            "first": (self.mailbox_match, decoys[1], decoys[2]),
            "middle": (decoys[0], self.mailbox_match, decoys[2]),
            "last": (decoys[0], decoys[1], self.mailbox_match),
            "miss": decoys,
        }
        original = hmac.compare_digest

        for label, entries in owner_cases.items():
            configuration = _configuration(owner_entries=entries)
            calls: list[tuple[object, object]] = []

            def compared(left: object, right: object) -> bool:
                calls.append((left, right))
                return original(left, right)  # type: ignore[arg-type]

            with self.subTest(kind="owner", position=label), patch.object(
                security._hmac,
                "compare_digest",
                side_effect=compared,
            ):
                result = owner_is_allowlisted(self.context, configuration)
            self.assertIs(result, label != "miss")
            self.assertEqual([right for _left, right in calls], list(entries))

        for label, entries in mailbox_cases.items():
            configuration = _configuration(mailbox_entries=entries)
            calls = []

            def compared(left: object, right: object) -> bool:
                calls.append((left, right))
                return original(left, right)  # type: ignore[arg-type]

            with self.subTest(kind="mailbox", position=label), patch.object(
                security._hmac,
                "compare_digest",
                side_effect=compared,
            ):
                result = mailbox_is_allowlisted(
                    self.context,
                    MAILBOX_ID,
                    configuration,
                )
            self.assertIs(result, label != "miss")
            self.assertEqual([right for _left, right in calls], list(entries))

    def test_domain_identity_and_mailbox_components_are_all_bound(self):
        configuration = _configuration(
            owner_entries=(self.owner_match,),
            mailbox_entries=(self.mailbox_match,),
        )
        self.assertNotEqual(self.owner_match, self.mailbox_match)
        changed_contexts = (
            _context(issuer="other-auth-v1"),
            _context(authentication_version=2),
            _context(subject="provider:other_0123456789"),
        )
        for context in changed_contexts:
            self.assertFalse(owner_is_allowlisted(context, configuration))
            self.assertFalse(
                mailbox_is_allowlisted(context, MAILBOX_ID, configuration)
            )
        self.assertFalse(
            mailbox_is_allowlisted(self.context, "other.mailbox", configuration)
        )

    def test_mailbox_identifier_is_exact_bounded_canonical_ascii(self):
        configuration = _configuration(mailbox_entries=(self.mailbox_match,))
        invalid = (
            None,
            "",
            "Primary.mailbox",
            "-primary",
            "primary mailbox",
            "maïlbox",
            "a" * 257,
            _StringSubclass(MAILBOX_ID),
        )
        for mailbox_id in invalid:
            with self.subTest(mailbox_id=mailbox_id):
                result = mailbox_is_allowlisted(
                    self.context, mailbox_id, configuration
                )
                self.assertIs(result, False)
        self.assertIs(owner_is_allowlisted({}, configuration), False)
        self.assertIs(mailbox_is_allowlisted({}, MAILBOX_ID, configuration), False)

    def test_configured_entries_contain_no_raw_email_or_mailbox(self):
        values = _trusted_configuration(
            owner_entries=(self.owner_match,), mailbox_entries=(self.mailbox_match,)
        )
        serialized = json.dumps(values, sort_keys=True)
        self.assertNotIn(self.context.owner_email, serialized)
        self.assertNotIn(MAILBOX_ID, serialized)


class FailureNormalizationTests(unittest.TestCase):
    def test_approved_reasons_map_to_exact_immutable_public_pairs(self):
        expected = {
            "authentication_required": (401, "unauthorized"),
            "authentication_unavailable": (503, "service_unavailable"),
            "forbidden_origin": (403, "forbidden"),
            "invalid_csrf": (403, "forbidden"),
            "rollout_unavailable": (404, "not_found"),
            "invalid_configuration": (503, "service_unavailable"),
            "internal_error": (500, "internal_error"),
        }
        for reason, public_pair in expected.items():
            with self.subTest(reason=reason):
                normalized = normalize_owner_security_failure(reason)
                self.assertIs(type(normalized), tuple)
                self.assertEqual(normalized, public_pair)
                self.assertEqual(
                    normalize_owner_security_failure(OwnerSecurityError(reason)),
                    public_pair,
                )
                with self.assertRaises(TypeError):
                    normalized[0] = 999  # type: ignore[index]

    def test_malformed_reasons_do_not_execute_or_leak_custom_behavior(self):
        malformed = (
            "unknown-private-reason",
            _StringSubclass("authentication_required"),
            True,
            None,
            _ExplosiveObject(),
        )
        for reason in malformed:
            self.assertEqual(
                normalize_owner_security_failure(reason),
                (500, "internal_error"),
            )

        error = OwnerSecurityError("invalid_csrf")
        error.reason = _ExplosiveObject()  # type: ignore[assignment]
        error.args = ("private-marker",)
        error.__cause__ = RuntimeError("private-marker")
        error.__context__ = ValueError("private-marker")
        self.assertEqual(
            normalize_owner_security_failure(error), (500, "internal_error")
        )

        absent = OwnerSecurityError.__new__(OwnerSecurityError)
        deleted = OwnerSecurityError("invalid_csrf")
        object.__delattr__(deleted, "reason")
        for malformed_error in (absent, deleted):
            self.assertEqual(malformed_error.args, ())
            self.assertEqual(
                normalize_owner_security_failure(malformed_error),
                (500, "internal_error"),
            )

        malformed_exact_reasons = (
            _StringSubclass("authentication_required"),
            True,
            _ExplosiveObject(),
            "unknown-private-reason",
        )
        for malformed_reason in malformed_exact_reasons:
            malformed_error = OwnerSecurityError("invalid_csrf")
            object.__setattr__(malformed_error, "reason", malformed_reason)
            self.assertEqual(
                normalize_owner_security_failure(malformed_error),
                (500, "internal_error"),
            )
            self.assertEqual(malformed_error.args, ())

    def test_error_constructor_never_retains_unknown_private_reason(self):
        marker = "private-owner-security-detail"
        error = OwnerSecurityError(marker)
        self.assertEqual(error.reason, "internal_error")
        self.assertEqual(error.args, ())
        self.assertNotIn(marker, str(error))
        self.assertNotIn(marker, repr(error))

        for malformed_reason in (
            _StringSubclass("authentication_required"),
            True,
            _ExplosiveObject(),
        ):
            malformed = OwnerSecurityError(malformed_reason)
            self.assertEqual(malformed.reason, "internal_error")
            self.assertEqual(malformed.args, ())


if __name__ == "__main__":
    unittest.main()
