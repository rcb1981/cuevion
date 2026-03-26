import { onboardingText } from "../../copy/onboardingCopy";

interface NavigationBarProps {
  canGoBack: boolean;
  backLabel?: string;
  nextLabel: string;
  onBack: () => void;
  onNext: () => void;
  isNextDisabled?: boolean;
}

export function NavigationBar({
  canGoBack,
  backLabel = onboardingText.navigation.back,
  nextLabel,
  onBack,
  onNext,
  isNextDisabled = false,
}: NavigationBarProps) {
  const primaryActionClass =
    "rounded-full bg-pine px-6 py-3 text-sm font-semibold text-white transition hover:bg-moss active:scale-[0.99]";

  return (
    <div className="flex items-center justify-between border-t border-ink/10 pt-6">
      {canGoBack ? (
        <button
          type="button"
          onClick={onBack}
          className={primaryActionClass}
        >
          {backLabel}
        </button>
      ) : (
        <div />
      )}
      <button
        type="button"
        onClick={onNext}
        disabled={isNextDisabled}
        className={`${primaryActionClass} disabled:cursor-not-allowed disabled:bg-ink/30 disabled:hover:bg-ink/30`}
      >
        {nextLabel}
      </button>
    </div>
  );
}
