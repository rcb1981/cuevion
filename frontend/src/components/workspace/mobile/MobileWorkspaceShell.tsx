import { useEffect, useMemo, useState } from "react";

export type MobileWorkspaceMessage = {
  id: string;
  mailboxId: string;
  mailboxTitle: string;
  sender: string;
  from: string;
  subject: string;
  snippet: string;
  time: string;
  timestamp: string;
  body: string[];
  unread?: boolean;
  badge?: string | null;
};

export type MobileWorkspaceMailbox = {
  id: string;
  title: string;
  email: string;
  detail: string;
  connected: boolean;
  syncError?: string | null;
  /** Transient refresh diagnostic set by the onSyncMailbox callback in WorkspaceShell.
   *  Shows the result of the last explicit per-mailbox refresh. */
  refreshStatus?: string | null;
  cachedMessageCount?: number;
  messages: MobileWorkspaceMessage[];
};

type MobileTab = "priority" | "inboxes" | "settings";
type MobileMessageComposeMode = "reply" | "reply_all" | "forward";
type MobileView =
  | { kind: "root" }
  | { kind: "mailbox"; mailboxId: string }
  | { kind: "message"; message: MobileWorkspaceMessage; backView: MobileView };

type MobileComposeState = {
  isOpen: boolean;
  to: string;
  subject: string;
  mailboxEmail: string;
  isSending: boolean;
  sendError: string | null;
};

type MobileWorkspaceShellProps = {
  themeMode: "light" | "dark";
  accountName: string;
  accountEmail: string;
  connectedInboxCount: number;
  syncFeedbackMessage?: string | null;
  syncingMailboxId?: string | null;
  mailboxes: MobileWorkspaceMailbox[];
  priorityMessages: MobileWorkspaceMessage[];
  onLogoutClick: () => void;
  /** Optional manual refresh callback for a mailbox. Opening a mailbox itself
   *  stays snapshot-first on mobile so navigation is stable when refresh fails. */
  onSyncMailbox?: (mailboxId: string) => void | Promise<void>;
  /** Called when the user taps Compose for a mailbox. Host opens compose state. */
  onComposeMailbox?: (mailboxId: string) => void;
  /** Called when the user taps a message compose action. Host opens compose state. */
  onComposeMessage?: (
    mailboxId: string,
    messageId: string,
    mode: MobileMessageComposeMode,
  ) => void;
  /** Current compose state to show the mobile compose overlay. Null when closed. */
  mobileCompose?: MobileComposeState | null;
  /** Called with the user's plain-text reply when they tap Send. */
  onMobileComposeSend?: (userReplyText: string) => void;
  /** Called when the user dismisses the compose overlay. */
  onMobileComposeClose?: () => void;
  /** Navigation context to restore on mount after returning from a mobile compose
   *  flow. WorkspaceShell sets this when a compose action fires so the user lands
   *  back in the same inbox/message view instead of Priority after closing compose.
   *  Only consumed once via the useState initialiser — no useEffect needed. */
  mobileNavRestoreContext?: {
    tab: "priority" | "inboxes" | "settings";
    mailboxId?: string;
    messageId?: string;
  } | null;
};

function formatMessageBody(message: MobileWorkspaceMessage) {
  const bodyLines = message.body.filter((line) => line.trim().length > 0);
  return bodyLines.length > 0 ? bodyLines : [message.snippet || "No preview available."];
}

function MobileMark() {
  return (
    <div className="flex items-center gap-3">
      <span className="flex h-9 w-9 items-center justify-center rounded-full border border-[color:rgba(244,224,183,0.36)] bg-[color:rgba(255,250,239,0.12)] shadow-[0_12px_30px_rgba(19,44,34,0.2)]">
        <span className="h-2.5 w-2.5 rounded-full bg-[color:#e7c783]" />
      </span>
      <span className="text-[1.02rem] font-semibold tracking-normal text-[color:#fff8ec]">
        Cuevion
      </span>
    </div>
  );
}

