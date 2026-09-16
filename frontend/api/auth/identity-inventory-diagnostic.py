"""TEMPORARY migration diagnostic. Remove after the passkey cutover."""

from http.server import BaseHTTPRequestHandler

from api.auth import http
from cuevion_auth import identity_inventory_diagnostic as diagnostic


class handler(BaseHTTPRequestHandler):
    def _respond(self):
        try:
            headers = http.snapshot_request_headers(self)
            response = diagnostic.diagnostic_response(self.command, headers, self.path)
        except Exception:
            response = diagnostic._failure(503)
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

    def send_error(self, code, message=None, explain=None):
        # BaseHTTPRequestHandler uses this for unknown methods and parse errors.
        # Never echo its request-derived message or fall back to cacheable HTML.
        http.send_public_response(self, diagnostic._failure(405 if code == 501 else code))

    def log_message(self, _format, *_args):
        return
