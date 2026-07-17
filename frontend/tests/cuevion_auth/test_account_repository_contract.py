"""Security tests for the inactive initial-account repository contract."""

import ast
import base64
import contextlib
import dataclasses
import inspect
import json
import os
import pickle
from pathlib import Path, PurePosixPath
import subprocess
import sys
import types
import typing
import unittest
from unittest import mock

from api.auth import models as auth_models
from cuevion_auth import account_repository_contract as contract


_TEST_DIRECTORY = Path(__file__).resolve().parent
_FRONTEND_DIRECTORY = _TEST_DIRECTORY.parents[1]
_SOURCE_DIRECTORY = _FRONTEND_DIRECTORY / "cuevion_auth"
_SOURCE_PATH = _SOURCE_DIRECTORY / "account_repository_contract.py"
_DOCUMENTATION_PATH = (
    _SOURCE_DIRECTORY / "ACCOUNT_REPOSITORY_ACTIVATION_REQUIREMENTS.md"
)


def _b64(octet: int, length: int) -> str:
    return base64.urlsafe_b64encode(bytes((octet,)) * length).rstrip(b"=").decode(
        "ascii"
    )


USER_ID = "usr_" + _b64(1, 16)
OTHER_USER_ID = "usr_" + _b64(2, 16)
EMAIL_ID = "vem_" + _b64(3, 16)
OTHER_EMAIL_ID = "vem_" + _b64(4, 16)
IDENTITY_ID = "aid_" + _b64(5, 16)
OTHER_IDENTITY_ID = "aid_" + _b64(6, 16)
WORKSPACE_ID = "wsp_" + _b64(7, 16)
OTHER_WORKSPACE_ID = "wsp_" + _b64(8, 16)
OPERATION_DIGEST = _b64(9, 32)
OTHER_OPERATION_DIGEST = _b64(10, 32)
ASSERTION_ID = _b64(11, 32)
OTHER_ASSERTION_ID = _b64(12, 32)
SECURITY_EVENT_ID = "sev_" + _b64(13, 16)
OTHER_SECURITY_EVENT_ID = "sev_" + _b64(14, 16)
SENSITIVE_RECORD_MARKERS = (
    OPERATION_DIGEST,
    ASSERTION_ID,
    SECURITY_EVENT_ID,
    USER_ID,
    EMAIL_ID,
    IDENTITY_ID,
    WORKSPACE_ID,
    "Initial Owner",
    "initial.owner@example.test",
    "trusted_coordinator",
    "https://identity.example.test/tenant",
    "opaque-subject-A",
    "production.eu",
    "initial-account-coordinator:v1",
)


def _operation_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": 1,
        "derivation_key_epoch": 1,
        "operation_digest": OPERATION_DIGEST,
    }
    values.update(overrides)
    return values


def _operation(**overrides: object) -> contract.InitialAccountOperationReference:
    return contract.InitialAccountOperationReference(
        **_operation_values(**overrides)
    )


def _user_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": 1,
        "user_id": USER_ID,
        "status": auth_models.UserStatus.ACTIVE,
        "primary_verified_email_id": EMAIL_ID,
        "display_name": "Initial Owner",
        "security_epoch": 1,
        "created_at": 0,
        "updated_at": 1,
        "row_version": 1,
    }
    values.update(overrides)
    return values


def _user(**overrides: object) -> auth_models.CuevionUser:
    return auth_models.CuevionUser(**_user_values(**overrides))


def _email_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": 1,
        "email_id": EMAIL_ID,
        "user_id": USER_ID,
        "canonical_email": "initial.owner@example.test",
        "status": auth_models.VerifiedEmailStatus.VERIFIED,
        "verification_source": "trusted_coordinator",
        "created_at": 0,
        "verified_at": 1,
        "retired_at": None,
        "row_version": 1,
    }
    values.update(overrides)
    return values


def _email(**overrides: object) -> auth_models.VerifiedEmail:
    return auth_models.VerifiedEmail(**_email_values(**overrides))


def _identity_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": 1,
        "identity_id": IDENTITY_ID,
        "user_id": USER_ID,
        "issuer": "https://identity.example.test/tenant",
        "subject": "opaque-subject-A",
        "method": auth_models.AuthenticationMethod.OIDC,
        "status": auth_models.AuthenticationIdentityStatus.ACTIVE,
        "verified_email_id": EMAIL_ID,
        "created_at": 1,
        "last_used_at": None,
        "row_version": 1,
    }
    values.update(overrides)
    return values


def _identity(**overrides: object) -> auth_models.AuthenticationIdentity:
    return auth_models.AuthenticationIdentity(**_identity_values(**overrides))


def _workspace_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": 1,
        "workspace_id": WORKSPACE_ID,
        "status": auth_models.WorkspaceStatus.ACTIVE,
        "created_by_user_id": USER_ID,
        "created_at": 1,
        "updated_at": 1,
        "row_version": 1,
    }
    values.update(overrides)
    return values


def _workspace(**overrides: object) -> auth_models.Workspace:
    return auth_models.Workspace(**_workspace_values(**overrides))


def _membership_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": 1,
        "workspace_id": WORKSPACE_ID,
        "user_id": USER_ID,
        "role": auth_models.WorkspaceRole.OWNER,
        "status": auth_models.WorkspaceMembershipStatus.ACTIVE,
        "created_at": 1,
        "updated_at": 1,
        "row_version": 1,
    }
    values.update(overrides)
    return values


def _membership(**overrides: object) -> auth_models.WorkspaceMembership:
    return auth_models.WorkspaceMembership(**_membership_values(**overrides))


def _evidence_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": 1,
        "trust_domain": "production.eu",
        "verification_coordinator_id": "initial-account-coordinator:v1",
        "assertion_id": ASSERTION_ID,
        "issuer": "https://identity.example.test/tenant",
        "subject": "opaque-subject-A",
        "authentication_method": auth_models.AuthenticationMethod.OIDC,
        "canonical_verified_email": "initial.owner@example.test",
        "verified_at": 1,
        "issued_at": 2,
        "expires_at": 3,
    }
    values.update(overrides)
    return values


def _evidence(**overrides: object) -> contract.VerifiedAuthenticationEvidence:
    return contract.VerifiedAuthenticationEvidence(
        **_evidence_values(**overrides)
    )


def _security_event_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": 1,
        "event_id": SECURITY_EVENT_ID,
        "event_type": contract.InitialSecurityEventType.INITIAL_ACCOUNT_CREATED,
    }
    values.update(overrides)
    return values


def _security_event(
    **overrides: object,
) -> contract.InitialSecurityEventRequest:
    return contract.InitialSecurityEventRequest(
        **_security_event_values(**overrides)
    )


def _request_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "request_version": 1,
        "operation_reference": _operation(),
        "user": _user(),
        "verified_email": _email(),
        "authentication_identity": _identity(),
        "workspace": _workspace(),
        "workspace_membership": _membership(),
        "authentication_evidence": _evidence(),
        "security_event": _security_event(),
    }
    values.update(overrides)
    return values


def _request(**overrides: object) -> contract.InitialAccountCreationRequest:
    return contract.InitialAccountCreationRequest(
        **_request_values(**overrides)
    )


def _receipt_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": 1,
        "user_id": USER_ID,
        "verified_email_id": EMAIL_ID,
        "authentication_identity_id": IDENTITY_ID,
        "workspace_id": WORKSPACE_ID,
        "security_event_id": SECURITY_EVENT_ID,
    }
    values.update(overrides)
    return values


def _receipt(**overrides: object) -> contract.InitialAccountCreationReceipt:
    return contract.InitialAccountCreationReceipt(**_receipt_values(**overrides))


def _result(
    outcome: contract.InitialAccountCreationOutcome,
    *,
    conflict_reason: contract.InitialAccountConflictReason | None = None,
    receipt: contract.InitialAccountCreationReceipt | None = None,
) -> contract.InitialAccountCreationResult:
    return contract.InitialAccountCreationResult(
        outcome=outcome,
        conflict_reason=conflict_reason,
        receipt=receipt,
    )


def _all_contract_records() -> tuple[object, ...]:
    receipt = _receipt()
    return (
        _operation(),
        _evidence(),
        _security_event(),
        _request(),
        receipt,
        _result(
            contract.InitialAccountCreationOutcome.CREATED,
            receipt=receipt,
        ),
    )


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


