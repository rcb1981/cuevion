import { useMemo, useState } from "react";
import { onboardingText } from "../../copy/onboardingCopy";
import {
  createCustomInboxId,
  createInboxConnection,
  mainInboxOptions,
  specializedInboxOptions,
} from "../../data/onboardingOptions";
import type { LiveInboxMessageSnapshot } from "../../lib/inboxConnectionApi";
import {
  applyProviderDefaults,
  createDefaultCustomSmtpSettings,
  getDefaultConnectionStatus,
  getProviderConnectionMethod,
  isImapCredentialsProvider,
  isOAuthConnectionProvider,
  usesEmailAsImapUsername,
} from "../../lib/inboxProviderDefaults";
import { mapRoleToInternal, type InternalRole } from "../../lib/roleMapping";
import { saveLiveInboxSnapshot } from "../../lib/liveInboxSnapshots";
import type {
  CustomInboxDefinition,
  CustomImapSettings,
  CustomSmtpSettings,
  FocusPreferenceLevel,
  InboxId,
  OnboardingState,
  PrimaryInboxType,
  ProviderId,
  RoleId,
} from "../../types/onboarding";
import type { UserConfig } from "../../types/userConfig";
import { NavigationBar } from "./NavigationBar";
import { ProgressIndicator } from "./ProgressIndicator";
import { StepComplete } from "./StepComplete";
import { StepConnectInboxes } from "./StepConnectInboxes";
import { StepFocusPreferences } from "./StepFocusPreferences";
import { StepInboxCount } from "./StepInboxCount";
import { StepInboxSetup } from "./StepInboxSetup";
import { StepRoleSelection } from "./StepRoleSelection";
import { StepWelcome } from "./StepWelcome";

const totalScreens = 7;
const totalProgressSteps = 6;

function dedupeInboxes(inboxes: Array<InboxId | null | undefined>) {
  return [...new Set(inboxes.filter((inboxId): inboxId is InboxId => Boolean(inboxId)))];
}

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

function buildSelectedInboxesForCount(
  inboxCount: OnboardingState["inboxCount"],
  primaryInbox: InboxId,
  secondInbox: InboxId | null,
  thirdInbox: InboxId | null,
  currentSelectedInboxes: InboxId[],
) {
  const orderedSlots = dedupeInboxes([
    primaryInbox,
    secondInbox,
    thirdInbox,
  ]);

  if (inboxCount === "1") {
    return [primaryInbox];
  }

  if (inboxCount === "2") {
    return orderedSlots.slice(0, 2);
  }

  if (inboxCount === "3") {
    return orderedSlots.slice(0, 3);
  }

  return dedupeInboxes([
    primaryInbox,
    ...currentSelectedInboxes.filter((inboxId) => inboxId !== primaryInbox),
  ]);
}

function getDefaultPrimaryInboxForRole(role: RoleId): InboxId {
  const internalRole = mapRoleToInternal(role);

  if (role === "ar_manager") {
    return "demo";
  }

  if (role === "label_ar_manager") {
    return "main";
  }

  if (internalRole === "product_manager") {
    return "business";
  }

  if (internalRole === "artist_manager") {
    return "main";
  }

  if (internalRole === "dj") {
    return "main";
  }

  if (internalRole === "producer") {
    return "main";
  }

  return "main";
}

