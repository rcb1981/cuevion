from __future__ import annotations

import io
import json
import unittest
from email.message import Message
from types import SimpleNamespace
from unittest.mock import patch

from api.inboxes import microsoft_outbound_http as http
from api.inboxes import microsoft_outbound_service as service
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes.microsoft_mailbox_access import MicrosoftMailboxAccessError

BOX = "microsoft-0123456789abcdefabcd12345678"
ID = "i"*43
COMPOSE = {"mode":"new","to":["artist@example.com"],"subject":"A&R Update","body":"A short message"}


def request(payload=None, *, method="POST", headers=(), raw=None):
    data = raw if raw is not None else json.dumps(
        payload if payload is not None else {"mailboxId":BOX,"attemptId":ID,"compose":COMPOSE}
    ).encode()
    h = Message()
    for name, value in (
        ("Host","app.cuevion.com"),
        ("Origin","https://app.cuevion.com"),
        ("Content-Type","application/json"),
        ("Content-Length",str(len(data))),
        ("Cookie","test-session"),
        *headers,
    ):
        h[name] = value
    return SimpleNamespace(command=method,headers=h,rfile=io.BytesIO(data))


def body(reply):return json.loads(reply.body.decode())

PROOF={"attemptId":ID,"status":"accepted_by_microsoft",
       "deliveryConfirmed":False,"sentCopyConfirmed":False,"replayed":False}

class HTTPOutboundTests(unittest.TestCase):
    def test_new_message_returns_202_but_not_delivery_claim(self):
        with patch.object(service,"send_owned_microsoft_message",return_value=PROOF) as send:
            reply=http.microsoft_send_response(request())
        self.assertEqual(reply.status,202)
        self.assertEqual(body(reply),{"ok":True,**PROOF})
        self.assertEqual(send.call_args.args[1],BOX)
        self.assertEqual(send.call_args.kwargs,{"attempt_id":ID,"compose":{**COMPOSE,"cc":[]}})
        self.assertIn(("Cache-Control","no-store"),reply.headers)
        self.assertNotIn("artist@example.com",reply.body.decode())
        self.assertNotIn("A&R Update",reply.body.decode())

    def test_reply_has_no_caller_controlled_recipients_or_thread(self):
        for mode in ("reply","reply_all"):
            incoming={
                "mailboxId":BOX,"attemptId":ID,
                "compose":{"mode":mode,"sourceProviderMessageId":"source-msg","body":"Reply content"},
            }
            with patch.object(service,"send_owned_microsoft_message",return_value=PROOF) as send:
                response=http.microsoft_send_response(request(incoming))
            self.assertEqual(response.status,202)
            self.assertEqual(send.call_args.kwargs["compose"],incoming["compose"])

    def test_wrong_origin_and_host_rejected_before_auth_or_graph(self):
        for pair in (("Origin","https://other.example"),("Host","evil.example")):
            with self.subTest(pair=pair),patch.object(service,"send_owned_microsoft_message") as send:
                reply=http.microsoft_send_response(request(headers=(pair,)))
                self.assertEqual(reply.status,400)  # duplicate sensitive header rejected first
                send.assert_not_called()

    def test_wrong_unique_host_is_denied_with_403(self):
        req = request()
        req.headers.replace_header("Host", "evil.example")
        with patch.object(service,"send_owned_microsoft_message") as send:
            response=http.microsoft_send_response(req)
        self.assertEqual(response.status,403)
        send.assert_not_called()

    def test_missing_origin_denied(self):
        req=request()
        del req.headers["Origin"]
        with patch.object(service,"send_owned_microsoft_message") as send:
            response=http.microsoft_send_response(req)
        self.assertEqual(response.status,400)
        send.assert_not_called()

    def test_unexpected_payload_and_sender_spoof_denied(self):
        bad=[
            {"mailboxId":BOX,"attemptId":ID,"compose":{**COMPOSE,"from":"spoof@example.com"}},
            {"mailboxId":BOX,"attemptId":ID,"compose":COMPOSE,"accessToken":"secret"},
            {"mailboxId":BOX,"attemptId":ID,"compose":{**COMPOSE,"body":"  "}},
            {"mailboxId":BOX,"attemptId":ID,"compose":{"mode":"reply","sourceProviderMessageId":"source-msg","body":"reply","to":["spoof@example.com"]}},
            {"mailboxId":BOX,"attemptId":"invalid","compose":COMPOSE},
            {"mailboxId":"../other","attemptId":ID,"compose":COMPOSE},
        ]
        for item in bad:
            with self.subTest(item=item),patch.object(service,"send_owned_microsoft_message") as send:
                response=http.microsoft_send_response(request(item))
                self.assertEqual(response.status,400)
                send.assert_not_called()

    def test_send_body_limit_prevents_auth_or_graph(self):
        with patch.object(service,"send_owned_microsoft_message") as send:
            reply=http.microsoft_send_response(request(raw=b" "* (http.MAX_REQUEST_BYTES+1)))
        self.assertEqual(reply.status,413)
        send.assert_not_called()

    def test_uncertain_result_not_success(self):
        with patch.object(service,"send_owned_microsoft_message",return_value={
            **PROOF,"deliveryConfirmed":True
        }):
            reply=http.microsoft_send_response(request())
        self.assertEqual(reply.status,503)
        self.assertFalse(body(reply)["ok"])

    def test_known_safe_permission_and_ledger_errors(self):
        for exception, status, code in (
            (MicrosoftMailboxAccessError("microsoft_send_permission_required"),403,"microsoft_send_permission_required"),
            (MicrosoftMailboxAccessError("microsoft_reconnect_required"),409,"microsoft_reconnect_required"),
            (service.MicrosoftOutboundError("microsoft_send_attempt_already_started"),409,"microsoft_send_attempt_already_started"),
            (service.MicrosoftOutboundError("microsoft_send_store_unavailable"),503,"microsoft_send_store_unavailable"),
            (MicrosoftGraphError("microsoft_send_unconfirmed"),503,"microsoft_send_unconfirmed"),
            (MicrosoftGraphError("microsoft_graph_rate_limited"),429,"microsoft_graph_rate_limited"),
        ):
            with self.subTest(code=code),patch.object(service,"send_owned_microsoft_message",side_effect=exception):
                response=http.microsoft_send_response(request())
                self.assertEqual(response.status,status)
                self.assertEqual(body(response)["error"]["code"],code)

    def test_provider_private_exception_not_reflected(self):
        with patch.object(service,"send_owned_microsoft_message",side_effect=RuntimeError("private token and recipients")):
            response=http.microsoft_send_response(request())
        self.assertEqual(response.status,503)
        self.assertNotIn("private token",response.body.decode())

    def test_other_methods_never_send(self):
        for method in ("GET","HEAD","DELETE","PUT","PATCH","OPTIONS"):
            with self.subTest(method=method),patch.object(service,"send_owned_microsoft_message") as send:
                reply=http.microsoft_send_response(request(method=method))
                self.assertEqual(reply.status,405)
                self.assertIn(("Allow","POST"),reply.headers)
                send.assert_not_called()


if __name__=="__main__":
    unittest.main()
