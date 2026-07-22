import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { Auth0LoginView } from "./components/auth/Auth0LoginView";
import { OnboardingFlow } from "./components/onboarding/OnboardingFlow";
import { WorkspaceTransition } from "./components/workspace/WorkspaceTransition";
import { initialOnboardingState } from "./data/onboardingOptions";
import {
  fetchTeamInvite,
  mutateTeamInvite,
  type TeamInvite,
} from "./lib/teamInviteApi";
import {
  loadUserAccountConfig,
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
  ONBOARDING_STEP_MAX,
  ONBOARDING_STEP_MIN,
  clampOnboardingStep,
  normalizeFocusPreferences,
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
  status: "success";
  provider: "google";
  inboxPosition?: string;
  email: string;
  mailboxId: string;
  message: string;
};

type PendingOAuthManagedInbox = {
  id?: string;
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
  persistedOnboardingSession: PersistedOnboardingSession | null;
  onboardingState: OnboardingState;
  onboardingStep: number;
  userConfig: UserConfig | null;
  view: Extract<AppView, "onboarding" | "workspace">;
};

type LocalOnboardingHydrationState = Omit<
  AccountConfigStartupAccountState,
  "displayNameOverrides"
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
};

function createAccountConfigSaveQueue({
  save = saveUserAccountConfig,
  onClean,
}: {
  save?: (config: UserAccountConfig) => Promise<UserAccountConfigSaveResult>;
  onClean?: (saved: {
    accountKey: string;
    revision: number;
    config: UserAccountConfig;
  }) => void;
} = {}) {
  let activeAccountKey = "";
  let generation = 0;
  let isSaving = false;
  let dirty = false;
  let latestRevision = 0;
  let pendingRequest: AccountConfigSaveQueueRequest | null = null;

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
    try {
      const result = await save(request.config);
      success = result.status === "found";
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
    reset(accountKey: string) {
      generation += 1;
      activeAccountKey = accountKey;
      dirty = false;
      latestRevision = 0;
      pendingRequest = null;
    },
    markDirty(accountKey: string) {
      if (!accountKey || accountKey !== activeAccountKey) {
        return null;
      }

      latestRevision += 1;
      dirty = true;
      return latestRevision;
    },
    isDirty(accountKey: string) {
      return accountKey === activeAccountKey && dirty;
    },
    enqueue({
      accountKey,
      config,
    }: Omit<AccountConfigSaveQueueRequest, "revision">) {
      if (
        !accountKey ||
        accountKey !== activeAccountKey ||
        !dirty ||
        latestRevision < 1
      ) {
        return false;
      }

      pendingRequest = {
        accountKey,
        revision: latestRevision,
        config,
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

  return {
    ...normalized,
    inboxConnections: cleanDefaults.inboxConnections,
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
    if (persistSanitizedValue && migrated.rewriteRequired && migrated.serialized) {
      storage.setItem(MANAGED_INBOXES_STORAGE_KEY, migrated.serialized);
    }
    const parsed = migrated.value as StoredManagedWorkspaceInbox[];
    return Array.isArray(parsed)
      ? { status: "valid", value: parsed }
      : { status: "invalid", value: [] };
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
  return sanitizeManagedInboxCredentials(managedInboxes) as StoredManagedWorkspaceInbox[];
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

function projectConnectedManagedInboxesOntoOnboardingState(
  state: OnboardingState,
  managedInboxes: StoredManagedWorkspaceInbox[],
): OnboardingState {
  const selectedPositions = new Set(state.selectedInboxes);
  const nextConnections = { ...state.inboxConnections };
  let didProject = false;

  for (const inboxPosition of selectedPositions) {
    const positionMatches = managedInboxes.filter(
      (mailbox) => mailbox?.onboardingInboxId === inboxPosition,
    );
    if (positionMatches.length !== 1) {
      continue;
    }

    const mailbox = positionMatches[0];
    const mailboxId = typeof mailbox.id === "string" ? mailbox.id.trim() : "";
    const email = typeof mailbox.email === "string" ? mailbox.email.trim().toLowerCase() : "";
    const currentConnection = nextConnections[inboxPosition];
    if (
      !currentConnection ||
      !mailboxId ||
      mailbox.id !== mailboxId ||
      !email ||
      mailbox.email !== email ||
      !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) ||
      mailbox.provider !== "google" ||
      mailbox.connectionMethod !== "oauth" ||
      mailbox.connected !== true ||
      mailbox.connectionStatus !== "connected"
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

    nextConnections[inboxPosition] = {
      ...currentConnection,
      provider: "google",
      email,
      connected: true,
      connectionMethod: "oauth",
      connectionStatus: "connected",
      connectionMessage:
        typeof mailbox.connectionMessage === "string"
          ? mailbox.connectionMessage
          : null,
      oauthAuthorizationUrl: null,
    };
    didProject = true;
  }

  return didProject ? { ...state, inboxConnections: nextConnections } : state;
}

function createCleanAccountConfigStartupState(): AccountConfigStartupAccountState {
  const onboardingState = normalizeOnboardingState(initialOnboardingState);

  return {
    displayNameOverrides: {},
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
  const sanitizedManagedInboxes = sanitizeManagedInboxCredentials(
    managedInboxes,
  ) as StoredManagedWorkspaceInbox[];
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
  consumeGoogleOAuthCallbackSignal,
  processGoogleOAuthCallbackSignal,
  hydrateLocalOnboardingScope: hydrateLocalOnboardingIdentityScope,
  hydrateChoices: buildOnboardingStateFromChoices,
  parseOnboardingSession: parseAccountOnboardingSession,
  projectChoices: projectOnboardingChoices,
  projectConnectedManagedInboxes: projectConnectedManagedInboxesOntoOnboardingState,
  resolveLocalOnboardingIdentityScope,
  resolveSessionDisposition: resolveAccountConfigSessionDisposition,
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
      "inboxPosition",
      "email",
      "mailboxId",
      "message",
    ]) ||
    value.status !== "success" ||
    value.provider !== "google" ||
    typeof value.email !== "string" ||
    typeof value.mailboxId !== "string" ||
    typeof value.message !== "string" ||
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
    status: "success",
    provider: "google",
    ...(value.inboxPosition !== undefined
      ? { inboxPosition: value.inboxPosition }
      : {}),
    email,
    mailboxId,
    message: value.message,
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

function TeamInviteRouteView({ route }: { route: TeamInviteRoute }) {
  const [invite, setInvite] = useState<TeamInvite | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "unavailable" | "updating">(
    "loading",
  );
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
        setStatus("unavailable");
        return;
      }

      setInvite(result.invite);
      setStatus("ready");
    };

    void loadInvite();

    return () => {
      cancelled = true;
    };
  }, [route.inviteToken]);

  const handleInviteAction = async (actionType: "accept" | "decline") => {
    if (!invite || status === "updating") {
      return;
    }

    setStatus("updating");
    setError(null);

    const result = await mutateTeamInvite({
      token: invite.token,
      action: {
        type: actionType,
      },
    });

    if (!result.ok) {
      setError(result.error?.message ?? "Could not update this team invite.");
      setStatus("ready");
      return;
    }

    setInvite(result.invite);
    setStatus("ready");
  };

  const inviteStatusLabel =
    invite?.status === "accepted"
      ? "Accepted"
      : invite?.status === "declined"
        ? "Declined"
        : invite?.status === "cancelled"
          ? "Invite cancelled"
          : "Invited";

  return (
    <div className="min-h-screen bg-[linear-gradient(180deg,#f6efe7_0%,#efe5da_100%)] px-6 py-10 text-[color:#2f2a24] dark:bg-[linear-gradient(180deg,#171411_0%,#221c17_100%)] dark:text-[color:#f1e9de]">
      <div className="mx-auto flex min-h-[calc(100vh-5rem)] max-w-[560px] items-center justify-center">
        <div className="w-full rounded-[32px] border border-[rgba(120,104,89,0.14)] bg-[rgba(255,252,247,0.82)] p-8 shadow-[0_28px_80px_rgba(61,44,32,0.12)] backdrop-blur dark:border-[rgba(255,255,255,0.08)] dark:bg-[rgba(33,28,24,0.82)]">
          <div className="space-y-3 text-center">
            <div className="text-[0.72rem] font-medium uppercase tracking-[0.22em] text-[rgba(120,104,89,0.7)] dark:text-[rgba(214,201,189,0.64)]">
              Team invite
            </div>
            <h1 className="text-[1.7rem] font-medium tracking-[-0.03em]">
              {status === "loading"
                ? "Loading invite"
                : invite
                  ? "Cuevion team invite"
                  : "Invite unavailable"}
            </h1>
            <p className="text-[0.96rem] leading-7 text-[rgba(88,80,71,0.84)] dark:text-[rgba(222,211,200,0.76)]">
              {invite
                ? `${invite.createdByUserName} invited ${invite.inviteeName} to invite-only collaboration access.`
                : "This invite could not be opened."}
            </p>
          </div>

          <div className="mt-8 space-y-3">
            {invite ? (
              <div className="rounded-[20px] border border-[rgba(120,104,89,0.14)] bg-[rgba(255,255,255,0.52)] px-4 py-4 text-[0.9rem] leading-7 text-[rgba(88,80,71,0.86)] dark:border-[rgba(255,255,255,0.08)] dark:bg-[rgba(44,38,33,0.7)] dark:text-[rgba(222,211,200,0.76)]">
                <div>Status: {inviteStatusLabel}</div>
                <div>Email: {invite.inviteeEmail}</div>
                <div>Access: Invite-only</div>
              </div>
            ) : null}
            {error ? (
              <div className="text-[0.84rem] leading-6 text-[rgba(132,77,63,0.94)] dark:text-[rgba(244,186,168,0.84)]">
                {error}
              </div>
            ) : null}
          </div>

          {invite?.status === "invited" ? (
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
  const [sessionStatus, setSessionStatus] = useState<MemberSessionStatus>("loading");
  const [accountConfigHydrationStatus, setAccountConfigHydrationStatus] =
    useState<AccountConfigHydrationStatus>("idle");
  const [accountConfigErrorStatus, setAccountConfigErrorStatus] =
    useState<RetryableAccountConfigStatus | null>(null);
  const [hydratedMemberAccountKey, setHydratedMemberAccountKey] = useState("");
  const [accountConfigRetryVersion, setAccountConfigRetryVersion] = useState(0);
  const [accountConfigMutationVersion, setAccountConfigMutationVersion] = useState(0);
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
    if (collaborationInviteRoute || teamInviteRoute) {
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
    return <TeamInviteRouteView route={teamInviteRoute} />;
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
          authenticatedUser={sessionUser}
          authenticationContext="auth0"
          workspaceDataMode={workspaceDataMode}
        />
      </Suspense>
    );
  }

  if (view === "transition" && canOpenWorkspaceInMemory) {
    return (
      <WorkspaceTransition
        connectedInboxIds={onboardingState.selectedInboxes.filter((inboxId) => {
          const connection = onboardingState.inboxConnections[inboxId];
          return connection?.connected || connection?.connectionStatus === "connected";
        })}
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
      canOpenWorkspace={canOpenWorkspaceInMemory}
    />
  );
}

export default function App() {
  const [appRoute, setAppRoute] = useState<RootAppRoute>(() => resolveRootAppRoute());

  useEffect(() => {
    const handlePopState = () => {
      setAppRoute(resolveRootAppRoute());
    };

    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

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
