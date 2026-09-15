from __future__ import annotations

import base64
import binascii
import json
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass
from email import message_from_bytes
from email.errors import MessageError
from enum import Enum
from typing import Callable
from urllib.parse import quote, urlencode

from .authenticated_gmail import (
    MAX_GMAIL_RESPONSE_BYTES,
    valid_identifier,
)

GMAIL_API_UID_VALIDITY = "gmail-api"
GMAIL_ARCHIVE_QUERY = (
    "-label:inbox -label:trash -label:spam -label:drafts -label:sent"
)
DEFAULT_GMAIL_SNAPSHOT_LIMIT = 50
MAX_GMAIL_SNAPSHOT_LIMIT = 100
GMAIL_DETAIL_CONCURRENCY = 4
_ARCHIVE_EXCLUDED_LABELS = {"INBOX", "TRASH", "SPAM", "DRAFT", "SENT"}
_INBOX_EXCLUDED_LABELS = _ARCHIVE_EXCLUDED_LABELS - {"INBOX"}

GmailRequestWithOneRefresh = Callable[
    [dict, str],
    tuple[dict | None, dict | None, dict, dict | None],
]
GmailRawRequest = Callable[[str, str], tuple[dict | None, dict | None]]
GmailContextRefresh = Callable[[dict], dict]


class _GmailSnapshotRequests:
    """Caller-thread authority; workers receive only a token string and path.

    Refresh and the single transport retry are decided in Gmail list order.
    The initial list and legacy callbacks retain their sequential contract.
    """

    def __init__(
        self,
        context: dict,
        request_with_one_refresh: GmailRequestWithOneRefresh,
        gmail_request: GmailRawRequest | None,
        refresh_context: GmailContextRefresh | None,
    ):
        self.context = context
        self.request_with_one_refresh = request_with_one_refresh
        self.gmail_request = gmail_request
        self.refresh_context = refresh_context
        self.transport_retry_available = True
        self.generation = 0
        self.refresh_attempted = False

    def _with_transport_retry(self, result, retry):
        _payload, error, self.context, refresh_failure = result
        if (
            self.transport_retry_available
            and refresh_failure is None
            and isinstance(error, dict)
            and error.get("code") == "gmail_unavailable"
        ):
            self.transport_retry_available = False
            result = retry()
            self.context = result[2]
        return result

    def sequential_request(self, path):
        def request():
            return self.request_with_one_refresh(self.context, path)

        result = self._with_transport_retry(request(), request)
        if self.gmail_request is not None:
            self.refresh_attempted = bool(self.context.get("refresh_attempted"))
        return result

    def _detail_once(self, path, response=None):
        if response is None:
            response = self.gmail_request(self.context["access_token"], path)
        payload, error = response
        if (
            error
            and error.get("code") == "gmail_token_invalid"
            and not self.refresh_attempted
        ):
            # Only the ordered consumer can enter this branch. Reserve the
            # attempt before invoking the authoritative route capability.
            self.refresh_attempted = True
            refreshed = self.refresh_context(self.context)
            if refreshed["status"] != "ok":
                return None, error, self.context, refreshed
            self.context = refreshed["context"]
            self.generation += 1
            payload, error = self.gmail_request(self.context["access_token"], path)
        return payload, error, self.context, None

    @contextmanager
    def details(self, message_ids):
        def path_for(message_id):
            return f"/messages/{quote(message_id, safe='')}?format=raw"

        if self.gmail_request is None or not message_ids:
            yield (
                (index, message_id, self.sequential_request(path_for(message_id)))
                for index, message_id in enumerate(message_ids)
            )
            return

        window_size = min(GMAIL_DETAIL_CONCURRENCY, len(message_ids))
        executor = ThreadPoolExecutor(
            max_workers=window_size,
            thread_name_prefix="gmail-detail",
        )
        pending = {}

        def submit(index):
            # Neither context nor coordinator is passed to a worker.
            pending[index] = (
                self.generation,
                executor.submit(
                    self.gmail_request,
                    self.context["access_token"],
                    path_for(message_ids[index]),
                ),
            )

        def ordered_results():
            for index, message_id in enumerate(message_ids):
                generation, future = pending.pop(index)
                # Await even a stale request before replaying it: at most
                # window_size - 1 speculative requests can still be in flight.
                wait((future,))
                # Replay every stale outcome, including successes and worker
                # exceptions: sequential code would use the new context here.
                response = (
                    future.result() if generation == self.generation else None
                )
                path = path_for(message_id)
                result = self._with_transport_retry(
                    self._detail_once(path, response),
                    lambda: self._detail_once(path),
                )
                yield index, message_id, result
                # Resume only after the caller parsed/accounted for this row.
                # A terminal error or truncation never admits another request.
                next_index = index + window_size
                if next_index < len(message_ids):
                    submit(next_index)

        try:
            for index in range(window_size):
                submit(index)
            yield ordered_results()
        finally:
            for _generation, future in pending.values():
                future.cancel()
            # Running speculative requests may finish, but never outlive the
            # snapshot return (including parser/worker exceptions).
            executor.shutdown(wait=True, cancel_futures=True)


