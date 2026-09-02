import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { Auth0LoginView } from "./components/auth/Auth0LoginView";
import {
  OnboardingFlow,
  areSelectedOnboardingInboxesFullyConnected,
} from "./components/onboarding/OnboardingFlow";
import { WorkspaceTransition } from "./components/workspace/WorkspaceTransition";
import {
  createInboxConnection,
  initialOnboardingState,
} from "./data/onboardingOptions";
import {
  fetchTeamInvite,
  mutateTeamInvite,
  type PublicTeamInvite,
  type TeamLifecycleFailureStatus,
} from "./lib/teamInviteApi";
import {
  completeUserOnboarding,
  loadUserAccountConfig,
  loadUserAccountConfigAfterPendingWrites,
  saveUserAccountConfig,
  setUserAccountConfigHydrationEchoExpectation,
  type UserAccountConfig,
  type UserAccountConfigReadResult,
  type UserAccountConfigSaveResult,
} from "./lib/userConfigApi";
import {
  sanitizeManagedInboxCredentials,
  sanitizeStoredMailboxCredentialJson,
} from "./lib/mailboxCredentialPersistence";
import {
  doesCustomImapOnboardingSnapshotMatchState,
  type CustomImapOnboardingAttemptSnapshot,
  type CustomImapOnboardingReconciliationResult,
} from "./lib/customImapOnboardingAttempt";
import {
  ONBOARDING_STEP_MAX,
  ONBOARDING_STEP_MIN,
  clampOnboardingStep,
  normalizeFocusPreferences,
  type CustomImapSettings,
  type CustomSmtpSettings,
  type InboxId,
  type MailboxCapabilityConnectionStatus,
  type OnboardingChoices,
  type OnboardingSessionV1,
  type OnboardingState,
} from "./types/onboarding";
import type { UserConfig } from "./types/userConfig";
import {
  getSessionAccountStorageKey,
  isAuth0LoginPath,
  loadStartupSession,
} from "./lib/authApi";
import { GMAIL_OAUTH_RECONNECT_REQUIRED_CONNECTION_MESSAGE } from "./lib/inboxConnectionApi";
import { ExternalCollaborationGuestView } from "./components/collaboration/ExternalCollaborationGuestView";
import {
  parseCollaborationGuestEntryRoute,
  type CollaborationGuestRoute,
} from "./lib/collaborationGuestInviteLink";

const WorkspaceShell = lazy(() =>
  import("./components/workspace/WorkspaceShell").then((module) => ({
    default: module.WorkspaceShell,
  })),
);

const ONBOARDING_STATE_STORAGE_KEY = "label-inbox-ai-onboarding-state";
const ONBOARDING_DRAFT_STATE_STORAGE_KEY = "label-inbox-ai-onboarding-draft-state";
const CUEVION_APP_VIEW_STORAGE_KEY = "cuevion-app-view";
const MANAGED_INBOXES_STORAGE_KEY = "cuevion-managed-inboxes";
const PENDING_OAUTH_MANAGED_INBOX_STORAGE_KEY = "cuevion-pending-oauth-managed-inbox";
const CUEVION_AUTH_STORAGE_KEY = "label-inbox-ai-auth-user";
const CUEVION_DISPLAY_NAME_OVERRIDES_STORAGE_KEY = "cuevion-display-name-overrides";
const PENDING_COLLAB_INVITE_STORAGE_KEY = "label-inbox-ai-pending-collab-invite";
const PENDING_COLLAB_INVITE_URL_STORAGE_KEY = "label-inbox-ai-pending-collab-invite-url";
const OAUTH_CALLBACK_RESULT_STORAGE_KEY = "cuevion-oauth-callback-result";
const WORKSPACE_THEME_MODE_STORAGE_KEY = "cuevion-workspace-theme-mode";
const AI_SUGGESTIONS_STORAGE_KEY = "cuevion-ai-suggestions-enabled";
const INBOX_CHANGES_STORAGE_KEY = "cuevion-inbox-changes-enabled";
const TEAM_ACTIVITY_STORAGE_KEY = "cuevion-team-activity-enabled";
const MAIL_SIGNATURES_STORAGE_KEY = "cuevion-mail-signatures";
const PRIMARY_MANAGED_INBOX_ID_STORAGE_KEY = "cuevion-primary-managed-inbox-id";
const MAILBOX_TITLE_OVERRIDES_STORAGE_KEY = "cuevion-mailbox-title-overrides";
const MAILBOX_FOCUS_PREFERENCE_OVERRIDES_STORAGE_KEY =
  "cuevion-mailbox-focus-preference-overrides";
const SMART_FOLDERS_STORAGE_KEY = "cuevion-smart-folders";
const premiumAccessButtonClass =
  "inline-flex h-10 items-center justify-center rounded-full border border-[rgba(218,194,142,0.56)] bg-[linear-gradient(180deg,rgba(237,222,184,0.98),rgba(199,166,104,0.96))] px-5 text-[0.72rem] font-semibold uppercase tracking-[0.15em] text-[rgba(29,58,48,0.96)] shadow-[inset_0_1px_0_rgba(255,252,240,0.66),inset_0_-1px_0_rgba(119,82,38,0.14),0_10px_22px_rgba(15,36,30,0.18)] transition-[background-image,border-color,transform,box-shadow] duration-150 hover:border-[rgba(231,207,156,0.66)] hover:bg-[linear-gradient(180deg,rgba(242,228,192,0.98),rgba(184,149,88,0.98))] hover:shadow-[inset_0_1px_0_rgba(255,252,240,0.72),inset_0_-1px_0_rgba(99,68,32,0.16),0_12px_26px_rgba(15,36,30,0.22)] hover:-translate-y-px active:translate-y-0 active:scale-[0.99] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(237,222,184,0.78)] focus-visible:ring-offset-2 focus-visible:ring-offset-[rgba(38,66,56,1)] disabled:cursor-not-allowed disabled:opacity-60";

type AuthenticatedCuevionUser = {
  email: string;
  name: string;
  userType: "member" | "guest";
  userId?: string;
  workspaceId?: string;
};

type RootAppRoute = "login" | "preview" | "app";

type CollaborationInviteRoute = {
  mode: "invite" | "external_review";
  inviteToken: string;
  messageId?: string;
  inviteeEmail?: string;
  status?: string;
};
type TeamInviteRoute = {
  inviteToken: string;
};

type PersistedOnboardingSession = OnboardingSessionV1;
type AppView = "onboarding" | "transition" | "workspace";
type WorkspaceDataMode = "demo" | "live";
type AccountConfigHydrationStatus = "idle" | "loading" | "ready" | "error";
type WorkspaceCompletionStatus =
  | "idle"
  | "completing"
  | "error"
  | "inboxes_incomplete"
  | "verification_required";
type RetryableAccountConfigStatus = Exclude<
  UserAccountConfigReadResult["status"],
  "found" | "missing" | "unauthorized"
>;
type MemberSessionStatus =
  | "loading"
  | "authenticated"
  | "unauthenticated"
  | "unavailable";
type StoredManagedWorkspaceInbox = {
  id?: string;
  onboardingInboxId?: string;
  title?: string;
  email?: string;
  provider?: string | null;
  connected?: boolean;
  connectionMethod?: string | null;
  connectionStatus?: string;
  connectionMessage?: string | null;
  oauthAuthorizationUrl?: string | null;
  customImap?: unknown;
  customSmtp?: unknown;
  imapConnectionStatus?: MailboxCapabilityConnectionStatus;
  smtpConnectionStatus?: MailboxCapabilityConnectionStatus;
  imapPasswordSet?: boolean;
  smtpPasswordSet?: boolean;
  fullyConnected?: boolean;
  customImapFolderMappings?: {
    schemaVersion: 1;
    trashFolder: string;
  };
};
type StoredTeamMemberEntry = {
  email?: string;
  name?: string;
};
type MicrosoftOAuthCallbackStorageResult = {
  provider: "microsoft";
  email?: string;
  displayName?: string;
  connectionMethod?: string;
  connectionStatus?: string;
  connected?: boolean;
  message?: string | null;
};

type GoogleOAuthCallbackSignal = {
  status: "success" | "error";
  provider: "google";
  mode?: "initial" | "reconnect";
  inboxPosition?: string;
  email: string;
  mailboxId: string;
  message: string;
};

type PendingOAuthManagedInbox = {
  id?: string;
  mailboxId?: string;
  mode?: "initial" | "reconnect";
  title?: string;
  email?: string;
  provider?: string | null;
};

type DisplayNameOverrideStore = Record<string, string>;

type AccountConfigStorage = Pick<
  Storage,
  "getItem" | "setItem" | "removeItem"
>;

type LocalOnboardingStorageKeys = {
  state: string;
  draft: string;
  view: string;
};

type LocalOnboardingIdentityScope = {
  hydrationKey: string;
  storageKeys: LocalOnboardingStorageKeys | null;
};

type LocalOnboardingIdentityInput = {
  userType: AuthenticatedCuevionUser["userType"] | null;
  userEmail?: string | null;
  ephemeralIdentity?: string | null;
  collaborationInvite?: CollaborationInviteRoute | null;
  teamInviteToken?: string | null;
};

type AccountConfigStartupAccountState = {
  displayNameOverrides: DisplayNameOverrideStore;
  authoritativeManagedInboxes: StoredManagedWorkspaceInbox[];
  persistedOnboardingSession: PersistedOnboardingSession | null;
  onboardingState: OnboardingState;
  onboardingStep: number;
  userConfig: UserConfig | null;
  view: Extract<AppView, "onboarding" | "workspace">;
};

type LocalOnboardingHydrationState = Omit<
  AccountConfigStartupAccountState,
  "displayNameOverrides" | "authoritativeManagedInboxes"
>;

function canUseHydratedLocalAccountState(
  sessionStatus: MemberSessionStatus,
  hydrationStatus: AccountConfigHydrationStatus,
) {
  return sessionStatus === "authenticated" && hydrationStatus === "ready";
}

function resolveAccountConfigSessionDisposition({
  hasInviteRoute,
  sessionStatus,
  userType,
  accountStorageKey,
}: {
  hasInviteRoute: boolean;
  sessionStatus: MemberSessionStatus;
  userType: AuthenticatedCuevionUser["userType"] | null;
  accountStorageKey: string;
}): "invite" | "guest" | "member" | "idle" {
  if (hasInviteRoute) {
    return "invite";
  }
  if (sessionStatus === "authenticated" && userType === "guest") {
    return "guest";
  }
  if (
    sessionStatus === "authenticated" &&
    userType === "member" &&
    accountStorageKey
  ) {
    return "member";
  }
  return "idle";
}

function canOpenWorkspaceWithoutServerCompletion(
  userType: AuthenticatedCuevionUser["userType"] | null,
) {
  return userType === "guest";
}

type AccountConfigHydrationOutcome =
  | {
      status: "found";
      accountState: AccountConfigStartupAccountState;
      didResetOnboarding: boolean;
      clearResetQuery: boolean;
      expectedWorkspaceHydrationEcho: UserAccountConfig | null;
    }
  | {
      status: "missing";
      accountState: AccountConfigStartupAccountState;
      didResetOnboarding: boolean;
      clearResetQuery: boolean;
    }
  | { status: "unauthorized" }
  | { status: "error"; errorStatus: RetryableAccountConfigStatus };

type AccountConfigSaveQueueRequest = {
  accountKey: string;
  revision: number;
  config: UserAccountConfig;
  conflictRetryCount: number;
};

function createAccountConfigSaveQueue({
  save = saveUserAccountConfig,
  onClean,
  scheduleRetry = (callback, delayMs) => window.setTimeout(callback, delayMs),
  cancelRetry = (handle) => window.clearTimeout(handle),
}: {
  save?: (config: UserAccountConfig) => Promise<UserAccountConfigSaveResult>;
  onClean?: (saved: {
    accountKey: string;
    revision: number;
    config: UserAccountConfig;
  }) => void;
  scheduleRetry?: (callback: () => void, delayMs: number) => number;
  cancelRetry?: (handle: number) => void;
} = {}) {
  const conflictRetryDelaysMs = [120, 320] as const;
  let activeAccountKey = "";
  let generation = 0;
  let isSaving = false;
  let dirty = false;
  let latestRevision = 0;
  let pendingRequest: AccountConfigSaveQueueRequest | null = null;
  let scheduledRetry:
    | {
        handle: number;
        request: AccountConfigSaveQueueRequest;
        generation: number;
      }
    | null = null;

  const clearScheduledRetry = () => {
    if (!scheduledRetry) {
      return;
    }
    cancelRetry(scheduledRetry.handle);
    scheduledRetry = null;
  };

  const scheduleConflictRetry = (
    request: AccountConfigSaveQueueRequest,
    requestGeneration: number,
  ) => {
    const delayMs = conflictRetryDelaysMs[request.conflictRetryCount];
    if (delayMs === undefined) {
      return false;
    }

    clearScheduledRetry();
    const retryRequest = {
      ...request,
      conflictRetryCount: request.conflictRetryCount + 1,
    };
    const handle = scheduleRetry(() => {
      const currentRetry = scheduledRetry;
      if (
        !currentRetry ||
        currentRetry.handle !== handle ||
        currentRetry.generation !== generation ||
        currentRetry.request.accountKey !== activeAccountKey
      ) {
        return;
      }

      scheduledRetry = null;
      if (
        !pendingRequest ||
        pendingRequest.revision <= currentRetry.request.revision
      ) {
        pendingRequest = currentRetry.request;
      }
      void drain();
    }, delayMs);
    scheduledRetry = {
      handle,
      request: retryRequest,
      generation: requestGeneration,
    };
    return true;
  };

  const drain = async () => {
    if (isSaving || !pendingRequest) {
      return;
    }

    const request = pendingRequest;
    const requestGeneration = generation;
    pendingRequest = null;
    isSaving = true;

    let success = false;
    let savedConfig: UserAccountConfig | null = null;
    let retryableConflict = false;
    try {
      const result = await save(request.config);
      success = result.status === "found";
      retryableConflict =
        result.status === "conflict" &&
        result.error.code === "user_config_write_conflict";
      if (result.status === "found") {
        savedConfig = result.config;
      }
    } catch {
      success = false;
    }

    if (
      requestGeneration !== generation ||
      request.accountKey !== activeAccountKey
    ) {
      isSaving = false;
      if (pendingRequest) {
        void drain();
      }
      return;
    }

    isSaving = false;
    dirty =
      !success ||
      pendingRequest !== null ||
      request.revision < latestRevision;

    if (
      retryableConflict &&
      pendingRequest === null &&
      request.revision === latestRevision
    ) {
      scheduleConflictRetry(request, requestGeneration);
    }

    if (success && !dirty) {
      try {
        onClean?.({
          accountKey: request.accountKey,
          revision: request.revision,
          config: savedConfig ?? request.config,
        });
      } catch {
        // Saving succeeded; URL cleanup can safely be retried on the next startup.
      }
    }

    if (pendingRequest) {
      void drain();
    }
  };

  return {
    cancel() {
      generation += 1;
      clearScheduledRetry();
      activeAccountKey = "";
      dirty = false;
      latestRevision = 0;
      pendingRequest = null;
    },
    reset(accountKey: string) {
      generation += 1;
      clearScheduledRetry();
      activeAccountKey = accountKey;
      dirty = false;
      latestRevision = 0;
      pendingRequest = null;
    },
    markDirty(accountKey: string) {
      if (!accountKey || accountKey !== activeAccountKey) {
        return null;
      }

      clearScheduledRetry();
      latestRevision += 1;
      dirty = true;
      return latestRevision;
    },
    isDirty(accountKey: string) {
      return accountKey === activeAccountKey && dirty;
    },
    prepareForCompletion(accountKey: string) {
      if (!accountKey || accountKey !== activeAccountKey) {
        return false;
      }
      generation += 1;
      clearScheduledRetry();
      dirty = false;
      latestRevision = 0;
      pendingRequest = null;
      return true;
    },
    enqueue({
      accountKey,
      config,
    }: Omit<AccountConfigSaveQueueRequest, "revision" | "conflictRetryCount">) {
      if (
        !accountKey ||
        accountKey !== activeAccountKey ||
        !dirty ||
        latestRevision < 1
      ) {
        return false;
      }

      clearScheduledRetry();
      pendingRequest = {
        accountKey,
        revision: latestRevision,
        config,
        conflictRetryCount: 0,
      };
      void drain();
      return true;
    },
  };
}

function createAccountConfigHydrator(
  load: () => Promise<UserAccountConfigReadResult> = loadUserAccountConfig,
) {
  let generation = 0;

  return {
    cancel() {
      generation += 1;
    },
    async hydrate({
      accountStorageOwnerKey,
      storage,
      resetOnboarding,
      clearResetQuery,
    }: {
      accountStorageOwnerKey: string;
      storage: AccountConfigStorage;
      resetOnboarding: boolean;
      clearResetQuery: () => void;
    }): Promise<AccountConfigHydrationOutcome | { status: "cancelled" }> {
      const requestGeneration = ++generation;
      let result: UserAccountConfigReadResult;
      try {
        result = await load();
      } catch {
        if (requestGeneration !== generation) {
          return { status: "cancelled" };
        }
        return { status: "error", errorStatus: "network_error" };
      }

      if (requestGeneration !== generation) {
        return { status: "cancelled" };
      }

      try {
        const outcome = applyLoadedUserAccountConfig(
          result,
          accountStorageOwnerKey,
          storage,
          resetOnboarding,
        );
        if (
          (outcome.status === "found" || outcome.status === "missing") &&
          outcome.clearResetQuery &&
          !(outcome.status === "found" && outcome.didResetOnboarding)
        ) {
          clearResetQuery();
        }
        return outcome;
      } catch {
        return { status: "error", errorStatus: "unavailable" };
      }
    },
  };
}

function isOnboardingPreviewRoute() {
  if (typeof window === "undefined") {
    return false;
  }

  const params = new URLSearchParams(window.location.search);
  return (
    window.location.pathname.replace(/\/+$/, "") === "/onboarding-preview" ||
    params.get("preview") === "onboarding"
  );
}

function resolveRootAppRoute(): RootAppRoute {
  if (typeof window === "undefined") {
    return "app";
  }

  if (isAuth0LoginPath(window.location.pathname)) {
    return "login";
  }

  return isOnboardingPreviewRoute() ? "preview" : "app";
}

export function resolveSafeCollaborationGuestRoute(): CollaborationGuestRoute | null {
  if (typeof window === "undefined") {
    return null;
  }
  return parseCollaborationGuestEntryRoute(
    window.location.hash,
    window.location.search,
  );
}

function createPreviewOnboardingState(): OnboardingState {
  return normalizeOnboardingState(
    JSON.parse(JSON.stringify(initialOnboardingState)) as Partial<OnboardingState>,
  );
}

function buildUserConfig(state: OnboardingState): UserConfig {
  return {
    primaryRole: state.primaryRole,
    internalRole: state.internalRole,
    focusPreferences: normalizeFocusPreferences(state.focusPreferences),
    inboxCount: state.inboxCount,
    selectedInboxes: state.selectedInboxes,
    primaryInboxType: state.primaryInboxType,
  };
}

function ComingSoonLanding() {
  return (
    <div className="min-h-screen bg-[#264238] px-6 py-10 text-[rgba(248,247,242,0.98)]">
      <div className="mx-auto flex min-h-[calc(100vh-5rem)] max-w-[760px] items-center justify-center">
        <div className="flex w-full flex-col items-center justify-center text-center">
          <div className="mb-8 inline-flex items-center gap-4 text-[rgba(248,247,242,0.98)]">
            <span
              aria-hidden="true"
              className="flex h-14 w-14 items-center justify-center rounded-full border border-[rgba(255,255,255,0.28)] bg-[rgba(255,255,255,0.1)] shadow-[inset_0_1px_0_rgba(255,255,255,0.12)] backdrop-blur"
            >
              <span className="h-4 w-4 rounded-full bg-[rgba(248,247,242,0.98)]" />
            </span>
          </div>
          <h1 className="text-[2.9rem] font-semibold tracking-[-0.06em] text-[rgba(255,255,255,0.99)] sm:text-[4.4rem]">
            Cuevion
          </h1>
          <p className="mt-4 max-w-[32rem] text-[1.05rem] font-medium tracking-[-0.02em] text-[rgba(244,242,235,0.82)] sm:text-[1.35rem]">
            Email for the music industry.
          </p>
          <p className="mt-6 text-[0.9rem] font-medium tracking-[0.08em] text-[rgba(244,242,235,0.56)]">
            Coming soon...
          </p>
        </div>
      </div>
    </div>
  );
}

