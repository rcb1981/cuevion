from __future__ import annotations

import importlib.util
import inspect
import io
import json
import os
import sys
import types
import unittest
from copy import deepcopy
from http.client import IncompleteRead
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

FRONTEND_DIR = Path(__file__).resolve().parents[2]
INBOX_API_DIR = Path(__file__).resolve().parent
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

from api.inboxes import oauth_token_store  # noqa: E402


def install_route_import_stubs() -> None:
    """Keep focused pure-helper tests independent of optional auth crypto deps."""
    user_config_store = types.ModuleType("api.user_config_store")

    def unavailable(*_args, **_kwargs):
        return {"status": "unavailable"}

    user_config_store.acquire_mailbox_mutation_lease = unavailable
    user_config_store.read_user_config_record = unavailable
    user_config_store.release_mailbox_mutation_lease = unavailable
    user_config_store.resolve_authenticated_member_authority = lambda *_args, **_kwargs: (None, None)
    user_config_store.resolve_owned_managed_inbox_record = unavailable
    user_config_store.resolve_user_config_store = lambda: (None, {"code": "unavailable"})
    user_config_store.write_user_config_record_if_unchanged = unavailable
    sys.modules["api.user_config_store"] = user_config_store
    sys.modules["user_config_store"] = user_config_store

    auth_http = types.ModuleType("api.auth.http")
    auth_http.snapshot_request_headers = lambda _request: ()
    auth_runtime = types.ModuleType("api.auth.runtime")

    class AuthenticatedMemberContext:
        pass

    class MemberResolutionOutcome:
        AUTHENTICATED = "authenticated"
        UNAVAILABLE = "unavailable"

    auth_runtime.AuthenticatedMemberContext = AuthenticatedMemberContext
    auth_runtime.MemberResolutionOutcome = MemberResolutionOutcome
    auth_runtime.resolve_authenticated_member = lambda _headers: None
    sys.modules["api.auth.http"] = auth_http
    sys.modules["api.auth.runtime"] = auth_runtime

    import api.auth as auth_package

    auth_package.http = auth_http
    auth_package.runtime = auth_runtime

    user_config = types.ModuleType("api.user.config")
    user_config._classify_stored_onboarding_session = lambda value: (None, value)
    sys.modules["api.user.config"] = user_config


install_route_import_stubs()

from api.inboxes import authenticated_gmail  # noqa: E402


