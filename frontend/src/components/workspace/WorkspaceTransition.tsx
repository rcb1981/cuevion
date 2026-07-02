import { useEffect, useMemo, useState } from "react";
import { readLiveInboxSnapshots } from "../../lib/liveInboxSnapshots";
import type { InboxId } from "../../types/onboarding";

const transitionStatuses = [
  "Connecting inboxes",
  "Syncing recent messages",
  "Classifying your inbox",
  "Preparing workspace",
] as const;

type TransitionStatus = "syncing" | "completing" | "needs_action";

interface WorkspaceTransitionProps {
  connectedInboxIds: InboxId[];
  onComplete: () => void;
}

function haveSnapshotsForConnectedInboxes(connectedInboxIds: InboxId[]) {
  if (connectedInboxIds.length === 0) {
    return true;
  }

  const snapshots = readLiveInboxSnapshots();
  return connectedInboxIds.every((inboxId) => Boolean(snapshots[inboxId]));
}

export function WorkspaceTransition({
  connectedInboxIds,
  onComplete,
}: WorkspaceTransitionProps) {
  const [statusIndex, setStatusIndex] = useState(0);
  const [attemptCount, setAttemptCount] = useState(0);
  const [transitionStatus, setTransitionStatus] =
    useState<TransitionStatus>("syncing");
  const connectedInboxKey = useMemo(
    () => connectedInboxIds.join("|"),
    [connectedInboxIds],
  );

  useEffect(() => {
    setStatusIndex(0);
    setAttemptCount(0);
    setTransitionStatus("syncing");
  }, [connectedInboxKey]);

  useEffect(() => {
    if (transitionStatus !== "syncing") {
      return;
    }

    const interval = window.setInterval(() => {
      const hasCompletedInitialSync =
        haveSnapshotsForConnectedInboxes(connectedInboxIds);

      setStatusIndex((current) =>
        current < transitionStatuses.length - 1 ? current + 1 : current,
      );
      setAttemptCount((current) => current + 1);

      if (hasCompletedInitialSync) {
        window.clearInterval(interval);
        setTransitionStatus("completing");
        window.setTimeout(onComplete, 450);
        return;
      }

      if (attemptCount >= 8) {
        window.clearInterval(interval);
        setTransitionStatus("needs_action");
      }
    }, 700);

    return () => window.clearInterval(interval);
  }, [attemptCount, connectedInboxIds, onComplete, transitionStatus]);

  const handleRetry = () => {
    setStatusIndex(0);
    setAttemptCount(0);
    setTransitionStatus("syncing");
  };

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
                {transitionStatus === "needs_action"
                  ? "Initial sync is taking longer than expected."
                  : "Preparing your inbox environment"}
              </p>
            </div>
            {transitionStatus === "needs_action" ? (
              <div className="space-y-4">
                <p className="text-sm leading-7 text-ink/56">
                  Recent messages are not ready yet for every connected inbox. You can
                  retry the sync check or continue and let the workspace keep loading.
                </p>
                <div className="flex flex-wrap justify-center gap-3">
                  <button
                    type="button"
                    onClick={handleRetry}
                    className="inline-flex h-10 items-center justify-center rounded-full border border-moss/16 bg-white/72 px-5 text-sm font-medium text-moss transition hover:border-moss/28 hover:bg-white"
                  >
                    Retry
                  </button>
                  <button
                    type="button"
                    onClick={onComplete}
                    className="inline-flex h-10 items-center justify-center rounded-full border border-[rgba(218,194,142,0.56)] bg-[linear-gradient(180deg,rgba(237,222,184,0.98),rgba(199,166,104,0.96))] px-5 text-[0.72rem] font-semibold uppercase tracking-[0.15em] text-[rgba(29,58,48,0.96)] shadow-[inset_0_1px_0_rgba(255,252,240,0.66),inset_0_-1px_0_rgba(119,82,38,0.14),0_10px_22px_rgba(15,36,30,0.18)] transition hover:border-[rgba(231,207,156,0.66)]"
                  >
                    Continue
                  </button>
                </div>
              </div>
            ) : (
              <div className="mx-auto max-w-[20rem] space-y-3">
                <div className="h-1 overflow-hidden rounded-full bg-ink/10">
                  <div className="h-full w-1/3 animate-loading-line rounded-full bg-gradient-to-r from-[#dec1a6] to-[#cea783]" />
                </div>
                <p className="text-sm leading-7 text-ink/50 transition-opacity duration-300">
                  {transitionStatuses[statusIndex]}
                </p>
              </div>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}
