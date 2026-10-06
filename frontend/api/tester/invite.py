"""HTTP boundary for Tester Invite issue, lookup and cancellation."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qsl, urlsplit

from api.auth import http
from api.auth.runtime import MemberResolutionOutcome, resolve_authenticated_member
from api.team.http_security import require_safe_json_mutation
from api.tester.authority import (
    TesterInviteAuthorityError,
    build_runtime_tester_invite_authority,
)

_ROUTE = "/api/tester/invite"
_MAX_BODY_BYTES = 4 * 1024


def _error(code: str, message: str) -> dict[str, object]:
    return {"ok": False, "error": {"code": code, "message": message}}


def _send(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, object], *, cookies=()) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    for cookie in cookies:
        handler.send_header("Set-Cookie", cookie)
    handler.end_headers()
    handler.wfile.write(body)


def _operation(raw_path: str) -> str:
    parsed = urlsplit(raw_path)
    if parsed.scheme or parsed.netloc or parsed.fragment or parsed.path != _ROUTE:
        raise ValueError
    pairs = parse_qsl(
        parsed.query,
        keep_blank_values=True,
        strict_parsing=True,
        max_num_fields=1,
        encoding="utf-8",
        errors="strict",
    )
    if len(pairs) != 1 or pairs[0][0] != "op" or pairs[0][1] not in {"lookup", "issue", "cancel"}:
        raise ValueError
    return pairs[0][1]


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, object]:
    raw_length = handler.headers.get("content-length")
    if (
        raw_length is None
        or not raw_length.isascii()
        or not raw_length.isdigit()
        or not 2 <= int(raw_length) <= _MAX_BODY_BYTES
        or handler.headers.get("transfer-encoding") is not None
    ):
        raise ValueError
    raw = handler.rfile.read(int(raw_length))
    value = json.loads(raw.decode("utf-8", errors="strict"))
    if type(value) is not dict:
        raise ValueError
    return value


def _authority_error(handler: BaseHTTPRequestHandler, error: TesterInviteAuthorityError) -> None:
    mapping = {
        "forbidden": (403, "forbidden"),
        "invalid_request": (400, "invalid_request"),
        "invalid_invite": (404, "invalid_invite"),
        "expired_invite": (410, "expired_invite"),
        "cancelled_invite": (410, "cancelled_invite"),
        "used_invite": (409, "used_invite"),
        "live_invitation_exists": (409, "live_invitation_exists"),
        "account_already_provisioned": (409, "account_already_provisioned"),
        "conflict": (409, "conflict"),
    }
    status, code = mapping.get(error.code, (503, "tester_authority_unavailable"))
    _send(handler, status, _error(code, "Tester invitation could not be processed."))


def _authenticated_actor(handler: BaseHTTPRequestHandler, headers) -> str | None:
    resolution = resolve_authenticated_member(headers, environment=os.environ)
    if resolution.outcome is MemberResolutionOutcome.AUTHENTICATED and resolution.member is not None:
        return resolution.member.user_id
    if resolution.outcome is MemberResolutionOutcome.UNAVAILABLE:
        _send(
            handler,
            503,
            _error("authentication_unavailable", "Authentication is temporarily unavailable."),
            cookies=resolution.set_cookies,
        )
    else:
        _send(
            handler,
            401,
            _error("unauthorized", "Authentication is required."),
            cookies=resolution.set_cookies,
        )
    return None


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            headers = http.snapshot_request_headers(self)
            http.require_canonical_host(headers)
            require_safe_json_mutation(headers)
            operation = _operation(self.path)
            payload = _read_json(self)
        except http.HttpBoundaryError as error:
            _send(self, error.status, _error(error.code, "Request was rejected."))
            return
        except Exception:
            _send(self, 400, _error("invalid_request", "Request was rejected."))
            return

        authority = build_runtime_tester_invite_authority(os.environ)
        try:
            if operation == "lookup":
                if set(payload) != {"token"} or type(payload.get("token")) is not str:
                    raise TesterInviteAuthorityError("invalid_request")
                invitation = authority.read_provisioning_invitation(
                    payload["token"],
                    allow_provisioned=True,
                )
                _send(
                    self,
                    200,
                    {
                        "ok": True,
                        "invite": {
                            "inviteeName": invitation.display_name,
                            "status": invitation.status,
                            "expiresAt": invitation.expires_at,
                        },
                    },
                )
                return

            actor_user_id = _authenticated_actor(self, headers)
            if actor_user_id is None:
                return

            if operation == "issue":
                allowed = {"inviteeEmail", "inviteeName", "sourceRequestId"}
                if (
                    not {"inviteeEmail", "inviteeName"}.issubset(payload)
                    or not set(payload).issubset(allowed)
                    or type(payload["inviteeEmail"]) is not str
                    or type(payload["inviteeName"]) is not str
                    or (
                        "sourceRequestId" in payload
                        and payload["sourceRequestId"] is not None
                        and type(payload["sourceRequestId"]) is not str
                    )
                ):
                    raise TesterInviteAuthorityError("invalid_request")
                result = authority.issue_invitation(
                    actor_user_id=actor_user_id,
                    invitee_email=payload["inviteeEmail"],
                    invitee_name=payload["inviteeName"],
                    source_request_id=payload.get("sourceRequestId"),
                )
                token = str(result["rawToken"])
                invite = result["invite"]
                _send(
                    self,
                    200,
                    {
                        "ok": True,
                        "invite": invite,
                        "inviteUrl": f"{http.CANONICAL_APP_ORIGIN}/#tester_invite={token}",
                    },
                )
                return

            if set(payload) != {"invitationId"} or type(payload.get("invitationId")) is not str:
                raise TesterInviteAuthorityError("invalid_request")
            cancelled = authority.cancel_invitation(
                actor_user_id=actor_user_id,
                invitation_id=payload["invitationId"],
            )
            _send(self, 200, {"ok": True, "invite": cancelled})
        except TesterInviteAuthorityError as error:
            _authority_error(self, error)
        except Exception:
            _send(
                self,
                503,
                _error(
                    "tester_authority_unavailable",
                    "Tester invitation could not be processed.",
                ),
            )

    def do_GET(self):
        _send(self, 405, _error("method_not_allowed", "Request was rejected."))

    do_PUT = do_GET
    do_PATCH = do_GET
    do_DELETE = do_GET
    do_OPTIONS = do_GET
    do_HEAD = do_GET
    do_TRACE = do_GET
    do_CONNECT = do_GET

    def log_message(self, _format, *_args):
        return
