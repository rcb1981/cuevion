import type {
  OnboardingState,
  SelectableFocusPreferenceLevel,
} from "../../types/onboarding";

type FocusPreferenceKey = keyof OnboardingState["focusPreferences"];

export const onboardingFocusItems: Array<{
  id: string;
  label: string;
  fields: FocusPreferenceKey[];
}> = [
  { id: "demos", label: "Demos / A&R", fields: ["demos"] },
  { id: "promo", label: "Promo", fields: ["promo"] },
  { id: "business", label: "Business", fields: ["business"] },
  {
    id: "financeRoyalties",
    label: "Finance & royalties",
    fields: ["finance", "royalties"],
  },
  {
    id: "distributionUpdates",
    label: "Distribution updates",
    fields: ["distribution", "updates"],
  },
  { id: "legal", label: "Contracts / legal", fields: ["legal"] },
  { id: "promoReminders", label: "Promo reminders", fields: ["promoReminders"] },
  {
    id: "paymentReminders",
    label: "Payment reminders",
    fields: ["paymentReminders"],
  },
];

const preferenceLevels: SelectableFocusPreferenceLevel[] = ["medium", "low"];

const preferenceLevelLabels: Record<SelectableFocusPreferenceLevel, string> = {
  medium: "Normal",
  low: "Low",
};

function resolveItemLevel(
  value: OnboardingState["focusPreferences"],
  fields: FocusPreferenceKey[],
) {
  if (fields.every((field) => value[field] === "low")) {
    return "low";
  }

  return "medium";
}

interface StepFocusPreferencesProps {
  value: OnboardingState["focusPreferences"];
  onChange: (fields: FocusPreferenceKey[], value: SelectableFocusPreferenceLevel) => void;
}

export function StepFocusPreferences({
  value,
  onChange,
}: StepFocusPreferencesProps) {
  return (
    <section className="space-y-7">
      <div className="space-y-3">
        <h2 className="text-3xl font-semibold tracking-tight text-ink">
          Set your focus
        </h2>
        <p className="max-w-2xl text-base leading-7 text-ink/68">
          Choose which mail types stay normal and which should be lowered. You can
          change this later in Settings &gt; Focus.
        </p>
      </div>

      <div className="space-y-3">
        {onboardingFocusItems.map((item) => {
          const selectedLevel = resolveItemLevel(value, item.fields);

          return (
            <div
              key={item.id}
              className="rounded-[22px] border border-ink/10 bg-white/82 px-4 py-3 shadow-[0_10px_30px_rgba(32,28,24,0.04)]"
            >
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div className="text-[0.98rem] font-semibold tracking-[-0.015em] text-ink">
                  {item.label}
                </div>
                <div className="grid grid-cols-2 gap-1.5 md:w-[12.5rem]">
                  {preferenceLevels.map((level) => {
                    const selected = selectedLevel === level;

                    return (
                      <button
                        key={level}
                        type="button"
                        onClick={() => onChange(item.fields, level)}
                        className={`min-w-0 rounded-full border px-2 py-1.5 text-center text-[0.7rem] font-medium uppercase tracking-[0.08em] transition ${
                          selected
                            ? "border-pine bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))] text-ink shadow-panel"
                            : "border-ink/10 bg-white/62 text-ink/50 hover:border-moss/24 hover:text-ink"
                        }`}
                      >
                        {preferenceLevelLabels[level]}
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <p className="text-sm leading-6 text-ink/52">
        Normal is the default for anything you do not mark as Low.
      </p>
    </section>
  );
}
