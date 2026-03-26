import { useEffect, useState } from "react";

const transitionStatuses = [
  "Applying inbox profiles",
  "Loading connected inboxes",
  "Preparing dashboard",
] as const;

export function WorkspaceTransition() {
  const [statusIndex, setStatusIndex] = useState(0);

  useEffect(() => {
    const interval = window.setInterval(() => {
      setStatusIndex((current) =>
        current < transitionStatuses.length - 1 ? current + 1 : current,
      );
    }, 700);

    return () => window.clearInterval(interval);
  }, []);

  return (
    <main className="min-h-screen px-4 py-8 md:px-8 md:py-10 animate-fade-in">
      <div className="mx-auto flex min-h-[calc(100vh-4rem)] max-w-5xl items-center justify-center">
        <section className="w-full max-w-[34rem] rounded-[36px] border border-white/50 bg-white/58 p-8 text-center shadow-panel backdrop-blur-xl md:p-10">
          <div className="space-y-6">
            <span className="inline-flex rounded-full bg-moss/10 px-4 py-2 text-xs uppercase tracking-[0.28em] text-moss">
              Setup Complete
            </span>
            <div className="space-y-3">
              <h1 className="text-4xl font-semibold tracking-tight text-ink md:text-[2.8rem]">
                Finalizing your workspace
              </h1>
              <p className="text-lg leading-8 text-ink/66">
                Preparing your inbox environment
              </p>
            </div>
            <div className="mx-auto max-w-[20rem] space-y-3">
              <div className="h-1 overflow-hidden rounded-full bg-ink/10">
                <div className="h-full w-1/3 animate-loading-line rounded-full bg-gradient-to-r from-[#dec1a6] to-[#cea783]" />
              </div>
              <p className="text-sm leading-7 text-ink/50 transition-opacity duration-300">
                {transitionStatuses[statusIndex]}
              </p>
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
