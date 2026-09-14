import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { logoutAuth0Session, type CuevionSessionUser } from "../../lib/authApi";
import { useCollaborationSummaries } from "../../lib/useCollaborationSummaries";
import {
  readCollaborationForOwner,
  isValidCollaborationOwnerReadId,
  type CollaborationOwnerReadDto,
  type CollaborationOwnerReadResult,
  type CollaborationParticipantViewerReadDto,
} from "../../lib/collaborationOwnerReadApi";
import {
  prepareInternalCollaborationMessageForOwner,
  prepareSharedCollaborationMessageForOwner,
  type CollaborationOwnerAppendOperation,
  type CollaborationOwnerAppendPreparationResult,
} from "../../lib/collaborationOwnerWriteApi";

type MemberIdentity = CuevionSessionUser & { userId: string; workspaceId: string };
type ReplyVisibility = "internal" | "shared";
type MemberEntry =
  | { status: "loading" }
  | { status: "failure"; message: string }
  | { status: "ready"; collaboration: CollaborationParticipantViewerReadDto };
type MemberSnapshot = {
  entries: Map<string, MemberEntry>;
  selectedId: string | null;
  sending: boolean;
  error: string | null;
  authenticationLost: boolean;
};
type MemberPorts = {
  read: (id: string) => Promise<CollaborationOwnerReadResult>;
  prepare: (id: string, text: string, visibility: ReplyVisibility) => CollaborationOwnerAppendPreparationResult;
};

export function isTeamMemberSession(user: CuevionSessionUser | null): user is MemberIdentity {
  return user?.userType === "member" && user.workspaceRole === "member" &&
    /^usr_[A-Za-z0-9_-]{22}$/.test(user.userId ?? "") &&
    /^wsp_[A-Za-z0-9_-]{22}$/.test(user.workspaceId ?? "");
}

export function isMemberCollaboration(
  dto: CollaborationOwnerReadDto,
  collaborationId: string,
  userId: string,
): dto is CollaborationParticipantViewerReadDto {
  return dto.collaborationId === collaborationId && dto.viewerAccess === "participant" &&
    dto.participants.filter((participant) => participant.userId === userId && participant.access === "participant").length === 1;
}

function failureMessage(status: string) {
  if (status === "unauthorized") return "Your session ended. Sign in again to continue.";
  if (status === "forbidden" || status === "not_found") return "This collaboration is no longer available to you.";
  if (status === "conflict") return "This collaboration changed. Refresh it before replying.";
  if (status === "rate_limited") return "Too many requests. Try again shortly.";
  if (status === "invalid_text") return "Enter a shorter reply using plain text.";
  return "Collaboration is temporarily unavailable. Try again.";
}

