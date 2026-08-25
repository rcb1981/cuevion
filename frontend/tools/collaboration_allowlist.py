"""Offline operator tool for Collaboration v2 rollout allowlist generation.

Run from the frontend serverless project root with
``python -m tools.collaboration_allowlist --dry-run`` or ``--generate``.
The tool reads only stdin (or one explicit input file) and, in generate mode,
the dedicated allowlist HMAC-key environment variable. It has no network path.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, TextIO

from api.collaboration.owner_request_security import (
    derive_mailbox_allowlist_entry,
    derive_owner_allowlist_entry,
    parse_allowlist_hmac_key,
    valid_allowlist_mailbox_id,
    valid_allowlist_owner_identity,
)


HMAC_KEY_ENV = "CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY"
OWNER_ALLOWLIST_ENV = "CUEVION_COLLAB_V2_OWNER_ALLOWLIST"
MAILBOX_ALLOWLIST_ENV = "CUEVION_COLLAB_V2_MAILBOX_ALLOWLIST"
MAX_OWNERS = 25
MAX_MAILBOXES_TOTAL = 50
MAX_INPUT_BYTES = 64 * 1024

_ROOT_FIELDS = frozenset({"owners"})
_OWNER_FIELDS = frozenset(
    {"issuer", "authenticationVersion", "subject", "mailboxes"}
)


class AllowlistToolError(Exception):
    """Value-free validation failure suitable for operator output."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class OwnerInput:
    issuer: str
    authentication_version: int
    subject: str
    mailboxes: tuple[str, ...]


@dataclass(frozen=True, slots=True, repr=False)
class ValidatedInput:
    owners: tuple[OwnerInput, ...]

    @property
    def mailbox_count(self) -> int:
        return sum(len(owner.mailboxes) for owner in self.owners)


@dataclass(frozen=True, slots=True, repr=False)
class GeneratedAllowlists:
    owners: int
    mailboxes: int
    owner_digests: tuple[str, ...]
    mailbox_digests: tuple[str, ...]


def _reject_duplicate_object_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AllowlistToolError("duplicate_json_field")
        result[key] = value
    return result


def parse_input(raw: object) -> ValidatedInput:
    if type(raw) is not bytes or not raw or len(raw) > MAX_INPUT_BYTES:
        raise AllowlistToolError("invalid_input")
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_object_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                AllowlistToolError("invalid_json")
            ),
        )
    except AllowlistToolError:
        raise
    except Exception:
        raise AllowlistToolError("invalid_json") from None

    if type(value) is not dict or frozenset(value) != _ROOT_FIELDS:
        raise AllowlistToolError("invalid_schema")
    raw_owners = value["owners"]
    if type(raw_owners) is not list or not raw_owners:
        raise AllowlistToolError("invalid_owners")
    if len(raw_owners) > MAX_OWNERS:
        raise AllowlistToolError("owner_limit_exceeded")

    owners: list[OwnerInput] = []
    owner_identities: set[tuple[str, int, str]] = set()
    all_mailboxes: set[str] = set()
    mailbox_count = 0
    for raw_owner in raw_owners:
        if type(raw_owner) is not dict or frozenset(raw_owner) != _OWNER_FIELDS:
            raise AllowlistToolError("invalid_owner_schema")
        issuer = raw_owner["issuer"]
        authentication_version = raw_owner["authenticationVersion"]
        subject = raw_owner["subject"]
        raw_mailboxes = raw_owner["mailboxes"]
        if type(raw_mailboxes) is not list or not raw_mailboxes:
            raise AllowlistToolError("invalid_mailboxes")

        if not valid_allowlist_owner_identity(
            issuer,
            authentication_version,
            subject,
        ):
            raise AllowlistToolError("invalid_owner_identity")

        identity = (issuer, authentication_version, subject)
        if identity in owner_identities:
            raise AllowlistToolError("duplicate_owner")
        owner_identities.add(identity)

        mailboxes: list[str] = []
        seen_mailboxes: set[str] = set()
        for mailbox_id in raw_mailboxes:
            if not valid_allowlist_mailbox_id(mailbox_id):
                raise AllowlistToolError("invalid_mailbox")
            if mailbox_id in seen_mailboxes or mailbox_id in all_mailboxes:
                raise AllowlistToolError("duplicate_mailbox")
            seen_mailboxes.add(mailbox_id)
            all_mailboxes.add(mailbox_id)
            mailboxes.append(mailbox_id)

        mailbox_count += len(mailboxes)
        if mailbox_count > MAX_MAILBOXES_TOTAL:
            raise AllowlistToolError("mailbox_limit_exceeded")
        owners.append(
            OwnerInput(
                issuer=issuer,
                authentication_version=authentication_version,
                subject=subject,
                mailboxes=tuple(mailboxes),
            )
        )
    return ValidatedInput(tuple(owners))


