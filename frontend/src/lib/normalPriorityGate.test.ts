/**
 * Tests for normalPriorityGate.ts.
 *
 * Run with:
 *   cd frontend && node -e "require('./node_modules/sucrase/register/ts.js'); require('./src/lib/normalPriorityGate.test.ts')"
 */

import assert from "node:assert/strict";
import { shouldAllowNormalPriority } from "./normalPriorityGate";
import type {
  NormalPriorityGateInput,
  NormalPriorityGatePrioritySource,
  NormalPriorityGateReturnedReplyEvidence,
} from "./normalPriorityGate";

let passed = 0;
let failed = 0;

function test(name: string, fn: () => void) {
  try {
    fn();
    console.log(`  ✓ ${name}`);
    passed++;
  } catch (err) {
    console.error(`  ✗ ${name}`);
    console.error(`    ${(err as Error).message}`);
    failed++;
  }
}

function source(
  overrides: NormalPriorityGatePrioritySource,
): NormalPriorityGatePrioritySource {
  return {
    level: "priority",
    confidence: "high",
    ...overrides,
  };
}

function returnedReplyEvidence(
  overrides: NormalPriorityGateReturnedReplyEvidence,
): NormalPriorityGateReturnedReplyEvidence {
  return {
    hasEvidence: true,
    confidence: "high",
    ...overrides,
  };
}

function allows(input: NormalPriorityGateInput) {
  return shouldAllowNormalPriority(input);
}

console.log("\nnormalPriorityGate");

for (const noiseDisposition of [
  "strong_spam",
  "unsolicited_low_value",
] as const) {
  test(`${noiseDisposition} blocks every otherwise-concrete Priority source`, () => {
    const concreteInputs: NormalPriorityGateInput[] = [
      {
        noiseDisposition,
        manualOverride: "priority",
        prioritySource: source({ source: "manual" }),
      },
      {
        noiseDisposition,
        prioritySource: source({ source: "learning" }),
      },
      {
        noiseDisposition,
        prioritySource: source({ source: "waiting_on_other" }),
      },
      {
        noiseDisposition,
        prioritySource: source({ source: "returned_reply" }),
        returnedReplyEvidence: returnedReplyEvidence({ confidence: "high" }),
      },
      {
        noiseDisposition,
        prioritySource: source({ level: "normal", source: "collaboration" }),
      },
      {
        noiseDisposition,
        prioritySource: source({ level: "normal", source: "assigned_review" }),
      },
      {
        noiseDisposition,
        prioritySource: source({ source: "strong_system_rule" }),
        isStrongSystemRuleConcreteActionable: true,
      },
      {
        noiseDisposition,
        prioritySource: source({ level: "normal", source: "none" }),
        hasCollaborationContext: true,
        hasAssignedReviewContext: true,
      },
    ];

    concreteInputs.forEach((input) => assert.equal(allows(input), false));
  });
}

test("bulk_marketing does not override an explicit manual Priority decision", () => {
  assert.equal(
    allows({
      noiseDisposition: "bulk_marketing",
      manualOverride: "priority",
      prioritySource: source({ source: "manual" }),
    }),
    true,
  );
});

test("manual source allows Priority", () => {
  assert.equal(
    allows({
      prioritySource: source({ source: "manual" }),
    }),
    true,
  );
});

test("manual removed does not allow Priority", () => {
  assert.equal(
    allows({
      manualOverride: "removed",
      prioritySource: source({ source: "manual" }),
    }),
    false,
  );
});

test("learning source allows Priority", () => {
  assert.equal(
    allows({
      prioritySource: source({ source: "learning" }),
    }),
    true,
  );
});

test("learning low source does not allow Priority", () => {
  assert.equal(
    allows({
      prioritySource: source({ level: "low", source: "learning" }),
    }),
    false,
  );
});

test("waiting_on_other source allows Priority independently of read state", () => {
  assert.equal(
    allows({
      prioritySource: source({ source: "waiting_on_other" }),
    }),
    true,
  );
});

test("manual removed beats waiting_on_other at the gate", () => {
  assert.equal(
    allows({
      manualOverride: "removed",
      prioritySource: source({ source: "waiting_on_other" }),
    }),
    false,
  );
});

test("returned_reply with high confidence allows Priority", () => {
  assert.equal(
    allows({
      prioritySource: source({ source: "returned_reply", confidence: "medium" }),
      returnedReplyEvidence: returnedReplyEvidence({ confidence: "high" }),
    }),
    true,
  );
});

test("returned_reply with medium confidence does not allow Priority", () => {
  assert.equal(
    allows({
      prioritySource: source({ source: "returned_reply" }),
      returnedReplyEvidence: returnedReplyEvidence({ confidence: "medium" }),
    }),
    false,
  );
});

test("returned_reply with low confidence does not allow Priority", () => {
  assert.equal(
    allows({
      prioritySource: source({ source: "returned_reply" }),
      returnedReplyEvidence: returnedReplyEvidence({ confidence: "low" }),
    }),
    false,
  );
});

test("returned_reply with no confidence does not allow Priority", () => {
  assert.equal(
    allows({
      prioritySource: source({ source: "returned_reply" }),
      returnedReplyEvidence: { hasEvidence: false },
    }),
    false,
  );
});

test("collaboration source allows Priority", () => {
  assert.equal(
    allows({
      prioritySource: source({ level: "normal", source: "collaboration" }),
    }),
    true,
  );
});

test("collaboration flag allows Priority", () => {
  assert.equal(
    allows({
      hasCollaborationContext: true,
      prioritySource: source({ level: "normal", source: "none" }),
    }),
    true,
  );
});

