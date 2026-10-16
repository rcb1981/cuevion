import React, { useId, useState } from "react";

/**
 * Read-only, visual preview of the established Cuevion Gmail/IMAP action bar.
 * No mailbox command is wired to these buttons. A future feature must
 * separately add ownership, provider scopes, confirmation, and send safety.
 */
function ActionGlyph({name}: {name: "reply" | "reply-all" | "forward" | "more" | "archive" | "delete" | "mail"}) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 16 16"
      className="h-4 w-4 flex-none"
      fill={name === "more" ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth={name === "reply-all" ? "1.75" : "1.6"}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {name === "reply" && (
        <>
          <path d="M6 4 2.5 8 6 12" />
          <path d="M3 8h7c2.3 0 4 1.2 4 3.5" />
        </>
      )}
      {name === "reply-all" && (
        <>
          <path d="M5.75 4 2.5 8l3.25 4" />
          <path d="M10.75 4 7.5 8l3.25 4" />
          <path d="M3 8h7c2.1 0 3.75 1.1 3.75 3.3" />
        </>
      )}
      {name === "forward" && (
        <>
          <path d="M10 4 13.5 8 10 12" />
          <path d="M13 8H6.5C4.2 8 2.5 9.2 2.5 11.5" />
        </>
      )}
      {name === "more" && (
        <>
          <circle cx="3.5" cy="8" r="1.1" stroke="none" />
          <circle cx="8" cy="8" r="1.1" stroke="none" />
          <circle cx="12.5" cy="8" r="1.1" stroke="none" />
        </>
      )}
      {name === "archive" && (
        <>
          <rect x="2.4" y="3" width="11.2" height="3" rx="0.7" />
          <path d="M3.4 6v7h9.2V6M6 9h4" />
        </>
      )}
      {name === "delete" && (
        <>
          <path d="M3.5 5.2h9M6 3.5h4M5 5.2l.5 7.5h5l.5-7.5M7 7.5v3M9 7.5v3" />
        </>
      )}
      {name === "mail" && (
        <>
          <rect x="2.2" y="4.2" width="11.6" height="8.2" rx="1" />
          <path d="m2.5 5 5.5 4 5.5-4" />
        </>
      )}
    </svg>
  );
}

const primaryStyle =
  "inline-flex h-7 items-center justify-center gap-1.5 whitespace-nowrap rounded-full border border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] px-3 text-[0.68rem] font-medium text-[var(--workspace-accent-text)] disabled:cursor-not-allowed disabled:opacity-55";
const secondaryStyle =
  "inline-flex h-7 items-center justify-center gap-1.5 whitespace-nowrap rounded-full border border-[var(--workspace-border-soft)] bg-transparent px-3 text-[0.68rem] font-medium text-[var(--workspace-text-soft)] disabled:cursor-not-allowed disabled:opacity-55";

export function MicrosoftPlannedMessageActions({unread}: {unread: boolean}) {
  const [moreOpen, setMoreOpen] = useState(false);
  const moreId = useId();
  return (
    <section aria-label="Microsoft message actions preview" className="mt-4 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" disabled title="Reply will be available after a separate send approval." className={primaryStyle}>
          <ActionGlyph name="reply" />
          Reply
        </button>
        <button type="button" disabled title="Reply All requires separate Microsoft sending approval." className={secondaryStyle}>
          <ActionGlyph name="reply-all" />
          Reply All
        </button>
        <button type="button" disabled title="Forward requires separate Microsoft sending approval." className={secondaryStyle}>
          <ActionGlyph name="forward" />
          Forward
        </button>
        <div className="relative">
          <button
            type="button"
            aria-expanded={moreOpen}
            aria-controls={moreId}
            onClick={() => setMoreOpen(!moreOpen)}
            title="Preview the other planned mail actions"
            className={secondaryStyle + " cursor-pointer hover:bg-[var(--workspace-hover-surface)] hover:text-[var(--workspace-text)]"}
          >
            <ActionGlyph name="more" />
            More
          </button>
          {moreOpen && (
            <div
              id={moreId}
              aria-label="Other planned Microsoft message actions"
              className="absolute left-0 top-full z-20 mt-2 min-w-[190px] rounded-[18px] border border-[var(--workspace-menu-border)] bg-[var(--workspace-menu-bg)] p-2 shadow-[0_14px_32px_rgba(41,34,27,0.10)]"
            >
              {["Archive", "Delete", unread ? "Mark as read" : "Mark as unread"].map((action) => (
                <button
                  type="button"
                  disabled
                  key={action}
                  title="Planned action; not yet enabled for Microsoft"
                  className="flex w-full cursor-not-allowed items-center gap-2.5 rounded-[14px] px-3 py-2.5 text-left text-[0.82rem] text-[var(--workspace-text-soft)] opacity-60"
                >
                  <ActionGlyph name={action === "Archive" ? "archive" : action === "Delete" ? "delete" : "mail"} />
                  {action}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
      <p className="text-[0.68rem] text-[var(--workspace-text-faint)]">
        Preview only — Reply, Forward and mailbox-moving actions are not yet enabled for Microsoft.
      </p>
    </section>
  );
}
