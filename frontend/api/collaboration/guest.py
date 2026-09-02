import importlib
import os
from http.server import BaseHTTPRequestHandler

from api.collaboration import http_adapter


_GUEST_HTTP_MODE_ENVIRONMENT_NAME = "CUEVION_COLLAB_V2_GUEST_HTTP_MODE"
_GUEST_HTTP_MODE_ACTIVE = "guest_on"


class handler(BaseHTTPRequestHandler):
    def _respond(self) -> None:
        mode = os.environ.get(_GUEST_HTTP_MODE_ENVIRONMENT_NAME)
        if type(mode) is not str or mode != _GUEST_HTTP_MODE_ACTIVE:
            response = http_adapter.json_failure("not_found", status=404)
        else:
            def activated_response() -> http_adapter.PublicResponse:
                guest_http = importlib.import_module(
                    "api.collaboration.guest_http"
                )
                return guest_http.guest_response(
                    self,
                    http_mode=_GUEST_HTTP_MODE_ACTIVE,
                )

            response = http_adapter.invoke_safely(
                activated_response,
                allow_method="GET, POST",
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