function WorkspaceLoadingFallback() {
  return (
    <div className="min-h-screen bg-[linear-gradient(180deg,#f6efe7_0%,#efe5da_100%)] px-6 py-10 text-[color:#2f2a24] dark:bg-[linear-gradient(180deg,#171411_0%,#221c17_100%)] dark:text-[color:#f1e9de]">
      <div className="mx-auto flex min-h-[calc(100vh-5rem)] max-w-[560px] items-center justify-center text-center">
        <p className="text-[0.98rem] leading-7 text-[rgba(88,80,71,0.84)] dark:text-[rgba(222,211,200,0.76)]">
          Opening Cuevion…
        </p>
      </div>
    </div>
  );
}

function isPublicLandingHost() {
  if (typeof window === "undefined") {
    return false;
  }

  const hostname = window.location.hostname.toLowerCase();

  return hostname === "cuevion.com" || hostname === "www.cuevion.com";
}

function normalizeOnboardingState(value: Partial<OnboardingState>): OnboardingState {
  return {
    ...initialOnboardingState,
    ...value,
    internalRole: value.internalRole ?? null,
    primaryInbox: value.primaryInbox ?? initialOnboardingState.primaryInbox,
    primaryInboxType: value.primaryInboxType ?? null,
    focusPreferences: normalizeFocusPreferences(value.focusPreferences),
    customInboxes: Array.isArray(value.customInboxes) ? value.customInboxes : [],
    inboxConnections: {
      ...initialOnboardingState.inboxConnections,
      ...(value.inboxConnections ?? {}),
    },
  };
}

const buildTeamMembersStorageKey = (workspaceKey: string) =>
  `cuevion-team-members:${workspaceKey}`;

function isValidAuthEmail(value: string) {
  return /^[^\s@]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$/.test(
    value.trim(),
  );
}

function formatUserNameFromEmail(email: string) {
  const localPart = email.split("@")[0] ?? "Guest user";
  const normalizedLocalPart = localPart.replace(/[._-]+/g, " ").trim();

  if (!normalizedLocalPart) {
    return "Guest user";
  }

  return normalizedLocalPart.replace(/\b\w/g, (character) =>
    character.toUpperCase(),
  );
}

function normalizeCollaborationUser(value: unknown): AuthenticatedCuevionUser | null {
  if (!value || typeof value !== "object") {
    return null;
  }

  const nextValue = value as Partial<AuthenticatedCuevionUser>;

  if (
    typeof nextValue.email !== "string" ||
    typeof nextValue.name !== "string" ||
    (nextValue.userType !== "member" && nextValue.userType !== "guest")
  ) {
    return null;
  }

  return {
    email: nextValue.email.toLowerCase(),
    name: nextValue.name,
    userType: nextValue.userType,
  };
}

function parseCollaborationInviteRoute(): CollaborationInviteRoute | null {
  const params = new URLSearchParams(window.location.search);
  const externalReviewToken = params.get("external_review");
  const inviteToken = externalReviewToken ?? params.get("collab_invite");
  const messageId = params.get("message_id");
  const inviteeEmail = params.get("invitee");
  const inviteStatus = params.get("invite_status") ?? undefined;

  if (!inviteToken) {
    return null;
  }

  const status =
    inviteStatus ??
    (!messageId || !inviteeEmail
      ? "invalid"
      : undefined);

  return {
    mode: externalReviewToken ? "external_review" : "invite",
    inviteToken,
    messageId: messageId ?? undefined,
    inviteeEmail: inviteeEmail?.toLowerCase(),
    status,
  };
}

function parseTeamInviteRoute(): TeamInviteRoute | null {
  const params = new URLSearchParams(window.location.search);
  const inviteToken = params.get("team_invite");

  if (!inviteToken) {
    return null;
  }

  return {
    inviteToken,
  };
}

function createLocalOnboardingStorageKeys(
  identityScope: string,
): LocalOnboardingStorageKeys {
  const suffix = encodeURIComponent(identityScope);

  return {
    state: `${ONBOARDING_STATE_STORAGE_KEY}:guest-v1:${suffix}`,
    draft: `${ONBOARDING_DRAFT_STATE_STORAGE_KEY}:guest-v1:${suffix}`,
    view: `${CUEVION_APP_VIEW_STORAGE_KEY}:guest-v1:${suffix}`,
  };
}

function resolveLocalOnboardingIdentityScope({
  userType,
  userEmail,
  ephemeralIdentity,
  collaborationInvite,
  teamInviteToken,
}: LocalOnboardingIdentityInput): LocalOnboardingIdentityScope | null {
  const normalizedUserEmail = userEmail?.trim().toLowerCase() ?? "";
  const normalizedEphemeralIdentity = ephemeralIdentity?.trim() ?? "";

  if (collaborationInvite) {
    const inviteToken = collaborationInvite.inviteToken.trim();
    const messageId = collaborationInvite.messageId?.trim() ?? "";
    const inviteeEmail =
      collaborationInvite.inviteeEmail?.trim().toLowerCase() ?? "";
    const safeIdentityScope = messageId
      ? `collaboration-message:${messageId}:invitee:${inviteeEmail}`
      : inviteeEmail
        ? `collaboration-invitee:${inviteeEmail}`
        : userType === "guest" && normalizedUserEmail
          ? `collaboration-guest:${normalizedUserEmail}`
          : null;

    return {
      // The token is deliberately confined to this in-memory identity key.
      hydrationKey: `collaboration:${inviteToken}:${safeIdentityScope ?? "ephemeral"}`,
      storageKeys: safeIdentityScope
        ? createLocalOnboardingStorageKeys(safeIdentityScope)
        : null,
    };
  }

  if (teamInviteToken) {
    return {
      // Team invites expose no non-secret identity suitable for localStorage.
      hydrationKey: `team-invite:${teamInviteToken}`,
      storageKeys: null,
    };
  }

  if (userType === "guest") {
    if (!normalizedUserEmail) {
      return normalizedEphemeralIdentity
        ? {
            hydrationKey: `guest-ephemeral:${normalizedEphemeralIdentity}`,
            storageKeys: null,
          }
        : null;
    }
    const safeIdentityScope = `guest-email:${normalizedUserEmail}`;

    return {
      hydrationKey: `guest:${safeIdentityScope}`,
      storageKeys: createLocalOnboardingStorageKeys(safeIdentityScope),
    };
  }

  return null;
}

function getCurrentInviteUrl() {
  return `${window.location.pathname}${window.location.search}${window.location.hash}`;
}

function shouldResetOnboardingFromQuery() {
  const params = new URLSearchParams(window.location.search);
  return params.get("reset_onboarding") === "1";
}

function clearOnboardingResetQueryParam() {
  const url = new URL(window.location.href);
  url.searchParams.delete("reset_onboarding");
  window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
}

function resolveWorkspaceDataMode(): WorkspaceDataMode {
  if (typeof window === "undefined") {
    return "live";
  }

  const params = new URLSearchParams(window.location.search);
  const workspaceMode = params.get("workspace_mode");
  const demoMode = params.get("demo_mode");

  if (workspaceMode === "demo" || demoMode === "1") {
    return "demo";
  }

  if (workspaceMode === "live" || demoMode === "0") {
    return "live";
  }

  return window.location.hostname === "localhost" ||
    window.location.hostname === "127.0.0.1"
    ? "demo"
    : "live";
}

const ONBOARDING_CHOICE_KEYS = [
  "primaryRole",
  "internalRole",
  "secondaryRole",
  "primaryInbox",
  "primaryInboxType",
  "focusPreferences",
  "inboxCount",
  "selectedInboxes",
  "customInboxes",
] as const satisfies readonly (keyof OnboardingChoices)[];
const ONBOARDING_SESSION_V1_KEYS = [
  "schemaVersion",
  "completed",
  "currentStep",
  "choices",
] as const;
const ONBOARDING_FOCUS_KEYS = [
  "demos",
  "promo",
  "finance",
  "legal",
  "business",
  "updates",
  "distribution",
  "royalties",
  "promoReminders",
  "paymentReminders",
] as const satisfies readonly (keyof OnboardingChoices["focusPreferences"])[];
const ONBOARDING_ROLE_IDS = new Set([
  "label_ar_manager",
  "label_manager",
  "ar_manager",
  "dj",
  "producer",
  "dj_producer",
  "label_owner",
  "legal",
  "finance",
  "royalty",
  "sync_licensing",
  "social_media_manager",
  "promo_manager",
  "distribution",
  "admin",
]);
const ONBOARDING_INTERNAL_ROLES = new Set([
  "management",
  "label_ar_manager",
  "label_manager",
  "ar_manager",
  "product_manager",
  "artist_manager",
  "dj",
  "producer",
]);
const ONBOARDING_PRESET_INBOX_IDS = new Set([
  "main",
  "demo",
  "business",
  "promo",
  "legal",
  "finance",
  "royalty",
  "sync",
]);
const ONBOARDING_FOCUS_LEVELS = new Set(["medium", "low"]);
const LEGACY_ONBOARDING_FOCUS_LEVELS = new Set(["high", "medium", "low"]);
const ONBOARDING_INBOX_COUNTS = new Set(["1", "2", "3", "4+", "not_sure"]);

type ParsedAccountOnboardingSession =
  | { status: "not_started"; session: null }
  | { status: "valid"; session: PersistedOnboardingSession }
  | { status: "invalid"; session: null };

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, allowedKeys: readonly string[]) {
  const allowed = new Set(allowedKeys);
  return Object.keys(value).every((key) => allowed.has(key));
}

function hasOwn(value: Record<string, unknown>, key: string) {
  return Object.prototype.hasOwnProperty.call(value, key);
}

function isInboxId(value: unknown): value is OnboardingChoices["primaryInbox"] & string {
  return (
    typeof value === "string" &&
    (ONBOARDING_PRESET_INBOX_IDS.has(value) ||
      (value.startsWith("custom:") &&
        /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(value.slice("custom:".length))))
  );
}

function isCustomInboxId(value: unknown): value is `custom:${string}` {
  return (
    typeof value === "string" &&
    value.startsWith("custom:") &&
    /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(value.slice("custom:".length))
  );
}

function canonicalizeFocusLevel(value: "high" | "medium" | "low") {
  return value === "low" ? "low" : "medium";
}

function isValidOnboardingStep(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isInteger(value) &&
    value >= ONBOARDING_STEP_MIN &&
    value <= ONBOARDING_STEP_MAX
  );
}

function projectOnboardingChoices(state: OnboardingState): OnboardingChoices {
  const focusPreferences = normalizeFocusPreferences(state.focusPreferences);
  const customInboxes = Array.from(
    new Map(
      state.customInboxes
        .filter(
          (inbox) =>
            isCustomInboxId(inbox.id) && typeof inbox.name === "string",
        )
        .map((inbox) => [inbox.id, { id: inbox.id, name: inbox.name }]),
    ).values(),
  );
  const selectedInboxes = Array.from(
    new Set(state.selectedInboxes.filter(isInboxId)),
  );

  return {
    primaryRole:
      state.primaryRole === null || ONBOARDING_ROLE_IDS.has(state.primaryRole)
        ? state.primaryRole
        : initialOnboardingState.primaryRole,
    internalRole:
      state.internalRole === null || ONBOARDING_INTERNAL_ROLES.has(state.internalRole)
        ? state.internalRole
        : initialOnboardingState.internalRole,
    secondaryRole:
      state.secondaryRole === null || ONBOARDING_ROLE_IDS.has(state.secondaryRole)
        ? state.secondaryRole
        : initialOnboardingState.secondaryRole,
    primaryInbox:
      state.primaryInbox === null || isInboxId(state.primaryInbox)
        ? state.primaryInbox
        : initialOnboardingState.primaryInbox,
    primaryInboxType:
      state.primaryInboxType === null ||
      state.primaryInboxType === "personal" ||
      state.primaryInboxType === "work"
        ? state.primaryInboxType
        : initialOnboardingState.primaryInboxType,
    focusPreferences: {
      demos: canonicalizeFocusLevel(focusPreferences.demos),
      promo: canonicalizeFocusLevel(focusPreferences.promo),
      finance: canonicalizeFocusLevel(focusPreferences.finance),
      legal: canonicalizeFocusLevel(focusPreferences.legal),
      business: canonicalizeFocusLevel(focusPreferences.business),
      updates: canonicalizeFocusLevel(focusPreferences.updates),
      distribution: canonicalizeFocusLevel(focusPreferences.distribution),
      royalties: canonicalizeFocusLevel(focusPreferences.royalties),
      promoReminders: canonicalizeFocusLevel(focusPreferences.promoReminders),
      paymentReminders: canonicalizeFocusLevel(focusPreferences.paymentReminders),
    },
    inboxCount:
      state.inboxCount === null || ONBOARDING_INBOX_COUNTS.has(state.inboxCount)
        ? state.inboxCount
        : initialOnboardingState.inboxCount,
    selectedInboxes,
    customInboxes,
  };
}

function parseOnboardingChoices(
  value: unknown,
  { allowLegacyHigh = false }: { allowLegacyHigh?: boolean } = {},
): OnboardingChoices | null {
  if (!isPlainRecord(value) || !hasOnlyKeys(value, ONBOARDING_CHOICE_KEYS)) {
    return null;
  }

  if (
    hasOwn(value, "primaryRole") &&
    value.primaryRole !== null &&
    (typeof value.primaryRole !== "string" || !ONBOARDING_ROLE_IDS.has(value.primaryRole))
  ) {
    return null;
  }
  if (
    hasOwn(value, "internalRole") &&
    value.internalRole !== null &&
    (typeof value.internalRole !== "string" ||
      !ONBOARDING_INTERNAL_ROLES.has(value.internalRole))
  ) {
    return null;
  }
  if (
    hasOwn(value, "secondaryRole") &&
    value.secondaryRole !== null &&
    (typeof value.secondaryRole !== "string" || !ONBOARDING_ROLE_IDS.has(value.secondaryRole))
  ) {
    return null;
  }
  if (
    hasOwn(value, "primaryInbox") &&
    value.primaryInbox !== null &&
    !isInboxId(value.primaryInbox)
  ) {
    return null;
  }
  if (
    hasOwn(value, "primaryInboxType") &&
    value.primaryInboxType !== null &&
    value.primaryInboxType !== "personal" &&
    value.primaryInboxType !== "work"
  ) {
    return null;
  }
  if (
    hasOwn(value, "inboxCount") &&
    value.inboxCount !== null &&
    (typeof value.inboxCount !== "string" || !ONBOARDING_INBOX_COUNTS.has(value.inboxCount))
  ) {
    return null;
  }
  if (
    hasOwn(value, "selectedInboxes") &&
    (!Array.isArray(value.selectedInboxes) ||
      !value.selectedInboxes.every(isInboxId) ||
      new Set(value.selectedInboxes).size !== value.selectedInboxes.length)
  ) {
    return null;
  }
  if (
    hasOwn(value, "customInboxes") &&
    (!Array.isArray(value.customInboxes) ||
      !value.customInboxes.every(
        (entry) =>
          isPlainRecord(entry) &&
          hasOnlyKeys(entry, ["id", "name"]) &&
          isCustomInboxId(entry.id) &&
          typeof entry.name === "string",
      ) ||
      new Set(
        value.customInboxes.flatMap((entry) =>
          isPlainRecord(entry) && typeof entry.id === "string" ? [entry.id] : [],
        ),
      ).size !== value.customInboxes.length)
  ) {
    return null;
  }
  if (hasOwn(value, "focusPreferences")) {
    if (
      !isPlainRecord(value.focusPreferences) ||
      !hasOnlyKeys(value.focusPreferences, ONBOARDING_FOCUS_KEYS) ||
      !Object.values(value.focusPreferences).every(
        (entry) =>
          typeof entry === "string" &&
          (allowLegacyHigh
            ? LEGACY_ONBOARDING_FOCUS_LEVELS.has(entry)
            : ONBOARDING_FOCUS_LEVELS.has(entry)),
      )
    ) {
      return null;
    }
  }

  const defaults = projectOnboardingChoices(
    normalizeOnboardingState(initialOnboardingState),
  );
  const rawFocusPreferences = isPlainRecord(value.focusPreferences)
    ? value.focusPreferences
    : {};
  const focusPreferences = { ...defaults.focusPreferences };
  for (const key of ONBOARDING_FOCUS_KEYS) {
    if (hasOwn(rawFocusPreferences, key)) {
      focusPreferences[key] = canonicalizeFocusLevel(
        rawFocusPreferences[key] as "high" | "medium" | "low",
      );
    }
  }

  return {
    primaryRole: hasOwn(value, "primaryRole")
      ? (value.primaryRole as OnboardingChoices["primaryRole"])
      : defaults.primaryRole,
    internalRole: hasOwn(value, "internalRole")
      ? (value.internalRole as OnboardingChoices["internalRole"])
      : defaults.internalRole,
    secondaryRole: hasOwn(value, "secondaryRole")
      ? (value.secondaryRole as OnboardingChoices["secondaryRole"])
      : defaults.secondaryRole,
    primaryInbox: hasOwn(value, "primaryInbox")
      ? (value.primaryInbox as OnboardingChoices["primaryInbox"])
      : defaults.primaryInbox,
    primaryInboxType: hasOwn(value, "primaryInboxType")
      ? (value.primaryInboxType as OnboardingChoices["primaryInboxType"])
      : defaults.primaryInboxType,
    focusPreferences,
    inboxCount: hasOwn(value, "inboxCount")
      ? (value.inboxCount as OnboardingChoices["inboxCount"])
      : defaults.inboxCount,
    selectedInboxes: hasOwn(value, "selectedInboxes")
      ? [...(value.selectedInboxes as OnboardingChoices["selectedInboxes"])]
      : defaults.selectedInboxes,
    customInboxes: hasOwn(value, "customInboxes")
      ? (value.customInboxes as OnboardingChoices["customInboxes"]).map((inbox) => ({
          id: inbox.id,
          name: inbox.name,
        }))
      : defaults.customInboxes,
  };
}

function parseLegacyOnboardingChoices(value: unknown): OnboardingChoices | null {
  if (!isPlainRecord(value)) {
    return null;
  }

  const safeProjection: Record<string, unknown> = {};
  for (const key of ONBOARDING_CHOICE_KEYS) {
    if (hasOwn(value, key)) {
      safeProjection[key] = value[key];
    }
  }
  return parseOnboardingChoices(safeProjection, { allowLegacyHigh: true });
}

function parseAccountOnboardingSession(value: unknown): ParsedAccountOnboardingSession {
  if (!isPlainRecord(value)) {
    return { status: "invalid", session: null };
  }
  if (Object.keys(value).length === 0) {
    return { status: "not_started", session: null };
  }

  if (hasOwn(value, "schemaVersion")) {
    if (
      !hasOnlyKeys(value, ONBOARDING_SESSION_V1_KEYS) ||
      value.schemaVersion !== 1 ||
      typeof value.completed !== "boolean" ||
      !isValidOnboardingStep(value.currentStep)
    ) {
      return { status: "invalid", session: null };
    }
    const choices = parseOnboardingChoices(value.choices);
    if (!choices) {
      return { status: "invalid", session: null };
    }
    return {
      status: "valid",
      session: value.completed
        ? { schemaVersion: 1, completed: true, currentStep: value.currentStep, choices }
        : { schemaVersion: 1, completed: false, currentStep: value.currentStep, choices },
    };
  }

  if (
    value.completed === true &&
    hasOnlyKeys(value, ["completed", "state"]) &&
    hasOwn(value, "state")
  ) {
    const choices = parseLegacyOnboardingChoices(value.state);
    if (!choices) {
      return { status: "invalid", session: null };
    }
    return {
      status: "valid",
      session: {
        schemaVersion: 1,
        completed: true,
        currentStep: ONBOARDING_STEP_MAX,
        choices,
      },
    };
  }

  return { status: "invalid", session: null };
}

function buildOnboardingStateFromChoices(choices: OnboardingChoices): OnboardingState {
  const cleanDefaults = normalizeOnboardingState(initialOnboardingState);
  const normalized = normalizeOnboardingState({
    ...cleanDefaults,
    primaryRole: choices.primaryRole,
    internalRole: choices.internalRole,
    secondaryRole: choices.secondaryRole,
    primaryInbox: choices.primaryInbox,
    primaryInboxType: choices.primaryInboxType,
    focusPreferences: choices.focusPreferences,
    inboxCount: choices.inboxCount,
    selectedInboxes: choices.selectedInboxes,
    customInboxes: choices.customInboxes,
    inboxConnections: cleanDefaults.inboxConnections,
  });
  const inboxConnections = { ...cleanDefaults.inboxConnections };
  for (const inboxId of normalized.selectedInboxes) {
    if (!inboxConnections[inboxId]) {
      inboxConnections[inboxId] = createInboxConnection();
    }
  }

  return {
    ...normalized,
    inboxConnections,
  };
}

function createIncompleteOnboardingSession(
  state: OnboardingState,
  currentStep: number,
): OnboardingSessionV1 {
  return {
    schemaVersion: 1,
    completed: false,
    currentStep: clampOnboardingStep(currentStep),
    choices: projectOnboardingChoices(state),
  };
}

