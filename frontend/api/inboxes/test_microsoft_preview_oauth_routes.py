"""Microsoft OAuth start/callback Preview gate wiring regression tests."""
from __future__ import annotations

import importlib.util
import inspect
import io
import json
import os
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from api.inboxes.test_microsoft_preview_origin import PREVIEW, HOST, ORIGIN

FOLDER = Path(__file__).resolve().parent


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, FOLDER / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MicrosoftOauthGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.start_module = _load("cuevion_preview_start_oauth", "connect-oauth.py")
        cls.callback_module = _load("cuevion_preview_oauth_callback", "oauth-callback.py")

    def _start(self, *, origin=ORIGIN, host=HOST, env=None):
        payload = {
            "provider": "microsoft",
            "email": "artist@example.com",
            "mode": "initial",
        }
        raw = json.dumps(payload).encode()
        headers = Message()
        headers["Content-Length"] = str(len(raw))
        headers["Content-Type"] = "application/json"
        headers["Host"] = host
        if origin is not None:
            headers["Origin"] = origin
        instance = self.start_module.handler.__new__(self.start_module.handler)
        instance.headers = headers
        instance.rfile = io.BytesIO(raw)
        reports = []
        instance._send_json = lambda status, content, **_kw: reports.append((status, content))
        member = SimpleNamespace(
            membership_role="owner", email="owner@example.com",
            user_id="user-1", workspace_id="workspace-1",
            name="Owner",
        )
        with patch.dict(os.environ, env or PREVIEW, clear=True), patch.object(
            self.start_module, "resolve_authenticated_member_authority",
            return_value=(member, None),
        ), patch.object(
            self.start_module, "reserve_authoritative_microsoft_reconnect",
        ) as reserve, patch.object(
            self.start_module, "build_signed_state",
        ) as state:
            instance.do_POST()
        return reports, reserve, state

    def test_forbidden_preview_host_rejected_before_signed_state_or_reservation(self):
        reports, reserve, state = self._start(host="other.vercel.app")
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0][0], 403)
        self.assertEqual(reports[0][1]["error"]["code"], "microsoft_preview_origin_invalid")
        reserve.assert_not_called()
        state.assert_not_called()

    def test_missing_preview_origin_rejected_before_oauth_state(self):
        reports, reserve, state = self._start(env={
            **PREVIEW, "CUEVION_MICROSOFT_PREVIEW_ORIGIN": "",
        })
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0][0], 503)
        reserve.assert_not_called()
        state.assert_not_called()

    def test_wrong_post_origin_rejected_before_oauth_state(self):
        reports, reserve, state = self._start(origin="https://other.vercel.app")
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0][0], 403)
        reserve.assert_not_called()
        state.assert_not_called()

    def test_valid_preview_origin_does_not_bypass_missing_app_credentials(self):
        reports, reserve, state = self._start()
        self.assertTrue(reports)
        self.assertEqual(reports[-1][0], 503)
        self.assertEqual(reports[-1][1]["error"]["code"], "oauth_not_configured")
        reserve.assert_not_called()
        state.assert_not_called()

    def test_callback_uses_exact_redirect_before_exchange(self):
        source = inspect.getsource(self.callback_module.handler._handle_get)
        self.assertIn("verified_microsoft_redirect_uri = require_microsoft_oauth_redirect(", source)
        self.assertIn('snapshot_request_headers(self), callback=True,', source)
        self.assertIn('failure_code="microsoft_preview_origin_invalid"', source)
        self.assertIn("microsoft_redirect_uri = verified_microsoft_redirect_uri", source)
        self.assertLess(
            source.index("verified_microsoft_redirect_uri = require_microsoft_oauth_redirect("),
            source.index("token_payload, token_error = _exchange_microsoft_code("),
        )

    def test_google_start_and_callback_remain_separate_from_microsoft_gate(self):
        start = inspect.getsource(self.start_module.handler.do_POST)
        callback = inspect.getsource(self.callback_module.handler._handle_get)
        self.assertIn('if provider == "microsoft":', start)
        self.assertIn('if provider == "microsoft":', callback)
        self.assertIn("resolve_google_redirect_uri(self.headers)", start)
        self.assertIn('google_redirect_uri = f"{public_app_origin}{OAUTH_CALLBACK_PATH}"', callback)


if __name__ == "__main__":
    unittest.main()
