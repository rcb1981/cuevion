from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import Mock, call, patch


CURRENT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = CURRENT_DIR.parent.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))


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
    "message_action_trash_contract_test",
)


MAILBOX_ID = "server-mailbox"
MESSAGE_ID = "18f-provider-message"
OTHER_MESSAGE_ID = "18f-other-message-in-thread"
ACCESS_TOKEN = "test-only-trash-access-token-never-return"
IMAP_UID = "42"
IMAP_UID_VALIDITY = "9001"
TARGET_IMAP_UID = "314"
TARGET_UID_VALIDITY = "9002"
TRASH_FOLDER = "Deleted Messages"
IMAP_PASSWORD = "test-only-imap-password-never-return"


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
    def __init__(self, body: bytes | str):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.headers: dict[str, str] = {}
        self._stream = io.BytesIO(self.body)

    def read(self, amount=-1):
        return self._stream.read(amount)

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False


def _request_payload(**overrides) -> dict:
    return {
        "mailboxId": MAILBOX_ID,
        "action": "trash",
        "providerMessageId": MESSAGE_ID,
        "sourceFolder": "INBOX",
        **overrides,
    }


def _imap_request_payload(**overrides) -> dict:
    return {
        "mailboxId": MAILBOX_ID,
        "action": "trash",
        "sourceFolder": "INBOX",
        "imapUid": IMAP_UID,
        "uidValidity": IMAP_UID_VALIDITY,
        **overrides,
    }


def _owned_mailbox(provider: str = "google") -> dict:
    return {
        "status": "ok",
        "user": {"email": "owner@example.test"},
        "inbox": {
            "id": MAILBOX_ID,
            "email": "owned@gmail.test",
            "provider": provider,
        },
    }


def _authenticated_imap_mailbox() -> dict:
    return {
        "status": "ok",
        "mailbox": {
            "mailboxId": MAILBOX_ID,
            "email": "owned@imap.test",
            "provider": "custom_imap",
            "customImapFolderMappings": None,
            "imap": {
                "host": "imap.example.test",
                "port": 993,
                "username": "owned@imap.test",
                "password": IMAP_PASSWORD,
                "ssl": True,
            },
        },
        "error": None,
    }


def _imap_trash_success(**overrides) -> dict:
    return {
        "ok": True,
        "status": "ok",
        "source_folder": "INBOX",
        "source_uid": IMAP_UID,
        "source_uid_validity": IMAP_UID_VALIDITY,
        "trash_folder": TRASH_FOLDER,
        "target_uid": TARGET_IMAP_UID,
        "target_uid_validity": TARGET_UID_VALIDITY,
        "confirmation": "exact_target_verified",
        "error": None,
        **overrides,
    }


def _imap_trash_failure(code: str, stage: str) -> dict:
    return {
        "ok": False,
        "status": "error",
        "source_folder": None,
        "source_uid": None,
        "source_uid_validity": None,
        "trash_folder": None,
        "target_uid": None,
        "target_uid_validity": None,
        "confirmation": None,
        "error": {
            "code": code,
            "message": "raw helper detail must not escape",
            "stage": stage,
        },
    }


def _owned_error(status_code: int, code: str) -> dict:
    return {
        "status": "error",
        "status_code": status_code,
        "error": message_action.error_payload(code, "safe authority failure"),
    }


def _gmail_context() -> dict:
    return {
        "mailbox_id": MAILBOX_ID,
        "mailbox_email": "owned@gmail.test",
        "owner_email": "owner@example.test",
        "access_token": ACCESS_TOKEN,
        "refresh_attempted": False,
        "scope": message_action.GMAIL_MODIFY_SCOPE,
    }


def _message(message_id: str = MESSAGE_ID, labels=None) -> dict:
    return {
        "id": message_id,
        "threadId": "18f-thread-with-two-messages",
        "labelIds": ["INBOX", "STARRED"] if labels is None else labels,
    }


def _run_trash(
    *,
    payload: dict | None = None,
    owned_result: dict | None = None,
    gmail_result: dict | None = None,
    get_results: list[
        tuple[dict | None, dict | None] | Exception
    ] | None = None,
    mutation_result: tuple[dict | None, dict | None] | None = None,
    mutation_exception: Exception | None = None,
):
    request = FakeHandler(payload or _request_payload())
    owned = Mock(return_value=owned_result or _owned_mailbox())
    gmail = Mock(
        return_value=gmail_result
        or {"status": "ok", "context": _gmail_context()}
    )
    get_request = Mock(
        side_effect=get_results
        if get_results is not None
        else [
            (_message(), None),
            (_message(labels=["TRASH", "STARRED"]), None),
        ]
    )
    trash_request = Mock(
        return_value=(
            mutation_result
            or (_message(labels=["TRASH", "STARRED"]), None)
        ),
        side_effect=mutation_exception,
    )
    refresh = Mock(side_effect=AssertionError("unexpected token refresh"))
    imap_action = Mock(
        side_effect=AssertionError("Trash must not enter the IMAP dispatcher")
    )

    with patch.object(
        message_action,
        "resolve_owned_mailbox",
        owned,
    ), patch.object(
        message_action,
        "resolve_gmail_context",
        gmail,
    ), patch.object(
        message_action,
        "_gmail_get_request",
        get_request,
    ), patch.object(
        message_action,
        "_gmail_trash_request",
        trash_request,
    ), patch.object(
        message_action,
        "refresh_gmail_context",
        refresh,
    ), patch.object(
        message_action,
        "_perform_imap_action",
        imap_action,
    ):
        message_action.handler.do_POST(request)

    return {
        "handler": request,
        "owned": owned,
        "gmail": gmail,
        "get_request": get_request,
        "trash_request": trash_request,
        "refresh": refresh,
        "imap_action": imap_action,
    }


