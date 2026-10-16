import React from "react";

export function MicrosoftFilteredSenderAction({
  folder,
  disabled,
  onChange,
}: {
  folder: "Inbox" | "Filtered";
  disabled: boolean;
  onChange: (placement: "show_less" | "keep") => void;
}) {
  const placement = folder === "Inbox" ? "show_less" : "keep";
  const text = folder === "Inbox" ? "Show less from this sender" : "Keep this sender in Inbox";
  return (
    <section aria-label="Microsoft sender filtering" className="mt-3 flex flex-wrap items-center gap-2">
      <button
        type="button"
        disabled={disabled}
        onClick={() => onChange(placement)}
        title="Change Cuevion display only. No message is moved in Outlook."
        className="rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-3 py-1.5 text-xs font-medium text-[var(--workspace-text)] hover:border-[var(--workspace-border)] disabled:cursor-not-allowed disabled:opacity-50"
      >
        {text}
      </button>
      <span className="text-[0.68rem] text-[var(--workspace-text-muted)]">
        Affects this Microsoft mailbox only. Outlook stays unchanged.
      </span>
    </section>
  );
}