class GmailExactMessageRecoveryResult(str, Enum):
    RECOVERED = "recovered"
    TERMINAL_ABSENT = "terminal_absent"
    RETRY = "retry"


@dataclass(frozen=True, slots=True)
class GmailExactMessageRecovery:
    result: GmailExactMessageRecoveryResult
    context: dict
    candidate_source: dict | None = None


def _result(
    context: dict,
    *,
    snapshot: dict | None = None,
    error: dict | None = None,
    refresh_failure: dict | None = None,
    priority_candidate_sources: list[dict] | None = None,
) -> dict:
    result = {
        "status": (
            "ok"
            if snapshot is not None
            and error is None
            and refresh_failure is None
            else "error"
        ),
        "context": context,
        "snapshot": snapshot,
        "error": error,
        "refresh_failure": refresh_failure,
    }
    if priority_candidate_sources is not None:
        result["_priorityCandidateSources"] = priority_candidate_sources
    return result


def _invalid_response(context: dict) -> dict:
    return _result(
        context,
        error={"code": "gmail_response_invalid"},
    )


def _base64url_decode(value: str) -> bytes:
    unpadded = value.rstrip("=")
    padding_count = len(value) - len(unpadded)
    if (
        not unpadded
        or padding_count > 2
        or padding_count not in {0, -len(unpadded) % 4}
        or "=" in unpadded
        or any(
            not (
                character.isascii()
                and (
                    character.isalnum()
                    or character in {"-", "_"}
                )
            )
            for character in unpadded
        )
        or len(unpadded) % 4 == 1
    ):
        raise ValueError("invalid base64url value")

    padding = "=" * (-len(unpadded) % 4)
    decoded = base64.b64decode(
        f"{unpadded}{padding}".encode("ascii"),
        altchars=b"-_",
        validate=True,
    )
    if (
        base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
        != unpadded
    ):
        raise ValueError("non-canonical base64url value")
    return decoded


def _list_path(provider_folder: str, limit: int) -> str:
    if provider_folder == "Inbox":
        query = {"labelIds": "INBOX", "maxResults": limit}
    elif provider_folder == "Trash":
        query = {
            "labelIds": "TRASH",
            "includeSpamTrash": "true",
            "maxResults": limit,
        }
    else:
        query = {"q": GMAIL_ARCHIVE_QUERY, "maxResults": limit}
    return f"/messages?{urlencode(query)}"


def _strict_labels_match_folder(
    label_ids: list[str],
    provider_folder: str,
) -> bool:
    normalized = {label_id.upper() for label_id in label_ids}
    if provider_folder == "Inbox":
        return "INBOX" in normalized
    if provider_folder == "Trash":
        return "TRASH" in normalized and "INBOX" not in normalized
    return not normalized.intersection(_ARCHIVE_EXCLUDED_LABELS)


