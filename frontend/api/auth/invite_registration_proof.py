"""Signed, short-lived Team Invite registration proof for Auth0 pre-registration.

The proof is transported only in OIDC ``acr_values`` for invite-bound database
signup.  It binds the Cuevion client, normalized invitee email, canonical Team
invitation id/digest and a short expiry.  The Auth0 Action validates the HMAC
before permitting database registration; Cuevion still re-proves the live Team
invitation during callback before provisioning any account authority.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Mapping

from api.auth.email_address import is_valid_auth_email, normalize_auth_email


ENV_NAME = "CUEVION_AUTH_INVITE_REGISTRATION_SECRET"
ACR_PREFIX = "urn:cuevion:invite-registration:v1:"
MAX_TTL_SECONDS = 10 * 60
_MIN_SECRET_BYTES = 32
_INVITATION_ID_RE = re.compile(r"tinv_[A-Za-z0-9_-]{1,64}")
_TOKEN_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_CLIENT_ID_RE = re.compile(r"[!-~]{1,512}")
_BASE64URL_RE = re.compile(r"[A-Za-z0-9_-]+")


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _secret(environment: Mapping[str, str]) -> bytes:
    value = environment.get(ENV_NAME)
    if type(value) is not str or value != value.strip():
        raise ValueError("invalid invite registration configuration")
    encoded = value.encode("utf-8", errors="strict")
    if not _MIN_SECRET_BYTES <= len(encoded) <= 4096:
        raise ValueError("invalid invite registration configuration")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("invalid invite registration configuration")
    return encoded


def build_invite_registration_acr(
    environment: Mapping[str, str],
    *,
    client_id: str,
    email: str,
    invitation_id: str,
    token_digest: str,
    now: int,
    invitation_expires_at_ms: int,
) -> str:
    """Return one bounded ACR value proving server-authorized invite signup."""

    normalized_email = normalize_auth_email(email)
    if (
        type(client_id) is not str
        or _CLIENT_ID_RE.fullmatch(client_id) is None
        or not is_valid_auth_email(normalized_email)
        or type(invitation_id) is not str
        or _INVITATION_ID_RE.fullmatch(invitation_id) is None
        or type(token_digest) is not str
        or _TOKEN_DIGEST_RE.fullmatch(token_digest) is None
        or type(now) is not int
        or now < 0
        or type(invitation_expires_at_ms) is not int
        or invitation_expires_at_ms <= now * 1000
    ):
        raise ValueError("invalid invite registration proof input")

    expires_at = min(now + MAX_TTL_SECONDS, invitation_expires_at_ms // 1000)
    if expires_at <= now:
        raise ValueError("invalid invite registration proof input")

    payload = {
        "aud": client_id,
        "email": normalized_email,
        "exp": expires_at,
        "iat": now,
        "invitation_id": invitation_id,
        "token_digest": token_digest,
        "v": 1,
    }
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    payload_part = _b64url(encoded)
    signature = hmac.new(_secret(environment), encoded, hashlib.sha256).digest()
    proof = ACR_PREFIX + payload_part + "." + _b64url(signature)
    if len(proof) > 2048 or _BASE64URL_RE.fullmatch(payload_part) is None:
        raise ValueError("invalid invite registration proof")
    return proof
