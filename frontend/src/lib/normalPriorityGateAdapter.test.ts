/**
 * Tests for normalPriorityGateAdapter.ts.
 *
 * Run with:
 *   cd frontend && node -e "require('./node_modules/sucrase/register/ts.js'); require('./src/lib/normalPriorityGateAdapter.test.ts')"
 */

import assert from "node:assert/strict";
import { shouldAllowNormalPriority } from "./normalPriorityGate";
import {
  buildNormalPriorityGateInput,
  type BuildNormalPriorityGateInputOptions,
} from "./normalPriorityGateAdapter";

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

function build(options: BuildNormalPriorityGateInputOptions) {
  return buildNormalPriorityGateInput(options);
}

function allows(options: BuildNormalPriorityGateInputOptions) {
  return shouldAllowNormalPriority(build(options));
}

console.log("\nnormalPriorityGateAdapter");

test("manual priority override maps to allowed gate input", () => {
  const input = build({
    manualOverride: "priority",
  });

  assert.equal(input.manualOverride, "priority");
  assert.equal(input.prioritySource?.source, "manual");
  assert.equal(input.prioritySource?.level, "priority");
  assert.equal(shouldAllowNormalPriority(input), true);
});

test("manual removed override produces non-allowed gate input", () => {
  const input = build({
    manualOverride: "removed",
    message: {
      signal: "Priority",
      priorityScore: "high",
    },
  });

  assert.equal(input.manualOverride, "removed");
  assert.equal(input.prioritySource?.source, "manual");
  assert.equal(shouldAllowNormalPriority(input), false);
});

test("runtime source manual is preserved", () => {
  const input = build({
    runtimeSignal: {
      prioritySource: {
        level: "priority",
        source: "manual",
        confidence: "high",
      },
    },
  });

  assert.equal(input.prioritySource?.source, "manual");
  assert.equal(input.prioritySource?.confidence, "high");
  assert.equal(shouldAllowNormalPriority(input), true);
});

test("runtime source learning is preserved", () => {
  const input = build({
    runtimeSignal: {
      prioritySource: {
        level: "priority",
        source: "learning",
        confidence: "high",
      },
    },
  });

  assert.equal(input.prioritySource?.source, "learning");
  assert.equal(input.prioritySource?.level, "priority");
  assert.equal(shouldAllowNormalPriority(input), true);
});

test("high-confidence returned_reply evidence maps to allowed gate input", () => {
  const input = build({
    returnedReplyEvidence: {
      hasEvidence: true,
      confidence: "high",
    },
  });

  assert.equal(input.prioritySource?.source, "returned_reply");
  assert.equal(input.returnedReplyEvidence?.confidence, "high");
  assert.equal(shouldAllowNormalPriority(input), true);
});

test("medium returned_reply evidence does not become high-confidence", () => {
  const input = build({
    returnedReplyEvidence: {
      hasEvidence: true,
      confidence: "medium",
    },
  });

  assert.equal(input.prioritySource?.source, "returned_reply");
  assert.equal(input.returnedReplyEvidence?.confidence, "medium");
  assert.equal(shouldAllowNormalPriority(input), false);
});

test("low returned_reply evidence does not become high-confidence", () => {
  const input = build({
    returnedReplyEvidence: {
      hasEvidence: true,
      confidence: "low",
    },
  });

  assert.equal(input.prioritySource?.source, "returned_reply");
  assert.equal(input.returnedReplyEvidence?.confidence, "low");
  assert.equal(shouldAllowNormalPriority(input), false);
});

test("no returned_reply evidence does not become high-confidence", () => {
  const input = build({
    returnedReplyEvidence: {
      hasEvidence: false,
      confidence: "low",
    },
  });

  assert.equal(input.prioritySource?.source, "none");
  assert.equal(input.returnedReplyEvidence?.hasEvidence, false);
  assert.equal(shouldAllowNormalPriority(input), false);
});

test("collaboration state maps to collaboration", () => {
  const input = build({
    message: {
      isShared: true,
    },
  });

  assert.equal(input.prioritySource?.source, "collaboration");
  assert.equal(input.hasCollaborationContext, true);
  assert.equal(shouldAllowNormalPriority(input), true);
});

test("assigned review state maps to assigned_review", () => {
  const input = build({
    message: {
      assignedReviewId: "review-1",
    },
  });

  assert.equal(input.prioritySource?.source, "assigned_review");
  assert.equal(input.hasAssignedReviewContext, true);
  assert.equal(shouldAllowNormalPriority(input), true);
});

