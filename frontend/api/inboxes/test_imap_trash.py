from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


CURRENT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = CURRENT_DIR.parent.parent
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

from api.inboxes import imap_trash


_DEFAULT = object()
SOURCE_FOLDER = "INBOX"
SOURCE_UID = "123"
SOURCE_UID_VALIDITY = "456"
TRASH_FOLDER = "Deleted Items"
TARGET_UID = "900"
TARGET_UID_VALIDITY = "789"
FINGERPRINT = "a" * 64
RFC_MESSAGE_ID = "message@example.test"


def identity_result(
    *,
    folder: str,
    uid: str,
    uid_validity: str,
    fingerprint: str = FINGERPRINT,
    rfc_message_id: str | None = RFC_MESSAGE_ID,
) -> dict:
    return {
        "ok": True,
        "status": "ok",
        "identity": {
            "providerFolder": folder,
            "imapUid": uid,
            "uidValidity": uid_validity,
            "fingerprint": fingerprint,
            "rfcMessageId": rfc_message_id,
        },
        "error": None,
    }


def identity_failure(code: str) -> dict:
    return {
        "ok": False,
        "status": "error",
        "identity": None,
        "error": {
            "code": code,
            "message": "provider detail must not escape",
            "stage": "provider",
        },
    }


class RecordingMailbox:
    def __init__(
        self,
        *,
        list_response=_DEFAULT,
        capability_response=_DEFAULT,
        move_response=_DEFAULT,
        stale_copyuid_responses: list[object] | None = None,
        post_copyuid_response=_DEFAULT,
        select_response=_DEFAULT,
        uid_validity_response=_DEFAULT,
        source_search_response=_DEFAULT,
        stale_copyuid_never_ends: bool = False,
    ):
        self.list_response = (
            (
                "OK",
                [br'(\HasNoChildren \Trash) "/" "Deleted Items"'],
            )
            if list_response is _DEFAULT
            else list_response
        )
        self.capability_response = (
            ("OK", [b"IMAP4rev1 MOVE UIDPLUS"])
            if capability_response is _DEFAULT
            else capability_response
        )
        self.move_response = (
            (
                "OK",
                [b"[COPYUID 789 123 900] MOVE completed"],
            )
            if move_response is _DEFAULT
            else move_response
        )
        self.stale_copyuid_responses = list(stale_copyuid_responses or [])
        self.post_copyuid_response = (
            ("COPYUID", [None])
            if post_copyuid_response is _DEFAULT
            else post_copyuid_response
        )
        self.select_response = (
            ("OK", [b"0"])
            if select_response is _DEFAULT
            else select_response
        )
        self.uid_validity_response = (
            ("UIDVALIDITY", [b"456"])
            if uid_validity_response is _DEFAULT
            else uid_validity_response
        )
        self.source_search_response = (
            ("OK", [b""])
            if source_search_response is _DEFAULT
            else source_search_response
        )
        self.stale_copyuid_never_ends = stale_copyuid_never_ends
        self.move_called = False
        self.post_copyuid_consumed = False
        self.events: list[str] = []
        self.list_calls = 0
        self.capability_calls = 0
        self.response_calls: list[str] = []
        self.select_calls: list[str] = []
        self.uid_calls: list[tuple] = []
        self.unsafe_calls: list[tuple] = []

    @staticmethod
    def _resolve(value):
        if isinstance(value, BaseException):
            raise value
        return value

    def list(self):
        self.events.append("discovery")
        self.list_calls += 1
        return self._resolve(self.list_response)

    def capability(self):
        self.events.append("capability")
        self.capability_calls += 1
        return self._resolve(self.capability_response)

    def response(self, name):
        self.response_calls.append(name)
        if name == "UIDVALIDITY":
            self.events.append("source_uid_validity")
            return self._resolve(self.uid_validity_response)
        if name != "COPYUID":
            raise AssertionError(f"unexpected response code: {name}")

        if not self.move_called:
            self.events.append("copyuid_drain")
            if self.stale_copyuid_never_ends:
                return "COPYUID", [b"700 12 88"]
            if self.stale_copyuid_responses:
                return self._resolve(self.stale_copyuid_responses.pop(0))
            return "COPYUID", [None]

        self.events.append("copyuid_readback")
        if not self.post_copyuid_consumed:
            self.post_copyuid_consumed = True
            return self._resolve(self.post_copyuid_response)
        return "COPYUID", [None]

    def select(self, folder):
        self.events.append("source_reselect")
        self.select_calls.append(folder)
        return self._resolve(self.select_response)

    def uid(self, command, *arguments):
        call = (command, *arguments)
        self.uid_calls.append(call)
        normalized = command.casefold()
        if normalized == "move":
            self.events.append("move")
            self.move_called = True
            return self._resolve(self.move_response)
        if normalized == "search":
            self.events.append("source_absence")
            return self._resolve(self.source_search_response)
        self.unsafe_calls.append(call)
        raise AssertionError(f"unexpected UID command: {command}")

    def search(self, *arguments):
        self.unsafe_calls.append(("sequence_search", *arguments))
        raise AssertionError("sequence-number SEARCH must not be used")

    def copy(self, *arguments):
        self.unsafe_calls.append(("copy", *arguments))
        raise AssertionError("COPY fallback must not be used")

    def store(self, *arguments):
        self.unsafe_calls.append(("store", *arguments))
        raise AssertionError("STORE fallback must not be used")

    def expunge(self, *arguments):
        self.unsafe_calls.append(("expunge", *arguments))
        raise AssertionError("EXPUNGE fallback must not be used")