function parsePersistedOnboardingSession(
  storage: AccountConfigStorage,
  storageKeys: LocalOnboardingStorageKeys,
  persistSanitizedValue = true,
): PersistedOnboardingSession | null {
  const storedState = storage.getItem(storageKeys.state);

  if (!storedState) {
    return null;
  }

  try {
    const parsed = parseAccountOnboardingSession(JSON.parse(storedState));
    if (parsed.status !== "valid" || !parsed.session.completed) {
      if (persistSanitizedValue) {
        storage.removeItem(storageKeys.state);
      }
      return null;
    }
    const serialized = JSON.stringify(parsed.session);
    if (persistSanitizedValue && serialized !== storedState) {
      storage.setItem(storageKeys.state, serialized);
    }
    return parsed.session;
  } catch {
    if (persistSanitizedValue) {
      storage.removeItem(storageKeys.state);
    }
    return null;
  }
}

function parsePersistedOnboardingDraft(
  storage: AccountConfigStorage,
  storageKeys: LocalOnboardingStorageKeys,
  persistSanitizedValue = true,
): PersistedOnboardingSession | null {
  const storedState = storage.getItem(storageKeys.draft);

  if (!storedState) {
    return null;
  }

  try {
    const rawValue = JSON.parse(storedState) as unknown;
    let parsed = parseAccountOnboardingSession(rawValue);
    if (
      parsed.status === "invalid" &&
      isPlainRecord(rawValue) &&
      hasOnlyKeys(rawValue, ["state"]) &&
      hasOwn(rawValue, "state")
    ) {
      const choices = parseLegacyOnboardingChoices(rawValue.state);
      if (choices) {
        parsed = {
          status: "valid",
          session: {
            schemaVersion: 1,
            completed: false,
            currentStep: ONBOARDING_STEP_MIN,
            choices,
          },
        };
      }
    }

    if (parsed.status !== "valid" || parsed.session.completed) {
      if (persistSanitizedValue) {
        storage.removeItem(storageKeys.draft);
      }
      return null;
    }
    const serialized = JSON.stringify(parsed.session);
    if (persistSanitizedValue && serialized !== storedState) {
      storage.setItem(storageKeys.draft, serialized);
    }
    return parsed.session;
  } catch {
    if (persistSanitizedValue) {
      storage.removeItem(storageKeys.draft);
    }
    return null;
  }
}

function hydrateLocalOnboardingIdentityScope(
  identityScope: LocalOnboardingIdentityScope,
  storage: AccountConfigStorage,
): LocalOnboardingHydrationState {
  const localSession = identityScope.storageKeys
    ? parsePersistedOnboardingSession(storage, identityScope.storageKeys) ??
      parsePersistedOnboardingDraft(storage, identityScope.storageKeys)
    : null;
  const onboardingState = localSession
    ? buildOnboardingStateFromChoices(localSession.choices)
    : normalizeOnboardingState(initialOnboardingState);
  const isCompleted = localSession?.completed === true;

  return {
    persistedOnboardingSession: localSession,
    onboardingState,
    onboardingStep: localSession?.currentStep ?? ONBOARDING_STEP_MIN,
    userConfig: isCompleted ? buildUserConfig(onboardingState) : null,
    view: isCompleted ? "workspace" : "onboarding",
  };
}

type StoredManagedWorkspaceInboxReadResult =
  | { status: "missing" | "invalid"; value: [] }
  | { status: "valid"; value: StoredManagedWorkspaceInbox[] };

function readStoredManagedWorkspaceInboxes(
  persistSanitizedValue = true,
  storage: AccountConfigStorage = window.localStorage,
): StoredManagedWorkspaceInboxReadResult {
  const storedValue = storage.getItem(MANAGED_INBOXES_STORAGE_KEY);

  if (!storedValue) {
    return { status: "missing", value: [] };
  }

  try {
    const migrated = sanitizeStoredMailboxCredentialJson(storedValue);
    if (!Array.isArray(migrated.value)) {
      return { status: "invalid", value: [] };
    }
    const sanitized = sanitizeManagedInboxesForBrowserStorage(
      migrated.value as StoredManagedWorkspaceInbox[],
    );
    const serialized = JSON.stringify(sanitized);
    if (persistSanitizedValue && serialized !== storedValue) {
      storage.setItem(MANAGED_INBOXES_STORAGE_KEY, serialized);
    }
    return { status: "valid", value: sanitized };
  } catch {
    return { status: "invalid", value: [] };
  }
}

function parseStoredManagedWorkspaceInboxes(
  persistSanitizedValue = true,
  storage: AccountConfigStorage = window.localStorage,
): StoredManagedWorkspaceInbox[] {
  return readStoredManagedWorkspaceInboxes(persistSanitizedValue, storage).value;
}

function scrubManagedInboxBrowserStorage(
  storage: AccountConfigStorage = window.localStorage,
) {
  try {
    const result = readStoredManagedWorkspaceInboxes(true, storage);
    if (result.status === "invalid") {
      storage.removeItem(MANAGED_INBOXES_STORAGE_KEY);
    }
  } catch {
    // Storage availability must not block startup.
  }
}

function parseDisplayNameOverrides(): DisplayNameOverrideStore {
  const storedValue = window.localStorage.getItem(CUEVION_DISPLAY_NAME_OVERRIDES_STORAGE_KEY);

  if (!storedValue) {
    return {};
  }

  try {
    const parsed = JSON.parse(storedValue) as DisplayNameOverrideStore;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function normalizeAccountStorageKey(value: string) {
  const normalizedValue = value.trim().toLowerCase();
  const emailMatch = normalizedValue.match(/([a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,})/i);

  return emailMatch?.[1] ?? normalizedValue;
}

function buildMailboxFocusPreferenceOverridesStorageKey(workspaceUserId: string) {
  return `${MAILBOX_FOCUS_PREFERENCE_OVERRIDES_STORAGE_KEY}:${workspaceUserId}`;
}

function buildPrimaryManagedInboxStorageKey(
  workspaceUserId: string,
  managedInboxes: StoredManagedWorkspaceInbox[],
) {
  const managedInboxSetKey =
    managedInboxes
      .map((mailbox) => `${mailbox.id ?? ""}:${mailbox.email ?? ""}`)
      .sort()
      .join("|") || "default";

  return `${PRIMARY_MANAGED_INBOX_ID_STORAGE_KEY}:${workspaceUserId}:${managedInboxSetKey}`;
}

function parseStoredJsonValue<T>(
  storageKey: string,
  fallback: T,
  storage: AccountConfigStorage = window.localStorage,
): T {
  const storedValue = storage.getItem(storageKey);

  if (!storedValue) {
    return fallback;
  }

  try {
    return JSON.parse(storedValue) as T;
  } catch {
    return fallback;
  }
}

function sanitizeOnboardingSessionForAccountConfig(
  session: PersistedOnboardingSession | null,
): PersistedOnboardingSession | null {
  if (!session) {
    return null;
  }

  return {
    ...session,
    schemaVersion: 1,
    currentStep: clampOnboardingStep(session.currentStep),
    choices: projectOnboardingChoices(
      buildOnboardingStateFromChoices(session.choices),
    ),
  };
}

function sanitizeManagedInboxesForAccountConfig(
  managedInboxes: StoredManagedWorkspaceInbox[],
): StoredManagedWorkspaceInbox[] {
  return sanitizeManagedInboxesForBrowserStorage(managedInboxes);
}

function normalizeStoredWorkspaceThemeMode(value: unknown): "Light" | "Dark" | "System" | null {
  if (value === "Light" || value === "Dark" || value === "System") {
    return value;
  }

  if (value === "light") {
    return "Light";
  }

  if (value === "dark") {
    return "Dark";
  }

  return null;
}

function buildAccountConfigFromLocalStorage(
  accountStorageOwnerKey: string,
  onboardingSession: PersistedOnboardingSession | null = null,
  displayNameOverrides: DisplayNameOverrideStore = parseDisplayNameOverrides(),
  storage: AccountConfigStorage = window.localStorage,
  preserveExplicitEmptyManagedInboxes = false,
): UserAccountConfig {
  const storedManagedInboxes = readStoredManagedWorkspaceInboxes(
    true,
    storage,
  );
  const shouldUseStoredManagedInboxes =
    storedManagedInboxes.status === "valid" &&
    (preserveExplicitEmptyManagedInboxes || storedManagedInboxes.value.length > 0);
  const managedInboxes = sanitizeManagedInboxesForAccountConfig(
    shouldUseStoredManagedInboxes
      ? storedManagedInboxes.value
      : [],
  );
  const workspaceUserId = normalizeAccountStorageKey(accountStorageOwnerKey);
  const storedPrimaryManagedInboxId = storage.getItem(
    buildPrimaryManagedInboxStorageKey(workspaceUserId, managedInboxes),
  );
  const normalizedStoredPrimaryManagedInboxId =
    storedPrimaryManagedInboxId?.trim() ?? "";
  const normalizedManagedInboxIds = managedInboxes.flatMap((mailbox) => {
    const inboxId = typeof mailbox.id === "string" ? mailbox.id.trim() : "";
    return inboxId ? [inboxId] : [];
  });
  const echoPrimaryManagedInboxId = normalizedManagedInboxIds.includes(
    normalizedStoredPrimaryManagedInboxId,
  )
    ? normalizedStoredPrimaryManagedInboxId
    : normalizedManagedInboxIds[0] ?? null;
  const themeMode =
    normalizeStoredWorkspaceThemeMode(
      storage.getItem(WORKSPACE_THEME_MODE_STORAGE_KEY),
    ) ?? "Light";

  return {
    v: 1,
    ...(onboardingSession?.completed
      ? {}
      : {
          onboardingSession:
            sanitizeOnboardingSessionForAccountConfig(onboardingSession) ?? {},
        }),
    managedInboxes,
    mailboxTitleOverrides: parseStoredJsonValue(
      MAILBOX_TITLE_OVERRIDES_STORAGE_KEY,
      {},
      storage,
    ),
    primaryManagedInboxId: preserveExplicitEmptyManagedInboxes
      ? storedPrimaryManagedInboxId
      : echoPrimaryManagedInboxId,
    mailboxFocusPreferenceOverrides: parseStoredJsonValue(
      buildMailboxFocusPreferenceOverridesStorageKey(workspaceUserId),
      {},
      storage,
    ),
    inboxSignatures: parseStoredJsonValue(MAIL_SIGNATURES_STORAGE_KEY, {}, storage),
    smartFolders: parseStoredJsonValue(SMART_FOLDERS_STORAGE_KEY, [], storage),
    uiPreferences: {
      themeMode,
      aiSuggestionsEnabled:
        storage.getItem(AI_SUGGESTIONS_STORAGE_KEY) !== "false",
      inboxChangesEnabled:
        storage.getItem(INBOX_CHANGES_STORAGE_KEY) !== "false",
      teamActivityEnabled:
        storage.getItem(TEAM_ACTIVITY_STORAGE_KEY) !== "false",
    },
    displayNameOverrides,
  };
}

function buildAuthoritativeAccountConfigFromLocalStorage(
  accountStorageOwnerKey: string,
  onboardingSession: PersistedOnboardingSession | null,
  displayNameOverrides: DisplayNameOverrideStore,
  storage: AccountConfigStorage = window.localStorage,
) {
  return buildAccountConfigFromLocalStorage(
    accountStorageOwnerKey,
    onboardingSession,
    displayNameOverrides,
    storage,
    true,
  );
}

function getServerManagedInboxesForHydration(
  config: UserAccountConfig,
): StoredManagedWorkspaceInbox[] {
  return Array.isArray(config.managedInboxes)
    ? (config.managedInboxes as StoredManagedWorkspaceInbox[])
    : [];
}

function isValidServerManagedInboxId(value: string) {
  return (
    !value.toLowerCase().startsWith("draft-") &&
    /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value)
  );
}

const unsafeMailboxMetadataFields = new Set([
  "password",
  "imappassword",
  "smtppassword",
  "encryptedpassword",
  "ciphertext",
  "nonce",
  "salt",
  "keymaterial",
  "credentialid",
  "credentialreference",
  "credentialrecord",
  "rawcredentialrecord",
  "secret",
  "encryptedsecret",
  "secretkey",
  "secretstorekey",
  "encryptionkey",
  "token",
  "authorization",
  "authorizationheader",
  "authtoken",
  "accesstoken",
  "refreshtoken",
  "idtoken",
  "session",
  "sessionid",
  "cookie",
  "credentialversion",
  "secretversion",
  "credentialgeneration",
  "secretgeneration",
  "credentialrevision",
  "secretrevision",
  "archive",
  "archived",
  "attachment",
  "attachments",
  "body",
  "bodyhtml",
  "content",
  "contentbase64",
  "draft",
  "drafts",
  "file",
  "filebytes",
  "filecontent",
  "filedata",
  "files",
  "inbox",
  "inboxes",
  "invite",
  "invites",
  "liveinboxsnapshots",
  "mailboxstore",
  "messages",
  "oauthcallback",
  "oauthcallbackstate",
  "readstate",
  "sent",
  "snapshot",
  "snapshots",
  "spam",
  "trash",
  "unread",
]);
const safeManagedMailboxFields = new Set([
  "id",
  "onboardingInboxId",
  "title",
  "email",
  "provider",
  "connected",
  "connectionMethod",
  "connectionStatus",
  "connectionMessage",
  "oauthAuthorizationUrl",
  "customImap",
  "customSmtp",
  "imapConnectionStatus",
  "smtpConnectionStatus",
  "imapPasswordSet",
  "smtpPasswordSet",
  "fullyConnected",
  "internalRole",
  "focusPreferences",
  "customImapFolderMappings",
]);
const safeManagedCustomImapFields = new Set([
  "host",
  "port",
  "ssl",
  "username",
  "password",
]);
const safeManagedCustomSmtpFields = new Set([
  "host",
  "port",
  "security",
  "username",
  "password",
  "useSameCredentials",
]);

function containsUnsafeMailboxMetadata(
  value: unknown,
  parentField = "",
): boolean {
  if (Array.isArray(value)) {
    return value.some((item) => containsUnsafeMailboxMetadata(item));
  }
  if (!value || typeof value !== "object") {
    return false;
  }

  return Object.entries(value as Record<string, unknown>).some(([key, item]) => {
    return (
      isUnsafeMailboxMetadataEntry(key, item, parentField) ||
      containsUnsafeMailboxMetadata(item, key)
    );
  });
}

function isUnsafeMailboxMetadataEntry(
  key: string,
  item: unknown,
  parentField: string,
) {
  const compactKey = key.toLowerCase().replace(/[^a-z0-9]/g, "");
  const isAllowedEmptyPassword =
    item === "" &&
    compactKey.includes("password") &&
    (parentField === "customImap" || parentField === "customSmtp");
  const isAllowedSameCredentialsFlag =
    key === "useSameCredentials" &&
    parentField === "customSmtp" &&
    typeof item === "boolean";
  const isAllowedPasswordCapability =
    (key === "imapPasswordSet" || key === "smtpPasswordSet") &&
    typeof item === "boolean";
  const isAllowedNullOauthAuthorizationUrl =
    key === "oauthAuthorizationUrl" &&
    (item === null || item === undefined);
  const isCredentialLifecycleMetadata =
    (compactKey.includes("credential") ||
      compactKey.includes("secret")) &&
    (compactKey.includes("version") ||
      compactKey.includes("generation") ||
      compactKey.includes("revision"));
  const containsSensitiveKeyMarker =
    compactKey.includes("password") ||
    compactKey.includes("secret") ||
    compactKey.includes("token") ||
    compactKey.includes("authorization") ||
    compactKey.includes("authheader") ||
    compactKey.includes("credential") ||
    compactKey.includes("ciphertext") ||
    compactKey.includes("keymaterial") ||
    compactKey.includes("session") ||
    compactKey.includes("cookie");
  return (
    !isAllowedEmptyPassword &&
    !isAllowedSameCredentialsFlag &&
    !isAllowedPasswordCapability &&
    !isAllowedNullOauthAuthorizationUrl &&
    (unsafeMailboxMetadataFields.has(compactKey) ||
      containsSensitiveKeyMarker ||
      isCredentialLifecycleMetadata)
  );
}

function projectCustomImapForBrowserStorage(
  value: unknown,
): Record<string, unknown> | undefined {
  if (!isPlainRecord(value)) {
    return undefined;
  }
  const projected: Record<string, unknown> = {};
  for (const field of ["host", "port", "username"] as const) {
    if (typeof value[field] === "string") {
      projected[field] = value[field];
    }
  }
  if (typeof value.ssl === "boolean") {
    projected.ssl = value.ssl;
  }
  if (hasOwn(value, "password")) {
    projected.password = "";
  }
  return projected;
}

function projectCustomSmtpForBrowserStorage(
  value: unknown,
): Record<string, unknown> | undefined {
  if (!isPlainRecord(value)) {
    return undefined;
  }
  const projected: Record<string, unknown> = {};
  for (const field of ["host", "port", "username"] as const) {
    if (typeof value[field] === "string") {
      projected[field] = value[field];
    }
  }
  if (value.security === "ssl" || value.security === "starttls") {
    projected.security = value.security;
  }
  if (typeof value.useSameCredentials === "boolean") {
    projected.useSameCredentials = value.useSameCredentials;
  }
  if (hasOwn(value, "password")) {
    projected.password = "";
  }
  return projected;
}

function projectCustomImapFolderMappingsForBrowserStorage(
  value: unknown,
): StoredManagedWorkspaceInbox["customImapFolderMappings"] | undefined {
  if (
    !isPlainRecord(value) ||
    !hasOnlyKeys(value, ["schemaVersion", "trashFolder"]) ||
    value.schemaVersion !== 1 ||
    typeof value.trashFolder !== "string" ||
    !value.trashFolder ||
    value.trashFolder !== value.trashFolder.trim() ||
    value.trashFolder.toLowerCase() === "inbox" ||
    /[\u0000-\u001f\u007f-\u009f]/u.test(value.trashFolder)
  ) {
    return undefined;
  }

  return {
    schemaVersion: 1,
    trashFolder: value.trashFolder,
  };
}

function projectFocusPreferencesForBrowserStorage(
  value: unknown,
): Record<string, string> | undefined {
  if (!isPlainRecord(value)) {
    return undefined;
  }
  return Object.fromEntries(
    ONBOARDING_FOCUS_KEYS.flatMap((field) => {
      const level = value[field];
      return typeof level === "string" &&
        LEGACY_ONBOARDING_FOCUS_LEVELS.has(level)
        ? [[field, level]]
        : [];
    }),
  );
}

function projectManagedInboxForBrowserStorage(
  mailbox: unknown,
): StoredManagedWorkspaceInbox | null {
  if (!isPlainRecord(mailbox)) {
    return null;
  }
  const id = typeof mailbox.id === "string" ? mailbox.id : "";
  if (!id.trim()) {
    return null;
  }

  const projected: Record<string, unknown> = { id };
  for (const field of ["title", "email"] as const) {
    if (typeof mailbox[field] === "string") {
      projected[field] = mailbox[field];
    }
  }
  if (
    typeof mailbox.onboardingInboxId === "string" &&
    isInboxId(mailbox.onboardingInboxId)
  ) {
    projected.onboardingInboxId = mailbox.onboardingInboxId;
  }
  if (
    mailbox.provider === null ||
    mailbox.provider === "google" ||
    mailbox.provider === "microsoft" ||
    mailbox.provider === "icloud" ||
    mailbox.provider === "yahoo" ||
    mailbox.provider === "custom_imap"
  ) {
    projected.provider = mailbox.provider;
  }
  if (typeof mailbox.connected === "boolean") {
    projected.connected = mailbox.connected;
  }
  if (
    mailbox.connectionMethod === null ||
    mailbox.connectionMethod === "imap" ||
    mailbox.connectionMethod === "oauth"
  ) {
    projected.connectionMethod = mailbox.connectionMethod;
  }
  if (
    mailbox.connectionStatus === "not_connected" ||
    mailbox.connectionStatus === "oauth_required" ||
    mailbox.connectionStatus === "waiting_for_authentication" ||
    mailbox.connectionStatus ===
      "authenticated_pending_activation" ||
    mailbox.connectionStatus === "connected" ||
    mailbox.connectionStatus === "connection_failed"
  ) {
    projected.connectionStatus = mailbox.connectionStatus;
  }
  if (
    mailbox.connectionMessage === null ||
    typeof mailbox.connectionMessage === "string"
  ) {
    projected.connectionMessage = mailbox.connectionMessage;
  }
  if (mailbox.oauthAuthorizationUrl === null) {
    projected.oauthAuthorizationUrl = null;
  }

  const customImap = projectCustomImapForBrowserStorage(
    mailbox.customImap,
  );
  if (customImap) {
    projected.customImap = customImap;
  }
  const customSmtp = projectCustomSmtpForBrowserStorage(
    mailbox.customSmtp,
  );
  if (customSmtp) {
    projected.customSmtp = customSmtp;
  }
  if (
    mailbox.imapConnectionStatus === "not_configured" ||
    mailbox.imapConnectionStatus === "not_connected" ||
    mailbox.imapConnectionStatus === "connected" ||
    mailbox.imapConnectionStatus === "connection_failed"
  ) {
    projected.imapConnectionStatus = mailbox.imapConnectionStatus;
  }
  if (
    mailbox.smtpConnectionStatus === "not_configured" ||
    mailbox.smtpConnectionStatus === "not_connected" ||
    mailbox.smtpConnectionStatus === "connected" ||
    mailbox.smtpConnectionStatus === "connection_failed"
  ) {
    projected.smtpConnectionStatus = mailbox.smtpConnectionStatus;
  }
  if (typeof mailbox.imapPasswordSet === "boolean") {
    projected.imapPasswordSet = mailbox.imapPasswordSet;
  }
  if (typeof mailbox.smtpPasswordSet === "boolean") {
    projected.smtpPasswordSet = mailbox.smtpPasswordSet;
  }
  if (typeof mailbox.fullyConnected === "boolean") {
    projected.fullyConnected = mailbox.fullyConnected;
  }
  const customImapFolderMappings =
    projectCustomImapFolderMappingsForBrowserStorage(
      mailbox.customImapFolderMappings,
    );
  if (customImapFolderMappings) {
    projected.customImapFolderMappings = customImapFolderMappings;
  }
  if (
    mailbox.internalRole === null ||
    (typeof mailbox.internalRole === "string" &&
      ONBOARDING_INTERNAL_ROLES.has(mailbox.internalRole))
  ) {
    projected.internalRole = mailbox.internalRole;
  }
  const focusPreferences =
    projectFocusPreferencesForBrowserStorage(
      mailbox.focusPreferences,
    );
  if (focusPreferences) {
    projected.focusPreferences = focusPreferences;
  }
  return projected as StoredManagedWorkspaceInbox;
}

function sanitizeManagedInboxesForBrowserStorage(
  managedInboxes: StoredManagedWorkspaceInbox[],
): StoredManagedWorkspaceInbox[] {
  return managedInboxes.flatMap((mailbox) => {
    const projected =
      projectManagedInboxForBrowserStorage(mailbox);
    return projected ? [projected] : [];
  });
}

function hasOnlySafeManagedMailboxFields(
  mailbox: StoredManagedWorkspaceInbox,
) {
  return Object.keys(mailbox).every((field) =>
    safeManagedMailboxFields.has(field),
  );
}

function hasSafeManagedMailboxSettings(
  mailbox: StoredManagedWorkspaceInbox,
) {
  const customImap = mailbox.customImap;
  if (
    customImap !== undefined &&
    (!isPlainRecord(customImap) ||
      Object.keys(customImap).some(
        (field) => !safeManagedCustomImapFields.has(field),
      ) ||
      (customImap.host !== undefined &&
        typeof customImap.host !== "string") ||
      (customImap.port !== undefined &&
        typeof customImap.port !== "string") ||
      (customImap.ssl !== undefined &&
        typeof customImap.ssl !== "boolean") ||
      (customImap.username !== undefined &&
        typeof customImap.username !== "string") ||
      (customImap.password !== undefined &&
        customImap.password !== ""))
  ) {
    return false;
  }

  const customSmtp = mailbox.customSmtp;
  return (
    customSmtp === undefined ||
    isSafeEmptyServerCustomSmtp(customSmtp) ||
    (isPlainRecord(customSmtp) &&
      Object.keys(customSmtp).every((field) =>
        safeManagedCustomSmtpFields.has(field),
      ) &&
      (customSmtp.host === undefined ||
        typeof customSmtp.host === "string") &&
      (customSmtp.port === undefined ||
        typeof customSmtp.port === "string") &&
      (customSmtp.security === undefined ||
        customSmtp.security === "ssl" ||
        customSmtp.security === "starttls") &&
      (customSmtp.username === undefined ||
        typeof customSmtp.username === "string") &&
      (customSmtp.password === undefined ||
        customSmtp.password === "") &&
      (customSmtp.useSameCredentials === undefined ||
        typeof customSmtp.useSameCredentials === "boolean"))
  );
}

function isSafeEmptyServerCustomSmtp(value: unknown) {
  if (value === undefined) {
    return true;
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const settings = value as Record<string, unknown>;
  return Object.entries(settings).every(
    ([field, fieldValue]) =>
      fieldValue === "" &&
      field.toLowerCase().replace(/[^a-z0-9]/g, "").includes("password"),
  );
}

function getAuthoritativeCustomImapCapabilityState(
  mailbox: StoredManagedWorkspaceInbox,
): "partial" | "full" | null {
  if (
    mailbox.provider !== "custom_imap" ||
    mailbox.imapConnectionStatus !== "connected" ||
    mailbox.imapPasswordSet !== true ||
    typeof mailbox.smtpPasswordSet !== "boolean" ||
    (mailbox.smtpConnectionStatus !== "not_configured" &&
      mailbox.smtpConnectionStatus !== "connection_failed" &&
      mailbox.smtpConnectionStatus !== "connected") ||
    typeof mailbox.fullyConnected !== "boolean"
  ) {
    return null;
  }

  if (
    mailbox.smtpConnectionStatus === "connected" &&
    mailbox.smtpPasswordSet === true &&
    mailbox.fullyConnected === true
  ) {
    return "full";
  }
  if (
    (mailbox.smtpConnectionStatus === "not_configured" ||
      mailbox.smtpConnectionStatus === "connection_failed") &&
    mailbox.smtpPasswordSet === false &&
    mailbox.fullyConnected === false
  ) {
    return "partial";
  }
  return null;
}

function isSafeServerCustomSmtpSettings(value: unknown) {
  if (isSafeEmptyServerCustomSmtp(value)) {
    return true;
  }
  if (
    !isPlainRecord(value) ||
    Object.keys(value).some(
      (field) => !safeManagedCustomSmtpFields.has(field),
    ) ||
    !["host", "port", "security", "username", "useSameCredentials"].every(
      (field) => hasOwn(value, field),
    )
  ) {
    return false;
  }

  const host = typeof value.host === "string" ? value.host : "";
  const port = typeof value.port === "string" ? value.port : "";
  const username =
    typeof value.username === "string" ? value.username : "";
  const useSameCredentials = value.useSameCredentials;
  const parsedPort = Number(port);
  return (
    Boolean(host) &&
    host === host.trim() &&
    !/\s/.test(host) &&
    /^\d+$/.test(port) &&
    port === port.trim() &&
    Number.isInteger(parsedPort) &&
    parsedPort >= 1 &&
    parsedPort <= 65_535 &&
    (value.security === "ssl" ||
      value.security === "starttls") &&
    ((value.security === "ssl" && port === "465") ||
      (value.security === "starttls" && port === "587")) &&
    typeof useSameCredentials === "boolean" &&
    username === username.trim() &&
    (useSameCredentials || Boolean(username)) &&
    (value.password === undefined || value.password === "")
  );
}

function projectServerCustomSmtpSettings(
  value: unknown,
  incomingOnlyFallback: CustomSmtpSettings,
): CustomSmtpSettings | null {
  if (isSafeEmptyServerCustomSmtp(value)) {
    return {
      ...incomingOnlyFallback,
      password: "",
    };
  }
  if (!isSafeServerCustomSmtpSettings(value) || !isPlainRecord(value)) {
    return null;
  }

  return {
    host: value.host as string,
    port: value.port as string,
    security: value.security as CustomSmtpSettings["security"],
    username: value.username as string,
    password: "",
    useSameCredentials: value.useSameCredentials as boolean,
  };
}

function hasAuthoritativeCustomImapCapabilityShape(
  mailbox: StoredManagedWorkspaceInbox,
) {
  const capabilityState =
    getAuthoritativeCustomImapCapabilityState(mailbox);
  if (capabilityState === "full") {
    return (
      mailbox.customSmtp === undefined ||
      (isSafeServerCustomSmtpSettings(mailbox.customSmtp) &&
        !isSafeEmptyServerCustomSmtp(mailbox.customSmtp))
    );
  }
  if (capabilityState !== "partial") {
    return false;
  }
  if (isSafeEmptyServerCustomSmtp(mailbox.customSmtp)) {
    return true;
  }
  return isSafeServerCustomSmtpSettings(mailbox.customSmtp);
}

function parseServerCustomImapSettings(
  value: unknown,
): CustomImapSettings | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }

  const settings = value as Record<string, unknown>;
  const allowedFields = new Set(["host", "port", "ssl", "username", "password"]);
  if (
    Object.keys(settings).some((field) => !allowedFields.has(field)) ||
    !["host", "port", "ssl", "username"].every((field) => hasOwn(settings, field))
  ) {
    return null;
  }
  const host = typeof settings.host === "string" ? settings.host : "";
  const port = typeof settings.port === "string" ? settings.port : "";
  const username =
    typeof settings.username === "string" ? settings.username : "";
  if (
    !host ||
    host !== host.trim() ||
    /\s/.test(host) ||
    !port ||
    port !== port.trim() ||
    !username ||
    username !== username.trim() ||
    settings.ssl !== true ||
    ("password" in settings && settings.password !== "")
  ) {
    return null;
  }

  const parsedPort = Number(port);
  if (
    !/^\d+$/.test(port) ||
    !Number.isInteger(parsedPort) ||
    parsedPort < 1 ||
    parsedPort > 65_535
  ) {
    return null;
  }

  return {
    host,
    port,
    ssl: true,
    username,
    password: "",
  };
}

