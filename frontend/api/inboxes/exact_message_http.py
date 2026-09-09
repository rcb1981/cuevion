"""Neutral exact cold-open HTTP boundary; it owns no notification behavior."""

from __future__ import annotations

import os

from api.auth import http as security, runtime
from api.collaboration import http_adapter, http_boundary
from api.collaboration.models import (
    normalize_v2_source_ref, normalize_v2_user_id, normalize_v2_workspace_id,
)
from api.team.http_security import require_safe_json_mutation
from api.user_config_store import resolve_owned_managed_inbox_record
from .authenticated_gmail import resolve_gmail_context
from .authenticated_imap import resolve_authenticated_imap_mailbox
from .imap_uid_validity import is_canonical_uid_validity
from . import exact_message_provider as provider
from .exact_message_rate_limit import consume_exact_message_rate_limit


ROUTE = "/api/inboxes/fetch-exact-message"
MAX_REQUEST_BYTES = 2_048
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
ERROR_STATUSES = {
    "authentication_required": 401, "forbidden": 403, "mailbox_not_found": 404,
    "provider_mismatch": 409, "source_invalid": 400, "source_changed": 409,
    "message_not_found": 404, "service_unavailable": 503, "invalid_response": 502,
    "rate_limited": 429, "method_not_allowed": 405, "payload_too_large": 413,
    "unsupported_media_type": 415,
}


def failure(code, *, retry_after=None):
    code = code if code in ERROR_STATUSES else "service_unavailable"
    extras = (("Allow", "POST"),) if code == "method_not_allowed" else ()
    if retry_after is not None:
        extras += (("Retry-After", str(retry_after)),)
    return security.json_response(
        ERROR_STATUSES[code],
        {"error": {"code": code, "message": "The exact message could not be loaded."}},
        extra_headers=extras,
    )


def parse_request(request, headers):
    size = http_adapter.preflight_content_length(
        headers, maximum_bytes=MAX_REQUEST_BYTES, required=True,
    )
    body = http_boundary.require_bounded_body(
        headers, request.rfile.read(size), maximum_bytes=MAX_REQUEST_BYTES,
    )
    payload = http_boundary.parse_json_object(
        http_boundary.decode_strict_utf8(body),
        allowed_fields={"v", "mailboxId", "sourceRef"},
        required_fields={"v", "mailboxId", "sourceRef"}, reject_numbers=False,
    )
    mailbox_id = payload["mailboxId"]
    source = normalize_v2_source_ref(payload["sourceRef"])
    if (type(payload["v"]) is not int or payload["v"] != 1
            or type(mailbox_id) is not str or not 1 <= len(mailbox_id) <= 256
            or mailbox_id != mailbox_id.strip()
            or any(ord(char) < 33 or ord(char) > 126 for char in mailbox_id)
            or source is None):
        return None
    if source["provider"] == "custom_imap" and (
        not is_canonical_uid_validity(source["uidValidity"])
        or len(source["imapUid"]) > 10 or int(source["imapUid"]) > 4_294_967_295
    ):
        return None
    return mailbox_id, source


def valid_member(member):
    return (
        type(member) is runtime.AuthenticatedMemberContext
        and member.auth_source == "auth0" and member.user_type == "member"
        and normalize_v2_user_id(member.user_id) == member.user_id
        and normalize_v2_workspace_id(member.workspace_id) == member.workspace_id
    )


def exact_message_response(request, *, environment=None):
    environment = os.environ if environment is None else environment
    try:
        security.require_method(request.command, "POST")
        if request.path != ROUTE:
            return failure("source_invalid")
        headers = security.snapshot_request_headers(request)
        security.require_canonical_host(headers)
        security.require_same_origin(headers)
        require_safe_json_mutation(headers, environment)
        parsed = parse_request(request, headers)
        if parsed is None:
            return failure("source_invalid")
        mailbox_id, source = parsed
        owned = resolve_owned_managed_inbox_record(
            headers, mailbox_id, include_member_authority=True,
        )
        status = owned.get("status") if type(owned) is dict else None
        if status != "ok":
            return failure({"unauthorized": "authentication_required",
                            "not_found": "mailbox_not_found"}.get(status, "service_unavailable"))
        member, inbox = owned.get("memberAuthority"), owned.get("inbox")
        if (not valid_member(member) or type(inbox) is not dict
                or inbox.get("id") != mailbox_id
                or type(owned.get("user")) is not dict
                or owned["user"].get("email") != member.email):
            return failure("service_unavailable")
        if inbox.get("provider") != source["provider"]:
            return failure("provider_mismatch")
        decision = consume_exact_message_rate_limit(member, environment=environment)
        if decision.status == "limited":
            return failure("rate_limited", retry_after=decision.retry_after_seconds)
        if decision.status != "allowed":
            return failure("service_unavailable")
        if source["provider"] == "google":
            resolved = resolve_gmail_context(owned)
            if (type(resolved) is not dict or resolved.get("status") != "ok"
                    or type(resolved.get("context")) is not dict):
                return failure("service_unavailable")
            context = resolved["context"]
            if context.get("mailbox_id") != mailbox_id:
                return failure("invalid_response")
            result = provider.fetch_google_message(context, source)
        else:
            # Reuse the existing credential/version/network authority unchanged.
            # Its fresh account resolution must still match this request's scope.
            resolved = resolve_authenticated_imap_mailbox(
                headers, mailbox_id, include_member_authority=True,
            )
            if type(resolved) is not dict or resolved.get("status") != "ok":
                return failure("service_unavailable")
            current_member = resolved.get("memberAuthority")
            mailbox = resolved.get("mailbox")
            if (not valid_member(current_member) or current_member != member
                    or type(mailbox) is not dict or mailbox.get("mailboxId") != mailbox_id
                    or mailbox.get("ownerEmail") != member.email):
                return failure("forbidden")
            result = provider.fetch_imap_message(mailbox, source)
        if type(result) is not dict or result.get("status") != "ok":
            return failure(result.get("status") if type(result) is dict else "service_unavailable")
        message = provider.normalize_exact_message(result.get("message"), mailbox_id, source)
        if message is None:
            return failure("invalid_response")
        response = security.json_response(200, {
            "v": 1, "mailboxId": mailbox_id, "sourceRef": source, "message": message,
        })
        return response if len(response.body) <= MAX_RESPONSE_BYTES else failure("invalid_response")
    except (security.HttpBoundaryError, http_boundary.BoundaryError) as error:
        return failure({403: "forbidden", 405: "method_not_allowed", 413: "payload_too_large",
                        415: "unsupported_media_type"}.get(error.status, "source_invalid"))
    except Exception:
        return failure("service_unavailable")
