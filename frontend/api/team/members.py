from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import Request, urlopen

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from beta_auth import is_valid_auth_email, normalize_auth_email, parse_beta_session_token, read_beta_session_cookie  # noqa: E402

TEAM_MEMBER_SCHEMA_VERSION = 1
TEAM_ROLES = {"Limited", "Shared"}
ACTIVE_TEAM_MEMBER_STATUS = "active"
REMOVED_TEAM_MEMBER_STATUS = "removed"
LEGACY_TEAM_ROLE_MAP = {
    "review": "Shared",
    "admin": "Shared",
    "editor": "Shared",
    "shared": "Shared",
    "limited": "Limited",
}


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


def _get_workspace_id(handler: BaseHTTPRequestHandler) -> str:
    return str((_get_query(handler).get("workspaceId") or [""])[0] or "").strip().lower()


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


def _normalize_team_role(value: object) -> str | None:
    normalized_value = str(value or "").strip().lower()
    return LEGACY_TEAM_ROLE_MAP.get(normalized_value)


def _normalize_email(value: object) -> str:
    return str(value or "").strip().lower()


def _normalize_workspace_owner_key(value: object) -> str:
    normalized_value = str(value or "").strip().lower()
    return normalized_value


def _normalize_members_index(value: object) -> list[str]:
    if not isinstance(value, list):
        return []

    deduped_emails: list[str] = []
    seen_emails: set[str] = set()
    for item in value:
        email = _normalize_email(item)
        if not email or email in seen_emails:
            continue

        seen_emails.add(email)
        deduped_emails.append(email)

    return deduped_emails


def _normalize_member_record(value: dict | None, workspace_id: str, email: str) -> dict | None:
    if not isinstance(value, dict):
        return None

    normalized_workspace_id = str(value.get("workspaceId") or "").strip().lower()
    normalized_email = _normalize_email(value.get("email"))
    access_level = _normalize_team_role(value.get("accessLevel"))
    status = str(value.get("status") or "").strip().lower()
    display_name = str(value.get("displayName") or value.get("name") or "").strip()
    invite_token = str(value.get("inviteToken") or "").strip()
    created_at = value.get("createdAt")
    updated_at = value.get("updatedAt")
    accepted_at = value.get("acceptedAt")

    if (
        normalized_workspace_id != workspace_id
        or normalized_email != email
        or access_level not in TEAM_ROLES
        or status != ACTIVE_TEAM_MEMBER_STATUS
        or not display_name
        or not invite_token
        or not isinstance(created_at, int)
        or not isinstance(updated_at, int)
        or not isinstance(accepted_at, int)
    ):
        return None

    invited_by_user_id = str(value.get("invitedByUserId") or value.get("inviterUserId") or "").strip()
    invited_by_user_name = str(value.get("invitedByUserName") or value.get("inviterName") or "").strip()

    return {
        "v": TEAM_MEMBER_SCHEMA_VERSION,
        "workspaceId": normalized_workspace_id,
        "email": normalized_email,
        "displayName": display_name,
        "name": display_name,
        "accessLevel": access_level,
        "status": ACTIVE_TEAM_MEMBER_STATUS,
        "inviteToken": invite_token,
        "invitedByUserId": invited_by_user_id,
        "invitedByUserName": invited_by_user_name,
        "inviterUserId": invited_by_user_id,
        "inviterName": invited_by_user_name,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "acceptedAt": accepted_at,
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


def _build_member_key(workspace_id: str, email: str) -> str:
    return f"cuevion:team:v1:member:{workspace_id.strip().lower()}:{email.strip().lower()}"


def _build_members_index_key(workspace_id: str) -> str:
    return f"cuevion:team:v1:members-index:{workspace_id.strip().lower()}"


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
            "code": "team_members_store_unavailable",
            "message": (
                parsed_error.get("error")
                or parsed_error.get("message")
                or f"Team members store request failed with HTTP {error.code}."
            ),
        }
    except URLError as error:
        return None, {
            "code": "team_members_store_unavailable",
            "message": (
                str(error.reason)
                if getattr(error, "reason", None)
                else "Could not reach the team members store."
            ),
        }


def _read_durable_value(config: dict, store_key: str) -> tuple[object | None, dict | None]:
    payload, error = _perform_rest_request(
        config,
        "GET",
        f"/get/{quote(store_key, safe='')}",
    )
    if error:
        return None, error

    if not isinstance(payload, dict):
        return None, {
            "code": "team_members_store_unavailable",
            "message": "Team members store returned an unreadable response.",
        }

    result = payload.get("result")
    if result is None:
        return None, None

    if isinstance(result, str):
        try:
            return json.loads(result), None
        except json.JSONDecodeError:
            return None, {
                "code": "team_members_store_unavailable",
                "message": "Team members store returned malformed JSON.",
            }

    return result, None


