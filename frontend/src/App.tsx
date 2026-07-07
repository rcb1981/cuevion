import { useEffect, useState } from "react";
import { OnboardingFlow } from "./components/onboarding/OnboardingFlow";
import { WorkspaceTransition } from "./components/workspace/WorkspaceTransition";
import { WorkspaceShell } from "./components/workspace/WorkspaceShell";
import { initialOnboardingState } from "./data/onboardingOptions";
import {
  fetchTeamInvite,
  mutateTeamInvite,
  type TeamInvite,
} from "./lib/teamInviteApi";
import {
  getUserAccountConfig,
  saveUserAccountConfig,
  type UserAccountConfig,
} from "./lib/userConfigApi";
import {
  normalizeFocusPreferences,
  type OnboardingState,
} from "./types/onboarding";
import type { UserConfig } from "./types/userConfig";

const ONBOARDING_STATE_STORAGE_KEY = "label-inbox-ai-onboarding-state";
const ONBOARDING_DRAFT_STATE_STORAGE_KEY = "label-inbox-ai-onboarding-draft-state";
const CUEVION_APP_VIEW_STORAGE_KEY = "cuevion-app-view";
const CATEGORY_LEARNING_STORAGE_KEY = "cuevion-sender-category-learning";
const MESSAGE_OWNERSHIP_STORAGE_KEY = "cuevion-message-ownership";
const MANAGED_INBOXES_STORAGE_KEY = "cuevion-managed-inboxes";
const PENDING_OAUTH_MANAGED_INBOX_STORAGE_KEY = "cuevion-pending-oauth-managed-inbox";
const CUEVION_AUTH_STORAGE_KEY = "label-inbox-ai-auth-user";
const CUEVION_DISPLAY_NAME_OVERRIDES_STORAGE_KEY = "cuevion-display-name-overrides";
const PENDING_COLLAB_INVITE_STORAGE_KEY = "label-inbox-ai-pending-collab-invite";
const PENDING_COLLAB_INVITE_URL_STORAGE_KEY = "label-inbox-ai-pending-collab-invite-url";
const OAUTH_CALLBACK_RESULT_STORAGE_KEY = "cuevion-oauth-callback-result";
const BETA_SESSION_ENDPOINT = "/api/beta/session";
const BETA_LOGIN_ENDPOINT = "/api/beta/login";
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
};

type BetaAccessRoute = "login" | "app";

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

