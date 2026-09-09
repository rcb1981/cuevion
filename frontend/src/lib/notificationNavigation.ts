import { fetchExactMailboxMessage, matchesExactMailboxMessageIdentity, areExactMailboxSourcesEqual, type ExactMailboxMessageIdentity, type ExactMailboxMessageResponse, type ExactMailboxSourceRef } from "./exactMailboxMessageApi";
import { readCollaborationForOwner, type CollaborationOwnerReadDto } from "./collaborationOwnerReadApi";
import { notificationScopeKey, parseServerNotification, type NotificationScope, type ServerNotification } from "./notificationsApi";
import type { ExactMailboxFolder } from "./exactMailboxMessageStore";

export type ExactNotificationTarget<T extends ExactMailboxMessageIdentity> = { message: T; folder: ExactMailboxFolder };
export type NotificationNavigationTicket = Readonly<{
  notification: ServerNotification; scope: NotificationScope; generation: number; configRevision: number;
  signal: AbortSignal; isCurrent: () => boolean;
}>;
export type NotificationDisplayRequest<T extends ExactMailboxMessageIdentity> = {
  ticket: NotificationNavigationTicket; target: ExactNotificationTarget<T>;
  collaboration: CollaborationOwnerReadDto | null; complete: (displayed: boolean) => void;
};
export function exactNotificationSourceKey(mailboxId: string, source: ExactMailboxSourceRef): string {
  return JSON.stringify(source.provider === "google" ? [mailboxId, "google", source.providerMessageId] : [mailboxId, "custom_imap", source.folder, source.uidValidity, source.imapUid]);
}
/** Rebuilt only when the projection changes, never on mailbox scroll or row render. */
export function buildExactNotificationMessageIndex<T extends ExactMailboxMessageIdentity>(mailboxes: Record<string, Record<ExactMailboxFolder, T[]>>) {
  const index = new Map<string, ExactNotificationTarget<T> | null>();
  for (const [mailboxId, collections] of Object.entries(mailboxes)) {
    const rendererIds = new Map<string, string>();
    const ambiguousIds = new Set<string>();
    for (const [folder, messages] of Object.entries(collections)) for (const message of messages) {
      const source: ExactMailboxSourceRef = message.providerMessageId !== undefined
        ? { provider: "google", providerMessageId: message.providerMessageId }
        : { provider: "custom_imap", folder: "INBOX", uidValidity: message.uidValidity ?? "", imapUid: message.imapUid ?? "" };
      const valid = matchesExactMailboxMessageIdentity(message, mailboxId, source);
      const key = valid ? exactNotificationSourceKey(mailboxId, source) : "";
      const prior = rendererIds.get(message.id);
      if (rendererIds.has(message.id) && prior !== key) ambiguousIds.add(message.id);
      if (valid && !index.has(key)) index.set(key, { message, folder: folder as ExactMailboxFolder });
      rendererIds.set(message.id, key);
    }
    for (const [key, target] of index) if (target?.message.serverMailboxId === mailboxId && ambiguousIds.has(target.message.id)) index.set(key, null);
  }
  return index;
}
export type NotificationNavigationPorts<T extends ExactMailboxMessageIdentity> = {
  getScope: () => NotificationScope | null;
  getMailbox: (id: string) => { provider: string; configRevision: number } | null;
  lookup: (id: string, source: ExactMailboxSourceRef) => ExactNotificationTarget<T> | null | undefined;
  augment: (ticket: NotificationNavigationTicket, response: ExactMailboxMessageResponse) => ExactNotificationTarget<T> | null;
  display: (request: Omit<NotificationDisplayRequest<T>, "complete">) => Promise<boolean>;
  markRead: (id: string, current: () => boolean, signal: AbortSignal) => Promise<boolean>;
  feedback: (message: string | null) => void;
  fetchExact?: typeof fetchExactMailboxMessage;
  readCollaboration?: typeof readCollaborationForOwner;
};
export function createNotificationNavigator<T extends ExactMailboxMessageIdentity>(ports: NotificationNavigationPorts<T>, timeoutMs = 45000) {
  let generation = 0;
  let active: { id: string; key: string; controller: AbortController; promise: Promise<boolean> } | null = null;
  function cancel() { generation++; active?.controller.abort(); active = null; }
  function open(input: ServerNotification): Promise<boolean> {
    const scope = ports.getScope(), key = notificationScopeKey(scope);
    if (active?.id === input.notificationId && active.key === key && !active.controller.signal.aborted) return active.promise;
    cancel();
    const row = scope ? parseServerNotification(input, scope.workspaceId) : null;
    const mailbox = row ? ports.getMailbox(row.mailboxId) : null;
    if (!scope || !row || !mailbox || mailbox.provider !== row.sourceRef.provider) {
      ports.feedback("This notification is no longer available."); return Promise.resolve(false);
    }
    const controller = new AbortController(), requestGeneration = generation;
    const isCurrent = () => !controller.signal.aborted && generation === requestGeneration &&
      notificationScopeKey(ports.getScope()) === key &&
      ports.getMailbox(row.mailboxId)?.provider === row.sourceRef.provider &&
      ports.getMailbox(row.mailboxId)?.configRevision === mailbox.configRevision;
    const ticket: NotificationNavigationTicket = Object.freeze({ notification: row, scope: Object.freeze({ ...scope }), generation: requestGeneration, configRevision: mailbox.configRevision, signal: controller.signal, isCurrent });
    ports.feedback(null);
    const fail = (message = "This notification could not be opened. Please try again.") => { if (isCurrent()) ports.feedback(message); return false; };
    const timer = setTimeout(() => { if (isCurrent()) ports.feedback("This notification could not be opened. Please try again."); controller.abort(); }, timeoutMs);
    const bounded = <V>(operation: Promise<V>): Promise<V> => new Promise((resolve, reject) => {
      const stop = () => reject(new Error("Navigation cancelled"));
      if (controller.signal.aborted) { stop(); return; }
      controller.signal.addEventListener("abort", stop, { once: true });
      operation.then(resolve, reject).finally(() => controller.signal.removeEventListener("abort", stop));
    });
    const promise = Promise.resolve().then(async () => {
      try {
        if (!isCurrent()) return false;
        let target = ports.lookup(row.mailboxId, row.sourceRef);
        if (target === null) return fail(); // Ambiguous loaded identity, never cold-fetch a substitute.
        if (!target) {
          const result = await bounded((ports.fetchExact ?? fetchExactMailboxMessage)(row.mailboxId, row.sourceRef, { signal: controller.signal }));
          if (!isCurrent()) return false;
          if (result.status !== "success") return fail(result.status === "source_changed" ? "The source mailbox has changed. This update is no longer available." : "The source message is no longer available.");
          if (result.response.mailboxId !== row.mailboxId || !areExactMailboxSourcesEqual(result.response.sourceRef, row.sourceRef) ||
            !matchesExactMailboxMessageIdentity(result.response.message, row.mailboxId, row.sourceRef)) return fail();
          target = ports.augment(ticket, result.response);
        }
        if (!isCurrent() || !target || !matchesExactMailboxMessageIdentity(target.message, row.mailboxId, row.sourceRef)) return fail();
        if (!await bounded(ports.display({ ticket, target, collaboration: null })) || !isCurrent()) return fail();
        const result = await bounded((ports.readCollaboration ?? readCollaborationForOwner)(row.collaborationId, { signal: controller.signal, isCurrent, retryForbidden: false }));
        if (!isCurrent()) return false;
        if (result.status !== "success" || result.collaboration.collaborationId !== row.collaborationId || result.collaboration.mailboxId !== row.mailboxId) return fail("This collaboration is no longer available.");
        if (row.activityId !== null && result.collaboration.messages.filter(entry => entry.id === row.activityId).length !== 1) return fail("This update is no longer available.");
        if (!await bounded(ports.display({ ticket, target, collaboration: result.collaboration })) || !isCurrent()) return fail();
        const marked = await bounded(ports.markRead(row.notificationId, isCurrent, controller.signal));
        if (!isCurrent()) return false;
        if (!marked) return fail("The update opened, but could not be marked as read. Please try again.");
        return true;
      } catch { return fail(); }
      finally { clearTimeout(timer); if (active?.controller === controller) active = null; }
    });
    active = { id: row.notificationId, key, controller, promise };
    return promise;
  }
  return { open, cancel };
}
