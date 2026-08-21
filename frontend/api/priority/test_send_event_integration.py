from __future__ import annotations

import base64
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from .authority import PriorityAuthority
from .event_reference import (
    EventReferenceError,
    verify_outgoing_event_reference,
)
from .semantic_config import SemanticMode, SemanticRuntimeConfig
from .semantic_types import SEMANTIC_SCHEMA_VERSION


SECRET = "priority-test-secret-with-more-than-thirty-two-bytes"


def _account(prefix: str, byte: int) -> str:
    suffix = base64.urlsafe_b64encode(bytes([byte]) * 16).rstrip(b"=").decode("ascii")
    return prefix + suffix


def _load_send_module():
    name = "api.inboxes.send_gmail_priority_test"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    path = Path(__file__).resolve().parents[1] / "inboxes" / "send-gmail.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("send route could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SEND = _load_send_module()


def _authority(provider: str) -> PriorityAuthority:
    inbox = {
        "id": "mailbox-1",
        "provider": provider,
        "email": "owner@example.com",
        "connected": True,
        "connectionStatus": "connected",
    }
    return PriorityAuthority(
        workspace_id=_account("wsp_", 1),
        user_id=_account("usr_", 2),
        member_email="owner@example.com",
        mailbox_id="mailbox-1",
        provider=provider,
        mailbox_email="owner@example.com",
        owned_emails=frozenset({"owner@example.com"}),
        user_record={"email": "owner@example.com"},
        inbox_record=inbox,
    )


def _payload(*, reply: bool, cc: str = "") -> dict:
    payload = {
        "mailboxId": "mailbox-1",
        "to": "outside@example.net",
        "cc": cc,
        "bcc": "",
        "subject": "Re: Project",
        "bodyHtml": "",
        "bodyText": "Everything is complete.",
        "attachments": [],
    }
    if reply:
        payload["replyContext"] = {"sourceProviderMessageId": "source-1"}
    return payload


def _owned_mailbox(provider: str = "google") -> dict:
    inbox = {
        "id": "mailbox-1",
        "provider": provider,
        "email": "owner@example.com",
        "connected": True,
        "connectionStatus": "connected",
    }
    return {
        "status": "ok",
        "memberAuthority": SimpleNamespace(
            workspace_id=_account("wsp_", 1),
            user_id=_account("usr_", 2),
            email="owner@example.com",
        ),
        "user": {"email": "owner@example.com", "name": "Owner"},
        "inbox": inbox,
        "config": {"managedInboxes": [dict(inbox)]},
    }


class _Handler:
    headers = (("Cookie", "session=opaque"),)


