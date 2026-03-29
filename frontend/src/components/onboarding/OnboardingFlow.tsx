import { useMemo, useState } from "react";
import { onboardingText } from "../../copy/onboardingCopy";
import {
  createCustomInboxId,
  createInboxConnection,
} from "../../data/onboardingOptions";
import type { LiveInboxMessageSnapshot } from "../../lib/inboxConnectionApi";
import {
  applyProviderDefaults,
  isImapCredentialsProvider,
} from "../../lib/inboxProviderDefaults";
import { mapRoleToInternal } from "../../lib/roleMapping";
import { saveLiveInboxSnapshot } from "../../lib/liveInboxSnapshots";
import type {
  CustomInboxDefinition,
  CustomImapSettings,
  FocusPreferenceLevel,
  InboxId,
  OnboardingState,
  ProviderId,
  RoleId,
  WorkflowStyleId,
} from "../../types/onboarding";
import { NavigationBar } from "./NavigationBar";
import { ProgressIndicator } from "./ProgressIndicator";
import { StepComplete } from "./StepComplete";
import { StepConnectInboxes } from "./StepConnectInboxes";
import { StepFocusPreferences } from "./StepFocusPreferences";
import { StepInboxCount } from "./StepInboxCount";
import { StepInboxSetup } from "./StepInboxSetup";
import { StepRoleSelection } from "./StepRoleSelection";
import { StepWelcome } from "./StepWelcome";
import { StepWorkflowStyle } from "./StepWorkflowStyle";

const totalScreens = 8;
const totalProgressSteps = 7;

function getMaxActiveInboxCount(inboxCount: OnboardingState["inboxCount"]) {
  if (inboxCount === "1") return 1;
  if (inboxCount === "2") return 2;
  if (inboxCount === "3") return 3;
  if (inboxCount === "not_sure") return 2;
  return Number.POSITIVE_INFINITY;
}

function getRequiredInboxCount(inboxCount: OnboardingState["inboxCount"]) {
  if (inboxCount === "1") return 1;
  if (inboxCount === "2") return 2;
  if (inboxCount === "3") return 3;
  if (inboxCount === "4+") return 4;
  if (inboxCount === "not_sure") return 2;
  return 1;
}

interface OnboardingFlowProps {
  state: OnboardingState;
  onStateChange: (
    value: OnboardingState | ((current: OnboardingState) => OnboardingState),
  ) => void;
  onOpenWorkspace: () => void;
}

