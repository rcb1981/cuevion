/**
 * Tests for priorityReasonCopy.ts.
 *
 * Run with:
 *   cd frontend && node -e "require('./node_modules/sucrase/register/ts.js'); require('./src/lib/priorityReasonCopy.test.ts')"
 */

import assert from "node:assert/strict";
import {
  formatPriorityReasonCopy,
  type PriorityReasonCopy,
} from "./priorityReasonCopy";
import type {
  PriorityConfidence,
  PrioritySource,
  PrioritySourceResult,
} from "./prioritySource";

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

const knownSources: PrioritySource[] = [
  "manual",
  "learning",
  "waiting_on_other",
  "returned_reply",
  "collaboration",
  "assigned_review",
  "strong_system_rule",
  "ai_heuristic",
  "focus_preference",
  "backend_visibility",
  "reply_protection",
  "none",
];

const visibleSources: PrioritySource[] = [
  "manual",
  "learning",
  "waiting_on_other",
  "returned_reply",
  "collaboration",
  "assigned_review",
  "reply_protection",
];

const hiddenGenericSources: PrioritySource[] = [
  "backend_visibility",
  "ai_heuristic",
  "strong_system_rule",
  "focus_preference",
];

const forbiddenTerms = [
  "backend_visibility",
  "ai_heuristic",
  "reply_protection",
  "returned_reply",
  "classifier",
  "runtime signal",
  "confidence score",
  "normalized subject",
  "provider threadid",
  "provider thread id",
  "localstorage",
];

function sourceResult(
  source: PrioritySource | string,
  options?: Partial<PrioritySourceResult>,
): PrioritySourceResult {
  return {
    level: "priority",
    source: source as PrioritySource,
    reason: "Internal reason text",
    confidence: "medium",
    ...options,
  };
}

function copyText(copy: PriorityReasonCopy) {
  return [copy.title, copy.detail ?? ""].join(" ").toLowerCase();
}

function assertNoForbiddenTerms(copy: PriorityReasonCopy) {
  const text = copyText(copy);

  forbiddenTerms.forEach((term) => {
    assert.equal(
      text.includes(term),
      false,
      `Unexpected technical term in copy: ${term}`,
    );
  });
}

console.log("\npriorityReasonCopy");

test("every known priority source maps to non-technical copy", () => {
  knownSources.forEach((source) => {
    const copy = formatPriorityReasonCopy({
      prioritySource: sourceResult(source),
    });

    assert.equal(copy.title.length > 0, true);
    assertNoForbiddenTerms(copy);

    assert.equal(copy.shouldShow, visibleSources.includes(source));
  });
});

test("concrete priority sources remain visible", () => {
  visibleSources.forEach((source) => {
    const copy = formatPriorityReasonCopy({
      prioritySource: sourceResult(source),
    });

    assert.equal(copy.shouldShow, true);
    assert.equal(copy.title, copy.title.trim());
    assert.equal(copy.title.length > 0, true);
  });
});

test("generic Cuevion and system sources are hidden", () => {
  hiddenGenericSources.forEach((source) => {
    const copy = formatPriorityReasonCopy({
      prioritySource: sourceResult(source),
    });

    assert.equal(copy.shouldShow, false);
    assert.equal(copy.title, "No clear priority reason yet");
  });
});

test("returned_reply copy does not expose internal source names", () => {
  const copy = formatPriorityReasonCopy({
    prioritySource: sourceResult("returned_reply"),
  });

  assert.equal(copy.shouldShow, true);
  assert.equal(copy.title, "They replied after your last reply");
  assert.equal(copy.detail, "This looks like an active conversation.");
  assertNoForbiddenTerms(copy);
});

test("waiting_on_other copy describes the open loop", () => {
  const copy = formatPriorityReasonCopy({
    prioritySource: sourceResult("waiting_on_other", { confidence: "high" }),
  });

  assert.equal(copy.shouldShow, true);
  assert.equal(copy.title, "Waiting for their reply");
  assert.equal(copy.detail, "You replied and this conversation is still open.");
  assertNoForbiddenTerms(copy);
});

test("none returns shouldShow false", () => {
  const copy = formatPriorityReasonCopy({
    prioritySource: sourceResult("none"),
  });

  assert.equal(copy.shouldShow, false);
  assert.equal(copy.title, "No clear priority reason yet");
});

test("unknown or missing source returns shouldShow false", () => {
  const unknownCopy = formatPriorityReasonCopy({
    prioritySource: sourceResult("mystery_source"),
  });
  const missingCopy = formatPriorityReasonCopy({});
  const nullCopy = formatPriorityReasonCopy(null);

  assert.equal(unknownCopy.shouldShow, false);
  assert.equal(missingCopy.shouldShow, false);
  assert.equal(nullCopy.shouldShow, false);
});

test("normal and low priority levels do not show Priority reason copy", () => {
  const normalCopy = formatPriorityReasonCopy({
    prioritySource: sourceResult("manual", { level: "normal" }),
  });
  const lowCopy = formatPriorityReasonCopy({
    prioritySource: sourceResult("learning", { level: "low" }),
  });

  assert.equal(normalCopy.shouldShow, false);
  assert.equal(lowCopy.shouldShow, false);
});

test("manual priority copy is stable", () => {
  const copy = formatPriorityReasonCopy({
    prioritySource: sourceResult("manual", { confidence: "high" }),
  });

  assert.equal(copy.shouldShow, true);
  assert.equal(copy.title, "Manually marked as priority");
  assert.equal(copy.confidenceLabel, "high" satisfies PriorityConfidence);
});

test("learning copy is stable", () => {
  const copy = formatPriorityReasonCopy({
    prioritySource: sourceResult("learning", { level: "normal" }),
  });

  assert.equal(copy.shouldShow, true);
  assert.equal(copy.title, "Learning preference applied");
  assert.equal(
    copy.detail,
    "Your Learning preference gives messages like this more attention.",
  );
  assert.doesNotMatch(
    copyText(copy),
    /marked important|usually needs a response/,
  );
});

test("reply_protection copy is user-friendly", () => {
  const copy = formatPriorityReasonCopy({
    prioritySource: sourceResult("reply_protection"),
  });

  assert.equal(copy.shouldShow, true);
  assert.equal(copy.title, "Kept visible as an active conversation");
  assertNoForbiddenTerms(copy);
});

test("no output contains forbidden technical terms", () => {
  knownSources.forEach((source) => {
    assertNoForbiddenTerms(
      formatPriorityReasonCopy({
        prioritySource: sourceResult(source),
      }),
    );
  });
});

test("helper does not mutate input", () => {
  const input = {
    prioritySource: sourceResult("returned_reply", {
      confidence: "high",
    }),
  };
  const before = JSON.stringify(input);

  formatPriorityReasonCopy(input);

  assert.equal(JSON.stringify(input), before);
});

if (failed > 0) {
  console.error(`\n${failed} priorityReasonCopy test${failed === 1 ? "" : "s"} failed.`);
  process.exit(1);
}

console.log(`\n${passed} priorityReasonCopy tests passed.`);