def _run_imap_trash(
    *,
    payload: dict | None = None,
    owned_result: dict | None = None,
    authenticated_result: dict | None = None,
    helper_result: dict | None = None,
    helper_exception: Exception | None = None,
    connect_exception: Exception | None = None,
    logout_exception: Exception | None = None,
):
    request = FakeHandler(
        _imap_request_payload() if payload is None else payload
    )
    owned = Mock(
        return_value=(
            _owned_mailbox("custom_imap")
            if owned_result is None
            else owned_result
        )
    )
    authenticated = Mock(
        return_value=(
            _authenticated_imap_mailbox()
            if authenticated_result is None
            else authenticated_result
        )
    )
    mailbox = Mock()
    if logout_exception is not None:
        mailbox.logout.side_effect = logout_exception
    connect = Mock(return_value=mailbox, side_effect=connect_exception)
    trash_imap = Mock(
        return_value=(
            _imap_trash_success()
            if helper_result is None
            else helper_result
        ),
        side_effect=helper_exception,
    )
    gmail = Mock(side_effect=AssertionError("custom IMAP Trash must not resolve Gmail"))
    imap_action = Mock(
        side_effect=AssertionError("Trash must not enter the flag/action dispatcher")
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
        "trash_imap_message",
        trash_imap,
    ), patch.object(
        message_action,
        "resolve_gmail_context",
        gmail,
    ), patch.object(
        message_action,
        "_perform_imap_action",
        imap_action,
    ):
        message_action.handler.do_POST(request)

    return {
        "handler": request,
        "owned": owned,
        "authenticated": authenticated,
        "mailbox": mailbox,
        "connect": connect,
        "trash_imap": trash_imap,
        "gmail": gmail,
        "imap_action": imap_action,
    }


class GmailTrashMessageLevelTests(unittest.TestCase):
    def test_exact_message_in_two_message_thread_is_trashed_once(self):
        result = _run_trash()

        self.assertEqual(result["handler"].status, 200)
        self.assertEqual(
            result["handler"].response(),
            {
                "ok": True,
                "action": "trash",
                "provider": "gmail",
                "mailboxId": MAILBOX_ID,
                "providerMessageId": MESSAGE_ID,
                "sourceFolder": "INBOX",
                "destinationFolder": "TRASH",
                "readback": {"inSource": False, "inTrash": True},
            },
        )
        result["trash_request"].assert_called_once_with(
            ACCESS_TOKEN,
            MESSAGE_ID,
        )
        self.assertNotIn(
            OTHER_MESSAGE_ID,
            json.dumps(result["trash_request"].call_args_list),
        )
        detail_path = f"/messages/{MESSAGE_ID}?format=minimal"
        self.assertEqual(
            result["get_request"].call_args_list,
            [
                call(ACCESS_TOKEN, detail_path),
                call(ACCESS_TOKEN, detail_path),
            ],
        )
        self.assertTrue(
            all("/threads/" not in current.args[1] for current in result["get_request"].call_args_list)
        )
        self.assertIn(
            ("Cache-Control", "no-store"),
            result["handler"].response_headers,
        )

    def test_provider_transport_uses_one_message_trash_endpoint_and_empty_body(self):
        response = json.dumps(_message(labels=["TRASH"]))
        with patch.object(
            message_action,
            "urlopen",
            return_value=FakeResponse(response),
        ) as transport:
            payload, error = message_action._gmail_trash_request(
                ACCESS_TOKEN,
                MESSAGE_ID,
            )

        self.assertIsNone(error)
        self.assertEqual(payload, _message(labels=["TRASH"]))
        transport.assert_called_once()
        provider_request = transport.call_args.args[0]
        self.assertEqual(provider_request.get_method(), "POST")
        self.assertEqual(
            provider_request.full_url,
            f"{message_action.GMAIL_API_BASE_URL}/messages/{MESSAGE_ID}/trash",
        )
        self.assertNotIn("/threads/", provider_request.full_url)
        self.assertEqual(provider_request.data, b"")

    def test_empty_non_json_and_non_object_trash_responses_are_invalid(self):
        for raw_response in (b"", b"not-json", b"[]"):
            with self.subTest(raw_response=raw_response):
                with patch.object(
                    message_action,
                    "urlopen",
                    return_value=FakeResponse(raw_response),
                ):
                    payload, error = message_action._gmail_trash_request(
                        ACCESS_TOKEN,
                        MESSAGE_ID,
                    )
                self.assertIsNone(payload)
                self.assertEqual(error, {"code": "gmail_response_invalid"})

    def test_provider_server_error_is_an_uncertain_mutation_transport(self):
        provider_error = HTTPError(
            "https://gmail.test.invalid/message/trash",
            503,
            "provider unavailable",
            None,
            None,
        )
        with patch.object(
            message_action,
            "urlopen",
            side_effect=provider_error,
        ):
            payload, error = message_action._gmail_trash_request(
                ACCESS_TOKEN,
                MESSAGE_ID,
            )

        self.assertIsNone(payload)
        self.assertEqual(error, {"code": "gmail_trash_unavailable"})


