import React from "react";
import type { LiveInboxMessageSnapshot } from "../../lib/inboxConnectionApi";

export function MicrosoftConversationNav({
  messages, selectedId, disabled, onSelect,
}: {
  messages: ReadonlyArray<LiveInboxMessageSnapshot>;
  selectedId: string;
  disabled: boolean;
  onSelect: (id: string) => void;
}) {
  if (messages.length < 2) return null;
  return (
    <section aria-label="Microsoft conversation messages" className="mt-4 rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] p-3">
      <h3 className="px-1 text-xs font-medium text-[var(--workspace-text)]">
        {messages.length} messages in this conversation
      </h3>
      <p className="px-1 pb-2 text-[0.68rem] text-[var(--workspace-text-faint)]">
        From the currently loaded page. Select a message to open it.
      </p>
      <div className="space-y-1.5">
        {messages.map((message) => {
          const current = message.providerMessageId === selectedId;
          return (
            <button key={message.providerMessageId} type="button"
              aria-current={current ? "true" : undefined}
              disabled={current || disabled || !message.providerMessageId}
              onClick={() => { if (message.providerMessageId) onSelect(message.providerMessageId); }}
              className={"block w-full rounded-xl border px-3 py-2 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)] disabled:cursor-default " +
                (current ? "border-[var(--workspace-accent-border)] bg-[var(--workspace-hover-surface)]" :
                  "border-[var(--workspace-border-soft)] hover:bg-[var(--workspace-hover-surface)]")}>
              <span className="flex items-center justify-between gap-2">
                <span className="min-w-0 truncate text-xs font-medium text-[var(--workspace-text)]">{message.sender}</span>
                <span className="shrink-0 text-[0.68rem] text-[var(--workspace-text-faint)]">{new Date(message.createdAt).toLocaleDateString()}</span>
              </span>
              <span className="mt-1 block truncate text-[0.76rem] text-[var(--workspace-text-soft)]">
                {message.snippet || message.subject}
              </span>
              {current && <span className="mt-1 block text-[0.67rem] text-[var(--workspace-text-faint)]">Open message</span>}
            </button>
          );
        })}
      </div>
    </section>
  );
}
