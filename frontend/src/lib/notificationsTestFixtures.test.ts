import type { ServerNotification } from "./notificationsApi";
export const scope = { accountId: "usr_" + "A".repeat(22), workspaceId: "wsp_" + "W".repeat(22) };
export const time = 1800000000000;
export const google = { provider: "google", providerMessageId: "provider-1" } as const;
export const imap = { provider: "custom_imap", folder: "INBOX", uidValidity: "23", imapUid: "42" } as const;
export function notification(index = 1, changes: Partial<ServerNotification> = {}): ServerNotification {
  return { v: 1, notificationId: "ntf_" + index.toString(16).padStart(40, "0"), workspaceId: scope.workspaceId, kind: "shared_message", mailboxId: "main", sourceRef: google, collaborationId: "C".repeat(22), activityId: "M".repeat(22), actor: { type: "cuevion_user", userId: "usr_" + "B".repeat(21) + "A", displayName: "Alex" }, createdAt: time - index * 1000, expiresAt: time + 86400000, readAt: null, ...changes };
}
export const page = (rows = [notification()], count = 87, nextCursor: string | null = null) => ({ v: 1 as const, notifications: rows, unreadCount: count, nextCursor });
export const success = <T>(value: T) => ({ status: "success" as const, value });
export function deferred<T = any>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; }
export const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
export function exactResponse(source = google as typeof google | typeof imap) {
  return { v: 1 as const, mailboxId: "main", sourceRef: source, message: {
    id: "renderer-1", serverMailboxId: "main", providerFolder: source.provider === "google" ? "Inbox" : "INBOX",
    sender: "Sender", subject: "Subject", snippet: "Body", from: "a@example.test", to: "b@example.test", cc: "", timestamp: "2027-01-15T08:00:00Z", createdAt: "2027-01-15T08:00:00Z", body: ["Body"], attachments: [], unread: true, flagged: false,
    ...(source.provider === "google" ? { providerMessageId: source.providerMessageId, labelIds: ["INBOX"] } : { uidValidity: source.uidValidity, imapUid: source.imapUid, threadId: "imap-thread" }),
  } };
}
export function collaboration(row = notification()) {
  return { collaborationId: row.collaborationId, mailboxId: row.mailboxId, state: "resolved" as const, createdAt: time, updatedAt: time, source: { subject: "Subject", senderDisplay: "Sender", fromDisplay: "Sender", timestamp: "2027-01-15T08:00:00Z", bodyText: "Body" }, viewerAccess: "owner" as const, externalGuests: [], participants: [{ userId: scope.accountId, displayName: "Owner", access: "owner" as const }], messages: row.activityId ? [{ id: row.activityId, authorDisplayName: "Alex", authorRole: "Cuevion user" as const, text: "Private note", visibility: "internal" as const, timestamp: time }] : [] };
}