export function OnboardingFlow({
  state,
  onStateChange,
  onOpenWorkspace,
}: OnboardingFlowProps) {
  const [step, setStep] = useState(0);
  const showSetupProgress = step > 0;
  const isFinalScreen = step === 7;
  const sidebarHelperText =
    (
      {
        1: onboardingText.sidebar.stepHelper[1],
        2: "Set what matters most to you inside the inbox before the workspace is structured.",
        3: onboardingText.sidebar.stepHelper[2],
        4: onboardingText.sidebar.stepHelper[3],
        5: onboardingText.sidebar.stepHelper[4],
        6: onboardingText.sidebar.stepHelper[5],
      } as const
    )[step as 1 | 2 | 3 | 4 | 5 | 6] ?? null;

  const getInboxConnection = (current: OnboardingState, inboxId: InboxId) =>
    current.inboxConnections[inboxId] ?? createInboxConnection();

  const canGoNext = useMemo(() => {
    if (step === 1) {
      return Boolean(state.primaryRole);
    }

    if (step === 3) {
      return Boolean(state.inboxCount);
    }

    if (step === 4) {
      return state.selectedInboxes.length >= getRequiredInboxCount(state.inboxCount);
    }

    if (step === 5) {
      return Boolean(state.workflowStyle);
    }

    if (step === 6) {
      return state.selectedInboxes.every((inboxId) => {
        const connection = getInboxConnection(state, inboxId);
        if (
          !connection.connected ||
          !connection.provider ||
          !connection.email.trim()
        ) {
          return false;
        }

        if (!isImapCredentialsProvider(connection.provider)) {
          return true;
        }

        const { host, port, username, password } = connection.customImap;
        return Boolean(
          host.trim() && port.trim() && username.trim() && password.trim(),
        );
      });
    }

    return true;
  }, [state, step]);

  const setPrimaryRole = (role: RoleId) => {
    onStateChange((current) => ({
      ...current,
      primaryRole: role,
      internalRole: mapRoleToInternal(role),
      secondaryRole: current.secondaryRole === role ? null : current.secondaryRole,
    }));
  };

  const setFocusPreference = (
    field: keyof OnboardingState["focusPreferences"],
    value: FocusPreferenceLevel,
  ) => {
    onStateChange((current) => ({
      ...current,
      focusPreferences: {
        ...current.focusPreferences,
        [field]: value,
      },
    }));
  };

  const toggleInbox = (inboxId: InboxId) => {
    onStateChange((current) => {
      const exists = current.selectedInboxes.includes(inboxId);
      const maxActiveInboxCount = getMaxActiveInboxCount(current.inboxCount);

      if (!exists && current.selectedInboxes.length >= maxActiveInboxCount) {
        return current;
      }

      return {
        ...current,
        selectedInboxes: exists
          ? current.selectedInboxes.filter((id) => id !== inboxId)
          : [...current.selectedInboxes, inboxId],
      };
    });
  };

  const setProvider = (inboxId: InboxId, provider: ProviderId) => {
    onStateChange((current) => ({
      ...current,
      inboxConnections: {
        ...current.inboxConnections,
        [inboxId]: {
          ...getInboxConnection(current, inboxId),
          connected: false,
          provider,
          customImap: applyProviderDefaults(
            provider,
            getInboxConnection(current, inboxId).customImap,
            getInboxConnection(current, inboxId).email,
          ),
        },
      },
    }));
  };

  const setEmail = (inboxId: InboxId, email: string) => {
    onStateChange((current) => ({
      ...current,
      inboxConnections: {
        ...current.inboxConnections,
        [inboxId]: {
          ...getInboxConnection(current, inboxId),
          connected: false,
          email,
          customImap:
            getInboxConnection(current, inboxId).provider === "google"
              ? {
                  ...getInboxConnection(current, inboxId).customImap,
                  username: email.trim(),
                }
              : getInboxConnection(current, inboxId).customImap,
        },
      },
    }));
  };

  const setCustomImap = (
    inboxId: InboxId,
    field: keyof CustomImapSettings,
    value: string | boolean,
  ) => {
    onStateChange((current) => ({
      ...current,
      inboxConnections: {
        ...current.inboxConnections,
        [inboxId]: {
          ...getInboxConnection(current, inboxId),
          connected: false,
          customImap: {
            ...getInboxConnection(current, inboxId).customImap,
            [field]: value,
          },
        },
      },
    }));
  };

  const reuseCustomImap = (inboxId: InboxId, settings: CustomImapSettings) => {
    onStateChange((current) => ({
      ...current,
      inboxConnections: {
        ...current.inboxConnections,
        [inboxId]: {
          ...getInboxConnection(current, inboxId),
          connected: false,
          customImap: {
            ...settings,
          },
        },
      },
    }));
  };

  const connectInbox = (
    inboxId: InboxId,
    messages: LiveInboxMessageSnapshot[] = [],
  ) => {
    onStateChange((current) => ({
      ...current,
      inboxConnections: {
        ...current.inboxConnections,
        [inboxId]: {
          ...getInboxConnection(current, inboxId),
          connected: true,
        },
      },
    }));

    const connection = state.inboxConnections[inboxId];

    if (connection?.email.trim()) {
      saveLiveInboxSnapshot({
        inboxId,
        email: connection.email.trim().toLowerCase(),
        fetchedAt: new Date().toISOString(),
        messages,
      });
    }
  };

  const addCustomInbox = (name: string) => {
    const trimmedName = name.trim();

    if (!trimmedName) {
      return false;
    }

    let added = false;

    onStateChange((current) => {
      const maxActiveInboxCount = getMaxActiveInboxCount(current.inboxCount);

      if (current.selectedInboxes.length >= maxActiveInboxCount) {
        return current;
      }

      const id = createCustomInboxId(trimmedName);
      const customInbox: CustomInboxDefinition = { id, name: trimmedName };
      added = true;

      return {
        ...current,
        customInboxes: [...current.customInboxes, customInbox],
        selectedInboxes: [...current.selectedInboxes, id],
        inboxConnections: {
          ...current.inboxConnections,
          [id]: createInboxConnection(),
        },
      };
    });

    return added;
  };

  const next = () => {
    if (!canGoNext) return;
    setStep((current) => Math.min(current + 1, totalScreens - 1));
  };

  const back = () => {
    setStep((current) => Math.max(current - 1, 0));
  };

  const renderStep = () => {
    switch (step) {
      case 0:
        return <StepWelcome />;
      case 1:
        return (
          <StepRoleSelection
            primaryRole={state.primaryRole}
            secondaryRole={state.secondaryRole}
            onPrimaryChange={setPrimaryRole}
            onSecondaryChange={(secondaryRole) =>
              onStateChange((current) => ({ ...current, secondaryRole }))
            }
          />
        );
      case 2:
        return (
          <StepFocusPreferences
            value={state.focusPreferences}
            onChange={setFocusPreference}
          />
        );
      case 3:
        return (
          <StepInboxCount
            value={state.inboxCount}
            onChange={(inboxCount) =>
              onStateChange((current) => {
                const maxActiveInboxCount = getMaxActiveInboxCount(inboxCount);
                const preservedSecondaryInboxes = current.selectedInboxes.filter(
                  (inboxId) => inboxId !== "main",
                );
                const nextSelectedInboxes =
                  maxActiveInboxCount === 1
                    ? (["main"] as InboxId[])
                    : ([
                        "main",
                        ...preservedSecondaryInboxes.slice(
                          0,
                          Math.max(0, maxActiveInboxCount - 1),
                        ),
                      ] as InboxId[]);

                return {
                  ...current,
                  inboxCount,
                  selectedInboxes: nextSelectedInboxes,
                };
              })
            }
          />
        );
      case 4:
        return (
          <StepInboxSetup
            selectedInboxes={state.selectedInboxes}
            customInboxes={state.customInboxes}
            maxActiveInboxCount={getMaxActiveInboxCount(state.inboxCount)}
            requiredInboxCount={getRequiredInboxCount(state.inboxCount)}
            onToggleInbox={toggleInbox}
            onAddCustomInbox={addCustomInbox}
          />
        );
      case 5:
        return (
          <StepWorkflowStyle
            value={state.workflowStyle}
            onChange={(workflowStyle: WorkflowStyleId) =>
              onStateChange((current) => ({ ...current, workflowStyle }))
            }
          />
        );
      case 6:
        return (
          <StepConnectInboxes
            selectedInboxes={state.selectedInboxes}
            customInboxes={state.customInboxes}
            inboxConnections={state.inboxConnections}
            internalRole={state.internalRole}
            onProviderChange={setProvider}
            onEmailChange={setEmail}
            onCustomImapChange={setCustomImap}
            onReuseCustomImap={reuseCustomImap}
            onConnectInbox={connectInbox}
          />
        );
      case 7:
        return (
          <StepComplete
            connectedInboxCount={state.selectedInboxes.filter(
              (inboxId) => state.inboxConnections[inboxId].connected,
            ).length}
          />
        );
      default:
        return null;
    }
  };

  const nextLabel =
    step === 0
      ? onboardingText.navigation.startSetup
      : step === 6
        ? onboardingText.navigation.completeSetup
        : step === 7
          ? onboardingText.navigation.goToDashboard
          : onboardingText.navigation.next;

  return (
    <main className="min-h-screen px-4 py-8 md:px-8 md:py-10">
      <div className="mx-auto max-w-6xl">
        <div className="overflow-hidden rounded-[36px] border border-white/50 bg-white/55 shadow-panel backdrop-blur-xl">
          <div className="grid min-h-[860px] lg:grid-cols-[320px_1fr]">
            <aside className="relative hidden border-r border-ink/8 bg-pine px-8 py-10 text-white lg:block">
              <div className="absolute inset-0 bg-[radial-gradient(circle_at_top,_rgba(255,255,255,0.12),_transparent_38%)]" />
              <div className="relative flex h-full flex-col justify-between">
                <div className="space-y-6">
                  <span className="inline-flex rounded-full border border-white/20 px-4 py-2 text-xs uppercase tracking-[0.28em] text-white/80">
                    {onboardingText.sidebar.workspaceSetup}
                  </span>
                  <div className="inline-flex items-center gap-3 text-white/92">
                    <span
                      aria-hidden="true"
                      className="flex h-8 w-8 items-center justify-center rounded-full border border-white/18 bg-white/8"
                    >
                      <span className="h-2.5 w-2.5 rounded-full bg-white/80" />
                    </span>
                    <span className="text-[1.15rem] font-semibold tracking-[0.03em]">
                      {onboardingText.brand.name}
                    </span>
                  </div>
                  <div className="space-y-3">
                    <h2 className="text-3xl font-semibold tracking-tight">
                      {onboardingText.sidebar.description}
                    </h2>
                    <p className="max-w-xs text-sm leading-7 text-white/70">
                      {isFinalScreen
                        ? onboardingText.complete.sidebarText
                        : "Move through the setup with local state only. Your choices stay intact while navigating forward and back."}
                    </p>
                    {sidebarHelperText ? (
                      <div className="space-y-2 pt-4">
                        <div className="text-sm font-semibold text-white/56">
                          {onboardingText.sidebar.helperLabel}
                        </div>
                        <p className="max-w-xs text-sm leading-7 text-white/54">
                          {sidebarHelperText}
                        </p>
                      </div>
                    ) : null}
                  </div>
                </div>
                {showSetupProgress ? (
                  <ProgressIndicator
                    currentStep={step === 7 ? totalProgressSteps : step}
                    totalSteps={totalProgressSteps}
                    variant="sidebar"
                    sidebarLabel={
                      isFinalScreen ? onboardingText.complete.sidebarLabel : undefined
                    }
                  />
                ) : null}
              </div>
            </aside>

            <section className="flex flex-col p-6 md:p-8 lg:p-10">
              {showSetupProgress ? (
                <div className="mb-8 lg:hidden">
                  <ProgressIndicator
                    currentStep={step === 7 ? totalProgressSteps : step}
                    totalSteps={totalProgressSteps}
                  />
                </div>
              ) : null}
              <div className="flex-1">{renderStep()}</div>
              <NavigationBar
                canGoBack={step > 0}
                backLabel={step === 7 ? "Edit setup" : undefined}
                onBack={back}
                onNext={step === 7 ? onOpenWorkspace : next}
                nextLabel={nextLabel}
                isNextDisabled={step === 7 ? false : !canGoNext}
              />
            </section>
          </div>
        </div>
      </div>
    </main>
  );
}