class ContractTestCase(unittest.TestCase):
    def assert_contract_error(
        self,
        callable_object: object,
        *,
        private_markers: tuple[str, ...] = (),
    ) -> contract.AccountRepositoryContractValidationError:
        try:
            callable_object()  # type: ignore[operator]
        except contract.AccountRepositoryContractValidationError as error:
            self.assertIs(
                type(error), contract.AccountRepositoryContractValidationError
            )
            self.assertEqual(error.args, ())
            self.assertEqual(
                str(error), "invalid initial account repository contract value"
            )
            self.assertEqual(
                repr(error), "AccountRepositoryContractValidationError()"
            )
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)
            for marker in private_markers:
                self.assertNotIn(marker, str(error))
                self.assertNotIn(marker, repr(error))
                self.assertNotIn(marker, repr(error.args))
            return error
        self.fail("contract validation failure was not raised")

    def assert_module_traceback_is_safe(
        self,
        callable_object: object,
        *,
        private_objects: tuple[object, ...],
        private_markers: tuple[str, ...],
    ) -> contract.AccountRepositoryContractValidationError:
        error = self.assert_contract_error(
            callable_object, private_markers=private_markers
        )
        source_filename = os.path.realpath(_SOURCE_PATH)
        module_frames = 0
        seen: set[int] = set()
        auth_record_types = (
            auth_models.CuevionUser,
            auth_models.VerifiedEmail,
            auth_models.AuthenticationIdentity,
            auth_models.Workspace,
            auth_models.WorkspaceMembership,
        )

        def inspect_value(value: object) -> None:
            if any(value is private for private in private_objects):
                self.fail("module traceback retained a private object")
            if isinstance(value, BaseException) and value is not error:
                self.fail("module traceback retained a private exception")
            value_type = type(value)
            if value_type is str:
                if any(marker in value for marker in private_markers):
                    self.fail("module traceback retained private text")
                return
            if value_type is bytes:
                if any(
                    marker.encode("utf-8") in value
                    for marker in private_markers
                ):
                    self.fail("module traceback retained private bytes")
                return

            identity = id(value)
            if identity in seen:
                return
            seen.add(identity)

            contract_fields = PublicSurfaceTests.RECORD_FIELDS.get(value_type)
            if contract_fields is not None:
                for field_name in contract_fields:
                    try:
                        nested = object.__getattribute__(value, field_name)
                    except AttributeError:
                        continue
                    inspect_value(nested)
                return
            if value_type in auth_record_types:
                for field in dataclasses.fields(value_type):
                    try:
                        nested = object.__getattribute__(value, field.name)
                    except AttributeError:
                        continue
                    inspect_value(nested)
                return
            if isinstance(value, type):
                inspect_value(type.__getattribute__(value, "__name__"))
                inspect_value(type.__getattribute__(value, "__dict__"))
                return
            if value_type is types.MappingProxyType:
                for key, nested in types.MappingProxyType.items(value):
                    inspect_value(key)
                    inspect_value(nested)
                return
            if isinstance(value, dict):
                for key, nested in dict.items(value):
                    inspect_value(key)
                    inspect_value(nested)
                return
            if isinstance(value, list):
                iterator = list.__iter__(value)
            elif isinstance(value, tuple):
                iterator = tuple.__iter__(value)
            elif isinstance(value, set):
                iterator = set.__iter__(value)
            elif isinstance(value, frozenset):
                iterator = frozenset.__iter__(value)
            else:
                return
            for nested in iterator:
                inspect_value(nested)

        traceback = error.__traceback__
        while traceback is not None:
            frame = traceback.tb_frame
            if os.path.realpath(frame.f_code.co_filename) == source_filename:
                module_frames += 1
                for local_name, local_value in dict.items(frame.f_locals):
                    inspect_value(local_name)
                    inspect_value(local_value)
            traceback = traceback.tb_next
        self.assertGreater(module_frames, 0)
        return error


class PublicSurfaceTests(ContractTestCase):
    EXPECTED_ALL = (
        "AccountRepositoryContractValidationError",
        "InitialAccountCreationOutcome",
        "InitialAccountConflictReason",
        "NEW_OPERATION_CONFLICT_PRECEDENCE",
        "InitialSecurityEventType",
        "InitialAccountOperationReference",
        "VerifiedAuthenticationEvidence",
        "InitialSecurityEventRequest",
        "InitialAccountCreationRequest",
        "InitialAccountCreationReceipt",
        "InitialAccountCreationResult",
        "validate_initial_account_creation_request",
        "initial_account_creation_requests_are_replay_equivalent",
        "InitialAccountRepository",
    )

    RECORD_FIELDS = {
        contract.InitialAccountOperationReference: (
            "schema_version",
            "derivation_key_epoch",
            "operation_digest",
        ),
        contract.VerifiedAuthenticationEvidence: (
            "schema_version",
            "trust_domain",
            "verification_coordinator_id",
            "assertion_id",
            "issuer",
            "subject",
            "authentication_method",
            "canonical_verified_email",
            "verified_at",
            "issued_at",
            "expires_at",
        ),
        contract.InitialSecurityEventRequest: (
            "schema_version",
            "event_id",
            "event_type",
        ),
        contract.InitialAccountCreationRequest: (
            "request_version",
            "operation_reference",
            "user",
            "verified_email",
            "authentication_identity",
            "workspace",
            "workspace_membership",
            "authentication_evidence",
            "security_event",
        ),
        contract.InitialAccountCreationReceipt: (
            "schema_version",
            "user_id",
            "verified_email_id",
            "authentication_identity_id",
            "workspace_id",
            "security_event_id",
        ),
        contract.InitialAccountCreationResult: (
            "outcome",
            "conflict_reason",
            "receipt",
        ),
    }

    def test_exact_public_exports_and_canonical_identity(self):
        self.assertEqual(contract.__all__, self.EXPECTED_ALL)
        self.assertEqual(
            {name for name in vars(contract) if not name.startswith("_")},
            set(self.EXPECTED_ALL),
        )
        self.assertEqual(
            contract.__name__, "cuevion_auth.account_repository_contract"
        )
        self.assertEqual(contract.__package__, "cuevion_auth")
        self.assertEqual(
            contract.__spec__.name, "cuevion_auth.account_repository_contract"
        )
        self.assertIs(
            contract, sys.modules["cuevion_auth.account_repository_contract"]
        )
        self.assertFalse((_SOURCE_DIRECTORY / "__init__.py").exists())

    def test_closed_enums_have_exact_members_and_values(self):
        class StringSubclass(str):
            pass

        expected = {
            contract.InitialAccountCreationOutcome: (
                ("CREATED", "created"),
                ("EXACT_REPLAY", "exact_replay"),
                ("CONFLICT", "conflict"),
                ("AMBIGUOUS", "ambiguous"),
                ("UNAVAILABLE", "unavailable"),
                ("INTERNAL_ERROR", "internal_error"),
            ),
            contract.InitialAccountConflictReason: (
                (
                    "OPERATION_REFERENCE_MISMATCH",
                    "operation_reference_mismatch",
                ),
                ("AUTHORITY_ALREADY_CLAIMED", "authority_already_claimed"),
                ("EVIDENCE_ALREADY_CONSUMED", "evidence_already_consumed"),
                ("RECORD_ID_COLLISION", "record_id_collision"),
            ),
            contract.InitialSecurityEventType: (
                ("INITIAL_ACCOUNT_CREATED", "initial_account_created"),
            ),
        }
        for enum_type, declared in expected.items():
            with self.subTest(enum=enum_type.__name__):
                self.assertTrue(issubclass(enum_type, str))
                self.assertEqual(tuple(enum_type.__members__.items()), tuple(
                    (name, getattr(enum_type, name)) for name, _value in declared
                ))
                self.assertEqual(
                    tuple((member.name, member.value) for member in enum_type),
                    declared,
                )
                for member in enum_type:
                    self.assertIs(enum_type(member), member)
                    self.assertIs(enum_type(member.value), member)
                self.assert_contract_error(lambda: enum_type("not-declared"))
                self.assert_contract_error(
                    lambda enum_type=enum_type, declared=declared: enum_type(
                        StringSubclass(declared[0][1])
                    )
                )

        self.assertNotIn(
            "OPERATION_AUTHORIZATION_EXPIRED",
            contract.InitialAccountConflictReason.__members__,
        )

    def test_new_operation_conflict_precedence_is_exact_and_reuses_enum(self):
        self.assertEqual(
            contract.NEW_OPERATION_CONFLICT_PRECEDENCE,
            (
                contract.InitialAccountConflictReason.EVIDENCE_ALREADY_CONSUMED,
                contract.InitialAccountConflictReason.AUTHORITY_ALREADY_CLAIMED,
                contract.InitialAccountConflictReason.RECORD_ID_COLLISION,
            ),
        )
        self.assertTrue(
            all(
                type(reason) is contract.InitialAccountConflictReason
                for reason in contract.NEW_OPERATION_CONFLICT_PRECEDENCE
            )
        )

    def test_records_have_exact_fields_and_exact_public_signatures(self):
        for record_type, expected_fields in self.RECORD_FIELDS.items():
            with self.subTest(record=record_type.__name__):
                self.assertFalse(dataclasses.is_dataclass(record_type))
                self.assertEqual(
                    tuple(record_type.__slots__),
                    expected_fields,
                )
                self.assertEqual(
                    tuple(typing.get_type_hints(record_type)),
                    expected_fields,
                )
                initializer = inspect.signature(record_type.__init__)
                self.assertEqual(
                    tuple(initializer.parameters),
                    ("self", *expected_fields),
                )
                constructor = inspect.signature(record_type)
                self.assertEqual(
                    tuple(constructor.parameters),
                    expected_fields,
                )
                for parameter in initializer.parameters.values():
                    self.assertIs(parameter.default, inspect.Parameter.empty)
                    self.assertIs(
                        parameter.kind,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    )
                for parameter in constructor.parameters.values():
                    self.assertIs(parameter.default, inspect.Parameter.empty)
                    self.assertIs(
                        parameter.kind,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    )

        request_hints = typing.get_type_hints(
            contract.InitialAccountCreationRequest
        )
        self.assertIs(request_hints["user"], auth_models.CuevionUser)
        self.assertIs(
            request_hints["verified_email"], auth_models.VerifiedEmail
        )
        self.assertIs(
            request_hints["authentication_identity"],
            auth_models.AuthenticationIdentity,
        )
        self.assertIs(request_hints["workspace"], auth_models.Workspace)
        self.assertIs(
            request_hints["workspace_membership"],
            auth_models.WorkspaceMembership,
        )

        validator = inspect.signature(
            contract.validate_initial_account_creation_request
        )
        self.assertEqual(tuple(validator.parameters), ("request",))
        self.assertIs(
            typing.get_type_hints(
                contract.validate_initial_account_creation_request
            )["request"],
            contract.InitialAccountCreationRequest,
        )
        self.assertIs(
            typing.get_type_hints(
                contract.validate_initial_account_creation_request
            )["return"],
            type(None),
        )

        replay = inspect.signature(
            contract.initial_account_creation_requests_are_replay_equivalent
        )
        self.assertEqual(tuple(replay.parameters), ("first", "second"))
        replay_hints = typing.get_type_hints(
            contract.initial_account_creation_requests_are_replay_equivalent
        )
        self.assertIs(replay_hints["first"], contract.InitialAccountCreationRequest)
        self.assertIs(replay_hints["second"], contract.InitialAccountCreationRequest)
        self.assertIs(replay_hints["return"], bool)
        for signature in (validator, replay):
            for parameter in signature.parameters.values():
                self.assertIs(parameter.default, inspect.Parameter.empty)
                self.assertIs(
                    parameter.kind, inspect.Parameter.POSITIONAL_OR_KEYWORD
                )

    def test_repository_protocol_is_inactive_nonruntime_and_has_one_method(self):
        protocol = contract.InitialAccountRepository
        self.assertTrue(protocol._is_protocol)
        self.assertFalse(getattr(protocol, "_is_runtime_protocol", False))
        methods = {
            name
            for name, value in protocol.__dict__.items()
            if inspect.isfunction(value) and not name.startswith("_")
        }
        self.assertEqual(methods, {"create_initial_account"})
        signature = inspect.signature(protocol.create_initial_account)
        self.assertEqual(tuple(signature.parameters), ("self", "request"))
        for parameter in signature.parameters.values():
            self.assertIs(parameter.default, inspect.Parameter.empty)
            self.assertIs(
                parameter.kind, inspect.Parameter.POSITIONAL_OR_KEYWORD
            )
        hints = typing.get_type_hints(protocol.create_initial_account)
        self.assertIs(hints["request"], contract.InitialAccountCreationRequest)
        self.assertIs(hints["return"], contract.InitialAccountCreationResult)
        with self.assertRaises(TypeError):
            isinstance(object(), protocol)
        with self.assertRaises(TypeError):
            protocol()

    def test_records_are_frozen_slotted_nonsubclassable_and_value_free(self):
        records = _all_contract_records()
        private_values = (
            OPERATION_DIGEST,
            ASSERTION_ID,
            SECURITY_EVENT_ID,
            USER_ID,
            EMAIL_ID,
            IDENTITY_ID,
            WORKSPACE_ID,
            "initial.owner@example.test",
            "opaque-subject-A",
        )
        for record in records:
            record_type = type(record)
            with self.subTest(record=record_type.__name__):
                self.assertFalse(dataclasses.is_dataclass(record))
                self.assertFalse(hasattr(record, "__dict__"))
                self.assertEqual(
                    {
                        name
                        for name in dir(record)
                        if not name.startswith("_")
                    },
                    set(self.RECORD_FIELDS[record_type]),
                )
                field_name = self.RECORD_FIELDS[record_type][0]
                original = object.__getattribute__(record, field_name)
                self.assert_contract_error(
                    lambda: setattr(record, field_name, object())
                )
                self.assertIs(
                    object.__getattribute__(record, field_name), original
                )
                self.assert_contract_error(
                    lambda: delattr(record, field_name)
                )
                self.assertIs(
                    object.__getattribute__(record, field_name), original
                )
                for rendered in (str(record), repr(record)):
                    self.assertIn(record_type.__name__, rendered)
                    for private_value in private_values:
                        self.assertNotIn(private_value, rendered)
                for forbidden_api in (
                    "asdict",
                    "astuple",
                    "to_dict",
                    "to_tuple",
                    "from_dict",
                    "serialize",
                    "deserialize",
                    "__iter__",
                    "items",
                    "keys",
                    "values",
                ):
                    self.assertFalse(hasattr(record, forbidden_api))

                self.assert_contract_error(
                    lambda record_type=record_type: type(
                        f"{record_type.__name__}Subclass",
                        (record_type,),
                        {"__slots__": ()},
                    )
                )


