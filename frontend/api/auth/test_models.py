"""Contract tests for the inactive Cuevion authentication records."""

import base64
import dataclasses
import importlib
import importlib.util
import inspect
import os
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest import mock

from . import models


_AUTH_DIRECTORY = Path(__file__).resolve().parent
_FRONTEND_DIRECTORY = _AUTH_DIRECTORY.parents[1]


def _run_isolated(program: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-c", program],
        cwd=_FRONTEND_DIRECTORY,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _encoded(octet: int, length: int) -> str:
    return base64.urlsafe_b64encode(bytes([octet]) * length).rstrip(b"=").decode("ascii")


USER_ID = "usr_" + _encoded(1, 16)
OTHER_USER_ID = "usr_" + _encoded(2, 16)
EMAIL_ID = "vem_" + _encoded(3, 16)
OTHER_EMAIL_ID = "vem_" + _encoded(4, 16)
IDENTITY_ID = "aid_" + _encoded(5, 16)
OTHER_IDENTITY_ID = "aid_" + _encoded(6, 16)
WORKSPACE_ID = "wsp_" + _encoded(7, 16)
OTHER_WORKSPACE_ID = "wsp_" + _encoded(8, 16)
SESSION_ID = _encoded(9, 32)
LOOKUP_DIGEST = _encoded(10, 32)
BINDING_DIGEST = _encoded(11, 32)


def user_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": 1,
        "user_id": USER_ID,
        "status": models.UserStatus.ACTIVE,
        "primary_verified_email_id": EMAIL_ID,
        "display_name": "Cuevion User",
        "security_epoch": 2,
        "created_at": 100,
        "updated_at": 101,
        "row_version": 3,
    }
    values.update(overrides)
    return values


def sample_user(**overrides: object) -> models.CuevionUser:
    return models.CuevionUser(**user_values(**overrides))


def email_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": 1,
        "email_id": EMAIL_ID,
        "user_id": USER_ID,
        "canonical_email": "owner@example.com",
        "status": models.VerifiedEmailStatus.VERIFIED,
        "verification_source": "email_otp",
        "created_at": 100,
        "verified_at": 101,
        "retired_at": None,
        "row_version": 3,
    }
    values.update(overrides)
    return values


def sample_email(**overrides: object) -> models.VerifiedEmail:
    return models.VerifiedEmail(**email_values(**overrides))


def identity_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": 1,
        "identity_id": IDENTITY_ID,
        "user_id": USER_ID,
        "issuer": "https://identity.example/tenant",
        "subject": "account|subject=123",
        "method": models.AuthenticationMethod.OIDC,
        "status": models.AuthenticationIdentityStatus.ACTIVE,
        "verified_email_id": EMAIL_ID,
        "created_at": 100,
        "last_used_at": 101,
        "row_version": 3,
    }
    values.update(overrides)
    return values


def sample_identity(**overrides: object) -> models.AuthenticationIdentity:
    return models.AuthenticationIdentity(**identity_values(**overrides))


def workspace_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": 1,
        "workspace_id": WORKSPACE_ID,
        "status": models.WorkspaceStatus.ACTIVE,
        "created_by_user_id": USER_ID,
        "created_at": 100,
        "updated_at": 101,
        "row_version": 3,
    }
    values.update(overrides)
    return values


def sample_workspace(**overrides: object) -> models.Workspace:
    return models.Workspace(**workspace_values(**overrides))


def membership_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": 1,
        "workspace_id": WORKSPACE_ID,
        "user_id": USER_ID,
        "role": models.WorkspaceRole.OWNER,
        "status": models.WorkspaceMembershipStatus.ACTIVE,
        "created_at": 100,
        "updated_at": 101,
        "row_version": 3,
    }
    values.update(overrides)
    return values


def sample_membership(**overrides: object) -> models.WorkspaceMembership:
    return models.WorkspaceMembership(**membership_values(**overrides))


def session_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": 1,
        "session_id": SESSION_ID,
        "user_id": USER_ID,
        "authentication_identity_id": IDENTITY_ID,
        "credential_lookup_digest": LOOKUP_DIGEST,
        "credential_binding_digest": BINDING_DIGEST,
        "credential_epoch": 4,
        "security_epoch": 2,
        "status": models.SessionStatus.ACTIVE,
        "authenticated_at": 100,
        "issued_at": 101,
        "last_used_at": 102,
        "idle_expires_at": 200,
        "absolute_expires_at": 300,
        "revoked_at": None,
        "revocation_reason": None,
        "row_version": 3,
    }
    values.update(overrides)
    return values


def sample_session(**overrides: object) -> models.StoredSessionSnapshot:
    return models.StoredSessionSnapshot(**session_values(**overrides))


def revoked_session(**overrides: object) -> models.StoredSessionSnapshot:
    values = {
        "status": models.SessionStatus.REVOKED,
        "revoked_at": 150,
        "revocation_reason": models.SessionRevocationReason.LOGOUT,
    }
    values.update(overrides)
    return sample_session(**values)


def _forged_record_subclass(record: object) -> object:
    previous_definition_state = models._RECORD_CLASS_DEFINITION_OPEN
    models._RECORD_CLASS_DEFINITION_OPEN = True
    try:
        subclass = type(
            f"{type(record).__name__}ForgedSubclass",
            (type(record),),
            {"__slots__": ()},
        )
    finally:
        models._RECORD_CLASS_DEFINITION_OPEN = previous_definition_state
    forged = object.__new__(subclass)
    for field in dataclasses.fields(type(record)):
        object.__setattr__(forged, field.name, getattr(record, field.name))
    return forged


def _noncanonical_pad_bit_alias(value: str) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    index = alphabet.index(value[-1])
    alias_index = index + 1 if index < len(alphabet) - 1 else index - 1
    return value[:-1] + alphabet[alias_index]


