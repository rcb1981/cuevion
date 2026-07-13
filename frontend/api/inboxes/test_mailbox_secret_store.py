import base64
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import mailbox_secret_store as store

ORIGINAL_READ_DURABLE_RECORD = store._read_durable_record


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


class MailboxSecretStoreTests(unittest.TestCase):
    def setUp(self):
        self.memory = InMemorySecretStore()
        self.patches = [
            patch.object(store, "_resolve_durable_store_config", return_value={"configured": True}),
            patch.object(store, "_read_durable_record", side_effect=self.memory.read),
            patch.object(store, "_write_durable_record", side_effect=self.memory.write),
            patch.object(store, "_delete_durable_record", side_effect=self.memory.delete),
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

    def test_legacy_read_migrates_without_deleting_or_overwriting_legacy(self):
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
        self.assertIn(encrypted_key, self.memory.records)
        self.assertNotIn("legacy-imap", json.dumps(self.memory.records[encrypted_key]))

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
        self.memory.records[legacy_key] = json.loads(json.dumps(legacy))

        missing_snapshot = store.snapshot_encrypted_mailbox_secret(
            "owner@example.com",
            "demo",
        )
        self.assertEqual(missing_snapshot["status"], "missing")
        store.save_mailbox_secret(
            "owner@example.com",
            "demo",
            imap_password="new",
            smtp_password="new",
        )
        self.assertIsNone(
            store.restore_encrypted_mailbox_secret_snapshot(
                "owner@example.com",
                "demo",
                missing_snapshot,
            )
        )
        encrypted_key = store.build_encrypted_mailbox_secret_key(
            "owner@example.com",
            "demo",
        )
        self.assertNotIn(encrypted_key, self.memory.records)
        self.assertEqual(self.memory.records[legacy_key], legacy)

        store.save_mailbox_secret(
            "owner@example.com",
            "demo",
            imap_password="before",
            smtp_password="before",
        )
        present_snapshot = store.snapshot_encrypted_mailbox_secret(
            "owner@example.com",
            "demo",
        )
        exact_before = json.loads(json.dumps(present_snapshot["record"]))
        store.save_mailbox_secret(
            "owner@example.com",
            "demo",
            imap_password="after",
            smtp_password="after",
        )
        self.assertIsNone(
            store.restore_encrypted_mailbox_secret_snapshot(
                "owner@example.com",
                "demo",
                present_snapshot,
            )
        )
        self.assertEqual(self.memory.records[encrypted_key], exact_before)


if __name__ == "__main__":
    unittest.main()
