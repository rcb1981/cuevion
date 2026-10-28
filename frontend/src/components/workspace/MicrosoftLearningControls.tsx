import React, { useEffect, useRef, useState } from "react";
import type {
  MicrosoftLearningLabel,
  MicrosoftMessageLearningRule,
} from "./microsoftLearningRules";

const labels: MicrosoftLearningLabel[] = [
  "Promo", "Update", "Finance", "Business", "Demo", "Other",
];

const menuItemClass =
  "flex w-full items-center justify-between gap-2 rounded-[12px] px-3 py-2.5 text-left text-[0.79rem] text-[var(--workspace-text)] transition hover:bg-[var(--workspace-hover-surface)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--workspace-accent-border)]";

export function MicrosoftLearningControls({
  from, rule, disabled, canShowLess, onSave, onKeepInInbox, openOnRequest = false,
}: {
  from: string;
  rule?: MicrosoftMessageLearningRule;
  disabled: boolean;
  canShowLess: boolean;
  onSave: (update: MicrosoftMessageLearningRule) => boolean;
  onKeepInInbox: () => boolean;
  openOnRequest?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [showLabels, setShowLabels] = useState(false);
  const [error, setError] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setOpen(false);
    setShowLabels(false);
    setError(false);
  }, [from]);
  useEffect(() => {
    if (openOnRequest && !disabled) setOpen(true);
  }, [openOnRequest, disabled]);
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) {
        setOpen(false);
        setShowLabels(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        setShowLabels(false);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const finish = (saved: boolean) => {
    if (saved) {
      setOpen(false);
      setShowLabels(false);
      setError(false);
    } else {
      setError(true);
    }
  };
  const commit = (next: MicrosoftMessageLearningRule) => finish(onSave(next));

  return (
    <div className="relative ml-auto flex shrink-0 justify-end" ref={menuRef} data-microsoft-learning-controls>
      <button type="button" aria-label="Learning" aria-expanded={open} aria-haspopup="menu"
        data-reading-learning-trigger="microsoft"
        disabled={disabled}
        onClick={() => { setOpen((current) => !current); setShowLabels(false); setError(false); }}
        className="inline-flex h-8 items-center justify-center rounded-full border border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] px-3.5 text-[0.66rem] font-medium uppercase tracking-[0.12em] text-[var(--workspace-accent-text)] disabled:cursor-not-allowed disabled:opacity-50">
        Learning
      </button>
      {open && (
        <div role="menu" aria-label="Microsoft Learning"
          className="absolute right-0 top-10 z-40 w-[244px] max-h-[min(500px,70dvh)] overflow-y-auto rounded-[20px] border border-[var(--workspace-menu-border)] bg-[var(--workspace-menu-bg)] p-2 shadow-panel">
          <div className="space-y-1">
            <button type="button" role="menuitem" className={menuItemClass}
              onClick={() => commit({ priority: "important" })}>
              <span>Mark as important</span>
              {rule?.priority === "important" && <span aria-label="Current priority">✓</span>}
            </button>
            <button type="button" role="menuitem" className={menuItemClass}
              onClick={() => commit({ priority: "normal" })}>
              <span>Mark as not important</span>
              {rule?.priority === "normal" && <span aria-label="Current priority">✓</span>}
            </button>
            <button type="button" role="menuitem" aria-expanded={showLabels}
              className={menuItemClass}
              onClick={() => setShowLabels((current) => !current)}>
              <span>Change label...</span>
            </button>
            {showLabels && (
              <div aria-label="Label choices" className="space-y-1 border-l border-[var(--workspace-divider)] pl-2">
                {labels.map((choice) => (
                  <button type="button" role="menuitem" key={choice}
                    onClick={() => commit({ label: choice })}
                    className={menuItemClass}>
                    <span>{choice}</span>
                    {rule?.label === choice && <span aria-label="Current label">✓</span>}
                  </button>
                ))}
              </div>
            )}
            <button type="button" role="menuitem" className={menuItemClass}
              onClick={() => finish(onKeepInInbox())}>
              Keep in inbox
            </button>
            <button type="button" role="menuitem" disabled
              title="Moving to spam is not yet available for Microsoft. No Outlook message is changed."
              className={menuItemClass + " cursor-not-allowed opacity-45"}>
              Move to spam
            </button>
            {!canShowLess && (
              <p className="px-3 py-1 text-[0.7rem] text-[var(--workspace-text-muted)]">
                Protected messages remain in Inbox.
              </p>
            )}
            {error && <p role="alert" className="px-3 py-1 text-xs text-[var(--workspace-text)]">Could not save Learning.</p>}
          </div>
        </div>
      )}
    </div>
  );
}
