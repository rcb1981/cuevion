"""Existing Microsoft Drafts only; independent source gate remains OFF."""
from http.server import BaseHTTPRequestHandler
from api.auth.http import send_public_response
from api.inboxes.microsoft_draft_send_http import microsoft_draft_send_response

class handler(BaseHTTPRequestHandler):
    def _respond(self):
        send_public_response(self, microsoft_draft_send_response(self))
    do_POST = _respond
    do_GET = _respond
    do_HEAD = _respond
    do_PUT = _respond
    do_PATCH = _respond
    do_DELETE = _respond
    do_OPTIONS = _respond
    def log_message(self,_format,*_args):
        return
