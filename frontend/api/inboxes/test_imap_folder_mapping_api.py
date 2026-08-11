from __future__ import annotations

import importlib
import importlib.util
import io
import json
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch


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
sys.modules.setdefault("api.auth.runtime", types.ModuleType("api.auth.runtime"))


folder_api = importlib.import_module("api.inboxes.imap_folder_mapping_api")
list_route = importlib.import_module("api.inboxes.list-imap-folders")
save_route = importlib.import_module("api.inboxes.save-imap-folder-mapping")


MAILBOX_ID = "server-mailbox"
OWNER_EMAIL = "owner@example.test"
TRASH_FOLDER = 'Deleted "Items"\\2024'
OTHER_FOLDER = "Bin"
IMAP_PASSWORD = "private-imap-password-never-return"


def mapping(folder: str = TRASH_FOLDER) -> dict:
    return {"schemaVersion": 1, "trashFolder": folder}


def resolved_mailbox(*, mapped_folder: str | None = None) -> dict:
    return {
        "status": "ok",
        "mailbox": {
            "mailboxId": MAILBOX_ID,
            "ownerEmail": OWNER_EMAIL,
            "email": "mailbox@example.test",
            "customImapFolderMappings": (
                mapping(mapped_folder) if mapped_folder is not None else None
            ),
            "imap": {
                "host": "imap.example.test",
                "port": 993,
                "ssl": True,
                "username": "imap-user",
                "password": IMAP_PASSWORD,
            },
        },
        "error": None,
    }


def authority() -> dict:
    inbox = {
        "id": MAILBOX_ID,
        "provider": "custom_imap",
        "connected": True,
        "connectionStatus": "connected",
        "credentialVersion": "A" * 43,
    }
    return {
        "status": "ok",
        "user": {"email": OWNER_EMAIL},
        "inbox": inbox,
        "config": {"managedInboxes": [inbox]},
        "error": None,
    }


def analysis(category: str):
    return SimpleNamespace(
        category=category,
        raw_marker_count=0,
        special_use_folder=TRASH_FOLDER if category == "C" else None,
    )


def resolution(
    *,
    folder: str | None,
    source: str | None,
    error: str | None = None,
    category: str = "B",
):
    return SimpleNamespace(
        folder=folder,
        source=source,
        error=error,
        analysis=analysis(category),
    )


class RecordingMailbox:
    def __init__(self, *, logout_error: Exception | None = None):
        self.logout_error = logout_error
        self.logout_count = 0
        self.shutdown_count = 0

    def logout(self):
        self.logout_count += 1
        if self.logout_error is not None:
            raise self.logout_error

    def shutdown(self):
        self.shutdown_count += 1


