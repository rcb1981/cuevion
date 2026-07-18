"""Security tests for the inactive current-account repository contract."""

import base64
import dataclasses
import inspect
import json
import pickle
import types
import typing
import unittest

from api.auth import models as auth_models
from cuevion_auth import current_account_repository_contract as contract


def _b64(octet: int) -> str:
    return base64.urlsafe_b64encode(bytes((octet,)) * 16).rstrip(b"=").decode(
        "ascii"
    )


USER_ID = "usr_" + _b64(1)
OTHER_USER_ID = "usr_" + _b64(2)
EMAIL_ID = "vem_" + _b64(3)
OTHER_EMAIL_ID = "vem_" + _b64(4)
IDENTITY_ID = "aid_" + _b64(5)
WORKSPACE_ID = "wsp_" + _b64(6)
OTHER_WORKSPACE_ID = "wsp_" + _b64(7)
ISSUER = "https://Identity.Example.test/Tenant-A"
SUBJECT = "Opaque:Subject-A"
CANONICAL_EMAIL = "current.owner@example.test"
SENSITIVE_MARKERS = (
    USER_ID,
    EMAIL_ID,
    IDENTITY_ID,
    WORKSPACE_ID,
    ISSUER,
    SUBJECT,
    CANONICAL_EMAIL,
)


def _user_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": 1,
        "user_id": USER_ID,
        "status": auth_models.UserStatus.ACTIVE,
        "primary_verified_email_id": EMAIL_ID,
        "display_name": "Current Owner",
        "security_epoch": 4,
        "created_at": 10,
        "updated_at": 20,
        "row_version": 5,
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
        "canonical_email": CANONICAL_EMAIL,
        "status": auth_models.VerifiedEmailStatus.VERIFIED,
        "verification_source": "trusted_coordinator",
        "created_at": 10,
        "verified_at": 11,
        "retired_at": None,
        "row_version": 3,
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
        "issuer": ISSUER,
        "subject": SUBJECT,
        "method": auth_models.AuthenticationMethod.OIDC,
        "status": auth_models.AuthenticationIdentityStatus.ACTIVE,
        "verified_email_id": EMAIL_ID,
        "created_at": 10,
        "last_used_at": 20,
        "row_version": 6,
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
        "created_by_user_id": OTHER_USER_ID,
        "created_at": 10,
        "updated_at": 20,
        "row_version": 7,
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
        "role": auth_models.WorkspaceRole.MEMBER,
        "status": auth_models.WorkspaceMembershipStatus.ACTIVE,
        "created_at": 10,
        "updated_at": 20,
        "row_version": 8,
    }
    values.update(overrides)
    return values


def _membership(**overrides: object) -> auth_models.WorkspaceMembership:
    return auth_models.WorkspaceMembership(
        **_membership_values(**overrides)
    )


def _authority(
    *,
    user: auth_models.CuevionUser | None = None,
    email: auth_models.VerifiedEmail | None = None,
    identity: auth_models.AuthenticationIdentity | None = None,
    workspace: auth_models.Workspace | None = None,
    membership: auth_models.WorkspaceMembership | None = None,
) -> contract.CurrentAccountAuthority:
    return contract.CurrentAccountAuthority(
        user=user if user is not None else _user(),
        primary_verified_email=email if email is not None else _email(),
        authentication_identity=(
            identity if identity is not None else _identity()
        ),
        workspace=workspace if workspace is not None else _workspace(),
        workspace_membership=(
            membership if membership is not None else _membership()
        ),
    )


def _by_user_authority(
    *,
    user: auth_models.CuevionUser | None = None,
    email: auth_models.VerifiedEmail | None = None,
    workspace: auth_models.Workspace | None = None,
    membership: auth_models.WorkspaceMembership | None = None,
) -> contract.CurrentAccountByUserAuthority:
    return contract.CurrentAccountByUserAuthority(
        user=user if user is not None else _user(),
        primary_verified_email=email if email is not None else _email(),
        workspace=workspace if workspace is not None else _workspace(),
        workspace_membership=(
            membership if membership is not None else _membership()
        ),
    )


