import { useMemo, useState, type ReactNode } from "react";
import { onboardingText } from "../../copy/onboardingCopy";
import {
  createCustomInboxId,
  createInboxConnection,
} from "../../data/onboardingOptions";
import type { LiveInboxMessageSnapshot } from "../../lib/inboxConnectionApi";
import {
  applyProviderDefaults,
  createDefaultCustomSmtpSettings,
  getDefaultConnectionStatus,
  getProviderConnectionMethod,
  usesEmailAsImapUsername,
} from "../../lib/inboxProviderDefaults";
import { saveLiveInboxSnapshot } from "../../lib/liveInboxSnapshots";
import type {
  CustomInboxDefinition,
  CustomImapSettings,
  CustomSmtpSettings,
  InboxId,
  OnboardingState,
  ProviderId,
  SelectableFocusPreferenceLevel,
} from "../../types/onboarding";
import { normalizeFocusPreferences } from "../../types/onboarding";
import type { UserConfig } from "../../types/userConfig";
import { NavigationBar } from "./NavigationBar";
import { ProgressIndicator } from "./ProgressIndicator";
import { StepComplete } from "./StepComplete";
import { StepConnectInboxes } from "./StepConnectInboxes";
import {
  onboardingFocusItems,
  StepFocusPreferences,
} from "./StepFocusPreferences";
import { StepWelcome } from "./StepWelcome";

const totalScreens = 4;
const totalProgressSteps = 3;

function dedupeInboxes(inboxes: Array<InboxId | null | undefined>) {
  return [...new Set(inboxes.filter((inboxId): inboxId is InboxId => Boolean(inboxId)))];
}

interface OnboardingFlowProps {
  state: OnboardingState;
  onStateChange: (
    value: OnboardingState | ((current: OnboardingState) => OnboardingState),
  ) => void;
  onOpenWorkspace: (userConfig: UserConfig) => void;
  isPreviewMode?: boolean;
  previewControls?: ReactNode;
}

