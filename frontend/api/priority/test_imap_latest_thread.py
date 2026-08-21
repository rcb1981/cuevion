from __future__ import annotations

import unittest
from unittest.mock import patch

from api.inboxes.imap_snapshot import read_imap_latest_thread_identity
from imap_connect_preview import build_bounded_thread_identity


class _Imap:
    def __init__(
        self,
        uids: list[str],
        *,
        uid_validity: str = "7",
        fetch_mode: str = "valid",
        headers_by_uid: dict[str, bytes] | None = None,
    ) -> None:
        self.uids = uids
        self.uid_validity = uid_validity
        self.fetch_mode = fetch_mode
        self.headers_by_uid = headers_by_uid or {}
        self.select_calls: list[tuple[str, bool]] = []
        self.fetches: list[str] = []
        self.uid_calls: list[tuple[object, object, object]] = []

    def select(self, folder: str, *, readonly: bool = False):
        self.select_calls.append((folder, readonly))
        return "OK", [b"1"]

    def response(self, name: str):
        return name, [self.uid_validity.encode("ascii")]

    def uid(self, command: str, first, second):
        self.uid_calls.append((command, first, second))
        if command == "SEARCH":
            if self.fetch_mode == "search_exception":
                raise OSError("fixed provider failure")
            return "OK", [" ".join(self.uids).encode("ascii")]
        uid = first
        self.fetches.append(uid)
        if self.fetch_mode == "fetch_exception":
            raise OSError("fixed provider failure")
        raw = self.headers_by_uid.get(
            uid,
            (
                b"x" * (64 * 1024 + 1)
                if self.fetch_mode == "oversized"
                else f"Message-ID: <message-{uid}@example.net>\r\n\r\n".encode(
                    "ascii"
                )
            ),
        )
        metadata = (
            b"malformed"
            if self.fetch_mode == "malformed"
            else f"1 (UID {uid} BODY[HEADER] {{{len(raw)}}})".encode("ascii")
        )
        return "OK", [(metadata, raw)]


