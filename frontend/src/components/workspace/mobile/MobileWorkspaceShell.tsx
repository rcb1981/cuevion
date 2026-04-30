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
      <span className="flex h-9 w-9 items-center justify-center rounded-full border border-[var(--workspace-border)] bg-[var(--workspace-card)]">
        <span className="h-2.5 w-2.5 rounded-full bg-[var(--workspace-text)]" />
      </span>
      <span className="text-[1.02rem] font-semibold tracking-[-0.03em] text-[var(--workspace-text)]">
        Cuevion
      </span>
    </div>
  );
}

function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="flex min-h-[42dvh] flex-col items-center justify-center px-8 text-center">
      <div className="text-[1.05rem] font-medium tracking-[-0.02em] text-[var(--workspace-text)]">
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
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex w-full gap-3 border-b border-[var(--workspace-border-soft)] px-4 py-3.5 text-left transition-colors duration-150 active:bg-[var(--workspace-hover-surface)]"
    >
      <span
        aria-hidden="true"
        className={`mt-2 h-2.5 w-2.5 shrink-0 rounded-full ${
          message.unread ? "bg-[var(--workspace-text)]" : "bg-transparent"
        }`}
      />
      <span className="min-w-0 flex-1">
        <span className="flex min-w-0 items-center justify-between gap-3">
          <span className="truncate text-[0.92rem] font-medium text-[var(--workspace-text)]">
            {message.sender || message.from}
          </span>
          <span className="shrink-0 text-[0.72rem] text-[var(--workspace-text-faint)]">
            {message.time || message.timestamp}
          </span>
        </span>
        <span className="mt-0.5 block truncate text-[0.9rem] font-medium text-[var(--workspace-text-soft)]">
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
            <span className="rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-2 py-0.5 text-[0.64rem] font-medium uppercase tracking-[0.12em] text-[var(--workspace-text-soft)]">
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
    <div className="bg-[var(--workspace-shell)]">
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
      className="box-border flex h-dvh flex-col overflow-hidden bg-[var(--workspace-bg)] text-[var(--workspace-text)]"
      style={{ colorScheme: themeMode }}
    >
      <header className="shrink-0 border-b border-[var(--workspace-border-soft)] bg-[var(--workspace-shell)] px-4 pb-3 pt-[calc(env(safe-area-inset-top)+0.75rem)]">
        <div className="flex min-h-10 items-center justify-between gap-3">
          {view.kind === "root" ? (
            <MobileMark />
          ) : (
            <button
              type="button"
              onClick={() =>
                setView(view.kind === "message" ? view.backView : { kind: "root" })
              }
              className="rounded-full px-1 py-2 text-[0.92rem] font-medium text-[var(--workspace-text)]"
            >
              Back
            </button>
          )}
          <div className="min-w-0 flex-1 text-center text-[1rem] font-semibold tracking-[-0.02em] text-[var(--workspace-text)]">
            {headerTitle}
          </div>
          <div className="w-12" />
        </div>
        {syncFeedbackMessage ? (
          <div className="mt-2 truncate rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-3 py-1.5 text-center text-[0.72rem] text-[var(--workspace-text-soft)]">
            {syncFeedbackMessage}
          </div>
        ) : null}
      </header>

      <section className="min-h-0 flex-1 overflow-y-auto pb-[calc(env(safe-area-inset-bottom)+4.75rem)]">
        {view.kind === "message" ? (
          <article className="min-h-full bg-[var(--workspace-shell)] px-5 py-5">
            <div className="space-y-2 border-b border-[var(--workspace-border-soft)] pb-4">
              <div className="text-[0.82rem] font-medium text-[var(--workspace-text-soft)]">
                {view.message.sender || view.message.from}
              </div>
              <h1 className="text-[1.35rem] font-semibold leading-8 tracking-[-0.03em] text-[var(--workspace-text)]">
                {view.message.subject || "No subject"}
              </h1>
              <div className="flex flex-wrap items-center gap-2 text-[0.78rem] text-[var(--workspace-text-faint)]">
                <span>{view.message.from}</span>
                <span>{view.message.timestamp || view.message.time}</span>
                {view.message.badge ? (
                  <span className="rounded-full border border-[var(--workspace-border-soft)] px-2 py-0.5 text-[0.64rem] font-medium uppercase tracking-[0.12em]">
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
              emptyTitle="No messages yet"
              emptyDetail="This inbox will fill as Cuevion refreshes connected mail."
              onOpenMessage={openMessage}
            />
          ) : (
            <div className="bg-[var(--workspace-shell)]">
              {connectedFirstMailboxes.map((mailbox) => (
                <button
                  key={mailbox.id}
                  type="button"
                  onClick={() => setView({ kind: "mailbox", mailboxId: mailbox.id })}
                  className="flex w-full items-center justify-between gap-4 border-b border-[var(--workspace-border-soft)] px-5 py-4 text-left active:bg-[var(--workspace-hover-surface)]"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-[0.98rem] font-medium text-[var(--workspace-text)]">
                      {mailbox.title}
                    </span>
                    <span className="mt-1 block truncate text-[0.8rem] text-[var(--workspace-text-faint)]">
                      {mailbox.email}
                    </span>
                  </span>
                  <span className="shrink-0 text-[0.72rem] text-[var(--workspace-text-faint)]">
                    {mailbox.connected ? `${mailbox.messages.length}` : "Pending"}
                  </span>
                </button>
              ))}
            </div>
          )
        ) : (
          <div className="space-y-4 px-5 py-5">
            <section className="rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-shell)] p-4">
              <div className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                Account
              </div>
              <div className="mt-3 text-[1rem] font-medium text-[var(--workspace-text)]">
                {accountName}
              </div>
              <div className="mt-1 truncate text-[0.84rem] text-[var(--workspace-text-soft)]">
                {accountEmail}
              </div>
            </section>
            <section className="rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-shell)] p-4">
              <div className="flex items-center justify-between gap-4">
                <span className="text-[0.9rem] text-[var(--workspace-text-soft)]">
                  Connected inboxes
                </span>
                <span className="text-[0.96rem] font-medium text-[var(--workspace-text)]">
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
              className="w-full rounded-[16px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-shell)] px-4 py-3 text-[0.9rem] font-medium text-[var(--workspace-text)]"
            >
              Log out
            </button>
          </div>
        )}
      </section>

      <nav className="fixed inset-x-0 bottom-0 z-[60] border-t border-[var(--workspace-border-soft)] bg-[var(--workspace-shell)] px-3 pb-[calc(env(safe-area-inset-bottom)+0.5rem)] pt-2">
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
                  ? "bg-[var(--workspace-hover-surface-strong)] text-[var(--workspace-text)]"
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
