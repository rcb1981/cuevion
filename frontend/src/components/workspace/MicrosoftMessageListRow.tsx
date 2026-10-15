import React from "react";
import type { LiveInboxMessageSnapshot } from "../../lib/inboxConnectionApi";

type MicrosoftMessageListRowProps = {
  message: LiveInboxMessageSnapshot;
  folder: "Inbox" | "Sent" | "Archive" | "Drafts" | "Deleted" | "Junk";
  selected: boolean;
  disabled: boolean;
  dateLabel: string;
  onOpenMessage: (providerMessageId: string) => void;
};

/**
 * Visual-only read state: uses the authoritative Graph unread flag.
 * Selecting a row NEVER changes its read/unread state.
 */
export function MicrosoftMessageListRow({
  message,
  folder,
  selected,
  disabled,
  dateLabel,
  onOpenMessage,
}: MicrosoftMessageListRowProps) {
  const id = message.providerMessageId;
  if (!id) {
    return null;
  }

  const unread = (folder === "Inbox" || folder === "Archive" || folder === "Deleted" || folder === "Junk") && message.unread === true;
  const readState = folder === "Sent" ? "sent" : folder === "Drafts" ? "draft" : unread ? "unread" : "read";

  return (
    <button
      type="button"
      aria-pressed={selected}
      data-read-state={readState}
      disabled={disabled}
      onClick={() => onOpenMessage(id)}
      className={
        "block w-full border-b border-l-[3px] border-b-[var(--workspace-border-soft)] px-4 py-4 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--workspace-provider-selected-border)] disabled:opacity-60 " +
        (selected
          ? "border-l-moss bg-[var(--workspace-card-subtle)] "
          : "border-l-transparent hover:bg-[var(--workspace-card-subtle)] ")
      }
    >
      <div className="flex items-start gap-2">
        <span
          aria-hidden="true"
          className={
            "mt-1.5 h-2 w-2 shrink-0 rounded-full " +
            (unread
              ? "bg-[#8B3DFF] shadow-[0_0_0_2px_rgba(139,61,255,0.18),0_0_10px_rgba(139,61,255,0.42)]"
              : "bg-transparent")
          }
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <span
              className={
                "min-w-0 truncate text-sm " +
                (unread
                  ? "font-semibold text-[var(--workspace-text)]"
                  : "font-medium text-[var(--workspace-text-muted)]")
              }
            >
              {folder === "Inbox" || folder === "Archive" || folder === "Deleted" || folder === "Junk" ? (
                <span className="sr-only">
                  {unread ? "Unread message. " : "Read message. "}
                </span>
              ) : null}
              {message.sender}
            </span>
            <span
              className={
                "shrink-0 text-[0.7rem] " +
                (unread
                  ? "font-medium text-[var(--workspace-text-soft)]"
                  : "text-[var(--workspace-text-faint)]")
              }
            >
              {dateLabel}
            </span>
          </div>
          <div
            className={
              "mt-1 truncate text-sm " +
              (unread
                ? "font-semibold text-[var(--workspace-text)]"
                : "font-normal text-[var(--workspace-text)]")
            }
          >
            {message.subject || "(No subject)"}
          </div>
          <p className="mt-1 truncate text-xs text-[var(--workspace-text-muted)]">
            {message.snippet || "No preview available"}
          </p>
          {message.hasAttachments === true && (
            <span aria-label="Has attachments" className="mt-1 inline-flex text-[0.68rem] text-[var(--workspace-text-muted)]">
              Attachment
            </span>
          )}
        </div>
      </div>
    </button>
  );
}
