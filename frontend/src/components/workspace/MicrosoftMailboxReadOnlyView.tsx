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
  setMicrosoftMessageUnread,
  archiveMicrosoftInboxMessage,
  deleteMicrosoftInboxMessage,
  discardMicrosoftDraft,
  type LiveInboxMessageSnapshot,
  type MicrosoftAttachmentMetadata,
} from "../../lib/inboxConnectionApi";
import { applyConfirmedMicrosoftUnread, isMicrosoftInboxProjection, shouldAutoMarkMicrosoftOpenedMessageRead } from "./microsoftReadState";
import { MicrosoftComposePanel } from "./MicrosoftComposePanel";
import { MICROSOFT_DRAFT_ATTACHMENTS_RELEASED } from "../../lib/microsoftDraftAttachmentPolicy";
import { MICROSOFT_COMPOSE_DRAFTS_RELEASED, MICROSOFT_RESPONSE_DRAFTS_RELEASED, type MicrosoftResponseMode, inspectMicrosoftDraftAcceptance, type MicrosoftDraftAcceptance } from "../../lib/microsoftComposeDraftApi";
import { MicrosoftComposeDraftPanel } from "./MicrosoftComposeDraftPanel";
import { MicrosoftDraftEditor } from "./MicrosoftDraftEditor";
import { MicrosoftRichDraftMetadataEditor } from "./MicrosoftRichDraftMetadataEditor";
import { MicrosoftComposeDesignPreview } from "./MicrosoftComposeDesignPreview";
import { MicrosoftMessageListRow } from "./MicrosoftMessageListRow";
import { MicrosoftFullMessageModal } from "./MicrosoftFullMessageModal";
import { MicrosoftAttachmentList } from "./MicrosoftAttachmentList";
import { fetchMicrosoftAttachments, downloadMicrosoftAttachmentFile } from "../../lib/microsoftAttachmentReadApi";
import { MicrosoftConversationNav } from "./MicrosoftConversationNav";
import { getMicrosoftConversationRows } from "./microsoftConversationRows";
import { MicrosoftPlannedMessageActions } from "./MicrosoftPlannedMessageActions";
import { MicrosoftMailboxFolderRail } from "./MicrosoftMailboxFolderRail";
import { MicrosoftConnectionState, type MicrosoftConnectionStatus } from "./MicrosoftConnectionState";
import { MicrosoftMessageListControls } from "./MicrosoftMessageListControls";
import { MicrosoftLinkifiedText } from "./MicrosoftLinkifiedText";
import { MicrosoftInertHtmlStage } from "./MicrosoftInertHtmlStage";
import { filterMicrosoftLoadedMessages, type MicrosoftMessageSortOrder } from "./microsoftMailboxListFilters";
import {
  microsoftVisibleFolderRows,
  isMicrosoftFilteredProtectionActive,
} from "./microsoftVirtualFiltered";
import { useMicrosoftFilteredLearning } from "./useMicrosoftFilteredLearning";
import { MicrosoftLearningControls } from "./MicrosoftLearningControls";
import { normalizeSenderLearningKey } from "../../lib/learningEngine";
import { isMicrosoftPriorityMessage } from "./microsoftLearningRules";
import type { MicrosoftLearningRule } from "./microsoftLearningRules";

