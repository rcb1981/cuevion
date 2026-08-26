"""Operator-only Collaboration v2 allowlist authority resolver.

Run from the frontend project root. ``--dry-run`` validates only the closed
mailbox-selector input and required import contracts. ``--execute-authority``
is additionally gated by an exact environment confirmation before it reads
any credential or opens any authority connection.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import ModuleType
from typing import TextIO

from tools import collaboration_allowlist


SESSION_COOKIE_ENV = "CUEVION_COLLAB_OPERATOR_SESSION_COOKIE"
AUTHORITY_CONFIRM_ENV = "CUEVION_COLLAB_AUTHORITY_CONFIRM"
AUTHORITY_CONFIRM_VALUE = "RESOLVE_CANONICAL_COLLAB_V2_ALLOWLIST_AUTHORITY"
MAX_SELECTED_MAILBOXES = 5
MAX_SELECTOR_INPUT_BYTES = 4096

_SELECTOR_FIELDS = frozenset({"mailboxIds"})


class AuthorityToolError(Exception):
    """A fixed value-free failure suitable for operator output."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__()


class _RedactedArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise AuthorityToolError("invalid_arguments")


@dataclass(frozen=True, slots=True, repr=False)
class ValidatedSelectors:
    mailbox_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True, repr=False)
class _AuthorityContracts:
    runtime: ModuleType
    session_store: ModuleType
    mailbox_resolver: Callable[[object, object], object]


def _reject_duplicate_object_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AuthorityToolError("invalid_selectors")
        result[key] = value
    return result


def parse_selectors(raw: object) -> ValidatedSelectors:
    """Parse one closed list of one to five canonical mailbox-ID selectors."""

    if (
        type(raw) is not bytes
        or not raw
        or len(raw) > MAX_SELECTOR_INPUT_BYTES
    ):
        raise AuthorityToolError("invalid_selectors")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_object_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                AuthorityToolError("invalid_selectors")
            ),
        )
    except AuthorityToolError:
        raise
    except Exception:
        raise AuthorityToolError("invalid_selectors") from None

    if type(value) is not dict or frozenset(value) != _SELECTOR_FIELDS:
        raise AuthorityToolError("invalid_selectors")
    mailbox_ids = value["mailboxIds"]
    if (
        type(mailbox_ids) is not list
        or not mailbox_ids
        or len(mailbox_ids) > MAX_SELECTED_MAILBOXES
    ):
        raise AuthorityToolError("invalid_selectors")

    validated: list[str] = []
    seen: set[str] = set()
    for mailbox_id in mailbox_ids:
        if not collaboration_allowlist.valid_allowlist_mailbox_id(mailbox_id):
            raise AuthorityToolError("invalid_selectors")
        if mailbox_id in seen:
            raise AuthorityToolError("invalid_selectors")
        seen.add(mailbox_id)
        validated.append(mailbox_id)
    return ValidatedSelectors(tuple(validated))


def _read_selectors(stdin: TextIO) -> bytes:
    try:
        stream = getattr(stdin, "buffer", stdin)
        value = stream.read(MAX_SELECTOR_INPUT_BYTES + 1)
        return value.encode("utf-8") if type(value) is str else value
    except Exception:
        raise AuthorityToolError("selector_read_failed") from None


def _load_authority_contracts() -> _AuthorityContracts:
    """Load import-safe server contracts without resolving configuration."""

    try:
        runtime = importlib.import_module("api.auth.runtime")
        session_store = importlib.import_module("api.auth.session_store")
        authorization = importlib.import_module("api.collaboration.authorization")
        user_config_store = importlib.import_module("api.user_config_store")
        mailbox_resolver = getattr(
            authorization,
            "_resolve_verified_owned_managed_inbox_record",
        )
        valid = (
            callable(getattr(runtime, "resolve_authenticated_member_session", None))
            and isinstance(
                getattr(runtime, "MemberResolutionOutcome", None),
                type,
            )
            and isinstance(
                getattr(runtime, "AuthenticatedMemberContext", None),
                type,
            )
            and isinstance(
                getattr(runtime, "AuthenticatedMemberSessionContext", None),
                type,
            )
            and type(getattr(session_store, "SESSION_COOKIE_NAME", None)) is str
            and bool(session_store.SESSION_COOKIE_NAME)
            and callable(getattr(session_store, "build_session_cookie", None))
            and callable(mailbox_resolver)
            and callable(
                getattr(
                    user_config_store,
                    "resolve_owned_managed_inbox_record",
                    None,
                )
            )
            and callable(collaboration_allowlist.parse_input)
            and callable(collaboration_allowlist.generate_allowlists)
        )
    except Exception:
        raise AuthorityToolError("contract_unavailable") from None
    if not valid:
        raise AuthorityToolError("contract_unavailable")
    return _AuthorityContracts(runtime, session_store, mailbox_resolver)