type PersistedOnboardingSession = {
  completed: true;
  state: OnboardingState;
};
type PersistedOnboardingDraft = {
  state: OnboardingState;
};
type AppView = "onboarding" | "transition" | "workspace";
type WorkspaceDataMode = "demo" | "live";
type StoredManagedWorkspaceInbox = {
  id?: string;
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
type OAuthCallbackStorageResult = {
  provider?: string;
  email?: string;
  displayName?: string;
  connectionMethod?: string;
  connectionStatus?: string;
  connected?: boolean;
  message?: string | null;
};

type PendingOAuthManagedInbox = {
  id?: string;
  title?: string;
  email?: string;
  provider?: string | null;
};

type DisplayNameOverrideStore = Record<string, string>;

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

function isPublicLandingHost() {
  if (typeof window === "undefined") {
    return false;
  }

  const hostname = window.location.hostname.toLowerCase();

  return hostname === "cuevion.com" || hostname === "www.cuevion.com";
}

function resolveBetaAccessRoute(): BetaAccessRoute {
  if (typeof window === "undefined") {
    return "app";
  }

  return "app";
}

function replaceBetaAccessRoute(route: BetaAccessRoute) {
  const nextPath = "/";
  if (window.location.pathname === nextPath) {
    return;
  }

  window.history.replaceState(null, "", `${nextPath}${window.location.search}${window.location.hash}`);
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

function normalizeAuthenticatedUser(value: unknown): AuthenticatedCuevionUser | null {
  if (!value || typeof value !== "object") {
    return null;
  }

  const nextValue = value as Partial<AuthenticatedCuevionUser>;

  if (
    typeof nextValue.email !== "string" ||
    typeof nextValue.name !== "string"
  ) {
    return null;
  }

  return {
    email: nextValue.email.toLowerCase(),
    name: nextValue.name,
    userType: nextValue.userType === "guest" ? "guest" : "member",
  };
}

type BetaAccessGateProps = {
  error: string | null;
  loading: boolean;
  onSubmit: (credentials: {
    name: string;
    email: string;
    inviteCode: string;
  }) => Promise<void>;
};

function BetaAccessGate({
  error,
  loading,
  onSubmit,
}: BetaAccessGateProps) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  return (
    <div className="min-h-screen bg-[linear-gradient(180deg,#f6efe7_0%,#efe5da_100%)] px-6 py-10 text-[color:#2f2a24] dark:bg-[linear-gradient(180deg,#171411_0%,#221c17_100%)] dark:text-[color:#f1e9de]">
      <div className="mx-auto flex min-h-[calc(100vh-5rem)] max-w-[560px] items-center justify-center">
        <div className="w-full rounded-[32px] border border-[rgba(120,104,89,0.14)] bg-[rgba(255,252,247,0.82)] p-8 shadow-[0_28px_80px_rgba(61,44,32,0.12)] backdrop-blur dark:border-[rgba(255,255,255,0.08)] dark:bg-[rgba(33,28,24,0.82)]">
          <div className="space-y-3 text-center">
            <div className="text-[0.72rem] font-medium uppercase tracking-[0.22em] text-[rgba(120,104,89,0.7)] dark:text-[rgba(214,201,189,0.64)]">
              Private beta
            </div>
            <h1 className="text-[1.7rem] font-medium tracking-[-0.03em]">
              Sign in to Cuevion
            </h1>
            <p className="text-[0.96rem] leading-7 text-[rgba(88,80,71,0.84)] dark:text-[rgba(222,211,200,0.76)]">
              Enter your beta invite details to open the active Cuevion app.
            </p>
          </div>

          <div className="mt-8 space-y-3">
            <input
              value={name}
              onChange={(event) => {
                setName(event.target.value);
                setLocalError(null);
              }}
              placeholder="Your name"
              autoCorrect="off"
              spellCheck={false}
              className="w-full rounded-[20px] border border-[rgba(120,104,89,0.16)] bg-[rgba(255,255,255,0.78)] px-4 py-3 text-[0.98rem] leading-7 outline-none placeholder:text-[rgba(120,104,89,0.56)] dark:border-[rgba(255,255,255,0.08)] dark:bg-[rgba(44,38,33,0.86)] dark:placeholder:text-[rgba(210,196,183,0.42)]"
            />
            <input
              value={email}
              onChange={(event) => {
                setEmail(event.target.value);
                setLocalError(null);
              }}
              placeholder="name@company.com"
              autoCorrect="off"
              autoCapitalize="none"
              spellCheck={false}
              className="w-full rounded-[20px] border border-[rgba(120,104,89,0.16)] bg-[rgba(255,255,255,0.78)] px-4 py-3 text-[0.98rem] leading-7 outline-none placeholder:text-[rgba(120,104,89,0.56)] dark:border-[rgba(255,255,255,0.08)] dark:bg-[rgba(44,38,33,0.86)] dark:placeholder:text-[rgba(210,196,183,0.42)]"
            />
            <input
              value={inviteCode}
              onChange={(event) => {
                setInviteCode(event.target.value);
                setLocalError(null);
              }}
              placeholder="Invite code"
              autoCorrect="off"
              autoCapitalize="none"
              spellCheck={false}
              className="w-full rounded-[20px] border border-[rgba(120,104,89,0.16)] bg-[rgba(255,255,255,0.78)] px-4 py-3 text-[0.98rem] leading-7 outline-none placeholder:text-[rgba(120,104,89,0.56)] dark:border-[rgba(255,255,255,0.08)] dark:bg-[rgba(44,38,33,0.86)] dark:placeholder:text-[rgba(210,196,183,0.42)]"
            />
            {localError || error ? (
              <div className="text-[0.84rem] leading-6 text-[rgba(132,77,63,0.94)] dark:text-[rgba(244,186,168,0.84)]">
                {localError ?? error}
              </div>
            ) : null}
          </div>

          <div className="mt-6 flex justify-end">
            <button
              type="button"
              disabled={loading}
              onClick={() => {
                const trimmedName = name.trim();
                const normalizedEmail = email.trim().toLowerCase();
                const trimmedInviteCode = inviteCode.trim();

                if (!trimmedName) {
                  setLocalError("Enter your name to continue.");
                  return;
                }

                if (!isValidAuthEmail(normalizedEmail)) {
                  setLocalError("Enter a valid email address to continue.");
                  return;
                }

                if (!trimmedInviteCode) {
                  setLocalError("Enter your invite code to continue.");
                  return;
                }

                void onSubmit({
                  name: trimmedName,
                  email: normalizedEmail,
                  inviteCode: trimmedInviteCode,
                });
              }}
              className={premiumAccessButtonClass}
            >
              {loading ? "Checking access..." : "Open beta"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
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

function clearOnboardingDependentWorkspaceState() {
  window.localStorage.removeItem(CATEGORY_LEARNING_STORAGE_KEY);
  window.localStorage.removeItem(MESSAGE_OWNERSHIP_STORAGE_KEY);
  window.localStorage.removeItem(MANAGED_INBOXES_STORAGE_KEY);
  window.localStorage.removeItem(ONBOARDING_DRAFT_STATE_STORAGE_KEY);
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

function parsePersistedOnboardingSession(): PersistedOnboardingSession | null {
  const storedState = window.localStorage.getItem(ONBOARDING_STATE_STORAGE_KEY);

  if (!storedState) {
    return null;
  }

  try {
    const parsed = JSON.parse(storedState) as Partial<PersistedOnboardingSession>;

    if (!parsed || parsed.completed !== true || !parsed.state) {
      window.localStorage.removeItem(ONBOARDING_STATE_STORAGE_KEY);
      return null;
    }

    return {
      completed: true,
      state: normalizeOnboardingState(parsed.state),
    };
  } catch {
    window.localStorage.removeItem(ONBOARDING_STATE_STORAGE_KEY);
    return null;
  }
}

function parsePersistedOnboardingDraft(): PersistedOnboardingDraft | null {
  const storedState = window.localStorage.getItem(ONBOARDING_DRAFT_STATE_STORAGE_KEY);

  if (!storedState) {
    return null;
  }

  try {
    const parsed = JSON.parse(storedState) as Partial<PersistedOnboardingDraft>;

    if (!parsed || !parsed.state) {
      window.localStorage.removeItem(ONBOARDING_DRAFT_STATE_STORAGE_KEY);
      return null;
    }

    return {
      state: normalizeOnboardingState(parsed.state),
    };
  } catch {
    window.localStorage.removeItem(ONBOARDING_DRAFT_STATE_STORAGE_KEY);
    return null;
  }
}

function parsePersistedAppView(): AppView | null {
  const storedValue = window.localStorage.getItem(CUEVION_APP_VIEW_STORAGE_KEY);

  if (storedValue === "onboarding" || storedValue === "workspace") {
    return storedValue;
  }

  return null;
}

function parseStoredManagedWorkspaceInboxes(): StoredManagedWorkspaceInbox[] {
  const storedValue = window.localStorage.getItem(MANAGED_INBOXES_STORAGE_KEY);

  if (!storedValue) {
    return [];
  }

  try {
    const parsed = JSON.parse(storedValue) as StoredManagedWorkspaceInbox[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
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

function parseStoredJsonValue<T>(storageKey: string, fallback: T): T {
  const storedValue = window.localStorage.getItem(storageKey);

  if (!storedValue) {
    return fallback;
  }

  try {
    return JSON.parse(storedValue) as T;
  } catch {
    return fallback;
  }
}

function sanitizeConnectionForAccountConfig(
  connection: Record<string, unknown>,
): Record<string, unknown> {
  const nextConnection = {
    ...connection,
  };

  if (nextConnection.customImap && typeof nextConnection.customImap === "object") {
    nextConnection.customImap = {
      ...(nextConnection.customImap as Record<string, unknown>),
      password: "",
    };
  }

  if (nextConnection.customSmtp && typeof nextConnection.customSmtp === "object") {
    nextConnection.customSmtp = {
      ...(nextConnection.customSmtp as Record<string, unknown>),
      password: "",
    };
  }

  if ("oauthAuthorizationUrl" in nextConnection) {
    nextConnection.oauthAuthorizationUrl = null;
  }

  return nextConnection;
}

function sanitizeOnboardingStateForAccountConfig(state: OnboardingState): OnboardingState {
  const nextState = normalizeOnboardingState(state);

  return {
    ...nextState,
    inboxConnections: Object.fromEntries(
      Object.entries(nextState.inboxConnections).map(([inboxId, connection]) => [
        inboxId,
        sanitizeConnectionForAccountConfig(connection as unknown as Record<string, unknown>),
      ]),
    ) as unknown as OnboardingState["inboxConnections"],
  };
}

function sanitizeOnboardingSessionForAccountConfig(
  session: PersistedOnboardingSession | null,
): PersistedOnboardingSession | null {
  if (!session) {
    return null;
  }

  return {
    completed: true,
    state: sanitizeOnboardingStateForAccountConfig(session.state),
  };
}

function sanitizeManagedInboxesForAccountConfig(
  managedInboxes: StoredManagedWorkspaceInbox[],
): StoredManagedWorkspaceInbox[] {
  return managedInboxes.map((mailbox) =>
    sanitizeConnectionForAccountConfig(mailbox as Record<string, unknown>),
  ) as StoredManagedWorkspaceInbox[];
}

function isRecordValue(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object";
}

function getStoredManagedInboxEmail(mailbox: StoredManagedWorkspaceInbox) {
  return typeof mailbox.email === "string" ? mailbox.email.trim().toLowerCase() : "";
}

function findMatchingStoredManagedInbox(
  next: StoredManagedWorkspaceInbox,
  savedInboxes: StoredManagedWorkspaceInbox[],
) {
  const nextId = typeof next.id === "string" ? next.id.trim() : "";

  if (nextId) {
    const matchedById = savedInboxes.find(
      (mailbox) => typeof mailbox.id === "string" && mailbox.id.trim() === nextId,
    );

    if (matchedById) {
      return matchedById;
    }
  }

  const nextEmail = getStoredManagedInboxEmail(next);

  if (!nextEmail) {
    return undefined;
  }

  return savedInboxes.find((mailbox) => getStoredManagedInboxEmail(mailbox) === nextEmail);
}

function mergeStoredCredentialFields(
  nextValue: unknown,
  previousValue: unknown,
  preservedStringFields: string[],
) {
  const next = isRecordValue(nextValue) ? { ...nextValue } : {};
  const previous = isRecordValue(previousValue) ? previousValue : {};

  preservedStringFields.forEach((field) => {
    const nextFieldValue = next[field];
    const previousFieldValue = previous[field];

    if (
      (typeof nextFieldValue !== "string" || nextFieldValue.trim().length === 0) &&
      typeof previousFieldValue === "string" &&
      previousFieldValue.trim().length > 0
    ) {
      next[field] = previousFieldValue;
    }
  });

  return next;
}

function mergeStoredManagedInboxCredentials(
  next: StoredManagedWorkspaceInbox,
  previous?: StoredManagedWorkspaceInbox,
): StoredManagedWorkspaceInbox {
  if (!previous || (next.provider && previous.provider && next.provider !== previous.provider)) {
    return next;
  }

  return {
    ...next,
    customImap: mergeStoredCredentialFields(next.customImap, previous.customImap, [
      "password",
    ]),
    customSmtp: mergeStoredCredentialFields(next.customSmtp, previous.customSmtp, [
      "host",
      "port",
      "username",
      "password",
    ]),
  };
}

function formatStoredInboxTitleFromEmail(email: string) {
  const localPart = email.split("@", 1)[0]?.replace(/[._-]+/g, " ").trim();

  if (!localPart) {
    return email;
  }

  return localPart
    .split(/\s+/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function buildStoredGmailManagedInboxId(
  email: string,
  managedInboxes: StoredManagedWorkspaceInbox[],
) {
  const existingIds = new Set(
    managedInboxes
      .map((mailbox) => (typeof mailbox.id === "string" ? mailbox.id.trim() : ""))
      .filter(Boolean),
  );
  const localSlug =
    email
      .split("@", 1)[0]
      ?.toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || "gmail";
  const localCandidate = `gmail-${localSlug}`;

  if (!existingIds.has(localCandidate)) {
    return localCandidate;
  }

  const emailSlug =
    email
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || "gmail";
  let candidate = `gmail-${emailSlug}`;
  let suffix = 2;

  while (existingIds.has(candidate)) {
    candidate = `gmail-${emailSlug}-${suffix}`;
    suffix += 1;
  }

  return candidate;
}

function createStoredGmailManagedInbox(
  callbackResult: OAuthCallbackStorageResult,
  managedInboxes: StoredManagedWorkspaceInbox[],
  pendingMailbox?: PendingOAuthManagedInbox | null,
): StoredManagedWorkspaceInbox {
  const normalizedEmail = callbackResult.email?.trim().toLowerCase() ?? "";
  const pendingTitle =
    pendingMailbox?.provider === "google" &&
    pendingMailbox.email?.trim().toLowerCase() === normalizedEmail
      ? pendingMailbox.title?.trim()
      : "";
  const displayName = callbackResult.displayName?.trim();

  return {
    id: buildStoredGmailManagedInboxId(normalizedEmail, managedInboxes),
    title: pendingTitle || displayName || formatStoredInboxTitleFromEmail(normalizedEmail),
    email: normalizedEmail,
    provider: "google",
    connected:
      callbackResult.connected === true &&
      callbackResult.connectionStatus === "connected",
    connectionMethod: "oauth",
    connectionStatus:
      callbackResult.connectionStatus === "connected"
        ? "connected"
        : callbackResult.connectionStatus === "authenticated_pending_activation"
          ? "authenticated_pending_activation"
          : "connection_failed",
    connectionMessage: callbackResult.message ?? null,
    oauthAuthorizationUrl: null,
    customImap: {
      host: "",
      port: "",
      ssl: true,
      username: "",
      password: "",
    },
    customSmtp: {
      host: "",
      port: "",
      security: "starttls",
      username: "",
      password: "",
      useSameCredentials: true,
    },
  };
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
  ownerEmail: string,
  onboardingSession: PersistedOnboardingSession | null = parsePersistedOnboardingSession(),
  displayNameOverrides: DisplayNameOverrideStore = parseDisplayNameOverrides(),
): UserAccountConfig {
  const managedInboxes = sanitizeManagedInboxesForAccountConfig(
    parseStoredManagedWorkspaceInboxes(),
  );
  const workspaceUserId = normalizeAccountStorageKey(ownerEmail);
  const themeMode =
    normalizeStoredWorkspaceThemeMode(
      window.localStorage.getItem(WORKSPACE_THEME_MODE_STORAGE_KEY),
    ) ?? "Light";

  return {
    v: 1,
    onboardingSession: sanitizeOnboardingSessionForAccountConfig(onboardingSession) ?? {},
    managedInboxes,
    mailboxTitleOverrides: parseStoredJsonValue(MAILBOX_TITLE_OVERRIDES_STORAGE_KEY, {}),
    primaryManagedInboxId:
      window.localStorage.getItem(
        buildPrimaryManagedInboxStorageKey(workspaceUserId, managedInboxes),
      ) ?? null,
    mailboxFocusPreferenceOverrides: parseStoredJsonValue(
      buildMailboxFocusPreferenceOverridesStorageKey(workspaceUserId),
      {},
    ),
    inboxSignatures: parseStoredJsonValue(MAIL_SIGNATURES_STORAGE_KEY, {}),
    smartFolders: parseStoredJsonValue(SMART_FOLDERS_STORAGE_KEY, []),
    uiPreferences: {
      themeMode,
      aiSuggestionsEnabled:
        window.localStorage.getItem(AI_SUGGESTIONS_STORAGE_KEY) !== "false",
      inboxChangesEnabled:
        window.localStorage.getItem(INBOX_CHANGES_STORAGE_KEY) !== "false",
      teamActivityEnabled:
        window.localStorage.getItem(TEAM_ACTIVITY_STORAGE_KEY) !== "false",
    },
    displayNameOverrides,
  };
}

function isPersistedOnboardingSession(value: unknown): value is PersistedOnboardingSession {
  return (
    Boolean(value) &&
    typeof value === "object" &&
    (value as Partial<PersistedOnboardingSession>).completed === true &&
    Boolean((value as Partial<PersistedOnboardingSession>).state)
  );
}

function writeAccountConfigToLocalStorage(config: UserAccountConfig, ownerEmail: string) {
  const onboardingSession = isPersistedOnboardingSession(config.onboardingSession)
    ? {
        completed: true,
        state: sanitizeOnboardingStateForAccountConfig(config.onboardingSession.state),
      }
    : null;
  const locallyStoredManagedInboxes = parseStoredManagedWorkspaceInboxes();
  const managedInboxes =
    Array.isArray(config.managedInboxes) && config.managedInboxes.length > 0
      ? (config.managedInboxes as StoredManagedWorkspaceInbox[])
      : locallyStoredManagedInboxes;
  const mergedManagedInboxes = managedInboxes.map((mailbox) =>
    mergeStoredManagedInboxCredentials(
      mailbox,
      findMatchingStoredManagedInbox(mailbox, locallyStoredManagedInboxes),
    ),
  );
  const workspaceUserId = normalizeAccountStorageKey(ownerEmail);

  if (onboardingSession) {
    window.localStorage.setItem(
      ONBOARDING_STATE_STORAGE_KEY,
      JSON.stringify(onboardingSession),
    );
    window.localStorage.removeItem(ONBOARDING_DRAFT_STATE_STORAGE_KEY);
  }

  window.localStorage.setItem(
    MANAGED_INBOXES_STORAGE_KEY,
    JSON.stringify(mergedManagedInboxes),
  );
  window.localStorage.setItem(
    MAILBOX_TITLE_OVERRIDES_STORAGE_KEY,
    JSON.stringify(config.mailboxTitleOverrides ?? {}),
  );
  window.localStorage.setItem(
    buildMailboxFocusPreferenceOverridesStorageKey(workspaceUserId),
    JSON.stringify(config.mailboxFocusPreferenceOverrides ?? {}),
  );
  window.localStorage.setItem(
    MAIL_SIGNATURES_STORAGE_KEY,
    JSON.stringify(config.inboxSignatures ?? {}),
  );
  window.localStorage.setItem(
    SMART_FOLDERS_STORAGE_KEY,
    JSON.stringify(config.smartFolders ?? []),
  );
  window.localStorage.setItem(
    CUEVION_DISPLAY_NAME_OVERRIDES_STORAGE_KEY,
    JSON.stringify(config.displayNameOverrides ?? {}),
  );

  const primaryStorageKey = buildPrimaryManagedInboxStorageKey(
    workspaceUserId,
    mergedManagedInboxes,
  );
  if (config.primaryManagedInboxId) {
    window.localStorage.setItem(primaryStorageKey, config.primaryManagedInboxId);
  } else {
    window.localStorage.removeItem(primaryStorageKey);
  }

  const uiPreferences = config.uiPreferences ?? {};
  const themeMode = normalizeStoredWorkspaceThemeMode(uiPreferences.themeMode);
  if (themeMode) {
    window.localStorage.setItem(WORKSPACE_THEME_MODE_STORAGE_KEY, themeMode);
  }

  if (typeof uiPreferences.aiSuggestionsEnabled === "boolean") {
    window.localStorage.setItem(
      AI_SUGGESTIONS_STORAGE_KEY,
      String(uiPreferences.aiSuggestionsEnabled),
    );
  }

  if (typeof uiPreferences.inboxChangesEnabled === "boolean") {
    window.localStorage.setItem(
      INBOX_CHANGES_STORAGE_KEY,
      String(uiPreferences.inboxChangesEnabled),
    );
  }

  if (typeof uiPreferences.teamActivityEnabled === "boolean") {
    window.localStorage.setItem(
      TEAM_ACTIVITY_STORAGE_KEY,
      String(uiPreferences.teamActivityEnabled),
    );
  }
}

function normalizeOAuthCallbackStorageResult(
  value: unknown,
): OAuthCallbackStorageResult | null {
  if (!value || typeof value !== "object") {
    return null;
  }

  const result = value as OAuthCallbackStorageResult;

  if (
    (result.provider !== "google" && result.provider !== "microsoft") ||
    typeof result.email !== "string"
  ) {
    return null;
  }

  const provider = result.provider;

  return {
    provider,
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

function applyOAuthCallbackResultToOnboardingState(
  state: OnboardingState,
  callbackResult: OAuthCallbackStorageResult,
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

function applyOAuthCallbackResultToManagedInboxes(
  inboxes: StoredManagedWorkspaceInbox[],
  callbackResult: OAuthCallbackStorageResult,
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
  let didUpdate = false;
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

    didUpdate = true;
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

  if (didUpdate || callbackResult.provider !== "google") {
    return nextInboxes;
  }

  return [
    ...nextInboxes,
    createStoredGmailManagedInbox(callbackResult, nextInboxes, pendingMailbox),
  ];
}

function resolveWorkspaceInviteUsers(
  onboardingState: OnboardingState,
): AuthenticatedCuevionUser[] {
  const managedInboxes = parseStoredManagedWorkspaceInboxes();
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
  const [previewState, setPreviewState] = useState<OnboardingState>(() =>
    createPreviewOnboardingState(),
  );
  const [isComplete, setIsComplete] = useState(false);

  const restartPreview = () => {
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
      onStateChange={setPreviewState}
      onOpenWorkspace={() => setIsComplete(true)}
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
  const [persistedOnboardingSession, setPersistedOnboardingSession] =
    useState<PersistedOnboardingSession | null>(() => {
      if (shouldResetOnboardingFromQuery()) {
        window.localStorage.removeItem(ONBOARDING_STATE_STORAGE_KEY);
        window.localStorage.removeItem(CUEVION_APP_VIEW_STORAGE_KEY);
        clearOnboardingDependentWorkspaceState();
        clearOnboardingResetQueryParam();
        return null;
      }

      return parsePersistedOnboardingSession();
    });
  const [persistedOnboardingDraft] = useState<PersistedOnboardingDraft | null>(() =>
    persistedOnboardingSession ? null : parsePersistedOnboardingDraft(),
  );
  const [view, setView] = useState<AppView>(() =>
    persistedOnboardingSession || parsePersistedAppView() === "workspace"
      ? "workspace"
      : "onboarding",
  );
  const [betaAccessRoute, setBetaAccessRoute] = useState<BetaAccessRoute>(() =>
    resolveBetaAccessRoute(),
  );
  const [betaSessionUser, setBetaSessionUser] = useState<AuthenticatedCuevionUser | null>(null);
  const [displayNameOverrides, setDisplayNameOverrides] = useState<DisplayNameOverrideStore>(() =>
    parseDisplayNameOverrides(),
  );
  const [betaSessionStatus, setBetaSessionStatus] = useState<
    "loading" | "authenticated" | "unauthenticated"
  >("loading");
  const [accountConfigHydrationStatus, setAccountConfigHydrationStatus] = useState<
    "idle" | "loading" | "ready"
  >("idle");
  const [betaLoginError, setBetaLoginError] = useState<string | null>(null);
  const [betaLoginPending, setBetaLoginPending] = useState(false);
  const [authenticatedUser, setAuthenticatedUser] = useState<AuthenticatedCuevionUser | null>(
    () => {
      const storedAuthUser = window.localStorage.getItem(CUEVION_AUTH_STORAGE_KEY);

      if (!storedAuthUser) {
        return null;
      }

      try {
        return normalizeAuthenticatedUser(JSON.parse(storedAuthUser));
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
    () =>
      normalizeOnboardingState(
        persistedOnboardingSession?.state ??
          persistedOnboardingDraft?.state ??
          initialOnboardingState,
      ),
  );
  const [userConfig, setUserConfig] = useState<UserConfig | null>(() =>
    persistedOnboardingSession?.state ? buildUserConfig(persistedOnboardingSession.state) : null,
  );
  const recognizedInviteUsers = resolveWorkspaceInviteUsers(onboardingState);
  const effectiveBetaSessionUser =
    betaSessionUser && betaSessionUser.userType === "member"
      ? {
          ...betaSessionUser,
          name:
            displayNameOverrides[betaSessionUser.email.toLowerCase()]?.trim() ||
            betaSessionUser.name,
        }
      : betaSessionUser;
  const activeCollaborationUser = authenticatedUser ?? effectiveBetaSessionUser;

  useEffect(() => {
    if (authenticatedUser) {
      window.localStorage.setItem(
        CUEVION_AUTH_STORAGE_KEY,
        JSON.stringify(authenticatedUser),
      );
      return;
    }

    window.localStorage.removeItem(CUEVION_AUTH_STORAGE_KEY);
  }, [authenticatedUser]);

  useEffect(() => {
    window.localStorage.setItem(
      CUEVION_DISPLAY_NAME_OVERRIDES_STORAGE_KEY,
      JSON.stringify(displayNameOverrides),
    );
  }, [displayNameOverrides]);

  useEffect(() => {
    if (
      accountConfigHydrationStatus !== "ready" ||
      !betaSessionUser ||
      betaSessionUser.userType !== "member"
    ) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      void saveUserAccountConfig(
        buildAccountConfigFromLocalStorage(
          betaSessionUser.email,
          persistedOnboardingSession,
          displayNameOverrides,
        ),
      );
    }, 700);

    return () => window.clearTimeout(timeoutId);
  }, [
    accountConfigHydrationStatus,
    betaSessionUser,
    displayNameOverrides,
    persistedOnboardingSession,
  ]);

  const handleBetaSessionDisplayNameChange = (nextName: string) => {
    if (!betaSessionUser || betaSessionUser.userType !== "member") {
      return;
    }

    const trimmedName = nextName.trim();

    if (!trimmedName) {
      return;
    }

    const normalizedEmail = betaSessionUser.email.toLowerCase();
    setDisplayNameOverrides((current) => ({
      ...current,
      [normalizedEmail]: trimmedName,
    }));
  };

  useEffect(() => {
    if (persistedOnboardingSession) {
      window.localStorage.removeItem(ONBOARDING_DRAFT_STATE_STORAGE_KEY);
      return;
    }

    if (view !== "onboarding") {
      return;
    }

    window.localStorage.setItem(
      ONBOARDING_DRAFT_STATE_STORAGE_KEY,
      JSON.stringify({ state: onboardingState }),
    );
  }, [onboardingState, persistedOnboardingSession, view]);

  useEffect(() => {
    if (collaborationInviteRoute || teamInviteRoute) {
      return;
    }

    if (view === "workspace" || view === "transition") {
      if (persistedOnboardingSession) {
        window.localStorage.setItem(CUEVION_APP_VIEW_STORAGE_KEY, "workspace");
      }
      return;
    }

    window.localStorage.setItem(CUEVION_APP_VIEW_STORAGE_KEY, "onboarding");
  }, [collaborationInviteRoute, persistedOnboardingSession, teamInviteRoute, view]);

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
      setBetaAccessRoute(resolveBetaAccessRoute());
      setCollaborationInviteRoute(parseCollaborationInviteRoute());
      setTeamInviteRoute(parseTeamInviteRoute());
    };

    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  useEffect(() => {
    if (collaborationInviteRoute || teamInviteRoute) {
      setBetaSessionStatus("unauthenticated");
      setBetaSessionUser(null);
      setAccountConfigHydrationStatus("ready");
      return;
    }

    let cancelled = false;

    const loadBetaSession = async () => {
      setBetaSessionStatus("loading");

      try {
        const response = await fetch(BETA_SESSION_ENDPOINT, {
          method: "GET",
          credentials: "include",
          cache: "no-store",
        });
        const payload = (await response.json()) as {
          user?: unknown;
          error?: { message?: string };
        };

        if (cancelled) {
          return;
        }

        if (!response.ok) {
          setBetaSessionUser(null);
          setBetaSessionStatus("unauthenticated");
          return;
        }

        const nextUser = normalizeAuthenticatedUser(payload.user);
        if (!nextUser || nextUser.userType !== "member") {
          setBetaSessionUser(null);
          setBetaSessionStatus("unauthenticated");
          return;
        }

        setBetaSessionUser(nextUser);
        setBetaSessionStatus("authenticated");
      } catch {
        if (cancelled) {
          return;
        }

        setBetaSessionUser(null);
        setBetaSessionStatus("unauthenticated");
      }
    };

    void loadBetaSession();

    return () => {
      cancelled = true;
    };
  }, [collaborationInviteRoute, teamInviteRoute]);

  useEffect(() => {
    if (collaborationInviteRoute || teamInviteRoute) {
      setAccountConfigHydrationStatus("ready");
      return;
    }

    if (betaSessionStatus !== "authenticated" || !betaSessionUser) {
      setAccountConfigHydrationStatus(
        betaSessionStatus === "loading" ? "idle" : "ready",
      );
      return;
    }

    let cancelled = false;

    const hydrateAccountConfig = async () => {
      setAccountConfigHydrationStatus("loading");
      const result = await getUserAccountConfig();

      if (cancelled) {
        return;
      }

      if (result.ok && result.config) {
        const rawOnboardingSession = result.config.onboardingSession;
        const nextDisplayNameOverrides =
          result.config.displayNameOverrides &&
          typeof result.config.displayNameOverrides === "object"
            ? result.config.displayNameOverrides
            : {};

        setDisplayNameOverrides(nextDisplayNameOverrides);
        writeAccountConfigToLocalStorage(result.config, betaSessionUser.email);

        if (isPersistedOnboardingSession(rawOnboardingSession)) {
          const nextSession: PersistedOnboardingSession = {
            completed: true,
            state: normalizeOnboardingState(
              rawOnboardingSession.state as Partial<OnboardingState>,
            ),
          };

          setPersistedOnboardingSession(nextSession);
          setOnboardingState(nextSession.state);
          setUserConfig(buildUserConfig(nextSession.state));
          window.localStorage.setItem(CUEVION_APP_VIEW_STORAGE_KEY, "workspace");
          setView("workspace");
        } else {
          setPersistedOnboardingSession(null);
          setUserConfig(null);
          window.localStorage.setItem(CUEVION_APP_VIEW_STORAGE_KEY, "onboarding");
          setView("onboarding");
        }

        setAccountConfigHydrationStatus("ready");
        return;
      }

      const localSession = parsePersistedOnboardingSession();
      if (localSession) {
        void saveUserAccountConfig(
          buildAccountConfigFromLocalStorage(
            betaSessionUser.email,
            localSession,
            displayNameOverrides,
          ),
        );
      }

      setAccountConfigHydrationStatus("ready");
    };

    void hydrateAccountConfig();

    return () => {
      cancelled = true;
    };
  }, [
    betaSessionStatus,
    betaSessionUser,
    collaborationInviteRoute,
    teamInviteRoute,
  ]);

  useEffect(() => {
    if (
      shouldShowLandingPage ||
      collaborationInviteRoute ||
      teamInviteRoute ||
      betaSessionStatus === "loading"
    ) {
      return;
    }

    if (betaSessionUser) {
      if (window.location.pathname !== "/") {
        replaceBetaAccessRoute("app");
      }
    } else if (window.location.pathname !== "/") {
      replaceBetaAccessRoute("login");
    }
    if (betaAccessRoute !== "app") {
      setBetaAccessRoute("app");
    }
  }, [
    betaAccessRoute,
    betaSessionStatus,
    betaSessionUser,
    collaborationInviteRoute,
    teamInviteRoute,
    shouldShowLandingPage,
  ]);

  useEffect(() => {
    const storedCallbackResult = window.localStorage.getItem(
      OAUTH_CALLBACK_RESULT_STORAGE_KEY,
    );

    if (!storedCallbackResult) {
      return;
    }

    window.localStorage.removeItem(OAUTH_CALLBACK_RESULT_STORAGE_KEY);

    let parsedCallbackResult: OAuthCallbackStorageResult | null = null;
    try {
      parsedCallbackResult = normalizeOAuthCallbackStorageResult(
        JSON.parse(storedCallbackResult),
      );
    } catch {
      parsedCallbackResult = null;
    }

    if (!parsedCallbackResult) {
      return;
    }

    setOnboardingState((current) => {
      const nextState = applyOAuthCallbackResultToOnboardingState(
        current,
        parsedCallbackResult as OAuthCallbackStorageResult,
      );

      if (
        persistedOnboardingSession &&
        JSON.stringify(nextState) !== JSON.stringify(persistedOnboardingSession.state)
      ) {
        const nextSession: PersistedOnboardingSession = {
          completed: true,
          state: nextState,
        };
        window.localStorage.setItem(
          ONBOARDING_STATE_STORAGE_KEY,
          JSON.stringify(nextSession),
        );
        setPersistedOnboardingSession(nextSession);
      }

      return nextState;
    });

    const pendingOAuthManagedInbox = parsePendingOAuthManagedInbox();
    const nextManagedInboxes = applyOAuthCallbackResultToManagedInboxes(
      parseStoredManagedWorkspaceInboxes(),
      parsedCallbackResult,
      pendingOAuthManagedInbox,
    );
    window.localStorage.setItem(
      MANAGED_INBOXES_STORAGE_KEY,
      JSON.stringify(nextManagedInboxes),
    );
    if (
      pendingOAuthManagedInbox?.provider === parsedCallbackResult.provider &&
      pendingOAuthManagedInbox?.email?.trim().toLowerCase() === parsedCallbackResult.email
    ) {
      window.localStorage.removeItem(PENDING_OAUTH_MANAGED_INBOX_STORAGE_KEY);
    }
  }, [persistedOnboardingSession]);

  const handleBetaLogin = async (credentials: {
    name: string;
    email: string;
    inviteCode: string;
  }) => {
    setBetaLoginPending(true);
    setBetaLoginError(null);

    try {
      const response = await fetch(BETA_LOGIN_ENDPOINT, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        credentials: "include",
        body: JSON.stringify(credentials),
      });
      const payload = (await response.json()) as {
        user?: unknown;
        error?: { message?: string };
      };

      if (!response.ok) {
        setBetaLoginError(payload.error?.message ?? "Access could not be granted.");
        return;
      }

      const nextUser = normalizeAuthenticatedUser(payload.user);
      if (!nextUser || nextUser.userType !== "member") {
        setBetaLoginError("Access could not be granted.");
        return;
      }

      setBetaSessionUser(nextUser);
      setBetaSessionStatus("authenticated");
      replaceBetaAccessRoute("app");
      setBetaAccessRoute("app");
    } catch {
      setBetaLoginError("Access could not be granted.");
    } finally {
      setBetaLoginPending(false);
    }
  };

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

            setAuthenticatedUser(user);
            setView("workspace");
          }}
        />
      );
    }

    return (
      <WorkspaceShell
        userConfig={userConfig ?? buildUserConfig(onboardingState)}
        onboardingState={onboardingState}
        authenticatedUser={
          collaborationInviteRoute.mode === "invite" ? activeCollaborationUser : null
        }
        collaborationInviteRoute={collaborationInviteRoute}
        workspaceDataMode={workspaceDataMode}
      />
    );
  }

  if (
    betaSessionStatus === "loading" ||
    (betaSessionUser && accountConfigHydrationStatus !== "ready")
  ) {
    return (
      <div className="min-h-screen bg-[linear-gradient(180deg,#f6efe7_0%,#efe5da_100%)] px-6 py-10 text-[color:#2f2a24] dark:bg-[linear-gradient(180deg,#171411_0%,#221c17_100%)] dark:text-[color:#f1e9de]">
        <div className="mx-auto flex min-h-[calc(100vh-5rem)] max-w-[560px] items-center justify-center text-center">
          <div className="space-y-3">
            <div className="text-[0.72rem] font-medium uppercase tracking-[0.22em] text-[rgba(120,104,89,0.7)] dark:text-[rgba(214,201,189,0.64)]">
              Private beta
            </div>
            <p className="text-[0.98rem] leading-7 text-[rgba(88,80,71,0.84)] dark:text-[rgba(222,211,200,0.76)]">
              Verifying access…
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (!betaSessionUser) {
    return (
      <BetaAccessGate
        error={betaLoginError}
        loading={betaLoginPending}
        onSubmit={handleBetaLogin}
      />
    );
  }

  if (view === "workspace" && userConfig) {
    return (
      <WorkspaceShell
        userConfig={userConfig}
        onboardingState={onboardingState}
        authenticatedUser={effectiveBetaSessionUser}
        onAuthenticatedUserNameChange={handleBetaSessionDisplayNameChange}
        workspaceDataMode={workspaceDataMode}
      />
    );
  }

  if (view === "transition") {
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
      onStateChange={setOnboardingState}
      onOpenWorkspace={(nextUserConfig) => {
        const completedSession: PersistedOnboardingSession = {
          completed: true,
          state: onboardingState,
        };

        window.localStorage.setItem(
          ONBOARDING_STATE_STORAGE_KEY,
          JSON.stringify(completedSession),
        );
        window.localStorage.setItem(CUEVION_APP_VIEW_STORAGE_KEY, "workspace");
        window.localStorage.removeItem(ONBOARDING_DRAFT_STATE_STORAGE_KEY);
        setPersistedOnboardingSession(completedSession);
        setUserConfig(nextUserConfig);
        if (betaSessionUser?.userType === "member") {
          void saveUserAccountConfig(
            buildAccountConfigFromLocalStorage(
              betaSessionUser.email,
              completedSession,
              displayNameOverrides,
            ),
          );
        }
        setView("transition");
      }}
    />
  );
}

export default function App() {
  const [isPreviewMode, setIsPreviewMode] = useState(() => isOnboardingPreviewRoute());

  useEffect(() => {
    const handlePopState = () => {
      setIsPreviewMode(isOnboardingPreviewRoute());
    };

    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  if (isPreviewMode) {
    return (
      <OnboardingPreviewRoute
        onExit={() => {
          window.history.replaceState(null, "", "/");
          setIsPreviewMode(false);
        }}
      />
    );
  }

  return <CuevionApp />;
}