class SendSemanticIntegrationTests(unittest.TestCase):
    def _run_gmail(
        self,
        payload: dict,
        *,
        send_payload: object = None,
        send_error: object = None,
    ) -> tuple[list[tuple[int, dict]], list[str], Mock, Mock]:
        responses: list[tuple[int, dict]] = []
        order: list[str] = []
        prepared = {
            "authority": _authority("google"),
            "semanticVersion": SEMANTIC_SCHEMA_VERSION,
            "hmacSecret": SECRET,
        }
        prepare = Mock(side_effect=lambda *args, **kwargs: order.append("prepare") or prepared)
        mint = Mock(side_effect=lambda *args, **kwargs: order.append("mint") or "signed-ref")
        send_result = (
            {"id": "sent-1", "threadId": "thread-1"}
            if send_payload is None
            else send_payload
        )
        with patch.object(SEND, "read_json_body", return_value=(payload, None)), patch.object(
            SEND, "find_forbidden_custom_request_fields", return_value=[]
        ), patch.object(
            SEND, "_semantic_authority_capture_enabled", return_value=True
        ), patch.object(
            SEND,
            "resolve_owned_mailbox",
            return_value=_owned_mailbox(),
        ) as owned_resolver, patch.object(
            SEND,
            "resolve_gmail_context",
            return_value={
                "status": "ok",
                "context": {
                    "mailbox_email": "owner@example.com",
                    "access_token": "server-token",
                    "refresh_attempted": False,
                },
            },
        ), patch.object(
            SEND, "_gmail_api_get_reply_source", return_value=({}, None)
        ), patch.object(
            SEND,
            "_validate_reply_source",
            return_value=(
                {
                    "threadId": "thread-1",
                    "inReplyTo": "<source@example.net>",
                    "references": "<source@example.net>",
                },
                None,
            ),
        ), patch.object(
            SEND,
            "_send_with_gmail_oauth",
            side_effect=lambda *args, **kwargs: (
                order.append("send") or send_result,
                send_error,
                None,
            ),
        ), patch.object(
            SEND, "_prepare_semantic_event_context", prepare
        ), patch.object(
            SEND, "_try_semantic_event_reference", mint
        ), patch.object(
            SEND,
            "send_json",
            side_effect=lambda _handler, status, body: responses.append((status, body)),
        ):
            SEND.handler._handle_post(_Handler())
            expected_call = (
                ((_Handler.headers, "mailbox-1"), {"include_member_authority": True})
                if "replyContext" in payload
                else ((_Handler.headers, "mailbox-1"), {})
            )
            self.assertEqual(
                (owned_resolver.call_args.args, owned_resolver.call_args.kwargs),
                expected_call,
            )
        return responses, order, prepare, mint

    def test_gmail_reply_and_reply_all_mint_only_after_confirmed_send(self):
        for cc in ("", "another@example.net"):
            with self.subTest(cc=cc):
                responses, order, prepare, mint = self._run_gmail(
                    _payload(reply=True, cc=cc)
                )
                self.assertEqual(responses[-1], (
                    200,
                    {
                        "ok": True,
                        "providerMessageId": "sent-1",
                        "providerThreadId": "thread-1",
                        "threadContinuityConfirmed": True,
                        "semanticEventRef": "signed-ref",
                    },
                ))
                self.assertEqual(order, ["prepare", "send", "mint"])
                prepare.assert_called_once()
                mint.assert_called_once()

    def test_failed_unconfirmed_or_non_reply_gmail_send_never_mints(self):
        failed, failed_order, _prepare, failed_mint = self._run_gmail(
            _payload(reply=True),
            send_error={"code": "gmail_unavailable"},
        )
        self.assertEqual(failed[-1][0], 502)
        self.assertEqual(failed_order, ["prepare", "send"])
        _prepare.assert_called_once()
        failed_mint.assert_not_called()

        unconfirmed, unconfirmed_order, unconfirmed_prepare, unconfirmed_mint = self._run_gmail(
            _payload(reply=True),
            send_payload={},
        )
        self.assertTrue(unconfirmed[-1][1]["ok"])
        self.assertFalse(unconfirmed[-1][1]["providerIdentityConfirmed"])
        self.assertEqual(unconfirmed_order, ["prepare", "send"])
        unconfirmed_prepare.assert_called_once()
        unconfirmed_mint.assert_not_called()

        new_message, order, prepare, mint = self._run_gmail(_payload(reply=False))
        self.assertEqual(new_message[-1][0], 200)
        self.assertEqual(order, ["send"])
        prepare.assert_not_called()
        mint.assert_not_called()

    def test_prepared_authority_mints_post_send_without_auth_or_config_io(self):
        current = _authority("google")
        prepared = {
            "authority": current,
            "semanticVersion": SEMANTIC_SCHEMA_VERSION,
            "hmacSecret": SECRET,
        }
        with patch.object(
            SEND,
            "priority_authority_from_owned_mailbox",
            side_effect=AssertionError("post-send auth lookup"),
        ), patch.object(
            SEND,
            "load_semantic_runtime_config",
            side_effect=AssertionError("post-send config lookup"),
        ):
            reference = SEND._try_semantic_event_reference(
                prepared,
                provider="google",
                provider_conversation_id="thread-1",
                latest_turn_id="sent-1",
                authored_text="Everything is complete.",
            )
        self.assertIsInstance(reference, str)
        claims = verify_outgoing_event_reference(reference, secret=SECRET)
        self.assertEqual(claims.mailbox_id, "mailbox-1")
        self.assertEqual(claims.latest_turn_id, "sent-1")

    def test_off_or_missing_hmac_preparation_is_fail_open(self):
        with patch.object(
            SEND,
            "load_semantic_runtime_config",
            return_value=SemanticRuntimeConfig(mode=SemanticMode.OFF, model=None),
        ), patch.object(
            SEND, "priority_authority_from_owned_mailbox"
        ) as builder:
            self.assertIsNone(SEND._prepare_semantic_event_context(
                _owned_mailbox(), mailbox_id="mailbox-1", provider="google"
            ))
            builder.assert_not_called()

        with patch.object(
            SEND,
            "load_semantic_runtime_config",
            return_value=SemanticRuntimeConfig(
                mode=SemanticMode.SHADOW,
                model="test-model",
            ),
        ), patch.object(
            SEND,
            "resolve_priority_hmac_secret",
            side_effect=EventReferenceError("configuration_invalid"),
        ), patch.object(
            SEND, "priority_authority_from_owned_mailbox"
        ) as builder:
            self.assertIsNone(SEND._prepare_semantic_event_context(
                _owned_mailbox(), mailbox_id="mailbox-1", provider="google"
            ))
            builder.assert_not_called()

    def test_preparation_reuses_owned_member_and_config_without_auth_io(self):
        owned = _owned_mailbox()
        with patch.object(
            SEND,
            "load_semantic_runtime_config",
            return_value=SemanticRuntimeConfig(
                mode=SemanticMode.SHADOW,
                model="test-model",
            ),
        ), patch.object(
            SEND,
            "resolve_priority_hmac_secret",
            return_value=SECRET,
        ):
            prepared = SEND._prepare_semantic_event_context(
                owned,
                mailbox_id="mailbox-1",
                provider="google",
            )
        self.assertIsNotNone(prepared)
        authority = prepared["authority"]
        self.assertEqual(authority.workspace_id, _account("wsp_", 1))
        self.assertEqual(authority.user_id, _account("usr_", 2))
        self.assertEqual(authority.member_email, "owner@example.com")
        self.assertEqual(authority.mailbox_id, "mailbox-1")
        self.assertEqual(authority.provider, "google")
        self.assertEqual(prepared["hmacSecret"], SECRET)

    def test_custom_semantic_thread_root_is_best_effort_for_legacy_send(self):
        context = {
            "sourceProviderFolder": "INBOX",
            "sourceImapUid": "8",
            "sourceUidValidity": "7",
        }
        result = {
            "ok": True,
            "status": "ok",
            "source": {
                "providerFolder": "INBOX",
                "imapUid": "8",
                "uidValidity": "7",
                "messageId": "<source@example.net>",
                "references": ["not-a-message-id"],
                "inReplyTo": None,
            },
            "error": None,
        }
        source, error = SEND._validate_custom_reply_source(result, context)
        self.assertIsNone(error)
        self.assertEqual(source["inReplyTo"], "<source@example.net>")
        self.assertNotIn("threadRoot", source)

    def _run_custom_reply(
        self,
        *,
        cc: str = "",
        send_error: Exception | None = None,
        open_error: Exception | None = None,
    ) -> tuple[list[tuple[int, dict]], Mock, Mock, Mock]:
        payload = _payload(reply=False, cc=cc)
        payload["imapReplyContext"] = {
            "sourceProviderFolder": "INBOX",
            "sourceImapUid": "8",
            "sourceUidValidity": "7",
        }
        mailbox = {
            "email": "owner@example.com",
            "imap": {
                "host": "imap.example.net",
                "port": 993,
                "ssl": True,
                "username": "owner@example.com",
                "password": "server-imap-secret",
            },
            "smtp": {
                "host": "smtp.example.net",
                "port": 465,
                "security": "ssl",
                "username": "owner@example.com",
                "password": "server-smtp-secret",
            },
        }
        source_result = {
            "ok": True,
            "status": "ok",
            "source": {
                "providerFolder": "INBOX",
                "imapUid": "8",
                "uidValidity": "7",
                "messageId": "<source@example.net>",
                "references": ["<root@example.net>"],
                "inReplyTo": None,
            },
            "error": None,
        }
        responses: list[tuple[int, dict]] = []
        connection = Mock()
        prepare = Mock(
            side_effect=AssertionError("custom SMTP must not prepare semantics")
        )
        mint = Mock(
            side_effect=AssertionError("custom SMTP must not mint semantics")
        )
        smtp_send = Mock(side_effect=send_error)
        with patch.object(SEND, "read_json_body", return_value=(payload, None)), patch.object(
            SEND, "find_forbidden_custom_request_fields", return_value=[]
        ), patch.object(
            SEND,
            "_semantic_authority_capture_enabled",
            side_effect=AssertionError("custom SMTP must not inspect semantic mode"),
        ), patch.object(
            SEND,
            "resolve_owned_mailbox",
            return_value={"status": "ok", "inbox": {"provider": "custom_imap"}},
        ) as owned_resolver, patch.object(
            SEND,
            "resolve_authenticated_imap_mailbox",
            return_value={"status": "ok", "mailbox": mailbox, "error": None},
        ), patch.object(
            SEND,
            "_open_custom_imap_connection",
            return_value=connection,
            side_effect=open_error,
        ), patch.object(
            SEND, "read_imap_reply_source", return_value=source_result
        ), patch.object(
            SEND,
            "_prepare_semantic_event_context",
            prepare,
        ), patch.object(
            SEND, "send_public_smtp_message", smtp_send
        ), patch.object(
            SEND, "_try_semantic_event_reference", mint
        ), patch.object(
            SEND,
            "send_json",
            side_effect=lambda _handler, status, body: responses.append((status, body)),
        ):
            SEND.handler._handle_post(_Handler())
            owned_resolver.assert_called_once_with(_Handler.headers, "mailbox-1")
        return responses, smtp_send, prepare, mint

    def test_custom_reply_and_reply_all_send_without_semantic_event(self):
        for cc in ("", "another@example.net"):
            with self.subTest(cc=cc):
                responses, smtp_send, prepare, mint = self._run_custom_reply(cc=cc)
                self.assertEqual(responses[-1], (200, {"ok": True}))
                smtp_send.assert_called_once()
                prepare.assert_not_called()
                mint.assert_not_called()

    def test_failed_custom_send_has_no_semantic_event(self):
        responses, smtp_send, prepare, mint = self._run_custom_reply(
            send_error=SEND.SmtpConnectionError("smtp_send_failed")
        )
        self.assertEqual(responses[-1][0], 502)
        self.assertEqual(responses[-1][1]["error"]["code"], "send_failed")
        smtp_send.assert_called_once()
        prepare.assert_not_called()
        mint.assert_not_called()

    def test_custom_send_is_unchanged_during_semantic_store_outage(self):
        responses, smtp_send, prepare, mint = self._run_custom_reply()
        self.assertEqual(responses[-1], (200, {"ok": True}))
        smtp_send.assert_called_once()
        prepare.assert_not_called()
        mint.assert_not_called()

    def test_custom_reply_source_uses_policy_enforced_verified_imap(self):
        mailbox = {
            "imap": {
                "host": "imap.example.net",
                "port": 993,
                "ssl": True,
                "username": "owner@example.com",
                "password": "server-imap-secret",
            }
        }
        connection = Mock()
        with patch.object(
            SEND,
            "connect_mailbox_with_settings",
            return_value=connection,
        ) as secure_connect, patch.object(
            SEND.imaplib,
            "IMAP4_SSL",
        ) as raw_tls:
            self.assertIs(SEND._open_custom_imap_connection(mailbox), connection)

        secure_connect.assert_called_once_with(
            "imap.example.net",
            993,
            "owner@example.com",
            "server-imap-secret",
            True,
            timeout=30,
        )
        raw_tls.assert_not_called()

    def test_custom_reply_policy_failure_never_reaches_smtp(self):
        for code in (
            "imap_destination_not_allowed",
            "imap_peer_mismatch",
            "imap_dns_failed",
        ):
            with self.subTest(code=code):
                responses, smtp_send, prepare, mint = self._run_custom_reply(
                    open_error=RuntimeError(code)
                )
                self.assertEqual(responses[-1][0], 503)
                smtp_send.assert_not_called()
                prepare.assert_not_called()
                mint.assert_not_called()

    def test_custom_provider_can_never_use_post_send_ticket_helper(self):
        reference = SEND._try_semantic_event_reference(
            {
                "authority": _authority("custom_imap"),
                "semanticVersion": SEMANTIC_SCHEMA_VERSION,
                "hmacSecret": SECRET,
            },
            provider="custom_imap",
            provider_conversation_id="root@example.net",
            latest_turn_id="sent@example.net",
            authored_text="Everything is complete.",
        )
        self.assertIsNone(reference)


if __name__ == "__main__":
    unittest.main()
