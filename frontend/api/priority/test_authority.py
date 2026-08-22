from __future__ import annotations

import base64
import unittest
from email.parser import BytesParser
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from .authority import (
    PROVIDER_TIMEOUT_SECONDS,
    PriorityAuthority,
    SemanticAuthorityError,
    _extract_semantic_mime_body,
    _gmail_message_request,
    _open_authenticated_imap,
    canonical_conversation_id,
    custom_imap_thread_id,
    gmail_thread_id,
    load_authorized_gmail_incoming,
    load_authorized_gmail_new_inbound,
    load_authorized_imap_incoming,
    load_authorized_imap_new_inbound,
    mint_outgoing_event_reference_for_authority,
    prove_authorized_new_inbound_source_current,
    prove_authorized_imap_latest,
    resolve_priority_authority,
)
from .event_reference import OutgoingEventClaims, verify_outgoing_event_reference
from .semantic_text import build_semantic_text_window
from .semantic_types import SemanticTurn, SpeakerRole, TurnDirection


def _account(prefix: str, byte: int) -> str:
    suffix = base64.urlsafe_b64encode(bytes([byte]) * 16).rstrip(b"=").decode("ascii")
    return prefix + suffix


WORKSPACE_ID = _account("wsp_", 1)
USER_ID = _account("usr_", 2)


def authority(provider: str = "google") -> PriorityAuthority:
    mailbox = {
        "id": "mailbox-1",
        "provider": provider,
        "email": "primary@example.com",
        "connected": True,
        "connectionStatus": "connected",
    }
    return PriorityAuthority(
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        member_email="owner@example.com",
        mailbox_id="mailbox-1",
        provider=provider,
        mailbox_email="primary@example.com",
        owned_emails=frozenset(
            {"owner@example.com", "primary@example.com", "secondary@example.com"}
        ),
        user_record={"email": "owner@example.com"},
        inbox_record=mailbox,
    )


def claims(
    provider: str,
    provider_conversation_id: str,
    conversation_id: str,
) -> OutgoingEventClaims:
    return OutgoingEventClaims(
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        mailbox_id="mailbox-1",
        provider=provider,
        conversation_id=conversation_id,
        provider_conversation_id=provider_conversation_id,
        latest_turn_id="sent@example.com" if provider == "custom_imap" else "sent-1",
        authored_text_digest="a" * 64,
        occurred_at=10_000,
        issued_at=10,
        expires_at=10 + 14 * 24 * 60 * 60,
        semantic_version="priority-semantic-state-v1",
    )


def normalized_latest_text(source) -> str:
    turn = source.turns[-1]
    window = build_semantic_text_window(
        (
            SemanticTurn(
                turn_id=turn["turnId"],
                speaker=SpeakerRole.EXTERNAL,
                direction=TurnDirection.INCOMING,
                text=turn["text"],
                timestamp=turn.get("timestamp"),
            ),
        )
    )
    return window.turns[-1].text


def gmail_message(
    *,
    message_id: str = "incoming-1",
    thread_id: str = "thread-new",
    sender: str = "External <outside@example.net>",
    labels: list[str] | None = None,
    body: str = "Can you review the agreement?",
    extra_headers: list[dict[str, str]] | None = None,
    internal_date: str = "12000",
) -> dict:
    raw_headers = [
        f"From: {sender}",
        "To: primary@example.com",
        "Subject: Provider-authoritative subject",
        "Date: Thu, 01 Jan 1970 00:00:12 +0000",
        "Message-ID: <incoming@example.net>",
        *(f"{header['name']}: {header['value']}" for header in (extra_headers or [])),
        "Content-Type: text/plain; charset=utf-8",
        "Content-Transfer-Encoding: 8bit",
    ]
    raw_rfc_message = ("\r\n".join(raw_headers) + "\r\n\r\n" + body).encode("utf-8")
    encoded_body = base64.urlsafe_b64encode(raw_rfc_message).rstrip(b"=").decode("ascii")
    return {
        "id": message_id,
        "threadId": thread_id,
        "internalDate": internal_date,
        "labelIds": ["INBOX"] if labels is None else labels,
        "raw": encoded_body,
    }


def gmail_preview(
    target: dict,
    *,
    sender: str = "External <outside@example.net>",
    body: str = "Can you review the agreement?",
    **overrides,
) -> dict:
    return {
        "providerMessageId": target["id"],
        "providerThreadId": target["threadId"],
        "providerFolder": "Inbox",
        "labelIds": list(target["labelIds"]),
        "from": sender,
        "createdAt": "1970-01-01T00:00:12.000Z",
        "body": [body],
        "noiseDisposition": "none",
        "noiseConfidence": "low",
        "noiseReasons": [],
        "v7_final_priority": "NORMAL",
        "final_visibility": "show_normal",
        "action": "show_in_main_feed",
        "category": "business",
        "internalClassification": "business",
        "ui_signal": "BUSINESS",
        "signal": None,
        **overrides,
    }


def gmail_thread_turn(
    target: dict,
    *,
    sender: str = "External <outside@example.net>",
    body: str = "Can you review the agreement?",
) -> dict:
    return {
        "providerMessageId": target["id"],
        "providerThreadId": target["threadId"],
        "from": sender,
        "bodyText": body,
        "createdAt": "1970-01-01T00:00:12.000Z",
        "internalDate": target["internalDate"],
        "labelIds": list(target["labelIds"]),
    }


class FakeImap:
    def __init__(self, raw_message: bytes, *, internaldate: str | None = " 1-Jan-1970 00:00:20 +0000") -> None:
        self.raw_message = raw_message
        self.internaldate = internaldate
        self.closed = False

    def uid(self, command, uid, query):
        if self.internaldate is None:
            metadata = f"1 (UID {uid} BODY[] {{{len(self.raw_message)}}})".encode("ascii")
        else:
            metadata = (
                f'1 (UID {uid} INTERNALDATE "{self.internaldate}" '
                f"BODY[] {{{len(self.raw_message)}}})"
            ).encode("ascii")
        return "OK", [(metadata, self.raw_message)]

    def logout(self):
        self.closed = True