class GmailTrashPremutationTests(unittest.TestCase):
    def test_only_exact_inbox_not_trash_state_allows_mutation(self):
        valid = _run_trash()
        self.assertEqual(valid["handler"].status, 200)
        valid["trash_request"].assert_called_once()

        invalid_sources = {
            "missing_inbox": _message(labels=["STARRED"]),
            "already_trash": _message(labels=["TRASH"]),
            "inbox_and_trash": _message(labels=["INBOX", "TRASH"]),
            "different_id": _message("different-provider-message"),
            "missing_labels": {"id": MESSAGE_ID},
            "string_labels": _message(labels="INBOX"),
            "non_string_label": _message(labels=["INBOX", 1]),
            "duplicate_labels": _message(labels=["INBOX", "INBOX"]),
        }
        for name, source_payload in invalid_sources.items():
            with self.subTest(name=name):
                result = _run_trash(
                    get_results=[(source_payload, None)],
                )
                self.assertNotEqual(result["handler"].status, 200)
                result["trash_request"].assert_not_called()
                self.assertFalse(result["handler"].response()["ok"])

    def test_source_transport_failure_never_mutates(self):
        result = _run_trash(
            get_results=[(None, {"code": "gmail_unavailable"})],
        )

        self.assertEqual(result["handler"].status, 502)
        self.assertEqual(
            result["handler"].response()["error"]["code"],
            "trash_source_unconfirmed",
        )
        result["trash_request"].assert_not_called()

    def test_source_transport_exception_is_ordinary_and_never_mutates(self):
        result = _run_trash(
            get_results=[ConnectionError("pre-read disconnected")],
        )

        self.assertEqual(result["handler"].status, 502)
        self.assertEqual(
            result["handler"].response()["error"]["code"],
            "trash_source_unconfirmed",
        )
        self.assertEqual(result["get_request"].call_count, 1)
        result["trash_request"].assert_not_called()

    def test_source_token_failure_is_not_refreshed_or_retried(self):
        result = _run_trash(
            get_results=[(None, {"code": "gmail_token_invalid"})],
        )

        self.assertEqual(result["handler"].status, 401)
        self.assertEqual(
            result["handler"].response()["error"]["code"],
            "reconnect_required",
        )
        self.assertEqual(result["get_request"].call_count, 1)
        result["refresh"].assert_not_called()
        result["trash_request"].assert_not_called()


