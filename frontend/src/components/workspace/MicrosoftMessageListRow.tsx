import React from "react";
import type { LiveInboxMessageSnapshot } from "../../lib/inboxConnectionApi";
import type { MicrosoftLearningRule } from "./microsoftLearningRules";
import { microsoftFocusCategory, type MicrosoftFocusPreferences } from "./microsoftVirtualFiltered";

type MicrosoftMessageListRowProps = {
  message: LiveInboxMessageSnapshot;
  folder: "Inbox" | "Filtered" | "Sent" | "Archive" | "Drafts" | "Deleted" | "Junk";
  selected: boolean;
  disabled: boolean;
  dateLabel: string;
  onOpenMessage: (providerMessageId: string) => void;
  onOpenFullMessage?: (providerMessageId: string) => void;
  onOpenContextMenu?: (providerMessageId: string, x: number, y: number) => void;
  learningRule?: MicrosoftLearningRule;
  focusPreferences?: MicrosoftFocusPreferences;
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
  onOpenFullMessage,
  onOpenContextMenu,
  learningRule,
  focusPreferences,
}: MicrosoftMessageListRowProps) {
  const id = message.providerMessageId;
  if (!id) {
    return null;
  }

  const unread = (folder === "Inbox" || folder === "Filtered" || folder === "Archive" || folder === "Deleted" || folder === "Junk") && message.unread === true;
  const readState = folder === "Sent" ? "sent" : folder === "Drafts" ? "draft" : unread ? "unread" : "read";
  const focusCategory = focusPreferences ? microsoftFocusCategory(message) : null;
  const categoryName = learningRule?.label !== "Other" && learningRule?.label
    ? learningRule.label
    : focusCategory === "demos" ? "Demo"
    : focusCategory === "promo" || focusCategory === "promoReminders" ? "Promo"
    : focusCategory === "finance" || focusCategory === "royalties" ? "Finance"
    : focusCategory === "legal" || focusCategory === "business" ? "Business"
    : focusCategory === "distribution" || focusCategory === "updates" || focusCategory === "paymentReminders" ? "Update"
    : null;
  const priorityLabel = learningRule?.priority === "important" ? "PRIORITY"
    : learningRule?.priority === "show_less" ? "LOW"
    : learningRule?.priority === "normal" ? "NORMAL"
    : focusCategory && focusPreferences?.[focusCategory] === "low" ? "LOW"
    : "NORMAL";

  return (
    <button
      type="button"
      aria-pressed={selected}
      data-read-state={readState}
      disabled={disabled}
      onClick={() => onOpenMessage(id)}
      onDoubleClick={() => onOpenFullMessage?.(id)}
      onContextMenu={(event) => {
        if (!onOpenContextMenu) return;
        event.preventDefault();
        onOpenContextMenu(id, event.clientX, event.clientY);
      }}
      className={
        "flex min-h-[92px] w-full cursor-pointer flex-col rounded-none border-b border-l-[3px] border-t-0 border-r-0 px-4 py-3 text-left transition-[background-color,border-color] duration-150 focus-visible:border-[var(--workspace-border-hover)] focus-visible:outline-none disabled:opacity-60 " +
        (selected
          ? "border-l-moss bg-[var(--workspace-card-subtle)] "
          : "border-l-transparent hover:bg-[var(--workspace-card-subtle)] ")
      }
    >
      <div className="flex min-w-0 flex-1 flex-col justify-between text-[var(--workspace-text)]">
        <div className="min-w-0 space-y-0.5">
          <div className="flex items-start justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2">
              {selected ? <span aria-label="Selected message" className="inline-flex h-4 w-4 flex-none items-center justify-center rounded-full border border-[var(--workspace-selection-indicator-border)] bg-[var(--workspace-selection-indicator-bg)] text-[var(--workspace-selection-indicator-text)]">✓</span> : null}
              <span
                aria-hidden="true"
                className={
                  "h-2 w-2 shrink-0 rounded-full " +
                  (unread
                    ? "bg-[#8B3DFF] shadow-[0_0_0_2px_rgba(139,61,255,0.18),0_0_10px_rgba(139,61,255,0.42)]"
                    : "bg-transparent")
                }
              />
            <span
              className={
                "min-w-0 truncate text-[0.96rem] tracking-[-0.014em] " +
                (unread
                  ? "font-semibold text-[var(--workspace-text)]"
                  : "font-medium text-[var(--workspace-text-muted)]")
              }
            >
              {folder === "Inbox" || folder === "Filtered" || folder === "Archive" || folder === "Deleted" || folder === "Junk" ? (
                <span className="sr-only">
                  {unread ? "Unread message. " : "Read message. "}
                </span>
              ) : null}
              {message.sender}
            </span>
            </div>
            <span
              className={
                "shrink-0 text-[0.64rem] font-medium uppercase tracking-[0.12em] " +
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
              "truncate text-[0.97rem] leading-5 tracking-[-0.015em] " +
              (unread
                ? "font-semibold text-[var(--workspace-text)]"
                : "font-normal text-[var(--workspace-text)]")
            }
          >
            {message.subject || "(No subject)"}
          </div>
          <p className="truncate text-[0.78rem] leading-5 text-[color:rgba(111,103,94,0.82)]">
            {message.snippet || "No preview available"}
          </p>
          <div className="pt-0.5 text-[0.64rem] font-medium uppercase tracking-[0.12em] text-[color:rgba(120,111,100,0.72)]">
            {priorityLabel}
            {categoryName ? <span className="ml-1.5 normal-case tracking-normal opacity-55">· {categoryName}</span> : null}
          </div>
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
