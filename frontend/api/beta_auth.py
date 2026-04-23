import base64
import hashlib
import hmac
import json
import os
import re
import time
from http.cookies import SimpleCookie

BETA_SESSION_COOKIE_NAME = "cuevion_beta_session"
DEFAULT_BETA_SESSION_TTL_SECONDS = 7 * 24 * 60 * 60


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


def is_valid_auth_email(value: str) -> bool:
    return re.match(r"^[^\s@]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$", value.strip()) is not None


def normalize_auth_email(value: str) -> str:
    return value.strip().lower()


def resolve_beta_session_secret() -> str:
    return os.getenv("CUEVION_BETA_SESSION_SECRET", "").strip()


def resolve_beta_invite_codes() -> set[str]:
    configured_codes = os.getenv("CUEVION_BETA_INVITE_CODES", "").strip()
    fallback_code = os.getenv("CUEVION_BETA_INVITE_CODE", "").strip()
    codes = configured_codes or fallback_code
    return {code.strip() for code in codes.split(",") if code.strip()}


def resolve_allowed_beta_emails() -> set[str]:
    configured_emails = os.getenv("CUEVION_BETA_ALLOWED_EMAILS", "").strip()
    return {
        normalize_auth_email(email)
        for email in configured_emails.split(",")
        if normalize_auth_email(email)
    }


def build_beta_session_token(*, name: str, email: str) -> str:
    session_secret = resolve_beta_session_secret()
    issued_at = int(time.time())
    expires_at = issued_at + DEFAULT_BETA_SESSION_TTL_SECONDS
    payload = {
        "name": name.strip(),
        "email": normalize_auth_email(email),
        "userType": "member",
        "issued_at": issued_at,
        "expires_at": expires_at,
    }
    encoded_payload = _base64url_encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8"),
    )
    signature = _base64url_encode(
        hmac.new(
            session_secret.encode("utf-8"),
            encoded_payload.encode("utf-8"),
            hashlib.sha256,
        ).digest(),
    )
    return f"{encoded_payload}.{signature}"


def parse_beta_session_token(token: str) -> dict | None:
    session_secret = resolve_beta_session_secret()
    if not session_secret or not token or "." not in token:
        return None

    encoded_payload, signature = token.split(".", 1)
    expected_signature = _base64url_encode(
        hmac.new(
            session_secret.encode("utf-8"),
            encoded_payload.encode("utf-8"),
            hashlib.sha256,
        ).digest(),
    )
    if not hmac.compare_digest(signature, expected_signature):
        return None

    try:
        payload = json.loads(_base64url_decode(encoded_payload).decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None

    email = payload.get("email")
    name = payload.get("name")
    expires_at = payload.get("expires_at")

    if (
        not isinstance(email, str)
        or not is_valid_auth_email(email)
        or not isinstance(name, str)
        or not name.strip()
        or not isinstance(expires_at, int)
        or expires_at <= int(time.time())
    ):
        return None

    return {
        "email": normalize_auth_email(email),
        "name": name.strip(),
        "userType": "member",
    }


def read_beta_session_cookie(headers) -> str | None:
    raw_cookie = headers.get("cookie") or ""
    if not raw_cookie.strip():
        return None

    cookie = SimpleCookie()
    cookie.load(raw_cookie)
    morsel = cookie.get(BETA_SESSION_COOKIE_NAME)
    if morsel is None:
        return None

    return morsel.value.strip() or None


def _should_use_secure_cookie(headers) -> bool:
    forwarded_proto = (headers.get("x-forwarded-proto") or "").strip().lower()
    host = (headers.get("x-forwarded-host") or headers.get("host") or "").strip().lower()

    if forwarded_proto == "http":
        return False

    if host.startswith("localhost") or host.startswith("127.0.0.1"):
        return False

    return True


def build_beta_session_cookie(token: str, headers) -> str:
    cookie = SimpleCookie()
    cookie[BETA_SESSION_COOKIE_NAME] = token
    morsel = cookie[BETA_SESSION_COOKIE_NAME]
    morsel["path"] = "/"
    morsel["httponly"] = True
    morsel["samesite"] = "Lax"
    morsel["max-age"] = str(DEFAULT_BETA_SESSION_TTL_SECONDS)
    if _should_use_secure_cookie(headers):
        morsel["secure"] = True
    return morsel.OutputString()


def build_beta_session_logout_cookie(headers) -> str:
    cookie = SimpleCookie()
    cookie[BETA_SESSION_COOKIE_NAME] = ""
    morsel = cookie[BETA_SESSION_COOKIE_NAME]
    morsel["path"] = "/"
    morsel["httponly"] = True
    morsel["samesite"] = "Lax"
    morsel["max-age"] = "0"
    morsel["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
    if _should_use_secure_cookie(headers):
        morsel["secure"] = True
    return morsel.OutputString()