class GmailTrashPostmutationTests(unittest.TestCase):
    def test_only_exact_trash_not_inbox_readback_succeeds(self):
        invalid_readbacks = {
            "trash_missing": _message(labels=["STARRED"]),
            "inbox_remains": _message(labels=["INBOX", "TRASH"]),
            "different_id": _message("different-provider-message", ["TRASH"]),
            "missing_labels": {"id": MESSAGE_ID},
            "malformed_labels": _message(labels=["TRASH", 1]),
        }
        for name, readback_payload in invalid_readbacks.items():
            with self.subTest(name=name):
                result = _run_trash(
                    get_results=[
                        (_message(), None),
                        (readback_payload, None),
                    ],
                )
                self.assertEqual(result["handler"].status, 502)
                self.assertEqual(
                    result["handler"].response()["error"]["code"],
                    "trash_mutation_unconfirmed",
                )
                result["trash_request"].assert_called_once()

    def test_postmutation_transport_failure_is_uncertain_without_retry(self):
        result = _run_trash(
            get_results=[
                (_message(), None),
                (None, {"code": "gmail_unavailable"}),
            ],
        )

        self.assertEqual(result["handler"].status, 502)
        self.assertEqual(
            result["handler"].response(),
            {
                "ok": False,
                "status": "mutation_unconfirmed",
                "action": "trash",
                "provider": "gmail",
                "mailboxId": MAILBOX_ID,
                "providerMessageId": MESSAGE_ID,
                "sourceFolder": "INBOX",
                "destinationFolder": "TRASH",
                "error": {
                    "code": "trash_mutation_unconfirmed",
                    "message": (
                        "Trash may have completed; the current Gmail state "
                        "could not be confirmed safely."
                    ),
                },
            },
        )
        result["trash_request"].assert_called_once()
        self.assertEqual(result["get_request"].call_count, 2)
        result["refresh"].assert_not_called()

    def test_postmutation_token_failure_is_uncertain_without_refresh(self):
        result = _run_trash(
            get_results=[
                (_message(), None),
                (None, {"code": "gmail_token_invalid"}),
            ],
        )

        self.assertEqual(result["handler"].status, 502)
        self.assertEqual(
            result["handler"].response()["error"]["code"],
            "trash_mutation_unconfirmed",
        )
        result["trash_request"].assert_called_once()
        self.assertEqual(result["get_request"].call_count, 2)
        result["refresh"].assert_not_called()

    def test_postmutation_transport_exception_is_uncertain_without_retry(self):
        result = _run_trash(
            get_results=[
                (_message(), None),
                ConnectionError("post-read disconnected"),
            ],
        )

        self.assertEqual(result["handler"].status, 502)
        self.assertEqual(
            result["handler"].response()["error"]["code"],
            "trash_mutation_unconfirmed",
        )
        result["trash_request"].assert_called_once()
        self.assertEqual(result["get_request"].call_count, 2)

    def test_mutation_transport_exception_is_uncertain_without_retry(self):
        result = _run_trash(
            get_results=[(_message(), None)],
            mutation_exception=ConnectionError("mutation disconnected"),
        )

        self.assertEqual(result["handler"].status, 502)
        self.assertEqual(
            result["handler"].response()["error"]["code"],
            "trash_mutation_unconfirmed",
        )
        result["trash_request"].assert_called_once()
        self.assertEqual(result["get_request"].call_count, 1)

    def test_definitive_mutation_failure_is_ordinary_without_retry(self):
        result = _run_trash(
            get_results=[(_message(), None)],
            mutation_result=(None, {"code": "gmail_permission_denied"}),
        )

        self.assertEqual(result["handler"].status, 403)
        self.assertEqual(
            result["handler"].response(),
            message_action.error_payload(
                "gmail_permission_denied",
                "Gmail did not permit this Trash action.",
            ),
        )
        result["trash_request"].assert_called_once()
        self.assertEqual(result["get_request"].call_count, 1)

    def test_malformed_or_transport_uncertain_mutation_is_never_retried(self):
        mutation_results = {
            "transport": (None, {"code": "gmail_trash_unavailable"}),
            "empty_object": ({}, None),
            "different_id": (_message("different-provider-message", ["TRASH"]), None),
            "malformed_labels": (_message(labels="TRASH"), None),
        }
        for name, mutation_result in mutation_results.items():
            with self.subTest(name=name):
                result = _run_trash(
                    get_results=[(_message(), None)],
                    mutation_result=mutation_result,
                )
                self.assertEqual(result["handler"].status, 502)
                self.assertEqual(
                    result["handler"].response()["error"]["code"],
                    "trash_mutation_unconfirmed",
                )
                result["trash_request"].assert_called_once()
                self.assertEqual(result["get_request"].call_count, 1)


