import { useMemo, useState } from "react";

type BundleOrganizerView =
  | "priority"
  | "shortlist"
  | "demo"
  | "promo"
  | "sent"
  | "trash"
  | "settings";

type BundleOrganizerIconName =
  | "priority"
  | "shortlist"
  | "demo"
  | "promo"
  | "sent"
  | "trash"
  | "settings";

type BundleOrganizerMessage = {
  id: string;
  kind: "demo" | "promo" | "sent";
  source: "workspace";
  sender: string;
  subject: string;
  snippet: string;
  body: string[];
  timestamp: string;
  sourceMailbox: string;
  manualCategory?: "demo" | "promo";
  internalClassification?: BundleOrganizerInternalClassification;
  category?: string;
  signal?: string;
  uiSignal?: string;
  unread?: boolean;
  shortlisted?: boolean;
  manualPriority?: boolean;
  active_work_status?: BundleOrganizerActiveWorkStatus;
  v7_final_priority?: string;
  priorityBadge?: string;
  status?: "replied" | "declined" | "interested" | "sent" | "trashed";
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
  internalClassification?: BundleOrganizerInternalClassification;
  category?: string;
  signal?: string;
  uiSignal?: string;
  unread?: boolean;
  manualPriority?: boolean;
  active_work_status?: BundleOrganizerActiveWorkStatus;
  v7_final_priority?: string;
  priorityBadge?: string;
  reason?: string;
  identityKey?: string;
  sortTimestamp?: number;
};

type BundleOrganizerVisibleCategory =
  | "demo"
  | "high_priority_demo"
  | "promo"
  | "promo_reminder";

type BundleOrganizerActiveWorkStatus =
  | "none"
  | "review"
  | "active"
  | "waiting"
  | "needs_reply"
  | "follow_up"
  | "closed";

type BundleOrganizerPromoFilter = "all" | "reminders" | "unread";
type BundleOrganizerSortOrder = "newest" | "oldest";

type BundleOrganizerSurfaceProps = {
  liveMessages?: BundleOrganizerWorkspaceMessage[];
  hasLiveWorkspaceData?: boolean;
  connectedInboxCount?: number;
};

const navItems: Array<{
  id: BundleOrganizerView;
  label: string;
  icon: BundleOrganizerIconName;
  showCount?: boolean;
}> = [
  { id: "priority", label: "Priority", icon: "priority", showCount: true },
  { id: "shortlist", label: "Shortlist", icon: "shortlist", showCount: true },
  { id: "demo", label: "Demo Inbox", icon: "demo", showCount: true },
  { id: "promo", label: "Promo Inbox", icon: "promo", showCount: true },
  { id: "sent", label: "Sent", icon: "sent", showCount: true },
  { id: "trash", label: "Trash", icon: "trash" },
  { id: "settings", label: "Settings", icon: "settings" },
];

const viewCopy: Record<
  BundleOrganizerView,
  { eyebrow: string; title: string; description: string; emptyTitle: string; emptyDescription: string }
> = {
  priority: {
    eyebrow: "Active work queue",
    title: "Priority",
    description: "Active demos, promos, replies, and follow-ups that need attention.",
    emptyTitle: "No priority messages yet.",
    emptyDescription: "Priority work from shared bundle inboxes will collect here.",
  },
  shortlist: {
    eyebrow: "Saved for follow-up",
    title: "Shortlist",
    description: "Saved demos and promos collected for focused follow-up.",
    emptyTitle: "No shortlisted messages yet.",
    emptyDescription: "Shortlist demos or promos to collect them here for review.",
  },
  demo: {
    eyebrow: "Unified demo intake",
    title: "Demo Inbox",
    description: "Demo submissions from connected workspace inboxes in one focused queue.",
    emptyTitle: "No synced demo messages yet.",
    emptyDescription: "Demo messages will appear here after bundle-managed sync is connected.",
  },
  promo: {
    eyebrow: "Unified promo review",
    title: "Promo Inbox",
    description: "Promo campaigns and reminders kept separate from demo discovery.",
    emptyTitle: "No synced promo messages yet.",
    emptyDescription: "Promo messages will appear here after bundle-managed sync is connected.",
  },
  sent: {
    eyebrow: "Organizer sent activity",
    title: "Sent",
    description: "Replies, declines, and forwards sent from Organizer workflows.",
    emptyTitle: "No sent activity yet.",
    emptyDescription: "Sent activity will appear here when safe read-only Organizer history is available.",
  },
  trash: {
    eyebrow: "Organizer-local trash",
    title: "Trash",
    description: "Messages hidden from active Organizer views until restored.",
    emptyTitle: "Trash is empty.",
    emptyDescription: "Trash is local to Organizer and does not move mail in IMAP.",
  },
  settings: {
    eyebrow: "Bundle-managed module",
    title: "Settings",
    description: "Organizer preferences will be managed from the shared Cuevion Workspace.",
    emptyTitle: "Settings are managed by Cuevion Workspace.",
    emptyDescription: "Connected inboxes are shared with this Organizer in Bundle Pilot.",
  },
};

const workspaceSetupEmptyCopy: { emptyTitle: string; emptyDescription: string } = {
  emptyTitle: "No workspace messages loaded yet.",
  emptyDescription:
    "Connect or sync inboxes in Cuevion Workspace to populate this Organizer.",
};