class LatestImapThreadTests(unittest.TestCase):
    def test_exact_readonly_folder_stream_returns_latest_same_root(self):
        mailbox = _Imap(["8", "9", "10"])

        def metadata(_message, uid, _fallback):
            return {"message_id": f"message-{uid}@example.net"}

        with patch(
            "api.inboxes.imap_snapshot.extract_message_thread_metadata",
            side_effect=metadata,
        ), patch(
            "api.inboxes.imap_snapshot.resolve_custom_imap_thread_ids",
            return_value=["imap:rfc:mailbox:root", "other", "imap:rfc:mailbox:root"],
        ):
            result = read_imap_latest_thread_identity(
                mailbox,
                mailbox_key="mailbox",
                folder="INBOX",
                expected_uid_validity="7",
                target_uid="8",
                expected_thread_id="imap:rfc:mailbox:root",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["latest"]["imapUid"], "10")
        self.assertEqual(result["latest"]["rfcMessageId"], "message-10@example.net")
        self.assertEqual(mailbox.select_calls, [('\"INBOX\"', True)])
        self.assertEqual(mailbox.fetches, ["8", "9", "10"])
        self.assertEqual(mailbox.uid_calls[0], ("SEARCH", None, "ALL"))
        self.assertTrue(all(
            call[0] == "FETCH" and call[2] == "(UID BODY.PEEK[HEADER])"
            for call in mailbox.uid_calls[1:]
        ))

    def test_target_latest_and_later_unrelated_remain_current_but_same_root_advances(self):
        def run(uids: list[str], thread_ids: list[str]):
            mailbox = _Imap(uids)
            with patch(
                "api.inboxes.imap_snapshot.extract_message_thread_metadata",
                side_effect=lambda _message, uid, _fallback: {
                    "message_id": f"message-{uid}@example.net"
                },
            ), patch(
                "api.inboxes.imap_snapshot.resolve_custom_imap_thread_ids",
                return_value=thread_ids,
            ):
                return read_imap_latest_thread_identity(
                    mailbox,
                    mailbox_key="mailbox",
                    folder="INBOX",
                    expected_uid_validity="7",
                    target_uid="8",
                    expected_thread_id="imap:rfc:mailbox:root",
                )

        target = run(["8"], ["imap:rfc:mailbox:root"])
        unrelated = run(["8", "9"], ["imap:rfc:mailbox:root", "other"])
        newer_same_root = run(
            ["8", "9"],
            ["imap:rfc:mailbox:root", "imap:rfc:mailbox:root"],
        )
        self.assertEqual(target["latest"]["imapUid"], "8")
        self.assertEqual(unrelated["latest"]["imapUid"], "8")
        self.assertEqual(newer_same_root["latest"]["imapUid"], "9")

    def test_required_predecessor_rejects_singleton_and_proves_existing_thread(self):
        def run(uids: list[str], thread_ids: list[str], target_uid: str = "8"):
            mailbox = _Imap(uids)
            with patch(
                "api.inboxes.imap_snapshot.extract_message_thread_metadata",
                side_effect=lambda _message, uid, _fallback: {
                    "message_id": f"message-{uid}@example.net"
                },
            ), patch(
                "api.inboxes.imap_snapshot.resolve_custom_imap_thread_ids",
                return_value=thread_ids,
            ):
                result = read_imap_latest_thread_identity(
                    mailbox,
                    mailbox_key="mailbox",
                    folder="INBOX",
                    expected_uid_validity="7",
                    target_uid=target_uid,
                    expected_thread_id="imap:rfc:mailbox:root",
                    require_predecessor=True,
                )
            return mailbox, result

        mailbox, current = run(
            ["7", "8", "9"],
            ["imap:rfc:mailbox:root", "imap:rfc:mailbox:root", "other"],
        )
        self.assertTrue(current["ok"])
        self.assertEqual(current["latest"]["imapUid"], "8")
        self.assertEqual(mailbox.fetches, ["7", "8", "9"])

        _mailbox, singleton = run(["8"], ["imap:rfc:mailbox:root"])
        self.assertFalse(singleton["ok"])
        self.assertEqual(singleton["error"]["stage"], "predecessor")

        _mailbox, unrelated_predecessor = run(
            ["7", "8"],
            ["other", "imap:rfc:mailbox:root"],
        )
        self.assertFalse(unrelated_predecessor["ok"])
        self.assertEqual(unrelated_predecessor["error"]["stage"], "predecessor")

        _mailbox, stale = run(
            ["7", "8", "9"],
            [
                "imap:rfc:mailbox:root",
                "imap:rfc:mailbox:root",
                "imap:rfc:mailbox:root",
            ],
        )
        self.assertEqual(stale["latest"]["imapUid"], "9")

    def test_required_predecessor_uses_real_rfc_thread_resolution(self):
        expected = build_bounded_thread_identity(
            "imap:rfc",
            "mailbox",
            "root@example.net",
        )
        mailbox = _Imap(
            ["8", "9", "10"],
            headers_by_uid={
                "8": b"Message-ID: <root@example.net>\r\n\r\n",
                "9": (
                    b"Message-ID: <incoming@example.net>\r\n"
                    b"References: <root@example.net>\r\n\r\n"
                ),
                "10": b"Message-ID: <unrelated@example.net>\r\n\r\n",
            },
        )
        result = read_imap_latest_thread_identity(
            mailbox,
            mailbox_key="mailbox",
            folder="INBOX",
            expected_uid_validity="7",
            target_uid="9",
            expected_thread_id=expected,
            require_predecessor=True,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["latest"]["imapUid"], "9")
        self.assertEqual(result["latest"]["threadId"], expected)

    def test_later_same_root_with_ambiguous_message_id_fails_closed(self):
        expected = build_bounded_thread_identity(
            "imap:rfc",
            "mailbox",
            "root@example.net",
        )
        mailbox = _Imap(
            ["7", "8", "9"],
            headers_by_uid={
                "7": b"Message-ID: <root@example.net>\r\n\r\n",
                "8": (
                    b"Message-ID: <target@example.net>\r\n"
                    b"References: <root@example.net>\r\n\r\n"
                ),
                "9": (
                    b"Message-ID: <ambiguous-a@example.net>\r\n"
                    b"Message-ID: <ambiguous-b@example.net>\r\n"
                    b"References: <root@example.net>\r\n\r\n"
                ),
            },
        )
        result = read_imap_latest_thread_identity(
            mailbox,
            mailbox_key="mailbox",
            folder="INBOX",
            expected_uid_validity="7",
            target_uid="8",
            expected_thread_id=expected,
            require_predecessor=True,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["stage"], "thread_resolution")
        self.assertEqual(mailbox.fetches, ["7", "8", "9"])

    def test_later_ambiguous_message_id_including_root_without_ancestry_fails_closed(self):
        expected = build_bounded_thread_identity(
            "imap:rfc",
            "mailbox",
            "root@example.net",
        )
        mailbox = _Imap(
            ["7", "8", "9"],
            headers_by_uid={
                "7": b"Message-ID: <root@example.net>\r\n\r\n",
                "8": (
                    b"Message-ID: <target@example.net>\r\n"
                    b"References: <root@example.net>\r\n\r\n"
                ),
                "9": (
                    b"Message-ID: <root@example.net>\r\n"
                    b"Message-ID: <evil@example.net>\r\n\r\n"
                ),
            },
        )
        result = read_imap_latest_thread_identity(
            mailbox,
            mailbox_key="mailbox",
            folder="INBOX",
            expected_uid_validity="7",
            target_uid="8",
            expected_thread_id=expected,
            require_predecessor=True,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["stage"], "thread_resolution")

    def test_required_predecessor_and_later_stream_share_one_25_header_bound(self):
        mailbox = _Imap([str(value) for value in range(1, 27)])
        result = read_imap_latest_thread_identity(
            mailbox,
            mailbox_key="mailbox",
            folder="INBOX",
            expected_uid_validity="7",
            target_uid="2",
            expected_thread_id="imap:rfc:mailbox:root",
            require_predecessor=True,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["stage"], "predecessor")
        self.assertEqual(mailbox.fetches, [])

    def test_uidvalidity_target_thread_and_scan_bound_fail_closed(self):
        wrong_generation = read_imap_latest_thread_identity(
            _Imap(["8"], uid_validity="9"),
            mailbox_key="mailbox",
            folder="INBOX",
            expected_uid_validity="7",
            target_uid="8",
            expected_thread_id="imap:rfc:mailbox:root",
        )
        self.assertFalse(wrong_generation["ok"])

        oversized = _Imap([str(value) for value in range(1, 27)])
        result = read_imap_latest_thread_identity(
            oversized,
            mailbox_key="mailbox",
            folder="INBOX",
            expected_uid_validity="7",
            target_uid="1",
            expected_thread_id="imap:rfc:mailbox:root",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["stage"], "scan_bound")
        self.assertEqual(oversized.fetches, [])

        mailbox = _Imap(["8"])
        with patch(
            "api.inboxes.imap_snapshot.extract_message_thread_metadata",
            return_value={"message_id": "message-8@example.net"},
        ), patch(
            "api.inboxes.imap_snapshot.resolve_custom_imap_thread_ids",
            return_value=["imap:rfc:mailbox:different"],
        ):
            wrong_root = read_imap_latest_thread_identity(
                mailbox,
                mailbox_key="mailbox",
                folder="INBOX",
                expected_uid_validity="7",
                target_uid="8",
                expected_thread_id="imap:rfc:mailbox:root",
            )
        self.assertFalse(wrong_root["ok"])
        self.assertEqual(wrong_root["error"]["stage"], "thread_resolution")

        missing_target = read_imap_latest_thread_identity(
            _Imap(["9"]),
            mailbox_key="mailbox",
            folder="INBOX",
            expected_uid_validity="7",
            target_uid="8",
            expected_thread_id="imap:rfc:mailbox:root",
        )
        self.assertFalse(missing_target["ok"])
        self.assertEqual(missing_target["error"]["stage"], "uid_search")

        invalid_folder = read_imap_latest_thread_identity(
            _Imap(["8"]),
            mailbox_key="mailbox",
            folder="bad\nfolder",
            expected_uid_validity="7",
            target_uid="8",
            expected_thread_id="imap:rfc:mailbox:root",
        )
        self.assertFalse(invalid_folder["ok"])
        self.assertEqual(invalid_folder["error"]["stage"], "input_validation")

    def test_malformed_oversized_and_provider_failures_never_return_latest(self):
        for mode, expected_stage in (
            ("malformed", "header_fetch"),
            ("oversized", "header_fetch"),
            ("fetch_exception", "header_fetch"),
            ("search_exception", "uid_search"),
        ):
            with self.subTest(mode=mode):
                mailbox = _Imap(["8"], fetch_mode=mode)
                result = read_imap_latest_thread_identity(
                    mailbox,
                    mailbox_key="mailbox",
                    folder="INBOX",
                    expected_uid_validity="7",
                    target_uid="8",
                    expected_thread_id="imap:rfc:mailbox:root",
                )
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["stage"], expected_stage)
                self.assertIsNone(result["snapshot"])


if __name__ == "__main__":
    unittest.main()
