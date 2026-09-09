"""Read-only authenticated exact mailbox message endpoint."""

from http.server import BaseHTTPRequestHandler
from api.auth.http import send_public_response
from api.inboxes.exact_message_http import exact_message_response


class handler(BaseHTTPRequestHandler):
    def _respond(self):
        send_public_response(self, exact_message_response(self))

    do_POST = _respond
    do_GET = _respond
    do_HEAD = _respond
    do_PUT = _respond
    do_PATCH = _respond
    do_DELETE = _respond
    do_OPTIONS = _respond
    do_TRACE = _respond
    do_CONNECT = _respond

    def log_message(self, _format, *_args):
        return
