import sys as _identity_sys

_CANONICAL_MODULE_NAME = "api.inboxes.save-imap-folder-mapping"
_LEGACY_MODULE_NAME = "save-imap-folder-mapping"
_CANONICAL_PACKAGE_NAME = "api.inboxes"

_loaded_module_name = __name__
_current_module = _identity_sys.modules[_loaded_module_name]
if _loaded_module_name not in {_CANONICAL_MODULE_NAME, _LEGACY_MODULE_NAME}:
    raise ImportError(
        "IMAP folder-mapping route helpers must be imported as "
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
        "canonical and legacy IMAP folder-mapping route identities cannot coexist"
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
from .imap_folder_mapping_api import save_imap_folder_mapping


class handler(BaseHTTPRequestHandler):
    def send_error(self, code, message=None, explain=None):
        if code == HTTPStatus.NOT_IMPLEMENTED:
            self.close_connection = True
            send_method_not_allowed(
                self,
                "Use POST to save an IMAP folder mapping.",
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
                    "The IMAP folder-mapping request could not be completed.",
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
            reject_unknown_fields(
                payload,
                {"mailboxId", "role", "selectedFolder"},
            )
            or set(payload) != {"mailboxId", "role", "selectedFolder"}
            or not valid_identifier(payload.get("mailboxId"))
            or payload.get("role") != "trash"
            or type(payload.get("selectedFolder")) is not str
        ):
            send_json(
                self,
                400,
                error_payload(
                    "invalid_request",
                    "Saving a folder mapping requires one managed Trash folder.",
                ),
            )
            return
        status_code, response = save_imap_folder_mapping(
            self.headers,
            payload["mailboxId"],
            payload["selectedFolder"],
        )
        send_json(self, status_code, response)

    def do_GET(self):
        send_method_not_allowed(
            self,
            "Use POST to save an IMAP folder mapping.",
        )

    def do_PUT(self):
        self.do_GET()

    def do_PATCH(self):
        self.do_GET()

    def do_DELETE(self):
        self.do_GET()

    def do_HEAD(self):
        send_method_not_allowed(
            self,
            "Use POST to save an IMAP folder mapping.",
            write_body=False,
        )

    def do_OPTIONS(self):
        send_json(self, 200, {"ok": True})

    def log_message(self, format, *args):
        return
