import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  bootstrapGuestSession,
  exchangeGuestInvitation,
  isValidCollaborationGuestDisplayName,
  isValidCollaborationGuestReply,
  logoutGuestCollaboration,
  readGuestCollaboration,
  replyToGuestCollaboration,
  type CollaborationGuestDto,
  type CollaborationGuestFailure,
  type CollaborationGuestFailureStatus,
  type CollaborationGuestSession,
} from "../../lib/collaborationGuestApi";

export type ExternalCollaborationGuestState =
  | "checking_session"
  | "invite_ready"
  | "exchanging"
  | "loading_collaboration"
  | "ready"
  | "replying"
  | "logging_out"
  | "logged_out"
  | "invitation_invalid"
  | "invitation_expired"
  | "invitation_revoked"
  | "invitation_already_used"
  | "session_expired"
  | "session_revoked"
  | "service_unavailable"
  | "rate_limited"
  | "retryable_error";

type GuestApi = {
  bootstrap: typeof bootstrapGuestSession;
  exchange: typeof exchangeGuestInvitation;
  read: typeof readGuestCollaboration;
  reply: typeof replyToGuestCollaboration;
  logout: typeof logoutGuestCollaboration;
};

const defaultGuestApi: GuestApi = {
  bootstrap: bootstrapGuestSession,
  exchange: exchangeGuestInvitation,
  read: readGuestCollaboration,
  reply: replyToGuestCollaboration,
  logout: logoutGuestCollaboration,
};

export function mapCollaborationGuestFailureToState(
  status: CollaborationGuestFailureStatus,
): ExternalCollaborationGuestState {
  switch (status) {
    case "invitation_invalid":
    case "invalid_request":
      return "invitation_invalid";
    case "invitation_expired":
      return "invitation_expired";
    case "invitation_revoked":
      return "invitation_revoked";
    case "invitation_already_exchanged":
      return "invitation_already_used";
    case "session_missing":
    case "session_expired":
      return "session_expired";
    case "session_revoked":
      return "session_revoked";
    case "rate_limited":
      return "rate_limited";
    case "service_unavailable":
    case "origin_rejected":
    case "internal_error":
    case "invalid_response":
      return "service_unavailable";
    case "csrf_failed":
    case "conflict":
    case "network_failure":
      return "retryable_error";
  }
}

export function shouldOfferInviteAfterBootstrap(
  status: CollaborationGuestFailureStatus,
  hasInviteToken: boolean,
) {
  return (
    hasInviteToken &&
    (status === "session_missing" ||
      status === "session_expired" ||
      status === "session_revoked")
  );
}

export function scrubCollaborationGuestFragment() {
  if (typeof window === "undefined" || window.location.hash === "") {
    return;
  }
  window.history.replaceState(
    window.history.state,
    "",
    `${window.location.pathname}${window.location.search}#collab_guest`,
  );
}

function formatTimestamp(value: number | string) {
  const date = typeof value === "number" ? new Date(value) : new Date(value);
  return Number.isNaN(date.getTime())
    ? typeof value === "string"
      ? value
      : ""
    : new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
}

const stateCopy: Record<
  Exclude<
    ExternalCollaborationGuestState,
    | "checking_session"
    | "invite_ready"
    | "exchanging"
    | "loading_collaboration"
    | "ready"
    | "replying"
    | "logging_out"
  >,
  { title: string; message: string }
> = {
  logged_out: {
    title: "Collaboration closed",
    message: "You’ve left this collaboration.",
  },
  invitation_invalid: {
    title: "Link unavailable",
    message: "This collaboration link is no longer valid.",
  },
  invitation_expired: {
    title: "Link expired",
    message: "This collaboration link has expired.",
  },
  invitation_revoked: {
    title: "Invitation inactive",
    message: "This collaboration invitation is no longer active.",
  },
  invitation_already_used: {
    title: "Link already used",
    message: "This collaboration link has already been used.",
  },
  session_expired: {
    title: "Session unavailable",
    message:
      "This invitation or session is no longer available. Reopen the original invitation link to continue.",
  },
  session_revoked: {
    title: "Session ended",
    message: "Access to this collaboration is no longer active.",
  },
  service_unavailable: {
    title: "Temporarily unavailable",
    message: "Collaboration is temporarily unavailable. Please try again later.",
  },
  rate_limited: {
    title: "Please wait",
    message: "Too many attempts. Please try again shortly.",
  },
  retryable_error: {
    title: "Couldn’t complete that request",
    message: "Check your connection and try again.",
  },
};

