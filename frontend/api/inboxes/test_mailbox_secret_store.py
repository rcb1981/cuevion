import base64
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
FRONTEND_DIR = API_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import mailbox_secret_store as store

ORIGINAL_READ_DURABLE_RECORD = store._read_durable_record
ORIGINAL_WRITE_DURABLE_RECORD = store._write_durable_record
ORIGINAL_DELETE_DURABLE_RECORD = store._delete_durable_record
ORIGINAL_ATOMIC_COMMAND = store._perform_atomic_secret_command


def encoded_key(byte=b"k"):
    return base64.urlsafe_b64encode(byte * 32).decode().rstrip("=")


class InMemorySecretStore:
    def __init__(self):
        self.records = {}
        self.writes = []

    def read(self, _config, key):
        return self.records.get(key), None

    def write(self, _config, key, record):
        self.records[key] = json.loads(json.dumps(record))
        self.writes.append((key, json.loads(json.dumps(record))))
        return None, None

    def delete(self, _config, key):
        self.records.pop(key, None)
        return None

    def create_namespace(self, _config, encrypted_key, legacy_key, replacement):
        if encrypted_key in self.records or legacy_key in self.records:
            return 0, None
        self.records[encrypted_key] = json.loads(json.dumps(replacement))
        self.writes.append(("create", encrypted_key, json.loads(json.dumps(replacement))))
        return 1, None

    def compare_and_set(self, _config, key, expected_snapshot, replacement):
        current = self.records.get(key)
        expected_status = expected_snapshot["status"]
        matches = (
            current is None
            if expected_status == "missing"
            else current == expected_snapshot.get("record")
        )
        if not matches:
            return 0, None
        self.records[key] = json.loads(json.dumps(replacement))
        self.writes.append(("replace", key, json.loads(json.dumps(replacement))))
        return 1, None

    def compare_and_delete(self, _config, key, expected_record):
        if self.records.get(key) != expected_record:
            return 0, None
        del self.records[key]
        self.writes.append(("delete", key))
        return 1, None