class AttributeCapabilityMailbox(RecordingMailbox):
    capability = None

    def __init__(self, capabilities, **kwargs):
        super().__init__(**kwargs)
        self.capabilities = capabilities


def run_trash(
    mailbox: RecordingMailbox | None = None,
    *,
    source_folder: str = SOURCE_FOLDER,
    uid: str = SOURCE_UID,
    expected_uid_validity: str = SOURCE_UID_VALIDITY,
    trash_folder: str = TRASH_FOLDER,
    source_result: object = _DEFAULT,
    target_result: object = _DEFAULT,
):
    mailbox = mailbox or RecordingMailbox()
    source_result = (
        identity_result(
            folder=source_folder,
            uid=uid,
            uid_validity=expected_uid_validity,
        )
        if source_result is _DEFAULT
        else source_result
    )
    target_result = (
        identity_result(
            folder=trash_folder,
            uid=TARGET_UID,
            uid_validity=TARGET_UID_VALIDITY,
        )
        if target_result is _DEFAULT
        else target_result
    )
    identity_calls: list[dict] = []

    def read_identity(mailbox_client, **kwargs):
        if mailbox_client is not mailbox:
            raise AssertionError("wrong mailbox client")
        identity_calls.append(dict(kwargs))
        if len(identity_calls) == 1:
            mailbox.events.append("source_identity")
            return RecordingMailbox._resolve(source_result)
        if len(identity_calls) == 2:
            mailbox.events.append("target_identity")
            return RecordingMailbox._resolve(target_result)
        raise AssertionError("unexpected extra identity read")

    with patch.object(
        imap_trash,
        "read_imap_message_identity",
        side_effect=read_identity,
    ):
        result = imap_trash.trash_imap_message(
            mailbox,
            source_folder=source_folder,
            uid=uid,
            expected_uid_validity=expected_uid_validity,
        )
    return result, mailbox, identity_calls


def error_code(result: dict) -> str:
    return result["error"]["code"]


def error_stage(result: dict) -> str:
    return result["error"]["stage"]