class StructuralRecordValidationTests(ContractTestCase):
    def test_operation_digest_epoch_and_version_are_exact(self):
        self.assertIs(type(_operation()), contract.InitialAccountOperationReference)
        for field, rejected in (
            ("schema_version", 0),
            ("schema_version", 2),
            ("schema_version", True),
            ("derivation_key_epoch", 0),
            ("derivation_key_epoch", -1),
            ("derivation_key_epoch", 4_294_967_296),
            ("derivation_key_epoch", True),
            ("operation_digest", "a" * 42),
            ("operation_digest", "a" * 43),
            ("operation_digest", "a" * 44),
            ("operation_digest", OPERATION_DIGEST + "="),
            ("operation_digest", "!" + OPERATION_DIGEST[1:]),
        ):
            with self.subTest(field=field, rejected=rejected):
                self.assert_contract_error(
                    lambda field=field, rejected=rejected: _operation(
                        **{field: rejected}
                    )
                )
        self.assertIs(
            type(_operation(derivation_key_epoch=4_294_967_295)),
            contract.InitialAccountOperationReference,
        )

    def test_assertion_event_and_coordinator_identifiers_are_canonical(self):
        for rejected in (
            "a" * 42,
            "a" * 43,
            "a" * 44,
            ASSERTION_ID + "=",
            "!" + ASSERTION_ID[1:],
        ):
            with self.subTest(assertion_id=rejected):
                self.assert_contract_error(
                    lambda rejected=rejected: _evidence(assertion_id=rejected)
                )
        for rejected in (
            "sev_" + "a" * 21,
            "sev_" + "a" * 22,
            "sev_" + "a" * 23,
            "sev_" + _b64(1, 16) + "=",
            "evt_" + _b64(1, 16),
            "sev_" + "!" + _b64(1, 16)[1:],
        ):
            with self.subTest(event_id=rejected):
                self.assert_contract_error(
                    lambda rejected=rejected: _security_event(event_id=rejected)
                )
        for field in ("trust_domain", "verification_coordinator_id"):
            for rejected in ("", "a" * 129, "contains space", "ümlaut", "slash/x"):
                with self.subTest(field=field, rejected=rejected):
                    self.assert_contract_error(
                        lambda field=field, rejected=rejected: _evidence(
                            **{field: rejected}
                        )
                    )

    def test_exact_scalar_enum_and_nested_types_reject_subclasses_and_ducks(self):
        class IntegerSubclass(int):
            pass

        class StringSubclass(str):
            pass

        for factory, field, valid_value in (
            (_operation, "schema_version", 1),
            (_operation, "derivation_key_epoch", 1),
            (_evidence, "schema_version", 1),
            (_evidence, "verified_at", 1),
            (_evidence, "issued_at", 2),
            (_evidence, "expires_at", 3),
            (_security_event, "schema_version", 1),
        ):
            for rejected in (False, True, IntegerSubclass(valid_value)):
                with self.subTest(factory=factory.__name__, field=field):
                    self.assert_contract_error(
                        lambda factory=factory, field=field, rejected=rejected: factory(
                            **{field: rejected}
                        )
                    )

        for factory, field, valid_value in (
            (_operation, "operation_digest", OPERATION_DIGEST),
            (_evidence, "trust_domain", "production.eu"),
            (_evidence, "verification_coordinator_id", "coordinator:v1"),
            (_evidence, "assertion_id", ASSERTION_ID),
            (_evidence, "issuer", "issuer-A"),
            (_evidence, "subject", "subject-A"),
            (_evidence, "canonical_verified_email", "owner@example.test"),
            (_security_event, "event_id", SECURITY_EVENT_ID),
        ):
            with self.subTest(factory=factory.__name__, field=field):
                self.assert_contract_error(
                    lambda factory=factory, field=field, valid_value=valid_value: factory(
                        **{field: StringSubclass(valid_value)}
                    )
                )

        self.assert_contract_error(
            lambda: _evidence(authentication_method="oidc")
        )
        self.assert_contract_error(
            lambda: _security_event(event_type="initial_account_created")
        )
        self.assert_contract_error(
            lambda: _result("created", receipt=_receipt())  # type: ignore[arg-type]
        )
        self.assert_contract_error(
            lambda: _result(
                contract.InitialAccountCreationOutcome.CONFLICT,
                conflict_reason="record_id_collision",  # type: ignore[arg-type]
            )
        )
        self.assert_contract_error(
            lambda: _result(
                contract.InitialAccountCreationOutcome.CREATED,
                receipt=types.SimpleNamespace(),  # type: ignore[arg-type]
            )
        )

        for field, valid_value in (
            ("user_id", USER_ID),
            ("verified_email_id", EMAIL_ID),
            ("authentication_identity_id", IDENTITY_ID),
            ("workspace_id", WORKSPACE_ID),
            ("security_event_id", SECURITY_EVENT_ID),
        ):
            with self.subTest(receipt_field=field):
                self.assert_contract_error(
                    lambda field=field, valid_value=valid_value: _receipt(
                        **{field: StringSubclass(valid_value)}
                    )
                )

        for field in (
            "operation_reference",
            "user",
            "verified_email",
            "authentication_identity",
            "workspace",
            "workspace_membership",
            "authentication_evidence",
            "security_event",
        ):
            valid = _request_values()[field]
            field_names = PublicSurfaceTests.RECORD_FIELDS.get(type(valid))
            if field_names is None:
                field_names = tuple(
                    dataclass_field.name
                    for dataclass_field in dataclasses.fields(type(valid))
                )
            duck = types.SimpleNamespace(
                **{
                    field_name: getattr(valid, field_name)
                    for field_name in field_names
                }
            )
            with self.subTest(nested_field=field):
                self.assert_contract_error(
                    lambda field=field, duck=duck: _request(**{field: duck})
                )

        for invalid_request in ({}, types.SimpleNamespace()):
            with self.subTest(invalid_request=type(invalid_request).__name__):
                self.assert_contract_error(
                    lambda invalid_request=invalid_request: contract.validate_initial_account_creation_request(
                        invalid_request  # type: ignore[arg-type]
                    )
                )

    def test_every_contract_schema_and_request_version_is_exactly_one(self):
        factories = (
            (_operation, "schema_version"),
            (_evidence, "schema_version"),
            (_security_event, "schema_version"),
            (_receipt, "schema_version"),
            (_request, "request_version"),
        )
        for factory, field in factories:
            for rejected in (0, 2, -1, True):
                with self.subTest(
                    factory=factory.__name__, field=field, rejected=rejected
                ):
                    self.assert_contract_error(
                        lambda factory=factory, field=field, rejected=rejected: factory(
                            **{field: rejected}
                        )
                    )


