from __future__ import annotations

import base64
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
from unittest.mock import Mock, call, patch


CURRENT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = CURRENT_DIR.parent.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import authenticated_gmail
import oauth_token_store


def _load_route(filename: str, name: str):
    spec = importlib.util.spec_from_file_location(name, CURRENT_DIR / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load active route {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


message_action = _load_route(
    "message-action.py",
    "message_action_archive_contract_test",
)


GMAIL_MESSAGE_ID = "18f-provider-message"
GMAIL_THREAD_ID = "18f-provider-thread"
MAILBOX_ID = "server-mailbox"
IMAP_PASSWORD = "test-only-imap-password-never-return"
IMAP_USERNAME = "server-imap-user"
IMAP_HOST = "imap.test.invalid"
GMAIL_ACCESS_TOKEN = "test-only-gmail-access-token-never-return"
_DEFAULT = object()


class FakeHandler:
    def __init__(self, payload: dict, *, headers: dict | None = None):
        body = json.dumps(payload).encode("utf-8")
        self.headers = {
            "content-length": str(len(body)),
            **(headers or {}),
        }
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status = None
        self.response_headers: list[tuple[str, str]] = []
        self.path = "/api/inboxes/message-action"

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        pass

    def response(self) -> dict:
        return json.loads(self.wfile.getvalue())


class FakeResponse:
    def __init__(
        self,
        body: bytes | str,
        *,
        headers: dict[str, str] | None = None,
    ):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.headers = headers or {}
        self._stream = io.BytesIO(self.body)

    def read(self, amount=-1):
        return self._stream.read(amount)

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False


class RecordingMailbox:
    def __init__(self, *, logout_error: Exception | None = None):
        self.logout_error = logout_error
        self.logout_count = 0
        self.shutdown_count = 0
        self.unsafe_calls: list[tuple] = []

    def logout(self):
        self.logout_count += 1
        if self.logout_error is not None:
            raise self.logout_error
        return "BYE", []

    def shutdown(self):
        self.shutdown_count += 1

    def uid(self, *arguments):
        self.unsafe_calls.append(("uid", *arguments))
        raise AssertionError("the route must delegate IMAP protocol work")

    def copy(self, *arguments):
        self.unsafe_calls.append(("copy", *arguments))
        raise AssertionError("the route must not add a COPY fallback")

    def store(self, *arguments):
        self.unsafe_calls.append(("store", *arguments))
        raise AssertionError("the route must not add a STORE fallback")

    def expunge(self, *arguments):
        self.unsafe_calls.append(("expunge", *arguments))
        raise AssertionError("the route must not expunge")


def _gmail_context(
    *,
    scope: object = message_action.GMAIL_MODIFY_SCOPE,
    access_token: str = GMAIL_ACCESS_TOKEN,
    refresh_attempted: bool = False,
) -> dict:
    return {
        "mailbox_id": MAILBOX_ID,
        "mailbox_email": "owned@gmail.test",
        "owner_email": "owner@example.test",
        "access_token": access_token,
        "refresh_attempted": refresh_attempted,
        "scope": scope,
    }


def _owned_mailbox(provider: str) -> dict:
    return {
        "status": "ok",
        "user": {"email": "owner@example.test"},
        "inbox": {
            "id": MAILBOX_ID,
            "email": "owned@example.test",
            "provider": provider,
        },
    }


def _owned_error(status_code: int, code: str) -> dict:
    return {
        "status": "error",
        "status_code": status_code,
        "error": {
            "ok": False,
            "error": {
                "code": code,
                "message": "safe ownership failure",
            },
        },
    }


def _resolved_imap_mailbox() -> dict:
    return {
        "status": "ok",
        "mailbox": {
            "mailboxId": MAILBOX_ID,
            "ownerEmail": "owner@example.test",
            "email": "owned@example.test",
            "imap": {
                "host": IMAP_HOST,
                "port": 993,
                "ssl": True,
                "username": IMAP_USERNAME,
                "password": IMAP_PASSWORD,
            },
        },
        "error": None,
    }


def _gmail_message(
    provider_message_id: str = GMAIL_MESSAGE_ID,
    *,
    provider_folder: str = "Archive",
) -> dict:
    labels = ["STARRED"] if provider_folder == "Archive" else ["INBOX"]
    return {
        "id": "rfc-message@example.test",
        "rfcMessageId": "rfc-message@example.test",
        "providerMessageId": provider_message_id,
        "providerThreadId": GMAIL_THREAD_ID,
        "providerFolder": provider_folder,
        "labelIds": labels,
    }


def _gmail_raw_message() -> str:
    raw = (
        b"Message-Id: <rfc-message@example.test>\r\n"
        b"From: sender@example.test\r\n"
        b"To: owned@gmail.test\r\n"
        b"Subject: Provider snapshot\r\n"
        b"\r\n"
        b"Body"
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class RecordingGmailReadback:
    def __init__(
        self,
        *,
        inbox_messages: list[dict] | None = None,
        archive_messages: list[dict] | None = None,
        fail_folder: str | None = None,
    ):
        self.inbox_messages = list(inbox_messages or [])
        self.archive_messages = list(
            [_gmail_message()]
            if archive_messages is None
            else archive_messages
        )
        self.fail_folder = fail_folder
        self.calls: list[tuple[dict, dict]] = []

    def __call__(self, context: dict, **kwargs):
        self.calls.append((dict(context), dict(kwargs)))
        provider_folder = kwargs["provider_folder"]
        if provider_folder == self.fail_folder:
            return {
                "status": "error",
                "context": context,
                "snapshot": None,
                "error": {
                    "code": "provider-read-failed",
                    "detail": GMAIL_ACCESS_TOKEN,
                },
            }
        messages = (
            self.inbox_messages
            if provider_folder == "Inbox"
            else self.archive_messages
        )
        return {
            "status": "ok",
            "context": context,
            "snapshot": {
                "serverMailboxId": MAILBOX_ID,
                "messages": list(messages),
                "uidValidity": "gmail-api",
                "providerFolder": provider_folder,
            },
        }


def _source_imap_identity(
    *,
    provider_folder: str = "INBOX",
    imap_uid: str = "123",
    uid_validity: str = "456",
) -> dict:
    return {
        "providerFolder": provider_folder,
        "imapUid": imap_uid,
        "uidValidity": uid_validity,
        "fingerprint": "stable-provider-message-fingerprint",
        "rfcMessageId": "rfc-message@example.test",
    }


def _imap_foundation_success() -> dict:
    return {
        "ok": True,
        "status": "ok",
        "source_folder": "INBOX",
        "archive_folder": "Archive",
        "uid": "123",
        "uid_validity": "456",
        "confirmation": "source_removed",
        "error": None,
    }


class RecordingImapReadback:
    def __init__(
        self,
        *,
        inbox_uid_set: list[str] | None = None,
        archive_uid_set: list[str] | None = None,
        archive_messages: list[dict] | None = None,
        archive_identities: dict[str, dict] | None = None,
        fail_folder: str | None = None,
    ):
        self.inbox_uid_set = list(inbox_uid_set or [])
        self.archive_uid_set = list(archive_uid_set or ["900"])
        self.archive_messages = list(
            [
                {
                    "id": "rfc-message@example.test",
                    "rfcMessageId": "rfc-message@example.test",
                    "imapUid": "900",
                    "providerFolder": "Archive",
                }
            ]
            if archive_messages is None
            else archive_messages
        )
        self.archive_identities = dict(
            {
                "900": _source_imap_identity(
                    provider_folder="Archive",
                    imap_uid="900",
                    uid_validity="789",
                )
            }
            if archive_identities is None
            else archive_identities
        )
        self.fail_folder = fail_folder
        self.calls: list[tuple[object, dict]] = []

    def __call__(self, mailbox, **kwargs):
        self.calls.append((mailbox, dict(kwargs)))
        folder = kwargs["folder"]
        if folder == self.fail_folder:
            return {
                "status": "error",
                "snapshot": None,
                "identities": {},
                "error": {
                    "code": "snapshot_fetch_failed",
                    "detail": IMAP_PASSWORD,
                },
            }
        if folder == "INBOX":
            return {
                "status": "ok",
                "snapshot": {
                    "serverMailboxId": MAILBOX_ID,
                    "messages": [],
                    "uidValidity": "456",
                    "providerFolder": "INBOX",
                    "imapUidSet": list(self.inbox_uid_set),
                },
                "identities": {},
            }
        return {
            "status": "ok",
            "snapshot": {
                "serverMailboxId": MAILBOX_ID,
                "messages": list(self.archive_messages),
                "uidValidity": "789",
                "providerFolder": "Archive",
                "imapUidSet": list(self.archive_uid_set),
            },
            "identities": dict(self.archive_identities),
        }


def _run_gmail_archive(
    *,
    payload: dict | None = None,
    context: dict | None = None,
    owned_result: dict | None = None,
    modify_results: list[tuple[dict | None, dict | None]] | None = None,
    readback: RecordingGmailReadback | None = None,
    refresh_result: object = _DEFAULT,
):
    request = FakeHandler(
        payload
        or {
            "mailboxId": MAILBOX_ID,
            "messageId": GMAIL_MESSAGE_ID,
            "action": "archive",
        }
    )
    context = context or _gmail_context()
    readback = readback or RecordingGmailReadback()
    owned = Mock(return_value=owned_result or _owned_mailbox("google"))
    gmail_context = Mock(
        return_value={"status": "ok", "context": context}
    )
    modify = Mock()
    if modify_results is None:
        modify.return_value = (
            {
                "id": GMAIL_MESSAGE_ID,
                "labelIds": ["STARRED"],
            },
            None,
        )
    else:
        modify.side_effect = list(modify_results)
    if refresh_result is _DEFAULT:
        refresh = Mock(
            side_effect=AssertionError("unexpected Gmail token refresh")
        )
    else:
        refresh = Mock(return_value=refresh_result)

    with patch.object(
        message_action,
        "resolve_owned_mailbox",
        owned,
    ), patch.object(
        message_action,
        "resolve_gmail_context",
        gmail_context,
    ), patch.object(
        message_action,
        "_gmail_modify_request",
        modify,
    ), patch.object(
        message_action,
        "refresh_gmail_context",
        refresh,
    ), patch.object(
        message_action,
        "read_gmail_folder_snapshot",
        side_effect=readback,
    ) as snapshot:
        message_action.handler.do_POST(request)

    return {
        "handler": request,
        "owned": owned,
        "gmail_context": gmail_context,
        "modify": modify,
        "refresh": refresh,
        "snapshot": snapshot,
        "readback": readback,
    }


def _run_imap_archive(
    *,
    payload: dict | None = None,
    resolved: dict | None = None,
    mailbox: RecordingMailbox | None = None,
    identity_result: dict | None = None,
    foundation_result: dict | None = None,
    readback: RecordingImapReadback | None = None,
):
    request = FakeHandler(
        payload
        or {
            "mailboxId": MAILBOX_ID,
            "folder": "INBOX",
            "uid": "123",
            "uidValidity": "456",
            "action": "archive",
        }
    )
    mailbox = mailbox or RecordingMailbox()
    readback = readback or RecordingImapReadback()
    owned = Mock(return_value=_owned_mailbox("custom_imap"))
    authenticated = Mock(
        return_value=resolved or _resolved_imap_mailbox()
    )
    connect = Mock(return_value=mailbox)
    identity = Mock(
        return_value=identity_result
        or {
            "status": "ok",
            "identity": _source_imap_identity(),
        }
    )
    foundation = Mock(
        return_value=foundation_result or _imap_foundation_success()
    )

    with patch.object(
        message_action,
        "resolve_owned_mailbox",
        owned,
    ), patch.object(
        message_action,
        "resolve_authenticated_imap_mailbox",
        authenticated,
    ), patch.object(
        message_action,
        "connect_mailbox_with_settings",
        connect,
    ), patch.object(
        message_action,
        "read_imap_message_identity",
        identity,
    ), patch.object(
        message_action,
        "archive_imap_message",
        foundation,
    ), patch.object(
        message_action,
        "read_imap_folder_snapshot",
        side_effect=readback,
    ) as snapshot:
        message_action.handler.do_POST(request)

    return {
        "handler": request,
        "mailbox": mailbox,
        "owned": owned,
        "authenticated": authenticated,
        "connect": connect,
        "identity": identity,
        "foundation": foundation,
        "snapshot": snapshot,
        "readback": readback,
    }


class ArchiveRequestAndAuthorityTests(unittest.TestCase):
    def test_authority_failures_stop_before_provider_resolution(self):
        cases = (
            ("other_owner", _owned_error(404, "gmail_connection_not_found")),
            ("forged_mailbox", _owned_error(404, "gmail_connection_not_found")),
            ("unknown_mailbox", _owned_error(404, "gmail_connection_not_found")),
            ("no_session", _owned_error(401, "unauthorized")),
        )
        for name, owned_result in cases:
            with self.subTest(name=name):
                request = FakeHandler(
                    {
                        "mailboxId": "not-authorized",
                        "messageId": GMAIL_MESSAGE_ID,
                        "action": "archive",
                    }
                )
                with patch.object(
                    message_action,
                    "resolve_owned_mailbox",
                    return_value=owned_result,
                ) as owned, patch.object(
                    message_action,
                    "resolve_gmail_context",
                ) as gmail_context, patch.object(
                    message_action,
                    "resolve_authenticated_imap_mailbox",
                ) as imap_context, patch.object(
                    message_action,
                    "_gmail_modify_request",
                ) as modify:
                    message_action.handler.do_POST(request)

                self.assertEqual(request.status, owned_result["status_code"])
                self.assertEqual(
                    request.response()["error"]["code"],
                    owned_result["error"]["error"]["code"],
                )
                owned.assert_called_once_with(
                    request.headers,
                    "not-authorized",
                )
                gmail_context.assert_not_called()
                imap_context.assert_not_called()
                modify.assert_not_called()

    def test_unsupported_provider_is_rejected_without_provider_calls(self):
        request = FakeHandler(
            {
                "mailboxId": MAILBOX_ID,
                "messageId": GMAIL_MESSAGE_ID,
                "action": "archive",
            }
        )
        with patch.object(
            message_action,
            "resolve_owned_mailbox",
            return_value=_owned_mailbox("microsoft"),
        ), patch.object(
            message_action,
            "_gmail_modify_request",
        ) as gmail_modify, patch.object(
            message_action,
            "resolve_authenticated_imap_mailbox",
        ) as imap_resolver:
            message_action.handler.do_POST(request)
        self.assertEqual(request.status, 400)
        self.assertEqual(
            request.response()["error"]["code"],
            "unsupported_provider",
        )
        gmail_modify.assert_not_called()
        imap_resolver.assert_not_called()

    def test_client_authority_connection_target_and_bulk_fields_are_rejected(self):
        forbidden_cases = (
            {"unexpected": "value"},
            {"provider": "google"},
            {"email": "forged@example.test"},
            {"host": "evil.invalid"},
            {"port": 993},
            {"username": "attacker"},
            {"password": "attacker-secret"},
            {"accessToken": "attacker-token"},
            {"refreshToken": "attacker-refresh"},
            {"credentialGeneration": "forged-generation"},
            {"archiveFolder": "Client Archive"},
            {"messageIds": [GMAIL_MESSAGE_ID]},
            {"uids": ["123", "124"]},
            {"connection": {"password": "nested-secret"}},
        )
        base = {
            "mailboxId": MAILBOX_ID,
            "messageId": GMAIL_MESSAGE_ID,
            "action": "archive",
        }
        for extra in forbidden_cases:
            with self.subTest(extra=extra):
                request = FakeHandler({**base, **extra})
                with patch.object(
                    message_action,
                    "resolve_owned_mailbox",
                ) as owned:
                    message_action.handler.do_POST(request)
                self.assertEqual(request.status, 400)
                self.assertEqual(
                    request.response()["error"]["code"],
                    "forbidden_connection_fields",
                )
                owned.assert_not_called()

    def test_gmail_contract_rejects_imap_fields_after_authority_resolution(self):
        request = FakeHandler(
            {
                "mailboxId": MAILBOX_ID,
                "messageId": GMAIL_MESSAGE_ID,
                "folder": "INBOX",
                "action": "archive",
            }
        )
        with patch.object(
            message_action,
            "resolve_owned_mailbox",
            return_value=_owned_mailbox("google"),
        ), patch.object(
            message_action,
            "resolve_gmail_context",
        ) as gmail_context:
            message_action.handler.do_POST(request)
        self.assertEqual(request.status, 400)
        self.assertEqual(
            request.response()["error"]["code"],
            "invalid_request",
        )
        gmail_context.assert_not_called()


class GmailArchiveTransportTests(unittest.TestCase):
    def test_modify_uses_exact_message_endpoint_and_only_removes_inbox(self):
        provider_id = "18f/provider:id"
        response = FakeResponse(
            json.dumps(
                {
                    "id": provider_id,
                    "labelIds": ["STARRED"],
                }
            )
        )
        with patch.object(
            message_action,
            "urlopen",
            return_value=response,
        ) as transport:
            payload, error = message_action._gmail_modify_request(
                GMAIL_ACCESS_TOKEN,
                provider_id,
                "archive",
            )

        self.assertIsNone(error)
        self.assertEqual(payload["id"], provider_id)
        transport.assert_called_once()
        request = transport.call_args.args[0]
        self.assertEqual(transport.call_args.kwargs, {"timeout": 20})
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(
            request.full_url,
            (
                f"{message_action.GMAIL_API_BASE_URL}/messages/"
                f"{quote(provider_id, safe='')}/modify"
            ),
        )
        self.assertNotIn("/threads/", request.full_url)
        self.assertNotIn("/trash", request.full_url)
        self.assertNotIn("/delete", request.full_url)
        self.assertEqual(
            json.loads(request.data),
            {"removeLabelIds": ["INBOX"]},
        )
        self.assertNotIn("TRASH", request.data.decode("utf-8"))
        self.assertEqual(
            request.get_header("Authorization"),
            f"Bearer {GMAIL_ACCESS_TOKEN}",
        )

    def test_invalid_json_and_oversized_modify_responses_fail_closed(self):
        cases = (
            (
                "invalid_json",
                FakeResponse(b"{not-json"),
                "gmail_response_invalid",
            ),
            (
                "oversized",
                FakeResponse(
                    b"{}",
                    headers={
                        "Content-Length": str(
                            message_action.MAX_GMAIL_RESPONSE_BYTES + 1
                        )
                    },
                ),
                "gmail_response_too_large",
            ),
        )
        for name, provider_response, expected_code in cases:
            with self.subTest(name=name), patch.object(
                message_action,
                "urlopen",
                return_value=provider_response,
            ):
                payload, error = message_action._gmail_modify_request(
                    GMAIL_ACCESS_TOKEN,
                    GMAIL_MESSAGE_ID,
                    "archive",
                )
            self.assertIsNone(payload)
            self.assertEqual(error, {"code": expected_code})

    def test_route_maps_invalid_json_non_object_and_oversized_transport_responses_to_unconfirmed(self):
        response_factories = (
            ("invalid_json", lambda: FakeResponse(b"{not-json")),
            ("non_object", lambda: FakeResponse(b"[]")),
            (
                "oversized",
                lambda: FakeResponse(
                    b"{}",
                    headers={
                        "Content-Length": str(
                            message_action.MAX_GMAIL_RESPONSE_BYTES + 1
                        )
                    },
                ),
            ),
        )
        for name, response_factory in response_factories:
            with self.subTest(name=name):
                request = FakeHandler(
                    {
                        "mailboxId": MAILBOX_ID,
                        "messageId": GMAIL_MESSAGE_ID,
                        "action": "archive",
                    }
                )
                with patch.object(
                    message_action,
                    "resolve_owned_mailbox",
                    return_value=_owned_mailbox("google"),
                ), patch.object(
                    message_action,
                    "resolve_gmail_context",
                    return_value={
                        "status": "ok",
                        "context": _gmail_context(),
                    },
                ), patch.object(
                    message_action,
                    "urlopen",
                    return_value=response_factory(),
                ) as transport, patch.object(
                    message_action,
                    "read_gmail_folder_snapshot",
                ) as readback:
                    message_action.handler.do_POST(request)

                self.assertEqual(request.status, 502)
                self.assertEqual(
                    request.response()["error"]["code"],
                    "gmail_archive_unconfirmed",
                )
                transport.assert_called_once()
                readback.assert_not_called()
                self.assertNotIn(
                    GMAIL_ACCESS_TOKEN,
                    json.dumps(request.response()),
                )

    def test_semantically_invalid_provider_confirmation_is_unconfirmed(self):
        invalid_responses = (
            {"id": "different", "labelIds": []},
            {"labelIds": []},
            {"id": GMAIL_MESSAGE_ID},
            {"id": GMAIL_MESSAGE_ID, "labelIds": "STARRED"},
            {"id": GMAIL_MESSAGE_ID, "labelIds": ["INBOX"]},
            {"id": GMAIL_MESSAGE_ID, "labelIds": ["TRASH"]},
            {"id": GMAIL_MESSAGE_ID, "labelIds": ["STARRED", 1]},
            {
                "id": GMAIL_MESSAGE_ID,
                "labelIds": ["STARRED", "STARRED"],
            },
        )
        for provider_response in invalid_responses:
            with self.subTest(provider_response=provider_response):
                result = _run_gmail_archive(
                    modify_results=[(provider_response, None)]
                )
                self.assertEqual(result["handler"].status, 502)
                self.assertEqual(
                    result["handler"].response()["error"]["code"],
                    "gmail_archive_unconfirmed",
                )
                result["snapshot"].assert_not_called()


class GmailArchiveScopeAndFailureTests(unittest.TestCase):
    def test_missing_or_unproven_modify_scope_stops_before_provider_call(self):
        for scope in (
            None,
            "",
            "openid email",
            "https://www.googleapis.com/auth/gmail.readonly",
            ["https://www.googleapis.com/auth/gmail.modify"],
        ):
            with self.subTest(scope=scope):
                result = _run_gmail_archive(
                    context=_gmail_context(scope=scope)
                )
                self.assertEqual(result["handler"].status, 403)
                self.assertEqual(
                    result["handler"].response()["error"]["code"],
                    "gmail_modify_scope_required",
                )
                result["modify"].assert_not_called()
                result["snapshot"].assert_not_called()

    def test_modify_and_full_mail_scopes_are_accepted(self):
        scopes = (
            message_action.GMAIL_MODIFY_SCOPE,
            f"openid {message_action.GMAIL_FULL_MAIL_SCOPE} email",
        )
        for scope in scopes:
            with self.subTest(scope=scope):
                result = _run_gmail_archive(
                    context=_gmail_context(scope=scope)
                )
                self.assertEqual(result["handler"].status, 200)
                result["modify"].assert_called_once_with(
                    GMAIL_ACCESS_TOKEN,
                    GMAIL_MESSAGE_ID,
                    "archive",
                )

    def test_existing_single_refresh_path_is_used_at_most_once(self):
        fresh_context = _gmail_context(
            access_token="fresh-test-token",
            refresh_attempted=True,
        )
        result = _run_gmail_archive(
            modify_results=[
                (None, {"code": "gmail_token_invalid"}),
                (
                    {
                        "id": GMAIL_MESSAGE_ID,
                        "labelIds": ["STARRED"],
                    },
                    None,
                ),
            ],
            refresh_result={
                "status": "ok",
                "context": fresh_context,
            },
        )
        self.assertEqual(result["handler"].status, 200)
        self.assertEqual(
            result["modify"].call_args_list,
            [
                call(
                    GMAIL_ACCESS_TOKEN,
                    GMAIL_MESSAGE_ID,
                    "archive",
                ),
                call(
                    "fresh-test-token",
                    GMAIL_MESSAGE_ID,
                    "archive",
                ),
            ],
        )
        result["refresh"].assert_called_once()

        already_refreshed = _run_gmail_archive(
            context=_gmail_context(refresh_attempted=True),
            modify_results=[
                (None, {"code": "gmail_token_invalid"}),
            ],
        )
        self.assertEqual(already_refreshed["handler"].status, 401)
        already_refreshed["modify"].assert_called_once()
        already_refreshed["refresh"].assert_not_called()

    def test_permission_timeout_and_rate_limit_never_report_success(self):
        cases = (
            ("gmail_permission_denied", 403, "gmail_archive_failed"),
            ("gmail_unavailable", 502, "gmail_archive_unconfirmed"),
            ("gmail_rate_limited", 502, "gmail_rate_limited"),
            ("gmail_message_action_failed", 502, "gmail_archive_failed"),
        )
        for provider_code, expected_status, expected_code in cases:
            with self.subTest(provider_code=provider_code):
                result = _run_gmail_archive(
                    modify_results=[
                        (None, {"code": provider_code}),
                    ]
                )
                self.assertEqual(
                    result["handler"].status,
                    expected_status,
                )
                self.assertEqual(
                    result["handler"].response()["error"]["code"],
                    expected_code,
                )
                result["snapshot"].assert_not_called()


class GmailArchiveScopePersistenceTests(unittest.TestCase):
    def test_refresh_token_record_preserves_existing_scope_when_omitted(self):
        record = oauth_token_store.build_google_token_record(
            email="owned@gmail.test",
            owner_email="owner@example.test",
            token_payload={"access_token": "fresh-access-token"},
            existing_record={
                "refresh_token": "stored-refresh-token",
                "scope": message_action.GMAIL_MODIFY_SCOPE,
            },
        )
        self.assertEqual(
            record["scope"],
            message_action.GMAIL_MODIFY_SCOPE,
        )

    def test_refresh_context_keeps_previously_proven_scope_if_omitted(self):
        context = _gmail_context()
        with patch.object(
            authenticated_gmail,
            "refresh_google_token_record",
            return_value=(
                {
                    "owner_email": context["owner_email"],
                    "access_token": "fresh-access-token",
                    "scope": None,
                },
                None,
            ),
        ):
            result = authenticated_gmail.refresh_gmail_context(context)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            result["context"]["scope"],
            message_action.GMAIL_MODIFY_SCOPE,
        )
        self.assertTrue(result["context"]["refresh_attempted"])


class GmailProviderSnapshotTests(unittest.TestCase):
    def _read_snapshot(
        self,
        *,
        provider_folder: str,
        detail: dict,
        required_message_id: str | None = None,
    ) -> tuple[dict, list[str]]:
        from api.inboxes.gmail_snapshot import read_gmail_folder_snapshot

        paths: list[str] = []
        responses = [
            (
                {"messages": []}
                if required_message_id is not None
                else {"messages": [{"id": GMAIL_MESSAGE_ID}]}
            ),
            detail,
        ]

        def request(context, path):
            paths.append(path)
            return responses.pop(0), None, context, None

        result = read_gmail_folder_snapshot(
            _gmail_context(),
            provider_folder=provider_folder,
            limit=100,
            focus_preferences=None,
            strict=True,
            required_message_id=required_message_id,
            request_with_one_refresh=request,
        )
        return result, paths

    def test_strict_snapshot_keeps_provider_identities_separate(self):
        result, paths = self._read_snapshot(
            provider_folder="Inbox",
            detail={
                "id": GMAIL_MESSAGE_ID,
                "threadId": GMAIL_THREAD_ID,
                "labelIds": ["INBOX", "UNREAD"],
                "raw": _gmail_raw_message(),
            },
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            result["snapshot"]["serverMailboxId"],
            MAILBOX_ID,
        )
        message = result["snapshot"]["messages"][0]
        self.assertEqual(message["providerMessageId"], GMAIL_MESSAGE_ID)
        self.assertEqual(message["providerThreadId"], GMAIL_THREAD_ID)
        self.assertEqual(
            message["rfcMessageId"],
            "rfc-message@example.test",
        )
        self.assertEqual(message["providerFolder"], "Inbox")
        self.assertEqual(message["serverMailboxId"], MAILBOX_ID)
        self.assertNotIn("imapUid", message)
        self.assertIn("labelIds=INBOX", paths[0])

    def test_archive_targeted_read_and_strict_detail_validation(self):
        success, paths = self._read_snapshot(
            provider_folder="Archive",
            required_message_id=GMAIL_MESSAGE_ID,
            detail={
                "id": GMAIL_MESSAGE_ID,
                "threadId": GMAIL_THREAD_ID,
                "labelIds": ["STARRED"],
                "raw": _gmail_raw_message(),
            },
        )
        self.assertEqual(success["status"], "ok")
        self.assertIn("-label%3Ainbox", paths[0])
        self.assertIn(
            f"/messages/{quote(GMAIL_MESSAGE_ID, safe='')}?format=raw",
            paths[1],
        )

        invalid_details = (
            {
                "id": GMAIL_MESSAGE_ID,
                "labelIds": ["STARRED"],
                "raw": _gmail_raw_message(),
            },
            {
                "id": "different-provider-message",
                "threadId": GMAIL_THREAD_ID,
                "labelIds": ["STARRED"],
                "raw": _gmail_raw_message(),
            },
            {
                "id": GMAIL_MESSAGE_ID,
                "threadId": GMAIL_THREAD_ID,
                "labelIds": ["STARRED", "STARRED"],
                "raw": _gmail_raw_message(),
            },
            {
                "id": GMAIL_MESSAGE_ID,
                "threadId": GMAIL_THREAD_ID,
                "labelIds": ["SENT"],
                "raw": _gmail_raw_message(),
            },
        )
        for detail in invalid_details:
            with self.subTest(detail=detail):
                result, _ = self._read_snapshot(
                    provider_folder="Archive",
                    required_message_id=GMAIL_MESSAGE_ID,
                    detail=detail,
                )
                self.assertEqual(result["status"], "error")
                self.assertEqual(
                    result["error"]["code"],
                    "gmail_response_invalid",
                )


class GmailArchiveReadbackTests(unittest.TestCase):
    def test_success_returns_two_fresh_snapshots_and_explicit_identities(self):
        readback = RecordingGmailReadback()
        result = _run_gmail_archive(readback=readback)

        self.assertEqual(result["handler"].status, 200)
        response = result["handler"].response()
        self.assertEqual(
            response["archivedMessageIdentity"],
            {
                "serverMailboxId": MAILBOX_ID,
                "providerMessageId": GMAIL_MESSAGE_ID,
                "providerThreadId": GMAIL_THREAD_ID,
                "providerFolder": "Archive",
                "rfcMessageId": "rfc-message@example.test",
            },
        )
        self.assertEqual(
            set(response["folders"]),
            {"Inbox", "Archive"},
        )
        self.assertEqual(
            response["folders"]["Inbox"]["messages"],
            [],
        )
        self.assertEqual(
            response["folders"]["Archive"]["messages"][0][
                "providerMessageId"
            ],
            GMAIL_MESSAGE_ID,
        )
        self.assertNotIn(
            "imapUid",
            response["folders"]["Archive"]["messages"][0],
        )
        self.assertEqual(len(readback.calls), 2)
        inbox_context, inbox_arguments = readback.calls[0]
        archive_context, archive_arguments = readback.calls[1]
        self.assertEqual(
            inbox_arguments,
            {
                "provider_folder": "Inbox",
                "limit": message_action.GMAIL_ARCHIVE_READBACK_LIMIT,
                "focus_preferences": None,
                "strict": True,
                "request_with_one_refresh": (
                    message_action._gmail_get_with_one_refresh
                ),
            },
        )
        self.assertEqual(
            archive_arguments,
            {
                "provider_folder": "Archive",
                "limit": message_action.GMAIL_ARCHIVE_READBACK_LIMIT,
                "focus_preferences": None,
                "strict": True,
                "required_message_id": GMAIL_MESSAGE_ID,
                "request_with_one_refresh": (
                    message_action._gmail_get_with_one_refresh
                ),
            },
        )
        self.assertEqual(inbox_context["mailbox_id"], MAILBOX_ID)
        self.assertEqual(archive_context["mailbox_id"], MAILBOX_ID)
        serialized = json.dumps(response)
        self.assertNotIn(GMAIL_ACCESS_TOKEN, serialized)
        self.assertNotIn("refresh_token", serialized)

    def test_archive_query_excludes_non_archive_product_folders(self):
        from api.inboxes.gmail_snapshot import read_gmail_folder_snapshot

        paths: list[str] = []

        def request(context, path):
            paths.append(path)
            return {"messages": []}, None, context, None

        result = read_gmail_folder_snapshot(
            _gmail_context(),
            provider_folder="Archive",
            limit=100,
            focus_preferences=None,
            strict=True,
            request_with_one_refresh=request,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(paths), 1)
        query = parse_qs(urlsplit(paths[0]).query)
        archive_query = query["q"][0]
        for excluded_label in (
            "inbox",
            "trash",
            "spam",
            "drafts",
            "sent",
        ):
            self.assertIn(
                f"-label:{excluded_label}",
                archive_query,
            )

    def test_failed_or_inconsistent_readback_is_uncertain_and_never_retries_mutation(self):
        cases = (
            (
                "provider_failure",
                RecordingGmailReadback(fail_folder="Archive"),
            ),
            (
                "still_in_inbox",
                RecordingGmailReadback(
                    inbox_messages=[
                        _gmail_message(provider_folder="Inbox")
                    ]
                ),
            ),
            (
                "missing_from_archive",
                RecordingGmailReadback(archive_messages=[]),
            ),
        )
        for name, readback in cases:
            with self.subTest(name=name):
                result = _run_gmail_archive(readback=readback)
                self.assertEqual(result["handler"].status, 502)
                response = result["handler"].response()
                self.assertFalse(response["ok"])
                self.assertEqual(
                    response["status"],
                    "mutation_confirmed_readback_failed",
                )
                self.assertEqual(
                    response["error"]["code"],
                    "archive_readback_failed",
                )
                result["modify"].assert_called_once_with(
                    GMAIL_ACCESS_TOKEN,
                    GMAIL_MESSAGE_ID,
                    "archive",
                )
                self.assertNotIn(GMAIL_ACCESS_TOKEN, json.dumps(response))

    def test_duplicate_archive_provider_id_is_uncertain_without_mutation_retry(self):
        refresh_token = "test-only-gmail-refresh-token-never-return"
        context = {
            **_gmail_context(),
            "refresh_token": refresh_token,
        }
        readback = RecordingGmailReadback(
            inbox_messages=[],
            archive_messages=[
                _gmail_message(),
                _gmail_message(),
            ],
        )
        result = _run_gmail_archive(
            context=context,
            readback=readback,
        )

        self.assertEqual(result["handler"].status, 502)
        response = result["handler"].response()
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["status"],
            "mutation_confirmed_readback_failed",
        )
        self.assertEqual(
            response["error"]["code"],
            "archive_readback_failed",
        )
        result["modify"].assert_called_once_with(
            GMAIL_ACCESS_TOKEN,
            GMAIL_MESSAGE_ID,
            "archive",
        )
        result["refresh"].assert_not_called()
        self.assertEqual(
            result["snapshot"].call_args_list,
            [
                call(
                    context,
                    provider_folder="Inbox",
                    limit=message_action.GMAIL_ARCHIVE_READBACK_LIMIT,
                    focus_preferences=None,
                    strict=True,
                    request_with_one_refresh=(
                        message_action._gmail_get_with_one_refresh
                    ),
                ),
                call(
                    context,
                    provider_folder="Archive",
                    limit=message_action.GMAIL_ARCHIVE_READBACK_LIMIT,
                    focus_preferences=None,
                    strict=True,
                    required_message_id=GMAIL_MESSAGE_ID,
                    request_with_one_refresh=(
                        message_action._gmail_get_with_one_refresh
                    ),
                ),
            ],
        )
        serialized = json.dumps(response)
        self.assertNotIn(GMAIL_ACCESS_TOKEN, serialized)
        self.assertNotIn(refresh_token, serialized)
        self.assertNotIn("access_token", serialized)
        self.assertNotIn("refresh_token", serialized)

    def test_synthetic_rfc_imap_and_thread_ids_are_not_mutated(self):
        invalid_ids = (
            None,
            "",
            "rfc-message@example.test",
            "<rfc-message@example.test>",
            "imap-uid-123",
            "rfc-message-123",
            "thread-123",
            "contains\nnewline",
            123,
        )
        for message_id in invalid_ids:
            with self.subTest(message_id=message_id):
                result = _run_gmail_archive(
                    payload={
                        "mailboxId": MAILBOX_ID,
                        "messageId": message_id,
                        "action": "archive",
                    }
                )
                self.assertEqual(result["handler"].status, 400)
                self.assertEqual(
                    result["handler"].response()["error"]["code"],
                    "invalid_request",
                )
                result["modify"].assert_not_called()


class ImapArchiveIntegrationTests(unittest.TestCase):
    def test_owned_credentials_foundation_once_and_two_readbacks(self):
        mailbox = RecordingMailbox()
        readback = RecordingImapReadback()
        result = _run_imap_archive(
            mailbox=mailbox,
            readback=readback,
        )

        self.assertEqual(result["handler"].status, 200)
        response = result["handler"].response()
        result["owned"].assert_called_once_with(
            result["handler"].headers,
            MAILBOX_ID,
        )
        result["authenticated"].assert_called_once_with(
            result["handler"].headers,
            MAILBOX_ID,
        )
        result["connect"].assert_called_once_with(
            host=IMAP_HOST,
            port=993,
            username=IMAP_USERNAME,
            password=IMAP_PASSWORD,
            ssl_enabled=True,
        )
        result["identity"].assert_called_once_with(
            mailbox,
            folder="INBOX",
            uid="123",
            expected_uid_validity="456",
        )
        result["foundation"].assert_called_once_with(
            mailbox,
            source_folder="INBOX",
            uid="123",
            expected_uid_validity="456",
        )
        self.assertEqual(
            result["snapshot"].call_args_list,
            [
                call(
                    mailbox,
                    folder="INBOX",
                    mailbox_key=MAILBOX_ID,
                    email_address="owned@example.test",
                    limit=message_action.IMAP_ARCHIVE_READBACK_LIMIT,
                ),
                call(
                    mailbox,
                    folder="Archive",
                    mailbox_key=MAILBOX_ID,
                    email_address="owned@example.test",
                    limit=message_action.IMAP_ARCHIVE_READBACK_LIMIT,
                ),
            ],
        )
        self.assertEqual(mailbox.logout_count, 1)
        self.assertEqual(mailbox.shutdown_count, 0)
        self.assertEqual(mailbox.unsafe_calls, [])
        self.assertEqual(
            response["folders"]["Inbox"]["imapUidSet"],
            [],
        )
        self.assertEqual(
            response["folders"]["Inbox"]["uidValidity"],
            "456",
        )
        self.assertEqual(
            response["folders"]["Archive"]["imapUidSet"],
            ["900"],
        )
        self.assertEqual(
            response["folders"]["Archive"]["uidValidity"],
            "789",
        )
        self.assertEqual(
            response["archivedMessageIdentity"]["imapUid"],
            "900",
        )
        self.assertEqual(
            response["archivedMessageIdentity"]["sourceImapUid"],
            "123",
        )
        serialized = json.dumps(response)
        for secret in (
            IMAP_PASSWORD,
            IMAP_USERNAME,
            IMAP_HOST,
            "owner@example.test",
        ):
            self.assertNotIn(secret, serialized)

    def test_foundation_failure_is_safely_mapped_and_logged_out(self):
        mailbox = RecordingMailbox()
        result = _run_imap_archive(
            mailbox=mailbox,
            foundation_result={
                "ok": False,
                "status": "error",
                "error": {
                    "code": "archive_move_failed",
                    "message": (
                        f"raw provider failure {IMAP_PASSWORD} "
                        f"{IMAP_HOST}"
                    ),
                    "stage": "move",
                },
            },
        )
        self.assertEqual(result["handler"].status, 502)
        response = result["handler"].response()
        self.assertEqual(
            response["error"]["code"],
            "archive_move_failed",
        )
        result["foundation"].assert_called_once()
        result["snapshot"].assert_not_called()
        self.assertEqual(mailbox.logout_count, 1)
        self.assertEqual(mailbox.unsafe_calls, [])
        serialized = json.dumps(response)
        self.assertNotIn(IMAP_PASSWORD, serialized)
        self.assertNotIn(IMAP_HOST, serialized)

    def test_readback_failure_after_move_is_uncertain_without_mutation_retry(self):
        mailbox = RecordingMailbox()
        readback = RecordingImapReadback(fail_folder="Archive")
        result = _run_imap_archive(
            mailbox=mailbox,
            readback=readback,
        )
        self.assertEqual(result["handler"].status, 502)
        response = result["handler"].response()
        self.assertEqual(
            response["status"],
            "mutation_confirmed_readback_failed",
        )
        self.assertEqual(
            response["error"]["code"],
            "archive_readback_failed",
        )
        result["foundation"].assert_called_once()
        self.assertEqual(result["snapshot"].call_count, 2)
        self.assertEqual(mailbox.logout_count, 1)
        self.assertNotIn(IMAP_PASSWORD, json.dumps(response))

    def test_readback_exception_after_move_is_uncertain_without_mutation_retry(self):
        mailbox = RecordingMailbox()
        successful_readback = RecordingImapReadback()

        def readback(mailbox_client, **kwargs):
            if kwargs["folder"] == "Archive":
                raise RuntimeError(
                    f"raw provider readback failure {IMAP_PASSWORD}"
                )
            return successful_readback(mailbox_client, **kwargs)

        result = _run_imap_archive(
            mailbox=mailbox,
            readback=readback,
        )
        self.assertEqual(result["handler"].status, 502)
        response = result["handler"].response()
        self.assertEqual(
            response["status"],
            "mutation_confirmed_readback_failed",
        )
        self.assertEqual(
            response["error"]["code"],
            "archive_readback_failed",
        )
        result["foundation"].assert_called_once()
        self.assertEqual(result["snapshot"].call_count, 2)
        self.assertEqual(mailbox.logout_count, 1)
        self.assertEqual(mailbox.unsafe_calls, [])
        self.assertNotIn(IMAP_PASSWORD, json.dumps(response))

    def test_inconsistent_uid_readbacks_are_uncertain(self):
        cases = (
            (
                "source_uid_still_present",
                RecordingImapReadback(inbox_uid_set=["123"]),
            ),
            (
                "identity_missing_from_archive",
                RecordingImapReadback(archive_identities={}),
            ),
            (
                "message_missing_from_archive",
                RecordingImapReadback(archive_messages=[]),
            ),
        )
        for name, readback in cases:
            with self.subTest(name=name):
                result = _run_imap_archive(readback=readback)
                self.assertEqual(result["handler"].status, 502)
                self.assertEqual(
                    result["handler"].response()["error"]["code"],
                    "archive_readback_failed",
                )
                result["foundation"].assert_called_once()
                self.assertEqual(result["mailbox"].logout_count, 1)

    def test_duplicate_archive_identity_match_is_uncertain_without_mutation_retry(self):
        mailbox = RecordingMailbox()
        archive_uids = ["900", "901"]
        readback = RecordingImapReadback(
            archive_uid_set=archive_uids,
            archive_messages=[
                {
                    "id": "rfc-message@example.test",
                    "rfcMessageId": "rfc-message@example.test",
                    "imapUid": target_uid,
                    "providerFolder": "Archive",
                    "uidValidity": "789",
                }
                for target_uid in archive_uids
            ],
            archive_identities={
                target_uid: _source_imap_identity(
                    provider_folder="Archive",
                    imap_uid=target_uid,
                    uid_validity="789",
                )
                for target_uid in archive_uids
            },
        )
        result = _run_imap_archive(
            mailbox=mailbox,
            readback=readback,
        )

        self.assertEqual(result["handler"].status, 502)
        response = result["handler"].response()
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["status"],
            "mutation_confirmed_readback_failed",
        )
        self.assertEqual(
            response["error"]["code"],
            "archive_readback_failed",
        )
        result["foundation"].assert_called_once_with(
            mailbox,
            source_folder="INBOX",
            uid="123",
            expected_uid_validity="456",
        )
        self.assertEqual(
            result["snapshot"].call_args_list,
            [
                call(
                    mailbox,
                    folder="INBOX",
                    mailbox_key=MAILBOX_ID,
                    email_address="owned@example.test",
                    limit=message_action.IMAP_ARCHIVE_READBACK_LIMIT,
                ),
                call(
                    mailbox,
                    folder="Archive",
                    mailbox_key=MAILBOX_ID,
                    email_address="owned@example.test",
                    limit=message_action.IMAP_ARCHIVE_READBACK_LIMIT,
                ),
            ],
        )
        self.assertEqual(mailbox.logout_count, 1)
        self.assertEqual(mailbox.unsafe_calls, [])
        serialized = json.dumps(response)
        for secret in (
            IMAP_PASSWORD,
            IMAP_USERNAME,
            IMAP_HOST,
            "owner@example.test",
            "stable-provider-message-fingerprint",
        ):
            self.assertNotIn(secret, serialized)

    def test_source_identity_failure_prevents_mutation(self):
        result = _run_imap_archive(
            identity_result={
                "status": "error",
                "identity": None,
                "error": {
                    "code": "message_not_found",
                    "message": f"raw {IMAP_PASSWORD}",
                },
            }
        )
        self.assertEqual(result["handler"].status, 404)
        self.assertEqual(
            result["handler"].response()["error"]["code"],
            "archive_message_not_found",
        )
        result["foundation"].assert_not_called()
        result["snapshot"].assert_not_called()
        self.assertEqual(result["mailbox"].logout_count, 1)
        self.assertNotIn(
            IMAP_PASSWORD,
            json.dumps(result["handler"].response()),
        )

    def test_wrongly_scoped_source_identity_prevents_mutation(self):
        cases = (
            {"providerFolder": "Archive"},
            {"imapUid": "999"},
            {"uidValidity": "999"},
        )
        for override in cases:
            with self.subTest(override=override):
                result = _run_imap_archive(
                    identity_result={
                        "status": "ok",
                        "identity": {
                            **_source_imap_identity(),
                            **override,
                        },
                        "error": None,
                    }
                )
                self.assertEqual(result["handler"].status, 502)
                self.assertEqual(
                    result["handler"].response()["error"]["code"],
                    "imap_archive_failed",
                )
                result["foundation"].assert_not_called()
                result["snapshot"].assert_not_called()
                self.assertEqual(result["mailbox"].logout_count, 1)

    def test_logout_failure_uses_shutdown_without_changing_success(self):
        mailbox = RecordingMailbox(
            logout_error=RuntimeError(f"provider logout {IMAP_PASSWORD}")
        )
        result = _run_imap_archive(mailbox=mailbox)
        self.assertEqual(result["handler"].status, 200)
        self.assertEqual(mailbox.logout_count, 1)
        self.assertEqual(mailbox.shutdown_count, 1)
        self.assertNotIn(
            IMAP_PASSWORD,
            json.dumps(result["handler"].response()),
        )


