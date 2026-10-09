"""Deterministic security tests for owner-bound Microsoft tokens (no network)."""
from __future__ import annotations

import copy
import io
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs

from api.inboxes import microsoft_token_authority as auth

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
OWNER = "owner@example.com"
ACCOUNT = "e93090af-0bcd-4768-a08c-93b9c3e55190"
GEN1 = "a" * 43
GEN2 = "b" * 43
TOKEN = "example-access-token-123456789012"
REFRESH = "example-refresh-token-123456789012"


def authority(*, owner=OWNER, mailbox="music-inbox", email="dj@example.com",
              account=ACCOUNT, generation=GEN1):
    return auth.MicrosoftMailboxAuthority(owner, mailbox, email, account, generation)


def payload(*, access=TOKEN, refresh=REFRESH, expires=3600,
            scope="User.Read Mail.Read offline_access"):
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "Bearer",
        "expires_in": expires,
        "scope": scope,
    }


class FakeDurableCAS:
    def __init__(self):
        self.records = {}
        self.reads = []
        self.writes = []
        self.on_write = None

    def read(self, config, key):
        self.reads.append(key)
        serialized = self.records.get(key)
        return (json.loads(serialized), serialized, None) if serialized else (None, None, None)

    def write(self, config, key, expected, record):
        self.writes.append((key, expected, copy.deepcopy(record)))
        if self.on_write:
            self.on_write(key, expected, record)
        if self.records.get(key) != expected:
            return None, {"code": "gmail_token_write_conflict"}
        encoded = json.dumps(record, separators=(",", ":"), sort_keys=True)
        self.records[key] = encoded
        return copy.deepcopy(record), None


class FakeResponse:
    def __init__(self, value):
        self.value = value if isinstance(value, bytes) else json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_unused):
        return False

    def read(self, limit):
        return self.value[:limit]


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        if isinstance(self.response, Exception):
            raise self.response
        return FakeResponse(self.response)


def refreshed(*, access="new-access-token-123456789012", refresh="new-refresh-token-123456789012",
              expires=3600, scope=None):
    v = {"access_token": access, "refresh_token": refresh,
         "token_type": "Bearer", "expires_in": expires}
    if scope is not None:
        v["scope"] = scope
    return v