def _message_ids_from_list(
    list_payload: dict,
    *,
    strict: bool,
    limit: int,
) -> tuple[list[str] | None, bool]:
    raw_refs = list_payload.get("messages")
    if raw_refs is None:
        raw_refs = []
    if not isinstance(raw_refs, list):
        return None, False

    message_ids: list[str] = []
    seen: set[str] = set()
    for raw_ref in raw_refs[:limit]:
        message_id = raw_ref.get("id") if isinstance(raw_ref, dict) else None
        if not valid_identifier(message_id) or message_id in seen:
            if strict:
                return None, False
            continue
        seen.add(message_id)
        message_ids.append(message_id)
    return message_ids, True


def _parse_gmail_message_detail_with_candidate_source(
    detail_payload: object,
    *,
    context: dict,
    provider_folder: str,
    requested_message_id: str,
    index: int,
    focus_preferences: dict | None = None,
    strict: bool = False,
    message_parser=message_from_bytes,
) -> tuple[dict, dict] | None:
    """Validate and normalize one Gmail ``format=raw`` detail response.

    This helper is transport-free. Documented provider-data failures return
    ``None``; unexpected parser or preview-building failures remain fatal so
    callers cannot accidentally publish a partial result.
    """

    if (
        provider_folder not in {"Inbox", "Archive", "Trash"}
        or not valid_identifier(requested_message_id)
        or not isinstance(index, int)
        or isinstance(index, bool)
        or index < 0
        or not callable(message_parser)
        or not isinstance(detail_payload, dict)
    ):
        return None

    provider_message_id = detail_payload.get("id")
    if (
        not valid_identifier(provider_message_id)
        or provider_message_id != requested_message_id
    ):
        return None

    raw_label_ids = detail_payload.get("labelIds", [])
    labels_are_valid = (
        isinstance(raw_label_ids, list)
        and all(valid_identifier(label_id) for label_id in raw_label_ids)
        and len(set(raw_label_ids)) == len(raw_label_ids)
    )
    if strict and (
        not labels_are_valid
        or not _strict_labels_match_folder(
            raw_label_ids,
            provider_folder,
        )
    ):
        return None
    label_ids = list(raw_label_ids) if labels_are_valid else []

    raw_message = detail_payload.get("raw")
    if not isinstance(raw_message, str) or not raw_message:
        return None
    try:
        decoded_message = _base64url_decode(raw_message)
    except (binascii.Error, UnicodeEncodeError, ValueError):
        return None
    try:
        parsed_message = message_parser(decoded_message)
    except MessageError:
        return None

    import imap_connect_preview

    unread = "UNREAD" in label_ids
    flagged = "STARRED" in label_ids
    preview = imap_connect_preview.to_message_preview(
        parsed_message,
        index,
        context["mailbox_email"],
        unread,
        None,
        flagged,
        internal_role=None,
        focus_preferences=focus_preferences,
    )
    preview.pop("imapUid", None)
    preview["providerMessageId"] = provider_message_id

    provider_thread_id = detail_payload.get("threadId")
    if valid_identifier(provider_thread_id):
        preview["providerThreadId"] = provider_thread_id
    elif strict:
        return None

    preview["labelIds"] = label_ids
    preview["providerFolder"] = provider_folder
    preview["serverMailboxId"] = context["mailbox_id"]

    rfc_message_id = parsed_message.get("Message-Id")
    if isinstance(rfc_message_id, str):
        normalized_rfc_message_id = (
            rfc_message_id.strip().strip("<>").strip()
        )
        if valid_identifier(normalized_rfc_message_id):
            preview["rfcMessageId"] = normalized_rfc_message_id

    candidate_source = {
        "provider": "google",
        "providerMessageId": provider_message_id,
        "providerThreadId": (
            provider_thread_id if valid_identifier(provider_thread_id) else None
        ),
        "providerFolder": provider_folder.upper(),
        "labels": label_ids,
        "providerTimestampMillis": (
            detail_payload.get("internalDate")
            if isinstance(detail_payload.get("internalDate"), str)
            else None
        ),
        **imap_connect_preview.build_priority_candidate_render_source(
            parsed_message,
            preview,
        ),
    }
    return preview, candidate_source


