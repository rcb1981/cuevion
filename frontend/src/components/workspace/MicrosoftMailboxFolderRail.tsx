import React from "react";
import { mailboxFolderRailClass, mailboxFolderHeadingClass, mailboxFolderListClass, mailboxFolderRowClass, mailboxFolderSelectedClass, mailboxFolderIdleClass } from "./mailboxFolderStyles";

/**
 * Provider-neutral visual treatment, Microsoft-only wiring.
 * All folder changes still call the existing guarded Microsoft selectFolder.
 * Does not create or move any folder/message in Graph.
 */
export const microsoftVisualFolders = [
  { id: "Inbox", label: "Inbox" },
  { id: "Filtered", label: "Filtered" },
  { id: "Sent", label: "Sent" },
  { id: "Archive", label: "Archive" },
  { id: "Drafts", label: "Drafts" },
  // Gmail/IMAP presentation labels; the guarded Microsoft folder IDs and
  // Graph endpoints remain Deleted Items and Junk Email respectively.
  { id: "Deleted", label: "Trash" },
  { id: "Junk", label: "Spam" },
] as const;

export type MicrosoftVisualFolder = (typeof microsoftVisualFolders)[number]["id"];

export function MicrosoftMailboxFolderRail({
  folder,
  onSelectFolder,
  disabled,
  compact = false,
  composeAction,
}: {
  folder: MicrosoftVisualFolder;
  onSelectFolder: (value: MicrosoftVisualFolder) => void;
  disabled: boolean;
  compact?: boolean;
  composeAction?: React.ReactNode;
}) {
  const containerClass = compact
    ? "flex shrink-0 gap-1 overflow-x-auto border-b border-[var(--workspace-border-soft)] px-3 py-2 xl:hidden"
    : `${mailboxFolderRailClass} hidden xl:flex`;

  return (
    <nav
      aria-label={compact ? "Microsoft mail folders (compact)" : "Microsoft mail folders"}
      className={containerClass}
    >
      {composeAction && (
        <div className={compact ? "mr-2 shrink-0" : "mb-4 min-w-0"}>
          {composeAction}
        </div>
      )}
      {!compact && (
        <p className={mailboxFolderHeadingClass}>
          Folders
        </p>
      )}
      <div className={compact ? "contents" : mailboxFolderListClass}>
      {microsoftVisualFolders.map((item) => {
        const selected = folder === item.id;
        return (
          <button
            key={item.id}
            type="button"
            disabled={disabled}
            aria-pressed={selected}
            onClick={() => onSelectFolder(item.id)}
            className={
              (compact
                ? "shrink-0 rounded-full px-3 py-2 text-[0.73rem] "
                : mailboxFolderRowClass + (item.id === "Filtered" ? " pl-7 pr-3 " : " px-3 ")) +
              "font-medium transition-[background-color,color,border-color] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)] disabled:cursor-not-allowed disabled:opacity-45 " +
              (selected
                ? compact
                  ? "bg-[var(--workspace-card-subtle)] text-[var(--workspace-text)]"
                  : mailboxFolderSelectedClass
                : compact
                  ? "text-[var(--workspace-text-muted)] hover:bg-[var(--workspace-hover-surface)] hover:text-[var(--workspace-text)]"
                  : mailboxFolderIdleClass + (item.id === "Filtered" ? " hover:text-[var(--workspace-text)]" : ""))
            }
          >
            {compact ? item.label : (
              <span className={item.id === "Filtered"
                ? `text-[0.77rem] font-medium ${selected ? "" : "opacity-95"}`
                : "block min-w-0 truncate text-[0.79rem] font-medium"}>
                {item.label}
              </span>
            )}
          </button>
        );
      })}
      </div>
    </nav>
  );
}