function projectServerCustomImapSettings(
  value: unknown,
  fallback: CustomImapSettings,
): CustomImapSettings | null {
  const parsed = parseServerCustomImapSettings(value);
  if (parsed) {
    return parsed;
  }
  return value === undefined
    ? { ...fallback, password: "" }
    : null;
}

function projectConnectedManagedInboxesOntoOnboardingState(
  state: OnboardingState,
  managedInboxes: StoredManagedWorkspaceInbox[],
): OnboardingState {
  const selectedInboxPositions = [...new Set(state.selectedInboxes)];
  const selectedPositions = new Set(selectedInboxPositions);
  const canRecoverPositionalMailboxIdentity =
    managedInboxes.length === selectedInboxPositions.length &&
    managedInboxes.every((mailbox, index) => {
      const explicitPosition = mailbox?.onboardingInboxId;
      return (
        explicitPosition === undefined ||
        explicitPosition === selectedInboxPositions[index]
      );
    });
  const nextConnections = { ...state.inboxConnections };
  let didProject = false;

  for (const inboxPosition of selectedPositions) {
    const positionMatches = managedInboxes.filter(
      (mailbox, index) =>
        mailbox?.onboardingInboxId === inboxPosition ||
        (canRecoverPositionalMailboxIdentity &&
          mailbox?.onboardingInboxId === undefined &&
          selectedInboxPositions[index] === inboxPosition),
    );
    if (positionMatches.length !== 1) {
      continue;
    }

    const mailbox = positionMatches[0];
    const mailboxId = typeof mailbox.id === "string" ? mailbox.id.trim() : "";
    const email = typeof mailbox.email === "string" ? mailbox.email.trim().toLowerCase() : "";
    const currentConnection =
      nextConnections[inboxPosition] ?? createInboxConnection();
    if (
      !mailboxId ||
      mailbox.id !== mailboxId ||
      !email ||
      mailbox.email !== email ||
      !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)
    ) {
      continue;
    }

    const isConnectedGoogleMailbox =
      mailbox.provider === "google" &&
      mailbox.connectionMethod === "oauth" &&
      mailbox.connected === true &&
      mailbox.connectionStatus === "connected";
    const isReconnectRequiredGoogleMailbox =
      mailbox.provider === "google" &&
      mailbox.connectionMethod === "oauth" &&
      mailbox.connected === false &&
      mailbox.connectionStatus === "connection_failed" &&
      mailbox.connectionMessage ===
        GMAIL_OAUTH_RECONNECT_REQUIRED_CONNECTION_MESSAGE;
    const projectedCustomImap =
      mailbox.provider === "custom_imap"
        ? projectServerCustomImapSettings(
            mailbox.customImap,
            currentConnection.customImap,
          )
        : null;
    const projectedCustomSmtp =
      mailbox.provider === "custom_imap"
        ? projectServerCustomSmtpSettings(
            mailbox.customSmtp,
            currentConnection.customSmtp,
          )
        : null;
    const customImapCapabilityState =
      getAuthoritativeCustomImapCapabilityState(mailbox);
    const isAuthoritativeCustomImapMailbox =
      isValidServerManagedInboxId(mailboxId) &&
      isWellFormedManagedMailboxEnvelope(mailbox) &&
      hasOnlySafeManagedMailboxFields(mailbox) &&
      mailbox.provider === "custom_imap" &&
      mailbox.connectionMethod === "imap" &&
      mailbox.connected === true &&
      mailbox.connectionStatus === "connected" &&
      customImapCapabilityState !== null &&
      projectedCustomImap !== null &&
      projectedCustomSmtp !== null &&
      hasAuthoritativeCustomImapCapabilityShape(mailbox) &&
      hasValidManagedMailboxOptionalMetadata(mailbox) &&
      !containsUnsafeMailboxMetadata(mailbox);
    if (
      !isConnectedGoogleMailbox &&
      !isReconnectRequiredGoogleMailbox &&
      !isAuthoritativeCustomImapMailbox
    ) {
      continue;
    }

    const sameIdCount = managedInboxes.filter(
      (candidate) =>
        typeof candidate?.id === "string" &&
        candidate.id.trim().toLowerCase() === mailboxId.toLowerCase(),
    ).length;
    const sameEmailCount = managedInboxes.filter(
      (candidate) =>
        typeof candidate?.email === "string" &&
        candidate.email.trim().toLowerCase() === email,
    ).length;
    if (sameIdCount !== 1 || sameEmailCount !== 1) {
      continue;
    }

    nextConnections[inboxPosition] =
      isConnectedGoogleMailbox || isReconnectRequiredGoogleMailbox
      ? {
          ...currentConnection,
          serverMailboxId: mailboxId,
          provider: "google",
          email,
          connected: isConnectedGoogleMailbox,
          connectionMethod: "oauth",
          connectionStatus: isConnectedGoogleMailbox
            ? "connected"
            : "connection_failed",
          connectionMessage:
            isReconnectRequiredGoogleMailbox
              ? GMAIL_OAUTH_RECONNECT_REQUIRED_CONNECTION_MESSAGE
              : typeof mailbox.connectionMessage === "string"
              ? mailbox.connectionMessage
              : null,
          oauthAuthorizationUrl: null,
        }
      : {
          ...currentConnection,
          serverMailboxId: mailboxId,
          provider: "custom_imap",
          email,
          connected: true,
          connectionMethod: "imap",
          connectionStatus: "connected",
          imapConnectionStatus: "connected",
          smtpConnectionStatus:
            customImapCapabilityState === "full"
              ? "connected"
              : mailbox.smtpConnectionStatus,
          fullyConnected: customImapCapabilityState === "full",
          connectionMessage:
            typeof mailbox.connectionMessage === "string"
              ? mailbox.connectionMessage
              : null,
          oauthAuthorizationUrl: null,
          customImap: projectedCustomImap!,
          customSmtp: projectedCustomSmtp!,
        };
    didProject = true;
  }

  return didProject ? { ...state, inboxConnections: nextConnections } : state;
}

type AuthoritativeOnboardingCompletionSnapshot =
  | {
      status: "ready";
      config: UserAccountConfig;
      session: OnboardingSessionV1;
      onboardingState: OnboardingState;
    }
  | { status: "invalid" | "inboxes_incomplete" };

function onboardingChoicesAreTypeExact(
  left: OnboardingChoices,
  right: OnboardingChoices,
) {
  try {
    return JSON.stringify(left) === JSON.stringify(right);
  } catch {
    return false;
  }
}

function resolveAuthoritativeOnboardingCompletionSnapshot({
  result,
  ownerEmail,
  requireCompleted,
  expectedChoices,
}: {
  result: UserAccountConfigReadResult;
  ownerEmail: string;
  requireCompleted: boolean;
  expectedChoices?: OnboardingChoices;
}): AuthoritativeOnboardingCompletionSnapshot {
  if (result.status !== "found") {
    return { status: "invalid" };
  }
  const storedOwner =
    typeof result.config.email === "string"
      ? result.config.email.trim().toLowerCase()
      : "";
  if (!storedOwner || storedOwner !== ownerEmail.trim().toLowerCase()) {
    return { status: "invalid" };
  }

  const parsedSession = parseAccountOnboardingSession(
    result.config.onboardingSession,
  );
  if (
    parsedSession.status !== "valid" ||
    (requireCompleted &&
      (parsedSession.session.completed !== true ||
        parsedSession.session.currentStep !== ONBOARDING_STEP_MAX)) ||
    (expectedChoices &&
      !onboardingChoicesAreTypeExact(
        parsedSession.session.choices,
        expectedChoices,
      ))
  ) {
    return { status: "invalid" };
  }

  const onboardingState = projectConnectedManagedInboxesOntoOnboardingState(
    buildOnboardingStateFromChoices(parsedSession.session.choices),
    getServerManagedInboxesForHydration(result.config),
  );
  if (!areSelectedOnboardingInboxesFullyConnected(onboardingState)) {
    return { status: "inboxes_incomplete" };
  }

  return {
    status: "ready",
    config: result.config,
    session: parsedSession.session,
    onboardingState,
  };
}

type MemberOnboardingCompletionResult =
  | Extract<AuthoritativeOnboardingCompletionSnapshot, { status: "ready" }>
  | {
      status:
        | "error"
        | "inboxes_incomplete"
        | "session_changed"
        | "unauthorized"
        | "verification_required";
    };

async function completeMemberOnboardingAuthoritatively({
  accountKey,
  ownerEmail,
  signal,
  prepareForCompletion,
  isActive,
  load = loadUserAccountConfigAfterPendingWrites,
  complete = completeUserOnboarding,
}: {
  accountKey: string;
  ownerEmail: string;
  signal?: AbortSignal;
  prepareForCompletion: (accountKey: string) => boolean;
  isActive: () => boolean;
  load?: (signal?: AbortSignal) => Promise<UserAccountConfigReadResult>;
  complete?: () => Promise<UserAccountConfigSaveResult>;
}): Promise<MemberOnboardingCompletionResult> {
  if (!prepareForCompletion(accountKey) || !isActive()) {
    return { status: "session_changed" };
  }

  let initialResult: UserAccountConfigReadResult;
  try {
    initialResult = await load(signal);
  } catch {
    return { status: "error" };
  }
  if (!isActive() || signal?.aborted) {
    return { status: "session_changed" };
  }
  if (initialResult.status === "unauthorized") {
    return { status: "unauthorized" };
  }
  const initialSnapshot = resolveAuthoritativeOnboardingCompletionSnapshot({
    result: initialResult,
    ownerEmail,
    requireCompleted: false,
  });
  if (initialSnapshot.status !== "ready") {
    return {
      status:
        initialSnapshot.status === "inboxes_incomplete"
          ? "inboxes_incomplete"
          : "error",
    };
  }

  let mutationResult: UserAccountConfigSaveResult;
  try {
    mutationResult = await complete();
  } catch {
    return { status: "error" };
  }
  if (!isActive() || signal?.aborted) {
    return { status: "session_changed" };
  }
  if (mutationResult.status === "unauthorized") {
    return { status: "unauthorized" };
  }
  if (mutationResult.status !== "found") {
    return {
      status:
        mutationResult.error.code === "onboarding_mailboxes_incomplete"
          ? "inboxes_incomplete"
          : "error",
    };
  }

  let readbackResult: UserAccountConfigReadResult;
  try {
    readbackResult = await load(signal);
  } catch {
    return { status: "verification_required" };
  }
  if (!isActive() || signal?.aborted) {
    return { status: "session_changed" };
  }
  if (readbackResult.status === "unauthorized") {
    return { status: "unauthorized" };
  }
  if (readbackResult.status !== "found") {
    return { status: "verification_required" };
  }

  const readbackSnapshot = resolveAuthoritativeOnboardingCompletionSnapshot({
    result: readbackResult,
    ownerEmail,
    requireCompleted: true,
    expectedChoices: initialSnapshot.session.choices,
  });
  if (readbackSnapshot.status !== "ready") {
    return {
      status:
        readbackSnapshot.status === "inboxes_incomplete"
          ? "inboxes_incomplete"
          : "verification_required",
    };
  }
  return readbackSnapshot;
}

