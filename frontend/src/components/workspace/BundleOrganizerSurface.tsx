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
  kind: "demo" | "promo" | "reply" | "sent";
  source: "sample" | "workspace";
  sender: string;
  subject: string;
  snippet: string;
  body: string[];
  timestamp: string;
  sourceMailbox: string;
  internalClassification?: BundleOrganizerInternalClassification;
  signal?: string;
  uiSignal?: string;
  unread?: boolean;
  shortlisted?: boolean;
  priority?: boolean;
  priorityBadge?: string;
  status?: "replied" | "declined" | "interested" | "sent" | "trashed";
  reason?: string;
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
  internalClassification?: BundleOrganizerInternalClassification;
  signal?: string;
  uiSignal?: string;
  unread?: boolean;
  priority?: boolean;
  priorityBadge?: string;
  reason?: string;
  sortTimestamp?: number;
};

type BundleOrganizerSurfaceProps = {
  liveMessages?: BundleOrganizerWorkspaceMessage[];
  connectedInboxCount?: number;
};

const bundleOrganizerMessages: BundleOrganizerMessage[] = [
  {
    id: "bundle-demo-mila-hart",
    kind: "demo",
    source: "sample",
    internalClassification: "high_priority_demo",
    sender: "Mila Hart",
    subject: "Demo submission - late night melodic house",
    snippet:
      "Private SoundCloud link included. References recent Nora En Pure and Lane 8 playlist support.",
    body: [
      "Hi Cuevion team, I wanted to share a late night melodic house demo that feels close to the sound you have been supporting.",
      "The private link includes the full mix, a short instrumental version, current streaming notes, and artist context.",
      "Would love to know if this is in range for the label.",
    ],
    timestamp: "12 min",
    sourceMailbox: "demos@cuevion.com",
    unread: true,
    priority: true,
    priorityBadge: "High-priority demo",
    reason: "Active demo with artist context and private audio link.",
  },
  {
    id: "bundle-demo-northline",
    kind: "demo",
    source: "sample",
    internalClassification: "demo",
    sender: "Northline Records",
    subject: "New artist demo for your A&R team",
    snippet:
      "Three unreleased tracks from a Berlin duo with recent Spotify editorial traction.",
    body: [
      "Sharing three unreleased tracks from a Berlin duo we are developing for Q3.",
      "The lead track sits between melodic house and indie dance, with a clear club arrangement.",
      "If there is interest, we can send WAVs and a clean one-sheet this week.",
    ],
    timestamp: "38 min",
    sourceMailbox: "info@cuevion.com",
    shortlisted: true,
    priority: true,
    priorityBadge: "Priority",
    reason: "Shortlisted demo with open follow-up.",
  },
  {
    id: "bundle-promo-riva",
    kind: "promo",
    source: "sample",
    internalClassification: "promo",
    sender: "Riva Promo Pool",
    subject: "Promo: Kaito Ray - Solar Drift",
    snippet:
      "Club mix, radio edit, and DJ feedback link available before Friday's release.",
    body: [
      "Kaito Ray returns with Solar Drift, a warm melodic club record scheduled for release this Friday.",
      "The promo pack includes the club mix, radio edit, WAV download, and DJ feedback link.",
      "Early support is coming from a small group of European selectors.",
    ],
    timestamp: "9 min",
    sourceMailbox: "promo@cuevion.com",
    unread: true,
    priority: true,
    priorityBadge: "Promo priority",
    reason: "Release deadline and feedback link detected.",
  },
  {
    id: "bundle-promo-labelworx",
    kind: "promo",
    source: "sample",
    internalClassification: "promo_reminder",
    sender: "LabelWorx Promos",
    subject: "Reminder: Maya Sol - Open Skies",
    snippet:
      "Promo reminder for pending feedback. Includes private stream and WAV download.",
    body: [
      "Quick reminder that Maya Sol - Open Skies is still open for feedback.",
      "The private stream and WAV download remain available in the promo portal.",
      "Feedback closes this Friday.",
    ],
    timestamp: "2h",
    sourceMailbox: "press@cuevion.com",
    shortlisted: true,
  },
  {
    id: "bundle-sent-decline",
    kind: "sent",
    source: "sample",
    sender: "Cuevion",
    subject: "Re: Demo submission - late night melodic house",
    snippet:
      "Thanks for sending this through. The production is strong, but it is not the right fit for the current release lane.",
    body: [
      "Thanks for sending this through. The production is strong, but it is not the right fit for the current release lane.",
      "Please keep us posted on future records. This was reviewed from the Organizer pilot shell.",
    ],
    timestamp: "Yesterday",
    sourceMailbox: "demos@cuevion.com",
    status: "sent",
  },
  {
    id: "bundle-trash-old-promo",
    kind: "promo",
    source: "sample",
    internalClassification: "promo_reminder",
    sender: "Archive Promo",
    subject: "Old campaign follow-up",
    snippet: "Archived promo reminder shown here as Organizer-local trash.",
    body: [
      "This static trash item shows the Organizer-local trash state.",
      "No real mail is moved, archived, deleted, or filtered in Bundle Pilot.",
    ],
    timestamp: "Last week",
    sourceMailbox: "promo@cuevion.com",
    status: "trashed",
  },
];

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
    emptyDescription: "Static sent activity will appear here in the bundle pilot shell.",
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