class ImportAndSurfaceTests(unittest.TestCase):
    def test_canonical_module_identity(self):
        self.assertEqual(models.__name__, "api.auth.models")
        self.assertEqual(models.__package__, "api.auth")

    def test_isolated_normal_canonical_import(self):
        completed = _run_isolated(
            "import importlib,sys\n"
            "assert 'api.auth.models' not in sys.modules\n"
            "module=importlib.import_module('api.auth.models')\n"
            "assert module is sys.modules['api.auth.models']\n"
            "assert module.UserStatus('active') is module.UserStatus.ACTIVE\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=completed.stdout + completed.stderr,
        )

    def test_isolated_top_level_and_alternate_dotted_identities_fail_closed(self):
        attempts = (
            (str(_AUTH_DIRECTORY), "models"),
            (str(_AUTH_DIRECTORY.parent), "auth.models"),
            (str(_FRONTEND_DIRECTORY.parent), "frontend.api.auth.models"),
        )
        for path_entry, module_name in attempts:
            with self.subTest(module_name=module_name):
                program = (
                    "import importlib,sys\n"
                    "original=importlib.import_module('api.auth.models')\n"
                    "identities=(original.ModelValidationError,original.UserStatus,original.CuevionUser)\n"
                    "enum_sentinel=original._ENUM_MISSING\n"
                    f"sys.path.insert(0, {path_entry!r})\n"
                    "try:\n"
                    f" importlib.import_module({module_name!r})\n"
                    "except ImportError:\n"
                    " pass\n"
                    "else:\n"
                    " raise SystemExit('alternate identity unexpectedly succeeded')\n"
                    "assert sys.modules['api.auth.models'] is original\n"
                    "assert identities == (original.ModelValidationError,original.UserStatus,original.CuevionUser)\n"
                    "assert original._ENUM_MISSING is enum_sentinel\n"
                    "assert original._RECORD_CLASS_DEFINITION_OPEN is False\n"
                    "assert original.UserStatus('active') is original.UserStatus.ACTIVE\n"
                )
                completed = _run_isolated(program)
                self.assertEqual(
                    completed.returncode,
                    0,
                    msg=completed.stdout + completed.stderr,
                )

    def test_isolated_duplicate_spec_and_reload_preserve_original_identities(self):
        path = str(_AUTH_DIRECTORY / "models.py")
        program = (
            "import base64,importlib,importlib.util,sys\n"
            "original=importlib.import_module('api.auth.models')\n"
            "identities=(original.ModelValidationError,original.UserStatus,original.CuevionUser)\n"
            "enum_sentinel=original._ENUM_MISSING\n"
            "def assert_original_usable():\n"
            " assert sys.modules['api.auth.models'] is original\n"
            " assert identities == (original.ModelValidationError,original.UserStatus,original.CuevionUser)\n"
            " assert original._ENUM_MISSING is enum_sentinel\n"
            " assert original._RECORD_CLASS_DEFINITION_OPEN is False\n"
            " assert original.UserStatus('active') is original.UserStatus.ACTIVE\n"
            " encoded=base64.urlsafe_b64encode(bytes([1])*16).rstrip(b'=').decode('ascii')\n"
            " user=original.CuevionUser(1,'usr_'+encoded,original.UserStatus.SUSPENDED,None,'User',1,1,1,1)\n"
            " assert type(user) is original.CuevionUser\n"
            "spec=importlib.util.spec_from_file_location('api.auth.models', "
            f"{path!r})\n"
            "duplicate=importlib.util.module_from_spec(spec)\n"
            "try:\n"
            " spec.loader.exec_module(duplicate)\n"
            "except ImportError:\n"
            " pass\n"
            "else:\n"
            " raise SystemExit('duplicate canonical spec unexpectedly succeeded')\n"
            "assert '_AUTH_A_MODELS_INITIALIZED' not in duplicate.__dict__\n"
            "for name in ('ModelValidationError','_ENUM_MISSING','UserStatus','_RECORD_CLASS_DEFINITION_OPEN','CuevionUser','StoredSessionSnapshot'):\n"
            " assert name not in duplicate.__dict__\n"
            "assert_original_usable()\n"
            "try:\n"
            " importlib.reload(original)\n"
            "except ImportError:\n"
            " pass\n"
            "else:\n"
            " raise SystemExit('reload unexpectedly succeeded')\n"
            "assert_original_usable()\n"
        )
        completed = _run_isolated(program)
        self.assertEqual(
            completed.returncode,
            0,
            msg=completed.stdout + completed.stderr,
        )

    def test_no_handler_or_route_surface(self):
        self.assertFalse(hasattr(models, "handler"))
        public = {name: value for name, value in vars(models).items() if not name.startswith("_")}
        self.assertNotIn("route", public)
        self.assertEqual(set(public), set(models.__all__))
        self.assertFalse(
            any(
                (inspect.isclass(value) or inspect.isfunction(value))
                and getattr(value, "__name__", None) == "handler"
                for value in public.values()
            )
        )


class EnumContractTests(unittest.TestCase):
    EXPECTED = {
        models.UserStatus: ("active", "suspended", "disabled"),
        models.VerifiedEmailStatus: ("pending", "verified", "retired"),
        models.AuthenticationMethod: ("email_otp", "oidc", "webauthn"),
        models.AuthenticationIdentityStatus: ("active", "disabled", "revoked"),
        models.WorkspaceStatus: ("active", "suspended", "archived"),
        models.WorkspaceRole: ("owner", "admin", "member"),
        models.WorkspaceMembershipStatus: ("active", "suspended", "removed"),
        models.SessionStatus: ("active", "revoked"),
        models.SessionRevocationReason: (
            "logout",
            "rotated",
            "security_change",
            "account_disabled",
            "recovery",
            "administrative",
        ),
    }

    def test_all_enums_are_closed_strings_with_only_declared_members(self):
        for enum_type, declared in self.EXPECTED.items():
            with self.subTest(enum=enum_type.__name__):
                self.assertTrue(issubclass(enum_type, str))
                self.assertEqual(tuple(member.value for member in enum_type), declared)
                for member in enum_type:
                    self.assertIs(enum_type(member.value), member)
                    self.assertIs(enum_type(member), member)

    def test_unknown_or_nonexact_enum_inputs_have_one_value_free_failure(self):
        class StringSubclass(str):
            pass

        secret = "never-reflect-this-enum-value"
        for enum_type, declared in self.EXPECTED.items():
            for rejected in (
                secret,
                StringSubclass(declared[0]),
                1,
                True,
                None,
                object(),
            ):
                with self.subTest(enum=enum_type.__name__, rejected_type=type(rejected).__name__):
                    with self.assertRaises(models.ModelValidationError) as caught:
                        enum_type(rejected)
                    self.assertIs(type(caught.exception), models.ModelValidationError)
                    self.assertEqual(caught.exception.args, ())
                    self.assertNotIn(secret, str(caught.exception))
                    self.assertNotIn(secret, repr(caught.exception))


