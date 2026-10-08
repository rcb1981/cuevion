from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Protocol, TypedDict

from api.auth.email_address import normalize_auth_email
from api.out_of_office_store import (
    ActiveOutOfOfficeTarget,
    OutOfOfficeCursor,
    OutOfOfficeSettings,
    OutOfOfficeStore,
    OutOfOfficeStoreUnavailable,
    is_out_of_office_active,
)


class InboundAutoReplyCandidate(TypedDict):
    providerMessageId: str
    senderEmail: str
    rfcMessageId: str | None
    receivedAt: str | None
    headers: dict[str, str]


class ProviderBatch(TypedDict):
    cursor: OutOfOfficeCursor
    candidates: list[InboundAutoReplyCandidate]


class TargetRunResult(TypedDict):
    ownerEmail: str
    mailboxId: str
    status: str
    sent: int
    suppressed: int
    skipped: int
    error: str | None


class OutOfOfficeProviderError(Exception):
    __slots__ = ("code",)

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class OutOfOfficeCursorReset(Exception):
    __slots__ = ("cursor",)

    def __init__(self, cursor: OutOfOfficeCursor):
        super().__init__("cursor_reset")
        self.cursor = cursor


class OutOfOfficeProviderAdapter(Protocol):
    provider: str

    def baseline(
        self,
        mailbox: dict,
        *,
        owner_email: str,
        now: datetime,
    ) -> OutOfOfficeCursor:
        ...

    def fetch_since(
        self,
        mailbox: dict,
        cursor: OutOfOfficeCursor,
        *,
        owner_email: str,
        now: datetime,
    ) -> ProviderBatch:
        ...

    def send_reply(
        self,
        mailbox: dict,
        candidate: InboundAutoReplyCandidate,
        settings: OutOfOfficeSettings,
        *,
        owner_email: str,
    ) -> None:
        ...


MailboxLoader = Callable[[str, str], dict]
AdapterResolver = Callable[[dict], OutOfOfficeProviderAdapter]


def _header(candidate: InboundAutoReplyCandidate, name: str) -> str:
    value = candidate["headers"].get(name.lower(), "")
    return value.strip() if isinstance(value, str) else ""


def _blocked_sender_local_part(sender_email: str) -> bool:
    local_part = sender_email.split("@", 1)[0].lower()
    collapsed = local_part.replace("-", "").replace("_", "").replace(".", "")
    return (
        local_part in {"mailer-daemon", "postmaster"}
        or collapsed in {
            "noreply",
            "donotreply",
            "noresponse",
            "autoresponder",
            "autoreply",
        }
    )


def _parse_aware_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def candidate_is_in_active_window(
    candidate: InboundAutoReplyCandidate,
    settings: OutOfOfficeSettings,
    *,
    now: datetime,
) -> bool:
    received_at = _parse_aware_timestamp(candidate.get("receivedAt"))
    activated_at = _parse_aware_timestamp(settings.get("activatedAt"))
    if activated_at is None:
        activated_at = _parse_aware_timestamp(settings.get("updatedAt"))
    starts_at = _parse_aware_timestamp(settings.get("startsAt"))
    ends_at = _parse_aware_timestamp(settings.get("endsAt"))
    if received_at is None or activated_at is None:
        return False
    effective_start = max(activated_at, starts_at) if starts_at else activated_at
    if received_at < effective_start:
        return False
    if ends_at is not None and received_at >= ends_at:
        return False
    if received_at > now:
        return False
    return True


def should_auto_reply(
    candidate: InboundAutoReplyCandidate,
    *,
    mailbox_email: str,
) -> bool:
    sender = normalize_auth_email(candidate.get("senderEmail"))
    mailbox = normalize_auth_email(mailbox_email)
    if not sender or not mailbox or sender == mailbox:
        return False
    if _blocked_sender_local_part(sender):
        return False

    auto_submitted = _header(candidate, "auto-submitted").lower()
    if auto_submitted and auto_submitted != "no":
        return False

    precedence = _header(candidate, "precedence").lower()
    if precedence in {"bulk", "list", "junk"}:
        return False

    if _header(candidate, "list-id"):
        return False
    if _header(candidate, "list-unsubscribe"):
        return False
    if _header(candidate, "x-autoreply"):
        return False
    if _header(candidate, "x-autorespond"):
        return False

    auto_response_suppress = _header(candidate, "x-auto-response-suppress").lower()
    if auto_response_suppress and auto_response_suppress != "none":
        return False

    return_path = _header(candidate, "return-path")
    if return_path == "<>":
        return False

    return True


def _result(
    target: ActiveOutOfOfficeTarget,
    status: str,
    *,
    sent: int = 0,
    suppressed: int = 0,
    skipped: int = 0,
    error: str | None = None,
) -> TargetRunResult:
    return {
        "ownerEmail": target["ownerEmail"],
        "mailboxId": target["mailboxId"],
        "status": status,
        "sent": sent,
        "suppressed": suppressed,
        "skipped": skipped,
        "error": error,
    }