const demoClassifications = new Set<BundleOrganizerInternalClassification>([
  "demo",
  "high_priority_demo",
]);

const promoClassifications = new Set<BundleOrganizerInternalClassification>([
  "promo",
  "promo_reminder",
]);

const organizerActiveWorkTerms = [
  "waiting",
  "follow up",
  "follow-up",
  "replied",
  "reply",
  "feedback",
  "interested",
  "shortlist",
  "shortlisted",
  "stems",
  "label copy",
  "signing",
  "sign",
  "contract",
  "agreement",
  "approve",
  "approval",
  "move forward",
  "proceed",
  "next step",
  "can you",
  "please send",
  "let me know",
];

const organizerWorkflowSignals = [
  "active",
  "follow-up",
  "follow up",
  "timing",
  "for review",
  "shortlist",
  "shortlisted",
  "interested",
  "needs_action",
  "needs action",
  "needs_review",
  "needs review",
];

function getOrganizerActiveWorkReason(message: BundleOrganizerMessage) {
  if (message.internalClassification === "reply") {
    return "Reply-classified message.";
  }

  if (/^(re|fw|fwd):/i.test(message.subject.trim())) {
    return "Existing reply or forwarded thread.";
  }

  const workflowSignal = [message.signal, message.uiSignal, message.status]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  if (organizerWorkflowSignals.some((term) => workflowSignal.includes(term))) {
    return "Organizer workflow signal detected.";
  }

  const searchableText = [
    message.subject,
    message.snippet,
    message.sender,
    message.sourceMailbox,
    ...message.body,
  ]
    .join(" ")
    .toLowerCase();
  const matchedTerm = organizerActiveWorkTerms.find((term) =>
    searchableText.includes(term),
  );

  if (matchedTerm) {
    return `Open-loop signal: ${matchedTerm}.`;
  }

  return null;
}

function isOrganizerActiveWorkMessage(message: BundleOrganizerMessage) {
  return getOrganizerActiveWorkReason(message) !== null;
}

function resolveWorkspaceMessageKind(
  classification?: BundleOrganizerInternalClassification,
): BundleOrganizerMessage["kind"] {
  if (classification === "reply") {
    return "reply";
  }

  if (classification && promoClassifications.has(classification)) {
    return "promo";
  }

  return "demo";
}

function normalizeWorkspaceMessages(
  liveMessages: BundleOrganizerWorkspaceMessage[],
): BundleOrganizerMessage[] {
  return liveMessages
    .map((message) => ({
      ...message,
      id: `workspace-${message.id}`,
      kind: resolveWorkspaceMessageKind(message.internalClassification),
      source: "workspace" as const,
      priorityBadge:
        message.priorityBadge ??
        (message.internalClassification === "high_priority_demo"
          ? "High-priority demo"
          : message.priority
          ? "Priority"
          : undefined),
    }))
    .sort((first, second) => (second.sortTimestamp ?? 0) - (first.sortTimestamp ?? 0));
}

