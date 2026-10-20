import React, { useEffect, useState } from "react";
import { normalizeSenderLearningKey } from "../../lib/learningEngine";
import type {
  MicrosoftLearningLabel,
  MicrosoftLearningPriority,
  MicrosoftLearningRule,
} from "./microsoftLearningRules";

const labels: MicrosoftLearningLabel[] = [
  "Demo", "Promo", "Business", "Finance", "Update", "Reply", "Other",
];
const priorities: Array<{ id: MicrosoftLearningPriority; label: string }> = [
  { id: "important", label: "Important" },
  { id: "normal", label: "Normal" },
  { id: "show_less", label: "Show Less" },
];

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
  const [priority, setPriority] = useState<MicrosoftLearningPriority>(rule?.priority ?? "normal");
  const [label, setLabel] = useState<MicrosoftLearningLabel>(rule?.label ?? "Other");
  const [error, setError] = useState(false);
  const sender = normalizeSenderLearningKey(from);
  const validSender = /^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$/.test(sender);
  useEffect(() => {
    setOpen(false);
    setPriority(rule?.priority ?? "normal");
    setLabel(rule?.label ?? "Other");
    setError(false);
  }, [from, rule?.priority, rule?.label]);
  useEffect(() => {
    if (openOnRequest && !disabled) setOpen(true);
  }, [openOnRequest, disabled]);

  return (
    <div className="relative ml-auto flex shrink-0 justify-end" data-microsoft-learning-controls>
      <button type="button" aria-label="Learning" aria-expanded={open} aria-haspopup="dialog"
        disabled={disabled || !validSender}
        onClick={() => setOpen((current) => !current)}
        className="inline-flex h-8 items-center justify-center rounded-full border border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] px-3.5 text-[0.66rem] font-medium uppercase tracking-[0.12em] text-[var(--workspace-accent-text)] disabled:cursor-not-allowed disabled:opacity-50">
        Learning
      </button>
      {open && (
        <div role="dialog" aria-label="Microsoft sender learning" className="absolute right-0 top-10 z-40 w-[min(320px,85vw)] rounded-[18px] border border-[var(--workspace-menu-border)] bg-[var(--workspace-menu-bg)] p-4 text-[var(--workspace-text)] shadow-[0_18px_42px_rgba(20,18,16,0.3)]">
          <div className="flex items-center justify-between gap-3">
            <strong className="text-sm">Learn from this sender</strong>
            <button type="button" aria-label="Close Learning" onClick={() => setOpen(false)} className="text-sm">✕</button>
          </div>
          <p className="mt-2 break-all text-xs text-[var(--workspace-text-muted)]">{sender}</p>
          <fieldset className="mt-4">
            <legend className="mb-2 text-xs font-medium text-[var(--workspace-text-soft)]">Priority</legend>
            <div className="flex flex-wrap gap-2">
              {priorities.map((option) => (
                <label key={option.id} className="flex cursor-pointer items-center gap-1.5 rounded-full border border-[var(--workspace-border-soft)] px-2.5 py-1.5 text-xs">
                  <input type="radio" name={"ms-learning-priority-"+sender} value={option.id}
                    checked={priority === option.id}
                    disabled={option.id === "show_less" && !canShowLess}
                    onChange={() => setPriority(option.id)} />
                  {option.label}
                </label>
              ))}
            </div>
            {!canShowLess && <p className="mt-2 text-xs text-[var(--workspace-text-muted)]">This message is protected from being filtered.</p>}
          </fieldset>
          <label className="mt-4 block text-xs font-medium text-[var(--workspace-text-soft)]">
            Category
            <select value={label} onChange={(event) => setLabel(event.target.value as MicrosoftLearningLabel)}
              className="mt-2 w-full rounded-xl border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-3 py-2 text-sm text-[var(--workspace-text)]">
              {labels.map((option) => <option key={option} value={option}>{option}</option>)}
            </select>
          </label>
          <p className="mt-3 text-xs leading-5 text-[var(--workspace-text-muted)]">
            Saves a Cuevion preference for this Microsoft mailbox. No message is moved or changed in Outlook.
          </p>
          {error && <p role="alert" className="mt-2 text-xs">Could not save this rule.</p>}
          <div className="mt-4 flex justify-end gap-2">
            <button type="button" className="rounded-full px-3 py-2 text-xs" onClick={() => setOpen(false)}>Cancel</button>
            <button type="button" className="rounded-full border border-[var(--workspace-accent-border)] bg-[var(--workspace-accent-surface-start)] px-3 py-2 text-xs font-semibold text-[var(--workspace-accent-text)]"
              onClick={() => {
                if (priority === "show_less" && !canShowLess) return;
                if (onSave({ priority, label })) setOpen(false);
                else setError(true);
              }}>Save Learning</button>
          </div>
        </div>
      )}
    </div>
  );
}
