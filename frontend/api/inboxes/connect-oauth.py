import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlencode

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from user_config_store import (  # noqa: E402
    read_user_config_record,
    resolve_authenticated_user,
    resolve_user_config_store,
)
from api.auth.email_address import normalize_auth_email  # noqa: E402
from api.user.config import _classify_stored_onboarding_session  # noqa: E402

GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
MICROSOFT_AUTHORIZATION_ENDPOINT_TEMPLATE = (
    "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
)
DEFAULT_GOOGLE_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]
DEFAULT_MICROSOFT_SCOPES = [
    "openid",
    "email",
    "profile",
    "offline_access",
    "https://graph.microsoft.com/Mail.Read",
]
MAX_REQUEST_BODY_BYTES = 16 * 1024
OAUTH_STATE_VERSION = 1
OAUTH_STATE_TTL_SECONDS = 15 * 60
STATE_SIGNATURE_DOMAIN = "cuevion-oauth-state-signature:v1"
OWNER_BINDING_DOMAIN = "cuevion-oauth-owner-binding:v1"
PKCE_DERIVATION_DOMAIN = "cuevion-oauth-pkce:v1"
ONBOARDING_CONFLICT_ERROR = {
    "ok": False,
    "error": {
        "code": "onboarding_state_conflict",
        "message": "The selected onboarding inbox is no longer available. Reload and try again.",
    },
}


