/**
 * Tests for priorityRuntimeAdapter.ts.
 *
 * Run with:
 *   cd frontend && node -e "require('./node_modules/sucrase/register/ts.js'); require('./src/lib/priorityRuntimeAdapter.test.ts')"
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  buildReturnedReplyEvidenceInput,
  isSubjectDerivedThreadId,
  resolveRuntimePrioritySource,
  resolveRuntimeReturnedReplyEvidence,
  type RuntimePriorityMessageLike,
} from "./priorityRuntimeAdapter";

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

function message(overrides: Partial<RuntimePriorityMessageLike>): RuntimePriorityMessageLike {
  return {
    id: "message-1",
    subject: "Re: Licensing question",
    from: "partner@example.com",
    sender: "Partner",
    to: "me@cuevion.com",
    createdAt: "2026-07-02T12:00:00.000Z",
    timestamp: "2026-07-02T12:00:00.000Z",
    priorityScore: "medium",
    ...overrides,
  };
}

const ownEmailAddresses = ["me@cuevion.com"];

console.log("\npriorityRuntimeAdapter");

test("explicit provider threadId gives high returned-reply confidence", () => {
  const evidence = resolveRuntimeReturnedReplyEvidence({
    ownEmailAddresses,
    currentMessage: message({
      id: "inbound-1",
      threadId: "provider-thread-1",
    }),
    sentMessages: [
      message({
        id: "sent-1",
        threadId: "provider-thread-1",
        from: "me@cuevion.com",
        to: "partner@example.com",
        createdAt: "2026-07-02T10:00:00.000Z",
      }),
    ],
  });

  assert.equal(evidence.hasEvidence, true);
  assert.equal(evidence.confidence, "high");
});

test("subject-derived threadId is downgraded and cannot become high confidence", () => {
  const currentMessage = message({
    id: "inbound-1",
    threadId: "licensing question",
  });
  const sentMessage = message({
    id: "sent-1",
    threadId: "licensing question",
    subject: "Licensing question",
    from: "me@cuevion.com",
    to: "partner@example.com",
    createdAt: "2026-07-02T10:00:00.000Z",
  });
  const input = buildReturnedReplyEvidenceInput({
    ownEmailAddresses,
    currentMessage,
    sentMessages: [sentMessage],
  });
  const evidence = resolveRuntimeReturnedReplyEvidence({
    ownEmailAddresses,
    currentMessage,
    sentMessages: [sentMessage],
  });

  assert.equal(isSubjectDerivedThreadId(currentMessage), true);
  assert.equal(input.currentMessage.threadId, undefined);
  assert.equal(input.sentMessages?.[0]?.threadId, undefined);
  assert.equal(evidence.hasEvidence, true);
  assert.equal(evidence.confidence, "medium");
});

test("subject fallback can become medium only with participant evidence", () => {
  const evidence = resolveRuntimeReturnedReplyEvidence({
    ownEmailAddresses,
    currentMessage: message({
      id: "inbound-1",
      threadId: undefined,
      from: "partner@example.com",
    }),
    sentMessages: [
      message({
        id: "sent-1",
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

test("subject fallback without participant evidence fails closed", () => {
  const evidence = resolveRuntimeReturnedReplyEvidence({
    ownEmailAddresses,
    currentMessage: message({
      id: "inbound-1",
      threadId: undefined,
      from: "other@example.com",
    }),
    sentMessages: [
      message({
        id: "sent-1",
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

test("empty own-address set fails closed", () => {
  const evidence = resolveRuntimeReturnedReplyEvidence({
    ownEmailAddresses: [],
    currentMessage: message({
      id: "inbound-1",
      threadId: "provider-thread-1",
    }),
    sentMessages: [
      message({
        id: "sent-1",
        threadId: "provider-thread-1",
        from: "me@cuevion.com",
        to: "partner@example.com",
        createdAt: "2026-07-02T10:00:00.000Z",
      }),
    ],
  });

  assert.equal(evidence.hasEvidence, false);
  assert.match(evidence.reason, /own email/i);
});

test("inbound from an own address fails closed", () => {
  const evidence = resolveRuntimeReturnedReplyEvidence({
    ownEmailAddresses,
    currentMessage: message({
      id: "inbound-1",
      threadId: "provider-thread-1",
      from: "Me <me@cuevion.com>",
    }),
    sentMessages: [
      message({
        id: "sent-1",
        threadId: "provider-thread-1",
        from: "me@cuevion.com",
        to: "partner@example.com",
        createdAt: "2026-07-02T10:00:00.000Z",
      }),
    ],
  });

  assert.equal(evidence.hasEvidence, false);
  assert.match(evidence.reason, /own addresses/i);
});

test("inbound before the sent message fails closed", () => {
  const evidence = resolveRuntimeReturnedReplyEvidence({
    ownEmailAddresses,
    currentMessage: message({
      id: "inbound-1",
      threadId: "provider-thread-1",
      createdAt: "2026-07-02T09:00:00.000Z",
    }),
    sentMessages: [
      message({
        id: "sent-1",
        threadId: "provider-thread-1",
        from: "me@cuevion.com",
        to: "partner@example.com",
        createdAt: "2026-07-02T10:00:00.000Z",
      }),
    ],
  });

  assert.equal(evidence.hasEvidence, false);
});

test("missing or invalid timestamps fail closed", () => {
  const evidence = resolveRuntimeReturnedReplyEvidence({
    ownEmailAddresses,
    currentMessage: message({
      id: "inbound-1",
      threadId: "provider-thread-1",
      createdAt: "not-a-date",
      timestamp: "also-not-a-date",
    }),
    sentMessages: [
      message({
        id: "sent-1",
        threadId: "provider-thread-1",
        from: "me@cuevion.com",
        to: "partner@example.com",
        createdAt: "2026-07-02T10:00:00.000Z",
      }),
    ],
  });

  assert.equal(evidence.hasEvidence, false);
  assert.match(evidence.reason, /timestamp/i);
});

test("imapUid alone never creates thread evidence", () => {
  const evidence = resolveRuntimeReturnedReplyEvidence({
    ownEmailAddresses,
    currentMessage: message({
      id: "inbound-1",
      imapUid: "123",
      threadId: undefined,
      subject: "Re: Licensing question",
      from: "partner@example.com",
    }),
    sentMessages: [
      message({
        id: "sent-1",
        imapUid: "123",
        threadId: undefined,
        subject: "Different question",
        from: "me@cuevion.com",
        to: "partner@example.com",
        createdAt: "2026-07-02T10:00:00.000Z",
      }),
    ],
  });

  assert.equal(evidence.hasEvidence, false);
});

test("prioritySource returns returned_reply only when returned evidence is explicitly passed", () => {
  const evidence = resolveRuntimeReturnedReplyEvidence({
    ownEmailAddresses,
    currentMessage: message({
      id: "inbound-1",
      threadId: "provider-thread-1",
    }),
    sentMessages: [
      message({
        id: "sent-1",
        threadId: "provider-thread-1",
        from: "me@cuevion.com",
        to: "partner@example.com",
        createdAt: "2026-07-02T10:00:00.000Z",
      }),
    ],
  });
  const withoutEvidence = resolveRuntimePrioritySource({
    message: {
      priorityScore: "medium",
    },
  });
  const withEvidence = resolveRuntimePrioritySource({
    returnedReplyEvidence: evidence,
    message: {
      priorityScore: "medium",
    },
  });

  assert.equal(withoutEvidence.source, "none");
  assert.equal(withEvidence.source, "returned_reply");
});

test("adapter is not imported into WorkspaceShell runtime", () => {
  const workspaceShellSource = readFileSync(
    "src/components/workspace/WorkspaceShell.tsx",
    "utf8",
  );

  assert.equal(workspaceShellSource.includes("priorityRuntimeAdapter"), false);
});

if (failed > 0) {
  console.error(`\n${failed} priorityRuntimeAdapter test${failed === 1 ? "" : "s"} failed.`);
  process.exit(1);
}

console.log(`\n${passed} priorityRuntimeAdapter tests passed.`);