const filterEmptyCopy: { emptyTitle: string; emptyDescription: string } = {
  emptyTitle: "No messages match this filter.",
  emptyDescription: "Try another inbox, status, or filter.",
};

const liveEmptyCopy: Partial<
  Record<BundleOrganizerView, { emptyTitle: string; emptyDescription: string }>
> = {
  priority: {
    emptyTitle: "No active Organizer priority yet",
    emptyDescription:
      "Active demo and promo follow-ups will appear here when they have Organizer workflow state.",
  },
  shortlist: {
    emptyTitle: "No live shortlisted messages yet.",
    emptyDescription:
      "Shortlist rows will appear here when safe Organizer workflow state is available.",
  },
  demo: {
    emptyTitle: "No live demo messages yet.",
    emptyDescription:
      "Demo and high-priority demo messages from connected workspace inboxes will appear here.",
  },
  promo: {
    emptyTitle: "No live promo messages yet.",
    emptyDescription:
      "Promo and promo reminder messages from connected workspace inboxes will appear here.",
  },
  sent: {
    emptyTitle: "No live sent activity yet.",
    emptyDescription:
      "Organizer sent rows will appear here when safe read-only sent activity is available.",
  },
  trash: {
    emptyTitle: "No live Organizer trash yet.",
    emptyDescription:
      "Organizer-local trash rows will appear here only when safe read-only workflow state is available.",
  },
};

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