class ImapTrashDiscoveryTests(unittest.TestCase):
    def test_unique_selectable_trash_role_is_case_insensitive(self):
        mailbox = RecordingMailbox(
            list_response=(
                "OK",
                [
                    r'(\HasNoChildren) "/" "Trash"',
                    r'(\Marked \tRaSh) "/" "Deleted Mail"',
                ],
            )
        )
        self.assertEqual(
            imap_trash.discover_trash_folder(mailbox),
            ("Deleted Mail", None),
        )

    def test_noselect_and_nonexistent_trash_are_never_targets(self):
        mailbox = RecordingMailbox(
            list_response=(
                "OK",
                [
                    r'(\Trash \Noselect) "/" "Container"',
                    r'(\Trash \NonExistent) "/" "Gone"',
                    r'(\Trash \HasNoChildren) "/" "Selectable"',
                ],
            )
        )
        self.assertEqual(
            imap_trash.discover_trash_folder(mailbox),
            ("Selectable", None),
        )

    def test_no_role_has_no_name_guessing_fallback(self):
        for name in ("Trash", "Deleted", "Bin", "Prullenbak", "INBOX.Trash"):
            with self.subTest(name=name):
                mailbox = RecordingMailbox(
                    list_response=(
                        "OK",
                        [f'(\\HasNoChildren) "/" "{name}"'],
                    )
                )
                self.assertEqual(
                    imap_trash.discover_trash_folder(mailbox),
                    (None, "trash_folder_unavailable"),
                )

    def test_conflicting_special_use_role_is_unavailable(self):
        for role in (
            r"\Archive",
            r"\All",
            r"\Drafts",
            r"\Flagged",
            r"\Important",
            r"\Inbox",
            r"\Junk",
            r"\Sent",
        ):
            with self.subTest(role=role):
                mailbox = RecordingMailbox(
                    list_response=(
                        "OK",
                        [f'(\\Trash {role}) "/" "Unsafe"'],
                    )
                )
                self.assertEqual(
                    imap_trash.discover_trash_folder(mailbox),
                    (None, "trash_folder_unavailable"),
                )

    def test_only_noselect_or_nonexistent_trash_is_unavailable(self):
        for attributes in (r"\Trash \Noselect", r"\Trash \NonExistent"):
            with self.subTest(attributes=attributes):
                mailbox = RecordingMailbox(
                    list_response=("OK", [f'({attributes}) "/" "Unsafe"'])
                )
                self.assertEqual(
                    imap_trash.discover_trash_folder(mailbox),
                    (None, "trash_folder_unavailable"),
                )

    def test_inbox_is_never_accepted_as_trash(self):
        for name in ("INBOX", "Inbox", "inbox"):
            with self.subTest(name=name):
                mailbox = RecordingMailbox(
                    list_response=("OK", [f'(\\Trash) "/" "{name}"'])
                )
                self.assertEqual(
                    imap_trash.discover_trash_folder(mailbox),
                    (None, "trash_folder_unavailable"),
                )

    def test_multiple_selectable_roles_are_ambiguous_even_when_names_repeat(self):
        for rows in (
            [r'(\Trash) "/" "A"', r'(\Trash) "/" "B"'],
            [r'(\Trash) "/" "Same"', r'(\Trash) "/" "Same"'],
        ):
            with self.subTest(rows=rows):
                mailbox = RecordingMailbox(list_response=("OK", rows))
                self.assertEqual(
                    imap_trash.discover_trash_folder(mailbox),
                    (None, "trash_folder_ambiguous"),
                )

    def test_unsafe_roles_are_filtered_before_safe_candidate_cardinality(self):
        mailbox = RecordingMailbox(
            list_response=(
                "OK",
                [
                    r'(\Trash \Archive) "/" "Conflict"',
                    r'(\Trash) "/" "Valid"',
                    r'(\Trash) "/" "INBOX"',
                ],
            )
        )
        self.assertEqual(
            imap_trash.discover_trash_folder(mailbox),
            ("Valid", None),
        )

    def test_literal_and_modified_utf7_mailbox_name_is_preserved_opaque(self):
        literal = b"&AMk-l&AOk-ments"
        mailbox = RecordingMailbox(
            list_response=(
                "OK",
                [
                    (
                        f'(\\Trash) "/" {{{len(literal)}}}'.encode(),
                        literal,
                    ),
                    b"",
                ],
            )
        )
        self.assertEqual(
            imap_trash.discover_trash_folder(mailbox),
            (literal.decode("ascii"), None),
        )

    def test_malformed_non_ok_or_exception_fails_closed(self):
        for response in (
            ("NO", [b"private provider detail"]),
            ("OK", None),
            ("OK", [None]),
            ("OK", [b"\xff"]),
            RuntimeError("password=provider-secret"),
        ):
            with self.subTest(response=response):
                mailbox = RecordingMailbox(list_response=response)
                result = imap_trash.discover_trash_folder(mailbox)
                self.assertEqual(result, (None, "trash_folder_unavailable"))
                self.assertNotIn("provider-secret", json.dumps(result))
                self.assertNotIn("private provider detail", json.dumps(result))