def _all_records() -> tuple[object, ...]:
    authority = _authority()
    by_user_authority = _by_user_authority()
    return (
        contract.AuthenticationIdentityLookupKey(ISSUER, SUBJECT),
        authority,
        by_user_authority,
        contract.CurrentAccountAuthorityResult(
            contract.CurrentAccountReadOutcome.FOUND, authority
        ),
        contract.CurrentAccountByUserAuthorityResult(
            contract.CurrentAccountReadOutcome.FOUND, by_user_authority
        ),
        contract.CurrentAccountAuthorityResult(
            contract.CurrentAccountReadOutcome.NOT_AUTHORIZED, None
        ),
        contract.CurrentAccountByUserAuthorityResult(
            contract.CurrentAccountReadOutcome.INTERNAL_ERROR, None
        ),
    )


class ContractTestCase(unittest.TestCase):
    def assert_contract_error(
        self,
        callable_object: typing.Callable[[], object],
        *,
        markers: tuple[str, ...] = (),
    ) -> contract.CurrentAccountRepositoryContractValidationError:
        try:
            callable_object()
        except contract.CurrentAccountRepositoryContractValidationError as error:
            self.assertIs(
                type(error),
                contract.CurrentAccountRepositoryContractValidationError,
            )
            self.assertEqual(error.args, ())
            self.assertEqual(
                str(error),
                "invalid current account repository contract value",
            )
            self.assertEqual(
                repr(error),
                "CurrentAccountRepositoryContractValidationError()",
            )
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)
            for marker in markers:
                self.assertNotIn(marker, str(error))
                self.assertNotIn(marker, repr(error))
                self.assertNotIn(marker, repr(error.args))
            return error
        self.fail("contract validation failure was not raised")


