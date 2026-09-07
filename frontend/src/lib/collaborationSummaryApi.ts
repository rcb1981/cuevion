import {
  performAuthenticatedCollaborationOwnerRequest,
  type CollaborationOwnerTransportFailure,
} from "./collaborationOwnerApiTransport";

export const COLLABORATION_SUMMARY_PAGE_SIZE = 50;
export type CollaborationSummaryState = "needs_review" | "needs_action" | "note_only" | "resolved";
export type CollaborationSummarySourceRef =
  | { provider: "google"; providerMessageId: string }
  | { provider: "custom_imap"; folder: "INBOX"; uidValidity: string; imapUid: string };
export type CollaborationSummary = {
  collaborationId: string;
  workspaceId: string;
  mailboxId: string;
  sourceRef: CollaborationSummarySourceRef;
  state: CollaborationSummaryState;
  updatedAt: number;
  viewerAccess: "owner" | "participant";
};
export type CollaborationSummaryPage = {
  v: 1;
  workspaceId: string;
  summaries: CollaborationSummary[];
  nextCursor: string | null;
};
export type CollaborationSummaryResult =
  | { status: "success"; page: CollaborationSummaryPage }
  | { status: "invalid_request" }
  | CollaborationOwnerTransportFailure;

const OPAQUE_ID = /^[A-Za-z0-9_-]{22,128}$/;
const WORKSPACE_ID = /^wsp_[A-Za-z0-9_-]{22}$/;
const MAILBOX_ID = /^[a-z0-9][a-z0-9._:-]{0,255}$/;
const POSITIVE_DECIMAL = /^[1-9][0-9]{0,19}$/;
function exact(value: unknown, keys: readonly string[]): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    && Object.keys(value).length === keys.length && keys.every((key) => Object.prototype.hasOwnProperty.call(value, key));
}
function id(value: unknown): value is string {
  return typeof value === "string" && OPAQUE_ID.test(value);
}
function workspace(value: unknown): value is string {
  return typeof value === "string" && WORKSPACE_ID.test(value);
}
function source(value: unknown): CollaborationSummarySourceRef | null {
  if (exact(value, ["provider", "providerMessageId"]) && value.provider === "google"
    && typeof value.providerMessageId === "string" && value.providerMessageId.length <= 512
    && /^[\x20-\x7e]+$/.test(value.providerMessageId)
    && value.providerMessageId === value.providerMessageId.trim()) {
    return { provider: "google", providerMessageId: value.providerMessageId };
  }
  if (exact(value, ["provider", "folder", "uidValidity", "imapUid"])
    && value.provider === "custom_imap" && value.folder === "INBOX"
    && typeof value.uidValidity === "string" && POSITIVE_DECIMAL.test(value.uidValidity)
    && typeof value.imapUid === "string" && POSITIVE_DECIMAL.test(value.imapUid)) {
    return { provider: "custom_imap", folder: "INBOX", uidValidity: value.uidValidity, imapUid: value.imapUid };
  }
  return null;
}
export function isActiveCollaborationSummary(summary: CollaborationSummary): boolean {
  return summary.state === "needs_review" || summary.state === "needs_action" || summary.state === "note_only";
}
export function parseCollaborationSummary(value: unknown, expectedWorkspaceId: string): CollaborationSummary | null {
  if (!workspace(expectedWorkspaceId)
    || !exact(value, ["collaborationId", "workspaceId", "mailboxId", "sourceRef", "state", "updatedAt", "viewerAccess"])
    || !id(value.collaborationId) || value.workspaceId !== expectedWorkspaceId
    || typeof value.mailboxId !== "string" || !MAILBOX_ID.test(value.mailboxId)
    || (value.state !== "needs_review" && value.state !== "needs_action" && value.state !== "note_only" && value.state !== "resolved")
    || typeof value.updatedAt !== "number" || !Number.isSafeInteger(value.updatedAt)
    || value.updatedAt < 1_577_836_800_000 || value.updatedAt > 4_102_444_800_999
    || (value.viewerAccess !== "owner" && value.viewerAccess !== "participant")) return null;
  const sourceRef = source(value.sourceRef);
  return sourceRef ? {
    collaborationId: value.collaborationId, workspaceId: expectedWorkspaceId, mailboxId: value.mailboxId,
    sourceRef, state: value.state, updatedAt: value.updatedAt, viewerAccess: value.viewerAccess,
  } : null;
}
export function parseCollaborationSummaryPage(
  value: unknown, expectedWorkspaceId: string, cursor: string | null = null,
): CollaborationSummaryPage | null {
  if (!workspace(expectedWorkspaceId) || (cursor !== null && !id(cursor))
    || !exact(value, ["v", "workspaceId", "summaries", "nextCursor"])
    || value.v !== 1 || value.workspaceId !== expectedWorkspaceId
    || !Array.isArray(value.summaries) || value.summaries.length > COLLABORATION_SUMMARY_PAGE_SIZE
    || (value.nextCursor !== null && (!id(value.nextCursor) || value.nextCursor <= (cursor ?? "")))) return null;
  const summaries: CollaborationSummary[] = [];
  let previous = cursor ?? "";
  for (const raw of value.summaries) {
    const summary = parseCollaborationSummary(raw, expectedWorkspaceId);
    if (!summary || summary.collaborationId <= previous) return null;
    previous = summary.collaborationId;
    summaries.push(summary);
  }
  if (value.nextCursor !== null && previous > value.nextCursor) return null;
  return { v: 1, workspaceId: expectedWorkspaceId, summaries, nextCursor: value.nextCursor as string | null };
}

/** One page per explicit fetch. Drain nextCursor to null before treating a set as
 * complete; an empty page can still have a cursor after entitlement filtering.
 * New enrollments require revalidation from cursor=null. No polling or cache. */
export async function listCollaborationSummaries(
  expectedWorkspaceId: string, cursor: string | null = null,
): Promise<CollaborationSummaryResult> {
  if (!workspace(expectedWorkspaceId) || (cursor !== null && !id(cursor))) return { status: "invalid_request" };
  const result = await performAuthenticatedCollaborationOwnerRequest({ operation: "list_summaries", cursor });
  if (result.status !== "response") return result;
  const page = exact(result.payload, ["ok", "data"]) && result.payload.ok === true
    ? parseCollaborationSummaryPage(result.payload.data, expectedWorkspaceId, cursor) : null;
  return page ? { status: "success", page } : { status: "invalid_response" };
}
