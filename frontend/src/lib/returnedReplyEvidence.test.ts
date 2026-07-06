/**
 * Tests for returnedReplyEvidence.ts.
 *
 * Run with:
 *   cd frontend && node -e "require('./node_modules/sucrase/register/ts.js'); require('./src/lib/returnedReplyEvidence.test.ts')"
 */

import assert from "node:assert/strict";
import { resolvePrioritySource } from "./prioritySource";
import { resolveReturnedReplyEvidence, type ReturnedReplyMessageLike } from "./returnedReplyEvidence";

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

function message(overrides: Partial<ReturnedReplyMessageLike>): ReturnedReplyMessageLike {
  return {
    subject: "Re: Licensing question",
    from: "partner@example.com",
    createdAt: "2026-07-02T12:00:00.000Z",
    ...overrides,
  };
}

const ownEmailAddresses = ["me@cuevion.com", "alias@cuevion.com"];

console.log("\nreturnedReplyEvidence");

test("returns high confidence for explicit threadId match after local sent reply", () => {
  const evidence = resolveReturnedReplyEvidence({
    ownEmailAddresses,
    currentMessage: message({
      threadId: "thread-1",
      from: "partner@example.com",
      createdAt: "2026-07-02T12:00:00.000Z",
    }),
    sentMessages: [
      message({
        threadId: "thread-1",
        from: "me@cuevion.com",
        to: "partner@example.com",
        createdAt: "2026-07-02T10:00:00.000Z",
      }),
    ],
  });

  assert.equal(evidence.hasEvidence, true);
  assert.equal(evidence.confidence, "high");
  assert.equal(evidence.lastUserReplyAt, "2026-07-02T10:00:00.000Z");
  assert.equal(evidence.returnedReplyAt, "2026-07-02T12:00:00.000Z");
});

test("returns no evidence when no sent message matches thread or participant-backed subject", () => {
  const evidence = resolveReturnedReplyEvidence({
    ownEmailAddresses,
    currentMessage: message({
      threadId: "thread-1",
    }),
    sentMessages: [
      message({
        threadId: "thread-2",
        from: "me@cuevion.com",
        to: "other@example.com",
        subject: "Different question",
        createdAt: "2026-07-02T10:00:00.000Z",
      }),
    ],
  });

  assert.equal(evidence.hasEvidence, false);
});

test("returns no evidence when inbound message is before the sent message", () => {
  const evidence = resolveReturnedReplyEvidence({
    ownEmailAddresses,
    currentMessage: message({
      threadId: "thread-1",
      createdAt: "2026-07-02T09:00:00.000Z",
    }),
    sentMessages: [
      message({
        threadId: "thread-1",
        from: "me@cuevion.com",
        to: "partner@example.com",
        createdAt: "2026-07-02T10:00:00.000Z",
      }),
    ],
  });

  assert.equal(evidence.hasEvidence, false);
});

test("returns no evidence when inbound sender is an own address", () => {
  const evidence = resolveReturnedReplyEvidence({
    ownEmailAddresses,
    currentMessage: message({
      threadId: "thread-1",
      from: "Alias <alias@cuevion.com>",
      createdAt: "2026-07-02T12:00:00.000Z",
    }),
    sentMessages: [
      message({
        threadId: "thread-1",
        from: "me@cuevion.com",
        to: "partner@example.com",
        createdAt: "2026-07-02T10:00:00.000Z",
      }),
    ],
  });

  assert.equal(evidence.hasEvidence, false);
  assert.match(evidence.reason, /own addresses/i);
});

test("returns medium confidence for normalized subject plus participant fallback", () => {
  const evidence = resolveReturnedReplyEvidence({
    ownEmailAddresses,
    currentMessage: message({
      threadId: undefined,
      subject: "Re: Licensing question",
      from: "partner@example.com",
      createdAt: "2026-07-02T12:00:00.000Z",
    }),
    sentMessages: [
      message({
        threadId: undefined,
        subject: "Licensing question",
        from: "me@cuevion.com",
        to: "Partner <partner@example.com>",
        createdAt: "2026-07-02T10:00:00.000Z",
      }),
    ],
  });

  assert.equal(evidence.hasEvidence, true);
  assert.equal(evidence.confidence, "medium");
});

test("does not infer returned reply from subject alone", () => {
  const evidence = resolveReturnedReplyEvidence({
    ownEmailAddresses,
    currentMessage: message({
      threadId: undefined,
      subject: "Re: Licensing question",
      from: "other@example.com",
      createdAt: "2026-07-02T12:00:00.000Z",
    }),
    sentMessages: [
      message({
        threadId: undefined,
        subject: "Licensing question",
        from: "me@cuevion.com",
        to: "partner@example.com",
        createdAt: "2026-07-02T10:00:00.000Z",
      }),
    ],
  });

  assert.equal(evidence.hasEvidence, false);
});

test("invalid current timestamp is conservative", () => {
  const evidence = resolveReturnedReplyEvidence({
    ownEmailAddresses,
    currentMessage: message({
      threadId: "thread-1",
      createdAt: "not-a-date",
      timestamp: "also-not-a-date",
    }),
    sentMessages: [
      message({
        threadId: "thread-1",
        from: "me@cuevion.com",
        to: "partner@example.com",
        createdAt: "2026-07-02T10:00:00.000Z",
      }),
    ],
  });

  assert.equal(evidence.hasEvidence, false);
  assert.match(evidence.reason, /timestamp/i);
});

test("missing own addresses are conservative", () => {
  const evidence = resolveReturnedReplyEvidence({
    ownEmailAddresses: [],
    currentMessage: message({
      threadId: "thread-1",
    }),
    sentMessages: [
      message({
        threadId: "thread-1",
        from: "me@cuevion.com",
        to: "partner@example.com",
        createdAt: "2026-07-02T10:00:00.000Z",
      }),
    ],
  });

  assert.equal(evidence.hasEvidence, false);
  assert.match(evidence.reason, /own email/i);
});

test("returned evidence feeds prioritySource only when explicitly passed", () => {
  const evidence = resolveReturnedReplyEvidence({
    ownEmailAddresses,
    currentMessage: message({
      threadId: "thread-1",
      from: "partner@example.com",
      createdAt: "2026-07-02T12:00:00.000Z",
    }),
    sentMessages: [
      message({
        threadId: "thread-1",
        from: "me@cuevion.com",
        to: "partner@example.com",
        createdAt: "2026-07-02T10:00:00.000Z",
      }),
    ],
  });

  const withoutEvidence = resolvePrioritySource({
    message: {
      priorityScore: "medium",
    },
  });
  const withEvidence = resolvePrioritySource({
    hasReturnedReplyEvidence: evidence.hasEvidence,
    message: {
      priorityScore: "medium",
    },
  });

  assert.equal(withoutEvidence.source, "none");
  assert.equal(withEvidence.source, "returned_reply");
  assert.equal(withEvidence.level, "priority");
});

if (failed > 0) {
  console.error(`\n${failed} returnedReplyEvidence test${failed === 1 ? "" : "s"} failed.`);
  process.exit(1);
}

console.log(`\n${passed} returnedReplyEvidence tests passed.`);
