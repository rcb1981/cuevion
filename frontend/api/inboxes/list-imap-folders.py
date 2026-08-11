import sys as _identity_sys

_CANONICAL_MODULE_NAME = "api.inboxes.list-imap-folders"
_LEGACY_MODULE_NAME = "list-imap-folders"
_CANONICAL_PACKAGE_NAME = "api.inboxes"

_loaded_module_name = __name__
_current_module = _identity_sys.modules[_loaded_module_name]
if _loaded_module_name not in {_CANONICAL_MODULE_NAME, _LEGACY_MODULE_NAME}:
    raise ImportError(
        "IMAP folder-list route helpers must be imported as "
        + _CANONICAL_MODULE_NAME
    )
_existing_canonical = _identity_sys.modules.get(_CANONICAL_MODULE_NAME)
_existing_legacy = _identity_sys.modules.get(_LEGACY_MODULE_NAME)
if (
    _existing_canonical is not None
    and _existing_canonical is not _current_module
) or (
    _existing_legacy is not None
    and _existing_legacy is not _current_module
):
    raise ImportError(
        "canonical and legacy IMAP folder-list route identities cannot coexist"
    )
if _loaded_module_name == _LEGACY_MODULE_NAME:
    _current_module.__name__ = _CANONICAL_MODULE_NAME
    _current_module.__package__ = _CANONICAL_PACKAGE_NAME
_identity_sys.modules[_CANONICAL_MODULE_NAME] = _current_module
_identity_sys.modules[_LEGACY_MODULE_NAME] = _current_module

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler

from .authenticated_gmail import (
    error_payload,
    read_json_body,
    reject_unknown_fields,
    send_json,
    send_method_not_allowed,
    valid_identifier,
)
from .imap_folder_mapping_api import list_imap_folders


class handler(BaseHTTPRequestHandler):
    def send_error(self, code, message=None, explain=None):
        if code == HTTPStatus.NOT_IMPLEMENTED:
            self.close_connection = True
            send_method_not_allowed(
                self,
                "Use POST to list IMAP folders.",
                write_body=getattr(self, "command", "") != "HEAD",
            )
            return
        super().send_error(code, message, explain)

    def do_POST(self):
        try:
            handler._handle_post(self)
        except Exception:
            send_json(
                self,
                500,
                error_payload(
                    "internal_error",
                    "The IMAP folder-list request could not be completed.",
                ),
            )

    def _handle_post(self):
        payload, request_error = read_json_body(self)
        if request_error:
            send_json(
                self,
                413
                if request_error["error"]["code"] == "request_too_large"
                else 400,
                request_error,
            )
            return
        if (
            reject_unknown_fields(payload, {"mailboxId"})
            or set(payload) != {"mailboxId"}
            or not valid_identifier(payload.get("mailboxId"))
        ):
            send_json(
                self,
                400,
                error_payload(
                    "invalid_request",
                    "Folder listing requires one managed mailbox.",
                ),
            )
            return
        status_code, response = list_imap_folders(
            self.headers,
            payload["mailboxId"],
        )
        send_json(self, status_code, response)

    def do_GET(self):
        send_method_not_allowed(self, "Use POST to list IMAP folders.")

    def do_PUT(self):
        self.do_GET()

    def do_PATCH(self):
        self.do_GET()

    def do_DELETE(self):
        self.do_GET()

    def do_HEAD(self):
        send_method_not_allowed(
            self,
            "Use POST to list IMAP folders.",
            write_body=False,
        )

    def do_OPTIONS(self):
        send_json(self, 200, {"ok": True})

    def log_message(self, format, *args):
        return
