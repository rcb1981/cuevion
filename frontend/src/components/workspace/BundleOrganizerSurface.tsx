import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  ACTIVE_WORK_PRIORITY_STATUSES,
  countUnreadMessages,
  formatOrganizerSignal,
  resolveOrganizerCategory,
  shouldShowInDemoInbox,
  shouldShowInOrganizerPriority,
  shouldShowInPromoInbox,
  type BundleOrganizerActiveWorkStatus,
} from "./bundleOrganizerFilters";

type BundleOrganizerView = "priority" | "shortlist" | "demo" | "promo" | "trash";
type BundleOrganizerDemoStatusFilter =
  | "all"
  | "unread"
  | "shortlisted"
  | "priority"
  | "replied"
  | "declined";
type BundleOrganizerPromoStatusFilter =
  | "all"
  | "unread"
  | "read"
  | "shortlisted"
  | "priority";
type BundleOrganizerPromoFilter = "new" | "reminders" | "all";
type BundleOrganizerDateSort = "newest" | "oldest";

type BundleOrganizerContextMenuState = {
  anchorX: number;
  anchorY: number;
  messageId: string;
  sourceView: BundleOrganizerView;
  x: number;
  y: number;
};

type BundleOrganizerContextMenuIconName =
  | "category"
  | "forward"
  | "mail"
  | "mailOpen"
  | "priority"
  | "priorityOff"
  | "restore"
  | "rule"
  | "shortlist"
  | "shortlistOff"
  | "smartView"
  | "trash";

type BundleOrganizerContextMenuAction = {
  disabled?: boolean;
  disabledReason?: string;
  icon?: BundleOrganizerContextMenuIconName;
  label: string;
  onSelect?: () => void;
};

type BundleOrganizerSearchRow = {
  message: BundleOrganizerMessage;
  sourceView: BundleOrganizerView;
};

type BundleOrganizerMessage = {
  id: string;
  sender: string;
  subject: string;
  snippet: string;
  body: string[];
  timestamp: string;
  sourceMailbox: string;
  manualCategory?: "demo" | "promo";
  manualCategoryAt?: string | null;
  internalClassification?: BundleOrganizerInternalClassification;
  category?: string;
  signal?: string;
  ui_signal?: string;
  uiSignal?: string;
  unread?: boolean;
  shortlisted?: boolean;
  shortlistedAt?: string | null;
  trashed?: boolean;
  trashedAt?: string | null;
  replied?: boolean;
  repliedAt?: string | null;
  replyHistory?: unknown[];
  declined?: boolean;
  declinedAt?: string | null;
  manualPriority?: boolean | null;
  manualPriorityAt?: string | null;
  active_work_status?: BundleOrganizerActiveWorkStatus | string | null;
  v7_final_priority?: string;
  priorityBadge?: string;
  reason?: string;
  identityKey?: string;
  sortTimestamp?: number;
};

export type BundleOrganizerInternalClassification =
  | "promo"
  | "promo_reminder"
  | "workflow_update"
  | "distributor_update"
  | "business_reminder"
  | "royalty_statement"
  | "finance"
  | "info"
  | "reply"
  | "business"
  | "demo"
  | "high_priority_demo"
  | "incomplete_demo"
  | "unknown";

export type BundleOrganizerWorkspaceMessage = {
  id: string;
  sender: string;
  subject: string;
  snippet: string;
  body: string[];
  timestamp: string;
  sourceMailbox: string;
  manualCategory?: "demo" | "promo";
  manualCategoryAt?: string | null;
  internalClassification?: BundleOrganizerInternalClassification;
  category?: string;
  signal?: string;
  uiSignal?: string;
  unread?: boolean;
  shortlisted?: boolean;
  shortlistedAt?: string | null;
  trashed?: boolean;
  trashedAt?: string | null;
  replied?: boolean;
  repliedAt?: string | null;
  replyHistory?: unknown[];
  declined?: boolean;
  declinedAt?: string | null;
  manualPriority?: boolean | null;
  manualPriorityAt?: string | null;
  active_work_status?: BundleOrganizerActiveWorkStatus | string | null;
  v7_final_priority?: string;
  priorityBadge?: string;
  reason?: string;
  identityKey?: string;
  sortTimestamp?: number;
};

type BundleOrganizerSurfaceProps = {
  liveMessages?: BundleOrganizerWorkspaceMessage[];
  hasLiveWorkspaceData?: boolean;
  connectedInboxCount?: number;
};

const navItems: Array<{
  id: BundleOrganizerView;
  label: string;
  icon: BundleOrganizerView;
}> = [
  { id: "priority", label: "Priority", icon: "priority" },
  { id: "shortlist", label: "Shortlist", icon: "shortlist" },
  { id: "demo", label: "Demo Inbox", icon: "demo" },
  { id: "promo", label: "Promo Inbox", icon: "promo" },
  { id: "trash", label: "Trash", icon: "trash" },
];

const allSourceFilterId = "all";
const demoStatusFilterOptions: Array<{
  id: BundleOrganizerDemoStatusFilter;
  label: string;
}> = [
  { id: "all", label: "All status" },
  { id: "unread", label: "Unread" },
  { id: "shortlisted", label: "Shortlisted" },
  { id: "priority", label: "Priority" },
  { id: "replied", label: "Replied" },
  { id: "declined", label: "Declined" },
];
const promoStatusFilterOptions: Array<{
  id: BundleOrganizerPromoStatusFilter;
  label: string;
}> = [
  { id: "all", label: "All status" },
  { id: "unread", label: "Unread" },
  { id: "read", label: "Read" },
  { id: "shortlisted", label: "Shortlisted" },
  { id: "priority", label: "Priority" },
];
const dateSortOptions: Array<{ id: BundleOrganizerDateSort; label: string }> = [
  { id: "newest", label: "Newest first" },
  { id: "oldest", label: "Oldest first" },
];

const viewCopy: Record<
  BundleOrganizerView,
  {
    title: string;
    eyebrow: string;
    description: string;
    emptyTitle: string;
    emptyDescription: string;
  }
> = {
  priority: {
    title: "Priority",
    eyebrow: "Active Work Queue",
    description: "Priority shows active work, replies, waiting items, and open follow-ups.",
    emptyTitle: "No priority messages.",
    emptyDescription: "Priority demo and promo messages will appear here.",
  },
  shortlist: {
    title: "Shortlist",
    eyebrow: "Saved for follow-up",
    description: "Messages you shortlist from Demo, Promo, and Priority stay collected here.",
    emptyTitle: "No shortlisted messages yet.",
    emptyDescription: "Shortlisted demos and promos will appear here.",
  },
  demo: {
    title: "Demo Inbox",
    eyebrow: "Unified Demo Intake",
    description: "Demos from connected workspace inboxes are filtered into one focused queue.",
    emptyTitle: "No demo messages.",
    emptyDescription: "Demo messages from connected inboxes will appear here.",
  },
  promo: {
    title: "Promo Inbox",
    eyebrow: "Unified Promo Review",
    description: "Promo mail and promo reminders are organized without mixing into demo review.",
    emptyTitle: "No promo messages.",
    emptyDescription: "Promo messages from connected inboxes will appear here.",
  },
  trash: {
    title: "Trash",
    eyebrow: "Organizer-local trash",
    description: "Messages moved out of Organizer views stay here until restored.",
    emptyTitle: "Trash is empty.",
    emptyDescription: "Messages moved out of Organizer views will appear here.",
  },
};

const bundleModeDisabledReason = "Not connected in Bundle mode yet";
const bundleOrganizerWorkflowStorageKey =
  "cuevion-bundle-organizer-workflow-state";
const contextMenuGap = 8;
const contextMenuViewportPadding = 12;
const contextMenuWidth = 190;
const contextMenuEstimatedActionHeight = 37;
const contextMenuVerticalChrome = 12;
const shortlistedPillClass =
  "rounded-full border border-[rgba(246,183,91,0.24)] bg-[rgba(214,137,45,0.14)] px-2 py-0.5 text-[0.68rem] font-medium uppercase tracking-[0.1em] text-[rgba(255,204,125,0.9)]";
const trashedPillClass =
  "rounded-full border border-white/15 bg-white/8 px-2 py-0.5 text-[0.68rem] font-medium uppercase tracking-[0.1em] text-[rgba(245,239,229,0.66)]";
const manualPillClass =
  "rounded-full border border-white/12 bg-white/6 px-2 py-0.5 text-[0.68rem] font-medium uppercase tracking-[0.1em] text-[rgba(245,239,229,0.6)]";

type BundleOrganizerWorkflowState = Record<
  string,
  {
    shortlisted?: boolean;
    shortlistedAt?: string;
    manualCategory?: "demo" | "promo";
    manualCategoryAt?: string;
    manualPriority?: boolean;
    manualPriorityAt?: string;
    trashed?: boolean;
    trashedAt?: string;
  }
>;

