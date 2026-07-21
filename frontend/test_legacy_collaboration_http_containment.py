from __future__ import annotations

import io
import os
import subprocess
import sys
import textwrap
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from api.collaboration import invite as collaboration_invite
from api.collaboration import redis_store as collaboration_store
from api.collaboration import thread as collaboration_thread
from api.team import invite as team_invite
from api.team import members as team_members


MODE_ENVIRONMENT_NAME = "CUEVION_LEGACY_COLLAB_V1_HTTP_MODE"
UNSAFE_MODE = "legacy_unsafe_on"
DISABLED_BODY = b'{"ok":false,"error":{"code":"not_found","message":"Not found."}}'
DISABLED_HEADERS = [
    ("Content-Type", "application/json"),
    ("Cache-Control", "no-store"),
    ("Content-Length", "64"),
]
UNSUPPORTED_BODY = (
    b'{"ok":false,"error":{"code":"not_implemented","message":"Unsupported method."}}'
)
UNSUPPORTED_HEADERS = [
    ("Content-Type", "application/json"),
    ("Cache-Control", "no-store"),
    ("Content-Length", "79"),
]
METHODS = ("GET", "POST", "HEAD", "OPTIONS", "PUT", "PATCH", "DELETE")
UNSUPPORTED_METHODS = ("HEAD", "OPTIONS", "PUT", "PATCH", "DELETE")
ROUTES = (
    ("collaboration.thread", collaboration_thread),
    ("collaboration.invite", collaboration_invite),
    ("team.invite", team_invite),
    ("team.members", team_members),
)
FRONTEND_ROOT = Path(__file__).resolve().parent


class _ForbiddenRequestAccess(AssertionError):
    pass


class _ResponseFailure(RuntimeError):
    pass


class _ExplosiveString(str):
    def __eq__(self, other):
        raise AssertionError("a non-built-in string was compared")


class _ExplosiveValue:
    def __eq__(self, other):
        raise AssertionError("a non-string environment value was compared")

    def __str__(self):
        raise AssertionError("an environment value was coerced")


class _RecordingWriter:
    def __init__(self, failure: Exception | None = None):
        self.writes: list[bytes] = []
        self.failure = failure

    def write(self, value: bytes):
        self.writes.append(value)
        if self.failure is not None:
            raise self.failure

    def getvalue(self) -> bytes:
        return b"".join(self.writes)


def _controlled_handler(route, *, fail_at: str | None = None):
    failure = _ResponseFailure(fail_at or "response failure")

    class ControlledHandler(route.handler):
        def __init__(self):
            self.status_only: list[int] = []
            self.status_normal: list[int] = []
            self.status_errors: list[int] = []
            self.response_headers: list[tuple[str, str]] = []
            self.end_headers_calls = 0
            self.wfile = _RecordingWriter(failure if fail_at == "write" else None)

        @property
        def path(self):
            raise _ForbiddenRequestAccess("path was accessed")

        @property
        def headers(self):
            raise _ForbiddenRequestAccess("headers were accessed")

        @property
        def rfile(self):
            raise _ForbiddenRequestAccess("rfile was accessed")

        @property
        def command(self):
            raise _ForbiddenRequestAccess("command was accessed")

        @property
        def requestline(self):
            raise _ForbiddenRequestAccess("requestline was accessed")

        def send_response_only(self, status_code, message=None):
            self.status_only.append(status_code)
            if fail_at == "status":
                raise failure

        def send_response(self, status_code, message=None):
            self.status_normal.append(status_code)

        def send_error(self, status_code, *args, **kwargs):
            self.status_errors.append(status_code)

        def send_header(self, name, value):
            self.response_headers.append((name, value))
            header_number = len(self.response_headers)
            if fail_at == f"header-{header_number}":
                raise failure

        def end_headers(self):
            self.end_headers_calls += 1
            if fail_at == "end-headers":
                raise failure

        def log_request(self, *args, **kwargs):
            raise AssertionError("request information was logged")

        def log_message(self, *args, **kwargs):
            raise AssertionError("request information was logged")

    return ControlledHandler()


def _invoke_direct(route, method: str, *, fail_at: str | None = None):
    request_handler = _controlled_handler(route, fail_at=fail_at)
    getattr(request_handler, f"do_{method}")()
    return request_handler


