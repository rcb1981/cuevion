"""Run the existing HTTP security/contract suite through the actual route."""

import importlib.util
import io
import json
import os
from pathlib import Path
import sys
from unittest.mock import Mock, patch

from api.auth.http import PublicResponse
from . import test_http as contract


class NotificationRouteHttpTests(contract.NotificationHttpTests):
    def setUp(self):
        super().setUp()
        path = Path(__file__).resolve().parents[1] / "notifications.py"
        spec = importlib.util.spec_from_file_location("api.notifications", path)
        self.route = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {spec.name: self.route}):
            spec.loader.exec_module(self.route)
        self.assertIs(self.route.notifications_response, contract.http.notifications_response)

    def invoke(self, req=None):
        req = req or contract.request()
        req.send_response_only = Mock()
        req.send_header = Mock()
        req.end_headers = Mock()
        req.wfile = io.BytesIO()
        # Only time/environment and the existing backend fixture are injected;
        # the route's response function and serializer run unmocked.
        with patch.object(os, "environ", dict(contract.ENV)), patch.object(
            contract.http.time, "time", return_value=contract.NOW,
        ):
            getattr(self.route.handler, "do_" + req.command.upper())(req)
        req.send_response_only.assert_called_once()
        req.end_headers.assert_called_once()
        return PublicResponse(
            status=req.send_response_only.call_args.args[0],
            headers=tuple(call.args for call in req.send_header.call_args_list),
            body=req.wfile.getvalue(),
        )

    def test_unsupported_operation_stays_invalid(self):
        response = self.invoke(contract.request({"operation": "unsupported"}))
        self.assertEqual(response.status, 400)
        self.assertEqual(json.loads(response.body)["error"]["code"], "invalid_request")
        self.rate.assert_not_called()
        self.summary.assert_not_called()

    def test_public_list_with_explicit_default_limit_and_null_cursor(self):
        response = self.invoke(contract.request({"operation": "list", "limit": 50, "cursor": None}))
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.body), {
            "v": 1, "unreadCount": 1, "notifications": [contract.dto()], "nextCursor": None,
        })
        self.list.assert_called_once_with(contract.WORKSPACE, contract.USER, limit=50, cursor=None)

    def test_remaining_methods_and_query_strings_keep_security_headers(self):
        for method in ("TRACE", "CONNECT", "GET", "OPTIONS", "HEAD"):
            response = self.invoke(contract.request(method=method))
            self.assertEqual(response.status, 405)
            self.assertIn(("Allow", "POST"), response.headers)
            self.assertIn(("Cache-Control", "no-store"), response.headers)
            self.assertIn(("X-Content-Type-Options", "nosniff"), response.headers)
        for query in ("?operation=summary", "?", "?cursor=opaque"):
            self.assertEqual(self.invoke(contract.request(path="/api/notifications" + query)).status, 400)
        self.auth.assert_not_called()
