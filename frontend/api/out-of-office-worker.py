from __future__ import annotations

import hmac
import json
import os
import sys
from collections import Counter
from http.server import BaseHTTPRequestHandler
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = CURRENT_DIR.parent
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from api.out_of_office_providers import (  # noqa: E402
    load_out_of_office_mailbox,
    resolve_out_of_office_adapter,
)
from api.out_of_office_store import (  # noqa: E402
    OutOfOfficeStoreUnavailable,
    build_out_of_office_store,
)
from api.out_of_office_worker import (  # noqa: E402
    OutOfOfficeProviderError,
    run_out_of_office_worker,
)


def _send_json(handler: BaseHTTPRequestHandler, status_code: int, payload: dict) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _authorized(headers) -> bool:
    if os.environ.get("VERCEL_ENV") != "production":
        return False
    secret = os.environ.get("CRON_SECRET", "")
    if len(secret) < 32:
        return False
    authorization = headers.get("authorization", "") if headers else ""
    expected = "Bearer " + secret
    return isinstance(authorization, str) and hmac.compare_digest(
        authorization,
        expected,
    )


def _summary(results: list[dict]) -> dict:
    statuses = Counter(
        result.get("status")
        for result in results
        if isinstance(result, dict) and isinstance(result.get("status"), str)
    )
    errors = Counter(
        result.get("error")
        for result in results
        if isinstance(result, dict) and isinstance(result.get("error"), str)
    )
    return {
        "processedTargets": len(results),
        "sent": sum(
            result.get("sent", 0)
            for result in results
            if isinstance(result, dict) and isinstance(result.get("sent"), int)
        ),
        "sentCopyFailures": sum(
            result.get("sentCopyFailures", 0)
            for result in results
            if isinstance(result, dict)
            and isinstance(result.get("sentCopyFailures"), int)
        ),
        "suppressed": sum(
            result.get("suppressed", 0)
            for result in results
            if isinstance(result, dict) and isinstance(result.get("suppressed"), int)
        ),
        "skipped": sum(
            result.get("skipped", 0)
            for result in results
            if isinstance(result, dict) and isinstance(result.get("skipped"), int)
        ),
        "statuses": dict(sorted(statuses.items())),
        "errors": dict(sorted(errors.items())),
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not _authorized(self.headers):
            _send_json(self, 404, {"ok": False, "error": {"code": "not_found"}})
            return

        try:
            store = build_out_of_office_store()
            results = run_out_of_office_worker(
                store=store,
                load_mailbox=load_out_of_office_mailbox,
                resolve_adapter=resolve_out_of_office_adapter,
            )
        except (OutOfOfficeStoreUnavailable, OutOfOfficeProviderError):
            _send_json(
                self,
                503,
                {
                    "ok": False,
                    "error": {"code": "out_of_office_worker_unavailable"},
                },
            )
            return
        except Exception:
            _send_json(
                self,
                500,
                {
                    "ok": False,
                    "error": {"code": "out_of_office_worker_failed"},
                },
            )
            return

        _send_json(self, 200, {"ok": True, **_summary(results)})

    def do_POST(self):
        _send_json(self, 405, {"ok": False, "error": {"code": "method_not_allowed"}})

    def log_message(self, format, *args):
        return