class AggregateValidationTests(ContractTestCase):
    def test_valid_initial_aggregate_and_canonical_validators_are_used(self):
        request = _request()
        self.assertIsNone(
            contract.validate_initial_account_creation_request(request)
        )
        with mock.patch.object(
            auth_models,
            "validate_user_primary_email",
            wraps=auth_models.validate_user_primary_email,
        ) as user_email, mock.patch.object(
            auth_models,
            "validate_identity_for_user",
            wraps=auth_models.validate_identity_for_user,
        ) as identity, mock.patch.object(
            auth_models,
            "validate_membership_for_user",
            wraps=auth_models.validate_membership_for_user,
        ) as membership:
            self.assertIsNone(
                contract.validate_initial_account_creation_request(request)
            )
        user_email.assert_called_once_with(request.user, request.verified_email)
        identity.assert_called_once_with(
            request.authentication_identity,
            request.user,
            request.verified_email,
        )
        membership.assert_called_once_with(
            request.workspace_membership,
            request.workspace,
            request.user,
        )

    def test_all_initial_row_versions_security_epoch_and_statuses_are_enforced(self):
        row_version_cases = (
            ("user", _user(row_version=2)),
            ("verified_email", _email(row_version=2)),
            ("authentication_identity", _identity(row_version=2)),
            ("workspace", _workspace(row_version=2)),
            ("workspace_membership", _membership(row_version=2)),
        )
        for field, value in row_version_cases:
            with self.subTest(field=field):
                self.assert_contract_error(
                    lambda field=field, value=value: _request(**{field: value})
                )

        invalid_initial_states = (
            (
                "security_epoch",
                {"user": _user(security_epoch=2)},
            ),
            (
                "user status",
                {
                    "user": _user(
                        status=auth_models.UserStatus.SUSPENDED,
                        primary_verified_email_id=None,
                    )
                },
            ),
            (
                "email pending",
                {
                    "verified_email": _email(
                        status=auth_models.VerifiedEmailStatus.PENDING,
                        verified_at=None,
                    )
                },
            ),
            (
                "email retired",
                {
                    "verified_email": _email(
                        status=auth_models.VerifiedEmailStatus.RETIRED,
                        retired_at=2,
                    )
                },
            ),
            (
                "identity disabled",
                {
                    "authentication_identity": _identity(
                        status=auth_models.AuthenticationIdentityStatus.DISABLED
                    )
                },
            ),
            (
                "workspace suspended",
                {
                    "workspace": _workspace(
                        status=auth_models.WorkspaceStatus.SUSPENDED
                    )
                },
            ),
            (
                "membership admin",
                {
                    "workspace_membership": _membership(
                        role=auth_models.WorkspaceRole.ADMIN
                    )
                },
            ),
            (
                "membership suspended",
                {
                    "workspace_membership": _membership(
                        status=auth_models.WorkspaceMembershipStatus.SUSPENDED
                    )
                },
            ),
        )
        for label, overrides in invalid_initial_states:
            with self.subTest(label=label):
                self.assert_contract_error(lambda overrides=overrides: _request(**overrides))

    def test_every_aggregate_link_confusion_is_rejected(self):
        attempts = (
            {
                "user": _user(primary_verified_email_id=OTHER_EMAIL_ID),
            },
            {
                "verified_email": _email(user_id=OTHER_USER_ID),
            },
            {
                "authentication_identity": _identity(user_id=OTHER_USER_ID),
            },
            {
                "authentication_identity": _identity(verified_email_id=None),
            },
            {
                "authentication_identity": _identity(
                    verified_email_id=OTHER_EMAIL_ID
                ),
            },
            {
                "workspace": _workspace(created_by_user_id=OTHER_USER_ID),
            },
            {
                "workspace_membership": _membership(
                    workspace_id=OTHER_WORKSPACE_ID
                ),
            },
            {
                "workspace_membership": _membership(user_id=OTHER_USER_ID),
            },
        )
        for overrides in attempts:
            with self.subTest(overrides=tuple(overrides)):
                self.assert_contract_error(lambda overrides=overrides: _request(**overrides))

    def test_every_evidence_binding_is_exact_but_coordinator_source_is_independent(self):
        attempts = (
            _evidence(issuer="https://other-issuer.example.test"),
            _evidence(subject="opaque-subject-B"),
            _evidence(subject="OPAQUE-SUBJECT-A"),
            _evidence(
                authentication_method=auth_models.AuthenticationMethod.EMAIL_OTP
            ),
            _evidence(canonical_verified_email="different@example.test"),
            _evidence(verified_at=2, issued_at=2, expires_at=3),
        )
        for evidence in attempts:
            with self.subTest(evidence_field_values=repr(evidence)):
                self.assert_contract_error(
                    lambda evidence=evidence: _request(
                        authentication_evidence=evidence
                    )
                )

        independent_coordinator = _request(
            authentication_evidence=_evidence(
                verification_coordinator_id="different-coordinator:v2"
            )
        )
        self.assertIsNone(
            contract.validate_initial_account_creation_request(
                independent_coordinator
            )
        )

    def test_timestamps_are_structural_only_and_never_use_current_time(self):
        for values in (
            {"verified_at": 0, "issued_at": 0, "expires_at": 1},
            {"verified_at": 1, "issued_at": 1, "expires_at": 2},
            {"verified_at": 1, "issued_at": 2, "expires_at": 3},
            {
                "verified_at": auth_models.MAX_UNIX_UTC_SECONDS - 2,
                "issued_at": auth_models.MAX_UNIX_UTC_SECONDS - 1,
                "expires_at": auth_models.MAX_UNIX_UTC_SECONDS,
            },
        ):
            with self.subTest(values=values):
                self.assertIs(
                    type(_evidence(**values)),
                    contract.VerifiedAuthenticationEvidence,
                )

        for values in (
            {"verified_at": 2, "issued_at": 1, "expires_at": 3},
            {"verified_at": 1, "issued_at": 2, "expires_at": 2},
            {"verified_at": -1, "issued_at": 2, "expires_at": 3},
            {"verified_at": 1, "issued_at": -1, "expires_at": 3},
            {"verified_at": 1, "issued_at": 2, "expires_at": -1},
        ):
            with self.subTest(values=values):
                self.assert_contract_error(lambda values=values: _evidence(**values))

        request_with_historic_numeric_expiry = _request()
        self.assertEqual(request_with_historic_numeric_expiry.authentication_evidence.expires_at, 3)
        self.assertIsNone(
            contract.validate_initial_account_creation_request(
                request_with_historic_numeric_expiry
            )
        )

    def test_evidence_timestamp_fields_dispatch_their_exact_values(self):
        canonical_helper = auth_models._is_timestamp
        with mock.patch.object(
            auth_models, "_is_timestamp", wraps=canonical_helper
        ) as helper_spy:
            evidence = _evidence(
                verified_at=101,
                issued_at=102,
                expires_at=103,
            )
        self.assertIs(type(evidence), contract.VerifiedAuthenticationEvidence)
        self.assertEqual(
            helper_spy.call_args_list,
            [
                mock.call(101),
                mock.call(102),
                mock.call(103),
                mock.call(101),
                mock.call(102),
                mock.call(103),
            ],
        )

    def test_each_evidence_timestamp_routes_maximum_plus_one_to_helper(self):
        maximum_plus_one = auth_models.MAX_UNIX_UTC_SECONDS + 1
        for field in ("verified_at", "issued_at", "expires_at"):
            with self.subTest(field=field):
                canonical_helper = auth_models._is_timestamp
                with mock.patch.object(
                    auth_models, "_is_timestamp", wraps=canonical_helper
                ) as helper_spy:
                    self.assert_contract_error(
                        lambda field=field: _evidence(
                            **{field: maximum_plus_one}
                        )
                    )
                self.assertIn(
                    mock.call(maximum_plus_one),
                    helper_spy.call_args_list,
                )

    def test_contract_record_direct_timestamp_field_inventory_is_explicit(self):
        expected = {
            contract.InitialAccountOperationReference: (),
            contract.VerifiedAuthenticationEvidence: (
                "verified_at",
                "issued_at",
                "expires_at",
            ),
            contract.InitialSecurityEventRequest: (),
            contract.InitialAccountCreationRequest: (),
            contract.InitialAccountCreationReceipt: (),
            contract.InitialAccountCreationResult: (),
        }
        for record_type, timestamp_fields in expected.items():
            with self.subTest(record_type=record_type.__name__):
                self.assertEqual(
                    tuple(
                        field_name
                        for field_name in record_type.__slots__
                        if field_name.endswith("_at")
                    ),
                    timestamp_fields,
                )

    def test_complete_request_revalidation_dispatches_all_nested_timestamps(self):
        request = _request(
            user=_user(created_at=10, updated_at=11),
            verified_email=_email(created_at=12, verified_at=13),
            authentication_identity=_identity(
                created_at=14,
                last_used_at=15,
            ),
            workspace=_workspace(created_at=16, updated_at=17),
            workspace_membership=_membership(created_at=18, updated_at=19),
            authentication_evidence=_evidence(
                verified_at=13,
                issued_at=20,
                expires_at=21,
            ),
        )
        canonical_helper = auth_models._is_timestamp
        with mock.patch.object(
            auth_models, "_is_timestamp", wraps=canonical_helper
        ) as helper_spy:
            self.assertIsNone(
                contract.validate_initial_account_creation_request(request)
            )
        self.assertEqual(
            helper_spy.call_args_list,
            [
                mock.call(13),
                mock.call(20),
                mock.call(21),
                mock.call(10),
                mock.call(11),
                mock.call(12),
                mock.call(13),
                mock.call(14),
                mock.call(15),
                mock.call(10),
                mock.call(11),
                mock.call(12),
                mock.call(13),
                mock.call(18),
                mock.call(19),
                mock.call(16),
                mock.call(17),
                mock.call(10),
                mock.call(11),
                mock.call(13),
                mock.call(20),
                mock.call(21),
            ],
        )

    def test_public_validation_rechecks_corrupted_exact_records(self):
        corruptions = (
            ("request", "request_version", 2),
            ("operation_reference", "operation_digest", "invalid"),
            ("user", "row_version", 2),
            ("verified_email", "retired_at", 2),
            ("authentication_identity", "row_version", 2),
            ("workspace", "row_version", 2),
            ("workspace_membership", "row_version", 2),
            ("authentication_evidence", "expires_at", 2),
            ("security_event", "event_id", "invalid"),
        )
        for component_name, field, corrupted_value in corruptions:
            request = _request()
            component = request if component_name == "request" else getattr(
                request, component_name
            )
            object.__setattr__(component, field, corrupted_value)
            with self.subTest(component=component_name, field=field):
                self.assert_contract_error(
                    lambda request=request: contract.validate_initial_account_creation_request(
                        request
                    )
                )

