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

    def test_custom_imap_is_explicitly_unsupported_without_dispatch(self):
        result = _run_trash(
            owned_result=_owned_mailbox("custom_imap"),
            get_results=[],
        )

        self.assertEqual(result["handler"].status, 409)
        self.assertEqual(
            result["handler"].response(),
            message_action.error_payload(
                "trash_provider_not_supported",
                "Provider-authoritative Trash is not supported for this mailbox.",
            ),
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
