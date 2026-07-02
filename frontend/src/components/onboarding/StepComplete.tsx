import { onboardingText } from "../../copy/onboardingCopy";

interface StepCompleteProps {
  connectedInboxCount: number;
  priorityFocusLabels: string[];
}

export function StepComplete({
  connectedInboxCount,
  priorityFocusLabels,
}: StepCompleteProps) {
  return (
    <section className="space-y-6 py-10">
      <span className="inline-flex rounded-full bg-moss/10 px-4 py-2 text-xs uppercase tracking-[0.28em] text-moss">
        {onboardingText.complete.badge}
      </span>
      <div className="max-w-2xl space-y-4">
        <h2 className="text-4xl font-semibold tracking-tight text-ink md:text-5xl">
          {onboardingText.complete.title}
        </h2>
        <p className="text-lg leading-8 text-ink/70">
          {onboardingText.complete.text}
        </p>
        <div className="grid gap-3 pt-2 md:grid-cols-2">
          <div className="rounded-[22px] border border-ink/10 bg-white/72 px-4 py-3">
            <div className="text-[0.7rem] font-semibold uppercase tracking-[0.16em] text-ink/42">
              Priority focus
            </div>
            <p className="mt-2 text-sm leading-6 text-ink/68">
              {priorityFocusLabels.length > 0
                ? priorityFocusLabels.join(", ")
                : "No priority focus selected"}
            </p>
          </div>
          <div className="rounded-[22px] border border-ink/10 bg-white/72 px-4 py-3">
            <div className="text-[0.7rem] font-semibold uppercase tracking-[0.16em] text-ink/42">
              Connected inboxes
            </div>
            <p className="mt-2 text-sm leading-6 text-ink/68">
              {connectedInboxCount}
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