class FakeHandler:
    def __init__(self, payload: object = None, *, raw_body: bytes | None = None):
        body = (
            raw_body
            if raw_body is not None
            else json.dumps({} if payload is None else payload).encode("utf-8")
        )
        self.headers = {"content-length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status = None
        self.response_headers: list[tuple[str, str]] = []
        self.command = "POST"

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        pass

    def response(self):
        return json.loads(self.wfile.getvalue())


class ListImapFolderApiTests(unittest.TestCase):
    def _invoke(
        self,
        *,
        category: str,
        mapped_folder: str | None = None,
        eligible: tuple[str, ...] = (TRASH_FOLDER, OTHER_FOLDER),
    ):
        mailbox = RecordingMailbox()
        inventory = SimpleNamespace(entries=(), error=None)

        def resolve_role(_inventory, *, configured_trash_folder=None):
            if category == "C":
                return resolution(
                    folder=TRASH_FOLDER,
                    source="special_use",
                    category="C",
                )
            if category == "B" and configured_trash_folder in eligible:
                return resolution(
                    folder=configured_trash_folder,
                    source="configured",
                )
            return resolution(
                folder=None,
                source=None,
                error="trash_folder_unavailable",
                category=category,
            )

        with patch.object(
            folder_api,
            "resolve_authenticated_imap_mailbox",
            return_value=resolved_mailbox(mapped_folder=mapped_folder),
        ), patch.object(
            folder_api,
            "connect_mailbox_with_settings",
            return_value=mailbox,
        ) as connect, patch.object(
            folder_api,
            "read_imap_list_inventory",
            return_value=inventory,
        ) as read_inventory, patch.object(
            folder_api,
            "analyze_trash_role",
            return_value=analysis(category),
        ), patch.object(
            folder_api,
            "resolve_trash_folder_from_inventory",
            side_effect=resolve_role,
        ), patch.object(
            folder_api,
            "configurable_trash_folder_entries",
            return_value=tuple(
                SimpleNamespace(mailbox=folder) for folder in eligible
            ),
        ):
            status, payload = folder_api.list_imap_folders({}, MAILBOX_ID)
        return status, payload, mailbox, connect, read_inventory

    def test_category_b_without_mapping_returns_safe_mapping_candidates(self):
        status, payload, mailbox, connect, read_inventory = self._invoke(category="B")
        self.assertEqual(status, 200)
        self.assertEqual(
            payload,
            {
                "ok": True,
                "mailboxId": MAILBOX_ID,
                "trash": {"mode": "needs_mapping", "currentFolder": None},
                "folders": [
                    {"providerFolder": TRASH_FOLDER},
                    {"providerFolder": OTHER_FOLDER},
                ],
            },
        )
        connect.assert_called_once()
        read_inventory.assert_called_once_with(mailbox)
        self.assertEqual(mailbox.logout_count, 1)

    def test_category_b_valid_mapping_returns_configured(self):
        status, payload, _mailbox, _connect, _read = self._invoke(
            category="B",
            mapped_folder=TRASH_FOLDER,
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            payload["trash"],
            {"mode": "configured", "currentFolder": TRASH_FOLDER},
        )
        self.assertIn(
            {"providerFolder": TRASH_FOLDER},
            payload["folders"],
        )

    def test_category_b_stale_mapping_returns_needs_mapping(self):
        status, payload, _mailbox, _connect, _read = self._invoke(
            category="B",
            mapped_folder="Renamed stale folder",
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            payload["trash"],
            {"mode": "needs_mapping", "currentFolder": None},
        )

    def test_runtime_candidate_that_cas_would_reject_is_not_public(self):
        status, payload, _mailbox, _connect, _read = self._invoke(
            category="B",
            eligible=(TRASH_FOLDER, "Unsafe\u0085Folder"),
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            payload["folders"],
            [{"providerFolder": TRASH_FOLDER}],
        )

    def test_category_c_returns_automatic_without_mapping_candidates(self):
        status, payload, _mailbox, _connect, _read = self._invoke(category="C")
        self.assertEqual(status, 200)
        self.assertEqual(
            payload,
            {
                "ok": True,
                "mailboxId": MAILBOX_ID,
                "trash": {"mode": "automatic", "currentFolder": TRASH_FOLDER},
                "folders": [],
            },
        )

    def test_categories_a_d_and_e_fail_without_folder_output(self):
        expected = {
            "A": (502, "imap_folder_inventory_failed"),
            "D": (409, "trash_folder_ambiguous"),
            "E": (409, "trash_folder_unavailable"),
        }
        for category, (expected_status, expected_code) in expected.items():
            with self.subTest(category=category):
                status, payload, _mailbox, _connect, _read = self._invoke(
                    category=category
                )
                self.assertEqual(status, expected_status)
                self.assertEqual(payload["error"]["code"], expected_code)
                self.assertNotIn("folders", payload)

    def test_connection_failure_is_sanitized_and_logout_falls_back_to_shutdown(self):
        with patch.object(
            folder_api,
            "resolve_authenticated_imap_mailbox",
            return_value=resolved_mailbox(),
        ), patch.object(
            folder_api,
            "connect_mailbox_with_settings",
            side_effect=RuntimeError(IMAP_PASSWORD),
        ):
            status, payload = folder_api.list_imap_folders({}, MAILBOX_ID)
        self.assertEqual(status, 502)
        self.assertEqual(payload["error"]["code"], "imap_connection_failed")
        self.assertNotIn(IMAP_PASSWORD, json.dumps(payload))

        inventory_failure_mailbox = RecordingMailbox()
        with patch.object(
            folder_api,
            "resolve_authenticated_imap_mailbox",
            return_value=resolved_mailbox(),
        ), patch.object(
            folder_api,
            "connect_mailbox_with_settings",
            return_value=inventory_failure_mailbox,
        ), patch.object(
            folder_api,
            "read_imap_list_inventory",
            side_effect=RuntimeError(f"raw LIST {IMAP_PASSWORD}"),
        ):
            status, payload = folder_api.list_imap_folders({}, MAILBOX_ID)
        self.assertEqual(status, 502)
        self.assertEqual(
            payload["error"]["code"],
            "imap_folder_inventory_failed",
        )
        self.assertEqual(inventory_failure_mailbox.logout_count, 1)
        self.assertNotIn(IMAP_PASSWORD, json.dumps(payload))

        mailbox = RecordingMailbox(logout_error=RuntimeError(IMAP_PASSWORD))
        inventory = SimpleNamespace(entries=(), error=None)
        with patch.object(
            folder_api,
            "resolve_authenticated_imap_mailbox",
            return_value=resolved_mailbox(),
        ), patch.object(
            folder_api,
            "connect_mailbox_with_settings",
            return_value=mailbox,
        ), patch.object(
            folder_api,
            "read_imap_list_inventory",
            return_value=inventory,
        ), patch.object(
            folder_api,
            "analyze_trash_role",
            return_value=analysis("B"),
        ), patch.object(
            folder_api,
            "resolve_trash_folder_from_inventory",
            return_value=resolution(
                folder=None,
                source=None,
                error="trash_folder_unavailable",
            ),
        ), patch.object(
            folder_api,
            "configurable_trash_folder_entries",
            return_value=(),
        ):
            status, _payload = folder_api.list_imap_folders({}, MAILBOX_ID)
        self.assertEqual(status, 200)
        self.assertEqual(mailbox.logout_count, 1)
        self.assertEqual(mailbox.shutdown_count, 1)

    def test_imap_protocol_error_is_classified_by_connection_phase(self):
        with patch.object(
            folder_api,
            "resolve_authenticated_imap_mailbox",
            return_value=resolved_mailbox(),
        ), patch.object(
            folder_api,
            "connect_mailbox_with_settings",
            side_effect=folder_api.imaplib.IMAP4.error(IMAP_PASSWORD),
        ):
            status, payload = folder_api.list_imap_folders({}, MAILBOX_ID)
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "invalid_credentials")
        self.assertNotIn(IMAP_PASSWORD, json.dumps(payload))

        mailbox = RecordingMailbox()
        with patch.object(
            folder_api,
            "resolve_authenticated_imap_mailbox",
            return_value=resolved_mailbox(),
        ), patch.object(
            folder_api,
            "connect_mailbox_with_settings",
            return_value=mailbox,
        ), patch.object(
            folder_api,
            "read_imap_list_inventory",
            side_effect=folder_api.imaplib.IMAP4.error(IMAP_PASSWORD),
        ):
            status, payload = folder_api.list_imap_folders({}, MAILBOX_ID)
        self.assertEqual(status, 502)
        self.assertEqual(
            payload["error"]["code"],
            "imap_folder_inventory_failed",
        )
        self.assertEqual(mailbox.logout_count, 1)
        self.assertNotIn(IMAP_PASSWORD, json.dumps(payload))

    def test_malformed_or_oversized_resolved_connection_fails_before_connect(self):
        cases = {
            "host_whitespace": ("imap", "host", "imap .example.test"),
            "host_c1": ("imap", "host", "imap\u0085.example.test"),
            "username_surrogate": ("imap", "username", "user\ud800"),
            "password_control": ("imap", "password", "secret\nvalue"),
            "host_oversized": ("imap", "host", "x" * 4_097),
            "username_oversized": ("imap", "username", "x" * 4_097),
            "password_oversized": ("imap", "password", "x" * 65_537),
            "bool_port": ("imap", "port", True),
            "owner_invalid": ("mailbox", "ownerEmail", "not-an-email"),
        }
        for name, (scope, field, invalid_value) in cases.items():
            with self.subTest(name=name):
                resolved = resolved_mailbox()
                target = (
                    resolved["mailbox"]["imap"]
                    if scope == "imap"
                    else resolved["mailbox"]
                )
                target[field] = invalid_value
                with patch.object(
                    folder_api,
                    "resolve_authenticated_imap_mailbox",
                    return_value=resolved,
                ), patch.object(
                    folder_api,
                    "connect_mailbox_with_settings",
                ) as connect:
                    status, payload = folder_api.list_imap_folders({}, MAILBOX_ID)
                self.assertEqual(status, 500)
                self.assertEqual(
                    payload["error"]["code"],
                    "mailbox_configuration_malformed",
                )
                connect.assert_not_called()
                serialized = json.dumps(payload)
                self.assertNotIn("imap.example.test", serialized)
                self.assertNotIn("imap-user", serialized)
                self.assertNotIn(IMAP_PASSWORD, serialized)

    def test_private_resolution_details_never_reach_public_error(self):
        resolution_with_private_detail = {
            "status": "service_unavailable",
            "mailbox": None,
            "credentialVersion": "private-generation",
            "rawProviderDetail": f"raw {IMAP_PASSWORD}",
            "error": {
                "code": "mailbox_secret_store_unavailable",
                "message": f"host imap.example.test user imap-user {IMAP_PASSWORD}",
            },
        }
        with patch.object(
            folder_api,
            "resolve_authenticated_imap_mailbox",
            return_value=resolution_with_private_detail,
        ):
            status, payload = folder_api.list_imap_folders({}, MAILBOX_ID)
        self.assertEqual(status, 503)
        self.assertEqual(
            payload["error"]["code"],
            "mailbox_secret_store_unavailable",
        )
        serialized = json.dumps(payload)
        for private in (
            "private-generation",
            "rawProviderDetail",
            "imap.example.test",
            "imap-user",
            IMAP_PASSWORD,
        ):
            self.assertNotIn(private, serialized)

    def test_not_owned_reconnect_and_generation_mismatch_never_connect(self):
        cases = (
            (
                "not_owned",
                {
                    "status": "not_found",
                    "mailbox": None,
                    "error": {"code": "managed_inbox_not_found"},
                },
                404,
                "managed_inbox_not_found",
            ),
            (
                "disconnected",
                {
                    "status": "reconnect_required",
                    "mailbox": None,
                    "error": {"code": "reconnect_required"},
                },
                409,
                "reconnect_required",
            ),
            (
                "secret_generation_mismatch",
                {
                    "status": "reconnect_required",
                    "mailbox": None,
                    "error": {"code": "reconnect_required"},
                },
                409,
                "reconnect_required",
            ),
        )
        for name, resolution_result, expected_status, expected_code in cases:
            with self.subTest(name=name), patch.object(
                folder_api,
                "resolve_authenticated_imap_mailbox",
                return_value=resolution_result,
            ), patch.object(
                folder_api,
                "connect_mailbox_with_settings",
            ) as connect:
                status, payload = folder_api.list_imap_folders({}, MAILBOX_ID)
            self.assertEqual(status, expected_status)
            self.assertEqual(payload["error"]["code"], expected_code)
            connect.assert_not_called()