class PublicSurfaceTests(ContractTestCase):
    RECORD_FIELDS = {
        contract.AuthenticationIdentityLookupKey: ("issuer", "subject"),
        contract.CurrentAccountAuthority: (
            "user",
            "primary_verified_email",
            "authentication_identity",
            "workspace",
            "workspace_membership",
        ),
        contract.CurrentAccountByUserAuthority: (
            "user",
            "primary_verified_email",
            "workspace",
            "workspace_membership",
        ),
        contract.CurrentAccountAuthorityResult: ("outcome", "authority"),
        contract.CurrentAccountByUserAuthorityResult: (
            "outcome",
            "authority",
        ),
    }

    def test_exact_public_exports(self):
        self.assertEqual(
            contract.__all__,
            (
                "CurrentAccountRepositoryContractValidationError",
                "CurrentAccountReadOutcome",
                "AuthenticationIdentityLookupKey",
                "CurrentAccountAuthority",
                "CurrentAccountByUserAuthority",
                "CurrentAccountAuthorityResult",
                "CurrentAccountByUserAuthorityResult",
                "validate_authentication_identity_lookup_key",
                "validate_current_account_user_id",
                "validate_current_account_workspace_id",
                "CurrentAccountAuthorityRepository",
            ),
        )
        self.assertEqual(
            {name for name in vars(contract) if not name.startswith("_")},
            set(contract.__all__),
        )

    def test_closed_outcome_enum(self):
        self.assertEqual(
            tuple(
                (member.name, member.value)
                for member in contract.CurrentAccountReadOutcome
            ),
            (
                ("FOUND", "found"),
                ("NOT_AUTHORIZED", "not_authorized"),
                ("UNAVAILABLE", "unavailable"),
                ("INTERNAL_ERROR", "internal_error"),
            ),
        )
        self.assertNotIn("NOT_FOUND", contract.CurrentAccountReadOutcome.__members__)
        self.assertNotIn("INACTIVE", contract.CurrentAccountReadOutcome.__members__)
        self.assertNotIn("AMBIGUOUS", contract.CurrentAccountReadOutcome.__members__)
        for member in contract.CurrentAccountReadOutcome:
            self.assertIs(contract.CurrentAccountReadOutcome(member), member)
            self.assertIs(
                contract.CurrentAccountReadOutcome(member.value), member
            )

        class StringSubclass(str):
            pass

        self.assert_contract_error(
            lambda: contract.CurrentAccountReadOutcome("not_found")
        )
        self.assert_contract_error(
            lambda: contract.CurrentAccountReadOutcome(
                StringSubclass("found")
            )
        )

    def test_records_have_exact_fields_signatures_and_hints(self):
        for record_type, field_names in self.RECORD_FIELDS.items():
            with self.subTest(record=record_type.__name__):
                self.assertFalse(dataclasses.is_dataclass(record_type))
                self.assertEqual(tuple(record_type.__slots__), field_names)
                self.assertEqual(
                    tuple(typing.get_type_hints(record_type)), field_names
                )
                self.assertEqual(
                    tuple(inspect.signature(record_type).parameters),
                    field_names,
                )
                self.assertEqual(
                    tuple(inspect.signature(record_type.__init__).parameters),
                    ("self", *field_names),
                )

        identity_hints = typing.get_type_hints(
            contract.CurrentAccountAuthority
        )
        self.assertIs(identity_hints["user"], auth_models.CuevionUser)
        self.assertIs(
            identity_hints["primary_verified_email"],
            auth_models.VerifiedEmail,
        )
        self.assertIs(
            identity_hints["authentication_identity"],
            auth_models.AuthenticationIdentity,
        )
        self.assertIs(identity_hints["workspace"], auth_models.Workspace)
        self.assertIs(
            identity_hints["workspace_membership"],
            auth_models.WorkspaceMembership,
        )

    def test_input_validators_have_exact_surface(self):
        expected = {
            contract.validate_authentication_identity_lookup_key: (
                "identity_key",
                contract.AuthenticationIdentityLookupKey,
            ),
            contract.validate_current_account_user_id: ("user_id", str),
            contract.validate_current_account_workspace_id: (
                "workspace_id",
                str,
            ),
        }
        for validator, (parameter_name, parameter_type) in expected.items():
            with self.subTest(validator=validator.__name__):
                signature = inspect.signature(validator)
                self.assertEqual(tuple(signature.parameters), (parameter_name,))
                hints = typing.get_type_hints(validator)
                self.assertIs(hints[parameter_name], parameter_type)
                self.assertIs(hints["return"], type(None))

    def test_protocol_has_only_the_two_exact_methods(self):
        protocol = contract.CurrentAccountAuthorityRepository
        self.assertTrue(protocol._is_protocol)
        self.assertFalse(getattr(protocol, "_is_runtime_protocol", False))
        methods = {
            name
            for name, value in protocol.__dict__.items()
            if inspect.isfunction(value) and not name.startswith("_")
        }
        self.assertEqual(
            methods,
            {
                "resolve_current_account_by_identity",
                "read_current_account_by_user",
            },
        )
        identity_method = protocol.resolve_current_account_by_identity
        self.assertEqual(
            tuple(inspect.signature(identity_method).parameters),
            ("self", "identity_key", "workspace_id"),
        )
        identity_hints = typing.get_type_hints(identity_method)
        self.assertIs(
            identity_hints["identity_key"],
            contract.AuthenticationIdentityLookupKey,
        )
        self.assertIs(identity_hints["workspace_id"], str)
        self.assertIs(
            identity_hints["return"], contract.CurrentAccountAuthorityResult
        )

        user_method = protocol.read_current_account_by_user
        self.assertEqual(
            tuple(inspect.signature(user_method).parameters),
            ("self", "user_id", "workspace_id"),
        )
        user_hints = typing.get_type_hints(user_method)
        self.assertIs(user_hints["user_id"], str)
        self.assertIs(user_hints["workspace_id"], str)
        self.assertIs(
            user_hints["return"],
            contract.CurrentAccountByUserAuthorityResult,
        )
        with self.assertRaises(TypeError):
            isinstance(object(), protocol)
        with self.assertRaises(TypeError):
            protocol()