class RecordConstructionTests(unittest.TestCase):
    def test_valid_construction_of_every_record(self):
        records = (
            sample_user(),
            sample_email(),
            sample_identity(),
            sample_workspace(),
            sample_membership(),
            sample_session(),
        )
        expected_types = (
            models.CuevionUser,
            models.VerifiedEmail,
            models.AuthenticationIdentity,
            models.Workspace,
            models.WorkspaceMembership,
            models.StoredSessionSnapshot,
        )
        self.assertEqual(tuple(type(record) for record in records), expected_types)

    def test_records_are_frozen_slotted_and_have_no_dictionary(self):
        for record in (
            sample_user(),
            sample_email(),
            sample_identity(),
            sample_workspace(),
            sample_membership(),
            sample_session(),
        ):
            with self.subTest(record_type=type(record).__name__):
                self.assertTrue(dataclasses.is_dataclass(record))
                self.assertFalse(hasattr(record, "__dict__"))
                with self.assertRaises((dataclasses.FrozenInstanceError, AttributeError)):
                    setattr(record, dataclasses.fields(record)[0].name, object())
                with self.assertRaises((dataclasses.FrozenInstanceError, AttributeError)):
                    delattr(record, dataclasses.fields(record)[0].name)

    def test_unknown_constructor_fields_fail_with_no_value_retention(self):
        secret = "unknown-field-secret"
        values = user_values()
        values[secret] = object()
        with self.assertRaises(models.ModelValidationError) as caught:
            models.CuevionUser(**values)
        self.assertEqual(caught.exception.args, ())
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(secret, repr(caught.exception))

    def test_exact_enums_are_required_in_all_record_fields(self):
        attempts = (
            lambda: sample_user(status="active"),
            lambda: sample_email(status="verified"),
            lambda: sample_identity(method="oidc"),
            lambda: sample_identity(status="active"),
            lambda: sample_workspace(status="active"),
            lambda: sample_membership(role="owner"),
            lambda: sample_membership(status="active"),
            lambda: sample_session(status="active"),
            lambda: sample_session(
                status=models.SessionStatus.REVOKED,
                revoked_at=150,
                revocation_reason="logout",
            ),
        )
        for attempt in attempts:
            with self.subTest(attempt=attempt):
                with self.assertRaises(models.ModelValidationError):
                    attempt()

    def test_bool_and_int_subclasses_are_rejected_for_every_integer_field(self):
        class IntegerSubclass(int):
            pass

        attempts = (
            (sample_user, ("schema_version", "security_epoch", "created_at", "updated_at", "row_version")),
            (sample_email, ("schema_version", "created_at", "verified_at", "row_version")),
            (sample_identity, ("schema_version", "created_at", "last_used_at", "row_version")),
            (sample_workspace, ("schema_version", "created_at", "updated_at", "row_version")),
            (sample_membership, ("schema_version", "created_at", "updated_at", "row_version")),
            (
                sample_session,
                (
                    "schema_version",
                    "credential_epoch",
                    "security_epoch",
                    "authenticated_at",
                    "issued_at",
                    "last_used_at",
                    "idle_expires_at",
                    "absolute_expires_at",
                    "row_version",
                ),
            ),
        )
        for factory, fields in attempts:
            for field in fields:
                for rejected in (False, True, IntegerSubclass(1)):
                    with self.subTest(factory=factory.__name__, field=field, rejected_type=type(rejected).__name__):
                        with self.assertRaises(models.ModelValidationError):
                            factory(**{field: rejected})
        for rejected in (False, True, IntegerSubclass(150)):
            with self.assertRaises(models.ModelValidationError):
                revoked_session(revoked_at=rejected)
            with self.assertRaises(models.ModelValidationError):
                sample_email(
                    status=models.VerifiedEmailStatus.RETIRED,
                    retired_at=rejected,
                )

    def test_record_subclasses_fail_before_overridden_post_init_can_bypass_validation(self):
        post_init_calls: list[str] = []
        cases = (
            (models.CuevionUser, user_values),
            (models.VerifiedEmail, email_values),
            (models.AuthenticationIdentity, identity_values),
            (models.Workspace, workspace_values),
            (models.WorkspaceMembership, membership_values),
            (models.StoredSessionSnapshot, session_values),
        )

        def bypass_validation(_self: object) -> None:
            post_init_calls.append("called")

        for record_type, values_factory in cases:
            class BypassMeta(type(record_type)):
                def __call__(cls, *arguments: object, **keywords: object) -> object:
                    return type.__call__(cls, *arguments, **keywords)

            def attempt_bypass() -> object:
                subclass = BypassMeta(
                    f"{record_type.__name__}BypassSubclass",
                    (record_type,),
                    {
                        "__slots__": (),
                        "__post_init__": bypass_validation,
                    },
                )
                values = values_factory()
                values["schema_version"] = 0
                return subclass(**values)

            with self.subTest(record_type=record_type.__name__):
                with self.assertRaises(models.ModelValidationError) as caught:
                    attempt_bypass()
                self.assertIs(type(caught.exception), models.ModelValidationError)
                self.assertEqual(caught.exception.args, ())
                self.assertEqual(
                    str(caught.exception),
                    "account model validation failed",
                )
                self.assertEqual(repr(caught.exception), "ModelValidationError()")
        self.assertEqual(post_init_calls, [])

    def test_schema_versions_epochs_and_row_versions_are_positive_as_required(self):
        for factory in (
            sample_user,
            sample_email,
            sample_identity,
            sample_workspace,
            sample_membership,
            sample_session,
        ):
            for version in (0, 2, -1):
                with self.subTest(factory=factory.__name__, schema_version=version):
                    with self.assertRaises(models.ModelValidationError):
                        factory(schema_version=version)
            for row_version in (0, -1):
                with self.assertRaises(models.ModelValidationError):
                    factory(row_version=row_version)
        for field, factory in (
            ("security_epoch", sample_user),
            ("credential_epoch", sample_session),
            ("security_epoch", sample_session),
        ):
            for rejected in (0, -1):
                with self.assertRaises(models.ModelValidationError):
                    factory(**{field: rejected})


