import importlib
import os
from http.server import BaseHTTPRequestHandler

from api.collaboration import http_adapter


_PRODUCTION_RUNTIME_ENVIRONMENT_NAME = "VERCEL_ENV"
_PRODUCTION_RUNTIME_ENVIRONMENT_VALUE = "production"
_OWNER_HTTP_MODE = "owner_read"
_READINESS_MODE_ENVIRONMENT_NAME = (
    "CUEVION_COLLAB_V2_OWNER_WRITE_READINESS_MODE"
)
_READINESS_MODE = "verify"


class handler(BaseHTTPRequestHandler):
    def _respond(self) -> None:
        enabled = (
            os.environ.get(_PRODUCTION_RUNTIME_ENVIRONMENT_NAME)
            == _PRODUCTION_RUNTIME_ENVIRONMENT_VALUE
            and http_adapter.parse_http_mode_mapping(os.environ)
            == _OWNER_HTTP_MODE
            and os.environ.get(_READINESS_MODE_ENVIRONMENT_NAME)
            == _READINESS_MODE
        )

        def activated_response() -> http_adapter.PublicResponse:
            readiness_http = importlib.import_module(
                "api.collaboration.owner_write_readiness_http"
            )
            return readiness_http.owner_write_readiness_response(self)

        response = (
            http_adapter.invoke_safely(
                activated_response,
                allow_method="POST",
            )
            if enabled
            else http_adapter.json_failure("not_found", status=404)
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
