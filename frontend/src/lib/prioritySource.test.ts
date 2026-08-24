/**
 * Tests for prioritySource.ts.
 *
 * Run with:
 *   cd frontend && node -e "require('./node_modules/sucrase/register/ts.js'); require('./src/lib/prioritySource.test.ts')"
 */

import assert from "node:assert/strict";
import { resolvePrioritySource } from "./prioritySource";

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

console.log("\nprioritySource");

test("manual priority wins", () => {
  const result = resolvePrioritySource({
    manualOverride: "priority",
    message: {
      final_visibility: "show_low",
      priorityScore: "low",
    },
  });

  assert.equal(result.level, "priority");
  assert.equal(result.source, "manual");
  assert.equal(result.confidence, "high");
});

test("manual removed does not return ai heuristic priority", () => {
  const result = resolvePrioritySource({
    manualOverride: "removed",
    message: {
      signal: "Priority",
      priorityScore: "high",
    },
  });

  assert.equal(result.level, "normal");
  assert.equal(result.source, "manual");
});

test("manual removed can preserve current low explanation", () => {
  const result = resolvePrioritySource({
    manualOverride: "removed",
    message: {
      final_visibility: "show_low",
      priorityScore: "low",
    },
  });

  assert.equal(result.level, "low");
  assert.equal(result.source, "manual");
});

test("backend show_priority resolves to backend_visibility", () => {
  const result = resolvePrioritySource({
    message: {
      final_visibility: "show_priority",
      priorityScore: "medium",
    },
  });

  assert.equal(result.level, "priority");
  assert.equal(result.source, "backend_visibility");
  assert.equal(result.confidence, "high");
});

test("reply-protected message resolves to reply_protection", () => {
  const result = resolvePrioritySource({
    message: {
      internalClassification: "reply",
      priorityScore: "medium",
    },
  });

  assert.equal(result.level, "normal");
  assert.equal(result.source, "reply_protection");
});

test("returned reply evidence resolves to returned_reply", () => {
  const result = resolvePrioritySource({
    hasReturnedReplyEvidence: true,
    message: {
      priorityScore: "medium",
    },
  });

  assert.equal(result.level, "priority");
  assert.equal(result.source, "returned_reply");
  assert.equal(result.confidence, "medium");
});

test("waiting_on_other is concrete Priority evidence", () => {
  const result = resolvePrioritySource({
    hasWaitingOnOtherEvidence: true,
    message: {
      priorityScore: "medium",
    },
  });

  assert.equal(result.level, "priority");
  assert.equal(result.source, "waiting_on_other");
  assert.equal(result.confidence, "high");
});

test("new returned reply evidence outranks stale waiting evidence", () => {
  const result = resolvePrioritySource({
    hasWaitingOnOtherEvidence: true,
    hasReturnedReplyEvidence: true,
    message: {
      priorityScore: "medium",
    },
  });

  assert.equal(result.source, "returned_reply");
});

test("waiting_on_other reason outranks learned sender priority", () => {
  const result = resolvePrioritySource({
    hasWaitingOnOtherEvidence: true,
    learnedPrioritySelection: "Important",
    message: {
      priorityScore: "high",
    },
  });

  assert.equal(result.source, "waiting_on_other");
});

test("manual removed beats waiting_on_other", () => {
  const result = resolvePrioritySource({
    manualOverride: "removed",
    hasWaitingOnOtherEvidence: true,
    message: {
      priorityScore: "high",
    },
  });

  assert.equal(result.level, "normal");
  assert.equal(result.source, "manual");
});

test("learning Important remains preference metadata without promoting NORMAL mail", () => {
  const result = resolvePrioritySource({
    learnedPrioritySelection: "Important",
    message: {
      priorityScore: "medium",
    },
  });

  assert.equal(result.level, "normal");
  assert.equal(result.source, "learning");
});

test("returned reply evidence outranks learning Important", () => {
  const result = resolvePrioritySource({
    learnedPrioritySelection: "Important",
    hasReturnedReplyEvidence: true,
    message: {
      priorityScore: "medium",
    },
  });

  assert.equal(result.level, "priority");
  assert.equal(result.source, "returned_reply");
});

test("no signals resolves to none and normal", () => {
  const result = resolvePrioritySource({
    message: {
      priorityScore: "medium",
    },
  });

  assert.equal(result.level, "normal");
  assert.equal(result.source, "none");
});

test("focus preference can be identified as focus_preference", () => {
  const result = resolvePrioritySource({
    focusPreferenceVisibility: "low",
    message: {
      signal: "Priority",
      priorityScore: "high",
    },
  });

  assert.equal(result.level, "low");
  assert.equal(result.source, "focus_preference");
  assert.equal(result.confidence, "high");
});

if (failed > 0) {
  console.error(`\n${failed} prioritySource test${failed === 1 ? "" : "s"} failed.`);
  process.exit(1);
}

console.log(`\n${passed} prioritySource tests passed.`);
