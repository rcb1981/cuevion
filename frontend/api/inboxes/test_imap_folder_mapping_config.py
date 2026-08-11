from __future__ import annotations

import copy
import importlib
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


CURRENT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = CURRENT_DIR.parent.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))


try:
    _CRYPTOGRAPHY_AVAILABLE = importlib.util.find_spec("cryptography") is not None
except ValueError:
    _CRYPTOGRAPHY_AVAILABLE = False
if not _CRYPTOGRAPHY_AVAILABLE:
    cryptography = types.ModuleType("cryptography")
    cryptography.__path__ = []
    cryptography_exceptions = types.ModuleType("cryptography.exceptions")
    cryptography_exceptions.InvalidSignature = type("InvalidSignature", (Exception,), {})
    cryptography_exceptions.InvalidTag = type("InvalidTag", (Exception,), {})
    hazmat = types.ModuleType("cryptography.hazmat")
    hazmat.__path__ = []
    primitives = types.ModuleType("cryptography.hazmat.primitives")
    primitives.__path__ = []
    ciphers = types.ModuleType("cryptography.hazmat.primitives.ciphers")
    ciphers.__path__ = []
    aead = types.ModuleType("cryptography.hazmat.primitives.ciphers.aead")
    aead.AESGCM = type("AESGCM", (), {})
    sys.modules.update(
        {
            "cryptography": cryptography,
            "cryptography.exceptions": cryptography_exceptions,
            "cryptography.hazmat": hazmat,
            "cryptography.hazmat.primitives": primitives,
            "cryptography.hazmat.primitives.ciphers": ciphers,
            "cryptography.hazmat.primitives.ciphers.aead": aead,
        }
    )

# These tests patch the authenticated-user boundary and do not exercise Auth0.
sys.modules.setdefault("api.auth.runtime", types.ModuleType("api.auth.runtime"))


user_config_store = importlib.import_module("api.user_config_store")
authenticated_imap = importlib.import_module("api.inboxes.authenticated_imap")
user_config_api = importlib.import_module("api.user.config")


OWNER_EMAIL = "owner@example.test"
MAILBOX_ID = "server-mailbox"
CREDENTIAL_VERSION = "A" * 43
TRASH_FOLDER = 'Deleted "Items"\\2024'


def managed_inbox(*, mapping: object = ...):
    inbox = {
        "id": MAILBOX_ID,
        "email": "mailbox@example.test",
        "provider": "custom_imap",
        "connected": True,
        "connectionStatus": "connected",
        "imapConnectionStatus": "connected",
        "credentialVersion": CREDENTIAL_VERSION,
        "customImap": {
            "host": "imap.example.test",
            "port": 993,
            "ssl": True,
            "username": "imap-user",
        },
        "customSmtp": {},
    }
    if mapping is not ...:
        inbox["customImapFolderMappings"] = copy.deepcopy(mapping)
    return inbox


def config_record(*, inbox: dict | None = None):
    return {
        "v": 1,
        "email": OWNER_EMAIL,
        "updatedAt": "2026-08-10T00:00:00Z",
        "onboardingSession": {},
        "managedInboxes": [copy.deepcopy(inbox or managed_inbox())],
    }


def owned_result(*, inbox: dict | None = None):
    current = copy.deepcopy(inbox or managed_inbox())
    return {
        "status": "ok",
        "user": {"email": OWNER_EMAIL, "name": "Owner", "userType": "member"},
        "inbox": current,
        "config": config_record(inbox=current),
        "error": None,
    }


def secret_result():
    return {
        "status": "present",
        "record": {
            "credentialVersion": CREDENTIAL_VERSION,
            "imapPassword": "private-imap-password",
            "smtpPassword": None,
        },
        "error": None,
    }


