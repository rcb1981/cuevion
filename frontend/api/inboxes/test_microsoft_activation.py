"""Two-phase Microsoft OAuth activation tests; all storage and token calls mocked."""
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from api.auth.runtime import AuthenticatedMemberContext
from api.inboxes import microsoft_activation as activation
from api.inboxes.microsoft_token_authority import MicrosoftTokenError

GEN = "g" * 43
OWNER = "owner@example.com"
EMAIL = "artist@example.com"
ACCOUNT = "e23090af-0bcd-4768-a08c-93b9c3e55190"
ACCESS = "microsoft-access-token-123456"
REFRESH = "microsoft-refresh-token-123456"
SCOPES = "https://graph.microsoft.com/User.Read https://graph.microsoft.com/Mail.Read offline_access"


def member(*, role="owner", email=OWNER, user_id="auth0|owner", workspace_id="ws-test"):
    return AuthenticatedMemberContext(
        user_id=user_id, email=email, name="Owner",
        workspace_id=workspace_id, membership_role=role,
    )


def identity(*, email=EMAIL, account_id=ACCOUNT):
    return {"email": email, "id": account_id, "display_name": "Artist"}


def token():
    return {
        "access_token": ACCESS,
        "refresh_token": REFRESH,
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": SCOPES,
    }


class FakeUserConfigStore:
    def __init__(self, current=None):
        self.current = copy.deepcopy(current)
        self.writes = []
        self.fail_write_at = None
        self.transform_read = False
        self.conflict_once = False

    def read(self, _store, owner):
        if self.current is None:
            return {"status": "missing", "config": None}
        result = copy.deepcopy(self.current)
        if self.transform_read:
            result["unexpected"] = "different"
        return {"status": "ok", "config": result}

    def write_missing(self, _store, owner, record):
        if self.current is not None:
            return {"status": "conflict", "record": None}
        return self._write(record)

    def write_if_same(self, _store, owner, previous, record):
        if self.conflict_once:
            self.conflict_once = False
            self.current["uiPreferences"]["concurrent"] = True
            return {"status": "conflict", "record": None}
        if not activation._exact(self.current, previous):
            return {"status": "conflict", "record": None}
        return self._write(record)

    def _write(self, record):
        self.writes.append(copy.deepcopy(record))
        if self.fail_write_at == len(self.writes):
            return {"status": "unavailable", "record": None}
        self.current = copy.deepcopy(record)
        return {"status": "ok", "record": copy.deepcopy(record)}


class FakeTokenStore:
    def __init__(self):
        self.record = None
        self.persist_error = None
        self.load_error = None
        self.calls = []
        self.callbacks = []

    def generation(self, authority):
        self.calls.append(("inspect", authority))
        if self.load_error:
            raise MicrosoftTokenError(self.load_error)
        if self.record is None:
            return None
        for key, value in (
            ("owner_email", authority.owner_email),
            ("mailbox_id", authority.mailbox_id),
            ("email", authority.email),
            ("account_id", authority.account_id),
        ):
            if self.record.get(key) != value:
                raise MicrosoftTokenError("microsoft_reconnect_required")
        return self.record["credential_generation"]

    def persist(self, authority, payload, *, mode, previous_generation=None):
        self.calls.append(("persist", authority, mode))
        if self.persist_error:
            raise MicrosoftTokenError(self.persist_error)
        if mode == "connect" and self.record is not None:
            raise MicrosoftTokenError("microsoft_token_write_conflict")
        if mode == "reconnect" and (
            self.record is None
            or self.record.get("credential_generation") != previous_generation
            or not isinstance(payload.get("refresh_token"), str)
            or not payload["refresh_token"]
        ):
            raise MicrosoftTokenError("microsoft_token_write_conflict")
        self.record = {
            "provider": "microsoft",
            "owner_email": authority.owner_email,
            "mailbox_id": authority.mailbox_id,
            "email": authority.email,
            "account_id": authority.account_id,
            "credential_generation": authority.credential_generation,
            "access_token": payload["access_token"],
        }
        for callback in self.callbacks:
            callback()
        return copy.deepcopy(self.record)

    def load(self, authority):
        self.calls.append(("load", authority))
        if self.load_error:
            raise MicrosoftTokenError(self.load_error)
        if self.record is None:
            raise MicrosoftTokenError("microsoft_reconnect_required")
        if (
            self.record["owner_email"] != authority.owner_email
            or self.record["mailbox_id"] != authority.mailbox_id
            or self.record["email"] != authority.email
            or self.record["account_id"] != authority.account_id
            or self.record["credential_generation"] != authority.credential_generation
        ):
            raise MicrosoftTokenError("microsoft_reconnect_required")
        return copy.deepcopy(self.record)


class MicrosoftActivationTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeUserConfigStore()
        self.tokens = FakeTokenStore()
        patches = [
            patch.object(activation.user_config_store, "resolve_user_config_store", return_value=({"backend": "test"}, None)),
            patch.object(activation.user_config_store, "read_user_config_record", side_effect=self.store.read),
            patch.object(activation.user_config_store, "write_user_config_record_if_missing", side_effect=self.store.write_missing),
            patch.object(activation.user_config_store, "write_user_config_record_if_unchanged", side_effect=self.store.write_if_same),
            patch.object(activation.token_authority, "persist_owned_microsoft_token", side_effect=self.tokens.persist),
            patch.object(activation.token_authority, "load_owned_microsoft_token", side_effect=self.tokens.load),
            patch.object(activation.token_authority, "read_owned_microsoft_token_generation", side_effect=self.tokens.generation),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def activate(
        self, current_member=None, verified_identity=None, generation=GEN,
        token_payload=None, issued_at=None, nonce=None,
    ):
        if issued_at is None:
            issued_at = 1000 if generation == GEN else 2000
        if nonce is None:
            nonce = "initial-StateNonce-001" if generation == GEN else "initial-StateNonce-002"
        return activation.activate_initial_microsoft_mailbox(
            current_member or member(), verified_identity or identity(),
            generation, token_payload if token_payload is not None else token(),
            authorization_issued_at=issued_at,
            authorization_nonce=nonce,
        )

    def test_success_requires_disconnected_stage_before_any_token_write(self):
        snapshots = []
        self.tokens.callbacks.append(lambda: snapshots.append(copy.deepcopy(self.store.current)))
        connected = self.activate()
        self.assertTrue(connected["connected"])
        self.assertEqual(connected["connectionStatus"], "connected")
        self.assertEqual(connected["oauthOwnerUserId"], "auth0|owner")
        self.assertEqual(connected["oauthWorkspaceId"], "ws-test")
        self.assertEqual(connected["oauthAccountId"], ACCOUNT)
        self.assertEqual(connected["oauthCredentialGeneration"], GEN)
        self.assertEqual(len(self.store.writes), 2)
        self.assertFalse(snapshots[0]["managedInboxes"][0]["connected"])
        self.assertEqual(snapshots[0]["managedInboxes"][0]["connectionStatus"], "authenticated_pending_activation")
        self.assertTrue(self.store.current["managedInboxes"][0]["connected"])
        self.assertNotIn(ACCESS, str(self.store.current))
        self.assertNotIn(REFRESH, str(self.store.current))

    def test_existing_settings_and_other_inboxes_are_unchanged(self):
        prior = {
            "email": OWNER,
            "managedInboxes": [{
                "id": "gmail-other", "email": "other@example.com",
                "provider": "google", "connected": True,
            }],
            "uiPreferences": {"theme": "dark"},
            "customFlags": {"myOwn": 123},
        }
        self.store.current = copy.deepcopy(prior)
        result = self.activate()
        self.assertEqual(len(self.store.current["managedInboxes"]), 2)
        self.assertEqual(self.store.current["managedInboxes"][0], prior["managedInboxes"][0])
        self.assertEqual(self.store.current["uiPreferences"], prior["uiPreferences"])
        self.assertEqual(self.store.current["customFlags"], prior["customFlags"])
        self.assertEqual(result["provider"], "microsoft")

    def test_only_owner_role_may_stage(self):
        for role in ("member", "admin"):
            with self.subTest(role=role), self.assertRaises(activation.MicrosoftActivationError) as caught:
                self.activate(member(role=role))
            self.assertEqual(caught.exception.code, "microsoft_owner_required")
        self.assertFalse(self.store.writes)
        self.assertFalse(self.tokens.calls)

    def test_malformed_identity_and_generation_fail_before_store(self):
        for changes in (identity(email="INVALID"), identity(account_id="bad\nid"), identity(email="ARTIST@example.com")):
            with self.subTest(identity=changes), self.assertRaises(activation.MicrosoftActivationError):
                self.activate(verified_identity=changes)
        with self.assertRaises(activation.MicrosoftActivationError):
            self.activate(generation="invalid")
        self.assertEqual(self.store.writes, [])

    def test_invalid_payload_does_not_create_pending_mailbox(self):
        p = token()
        p["scope"] = "User.Read"
        with self.assertRaises(activation.MicrosoftActivationError):
            self.activate(token_payload=p)
        self.assertFalse(self.store.writes)

    def test_same_email_existing_other_provider_fails(self):
        self.store.current = {"email": OWNER, "managedInboxes": [
            {"id": "gmail-other", "email": EMAIL, "provider": "google"}
        ]}
        with self.assertRaises(activation.MicrosoftActivationError) as caught:
            self.activate()
        self.assertEqual(caught.exception.code, "microsoft_mailbox_conflict")
        self.assertFalse(self.tokens.calls)

    def test_missing_durable_config_fails_without_staging(self):
        with patch.object(activation.user_config_store, "resolve_user_config_store", return_value=(None, {"code": "unavailable"})):
            with self.assertRaises(activation.MicrosoftActivationError) as caught:
                self.activate()
        self.assertEqual(caught.exception.code, "microsoft_config_unavailable")
        self.assertFalse(self.store.writes)

    def test_owner_mismatch_in_existing_config_fails(self):
        self.store.current = {"email": "other@example.com", "managedInboxes": []}
        with self.assertRaises(activation.MicrosoftActivationError):
            self.activate()
        self.assertFalse(self.store.writes)

    def test_stored_config_without_exact_owner_is_rejected(self):
        self.store.current = {"managedInboxes": []}
        with self.assertRaises(activation.MicrosoftActivationError) as caught:
            self.activate()
        self.assertEqual(caught.exception.code, "microsoft_owner_mismatch")
        self.assertFalse(self.tokens.calls)

    def test_same_graph_account_under_second_alias_is_rejected(self):
        self.store.current = {"email": OWNER, "managedInboxes": [{
            "id": "another-microsoft",
            "email": "alias@example.com",
            "provider": "microsoft",
            "oauthAccountId": ACCOUNT,
        }]}
        with self.assertRaises(activation.MicrosoftActivationError) as caught:
            self.activate()
        self.assertEqual(caught.exception.code, "microsoft_mailbox_conflict")
        self.assertFalse(self.tokens.calls)

    def test_unexpected_config_backend_exception_is_sanitized(self):
        with patch.object(
            activation.user_config_store, "resolve_user_config_store",
            side_effect=RuntimeError("private storage connection details"),
        ):
            with self.assertRaises(activation.MicrosoftActivationError) as caught:
                self.activate()
        self.assertEqual(caught.exception.code, "microsoft_config_unavailable")
        self.assertNotIn("private", str(caught.exception))
        self.assertFalse(self.tokens.calls)

    def test_unexpected_token_backend_exception_leaves_mailbox_pending(self):
        with patch.object(
            activation.token_authority, "persist_owned_microsoft_token",
            side_effect=RuntimeError("private refresh credential"),
        ):
            with self.assertRaises(activation.MicrosoftActivationError) as caught:
                self.activate()
        self.assertEqual(caught.exception.code, "microsoft_token_store_unavailable")
        self.assertNotIn("private", str(caught.exception))
        self.assertFalse(self.store.current["managedInboxes"][0]["connected"])

    def test_first_stage_write_failure_does_not_write_token(self):
        self.store.fail_write_at = 1
        with self.assertRaises(activation.MicrosoftActivationError):
            self.activate()
        self.assertIsNone(self.tokens.record)

    def test_stage_readback_mismatch_fails_before_token_write(self):
        self.store.transform_read = True
        with self.assertRaises(activation.MicrosoftActivationError):
            self.activate()
        self.assertIsNone(self.tokens.record)
        self.assertFalse(self.store.current["managedInboxes"][0]["connected"])

    def test_token_persist_failure_leaves_pending_not_connected(self):
        self.tokens.persist_error = "microsoft_token_store_unavailable"
        with self.assertRaises(activation.MicrosoftActivationError) as caught:
            self.activate()
        self.assertEqual(caught.exception.code, "microsoft_token_store_unavailable")
        self.assertEqual(self.store.current["managedInboxes"][0]["connected"], False)
        self.assertEqual(len(self.store.writes), 1)

    def test_token_readback_failure_prevents_activation(self):
        self.tokens.load_error = "microsoft_token_store_unavailable"
        with self.assertRaises(activation.MicrosoftActivationError):
            self.activate()
        self.assertFalse(self.store.current["managedInboxes"][0]["connected"])

    def test_activate_write_failure_keeps_pending(self):
        self.store.fail_write_at = 2
        with self.assertRaises(activation.MicrosoftActivationError):
            self.activate()
        self.assertFalse(self.store.current["managedInboxes"][0]["connected"])
        self.assertIsNotNone(self.tokens.record)

    def test_config_conflict_retry_preserves_unrelated_concurrent_mutation(self):
        self.store.current = {"email": OWNER, "managedInboxes": [], "uiPreferences": {}}
        self.store.conflict_once = True
        result = self.activate()
        self.assertTrue(result["connected"])
        self.assertTrue(self.store.current["uiPreferences"]["concurrent"])

    def test_callback_resume_after_successful_token_store_but_failed_activation(self):
        self.store.fail_write_at = 2
        with self.assertRaises(activation.MicrosoftActivationError):
            self.activate()
        self.store.fail_write_at = None
        final = self.activate()
        self.assertTrue(final["connected"])
        self.assertEqual(len([x for x in self.tokens.calls if x[0] == "persist"]), 1)

    def test_pending_new_authorization_recovers_after_token_persist(self):
        self.store.fail_write_at = 2
        with self.assertRaises(activation.MicrosoftActivationError):
            self.activate()
        self.assertFalse(self.store.current["managedInboxes"][0]["connected"])
        self.assertEqual(self.tokens.record["credential_generation"], GEN)
        self.store.fail_write_at = None
        final = self.activate(generation="z"*43)
        self.assertTrue(final["connected"])
        self.assertEqual(final["oauthCredentialGeneration"], "z"*43)
        self.assertEqual(self.tokens.record["credential_generation"], "z"*43)
        self.assertEqual(
            [call[2] for call in self.tokens.calls if call[0] == "persist"],
            ["connect", "reconnect"],
        )

    def test_pending_new_authorization_recovers_when_token_was_never_saved(self):
        self.tokens.persist_error = "microsoft_token_store_unavailable"
        with self.assertRaises(activation.MicrosoftActivationError):
            self.activate()
        self.assertIsNone(self.tokens.record)
        self.assertFalse(self.store.current["managedInboxes"][0]["connected"])
        self.tokens.persist_error = None
        final = self.activate(generation="z"*43)
        self.assertTrue(final["connected"])
        self.assertEqual(self.tokens.record["credential_generation"], "z"*43)
        self.assertEqual(
            [call[2] for call in self.tokens.calls if call[0] == "persist"][-1],
            "connect",
        )

    def test_older_initial_callback_cannot_supersede_newer_pending_state(self):
        self.tokens.persist_error = "microsoft_token_store_unavailable"
        with self.assertRaises(activation.MicrosoftActivationError):
            self.activate(generation=GEN, issued_at=1000, nonce="initial-StateNonce-001")
        with self.assertRaises(activation.MicrosoftActivationError):
            self.activate(generation="z"*43, issued_at=2000, nonce="initial-StateNonce-002")
        pending = copy.deepcopy(self.store.current["managedInboxes"][0])
        self.assertEqual(pending["oauthCredentialGeneration"], "z"*43)
        with self.assertRaises(activation.MicrosoftActivationError) as caught:
            self.activate(generation=GEN, issued_at=1000, nonce="initial-StateNonce-001")
        self.assertEqual(caught.exception.code, "microsoft_authorization_stale")
        self.assertEqual(self.store.current["managedInboxes"][0], pending)
        self.assertIsNone(self.tokens.record)

    def test_same_second_new_initial_generation_is_rejected(self):
        self.tokens.persist_error = "microsoft_token_store_unavailable"
        with self.assertRaises(activation.MicrosoftActivationError):
            self.activate(generation=GEN, issued_at=1000, nonce="initial-StateNonce-001")
        with self.assertRaises(activation.MicrosoftActivationError) as caught:
            self.activate(generation="z"*43, issued_at=1000, nonce="initial-StateNonce-002")
        self.assertEqual(caught.exception.code, "microsoft_authorization_stale")
        self.assertEqual(self.store.current["managedInboxes"][0]["oauthCredentialGeneration"], GEN)

    def test_new_generation_cannot_recover_pending_with_missing_signed_state(self):
        self.tokens.persist_error = "microsoft_token_store_unavailable"
        with self.assertRaises(activation.MicrosoftActivationError):
            self.activate()
        original = copy.deepcopy(self.store.current["managedInboxes"][0])
        self.store.current["managedInboxes"][0].pop("oauthAuthorizationIssuedAt")
        with self.assertRaises(activation.MicrosoftActivationError) as caught:
            self.activate(generation="z"*43, issued_at=2000)
        self.assertEqual(caught.exception.code, "microsoft_authorization_stale")
        self.assertEqual(self.store.current["managedInboxes"][0]["oauthCredentialGeneration"], GEN)

    def test_invalid_initial_callback_signed_state_is_rejected_before_storage(self):
        for stamp, nonce in (
            (True, "initial-StateNonce-001"),
            (-1, "initial-StateNonce-001"),
            (1000, ""),
            (1000, "invalid nonce"),
        ):
            with self.subTest(stamp=stamp, nonce=nonce), self.assertRaises(
                activation.MicrosoftActivationError
            ) as caught:
                self.activate(issued_at=stamp, nonce=nonce)
            self.assertEqual(caught.exception.code, "microsoft_authorization_invalid")
        self.assertFalse(self.store.writes)

    def test_stale_initial_callback_cannot_revert_newer_authorization(self):
        self.tokens.persist_error = "microsoft_token_store_unavailable"
        with self.assertRaises(activation.MicrosoftActivationError):
            self.activate()
        self.tokens.persist_error = None
        self.activate(generation="z"*43)
        with self.assertRaises(activation.MicrosoftActivationError):
            self.activate(generation=GEN)
        self.assertEqual(self.tokens.record["credential_generation"], "z"*43)

    def test_pending_mismatched_account_cannot_activate(self):
        self.tokens.persist_error = "microsoft_token_store_unavailable"
        with self.assertRaises(activation.MicrosoftActivationError):
            self.activate()
        self.tokens.persist_error = None
        with self.assertRaises(activation.MicrosoftActivationError):
            self.activate(verified_identity=identity(account_id="different-account"))
        self.assertIsNone(self.tokens.record)

    def test_activated_mailbox_is_accepted_by_operational_authority(self):
        from api.inboxes import microsoft_mailbox_access as access
        from api.inboxes import microsoft_graph

        connected = self.activate()
        current = copy.deepcopy(self.store.current)
        resolved = {
            "status": "ok",
            "user": {"email": OWNER},
            "memberAuthority": member(),
            "config": current,
            "inbox": copy.deepcopy(connected),
        }
        with patch.object(
            access, "resolve_owned_managed_inbox_record",
            return_value=resolved,
        ) as owned, patch.object(
            access, "ensure_owned_microsoft_access_token",
            return_value=self.tokens.record,
        ) as credentials, patch.object(
            microsoft_graph, "list_microsoft_inbox",
            return_value={"messages": [], "hasMore": False},
        ) as graph:
            result = access.list_owned_microsoft_inbox(
                {"Cookie": "authoritative-test"}, connected["id"], page_size=4,
            )
        self.assertEqual(result, {"messages": [], "hasMore": False})
        owned.assert_called_once()
        credentials.assert_called_once()
        graph.assert_called_once_with(ACCESS, page_size=4)

    def test_unfinished_activation_is_denied_by_operational_authority(self):
        from api.inboxes import microsoft_mailbox_access as access
        from api.inboxes import microsoft_graph

        self.tokens.persist_error = "microsoft_token_store_unavailable"
        with self.assertRaises(activation.MicrosoftActivationError):
            self.activate()
        pending = copy.deepcopy(self.store.current["managedInboxes"][0])
        resolved = {
            "status": "ok",
            "user": {"email": OWNER},
            "memberAuthority": member(),
            "config": copy.deepcopy(self.store.current),
            "inbox": pending,
        }
        with patch.object(access, "resolve_owned_managed_inbox_record", return_value=resolved), \
             patch.object(access, "ensure_owned_microsoft_access_token") as credentials, \
             patch.object(microsoft_graph, "list_microsoft_inbox") as graph:
            with self.assertRaises(access.MicrosoftMailboxAccessError):
                access.list_owned_microsoft_inbox({}, pending["id"])
        credentials.assert_not_called()
        graph.assert_not_called()

    def reserve_reconnect(self, generation="r"*43, *, current_member=None, asserted_email=EMAIL, mailbox_id=None):
        from api.inboxes import microsoft_mailbox_access as access
        current = copy.deepcopy(self.store.current)
        current_member = current_member or member()
        current_id = mailbox_id or activation._mailbox_id(OWNER, EMAIL)
        matching = [
            box for box in current["managedInboxes"]
            if box.get("id") == current_id
        ]
        owned_result = {
            "status": "ok",
            "user": {"email": OWNER},
            "memberAuthority": current_member,
            "config": current,
            "inbox": copy.deepcopy(matching[0]) if matching else None,
        }
        with patch.object(
            activation.user_config_store,
            "resolve_owned_managed_inbox_record",
            return_value=owned_result,
        ):
            return activation.reserve_authoritative_microsoft_reconnect(
                {"Cookie": "test-session"}, current_member, current_id,
                asserted_email, generation,
            )

    def finish_reconnect(self, generation="r"*43, *, verified_identity=None, current_member=None):
        return activation.activate_reconnected_microsoft_mailbox(
            current_member or member(),
            verified_identity or identity(),
            generation,
            token(),
            mailbox_id=activation._mailbox_id(OWNER, EMAIL),
            expected_email=EMAIL,
        )

    def test_reconnect_reserves_disconnected_and_rotates_new_generation(self):
        first = self.activate()
        self.assertTrue(first["connected"])
        self.assertEqual(self.reserve_reconnect(), EMAIL)
        pending = self.store.current["managedInboxes"][0]
        self.assertFalse(pending["connected"])
        self.assertEqual(pending["connectionStatus"], activation.PENDING_STATUS)
        self.assertEqual(pending["oauthReconnectGeneration"], "r"*43)
        self.assertEqual(pending["oauthCredentialGeneration"], "r"*43)
        self.assertEqual(self.tokens.record["credential_generation"], GEN)

        next_mailbox = self.finish_reconnect()
        self.assertTrue(next_mailbox["connected"])
        self.assertEqual(next_mailbox["oauthCredentialGeneration"], "r"*43)
        self.assertNotIn("oauthReconnectGeneration", next_mailbox)
        self.assertNotIn("oauthAuthorizationIssuedAt", next_mailbox)
        self.assertNotIn("oauthAuthorizationNonce", next_mailbox)
        self.assertEqual(self.tokens.record["credential_generation"], "r"*43)
        self.assertEqual(
            [call[2] for call in self.tokens.calls if call[0] == "persist"],
            ["connect", "reconnect"],
        )

    def test_reconnect_preserves_other_mailbox_and_ui_preferences(self):
        other = {"id": "gmail-stays", "email": "other@example.com", "provider": "google"}
        self.store.current = {
            "email": OWNER,
            "managedInboxes": [copy.deepcopy(other)],
            "uiPreferences": {"theme": "dark"},
            "customFlags": [1, 2, 3],
        }
        self.activate()
        self.reserve_reconnect()
        self.finish_reconnect()
        result = self.store.current
        self.assertEqual(result["managedInboxes"][0], other)
        self.assertEqual(result["uiPreferences"], {"theme": "dark"})
        self.assertEqual(result["customFlags"], [1, 2, 3])

    def test_reconnect_blocks_graph_access_while_pending(self):
        from api.inboxes import microsoft_mailbox_access as access
        from api.inboxes import microsoft_graph
        self.activate()
        self.reserve_reconnect()
        pending = copy.deepcopy(self.store.current["managedInboxes"][0])
        owned_result = {
            "status": "ok", "user": {"email": OWNER},
            "memberAuthority": member(),
            "config": copy.deepcopy(self.store.current),
            "inbox": pending,
        }
        with patch.object(access, "resolve_owned_managed_inbox_record", return_value=owned_result), \
             patch.object(access, "ensure_owned_microsoft_access_token") as credentials, \
             patch.object(microsoft_graph, "list_microsoft_inbox") as graph:
            with self.assertRaises(access.MicrosoftMailboxAccessError):
                access.list_owned_microsoft_inbox({}, pending["id"])
        credentials.assert_not_called()
        graph.assert_not_called()

    def test_reconnect_rejects_reused_credential_generation(self):
        self.activate()
        with self.assertRaises(activation.MicrosoftActivationError) as caught:
            self.reserve_reconnect(generation=GEN)
        self.assertEqual(caught.exception.code, "microsoft_generation_conflict")
        self.assertTrue(self.store.current["managedInboxes"][0]["connected"])
        self.assertEqual(self.tokens.record["credential_generation"], GEN)

    def test_reconnect_cannot_reuse_its_pending_generation(self):
        self.activate()
        self.reserve_reconnect(generation="r"*43)
        with self.assertRaises(activation.MicrosoftActivationError) as caught:
            self.reserve_reconnect(generation="r"*43)
        self.assertEqual(caught.exception.code, "microsoft_generation_conflict")
        self.assertFalse(self.store.current["managedInboxes"][0]["connected"])
        self.assertEqual(self.tokens.record["credential_generation"], GEN)

    def test_mismatched_browser_email_rejected_before_reconnect_reservation(self):
        self.activate()
        with self.assertRaises(activation.MicrosoftActivationError) as caught:
            self.reserve_reconnect(asserted_email="someoneelse@example.com")
        self.assertEqual(caught.exception.code, "microsoft_mailbox_conflict")
        self.assertTrue(self.store.current["managedInboxes"][0]["connected"])

    def test_mismatched_member_rejected_before_reconnect_reservation(self):
        self.activate()
        for wrong_member in (
            member(role="admin"),
            member(user_id="auth0|other"),
            member(workspace_id="wrong-workspace"),
            member(email="other@example.com"),
        ):
            with self.subTest(role=wrong_member.membership_role,
                              user_id=wrong_member.user_id), self.assertRaises(
                activation.MicrosoftActivationError
            ):
                self.reserve_reconnect(current_member=wrong_member)
            self.assertTrue(self.store.current["managedInboxes"][0]["connected"])

    def test_wrong_graph_account_rejected_before_token_rotation(self):
        self.activate()
        self.reserve_reconnect()
        with self.assertRaises(activation.MicrosoftActivationError):
            self.finish_reconnect(verified_identity=identity(account_id="different-graph-account"))
        self.assertEqual(self.tokens.record["credential_generation"], GEN)
        self.assertFalse(self.store.current["managedInboxes"][0]["connected"])

    def test_reconnect_reservation_with_unknown_mailbox_fails(self):
        self.activate()
        with self.assertRaises(activation.MicrosoftActivationError):
            self.reserve_reconnect(mailbox_id="microsoft-not-the-owned-mailbox")
        self.assertEqual(self.tokens.record["credential_generation"], GEN)
        self.assertTrue(self.store.current["managedInboxes"][0]["connected"])

    def test_old_callback_cannot_activate_after_newer_reservation(self):
        self.activate()
        self.reserve_reconnect(generation="r"*43)
        self.reserve_reconnect(generation="s"*43)
        with self.assertRaises(activation.MicrosoftActivationError):
            self.finish_reconnect(generation="r"*43)
        self.assertEqual(self.tokens.record["credential_generation"], GEN)
        next_mailbox = self.finish_reconnect(generation="s"*43)
        self.assertTrue(next_mailbox["connected"])
        self.assertEqual(self.tokens.record["credential_generation"], "s"*43)

    def test_reconnect_token_failure_remains_pending_and_next_attempt_recovers(self):
        self.activate()
        self.reserve_reconnect(generation="r"*43)
        self.tokens.persist_error = "microsoft_token_store_unavailable"
        with self.assertRaises(activation.MicrosoftActivationError):
            self.finish_reconnect(generation="r"*43)
        self.assertFalse(self.store.current["managedInboxes"][0]["connected"])
        self.assertEqual(self.tokens.record["credential_generation"], GEN)
        self.tokens.persist_error = None
        self.reserve_reconnect(generation="s"*43)
        updated = self.finish_reconnect(generation="s"*43)
        self.assertTrue(updated["connected"])
        self.assertEqual(self.tokens.record["credential_generation"], "s"*43)

    def test_reconnect_activation_write_failure_next_authorization_recovers(self):
        self.activate()
        self.reserve_reconnect(generation="r"*43)
        self.store.fail_write_at = len(self.store.writes) + 1
        with self.assertRaises(activation.MicrosoftActivationError):
            self.finish_reconnect(generation="r"*43)
        self.assertFalse(self.store.current["managedInboxes"][0]["connected"])
        self.assertEqual(self.tokens.record["credential_generation"], "r"*43)
        self.store.fail_write_at = None
        self.reserve_reconnect(generation="s"*43)
        updated = self.finish_reconnect(generation="s"*43)
        self.assertTrue(updated["connected"])
        self.assertEqual(self.tokens.record["credential_generation"], "s"*43)

    def test_reconnect_exact_replay_after_success_fails_closed(self):
        self.activate()
        self.reserve_reconnect()
        self.finish_reconnect()
        with self.assertRaises(activation.MicrosoftActivationError):
            self.finish_reconnect()
        self.assertTrue(self.store.current["managedInboxes"][0]["connected"])

    def test_duplicate_casefold_mailbox_ids_fail_closed(self):
        mailbox_id = activation._mailbox_id(OWNER, EMAIL)
        self.store.current = {"email": OWNER, "managedInboxes": [
            {"id": mailbox_id, "email": "different@example.com"},
            {"id": mailbox_id.upper(), "email": "other@example.com"},
        ]}
        with self.assertRaises(activation.MicrosoftActivationError):
            self.activate()
        self.assertFalse(self.tokens.calls)


if __name__ == "__main__":
    unittest.main()
