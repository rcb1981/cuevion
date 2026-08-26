from __future__ import annotations

import base64
import io
import json
import socket
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from api.auth import runtime, session_store
from api.collaboration.owner_request_security import (
    VerifiedOwnerAuthentication,
    mailbox_is_allowlisted,
    owner_is_allowlisted,
    parse_owner_security_configuration,
    resolve_owner_request_context,
)
from tools import collaboration_allowlist
from tools import collaboration_allowlist_authority as tool


KEY = b"k" * 32
CSRF_KEY = b"c" * 32
ENCODED_KEY = base64.urlsafe_b64encode(KEY).rstrip(b"=").decode("ascii")
ENCODED_CSRF_KEY = (
    base64.urlsafe_b64encode(CSRF_KEY).rstrip(b"=").decode("ascii")
)
ISSUER = "synthetic-auth-v1"
SUBJECT = "synthetic:user_0000000001"
OWNER_EMAIL = "owner@example.test"
USER_ID = "usr_" + ("U" * 22)
WORKSPACE_ID = "wsp_" + ("W" * 22)
SESSION_ID = "S" * 43
CREDENTIAL_DIGEST = (
    base64.urlsafe_b64encode(b"d" * 32).rstrip(b"=").decode("ascii")
)
SESSION_COOKIE = "v1.1.1.1." + ("q" * 43)
MAILBOX = "synthetic.mailbox-1"
OTHER_MAILBOX = "synthetic.mailbox-2"
UNSELECTED_MAILBOX = "synthetic.mailbox-3"
NOW = 1_800_000_100


def selector_input(mailboxes: object = None, **extra: object) -> bytes:
    value = {
        "mailboxIds": [MAILBOX] if mailboxes is None else mailboxes,
        **extra,
    }
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def authenticated_session() -> runtime.AuthenticatedMemberSessionContext:
    member = runtime.AuthenticatedMemberContext(
        user_id=USER_ID,
        email=OWNER_EMAIL,
        name="Synthetic Owner",
        workspace_id=WORKSPACE_ID,
        membership_role="owner",
    )
    return runtime.AuthenticatedMemberSessionContext(
        member=member,
        authentication_version=session_store.SESSION_SCHEMA_VERSION,
        issuer=ISSUER,
        subject=SUBJECT,
        session_id=SESSION_ID,
        credential_digest=CREDENTIAL_DIGEST,
        issued_at=NOW - 100,
        expires_at=NOW + 100,
    )


def authenticated_resolution(
    trusted: runtime.AuthenticatedMemberSessionContext | None = None,
) -> runtime.AuthenticatedMemberSessionResolution:
    return runtime.AuthenticatedMemberSessionResolution(
        runtime.MemberResolutionOutcome.AUTHENTICATED,
        authenticated_session() if trusted is None else trusted,
    )


def mailbox_result(
    mailbox_id: str,
    member: runtime.AuthenticatedMemberContext,
) -> dict[str, object]:
    return {
        "status": "ok",
        "memberAuthority": member,
        "user": {
            "email": member.email,
            "name": member.name,
            "userType": member.user_type,
        },
        "inbox": {
            "id": mailbox_id,
            "email": f"{mailbox_id}@example.test",
            "provider": "google",
        },
        "config": {},
        "error": None,
    }


def authority_contracts(
    resolution: object,
    mailbox_resolver: object | None = None,
) -> tool._AuthorityContracts:
    def resolve_session(_headers: object, **_keywords: object) -> object:
        if isinstance(resolution, BaseException):
            raise resolution
        return resolution

    runtime_contract = SimpleNamespace(
        resolve_authenticated_member_session=resolve_session,
        MemberResolutionOutcome=runtime.MemberResolutionOutcome,
        AuthenticatedMemberContext=runtime.AuthenticatedMemberContext,
        AuthenticatedMemberSessionContext=(
            runtime.AuthenticatedMemberSessionContext
        ),
    )
    session_contract = SimpleNamespace(
        SESSION_COOKIE_NAME=session_store.SESSION_COOKIE_NAME,
        build_session_cookie=session_store.build_session_cookie,
    )
    trusted = (
        resolution.session
        if type(resolution) is runtime.AuthenticatedMemberSessionResolution
        and resolution.session is not None
        else authenticated_session()
    )
    resolver = (
        (
            lambda _headers, mailbox_id: mailbox_result(
                mailbox_id,
                trusted.member,
            )
        )
        if mailbox_resolver is None
        else mailbox_resolver
    )
    return tool._AuthorityContracts(
        runtime_contract,
        session_contract,
        resolver,
    )


