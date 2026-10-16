import React, {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  fetchMicrosoftInbox,
  fetchMicrosoftMessage,
  fetchMicrosoftArchive,
  fetchMicrosoftArchiveMessage,
  fetchMicrosoftDrafts,
  fetchMicrosoftDraftMessage,
  fetchMicrosoftDeleted,
  fetchMicrosoftDeletedMessage,
  fetchMicrosoftJunk,
  fetchMicrosoftJunkMessage,
  fetchMicrosoftSent,
  fetchMicrosoftSentMessage,
  fetchMicrosoftAttachments,
  downloadMicrosoftAttachmentFile,
  setMicrosoftMessageUnread,
  type LiveInboxMessageSnapshot,
  type MicrosoftAttachmentMetadata,
} from "../../lib/inboxConnectionApi";
import { applyConfirmedMicrosoftUnread, shouldAutoMarkMicrosoftOpenedMessageRead } from "./microsoftReadState";
import { MicrosoftComposePanel } from "./MicrosoftComposePanel";
import { MicrosoftMessageListRow } from "./MicrosoftMessageListRow";
import { MicrosoftPlannedMessageActions } from "./MicrosoftPlannedMessageActions";
import { MicrosoftReconnectConsentButton } from "./MicrosoftReconnectConsentButton";
import { MicrosoftLinkifiedText } from "./MicrosoftLinkifiedText";
import { filterMicrosoftLoadedMessages, type MicrosoftMessageSortOrder } from "./microsoftMailboxListFilters";
import {
  microsoftVisibleFolderRows,
  isMicrosoftFilteredProtectionActive,
} from "./microsoftVirtualFiltered";
import { useMicrosoftFilteredLearning } from "./useMicrosoftFilteredLearning";
import { MicrosoftFilteredSenderAction } from "./MicrosoftFilteredSenderAction";

