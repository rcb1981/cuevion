from __future__ import annotations

import base64
import hashlib
import io
import json
import socket
import unittest
from unittest import mock

from api.collaboration.owner_request_security import (
    VerifiedOwnerAuthentication,
    derive_mailbox_allowlist_entry,
    derive_owner_allowlist_entry,
    mailbox_is_allowlisted,
    owner_is_allowlisted,
    parse_owner_security_configuration,
    resolve_owner_request_context,
)
from tools import collaboration_allowlist as tool


KEY = b"k" * 32
OTHER_KEY = b"z" * 32
ENCODED_KEY = base64.urlsafe_b64encode(KEY).rstrip(b"=").decode("ascii")
ISSUER = "synthetic-auth-v1"
SUBJECT = "synthetic:user_0000000001"
OTHER_SUBJECT = "synthetic:user_0000000002"
MAILBOX = "synthetic.mailbox-1"
OTHER_MAILBOX = "synthetic.mailbox-2"
THIRD_MAILBOX = "synthetic.mailbox-3"
OWNER_VECTOR = "v1_fg2DmrP0d8L041XP09EYf9K1tke6cmiiQldf1irbIek"
MAILBOX_VECTOR = "v1__w8rZeXRtQPRNtblA6gh_-n0hmOkYVaKr7KHopMS2Bk"


def owner(
    *,
    issuer: object = ISSUER,
    version: object = 1,
    subject: object = SUBJECT,
    mailboxes: object = None,
) -> dict[str, object]:
    return {
        "issuer": issuer,
        "authenticationVersion": version,
        "subject": subject,
        "mailboxes": [MAILBOX] if mailboxes is None else mailboxes,
    }


_DEFAULT_OWNERS = object()


def encoded_input(owners: object = _DEFAULT_OWNERS, **extra: object) -> bytes:
    value = {
        "owners": [owner()] if owners is _DEFAULT_OWNERS else owners,
        **extra,
    }
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def invoke(arguments: list[str], raw: bytes, environment: dict[str, str] | None = None):
    stdout = io.StringIO()
    stderr = io.StringIO()
    stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
    try:
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


class DerivationTests(unittest.TestCase):
    def test_fixed_synthetic_owner_and_mailbox_vectors(self):
        self.assertEqual(
            derive_owner_allowlist_entry(KEY, ISSUER, 1, SUBJECT), OWNER_VECTOR
        )
        self.assertEqual(
            derive_mailbox_allowlist_entry(KEY, ISSUER, 1, SUBJECT, MAILBOX),
            MAILBOX_VECTOR,
        )

    def test_identity_mailbox_key_and_repeat_are_bound(self):
        owner_digest = derive_owner_allowlist_entry(KEY, ISSUER, 1, SUBJECT)
        mailbox_digest = derive_mailbox_allowlist_entry(
            KEY, ISSUER, 1, SUBJECT, MAILBOX
        )
        self.assertEqual(
            owner_digest, derive_owner_allowlist_entry(KEY, ISSUER, 1, SUBJECT)
        )
        self.assertNotEqual(
            owner_digest,
            derive_owner_allowlist_entry(KEY, ISSUER, 1, OTHER_SUBJECT),
        )
        self.assertNotEqual(
            mailbox_digest,
            derive_mailbox_allowlist_entry(
                KEY, ISSUER, 1, SUBJECT, OTHER_MAILBOX
            ),
        )
        self.assertNotEqual(
            owner_digest,
            derive_owner_allowlist_entry(OTHER_KEY, ISSUER, 1, SUBJECT),
        )

    def test_equivalent_canonical_input_has_deterministic_sorted_output(self):
        first = tool.parse_input(
            encoded_input(
                [
                    owner(subject=OTHER_SUBJECT, mailboxes=[THIRD_MAILBOX]),
                    owner(mailboxes=[OTHER_MAILBOX, MAILBOX]),
                ]
            )
        )
        second = tool.parse_input(
            encoded_input(
                [
                    owner(mailboxes=[MAILBOX, OTHER_MAILBOX]),
                    owner(subject=OTHER_SUBJECT, mailboxes=[THIRD_MAILBOX]),
                ]
            )
        )
        first_result = tool.generate_allowlists(first, ENCODED_KEY)
        second_result = tool.generate_allowlists(second, ENCODED_KEY)
        self.assertEqual(first_result, second_result)
        self.assertEqual(first_result.owner_digests, tuple(sorted(first_result.owner_digests)))
        self.assertEqual(
            first_result.mailbox_digests,
            tuple(sorted(first_result.mailbox_digests)),
        )


