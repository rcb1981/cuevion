"""Authenticated read-only Gmail Drafts list/detail endpoint."""
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parents[2]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

from api.inboxes.authenticated_gmail import read_json_body, reject_unknown_fields, send_json, error_payload, send_method_not_allowed, valid_identifier
from api.inboxes.gmail_drafts import DraftReadError, MAX_PAGE, identifier, page_token, read_drafts


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            self._handle_post()
        except DraftReadError as error:
            send_json(self, error.status, error_payload(error.code, "Gmail drafts could not be loaded. Refresh Drafts or check the mailbox connection."))
        except Exception:
            send_json(self, 500, error_payload("internal_error", "Gmail drafts could not be loaded."))

    def _handle_post(self):
        payload, error = read_json_body(self)
        if error:
            send_json(self, 413 if error["error"]["code"] == "request_too_large" else 400, error)
            return
        error = reject_unknown_fields(payload, {"mailboxId", "limit", "pageToken", "draftId", "scope"})
        limit, token, draft_id, scope = payload.get("limit", MAX_PAGE), payload.get("pageToken"), payload.get("draftId"), payload.get("scope")
        if (error or not valid_identifier(payload.get("mailboxId")) or type(limit) is not int or not 1 <= limit <= MAX_PAGE
                or ("pageToken" in payload and (not page_token(token) or scope is None))
                or ("draftId" in payload and (not identifier(draft_id) or "pageToken" in payload or "limit" in payload or scope is None))
                or ("scope" in payload and (type(scope) is not str or len(scope) != 64 or any(c not in "0123456789abcdef" for c in scope)))):
            send_json(self, 400, error_payload("invalid_request", "Invalid Gmail Drafts request."))
            return
        send_json(self, 200, read_drafts(self.headers, payload["mailboxId"], limit=limit, token=token, draft_id=draft_id, scope=scope))

    def send_error(self, code, message=None, explain=None):
        if code == HTTPStatus.NOT_IMPLEMENTED:
            send_method_not_allowed(self, "Use POST for Gmail Drafts reads.", write_body=getattr(self, "command", "") != "HEAD")
            return
        super().send_error(code, message, explain)

    def log_message(self, format, *args):
        pass
