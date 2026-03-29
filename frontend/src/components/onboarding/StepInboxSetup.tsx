import { useEffect, useRef, useState } from "react";
import type { CustomInboxDefinition, InboxId } from "../../types/onboarding";

interface StepInboxSetupProps {
  primaryInbox: InboxId | null;
  selectedInboxes: InboxId[];
  availableInboxOptions: Array<{ id: InboxId; label: string }>;
  customInboxes: CustomInboxDefinition[];
  maxActiveInboxCount: number;
  requiredInboxCount: number;
  onPrimaryInboxChange: (inboxId: InboxId) => void;
  onToggleAdditionalInbox: (inboxId: InboxId) => void;
  onAddCustomInbox: (name: string) => boolean;
}

export function StepInboxSetup({
  primaryInbox,
  selectedInboxes,
  availableInboxOptions,
  customInboxes,
  maxActiveInboxCount,
  requiredInboxCount,
  onPrimaryInboxChange,
  onToggleAdditionalInbox,
  onAddCustomInbox,
}: StepInboxSetupProps) {
  const [isAddingCustomInbox, setIsAddingCustomInbox] = useState(false);
  const [customInboxName, setCustomInboxName] = useState("");
  const [customInboxError, setCustomInboxError] = useState<string | null>(null);
  const [showLimitHint, setShowLimitHint] = useState(false);
  const limitHintTimerRef = useRef<number | null>(null);
  const customInboxInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    return () => {
      if (limitHintTimerRef.current !== null) {
        window.clearTimeout(limitHintTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!isAddingCustomInbox) {
      return;
    }

    customInboxInputRef.current?.focus();
  }, [isAddingCustomInbox]);

  const showTemporaryLimitHint = () => {
    setShowLimitHint(true);

    if (limitHintTimerRef.current !== null) {
      window.clearTimeout(limitHintTimerRef.current);
    }

    limitHintTimerRef.current = window.setTimeout(() => {
      setShowLimitHint(false);
      limitHintTimerRef.current = null;
    }, 2500);
  };

  const renderPrimaryCard = (inboxId: InboxId, label: string) => {
    const selected = primaryInbox === inboxId;

    return (
      <button
        key={inboxId}
        type="button"
        onClick={() => {
          setShowLimitHint(false);
          onPrimaryInboxChange(inboxId);
        }}
        className={`rounded-3xl border p-5 text-left transition ${
          selected
            ? "border-pine bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))] text-ink shadow-panel"
            : "border-ink/10 bg-white/80 text-ink hover:border-moss/35"
        } cursor-pointer outline-none focus-visible:border-pine focus-visible:bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))] focus-visible:text-ink focus-visible:shadow-panel`}
      >
        <div className="flex items-center justify-between gap-4">
          <span className="text-base font-semibold">{label}</span>
          {selected ? (
            <span className="rounded-full border border-ink/8 bg-white/55 px-2.5 py-1 text-[10px] uppercase tracking-[0.18em] text-ink/46">
              Primary
            </span>
          ) : null}
        </div>
      </button>
    );
  };

  const renderAdditionalCard = (inboxId: InboxId, label: string) => {
    const selected = selectedInboxes.includes(inboxId);
    const maxReached = selectedInboxes.length >= maxActiveInboxCount;
    const disabled = !selected && maxReached;

    return (
      <button
        key={inboxId}
        type="button"
        aria-disabled={disabled}
        onClick={() => {
          if (disabled) {
            showTemporaryLimitHint();
            return;
          }
          setShowLimitHint(false);
          onToggleAdditionalInbox(inboxId);
        }}
        className={`rounded-3xl border p-5 text-left transition ${
          selected
            ? "border-pine bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))] text-ink shadow-panel"
            : "border-ink/10 bg-white/80 text-ink hover:border-moss/35"
        } ${disabled ? "cursor-default opacity-55 hover:border-ink/10" : ""} ${
          !disabled ? "cursor-pointer" : ""
        } outline-none focus-visible:border-pine focus-visible:bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))] focus-visible:text-ink focus-visible:shadow-panel`}
      >
        <div className="flex items-center justify-between gap-4">
          <span className="text-base font-semibold">{label}</span>
        </div>
      </button>
    );
  };

  const submitCustomInbox = () => {
    const trimmedName = customInboxName.trim();

    if (!trimmedName) {
      setCustomInboxError("Enter an inbox name.");
      return;
    }

    const added = onAddCustomInbox(trimmedName);

    if (!added) {
      showTemporaryLimitHint();
      return;
    }

    setCustomInboxName("");
    setCustomInboxError(null);
    setShowLimitHint(false);
    setIsAddingCustomInbox(false);
  };

  const showMinimumHint = selectedInboxes.length < requiredInboxCount;

  return (
    <section className="space-y-8">
      <div className="space-y-3">
        <h2 className="text-3xl font-semibold tracking-tight text-ink">Inbox setup</h2>
        <p className="text-base text-ink/68">
          Choose the inboxes you want Cuevion to organize. At least 1 inbox is required.
        </p>
      </div>

      <div className="space-y-3">
        <div className="space-y-1">
          <h3 className="text-lg font-medium text-ink">Primary inbox</h3>
          <p className="text-sm text-ink/52">
            Choose the inbox that should anchor your workspace.
          </p>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          {availableInboxOptions.map((option) =>
            renderPrimaryCard(option.id, option.label),
          )}
        </div>
      </div>

      <div
        className={`min-h-[20px] text-sm text-ink/48 transition-opacity ${
          showLimitHint ? "duration-300" : "duration-1000"
        } ${
          showLimitHint || showMinimumHint ? "opacity-100" : "opacity-0"
        }`}
      >
        {showLimitHint
          ? "To add more inboxes, go back and choose a larger setup."
          : showMinimumHint
            ? `Select at least ${requiredInboxCount} inboxes to continue.`
            : ""}
      </div>

      <div className="space-y-3 rounded-[28px] border border-ink/10 bg-sand/45 px-5 py-6">
        <div className="space-y-1">
          <h3 className="text-lg font-medium text-ink">Additional inboxes</h3>
          <p className="text-sm text-ink/52">
            Add any other inboxes you want Cuevion to organize alongside your primary inbox.
          </p>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          {availableInboxOptions
            .filter((option) => option.id !== primaryInbox)
            .map((option) => renderAdditionalCard(option.id, option.label))}
        </div>

        {isAddingCustomInbox ? (
          <div className="rounded-3xl border border-ink/10 bg-white/82 p-5 shadow-panel">
            <div className="flex flex-col gap-3 md:flex-row md:items-center">
              <input
                ref={customInboxInputRef}
                type="text"
                value={customInboxName}
                onChange={(event) => {
                  setCustomInboxName(event.target.value);
                  setCustomInboxError(null);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    submitCustomInbox();
                  }

                  if (event.key === "Escape") {
                    event.preventDefault();
                    setCustomInboxName("");
                    setCustomInboxError(null);
                    setIsAddingCustomInbox(false);
                  }
                }}
                placeholder="Inbox name"
                className="w-full rounded-2xl border border-ink/10 bg-white px-4 py-3 text-ink outline-none transition focus:border-moss"
              />
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={submitCustomInbox}
                  className="inline-flex h-11 items-center justify-center rounded-full border border-pine/35 bg-[linear-gradient(180deg,rgba(103,141,103,0.18),rgba(69,103,72,0.16))] px-5 text-[0.72rem] font-medium uppercase tracking-[0.16em] text-ink transition hover:border-pine/45"
                >
                  Add inbox
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setCustomInboxName("");
                    setCustomInboxError(null);
                    setIsAddingCustomInbox(false);
                  }}
                  className="text-sm font-medium text-ink/56 transition hover:text-ink/76"
                >
                  Cancel
                </button>
              </div>
            </div>
            {customInboxError ? (
              <div className="mt-3 text-sm text-amber-900/70">
                {customInboxError}
              </div>
            ) : null}
          </div>
        ) : (
          <button
            type="button"
            onClick={() => {
              setCustomInboxError(null);
              setIsAddingCustomInbox(true);
            }}
            className="flex w-full items-center justify-center rounded-3xl border border-dashed border-ink/12 bg-white/76 px-5 py-4 text-sm font-medium text-ink/66 transition hover:border-moss/35 hover:text-ink"
          >
            + Add custom inbox
          </button>
        )}
      </div>
    </section>
  );
}