class LookupAndIdentifierValidationTests(ContractTestCase):
    def test_lookup_key_preserves_exact_issuer_and_case_sensitive_subject(self):
        key = contract.AuthenticationIdentityLookupKey(ISSUER, SUBJECT)
        self.assertIs(key.issuer, ISSUER)
        self.assertIs(key.subject, SUBJECT)
        self.assertEqual(key.issuer, "https://Identity.Example.test/Tenant-A")
        self.assertNotEqual(key.issuer, key.issuer.lower())
        self.assertNotEqual(key.subject, key.subject.lower())
        self.assertIsNone(
            contract.validate_authentication_identity_lookup_key(key)
        )

        distinct = contract.AuthenticationIdentityLookupKey(
            ISSUER.lower(), SUBJECT.lower()
        )
        self.assertEqual(distinct.issuer, ISSUER.lower())
        self.assertEqual(distinct.subject, SUBJECT.lower())
        self.assertNotEqual(distinct.issuer, key.issuer)
        self.assertNotEqual(distinct.subject, key.subject)

    def test_lookup_key_never_trims_or_normalizes(self):
        for field in ("issuer", "subject"):
            for invalid in (
                "",
                " leading",
                "trailing ",
                "embedded space",
                "tab\tvalue",
                "line\nvalue",
                "https://idéntity.example.test",
                "x" * 513,
                b"bytes",
                object(),
                None,
            ):
                values = {"issuer": ISSUER, "subject": SUBJECT}
                values[field] = invalid
                with self.subTest(field=field, invalid=type(invalid).__name__):
                    self.assert_contract_error(
                        lambda values=values: contract.AuthenticationIdentityLookupKey(
                            **values  # type: ignore[arg-type]
                        )
                    )

    def test_lookup_key_rejects_exact_string_subclasses(self):
        class StringSubclass(str):
            pass

        for field in ("issuer", "subject"):
            values = {"issuer": ISSUER, "subject": SUBJECT}
            values[field] = StringSubclass(values[field])
            with self.subTest(field=field):
                self.assert_contract_error(
                    lambda values=values: contract.AuthenticationIdentityLookupKey(
                        **values  # type: ignore[arg-type]
                    )
                )

    def test_public_identifier_validators_accept_only_exact_canonical_ids(self):
        self.assertIsNone(contract.validate_current_account_user_id(USER_ID))
        self.assertIsNone(
            contract.validate_current_account_workspace_id(WORKSPACE_ID)
        )

        class StringSubclass(str):
            pass

        cases = (
            (
                contract.validate_current_account_user_id,
                (
                    WORKSPACE_ID,
                    USER_ID + "=",
                    USER_ID[:-1],
                    "usr_" + ("!" * 22),
                    StringSubclass(USER_ID),
                    True,
                    object(),
                    None,
                ),
            ),
            (
                contract.validate_current_account_workspace_id,
                (
                    USER_ID,
                    WORKSPACE_ID + "=",
                    WORKSPACE_ID[:-1],
                    "wsp_" + ("!" * 22),
                    StringSubclass(WORKSPACE_ID),
                    False,
                    object(),
                    None,
                ),
            ),
        )
        for validator, invalid_values in cases:
            for invalid in invalid_values:
                with self.subTest(
                    validator=validator.__name__, invalid=type(invalid).__name__
                ):
                    self.assert_contract_error(
                        lambda validator=validator, invalid=invalid: validator(
                            invalid  # type: ignore[arg-type]
                        )
                    )

    def test_lookup_revalidator_rejects_ducks_and_corrupted_keys(self):
        self.assert_contract_error(
            lambda: contract.validate_authentication_identity_lookup_key(
                types.SimpleNamespace(
                    issuer=ISSUER, subject=SUBJECT
                )  # type: ignore[arg-type]
            )
        )
        key = contract.AuthenticationIdentityLookupKey(ISSUER, SUBJECT)
        object.__setattr__(key, "subject", " invalid")
        self.assert_contract_error(
            lambda: contract.validate_authentication_identity_lookup_key(key)
        )