class ErrorAndTracebackTests(ContractTestCase):
    def test_fixed_error_accepts_no_constructor_arguments_and_is_value_free(self):
        error = contract.AccountRepositoryContractValidationError()
        self.assertIs(type(error), contract.AccountRepositoryContractValidationError)
        self.assertEqual(error.args, ())
        self.assertEqual(
            str(error), "invalid initial account repository contract value"
        )
        self.assertEqual(repr(error), "AccountRepositoryContractValidationError()")
        for arguments in (("private",), (object(),)):
            with self.subTest(arguments=arguments):
                with self.assertRaises(TypeError):
                    contract.AccountRepositoryContractValidationError(*arguments)
        with self.assertRaises(TypeError):
            contract.AccountRepositoryContractValidationError(
                private="rejected"  # type: ignore[call-arg]
            )

    def test_rejected_values_and_ordinary_exceptions_leave_safe_module_frames(self):
        private_marker = "private-rejected-operation-digest-marker"
        private_object = object()
        self.assert_module_traceback_is_safe(
            lambda: _operation(operation_digest=private_marker),
            private_objects=(),
            private_markers=(private_marker,),
        )
        self.assert_module_traceback_is_safe(
            lambda: _operation(operation_digest=private_object),
            private_objects=(private_object,),
            private_markers=(),
        )

        request = _request()
        exception_marker = "private-underlying-validator-exception-marker"
        private_exception = RuntimeError(exception_marker)
        with mock.patch.object(
            auth_models,
            "validate_user_primary_email",
            side_effect=private_exception,
        ):
            self.assert_module_traceback_is_safe(
                lambda: contract.validate_initial_account_creation_request(
                    request
                ),
                private_objects=(request, private_exception),
                private_markers=(
                    exception_marker,
                    request.verified_email.canonical_email,
                    request.authentication_identity.subject,
                    request.operation_reference.operation_digest,
                ),
            )

    def test_subclass_attempts_fail_before_creation_with_safe_tracebacks(self):
        for record_type in PublicSurfaceTests.RECORD_FIELDS:
            keyword_marker = (
                f"private-keyword-{record_type.__name__}-marker"
            )
            keyword_object = object()
            keyword_body_calls: list[bool] = []

            def keyword_body(namespace: dict[str, object]) -> None:
                keyword_body_calls.append(True)
                namespace["__slots__"] = ()

            self.assert_module_traceback_is_safe(
                lambda record_type=record_type: types.new_class(
                    f"PrivateKeyword{record_type.__name__}",
                    (record_type,),
                    {
                        "private_marker": keyword_marker,
                        "private_object": keyword_object,
                    },
                    keyword_body,
                ),
                private_objects=(keyword_object,),
                private_markers=(
                    keyword_marker,
                    f"PrivateKeyword{record_type.__name__}",
                ),
            )
            self.assertEqual(keyword_body_calls, [True])

            body_marker = f"private-body-{record_type.__name__}-marker"
            body_object = object()
            body_calls: list[bool] = []

            def private_body(namespace: dict[str, object]) -> None:
                body_calls.append(True)
                namespace["__slots__"] = ()
                namespace["private_marker"] = body_marker
                namespace["private_object"] = body_object

            self.assert_module_traceback_is_safe(
                lambda record_type=record_type: types.new_class(
                    f"PrivateBody{record_type.__name__}",
                    (record_type,),
                    {},
                    private_body,
                ),
                private_objects=(body_object,),
                private_markers=(
                    body_marker,
                    f"PrivateBody{record_type.__name__}",
                ),
            )
            self.assertEqual(body_calls, [True])

            record_metaclass = type(record_type)
            metaclass_marker = (
                f"private-metaclass-{record_type.__name__}-marker"
            )
            metaclass_object = object()
            metaclass_body_calls: list[bool] = []

            def metaclass_body(namespace: dict[str, object]) -> None:
                metaclass_body_calls.append(True)
                namespace["private_marker"] = metaclass_marker
                namespace["private_object"] = metaclass_object

            self.assert_module_traceback_is_safe(
                lambda record_type=record_type: types.new_class(
                    f"PrivateDerivedMeta{record_type.__name__}",
                    (record_metaclass,),
                    {
                        "private_keyword": metaclass_object,
                    },
                    metaclass_body,
                ),
                private_objects=(metaclass_object,),
                private_markers=(
                    metaclass_marker,
                    f"PrivateDerivedMeta{record_type.__name__}",
                ),
            )
            self.assertEqual(metaclass_body_calls, [True])

    def test_serialization_state_and_mutation_failures_are_traceback_safe(self):
        for record in _all_contract_records():
            record_type = type(record)
            marker = f"private-state-{record_type.__name__}-marker"
            state = {"private_marker": marker, "private_record": record}
            sensitive_markers = (marker, *SENSITIVE_RECORD_MARKERS)
            with self.subTest(record=record_type.__name__, operation="asdict"):
                with self.assertRaises(TypeError):
                    dataclasses.asdict(record)
            with self.subTest(record=record_type.__name__, operation="astuple"):
                with self.assertRaises(TypeError):
                    dataclasses.astuple(record)
            with self.subTest(record=record_type.__name__, operation="json"):
                with self.assertRaises(TypeError):
                    json.dumps(record)

            for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
                with self.subTest(
                    record=record_type.__name__,
                    operation="pickle",
                    protocol=protocol,
                ):
                    self.assert_module_traceback_is_safe(
                        lambda protocol=protocol: pickle.dumps(
                            record, protocol=protocol
                        ),
                        private_objects=(record,),
                        private_markers=sensitive_markers,
                    )

            blocked_operations = (
                ("reduce", lambda: record.__reduce__()),
                ("reduce_ex", lambda: record.__reduce_ex__(state)),
                ("getstate", lambda: record.__getstate__()),
                ("setstate", lambda: record.__setstate__(state)),
                (
                    "new",
                    lambda: record_type.__new__(record_type, state),
                ),
                (
                    "setattr",
                    lambda: setattr(
                        record,
                        PublicSurfaceTests.RECORD_FIELDS[record_type][0],
                        state,
                    ),
                ),
                (
                    "delattr",
                    lambda: delattr(
                        record,
                        PublicSurfaceTests.RECORD_FIELDS[record_type][0],
                    ),
                ),
            )
            for operation, attempt in blocked_operations:
                with self.subTest(
                    record=record_type.__name__, operation=operation
                ):
                    self.assert_module_traceback_is_safe(
                        attempt,
                        private_objects=(record, state),
                        private_markers=sensitive_markers,
                    )
            self.assertFalse(hasattr(record, "__getnewargs__"))
            self.assertFalse(hasattr(record, "__getnewargs_ex__"))

    def test_constructor_call_failures_and_reinitialization_are_traceback_safe(self):
        for record in _all_contract_records():
            record_type = type(record)
            field_names = PublicSurfaceTests.RECORD_FIELDS[record_type]
            field_values = tuple(
                object.__getattribute__(record, field_name)
                for field_name in field_names
            )
            marker = f"private-constructor-{record_type.__name__}-marker"
            private_object = object()
            attempts = (
                (
                    "unknown keyword",
                    lambda: record_type(
                        **{marker: private_object}
                    ),
                ),
                ("wrong arity", lambda: record_type(private_object)),
                (
                    "duplicate field",
                    lambda: record_type(
                        *field_values,
                        **{field_names[0]: private_object},
                    ),
                ),
                (
                    "direct reinitialization",
                    lambda: record_type.__init__(record, *field_values),
                ),
            )
            for operation, attempt in attempts:
                with self.subTest(
                    record=record_type.__name__, operation=operation
                ):
                    self.assert_module_traceback_is_safe(
                        attempt,
                        private_objects=(
                            record,
                            field_values,
                            private_object,
                            *field_values,
                        ),
                        private_markers=(
                            marker,
                            *SENSITIVE_RECORD_MARKERS,
                        ),
                    )
            for field_name, field_value in zip(field_names, field_values):
                self.assertIs(
                    object.__getattribute__(record, field_name),
                    field_value,
                )

    def test_constructor_dependency_ordinary_exception_is_fixed_and_traceback_safe(self):
        values = _request_values()
        marker = "private-constructor-validator-exception-marker"
        failure = RuntimeError(marker)

        with mock.patch.object(
            auth_models,
            "validate_user_primary_email",
            side_effect=failure,
        ) as validator:
            self.assert_module_traceback_is_safe(
                lambda: contract.InitialAccountCreationRequest(**values),
                private_objects=(failure,),
                private_markers=(marker, *SENSITIVE_RECORD_MARKERS),
            )

        validator.assert_called_once_with(
            values["user"], values["verified_email"]
        )

    def test_constructor_dependency_baseexception_propagates_unchanged(self):
        class PrivateStop(BaseException):
            pass

        values = _request_values()
        stop = PrivateStop("private-constructor-validator-stop")
        with mock.patch.object(
            auth_models,
            "validate_user_primary_email",
            side_effect=stop,
        ) as validator:
            with self.assertRaises(PrivateStop) as captured:
                contract.InitialAccountCreationRequest(**values)

        self.assertIs(captured.exception, stop)
        validator.assert_called_once_with(
            values["user"], values["verified_email"]
        )

    def test_baseexception_propagates_unchanged(self):
        class PrivateStop(BaseException):
            pass

        stop = PrivateStop("private-stop-marker")
        request = _request()
        with mock.patch.object(
            auth_models,
            "validate_user_primary_email",
            side_effect=stop,
        ):
            with self.assertRaises(PrivateStop) as captured:
                contract.validate_initial_account_creation_request(request)
        self.assertIs(captured.exception, stop)


