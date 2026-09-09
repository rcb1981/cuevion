import { isExactMailboxSourceRef, type ExactMailboxSourceRef } from "./exactMailboxMessageApi";

export type NotificationKind = "collaboration_started" | "participant_added" | "shared_message" | "internal_note";
export type ServerNotification = Readonly<{
  v: 1; notificationId: string; workspaceId: string; kind: NotificationKind;
  mailboxId: string; sourceRef: ExactMailboxSourceRef; collaborationId: string; activityId: string | null;
  actor: { type: "cuevion_user"; userId: string; displayName: string } | { type: "external_guest"; displayName: string };
  createdAt: number; expiresAt: number; readAt: number | null;
}>;
export type NotificationSummary = { v: 1; unreadCount: number };
export type NotificationPage = NotificationSummary & { notifications: ServerNotification[]; nextCursor: string | null };
export type NotificationRead = NotificationSummary & { notification: ServerNotification };
export type NotificationFailure = "unauthorized" | "forbidden" | "not_found" | "service_unavailable" | "invalid_response" | "aborted";
export type NotificationResult<T> = { status: "success"; value: T } | { status: NotificationFailure };
export type NotificationScope = Readonly<{ accountId: string; workspaceId: string }>;
export const notificationScopeKey = (scope: NotificationScope | null) => scope ? JSON.stringify([scope.accountId, scope.workspaceId]) : "";
export const isNotificationId = (value: unknown): value is string => typeof value === "string" && /^ntf_[0-9a-f]{40}$/.test(value);
const opaque = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9_-]{22,128}$/.test(value);
const user = (value: unknown): value is string => typeof value === "string" && /^usr_[A-Za-z0-9_-]{21}[AQgw]$/.test(value);
const workspace = (value: unknown): value is string => typeof value === "string" && /^wsp_[A-Za-z0-9_-]{22}$/.test(value);
export const validNotificationScope = (scope: NotificationScope | null) => !!scope && user(scope.accountId) && workspace(scope.workspaceId);
function closed(value: unknown, keys: string[]): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwnProperty.call(value, key));
}
const timestamp = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 1577836800000 && value <= 4102444800999;
export const validNotificationCursor = (value: unknown): value is string | null => value === null || (typeof value === "string" && /^[\x00-\x7f]{1,1024}$/.test(value));
export function parseServerNotification(value: unknown, workspaceId: string): ServerNotification | null {
  if (!closed(value, ["v", "notificationId", "workspaceId", "kind", "mailboxId", "sourceRef", "collaborationId", "activityId", "actor", "createdAt", "expiresAt", "readAt"]) ||
    value.v !== 1 || !isNotificationId(value.notificationId) || !workspace(value.workspaceId) || value.workspaceId !== workspaceId ||
    !["collaboration_started", "participant_added", "shared_message", "internal_note"].includes(value.kind as string) ||
    typeof value.mailboxId !== "string" || value.mailboxId.length > 256 || !/^[a-z0-9][a-z0-9._:-]*$/.test(value.mailboxId) ||
    !isExactMailboxSourceRef(value.sourceRef) || !opaque(value.collaborationId) || (value.activityId !== null && !opaque(value.activityId)) ||
    (["shared_message", "internal_note"].includes(value.kind as string) && value.activityId === null) ||
    !timestamp(value.createdAt) || !timestamp(value.expiresAt) || value.expiresAt <= value.createdAt || value.expiresAt > value.createdAt + 180 * 86400000 ||
    (value.readAt !== null && (!timestamp(value.readAt) || value.readAt < value.createdAt || value.readAt >= value.expiresAt))) return null;
  const actor = value.actor;
  if (!actor || typeof actor !== "object") return null;
  const type = (actor as Record<string, unknown>).type;
  if (!closed(actor, type === "cuevion_user" ? ["type", "userId", "displayName"] : ["type", "displayName"]) ||
    (type !== "cuevion_user" && type !== "external_guest") || (type === "cuevion_user" && !user(actor.userId)) ||
    (type === "external_guest" && value.kind !== "shared_message") || typeof actor.displayName !== "string" || !actor.displayName ||
    actor.displayName !== actor.displayName.trim() || /[\p{Cc}\p{Cf}\p{Cs}]/u.test(actor.displayName) || new TextEncoder().encode(actor.displayName).length > 256) return null;
  if (new TextEncoder().encode(JSON.stringify(value)).length > 4096) return null;
  return Object.freeze({ ...value, actor: Object.freeze({ ...actor }), sourceRef: Object.freeze({ ...value.sourceRef }) }) as ServerNotification;
}
function summaryFields(value: Record<string, unknown>) {
  return value.v === 1 && typeof value.unreadCount === "number" && Number.isSafeInteger(value.unreadCount) && value.unreadCount >= 0 && value.unreadCount <= 1000;
}
export function parseNotificationSummary(value: unknown): NotificationSummary | null {
  return closed(value, ["v", "unreadCount"]) && summaryFields(value) ? value as NotificationSummary : null;
}
export function parseNotificationPage(value: unknown, workspaceId: string, cursor: string | null): NotificationPage | null {
  if (!closed(value, ["v", "unreadCount", "notifications", "nextCursor"]) || !summaryFields(value) ||
    !Array.isArray(value.notifications) || value.notifications.length > 50 || !validNotificationCursor(value.nextCursor) ||
    (value.nextCursor !== null && (value.nextCursor === cursor || value.notifications.length === 0))) return null;
  const notifications = value.notifications.map(row => parseServerNotification(row, workspaceId));
  if (notifications.some(row => !row) || new Set(notifications.map(row => row?.notificationId)).size !== notifications.length ||
    notifications.some((row, index) => index > 0 && (row!.createdAt > notifications[index - 1]!.createdAt || (row!.createdAt === notifications[index - 1]!.createdAt && row!.notificationId > notifications[index - 1]!.notificationId)))) return null;
  return { v: 1, unreadCount: value.unreadCount as number, notifications: notifications as ServerNotification[], nextCursor: value.nextCursor };
}
export function parseNotificationRead(value: unknown, workspaceId: string, id: string): NotificationRead | null {
  if (!closed(value, ["v", "unreadCount", "notification"]) || !summaryFields(value)) return null;
  const notification = parseServerNotification(value.notification, workspaceId);
  return notification && notification.notificationId === id && notification.readAt !== null ? { v: 1, unreadCount: value.unreadCount as number, notification } : null;
}
export function notificationCopy(row: ServerNotification): string {
  // Only the server actor snapshot is used. Never expose an address as fallback copy.
  const actor = row.actor.displayName.includes("@") ? "Someone" : row.actor.displayName;
  const action = {
    collaboration_started: "started a collaboration", participant_added: "added you to a collaboration",
    shared_message: row.actor.type === "external_guest" ? "replied to a collaboration" : "added a shared message",
    internal_note: "added an internal note",
  }[row.kind];
  return `${actor} ${action}`;
}
export function createNotificationsApi(fetchImplementation: typeof fetch = (...args) => fetch(...args)) {
  async function request<T>(payload: object, parse: (value: unknown) => T | null, signal?: AbortSignal): Promise<NotificationResult<T>> {
    if (signal?.aborted) return { status: "aborted" };
    try {
      const response = await fetchImplementation("/api/notifications", { method: "POST", credentials: "include", cache: "no-store", headers: { Accept: "application/json", "Content-Type": "application/json" }, body: JSON.stringify(payload), signal });
      if (signal?.aborted) return { status: "aborted" };
      if (!response.ok) return { status: response.status === 401 ? "unauthorized" : response.status === 403 ? "forbidden" : response.status === 404 ? "not_found" : "service_unavailable" };
      const value = parse(await response.json());
      if (signal?.aborted) return { status: "aborted" };
      return value ? { status: "success", value } : { status: "invalid_response" };
    } catch { return { status: signal?.aborted ? "aborted" : "service_unavailable" }; }
  }
  return {
    summary: (signal?: AbortSignal) => request({ operation: "summary" }, parseNotificationSummary, signal),
    list: (workspaceId: string, cursor: string | null, signal?: AbortSignal) => request({ operation: "list", limit: 50, cursor }, value => parseNotificationPage(value, workspaceId, cursor), signal),
    markRead: (workspaceId: string, id: string, signal?: AbortSignal) => request({ operation: "mark_read", notificationId: id }, value => parseNotificationRead(value, workspaceId, id), signal),
  };
}
export type NotificationsApi = ReturnType<typeof createNotificationsApi>;
