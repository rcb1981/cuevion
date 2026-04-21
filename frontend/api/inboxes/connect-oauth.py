import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlencode

GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
DEFAULT_GOOGLE_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _build_signed_state(provider: str, email: str, signing_secret: str) -> tuple[str, str]:
    code_verifier = _base64url_encode(secrets.token_bytes(48))
    state_payload = {
        "provider": provider,
        "email": email,
        "issued_at": int(time.time()),
        "nonce": secrets.token_urlsafe(16),
        "code_verifier": code_verifier,
    }
    encoded_payload = _base64url_encode(
        json.dumps(state_payload, separators=(",", ":")).encode("utf-8"),
    )
    signature = _base64url_encode(
        hmac.new(
            signing_secret.encode("utf-8"),
            encoded_payload.encode("utf-8"),
            hashlib.sha256,
        ).digest(),
    )
    return f"{encoded_payload}.{signature}", code_verifier


def _build_code_challenge(code_verifier: str) -> str:
    return _base64url_encode(
        hashlib.sha256(code_verifier.encode("utf-8")).digest(),
    )


def _resolve_google_scopes() -> list[str]:
    configured_scopes = os.getenv("GOOGLE_OAUTH_SCOPES", "").strip()
    if not configured_scopes:
        return DEFAULT_GOOGLE_SCOPES

    return [scope for scope in configured_scopes.split() if scope]


class handler(BaseHTTPRequestHandler):
    def _send_json(self, status_code: int, payload: dict):
        response_body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def do_POST(self):
        content_length = int(self.headers.get("content-length", "0"))
        raw_body = (
            self.rfile.read(content_length).decode("utf-8")
            if content_length > 0
            else ""
        )

        try:
            payload = json.loads(raw_body or "{}")
        except json.JSONDecodeError:
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

        provider = payload.get("provider")
        email = str(payload.get("email", "")).strip().lower()

        if provider != "google":
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

        google_client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
        google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
        google_redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
        oauth_state_secret = (
            os.getenv("CUEVION_OAUTH_STATE_SECRET", "").strip() or google_client_secret
        )

        if not google_client_id or not google_client_secret or not google_redirect_uri:
            self._send_json(
                503,
                {
                    "ok": False,
                    "error": {
                        "code": "oauth_not_configured",
                        "message": (
                            "Google OAuth is not configured. Set GOOGLE_CLIENT_ID, "
                            "GOOGLE_CLIENT_SECRET, and GOOGLE_OAUTH_REDIRECT_URI."
                        ),
                    },
                },
            )
            return

        if not google_redirect_uri.startswith(("https://", "http://")):
            self._send_json(
                503,
                {
                    "ok": False,
                    "error": {
                        "code": "oauth_invalid_redirect_uri",
                        "message": "GOOGLE_OAUTH_REDIRECT_URI must be an absolute URL.",
                    },
                },
            )
            return

        authorization_state, code_verifier = _build_signed_state(
            provider,
            email,
            oauth_state_secret,
        )
        authorization_params = {
            "client_id": google_client_id,
            "redirect_uri": google_redirect_uri,
            "response_type": "code",
            "scope": " ".join(_resolve_google_scopes()),
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "login_hint": email,
            "state": authorization_state,
            "code_challenge": _build_code_challenge(code_verifier),
            "code_challenge_method": "S256",
        }
        authorization_url = (
            f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{urlencode(authorization_params)}"
        )

        self._send_json(
            200,
            {
                "ok": True,
                "connectionMethod": "oauth",
                "connectionStatus": "waiting_for_authentication",
                "authorizationUrl": authorization_url,
                "message": "Continue with Google to finish authentication.",
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

    def log_message(self, format, *args):
        return