def parse_gmail_message_detail(
    detail_payload: object,
    *,
    context: dict,
    provider_folder: str,
    requested_message_id: str,
    index: int,
    focus_preferences: dict | None = None,
    strict: bool = False,
    message_parser=message_from_bytes,
) -> dict | None:
    """Validate and normalize one Gmail ``format=raw`` detail response."""
    parsed = _parse_gmail_message_detail_with_candidate_source(
        detail_payload,
        context=context,
        provider_folder=provider_folder,
        requested_message_id=requested_message_id,
        index=index,
        focus_preferences=focus_preferences,
        strict=strict,
        message_parser=message_parser,
    )
    return parsed[0] if parsed is not None else None


def recover_exact_gmail_inbox_message(
    context: dict,
    *,
    provider_message_id: str,
    request_with_one_refresh: GmailRequestWithOneRefresh,
    focus_preferences: dict | None = None,
    message_parser=message_from_bytes,
) -> GmailExactMessageRecovery:
    """Fetch one exact Gmail message and produce its canonical source shape."""

    retry = GmailExactMessageRecoveryResult.RETRY
    if (
        not isinstance(context, dict)
        or not valid_identifier(provider_message_id)
        or not callable(request_with_one_refresh)
        or not callable(message_parser)
    ):
        return GmailExactMessageRecovery(retry, context)

    detail_payload, error, next_context, refresh_failure = (
        request_with_one_refresh(
            context,
            f"/messages/{quote(provider_message_id, safe='')}?format=raw",
        )
    )
    if not isinstance(next_context, dict):
        next_context = context
    if refresh_failure is not None:
        return GmailExactMessageRecovery(retry, next_context)
    if isinstance(error, dict):
        if error.get("code") == "gmail_message_not_found":
            return GmailExactMessageRecovery(
                GmailExactMessageRecoveryResult.TERMINAL_ABSENT,
                next_context,
            )
        return GmailExactMessageRecovery(retry, next_context)
    if not isinstance(detail_payload, dict):
        return GmailExactMessageRecovery(retry, next_context)

    returned_message_id = detail_payload.get("id")
    if (
        not valid_identifier(returned_message_id)
        or returned_message_id != provider_message_id
    ):
        return GmailExactMessageRecovery(retry, next_context)
    if not valid_identifier(detail_payload.get("threadId")):
        return GmailExactMessageRecovery(retry, next_context)

    raw_labels = detail_payload.get("labelIds")
    if (
        not isinstance(raw_labels, list)
        or any(not valid_identifier(label) for label in raw_labels)
        or len(set(raw_labels)) != len(raw_labels)
    ):
        return GmailExactMessageRecovery(retry, next_context)
    if (
        "INBOX" not in raw_labels
        or _INBOX_EXCLUDED_LABELS.intersection(raw_labels)
    ):
        return GmailExactMessageRecovery(
            GmailExactMessageRecoveryResult.TERMINAL_ABSENT,
            next_context,
        )

    parsed = _parse_gmail_message_detail_with_candidate_source(
        detail_payload,
        context=next_context,
        provider_folder="Inbox",
        requested_message_id=provider_message_id,
        index=0,
        focus_preferences=focus_preferences,
        strict=True,
        message_parser=message_parser,
    )
    if parsed is None:
        return GmailExactMessageRecovery(retry, next_context)
    _preview, candidate_source = parsed
    return GmailExactMessageRecovery(
        GmailExactMessageRecoveryResult.RECOVERED,
        next_context,
        candidate_source,
    )


