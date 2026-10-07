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
  const backNavigationClass =
    "rounded-full border border-[rgba(199,166,104,0.42)] bg-white/72 px-6 py-3 text-sm font-semibold text-[rgba(29,58,48,0.84)] shadow-[inset_0_1px_0_rgba(255,255,255,0.7)] transition-[background-color,border-color,transform,box-shadow] duration-150 hover:-translate-y-px hover:border-[rgba(199,166,104,0.64)] hover:bg-[rgba(237,222,184,0.38)] active:translate-y-0 active:scale-[0.99] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(218,194,142,0.55)] focus-visible:ring-offset-2 focus-visible:ring-offset-white disabled:cursor-not-allowed disabled:border-ink/10 disabled:bg-white/55 disabled:text-ink/42 disabled:shadow-none disabled:hover:translate-y-0";
  const forwardNavigationClass =
    "rounded-full border border-[rgba(218,194,142,0.56)] bg-[linear-gradient(180deg,rgba(237,222,184,0.98),rgba(199,166,104,0.96))] px-6 py-3 text-sm font-semibold text-[rgba(29,58,48,0.96)] shadow-[inset_0_1px_0_rgba(255,252,240,0.66),inset_0_-1px_0_rgba(119,82,38,0.14),0_10px_22px_rgba(15,36,30,0.18)] transition-[background-image,border-color,transform,box-shadow] duration-150 hover:-translate-y-px hover:border-[rgba(231,207,156,0.66)] hover:bg-[linear-gradient(180deg,rgba(242,228,192,0.98),rgba(184,149,88,0.98))] hover:shadow-[inset_0_1px_0_rgba(255,252,240,0.72),inset_0_-1px_0_rgba(99,68,32,0.16),0_12px_26px_rgba(15,36,30,0.22)] active:translate-y-0 active:scale-[0.99] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(237,222,184,0.78)] focus-visible:ring-offset-2 focus-visible:ring-offset-white disabled:cursor-not-allowed disabled:border-[rgba(199,166,104,0.24)] disabled:bg-[linear-gradient(180deg,rgba(237,222,184,0.58),rgba(199,166,104,0.52))] disabled:text-[rgba(29,58,48,0.52)] disabled:shadow-none disabled:hover:translate-y-0";

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
          className={backNavigationClass}
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
        className={forwardNavigationClass}
      >
        {nextLabel}
      </button>
    </div>
  );
}
