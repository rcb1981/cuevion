import { onboardingText } from "../../copy/onboardingCopy";

interface StepCompleteProps {
  connectedInboxCount: number;
}

export function StepComplete({ connectedInboxCount }: StepCompleteProps) {
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
        <p className="text-[13px] leading-7 text-ink/42">
          {onboardingText.complete.summary(connectedInboxCount)}
        </p>
      </div>
    </section>
  );
}
