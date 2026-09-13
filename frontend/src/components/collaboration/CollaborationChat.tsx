import { useId, useLayoutEffect, useRef, useState, type MutableRefObject } from "react";
import type { CollaborationOwnerReadMessage } from "../../lib/collaborationOwnerReadApi";
import {
  collaborationMentionKey, collaborationMentionQuery, filterCollaborationMentionCandidates,
  insertCollaborationMention, reconcileCollaborationMentionDraft, segmentCollaborationMentions,
  MAX_COLLABORATION_MENTIONS, type CollaborationMentionCandidate, type CollaborationMentionDraft,
} from "../../lib/collaborationMentions";

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
          <p className="whitespace-pre-wrap text-[0.88rem] leading-6">{segmentCollaborationMentions(entry.text, entry.mentions).map((segment, index) => segment.mention
            ? <span key={index} data-collaboration-mention className="rounded px-0.5 font-semibold ring-1 ring-inset ring-current/20">{segment.text}</span>
            : segment.text)}</p>
          <time dateTime={Number.isFinite(timestamp.getTime()) ? timestamp.toISOString() : undefined} title={formatTimestamp(entry.timestamp)} className={`mt-1 block text-right text-[0.68rem] ${own && !internal ? "text-[#fbf8f2]/80" : "text-[var(--workspace-text-muted)]"}`}>
            {formatTimestamp(entry.timestamp)}
          </time>
        </div>
      </div>;
    })}
  </section>;
}

export type CollaborationComposerChannel = {
  draft: CollaborationMentionDraft;
  sending: boolean;
  error: string | null;
  canRetry: boolean;
  onChange: (draft: CollaborationMentionDraft) => void;
  onSend: () => void;
};