def execution_environment(**updates: str) -> dict[str, str]:
    return {
        tool.AUTHORITY_CONFIRM_ENV: tool.AUTHORITY_CONFIRM_VALUE,
        tool.SESSION_COOKIE_ENV: SESSION_COOKIE,
        collaboration_allowlist.HMAC_KEY_ENV: ENCODED_KEY,
        **updates,
    }


def invoke(
    arguments: list[str],
    raw: bytes,
    *,
    environment: object | None = None,
    contracts: tool._AuthorityContracts | None = None,
) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
    patcher = (
        mock.patch.object(tool, "_load_authority_contracts", return_value=contracts)
        if contracts is not None
        else mock.patch.object(
            tool,
            "_load_authority_contracts",
            wraps=tool._load_authority_contracts,
        )
    )
    try:
        with patcher:
            code = tool.main(
                arguments,
                environment={} if environment is None else environment,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
            )
    finally:
        stdin.detach()
    return code, stdout.getvalue(), stderr.getvalue()


def output_environment(stdout: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in stdout.splitlines():
        if line.startswith("CUEVION_COLLAB_V2_"):
            name, value = line.split("=", 1)
            result[name] = value
    return result


class SelectorValidationTests(unittest.TestCase):
    def assert_invalid(self, raw: bytes) -> None:
        with self.assertRaises(tool.AuthorityToolError) as caught:
            tool.parse_selectors(raw)
        self.assertEqual(caught.exception.code, "invalid_selectors")

    def test_closed_schema_empty_malformed_and_duplicate_selectors_fail(self):
        for raw in (
            b"",
            b"not-json",
            b'{"mailboxIds":[],"extra":true}',
            b'{"mailboxIds":[],"mailboxIds":[]}',
            selector_input([]),
            selector_input("mailbox"),
            selector_input([MAILBOX, MAILBOX]),
            selector_input(["Synthetic.mailbox-1"]),
            selector_input([""]),
            selector_input([1]),
        ):
            with self.subTest(raw=raw):
                self.assert_invalid(raw)

    def test_more_than_five_selectors_fails_closed(self):
        self.assert_invalid(
            selector_input(
                [f"synthetic.mailbox-{index}" for index in range(6)]
            )
        )


class ArmingAndDryRunTests(unittest.TestCase):
    class UnreadableEnvironment(dict[str, str]):
        def get(self, *_args: object, **_kwargs: object) -> str:
            raise AssertionError("environment must not be read")

    class UnreadableInput(io.StringIO):
        def read(self, *_args: object, **_kwargs: object) -> str:
            raise AssertionError("stdin must not be read")

    def test_default_invocation_has_zero_authority_access_and_reads_no_input_or_env(
        self,
    ):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
            socket,
            "socket",
            side_effect=AssertionError("network is forbidden"),
        ), mock.patch.object(
            tool,
            "_load_authority_contracts",
            side_effect=AssertionError("contracts must not load"),
        ):
            code = tool.main(
                [],
                environment=self.UnreadableEnvironment(),
                stdin=self.UnreadableInput(),
                stdout=stdout,
                stderr=stderr,
            )
        self.assertEqual((code, stdout.getvalue()), (2, ""))
        self.assertEqual(stderr.getvalue(), "error: execution_not_armed\n")

    def test_dry_run_loads_real_contracts_without_credentials_or_network(self):
        with mock.patch.object(
            socket,
            "socket",
            side_effect=AssertionError("network is forbidden"),
        ), mock.patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("network is forbidden"),
        ):
            code, stdout, stderr = invoke(
                ["--dry-run"],
                selector_input([MAILBOX, OTHER_MAILBOX]),
                environment=self.UnreadableEnvironment(),
            )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("authorityAccess: not_executed", stdout)
        self.assertIn("owners: 1", stdout)
        self.assertIn("mailboxes: 2", stdout)
        for raw_value in (ISSUER, SUBJECT, MAILBOX, OTHER_MAILBOX):
            self.assertNotIn(raw_value, stdout)

        from api.collaboration import authorization

        contracts = tool._load_authority_contracts()
        self.assertIs(
            contracts.mailbox_resolver,
            authorization._resolve_verified_owned_managed_inbox_record,
        )

    def test_missing_first_and_second_guard_fail_before_authority_access(self):
        for arguments, environment, expected in (
            ([], self.UnreadableEnvironment(), "execution_not_armed"),
            (
                ["--execute-authority"],
                {},
                "confirmation_required",
            ),
            (
                ["--execute-authority"],
                {tool.AUTHORITY_CONFIRM_ENV: "wrong"},
                "confirmation_required",
            ),
        ):
            with self.subTest(arguments=arguments, environment=environment):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with mock.patch.object(
                    tool,
                    "_load_authority_contracts",
                    side_effect=AssertionError("authority access is forbidden"),
                ):
                    code = tool.main(
                        arguments,
                        environment=environment,
                        stdin=self.UnreadableInput(),
                        stdout=stdout,
                        stderr=stderr,
                    )
                self.assertEqual(code, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(stderr.getvalue(), f"error: {expected}\n")

    def test_cookie_is_not_an_argument_and_argument_errors_are_redacted(self):
        code, stdout, stderr = invoke(
            ["--cookie", SESSION_COOKIE],
            b"",
            environment=self.UnreadableEnvironment(),
        )
        self.assertEqual((code, stdout), (2, ""))
        self.assertEqual(stderr, "error: invalid_arguments\n")
        self.assertNotIn(SESSION_COOKIE, stderr)


class AuthorityFailureTests(unittest.TestCase):
    def assert_execute_error(
        self,
        resolution: object,
        expected: str,
        *,
        mailbox_resolver: object | None = None,
        environment: dict[str, str] | None = None,
    ) -> None:
        contracts = authority_contracts(resolution, mailbox_resolver)
        code, stdout, stderr = invoke(
            ["--execute-authority"],
            selector_input(),
            environment=(
                execution_environment()
                if environment is None
                else environment
            ),
            contracts=contracts,
        )
        self.assertEqual((code, stdout), (2, ""))
        self.assertEqual(stderr, f"error: {expected}\n")
        for secret in (SESSION_COOKIE, ENCODED_KEY, ISSUER, SUBJECT, MAILBOX):
            self.assertNotIn(secret, stderr)

    def test_malformed_unauthenticated_stale_and_revoked_sessions_fail_redacted(self):
        unauthenticated = runtime.AuthenticatedMemberSessionResolution(
            runtime.MemberResolutionOutcome.UNAUTHENTICATED,
            None,
        )
        for label in ("malformed", "unauthenticated", "stale", "revoked"):
            with self.subTest(label=label):
                self.assert_execute_error(
                    unauthenticated,
                    "session_not_authenticated",
                )

        malformed_environment = execution_environment()
        malformed_environment[tool.SESSION_COOKIE_ENV] = "x" * 129
        self.assert_execute_error(
            authenticated_resolution(),
            "session_not_authenticated",
            environment=malformed_environment,
        )

    def test_unavailable_session_store_and_account_authority_fail_redacted(self):
        unavailable = runtime.AuthenticatedMemberSessionResolution(
            runtime.MemberResolutionOutcome.UNAVAILABLE,
            None,
        )
        for label in ("session_store", "account_authority"):
            with self.subTest(label=label):
                self.assert_execute_error(
                    unavailable,
                    "session_authority_unavailable",
                )

    def test_wrong_auth_source_and_non_member_fail_closed(self):
        for field, value in (("auth_source", "legacy"), ("user_type", "guest")):
            trusted = authenticated_session()
            object.__setattr__(trusted.member, field, value)
            with self.subTest(field=field):
                self.assert_execute_error(
                    authenticated_resolution(trusted),
                    "session_not_authenticated",
                )

    def test_unknown_ambiguous_and_unavailable_mailbox_fail_closed(self):
        trusted = authenticated_session()
        resolution = authenticated_resolution(trusted)
        for status, expected in (
            ("not_found", "mailbox_not_owned"),
            ("malformed", "mailbox_authority_invalid"),
            ("conflict", "mailbox_authority_invalid"),
            ("unavailable", "mailbox_authority_unavailable"),
            ("unauthorized", "session_not_authenticated"),
        ):
            with self.subTest(status=status):
                self.assert_execute_error(
                    resolution,
                    expected,
                    mailbox_resolver=lambda _headers, _mailbox, status=status: {
                        "status": status
                    },
                )

    def test_mailbox_not_owned_by_exact_revalidated_member_fails(self):
        trusted = authenticated_session()
        different = runtime.AuthenticatedMemberContext(
            user_id="usr_" + ("X" * 22),
            email="different@example.test",
            name="Different Owner",
            workspace_id="wsp_" + ("X" * 22),
            membership_role="owner",
        )
        self.assert_execute_error(
            authenticated_resolution(trusted),
            "mailbox_not_owned",
            mailbox_resolver=lambda _headers, mailbox_id: mailbox_result(
                mailbox_id,
                different,
            ),
        )

    def test_provider_and_resolver_errors_are_redacted(self):
        marker = "provider-secret-body"
        self.assert_execute_error(
            authenticated_resolution(),
            "mailbox_authority_unavailable",
            mailbox_resolver=lambda _headers, _mailbox: (_ for _ in ()).throw(
                RuntimeError(marker)
            ),
        )


class SuccessfulGenerationTests(unittest.TestCase):
    def execute(self, mailboxes: list[str]) -> tuple[str, object]:
        trusted = authenticated_session()
        resolution = authenticated_resolution(trusted)
        contracts = authority_contracts(resolution)
        code, stdout, stderr = invoke(
            ["--execute-authority"],
            selector_input(mailboxes),
            environment=execution_environment(),
            contracts=contracts,
        )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        return stdout, trusted

    def test_one_owner_one_and_multiple_selected_mailboxes_succeed_safely(self):
        for mailboxes in ([MAILBOX], [MAILBOX, OTHER_MAILBOX]):
            with self.subTest(mailboxes=mailboxes):
                stdout, trusted = self.execute(mailboxes)
                self.assertIn("owners: 1", stdout)
                self.assertIn(f"mailboxes: {len(mailboxes)}", stdout)
                self.assertIn(
                    collaboration_allowlist.OWNER_ALLOWLIST_ENV + "=v1_",
                    stdout,
                )
                self.assertIn(
                    collaboration_allowlist.MAILBOX_ALLOWLIST_ENV + "=v1_",
                    stdout,
                )
                for raw_value in (
                    SESSION_COOKIE,
                    ISSUER,
                    SUBJECT,
                    OWNER_EMAIL,
                    USER_ID,
                    WORKSPACE_ID,
                    SESSION_ID,
                    CREDENTIAL_DIGEST,
                    ENCODED_KEY,
                    *mailboxes,
                ):
                    self.assertNotIn(raw_value, stdout)
                self.assertIsInstance(
                    trusted,
                    runtime.AuthenticatedMemberSessionContext,
                )

    def test_exact_runtime_receives_only_the_minimal_canonical_cookie_header(self):
        trusted = authenticated_session()
        resolution = authenticated_resolution(trusted)
        observed: list[object] = []

        def resolve_session(raw_headers: object, **keywords: object) -> object:
            observed.append((raw_headers, keywords))
            return resolution

        def resolve_mailbox(raw_headers: object, mailbox_id: object) -> object:
            observed.append((raw_headers, mailbox_id))
            return mailbox_result(str(mailbox_id), trusted.member)

        contracts = tool._AuthorityContracts(
            SimpleNamespace(
                resolve_authenticated_member_session=resolve_session,
                MemberResolutionOutcome=runtime.MemberResolutionOutcome,
                AuthenticatedMemberContext=runtime.AuthenticatedMemberContext,
                AuthenticatedMemberSessionContext=(
                    runtime.AuthenticatedMemberSessionContext
                ),
            ),
            SimpleNamespace(
                SESSION_COOKIE_NAME=session_store.SESSION_COOKIE_NAME,
                build_session_cookie=session_store.build_session_cookie,
            ),
            resolve_mailbox,
        )
        environment = execution_environment()
        code, _stdout, stderr = invoke(
            ["--execute-authority"],
            selector_input(),
            environment=environment,
            contracts=contracts,
        )
        expected_headers = (
            (
                "cookie",
                f"{session_store.SESSION_COOKIE_NAME}={SESSION_COOKIE}",
            ),
        )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(observed[0][0], expected_headers)
        self.assertIs(observed[0][1]["environment"], environment)
        self.assertIs(type(observed[0][1]["now"]), int)
        self.assertEqual(observed[1], (expected_headers, MAILBOX))

    def test_canonical_generator_equivalence_is_byte_for_byte(self):
        mailboxes = [MAILBOX, OTHER_MAILBOX]
        stdout, trusted = self.execute(mailboxes)
        direct_input = json.dumps(
            {
                "owners": [
                    {
                        "issuer": trusted.issuer,
                        "authenticationVersion": (
                            trusted.authentication_version
                        ),
                        "subject": trusted.subject,
                        "mailboxes": mailboxes,
                    }
                ]
            },
            separators=(",", ":"),
        ).encode("utf-8")
        direct = collaboration_allowlist.generate_allowlists(
            collaboration_allowlist.parse_input(direct_input),
            ENCODED_KEY,
        )
        self.assertEqual(
            stdout,
            collaboration_allowlist._generated_output(direct) + "\n",
        )

    def test_output_passes_production_parser_and_unselected_mailbox_is_denied(self):
        stdout, trusted = self.execute([MAILBOX, OTHER_MAILBOX])
        generated_environment = output_environment(stdout)
        configuration = parse_owner_security_configuration(
            {
                "CUEVION_APP_ORIGIN": "https://app.cuevion.com",
                "CUEVION_COLLAB_V2_OWNER_CSRF_KEY": ENCODED_CSRF_KEY,
                "CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY": ENCODED_KEY,
                **generated_environment,
            }
        )
        claims = VerifiedOwnerAuthentication(
            issuer=trusted.issuer,
            authentication_version=trusted.authentication_version,
            subject=trusted.subject,
            owner_email=trusted.member.email,
            workspace_id=trusted.member.workspace_id,
            display_name=trusted.member.name,
            session_id=trusted.session_id,
            credential_digest=trusted.credential_digest,
            issued_at=trusted.issued_at,
            expires_at=trusted.expires_at,
        )
        context = resolve_owner_request_context(
            (),
            authentication_resolver=lambda _headers: claims,
            now=NOW,
        )
        self.assertTrue(owner_is_allowlisted(context, configuration))
        self.assertTrue(mailbox_is_allowlisted(context, MAILBOX, configuration))
        self.assertTrue(
            mailbox_is_allowlisted(context, OTHER_MAILBOX, configuration)
        )
        self.assertFalse(
            mailbox_is_allowlisted(context, UNSELECTED_MAILBOX, configuration)
        )


class ImportSafetyTests(unittest.TestCase):
    def test_import_has_no_handler_or_mailbox_authority_initialization(self):
        script = "\n".join(
            (
                "import sys",
                "import tools.collaboration_allowlist_authority as tool",
                "assert not hasattr(tool, 'handler')",
                "assert 'api.user_config_store' not in sys.modules",
            )
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=".",
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
