"""Bounded notification GCRA policies using the existing Redis limiter."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping

from api.auth.runtime import AuthenticatedMemberContext
from api.collaboration import owner_rate_limit as existing
from api.collaboration.models import normalize_v2_user_id, normalize_v2_workspace_id
from api.collaboration.redis_store import V2_KEY_PREFIX, _v2_eval


# Reads: 60/minute sustained, 20 immediate. Exact writes: 30/minute, 10 immediate.
POLICIES = {
    "summary": existing.OwnerRateLimitPolicy("summary", 1_000_000, 20),
    "list": existing.OwnerRateLimitPolicy("list", 1_000_000, 20),
    "mark_read": existing.OwnerRateLimitPolicy("mark_read", 2_000_000, 10),
}
_DOMAIN = "cuevion/notifications/rate-limit/v1"


def build_notification_rate_limit_key(
    member: object, operation: object, configuration: object,
) -> str | None:
    if (
        type(member) is not AuthenticatedMemberContext
        or normalize_v2_user_id(member.user_id) != member.user_id
        or normalize_v2_workspace_id(member.workspace_id) != member.workspace_id
        or type(operation) is not str
        or operation not in POLICIES
    ):
        return None
    try:
        parsed = existing._require_configuration(configuration)
        identity = json.dumps(
            {
                "domain": _DOMAIN,
                "operation": operation,
                "userId": member.user_id,
                "workspaceId": member.workspace_id,
            },
            allow_nan=False, ensure_ascii=True, sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        digest = hmac.new(parsed._hmac_key, identity, hashlib.sha256).hexdigest()
        return f"{V2_KEY_PREFIX}:notification-rate:{operation}:{digest}"
    except Exception:
        return None


def consume_notification_rate_limit(
    member: object,
    operation: object,
    *,
    environment: Mapping[str, str],
    command_transport=None,
) -> existing.OwnerRateLimitDecision:
    try:
        configuration = existing.parse_owner_rate_limit_configuration({
            name: environment[name]
            for name in existing.RATE_LIMIT_CONFIGURATION_NAMES
            if name in environment
        })
        key = build_notification_rate_limit_key(member, operation, configuration)
        if key is None:
            return existing.OwnerRateLimitDecision("unavailable")
        policy = POLICIES[operation]
        result = _v2_eval(
            [
                "EVAL", existing._OWNER_RATE_LIMIT_LUA, 1, key,
                str(policy.emission_interval_microseconds), str(policy.burst),
                str(existing._MAX_RATE_LIMIT_RECORD_BYTES),
            ],
            command_transport,
            response_shapes={
                "allowed": set(), "limited": {"retryAfter"},
                "malformed": set(), "unavailable": set(),
            },
        )
    except Exception:
        return existing.OwnerRateLimitDecision("unavailable")
    if result.get("status") == "allowed":
        return existing.OwnerRateLimitDecision("allowed")
    if result.get("status") == "limited":
        retry = result.get("retryAfter")
        if (type(retry) is str and retry.isascii() and retry.isdigit()
                and 1 <= len(retry) <= 2 and str(int(retry)) == retry
                and 1 <= int(retry) <= 60):
            return existing.OwnerRateLimitDecision("limited", int(retry))
    return existing.OwnerRateLimitDecision("unavailable")
