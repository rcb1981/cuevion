"""Neutral authenticated notification endpoint; browser integration is C3D."""

from http.server import BaseHTTPRequestHandler

from api.auth.http import send_public_response
from api.notifications.http import notifications_response


class handler(BaseHTTPRequestHandler):
    def _respond(self) -> None:
        send_public_response(self, notifications_response(self))

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
