"""TEMPORARY Preview-only runtime proof for Gmail History delta reader."""

import json
import os
from http.server import BaseHTTPRequestHandler

from cuevion_mailbox.gmail_history_delta import read_gmail_history_delta


def _proof():
    if os.environ.get("VERCEL_ENV") != "preview":
        return 503, {"ok": False, "stage": "boundary"}

    context = {
        "mailbox_email": "preview-history-proof@example.invalid",
        "mailbox_id": "preview-history-proof",
        "refresh_attempted": False,
    }
    calls = []

    def request(request_context, path):
        calls.append(path)
        if len(calls) == 1:
            return (
                {
                    "historyId": "2005",
                    "history": [
                        {
                            "id": "2002",
                            "messages": [{"id": "message-1"}],
                            "labelsAdded": [
                                {"message": {"id": "message-1"}},
                                {"message": {"id": "message-2"}},
                            ],
                        }
                    ],
                    "nextPageToken": "page+2",
                },
                None,
                {**request_context, "proof_generation": 2},
                None,
            )
        return (
            {
                "historyId": "2010",
                "history": [
                    {
                        "id": "2008",
                        "labelsRemoved": [
                            {"message": {"id": "message-3"}}
                        ],
                        "messagesDeleted": [
                            {"message": {"id": "message-2"}}
                        ],
                    }
                ],
            },
            None,
            {**request_context, "proof_generation": 3},
            None,
        )

    delta = read_gmail_history_delta(
        context,
        start_history_id="2000",
        request_with_one_refresh=request,
    )
    stale = read_gmail_history_delta(
        context,
        start_history_id="2000",
        request_with_one_refresh=lambda request_context, _path: (
            None,
            {"code": "gmail_message_not_found"},
            request_context,
            None,
        ),
    )

    expected = (
        delta.status == "ok"
        and delta.next_history_id == "2010"
        and delta.affected_message_ids
        == ("message-1", "message-2", "message-3")
        and delta.page_count == 2
        and delta.history_record_count == 2
        and delta.context.get("proof_generation") == 3
        and calls
        == [
            "/history?startHistoryId=2000&maxResults=100",
            "/history?startHistoryId=2000&maxResults=100&pageToken=page%2B2",
        ]
        and stale.status == "full_sync_required"
        and stale.next_history_id is None
    )
    if not expected:
        return 503, {"ok": False, "stage": "assertion"}

    return 200, {
        "ok": True,
        "status": delta.status,
        "next_history_id": delta.next_history_id,
        "affected_count": len(delta.affected_message_ids),
        "affected_message_ids": list(delta.affected_message_ids),
        "page_count": delta.page_count,
        "history_record_count": delta.history_record_count,
        "stale_status": stale.status,
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            status, payload = _proof()
        except Exception:
            status, payload = 503, {"ok": False, "stage": "exception"}
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = b'{"ok":false,"stage":"method"}'
        self.send_response(405)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return