test("review state maps to assigned_review", () => {
  const input = build({
    message: {
      reviewStatus: "assigned",
    },
  });

  assert.equal(input.prioritySource?.source, "assigned_review");
  assert.equal(input.hasAssignedReviewContext, true);
  assert.equal(shouldAllowNormalPriority(input), true);
});

test("reply internal classification can preserve reply_protection where appropriate", () => {
  const input = build({
    message: {
      internalClassification: "reply",
    },
  });

  assert.equal(input.prioritySource?.source, "reply_protection");
  assert.equal(input.hasReplyProtection, false);
  assert.equal(shouldAllowNormalPriority(input), true);
});

test("final_visibility show_priority alone maps to backend_visibility, not concrete", () => {
  const input = build({
    message: {
      final_visibility: "show_priority",
    },
  });

  assert.equal(input.prioritySource?.source, "backend_visibility");
  assert.equal(input.currentLegacyPriority?.final_visibility, "show_priority");
  assert.equal(shouldAllowNormalPriority(input), false);
});

test("action show_in_priority alone maps to backend_visibility, not concrete", () => {
  const input = build({
    message: {
      action: "show_in_priority",
    },
  });

  assert.equal(input.prioritySource?.source, "backend_visibility");
  assert.equal(input.currentLegacyPriority?.action, "show_in_priority");
  assert.equal(shouldAllowNormalPriority(input), false);
});

test("signal Priority alone maps to ai_heuristic, not concrete", () => {
  const input = build({
    message: {
      signal: "Priority",
    },
  });

  assert.equal(input.prioritySource?.source, "ai_heuristic");
  assert.equal(input.currentLegacyPriority?.signal, "Priority");
  assert.equal(shouldAllowNormalPriority(input), false);
});

test("priorityScore high alone maps to ai_heuristic, not concrete", () => {
  const input = build({
    message: {
      priorityScore: "high",
    },
  });

  assert.equal(input.prioritySource?.source, "ai_heuristic");
  assert.equal(input.currentLegacyPriority?.priorityScore, "high");
  assert.equal(shouldAllowNormalPriority(input), false);
});

test("demo/high_priority_demo alone does not become concrete normal Priority", () => {
  const input = build({
    message: {
      internalClassification: "high_priority_demo",
    },
  });

  assert.equal(input.prioritySource?.source, "strong_system_rule");
  assert.equal(input.isStrongSystemRuleConcreteActionable, false);
  assert.equal(shouldAllowNormalPriority(input), false);
});

test("finance alone does not become concrete normal Priority", () => {
  const input = build({
    message: {
      internalClassification: "finance",
    },
  });

  assert.equal(input.prioritySource?.source, "none");
  assert.equal(shouldAllowNormalPriority(input), false);
});

test("subject fallback does not become returned_reply high confidence", () => {
  const input = build({
    message: {
      subject: "Re: Licensing question",
      threadId: "licensing question",
    },
    returnedReplyEvidence: {
      hasEvidence: true,
      confidence: "medium",
    },
  });

  assert.equal(input.prioritySource?.source, "returned_reply");
  assert.equal(input.returnedReplyEvidence?.confidence, "medium");
  assert.equal(shouldAllowNormalPriority(input), false);
});

test("normalized thread subject alone does not create returned_reply evidence", () => {
  const input = build({
    message: {
      subject: "Re: Licensing question",
      threadId: "licensing question",
    },
  });

  assert.equal(input.prioritySource?.source, "none");
  assert.equal(input.returnedReplyEvidence, null);
  assert.equal(shouldAllowNormalPriority(input), false);
});

test("explicit generic runtime source is preserved over collaboration-looking message", () => {
  const input = build({
    message: {
      isShared: true,
    },
    runtimeSignal: {
      prioritySource: {
        level: "priority",
        source: "ai_heuristic",
        confidence: "low",
      },
    },
  });

  assert.equal(input.prioritySource?.source, "ai_heuristic");
  assert.equal(input.hasCollaborationContext, true);
  assert.equal(shouldAllowNormalPriority(input), true);
});

test("explicit reply protection flag is preserved with generic runtime source", () => {
  const input = build({
    message: {
      hasReplyProtection: true,
    },
    runtimeSignal: {
      prioritySource: {
        level: "priority",
        source: "backend_visibility",
        confidence: "high",
      },
    },
  });

  assert.equal(input.prioritySource?.source, "backend_visibility");
  assert.equal(input.hasReplyProtection, true);
  assert.equal(shouldAllowNormalPriority(input), true);
});

if (failed > 0) {
  console.error(
    `\n${failed} normalPriorityGateAdapter test${failed === 1 ? "" : "s"} failed.`,
  );
  process.exit(1);
}

console.log(`\n${passed} normalPriorityGateAdapter tests passed.`);
