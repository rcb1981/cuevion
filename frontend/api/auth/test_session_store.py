from __future__ import annotations

import base64
import hashlib
import json
import unittest
from dataclasses import replace

from api.auth import session_store


def _encoded(byte: int, length: int) -> str:
    return base64.urlsafe_b64encode(bytes([byte]) * length).rstrip(b"=").decode("ascii")


SECRET = "test-session-secret-that-is-long-enough-for-hkdf"
USER_ID = "usr_" + _encoded(1, 16)
WORKSPACE_ID = "wsp_" + _encoded(2, 16)
ISSUER = "https://cuevion-dev.eu.auth0.com/"
SUBJECT = "auth0|test-subject"


class MemoryCommands:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.commands: list[list[object]] = []
        self.now = 1_000
        self.expirations: dict[str, int] = {}

    def __call__(self, command: list[object]) -> dict[str, object]:
        self.commands.append(list(command))
        operation = command[0]
        key = command[3] if operation == "EVAL" else command[1]
        if key in self.expirations and self.expirations[key] <= self.now:
            self.values.pop(key, None)
            self.expirations.pop(key, None)
        if operation == "SET":
            if command[-1] == "NX" and key in self.values:
                return {"result": None}
            self.values[key] = command[2]
            self.expirations[key] = self.now + command[4]
            return {"result": "OK"}
        if operation == "GET":
            return {"result": self.values.get(key)}
        if operation == "DEL":
            existed = key in self.values
            self.values.pop(key, None)
            self.expirations.pop(key, None)
            return {"result": 1 if existed else 0}
        if operation == "EVAL":
            current = self.values.get(key)
            if current is None or self.expirations.get(key, self.now) <= self.now:
                return {"result": 0}
            if current == command[5]:
                return {"result": 1}
            if current != command[4]:
                return {"result": 0}
            self.values[key] = command[5]
            return {"result": 1}
        raise AssertionError(operation)


class Headers:
    def __init__(self, cookie: str):
        self.cookie = cookie

    def raw_items(self):
        return [("host", "app.cuevion.com"), ("cookie", self.cookie)]


def _random_source():
    values = iter((bytes([4]) * 32, bytes([5]) * 32))
    return lambda length: next(values)


def _new_session(commands: MemoryCommands, now: int = 1_000, *, workspace_role="owner"):
    store = session_store.AuthSessionStore(commands)
    record, cookie = session_store.create_server_session(
        store,
        secret=SECRET,
        user_id=USER_ID,
        workspace_id=WORKSPACE_ID,
        security_epoch=3,
        issuer=ISSUER,
        subject=SUBJECT,
        now=now,
        random_bytes=_random_source(),
        workspace_role=workspace_role,
    )
    cookie_value = cookie.split(";", 1)[0]
    return store, record, cookie, Headers(cookie_value)


