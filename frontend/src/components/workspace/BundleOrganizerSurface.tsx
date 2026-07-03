import { useMemo, useState } from "react";
import {
  ACTIVE_WORK_PRIORITY_STATUSES,
  countUnreadMessages,
  formatOrganizerSignal,
  shouldShowInDemoInbox,
  shouldShowInOrganizerPriority,
  shouldShowInPromoInbox,
  type BundleOrganizerActiveWorkStatus,
} from "./bundleOrganizerFilters";

type BundleOrganizerView = "priority" | "demo" | "promo";

type BundleOrganizerMessage = {
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
  ui_signal?: string;
  uiSignal?: string;
  unread?: boolean;
  manualPriority?: boolean | null;
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
  internalClassification?: BundleOrganizerInternalClassification;
  category?: string;
  signal?: string;
  uiSignal?: string;
  unread?: boolean;
  manualPriority?: boolean | null;
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
  { id: "demo", label: "Demo Inbox", icon: "demo" },
  { id: "promo", label: "Promo Inbox", icon: "promo" },
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
};

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

function getMessagesForView(
  view: BundleOrganizerView,
  liveMessages: BundleOrganizerMessage[],
) {
  if (view === "priority") {
    return liveMessages.filter(shouldShowInOrganizerPriority);
  }

  if (view === "demo") {
    return liveMessages.filter(shouldShowInDemoInbox);
  }

  return liveMessages.filter(shouldShowInPromoInbox);
}

function getCounts(liveMessages: BundleOrganizerMessage[]) {
  return navItems.reduce<Partial<Record<BundleOrganizerView, number>>>((counts, item) => {
    counts[item.id] = countUnreadMessages(getMessagesForView(item.id, liveMessages));
    return counts;
  }, {});
}

function doesMessageMatchSearch(message: BundleOrganizerMessage, searchQuery: string) {
  const normalizedSearchQuery = searchQuery.trim().toLowerCase();

  if (!normalizedSearchQuery) {
    return true;
  }

  return [
    message.sender,
    message.subject,
    message.snippet,
    message.sourceMailbox,
  ].some((value) => value.toLowerCase().includes(normalizedSearchQuery));
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
    </svg>
  );
}

function MessagePills({ message }: { message: BundleOrganizerMessage }) {
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
      {message.internalClassification ? (
        <span className="rounded-full bg-[rgba(120,104,89,0.1)] px-2 py-0.5 text-[0.68rem] font-medium text-[rgba(64,56,48,0.62)] dark:bg-white/5 dark:text-[rgba(245,239,229,0.56)]">
          {message.internalClassification.replace(/_/g, " ")}
        </span>
      ) : null}
    </>
  );
}

function MessageDetail({
  message,
  onBack,
}: {
  message: BundleOrganizerMessage;
  onBack: () => void;
}) {
  const activeReason = resolvePriorityReason(message);
  const bodyLines = message.body.length > 0 ? message.body : [message.snippet];

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
  const [activeView, setActiveView] = useState<BundleOrganizerView>("priority");
  const [selectedMessage, setSelectedMessage] = useState<BundleOrganizerMessage | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const workspaceMessages = useMemo(
    () => normalizeWorkspaceMessages(liveMessages),
    [liveMessages],
  );
  const counts = useMemo(() => getCounts(workspaceMessages), [workspaceMessages]);
  const rawActiveMessages = useMemo(
    () => getMessagesForView(activeView, workspaceMessages),
    [activeView, workspaceMessages],
  );
  const activeMessages = useMemo(
    () =>
      rawActiveMessages.filter((message) =>
        doesMessageMatchSearch(message, searchQuery),
      ),
    [rawActiveMessages, searchQuery],
  );
  const activeCopy = viewCopy[activeView];
  const hasOrganizerData = hasLiveWorkspaceData || workspaceMessages.length > 0;
  const isSearchActive = searchQuery.trim().length > 0;

  const selectView = (view: BundleOrganizerView) => {
    setActiveView(view);
    setSelectedMessage(null);
  };

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
                    {count > 0 ? (
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

              {selectedMessage ? (
                <MessageDetail
                  message={selectedMessage}
                  onBack={() => setSelectedMessage(null)}
                />
              ) : activeMessages.length === 0 ? (
                <div className="mt-4 rounded-[18px] border border-white/10 bg-white/5 px-5 py-10 text-center">
                  <h3 className="text-[1rem] font-semibold tracking-[-0.02em] text-[color:#f5efe5]">
                    {isSearchActive
                      ? "No matching messages."
                      : hasOrganizerData
                      ? activeCopy.emptyTitle
                      : "No messages loaded."}
                  </h3>
                  <p className="mx-auto mt-2 max-w-[460px] text-[0.86rem] leading-6 text-[rgba(245,239,229,0.58)]">
                    {isSearchActive
                      ? "Try a different sender, subject, snippet, or source mailbox."
                      : hasOrganizerData
                      ? activeCopy.emptyDescription
                      : "Connected inbox messages will appear here after sync."}
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
                        onClick={() => setSelectedMessage(message)}
                        className="grid w-full gap-3 border-l-2 border-transparent px-3 py-3.5 text-left transition-[background-color,border-color,box-shadow] hover:bg-white/5 sm:grid-cols-[minmax(150px,0.55fr)_minmax(0,2.6fr)_minmax(72px,auto)] sm:px-4 lg:grid-cols-[minmax(170px,0.46fr)_minmax(0,3fr)_minmax(82px,auto)] xl:px-5"
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
            </section>
          </main>
        </div>
      </section>
    </div>
  );
}
