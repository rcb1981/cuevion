import { onboardingText } from "../../copy/onboardingCopy";

interface ProgressIndicatorProps {
  currentStep: number;
  totalSteps: number;
  variant?: "default" | "sidebar";
  sidebarLabel?: string;
}

export function ProgressIndicator({
  currentStep,
  totalSteps,
  variant = "default",
  sidebarLabel,
}: ProgressIndicatorProps) {
  const progress = Math.max(0, Math.min((currentStep / totalSteps) * 100, 100));
  const isSidebar = variant === "sidebar";

  return (
    <div className="space-y-3">
      {!isSidebar ? (
        <div className="flex items-center justify-between text-xs uppercase tracking-[0.24em] text-ink/55">
          <span>{onboardingText.sidebar.progressLabel}</span>
          <span>
            Step {currentStep + 1} of {totalSteps}
          </span>
        </div>
      ) : null}
      {isSidebar ? (
        <div className="text-[10px] font-medium uppercase tracking-[0.22em] text-white/38">
          {sidebarLabel ?? onboardingText.sidebar.progressStep(currentStep, totalSteps)}
        </div>
      ) : null}
      <div
        className={`overflow-hidden rounded-full ${
          isSidebar ? "h-1 bg-black/20" : "h-2 bg-ink/10"
        }`}
      >
        <div
          className={`h-full rounded-full transition-all duration-300 ${
            isSidebar
              ? "bg-gradient-to-r from-[#dec1a6] to-[#cea783]"
              : "bg-gradient-to-r from-moss to-clay"
          }`}
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  );
}
