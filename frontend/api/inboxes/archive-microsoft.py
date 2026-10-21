"""Microsoft exact Inbox -> Archive mutation endpoint, source-gated in Production."""
from http.server import BaseHTTPRequestHandler
from api.auth.http import send_public_response
from api.inboxes.microsoft_http import microsoft_inbox_response


class handler(BaseHTTPRequestHandler):
    def _respond(self):
        send_public_response(self, microsoft_inbox_response(self, mode="archive_move"))

    do_POST = _respond
    do_GET = _respond
    do_HEAD = _respond
    do_PUT = _respond
    do_PATCH = _respond
    do_DELETE = _respond
    do_OPTIONS = _respond

    def log_message(self, _format, *_args):
        return