test("assigned_review source allows Priority", () => {
  assert.equal(
    allows({
      prioritySource: source({ level: "normal", source: "assigned_review" }),
    }),
    true,
  );
});

test("assigned review flag allows Priority", () => {
  assert.equal(
    allows({
      hasAssignedReviewContext: true,
      prioritySource: source({ level: "normal", source: "none" }),
    }),
    true,
  );
});

test("reply_protection source alone does not allow Priority", () => {
  assert.equal(
    allows({
      prioritySource: source({ level: "normal", source: "reply_protection" }),
    }),
    false,
  );
});

test("reply protection flag alone does not allow Priority", () => {
  assert.equal(
    allows({
      hasReplyProtection: true,
      prioritySource: source({ level: "normal", source: "none" }),
    }),
    false,
  );
});

test("reply protection backed by high-confidence returned_reply evidence allows Priority", () => {
  assert.equal(
    allows({
      hasReplyProtection: true,
      prioritySource: source({ level: "normal", source: "reply_protection" }),
      returnedReplyEvidence: returnedReplyEvidence({ confidence: "high" }),
    }),
    true,
  );
});

test("old visible Reply category alone does not allow Priority", () => {
  assert.equal(
    allows({
      internalClassification: "reply",
      prioritySource: source({ level: "normal", source: "none" }),
    }),
    false,
  );
});

test("backend_visibility alone does not allow Priority", () => {
  assert.equal(
    allows({
      prioritySource: source({ source: "backend_visibility" }),
      currentLegacyPriority: {
        final_visibility: "show_priority",
      },
    }),
    false,
  );
});

test("own-address returned_reply fails closed without another concrete source", () => {
  assert.equal(
    allows({
      isFromOwnAddress: true,
      prioritySource: source({ source: "returned_reply" }),
      returnedReplyEvidence: returnedReplyEvidence({ confidence: "high" }),
    }),
    false,
  );
});

test("own-address manual priority remains allowed", () => {
  assert.equal(
    allows({
      isFromOwnAddress: true,
      manualOverride: "priority",
      prioritySource: source({ source: "manual" }),
    }),
    true,
  );
});

test("own-address collaboration remains allowed", () => {
  assert.equal(
    allows({
      isFromOwnAddress: true,
      prioritySource: source({ level: "normal", source: "collaboration" }),
    }),
    true,
  );
});

test("own-address assigned_review remains allowed", () => {
  assert.equal(
    allows({
      isFromOwnAddress: true,
      prioritySource: source({ level: "normal", source: "assigned_review" }),
    }),
    true,
  );
});

test("ai_heuristic alone does not allow Priority", () => {
  assert.equal(
    allows({
      prioritySource: source({ source: "ai_heuristic" }),
      currentLegacyPriority: {
        priorityScore: "high",
        signal: "Priority",
      },
    }),
    false,
  );
});

test("focus_preference alone does not allow Priority", () => {
  assert.equal(
    allows({
      prioritySource: source({ source: "focus_preference" }),
    }),
    false,
  );
});

test("priorityScore high or legacy visible badge alone does not allow Priority when source is generic", () => {
  assert.equal(
    allows({
      prioritySource: source({ source: "ai_heuristic" }),
      currentLegacyPriority: {
        hasVisiblePriorityBadge: true,
        priorityScore: "high",
      },
    }),
    false,
  );
});

test("signal and final_visibility-style generic Priority is blocked without concrete source", () => {
  assert.equal(
    allows({
      prioritySource: source({ source: "backend_visibility" }),
      currentLegacyPriority: {
        final_visibility: "show_priority",
        signal: "Priority",
        ui_signal: "Priority",
      },
    }),
    false,
  );
});

test("signal and final_visibility-style generic Priority is allowed with concrete source", () => {
  assert.equal(
    allows({
      prioritySource: source({ source: "learning" }),
      currentLegacyPriority: {
        final_visibility: "show_priority",
        signal: "Priority",
      },
    }),
    true,
  );
});

test("strong_system_rule allows Priority when explicitly concrete/actionable", () => {
  assert.equal(
    allows({
      isStrongSystemRuleConcreteActionable: true,
      prioritySource: source({ source: "strong_system_rule" }),
    }),
    true,
  );
});

test("strong_system_rule does not allow Priority without explicit concrete/actionable flag", () => {
  assert.equal(
    allows({
      prioritySource: source({ source: "strong_system_rule" }),
    }),
    false,
  );
});

test("none does not allow Priority", () => {
  assert.equal(
    allows({
      prioritySource: source({ level: "normal", source: "none" }),
    }),
    false,
  );
});

test("demo/high_priority_demo classification alone does not allow normal Priority", () => {
  assert.equal(
    allows({
      internalClassification: "high_priority_demo",
      prioritySource: source({ source: "strong_system_rule" }),
    }),
    false,
  );
});

test("finance classification alone does not allow normal Priority", () => {
  assert.equal(
    allows({
      internalClassification: "finance",
      prioritySource: source({ level: "normal", source: "none" }),
    }),
    false,
  );
});

test("finance classification allows Priority when reinforced by concrete source", () => {
  assert.equal(
    allows({
      internalClassification: "finance",
      prioritySource: source({ level: "normal", source: "assigned_review" }),
    }),
    true,
  );
});

if (failed > 0) {
  console.error(`\n${failed} normalPriorityGate test${failed === 1 ? "" : "s"} failed.`);
  process.exit(1);
}

console.log(`\n${passed} normalPriorityGate tests passed.`);