function resolvePriorityReason(message: BundleOrganizerMessage) {
  if (message.reason) {
    return message.reason;
  }

  if (message.manualPriority === true) {
    return "Manual priority.";
  }

  if (
    message.active_work_status &&
    ACTIVE_WORK_PRIORITY_STATUSES.has(formatOrganizerSignal(message.active_work_status))
  ) {
    return message.active_work_status.replace(/_/g, " ");
  }

  if (formatOrganizerSignal(message.v7_final_priority) === "priority") {
    return "Priority signal.";
  }

  return null;
}

function normalizeWorkspaceMessages(
  liveMessages: BundleOrganizerWorkspaceMessage[],
): BundleOrganizerMessage[] {
  const messagesByIdentity = new Map<string, BundleOrganizerMessage>();

  liveMessages
    .map((message): BundleOrganizerMessage => ({
      ...message,
      id: `workspace-${message.id}`,
      ui_signal: message.uiSignal,
      priorityBadge:
        message.priorityBadge ??
        (message.internalClassification === "high_priority_demo"
          ? "High-priority demo"
          : undefined),
    }))
    .forEach((message) => {
      const identityKey = message.identityKey ?? message.id;
      const existingMessage = messagesByIdentity.get(identityKey);

      if (
        !existingMessage ||
        (message.sortTimestamp ?? 0) >= (existingMessage.sortTimestamp ?? 0)
      ) {
        messagesByIdentity.set(identityKey, message);
      }
    });

  return Array.from(messagesByIdentity.values()).sort(
    (first, second) => (second.sortTimestamp ?? 0) - (first.sortTimestamp ?? 0),
  );
}

function getWorkflowIdentityKey(message: BundleOrganizerMessage) {
  return message.identityKey ?? message.id;
}

function readBundleOrganizerWorkflowState(): BundleOrganizerWorkflowState {
  if (typeof window === "undefined") {
    return {};
  }

  try {
    const storedValue = window.localStorage.getItem(
      bundleOrganizerWorkflowStorageKey,
    );
    if (!storedValue) {
      return {};
    }

    const parsedValue = JSON.parse(storedValue);
    if (!parsedValue || typeof parsedValue !== "object") {
      return {};
    }

    return parsedValue as BundleOrganizerWorkflowState;
  } catch {
    return {};
  }
}

function writeBundleOrganizerWorkflowState(state: BundleOrganizerWorkflowState) {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(
      bundleOrganizerWorkflowStorageKey,
      JSON.stringify(state),
    );
  } catch {
    // Local workflow state is optional; mailbox data must never depend on it.
  }
}

function applyBundleWorkflowState(
  messages: BundleOrganizerMessage[],
  workflowState: BundleOrganizerWorkflowState,
) {
  return messages.map((message) => {
    const workflowEntry = workflowState[getWorkflowIdentityKey(message)];

    if (!workflowEntry) {
      return message;
    }

    return {
      ...message,
      shortlisted:
        typeof workflowEntry.shortlisted === "boolean"
          ? workflowEntry.shortlisted
          : message.shortlisted,
      shortlistedAt: workflowEntry.shortlistedAt ?? message.shortlistedAt,
      manualCategory:
        workflowEntry.manualCategory === "demo" ||
        workflowEntry.manualCategory === "promo"
          ? workflowEntry.manualCategory
          : message.manualCategory,
      manualCategoryAt:
        workflowEntry.manualCategoryAt ?? message.manualCategoryAt,
      manualPriority:
        typeof workflowEntry.manualPriority === "boolean"
          ? workflowEntry.manualPriority
          : message.manualPriority,
      manualPriorityAt:
        workflowEntry.manualPriorityAt ?? message.manualPriorityAt,
      trashed:
        typeof workflowEntry.trashed === "boolean"
          ? workflowEntry.trashed
          : message.trashed,
      trashedAt: workflowEntry.trashedAt ?? message.trashedAt,
    };
  });
}

function getMessagesForView(
  view: BundleOrganizerView,
  liveMessages: BundleOrganizerMessage[],
) {
  if (view === "trash") {
    return liveMessages.filter((message) => message.trashed === true);
  }

  const activeMessages = liveMessages.filter((message) => message.trashed !== true);

  if (view === "priority") {
    return activeMessages.filter(shouldShowInOrganizerPriority);
  }

  if (view === "shortlist") {
    return activeMessages.filter((message) => message.shortlisted === true);
  }

  if (view === "demo") {
    return activeMessages.filter(shouldShowInDemoInbox);
  }

  return activeMessages.filter(shouldShowInPromoInbox);
}

function getCounts(liveMessages: BundleOrganizerMessage[]) {
  return navItems.reduce<Partial<Record<BundleOrganizerView, number>>>((counts, item) => {
    counts[item.id] = countUnreadMessages(getMessagesForView(item.id, liveMessages));
    return counts;
  }, {});
}

function getViewLabel(view: BundleOrganizerView) {
  return navItems.find((item) => item.id === view)?.label ?? viewCopy[view].title;
}

function resolveMessageSourceFilterId(message: BundleOrganizerMessage) {
  return message.sourceMailbox.trim() || "";
}

function buildSourceFilterOptions(messages: BundleOrganizerMessage[]) {
  const optionsById = new Map<
    string,
    { count: number; id: string; label: string }
  >();

  messages.forEach((message) => {
    const id = resolveMessageSourceFilterId(message);

    if (!id) {
      return;
    }

    const existingOption = optionsById.get(id);

    if (existingOption) {
      existingOption.count += 1;
      return;
    }

    optionsById.set(id, {
      count: 1,
      id,
      label: message.sourceMailbox.trim() || "Unknown inbox",
    });
  });

  return Array.from(optionsById.values()).sort((first, second) =>
    first.label.localeCompare(second.label),
  );
}

function filterMessagesBySource(
  messages: BundleOrganizerMessage[],
  sourceFilterId: string,
) {
  if (sourceFilterId === allSourceFilterId) {
    return messages;
  }

  return messages.filter(
    (message) => resolveMessageSourceFilterId(message) === sourceFilterId,
  );
}

function filterMessagesByDemoStatus(
  messages: BundleOrganizerMessage[],
  statusFilter: BundleOrganizerDemoStatusFilter,
) {
  if (statusFilter === "all") {
    return messages;
  }

  return messages.filter((message) => {
    if (statusFilter === "unread") {
      return message.unread === true;
    }

    if (statusFilter === "shortlisted") {
      return message.shortlisted === true;
    }

    if (statusFilter === "priority") {
      return shouldShowInOrganizerPriority(message);
    }

    if (statusFilter === "replied") {
      return (
        message.replied === true ||
        Boolean(message.repliedAt) ||
        Boolean(message.replyHistory?.length)
      );
    }

    if (statusFilter === "declined") {
      return message.declined === true || Boolean(message.declinedAt);
    }

    return true;
  });
}

function filterMessagesByPromoStatus(
  messages: BundleOrganizerMessage[],
  statusFilter: BundleOrganizerPromoStatusFilter,
) {
  return messages.filter((message) => {
    if (statusFilter === "unread") {
      return message.unread === true;
    }

    if (statusFilter === "read") {
      return message.unread !== true;
    }

    if (statusFilter === "shortlisted") {
      return message.shortlisted === true;
    }

    if (statusFilter === "priority") {
      return shouldShowInOrganizerPriority(message);
    }

    return true;
  });
}

function sortMessagesByDate(
  messages: BundleOrganizerMessage[],
  sortMode: BundleOrganizerDateSort,
) {
  return [...messages].sort((left, right) => {
    const leftTime = left.sortTimestamp ?? 0;
    const rightTime = right.sortTimestamp ?? 0;

    return sortMode === "oldest"
      ? leftTime - rightTime
      : rightTime - leftTime;
  });
}

function buildGlobalSearchSourceRows(
  liveMessages: BundleOrganizerMessage[],
  includeTrash: boolean,
): BundleOrganizerSearchRow[] {
  const messagesById = new Map<string, BundleOrganizerSearchRow>();
  const sourceViews: BundleOrganizerView[] = includeTrash
    ? ["trash"]
    : ["demo", "promo", "priority", "shortlist"];

  sourceViews.forEach((view) => {
    getMessagesForView(view, liveMessages).forEach((message) => {
      if (!messagesById.has(message.id)) {
        messagesById.set(message.id, { message, sourceView: view });
      }
    });
  });

  return Array.from(messagesById.values()).sort(
    (first, second) =>
      (second.message.sortTimestamp ?? 0) - (first.message.sortTimestamp ?? 0),
  );
}

function doesMessageMatchSearch(message: BundleOrganizerMessage, searchQuery: string) {
  const normalizedSearchQuery = searchQuery.trim().toLowerCase();

  if (!normalizedSearchQuery) {
    return true;
  }

  const searchableFields = [
    message.sender,
    message.subject,
    message.snippet,
    message.sourceMailbox,
    message.priorityBadge,
    message.reason,
    message.internalClassification,
    message.category,
    message.manualCategory,
    resolveManualCategoryLabel(message),
    message.ui_signal,
    message.shortlisted ? "shortlisted" : null,
    message.manualPriority ? "manual priority" : null,
    message.manualPriority ? "priority" : null,
    message.trashed ? "trashed" : null,
    ...(Array.isArray(message.body) ? message.body : []),
  ];

  return searchableFields.some((value) =>
    (value ?? "").toLowerCase().includes(normalizedSearchQuery),
  );
}

