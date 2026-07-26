import { useMemo, useRef, useState, type ReactNode } from "react";
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
import {
  doesCustomImapOnboardingSnapshotMatchState,
  isCustomImapOnboardingInteractionLocked,
  type CustomImapOnboardingAttemptGuard,
  type CustomImapOnboardingAttemptSnapshot,
  type CustomImapOnboardingReconciliationResult,
} from "../../lib/customImapOnboardingAttempt";
import type {
  CustomInboxDefinition,
  CustomImapSettings,
  CustomSmtpSettings,
  InboxConnection,
  InboxId,
  OnboardingState,
  ProviderId,
  SelectableFocusPreferenceLevel,
} from "../../types/onboarding";
import {
  clampOnboardingStep,
  normalizeFocusPreferences,
  ONBOARDING_STEP_MAX,
  ONBOARDING_STEP_MIN,
} from "../../types/onboarding";
import type { UserConfig } from "../../types/userConfig";
import { NavigationBar } from "./NavigationBar";
import { ProgressIndicator } from "./ProgressIndicator";
import { StepComplete } from "./StepComplete";
import {
  StepConnectInboxes,
  isOnboardingInboxFullyConnected,
} from "./StepConnectInboxes";
import {
  onboardingFocusItems,
  StepFocusPreferences,
} from "./StepFocusPreferences";
import { StepWelcome } from "./StepWelcome";

const totalProgressSteps = 3;

export function shouldBlockOnboardingMutation(
  guard: CustomImapOnboardingAttemptGuard | null,
) {
  return isCustomImapOnboardingInteractionLocked(guard);
}

export function invokeCustomImapServerReload(
  reload: () => void,
) {
  reload();
}

export function CustomImapServerReloadRecovery({
  visible,
  onReload,
}: {
  visible: boolean;
  onReload: () => void;
}) {
  if (!visible) {
    return null;
  }
  return (
    <div
      role="status"
      className="mb-5 flex flex-col gap-3 rounded-[22px] border border-amber-900/15 bg-white/75 px-5 py-4 text-sm text-ink/68 shadow-panel sm:flex-row sm:items-center sm:justify-between"
    >
      <span>
        Setup changed while the connection was pending. Reload from the
        server before continuing.
      </span>
      <button
        type="button"
        data-attempt-control="reload-custom-imap-recovery"
        onClick={() => invokeCustomImapServerReload(onReload)}
        className="shrink-0 rounded-full border border-moss/20 bg-white px-4 py-2 font-medium text-moss transition hover:border-moss/35 hover:bg-sand"
      >
        Reload setup from server
      </button>
    </div>
  );
}

export function applyAuthoritativeCustomImapConnection(
  state: OnboardingState,
  snapshot: CustomImapOnboardingAttemptSnapshot,
  result: Extract<
    CustomImapOnboardingReconciliationResult,
    { status: "matched" }
  >,
) {
  if (!doesCustomImapOnboardingSnapshotMatchState(snapshot, state)) {
    return state;
  }
  return {
    ...state,
    inboxConnections: {
      ...state.inboxConnections,
      [snapshot.onboardingInboxId]: {
        ...result.connection,
        serverMailboxId: result.serverMailboxId,
      },
    },
  };
}

export function attemptWorkspaceOpen(
  canOpenWorkspace: boolean,
  openWorkspace: () => void,
): boolean {
  if (!canOpenWorkspace) {
    return false;
  }

  openWorkspace();
  return true;
}

export const onboardingFlowProgression = {
  minStep: ONBOARDING_STEP_MIN,
  maxStep: ONBOARDING_STEP_MAX,
  clamp: clampOnboardingStep,
  next: (step: number) => clampOnboardingStep(step + 1),
  back: (step: number) => clampOnboardingStep(step - 1),
  attemptWorkspaceOpen,
} as const;

type OnboardingInboxConnectionResult = {
  connected: boolean;
  connectionMethod: InboxConnection["connectionMethod"];
  connectionStatus: InboxConnection["connectionStatus"];
  connectionMessage?: string | null;
  oauthAuthorizationUrl?: string | null;
};

