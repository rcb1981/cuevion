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

test("strong_spam disposition is projected and blocks manual Priority", () => {
  const input = build({
    manualOverride: "priority",
    message: {
      noiseDisposition: "strong_spam",
      noiseConfidence: "high",
      noiseReasons: ["provider_spam_evidence"],
    },
  });

  assert.equal(input.noiseDisposition, "strong_spam");
  assert.equal(input.prioritySource?.source, "manual");
  assert.equal(shouldAllowNormalPriority(input), false);
});

test("unsolicited_low_value option is projected and blocks collaboration", () => {
  const input = build({
    noiseDisposition: "unsolicited_low_value",
    message: {
      isShared: true,
    },
  });

  assert.equal(input.noiseDisposition, "unsolicited_low_value");
  assert.equal(input.prioritySource?.source, "collaboration");
  assert.equal(shouldAllowNormalPriority(input), false);
});

test("invalid noise disposition fails closed to no assessment", () => {
  const input = build({
    message: {
      noiseDisposition: "arbitrary-provider-value",
    } as any,
  });

  assert.equal(input.noiseDisposition, null);
});

test("partial assessment with a valid blocking disposition remains neutral", () => {
  const input = build({
    manualOverride: "priority",
    message: {
      noiseDisposition: "strong_spam",
    },
  });

  assert.equal(input.noiseDisposition, null);
  assert.equal(shouldAllowNormalPriority(input), true);
});

test("legacy message without noise assessment remains neutral", () => {
  const input = build({ message: { subject: "Legacy message" } });

  assert.equal(input.noiseDisposition, null);
});

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

test("reply internal classification preserves reply_protection but does not allow by itself", () => {
  const input = build({
    message: {
      internalClassification: "reply",
    },
  });

  assert.equal(input.prioritySource?.source, "reply_protection");
  assert.equal(input.hasReplyProtection, false);
  assert.equal(shouldAllowNormalPriority(input), false);
});

test("subject Re: alone does not allow normal Priority", () => {
  const input = build({
    message: {
      subject: "Re: ZG / New unsigned tracks for Hysteria",
    },
  });

  assert.equal(input.prioritySource?.source, "none");
  assert.equal(shouldAllowNormalPriority(input), false);
});

test("subject Fwd: alone does not allow normal Priority", () => {
  const input = build({
    message: {
      subject: "Fwd: NEW DEMO",
    },
  });

  assert.equal(input.prioritySource?.source, "none");
  assert.equal(shouldAllowNormalPriority(input), false);
});

test("subject Fw: alone does not allow normal Priority", () => {
  const input = build({
    message: {
      subject: "Fw: NEW DEMO",
    },
  });

  assert.equal(input.prioritySource?.source, "none");
  assert.equal(shouldAllowNormalPriority(input), false);
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

test("demo/high_priority_demo reply-like message without high-confidence returned reply does not allow normal Priority", () => {
  const input = build({
    message: {
      subject: "Re: ZG / New unsigned tracks for Hysteria",
      internalClassification: "high_priority_demo",
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

test("forwarded demo from own connected inbox address does not allow normal Priority", () => {
  const input = build({
    message: {
      from: "info@hysteriarecs.com",
      subject: "Fwd: NEW DEMO",
      internalClassification: "demo",
      signal: "Priority",
      priorityScore: "high",
    },
    ownEmailAddresses: ["info@hysteriarecs.com"],
  });

  assert.equal(input.isFromOwnAddress, true);
  assert.equal(input.prioritySource?.source, "ai_heuristic");
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

test("own-address high-confidence returned_reply evidence fails closed", () => {
  const input = build({
    message: {
      from: "Me <me@cuevion.com>",
      subject: "Re: Licensing question",
    },
    ownEmailAddresses: ["me@cuevion.com"],
    returnedReplyEvidence: {
      hasEvidence: true,
      confidence: "high",
    },
  });

  assert.equal(input.isFromOwnAddress, true);
  assert.equal(input.prioritySource?.source, "returned_reply");
  assert.equal(shouldAllowNormalPriority(input), false);
});

test("own-address manual priority remains allowed", () => {
  const input = build({
    manualOverride: "priority",
    message: {
      from: "me@cuevion.com",
      subject: "Fwd: NEW DEMO",
    },
    ownEmailAddresses: ["me@cuevion.com"],
  });

  assert.equal(input.isFromOwnAddress, true);
  assert.equal(shouldAllowNormalPriority(input), true);
});

test("own-address collaboration remains allowed", () => {
  const input = build({
    message: {
      from: "me@cuevion.com",
      isShared: true,
    },
    ownEmailAddresses: ["me@cuevion.com"],
  });

  assert.equal(input.isFromOwnAddress, true);
  assert.equal(input.prioritySource?.source, "collaboration");
  assert.equal(shouldAllowNormalPriority(input), true);
});

test("own-address assigned_review remains allowed", () => {
  const input = build({
    message: {
      from: "me@cuevion.com",
      assignedReviewId: "review-1",
    },
    ownEmailAddresses: ["me@cuevion.com"],
  });

  assert.equal(input.isFromOwnAddress, true);
  assert.equal(input.prioritySource?.source, "assigned_review");
  assert.equal(shouldAllowNormalPriority(input), true);
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

test("explicit reply protection flag is preserved with generic runtime source but does not allow by itself", () => {
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
  assert.equal(shouldAllowNormalPriority(input), false);
});

test("explicit reply protection flag allows with high-confidence returned_reply evidence", () => {
  const input = build({
    message: {
      hasReplyProtection: true,
    },
    runtimeSignal: {
      prioritySource: {
        level: "priority",
        source: "reply_protection",
        confidence: "medium",
      },
      returnedReplyEvidence: {
        hasEvidence: true,
        confidence: "high",
      },
    },
  });

  assert.equal(input.prioritySource?.source, "reply_protection");
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