class MailboxSecretStoreTests(unittest.TestCase):
    VERSION_A = base64.urlsafe_b64encode(b"a" * 32).decode().rstrip("=")
    VERSION_B = base64.urlsafe_b64encode(b"b" * 32).decode().rstrip("=")
    VERSION_C = base64.urlsafe_b64encode(b"c" * 32).decode().rstrip("=")

    def setUp(self):
        self.memory = InMemorySecretStore()
        self.patches = [
            patch.object(store, "_resolve_durable_store_config", return_value={"configured": True}),
            patch.object(store, "_read_durable_record", side_effect=self.memory.read),
            patch.object(store, "_write_durable_record", side_effect=self.memory.write),
            patch.object(store, "_delete_durable_record", side_effect=self.memory.delete),
            patch.object(
                store,
                "_perform_create_secret_namespace_if_missing",
                side_effect=self.memory.create_namespace,
            ),
            patch.object(
                store,
                "_perform_compare_and_set_secret",
                side_effect=self.memory.compare_and_set,
            ),
            patch.object(
                store,
                "_perform_compare_and_delete_secret",
                side_effect=self.memory.compare_and_delete,
            ),
            patch.dict(
                os.environ,
                {store.MAILBOX_SECRET_ENCRYPTION_KEY_ENV: encoded_key()},
                clear=False,
            ),
        ]
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()

    def test_new_records_are_aes_gcm_encrypted_and_owner_bound(self):
        saved, error = store.save_mailbox_secret(
            "Owner@Example.com",
            "demo",
            imap_password="imap-plaintext",
            smtp_password="smtp-plaintext",
        )
        self.assertIsNone(error)
        self.assertEqual(saved["imapPassword"], "imap-plaintext")

        key = store.build_encrypted_mailbox_secret_key("owner@example.com", "demo")
        persisted = self.memory.records[key]
        self.assertEqual(persisted["v"], 2)
        self.assertEqual(persisted["algorithm"], "AES-256-GCM")
        self.assertEqual(set(persisted), {"v", "algorithm", "nonce", "ciphertext", "updatedAt"})
        self.assertNotIn("imap-plaintext", json.dumps(persisted))
        self.assertNotIn("smtp-plaintext", json.dumps(persisted))

        result = store.read_mailbox_secret("owner@example.com", "demo")
        self.assertEqual(result["status"], "present")
        self.assertEqual(result["record"]["smtpPassword"], "smtp-plaintext")

        wrong_owner, decrypt_error = store._decrypt_secret_record(
            b"k" * 32,
            "other@example.com",
            "demo",
            persisted,
        )
        self.assertIsNone(wrong_owner)
        self.assertEqual(decrypt_error["code"], "mailbox_secret_malformed")

        wrong_mailbox, decrypt_error = store._decrypt_secret_record(
            b"k" * 32,
            "owner@example.com",
            "other",
            persisted,
        )
        self.assertIsNone(wrong_mailbox)
        self.assertEqual(decrypt_error["code"], "mailbox_secret_malformed")

    def test_corruption_and_invalid_or_missing_keys_fail_closed(self):
        store.save_mailbox_secret("owner@example.com", "demo", imap_password="secret")
        key = store.build_encrypted_mailbox_secret_key("owner@example.com", "demo")
        self.memory.records[key]["ciphertext"] = "AAAA"
        self.assertEqual(
            store.read_mailbox_secret("owner@example.com", "demo")["status"],
            "malformed",
        )
        self.memory.records[key] = {
            "v": 2,
            "algorithm": "AES-256-GCM",
            "nonce": "AAAA",
            "ciphertext": "AAAA",
            "updatedAt": "2026-01-01T00:00:00Z",
            "unexpected": True,
        }
        self.assertEqual(
            store.read_mailbox_secret("owner@example.com", "demo")["status"],
            "malformed",
        )

        for environment in ({}, {store.MAILBOX_SECRET_ENCRYPTION_KEY_ENV: "bad"}):
            with self.subTest(environment=environment), patch.dict(
                os.environ,
                environment,
                clear=True,
            ):
                self.assertEqual(
                    store.read_mailbox_secret("owner@example.com", "demo")["status"],
                    "unavailable",
                )

    def test_missing_and_outage_remain_distinct(self):
        self.assertEqual(
            store.read_mailbox_secret("owner@example.com", "missing")["status"],
            "missing",
        )
        with patch.object(
            store,
            "_read_durable_record",
            return_value=(None, {"code": "mailbox_secret_store_unavailable", "message": "offline"}),
        ):
            self.assertEqual(
                store.read_mailbox_secret("owner@example.com", "demo")["status"],
                "unavailable",
            )

    def test_legacy_read_is_side_effect_free_until_conditional_reconnect(self):
        legacy_key = store.build_mailbox_secret_key("owner@example.com", "demo")
        legacy = {
            "v": 1,
            "mailboxId": "demo",
            "imapPassword": "legacy-imap",
            "smtpPassword": "legacy-smtp",
            "updatedAt": "2026-01-01T00:00:00Z",
        }
        self.memory.records[legacy_key] = json.loads(json.dumps(legacy))

        result = store.read_mailbox_secret("owner@example.com", "demo")
        self.assertEqual(result["status"], "present")
        self.assertEqual(result["record"]["imapPassword"], "legacy-imap")
        self.assertEqual(self.memory.records[legacy_key], legacy)
        encrypted_key = store.build_encrypted_mailbox_secret_key("owner@example.com", "demo")
        self.assertNotIn(encrypted_key, self.memory.records)
        self.assertEqual(self.memory.writes, [])

        saved, save_error = store.save_mailbox_secret(
            "owner@example.com",
            "demo",
            imap_password="must-not-shadow-legacy",
        )
        self.assertIsNone(saved)
        self.assertEqual(save_error["code"], "mailbox_secret_write_conflict")
        self.assertNotIn(encrypted_key, self.memory.records)
        self.assertEqual(self.memory.records[legacy_key], legacy)

    def test_blank_updates_never_replace_nonempty_secrets(self):
        store.save_mailbox_secret(
            "owner@example.com",
            "demo",
            imap_password="imap",
            smtp_password="smtp",
        )
        saved, error = store.save_mailbox_secret(
            "owner@example.com",
            "demo",
            imap_password="",
            smtp_password="",
        )
        self.assertIsNone(error)
        self.assertEqual(saved["imapPassword"], "imap")
        self.assertEqual(saved["smtpPassword"], "smtp")

    def test_raw_record_types_and_unknown_versions_are_malformed(self):
        encrypted_key = store.build_encrypted_mailbox_secret_key(
            "owner@example.com",
            "demo",
        )
        legacy_key = store.build_mailbox_secret_key("owner@example.com", "demo")

        for value in ('"plain-string"', ["not", "an", "object"]):
            with self.subTest(value=value), patch.object(
                store,
                "_perform_rest_request",
                return_value=({"result": value}, None),
            ):
                record, error = ORIGINAL_READ_DURABLE_RECORD(
                    {"configured": True},
                    encrypted_key,
                )
            self.assertIsNone(record)
            self.assertEqual(error["code"], "mailbox_secret_malformed")

        self.memory.records[encrypted_key] = {
            "v": 99,
            "algorithm": "AES-256-GCM",
            "nonce": "AAAA",
            "ciphertext": "AAAA",
            "updatedAt": "2026-01-01T00:00:00Z",
        }
        self.assertEqual(
            store.read_mailbox_secret("owner@example.com", "demo")["status"],
            "malformed",
        )

        self.memory.records.pop(encrypted_key)
        self.memory.records[legacy_key] = {"imapPassword": "arbitrary"}
        self.assertEqual(
            store.read_mailbox_secret("owner@example.com", "demo")["status"],
            "malformed",
        )

    def test_durable_store_requires_explicit_get_set_and_delete_acknowledgements(self):
        config = {"configured": True}
        with patch.object(
            store,
            "_perform_rest_request",
            return_value=({"result": None}, None),
        ):
            missing, error = ORIGINAL_READ_DURABLE_RECORD(config, "secret-key")
        self.assertIsNone(missing)
        self.assertIsNone(error)

        for payload in ({}, {"result": None, "extra": True}):
            with self.subTest(operation="get", payload=payload), patch.object(
                store,
                "_perform_rest_request",
                return_value=(payload, None),
            ):
                record, error = ORIGINAL_READ_DURABLE_RECORD(config, "secret-key")
            self.assertIsNone(record)
            self.assertEqual(error["code"], "mailbox_secret_malformed")

        for payload in ({}, {"result": None}, {"result": "NOPE"}, {"result": "OK", "extra": True}):
            with self.subTest(operation="set", payload=payload), patch.object(
                store,
                "_perform_rest_request",
                return_value=(payload, None),
            ):
                _, error = ORIGINAL_WRITE_DURABLE_RECORD(config, "secret-key", {"v": 2})
            self.assertEqual(error["code"], "mailbox_secret_malformed")

        with patch.object(
            store,
            "_perform_rest_request",
            return_value=({"result": "OK"}, None),
        ):
            acknowledgement, error = ORIGINAL_WRITE_DURABLE_RECORD(
                config,
                "secret-key",
                {"v": 2},
            )
        self.assertEqual(acknowledgement, {"result": "OK"})
        self.assertIsNone(error)

        for result in (0, 1):
            with self.subTest(operation="delete", result=result), patch.object(
                store,
                "_perform_rest_request",
                return_value=({"result": result}, None),
            ):
                self.assertIsNone(ORIGINAL_DELETE_DURABLE_RECORD(config, "secret-key"))

        for payload in ({}, {"result": None}, {"result": True}, {"result": 2}, {"result": 1, "extra": True}):
            with self.subTest(operation="delete-invalid", payload=payload), patch.object(
                store,
                "_perform_rest_request",
                return_value=(payload, None),
            ):
                error = ORIGINAL_DELETE_DURABLE_RECORD(config, "secret-key")
            self.assertEqual(error["code"], "mailbox_secret_malformed")

    def test_http_error_parsing_is_total_for_non_object_json_bodies(self):
        config = {
            "rest_url": "https://store.invalid",
            "rest_token": "token",
        }
        for error_payload in ([], "gateway failure", 500, {"error": 123}):
            with self.subTest(error_payload=error_payload):
                http_error = HTTPError(
                    "https://store.invalid",
                    503,
                    "Service Unavailable",
                    hdrs=None,
                    fp=io.BytesIO(json.dumps(error_payload).encode("utf-8")),
                )
                with patch.object(store, "urlopen", side_effect=http_error):
                    payload, error = store._perform_rest_request(
                        config,
                        "POST",
                        "",
                        body=b"[]",
                    )
                self.assertIsNone(payload)
                self.assertEqual(error["code"], "mailbox_secret_store_unavailable")
                self.assertIn("HTTP 503", error["message"])

    def test_strict_legacy_schema_rejects_bad_types_and_wrong_mailbox(self):
        legacy_key = store.build_mailbox_secret_key("owner@example.com", "demo")
        valid = {
            "v": 1,
            "mailboxId": "demo",
            "imapPassword": "legacy-imap",
            "smtpPassword": "legacy-smtp",
            "updatedAt": "2026-01-01T00:00:00Z",
        }
        for overrides in (
            {"imapPassword": 123},
            {"smtpPassword": None},
            {"mailboxId": "other"},
            {"ownerEmail": "other@example.com"},
            {"v": 2},
        ):
            with self.subTest(overrides=overrides):
                self.memory.records.clear()
                self.memory.records[legacy_key] = {**valid, **overrides}
                result = store.read_mailbox_secret("owner@example.com", "demo")
                self.assertEqual(result["status"], "malformed")
                self.assertNotIn(
                    store.build_encrypted_mailbox_secret_key(
                        "owner@example.com",
                        "demo",
                    ),
                    self.memory.records,
                )

    def test_missing_malformed_v2_and_unavailable_are_distinct(self):
        self.assertEqual(
            store.read_mailbox_secret("owner@example.com", "demo")["status"],
            "missing",
        )
        key = store.build_encrypted_mailbox_secret_key("owner@example.com", "demo")
        self.memory.records[key] = {
            "v": 2,
            "algorithm": "AES-256-GCM",
            "nonce": "AAAA",
        }
        self.assertEqual(
            store.read_mailbox_secret("owner@example.com", "demo")["status"],
            "malformed",
        )
        with patch.object(
            store,
            "_read_durable_record",
            return_value=(
                None,
                {"code": "mailbox_secret_store_unavailable", "message": "offline"},
            ),
        ):
            self.assertEqual(
                store.read_mailbox_secret("owner@example.com", "demo")["status"],
                "unavailable",
            )

    def test_urlsafe_key_format_is_canonical_and_exactly_32_bytes(self):
        unpadded = encoded_key(b"\xfb")
        padded = f"{unpadded}="
        for value in (unpadded, padded):
            with self.subTest(value=value), patch.dict(
                os.environ,
                {store.MAILBOX_SECRET_ENCRYPTION_KEY_ENV: value},
                clear=True,
            ):
                key, error = store._resolve_encryption_key()
                self.assertEqual(key, b"\xfb" * 32)
                self.assertIsNone(error)

        standard = base64.b64encode(b"\xfb" * 32).decode().rstrip("=")
        noncanonical = f"{unpadded[:-1]}t"
        invalid_values = (
            standard,
            noncanonical,
            f"{unpadded}==",
            f"{unpadded}=x",
            f" {unpadded}",
            f"{unpadded}\n",
            encoded_key(b"k")[:-1],
            base64.urlsafe_b64encode(b"short").decode().rstrip("="),
        )
        for value in invalid_values:
            with self.subTest(value=value), patch.dict(
                os.environ,
                {store.MAILBOX_SECRET_ENCRYPTION_KEY_ENV: value},
                clear=True,
            ):
                key, error = store._resolve_encryption_key()
                self.assertIsNone(key)
                self.assertEqual(error["code"], "mailbox_secret_store_unavailable")

    def test_snapshot_restore_preserves_or_deletes_only_v2_state(self):
        legacy_key = store.build_mailbox_secret_key("owner@example.com", "demo")
        legacy = {
            "v": 1,
            "mailboxId": "demo",
            "imapPassword": "legacy",
            "smtpPassword": "legacy",
            "updatedAt": "2026-01-01T00:00:00Z",
        }

        missing_snapshot = store.snapshot_encrypted_mailbox_secret(
            "owner@example.com",
            "demo",
        )
        self.assertEqual(missing_snapshot["status"], "missing")
        created, _ = store.save_mailbox_secret(
            "owner@example.com",
            "demo",
            imap_password="new",
            smtp_password="new",
        )
        self.memory.records[legacy_key] = json.loads(json.dumps(legacy))
        self.assertIsNone(
            store.restore_encrypted_mailbox_secret_snapshot(
                "owner@example.com",
                "demo",
                missing_snapshot,
                expected_credential_version=created["credentialVersion"],
            )
        )
        encrypted_key = store.build_encrypted_mailbox_secret_key(
            "owner@example.com",
            "demo",
        )
        self.assertNotIn(encrypted_key, self.memory.records)
        self.assertEqual(self.memory.records[legacy_key], legacy)

        present_mailbox_id = "present"
        store.save_mailbox_secret(
            "owner@example.com",
            present_mailbox_id,
            imap_password="before",
            smtp_password="before",
        )
        present_snapshot = store.snapshot_encrypted_mailbox_secret(
            "owner@example.com",
            present_mailbox_id,
        )
        exact_before = json.loads(json.dumps(present_snapshot["record"]))
        after, _ = store.save_mailbox_secret(
            "owner@example.com",
            present_mailbox_id,
            imap_password="after",
            smtp_password="after",
        )
        self.assertIsNone(
            store.restore_encrypted_mailbox_secret_snapshot(
                "owner@example.com",
                present_mailbox_id,
                present_snapshot,
                expected_credential_version=after["credentialVersion"],
            )
        )
        present_encrypted_key = store.build_encrypted_mailbox_secret_key(
            "owner@example.com",
            present_mailbox_id,
        )
        self.assertEqual(self.memory.records[present_encrypted_key], exact_before)

    def test_namespace_snapshot_detects_legacy_without_migrating_or_writing(self):
        legacy_key = store.build_mailbox_secret_key("owner@example.com", "demo")
        legacy = {
            "v": 1,
            "mailboxId": "demo",
            "imapPassword": "legacy",
            "smtpPassword": "legacy",
            "updatedAt": "2026-01-01T00:00:00Z",
        }
        self.memory.records[legacy_key] = json.loads(json.dumps(legacy))

        snapshot = store.snapshot_mailbox_secret_namespace(
            "owner@example.com",
            "demo",
        )

        self.assertEqual(snapshot, {"status": "present", "record": None, "error": None})
        self.assertEqual(self.memory.records, {legacy_key: legacy})
        self.assertEqual(self.memory.writes, [])

        missing = store.snapshot_mailbox_secret_namespace(
            "owner@example.com",
            "unused",
        )
        self.assertEqual(missing, {"status": "missing", "record": None, "error": None})

        malformed_error = {
            "code": "mailbox_secret_malformed",
            "message": "missing result acknowledgement",
        }
        with patch.object(
            store,
            "_read_durable_record",
            return_value=(None, malformed_error),
        ):
            malformed = store.snapshot_mailbox_secret_namespace(
                "owner@example.com",
                "unused",
            )
        self.assertEqual(malformed["status"], "malformed")
        self.assertEqual(malformed["error"], malformed_error)

    def test_conditional_create_has_one_winner_and_preserves_it_exactly(self):
        winner = store.create_mailbox_secret_if_missing(
            "owner@example.com",
            "demo",
            self.VERSION_A,
            imap_password="winner",
            smtp_password="winner-smtp",
        )
        self.assertEqual(winner["status"], "applied")
        key = store.build_encrypted_mailbox_secret_key(
            "owner@example.com",
            "demo",
        )
        exact_winner = json.loads(json.dumps(self.memory.records[key]))

        loser = store.create_mailbox_secret_if_missing(
            "owner@example.com",
            "demo",
            self.VERSION_B,
            imap_password="loser",
            smtp_password="loser-smtp",
        )

        self.assertEqual(loser["status"], "conflict")
        self.assertEqual(self.memory.records[key], exact_winner)
        readback = store.read_mailbox_secret("owner@example.com", "demo")
        self.assertEqual(readback["record"]["credentialVersion"], self.VERSION_A)
        self.assertEqual(readback["record"]["imapPassword"], "winner")

    def test_conditional_create_does_not_shadow_a_concurrent_legacy_secret(self):
        owner_email = "owner@example.com"
        mailbox_id = "demo"
        legacy_key = store.build_mailbox_secret_key(owner_email, mailbox_id)
        encrypted_key = store.build_encrypted_mailbox_secret_key(
            owner_email,
            mailbox_id,
        )
        legacy = {
            "v": 1,
            "mailboxId": mailbox_id,
            "imapPassword": "newer-legacy",
            "smtpPassword": "newer-legacy-smtp",
            "updatedAt": "2026-01-01T00:00:00Z",
        }

        def concurrent_legacy_then_create(config, encrypted, legacy_store, replacement):
            self.memory.records[legacy_store] = json.loads(json.dumps(legacy))
            return self.memory.create_namespace(
                config,
                encrypted,
                legacy_store,
                replacement,
            )

        with patch.object(
            store,
            "_perform_create_secret_namespace_if_missing",
            side_effect=concurrent_legacy_then_create,
        ):
            result = store.create_mailbox_secret_if_missing(
                owner_email,
                mailbox_id,
                self.VERSION_A,
                imap_password="losing-request",
            )

        self.assertEqual(result["status"], "conflict")
        self.assertNotIn(encrypted_key, self.memory.records)
        self.assertEqual(self.memory.records[legacy_key], legacy)

    def test_lost_create_ack_classifies_full_secret_namespace(self):
        lost_ack = (
            None,
            {
                "code": "mailbox_secret_store_unavailable",
                "message": "lost acknowledgement",
            },
        )
        with patch.object(
            store,
            "_perform_create_secret_namespace_if_missing",
            return_value=lost_ack,
        ):
            not_applied = store.create_mailbox_secret_if_missing(
                "owner@example.com",
                "not-applied",
                self.VERSION_A,
                imap_password="new",
            )
        self.assertEqual(not_applied["status"], "not_applied")

        legacy_key = store.build_mailbox_secret_key(
            "owner@example.com",
            "legacy-winner",
        )
        legacy_winner = {
            "v": 1,
            "mailboxId": "legacy-winner",
            "updatedAt": "2026-01-01T00:00:00Z",
            "imapPassword": "winner",
            "smtpPassword": "winner",
        }

        def concurrent_legacy_without_ack(*_args):
            self.memory.records[legacy_key] = json.loads(
                json.dumps(legacy_winner)
            )
            return lost_ack

        with patch.object(
            store,
            "_perform_create_secret_namespace_if_missing",
            side_effect=concurrent_legacy_without_ack,
        ):
            conflict = store.create_mailbox_secret_if_missing(
                "owner@example.com",
                "legacy-winner",
                self.VERSION_A,
                imap_password="loser",
            )
        self.assertEqual(conflict["status"], "conflict")
        self.assertEqual(self.memory.records[legacy_key], legacy_winner)

        def committed_without_ack(config, encrypted, legacy, replacement):
            applied, _ = self.memory.create_namespace(
                config,
                encrypted,
                legacy,
                replacement,
            )
            self.assertEqual(applied, 1)
            raise TimeoutError("lost acknowledgement")

        with patch.object(
            store,
            "_perform_create_secret_namespace_if_missing",
            side_effect=committed_without_ack,
        ):
            applied = store.create_mailbox_secret_if_missing(
                "owner@example.com",
                "committed",
                self.VERSION_A,
                imap_password="committed",
            )
        self.assertEqual(applied["status"], "applied")

    def test_conditional_replace_rejects_stale_snapshot_and_preserves_new_generation(self):
        store.create_mailbox_secret_if_missing(
            "owner@example.com",
            "demo",
            self.VERSION_A,
            imap_password="v0",
        )
        v0 = store.snapshot_encrypted_mailbox_secret(
            "owner@example.com",
            "demo",
        )
        winner = store.replace_mailbox_secret_if_unchanged(
            "owner@example.com",
            "demo",
            v0,
            self.VERSION_B,
            imap_password="winner",
        )
        self.assertEqual(winner["status"], "applied")
        key = store.build_encrypted_mailbox_secret_key(
            "owner@example.com",
            "demo",
        )
        exact_winner = json.loads(json.dumps(self.memory.records[key]))

        loser = store.replace_mailbox_secret_if_unchanged(
            "owner@example.com",
            "demo",
            v0,
            self.VERSION_C,
            imap_password="loser",
        )

        self.assertEqual(loser["status"], "conflict")
        self.assertEqual(self.memory.records[key], exact_winner)
        self.assertEqual(
            store.read_mailbox_secret("owner@example.com", "demo")["record"][
                "credentialVersion"
            ],
            self.VERSION_B,
        )

        committed_snapshot = store.snapshot_encrypted_mailbox_secret(
            "owner@example.com",
            "demo",
        )
        with patch.object(
            store,
            "_perform_compare_and_set_secret",
            side_effect=RuntimeError("unexpected transport failure"),
        ):
            not_applied = store.replace_mailbox_secret_if_unchanged(
                "owner@example.com",
                "demo",
                committed_snapshot,
                self.VERSION_C,
                imap_password="must-not-write",
            )
        self.assertEqual(not_applied["status"], "not_applied")
        self.assertEqual(
            store.read_mailbox_secret("owner@example.com", "demo")["record"][
                "credentialVersion"
            ],
            self.VERSION_B,
        )

    def test_replace_requires_present_snapshot_and_merges_only_from_that_snapshot(self):
        missing = {
            "status": "missing",
            "record": None,
            "error": None,
        }
        rejected = store.replace_mailbox_secret_if_unchanged(
            "owner@example.com",
            "demo",
            missing,
            self.VERSION_B,
            imap_password="must-not-create",
        )
        self.assertEqual(rejected["status"], "malformed")

        store.create_mailbox_secret_if_missing(
            "owner@example.com",
            "demo",
            self.VERSION_A,
            imap_password="expected-imap",
            smtp_password="expected-smtp",
        )
        expected = store.snapshot_encrypted_mailbox_secret(
            "owner@example.com",
            "demo",
        )
        with patch.object(
            store,
            "read_mailbox_secret",
            side_effect=AssertionError(
                "replacement must not merge from a separate live read"
            ),
        ):
            replaced = store.replace_mailbox_secret_if_unchanged(
                "owner@example.com",
                "demo",
                expected,
                self.VERSION_B,
                imap_password="new-imap",
            )

        self.assertEqual(replaced["status"], "applied")
        readback = store.read_mailbox_secret("owner@example.com", "demo")
        self.assertEqual(readback["record"]["imapPassword"], "new-imap")
        self.assertEqual(readback["record"]["smtpPassword"], "expected-smtp")
        self.assertEqual(
            readback["record"]["credentialVersion"],
            self.VERSION_B,
        )

    def test_generationless_encrypted_snapshot_is_migrated_by_exact_cas(self):
        nonce = b"n" * store.MAILBOX_SECRET_NONCE_BYTES
        legacy_plaintext = json.dumps(
            {
                "imapPassword": "legacy-imap",
                "smtpPassword": "legacy-smtp",
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        legacy_encrypted_record = {
            "v": store.MAILBOX_SECRET_ENCRYPTED_SCHEMA_VERSION,
            "algorithm": store.MAILBOX_SECRET_ALGORITHM,
            "nonce": store._encode_base64url(nonce),
            "ciphertext": store._encode_base64url(
                store.AESGCM(b"k" * 32).encrypt(
                    nonce,
                    legacy_plaintext,
                    store._build_associated_data(
                        "owner@example.com",
                        "demo",
                    ),
                )
            ),
            "updatedAt": "2026-01-01T00:00:00Z",
        }
        encrypted_key = store.build_encrypted_mailbox_secret_key(
            "owner@example.com",
            "demo",
        )
        self.memory.records[encrypted_key] = json.loads(
            json.dumps(legacy_encrypted_record)
        )
        expected = store.snapshot_encrypted_mailbox_secret(
            "owner@example.com",
            "demo",
        )

        migrated = store.replace_mailbox_secret_if_unchanged(
            "owner@example.com",
            "demo",
            expected,
            self.VERSION_A,
            imap_password="new-imap",
        )

        self.assertEqual(migrated["status"], "applied")
        readback = store.read_mailbox_secret("owner@example.com", "demo")
        self.assertEqual(readback["record"]["credentialVersion"], self.VERSION_A)
        self.assertEqual(readback["record"]["imapPassword"], "new-imap")
        self.assertEqual(readback["record"]["smtpPassword"], "legacy-smtp")

    def test_delete_only_removes_the_expected_current_generation(self):
        store.create_mailbox_secret_if_missing(
            "owner@example.com",
            "demo",
            self.VERSION_A,
            imap_password="winner",
        )
        key = store.build_encrypted_mailbox_secret_key(
            "owner@example.com",
            "demo",
        )
        exact_winner = json.loads(json.dumps(self.memory.records[key]))

        stale_delete = store.delete_mailbox_secret_if_current_generation(
            "owner@example.com",
            "demo",
            self.VERSION_B,
        )
        self.assertEqual(stale_delete["status"], "conflict")
        self.assertEqual(self.memory.records[key], exact_winner)

        winner_delete = store.delete_mailbox_secret_if_current_generation(
            "owner@example.com",
            "demo",
            self.VERSION_A,
        )
        self.assertEqual(winner_delete["status"], "applied")
        self.assertNotIn(key, self.memory.records)

    def test_restore_only_replaces_the_expected_current_generation(self):
        store.create_mailbox_secret_if_missing(
            "owner@example.com",
            "demo",
            self.VERSION_A,
            imap_password="v0",
        )
        v0 = store.snapshot_encrypted_mailbox_secret(
            "owner@example.com",
            "demo",
        )
        store.replace_mailbox_secret_if_unchanged(
            "owner@example.com",
            "demo",
            v0,
            self.VERSION_B,
            imap_password="request-b",
        )
        restored = store.restore_mailbox_secret_if_current_generation(
            "owner@example.com",
            "demo",
            self.VERSION_B,
            v0,
        )
        self.assertEqual(restored["status"], "applied")
        key = store.build_encrypted_mailbox_secret_key(
            "owner@example.com",
            "demo",
        )
        self.assertEqual(self.memory.records[key], v0["record"])

        current_v0 = store.snapshot_encrypted_mailbox_secret(
            "owner@example.com",
            "demo",
        )
        store.replace_mailbox_secret_if_unchanged(
            "owner@example.com",
            "demo",
            current_v0,
            self.VERSION_C,
            imap_password="newer-winner",
        )
        exact_newer = json.loads(json.dumps(self.memory.records[key]))
        stale_restore = store.restore_mailbox_secret_if_current_generation(
            "owner@example.com",
            "demo",
            self.VERSION_B,
            v0,
        )
        self.assertEqual(stale_restore["status"], "conflict")
        self.assertEqual(self.memory.records[key], exact_newer)

    def test_zero_ack_is_a_definitive_conflict_without_readback(self):
        store.create_mailbox_secret_if_missing(
            "owner@example.com",
            "replace",
            self.VERSION_A,
            imap_password="v0",
        )
        replace_snapshot = store.snapshot_encrypted_mailbox_secret(
            "owner@example.com",
            "replace",
        )
        with patch.object(
            store,
            "_perform_compare_and_set_secret",
            return_value=(0, None),
        ), patch.object(
            store,
            "snapshot_encrypted_mailbox_secret",
            side_effect=AssertionError("ACK 0 must not trigger write readback"),
        ):
            replace_result = store.replace_mailbox_secret_if_unchanged(
                "owner@example.com",
                "replace",
                replace_snapshot,
                self.VERSION_B,
                imap_password="loser",
            )
        self.assertEqual(replace_result["status"], "conflict")

        store.create_mailbox_secret_if_missing(
            "owner@example.com",
            "delete",
            self.VERSION_A,
            imap_password="current",
        )
        original_snapshot = store.snapshot_encrypted_mailbox_secret
        with patch.object(
            store,
            "_perform_compare_and_delete_secret",
            return_value=(0, None),
        ), patch.object(
            store,
            "snapshot_encrypted_mailbox_secret",
            wraps=original_snapshot,
        ) as delete_reads:
            delete_result = store.delete_mailbox_secret_if_current_generation(
                "owner@example.com",
                "delete",
                self.VERSION_A,
            )
        self.assertEqual(delete_result["status"], "conflict")
        self.assertEqual(delete_reads.call_count, 1)

        store.create_mailbox_secret_if_missing(
            "owner@example.com",
            "restore",
            self.VERSION_A,
            imap_password="prior",
        )
        previous = store.snapshot_encrypted_mailbox_secret(
            "owner@example.com",
            "restore",
        )
        store.replace_mailbox_secret_if_unchanged(
            "owner@example.com",
            "restore",
            previous,
            self.VERSION_B,
            imap_password="temporary",
        )
        with patch.object(
            store,
            "_perform_compare_and_set_secret",
            return_value=(0, None),
        ), patch.object(
            store,
            "snapshot_encrypted_mailbox_secret",
            wraps=original_snapshot,
        ) as restore_reads:
            restore_result = store.restore_mailbox_secret_if_current_generation(
                "owner@example.com",
                "restore",
                self.VERSION_B,
                previous,
            )
        self.assertEqual(restore_result["status"], "conflict")
        self.assertEqual(restore_reads.call_count, 1)

    def test_lost_write_ack_uses_exact_readback_without_a_second_write(self):
        store.create_mailbox_secret_if_missing(
            "owner@example.com",
            "demo",
            self.VERSION_A,
            imap_password="v0",
        )
        v0 = store.snapshot_encrypted_mailbox_secret(
            "owner@example.com",
            "demo",
        )
        calls = []

        def committed_without_ack(config, key, expected, replacement):
            calls.append((key, expected, json.loads(json.dumps(replacement))))
            applied, _ = self.memory.compare_and_set(
                config,
                key,
                expected,
                replacement,
            )
            self.assertEqual(applied, 1)
            raise TimeoutError("lost acknowledgement")

        with patch.object(
            store,
            "_perform_compare_and_set_secret",
            side_effect=committed_without_ack,
        ):
            result = store.replace_mailbox_secret_if_unchanged(
                "owner@example.com",
                "demo",
                v0,
                self.VERSION_B,
                imap_password="committed",
            )

        self.assertEqual(result["status"], "applied")
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            store.read_mailbox_secret("owner@example.com", "demo")["record"][
                "credentialVersion"
            ],
            self.VERSION_B,
        )

    def test_lost_write_ack_classifies_prior_concurrent_and_unavailable_readback(self):
        store.create_mailbox_secret_if_missing(
            "owner@example.com",
            "demo",
            self.VERSION_A,
            imap_password="v0",
        )
        v0 = store.snapshot_encrypted_mailbox_secret(
            "owner@example.com",
            "demo",
        )
        lost_ack = (
            None,
            {
                "code": "mailbox_secret_store_unavailable",
                "message": "lost acknowledgement",
            },
        )
        with patch.object(
            store,
            "_perform_compare_and_set_secret",
            return_value=lost_ack,
        ):
            not_committed = store.replace_mailbox_secret_if_unchanged(
                "owner@example.com",
                "demo",
                v0,
                self.VERSION_B,
                imap_password="not-committed",
            )
        self.assertEqual(not_committed["status"], "not_applied")

        store.replace_mailbox_secret_if_unchanged(
            "owner@example.com",
            "demo",
            v0,
            self.VERSION_C,
            imap_password="concurrent",
        )
        with patch.object(
            store,
            "_perform_compare_and_set_secret",
            return_value=lost_ack,
        ):
            concurrent = store.replace_mailbox_secret_if_unchanged(
                "owner@example.com",
                "demo",
                v0,
                self.VERSION_B,
                imap_password="loser",
            )
        self.assertEqual(concurrent["status"], "conflict")

        current = store.snapshot_encrypted_mailbox_secret(
            "owner@example.com",
            "demo",
        )
        with patch.object(
            store,
            "_perform_compare_and_set_secret",
            return_value=lost_ack,
        ), patch.object(
            store,
            "snapshot_encrypted_mailbox_secret",
            return_value={
                "status": "unavailable",
                "record": None,
                "error": {"code": "mailbox_secret_store_unavailable"},
            },
        ):
            ambiguous = store.replace_mailbox_secret_if_unchanged(
                "owner@example.com",
                "demo",
                current,
                self.VERSION_B,
                imap_password="ambiguous",
            )
        self.assertEqual(ambiguous["status"], "ambiguous")

    def test_lost_cleanup_ack_is_resolved_by_exact_readback(self):
        store.create_mailbox_secret_if_missing(
            "owner@example.com",
            "delete-me",
            self.VERSION_A,
            imap_password="delete",
        )

        def delete_without_ack(config, key, expected_record):
            applied, _ = self.memory.compare_and_delete(
                config,
                key,
                expected_record,
            )
            self.assertEqual(applied, 1)
            raise TimeoutError("lost acknowledgement")

        with patch.object(
            store,
            "_perform_compare_and_delete_secret",
            side_effect=delete_without_ack,
        ):
            deleted = store.delete_mailbox_secret_if_current_generation(
                "owner@example.com",
                "delete-me",
                self.VERSION_A,
            )
        self.assertEqual(deleted["status"], "applied")

        for operation in ("delete", "restore"):
            with self.subTest(concurrent_namespace=operation):
                mailbox_id = f"{operation}-legacy-race"
                store.create_mailbox_secret_if_missing(
                    "owner@example.com",
                    mailbox_id,
                    self.VERSION_A,
                    imap_password="temporary",
                )
                legacy_key = store.build_mailbox_secret_key(
                    "owner@example.com",
                    mailbox_id,
                )
                legacy_winner = {
                    "v": 1,
                    "mailboxId": mailbox_id,
                    "updatedAt": "2026-01-01T00:00:00Z",
                    "imapPassword": "winner",
                    "smtpPassword": "winner",
                }

                def cleanup_then_legacy(config, key, expected_record):
                    applied, _ = self.memory.compare_and_delete(
                        config,
                        key,
                        expected_record,
                    )
                    self.assertEqual(applied, 1)
                    self.memory.records[legacy_key] = json.loads(
                        json.dumps(legacy_winner)
                    )
                    raise TimeoutError("lost acknowledgement")

                with patch.object(
                    store,
                    "_perform_compare_and_delete_secret",
                    side_effect=cleanup_then_legacy,
                ):
                    if operation == "delete":
                        concurrent = (
                            store.delete_mailbox_secret_if_current_generation(
                                "owner@example.com",
                                mailbox_id,
                                self.VERSION_A,
                            )
                        )
                    else:
                        concurrent = (
                            store.restore_mailbox_secret_if_current_generation(
                                "owner@example.com",
                                mailbox_id,
                                self.VERSION_A,
                                {
                                    "status": "missing",
                                    "record": None,
                                    "error": None,
                                },
                            )
                        )

                self.assertEqual(concurrent["status"], "conflict")
                self.assertEqual(
                    self.memory.records[legacy_key],
                    legacy_winner,
                )

        store.create_mailbox_secret_if_missing(
            "owner@example.com",
            "restore-me",
            self.VERSION_A,
            imap_password="prior",
        )
        prior = store.snapshot_encrypted_mailbox_secret(
            "owner@example.com",
            "restore-me",
        )
        store.replace_mailbox_secret_if_unchanged(
            "owner@example.com",
            "restore-me",
            prior,
            self.VERSION_B,
            imap_password="temporary",
        )

        def restore_without_ack(config, key, expected, replacement):
            applied, _ = self.memory.compare_and_set(
                config,
                key,
                expected,
                replacement,
            )
            self.assertEqual(applied, 1)
            raise TimeoutError("lost acknowledgement")

        with patch.object(
            store,
            "_perform_compare_and_set_secret",
            side_effect=restore_without_ack,
        ):
            restored = store.restore_mailbox_secret_if_current_generation(
                "owner@example.com",
                "restore-me",
                self.VERSION_B,
                prior,
            )
        self.assertEqual(restored["status"], "applied")
        key = store.build_encrypted_mailbox_secret_key(
            "owner@example.com",
            "restore-me",
        )
        self.assertEqual(self.memory.records[key], prior["record"])

    def test_atomic_command_acknowledgement_requires_exact_integer_result(self):
        config = {"rest_url": "https://store.invalid", "rest_token": "token"}
        command = ["EVAL", "return 1", 0]
        for payload in (
            {},
            {"result": True},
            {"result": "1"},
            {"result": 2},
            {"result": 1, "extra": None},
        ):
            with self.subTest(payload=payload), patch.object(
                store,
                "_perform_rest_request",
                return_value=(payload, None),
            ):
                result, error = ORIGINAL_ATOMIC_COMMAND(config, command)
            self.assertIsNone(result)
            self.assertEqual(error["code"], "mailbox_secret_malformed")

        for result_value in (0, 1):
            with self.subTest(result=result_value), patch.object(
                store,
                "_perform_rest_request",
                return_value=({"result": result_value}, None),
            ):
                result, error = ORIGINAL_ATOMIC_COMMAND(config, command)
            self.assertEqual(result, result_value)
            self.assertIsNone(error)

    def test_generation_is_encrypted_and_malformed_values_are_rejected(self):
        generated = store.generate_mailbox_credential_version()
        self.assertTrue(store.is_valid_mailbox_credential_version(generated))
        self.assertEqual(
            len(base64.urlsafe_b64decode(f"{generated}=")),
            store.MAILBOX_CREDENTIAL_VERSION_BYTES,
        )

        result = store.create_mailbox_secret_if_missing(
            "owner@example.com",
            "demo",
            self.VERSION_A,
            imap_password="secret",
        )
        self.assertEqual(result["status"], "applied")
        key = store.build_encrypted_mailbox_secret_key(
            "owner@example.com",
            "demo",
        )
        persisted = self.memory.records[key]
        self.assertNotIn("credentialVersion", persisted)
        self.assertNotIn(self.VERSION_A, json.dumps(persisted))
        self.assertEqual(
            store.read_mailbox_secret("owner@example.com", "demo")["record"][
                "credentialVersion"
            ],
            self.VERSION_A,
        )

        for malformed in (
            None,
            "",
            "short",
            "a" * 42,
            "a" * 43,
            "a" * 44,
            "!" * 43,
            f"{self.VERSION_A[:-1]}B",
            123,
            True,
        ):
            with self.subTest(malformed=malformed):
                rejected = store.create_mailbox_secret_if_missing(
                    "owner@example.com",
                    "other",
                    malformed,
                    imap_password="secret",
                )
                self.assertEqual(rejected["status"], "malformed")


if __name__ == "__main__":
    unittest.main()