export function buildOnboardingInboxConnectionUpdate(
  connection: InboxConnection,
  result: OnboardingInboxConnectionResult,
): InboxConnection {
  const isCustomImap = connection.provider === "custom_imap";
  const customImapFailure =
    isCustomImap && result.connectionStatus === "connection_failed";

  return {
    ...connection,
    serverMailboxId: isCustomImap
      ? null
      : connection.serverMailboxId,
    connected: isCustomImap ? false : result.connected,
    connectionMethod: result.connectionMethod,
    connectionStatus: isCustomImap
      ? customImapFailure
        ? "connection_failed"
        : "not_connected"
      : result.connectionStatus,
    connectionMessage: result.connectionMessage ?? null,
    oauthAuthorizationUrl: result.oauthAuthorizationUrl ?? null,
    customImap: {
      ...connection.customImap,
      password: "",
    },
    customSmtp: {
      ...connection.customSmtp,
      password: "",
    },
    ...(isCustomImap
      ? {
          imapConnectionStatus: customImapFailure
            ? ("connection_failed" as const)
            : ("not_connected" as const),
          smtpConnectionStatus: "not_configured" as const,
          fullyConnected: false,
        }
      : {}),
  };
}

function dedupeInboxes(inboxes: Array<InboxId | null | undefined>) {
  return [...new Set(inboxes.filter((inboxId): inboxId is InboxId => Boolean(inboxId)))];
}

export function areSelectedOnboardingInboxesFullyConnected(
  state: Pick<OnboardingState, "selectedInboxes" | "inboxConnections">,
) {
  return (
    state.selectedInboxes.length > 0 &&
    state.selectedInboxes.every((inboxId) =>
      isOnboardingInboxFullyConnected(
        state.inboxConnections[inboxId],
      ),
    )
  );
}

interface OnboardingFlowProps {
  state: OnboardingState;
  currentStep: number;
  onStepChange: (step: number) => void;
  onStateChange: (
    value: OnboardingState | ((current: OnboardingState) => OnboardingState),
  ) => void;
  onSafeStateChange: (
    value: OnboardingState | ((current: OnboardingState) => OnboardingState),
  ) => void;
  onOpenWorkspace: (userConfig: UserConfig) => void;
  onReloadAccountConfig?: (
    snapshot: CustomImapOnboardingAttemptSnapshot,
    signal: AbortSignal,
  ) => Promise<CustomImapOnboardingReconciliationResult>;
  canOpenWorkspace?: boolean;
  isPreviewMode?: boolean;
  previewControls?: ReactNode;
}

