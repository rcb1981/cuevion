import json
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    def _send_json(self, status_code: int, payload: dict):
        response_body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def do_POST(self):
        content_length = int(self.headers.get("content-length", "0"))
        raw_body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""

        try:
            payload = json.loads(raw_body or "{}")
        except json.JSONDecodeError:
            self._send_json(
                400,
                {
                    "ok": False,
                    "error": {
                        "code": "invalid_request",
                        "message": "Request body must be valid JSON",
                    },
                },
            )
            return

        internal_role = payload.get("internalRole", None)
        payload["internalRole"] = internal_role

        try:
            from imap_connect_preview import build_connect_preview_response

            status_code, response_payload = build_connect_preview_response(payload)
            self._send_json(status_code, response_payload)
        except Exception:
            self._send_json(
                500,
                {
                    "ok": False,
                    "error": {
                        "code": "server_error",
                        "message": "Could not connect to inbox.",
                    },
                },
            )

    def do_GET(self):
        self._send_json(
            405,
            {
                "ok": False,
                "error": {
                    "code": "method_not_allowed",
                    "message": "Use POST for inbox connection tests",
                },
            },
        )

    def log_message(self, format, *args):
        return
