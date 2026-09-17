from http.server import BaseHTTPRequestHandler

from api.auth import http, registration_authority


class handler(BaseHTTPRequestHandler):
    def _read_body(self) -> bytes:
        raw = self.headers.get("content-length")
        if raw is None or not raw.isascii() or not raw.isdigit():
            return b""
        length = int(raw)
        if length < 0 or length > registration_authority.MAX_REQUEST_BODY_BYTES:
            return b""
        return self.rfile.read(length) if length else b""

    def _respond(self) -> None:
        try:
            raw_headers = http.snapshot_request_headers(self)
        except http.HttpBoundaryError:
            raw_headers = ()
        body = self._read_body() if self.command == "POST" else b""
        response = registration_authority.validation_response(
            self.command,
            raw_headers,
            body,
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