function OrganizerIcon({ className = "" }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 72 72"
      className={className}
      fill="none"
    >
      <path
        d="M47.2 12.2C40.6 8.6 32.5 8.2 25.4 11.2C15.5 15.4 9.1 25 9.1 36C9.1 47 15.5 56.6 25.4 60.8C32.5 63.8 40.6 63.4 47.2 59.8"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="7.5"
      />
      <path d="M38.5 31.5V40.5" stroke="currentColor" strokeLinecap="round" strokeWidth="4.8" />
      <path d="M46 26.5V45.5" stroke="currentColor" strokeLinecap="round" strokeWidth="4.8" />
      <path d="M53.5 22.5V49.5" stroke="currentColor" strokeLinecap="round" strokeWidth="4.8" />
      <path d="M61 29.5V42.5" stroke="currentColor" strokeLinecap="round" strokeWidth="4.8" />
    </svg>
  );
}

function OrganizerNavIcon({ name }: { name: BundleOrganizerView }) {
  return (
    <svg
      aria-hidden="true"
      className="h-[18px] w-[18px] shrink-0"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.8"
      viewBox="0 0 24 24"
    >
      {name === "priority" ? (
        <>
          <path d="m13 2-2 8h7l-7 12 2-8H6l7-12Z" />
          <path d="M5 19h4" />
        </>
      ) : null}
      {name === "shortlist" ? (
        <path d="M6.5 4.75A2.25 2.25 0 0 1 8.75 2.5h6.5a2.25 2.25 0 0 1 2.25 2.25v16l-5.5-3.2-5.5 3.2v-16Z" />
      ) : null}
      {name === "demo" ? (
        <>
          <path d="M4 7.5A2.5 2.5 0 0 1 6.5 5h11A2.5 2.5 0 0 1 20 7.5v9A2.5 2.5 0 0 1 17.5 19h-11A2.5 2.5 0 0 1 4 16.5v-9Z" />
          <path d="M4 11h4.1a2.75 2.75 0 0 0 2.55 1.72h2.7A2.75 2.75 0 0 0 15.9 11H20" />
          <path d="M14.5 7.4v3.8" />
          <path d="M14.5 7.4 17 6.75" />
        </>
      ) : null}
      {name === "promo" ? (
        <>
          <path d="M4 13.5h3.25l8.25 4.25V6.25L7.25 10.5H4v3Z" />
          <path d="M7.25 13.5 8.5 19" />
          <path d="M18 9.1a3.25 3.25 0 0 1 0 5.8" />
          <path d="M20.25 7a6 6 0 0 1 0 10" />
        </>
      ) : null}
      {name === "trash" ? (
        <>
          <path d="M4 6.5h16" />
          <path d="M9.5 6.5v-2h5v2" />
          <path d="M7 6.5 8 20h8l1-13.5" />
          <path d="M10.5 10.5v5.5" />
          <path d="M13.5 10.5v5.5" />
        </>
      ) : null}
    </svg>
  );
}

function ContextMenuIcon({ name }: { name: BundleOrganizerContextMenuIconName }) {
  const paths: Record<BundleOrganizerContextMenuIconName, ReactNode> = {
    category: (
      <>
        <path d="M4 7h10" />
        <path d="M4 12h16" />
        <path d="M4 17h10" />
      </>
    ),
    forward: <path d="M15 7l5 5-5 5M20 12H8a4 4 0 0 0-4 4v1" />,
    mail: (
      <>
        <rect height="14" rx="2" width="18" x="3" y="5" />
        <path d="m3 7 9 6 9-6" />
      </>
    ),
    mailOpen: (
      <>
        <path d="M3 9 12 3l9 6" />
        <path d="M21 9v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9" />
        <path d="m3 9 9 6 9-6" />
      </>
    ),
    priority: (
      <>
        <path d="M5 21V4" />
        <path d="M5 4h11l-1.5 4L16 12H5" />
      </>
    ),
    priorityOff: (
      <>
        <path d="M5 21V4" />
        <path d="M5 4h11l-1.5 4L16 12H5" />
        <path d="M4 4 20 20" />
      </>
    ),
    restore: (
      <>
        <path d="M3 12a9 9 0 1 0 3-6.7" />
        <path d="M3 4v6h6" />
      </>
    ),
    rule: (
      <>
        <path d="M4 6h16" />
        <path d="M7 12h10" />
        <path d="M10 18h4" />
      </>
    ),
    shortlist: (
      <path d="M6.5 4.75A2.25 2.25 0 0 1 8.75 2.5h6.5a2.25 2.25 0 0 1 2.25 2.25v16l-5.5-3.2-5.5 3.2v-16Z" />
    ),
    shortlistOff: (
      <>
        <path d="M6.5 4.75A2.25 2.25 0 0 1 8.75 2.5h6.5a2.25 2.25 0 0 1 2.25 2.25v16l-5.5-3.2-5.5 3.2v-16Z" />
        <path d="M4 4 20 20" />
      </>
    ),
    smartView: (
      <>
        <circle cx="11" cy="11" r="7" />
        <path d="m16 16 4 4" />
        <path d="M8.5 11h5" />
        <path d="M11 8.5v5" />
      </>
    ),
    trash: (
      <>
        <path d="M3 6h18" />
        <path d="M8 6V4h8v2" />
        <path d="M19 6 18 20H6L5 6" />
        <path d="M10 11v5" />
        <path d="M14 11v5" />
      </>
    ),
  };

  return (
    <svg
      aria-hidden="true"
      className="h-3.5 w-3.5 shrink-0"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.9"
      viewBox="0 0 24 24"
    >
      {paths[name]}
    </svg>
  );
}

function isPromoReminderMessage(message: BundleOrganizerMessage) {
  return (
    formatOrganizerSignal(message.internalClassification) === "promo_reminder" ||
    formatOrganizerSignal(message.category) === "promo_reminder" ||
    formatOrganizerSignal(message.ui_signal) === "promo_reminder"
  );
}

function resolveManualCategoryLabel(message: BundleOrganizerMessage) {
  if (message.manualCategory === "demo") {
    return "Demo Inbox";
  }

  if (message.manualCategory === "promo") {
    return "Promo Inbox";
  }

  return null;
}

function buildDisabledMenuAction(
  action: Omit<BundleOrganizerContextMenuAction, "disabled" | "disabledReason">,
): BundleOrganizerContextMenuAction {
  return {
    ...action,
    disabled: true,
    disabledReason: bundleModeDisabledReason,
  };
}

function estimateContextMenuHeight(actionCount: number) {
  return contextMenuVerticalChrome + actionCount * contextMenuEstimatedActionHeight;
}

function resolveContextMenuPosition(
  anchorX: number,
  anchorY: number,
  menuHeight: number,
  menuWidth = contextMenuWidth,
) {
  if (typeof window === "undefined") {
    return { x: anchorX, y: anchorY };
  }

  const maxX = Math.max(
    contextMenuViewportPadding,
    window.innerWidth - menuWidth - contextMenuViewportPadding,
  );
  const x = Math.min(
    Math.max(contextMenuViewportPadding, anchorX),
    maxX,
  );
  const availableMenuHeight = Math.min(
    menuHeight,
    Math.max(0, window.innerHeight - contextMenuViewportPadding * 2),
  );
  const maxY = Math.max(
    contextMenuViewportPadding,
    window.innerHeight - availableMenuHeight - contextMenuViewportPadding,
  );
  const opensPastBottom =
    anchorY + menuHeight > window.innerHeight - contextMenuViewportPadding;
  const preferredY = opensPastBottom
    ? anchorY - menuHeight - contextMenuGap * 2
    : anchorY;
  const y = Math.min(
    Math.max(contextMenuViewportPadding, preferredY),
    maxY,
  );

  return { x, y };
}

