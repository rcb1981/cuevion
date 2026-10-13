from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from api.inboxes import microsoft_outbound_ledger as ledger
from api.inboxes.microsoft_token_authority import MicrosoftMailboxAuthority

AUTH = MicrosoftMailboxAuthority("owner@example.com","microsoft-box","music@example.com","graph-account","g"*43)
ID = "i"*43
COMPOSE = {"mode":"new","to":["artist@example.com"],"subject":"Private release","body":"Full private message"}

class CAS:
    def __init__(self):
        self.records = {}
        self.writes = []
        self.bad_read = False
        self.bad_write = False
        self.concurrent = None
    def read(self, config, key):
        if self.bad_read:
            return None,None,{"code":"unavailable"}
        raw = self.records.get(key)
        return (json.loads(raw),raw,None) if raw is not None else (None,None,None)
    def write(self, config, key, expected, value):
        self.writes.append((key,expected,copy.deepcopy(value)))
        if self.bad_write:
            return None,{"code":"unavailable"}
        if self.concurrent:
            self.concurrent(key)
        if self.records.get(key)!=expected:
            return None,{"code":"gmail_token_write_conflict"}
        self.records[key]=json.dumps(value,sort_keys=True,separators=(",",":"))
        return copy.deepcopy(value),None

class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.cas=CAS()
        self.mocks=(
            patch.object(ledger.store,"_resolve_durable_store_config",return_value={"backend":"fake"}),
            patch.object(ledger.store,"_read_durable_record_snapshot",side_effect=self.cas.read),
            patch.object(ledger.store,"_write_google_durable_record_if_unchanged",side_effect=self.cas.write),
        )
        for mock in self.mocks:
            mock.start()
            self.addCleanup(mock.stop)

    def test_reserve_is_durable_and_never_stores_private_mail_data(self):
        result=ledger.reserve_outbound_attempt(AUTH,ID,COMPOSE)
        self.assertEqual(result,{"new":True,"phase":"reserved"})
        self.assertEqual(len(self.cas.records),1)
        key,content=next(iter(self.cas.records.items()))
        self.assertTrue(key.startswith(ledger.PREFIX))
        for private in ("owner@example.com","music@example.com","artist@example.com","Private release","Full private message"):
            self.assertNotIn(private,key)
            self.assertNotIn(private,content)

    def test_same_id_never_starts_second_reservation(self):
        self.assertTrue(ledger.reserve_outbound_attempt(AUTH,ID,COMPOSE)["new"])
        result=ledger.reserve_outbound_attempt(AUTH,ID,COMPOSE)
        self.assertEqual(result,{"new":False,"phase":"reserved"})
        self.assertEqual(len(self.cas.writes),1)

    def test_same_id_changed_recipients_or_body_denied(self):
        ledger.reserve_outbound_attempt(AUTH,ID,COMPOSE)
        for changes in ({"body":"different"},{"to":["another@example.com"]}):
            with self.subTest(changes=changes),self.assertRaises(ledger.OutboundLedgerError) as caught:
                ledger.reserve_outbound_attempt(AUTH,ID,{**COMPOSE,**changes})
            self.assertEqual(caught.exception.code,"microsoft_send_attempt_conflict")

    def test_cannot_reuse_attempt_under_new_generation(self):
        ledger.reserve_outbound_attempt(AUTH,ID,COMPOSE)
        changed=MicrosoftMailboxAuthority(AUTH.owner_email,AUTH.mailbox_id,AUTH.email,AUTH.account_id,"n"*43)
        with self.assertRaises(ledger.OutboundLedgerError):
            ledger.reserve_outbound_attempt(changed,ID,COMPOSE)

    def test_lifecycle_cas_and_terminal_duplicate(self):
        ledger.reserve_outbound_attempt(AUTH,ID,COMPOSE)
        self.assertEqual(ledger.advance_outbound_attempt(
            AUTH,ID,COMPOSE,from_phase="reserved",to_phase="draft_prepared",draft_id="draft-id"
        )["phase"],"draft_prepared")
        ledger.advance_outbound_attempt(AUTH,ID,COMPOSE,from_phase="draft_prepared",to_phase="send_started")
        ledger.advance_outbound_attempt(AUTH,ID,COMPOSE,from_phase="send_started",to_phase="accepted_by_microsoft")
        self.assertEqual(ledger.reserve_outbound_attempt(AUTH,ID,COMPOSE),{
            "new":False,"phase":"accepted_by_microsoft"
        })
        self.assertEqual(len(self.cas.writes),4)
        with self.assertRaises(ledger.OutboundLedgerError):
            ledger.advance_outbound_attempt(AUTH,ID,COMPOSE,from_phase="draft_prepared",to_phase="send_started")

    def test_no_invalid_transition_or_empty_draft_id(self):
        ledger.reserve_outbound_attempt(AUTH,ID,COMPOSE)
        for phase, next_state in (
            ("reserved","send_started"),("reserved","accepted_by_microsoft"),
            ("send_started","draft_prepared"),
        ):
            with self.assertRaises(ledger.OutboundLedgerError):
                ledger.advance_outbound_attempt(AUTH,ID,COMPOSE,from_phase=phase,to_phase=next_state)
        with self.assertRaises(ledger.OutboundLedgerError):
            ledger.advance_outbound_attempt(AUTH,ID,COMPOSE,from_phase="reserved",to_phase="draft_prepared",draft_id="")
        self.assertEqual(len(self.cas.writes),1)

    def test_concurrent_compare_and_swap_denied(self):
        self.cas.concurrent=lambda key:self.cas.records.update({key:'{"foreign":"record"}'})
        with self.assertRaises(ledger.OutboundLedgerError) as caught:
            ledger.reserve_outbound_attempt(AUTH,ID,COMPOSE)
        self.assertEqual(caught.exception.code,"microsoft_send_attempt_conflict")

    def test_missing_durable_store_prevents_reservation(self):
        with patch.object(ledger.store,"_resolve_durable_store_config",return_value=None):
            with self.assertRaises(ledger.OutboundLedgerError) as caught:
                ledger.reserve_outbound_attempt(AUTH,ID,COMPOSE)
        self.assertEqual(caught.exception.code,"microsoft_send_store_unavailable")
        self.assertFalse(self.cas.writes)

    def test_lost_ack_does_not_claim_success(self):
        self.cas.bad_write=True
        with self.assertRaises(ledger.OutboundLedgerError) as caught:
            ledger.reserve_outbound_attempt(AUTH,ID,COMPOSE)
        self.assertEqual(caught.exception.code,"microsoft_send_store_unavailable")

    def test_unreadable_saved_record_cannot_send(self):
        ledger.reserve_outbound_attempt(AUTH,ID,COMPOSE)
        self.cas.bad_read=True
        with self.assertRaises(ledger.OutboundLedgerError):
            ledger.reserve_outbound_attempt(AUTH,ID,COMPOSE)

    def test_invalid_id_no_storage(self):
        for bad in ("", "../token", "a"*42, "x"*44, "special?token"):
            with self.subTest(bad=bad),self.assertRaises(ledger.OutboundLedgerError):
                ledger.reserve_outbound_attempt(AUTH,bad,COMPOSE)
        self.assertFalse(self.cas.writes)


    def test_inspection_requires_owner_generation_and_never_mutates(self):
        self.assertIsNone(ledger.inspect_outbound_attempt(AUTH, ID))
        ledger.reserve_outbound_attempt(AUTH, ID, COMPOSE)
        before = len(self.cas.writes)
        self.assertEqual(
            ledger.inspect_outbound_attempt(AUTH, ID),
            {"phase": "reserved", "draftId": None},
        )
        ledger.advance_outbound_attempt(
            AUTH, ID, COMPOSE,
            from_phase="reserved", to_phase="draft_prepared", draft_id="draft-id",
        )
        self.assertEqual(
            ledger.inspect_outbound_attempt(AUTH, ID),
            {"phase": "draft_prepared", "draftId": "draft-id"},
        )
        self.assertEqual(len(self.cas.writes), before + 1)
        new_generation=MicrosoftMailboxAuthority(
            AUTH.owner_email, AUTH.mailbox_id, AUTH.email, AUTH.account_id, "n"*43,
        )
        with self.assertRaises(ledger.OutboundLedgerError):
            ledger.inspect_outbound_attempt(new_generation, ID)

    def test_inspection_does_not_expose_private_compose_hash_or_tokens(self):
        ledger.reserve_outbound_attempt(AUTH, ID, COMPOSE)
        visible = ledger.inspect_outbound_attempt(AUTH, ID)
        self.assertEqual(set(visible), {"phase", "draftId"})
        for private in ("artist@example.com", "Private release", "Full private message"):
            self.assertNotIn(private, str(visible))

    def test_inspection_fails_closed_on_tampered_digest_and_draft(self):
        ledger.reserve_outbound_attempt(AUTH, ID, COMPOSE)
        key = next(iter(self.cas.records))
        initial = json.loads(self.cas.records[key])
        for changed in (
            {"digest": "bad"},
            {"scope": "wrong-authority"},
            {"attemptId": "different"},
            {"phase": "accepted_by_microsoft", "draftId": "../other"},
        ):
            self.cas.records[key] = json.dumps({**initial, **changed})
            with self.subTest(changed=changed), self.assertRaises(ledger.OutboundLedgerError):
                ledger.inspect_outbound_attempt(AUTH, ID)
        self.cas.records[key] = json.dumps(initial)

if __name__=="__main__":
    unittest.main()
