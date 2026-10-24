import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/**
 * UI-only Microsoft composer for choosing the permanent Cuevion mail design.
 * Intentionally no Graph client, send/draft API, attachments, storage or
 * mailbox mutation. Any entered text is discarded when the preview closes.
 */
type PreviewGlyph = "close" | "send" | "attach" | "link" | "format" | "smile" | "discard";

export function Glyph({ name }: { name: PreviewGlyph }) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 20 20"
      className="h-4 w-4 shrink-0"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.65"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {name === "close" && <path d="m5 5 10 10M15 5 5 15" />}
      {name === "send" && (
        <>
          <path d="m3 10 13-6-4.1 12-2.8-5.2L3 10Z" />
          <path d="m9.1 10.8 6.9-6.8" />
        </>
      )}
      {name === "attach" && (
        <path d="m7.5 10.7 4.6-4.7a3.1 3.1 0 0 1 4.4 4.4l-6.6 6.6a5.1 5.1 0 0 1-7.2-7.2l6.2-6.2" />
      )}
      {name === "link" && (
        <>
          <path d="M7.4 12.7 12.6 7.5" />
          <path d="M8 6.8 9.8 5a3.7 3.7 0 0 1 5.2 5.2l-2 2M12 13.2l-1.8 1.8A3.7 3.7 0 0 1 5 9.8l2-2" />
        </>
      )}
      {name === "format" && (
        <>
          <path d="m4 15 6-12 6 12M6.5 10h7" />
          <path d="M3 18h14" />
        </>
      )}
      {name === "smile" && (
        <>
          <circle cx="10" cy="10" r="7" />
          <path d="M7 12c1.8 2.2 4.2 2.2 6 0" />
          <path d="M7.1 7.5h.1m5.6 0h.1" strokeWidth="2.8" />
        </>
      )}
      {name === "discard" && (
        <>
          <path d="M4.5 6h11M8 4h4M6 6l.7 10h6.6L14 6" />
          <path d="M8.5 9v4m3-4v4" />
        </>
      )}
    </svg>
  );
}

const iconButtonClass =
  "inline-flex h-9 w-9 items-center justify-center rounded-full border border-[var(--workspace-border-soft)] text-[var(--workspace-text-soft)] transition hover:bg-[var(--workspace-hover-surface)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)] disabled:cursor-not-allowed disabled:opacity-45";

const inputClass =
  "min-w-0 flex-1 bg-transparent px-2 py-2 text-sm text-[var(--workspace-text)] placeholder:text-[var(--workspace-text-faint)] outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--workspace-accent-border)]";

