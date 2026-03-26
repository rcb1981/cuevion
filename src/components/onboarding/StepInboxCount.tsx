import { inboxCountOptions } from "../../data/onboardingOptions";
import { onboardingText } from "../../copy/onboardingCopy";
import type { InboxCountId } from "../../types/onboarding";

interface StepInboxCountProps {
  value: InboxCountId | null;
  onChange: (value: InboxCountId) => void;
}

export function StepInboxCount({ value, onChange }: StepInboxCountProps) {
  const renderOptionCard = (option: (typeof inboxCountOptions)[number]) => (
    <button
      key={option.id}
      type="button"
      onClick={() => onChange(option.id)}
      className={`min-h-[60px] rounded-3xl border px-5 py-4 text-left transition ${
        value === option.id
          ? "border-pine bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))] text-ink shadow-panel"
          : "border-ink/10 bg-white/80 text-ink hover:border-moss/35"
      } outline-none focus-visible:border-pine focus-visible:bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))] focus-visible:text-ink focus-visible:shadow-panel`}
    >
      <span className="text-base font-semibold">{option.label}</span>
    </button>
  );

  return (
    <section className="space-y-11">
      <div className="space-y-3">
        <h2 className="text-3xl font-semibold tracking-tight text-ink">
          {onboardingText.inboxCount.title}
        </h2>
      </div>
      <div className="flex justify-center">
        <div className="flex w-full max-w-[35rem] flex-col gap-4">
          {inboxCountOptions.map(renderOptionCard)}
        </div>
      </div>
    </section>
  );
}