class AggregateValidationTests(ContractTestCase):
    def test_complete_graphs_succeed_and_preserve_exact_records(self):
        user = _user()
        email = _email()
        identity = _identity()
        workspace = _workspace()
        membership = _membership()
        authority = _authority(
            user=user,
            email=email,
            identity=identity,
            workspace=workspace,
            membership=membership,
        )
        self.assertIs(authority.user, user)
        self.assertIs(authority.primary_verified_email, email)
        self.assertIs(authority.authentication_identity, identity)
        self.assertIs(authority.workspace, workspace)
        self.assertIs(authority.workspace_membership, membership)

        by_user = _by_user_authority(
            user=user,
            email=email,
            workspace=workspace,
            membership=membership,
        )
        self.assertIs(by_user.user, user)
        self.assertIs(by_user.primary_verified_email, email)
        self.assertIs(by_user.workspace, workspace)
        self.assertIs(by_user.workspace_membership, membership)

    def test_all_active_membership_roles_are_valid(self):
        for role in auth_models.WorkspaceRole:
            with self.subTest(role=role):
                membership = _membership(role=role)
                self.assertIs(
                    _authority(membership=membership).workspace_membership,
                    membership,
                )
                self.assertIs(
                    _by_user_authority(membership=membership).workspace_membership,
                    membership,
                )

    def test_workspace_creator_provenance_is_not_ownership_authority(self):
        workspace = _workspace(created_by_user_id=OTHER_USER_ID)
        membership = _membership(role=auth_models.WorkspaceRole.ADMIN)
        authority = _authority(workspace=workspace, membership=membership)
        self.assertEqual(authority.workspace.created_by_user_id, OTHER_USER_ID)
        self.assertIs(
            authority.workspace_membership.role,
            auth_models.WorkspaceRole.ADMIN,
        )

    def test_identity_may_have_no_verified_email_link_or_match_primary(self):
        for linked_email_id in (None, EMAIL_ID):
            identity = _identity(verified_email_id=linked_email_id)
            with self.subTest(linked_email_id=linked_email_id):
                self.assertIs(
                    _authority(identity=identity).authentication_identity,
                    identity,
                )

    def test_another_ordinary_email_is_irrelevant_but_identity_cannot_link_it(self):
        ordinary_email = _email(
            email_id=OTHER_EMAIL_ID,
            canonical_email="ordinary@example.test",
        )
        self.assertEqual(ordinary_email.user_id, USER_ID)
        self.assertIsInstance(_authority(), contract.CurrentAccountAuthority)
        linked_elsewhere = _identity(verified_email_id=OTHER_EMAIL_ID)
        self.assert_contract_error(
            lambda: _authority(identity=linked_elsewhere)
        )

    def test_cross_record_relationship_confusion_is_rejected(self):
        cases = (
            (
                "email user",
                lambda: _authority(email=_email(user_id=OTHER_USER_ID)),
            ),
            (
                "wrong primary email",
                lambda: _authority(
                    user=_user(primary_verified_email_id=OTHER_EMAIL_ID)
                ),
            ),
            (
                "identity user",
                lambda: _authority(
                    identity=_identity(
                        user_id=OTHER_USER_ID, verified_email_id=None
                    )
                ),
            ),
            (
                "identity email",
                lambda: _authority(
                    identity=_identity(verified_email_id=OTHER_EMAIL_ID)
                ),
            ),
            (
                "membership user",
                lambda: _authority(
                    membership=_membership(user_id=OTHER_USER_ID)
                ),
            ),
            (
                "membership workspace",
                lambda: _authority(
                    membership=_membership(workspace_id=OTHER_WORKSPACE_ID)
                ),
            ),
            (
                "by-user email user",
                lambda: _by_user_authority(
                    email=_email(user_id=OTHER_USER_ID)
                ),
            ),
            (
                "by-user wrong primary email",
                lambda: _by_user_authority(
                    user=_user(primary_verified_email_id=OTHER_EMAIL_ID)
                ),
            ),
            (
                "by-user membership user",
                lambda: _by_user_authority(
                    membership=_membership(user_id=OTHER_USER_ID)
                ),
            ),
            (
                "by-user membership workspace",
                lambda: _by_user_authority(
                    membership=_membership(workspace_id=OTHER_WORKSPACE_ID)
                ),
            ),
        )
        for label, attempt in cases:
            with self.subTest(case=label):
                self.assert_contract_error(attempt)

    def test_every_valid_inactive_state_is_rejected(self):
        identity_cases = (
            lambda: _authority(
                user=_user(status=auth_models.UserStatus.SUSPENDED)
            ),
            lambda: _authority(
                user=_user(status=auth_models.UserStatus.DISABLED)
            ),
            lambda: _authority(
                email=_email(
                    status=auth_models.VerifiedEmailStatus.PENDING,
                    verified_at=None,
                )
            ),
            lambda: _authority(
                email=_email(
                    status=auth_models.VerifiedEmailStatus.RETIRED,
                    retired_at=19,
                )
            ),
            lambda: _authority(
                identity=_identity(
                    status=auth_models.AuthenticationIdentityStatus.DISABLED
                )
            ),
            lambda: _authority(
                identity=_identity(
                    status=auth_models.AuthenticationIdentityStatus.REVOKED
                )
            ),
            lambda: _authority(
                workspace=_workspace(status=auth_models.WorkspaceStatus.SUSPENDED)
            ),
            lambda: _authority(
                workspace=_workspace(status=auth_models.WorkspaceStatus.ARCHIVED)
            ),
            lambda: _authority(
                membership=_membership(
                    status=auth_models.WorkspaceMembershipStatus.SUSPENDED
                )
            ),
            lambda: _authority(
                membership=_membership(
                    role=auth_models.WorkspaceRole.OWNER,
                    status=auth_models.WorkspaceMembershipStatus.REMOVED,
                )
            ),
        )
        for index, attempt in enumerate(identity_cases):
            with self.subTest(operation="identity", case=index):
                self.assert_contract_error(attempt)

        by_user_cases = (
            lambda: _by_user_authority(
                user=_user(status=auth_models.UserStatus.SUSPENDED)
            ),
            lambda: _by_user_authority(
                email=_email(
                    status=auth_models.VerifiedEmailStatus.PENDING,
                    verified_at=None,
                )
            ),
            lambda: _by_user_authority(
                workspace=_workspace(status=auth_models.WorkspaceStatus.ARCHIVED)
            ),
            lambda: _by_user_authority(
                membership=_membership(
                    status=auth_models.WorkspaceMembershipStatus.REMOVED
                )
            ),
        )
        for index, attempt in enumerate(by_user_cases):
            with self.subTest(operation="user", case=index):
                self.assert_contract_error(attempt)

    def test_corrupted_versions_security_epoch_and_exact_types_are_rejected(self):
        corruption_cases = (
            ("user schema", _user(), "schema_version", 2, "identity"),
            ("user row", _user(), "row_version", 0, "identity"),
            ("user bool row", _user(), "row_version", True, "identity"),
            ("security epoch", _user(), "security_epoch", 0, "identity"),
            ("email schema", _email(), "schema_version", 2, "identity"),
            ("email row", _email(), "row_version", -1, "identity"),
            ("identity schema", _identity(), "schema_version", 2, "identity"),
            ("identity row", _identity(), "row_version", 0, "identity"),
            ("workspace schema", _workspace(), "schema_version", 2, "identity"),
            ("workspace row", _workspace(), "row_version", False, "identity"),
            ("membership schema", _membership(), "schema_version", 2, "identity"),
            ("membership row", _membership(), "row_version", 0, "identity"),
            ("by-user user row", _user(), "row_version", 0, "user"),
            ("by-user email row", _email(), "row_version", 0, "user"),
            ("by-user workspace row", _workspace(), "row_version", 0, "user"),
            ("by-user membership row", _membership(), "row_version", 0, "user"),
        )
        for label, record, field, value, operation in corruption_cases:
            object.__setattr__(record, field, value)
            if operation == "identity":
                kwargs = {
                    auth_models.CuevionUser: {"user": record},
                    auth_models.VerifiedEmail: {"email": record},
                    auth_models.AuthenticationIdentity: {"identity": record},
                    auth_models.Workspace: {"workspace": record},
                    auth_models.WorkspaceMembership: {"membership": record},
                }[type(record)]
                attempt = (
                    lambda kwargs=kwargs: _authority(
                        **kwargs  # type: ignore[arg-type]
                    )
                )
            else:
                kwargs = {
                    auth_models.CuevionUser: {"user": record},
                    auth_models.VerifiedEmail: {"email": record},
                    auth_models.Workspace: {"workspace": record},
                    auth_models.WorkspaceMembership: {"membership": record},
                }[type(record)]
                attempt = (
                    lambda kwargs=kwargs: _by_user_authority(
                        **kwargs  # type: ignore[arg-type]
                    )
                )
            with self.subTest(case=label):
                self.assert_contract_error(attempt)

    def test_exact_nested_record_types_reject_ducks(self):
        valid_identity_values: dict[str, object] = {
            "user": _user(),
            "primary_verified_email": _email(),
            "authentication_identity": _identity(),
            "workspace": _workspace(),
            "workspace_membership": _membership(),
        }
        for field in tuple(valid_identity_values):
            values = dict(valid_identity_values)
            values[field] = types.SimpleNamespace()
            with self.subTest(aggregate="identity", field=field):
                self.assert_contract_error(
                    lambda values=values: contract.CurrentAccountAuthority(
                        **values  # type: ignore[arg-type]
                    )
                )

        valid_user_values: dict[str, object] = {
            "user": _user(),
            "primary_verified_email": _email(),
            "workspace": _workspace(),
            "workspace_membership": _membership(),
        }
        for field in tuple(valid_user_values):
            values = dict(valid_user_values)
            values[field] = types.SimpleNamespace()
            with self.subTest(aggregate="user", field=field):
                self.assert_contract_error(
                    lambda values=values: contract.CurrentAccountByUserAuthority(
                        **values  # type: ignore[arg-type]
                    )
                )