class PriorityAuthorityTests(unittest.TestCase):
    def _gmail_source(self, messages: list[dict]):
        current = authority("google")
        provider_thread = "thread-1"
        conversation = canonical_conversation_id(
            current.mailbox_id,
            gmail_thread_id(current.mailbox_id, provider_thread),
        )
        with patch(
            "api.priority.authority.resolve_gmail_context",
            return_value={
                "status": "ok",
                "context": {"access_token": "internal", "refresh_attempted": False},
            },
        ), patch(
            "api.priority.authority._gmail_thread_request",
            return_value=({}, None),
        ), patch(
            "api.priority.authority.parse_gmail_thread",
            return_value=messages,
        ):
            return load_authorized_gmail_incoming(
                current,
                claims("google", provider_thread, conversation),
                provider_message_id="incoming-2",
            )

    def test_member_workspace_and_every_owned_mailbox_email_are_server_derived(self):
        member = SimpleNamespace(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            email="owner@example.com",
        )
        owned = {
            "status": "ok",
            "user": {"email": "owner@example.com"},
            "inbox": {
                "id": "mailbox-1",
                "provider": "google",
                "email": "primary@example.com",
                "connected": True,
                "connectionStatus": "connected",
            },
            "config": {
                "managedInboxes": [
                    {"id": "mailbox-1", "email": "primary@example.com"},
                    {"id": "mailbox-2", "email": "secondary@example.com"},
                ]
            },
        }
        with patch(
            "api.priority.authority.resolve_authenticated_member_authority",
            return_value=(member, None),
        ), patch(
            "api.priority.authority.resolve_owned_managed_inbox_record",
            return_value=owned,
        ):
            resolved = resolve_priority_authority([], "mailbox-1")

        self.assertEqual(resolved.workspace_id, WORKSPACE_ID)
        self.assertEqual(resolved.user_id, USER_ID)
        self.assertEqual(
            resolved.owned_emails,
            frozenset(
                {"owner@example.com", "primary@example.com", "secondary@example.com"}
            ),
        )

    def test_member_config_email_mismatch_fails_closed(self):
        member = SimpleNamespace(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            email="owner@example.com",
        )
        owned = {
            "status": "ok",
            "user": {"email": "other@example.com"},
            "inbox": {"id": "mailbox-1"},
            "config": {},
        }
        with patch(
            "api.priority.authority.resolve_authenticated_member_authority",
            return_value=(member, None),
        ), patch(
            "api.priority.authority.resolve_owned_managed_inbox_record",
            return_value=owned,
        ), self.assertRaises(SemanticAuthorityError):
            resolve_priority_authority([], "mailbox-1")

    def test_gmail_exact_latest_external_same_thread_and_unknown_prior_is_dropped(self):
        current = authority("google")
        provider_thread = "thread-1"
        conversation = canonical_conversation_id(
            current.mailbox_id,
            gmail_thread_id(current.mailbox_id, provider_thread),
        )
        event = claims("google", provider_thread, conversation)
        messages = [
            {
                "providerMessageId": "sent-1",
                "providerThreadId": provider_thread,
                "from": "primary@example.com",
                "bodyText": "I will send it.",
                "createdAt": "1970-01-01T00:00:10.000Z",
                "internalDate": "10000",
                "labelIds": ["SENT"],
            },
            {
                "providerMessageId": "unknown-prior",
                "providerThreadId": provider_thread,
                "from": "first@example.com, second@example.com",
                "bodyText": "ambiguous sender",
                "createdAt": "1970-01-01T00:00:11.000Z",
                "internalDate": "11000",
                "labelIds": [],
            },
            {
                "providerMessageId": "incoming-2",
                "providerThreadId": provider_thread,
                "from": "External <outside@example.net>",
                "bodyText": "BODY_TEXT_SHOULD_NOT_REACH_MODEL",
                "bodyHtml": (
                    "<div>Can you send the contract?</div>"
                    "<blockquote>QUOTED_GMAIL_SECRET</blockquote>"
                    '<div style="display:none">HIDDEN_GMAIL_SECRET</div>'
                ),
                "createdAt": "1970-01-01T00:00:12.000Z",
                "internalDate": "12000",
                "labelIds": ["INBOX"],
            },
        ]
        with patch(
            "api.priority.authority.resolve_gmail_context",
            return_value={
                "status": "ok",
                "context": {"access_token": "internal", "refresh_attempted": False},
            },
        ), patch(
            "api.priority.authority._gmail_thread_request",
            return_value=({}, None),
        ), patch(
            "api.priority.authority.parse_gmail_thread",
            return_value=messages,
        ):
            source = load_authorized_gmail_incoming(
                current,
                event,
                provider_message_id="incoming-2",
            )

        self.assertEqual(source.conversation_id, conversation)
        self.assertEqual(source.latest_turn_id, "incoming-2")
        self.assertEqual(len(source.turns), 2)
        self.assertEqual(source.turns[0]["speaker"], "user")
        self.assertEqual(source.turns[1]["speaker"], "external")
        self.assertIn("<blockquote>", source.turns[1]["text"])
        normalized = normalized_latest_text(source)
        self.assertIn("Can you send the contract?", normalized)
        self.assertNotIn("BODY_TEXT_SHOULD_NOT_REACH_MODEL", normalized)
        self.assertNotIn("QUOTED_GMAIL_SECRET", normalized)
        self.assertNotIn("HIDDEN_GMAIL_SECRET", normalized)

    def test_gmail_secondary_owned_sender_is_not_external(self):
        current = authority("google")
        provider_thread = "thread-1"
        conversation = canonical_conversation_id(
            current.mailbox_id,
            gmail_thread_id(current.mailbox_id, provider_thread),
        )
        messages = [
            {
                "providerMessageId": "sent-1",
                "providerThreadId": provider_thread,
                "from": "primary@example.com",
                "bodyText": "I will send it.",
                "createdAt": "1970-01-01T00:00:10.000Z",
                "internalDate": "10000",
                "labelIds": ["SENT"],
            },
            {
                "providerMessageId": "incoming-2",
                "providerThreadId": provider_thread,
                "from": "secondary@example.com",
                "bodyText": "self sent",
                "createdAt": "1970-01-01T00:00:12.000Z",
                "internalDate": "12000",
                "labelIds": ["INBOX"],
            },
        ]
        with patch(
            "api.priority.authority.resolve_gmail_context",
            return_value={
                "status": "ok",
                "context": {"access_token": "internal", "refresh_attempted": False},
            },
        ), patch(
            "api.priority.authority._gmail_thread_request",
            return_value=({}, None),
        ), patch(
            "api.priority.authority.parse_gmail_thread",
            return_value=messages,
        ), self.assertRaises(SemanticAuthorityError) as captured:
            load_authorized_gmail_incoming(
                current,
                claims("google", provider_thread, conversation),
                provider_message_id="incoming-2",
            )
        self.assertEqual(captured.exception.code, "incoming_message_not_external")

    def test_gmail_requires_signed_sent_event_preceding_target_in_provider_order(self):
        sent = {
            "providerMessageId": "sent-1",
            "providerThreadId": "thread-1",
            "from": "primary@example.com",
            "bodyText": "I will send it.",
            "createdAt": "1970-01-01T00:00:10.000Z",
            "internalDate": "10000",
            "labelIds": ["SENT"],
        }
        incoming = {
            "providerMessageId": "incoming-2",
            "providerThreadId": "thread-1",
            "from": "outside@example.net",
            "bodyText": "Thanks.",
            "createdAt": "1970-01-01T00:00:12.000Z",
            "internalDate": "12000",
            "labelIds": ["INBOX"],
        }
        cases = (
            ([incoming], "incoming_message_identity_unconfirmed"),
            ([{**sent, "labelIds": ["INBOX"], "from": "outside@example.net"}, incoming], "incoming_message_identity_unconfirmed"),
            ([incoming, sent], "incoming_message_stale"),
        )
        for messages, code in cases:
            with self.subTest(code=code), self.assertRaises(SemanticAuthorityError) as captured:
                self._gmail_source(messages)
            self.assertEqual(captured.exception.code, code)

    def test_new_inbound_gmail_uses_exact_message_and_current_inbox_thread(self):
        current = authority("google")
        target = gmail_message()
        with patch(
            "api.priority.authority.resolve_gmail_context",
            return_value={
                "status": "ok",
                "context": {"access_token": "internal", "refresh_attempted": False},
            },
        ), patch(
            "api.priority.authority._gmail_message_request",
            return_value=(target, None),
        ) as message_read, patch(
            "api.priority.authority._gmail_thread_request",
            return_value=({}, None),
        ) as thread_read, patch(
            "api.priority.authority.parse_gmail_message_detail",
            return_value=gmail_preview(target),
        ) as detail_parser, patch(
            "api.priority.authority.parse_gmail_thread",
            return_value=[gmail_thread_turn(target)],
        ):
            source = load_authorized_gmail_new_inbound(
                current,
                provider_message_id="incoming-1",
            )
            prove_authorized_new_inbound_source_current([], source)

        self.assertEqual(message_read.call_count, 2)
        self.assertEqual(thread_read.call_count, 2)
        self.assertTrue(
            all(call.args == ("internal", "incoming-1") for call in message_read.call_args_list)
        )
        self.assertTrue(
            all(call.args == ("internal", "thread-new") for call in thread_read.call_args_list)
        )
        self.assertEqual(detail_parser.call_count, 2)
        self.assertTrue(
            all(call.kwargs["provider_folder"] == "Inbox" for call in detail_parser.call_args_list)
        )
        self.assertTrue(all(call.kwargs["strict"] is True for call in detail_parser.call_args_list))
        self.assertEqual(source.provider_conversation_id, "thread-new")
        self.assertEqual(source.latest_turn_id, "incoming-1")
        self.assertEqual(source.turns[-1]["text"], "Can you review the agreement?")
        self.assertEqual(source.revalidation_locator["authorityKind"], "new_inbound")

    def test_new_inbound_gmail_exact_detail_transport_uses_raw_format(self):
        payload = b'{"id":"incoming-1"}'
        response = MagicMock()
        response.headers = {"Content-Length": str(len(payload))}
        response.read.return_value = payload
        response.__enter__.return_value = response
        with patch(
            "api.priority.authority.urlopen",
            return_value=response,
        ) as opener:
            parsed, error = _gmail_message_request("internal", "incoming-1")

        self.assertIsNone(error)
        self.assertEqual(parsed, {"id": "incoming-1"})
        request = opener.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/incoming-1?format=raw",
        )

    def test_new_inbound_gmail_rejects_non_inbox_noise_and_ambiguous_sender(self):
        outside = gmail_message()
        non_inbox = gmail_message(labels=[])
        promotion = gmail_message(labels=["INBOX", "CATEGORY_PROMOTIONS"])
        owned = gmail_message(sender="secondary@example.com")
        cases = (
            (non_inbox, gmail_preview(non_inbox), "incoming_message_not_in_inbox"),
            (
                promotion,
                gmail_preview(promotion),
                "incoming_message_noise_excluded",
            ),
            (
                outside,
                gmail_preview(outside, sender="first@example.net, second@example.net"),
                "incoming_message_not_external",
            ),
            (
                owned,
                gmail_preview(owned, sender="secondary@example.com"),
                "incoming_message_not_external",
            ),
        )
        for target, preview, expected_code in cases:
            with self.subTest(expected_code=expected_code), patch(
                "api.priority.authority.resolve_gmail_context",
                return_value={
                    "status": "ok",
                    "context": {
                        "access_token": "internal",
                        "refresh_attempted": False,
                    },
                },
            ), patch(
                "api.priority.authority._gmail_message_request",
                return_value=(target, None),
            ), patch(
                "api.priority.authority.parse_gmail_message_detail",
                return_value=preview,
            ), patch(
                "api.priority.authority._gmail_thread_request"
            ) as thread_read, self.assertRaises(SemanticAuthorityError) as captured:
                load_authorized_gmail_new_inbound(
                    authority("google"),
                    provider_message_id="incoming-1",
                )
            self.assertEqual(captured.exception.code, expected_code)
            thread_read.assert_not_called()

    def test_new_inbound_gmail_rejects_delivery_when_thread_has_advanced(self):
        target = gmail_message()
        newer = gmail_message(
            message_id="incoming-2",
            body="This later message owns current semantics.",
            internal_date="13000",
        )
        with patch(
            "api.priority.authority.resolve_gmail_context",
            return_value={
                "status": "ok",
                "context": {"access_token": "internal", "refresh_attempted": False},
            },
        ), patch(
            "api.priority.authority._gmail_message_request",
            return_value=(target, None),
        ), patch(
            "api.priority.authority.parse_gmail_message_detail",
            return_value=gmail_preview(target),
        ), patch(
            "api.priority.authority._gmail_thread_request",
            return_value=({}, None),
        ), patch(
            "api.priority.authority.parse_gmail_thread",
            return_value=[gmail_thread_turn(target), gmail_thread_turn(newer)],
        ), self.assertRaises(SemanticAuthorityError) as captured:
            load_authorized_gmail_new_inbound(
                authority("google"),
                provider_message_id="incoming-1",
            )
        self.assertEqual(captured.exception.code, "incoming_message_stale")

    def test_new_inbound_gmail_applies_normalized_noise_low_quiet_and_priority_gate(self):
        target = gmail_message()
        cases = (
            (
                {"noiseDisposition": "strong_spam", "noiseConfidence": "high"},
                "incoming_message_noise_excluded",
            ),
            ({"v7_final_priority": "LOW"}, "incoming_message_routing_excluded"),
            ({"final_visibility": "show_low"}, "incoming_message_routing_excluded"),
            ({"action": "show_in_quiet_view"}, "incoming_message_routing_excluded"),
            ({"v7_final_priority": "PRIORITY"}, "incoming_message_routing_excluded"),
            ({"v7_final_priority": "REVIEW"}, "incoming_message_routing_excluded"),
            ({"category": "promo"}, "incoming_message_routing_excluded"),
            ({"ui_signal": "DEMO"}, "incoming_message_routing_excluded"),
        )
        for overrides, expected_code in cases:
            with self.subTest(overrides=overrides), patch(
                "api.priority.authority.resolve_gmail_context",
                return_value={
                    "status": "ok",
                    "context": {
                        "access_token": "internal",
                        "refresh_attempted": False,
                    },
                },
            ), patch(
                "api.priority.authority._gmail_message_request",
                return_value=(target, None),
            ), patch(
                "api.priority.authority.parse_gmail_message_detail",
                return_value=gmail_preview(target, **overrides),
            ), patch(
                "api.priority.authority._gmail_thread_request"
            ) as thread_read, self.assertRaises(SemanticAuthorityError) as captured:
                load_authorized_gmail_new_inbound(
                    authority("google"),
                    provider_message_id="incoming-1",
                )
            self.assertEqual(captured.exception.code, expected_code)
            thread_read.assert_not_called()

    def test_imap_locator_is_refetched_and_bound_to_rfc_root(self):
        current = authority("custom_imap")
        root = "root@example.net"
        conversation = canonical_conversation_id(
            current.mailbox_id,
            custom_imap_thread_id(current.mailbox_id, root),
        )
        raw = (
            b"From: External <outside@example.net>\r\n"
            b"Date: Thu, 01 Jan 2099 00:00:20 +0000\r\n"
            b"Message-ID: <incoming@example.net>\r\n"
            b"References: <root@example.net>\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n\r\n"
            b"<div>Everything is sorted now.</div>"
            b"<blockquote>QUOTED_IMAP_SECRET</blockquote>"
            b"<div style=\"display:none\">HIDDEN_IMAP_SECRET</div>"
        )
        connection = FakeImap(raw)
        imap_mailbox = {
            "mailboxId": "mailbox-1",
            "ownerEmail": "owner@example.com",
            "email": "primary@example.com",
            "imap": {
                "host": "imap.example.net",
                "port": 993,
                "ssl": True,
                "username": "primary@example.com",
                "password": "internal-secret",
            },
        }
        source_result = {
            "ok": True,
            "status": "ok",
            "source": {
                "providerFolder": "INBOX",
                "imapUid": "9",
                "uidValidity": "7",
                "messageId": "<incoming@example.net>",
                "references": ["<root@example.net>"],
                "inReplyTo": "<sent@example.com>",
            },
            "error": None,
        }
        with patch(
            "api.priority.authority.resolve_authenticated_imap_mailbox",
            return_value={"status": "ok", "mailbox": imap_mailbox, "error": None},
        ), patch(
            "api.priority.authority._open_authenticated_imap",
            return_value=connection,
        ), patch(
            "api.priority.authority.read_imap_reply_source",
            return_value=source_result,
        ), patch(
            "api.priority.authority.read_imap_latest_thread_identity",
            return_value={
                "ok": True,
                "status": "ok",
                "latest": {
                    "providerFolder": "INBOX",
                    "uidValidity": "7",
                    "imapUid": "9",
                    "threadId": custom_imap_thread_id("mailbox-1", root),
                    "rfcMessageId": "incoming@example.net",
                },
                "error": None,
            },
        ) as latest_reader:
            source = load_authorized_imap_incoming(
                [],
                current,
                provider_folder="INBOX",
                uid_validity="7",
                imap_uid="9",
            )

        self.assertTrue(connection.closed)
        self.assertEqual(source.conversation_id, conversation)
        self.assertEqual(source.latest_turn_id, "incoming@example.net")
        self.assertEqual(source.occurred_at, 20_000)
        self.assertIn("<blockquote>", source.turns[0]["text"])
        normalized = normalized_latest_text(source)
        self.assertIn("Everything is sorted now.", normalized)
        self.assertNotIn("QUOTED_IMAP_SECRET", normalized)
        self.assertNotIn("HIDDEN_IMAP_SECRET", normalized)
        self.assertNotIn("internal-secret", repr(source))
        self.assertTrue(latest_reader.call_args.kwargs["require_predecessor"])

    def test_new_inbound_imap_allows_only_a_truly_singleton_inbox_without_predecessor(self):
        current = authority("custom_imap")
        raw = (
            b"From: External <outside@example.net>\r\n"
            b"Message-ID: <singleton@example.net>\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"Please approve the attached plan."
        )
        source_result = {
            "ok": True,
            "status": "ok",
            "source": {
                "providerFolder": "INBOX",
                "imapUid": "9",
                "uidValidity": "7",
                "messageId": "<singleton@example.net>",
                "references": [],
                "inReplyTo": None,
            },
            "error": None,
        }
        expected_thread = custom_imap_thread_id(
            current.mailbox_id,
            "singleton@example.net",
        )
        mailbox = {
            "imap": {
                "host": "imap.example.net",
                "port": 993,
                "ssl": True,
                "username": "primary@example.com",
                "password": "internal-secret",
            }
        }
        connection = FakeImap(raw)
        with patch(
            "api.priority.authority.resolve_authenticated_imap_mailbox",
            return_value={"status": "ok", "mailbox": mailbox},
        ), patch(
            "api.priority.authority._open_authenticated_imap",
            return_value=connection,
        ), patch(
            "api.priority.authority.read_imap_reply_source",
            return_value=source_result,
        ), patch(
            "api.priority.authority.read_imap_latest_thread_identity",
            return_value={
                "ok": True,
                "latest": {
                    "providerFolder": "INBOX",
                    "uidValidity": "7",
                    "imapUid": "9",
                    "threadId": expected_thread,
                    "rfcMessageId": "singleton@example.net",
                },
            },
        ) as latest_reader:
            source = load_authorized_imap_new_inbound(
                [],
                current,
                provider_folder="INBOX",
                uid_validity="7",
                imap_uid="9",
            )

        self.assertFalse(latest_reader.call_args.kwargs["require_predecessor"])
        self.assertEqual(source.provider_conversation_id, "singleton@example.net")
        self.assertEqual(source.latest_turn_id, "singleton@example.net")
        self.assertEqual(source.revalidation_locator["authorityKind"], "new_inbound")
        self.assertTrue(connection.closed)

        revalidation_connection = FakeImap(raw)
        with patch(
            "api.priority.authority.resolve_authenticated_imap_mailbox",
            return_value={"status": "ok", "mailbox": mailbox},
        ), patch(
            "api.priority.authority._open_authenticated_imap",
            return_value=revalidation_connection,
        ), patch(
            "api.priority.authority.read_imap_latest_thread_identity",
            return_value={
                "ok": True,
                "latest": {
                    "providerFolder": "INBOX",
                    "uidValidity": "7",
                    "imapUid": "9",
                    "threadId": expected_thread,
                    "rfcMessageId": "singleton@example.net",
                },
            },
        ) as latest_reader:
            prove_authorized_new_inbound_source_current([], source)
        self.assertFalse(latest_reader.call_args.kwargs["require_predecessor"])
        self.assertTrue(revalidation_connection.closed)

    def test_new_inbound_imap_ancestry_stays_conservative_and_non_inbox_fails_early(self):
        current = authority("custom_imap")
        raw = (
            b"From: outside@example.net\r\n"
            b"Message-ID: <incoming@example.net>\r\n"
            b"References: <root@example.net>\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\nDone."
        )
        source_result = {
            "ok": True,
            "source": {
                "providerFolder": "INBOX",
                "imapUid": "9",
                "uidValidity": "7",
                "messageId": "<incoming@example.net>",
                "references": ["<root@example.net>"],
                "inReplyTo": None,
            },
        }
        expected_thread = custom_imap_thread_id(current.mailbox_id, "root@example.net")
        with patch(
            "api.priority.authority.resolve_authenticated_imap_mailbox",
            return_value={"status": "ok", "mailbox": {"imap": {}}},
        ), patch(
            "api.priority.authority._open_authenticated_imap",
            return_value=FakeImap(raw),
        ), patch(
            "api.priority.authority.read_imap_reply_source",
            return_value=source_result,
        ), patch(
            "api.priority.authority.read_imap_latest_thread_identity",
            return_value={
                "ok": True,
                "latest": {
                    "providerFolder": "INBOX",
                    "uidValidity": "7",
                    "imapUid": "9",
                    "threadId": expected_thread,
                    "rfcMessageId": "incoming@example.net",
                },
            },
        ) as latest_reader:
            load_authorized_imap_new_inbound(
                [], current,
                provider_folder="INBOX", uid_validity="7", imap_uid="9",
            )
        self.assertTrue(latest_reader.call_args.kwargs["require_predecessor"])

        with patch(
            "api.priority.authority.resolve_authenticated_imap_mailbox"
        ) as resolver, self.assertRaises(SemanticAuthorityError) as captured:
            load_authorized_imap_new_inbound(
                [], current,
                provider_folder="Archive", uid_validity="7", imap_uid="9",
            )
        self.assertEqual(captured.exception.code, "incoming_message_not_in_inbox")
        resolver.assert_not_called()

    def test_new_inbound_imap_rejects_present_malformed_ancestry_and_spam_headers(self):
        current = authority("custom_imap")
        source_result = {
            "ok": True,
            "source": {
                "providerFolder": "INBOX",
                "imapUid": "9",
                "uidValidity": "7",
                "messageId": "<incoming@example.net>",
                "references": [],
                "inReplyTo": None,
            },
        }
        samples = (
            (
                b"From: outside@example.net\r\n"
                b"Message-ID: <incoming@example.net>\r\n"
                b"References: malformed-ancestry\r\n"
                b"Content-Type: text/plain\r\n\r\nVisible body.",
                "incoming_message_identity_unconfirmed",
            ),
            (
                b"From: outside@example.net\r\n"
                b"Message-ID: <incoming@example.net>\r\n"
                b"X-Spam-Flag: YES\r\n"
                b"Content-Type: text/plain\r\n\r\nVisible body.",
                "incoming_message_noise_excluded",
            ),
        )
        for raw, expected_code in samples:
            with self.subTest(expected_code=expected_code), patch(
                "api.priority.authority.resolve_authenticated_imap_mailbox",
                return_value={"status": "ok", "mailbox": {"imap": {}}},
            ), patch(
                "api.priority.authority._open_authenticated_imap",
                return_value=FakeImap(raw),
            ), patch(
                "api.priority.authority.read_imap_reply_source",
                return_value=source_result,
            ), patch(
                "api.priority.authority.read_imap_latest_thread_identity"
            ) as latest_reader, self.assertRaises(SemanticAuthorityError) as captured:
                load_authorized_imap_new_inbound(
                    [], current,
                    provider_folder="INBOX", uid_validity="7", imap_uid="9",
                )
            self.assertEqual(captured.exception.code, expected_code)
            latest_reader.assert_not_called()

    def test_new_inbound_imap_applies_normalized_noise_low_quiet_and_priority_gate(self):
        current = authority("custom_imap")
        raw = (
            b"From: outside@example.net\r\n"
            b"Message-ID: <incoming@example.net>\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\nVisible body."
        )
        source_result = {
            "ok": True,
            "source": {
                "providerFolder": "INBOX",
                "imapUid": "9",
                "uidValidity": "7",
                "messageId": "<incoming@example.net>",
                "references": [],
                "inReplyTo": None,
            },
        }
        cases = (
            (
                {"noiseDisposition": "unsolicited_low_value"},
                "incoming_message_noise_excluded",
            ),
            ({"v7_final_priority": "LOW"}, "incoming_message_routing_excluded"),
            ({"final_visibility": "show_low"}, "incoming_message_routing_excluded"),
            ({"action": "show_in_quiet_view"}, "incoming_message_routing_excluded"),
            ({"v7_final_priority": "PRIORITY"}, "incoming_message_routing_excluded"),
            ({"category": "demo"}, "incoming_message_routing_excluded"),
        )
        target = gmail_message()
        for overrides, expected_code in cases:
            with self.subTest(overrides=overrides), patch(
                "api.priority.authority.resolve_authenticated_imap_mailbox",
                return_value={"status": "ok", "mailbox": {"imap": {}}},
            ), patch(
                "api.priority.authority._open_authenticated_imap",
                return_value=FakeImap(raw),
            ), patch(
                "api.priority.authority.read_imap_reply_source",
                return_value=source_result,
            ), patch(
                "api.priority.authority.to_message_preview",
                return_value=gmail_preview(target, **overrides),
            ), patch(
                "api.priority.authority.read_imap_latest_thread_identity"
            ) as latest_reader, self.assertRaises(SemanticAuthorityError) as captured:
                load_authorized_imap_new_inbound(
                    [], current,
                    provider_folder="INBOX", uid_validity="7", imap_uid="9",
                )
            self.assertEqual(captured.exception.code, expected_code)
            latest_reader.assert_not_called()

    def test_semantic_mime_extractor_excludes_attachment_text_and_subtrees(self):
        raw = (
            b'MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary="outer"'
            b"\r\n\r\n--outer\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
            b"VISIBLE_AUTHORED_BODY\r\n"
            b"--outer\r\nContent-Type: text/plain; charset=utf-8\r\n"
            b"Content-Disposition: attachment\r\n\r\nPLAIN_ATTACHMENT_SECRET\r\n"
            b"--outer\r\nContent-Type: text/html; name=secret.html\r\n\r\n"
            b"<p>HTML_ATTACHMENT_SECRET</p>\r\n"
            b'--outer\r\nContent-Type: multipart/mixed; boundary="inner"\r\n'
            b"Content-Disposition: attachment\r\n\r\n"
            b"--inner\r\nContent-Type: text/plain\r\n\r\n"
            b"MULTIPART_ATTACHMENT_SECRET\r\n--inner--\r\n"
            b"--outer\r\nContent-Type: message/rfc822\r\n\r\n"
            b"From: forwarded@example.net\r\nContent-Type: text/plain\r\n\r\n"
            b"FORWARDED_MESSAGE_SECRET\r\n--outer--\r\n"
        )
        extracted = _extract_semantic_mime_body(BytesParser().parsebytes(raw))
        self.assertEqual(extracted.strip(), "VISIBLE_AUTHORED_BODY")
        self.assertNotIn("PLAIN_ATTACHMENT_SECRET", extracted)
        self.assertNotIn("HTML_ATTACHMENT_SECRET", extracted)
        self.assertNotIn("MULTIPART_ATTACHMENT_SECRET", extracted)
        self.assertNotIn("FORWARDED_MESSAGE_SECRET", extracted)

    def test_semantic_mime_extractor_rejects_nonmultipart_attachments_and_bad_decode(self):
        samples = (
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"Content-Disposition: attachment\r\n\r\nATTACHMENT_SECRET",
            b"Content-Type: text/plain; charset=utf-8; name=secret.txt\r\n\r\n"
            b"FILENAME_ATTACHMENT_SECRET",
            b"Content-Type: text/plain; charset=ascii\r\n\r\n\xff",
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            + b"x" * (256 * 1024 + 1),
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"Content-Disposition: inline\r\n"
            b"Content-Disposition: attachment\r\n\r\n"
            b"DUPLICATE_DISPOSITION_ATTACHMENT_SECRET",
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"Content-Type: application/octet-stream\r\n\r\n"
            b"DUPLICATE_CONTENT_TYPE_SECRET",
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"Content-Transfer-Encoding: base64\r\n\r\n"
            b"VklTSUJMRV9TRUNSRVQ$",
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"Content-Transfer-Encoding: x-evil\r\n\r\n"
            b"UNKNOWN_TRANSFER_SECRET",
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"Content-Transfer-Encoding: quoted-printable\r\n\r\n"
            b"VISIBLE=ZZSECRET",
            b"Content-Type: text/plain; charset=latin-1\r\n\r\n\xff",
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"Content-Transfer-Encoding: 7bit\r\n\r\n\xc3\xa9",
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"Content-Transfer-Encoding: binary\r\n\r\nBINARY_SECRET",
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"Content-Transfer-Encoding: 8bit\r\n\r\nVISIBLE\x00SECRET",
        )
        for raw in samples:
            with self.subTest(length=len(raw)):
                self.assertIsNone(
                    _extract_semantic_mime_body(BytesParser().parsebytes(raw))
                )

    def test_semantic_mime_extractor_accepts_strict_base64_and_quoted_printable(self):
        samples = (
            (
                b"Content-Type: text/plain; charset=us-ascii\r\n"
                b"Content-Transfer-Encoding: 7bit\r\n\r\n"
                b"VISIBLE_AUTHORED_BODY",
                "VISIBLE_AUTHORED_BODY",
            ),
            (
                b"Content-Type: text/plain; charset=utf-8\r\n"
                b"Content-Transfer-Encoding: 8bit\r\n\r\n"
                b"Meertalige tekst: \xc3\xa9",
                "Meertalige tekst: é",
            ),
            (
                b"Content-Type: text/plain; charset=utf-8\r\n"
                b"Content-Transfer-Encoding: base64\r\n\r\n"
                b"VklTSUJMRV9BVVRIT1JFRF9CT0RZ",
                "VISIBLE_AUTHORED_BODY",
            ),
            (
                b"Content-Type: text/plain; charset=utf-8\r\n"
                b"Content-Transfer-Encoding: quoted-printable\r\n\r\n"
                b"VISIBLE=5FAUTHORED=5FBODY",
                "VISIBLE_AUTHORED_BODY",
            ),
        )
        for raw, expected in samples:
            with self.subTest(expected=expected):
                self.assertEqual(
                    _extract_semantic_mime_body(BytesParser().parsebytes(raw)),
                    expected,
                )

    def test_imap_duplicate_from_headers_fail_closed_before_externality(self):
        current = authority("custom_imap")
        root = "root@example.net"
        raw = (
            b"From: outside@example.net\r\n"
            b"From: owner@example.com\r\n"
            b"Message-ID: <incoming@example.net>\r\n"
            b"References: <root@example.net>\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\nDone."
        )
        connection = FakeImap(raw)
        mailbox = {
            "email": "primary@example.com",
            "imap": {
                "host": "imap.example.net",
                "port": 993,
                "ssl": True,
                "username": "primary@example.com",
                "password": "internal-secret",
            },
        }
        source_result = {
            "ok": True,
            "status": "ok",
            "source": {
                "providerFolder": "INBOX",
                "imapUid": "9",
                "uidValidity": "7",
                "messageId": "<incoming@example.net>",
                "references": ["<root@example.net>"],
                "inReplyTo": "<sent@example.com>",
            },
            "error": None,
        }
        with patch(
            "api.priority.authority.resolve_authenticated_imap_mailbox",
            return_value={"status": "ok", "mailbox": mailbox, "error": None},
        ), patch(
            "api.priority.authority._open_authenticated_imap",
            return_value=connection,
        ), patch(
            "api.priority.authority.read_imap_reply_source",
            return_value=source_result,
        ), patch(
            "api.priority.authority.read_imap_latest_thread_identity",
            return_value={
                "ok": True,
                "latest": {
                    "providerFolder": "INBOX",
                    "uidValidity": "7",
                    "imapUid": "9",
                    "threadId": custom_imap_thread_id("mailbox-1", root),
                    "rfcMessageId": "incoming@example.net",
                },
            },
        ), self.assertRaises(SemanticAuthorityError) as captured:
            load_authorized_imap_incoming(
                [],
                current,
                provider_folder="INBOX",
                uid_validity="7",
                imap_uid="9",
            )

        self.assertEqual(
            captured.exception.code,
            "incoming_message_identity_unconfirmed",
        )
        self.assertTrue(connection.closed)

    def test_imap_missing_mime_date_uses_internaldate_and_malformed_internaldate_fails(self):
        current = authority("custom_imap")
        root = "root@example.net"
        conversation = canonical_conversation_id(
            current.mailbox_id,
            custom_imap_thread_id(current.mailbox_id, root),
        )
        raw = (
            b"From: outside@example.net\r\n"
            b"Message-ID: <incoming@example.net>\r\n"
            b"References: <root@example.net>\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"Done."
        )
        source_result = {
            "ok": True,
            "status": "ok",
            "source": {
                "providerFolder": "INBOX",
                "imapUid": "9",
                "uidValidity": "7",
                "messageId": "<incoming@example.net>",
                "references": ["<root@example.net>"],
                "inReplyTo": "<sent@example.com>",
            },
            "error": None,
        }
        mailbox = {
            "imap": {
                "host": "imap.example.net",
                "port": 993,
                "ssl": True,
                "username": "owner@example.com",
                "password": "secret",
            }
        }
        latest = {
            "ok": True,
            "latest": {
                "providerFolder": "INBOX",
                "uidValidity": "7",
                "imapUid": "9",
                "threadId": custom_imap_thread_id("mailbox-1", root),
                "rfcMessageId": "incoming@example.net",
            },
        }
        with patch(
            "api.priority.authority.resolve_authenticated_imap_mailbox",
            return_value={"status": "ok", "mailbox": mailbox},
        ), patch(
            "api.priority.authority.read_imap_reply_source",
            return_value=source_result,
        ), patch(
            "api.priority.authority.read_imap_latest_thread_identity",
            return_value=latest,
        ), patch(
            "api.priority.authority._open_authenticated_imap",
            # Provider INTERNALDATE is deliberately older than the API-server
            # send timestamp; same-folder UID order remains authoritative.
            return_value=FakeImap(
                raw,
                internaldate=" 1-Jan-1970 00:00:05 +0000",
            ),
        ):
            source = load_authorized_imap_incoming(
                [], current,
                provider_folder="INBOX", uid_validity="7", imap_uid="9",
            )
        self.assertEqual(source.occurred_at, 5_000)

        with patch(
            "api.priority.authority.resolve_authenticated_imap_mailbox",
            return_value={"status": "ok", "mailbox": mailbox},
        ), patch(
            "api.priority.authority.read_imap_reply_source",
            return_value=source_result,
        ), patch(
            "api.priority.authority._open_authenticated_imap",
            return_value=FakeImap(raw, internaldate=None),
        ), self.assertRaises(SemanticAuthorityError) as captured:
            load_authorized_imap_incoming(
                [], current,
                provider_folder="INBOX", uid_validity="7", imap_uid="9",
            )
        self.assertEqual(captured.exception.code, "incoming_message_identity_unconfirmed")

        connection = SimpleNamespace(logout=lambda: None)
        rootless_source = {
            "ok": True,
            "status": "ok",
            "source": {
                "providerFolder": "INBOX",
                "imapUid": "9",
                "uidValidity": "7",
                "messageId": "<incoming@example.net>",
                "references": [],
                "inReplyTo": None,
            },
            "error": None,
        }
        with patch(
            "api.priority.authority.resolve_authenticated_imap_mailbox",
            return_value={"status": "ok", "mailbox": {"imap": {}}},
        ), patch(
            "api.priority.authority._open_authenticated_imap",
            return_value=connection,
        ), patch(
            "api.priority.authority.read_imap_reply_source",
            return_value=rootless_source,
        ), patch(
            "api.priority.authority.read_imap_latest_thread_identity"
        ) as latest_reader, self.assertRaises(SemanticAuthorityError) as captured:
            load_authorized_imap_incoming(
                [], current,
                provider_folder="INBOX", uid_validity="7", imap_uid="9",
            )
        self.assertEqual(captured.exception.code, "incoming_message_identity_unconfirmed")
        latest_reader.assert_not_called()

        raw = (
            b"From: outside@example.net\r\n"
            b"Message-ID: <incoming@example.net>\r\n"
            b"References: <root@example.net>\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\nDone."
        )
        valid_source = {
            "ok": True,
            "status": "ok",
            "source": {
                "providerFolder": "INBOX",
                "imapUid": "9",
                "uidValidity": "7",
                "messageId": "<incoming@example.net>",
                "references": ["<root@example.net>"],
                "inReplyTo": None,
            },
            "error": None,
        }
        with patch(
            "api.priority.authority.resolve_authenticated_imap_mailbox",
            return_value={"status": "ok", "mailbox": {"imap": {}}},
        ), patch(
            "api.priority.authority._open_authenticated_imap",
            return_value=FakeImap(raw),
        ), patch(
            "api.priority.authority.read_imap_reply_source",
            return_value=valid_source,
        ), patch(
            "api.priority.authority.read_imap_latest_thread_identity",
            return_value={
                "ok": True,
                "latest": {
                    "providerFolder": "INBOX",
                    "uidValidity": "7",
                    "imapUid": "9",
                    "threadId": custom_imap_thread_id("mailbox-2", root),
                    "rfcMessageId": "incoming@example.net",
                },
            },
        ), self.assertRaises(SemanticAuthorityError) as captured:
            load_authorized_imap_incoming(
                [], current,
                provider_folder="INBOX", uid_validity="7", imap_uid="9",
            )
        self.assertEqual(captured.exception.code, "incoming_message_stale")

    def test_imap_locator_stream_and_latest_identity_mismatch_fail_closed(self):
        current = authority("custom_imap")
        root = "root@example.net"
        conversation = canonical_conversation_id(
            current.mailbox_id,
            custom_imap_thread_id(current.mailbox_id, root),
        )
        with patch(
            "api.priority.authority.resolve_authenticated_imap_mailbox"
        ) as resolver, self.assertRaises(SemanticAuthorityError) as captured:
            load_authorized_imap_incoming(
                [], current,
                provider_folder="Archive\n", uid_validity="7", imap_uid="9",
            )
        self.assertEqual(captured.exception.code, "invalid_incoming_locator")
        resolver.assert_not_called()

        mismatched_source = {
            "ok": True,
            "status": "ok",
            "source": {
                "providerFolder": "Archive",
                "imapUid": "9",
                "uidValidity": "7",
                "messageId": "<incoming@example.net>",
                "references": ["<root@example.net>"],
                "inReplyTo": None,
            },
            "error": None,
        }
        connection = SimpleNamespace(logout=lambda: None)
        with patch(
            "api.priority.authority.resolve_authenticated_imap_mailbox",
            return_value={"status": "ok", "mailbox": {"imap": {}}},
        ), patch(
            "api.priority.authority._open_authenticated_imap",
            return_value=connection,
        ), patch(
            "api.priority.authority.read_imap_reply_source",
            return_value=mismatched_source,
        ), self.assertRaises(SemanticAuthorityError) as captured:
            load_authorized_imap_incoming(
                [], current,
                provider_folder="INBOX", uid_validity="7", imap_uid="9",
            )
        self.assertEqual(captured.exception.code, "incoming_message_identity_unconfirmed")

    def test_prederived_authority_mint_is_local_and_regression_safe(self):
        current = authority("google")
        reference, conversation_id = mint_outgoing_event_reference_for_authority(
            current,
            provider="google",
            provider_conversation_id="thread-1",
            latest_turn_id="sent-1",
            authored_text="Done.",
            occurred_at=10_000,
            semantic_version="priority-semantic-state-v1",
            hmac_secret="priority-test-secret-with-more-than-thirty-two-bytes",
            now=10,
        )
        verified = verify_outgoing_event_reference(
            reference,
            secret="priority-test-secret-with-more-than-thirty-two-bytes",
            now=11,
        )
        self.assertEqual(verified.mailbox_id, current.mailbox_id)
        self.assertEqual(verified.conversation_id, conversation_id)

        with self.assertRaises(SemanticAuthorityError) as captured:
            mint_outgoing_event_reference_for_authority(
                authority("custom_imap"),
                provider="custom_imap",
                provider_conversation_id="root@example.net",
                latest_turn_id="sent@example.net",
                authored_text="Done.",
                occurred_at=10_000,
                semantic_version="priority-semantic-state-v1",
                hmac_secret="priority-test-secret-with-more-than-thirty-two-bytes",
                now=10,
            )
        self.assertEqual(captured.exception.code, "unsupported_provider")

    def test_imap_latest_mismatch_and_safe_connection_boundary_fail_closed(self):
        current = authority("custom_imap")
        root = "root@example.net"
        conversation = canonical_conversation_id(
            current.mailbox_id,
            custom_imap_thread_id(current.mailbox_id, root),
        )
        mailbox = {
            "imap": {
                "host": "imap.example.net",
                "port": 993,
                "ssl": True,
                "username": "owner@example.com",
                "password": "server-secret",
            }
        }
        connection = SimpleNamespace(logout=lambda: None)
        with patch(
            "api.priority.authority.resolve_authenticated_imap_mailbox",
            return_value={"status": "ok", "mailbox": mailbox},
        ), patch(
            "api.priority.authority._open_authenticated_imap",
            return_value=connection,
        ), patch(
            "api.priority.authority.read_imap_latest_thread_identity",
            return_value={
                "ok": True,
                "latest": {
                    "providerFolder": "INBOX",
                    "uidValidity": "7",
                    "imapUid": "10",
                    "threadId": custom_imap_thread_id("mailbox-1", root),
                    "rfcMessageId": "newer@example.net",
                },
            },
        ), self.assertRaises(SemanticAuthorityError) as captured:
            prove_authorized_imap_latest(
                [], current,
                provider_conversation_id=root,
                conversation_id=conversation,
                provider_folder="INBOX", uid_validity="7", target_uid="9",
                expected_rfc_message_id="incoming@example.net",
            )
        self.assertEqual(captured.exception.code, "incoming_message_stale")

        sentinel = object()
        with patch(
            "api.priority.authority.connect_mailbox_with_settings",
            return_value=sentinel,
        ) as connect:
            self.assertIs(_open_authenticated_imap(mailbox), sentinel)
        connect.assert_called_once_with(
            "imap.example.net",
            993,
            "owner@example.com",
            "server-secret",
            True,
            timeout=PROVIDER_TIMEOUT_SECONDS,
        )


if __name__ == "__main__":
    unittest.main()