def read_gmail_folder_snapshot(
    context: dict,
    *,
    provider_folder: str,
    request_with_one_refresh: GmailRequestWithOneRefresh,
    limit: int = DEFAULT_GMAIL_SNAPSHOT_LIMIT,
    focus_preferences: dict | None = None,
    strict: bool = False,
    required_message_id: str | None = None,
    message_parser=message_from_bytes,
    gmail_request: GmailRawRequest | None = None,
    refresh_context: GmailContextRefresh | None = None,
) -> dict:
    """Read and normalize one bounded Gmail folder snapshot.

    The initial list retains the injected authenticated callback. Routes can
    additionally provide raw transport and refresh capabilities for bounded
    detail overlap; opaque legacy callbacks stay sequential. Every return
    includes the latest ordered context and any provider or refresh failure.
    """

    if (
        provider_folder not in {"Inbox", "Archive", "Trash"}
        or not isinstance(limit, int)
        or isinstance(limit, bool)
        or limit < 1
        or limit > MAX_GMAIL_SNAPSHOT_LIMIT
        or not callable(request_with_one_refresh)
        or (
            (gmail_request is not None or refresh_context is not None)
            and (not callable(gmail_request) or not callable(refresh_context))
        )
        or (
            required_message_id is not None
            and (
                provider_folder != "Archive"
                or not valid_identifier(required_message_id)
            )
        )
    ):
        return _result(
            context,
            error={"code": "gmail_snapshot_invalid_request"},
        )

    requests = _GmailSnapshotRequests(
        context, request_with_one_refresh, gmail_request, refresh_context,
    )

    list_payload, list_error, context, refresh_failure = (
        requests.sequential_request(_list_path(provider_folder, limit))
    )
    if refresh_failure is not None:
        return _result(
            context,
            error=list_error,
            refresh_failure=refresh_failure,
        )
    if list_error is not None:
        return _result(context, error=list_error)
    if not isinstance(list_payload, dict):
        return _invalid_response(context)

    message_ids, list_is_valid = _message_ids_from_list(
        list_payload,
        strict=strict,
        limit=limit,
    )
    if not list_is_valid or message_ids is None:
        return _invalid_response(context)

    if (
        required_message_id is not None
        and required_message_id not in message_ids
    ):
        if len(message_ids) >= limit:
            message_ids = message_ids[: limit - 1]
        message_ids.append(required_message_id)

    messages: list[dict] = []
    priority_candidate_sources: list[dict] = []
    snapshot = {
        "providerFolder": provider_folder,
        "serverMailboxId": context.get("mailbox_id"),
        "messages": messages,
        "uidValidity": GMAIL_API_UID_VALIDITY,
    }
    snapshot_size: int | None = None
    message_separator_size = len(
        json.JSONEncoder().item_separator.encode("utf-8")
    )
    with requests.details(message_ids) as details:
        for index, requested_message_id, detail_result in details:
            detail_payload, detail_error, context, refresh_failure = detail_result
            if refresh_failure is not None:
                return _result(
                    context,
                    error=detail_error,
                    refresh_failure=refresh_failure,
                )
            if detail_error is not None:
                return _result(context, error=detail_error)
            parsed = _parse_gmail_message_detail_with_candidate_source(
                detail_payload,
                context=context,
                provider_folder=provider_folder,
                requested_message_id=requested_message_id,
                index=index,
                focus_preferences=focus_preferences,
                strict=strict,
                message_parser=message_parser,
            )
            if parsed is None:
                if strict:
                    return _invalid_response(context)
                continue
            preview, priority_candidate_source = parsed

            try:
                if snapshot_size is None:
                    # Keep serialization lazy: empty/all-invalid snapshots were
                    # never size-checked. The empty wrapper already includes [].
                    snapshot_size = len(json.dumps(snapshot).encode("utf-8"))
                # Default json.dumps encodes each list element identically on its
                # own, with item_separator only between elements. Preserve those
                # exact UTF-8 bytes without serializing accepted prefixes again.
                candidate_size = (
                    snapshot_size
                    + len(json.dumps(preview).encode("utf-8"))
                    + (message_separator_size if messages else 0)
                )
            except (TypeError, ValueError):
                raise
            if candidate_size > MAX_GMAIL_RESPONSE_BYTES:
                if strict:
                    return _result(
                        context,
                        error={"code": "gmail_response_too_large"},
                    )
                break
            messages.append(preview)
            snapshot_size = candidate_size
            if provider_folder == "Inbox":
                priority_candidate_sources.append(priority_candidate_source)

    return _result(
        context,
        snapshot=snapshot,
        priority_candidate_sources=priority_candidate_sources,
    )
