import type { FocusPreferenceLevel, OnboardingState } from "../../types/onboarding";

const focusOptions: Array<{
  id: keyof OnboardingState["focusPreferences"];
  label: string;
}> = [
  { id: "demos", label: "Demos" },
  { id: "promo", label: "Promo" },
  { id: "finance", label: "Finance" },
  { id: "legal", label: "Legal" },
  { id: "business", label: "Business" },
  { id: "updates", label: "Updates" },
  { id: "distribution", label: "Distribution" },
  { id: "royalties", label: "Royalties" },
];

const reminderOptions: Array<{
  id: keyof OnboardingState["focusPreferences"];
  label: string;
}> = [
  { id: "promoReminders", label: "Promo reminders" },
  { id: "paymentReminders", label: "Payment reminders" },
];

const preferenceLevels: FocusPreferenceLevel[] = ["high", "medium", "low"];

const preferenceLevelLabels: Record<FocusPreferenceLevel, string> = {
  high: "Priority",
  medium: "Normal",
  low: "Low",
};

interface StepFocusPreferencesProps {
  value: OnboardingState["focusPreferences"];
  onChange: (
    field: keyof OnboardingState["focusPreferences"],
    value: FocusPreferenceLevel,
  ) => void;
}

export function StepFocusPreferences({
  value,
  onChange,
}: StepFocusPreferencesProps) {
  return (
    <section className="space-y-8">
      <div className="space-y-3">
        <h2 className="text-3xl font-semibold tracking-tight text-ink">
          What should Cuevion surface first?
        </h2>
        <p className="max-w-2xl text-base leading-7 text-ink/68">
          Set your personal focus across the inbox. You can fine-tune it later.
        </p>
      </div>

      <div className="grid gap-4 md:gap-x-6 grid-cols-1 md:grid-cols-2">
        {focusOptions.map((option) => (
          <div
            key={option.id}
            className="rounded-[28px] border border-ink/10 bg-white/82 px-4 py-3.5 shadow-[0_10px_30px_rgba(32,28,24,0.04)]"
          >
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div className="text-[0.98rem] font-semibold tracking-[-0.015em] text-ink">
                {option.label}
              </div>
              <div className="grid grid-cols-3 gap-2 md:w-auto">
                {preferenceLevels.map((level) => {
                  const selected = value[option.id] === level;

                  return (
                    <button
                      key={level}
                      type="button"
                      onClick={() => onChange(option.id, level)}
                      className={`rounded-full border px-3 py-1.5 text-[0.72rem] font-medium uppercase tracking-[0.12em] transition ${
                        selected
                          ? "border-pine bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))] text-ink shadow-panel"
                          : "border-ink/10 bg-white/70 text-ink/56 hover:border-moss/24 hover:text-ink"
                      }`}
                    >
                      {preferenceLevelLabels[level]}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="space-y-3 pt-2">
        <div className="space-y-2">
          <h3 className="text-[1.05rem] font-semibold tracking-[-0.018em] text-ink">
            Reminder handling
          </h3>
          <p className="max-w-2xl text-sm leading-6 text-ink/62">
            Control how strongly recurring reminder emails should be surfaced.
          </p>
        </div>

        <div className="grid gap-3 grid-cols-1">
          {reminderOptions.map((option) => (
            <div
              key={option.id}
              className="rounded-[28px] border border-ink/10 bg-white/82 px-4 py-3.5 shadow-[0_10px_30px_rgba(32,28,24,0.04)]"
            >
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div className="text-[0.98rem] font-semibold tracking-[-0.015em] text-ink">
                  {option.label}
                </div>
                <div className="grid grid-cols-3 gap-2 md:w-auto">
                  {preferenceLevels.map((level) => {
                    const selected = value[option.id] === level;

                    return (
                      <button
                        key={level}
                        type="button"
                        onClick={() => onChange(option.id, level)}
                        className={`rounded-full border px-3 py-1.5 text-[0.72rem] font-medium uppercase tracking-[0.12em] transition ${
                          selected
                            ? "border-pine bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))] text-ink shadow-panel"
                            : "border-ink/10 bg-white/70 text-ink/56 hover:border-moss/24 hover:text-ink"
                        }`}
                      >
                        {level}
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