def load_route_module(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        module_name,
        INBOX_API_DIR / filename,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {filename}.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


connect_oauth = load_route_module(
    "cuevion_gmail_lifecycle_connect_oauth",
    "connect-oauth.py",
)
oauth_callback = load_route_module(
    "cuevion_gmail_lifecycle_oauth_callback",
    "oauth-callback.py",
)

OWNER_EMAIL = "owner@example.com"
MAILBOX_EMAIL = "artist@example.com"
MAILBOX_ID = "gmail-artist"
OLD_GENERATION = "o" * 43
NEW_GENERATION = "n" * 43
STATE_SECRET = "state-secret-for-focused-regression-tests"


def callback_record(
    *,
    access_token: str = "access-old",
    refresh_token: str = "refresh-old",
    generation: str | None = OLD_GENERATION,
) -> dict:
    return oauth_callback.build_google_token_record(
        email=MAILBOX_EMAIL,
        owner_email=OWNER_EMAIL,
        token_payload={
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "scope": "gmail.readonly gmail.send",
            "expires_in": 3600,
        },
        credential_generation=generation,
    )


def token_store_record(
    *,
    access_token: str = "access-old",
    refresh_token: str = "refresh-old",
    generation: str | None = OLD_GENERATION,
) -> dict:
    return oauth_token_store.build_google_token_record(
        email=MAILBOX_EMAIL,
        owner_email=OWNER_EMAIL,
        token_payload={
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "scope": "gmail.readonly gmail.send",
            "expires_in": 3600,
        },
        credential_generation=generation,
    )


class SignedReconnectStateTests(unittest.TestCase):
    def test_connect_state_round_trips_through_callback_verifier(self):
        state, code_verifier = connect_oauth.build_signed_state(
            "google",
            MAILBOX_EMAIL,
            OWNER_EMAIL,
            STATE_SECRET,
            member_user_id="member-1",
            member_workspace_id="workspace-1",
            mode="reconnect",
            mailbox_id=MAILBOX_ID,
            expected_email=MAILBOX_EMAIL,
            credential_generation=NEW_GENERATION,
        )

        payload, error = oauth_callback.verify_signed_state(
            state,
            STATE_SECRET,
        )
        self.assertIsNone(error)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["mode"], "reconnect")
        self.assertEqual(payload["mailboxId"], MAILBOX_ID)
        self.assertEqual(payload["expected_email"], MAILBOX_EMAIL)
        self.assertEqual(payload["credential_generation"], NEW_GENERATION)
        self.assertEqual(payload["code_verifier"], code_verifier)
        self.assertNotIn("owner_email", payload)
        self.assertTrue(
            oauth_callback.verify_owner_binding(
                payload,
                OWNER_EMAIL,
                STATE_SECRET,
                member_user_id="member-1",
                member_workspace_id="workspace-1",
            )
        )

        tampered_state = f"{state[:-1]}{'A' if state[-1] != 'A' else 'B'}"
        tampered_payload, tampered_error = oauth_callback.verify_signed_state(
            tampered_state,
            STATE_SECRET,
        )
        self.assertIsNone(tampered_payload)
        self.assertEqual(tampered_error, "invalid_state")

    def test_initial_state_has_no_reconnect_target(self):
        state, _ = connect_oauth.build_signed_state(
            "google",
            MAILBOX_EMAIL,
            OWNER_EMAIL,
            STATE_SECRET,
            "main",
            member_user_id="member-1",
            member_workspace_id="workspace-1",
            mode="initial",
            credential_generation=NEW_GENERATION,
        )
        payload, error = oauth_callback.verify_signed_state(state, STATE_SECRET)
        self.assertIsNone(error)
        self.assertEqual(payload["mode"], "initial")
        self.assertEqual(payload["inboxPosition"], "main")
        self.assertNotIn("mailboxId", payload)
        self.assertNotIn("expected_email", payload)

    def test_oauth_configuration_is_checked_before_reconnect_reservation(self):
        source = inspect.getsource(connect_oauth.handler.do_POST)
        self.assertLess(
            source.index('client_id = os.getenv("GOOGLE_CLIENT_ID"'),
            source.index("reserve_authoritative_google_reconnect("),
        )


