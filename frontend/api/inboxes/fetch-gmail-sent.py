"""On-demand Gmail Sent reads. No mailbox sync or sending entry points."""
import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

FRONTEND_DIR = Path(__file__).resolve().parents[2]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

from api.inboxes.authenticated_gmail import (
    MAX_GMAIL_RESPONSE_BYTES, error_payload, gmail_http_error_code,
    read_bounded_response, read_json_body, refresh_gmail_context,
    reject_unknown_fields, resolve_authenticated_gmail, send_json,
    send_method_not_allowed,
)
from api.inboxes.gmail_snapshot import read_gmail_folder_snapshot, valid_sent_page_token

GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"

def _gmail_request(
    access_token: str,
    path: str,
) -> tuple[dict | None, dict | None]:
    request = Request(
        f"{GMAIL_API_BASE_URL}{path}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=20) as response:
            body = read_bounded_response(response, MAX_GMAIL_RESPONSE_BYTES)
            if body is None:
                return None, {"code": "gmail_response_too_large"}
            if not body:
                return None, {"code": "gmail_response_invalid"}
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return None, {"code": "gmail_response_invalid"}
            if type(payload) is not dict:
                return None, {"code": "gmail_response_invalid"}
            return payload, None
    except HTTPError as error:
        return None, {
            "code": ("gmail_message_not_found" if error.code == 404 else
                     "invalid_page_token" if error.code == 400 else
                     gmail_http_error_code(error.code, "gmail_fetch_failed"))
        }
    except (URLError, TimeoutError):
        return None, {"code": "gmail_unavailable"}


def _request_with_one_refresh(context: dict, path: str):
    payload, error = _gmail_request(context["access_token"], path)
    if (
        error
        and error.get("code") == "gmail_token_invalid"
        and not context["refresh_attempted"]
    ):
        refreshed = refresh_gmail_context(context)
        if refreshed["status"] != "ok":
            return None, error, context, refreshed
        context = refreshed["context"]
        payload, error = _gmail_request(context["access_token"], path)
    return payload, error, context, None


def _send_gmail_error(handler: BaseHTTPRequestHandler, error: dict):
    code = error.get("code")
    mapping = {
        "invalid_page_token": (400, "invalid_page_token", "Sent pagination expired. Refresh Sent to continue."),
        "gmail_token_invalid": (
            401,
            "reconnect_required",
            "Reconnect this Gmail inbox to continue.",
        ),
        "gmail_permission_denied": (
            403,
            "gmail_permission_denied",
            "Gmail did not permit this operation.",
        ),
        "gmail_rate_limited": (
            502,
            "gmail_rate_limited",
            "Gmail is temporarily rate limited.",
        ),
        "gmail_unavailable": (
            502,
            "gmail_unavailable",
            "Gmail is temporarily unavailable.",
        ),
        "gmail_response_invalid": (
            502,
            "gmail_response_invalid",
            "Gmail returned an invalid response.",
        ),
        "gmail_response_too_large": (
            502,
            "gmail_response_too_large",
            "Gmail returned a response that is too large.",
        ),
    }
    status, safe_code, message = mapping.get(
        code,
        (
            502,
            "gmail_fetch_failed",
            "Gmail Sent could not be loaded.",
        ),
    )
    send_json(handler, status, error_payload(safe_code, message))


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            self._handle_post()
        except Exception:
            send_json(self, 500, error_payload("internal_error", "Gmail Sent could not be loaded."))

    def _handle_post(self):
        payload, error = read_json_body(self)
        if error:
            send_json(self, 413 if error["error"]["code"] == "request_too_large" else 400, error)
            return
        error = reject_unknown_fields(payload, {"mailboxId", "limit", "pageToken"})
        limit = payload.get("limit", 50)
        token = payload.get("pageToken")
        if error or type(limit) is not int or not 1 <= limit <= 100 or (
            "pageToken" in payload and not valid_sent_page_token(token)
        ):
            send_json(self, 400, error or error_payload("invalid_request", "Invalid Sent page parameters."))
            return
        resolution = resolve_authenticated_gmail(self.headers, payload.get("mailboxId"))
        if resolution["status"] != "ok":
            send_json(self, resolution["status_code"], resolution["error"])
            return
        result = read_gmail_folder_snapshot(
            resolution["context"], provider_folder="Sent", limit=limit,
            page_token=token, strict=True, request_with_one_refresh=_request_with_one_refresh,
            gmail_request=_gmail_request, refresh_context=refresh_gmail_context,
        )
        failure = result.get("refresh_failure")
        if failure:
            send_json(self, failure["status_code"], failure["error"])
            return
        if result.get("error"):
            _send_gmail_error(self, result["error"])
            return
        response = {"ok": True, "mailboxId": resolution["context"]["mailbox_id"], "folder": result["snapshot"]}
        if len(json.dumps(response).encode("utf-8")) > MAX_GMAIL_RESPONSE_BYTES:
            _send_gmail_error(self, {"code": "gmail_response_too_large"})
            return
        send_json(self, 200, response)

    def send_error(self, code, message=None, explain=None):
        if code == HTTPStatus.NOT_IMPLEMENTED:
            send_method_not_allowed(self, "Use POST for Gmail Sent reads.",
                                    write_body=getattr(self, "command", "") != "HEAD")
            return
        super().send_error(code, message, explain)

    def log_message(self, format, *args):
        pass