export function OnboardingFlow({
  state,
  onStateChange,
  onOpenWorkspace,
  isPreviewMode = false,
  previewControls,
}: OnboardingFlowProps) {
  const [step, setStep] = useState(0);
  const showSetupProgress = step > 0;
  const isFinalScreen = step === 3;
  const sidebarHelperText =
    (
      {
        1: "Choose which mail types stay Normal and which should be Low.",
        2: "Connect at least one source account. More inboxes can be added later.",
      } as const
    )[step as 1 | 2] ?? null;

  const getInboxConnection = (current: OnboardingState, inboxId: InboxId) => {
    const connection = current.inboxConnections[inboxId] ?? createInboxConnection();
    const defaults = createInboxConnection();

    return {
      ...defaults,
      ...connection,
      customImap: {
        ...defaults.customImap,
        ...connection.customImap,
      },
      customSmtp: {
        ...createDefaultCustomSmtpSettings(),
        ...connection.customSmtp,
      },
    };
  };

  const connectedInboxIds = state.selectedInboxes.filter((inboxId) => {
    const connection = getInboxConnection(state, inboxId);
    return connection.connected || connection.connectionStatus === "connected";
  });
  const normalizedFocusPreferences = normalizeFocusPreferences(state.focusPreferences);
  const lowFocusLabels = onboardingFocusItems
    .filter((item) =>
      item.fields.every((field) => normalizedFocusPreferences[field] === "low"),
    )
    .map((item) => item.label);

  const canGoNext = useMemo(() => {
    if (step === 2) {
      return connectedInboxIds.length > 0;
    }

    return true;
  }, [connectedInboxIds.length, step]);

  const setFocusPreference = (
    fields: Array<keyof OnboardingState["focusPreferences"]>,
    value: SelectableFocusPreferenceLevel,
  ) => {
    onStateChange((current) => ({
      ...current,
      focusPreferences: {
        ...current.focusPreferences,
        ...Object.fromEntries(fields.map((field) => [field, value])),
      },
    }));
  };

  const userConfig: UserConfig = {
    primaryRole: state.primaryRole,
    internalRole: state.internalRole,
    focusPreferences: normalizedFocusPreferences,
    inboxCount: state.inboxCount,
    selectedInboxes: state.selectedInboxes,
    primaryInboxType: state.primaryInboxType,
  };

  const addSourceInbox = () => {
    onStateChange((current) => {
      const id = createCustomInboxId(`Inbox ${current.selectedInboxes.length + 1}`);
      const customInbox: CustomInboxDefinition = {
        id,
        name: `Inbox ${current.selectedInboxes.length + 1}`,
      };

      return {
        ...current,
        inboxCount: "4+",
        customInboxes: [...current.customInboxes, customInbox],
        selectedInboxes: dedupeInboxes([...current.selectedInboxes, id]),
        inboxConnections: {
          ...current.inboxConnections,
          [id]: createInboxConnection(),
        },
      };
    });
  };

  const canRemoveSelectedInbox = (inboxId: InboxId) =>
    state.selectedInboxes.length > 1 && state.selectedInboxes.includes(inboxId);

  const removeSelectedInbox = (inboxId: InboxId) => {
    onStateChange((current) => {
      if (!current.selectedInboxes.includes(inboxId)) {
        return current;
      }

      const nextSelectedInboxes = current.selectedInboxes.filter((id) => id !== inboxId);
      const nextCustomInboxes = current.customInboxes.filter((inbox) => inbox.id !== inboxId);

      return {
        ...current,
        primaryInbox:
          current.primaryInbox === inboxId
            ? nextSelectedInboxes[0] ?? "main"
            : current.primaryInbox,
        selectedInboxes: nextSelectedInboxes,
        customInboxes: nextCustomInboxes,
        inboxConnections: {
          ...current.inboxConnections,
          [inboxId]: createInboxConnection(),
        },
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
          connectionMethod: getProviderConnectionMethod(provider),
          connectionStatus: getDefaultConnectionStatus(provider),
          connectionMessage: null,
          oauthAuthorizationUrl: null,
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
          connectionStatus: getDefaultConnectionStatus(
            getInboxConnection(current, inboxId).provider,
          ),
          connectionMessage: null,
          oauthAuthorizationUrl: null,
          email,
          customImap:
            usesEmailAsImapUsername(getInboxConnection(current, inboxId).provider)
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
          connectionStatus: getDefaultConnectionStatus(
            getInboxConnection(current, inboxId).provider,
          ),
          connectionMessage: null,
          oauthAuthorizationUrl: null,
          customImap: {
            ...getInboxConnection(current, inboxId).customImap,
            [field]: value,
          },
        },
      },
    }));
  };

  const setCustomSmtp = (
    inboxId: InboxId,
    field: keyof CustomSmtpSettings,
    value: string | boolean,
  ) => {
    onStateChange((current) => ({
      ...current,
      inboxConnections: {
        ...current.inboxConnections,
        [inboxId]: {
          ...getInboxConnection(current, inboxId),
          customSmtp: {
            ...getInboxConnection(current, inboxId).customSmtp,
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
          connectionStatus: getDefaultConnectionStatus(
            getInboxConnection(current, inboxId).provider,
          ),
          connectionMessage: null,
          oauthAuthorizationUrl: null,
          customImap: {
            ...settings,
          },
        },
      },
    }));
  };

  const connectInbox = (
    inboxId: InboxId,
    result: {
      connected: boolean;
      connectionMethod: ReturnType<typeof getProviderConnectionMethod>;
      connectionStatus:
        | "not_connected"
        | "oauth_required"
        | "waiting_for_authentication"
        | "authenticated_pending_activation"
        | "connected"
        | "connection_failed";
      connectionMessage?: string | null;
      oauthAuthorizationUrl?: string | null;
    },
    messages: LiveInboxMessageSnapshot[] = [],
  ) => {
    onStateChange((current) => ({
      ...current,
      inboxConnections: {
        ...current.inboxConnections,
        [inboxId]: {
          ...getInboxConnection(current, inboxId),
          connected: result.connected,
          connectionMethod: result.connectionMethod,
          connectionStatus: result.connectionStatus,
          connectionMessage: result.connectionMessage ?? null,
          oauthAuthorizationUrl: result.oauthAuthorizationUrl ?? null,
          customImap: {
            ...getInboxConnection(current, inboxId).customImap,
            password: result.connected
              ? ""
              : getInboxConnection(current, inboxId).customImap.password,
          },
          customSmtp: {
            ...getInboxConnection(current, inboxId).customSmtp,
            password: result.connected
              ? ""
              : getInboxConnection(current, inboxId).customSmtp.password,
          },
        },
      },
    }));

    const connection = state.inboxConnections[inboxId];

    if (!isPreviewMode && result.connected && connection?.email.trim()) {
      saveLiveInboxSnapshot({
        inboxId,
        email: connection.email.trim().toLowerCase(),
        fetchedAt: new Date().toISOString(),
        messages,
      });
    }
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
          <StepFocusPreferences
            value={state.focusPreferences}
            onChange={setFocusPreference}
          />
        );
      case 2:
        return (
          <StepConnectInboxes
            selectedInboxes={state.selectedInboxes}
            customInboxes={state.customInboxes}
            inboxConnections={state.inboxConnections}
            internalRole={state.internalRole}
            focusPreferences={state.focusPreferences}
            onProviderChange={setProvider}
            onEmailChange={setEmail}
            onCustomImapChange={setCustomImap}
            onCustomSmtpChange={setCustomSmtp}
            onReuseCustomImap={reuseCustomImap}
            onConnectInbox={connectInbox}
            canRemoveInbox={canRemoveSelectedInbox}
            onRemoveInbox={removeSelectedInbox}
            onAddInbox={addSourceInbox}
            isPreviewMode={isPreviewMode}
          />
        );
      case 3:
        return (
          <StepComplete
            connectedInboxCount={connectedInboxIds.length}
            lowFocusLabels={lowFocusLabels}
          />
        );
      default:
        return null;
    }
  };

  const nextLabel =
    step === 0
      ? onboardingText.navigation.startSetup
      : step === 2
        ? onboardingText.navigation.completeSetup
        : step === 3
          ? onboardingText.navigation.goToDashboard
          : onboardingText.navigation.next;

  return (
    <main className="min-h-screen px-4 py-8 md:px-8 md:py-10">
      <div className="mx-auto max-w-6xl">
        {previewControls}
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
                        : "A short setup for focus and inbox access. Everything can be adjusted later."}
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
                    currentStep={step === 3 ? totalProgressSteps : step}
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
                    currentStep={step === 3 ? totalProgressSteps : step}
                    totalSteps={totalProgressSteps}
                  />
                </div>
              ) : null}
              <div className="flex-1">{renderStep()}</div>
              <NavigationBar
                canGoBack={step > 0}
                backLabel={step === 3 ? "Edit setup" : undefined}
                onBack={back}
                onNext={
                  step === 3
                    ? () => onOpenWorkspace(userConfig)
                    : next
                }
                nextLabel={nextLabel}
                isNextDisabled={step === 3 ? false : !canGoNext}
              />
            </section>
          </div>
        </div>
      </div>
    </main>
  );
}