def process_out_of_office_target(
    target: ActiveOutOfOfficeTarget,
    *,
    store: OutOfOfficeStore,
    load_mailbox: MailboxLoader,
    resolve_adapter: AdapterResolver,
    now: datetime | None = None,
) -> TargetRunResult:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)

    try:
        settings = store.get(target["ownerEmail"], target["mailboxId"])
    except OutOfOfficeStoreUnavailable:
        return _result(target, "error", error="settings_unavailable")

    if settings is None or not is_out_of_office_active(settings, now=current):
        return _result(target, "inactive")

    try:
        lease_token = store.acquire_worker_lease(target["ownerEmail"], target["mailboxId"])
    except OutOfOfficeStoreUnavailable:
        return _result(target, "error", error="lease_unavailable")

    if lease_token is None:
        return _result(target, "busy")

    try:
        try:
            mailbox = load_mailbox(target["ownerEmail"], target["mailboxId"])
            adapter = resolve_adapter(mailbox)
        except OutOfOfficeProviderError as exc:
            return _result(target, "error", error=exc.code)
        except Exception:
            return _result(target, "error", error="mailbox_unavailable")

        provider = mailbox.get("provider")
        mailbox_email = mailbox.get("email")
        if (
            not isinstance(provider, str)
            or not isinstance(mailbox_email, str)
            or not normalize_auth_email(mailbox_email)
        ):
            return _result(target, "error", error="mailbox_malformed")

        try:
            cursor = store.get_cursor(target["ownerEmail"], target["mailboxId"])
        except OutOfOfficeStoreUnavailable:
            return _result(target, "error", error="cursor_unavailable")

        if cursor is None or cursor.get("provider") != provider:
            try:
                baseline = adapter.baseline(
                    mailbox,
                    owner_email=target["ownerEmail"],
                    now=current,
                )
                store.put_cursor(target["ownerEmail"], target["mailboxId"], baseline)
            except (OutOfOfficeProviderError, OutOfOfficeStoreUnavailable) as exc:
                code = exc.code if isinstance(exc, OutOfOfficeProviderError) else "cursor_unavailable"
                return _result(target, "error", error=code)
            return _result(target, "baselined")

        try:
            batch = adapter.fetch_since(
                mailbox,
                cursor,
                owner_email=target["ownerEmail"],
                now=current,
            )
        except OutOfOfficeCursorReset as reset:
            try:
                store.put_cursor(target["ownerEmail"], target["mailboxId"], reset.cursor)
            except OutOfOfficeStoreUnavailable:
                return _result(target, "error", error="cursor_unavailable")
            return _result(target, "rebaselined")
        except OutOfOfficeProviderError as exc:
            return _result(target, "error", error=exc.code)
        except Exception:
            return _result(target, "error", error="provider_unavailable")

        sent = 0
        suppressed = 0
        skipped = 0

        for candidate in batch["candidates"]:
            if not candidate_is_in_active_window(candidate, settings, now=current):
                skipped += 1
                continue
            if not should_auto_reply(candidate, mailbox_email=mailbox_email):
                skipped += 1
                continue

            sender = normalize_auth_email(candidate["senderEmail"])
            if not sender:
                skipped += 1
                continue

            try:
                suppression_token = store.reserve_sender_reply(
                    target["ownerEmail"],
                    target["mailboxId"],
                    sender,
                )
            except OutOfOfficeStoreUnavailable:
                return _result(
                    target,
                    "error",
                    sent=sent,
                    suppressed=suppressed,
                    skipped=skipped,
                    error="suppression_unavailable",
                )

            if suppression_token is None:
                suppressed += 1
                continue

            try:
                adapter.send_reply(
                    mailbox,
                    candidate,
                    settings,
                    owner_email=target["ownerEmail"],
                )
            except OutOfOfficeProviderError as exc:
                try:
                    store.release_sender_reply(
                        target["ownerEmail"],
                        target["mailboxId"],
                        sender,
                        suppression_token,
                    )
                except OutOfOfficeStoreUnavailable:
                    pass
                return _result(
                    target,
                    "error",
                    sent=sent,
                    suppressed=suppressed,
                    skipped=skipped,
                    error=exc.code,
                )
            except Exception:
                try:
                    store.release_sender_reply(
                        target["ownerEmail"],
                        target["mailboxId"],
                        sender,
                        suppression_token,
                    )
                except OutOfOfficeStoreUnavailable:
                    pass
                return _result(
                    target,
                    "error",
                    sent=sent,
                    suppressed=suppressed,
                    skipped=skipped,
                    error="send_failed",
                )

            try:
                claim_is_still_owned = store.complete_sender_reply(
                    target["ownerEmail"],
                    target["mailboxId"],
                    sender,
                    suppression_token,
                )
            except OutOfOfficeStoreUnavailable:
                claim_is_still_owned = False

            if not claim_is_still_owned:
                return _result(
                    target,
                    "error",
                    sent=sent + 1,
                    suppressed=suppressed,
                    skipped=skipped,
                    error="suppression_confirmation_failed",
                )

            sent += 1

        try:
            store.put_cursor(
                target["ownerEmail"],
                target["mailboxId"],
                batch["cursor"],
            )
        except OutOfOfficeStoreUnavailable:
            return _result(
                target,
                "error",
                sent=sent,
                suppressed=suppressed,
                skipped=skipped,
                error="cursor_unavailable",
            )

        return _result(
            target,
            "processed",
            sent=sent,
            suppressed=suppressed,
            skipped=skipped,
        )
    finally:
        try:
            store.release_worker_lease(
                target["ownerEmail"],
                target["mailboxId"],
                lease_token,
            )
        except OutOfOfficeStoreUnavailable:
            pass


def run_out_of_office_worker(
    *,
    store: OutOfOfficeStore,
    load_mailbox: MailboxLoader,
    resolve_adapter: AdapterResolver,
    now: datetime | None = None,
) -> list[TargetRunResult]:
    try:
        targets = store.list_active_targets()
    except OutOfOfficeStoreUnavailable:
        raise OutOfOfficeProviderError("active_index_unavailable") from None

    return [
        process_out_of_office_target(
            target,
            store=store,
            load_mailbox=load_mailbox,
            resolve_adapter=resolve_adapter,
            now=now,
        )
        for target in targets
    ]