class CustomImapTrashRouteTests(unittest.TestCase):
    def test_public_target_folder_requires_exact_safe_utf8_bounded_text(self):
        class FolderName(str):
            pass

        self.assertTrue(message_action._valid_imap_folder_name("Deleted Items"))
        self.assertTrue(message_action._valid_imap_folder_name("é" * 8_192))
        for invalid_folder in (
            FolderName("Deleted Items"),
            "",
            " Deleted Items",
            "Deleted Items ",
            "x" * 16_385,
            "é" * 8_193,
            "Deleted\x00Items",
            "Deleted\x1fItems",
            "Deleted\x7fItems",
            "Deleted\x80Items",
            "Deleted\x9fItems",
            "\ud800",
        ):
            with self.subTest(invalid_folder=repr(invalid_folder)):
                self.assertFalse(
                    message_action._valid_imap_folder_name(invalid_folder)
                )

    def test_exact_custom_imap_identity_returns_minimal_confirmed_target(self):
        result = _run_imap_trash()

        self.assertEqual(result["handler"].status, 200)
        self.assertEqual(
            result["handler"].response(),
            {
                "ok": True,
                "status": "ok",
                "action": "trash",
                "provider": "custom_imap",
                "mailboxId": MAILBOX_ID,
                "sourceFolder": "INBOX",
                "sourceImapUid": IMAP_UID,
                "sourceUidValidity": IMAP_UID_VALIDITY,
                "targetFolder": TRASH_FOLDER,
                "targetImapUid": TARGET_IMAP_UID,
                "targetUidValidity": TARGET_UID_VALIDITY,
                "confirmation": "source_removed_target_bound",
            },
        )
        result["authenticated"].assert_called_once_with(
            result["handler"].headers,
            MAILBOX_ID,
        )
        result["connect"].assert_called_once_with(
            host="imap.example.test",
            port=993,
            username="owned@imap.test",
            password=IMAP_PASSWORD,
            ssl_enabled=True,
        )
        result["trash_imap"].assert_called_once_with(
            result["mailbox"],
            source_folder="INBOX",
            uid=IMAP_UID,
            expected_uid_validity=IMAP_UID_VALIDITY,
            configured_trash_folder=None,
        )
        result["mailbox"].logout.assert_called_once_with()
        result["mailbox"].shutdown.assert_not_called()
        result["gmail"].assert_not_called()
        result["imap_action"].assert_not_called()
        public_body = json.dumps(result["handler"].response())
        self.assertNotIn(IMAP_PASSWORD, public_body)
        self.assertNotIn("imap.example.test", public_body)
        self.assertNotIn("exact_target_verified", public_body)
        self.assertIn(
            ("Cache-Control", "no-store"),
            result["handler"].response_headers,
        )

    def test_custom_imap_trash_passes_only_trusted_server_mapping(self):
        authenticated = _authenticated_imap_mailbox()
        authenticated["mailbox"]["customImapFolderMappings"] = {
            "schemaVersion": 1,
            "trashFolder": TRASH_FOLDER,
        }

        result = _run_imap_trash(authenticated_result=authenticated)

        self.assertEqual(result["handler"].status, 200)
        result["trash_imap"].assert_called_once_with(
            result["mailbox"],
            source_folder="INBOX",
            uid=IMAP_UID,
            expected_uid_validity=IMAP_UID_VALIDITY,
            configured_trash_folder=TRASH_FOLDER,
        )

    def test_malformed_server_mapping_fails_before_connect_or_move(self):
        authenticated = _authenticated_imap_mailbox()
        authenticated["mailbox"]["customImapFolderMappings"] = {
            "schemaVersion": 2,
            "trashFolder": TRASH_FOLDER,
        }

        result = _run_imap_trash(authenticated_result=authenticated)

        self.assertEqual(result["handler"].status, 500)
        self.assertEqual(
            result["handler"].response()["error"]["code"],
            "mailbox_configuration_malformed",
        )
        result["connect"].assert_not_called()
        result["trash_imap"].assert_not_called()

    def test_exact_request_union_rejects_missing_mixed_and_authority_fields(self):
        missing_uid = _imap_request_payload()
        del missing_uid["imapUid"]
        cases = {
            "missing_uid": missing_uid,
            "wrong_source": _imap_request_payload(sourceFolder="Archive"),
            "zero_uid": _imap_request_payload(imapUid="0"),
            "leading_zero_uid": _imap_request_payload(imapUid="042"),
            "invalid_uid_validity": _imap_request_payload(uidValidity="0"),
            "mixed_gmail_identity": _imap_request_payload(
                providerMessageId=MESSAGE_ID,
            ),
            "mixed_message_identity": _imap_request_payload(messageId=MESSAGE_ID),
            "client_provider": _imap_request_payload(provider="custom_imap"),
            "client_host": _imap_request_payload(host="attacker.invalid"),
            "client_password": _imap_request_payload(password="must-not-enter"),
        }
        for name, payload in cases.items():
            with self.subTest(name=name):
                result = _run_imap_trash(payload=payload)
                self.assertEqual(result["handler"].status, 400)
                self.assertEqual(
                    result["handler"].response()["error"]["code"],
                    "invalid_trash_request",
                )
                result["owned"].assert_not_called()
                result["authenticated"].assert_not_called()
                result["connect"].assert_not_called()
                result["trash_imap"].assert_not_called()

    def test_custom_identity_shape_cannot_dispatch_to_gmail(self):
        result = _run_imap_trash(owned_result=_owned_mailbox("google"))

        self.assertEqual(result["handler"].status, 400)
        self.assertEqual(
            result["handler"].response()["error"]["code"],
            "invalid_trash_request",
        )
        result["gmail"].assert_not_called()
        result["authenticated"].assert_not_called()
        result["connect"].assert_not_called()
        result["trash_imap"].assert_not_called()

    def test_mailbox_authority_and_authenticated_resolution_fail_before_connect(self):
        authority = _run_imap_trash(
            owned_result=_owned_error(404, "gmail_connection_not_found"),
        )
        self.assertEqual(authority["handler"].status, 404)
        self.assertEqual(
            authority["handler"].response()["error"]["code"],
            "gmail_connection_not_found",
        )
        authority["authenticated"].assert_not_called()
        authority["connect"].assert_not_called()
        authority["trash_imap"].assert_not_called()

        resolution = _run_imap_trash(
            authenticated_result={
                "status": "reconnect_required",
                "mailbox": None,
                "error": {
                    "code": "reconnect_required",
                    "message": "Reconnect this mailbox.",
                    "status_code": 409,
                },
            },
        )
        self.assertEqual(resolution["handler"].status, 409)
        self.assertEqual(
            resolution["handler"].response()["error"]["code"],
            "reconnect_required",
        )
        resolution["connect"].assert_not_called()
        resolution["trash_imap"].assert_not_called()

    def test_resolved_mailbox_mismatch_and_malformed_shape_fail_before_connect(self):
        mismatch = _authenticated_imap_mailbox()
        mismatch["mailbox"]["mailboxId"] = "different-server-mailbox"

        missing_email = _authenticated_imap_mailbox()
        del missing_email["mailbox"]["email"]

        malformed_email = _authenticated_imap_mailbox()
        malformed_email["mailbox"]["email"] = "not-an-email"

        missing_imap = _authenticated_imap_mailbox()
        del missing_imap["mailbox"]["imap"]

        extra_imap_authority = _authenticated_imap_mailbox()
        extra_imap_authority["mailbox"]["imap"]["credentialVersion"] = (
            "must-not-enter-connect"
        )

        invalid_port = _authenticated_imap_mailbox()
        invalid_port["mailbox"]["imap"]["port"] = True

        invalid_ssl = _authenticated_imap_mailbox()
        invalid_ssl["mailbox"]["imap"]["ssl"] = False

        missing_password = _authenticated_imap_mailbox()
        missing_password["mailbox"]["imap"]["password"] = ""

        for name, authenticated_result in {
            "mailbox_mismatch": mismatch,
            "missing_email": missing_email,
            "malformed_email": malformed_email,
            "missing_imap": missing_imap,
            "extra_imap_authority": extra_imap_authority,
            "invalid_port": invalid_port,
            "invalid_ssl": invalid_ssl,
            "missing_password": missing_password,
        }.items():
            with self.subTest(name=name):
                result = _run_imap_trash(
                    authenticated_result=authenticated_result,
                )
                self.assertEqual(result["handler"].status, 500)
                self.assertEqual(
                    result["handler"].response()["error"]["code"],
                    "mailbox_configuration_malformed",
                )
                self.assertNotIn(
                    IMAP_PASSWORD,
                    json.dumps(result["handler"].response()),
                )
                result["connect"].assert_not_called()
                result["trash_imap"].assert_not_called()

    def test_resolved_mailbox_text_fields_reject_oversize_controls_and_surrogates(self):
        cases = {
            "oversized_email": (
                "email",
                "é" * 2_045 + "@x.test",
            ),
            "oversized_host": ("host", "é" * 2_049),
            "oversized_username": ("username", "é" * 2_049),
            "oversized_password": ("password", "é" * 32_769),
            "controlled_email": ("email", "owned\x01@imap.test"),
            "controlled_host": ("host", "imap\x80.example.test"),
            "controlled_username": (
                "username",
                "owned\x9f@imap.test",
            ),
            "controlled_password": ("password", "secret\x00"),
            "surrogate_email": ("email", "owned\ud800@imap.test"),
            "surrogate_host": ("host", "imap\ud800.example.test"),
            "surrogate_username": (
                "username",
                "owned\ud800@imap.test",
            ),
            "surrogate_password": ("password", "secret\ud800"),
        }
        for name, (field, value) in cases.items():
            with self.subTest(name=name):
                authenticated_result = _authenticated_imap_mailbox()
                settings = authenticated_result["mailbox"]
                if field != "email":
                    settings = settings["imap"]
                settings[field] = value

                result = _run_imap_trash(
                    authenticated_result=authenticated_result,
                )

                self.assertEqual(result["handler"].status, 500)
                self.assertEqual(
                    result["handler"].response(),
                    message_action.error_payload(
                        "mailbox_configuration_malformed",
                        "Mailbox configuration is invalid.",
                    ),
                )
                result["connect"].assert_not_called()
                result["trash_imap"].assert_not_called()

    def test_capability_failures_preserve_safe_specific_codes_without_retry(self):
        capability_failures = {
            "trash_folder_unavailable": "trash_discovery",
            "trash_folder_ambiguous": "trash_discovery",
            "trash_move_unsupported": "move_capability",
            "trash_uidplus_unsupported": "move_capability",
        }
        for code, stage in capability_failures.items():
            with self.subTest(code=code):
                result = _run_imap_trash(
                    helper_result=_imap_trash_failure(code, stage),
                )
                self.assertEqual(result["handler"].status, 409)
                self.assertEqual(
                    result["handler"].response()["error"]["code"],
                    code,
                )
                self.assertNotIn(
                    "raw helper detail",
                    json.dumps(result["handler"].response()),
                )
                result["trash_imap"].assert_called_once()
                result["mailbox"].logout.assert_called_once()

    def test_source_failures_are_ordinary_and_never_retry(self):
        source_failures = {
            "source_folder_unavailable": "source_selection",
            "trash_message_not_found": "source_existence",
        }
        for code, stage in source_failures.items():
            with self.subTest(code=code):
                result = _run_imap_trash(
                    helper_result=_imap_trash_failure(code, stage),
                )
                self.assertEqual(result["handler"].status, 409)
                self.assertEqual(
                    result["handler"].response()["error"]["code"],
                    "trash_source_invalid",
                )
                result["trash_imap"].assert_called_once()

        identity = _run_imap_trash(
            helper_result=_imap_trash_failure(
                "source_identity_unconfirmed",
                "source_identity",
            ),
        )
        self.assertEqual(identity["handler"].status, 502)
        self.assertEqual(
            identity["handler"].response()["error"]["code"],
            "trash_source_unconfirmed",
        )
        self.assertNotIn("status", identity["handler"].response())
        identity["trash_imap"].assert_called_once()

    def test_uid_validity_failures_become_uncertain_after_move(self):
        for code in ("uid_validity_unavailable", "uid_validity_changed"):
            with self.subTest(code=code, stage="uid_validity"):
                preflight = _run_imap_trash(
                    helper_result=_imap_trash_failure(code, "uid_validity"),
                )
                self.assertEqual(preflight["handler"].status, 409)
                self.assertEqual(
                    preflight["handler"].response()["error"]["code"],
                    "trash_source_invalid",
                )
                self.assertNotIn("status", preflight["handler"].response())
                preflight["trash_imap"].assert_called_once()

            with self.subTest(code=code, stage="source_postcondition"):
                post_move = _run_imap_trash(
                    helper_result=_imap_trash_failure(
                        code,
                        "source_postcondition",
                    ),
                )
                self.assertEqual(post_move["handler"].status, 502)
                self.assertEqual(
                    post_move["handler"].response()["status"],
                    "mutation_unconfirmed",
                )
                self.assertEqual(
                    post_move["handler"].response()["provider"],
                    "custom_imap",
                )
                self.assertEqual(
                    post_move["handler"].response()["error"]["code"],
                    "trash_mutation_unconfirmed",
                )
                post_move["trash_imap"].assert_called_once()

    def test_definitive_move_rejection_is_ordinary_without_retry(self):
        result = _run_imap_trash(
            helper_result=_imap_trash_failure("trash_move_failed", "move"),
        )

        self.assertEqual(result["handler"].status, 502)
        self.assertEqual(
            result["handler"].response(),
            message_action.error_payload(
                "trash_move_failed",
                "The IMAP server rejected this Trash move.",
            ),
        )
        result["trash_imap"].assert_called_once()

    def test_post_send_binding_and_readback_failures_are_uncertain_without_retry(self):
        uncertain_failures = {
            "trash_move_unconfirmed": "move",
            "target_folder_unavailable": "target_selection",
            "target_uid_validity_unavailable": "target_uid_validity",
            "target_uid_validity_changed": "target_uid_validity",
            "target_message_not_found": "target_readback",
            "target_identity_unconfirmed": "target_binding",
            "trash_target_mismatch": "target_binding",
        }
        expected = {
            "ok": False,
            "status": "mutation_unconfirmed",
            "action": "trash",
            "provider": "custom_imap",
            "mailboxId": MAILBOX_ID,
            "sourceFolder": "INBOX",
            "sourceImapUid": IMAP_UID,
            "sourceUidValidity": IMAP_UID_VALIDITY,
            "error": {
                "code": "trash_mutation_unconfirmed",
                "message": (
                    "Trash may have completed; the current IMAP state could "
                    "not be confirmed safely."
                ),
            },
        }
        for code, stage in uncertain_failures.items():
            with self.subTest(code=code):
                result = _run_imap_trash(
                    helper_result=_imap_trash_failure(code, stage),
                )
                self.assertEqual(result["handler"].status, 502)
                self.assertEqual(result["handler"].response(), expected)
                self.assertNotIn("targetFolder", result["handler"].response())
                self.assertNotIn(
                    "raw helper detail",
                    json.dumps(result["handler"].response()),
                )
                result["trash_imap"].assert_called_once()

    def test_helper_exception_and_malformed_success_are_uncertain_without_retry(self):
        exception = _run_imap_trash(
            helper_exception=ConnectionError("secret provider transport detail"),
        )
        self.assertEqual(exception["handler"].status, 502)
        self.assertEqual(
            exception["handler"].response()["error"]["code"],
            "trash_mutation_unconfirmed",
        )
        exception["trash_imap"].assert_called_once()
        self.assertNotIn(
            "secret provider transport detail",
            json.dumps(exception["handler"].response()),
        )

        malformed = _run_imap_trash(
            helper_result=_imap_trash_success(target_uid=None),
        )
        self.assertEqual(malformed["handler"].status, 502)
        self.assertEqual(
            malformed["handler"].response()["error"]["code"],
            "trash_mutation_unconfirmed",
        )
        malformed["trash_imap"].assert_called_once()

        authority_bearing = _run_imap_trash(
            helper_result=_imap_trash_success(
                password="helper-secret-must-not-escape"
            ),
        )
        self.assertEqual(authority_bearing["handler"].status, 502)
        self.assertEqual(
            authority_bearing["handler"].response()["error"]["code"],
            "trash_mutation_unconfirmed",
        )
        self.assertNotIn(
            "helper-secret-must-not-escape",
            json.dumps(authority_bearing["handler"].response()),
        )
        authority_bearing["trash_imap"].assert_called_once()

        post_move = _run_imap_trash(
            helper_result=_imap_trash_failure("imap_trash_failed", "post_move"),
        )
        self.assertEqual(post_move["handler"].status, 502)
        self.assertEqual(
            post_move["handler"].response()["error"]["code"],
            "trash_mutation_unconfirmed",
        )
        post_move["trash_imap"].assert_called_once()

        pre_move = _run_imap_trash(
            helper_result=_imap_trash_failure("imap_trash_failed", "pre_move"),
        )
        self.assertEqual(pre_move["handler"].status, 502)
        self.assertEqual(
            pre_move["handler"].response()["error"]["code"],
            "imap_trash_failed",
        )
        self.assertNotIn("status", pre_move["handler"].response())
        pre_move["trash_imap"].assert_called_once()

    def test_connection_failure_is_ordinary_and_logout_falls_back_to_shutdown(self):
        credentials = _run_imap_trash(
            connect_exception=message_action.imaplib.IMAP4.error(
                "raw credential rejection"
            ),
        )
        self.assertEqual(credentials["handler"].status, 401)
        self.assertEqual(
            credentials["handler"].response()["error"]["code"],
            "invalid_credentials",
        )
        credentials["trash_imap"].assert_not_called()
        self.assertNotIn(
            "raw credential rejection",
            json.dumps(credentials["handler"].response()),
        )

        connection = _run_imap_trash(
            connect_exception=ConnectionError("secret connection detail"),
        )
        self.assertEqual(connection["handler"].status, 502)
        self.assertEqual(
            connection["handler"].response()["error"]["code"],
            "imap_trash_failed",
        )
        connection["trash_imap"].assert_not_called()
        self.assertNotIn(
            "secret connection detail",
            json.dumps(connection["handler"].response()),
        )

        fallback = _run_imap_trash(
            logout_exception=RuntimeError("logout failed"),
        )
        self.assertEqual(fallback["handler"].status, 200)
        fallback["mailbox"].logout.assert_called_once_with()
        fallback["mailbox"].shutdown.assert_called_once_with()