class ImapArchiveValidationTests(unittest.TestCase):
    def _assert_invalid_request(
        self,
        payload: dict,
        *,
        expected_code: str,
    ):
        request = FakeHandler(payload)
        with patch.object(
            message_action,
            "resolve_owned_mailbox",
            return_value=_owned_mailbox("custom_imap"),
        ), patch.object(
            message_action,
            "resolve_authenticated_imap_mailbox",
        ) as authenticated, patch.object(
            message_action,
            "connect_mailbox_with_settings",
        ) as connect, patch.object(
            message_action,
            "archive_imap_message",
        ) as foundation:
            message_action.handler.do_POST(request)
        self.assertEqual(request.status, 400)
        self.assertEqual(
            request.response()["error"]["code"],
            expected_code,
        )
        authenticated.assert_not_called()
        connect.assert_not_called()
        foundation.assert_not_called()

    def test_imap_contract_rejects_forged_connection_target_and_bulk_fields(self):
        forbidden_cases = (
            {"provider": "custom_imap"},
            {"host": "evil.invalid"},
            {"username": "attacker"},
            {"password": "attacker-secret"},
            {"credentialGeneration": "forged-generation"},
            {"archiveFolder": "Client Chosen Archive"},
            {"targetFolder": "Client Chosen Archive"},
            {"uids": ["123", "124"]},
            {"messages": [{"uid": "123"}]},
            {"messageId": "gmail-id-is-not-an-imap-uid"},
            {
                "connection": {
                    "host": "evil.invalid",
                    "password": "nested-secret",
                }
            },
        )
        base = {
            "mailboxId": MAILBOX_ID,
            "folder": "INBOX",
            "uid": "123",
            "uidValidity": "456",
            "action": "archive",
        }
        for extra in forbidden_cases:
            with self.subTest(extra=extra):
                request = FakeHandler({**base, **extra})
                with patch.object(
                    message_action,
                    "resolve_owned_mailbox",
                    return_value=_owned_mailbox("custom_imap"),
                ), patch.object(
                    message_action,
                    "resolve_authenticated_imap_mailbox",
                ) as authenticated, patch.object(
                    message_action,
                    "connect_mailbox_with_settings",
                ) as connect, patch.object(
                    message_action,
                    "archive_imap_message",
                ) as foundation:
                    message_action.handler.do_POST(request)

                self.assertEqual(request.status, 400)
                self.assertEqual(
                    request.response()["error"]["code"],
                    "forbidden_connection_fields",
                )
                authenticated.assert_not_called()
                connect.assert_not_called()
                foundation.assert_not_called()

    def test_source_folder_must_be_exact_inbox(self):
        for folder in (
            None,
            "",
            "inbox",
            "Inbox",
            " INBOX",
            "INBOX ",
            "Archive",
        ):
            with self.subTest(folder=folder):
                self._assert_invalid_request(
                    {
                        "mailboxId": MAILBOX_ID,
                        "folder": folder,
                        "uid": "123",
                        "uidValidity": "456",
                        "action": "archive",
                    },
                    expected_code="unsupported_source_folder",
                )

    def test_uid_must_be_one_concrete_canonical_uid(self):
        for uid in (
            None,
            "",
            "0",
            "01",
            "-1",
            "1:2",
            "1,2",
            "１２",
            "4294967296",
            123,
            ["123", "124"],
        ):
            with self.subTest(uid=uid):
                self._assert_invalid_request(
                    {
                        "mailboxId": MAILBOX_ID,
                        "folder": "INBOX",
                        "uid": uid,
                        "uidValidity": "456",
                        "action": "archive",
                    },
                    expected_code="missing_imap_uid",
                )

    def test_uidvalidity_is_required_and_canonical(self):
        for uid_validity in (
            None,
            "",
            "0",
            "01",
            "+1",
            " 1",
            "1 ",
            "１２",
            "100000000000000000000",
            456,
        ):
            with self.subTest(uid_validity=uid_validity):
                self._assert_invalid_request(
                    {
                        "mailboxId": MAILBOX_ID,
                        "folder": "INBOX",
                        "uid": "123",
                        "uidValidity": uid_validity,
                        "action": "archive",
                    },
                    expected_code="invalid_request",
                )

    def test_server_credential_resolution_failure_prevents_connection(self):
        request = FakeHandler(
            {
                "mailboxId": MAILBOX_ID,
                "folder": "INBOX",
                "uid": "123",
                "uidValidity": "456",
                "action": "archive",
            }
        )
        resolved_error = {
            "status": "not_found",
            "mailbox": None,
            "error": {
                "code": "managed_inbox_not_found",
                "message": "The requested mailbox was not found.",
                "status_code": 404,
            },
        }
        with patch.object(
            message_action,
            "resolve_owned_mailbox",
            return_value=_owned_mailbox("custom_imap"),
        ), patch.object(
            message_action,
            "resolve_authenticated_imap_mailbox",
            return_value=resolved_error,
        ), patch.object(
            message_action,
            "connect_mailbox_with_settings",
        ) as connect, patch.object(
            message_action,
            "archive_imap_message",
        ) as foundation:
            message_action.handler.do_POST(request)
        self.assertEqual(request.status, 404)
        self.assertEqual(
            request.response()["error"]["code"],
            "managed_inbox_not_found",
        )
        connect.assert_not_called()
        foundation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