class ReplayEquivalenceTests(ContractTestCase):
    def test_separately_constructed_exact_requests_are_equivalent_without_wholesale_equality(self):
        first = _request()
        second = _request()
        record_types = (
            contract.InitialAccountOperationReference,
            contract.VerifiedAuthenticationEvidence,
            contract.InitialSecurityEventRequest,
            contract.InitialAccountCreationRequest,
            auth_models.CuevionUser,
            auth_models.VerifiedEmail,
            auth_models.AuthenticationIdentity,
            auth_models.Workspace,
            auth_models.WorkspaceMembership,
        )
        with contextlib.ExitStack() as stack:
            for record_type in record_types:
                stack.enter_context(
                    mock.patch.object(
                        record_type,
                        "__eq__",
                        side_effect=AssertionError("record equality was invoked"),
                    )
                )
            self.assertTrue(
                contract.initial_account_creation_requests_are_replay_equivalent(
                    first, second
                )
            )

    def test_every_valid_caller_controlled_field_change_is_not_equivalent(self):
        alternate_user = _request(
            user=_user(user_id=OTHER_USER_ID),
            verified_email=_email(user_id=OTHER_USER_ID),
            authentication_identity=_identity(user_id=OTHER_USER_ID),
            workspace=_workspace(created_by_user_id=OTHER_USER_ID),
            workspace_membership=_membership(user_id=OTHER_USER_ID),
        )
        alternate_email_id = _request(
            user=_user(primary_verified_email_id=OTHER_EMAIL_ID),
            verified_email=_email(email_id=OTHER_EMAIL_ID),
            authentication_identity=_identity(
                verified_email_id=OTHER_EMAIL_ID
            ),
        )
        alternate_verified_at = _request(
            verified_email=_email(verified_at=2),
            authentication_evidence=_evidence(
                verified_at=2, issued_at=2, expires_at=3
            ),
        )
        alternate_issuer = "https://identity.example.test/other-tenant"
        alternate_subject = "opaque-subject-B"
        changes = (
            ("operation epoch", _request(operation_reference=_operation(derivation_key_epoch=2))),
            ("operation digest", _request(operation_reference=_operation(operation_digest=OTHER_OPERATION_DIGEST))),
            ("user id and required links", alternate_user),
            ("primary email id and required links", alternate_email_id),
            ("user display name", _request(user=_user(display_name="Different Owner"))),
            ("user created_at", _request(user=_user(created_at=1))),
            ("user updated_at", _request(user=_user(updated_at=2))),
            ("email canonical and evidence", _request(
                verified_email=_email(canonical_email="other@example.test"),
                authentication_evidence=_evidence(canonical_verified_email="other@example.test"),
            )),
            ("email verification source", _request(verified_email=_email(verification_source="other_source"))),
            ("email created_at", _request(verified_email=_email(created_at=1))),
            ("email verified_at and evidence", alternate_verified_at),
            ("identity id", _request(authentication_identity=_identity(identity_id=OTHER_IDENTITY_ID))),
            ("identity issuer and evidence", _request(
                authentication_identity=_identity(issuer=alternate_issuer),
                authentication_evidence=_evidence(issuer=alternate_issuer),
            )),
            ("identity subject and evidence", _request(
                authentication_identity=_identity(subject=alternate_subject),
                authentication_evidence=_evidence(subject=alternate_subject),
            )),
            ("identity method and evidence", _request(
                authentication_identity=_identity(method=auth_models.AuthenticationMethod.EMAIL_OTP),
                authentication_evidence=_evidence(authentication_method=auth_models.AuthenticationMethod.EMAIL_OTP),
            )),
            ("identity created_at", _request(authentication_identity=_identity(created_at=0))),
            ("identity last_used_at", _request(authentication_identity=_identity(last_used_at=2))),
            ("workspace id and membership", _request(
                workspace=_workspace(workspace_id=OTHER_WORKSPACE_ID),
                workspace_membership=_membership(workspace_id=OTHER_WORKSPACE_ID),
            )),
            ("workspace created_at", _request(workspace=_workspace(created_at=0))),
            ("workspace updated_at", _request(workspace=_workspace(updated_at=2))),
            ("membership created_at", _request(workspace_membership=_membership(created_at=0))),
            ("membership updated_at", _request(workspace_membership=_membership(updated_at=2))),
            ("evidence trust domain", _request(authentication_evidence=_evidence(trust_domain="preview.eu"))),
            ("evidence coordinator", _request(authentication_evidence=_evidence(verification_coordinator_id="other-coordinator:v1"))),
            ("evidence assertion", _request(authentication_evidence=_evidence(assertion_id=OTHER_ASSERTION_ID))),
            ("evidence issued_at", _request(authentication_evidence=_evidence(issued_at=1))),
            ("evidence expires_at", _request(authentication_evidence=_evidence(expires_at=4))),
            ("security event id", _request(security_event=_security_event(event_id=OTHER_SECURITY_EVENT_ID))),
        )
        baseline = _request()
        for label, changed in changes:
            with self.subTest(field=label):
                self.assertFalse(
                    contract.initial_account_creation_requests_are_replay_equivalent(
                        baseline, changed
                    )
                )

    def test_fixed_fields_are_revalidated_and_corruption_never_returns_false(self):
        corruptions = (
            ("request_version", lambda request: request, 2),
            ("operation schema", lambda request: request.operation_reference, 2),
            ("user status", lambda request: request.user, auth_models.UserStatus.SUSPENDED),
            ("email row", lambda request: request.verified_email, 2),
            ("identity status", lambda request: request.authentication_identity, auth_models.AuthenticationIdentityStatus.DISABLED),
            ("workspace status", lambda request: request.workspace, auth_models.WorkspaceStatus.SUSPENDED),
            ("membership role", lambda request: request.workspace_membership, auth_models.WorkspaceRole.ADMIN),
            ("evidence schema", lambda request: request.authentication_evidence, 2),
            ("event type", lambda request: request.security_event, "bad"),
        )
        field_names = (
            "request_version",
            "schema_version",
            "status",
            "row_version",
            "status",
            "status",
            "role",
            "schema_version",
            "event_type",
        )
        for (label, selector, value), field_name in zip(corruptions, field_names):
            corrupted = _request()
            object.__setattr__(selector(corrupted), field_name, value)
            for position in ("first", "second"):
                with self.subTest(field=label, position=position):
                    self.assert_contract_error(
                        lambda corrupted=corrupted, position=position: contract.initial_account_creation_requests_are_replay_equivalent(
                            corrupted if position == "first" else _request(),
                            corrupted if position == "second" else _request(),
                        )
                    )

        for position in ("first", "second"):
            invalid = types.SimpleNamespace()
            with self.subTest(invalid="namespace", position=position):
                self.assert_contract_error(
                    lambda position=position, invalid=invalid: contract.initial_account_creation_requests_are_replay_equivalent(
                        invalid if position == "first" else _request(),
                        invalid if position == "second" else _request(),
                    )
                )

    def test_replay_dependency_failures_are_symmetric_and_value_free(self):
        for position in ("first", "second"):
            first = _request()
            second = _request()
            marker = f"private-replay-{position}-exception-marker"
            failure = RuntimeError(marker)
            side_effect = (
                (failure, None) if position == "first" else (None, failure)
            )
            with mock.patch.object(
                auth_models,
                "validate_user_primary_email",
                side_effect=side_effect,
            ) as validator:
                self.assert_module_traceback_is_safe(
                    lambda: contract.initial_account_creation_requests_are_replay_equivalent(
                        first, second
                    ),
                    private_objects=(first, second, failure),
                    private_markers=(
                        marker,
                        first.operation_reference.operation_digest,
                        first.authentication_identity.subject,
                        first.verified_email.canonical_email,
                    ),
                )
            self.assertEqual(validator.call_count, 2)

        class PrivateStop(BaseException):
            pass

        for position in ("first", "second"):
            first = _request()
            second = _request()
            stop = PrivateStop(f"private-replay-{position}-stop")
            side_effect = (
                (stop,) if position == "first" else (None, stop)
            )
            with mock.patch.object(
                auth_models,
                "validate_user_primary_email",
                side_effect=side_effect,
            ) as validator:
                with self.assertRaises(PrivateStop) as captured:
                    contract.initial_account_creation_requests_are_replay_equivalent(
                        first, second
                    )
            self.assertIs(captured.exception, stop)
            self.assertEqual(
                validator.call_count, 1 if position == "first" else 2
            )


