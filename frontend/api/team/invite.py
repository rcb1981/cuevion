from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import Request, urlopen

TEAM_INVITE_SCHEMA_VERSION = 1
TEAM_ROLES = {"Limited", "Shared", "Editor", "Admin"}
TEAM_INVITE_ISSUABLE_ROLES = {"Limited"}
TEAM_INVITE_STATUSES = {"invited", "accepted", "declined", "cancelled"}


def _send_json(handler: BaseHTTPRequestHandler, status_code: int, payload: dict):
    response_body = json.dumps(payload).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(response_body)))
    handler.end_headers()
    handler.wfile.write(response_body)


def _build_error(code: str, message: str) -> dict:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
        },
    }


def _get_query(handler: BaseHTTPRequestHandler) -> dict[str, list[str]]:
    return parse_qs(urlsplit(handler.path).query)


def _get_operation(handler: BaseHTTPRequestHandler) -> str:
    return str((_get_query(handler).get("op") or [""])[0] or "").strip().lower()


def _get_token(handler: BaseHTTPRequestHandler) -> str:
    return str((_get_query(handler).get("token") or [""])[0] or "").strip()


def _read_json_body(handler: BaseHTTPRequestHandler) -> tuple[dict | None, dict | None]:
    content_length = int(handler.headers.get("content-length", "0"))
    raw_body = handler.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""

    try:
        payload = json.loads(raw_body or "{}")
    except json.JSONDecodeError:
        return None, _build_error("invalid_request", "Request body must be valid JSON.")

    if not isinstance(payload, dict):
        return None, _build_error("invalid_request", "Request body must be a JSON object.")

    return payload, None


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _normalize_invite_record(value: dict | None) -> dict | None:
    if not isinstance(value, dict):
        return None

    token = str(value.get("token") or "").strip()
    workspace_id = str(value.get("workspaceId") or "").strip().lower()
    invitee_email = str(value.get("inviteeEmail") or "").strip().lower()
    invitee_name = str(value.get("inviteeName") or "").strip()
    access_level = str(value.get("accessLevel") or "").strip()
    status = str(value.get("status") or "").strip().lower()
    created_by_user_id = str(value.get("createdByUserId") or "").strip()
    created_by_user_name = str(value.get("createdByUserName") or "").strip()
    created_at = value.get("createdAt")
    updated_at = value.get("updatedAt")

    if (
        not token
        or not workspace_id
        or not invitee_email
        or not invitee_name
        or access_level not in TEAM_INVITE_ISSUABLE_ROLES
        or status not in TEAM_INVITE_STATUSES
        or not created_by_user_id
        or not created_by_user_name
        or not isinstance(created_at, int)
        or not isinstance(updated_at, int)
    ):
        return None

    return {
        "v": TEAM_INVITE_SCHEMA_VERSION,
        "token": token,
        "workspaceId": workspace_id,
        "inviteeEmail": invitee_email,
        "inviteeName": invitee_name,
        "accessLevel": "Limited",
        "status": status,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "createdByUserId": created_by_user_id,
        "createdByUserName": created_by_user_name,
    }


def _resolve_durable_store_config() -> dict | None:
    rest_url = os.getenv("KV_REST_API_URL", "").strip()
    rest_token = os.getenv("KV_REST_API_TOKEN", "").strip()

    if not rest_url or not rest_token:
        return None

    return {
        "rest_url": rest_url.rstrip("/"),
        "rest_token": rest_token,
    }


def _build_invite_key(token: str) -> str:
    return f"cuevion:team:v1:invite:{token.strip()}"


def _build_workspace_invite_key(workspace_id: str, invitee_email: str) -> str:
    return f"cuevion:team:v1:workspace-invite:{workspace_id.strip().lower()}:{invitee_email.strip().lower()}"


def _perform_rest_request(
    config: dict,
    method: str,
    path: str,
    body: bytes | None = None,
) -> tuple[dict | None, dict | None]:
    request = Request(
        f"{config['rest_url']}{path}",
        data=body,
        headers={
            "Authorization": f"Bearer {config['rest_token']}",
            "Content-Type": "application/json",
        },
        method=method,
    )

    try:
        with urlopen(request, timeout=20) as response:
            payload = response.read().decode("utf-8")
            return json.loads(payload) if payload else {}, None
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        try:
            parsed_error = json.loads(error_body) if error_body else {}
        except json.JSONDecodeError:
            parsed_error = {}

        return None, {
            "code": "team_invite_store_unavailable",
            "message": (
                parsed_error.get("error")
                or parsed_error.get("message")
                or f"Team invite store request failed with HTTP {error.code}."
            ),
        }
    except URLError as error:
        return None, {
            "code": "team_invite_store_unavailable",
            "message": (
                str(error.reason)
                if getattr(error, "reason", None)
                else "Could not reach the team invite store."
            ),
        }


