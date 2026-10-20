import React from "react";
import type { MicrosoftMessageSortOrder } from "./microsoftMailboxListFilters";

/**
 * Microsoft-only, view-level controls. Search and filters affect only the
 * currently loaded page. Priority intentionally stays unavailable until
 * Microsoft's trusted classification path has parity with Gmail/IMAP.
 */
export function MicrosoftMessageListControls({
  mailboxId,
  searchQuery,
  onSearchChange,
  unreadOnly,
  onUnreadOnlyChange,
  canFilterUnread,
  sortOrder,
  onSortChange,
  refreshBusy,
  refreshDisabled,
  onRefresh,
  hideRefreshButton = false,
}: {
  mailboxId: string;
  searchQuery: string;
  onSearchChange: (value: string) => void;
  unreadOnly: boolean;
  onUnreadOnlyChange: (value: boolean) => void;
  canFilterUnread: boolean;
  sortOrder: MicrosoftMessageSortOrder;
  onSortChange: (value: MicrosoftMessageSortOrder) => void;
  refreshBusy: boolean;
  refreshDisabled: boolean;
  onRefresh: () => void;
  hideRefreshButton?: boolean;
}) {
  const pillBase =
    "inline-flex h-8 shrink-0 items-center justify-center rounded-full border px-3 text-[0.73rem] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)]";
  const selectedPill =
    "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)]";
  const unselectedPill =
    "border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] text-[var(--workspace-text-muted)] hover:bg-[var(--workspace-hover-surface)] hover:text-[var(--workspace-text)]";
  const searchId = "microsoft-list-search-" + mailboxId;

  return (
    <div className="space-y-2.5" aria-label="Microsoft loaded message controls">
      <label htmlFor={searchId} className="sr-only">
        Search loaded Microsoft messages
      </label>
      <div className="relative">
        <svg
          aria-hidden="true"
          viewBox="0 0 16 16"
          className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--workspace-text-faint)]"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.65"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="7" cy="7" r="4.5" />
          <path d="m10.4 10.4 3.1 3.1" />
        </svg>
        <input
          id={searchId}
          type="search"
          autoComplete="off"
          value={searchQuery}
          maxLength={160}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="Search mail"
          aria-label="Search loaded Microsoft messages"
          className="w-full rounded-xl border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] py-2 pl-9 pr-3 text-sm text-[var(--workspace-text)] placeholder:text-[var(--workspace-text-faint)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)]"
        />
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex shrink-0 flex-wrap items-center gap-1.5" role="group" aria-label="Filter loaded messages">
          <button
            type="button"
            aria-pressed={!unreadOnly}
            onClick={() => onUnreadOnlyChange(false)}
            className={pillBase + " " + (!unreadOnly ? selectedPill : unselectedPill)}
          >
            All
          </button>
          <button
            type="button"
            disabled={!canFilterUnread}
            aria-pressed={canFilterUnread && unreadOnly}
            onClick={() => onUnreadOnlyChange(true)}
            title={canFilterUnread ? "Show unread messages on this loaded page" : "Unread filtering is not available in this folder"}
            className={pillBase + " " + (canFilterUnread && unreadOnly ? selectedPill : unselectedPill) + " disabled:cursor-not-allowed disabled:opacity-45"}
          >
            Unread
          </button>
          <button
            type="button"
            disabled
            aria-pressed={false}
            title="Priority filtering for Microsoft is not available yet"
            className={pillBase + " " + unselectedPill + " cursor-not-allowed opacity-55"}
          >
            Priority
          </button>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <label className="text-xs text-[var(--workspace-text-muted)]">
            <span className="sr-only">Sort loaded Microsoft messages</span>
            <select
              aria-label="Sort loaded Microsoft messages"
              value={sortOrder}
              onChange={(event) => onSortChange(event.target.value as MicrosoftMessageSortOrder)}
              className="h-8 max-w-[132px] rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-2 text-xs text-[var(--workspace-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)]"
            >
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
            </select>
          </label>
          {!hideRefreshButton && <button
            type="button"
            aria-label={refreshBusy ? "Refreshing Microsoft messages" : "Refresh Microsoft messages"}
            title="Refresh loaded Microsoft messages (read-only)"
            disabled={refreshDisabled}
            onClick={onRefresh}
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] text-[var(--workspace-text-soft)] hover:bg-[var(--workspace-hover-surface)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)] disabled:cursor-not-allowed disabled:opacity-40"
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 16 16"
              className={"h-4 w-4 " + (refreshBusy ? "animate-spin" : "")}
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
          </button>}
        </div>
      </div>
      <span className="sr-only">
        Search, All, Unread and sorting affect only this loaded page. Priority is not implemented for Microsoft.
      </span>
    </div>
  );
}
