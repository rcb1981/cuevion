"""Durable Microsoft-only Filtered/Learning owner, CAS, and integrity tests."""
from __future__ import annotations

from copy import deepcopy
from email.message import Message
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from api.inboxes import microsoft_filtered_learning as learning
from api.inboxes.microsoft_mailbox_access import MicrosoftMailboxAccessError

BOX = "microsoft-example-01"
OWNER = "owner@example.com"
HEADERS = object()
SENDER = "dj@example.com"


_ROUTE_FILE = Path(__file__).with_name("microsoft-filtered-learning.py")
_ROUTE_SPEC = spec_from_file_location("microsoft_filtered_learning_http_route", _ROUTE_FILE)
assert _ROUTE_SPEC and _ROUTE_SPEC.loader
_ROUTE = module_from_spec(_ROUTE_SPEC)
_ROUTE_SPEC.loader.exec_module(_ROUTE)


def _request(payload: dict, *, origin: str = "https://app.cuevion.com", method="POST"):
    encoded = json.dumps(payload).encode("utf-8")
    headers = Message()
    for key, value in (
        ("Host", "app.cuevion.com"), ("Origin", origin),
        ("Content-Type", "application/json"), ("Content-Length", str(len(encoded))),
    ):
        headers[key] = value
    return SimpleNamespace(command=method, headers=headers, rfile=BytesIO(encoded))


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.record = {
            "email": OWNER, "managedInboxes": [{"id": BOX, "provider": "microsoft"}],
            "uiPreferences": {"themeMode": "Dark"},
        }
        self.cas_conflict_once = False
        self.cas_count = 0
        self.auth_count = 0
        for name, value in (
            ("resolve_owned_microsoft_mailbox", self.authorize),
            ("user_config_store.resolve_user_config_store", lambda: ({"store": "valid"}, None)),
            ("user_config_store.read_user_config_record", self.read),
            ("user_config_store.write_user_config_record_if_unchanged", self.cas),
        ):
            target = learning if not name.startswith("user_config_store.") else learning.user_config_store
            attribute = name.split(".")[-1]
            patcher = patch.object(target, attribute, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def authorize(self, headers, mailbox_id):
        self.assertIs(headers, HEADERS)
        self.assertEqual(mailbox_id, BOX)
        self.auth_count += 1
        return SimpleNamespace(owner_email=OWNER)

    def read(self, store, owner):
        self.assertEqual(store, {"store": "valid"})
        self.assertEqual(owner, OWNER)
        return {"status": "ok", "config": deepcopy(self.record)}

    def cas(self, store, owner, expected, replacement):
        self.assertEqual(owner, OWNER)
        self.cas_count += 1
        if self.cas_conflict_once:
            self.cas_conflict_once = False
            self.record["uiPreferences"]["themeMode"] = "Light"
            return {"status": "conflict"}
        self.assertEqual(expected, self.record)
        self.record = deepcopy(replacement)
        return {"status": "ok"}

    def call(self, action, **fields):
        return learning.apply_owned_learning_action(HEADERS, {
            "mailboxId": BOX, "action": action, **fields,
        })

    def test_bootstrap_and_individual_updates_persist_across_reloads(self):
        result = self.call("bootstrap", placements={SENDER: "keep"}, rules={})
        self.assertTrue(result["exists"])
        self.assertEqual(self.call("read")["placements"], {SENDER: "keep"})
        self.call("setPlacement", sender="promo@example.com", placement="show_less")
        self.call("setRule", sender=SENDER, rule={"priority": "important", "label": "Demo"})
        read = self.call("read")
        self.assertEqual(read["placements"], {SENDER: "keep", "promo@example.com": "show_less"})
        self.assertEqual(read["rules"], {SENDER: {"priority": "important", "label": "Demo"}})
        self.assertEqual(self.record["uiPreferences"], {"themeMode": "Dark"})
        self.assertEqual(self.record["managedInboxes"][0]["id"], BOX)

    def test_second_browser_bootstrap_never_overwrites_first(self):
        self.call("bootstrap", placements={SENDER: "keep"}, rules={})
        self.call("bootstrap", placements={SENDER: "show_less"}, rules={})
        self.assertEqual(self.call("read")["placements"][SENDER], "keep")
        self.assertEqual(self.cas_count, 1)

    def test_parallel_settings_edit_cas_retries_and_preserves_both(self):
        self.cas_conflict_once = True
        self.call("setRule", sender=SENDER, rule={"priority": "normal", "label": "Promo"})
        self.assertEqual(self.record["uiPreferences"]["themeMode"], "Light")
        self.assertEqual(self.record[learning.FIELD][BOX]["rules"][SENDER]["priority"], "normal")
        self.assertEqual(self.cas_count, 2)
        self.assertEqual(self.auth_count, 2)

    def test_invalid_rules_cannot_mutate_config(self):
        for sender, rule in (("Someone <dj@example.com>", {"priority": "important", "label": "Demo"}),
                             ("dj@example.com", {"priority": "important", "label": "SuperAdmin"}),
                             ("dj@example.com", {"priority": "important", "label": "Demo", "token": "no"})):
            with self.subTest(sender=sender, rule=rule):
                with self.assertRaises(learning.MicrosoftFilteredLearningError):
                    self.call("setRule", sender=sender, rule=rule)
        self.assertEqual(self.cas_count, 0)

    def test_http_same_origin_and_post_only_before_owner_or_storage(self):
        payload = {"mailboxId": BOX, "action": "read"}
        with patch.object(_ROUTE, "apply_owned_learning_action", side_effect=AssertionError("called")):
            wrong_origin = _ROUTE.learning_response(_request(payload, origin="https://attacker.invalid"))
            wrong_method = _ROUTE.learning_response(_request(payload, method="GET"))
            self.assertEqual(wrong_origin.status, 403)
            self.assertEqual(wrong_method.status, 405)

    def test_http_rejects_oversized_payload_before_auth(self):
        request = _request({"mailboxId": BOX, "action": "read"})
        request.headers.replace_header("Content-Length", "65537")
        with patch.object(_ROUTE, "apply_owned_learning_action", side_effect=AssertionError("called")):
            self.assertEqual(_ROUTE.learning_response(request).status, 413)

    def test_nonstring_placement_or_rule_is_rejected_without_500(self):
        for placement in ({"show_less": True}, [], False, 0, None):
            with self.subTest(placement=placement):
                with self.assertRaises(learning.MicrosoftFilteredLearningError) as error:
                    self.call("setPlacement", sender=SENDER, placement=placement)
                self.assertEqual(error.exception.code, "invalid_learning")
        with self.assertRaises(learning.MicrosoftFilteredLearningError) as error:
            self.call("setRule", sender=SENDER, rule={"priority": [], "label": "Demo"})
        self.assertEqual(error.exception.code, "invalid_learning")
        self.assertEqual(self.cas_count, 0)

    def test_max_sender_limit_rejected_not_truncated(self):
        placements = {f"s{i}@example.com": "keep" for i in range(learning.MAX_SENDERS)}
        self.call("bootstrap", placements=placements, rules={})
        with self.assertRaises(learning.MicrosoftFilteredLearningError) as error:
            self.call("setPlacement", sender="extra@example.com", placement="show_less")
        self.assertEqual(error.exception.code, "learning_limit_reached")
        self.assertEqual(self.cas_count, 1)

    def test_malformed_server_state_fails_closed_without_writing(self):
        self.record[learning.FIELD] = {BOX: {"placements": {"not an address": "keep"}, "rules": {}}}
        with self.assertRaises(learning.MicrosoftFilteredLearningError) as error:
            self.call("read")
        self.assertEqual(error.exception.code, "learning_unavailable")
        self.assertEqual(self.cas_count, 0)

    def test_unauthorized_mailbox_is_never_read_or_saved(self):
        with patch.object(learning, "resolve_owned_microsoft_mailbox",
                          side_effect=MicrosoftMailboxAccessError("microsoft_unauthorized")):
            with self.assertRaises(learning.MicrosoftFilteredLearningError) as error:
                self.call("read")
        self.assertEqual(error.exception.code, "microsoft_unauthorized")
        self.assertEqual(self.cas_count, 0)


if __name__ == "__main__":
    unittest.main()
