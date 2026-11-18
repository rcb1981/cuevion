import { sameMailboxSnapshotOwner, type MailboxSnapshotSession } from "./mailboxSnapshotSession";

export const LEGACY_RECIPIENT_MEMORY_KEY = "cuevion-compose-recipient-memory";
const RECIPIENT_MEMORY_PREFIX = `${LEGACY_RECIPIENT_MEMORY_KEY}:v1:`;
const EMPTY: readonly string[] = Object.freeze([]);
// A failed logout storage write must not revive this epoch later in this page.
const revokedKeys = new Set<string>();

export const normalizeRememberedRecipient = (value: string) => value.trim().toLowerCase();
export const extractRecipientEmails = (value: string) => value.split(/[,;]+/)
  .map(normalizeRememberedRecipient)
  .filter((entry) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(entry));

export function recipientMemoryKey(session: MailboxSnapshotSession) {
  return RECIPIENT_MEMORY_PREFIX + encodeURIComponent(JSON.stringify([
    session.owner.userId, session.owner.workspaceId, session.epoch,
  ]));
}

function storage(): Storage | null {
  try { return typeof window === "undefined" ? null : window.localStorage; }
  catch { return null; }
}

// Never inspect or migrate the unowned addresses, even if removal fails.
export function removeLegacyRecipientMemory(): boolean {
  try {
    const target = storage();
    if (!target) return false;
    target.removeItem(LEGACY_RECIPIENT_MEMORY_KEY);
    return true;
  } catch { return false; }
}

export function createRecipientMemory(session: MailboxSnapshotSession | null) {
  const key = session ? recipientMemoryKey(session) : null;
  let recipients = EMPTY;
  let started = false;
  let enabled = false;
  const listeners = new Set<() => void>();
  const authorized = () => !!session?.persistent && !revokedKeys.has(key!) && session.isActive();
  const getRecipients = () => enabled && authorized() ? recipients : EMPTY;
  const publish = (next: readonly string[]) => {
    recipients = next;
    listeners.forEach((listener) => listener());
  };

  const read = (): readonly string[] => {
    if (!authorized() || !key) return EMPTY;
    const value = JSON.parse(storage()?.getItem(key) ?? "null");
    if (!value || value.version !== 1 || value.epoch !== session!.epoch ||
        !sameMailboxSnapshotOwner(value.owner, session!.owner) ||
        !Array.isArray(value.recipients) || value.recipients.length > 50 ||
        !value.recipients.every((entry: unknown) => typeof entry === "string" &&
          extractRecipientEmails(entry).length === 1 && extractRecipientEmails(entry)[0] === entry) ||
        !authorized()) return EMPTY;
    return Object.freeze([...new Set<string>(value.recipients)]);
  };

  const reload = () => {
    try { publish(enabled ? read() : EMPTY); }
    catch { enabled = false; publish(EMPTY); }
  };

  const onStorage = (event: StorageEvent) => {
    if (event.key === LEGACY_RECIPIENT_MEMORY_KEY && !removeLegacyRecipientMemory()) {
      enabled = false;
      publish(EMPTY);
    } else if (event.key === key) {
      try {
        // Removal in another tab also revokes history if its session-marker
        // write failed. An old pending send must not recreate the deleted copy.
        if (storage()?.getItem(key!) === null) revoke();
        else reload();
      } catch { enabled = false; publish(EMPTY); }
    }
  };

  const revoke = () => {
    enabled = false;
    if (key) revokedKeys.add(key);
    // This key belongs only to the captured epoch; never delete a newer session's copy.
    try { if (key) storage()?.removeItem(key); } catch { /* Access stays revoked. */ }
    if (typeof window !== "undefined") window.removeEventListener("storage", onStorage);
    publish(EMPTY);
  };

  const start = () => {
    if (started) return;
    started = true;
    const legacyRemoved = removeLegacyRecipientMemory();
    if (!authorized()) return;
    enabled = legacyRemoved;
    // Keep revocation cleanup alive across ordinary Compose/mailbox unmounts.
    session!.signal.addEventListener("abort", revoke, { once: true });
    if (typeof window !== "undefined") window.addEventListener("storage", onStorage);
    reload();
  };

  const remember = (values: Array<string | undefined>) => {
    if (!enabled || !authorized() || !key) return;
    const next = values.flatMap((value) => extractRecipientEmails(value ?? ""));
    if (!next.length) return;
    try {
      const merged = Object.freeze([...new Set([...next, ...read(), ...recipients])].slice(0, 50));
      const serialized = JSON.stringify({ version: 1, owner: session!.owner, epoch: session!.epoch, recipients: merged });
      if (!authorized()) return;
      storage()?.setItem(key, serialized);
      if (!authorized()) {
        if (storage()?.getItem(key) === serialized) storage()?.removeItem(key);
        return;
      }
      publish(merged);
    } catch {
      // Best-effort history must never affect provider-confirmed send handling.
      enabled = false;
      publish(EMPTY);
    }
  };

  return {
    start, getRecipients, remember,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
  };
}

const stores = new WeakMap<MailboxSnapshotSession, ReturnType<typeof createRecipientMemory>>();
const unowned = createRecipientMemory(null);

export function getRecipientMemory(session: MailboxSnapshotSession | null) {
  if (!session) return unowned;
  let store = stores.get(session);
  if (!store) { store = createRecipientMemory(session); stores.set(session, store); }
  return store;
}