// UI convenience only. Real production write prevention is enforced before
// any Graph mutation at the server boundary in microsoft_production_pilot.py.
const MICROSOFT_READ_ONLY_PILOT = true;
// Separately approved, source-gated Inbox unread-to-read operation.
// Reply, Forward, Archive, Delete, and Mail.Send remain disabled.
const MICROSOFT_AUTO_MARK_READ_RELEASED = true;
const MICROSOFT_POLL_INTERVAL_MS = 120_000;
const MICROSOFT_INBOX_LIMIT = 25;
const MICROSOFT_MAX_PAGES = 4;
type MicrosoftFolder = "Inbox" | "Filtered" | "Sent" | "Archive" | "Drafts" | "Deleted" | "Junk";

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
  const attachmentTicket = React.useRef(0);
  const attachmentDownloadTicket = React.useRef(0);
  const selectedIdRef = React.useRef<string | null>(null);
  const folderRef = React.useRef<MicrosoftFolder>("Inbox");
  const [folder, setFolder] = useState<MicrosoftFolder>("Inbox");
  const [page, setPage] = useState(1);
  const [composer, setComposer] = useState<{
    mode: "new" | "reply";
    sourceProviderMessageId?: string;
    sourceDisplay?: string;
  } | null>(null);
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
  const [attachmentRows, setAttachmentRows] = useState<MicrosoftAttachmentMetadata[]>([]);
  const [attachmentBusy, setAttachmentBusy] = useState(false);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const [attachmentHasMore, setAttachmentHasMore] = useState(false);
  const [attachmentDownloadBusy, setAttachmentDownloadBusy] = useState<string | null>(null);
  const [attachmentDownloadError, setAttachmentDownloadError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [messageError, setMessageError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [sortOrder, setSortOrder] = useState<MicrosoftMessageSortOrder>("newest");
  const [placementNotice, setPlacementNotice] = useState<string | null>(null);
  const microsoftFiltering = useMicrosoftFilteredLearning(mailbox.id, connected);
  const folderRows = useMemo(
    () => microsoftVisibleFolderRows(
      rows, folder, microsoftFiltering.learned, microsoftFiltering.placements,
      microsoftFiltering.ready,
    ),
    [rows, folder, microsoftFiltering.learned, microsoftFiltering.placements, microsoftFiltering.ready],
  );
  const visibleRows = useMemo(
    () => filterMicrosoftLoadedMessages(folderRows, {
      folder,
      search: searchQuery,
      unreadOnly,
      sortOrder,
    }),
    [folderRows, folder, searchQuery, unreadOnly, sortOrder],
  );
  const hasActiveListFilter = Boolean(searchQuery.trim()) || ((folder === "Inbox" || folder === "Filtered" || folder === "Archive" || folder === "Deleted" || folder === "Junk") && unreadOnly);

  const refresh = useCallback(async () => {
    if (!connected || mutationInFlight.current || composer !== null) {
      return;
    }
    const ticket = ++inboxTicket.current;
    setListBusy(true);
    setListError(null);
    try {
      const requestedFolder = folder;
      const response = requestedFolder === "Sent"
        ? await fetchMicrosoftSent({
            mailboxId: mailbox.id, limit: MICROSOFT_INBOX_LIMIT, page,
          })
        : requestedFolder === "Archive"
          ? await fetchMicrosoftArchive({
              mailboxId: mailbox.id, limit: MICROSOFT_INBOX_LIMIT, page,
            })
          : requestedFolder === "Drafts"
            ? await fetchMicrosoftDrafts({
                mailboxId: mailbox.id, limit: MICROSOFT_INBOX_LIMIT, page,
              })
            : requestedFolder === "Deleted"
              ? await fetchMicrosoftDeleted({
                  mailboxId: mailbox.id, limit: MICROSOFT_INBOX_LIMIT, page,
                })
              : requestedFolder === "Junk"
                ? await fetchMicrosoftJunk({
                    mailboxId: mailbox.id, limit: MICROSOFT_INBOX_LIMIT, page,
                  })
                : await fetchMicrosoftInbox({
              mailboxId: mailbox.id, limit: MICROSOFT_INBOX_LIMIT, page,
            });
      if (
        ticket !== inboxTicket.current ||
        folderRef.current !== requestedFolder
      ) {
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
          response.error?.code, "The Microsoft " + folder + " could not be refreshed.",
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
        setListError("The Microsoft " + folder + " could not be refreshed.");
      }
    } finally {
      if (ticket === inboxTicket.current) {
        setListBusy(false);
      }
    }
  }, [connected, mailbox.id, folder, composer, page]);

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
      attachmentTicket.current += 1;
      attachmentDownloadTicket.current += 1;
      mutationInFlight.current = false;
    };
  }, [connected, refresh]);

  const selectFolder = useCallback((
    nextFolder: MicrosoftFolder,
    allowComposer = false,
  ) => {
    if (
      !connected ||
      (composer !== null && !allowComposer) ||
      mutationInFlight.current ||
      folderRef.current === nextFolder
    ) {
      return;
    }
    // Synchronously revoke all responses for the previous folder before
    // React schedules the next render. Never reuse Inbox, Sent, or Archive bodies.
    folderRef.current = nextFolder;
    inboxTicket.current += 1;
    messageTicket.current += 1;
    mutationTicket.current += 1;
    attachmentTicket.current += 1;
    attachmentDownloadTicket.current += 1;
    selectedIdRef.current = null;
    setAttachmentRows([]);
    setAttachmentBusy(false);
    setAttachmentError(null);
    setAttachmentHasMore(false);
    setAttachmentDownloadBusy(null);
    setAttachmentDownloadError(null);
    setRows([]);
    setSelectedId(null);
    setSelectedMessage(null);
    setListBusy(false);
    setMessageBusy(false);
    setListError(null);
    setMessageError(null);
    setMutationError(null);
    setMutationConfirmation(null);
    setHasMore(false);
    setLastUpdated(null);
    setSearchQuery("");
    setUnreadOnly(false);
    setPlacementNotice(null);
    setPage(1);
    setFolder(nextFolder);
  }, [connected, composer]);

  const goToPage = useCallback((nextPage: number) => {
    if (
      !connected || composer !== null || mutationInFlight.current ||
      listBusy || !Number.isInteger(nextPage) ||
      nextPage < 1 || nextPage > MICROSOFT_MAX_PAGES ||
      nextPage === page || (nextPage > page && !hasMore)
    ) {
      return;
    }
    // Invalidate pending list/detail requests before React switches pages.
    inboxTicket.current += 1;
    messageTicket.current += 1;
    attachmentTicket.current += 1;
    attachmentDownloadTicket.current += 1;
    selectedIdRef.current = null;
    setSelectedId(null);
    setSelectedMessage(null);
    setAttachmentRows([]);
    setAttachmentBusy(false);
    setAttachmentError(null);
    setAttachmentHasMore(false);
    setAttachmentDownloadBusy(null);
    setAttachmentDownloadError(null);
    setRows([]);
    setHasMore(false);
    setLastUpdated(null);
    setListError(null);
    setMessageError(null);
    setSearchQuery("");
    setUnreadOnly(false);
    setPage(nextPage);
  }, [connected, composer, listBusy, page, hasMore]);

  const openComposer = useCallback((mode: "new" | "reply") => {
    if (MICROSOFT_READ_ONLY_PILOT || !connected || composer !== null || mutationInFlight.current) return;
    const source = mode === "reply" && folderRef.current === "Inbox"
      ? selectedMessage
      : null;
    if (mode === "reply" && !source?.providerMessageId) return;
    inboxTicket.current += 1;
    messageTicket.current += 1;
    setListBusy(false);
    setMessageBusy(false);
    setComposer({
      mode,
      sourceProviderMessageId: source?.providerMessageId,
      sourceDisplay: source?.from,
    });
  }, [connected, composer, selectedMessage]);

  const openMessage = useCallback(async (providerMessageId: string) => {
    if (
      !connected ||
      composer !== null ||
      mutationInFlight.current ||
      !rows.some((entry) => entry.providerMessageId === providerMessageId)
    ) {
      return;
    }
    const ticket = ++messageTicket.current;
    const attachmentRequest = ++attachmentTicket.current;
    selectedIdRef.current = providerMessageId;
    setSelectedId(providerMessageId);
    setSelectedMessage(null);
    setAttachmentRows([]);
    setAttachmentBusy(false);
    setAttachmentError(null);
    setAttachmentHasMore(false);
    attachmentDownloadTicket.current += 1;
    setAttachmentDownloadBusy(null);
    setAttachmentDownloadError(null);
    setMessageBusy(true);
    setMessageError(null);
    setMutationError(null);
    setMutationConfirmation(null);
    try {
      const requestedFolder = folderRef.current;
      const response = requestedFolder === "Sent"
        ? await fetchMicrosoftSentMessage({
            mailboxId: mailbox.id, providerMessageId,
          })
        : requestedFolder === "Archive"
          ? await fetchMicrosoftArchiveMessage({
              mailboxId: mailbox.id, providerMessageId,
            })
          : requestedFolder === "Drafts"
            ? await fetchMicrosoftDraftMessage({
                mailboxId: mailbox.id, providerMessageId,
              })
            : requestedFolder === "Deleted"
              ? await fetchMicrosoftDeletedMessage({
                  mailboxId: mailbox.id, providerMessageId,
                })
              : requestedFolder === "Junk"
                ? await fetchMicrosoftJunkMessage({
                    mailboxId: mailbox.id, providerMessageId,
                  })
                : await fetchMicrosoftMessage({
              mailboxId: mailbox.id, providerMessageId,
            });
      if (
        ticket !== messageTicket.current ||
        folderRef.current !== requestedFolder ||
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
      if (
        shouldAutoMarkMicrosoftOpenedMessageRead({
          readWriteEnabled: MICROSOFT_AUTO_MARK_READ_RELEASED,
          folder: requestedFolder,
          selectedMessageId: selectedIdRef.current,
          message: response.message,
        }) &&
        !mutationInFlight.current
      ) {
        // Stage automatic open-to-read with the existing exact-ID, owned and
        // server-confirmed Graph flag mutation. This is unreachable while
        // the Microsoft Production/Preview read-only pilot remains enabled.
        const flagTicket = ++mutationTicket.current;
        mutationInFlight.current = true;
        inboxTicket.current += 1; // discard any older inbox polling result
        setListBusy(false);
        setMutationBusy(true);
        setMutationError(null);
        void setMicrosoftMessageUnread({
          mailboxId: mailbox.id,
          providerMessageId,
          unread: false,
        }).then((flag) => {
          if (
            flagTicket !== mutationTicket.current ||
            ticket !== messageTicket.current ||
            folderRef.current !== "Inbox" ||
            selectedIdRef.current !== providerMessageId
          ) return;
          if (
            !flag.ok ||
            flag.providerMessageId !== providerMessageId ||
            flag.unread !== false
          ) {
            setMutationError(
              !flag.ok && flag.error?.code === "microsoft_write_permission_required"
                ? "Reconnect Microsoft to grant permission for marking messages as read."
                : "Read status could not be confirmed. Refresh Inbox to check.",
            );
            return;
          }
          setRows((current) => current.map((item) =>
            applyConfirmedMicrosoftUnread(item, providerMessageId, false),
          ));
          setSelectedMessage((current) =>
            current ? applyConfirmedMicrosoftUnread(current, providerMessageId, false) : null,
          );
          setMutationConfirmation("Marked as read.");
        }).catch(() => {
          if (flagTicket === mutationTicket.current &&
              ticket === messageTicket.current) {
            setMutationError("Read status could not be confirmed. Refresh Inbox to check.");
          }
        }).finally(() => {
          mutationInFlight.current = false;
          if (flagTicket === mutationTicket.current) setMutationBusy(false);
        });
      }
      if (response.message.hasAttachments === true) {
        setAttachmentBusy(true);
        void fetchMicrosoftAttachments({
          mailboxId: mailbox.id,
          providerMessageId,
          folder: requestedFolder === "Filtered" ? "Inbox" : requestedFolder,
        }).then((result) => {
          if (
            ticket !== messageTicket.current ||
            attachmentRequest !== attachmentTicket.current ||
            folderRef.current !== requestedFolder ||
            selectedIdRef.current !== providerMessageId
          ) return;
          if (!result.ok || !Array.isArray(result.attachments)) {
            setAttachmentError("Attachment details could not be loaded.");
            return;
          }
          setAttachmentRows(result.attachments);
          setAttachmentHasMore(result.hasMore === true);
        }).catch(() => {
          if (ticket === messageTicket.current) {
            setAttachmentError("Attachment details could not be loaded.");
          }
        }).finally(() => {
          if (ticket === messageTicket.current && attachmentRequest === attachmentTicket.current) {
            setAttachmentBusy(false);
          }
        });
      }
    } catch {
      if (ticket === messageTicket.current) {
        setMessageError("The selected Microsoft message could not be opened.");
      }
    } finally {
      if (ticket === messageTicket.current) {
        setMessageBusy(false);
      }
    }
  }, [connected, mailbox.id, rows, composer]);

  const changeSenderPlacement = useCallback((placement: "show_less" | "keep") => {
    const activeFolder = folderRef.current;
    const message = selectedMessage;
    if (
      !connected ||
      !microsoftFiltering.ready ||
      mutationInFlight.current ||
      mutationBusy ||
      !message?.providerMessageId ||
      selectedIdRef.current !== message.providerMessageId ||
      message.providerFolder !== "INBOX" ||
      (placement === "show_less" && isMicrosoftFilteredProtectionActive(message)) ||
      (activeFolder !== "Inbox" && activeFolder !== "Filtered")
    ) return;
    if (!microsoftFiltering.savePlacement(message.from, placement)) return;
    // The visible partition changes synchronously. Invalidate any pending
    // detail/attachment read; the user can re-open from the new tab.
    selectedIdRef.current = null;
    messageTicket.current += 1;
    attachmentTicket.current += 1;
    attachmentDownloadTicket.current += 1;
    setSelectedId(null);
    setSelectedMessage(null);
    setAttachmentRows([]);
    setAttachmentBusy(false);
    setAttachmentError(null);
    setAttachmentDownloadBusy(null);
    setAttachmentDownloadError(null);
    setPlacementNotice(
      placement === "show_less"
        ? "This sender now appears in Microsoft Filtered."
        : "This sender now appears in Microsoft Inbox.",
    );
  }, [connected, microsoftFiltering, mutationBusy, selectedMessage]);

  const downloadAttachment = useCallback(async (item: MicrosoftAttachmentMetadata) => {
    const messageId = selectedMessage?.providerMessageId;
    if (
      !connected ||
      !messageId ||
      selectedIdRef.current !== messageId ||
      item.kind !== "file" ||
      folderRef.current === "Junk" ||
      item.size > 2 * 1024 * 1024 ||
      !attachmentRows.some((entry) => entry.id === item.id) ||
      attachmentDownloadBusy !== null
    ) {
      return;
    }
    const currentFolder = folderRef.current;
    const currentMessageTicket = messageTicket.current;
    const ticket = ++attachmentDownloadTicket.current;
    setAttachmentDownloadBusy(item.id);
    setAttachmentDownloadError(null);
    try {
      const result = await downloadMicrosoftAttachmentFile({
        mailboxId: mailbox.id,
        providerMessageId: messageId,
        attachmentId: item.id,
        folder: currentFolder === "Filtered" ? "Inbox" : currentFolder,
      });
      if (
        ticket !== attachmentDownloadTicket.current ||
        currentMessageTicket !== messageTicket.current ||
        selectedIdRef.current !== messageId ||
        folderRef.current !== currentFolder
      ) return;
      if (!result.ok || result.attachmentId !== item.id ||
          typeof result.size !== "number" || result.size !== item.size ||
          typeof result.base64 !== "string" || !result.fileName) {
        setAttachmentDownloadError(
          result.error?.code === "microsoft_attachment_too_large"
            ? "This attachment exceeds the 2 MB download limit."
            : "This attachment could not be downloaded. Please try again.",
        );
        return;
      }
      // Keep provider bytes transient. Never render potentially unsafe files inline.
      const binary = atob(result.base64);
      if (binary.length !== result.size) throw new Error("invalid_download");
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i += 1) {
        bytes[i] = binary.charCodeAt(i);
      }
      const blob = new Blob([bytes], { type: "application/octet-stream" });
      const blobUrl = URL.createObjectURL(blob);
      try {
        const link = document.createElement("a");
        link.href = blobUrl;
        link.download = result.fileName;
        link.style.display = "none";
        document.body.appendChild(link);
        link.click();
        link.remove();
      } finally {
        window.setTimeout(() => URL.revokeObjectURL(blobUrl), 5000);
      }
    } catch {
      if (ticket === attachmentDownloadTicket.current &&
          currentMessageTicket === messageTicket.current) {
        setAttachmentDownloadError("This attachment could not be downloaded.");
      }
    } finally {
      if (ticket === attachmentDownloadTicket.current &&
          currentMessageTicket === messageTicket.current) {
        setAttachmentDownloadBusy(null);
      }
    }
  }, [attachmentDownloadBusy, attachmentRows, connected, mailbox.id, selectedMessage]);

  const changeReadState = useCallback(async (nextUnread: boolean) => {
    const targetId = selectedMessage?.providerMessageId;
    if (
      MICROSOFT_READ_ONLY_PILOT ||
      !connected ||
      folderRef.current !== "Inbox" ||
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
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-[var(--workspace-border-soft)] px-3 py-1 text-[0.7rem] font-medium text-[var(--workspace-text-muted)]">
            Microsoft 365 · Mail
          </span>
          <span role="status" className="rounded-full border border-[var(--workspace-border-soft)] px-3 py-1 text-[0.7rem] font-medium text-[var(--workspace-text-muted)]">
            {MICROSOFT_AUTO_MARK_READ_RELEASED
              ? "Auto-read enabled · other actions disabled"
              : "Read-only pilot"}
          </span>
          <MicrosoftReconnectConsentButton mailboxId={mailbox.id} email={mailbox.email} />
          {!MICROSOFT_READ_ONLY_PILOT && (
            <button
              type="button"
              disabled={!connected || composer !== null || mutationBusy}
              onClick={() => openComposer("new")}
              className="rounded-lg border border-[var(--workspace-border)] px-3 py-2 text-xs font-semibold disabled:opacity-50"
            >
              New message
            </button>
          )}
          <button
            type="button"
            aria-pressed={folder === "Inbox"}
            disabled={!connected || composer !== null || mutationBusy}
            onClick={() => selectFolder("Inbox")}
            className="rounded-lg border border-[var(--workspace-border)] px-3 py-2 text-xs font-medium aria-pressed:bg-[var(--workspace-card-subtle)] disabled:opacity-50"
          >
            Inbox
          </button>
          <button
            type="button"
            aria-pressed={folder === "Filtered"}
            disabled={!connected || composer !== null || mutationBusy}
            onClick={() => selectFolder("Filtered")}
            title="Quiet view from the same Outlook Inbox; no messages are moved."
            className="rounded-lg border border-[var(--workspace-border)] px-3 py-2 text-xs font-medium aria-pressed:bg-[var(--workspace-card-subtle)] disabled:opacity-50"
          >
            Filtered
          </button>
          <button
            type="button"
            aria-pressed={folder === "Sent"}
            disabled={!connected || composer !== null || mutationBusy}
            onClick={() => selectFolder("Sent")}
            className="rounded-lg border border-[var(--workspace-border)] px-3 py-2 text-xs font-medium aria-pressed:bg-[var(--workspace-card-subtle)] disabled:opacity-50"
          >
            Sent
          </button>
          <button
            type="button"
            aria-pressed={folder === "Archive"}
            disabled={!connected || composer !== null || mutationBusy}
            onClick={() => selectFolder("Archive")}
            className="rounded-lg border border-[var(--workspace-border)] px-3 py-2 text-xs font-medium aria-pressed:bg-[var(--workspace-card-subtle)] disabled:opacity-50"
          >
            Archive
          </button>
          <button
            type="button"
            aria-pressed={folder === "Drafts"}
            disabled={!connected || composer !== null || mutationBusy}
            onClick={() => selectFolder("Drafts")}
            className="rounded-lg border border-[var(--workspace-border)] px-3 py-2 text-xs font-medium aria-pressed:bg-[var(--workspace-card-subtle)] disabled:opacity-50"
          >
            Drafts
          </button>
          <button
            type="button"
            aria-pressed={folder === "Deleted"}
            disabled={!connected || composer !== null || mutationBusy}
            onClick={() => selectFolder("Deleted")}
            className="rounded-lg border border-[var(--workspace-border)] px-3 py-2 text-xs font-medium aria-pressed:bg-[var(--workspace-card-subtle)] disabled:opacity-50"
          >
            Deleted Items
          </button>
          <button
            type="button"
            aria-pressed={folder === "Junk"}
            disabled={!connected || composer !== null || mutationBusy}
            onClick={() => selectFolder("Junk")}
            className="rounded-lg border border-[var(--workspace-border)] px-3 py-2 text-xs font-medium aria-pressed:bg-[var(--workspace-card-subtle)] disabled:opacity-50"
          >
            Junk Email
          </button>
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={!connected || composer !== null || listBusy || mutationBusy}
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
      ) : !MICROSOFT_READ_ONLY_PILOT && composer !== null ? (
        <MicrosoftComposePanel
          key={mailbox.id + "-" + composer.mode + "-" + (composer.sourceProviderMessageId ?? "new")}
          mailboxId={mailbox.id}
          mode={composer.mode}
          sourceProviderMessageId={composer.sourceProviderMessageId}
          sourceDisplay={composer.sourceDisplay}
          onClose={() => setComposer(null)}
          onOpenSent={() => {
            setComposer(null);
            selectFolder("Sent", true);
          }}
        />
      ) : (
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-0 md:grid-cols-[minmax(245px,37%)_minmax(0,1fr)]">
          <section aria-label={"Microsoft " + folder + " messages"} className="flex min-h-0 flex-col border-b border-[var(--workspace-border-soft)] md:border-b-0 md:border-r">
            <div className="flex shrink-0 items-center justify-between border-b border-[var(--workspace-border-soft)] px-4 py-3">
              <h2 className="text-sm font-semibold">
                {folder} <span className="font-normal text-[var(--workspace-text-muted)]">
                  ({folder === "Filtered" || folder === "Inbox" ? folderRows.length : rows.length}{folder === "Sent"
                    ? " sent shown"
                    : folder === "Drafts"
                      ? " drafts shown"
                      : folder === "Deleted"
                        ? " deleted items shown"
                        : folder === "Junk"
                          ? " junk messages shown"
                          : ", " + folderRows.filter((row) => row.unread).length + " unread shown"})
                </span>
              </h2>
              {lastUpdated && <span className="text-[0.7rem] text-[var(--workspace-text-faint)]">Updated {readableTime(lastUpdated)}</span>}
            </div>
            <div className="shrink-0 space-y-2 border-b border-[var(--workspace-border-soft)] px-3 py-3">
              <label htmlFor={"microsoft-list-search-" + mailbox.id} className="sr-only">
                Search loaded Microsoft messages
              </label>
              <input
                id={"microsoft-list-search-" + mailbox.id}
                type="search"
                autoComplete="off"
                value={searchQuery}
                maxLength={160}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder={"Search loaded " + folder.toLowerCase() + " messages"}
                aria-label="Search loaded Microsoft messages"
                className="w-full rounded-xl border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-3 py-2 text-sm text-[var(--workspace-text)] placeholder:text-[var(--workspace-text-faint)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)]"
              />
              <div className="flex flex-wrap items-center justify-between gap-2">
                {folder === "Inbox" || folder === "Filtered" || folder === "Archive" || folder === "Deleted" || folder === "Junk" ? (
                  <label className="inline-flex cursor-pointer items-center gap-2 text-xs text-[var(--workspace-text-muted)]">
                    <input
                      type="checkbox"
                      checked={unreadOnly}
                      onChange={(event) => setUnreadOnly(event.target.checked)}
                      className="accent-[var(--workspace-accent-primary)]"
                    />
                    Unread only
                  </label>
                ) : (
                  <span className="text-xs text-[var(--workspace-text-faint)]">
                    {folder === "Drafts" ? "Unsent drafts" : "Sent Items"}
                  </span>
                )}
                <label className="inline-flex items-center gap-2 text-xs text-[var(--workspace-text-muted)]">
                  <span className="sr-only">Sort loaded Microsoft messages</span>
                  <select
                    aria-label="Sort loaded Microsoft messages"
                    value={sortOrder}
                    onChange={(event) => setSortOrder(event.target.value as MicrosoftMessageSortOrder)}
                    className="rounded-lg border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-2 py-1.5 text-xs text-[var(--workspace-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)]"
                  >
                    <option value="newest">Newest first</option>
                    <option value="oldest">Oldest first</option>
                  </select>
                </label>
              </div>
              {(folder === "Inbox" || folder === "Filtered") && (
                <p className="text-[0.68rem] text-[var(--workspace-text-muted)]">
                  {folder === "Filtered"
                    ? "Quiet view from your Outlook Inbox. Only the loaded page is checked; no messages are moved."
                    : "Only messages in this loaded page are shown; quiet senders appear under Filtered."}
                </p>
              )}
              {placementNotice && <p role="status" className="text-xs text-[var(--workspace-text-muted)]">{placementNotice}</p>}
              {microsoftFiltering.error && (folder === "Inbox" || folder === "Filtered") && (
                <p role="alert" className="text-xs text-[var(--workspace-text-muted)]">{microsoftFiltering.error}</p>
              )}
              {hasActiveListFilter ? (
                <p className="text-xs text-[var(--workspace-text-faint)]" aria-live="polite">
                  {visibleRows.length} of {rows.length} loaded messages match.
                  {" "}Search and filters apply only to the current page.
                </p>
              ) : null}
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
                  {listBusy ? "Loading Microsoft " + folder + "…" : listError ? "No messages available." : "Your Microsoft " + folder + " is empty."}
                </p>
              )}
              {rows.length > 0 && visibleRows.length === 0 && (
                <p className="p-5 text-sm text-[var(--workspace-text-muted)]" role="status">
                  {folder === "Filtered" && !searchQuery.trim() && !unreadOnly
                    ? microsoftFiltering.ready
                      ? "No quiet messages on this page yet. Open an Inbox message and choose Show less from this sender."
                      : "Loading your learned preferences. No Outlook messages are being moved."
                    : "No matching messages in this loaded set. Clear the search or unread filter."}
                </p>
              )}
              {visibleRows.map((message) => (
                <MicrosoftMessageListRow
                  key={message.providerMessageId ?? message.id}
                  message={message}
                  folder={folder}
                  selected={selectedId === message.providerMessageId}
                  disabled={mutationBusy}
                  dateLabel={readableTime(message.createdAt)}
                  onOpenMessage={(providerMessageId) => void openMessage(providerMessageId)}
                />
              ))}
              <nav aria-label={"Microsoft " + folder + " pages"} className="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--workspace-border-soft)] p-3">
                <button
                  type="button"
                  onClick={() => goToPage(page - 1)}
                  disabled={!connected || listBusy || page === 1 || mutationBusy}
                  className="rounded-lg border border-[var(--workspace-border)] px-3 py-2 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Newer messages
                </button>
                <span className="text-xs text-[var(--workspace-text-muted)]" aria-live="polite">
                  Page {page} of up to {MICROSOFT_MAX_PAGES}
                </span>
                <button
                  type="button"
                  onClick={() => goToPage(page + 1)}
                  disabled={!connected || listBusy || !hasMore || page >= MICROSOFT_MAX_PAGES || mutationBusy}
                  className="rounded-lg border border-[var(--workspace-border)] px-3 py-2 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Older messages
                </button>
              </nav>
              {hasMore && page >= MICROSOFT_MAX_PAGES && (
                <p className="px-4 pb-3 text-xs text-[var(--workspace-text-muted)]">
                  This Microsoft pilot currently limits loaded history to {MICROSOFT_MAX_PAGES * MICROSOFT_INBOX_LIMIT} messages per folder. Filtered is a view of the underlying Inbox pages.
                </p>
              )}
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
                  <p className="font-medium">
                    {folder === "Sent" || folder === "Drafts"
                      ? "To: " + selectedMessage.to
                      : folder === "Deleted"
                        ? (selectedMessage.from || selectedMessage.sender)
                        : selectedMessage.from}
                  </p>
                  {folder === "Sent" && (
                    <p className="mt-1 text-xs text-[var(--workspace-text-muted)]">
                      From: {selectedMessage.from}
                    </p>
                  )}
                  <p className="mt-1 text-xs text-[var(--workspace-text-muted)]">
                    {folder === "Deleted" ? "Last changed: " : ""}{readableTime(selectedMessage.createdAt)}
                  </p>
                </div>
                {folder === "Inbox" && MICROSOFT_READ_ONLY_PILOT && (
                  <MicrosoftPlannedMessageActions unread={selectedMessage.unread === true} />
                )}
                {(folder === "Inbox" || folder === "Filtered") && (
                  <MicrosoftFilteredSenderAction
                    folder={folder}
                    disabled={
                      !microsoftFiltering.ready || mutationBusy ||
                      !selectedMessage.providerMessageId ||
                      (folder === "Inbox" && isMicrosoftFilteredProtectionActive(selectedMessage))
                    }
                    onChange={changeSenderPlacement}
                  />
                )}
                {folder === "Inbox" && (
                  <>
                <div className="mt-4 flex flex-wrap items-center gap-3">
                  {!MICROSOFT_READ_ONLY_PILOT && (
                    <>
                      <button
                        type="button"
                        disabled={mutationBusy}
                        onClick={() => openComposer("reply")}
                        className="rounded-xl border border-[var(--workspace-border)] px-4 py-2 text-xs font-semibold disabled:opacity-50"
                      >
                        Reply
                      </button>
                      <button
                        type="button"
                        disabled={mutationBusy}
                        onClick={() => void changeReadState(!selectedMessage.unread)}
                        className="rounded-xl border border-[var(--workspace-border)] px-4 py-2 text-xs font-semibold disabled:opacity-50 focus-visible:outline focus-visible:outline-2"
                      >
                        {mutationBusy ? "Updating…" : selectedMessage.unread ? "Mark as read" : "Mark as unread"}
                      </button>
                    </>
                  )}
                  <span className="text-xs text-[var(--workspace-text-muted)]">
                    {MICROSOFT_AUTO_MARK_READ_RELEASED
                      ? "Opening an unread Inbox message marks it as read after Microsoft confirms."
                      : "Opening a message does not change its read status."}
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
                  </>
                )}
                {selectedMessage.hasAttachments && (
                  <section aria-label="Microsoft message attachments" className="mt-4 rounded-xl border border-[var(--workspace-border-soft)] p-4 text-xs text-[var(--workspace-text-muted)]">
                    <h3 className="mb-2 text-sm font-semibold text-[var(--workspace-text)]">Attachments</h3>
                    {attachmentBusy && <p role="status">Loading attachment details…</p>}
                    {attachmentError && <p role="alert">{attachmentError}</p>}
                    {!attachmentBusy && !attachmentError && attachmentRows.map((item) => (
                      <div key={item.id} className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--workspace-border-soft)] py-2">
                        <span className="min-w-0 break-words">
                          {item.name}
                          <span className="ml-2 text-[var(--workspace-text-faint)]">
                            {item.size.toLocaleString()} bytes
                          </span>
                        </span>
                        {folder !== "Junk" && item.kind === "file" && item.size <= 2 * 1024 * 1024 ? (
                          <button type="button"
                            disabled={attachmentDownloadBusy !== null}
                            onClick={() => void downloadAttachment(item)}
                            className="rounded-lg border border-[var(--workspace-border)] px-3 py-1.5 text-xs font-medium disabled:opacity-50"
                            aria-label={"Download " + item.name}>
                            {attachmentDownloadBusy === item.id ? "Downloading…" : "Download"}
                          </button>
                        ) : (
                          <span className="text-[var(--workspace-text-faint)]">
                            {folder === "Junk"
                              ? "Download disabled for junk mail"
                              : item.size > 2 * 1024 * 1024
                                ? "Over 2 MB limit"
                                : "Download not supported for this attachment type"}
                          </span>
                        )}
                      </div>
                    ))}
                    {!attachmentBusy && !attachmentError && attachmentRows.length === 0 && (
                      <p>No attachment details returned.</p>
                    )}
                    {attachmentHasMore && <p>Only the first 25 attachments are shown.</p>}
                    {attachmentDownloadError && <p role="alert" className="mt-2">{attachmentDownloadError}</p>}
                    <p className="mt-2">
                      {folder === "Junk"
                        ? "Attachments are listed for reference. Downloads are disabled for safety."
                        : "Read-only downloads of file attachments up to 2 MB. No messages are changed."}
                    </p>
                  </section>
                )}
                {folder === "Junk" && (
                  <p role="note" className="mt-4 text-xs text-[var(--workspace-text-muted)]">
                    Links in junk mail are displayed as plain text and cannot be clicked here.
                  </p>
                )}
                <div className="mt-6 space-y-4 whitespace-pre-wrap break-words text-sm leading-7">
                  {selectedMessage.body.map((part, index) => (
                    <p key={index}>
                      {folder === "Junk" ? part : <MicrosoftLinkifiedText text={part} />}
                    </p>
                  ))}
                </div>
                <p className="mt-8 text-xs text-[var(--workspace-text-faint)]">
                  {folder === "Sent"
                    ? "Sent Items are read-only. Sending and replies are not enabled yet."
                    : folder === "Archive"
                      ? "Archive is read-only. Replying and changing mailbox folders are not enabled yet."
                      : folder === "Drafts"
                        ? "Drafts are read-only. Editing, sending, and deleting drafts are disabled."
                        : folder === "Deleted"
                          ? "Deleted Items are read-only. Restoring and permanently deleting messages are disabled."
                          : folder === "Junk"
                            ? "Junk Email is read-only. Spam reporting, moving, and deleting are disabled."
                            : "Replies and other mailbox changes are not enabled yet."}
                </p>
              </article>
            )}
          </section>
        </div>
      )}
    </div>
  );
});
