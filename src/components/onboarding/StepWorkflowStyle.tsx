import { useState } from "react";
import { workflowStyleOptions } from "../../data/onboardingOptions";
import { onboardingText } from "../../copy/onboardingCopy";
import type { WorkflowStyleId } from "../../types/onboarding";

interface StepWorkflowStyleProps {
  value: WorkflowStyleId | null;
  onChange: (value: WorkflowStyleId) => void;
}

export function StepWorkflowStyle({
  value,
  onChange,
}: StepWorkflowStyleProps) {
  const [openTooltipId, setOpenTooltipId] = useState<WorkflowStyleId | null>(null);

  return (
    <section className="space-y-8">
      <div className="space-y-3">
        <h2 className="text-3xl font-semibold tracking-tight text-ink">
          {onboardingText.workflowStyle.title}
        </h2>
        <p className="max-w-2xl text-base leading-7 text-ink/68">
          {onboardingText.workflowStyle.description}
        </p>
      </div>

      <div className="flex justify-center">
        <div className="flex w-full max-w-[35rem] flex-col gap-4">
          {workflowStyleOptions.map((option) => {
            const selected = value === option.id;
            const showTooltip = openTooltipId === option.id;

            return (
              <div key={option.id} className="relative">
                <button
                  type="button"
                  onClick={() => onChange(option.id)}
                  className={`min-h-[84px] w-full rounded-3xl border px-5 py-4 text-left transition ${
                    selected
                      ? "border-pine bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))] text-ink shadow-panel"
                      : "border-ink/10 bg-white/80 text-ink hover:border-moss/35"
                  } outline-none focus-visible:border-pine focus-visible:bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))] focus-visible:text-ink focus-visible:shadow-panel`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <span className="text-base font-semibold">{option.label}</span>
                      <p className="mt-2 text-sm leading-6 text-ink/62">
                        {option.description}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      {option.recommended ? (
                        <span className="rounded-full border border-ink/8 bg-white/55 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink/46">
                          {onboardingText.workflowStyle.recommended}
                        </span>
                      ) : null}
                      <div
                        className="relative"
                        onMouseEnter={() => setOpenTooltipId(option.id)}
                        onMouseLeave={() =>
                          setOpenTooltipId((current) =>
                            current === option.id ? null : current,
                          )
                        }
                      >
                        <button
                          type="button"
                          aria-label={`${option.label} information`}
                          aria-expanded={showTooltip}
                          onClick={(event) => {
                            event.stopPropagation();
                            setOpenTooltipId((current) =>
                              current === option.id ? null : option.id,
                            );
                          }}
                          className="flex h-5 w-5 items-center justify-center rounded-full border border-ink/10 bg-white/45 text-[10px] font-medium text-ink/38 outline-none transition hover:border-ink/15 hover:text-ink/52 focus-visible:border-pine focus-visible:bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))] focus-visible:text-ink"
                        >
                          i
                        </button>
                        <div
                          className={`absolute right-0 top-full z-10 mt-3 w-[18rem] rounded-3xl border border-ink/8 bg-white/95 p-4 shadow-panel transition-all duration-200 ${
                            showTooltip
                              ? "pointer-events-auto translate-y-0 opacity-100"
                              : "pointer-events-none -translate-y-1 opacity-0"
                          }`}
                        >
                          <div className="space-y-1">
                            <div className="text-sm font-semibold text-ink">
                              {option.label}
                            </div>
                            <p className="text-sm leading-6 text-ink/62">
                              {option.tooltip}
                            </p>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