function getSampleMessagesForView(view: BundleOrganizerView) {
  if (view === "priority") {
    return bundleOrganizerMessages.filter((message) => message.priority && message.status !== "trashed");
  }

  if (view === "shortlist") {
    return bundleOrganizerMessages.filter((message) => message.shortlisted && message.status !== "trashed");
  }

  if (view === "demo") {
    return bundleOrganizerMessages.filter((message) => message.kind === "demo" && message.status !== "trashed");
  }

  if (view === "promo") {
    return bundleOrganizerMessages.filter((message) => message.kind === "promo" && message.status !== "trashed");
  }

  if (view === "sent") {
    return bundleOrganizerMessages.filter((message) => message.kind === "sent");
  }

  if (view === "trash") {
    return bundleOrganizerMessages.filter((message) => message.status === "trashed");
  }

  return [];
}

function getLiveMessagesForView(
  view: BundleOrganizerView,
  liveMessages: BundleOrganizerMessage[],
) {
  if (view === "priority") {
    return liveMessages.filter(isOrganizerActiveWorkMessage);
  }

  if (view === "demo") {
    return liveMessages.filter(
      (message) =>
        message.internalClassification != null &&
        demoClassifications.has(message.internalClassification),
    );
  }

  if (view === "promo") {
    return liveMessages.filter(
      (message) =>
        message.internalClassification != null &&
        promoClassifications.has(message.internalClassification),
    );
  }

  return [];
}

function getMessagesForView(
  view: BundleOrganizerView,
  liveMessages: BundleOrganizerMessage[],
) {
  const liveViewMessages = getLiveMessagesForView(view, liveMessages);

  if (liveViewMessages.length > 0) {
    return {
      messages: liveViewMessages,
      source: "workspace" as const,
    };
  }

  return {
    messages: getSampleMessagesForView(view),
    source: "sample" as const,
  };
}

