import { useMemo, useState } from "react";

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
  messages: MobileWorkspaceMessage[];
};

type MobileTab = "priority" | "inboxes" | "settings";
type MobileView =
  | { kind: "root" }
  | { kind: "mailbox"; mailboxId: string }
  | { kind: "message"; message: MobileWorkspaceMessage; backView: MobileView };

type MobileWorkspaceShellProps = {
  themeMode: "light" | "dark";
  accountName: string;
  accountEmail: string;
  connectedInboxCount: number;
  syncFeedbackMessage?: string | null;
  mailboxes: MobileWorkspaceMailbox[];
  priorityMessages: MobileWorkspaceMessage[];
  onLogoutClick: () => void;
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

  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex w-full gap-3 border-b border-[color:rgba(86,69,46,0.1)] bg-[color:rgba(255,253,248,0.74)] px-4 py-4 text-left transition-colors duration-150 active:bg-[color:rgba(232,219,199,0.72)] dark:border-[color:rgba(232,211,174,0.1)] dark:bg-[color:rgba(28,25,21,0.78)] dark:active:bg-[color:rgba(55,47,39,0.8)]"
    >
      <span
        aria-hidden="true"
        className={`mt-2 h-2.5 w-2.5 shrink-0 rounded-full ${
          message.unread ? "bg-[color:#2e704f] dark:bg-[color:#8fc69f]" : "bg-transparent"
        }`}
      />
      <span className="min-w-0 flex-1">
        <span className="flex min-w-0 items-center justify-between gap-3">
          <span className="truncate text-[0.95rem] font-semibold tracking-normal text-[var(--workspace-text)]">
            {message.sender || message.from}
          </span>
          <span className="shrink-0 text-[0.72rem] text-[var(--workspace-text-faint)]">
            {message.time || message.timestamp}
          </span>
        </span>
        <span className="mt-0.5 block truncate text-[0.92rem] font-medium text-[var(--workspace-text)]">
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
  mailboxes,
  priorityMessages,
  onLogoutClick,
}: MobileWorkspaceShellProps) {
  const [activeTab, setActiveTab] = useState<MobileTab>("priority");
  const [view, setView] = useState<MobileView>({ kind: "root" });
  const connectedFirstMailboxes = useMemo(
    () => [...mailboxes].sort((first, second) => Number(second.connected) - Number(first.connected)),
    [mailboxes],
  );
  const activeMailbox =
    view.kind === "mailbox"
      ? mailboxes.find((mailbox) => mailbox.id === view.mailboxId) ?? null
      : null;

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
          <div className="w-12" />
        </div>
        {syncFeedbackMessage ? (
          <div className="mt-2 truncate rounded-full border border-[color:rgba(232,211,174,0.24)] bg-[color:rgba(255,250,239,0.12)] px-3 py-1.5 text-center text-[0.72rem] font-medium text-[color:rgba(255,248,236,0.88)]">
            {syncFeedbackMessage}
          </div>
        ) : null}
      </header>

      <section className="min-h-0 flex-1 overflow-y-auto pb-[calc(env(safe-area-inset-bottom)+4.75rem)]">
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
            <MessageList
              messages={activeMailbox.messages}
              emptyTitle="No visible messages"
              emptyDetail="Filtered, quiet, archived, and spam messages stay out of this mobile inbox."
              onOpenMessage={openMessage}
            />
          ) : (
            <div className="overflow-hidden border-y border-[color:rgba(86,69,46,0.08)] bg-[color:rgba(255,251,244,0.5)] dark:border-[color:rgba(232,211,174,0.08)] dark:bg-[color:rgba(19,17,15,0.72)]">
              {connectedFirstMailboxes.map((mailbox) => (
                <button
                  key={mailbox.id}
                  type="button"
                  onClick={() => setView({ kind: "mailbox", mailboxId: mailbox.id })}
                  className="flex w-full items-center justify-between gap-4 border-b border-[color:rgba(86,69,46,0.1)] bg-[color:rgba(255,253,248,0.74)] px-5 py-4 text-left active:bg-[color:rgba(232,219,199,0.72)] dark:border-[color:rgba(232,211,174,0.1)] dark:bg-[color:rgba(28,25,21,0.78)] dark:active:bg-[color:rgba(55,47,39,0.8)]"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-[1rem] font-semibold tracking-normal text-[var(--workspace-text)]">
                      {mailbox.title}
                    </span>
                    <span className="mt-1 block truncate text-[0.8rem] text-[var(--workspace-text-faint)]">
                      {mailbox.email}
                    </span>
                  </span>
                  <span className={`shrink-0 rounded-full border px-2.5 py-1 text-[0.72rem] font-semibold ${
                    mailbox.connected
                      ? "border-[color:rgba(46,112,82,0.18)] bg-[color:rgba(69,130,96,0.1)] text-[color:rgba(35,92,65,0.9)] dark:border-[color:rgba(129,191,153,0.22)] dark:bg-[color:rgba(91,145,109,0.16)] dark:text-[color:rgba(184,225,197,0.9)]"
                      : "border-[color:rgba(159,124,66,0.16)] bg-[color:rgba(214,179,114,0.1)] text-[color:rgba(109,84,43,0.72)] dark:border-[color:rgba(218,190,138,0.2)] dark:bg-[color:rgba(214,179,114,0.08)] dark:text-[color:rgba(232,211,174,0.72)]"
                  }`}>
                    {mailbox.connected ? `${mailbox.messages.length}` : "Pending"}
                  </span>
                </button>
              ))}
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
              {syncFeedbackMessage ? (
                <div className="mt-3 text-[0.8rem] leading-5 text-[var(--workspace-text-faint)]">
                  {syncFeedbackMessage}
                </div>
              ) : null}
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

      <nav className="fixed inset-x-0 bottom-0 z-[60] border-t border-[color:rgba(86,69,46,0.12)] bg-[color:rgba(255,250,241,0.9)] px-3 pb-[calc(env(safe-area-inset-bottom)+0.5rem)] pt-2 shadow-[0_-16px_34px_rgba(66,48,27,0.08)] backdrop-blur-xl dark:border-[color:rgba(232,211,174,0.1)] dark:bg-[color:rgba(27,23,19,0.9)]">
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
    </main>
  );
}