class ValidationTests(unittest.TestCase):
    def assert_error(self, raw: bytes, code: str) -> None:
        with self.assertRaises(tool.AllowlistToolError) as caught:
            tool.parse_input(raw)
        self.assertEqual(caught.exception.code, code)

    def test_closed_schema_duplicate_fields_and_wrong_types(self):
        self.assert_error(encoded_input(extra=True), "invalid_schema")
        self.assert_error(
            json.dumps({"owners": [{**owner(), "extra": True}]}).encode(),
            "invalid_owner_schema",
        )
        self.assert_error(b'{"owners":[],"owners":[]}', "duplicate_json_field")
        for owners in (None, {}, "owner", True, 1, []):
            with self.subTest(owners=owners):
                self.assert_error(encoded_input(owners), "invalid_owners")
        self.assert_error(encoded_input(["owner"]), "invalid_owner_schema")

    def test_malformed_owner_identity_fields_are_rejected_without_normalization(self):
        invalid = (
            owner(issuer=" synthetic-auth-v1"),
            owner(issuer="synthetic auth"),
            owner(issuer=1),
            owner(version=0),
            owner(version=True),
            owner(version="1"),
            owner(subject=" synthetic:user_0000000001"),
            owner(subject="synthetic user"),
            owner(subject=1),
        )
        for value in invalid:
            with self.subTest(value=value):
                self.assert_error(encoded_input([value]), "invalid_owner_identity")

    def test_mailbox_validation_empty_and_duplicates_fail_closed(self):
        for mailboxes, expected in (
            ([], "invalid_mailboxes"),
            ("mailbox", "invalid_mailboxes"),
            ([MAILBOX, MAILBOX], "duplicate_mailbox"),
            (["Synthetic.mailbox-1"], "invalid_mailbox"),
            ([" synthetic.mailbox-1"], "invalid_mailbox"),
            ([1], "invalid_mailbox"),
        ):
            with self.subTest(mailboxes=mailboxes):
                self.assert_error(encoded_input([owner(mailboxes=mailboxes)]), expected)

    def test_duplicate_owner_and_hard_cardinality_limits(self):
        self.assert_error(encoded_input([owner(), owner()]), "duplicate_owner")
        self.assert_error(
            encoded_input([owner(), owner(subject=OTHER_SUBJECT)]),
            "duplicate_mailbox",
        )
        too_many_owners = [
            owner(subject=f"synthetic:user_{index:010d}")
            for index in range(tool.MAX_OWNERS + 1)
        ]
        self.assert_error(encoded_input(too_many_owners), "owner_limit_exceeded")
        too_many_mailboxes = [f"synthetic.mailbox-{index}" for index in range(51)]
        self.assert_error(
            encoded_input([owner(mailboxes=too_many_mailboxes)]),
            "mailbox_limit_exceeded",
        )

    def test_key_is_canonical_unpadded_base64url_and_at_least_32_bytes(self):
        validated = tool.parse_input(encoded_input())
        for value in (None, "not+base64", ENCODED_KEY + "=", "YQ", 1):
            with self.subTest(value=value), self.assertRaises(
                tool.AllowlistToolError
            ) as caught:
                tool.generate_allowlists(validated, value)
            self.assertEqual(caught.exception.code, "invalid_hmac_key")