class IdentifierAndEmailTests(unittest.TestCase):
    def test_every_identifier_prefix_and_decoded_length_is_enforced(self):
        cases = (
            (sample_user, "user_id", "usr_", 16),
            (sample_user, "primary_verified_email_id", "vem_", 16),
            (sample_email, "email_id", "vem_", 16),
            (sample_email, "user_id", "usr_", 16),
            (sample_identity, "identity_id", "aid_", 16),
            (sample_identity, "user_id", "usr_", 16),
            (sample_identity, "verified_email_id", "vem_", 16),
            (sample_workspace, "workspace_id", "wsp_", 16),
            (sample_workspace, "created_by_user_id", "usr_", 16),
            (sample_membership, "workspace_id", "wsp_", 16),
            (sample_membership, "user_id", "usr_", 16),
            (sample_session, "user_id", "usr_", 16),
            (sample_session, "authentication_identity_id", "aid_", 16),
        )
        for factory, field, prefix, decoded_length in cases:
            for rejected in (
                "bad_" + _encoded(20, decoded_length),
                prefix + _encoded(20, decoded_length - 1),
                prefix + _encoded(20, decoded_length + 1),
            ):
                with self.subTest(factory=factory.__name__, field=field, value_length=len(rejected)):
                    with self.assertRaises(models.ModelValidationError):
                        factory(**{field: rejected})

    def test_session_and_digest_identifiers_require_exactly_32_bytes(self):
        for field in (
            "session_id",
            "credential_lookup_digest",
            "credential_binding_digest",
        ):
            for rejected in (_encoded(30, 31), _encoded(30, 33), "x" * 43):
                with self.subTest(field=field, length=len(rejected)):
                    with self.assertRaises(models.ModelValidationError):
                        sample_session(**{field: rejected})

    def test_padded_whitespace_unicode_and_noncanonical_pad_bits_are_rejected(self):
        canonical_suffix = USER_ID[4:]
        alias_suffix = _noncanonical_pad_bit_alias(canonical_suffix)
        self.assertEqual(
            base64.urlsafe_b64decode(alias_suffix + "=="),
            base64.urlsafe_b64decode(canonical_suffix + "=="),
        )
        canonical_digest = SESSION_ID
        alias_digest = _noncanonical_pad_bit_alias(canonical_digest)
        self.assertEqual(
            base64.urlsafe_b64decode(alias_digest + "="),
            base64.urlsafe_b64decode(canonical_digest + "="),
        )
        for rejected in (
            USER_ID + "=",
            " " + USER_ID,
            USER_ID + "\n",
            USER_ID[:-1] + "é",
            "usr_" + alias_suffix,
        ):
            with self.subTest(identifier=rejected[-4:]):
                with self.assertRaises(models.ModelValidationError):
                    sample_user(user_id=rejected)
        for rejected in (
            SESSION_ID + "=",
            " " + SESSION_ID,
            SESSION_ID + "\n",
            SESSION_ID[:-1] + "é",
            alias_digest,
        ):
            with self.assertRaises(models.ModelValidationError):
                sample_session(session_id=rejected)

    def test_string_subclasses_are_rejected_for_security_identifiers(self):
        class StringSubclass(str):
            pass

        attempts = (
            (sample_user, "user_id", StringSubclass(USER_ID)),
            (sample_email, "canonical_email", StringSubclass("owner@example.com")),
            (sample_email, "verification_source", StringSubclass("email_otp")),
            (sample_identity, "issuer", StringSubclass("https://issuer.example")),
            (sample_identity, "subject", StringSubclass("subject")),
            (sample_session, "session_id", StringSubclass(SESSION_ID)),
        )
        for factory, field, rejected in attempts:
            with self.subTest(factory=factory.__name__, field=field):
                with self.assertRaises(models.ModelValidationError):
                    factory(**{field: rejected})

    def test_canonical_email_syntax_is_strict_and_never_transformed(self):
        accepted = (
            "owner@example.com",
            "owner+release@example.com",
            "first.last@example.co.uk",
            "a" * 64 + "@example.com",
            "owner@" + "a" * 63 + ".example",
        )
        for value in accepted:
            with self.subTest(value=value[:20]):
                record = sample_email(canonical_email=value)
                self.assertEqual(record.canonical_email, value)
        rejected = (
            "Owner@example.com",
            "owner@Example.com",
            " owner@example.com",
            "owner@example.com ",
            "owner@example.com\n",
            "owner@@example.com",
            "@example.com",
            "owner@",
            "owner@example",
            "owner..name@example.com",
            ".owner@example.com",
            "owner.@example.com",
            "owner@-example.com",
            "owner@example-.com",
            "rütger@example.com",
            "a" * 65 + "@example.com",
            "owner@" + "a" * 64 + ".example",
        )
        for value in rejected:
            with self.subTest(value=value[:20]):
                with self.assertRaises(models.ModelValidationError):
                    sample_email(canonical_email=value)

    def test_plus_and_dot_addresses_remain_distinct_values(self):
        plain = sample_email(canonical_email="owner@example.com")
        plus = sample_email(canonical_email="owner+tag@example.com")
        dotted = sample_email(canonical_email="own.er@example.com")
        self.assertEqual(
            {plain.canonical_email, plus.canonical_email, dotted.canonical_email},
            {"owner@example.com", "owner+tag@example.com", "own.er@example.com"},
        )

    def test_ascii_security_identifiers_reject_whitespace_controls_unicode_and_size(self):
        cases = (
            (sample_email, "verification_source"),
            (sample_identity, "issuer"),
            (sample_identity, "subject"),
        )
        for factory, field in cases:
            for rejected in ("", " leading", "trailing ", "has space", "line\nfeed", "idé", "x" * 1024):
                with self.subTest(factory=factory.__name__, field=field, rejected_length=len(rejected)):
                    with self.assertRaises(models.ModelValidationError):
                        factory(**{field: rejected})

    def test_display_name_utf8_bound_and_forbidden_unicode_categories(self):
        self.assertEqual(sample_user(display_name="Rütger").display_name, "Rütger")
        self.assertEqual(sample_user(display_name="é" * 128).display_name, "é" * 128)
        for rejected in (
            "",
            "é" * 129,
            "line\nfeed",
            "hidden\u200bformat",
            "surrogate\ud800",
        ):
            with self.subTest(rejected_length=len(rejected)):
                with self.assertRaises(models.ModelValidationError):
                    sample_user(display_name=rejected)