const pageClass =
  "min-h-screen bg-[radial-gradient(circle_at_top_left,rgba(239,229,214,0.96),transparent_38%),radial-gradient(circle_at_top_right,rgba(171,194,177,0.32),transparent_30%),linear-gradient(180deg,#f8f4ee_0%,#eee6dc_100%)] px-4 py-6 text-[#25352e] sm:px-6 sm:py-10";
const cardClass =
  "rounded-[28px] border border-[rgba(70,91,78,0.12)] bg-[rgba(255,253,249,0.88)] shadow-[0_24px_70px_rgba(57,48,39,0.11)] backdrop-blur";
const primaryButtonClass =
  "inline-flex min-h-11 items-center justify-center rounded-full border border-[rgba(58,105,74,0.28)] bg-[#315f49] px-6 text-sm font-semibold text-white shadow-[0_10px_24px_rgba(49,95,73,0.18)] transition hover:bg-[#284f3d] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#315f49] focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-55";
const secondaryButtonClass =
  "inline-flex min-h-10 items-center justify-center rounded-full border border-[rgba(70,91,78,0.18)] bg-white/70 px-5 text-sm font-semibold text-[#315f49] transition hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#315f49] focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-55";

function GuestHeader() {
  return (
    <header className="mx-auto flex w-full max-w-4xl items-center justify-between px-1 pb-6 sm:pb-8">
      <div>
        <div className="text-[0.72rem] font-semibold uppercase tracking-[0.24em] text-[#315f49]">
          Cuevion
        </div>
        <div className="mt-1 text-sm text-[rgba(37,53,46,0.64)]">
          External collaboration
        </div>
      </div>
      <div className="rounded-full border border-[rgba(49,95,73,0.14)] bg-white/55 px-3 py-1.5 text-xs font-medium text-[rgba(37,53,46,0.7)]">
        No account required
      </div>
    </header>
  );
}

function CenteredState({
  state,
  onRetry,
}: {
  state: keyof typeof stateCopy;
  onRetry?: () => void;
}) {
  const copy = stateCopy[state];
  return (
    <main className={pageClass}>
      <GuestHeader />
      <section
        className={`mx-auto max-w-xl p-7 text-center sm:p-10 ${cardClass}`}
        aria-live="polite"
      >
        <div className="text-[0.72rem] font-semibold uppercase tracking-[0.2em] text-[rgba(49,95,73,0.68)]">
          Shared collaboration
        </div>
        <h1 className="mt-3 text-2xl font-semibold tracking-[-0.03em] sm:text-3xl">
          {copy.title}
        </h1>
        <p className="mx-auto mt-3 max-w-md text-[0.96rem] leading-7 text-[rgba(37,53,46,0.7)]">
          {copy.message}
        </p>
        {onRetry ? (
          <button type="button" className={`${secondaryButtonClass} mt-6`} onClick={onRetry}>
            Try again
          </button>
        ) : null}
      </section>
    </main>
  );
}