function isWellFormedManagedMailboxEnvelope(
  mailbox: StoredManagedWorkspaceInbox,
) {
  if (!mailbox || typeof mailbox !== "object" || Array.isArray(mailbox)) {
    return false;
  }
  const mailboxId =
    typeof mailbox.id === "string" ? mailbox.id.trim() : "";
  const email =
    typeof mailbox.email === "string"
      ? mailbox.email.trim().toLowerCase()
      : "";
  return (
    Boolean(mailboxId) &&
    mailbox.id === mailboxId &&
    isValidServerManagedInboxId(mailboxId) &&
    Boolean(email) &&
    mailbox.email === email &&
    /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) &&
    (mailbox.provider === "google" ||
      mailbox.provider === "microsoft" ||
      mailbox.provider === "icloud" ||
      mailbox.provider === "yahoo" ||
      mailbox.provider === "custom_imap") &&
    typeof mailbox.connected === "boolean" &&
    ((mailbox.connectionMethod === "oauth" &&
      (mailbox.provider === "google" ||
        mailbox.provider === "microsoft")) ||
      (mailbox.connectionMethod === "imap" &&
        (mailbox.provider === "icloud" ||
          mailbox.provider === "yahoo" ||
          mailbox.provider === "custom_imap"))) &&
    (mailbox.connectionStatus === "not_connected" ||
      mailbox.connectionStatus === "oauth_required" ||
      mailbox.connectionStatus === "waiting_for_authentication" ||
      mailbox.connectionStatus ===
        "authenticated_pending_activation" ||
      mailbox.connectionStatus === "connected" ||
      mailbox.connectionStatus === "connection_failed") &&
    (mailbox.connected
      ? mailbox.connectionStatus === "connected"
      : mailbox.connectionStatus !== "connected") &&
    hasOnlySafeManagedMailboxFields(mailbox) &&
    hasSafeManagedMailboxSettings(mailbox) &&
    hasValidManagedMailboxOptionalMetadata(mailbox) &&
    (mailbox.provider !== "custom_imap" ||
      mailbox.connected !== true ||
      ((mailbox.customImap === undefined ||
        parseServerCustomImapSettings(mailbox.customImap) !== null) &&
        isSafeServerCustomSmtpSettings(mailbox.customSmtp) &&
        hasAuthoritativeCustomImapCapabilityShape(mailbox))) &&
    (mailbox.onboardingInboxId === undefined ||
      (typeof mailbox.onboardingInboxId === "string" &&
        isInboxId(mailbox.onboardingInboxId)))
  );
}

function hasValidManagedMailboxOptionalMetadata(
  mailbox: StoredManagedWorkspaceInbox,
) {
  const metadata = mailbox as StoredManagedWorkspaceInbox &
    Record<string, unknown>;
  const focusPreferences = metadata.focusPreferences;
  const customImapFolderMappings = metadata.customImapFolderMappings;
  return (
    (mailbox.title === undefined || typeof mailbox.title === "string") &&
    (mailbox.connectionMessage === undefined ||
      mailbox.connectionMessage === null ||
      typeof mailbox.connectionMessage === "string") &&
    (mailbox.oauthAuthorizationUrl === undefined ||
      mailbox.oauthAuthorizationUrl === null) &&
    (mailbox.imapConnectionStatus === undefined ||
      mailbox.imapConnectionStatus === "not_configured" ||
      mailbox.imapConnectionStatus === "not_connected" ||
      mailbox.imapConnectionStatus === "connected" ||
      mailbox.imapConnectionStatus === "connection_failed") &&
    (mailbox.smtpConnectionStatus === undefined ||
      mailbox.smtpConnectionStatus === "not_configured" ||
      mailbox.smtpConnectionStatus === "not_connected" ||
      mailbox.smtpConnectionStatus === "connected" ||
      mailbox.smtpConnectionStatus === "connection_failed") &&
    (mailbox.imapPasswordSet === undefined ||
      typeof mailbox.imapPasswordSet === "boolean") &&
    (mailbox.smtpPasswordSet === undefined ||
      typeof mailbox.smtpPasswordSet === "boolean") &&
    (mailbox.fullyConnected === undefined ||
      typeof mailbox.fullyConnected === "boolean") &&
    (metadata.internalRole === undefined ||
      metadata.internalRole === null ||
      (typeof metadata.internalRole === "string" &&
        ONBOARDING_INTERNAL_ROLES.has(metadata.internalRole))) &&
    (customImapFolderMappings === undefined ||
      projectCustomImapFolderMappingsForBrowserStorage(
        customImapFolderMappings,
      ) !== undefined) &&
    (focusPreferences === undefined ||
      focusPreferences === null ||
      (isPlainRecord(focusPreferences) &&
        hasOnlyKeys(focusPreferences, ONBOARDING_FOCUS_KEYS) &&
        Object.values(focusPreferences).every(
          (value) =>
            typeof value === "string" &&
            LEGACY_ONBOARDING_FOCUS_LEVELS.has(value),
        )))
  );
}

function resolveCustomImapAccountConfigReadback(
  state: OnboardingState,
  snapshot: CustomImapOnboardingAttemptSnapshot,
  result: UserAccountConfigReadResult,
): CustomImapOnboardingReconciliationResult {
  const snapshotMatchesCurrentState =
    doesCustomImapOnboardingSnapshotMatchState(snapshot, state);
  if (result.status === "missing") {
    return { status: "absent" };
  }
  if (
    result.status !== "found" ||
    !hasOwn(result.config as Record<string, unknown>, "onboardingSession") ||
    !hasOwn(result.config as Record<string, unknown>, "managedInboxes") ||
    !Array.isArray(result.config.managedInboxes)
  ) {
    return { status: "required" };
  }

  const parsedSession = parseAccountOnboardingSession(
    result.config.onboardingSession,
  );
  if (
    parsedSession.status !== "valid" ||
    parsedSession.session.completed !== false ||
    !parsedSession.session.choices.selectedInboxes.includes(
      snapshot.onboardingInboxId,
    ) ||
    parsedSession.session.choices.selectedInboxes.length !==
      snapshot.selectedInboxes.length ||
    !parsedSession.session.choices.selectedInboxes.every(
      (inboxId, index) => inboxId === snapshot.selectedInboxes[index],
    )
  ) {
    return { status: "required" };
  }

  const managedInboxes = getServerManagedInboxesForHydration(result.config);
  const positionMatches = managedInboxes.filter(
    (mailbox) =>
      mailbox?.onboardingInboxId === snapshot.onboardingInboxId,
  );
  const emailMatches = managedInboxes.filter(
    (mailbox) =>
      typeof mailbox?.email === "string" &&
      mailbox.email.trim().toLowerCase() === snapshot.normalizedEmail,
  );
  if (
    positionMatches.length === 0 &&
    emailMatches.length === 0
  ) {
    return { status: "absent" };
  }
  if (
    positionMatches.length !== 1 ||
    emailMatches.length !== 1 ||
    positionMatches[0] !== emailMatches[0]
  ) {
    return { status: "required" };
  }

  const mailbox = positionMatches[0];
  const mailboxId =
    typeof mailbox.id === "string" ? mailbox.id.trim() : "";
  const email =
    typeof mailbox.email === "string"
      ? mailbox.email.trim().toLowerCase()
      : "";
  const parsedCustomImap = parseServerCustomImapSettings(
    mailbox.customImap,
  );
  const capabilityState =
    getAuthoritativeCustomImapCapabilityState(mailbox);
  const currentConnection =
    state.inboxConnections[snapshot.onboardingInboxId] ??
    createInboxConnection();
  const projectedCustomSmtp = projectServerCustomSmtpSettings(
    mailbox.customSmtp,
    currentConnection.customSmtp,
  );
  const projectedSmtpMatchesSnapshot =
    projectedCustomSmtp !== null &&
    projectedCustomSmtp.host.toLowerCase() === snapshot.normalizedSmtpHost &&
    projectedCustomSmtp.port === snapshot.smtpPort &&
    projectedCustomSmtp.security === snapshot.smtpSecurity &&
    projectedCustomSmtp.useSameCredentials === snapshot.useSameCredentials &&
    projectedCustomSmtp.username ===
      (snapshot.useSameCredentials ? "" : snapshot.normalizedSmtpUsername);
  const fullSmtpMatchesSnapshot =
    capabilityState === "full" && projectedSmtpMatchesSnapshot;
  const partialSmtpIsAuthoritative =
    capabilityState === "partial" &&
    (isSafeEmptyServerCustomSmtp(mailbox.customSmtp) ||
      projectedSmtpMatchesSnapshot);
  const sameIdCount = managedInboxes.filter(
    (candidate) =>
      typeof candidate?.id === "string" &&
      candidate.id.trim().toLowerCase() === mailboxId.toLowerCase(),
  ).length;
  if (
    !mailboxId ||
    mailbox.id !== mailboxId ||
    !isValidServerManagedInboxId(mailboxId) ||
    sameIdCount !== 1 ||
    (snapshot.serverMailboxId !== null &&
      mailboxId !== snapshot.serverMailboxId) ||
    email !== snapshot.normalizedEmail ||
    mailbox.email !== email ||
    mailbox.provider !== snapshot.provider ||
    mailbox.connectionMethod !== "imap" ||
    mailbox.connected !== true ||
    mailbox.connectionStatus !== "connected" ||
    !isWellFormedManagedMailboxEnvelope(mailbox) ||
    !hasOnlySafeManagedMailboxFields(mailbox) ||
    !hasValidManagedMailboxOptionalMetadata(mailbox) ||
    containsUnsafeMailboxMetadata(mailbox) ||
    parsedCustomImap === null ||
    parsedCustomImap.host.toLowerCase() !== snapshot.normalizedHost ||
    parsedCustomImap.port !== snapshot.port ||
    parsedCustomImap.username !== snapshot.normalizedUsername ||
    parsedCustomImap.ssl !== snapshot.ssl ||
    (!partialSmtpIsAuthoritative && !fullSmtpMatchesSnapshot)
  ) {
    return { status: "required" };
  }

  if (!snapshotMatchesCurrentState) {
    return { status: "conflict" };
  }
  return {
    status: "matched",
    serverMailboxId: mailboxId,
    connection: {
      ...currentConnection,
      serverMailboxId: mailboxId,
      provider: "custom_imap",
      email,
      connected: true,
      connectionMethod: "imap",
      connectionStatus: "connected",
      imapConnectionStatus: "connected",
      smtpConnectionStatus:
        capabilityState === "full"
          ? "connected"
          : mailbox.smtpConnectionStatus,
      fullyConnected: capabilityState === "full",
      connectionMessage:
        typeof mailbox.connectionMessage === "string"
          ? mailbox.connectionMessage
          : null,
      oauthAuthorizationUrl: null,
      customImap: parsedCustomImap,
      customSmtp: projectedCustomSmtp!,
    },
  };
}

function createCleanAccountConfigStartupState(): AccountConfigStartupAccountState {
  const onboardingState = normalizeOnboardingState(initialOnboardingState);

  return {
    displayNameOverrides: {},
    authoritativeManagedInboxes: [],
    persistedOnboardingSession: null,
    onboardingState,
    onboardingStep: ONBOARDING_STEP_MIN,
    userConfig: null,
    view: "onboarding",
  };
}

function createCleanUserAccountConfig(): UserAccountConfig {
  return {
    v: 1,
    onboardingSession: {},
    managedInboxes: [],
    mailboxTitleOverrides: {},
    primaryManagedInboxId: null,
    mailboxFocusPreferenceOverrides: {},
    inboxSignatures: {},
    smartFolders: [],
    uiPreferences: {},
    displayNameOverrides: {},
  };
}

function writeOnboardingSessionMirror(
  session: PersistedOnboardingSession | null,
  storage: AccountConfigStorage,
  storageKeys: LocalOnboardingStorageKeys,
) {
  const safeSession = sanitizeOnboardingSessionForAccountConfig(session);
  if (safeSession?.completed) {
    storage.setItem(storageKeys.state, JSON.stringify(safeSession));
    storage.removeItem(storageKeys.draft);
    storage.setItem(storageKeys.view, "workspace");
    return;
  }

  storage.removeItem(storageKeys.state);
  if (safeSession) {
    storage.setItem(storageKeys.draft, JSON.stringify(safeSession));
  } else {
    storage.removeItem(storageKeys.draft);
  }
  storage.setItem(storageKeys.view, "onboarding");
}

function writeFoundAccountConfigToLocalStorage(
  config: UserAccountConfig,
  accountStorageOwnerKey: string,
  storage: AccountConfigStorage,
) {
  const previousManagedInboxes = parseStoredManagedWorkspaceInboxes(
    false,
    storage,
  );
  const managedInboxes = getServerManagedInboxesForHydration(config);
  const sanitizedManagedInboxes = sanitizeManagedInboxesForBrowserStorage(
    managedInboxes,
  );
  const workspaceUserId = normalizeAccountStorageKey(accountStorageOwnerKey);
  storage.removeItem(
    buildPrimaryManagedInboxStorageKey(workspaceUserId, previousManagedInboxes),
  );

  storage.setItem(
    MANAGED_INBOXES_STORAGE_KEY,
    JSON.stringify(sanitizedManagedInboxes),
  );
  storage.setItem(
    MAILBOX_TITLE_OVERRIDES_STORAGE_KEY,
    JSON.stringify(config.mailboxTitleOverrides ?? {}),
  );
  storage.setItem(
    buildMailboxFocusPreferenceOverridesStorageKey(workspaceUserId),
    JSON.stringify(config.mailboxFocusPreferenceOverrides ?? {}),
  );
  storage.setItem(
    MAIL_SIGNATURES_STORAGE_KEY,
    JSON.stringify(config.inboxSignatures ?? {}),
  );
  storage.setItem(
    SMART_FOLDERS_STORAGE_KEY,
    JSON.stringify(config.smartFolders ?? []),
  );
  storage.setItem(
    CUEVION_DISPLAY_NAME_OVERRIDES_STORAGE_KEY,
    JSON.stringify(config.displayNameOverrides ?? {}),
  );

  const primaryStorageKey = buildPrimaryManagedInboxStorageKey(
    workspaceUserId,
    sanitizedManagedInboxes,
  );
  if (config.primaryManagedInboxId) {
    storage.setItem(primaryStorageKey, config.primaryManagedInboxId);
  } else {
    storage.removeItem(primaryStorageKey);
  }

  const uiPreferences = config.uiPreferences ?? {};
  const themeMode = normalizeStoredWorkspaceThemeMode(uiPreferences.themeMode);
  if (themeMode) {
    storage.setItem(WORKSPACE_THEME_MODE_STORAGE_KEY, themeMode);
  } else {
    storage.removeItem(WORKSPACE_THEME_MODE_STORAGE_KEY);
  }

  if (typeof uiPreferences.aiSuggestionsEnabled === "boolean") {
    storage.setItem(
      AI_SUGGESTIONS_STORAGE_KEY,
      String(uiPreferences.aiSuggestionsEnabled),
    );
  } else {
    storage.removeItem(AI_SUGGESTIONS_STORAGE_KEY);
  }

  if (typeof uiPreferences.inboxChangesEnabled === "boolean") {
    storage.setItem(
      INBOX_CHANGES_STORAGE_KEY,
      String(uiPreferences.inboxChangesEnabled),
    );
  } else {
    storage.removeItem(INBOX_CHANGES_STORAGE_KEY);
  }

  if (typeof uiPreferences.teamActivityEnabled === "boolean") {
    storage.setItem(
      TEAM_ACTIVITY_STORAGE_KEY,
      String(uiPreferences.teamActivityEnabled),
    );
  } else {
    storage.removeItem(TEAM_ACTIVITY_STORAGE_KEY);
  }
}

function prepareMissingAccountConfigForFirstExplicitSave({
  accountStorageOwnerKey,
  storage,
}: {
  accountStorageOwnerKey: string;
  storage: AccountConfigStorage;
}) {
  writeFoundAccountConfigToLocalStorage(
    createCleanUserAccountConfig(),
    accountStorageOwnerKey,
    storage,
  );
}

function applyLoadedUserAccountConfig(
  result: UserAccountConfigReadResult,
  accountStorageOwnerKey: string,
  storage: AccountConfigStorage,
  resetOnboarding = false,
): AccountConfigHydrationOutcome {
  if (result.status === "found") {
    if (
      !hasOwn(
        result.config as Record<string, unknown>,
        "onboardingSession",
      )
    ) {
      throw new Error("The stored onboarding session was missing.");
    }
    const rawOnboardingSession = result.config.onboardingSession;
    const parsedSession = parseAccountOnboardingSession(
      rawOnboardingSession,
    );
    if (parsedSession.status === "invalid") {
      throw new Error("The stored onboarding session was malformed.");
    }
    const didResetOnboarding =
      resetOnboarding &&
      parsedSession.status === "valid" &&
      parsedSession.session.completed === false;
    const persistedOnboardingSession = didResetOnboarding
      ? null
      : parsedSession.session;
    const onboardingChoicesState =
      persistedOnboardingSession
        ? buildOnboardingStateFromChoices(persistedOnboardingSession.choices)
        :
      normalizeOnboardingState(initialOnboardingState);
    const onboardingState = projectConnectedManagedInboxesOntoOnboardingState(
      onboardingChoicesState,
      getServerManagedInboxesForHydration(result.config),
    );
    const isCompleted = persistedOnboardingSession?.completed === true;
    let accountState: AccountConfigStartupAccountState = {
      displayNameOverrides:
        result.config.displayNameOverrides &&
        typeof result.config.displayNameOverrides === "object"
          ? result.config.displayNameOverrides
          : {},
      authoritativeManagedInboxes: sanitizeManagedInboxesForBrowserStorage(
        getServerManagedInboxesForHydration(result.config),
      ),
      persistedOnboardingSession,
      onboardingState,
      onboardingStep:
        persistedOnboardingSession?.currentStep ?? ONBOARDING_STEP_MIN,
      userConfig: isCompleted
        ? buildUserConfig(onboardingState)
        : null,
      view: isCompleted ? "workspace" : "onboarding",
    };

    writeFoundAccountConfigToLocalStorage(
      result.config,
      accountStorageOwnerKey,
      storage,
    );

    if (didResetOnboarding) {
      accountState = {
        ...createCleanAccountConfigStartupState(),
        displayNameOverrides: accountState.displayNameOverrides,
      };
    }

    return {
      status: "found",
      accountState,
      didResetOnboarding,
      clearResetQuery: resetOnboarding,
      expectedWorkspaceHydrationEcho:
        accountState.view === "workspace"
          ? buildExpectedWorkspaceHydrationEcho(
              accountStorageOwnerKey,
              accountState,
              storage,
            )
          : null,
    };
  }

  if (result.status === "missing") {
    return {
      status: "missing",
      accountState: createCleanAccountConfigStartupState(),
      didResetOnboarding: false,
      clearResetQuery: resetOnboarding,
    };
  }

  if (result.status === "unauthorized") {
    return { status: "unauthorized" };
  }

  return {
    status: "error",
    errorStatus: result.status,
  };
}

function buildExpectedWorkspaceHydrationEcho(
  accountStorageOwnerKey: string,
  accountState: AccountConfigStartupAccountState,
  storage: AccountConfigStorage,
): UserAccountConfig {
  const {
    v: _version,
    displayNameOverrides: _displayNameOverrides,
    ...expectedWorkspacePayload
  } = buildAccountConfigFromLocalStorage(
    accountStorageOwnerKey,
    accountState.persistedOnboardingSession,
    accountState.displayNameOverrides,
    storage,
  );

  return expectedWorkspacePayload;
}

type OnboardingStateChange =
  | OnboardingState
  | ((current: OnboardingState) => OnboardingState);

function createOnboardingAppHandlers({
  getOnboardingState,
  getOnboardingStep,
  commitAccountConfigMutation,
  setOnboardingState,
  setOnboardingStep,
  setPersistedOnboardingSession,
  canOpenWorkspace,
  openWorkspace,
}: {
  getOnboardingState: () => OnboardingState;
  getOnboardingStep: () => number;
  commitAccountConfigMutation: (mutation: () => void) => void;
  setOnboardingState: (state: OnboardingState) => void;
  setOnboardingStep: (step: number) => void;
  setPersistedOnboardingSession: (
    session: PersistedOnboardingSession,
  ) => void;
  canOpenWorkspace: boolean;
  openWorkspace: (config: UserConfig) => void;
}) {
  const resolveStateChange = (value: OnboardingStateChange) =>
    typeof value === "function" ? value(getOnboardingState()) : value;

  return {
    onStateChange(value: OnboardingStateChange) {
      setOnboardingState(resolveStateChange(value));
    },
    onSafeStateChange(value: OnboardingStateChange) {
      const nextState = resolveStateChange(value);
      commitAccountConfigMutation(() => {
        setOnboardingState(nextState);
        setPersistedOnboardingSession(
          createIncompleteOnboardingSession(nextState, getOnboardingStep()),
        );
      });
    },
    onStepChange(nextStep: number) {
      const safeStep = clampOnboardingStep(nextStep);
      commitAccountConfigMutation(() => {
        setOnboardingStep(safeStep);
        setPersistedOnboardingSession(
          createIncompleteOnboardingSession(getOnboardingState(), safeStep),
        );
      });
    },
    onOpenWorkspace(config: UserConfig) {
      if (!canOpenWorkspace) {
        return;
      }
      openWorkspace(config);
    },
  };
}

