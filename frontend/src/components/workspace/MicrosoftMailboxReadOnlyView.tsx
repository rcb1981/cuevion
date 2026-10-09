import React, {
  memo,
  useCallback,
  useEffect,
  useState,
} from "react";
import {
  fetchMicrosoftInbox,
  fetchMicrosoftMessage,
  setMicrosoftMessageUnread,
  type LiveInboxMessageSnapshot,
} from "../../lib/inboxConnectionApi";
import { applyConfirmedMicrosoftUnread } from "./microsoftReadState";

const MICROSOFT_POLL_INTERVAL_MS = 120_000;
const MICROSOFT_INBOX_LIMIT = 25;

type MicrosoftReadOnlyMailbox = {
  id: string;
  title: string;
  email: string;
};

type MicrosoftMailboxReadOnlyViewProps = {
  mailbox: MicrosoftReadOnlyMailbox;
  connected: boolean;
  onBack: () => void;
  onOpenConnectionSettings: () => void;
};

function readableTime(isoValue: string) {
  const stamp = new Date(isoValue);
  if (!Number.isFinite(stamp.getTime())) {
    return "Unknown date";
  }
  return stamp.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function safeErrorMessage(code: string | undefined, fallback: string) {
  return code === "microsoft_reconnect_required"
    ? "The Microsoft mailbox needs to be reconnected."
    : code === "microsoft_unauthorized"
      ? "Your Cuevion session has expired. Please sign in again."
      : code === "microsoft_graph_rate_limited"
        ? "Microsoft is rate-limiting requests. Please try again shortly."
        : fallback;
}

/**
 * Microsoft Inbox with explicit, verified read/unread actions.
 *
 * Important: this component has NO dependency on Gmail/IMAP mailboxStore,
 * background reconciliation, archive/trash mutations, replies, or localStorage.
 * A mailbox switch, reconnect, or unmount revokes all pending async tickets.
 */
export const MicrosoftMailboxReadOnlyView = memo(function MicrosoftMailboxReadOnlyView({
  mailbox,
  connected,
  onBack,
  onOpenConnectionSettings,
}: MicrosoftMailboxReadOnlyViewProps) {
  const inboxTicket = React.useRef(0);
  const messageTicket = React.useRef(0);
  const selectedIdRef = React.useRef<string | null>(null);
  const mutationTicket = React.useRef(0);
  const mutationInFlight = React.useRef(false);
  const [mutationBusy, setMutationBusy] = useState(false);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [mutationConfirmation, setMutationConfirmation] = useState<string | null>(null);
  const [rows, setRows] = useState<LiveInboxMessageSnapshot[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedMessage, setSelectedMessage] = useState<LiveInboxMessageSnapshot | null>(null);
  const [listBusy, setListBusy] = useState(false);
  const [messageBusy, setMessageBusy] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [messageError, setMessageError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!connected || mutationInFlight.current) {
      return;
    }
    const ticket = ++inboxTicket.current;
    setListBusy(true);
    setListError(null);
    try {
      const response = await fetchMicrosoftInbox({
        mailboxId: mailbox.id,
        limit: MICROSOFT_INBOX_LIMIT,
      });
      if (ticket !== inboxTicket.current) {
        return;
      }
      if (!response.ok || !Array.isArray(response.messages)) {
        if (
          response.error?.code === "microsoft_reconnect_required" ||
          response.error?.code === "microsoft_unauthorized"
        ) {
          // Never continue showing a prior snapshot after authority changed.
          selectedIdRef.current = null;
          messageTicket.current += 1;
          setRows([]);
          setSelectedId(null);
          setSelectedMessage(null);
          setMessageBusy(false);
          setMessageError(null);
          setHasMore(false);
        }
        setListError(safeErrorMessage(
          response.error?.code, "The Microsoft Inbox could not be refreshed.",
        ));
        return;
      }
      const incoming = response.messages;
      const stillSelected = selectedIdRef.current !== null
        && incoming.some((entry) => entry.providerMessageId === selectedIdRef.current);
      if (!stillSelected) {
        selectedIdRef.current = null;
        messageTicket.current += 1;
        setSelectedId(null);
        setSelectedMessage(null);
        setMessageBusy(false);
        setMessageError(null);
      }
      setRows(incoming);
      setHasMore(response.hasMore === true);
      setLastUpdated(new Date().toISOString());
    } catch {
      if (ticket === inboxTicket.current) {
        setListError("The Microsoft Inbox could not be refreshed.");
      }
    } finally {
      if (ticket === inboxTicket.current) {
        setListBusy(false);
      }
    }
  }, [connected, mailbox.id]);

  useEffect(() => {
    if (!connected) {
      return;
    }
    void refresh();
    const interval = window.setInterval(() => {
      void refresh();
    }, MICROSOFT_POLL_INTERVAL_MS);
    return () => {
      window.clearInterval(interval);
      inboxTicket.current += 1;
      messageTicket.current += 1;
      mutationTicket.current += 1;
      mutationInFlight.current = false;
    };
  }, [connected, refresh]);

  const openMessage = useCallback(async (providerMessageId: string) => {
    if (
      !connected ||
      mutationInFlight.current ||
      !rows.some((entry) => entry.providerMessageId === providerMessageId)
    ) {
      return;
    }
    const ticket = ++messageTicket.current;
    selectedIdRef.current = providerMessageId;
    setSelectedId(providerMessageId);
    setSelectedMessage(null);
    setMessageBusy(true);
    setMessageError(null);
    setMutationError(null);
    setMutationConfirmation(null);
    try {
      const response = await fetchMicrosoftMessage({
        mailboxId: mailbox.id,
        providerMessageId,
      });
      if (
        ticket !== messageTicket.current ||
        selectedIdRef.current !== providerMessageId
      ) {
        return;
      }
      if (
        !response.ok ||
        !response.message ||
        response.message.providerMessageId !== providerMessageId
      ) {
        if (
          response.error?.code === "microsoft_reconnect_required" ||
          response.error?.code === "microsoft_unauthorized"
        ) {
          setRows([]);
          setHasMore(false);
          setSelectedId(null);
          selectedIdRef.current = null;
          setListError(safeErrorMessage(
            response.error?.code,
            "The Microsoft mailbox is no longer authorized.",
          ));
          setMessageError(null);
        } else {
          setMessageError(safeErrorMessage(
            response.error?.code,
            "The selected Microsoft message could not be opened.",
          ));
        }
        return;
      }
      setSelectedMessage(response.message);
    } catch {
      if (ticket === messageTicket.current) {
        setMessageError("The selected Microsoft message could not be opened.");
      }
    } finally {
      if (ticket === messageTicket.current) {
        setMessageBusy(false);
      }
    }
  }, [connected, mailbox.id, rows]);

  const changeReadState = useCallback(async (nextUnread: boolean) => {
    const targetId = selectedMessage?.providerMessageId;
    if (
      !connected ||
      mutationInFlight.current ||
      !targetId ||
      selectedIdRef.current !== targetId ||
      selectedMessage?.unread === nextUnread
    ) {
      return;
    }
    mutationInFlight.current = true;
    const ticket = ++mutationTicket.current;
    // Prevent an older, in-flight Inbox refresh from overwriting an
    // acknowledged Graph mutation with its earlier unread snapshot.
    inboxTicket.current += 1;
    setListBusy(false);
    setMutationBusy(true);
    setMutationError(null);
    setMutationConfirmation(null);
    try {
      const result = await setMicrosoftMessageUnread({
        mailboxId: mailbox.id,
        providerMessageId: targetId,
        unread: nextUnread,
      });
      if (
        mutationTicket.current !== ticket ||
        selectedIdRef.current !== targetId
      ) {
        return;
      }
      if (!result.ok) {
        const code = result.error?.code;
        if (
          code === "microsoft_reconnect_required" ||
          code === "microsoft_unauthorized"
        ) {
          selectedIdRef.current = null;
          messageTicket.current += 1;
          setRows([]);
          setSelectedId(null);
          setSelectedMessage(null);
          setHasMore(false);
          setListError(safeErrorMessage(
            code, "The Microsoft mailbox is no longer authorized.",
          ));
        } else {
          setMutationError(
            code === "microsoft_write_permission_required"
              ? "Reconnect Microsoft to grant permission for message changes."
              : code === "microsoft_mutation_in_progress"
                ? "Another message change is in progress. Try again shortly."
                : "The change could not be confirmed. Refresh the Inbox to check its status.",
          );
        }
        return;
      }
      // Only a server-confirmed exact message result can alter the UI.
      setRows((current) => current.map((message) =>
        applyConfirmedMicrosoftUnread(message, targetId, nextUnread),
      ));
      setSelectedMessage((current) =>
        current ? applyConfirmedMicrosoftUnread(current, targetId, nextUnread) : null,
      );
      setMutationConfirmation(nextUnread ? "Marked as unread." : "Marked as read.");
    } catch {
      if (mutationTicket.current === ticket) {
        setMutationError(
          "The change could not be confirmed. Refresh the Inbox to check its status.",
        );
      }
    } finally {
      if (mutationTicket.current === ticket) {
        mutationInFlight.current = false;
        setMutationBusy(false);
      }
    }
  }, [connected, mailbox.id, selectedMessage]);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden text-[var(--workspace-text)]">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-[var(--workspace-border-soft)] px-4 py-4 sm:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            onClick={onBack}
            className="rounded-lg px-2 py-1 text-sm text-[var(--workspace-text-muted)] hover:text-[var(--workspace-text)] focus-visible:outline focus-visible:outline-2"
            aria-label="Back to workspace"
          >
            ← Back
          </button>
          <div className="min-w-0">
            <h1 className="truncate text-lg font-semibold">{mailbox.title}</h1>
            <p className="truncate text-xs text-[var(--workspace-text-muted)]">{mailbox.email}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-full border border-[var(--workspace-border-soft)] px-3 py-1 text-[0.7rem] font-medium text-[var(--workspace-text-muted)]">
            Microsoft 365 · Inbox
          </span>
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={!connected || listBusy || mutationBusy}
            className="rounded-xl border border-[var(--workspace-border)] px-3 py-2 text-xs font-medium disabled:opacity-50"
          >
            {listBusy ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </header>

      {!connected ? (
        <div className="m-6 max-w-lg rounded-xl border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] p-6" role="status">
          <h2 className="text-base font-semibold">Microsoft mailbox not connected</h2>
          <p className="mt-2 text-sm text-[var(--workspace-text-muted)]">
            Reconnect this mailbox to view its messages.
          </p>
          <button type="button" onClick={onOpenConnectionSettings} className="mt-4 rounded-lg border border-[var(--workspace-border)] px-4 py-2 text-sm">
            Connection settings
          </button>
        </div>
      ) : (
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-0 md:grid-cols-[minmax(245px,37%)_minmax(0,1fr)]">
          <section aria-label="Microsoft Inbox messages" className="flex min-h-0 flex-col border-b border-[var(--workspace-border-soft)] md:border-b-0 md:border-r">
            <div className="flex shrink-0 items-center justify-between border-b border-[var(--workspace-border-soft)] px-4 py-3">
              <h2 className="text-sm font-semibold">Inbox <span className="font-normal text-[var(--workspace-text-muted)]">({rows.length}, {rows.filter((row) => row.unread).length} unread shown)</span></h2>
              {lastUpdated && <span className="text-[0.7rem] text-[var(--workspace-text-faint)]">Updated {readableTime(lastUpdated)}</span>}
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto" aria-busy={listBusy}>
              {listError && (
                <div role="alert" className="m-3 rounded-lg border border-[var(--workspace-border)] p-3 text-sm">
                  {listError}
                  <button type="button" onClick={onOpenConnectionSettings} className="ml-2 underline">Settings</button>
                </div>
              )}
              {rows.length === 0 && (
                <p className="p-5 text-sm text-[var(--workspace-text-muted)]" role="status">
                  {listBusy ? "Loading Microsoft Inbox…" : listError ? "No new messages available." : "Your Microsoft Inbox is empty."}
                </p>
              )}
              {rows.map((message) => {
                const id = message.providerMessageId;
                if (!id) return null;
                const selected = selectedId === id;
                return (
                  <button
                    key={id}
                    type="button"
                    aria-pressed={selected}
                    disabled={mutationBusy}
                    onClick={() => void openMessage(id)}
                    className={"block w-full border-b border-[var(--workspace-border-soft)] px-4 py-4 text-left transition-colors hover:bg-[var(--workspace-card-subtle)] focus-visible:outline focus-visible:outline-2 " +
                      (selected ? "bg-[var(--workspace-card)] " : "")}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className={"min-w-0 truncate text-sm " + (message.unread ? "font-semibold" : "font-medium")}>{message.sender}</span>
                      <span className="shrink-0 text-[0.7rem] text-[var(--workspace-text-faint)]">{readableTime(message.createdAt)}</span>
                    </div>
                    <div className={"mt-1 truncate text-sm " + (message.unread ? "font-semibold" : "font-normal")}>{message.subject || "(No subject)"}</div>
                    <p className="mt-1 truncate text-xs text-[var(--workspace-text-muted)]">{message.snippet || "No preview available"}</p>
                  </button>
                );
              })}
              {hasMore && <p className="p-4 text-xs text-[var(--workspace-text-muted)]">Showing the 25 most recent messages. Older-message navigation will follow.</p>}
            </div>
          </section>
          <section aria-label="Selected Microsoft message" className="min-h-0 overflow-y-auto p-5 sm:p-7">
            {!selectedId && <p className="text-sm text-[var(--workspace-text-muted)]">Select a message to read it.</p>}
            {selectedId && messageBusy && <p role="status" className="text-sm text-[var(--workspace-text-muted)]">Opening message…</p>}
            {selectedId && messageError && <div role="alert" className="rounded-xl border border-[var(--workspace-border)] p-4 text-sm">{messageError}</div>}
            {selectedId && selectedMessage && !messageBusy && (
              <article className="max-w-3xl">
                <h2 className="text-xl font-semibold">{selectedMessage.subject || "(No subject)"}</h2>
                <div className="mt-4 border-b border-[var(--workspace-border-soft)] pb-4 text-sm">
                  <p className="font-medium">{selectedMessage.from}</p>
                  <p className="mt-1 text-xs text-[var(--workspace-text-muted)]">{readableTime(selectedMessage.createdAt)}</p>
                </div>
                <div className="mt-4 flex flex-wrap items-center gap-3">
                  <button
                    type="button"
                    disabled={mutationBusy}
                    onClick={() => void changeReadState(!selectedMessage.unread)}
                    className="rounded-xl border border-[var(--workspace-border)] px-4 py-2 text-xs font-semibold disabled:opacity-50 focus-visible:outline focus-visible:outline-2"
                  >
                    {mutationBusy ? "Updating…" : selectedMessage.unread ? "Mark as read" : "Mark as unread"}
                  </button>
                  <span className="text-xs text-[var(--workspace-text-muted)]">
                    Opening a message does not change its read status.
                  </span>
                </div>
                {mutationError && (
                  <div role="alert" className="mt-3 rounded-xl border border-[var(--workspace-border)] p-3 text-sm">
                    {mutationError}
                    <button type="button" disabled={mutationBusy} onClick={() => void refresh()} className="ml-3 underline">
                      Refresh Inbox
                    </button>
                    <button type="button" onClick={onOpenConnectionSettings} className="ml-3 underline">
                      Connection settings
                    </button>
                  </div>
                )}
                {mutationConfirmation && (
                  <p role="status" className="mt-3 text-xs text-[var(--workspace-text-muted)]">
                    {mutationConfirmation}
                  </p>
                )}
                <div className="mt-6 space-y-4 whitespace-pre-wrap break-words text-sm leading-7">
                  {selectedMessage.body.map((part, index) => <p key={index}>{part}</p>)}
                </div>
                <p className="mt-8 text-xs text-[var(--workspace-text-faint)]">
                  Replies, attachments and other message actions are not enabled yet.
                </p>
              </article>
            )}
          </section>
        </div>
      )}
    </div>
  );
});