function NavIcon({ name }: { name: BundleOrganizerIconName }) {
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
      {name === "sent" ? (
        <>
          <path d="M21 3 10 14" />
          <path d="m21 3-7 18-4-7-7-4 18-7Z" />
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
      {name === "settings" ? (
        <>
          <path d="M12 8.25A3.75 3.75 0 1 0 12 15.75 3.75 3.75 0 0 0 12 8.25Z" />
          <path d="M19.4 15a1.8 1.8 0 0 0 .36 1.98l.05.05a2.15 2.15 0 0 1-3.04 3.04l-.05-.05a1.8 1.8 0 0 0-1.98-.36 1.8 1.8 0 0 0-1.09 1.65v.14a2.15 2.15 0 0 1-4.3 0v-.08a1.8 1.8 0 0 0-1.18-1.68 1.8 1.8 0 0 0-1.98.36l-.05.05a2.15 2.15 0 1 1-3.04-3.04l.05-.05A1.8 1.8 0 0 0 3.51 15a1.8 1.8 0 0 0-1.65-1.09h-.14a2.15 2.15 0 0 1 0-4.3h.08a1.8 1.8 0 0 0 1.68-1.18 1.8 1.8 0 0 0-.36-1.98l-.05-.05a2.15 2.15 0 0 1 3.04-3.04l.05.05a1.8 1.8 0 0 0 1.98.36h.08a1.8 1.8 0 0 0 1.09-1.65v-.14a2.15 2.15 0 0 1 4.3 0v.08a1.8 1.8 0 0 0 1.09 1.65 1.8 1.8 0 0 0 1.98-.36l.05-.05a2.15 2.15 0 0 1 3.04 3.04l-.05.05a1.8 1.8 0 0 0-.36 1.98v.08a1.8 1.8 0 0 0 1.65 1.09h.14a2.15 2.15 0 0 1 0 4.3h-.08A1.8 1.8 0 0 0 19.4 15Z" />
        </>
      ) : null}
    </svg>
  );
}

const organizerVisibleCategories = new Set<BundleOrganizerVisibleCategory>([
  "demo",
  "high_priority_demo",
  "promo",
  "promo_reminder",
]);

const activeWorkPriorityStatuses = new Set<BundleOrganizerActiveWorkStatus>([
  "active",
  "waiting",
  "needs_reply",
  "follow_up",
]);

function normalizeOrganizerSignal(value?: string | null) {
  return value?.trim().toLowerCase() ?? "";
}

function isOrganizerVisibleCategory(
  value?: string | null,
): value is BundleOrganizerVisibleCategory {
  return organizerVisibleCategories.has(
    normalizeOrganizerSignal(value) as BundleOrganizerVisibleCategory,
  );
}

function resolveOrganizerSignalFallback(value?: string | null) {
  const normalizedValue = normalizeOrganizerSignal(value);

  if (
    normalizedValue === "demo" ||
    normalizedValue === "for review" ||
    normalizedValue === "shortlist"
  ) {
    return "demo";
  }

  if (normalizedValue === "promo") {
    return "promo";
  }

  return null;
}

function resolveOrganizerCategory(
  message: Pick<
    BundleOrganizerMessage,
    "manualCategory" | "internalClassification" | "category" | "uiSignal" | "signal"
  >,
) {
  if (message.manualCategory === "demo" || message.manualCategory === "promo") {
    return message.manualCategory;
  }

  if (isOrganizerVisibleCategory(message.internalClassification)) {
    return normalizeOrganizerSignal(
      message.internalClassification,
    ) as BundleOrganizerVisibleCategory;
  }

  if (isOrganizerVisibleCategory(message.category)) {
    return normalizeOrganizerSignal(message.category) as BundleOrganizerVisibleCategory;
  }

  return (
    resolveOrganizerSignalFallback(message.uiSignal) ??
    resolveOrganizerSignalFallback(message.signal)
  );
}

function shouldShowInDemoInbox(message: BundleOrganizerMessage) {
  const category = resolveOrganizerCategory(message);
  return category === "demo" || category === "high_priority_demo";
}

function shouldShowInPromoInbox(message: BundleOrganizerMessage) {
  const category = resolveOrganizerCategory(message);
  return category === "promo" || category === "promo_reminder";
}

function shouldShowInOrganizerPriority(message: BundleOrganizerMessage) {
  if (resolveOrganizerCategory(message) === null) {
    return false;
  }

  if (message.manualPriority === true) {
    return true;
  }

  if (
    message.active_work_status &&
    activeWorkPriorityStatuses.has(message.active_work_status)
  ) {
    return true;
  }

  return normalizeOrganizerSignal(message.v7_final_priority) === "priority";
}

function countUnreadMessages(messages: BundleOrganizerMessage[]) {
  return messages.filter((message) => message.unread === true).length;
}

function resolvePriorityReason(message: BundleOrganizerMessage) {
  if (message.reason) {
    return message.reason;
  }

  if (message.manualPriority === true) {
    return "Manual Organizer priority.";
  }

  if (message.active_work_status && activeWorkPriorityStatuses.has(message.active_work_status)) {
    return `Organizer active work: ${message.active_work_status.replace(/_/g, " ")}.`;
  }

  if (normalizeOrganizerSignal(message.v7_final_priority) === "priority") {
    return "Organizer priority signal.";
  }

  return null;
}

function resolveWorkspaceMessageKind(message: BundleOrganizerMessage): BundleOrganizerMessage["kind"] {
  const category = resolveOrganizerCategory(message);
  return category === "promo" || category === "promo_reminder" ? "promo" : "demo";
}

function normalizeWorkspaceMessages(
  liveMessages: BundleOrganizerWorkspaceMessage[],
): BundleOrganizerMessage[] {
  const messagesByIdentity = new Map<string, BundleOrganizerMessage>();

  liveMessages
    .map((message): BundleOrganizerMessage => ({
      ...message,
      id: `workspace-${message.id}`,
      kind: "demo",
      source: "workspace" as const,
      priorityBadge:
        message.priorityBadge ??
        (message.internalClassification === "high_priority_demo"
          ? "High-priority demo"
          : undefined),
    }))
    .map((message) => ({
      ...message,
      kind: resolveWorkspaceMessageKind(message),
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

function getLiveMessagesForView(
  view: BundleOrganizerView,
  liveMessages: BundleOrganizerMessage[],
) {
  if (view === "priority") {
    return liveMessages.filter(shouldShowInOrganizerPriority);
  }

  if (view === "demo") {
    return liveMessages.filter(shouldShowInDemoInbox);
  }

  if (view === "promo") {
    return liveMessages.filter(shouldShowInPromoInbox);
  }

  return [];
}

function getMessagesForView(
  view: BundleOrganizerView,
  liveMessages: BundleOrganizerMessage[],
) {
  return {
    messages: getLiveMessagesForView(view, liveMessages),
    source: "workspace" as const,
  };
}

function getCounts(liveMessages: BundleOrganizerMessage[]) {
  return navItems.reduce<Partial<Record<BundleOrganizerView, number>>>((counts, item) => {
    const viewMessages = getMessagesForView(item.id, liveMessages).messages;

    counts[item.id] =
      item.id === "promo"
        ? countUnreadMessages(
            viewMessages.filter(
              (message) => resolveOrganizerCategory(message) !== "promo_reminder",
            ),
          )
        : countUnreadMessages(viewMessages);

    return counts;
  }, {});
}

function getSourceMailboxOptions(messages: BundleOrganizerMessage[]) {
  return Array.from(
    new Set(messages.map((message) => message.sourceMailbox).filter(Boolean)),
  ).sort((first, second) => first.localeCompare(second));
}

function getMessageSortValue(message: BundleOrganizerMessage) {
  return message.sortTimestamp ?? 0;
}

function statusPillClass(status: NonNullable<BundleOrganizerMessage["status"]> | "shortlisted" | "priority") {
  if (status === "declined") {
    return "border-[rgba(232,146,118,0.22)] bg-[rgba(161,78,55,0.14)] text-[rgba(246,184,162,0.88)]";
  }

  if (status === "shortlisted") {
    return "border-[rgba(246,183,91,0.24)] bg-[rgba(214,137,45,0.14)] text-[rgba(255,204,125,0.9)]";
  }

  if (status === "sent") {
    return "border-[rgba(139,179,194,0.22)] bg-[rgba(139,179,194,0.12)] text-[rgba(196,226,237,0.88)]";
  }

  if (status === "trashed") {
    return "border-white/15 bg-white/8 text-[rgba(245,239,229,0.66)]";
  }

  return "border-[rgba(143,179,159,0.22)] bg-[rgba(143,179,159,0.12)] text-[rgba(198,228,209,0.88)]";
}

function MessagePill({ children, tone }: { children: string; tone: Parameters<typeof statusPillClass>[0] }) {
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[0.68rem] font-medium uppercase tracking-[0.1em] ${statusPillClass(tone)}`}>
      {children}
    </span>
  );
}

export function BundleOrganizerSurface({
  liveMessages = [],
  hasLiveWorkspaceData = false,
  connectedInboxCount = 0,
}: BundleOrganizerSurfaceProps) {
  const [activeView, setActiveView] = useState<BundleOrganizerView>("priority");
  const [searchQuery, setSearchQuery] = useState("");
  const [actionFeedback, setActionFeedback] = useState<string | null>(null);
  const [selectedMessage, setSelectedMessage] = useState<BundleOrganizerMessage | null>(null);
  const [sourceMailboxFilter, setSourceMailboxFilter] = useState("all");
  const [promoFilter, setPromoFilter] = useState<BundleOrganizerPromoFilter>("all");
  const [sortOrder, setSortOrder] = useState<BundleOrganizerSortOrder>("newest");

  const workspaceMessages = useMemo(
    () => normalizeWorkspaceMessages(liveMessages),
    [liveMessages],
  );
  const shouldUseLiveWorkspaceData = hasLiveWorkspaceData || workspaceMessages.length > 0;
  const counts = useMemo(() => getCounts(workspaceMessages), [workspaceMessages]);
  const activeDisplay = useMemo(
    () => getMessagesForView(activeView, workspaceMessages),
    [activeView, workspaceMessages],
  );
  const sourceMailboxOptions = useMemo(
    () =>
      activeView === "demo" || activeView === "promo"
        ? getSourceMailboxOptions(activeDisplay.messages)
        : [],
    [activeDisplay.messages, activeView],
  );
  const activeMessages = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLowerCase();
    let nextMessages = activeDisplay.messages;

    if (
      (activeView === "demo" || activeView === "promo") &&
      sourceMailboxFilter !== "all"
    ) {
      nextMessages = nextMessages.filter(
        (message) => message.sourceMailbox === sourceMailboxFilter,
      );
    }

    if (activeView === "promo") {
      if (promoFilter === "reminders") {
        nextMessages = nextMessages.filter(
          (message) => resolveOrganizerCategory(message) === "promo_reminder",
        );
      } else if (promoFilter === "unread") {
        nextMessages = nextMessages.filter((message) => message.unread === true);
      }
    }

    if (normalizedQuery) {
      nextMessages = nextMessages.filter((message) =>
        [
          message.sender,
          message.subject,
          message.snippet,
          message.sourceMailbox,
          ...message.body,
        ].some((value) => value.toLowerCase().includes(normalizedQuery)),
      );
    }

    return [...nextMessages].sort((first, second) =>
      sortOrder === "oldest"
        ? getMessageSortValue(first) - getMessageSortValue(second)
        : getMessageSortValue(second) - getMessageSortValue(first),
    );
  }, [
    activeDisplay,
    activeView,
    promoFilter,
    searchQuery,
    sortOrder,
    sourceMailboxFilter,
  ]);
  const activeCopy = viewCopy[activeView];
  const isFilterableView = activeView === "demo" || activeView === "promo";
  const hasLocalFilter =
    searchQuery.trim().length > 0 ||
    (isFilterableView && sourceMailboxFilter !== "all") ||
    (activeView === "promo" && promoFilter !== "all");
  const activeEmptyCopy =
    !shouldUseLiveWorkspaceData
      ? workspaceSetupEmptyCopy
      : activeDisplay.messages.length > 0 && hasLocalFilter
      ? filterEmptyCopy
      : liveEmptyCopy[activeView] ?? activeCopy;
  const activeSourceLabel = shouldUseLiveWorkspaceData
    ? "Live workspace preview"
    : "Workspace setup";
  const activeSourceDescription =
    shouldUseLiveWorkspaceData
      ? "Focused Demo and Promo views are displaying read-only workspace messages."
      : "Connect or sync inboxes in Cuevion Workspace to populate this Organizer.";
  const displayedConnectedInboxCount = connectedInboxCount;
  const previewGroupCounts = useMemo(() => {
    const demoMessages = getMessagesForView("demo", workspaceMessages).messages;
    const promoMessages = getMessagesForView("promo", workspaceMessages).messages;

    return {
      highPriorityDemos: demoMessages.filter(
        (message) => message.internalClassification === "high_priority_demo",
      ).length,
      promoReminders: promoMessages.filter(
        (message) => message.internalClassification === "promo_reminder",
      ).length,
    };
  }, [workspaceMessages]);

  const selectView = (view: BundleOrganizerView) => {
    setActiveView(view);
    setSourceMailboxFilter("all");
    setPromoFilter("all");
    setActionFeedback(null);
    setSelectedMessage(null);
  };

  const showStaticFeedback = (message: string) => {
    setActionFeedback(message);
  };

  const openMessageDetail = (message: BundleOrganizerMessage) => {
    setSelectedMessage(message);
    setActionFeedback(null);
  };

  const closeMessageDetail = () => {
    setSelectedMessage(null);
    setActionFeedback(null);
  };

  return (
    <div className="min-h-0 flex-1 overflow-hidden">
      <section className="flex h-full min-h-0 flex-col overflow-hidden rounded-[18px] bg-[radial-gradient(circle_at_top_left,rgba(88,69,55,0.24),transparent_32%),radial-gradient(circle_at_right,rgba(55,87,74,0.2),transparent_28%),linear-gradient(180deg,#1d1b18_0%,#101915_100%)] p-2 text-[color:#f5efe5] md:p-3">
        <header className="border-b border-white/10 pb-2.5">
          <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
            <div>
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
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center xl:max-w-[600px]">
              <span className="w-fit rounded-full border border-[rgba(143,179,159,0.22)] bg-[rgba(143,179,159,0.1)] px-3 py-1 text-[0.68rem] font-medium uppercase tracking-[0.14em] text-[rgba(198,228,209,0.78)]">
                Bundle Pilot
              </span>
              <p className="text-[0.84rem] leading-6 text-[rgba(245,239,229,0.58)]">
                {shouldUseLiveWorkspaceData
                  ? "Live workspace preview for the embedded Organizer."
                  : "Workspace messages will appear here after inbox data is loaded."}
              </p>
            </div>
          </div>
          <div className="mt-3 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <p className="max-w-[560px] text-[0.96rem] leading-7 text-[rgba(245,239,229,0.66)]">
              A dedicated music inbox for demos, promos, and active follow-ups.
            </p>
            <div className="relative w-full lg:max-w-[340px]">
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
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder="Search messages..."
                className="h-10 w-full rounded-full border border-white/10 bg-white/5 pl-10 pr-10 text-[0.86rem] font-medium text-[rgba(245,239,229,0.84)] outline-none transition-colors placeholder:text-[rgba(245,239,229,0.38)] hover:border-[rgba(143,179,159,0.24)] hover:bg-white/8 focus:border-[rgba(143,179,159,0.34)] focus:bg-white/10 focus:ring-2 focus:ring-[rgba(143,179,159,0.14)]"
              />
              {searchQuery ? (
                <button
                  type="button"
                  onClick={() => setSearchQuery("")}
                  className="absolute right-2 top-1/2 inline-flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-full text-[1rem] leading-none text-[rgba(245,239,229,0.46)] transition-colors hover:bg-[rgba(143,179,159,0.12)] hover:text-[rgba(198,228,209,0.9)] focus-visible:outline-none"
                  aria-label="Clear message search"
                >
                  x
                </button>
              ) : null}
            </div>
          </div>
        </header>

        <div className="grid min-h-0 flex-1 gap-3 overflow-hidden pt-3 lg:grid-cols-[210px_minmax(0,1fr)] xl:grid-cols-[220px_minmax(0,1fr)]">
          <aside className="flex min-h-0 min-w-0 flex-col gap-3 overflow-y-auto pb-2.5 lg:pb-0">
            <section className="rounded-[14px] border border-white/10 bg-white/[0.04] px-3 py-2.5">
              <p className="text-[0.66rem] font-medium uppercase tracking-[0.15em] text-[rgba(217,203,184,0.55)]">
                Connected Inboxes
              </p>
              <div className="mt-1.5 flex items-center justify-between gap-2">
                <span className="text-[1.45rem] font-semibold leading-none tracking-[-0.04em] text-[color:#f5efe5]">
                  {displayedConnectedInboxCount}
                </span>
                <span className="inline-flex h-7 shrink-0 items-center justify-center rounded-full border border-[rgba(143,179,159,0.24)] bg-[rgba(143,179,159,0.1)] px-2.5 text-[0.64rem] font-medium uppercase tracking-[0.1em] text-[rgba(167,203,181,0.84)]">
                  Shared
                </span>
              </div>
              <p className="mt-1 truncate text-[0.68rem] font-medium leading-4 text-[rgba(167,203,181,0.72)]">
                Workspace preview
              </p>
            </section>

            <nav
              aria-label="Organizer sections"
              className="flex gap-2 overflow-x-auto lg:block lg:overflow-visible"
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
                      <NavIcon name={item.icon} />
                      <span className="truncate">{item.label}</span>
                    </span>
                    {item.showCount && count > 0 ? (
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

            <section className="border-t border-white/10 pt-3">
              <div className="flex h-10 items-center justify-between gap-2 rounded-full px-3.5 text-[0.62rem] font-semibold uppercase tracking-[0.14em] text-[rgba(217,203,184,0.5)]">
                <span>Preview Groups</span>
                <button
                  type="button"
                  onClick={() => showStaticFeedback("Preview groups are static in Bundle Pilot.")}
                  className="inline-flex h-6 w-6 items-center justify-center rounded-full border border-[rgba(143,179,159,0.2)] bg-[rgba(143,179,159,0.1)] text-[0.86rem] leading-none text-[rgba(198,228,209,0.86)] transition-colors hover:bg-[rgba(143,179,159,0.16)]"
                  aria-label="Create preview group"
                >
                  +
                </button>
              </div>
              {[
                { label: "High priority demos", count: previewGroupCounts.highPriorityDemos },
                { label: "Promo reminders", count: previewGroupCounts.promoReminders },
              ].map((previewGroup) => (
                <button
                  key={previewGroup.label}
                  type="button"
                  onClick={() => showStaticFeedback(`${previewGroup.label} is a static Bundle Pilot preview group.`)}
                  className="mb-2 flex h-10 w-full shrink-0 items-center justify-between gap-2 rounded-full px-3.5 text-[0.82rem] font-medium text-[rgba(245,239,229,0.72)] transition-colors hover:bg-white/5"
                >
                  <span className="truncate">{previewGroup.label}</span>
                  <span className="rounded-full bg-white/5 px-2 py-0.5 text-[0.72rem] text-[rgba(245,239,229,0.52)]">
                    {previewGroup.count}
                  </span>
                </button>
              ))}
            </section>
          </aside>

          <main className="min-h-0 min-w-0 overflow-y-auto">
            <section className="rounded-[20px] border border-white/10 bg-[rgba(25,34,30,0.82)] p-4 shadow-[0_18px_48px_rgba(0,0,0,0.26)] sm:p-5 xl:p-6">
              <div className="flex flex-col gap-3 border-b border-white/10 pb-4 md:flex-row md:items-end md:justify-between">
                <div>
                  <p className="text-[0.74rem] font-medium uppercase tracking-[0.16em] text-[rgba(217,203,184,0.58)]">
                    {activeCopy.eyebrow}
                  </p>
                  <h2 className="mt-1.5 text-[1.36rem] font-semibold tracking-[-0.03em] text-[color:#f5efe5]">
                    {activeCopy.title}
                  </h2>
                </div>
                <p className="max-w-[460px] text-[0.82rem] leading-6 text-[rgba(245,239,229,0.58)] md:text-right">
                  {activeView === "sent"
                    ? "Organizer-sent replies and declines will appear when read-only history is connected."
                    : activeView === "trash"
                    ? "Trash is Organizer-local and does not move mail in IMAP."
                    : activeView === "settings"
                    ? "Bundle Pilot settings are shown as safe, static workspace preview controls."
                    : activeSourceDescription}
                </p>
              </div>

              {activeView === "settings" ? (
                <div className="pt-4">
                  <BundleOrganizerSettings />
                </div>
              ) : (
                <div className="pt-4">
                  <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                    <p className="max-w-[920px] text-[0.9rem] leading-6 text-[rgba(245,239,229,0.66)]">
                      {activeCopy.description}
                    </p>
                    <span className="w-fit shrink-0 rounded-full border border-[rgba(143,179,159,0.2)] bg-[rgba(143,179,159,0.1)] px-3 py-1 text-[0.68rem] font-medium uppercase tracking-[0.12em] text-[rgba(198,228,209,0.78)]">
                      {activeSourceLabel}
                    </span>
                  </div>

                  {selectedMessage ? (
                    <BundleOrganizerMessageDetail
                      message={selectedMessage}
                      onBack={closeMessageDetail}
                      onAction={showStaticFeedback}
                    />
                  ) : (
                    <>
                      {activeView === "demo" || activeView === "promo" ? (
                        <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
                          <div className="flex flex-wrap items-center gap-2">
                            {activeView === "promo"
                              ? ([
                                  { id: "all", label: "All promos" },
                                  { id: "reminders", label: "Reminders" },
                                  { id: "unread", label: "Unread" },
                                ] satisfies Array<{
                                  id: BundleOrganizerPromoFilter;
                                  label: string;
                                }>).map((filter) => (
                                  <button
                                    key={filter.id}
                                    type="button"
                                    onClick={() => setPromoFilter(filter.id)}
                                    className={`inline-flex h-9 items-center justify-center rounded-full border px-3.5 text-[0.74rem] font-medium uppercase tracking-[0.1em] transition-colors ${
                                      promoFilter === filter.id
                                        ? "border-[rgba(143,179,159,0.34)] bg-[rgba(143,179,159,0.16)] text-[rgba(198,228,209,0.9)]"
                                        : "border-white/10 bg-white/5 text-[rgba(245,239,229,0.62)] hover:border-[rgba(143,179,159,0.24)] hover:bg-[rgba(143,179,159,0.1)] hover:text-[rgba(198,228,209,0.84)]"
                                    }`}
                                  >
                                    {filter.label}
                                  </button>
                                ))
                              : null}
                            <label className="sr-only" htmlFor="bundle-organizer-source-filter">
                              Filter by source inbox
                            </label>
                            <select
                              id="bundle-organizer-source-filter"
                              value={sourceMailboxFilter}
                              onChange={(event) => setSourceMailboxFilter(event.target.value)}
                              className="h-9 max-w-[190px] rounded-full border border-white/10 bg-[rgba(255,255,255,0.05)] px-3.5 text-[0.74rem] font-medium uppercase tracking-[0.1em] text-[rgba(245,239,229,0.72)] outline-none transition-colors hover:border-[rgba(143,179,159,0.24)] hover:bg-[rgba(143,179,159,0.1)] focus:border-[rgba(143,179,159,0.34)] focus:ring-2 focus:ring-[rgba(143,179,159,0.14)]"
                            >
                              <option value="all">All inboxes</option>
                              {sourceMailboxOptions.map((sourceMailbox) => (
                                <option key={sourceMailbox} value={sourceMailbox}>
                                  {sourceMailbox}
                                </option>
                              ))}
                            </select>
                          </div>
                          <div className="flex flex-wrap items-center gap-2">
                            <button
                              type="button"
                              disabled
                              title="Status filtering will activate when Organizer workflow state is connected."
                              className="inline-flex h-9 cursor-not-allowed items-center justify-center rounded-full border border-white/10 bg-white/[0.025] px-3.5 text-[0.74rem] font-medium uppercase tracking-[0.1em] text-[rgba(245,239,229,0.34)]"
                            >
                              All status
                            </button>
                            <label className="sr-only" htmlFor="bundle-organizer-sort-order">
                              Sort messages
                            </label>
                            <select
                              id="bundle-organizer-sort-order"
                              value={sortOrder}
                              onChange={(event) =>
                                setSortOrder(event.target.value as BundleOrganizerSortOrder)
                              }
                              className="h-9 rounded-full border border-white/10 bg-[rgba(255,255,255,0.05)] px-3.5 text-[0.74rem] font-medium uppercase tracking-[0.1em] text-[rgba(245,239,229,0.72)] outline-none transition-colors hover:border-[rgba(143,179,159,0.24)] hover:bg-[rgba(143,179,159,0.1)] focus:border-[rgba(143,179,159,0.34)] focus:ring-2 focus:ring-[rgba(143,179,159,0.14)]"
                            >
                              <option value="newest">Newest first</option>
                              <option value="oldest">Oldest first</option>
                            </select>
                          </div>
                        </div>
                      ) : null}

                      {activeMessages.length === 0 ? (
                        <div className="mt-4 rounded-[18px] border border-white/10 bg-white/5 px-5 py-10 text-center">
                          {activeView === "shortlist" ? (
                            <span className="mx-auto mb-3 inline-flex rounded-full border border-[rgba(143,179,159,0.2)] bg-[rgba(143,179,159,0.1)] px-3 py-1 text-[0.68rem] font-medium uppercase tracking-[0.12em] text-[rgba(198,228,209,0.78)]">
                              Saved follow-up
                            </span>
                          ) : null}
                          <h3 className="text-[1rem] font-semibold tracking-[-0.02em] text-[color:#f5efe5]">
                            {activeEmptyCopy.emptyTitle}
                          </h3>
                          <p className="mx-auto mt-2 max-w-[460px] text-[0.86rem] leading-6 text-[rgba(245,239,229,0.58)]">
                            {activeEmptyCopy.emptyDescription}
                          </p>
                        </div>
                      ) : (
                        <ul className="mt-4 overflow-hidden rounded-[16px] border border-white/10 bg-white/5">
                          {activeMessages.map((message) => (
                            <li
                              key={message.id}
                              className="border-b border-white/10 last:border-b-0"
                            >
                              <button
                                type="button"
                                onClick={() => openMessageDetail(message)}
                                className="relative grid w-full gap-3 border-l-2 border-transparent px-4 py-3.5 pr-5 text-left transition-[background-color,border-color,box-shadow] hover:border-[color:#8fb39f] hover:bg-white/[0.04] sm:grid-cols-[minmax(150px,0.55fr)_minmax(0,2.6fr)_minmax(72px,auto)] lg:grid-cols-[minmax(170px,0.46fr)_minmax(0,3fr)_minmax(82px,auto)] xl:px-5"
                              >
                          <div className="grid min-w-0 grid-cols-[0.5rem_minmax(0,1fr)] items-start gap-2">
                            <span
                              className={`mt-[0.36rem] h-2 w-2 rounded-full ${
                                message.unread ? "bg-[color:#8fb39f]" : "bg-white/20"
                              }`}
                            />
                            <div className="min-w-0">
                              <div className="truncate text-[0.92rem] font-semibold tracking-[-0.01em] text-[color:#f5efe5]">
                                {message.sender}
                              </div>
                              <div className="mt-1 flex flex-wrap gap-1.5">
                                <BundleOrganizerMessagePills message={message} />
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
                            {activeView === "priority" && resolvePriorityReason(message) ? (
                              <p className="mt-2 text-[0.76rem] font-medium uppercase tracking-[0.12em] text-[rgba(143,179,159,0.78)]">
                                {resolvePriorityReason(message)}
                              </p>
                            ) : null}
                          </div>
                          <div className="text-[0.78rem] font-medium text-[rgba(245,239,229,0.45)] sm:pt-0.5 sm:text-right">
                            {message.timestamp}
                          </div>
                              </button>
                            </li>
                          ))}
                        </ul>
                      )}
                    </>
                  )}
                  {actionFeedback ? (
                    <p className="mt-3 rounded-[14px] border border-[rgba(143,179,159,0.16)] bg-[rgba(143,179,159,0.1)] px-3.5 py-2.5 text-[0.84rem] leading-6 text-[rgba(167,203,181,0.9)]">
                      {actionFeedback}
                    </p>
                  ) : null}
                </div>
              )}
            </section>
          </main>
        </div>
      </section>
    </div>
  );
}

function BundleOrganizerMessagePills({ message }: { message: BundleOrganizerMessage }) {
  return (
    <>
      <span className="max-w-full truncate rounded-full bg-white/5 px-2 py-0.5 text-[0.68rem] font-medium text-[rgba(245,239,229,0.56)]">
        {message.sourceMailbox}
      </span>
      {message.priorityBadge ? (
        <span className="rounded-full bg-[rgba(143,179,159,0.14)] px-2 py-0.5 text-[0.68rem] font-medium text-[rgba(167,203,181,0.9)]">
          {message.priorityBadge}
        </span>
      ) : null}
      {message.internalClassification ? (
        <span className="rounded-full bg-white/5 px-2 py-0.5 text-[0.68rem] font-medium text-[rgba(245,239,229,0.56)]">
          {message.internalClassification.replace(/_/g, " ")}
        </span>
      ) : null}
      {message.shortlisted ? <MessagePill tone="shortlisted">Shortlisted</MessagePill> : null}
      {message.status ? <MessagePill tone={message.status}>{message.status}</MessagePill> : null}
    </>
  );
}

function BundleOrganizerMessageDetail({
  message,
  onBack,
  onAction,
}: {
  message: BundleOrganizerMessage;
  onBack: () => void;
  onAction: (message: string) => void;
}) {
  const activeReason = resolvePriorityReason(message) ?? message.reason;
  const bodyLines = message.body.length > 0 ? message.body : [message.snippet];
  const actionButtonClass =
    "inline-flex h-9 items-center justify-center rounded-full border border-[rgba(143,179,159,0.2)] bg-[rgba(143,179,159,0.1)] px-3.5 text-[0.72rem] font-medium uppercase tracking-[0.11em] text-[rgba(198,228,209,0.82)] transition-colors hover:border-[rgba(143,179,159,0.3)] hover:bg-[rgba(143,179,159,0.16)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(143,179,159,0.24)]";

  const handleAction = (label: string) => {
    onAction(`${label} is a read-only Bundle Pilot action. No mailbox message was changed.`);
  };

  return (
    <article className="mt-4 rounded-[18px] border border-white/10 bg-white/[0.045] p-4 sm:p-5">
      <div className="flex flex-col gap-3 border-b border-white/10 pb-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <button
            type="button"
            onClick={onBack}
            className="mb-3 inline-flex h-8 items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 text-[0.7rem] font-medium uppercase tracking-[0.12em] text-[rgba(245,239,229,0.62)] transition-colors hover:border-[rgba(143,179,159,0.24)] hover:bg-[rgba(143,179,159,0.1)] hover:text-[rgba(198,228,209,0.84)]"
          >
            Back
          </button>
          <p className="text-[0.74rem] font-medium uppercase tracking-[0.16em] text-[rgba(217,203,184,0.58)]">
            {message.sender}
          </p>
          <h3 className="mt-1.5 text-[1.3rem] font-semibold tracking-[-0.03em] text-[color:#f5efe5]">
            {message.subject}
          </h3>
          <div className="mt-3 flex flex-wrap gap-1.5">
            <BundleOrganizerMessagePills message={message} />
          </div>
        </div>
        <div className="shrink-0 text-left text-[0.78rem] font-medium text-[rgba(245,239,229,0.48)] sm:text-right">
          <div>{message.timestamp}</div>
          <div className="mt-1 text-[rgba(245,239,229,0.58)]">{message.sourceMailbox}</div>
        </div>
      </div>

      <div className="grid gap-4 pt-4 lg:grid-cols-[minmax(0,1fr)_220px]">
        <div className="min-w-0">
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

        <aside className="rounded-[16px] border border-white/10 bg-white/[0.04] p-3">
          <p className="px-1 text-[0.66rem] font-semibold uppercase tracking-[0.14em] text-[rgba(217,203,184,0.52)]">
            Safe actions
          </p>
          <div className="mt-3 flex flex-col gap-2">
            {["Reply", "Decline", "Shortlist", "Mark Reviewed"].map((label) => (
              <button
                key={label}
                type="button"
                onClick={() => handleAction(label)}
                className={actionButtonClass}
              >
                {label}
              </button>
            ))}
          </div>
        </aside>
      </div>
    </article>
  );
}

function BundleOrganizerSettings() {
  return (
    <div className="space-y-3">
      {[
        {
          eyebrow: "Workspace managed",
          title: "Inbox access",
          description:
            "Connected inboxes are shared with this Organizer in Bundle Pilot. Inbox setup stays in Cuevion Workspace.",
          badge: "Shared",
        },
        {
          eyebrow: "Organizer preview",
          title: "Preview groups and routing",
          description:
            "Preview groups are static pilot structure. No classifier, filtering, or mailbox routing changes are active.",
          badge: "Static",
        },
        {
          eyebrow: "Safe shell",
          title: "Mail actions",
          description:
            "Reply, decline, shortlist, trash, and restore controls are visual only in this internal review surface.",
          badge: "No-op",
        },
      ].map((item) => (
        <section
          key={item.title}
          className="rounded-[16px] border border-white/10 bg-white/[0.04] p-4"
        >
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <p className="text-[0.68rem] font-semibold uppercase tracking-[0.16em] text-[rgba(167,203,181,0.78)]">
                {item.eyebrow}
              </p>
              <h3 className="mt-2 text-[1rem] font-semibold tracking-[-0.02em] text-[color:#f5efe5]">
                {item.title}
              </h3>
              <p className="mt-2 text-[0.84rem] leading-6 text-[rgba(245,239,229,0.6)]">
                {item.description}
              </p>
            </div>
            <span className="w-fit rounded-full border border-[rgba(143,179,159,0.22)] bg-[rgba(143,179,159,0.1)] px-2.5 py-1 text-[0.66rem] font-medium uppercase tracking-[0.12em] text-[rgba(198,228,209,0.82)]">
              {item.badge}
            </span>
          </div>
        </section>
      ))}
    </div>
  );
}