class ImapTrashInputAndCapabilityTests(unittest.TestCase):
    def test_invalid_inputs_stop_before_provider_calls(self):
        cases = (
            ({"source_folder": " INBOX"}, "invalid_source_folder"),
            ({"source_folder": "IN\nBOX"}, "invalid_source_folder"),
            ({"source_folder": "Inbox"}, "invalid_source_folder"),
            ({"source_folder": "Archive"}, "invalid_source_folder"),
            ({"uid": "0"}, "invalid_imap_uid"),
            ({"uid": "1:2"}, "invalid_imap_uid"),
            ({"uid": "4294967296"}, "invalid_imap_uid"),
            ({"expected_uid_validity": "01"}, "invalid_uid_validity"),
            ({"expected_uid_validity": None}, "invalid_uid_validity"),
        )
        for override, expected_code in cases:
            with self.subTest(override=override):
                mailbox = RecordingMailbox()
                arguments = {
                    "source_folder": SOURCE_FOLDER,
                    "uid": SOURCE_UID,
                    "expected_uid_validity": SOURCE_UID_VALIDITY,
                    **override,
                }
                result = imap_trash.trash_imap_message(mailbox, **arguments)
                self.assertEqual(error_code(result), expected_code)
                self.assertEqual(error_stage(result), "input_validation")
                self.assertEqual(mailbox.events, [])

    def test_move_and_uidplus_are_both_required_before_identity_or_mutation(self):
        for capabilities in (
            b"IMAP4rev1",
            b"IMAP4rev1 MOVE",
            b"IMAP4rev1 UIDPLUS",
        ):
            with self.subTest(capabilities=capabilities):
                mailbox = RecordingMailbox(
                    capability_response=("OK", [capabilities])
                )
                result, mailbox, identity_calls = run_trash(mailbox)
                expected_code = (
                    "trash_uidplus_unsupported"
                    if capabilities == b"IMAP4rev1 MOVE"
                    else "trash_move_unsupported"
                )
                self.assertEqual(error_code(result), expected_code)
                self.assertEqual(error_stage(result), "move_capability")
                self.assertEqual(identity_calls, [])
                self.assertEqual(mailbox.uid_calls, [])

    def test_fresh_malformed_capability_does_not_use_stale_attribute(self):
        mailbox = RecordingMailbox(
            capability_response=("NO", [b"MOVE UIDPLUS"])
        )
        mailbox.capabilities = ("MOVE", "UIDPLUS")
        result, mailbox, _ = run_trash(mailbox)
        self.assertEqual(error_code(result), "trash_move_unsupported")
        self.assertEqual(mailbox.uid_calls, [])

    def test_attribute_capabilities_are_accepted_only_when_method_is_absent(self):
        mailbox = AttributeCapabilityMailbox((b"IMAP4rev1", "mOvE", b"UIDPLUS"))
        result, mailbox, _ = run_trash(mailbox)
        self.assertTrue(result["ok"])
        self.assertEqual(mailbox.capability_calls, 0)

    def test_discovery_rejects_source_inbox_even_with_trash_role(self):
        mailbox = RecordingMailbox(
            list_response=("OK", [r'(\Trash) "/" "inbox"'])
        )
        result, mailbox, identity_calls = run_trash(mailbox)
        self.assertEqual(error_code(result), "trash_folder_unavailable")
        self.assertEqual(error_stage(result), "trash_discovery")
        self.assertEqual(identity_calls, [])
        self.assertEqual(mailbox.uid_calls, [])