class TokenHarness:
    def setUp(self):
        self.db = FakeDurableCAS()
        patchers = [
            patch.object(auth.oauth_store, "_resolve_durable_store_config", return_value={"backend": "mock"}),
            patch.object(auth.oauth_store, "_read_durable_record_snapshot", side_effect=self.db.read),
            patch.object(auth.oauth_store, "_write_google_durable_record_if_unchanged", side_effect=self.db.write),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def connect(self, a=None, p=None, **kwargs):
        return auth.persist_owned_microsoft_token(a or authority(), p or payload(), now=NOW, **kwargs)

class OwnedMicrosoftTokenTests(TokenHarness, unittest.TestCase):
    def test_first_connect_owner_bound_durable_create(self):
        record = self.connect()
        self.assertEqual(record["owner_email"], OWNER)
        self.assertEqual(record["email"], "dj@example.com")
        self.assertEqual(record["account_id"], ACCOUNT)
        self.assertEqual(record["credential_generation"], GEN1)
        self.assertEqual(record["provider"], "microsoft")
        self.assertEqual(len(self.db.writes), 1)
        key, serialized, _new = self.db.writes[0]
        self.assertIsNone(serialized)
        self.assertTrue(key.startswith(auth.KEY_PREFIX))
        self.assertNotIn(OWNER, key)
        self.assertNotIn("dj@example.com", key)
        self.assertIsNotNone(auth.load_owned_microsoft_token(authority())["access_token"])

    def test_canonical_email_forms_share_key(self):
        self.connect()
        a = authority(owner="OWNER@EXAMPLE.COM", email="DJ@EXAMPLE.COM")
        self.assertEqual(auth.load_owned_microsoft_token(a)["email"], "dj@example.com")

    def test_different_owner_same_mailbox_is_isolated(self):
        self.connect()
        other = authority(owner="other@example.com")
        with self.assertRaises(auth.MicrosoftTokenError) as caught:
            auth.load_owned_microsoft_token(other)
        self.assertEqual(caught.exception.code, "microsoft_reconnect_required")
        self.connect(other, payload(access="other-access-token-12345678901"))
        self.assertEqual(len(self.db.records), 2)

    def test_same_owner_different_mailbox_is_isolated(self):
        self.connect()
        other = authority(mailbox="another-inbox")
        self.connect(other, payload(access="other-access-token-12345678901"))
        self.assertNotEqual(
            auth.load_owned_microsoft_token(other)["access_token"],
            auth.load_owned_microsoft_token(authority())["access_token"],
        )

    def test_connect_never_overwrites_active_tokens(self):
        self.connect()
        with self.assertRaises(auth.MicrosoftTokenError) as caught:
            self.connect(p=payload(access="attacker-access-token-123456"))
        self.assertEqual(caught.exception.code, "microsoft_token_write_conflict")
        self.assertEqual(len(self.db.writes), 1)

    def test_wrong_graph_account_cannot_reconnect(self):
        self.connect()
        with self.assertRaises(auth.MicrosoftTokenError) as caught:
            self.connect(
                authority(generation=GEN2, account="different-account"),
                mode="reconnect", previous_generation=GEN1,
            )
        self.assertEqual(caught.exception.code, "microsoft_reconnect_required")
        self.assertEqual(len(self.db.writes), 1)

    def test_wrong_mailbox_email_cannot_reconnect(self):
        self.connect()
        with self.assertRaises(auth.MicrosoftTokenError):
            self.connect(authority(email="other@example.com", generation=GEN2),
                         mode="reconnect", previous_generation=GEN1)

    def test_new_reconnect_changes_generation_atomically(self):
        self.connect()
        new = authority(generation=GEN2)
        record = self.connect(
            new, payload(access="new-access-token-123456789", refresh="new-refresh-token-123456789"),
            mode="reconnect", previous_generation=GEN1,
        )
        self.assertEqual(record["credential_generation"], GEN2)
        self.assertEqual(len(self.db.writes), 2)
        self.assertIsNotNone(self.db.writes[1][1])
        self.assertEqual(auth.load_owned_microsoft_token(new)["access_token"], "new-access-token-123456789")
        with self.assertRaises(auth.MicrosoftTokenError):
            auth.load_owned_microsoft_token(authority())

    def test_reconnect_cannot_reuse_old_refresh_token(self):
        self.connect()
        p = payload()
        del p["refresh_token"]
        with self.assertRaises(auth.MicrosoftTokenError) as caught:
            self.connect(authority(generation=GEN2), p, mode="reconnect", previous_generation=GEN1)
        self.assertEqual(caught.exception.code, "microsoft_token_payload_invalid")

    def test_reconnect_requires_exact_previous_generation(self):
        self.connect()
        with self.assertRaises(auth.MicrosoftTokenError):
            self.connect(authority(generation=GEN2), mode="reconnect", previous_generation="c"*43)
        self.assertEqual(len(self.db.writes), 1)

    def test_missing_durable_storage_fails_before_any_write(self):
        with patch.object(auth.oauth_store, "_resolve_durable_store_config", return_value=None):
            with self.assertRaises(auth.MicrosoftTokenError) as caught:
                self.connect()
        self.assertEqual(caught.exception.code, "microsoft_token_store_unavailable")
        self.assertFalse(self.db.writes)

    def test_malformed_existing_record_is_fail_closed(self):
        self.connect()
        key = auth._key(authority())
        self.db.records[key] = json.dumps({"provider":"microsoft", "access_token":"malformed"})
        with self.assertRaises(auth.MicrosoftTokenError) as caught:
            auth.load_owned_microsoft_token(authority())
        self.assertEqual(caught.exception.code, "microsoft_token_record_invalid")

    def test_malformed_owner_authority_no_storage(self):
        invalid = [
            authority(owner="noemail"),
            authority(mailbox="../../bad"),
            authority(generation="a"*42),
            authority(account="with\nnewline"),
            authority(email="untrusted invalid"),
        ]
        for a in invalid:
            with self.subTest(a=a):
                with self.assertRaises(auth.MicrosoftTokenError):
                    self.connect(a)
        self.assertFalse(self.db.reads)

    def test_invalid_token_payloads_are_rejected(self):
        invalid = [
            {"token_type": 5},
            payload(access="short"),
            payload(refresh="short"),
            payload(scope="Mail.Read offline_access"),
            payload(scope="https://malicious.example/Mail.Read User.Read"),
            payload(expires=True),
            payload(expires="3600"),
            payload(expires=0),
            payload(expires=999999999),
            payload(access="bad\nnewlines-token-12345"),
            payload(access=TOKEN + "é"),
        ]
        for p in invalid:
            with self.subTest(p=p.get("scope")):
                with self.assertRaises(auth.MicrosoftTokenError):
                    self.connect(p=p)
        self.assertFalse(self.db.writes)

    def test_unauthorized_key_changed_between_read_and_write(self):
        self.db.on_write = lambda key, expected, record: self.db.records.update({key: "another-writer"})
        with self.assertRaises(auth.MicrosoftTokenError) as caught:
            self.connect()
        self.assertEqual(caught.exception.code, "microsoft_token_write_conflict")

    def test_forged_cas_write_acknowledgment_is_rejected(self):
        with patch.object(
            auth.oauth_store, "_write_google_durable_record_if_unchanged",
            return_value=({"tampered": True}, None),
        ):
            with self.assertRaises(auth.MicrosoftTokenError) as caught:
                self.connect()
        self.assertEqual(caught.exception.code, "microsoft_token_store_unavailable")


class MicrosoftRefreshTests(TokenHarness, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.patcher = patch.dict(os.environ, {
            "MICROSOFT_CLIENT_ID": "unit-test-app-client-id",
            "MICROSOFT_CLIENT_SECRET": "unit-test-app-secret",
            "MICROSOFT_OAUTH_TENANT": "organizations",
        })
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_unexpired_token_is_not_refreshed(self):
        self.connect()
        opener = FakeOpener(AssertionError("should not call"))
        result = auth.ensure_owned_microsoft_access_token(authority(), now=NOW, opener=opener)
        self.assertEqual(result["access_token"], TOKEN)
        self.assertFalse(opener.calls)

    def test_expired_token_is_refreshed_and_rotated_with_cas(self):
        self.connect(p=payload(expires=120))
        opener = FakeOpener(refreshed())
        result = auth.ensure_owned_microsoft_access_token(authority(), now=NOW, opener=opener)
        self.assertEqual(result["access_token"], "new-access-token-123456789012")
        self.assertEqual(result["refresh_token"], "new-refresh-token-123456789012")
        self.assertEqual(result["credential_generation"], GEN1)
        self.assertEqual(result["created_at"], NOW.isoformat())
        self.assertEqual(len(opener.calls), 1)
        request, timeout = opener.calls[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(timeout, 20)
        self.assertEqual(request.full_url, "https://login.microsoftonline.com/organizations/oauth2/v2.0/token")
        posted = parse_qs(request.data.decode("utf-8"))
        self.assertEqual(posted["grant_type"], ["refresh_token"])
        self.assertEqual(posted["refresh_token"], [REFRESH])
        self.assertEqual(posted["client_id"], ["unit-test-app-client-id"])
        self.assertEqual(len(self.db.writes), 2)
        self.assertEqual(auth.load_owned_microsoft_token(authority())["access_token"], result["access_token"])

    def test_missing_scope_on_refresh_preserves_original_permissions(self):
        self.connect(p=payload(expires=120))
        opener = FakeOpener(refreshed(scope=None))
        updated = auth.ensure_owned_microsoft_access_token(authority(), now=NOW, opener=opener)
        self.assertEqual(updated["scope"], "User.Read Mail.Read offline_access")

    def test_missing_new_refresh_token_preserves_current_refresh_token(self):
        self.connect(p=payload(expires=120))
        response = refreshed()
        del response["refresh_token"]
        updated = auth.ensure_owned_microsoft_access_token(
            authority(), now=NOW, opener=FakeOpener(response)
        )
        self.assertEqual(updated["refresh_token"], REFRESH)

    def test_explicit_bad_refreshed_scopes_fail_closed(self):
        self.connect(p=payload(expires=120))
        with self.assertRaises(auth.MicrosoftTokenError) as caught:
            auth.ensure_owned_microsoft_access_token(
                authority(), now=NOW,
                opener=FakeOpener(refreshed(scope="User.Read")),
            )
        self.assertEqual(caught.exception.code, "microsoft_token_payload_invalid")
        self.assertEqual(len(self.db.writes), 1)

    def test_refresh_network_fail_safe(self):
        self.connect(p=payload(expires=120))
        with self.assertRaises(auth.MicrosoftTokenError) as caught:
            auth.ensure_owned_microsoft_access_token(
                authority(), now=NOW, opener=FakeOpener(URLError("private token details")),
            )
        self.assertEqual(caught.exception.code, "microsoft_refresh_unavailable")
        self.assertNotIn("private", str(caught.exception))

    def test_invalid_grant_requires_reconnect(self):
        self.connect(p=payload(expires=120))
        error = HTTPError("url", 400, "private details", {}, io.BytesIO(b"tokens"))
        with self.assertRaises(auth.MicrosoftTokenError) as caught:
            auth.ensure_owned_microsoft_access_token(
                authority(), now=NOW, opener=FakeOpener(error),
            )
        self.assertEqual(caught.exception.code, "microsoft_reconnect_required")
        self.assertEqual(len(self.db.writes), 1)

    def test_invalid_grant_after_concurrent_refresh_accepts_exact_winner(self):
        self.connect(p=payload(expires=120))
        key = auth._key(authority())
        def rotated(_request, *, timeout):
            record = json.loads(self.db.records[key])
            record["access_token"] = "winner-access-token-1234567890"
            record["refresh_token"] = "winner-refresh-token-123456789"
            record["expires_at"] = (NOW + timedelta(hours=1)).isoformat()
            self.db.records[key] = json.dumps(record, sort_keys=True, separators=(",", ":"))
            raise HTTPError("url", 400, "invalid_grant", {}, io.BytesIO(b"secret"))
        result = auth.ensure_owned_microsoft_access_token(authority(), now=NOW, opener=rotated)
        self.assertEqual(result["access_token"], "winner-access-token-1234567890")
        self.assertEqual(len(self.db.writes), 1)

    def test_concurrent_reconnect_does_not_get_adopted_on_invalid_grant(self):
        self.connect(p=payload(expires=120))
        replacement = authority(generation=GEN2)
        def rotated(_request, *, timeout):
            self.connect(
                replacement, payload(access="different-grant-access-token-1234", refresh="different-grant-refresh-token-1234"),
                mode="reconnect", previous_generation=GEN1,
            )
            raise HTTPError("url", 400, "invalid_grant", {}, io.BytesIO(b"secret"))
        with self.assertRaises(auth.MicrosoftTokenError) as caught:
            auth.ensure_owned_microsoft_access_token(authority(), now=NOW, opener=rotated)
        self.assertEqual(caught.exception.code, "microsoft_reconnect_required")

    def test_oversized_token_response_rejected(self):
        self.connect(p=payload(expires=120))
        with self.assertRaises(auth.MicrosoftTokenError):
            auth.ensure_owned_microsoft_access_token(
                authority(), now=NOW, opener=FakeOpener(b"x"*(auth.MAX_TOKEN_RESPONSE_BYTES + 1))
            )

    def test_unsupported_tenant_is_rejected_before_http(self):
        self.connect(p=payload(expires=120))
        with patch.dict(os.environ, {"MICROSOFT_OAUTH_TENANT": "evil/../tenant"}):
            opener = FakeOpener(refreshed())
            with self.assertRaises(auth.MicrosoftTokenError) as caught:
                auth.ensure_owned_microsoft_access_token(authority(), now=NOW, opener=opener)
            self.assertEqual(caught.exception.code, "microsoft_oauth_configuration_unavailable")
            self.assertFalse(opener.calls)

    def test_stale_refresh_does_not_override_reconnect_winner(self):
        self.connect(p=payload(expires=120))
        new_authority = authority(generation=GEN2)
        def concurrent_reconnect(key, expected, record):
            if record["credential_generation"] == GEN1:
                self.db.on_write = None
                self.connect(
                    new_authority,
                    payload(access="new-winner-access-token-123456", refresh="new-winner-refresh-token-12345"),
                    mode="reconnect", previous_generation=GEN1,
                )
        self.db.on_write = concurrent_reconnect
        with self.assertRaises(auth.MicrosoftTokenError) as caught:
            auth.ensure_owned_microsoft_access_token(
                authority(), now=NOW, opener=FakeOpener(refreshed()),
            )
        self.assertEqual(caught.exception.code, "microsoft_reconnect_required")
        self.assertEqual(auth.load_owned_microsoft_token(new_authority)["access_token"], "new-winner-access-token-123456")

    def test_refresh_race_returns_valid_same_generation_winner(self):
        self.connect(p=payload(expires=120))
        key = auth._key(authority())
        def concurrent_refresh(k, expected, record):
            self.db.on_write = None
            current = json.loads(self.db.records[key])
            current["access_token"] = "concurrent-access-token-123456"
            current["refresh_token"] = "concurrent-refresh-token-123456"
            current["expires_at"] = (NOW + timedelta(hours=1)).isoformat()
            self.db.records[key] = json.dumps(current, separators=(",", ":"), sort_keys=True)
        self.db.on_write = concurrent_refresh
        record = auth.ensure_owned_microsoft_access_token(authority(), now=NOW, opener=FakeOpener(refreshed()))
        self.assertEqual(record["access_token"], "concurrent-access-token-123456")
        self.assertEqual(len(self.db.writes), 2)

    def test_malformed_token_store_record_cannot_force_refresh(self):
        self.connect(p=payload(expires=120))
        key = auth._key(authority())
        r = json.loads(self.db.records[key])
        r["owner_email"] = "wrong-owner@example.com"
        self.db.records[key] = json.dumps(r)
        opener = FakeOpener(refreshed())
        with self.assertRaises(auth.MicrosoftTokenError):
            auth.ensure_owned_microsoft_access_token(authority(), now=NOW, opener=opener)
        self.assertFalse(opener.calls)


if __name__ == "__main__":
    unittest.main()