export function OnboardingFlow({
  state,
  currentStep,
  onStepChange,
  onStateChange,
  onSafeStateChange,
  onOpenWorkspace,
  onReloadAccountConfig = async () => ({ status: "required" }),
  canOpenWorkspace = false,
  isPreviewMode = false,
  previewControls,
}: OnboardingFlowProps) {
  const [showWorkspaceBlockedMessage, setShowWorkspaceBlockedMessage] =
    useState(false);
  const [
    customImapAttemptGuard,
    setCustomImapAttemptGuard,
  ] = useState<CustomImapOnboardingAttemptGuard | null>(null);
  const customImapAttemptGuardRef =
    useRef<CustomImapOnboardingAttemptGuard | null>(null);
  const customImapInteractionLocked =
    isCustomImapOnboardingInteractionLocked(customImapAttemptGuard);

  const updateCustomImapAttemptGuard = (
    guard: CustomImapOnboardingAttemptGuard | null,
  ) => {
    customImapAttemptGuardRef.current = guard;
    setCustomImapAttemptGuard(guard);
  };

  const mutationIsLocked = () =>
    shouldBlockOnboardingMutation(customImapAttemptGuardRef.current);
  const step = onboardingFlowProgression.clamp(currentStep);
  const showSetupProgress = step > 0;
  const isFinalScreen = step === 3;
  const sidebarHelperText =
    (
      {
        1: "Choose which mail types stay Normal and which should be Low.",
        2: "Connect each selected source account. More inboxes can be added later.",
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
    return isOnboardingInboxFullyConnected(connection);
  });
  const everySelectedInboxIsFullyConnected =
    areSelectedOnboardingInboxesFullyConnected(state);
  const normalizedFocusPreferences = normalizeFocusPreferences(state.focusPreferences);
  const lowFocusLabels = onboardingFocusItems
    .filter((item) =>
      item.fields.every((field) => normalizedFocusPreferences[field] === "low"),
    )
    .map((item) => item.label);

  const canGoNext = useMemo(() => {
    if (step === 2) {
      return everySelectedInboxIsFullyConnected;
    }

    return true;
  }, [everySelectedInboxIsFullyConnected, step]);

  const setFocusPreference = (
    fields: Array<keyof OnboardingState["focusPreferences"]>,
    value: SelectableFocusPreferenceLevel,
  ) => {
    onSafeStateChange((current) => ({
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
    if (mutationIsLocked()) {
      return;
    }
    onSafeStateChange((current) => {
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
    !mutationIsLocked() &&
    state.selectedInboxes.length > 1 &&
    state.selectedInboxes.includes(inboxId);

  const removeSelectedInbox = (inboxId: InboxId) => {
    if (mutationIsLocked()) {
      return;
    }
    onSafeStateChange((current) => {
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
    if (mutationIsLocked()) {
      return;
    }
    onStateChange((current) => ({
      ...current,
      inboxConnections: {
        ...current.inboxConnections,
        [inboxId]: {
          ...getInboxConnection(current, inboxId),
          serverMailboxId: null,
          connected: false,
          connectionMethod: getProviderConnectionMethod(provider),
          connectionStatus: getDefaultConnectionStatus(provider),
          imapConnectionStatus: "not_connected",
          smtpConnectionStatus: "not_configured",
          fullyConnected: false,
          connectionMessage: null,
          oauthAuthorizationUrl: null,
          provider,
          customImap: {
            ...applyProviderDefaults(
              provider,
              getInboxConnection(current, inboxId).customImap,
              getInboxConnection(current, inboxId).email,
            ),
            ssl: true,
            password: "",
          },
          customSmtp: {
            ...getInboxConnection(current, inboxId).customSmtp,
            password: "",
          },
        },
      },
    }));
  };

  const setEmail = (inboxId: InboxId, email: string) => {
    if (mutationIsLocked()) {
      return;
    }
    onStateChange((current) => ({
      ...current,
      inboxConnections: {
        ...current.inboxConnections,
        [inboxId]: {
          ...getInboxConnection(current, inboxId),
          serverMailboxId: null,
          connected: false,
          connectionStatus: getDefaultConnectionStatus(
            getInboxConnection(current, inboxId).provider,
          ),
          imapConnectionStatus: "not_connected",
          smtpConnectionStatus: "not_configured",
          fullyConnected: false,
          connectionMessage: null,
          oauthAuthorizationUrl: null,
          email,
          customImap: {
            ...getInboxConnection(current, inboxId).customImap,
            ...(usesEmailAsImapUsername(getInboxConnection(current, inboxId).provider)
              ? { username: email.trim() }
              : {}),
            password: "",
          },
          customSmtp: {
            ...getInboxConnection(current, inboxId).customSmtp,
            password: "",
          },
        },
      },
    }));
  };

  const setCustomImap = (
    inboxId: InboxId,
    field: keyof CustomImapSettings,
    value: string | boolean,
  ) => {
    if (
      mutationIsLocked() ||
      field === "password" ||
      (field === "ssl" && value !== true)
    ) {
      return;
    }

    onStateChange((current) => ({
      ...current,
      inboxConnections: {
        ...current.inboxConnections,
        [inboxId]: {
          ...getInboxConnection(current, inboxId),
          serverMailboxId: null,
          connected: false,
          connectionStatus: getDefaultConnectionStatus(
            getInboxConnection(current, inboxId).provider,
          ),
          imapConnectionStatus: "not_connected",
          smtpConnectionStatus: "not_configured",
          fullyConnected: false,
          connectionMessage: null,
          oauthAuthorizationUrl: null,
          customImap: {
            ...getInboxConnection(current, inboxId).customImap,
            [field]: value,
            password: "",
          },
          customSmtp: {
            ...getInboxConnection(current, inboxId).customSmtp,
            password: "",
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
    if (mutationIsLocked() || field === "password") {
      return;
    }

    onStateChange((current) => {
      const currentConnection = getInboxConnection(current, inboxId);
      return {
        ...current,
        inboxConnections: {
          ...current.inboxConnections,
          [inboxId]: {
            ...currentConnection,
            smtpConnectionStatus: "not_configured",
            fullyConnected: false,
            connectionMessage: null,
            customSmtp: {
              ...currentConnection.customSmtp,
              [field]: value,
              password: "",
            },
          },
        },
      };
    });
  };

  const reuseCustomImap = (inboxId: InboxId, settings: CustomImapSettings) => {
    if (mutationIsLocked()) {
      return;
    }
    onStateChange((current) => ({
      ...current,
      inboxConnections: {
        ...current.inboxConnections,
        [inboxId]: {
          ...getInboxConnection(current, inboxId),
          serverMailboxId: null,
          connected: false,
          connectionStatus: getDefaultConnectionStatus(
            getInboxConnection(current, inboxId).provider,
          ),
          imapConnectionStatus: "not_connected",
          smtpConnectionStatus: "not_configured",
          fullyConnected: false,
          connectionMessage: null,
          oauthAuthorizationUrl: null,
          customImap: {
            ...settings,
            ssl: true,
            password: "",
          },
          customSmtp: {
            ...getInboxConnection(current, inboxId).customSmtp,
            password: "",
          },
        },
      },
    }));
  };

  const connectInbox = (
    inboxId: InboxId,
    result: OnboardingInboxConnectionResult,
    messages: LiveInboxMessageSnapshot[] = [],
  ) => {
    onStateChange((current) => ({
      ...current,
      inboxConnections: {
        ...current.inboxConnections,
        [inboxId]: buildOnboardingInboxConnectionUpdate(
          getInboxConnection(current, inboxId),
          result,
        ),
      },
    }));

    const connection = state.inboxConnections[inboxId];

    if (
      !isPreviewMode &&
      connection?.provider !== "custom_imap" &&
      result.connected &&
      connection?.email.trim()
    ) {
      saveLiveInboxSnapshot({
        inboxId,
        email: connection.email.trim().toLowerCase(),
        fetchedAt: new Date().toISOString(),
        messages,
      });
    }
  };

  const applyAuthoritativeCustomImapConnectionForAttempt = (
    snapshot: CustomImapOnboardingAttemptSnapshot,
    result: Extract<
      CustomImapOnboardingReconciliationResult,
      { status: "matched" }
    >,
  ) => {
    if (
      customImapAttemptGuardRef.current?.attemptToken !==
        snapshot.attemptToken ||
      customImapAttemptGuardRef.current.onboardingInboxId !==
        snapshot.onboardingInboxId
    ) {
      return;
    }
    onStateChange((current) => {
      return applyAuthoritativeCustomImapConnection(
        current,
        snapshot,
        result,
      );
    });
  };

  const next = () => {
    if (mutationIsLocked() || !canGoNext) return;
    const nextStep = onboardingFlowProgression.next(step);

    if (nextStep !== step) {
      onStepChange(nextStep);
    }
  };

  const back = () => {
    if (mutationIsLocked()) {
      return;
    }
    setShowWorkspaceBlockedMessage(false);
    const previousStep = onboardingFlowProgression.back(step);

    if (previousStep !== step) {
      onStepChange(previousStep);
    }
  };

  const openWorkspace = () => {
    if (mutationIsLocked()) {
      return;
    }
    const didOpenWorkspace = onboardingFlowProgression.attemptWorkspaceOpen(
      canOpenWorkspace,
      () => onOpenWorkspace(userConfig),
    );

    setShowWorkspaceBlockedMessage(!didOpenWorkspace);
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
            onReloadAccountConfig={onReloadAccountConfig}
            onApplyAuthoritativeCustomImapConnection={
              applyAuthoritativeCustomImapConnectionForAttempt
            }
            customImapAttemptGuard={customImapAttemptGuard}
            onCustomImapAttemptGuardChange={
              updateCustomImapAttemptGuard
            }
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
        <CustomImapServerReloadRecovery
          visible={
            customImapAttemptGuard?.recovery === "reload"
          }
          onReload={() => window.location.reload()}
        />
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
              {isFinalScreen && showWorkspaceBlockedMessage ? (
                <p
                  role="status"
                  className="mb-4 text-center text-sm leading-6 text-ink/60"
                >
                  Mailbox setup must be safely completed before you can open your
                  workspace.
                </p>
              ) : null}
              <NavigationBar
                canGoBack={step > 0}
                backLabel={step === 3 ? "Edit setup" : undefined}
                onBack={back}
                onNext={step === 3 ? openWorkspace : next}
                nextLabel={nextLabel}
                isBackDisabled={customImapInteractionLocked}
                isNextDisabled={
                  customImapInteractionLocked ||
                  (step === 3 ? false : !canGoNext)
                }
              />
            </section>
          </div>
        </div>
      </div>
    </main>
  );
}
