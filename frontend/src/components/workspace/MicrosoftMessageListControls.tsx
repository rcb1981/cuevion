import React, { useEffect, useRef, useState } from "react";
import type { MicrosoftMessageSortOrder } from "./microsoftMailboxListFilters";

/**
 * Microsoft-only, view-level controls. Search and filters affect only the
 * currently loaded page. The chrome follows the fixed Gmail/IMAP reference;
 * Graph querying and classification continue to use guarded Microsoft logic.
 */
export function MicrosoftMessageListControls({
  mailboxId,
  searchQuery,
  onSearchChange,
  unreadOnly,
  onUnreadOnlyChange,
  priorityOnly = false,
  onPriorityOnlyChange = () => {},
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
  priorityOnly?: boolean;
  onPriorityOnlyChange?: (value: boolean) => void;
  canFilterUnread: boolean;
  sortOrder: MicrosoftMessageSortOrder;
  onSortChange: (value: MicrosoftMessageSortOrder) => void;
  refreshBusy: boolean;
  refreshDisabled: boolean;
  onRefresh: () => void;
  hideRefreshButton?: boolean;
}) {
  const [sortOpen, setSortOpen] = useState(false);
  const sortRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!sortOpen) return;
    const closeOnOutside = (event: PointerEvent) => {
      if (!sortRef.current?.contains(event.target as Node)) setSortOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSortOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [sortOpen]);
  const pillBase =
    "inline-flex h-8 items-center justify-center rounded-full px-3.5 text-[0.66rem] font-medium uppercase tracking-[0.14em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)]";
  const selectedPill =
    "bg-[linear-gradient(180deg,var(--workspace-card-featured-start),var(--workspace-card-featured-end))] text-[var(--workspace-text)]";
  const unselectedPill =
    "border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface)]";
  const searchId = "microsoft-list-search-" + mailboxId;

  return (
    <div className="space-y-3" aria-label="Microsoft loaded message controls">
      <label htmlFor={searchId} className="sr-only">
        Search loaded Microsoft messages
      </label>
      <div className="flex h-10 items-center gap-3 rounded-xl border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-3 text-[var(--workspace-text-soft)] transition-[background-color,border-color,color] duration-150 focus-within:border-[var(--workspace-border)] focus-within:bg-[var(--workspace-hover-surface)]">
        <svg
          aria-hidden="true"
          viewBox="0 0 16 16"
          className="h-3.5 w-3.5 flex-none"
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
          className="w-full min-w-0 bg-transparent text-[0.88rem] text-[var(--workspace-text)] outline-none placeholder:text-[var(--workspace-text-faint)]"
        />
        {searchQuery ? <button
          type="button"
          aria-label="Clear search"
          onClick={() => onSearchChange("")}
          className="inline-flex h-6 w-6 flex-none items-center justify-center rounded-full text-[var(--workspace-text-faint)] hover:bg-[var(--workspace-card)] hover:text-[var(--workspace-text-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)]"
        >
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="h-3 w-3" aria-hidden="true">
            <path d="M4 4l8 8M12 4 4 12" />
          </svg>
        </button> : null}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex shrink-0 flex-wrap items-center gap-1.5" role="group" aria-label="Filter loaded messages">
          <button
            type="button"
            aria-pressed={!unreadOnly && !priorityOnly}
            onClick={() => { onUnreadOnlyChange(false); onPriorityOnlyChange(false); }}
            className={pillBase + " " + (!unreadOnly && !priorityOnly ? selectedPill : unselectedPill)}
          >
            All
          </button>
          <button
            type="button"
            disabled={!canFilterUnread}
            aria-pressed={canFilterUnread && unreadOnly}
            onClick={() => { onUnreadOnlyChange(true); onPriorityOnlyChange(false); }}
            title={canFilterUnread ? "Show unread messages on this loaded page" : "Unread filtering is not available in this folder"}
            className={pillBase + " " + (canFilterUnread && unreadOnly ? selectedPill : unselectedPill) + " disabled:cursor-not-allowed disabled:opacity-45"}
          >
            Unread
          </button>
          <button
            type="button"
            aria-pressed={priorityOnly}
            title="Show confirmed important messages on this loaded page"
            onClick={() => { onPriorityOnlyChange(!priorityOnly); onUnreadOnlyChange(false); }}
            className={pillBase + " " + (priorityOnly ? selectedPill : unselectedPill)}
          >
            Priority
          </button>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <div className="relative" ref={sortRef}>
            <button type="button" aria-label="Sort messages" aria-expanded={sortOpen}
              title={sortOrder === "newest" ? "Newest first" : "Oldest first"}
              onClick={() => setSortOpen((current) => !current)}
              className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] text-[var(--workspace-text-faint)] transition-colors duration-150 hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface)] hover:text-[var(--workspace-text-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)]">
              <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.55" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
                <path d="M5.25 2.75v10.5M3.75 4.25 5.25 2.75l1.5 1.5M10.75 13.25V2.75M9.25 11.75 10.75 13.25l1.5-1.5" />
              </svg>
            </button>
            {sortOpen ? <div role="menu" aria-label="Sort messages" className="absolute right-0 top-full z-30 mt-2 w-[172px] rounded-[18px] border border-[var(--workspace-menu-border)] bg-[var(--workspace-menu-bg)] p-2 shadow-panel">
              {([ { value: "newest", label: "Newest first" }, { value: "oldest", label: "Oldest first" } ] as const).map((option) => (
                <button key={option.value} type="button" role="menuitemradio" aria-checked={sortOrder === option.value}
                  onClick={() => { onSortChange(option.value); setSortOpen(false); }}
                  className="flex w-full items-center justify-between rounded-[14px] px-3 py-2.5 text-left text-[0.82rem] text-[var(--workspace-text-soft)] transition-colors duration-200 hover:bg-[var(--workspace-menu-hover)]">
                  <span>{option.label}</span>
                  {sortOrder === option.value ? <span aria-hidden="true">✓</span> : null}
                </button>
              ))}
            </div> : null}
          </div>
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
        Search, All, Unread, Priority and sorting affect only this loaded page. Priority includes learned important senders and confirmed message signals.
      </span>
    </div>
  );
}
