import React, { useEffect, useRef, useState } from "react";
import { normalizeSenderLearningKey } from "../../lib/learningEngine";
import type {
  MicrosoftLearningLabel,
  MicrosoftLearningRule,
} from "./microsoftLearningRules";

const labels: MicrosoftLearningLabel[] = [
  "Demo", "Promo", "Business", "Finance", "Update", "Reply", "Other",
];

const menuItemClass =
  "flex w-full items-center justify-between gap-2 rounded-[12px] px-3 py-2.5 text-left text-[0.79rem] text-[var(--workspace-text)] transition hover:bg-[var(--workspace-hover-surface)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--workspace-accent-border)]";

export function MicrosoftLearningControls({
  from, rule, disabled, canShowLess, onSave, openOnRequest = false,
}: {
  from: string;
  rule?: MicrosoftLearningRule;
  disabled: boolean;
  canShowLess: boolean;
  onSave: (rule: MicrosoftLearningRule) => boolean;
  openOnRequest?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [showLabels, setShowLabels] = useState(false);
  const [error, setError] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const sender = normalizeSenderLearningKey(from);
  const validSender = /^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$/.test(sender);

  useEffect(() => {
    setOpen(false);
    setShowLabels(false);
    setError(false);
  }, [from]);
  useEffect(() => {
    if (openOnRequest && !disabled && validSender) setOpen(true);
  }, [openOnRequest, disabled, validSender]);
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

  const commit = (next: MicrosoftLearningRule) => {
    if (onSave(next)) {
      setOpen(false);
      setShowLabels(false);
      setError(false);
    } else {
      setError(true);
    }
  };
  const priority = rule?.priority ?? "normal";
  const label = rule?.label ?? "Other";

  return (
    <div className="relative ml-auto flex shrink-0 justify-end" ref={menuRef} data-microsoft-learning-controls>
      <button type="button" aria-label="Learning" aria-expanded={open} aria-haspopup="menu"
        data-reading-learning-trigger="microsoft"
        disabled={disabled || !validSender}
        onClick={() => { setOpen((current) => !current); setShowLabels(false); setError(false); }}
        className="inline-flex h-8 items-center justify-center rounded-full border border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] px-3.5 text-[0.66rem] font-medium uppercase tracking-[0.12em] text-[var(--workspace-accent-text)] disabled:cursor-not-allowed disabled:opacity-50">
        Learning
      </button>
      {open && (
        <div role="menu" aria-label="Microsoft Learning"
          className="absolute right-0 top-10 z-40 w-[244px] max-h-[min(500px,70dvh)] overflow-y-auto rounded-[20px] border border-[var(--workspace-menu-border)] bg-[var(--workspace-menu-bg)] p-2 shadow-panel">
          <div className="space-y-1">
            <button type="button" role="menuitem" className={menuItemClass}
              onClick={() => commit({ priority: "important", label })}>
              <span>Mark as important</span>
              {rule?.priority === "important" && <span aria-label="Current priority">✓</span>}
            </button>
            <button type="button" role="menuitem" className={menuItemClass}
              onClick={() => commit({ priority: "normal", label })}>
              <span>Mark as not important</span>
              {rule?.priority === "normal" && <span aria-label="Current priority">✓</span>}
            </button>
            <button type="button" role="menuitem" aria-expanded={showLabels}
              className={menuItemClass}
              onClick={() => setShowLabels((current) => !current)}>
              <span>Change label...</span>
              <span aria-hidden="true">{showLabels ? "⌃" : "⌄"}</span>
            </button>
            {showLabels && (
              <div aria-label="Label choices" className="space-y-1 border-l border-[var(--workspace-divider)] pl-2">
                {labels.map((choice) => (
                  <button type="button" role="menuitem" key={choice}
                    onClick={() => commit({ priority: choice === "Reply" ? "important" : priority, label: choice })}
                    className={menuItemClass}>
                    <span>{choice}</span>
                    {rule?.label === choice && <span aria-label="Current label">✓</span>}
                  </button>
                ))}
              </div>
            )}
            <button type="button" role="menuitem" className={menuItemClass}
              onClick={() => commit({ priority: priority === "show_less" ? "normal" : priority, label })}>
              Keep in inbox
            </button>
            <button type="button" role="menuitem" disabled
              title="Moving to spam is not yet available for Microsoft. No Outlook message is changed."
              className={menuItemClass + " cursor-not-allowed opacity-45"}>
              Move to spam
            </button>
            {rule?.priority === "show_less" && (
              <p className="px-3 py-1 text-[0.72rem] text-[var(--workspace-text-muted)]">
                Currently set to Show Less
              </p>
            )}
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