class ResultAndReceiptTests(ContractTestCase):
    def test_exact_outcome_receipt_and_conflict_reason_matrix(self):
        receipt = _receipt()
        reasons = (None, *tuple(contract.InitialAccountConflictReason))
        receipts = (None, receipt)
        valid_count = 0
        invalid_count = 0

        for outcome in contract.InitialAccountCreationOutcome:
            for reason in reasons:
                for result_receipt in receipts:
                    valid = (
                        outcome
                        in (
                            contract.InitialAccountCreationOutcome.CREATED,
                            contract.InitialAccountCreationOutcome.EXACT_REPLAY,
                        )
                        and reason is None
                        and result_receipt is receipt
                    ) or (
                        outcome
                        is contract.InitialAccountCreationOutcome.CONFLICT
                        and type(reason)
                        is contract.InitialAccountConflictReason
                        and result_receipt is None
                    ) or (
                        outcome
                        in (
                            contract.InitialAccountCreationOutcome.AMBIGUOUS,
                            contract.InitialAccountCreationOutcome.UNAVAILABLE,
                            contract.InitialAccountCreationOutcome.INTERNAL_ERROR,
                        )
                        and reason is None
                        and result_receipt is None
                    )
                    with self.subTest(
                        outcome=outcome,
                        reason=reason,
                        receipt=result_receipt is not None,
                    ):
                        if valid:
                            result = _result(
                                outcome,
                                conflict_reason=reason,
                                receipt=result_receipt,
                            )
                            self.assertIs(result.outcome, outcome)
                            self.assertIs(result.conflict_reason, reason)
                            self.assertIs(result.receipt, result_receipt)
                            valid_count += 1
                        else:
                            self.assert_contract_error(
                                lambda outcome=outcome, reason=reason, result_receipt=result_receipt: _result(
                                    outcome,
                                    conflict_reason=reason,
                                    receipt=result_receipt,
                                )
                            )
                            invalid_count += 1

        self.assertEqual(valid_count, 9)
        self.assertEqual(invalid_count, 51)

    def test_receipt_contains_only_immutable_creation_ids_and_is_revalidated(self):
        expected = (
            "schema_version",
            "user_id",
            "verified_email_id",
            "authentication_identity_id",
            "workspace_id",
            "security_event_id",
        )
        self.assertEqual(
            PublicSurfaceTests.RECORD_FIELDS[
                contract.InitialAccountCreationReceipt
            ],
            expected,
        )
        receipt = _receipt()
        object.__setattr__(receipt, "security_event_id", "invalid")
        self.assert_contract_error(
            lambda: _result(
                contract.InitialAccountCreationOutcome.CREATED,
                receipt=receipt,
            )
        )

        for field, invalid in (
            ("user_id", OTHER_EMAIL_ID),
            ("verified_email_id", USER_ID),
            ("authentication_identity_id", WORKSPACE_ID),
            ("workspace_id", IDENTITY_ID),
            ("security_event_id", SECURITY_EVENT_ID + "="),
        ):
            with self.subTest(field=field):
                self.assert_contract_error(
                    lambda field=field, invalid=invalid: _receipt(
                        **{field: invalid}
                    )
                )

    def test_public_records_have_no_secret_provider_mailbox_or_product_authority_fields(self):
        forbidden = (
            "token",
            "secret",
            "authorization_code",
            "provider_payload",
            "raw_provider",
            "pkce",
            "nonce",
            "challenge",
            "cookie",
            "header",
            "mailbox",
            "imap",
            "smtp",
            "email_client",
            "organizer",
            "bundle",
            "product",
            "plan",
            "package",
            "subscription",
            "billing",
            "entitlement",
            "seat",
            "commit_reference",
            "storage_reference",
        )
        for record_type, record_fields in PublicSurfaceTests.RECORD_FIELDS.items():
            names = tuple(field.casefold() for field in record_fields)
            for fragment in forbidden:
                with self.subTest(record=record_type.__name__, fragment=fragment):
                    self.assertTrue(all(fragment not in name for name in names))


