import { useEffect, useState } from "react";
import { OnboardingFlow } from "./components/onboarding/OnboardingFlow";
import { WorkspaceTransition } from "./components/workspace/WorkspaceTransition";
import { WorkspaceShell } from "./components/workspace/WorkspaceShell";
import { initialOnboardingState } from "./data/onboardingOptions";
import type { OnboardingState } from "./types/onboarding";
import type { UserConfig } from "./types/userConfig";

const ONBOARDING_STATE_STORAGE_KEY = "label-inbox-ai-onboarding-state";
const ONBOARDING_DRAFT_STATE_STORAGE_KEY = "label-inbox-ai-onboarding-draft-state";
const CATEGORY_LEARNING_STORAGE_KEY = "cuevion-sender-category-learning";
const MESSAGE_OWNERSHIP_STORAGE_KEY = "cuevion-message-ownership";
const MANAGED_INBOXES_STORAGE_KEY = "cuevion-managed-inboxes";
const CUEVION_AUTH_STORAGE_KEY = "label-inbox-ai-auth-user";
const PENDING_COLLAB_INVITE_STORAGE_KEY = "label-inbox-ai-pending-collab-invite";
const PENDING_COLLAB_INVITE_URL_STORAGE_KEY = "label-inbox-ai-pending-collab-invite-url";
const OAUTH_CALLBACK_RESULT_STORAGE_KEY = "cuevion-oauth-callback-result";

type AuthenticatedCuevionUser = {
  email: string;
  name: string;
  userType: "member" | "guest";
};

type CollaborationInviteRoute = {
  mode: "invite" | "external_review";
  inviteToken: string;
  messageId?: string;
  inviteeEmail?: string;
  status?: string;
};

type PersistedOnboardingSession = {
  completed: true;
  state: OnboardingState;
};
type PersistedOnboardingDraft = {
  state: OnboardingState;
};
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
};
type StoredTeamMemberEntry = {
  email?: string;
  name?: string;
};
type OAuthCallbackStorageResult = {
  provider?: string;
  email?: string;
  connectionMethod?: string;
  connectionStatus?: string;
  connected?: boolean;
  message?: string | null;
};

function buildUserConfig(state: OnboardingState): UserConfig {
  return {
    primaryRole: state.primaryRole,
    internalRole: state.internalRole,
    focusPreferences: state.focusPreferences,
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

function normalizeOnboardingState(value: Partial<OnboardingState>): OnboardingState {
  return {
    ...initialOnboardingState,
    ...value,
    internalRole: value.internalRole ?? null,
    primaryInbox: value.primaryInbox ?? initialOnboardingState.primaryInbox,
    primaryInboxType: value.primaryInboxType ?? null,
    focusPreferences: {
      ...initialOnboardingState.focusPreferences,
      ...(value.focusPreferences ?? {}),
    },
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
) {
  const normalizedEmail = callbackResult.email?.trim().toLowerCase() ?? "";

  if (!normalizedEmail) {
    return inboxes;
  }

  const providerName =
    callbackResult.provider === "microsoft" ? "Microsoft" : "Google";
  return inboxes.map((mailbox) => {
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
      connected:
        callbackResult.connected === true &&
        callbackResult.connectionStatus === "connected",
      connectionMethod: "oauth",
      connectionStatus,
      connectionMessage,
      oauthAuthorizationUrl: null,
    };
  });
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
              className="inline-flex h-10 items-center justify-center rounded-full border border-[rgba(66,99,69,0.52)] bg-[linear-gradient(180deg,rgba(103,141,103,0.98),rgba(69,103,72,0.98))] px-5 text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[rgba(251,248,242,0.98)] shadow-[inset_0_1px_0_rgba(255,255,255,0.1),0_8px_18px_rgba(66,99,69,0.12)] transition-[background-image,border-color,transform,box-shadow] duration-150 hover:border-[rgba(58,88,62,0.6)] hover:bg-[linear-gradient(180deg,rgba(93,130,95,0.98),rgba(61,95,65,0.98))] active:scale-[0.99] focus-visible:outline-none"
            >
              Continue
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const shouldShowLandingPage = isPublicLandingHost();

  if (shouldShowLandingPage) {
    return <ComingSoonLanding />;
  }

  const workspaceDataMode = resolveWorkspaceDataMode();
  const [persistedOnboardingSession, setPersistedOnboardingSession] =
    useState<PersistedOnboardingSession | null>(() => {
      if (shouldResetOnboardingFromQuery()) {
        window.localStorage.removeItem(ONBOARDING_STATE_STORAGE_KEY);
        clearOnboardingDependentWorkspaceState();
        clearOnboardingResetQueryParam();
        return null;
      }

      return parsePersistedOnboardingSession();
    });
  const [persistedOnboardingDraft] = useState<PersistedOnboardingDraft | null>(() =>
    persistedOnboardingSession ? null : parsePersistedOnboardingDraft(),
  );
  const [view, setView] = useState<"onboarding" | "transition" | "workspace">(
    () => (persistedOnboardingSession ? "workspace" : "onboarding"),
  );
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
  const [onboardingState, setOnboardingState] = useState<OnboardingState>(
    () =>
      persistedOnboardingSession?.state ??
      persistedOnboardingDraft?.state ??
      initialOnboardingState,
  );
  const [userConfig, setUserConfig] = useState<UserConfig | null>(() =>
    persistedOnboardingSession?.state ? buildUserConfig(persistedOnboardingSession.state) : null,
  );
  const recognizedInviteUsers = resolveWorkspaceInviteUsers(onboardingState);

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
    const nextInviteRoute = parseCollaborationInviteRoute();
    setCollaborationInviteRoute(nextInviteRoute);

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
    if (view !== "transition") {
      return;
    }

    const timer = window.setTimeout(() => {
      setView("workspace");
    }, 2200);

    return () => window.clearTimeout(timer);
  }, [view]);

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

    const nextManagedInboxes = applyOAuthCallbackResultToManagedInboxes(
      parseStoredManagedWorkspaceInboxes(),
      parsedCallbackResult,
    );
    window.localStorage.setItem(
      MANAGED_INBOXES_STORAGE_KEY,
      JSON.stringify(nextManagedInboxes),
    );
  }, [persistedOnboardingSession]);

  if (collaborationInviteRoute) {
    if (collaborationInviteRoute.mode === "invite" && !authenticatedUser) {
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
        authenticatedUser={collaborationInviteRoute.mode === "invite" ? authenticatedUser : null}
        collaborationInviteRoute={collaborationInviteRoute}
        workspaceDataMode={workspaceDataMode}
      />
    );
  }

  if (view === "workspace" && userConfig) {
    return (
      <WorkspaceShell
        userConfig={userConfig}
        onboardingState={onboardingState}
        workspaceDataMode={workspaceDataMode}
      />
    );
  }

  if (view === "transition") {
    return <WorkspaceTransition />;
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
        window.localStorage.removeItem(ONBOARDING_DRAFT_STATE_STORAGE_KEY);
        setPersistedOnboardingSession(completedSession);
        setUserConfig(nextUserConfig);
        setView("transition");
      }}
    />
  );
}
