from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import unittest
from enum import Enum
from types import SimpleNamespace
from unittest import mock

from . import (
    authorization,
    http_adapter,
    owner_authentication,
    owner_http,
    owner_rate_limit,
    owner_request_security,
)
from .owner_request_security import (
    OwnerSecurityError,
    VerifiedOwnerAuthentication,
    issue_owner_csrf_token,
    parse_owner_security_configuration,
    resolve_owner_request_context,
)


NOW = 1_800_000_000
ORIGIN = "https://app.cuevion.com"
OWNER_EMAIL = "owner@example.com"
WORKSPACE_ID = "wsp_" + ("w" * 22)
OTHER_WORKSPACE_ID = "wsp_" + ("x" * 22)
MAILBOX_ID = "primary.mailbox"
COLLABORATION_ID = "A" * 22
ISSUER = "https://cuevion.eu.auth0.com/"
SUBJECT = "auth0|0123456789abcdef"
SESSION_ID = base64.urlsafe_b64encode(b"s" * 32).rstrip(b"=").decode("ascii")
CREDENTIAL_DIGEST = base64.urlsafe_b64encode(
    hashlib.sha256(b"credential-binding").digest()
).rstrip(b"=").decode("ascii")
IDEMPOTENCY_KEY = base64.urlsafe_b64encode(b"i" * 32).rstrip(b"=").decode("ascii")
CSRF_KEY = b"owner-csrf-key-material-32-bytes!"
ALLOWLIST_KEY = b"owner-allowlist-material-32-bytes"
RATE_LIMIT_KEY = b"owner-rate-limit-material-32-bytes!"

_OWNER_DOMAIN = b"cuevion/collaboration-v2/owner-allowlist/v1\x00"
_MAILBOX_DOMAIN = b"cuevion/collaboration-v2/mailbox-allowlist/v1\x00"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _framed(domain: bytes, values: tuple[str, ...]) -> bytes:
    framed = bytearray(domain)
    for value in values:
        encoded = value.encode("ascii")
        framed.extend(len(encoded).to_bytes(4, "big"))
        framed.extend(encoded)
    return bytes(framed)


def _entry(domain: bytes, values: tuple[str, ...]) -> str:
    return "v1_" + _b64(
        hmac.new(ALLOWLIST_KEY, _framed(domain, values), hashlib.sha256).digest()
    )


def _environment(*, mailbox_id: str = MAILBOX_ID) -> dict[str, str]:
    return {
        "CUEVION_APP_ORIGIN": ORIGIN,
        "CUEVION_COLLAB_V2_OWNER_CSRF_KEY": _b64(CSRF_KEY),
        "CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY": _b64(ALLOWLIST_KEY),
        "CUEVION_COLLAB_V2_RATE_LIMIT_HMAC_KEY": _b64(RATE_LIMIT_KEY),
        "CUEVION_COLLAB_V2_OWNER_ALLOWLIST": _entry(
            _OWNER_DOMAIN,
            (ISSUER, "1", SUBJECT),
        ),
        "CUEVION_COLLAB_V2_MAILBOX_ALLOWLIST": _entry(
            _MAILBOX_DOMAIN,
            (ISSUER, "1", SUBJECT, mailbox_id),
        ),
    }


def _claims(**updates: object) -> VerifiedOwnerAuthentication:
    values: dict[str, object] = {
        "issuer": ISSUER,
        "authentication_version": 1,
        "subject": SUBJECT,
        "owner_email": OWNER_EMAIL,
        "workspace_id": WORKSPACE_ID,
        "display_name": "Owner Person",
        "session_id": SESSION_ID,
        "credential_digest": CREDENTIAL_DIGEST,
        "issued_at": NOW - 60,
        "expires_at": NOW + 3600,
    }
    values.update(updates)
    return VerifiedOwnerAuthentication(**values)  # type: ignore[arg-type]


def _context(**updates: object):
    claims = _claims(**updates)
    return resolve_owner_request_context(
        (),
        authentication_resolver=lambda _headers: claims,
        now=NOW,
    )


class _Headers:
    def __init__(self, pairs: list[tuple[str, str]]) -> None:
        self.pairs = pairs

    def raw_items(self):
        return iter(self.pairs)


class _GuardedReader:
    def read(self, _size: int) -> bytes:
        raise AssertionError("request body must not be read")


