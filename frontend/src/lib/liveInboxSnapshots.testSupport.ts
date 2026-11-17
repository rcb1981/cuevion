// Explicitly owned fixtures for the existing classification/thread/Trash
// regressions. Unowned-cache rejection is tested separately against raw storage.
import { readLiveInboxSnapshots, saveLiveInboxSnapshot, type LiveInboxSnapshot, type TrustedLiveInboxSnapshotContexts } from "./liveInboxSnapshots";
import { activateMailboxSnapshotSession, getMailboxSnapshotSession, MAILBOX_SESSION_KEY } from "./mailboxSnapshotSession";

const owner = { userId: "usr_snapshot_fixture", workspaceId: "wsp_snapshot_fixture" };
const epoch = "owned-snapshot-fixture";

export function serializeOwnedSnapshotFixture(snapshots: unknown) {
  return JSON.stringify({ version: 1, owner, epoch, snapshots });
}

function fixtureSession() {
  try { window.localStorage.setItem(MAILBOX_SESSION_KEY, JSON.stringify({ owner, epoch })); } catch { /* Quota test. */ }
  activateMailboxSnapshotSession(owner);
  return getMailboxSnapshotSession();
}

export const readOwnedSnapshotFixture = (contexts?: TrustedLiveInboxSnapshotContexts) =>
  readLiveInboxSnapshots(contexts, fixtureSession());
export const saveOwnedSnapshotFixture = (snapshot: LiveInboxSnapshot) =>
  saveLiveInboxSnapshot(snapshot, fixtureSession());