class ServerSessionTests(unittest.TestCase):
    def test_session_is_opaque_and_only_digest_is_used_as_kv_key(self):
        commands = MemoryCommands()
        _store, record, cookie, _headers = _new_session(commands)
        raw_token = _encoded(4, 32)
        command = commands.commands[0]
        self.assertEqual(command[0], "SET")
        self.assertTrue(str(command[1]).startswith(session_store.SESSION_KEY_PREFIX))
        self.assertNotIn(raw_token, str(command[1]))
        self.assertNotIn(raw_token, str(command[2]))
        self.assertNotIn(cookie.split(";", 1)[0], str(command[2]))
        stored = json.loads(str(command[2]))
        self.assertEqual(stored["sessionId"], record.session_id)
        self.assertEqual(
            set(stored),
            {
                "schemaVersion",
                "sessionId",
                "userId",
                "workspaceId",
                "workspaceRole",
                "securityEpoch",
                "issuer",
                "subject",
                "createdAt",
                "expiresAt",
                "bindingDigest",
            },
        )
        self.assertEqual(stored["workspaceRole"], "owner")

    def test_workspace_roles_roundtrip_without_owner_promotion(self):
        for role in ("owner", "admin", "member"):
            with self.subTest(role=role):
                commands = MemoryCommands()
                store, record, _cookie, headers = _new_session(commands, workspace_role=role)
                loaded, _lookup = session_store.load_server_session(
                    store, headers=headers, secret=SECRET, now=1_001,
                )
                self.assertEqual(record.workspace_role, role)
                self.assertEqual(loaded.workspace_role, role)
                self.assertEqual(json.loads(commands.commands[0][2])["workspaceRole"], role)

    def test_legacy_session_without_role_retains_owner_compatibility(self):
        commands = MemoryCommands()
        store, record, _cookie, headers = _new_session(commands)
        key = next(iter(commands.values))
        payload = json.loads(commands.values[key])
        del payload["workspaceRole"]
        commands.values[key] = json.dumps(payload)
        loaded, _lookup = session_store.load_server_session(
            store, headers=headers, secret=SECRET, now=1_001,
        )
        self.assertEqual(loaded, record)
        self.assertEqual(loaded.workspace_role, "owner")

    def test_malformed_present_role_fails_closed_and_cannot_be_written(self):
        for role in (None, True, 1, "OWNER", "guest", "", " member ", [], {}):
            with self.subTest(role_type=type(role).__name__):
                commands = MemoryCommands()
                store, _record, _cookie, headers = _new_session(commands)
                key = next(iter(commands.values))
                payload = json.loads(commands.values[key])
                payload["workspaceRole"] = role
                commands.values[key] = json.dumps(payload)
                with self.assertRaises(session_store.SessionStoreUnavailable):
                    session_store.load_server_session(
                        store, headers=headers, secret=SECRET, now=1_001,
                    )
                new_commands = MemoryCommands()
                with self.assertRaises(ValueError):
                    _new_session(new_commands, workspace_role=role)
                self.assertEqual(new_commands.commands, [])

    def test_cookie_flags_and_eight_hour_lifetime_are_exact(self):
        commands = MemoryCommands()
        _store, _record, cookie, _headers = _new_session(commands)
        self.assertIn("__Host-cuevion_session=", cookie)
        self.assertIn("Path=/", cookie)
        self.assertIn("Max-Age=28800", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn("Domain", cookie)

    def test_successful_load_and_missing_record(self):
        commands = MemoryCommands()
        store, record, _cookie, headers = _new_session(commands)
        loaded, lookup = session_store.load_server_session(
            store, headers=headers, secret=SECRET, now=1_001
        )
        self.assertEqual(loaded, record)
        self.assertIsInstance(lookup, str)
        commands.values.clear()
        missing, missing_lookup = session_store.load_server_session(
            store, headers=headers, secret=SECRET, now=1_001
        )
        self.assertIsNone(missing)
        self.assertEqual(missing_lookup, lookup)

    def test_expired_record_is_deleted(self):
        commands = MemoryCommands()
        store, record, _cookie, headers = _new_session(commands)
        loaded, lookup = session_store.load_server_session(
            store, headers=headers, secret=SECRET, now=record.expires_at
        )
        self.assertIsNone(loaded)
        self.assertNotIn(session_store.SESSION_KEY_PREFIX + str(lookup), commands.values)
        self.assertEqual(commands.commands[-1][0], "DEL")

    def test_binding_mismatch_is_deleted(self):
        commands = MemoryCommands()
        store, _record, _cookie, headers = _new_session(commands)
        key = next(iter(commands.values))
        payload = json.loads(commands.values[key])
        payload["bindingDigest"] = _encoded(9, 32)
        commands.values[key] = json.dumps(payload, separators=(",", ":"))
        loaded, _lookup = session_store.load_server_session(
            store, headers=headers, secret=SECRET, now=1_001
        )
        self.assertIsNone(loaded)
        self.assertNotIn(key, commands.values)

    def test_wrong_key_and_malformed_cookie_are_rejected(self):
        commands = MemoryCommands()
        store, _record, _cookie, headers = _new_session(commands)
        loaded, lookup = session_store.load_server_session(
            store,
            headers=headers,
            secret="different-session-secret-that-is-long-enough-for-hkdf",
            now=1_001,
        )
        self.assertIsNone(loaded)
        self.assertIsNotNone(lookup)
        malformed, malformed_lookup = session_store.load_server_session(
            store,
            headers=Headers("__Host-cuevion_session=invalid"),
            secret=SECRET,
            now=1_001,
        )
        self.assertIsNone(malformed)
        self.assertIsNone(malformed_lookup)

    def test_transaction_marker_rejects_replay_and_contains_only_digest(self):
        commands = MemoryCommands()
        store = session_store.AuthSessionStore(commands)
        transaction_id = _encoded(7, 32)
        self.assertTrue(store.consume_transaction(transaction_id, SECRET, 300))
        self.assertFalse(store.consume_transaction(transaction_id, SECRET, 300))
        key = str(commands.commands[0][1])
        self.assertTrue(key.startswith(session_store.TRANSACTION_USE_KEY_PREFIX))
        self.assertNotIn(transaction_id, key)

    def test_unavailable_transport_is_value_free(self):
        def unavailable(_command):
            raise RuntimeError("contains-sensitive-value")

        store = session_store.AuthSessionStore(unavailable)
        with self.assertRaises(session_store.SessionStoreUnavailable) as raised:
            store.get(_encoded(8, 32))
        self.assertNotIn("sensitive", str(raised.exception))

    def test_logout_cookie_is_host_only_and_expired(self):
        cookie = session_store.clear_session_cookie()
        self.assertIn("__Host-cuevion_session=", cookie)
        self.assertIn("Max-Age=0", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertNotIn("Domain", cookie)


class TeamInviteContinuationTests(unittest.TestCase):
    def setUp(self):
        self.commands = MemoryCommands()
        self.store = session_store.AuthSessionStore(self.commands)
        self.binding = session_store.TeamInviteContinuationBinding(
            _encoded(11, 32), USER_ID, WORKSPACE_ID, ISSUER, SUBJECT, 1_000, 2_000,
        )
        self.token = "tinv_test." + "a" * 43
        self.record = session_store.TeamInviteContinuationRecord(
            binding=self.binding, invitation_id="tinv_test",
            token_digest=hashlib.sha256(self.token.encode()).hexdigest(),
            inviter_user_id="usr_" + _encoded(12, 16), invitee_email="member@example.com",
            invitation_expires_at=1_600_000, raw_invite_token=self.token,
            created_at=1_000, expires_at=1_600,
        )

    def _put(self, *, now=1_000):
        self.commands.now = now
        self.assertTrue(self.store.put_team_invite_continuation(self.record, secret=SECRET, now=now))
        return next(iter(self.commands.values))

    def _get(self, *, binding=None, now=1_001):
        return self.store.get_team_invite_continuation(binding or self.binding, secret=SECRET, now=now)

    def _complete(self, *, record=None, binding=None, now=1_001):
        return self.store.complete_team_invite_continuation(
            record or self.record, binding or self.binding, secret=SECRET, now=now,
        )

    def test_pending_roundtrip_is_server_only_session_bound_and_ttl_bounded(self):
        key = self._put(now=1_010)
        self.assertEqual(self._get(now=1_010), self.record)
        self.assertEqual(self.commands.commands[0][3:], ["EX", 590, "NX"])
        self.assertTrue(key.startswith(session_store.TEAM_INVITE_CONTINUATION_KEY_PREFIX))
        self.assertNotIn(self.binding.session_id, key)
        self.assertNotIn(self.token, key)
        self.assertNotIn(self.token, repr(self.record))
        self.assertNotIn(USER_ID, repr(self.binding))
        self.assertEqual(json.loads(self.commands.values[key])["rawInviteToken"], self.token)

    def test_every_trusted_session_binding_must_match_exactly(self):
        self._put()
        for change in (
            {"session_id": _encoded(13, 32)}, {"user_id": "usr_" + _encoded(13, 16)},
            {"workspace_id": "wsp_" + _encoded(13, 16)},
            {"issuer": "https://other-issuer.example/"}, {"subject": "auth0|other"},
            {"session_created_at": 999}, {"session_expires_at": 2_001},
        ):
            with self.subTest(change=change):
                binding = replace(self.binding, **change)
                self.assertIsNone(self._get(binding=binding))
                self.assertFalse(self._complete(binding=binding))
        self.assertEqual(self._get(), self.record)

    def test_secret_and_transaction_key_domain_are_independent(self):
        key = self._put()
        self.assertIsNone(self.store.get_team_invite_continuation(
            self.binding, secret="other-test-session-secret-with-enough-entropy", now=1_001,
        ))
        self.assertTrue(self.store.consume_transaction(self.binding.session_id, SECRET, 600))
        transaction_key = self.commands.commands[-1][1]
        self.assertNotEqual(key.rsplit(":", 1)[1], transaction_key.rsplit(":", 1)[1])

    def test_pending_cannot_be_replaced_or_rebound_to_another_invite_incarnation(self):
        key = self._put()
        original = self.commands.values[key]
        token = "tinv_replacement." + "b" * 43
        replacement = replace(self.record, invitation_id="tinv_replacement", raw_invite_token=token,
                              token_digest=hashlib.sha256(token.encode()).hexdigest())
        self.assertFalse(self.store.put_team_invite_continuation(replacement, secret=SECRET, now=1_001))
        self.assertEqual(self.commands.values[key], original)
        self.assertFalse(self._complete(record=replacement))
        self.assertEqual(self.commands.values[key], original)

    def test_invalid_record_contracts_are_rejected_without_raw_token_in_errors(self):
        for change in (
            {"schema_version": True}, {"schema_version": 2}, {"status": "accepted"},
            {"raw_invite_token": None}, {"raw_invite_token": self.token + "x"},
            {"token_digest": "0" * 64}, {"invitation_id": "tinv_different"},
            {"inviter_user_id": WORKSPACE_ID}, {"invitee_email": "Member@example.com"},
            {"invitee_email": "member\x00@example.com"}, {"created_at": True},
            {"created_at": 999}, {"expires_at": 1_601},
            {"invitation_expires_at": 1_599_999},
            {"status": "complete"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError) as raised:
                replace(self.record, **change)
            self.assertNotIn(self.token, str(raised.exception))
            self.assertNotIn(self.token, repr(raised.exception))
        self.assertEqual(self.commands.commands, [])

    def test_malformed_duplicate_unknown_and_terminal_secret_payloads_fail_closed(self):
        key = self._put()
        original = json.loads(self.commands.values[key])
        malformed = [None, 1, "", "[]", "{" + '"status":"pending",' * 2 + "}", "x" * 4097, "\ud800"]
        for change in (
            {"schemaVersion": True}, {"schemaVersion": 2}, {"unexpected": "value"},
            {"status": "unknown"}, {"expiresAt": True}, {"rawInviteToken": "sensitive-invalid"},
            {"status": "complete"}, {"tokenDigest": "0" * 64},
        ):
            malformed.append(json.dumps({**original, **change}))
        for raw in malformed:
            with self.subTest(raw_type=type(raw).__name__):
                self.commands.values[key] = raw
                if raw is None:
                    self.assertIsNone(self._get())
                else:
                    with self.assertRaises(session_store.SessionStoreUnavailable) as raised:
                        self._get()
                    self.assertNotIn(self.token, repr(raised.exception))

    def test_missing_future_expired_and_invalid_clock_never_resolve_or_complete(self):
        self.assertIsNone(self._get())
        self.assertFalse(self._complete())
        self._put()
        for now in (999, 1_600, 2_000, True):
            self.assertIsNone(self._get(now=now))
            self.assertFalse(self._complete(now=now))
        self.assertFalse(self.store.put_team_invite_continuation(self.record, secret=SECRET, now=1_600))

    def test_completion_erases_token_atomically_and_retains_original_expiry(self):
        key = self._put()
        expiry = self.commands.expirations[key]
        self.commands.now = 1_100
        self.assertTrue(self._complete(now=1_100))
        terminal = self._get(now=1_100)
        self.assertEqual(terminal.status, "complete")
        self.assertIsNone(terminal.raw_invite_token)
        self.assertEqual(terminal.binding, self.binding)
        self.assertEqual(terminal.token_digest, self.record.token_digest)
        self.assertNotIn(self.token, self.commands.values[key])
        self.assertNotIn("rawInviteToken", json.loads(self.commands.values[key]))
        self.assertEqual(self.commands.expirations[key], expiry)
        completion = self.commands.commands[-2]
        self.assertEqual(completion[0], "EVAL")
        self.assertIn("'KEEPTTL'", completion[1])
        self.assertNotIn("GETDEL", completion[1])
        self.assertNotIn("'DEL'", completion[1])
        self.assertNotIn(self.token, completion[5])

    def test_same_completion_retries_are_idempotent_without_ttl_extension(self):
        key = self._put()
        self.assertTrue(self._complete())
        terminal = self.commands.values[key]
        self.commands.now = 1_599
        self.assertTrue(self._complete(now=1_599))
        self.assertEqual(self.commands.values[key], terminal)
        self.assertEqual(self.commands.expirations[key], 1_600)
        self.commands.now = 1_600
        self.assertFalse(self._complete(now=1_599))
        self.assertIsNone(self._get(now=1_599))

    def test_completion_cas_rejects_changed_pending_or_terminal_record(self):
        key = self._put()
        for change in (
            {"inviter_user_id": "usr_" + _encoded(13, 16)},
            {"invitee_email": "different@example.com"},
            {"invitation_expires_at": 1_601_000}, {"expires_at": 1_599},
        ):
            with self.subTest(change=change):
                changed = replace(self.record, **change)
                self.assertFalse(self._complete(record=changed))
                self.assertEqual(self._get(), self.record)
        self.assertTrue(self._complete())
        self.assertFalse(self._complete(record=replace(self.record, invitee_email="different@example.com")))
        terminal = self._get()
        self.assertFalse(self.store.put_team_invite_continuation(terminal, secret=SECRET, now=1_001))
        self.assertIsNone(terminal.raw_invite_token)

    def test_transport_errors_and_invalid_cas_results_are_value_free(self):
        for response in ({"result": True}, {"result": 2}, {"unexpected": "sensitive"}):
            store = session_store.AuthSessionStore(lambda _command: response)
            with self.assertRaises(session_store.SessionStoreUnavailable) as raised:
                store.complete_team_invite_continuation(self.record, self.binding, secret=SECRET, now=1_001)
            self.assertNotIn(self.token, repr(raised.exception))
        def unavailable(_command):
            raise RuntimeError(self.token)
        store = session_store.AuthSessionStore(unavailable)
        with self.assertRaises(session_store.SessionStoreUnavailable) as raised:
            store.put_team_invite_continuation(self.record, secret=SECRET, now=1_001)
        self.assertNotIn(self.token, repr(raised.exception))


if __name__ == "__main__":
    unittest.main()