// The controller owns only canonical participant DTOs and prepared reply retries.
// Every asynchronous publication is fenced by the current discovery and request.
export function createTeamMemberController(user: CuevionSessionUser | null, ports: MemberPorts) {
  const listeners = new Set<() => void>();
  const empty = (): MemberSnapshot => ({ entries: new Map(), selectedId: null, sending: false, error: null, authenticationLost: false });
  let snapshot = empty();
  let enabled = isTeamMemberSession(user);
  let allowedIds = new Set<string>();
  const reads = new Map<string, object>();
  let pending: { id: string; text: string; visibility: ReplyVisibility; operation: CollaborationOwnerAppendOperation } | null = null;
  const publish = (next: MemberSnapshot) => {
    snapshot = next;
    listeners.forEach((listener) => listener());
  };
  const updateEntry = (id: string, entry: MemberEntry) => {
    const entries = new Map(snapshot.entries);
    entries.set(id, entry);
    publish({ ...snapshot, entries });
  };
  const loseAuthentication = () => {
    enabled = false;
    reads.clear();
    allowedIds.clear();
    pending = null;
    publish({ ...empty(), authenticationLost: true });
  };
  const read = async (id: string) => {
    if (!enabled || !allowedIds.has(id) || (snapshot.sending && pending?.id === id)) return;
    const ticket = {};
    reads.set(id, ticket);
    updateEntry(id, { status: "loading" });
    let result: CollaborationOwnerReadResult;
    try { result = await ports.read(id); } catch { result = { status: "network_failure" }; }
    if (!enabled || !allowedIds.has(id) || reads.get(id) !== ticket) return;
    reads.delete(id);
    if (result.status === "unauthorized") {
      loseAuthentication();
    } else if (result.status === "success" && user?.userId && isMemberCollaboration(result.collaboration, id, user.userId)) {
      updateEntry(id, { status: "ready", collaboration: result.collaboration });
    } else {
      pending = pending?.id === id ? null : pending;
      updateEntry(id, { status: "failure", message: failureMessage(result.status === "success" ? "forbidden" : result.status) });
    }
  };
  return {
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    getSnapshot: () => snapshot,
    setDiscovery(ids: string[]) {
      if (!enabled) return;
      ids = ids.filter(isValidCollaborationOwnerReadId);
      allowedIds = new Set(ids);
      reads.forEach((_, id) => { if (!allowedIds.has(id)) reads.delete(id); });
      if (pending && !allowedIds.has(pending.id)) pending = null;
      const entries = new Map([...snapshot.entries].filter(([id]) => allowedIds.has(id)));
      const selectedId = snapshot.selectedId && allowedIds.has(snapshot.selectedId) ? snapshot.selectedId : ids[0] ?? null;
      publish({ ...snapshot, entries, selectedId, sending: snapshot.sending && pending !== null, error: null });
      ids.forEach((id) => { if (!entries.has(id)) void read(id); });
    },
    select(id: string) {
      if (!enabled || !allowedIds.has(id) || snapshot.sending) return;
      if (snapshot.selectedId === id) return;
      pending = null;
      publish({ ...snapshot, selectedId: id, error: null });
    },
    refresh: read,
    async send(text: string, visibility: ReplyVisibility): Promise<boolean> {
      const id = snapshot.selectedId;
      const entry = id ? snapshot.entries.get(id) : undefined;
      if (!enabled || !id || !allowedIds.has(id) || snapshot.sending || entry?.status !== "ready" || entry.collaboration.state === "resolved" || !text.trim()) return false;
      if (!pending || pending.id !== id || pending.text !== text || pending.visibility !== visibility) {
        const preparation = ports.prepare(id, text, visibility);
        if (preparation.status !== "ready") {
          publish({ ...snapshot, error: failureMessage(preparation.status) });
          return false;
        }
        pending = { id, text, visibility, operation: preparation.operation };
      }
      const attempt = pending;
      reads.delete(id);
      publish({ ...snapshot, sending: true, error: null });
      let result;
      try { result = await attempt.operation.execute(); } catch { result = { status: "network_failure" } as const; }
      if (!enabled || pending !== attempt || !allowedIds.has(id)) return false;
      if (result.status === "unauthorized") { loseAuthentication(); return false; }
      if (result.status !== "success") {
        const accessChanged = result.status === "forbidden" || result.status === "not_found" || result.status === "conflict";
        if (accessChanged) pending = null;
        publish({ ...snapshot, sending: false, error: failureMessage(result.status) });
        if (accessChanged) updateEntry(id, { status: "failure", message: failureMessage(result.status) });
        return false;
      }
      const current = snapshot.entries.get(id);
      pending = null;
      if (current?.status !== "ready") { publish({ ...snapshot, sending: false }); return false; }
      const collaboration = {
        ...current.collaboration,
        updatedAt: result.updatedAt,
        messages: current.collaboration.messages.some((message) => message.id === result.message.id)
          ? current.collaboration.messages : [...current.collaboration.messages, result.message],
      };
      publish({ ...snapshot, sending: false, error: null });
      updateEntry(id, { status: "ready", collaboration });
      return true;
    },
    cancel() {
      reads.clear(); allowedIds.clear(); pending = null;
      publish(empty());
    },
    endSession: loseAuthentication,
  };
}

const buttonClass = "rounded-full border border-[#bbc6b8] bg-white px-4 py-2 text-sm font-medium text-[#274536] transition hover:bg-[#edf1e9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#52785b] disabled:cursor-not-allowed disabled:opacity-50";
const memberPorts: MemberPorts = {
  read: readCollaborationForOwner,
  prepare: (id, text, visibility) => visibility === "internal"
    ? prepareInternalCollaborationMessageForOwner(id, text)
    : prepareSharedCollaborationMessageForOwner(id, text),
};

