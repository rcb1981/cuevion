import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from oauth_google import GOOGLE_TOKEN_ENDPOINT, verify_signed_state
from oauth_token_store import is_google_token_store_durable, persist_google_token_record

OAUTH_CALLBACK_RESULT_STORAGE_KEY = "cuevion-oauth-callback-result"


def _build_app_redirect_url(headers) -> str:
    configured_app_url = os.getenv("CUEVION_APP_URL", "").strip()
    if configured_app_url:
        return configured_app_url

    host = (
        headers.get("x-forwarded-host")
        or headers.get("host")
        or "localhost:3000"
    )
    protocol = headers.get("x-forwarded-proto")
    if not protocol:
        protocol = "http" if host.startswith(("localhost", "127.0.0.1")) else "https"

    return f"{protocol}://{host}/"


def _build_callback_payload(
    *,
    provider: str,
    email: str,
    connection_status: str,
    message: str,
    connected: bool,
) -> dict:
    return {
        "provider": provider,
        "email": email,
        "connectionMethod": "oauth",
        "connectionStatus": connection_status,
        "connected": connected,
        "message": message,
    }


def _render_callback_bridge_page(app_redirect_url: str, payload: dict) -> bytes:
    payload_json = json.dumps(payload).replace("</", "<\\/")
    redirect_json = json.dumps(app_redirect_url)
    storage_key_json = json.dumps(OAUTH_CALLBACK_RESULT_STORAGE_KEY)
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Cuevion Gmail Connection</title>
  </head>
  <body>
    <script>
      const payload = {payload_json};
      const redirectUrl = {redirect_json};
      window.localStorage.setItem({storage_key_json}, JSON.stringify(payload));
      window.location.replace(redirectUrl);
    </script>
    <p>Returning to Cuevion…</p>
  </body>
</html>
"""
    return html.encode("utf-8")


def _exchange_google_code(
    *,
    code: str,
    code_verifier: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> tuple[dict | None, dict | None]:
    request_payload = urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
    ).encode("utf-8")
    request = Request(
        GOOGLE_TOKEN_ENDPOINT,
        data=request_payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8")), None
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        try:
            parsed_error = json.loads(error_body) if error_body else {}
        except json.JSONDecodeError:
            parsed_error = {}
        return None, {
            "code": "token_exchange_failed",
            "message": (
                parsed_error.get("error_description")
                or parsed_error.get("error")
                or "Google token exchange failed."
            ),
        }
    except URLError as error:
        return None, {
            "code": "token_exchange_unavailable",
            "message": str(error.reason) if getattr(error, "reason", None) else "Could not reach Google.",
        }


class handler(BaseHTTPRequestHandler):
    def _send_callback_page(self, payload: dict):
        page = _render_callback_bridge_page(
            _build_app_redirect_url(self.headers),
            payload,
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def do_GET(self):
        parsed_url = urlparse(self.path)
        params = parse_qs(parsed_url.query)
        oauth_error = params.get("error", [None])[0]
        state = params.get("state", [None])[0]
        google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
        oauth_state_secret = (
            os.getenv("CUEVION_OAUTH_STATE_SECRET", "").strip() or google_client_secret
        )

        state_payload, state_error = verify_signed_state(
            state or "",
            oauth_state_secret,
        )

        provider = "google"
        email = (
            state_payload.get("email", "")
            if state_payload is not None
            else ""
        )

        if state_error:
            self._send_callback_page(
                _build_callback_payload(
                    provider=provider,
                    email=email,
                    connection_status="connection_failed",
                    message="Google authentication could not be verified. Please try again.",
                    connected=False,
                )
            )
            return

        if oauth_error:
            self._send_callback_page(
                _build_callback_payload(
                    provider=provider,
                    email=email,
                    connection_status="connection_failed",
                    message="Google authentication was cancelled or denied.",
                    connected=False,
                )
            )
            return

        authorization_code = params.get("code", [None])[0]
        if not authorization_code:
            self._send_callback_page(
                _build_callback_payload(
                    provider=provider,
                    email=email,
                    connection_status="connection_failed",
                    message="Google did not return an authorization code.",
                    connected=False,
                )
            )
            return

        google_client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
        google_redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip()

        if not google_client_id or not google_client_secret or not google_redirect_uri:
            self._send_callback_page(
                _build_callback_payload(
                    provider=provider,
                    email=email,
                    connection_status="connection_failed",
                    message="Google OAuth callback is not fully configured.",
                    connected=False,
                )
            )
            return

        token_payload, token_error = _exchange_google_code(
            code=authorization_code,
            code_verifier=state_payload["code_verifier"],
            client_id=google_client_id,
            client_secret=google_client_secret,
            redirect_uri=google_redirect_uri,
        )

        if token_error:
            self._send_callback_page(
                _build_callback_payload(
                    provider=provider,
                    email=email,
                    connection_status="connection_failed",
                    message=token_error["message"],
                    connected=False,
                )
            )
            return

        if not token_payload or not token_payload.get("access_token"):
            self._send_callback_page(
                _build_callback_payload(
                    provider=provider,
                    email=email,
                    connection_status="connection_failed",
                    message="Google returned an incomplete token response.",
                    connected=False,
                )
            )
            return

        persisted_record, persistence_error = persist_google_token_record(
            email=email,
            token_payload=token_payload,
        )

        if persistence_error:
            self._send_callback_page(
                _build_callback_payload(
                    provider=provider,
                    email=email,
                    connection_status="authenticated_pending_activation",
                    message=(
                        persistence_error["message"]
                        or "Google authentication completed. Tokens are stored only in the current server runtime. Final mailbox activation requires durable secure mailbox token storage."
                    ),
                    connected=False,
                )
            )
            return

        if not persisted_record:
            self._send_callback_page(
                _build_callback_payload(
                    provider=provider,
                    email=email,
                    connection_status="authenticated_pending_activation",
                    message="Google authentication completed. Tokens are stored only in the current server runtime. Final mailbox activation requires durable secure mailbox token storage.",
                    connected=False,
                )
            )
            return

        if not is_google_token_store_durable():
            self._send_callback_page(
                _build_callback_payload(
                    provider=provider,
                    email=email,
                    connection_status="authenticated_pending_activation",
                    message=(
                        "Google authentication completed. Tokens are stored only in the current server runtime. "
                        "Final mailbox activation requires durable secure mailbox token storage."
                    ),
                    connected=False,
                )
            )
            return

        self._send_callback_page(
            _build_callback_payload(
                provider=provider,
                email=email,
                connection_status="connected",
                message="Google account connected. Durable mailbox token storage is active.",
                connected=True,
            )
        )

    def do_POST(self):
        self.send_response(405)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "method_not_allowed",
                        "message": "Use GET for Google OAuth callbacks",
                    },
                }
            ).encode("utf-8")
        )

    def log_message(self, format, *args):
        return