// UI convenience only. Real production write prevention is enforced before
// any Graph mutation at the server boundary in microsoft_production_pilot.py.
const MICROSOFT_READ_ONLY_PILOT = true;
// Must remain false until the independently audited sender gate is released.
const MICROSOFT_OUTBOUND_ACTIONS_RELEASED = false;
// Separate release gate: exact Drafts -> Deleted Items is not owner-approved yet.
const MICROSOFT_DRAFT_DISCARD_ACTIONS_RELEASED = false;
// Independent owner approval is required before real Drafts edits.
const MICROSOFT_DRAFT_EDIT_ACTIONS_RELEASED = false;
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
  onConnectionStatusChange?: (status: MicrosoftConnectionStatus) => void;
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
  onConnectionStatusChange,
}: MicrosoftMailboxReadOnlyViewProps) {
  const [connectionStatus, setConnectionStatus] = useState<MicrosoftConnectionStatus>(
    connected ? "checking" : "action_required"
  );
  const statusCallbackRef = React.useRef(onConnectionStatusChange);
  useEffect(() => { statusCallbackRef.current = onConnectionStatusChange; }, [onConnectionStatusChange]);
  const reportGraphStatus = useCallback((status: MicrosoftConnectionStatus) => {
    setConnectionStatus(status);
    statusCallbackRef.current?.(status);
  }, []);
  const inboxTicket = React.useRef(0);
  const messageTicket = React.useRef(0);
  const attachmentTicket = React.useRef(0);
  const attachmentDownloadTicket = React.useRef(0);
  const attachmentDownloadInFlight = React.useRef<string | null>(null);
  const selectedIdRef = React.useRef<string | null>(null);
  const folderRef = React.useRef<MicrosoftFolder>("Inbox");
  const [folder, setFolder] = useState<MicrosoftFolder>("Inbox");
  const [composeDesignPreviewOpen, setComposeDesignPreviewOpen] = useState(false);
  const [draftAcceptance, setDraftAcceptance] = useState<MicrosoftDraftAcceptance | null>(null);
  const [permissionChecked, setPermissionChecked] = useState(false);
  const acceptanceMailbox = React.useRef(mailbox.id);
  const permissionTicket = React.useRef(0);
  acceptanceMailbox.current = mailbox.id;
  const checkDraftPermissions = async () => {
    const id = mailbox.id, ticket = ++permissionTicket.current;
    const result = await inspectMicrosoftDraftAcceptance(id);
    if (acceptanceMailbox.current !== id || permissionTicket.current !== ticket) return;
    setDraftAcceptance(result); setPermissionChecked(true);
  };
  useEffect(() => {
    permissionTicket.current++; setDraftAcceptance(null); setPermissionChecked(false);
    return () => { permissionTicket.current++; };
  }, [mailbox.id, connected]);
  const acceptance = draftAcceptance?.canInspect ? { canWrite: draftAcceptance.canWrite } : undefined;
  const composeAvailable = MICROSOFT_COMPOSE_DRAFTS_RELEASED || Boolean(acceptance);
  const responseAvailable = MICROSOFT_RESPONSE_DRAFTS_RELEASED && MICROSOFT_COMPOSE_DRAFTS_RELEASED || Boolean(acceptance?.canWrite);
  const [composeDraftTarget, setComposeDraftTarget] = useState<{ providerMessageId?: string; responseMode?: MicrosoftResponseMode; sourceProviderMessageId?: string } | null>(null);
  const [draftEditId, setDraftEditId] = useState<string | null>(null);
  const [richDraftEditId, setRichDraftEditId] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [composer, setComposer] = useState<{
    mode: "new" | "reply" | "reply_all" | "forward";
    sourceProviderMessageId?: string;
    sourceDisplay?: string;
    source?: {serverMailboxId: string; providerMessageId: string; providerFolder: string} | null;
    selectedProviderMessageId?: string | null;
    sourceIsExact?: boolean;
  } | null>(null);
  const mutationTicket = React.useRef(0);
  const mutationInFlight = React.useRef(false);
  const [uncertainDraftDiscards, setUncertainDraftDiscards] = useState<ReadonlySet<string>>(() => new Set());
  const [mutationBusy, setMutationBusy] = useState(false);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [mutationConfirmation, setMutationConfirmation] = useState<string | null>(null);
  const [rows, setRows] = useState<LiveInboxMessageSnapshot[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [fullMessageTargetId, setFullMessageTargetId] = useState<string | null>(null);
  const [learningRequestId, setLearningRequestId] = useState<string | null>(null);
  const [rowContext, setRowContext] = useState<{ id: string; x: number; y: number } | null>(null);
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
  const [priorityOnly, setPriorityOnly] = useState(false);
  const [sortOrder, setSortOrder] = useState<MicrosoftMessageSortOrder>("newest");
  const [placementNotice, setPlacementNotice] = useState<string | null>(null);
  useEffect(() => {
    setComposeDesignPreviewOpen(false);
    setComposeDraftTarget(null);
    setDraftEditId(null);
    setRichDraftEditId(null);
    setFullMessageTargetId(null);
    setLearningRequestId(null);
    setRowContext(null);
  }, [mailbox.id, connected, folder, page]);
  const microsoftFiltering = useMicrosoftFilteredLearning(mailbox.id, connected);
  const folderRows = useMemo(
    () => microsoftVisibleFolderRows(
      rows, folder, microsoftFiltering.learned, microsoftFiltering.placements,
      microsoftFiltering.ready, microsoftFiltering.rules,
    ),
    [rows, folder, microsoftFiltering.learned, microsoftFiltering.placements, microsoftFiltering.ready, microsoftFiltering.rules],
  );
  const visibleRows = useMemo(
    () => filterMicrosoftLoadedMessages(
      priorityOnly
        ? folderRows.filter((row) => isMicrosoftPriorityMessage(row, microsoftFiltering.rules))
        : folderRows,
      { folder, search: searchQuery, unreadOnly, sortOrder },
    ),
    [folderRows, folder, searchQuery, unreadOnly, sortOrder, priorityOnly, microsoftFiltering.rules],
  );
  const conversationRows = useMemo(
    () => getMicrosoftConversationRows(folderRows, selectedId, mailbox.id),
    [folderRows, selectedId, mailbox.id],
  );
  const hasActiveListFilter = priorityOnly || Boolean(searchQuery.trim()) || ((folder === "Inbox" || folder === "Filtered" || folder === "Archive" || folder === "Deleted" || folder === "Junk") && unreadOnly);

  useEffect(() => {
    if (!rowContext) return;
    const close = (event: PointerEvent) => {
      if (event.target instanceof Element &&
          event.target.closest('[aria-label="Microsoft message context menu"]')) return;
      setRowContext(null);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setRowContext(null);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [rowContext]);

  const refresh = useCallback(async () => {
    if (!connected || mutationInFlight.current || composer !== null) {
      return;
    }
    const ticket = ++inboxTicket.current;
    reportGraphStatus("checking");
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
        reportGraphStatus(
          response.error?.code === "microsoft_reconnect_required" ||
          response.error?.code === "microsoft_unauthorized"
            ? "action_required" : "temporary_issue"
        );
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
      reportGraphStatus("connected");
    } catch {
      if (ticket === inboxTicket.current) {
        reportGraphStatus("temporary_issue");
        setListError("The Microsoft " + folder + " could not be refreshed.");
      }
    } finally {
      if (ticket === inboxTicket.current) {
        setListBusy(false);
      }
    }
  }, [connected, mailbox.id, folder, composer, page, reportGraphStatus]);

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

  const openComposer = useCallback((mode: "new" | "reply" | "reply_all" | "forward") => {
    if (!connected || composer !== null || composeDraftTarget !== null || mutationInFlight.current || messageBusy) return;
    if (mode !== "new") {
      if ((!MICROSOFT_COMPOSE_DRAFTS_RELEASED || !MICROSOFT_RESPONSE_DRAFTS_RELEASED) && !acceptance?.canWrite ||
          acceptance && mode === "reply_all" ||
          !["Inbox", "Filtered", "Archive", "Sent"].includes(folderRef.current) ||
          !selectedMessage?.providerMessageId || selectedMessage.serverMailboxId !== mailbox.id ||
          selectedIdRef.current !== selectedMessage.providerMessageId) return;
      setComposeDraftTarget({ responseMode: mode, sourceProviderMessageId: selectedMessage.providerMessageId });
      return;
    }
    if (MICROSOFT_READ_ONLY_PILOT || !MICROSOFT_OUTBOUND_ACTIONS_RELEASED) return;
    setComposer({ mode: "new", source: null, selectedProviderMessageId: null, sourceIsExact: false });
  }, [connected, composer, composeDraftTarget, selectedMessage, mailbox.id, messageBusy, acceptance?.canWrite]);

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
      // The listing already has authoritative Graph attachment presence.
      // Begin its read in parallel with exact body loading when available.
      // Inline-only messages with hasAttachments=false still get their
      // metadata after the exact HTML CID markers are confirmed.
      const hasKnownAttachments = rows.some(
        (entry) => entry.providerMessageId === providerMessageId &&
          entry.hasAttachments === true,
      );
      const attachmentFolder = requestedFolder === "Filtered" ? "Inbox" : requestedFolder;
      const prefetchedAttachments = hasKnownAttachments && attachmentFolder !== "Junk"
        ? fetchMicrosoftAttachments({
            mailboxId: mailbox.id, providerMessageId, folder: attachmentFolder,
          })
        : null;
      if (prefetchedAttachments) setAttachmentBusy(true);
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
        response.message.providerMessageId !== providerMessageId ||
        response.message.serverMailboxId !== mailbox.id
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
        // Automatic open-to-read uses the exact-ID, owned and
        // server-confirmed Inbox Graph flag mutation. Other Microsoft
        // mailbox actions remain unavailable under the Production pilot.
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
            !isMicrosoftInboxProjection(folderRef.current) ||
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
      // Graph hasAttachments excludes inline-only messages. Detect the inert
      // CID markers in the authenticated exact HTML response as well.
      if (response.message.hasAttachments === true ||
          requestedFolder !== "Junk" &&
          typeof response.message.bodyHtml === "string" &&
          response.message.bodyHtml.includes("data-microsoft-cid=")) {
        setAttachmentBusy(true);
        void (prefetchedAttachments ?? fetchMicrosoftAttachments({
          mailboxId: mailbox.id,
          providerMessageId,
          folder: attachmentFolder,
        })).then((result) => {
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
      } else if (prefetchedAttachments) {
        // Do not leave the reader waiting when the exact response says the
        // message has neither downloadable files nor inline CID references.
        setAttachmentBusy(false);
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

  const invalidateDraftAttachments = useCallback((providerMessageId: string) => {
    if (selectedIdRef.current !== providerMessageId || folderRef.current !== "Drafts") return;
    messageTicket.current += 1;
    attachmentTicket.current += 1;
    attachmentDownloadTicket.current += 1;
    setSelectedMessage(null);
    setAttachmentRows([]);
    setMessageBusy(false);
    setAttachmentBusy(false);
    setFullMessageTargetId(null);
    setMessageError("Attachment status needs verification. Reopen this draft to check its status.");
  }, []);

  const refreshSavedDraft = useCallback((providerMessageId: string) => {
    setDraftEditId(null);
    setRichDraftEditId(null);
    if (!connected || folderRef.current !== "Drafts") return;
    // The list refresh retains an existing selected body. Reload that exact
    // draft after a confirmed save, without taking over a newer selection.
    if (selectedIdRef.current === providerMessageId) {
      void openMessage(providerMessageId);
    }
    void refresh();
  }, [connected, openMessage, refresh]);

  const saveSenderLearning = useCallback((rule: MicrosoftLearningRule) => {
    const activeFolder = folderRef.current;
    const message = selectedMessage;
    if (!connected || !microsoftFiltering.ready || mutationBusy ||
        !message?.providerMessageId || selectedIdRef.current !== message.providerMessageId ||
        (activeFolder !== "Inbox" && activeFolder !== "Filtered") ||
        (rule.priority === "show_less" && isMicrosoftFilteredProtectionActive(message))) return false;
    if (!microsoftFiltering.saveRule(message.from, rule)) return false;
    const rerouted = (activeFolder === "Inbox" && rule.priority === "show_less") ||
      (activeFolder === "Filtered" && rule.priority !== "show_less");
    if (rerouted) {
      selectedIdRef.current = null;
      messageTicket.current += 1;
      attachmentTicket.current += 1;
      attachmentDownloadTicket.current += 1;
      setSelectedId(null);
      setSelectedMessage(null);
      setFullMessageTargetId(null);
      setAttachmentRows([]);
      setAttachmentBusy(false);
      setAttachmentError(null);
      setAttachmentDownloadBusy(null);
      setAttachmentDownloadError(null);
    }
    setPlacementNotice(
      "Learning saved for this Microsoft mailbox." +
      (rerouted ? " The sender appears under " +
        (rule.priority === "show_less" ? "Filtered." : "Inbox.") : ""),
    );
    return true;
  }, [connected, microsoftFiltering, mutationBusy, selectedMessage]);

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
      selectedMessage?.serverMailboxId !== mailbox.id ||
      attachmentDownloadInFlight.current === JSON.stringify([mailbox.id, messageId]) ||
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
    attachmentDownloadInFlight.current = JSON.stringify([mailbox.id, messageId]);
    const failDownload = (message: string) => {
      setAttachmentDownloadError(message);
      if (typeof window.alert === "function") window.alert(message);
    };
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
          typeof result.size !== "number" || (result.size !== item.size && result.sizeVerifiedAsPng !== true) ||
          typeof result.base64 !== "string" || !result.fileName) {
        failDownload(
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
        failDownload("This attachment could not be downloaded.");
      }
    } finally {
      // Release this request's synchronous lock even after navigation, while
      // leaving any newer message's download lock and UI untouched.
      if (attachmentDownloadInFlight.current === JSON.stringify([mailbox.id, messageId])) {
        attachmentDownloadInFlight.current = null;
      }
      if (ticket === attachmentDownloadTicket.current &&
          currentMessageTicket === messageTicket.current) {
        setAttachmentDownloadBusy(null);
      }
    }
  }, [attachmentDownloadBusy, attachmentRows, connected, mailbox.id, selectedMessage]);

  // Manual Inbox read-state changes reuse the owner-bound, exact-ID Graph
  // transport. Production currently rejects nextUnread=true at the server
  // source-only policy gate until a separately approved release.
  const changeReadState = useCallback(async (nextUnread: boolean) => {
    const targetId = selectedMessage?.providerMessageId;
    if (
      !connected ||
      folderRef.current !== "Inbox" ||
      selectedMessage?.serverMailboxId !== mailbox.id ||
      selectedMessage?.providerFolder !== "INBOX" ||
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

  // Inbox -> Archive is the only authorized move. The Production endpoint
  // has a dedicated source-only release gate; other Microsoft writes stay denied.
  const archiveSelectedMessage = useCallback(async () => {
    const targetId = selectedMessage?.providerMessageId;
    if (!connected || folderRef.current !== "Inbox" || mutationInFlight.current ||
        !targetId || selectedIdRef.current !== targetId ||
        selectedMessage?.serverMailboxId !== mailbox.id ||
        selectedMessage?.providerFolder !== "INBOX") return;

    mutationInFlight.current = true;
    const ticket = ++mutationTicket.current;
    inboxTicket.current += 1;
    setListBusy(false);
    setMutationBusy(true);
    setMutationError(null);
    setMutationConfirmation(null);
    try {
      const result = await archiveMicrosoftInboxMessage({
        mailboxId: mailbox.id, providerMessageId: targetId,
      });
      if (ticket !== mutationTicket.current ||
          folderRef.current !== "Inbox" ||
          selectedIdRef.current !== targetId) return;
      if (!result.ok || result.providerMessageId !== targetId || result.archived !== true) {
        setMutationError(
          !result.ok && result.error?.code === "microsoft_write_permission_required"
            ? "Reconnect Microsoft to grant permission to archive messages."
            : !result.ok && result.error?.code === "microsoft_pilot_read_only"
              ? "Microsoft Archive is not enabled on Production yet."
              : "Archive could not be confirmed. Refresh Inbox before trying again.",
        );
        return;
      }

      // Only a confirmed Archive result may remove the exact message from the
      // current Inbox. Never imply success based on a sent/uncertain request.
      messageTicket.current += 1;
      attachmentTicket.current += 1;
      attachmentDownloadTicket.current += 1;
      selectedIdRef.current = null;
      setFullMessageTargetId(null);
      setLearningRequestId(null);
      setRowContext(null);
      setAttachmentRows([]);
      setAttachmentBusy(false);
      setAttachmentError(null);
      setSelectedId(null);
      setSelectedMessage(null);
      setRows((current) => current.filter((message) =>
        message.providerMessageId !== targetId
      ));
      setMutationConfirmation("Archived. The message is now in Archive.");
    } catch {
      if (ticket === mutationTicket.current) {
        setMutationError("Archive could not be confirmed. Refresh Inbox before trying again.");
      }
    } finally {
      if (ticket === mutationTicket.current) {
        mutationInFlight.current = false;
        setMutationBusy(false);
      }
    }
  }, [connected, mailbox.id, selectedMessage]);

  // Soft-delete moves only the selected real Inbox item into Deleted Items.
  // Production denies this new mutation independently of released Archive.
  const deleteSelectedMessage = useCallback(async () => {
    const targetId = selectedMessage?.providerMessageId;
    if (!connected || folderRef.current !== "Inbox" || mutationInFlight.current ||
        !targetId || selectedIdRef.current !== targetId ||
        selectedMessage?.serverMailboxId !== mailbox.id ||
        selectedMessage?.providerFolder !== "INBOX") return;

    mutationInFlight.current = true;
    const ticket = ++mutationTicket.current;
    inboxTicket.current += 1;
    setListBusy(false);
    setMutationBusy(true);
    setMutationError(null);
    setMutationConfirmation(null);
    try {
      const result = await deleteMicrosoftInboxMessage({
        mailboxId: mailbox.id, providerMessageId: targetId,
      });
      if (ticket !== mutationTicket.current ||
          folderRef.current !== "Inbox" ||
          selectedIdRef.current !== targetId) return;
      if (!result.ok || result.providerMessageId !== targetId || result.deleted !== true) {
        setMutationError(
          !result.ok && result.error?.code === "microsoft_write_permission_required"
            ? "Reconnect Microsoft to grant permission to move messages to Deleted Items."
            : !result.ok && result.error?.code === "microsoft_pilot_read_only"
              ? "Microsoft Delete is not enabled on Production yet."
              : "Delete could not be confirmed. Refresh Inbox before trying again.",
        );
        return;
      }

      // Only a confirmed Deleted Items result may remove the exact message from the
      // current Inbox. Never imply success based on a sent/uncertain request.
      messageTicket.current += 1;
      attachmentTicket.current += 1;
      attachmentDownloadTicket.current += 1;
      selectedIdRef.current = null;
      setFullMessageTargetId(null);
      setLearningRequestId(null);
      setRowContext(null);
      setAttachmentRows([]);
      setAttachmentBusy(false);
      setAttachmentError(null);
      setSelectedId(null);
      setSelectedMessage(null);
      setRows((current) => current.filter((message) =>
        message.providerMessageId !== targetId
      ));
      setMutationConfirmation("Moved to Deleted Items.");
    } catch {
      if (ticket === mutationTicket.current) {
        setMutationError("Delete could not be confirmed. Refresh Inbox before trying again.");
      }
    } finally {
      if (ticket === mutationTicket.current) {
        mutationInFlight.current = false;
        setMutationBusy(false);
      }
    }
  }, [connected, mailbox.id, selectedMessage]);

  // Draft discard is a distinct, unreleased Microsoft-only permission. Never
  // remove the row until the owner-authorized Graph destination is verified.
  const discardSelectedDraft = useCallback(async () => {
    const targetId = selectedMessage?.providerMessageId;
    if (!MICROSOFT_DRAFT_DISCARD_ACTIONS_RELEASED ||
        !connected || folderRef.current !== "Drafts" || mutationInFlight.current ||
        !targetId || selectedIdRef.current !== targetId ||
        uncertainDraftDiscards.has(JSON.stringify([mailbox.id, targetId])) ||
        selectedMessage?.serverMailboxId !== mailbox.id ||
        selectedMessage?.providerFolder !== "DRAFTS") return;
    mutationInFlight.current = true;
    const ticket = ++mutationTicket.current;
    inboxTicket.current += 1;
    setListBusy(false);
    setMutationBusy(true);
    setMutationError(null);
    setMutationConfirmation(null);
    try {
      const result = await discardMicrosoftDraft({
        mailboxId: mailbox.id, providerMessageId: targetId,
      });
      if (!result.ok && result.outcome === "uncertain") {
        setUncertainDraftDiscards(current => new Set(current).add(JSON.stringify([mailbox.id, targetId])));
      }
      if (ticket !== mutationTicket.current || folderRef.current !== "Drafts" ||
          selectedIdRef.current !== targetId) return;
      if (!result.ok) {
        setMutationError(result.error.message);
        return;
      }
      messageTicket.current += 1;
      attachmentTicket.current += 1;
      attachmentDownloadTicket.current += 1;
      selectedIdRef.current = null;
      setFullMessageTargetId(null);
      setLearningRequestId(null);
      setRowContext(null);
      setAttachmentRows([]);
      setAttachmentBusy(false);
      setAttachmentError(null);
      setSelectedId(null);
      setSelectedMessage(null);
      setRows((current) => current.filter((message) => message.providerMessageId !== targetId));
      setMutationConfirmation("Draft moved to Deleted Items.");
    } catch {
      setUncertainDraftDiscards(current => new Set(current).add(JSON.stringify([mailbox.id, targetId])));
      if (ticket === mutationTicket.current && folderRef.current === "Drafts" && selectedIdRef.current === targetId) {
        setMutationError("Discard status is uncertain. Check Drafts and Deleted Items in Outlook. No further move will be attempted here during this session.");
      }
    } finally {
      if (ticket === mutationTicket.current) {
        mutationInFlight.current = false;
        setMutationBusy(false);
      }
    }
  }, [connected, mailbox.id, selectedMessage, uncertainDraftDiscards]);

  const attachmentList = selectedMessage?.serverMailboxId === mailbox.id ? (
    <MicrosoftAttachmentList attachments={attachmentRows} busy={attachmentBusy}
      error={attachmentError} hasMore={attachmentHasMore} folder={folder}
      downloadBusy={attachmentDownloadBusy} downloadError={attachmentDownloadError}
      onDownload={item => { void downloadAttachment(item); }} />
  ) : null;

  // Compose belongs to the folder rail and never invokes a send during the pilot.
  const composeAction = (
    <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          aria-haspopup={MICROSOFT_READ_ONLY_PILOT || composeAvailable ? "dialog" : undefined}
          aria-label={MICROSOFT_READ_ONLY_PILOT && !composeAvailable ? "Compose message (design preview only)" : "Compose message"}
          title={MICROSOFT_READ_ONLY_PILOT && !composeAvailable ? "Explore the new-message design. Nothing can be sent or saved." : "Compose new message"}
          disabled={!connected || composer !== null || mutationBusy || composeDesignPreviewOpen || composeDraftTarget !== null}
          onClick={() => {
            if (composeAvailable) {
              setComposeDraftTarget({});
            } else if (MICROSOFT_READ_ONLY_PILOT) {
              setComposeDesignPreviewOpen(true);
            } else {
              openComposer("new");
            }
          }}
          className="inline-flex h-9 items-center justify-center gap-2 rounded-full border border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] px-3.5 text-[0.69rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-accent-text)] transition hover:brightness-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          <svg aria-hidden="true" viewBox="0 0 16 16" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
            <path d="M8 3v10M3 8h10" />
          </svg>
          Compose
        </button>
        <button type="button" className="text-xs underline" disabled={!connected}
          onClick={() => { void checkDraftPermissions(); }}>Check Microsoft draft permissions</button>
        {permissionChecked && <span role="status" className="text-xs">
          {draftAcceptance ? `Stored Mail.ReadWrite: ${draftAcceptance.mailReadWrite ? "granted" : "not granted"}${draftAcceptance.tokenExpired ? " (token expired; connection must revalidate)" : ""}. Draft test: ${draftAcceptance.canWrite ? "authorized" : "off"}. Sending remains off.` : "Permissions could not be verified. Draft testing remains off."}
        </span>}
    </div>
  );

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden text-[var(--workspace-text)]">
      <header className="flex shrink-0 items-center justify-between gap-4 border-b border-[var(--workspace-border-soft)] px-4 py-4 sm:px-6">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full px-2.5 text-xs font-medium text-[var(--workspace-text-muted)] transition hover:bg-[var(--workspace-hover-surface)] hover:text-[var(--workspace-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)]"
          aria-label="Back to workspace"
        >
          <svg aria-hidden="true" viewBox="0 0 16 16" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12.5 8h-9M7 4.5 3.5 8 7 11.5" />
          </svg>
          Back
        </button>
        <div className="ml-auto flex min-w-0 items-center gap-3 sm:gap-4">
          <div className="min-w-0 text-right">
            <h1 className="truncate text-sm font-semibold">{mailbox.title}</h1>
            <p className="truncate text-xs text-[var(--workspace-text-muted)]">{mailbox.email}</p>
          </div>
          <MicrosoftConnectionState
            status={connectionStatus}
            onActionRequired={onOpenConnectionSettings}
          />
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
          source={composer.source}
          selectedProviderMessageId={composer.selectedProviderMessageId}
          sourceIsExact={composer.sourceIsExact}
          connected={connected}
          onClose={() => setComposer(null)}
          onOpenSent={() => {
            setComposer(null);
            selectFolder("Sent", true);
          }}
        />
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          <div data-microsoft-mailbox-toolbar="unified" className="flex shrink-0 flex-wrap gap-2 border-b border-[var(--workspace-border-soft)] px-3 py-2 xl:grid xl:grid-cols-[152px_minmax(275px,37%)_minmax(0,1fr)] xl:gap-0 xl:px-0 xl:py-0">
            <div data-microsoft-toolbar-compose className="flex min-h-[60px] items-center border-r border-[var(--workspace-border-soft)] px-3">
              {composeAction}
            </div>
            <div data-microsoft-toolbar-sync className="flex min-h-[60px] min-w-0 items-center gap-3 border-r border-[var(--workspace-border-soft)] px-3">
              <button type="button"
                aria-label={listBusy ? "Syncing…" : "Sync"}
                title="Sync Microsoft messages"
                disabled={!connected || composer !== null || listBusy || mutationBusy}
                onClick={() => void refresh()}
                className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-[var(--workspace-border)] bg-[var(--workspace-card-subtle)] text-[var(--workspace-text-soft)] transition hover:bg-[var(--workspace-hover-surface)] disabled:cursor-not-allowed disabled:opacity-45"
              >
                <svg aria-hidden="true" viewBox="0 0 16 16" className={"h-4 w-4 " + (listBusy ? "animate-spin" : "")}
                  fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M13 5.5A5 5 0 0 0 4.5 3L3 4.5" />
                  <path d="M3.5 2.5v2h2" />
                  <path d="M3 10.5A5 5 0 0 0 11.5 13L13 11.5" />
                  <path d="M12.5 13.5v-2h-2" />
                </svg>
              </button>
              <div className="min-w-0 flex-1">
                <h2 className="truncate text-[0.8rem] font-semibold">
                  {folder} <span className="font-normal text-[var(--workspace-text-muted)]">
                    ({visibleRows.length}, {visibleRows.filter((row) => row.unread).length} unread shown)
                  </span>
                </h2>
                {lastUpdated && <span role="status" className="block truncate text-[0.67rem] text-[var(--workspace-text-faint)]">Updated {readableTime(lastUpdated)}</span>}
                {mutationConfirmation && !selectedId && (
                  <span role="status" className="block text-[0.72rem] text-[var(--workspace-accent-text)]">
                    {mutationConfirmation}
                  </span>
                )}
              </div>
            </div>
            <div data-microsoft-toolbar-actions className="relative z-20 flex min-h-[60px] items-center justify-end px-4 sm:px-6">
              {MICROSOFT_READ_ONLY_PILOT && (
                <MicrosoftPlannedMessageActions
                  unread={selectedMessage?.unread === true}
                  hasMessage={Boolean(selectedId && selectedMessage && !messageBusy)}
                  canChangeReadState={Boolean(
                    connected && folder === "Inbox" && selectedId &&
                    !messageBusy && selectedMessage?.serverMailboxId === mailbox.id &&
                    selectedMessage?.providerFolder === "INBOX" &&
                    selectedMessage.providerMessageId === selectedId
                  )}
                  changeReadStateBusy={mutationBusy}
                  onChangeReadState={(nextUnread) => { void changeReadState(nextUnread); }}
                  canArchive={Boolean(
                    connected && folder === "Inbox" && selectedId && !messageBusy &&
                    selectedMessage?.serverMailboxId === mailbox.id &&
                    selectedMessage?.providerFolder === "INBOX" &&
                    selectedMessage.providerMessageId === selectedId
                  )}
                  archiveBusy={mutationBusy}
                  onArchive={() => { void archiveSelectedMessage(); }}
                  canDelete={Boolean(
                    connected && folder === "Inbox" && selectedId && !messageBusy &&
                    selectedMessage?.serverMailboxId === mailbox.id &&
                    selectedMessage?.providerFolder === "INBOX" &&
                    selectedMessage.providerMessageId === selectedId
                  )}
                  deleteBusy={mutationBusy}
                  onDelete={() => { void deleteSelectedMessage(); }}
                  canReplyAll={!acceptance}
                  canCompose={Boolean(responseAvailable &&
                    connected && ["Inbox", "Filtered", "Archive", "Sent"].includes(folder) && selectedMessage &&
                    selectedMessage.serverMailboxId === mailbox.id &&
                    selectedId === selectedMessage.providerMessageId && !messageBusy && !mutationBusy)}
                  onCompose={(mode) => { openComposer(mode); }}
                />
              )}
            </div>
          </div>
          <MicrosoftMailboxFolderRail
            folder={folder}
            disabled={!connected || composer !== null || mutationBusy}
            onSelectFolder={selectFolder}
            compact
          />
          <div className="grid h-0 min-h-0 flex-1 grid-cols-1 gap-0 md:grid-cols-[minmax(245px,37%)_minmax(0,1fr)] xl:grid-cols-[152px_minmax(275px,37%)_minmax(0,1fr)]">
            <MicrosoftMailboxFolderRail
              folder={folder}
              disabled={!connected || composer !== null || mutationBusy}
              onSelectFolder={selectFolder}
            />
          <section aria-label={"Microsoft " + folder + " messages"} className="flex min-h-0 flex-col border-b border-[var(--workspace-border-soft)] md:border-b-0 md:border-r">
            <div className="shrink-0 space-y-2 border-b border-[var(--workspace-border-soft)] px-3 py-3">
              <MicrosoftMessageListControls
                mailboxId={mailbox.id}
                searchQuery={searchQuery}
                onSearchChange={setSearchQuery}
                unreadOnly={unreadOnly}
                onUnreadOnlyChange={setUnreadOnly}
                priorityOnly={priorityOnly}
                onPriorityOnlyChange={setPriorityOnly}
                canFilterUnread={folder !== "Sent" && folder !== "Drafts"}
                sortOrder={sortOrder}
                onSortChange={setSortOrder}
                refreshBusy={listBusy}
                refreshDisabled={!connected || composer !== null || listBusy || mutationBusy}
                onRefresh={() => void refresh()}
                hideRefreshButton
              />
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
                  learningRule={microsoftFiltering.rules[normalizeSenderLearningKey(message.from)]}
                  disabled={mutationBusy}
                  dateLabel={readableTime(message.createdAt)}
                  onOpenMessage={(providerMessageId) => {
                    setFullMessageTargetId(null);
                    setLearningRequestId(null);
                    void openMessage(providerMessageId);
                  }}
                  onOpenContextMenu={(providerMessageId, x, y) => {
                    setRowContext({
                      id: providerMessageId,
                      x: Math.max(8, Math.min(x, window.innerWidth - 232)),
                      y: Math.max(8, Math.min(y, window.innerHeight - 150)),
                    });
                  }}
                  onOpenFullMessage={(providerMessageId) => {
                    setFullMessageTargetId(providerMessageId);
                    if (selectedIdRef.current !== providerMessageId) void openMessage(providerMessageId);
                  }}
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
          <div className="flex min-h-0 flex-col">
            <section aria-label="Selected Microsoft message" className="min-h-0 flex-1 overflow-y-auto p-5 sm:p-7">
            {!selectedId && <p className="text-sm text-[var(--workspace-text-muted)]">Select a message to read it.</p>}
            {selectedId && messageBusy && <p role="status" className="text-sm text-[var(--workspace-text-muted)]">Opening message…</p>}
            {selectedId && messageError && <div role="alert" className="rounded-xl border border-[var(--workspace-border)] p-4 text-sm">{messageError}</div>}
            {selectedId && selectedMessage && !messageBusy && (
              <article className="max-w-3xl">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h2 className="min-w-0 flex-1 text-xl font-semibold">{selectedMessage.subject || "(No subject)"}</h2>
                  {(folder === "Inbox" || folder === "Filtered") && (
                    <MicrosoftLearningControls
                      key={selectedMessage.providerMessageId}
                      from={selectedMessage.from}
                      rule={microsoftFiltering.rules[normalizeSenderLearningKey(selectedMessage.from)]}
                      disabled={!microsoftFiltering.ready || mutationBusy}
                      canShowLess={!isMicrosoftFilteredProtectionActive(selectedMessage)}
                      onSave={saveSenderLearning}
                      openOnRequest={learningRequestId === selectedMessage.providerMessageId}
                    />
                  )}
                </div>
                <MicrosoftConversationNav
                  messages={conversationRows}
                  selectedId={selectedMessage.providerMessageId ?? ""}
                  disabled={mutationBusy || listBusy}
                  onSelect={(id) => {
                    if (conversationRows.some((row) => row.providerMessageId === id)) {
                      setFullMessageTargetId(null);
                      setLearningRequestId(null);
                      void openMessage(id);
                    }
                  }}
                />
                <div className="mt-4 border-b border-[var(--workspace-border-soft)] pb-4 text-sm">
                  <p className="font-medium">
                    {folder === "Sent" || folder === "Drafts"
                      ? "To: " + selectedMessage.to
                      : folder === "Deleted"
                        ? (selectedMessage.from || selectedMessage.sender)
                        : selectedMessage.from}
                  </p>
                  {folder === "Drafts" && selectedMessage.cc?.trim() && (
                    <p className="mt-1 break-words text-xs text-[var(--workspace-text-muted)]">
                      Cc: {selectedMessage.cc}
                    </p>
                  )}
                  {folder === "Sent" && (
                    <p className="mt-1 text-xs text-[var(--workspace-text-muted)]">
                      From: {selectedMessage.from}
                    </p>
                  )}
                  <p className="mt-1 text-xs text-[var(--workspace-text-muted)]">
                    {folder === "Deleted" ? "Last changed: " : ""}{readableTime(selectedMessage.createdAt)}
                  </p>
                </div>
                {folder === "Drafts" && (MICROSOFT_DRAFT_EDIT_ACTIONS_RELEASED || composeAvailable) &&
                  selectedMessage.providerFolder === "DRAFTS" && (
                  <div className="mt-4">
                    {composeAvailable && selectedMessage.bodyHtml && (
                      <button type="button" onClick={() => setComposeDraftTarget({ providerMessageId: selectedMessage.providerMessageId })}
                        title="Reopen a text draft or supported formatted draft created in Cuevion"
                        disabled={mutationBusy || !connected || !selectedMessage.providerMessageId}
                        className="mb-2 mr-2 rounded-xl border border-[var(--workspace-border)] px-4 py-2 text-xs font-semibold disabled:opacity-50">
                        Edit in Compose
                      </button>
                    )}
                    {selectedMessage.hasAttachments && !MICROSOFT_DRAFT_ATTACHMENTS_RELEASED ? (
                      <p className="text-xs text-[var(--workspace-text-muted)]">
                        Drafts with attachments cannot be edited here yet.
                      </p>
                    ) : selectedMessage.bodyHtml ? (
                      <button type="button" onClick={() => setRichDraftEditId(selectedMessage.providerMessageId ?? null)}
                        disabled={mutationBusy || !connected || !selectedMessage.providerMessageId}
                        className="rounded-xl border border-[var(--workspace-border)] px-4 py-2 text-xs font-semibold disabled:opacity-50">
                        Edit subject and recipients
                      </button>
                    ) : (
                      <button type="button" onClick={() => {
                        if (composeAvailable && selectedMessage.providerMessageId) setComposeDraftTarget({ providerMessageId: selectedMessage.providerMessageId });
                        else setDraftEditId(selectedMessage.providerMessageId ?? null);
                      }}
                        disabled={mutationBusy || !connected || !selectedMessage.providerMessageId}
                        className="rounded-xl border border-[var(--workspace-border)] px-4 py-2 text-xs font-semibold disabled:opacity-50">
                        Edit draft
                      </button>
                    )}
                  </div>
                )}
                {folder === "Drafts" && MICROSOFT_DRAFT_DISCARD_ACTIONS_RELEASED && (
                  <div className="mt-4 flex flex-wrap items-center gap-3">
                    <button
                      type="button"
                      disabled={mutationBusy || !connected || selectedMessage.providerFolder !== "DRAFTS" || uncertainDraftDiscards.has(JSON.stringify([mailbox.id, selectedMessage.providerMessageId]))}
                      onClick={() => { void discardSelectedDraft(); }}
                      className="rounded-xl border border-[var(--workspace-border)] px-4 py-2 text-xs font-semibold disabled:opacity-50"
                    >
                      {mutationBusy ? "Discarding…" : "Discard draft"}
                    </button>
                    {uncertainDraftDiscards.has(JSON.stringify([mailbox.id, selectedMessage.providerMessageId])) ? (
                      <p role="alert" className="text-xs">Discard status is uncertain. Check Drafts and Deleted Items in Outlook. This draft will not be moved again here during this session.</p>
                    ) : mutationError && <p role="alert" className="text-xs">{mutationError}</p>}
                  </div>
                )}
                {isMicrosoftInboxProjection(folder) && (
                  <>
                <div className="mt-4 flex flex-wrap items-center gap-3">
                  {folder === "Inbox" && !MICROSOFT_READ_ONLY_PILOT && (
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
                {attachmentList}
                {folder === "Junk" && (
                  <p role="note" className="mt-4 text-xs text-[var(--workspace-text-muted)]">
                    Links in junk mail are displayed as plain text and cannot be clicked here.
                  </p>
                )}
                {selectedMessage.bodyHtml && folder !== "Junk" ? (
                  <div className="mt-6">
                    <MicrosoftInertHtmlStage
                      key={mailbox.id + ":" + folder + ":" + selectedMessage.providerMessageId}
                      messageKey={mailbox.id + ":" + folder + ":" + selectedMessage.providerMessageId}
                      html={selectedMessage.bodyHtml}
                      fallbackText={
                        selectedMessage.body.join("\n\n").trim() ||
                        selectedMessage.snippet ||
                        "This message has no readable text."
                      }
                      mailboxId={mailbox.id}
                      providerMessageId={selectedMessage.providerMessageId}
                      folder={folder === "Filtered" ? "Inbox" : folder}
                      attachments={attachmentRows}
                      attachmentsBusy={attachmentBusy}
                    />
                  </div>
                ) : (
                  <div className="mt-6 space-y-4 whitespace-pre-wrap break-words text-sm leading-7">
                    {selectedMessage.body.map((part, index) => (
                      <p key={index}>
                        {folder === "Junk" ? part : <MicrosoftLinkifiedText text={part} />}
                      </p>
                    ))}
                  </div>
                )}
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
          </div>
        </div>
      )}
      {connected && draftEditId && MICROSOFT_DRAFT_EDIT_ACTIONS_RELEASED && folder === "Drafts" && (
        <div className="fixed inset-0 z-[350] bg-black/60 p-4 sm:p-8" role="presentation">
          <div className="mx-auto h-full max-w-3xl overflow-hidden rounded-2xl bg-[var(--workspace-card)] shadow-xl">
            <MicrosoftDraftEditor key={JSON.stringify([mailbox.id, draftEditId])} mailboxId={mailbox.id} providerMessageId={draftEditId}
              onAttachmentStart={() => invalidateDraftAttachments(draftEditId)}
              onClose={() => setDraftEditId(null)}
              onSaved={() => refreshSavedDraft(draftEditId)} />
          </div>
        </div>
      )}
      {connected && richDraftEditId && MICROSOFT_DRAFT_EDIT_ACTIONS_RELEASED && folder === "Drafts" && (
        <div className="fixed inset-0 z-[350] bg-black/60 p-4 sm:p-8" role="presentation">
          <div className="mx-auto h-full max-w-3xl overflow-hidden rounded-2xl bg-[var(--workspace-card)] shadow-xl">
            <MicrosoftRichDraftMetadataEditor key={JSON.stringify([mailbox.id, richDraftEditId])} mailboxId={mailbox.id} providerMessageId={richDraftEditId}
              onAttachmentStart={() => invalidateDraftAttachments(richDraftEditId)}
              onClose={() => setRichDraftEditId(null)}
              onSaved={() => refreshSavedDraft(richDraftEditId)} />
          </div>
        </div>
      )}
      {connected && rowContext && (
        <div role="menu" aria-label="Microsoft message context menu"
          className="fixed z-[330] min-w-[205px] rounded-[14px] border border-[var(--workspace-menu-border)] bg-[var(--workspace-menu-bg)] p-1.5 shadow-[0_16px_36px_rgba(20,18,16,0.28)]"
          style={{ left: rowContext.x, top: rowContext.y }}>
          {([
            { label: "Open message", mode: "open" },
            { label: "Open full message", mode: "full" },
            { label: "Learning", mode: "learning" },
          ] as const).map((option) => (
            <button key={option.mode} role="menuitem" type="button"
              className="block w-full rounded-lg px-3 py-2 text-left text-xs text-[var(--workspace-text)] hover:bg-[var(--workspace-hover-surface)]"
              onClick={() => {
                const targetId = rowContext.id;
                setRowContext(null);
                if (option.mode === "full") {
                  setFullMessageTargetId(targetId);
                } else if (option.mode === "learning") {
                  setLearningRequestId(targetId);
                  setFullMessageTargetId(null);
                } else {
                  setFullMessageTargetId(null);
                  setLearningRequestId(null);
                }
                if (selectedIdRef.current !== targetId) void openMessage(targetId);
              }}>
              {option.label}
            </button>
          ))}
        </div>
      )}
      {connected && fullMessageTargetId && selectedId === fullMessageTargetId &&
        selectedMessage?.providerMessageId === fullMessageTargetId && !messageBusy && (
          <MicrosoftFullMessageModal
            message={selectedMessage}
            attachmentList={attachmentList}
            mailboxId={mailbox.id}
            folder={folder}
            attachments={attachmentRows}
            attachmentsBusy={attachmentBusy}
            learningRule={microsoftFiltering.rules[normalizeSenderLearningKey(selectedMessage.from)]}
            learningDisabled={!microsoftFiltering.ready || mutationBusy}
            canShowLess={!isMicrosoftFilteredProtectionActive(selectedMessage)}
            onSaveLearning={saveSenderLearning}
            conversationRows={conversationRows}
            onSelectConversationMessage={(id) => {
              if (conversationRows.some((row) => row.providerMessageId === id)) {
                setFullMessageTargetId(id);
                void openMessage(id);
              }
            }}
            onClose={() => setFullMessageTargetId(null)}
          />
        )}
      {composeAvailable && connected && composeDraftTarget !== null && (
        <MicrosoftComposeDraftPanel acceptance={acceptance} key={JSON.stringify([mailbox.id, composeDraftTarget])}
          mailboxId={mailbox.id} mailboxEmail={mailbox.email} connected={connected}
          providerMessageId={composeDraftTarget.providerMessageId}
          responseMode={composeDraftTarget.responseMode} sourceProviderMessageId={composeDraftTarget.sourceProviderMessageId}
          onClose={() => setComposeDraftTarget(null)}
          onOpenDrafts={() => { setComposeDraftTarget(null); selectFolder("Drafts"); }}
          onOperationStart={id => { if (id) invalidateDraftAttachments(id); }}
          onSent={id => {
            if (folderRef.current === "Drafts") {
              setRows(current => current.filter(row => row.providerMessageId !== id));
              if (selectedIdRef.current === id) {
                invalidateDraftAttachments(id);
                selectedIdRef.current = null; setSelectedId(null); setMessageError(null);
              }
              void refresh();
            }
          }}
          onSaved={id => {
            if (folderRef.current === "Drafts" && selectedIdRef.current === id) void openMessage(id);
            void refresh();
          }} />
      )}
      {MICROSOFT_READ_ONLY_PILOT && connected && composeDesignPreviewOpen && (
        <MicrosoftComposeDesignPreview
          mailboxEmail={mailbox.email}
          onClose={() => setComposeDesignPreviewOpen(false)}
        />
      )}
    </div>
  );
});
