"""Dedicated user/workspace cold-open budget, using the existing GCRA engine."""

from __future__ import annotations

import hashlib
import hmac
import json

from api.auth.runtime import AuthenticatedMemberContext
from api.collaboration import owner_rate_limit as engine
from api.collaboration.models import normalize_v2_user_id, normalize_v2_workspace_id
from api.collaboration.redis_store import V2_KEY_PREFIX, _v2_eval


POLICY = engine.OwnerRateLimitPolicy("fetch_exact_message", 2_000_000, 10)
DOMAIN = "cuevion/inboxes/fetch-exact-message/rate-limit/v1"


def build_rate_limit_key(member, configuration):
    if (type(member) is not AuthenticatedMemberContext
            or normalize_v2_user_id(member.user_id) != member.user_id
            or normalize_v2_workspace_id(member.workspace_id) != member.workspace_id):
        return None
    try:
        config = engine._require_configuration(configuration)
        identity = json.dumps(
            [DOMAIN, member.user_id, member.workspace_id, POLICY.name],
            ensure_ascii=True, separators=(",", ":"),
        ).encode("ascii")
        digest = hmac.new(config._hmac_key, identity, hashlib.sha256).hexdigest()
        return f"{V2_KEY_PREFIX}:inbox-exact-rate:{digest}"
    except Exception:
        return None


def consume_exact_message_rate_limit(member, *, environment, command_transport=None):
    try:
        configuration = engine.parse_owner_rate_limit_configuration({
            key: environment[key] for key in engine.RATE_LIMIT_CONFIGURATION_NAMES
            if key in environment
        })
        key = build_rate_limit_key(member, configuration)
        if key is None:
            return engine.OwnerRateLimitDecision("unavailable")
        result = _v2_eval(
            ["EVAL", engine._OWNER_RATE_LIMIT_LUA, 1, key,
             str(POLICY.emission_interval_microseconds), str(POLICY.burst),
             str(engine._MAX_RATE_LIMIT_RECORD_BYTES)],
            command_transport,
            response_shapes={"allowed": set(), "limited": {"retryAfter"},
                             "malformed": set(), "unavailable": set()},
        )
        if result.get("status") == "allowed":
            return engine.OwnerRateLimitDecision("allowed")
        retry = result.get("retryAfter")
        if (result.get("status") == "limited" and type(retry) is str
                and retry.isascii() and retry.isdigit() and 1 <= len(retry) <= 2
                and str(int(retry)) == retry and 1 <= int(retry) <= 60):
            return engine.OwnerRateLimitDecision("limited", int(retry))
    except Exception:
        pass
    return engine.OwnerRateLimitDecision("unavailable")