class ResultAndImmutabilityTests(ContractTestCase):
    def test_both_result_envelopes_enforce_the_exact_outcome_matrix(self):
        identity_authority = _authority()
        user_authority = _by_user_authority()
        result_types = (
            (contract.CurrentAccountAuthorityResult, identity_authority),
            (contract.CurrentAccountByUserAuthorityResult, user_authority),
        )
        for result_type, expected_authority in result_types:
            payloads = (None, expected_authority)
            for outcome in contract.CurrentAccountReadOutcome:
                for authority in payloads:
                    valid = (
                        outcome is contract.CurrentAccountReadOutcome.FOUND
                        and authority is expected_authority
                    ) or (
                        outcome is not contract.CurrentAccountReadOutcome.FOUND
                        and authority is None
                    )
                    with self.subTest(
                        result=result_type.__name__,
                        outcome=outcome,
                        payload=authority is not None,
                    ):
                        if valid:
                            result = result_type(outcome, authority)
                            self.assertIs(result.outcome, outcome)
                            self.assertIs(result.authority, authority)
                        else:
                            self.assert_contract_error(
                                lambda
                                result_type=result_type,
                                outcome=outcome,
                                authority=authority: result_type(
                                    outcome, authority
                                ),
                            )

        self.assert_contract_error(
            lambda: contract.CurrentAccountAuthorityResult(
                contract.CurrentAccountReadOutcome.FOUND,
                user_authority,  # type: ignore[arg-type]
            )
        )
        self.assert_contract_error(
            lambda: contract.CurrentAccountByUserAuthorityResult(
                contract.CurrentAccountReadOutcome.FOUND,
                identity_authority,  # type: ignore[arg-type]
            )
        )

    def test_result_revalidates_nested_authority(self):
        authority = _authority()
        object.__setattr__(authority.user, "row_version", 0)
        self.assert_contract_error(
            lambda: contract.CurrentAccountAuthorityResult(
                contract.CurrentAccountReadOutcome.FOUND, authority
            )
        )

        by_user = _by_user_authority()
        object.__setattr__(by_user.workspace_membership, "row_version", 0)
        self.assert_contract_error(
            lambda: contract.CurrentAccountByUserAuthorityResult(
                contract.CurrentAccountReadOutcome.FOUND, by_user
            )
        )

    def test_failure_results_are_value_free_and_retain_no_submitted_values(self):
        key = contract.AuthenticationIdentityLookupKey(ISSUER, SUBJECT)
        del key
        for result_type in (
            contract.CurrentAccountAuthorityResult,
            contract.CurrentAccountByUserAuthorityResult,
        ):
            for outcome in (
                contract.CurrentAccountReadOutcome.NOT_AUTHORIZED,
                contract.CurrentAccountReadOutcome.UNAVAILABLE,
                contract.CurrentAccountReadOutcome.INTERNAL_ERROR,
            ):
                result = result_type(outcome, None)
                self.assertIsNone(result.authority)
                self.assertEqual(tuple(result.__slots__), ("outcome", "authority"))
                for marker in SENSITIVE_MARKERS:
                    self.assertNotIn(marker, repr(result))
                    self.assertNotIn(marker, str(result))
                    self.assertNotIn(marker, repr((result.outcome, result.authority)))

    def test_records_are_slotted_immutable_nonsubclassable_and_opaque(self):
        for record in _all_records():
            record_type = type(record)
            field_names = PublicSurfaceTests.RECORD_FIELDS[record_type]
            with self.subTest(record=record_type.__name__):
                self.assertFalse(hasattr(record, "__dict__"))
                self.assertFalse(dataclasses.is_dataclass(record))
                field = field_names[0]
                original = object.__getattribute__(record, field)
                self.assert_contract_error(
                    lambda: setattr(record, field, object())
                )
                self.assertIs(object.__getattribute__(record, field), original)
                self.assert_contract_error(lambda: delattr(record, field))
                self.assertIs(object.__getattribute__(record, field), original)
                for rendering in (repr(record), str(record)):
                    self.assertEqual(rendering, f"{record_type.__name__}(...)")
                    for marker in SENSITIVE_MARKERS:
                        self.assertNotIn(marker, rendering)
                with self.assertRaises(
                    contract.CurrentAccountRepositoryContractValidationError
                ):
                    types.new_class(
                        f"Derived{record_type.__name__}",
                        (record_type,),
                        {},
                        lambda namespace: namespace.update(__slots__=()),
                    )

    def test_uncontrolled_serialization_is_blocked(self):
        for record in _all_records():
            with self.subTest(record=type(record).__name__, operation="json"):
                with self.assertRaises(TypeError):
                    json.dumps(record)
            with self.subTest(record=type(record).__name__, operation="asdict"):
                with self.assertRaises(TypeError):
                    dataclasses.asdict(record)
            for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
                with self.subTest(
                    record=type(record).__name__,
                    operation="pickle",
                    protocol=protocol,
                ):
                    self.assert_contract_error(
                        lambda record=record, protocol=protocol: pickle.dumps(
                            record, protocol=protocol
                        ),
                        markers=SENSITIVE_MARKERS,
                    )
            state = {"private": SUBJECT}
            for attempt in (
                lambda record=record: record.__reduce__(),
                lambda record=record: record.__reduce_ex__(state),
                lambda record=record: record.__getstate__(),
                lambda record=record: record.__setstate__(state),
            ):
                self.assert_contract_error(attempt, markers=(SUBJECT,))