export function ExternalCollaborationGuestView({
  initialInviteToken,
  api = defaultGuestApi,
}: {
  initialInviteToken: string | null;
  api?: GuestApi;
}) {
  const [inviteToken, setInviteToken] = useState<string | null>(
    initialInviteToken,
  );
  const [state, setState] =
    useState<ExternalCollaborationGuestState>("checking_session");
  const [session, setSession] = useState<CollaborationGuestSession | null>(null);
  const [csrfToken, setCsrfToken] = useState<string | null>(null);
  const [collaboration, setCollaboration] =
    useState<CollaborationGuestDto | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [displayNameError, setDisplayNameError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [draftError, setDraftError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [retryAfterSeconds, setRetryAfterSeconds] = useState<number | null>(null);
  const startupPromiseRef = useRef<Promise<void> | null>(null);

  useLayoutEffect(() => {
    scrubCollaborationGuestFragment();
  }, []);

  const applyFailure = (failure: CollaborationGuestFailure) => {
    setRetryAfterSeconds(failure.retryAfterSeconds ?? null);
    setState(mapCollaborationGuestFailureToState(failure.status));
  };

  const loadCollaboration = async () => {
    setState("loading_collaboration");
    const result = await api.read();
    if (result.status !== "success") {
      applyFailure(result);
      return false;
    }
    setCollaboration(result.collaboration);
    setState("ready");
    return true;
  };

  const startSessionFirst = async () => {
    setState("checking_session");
    setNotice(null);
    const result = await api.bootstrap();
    if (result.status === "success") {
      setInviteToken(null);
      setSession(result.session);
      setCsrfToken(result.csrfToken);
      await loadCollaboration();
      return;
    }
    if (shouldOfferInviteAfterBootstrap(result.status, inviteToken !== null)) {
      setState("invite_ready");
      return;
    }
    applyFailure(result);
  };

  useEffect(() => {
    startupPromiseRef.current ??= startSessionFirst();
  }, []);

  const handleExchange = async (event: FormEvent) => {
    event.preventDefault();
    if (!inviteToken) {
      setState("invitation_invalid");
      return;
    }
    if (!isValidCollaborationGuestDisplayName(displayName)) {
      setDisplayNameError(
        "Enter a name without hidden characters (up to 256 UTF-8 bytes).",
      );
      return;
    }
    setDisplayNameError(null);
    setState("exchanging");
    const result = await api.exchange(inviteToken, displayName);
    if (result.status !== "success") {
      if (
        result.status !== "network_failure" &&
        result.status !== "rate_limited" &&
        result.status !== "service_unavailable" &&
        result.status !== "conflict"
      ) {
        setInviteToken(null);
      }
      applyFailure(result);
      return;
    }
    setInviteToken(null);
    setSession(result.session);
    setCsrfToken(result.csrfToken);
    await loadCollaboration();
  };

  const recoverSessionAfterReplyFailure = async () => {
    const result = await api.bootstrap();
    if (result.status !== "success") {
      applyFailure(result);
      return;
    }
    setSession(result.session);
    setCsrfToken(result.csrfToken);
    setNotice("Your session was refreshed. Review your draft and press Send reply again.");
    setState("ready");
  };

  const handleReply = async (event: FormEvent) => {
    event.preventDefault();
    if (!isValidCollaborationGuestReply(draft)) {
      setDraftError("Enter a reply up to 16 KB without hidden control characters.");
      return;
    }
    if (!csrfToken) {
      setDraftError(null);
      await recoverSessionAfterReplyFailure();
      return;
    }
    setDraftError(null);
    setNotice(null);
    setState("replying");
    const result = await api.reply(draft, csrfToken);
    if (result.status === "success") {
      setCollaboration(result.collaboration);
      setDraft("");
      setState("ready");
      return;
    }
    if (
      result.status === "csrf_failed" ||
      result.status === "session_missing" ||
      result.status === "session_expired" ||
      result.status === "session_revoked"
    ) {
      await recoverSessionAfterReplyFailure();
      return;
    }
    applyFailure(result);
  };

  const clearGuestMemory = () => {
    setInviteToken(null);
    setSession(null);
    setCsrfToken(null);
    setCollaboration(null);
    setDraft("");
    setNotice(null);
  };

  const handleLogout = async () => {
    if (!csrfToken) {
      clearGuestMemory();
      setState("session_expired");
      return;
    }
    setState("logging_out");
    const result = await api.logout(csrfToken);
    if (result.status === "success") {
      clearGuestMemory();
      setState("logged_out");
      return;
    }
    if (
      result.status === "session_missing" ||
      result.status === "session_expired" ||
      result.status === "session_revoked"
    ) {
      clearGuestMemory();
    }
    applyFailure(result);
  };

  if (
    state === "checking_session" ||
    state === "loading_collaboration" ||
    state === "exchanging"
  ) {
    return (
      <main className={pageClass}>
        <GuestHeader />
        <section className={`mx-auto max-w-xl p-8 text-center ${cardClass}`} aria-live="polite">
          <h1 className="text-2xl font-semibold tracking-[-0.03em]">
            {state === "exchanging"
              ? "Opening collaboration"
              : state === "loading_collaboration"
                ? "Loading shared messages"
                : "Checking your session"}
          </h1>
          <p className="mt-3 text-sm leading-6 text-[rgba(37,53,46,0.68)]">
            This should only take a moment.
          </p>
        </section>
      </main>
    );
  }

  if (state === "invite_ready") {
    return (
      <main className={pageClass}>
        <GuestHeader />
        <section className={`mx-auto max-w-xl p-7 sm:p-10 ${cardClass}`}>
          <div className="text-[0.72rem] font-semibold uppercase tracking-[0.2em] text-[rgba(49,95,73,0.68)]">
            Shared collaboration
          </div>
          <h1 className="mt-3 text-2xl font-semibold tracking-[-0.03em] sm:text-3xl">
            You’ve been invited to review a message
          </h1>
          <p className="mt-3 text-[0.96rem] leading-7 text-[rgba(37,53,46,0.7)]">
            No Cuevion account is required. Enter the name you want shown with your replies.
          </p>
          <form className="mt-7 space-y-5" onSubmit={handleExchange}>
            <div>
              <label htmlFor="collaboration-guest-name" className="block text-sm font-semibold">
                Your name
              </label>
              <input
                id="collaboration-guest-name"
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                autoComplete="name"
                className="mt-2 min-h-12 w-full rounded-2xl border border-[rgba(70,91,78,0.2)] bg-white/76 px-4 text-base outline-none transition focus:border-[#315f49] focus:ring-2 focus:ring-[rgba(49,95,73,0.16)]"
                aria-describedby={displayNameError ? "collaboration-guest-name-error" : undefined}
              />
              {displayNameError ? (
                <p id="collaboration-guest-name-error" className="mt-2 text-sm text-[#934c3b]" aria-live="polite">
                  {displayNameError}
                </p>
              ) : null}
            </div>
            <button type="submit" className={primaryButtonClass}>
              Open collaboration
            </button>
          </form>
        </section>
      </main>
    );
  }

  if (state !== "ready" && state !== "replying" && state !== "logging_out") {
    const canRetry =
      state === "retryable_error" ||
      state === "rate_limited" ||
      (state === "service_unavailable" && inviteToken !== null);
    return (
      <CenteredState
        state={state}
        onRetry={
          canRetry
            ? () => {
                setRetryAfterSeconds(null);
                startupPromiseRef.current = startSessionFirst();
              }
            : undefined
        }
      />
    );
  }

  if (!collaboration || !session) {
    return <CenteredState state="service_unavailable" />;
  }

  return (
    <main className={pageClass}>
      <GuestHeader />
      <div className="mx-auto grid w-full max-w-4xl gap-5 lg:grid-cols-[minmax(0,1.18fr)_minmax(300px,0.82fr)] lg:items-start">
        <section className={`min-w-0 overflow-hidden ${cardClass}`} aria-labelledby="shared-message-heading">
          <div className="border-b border-[rgba(70,91,78,0.1)] px-5 py-5 sm:px-7">
            <div className="text-[0.7rem] font-semibold uppercase tracking-[0.18em] text-[rgba(49,95,73,0.65)]">
              Shared message
            </div>
            <h1 id="shared-message-heading" className="mt-2 break-words text-xl font-semibold tracking-[-0.025em] sm:text-2xl">
              {collaboration.sharedSource.subject || "Shared message"}
            </h1>
            <dl className="mt-4 grid gap-2 text-sm text-[rgba(37,53,46,0.67)] sm:grid-cols-2">
              <div className="min-w-0">
                <dt className="sr-only">Sender</dt>
                <dd className="break-words">{collaboration.sharedSource.senderDisplay}</dd>
              </div>
              <div className="min-w-0 sm:text-right">
                <dt className="sr-only">Sent</dt>
                <dd>{formatTimestamp(collaboration.sharedSource.timestamp)}</dd>
              </div>
              {collaboration.sharedSource.fromDisplay ? (
                <div className="min-w-0 sm:col-span-2">
                  <dt className="inline font-medium">From: </dt>
                  <dd className="inline break-all">{collaboration.sharedSource.fromDisplay}</dd>
                </div>
              ) : null}
            </dl>
          </div>
          <div className="whitespace-pre-wrap break-words px-5 py-6 text-[0.96rem] leading-7 text-[rgba(37,53,46,0.86)] sm:px-7 sm:py-8">
            {collaboration.sharedSource.bodyText}
          </div>
        </section>

        <section className={`min-w-0 p-5 sm:p-6 ${cardClass}`} aria-labelledby="collaboration-heading">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-[0.7rem] font-semibold uppercase tracking-[0.18em] text-[rgba(49,95,73,0.65)]">
                Collaboration
              </div>
              <h2 id="collaboration-heading" className="mt-1 text-xl font-semibold tracking-[-0.025em]">
                Shared activity
              </h2>
            </div>
            <button
              type="button"
              className={secondaryButtonClass}
              onClick={() => void handleLogout()}
              disabled={state === "logging_out" || state === "replying"}
            >
              {state === "logging_out" ? "Leaving…" : "Leave collaboration"}
            </button>
          </div>

          <p className="mt-3 rounded-2xl bg-[rgba(224,235,227,0.58)] px-4 py-3 text-sm leading-6 text-[rgba(37,53,46,0.72)]">
            You can see shared collaboration messages only.
          </p>

          <ol className="mt-5 space-y-3">
            {collaboration.messages.map((message) => (
              <li
                key={message.id}
                className={`rounded-2xl border px-4 py-3 ${
                  message.authorRole === "Guest reviewer"
                    ? "border-[rgba(49,95,73,0.18)] bg-[rgba(224,235,227,0.46)]"
                    : message.authorRole === "System"
                      ? "border-[rgba(120,104,89,0.12)] bg-[rgba(242,236,228,0.65)]"
                      : "border-[rgba(70,91,78,0.12)] bg-white/62"
                }`}
              >
                <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                  <div className="min-w-0 break-words text-sm font-semibold">
                    {message.authorDisplayName}
                    <span className="ml-2 text-xs font-medium text-[rgba(37,53,46,0.52)]">
                      {message.authorRole}
                    </span>
                  </div>
                  <time className="text-xs text-[rgba(37,53,46,0.5)]">
                    {formatTimestamp(message.timestamp)}
                  </time>
                </div>
                <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-[rgba(37,53,46,0.82)]">
                  {message.text}
                </p>
              </li>
            ))}
          </ol>

          <form className="mt-6 border-t border-[rgba(70,91,78,0.1)] pt-5" onSubmit={handleReply}>
            <label htmlFor="collaboration-guest-reply" className="block text-sm font-semibold">
              Reply
            </label>
            <textarea
              id="collaboration-guest-reply"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              rows={5}
              disabled={state === "replying" || state === "logging_out"}
              className="mt-2 w-full resize-y rounded-2xl border border-[rgba(70,91,78,0.2)] bg-white/76 px-4 py-3 text-base leading-6 outline-none transition focus:border-[#315f49] focus:ring-2 focus:ring-[rgba(49,95,73,0.16)] disabled:opacity-60"
              aria-describedby="collaboration-guest-reply-status"
            />
            <div id="collaboration-guest-reply-status" className="mt-2 min-h-6 text-sm" aria-live="polite">
              {draftError ? <span className="text-[#934c3b]">{draftError}</span> : null}
              {notice ? <span className="text-[#315f49]">{notice}</span> : null}
              {retryAfterSeconds ? (
                <span className="text-[#934c3b]">Try again in about {retryAfterSeconds} seconds.</span>
              ) : null}
            </div>
            <button
              type="submit"
              className={`${primaryButtonClass} mt-3`}
              disabled={state === "replying" || state === "logging_out"}
            >
              {state === "replying" ? "Sending…" : "Send reply"}
            </button>
          </form>
        </section>
      </div>
    </main>
  );
}