def generate_allowlists(
    validated: ValidatedInput,
    encoded_hmac_key: object,
) -> GeneratedAllowlists:
    try:
        key = parse_allowlist_hmac_key(encoded_hmac_key)
    except ValueError:
        raise AllowlistToolError("invalid_hmac_key") from None

    owner_digests = sorted(
        derive_owner_allowlist_entry(
            key,
            owner.issuer,
            owner.authentication_version,
            owner.subject,
        )
        for owner in validated.owners
    )
    mailbox_digests = sorted(
        derive_mailbox_allowlist_entry(
            key,
            owner.issuer,
            owner.authentication_version,
            owner.subject,
            mailbox_id,
        )
        for owner in validated.owners
        for mailbox_id in owner.mailboxes
    )
    if (
        len(set(owner_digests)) != len(owner_digests)
        or len(set(mailbox_digests)) != len(mailbox_digests)
    ):
        raise AllowlistToolError("duplicate_digest")
    return GeneratedAllowlists(
        owners=len(validated.owners),
        mailboxes=validated.mailbox_count,
        owner_digests=tuple(owner_digests),
        mailbox_digests=tuple(mailbox_digests),
    )


def _read_input(path: str | None, stdin: TextIO) -> bytes:
    try:
        if path is None:
            stream = getattr(stdin, "buffer", stdin)
            value = stream.read(MAX_INPUT_BYTES + 1)
            return value.encode("utf-8") if type(value) is str else value
        with Path(path).open("rb") as input_file:
            return input_file.read(MAX_INPUT_BYTES + 1)
    except Exception:
        raise AllowlistToolError("input_read_failed") from None


def _safe_counts(validated: ValidatedInput) -> str:
    return "\n".join(
        (
            "validation: ok",
            f"owners: {len(validated.owners)}",
            f"mailboxes: {validated.mailbox_count}",
            f"ownerDigests: {len(validated.owners)}",
            f"mailboxDigests: {validated.mailbox_count}",
        )
    )


def _generated_output(result: GeneratedAllowlists) -> str:
    return "\n".join(
        (
            f"owners: {result.owners}",
            f"mailboxes: {result.mailboxes}",
            f"ownerDigests: {len(result.owner_digests)}",
            f"mailboxDigests: {len(result.mailbox_digests)}",
            f"{OWNER_ALLOWLIST_ENV}={','.join(result.owner_digests)}",
            f"{MAILBOX_ALLOWLIST_ENV}={','.join(result.mailbox_digests)}",
        )
    )


def main(
    arguments: list[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--generate", action="store_true")
    parser.add_argument("--input", metavar="PATH")
    args = parser.parse_args(arguments)

    supplied_environment = os.environ if environment is None else environment
    input_stream = sys.stdin if stdin is None else stdin
    output_stream = sys.stdout if stdout is None else stdout
    error_stream = sys.stderr if stderr is None else stderr
    try:
        validated = parse_input(_read_input(args.input, input_stream))
        if args.dry_run:
            output_stream.write(_safe_counts(validated) + "\n")
            return 0
        encoded_key = supplied_environment.get(HMAC_KEY_ENV)
        if encoded_key is None:
            raise AllowlistToolError("missing_hmac_key")
        result = generate_allowlists(validated, encoded_key)
        output_stream.write(_generated_output(result) + "\n")
        return 0
    except AllowlistToolError as error:
        error_stream.write(f"error: {error.code}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "AllowlistToolError",
    "GeneratedAllowlists",
    "HMAC_KEY_ENV",
    "MAILBOX_ALLOWLIST_ENV",
    "MAX_MAILBOXES_TOTAL",
    "MAX_OWNERS",
    "OWNER_ALLOWLIST_ENV",
    "ValidatedInput",
    "generate_allowlists",
    "main",
    "parse_input",
)
