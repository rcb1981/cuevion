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
ORIGINAL_DELETE_DURABLE_RECORD_WITH_OUTCOME = (
    store._delete_durable_record_with_outcome
)


def encoded_key(byte=b"k"):
    return base64.urlsafe_b64encode(byte * 32).decode().rstrip("=")


class InMemorySecretStore:
    def __init__(self):
        self.records = {}
        self.writes = []
        self.cleanup_deletes = []
        self.cleanup_delete_error = None

    def read(self, _config, key):
        return self.records.get(key), None

    def write(self, _config, key, record):
        self.records[key] = json.loads(json.dumps(record))
        self.writes.append((key, json.loads(json.dumps(record))))
        return None, None

    def delete(self, _config, key):
        self.records.pop(key, None)
        return None

    def delete_with_outcome(self, _config, key):
        self.cleanup_deletes.append(key)
        if self.cleanup_delete_error:
            return None, self.cleanup_delete_error
        if key in self.records:
            self.records.pop(key)
            return "deleted", None
        return "already_absent", None


class MailboxSecretStoreTests(unittest.TestCase):
    def setUp(self):
        self.memory = InMemorySecretStore()
        self.patches = [
            patch.object(store, "_resolve_durable_store_config", return_value={"configured": True}),
            patch.object(store, "_read_durable_record", side_effect=self.memory.read),
            patch.object(store, "_write_durable_record", side_effect=self.memory.write),
            patch.object(store, "_delete_durable_record", side_effect=self.memory.delete),
            patch.object(
                store,
                "_delete_durable_record_with_outcome",
                side_effect=self.memory.delete_with_outcome,
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

    def _save_v2(
        self,
        owner="owner@example.com",
        mailbox_id="demo",
        imap_password="imap-secret",
        smtp_password="smtp-secret",
    ):
        saved, error = store.save_mailbox_secret(
            owner,
            mailbox_id,
            imap_password=imap_password,
            smtp_password=smtp_password,
        )
        self.assertIsNone(error)
        self.assertIsNotNone(saved)
        return store.build_encrypted_mailbox_secret_key(owner, mailbox_id)

    def _put_v1(
        self,
        owner="owner@example.com",
        mailbox_id="demo",
        imap_password="legacy-imap",
        smtp_password="legacy-smtp",
    ):
        key = store.build_mailbox_secret_key(owner, mailbox_id)
        self.memory.records[key] = {
            "v": 1,
            "mailboxId": mailbox_id,
            "updatedAt": "2026-01-01T00:00:00Z",
            "imapPassword": imap_password,
            "smtpPassword": smtp_password,
        }
        return key

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

    def test_cleanup_delete_outcome_accepts_only_exact_single_key_results(self):
        cases = (
            ({"result": 1}, ("deleted", None)),
            ({"result": 0}, ("already_absent", None)),
        )
        for payload, expected in cases:
            with self.subTest(payload=payload), patch.object(
                store,
                "_perform_rest_request",
                return_value=(payload, None),
            ):
                self.assertEqual(
                    ORIGINAL_DELETE_DURABLE_RECORD_WITH_OUTCOME(
                        {"configured": True},
                        "exact-key",
                    ),
                    expected,
                )

        for payload in ({}, {"result": True}, {"result": 2}, ["unexpected"]):
            with self.subTest(payload=payload), patch.object(
                store,
                "_perform_rest_request",
                return_value=(payload, None),
            ):
                outcome, error = ORIGINAL_DELETE_DURABLE_RECORD_WITH_OUTCOME(
                    {"configured": True},
                    "exact-key",
                )
                self.assertIsNone(outcome)
                self.assertEqual(error["code"], "mailbox_secret_store_unavailable")

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

    def test_cleanup_valid_v2_deletes_only_matching_v1_and_preserves_every_v2(self):
        encrypted_key = self._save_v2()
        legacy_key = self._put_v1()
        other_encrypted_key = self._save_v2(
            mailbox_id="other",
            imap_password="other-imap",
            smtp_password="other-smtp",
        )
        other_legacy_key = self._put_v1(
            mailbox_id="other",
            imap_password="other-legacy-imap",
            smtp_password="other-legacy-smtp",
        )
        encrypted_before = json.loads(json.dumps(self.memory.records[encrypted_key]))
        other_encrypted_before = json.loads(
            json.dumps(self.memory.records[other_encrypted_key])
        )
        other_legacy_before = json.loads(json.dumps(self.memory.records[other_legacy_key]))

        result = store.cleanup_legacy_mailbox_secret_v1(
            "Owner@Example.com",
            "demo",
            False,
        )

        self.assertEqual(result, {"status": "deleted", "error": None})
        self.assertNotIn(legacy_key, self.memory.records)
        self.assertEqual(self.memory.records[encrypted_key], encrypted_before)
        self.assertEqual(self.memory.records[other_encrypted_key], other_encrypted_before)
        self.assertEqual(self.memory.records[other_legacy_key], other_legacy_before)
        self.assertEqual(self.memory.cleanup_deletes, [legacy_key])
        serialized_result = json.dumps(result)
        for forbidden in (
            "imap-secret",
            "smtp-secret",
            "legacy-imap",
            "legacy-smtp",
            encrypted_before["nonce"],
            encrypted_before["ciphertext"],
            encoded_key(),
        ):
            self.assertNotIn(forbidden, serialized_result)

    def test_cleanup_already_absent_is_idempotent(self):
        encrypted_key = self._save_v2()
        encrypted_before = json.loads(json.dumps(self.memory.records[encrypted_key]))

        first = store.cleanup_legacy_mailbox_secret_v1(
            "owner@example.com",
            "demo",
            False,
        )
        second = store.cleanup_legacy_mailbox_secret_v1(
            "owner@example.com",
            "demo",
            False,
        )

        self.assertEqual(first["status"], "already_absent")
        self.assertEqual(second["status"], "already_absent")
        self.assertEqual(self.memory.records[encrypted_key], encrypted_before)

    def test_cleanup_missing_v2_never_deletes_v1(self):
        legacy_key = self._put_v1()

        result = store.cleanup_legacy_mailbox_secret_v1(
            "owner@example.com",
            "demo",
            False,
        )

        self.assertEqual(result["status"], "v2_missing")
        self.assertIn(legacy_key, self.memory.records)
        self.assertEqual(self.memory.cleanup_deletes, [])

    def test_cleanup_rejects_non_object_unknown_and_malformed_v2_records(self):
        encrypted_key = store.build_encrypted_mailbox_secret_key(
            "owner@example.com",
            "demo",
        )
        legacy_key = self._put_v1()
        invalid_records = (
            "string-v2",
            ["list-v2"],
            {"arbitrary": "dictionary"},
            {
                "v": 99,
                "algorithm": "AES-256-GCM",
                "nonce": "AAAA",
                "ciphertext": "AAAA",
                "updatedAt": "2026-01-01T00:00:00Z",
            },
            {
                "v": 2,
                "algorithm": "AES-128-GCM",
                "nonce": "AAAA",
                "ciphertext": "AAAA",
                "updatedAt": "2026-01-01T00:00:00Z",
            },
            {
                "v": 2,
                "algorithm": "AES-256-GCM",
                "nonce": "AAAA",
                "updatedAt": "2026-01-01T00:00:00Z",
            },
        )

        for record in invalid_records:
            with self.subTest(record=record):
                self.memory.records[encrypted_key] = json.loads(json.dumps(record))
                result = store.cleanup_legacy_mailbox_secret_v1(
                    "owner@example.com",
                    "demo",
                    False,
                )
                self.assertEqual(result["status"], "v2_malformed")
                self.assertEqual(
                    result["error"]["code"],
                    "mailbox_secret_v2_malformed",
                )
                self.assertIn(legacy_key, self.memory.records)
                self.assertEqual(self.memory.records[encrypted_key], record)
                self.assertEqual(self.memory.cleanup_deletes, [])

    def test_cleanup_wrong_key_and_corrupt_ciphertext_never_delete_v1(self):
        encrypted_key = self._save_v2()
        legacy_key = self._put_v1()
        encrypted_before = json.loads(json.dumps(self.memory.records[encrypted_key]))

        with patch.dict(
            os.environ,
            {store.MAILBOX_SECRET_ENCRYPTION_KEY_ENV: encoded_key(b"j")},
            clear=True,
        ):
            wrong_key = store.cleanup_legacy_mailbox_secret_v1(
                "owner@example.com",
                "demo",
                False,
            )
        self.assertEqual(wrong_key["status"], "v2_decryption_failed")
        self.assertEqual(
            wrong_key["error"]["code"],
            "mailbox_secret_v2_decryption_failed",
        )
        self.assertIn(legacy_key, self.memory.records)
        self.assertEqual(self.memory.records[encrypted_key], encrypted_before)

        first_character = encrypted_before["ciphertext"][0]
        self.memory.records[encrypted_key]["ciphertext"] = (
            ("A" if first_character != "A" else "B")
            + encrypted_before["ciphertext"][1:]
        )
        corrupt_before = json.loads(json.dumps(self.memory.records[encrypted_key]))
        corrupt = store.cleanup_legacy_mailbox_secret_v1(
            "owner@example.com",
            "demo",
            False,
        )
        self.assertEqual(corrupt["status"], "v2_decryption_failed")
        self.assertEqual(
            corrupt["error"]["code"],
            "mailbox_secret_v2_decryption_failed",
        )
        self.assertIn(legacy_key, self.memory.records)
        self.assertEqual(self.memory.records[encrypted_key], corrupt_before)
        self.assertEqual(self.memory.cleanup_deletes, [])

    def test_cleanup_malformed_nonce_and_ciphertext_encoding_never_delete_v1(self):
        encrypted_key = self._save_v2()
        legacy_key = self._put_v1()
        valid_record = json.loads(json.dumps(self.memory.records[encrypted_key]))
        malformed_values = (
            ("nonce", "***"),
            ("ciphertext", "***"),
            ("nonce", "AAAA"),
            ("ciphertext", "AAAA"),
        )

        for field, value in malformed_values:
            with self.subTest(field=field, value=value):
                malformed_record = json.loads(json.dumps(valid_record))
                malformed_record[field] = value
                self.memory.records[encrypted_key] = malformed_record
                result = store.cleanup_legacy_mailbox_secret_v1(
                    "owner@example.com",
                    "demo",
                    False,
                )
                self.assertEqual(result["status"], "v2_malformed")
                self.assertEqual(
                    result["error"]["code"],
                    "mailbox_secret_v2_malformed",
                )
                self.assertIn(legacy_key, self.memory.records)
                self.assertEqual(self.memory.records[encrypted_key], malformed_record)

        self.assertEqual(self.memory.cleanup_deletes, [])

    def test_cleanup_missing_or_invalid_encryption_key_never_deletes_v1(self):
        encrypted_key = self._save_v2()
        legacy_key = self._put_v1()
        encrypted_before = json.loads(json.dumps(self.memory.records[encrypted_key]))

        for environment in ({}, {store.MAILBOX_SECRET_ENCRYPTION_KEY_ENV: "bad"}):
            with self.subTest(environment=environment), patch.dict(
                os.environ,
                environment,
                clear=True,
            ):
                result = store.cleanup_legacy_mailbox_secret_v1(
                    "owner@example.com",
                    "demo",
                    False,
                )
                self.assertEqual(result["status"], "encryption_unavailable")
                self.assertEqual(
                    result["error"]["code"],
                    "mailbox_secret_encryption_unavailable",
                )
                self.assertIn(legacy_key, self.memory.records)
                self.assertEqual(self.memory.records[encrypted_key], encrypted_before)

        self.assertEqual(self.memory.cleanup_deletes, [])

    def test_cleanup_aad_owner_and_mailbox_mismatches_never_delete_v1(self):
        source_key = self._save_v2()
        source_record = json.loads(json.dumps(self.memory.records[source_key]))

        cases = (
            ("other@example.com", "demo"),
            ("owner@example.com", "other"),
        )
        for owner, mailbox_id in cases:
            with self.subTest(owner=owner, mailbox_id=mailbox_id):
                encrypted_key = store.build_encrypted_mailbox_secret_key(
                    owner,
                    mailbox_id,
                )
                legacy_key = self._put_v1(owner=owner, mailbox_id=mailbox_id)
                self.memory.records[encrypted_key] = json.loads(
                    json.dumps(source_record)
                )
                result = store.cleanup_legacy_mailbox_secret_v1(
                    owner,
                    mailbox_id,
                    False,
                )
                self.assertEqual(result["status"], "v2_decryption_failed")
                self.assertEqual(
                    result["error"]["code"],
                    "mailbox_secret_v2_decryption_failed",
                )
                self.assertIn(legacy_key, self.memory.records)
                self.assertEqual(self.memory.records[encrypted_key], source_record)

        self.assertEqual(self.memory.cleanup_deletes, [])

    def test_cleanup_enforces_secret_requirements_for_credential_mode(self):
        encrypted_key = store.build_encrypted_mailbox_secret_key(
            "owner@example.com",
            "demo",
        )
        legacy_key = self._put_v1()

        cases = (
            ("", "smtp-secret", False, "v2_unusable"),
            ("imap-secret", "", False, "v2_unusable"),
            ("imap-secret", "", True, "deleted"),
        )
        for imap_password, smtp_password, use_same, expected in cases:
            with self.subTest(use_same=use_same, expected=expected):
                self.memory.records[legacy_key] = {
                    "v": 1,
                    "mailboxId": "demo",
                    "updatedAt": "2026-01-01T00:00:00Z",
                    "imapPassword": "legacy-imap",
                    "smtpPassword": "legacy-smtp",
                }
                self.memory.records[encrypted_key] = store._encrypt_secret_record(
                    b"k" * 32,
                    "owner@example.com",
                    "demo",
                    {
                        "imapPassword": imap_password,
                        "smtpPassword": smtp_password,
                        "updatedAt": "2026-01-01T00:00:00Z",
                    },
                )
                result = store.cleanup_legacy_mailbox_secret_v1(
                    "owner@example.com",
                    "demo",
                    use_same,
                )
                self.assertEqual(result["status"], expected)
                if expected == "deleted":
                    self.assertNotIn(legacy_key, self.memory.records)
                else:
                    self.assertIn(legacy_key, self.memory.records)

    def test_cleanup_read_and_delete_outages_fail_closed(self):
        self._save_v2()
        legacy_key = self._put_v1()
        outage = {
            "code": "mailbox_secret_store_unavailable",
            "message": "offline",
        }

        with patch.object(
            store,
            "_read_durable_record",
            return_value=(None, outage),
        ):
            read_failure = store.cleanup_legacy_mailbox_secret_v1(
                "owner@example.com",
                "demo",
                False,
            )
        self.assertEqual(read_failure["status"], "storage_unavailable")
        self.assertIn(legacy_key, self.memory.records)

        self.memory.cleanup_delete_error = outage
        delete_failure = store.cleanup_legacy_mailbox_secret_v1(
            "owner@example.com",
            "demo",
            False,
        )
        self.assertEqual(delete_failure["status"], "delete_failed")
        self.assertIn(legacy_key, self.memory.records)


if __name__ == "__main__":
    unittest.main()
