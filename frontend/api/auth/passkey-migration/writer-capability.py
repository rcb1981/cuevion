"""Temporary production initial OWNER writer capability diagnostic."""

from http.server import BaseHTTPRequestHandler

from api.auth import http
from cuevion_migration import writer_capability as diagnostic


class handler(BaseHTTPRequestHandler):
    def _respond(self):
        try:
            response = diagnostic.capability_response(
                self.command, http.snapshot_request_headers(self), self.path)
        except Exception:
            response = diagnostic.failure(503)
        http.send_public_response(self, response)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _respond
    do_OPTIONS = do_HEAD = do_TRACE = do_CONNECT = _respond

    def send_error(self, code, message=None, explain=None):
        http.send_public_response(self, diagnostic.failure(405 if code == 501 else code))

    def log_message(self, _format, *_args):
        return
