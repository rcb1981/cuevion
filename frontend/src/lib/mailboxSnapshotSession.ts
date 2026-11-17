// Browser cache isolation only. Ownership is supplied exclusively by the
// authenticated /api/auth/session response, never recovered from browser data.
export const LEGACY_MAILBOX_SNAPSHOT_KEY = "cuevion-live-inbox-snapshots";
export const MAILBOX_SNAPSHOT_KEY = "cuevion-live-inbox-snapshots:v1";
export const MAILBOX_FOLDER_SNAPSHOT_KEY = "cuevion-mailbox-folder-snapshots:v1";
export const MAILBOX_SESSION_KEY = "cuevion-mailbox-session:v1";
const LEGACY_FOLDER_PREFIXES = ["cuevion-sent-messages", "cuevion-trash-messages", "cuevion-spam-messages", "cuevion-archive-messages"];

export type MailboxSnapshotOwner = Readonly<{ userId: string; workspaceId: string }>;
export type MailboxSnapshotSession = Readonly<{
  owner: MailboxSnapshotOwner;
  epoch: string;
  persistent: boolean;
  signal: AbortSignal;
  isActive: () => boolean;
}>;

let active: MailboxSnapshotSession | null = null;
let controller: AbortController | null = null;
let revision = 0;
const listeners = new Set<() => void>();
let observedWindow: Window | null = null;

export function sameMailboxSnapshotOwner(a: unknown, b: MailboxSnapshotOwner): boolean {
  if (!a || typeof a !== "object") return false;
  const owner = a as Partial<MailboxSnapshotOwner>;
  return owner.userId === b.userId && owner.workspaceId === b.workspaceId;
}

function storage(): Storage | null {
  try { return typeof window === "undefined" ? null : window.localStorage; }
  catch { return null; }
}

function marker(): string | null {
  try { return storage()?.getItem(MAILBOX_SESSION_KEY) ?? null; }
  catch { return null; }
}

function discardLegacySnapshots() {
  const keys = [LEGACY_MAILBOX_SNAPSHOT_KEY];
  try {
    const target = storage();
    for (let i = 0; target && i < target.length; i += 1) {
      const key = target.key(i);
      if (key && LEGACY_FOLDER_PREFIXES.some((prefix) => key === prefix || key.startsWith(`${prefix}:`))) keys.push(key);
    }
  } catch { /* Unowned payloads are never read even if physical removal fails. */ }
  for (const key of keys) {
    try { storage()?.removeItem(key); } catch { /* Best effort. */ }
  }
}

function discardSnapshots() {
  discardLegacySnapshots();
  for (const key of [MAILBOX_SNAPSHOT_KEY, MAILBOX_FOLDER_SNAPSHOT_KEY]) {
    try { storage()?.removeItem(key); } catch { /* Fail closed even without storage. */ }
  }
}

function revoke() {
  revision += 1;
  active = null;
  controller?.abort();
  controller = null;
}

function publish() { listeners.forEach((listener) => listener()); }

function observeOtherTabs() {
  if (typeof window === "undefined" || observedWindow === window) return;
  observedWindow = window;
  window.addEventListener?.("storage", (event) => {
    if (event.key !== null && event.key !== MAILBOX_SESSION_KEY) return;
    if (active?.isActive()) return;
    // Do not erase the new account's storage when another tab changes account.
    revoke();
    publish();
  });
}