class StatusAndTimestampTests(unittest.TestCase):
    def test_canonical_timestamp_helper_has_one_exact_closed_domain(self):
        class IntegerSubclass(int):
            pass

        maximum = models.MAX_UNIX_UTC_SECONDS
        self.assertEqual(maximum, 253_402_300_799)
        for accepted in (0, maximum):
            with self.subTest(accepted=accepted):
                self.assertIs(models._is_timestamp(accepted), True)
        for rejected in (
            -1,
            maximum + 1,
            False,
            True,
            IntegerSubclass(0),
            0.0,
        ):
            with self.subTest(rejected_type=type(rejected).__name__):
                self.assertIs(models._is_timestamp(rejected), False)

        self.assertIs(models._is_optional_timestamp(None), True)
        self.assertIs(models._is_optional_timestamp(maximum), True)
        self.assertIs(models._is_optional_timestamp(maximum + 1), False)

    def test_timestamp_domain_has_one_exact_inclusive_maximum(self):
        maximum = models.MAX_UNIX_UTC_SECONDS
        self.assertEqual(sample_user(created_at=maximum, updated_at=maximum).updated_at, maximum)
        self.assertEqual(sample_email(created_at=maximum, verified_at=maximum).verified_at, maximum)
        self.assertEqual(sample_identity(created_at=maximum, last_used_at=maximum).last_used_at, maximum)
        self.assertEqual(sample_workspace(created_at=maximum, updated_at=maximum).updated_at, maximum)
        self.assertEqual(sample_membership(created_at=maximum, updated_at=maximum).updated_at, maximum)
        near_maximum_session = sample_session(
            authenticated_at=maximum - 4,
            issued_at=maximum - 3,
            last_used_at=maximum - 2,
            idle_expires_at=maximum - 1,
            absolute_expires_at=maximum,
        )
        self.assertEqual(near_maximum_session.absolute_expires_at, maximum)

    def test_every_model_timestamp_field_dispatches_its_exact_value(self):
        cases = (
            (
                models.CuevionUser,
                user_values(created_at=11, updated_at=12),
                (11, 12),
            ),
            (
                models.VerifiedEmail,
                email_values(
                    status=models.VerifiedEmailStatus.RETIRED,
                    created_at=21,
                    verified_at=22,
                    retired_at=23,
                ),
                (21, 22, 23),
            ),
            (
                models.AuthenticationIdentity,
                identity_values(created_at=31, last_used_at=32),
                (31, 32),
            ),
            (
                models.Workspace,
                workspace_values(created_at=41, updated_at=42),
                (41, 42),
            ),
            (
                models.WorkspaceMembership,
                membership_values(created_at=51, updated_at=52),
                (51, 52),
            ),
            (
                models.StoredSessionSnapshot,
                session_values(
                    status=models.SessionStatus.REVOKED,
                    authenticated_at=61,
                    issued_at=62,
                    last_used_at=63,
                    idle_expires_at=65,
                    absolute_expires_at=67,
                    revoked_at=66,
                    revocation_reason=models.SessionRevocationReason.LOGOUT,
                ),
                (61, 62, 63, 65, 67, 66),
            ),
        )
        canonical_helper = models._is_timestamp
        for record_type, values, expected_values in cases:
            with self.subTest(record_type=record_type.__name__):
                with mock.patch.object(
                    models, "_is_timestamp", wraps=canonical_helper
                ) as helper_spy:
                    record_type(**values)
                self.assertEqual(
                    helper_spy.call_args_list,
                    [mock.call(value) for value in expected_values],
                )

    def test_every_model_timestamp_field_routes_maximum_plus_one_to_helper(self):
        maximum_plus_one = models.MAX_UNIX_UTC_SECONDS + 1
        timestamp_fields = (
            (sample_user, ("created_at", "updated_at")),
            (sample_email, ("created_at", "verified_at", "retired_at")),
            (sample_identity, ("created_at", "last_used_at")),
            (sample_workspace, ("created_at", "updated_at")),
            (sample_membership, ("created_at", "updated_at")),
            (
                sample_session,
                (
                    "authenticated_at",
                    "issued_at",
                    "last_used_at",
                    "idle_expires_at",
                    "absolute_expires_at",
                    "revoked_at",
                ),
            ),
        )
        for factory, fields in timestamp_fields:
            for field in fields:
                with self.subTest(factory=factory.__name__, field=field):
                    canonical_helper = models._is_timestamp
                    with mock.patch.object(
                        models, "_is_timestamp", wraps=canonical_helper
                    ) as helper_spy:
                        with self.assertRaises(models.ModelValidationError):
                            factory(**{field: maximum_plus_one})
                    self.assertIn(
                        mock.call(maximum_plus_one),
                        helper_spy.call_args_list,
                    )

    def test_optional_timestamp_fields_accept_none_and_exact_maximum(self):
        maximum = models.MAX_UNIX_UTC_SECONDS
        pending_email = sample_email(
            status=models.VerifiedEmailStatus.PENDING,
            verified_at=None,
            retired_at=None,
        )
        self.assertIsNone(pending_email.verified_at)
        self.assertIsNone(pending_email.retired_at)
        self.assertEqual(
            sample_email(created_at=0, verified_at=maximum).verified_at,
            maximum,
        )
        self.assertEqual(
            sample_email(
                status=models.VerifiedEmailStatus.RETIRED,
                created_at=0,
                verified_at=0,
                retired_at=maximum,
            ).retired_at,
            maximum,
        )
        self.assertIsNone(sample_identity(last_used_at=None).last_used_at)
        self.assertEqual(
            sample_identity(created_at=0, last_used_at=maximum).last_used_at,
            maximum,
        )
        self.assertIsNone(sample_session().revoked_at)
        self.assertEqual(
            revoked_session(
                authenticated_at=0,
                issued_at=0,
                last_used_at=0,
                idle_expires_at=maximum,
                absolute_expires_at=maximum,
                revoked_at=maximum,
            ).revoked_at,
            maximum,
        )

    def test_user_primary_email_and_timestamp_invariants(self):
        self.assertEqual(sample_user(created_at=100, updated_at=100).updated_at, 100)
        for status in (models.UserStatus.SUSPENDED, models.UserStatus.DISABLED):
            self.assertIsNone(sample_user(status=status, primary_verified_email_id=None).primary_verified_email_id)
            self.assertEqual(sample_user(status=status).primary_verified_email_id, EMAIL_ID)
        with self.assertRaises(models.ModelValidationError):
            sample_user(primary_verified_email_id=None)
        with self.assertRaises(models.ModelValidationError):
            sample_user(created_at=101, updated_at=100)
        for field in ("created_at", "updated_at"):
            with self.assertRaises(models.ModelValidationError):
                sample_user(**{field: -1})

    def test_verified_email_status_dependent_timestamps_and_boundaries(self):
        pending = sample_email(
            status=models.VerifiedEmailStatus.PENDING,
            verified_at=None,
            retired_at=None,
        )
        verified = sample_email(created_at=100, verified_at=100)
        retired = sample_email(
            status=models.VerifiedEmailStatus.RETIRED,
            verified_at=100,
            retired_at=100,
        )
        self.assertIsNone(pending.verified_at)
        self.assertEqual(verified.verified_at, 100)
        self.assertEqual(retired.retired_at, 100)
        rejected = (
            {"status": models.VerifiedEmailStatus.PENDING, "verified_at": 100},
            {"status": models.VerifiedEmailStatus.PENDING, "retired_at": 100},
            {"status": models.VerifiedEmailStatus.VERIFIED, "verified_at": None},
            {"status": models.VerifiedEmailStatus.VERIFIED, "retired_at": 102},
            {"status": models.VerifiedEmailStatus.RETIRED, "retired_at": None},
            {"status": models.VerifiedEmailStatus.RETIRED, "verified_at": None, "retired_at": 102},
            {"created_at": 102, "verified_at": 101},
            {"status": models.VerifiedEmailStatus.RETIRED, "verified_at": 102, "retired_at": 101},
            {"created_at": -1},
            {"verified_at": -1},
        )
        for overrides in rejected:
            with self.subTest(overrides=tuple(overrides)):
                with self.assertRaises(models.ModelValidationError):
                    sample_email(**overrides)

    def test_identity_optional_last_use_and_ordering(self):
        self.assertIsNone(sample_identity(last_used_at=None).last_used_at)
        self.assertEqual(sample_identity(last_used_at=100).last_used_at, 100)
        for overrides in ({"created_at": -1}, {"last_used_at": -1}, {"created_at": 101, "last_used_at": 100}):
            with self.assertRaises(models.ModelValidationError):
                sample_identity(**overrides)

    def test_workspace_and_membership_timestamp_ordering(self):
        self.assertEqual(sample_workspace(updated_at=100).updated_at, 100)
        self.assertEqual(sample_membership(updated_at=100).updated_at, 100)
        for factory in (sample_workspace, sample_membership):
            for overrides in ({"created_at": -1}, {"updated_at": -1}, {"created_at": 101, "updated_at": 100}):
                with self.assertRaises(models.ModelValidationError):
                    factory(**overrides)

    def test_session_timestamp_boundaries(self):
        boundary = sample_session(
            authenticated_at=100,
            issued_at=100,
            last_used_at=100,
            idle_expires_at=101,
            absolute_expires_at=101,
        )
        self.assertEqual(boundary.idle_expires_at, boundary.absolute_expires_at)
        rejected = (
            {"authenticated_at": 102, "issued_at": 101},
            {"issued_at": 103, "last_used_at": 102},
            {"last_used_at": 200},
            {"last_used_at": 201},
            {"last_used_at": 301},
            {"idle_expires_at": 301, "absolute_expires_at": 300},
        )
        for field in (
            "authenticated_at",
            "issued_at",
            "last_used_at",
            "idle_expires_at",
            "absolute_expires_at",
        ):
            rejected += ({field: -1},)
        for overrides in rejected:
            with self.subTest(overrides=tuple(overrides)):
                with self.assertRaises(models.ModelValidationError):
                    sample_session(**overrides)

    def test_session_status_dependent_revocation_invariants(self):
        self.assertEqual(revoked_session(revoked_at=102).revoked_at, 102)
        self.assertEqual(revoked_session(revoked_at=300).revoked_at, 300)
        rejected = (
            {"revoked_at": 150},
            {"revocation_reason": models.SessionRevocationReason.LOGOUT},
            {"status": models.SessionStatus.REVOKED, "revoked_at": None, "revocation_reason": models.SessionRevocationReason.LOGOUT},
            {"status": models.SessionStatus.REVOKED, "revoked_at": 150, "revocation_reason": None},
            {"status": models.SessionStatus.REVOKED, "revoked_at": 100, "revocation_reason": models.SessionRevocationReason.LOGOUT},
            {"status": models.SessionStatus.REVOKED, "revoked_at": 101, "revocation_reason": models.SessionRevocationReason.LOGOUT},
            {"status": models.SessionStatus.REVOKED, "revoked_at": 301, "revocation_reason": models.SessionRevocationReason.LOGOUT},
            {"status": models.SessionStatus.REVOKED, "revoked_at": -1, "revocation_reason": models.SessionRevocationReason.LOGOUT},
        )
        for overrides in rejected:
            with self.subTest(overrides=tuple(overrides)):
                with self.assertRaises(models.ModelValidationError):
                    sample_session(**overrides)
        for reason in models.SessionRevocationReason:
            self.assertIs(revoked_session(revocation_reason=reason).revocation_reason, reason)