class FolderMappingShapeTests(unittest.TestCase):
    def test_exact_versioned_mapping_is_valid(self):
        mapping = {"schemaVersion": 1, "trashFolder": TRASH_FOLDER}
        self.assertEqual(
            user_config_store.validate_custom_imap_folder_mappings(mapping),
            mapping,
        )
        self.assertIsNot(
            user_config_store.validate_custom_imap_folder_mappings(mapping),
            mapping,
        )

    def test_mapping_rejects_unknown_or_type_coerced_schema(self):
        invalid = (
            {},
            {"schemaVersion": True, "trashFolder": TRASH_FOLDER},
            {"schemaVersion": 2, "trashFolder": TRASH_FOLDER},
            {
                "schemaVersion": 1,
                "trashFolder": TRASH_FOLDER,
                "archiveFolder": "Archive",
            },
        )
        for mapping in invalid:
            with self.subTest(mapping=mapping):
                self.assertIsNone(
                    user_config_store.validate_custom_imap_folder_mappings(mapping)
                )

    def test_mapping_rejects_non_runtime_compatible_folder_names(self):
        invalid = (
            "",
            " INBOX",
            "Inbox",
            "Trash\rInjected",
            "Trash\x00Injected",
            "\ud800",
            "x" * (user_config_store.MAX_CUSTOM_IMAP_FOLDER_NAME_BYTES + 1),
        )
        for folder in invalid:
            with self.subTest(folder=repr(folder)):
                self.assertFalse(
                    user_config_store.is_valid_custom_imap_folder_name(folder)
                )

    def test_pure_authenticated_extractor_distinguishes_absent_and_malformed(self):
        self.assertEqual(
            authenticated_imap.configured_imap_trash_folder(None),
            (None, None),
        )
        self.assertEqual(
            authenticated_imap.configured_imap_trash_folder(
                {"schemaVersion": 1, "trashFolder": TRASH_FOLDER}
            ),
            (TRASH_FOLDER, None),
        )
        self.assertEqual(
            authenticated_imap.configured_imap_trash_folder(
                {"schemaVersion": 2, "trashFolder": TRASH_FOLDER}
            ),
            (None, "mailbox_configuration_malformed"),
        )


class AuthenticatedMappingResolutionTests(unittest.TestCase):
    def _resolve(self, inbox: dict):
        with patch.object(
            authenticated_imap,
            "resolve_owned_managed_inbox_record",
            return_value=owned_result(inbox=inbox),
        ), patch.object(
            authenticated_imap,
            "read_mailbox_secret",
            return_value=secret_result(),
        ):
            return authenticated_imap.resolve_authenticated_imap_mailbox(
                {}, MAILBOX_ID
            )

    def test_absent_mapping_resolves_to_explicit_none(self):
        result = self._resolve(managed_inbox())
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(result["mailbox"]["customImapFolderMappings"])
        self.assertEqual(
            set(result["mailbox"]["imap"]),
            {"host", "port", "ssl", "username", "password"},
        )

    def test_valid_mapping_is_returned_as_server_owned_sibling(self):
        mapping = {"schemaVersion": 1, "trashFolder": TRASH_FOLDER}
        result = self._resolve(managed_inbox(mapping=mapping))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["mailbox"]["customImapFolderMappings"], mapping)

    def test_malformed_stored_mapping_fails_before_secret_read(self):
        inbox = managed_inbox(
            mapping={"schemaVersion": 1, "trashFolder": "Inbox"}
        )
        with patch.object(
            authenticated_imap,
            "resolve_owned_managed_inbox_record",
            return_value=owned_result(inbox=inbox),
        ), patch.object(
            authenticated_imap,
            "read_mailbox_secret",
        ) as read_secret:
            result = authenticated_imap.resolve_authenticated_imap_mailbox(
                {}, MAILBOX_ID
            )
        self.assertEqual(result["status"], "malformed")
        self.assertEqual(
            result["error"]["code"], "mailbox_configuration_malformed"
        )
        read_secret.assert_not_called()