def _dispatch_raw_http(
    route,
    method: str,
    path: str,
    body: bytes,
    *,
    extra_headers: tuple[tuple[str, str], ...] = (),
):
    request_handler = object.__new__(route.handler)
    header_lines = [
        f"{method} {path} HTTP/1.1",
        "Host: containment.invalid",
        f"Content-Length: {len(body)}",
        "Cookie: cuevion_beta_session=forged-victim-cookie",
        *(f"{name}: {value}" for name, value in extra_headers),
        "",
        "",
    ]
    raw_request = "\r\n".join(header_lines).encode("ascii") + body
    request_handler.rfile = io.BytesIO(raw_request)
    request_handler.wfile = io.BytesIO()
    request_handler.client_address = ("127.0.0.1", 0)
    request_handler.close_connection = True
    request_handler.handle_one_request()
    response = request_handler.wfile.getvalue()
    unread_body = request_handler.rfile.read()
    return response, unread_body


def _parse_raw_response(raw_response: bytes):
    raw_headers, body = raw_response.split(b"\r\n\r\n", 1)
    lines = raw_headers.decode("iso-8859-1").split("\r\n")
    status = int(lines[0].split(" ", 2)[1])
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        name, value = line.split(":", 1)
        headers.append((name, value.strip()))
    return status, headers, body, raw_headers


