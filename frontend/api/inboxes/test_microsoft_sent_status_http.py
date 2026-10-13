"""No-network contracts for the owner-scoped Sent-copy status HTTP route."""
from __future__ import annotations

import importlib.util
import io
import json
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
import unittest
import os
from unittest.mock import patch

from api.inboxes import microsoft_sent_status_http as http
from api.inboxes import microsoft_sent_status_service as service
from api.inboxes.microsoft_mailbox_access import MicrosoftMailboxAccessError
from api.inboxes.microsoft_graph import MicrosoftGraphError

MAILBOX = "microsoft-0123456789abcdefabcd12345678"
ATTEMPT = "a"*43
PROOF = {
    "attemptId":ATTEMPT, "status":"sent_copy_confirmed",
    "sentCopyConfirmed":True, "deliveryConfirmed":False,
}


def request(payload=None, *, method="POST", missing_origin=False, raw=None):
    data=raw if raw is not None else json.dumps(
        payload if payload is not None else {"mailboxId":MAILBOX,"attemptId":ATTEMPT}
    ).encode()
    headers=Message()
    for name,value in (
        ("Host","app.cuevion.com"),
        ("Content-Type","application/json"),
        ("Content-Length",str(len(data))),
        ("Cookie","testing"),
    ):
        headers[name]=value
    if not missing_origin:
        headers["Origin"]="https://app.cuevion.com"
    return SimpleNamespace(command=method,headers=headers,rfile=io.BytesIO(data))


def body(res): return json.loads(res.body.decode())


