import { useState, type MutableRefObject } from "react";
import type { CollaborationOwnerReadMessage } from "../../lib/collaborationOwnerReadApi";

export function isOwnCollaborationActivity(entry: CollaborationOwnerReadMessage, currentCanonicalUserId: string | null) {
  return entry.authorRole === "Cuevion user" && entry.authorUserId !== null && entry.authorUserId === currentCanonicalUserId;
}

export function CollaborationLock() {
  return <svg aria-hidden="true" width="13" height="13" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"><rect x="4" y="8" width="12" height="9" rx="2" /><path d="M6.5 8V5a3.5 3.5 0 0 1 7 0v3" /></svg>;
}

export function CollaborationChatTimeline({ messages, currentCanonicalUserId, activityRefs, highlightedId, formatTimestamp }: {
  messages: CollaborationOwnerReadMessage[];
  currentCanonicalUserId: string | null;
  activityRefs: MutableRefObject<Record<string, HTMLDivElement | null>>;
  highlightedId: string | null;
  formatTimestamp: (timestamp: number) => string;
}) {
  // The canonical projection is already chronological. Keep one addressable node per activity.
  return <section aria-label="Conversation" className="space-y-4 py-3">
    {messages.length === 0 ? <p className="py-10 text-center text-sm text-[var(--workspace-text-muted)]">No messages yet.</p> : messages.map(entry => {
      const own = isOwnCollaborationActivity(entry, currentCanonicalUserId);
      const internal = entry.visibility === "internal";
      const timestamp = new Date(entry.timestamp);
      return <div key={entry.id} className={`flex min-w-0 ${own ? "justify-end pl-6 sm:pl-12" : "justify-start pr-6 sm:pr-12"}`}>
        <div
          ref={node => { activityRefs.current[entry.id] = node; }}
          tabIndex={-1}
          role="article"
          aria-label={`${entry.authorDisplayName}${internal ? ", Internal note" : ""}`}
          data-collaboration-activity-id={entry.id}
          data-collaboration-alignment={own ? "right" : "left"}
          data-notification-highlighted={highlightedId === entry.id ? "true" : undefined}
          className={`min-w-0 max-w-[34rem] rounded-[18px] px-3.5 py-2.5 [overflow-wrap:anywhere] ${own ? "rounded-br-[5px]" : "rounded-bl-[5px]"} ${internal ? "border border-[var(--workspace-accent-border)] bg-[var(--workspace-card-featured-start)] text-[var(--workspace-text)]" : own ? "bg-pine text-[#fbf8f2]" : "border border-[var(--workspace-border)] bg-[var(--workspace-card)] text-[var(--workspace-text)]"} ${highlightedId === entry.id ? "outline outline-2 outline-offset-2 outline-[var(--workspace-accent-text)]" : ""}`}
        >
          <div className={`mb-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs ${own && !internal ? "text-[#fbf8f2]/80" : "text-[var(--workspace-text-muted)]"}`}>
            <span className="font-medium">{entry.authorDisplayName}</span>
            {entry.authorRole === "Guest reviewer" ? <span>Guest</span> : null}
            {internal ? <span className="inline-flex items-center gap-1"><CollaborationLock />Internal note</span> : null}
          </div>
          <p className="whitespace-pre-wrap text-[0.88rem] leading-6">{entry.text}</p>
          <time dateTime={Number.isFinite(timestamp.getTime()) ? timestamp.toISOString() : undefined} title={formatTimestamp(entry.timestamp)} className={`mt-1 block text-right text-[0.68rem] ${own && !internal ? "text-[#fbf8f2]/80" : "text-[var(--workspace-text-muted)]"}`}>
            {formatTimestamp(entry.timestamp)}
          </time>
        </div>
      </div>;
    })}
  </section>;
}

export type CollaborationComposerChannel = {
  draft: string;
  sending: boolean;
  error: string | null;
  canRetry: boolean;
  onChange: (text: string) => void;
  onSend: () => void;
};

export function CollaborationChatComposer({ shared, internal }: {
  shared: CollaborationComposerChannel;
  internal: CollaborationComposerChannel;
}) {
  const [mode, setMode] = useState<"shared" | "internal">("shared");
  const channel = mode === "shared" ? shared : internal;
  // Drafts and retained retry operations belong to the existing owner handlers.
  const sending = shared.sending || internal.sending;
  return <section data-collaboration-composers className="shrink-0 space-y-2 border-t border-[var(--workspace-border)] pt-3">
    <div role="group" aria-label="Message visibility" className="flex gap-1">
      {(["shared", "internal"] as const).map(value => <button key={value} type="button" aria-pressed={mode === value} onClick={() => setMode(value)} className={`inline-flex min-h-11 items-center gap-1.5 rounded-full px-4 text-sm ${mode === value ? "bg-[var(--workspace-accent-surface-start)] font-medium text-[var(--workspace-text)]" : "text-[var(--workspace-text-muted)] hover:bg-[var(--workspace-hover-surface)]"}`}>
        {value === "internal" ? <CollaborationLock /> : null}{value === "shared" ? "Shared" : "Internal"}
      </button>)}
    </div>
    {mode === "internal" ? <p id="collaboration-composer-privacy" className="text-xs text-[var(--workspace-text-muted)]">Only your Cuevion team can see this.</p> : null}
    <label className="block">
      <span className="sr-only">{mode === "shared" ? "Message" : "Internal note"}</span>
      <textarea
        data-collaboration-composer-mode={mode}
        aria-describedby={mode === "internal" ? "collaboration-composer-privacy" : undefined}
        value={channel.draft}
        onChange={event => channel.onChange(event.target.value)}
        disabled={channel.sending}
        rows={2}
        placeholder={mode === "shared" ? "Write a message…" : "Write an internal note…"}
        className="block max-h-32 min-h-16 w-full resize-none rounded-[14px] border border-[var(--workspace-border)] bg-[var(--workspace-card)] px-3 py-2 text-base leading-6 text-[var(--workspace-text)] outline-none placeholder:text-[var(--workspace-text-muted)] focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)] disabled:opacity-60 sm:text-sm"
      />
    </label>
    <div className="flex items-center justify-between gap-3">
      <div role="status" className="min-w-0 text-xs text-[var(--workspace-text-muted)]">{channel.error}</div>
      <button type="button" onClick={channel.onSend} disabled={sending || !channel.draft.trim()} className="inline-flex min-h-11 shrink-0 items-center justify-center rounded-full bg-pine px-6 text-sm font-medium text-[#fbf8f2] hover:bg-moss disabled:cursor-not-allowed disabled:opacity-45">
        {channel.sending ? "Sending…" : channel.error && channel.canRetry ? "Retry" : "Send"}
      </button>
    </div>
  </section>;
}