class SaveImapFolderApiTests(unittest.TestCase):
    def test_invalid_selection_is_rejected_before_authority_or_lease(self):
        with patch.object(
            folder_api,
            "resolve_owned_managed_inbox_record",
        ) as owned, patch.object(
            folder_api,
            "acquire_mailbox_mutation_lease",
        ) as acquire:
            status, payload = folder_api.save_imap_folder_mapping(
                {}, MAILBOX_ID, "Inbox"
            )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_request")
        owned.assert_not_called()
        acquire.assert_not_called()

    def test_held_lease_fails_before_authentication_or_connection(self):
        with patch.object(
            folder_api,
            "resolve_owned_managed_inbox_record",
            return_value=authority(),
        ), patch.object(
            folder_api,
            "acquire_mailbox_mutation_lease",
            return_value={"status": "held", "token": None, "error": None},
        ), patch.object(
            folder_api,
            "resolve_authenticated_imap_mailbox",
        ) as authenticate, patch.object(
            folder_api,
            "connect_mailbox_with_settings",
        ) as connect:
            status, payload = folder_api.save_imap_folder_mapping(
                {}, MAILBOX_ID, TRASH_FOLDER
            )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "mailbox_mutation_in_progress")
        authenticate.assert_not_called()
        connect.assert_not_called()

    def test_non_custom_provider_is_hidden_as_not_found_before_lease(self):
        google_authority = authority()
        google_authority["inbox"]["provider"] = "google"
        with patch.object(
            folder_api,
            "resolve_owned_managed_inbox_record",
            return_value=google_authority,
        ), patch.object(
            folder_api,
            "acquire_mailbox_mutation_lease",
        ) as acquire, patch.object(
            folder_api,
            "connect_mailbox_with_settings",
        ) as connect:
            status, payload = folder_api.save_imap_folder_mapping(
                {}, MAILBOX_ID, TRASH_FOLDER
            )
        self.assertEqual(status, 404)
        self.assertEqual(
            payload["error"]["code"],
            "managed_inbox_not_found",
        )
        acquire.assert_not_called()
        connect.assert_not_called()

    def test_not_owned_and_duplicate_authority_fail_before_lease_or_connect(self):
        cases = (
            (
                "not_owned",
                {
                    "status": "not_found",
                    "user": {"email": OWNER_EMAIL},
                    "inbox": None,
                    "config": None,
                    "error": {"code": "managed_inbox_not_found"},
                },
                404,
                "managed_inbox_not_found",
            ),
            (
                "duplicate_mailbox",
                {
                    "status": "malformed",
                    "user": {"email": OWNER_EMAIL},
                    "inbox": None,
                    "config": None,
                    "error": {"code": "duplicate_mailbox_id"},
                },
                500,
                "mailbox_configuration_malformed",
            ),
        )
        for name, authority_result, expected_status, expected_code in cases:
            with self.subTest(name=name), patch.object(
                folder_api,
                "resolve_owned_managed_inbox_record",
                return_value=authority_result,
            ), patch.object(
                folder_api,
                "acquire_mailbox_mutation_lease",
            ) as acquire, patch.object(
                folder_api,
                "connect_mailbox_with_settings",
            ) as connect:
                status, payload = folder_api.save_imap_folder_mapping(
                    {}, MAILBOX_ID, TRASH_FOLDER
                )
            self.assertEqual(status, expected_status)
            self.assertEqual(payload["error"]["code"], expected_code)
            acquire.assert_not_called()
            connect.assert_not_called()

    def test_disconnected_custom_releases_lease_without_list_or_cas(self):
        disconnected_authority = authority()
        disconnected_authority["inbox"]["connected"] = False
        reconnect_required = {
            "status": "reconnect_required",
            "mailbox": None,
            "error": {"code": "reconnect_required"},
        }
        with patch.object(
            folder_api,
            "resolve_owned_managed_inbox_record",
            return_value=disconnected_authority,
        ), patch.object(
            folder_api,
            "acquire_mailbox_mutation_lease",
            return_value={"status": "acquired", "token": "lease-token", "error": None},
        ), patch.object(
            folder_api,
            "release_mailbox_mutation_lease",
            return_value={"status": "released", "token": "lease-token", "error": None},
        ) as release, patch.object(
            folder_api,
            "resolve_authenticated_imap_mailbox",
            return_value=reconnect_required,
        ), patch.object(
            folder_api,
            "connect_mailbox_with_settings",
        ) as connect, patch.object(
            folder_api,
            "read_imap_list_inventory",
        ) as read_inventory, patch.object(
            folder_api,
            "save_owned_custom_imap_folder_mapping",
        ) as save:
            status, payload = folder_api.save_imap_folder_mapping(
                {}, MAILBOX_ID, TRASH_FOLDER
            )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "reconnect_required")
        connect.assert_not_called()
        read_inventory.assert_not_called()
        save.assert_not_called()
        release.assert_called_once_with(OWNER_EMAIL, MAILBOX_ID, "lease-token")

    def test_save_authority_error_does_not_expose_private_details(self):
        private_authority = {
            "status": "unavailable",
            "user": {"email": OWNER_EMAIL},
            "inbox": {
                "credentialVersion": "private-generation",
                "customImap": {
                    "host": "imap.example.test",
                    "username": "imap-user",
                    "password": IMAP_PASSWORD,
                },
            },
            "rawProviderDetail": f"raw {IMAP_PASSWORD}",
            "error": {"code": "private", "message": IMAP_PASSWORD},
        }
        with patch.object(
            folder_api,
            "resolve_owned_managed_inbox_record",
            return_value=private_authority,
        ), patch.object(
            folder_api,
            "acquire_mailbox_mutation_lease",
        ) as acquire:
            status, payload = folder_api.save_imap_folder_mapping(
                {}, MAILBOX_ID, TRASH_FOLDER
            )
        self.assertEqual(status, 503)
        serialized = json.dumps(payload)
        for private in (
            "private-generation",
            "rawProviderDetail",
            "imap.example.test",
            "imap-user",
            IMAP_PASSWORD,
        ):
            self.assertNotIn(private, serialized)
        acquire.assert_not_called()

    def _invoke_under_lease(
        self,
        *,
        category: str = "B",
        selection_is_current: bool = True,
        save_status: str = "ok",
        release_status: str = "released",
        inventory_error: Exception | None = None,
    ):
        mailbox = RecordingMailbox()
        inventory = SimpleNamespace(entries=(), error=None)

        def resolve_role(_inventory, *, configured_trash_folder=None):
            if category == "C":
                return resolution(
                    folder=TRASH_FOLDER,
                    source="special_use",
                    category="C",
                )
            if configured_trash_folder == TRASH_FOLDER and selection_is_current:
                return resolution(folder=TRASH_FOLDER, source="configured")
            return resolution(
                folder=None,
                source=None,
                error="trash_folder_unavailable",
            )

        save_result = (
            {
                "status": "ok",
                "inbox": {"customImapFolderMappings": mapping()},
                "error": None,
            }
            if save_status == "ok"
            else {"status": save_status, "inbox": None, "error": None}
        )
        auth_results = [resolved_mailbox(), resolved_mailbox(mapped_folder=TRASH_FOLDER)]
        with patch.object(
            folder_api,
            "resolve_owned_managed_inbox_record",
            return_value=authority(),
        ) as owned, patch.object(
            folder_api,
            "acquire_mailbox_mutation_lease",
            return_value={"status": "acquired", "token": "lease-token", "error": None},
        ) as acquire, patch.object(
            folder_api,
            "release_mailbox_mutation_lease",
            return_value={"status": release_status, "token": "lease-token", "error": None},
        ) as release, patch.object(
            folder_api,
            "resolve_authenticated_imap_mailbox",
            side_effect=auth_results,
        ) as authenticate, patch.object(
            folder_api,
            "connect_mailbox_with_settings",
            return_value=mailbox,
        ), patch.object(
            folder_api,
            "read_imap_list_inventory",
            return_value=inventory,
            side_effect=inventory_error,
        ) as read_inventory, patch.object(
            folder_api,
            "analyze_trash_role",
            return_value=analysis(category),
        ), patch.object(
            folder_api,
            "resolve_trash_folder_from_inventory",
            side_effect=resolve_role,
        ), patch.object(
            folder_api,
            "configurable_trash_folder_entries",
            return_value=(SimpleNamespace(mailbox=TRASH_FOLDER),),
        ), patch.object(
            folder_api,
            "save_owned_custom_imap_folder_mapping",
            return_value=save_result,
        ) as save:
            status, payload = folder_api.save_imap_folder_mapping(
                {}, MAILBOX_ID, TRASH_FOLDER
            )
        return {
            "status": status,
            "payload": payload,
            "mailbox": mailbox,
            "owned": owned,
            "acquire": acquire,
            "release": release,
            "authenticate": authenticate,
            "read_inventory": read_inventory,
            "save": save,
        }

    def test_save_uses_one_fresh_inventory_cas_readback_and_releases_lease(self):
        result = self._invoke_under_lease()
        self.assertEqual(result["status"], 200)
        self.assertEqual(
            result["payload"],
            {
                "ok": True,
                "mailboxId": MAILBOX_ID,
                "trash": {"mode": "configured", "currentFolder": TRASH_FOLDER},
                "folders": [{"providerFolder": TRASH_FOLDER}],
            },
        )
        self.assertEqual(result["owned"].call_count, 2)
        result["acquire"].assert_called_once_with(OWNER_EMAIL, MAILBOX_ID)
        result["release"].assert_called_once_with(
            OWNER_EMAIL, MAILBOX_ID, "lease-token"
        )
        self.assertEqual(result["authenticate"].call_count, 2)
        result["read_inventory"].assert_called_once_with(result["mailbox"])
        result["save"].assert_called_once()
        self.assertEqual(
            result["save"].call_args.kwargs["expected_inbox"],
            authority()["inbox"],
        )
        self.assertEqual(result["mailbox"].logout_count, 1)
        serialized = json.dumps(result["payload"])
        for private in (
            "credentialVersion",
            "imap.example.test",
            "imap-user",
            IMAP_PASSWORD,
        ):
            self.assertNotIn(private, serialized)

    def test_stale_selection_fails_before_cas_and_still_releases(self):
        result = self._invoke_under_lease(selection_is_current=False)
        self.assertEqual(result["status"], 409)
        self.assertEqual(
            result["payload"]["error"]["code"],
            "imap_folder_selection_stale",
        )
        result["save"].assert_not_called()
        result["release"].assert_called_once()
        self.assertEqual(result["mailbox"].logout_count, 1)

    def test_special_use_wins_without_writing_mapping(self):
        result = self._invoke_under_lease(category="C")
        self.assertEqual(result["status"], 200)
        self.assertEqual(
            result["payload"]["trash"],
            {"mode": "automatic", "currentFolder": TRASH_FOLDER},
        )
        result["save"].assert_not_called()
        result["release"].assert_called_once()

    def test_save_inventory_protocol_error_is_not_an_authentication_error(self):
        result = self._invoke_under_lease(
            inventory_error=folder_api.imaplib.IMAP4.error(IMAP_PASSWORD),
        )
        self.assertEqual(result["status"], 502)
        self.assertEqual(
            result["payload"]["error"]["code"],
            "imap_folder_inventory_failed",
        )
        self.assertEqual(result["mailbox"].logout_count, 1)
        result["save"].assert_not_called()
        result["release"].assert_called_once()
        self.assertNotIn(IMAP_PASSWORD, json.dumps(result["payload"]))

    def test_cas_conflict_is_public_409_and_releases(self):
        result = self._invoke_under_lease(save_status="conflict")
        self.assertEqual(result["status"], 409)
        self.assertEqual(
            result["payload"]["error"]["code"],
            "mailbox_configuration_changed",
        )
        result["release"].assert_called_once()

    def test_release_failure_overrides_success_confirmation(self):
        result = self._invoke_under_lease(release_status="ambiguous")
        self.assertEqual(result["status"], 503)
        self.assertEqual(
            result["payload"]["error"]["code"],
            "mailbox_mutation_lease_unavailable",
        )