export const getMailboxSnapshotSession = () => active;
export function subscribeMailboxSnapshotSession(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

// A response that started before logout or a cross-tab transition cannot
// reactivate the old account, even if that response arrives last.
export function captureMailboxSessionCheck(): () => boolean {
  observeOtherTabs();
  const expectedRevision = revision;
  const expectedMarker = marker();
  return () => revision === expectedRevision && marker() === expectedMarker;
}

export function activateMailboxSnapshotSession(owner: MailboxSnapshotOwner) {
  observeOtherTabs();
  if (!owner.userId || owner.userId !== owner.userId.trim() ||
      !owner.workspaceId || owner.workspaceId !== owner.workspaceId.trim()) {
    clearMailboxSnapshotSession();
    return;
  }
  if (active?.isActive() && sameMailboxSnapshotOwner(active.owner, owner)) return;
  revoke();
  let storedMarker: { owner?: unknown; epoch?: unknown } | null = null;
  try { storedMarker = JSON.parse(marker() ?? "null"); } catch { /* Untrusted cache. */ }
  const canRestore = sameMailboxSnapshotOwner(storedMarker?.owner, owner) &&
    typeof storedMarker?.epoch === "string" && storedMarker.epoch.length > 0;
  if (!canRestore) discardSnapshots();
  // Never migrate unowned legacy payloads into an authenticated session.
  discardLegacySnapshots();
  const epoch = canRestore ? storedMarker!.epoch as string : crypto.randomUUID();
  const boundOwner = Object.freeze({ userId: owner.userId, workspaceId: owner.workspaceId });
  const expectedMarker = JSON.stringify({ owner: boundOwner, epoch });
  let persistent = false;
  try {
    storage()?.setItem(MAILBOX_SESSION_KEY, expectedMarker);
    persistent = marker() === expectedMarker;
  } catch { /* Live mailbox use remains available without a persistent cache. */ }
  controller = new AbortController();
  const signal = controller.signal;
  const session: MailboxSnapshotSession = Object.freeze({
    owner: boundOwner, epoch, persistent, signal,
    isActive: () => active === session && !signal.aborted &&
      (!persistent || marker() === expectedMarker),
  });
  active = session;
  publish();
}

export function clearMailboxSnapshotSession(expectedSession?: MailboxSnapshotSession | null) {
  // Async failures from an old component cannot clear a newer account.
  if (expectedSession !== undefined && !expectedSession?.isActive()) return;
  // Revoke synchronously, before logout's first await or any cleanup attempt.
  revoke();
  try {
    storage()?.setItem(MAILBOX_SESSION_KEY, JSON.stringify({ owner: null, epoch: crypto.randomUUID() }));
  } catch { /* Local revocation does not depend on browser storage. */ }
  discardSnapshots();
  publish();
}

type MailboxCacheKey = typeof MAILBOX_SNAPSHOT_KEY | typeof MAILBOX_FOLDER_SNAPSHOT_KEY;

export function readOwnedMailboxCache(key: MailboxCacheKey, session: MailboxSnapshotSession | null): Record<string, unknown> | null {
  if (!session?.persistent || !session.isActive()) return null;
  try {
    const value = JSON.parse(storage()?.getItem(key) ?? "null");
    return value && value.version === 1 && value.epoch === session.epoch &&
      sameMailboxSnapshotOwner(value.owner, session.owner) && session.isActive() ? value : null;
  } catch { return null; }
}

export function writeOwnedMailboxCache(key: MailboxCacheKey, data: Record<string, unknown>, session: MailboxSnapshotSession | null) {
  if (!session?.persistent || !session.isActive()) return;
  try {
    const serialized = JSON.stringify({ ...data, version: 1, owner: session.owner, epoch: session.epoch });
    if (!session.isActive()) return;
    storage()?.setItem(key, serialized);
    // Another tab may revoke this epoch while serialization/storage runs.
    if (!session.isActive() && storage()?.getItem(key) === serialized) storage()?.removeItem(key);
  } catch { /* Cache failure never changes provider-confirmed state. */ }
}

export function readMailboxFolderSnapshot(key: string, session: MailboxSnapshotSession | null): string | null {
  const folders = readOwnedMailboxCache(MAILBOX_FOLDER_SNAPSHOT_KEY, session)?.folders;
  if (!folders || typeof folders !== "object" || Array.isArray(folders)) return null;
  const value = Object.prototype.hasOwnProperty.call(folders, key)
    ? (folders as Record<string, unknown>)[key] : null;
  return typeof value === "string" ? value : null;
}

export function saveMailboxFolderSnapshot(key: string, value: string, session: MailboxSnapshotSession | null) {
  if (!LEGACY_FOLDER_PREFIXES.some((prefix) => key.startsWith(`${prefix}:`))) return;
  const stored = readOwnedMailboxCache(MAILBOX_FOLDER_SNAPSHOT_KEY, session)?.folders;
  const folders = stored && typeof stored === "object" && !Array.isArray(stored) ? stored : {};
  writeOwnedMailboxCache(MAILBOX_FOLDER_SNAPSHOT_KEY, { folders: { ...folders, [key]: value } }, session);
}