class ReprAndFieldSurfaceTests(unittest.TestCase):
    def test_stored_session_repr_excludes_both_digests(self):
        rendered = repr(sample_session())
        self.assertNotIn(LOOKUP_DIGEST, rendered)
        self.assertNotIn(BINDING_DIGEST, rendered)
        field_map = {field.name: field for field in dataclasses.fields(models.StoredSessionSnapshot)}
        self.assertFalse(field_map["credential_lookup_digest"].repr)
        self.assertFalse(field_map["credential_binding_digest"].repr)

    def test_public_record_fields_are_exact_and_have_no_raw_credentials(self):
        expected = {
            models.CuevionUser: set(user_values()),
            models.VerifiedEmail: set(email_values()),
            models.AuthenticationIdentity: set(identity_values()),
            models.Workspace: set(workspace_values()),
            models.WorkspaceMembership: set(membership_values()),
            models.StoredSessionSnapshot: set(session_values()),
        }
        forbidden = {
            "raw_cookie",
            "session_cookie",
            "session_cookie_value",
            "bearer_token",
            "access_token",
            "refresh_token",
            "id_token",
            "authorization_code",
            "oauth_code",
            "otp",
            "password",
            "password_hash",
            "magic_link",
            "challenge_secret",
            "pkce_verifier",
            "provider_client_secret",
            "provider_token",
            "mailbox_password",
            "mailbox_credential",
            "imap_secret",
            "smtp_secret",
            "encryption_key",
        }
        for record_type, exact_fields in expected.items():
            with self.subTest(record_type=record_type.__name__):
                actual = {field.name for field in dataclasses.fields(record_type)}
                self.assertEqual(actual, exact_fields)
                self.assertTrue(forbidden.isdisjoint(actual))
                self.assertEqual(set(record_type.__annotations__), exact_fields)

    def test_record_repr_does_not_invoke_or_reveal_corrupted_field_repr(self):
        class PrivateValue:
            def __repr__(self):
                raise AssertionError("private repr invoked")

        user = sample_user()
        object.__setattr__(user, "display_name", PrivateValue())
        self.assertEqual(repr(user), "CuevionUser(...)")