export const accountConfigOrchestration = {
  buildAuthoritativeConfig: buildAuthoritativeAccountConfigFromLocalStorage,
  canOpenWorkspaceWithoutServerCompletion,
  createCleanStartupState: createCleanAccountConfigStartupState,
  createIncompleteSession: createIncompleteOnboardingSession,
  createHydrator: createAccountConfigHydrator,
  createOnboardingHandlers: createOnboardingAppHandlers,
  createSaveQueue: createAccountConfigSaveQueue,
  completeMemberOnboarding: completeMemberOnboardingAuthoritatively,
  consumeGoogleOAuthCallbackSignal,
  processGoogleOAuthCallbackSignal,
  hydrateLocalOnboardingScope: hydrateLocalOnboardingIdentityScope,
  hydrateChoices: buildOnboardingStateFromChoices,
  parseOnboardingSession: parseAccountOnboardingSession,
  projectChoices: projectOnboardingChoices,
  projectConnectedManagedInboxes: projectConnectedManagedInboxesOntoOnboardingState,
  resolveCustomImapReadback: resolveCustomImapAccountConfigReadback,
  resolveLocalOnboardingIdentityScope,
  resolveSessionDisposition: resolveAccountConfigSessionDisposition,
  scrubManagedInboxBrowserStorage,
  writeOnboardingSessionMirror,
};

function normalizeGoogleOAuthCallbackSignal(
  value: unknown,
): GoogleOAuthCallbackSignal | null {
  if (
    !isPlainRecord(value) ||
    !hasOnlyKeys(value, [
      "status",
      "provider",
      "mode",
      "inboxPosition",
      "email",
      "mailboxId",
      "message",
    ]) ||
    (value.status !== "success" && value.status !== "error") ||
    value.provider !== "google" ||
    typeof value.email !== "string" ||
    typeof value.mailboxId !== "string" ||
    typeof value.message !== "string" ||
    (value.mode !== undefined &&
      value.mode !== "initial" &&
      value.mode !== "reconnect") ||
    (value.inboxPosition !== undefined && !isInboxId(value.inboxPosition))
  ) {
    return null;
  }

  const email = value.email.trim().toLowerCase();
  const mailboxId = value.mailboxId.trim();
  if (
    value.email !== email ||
    value.mailboxId !== mailboxId ||
    !email ||
    !mailboxId ||
    !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)
  ) {
    return null;
  }

  return {
    status: value.status,
    provider: "google",
    ...(value.mode !== undefined ? { mode: value.mode } : {}),
    ...(value.inboxPosition !== undefined
      ? { inboxPosition: value.inboxPosition }
      : {}),
    email,
    mailboxId,
    message:
      value.status === "error"
        ? "Google authentication was not completed. Try reconnecting Gmail."
        : value.message,
  };
}

function consumeGoogleOAuthCallbackSignal(storage: AccountConfigStorage) {
  const storedValue = storage.getItem(OAUTH_CALLBACK_RESULT_STORAGE_KEY);
  if (!storedValue) {
    return false;
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(storedValue);
  } catch {
    storage.removeItem(OAUTH_CALLBACK_RESULT_STORAGE_KEY);
    return false;
  }

  if (isPlainRecord(parsed) && parsed.provider === "microsoft") {
    return false;
  }

  storage.removeItem(OAUTH_CALLBACK_RESULT_STORAGE_KEY);
  return normalizeGoogleOAuthCallbackSignal(parsed) !== null;
}

function processGoogleOAuthCallbackSignal(
  storage: AccountConfigStorage,
  requestServerConfigReload: () => void,
) {
  const shouldReload = consumeGoogleOAuthCallbackSignal(storage);
  if (shouldReload) {
    requestServerConfigReload();
  }
  return shouldReload;
}

function normalizeMicrosoftOAuthCallbackStorageResult(
  value: unknown,
): MicrosoftOAuthCallbackStorageResult | null {
  if (!value || typeof value !== "object") {
    return null;
  }

  const result = value as Partial<MicrosoftOAuthCallbackStorageResult>;

  if (
    result.provider !== "microsoft" ||
    typeof result.email !== "string"
  ) {
    return null;
  }

  return {
    provider: "microsoft",
    email: result.email.trim().toLowerCase(),
    displayName:
      typeof result.displayName === "string" ? result.displayName.trim() : undefined,
    connectionMethod: "oauth",
    connectionStatus:
      result.connectionStatus === "connected" ||
      result.connectionStatus === "authenticated_pending_activation" ||
      result.connectionStatus === "connection_failed" ||
      result.connectionStatus === "oauth_required" ||
      result.connectionStatus === "waiting_for_authentication"
        ? result.connectionStatus
        : "connection_failed",
    connected: result.connected === true,
    message: typeof result.message === "string" ? result.message : null,
  };
}

