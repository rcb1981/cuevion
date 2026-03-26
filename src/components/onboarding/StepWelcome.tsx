import { onboardingText } from "../../copy/onboardingCopy";

export function StepWelcome() {
  return (
    <section className="space-y-6 py-10">
      <div className="max-w-2xl space-y-4">
        <h1 className="text-4xl font-semibold tracking-tight text-ink md:text-5xl">
          {onboardingText.welcome.title}
        </h1>
        <p className="text-lg leading-8 text-ink/70">
          {onboardingText.welcome.text}
        </p>
      </div>
    </section>
  );
}