function getCounts(liveMessages: BundleOrganizerMessage[]) {
  return navItems.reduce<Partial<Record<BundleOrganizerView, number>>>((counts, item) => {
    counts[item.id] = getMessagesForView(item.id, liveMessages).messages.length;
    return counts;
  }, {});
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
  connectedInboxCount = 0,
}: BundleOrganizerSurfaceProps) {
  const [activeView, setActiveView] = useState<BundleOrganizerView>("priority");
  const [searchQuery, setSearchQuery] = useState("");
  const [actionFeedback, setActionFeedback] = useState<string | null>(null);
  const [selectedMessage, setSelectedMessage] = useState<BundleOrganizerMessage | null>(null);

  const workspaceMessages = useMemo(
    () => normalizeWorkspaceMessages(liveMessages),
    [liveMessages],
  );
  const counts = useMemo(() => getCounts(workspaceMessages), [workspaceMessages]);
  const activeDisplay = useMemo(
    () => getMessagesForView(activeView, workspaceMessages),
    [activeView, workspaceMessages],
  );
  const activeMessages = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLowerCase();

    if (!normalizedQuery) {
      return activeDisplay.messages;
    }

    return activeDisplay.messages.filter((message) =>
      [
        message.sender,
        message.subject,
        message.snippet,
        message.sourceMailbox,
        ...message.body,
      ].some((value) => value.toLowerCase().includes(normalizedQuery)),
    );
  }, [activeDisplay, searchQuery]);
  const activeCopy = viewCopy[activeView];
  const activeSourceLabel =
    activeDisplay.source === "workspace" ? "Live workspace preview" : "Pilot sample data";
  const activeSourceDescription =
    activeDisplay.source === "workspace"
      ? "Focused Demo and Promo views are displaying read-only workspace messages."
      : "Focused Demo and Promo views are represented with pilot sample data.";
  const displayedConnectedInboxCount =
    activeDisplay.source === "workspace"
      ? connectedInboxCount
      : Math.max(2, connectedInboxCount);
  const smartViewCounts = useMemo(() => {
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
                {activeDisplay.source === "workspace"
                  ? "Live workspace preview for the embedded Organizer."
                  : "Pilot sample data for the embedded workspace preview."}
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
                <span>Smart Views</span>
                <button
                  type="button"
                  onClick={() => showStaticFeedback("Smart Views are static in Bundle Pilot.")}
                  className="inline-flex h-6 w-6 items-center justify-center rounded-full border border-[rgba(143,179,159,0.2)] bg-[rgba(143,179,159,0.1)] text-[0.86rem] leading-none text-[rgba(198,228,209,0.86)] transition-colors hover:bg-[rgba(143,179,159,0.16)]"
                  aria-label="Create Smart View"
                >
                  +
                </button>
              </div>
              {[
                { label: "High priority demos", count: smartViewCounts.highPriorityDemos },
                { label: "Promo reminders", count: smartViewCounts.promoReminders },
              ].map((smartView) => (
                <button
                  key={smartView.label}
                  type="button"
                  onClick={() => showStaticFeedback(`${smartView.label} is a static Bundle Pilot Smart View.`)}
                  className="mb-2 flex h-10 w-full shrink-0 items-center justify-between gap-2 rounded-full px-3.5 text-[0.82rem] font-medium text-[rgba(245,239,229,0.72)] transition-colors hover:bg-white/5"
                >
                  <span className="truncate">{smartView.label}</span>
                  <span className="rounded-full bg-white/5 px-2 py-0.5 text-[0.72rem] text-[rgba(245,239,229,0.52)]">
                    {smartView.count}
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
                    ? "Organizer-sent replies and declines across pilot sample messages."
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
                              ? ["All promos", "Reminders", "Unread"].map((filter) => (
                                  <button
                                    key={filter}
                                    type="button"
                                    onClick={() => showStaticFeedback(`${filter} is a static preview filter.`)}
                                    className="inline-flex h-9 items-center justify-center rounded-full border border-white/10 bg-white/5 px-3.5 text-[0.74rem] font-medium uppercase tracking-[0.1em] text-[rgba(245,239,229,0.62)] transition-colors hover:border-[rgba(143,179,159,0.24)] hover:bg-[rgba(143,179,159,0.1)] hover:text-[rgba(198,228,209,0.84)]"
                                  >
                                    {filter}
                                  </button>
                                ))
                              : null}
                            <button
                              type="button"
                              onClick={() => showStaticFeedback("Source inbox filtering is static in this preview.")}
                              className="inline-flex h-9 items-center justify-center rounded-full border border-white/10 bg-white/5 px-3.5 text-[0.74rem] font-medium uppercase tracking-[0.1em] text-[rgba(245,239,229,0.62)] transition-colors hover:border-[rgba(143,179,159,0.24)] hover:bg-[rgba(143,179,159,0.1)] hover:text-[rgba(198,228,209,0.84)]"
                            >
                              All inboxes
                            </button>
                          </div>
                          <div className="flex flex-wrap items-center gap-2">
                            <button
                              type="button"
                              onClick={() => showStaticFeedback("Status filtering is static in this preview.")}
                              className="inline-flex h-9 items-center justify-center rounded-full border border-white/10 bg-white/5 px-3.5 text-[0.74rem] font-medium uppercase tracking-[0.1em] text-[rgba(245,239,229,0.62)] transition-colors hover:border-[rgba(143,179,159,0.24)] hover:bg-[rgba(143,179,159,0.1)] hover:text-[rgba(198,228,209,0.84)]"
                            >
                              All status
                            </button>
                            <button
                              type="button"
                              onClick={() => showStaticFeedback("Sorting is static in this preview.")}
                              className="inline-flex h-9 items-center justify-center rounded-full border border-white/10 bg-white/5 px-3.5 text-[0.74rem] font-medium uppercase tracking-[0.1em] text-[rgba(245,239,229,0.62)] transition-colors hover:border-[rgba(143,179,159,0.24)] hover:bg-[rgba(143,179,159,0.1)] hover:text-[rgba(198,228,209,0.84)]"
                            >
                              Newest first
                            </button>
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
                            {activeCopy.emptyTitle}
                          </h3>
                          <p className="mx-auto mt-2 max-w-[460px] text-[0.86rem] leading-6 text-[rgba(245,239,229,0.58)]">
                            {activeCopy.emptyDescription}
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
                            {activeView === "priority" && getOrganizerActiveWorkReason(message) ? (
                              <p className="mt-2 text-[0.76rem] font-medium uppercase tracking-[0.12em] text-[rgba(143,179,159,0.78)]">
                                {getOrganizerActiveWorkReason(message)}
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
  const activeReason = getOrganizerActiveWorkReason(message) ?? message.reason;
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
          eyebrow: "Organizer rules",
          title: "Smart Views and routing",
          description:
            "Smart Views are shown here as static pilot structure. No classifier, filtering, or mailbox routing changes are active.",
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
