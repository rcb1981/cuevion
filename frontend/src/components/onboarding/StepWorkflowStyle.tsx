import { onboardingText } from "../../copy/onboardingCopy";

type LegacyWorkflowStyleId = "quiet" | "balanced" | "active";

interface StepWorkflowStyleProps {
  value: LegacyWorkflowStyleId | null;
  onChange: (value: LegacyWorkflowStyleId) => void;
}

export function StepWorkflowStyle({
  value: _value,
  onChange: _onChange,
}: StepWorkflowStyleProps) {
  return (
    <section className="space-y-6">
      <div className="space-y-3">
        <h2 className="text-3xl font-semibold tracking-tight text-ink">
          {onboardingText.workflowStyle.title}
        </h2>
        <p className="max-w-2xl text-base leading-7 text-ink/68">
          {onboardingText.workflowStyle.description}
        </p>
      </div>

      <div className="rounded-3xl border border-ink/10 bg-white/80 px-5 py-4 text-sm leading-6 text-ink/62 shadow-panel">
        This legacy component is kept only for compatibility while workflow style
        stays implicitly balanced outside the active onboarding flow.
      </div>
    </section>
  );
}
