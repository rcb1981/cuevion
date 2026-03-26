import { useEffect, useState } from "react";
import { OnboardingFlow } from "./components/onboarding/OnboardingFlow";
import { WorkspaceTransition } from "./components/workspace/WorkspaceTransition";
import { WorkspaceShell } from "./components/workspace/WorkspaceShell";
import { initialOnboardingState } from "./data/onboardingOptions";
import type { OnboardingState } from "./types/onboarding";

const ONBOARDING_STATE_STORAGE_KEY = "label-inbox-ai-onboarding-state";
const CATEGORY_LEARNING_STORAGE_KEY = "cuevion-sender-category-learning";
const MESSAGE_OWNERSHIP_STORAGE_KEY = "cuevion-message-ownership";
const MANAGED_INBOXES_STORAGE_KEY = "cuevion-managed-inboxes";
const CUEVION_AUTH_STORAGE_KEY = "label-inbox-ai-auth-user";
const PENDING_COLLAB_INVITE_STORAGE_KEY = "label-inbox-ai-pending-collab-invite";
const PENDING_COLLAB_INVITE_URL_STORAGE_KEY = "label-inbox-ai-pending-collab-invite-url";

type AuthenticatedCuevionUser = {
  email: string;
  name: string;
  userType: "member" | "guest";
};

type CollaborationInviteRoute = {
  inviteToken: string;
  messageId?: string;
  inviteeEmail?: string;
  status?: string;
};

type PersistedOnboardingSession = {
  completed: true;
  state: OnboardingState;
};
type WorkspaceDataMode = "demo" | "live";

function ComingSoonLanding() {
  return (
    <div className="min-h-screen bg-[linear-gradient(180deg,#8cab74_0%,#6f8f5f_100%)] px-6 py-10 text-[rgba(248,247,242,0.98)]">
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
            The new standard for music communication.
          </p>
          <p className="mt-6 text-[0.9rem] font-medium tracking-[0.08em] text-[rgba(244,242,235,0.56)]">
            Coming soon...
          </p>
        </div>
      </div>
    </div>
  );
}

function normalizeOnboardingState(value: Partial<OnboardingState>): OnboardingState {
  return {
    ...initialOnboardingState,
    ...value,
    customInboxes: Array.isArray(value.customInboxes) ? value.customInboxes : [],
    inboxConnections: {
      ...initialOnboardingState.inboxConnections,
      ...(value.inboxConnections ?? {}),
    },
  };
}

const existingCuevionUsers: AuthenticatedCuevionUser[] = [
  {
    email: "emma@cuevion.com",
    name: "Emma Stone",
    userType: "member",
  },
  {
    email: "david@cuevion.com",
    name: "David Cole",
    userType: "member",
  },
  {
    email: "mila@cuevion.com",
    name: "Mila Hart",
    userType: "member",
  },
];

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
  const inviteToken = params.get("collab_invite");
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

function CollaborationInviteAuthGate({
  onAuthenticate,
}: {
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

                const matchedUser = existingCuevionUsers.find(
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
  return <ComingSoonLanding />;

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
    () => persistedOnboardingSession?.state ?? initialOnboardingState,
  );

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

  if (collaborationInviteRoute) {
    if (!authenticatedUser) {
      return (
        <CollaborationInviteAuthGate
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
        onboardingState={onboardingState}
        authenticatedUser={authenticatedUser}
        collaborationInviteRoute={collaborationInviteRoute}
        workspaceDataMode={workspaceDataMode}
      />
    );
  }

  if (view === "workspace") {
    return (
      <WorkspaceShell
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
      onOpenWorkspace={() => {
        const completedSession: PersistedOnboardingSession = {
          completed: true,
          state: onboardingState,
        };

        window.localStorage.setItem(
          ONBOARDING_STATE_STORAGE_KEY,
          JSON.stringify(completedSession),
        );
        setPersistedOnboardingSession(completedSession);
        setView("transition");
      }}
    />
  );
}
