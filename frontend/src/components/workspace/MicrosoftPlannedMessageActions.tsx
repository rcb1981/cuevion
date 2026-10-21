import React, { useId, useState } from "react";

/** Microsoft-only actions: read-state callback may be enabled; all other writes remain disabled. */
type GlyphName =
  | "reply" | "reply-all" | "forward" | "archive" | "attach"
  | "delete" | "bookmark" | "more" | "mail" | "move";

function ActionGlyph({ name }: { name: GlyphName }) {
  return (
    <svg aria-hidden="true" focusable="false" viewBox="0 0 16 16"
      className="h-4 w-4 shrink-0" fill={name === "more" ? "currentColor" : "none"}
      stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round">
      {name === "reply" && <><path d="M6 4 2.5 8 6 12"/><path d="M3 8h7c2.3 0 4 1.2 4 3.5"/></>}
      {name === "reply-all" && <><path d="M5.75 4 2.5 8l3.25 4"/><path d="M10.75 4 7.5 8l3.25 4"/><path d="M3 8h7c2.1 0 3.75 1.1 3.75 3.3"/></>}
      {name === "forward" && <><path d="M10 4 13.5 8 10 12"/><path d="M13 8H6.5C4.2 8 2.5 9.2 2.5 11.5"/></>}
      {name === "archive" && <><path d="M2.5 4.5h11v3h-11z"/><path d="M4 7.5v4.25h8V7.5M6 9.5h4"/></>}
      {name === "attach" && <path d="M6 6.5v4.5a2 2 0 0 0 4 0v-6a3 3 0 1 0-6 0v6.5a4 4 0 0 0 8 0V6.5"/>}
      {name === "delete" && <><path d="M3.2 4.5h9.6M5.1 4.5l.6 8h4.6l.6-8M6.6 3h2.8"/><path d="M7 6.5V10m2-3.5V10"/></>}
      {name === "bookmark" && <path d="M4.5 2.8h7v10l-3.5-2.5-3.5 2.5V2.8z"/>}
      {name === "mail" && <><rect x="2.1" y="4.1" width="11.8" height="8" rx="1"/><path d="m2.5 5 5.5 4 5.5-4"/></>}
      {name === "move" && <><path d="M2.3 5h4l1.2 1.4h6.2v6.3H2.3z"/><path d="M8 9.5h4m-1.5-1.5L12 9.5 10.5 11"/></>}
      {name === "more" && <><circle cx="3.5" cy="8" r="1.1" stroke="none"/><circle cx="8" cy="8" r="1.1" stroke="none"/><circle cx="12.5" cy="8" r="1.1" stroke="none"/></>}
    </svg>
  );
}

const iconButton =
  "inline-flex h-9 w-9 items-center justify-center rounded-full border border-[var(--workspace-border)] bg-[var(--workspace-card-subtle)] text-[var(--workspace-text-soft)] transition-[background-color,border-color,color,transform] duration-150 hover:border-[var(--workspace-border-hover)] hover:bg-[var(--workspace-hover-surface)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)] disabled:cursor-not-allowed disabled:opacity-60";

const actions = [
  { name: "reply", label: "Reply" },
  { name: "reply-all", label: "Reply All" },
  { name: "forward", label: "Forward" },
  { name: "archive", label: "Archive" },
  { name: "attach", label: "Attachments" },
  { name: "delete", label: "Delete" },
  { name: "bookmark", label: "Bookmark" },
] as const;

export function MicrosoftPlannedMessageActions({
  unread, hasMessage = true, canChangeReadState = false,
  changeReadStateBusy = false, onChangeReadState,
  canArchive = false, archiveBusy = false, onArchive,
}: {
  unread: boolean;
  hasMessage?: boolean;
  canChangeReadState?: boolean;
  changeReadStateBusy?: boolean;
  onChangeReadState?: (nextUnread: boolean) => void;
  canArchive?: boolean;
  archiveBusy?: boolean;
  onArchive?: () => void;
}) {
  const [moreOpen, setMoreOpen] = useState(false);
  const moreId = useId();

  return (
    <div aria-label="Microsoft message actions" className="flex flex-wrap items-center justify-end gap-1.5">
      {actions.map(({ name, label }) => (
        <button
          key={name} type="button" aria-label={label}
          disabled={name !== "archive" || !canArchive || archiveBusy || !onArchive}
          title={name === "archive" && canArchive
            ? "Move the selected Microsoft Inbox message to Archive after Graph confirmation"
            : label + " requires separate Microsoft action approval"}
          onClick={() => {
            if (name === "archive" && canArchive && !archiveBusy && onArchive) onArchive();
          }}
          className={iconButton}
        >
          <ActionGlyph name={name} />
        </button>
      ))}
      <div className="relative">
        <button type="button" aria-label="More" aria-expanded={moreOpen}
          aria-controls={moreId} disabled={!hasMessage} onClick={() => setMoreOpen((open) => !open)}
          title="More Microsoft message actions" className={iconButton}>
          <ActionGlyph name="more" />
        </button>
        {moreOpen && hasMessage && (
          <div id={moreId} aria-label="More Microsoft message actions"
            className="absolute right-0 top-full z-40 mt-2 min-w-[190px] rounded-[18px] border border-[var(--workspace-menu-border)] bg-[var(--workspace-menu-bg)] p-2 shadow-[0_14px_32px_rgba(20,18,16,0.25)]">
            {([
              { key: "read", label: unread ? "Mark as read" : "Mark as unread", icon: "mail" },
              { key: "move", label: "Move to folder", icon: "move" },
            ] as const).map(({ key, label, icon }) => (
              <button key={key} type="button"
                disabled={key !== "read" || !canChangeReadState || changeReadStateBusy || !onChangeReadState}
                title={key === "read" && canChangeReadState
                  ? "Update this exact Microsoft Inbox message and confirm the status"
                  : "Planned action; not enabled for Microsoft"}
                onClick={() => {
                  if (key !== "read" || !canChangeReadState || changeReadStateBusy || !onChangeReadState) return;
                  setMoreOpen(false);
                  onChangeReadState(!unread);
                }}
                className={"flex w-full items-center gap-2.5 rounded-[12px] px-3 py-2 text-left text-[0.79rem] text-[var(--workspace-text-soft)] " +
                  (key === "read" && canChangeReadState && !changeReadStateBusy && onChangeReadState
                    ? "hover:bg-[var(--workspace-hover-surface)]"
                    : "cursor-not-allowed opacity-55")}>
                <ActionGlyph name={icon} />
                {key === "read" && changeReadStateBusy ? "Updating…" : label}
              </button>
            ))}
          </div>
        )}
      </div>
      <span className="sr-only">Most Microsoft actions are not yet available. Read status can be changed for eligible Inbox messages.</span>
    </div>
  );
}
