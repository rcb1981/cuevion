from http.server import BaseHTTPRequestHandler

from api.auth import http, runtime


class handler(BaseHTTPRequestHandler):
    def _respond(self) -> None:
        try:
            raw_headers = http.snapshot_request_headers(self)
        except http.HttpBoundaryError:
            raw_headers = ()
        response = runtime.logout_response(
            self.command,
            raw_headers,
        )
        http.send_public_response(self, response)

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
