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

        provider = payload.get("provider")

        if provider != "google":
            self._send_json(
                400,
                {
                    "ok": False,
                    "error": {
                        "code": "unsupported_provider",
                        "message": "OAuth is not configured for this provider.",
                    },
                },
            )
            return

        self._send_json(
            200,
            {
                "ok": True,
                "connectionMethod": "oauth",
                "connectionStatus": "oauth_required",
                "authorizationUrl": None,
                "message": "Gmail OAuth is not configured in this runtime yet.",
            },
        )

    def do_GET(self):
        self._send_json(
            405,
            {
                "ok": False,
                "error": {
                    "code": "method_not_allowed",
                    "message": "Use POST to start inbox authentication",
                },
            },
        )

    def log_message(self, format, *args):
        return
