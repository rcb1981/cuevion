import importlib
import os
from http.server import BaseHTTPRequestHandler

from api.collaboration import http_adapter


_ALLOWED_OWNER_MODES = frozenset({"owner_read", "owner_write"})


class handler(BaseHTTPRequestHandler):
    def _respond(self) -> None:
        mode = http_adapter.parse_http_mode_mapping(os.environ)

        def activated_response() -> http_adapter.PublicResponse:
            owner_http = importlib.import_module("api.collaboration.owner_http")
            return owner_http.owner_response(self, http_mode=mode)

        response = http_adapter.invoke_if_http_mode(
            mode,
            allowed_modes=_ALLOWED_OWNER_MODES,
            callback=activated_response,
            allow_method="POST",
        )
        http_adapter.write_public_response(self, response)

    do_GET = _respond
    do_POST = _respond
    do_PUT = _respond
    do_PATCH = _respond
    do_DELETE = _respond
    do_OPTIONS = _respond
    do_HEAD = _respond
    do_TRACE = _respond
    do_CONNECT = _respond

    def log_message(self, _format, *_args):
        return