function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="flex min-h-[42dvh] flex-col items-center justify-center px-8 text-center">
      <div className="rounded-full border border-[color:rgba(55,96,78,0.18)] bg-[color:rgba(255,250,241,0.72)] px-4 py-1.5 text-[0.72rem] font-medium uppercase tracking-[0.14em] text-[color:rgba(49,92,75,0.82)] dark:border-[color:rgba(215,190,146,0.16)] dark:bg-[color:rgba(255,247,232,0.08)] dark:text-[color:rgba(232,211,174,0.82)]">
        Cuevion
      </div>
      <div className="mt-4 text-[1.05rem] font-semibold tracking-normal text-[var(--workspace-text)]">
        {title}
      </div>
      <div className="mt-2 max-w-[18rem] text-[0.88rem] leading-6 text-[var(--workspace-text-soft)]">
        {detail}
      </div>
    </div>
  );
}

function MessageRow({
  message,
  onOpen,
}: {
  message: MobileWorkspaceMessage;
  onOpen: () => void;
}) {
  const badgeTone = message.badge?.toLowerCase() ?? "";
  const badgeClassName = badgeTone.includes("priority")
    ? "border-[color:rgba(46,112,82,0.24)] bg-[color:rgba(69,130,96,0.12)] text-[color:rgba(35,92,65,0.92)] dark:border-[color:rgba(129,191,153,0.24)] dark:bg-[color:rgba(91,145,109,0.18)] dark:text-[color:rgba(184,225,197,0.9)]"
    : "border-[color:rgba(159,124,66,0.2)] bg-[color:rgba(214,179,114,0.14)] text-[color:rgba(109,84,43,0.86)] dark:border-[color:rgba(218,190,138,0.22)] dark:bg-[color:rgba(214,179,114,0.1)] dark:text-[color:rgba(232,211,174,0.86)]";
  const unreadAttentionDotClass =
    "h-2 w-2 rounded-full bg-[#4E2070] shadow-[0_0_0_2px_rgba(78,32,112,0.08)]";

  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex w-full border-b border-[color:rgba(86,69,46,0.1)] bg-[color:rgba(255,253,248,0.74)] px-4 py-4 text-left transition-colors duration-150 active:bg-[color:rgba(232,219,199,0.72)] dark:border-[color:rgba(232,211,174,0.1)] dark:bg-[color:rgba(28,25,21,0.78)] dark:active:bg-[color:rgba(55,47,39,0.8)]"
    >
      <span className="min-w-0 flex-1">
        <span className="flex min-w-0 items-center justify-between gap-3">
          <span className="flex min-w-0 items-center gap-2">
            {message.unread ? (
              <span aria-hidden="true" className={`${unreadAttentionDotClass} shrink-0`} />
            ) : null}
            <span
              className={`truncate text-[0.95rem] tracking-normal ${
                message.unread
                  ? "font-semibold text-[var(--workspace-text)]"
                  : "font-medium text-[var(--workspace-text-soft)]"
              }`}
            >
              {message.sender || message.from}
            </span>
          </span>
          <span className="shrink-0 text-[0.72rem] text-[var(--workspace-text-faint)]">
            {message.time || message.timestamp}
          </span>
        </span>
        <span
          className={`mt-0.5 block truncate text-[0.92rem] ${
            message.unread
              ? "font-semibold text-[var(--workspace-text)]"
              : "font-medium text-[var(--workspace-text-soft)]"
          }`}
        >
          {message.subject || "No subject"}
        </span>
        <span className="mt-1 block line-clamp-2 text-[0.82rem] leading-5 text-[var(--workspace-text-faint)]">
          {message.snippet}
        </span>
        <span className="mt-2 flex items-center gap-2">
          <span className="truncate text-[0.72rem] text-[var(--workspace-text-faint)]">
            {message.mailboxTitle}
          </span>
          {message.badge ? (
            <span className={`rounded-full border px-2 py-0.5 text-[0.64rem] font-semibold uppercase tracking-[0.12em] ${badgeClassName}`}>
              {message.badge}
            </span>
          ) : null}
        </span>
      </span>
    </button>
  );
}