class FolderMappingPersistenceTests(unittest.TestCase):
    def test_cas_save_preserves_credentials_and_confirms_exact_readback(self):
        baseline = config_record()
        state = {"replacement": None}

        def read_record(_store, _owner):
            record = state["replacement"] or baseline
            return {"status": "ok", "config": copy.deepcopy(record), "error": None}

        def write_record(_store, _owner, expected, replacement):
            self.assertEqual(expected, baseline)
            state["replacement"] = copy.deepcopy(replacement)
            return {"status": "ok", "record": replacement, "error": None}

        with patch.object(
            user_config_store,
            "resolve_authenticated_user",
            return_value=(
                {"email": OWNER_EMAIL, "name": "Owner", "userType": "member"},
                None,
            ),
        ), patch.object(
            user_config_store,
            "resolve_user_config_store",
            return_value=({"rest_url": "https://kv.test", "rest_token": "private"}, None),
        ), patch.object(
            user_config_store,
            "read_user_config_record",
            side_effect=read_record,
        ), patch.object(
            user_config_store,
            "write_user_config_record_if_unchanged",
            side_effect=write_record,
        ):
            result = user_config_store.save_owned_custom_imap_folder_mapping(
                {},
                MAILBOX_ID,
                TRASH_FOLDER,
                expected_inbox=copy.deepcopy(baseline["managedInboxes"][0]),
            )

        self.assertEqual(result["status"], "ok")
        saved = result["inbox"]
        self.assertEqual(
            saved["customImapFolderMappings"],
            {"schemaVersion": 1, "trashFolder": TRASH_FOLDER},
        )
        self.assertEqual(saved["credentialVersion"], CREDENTIAL_VERSION)
        self.assertEqual(saved["customImap"], baseline["managedInboxes"][0]["customImap"])

    def test_cas_save_rejects_stale_mailbox_authority_without_writing(self):
        baseline = config_record()
        stale = copy.deepcopy(baseline["managedInboxes"][0])
        stale["title"] = "stale"
        with patch.object(
            user_config_store,
            "resolve_authenticated_user",
            return_value=(
                {"email": OWNER_EMAIL, "name": "Owner", "userType": "member"},
                None,
            ),
        ), patch.object(
            user_config_store,
            "resolve_user_config_store",
            return_value=({"rest_url": "https://kv.test", "rest_token": "private"}, None),
        ), patch.object(
            user_config_store,
            "read_user_config_record",
            return_value={"status": "ok", "config": baseline, "error": None},
        ), patch.object(
            user_config_store,
            "write_user_config_record_if_unchanged",
        ) as write_record:
            result = user_config_store.save_owned_custom_imap_folder_mapping(
                {}, MAILBOX_ID, TRASH_FOLDER, expected_inbox=stale
            )
        self.assertEqual(result["status"], "conflict")
        write_record.assert_not_called()

    def test_cas_save_scrubs_legacy_password_and_verifies_sanitized_readback(self):
        legacy = managed_inbox()
        legacy["customImap"]["password"] = "legacy-password"
        baseline = config_record(inbox=legacy)
        state = {"replacement": None}

        def read_record(_store, _owner):
            record = state["replacement"] or baseline
            return {"status": "ok", "config": copy.deepcopy(record), "error": None}

        def write_record(_store, _owner, _expected, replacement):
            state["replacement"] = copy.deepcopy(replacement)
            return {"status": "ok", "record": replacement, "error": None}

        with patch.object(
            user_config_store,
            "resolve_authenticated_user",
            return_value=(
                {"email": OWNER_EMAIL, "name": "Owner", "userType": "member"},
                None,
            ),
        ), patch.object(
            user_config_store,
            "resolve_user_config_store",
            return_value=({"rest_url": "https://kv.test", "rest_token": "private"}, None),
        ), patch.object(
            user_config_store,
            "read_user_config_record",
            side_effect=read_record,
        ), patch.object(
            user_config_store,
            "write_user_config_record_if_unchanged",
            side_effect=write_record,
        ):
            result = user_config_store.save_owned_custom_imap_folder_mapping(
                {},
                MAILBOX_ID,
                TRASH_FOLDER,
                expected_inbox=copy.deepcopy(legacy),
            )
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("password", result["inbox"]["customImap"])

    def test_cas_save_rejects_provider_mismatch_without_writing(self):
        wrong = managed_inbox()
        wrong["provider"] = "google"
        baseline = config_record(inbox=wrong)
        with patch.object(
            user_config_store,
            "resolve_authenticated_user",
            return_value=(
                {"email": OWNER_EMAIL, "name": "Owner", "userType": "member"},
                None,
            ),
        ), patch.object(
            user_config_store,
            "resolve_user_config_store",
            return_value=({"rest_url": "https://kv.test", "rest_token": "private"}, None),
        ), patch.object(
            user_config_store,
            "read_user_config_record",
            return_value={"status": "ok", "config": baseline, "error": None},
        ), patch.object(
            user_config_store,
            "write_user_config_record_if_unchanged",
        ) as write_record:
            result = user_config_store.save_owned_custom_imap_folder_mapping(
                {},
                MAILBOX_ID,
                TRASH_FOLDER,
                expected_inbox=copy.deepcopy(wrong),
            )
        self.assertEqual(result["status"], "conflict")
        write_record.assert_not_called()


