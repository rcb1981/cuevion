from __future__ import annotations

import base64
import json
import unittest

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

    def __call__(self, command: list[object]) -> dict[str, object]:
        self.commands.append(list(command))
        operation = command[0]
        key = command[1]
        if operation == "SET":
            if command[-1] == "NX" and key in self.values:
                return {"result": None}
            self.values[key] = command[2]
            return {"result": "OK"}
        if operation == "GET":
            return {"result": self.values.get(key)}
        if operation == "DEL":
            existed = key in self.values
            self.values.pop(key, None)
            return {"result": 1 if existed else 0}
        raise AssertionError(operation)


class Headers:
    def __init__(self, cookie: str):
        self.cookie = cookie

    def raw_items(self):
        return [("host", "app.cuevion.com"), ("cookie", self.cookie)]


def _random_source():
    values = iter((bytes([4]) * 32, bytes([5]) * 32))
    return lambda length: next(values)


def _new_session(commands: MemoryCommands, now: int = 1_000):
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
                "securityEpoch",
                "issuer",
                "subject",
                "createdAt",
                "expiresAt",
                "bindingDigest",
            },
        )

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


if __name__ == "__main__":
    unittest.main()
