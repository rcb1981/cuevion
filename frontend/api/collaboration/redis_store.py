from __future__ import annotations

import json
import os
import secrets
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from models import (
    COLLABORATION_THREAD_SCHEMA_VERSION,
    is_active_collaboration_invite_record,
    normalize_collaboration_invite_record,
    normalize_collaboration_thread_record,
)

MAX_COLLABORATION_THREAD_BATCH_SIZE = 200


def _resolve_durable_store_config() -> dict | None:
    rest_url = os.getenv("KV_REST_API_URL", "").strip()
    rest_token = os.getenv("KV_REST_API_TOKEN", "").strip()

    if not rest_url or not rest_token:
        return None

    return {
        "backend": "vercel_kv_rest",
        "rest_url": rest_url.rstrip("/"),
        "rest_token": rest_token,
    }


def build_thread_key(workspace_id: str, message_id: str) -> str:
    return f"cuevion:collab:v1:thread:{workspace_id.strip().lower()}:{message_id.strip()}"


def build_invite_key(token: str) -> str:
    return f"cuevion:collab:v1:invite:{token.strip()}"


def build_thread_invite_key(workspace_id: str, message_id: str, invitee_email: str) -> str:
    return (
        "cuevion:collab:v1:thread-invite:"
        f"{workspace_id.strip().lower()}:{message_id.strip()}:{invitee_email.strip().lower()}"
    )


def is_collaboration_store_configured() -> bool:
    return _resolve_durable_store_config() is not None


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
            "code": "collaboration_store_unavailable",
            "message": (
                parsed_error.get("error")
                or parsed_error.get("message")
                or f"Collaboration store request failed with HTTP {error.code}."
            ),
        }
    except URLError as error:
        return None, {
            "code": "collaboration_store_unavailable",
            "message": (
                str(error.reason)
                if getattr(error, "reason", None)
                else "Could not reach the collaboration store."
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
            "code": "collaboration_store_unavailable",
            "message": "Collaboration store returned an unreadable response.",
        }

    result = payload.get("result")
    if result is None:
        return None, None

    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            return None, {
                "code": "collaboration_store_unavailable",
                "message": "Collaboration store returned malformed JSON.",
            }
        return parsed if isinstance(parsed, dict) else None, None

    return result if isinstance(result, dict) else None, None


def get_threads_many(workspace_id: str, message_ids: list[str]) -> dict[str, dict]:
    normalized_workspace_id = workspace_id.strip().lower()
    if not normalized_workspace_id:
        return {}

    deduped_message_ids: list[str] = []
    seen_message_ids: set[str] = set()
    for message_id in message_ids:
        if not isinstance(message_id, str):
            continue

        normalized_message_id = message_id.strip()
        if not normalized_message_id or normalized_message_id in seen_message_ids:
            continue

        seen_message_ids.add(normalized_message_id)
        deduped_message_ids.append(normalized_message_id)

        if len(deduped_message_ids) >= MAX_COLLABORATION_THREAD_BATCH_SIZE:
            break

    if not deduped_message_ids:
        return {}

    config = _resolve_durable_store_config()
    if not config:
        return {}

    threads_by_message_id: dict[str, dict] = {}

    for message_id in deduped_message_ids:
        thread_key = build_thread_key(normalized_workspace_id, message_id)
        record, error = _read_durable_record(config, thread_key)
        if error or not record:
            continue

        normalized_thread = normalize_collaboration_thread_record(record)
        if not normalized_thread:
            continue

        if (
            normalized_thread["workspaceId"] != normalized_workspace_id
            or normalized_thread["messageId"] != message_id
        ):
            continue

        threads_by_message_id[message_id] = normalized_thread

    return threads_by_message_id


def get_thread(workspace_id: str, message_id: str) -> dict | None:
    normalized_workspace_id = workspace_id.strip().lower()
    normalized_message_id = message_id.strip()

    if not normalized_workspace_id or not normalized_message_id:
        return None

    config = _resolve_durable_store_config()
    if not config:
        return None

    record, error = _read_durable_record(
        config,
        build_thread_key(normalized_workspace_id, normalized_message_id),
    )
    if error or not record:
        return None

    normalized_thread = normalize_collaboration_thread_record(record)
    if not normalized_thread:
        return None

    if (
        normalized_thread["workspaceId"] != normalized_workspace_id
        or normalized_thread["messageId"] != normalized_message_id
    ):
        return None

    return normalized_thread


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
            "code": "collaboration_store_unavailable",
            "message": "Collaboration store did not confirm the write.",
        }

    return payload, None


def save_thread(thread_record: dict) -> tuple[dict | None, dict | None]:
    normalized_thread = normalize_collaboration_thread_record(thread_record)
    if not normalized_thread:
        return None, {
            "code": "invalid_thread",
            "message": "Thread record is invalid.",
        }

    config = _resolve_durable_store_config()
    if not config:
        return None, {
            "code": "collaboration_store_unavailable",
            "message": "Collaboration store is not configured.",
        }

    _, error = _write_durable_record(
        config,
        build_thread_key(normalized_thread["workspaceId"], normalized_thread["messageId"]),
        normalized_thread,
    )
    if error:
        return None, error

    return normalized_thread, None