function TeamMemberWorkspace({ authenticatedUser, onReload }: { authenticatedUser: MemberIdentity; onReload: () => void }) {
  const summaries = useCollaborationSummaries(authenticatedUser.workspaceId, authenticatedUser.userId);
  const controller = useMemo(() => createTeamMemberController(authenticatedUser, memberPorts), [authenticatedUser.workspaceId, authenticatedUser.userId]);
  const snapshot = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot);
  const discoveryKey = JSON.stringify([...new Set([...summaries.historyIndex.values()].map((summary) => summary.collaborationId))].sort());
  const [draft, setDraft] = useState("");
  const [visibility, setVisibility] = useState<ReplyVisibility>("internal");
  const [signingOut, setSigningOut] = useState(false);
  const [signOutError, setSignOutError] = useState<string | null>(null);
  useEffect(() => {
    controller.setDiscovery(JSON.parse(discoveryKey) as string[]);
  }, [controller, discoveryKey]);
  useEffect(() => controller.cancel, [controller]);
  useEffect(() => { setDraft(""); setVisibility("internal"); }, [snapshot.selectedId]);
  const selected = snapshot.selectedId ? snapshot.entries.get(snapshot.selectedId) : undefined;
  const collaboration = selected?.status === "ready" ? selected.collaboration : null;
  const signOut = async () => {
    if (signingOut) return;
    setSigningOut(true); setSignOutError(null); controller.endSession();
    try {
      const result = await logoutAuth0Session();
      if (result.ok && result.logoutUrl) { window.location.assign(result.logoutUrl); return; }
    } catch { /* Show a retry without restoring collaboration data. */ }
    setSigningOut(false); setSignOutError("Sign out could not be completed. Please try again.");
  };
  return (
    <main className="min-h-screen bg-[#f4f1e9] text-[#263b2e]">
      <header className="border-b border-[#d9ded1] bg-[#fcfbf7] px-5 py-5 sm:px-8">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4">
          <div><p className="text-xs font-semibold uppercase tracking-[0.24em] text-[#5f705e]">Cuevion</p><h1 className="mt-1 text-2xl font-semibold tracking-tight">Collaboration</h1></div>
          <div className="flex items-center gap-4"><span className="text-sm text-[#596752]">{authenticatedUser.name}</span><button type="button" className={buttonClass} disabled={signingOut} onClick={() => void signOut()}>{signingOut ? "Signing out…" : "Sign out"}</button></div>
        </div>
      </header>
      <div className="mx-auto max-w-6xl px-5 py-7 sm:px-8">
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3"><p className="text-sm leading-6 text-[#65715e]">Conversations shared with you and your team.</p><button type="button" className={buttonClass} disabled={snapshot.sending || Boolean(draft)} onClick={onReload}>Refresh collaborations</button></div>
        {signOutError ? <p role="alert" className="mb-4 text-sm text-[#92503f]">{signOutError}</p> : null}
        {snapshot.authenticationLost ? <div role="alert" className="rounded-2xl border border-[#d9ded1] bg-white p-8"><p>{failureMessage("unauthorized")}</p><a className={`${buttonClass} mt-4 inline-block`} href="/login">Sign in</a></div> : (
          <div className="grid gap-5 md:grid-cols-[260px_minmax(0,1fr)]">
            <aside aria-label="Your collaborations" className="space-y-2">
              {snapshot.entries.size === 0 ? <p className="rounded-2xl border border-[#d9ded1] bg-[#fcfbf7] p-5 text-sm leading-6 text-[#65715e]">Conversations shared with you appear here. Refresh to check for updates.</p> : null}
              {[...snapshot.entries].map(([id, entry], index) => (
                <button type="button" key={id} disabled={snapshot.sending} aria-pressed={id === snapshot.selectedId} onClick={() => controller.select(id)} className={`w-full rounded-2xl border p-4 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#52785b] disabled:opacity-60 ${id === snapshot.selectedId ? "border-[#82997b] bg-[#e7eddf]" : "border-[#d9ded1] bg-[#fcfbf7] hover:bg-white"}`}>
                  <span className="block text-sm font-semibold">{entry.status === "ready" ? entry.collaboration.source.subject || "Untitled conversation" : `Collaboration ${index + 1}`}</span>
                  <span className="mt-1 block text-xs text-[#65715e]">{entry.status === "ready" ? entry.collaboration.state === "resolved" ? "Resolved" : "Open conversation" : entry.status === "loading" ? "Loading…" : "Unavailable"}</span>
                </button>
              ))}
            </aside>
            <section aria-label="Conversation" className="min-w-0 rounded-3xl border border-[#d9ded1] bg-[#fcfbf7] p-5 sm:p-7">
              {selected?.status === "loading" ? <p role="status" className="py-8 text-sm text-[#65715e]">Loading collaboration…</p> : null}
              {selected?.status === "failure" ? <div role="alert" className="space-y-4 py-8"><p>{selected.message}</p><button type="button" className={buttonClass} onClick={() => snapshot.selectedId && void controller.refresh(snapshot.selectedId)}>Retry</button></div> : null}
              {!selected ? <div className="py-16 text-center"><h2 className="text-xl font-medium">A shared place to move things forward</h2><p className="mt-3 text-sm text-[#65715e]">Select a collaboration to read the conversation and reply.</p></div> : null}
              {collaboration ? <>
                <div className="flex items-start justify-between gap-4"><div className="min-w-0"><p className="text-xs font-medium uppercase tracking-widest text-[#65715e]">{collaboration.state === "resolved" ? "Resolved" : "Team conversation"}</p><h2 className="mt-2 break-words text-xl font-semibold">{collaboration.source.subject || "Untitled conversation"}</h2></div><button type="button" disabled={snapshot.sending} className={buttonClass} onClick={() => snapshot.selectedId && void controller.refresh(snapshot.selectedId)}>Refresh</button></div>
                <details className="my-6 rounded-2xl border border-[#dfe3d8] bg-[#f3f5ee] p-4"><summary className="cursor-pointer text-sm font-medium">Original email</summary><p className="mt-3 text-sm text-[#65715e]">{collaboration.source.senderDisplay} · {collaboration.source.fromDisplay}</p>{collaboration.source.timestamp ? <p className="mt-1 text-xs text-[#65715e]">{collaboration.source.timestamp}</p> : null}<p className="mt-4 whitespace-pre-wrap break-words text-sm leading-7">{collaboration.source.bodyText}</p></details>
                <ol aria-label="Messages" className="space-y-4">
                  {collaboration.messages.map((message) => <li key={message.id} className="rounded-2xl border border-[#e1e5da] bg-white p-4"><div className="flex flex-wrap items-center justify-between gap-2 text-xs text-[#65715e]"><span className="font-semibold text-[#344a37]">{message.authorDisplayName}</span><span>{message.visibility === "internal" ? "Internal note" : "Shared reply"}</span></div><p className="mt-2 whitespace-pre-wrap break-words text-sm leading-7">{message.text}</p></li>)}
                </ol>
                {collaboration.messages.length === 0 ? <p className="py-4 text-sm text-[#65715e]">No replies yet.</p> : null}
                {collaboration.state === "resolved" ? <p className="mt-6 text-sm text-[#65715e]">This collaboration is resolved.</p> : <form className="mt-6 border-t border-[#dfe3d8] pt-5" onSubmit={(event) => { event.preventDefault(); void controller.send(draft, visibility).then((sent) => { if (sent) setDraft(""); }); }}>
                  <label className="text-sm font-medium" htmlFor="member-reply-visibility">Reply visibility</label><select id="member-reply-visibility" disabled={snapshot.sending} value={visibility} onChange={(event) => setVisibility(event.target.value as ReplyVisibility)} className="ml-3 rounded-lg border border-[#cbd3c4] bg-white px-3 py-2 text-sm"><option value="internal">Internal note</option><option value="shared">Shared reply</option></select>
                  <p className="mt-2 text-xs text-[#65715e]">{visibility === "internal" ? "Visible to team participants in this collaboration." : "Visible to everyone in this collaboration, including guest reviewers."}</p>
                  <label className="sr-only" htmlFor="member-reply">Your reply</label><textarea id="member-reply" value={draft} onChange={(event) => setDraft(event.target.value)} disabled={snapshot.sending} rows={4} placeholder="Write a reply…" className="mt-3 w-full resize-y rounded-2xl border border-[#cbd3c4] bg-white p-4 text-sm leading-6 focus:outline-none focus:ring-2 focus:ring-[#82997b]" />
                  {snapshot.error ? <p role="alert" className="mt-2 text-sm text-[#92503f]">{snapshot.error}</p> : null}
                  <div className="mt-3 flex justify-end"><button type="submit" disabled={snapshot.sending || !draft.trim()} className={`${buttonClass} !border-[#36583d] !bg-[#36583d] !text-white`}>{snapshot.sending ? "Sending…" : snapshot.error ? "Retry reply" : "Send reply"}</button></div>
                </form>}
              </> : null}
            </section>
          </div>
        )}
      </div>
    </main>
  );
}

function TeamMemberSession({ authenticatedUser }: { authenticatedUser: MemberIdentity }) {
  const [reloadKey, setReloadKey] = useState(0);
  return <TeamMemberWorkspace key={reloadKey} authenticatedUser={authenticatedUser} onReload={() => setReloadKey((current) => current + 1)} />;
}

export function TeamMemberShell({ authenticatedUser }: { authenticatedUser: CuevionSessionUser | null }) {
  if (!isTeamMemberSession(authenticatedUser)) return <main role="alert">Collaboration access is unavailable. Sign in again.</main>;
  return <TeamMemberSession key={JSON.stringify([authenticatedUser.workspaceId, authenticatedUser.userId])} authenticatedUser={authenticatedUser} />;
}