class _Request:
    def __init__(
        self,
        payload: bytes,
        *,
        method: str = "POST",
        headers: list[tuple[str, str]] | None = None,
        guarded_reader: bool = False,
    ) -> None:
        self.command = method
        self.headers = _Headers(
            headers
            if headers is not None
            else [
                ("Origin", ORIGIN),
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(payload))),
            ]
        )
        self.rfile = _GuardedReader() if guarded_reader else io.BytesIO(payload)
        self.wfile = io.BytesIO()
        self.status = None
        self.response_headers: list[tuple[str, str]] = []

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, name: str, value: str) -> None:
        self.response_headers.append((name, value))

    def end_headers(self) -> None:
        return


def _request(
    payload: dict,
    *,
    csrf: str | None = None,
    idempotency_key: str | None = None,
    **kwargs,
) -> _Request:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = [
        ("Origin", ORIGIN),
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(body))),
    ]
    if csrf is not None:
        headers.append(("X-Cuevion-CSRF", csrf))
    if idempotency_key is not None:
        headers.append(("X-Cuevion-Idempotency-Key", idempotency_key))
    return _Request(body, headers=headers, **kwargs)


def _invoke(request: _Request, *, mode: str = "owner_write"):
    return http_adapter.invoke_safely(
        lambda: owner_http.owner_response(
            request,
            http_mode=mode,
            environment=_environment(),
            now=NOW,
        ),
        allow_method="POST",
    )


def _json(response: http_adapter.PublicResponse) -> dict:
    return json.loads(response.body.decode("utf-8"))


class OwnerAuthenticationAdapterTests(unittest.TestCase):
    class Outcome(Enum):
        AUTHENTICATED = "authenticated"
        UNAUTHENTICATED = "unauthenticated"
        UNAVAILABLE = "unavailable"

    class Member:
        pass

    class Session:
        pass

    def _runtime_modules(self, outcome, *, resolution_session=None):
        runtime = SimpleNamespace(
            MemberResolutionOutcome=self.Outcome,
            AuthenticatedMemberContext=self.Member,
            AuthenticatedMemberSessionContext=self.Session,
            resolve_authenticated_member_session=mock.Mock(
                return_value=SimpleNamespace(
                    outcome=outcome,
                    session=resolution_session,
                )
            ),
        )
        modules = {
            "api.auth.http": SimpleNamespace(HttpBoundaryError=type("Boundary", (Exception,), {})),
            "api.auth.runtime": runtime,
            "api.auth.session_store": SimpleNamespace(
                build_runtime_session_store=lambda _environment: object()
            ),
            "api.auth.account_authority": SimpleNamespace(
                build_runtime_account_authority=lambda _environment: object()
            ),
        }
        return runtime, modules

    def test_valid_revalidated_session_mints_exact_owner_claims(self):
        member = self.Member()
        member.email = OWNER_EMAIL
        member.workspace_id = WORKSPACE_ID
        member.name = "Owner Person"
        member.auth_source = "auth0"
        member.user_type = "member"
        session = self.Session()
        session.member = member
        session.authentication_version = 1
        session.issuer = ISSUER
        session.subject = SUBJECT
        session.session_id = SESSION_ID
        session.credential_digest = CREDENTIAL_DIGEST
        session.issued_at = NOW - 60
        session.expires_at = NOW + 3600
        runtime, modules = self._runtime_modules(
            self.Outcome.AUTHENTICATED,
            resolution_session=session,
        )

        with mock.patch.object(
            owner_authentication.importlib,
            "import_module",
            side_effect=lambda name: modules[name],
        ):
            claims = owner_authentication.resolve_verified_auth0_owner(
                (("cookie", "opaque"),),
                environment={},
                now=NOW,
            )

        self.assertEqual(claims.owner_email, OWNER_EMAIL)
        self.assertEqual(claims.workspace_id, WORKSPACE_ID)
        self.assertEqual(claims.subject, SUBJECT)
        self.assertEqual(claims.session_id, SESSION_ID)
        self.assertEqual(claims.credential_digest, CREDENTIAL_DIGEST)
        self.assertEqual((claims.issued_at, claims.expires_at), (NOW - 60, NOW + 3600))
        runtime.resolve_authenticated_member_session.assert_called_once()

    def test_missing_session_and_unavailable_store_remain_distinct(self):
        for outcome, reason in (
            (self.Outcome.UNAUTHENTICATED, "authentication_required"),
            (self.Outcome.UNAVAILABLE, "authentication_unavailable"),
        ):
            _runtime, modules = self._runtime_modules(outcome)
            with self.subTest(outcome=outcome), mock.patch.object(
                owner_authentication.importlib,
                "import_module",
                side_effect=lambda name, modules=modules: modules[name],
            ):
                with self.assertRaises(OwnerSecurityError) as raised:
                    owner_authentication.resolve_verified_auth0_owner(
                        (), environment={}, now=NOW
                    )
                self.assertEqual(raised.exception.reason, reason)


class VerifiedOwnerAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = _context()
        self.configuration = parse_owner_security_configuration(
            owner_http._trusted_security_snapshot(_environment())
        )

    def _mailbox_result(self, member, *, email: str = OWNER_EMAIL):
        return {
            "status": "ok",
            "memberAuthority": member,
            "user": {"email": email, "name": "Owner Person"},
            "inbox": {"id": MAILBOX_ID, "provider": "google"},
            "config": {},
            "error": None,
        }

    def test_capability_uses_canonical_workspace_and_revalidated_mailbox_owner(self):
        class Member:
            pass

        member = Member()
        member.email = OWNER_EMAIL
        member.workspace_id = WORKSPACE_ID
        member.name = "Owner Person"
        member.auth_source = "auth0"
        member.user_type = "member"
        with mock.patch.object(
            authorization.importlib,
            "import_module",
            side_effect=lambda name: (
                owner_request_security
                if name == "api.collaboration.owner_request_security"
                else SimpleNamespace(AuthenticatedMemberContext=Member)
            ),
        ):
            result = authorization.resolve_verified_owner_collaboration_context(
                self.context,
                (("cookie", "opaque"),),
                MAILBOX_ID,
                required_action="create",
                owner_security_configuration=self.configuration,
                mailbox_resolver=lambda _headers, _mailbox: self._mailbox_result(member),
            )
        capability = result["context"]
        self.assertTrue(authorization._is_internal_capability(capability, actions={"create"}))
        self.assertEqual(capability.owner_email, OWNER_EMAIL)
        self.assertEqual(capability.workspace_id, WORKSPACE_ID)
        self.assertEqual(capability.mailbox_id, MAILBOX_ID)

    def test_foreign_workspace_thread_is_denied_before_mailbox_access(self):
        mailbox_resolver = mock.Mock(side_effect=AssertionError("must not resolve mailbox"))
        thread = {
            "v": 2,
            "collaborationId": COLLABORATION_ID,
            "ownerEmail": OWNER_EMAIL,
            "workspaceId": OTHER_WORKSPACE_ID,
            "mailboxId": MAILBOX_ID,
            "sourceRef": {"provider": "google", "providerMessageId": "message-1"},
            "sourceMessage": {
                "subject": "Review",
                "senderDisplay": "Sender",
                "fromDisplay": "sender@example.com",
                "timestamp": "today",
                "bodyText": "Body",
            },
            "state": "needs_review",
            "messages": [],
            "createdAt": NOW * 1000,
            "updatedAt": NOW * 1000,
        }
        result = authorization.resolve_verified_owner_collaboration_context(
            self.context,
            (),
            collaboration_id=COLLABORATION_ID,
            required_action="read",
            owner_security_configuration=self.configuration,
            mailbox_resolver=mailbox_resolver,
            thread_loader=lambda _collaboration_id: {
                "status": "ok",
                "record": thread,
            },
        )
        self.assertEqual(result["error"], {"code": "forbidden"})
        mailbox_resolver.assert_not_called()

    def test_unallowlisted_mailbox_fails_closed_before_mailbox_access(self):
        mailbox_resolver = mock.Mock(side_effect=AssertionError("must not resolve mailbox"))
        configuration = parse_owner_security_configuration(
            owner_http._trusted_security_snapshot(
                _environment(mailbox_id="other.mailbox")
            )
        )
        with self.assertRaises(OwnerSecurityError) as raised:
            authorization.resolve_verified_owner_collaboration_context(
                self.context,
                (),
                MAILBOX_ID,
                required_action="create",
                owner_security_configuration=configuration,
                mailbox_resolver=mailbox_resolver,
            )
        self.assertEqual(raised.exception.reason, "rollout_unavailable")
        mailbox_resolver.assert_not_called()


class OwnerHttpBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = _context()
        self.context_patch = mock.patch.object(
            owner_http,
            "_resolve_context",
            return_value=self.context,
        )
        self.context_patch.start()
        self.rate_limit_patch = mock.patch.object(
            owner_http.owner_rate_limit,
            "consume_owner_rate_limit",
            return_value=owner_rate_limit.OwnerRateLimitDecision("allowed"),
        )
        self.rate_limiter = self.rate_limit_patch.start()

    def tearDown(self) -> None:
        self.rate_limit_patch.stop()
        self.context_patch.stop()

    def _csrf(self) -> str:
        configuration = parse_owner_security_configuration(
            owner_http._trusted_security_snapshot(_environment())
        )
        return issue_owner_csrf_token(
            self.context,
            configuration,
            now=NOW,
        )[0]

    def test_csrf_bootstrap_requires_exact_origin_and_returns_session_token(self):
        response = _invoke(_request({"operation": "csrf"}))
        self.assertEqual(response.status, 200)
        data = _json(response)["data"]
        self.assertTrue(data["csrfToken"].startswith("oc1."))
        self.assertGreater(data["expiresAt"], NOW)

        for origins in ([], [("Origin", "https://evil.example")], [("Origin", ORIGIN), ("Origin", ORIGIN)]):
            body = b'{"operation":"csrf"}'
            request = _Request(
                body,
                headers=[
                    *origins,
                    ("Content-Type", "application/json"),
                    ("Content-Length", str(len(body))),
                ],
            )
            rejected = _invoke(request)
            self.assertIn(rejected.status, {400, 403})

    def test_mutations_require_same_session_csrf_and_valid_token_is_accepted(self):
        payload = {
            "operation": "append_shared",
            "collaborationId": COLLABORATION_ID,
            "text": "Approved reply",
        }
        self.assertEqual(_invoke(_request(payload)).status, 403)
        self.rate_limiter.assert_not_called()

        other_context = _context(session_id=_b64(b"z" * 32))
        with mock.patch.object(owner_http, "_resolve_context", return_value=other_context):
            self.assertEqual(_invoke(_request(payload, csrf=self._csrf())).status, 403)
        self.rate_limiter.assert_not_called()

        success = {
            "message": {
                "id": "M" * 22,
                "authorDisplayName": "Owner Person",
                "authorRole": "Cuevion user",
                "text": "Approved reply",
                "timestamp": NOW * 1000,
                "visibility": "shared",
            },
            "updatedAt": NOW * 1000,
        }
        with mock.patch.object(
            owner_http.application,
            "append_v2_shared_message_for_verified_owner",
            return_value=success,
        ) as service:
            accepted = _invoke(
                _request(
                    payload,
                    csrf=self._csrf(),
                    idempotency_key=IDEMPOTENCY_KEY,
                )
            )
        self.assertEqual(accepted.status, 200)
        service.assert_called_once()
        self.assertIs(service.call_args.args[0], self.context)
        self.assertEqual(
            service.call_args.kwargs["idempotency_key"],
            IDEMPOTENCY_KEY,
        )

    def test_owner_append_requires_one_canonical_idempotency_header(self):
        payload = {
            "operation": "append_internal",
            "collaborationId": COLLABORATION_ID,
            "text": "Private note",
        }
        service = mock.Mock(side_effect=AssertionError("service must not run"))
        with mock.patch.object(
            owner_http.application,
            "append_v2_internal_note_for_verified_owner",
            service,
        ):
            missing = _invoke(_request(payload, csrf=self._csrf()))
            self.assertEqual(missing.status, 400)
            for malformed in (
                "short",
                "A" * 44,
                ("A" * 42) + "!",
                ("A" * 42) + "B",
            ):
                with self.subTest(malformed=malformed):
                    rejected = _invoke(
                        _request(
                            payload,
                            csrf=self._csrf(),
                            idempotency_key=malformed,
                        )
                    )
                    self.assertEqual(rejected.status, 400)

            duplicate = _request(
                payload,
                csrf=self._csrf(),
                idempotency_key=IDEMPOTENCY_KEY,
            )
            duplicate.headers.pairs.append(
                ("x-cuevion-idempotency-key", IDEMPOTENCY_KEY)
            )
            self.assertEqual(_invoke(duplicate).status, 400)
        service.assert_not_called()

    def test_strict_json_headers_body_limit_and_unknown_fields_fail_before_service(self):
        service = mock.Mock(side_effect=AssertionError("service must not run"))
        csrf = self._csrf()
        cases = [
            _Request(
                b'{"operation":"read"}',
                headers=[
                    ("Origin", ORIGIN),
                    ("Content-Type", "application/json"),
                    ("Content-Type", "application/json"),
                    ("Content-Length", "20"),
                    ("X-Cuevion-CSRF", csrf),
                ],
            ),
            _Request(
                b'{"operation":"read","operation":"create"}',
                headers=[
                    ("Origin", ORIGIN),
                    ("Content-Type", "application/json"),
                    ("Content-Length", "46"),
                    ("X-Cuevion-CSRF", csrf),
                ],
            ),
            _request({"operation": "unknown"}, csrf=csrf),
            _request(
                {
                    "operation": "read",
                    "collaborationId": COLLABORATION_ID,
                    "workspaceId": OTHER_WORKSPACE_ID,
                },
                csrf=csrf,
            ),
        ]
        with mock.patch.object(
            owner_http.application,
            "read_v2_collaboration_for_verified_owner",
            service,
        ):
            for request in cases:
                with self.subTest(body=getattr(request.rfile, "getvalue", lambda: b"")()):
                    self.assertEqual(_invoke(request).status, 400)
        service.assert_not_called()
        self.rate_limiter.assert_not_called()

        oversized = _Request(
            b"",
            headers=[
                ("Origin", ORIGIN),
                ("Content-Type", "application/json"),
                ("Content-Length", str(owner_http.MAX_OWNER_REQUEST_BYTES + 1)),
            ],
            guarded_reader=True,
        )
        self.assertEqual(_invoke(oversized).status, 413)
        self.rate_limiter.assert_not_called()

    def test_read_and_create_use_only_verified_owner_services(self):
        csrf = self._csrf()
        collaboration = {
            "collaborationId": COLLABORATION_ID,
            "mailboxId": MAILBOX_ID,
            "state": "needs_review",
            "createdAt": NOW * 1000,
            "updatedAt": NOW * 1000,
            "source": {},
            "messages": [],
        }
        read_result = {"status": "ok", "collaboration": collaboration, "error": None}
        with mock.patch.object(
            owner_http.application,
            "read_v2_collaboration_for_verified_owner",
            return_value=read_result,
        ) as read_service:
            response = _invoke(
                _request(
                    {"operation": "read", "collaborationId": COLLABORATION_ID},
                    csrf=csrf,
                )
            )
        self.assertEqual(response.status, 200)
        self.assertIs(read_service.call_args.args[0], self.context)

        create_result = {"created": True, "collaboration": collaboration}
        with mock.patch.object(
            owner_http.application,
            "create_v2_collaboration_for_verified_owner",
            return_value=create_result,
        ) as create_service:
            response = _invoke(
                _request(
                    {
                        "operation": "create",
                        "mailboxId": MAILBOX_ID,
                        "sourceRef": {"providerMessageId": "gmail-message-1"},
                        "state": "needs_review",
                    },
                    csrf=csrf,
                )
            )
        self.assertEqual(response.status, 201)
        self.assertIs(create_service.call_args.args[0], self.context)

    def test_owner_operations_use_exact_shared_rate_limit_classes(self):
        self.rate_limiter.reset_mock()
        self.assertEqual(_invoke(_request({"operation": "csrf"})).status, 200)
        self.assertEqual(
            self.rate_limiter.call_args.args[1],
            owner_rate_limit.RATE_LIMIT_BOOTSTRAP,
        )

        csrf = self._csrf()
        collaboration = {
            "collaborationId": COLLABORATION_ID,
            "mailboxId": MAILBOX_ID,
            "state": "needs_review",
            "createdAt": NOW * 1000,
            "updatedAt": NOW * 1000,
            "source": {},
            "messages": [],
        }
        with mock.patch.object(
            owner_http.application,
            "read_v2_collaboration_for_verified_owner",
            return_value={
                "status": "ok",
                "collaboration": collaboration,
                "error": None,
            },
        ):
            self.assertEqual(
                _invoke(
                    _request(
                        {"operation": "read", "collaborationId": COLLABORATION_ID},
                        csrf=csrf,
                    )
                ).status,
                200,
            )
        self.assertEqual(
            self.rate_limiter.call_args.args[1],
            owner_rate_limit.RATE_LIMIT_READ,
        )

        with mock.patch.object(
            owner_http.application,
            "create_v2_collaboration_for_verified_owner",
            return_value={"created": True, "collaboration": collaboration},
        ):
            self.assertEqual(
                _invoke(
                    _request(
                        {
                            "operation": "create",
                            "mailboxId": MAILBOX_ID,
                            "sourceRef": {
                                "providerMessageId": "gmail-message-1"
                            },
                            "state": "needs_review",
                        },
                        csrf=csrf,
                    )
                ).status,
                201,
            )
        self.assertEqual(
            self.rate_limiter.call_args.args[1],
            owner_rate_limit.RATE_LIMIT_WRITE,
        )

        append_result = {
            "message": {
                "id": "M" * 22,
                "authorDisplayName": "Owner Person",
                "authorRole": "Cuevion user",
                "text": "Bounded",
                "timestamp": NOW * 1000,
                "visibility": "shared",
            },
            "updatedAt": NOW * 1000,
        }
        for operation, service_name in (
            ("append_shared", "append_v2_shared_message_for_verified_owner"),
            ("append_internal", "append_v2_internal_note_for_verified_owner"),
        ):
            with self.subTest(operation=operation), mock.patch.object(
                owner_http.application,
                service_name,
                return_value=append_result,
            ):
                response = _invoke(
                    _request(
                        {
                            "operation": operation,
                            "collaborationId": COLLABORATION_ID,
                            "text": "Bounded",
                        },
                        csrf=csrf,
                        idempotency_key=IDEMPOTENCY_KEY,
                    )
                )
            self.assertEqual(response.status, 200)
            self.assertEqual(
                self.rate_limiter.call_args.args[1],
                owner_rate_limit.RATE_LIMIT_WRITE,
            )

    def test_rate_limited_and_unavailable_decisions_are_publicly_safe(self):
        payload = {
            "operation": "append_shared",
            "collaborationId": COLLABORATION_ID,
            "text": "Only once",
        }
        service = mock.Mock(side_effect=AssertionError("mutation must not run"))
        with mock.patch.object(
            owner_http.application,
            "append_v2_shared_message_for_verified_owner",
            service,
        ):
            self.rate_limiter.return_value = owner_rate_limit.OwnerRateLimitDecision(
                "limited",
                2,
            )
            limited = _invoke(
                _request(
                    payload,
                    csrf=self._csrf(),
                    idempotency_key=IDEMPOTENCY_KEY,
                )
            )
            self.assertEqual(limited.status, 429)
            self.assertEqual(_json(limited), {"ok": False, "error": {"code": "rate_limited"}})
            self.assertIn(("Retry-After", "2"), limited.headers)

            self.rate_limiter.return_value = owner_rate_limit.OwnerRateLimitDecision(
                "unavailable"
            )
            unavailable = _invoke(
                _request(
                    payload,
                    csrf=self._csrf(),
                    idempotency_key=IDEMPOTENCY_KEY,
                )
            )
            self.assertEqual(unavailable.status, 503)
            self.assertEqual(
                _json(unavailable),
                {"ok": False, "error": {"code": "service_unavailable"}},
            )

            for malformed_retry in (None, 0, 61, "2", True):
                with self.subTest(malformed_retry=malformed_retry):
                    self.rate_limiter.return_value = (
                        owner_rate_limit.OwnerRateLimitDecision(
                            "limited",
                            malformed_retry,  # type: ignore[arg-type]
                        )
                    )
                    malformed = _invoke(
                        _request(
                            payload,
                            csrf=self._csrf(),
                            idempotency_key=IDEMPOTENCY_KEY,
                        )
                    )
                    self.assertEqual(malformed.status, 503)
                    self.assertEqual(
                        _json(malformed),
                        {
                            "ok": False,
                            "error": {"code": "service_unavailable"},
                        },
                    )
        service.assert_not_called()

    def test_read_mode_limits_bootstrap_and_read_but_never_activates_writes(self):
        self.rate_limiter.return_value = owner_rate_limit.OwnerRateLimitDecision(
            "limited",
            1,
        )
        self.assertEqual(
            _invoke(_request({"operation": "csrf"}), mode="owner_read").status,
            429,
        )
        self.rate_limiter.reset_mock()
        self.assertEqual(
            _invoke(
                _request(
                    {"operation": "read", "collaborationId": COLLABORATION_ID},
                    csrf=self._csrf(),
                ),
                mode="owner_read",
            ).status,
            429,
        )
        self.assertEqual(
            self.rate_limiter.call_args.args[1],
            owner_rate_limit.RATE_LIMIT_READ,
        )
        self.rate_limiter.reset_mock()
        self.assertEqual(
            _invoke(
                _request(
                    {
                        "operation": "append_internal",
                        "collaborationId": COLLABORATION_ID,
                        "text": "No write",
                    },
                    csrf=self._csrf(),
                    idempotency_key=IDEMPOTENCY_KEY,
                ),
                mode="owner_read",
            ).status,
            404,
        )
        self.rate_limiter.assert_not_called()

    def test_missing_rate_configuration_and_untrusted_requests_fail_before_limiter(self):
        environment = _environment()
        environment.pop(owner_rate_limit.RATE_LIMIT_HMAC_ENV)
        response = http_adapter.invoke_safely(
            lambda: owner_http.owner_response(
                _request({"operation": "csrf"}),
                http_mode="owner_write",
                environment=environment,
                now=NOW,
            ),
            allow_method="POST",
        )
        self.assertEqual(response.status, 503)
        self.rate_limiter.assert_not_called()

        substituted = _environment()
        substituted[owner_rate_limit.RATE_LIMIT_HMAC_ENV] = substituted[
            "CUEVION_COLLAB_V2_OWNER_CSRF_KEY"
        ]
        response = http_adapter.invoke_safely(
            lambda: owner_http.owner_response(
                _request({"operation": "csrf"}),
                http_mode="owner_write",
                environment=substituted,
                now=NOW,
            ),
            allow_method="POST",
        )
        self.assertEqual(response.status, 503)
        self.rate_limiter.assert_not_called()

        wrong_origin = _request({"operation": "csrf"})
        wrong_origin.headers.pairs[0] = ("Origin", "https://evil.example")
        self.assertEqual(_invoke(wrong_origin).status, 403)
        self.rate_limiter.assert_not_called()

        self.assertEqual(
            _invoke(
                _request(
                    {
                        "operation": "read",
                        "collaborationId": COLLABORATION_ID,
                        "workspaceId": OTHER_WORKSPACE_ID,
                    },
                    csrf=self._csrf(),
                )
            ).status,
            400,
        )
        self.rate_limiter.assert_not_called()

    def test_authentication_failures_are_fixed_and_read_mode_cannot_mutate(self):
        for reason, status in (
            ("authentication_required", 401),
            ("authentication_unavailable", 503),
        ):
            with self.subTest(reason=reason), mock.patch.object(
                owner_http,
                "_resolve_context",
                side_effect=OwnerSecurityError(reason),
            ):
                self.assertEqual(_invoke(_request({"operation": "csrf"})).status, status)

        response = _invoke(
            _request(
                {
                    "operation": "append_internal",
                    "collaborationId": COLLABORATION_ID,
                    "text": "Private note",
                },
                csrf=self._csrf(),
            ),
            mode="owner_read",
        )
        self.assertEqual(response.status, 404)
        self.rate_limiter.assert_not_called()

    def test_non_allowlisted_owner_is_rejected_without_consuming_budget(self):
        with mock.patch.object(owner_http, "owner_is_allowlisted", return_value=False):
            response = _invoke(_request({"operation": "csrf"}))

        self.assertEqual(response.status, 404)
        self.rate_limiter.assert_not_called()


class OwnerRouteActivationTests(unittest.TestCase):
    def test_activation_off_does_not_import_owner_services_or_read_body(self):
        from . import owner

        request = _Request(b"", guarded_reader=True)
        with mock.patch.dict(owner.os.environ, {}, clear=True), mock.patch.object(
            owner.importlib,
            "import_module",
            side_effect=AssertionError("disabled route must not import services"),
        ):
            owner.handler._respond(request)
        self.assertEqual(request.status, 404)

    def test_active_route_rejects_every_non_post_method_before_body_read(self):
        from . import owner

        request = _Request(b"", method="GET", guarded_reader=True)
        with mock.patch.dict(
            owner.os.environ,
            {http_adapter.HTTP_MODE_ENVIRONMENT_NAME: "owner_read"},
            clear=True,
        ):
            owner.handler._respond(request)
        self.assertEqual(request.status, 405)
        self.assertIn(("Allow", "POST"), request.response_headers)


if __name__ == "__main__":
    unittest.main()
