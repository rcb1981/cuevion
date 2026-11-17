// Shared folder presentation only; provider routing and folder availability stay
// in their existing components. Keep the 152px grid track and mobile padding.
export const mailboxFolderRailClass = "flex min-h-0 min-w-0 flex-col overflow-hidden border-r border-[var(--workspace-border-soft)] px-3 py-5 xl:pl-0";
export const mailboxFolderHeadingClass = "shrink-0 px-2 pb-3 text-[0.62rem] font-semibold uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]";
export const mailboxFolderListClass = "min-h-0 flex-1 space-y-1 overflow-y-auto";
export const mailboxFolderRowClass = "flex w-full items-center justify-between rounded-[12px] border-l-2 py-2.5 text-left";
export const mailboxFolderSelectedClass = "border-l-[var(--workspace-accent-border)] bg-[var(--workspace-card-subtle)] text-[var(--workspace-text)]";
export const mailboxFolderIdleClass = "border-l-transparent text-[var(--workspace-text-soft)] hover:bg-[var(--workspace-card-subtle)]";
