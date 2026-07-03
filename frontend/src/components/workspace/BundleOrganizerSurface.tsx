import { useMemo, useState } from "react";

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

type BundleOrganizerSurfaceProps = {
  liveMessages?: BundleOrganizerWorkspaceMessage[];
  hasLiveWorkspaceData?: boolean;
  connectedInboxCount?: number;
};

const navItems: Array<{
  id: BundleOrganizerView;
  label: string;
}> = [
  { id: "priority", label: "Priority" },
  { id: "demo", label: "Demo Inbox" },
  { id: "promo", label: "Promo Inbox" },
];

const viewCopy: Record<
  BundleOrganizerView,
  { title: string; emptyTitle: string; emptyDescription: string }
> = {
  priority: {
    title: "Priority",
    emptyTitle: "No priority messages.",
    emptyDescription: "Priority demo and promo messages will appear here.",
  },
  demo: {
    title: "Demo Inbox",
    emptyTitle: "No demo messages.",
    emptyDescription: "Demo messages from connected inboxes will appear here.",
  },
  promo: {
    title: "Promo Inbox",
    emptyTitle: "No promo messages.",
    emptyDescription: "Promo messages from connected inboxes will appear here.",
  },
};

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
    return "Manual priority.";
  }

  if (message.active_work_status && activeWorkPriorityStatuses.has(message.active_work_status)) {
    return message.active_work_status.replace(/_/g, " ");
  }

  if (normalizeOrganizerSignal(message.v7_final_priority) === "priority") {
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

function MessagePills({ message }: { message: BundleOrganizerMessage }) {
  return (
    <>
      {message.sourceMailbox ? (
        <span className="max-w-full truncate rounded-full bg-white/5 px-2 py-0.5 text-[0.68rem] font-medium text-[rgba(245,239,229,0.56)]">
          {message.sourceMailbox}
        </span>
      ) : null}
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
}: BundleOrganizerSurfaceProps) {
  const [activeView, setActiveView] = useState<BundleOrganizerView>("priority");
  const [selectedMessage, setSelectedMessage] = useState<BundleOrganizerMessage | null>(null);
  const workspaceMessages = useMemo(
    () => normalizeWorkspaceMessages(liveMessages),
    [liveMessages],
  );
  const counts = useMemo(() => getCounts(workspaceMessages), [workspaceMessages]);
  const activeMessages = useMemo(
    () => getMessagesForView(activeView, workspaceMessages),
    [activeView, workspaceMessages],
  );
  const activeCopy = viewCopy[activeView];
  const hasOrganizerData = hasLiveWorkspaceData || workspaceMessages.length > 0;

  const selectView = (view: BundleOrganizerView) => {
    setActiveView(view);
    setSelectedMessage(null);
  };

  return (
    <div className="min-h-0 flex-1 overflow-hidden">
      <section className="flex h-full min-h-0 flex-col overflow-hidden rounded-[18px] bg-[linear-gradient(180deg,#1d1b18_0%,#101915_100%)] p-2 text-[color:#f5efe5] md:p-3">
        <header className="border-b border-white/10 pb-3">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
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
        </header>

        <div className="grid min-h-0 flex-1 gap-3 overflow-hidden pt-3 lg:grid-cols-[210px_minmax(0,1fr)] xl:grid-cols-[220px_minmax(0,1fr)]">
          <aside className="min-h-0 min-w-0 overflow-y-auto pb-2.5 lg:pb-0">
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
                    <span className="truncate">{item.label}</span>
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

          <main className="min-h-0 min-w-0 overflow-y-auto">
            <section className="rounded-[20px] border border-white/10 bg-[rgba(25,34,30,0.82)] p-4 shadow-[0_18px_48px_rgba(0,0,0,0.26)] sm:p-5 xl:p-6">
              <div className="border-b border-white/10 pb-4">
                <h2 className="text-[1.36rem] font-semibold tracking-[-0.03em] text-[color:#f5efe5]">
                  {activeCopy.title}
                </h2>
              </div>

              {selectedMessage ? (
                <MessageDetail
                  message={selectedMessage}
                  onBack={() => setSelectedMessage(null)}
                />
              ) : activeMessages.length === 0 ? (
                <div className="mt-4 rounded-[18px] border border-white/10 bg-white/5 px-5 py-10 text-center">
                  <h3 className="text-[1rem] font-semibold tracking-[-0.02em] text-[color:#f5efe5]">
                    {hasOrganizerData ? activeCopy.emptyTitle : "No messages loaded."}
                  </h3>
                  <p className="mx-auto mt-2 max-w-[460px] text-[0.86rem] leading-6 text-[rgba(245,239,229,0.58)]">
                    {hasOrganizerData
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
                        className="grid w-full gap-3 border-l-2 border-transparent px-4 py-3.5 text-left transition-[background-color,border-color,box-shadow] hover:border-[color:#8fb39f] hover:bg-white/[0.04] sm:grid-cols-[minmax(150px,0.55fr)_minmax(0,2.6fr)_minmax(72px,auto)] lg:grid-cols-[minmax(170px,0.46fr)_minmax(0,3fr)_minmax(82px,auto)] xl:px-5"
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