class CrossRecordValidationTests(unittest.TestCase):
    def assert_fixed_failure(self, callable_object, *arguments):
        with self.assertRaises(models.ModelValidationError) as caught:
            callable_object(*arguments)
        self.assertIs(type(caught.exception), models.ModelValidationError)
        self.assertEqual(caught.exception.args, ())
        self.assertEqual(str(caught.exception), "account model validation failed")
        self.assertEqual(repr(caught.exception), "ModelValidationError()")

    def test_user_primary_email_success_and_confusion_failures(self):
        self.assertIsNone(models.validate_user_primary_email(sample_user(), sample_email()))
        failures = (
            (sample_user(user_id=OTHER_USER_ID), sample_email()),
            (sample_user(primary_verified_email_id=OTHER_EMAIL_ID), sample_email()),
            (sample_user(status=models.UserStatus.SUSPENDED), sample_email()),
            (sample_user(), sample_email(user_id=OTHER_USER_ID)),
            (sample_user(), sample_email(email_id=OTHER_EMAIL_ID)),
            (
                sample_user(),
                sample_email(
                    status=models.VerifiedEmailStatus.PENDING,
                    verified_at=None,
                ),
            ),
        )
        for user, email in failures:
            with self.subTest(user_id=user.user_id, email_id=email.email_id):
                self.assert_fixed_failure(models.validate_user_primary_email, user, email)

    def test_identity_success_including_identity_without_email_link(self):
        self.assertIsNone(
            models.validate_identity_for_user(sample_identity(), sample_user(), sample_email())
        )
        unlinked = sample_identity(verified_email_id=None)
        self.assertIsNone(models.validate_identity_for_user(unlinked, sample_user(), None))
        self.assertIsNone(
            models.validate_identity_for_user(unlinked, sample_user(), sample_email())
        )

    def test_identity_rejects_cross_user_cross_email_and_inactive_confusion(self):
        user = sample_user()
        identity = sample_identity()
        failures = (
            (sample_identity(user_id=OTHER_USER_ID), user, sample_email()),
            (
                sample_identity(status=models.AuthenticationIdentityStatus.DISABLED),
                user,
                sample_email(),
            ),
            (identity, user, None),
            (identity, user, sample_email(email_id=OTHER_EMAIL_ID)),
            (identity, user, sample_email(user_id=OTHER_USER_ID)),
            (
                identity,
                user,
                sample_email(status=models.VerifiedEmailStatus.PENDING, verified_at=None),
            ),
        )
        for values in failures:
            self.assert_fixed_failure(models.validate_identity_for_user, *values)

    def test_matching_email_text_alone_never_links_an_identity(self):
        same_text_different_id = sample_email(
            email_id=OTHER_EMAIL_ID,
            canonical_email=sample_email().canonical_email,
        )
        self.assert_fixed_failure(
            models.validate_identity_for_user,
            sample_identity(verified_email_id=EMAIL_ID),
            sample_user(),
            same_text_different_id,
        )

    def test_membership_success_and_cross_workspace_user_status_failures(self):
        self.assertIsNone(
            models.validate_membership_for_user(
                sample_membership(), sample_workspace(), sample_user()
            )
        )
        failures = (
            (sample_membership(workspace_id=OTHER_WORKSPACE_ID), sample_workspace(), sample_user()),
            (sample_membership(user_id=OTHER_USER_ID), sample_workspace(), sample_user()),
            (
                sample_membership(status=models.WorkspaceMembershipStatus.SUSPENDED),
                sample_workspace(),
                sample_user(),
            ),
            (
                sample_membership(),
                sample_workspace(status=models.WorkspaceStatus.SUSPENDED),
                sample_user(),
            ),
            (
                sample_membership(),
                sample_workspace(),
                sample_user(status=models.UserStatus.SUSPENDED),
            ),
        )
        for values in failures:
            self.assert_fixed_failure(models.validate_membership_for_user, *values)

    def test_active_session_success_and_expiry_boundaries(self):
        session = sample_session()
        user = sample_user()
        identity = sample_identity()
        for now in (102, 150, 199):
            with self.subTest(now=now):
                self.assertIsNone(
                    models.validate_session_snapshot(session, user, identity, now)
                )
        for now in (100, 101, 200, 300, -1, True):
            with self.subTest(now=now):
                self.assert_fixed_failure(
                    models.validate_session_snapshot, session, user, identity, now
                )

        absolute_boundary = sample_session(
            idle_expires_at=300,
            absolute_expires_at=300,
        )
        self.assert_fixed_failure(
            models.validate_session_snapshot,
            absolute_boundary,
            user,
            identity,
            300,
        )
        future_last_use = sample_session(last_used_at=160)
        self.assert_fixed_failure(
            models.validate_session_snapshot,
            future_last_use,
            user,
            identity,
            150,
        )

    def test_session_validator_dispatches_now_to_canonical_timestamp_helper(self):
        session = sample_session(
            authenticated_at=61,
            issued_at=62,
            last_used_at=63,
            idle_expires_at=65,
            absolute_expires_at=67,
        )
        user = sample_user(created_at=11, updated_at=12)
        identity = sample_identity(created_at=31, last_used_at=32)
        canonical_helper = models._is_timestamp
        with mock.patch.object(
            models, "_is_timestamp", wraps=canonical_helper
        ) as helper_spy:
            self.assertIsNone(
                models.validate_session_snapshot(
                    session,
                    user,
                    identity,
                    64,
                )
            )
        self.assertEqual(
            helper_spy.call_args_list,
            [
                mock.call(61),
                mock.call(62),
                mock.call(63),
                mock.call(65),
                mock.call(67),
                mock.call(11),
                mock.call(12),
                mock.call(31),
                mock.call(32),
                mock.call(64),
            ],
        )

    def test_corrupted_exact_session_timestamp_records_are_revalidated(self):
        user = sample_user()
        identity = sample_identity()
        corruptions = (
            {"authenticated_at": 103},
            {"issued_at": 103},
            {"last_used_at": 200},
            {"last_used_at": 201},
            {"last_used_at": 301},
            {"idle_expires_at": 301},
            {"revoked_at": 150},
            {"revocation_reason": models.SessionRevocationReason.LOGOUT},
            {
                "revoked_at": 150,
                "revocation_reason": models.SessionRevocationReason.LOGOUT,
            },
        )
        for overrides in corruptions:
            session = sample_session()
            for field_name, value in overrides.items():
                object.__setattr__(session, field_name, value)
            with self.subTest(fields=tuple(overrides)):
                self.assert_fixed_failure(
                    models.validate_session_snapshot,
                    session,
                    user,
                    identity,
                    150,
                )

    def test_session_rejects_identity_user_epoch_and_status_confusion(self):
        session = sample_session()
        user = sample_user()
        identity = sample_identity()
        failures = (
            (sample_session(user_id=OTHER_USER_ID), user, identity, 150),
            (session, sample_user(user_id=OTHER_USER_ID), identity, 150),
            (session, user, sample_identity(user_id=OTHER_USER_ID), 150),
            (
                sample_session(authentication_identity_id=OTHER_IDENTITY_ID),
                user,
                identity,
                150,
            ),
            (session, user, sample_identity(identity_id=OTHER_IDENTITY_ID), 150),
            (sample_session(security_epoch=3), user, identity, 150),
            (session, sample_user(security_epoch=3), identity, 150),
            (session, sample_user(status=models.UserStatus.SUSPENDED), identity, 150),
            (
                session,
                user,
                sample_identity(status=models.AuthenticationIdentityStatus.REVOKED),
                150,
            ),
            (revoked_session(), user, identity, 150),
        )
        for values in failures:
            self.assert_fixed_failure(models.validate_session_snapshot, *values)

    def test_validators_reject_dict_namespace_duck_and_record_subclasses(self):
        class Duck:
            pass

        exact_user = sample_user()
        forged_subclass = _forged_record_subclass(exact_user)
        for rejected in ({}, types.SimpleNamespace(**user_values()), Duck(), forged_subclass):
            with self.subTest(rejected_type=type(rejected).__name__):
                self.assert_fixed_failure(
                    models.validate_user_primary_email, rejected, sample_email()
                )

        user = sample_user()
        email = sample_email()
        identity = sample_identity()
        membership = sample_membership()
        workspace = sample_workspace()
        session = sample_session()
        exact_type_cases = (
            (
                models.validate_user_primary_email,
                (_forged_record_subclass(user), email),
            ),
            (
                models.validate_user_primary_email,
                (user, _forged_record_subclass(email)),
            ),
            (
                models.validate_identity_for_user,
                (_forged_record_subclass(identity), user, email),
            ),
            (
                models.validate_identity_for_user,
                (identity, _forged_record_subclass(user), email),
            ),
            (
                models.validate_identity_for_user,
                (identity, user, _forged_record_subclass(email)),
            ),
            (
                models.validate_membership_for_user,
                (_forged_record_subclass(membership), workspace, user),
            ),
            (
                models.validate_membership_for_user,
                (membership, _forged_record_subclass(workspace), user),
            ),
            (
                models.validate_membership_for_user,
                (membership, workspace, _forged_record_subclass(user)),
            ),
            (
                models.validate_session_snapshot,
                (_forged_record_subclass(session), user, identity, 150),
            ),
            (
                models.validate_session_snapshot,
                (session, _forged_record_subclass(user), identity, 150),
            ),
            (
                models.validate_session_snapshot,
                (session, user, _forged_record_subclass(identity), 150),
            ),
        )
        for validator, arguments in exact_type_cases:
            with self.subTest(
                validator=validator.__name__,
                argument_types=tuple(type(value).__name__ for value in arguments),
            ):
                self.assert_fixed_failure(validator, *arguments)

    def test_corrupted_and_partially_initialized_exact_records_fail_safely(self):
        touched: list[str] = []

        class PrivateObject:
            def __eq__(self, _other):
                touched.append("eq")
                raise AssertionError("equality invoked")

            def __hash__(self):
                touched.append("hash")
                raise AssertionError("hash invoked")

            def __repr__(self):
                touched.append("repr")
                raise AssertionError("repr invoked")

            def __str__(self):
                touched.append("str")
                raise AssertionError("str invoked")

            def __int__(self):
                touched.append("int")
                raise AssertionError("int invoked")

        corrupted = sample_user()
        object.__setattr__(corrupted, "user_id", PrivateObject())
        self.assert_fixed_failure(
            models.validate_user_primary_email, corrupted, sample_email()
        )
        self.assertEqual(touched, [])

        missing = sample_user()
        object.__delattr__(missing, "user_id")
        self.assert_fixed_failure(
            models.validate_user_primary_email, missing, sample_email()
        )
        partial = object.__new__(models.CuevionUser)
        self.assert_fixed_failure(
            models.validate_user_primary_email, partial, sample_email()
        )


