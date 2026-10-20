import React from "react";

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
  { id: "Deleted", label: "Deleted Items" },
  { id: "Junk", label: "Junk Email" },
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
    : "hidden min-h-0 shrink-0 flex-col gap-1 border-r border-[var(--workspace-border-soft)] px-3 py-5 xl:flex";

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
        <p className="mb-3 px-3 text-[0.64rem] font-medium uppercase tracking-[0.18em] text-[var(--workspace-text-faint)]">
          Folders
        </p>
      )}
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
                : "w-full rounded-[12px] border-l-2 pr-3 py-2.5 text-left text-[0.77rem] " + (item.id === "Filtered" ? "pl-7 " : "pl-3 ")) +
              "font-medium transition-[background-color,color,border-color] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)] disabled:cursor-not-allowed disabled:opacity-45 " +
              (selected
                ? compact
                  ? "bg-[var(--workspace-card-subtle)] text-[var(--workspace-text)]"
                  : "border-l-[var(--workspace-accent-border)] bg-[var(--workspace-card-subtle)] text-[var(--workspace-text)]"
                : compact
                  ? "text-[var(--workspace-text-muted)] hover:bg-[var(--workspace-hover-surface)] hover:text-[var(--workspace-text)]"
                  : "border-l-transparent text-[var(--workspace-text-muted)] hover:bg-[var(--workspace-hover-surface)] hover:text-[var(--workspace-text)]")
            }
          >
            {item.label}
          </button>
        );
      })}
    </nav>
  );
}