function getContextMenuActions(
  message: BundleOrganizerMessage,
  sourceView: BundleOrganizerView,
  onMoveToCategory: (
    message: BundleOrganizerMessage,
    manualCategory: "demo" | "promo",
  ) => void,
  onToggleShortlist: (message: BundleOrganizerMessage) => void,
  onTogglePriority: (message: BundleOrganizerMessage) => void,
  onToggleTrash: (message: BundleOrganizerMessage) => void,
): BundleOrganizerContextMenuAction[] {
  const actions: BundleOrganizerContextMenuAction[] = [
    buildDisabledMenuAction({
      icon: "forward",
      label: "Forward",
    }),
    buildDisabledMenuAction({
      icon: message.unread ? "mailOpen" : "mail",
      label: message.unread ? "Mark as read" : "Mark as unread",
    }),
  ];

  if (sourceView === "trash" || message.trashed === true) {
    actions.push(
      {
        icon: "restore",
        label: "Restore",
        onSelect: () => onToggleTrash(message),
      },
      buildDisabledMenuAction({
        icon: "trash",
        label: "Delete permanently",
      }),
    );

    return actions;
  }

  const resolvedCategory = resolveOrganizerCategory(message);

  if (resolvedCategory !== "demo") {
    actions.push(
      {
        icon: "category",
        label: "Move to Demo",
        onSelect: () => onMoveToCategory(message, "demo"),
      },
    );
  }

  if (resolvedCategory !== "promo") {
    actions.push(
      {
        icon: "category",
        label: "Move to Promo",
        onSelect: () => onMoveToCategory(message, "promo"),
      },
    );
  }

  actions.push(
    {
      icon: message.shortlisted === true ? "shortlistOff" : "shortlist",
      label:
        message.shortlisted === true ? "Remove from Shortlist" : "Shortlist",
      onSelect: () => onToggleShortlist(message),
    },
    {
      icon: message.manualPriority === true ? "priorityOff" : "priority",
      label: message.manualPriority === true ? "Remove Priority" : "Mark as Priority",
      onSelect: () => onTogglePriority(message),
    },
  );

  if (sourceView === "promo" || (sourceView === "shortlist" && shouldShowInPromoInbox(message))) {
    if (isPromoReminderMessage(message)) {
      actions.push(
        buildDisabledMenuAction({
          icon: "rule",
          label: "Hide future reminders from this sender",
        }),
      );
    }
    actions.push(
      buildDisabledMenuAction({
        icon: "rule",
        label: "Hide future promos from this sender",
      }),
    );
  }

  actions.push(
    buildDisabledMenuAction({
      icon: "rule",
      label: "Always prioritize this sender",
    }),
    buildDisabledMenuAction({
      icon: "smartView",
      label: "Create Smart View for this sender",
    }),
    {
      icon: "trash",
      label: "Move to Trash",
      onSelect: () => onToggleTrash(message),
    },
  );

  return actions;
}

function MessagePills({ message }: { message: BundleOrganizerMessage }) {
  const manualCategoryLabel = resolveManualCategoryLabel(message);

  return (
    <>
      {message.sourceMailbox ? (
        <span className="max-w-full truncate rounded-full bg-[rgba(120,104,89,0.1)] px-2 py-0.5 text-[0.68rem] font-medium text-[rgba(64,56,48,0.62)] dark:bg-white/5 dark:text-[rgba(245,239,229,0.56)]">
          {message.sourceMailbox}
        </span>
      ) : null}
      {message.priorityBadge ? (
        <span className="rounded-full bg-[rgba(48,72,61,0.1)] px-2 py-0.5 text-[0.68rem] font-medium text-[rgba(48,72,61,0.86)] dark:bg-[rgba(143,179,159,0.14)] dark:text-[rgba(167,203,181,0.9)]">
          {message.priorityBadge}
        </span>
      ) : null}
      {message.manualPriority === true ? (
        <span className={manualPillClass}>Manual Priority</span>
      ) : null}
      {manualCategoryLabel ? (
        <span className={manualPillClass}>
          Manual: {manualCategoryLabel}
        </span>
      ) : null}
      {message.shortlisted ? (
        <span className={shortlistedPillClass}>Shortlisted</span>
      ) : null}
      {message.trashed ? (
        <span className={trashedPillClass}>Trashed</span>
      ) : null}
      {message.internalClassification ? (
        <span className="rounded-full bg-[rgba(120,104,89,0.1)] px-2 py-0.5 text-[0.68rem] font-medium text-[rgba(64,56,48,0.62)] dark:bg-white/5 dark:text-[rgba(245,239,229,0.56)]">
          {message.internalClassification.replace(/_/g, " ")}
        </span>
      ) : null}
    </>
  );
}

function FilterSelect({
  ariaLabel,
  children,
  onChange,
  value,
}: {
  ariaLabel: string;
  children: ReactNode;
  onChange: (value: string) => void;
  value: string;
}) {
  return (
    <label className="relative inline-flex">
      <span className="sr-only">{ariaLabel}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-9 max-w-[220px] appearance-none rounded-full border border-white/10 bg-white/5 px-3.5 pr-10 text-[0.74rem] font-medium uppercase tracking-[0.1em] text-[rgba(245,239,229,0.66)] outline-none transition-colors hover:border-[rgba(143,179,159,0.24)] hover:bg-[rgba(143,179,159,0.1)] hover:text-[rgba(198,228,209,0.84)] focus:border-[rgba(143,179,159,0.34)]"
      >
        {children}
      </select>
      <svg
        aria-hidden="true"
        className="pointer-events-none absolute right-3.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[rgba(245,239,229,0.52)]"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
        viewBox="0 0 24 24"
      >
        <path d="m6 9 6 6 6-6" />
      </svg>
    </label>
  );
}

