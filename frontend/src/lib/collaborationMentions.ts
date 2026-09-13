import type { CollaborationMention, CollaborationOwnerReadParticipant } from "./collaborationOwnerReadApi";

export type CollaborationMentionDraft = Readonly<{
  text: string;
  mentions: readonly Readonly<CollaborationMention>[];
}>;
export type CollaborationMentionCandidate = Pick<CollaborationOwnerReadParticipant, "userId" | "displayName">;
export type CollaborationMentionQuery = { start: number; end: number; query: string };
export const MAX_COLLABORATION_MENTIONS = 32;
export const EMPTY_COLLABORATION_MENTION_DRAFT: CollaborationMentionDraft = Object.freeze({ text: "", mentions: Object.freeze([]) });

const compareText = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0;
export function orderCollaborationMentions(mentions: readonly Readonly<CollaborationMention>[]) {
  return mentions.map(({ userId, start, end, displayText }) => ({ userId, start, end, displayText }))
    .sort((a, b) => a.start - b.start || a.end - b.end || compareText(a.userId, b.userId));
}

// This accepts only the authenticated reader's entitlement-filtered participant
// projection. External guests and the workspace roster are separate DTO fields.
export function collaborationMentionCandidates(participants: readonly CollaborationOwnerReadParticipant[]): CollaborationMentionCandidate[] {
  const seen = new Set<string>();
  return participants.filter(participant => {
    if ((participant.access !== "owner" && participant.access !== "participant") ||
        !participant.userId || !participant.displayName.trim() || seen.has(participant.userId)) return false;
    seen.add(participant.userId);
    return true;
  }).map(({ userId, displayName }) => ({ userId, displayName }));
}

export function collaborationMentionQuery(text: string, caret: number, selectionEnd = caret): CollaborationMentionQuery | null {
  if (!Number.isInteger(caret) || caret < 0 || caret > text.length || selectionEnd !== caret) return null;
  // Scan one bounded token; never scan the message or infer an identity on Send.
  const lowerBound = Math.max(0, caret - 128);
  for (let index = caret - 1; index >= lowerBound; index -= 1) {
    const character = text[index];
    if (character === "@") {
      if (index > 0 && !/[\s([{]/u.test(text[index - 1])) return null;
      return { start: index, end: caret, query: text.slice(index + 1, caret) };
    }
    if (!/[\p{L}\p{M}\p{N}._'-]/u.test(character)) return null;
  }
  return null;
}

export function filterCollaborationMentionCandidates(candidates: readonly CollaborationMentionCandidate[], query: string) {
  const needle = query.toLowerCase();
  return candidates.filter(candidate => candidate.displayName.toLowerCase().includes(needle))
    .sort((a, b) => Number(b.displayName.toLowerCase().startsWith(needle)) - Number(a.displayName.toLowerCase().startsWith(needle)) ||
      compareText(a.displayName.toLowerCase(), b.displayName.toLowerCase()) || compareText(a.userId, b.userId))
    .slice(0, 8);
}

function isUtf16Boundary(text: string, index: number) {
  return !(index > 0 && index < text.length &&
    text.charCodeAt(index - 1) >= 0xd800 && text.charCodeAt(index - 1) <= 0xdbff &&
    text.charCodeAt(index) >= 0xdc00 && text.charCodeAt(index) <= 0xdfff);
}

function usableMentions(text: string, mentions: readonly Readonly<CollaborationMention>[]) {
  if (!Array.isArray(mentions) || mentions.length > MAX_COLLABORATION_MENTIONS) return null;
  for (const mention of mentions) {
    if (!mention || typeof mention.userId !== "string" || !mention.userId ||
    typeof mention.displayText !== "string" || !mention.displayText.startsWith("@") || mention.displayText.length < 2 ||
    !Number.isInteger(mention.start) || !Number.isInteger(mention.end) || mention.start < 0 ||
    mention.end <= mention.start || mention.end > text.length ||
    !isUtf16Boundary(text, mention.start) || !isUtf16Boundary(text, mention.end) ||
    text.slice(mention.start, mention.end) !== mention.displayText) return null;
  }
  const ordered = orderCollaborationMentions(mentions);
  if (ordered.some((mention, index) => index > 0 && mention.start < ordered[index - 1].end)) return null;
  return ordered;
}

function reconcileWindow(draft: CollaborationMentionDraft, text: string, start: number, end: number, replacementLength: number): CollaborationMentionDraft {
  const delta = replacementLength - (end - start);
  const mentions = (usableMentions(draft.text, draft.mentions) ?? []).flatMap(mention => {
    if (end <= mention.start) return [{ ...mention, start: mention.start + delta, end: mention.end + delta }];
    if (start >= mention.end) return [mention];
    return []; // Every interior insertion or overlap invalidates the whole span.
  });
  return { text, mentions: usableMentions(text, mentions) ?? [] };
}

export function reconcileCollaborationMentionDraft(draft: CollaborationMentionDraft, text: string): CollaborationMentionDraft {
  if (draft.text === text) return draft;
  let start = 0;
  while (start < draft.text.length && start < text.length && draft.text[start] === text[start]) start += 1;
  let end = draft.text.length;
  let nextEnd = text.length;
  while (end > start && nextEnd > start && draft.text[end - 1] === text[nextEnd - 1]) { end -= 1; nextEnd -= 1; }
  return reconcileWindow(draft, text, start, end, nextEnd - start);
}

export function insertCollaborationMention(draft: CollaborationMentionDraft, query: CollaborationMentionQuery, candidate: CollaborationMentionCandidate) {
  const active = collaborationMentionQuery(draft.text, query.end);
  if (!active || active.start !== query.start || active.query !== query.query || !candidate.userId || !candidate.displayName.trim()) return null;
  const displayText = `@${candidate.displayName}`;
  const following = draft.text[query.end];
  const insertion = displayText + (following && /[\s.,!?;:)\]}]/u.test(following) ? "" : " ");
  const text = draft.text.slice(0, query.start) + insertion + draft.text.slice(query.end);
  const reconciled = reconcileWindow(draft, text, query.start, query.end, insertion.length);
  if (reconciled.mentions.length >= MAX_COLLABORATION_MENTIONS) return null;
  const mentions = orderCollaborationMentions([...reconciled.mentions, {
    userId: candidate.userId, start: query.start, end: query.start + displayText.length, displayText,
  }]);
  return { draft: { text, mentions } satisfies CollaborationMentionDraft, caret: query.start + insertion.length };
}

export function collaborationMentionKey(key: string, index: number, count: number) {
  if (count === 0) return null;
  if (key === "ArrowDown") return { action: "move" as const, index: (index + 1) % count };
  if (key === "ArrowUp") return { action: "move" as const, index: (index + count - 1) % count };
  if (key === "Enter" || key === "Tab") return { action: "select" as const, index };
  if (key === "Escape") return { action: "close" as const, index };
  return null;
}

export function segmentCollaborationMentions(text: string, mentions?: readonly Readonly<CollaborationMention>[]) {
  const plain = [{ text, mention: null }];
  if (!mentions?.length) return plain;
  const ordered = usableMentions(text, mentions);
  if (!ordered) return plain;
  const segments: { text: string; mention: CollaborationMention | null }[] = [];
  let cursor = 0;
  for (const mention of ordered) {
    if (mention.start > cursor) segments.push({ text: text.slice(cursor, mention.start), mention: null });
    segments.push({ text: text.slice(mention.start, mention.end), mention });
    cursor = mention.end;
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor), mention: null });
  return segments;
}