def _require_environment_value(
    environment: Mapping[str, str],
    name: str,
    error_code: str,
) -> str:
    try:
        value = environment.get(name)
    except Exception:
        raise AuthorityToolError(error_code) from None
    if type(value) is not str or not value:
        raise AuthorityToolError(error_code)
    return value


def _member_authority_matches(
    runtime: ModuleType,
    candidate: object,
    expected: object,
) -> bool:
    try:
        return (
            type(candidate) is runtime.AuthenticatedMemberContext
            and type(expected) is runtime.AuthenticatedMemberContext
            and candidate.auth_source == "auth0"
            and candidate.user_type == "member"
            and candidate.user_id == expected.user_id
            and candidate.email == expected.email
            and candidate.name == expected.name
            and candidate.workspace_id == expected.workspace_id
            and candidate.membership_role == expected.membership_role
        )
    except Exception:
        return False


def _resolve_owned_mailbox_ids(
    contracts: _AuthorityContracts,
    raw_headers: tuple[tuple[str, str], ...],
    member: object,
    selectors: ValidatedSelectors,
) -> tuple[str, ...]:
    resolved: list[str] = []
    for mailbox_id in selectors.mailbox_ids:
        try:
            result = contracts.mailbox_resolver(raw_headers, mailbox_id)
        except Exception:
            raise AuthorityToolError("mailbox_authority_unavailable") from None
        if type(result) is not dict:
            raise AuthorityToolError("mailbox_authority_unavailable")

        status = result.get("status")
        if status == "unauthorized":
            raise AuthorityToolError("session_not_authenticated")
        if status == "not_found":
            raise AuthorityToolError("mailbox_not_owned")
        if status == "unavailable":
            raise AuthorityToolError("mailbox_authority_unavailable")
        if status in {"malformed", "conflict"}:
            raise AuthorityToolError("mailbox_authority_invalid")
        if status != "ok":
            raise AuthorityToolError("mailbox_authority_unavailable")

        member_authority = result.get("memberAuthority")
        owned_user = result.get("user")
        inbox = result.get("inbox")
        if (
            not _member_authority_matches(
                contracts.runtime,
                member_authority,
                member,
            )
            or type(owned_user) is not dict
            or owned_user.get("email") != member.email
            or type(inbox) is not dict
            or inbox.get("id") != mailbox_id
            or inbox.get("provider") not in {"google", "custom_imap"}
        ):
            raise AuthorityToolError("mailbox_not_owned")
        resolved.append(mailbox_id)
    return tuple(resolved)


def _canonical_generator_input(
    trusted_session: object,
    mailbox_ids: tuple[str, ...],
) -> bytes:
    try:
        value = {
            "owners": [
                {
                    "issuer": trusted_session.issuer,
                    "authenticationVersion": (
                        trusted_session.authentication_version
                    ),
                    "subject": trusted_session.subject,
                    "mailboxes": list(mailbox_ids),
                }
            ]
        }
        return json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except Exception:
        raise AuthorityToolError("authority_context_invalid") from None