class GmailTrashAuthorityAndRequestTests(unittest.TestCase):
    def test_authority_failures_win_before_provider_calls(self):
        for status_code, code in (
            (401, "unauthorized"),
            (404, "gmail_connection_not_found"),
            (404, "gmail_connection_not_found"),
        ):
            with self.subTest(status_code=status_code, code=code):
                result = _run_trash(
                    owned_result=_owned_error(status_code, code),
                    get_results=[],
                )
                self.assertEqual(result["handler"].status, status_code)
                self.assertEqual(
                    result["handler"].response()["error"]["code"],
                    code,
                )
                result["gmail"].assert_not_called()
                result["trash_request"].assert_not_called()

    def test_disabled_gmail_mailbox_fails_before_provider_calls(self):
        result = _run_trash(
            gmail_result={
                "status": "error",
                "status_code": 409,
                "error": message_action.error_payload(
                    "gmail_connection_not_ready",
                    "Gmail connection is not ready.",
                ),
            },
            get_results=[],
        )

        self.assertEqual(result["handler"].status, 409)
        self.assertEqual(
            result["handler"].response()["error"]["code"],
            "gmail_connection_not_ready",
        )
        result["trash_request"].assert_not_called()

    def test_gmail_identity_shape_cannot_dispatch_to_custom_imap(self):
        result = _run_trash(
            owned_result=_owned_mailbox("custom_imap"),
            get_results=[],
        )

        self.assertEqual(result["handler"].status, 400)
        self.assertEqual(
            result["handler"].response()["error"]["code"],
            "invalid_trash_request",
        )
        result["gmail"].assert_not_called()
        result["imap_action"].assert_not_called()
        result["trash_request"].assert_not_called()

    def test_invalid_identity_source_and_extra_fields_never_mutate(self):
        cases = {
            "missing_provider_id": {
                "mailboxId": MAILBOX_ID,
                "action": "trash",
                "sourceFolder": "INBOX",
            },
            "thread_id": _request_payload(providerMessageId="thread-123"),
            "rfc_id": _request_payload(providerMessageId="rfc-message@example.test"),
            "wrong_source": _request_payload(sourceFolder="Archive"),
            "extra_authority": _request_payload(providerThreadId="18f-thread"),
            "conflicting_message_id": _request_payload(messageId=MESSAGE_ID),
        }
        for name, payload in cases.items():
            with self.subTest(name=name):
                result = _run_trash(payload=payload, get_results=[])
                self.assertEqual(result["handler"].status, 400)
                self.assertIn(
                    result["handler"].response()["error"]["code"],
                    {"invalid_trash_request"},
                )
                result["owned"].assert_not_called()
                result["gmail"].assert_not_called()
                result["trash_request"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
