"""Vercel adapter for authenticated Priority semantic assessment."""

from __future__ import annotations

import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path


CURRENT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = CURRENT_DIR.parent.parent
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

from api.auth import http as auth_http
from api.priority.http import (
    read_semantic_json_request,
    send_http_boundary_error,
    send_semantic_json,
)
from api.priority.semantic_route import process_semantic_request


class handler(BaseHTTPRequestHandler):
    def send_error(self, code, message=None, explain=None):
        if code == HTTPStatus.NOT_IMPLEMENTED:
            self.close_connection = True
            send_http_boundary_error(
                self,
                auth_http.HttpBoundaryError("method_not_allowed", 405),
            )
            return
        super().send_error(code, message, explain)

    def do_POST(self):
        try:
            headers, payload = read_semantic_json_request(self)
            result = process_semantic_request(headers, payload)
            send_semantic_json(
                self,
                result.status_code,
                result.payload,
                retry_after=result.retry_after,
            )
        except auth_http.HttpBoundaryError as error:
            send_http_boundary_error(self, error)
        except Exception:
            send_semantic_json(
                self,
                500,
                {
                    "ok": False,
                    "error": {
                        "code": "internal_error",
                        "message": "Semantic assessment is temporarily unavailable.",
                    },
                },
            )

    def do_GET(self):
        send_http_boundary_error(
            self,
            auth_http.HttpBoundaryError("method_not_allowed", 405),
        )

    def do_PUT(self):
        self.do_GET()

    def do_PATCH(self):
        self.do_GET()

    def do_DELETE(self):
        self.do_GET()

    def do_HEAD(self):
        self.do_GET()

    def do_OPTIONS(self):
        self.do_GET()

    def log_message(self, format, *args):
        return