class TokenDurabilityTests(unittest.TestCase):
    def test_refresh_payload_preserves_reusable_fields_and_generation(self):
        existing = token_store_record()
        refreshed = oauth_token_store.build_google_token_record(
            email=MAILBOX_EMAIL,
            owner_email=OWNER_EMAIL,
            token_payload={"access_token": "access-new", "expires_in": 3600},
            existing_record=existing,
        )
        self.assertEqual(refreshed["refresh_token"], "refresh-old")
        self.assertEqual(refreshed["scope"], existing["scope"])
        self.assertEqual(
            refreshed["credential_generation"],
            OLD_GENERATION,
        )
        self.assertEqual(refreshed["created_at"], existing["created_at"])

    def test_callback_payload_preserves_refresh_scope_and_token_type(self):
        existing = callback_record()
        reconnected = oauth_callback.build_google_token_record(
            email=MAILBOX_EMAIL,
            owner_email=OWNER_EMAIL,
            token_payload={"access_token": "access-new", "expires_in": 3600},
            existing_record=existing,
            credential_generation=NEW_GENERATION,
        )
        self.assertEqual(reconnected["refresh_token"], "refresh-old")
        self.assertEqual(reconnected["scope"], existing["scope"])
        self.assertEqual(reconnected["token_type"], existing["token_type"])
        self.assertEqual(reconnected["credential_generation"], NEW_GENERATION)

    def test_partial_exact_owner_record_is_not_reusable(self):
        partial = {
            "provider": "google",
            "email": MAILBOX_EMAIL,
            "owner_email": OWNER_EMAIL,
            "refresh_token": "refresh-partial",
        }
        classification = oauth_callback._classify_existing_google_token_record(
            partial,
            normalized_email=MAILBOX_EMAIL,
            normalized_owner_email=OWNER_EMAIL,
        )
        self.assertEqual(
            classification,
            oauth_callback.GOOGLE_TOKEN_RECORD_MALFORMED_OR_AMBIGUOUS,
        )

    def test_exact_redis_snapshot_round_trips_original_bytes(self):
        raw_record = json.dumps(
            token_store_record(),
            separators=(",", ":"),
            sort_keys=False,
        )
        response = {
            "result": (
                oauth_token_store.GOOGLE_TOKEN_RAW_SNAPSHOT_PREFIX + raw_record
            )
        }
        with patch.object(
            oauth_token_store,
            "_perform_rest_request",
            return_value=(response, None),
        ) as perform:
            record, raw_snapshot, error = (
                oauth_token_store._read_durable_record_snapshot(
                    {"rest_url": "https://kv.test", "rest_token": "secret"},
                    "token-key",
                )
            )
        self.assertIsNone(error)
        self.assertEqual(record, json.loads(raw_record))
        self.assertEqual(raw_snapshot, raw_record)
        command = json.loads(perform.call_args.args[3].decode("utf-8"))
        self.assertEqual(command[0], "EVAL")
        self.assertEqual(command[1], oauth_token_store.GOOGLE_TOKEN_READ_EXACT_SCRIPT)

    def test_google_cas_write_has_no_expiry_and_microsoft_keeps_ttl(self):
        google_record = token_store_record()
        google_calls: list[tuple[str, str, bytes | None]] = []

        def google_rest(_config, method, path, body=None):
            google_calls.append((method, path, body))
            command = json.loads(body.decode("utf-8"))
            if command[1] == oauth_token_store.GOOGLE_TOKEN_READ_EXACT_SCRIPT:
                raw = json.dumps(
                    google_record,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                return {
                    "result": oauth_token_store.GOOGLE_TOKEN_RAW_SNAPSHOT_PREFIX
                    + raw
                }, None
            return {"result": 1}, None

        durable_config = {
            "backend": "vercel_kv_rest",
            "rest_url": "https://kv.test",
            "rest_token": "secret",
        }
        with patch.object(
            oauth_token_store,
            "_perform_rest_request",
            side_effect=google_rest,
        ):
            persisted, error = (
                oauth_token_store._write_google_durable_record_if_unchanged(
                    durable_config,
                    "google-key",
                    None,
                    google_record,
                )
            )
        self.assertIsNone(error)
        self.assertEqual(persisted, google_record)
        google_write = json.loads(google_calls[0][2].decode("utf-8"))
        self.assertEqual(google_write[:4], ["EVAL", oauth_token_store.GOOGLE_TOKEN_CREATE_IF_MISSING_SCRIPT, 1, "google-key"])
        self.assertNotIn("EXPIRE", google_write[1])
        self.assertNotIn("PEXPIRE", google_write[1])

        microsoft_record = oauth_token_store.build_microsoft_token_record(
            email=MAILBOX_EMAIL,
            token_payload={
                "access_token": "microsoft-access",
                "refresh_token": "microsoft-refresh",
                "expires_in": 3600,
            },
        )
        microsoft_paths: list[str] = []

        def microsoft_rest(_config, _method, path, body=None):
            microsoft_paths.append(path)
            if path.startswith("/set/"):
                return {"result": "OK"}, None
            raw = json.dumps(
                microsoft_record,
                separators=(",", ":"),
                sort_keys=True,
            )
            return {
                "result": oauth_token_store.GOOGLE_TOKEN_RAW_SNAPSHOT_PREFIX + raw
            }, None

        with patch.object(
            oauth_token_store,
            "_perform_rest_request",
            side_effect=microsoft_rest,
        ):
            persisted, error = oauth_token_store._write_microsoft_durable_record(
                durable_config,
                "microsoft-key",
                microsoft_record,
            )
        self.assertIsNone(error)
        self.assertEqual(persisted, microsoft_record)
        self.assertIn("?EX=2592000", microsoft_paths[0])

    def test_owned_legacy_google_key_is_migrated_with_conditional_persist(self):
        raw_record = json.dumps(token_store_record(), separators=(",", ":"), sort_keys=True)
        calls: list[list] = []

        def fake_rest(_config, _method, _path, body=None):
            command = json.loads(body.decode("utf-8"))
            calls.append(command)
            if command[1] == oauth_token_store.GOOGLE_TOKEN_READ_EXACT_SCRIPT:
                return {
                    "result": oauth_token_store.GOOGLE_TOKEN_RAW_SNAPSHOT_PREFIX
                    + raw_record
                }, None
            return {"result": 1}, None

        with patch.dict(
            os.environ,
            {"KV_REST_API_URL": "https://kv.test", "KV_REST_API_TOKEN": "secret"},
            clear=False,
        ), patch.object(
            oauth_token_store,
            "_perform_rest_request",
            side_effect=fake_rest,
        ):
            record, error = oauth_token_store.load_google_token_record_with_metadata(
                MAILBOX_EMAIL,
                owner_email=OWNER_EMAIL,
            )
        self.assertIsNone(error)
        self.assertTrue(record["_storage_durable"])
        self.assertEqual(calls[1][1], oauth_token_store.GOOGLE_TOKEN_PERSIST_IF_UNCHANGED_SCRIPT)
        self.assertEqual(calls[1][-1], raw_record)
        self.assertIn("PERSIST", calls[1][1])
        self.assertNotIn("EXPIRE", calls[1][1])

    def test_missing_durable_config_is_unavailable_not_missing(self):
        with patch.dict(
            os.environ,
            {"KV_REST_API_URL": "", "KV_REST_API_TOKEN": ""},
            clear=False,
        ), patch.object(
            oauth_token_store,
            "_read_runtime_store",
        ) as runtime_read:
            record, error = oauth_token_store.load_google_token_record_with_metadata(
                MAILBOX_EMAIL,
                owner_email=OWNER_EMAIL,
            )
        self.assertIsNone(record)
        self.assertEqual(error["code"], "gmail_token_store_unavailable")
        runtime_read.assert_not_called()

    def test_shared_persistence_requires_refresh_before_any_write(self):
        with patch.object(
            oauth_token_store,
            "_load_existing_google_record",
            return_value=("token-key", {"backend": "vercel_kv_rest"}, None, None, None),
        ), patch.object(
            oauth_token_store,
            "_persist_google_record",
        ) as persist:
            record, error = oauth_token_store.persist_google_token_record(
                email=MAILBOX_EMAIL,
                owner_email=OWNER_EMAIL,
                token_payload={"access_token": "access-new", "expires_in": 3600},
                credential_generation=NEW_GENERATION,
            )
        self.assertIsNone(record)
        self.assertEqual(error["code"], "invalid_token_payload")
        persist.assert_not_called()

    def test_shared_persistence_never_adopts_another_owner_refresh(self):
        other_owner_record = oauth_token_store.build_google_token_record(
            email=MAILBOX_EMAIL,
            owner_email="other-owner@example.com",
            token_payload={
                "access_token": "access-other",
                "refresh_token": "refresh-other",
                "expires_in": 3600,
            },
            credential_generation=OLD_GENERATION,
        )
        raw_record = json.dumps(
            other_owner_record,
            separators=(",", ":"),
            sort_keys=True,
        )
        with patch.object(
            oauth_token_store,
            "_load_existing_google_record",
            return_value=(
                "token-key",
                {"backend": "vercel_kv_rest"},
                other_owner_record,
                raw_record,
                None,
            ),
        ), patch.object(
            oauth_token_store,
            "_persist_google_record",
        ) as persist:
            record, error = oauth_token_store.persist_google_token_record(
                email=MAILBOX_EMAIL,
                owner_email=OWNER_EMAIL,
                token_payload={"access_token": "access-new", "expires_in": 3600},
                credential_generation=NEW_GENERATION,
            )
        self.assertIsNone(record)
        self.assertEqual(error["code"], "gmail_token_record_malformed")
        persist.assert_not_called()


class CallbackPersistenceTests(unittest.TestCase):
    def test_initial_connect_without_new_refresh_token_fails_closed(self):
        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value=None,
        ), patch.object(
            oauth_callback,
            "_read_runtime_store",
            return_value={},
        ), patch.object(
            oauth_callback,
            "_persist_runtime_record",
        ) as persist:
            record, error = oauth_callback.persist_google_token_record(
                email=MAILBOX_EMAIL,
                owner_email=OWNER_EMAIL,
                token_payload={"access_token": "access-new", "expires_in": 3600},
                mode="initial",
                credential_generation=NEW_GENERATION,
            )
        self.assertIsNone(record)
        self.assertEqual(error["_gmail_callback_failure_code"], "refresh_token_missing")
        persist.assert_not_called()

    def test_reconnect_may_preserve_only_exact_owner_refresh_token(self):
        existing = callback_record()
        key = oauth_callback._build_store_key(MAILBOX_EMAIL)

        def persist_runtime(_key, record):
            return deepcopy(record), None

        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value=None,
        ), patch.object(
            oauth_callback,
            "_read_runtime_store",
            return_value={key: existing},
        ), patch.object(
            oauth_callback,
            "_persist_runtime_record",
            side_effect=persist_runtime,
        ):
            record, error = oauth_callback.persist_google_token_record(
                email=MAILBOX_EMAIL,
                owner_email=OWNER_EMAIL,
                token_payload={"access_token": "access-new", "expires_in": 3600},
                mode="reconnect",
                credential_generation=NEW_GENERATION,
            )
        self.assertIsNone(error)
        self.assertEqual(record["refresh_token"], "refresh-old")
        self.assertEqual(record["credential_generation"], NEW_GENERATION)

    def test_reconnect_retries_when_stale_refresh_wins_first_cas(self):
        existing = callback_record()
        stale_refresh = oauth_callback.build_google_token_record(
            email=MAILBOX_EMAIL,
            owner_email=OWNER_EMAIL,
            token_payload={"access_token": "access-refreshed", "expires_in": 3600},
            existing_record=existing,
        )
        conflict = oauth_callback._google_token_owner_conflict_error()
        writes = 0

        def write_record(_config, _key, expected, next_record):
            nonlocal writes
            writes += 1
            if writes == 1:
                self.assertEqual(expected, existing)
                return None, conflict
            self.assertEqual(expected, stale_refresh)
            return deepcopy(next_record), None

        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value={"backend": "vercel_kv_rest"},
        ), patch.object(
            oauth_callback,
            "_read_durable_record",
            side_effect=[(existing, None), (stale_refresh, None)],
        ), patch.object(
            oauth_callback,
            "_write_durable_record",
            side_effect=write_record,
        ):
            record, error = oauth_callback.persist_google_token_record(
                email=MAILBOX_EMAIL,
                owner_email=OWNER_EMAIL,
                token_payload={"access_token": "access-callback", "expires_in": 3600},
                mode="reconnect",
                credential_generation=NEW_GENERATION,
            )
        self.assertIsNone(error)
        self.assertEqual(writes, 2)
        self.assertEqual(record["access_token"], "access-callback")
        self.assertEqual(record["refresh_token"], "refresh-old")
        self.assertEqual(record["credential_generation"], NEW_GENERATION)

    def test_reconnect_accepts_refresh_descendant_after_its_cas(self):
        existing = callback_record()
        descendant = oauth_callback.build_google_token_record(
            email=MAILBOX_EMAIL,
            owner_email=OWNER_EMAIL,
            token_payload={"access_token": "access-descendant", "expires_in": 3600},
            existing_record=existing,
            credential_generation=NEW_GENERATION,
        )
        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value={"backend": "vercel_kv_rest"},
        ), patch.object(
            oauth_callback,
            "_read_durable_record",
            side_effect=[(existing, None), (descendant, None)],
        ), patch.object(
            oauth_callback,
            "_write_durable_record",
            return_value=(None, oauth_callback._google_token_owner_conflict_error()),
        ):
            record, error = oauth_callback.persist_google_token_record(
                email=MAILBOX_EMAIL,
                owner_email=OWNER_EMAIL,
                token_payload={"access_token": "access-callback", "expires_in": 3600},
                mode="reconnect",
                credential_generation=NEW_GENERATION,
            )
        self.assertIsNone(error)
        self.assertEqual(record["access_token"], "access-descendant")
        self.assertEqual(record["credential_generation"], NEW_GENERATION)

    def test_stale_operational_refresh_accepts_new_reconnect_winner(self):
        old_record = token_store_record()
        reconnect_winner = token_store_record(
            access_token="access-reconnect",
            refresh_token="refresh-reconnect",
            generation=NEW_GENERATION,
        )
        winner_raw = json.dumps(
            reconnect_winner,
            separators=(",", ":"),
            sort_keys=True,
        )
        with patch.object(
            oauth_token_store,
            "_write_google_durable_record_if_unchanged",
            return_value=(
                None,
                {
                    "code": "gmail_token_write_conflict",
                    "message": "conflict",
                },
            ),
        ), patch.object(
            oauth_token_store,
            "_read_durable_record_snapshot",
            return_value=(reconnect_winner, winner_raw, None),
        ), patch.object(
            oauth_token_store,
            "_clear_google_store_key_expiry",
            return_value=None,
        ):
            record, error = oauth_token_store._persist_google_record(
                normalized_email=MAILBOX_EMAIL,
                store_key="token-key",
                durable_config={"backend": "vercel_kv_rest"},
                expected_serialized_record="old-raw",
                record=old_record,
                accept_valid_winner_on_conflict=True,
            )
        self.assertIsNone(error)
        self.assertEqual(record["credential_generation"], NEW_GENERATION)
        self.assertEqual(record["refresh_token"], "refresh-reconnect")