class ImapTrashCopyUidTests(unittest.TestCase):
    def test_tagged_copyuid_success_returns_exact_public_identity(self):
        result, mailbox, identity_calls = run_trash()
        self.assertEqual(
            result,
            {
                "ok": True,
                "status": "ok",
                "source_folder": "INBOX",
                "source_uid": "123",
                "source_uid_validity": "456",
                "trash_folder": "Deleted Items",
                "target_uid": "900",
                "target_uid_validity": "789",
                "confirmation": "exact_target_verified",
                "error": None,
            },
        )
        self.assertEqual(
            identity_calls,
            [
                {
                    "folder": "INBOX",
                    "uid": "123",
                    "expected_uid_validity": "456",
                },
                {
                    "folder": "Deleted Items",
                    "uid": "900",
                    "expected_uid_validity": "789",
                },
            ],
        )
        self.assertEqual(
            [call for call in mailbox.uid_calls if call[0] == "MOVE"],
            [("MOVE", "123", '"Deleted Items"')],
        )
        self.assertEqual(
            [call for call in mailbox.uid_calls if call[0] == "SEARCH"],
            [("SEARCH", None, "UID", "123")],
        )
        self.assertEqual(mailbox.unsafe_calls, [])
        self.assertNotIn(FINGERPRINT, json.dumps(result))

    def test_only_leading_tagged_copyuid_response_code_is_authoritative(self):
        accepted = RecordingMailbox(
            move_response=(
                "OK",
                [b"[COPYUID 789 123 900] MOVE completed"],
            )
        )
        result, _, _ = run_trash(accepted)
        self.assertTrue(result["ok"])

        for text in (
            b"MOVE completed [COPYUID 789 123 900]",
            b"prefix[COPYUID 789 123 900]",
            b"[COPYUID 789 123 900]suffix",
        ):
            with self.subTest(text=text):
                mailbox = RecordingMailbox(move_response=("OK", [text]))
                result, _, _ = run_trash(mailbox)
                self.assertEqual(error_code(result), "trash_move_unconfirmed")
                self.assertEqual(error_stage(result), "copyuid")

    def test_untagged_copyuid_success_is_accepted(self):
        mailbox = RecordingMailbox(
            move_response=("OK", [b"MOVE completed"]),
            post_copyuid_response=("COPYUID", [b"789 123 900"]),
        )
        result, _, _ = run_trash(mailbox)
        self.assertTrue(result["ok"])

    def test_real_imaplib_empty_copyuid_shape_allows_tagged_success(self):
        mailbox = RecordingMailbox(
            post_copyuid_response=("COPYUID", [None])
        )
        result, mailbox, _ = run_trash(mailbox)
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(mailbox.response_calls.count("COPYUID"), 2)

    def test_identical_tagged_and_untagged_evidence_is_accepted(self):
        mailbox = RecordingMailbox(
            post_copyuid_response=("COPYUID", [b"789 123 900"])
        )
        result, _, _ = run_trash(mailbox)
        self.assertTrue(result["ok"])

    def test_conflicting_tagged_and_untagged_evidence_is_unconfirmed(self):
        mailbox = RecordingMailbox(
            post_copyuid_response=("COPYUID", [b"789 123 901"])
        )
        result, mailbox, calls = run_trash(mailbox)
        self.assertEqual(error_code(result), "trash_move_unconfirmed")
        self.assertEqual(error_stage(result), "copyuid")
        self.assertEqual(len(calls), 1)
        self.assertEqual(mailbox.events.count("move"), 1)
        self.assertNotIn("source_reselect", mailbox.events)

    def test_missing_current_evidence_does_not_reuse_stale_copyuid(self):
        mailbox = RecordingMailbox(
            move_response=("OK", [b"MOVE completed"]),
            stale_copyuid_responses=[
                ("COPYUID", [b"700 123 800"]),
            ],
        )
        result, mailbox, _ = run_trash(mailbox)
        self.assertEqual(error_code(result), "trash_move_unconfirmed")
        self.assertEqual(error_stage(result), "copyuid")
        self.assertEqual(mailbox.events.count("copyuid_drain"), 2)
        self.assertEqual(mailbox.events.count("copyuid_readback"), 1)

    def test_stale_copyuid_is_drained_before_valid_current_evidence(self):
        mailbox = RecordingMailbox(
            stale_copyuid_responses=[
                ("COPYUID", [b"700 12 88"]),
                ("COPYUID", [b"701 13 89"]),
            ],
        )
        result, mailbox, _ = run_trash(mailbox)
        self.assertTrue(result["ok"])
        self.assertEqual(mailbox.events.count("copyuid_drain"), 3)
        self.assertLess(
            mailbox.events.index("copyuid_drain"),
            mailbox.events.index("move"),
        )

    def test_unbounded_or_failed_stale_drain_stops_before_move(self):
        for mailbox in (
            RecordingMailbox(stale_copyuid_never_ends=True),
            RecordingMailbox(
                stale_copyuid_responses=[RuntimeError("secret stale state")]
            ),
        ):
            with self.subTest(mailbox=mailbox):
                result, mailbox, calls = run_trash(mailbox)
                self.assertEqual(error_code(result), "trash_move_unconfirmed")
                self.assertEqual(error_stage(result), "copyuid_drain")
                self.assertEqual(len(calls), 1)
                self.assertNotIn("move", mailbox.events)

    def test_malformed_ambiguous_or_wrong_mapping_fails_closed(self):
        move_values = (
            [b"MOVE completed"],
            [b"COPYUID 789 123 900"],
            [b"[COPYUID 0 123 900]"],
            [b"[COPYUID 4294967296 123 900]"],
            [b"[COPYUID 789 124 900]"],
            [b"[COPYUID 789 0 900]"],
            [b"[COPYUID 789 4294967296 900]"],
            [b"[COPYUID 789 * 900]"],
            [b"[COPYUID 789 123:124 900]"],
            [b"[COPYUID 789 123,124 900]"],
            [b"[COPYUID 789 123 0]"],
            [b"[COPYUID 789 123 4294967296]"],
            [b"[COPYUID 789 123 *]"],
            [b"[COPYUID 789 123 900:901]"],
            [b"[COPYUID 789 123 900,901]"],
            [b"[COPYUID 0789 123 900]"],
            [b"[COPYUID 789 123 900] [COPYUID 789 123 900]"],
            [b"[COPYUID 789 123 900", b"]"],
            [b"prefix[COPYUID 789 123 900]"],
            [b"[COPYUID 789 123 900]suffix"],
            [b"x" * 4_097],
            [b"x"] * 65,
        )
        for values in move_values:
            with self.subTest(values=values):
                mailbox = RecordingMailbox(move_response=("OK", values))
                result, mailbox, calls = run_trash(mailbox)
                self.assertEqual(error_code(result), "trash_move_unconfirmed")
                self.assertEqual(error_stage(result), "copyuid")
                self.assertEqual(mailbox.events.count("move"), 1)
                self.assertEqual(len(calls), 1)

    def test_multiple_untagged_values_are_ambiguous(self):
        mailbox = RecordingMailbox(
            move_response=("OK", [b"MOVE completed"]),
            post_copyuid_response=(
                "COPYUID",
                [b"789 123 900", b"789 123 900"],
            ),
        )
        result, _, _ = run_trash(mailbox)
        self.assertEqual(error_code(result), "trash_move_unconfirmed")
        self.assertEqual(error_stage(result), "copyuid")

    def test_malformed_untagged_copyuid_sets_fail_closed(self):
        for payload in (
            b"789 123:124 900",
            b"789 123,124 900",
            b"789 123 900:901",
            b"789 123 900,901",
            b"4294967296 123 900",
            b"789 123 4294967296",
            b"789 123 *",
            b"789 123 900 trailing",
        ):
            with self.subTest(payload=payload):
                mailbox = RecordingMailbox(
                    move_response=("OK", [b"MOVE completed"]),
                    post_copyuid_response=("COPYUID", [payload]),
                )
                result, mailbox, calls = run_trash(mailbox)
                self.assertEqual(error_code(result), "trash_move_unconfirmed")
                self.assertEqual(error_stage(result), "copyuid")
                self.assertEqual(mailbox.events.count("move"), 1)
                self.assertEqual(len(calls), 1)


