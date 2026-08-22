import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  rankPriorityItems,
  resolvePriorityWorkState,
  type PriorityChronologicalSortOrder,
  type PriorityWorkStateByItemId,
} from "./priorityRanking";

type TestItem = {
  id: string;
  updatedAt: string;
  createdAt: string;
};

const item = (
  id: string,
  updatedAt: string,
  createdAt = updatedAt,
): TestItem => ({ id, updatedAt, createdAt });

const items = [
  item("waiting-new", "2026-08-22T12:00:00.000Z"),
  item("actionable-old", "2026-08-19T12:00:00.000Z"),
  item("neutral-old", "2026-08-18T12:00:00.000Z"),
  item("actionable-new", "2026-08-21T12:00:00.000Z"),
  item("waiting-old", "2026-08-20T12:00:00.000Z"),
  item("neutral-new", "invalid", "2026-08-23T12:00:00.000Z"),
];
const workStateByItemId: PriorityWorkStateByItemId = {
  "actionable-old": "needs_user_action",
  "actionable-new": "needs_user_action",
  "waiting-old": "waiting_on_other",
  "waiting-new": "waiting_on_other",
};
const getTimestamp = (entry: TestItem) => {
  const updatedAt = Date.parse(entry.updatedAt);

  return Number.isNaN(updatedAt) ? Date.parse(entry.createdAt) || 0 : updatedAt;
};
const rankedIds = (sortOrder: PriorityChronologicalSortOrder) =>
  rankPriorityItems(items, {
    sortOrder,
    workStateByItemId,
    getItemId: (entry) => entry.id,
    getTimestamp,
  }).map(({ id }) => id);

assert.deepEqual(rankedIds("newest"), [
  "actionable-new",
  "actionable-old",
  "neutral-new",
  "neutral-old",
  "waiting-new",
  "waiting-old",
]);
assert.deepEqual(rankedIds("oldest"), [
  "actionable-old",
  "actionable-new",
  "neutral-old",
  "neutral-new",
  "waiting-old",
  "waiting-new",
]);
assert.deepEqual(
  items.map(({ id }) => id),
  [
    "waiting-new",
    "actionable-old",
    "neutral-old",
    "actionable-new",
    "waiting-old",
    "neutral-new",
  ],
  "ranking must not mutate canonical membership order",
);
assert.equal(new Set(rankedIds("newest")).size, items.length);
assert.equal(resolvePriorityWorkState({
  hasSemanticNeedsUserAction: true,
  hasReturnedReplyEvidence: false,
  hasWaitingOnOtherEvidence: false,
}), "needs_user_action");
assert.equal(resolvePriorityWorkState({
  hasSemanticNeedsUserAction: false,
  hasReturnedReplyEvidence: true,
  hasWaitingOnOtherEvidence: false,
}), "needs_user_action");
assert.equal(resolvePriorityWorkState({
  hasSemanticNeedsUserAction: false,
  hasReturnedReplyEvidence: false,
  hasWaitingOnOtherEvidence: true,
}), "waiting_on_other");
assert.equal(resolvePriorityWorkState({
  hasSemanticNeedsUserAction: true,
  hasReturnedReplyEvidence: true,
  hasWaitingOnOtherEvidence: true,
}), "waiting_on_other", "authoritative waiting state must win conflicting evidence");
assert.equal(resolvePriorityWorkState({
  hasSemanticNeedsUserAction: false,
  hasReturnedReplyEvidence: false,
  hasWaitingOnOtherEvidence: false,
}), "other_priority");

const stableTieItems = [
  item("first", "2026-08-22T12:00:00.000Z"),
  item("second", "2026-08-22T12:00:00.000Z"),
];
assert.deepEqual(
  rankPriorityItems(stableTieItems, {
    sortOrder: "newest",
    workStateByItemId: {},
    getItemId: (entry) => entry.id,
    getTimestamp,
  }).map(({ id }) => id),
  ["first", "second"],
  "exact ties must preserve canonical order",
);

const workspaceSource = readFileSync(
  "src/components/workspace/WorkspaceShell.tsx",
  "utf8",
);
const canonicalMembershipStart = workspaceSource.indexOf(
  "const livePriorityInboxEntries =",
);
const rankingMetadataStart = workspaceSource.indexOf(
  "const priorityWorkStateByReviewItemId =",
  canonicalMembershipStart,
);
const rankingMetadataEnd = workspaceSource.indexOf(
  "const resolveExactPrioritySemanticNewInboundObservation =",
  rankingMetadataStart,
);
const rankingMetadataSource = workspaceSource.slice(
  rankingMetadataStart,
  rankingMetadataEnd,
);

assert.ok(
  canonicalMembershipStart >= 0 &&
    rankingMetadataStart > canonicalMembershipStart &&
    rankingMetadataEnd > rankingMetadataStart,
  "ranking metadata must be derived after canonical Priority membership",
);
assert.match(
  rankingMetadataSource,
  /livePriorityInboxEntries\.map[\s\S]*?resolveWaitingOnOtherState[\s\S]*?returnedReplyEvidence\?\.hasEvidence[\s\S]*?confidence === "high"[\s\S]*?priorityEffect === "promote_new_inbound"[\s\S]*?assessment\.state === "needs_user_action"[\s\S]*?meetsPrioritySemanticNewInboundPromotionThreshold/,
  "ranking must use only authoritative waiting, returned-reply, and promoted semantic state",
);
assert.doesNotMatch(
  rankingMetadataSource,
  /\.filter\(|\.concat\(|\.push\(|dedupe|subject|snippet|unread|senderCategoryLearning|resolveOrganizerCategory/,
  "ranking metadata must not alter membership or infer work state heuristically",
);
assert.match(
  workspaceSource,
  /supplementalItems=\{livePriorityInboxItems\}[\s\S]*?priorityWorkStateByItemId=\{priorityWorkStateByReviewItemId\}/,
  "the canonical Priority list and its ranking metadata must enter ReviewModule separately",
);

const reviewModuleSource = readFileSync(
  "src/components/workspace/review/ReviewModule.tsx",
  "utf8",
);
assert.match(
  reviewModuleSource,
  /rankPriorityItems\([\s\S]*?sortOrder: prioritySortOrder[\s\S]*?getTimestamp: getReviewItemSortTimestamp/,
  "ReviewModule must retain its selected chronology inside work-state tiers",
);
assert.match(
  reviewModuleSource,
  /Date\.parse\(item\.updatedAt\)[\s\S]*?Date\.parse\(item\.createdAt\)/,
  "existing updatedAt-first, createdAt-fallback timestamp semantics must remain",
);
assert.match(reviewModuleSource, /"Newest first"/);
assert.match(reviewModuleSource, /"Oldest first"/);

console.log("\nWorkspaceShell Priority ranking tests passed.");