class GenericConfigAuthorityTests(unittest.TestCase):
    def test_generic_merge_cannot_change_remove_or_create_mapping(self):
        existing_mapping = {"schemaVersion": 1, "trashFolder": TRASH_FOLDER}
        existing = config_record(inbox=managed_inbox(mapping=existing_mapping))
        requested = {
            "email": OWNER_EMAIL,
            "updatedAt": "2026-08-10T01:00:00Z",
            "managedInboxes": [
                {
                    "id": MAILBOX_ID,
                    "title": "Safe presentation edit",
                    "customImapFolderMappings": {
                        "schemaVersion": 1,
                        "trashFolder": "Attacker supplied",
                    },
                },
                {
                    "id": "attacker-created",
                    "customImapFolderMappings": {
                        "schemaVersion": 1,
                        "trashFolder": "Attacker supplied",
                    },
                },
            ],
        }
        merged = user_config_api._merge_user_config(existing, requested)
        self.assertEqual(len(merged["managedInboxes"]), 1)
        self.assertEqual(merged["managedInboxes"][0]["title"], "Safe presentation edit")
        self.assertEqual(
            merged["managedInboxes"][0]["customImapFolderMappings"],
            existing_mapping,
        )

        removed = user_config_api._merge_user_config(
            existing,
            {
                "email": OWNER_EMAIL,
                "updatedAt": "2026-08-10T02:00:00Z",
                "managedInboxes": [{"id": MAILBOX_ID}],
            },
        )
        self.assertEqual(
            removed["managedInboxes"][0]["customImapFolderMappings"],
            existing_mapping,
        )

    def test_known_shape_validator_rejects_malformed_or_wrong_provider_mapping(self):
        valid = config_record(
            inbox=managed_inbox(
                mapping={"schemaVersion": 1, "trashFolder": TRASH_FOLDER}
            )
        )
        self.assertTrue(user_config_api._has_valid_known_stored_config_shapes(valid))

        malformed = copy.deepcopy(valid)
        malformed["managedInboxes"][0]["customImapFolderMappings"][
            "schemaVersion"
        ] = 2
        self.assertFalse(
            user_config_api._has_valid_known_stored_config_shapes(malformed)
        )

        wrong_provider = copy.deepcopy(valid)
        wrong_provider["managedInboxes"][0]["provider"] = "google"
        self.assertFalse(
            user_config_api._has_valid_known_stored_config_shapes(wrong_provider)
        )


if __name__ == "__main__":
    unittest.main()