function getDefaultFocusPreferencesForRole(
  role: RoleId,
): OnboardingState["focusPreferences"] {
  const internalRole = mapRoleToInternal(role);

  if (role === "ar_manager") {
    return {
      demos: "high",
      promo: "low",
      finance: "low",
      legal: "low",
      business: "medium",
      updates: "medium",
      distribution: "low",
      royalties: "low",
      promoReminders: "low",
      paymentReminders: "low",
    };
  }

  if (internalRole === "label_ar_manager") {
    return {
      demos: "high",
      promo: "medium",
      finance: "medium",
      legal: "medium",
      business: "high",
      updates: "medium",
      distribution: "medium",
      royalties: "medium",
      promoReminders: "medium",
      paymentReminders: "medium",
    };
  }

  const defaultsByInternalRole: Partial<
    Record<InternalRole, OnboardingState["focusPreferences"]>
  > = {
    label_manager: {
      demos: "medium",
      promo: "low",
      finance: "high",
      legal: "medium",
      business: "high",
      updates: "medium",
      distribution: "medium",
      royalties: "high",
      promoReminders: "low",
      paymentReminders: "high",
    },
    product_manager: {
      demos: "low",
      promo: "low",
      finance: "medium",
      legal: "medium",
      business: "high",
      updates: "high",
      distribution: "high",
      royalties: "medium",
      promoReminders: "low",
      paymentReminders: "medium",
    },
    artist_manager: {
      demos: "medium",
      promo: "medium",
      finance: "medium",
      legal: "low",
      business: "high",
      updates: "medium",
      distribution: "low",
      royalties: "high",
      promoReminders: "medium",
      paymentReminders: "high",
    },
    dj: {
      demos: "low",
      promo: "high",
      finance: "medium",
      legal: "low",
      business: "medium",
      updates: "low",
      distribution: "low",
      royalties: "high",
      promoReminders: "low",
      paymentReminders: "medium",
    },
    producer: {
      demos: "medium",
      promo: "medium",
      finance: "high",
      legal: "low",
      business: "medium",
      updates: "low",
      distribution: "low",
      royalties: "high",
      promoReminders: "low",
      paymentReminders: "high",
    },
    management: {
      demos: "low",
      promo: "low",
      finance: "high",
      legal: "high",
      business: "high",
      updates: "medium",
      distribution: "medium",
      royalties: "high",
      promoReminders: "low",
      paymentReminders: "high",
    },
  };

  return (
    defaultsByInternalRole[internalRole] ?? {
      demos: "medium",
      promo: "medium",
      finance: "medium",
      legal: "medium",
      business: "medium",
      updates: "medium",
      distribution: "medium",
      royalties: "medium",
      promoReminders: "medium",
      paymentReminders: "medium",
    }
  );
}

interface OnboardingFlowProps {
  state: OnboardingState;
  onStateChange: (
    value: OnboardingState | ((current: OnboardingState) => OnboardingState),
  ) => void;
  onOpenWorkspace: (userConfig: UserConfig) => void;
}