class FolderMappingRouteTests(unittest.TestCase):
    def test_list_route_requires_exact_request_and_forwards_exact_success(self):
        expected = {
            "ok": True,
            "mailboxId": MAILBOX_ID,
            "trash": {"mode": "needs_mapping", "currentFolder": None},
            "folders": [{"providerFolder": TRASH_FOLDER}],
        }
        target = FakeHandler({"mailboxId": MAILBOX_ID})
        with patch.object(
            list_route,
            "list_imap_folders",
            return_value=(200, expected),
        ) as invoke:
            list_route.handler._handle_post(target)
        self.assertEqual(target.status, 200)
        self.assertEqual(target.response(), expected)
        invoke.assert_called_once_with(target.headers, MAILBOX_ID)
        self.assertIn(("Cache-Control", "no-store"), target.response_headers)

        invalid = FakeHandler({"mailboxId": MAILBOX_ID, "password": IMAP_PASSWORD})
        with patch.object(list_route, "list_imap_folders") as invoke:
            list_route.handler._handle_post(invalid)
        self.assertEqual(invalid.status, 400)
        self.assertEqual(invalid.response()["error"]["code"], "invalid_request")
        self.assertNotIn(IMAP_PASSWORD, json.dumps(invalid.response()))
        invoke.assert_not_called()

    def test_save_route_accepts_only_trash_and_exact_fields(self):
        expected = {
            "ok": True,
            "mailboxId": MAILBOX_ID,
            "trash": {"mode": "configured", "currentFolder": TRASH_FOLDER},
            "folders": [{"providerFolder": TRASH_FOLDER}],
        }
        target = FakeHandler(
            {
                "mailboxId": MAILBOX_ID,
                "role": "trash",
                "selectedFolder": TRASH_FOLDER,
            }
        )
        with patch.object(
            save_route,
            "save_imap_folder_mapping",
            return_value=(200, expected),
        ) as invoke:
            save_route.handler._handle_post(target)
        self.assertEqual(target.status, 200)
        self.assertEqual(target.response(), expected)
        invoke.assert_called_once_with(target.headers, MAILBOX_ID, TRASH_FOLDER)

        for payload in (
            {
                "mailboxId": MAILBOX_ID,
                "role": "archive",
                "selectedFolder": TRASH_FOLDER,
            },
            {
                "mailboxId": MAILBOX_ID,
                "role": "trash",
                "selectedFolder": TRASH_FOLDER,
                "password": IMAP_PASSWORD,
            },
        ):
            with self.subTest(payload=payload):
                invalid = FakeHandler(payload)
                with patch.object(save_route, "save_imap_folder_mapping") as invoke:
                    save_route.handler._handle_post(invalid)
                self.assertEqual(invalid.status, 400)
                self.assertNotIn(IMAP_PASSWORD, json.dumps(invalid.response()))
                invoke.assert_not_called()

    def test_routes_are_post_only_cache_free_and_hide_exceptions(self):
        for route in (list_route, save_route):
            for method in ("do_GET", "do_PUT", "do_PATCH", "do_DELETE"):
                with self.subTest(route=route.__name__, method=method):
                    target = FakeHandler({})
                    target.command = method.removeprefix("do_")
                    target.do_GET = types.MethodType(route.handler.do_GET, target)
                    getattr(route.handler, method)(target)
                    self.assertEqual(target.status, 405)
                    self.assertEqual(
                        target.response()["error"]["code"], "method_not_allowed"
                    )
                    self.assertIn(
                        ("Cache-Control", "no-store"),
                        target.response_headers,
                    )

            head = FakeHandler({})
            head.command = "HEAD"
            route.handler.do_HEAD(head)
            self.assertEqual(head.status, 405)
            self.assertEqual(head.wfile.getvalue(), b"")

            options = FakeHandler({})
            route.handler.do_OPTIONS(options)
            self.assertEqual(options.status, 200)
            self.assertEqual(options.response(), {"ok": True})

        target = FakeHandler({"mailboxId": MAILBOX_ID})
        with patch.object(
            list_route.handler,
            "_handle_post",
            side_effect=RuntimeError(IMAP_PASSWORD),
        ):
            list_route.handler.do_POST(target)
        self.assertEqual(target.status, 500)
        self.assertEqual(target.response()["error"]["code"], "internal_error")
        self.assertNotIn(IMAP_PASSWORD, json.dumps(target.response()))


if __name__ == "__main__":
    unittest.main()