def _read_durable_record(config: dict, store_key: str) -> tuple[dict | None, dict | None]:
    payload, error = _perform_rest_request(
        config,
        "GET",
        f"/get/{quote(store_key, safe='')}",
    )
    if error:
        return None, error

    if not isinstance(payload, dict):
        return None, {
            "code": "team_invite_store_unavailable",
            "message": "Team invite store returned an unreadable response.",
        }

    result = payload.get("result")
    if result is None:
        return None, None

    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            return None, {
                "code": "team_invite_store_unavailable",
                "message": "Team invite store returned malformed JSON.",
            }
        return parsed if isinstance(parsed, dict) else None, None

    return result if isinstance(result, dict) else None, None


def _write_durable_record(config: dict, store_key: str, record: dict) -> tuple[dict | None, dict | None]:
    encoded_record = json.dumps(record, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload, error = _perform_rest_request(
        config,
        "POST",
        f"/set/{quote(store_key, safe='')}",
        body=encoded_record,
    )
    if error:
        return None, error

    if not isinstance(payload, dict) or payload.get("result") != "OK":
        return None, {
            "code": "team_invite_store_unavailable",
            "message": "Team invite store did not confirm the write.",
        }

    return payload, None


def _get_invite(token: str) -> dict | None:
    normalized_token = token.strip()
    if not normalized_token:
        return None

    config = _resolve_durable_store_config()
    if not config:
        return None

    record, error = _read_durable_record(config, _build_invite_key(normalized_token))
    if error or not record:
        return None

    normalized_invite = _normalize_invite_record(record)
    if not normalized_invite or normalized_invite["token"] != normalized_token:
        return None

    return normalized_invite


def _get_workspace_invite(workspace_id: str, invitee_email: str) -> dict | None:
    normalized_workspace_id = workspace_id.strip().lower()
    normalized_invitee_email = invitee_email.strip().lower()
    if not normalized_workspace_id or not normalized_invitee_email:
        return None

    config = _resolve_durable_store_config()
    if not config:
        return None

    record, error = _read_durable_record(
        config,
        _build_workspace_invite_key(normalized_workspace_id, normalized_invitee_email),
    )
    if error or not record:
        return None

    normalized_invite = _normalize_invite_record(record)
    if not normalized_invite:
        return None

    if (
        normalized_invite["workspaceId"] != normalized_workspace_id
        or normalized_invite["inviteeEmail"] != normalized_invitee_email
    ):
        return None

    return normalized_invite


def _save_invite(invite_record: dict) -> tuple[dict | None, dict | None]:
    normalized_invite = _normalize_invite_record(invite_record)
    if not normalized_invite:
        return None, {
            "code": "invalid_invite",
            "message": "Team invite record is invalid.",
        }

    config = _resolve_durable_store_config()
    if not config:
        return None, {
            "code": "team_invite_store_unavailable",
            "message": "Team invite store is not configured.",
        }

    _, error = _write_durable_record(
        config,
        _build_invite_key(normalized_invite["token"]),
        normalized_invite,
    )
    if error:
        return None, error

    _, pointer_error = _write_durable_record(
        config,
        _build_workspace_invite_key(
            normalized_invite["workspaceId"],
            normalized_invite["inviteeEmail"],
        ),
        normalized_invite,
    )
    if pointer_error:
        return None, pointer_error

    return normalized_invite, None


def _build_invite_url(handler: BaseHTTPRequestHandler, *, token: str) -> str:
    forwarded_proto = str(handler.headers.get("x-forwarded-proto") or "").strip()
    forwarded_host = str(handler.headers.get("x-forwarded-host") or "").strip()
    host = forwarded_host or str(handler.headers.get("host") or "").strip()
    scheme = forwarded_proto or ("http" if host.startswith("localhost") or host.startswith("127.0.0.1") else "https")

    origin = f"{scheme}://{host}" if host else ""
    if not origin:
        return f"/?team_invite={token}"

    return f"{origin}/?team_invite={token}"


def _handle_issue(handler: BaseHTTPRequestHandler, payload: dict):
    workspace_id = str(payload.get("workspaceId") or "").strip().lower()
    invitee_email = str(payload.get("inviteeEmail") or "").strip().lower()
    invitee_name = str(payload.get("inviteeName") or "").strip()
    access_level = str(payload.get("accessLevel") or "").strip()
    created_by_user_id = str(payload.get("createdByUserId") or "").strip()
    created_by_user_name = str(payload.get("createdByUserName") or "").strip()

    if access_level not in TEAM_ROLES:
        _send_json(handler, 400, _build_error("invalid_request", "Unsupported team role."))
        return

    if (
        not workspace_id
        or not invitee_email
        or not invitee_name
        or access_level not in TEAM_INVITE_ISSUABLE_ROLES
        or not created_by_user_id
        or not created_by_user_name
    ):
        _send_json(
            handler,
            400,
            _build_error(
                "invalid_request",
                "workspaceId, inviteeEmail, inviteeName, accessLevel, createdByUserId, and createdByUserName are required.",
            ),
        )
        return

    existing_invite = _get_workspace_invite(workspace_id, invitee_email)
    if existing_invite and existing_invite["status"] == "invited":
        _send_json(
            handler,
            200,
            {
                "ok": True,
                "invite": existing_invite,
                "inviteUrl": _build_invite_url(handler, token=existing_invite["token"]),
            },
        )
        return

    now_ms = _now_ms()
    invite_record, invite_error = _save_invite(
        {
            "v": TEAM_INVITE_SCHEMA_VERSION,
            "token": secrets.token_urlsafe(24),
            "workspaceId": workspace_id,
            "inviteeEmail": invitee_email,
            "inviteeName": invitee_name,
            "accessLevel": "Limited",
            "status": "invited",
            "createdAt": now_ms,
            "updatedAt": now_ms,
            "createdByUserId": created_by_user_id,
            "createdByUserName": created_by_user_name,
        }
    )
    if invite_error or invite_record is None:
        _send_json(
            handler,
            503,
            _build_error(
                invite_error["code"] if invite_error else "team_invite_store_unavailable",
                invite_error["message"] if invite_error else "Could not issue team invite.",
            ),
        )
        return

    _send_json(
        handler,
        200,
        {
            "ok": True,
            "invite": invite_record,
            "inviteUrl": _build_invite_url(handler, token=invite_record["token"]),
        },
    )


def _handle_lookup(handler: BaseHTTPRequestHandler):
    token = _get_token(handler)
    invite = _get_invite(token)

    if invite is None:
        _send_json(handler, 404, _build_error("invalid_invite", "Team invite was not found."))
        return

    _send_json(handler, 200, {"ok": True, "invite": invite})


def _handle_action(handler: BaseHTTPRequestHandler, payload: dict):
    token = _get_token(handler)
    invite = _get_invite(token)

    if invite is None:
        _send_json(handler, 404, _build_error("invalid_invite", "Team invite was not found."))
        return

    action = payload.get("action")
    action_type = str(action.get("type") if isinstance(action, dict) else "").strip().lower()
    next_status_by_action = {
        "accept": "accepted",
        "decline": "declined",
        "cancel": "cancelled",
    }
    next_status = next_status_by_action.get(action_type)

    if not next_status:
        _send_json(handler, 400, _build_error("invalid_request", "Unsupported team invite action."))
        return

    if invite["status"] == "cancelled" and action_type != "cancel":
        _send_json(handler, 409, _build_error("cancelled_invite", "Team invite has been cancelled."))
        return

    next_invite, invite_error = _save_invite(
        {
            **invite,
            "status": next_status,
            "updatedAt": _now_ms(),
        }
    )
    if invite_error or next_invite is None:
        _send_json(
            handler,
            503,
            _build_error(
                invite_error["code"] if invite_error else "team_invite_store_unavailable",
                invite_error["message"] if invite_error else "Could not update team invite.",
            ),
        )
        return

    _send_json(handler, 200, {"ok": True, "invite": next_invite})


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        operation = _get_operation(self)

        if operation == "lookup":
            _handle_lookup(self)
            return

        _send_json(self, 404, _build_error("not_found", "Unsupported team invite operation."))

    def do_POST(self):
        operation = _get_operation(self)
        payload, payload_error = _read_json_body(self)

        if payload_error or payload is None:
            _send_json(self, 400, payload_error or _build_error("invalid_request", "Request is invalid."))
            return

        if operation == "issue":
            _handle_issue(self, payload)
            return

        if operation == "action":
            _handle_action(self, payload)
            return

        _send_json(self, 404, _build_error("not_found", "Unsupported team invite operation."))