export function OnboardingFlow({
  state,
  onStateChange,
  onOpenWorkspace,
}: OnboardingFlowProps) {
  const [step, setStep] = useState(0);
  const showSetupProgress = step > 0;
  const isFinalScreen = step === 6;
  const sidebarHelperText =
    (
      {
        1: onboardingText.sidebar.stepHelper[1],
        2: "Set what matters most to you inside the inbox before the workspace is structured.",
        3: onboardingText.sidebar.stepHelper[2],
        4: onboardingText.sidebar.stepHelper[3],
        5: onboardingText.sidebar.stepHelper[5],
      } as const
    )[step as 1 | 2 | 3 | 4 | 5] ?? null;

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

  const canGoNext = useMemo(() => {
    if (step === 1) {
      return Boolean(state.primaryRole);
    }

    if (step === 3) {
      return Boolean(state.inboxCount);
    }

    if (step === 4) {
      if (!state.primaryInbox) {
        return false;
      }

      if (state.primaryInbox === "main" && state.primaryInboxType === null) {
        return false;
      }

      if (state.inboxCount === "1") {
        return true;
      }

      if (state.inboxCount === "2") {
        return Boolean(state.selectedInboxes[1]);
      }

      if (state.inboxCount === "3") {
        return Boolean(state.selectedInboxes[1] && state.selectedInboxes[2]);
      }

      return state.selectedInboxes.length >= getRequiredInboxCount(state.inboxCount);
    }

    if (step === 5) {
      return state.selectedInboxes.every((inboxId) => {
        const connection = getInboxConnection(state, inboxId);
        if (!connection.provider || !connection.email.trim()) {
          return false;
        }

        if (isOAuthConnectionProvider(connection.provider)) {
          return (
            connection.connectionStatus === "oauth_required" ||
            connection.connectionStatus === "waiting_for_authentication" ||
            connection.connectionStatus === "authenticated_pending_activation" ||
            connection.connectionStatus === "connected"
          );
        }

        if (!connection.connected) {
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
    onStateChange((current) => {
      const nextPrimaryInbox = getDefaultPrimaryInboxForRole(role);
      const currentSecondInbox =
        current.selectedInboxes.find(
          (inboxId) => inboxId !== (current.primaryInbox ?? nextPrimaryInbox),
        ) ?? null;
      const currentThirdInbox =
        current.selectedInboxes.filter(
          (inboxId) => inboxId !== (current.primaryInbox ?? nextPrimaryInbox),
        )[1] ?? null;
      const nextSelectedInboxes = buildSelectedInboxesForCount(
        current.inboxCount,
        nextPrimaryInbox,
        currentSecondInbox,
        currentThirdInbox,
        current.selectedInboxes,
      );

      return {
        ...current,
        primaryRole: role,
        internalRole: mapRoleToInternal(role),
        secondaryRole: current.secondaryRole === role ? null : current.secondaryRole,
        primaryInbox: nextPrimaryInbox,
        selectedInboxes: nextSelectedInboxes,
        focusPreferences: getDefaultFocusPreferencesForRole(role),
      };
    });
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

  const userConfig: UserConfig = {
    primaryRole: state.primaryRole,
    internalRole: state.internalRole,
    focusPreferences: state.focusPreferences,
    inboxCount: state.inboxCount,
    selectedInboxes: state.selectedInboxes,
    primaryInboxType: state.primaryInboxType,
  };

  const availableInboxOptions = [
    ...mainInboxOptions,
    ...specializedInboxOptions,
    ...state.customInboxes.map((inbox) => ({
      id: inbox.id,
      label: inbox.name,
    })),
  ];

  const setPrimaryInbox = (inboxId: InboxId) => {
    onStateChange((current) => {
      const currentNonPrimaryInboxes = current.selectedInboxes.filter(
        (selectedInboxId) => selectedInboxId !== (current.primaryInbox ?? inboxId),
      );
      const secondInbox = currentNonPrimaryInboxes[0] ?? null;
      const thirdInbox = currentNonPrimaryInboxes[1] ?? null;
      const nextSelectedInboxes = buildSelectedInboxesForCount(
        current.inboxCount,
        inboxId,
        secondInbox,
        thirdInbox,
        current.selectedInboxes,
      );

      return {
        ...current,
        primaryInbox: inboxId,
        selectedInboxes: nextSelectedInboxes,
      };
    });
  };

  const setPrimaryInboxType = (primaryInboxType: PrimaryInboxType | null) => {
    onStateChange((current) => ({
      ...current,
      primaryInboxType,
    }));
  };

  const setSecondInbox = (inboxId: InboxId | null) => {
    onStateChange((current) => {
      if (!current.primaryInbox) {
        return current;
      }

      const currentNonPrimaryInboxes = current.selectedInboxes.filter(
        (selectedInboxId) => selectedInboxId !== current.primaryInbox,
      );
      const nextSecondInbox =
        inboxId && inboxId !== current.primaryInbox ? inboxId : null;
      const nextThirdInbox =
        currentNonPrimaryInboxes.find((selectedInboxId) => selectedInboxId !== nextSecondInbox) ??
        null;

      return {
        ...current,
        selectedInboxes: buildSelectedInboxesForCount(
          current.inboxCount,
          current.primaryInbox,
          nextSecondInbox,
          nextThirdInbox,
          current.selectedInboxes,
        ),
      };
    });
  };

  const setThirdInbox = (inboxId: InboxId | null) => {
    onStateChange((current) => {
      if (!current.primaryInbox) {
        return current;
      }

      const currentNonPrimaryInboxes = current.selectedInboxes.filter(
        (selectedInboxId) => selectedInboxId !== current.primaryInbox,
      );
      const currentSecondInbox = currentNonPrimaryInboxes[0] ?? null;
      const nextThirdInbox =
        inboxId &&
        inboxId !== current.primaryInbox &&
        inboxId !== currentSecondInbox
          ? inboxId
          : null;

      return {
        ...current,
        selectedInboxes: buildSelectedInboxesForCount(
          current.inboxCount,
          current.primaryInbox,
          currentSecondInbox,
          nextThirdInbox,
          current.selectedInboxes,
        ),
      };
    });
  };

  const toggleAdditionalInbox = (inboxId: InboxId) => {
    onStateChange((current) => {
      if (current.primaryInbox === inboxId) {
        return current;
      }

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
        },
      },
    }));

    const connection = state.inboxConnections[inboxId];

    if (result.connected && connection?.email.trim()) {
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
      const id = createCustomInboxId(trimmedName);
      const customInbox: CustomInboxDefinition = { id, name: trimmedName };
      const nextCustomInboxes = [...current.customInboxes, customInbox];
      const nextInboxConnections = {
        ...current.inboxConnections,
        [id]: createInboxConnection(),
      };
      const isExpandedInboxMode =
        current.inboxCount === "4+" || current.inboxCount === "not_sure";
      const maxActiveInboxCount = getMaxActiveInboxCount(current.inboxCount);
      added = true;

      if (!isExpandedInboxMode) {
        return {
          ...current,
          customInboxes: nextCustomInboxes,
          inboxConnections: nextInboxConnections,
        };
      }

      if (current.selectedInboxes.length >= maxActiveInboxCount) {
        return {
          ...current,
          customInboxes: nextCustomInboxes,
          inboxConnections: nextInboxConnections,
        };
      }

      return {
        ...current,
        customInboxes: nextCustomInboxes,
        selectedInboxes: [...current.selectedInboxes, id],
        inboxConnections: nextInboxConnections,
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
                const resolvedPrimaryInbox = current.primaryInbox ?? "main";
                const currentNonPrimaryInboxes = current.selectedInboxes.filter(
                  (inboxId) => inboxId !== resolvedPrimaryInbox,
                );
                const nextSelectedInboxes = buildSelectedInboxesForCount(
                  inboxCount,
                  resolvedPrimaryInbox,
                  currentNonPrimaryInboxes[0] ?? null,
                  currentNonPrimaryInboxes[1] ?? null,
                  current.selectedInboxes.slice(0, maxActiveInboxCount),
                ).slice(0, maxActiveInboxCount);

                return {
                  ...current,
                  inboxCount,
                  primaryInbox: resolvedPrimaryInbox,
                  selectedInboxes: nextSelectedInboxes,
                };
              })
            }
          />
        );
      case 4:
        return (
          <StepInboxSetup
            inboxCount={state.inboxCount}
            primaryInbox={state.primaryInbox}
            primaryInboxType={state.primaryInboxType}
            selectedInboxes={state.selectedInboxes}
            availableInboxOptions={availableInboxOptions}
            customInboxes={state.customInboxes}
            maxActiveInboxCount={getMaxActiveInboxCount(state.inboxCount)}
            requiredInboxCount={getRequiredInboxCount(state.inboxCount)}
            onPrimaryInboxChange={setPrimaryInbox}
            onPrimaryInboxTypeChange={setPrimaryInboxType}
            onSecondaryInboxChange={setSecondInbox}
            onThirdInboxChange={setThirdInbox}
            onToggleAdditionalInbox={toggleAdditionalInbox}
            onAddCustomInbox={addCustomInbox}
          />
        );
      case 5:
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
          />
        );
      case 6:
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
      : step === 5
        ? onboardingText.navigation.completeSetup
        : step === 6
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
                    currentStep={step === 6 ? totalProgressSteps : step}
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
                    currentStep={step === 6 ? totalProgressSteps : step}
                    totalSteps={totalProgressSteps}
                  />
                </div>
              ) : null}
              <div className="flex-1">{renderStep()}</div>
              <NavigationBar
                canGoBack={step > 0}
                backLabel={step === 6 ? "Edit setup" : undefined}
                onBack={back}
                onNext={
                  step === 6
                    ? () => {
                        console.log(
                          "[DEBUG] OnboardingFlow selectedInboxes:",
                          state.selectedInboxes,
                        );
                        onOpenWorkspace(userConfig);
                      }
                    : next
                }
                nextLabel={nextLabel}
                isNextDisabled={step === 6 ? false : !canGoNext}
              />
            </section>
          </div>
        </div>
      </div>
    </main>
  );
}
