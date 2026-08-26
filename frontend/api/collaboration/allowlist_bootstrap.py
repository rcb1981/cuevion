import importlib
import os
from http.server import BaseHTTPRequestHandler

from api.collaboration import http_adapter


_ALLOWED_BOOTSTRAP_MODES = frozenset({"allowlist_bootstrap"})
_PRODUCTION_RUNTIME_ENVIRONMENT_NAME = "VERCEL_ENV"
_PRODUCTION_RUNTIME_ENVIRONMENT_VALUE = "production"


class handler(BaseHTTPRequestHandler):
    def _respond(self) -> None:
        mode = http_adapter.parse_http_mode_mapping(os.environ)
        runtime_mode = (
            mode
            if os.environ.get(_PRODUCTION_RUNTIME_ENVIRONMENT_NAME)
            == _PRODUCTION_RUNTIME_ENVIRONMENT_VALUE
            else http_adapter.HTTP_MODE_OFF
        )

        def activated_response() -> http_adapter.PublicResponse:
            bootstrap_http = importlib.import_module(
                "api.collaboration.allowlist_bootstrap_http"
            )
            return bootstrap_http.allowlist_bootstrap_response(
                self,
                http_mode=mode,
            )

        response = http_adapter.invoke_if_http_mode(
            runtime_mode,
            allowed_modes=_ALLOWED_BOOTSTRAP_MODES,
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
