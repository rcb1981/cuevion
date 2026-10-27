"""Cross-module no-network tests of one-shot Microsoft outbound saga."""
from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from api.inboxes import microsoft_outbound_service as service
from api.inboxes import microsoft_outbound_ledger as ledger
from api.inboxes.microsoft_token_authority import MicrosoftMailboxAuthority
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes.microsoft_mailbox_access import MicrosoftMailboxAccessError

AUTH = MicrosoftMailboxAuthority(
    "owner@example.com","microsoft-box","music@example.com","graph-account","g"*43
)
TOKEN="microsoft-owner-access-token-1234567"
ATTEMPT="a"*43
COMPOSE={"mode":"new","to":["artist@example.com"],"subject":"Release proposal","body":"Private release mail"}
HEADER=(("Cookie","test"),)

class MemoryCAS:
    def __init__(self):
        self.rows={}
        self.writes=[]
        self.fail=False
    def read(self,conf,key):
        raw=self.rows.get(key)
        return (json.loads(raw),raw,None) if raw else (None,None,None)
    def write(self,conf,key,prior,obj):
        self.writes.append((key,prior,copy.deepcopy(obj)))
        if self.fail:return None,{"code":"unavailable"}
        if self.rows.get(key)!=prior:return None,{"code":"gmail_token_write_conflict"}
        self.rows[key]=json.dumps(obj,sort_keys=True,separators=(",",":"))
        return copy.deepcopy(obj),None