def _resolve_and_generate(
    selectors: ValidatedSelectors,
    *,
    environment: Mapping[str, str],
    session_credential: str,
    encoded_hmac_key: str,
    now: int,
    contracts: _AuthorityContracts,
) -> collaboration_allowlist.GeneratedAllowlists:
    cookie_name = contracts.session_store.SESSION_COOKIE_NAME
    try:
        contracts.session_store.build_session_cookie(session_credential)
    except Exception:
        raise AuthorityToolError("session_not_authenticated") from None
    raw_headers = (("cookie", f"{cookie_name}={session_credential}"),)
    try:
        resolution = contracts.runtime.resolve_authenticated_member_session(
            raw_headers,
            environment=environment,
            now=now,
        )
    except Exception:
        raise AuthorityToolError("session_authority_unavailable") from None

    if resolution.outcome is contracts.runtime.MemberResolutionOutcome.UNAUTHENTICATED:
        raise AuthorityToolError("session_not_authenticated")
    if (
        resolution.outcome
        is not contracts.runtime.MemberResolutionOutcome.AUTHENTICATED
    ):
        raise AuthorityToolError("session_authority_unavailable")
    trusted = resolution.session
    if type(trusted) is not contracts.runtime.AuthenticatedMemberSessionContext:
        raise AuthorityToolError("authority_context_invalid")
    member = trusted.member
    if (
        type(member) is not contracts.runtime.AuthenticatedMemberContext
        or member.auth_source != "auth0"
        or member.user_type != "member"
    ):
        raise AuthorityToolError("session_not_authenticated")

    mailbox_ids = _resolve_owned_mailbox_ids(
        contracts,
        raw_headers,
        member,
        selectors,
    )
    raw_generator_input = _canonical_generator_input(trusted, mailbox_ids)
    try:
        validated = collaboration_allowlist.parse_input(raw_generator_input)
        generated = collaboration_allowlist.generate_allowlists(
            validated,
            encoded_hmac_key,
        )
    except collaboration_allowlist.AllowlistToolError as error:
        code = (
            "invalid_hmac_key"
            if error.code == "invalid_hmac_key"
            else "generation_failed"
        )
        raise AuthorityToolError(code) from None
    except Exception:
        raise AuthorityToolError("generation_failed") from None
    if generated.owners != 1 or generated.mailboxes != len(mailbox_ids):
        raise AuthorityToolError("generation_failed")
    return generated


def _dry_run_output(selectors: ValidatedSelectors) -> str:
    mailbox_count = len(selectors.mailbox_ids)
    return "\n".join(
        (
            "validation: ok",
            "authorityContracts: available",
            "authorityAccess: not_executed",
            "owners: 1",
            f"mailboxes: {mailbox_count}",
            "ownerDigests: 1",
            f"mailboxDigests: {mailbox_count}",
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
    parser = _RedactedArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute-authority", action="store_true")

    input_stream = sys.stdin if stdin is None else stdin
    output_stream = sys.stdout if stdout is None else stdout
    error_stream = sys.stderr if stderr is None else stderr
    source = os.environ if environment is None else environment

    try:
        args = parser.parse_args(arguments)
        if not args.dry_run and not args.execute_authority:
            raise AuthorityToolError("execution_not_armed")
        if args.execute_authority:
            confirmation = _require_environment_value(
                source,
                AUTHORITY_CONFIRM_ENV,
                "confirmation_required",
            )
            if confirmation != AUTHORITY_CONFIRM_VALUE:
                raise AuthorityToolError("confirmation_required")

        selectors = parse_selectors(_read_selectors(input_stream))
        contracts = _load_authority_contracts()
        if args.dry_run:
            output_stream.write(_dry_run_output(selectors) + "\n")
            return 0

        session_credential = _require_environment_value(
            source,
            SESSION_COOKIE_ENV,
            "session_credential_missing",
        )
        encoded_hmac_key = _require_environment_value(
            source,
            collaboration_allowlist.HMAC_KEY_ENV,
            "missing_hmac_key",
        )
        generated = _resolve_and_generate(
            selectors,
            environment=source,
            session_credential=session_credential,
            encoded_hmac_key=encoded_hmac_key,
            now=int(time.time()),
            contracts=contracts,
        )
        output_stream.write(
            collaboration_allowlist._generated_output(generated) + "\n"
        )
        return 0
    except AuthorityToolError as error:
        error_stream.write(f"error: {error.code}\n")
        return 2
    except Exception:
        error_stream.write("error: internal_error\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "AUTHORITY_CONFIRM_ENV",
    "AUTHORITY_CONFIRM_VALUE",
    "AuthorityToolError",
    "MAX_SELECTED_MAILBOXES",
    "SESSION_COOKIE_ENV",
    "ValidatedSelectors",
    "main",
    "parse_selectors",
)