class LegacyCollaborationHttpContainmentTests(unittest.TestCase):
    def assert_disabled_direct(self, request_handler, *, method: str):
        self.assertEqual(request_handler.status_only, [404])
        self.assertEqual(request_handler.status_normal, [])
        self.assertEqual(request_handler.status_errors, [])
        self.assertEqual(request_handler.response_headers, DISABLED_HEADERS)
        self.assertEqual(request_handler.end_headers_calls, 1)
        expected_writes = [] if method == "HEAD" else [DISABLED_BODY]
        self.assertEqual(request_handler.wfile.writes, expected_writes)

    def test_configuration_is_exact_fail_closed_and_evaluated_per_request(self):
        disabled_values = (
            None,
            "",
            "off",
            "unknown",
            " legacy_unsafe_on",
            "legacy_unsafe_on ",
            "\tlegacy_unsafe_on",
            "legacy_unsafe_on\n",
            "LEGACY_UNSAFE_ON",
            "Legacy_Unsafe_On",
        )
        for route_name, route in ROUTES:
            for value in disabled_values:
                with self.subTest(route=route_name, value=value), patch.dict(os.environ, {}, clear=True):
                    if value is not None:
                        os.environ[MODE_ENVIRONMENT_NAME] = value
                    request_handler = _invoke_direct(route, "GET")
                    self.assert_disabled_direct(request_handler, method="GET")

            with self.subTest(route=route_name, value=UNSAFE_MODE), patch.dict(
                os.environ, {MODE_ENVIRONMENT_NAME: UNSAFE_MODE}, clear=True
            ):
                self.assertTrue(route._legacy_http_is_enabled())

        with patch.dict(os.environ, {MODE_ENVIRONMENT_NAME: UNSAFE_MODE}, clear=True):
            request_handler = _controlled_handler(collaboration_thread)
            collaboration_thread.handler.do_GET(request_handler)
        self.assertEqual(request_handler.status_only, [])
        self.assertEqual(request_handler.status_normal, [405])
        self.assertNotEqual(request_handler.wfile.getvalue(), DISABLED_BODY)

        for route_name, route in ROUTES:
            with self.subTest(route=route_name, behavior="per-request"), patch.dict(
                os.environ, {}, clear=True
            ):
                first = _invoke_direct(route, "GET")
                os.environ[MODE_ENVIRONMENT_NAME] = UNSAFE_MODE
                self.assertTrue(route._legacy_http_is_enabled())
                os.environ.pop(MODE_ENVIRONMENT_NAME)
                third = _invoke_direct(route, "GET")
            self.assert_disabled_direct(first, method="GET")
            self.assert_disabled_direct(third, method="GET")

    def test_configuration_requires_an_exact_builtin_string(self):
        values = (_ExplosiveString(UNSAFE_MODE), _ExplosiveValue(), 1, True, b"legacy_unsafe_on")
        for route_name, route in ROUTES:
            for value in values:
                with self.subTest(route=route_name, value_type=type(value).__name__), patch.object(
                    route.os, "getenv", return_value=value
                ):
                    request_handler = _invoke_direct(route, "GET")
                    self.assert_disabled_direct(request_handler, method="GET")

    def test_environment_read_exceptions_fail_closed_without_catching_base_exception(self):
        for route_name, route in ROUTES:
            with self.subTest(route=route_name, exception="RuntimeError"), patch.object(
                route.os, "getenv", side_effect=RuntimeError("environment unavailable")
            ):
                request_handler = _invoke_direct(route, "GET")
                self.assert_disabled_direct(request_handler, method="GET")

            for exception_type in (KeyboardInterrupt, SystemExit):
                with self.subTest(route=route_name, exception=exception_type.__name__), patch.object(
                    route.os, "getenv", side_effect=exception_type()
                ), self.assertRaises(exception_type):
                    _invoke_direct(route, "GET")

    def test_disabled_wire_contract_is_uniform_for_every_route_and_method(self):
        body = b'{"private":"request bytes must remain unread"}'
        non_head_response = None
        head_headers = None
        with patch.dict(os.environ, {}, clear=True):
            for route_name, route in ROUTES:
                for method in METHODS:
                    with self.subTest(route=route_name, method=method):
                        self.assertIn(f"do_{method}", route.handler.__dict__)
                        raw_response, unread_body = _dispatch_raw_http(
                            route,
                            method,
                            "/api/private?op=action&token=raw-secret&viewer=external",
                            body,
                        )
                        status, headers, response_body, raw_headers = _parse_raw_response(raw_response)
                        self.assertEqual(status, 404)
                        self.assertEqual(headers, DISABLED_HEADERS)
                        self.assertEqual(len(headers), len({name.lower() for name, _ in headers}))
                        self.assertNotIn(b"Access-Control-Allow-Origin", raw_headers)
                        self.assertNotIn(b"Set-Cookie", raw_headers)
                        self.assertNotIn(b"Server", raw_headers)
                        self.assertNotIn(b"Date", raw_headers)
                        self.assertEqual(unread_body, body)

                        if method == "HEAD":
                            self.assertEqual(response_body, b"")
                            self.assertIsNotNone(head_headers or raw_headers)
                            if head_headers is None:
                                head_headers = raw_headers
                            self.assertEqual(raw_headers, head_headers)
                        else:
                            self.assertEqual(response_body, DISABLED_BODY)
                            if non_head_response is None:
                                non_head_response = raw_response
                            self.assertEqual(raw_response, non_head_response)

        self.assertIsNotNone(non_head_response)
        self.assertIsNotNone(head_headers)
        _, _, _, non_head_headers = _parse_raw_response(non_head_response)
        self.assertEqual(head_headers, non_head_headers)

    def test_disabled_gate_does_not_inspect_request_objects(self):
        with patch.dict(os.environ, {}, clear=True):
            for route_name, route in ROUTES:
                for method in METHODS:
                    with self.subTest(route=route_name, method=method):
                        request_handler = _invoke_direct(route, method)
                        self.assert_disabled_direct(request_handler, method=method)

    def test_disabled_gate_calls_no_parsing_authentication_storage_or_transport(self):
        entry_points = {
            "collaboration.thread": (
                "_send_json",
                "_build_error",
                "_get_operation",
                "_read_json_body",
                "_require_authenticated_member",
                "_workspace_is_authorized",
                "_resolve_thread_for_lookup_record",
                "_handle_get_many",
                "_handle_get_participant",
                "_handle_create",
                "_handle_action",
                "resolve_authenticated_member",
                "snapshot_request_headers",
                "normalize_auth_email",
                "get_thread",
                "get_threads_many",
                "get_participant_threads",
                "create_thread_if_missing",
                "save_thread_if_expected",
                "parse_qs",
                "urlsplit",
            ),
            "collaboration.invite": (
                "_send_json",
                "_build_error",
                "_get_query",
                "_get_operation",
                "_get_token",
                "_read_json_body",
                "_resolve_viewer",
                "_resolve_active_invite_and_thread",
                "_build_external_thread_payload",
                "_handle_issue",
                "_handle_lookup",
                "_handle_action",
                "get_invite",
                "get_thread",
                "issue_invite_for_thread",
                "save_thread",
                "save_thread_if_expected",
                "parse_qs",
                "urlsplit",
            ),
            "team.invite": (
                "_send_json",
                "_build_error",
                "_get_query",
                "_get_operation",
                "_get_token",
                "_read_json_body",
                "_resolve_durable_store_config",
                "_perform_rest_request",
                "_read_durable_record",
                "_read_durable_value",
                "_write_durable_record",
                "_get_invite",
                "_get_workspace_invite",
                "_save_invite",
                "_save_membership_for_accepted_invite",
                "_handle_issue",
                "_handle_lookup",
                "_handle_action",
                "parse_qs",
                "urlsplit",
                "urlopen",
            ),
            "team.members": (
                "_send_json",
                "_build_error",
                "_get_query",
                "_get_operation",
                "_get_workspace_id",
                "_read_json_body",
                "_require_authenticated_member",
                "_resolve_durable_store_config",
                "_perform_rest_request",
                "_read_durable_value",
                "_read_durable_record",
                "_write_durable_record",
                "_list_team_members",
                "_remove_team_member",
                "_handle_list",
                "_handle_remove",
                "resolve_authenticated_member",
                "snapshot_request_headers",
                "is_valid_auth_email",
                "parse_qs",
                "urlsplit",
                "urlopen",
            ),
        }

        for route_name, route in ROUTES:
            with self.subTest(route=route_name), ExitStack() as stack:
                forbidden_calls = []
                stack.enter_context(patch.object(route.os, "getenv", return_value=None))
                for name in entry_points[route_name]:
                    mocked = stack.enter_context(
                        patch.object(route, name, side_effect=AssertionError(f"{route_name}.{name} called"))
                    )
                    forbidden_calls.append(mocked)
                for json_method in ("loads", "dumps"):
                    mocked = stack.enter_context(
                        patch.object(
                            route.json,
                            json_method,
                            side_effect=AssertionError(f"{route_name}.json.{json_method} called"),
                        )
                    )
                    forbidden_calls.append(mocked)
                transport = stack.enter_context(
                    patch.object(
                        collaboration_store,
                        "urlopen",
                        side_effect=AssertionError("collaboration store transport called"),
                    )
                )
                forbidden_calls.append(transport)
                store_command_transport = stack.enter_context(
                    patch.object(
                        collaboration_store,
                        "_perform_rest_request",
                        side_effect=AssertionError("collaboration store command transport called"),
                    )
                )
                forbidden_calls.append(store_command_transport)

                for method in ("GET", "POST"):
                    request_handler = _invoke_direct(route, method)
                    self.assert_disabled_direct(request_handler, method=method)
                for mocked in forbidden_calls:
                    mocked.assert_not_called()

    def test_enabled_member_routes_reject_legacy_cookie_before_storage(self):
        cases = (
            (
                team_members,
                "GET",
                "/api/team/members?op=list&workspaceId=workspace-a",
                b"",
                "_list_team_members",
            ),
            (
                collaboration_thread,
                "POST",
                "/api/collaboration/thread?op=get-many",
                b'{"workspaceId":"workspace-a","messageIds":[]}',
                "get_threads_many",
            ),
        )
        with patch.dict(os.environ, {MODE_ENVIRONMENT_NAME: UNSAFE_MODE}, clear=True):
            for route, method, path, body, storage_name in cases:
                with self.subTest(route=route.__name__), patch.object(
                    route,
                    storage_name,
                    side_effect=AssertionError("storage called without Auth0 member authority"),
                ) as storage:
                    raw_response, _unread_body = _dispatch_raw_http(
                        route,
                        method,
                        path,
                        body,
                    )
                status, _headers, response_body, _raw_headers = _parse_raw_response(raw_response)
                self.assertEqual(status, 401)
                self.assertIn(b'"code": "unauthorized"', response_body)
                storage.assert_not_called()

    def test_enabled_member_routes_use_canonical_auth0_member_context(self):
        member = SimpleNamespace(
            user_id="user-a",
            email="owner@example.test",
            name="Owner",
            workspace_id="workspace-a",
            membership_role="owner",
            user_type="member",
            auth_source="auth0",
        )
        resolution = SimpleNamespace(
            outcome=team_members.MemberResolutionOutcome.AUTHENTICATED,
            member=member,
            set_cookies=(),
        )
        with patch.dict(os.environ, {MODE_ENVIRONMENT_NAME: UNSAFE_MODE}, clear=True):
            with patch.object(
                team_members,
                "resolve_authenticated_member",
                return_value=resolution,
            ) as resolve_member, patch.object(
                team_members,
                "_list_team_members",
                return_value=([], None),
            ) as list_members:
                raw_response, _unread_body = _dispatch_raw_http(
                    team_members,
                    "GET",
                    "/api/team/members?op=list&workspaceId=workspace-a",
                    b"",
                )
            status, _headers, _body, _raw_headers = _parse_raw_response(raw_response)
            self.assertEqual(status, 200)
            resolve_member.assert_called_once()
            list_members.assert_called_once_with("workspace-a")

            with patch.object(
                collaboration_thread,
                "resolve_authenticated_member",
                return_value=resolution,
            ) as resolve_member, patch.object(
                collaboration_thread,
                "get_participant_threads",
                return_value=([], None),
            ) as get_threads:
                body = b'{"workspaceId":"workspace-a","participantEmail":"owner@example.test"}'
                raw_response, _unread_body = _dispatch_raw_http(
                    collaboration_thread,
                    "POST",
                    "/api/collaboration/thread?op=get-participant",
                    body,
                )
            status, _headers, _body, _raw_headers = _parse_raw_response(raw_response)
            self.assertEqual(status, 200)
            resolve_member.assert_called_once()
            get_threads.assert_called_once_with(
                "owner@example.test",
                workspace_id="workspace-a",
            )

    def test_enabled_collaboration_actions_use_canonical_member_actor(self):
        member = SimpleNamespace(
            user_id="usr-canonical",
            email="owner@example.test",
            name="Canonical Owner",
            workspace_id="workspace-a",
            membership_role="owner",
        )
        current_thread = {
            "isShared": True,
            "collaboration": {
                "state": "needs_review",
                "messages": [],
            },
        }

        def invoke(action):
            request_handler = _controlled_handler(collaboration_thread)
            with patch.object(
                collaboration_thread,
                "get_thread",
                return_value=current_thread,
            ), patch.object(
                collaboration_thread,
                "normalize_collaboration_thread_record",
                side_effect=lambda value: value,
            ), patch.object(
                collaboration_thread,
                "save_thread_if_expected",
                side_effect=lambda value, **_kwargs: (value, None),
            ) as save_thread, patch.object(
                collaboration_thread,
                "time",
                return_value=123.0,
            ):
                collaboration_thread._handle_action(
                    request_handler,
                    {
                        "workspaceId": "workspace-a",
                        "messageId": "message-a",
                        "action": action,
                    },
                    member,
                )
            self.assertEqual(request_handler.status_normal, [200])
            return save_thread.call_args.args[0]

        replied = invoke(
            {
                "type": "reply",
                "authorId": "forged-user",
                "authorName": "Forged Name",
                "text": "Canonical actor only",
                "visibility": "shared",
            }
        )
        reply = replied["collaboration"]["messages"][-1]
        self.assertEqual(reply["authorId"], member.user_id)
        self.assertEqual(reply["authorName"], member.name)

        resolved = invoke(
            {
                "type": "resolve",
                "resolvedByUserId": "forged-user",
                "resolvedByUserName": "Forged Name",
            }
        )
        self.assertEqual(resolved["collaboration"]["resolvedByUserId"], member.user_id)
        self.assertEqual(resolved["collaboration"]["resolvedByUserName"], member.name)

    def test_disabled_responses_create_no_operation_or_existence_oracle(self):
        scenarios = (
            (collaboration_thread, "POST", "/api/collaboration/thread?op=get-many", b'{"messageIds":["existing-thread"]}'),
            (collaboration_thread, "POST", "/api/collaboration/thread?op=get-many", b'{"messageIds":["missing-thread"]}'),
            (collaboration_thread, "POST", "/api/collaboration/thread?op=get-participant", b'{"email":"person@example.invalid"}'),
            (collaboration_thread, "POST", "/api/collaboration/thread?op=create", b'{"workspaceId":"workspace-existing"}'),
            (collaboration_thread, "POST", "/api/collaboration/thread?op=action", b'{"action":"share"}'),
            (collaboration_thread, "POST", "/api/collaboration/thread?op=unknown", b"{"),
            (collaboration_thread, "POST", "/api/collaboration/thread", b"not-json"),
            (collaboration_invite, "GET", "/api/collaboration/invite?op=lookup&token=valid-looking-token", b"viewer-absent"),
            (collaboration_invite, "GET", "/api/collaboration/invite?op=lookup&token=valid-looking-token&viewer=external", b"viewer-external"),
            (collaboration_invite, "GET", "/api/collaboration/invite?op=lookup&token=valid-looking-token&viewer=workspace", b"viewer-workspace"),
            (collaboration_invite, "GET", "/api/collaboration/invite?op=lookup&token=malformed%00&viewer=unknown", b"viewer-unknown"),
            (collaboration_invite, "POST", "/api/collaboration/invite?op=issue", b'{"messageId":"existing-thread"}'),
            (collaboration_invite, "POST", "/api/collaboration/invite?op=action", b'{"token":"raw-token","action":"comment"}'),
            (collaboration_invite, "POST", "/api/collaboration/invite?op=unknown", b"not-json"),
            (collaboration_invite, "POST", "/api/collaboration/invite", b"{}"),
            (team_invite, "GET", "/api/team/invite?op=lookup&token=existing-team-invite", b"existing-team-invite"),
            (team_invite, "GET", "/api/team/invite?op=lookup&token=missing-team-invite", b"missing-team-invite"),
            (team_invite, "POST", "/api/team/invite?op=issue", b'{"workspaceId":"workspace-existing"}'),
            (team_invite, "POST", "/api/team/invite?op=action", b'{"token":"team-token","action":"accept"}'),
            (team_invite, "POST", "/api/team/invite?op=unknown", b"{"),
            (team_invite, "POST", "/api/team/invite", b"{}"),
            (team_members, "GET", "/api/team/members?op=list&workspaceId=workspace-existing", b"existing-workspace"),
            (team_members, "GET", "/api/team/members?op=list&workspaceId=workspace-missing", b"missing-workspace"),
            (team_members, "POST", "/api/team/members?op=remove&workspaceId=workspace-existing", b'{"email":"existing-member@example.invalid"}'),
            (team_members, "POST", "/api/team/members?op=revoke&workspaceId=workspace-existing", b'{"email":"missing-member@example.invalid"}'),
            (team_members, "POST", "/api/team/members?op=unknown", b"not-json"),
            (team_members, "POST", "/api/team/members", b"{}"),
        )
        expected_response = None
        with patch.dict(os.environ, {}, clear=True):
            for index, (route, method, path, body) in enumerate(scenarios):
                with self.subTest(index=index, route=route.__name__, method=method):
                    raw_response, unread_body = _dispatch_raw_http(route, method, path, body)
                    self.assertEqual(unread_body, body)
                    if expected_response is None:
                        expected_response = raw_response
                    self.assertEqual(raw_response, expected_response)

    def test_disabled_response_cannot_disclose_internal_thread_data(self):
        markers = (
            "INTERNAL_PRIVATE_NOTE_7e1c",
            "SHARED_MESSAGE_528f",
            "INTERNAL_PREVIEW_942a",
            "participant-pii@example.invalid",
            "RAW_EXTERNAL_REVIEW_TOKEN_8c44",
        )
        fake_canonical_thread = {
            "messages": [
                {"visibility": "internal", "body": markers[0]},
                {"visibility": "shared", "body": markers[1]},
            ],
            "preview": markers[2],
            "participants": [{"email": markers[3]}],
            "externalReviewToken": markers[4],
        }
        storage_calls = []
        with ExitStack() as stack:
            stack.enter_context(patch.object(collaboration_thread.os, "getenv", return_value=None))
            for name in (
                "get_thread",
                "get_threads_many",
                "get_participant_threads",
                "create_thread_if_missing",
                "save_thread_if_expected",
            ):
                mocked = stack.enter_context(
                    patch.object(
                        collaboration_thread,
                        name,
                        side_effect=AssertionError(f"storage exposed {fake_canonical_thread!r}"),
                    )
                )
                storage_calls.append(mocked)
            request_handler = _invoke_direct(collaboration_thread, "POST")

        self.assert_disabled_direct(request_handler, method="POST")
        for mocked in storage_calls:
            mocked.assert_not_called()
        response = request_handler.wfile.getvalue().decode("utf-8")
        for marker in markers:
            self.assertNotIn(marker, response)

    def test_exact_unsafe_mode_reaches_each_existing_get_and_post_dispatch(self):
        authenticated_member = SimpleNamespace(
            email="owner@example.test",
            workspace_id="workspace-a",
            membership_role="owner",
        )
        forbidden_modules = {
            "api.collaboration.http_adapter",
            "api.collaboration.http_boundary",
            "api.collaboration.owner_request_security",
            "api.collaboration.application",
            "api.collaboration.authorization",
            "api.collaboration.source_message",
            "api.collaboration.guest_session",
            "api.collaboration.mutations",
            "api.collaboration.v2_stateful_test_store",
        }
        self.assertTrue(forbidden_modules.isdisjoint(sys.modules))

        with patch.object(collaboration_thread.os, "getenv", return_value=UNSAFE_MODE):
            get_handler = _controlled_handler(collaboration_thread)
            collaboration_thread.handler.do_GET(get_handler)
            self.assertEqual(get_handler.status_normal, [405])
            self.assertEqual(get_handler.status_only, [])
            self.assertNotEqual(get_handler.wfile.getvalue(), DISABLED_BODY)

            post_handler = _controlled_handler(collaboration_thread)
            with patch.object(
                collaboration_thread,
                "_require_authenticated_member",
                return_value=authenticated_member,
            ) as require_member, patch.object(collaboration_thread, "_get_operation", return_value="get-many") as operation, patch.object(
                collaboration_thread, "_read_json_body", return_value=({}, None)
            ) as read_body, patch.object(collaboration_thread, "_handle_get_many") as handle:
                collaboration_thread.handler.do_POST(post_handler)
            require_member.assert_called_once_with(post_handler)
            operation.assert_called_once_with(post_handler)
            read_body.assert_called_once_with(post_handler)
            handle.assert_called_once_with(post_handler, {}, authenticated_member)
            self.assertEqual(post_handler.status_only, [])
            self.assertEqual(post_handler.wfile.getvalue(), b"")

        unsafe_cases = (
            (collaboration_invite, "lookup", "issue", "_handle_lookup", "_handle_issue", True),
            (team_invite, "lookup", "issue", "_handle_lookup", "_handle_issue", True),
            (team_members, "list", "remove", "_handle_list", "_handle_remove", False),
        )
        for route, get_operation, post_operation, get_callback, post_callback, post_reads_body in unsafe_cases:
            with self.subTest(route=route.__name__), patch.object(route.os, "getenv", return_value=UNSAFE_MODE):
                get_handler = _controlled_handler(route)
                with ExitStack() as stack:
                    operation = stack.enter_context(patch.object(route, "_get_operation", return_value=get_operation))
                    callback = stack.enter_context(patch.object(route, get_callback))
                    require_member = None
                    if route is team_members:
                        require_member = stack.enter_context(
                            patch.object(
                                route,
                                "_require_authenticated_member",
                                return_value=authenticated_member,
                            )
                        )
                    route.handler.do_GET(get_handler)
                if require_member is not None:
                    require_member.assert_called_once_with(get_handler)
                operation.assert_called_once_with(get_handler)
                if route is team_members:
                    callback.assert_called_once_with(get_handler, authenticated_member)
                else:
                    callback.assert_called_once_with(get_handler)
                self.assertEqual(get_handler.status_only, [])

                post_handler = _controlled_handler(route)
                with ExitStack() as stack:
                    operation = stack.enter_context(patch.object(route, "_get_operation", return_value=post_operation))
                    callback = stack.enter_context(patch.object(route, post_callback))
                    require_member = None
                    if route is team_members:
                        require_member = stack.enter_context(
                            patch.object(
                                route,
                                "_require_authenticated_member",
                                return_value=authenticated_member,
                            )
                        )
                    if post_reads_body:
                        read_body = stack.enter_context(patch.object(route, "_read_json_body", return_value=({}, None)))
                    route.handler.do_POST(post_handler)
                if require_member is not None:
                    require_member.assert_called_once_with(post_handler)
                operation.assert_called_once_with(post_handler)
                if post_reads_body:
                    read_body.assert_called_once_with(post_handler)
                    callback.assert_called_once_with(post_handler, {})
                elif route is team_members:
                    callback.assert_called_once_with(post_handler, authenticated_member)
                else:
                    callback.assert_called_once_with(post_handler)
                self.assertEqual(post_handler.status_only, [])
                self.assertEqual(post_handler.wfile.getvalue(), b"")

        self.assertTrue(forbidden_modules.isdisjoint(sys.modules))

    def test_unsafe_unsupported_methods_are_local_bodyless_for_head_and_do_not_dispatch(self):
        for route_name, route in ROUTES:
            for method in UNSUPPORTED_METHODS:
                with self.subTest(route=route_name, method=method), ExitStack() as stack:
                    stack.enter_context(patch.object(route.os, "getenv", return_value=UNSAFE_MODE))
                    legacy_get = stack.enter_context(patch.object(route.handler, "do_GET"))
                    legacy_post = stack.enter_context(patch.object(route.handler, "do_POST"))
                    operation = stack.enter_context(
                        patch.object(route, "_get_operation", side_effect=AssertionError("operation parsed"))
                    )
                    read_body = None
                    if hasattr(route, "_read_json_body"):
                        read_body = stack.enter_context(
                            patch.object(route, "_read_json_body", side_effect=AssertionError("body read"))
                        )

                    request_handler = _controlled_handler(route)
                    route.handler.__dict__[f"do_{method}"](request_handler)

                    self.assertEqual(request_handler.status_only, [501])
                    self.assertEqual(request_handler.status_normal, [])
                    self.assertEqual(request_handler.status_errors, [])
                    self.assertEqual(request_handler.response_headers, UNSUPPORTED_HEADERS)
                    self.assertEqual(request_handler.end_headers_calls, 1)
                    expected_writes = [] if method == "HEAD" else [UNSUPPORTED_BODY]
                    self.assertEqual(request_handler.wfile.writes, expected_writes)
                    legacy_get.assert_not_called()
                    legacy_post.assert_not_called()
                    operation.assert_not_called()
                    if read_body is not None:
                        read_body.assert_not_called()

    def test_disabled_response_failures_propagate_without_retry(self):
        failure_points = ("status", "header-1", "header-2", "header-3", "end-headers", "write")
        for route_name, route in ROUTES:
            for failure_point in failure_points:
                with self.subTest(route=route_name, failure=failure_point), patch.object(
                    route.os, "getenv", return_value=None
                ), self.assertRaisesRegex(_ResponseFailure, failure_point):
                    request_handler = _controlled_handler(route, fail_at=failure_point)
                    route.handler.do_GET(request_handler)
                self.assertEqual(request_handler.status_only, [404])
                self.assertEqual(request_handler.status_normal, [])
                self.assertEqual(request_handler.status_errors, [])
                self.assertLessEqual(request_handler.end_headers_calls, 1)
                self.assertLessEqual(len(request_handler.wfile.writes), 1)

    def test_fixed_response_is_module_local_and_does_not_serialize(self):
        self.assertEqual(len(DISABLED_BODY), 64)
        self.assertEqual(len(UNSUPPORTED_BODY), 79)
        for route_name, route in ROUTES:
            with self.subTest(route=route_name), patch.object(route.os, "getenv", return_value=None), patch.object(
                route.json, "dumps", side_effect=AssertionError("json.dumps called")
            ) as dumps:
                self.assertEqual(route._DISABLED_RESPONSE_BODY, DISABLED_BODY)
                self.assertEqual(route._DISABLED_RESPONSE_CONTENT_LENGTH, str(len(DISABLED_BODY)))
                request_handler = _invoke_direct(route, "GET")
                self.assert_disabled_direct(request_handler, method="GET")
                dumps.assert_not_called()

    def test_pristine_import_and_disabled_invocation_do_not_load_inactive_collaboration_modules(self):
        script = textwrap.dedent(
            """
            import importlib
            import imaplib
            import os
            import smtplib
            import sys
            from unittest.mock import patch

            routes = (
                "api.collaboration.thread",
                "api.collaboration.invite",
                "api.team.invite",
                "api.team.members",
            )
            allowed_collaboration_modules = {
                "api.collaboration",
                "api.collaboration.models",
                "api.collaboration.redis_store",
                "api.collaboration.thread",
                "api.collaboration.invite",
            }

            class Writer:
                def __init__(self):
                    self.values = []
                def write(self, value):
                    self.values.append(value)

            class Fake:
                def __init__(self):
                    self.status = None
                    self.headers = []
                    self.wfile = Writer()
                def send_response_only(self, status):
                    self.status = status
                def send_header(self, name, value):
                    self.headers.append((name, value))
                def end_headers(self):
                    pass

            with patch.dict(os.environ, {}, clear=True), patch(
                "os.getenv", side_effect=RuntimeError("environment unavailable")
            ), patch(
                "urllib.request.urlopen", side_effect=AssertionError("network during import")
            ), patch(
                "socket.create_connection", side_effect=AssertionError("socket during import")
            ), patch(
                "imaplib.IMAP4", side_effect=AssertionError("IMAP during import")
            ), patch(
                "imaplib.IMAP4_SSL", side_effect=AssertionError("IMAPS during import")
            ), patch(
                "smtplib.SMTP", side_effect=AssertionError("SMTP during import")
            ), patch(
                "smtplib.SMTP_SSL", side_effect=AssertionError("SMTPS during import")
            ):
                modules = [importlib.import_module(name) for name in routes]
                for module in modules:
                    fake = Fake()
                    module.handler.do_POST(fake)
                    assert fake.status == 404
                    assert fake.headers == [
                        ("Content-Type", "application/json"),
                        ("Cache-Control", "no-store"),
                        ("Content-Length", "64"),
                    ]
                    assert fake.wfile.values == [
                        b'{"ok":false,"error":{"code":"not_found","message":"Not found."}}'
                    ]

            imported_collaboration_modules = {
                name for name in sys.modules if name.startswith("api.collaboration")
            }
            assert imported_collaboration_modules <= allowed_collaboration_modules, imported_collaboration_modules
            for short_name in (
                "http_adapter",
                "http_boundary",
                "owner_request_security",
                "application",
                "authorization",
                "source_message",
                "guest_session",
                "mutations",
                "v2_stateful_test_store",
            ):
                assert short_name not in sys.modules
            """
        )
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=FRONTEND_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )


if __name__ == "__main__":
    unittest.main()