function MessageList({
  messages,
  emptyTitle,
  emptyDetail,
  onOpenMessage,
}: {
  messages: MobileWorkspaceMessage[];
  emptyTitle: string;
  emptyDetail: string;
  onOpenMessage: (message: MobileWorkspaceMessage) => void;
}) {
  if (messages.length === 0) {
    return <EmptyState title={emptyTitle} detail={emptyDetail} />;
  }

  return (
    <div className="overflow-hidden border-y border-[color:rgba(86,69,46,0.08)] bg-[color:rgba(255,251,244,0.5)] dark:border-[color:rgba(232,211,174,0.08)] dark:bg-[color:rgba(19,17,15,0.72)]">
      {messages.map((message) => (
        <MessageRow
          key={`${message.mailboxId}-${message.id}`}
          message={message}
          onOpen={() => onOpenMessage(message)}
        />
      ))}
    </div>
  );
}

export function MobileWorkspaceShell({
  themeMode,
  accountName,
  accountEmail,
  connectedInboxCount,
  syncFeedbackMessage,
  syncingMailboxId,
  mailboxes,
  priorityMessages,
  onLogoutClick,
  onSyncMailbox,
  onComposeMailbox,
  onComposeMessage,
  mobileCompose,
  onMobileComposeSend,
  onMobileComposeClose,
  mobileNavRestoreContext,
}: MobileWorkspaceShellProps) {
  // Seed tab/view from mobileNavRestoreContext when provided (post-compose return).
  // Using the useState initialiser (not a useEffect) avoids a Priority flash on
  // mount — the initialiser is synchronous and runs before the first paint.
  const [activeTab, setActiveTab] = useState<MobileTab>(() =>
    mobileNavRestoreContext?.tab ?? "priority",
  );
  const [view, setView] = useState<MobileView>(() => {
    const ctx = mobileNavRestoreContext;
    if (!ctx?.mailboxId) return { kind: "root" };
    // Safety: validate that the restored mailboxId actually exists in the
    // current mailboxes prop. If it is absent (stale/invalid context) fall back
    // to the root inboxes list rather than hanging on an empty mailbox view.
    if (!mailboxes.some((m) => m.id === ctx.mailboxId)) return { kind: "root" };
    const mailboxView: MobileView = { kind: "mailbox", mailboxId: ctx.mailboxId };
    if (ctx.messageId) {
      const mb = mailboxes.find((m) => m.id === ctx.mailboxId);
      const msg = mb?.messages.find((m) => m.id === ctx.messageId);
      if (msg) {
        // Restore message detail; Back navigates to the mailbox list.
        return { kind: "message", message: msg, backView: mailboxView };
      }
    }
    // Message not found (e.g. after send) — fall back to mailbox list.
    return mailboxView;
  });
  const [replyText, setReplyText] = useState("");

  // Reset reply textarea whenever the compose overlay closes
  useEffect(() => {
    if (!mobileCompose?.isOpen) {
      setReplyText("");
    }
  }, [mobileCompose?.isOpen]);

  const connectedFirstMailboxes = useMemo(
    () => [...mailboxes].sort((first, second) => Number(second.connected) - Number(first.connected)),
    [mailboxes],
  );
  const activeMailbox =
    view.kind === "mailbox"
      ? mailboxes.find((mailbox) => mailbox.id === view.mailboxId) ?? null
      : null;
  const mobileComposeMailbox =
    activeMailbox ??
    (activeTab === "inboxes"
      ? connectedFirstMailboxes.find((mailbox) => mailbox.connected) ?? null
      : null);
  const isActiveMailboxSyncing =
    activeMailbox !== null && syncingMailboxId === activeMailbox.id;

  const openTab = (tab: MobileTab) => {
    setActiveTab(tab);
    setView({ kind: "root" });
  };

  const openMessage = (message: MobileWorkspaceMessage) => {
    setView({ kind: "message", message, backView: view });
  };

  const headerTitle =
    view.kind === "message"
      ? "Message"
      : view.kind === "mailbox"
        ? activeMailbox?.title ?? "Inbox"
        : activeTab === "priority"
          ? "Priority"
          : activeTab === "inboxes"
            ? "Inboxes"
            : "Settings";

  return (
    <main
      data-theme={themeMode}
      className="box-border flex h-dvh flex-col overflow-hidden bg-[linear-gradient(180deg,#f7efe4_0%,#efe4d6_100%)] text-[var(--workspace-text)] dark:bg-[linear-gradient(180deg,#171411_0%,#221c17_100%)]"
      style={{ colorScheme: themeMode }}
    >
      <header className="shrink-0 border-b border-[color:rgba(244,224,183,0.22)] bg-[linear-gradient(180deg,#28473c_0%,#1f352e_100%)] px-4 pb-3 pt-[calc(env(safe-area-inset-top)+0.75rem)] shadow-[0_14px_40px_rgba(31,53,46,0.18)] dark:border-[color:rgba(244,224,183,0.14)] dark:bg-[linear-gradient(180deg,#20372f_0%,#172720_100%)]">
        <div className="flex min-h-10 items-center justify-between gap-3">
          {view.kind === "root" ? (
            <MobileMark />
          ) : (
            <button
              type="button"
              onClick={() =>
                setView(view.kind === "message" ? view.backView : { kind: "root" })
              }
              className="rounded-full px-1 py-2 text-[0.92rem] font-medium text-[color:#fff8ec]"
            >
              Back
            </button>
          )}
          <div className="min-w-0 flex-1 text-center text-[1rem] font-semibold tracking-normal text-[color:#fff8ec]">
            {headerTitle}
          </div>
          {mobileComposeMailbox ? (
            <div className="flex items-center gap-2">
              <button
                type="button"
                aria-label="Compose email"
                disabled={!mobileComposeMailbox.connected || !onComposeMailbox}
                onClick={() => onComposeMailbox?.(mobileComposeMailbox.id)}
                className="flex h-10 w-10 items-center justify-center rounded-full border border-[color:rgba(232,211,174,0.28)] bg-[color:rgba(255,250,239,0.13)] text-[color:#fff8ec] disabled:cursor-not-allowed disabled:opacity-55"
              >
                <svg
                  aria-hidden="true"
                  viewBox="0 0 16 16"
                  className="h-4 w-4"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M8 3v10" />
                  <path d="M3 8h10" />
                </svg>
              </button>
              {view.kind === "mailbox" && activeMailbox ? (
                <button
                  type="button"
                  aria-label={isActiveMailboxSyncing ? "Syncing inbox" : "Sync inbox"}
                  disabled={!activeMailbox.connected || isActiveMailboxSyncing || !onSyncMailbox}
                  onClick={() => {
                    void onSyncMailbox?.(activeMailbox.id);
                  }}
                  className="flex h-10 w-10 items-center justify-center rounded-full border border-[color:rgba(232,211,174,0.28)] bg-[color:rgba(255,250,239,0.13)] text-[color:#fff8ec] disabled:cursor-not-allowed disabled:opacity-55"
                >
                  <svg
                    aria-hidden="true"
                    viewBox="0 0 16 16"
                    className={`h-4 w-4 ${isActiveMailboxSyncing ? "animate-spin [animation-direction:reverse]" : ""}`}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.7"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M13 5.5A5 5 0 0 0 4.5 3L3 4.5" />
                    <path d="M3.5 2.5v2h2" />
                    <path d="M3 10.5A5 5 0 0 0 11.5 13L13 11.5" />
                    <path d="M12.5 13.5v-2h-2" />
                  </svg>
                </button>
              ) : null}
            </div>
          ) : (
            <div className="w-12" />
          )}
        </div>
        {/* syncFeedbackMessage suppressed on mobile — per-mailbox status shown on inbox cards */}
      </header>

      <section className="min-h-0 flex-1 overflow-y-auto">
        {view.kind === "message" ? (
          <article className="min-h-full bg-[color:rgba(255,253,248,0.78)] px-5 py-5 dark:bg-[color:rgba(23,20,17,0.82)]">
            <div className="space-y-2 border-b border-[color:rgba(86,69,46,0.12)] pb-4 dark:border-[color:rgba(232,211,174,0.1)]">
              <div className="text-[0.82rem] font-medium text-[var(--workspace-text-soft)]">
                {view.message.sender || view.message.from}
              </div>
              <h1 className="text-[1.35rem] font-semibold leading-8 tracking-normal text-[var(--workspace-text)]">
                {view.message.subject || "No subject"}
              </h1>
              <div className="flex flex-wrap items-center gap-2 text-[0.78rem] text-[var(--workspace-text-faint)]">
                <span>{view.message.from}</span>
                <span>{view.message.timestamp || view.message.time}</span>
                {view.message.badge ? (
                  <span className="rounded-full border border-[color:rgba(46,112,82,0.22)] bg-[color:rgba(69,130,96,0.12)] px-2 py-0.5 text-[0.64rem] font-semibold uppercase tracking-[0.12em] text-[color:rgba(35,92,65,0.9)] dark:border-[color:rgba(129,191,153,0.24)] dark:bg-[color:rgba(91,145,109,0.18)] dark:text-[color:rgba(184,225,197,0.9)]">
                    {view.message.badge}
                  </span>
                ) : null}
              </div>
            </div>
            <div className="space-y-4 py-5 text-[0.96rem] leading-7 text-[var(--workspace-text-soft)]">
              {formatMessageBody(view.message).map((line, index) => (
                <p key={`${view.message.id}-body-${index}`}>{line}</p>
              ))}
            </div>
            {onComposeMessage ? (
              <div className="flex items-center gap-2 border-t border-[color:rgba(86,69,46,0.1)] pt-4 dark:border-[color:rgba(232,211,174,0.1)]">
                <button
                  type="button"
                  aria-label="Reply"
                  onClick={() =>
                    onComposeMessage(view.message.mailboxId, view.message.id, "reply")
                  }
                  className="flex h-11 w-11 items-center justify-center rounded-full border border-[color:rgba(47,96,73,0.3)] bg-[linear-gradient(180deg,#3f7659_0%,#2f6049_100%)] text-[color:#fff8ec] shadow-[0_8px_20px_rgba(47,96,73,0.22)] active:opacity-80"
                >
                  <svg
                    aria-hidden="true"
                    viewBox="0 0 16 16"
                    className="h-4 w-4"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M6 4 2.5 8 6 12" />
                    <path d="M3 8h7c2.3 0 4 1.2 4 3.5" />
                  </svg>
                </button>
                <button
                  type="button"
                  aria-label="Reply all"
                  onClick={() =>
                    onComposeMessage(view.message.mailboxId, view.message.id, "reply_all")
                  }
                  className="flex h-11 w-11 items-center justify-center rounded-full border border-[color:rgba(47,96,73,0.3)] bg-[linear-gradient(180deg,#3f7659_0%,#2f6049_100%)] text-[color:#fff8ec] shadow-[0_8px_20px_rgba(47,96,73,0.22)] active:opacity-80"
                >
                  <svg
                    aria-hidden="true"
                    viewBox="0 0 16 16"
                    className="h-4 w-4"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.75"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M5.75 4 2.5 8l3.25 4" />
                    <path d="M10.75 4 7.5 8l3.25 4" />
                    <path d="M3 8h7c2.1 0 3.75 1.1 3.75 3.3" />
                  </svg>
                </button>
                <button
                  type="button"
                  aria-label="Forward"
                  onClick={() =>
                    onComposeMessage(view.message.mailboxId, view.message.id, "forward")
                  }
                  className="flex h-11 w-11 items-center justify-center rounded-full border border-[color:rgba(47,96,73,0.3)] bg-[linear-gradient(180deg,#3f7659_0%,#2f6049_100%)] text-[color:#fff8ec] shadow-[0_8px_20px_rgba(47,96,73,0.22)] active:opacity-80"
                >
                  <svg
                    aria-hidden="true"
                    viewBox="0 0 16 16"
                    className="h-4 w-4"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M10 4 13.5 8 10 12" />
                    <path d="M13 8H6.5C4.2 8 2.5 9.2 2.5 11.5" />
                  </svg>
                </button>
              </div>
            ) : null}
          </article>
        ) : activeTab === "priority" ? (
          <MessageList
            messages={priorityMessages}
            emptyTitle="No priority mail yet"
            emptyDetail="Messages Cuevion marks as important will appear here."
            onOpenMessage={openMessage}
          />
        ) : activeTab === "inboxes" ? (
          view.kind === "mailbox" && activeMailbox ? (
            <>
              {activeMailbox.refreshStatus ? (
                <div className="border-b border-[color:rgba(86,69,46,0.08)] bg-[color:rgba(255,250,239,0.6)] px-5 py-2 text-[0.72rem] text-[color:rgba(49,92,75,0.82)] dark:border-[color:rgba(232,211,174,0.08)] dark:bg-[color:rgba(19,17,15,0.5)] dark:text-[color:rgba(184,225,197,0.82)] whitespace-pre-wrap break-all">
                  {activeMailbox.refreshStatus}
                </div>
              ) : null}
              <MessageList
                messages={activeMailbox.messages}
                emptyTitle="No visible messages"
                emptyDetail={
                  activeMailbox.syncError ??
                  "Filtered, quiet, archived, and spam messages stay out of this mobile inbox."
                }
                onOpenMessage={openMessage}
              />
            </>
          ) : (
            <div className="overflow-hidden border-y border-[color:rgba(86,69,46,0.08)] bg-[color:rgba(255,251,244,0.5)] dark:border-[color:rgba(232,211,174,0.08)] dark:bg-[color:rgba(19,17,15,0.72)]">
              {connectedFirstMailboxes.map((mailbox) => {
                const unreadCount = mailbox.messages.filter((message) => message.unread).length;
                const visibleMessageLabel =
                  mailbox.syncError &&
                  mailbox.messages.length === 0 &&
                  (mailbox.cachedMessageCount ?? 0) > 0
                    ? "Existing messages kept"
                    : mailbox.messages.length === 1
                      ? "1 visible message"
                      : `${mailbox.messages.length} visible messages`;

                return (
                  <button
                    key={mailbox.id}
                    type="button"
                    onClick={() => {
                      setView({ kind: "mailbox", mailboxId: mailbox.id });
                    }}
                    className="flex w-full items-center justify-between gap-4 border-b border-[color:rgba(86,69,46,0.1)] bg-[color:rgba(255,253,248,0.74)] px-5 py-4 text-left active:bg-[color:rgba(232,219,199,0.72)] dark:border-[color:rgba(232,211,174,0.1)] dark:bg-[color:rgba(28,25,21,0.78)] dark:active:bg-[color:rgba(55,47,39,0.8)]"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[1rem] font-semibold tracking-normal text-[var(--workspace-text)]">
                        {mailbox.title}
                      </span>
                      <span className="mt-1 block truncate text-[0.8rem] text-[var(--workspace-text-faint)]">
                        {mailbox.email}
                      </span>
                      {mailbox.connected ? (
                        <span className="mt-0.5 block truncate text-[0.74rem] text-[var(--workspace-text-faint)]">
                          {visibleMessageLabel}
                        </span>
                      ) : null}
                      {/* syncError is intentionally omitted from list cards — background
                          refresh failures should not persist as a permanent orange warning
                          on every inbox row. The refreshStatus (auto-dismissed after a few
                          seconds) conveys the result of a user-triggered sync instead. */}
                      {mailbox.refreshStatus ? (
                        <span className="mt-0.5 block truncate text-[0.68rem] text-[color:rgba(49,92,75,0.76)] dark:text-[color:rgba(184,225,197,0.76)]">
                          {mailbox.refreshStatus}
                        </span>
                      ) : null}
                    </span>
                    {unreadCount > 0 ? (
                      <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-[color:rgba(78,32,112,0.18)] bg-[color:rgba(78,32,112,0.06)] px-2.5 py-1 text-[0.72rem] font-semibold text-[color:#4E2070] dark:border-[color:rgba(184,144,205,0.22)] dark:bg-[color:rgba(78,32,112,0.16)] dark:text-[color:rgba(221,190,236,0.92)]">
                        <span
                          aria-hidden="true"
                          className="h-2 w-2 rounded-full bg-[#4E2070] shadow-[0_0_0_2px_rgba(78,32,112,0.08)] dark:bg-[color:rgba(221,190,236,0.92)]"
                        />
                        {unreadCount} unread
                      </span>
                    ) : mailbox.connected ? null : (
                      <span className="shrink-0 rounded-full border border-[color:rgba(159,124,66,0.16)] bg-[color:rgba(214,179,114,0.1)] px-2.5 py-1 text-[0.72rem] font-semibold text-[color:rgba(109,84,43,0.72)] dark:border-[color:rgba(218,190,138,0.2)] dark:bg-[color:rgba(214,179,114,0.08)] dark:text-[color:rgba(232,211,174,0.72)]">
                        Pending
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          )
        ) : (
          <div className="space-y-4 px-5 py-5">
            <section className="rounded-[18px] border border-[color:rgba(86,69,46,0.1)] bg-[color:rgba(255,253,248,0.74)] p-4 shadow-[0_16px_34px_rgba(66,48,27,0.06)] dark:border-[color:rgba(232,211,174,0.1)] dark:bg-[color:rgba(28,25,21,0.78)]">
              <div className="text-[0.72rem] font-semibold uppercase tracking-[0.16em] text-[color:rgba(49,92,75,0.72)] dark:text-[color:rgba(184,225,197,0.76)]">
                Account
              </div>
              <div className="mt-3 text-[1rem] font-semibold text-[var(--workspace-text)]">
                {accountName}
              </div>
              <div className="mt-1 truncate text-[0.84rem] text-[var(--workspace-text-soft)]">
                {accountEmail}
              </div>
            </section>
            <section className="rounded-[18px] border border-[color:rgba(86,69,46,0.1)] bg-[color:rgba(255,253,248,0.74)] p-4 shadow-[0_16px_34px_rgba(66,48,27,0.06)] dark:border-[color:rgba(232,211,174,0.1)] dark:bg-[color:rgba(28,25,21,0.78)]">
              <div className="flex items-center justify-between gap-4">
                <span className="text-[0.9rem] text-[var(--workspace-text-soft)]">
                  Connected inboxes
                </span>
                <span className="rounded-full border border-[color:rgba(46,112,82,0.18)] bg-[color:rgba(69,130,96,0.1)] px-2.5 py-1 text-[0.86rem] font-semibold text-[color:rgba(35,92,65,0.9)] dark:border-[color:rgba(129,191,153,0.22)] dark:bg-[color:rgba(91,145,109,0.16)] dark:text-[color:rgba(184,225,197,0.9)]">
                  {connectedInboxCount}
                </span>
              </div>
              {/* syncFeedbackMessage suppressed on mobile */}
            </section>
            <button
              type="button"
              onClick={onLogoutClick}
              className="w-full rounded-[16px] border border-[color:rgba(86,69,46,0.1)] bg-[color:rgba(255,253,248,0.74)] px-4 py-3 text-[0.9rem] font-semibold text-[var(--workspace-text)] shadow-[0_16px_34px_rgba(66,48,27,0.06)] active:bg-[color:rgba(232,219,199,0.72)] dark:border-[color:rgba(232,211,174,0.1)] dark:bg-[color:rgba(28,25,21,0.78)]"
            >
              Log out
            </button>
          </div>
        )}
      </section>

      <nav className="shrink-0 border-t border-[color:rgba(86,69,46,0.12)] bg-[color:rgba(255,250,241,0.9)] px-3 pb-[calc(env(safe-area-inset-bottom)+0.5rem)] pt-2 shadow-[0_-16px_34px_rgba(66,48,27,0.08)] backdrop-blur-xl dark:border-[color:rgba(232,211,174,0.1)] dark:bg-[color:rgba(27,23,19,0.9)]">
        <div className="grid grid-cols-3 gap-2">
          {([
            ["priority", "Priority"],
            ["inboxes", "Inboxes"],
            ["settings", "Settings"],
          ] as const).map(([tab, label]) => (
            <button
              key={tab}
              type="button"
              onClick={() => openTab(tab)}
              className={`rounded-[14px] px-3 py-2 text-[0.78rem] font-medium ${
                activeTab === tab && view.kind === "root"
                  ? "bg-[linear-gradient(180deg,#3f7659_0%,#2f6049_100%)] text-[color:#fff8ec] shadow-[0_10px_24px_rgba(47,96,73,0.22)]"
                  : "text-[var(--workspace-text-soft)]"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </nav>

      {/* Mobile compose overlay — shown when a reply is in progress */}
      {mobileCompose?.isOpen ? (
        <div
          data-theme={themeMode}
          className="fixed inset-0 z-[80] flex flex-col bg-[linear-gradient(180deg,#f7efe4_0%,#efe4d6_100%)] dark:bg-[linear-gradient(180deg,#171411_0%,#221c17_100%)]"
          style={{ colorScheme: themeMode }}
        >
          {/* Compose header */}
          <div className="shrink-0 border-b border-[color:rgba(244,224,183,0.22)] bg-[linear-gradient(180deg,#28473c_0%,#1f352e_100%)] px-4 pb-3 pt-[calc(env(safe-area-inset-top)+0.75rem)] shadow-[0_14px_40px_rgba(31,53,46,0.18)] dark:border-[color:rgba(244,224,183,0.14)] dark:bg-[linear-gradient(180deg,#20372f_0%,#172720_100%)]">
            <div className="flex min-h-10 items-center justify-between gap-3">
              <button
                type="button"
                onClick={onMobileComposeClose}
                className="rounded-full px-1 py-2 text-[0.92rem] font-medium text-[color:#fff8ec]"
              >
                Cancel
              </button>
              <div className="min-w-0 flex-1 text-center text-[1rem] font-semibold tracking-normal text-[color:#fff8ec]">
                Reply
              </div>
              <button
                type="button"
                disabled={mobileCompose.isSending || replyText.trim().length === 0}
                onClick={() => onMobileComposeSend?.(replyText)}
                className="rounded-full px-3 py-2 text-[0.92rem] font-semibold text-[color:#e7c783] disabled:opacity-40"
              >
                {mobileCompose.isSending ? "Sending…" : "Send"}
              </button>
            </div>
          </div>

          {/* Compose meta */}
          <div className="shrink-0 space-y-0 border-b border-[color:rgba(86,69,46,0.1)] bg-[color:rgba(255,253,248,0.78)] dark:border-[color:rgba(232,211,174,0.1)] dark:bg-[color:rgba(23,20,17,0.82)]">
            <div className="flex items-baseline gap-2 border-b border-[color:rgba(86,69,46,0.06)] px-5 py-3 dark:border-[color:rgba(232,211,174,0.06)]">
              <span className="w-16 shrink-0 text-[0.76rem] font-medium uppercase tracking-[0.12em] text-[var(--workspace-text-faint)]">
                From
              </span>
              <span className="min-w-0 truncate text-[0.88rem] text-[var(--workspace-text-soft)]">
                {mobileCompose.mailboxEmail}
              </span>
            </div>
            <div className="flex items-baseline gap-2 border-b border-[color:rgba(86,69,46,0.06)] px-5 py-3 dark:border-[color:rgba(232,211,174,0.06)]">
              <span className="w-16 shrink-0 text-[0.76rem] font-medium uppercase tracking-[0.12em] text-[var(--workspace-text-faint)]">
                To
              </span>
              <span className="min-w-0 truncate text-[0.88rem] text-[var(--workspace-text-soft)]">
                {mobileCompose.to}
              </span>
            </div>
            <div className="flex items-baseline gap-2 px-5 py-3">
              <span className="w-16 shrink-0 text-[0.76rem] font-medium uppercase tracking-[0.12em] text-[var(--workspace-text-faint)]">
                Subject
              </span>
              <span className="min-w-0 truncate text-[0.88rem] text-[var(--workspace-text-soft)]">
                {mobileCompose.subject}
              </span>
            </div>
          </div>

          {/* Compose body */}
          <div className="min-h-0 flex-1 overflow-y-auto bg-[color:rgba(255,253,248,0.78)] dark:bg-[color:rgba(23,20,17,0.82)]">
            <textarea
              autoFocus
              value={replyText}
              onChange={(e) => setReplyText(e.target.value)}
              placeholder="Write your reply…"
              className="h-full min-h-[12rem] w-full resize-none bg-transparent px-5 py-5 text-[0.96rem] leading-7 text-[var(--workspace-text)] placeholder:text-[var(--workspace-text-faint)] focus:outline-none"
            />
          </div>

          {/* Send error */}
          {mobileCompose.sendError ? (
            <div className="shrink-0 border-t border-[color:rgba(143,82,48,0.2)] bg-[color:rgba(255,248,243,0.9)] px-5 py-3 text-[0.82rem] text-[color:rgba(143,82,48,0.92)] dark:border-[color:rgba(235,174,138,0.2)] dark:bg-[color:rgba(42,24,15,0.9)] dark:text-[color:rgba(235,174,138,0.86)]">
              {mobileCompose.sendError}
            </div>
          ) : null}
        </div>
      ) : null}
    </main>
  );
}