def _read_durable_record(config: dict, store_key: str) -> tuple[dict | None, dict | None]:
    value, error = _read_durable_value(config, store_key)
    if error:
        return None, error

    return value if isinstance(value, dict) else None, None


def _write_durable_record(config: dict, store_key: str, record: object) -> tuple[dict | None, dict | None]:
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
            "code": "team_members_store_unavailable",
            "message": "Team members store did not confirm the write.",
        }

    return payload, None


def _get_authenticated_user(headers) -> dict | None:
    session_token = read_beta_session_cookie(headers)
    return parse_beta_session_token(session_token or "")


def _remove_team_member(workspace_id: str, member_email: str) -> tuple[dict | None, dict | None]:
    config = _resolve_durable_store_config()
    if not config:
        return None, {
            "code": "team_members_store_unavailable",
            "message": "Team members store is not configured.",
        }

    normalized_workspace_id = workspace_id.strip().lower()
    normalized_member_email = _normalize_email(member_email)
    member_key = _build_member_key(normalized_workspace_id, normalized_member_email)
    index_key = _build_members_index_key(normalized_workspace_id)
    removed_at = _now_ms()

    record, record_error = _read_durable_record(config, member_key)
    if record_error:
        return None, record_error

    if isinstance(record, dict):
        removed_record = {
            **record,
            "workspaceId": normalized_workspace_id,
            "email": normalized_member_email,
            "status": REMOVED_TEAM_MEMBER_STATUS,
            "updatedAt": removed_at,
            "removedAt": removed_at,
            "revokedAt": removed_at,
        }
        _, write_error = _write_durable_record(config, member_key, removed_record)
        if write_error:
            return None, write_error

    index_value, index_error = _read_durable_value(config, index_key)
    if index_error:
        return None, index_error

    next_index = [
        email
        for email in _normalize_members_index(index_value)
        if email != normalized_member_email
    ]
    _, index_write_error = _write_durable_record(config, index_key, next_index)
    if index_write_error:
        return None, index_write_error

    return {
        "workspaceId": normalized_workspace_id,
        "email": normalized_member_email,
        "status": REMOVED_TEAM_MEMBER_STATUS,
        "removedAt": removed_at,
    }, None


def _list_team_members(workspace_id: str) -> tuple[list[dict] | None, dict | None]:
    config = _resolve_durable_store_config()
    if not config:
        return None, {
            "code": "team_members_store_unavailable",
            "message": "Team members store is not configured.",
        }

    index_value, index_error = _read_durable_value(config, _build_members_index_key(workspace_id))
    if index_error:
        return None, index_error

    members: list[dict] = []
    for email in _normalize_members_index(index_value):
        record, record_error = _read_durable_record(config, _build_member_key(workspace_id, email))
        if record_error:
            return None, record_error

        normalized_member = _normalize_member_record(record, workspace_id, email)
        if normalized_member:
            members.append(normalized_member)

    return members, None


def _handle_list(handler: BaseHTTPRequestHandler):
    workspace_id = _get_workspace_id(handler)
    if not workspace_id:
        _send_json(handler, 400, _build_error("invalid_request", "workspaceId is required."))
        return

    members, error = _list_team_members(workspace_id)
    if error:
        _send_json(handler, 503, _build_error(error["code"], error["message"]))
        return

    _send_json(handler, 200, {"ok": True, "members": members or []})


def _handle_remove(handler: BaseHTTPRequestHandler):
    payload, read_error = _read_json_body(handler)
    if read_error:
        _send_json(handler, 400, read_error)
        return

    workspace_id = str((payload or {}).get("workspaceId") or "").strip().lower()
    member_email = _normalize_email((payload or {}).get("memberEmail"))
    if not workspace_id or not is_valid_auth_email(member_email):
        _send_json(
            handler,
            400,
            _build_error("invalid_request", "workspaceId and memberEmail are required."),
        )
        return

    authenticated_user = _get_authenticated_user(handler.headers)
    authenticated_email = normalize_auth_email(str((authenticated_user or {}).get("email") or ""))
    if (
        not authenticated_email
        or _normalize_workspace_owner_key(authenticated_email) != workspace_id
    ):
        _send_json(handler, 403, _build_error("forbidden", "Only the workspace owner can remove Team members."))
        return

    member, error = _remove_team_member(workspace_id, member_email)
    if error:
        _send_json(handler, 503, _build_error(error["code"], error["message"]))
        return

    _send_json(handler, 200, {"ok": True, "member": member})


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        operation = _get_operation(self)

        if operation == "list":
            _handle_list(self)
            return

        _send_json(self, 404, _build_error("not_found", "Unsupported team members operation."))

    def do_POST(self):
        operation = _get_operation(self)

        if operation in {"remove", "revoke"}:
            _handle_remove(self)
            return

        _send_json(self, 404, _build_error("not_found", "Unsupported team members operation."))