class CommandTests(unittest.TestCase):
    def test_dry_run_needs_no_secret_and_reports_safe_counts_only(self):
        class UnreadableEnvironment(dict[str, str]):
            def get(self, *_args: object, **_kwargs: object) -> str:
                raise AssertionError("dry-run must not read the environment")

        code, stdout, stderr = invoke(
            ["--dry-run"], encoded_input(), UnreadableEnvironment()
        )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(
            stdout,
            "validation: ok\nowners: 1\nmailboxes: 1\n"
            "ownerDigests: 1\nmailboxDigests: 1\n",
        )
        self.assertNotIn(ISSUER, stdout)
        self.assertNotIn(SUBJECT, stdout)
        self.assertNotIn(MAILBOX, stdout)

    def test_generate_requires_key_and_redacts_failures(self):
        code, stdout, stderr = invoke(["--generate"], encoded_input())
        self.assertEqual((code, stdout, stderr), (2, "", "error: missing_hmac_key\n"))
        secret_marker = "this-is-not-a-valid-secret+"
        code, stdout, stderr = invoke(
            ["--generate"],
            encoded_input(),
            {tool.HMAC_KEY_ENV: secret_marker},
        )
        self.assertEqual(code, 2)
        self.assertNotIn(secret_marker, stdout + stderr)

    def test_generate_output_is_exact_deployment_format_and_contains_no_raw_input_or_key(self):
        code, stdout, stderr = invoke(
            ["--generate"], encoded_input(), {tool.HMAC_KEY_ENV: ENCODED_KEY}
        )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(
            stdout,
            "owners: 1\nmailboxes: 1\nownerDigests: 1\nmailboxDigests: 1\n"
            f"{tool.OWNER_ALLOWLIST_ENV}={OWNER_VECTOR}\n"
            f"{tool.MAILBOX_ALLOWLIST_ENV}={MAILBOX_VECTOR}\n",
        )
        for raw_value in (ISSUER, SUBJECT, MAILBOX, ENCODED_KEY):
            self.assertNotIn(raw_value, stdout)
        owner_line, mailbox_line = stdout.splitlines()[-2:]
        self.assertNotIn(" ", owner_line)
        self.assertNotIn(" ", mailbox_line)
        self.assertNotIn(",,", stdout)

    def test_generation_has_zero_network_behavior(self):
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
                ["--generate"], encoded_input(), {tool.HMAC_KEY_ENV: ENCODED_KEY}
            )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn(tool.OWNER_ALLOWLIST_ENV, stdout)


class ProductionCompatibilityTests(unittest.TestCase):
    def test_generated_values_pass_parser_and_runtime_allowlist_checks(self):
        validated = tool.parse_input(encoded_input())
        generated = tool.generate_allowlists(validated, ENCODED_KEY)
        configuration = parse_owner_security_configuration(
            {
                "CUEVION_APP_ORIGIN": "https://app.cuevion.com",
                "CUEVION_COLLAB_V2_OWNER_CSRF_KEY": base64.urlsafe_b64encode(
                    b"c" * 32
                ).rstrip(b"=").decode("ascii"),
                tool.HMAC_KEY_ENV: ENCODED_KEY,
                tool.OWNER_ALLOWLIST_ENV: ",".join(generated.owner_digests),
                tool.MAILBOX_ALLOWLIST_ENV: ",".join(generated.mailbox_digests),
            }
        )

        def context(subject: str):
            claims = VerifiedOwnerAuthentication(
                issuer=ISSUER,
                authentication_version=1,
                subject=subject,
                owner_email="synthetic-owner@example.invalid",
                workspace_id="wsp_" + ("w" * 22),
                display_name="Synthetic Owner",
                session_id=base64.urlsafe_b64encode(b"s" * 16)
                .rstrip(b"=")
                .decode("ascii"),
                credential_digest=base64.urlsafe_b64encode(
                    hashlib.sha256(b"synthetic-credential").digest()
                ).rstrip(b"=").decode("ascii"),
                issued_at=1_900_000_000,
                expires_at=1_900_003_600,
            )
            return resolve_owner_request_context(
                [], authentication_resolver=lambda _headers: claims, now=1_900_000_001
            )

        allowed = context(SUBJECT)
        denied = context(OTHER_SUBJECT)
        self.assertTrue(owner_is_allowlisted(allowed, configuration))
        self.assertTrue(mailbox_is_allowlisted(allowed, MAILBOX, configuration))
        self.assertFalse(owner_is_allowlisted(denied, configuration))
        self.assertFalse(mailbox_is_allowlisted(allowed, OTHER_MAILBOX, configuration))


if __name__ == "__main__":
    unittest.main()