export function MicrosoftComposeDesignPreview({
  mailboxEmail,
  onClose,
}: {
  mailboxEmail: string;
  onClose: () => void;
}) {
  // The portal is outside WorkspaceShell: restore its light/dark CSS token scope.
  const themeMode =
    typeof document !== "undefined" &&
    (document.querySelector('[data-theme="dark"]') !== null ||
      document.body.style.colorScheme === "dark" ||
      document.documentElement.style.colorScheme === "dark")
      ? "dark"
      : "light";
  const [showCc, setShowCc] = useState(false);
  const [showBcc, setShowBcc] = useState(false);
  const dialogRef = useRef<HTMLElement | null>(null);
  const recipientRef = useRef<HTMLInputElement | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    recipientRef.current?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;

      const items = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          "button:not([disabled]), input:not([disabled]), textarea:not([disabled])",
        ),
      );
      const first = items[0];
      const last = items[items.length - 1];
      if (!first || !last) return;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previouslyFocused?.focus();
    };
  }, []);

  const preview = (
    <div
      className="fixed inset-0 z-[350] flex items-center justify-center bg-[var(--workspace-modal-scrim)] p-3 backdrop-blur-[2px] sm:p-6"
      data-microsoft-compose-design-preview="true"
      data-theme={themeMode}
    >
      <section
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="microsoft-compose-preview-title"
        aria-describedby="microsoft-compose-preview-description"
        className="flex h-[min(710px,calc(100dvh-24px))] w-full max-w-[810px] flex-col overflow-hidden rounded-[24px] border border-[var(--workspace-modal-border-strong)] bg-[var(--workspace-modal-bg)] text-[var(--workspace-text)] shadow-[0_30px_90px_rgba(0,0,0,0.30)]"
      >
        <header className="flex flex-none items-center justify-between gap-4 border-b border-[var(--workspace-border-soft)] px-5 py-4 sm:px-6">
          <div className="min-w-0">
            <h2 id="microsoft-compose-preview-title" className="text-[1.1rem] font-semibold tracking-tight">New message</h2>
            <p id="microsoft-compose-preview-description" className="mt-1 text-[0.75rem] text-[var(--workspace-text-muted)]">
              Design preview only — sending and draft saving are disabled.
            </p>
          </div>
          <button
            type="button"
            aria-label="Close compose design preview"
            onClick={onClose}
            className={iconButtonClass}
          >
            <Glyph name="close" />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 sm:px-6">
          <div className="flex min-h-[44px] items-center gap-3 border-b border-[var(--workspace-border-soft)] py-2 text-sm">
            <span className="w-14 shrink-0 text-[var(--workspace-text-faint)]">From</span>
            <span className="min-w-0 truncate text-[var(--workspace-text-soft)]">{mailboxEmail}</span>
          </div>
          <div className="flex min-h-[48px] items-center gap-2 border-b border-[var(--workspace-border-soft)] py-1">
            <label htmlFor="microsoft-compose-preview-to" className="w-14 shrink-0 text-sm text-[var(--workspace-text-faint)]">To</label>
            <input
              ref={recipientRef}
              id="microsoft-compose-preview-to"
              type="text"
              inputMode="email"
              autoComplete="off"
              placeholder="Recipients"
              className={inputClass}
            />
            <button
              type="button"
              aria-expanded={showCc}
              onClick={() => setShowCc((value) => !value)}
              className="rounded-full px-2 py-1 text-xs font-medium text-[var(--workspace-text-soft)] hover:bg-[var(--workspace-hover-surface)]"
            >
              Cc
            </button>
            <button
              type="button"
              aria-expanded={showBcc}
              onClick={() => setShowBcc((value) => !value)}
              className="rounded-full px-2 py-1 text-xs font-medium text-[var(--workspace-text-soft)] hover:bg-[var(--workspace-hover-surface)]"
            >
              Bcc
            </button>
          </div>
          {showCc && (
            <div className="flex min-h-[48px] items-center gap-2 border-b border-[var(--workspace-border-soft)] py-1">
              <label htmlFor="microsoft-compose-preview-cc" className="w-14 shrink-0 text-sm text-[var(--workspace-text-faint)]">Cc</label>
              <input id="microsoft-compose-preview-cc" type="text" inputMode="email" autoComplete="off" placeholder="Copy recipients" className={inputClass} />
            </div>
          )}
          {showBcc && (
            <div className="flex min-h-[48px] items-center gap-2 border-b border-[var(--workspace-border-soft)] py-1">
              <label htmlFor="microsoft-compose-preview-bcc" className="w-14 shrink-0 text-sm text-[var(--workspace-text-faint)]">Bcc</label>
              <input id="microsoft-compose-preview-bcc" type="text" inputMode="email" autoComplete="off" placeholder="Blind copy recipients" className={inputClass} />
            </div>
          )}
          <div className="border-b border-[var(--workspace-border-soft)] py-1">
            <label htmlFor="microsoft-compose-preview-subject" className="sr-only">Subject</label>
            <input id="microsoft-compose-preview-subject" type="text" maxLength={250} autoComplete="off" placeholder="Subject" className={"w-full " + inputClass} />
          </div>
          <label htmlFor="microsoft-compose-preview-body" className="sr-only">Message body</label>
          <textarea
            id="microsoft-compose-preview-body"
            className="min-h-[260px] w-full flex-1 resize-none bg-transparent py-5 text-[0.92rem] leading-7 text-[var(--workspace-text)] placeholder:text-[var(--workspace-text-faint)] outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--workspace-accent-border)]"
            placeholder="Write your message…"
            spellCheck
          />
        </div>

        <footer className="flex flex-none flex-wrap items-center justify-between gap-3 border-t border-[var(--workspace-border-soft)] px-5 py-4 sm:px-6">
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled
              title="Preview only — sending is not enabled."
              className="inline-flex h-9 items-center gap-2 rounded-full border border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] px-5 text-xs font-semibold text-[var(--workspace-accent-text)] disabled:cursor-not-allowed disabled:opacity-55"
            >
              <Glyph name="send" />
              Send
            </button>
            {(["attach", "link", "format", "smile"] as const).map((iconName) => (
              <button
                key={iconName}
                type="button"
                disabled
                aria-label={
                  iconName === "attach" ? "Attach file (preview only)" :
                  iconName === "link" ? "Insert link (preview only)" :
                  iconName === "format" ? "Formatting options (preview only)" :
                  "Insert emoji (preview only)"
                }
                title="Not enabled during the design preview"
                className={iconButtonClass}
              >
                <Glyph name={iconName} />
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={onClose}
            title="Discard the visual preview (nothing was saved)"
            className={iconButtonClass}
            aria-label="Discard compose design preview"
          >
            <Glyph name="discard" />
          </button>
        </footer>
        <p className="border-t border-[var(--workspace-border-soft)] px-5 py-2 text-[0.68rem] text-[var(--workspace-text-faint)] sm:px-6">
          Nothing is sent or saved. This window is for choosing the layout and icons.
        </p>
      </section>
    </div>
  );

  return typeof document === "undefined" ? preview : createPortal(preview, document.body);
}
