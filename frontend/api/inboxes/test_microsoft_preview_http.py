"""Request-level Microsoft Preview send and Sent-status origin security tests."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from api.inboxes import microsoft_outbound_http as send_http
from api.inboxes import microsoft_outbound_service as send_service
from api.inboxes import microsoft_sent_status_http as status_http
from api.inboxes import microsoft_sent_status_service as status_service
from api.inboxes.test_microsoft_outbound_http import request as send_request, PROOF
from api.inboxes.test_microsoft_sent_status_http import request as status_request, PROOF as SENT_PROOF
from api.inboxes.test_microsoft_preview_origin import HOST, ORIGIN, PREVIEW, REDIRECT


def _on_preview(request):
    request.headers.replace_header("Host", HOST)
    request.headers.replace_header("Origin", ORIGIN)
    request.headers["X-Forwarded-Host"] = HOST
    request.headers["X-Forwarded-Proto"] = "https"
    return request


class PreviewSendStatusRequestTests(unittest.TestCase):
    def setUp(self):
        scoped = patch.dict(os.environ, PREVIEW, clear=True)
        scoped.start()
        self.addCleanup(scoped.stop)

    def test_exact_preview_send_accepted_after_full_origin_checks(self):
        req = _on_preview(send_request())
        with patch.object(
            send_service, "send_owned_microsoft_message", return_value=PROOF,
        ) as send:
            answer = send_http.microsoft_send_response(req)
        self.assertEqual(answer.status, 202)
        send.assert_called_once()

    def test_exact_preview_sent_check_uses_read_only_service(self):
        req = _on_preview(status_request())
        with patch.object(
            status_service, "check_owned_microsoft_sent_status",
            return_value=SENT_PROOF,
        ) as check:
            answer = status_http.microsoft_sent_status_response(req)
        self.assertEqual(answer.status, 200)
        check.assert_called_once()

    def test_missing_pin_fails_closed_without_send_or_status_lookup(self):
        for key in ("CUEVION_MICROSOFT_PREVIEW_ORIGIN", "CUEVION_APP_URL", "MICROSOFT_OAUTH_REDIRECT_URI"):
            config = dict(PREVIEW)
            config.pop(key)
            with self.subTest(key=key), patch.dict(os.environ, config, clear=True), patch.object(
                send_service, "send_owned_microsoft_message",
            ) as send, patch.object(
                status_service, "check_owned_microsoft_sent_status",
            ) as status:
                self.assertEqual(send_http.microsoft_send_response(
                    _on_preview(send_request()),
                ).status, 503)
                self.assertEqual(status_http.microsoft_sent_status_response(
                    _on_preview(status_request()),
                ).status, 503)
                send.assert_not_called()
                status.assert_not_called()

    def test_cross_host_or_cross_origin_preview_is_never_allowed(self):
        for header, value in (
            ("Host", "other.vercel.app"),
            ("Origin", "https://other.vercel.app"),
            ("X-Forwarded-Host", "other.vercel.app"),
            ("X-Forwarded-Proto", "http"),
        ):
            send_req = _on_preview(send_request())
            status_req = _on_preview(status_request())
            send_req.headers.replace_header(header, value)
            status_req.headers.replace_header(header, value)
            with self.subTest(header=header), patch.object(
                send_service, "send_owned_microsoft_message",
            ) as send, patch.object(
                status_service, "check_owned_microsoft_sent_status",
            ) as status:
                self.assertIn(send_http.microsoft_send_response(send_req).status, (400, 403))
                self.assertIn(status_http.microsoft_sent_status_response(status_req).status, (400, 403))
                send.assert_not_called()
                status.assert_not_called()

    def test_preview_cannot_target_production_oauth_redirect(self):
        settings = {**PREVIEW, "MICROSOFT_OAUTH_REDIRECT_URI":
                    "https://app.cuevion.com/api/inboxes/oauth-callback"}
        with patch.dict(os.environ, settings, clear=True), patch.object(
            send_service, "send_owned_microsoft_message",
        ) as send:
            result = send_http.microsoft_send_response(_on_preview(send_request()))
        self.assertEqual(result.status, 503)
        send.assert_not_called()

    def test_preview_does_not_inherit_production_origin_authority(self):
        send_req = send_request()
        status_req = status_request()
        with patch.object(send_service, "send_owned_microsoft_message") as send, patch.object(
            status_service, "check_owned_microsoft_sent_status",
        ) as check:
            self.assertIn(send_http.microsoft_send_response(send_req).status, (403, 503))
            self.assertIn(status_http.microsoft_sent_status_response(status_req).status, (403, 503))
            send.assert_not_called()
            check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