function MessageDetail({
  message,
  onBack,
  onMoveToCategory,
  onTogglePriority,
  onToggleShortlist,
  onToggleTrash,
}: {
  message: BundleOrganizerMessage;
  onBack: () => void;
  onMoveToCategory: (
    message: BundleOrganizerMessage,
    manualCategory: "demo" | "promo",
  ) => void;
  onTogglePriority: (message: BundleOrganizerMessage) => void;
  onToggleShortlist: (message: BundleOrganizerMessage) => void;
  onToggleTrash: (message: BundleOrganizerMessage) => void;
}) {
  const activeReason = resolvePriorityReason(message);
  const bodyLines = message.body.length > 0 ? message.body : [message.snippet];
  const resolvedCategory = resolveOrganizerCategory(message);

  return (
    <article className="mt-4 rounded-[18px] border border-white/10 bg-white/[0.045] p-4 sm:p-5">
      <div className="flex flex-col gap-3 border-b border-white/10 pb-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={onBack}
              className="inline-flex h-8 items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 text-[0.7rem] font-medium uppercase tracking-[0.12em] text-[rgba(245,239,229,0.62)] transition-colors hover:border-[rgba(143,179,159,0.24)] hover:bg-[rgba(143,179,159,0.1)] hover:text-[rgba(198,228,209,0.84)]"
            >
              Back
            </button>
            {resolvedCategory !== "demo" ? (
              <button
                type="button"
                onClick={() => onMoveToCategory(message, "demo")}
                className="inline-flex h-8 items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-3 text-[0.68rem] font-medium uppercase tracking-[0.1em] text-[rgba(245,239,229,0.56)] transition-colors hover:border-[rgba(143,179,159,0.24)] hover:bg-[rgba(143,179,159,0.1)] hover:text-[rgba(198,228,209,0.88)]"
              >
                <ContextMenuIcon name="category" />
                <span>Move to Demo</span>
              </button>
            ) : null}
            {resolvedCategory !== "promo" ? (
              <button
                type="button"
                onClick={() => onMoveToCategory(message, "promo")}
                className="inline-flex h-8 items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-3 text-[0.68rem] font-medium uppercase tracking-[0.1em] text-[rgba(245,239,229,0.56)] transition-colors hover:border-[rgba(143,179,159,0.24)] hover:bg-[rgba(143,179,159,0.1)] hover:text-[rgba(198,228,209,0.88)]"
              >
                <ContextMenuIcon name="category" />
                <span>Move to Promo</span>
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => onToggleShortlist(message)}
              className={`inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-[0.68rem] font-medium uppercase tracking-[0.1em] transition-colors ${
                message.shortlisted
                  ? "border-[rgba(143,179,159,0.28)] bg-[rgba(143,179,159,0.12)] text-[rgba(198,228,209,0.92)] hover:bg-[rgba(143,179,159,0.16)]"
                  : "border-white/10 bg-white/5 text-[rgba(245,239,229,0.56)] hover:border-[rgba(143,179,159,0.24)] hover:bg-[rgba(143,179,159,0.1)] hover:text-[rgba(198,228,209,0.88)]"
              }`}
            >
              <ContextMenuIcon
                name={message.shortlisted ? "shortlistOff" : "shortlist"}
              />
              <span>
                {message.shortlisted ? "Remove from shortlist" : "Shortlist"}
              </span>
            </button>
            <button
              type="button"
              onClick={() => onTogglePriority(message)}
              className={`inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-[0.68rem] font-medium uppercase tracking-[0.1em] transition-colors ${
                message.manualPriority
                  ? "border-[rgba(143,179,159,0.28)] bg-[rgba(143,179,159,0.12)] text-[rgba(198,228,209,0.92)] hover:bg-[rgba(143,179,159,0.16)]"
                  : "border-white/10 bg-white/5 text-[rgba(245,239,229,0.56)] hover:border-[rgba(143,179,159,0.24)] hover:bg-[rgba(143,179,159,0.1)] hover:text-[rgba(198,228,209,0.88)]"
              }`}
            >
              <ContextMenuIcon
                name={message.manualPriority ? "priorityOff" : "priority"}
              />
              <span>
                {message.manualPriority ? "Remove Priority" : "Mark as Priority"}
              </span>
            </button>
            <button
              type="button"
              onClick={() => onToggleTrash(message)}
              className={`inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-[0.68rem] font-medium uppercase tracking-[0.1em] transition-colors ${
                message.trashed
                  ? "border-[rgba(143,179,159,0.28)] bg-[rgba(143,179,159,0.12)] text-[rgba(198,228,209,0.92)] hover:bg-[rgba(143,179,159,0.16)]"
                  : "border-white/10 bg-white/5 text-[rgba(245,239,229,0.56)] hover:border-[rgba(143,179,159,0.24)] hover:bg-[rgba(143,179,159,0.1)] hover:text-[rgba(198,228,209,0.88)]"
              }`}
            >
              <ContextMenuIcon name={message.trashed ? "restore" : "trash"} />
              <span>{message.trashed ? "Restore" : "Move to Trash"}</span>
            </button>
          </div>
          <p className="text-[0.74rem] font-medium uppercase tracking-[0.16em] text-[rgba(217,203,184,0.58)]">
            {message.sender}
          </p>
          <h3 className="mt-1.5 text-[1.3rem] font-semibold tracking-[-0.03em] text-[color:#f5efe5]">
            {message.subject}
          </h3>
          <div className="mt-3 flex flex-wrap gap-1.5">
            <MessagePills message={message} />
          </div>
        </div>
        <div className="shrink-0 text-left text-[0.78rem] font-medium text-[rgba(245,239,229,0.48)] sm:text-right">
          <div>{message.timestamp}</div>
          {message.sourceMailbox ? (
            <div className="mt-1 text-[rgba(245,239,229,0.58)]">{message.sourceMailbox}</div>
          ) : null}
        </div>
      </div>

      <div className="pt-4">
        {activeReason ? (
          <p className="mb-3 rounded-[14px] border border-[rgba(143,179,159,0.18)] bg-[rgba(143,179,159,0.1)] px-3.5 py-2.5 text-[0.82rem] font-medium leading-6 text-[rgba(167,203,181,0.9)]">
            {activeReason}
          </p>
        ) : null}
        <p className="text-[0.9rem] leading-6 text-[rgba(245,239,229,0.68)]">
          {message.snippet}
        </p>
        <div className="mt-4 space-y-3 rounded-[16px] border border-white/10 bg-[rgba(12,18,15,0.38)] p-4">
          {bodyLines.map((line, index) => (
            <p
              key={`${message.id}-body-${index}`}
              className="text-[0.88rem] leading-7 text-[rgba(245,239,229,0.72)]"
            >
              {line}
            </p>
          ))}
        </div>
      </div>
    </article>
  );
}

export function BundleOrganizerSurface({
  liveMessages = [],
  hasLiveWorkspaceData = false,
  connectedInboxCount = 0,
}: BundleOrganizerSurfaceProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [activeView, setActiveView] = useState<BundleOrganizerView>("priority");
  const [selectedMessage, setSelectedMessage] = useState<BundleOrganizerMessage | null>(null);
  const [contextMenu, setContextMenu] =
    useState<BundleOrganizerContextMenuState | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [demoSourceFilterId, setDemoSourceFilterId] = useState(allSourceFilterId);
  const [promoSourceFilterId, setPromoSourceFilterId] = useState(allSourceFilterId);
  const [demoStatusFilter, setDemoStatusFilter] =
    useState<BundleOrganizerDemoStatusFilter>("all");
  const [promoFilter, setPromoFilter] =
    useState<BundleOrganizerPromoFilter>("new");
  const [promoStatusFilter, setPromoStatusFilter] =
    useState<BundleOrganizerPromoStatusFilter>("all");
  const [demoSort, setDemoSort] = useState<BundleOrganizerDateSort>("newest");
  const [promoSort, setPromoSort] = useState<BundleOrganizerDateSort>("newest");
  const [workflowState, setWorkflowState] =
    useState<BundleOrganizerWorkflowState>(readBundleOrganizerWorkflowState);
  const baseWorkspaceMessages = useMemo(
    () => normalizeWorkspaceMessages(liveMessages),
    [liveMessages],
  );
  const workspaceMessages = useMemo(
    () => applyBundleWorkflowState(baseWorkspaceMessages, workflowState),
    [baseWorkspaceMessages, workflowState],
  );
  const counts = useMemo(() => getCounts(workspaceMessages), [workspaceMessages]);
  const demoAllMessages = useMemo(
    () => getMessagesForView("demo", workspaceMessages),
    [workspaceMessages],
  );
  const promoAllMessages = useMemo(
    () => getMessagesForView("promo", workspaceMessages),
    [workspaceMessages],
  );
  const demoSourceFilterOptions = useMemo(
    () => buildSourceFilterOptions(demoAllMessages),
    [demoAllMessages],
  );
  const promoSourceFilterOptions = useMemo(
    () => buildSourceFilterOptions(promoAllMessages),
    [promoAllMessages],
  );
  const sourceFilteredDemoMessages = useMemo(
    () => filterMessagesBySource(demoAllMessages, demoSourceFilterId),
    [demoAllMessages, demoSourceFilterId],
  );
  const sortedVisibleDemoMessages = useMemo(
    () =>
      sortMessagesByDate(
        filterMessagesByDemoStatus(sourceFilteredDemoMessages, demoStatusFilter),
        demoSort,
      ),
    [demoSort, demoStatusFilter, sourceFilteredDemoMessages],
  );
  const sourceFilteredPromoMessages = useMemo(
    () => filterMessagesBySource(promoAllMessages, promoSourceFilterId),
    [promoAllMessages, promoSourceFilterId],
  );
  const promoReminderMessages = useMemo(
    () => sourceFilteredPromoMessages.filter(isPromoReminderMessage),
    [sourceFilteredPromoMessages],
  );
  const promoNewMessages = useMemo(
    () =>
      sourceFilteredPromoMessages.filter(
        (message) => !isPromoReminderMessage(message),
      ),
    [sourceFilteredPromoMessages],
  );
  const visiblePromoMessages = useMemo(
    () =>
      promoFilter === "reminders"
        ? promoReminderMessages
        : promoFilter === "all"
        ? sourceFilteredPromoMessages
        : promoNewMessages,
    [
      promoFilter,
      promoNewMessages,
      promoReminderMessages,
      sourceFilteredPromoMessages,
    ],
  );
  const sortedVisiblePromoMessages = useMemo(
    () =>
      sortMessagesByDate(
        filterMessagesByPromoStatus(visiblePromoMessages, promoStatusFilter),
        promoSort,
      ),
    [promoSort, promoStatusFilter, visiblePromoMessages],
  );
  const promoFilterOptions: Array<{
    count: number;
    id: BundleOrganizerPromoFilter;
    label: string;
  }> = [
    { id: "new", label: "Promos", count: promoNewMessages.length },
    { id: "reminders", label: "Reminders", count: promoReminderMessages.length },
    { id: "all", label: "All", count: sourceFilteredPromoMessages.length },
  ];
  const rawActiveMessages = useMemo(
    () =>
      activeView === "demo"
        ? sortedVisibleDemoMessages
        : activeView === "promo"
        ? sortedVisiblePromoMessages
        : getMessagesForView(activeView, workspaceMessages),
    [
      activeView,
      sortedVisibleDemoMessages,
      sortedVisiblePromoMessages,
      workspaceMessages,
    ],
  );
  const globalSearchSourceRows = useMemo(
    () => buildGlobalSearchSourceRows(workspaceMessages, activeView === "trash"),
    [activeView, workspaceMessages],
  );
  const visibleRows = useMemo(
    () =>
      searchQuery.trim()
        ? globalSearchSourceRows.filter(({ message }) =>
            doesMessageMatchSearch(message, searchQuery),
          )
        : rawActiveMessages.map((message) => ({
            message,
            sourceView: activeView,
          })),
    [activeView, globalSearchSourceRows, rawActiveMessages, searchQuery],
  );
  const isSearchActive = searchQuery.trim().length > 0;
  const activeCopy = isSearchActive
      ? {
        title: "Search results",
        eyebrow: "Global Organizer search",
        description:
          activeView === "trash"
            ? "Matching messages in embedded Organizer Trash."
            : "Matching messages across embedded Organizer views.",
        emptyTitle: "No messages match your search.",
        emptyDescription: "Clear the search to return to the current view.",
      }
    : viewCopy[activeView];
  const hasOrganizerData = hasLiveWorkspaceData || workspaceMessages.length > 0;
  const contextMenuMessage = contextMenu
    ? visibleRows.find(
        ({ message, sourceView }) =>
          message.id === contextMenu.messageId &&
          sourceView === contextMenu.sourceView,
      ) ?? null
    : null;
  const contextMenuActions = contextMenuMessage
    ? getContextMenuActions(
        contextMenuMessage.message,
        contextMenuMessage.sourceView,
        moveMessageToCategory,
        toggleMessageShortlist,
        toggleMessagePriority,
        toggleMessageTrash,
      )
    : [];
  const isViewFilterActive =
    !isSearchActive &&
    ((activeView === "demo" &&
      (demoSourceFilterId !== allSourceFilterId ||
        demoStatusFilter !== "all" ||
        demoSort !== "newest")) ||
      (activeView === "promo" &&
        (promoSourceFilterId !== allSourceFilterId ||
          promoStatusFilter !== "all" ||
          promoFilter !== "new" ||
          promoSort !== "newest")));
  const shouldShowControlBar =
    !selectedMessage &&
    !isSearchActive &&
    (activeView === "demo" || activeView === "promo");

  const selectView = (view: BundleOrganizerView) => {
    setActiveView(view);
    setSelectedMessage(null);
    setContextMenu(null);
  };

  function moveMessageToCategory(
    message: BundleOrganizerMessage,
    manualCategory: "demo" | "promo",
  ) {
    const identityKey = getWorkflowIdentityKey(message);
    const manualCategoryAt = new Date().toISOString();

    setWorkflowState((currentState) => {
      const nextState = {
        ...currentState,
        [identityKey]: {
          ...currentState[identityKey],
          manualCategory,
          manualCategoryAt,
        },
      };

      writeBundleOrganizerWorkflowState(nextState);
      return nextState;
    });
    setContextMenu(null);
    setSelectedMessage((currentMessage) =>
      currentMessage && getWorkflowIdentityKey(currentMessage) === identityKey
        ? {
            ...currentMessage,
            manualCategory,
            manualCategoryAt,
          }
        : currentMessage,
    );
  }

  function toggleMessageShortlist(message: BundleOrganizerMessage) {
    const identityKey = getWorkflowIdentityKey(message);
    const nextShortlisted = message.shortlisted !== true;
    const shortlistedAt = nextShortlisted
      ? new Date().toISOString()
      : undefined;

    setWorkflowState((currentState) => {
      const nextState = {
        ...currentState,
        [identityKey]: {
          ...currentState[identityKey],
          shortlisted: nextShortlisted,
          shortlistedAt,
        },
      };

      if (!nextShortlisted) {
        delete nextState[identityKey].shortlistedAt;
      }

      writeBundleOrganizerWorkflowState(nextState);
      return nextState;
    });
    setContextMenu(null);
    setSelectedMessage((currentMessage) =>
      currentMessage && getWorkflowIdentityKey(currentMessage) === identityKey
        ? {
            ...currentMessage,
            shortlisted: nextShortlisted,
            shortlistedAt,
          }
        : currentMessage,
    );
  }

  function toggleMessagePriority(message: BundleOrganizerMessage) {
    const identityKey = getWorkflowIdentityKey(message);
    const nextManualPriority = message.manualPriority !== true;
    const manualPriorityAt = nextManualPriority
      ? new Date().toISOString()
      : undefined;

    setWorkflowState((currentState) => {
      const nextState = {
        ...currentState,
        [identityKey]: {
          ...currentState[identityKey],
          manualPriority: nextManualPriority,
          manualPriorityAt,
        },
      };

      if (!nextManualPriority) {
        delete nextState[identityKey].manualPriorityAt;
      }

      writeBundleOrganizerWorkflowState(nextState);
      return nextState;
    });
    setContextMenu(null);
    setSelectedMessage((currentMessage) =>
      currentMessage && getWorkflowIdentityKey(currentMessage) === identityKey
        ? {
            ...currentMessage,
            manualPriority: nextManualPriority,
            manualPriorityAt,
          }
        : currentMessage,
    );
  }

  function toggleMessageTrash(message: BundleOrganizerMessage) {
    const identityKey = getWorkflowIdentityKey(message);
    const nextTrashed = message.trashed !== true;
    const trashedAt = nextTrashed ? new Date().toISOString() : undefined;

    setWorkflowState((currentState) => {
      const nextState = {
        ...currentState,
        [identityKey]: {
          ...currentState[identityKey],
          trashed: nextTrashed,
          trashedAt,
        },
      };

      if (!nextTrashed) {
        delete nextState[identityKey].trashedAt;
      }

      writeBundleOrganizerWorkflowState(nextState);
      return nextState;
    });
    setContextMenu(null);
    setSelectedMessage((currentMessage) =>
      currentMessage && getWorkflowIdentityKey(currentMessage) === identityKey
        ? {
            ...currentMessage,
            trashed: nextTrashed,
            trashedAt,
          }
        : currentMessage,
    );
  }

  const openContextMenu = (
    message: BundleOrganizerMessage,
    sourceView: BundleOrganizerView,
    x: number,
    y: number,
  ) => {
    const estimatedActionCount = getContextMenuActions(
      message,
      sourceView,
      moveMessageToCategory,
      toggleMessageShortlist,
      toggleMessagePriority,
      toggleMessageTrash,
    ).length;
    const position = resolveContextMenuPosition(
      x,
      y,
      estimateContextMenuHeight(estimatedActionCount),
    );

    setContextMenu({
      anchorX: x,
      anchorY: y,
      messageId: message.id,
      sourceView,
      x: position.x,
      y: position.y,
    });
  };

  useEffect(() => {
    setContextMenu(null);
  }, [activeView, isSearchActive]);

  useLayoutEffect(() => {
    if (!contextMenu || !menuRef.current) {
      return;
    }

    const rect = menuRef.current.getBoundingClientRect();
    const position = resolveContextMenuPosition(
      contextMenu.anchorX,
      contextMenu.anchorY,
      rect.height,
      rect.width,
    );

    if (position.x === contextMenu.x && position.y === contextMenu.y) {
      return;
    }

    setContextMenu((currentContextMenu) =>
      currentContextMenu
        ? {
            ...currentContextMenu,
            x: position.x,
            y: position.y,
          }
        : currentContextMenu,
    );
  }, [contextMenu, contextMenuActions.length]);

  useEffect(() => {
    if (
      demoSourceFilterId !== allSourceFilterId &&
      !demoSourceFilterOptions.some((option) => option.id === demoSourceFilterId)
    ) {
      setDemoSourceFilterId(allSourceFilterId);
    }
  }, [demoSourceFilterId, demoSourceFilterOptions]);

  useEffect(() => {
    if (
      promoSourceFilterId !== allSourceFilterId &&
      !promoSourceFilterOptions.some((option) => option.id === promoSourceFilterId)
    ) {
      setPromoSourceFilterId(allSourceFilterId);
    }
  }, [promoSourceFilterId, promoSourceFilterOptions]);

  useEffect(() => {
    if (!contextMenu) {
      return;
    }

    if (!contextMenuMessage) {
      setContextMenu(null);
    }
  }, [contextMenu, contextMenuMessage]);

  useEffect(() => {
    if (!contextMenu) {
      return;
    }

    const closeOnOutsideMouseDown = (event: MouseEvent) => {
      if (
        event.target instanceof Node &&
        menuRef.current?.contains(event.target)
      ) {
        return;
      }
      if (
        event.target instanceof Element &&
        event.target.closest("[data-organizer-context-menu-surface='true']")
      ) {
        return;
      }

      setContextMenu(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setContextMenu(null);
      }
    };

    document.addEventListener("mousedown", closeOnOutsideMouseDown);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutsideMouseDown);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [contextMenu]);

  return (
    <div className="min-h-0 flex-1 overflow-hidden">
      <section className="dark flex h-full min-h-0 flex-col overflow-hidden rounded-[18px] bg-[linear-gradient(180deg,#111a16_0%,#19241f_100%)] px-3 py-3 text-[color:#f5efe5] sm:px-4 lg:px-5">
        <header className="border-b border-white/10 pb-4">
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                <div className="flex items-center gap-2.5">
                  <OrganizerIcon className="h-8 w-8 shrink-0 text-[color:#8fb39f]" />
                  <p className="text-[0.72rem] font-medium uppercase tracking-[0.18em] text-[rgba(217,203,184,0.68)]">
                    Cuevion
                  </p>
                </div>
                <h1 className="mt-2.5 text-[1.7rem] font-semibold tracking-[-0.03em] text-[color:#f5efe5] sm:text-[2rem]">
                  Demo &amp; Promo Organizer
                </h1>
              </div>
            </div>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="max-w-[520px] text-[0.96rem] leading-7 text-[rgba(245,239,229,0.66)]">
                A dedicated music inbox for demos, promos, and active follow-ups.
              </p>
              <div className="relative w-full sm:max-w-[320px]">
                <span className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-[rgba(245,239,229,0.42)]">
                  <svg
                    aria-hidden="true"
                    className="h-4 w-4"
                    fill="none"
                    stroke="currentColor"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth="2"
                    viewBox="0 0 24 24"
                  >
                    <path d="m21 21-4.34-4.34" />
                    <circle cx="11" cy="11" r="8" />
                  </svg>
                </span>
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(event) => {
                    setSearchQuery(event.target.value);
                    setSelectedMessage(null);
                  }}
                  placeholder="Search messages..."
                  className="h-10 w-full rounded-full border border-white/10 bg-white/5 pl-10 pr-10 text-[0.86rem] font-medium text-[rgba(245,239,229,0.84)] outline-none transition-colors placeholder:text-[rgba(245,239,229,0.38)] hover:border-[rgba(143,179,159,0.24)] hover:bg-white/8 focus:border-[rgba(143,179,159,0.34)] focus:bg-white/10 focus:ring-2 focus:ring-[rgba(143,179,159,0.14)]"
                />
                {searchQuery ? (
                  <button
                    type="button"
                    onClick={() => {
                      setSearchQuery("");
                      setSelectedMessage(null);
                    }}
                    className="absolute right-2 top-1/2 inline-flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-full text-[0.78rem] font-semibold leading-none text-[rgba(245,239,229,0.46)] transition-colors hover:bg-[rgba(143,179,159,0.12)] hover:text-[rgba(198,228,209,0.9)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(143,179,159,0.2)]"
                    aria-label="Clear message search"
                  >
                    X
                  </button>
                ) : null}
              </div>
            </div>
          </div>
        </header>

        <div className="mt-3 grid min-h-0 flex-1 gap-4 overflow-hidden lg:grid-cols-[210px_minmax(0,1fr)] xl:grid-cols-[220px_minmax(0,1fr)]">
          <aside className="flex min-w-0 flex-col gap-3 overflow-y-auto pb-2.5 lg:pb-0">
            <section className="w-full rounded-[15px] border border-white/10 bg-white/5 px-3 py-2.5 shadow-[0_10px_24px_rgba(0,0,0,0.16)]">
              <p className="text-[0.66rem] font-medium uppercase tracking-[0.15em] text-[rgba(217,203,184,0.55)]">
                Connected Inboxes
              </p>
              <div className="mt-1.5 flex items-center justify-between gap-2">
                <span className="text-[1.45rem] font-semibold leading-none tracking-[-0.04em] text-[color:#f5efe5]">
                  {connectedInboxCount}
                </span>
                <span className="inline-flex h-7 shrink-0 items-center justify-center rounded-full border border-white/10 bg-white/5 px-2.5 text-[0.64rem] font-medium uppercase tracking-[0.1em] text-[rgba(245,239,229,0.5)]">
                  Live
                </span>
              </div>
              <p className="mt-1 truncate text-[0.68rem] font-medium leading-4 text-[rgba(167,203,181,0.72)]">
                Workspace messages only
              </p>
            </section>

            <nav
              aria-label="Organizer sections"
              className="flex gap-2 overflow-x-auto border-b border-white/10 pb-3 lg:block lg:overflow-visible lg:border-b-0 lg:border-r lg:bg-transparent lg:pb-0 lg:pr-4 xl:pr-5"
            >
              {navItems.map((item) => {
                const isActive = item.id === activeView;
                const count = counts[item.id] ?? 0;

                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => selectView(item.id)}
                    className={`flex h-10 shrink-0 items-center justify-between gap-3 rounded-full px-3.5 text-left text-[0.82rem] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(143,179,159,0.28)] lg:mb-2 lg:w-full ${
                      isActive
                        ? "bg-[color:#8fb39f] text-[color:#14201a]"
                        : "text-[rgba(245,239,229,0.72)] hover:bg-white/5"
                    }`}
                    aria-current={isActive ? "page" : undefined}
                  >
                    <span className="flex min-w-0 items-center gap-2.5">
                      <OrganizerNavIcon name={item.icon} />
                      <span className="truncate">{item.label}</span>
                    </span>
                    {item.id !== "trash" && count > 0 ? (
                      <span
                        className={`rounded-full px-2 py-0.5 text-[0.72rem] ${
                          isActive
                            ? "bg-[rgba(20,32,26,0.18)] text-[rgba(20,32,26,0.72)]"
                            : "bg-white/5 text-[rgba(245,239,229,0.52)]"
                        }`}
                      >
                        {count}
                      </span>
                    ) : null}
                  </button>
                );
              })}
            </nav>
          </aside>

          <main className="min-h-0 min-w-0 overflow-y-auto pr-1">
            <section className="rounded-[22px] border border-white/10 bg-[rgba(25,34,30,0.82)] p-4 shadow-[0_18px_48px_rgba(0,0,0,0.26)] sm:p-5 xl:p-6">
              <div className="flex items-center gap-2.5">
                <p className="text-[0.72rem] font-semibold uppercase tracking-[0.16em] text-[rgba(217,203,184,0.58)]">
                  {activeCopy.eyebrow}
                </p>
              </div>
              <div className="mt-1 border-b border-white/10 pb-4">
                <h2 className="text-[1.45rem] font-semibold tracking-[-0.03em] text-[color:#f5efe5]">
                  {activeCopy.title}
                </h2>
                <p className="mt-1.5 max-w-[660px] text-[0.86rem] leading-6 text-[rgba(245,239,229,0.58)]">
                  {activeCopy.description}
                </p>
              </div>

              {shouldShowControlBar ? (
                <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    {activeView === "promo"
                      ? promoFilterOptions.map((option) => {
                          const isSelected = option.id === promoFilter;

                          return (
                            <button
                              key={option.id}
                              type="button"
                              onClick={() => {
                                setPromoFilter(option.id);
                                setSelectedMessage(null);
                                setContextMenu(null);
                              }}
                              className={`inline-flex h-9 items-center justify-center gap-2 rounded-full border px-3.5 text-[0.74rem] font-medium uppercase tracking-[0.1em] transition-colors ${
                                isSelected
                                  ? "border-[rgba(143,179,159,0.34)] bg-[rgba(143,179,159,0.16)] text-[rgba(198,228,209,0.94)]"
                                  : "border-white/10 bg-white/5 text-[rgba(245,239,229,0.62)] hover:border-[rgba(143,179,159,0.24)] hover:bg-[rgba(143,179,159,0.1)] hover:text-[rgba(198,228,209,0.84)]"
                              }`}
                            >
                              <span>{option.label}</span>
                              <span
                                className={`rounded-full px-2 py-0.5 text-[0.68rem] ${
                                  isSelected
                                    ? "bg-[rgba(143,179,159,0.16)] text-[rgba(198,228,209,0.92)]"
                                    : "bg-white/5 text-[rgba(245,239,229,0.5)]"
                                }`}
                              >
                                {option.count}
                              </span>
                            </button>
                          );
                        })
                      : null}
                    <FilterSelect
                      ariaLabel="Filter by source inbox"
                      value={
                        activeView === "demo"
                          ? demoSourceFilterId
                          : promoSourceFilterId
                      }
                      onChange={(value) => {
                        if (activeView === "demo") {
                          setDemoSourceFilterId(value);
                        } else {
                          setPromoSourceFilterId(value);
                        }
                        setSelectedMessage(null);
                        setContextMenu(null);
                      }}
                    >
                      <option value={allSourceFilterId}>All inboxes</option>
                      {(activeView === "demo"
                        ? demoSourceFilterOptions
                        : promoSourceFilterOptions
                      ).map((option) => (
                        <option key={option.id} value={option.id}>
                          {option.label} ({option.count})
                        </option>
                      ))}
                    </FilterSelect>
                  </div>

                  {activeView === "demo" ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <FilterSelect
                        ariaLabel="Filter by demo status"
                        value={demoStatusFilter}
                        onChange={(value) => {
                          setDemoStatusFilter(value as BundleOrganizerDemoStatusFilter);
                          setSelectedMessage(null);
                          setContextMenu(null);
                        }}
                      >
                        {demoStatusFilterOptions.map((option) => (
                          <option key={option.id} value={option.id}>
                            {option.label}
                          </option>
                        ))}
                      </FilterSelect>
                      <FilterSelect
                        ariaLabel="Sort demo messages"
                        value={demoSort}
                        onChange={(value) => {
                          setDemoSort(value as BundleOrganizerDateSort);
                          setSelectedMessage(null);
                          setContextMenu(null);
                        }}
                      >
                        {dateSortOptions.map((option) => (
                          <option key={option.id} value={option.id}>
                            {option.label}
                          </option>
                        ))}
                      </FilterSelect>
                    </div>
                  ) : null}

                  {activeView === "promo" ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <FilterSelect
                        ariaLabel="Filter promo status"
                        value={promoStatusFilter}
                        onChange={(value) => {
                          setPromoStatusFilter(value as BundleOrganizerPromoStatusFilter);
                          setSelectedMessage(null);
                          setContextMenu(null);
                        }}
                      >
                        {promoStatusFilterOptions.map((option) => (
                          <option key={option.id} value={option.id}>
                            {option.label}
                          </option>
                        ))}
                      </FilterSelect>
                      <FilterSelect
                        ariaLabel="Sort promo messages"
                        value={promoSort}
                        onChange={(value) => {
                          setPromoSort(value as BundleOrganizerDateSort);
                          setSelectedMessage(null);
                          setContextMenu(null);
                        }}
                      >
                        {dateSortOptions.map((option) => (
                          <option key={option.id} value={option.id}>
                            {option.label}
                          </option>
                        ))}
                      </FilterSelect>
                    </div>
                  ) : null}
                </div>
              ) : null}

              {selectedMessage ? (
                <MessageDetail
                  message={selectedMessage}
                  onBack={() => setSelectedMessage(null)}
                  onMoveToCategory={moveMessageToCategory}
                  onTogglePriority={toggleMessagePriority}
                  onToggleShortlist={toggleMessageShortlist}
                  onToggleTrash={toggleMessageTrash}
                />
              ) : visibleRows.length === 0 ? (
                <div className="mt-4 rounded-[18px] border border-white/10 bg-white/5 px-5 py-10 text-center">
                  <h3 className="text-[1rem] font-semibold tracking-[-0.02em] text-[color:#f5efe5]">
                    {isSearchActive
                      ? "No matching messages."
                      : isViewFilterActive
                      ? "No messages match this filter."
                      : hasOrganizerData
                      ? activeCopy.emptyTitle
                      : "No messages loaded."}
                  </h3>
                  <p className="mx-auto mt-2 max-w-[460px] text-[0.86rem] leading-6 text-[rgba(245,239,229,0.58)]">
                    {isSearchActive
                      ? "Try a different sender, subject, snippet, or source mailbox."
                      : isViewFilterActive
                      ? "Try another inbox, status, or filter."
                      : hasOrganizerData
                      ? activeCopy.emptyDescription
                      : "Connected inbox messages will appear here after sync."}
                  </p>
                </div>
              ) : (
                <ul className="mt-4 overflow-hidden rounded-[16px] border border-white/10 bg-white/5">
                  {visibleRows.map(({ message, sourceView }) => (
                    <li
                      key={`${isSearchActive ? "search" : activeView}-${sourceView}-${message.id}`}
                      className="border-b border-white/10 last:border-b-0"
                    >
                      <div
                        className="relative border-l-2 border-transparent transition-[background-color,border-color,box-shadow] hover:bg-white/5"
                      >
                        <button
                          type="button"
                          onContextMenu={(event) => {
                            event.preventDefault();
                            event.stopPropagation();
                            openContextMenu(
                              message,
                              sourceView,
                              event.clientX,
                              event.clientY,
                            );
                          }}
                          onClick={() => {
                            setContextMenu(null);
                            setSelectedMessage(message);
                          }}
                          className="grid w-full gap-3 px-3 py-3.5 pr-12 text-left transition-[background-color,border-color,box-shadow] sm:grid-cols-[minmax(150px,0.55fr)_minmax(0,2.6fr)_minmax(72px,auto)] sm:px-4 sm:pr-14 lg:grid-cols-[minmax(170px,0.46fr)_minmax(0,3fr)_minmax(82px,auto)] xl:px-5 xl:pr-14"
                        >
                          <div className="grid min-w-0 grid-cols-[0.5rem_minmax(0,1fr)] items-start gap-2">
                            <span
                              aria-hidden="true"
                              className={`mt-[0.36rem] h-2 w-2 rounded-full ${
                                message.unread
                                  ? "bg-[#a78bfa] shadow-[0_0_0_2px_rgba(167,139,250,0.2)]"
                                  : "bg-transparent"
                              }`}
                            />
                            <div className="min-w-0">
                              <div className="truncate text-[0.92rem] font-semibold tracking-[-0.01em] text-[color:#f5efe5]">
                                {message.sender}
                              </div>
                              <div className="mt-1 flex flex-wrap gap-1.5">
                                {isSearchActive ? (
                                  <span className="rounded-full bg-[rgba(143,179,159,0.14)] px-2 py-0.5 text-[0.68rem] font-medium text-[rgba(167,203,181,0.9)]">
                                    {getViewLabel(sourceView)}
                                  </span>
                                ) : null}
                                <MessagePills message={message} />
                              </div>
                            </div>
                          </div>
                          <div className="min-w-0">
                            <div className="truncate text-[0.95rem] font-medium tracking-[-0.01em] text-[rgba(245,239,229,0.88)]">
                              {message.subject}
                            </div>
                            <p className="mt-1 line-clamp-2 text-[0.84rem] leading-5 text-[rgba(245,239,229,0.6)]">
                              {message.snippet}
                            </p>
                            {sourceView === "priority" && resolvePriorityReason(message) ? (
                              <p className="mt-2 text-[0.76rem] font-medium uppercase tracking-[0.12em] text-[rgba(143,179,159,0.78)]">
                                {resolvePriorityReason(message)}
                              </p>
                            ) : null}
                          </div>
                          <div className="text-[0.78rem] font-medium text-[rgba(245,239,229,0.45)] sm:pt-0.5 sm:text-right">
                            {message.timestamp}
                          </div>
                        </button>
                        <button
                          type="button"
                          aria-expanded={
                            contextMenu?.messageId === message.id &&
                            contextMenu.sourceView === sourceView
                          }
                          aria-haspopup="menu"
                          aria-label="Message actions"
                          data-organizer-context-menu-surface="true"
                          data-organizer-list-control="true"
                          onMouseDown={(event) => {
                            event.preventDefault();
                            event.stopPropagation();
                          }}
                          onClick={(event) => {
                            event.preventDefault();
                            event.stopPropagation();
                            const rect = event.currentTarget.getBoundingClientRect();
                            openContextMenu(
                              message,
                              sourceView,
                              rect.right - contextMenuWidth,
                              rect.bottom + contextMenuGap,
                            );
                          }}
                          className={`absolute right-3 top-3 inline-flex h-8 w-8 items-center justify-center rounded-full border text-[1rem] leading-none transition-colors sm:right-4 ${
                            contextMenu?.messageId === message.id &&
                            contextMenu.sourceView === sourceView
                              ? "border-[rgba(143,179,159,0.28)] bg-[rgba(143,179,159,0.12)] text-[rgba(198,228,209,0.9)]"
                              : "border-white/10 bg-white/5 text-[rgba(245,239,229,0.48)] hover:border-[rgba(143,179,159,0.24)] hover:bg-[rgba(143,179,159,0.1)] hover:text-[rgba(198,228,209,0.86)]"
                          }`}
                        >
                          <span aria-hidden="true">...</span>
                        </button>
                        {activeView === "shortlist" || activeView === "trash" ? (
                          <div className="flex justify-end px-3 pb-3 sm:px-4 xl:px-5">
                            <button
                              type="button"
                              onClick={(event) => {
                                event.preventDefault();
                                event.stopPropagation();
                                if (activeView === "trash") {
                                  toggleMessageTrash(message);
                                  return;
                                }
                                toggleMessageShortlist(message);
                              }}
                              data-organizer-list-control="true"
                              className="inline-flex h-8 items-center justify-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-3 text-[0.68rem] font-medium uppercase tracking-[0.1em] text-[rgba(245,239,229,0.56)] transition-colors hover:border-[rgba(143,179,159,0.24)] hover:bg-[rgba(143,179,159,0.1)] hover:text-[rgba(198,228,209,0.88)]"
                            >
                              <ContextMenuIcon
                                name={
                                  activeView === "trash"
                                    ? "restore"
                                    : "shortlistOff"
                                }
                              />
                              <span>
                                {activeView === "trash"
                                  ? "Restore"
                                  : "Remove from shortlist"}
                              </span>
                            </button>
                          </div>
                        ) : null}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              {contextMenu && contextMenuActions.length > 0 ? (
                <div
                  ref={menuRef}
                  role="menu"
                  data-organizer-context-menu-surface="true"
                  onContextMenu={(event) => event.preventDefault()}
                  onMouseDown={(event) => event.stopPropagation()}
                  style={{
                    left: contextMenu.x,
                    maxHeight: `calc(100vh - ${contextMenuViewportPadding * 2}px)`,
                    top: contextMenu.y,
                  }}
                  className="fixed z-50 min-w-[190px] overflow-y-auto rounded-[14px] border border-white/10 bg-[rgba(25,34,30,0.96)] p-1.5 shadow-[0_18px_40px_rgba(0,0,0,0.34)] backdrop-blur-sm"
                >
                  {contextMenuActions.map((action) => (
                    <button
                      key={action.label}
                      type="button"
                      role="menuitem"
                      disabled={action.disabled}
                      title={action.disabledReason}
                      onClick={() => {
                        if (action.disabled) {
                          return;
                        }
                        setContextMenu(null);
                        action.onSelect?.();
                      }}
                      className={`flex w-full items-center gap-2.5 rounded-[10px] px-3 py-2 text-left text-[0.82rem] font-medium transition-colors ${
                        action.disabled
                          ? "cursor-not-allowed text-[rgba(245,239,229,0.34)]"
                          : "text-[rgba(245,239,229,0.72)] hover:bg-[rgba(143,179,159,0.12)] hover:text-[rgba(198,228,209,0.94)]"
                      }`}
                    >
                      {action.icon ? <ContextMenuIcon name={action.icon} /> : null}
                      <span>{action.label}</span>
                    </button>
                  ))}
                </div>
              ) : null}
            </section>
          </main>
        </div>
      </section>
    </div>
  );
}