function parsePendingOAuthManagedInbox(): PendingOAuthManagedInbox | null {
  const storedValue = window.localStorage.getItem(PENDING_OAUTH_MANAGED_INBOX_STORAGE_KEY);

  if (!storedValue) {
    return null;
  }

  try {
    const parsed = JSON.parse(storedValue) as PendingOAuthManagedInbox;
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

function applyMicrosoftOAuthCallbackResultToOnboardingState(
  state: OnboardingState,
  callbackResult: MicrosoftOAuthCallbackStorageResult,
): OnboardingState {
  const normalizedEmail = callbackResult.email?.trim().toLowerCase() ?? "";

  if (!normalizedEmail) {
    return state;
  }

  const providerName =
    callbackResult.provider === "microsoft" ? "Microsoft" : "Google";
  let didUpdate = false;
  const nextConnections = Object.fromEntries(
    Object.entries(state.inboxConnections).map(([inboxId, connection]) => {
      if (
        connection.provider !== callbackResult.provider ||
        connection.email.trim().toLowerCase() !== normalizedEmail
      ) {
        return [inboxId, connection];
      }

      didUpdate = true;
      const isConnected = callbackResult.connectionStatus === "connected";
      const connectionStatus = isConnected
        ? "connected"
        : callbackResult.connectionStatus ===
            "authenticated_pending_activation"
          ? "authenticated_pending_activation"
          : "connection_failed";
      const connectionMessage =
        callbackResult.message ??
        (isConnected
          ? `${providerName} authentication completed.`
          : connectionStatus === "authenticated_pending_activation"
            ? `${providerName} authentication completed. Tokens are stored only in the current server runtime. Final mailbox activation requires durable secure mailbox token storage.`
            : `${providerName} authentication failed.`);
      return [
        inboxId,
        {
          ...connection,
          connected:
            callbackResult.connected === true &&
            callbackResult.connectionStatus === "connected",
          connectionMethod: "oauth",
          connectionStatus,
          connectionMessage,
          oauthAuthorizationUrl: null,
        },
      ];
    }),
  ) as OnboardingState["inboxConnections"];

  return didUpdate
    ? {
        ...state,
        inboxConnections: nextConnections,
      }
    : state;
}

function applyMicrosoftOAuthCallbackResultToManagedInboxes(
  inboxes: StoredManagedWorkspaceInbox[],
  callbackResult: MicrosoftOAuthCallbackStorageResult,
  pendingMailbox?: PendingOAuthManagedInbox | null,
) {
  const normalizedEmail = callbackResult.email?.trim().toLowerCase() ?? "";

  if (!normalizedEmail) {
    return inboxes;
  }

  const providerName =
    callbackResult.provider === "microsoft" ? "Microsoft" : "Google";
  const pendingTitle =
    pendingMailbox?.provider === callbackResult.provider &&
    pendingMailbox?.email?.trim().toLowerCase() === normalizedEmail
      ? pendingMailbox.title?.trim()
      : "";
  const nextInboxes = inboxes.map((mailbox) => {
    if (
      mailbox.provider !== callbackResult.provider ||
      mailbox.email?.trim().toLowerCase() !== normalizedEmail
    ) {
      return mailbox;
    }

    const isConnected = callbackResult.connectionStatus === "connected";
    const connectionStatus = isConnected
      ? "connected"
      : callbackResult.connectionStatus === "authenticated_pending_activation"
        ? "authenticated_pending_activation"
        : "connection_failed";
    const connectionMessage =
      callbackResult.message ??
      (isConnected
        ? `${providerName} authentication completed.`
        : connectionStatus === "authenticated_pending_activation"
          ? `${providerName} authentication completed. Tokens are stored only in the current server runtime. Final mailbox activation requires durable secure mailbox token storage.`
          : `${providerName} authentication failed.`);

    return {
      ...mailbox,
      title: pendingTitle || mailbox.title,
      connected:
        callbackResult.connected === true &&
        callbackResult.connectionStatus === "connected",
      connectionMethod: "oauth",
      connectionStatus,
      connectionMessage,
      oauthAuthorizationUrl: null,
    };
  });

  return nextInboxes;
}

function resolveWorkspaceInviteUsers(
  onboardingState: OnboardingState,
): AuthenticatedCuevionUser[] {
  const managedInboxes = parseStoredManagedWorkspaceInboxes(false);
  const primaryManagedInbox = managedInboxes.find(
    (mailbox) => typeof mailbox.email === "string" && mailbox.email.trim().length > 0,
  );
  const primaryInboxId = onboardingState.selectedInboxes[0];
  const fallbackPrimaryEmail = primaryInboxId
    ? onboardingState.inboxConnections[primaryInboxId]?.email?.trim().toLowerCase() ?? ""
    : "";
  const workspaceEmail = (
    primaryManagedInbox?.email?.trim().toLowerCase() || fallbackPrimaryEmail
  ).trim();
  const recognizedUsers = new Map<string, AuthenticatedCuevionUser>();

  if (workspaceEmail) {
    recognizedUsers.set(workspaceEmail, {
      email: workspaceEmail,
      name:
        primaryManagedInbox?.title?.trim() || formatUserNameFromEmail(workspaceEmail),
      userType: "member",
    });

    const teamMembersStorageKey = buildTeamMembersStorageKey(workspaceEmail);
    const storedTeamMembers = window.localStorage.getItem(teamMembersStorageKey);

    if (storedTeamMembers) {
      try {
        const parsed = JSON.parse(storedTeamMembers) as StoredTeamMemberEntry[];

        if (Array.isArray(parsed)) {
          parsed.forEach((member) => {
            const memberEmail = member.email?.trim().toLowerCase() ?? "";

            if (!memberEmail || !isValidAuthEmail(memberEmail)) {
              return;
            }

            recognizedUsers.set(memberEmail, {
              email: memberEmail,
              name: member.name?.trim() || formatUserNameFromEmail(memberEmail),
              userType: "member",
            });
          });
        }
      } catch {
        // Ignore malformed local state and fall back to guest auth.
      }
    }
  }

  return Array.from(recognizedUsers.values());
}

function CollaborationInviteAuthGate({
  recognizedUsers,
  onAuthenticate,
}: {
  recognizedUsers: AuthenticatedCuevionUser[];
  onAuthenticate: (user: AuthenticatedCuevionUser) => void;
}) {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="min-h-screen bg-[linear-gradient(180deg,#f6efe7_0%,#efe5da_100%)] px-6 py-10 text-[color:#2f2a24] dark:bg-[linear-gradient(180deg,#171411_0%,#221c17_100%)] dark:text-[color:#f1e9de]">
      <div className="mx-auto flex min-h-[calc(100vh-5rem)] max-w-[560px] items-center justify-center">
        <div className="w-full rounded-[32px] border border-[rgba(120,104,89,0.14)] bg-[rgba(255,252,247,0.82)] p-8 shadow-[0_28px_80px_rgba(61,44,32,0.12)] backdrop-blur dark:border-[rgba(255,255,255,0.08)] dark:bg-[rgba(33,28,24,0.82)]">
          <div className="space-y-3 text-center">
            <div className="text-[0.72rem] font-medium uppercase tracking-[0.22em] text-[rgba(120,104,89,0.7)] dark:text-[rgba(214,201,189,0.64)]">
              Collaboration invite
            </div>
            <h1 className="text-[1.7rem] font-medium tracking-[-0.03em]">
              Sign in to continue
            </h1>
            <p className="text-[0.96rem] leading-7 text-[rgba(88,80,71,0.84)] dark:text-[rgba(222,211,200,0.76)]">
              Continue with your email to open this collaboration. Existing Cuevion users keep their current access. New users enter as guests.
            </p>
          </div>

          <div className="mt-8 space-y-3">
            <input
              value={email}
              onChange={(event) => {
                setEmail(event.target.value);
                setError(null);
              }}
              placeholder="name@cuevion.com"
              autoCorrect="off"
              autoCapitalize="none"
              spellCheck={false}
              className="w-full rounded-[20px] border border-[rgba(120,104,89,0.16)] bg-[rgba(255,255,255,0.78)] px-4 py-3 text-[0.98rem] leading-7 outline-none placeholder:text-[rgba(120,104,89,0.56)] dark:border-[rgba(255,255,255,0.08)] dark:bg-[rgba(44,38,33,0.86)] dark:placeholder:text-[rgba(210,196,183,0.42)]"
            />
            {error ? (
              <div className="text-[0.84rem] leading-6 text-[rgba(132,77,63,0.94)] dark:text-[rgba(244,186,168,0.84)]">
                {error}
              </div>
            ) : null}
          </div>

          <div className="mt-6 flex justify-end">
            <button
              type="button"
              onClick={() => {
                const normalizedEmail = email.trim().toLowerCase();
                if (!isValidAuthEmail(normalizedEmail)) {
                  setError("Enter a valid email address to continue.");
                  return;
                }

                const matchedUser = recognizedUsers.find(
                  (user) => user.email === normalizedEmail,
                );

                onAuthenticate(
                  matchedUser ?? {
                    email: normalizedEmail,
                    name: formatUserNameFromEmail(normalizedEmail),
                    userType: "guest",
                  },
                );
              }}
              className={premiumAccessButtonClass}
            >
              Continue
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

type TeamInviteRouteStatus =
  | "loading"
  | "ready"
  | "updating"
  | "accepted"
  | "declined"
  | "expired"
  | "used"
  | "wrong-user"
  | "invalid"
  | "unauthorized"
  | "unavailable";

export function classifyTeamInviteRouteFailure(
  failureStatus: TeamLifecycleFailureStatus,
  errorCode?: string,
): TeamInviteRouteStatus {
  const code = errorCode?.trim().toLowerCase().replace(/[\s-]+/g, "_") ?? "";
  if (failureStatus === "forbidden" && code.includes("recipient")) {
    return "wrong-user";
  }
  if (failureStatus === "expired") {
    return "expired";
  }
  if (failureStatus === "used" || failureStatus === "conflict") {
    return "used";
  }
  if (failureStatus === "invalid") {
    return "invalid";
  }
  if (failureStatus === "unauthorized") {
    return "unauthorized";
  }
  return "unavailable";
}

function TeamInviteRouteView({
  route,
  sessionStatus,
  sessionUser,
}: {
  route: TeamInviteRoute;
  sessionStatus: MemberSessionStatus;
  sessionUser: AuthenticatedCuevionUser | null;
}) {
  const [invite, setInvite] = useState<PublicTeamInvite | null>(null);
  const [status, setStatus] = useState<TeamInviteRouteStatus>("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const loadInvite = async () => {
      setStatus("loading");
      setError(null);

      const result = await fetchTeamInvite(route.inviteToken);

      if (cancelled) {
        return;
      }

      if (!result.ok) {
        setInvite(null);
        setError(result.error?.message ?? "This team invite is no longer available.");
        setStatus(classifyTeamInviteRouteFailure(result.status, result.error?.code));
        return;
      }

      setInvite(result.invite);
      setStatus(
        result.invite.status === "accepted"
          ? "accepted"
          : result.invite.status === "declined"
            ? "declined"
            : result.invite.status === "cancelled"
              ? "used"
              : "ready",
      );
    };

    void loadInvite();

    return () => {
      cancelled = true;
    };
  }, [route.inviteToken]);

  const handleInviteAction = async (actionType: "accept" | "decline") => {
    if (
      !invite ||
      invite.status !== "pending" ||
      status === "updating" ||
      sessionStatus !== "authenticated" ||
      !sessionUser
    ) {
      return;
    }

    setStatus("updating");
    setError(null);

    const result = await mutateTeamInvite({
      token: route.inviteToken,
      action: {
        type: actionType,
      },
    });

    if (!result.ok) {
      setError(result.error?.message ?? "Could not update this team invite.");
      setStatus(classifyTeamInviteRouteFailure(result.status, result.error?.code));
      return;
    }

    setInvite(result.invite);
    setStatus(result.invite.status === "accepted" ? "accepted" : "declined");
  };

  const inviteStatusLabel =
    invite?.status === "accepted"
      ? "Accepted"
      : invite?.status === "declined"
        ? "Declined"
        : invite?.status === "cancelled"
          ? "Already handled"
          : "Invited";

  const effectiveStatus: TeamInviteRouteStatus =
    status === "ready" && sessionStatus === "unauthenticated"
      ? "unauthorized"
      : status === "ready" && sessionStatus === "unavailable"
        ? "unavailable"
        : status;
  const stateMessage =
    effectiveStatus === "expired"
      ? "This invitation has expired."
      : effectiveStatus === "used"
        ? "This invitation has already been handled."
        : effectiveStatus === "wrong-user"
          ? "This invitation belongs to a different signed-in user."
          : effectiveStatus === "invalid"
            ? "This invitation link is invalid."
            : effectiveStatus === "unauthorized"
              ? "Sign in with the invited email address, then reopen this link."
              : effectiveStatus === "unavailable"
                ? "Team invitation authority is temporarily unavailable."
                : null;

  return (
    <div className="min-h-screen bg-[linear-gradient(180deg,#f6efe7_0%,#efe5da_100%)] px-6 py-10 text-[color:#2f2a24] dark:bg-[linear-gradient(180deg,#171411_0%,#221c17_100%)] dark:text-[color:#f1e9de]">
      <div className="mx-auto flex min-h-[calc(100vh-5rem)] max-w-[560px] items-center justify-center">
        <div className="w-full rounded-[32px] border border-[rgba(120,104,89,0.14)] bg-[rgba(255,252,247,0.82)] p-8 shadow-[0_28px_80px_rgba(61,44,32,0.12)] backdrop-blur dark:border-[rgba(255,255,255,0.08)] dark:bg-[rgba(33,28,24,0.82)]">
          <div className="space-y-3 text-center">
            <div className="text-[0.72rem] font-medium uppercase tracking-[0.22em] text-[rgba(120,104,89,0.7)] dark:text-[rgba(214,201,189,0.64)]">
              Team invite
            </div>
            <h1 className="text-[1.7rem] font-medium tracking-[-0.03em]">
              {effectiveStatus === "loading"
                ? "Loading invite"
                : invite
                  ? "Cuevion team invite"
                  : "Invite unavailable"}
            </h1>
            <p className="text-[0.96rem] leading-7 text-[rgba(88,80,71,0.84)] dark:text-[rgba(222,211,200,0.76)]">
              {invite
                ? `${invite.inviteeName} was invited to collaboration-only Team access.`
                : "This invite could not be opened."}
            </p>
          </div>

          <div className="mt-8 space-y-3">
            {invite ? (
              <div className="rounded-[20px] border border-[rgba(120,104,89,0.14)] bg-[rgba(255,255,255,0.52)] px-4 py-4 text-[0.9rem] leading-7 text-[rgba(88,80,71,0.86)] dark:border-[rgba(255,255,255,0.08)] dark:bg-[rgba(44,38,33,0.7)] dark:text-[rgba(222,211,200,0.76)]">
                <div>Status: {inviteStatusLabel}</div>
                <div>Access: {invite.accessLevel}</div>
                {sessionStatus === "authenticated" && sessionUser ? (
                  <div>Signed in as: {sessionUser.email}</div>
                ) : null}
              </div>
            ) : null}
            {stateMessage || error ? (
              <div className="text-[0.84rem] leading-6 text-[rgba(132,77,63,0.94)] dark:text-[rgba(244,186,168,0.84)]">
                {stateMessage ?? error}
              </div>
            ) : null}
          </div>

          {invite?.status === "pending" &&
          effectiveStatus === "ready" &&
          sessionStatus === "authenticated" &&
          sessionUser ? (
            <div className="mt-6 flex justify-end gap-3">
              <button
                type="button"
                disabled={status === "updating"}
                onClick={() => void handleInviteAction("decline")}
                className="inline-flex h-10 items-center justify-center rounded-full border border-[rgba(120,104,89,0.16)] bg-[rgba(255,255,255,0.72)] px-5 text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[rgba(88,80,71,0.78)] transition-[background-color,border-color,transform] duration-150 hover:border-[rgba(120,104,89,0.26)] hover:bg-[rgba(255,255,255,0.92)] active:scale-[0.99] focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-60 dark:border-[rgba(255,255,255,0.08)] dark:bg-[rgba(44,38,33,0.86)] dark:text-[rgba(222,211,200,0.76)]"
              >
                Decline
              </button>
              <button
                type="button"
                disabled={status === "updating"}
                onClick={() => void handleInviteAction("accept")}
                className={premiumAccessButtonClass}
              >
                Accept
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function OnboardingPreviewRoute({ onExit }: { onExit: () => void }) {
  const [previewRunId, setPreviewRunId] = useState(0);
  const [previewStep, setPreviewStep] = useState(ONBOARDING_STEP_MIN);
  const [previewState, setPreviewState] = useState<OnboardingState>(() =>
    createPreviewOnboardingState(),
  );
  const [isComplete, setIsComplete] = useState(false);

  const restartPreview = () => {
    setPreviewStep(ONBOARDING_STEP_MIN);
    setPreviewState(createPreviewOnboardingState());
    setIsComplete(false);
    setPreviewRunId((current) => current + 1);
  };
  const previewControls = (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-[18px] border border-[rgba(38,66,56,0.13)] bg-[rgba(255,252,247,0.88)] px-4 py-3 text-[color:#2f2a24] shadow-[0_14px_36px_rgba(61,44,32,0.10)] backdrop-blur dark:border-[rgba(255,255,255,0.08)] dark:bg-[rgba(33,28,24,0.86)] dark:text-[color:#f1e9de]">
      <div className="flex items-center gap-3">
        <span className="inline-flex h-2.5 w-2.5 rounded-full bg-[rgba(52,115,88,0.92)] shadow-[0_0_0_4px_rgba(52,115,88,0.12)]" />
        <div>
          <div className="text-[0.72rem] font-semibold uppercase tracking-[0.18em] text-[rgba(38,66,56,0.72)] dark:text-[rgba(222,211,200,0.72)]">
            Preview mode
          </div>
          <div className="text-sm text-[rgba(88,80,71,0.72)] dark:text-[rgba(222,211,200,0.66)]">
            Review-only onboarding. Workspace, inbox, and user settings are not saved.
          </div>
        </div>
      </div>
      <button
        type="button"
        onClick={onExit}
        className="inline-flex h-9 items-center justify-center rounded-full border border-[rgba(38,66,56,0.16)] bg-white/72 px-4 text-[0.72rem] font-semibold uppercase tracking-[0.14em] text-[rgba(38,66,56,0.82)] transition hover:border-[rgba(38,66,56,0.28)] hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(52,115,88,0.28)] dark:border-[rgba(255,255,255,0.1)] dark:bg-[rgba(44,38,33,0.76)] dark:text-[rgba(241,233,222,0.78)]"
      >
        Exit preview
      </button>
    </div>
  );

  if (isComplete) {
    return (
      <main className="min-h-screen bg-[linear-gradient(180deg,#f6efe7_0%,#efe5da_100%)] px-4 py-8 text-[color:#2f2a24] md:px-8 md:py-10 dark:bg-[linear-gradient(180deg,#171411_0%,#221c17_100%)] dark:text-[color:#f1e9de]">
        <div className="mx-auto max-w-3xl">
          {previewControls}
          <div className="rounded-[32px] border border-[rgba(120,104,89,0.14)] bg-[rgba(255,252,247,0.86)] p-8 text-center shadow-[0_28px_80px_rgba(61,44,32,0.12)] backdrop-blur dark:border-[rgba(255,255,255,0.08)] dark:bg-[rgba(33,28,24,0.82)]">
            <div className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-full border border-[rgba(52,115,88,0.18)] bg-[rgba(226,236,229,0.78)] text-[rgba(38,66,56,0.82)]">
              OK
            </div>
            <div className="text-[0.72rem] font-semibold uppercase tracking-[0.2em] text-[rgba(120,104,89,0.7)] dark:text-[rgba(214,201,189,0.64)]">
              Preview complete
            </div>
            <h1 className="mt-3 text-[2rem] font-semibold tracking-[-0.04em]">
              Onboarding review finished
            </h1>
            <p className="mx-auto mt-3 max-w-xl text-[0.98rem] leading-7 text-[rgba(88,80,71,0.78)] dark:text-[rgba(222,211,200,0.7)]">
              This preview did not open the workspace or save onboarding, inbox, mailbox, or user configuration.
            </p>
            <div className="mt-7 flex flex-wrap justify-center gap-3">
              <button
                type="button"
                onClick={restartPreview}
                className="inline-flex h-10 items-center justify-center rounded-full border border-[rgba(38,66,56,0.14)] bg-white/74 px-5 text-[0.74rem] font-semibold uppercase tracking-[0.14em] text-[rgba(38,66,56,0.82)] transition hover:border-[rgba(38,66,56,0.26)] hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(52,115,88,0.28)] dark:border-[rgba(255,255,255,0.1)] dark:bg-[rgba(44,38,33,0.76)] dark:text-[rgba(241,233,222,0.78)]"
              >
                Restart preview
              </button>
              <button
                type="button"
                onClick={onExit}
                className={premiumAccessButtonClass}
              >
                Exit preview
              </button>
            </div>
          </div>
        </div>
      </main>
    );
  }

  return (
    <OnboardingFlow
      key={previewRunId}
      state={previewState}
      currentStep={previewStep}
      onStepChange={setPreviewStep}
      onStateChange={setPreviewState}
      onSafeStateChange={setPreviewState}
      onOpenWorkspace={() => setIsComplete(true)}
      canOpenWorkspace
      previewControls={previewControls}
      isPreviewMode
    />
  );
}

function CuevionApp() {
  const shouldShowLandingPage = isPublicLandingHost();

  if (shouldShowLandingPage) {
    return <ComingSoonLanding />;
  }

  const workspaceDataMode = resolveWorkspaceDataMode();
  const [initialAccountState] = useState(createCleanAccountConfigStartupState);
  const [persistedOnboardingSession, setPersistedOnboardingSession] =
    useState<PersistedOnboardingSession | null>(
      initialAccountState.persistedOnboardingSession,
    );
  const [view, setView] = useState<AppView>(initialAccountState.view);
  const [sessionUser, setSessionUser] = useState<AuthenticatedCuevionUser | null>(null);
  const memberSessionProbeRef = useRef<ReturnType<typeof loadStartupSession> | null>(null);
  const [displayNameOverrides, setDisplayNameOverrides] = useState<DisplayNameOverrideStore>(() =>
    parseDisplayNameOverrides(),
  );
  const [authoritativeManagedInboxes, setAuthoritativeManagedInboxes] =
    useState<StoredManagedWorkspaceInbox[]>(
      initialAccountState.authoritativeManagedInboxes,
    );
  const [sessionStatus, setSessionStatus] = useState<MemberSessionStatus>("loading");
  const [accountConfigHydrationStatus, setAccountConfigHydrationStatus] =
    useState<AccountConfigHydrationStatus>("idle");
  const [accountConfigErrorStatus, setAccountConfigErrorStatus] =
    useState<RetryableAccountConfigStatus | null>(null);
  const [hydratedMemberAccountKey, setHydratedMemberAccountKey] = useState("");
  const [accountConfigRetryVersion, setAccountConfigRetryVersion] = useState(0);
  const [accountConfigMutationVersion, setAccountConfigMutationVersion] = useState(0);
  const [workspaceCompletionStatus, setWorkspaceCompletionStatus] =
    useState<WorkspaceCompletionStatus>("idle");
  const missingAccountConfigNeedsCleanMirrorRef = useRef(false);
  const pendingResetSaveAccountKeyRef = useRef<string | null>(null);
  const localOnboardingHydratedScopeRef = useRef<string | null>(null);
  const accountConfigHydratorRef = useRef<
    ReturnType<typeof accountConfigOrchestration.createHydrator> | null
  >(null);
  if (!accountConfigHydratorRef.current) {
    accountConfigHydratorRef.current = accountConfigOrchestration.createHydrator();
  }
  const accountConfigSaveQueueRef = useRef<
    ReturnType<typeof accountConfigOrchestration.createSaveQueue> | null
  >(null);
  const workspaceCompletionAttemptRef = useRef<{
    identity: string;
    controller: AbortController;
  } | null>(null);
  const activeMemberCompletionIdentityRef = useRef("");
  if (!accountConfigSaveQueueRef.current) {
    accountConfigSaveQueueRef.current = accountConfigOrchestration.createSaveQueue({
      onClean: ({ accountKey }) => {
        if (pendingResetSaveAccountKeyRef.current === accountKey) {
          clearOnboardingResetQueryParam();
          pendingResetSaveAccountKeyRef.current = null;
        }
      },
    });
  }
  useEffect(
    () => () => {
      accountConfigSaveQueueRef.current?.cancel();
      workspaceCompletionAttemptRef.current?.controller.abort();
      workspaceCompletionAttemptRef.current = null;
    },
    [],
  );
  const [collaborationUser, setCollaborationUser] = useState<AuthenticatedCuevionUser | null>(
    () => {
      const storedAuthUser = window.localStorage.getItem(CUEVION_AUTH_STORAGE_KEY);

      if (!storedAuthUser) {
        return null;
      }

      try {
        return normalizeCollaborationUser(JSON.parse(storedAuthUser));
      } catch {
        return null;
      }
    },
  );
  const [collaborationInviteRoute, setCollaborationInviteRoute] =
    useState<CollaborationInviteRoute | null>(() => parseCollaborationInviteRoute());
  const [teamInviteRoute, setTeamInviteRoute] = useState<TeamInviteRoute | null>(() =>
    parseTeamInviteRoute(),
  );
  const [onboardingState, setOnboardingState] = useState<OnboardingState>(
    initialAccountState.onboardingState,
  );
  const [onboardingStep, setOnboardingStep] = useState(
    initialAccountState.onboardingStep,
  );
  const [userConfig, setUserConfig] = useState<UserConfig | null>(
    initialAccountState.userConfig,
  );
  const onboardingStateRef = useRef(onboardingState);
  const onboardingStepRef = useRef(onboardingStep);
  const activeHydratedMemberAccountKeyRef = useRef("");
  const localOnboardingEphemeralIdentityCounterRef = useRef(0);
  const localOnboardingEphemeralIdentityRef = useRef<{
    user: AuthenticatedCuevionUser | null;
    identity: string;
  }>({ user: null, identity: "" });
  const recognizedInviteUsers = resolveWorkspaceInviteUsers(onboardingState);
  const sessionAccountStorageKey = getSessionAccountStorageKey(
    "auth0",
    sessionUser?.userType === "member" ? sessionUser : null,
  );
  const activeCollaborationUser = collaborationUser ?? sessionUser;
  const localOnboardingIdentityUser = collaborationInviteRoute
    ? activeCollaborationUser
    : sessionUser;
  if (
    localOnboardingEphemeralIdentityRef.current.user !==
    localOnboardingIdentityUser
  ) {
    localOnboardingEphemeralIdentityCounterRef.current += 1;
    localOnboardingEphemeralIdentityRef.current = {
      user: localOnboardingIdentityUser,
      identity: `local-session-${localOnboardingEphemeralIdentityCounterRef.current}`,
    };
  }
  const localOnboardingIdentityScope =
    accountConfigOrchestration.resolveLocalOnboardingIdentityScope({
      userType: localOnboardingIdentityUser?.userType ?? null,
      userEmail: localOnboardingIdentityUser?.email ?? null,
      ephemeralIdentity:
        localOnboardingIdentityUser?.userId ??
        localOnboardingEphemeralIdentityRef.current.identity,
      collaborationInvite: collaborationInviteRoute,
      teamInviteToken: teamInviteRoute?.inviteToken ?? null,
    });
  const hasHydratedCurrentLocalOnboardingScope = Boolean(
    localOnboardingIdentityScope &&
      localOnboardingHydratedScopeRef.current ===
        localOnboardingIdentityScope.hydrationKey,
  );
  const hasHydratedCurrentMemberAccount =
    sessionUser?.userType === "member" &&
    Boolean(sessionAccountStorageKey) &&
    hydratedMemberAccountKey === sessionAccountStorageKey &&
    canUseHydratedLocalAccountState(
      sessionStatus,
      accountConfigHydrationStatus,
    );
  activeHydratedMemberAccountKeyRef.current = hasHydratedCurrentMemberAccount
    ? sessionAccountStorageKey
    : "";
  activeMemberCompletionIdentityRef.current = hasHydratedCurrentMemberAccount
    ? `${sessionAccountStorageKey}:${sessionUser.email.trim().toLowerCase()}`
    : "";
  const canProcessLocalOAuthCallback =
    Boolean(collaborationInviteRoute || teamInviteRoute) ||
    hasHydratedCurrentMemberAccount;
  const canPersistLocalAccountState =
    Boolean(collaborationInviteRoute || teamInviteRoute) ||
    (accountConfigSaveQueueRef.current?.isDirty(sessionAccountStorageKey) === true &&
      hasHydratedCurrentMemberAccount);
  const markAccountConfigDirty = useCallback((accountKey: string) => {
    const nextRevision = accountConfigSaveQueueRef.current?.markDirty(accountKey);
    if (nextRevision !== null && nextRevision !== undefined) {
      setAccountConfigMutationVersion(nextRevision);
    }
  }, []);
  const resetAccountConfigSaveState = useCallback((
    accountKey: string,
    updateMutationVersion = true,
  ) => {
    pendingResetSaveAccountKeyRef.current = null;
    if (updateMutationVersion) {
      setAccountConfigMutationVersion(0);
    }
    accountConfigSaveQueueRef.current?.reset(accountKey);
  }, []);
  const registerAccountConfigMutation = useCallback(() => {
    if (
      sessionStatus !== "authenticated" ||
      sessionUser?.userType !== "member" ||
      accountConfigHydrationStatus !== "ready" ||
      !sessionAccountStorageKey ||
      hydratedMemberAccountKey !== sessionAccountStorageKey
    ) {
      return false;
    }

    if (missingAccountConfigNeedsCleanMirrorRef.current) {
      prepareMissingAccountConfigForFirstExplicitSave({
        accountStorageOwnerKey: sessionAccountStorageKey,
        storage: window.localStorage,
      });
      missingAccountConfigNeedsCleanMirrorRef.current = false;
    }

    markAccountConfigDirty(sessionAccountStorageKey);
    return true;
  }, [
    accountConfigHydrationStatus,
    hydratedMemberAccountKey,
    markAccountConfigDirty,
    sessionAccountStorageKey,
    sessionStatus,
    sessionUser,
  ]);
  const applyAccountConfigMutation = useCallback((
    mutation: (canPersistLocally: boolean) => void,
  ) => {
    const isMemberMutation = registerAccountConfigMutation();
    mutation(
      isMemberMutation || Boolean(collaborationInviteRoute || teamInviteRoute),
    );
  }, [
    collaborationInviteRoute,
    registerAccountConfigMutation,
    teamInviteRoute,
  ]);
  const reloadCustomImapAccountConfig = useCallback(
    async (
      snapshot: CustomImapOnboardingAttemptSnapshot,
      signal: AbortSignal,
    ): Promise<CustomImapOnboardingReconciliationResult> => {
      const accountKey = activeHydratedMemberAccountKeyRef.current;
      if (!accountKey) {
        return { status: "required" };
      }

      const result = await loadUserAccountConfig(signal);
      if (activeHydratedMemberAccountKeyRef.current !== accountKey) {
        return { status: "required" };
      }

      return resolveCustomImapAccountConfigReadback(
        onboardingStateRef.current,
        snapshot,
        result,
      );
    },
    [],
  );
  const completeMemberOnboarding = useCallback(async () => {
    if (workspaceCompletionAttemptRef.current) {
      return;
    }
    const accountKey = activeHydratedMemberAccountKeyRef.current;
    const ownerEmail = sessionUser?.email.trim().toLowerCase() ?? "";
    const identity = activeMemberCompletionIdentityRef.current;
    if (
      sessionUser?.userType !== "member" ||
      !accountKey ||
      !ownerEmail ||
      !identity
    ) {
      return;
    }

    const controller = new AbortController();
    workspaceCompletionAttemptRef.current = { identity, controller };
    setWorkspaceCompletionStatus("completing");
    const isActive = () =>
      workspaceCompletionAttemptRef.current?.identity === identity &&
      activeMemberCompletionIdentityRef.current === identity;
    const outcome =
      await accountConfigOrchestration.completeMemberOnboarding({
        accountKey,
        ownerEmail,
        signal: controller.signal,
        prepareForCompletion: (currentAccountKey) =>
          accountConfigSaveQueueRef.current?.prepareForCompletion(
            currentAccountKey,
          ) === true,
        isActive,
      });

    if (!isActive()) {
      return;
    }
    workspaceCompletionAttemptRef.current = null;
    if (outcome.status === "session_changed") {
      setWorkspaceCompletionStatus("idle");
      return;
    }
    if (outcome.status === "unauthorized") {
      setWorkspaceCompletionStatus("idle");
      setSessionUser(null);
      setSessionStatus("unauthenticated");
      return;
    }
    if (outcome.status !== "ready") {
      setWorkspaceCompletionStatus(outcome.status);
      return;
    }

    try {
      writeFoundAccountConfigToLocalStorage(
        outcome.config,
        accountKey,
        window.localStorage,
      );
    } catch {
      // The server readback remains authoritative; refresh can rehydrate storage.
    }
    setDisplayNameOverrides(
      outcome.config.displayNameOverrides &&
        typeof outcome.config.displayNameOverrides === "object"
        ? outcome.config.displayNameOverrides
        : {},
    );
    setPersistedOnboardingSession(outcome.session);
    onboardingStateRef.current = outcome.onboardingState;
    setOnboardingState(outcome.onboardingState);
    onboardingStepRef.current = ONBOARDING_STEP_MAX;
    setOnboardingStep(ONBOARDING_STEP_MAX);
    setUserConfig(buildUserConfig(outcome.onboardingState));
    setWorkspaceCompletionStatus("idle");
    setView("transition");
  }, [sessionUser]);

  useEffect(() => {
    const activeAttempt = workspaceCompletionAttemptRef.current;
    if (
      activeAttempt &&
      activeAttempt.identity !== activeMemberCompletionIdentityRef.current
    ) {
      activeAttempt.controller.abort();
      workspaceCompletionAttemptRef.current = null;
      setWorkspaceCompletionStatus("idle");
    }
  }, [sessionAccountStorageKey, sessionStatus, sessionUser]);

  useEffect(() => {
    if (collaborationUser) {
      window.localStorage.setItem(
        CUEVION_AUTH_STORAGE_KEY,
        JSON.stringify(collaborationUser),
      );
      return;
    }

    window.localStorage.removeItem(CUEVION_AUTH_STORAGE_KEY);
  }, [collaborationUser]);

  useEffect(() => {
    if (!canPersistLocalAccountState) {
      return;
    }

    window.localStorage.setItem(
      CUEVION_DISPLAY_NAME_OVERRIDES_STORAGE_KEY,
      JSON.stringify(displayNameOverrides),
    );
  }, [canPersistLocalAccountState, displayNameOverrides]);

  useEffect(() => {
    if (
      accountConfigHydrationStatus !== "ready" ||
      sessionUser?.userType !== "member" ||
      !sessionAccountStorageKey ||
      accountConfigSaveQueueRef.current?.isDirty(sessionAccountStorageKey) !== true ||
      accountConfigMutationVersion < 1
    ) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      if (!accountConfigSaveQueueRef.current?.isDirty(sessionAccountStorageKey)) {
        return;
      }

      accountConfigSaveQueueRef.current?.enqueue({
        accountKey: sessionAccountStorageKey,
        config: buildAuthoritativeAccountConfigFromLocalStorage(
          sessionAccountStorageKey,
          persistedOnboardingSession,
          displayNameOverrides,
        ),
      });
    }, 700);

    return () => window.clearTimeout(timeoutId);
  }, [
    accountConfigHydrationStatus,
    accountConfigMutationVersion,
    sessionUser,
    sessionAccountStorageKey,
    displayNameOverrides,
    persistedOnboardingSession,
  ]);

  useEffect(() => {
    if (!hasHydratedCurrentMemberAccount || !sessionAccountStorageKey) {
      return;
    }

    const retryDirtyAccountConfig = () => {
      if (accountConfigSaveQueueRef.current?.isDirty(sessionAccountStorageKey)) {
        setAccountConfigMutationVersion((current) => current + 1);
      }
    };

    window.addEventListener("online", retryDirtyAccountConfig);
    return () => window.removeEventListener("online", retryDirtyAccountConfig);
  }, [hasHydratedCurrentMemberAccount, sessionAccountStorageKey]);

  useEffect(() => {
    if (
      !localOnboardingIdentityScope?.storageKeys ||
      localOnboardingHydratedScopeRef.current !==
        localOnboardingIdentityScope.hydrationKey ||
      view !== "onboarding" ||
      !persistedOnboardingSession ||
      persistedOnboardingSession.completed
    ) {
      return;
    }

    accountConfigOrchestration.writeOnboardingSessionMirror(
      persistedOnboardingSession,
      window.localStorage,
      localOnboardingIdentityScope.storageKeys,
    );
  }, [
    localOnboardingIdentityScope,
    persistedOnboardingSession,
    view,
  ]);

  useEffect(() => {
    const nextInviteRoute = parseCollaborationInviteRoute();
    const nextTeamInviteRoute = parseTeamInviteRoute();
    setCollaborationInviteRoute(nextInviteRoute);
    setTeamInviteRoute(nextTeamInviteRoute);

    if (nextInviteRoute) {
      window.localStorage.setItem(
        PENDING_COLLAB_INVITE_STORAGE_KEY,
        JSON.stringify(nextInviteRoute),
      );
      window.localStorage.setItem(
        PENDING_COLLAB_INVITE_URL_STORAGE_KEY,
        getCurrentInviteUrl(),
      );
      return;
    }

    window.localStorage.removeItem(PENDING_COLLAB_INVITE_STORAGE_KEY);
    window.localStorage.removeItem(PENDING_COLLAB_INVITE_URL_STORAGE_KEY);
  }, []);

  useEffect(() => {
    const handlePopState = () => {
      setCollaborationInviteRoute(parseCollaborationInviteRoute());
      setTeamInviteRoute(parseTeamInviteRoute());
    };

    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  useEffect(() => {
    if (collaborationInviteRoute) {
      setSessionStatus("unauthenticated");
      setSessionUser(null);
      setAccountConfigHydrationStatus("ready");
      return;
    }

    let cancelled = false;

    const loadSession = async () => {
      setSessionStatus("loading");

      try {
        const startupResult = await (
          memberSessionProbeRef.current ??= loadStartupSession()
        );

        if (cancelled) {
          return;
        }

        if (startupResult.status !== "authenticated") {
          setSessionUser(null);
          setSessionStatus(startupResult.status);
          return;
        }

        setSessionUser(startupResult.user);
        setSessionStatus("authenticated");
      } catch {
        if (cancelled) {
          return;
        }

        setSessionUser(null);
        setSessionStatus("unavailable");
      }
    };

    void loadSession();

    return () => {
      cancelled = true;
    };
  }, [collaborationInviteRoute, teamInviteRoute]);

  useEffect(() => {
    const sessionDisposition = accountConfigOrchestration.resolveSessionDisposition({
      hasInviteRoute: Boolean(collaborationInviteRoute || teamInviteRoute),
      sessionStatus,
      userType: sessionUser?.userType ?? null,
      accountStorageKey: sessionAccountStorageKey,
    });

    if (sessionDisposition === "invite" || sessionDisposition === "guest") {
      accountConfigHydratorRef.current?.cancel();
      setUserAccountConfigHydrationEchoExpectation(null, null);
      resetAccountConfigSaveState("");
      setHydratedMemberAccountKey("");
      setAuthoritativeManagedInboxes([]);
      missingAccountConfigNeedsCleanMirrorRef.current = false;
      setAccountConfigErrorStatus(null);
      setAccountConfigHydrationStatus("ready");

      if (
        localOnboardingIdentityScope &&
        localOnboardingHydratedScopeRef.current !==
          localOnboardingIdentityScope.hydrationKey
      ) {
        const localState =
          accountConfigOrchestration.hydrateLocalOnboardingScope(
            localOnboardingIdentityScope,
            window.localStorage,
          );
        localOnboardingHydratedScopeRef.current =
          localOnboardingIdentityScope.hydrationKey;
        setPersistedOnboardingSession(localState.persistedOnboardingSession);
        onboardingStateRef.current = localState.onboardingState;
        setOnboardingState(localState.onboardingState);
        onboardingStepRef.current = localState.onboardingStep;
        setOnboardingStep(localState.onboardingStep);
        setUserConfig(localState.userConfig);
        setView(localState.view);
      }
      return;
    }

    if (sessionDisposition !== "member" || !sessionUser) {
      localOnboardingHydratedScopeRef.current = null;
      accountConfigHydratorRef.current?.cancel();
      setUserAccountConfigHydrationEchoExpectation(null, null);
      resetAccountConfigSaveState("");
      setHydratedMemberAccountKey("");
      setAuthoritativeManagedInboxes([]);
      missingAccountConfigNeedsCleanMirrorRef.current = false;
      setAccountConfigErrorStatus(null);
      setAccountConfigHydrationStatus("idle");
      return;
    }

    const hydrateAccountConfig = async () => {
      localOnboardingHydratedScopeRef.current = null;
      resetAccountConfigSaveState(sessionAccountStorageKey);
      setHydratedMemberAccountKey("");
      missingAccountConfigNeedsCleanMirrorRef.current = false;
      setUserAccountConfigHydrationEchoExpectation(null, null);
      setUserAccountConfigHydrationEchoExpectation(
        sessionAccountStorageKey,
        null,
      );
      setAccountConfigErrorStatus(null);
      setAccountConfigHydrationStatus("loading");
      const outcome = await accountConfigHydratorRef.current!.hydrate({
        accountStorageOwnerKey: sessionAccountStorageKey,
        storage: window.localStorage,
        resetOnboarding: shouldResetOnboardingFromQuery(),
        clearResetQuery: clearOnboardingResetQueryParam,
      });

      if (outcome.status === "cancelled") {
        return;
      }

      if (outcome.status === "found" || outcome.status === "missing") {
        const { accountState } = outcome;
        if (
          outcome.status === "found" &&
          outcome.expectedWorkspaceHydrationEcho
        ) {
          setUserAccountConfigHydrationEchoExpectation(
            sessionAccountStorageKey,
            outcome.expectedWorkspaceHydrationEcho,
          );
        } else {
          setUserAccountConfigHydrationEchoExpectation(
            sessionAccountStorageKey,
            null,
          );
        }
        missingAccountConfigNeedsCleanMirrorRef.current =
          outcome.status === "missing" && !outcome.didResetOnboarding;
        setAccountConfigErrorStatus(null);
        setDisplayNameOverrides(accountState.displayNameOverrides);
        setAuthoritativeManagedInboxes(
          accountState.authoritativeManagedInboxes,
        );
        setPersistedOnboardingSession(accountState.persistedOnboardingSession);
        onboardingStateRef.current = accountState.onboardingState;
        setOnboardingState(accountState.onboardingState);
        onboardingStepRef.current = accountState.onboardingStep;
        setOnboardingStep(accountState.onboardingStep);
        setUserConfig(accountState.userConfig);
        setView(accountState.view);
        setHydratedMemberAccountKey(sessionAccountStorageKey);
        setAccountConfigHydrationStatus("ready");

        if (outcome.status === "found" && outcome.didResetOnboarding) {
          pendingResetSaveAccountKeyRef.current = sessionAccountStorageKey;
          markAccountConfigDirty(sessionAccountStorageKey);
        }
        return;
      }

      setUserAccountConfigHydrationEchoExpectation(null, null);
      if (outcome.status === "unauthorized") {
        setHydratedMemberAccountKey("");
        setAccountConfigErrorStatus(null);
        setAccountConfigHydrationStatus("idle");
        setSessionUser(null);
        setSessionStatus("unauthenticated");
        return;
      }

      setAccountConfigErrorStatus(outcome.errorStatus);
      setHydratedMemberAccountKey("");
      setAccountConfigHydrationStatus("error");
    };

    void hydrateAccountConfig();

    return () => {
      accountConfigHydratorRef.current?.cancel();
      setUserAccountConfigHydrationEchoExpectation(null, null);
      resetAccountConfigSaveState("", false);
    };
  }, [
    sessionStatus,
    sessionUser,
    sessionAccountStorageKey,
    collaborationInviteRoute,
    teamInviteRoute,
    accountConfigRetryVersion,
    localOnboardingIdentityScope?.hydrationKey,
    markAccountConfigDirty,
    resetAccountConfigSaveState,
  ]);

  useEffect(() => {
    processGoogleOAuthCallbackSignal(window.localStorage, () => {
      setAccountConfigRetryVersion((current) => current + 1);
    });
  }, []);

  useEffect(() => {
    if (!canProcessLocalOAuthCallback) {
      return;
    }

    const storedCallbackResult = window.localStorage.getItem(
      OAUTH_CALLBACK_RESULT_STORAGE_KEY,
    );

    if (!storedCallbackResult) {
      return;
    }

    window.localStorage.removeItem(OAUTH_CALLBACK_RESULT_STORAGE_KEY);

    let parsedCallbackResult: MicrosoftOAuthCallbackStorageResult | null = null;
    try {
      parsedCallbackResult = normalizeMicrosoftOAuthCallbackStorageResult(
        JSON.parse(storedCallbackResult),
      );
    } catch {
      parsedCallbackResult = null;
    }

    if (!parsedCallbackResult) {
      return;
    }

    const callbackResult = parsedCallbackResult;
    applyAccountConfigMutation((canPersistLocally) => {
      setOnboardingState((current) => {
        const nextState = applyMicrosoftOAuthCallbackResultToOnboardingState(
          current,
          callbackResult,
        );
        onboardingStateRef.current = nextState;
        return nextState;
      });

      if (!canPersistLocally) {
        return;
      }

      const pendingOAuthManagedInbox = parsePendingOAuthManagedInbox();
      const nextManagedInboxes = applyMicrosoftOAuthCallbackResultToManagedInboxes(
        parseStoredManagedWorkspaceInboxes(),
        callbackResult,
        pendingOAuthManagedInbox,
      );
      window.localStorage.setItem(
        MANAGED_INBOXES_STORAGE_KEY,
        JSON.stringify(sanitizeManagedInboxCredentials(nextManagedInboxes)),
      );
      if (
        pendingOAuthManagedInbox?.provider === callbackResult.provider &&
        pendingOAuthManagedInbox?.email?.trim().toLowerCase() === callbackResult.email
      ) {
        window.localStorage.removeItem(PENDING_OAUTH_MANAGED_INBOX_STORAGE_KEY);
      }
    });
  }, [
    applyAccountConfigMutation,
    canProcessLocalOAuthCallback,
  ]);

  if (
    (teamInviteRoute || collaborationInviteRoute) &&
    !hasHydratedCurrentLocalOnboardingScope
  ) {
    return <WorkspaceLoadingFallback />;
  }

  if (teamInviteRoute) {
    return (
      <TeamInviteRouteView
        key={teamInviteRoute.inviteToken}
        route={teamInviteRoute}
        sessionStatus={sessionStatus}
        sessionUser={sessionUser}
      />
    );
  }

  if (collaborationInviteRoute) {
    if (collaborationInviteRoute.mode === "invite" && !activeCollaborationUser) {
      return (
        <CollaborationInviteAuthGate
          recognizedUsers={recognizedInviteUsers}
          onAuthenticate={(user) => {
            const pendingInvite = window.localStorage.getItem(
              PENDING_COLLAB_INVITE_STORAGE_KEY,
            );
            const pendingInviteUrl = window.localStorage.getItem(
              PENDING_COLLAB_INVITE_URL_STORAGE_KEY,
            );

            if (pendingInviteUrl) {
              window.history.replaceState(null, "", pendingInviteUrl);
            }

            const restoredInviteRoute = parseCollaborationInviteRoute();

            if (restoredInviteRoute) {
              setCollaborationInviteRoute(restoredInviteRoute);
            } else if (pendingInvite) {
              setCollaborationInviteRoute(
                JSON.parse(pendingInvite) as CollaborationInviteRoute,
              );
            }

            setCollaborationUser(user);
            setView("workspace");
          }}
        />
      );
    }

    return (
      <Suspense fallback={<WorkspaceLoadingFallback />}>
        <WorkspaceShell
          userConfig={userConfig ?? buildUserConfig(onboardingState)}
          onboardingState={onboardingState}
          authenticatedUser={
            collaborationInviteRoute.mode === "invite" ? activeCollaborationUser : null
          }
          authenticationContext="collaboration"
          collaborationInviteRoute={collaborationInviteRoute}
          workspaceDataMode={workspaceDataMode}
        />
      </Suspense>
    );
  }

  if (
    sessionStatus === "loading" ||
    (sessionUser &&
      (accountConfigHydrationStatus === "idle" ||
        accountConfigHydrationStatus === "loading" ||
        (sessionUser.userType === "guest" &&
          !hasHydratedCurrentLocalOnboardingScope) ||
        (sessionUser.userType === "member" &&
          accountConfigHydrationStatus === "ready" &&
          hydratedMemberAccountKey !== sessionAccountStorageKey)))
  ) {
    return (
      <div className="min-h-screen bg-[linear-gradient(180deg,#f6efe7_0%,#efe5da_100%)] px-6 py-10 text-[color:#2f2a24] dark:bg-[linear-gradient(180deg,#171411_0%,#221c17_100%)] dark:text-[color:#f1e9de]">
        <div className="mx-auto flex min-h-[calc(100vh-5rem)] max-w-[560px] items-center justify-center text-center">
          <div className="space-y-3">
            <div className="text-[0.72rem] font-medium uppercase tracking-[0.22em] text-[rgba(120,104,89,0.7)] dark:text-[rgba(214,201,189,0.64)]">
              Cuevion
            </div>
            <p className="text-[0.98rem] leading-7 text-[rgba(88,80,71,0.84)] dark:text-[rgba(222,211,200,0.76)]">
              Verifying access…
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (
    sessionUser &&
    accountConfigHydrationStatus === "error" &&
    accountConfigErrorStatus
  ) {
    return (
      <div className="min-h-screen bg-[linear-gradient(180deg,#f6efe7_0%,#efe5da_100%)] px-6 py-10 text-[color:#2f2a24] dark:bg-[linear-gradient(180deg,#171411_0%,#221c17_100%)] dark:text-[color:#f1e9de]">
        <div className="mx-auto flex min-h-[calc(100vh-5rem)] max-w-[560px] items-center justify-center text-center">
          <div className="space-y-5">
            <div className="text-[0.72rem] font-medium uppercase tracking-[0.22em] text-[rgba(120,104,89,0.7)] dark:text-[rgba(214,201,189,0.64)]">
              Cuevion
            </div>
            <div className="space-y-2">
              <h1 className="text-2xl font-semibold tracking-tight">
                Workspace settings are temporarily unavailable
              </h1>
              <p className="text-[0.98rem] leading-7 text-[rgba(88,80,71,0.84)] dark:text-[rgba(222,211,200,0.76)]">
                Your saved setup could not be verified. No local setup was opened or
                uploaded.
              </p>
            </div>
            <button
              type="button"
              onClick={() => {
                setAccountConfigHydrationStatus("loading");
                setAccountConfigRetryVersion((current) => current + 1);
              }}
              className="inline-flex h-11 items-center justify-center rounded-full border border-moss/24 bg-white/80 px-6 text-sm font-semibold text-moss transition hover:border-moss/38 hover:bg-white"
            >
              Retry
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (sessionStatus === "unavailable") {
    return (
      <div className="min-h-screen bg-[linear-gradient(180deg,#f6efe7_0%,#efe5da_100%)] px-6 py-10 text-[color:#2f2a24] dark:bg-[linear-gradient(180deg,#171411_0%,#221c17_100%)] dark:text-[color:#f1e9de]">
        <div className="mx-auto flex min-h-[calc(100vh-5rem)] max-w-[560px] items-center justify-center text-center">
          <div className="space-y-3">
            <div className="text-[0.72rem] font-medium uppercase tracking-[0.22em] text-[rgba(120,104,89,0.7)] dark:text-[rgba(214,201,189,0.64)]">
              Cuevion
            </div>
            <p className="text-[0.98rem] leading-7 text-[rgba(88,80,71,0.84)] dark:text-[rgba(222,211,200,0.76)]">
              Sign-in is temporarily unavailable. Please try again.
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (!sessionUser) {
    return <Auth0LoginView />;
  }

  const canOpenWorkspaceInMemory = canOpenWorkspaceWithoutServerCompletion(
    sessionUser.userType,
  );
  const hasAuthoritativeMemberCompletion =
    sessionUser.userType === "member" &&
    persistedOnboardingSession?.completed === true &&
    persistedOnboardingSession.currentStep === ONBOARDING_STEP_MAX;
  const workspaceCompletionError =
    workspaceCompletionStatus === "inboxes_incomplete"
      ? "One or more inboxes are no longer fully connected. Edit setup to continue."
      : workspaceCompletionStatus === "verification_required"
        ? "Setup may have finished, but we could not verify it. Check status and try again."
        : workspaceCompletionStatus === "error"
          ? "We could not safely finish setup. Your inbox connections were not changed. Try again."
          : null;
  const onboardingHandlers =
    accountConfigOrchestration.createOnboardingHandlers({
      getOnboardingState: () => onboardingStateRef.current,
      getOnboardingStep: () => onboardingStepRef.current,
      commitAccountConfigMutation: (mutation) => {
        applyAccountConfigMutation(() => mutation());
      },
      setOnboardingState: (nextState) => {
        onboardingStateRef.current = nextState;
        setOnboardingState(nextState);
      },
      setOnboardingStep: (nextStep) => {
        onboardingStepRef.current = nextStep;
        setOnboardingStep(nextStep);
      },
      setPersistedOnboardingSession,
      canOpenWorkspace: canOpenWorkspaceInMemory,
      openWorkspace: (nextUserConfig) => {
        setUserConfig(nextUserConfig);
        setView("transition");
      },
    });

  if (view === "workspace" && userConfig) {
    return (
      <Suspense fallback={<WorkspaceLoadingFallback />}>
        <WorkspaceShell
          userConfig={userConfig}
          onboardingState={onboardingState}
          authoritativeManagedInboxes={authoritativeManagedInboxes}
          authenticatedUser={sessionUser}
          authenticationContext="auth0"
          workspaceDataMode={workspaceDataMode}
        />
      </Suspense>
    );
  }

  if (
    view === "transition" &&
    (canOpenWorkspaceInMemory || hasAuthoritativeMemberCompletion)
  ) {
    return (
      <WorkspaceTransition
        connectedInboxIds={
          areSelectedOnboardingInboxesFullyConnected(onboardingState)
            ? [...onboardingState.selectedInboxes]
            : []
        }
        onComplete={() => setView("workspace")}
      />
    );
  }

  return (
    <OnboardingFlow
      state={onboardingState}
      currentStep={onboardingStep}
      onStepChange={onboardingHandlers.onStepChange}
      onStateChange={onboardingHandlers.onStateChange}
      onSafeStateChange={onboardingHandlers.onSafeStateChange}
      onOpenWorkspace={onboardingHandlers.onOpenWorkspace}
      onCompleteWorkspaceSetup={
        sessionUser.userType === "member"
          ? completeMemberOnboarding
          : undefined
      }
      onReloadAccountConfig={reloadCustomImapAccountConfig}
      canOpenWorkspace={canOpenWorkspaceInMemory}
      workspaceCompletionStatus={workspaceCompletionStatus}
      workspaceCompletionError={workspaceCompletionError}
    />
  );
}

export default function App() {
  const [collaborationGuestRoute, setCollaborationGuestRoute] =
    useState<CollaborationGuestRoute | null>(() =>
      resolveSafeCollaborationGuestRoute(),
    );
  const [appRoute, setAppRoute] = useState<RootAppRoute>(() => resolveRootAppRoute());

  useEffect(() => {
    if (!collaborationGuestRoute) {
      scrubManagedInboxBrowserStorage();
    }
  }, [collaborationGuestRoute]);

  useEffect(() => {
    const handleGuestRouteChange = () => {
      setCollaborationGuestRoute(resolveSafeCollaborationGuestRoute());
    };

    window.addEventListener("hashchange", handleGuestRouteChange);
    window.addEventListener("popstate", handleGuestRouteChange);
    return () => {
      window.removeEventListener("hashchange", handleGuestRouteChange);
      window.removeEventListener("popstate", handleGuestRouteChange);
    };
  }, []);

  useEffect(() => {
    const handlePopState = () => {
      setAppRoute(resolveRootAppRoute());
    };

    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  if (collaborationGuestRoute) {
    return (
      <ExternalCollaborationGuestView
        key={collaborationGuestRoute.token ?? "guest-session"}
        initialInviteToken={collaborationGuestRoute.token}
      />
    );
  }

  if (appRoute === "login") {
    return <Auth0LoginView />;
  }

  if (appRoute === "preview") {
    return (
      <OnboardingPreviewRoute
        onExit={() => {
          window.history.replaceState(null, "", "/");
          setAppRoute("app");
        }}
      />
    );
  }

  return <CuevionApp />;
}