class OutboundSagaTests(unittest.TestCase):
    def setUp(self):
        # Exercise the existing lower-level contract in an isolated fake-service
        # test; source release remains OFF and separate negative tests cover it.
        gate = patch('api.inboxes.microsoft_legacy_send_policy.blocked', return_value=False)
        gate.start(); self.addCleanup(gate.stop)
        self.db=MemoryCAS()
        self.events=[]
        self.active=AUTH
        self.lease={"status":"acquired","token":"l"*43}
        self.release={"status":"released"}
        self.prepare_error=None
        self.submit_error=None
        self.incoming_token=TOKEN

        def prepare(token,compose):
            self.events.append("prepare")
            if self.prepare_error:raise self.prepare_error
            return {"draftId":"draft-verified","conversationId":"conversation-1"}
        def send(token,draft_id):
            self.events.append("send")
            if self.submit_error:raise self.submit_error
            return {"status":"accepted_by_microsoft","draftId":draft_id}
        def resolve(headers,mid):
            self.events.append("authorize")
            if self.active is None:raise MicrosoftMailboxAccessError("microsoft_unauthorized")
            return self.active
        def token(headers,mid,**opts):
            self.events.append("token")
            self.assertEqual(opts,{"require_read_write":True,"require_mail_send":True})
            return self.incoming_token, AUTH
        def recheck(headers,mid,captured):
            self.events.append("recheck")
            if self.active!=captured:
                raise MicrosoftMailboxAccessError("microsoft_reconnect_required")
        def lease(owner,mid):
            self.events.append("lease")
            return self.lease
        def release(owner,mid,token):
            self.events.append("release")
            return self.release

        for module,name,callback in (
            (ledger.store,"_resolve_durable_store_config",lambda:{"backend":"fake"}),
            (ledger.store,"_read_durable_record_snapshot",self.db.read),
            (ledger.store,"_write_google_durable_record_if_unchanged",self.db.write),
            (service.graph,"prepare_microsoft_draft",prepare),
            (service.graph,"submit_prepared_microsoft_draft",send),
            (service.owned,"resolve_owned_microsoft_mailbox",resolve),
            (service.owned,"_owned_token",token),
            (service.owned,"_verify_unchanged_owned_mailbox",recheck),
            (service,"acquire_mailbox_mutation_lease",lease),
            (service,"release_mailbox_mutation_lease",release),
        ):
            p=patch.object(module,name,side_effect=callback)
            p.start()
            self.addCleanup(p.stop)

    def action(self,compose=None, attempt=ATTEMPT):
        return service.send_owned_microsoft_message(
            HEADER,AUTH.mailbox_id,attempt_id=attempt,compose=compose or COMPOSE
        )

    def test_success_is_acceptance_not_delivery(self):
        result=self.action()
        self.assertEqual(result,{
            "attemptId":ATTEMPT, "status":"accepted_by_microsoft",
            "deliveryConfirmed":False,"sentCopyConfirmed":False,"replayed":False,
        })
        self.assertEqual(self.events.count("send"),1)
        self.assertEqual(self.events.count("prepare"),1)
        self.assertEqual(self.events.count("release"),1)
        self.assertEqual(len(self.db.writes),4)
        stages=[record["phase"] for _key,_prior,record in self.db.writes]
        self.assertEqual(stages,["reserved","draft_prepared","send_started","accepted_by_microsoft"])
        self.assertLess(self.events.index("lease"),self.events.index("prepare"))
        self.assertLess(self.events.index("recheck"),self.events.index("send"))
        raw=next(iter(self.db.rows.values()))
        for value in ("artist@example.com","Release proposal","Private release mail",TOKEN):
            self.assertNotIn(value,raw)

    def test_retry_same_attempt_returns_receipt_no_second_graph_send(self):
        self.action()
        result=self.action()
        self.assertEqual(result["status"],"accepted_by_microsoft")
        self.assertTrue(result["replayed"])
        self.assertEqual(self.events.count("prepare"),1)
        self.assertEqual(self.events.count("send"),1)
        self.assertEqual(len(self.db.writes),4)

    def test_uncertain_graph_send_blocks_duplicate_request(self):
        self.submit_error=MicrosoftGraphError("microsoft_send_unconfirmed")
        with self.assertRaises(MicrosoftGraphError):
            self.action()
        self.assertEqual(self.events.count("send"),1)
        with self.assertRaises(service.MicrosoftOutboundError) as caught:
            self.action()
        self.assertEqual(caught.exception.code,"microsoft_send_attempt_already_started")
        self.assertEqual(self.events.count("send"),1)
        self.assertEqual(len(self.db.writes),3)

    def test_draft_creation_uncertain_blocks_duplicate(self):
        self.prepare_error=MicrosoftGraphError("microsoft_draft_unconfirmed")
        with self.assertRaises(MicrosoftGraphError):
            self.action()
        with self.assertRaises(service.MicrosoftOutboundError):
            self.action()
        self.assertEqual(self.events.count("prepare"),1)
        self.assertEqual(self.events.count("send"),0)

    def test_ledger_store_failure_prevents_graph_create(self):
        self.db.fail=True
        with self.assertRaises(service.MicrosoftOutboundError) as caught:
            self.action()
        self.assertEqual(caught.exception.code,"microsoft_send_store_unavailable")
        self.assertNotIn("prepare",self.events)
        self.assertNotIn("send",self.events)
        self.assertEqual(self.events.count("release"),1)

    def test_missing_or_invalid_attempt_never_calls_graph(self):
        for value in ("", "b"*42, "../../private"):
            self.events=[]
            with self.subTest(value=value),self.assertRaises(service.MicrosoftOutboundError):
                self.action(attempt=value)
            self.assertFalse(self.events)

    def test_invalid_compose_never_acquires_lease(self):
        bad={**COMPOSE,"to":["not an email"]}
        with self.assertRaises(MicrosoftGraphError):
            self.action(compose=bad)
        self.assertFalse(self.events)

    def test_held_lease_prevents_duplicate_graph(self):
        self.lease={"status":"held"}
        with self.assertRaises(service.MicrosoftOutboundError) as caught:
            self.action()
        self.assertEqual(caught.exception.code,"microsoft_mutation_in_progress")
        self.assertNotIn("prepare",self.events)
        self.assertNotIn("send",self.events)

    def test_owner_revoked_after_draft_preparation_prevents_send(self):
        def patched(token,compose):
            self.events.append("prepare")
            self.active=None
            return {"draftId":"draft-verified","conversationId":"conversation-1"}
        with patch.object(service.graph,"prepare_microsoft_draft",side_effect=patched):
            with self.assertRaises(MicrosoftMailboxAccessError):
                self.action()
        self.assertEqual(self.events.count("send"),0)
        self.assertEqual(self.events.count("release"),1)

    def test_account_mismatch_before_reservation_prevents_graph(self):
        self.active=MicrosoftMailboxAuthority(AUTH.owner_email,AUTH.mailbox_id,AUTH.email,
                                              AUTH.account_id,"s"*43)
        with self.assertRaises(MicrosoftMailboxAccessError):
            self.action()
        self.assertNotIn("prepare",self.events)

    def test_failed_release_after_202_never_reports_confirmed(self):
        self.release={"status":"unavailable"}
        with self.assertRaises(service.MicrosoftOutboundError) as caught:
            self.action()
        self.assertEqual(caught.exception.code,"microsoft_send_unconfirmed")
        self.assertEqual(self.events.count("send"),1)
        # The ledger records Graph acceptance, so the same attempt still
        # cannot resend even if the client gets an error from lease release.
        self.release={"status":"released"}
        self.assertTrue(self.action()["replayed"])
        self.assertEqual(self.events.count("send"),1)

    def test_same_attempt_different_content_is_denied_without_graph(self):
        self.action()
        with self.assertRaises(service.MicrosoftOutboundError) as caught:
            self.action(compose={**COMPOSE,"body":"different private body"})
        self.assertEqual(caught.exception.code,"microsoft_send_attempt_conflict")
        self.assertEqual(self.events.count("send"),1)


if __name__=="__main__":
    unittest.main()