class ImapTrashRaceAndReadbackTests(unittest.TestCase):
    def test_source_identity_failure_or_wrong_scope_stops_before_move(self):
        cases = (
            identity_failure("message_not_found"),
            identity_result(
                folder="Other",
                uid=SOURCE_UID,
                uid_validity=SOURCE_UID_VALIDITY,
            ),
            identity_result(
                folder=SOURCE_FOLDER,
                uid="999",
                uid_validity=SOURCE_UID_VALIDITY,
            ),
            identity_result(
                folder=SOURCE_FOLDER,
                uid=SOURCE_UID,
                uid_validity="999",
            ),
            identity_result(
                folder=SOURCE_FOLDER,
                uid=SOURCE_UID,
                uid_validity=SOURCE_UID_VALIDITY,
                fingerprint="",
            ),
        )
        for source_result in cases:
            with self.subTest(source_result=source_result):
                result, mailbox, calls = run_trash(source_result=source_result)
                expected = (
                    "trash_message_not_found"
                    if source_result.get("status") == "error"
                    else "source_identity_unconfirmed"
                )
                self.assertEqual(error_code(result), expected)
                self.assertEqual(error_stage(result), "source_identity")
                self.assertEqual(len(calls), 1)
                self.assertNotIn("move", mailbox.events)

    def test_move_rejection_and_exception_are_not_retried_or_leaked(self):
        secret = "password=provider-secret"
        for move_response, expected_code in (
            (("NO", [secret.encode()]), "trash_move_failed"),
            (("BAD", []), "trash_move_failed"),
            (RuntimeError(secret), "trash_move_unconfirmed"),
        ):
            with self.subTest(move_response=move_response):
                mailbox = RecordingMailbox(move_response=move_response)
                result, mailbox, calls = run_trash(mailbox)
                self.assertEqual(error_code(result), expected_code)
                self.assertEqual(error_stage(result), "move")
                self.assertEqual(mailbox.events.count("move"), 1)
                self.assertEqual(len(calls), 1)
                self.assertNotIn(secret, json.dumps(result))

    def test_source_uid_still_present_after_copyuid_is_unconfirmed(self):
        mailbox = RecordingMailbox(source_search_response=("OK", [b"123"]))
        result, mailbox, calls = run_trash(mailbox)
        self.assertEqual(error_code(result), "trash_move_unconfirmed")
        self.assertEqual(error_stage(result), "source_postcondition")
        self.assertEqual(mailbox.events.count("move"), 1)
        self.assertEqual(len(calls), 1)

    def test_source_postcondition_reselects_and_rechecks_uidvalidity(self):
        result, mailbox, _ = run_trash()
        self.assertTrue(result["ok"])
        self.assertEqual(mailbox.select_calls, ['"INBOX"'])
        self.assertEqual(mailbox.response_calls.count("UIDVALIDITY"), 1)
        self.assertIn(("SEARCH", None, "UID", "123"), mailbox.uid_calls)

    def test_changed_or_missing_source_uidvalidity_after_move_fails_closed(self):
        for response in (
            ("UIDVALIDITY", [b"457"]),
            ("OK", [b"456"]),
            RuntimeError("provider failure"),
        ):
            with self.subTest(response=response):
                mailbox = RecordingMailbox(uid_validity_response=response)
                result, mailbox, calls = run_trash(mailbox)
                self.assertEqual(error_code(result), "trash_move_unconfirmed")
                self.assertEqual(error_stage(result), "source_postcondition")
                self.assertEqual(mailbox.events.count("move"), 1)
                self.assertEqual(len(calls), 1)

    def test_indeterminate_source_absence_is_unconfirmed(self):
        for response in (
            ("NO", [b""]),
            ("OK", [b"124"]),
            ("OK", [b"123 124"]),
            ("OK", None),
            RuntimeError("search failed"),
        ):
            with self.subTest(response=response):
                mailbox = RecordingMailbox(source_search_response=response)
                result, mailbox, calls = run_trash(mailbox)
                self.assertEqual(error_code(result), "trash_move_unconfirmed")
                self.assertEqual(error_stage(result), "source_postcondition")
                self.assertEqual(mailbox.events.count("move"), 1)
                self.assertEqual(len(calls), 1)

    def test_exact_copyuid_target_is_read_and_fingerprint_must_match(self):
        target = identity_result(
            folder=TRASH_FOLDER,
            uid=TARGET_UID,
            uid_validity=TARGET_UID_VALIDITY,
            fingerprint="b" * 64,
        )
        result, mailbox, calls = run_trash(target_result=target)
        self.assertEqual(error_code(result), "trash_target_mismatch")
        self.assertEqual(error_stage(result), "target_identity")
        self.assertEqual(calls[1]["uid"], TARGET_UID)
        self.assertEqual(
            calls[1]["expected_uid_validity"],
            TARGET_UID_VALIDITY,
        )
        self.assertNotEqual(
            calls[1]["expected_uid_validity"],
            SOURCE_UID_VALIDITY,
        )
        self.assertEqual(mailbox.events.count("move"), 1)

    def test_message_id_difference_does_not_override_fingerprint_identity(self):
        target = identity_result(
            folder=TRASH_FOLDER,
            uid=TARGET_UID,
            uid_validity=TARGET_UID_VALIDITY,
            rfc_message_id="different@example.test",
        )
        result, _, _ = run_trash(target_result=target)
        self.assertTrue(result["ok"])

    def test_missing_message_id_does_not_override_fingerprint_identity(self):
        target = identity_result(
            folder=TRASH_FOLDER,
            uid=TARGET_UID,
            uid_validity=TARGET_UID_VALIDITY,
            rfc_message_id=None,
        )
        result, _, _ = run_trash(target_result=target)
        self.assertTrue(result["ok"])

    def test_duplicate_message_id_with_different_fingerprint_is_rejected(self):
        target = identity_result(
            folder=TRASH_FOLDER,
            uid=TARGET_UID,
            uid_validity=TARGET_UID_VALIDITY,
            fingerprint="b" * 64,
            rfc_message_id=RFC_MESSAGE_ID,
        )
        result, _, _ = run_trash(target_result=target)
        self.assertEqual(error_code(result), "trash_target_mismatch")

    def test_target_identity_scope_must_match_copyuid_exactly(self):
        for override in (
            {"folder": "Other"},
            {"uid": "901"},
            {"uid_validity": "790"},
        ):
            with self.subTest(override=override):
                target = identity_result(
                    folder=override.get("folder", TRASH_FOLDER),
                    uid=override.get("uid", TARGET_UID),
                    uid_validity=override.get(
                        "uid_validity",
                        TARGET_UID_VALIDITY,
                    ),
                )
                result, mailbox, calls = run_trash(target_result=target)
                self.assertEqual(
                    error_code(result),
                    "target_identity_unconfirmed",
                )
                self.assertEqual(error_stage(result), "target_identity")
                self.assertEqual(calls[1]["folder"], TRASH_FOLDER)
                self.assertEqual(calls[1]["uid"], TARGET_UID)
                self.assertEqual(
                    calls[1]["expected_uid_validity"],
                    TARGET_UID_VALIDITY,
                )
                self.assertEqual(mailbox.events.count("move"), 1)

    def test_target_identity_errors_are_safely_mapped_without_retry(self):
        cases = (
            ("folder_unavailable", "target_folder_unavailable"),
            ("uid_validity_unavailable", "target_uid_validity_unavailable"),
            ("uid_validity_changed", "target_uid_validity_changed"),
            ("message_not_found", "target_message_not_found"),
            ("message_identity_unconfirmed", "target_identity_unconfirmed"),
        )
        for provider_code, expected_code in cases:
            with self.subTest(provider_code=provider_code):
                result, mailbox, calls = run_trash(
                    target_result=identity_failure(provider_code)
                )
                self.assertEqual(error_code(result), expected_code)
                self.assertEqual(error_stage(result), "target_identity")
                self.assertEqual(mailbox.events.count("move"), 1)
                self.assertEqual(len(calls), 2)

    def test_provider_folder_is_safely_quoted_in_exact_single_move(self):
        trash_folder = 'Team "Trash"\\2024'
        mailbox = RecordingMailbox(
            list_response=(
                "OK",
                [r'(\Trash) "/" "Team \"Trash\"\\2024"'],
            )
        )
        result, mailbox, _ = run_trash(
            mailbox,
            trash_folder=trash_folder,
            target_result=identity_result(
                folder=trash_folder,
                uid=TARGET_UID,
                uid_validity=TARGET_UID_VALIDITY,
            ),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            [call for call in mailbox.uid_calls if call[0] == "MOVE"],
            [("MOVE", "123", r'"Team \"Trash\"\\2024"')],
        )

    def test_operation_order_is_fixed_and_has_no_fallback(self):
        result, mailbox, _ = run_trash()
        self.assertTrue(result["ok"])
        self.assertEqual(
            mailbox.events,
            [
                "discovery",
                "capability",
                "source_identity",
                "copyuid_drain",
                "move",
                "copyuid_readback",
                "source_reselect",
                "source_uid_validity",
                "source_absence",
                "target_identity",
            ],
        )
        self.assertEqual(mailbox.events.count("move"), 1)
        self.assertEqual(mailbox.unsafe_calls, [])


if __name__ == "__main__":
    unittest.main()