class RefreshFailureClassificationTests(unittest.TestCase):
    def exchange_http_error(self, status: int, provider_error: str):
        http_error = HTTPError(
            oauth_token_store.GOOGLE_TOKEN_ENDPOINT,
            status,
            "provider error",
            {},
            io.BytesIO(json.dumps({"error": provider_error}).encode("utf-8")),
        )
        with patch.dict(
            os.environ,
            {"GOOGLE_CLIENT_ID": "client", "GOOGLE_CLIENT_SECRET": "secret"},
            clear=False,
        ), patch.object(
            oauth_token_store,
            "urlopen",
            side_effect=http_error,
        ):
            return oauth_token_store._exchange_google_refresh_token(
                refresh_token="refresh-token",
            )

    def test_definitive_and_temporary_refresh_failures_stay_distinct(self):
        cases = [
            (400, "invalid_grant", "gmail_refresh_invalid_grant"),
            (429, "rate_limit_exceeded", "gmail_refresh_rate_limited"),
            (503, "server_error", "gmail_refresh_unavailable"),
            (401, "invalid_client", "gmail_refresh_not_configured"),
            (400, "invalid_request", "gmail_refresh_failed"),
        ]
        for status, provider_error, expected_code in cases:
            with self.subTest(status=status, provider_error=provider_error):
                payload, error = self.exchange_http_error(status, provider_error)
                self.assertIsNone(payload)
                self.assertEqual(error["code"], expected_code)

        with patch.dict(
            os.environ,
            {"GOOGLE_CLIENT_ID": "client", "GOOGLE_CLIENT_SECRET": "secret"},
            clear=False,
        ), patch.object(
            oauth_token_store,
            "urlopen",
            side_effect=URLError("network unavailable"),
        ):
            payload, error = oauth_token_store._exchange_google_refresh_token(
                refresh_token="refresh-token",
            )
        self.assertIsNone(payload)
        self.assertEqual(error["code"], "gmail_refresh_unavailable")

    def test_http_error_body_read_failure_is_temporary(self):
        class FailingBody:
            def read(self, _limit):
                raise IncompleteRead(b"partial")

            def close(self):
                return None

        http_error = HTTPError(
            oauth_token_store.GOOGLE_TOKEN_ENDPOINT,
            503,
            "provider error",
            {},
            FailingBody(),
        )
        with patch.dict(
            os.environ,
            {"GOOGLE_CLIENT_ID": "client", "GOOGLE_CLIENT_SECRET": "secret"},
            clear=False,
        ), patch.object(
            oauth_token_store,
            "urlopen",
            side_effect=http_error,
        ):
            payload, error = oauth_token_store._exchange_google_refresh_token(
                refresh_token="refresh-token",
            )
        self.assertIsNone(payload)
        self.assertEqual(error["code"], "gmail_refresh_unavailable")

    def test_callback_exchange_body_read_failure_is_temporary(self):
        class FailingBody:
            def read(self, _limit):
                raise IncompleteRead(b"partial")

            def close(self):
                return None

        http_error = HTTPError(
            oauth_callback.GOOGLE_TOKEN_ENDPOINT,
            503,
            "provider error",
            {},
            FailingBody(),
        )
        with patch.object(
            oauth_callback,
            "urlopen",
            side_effect=http_error,
        ):
            payload, error = oauth_callback._exchange_google_code(
                code="authorization-code",
                code_verifier="code-verifier",
                client_id="client",
                client_secret="secret",
                redirect_uri="https://app.cuevion.com/api/inboxes/oauth-callback",
            )
        self.assertIsNone(payload)
        self.assertEqual(error["code"], "token_exchange_unavailable")

    def test_public_route_mapping_only_requires_reconnect_for_definitive_failures(self):
        definitive = authenticated_gmail._token_failure(
            {"code": "gmail_refresh_invalid_grant"}
        )
        missing = authenticated_gmail._token_failure()
        store_outage = authenticated_gmail._token_failure(
            {"code": "gmail_token_store_unavailable"}
        )
        provider_outage = authenticated_gmail._token_failure(
            {"code": "gmail_refresh_unavailable"}
        )
        rate_limited = authenticated_gmail._token_failure(
            {"code": "gmail_refresh_rate_limited"}
        )
        self.assertEqual(definitive["error"]["error"]["code"], "reconnect_required")
        self.assertEqual(missing["error"]["error"]["code"], "reconnect_required")
        self.assertEqual(store_outage["status_code"], 503)
        self.assertEqual(store_outage["error"]["error"]["code"], "gmail_token_store_unavailable")
        self.assertEqual(provider_outage["status_code"], 502)
        self.assertEqual(provider_outage["error"]["error"]["code"], "gmail_refresh_unavailable")
        self.assertEqual(rate_limited["status_code"], 429)