class InactivityAndDocumentationTests(ContractTestCase):
    def test_cold_canonical_import_has_no_forbidden_side_effects(self):
        source_path = str(_SOURCE_PATH)
        program = f"""
import atexit
import builtins
import http.client
import importlib
import io
import logging
import os
import pathlib
import random
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

importlib.import_module('api.auth.models')
target = 'cuevion_auth.account_repository_contract'
source_path = {source_path!r}
assert target not in sys.modules

class BlockedEnvironment:
    def blocked(self, *_arguments, **_keywords):
        raise AssertionError('environment access')
    __getitem__ = blocked
    __setitem__ = blocked
    __delitem__ = blocked
    __iter__ = blocked
    __len__ = blocked
    __contains__ = blocked
    get = blocked
    keys = blocked
    items = blocked
    values = blocked
    copy = blocked

side_effects = []
def blocked(*_arguments, **_keywords):
    side_effects.append('forbidden operation')
    raise AssertionError('forbidden import side effect')

audited = []
audit_active = False
def audit(event, _arguments):
    global audit_active
    if audit_active:
        return
    audit_active = True
    try:
        caller = sys._getframe(1).f_globals.get('__name__')
        if caller == target and (
            event == 'open'
            or event.startswith('socket.')
            or event.startswith('subprocess.')
            or event.startswith('http.client.')
            or event.startswith('os.')
        ):
            audited.append(event)
    finally:
        audit_active = False
sys.addaudithook(audit)

os.environ = BlockedEnvironment()
if hasattr(os, 'environb'):
    os.environb = BlockedEnvironment()
os.getenv = blocked
if hasattr(os, 'getenvb'):
    os.getenvb = blocked
os.urandom = blocked
for name in ('time', 'time_ns', 'monotonic', 'monotonic_ns', 'perf_counter'):
    if hasattr(time, name):
        setattr(time, name, blocked)
for name in ('random', 'getrandbits', 'randbytes'):
    if hasattr(random, name):
        setattr(random, name, blocked)
for name in ('token_bytes', 'token_hex', 'token_urlsafe', 'randbelow'):
    setattr(secrets, name, blocked)
uuid.uuid1 = blocked
uuid.uuid4 = blocked
socket.socket = blocked
socket.create_connection = blocked
socket.getaddrinfo = blocked
urllib.request.urlopen = blocked
urllib.request.OpenerDirector.open = blocked
http.client.HTTPConnection.connect = blocked
http.client.HTTPSConnection.connect = blocked
subprocess.Popen = blocked
threading.Thread.start = blocked
atexit.register = blocked
for name in ('getLogger', 'debug', 'info', 'warning', 'error', 'exception', 'critical', 'log', 'basicConfig'):
    setattr(logging, name, blocked)
logging.Logger._log = blocked

allowed_production_imports = ('base64', 'enum', 'enum', 'typing', 'api.auth')
production_imports = []
original_import = builtins.__import__
def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    caller = globals.get('__name__') if type(globals) is dict else None
    if caller == target:
        production_imports.append(name)
        if level != 0 or name not in allowed_production_imports:
            raise AssertionError('forbidden production import')
    return original_import(name, globals, locals, fromlist, level)
builtins.__import__ = guarded_import

path_before = tuple(sys.path)
captured_stdout = io.StringIO()
captured_stderr = io.StringIO()
original_stdout = sys.stdout
original_stderr = sys.stderr
try:
    sys.stdout = captured_stdout
    sys.stderr = captured_stderr
    module = importlib.import_module(target)
finally:
    sys.stdout = original_stdout
    sys.stderr = original_stderr

assert tuple(sys.path) == path_before
assert module is sys.modules[target]
assert [name for name, value in sys.modules.items() if value is module] == [target]
assert tuple(production_imports) == allowed_production_imports
assert captured_stdout.getvalue() == ''
assert captured_stderr.getvalue() == ''
assert side_effects == []
assert audited == []
for forbidden_surface in ('handler', 'route', 'router', 'app'):
    assert forbidden_surface not in vars(module)
"""
        completed = _run_isolated(program)
        self.assertEqual(
            completed.returncode,
            0,
            msg=completed.stdout + completed.stderr,
        )
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")

    def test_source_import_boundary_and_no_active_surface(self):
        tree = ast.parse(_SOURCE_PATH.read_text(encoding="utf-8"))
        production_imports: list[tuple[str, int, str | None, str | None]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                production_imports.extend(
                    ("import", 0, alias.name, None)
                    for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                production_imports.extend(
                    ("from", node.level, node.module, alias.name)
                    for alias in node.names
                )
        self.assertCountEqual(
            production_imports,
            (
                ("import", 0, "base64", None),
                ("from", 0, "enum", "Enum"),
                ("from", 0, "enum", "EnumMeta"),
                ("from", 0, "typing", "Protocol"),
                ("from", 0, "api.auth", "models"),
            ),
        )
        for name in ("handler", "route", "router", "app", "server"):
            self.assertNotIn(name, vars(contract))
        for value in vars(contract).values():
            if (
                inspect.isclass(value)
                and value.__module__ == contract.__name__
                and value is not contract.InitialAccountRepository
            ):
                self.assertNotIn("create_initial_account", value.__dict__)
        module_protocols = {
            value
            for value in vars(contract).values()
            if inspect.isclass(value)
            and value.__module__ == contract.__name__
            and getattr(value, "_is_protocol", False)
        }
        self.assertEqual(
            module_protocols, {contract.InitialAccountRepository}
        )

    def test_contract_and_tests_are_outside_the_vercel_route_glob(self):
        configuration = json.loads(
            (_FRONTEND_DIRECTORY / "vercel.json").read_text(encoding="utf-8")
        )
        patterns = set(configuration["functions"])
        self.assertEqual(patterns, {"api/**/*.py"})
        for relative_path in (
            "cuevion_auth/account_repository_contract.py",
            "tests/cuevion_auth/test_account_repository_contract.py",
        ):
            with self.subTest(relative_path=relative_path):
                self.assertTrue(
                    all(
                        not PurePosixPath(relative_path).match(pattern)
                        for pattern in patterns
                    )
                )

    def test_no_active_route_imports_the_contract(self):
        protected = (
            _FRONTEND_DIRECTORY / "api" / "inboxes" / "oauth_google.py"
        ).resolve()
        import_markers = (
            "cuevion_auth.account_repository_contract",
            "from cuevion_auth import account_repository_contract",
        )
        for path in (_FRONTEND_DIRECTORY / "api").rglob("*.py"):
            if path.resolve() == protected:
                continue
            source = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(_FRONTEND_DIRECTORY)):
                for marker in import_markers:
                    self.assertNotIn(marker, source)

    def test_activation_document_covers_all_blockers_and_entitlement_boundary(self):
        documentation = _DOCUMENTATION_PATH.read_text(encoding="utf-8")
        normalized = " ".join(documentation.casefold().split())
        required = (
            "completely inactive",
            "durable operation result",
            "`created`",
            "`exact_replay`",
            "`conflict`",
            "`ambiguous`",
            "`unavailable`",
            "`internal_error`",
            "unknown commit status",
            "exact same complete request",
            "raw operation key never appears",
            "provider-evidence boundary",
            "access token",
            "refresh token",
            "id token",
            "trusted-now",
            "one explicit, exact trusted-now snapshot and the current key and policy context through a separately reviewed execution context",
            "on every call, including reconciliation after `ambiguous`",
            "resolve durable operation state before applying current evidence-expiry or key policy",
            "must not reject current evidence freshness or key policy before that resolution",
            "stored historical receipt as `exact_replay`, even when current policy would reject a new operation",
            "`conflict` with `operation_reference_mismatch`",
            "inconsistent durable operation state returns `internal_error`",
            "remains `ambiguous` and expiry cannot downgrade it",
            "only after authoritatively establishing that no prior operation state exists",
            "only after authoritatively establishing that no prior operation state exists may current evidence freshness, operation authorization, and key policy gate a new write",
            "historical `exact_replay` must never be blocked",
            "this pure contract reads no clock and decides no current freshness",
            "trusted now is neither a request field nor a replay field",
            "current expiry policy and its failure mapping remain activation blockers",
            "no permanent retired-email reuse policy",
            "repository-generated",
            "relational schemas",
            "atomic transaction",
            "migration",
            "resolver",
            "session",
            "one cuevion account can later use multiple products",
            "workspace-entitlement",
            "`email_client` entitlement",
            "`organizer` entitlement",
            "commercial combination",
            "browser input",
            "session cookie",
            "future entitlement layer is completely outside this slice",
            "`api/**/*.py`",
            "no active route imports it",
            "collaboration",
        )
        for statement in required:
            with self.subTest(statement=statement):
                self.assertIn(statement, normalized)
        self.assertNotIn(
            "establish evidence freshness before calling this boundary",
            normalized,
        )


if __name__ == "__main__":
    unittest.main()