export function CollaborationChatComposer({ shared, internal, mentionCandidates, resolved }: {
  shared: CollaborationComposerChannel;
  internal: CollaborationComposerChannel;
  mentionCandidates: readonly CollaborationMentionCandidate[];
  resolved?: {
    canReopen: boolean;
    pending: boolean;
    error: string | null;
    onReopen: () => void;
  };
}) {
  const [mode, setMode] = useState<"shared" | "internal">("shared");
  const [selection, setSelection] = useState<{ text: string; mode: typeof mode; start: number; end: number } | null>(null);
  const [mentionIndex, setMentionIndex] = useState(0);
  const [composing, setComposing] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const highlightedOptionRef = useRef<HTMLButtonElement>(null);
  const pendingCaret = useRef<number | null>(null);
  const listboxId = useId();
  const channel = mode === "shared" ? shared : internal;
  useLayoutEffect(() => {
    if (pendingCaret.current === null || !textareaRef.current || resolved) return;
    textareaRef.current.focus();
    textareaRef.current.setSelectionRange(pendingCaret.current, pendingCaret.current);
    pendingCaret.current = null;
  }, [channel.draft, resolved]);
  const syncSelection = (input: HTMLTextAreaElement) => {
    setSelection(current => current?.text === input.value && current.mode === mode && current.start === input.selectionStart && current.end === input.selectionEnd
      ? current : { text: input.value, mode, start: input.selectionStart, end: input.selectionEnd });
  };
  const query = !resolved && !composing && !channel.sending && selection?.mode === mode && selection.text === channel.draft.text
    ? collaborationMentionQuery(channel.draft.text, selection.start, selection.end) : null;
  const atMentionLimit = channel.draft.mentions.length >= MAX_COLLABORATION_MENTIONS;
  const suggestions = query && !atMentionLimit ? filterCollaborationMentionCandidates(mentionCandidates, query.query) : [];
  const activeIndex = Math.min(mentionIndex, Math.max(0, suggestions.length - 1));
  useLayoutEffect(() => {
    highlightedOptionRef.current?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, query?.query]);
  const selectMention = (candidate: CollaborationMentionCandidate) => {
    if (!query) return;
    const inserted = insertCollaborationMention(channel.draft, query, candidate);
    if (!inserted) return;
    pendingCaret.current = inserted.caret;
    channel.onChange(inserted.draft);
    setSelection(null);
    setMentionIndex(0);
  };
  // Keep the mode and parent-owned drafts while removing all writable controls.
  if (resolved) return <section data-collaboration-resolved-panel aria-label="Resolved collaboration" className="shrink-0 space-y-2 border-t border-[var(--workspace-border)] pt-3">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="min-w-0">
        <p className="text-sm font-medium text-[var(--workspace-text)]">This collaboration is resolved.</p>
        <p className="mt-1 text-xs leading-5 text-[var(--workspace-text-muted)]">{resolved.canReopen ? "Reopen it to continue the conversation." : "Ask the collaboration owner to reopen it to continue the conversation."}</p>
      </div>
      {resolved.canReopen ? <button type="button" disabled={resolved.pending} onClick={resolved.onReopen} className="min-h-11 shrink-0 rounded-full border border-[var(--workspace-border)] bg-[var(--workspace-card)] px-4 text-sm text-[var(--workspace-text)] hover:bg-[var(--workspace-hover-surface)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--workspace-accent-text)] disabled:cursor-not-allowed disabled:text-[var(--workspace-text-muted)]">
        {resolved.pending ? "Reopening…" : "Reopen collaboration"}
      </button> : null}
    </div>
    {resolved.error ? <p role="status" className="text-xs text-[var(--workspace-text-muted)]">{resolved.error}</p> : null}
  </section>;
  // Drafts and retained retry operations belong to the existing owner handlers.
  const sending = shared.sending || internal.sending;
  return <section data-collaboration-composers className="shrink-0 space-y-2 border-t border-[var(--workspace-border)] pt-3">
    <div role="group" aria-label="Message visibility" className="flex gap-1">
      {(["shared", "internal"] as const).map(value => <button key={value} type="button" aria-pressed={mode === value} onClick={() => { setMode(value); setSelection(null); setMentionIndex(0); }} className={`inline-flex min-h-11 items-center gap-1.5 rounded-full px-4 text-sm ${mode === value ? "bg-[var(--workspace-accent-surface-start)] font-medium text-[var(--workspace-text)]" : "text-[var(--workspace-text-muted)] hover:bg-[var(--workspace-hover-surface)]"}`}>
        {value === "internal" ? <CollaborationLock /> : null}{value === "shared" ? "Shared" : "Internal"}
      </button>)}
    </div>
    {mode === "internal" ? <p id="collaboration-composer-privacy" className="text-xs text-[var(--workspace-text-muted)]">Only your Cuevion team can see this.</p> : null}
    <label className="block">
      <span className="sr-only">{mode === "shared" ? "Message" : "Internal note"}</span>
      <textarea
        ref={textareaRef}
        data-collaboration-composer-mode={mode}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={suggestions.length > 0}
        aria-controls={suggestions.length > 0 ? listboxId : undefined}
        aria-activedescendant={suggestions.length > 0 ? `${listboxId}-${activeIndex}` : undefined}
        aria-describedby={mode === "internal" ? "collaboration-composer-privacy" : undefined}
        value={channel.draft.text}
        onChange={event => {
          channel.onChange(reconcileCollaborationMentionDraft(channel.draft, event.currentTarget.value));
          syncSelection(event.currentTarget);
          setMentionIndex(0);
        }}
        onSelect={event => syncSelection(event.currentTarget)}
        onBlur={() => setSelection(null)}
        onCompositionStart={() => setComposing(true)}
        onCompositionEnd={event => { setComposing(false); syncSelection(event.currentTarget); }}
        onKeyDown={event => {
          if (composing || event.nativeEvent.isComposing || event.keyCode === 229) return;
          if (event.key === "Escape" && query) { event.preventDefault(); setSelection(null); return; }
          const action = collaborationMentionKey(event.key, activeIndex, suggestions.length);
          if (!action) return;
          event.preventDefault();
          if (action.action === "move") setMentionIndex(action.index);
          else if (action.action === "select") selectMention(suggestions[action.index]);
          else setSelection(null);
        }}
        disabled={channel.sending}
        rows={2}
        placeholder={mode === "shared" ? "Write a message…" : "Write an internal note…"}
        className="block max-h-32 min-h-16 w-full resize-none rounded-[14px] border border-[var(--workspace-border)] bg-[var(--workspace-card)] px-3 py-2 text-base leading-6 text-[var(--workspace-text)] outline-none placeholder:text-[var(--workspace-text-muted)] focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)] disabled:opacity-60 sm:text-sm"
      />
    </label>
    {suggestions.length > 0 ? <ul id={listboxId} role="listbox" aria-label="Mention a collaborator" className="max-h-52 overflow-y-auto rounded-[14px] border border-[var(--workspace-border)] bg-[var(--workspace-card)] p-1 shadow-lg">
      {suggestions.map((candidate, index) => <li key={candidate.userId} role="presentation">
        <button id={`${listboxId}-${index}`} type="button" role="option" tabIndex={-1} aria-selected={index === activeIndex}
          ref={index === activeIndex ? highlightedOptionRef : undefined}
          onPointerDown={event => event.preventDefault()}
          onClick={() => selectMention(candidate)}
          className={`min-h-11 w-full rounded-lg px-3 py-2 text-left text-sm text-[var(--workspace-text)] ${index === activeIndex ? "bg-[var(--workspace-accent-surface-start)] font-medium" : "hover:bg-[var(--workspace-hover-surface)]"}`}>
          {candidate.displayName}
        </button>
      </li>)}
    </ul> : query && atMentionLimit ? <p role="status" className="text-xs text-[var(--workspace-text-muted)]">Up to 32 mentions per message.</p> : null}
    <div className="flex items-center justify-between gap-3">
      <div role="status" className="min-w-0 text-xs text-[var(--workspace-text-muted)]">{channel.error}</div>
      <button type="button" onClick={channel.onSend} disabled={sending || !channel.draft.text.trim()} className="inline-flex min-h-11 shrink-0 items-center justify-center rounded-full bg-pine px-6 text-sm font-medium text-[#fbf8f2] hover:bg-moss disabled:cursor-not-allowed disabled:opacity-45">
        {channel.sending ? "Sending…" : channel.error && channel.canRetry ? "Retry" : "Send"}
      </button>
    </div>
  </section>;
}