class SentStatusHTTPTests(unittest.TestCase):
    def setUp(self):
        # These pre-existing tests prove strict canonical Production behavior.
        # Preview has its own exact-host/Origin tests and no wildcard fallback.
        scope = patch.dict(os.environ, {
            "VERCEL_ENV": "production",
            "MICROSOFT_OAUTH_REDIRECT_URI": "https://app.cuevion.com/api/inboxes/oauth-callback",
        })
        scope.start()
        self.addCleanup(scope.stop)

    def test_confirmed_sent_copy_is_distinct_from_delivery(self):
        with patch.object(service,"check_owned_microsoft_sent_status",return_value=PROOF) as check:
            result=http.microsoft_sent_status_response(request())
        self.assertEqual(result.status,200)
        self.assertEqual(body(result),{"ok":True,**PROOF})
        self.assertIn(("Cache-Control","no-store"),result.headers)
        self.assertEqual(check.call_args.args[1],MAILBOX)
        self.assertEqual(check.call_args.kwargs,{"attempt_id":ATTEMPT})
        self.assertNotIn("draftId",body(result))

    def test_no_sent_copy_yet_is_pending_not_failed(self):
        with patch.object(service,"check_owned_microsoft_sent_status",return_value={
            **PROOF,"status":"sent_copy_pending","sentCopyConfirmed":False,
        }):
            result=http.microsoft_sent_status_response(request())
        self.assertEqual(result.status,200)
        self.assertEqual(body(result)["status"],"sent_copy_pending")

    def test_prepared_draft_has_not_started_sending(self):
        with patch.object(service,"check_owned_microsoft_sent_status",return_value={
            **PROOF,"status":"send_not_started","sentCopyConfirmed":False,
        }):
            result=http.microsoft_sent_status_response(request())
        self.assertEqual(result.status,200)
        self.assertFalse(body(result)["sentCopyConfirmed"])

    def test_invalid_success_proofs_do_not_escape(self):
        for patch_result in (
            {**PROOF,"status":"sent_copy_pending","sentCopyConfirmed":True},
            {**PROOF,"deliveryConfirmed":True},
            {**PROOF,"attemptId":"another-id"},
            {**PROOF,"status":"delivered"},
            {**PROOF,"sentCopyConfirmed":"true"},
            None,
        ):
            with self.subTest(proof=patch_result), patch.object(
                service,"check_owned_microsoft_sent_status",return_value=patch_result,
            ):
                res=http.microsoft_sent_status_response(request())
                self.assertEqual(res.status,503)
                self.assertEqual(body(res)["ok"],False)

    def test_invalid_input_never_calls_authority_or_graph(self):
        for payload in (
            {"mailboxId":MAILBOX,"attemptId":"bad"},
            {"mailboxId":"../mailbox","attemptId":ATTEMPT},
            {"mailboxId":MAILBOX,"attemptId":ATTEMPT,"draftId":"user-controlled"},
            {"mailboxId":MAILBOX,"attemptId":ATTEMPT,"token":"private"},
            {"mailboxId":MAILBOX},
            [],
        ):
            with self.subTest(payload=payload), patch.object(
                service,"check_owned_microsoft_sent_status",
            ) as check:
                res=http.microsoft_sent_status_response(request(payload))
                self.assertEqual(res.status,400)
                check.assert_not_called()

    def test_wrong_origin_or_host_denied_before_status_check(self):
        for name,value in (("Host","evil.example"),("Origin","https://evil.example")):
            req=request()
            req.headers.replace_header(name,value)
            with self.subTest(name=name),patch.object(
                service,"check_owned_microsoft_sent_status",
            ) as check:
                response=http.microsoft_sent_status_response(req)
                self.assertEqual(response.status,403)
                check.assert_not_called()

    def test_missing_origin_denied(self):
        with patch.object(service,"check_owned_microsoft_sent_status") as check:
            response=http.microsoft_sent_status_response(request(missing_origin=True))
        self.assertEqual(response.status,400)
        check.assert_not_called()

    def test_non_post_methods_never_access_storage(self):
        for method in ("GET","HEAD","PUT","PATCH","DELETE","OPTIONS"):
            with self.subTest(method=method),patch.object(
                service,"check_owned_microsoft_sent_status",
            ) as check:
                res=http.microsoft_sent_status_response(request(method=method))
                self.assertEqual(res.status,405)
                self.assertIn(("Allow","POST"),res.headers)
                check.assert_not_called()

    def test_safe_errors_for_missing_attempt_and_graph_outage(self):
        for exception,code,status in (
            (service.MicrosoftSentStatusError("microsoft_send_attempt_not_found"),"microsoft_send_attempt_not_found",404),
            (service.MicrosoftSentStatusError("microsoft_send_attempt_conflict"),"microsoft_send_attempt_conflict",409),
            (MicrosoftMailboxAccessError("microsoft_reconnect_required"),"microsoft_reconnect_required",409),
            (MicrosoftMailboxAccessError("microsoft_unauthorized"),"microsoft_unauthorized",401),
            (MicrosoftGraphError("microsoft_graph_rate_limited"),"microsoft_graph_rate_limited",429),
            (MicrosoftGraphError("microsoft_sent_copy_unavailable"),"microsoft_sent_copy_unavailable",503),
        ):
            with self.subTest(code=code), patch.object(
                service,"check_owned_microsoft_sent_status",side_effect=exception,
            ):
                res=http.microsoft_sent_status_response(request())
                self.assertEqual(res.status,status)
                self.assertEqual(body(res)["error"]["code"],code)

    def test_oversized_body_and_private_exceptions_are_sanitized(self):
        req=request(raw=b" "* (http.MAX_REQUEST_BYTES+1))
        with patch.object(service,"check_owned_microsoft_sent_status") as check:
            res=http.microsoft_sent_status_response(req)
        self.assertEqual(res.status,413)
        check.assert_not_called()
        with patch.object(service,"check_owned_microsoft_sent_status",side_effect=RuntimeError("secret token")):
            res=http.microsoft_sent_status_response(request())
        self.assertEqual(res.status,503)
        self.assertNotIn("secret token",res.body.decode())

    def test_route_adapter_is_one_mode_and_rejects_other_methods(self):
        path=Path(__file__).with_name("check-microsoft-sent-status.py")
        spec=importlib.util.spec_from_file_location("cuevion_test_sent_status",path)
        assert spec and spec.loader
        route=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(route)
        self.assertIs(route.handler.do_GET,route.handler.do_POST)
        with patch.object(route,"microsoft_sent_status_response",return_value=object()) as run, patch.object(
            route,"send_public_response",
        ):
            instance=route.handler.__new__(route.handler)
            instance._respond()
        run.assert_called_once()


if __name__=="__main__":
    unittest.main()