class ErrorRedactionTests(ContractTestCase):
    def test_fixed_error_accepts_no_values_and_remains_opaque(self):
        error = contract.CurrentAccountRepositoryContractValidationError()
        self.assertEqual(error.args, ())
        self.assertEqual(
            str(error), "invalid current account repository contract value"
        )
        self.assertEqual(
            repr(error),
            "CurrentAccountRepositoryContractValidationError()",
        )
        for arguments in ((SUBJECT,), (object(),)):
            with self.subTest(arguments=type(arguments[0]).__name__):
                with self.assertRaises(TypeError) as captured:
                    contract.CurrentAccountRepositoryContractValidationError(
                        *arguments
                    )
                self.assertNotIn(SUBJECT, str(captured.exception))
        with self.assertRaises(TypeError):
            types.new_class(
                "DerivedValidationError",
                (contract.CurrentAccountRepositoryContractValidationError,),
            )

    def test_rejected_sensitive_inputs_never_reach_controlled_surfaces(self):
        private_issuer = " private-issuer-marker"
        self.assert_contract_error(
            lambda: contract.AuthenticationIdentityLookupKey(
                private_issuer, SUBJECT
            ),
            markers=(private_issuer, SUBJECT),
        )
        private_user = "usr_private-user-marker"
        self.assert_contract_error(
            lambda: contract.validate_current_account_user_id(private_user),
            markers=(private_user,),
        )


if __name__ == "__main__":
    unittest.main()