def base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def build_owner_binding(
    *,
    owner_email: str,
    provider: str,
    email_hint: str,
    nonce: str,
    issued_at: int,
    expires_at: int,
    signing_secret: str,
    inbox_position: str | None = None,
) -> str:
    normalized_owner = normalize_auth_email(owner_email)
    binding_fields = [
        OWNER_BINDING_DOMAIN,
        str(OAUTH_STATE_VERSION),
        normalized_owner,
        provider,
        email_hint,
    ]
    if inbox_position is not None:
        binding_fields.append(inbox_position)
    binding_fields.extend((nonce, str(issued_at), str(expires_at)))
    binding_message = "\n".join(binding_fields)
    return base64url_encode(
        hmac.new(
            signing_secret.encode("utf-8"),
            binding_message.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    )


def build_signed_state(
    provider: str,
    email_hint: str,
    owner_email: str,
    signing_secret: str,
    inbox_position: str | None = None,
) -> tuple[str, str]:
    issued_at = int(time.time())
    expires_at = issued_at + OAUTH_STATE_TTL_SECONDS
    nonce = secrets.token_urlsafe(16)
    state_payload = {
        "v": OAUTH_STATE_VERSION,
        "provider": provider,
        "email_hint": email_hint,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "nonce": nonce,
        "owner_binding": build_owner_binding(
            owner_email=owner_email,
            provider=provider,
            email_hint=email_hint,
            nonce=nonce,
            issued_at=issued_at,
            expires_at=expires_at,
            signing_secret=signing_secret,
            inbox_position=inbox_position,
        ),
    }
    if inbox_position is not None:
        state_payload["inboxPosition"] = inbox_position
    encoded_payload = base64url_encode(
        json.dumps(state_payload, separators=(",", ":"), sort_keys=True).encode("utf-8"),
    )
    signature = base64url_encode(
        hmac.new(
            signing_secret.encode("utf-8"),
            f"{STATE_SIGNATURE_DOMAIN}:{encoded_payload}".encode("utf-8"),
            hashlib.sha256,
        ).digest(),
    )
    code_verifier = base64url_encode(
        hmac.new(
            signing_secret.encode("utf-8"),
            f"{PKCE_DERIVATION_DOMAIN}:{encoded_payload}".encode("utf-8"),
            hashlib.sha256,
        ).digest(),
    )
    return f"{encoded_payload}.{signature}", code_verifier


def build_code_challenge(code_verifier: str) -> str:
    return base64url_encode(
        hashlib.sha256(code_verifier.encode("utf-8")).digest(),
    )


def resolve_google_scopes() -> list[str]:
    configured_scopes = os.getenv("GOOGLE_OAUTH_SCOPES", "").strip()
    if not configured_scopes:
        return DEFAULT_GOOGLE_SCOPES

    return [scope for scope in configured_scopes.split() if scope]


def resolve_microsoft_scopes() -> list[str]:
    configured_scopes = os.getenv("MICROSOFT_OAUTH_SCOPES", "").strip()
    if not configured_scopes:
        return DEFAULT_MICROSOFT_SCOPES

    return [scope for scope in configured_scopes.split() if scope]


def resolve_authoritative_onboarding_position(
    session_user: dict,
    inbox_position: str,
) -> tuple[str | None, int | None, dict | None]:
    store, store_error = resolve_user_config_store()
    if store_error or not store:
        return None, 503, {
            "ok": False,
            "error": {
                "code": "user_config_store_unavailable",
                "message": "User config storage is temporarily unavailable.",
            },
        }

    read_result = read_user_config_record(store, session_user["email"])
    if read_result["status"] == "unavailable":
        return None, 503, {
            "ok": False,
            "error": {
                "code": "user_config_store_unavailable",
                "message": "User config storage is temporarily unavailable.",
            },
        }
    if read_result["status"] != "ok" or not isinstance(read_result["config"], dict):
        return None, 409, ONBOARDING_CONFLICT_ERROR

    config = read_result["config"]
    stored_owner = config.get("email")
    if stored_owner is not None and (
        not isinstance(stored_owner, str)
        or normalize_auth_email(stored_owner)
        != normalize_auth_email(session_user["email"])
    ):
        return None, 409, ONBOARDING_CONFLICT_ERROR

    session_state, normalized_session = _classify_stored_onboarding_session(
        config.get("onboardingSession")
    )
    if (
        getattr(session_state, "value", None) != "valid"
        or not isinstance(normalized_session, dict)
        or normalized_session.get("schemaVersion") != 1
        or normalized_session.get("completed") is not False
    ):
        return None, 409, ONBOARDING_CONFLICT_ERROR

    choices = normalized_session.get("choices")
    selected_inboxes = choices.get("selectedInboxes") if isinstance(choices, dict) else None
    if not isinstance(selected_inboxes, list) or inbox_position not in selected_inboxes:
        return None, 409, ONBOARDING_CONFLICT_ERROR

    return inbox_position, None, None


class handler(BaseHTTPRequestHandler):
    def send_error(self, code, message=None, explain=None):
        if code == HTTPStatus.NOT_IMPLEMENTED:
            self.close_connection = True
            self._send_json(
                405,
                {"ok": False, "error": {"code": "method_not_allowed", "message": "Use POST to start inbox authentication"}},
                write_body=getattr(self, "command", "") != "HEAD",
            )
            return
        super().send_error(code, message, explain)

    def _send_json(self, status_code: int, payload: dict, *, write_body: bool = True):
        response_body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        if write_body:
            self.wfile.write(response_body)

    def do_POST(self):
        try:
            session_user, auth_error = resolve_authenticated_user(self.headers)
            if not session_user:
                if auth_error and auth_error.get("code") == "session_auth_unavailable":
                    self._send_json(
                        503,
                        {"ok": False, "error": {"code": "session_auth_unavailable", "message": "Authentication is temporarily unavailable."}},
                    )
                else:
                    self._send_json(
                        401,
                        {"ok": False, "error": {"code": "unauthorized", "message": "A valid member session is required."}},
                    )
                return

            try:
                content_length = int(self.headers.get("content-length", "0"))
            except (TypeError, ValueError):
                self._send_json(400, {"ok": False, "error": {"code": "invalid_request", "message": "Content-Length must be valid."}})
                return
            if content_length < 0 or content_length > MAX_REQUEST_BODY_BYTES:
                self._send_json(400, {"ok": False, "error": {"code": "invalid_request", "message": "Request body size is invalid."}})
                return
            raw_body = (
                self.rfile.read(content_length)
                if content_length > 0
                else b""
            )

            try:
                payload = json.loads(raw_body.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json(
                    400,
                    {
                        "ok": False,
                        "error": {
                            "code": "invalid_request",
                            "message": "Request body must be valid JSON",
                        },
                    },
                )
                return
            if not isinstance(payload, dict) or set(payload) - {
                "provider",
                "email",
                "inboxPosition",
            }:
                self._send_json(400, {"ok": False, "error": {"code": "invalid_request", "message": "Request body contains unsupported fields."}})
                return

            provider = payload.get("provider")
            raw_email = payload.get("email", "")
            email = raw_email.strip().lower() if isinstance(raw_email, str) else ""
            inbox_position = payload.get("inboxPosition")
            has_inbox_position = "inboxPosition" in payload
            email_pattern = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

            if not isinstance(provider, str) or provider not in ("google", "microsoft"):
                self._send_json(
                    400,
                    {
                        "ok": False,
                        "error": {
                            "code": "unsupported_provider",
                            "message": "OAuth is not configured for this provider.",
                        },
                    },
                )
                return

            if "email" in payload and not isinstance(raw_email, str):
                self._send_json(
                    400,
                    {
                        "ok": False,
                        "error": {
                            "code": "invalid_request",
                            "message": "Email hint must be a string.",
                        },
                    },
                )
                return

            if email and not email_pattern.match(email):
                email_message = (
                    "Email hint must be a valid Gmail or Google Workspace address."
                    if provider == "google"
                    else "Email hint must be a valid Microsoft 365 or Outlook address."
                )
                self._send_json(
                    400,
                    {
                        "ok": False,
                        "error": {
                            "code": "invalid_request",
                            "message": email_message,
                        },
                    },
                )
                return

            if has_inbox_position:
                if provider != "google" or not isinstance(inbox_position, str):
                    self._send_json(
                        400,
                        {
                            "ok": False,
                            "error": {
                                "code": "invalid_request",
                                "message": "Onboarding inbox position is invalid.",
                            },
                        },
                    )
                    return
                (
                    inbox_position,
                    onboarding_error_status,
                    onboarding_error_payload,
                ) = resolve_authoritative_onboarding_position(
                    session_user,
                    inbox_position,
                )
                if onboarding_error_status is not None and onboarding_error_payload:
                    self._send_json(
                        onboarding_error_status,
                        onboarding_error_payload,
                    )
                    return

            if provider == "google":
                client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
                client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
                redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
                oauth_state_secret = (
                    os.getenv("CUEVION_OAUTH_STATE_SECRET", "").strip() or client_secret
                )
            else:
                client_id = os.getenv("MICROSOFT_CLIENT_ID", "").strip()
                client_secret = os.getenv("MICROSOFT_CLIENT_SECRET", "").strip()
                redirect_uri = os.getenv("MICROSOFT_OAUTH_REDIRECT_URI", "").strip()
                oauth_state_secret = (
                    os.getenv("CUEVION_OAUTH_STATE_SECRET", "").strip() or client_secret
                )

            if not client_id or not client_secret or not redirect_uri:
                config_message = (
                    "Google OAuth is not configured. Set GOOGLE_CLIENT_ID, "
                    "GOOGLE_CLIENT_SECRET, and GOOGLE_OAUTH_REDIRECT_URI."
                    if provider == "google"
                    else "Microsoft OAuth is not configured. Set MICROSOFT_CLIENT_ID, "
                    "MICROSOFT_CLIENT_SECRET, and MICROSOFT_OAUTH_REDIRECT_URI."
                )
                self._send_json(
                    503,
                    {
                        "ok": False,
                        "error": {
                            "code": "oauth_not_configured",
                            "message": config_message,
                        },
                    },
                )
                return

            if not redirect_uri.startswith(("https://", "http://")):
                redirect_message = (
                    "GOOGLE_OAUTH_REDIRECT_URI must be an absolute URL."
                    if provider == "google"
                    else "MICROSOFT_OAUTH_REDIRECT_URI must be an absolute URL."
                )
                self._send_json(
                    503,
                    {
                        "ok": False,
                        "error": {
                            "code": "oauth_invalid_redirect_uri",
                            "message": redirect_message,
                        },
                    },
                )
                return

            authorization_state, code_verifier = build_signed_state(
                provider,
                email,
                session_user["email"],
                oauth_state_secret,
                inbox_position,
            )
            if provider == "google":
                authorization_params = {
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "scope": " ".join(resolve_google_scopes()),
                    "access_type": "offline",
                    "include_granted_scopes": "true",
                    "prompt": "consent",
                    "state": authorization_state,
                    "code_challenge": build_code_challenge(code_verifier),
                    "code_challenge_method": "S256",
                }
                if email:
                    authorization_params["login_hint"] = email
                authorization_url = (
                    f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{urlencode(authorization_params)}"
                )
                message = "Continue with Google to finish authentication."
            else:
                microsoft_tenant = os.getenv("MICROSOFT_OAUTH_TENANT", "").strip() or "common"
                authorization_params = {
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "response_mode": "query",
                    "scope": " ".join(resolve_microsoft_scopes()),
                    "prompt": "select_account",
                    "state": authorization_state,
                    "code_challenge": build_code_challenge(code_verifier),
                    "code_challenge_method": "S256",
                }
                if email:
                    authorization_params["login_hint"] = email
                authorization_url = (
                    f"{MICROSOFT_AUTHORIZATION_ENDPOINT_TEMPLATE.format(tenant=microsoft_tenant)}"
                    f"?{urlencode(authorization_params)}"
                )
                message = "Continue with Microsoft to finish authentication."

            self._send_json(
                200,
                {
                    "ok": True,
                    "connectionMethod": "oauth",
                    "connectionStatus": "waiting_for_authentication",
                    "authorizationUrl": authorization_url,
                    "message": message,
                },
            )
        except Exception:
            self._send_json(
                500,
                {
                    "ok": False,
                    "error": {
                        "code": "server_error",
                        "message": "OAuth could not be started.",
                    },
                },
            )

    def do_GET(self):
        self._send_json(
            405,
            {
                "ok": False,
                "error": {
                    "code": "method_not_allowed",
                    "message": "Use POST to start inbox authentication",
                },
            },
        )

    def do_OPTIONS(self):
        self._send_json(200, {"ok": True})

    def do_PUT(self):
        self.do_GET()

    def do_PATCH(self):
        self.do_GET()

    def do_DELETE(self):
        self.do_GET()

    def do_HEAD(self):
        self._send_json(
            405,
            {"ok": False, "error": {"code": "method_not_allowed", "message": "Use POST to start inbox authentication"}},
            write_body=False,
        )

    def log_message(self, format, *args):
        return