class ExactTargetRegistrationTests(unittest.TestCase):
    def managed_config(self, *, duplicate_email: bool = False) -> dict:
        target = {
            "id": MAILBOX_ID,
            "onboardingInboxId": "main",
            "title": "Artist inbox",
            "email": MAILBOX_EMAIL,
            "provider": "google",
            "oauthOwnerEmail": OWNER_EMAIL,
            "connected": False,
            "connectionMethod": "oauth",
            "connectionType": "oauth",
            "connectionStatus": "connection_failed",
            "connectionMessage": "Reconnect mailbox to continue syncing.",
            "oauthAuthorizationUrl": None,
            "oauthReconnectGeneration": NEW_GENERATION,
            "customImap": {"host": "", "port": "", "ssl": True, "username": "", "password": ""},
            "customSmtp": {"host": "", "port": "", "security": "starttls", "username": "", "password": "", "useSameCredentials": True},
            "preservedSetting": {"nested": [1, 2, 3]},
        }
        managed = [target]
        if duplicate_email:
            managed.append(
                {
                    "id": "legacy-duplicate",
                    "email": MAILBOX_EMAIL,
                    "provider": "custom_imap",
                }
            )
        return {
            "schemaVersion": 1,
            "email": OWNER_EMAIL,
            "managedInboxes": managed,
            "updatedAt": "2026-01-01T00:00:00Z",
        }

    def test_reconnect_preserves_exact_id_and_all_unrelated_settings(self):
        member = SimpleNamespace(email=OWNER_EMAIL)
        original = self.managed_config()
        captured: dict[str, dict] = {}

        def write_config(_store, _owner, expected, replacement):
            self.assertEqual(expected, original)
            captured["record"] = deepcopy(replacement)
            return {"status": "ok", "record": deepcopy(replacement)}

        def read_config(_store, _owner):
            record = captured.get("record", original)
            return {"status": "ok", "config": deepcopy(record)}

        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value={"backend": "vercel_kv_rest"},
        ), patch.object(
            oauth_callback.user_config_store,
            "read_user_config_record",
            side_effect=read_config,
        ), patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record_if_unchanged",
            side_effect=write_config,
        ):
            saved, error = oauth_callback._register_gmail_reconnect_in_user_config(
                member,
                mailbox_id=MAILBOX_ID,
                expected_email=MAILBOX_EMAIL,
                verified_email=MAILBOX_EMAIL,
                owner_email=OWNER_EMAIL,
                message="Google account connected.",
                credential_generation=NEW_GENERATION,
            )
        self.assertIsNone(error)
        self.assertEqual(saved["id"], MAILBOX_ID)
        self.assertEqual(saved["onboardingInboxId"], "main")
        self.assertEqual(saved["title"], "Artist inbox")
        self.assertEqual(saved["customImap"], original["managedInboxes"][0]["customImap"])
        self.assertEqual(saved["customSmtp"], original["managedInboxes"][0]["customSmtp"])
        self.assertEqual(saved["preservedSetting"], {"nested": [1, 2, 3]})
        self.assertNotIn("oauthReconnectGeneration", saved)
        self.assertTrue(saved["connected"])

    def test_reconnect_rejects_transformed_store_write(self):
        member = SimpleNamespace(email=OWNER_EMAIL)
        original = self.managed_config()

        def transformed_write(_store, _owner, _expected, replacement):
            transformed = deepcopy(replacement)
            transformed["managedInboxes"][0].pop("preservedSetting")
            return {"status": "ok", "record": transformed}

        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value={"backend": "vercel_kv_rest"},
        ), patch.object(
            oauth_callback.user_config_store,
            "read_user_config_record",
            return_value={"status": "ok", "config": original},
        ), patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record_if_unchanged",
            side_effect=transformed_write,
        ):
            saved, error = oauth_callback._register_gmail_reconnect_in_user_config(
                member,
                mailbox_id=MAILBOX_ID,
                expected_email=MAILBOX_EMAIL,
                verified_email=MAILBOX_EMAIL,
                owner_email=OWNER_EMAIL,
                message="Google account connected.",
                credential_generation=NEW_GENERATION,
            )
        self.assertIsNone(saved)
        self.assertEqual(error["_gmail_callback_failure_code"], "user_config_write_failed")

    def test_duplicate_normalized_email_and_wrong_account_fail_before_write(self):
        member = SimpleNamespace(email=OWNER_EMAIL)
        duplicate = self.managed_config(duplicate_email=True)
        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value={"backend": "vercel_kv_rest"},
        ), patch.object(
            oauth_callback.user_config_store,
            "read_user_config_record",
            return_value={"status": "ok", "config": duplicate},
        ):
            prepared, error = oauth_callback._prepare_gmail_reconnect_target(
                member,
                mailbox_id=MAILBOX_ID,
                expected_email=MAILBOX_EMAIL,
                credential_generation=NEW_GENERATION,
            )
        self.assertIsNone(prepared)
        self.assertEqual(error["_gmail_callback_failure_code"], "oauth_reconnect_stale")

        with patch.object(
            oauth_callback.user_config_store,
            "read_user_config_record",
        ) as read_config:
            saved, error = oauth_callback._register_gmail_reconnect_in_user_config(
                member,
                mailbox_id=MAILBOX_ID,
                expected_email=MAILBOX_EMAIL,
                verified_email="different@example.com",
                owner_email=OWNER_EMAIL,
                message="Google account connected.",
                credential_generation=NEW_GENERATION,
            )
        self.assertIsNone(saved)
        self.assertEqual(error["_gmail_callback_failure_code"], "oauth_reconnect_email_mismatch")
        self.assertEqual(
            error["message"],
            f"Please reconnect using the Google account for {MAILBOX_EMAIL}.",
        )
        read_config.assert_not_called()

    def test_account_mismatch_bridge_is_safe_visible_and_token_free(self):
        payload = oauth_callback._build_callback_payload(
            provider="google",
            email=MAILBOX_EMAIL,
            connection_status="connection_failed",
            message=f"Please reconnect using the Google account for {MAILBOX_EMAIL}.",
            connected=False,
            mailbox_id=MAILBOX_ID,
            mode="reconnect",
        )
        self.assertEqual(payload["status"], "error")
        self.assertEqual(
            set(payload),
            {"status", "provider", "message", "mode", "email", "mailboxId"},
        )
        page = oauth_callback._render_callback_bridge_page(
            "https://app.cuevion.com/",
            payload,
        ).decode("utf-8")
        self.assertIn(f"Please reconnect using the Google account for {MAILBOX_EMAIL}.", page)
        self.assertIn("statusNode.textContent", page)
        self.assertNotIn("innerHTML", page)
        self.assertIn('payload.status === "error" ? 1500 : 0', page)
        for secret_name in ("access_token", "refresh_token", "code_verifier"):
            self.assertNotIn(secret_name, json.dumps(payload))


if __name__ == "__main__":
    unittest.main(verbosity=2)