class FixedFailureTests(unittest.TestCase):
    def test_model_validation_error_ignores_all_supplied_values(self):
        secret = "private-error-input"
        error = models.ModelValidationError(secret, private=object())
        self.assertEqual(error.args, ())
        self.assertEqual(str(error), "account model validation failed")
        self.assertEqual(repr(error), "ModelValidationError()")
        self.assertNotIn(secret, str(error))
        self.assertNotIn(secret, repr(error))

    def test_constructor_failures_are_exact_fixed_and_value_free(self):
        secret = "rejected-private-identifier"
        attempts = (
            lambda: sample_user(user_id=secret),
            lambda: sample_email(canonical_email=secret),
            lambda: sample_identity(subject=secret + " "),
            lambda: sample_session(credential_lookup_digest=secret),
        )
        for attempt in attempts:
            with self.assertRaises(models.ModelValidationError) as caught:
                attempt()
            error = caught.exception
            self.assertIs(type(error), models.ModelValidationError)
            self.assertEqual(error.args, ())
            self.assertNotIn(secret, str(error))
            self.assertNotIn(secret, repr(error))
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)

    def test_every_important_internal_failure_is_clean_inside_private_handler(self):
        marker = "distinctive-private-source-exception-marker"
        self.assertIsNone(models._raise_validation_error.__closure__)
        corrupted = sample_session()
        object.__setattr__(corrupted, "last_used_at", corrupted.idle_expires_at)

        unknown_fields = user_values()
        unknown_fields["private_unknown_constructor_field"] = object()
        attempts = (
            ("record post-init", lambda: sample_user(display_name="")),
            (
                "unknown constructor field",
                lambda: models.CuevionUser(**unknown_fields),
            ),
            ("enum type", lambda: models.SessionStatus("private-invalid-status")),
            ("identifier", lambda: sample_user(user_id="private-invalid-user-id")),
            (
                "cross-record validator",
                lambda: models.validate_user_primary_email(
                    sample_user(),
                    sample_email(user_id=OTHER_USER_ID),
                ),
            ),
            (
                "corrupted exact record",
                lambda: models.validate_session_snapshot(
                    corrupted,
                    sample_user(),
                    sample_identity(),
                    150,
                ),
            ),
        )
        for label, attempt in attempts:
            with self.subTest(path=label):
                try:
                    raise RuntimeError(marker)
                except RuntimeError:
                    try:
                        attempt()
                    except models.ModelValidationError as error:
                        captured = error
                    else:
                        self.fail("invalid model operation unexpectedly succeeded")
                self.assertIs(type(captured), models.ModelValidationError)
                self.assertEqual(captured.args, ())
                self.assertEqual(str(captured), "account model validation failed")
                self.assertEqual(repr(captured), "ModelValidationError()")
                self.assertIsNone(captured.__context__)
                self.assertIsNone(captured.__cause__)
                self.assertEqual(vars(captured), {})
                self.assertNotIn(marker, captured.args)
                self.assertNotIn(marker, str(captured))
                self.assertNotIn(marker, repr(captured))

    def test_model_construction_does_not_swallow_baseexception(self):
        class PrivateStop(BaseException):
            pass

        stop = PrivateStop()
        original_validator = models._cuevion_user_values

        def stop_validation(_value: object) -> None:
            raise stop

        models._cuevion_user_values = stop_validation
        try:
            with self.assertRaises(PrivateStop) as raised:
                sample_user()
        finally:
            models._cuevion_user_values = original_validator
        self.assertIs(raised.exception, stop)


if __name__ == "__main__":
    unittest.main()