def create_thread_if_missing(thread_record: dict) -> tuple[dict | None, dict | None]:
    normalized_thread = normalize_collaboration_thread_record(thread_record)
    if not normalized_thread:
        return None, {
            "code": "invalid_thread",
            "message": "Thread record is invalid.",
        }

    existing_thread = get_thread(
        normalized_thread["workspaceId"],
        normalized_thread["messageId"],
    )
    if existing_thread:
        return existing_thread, None

    return save_thread(normalized_thread)


def save_thread_if_expected(
    thread_record: dict,
    expected_updated_at: int | None = None,
) -> tuple[dict | None, dict | None]:
    normalized_thread = normalize_collaboration_thread_record(thread_record)
    if not normalized_thread:
        return None, {
            "code": "invalid_thread",
            "message": "Thread record is invalid.",
        }

    existing_thread = get_thread(
        normalized_thread["workspaceId"],
        normalized_thread["messageId"],
    )
    if not existing_thread:
        return None, {
            "code": "thread_not_found",
            "message": "Canonical collaboration thread was not found.",
        }

    if (
        expected_updated_at is not None
        and existing_thread["collaboration"]["updatedAt"] != expected_updated_at
    ):
        return existing_thread, {
            "code": "stale_thread",
            "message": "Canonical collaboration thread is newer than the local version.",
        }

    return save_thread(normalized_thread)


def get_invite(token: str) -> dict | None:
    normalized_token = token.strip()
    if not normalized_token:
        return None

    config = _resolve_durable_store_config()
    if not config:
        return None

    record, error = _read_durable_record(
        config,
        build_invite_key(normalized_token),
    )
    if error or not record:
        return None

    normalized_invite = normalize_collaboration_invite_record(record)
    if not normalized_invite or normalized_invite["token"] != normalized_token:
        return None

    return normalized_invite


def get_thread_invite(workspace_id: str, message_id: str, invitee_email: str) -> dict | None:
    normalized_workspace_id = workspace_id.strip().lower()
    normalized_message_id = message_id.strip()
    normalized_invitee_email = invitee_email.strip().lower()

    if not normalized_workspace_id or not normalized_message_id or not normalized_invitee_email:
        return None

    config = _resolve_durable_store_config()
    if not config:
        return None

    record, error = _read_durable_record(
        config,
        build_thread_invite_key(
            normalized_workspace_id,
            normalized_message_id,
            normalized_invitee_email,
        ),
    )
    if error or not record:
        return None

    normalized_invite = normalize_collaboration_invite_record(record)
    if not normalized_invite:
        return None

    if (
        normalized_invite["workspaceId"] != normalized_workspace_id
        or normalized_invite["messageId"] != normalized_message_id
        or normalized_invite["inviteeEmail"] != normalized_invitee_email
    ):
        return None

    return normalized_invite


def save_invite(invite_record: dict) -> tuple[dict | None, dict | None]:
    normalized_invite = normalize_collaboration_invite_record(invite_record)
    if not normalized_invite:
        return None, {
            "code": "invalid_invite",
            "message": "Invite record is invalid.",
        }

    config = _resolve_durable_store_config()
    if not config:
        return None, {
            "code": "collaboration_store_unavailable",
            "message": "Collaboration store is not configured.",
        }

    _, error = _write_durable_record(
        config,
        build_invite_key(normalized_invite["token"]),
        normalized_invite,
    )
    if error:
        return None, error

    _, pointer_error = _write_durable_record(
        config,
        build_thread_invite_key(
            normalized_invite["workspaceId"],
            normalized_invite["messageId"],
            normalized_invite["inviteeEmail"],
        ),
        normalized_invite,
    )
    if pointer_error:
        return None, pointer_error

    return normalized_invite, None


def issue_invite_for_thread(
    *,
    workspace_id: str,
    mailbox_id: str,
    message_id: str,
    invitee_email: str,
    participant_id: str,
    created_by_user_id: str,
    created_by_user_name: str,
    created_at: int,
    updated_at: int,
) -> tuple[dict | None, dict | None]:
    normalized_workspace_id = workspace_id.strip().lower()
    normalized_mailbox_id = mailbox_id.strip()
    normalized_message_id = message_id.strip()
    normalized_invitee_email = invitee_email.strip().lower()
    normalized_participant_id = participant_id.strip()
    normalized_created_by_user_id = created_by_user_id.strip()
    normalized_created_by_user_name = created_by_user_name.strip()

    if (
        not normalized_workspace_id
        or not normalized_mailbox_id
        or not normalized_message_id
        or not normalized_invitee_email
        or not normalized_participant_id
        or not normalized_created_by_user_id
        or not normalized_created_by_user_name
    ):
        return None, {
            "code": "invalid_invite",
            "message": "Invite payload is invalid.",
        }

    existing_invite = get_thread_invite(
        normalized_workspace_id,
        normalized_message_id,
        normalized_invitee_email,
    )
    if is_active_collaboration_invite_record(existing_invite):
        return existing_invite, None

    invite_record = {
        "v": COLLABORATION_THREAD_SCHEMA_VERSION,
        "token": secrets.token_urlsafe(24),
        "workspaceId": normalized_workspace_id,
        "mailboxId": normalized_mailbox_id,
        "messageId": normalized_message_id,
        "inviteeEmail": normalized_invitee_email,
        "participantId": normalized_participant_id,
        "status": "active",
        "createdAt": created_at,
        "updatedAt": updated_at,
        "createdByUserId": normalized_created_by_user_id,
        "createdByUserName": normalized_created_by_user_name,
    }

    return save_invite(invite_record)
