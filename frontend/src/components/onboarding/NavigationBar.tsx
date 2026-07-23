import { onboardingText } from "../../copy/onboardingCopy";

export function invokeNavigationAction(
  disabled: boolean,
  action: () => void,
) {
  if (disabled) {
    return false;
  }
  action();
  return true;
}

interface NavigationBarProps {
  canGoBack: boolean;
  backLabel?: string;
  nextLabel: string;
  onBack: () => void;
  onNext: () => void;
  isBackDisabled?: boolean;
  isNextDisabled?: boolean;
}

export function NavigationBar({
  canGoBack,
  backLabel = onboardingText.navigation.back,
  nextLabel,
  onBack,
  onNext,
  isBackDisabled = false,
  isNextDisabled = false,
}: NavigationBarProps) {
  const primaryActionClass =
    "rounded-full bg-pine px-6 py-3 text-sm font-semibold text-white transition hover:bg-moss active:scale-[0.99]";

  return (
    <div className="flex items-center justify-between border-t border-ink/10 pt-6">
      {canGoBack ? (
        <button
          type="button"
          data-attempt-control="back"
          onClick={() =>
            invokeNavigationAction(isBackDisabled, onBack)
          }
          disabled={isBackDisabled}
          className={`${primaryActionClass} disabled:cursor-not-allowed disabled:bg-ink/30 disabled:hover:bg-ink/30`}
        >
          {backLabel}
        </button>
      ) : (
        <div />
      )}
      <button
        type="button"
        data-attempt-control="next"
        onClick={onNext}
        disabled={isNextDisabled}
        className={`${primaryActionClass} disabled:cursor-not-allowed disabled:bg-ink/30 disabled:hover:bg-ink/30`}
      >
        {nextLabel}
      </button>
    </div>
  );
}
