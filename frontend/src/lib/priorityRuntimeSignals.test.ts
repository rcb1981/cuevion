/**
 * Tests for priorityRuntimeSignals.ts.
 *
 * Run with:
 *   cd frontend && node -e "require('./node_modules/sucrase/register/ts.js'); require('./src/lib/priorityRuntimeSignals.test.ts')"
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  buildPriorityRuntimeSignalsForCandidates,
  type PriorityRuntimeCandidateMessage,
} from "./priorityRuntimeSignals";
import type { RuntimePriorityMessageLike } from "./priorityRuntimeAdapter";

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

function message(
  overrides: Partial<PriorityRuntimeCandidateMessage>,
): PriorityRuntimeCandidateMessage {
  return {
    id: "message-1",
    mailboxId: "main",
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

function sentMessage(
  overrides: Partial<RuntimePriorityMessageLike>,
): RuntimePriorityMessageLike {
  return {
    id: "sent-1",
    subject: "Licensing question",
    from: "me@cuevion.com",
    sender: "You",
    to: "partner@example.com",
    createdAt: "2026-07-02T10:00:00.000Z",
    timestamp: "2026-07-02T10:00:00.000Z",
    signal: "Sent",
    priorityScore: "medium",
    ...overrides,
  };
}

const ownEmailAddresses = ["me@cuevion.com"];

console.log("\npriorityRuntimeSignals");

test("batch selector returns one signal per candidate", () => {
  const signals = buildPriorityRuntimeSignalsForCandidates({
    ownEmailAddresses,
    candidateMessages: [
      message({ id: "inbound-1", threadId: "provider-thread-1" }),
      message({ id: "inbound-2", threadId: "provider-thread-2" }),
    ],
    sentMessagesByMailboxId: {
      main: [
        sentMessage({
          id: "sent-1",
          threadId: "provider-thread-1",
        }),
      ],
    },
  });

  assert.deepEqual(Object.keys(signals), ["main:inbound-1", "main:inbound-2"]);
  assert.equal(signals["main:inbound-1"].messageKey, "main:inbound-1");
  assert.equal(signals["main:inbound-2"].messageKey, "main:inbound-2");
});

test("explicit provider threadId can produce high returned-reply evidence", () => {
  const signals = buildPriorityRuntimeSignalsForCandidates({
    ownEmailAddresses,
    candidateMessages: [
      message({
        id: "inbound-1",
        threadId: "provider-thread-1",
      }),
    ],
    sentMessagesByMailboxId: {
      main: [
        sentMessage({
          id: "sent-1",
          threadId: "provider-thread-1",
        }),
      ],
    },
  });

  assert.equal(signals["main:inbound-1"].returnedReplyEvidence.hasEvidence, true);
  assert.equal(signals["main:inbound-1"].returnedReplyEvidence.confidence, "high");
});

test("normalized-subject threadId fallback is downgraded and cannot produce high confidence", () => {
  const signals = buildPriorityRuntimeSignalsForCandidates({
    ownEmailAddresses,
    candidateMessages: [
      message({
        id: "inbound-1",
        threadId: "licensing question",
      }),
    ],
    sentMessagesByMailboxId: {
      main: [
        sentMessage({
          id: "sent-1",
          threadId: "licensing question",
          to: "partner@example.com",
        }),
      ],
    },
  });

  assert.equal(signals["main:inbound-1"].returnedReplyEvidence.hasEvidence, true);
  assert.equal(signals["main:inbound-1"].returnedReplyEvidence.confidence, "medium");
});

test("subject fallback can produce medium confidence with participant evidence", () => {
  const signals = buildPriorityRuntimeSignalsForCandidates({
    ownEmailAddresses,
    candidateMessages: [
      message({
        id: "inbound-1",
        threadId: undefined,
        from: "partner@example.com",
      }),
    ],
    sentMessagesByMailboxId: {
      main: [
        sentMessage({
          id: "sent-1",
          threadId: undefined,
          to: "Partner <partner@example.com>",
        }),
      ],
    },
  });

  assert.equal(signals["main:inbound-1"].returnedReplyEvidence.hasEvidence, true);
  assert.equal(signals["main:inbound-1"].returnedReplyEvidence.confidence, "medium");
});

test("subject fallback without participant evidence fails closed", () => {
  const signals = buildPriorityRuntimeSignalsForCandidates({
    ownEmailAddresses,
    candidateMessages: [
      message({
        id: "inbound-1",
        threadId: undefined,
        from: "other@example.com",
      }),
    ],
    sentMessagesByMailboxId: {
      main: [
        sentMessage({
          id: "sent-1",
          threadId: undefined,
          to: "partner@example.com",
        }),
      ],
    },
  });

  assert.equal(signals["main:inbound-1"].returnedReplyEvidence.hasEvidence, false);
});

test("empty own-address set returns no returned-reply evidence for all candidates", () => {
  const signals = buildPriorityRuntimeSignalsForCandidates({
    ownEmailAddresses: [],
    candidateMessages: [
      message({ id: "inbound-1", threadId: "provider-thread-1" }),
      message({ id: "inbound-2", threadId: "provider-thread-1" }),
    ],
    sentMessagesByMailboxId: {
      main: [
        sentMessage({
          id: "sent-1",
          threadId: "provider-thread-1",
        }),
      ],
    },
  });

  assert.equal(signals["main:inbound-1"].returnedReplyEvidence.hasEvidence, false);
  assert.equal(signals["main:inbound-2"].returnedReplyEvidence.hasEvidence, false);
});

test("inbound from own address fails closed", () => {
  const signals = buildPriorityRuntimeSignalsForCandidates({
    ownEmailAddresses,
    candidateMessages: [
      message({
        id: "inbound-1",
        threadId: "provider-thread-1",
        from: "Me <me@cuevion.com>",
      }),
    ],
    sentMessagesByMailboxId: {
      main: [
        sentMessage({
          id: "sent-1",
          threadId: "provider-thread-1",
        }),
      ],
    },
  });

  assert.equal(signals["main:inbound-1"].returnedReplyEvidence.hasEvidence, false);
});

test("inbound before sent message fails closed", () => {
  const signals = buildPriorityRuntimeSignalsForCandidates({
    ownEmailAddresses,
    candidateMessages: [
      message({
        id: "inbound-1",
        threadId: "provider-thread-1",
        createdAt: "2026-07-02T09:00:00.000Z",
      }),
    ],
    sentMessagesByMailboxId: {
      main: [
        sentMessage({
          id: "sent-1",
          threadId: "provider-thread-1",
          createdAt: "2026-07-02T10:00:00.000Z",
        }),
      ],
    },
  });

  assert.equal(signals["main:inbound-1"].returnedReplyEvidence.hasEvidence, false);
});

test("invalid timestamps fail closed", () => {
  const signals = buildPriorityRuntimeSignalsForCandidates({
    ownEmailAddresses,
    candidateMessages: [
      message({
        id: "inbound-1",
        threadId: "provider-thread-1",
        createdAt: "not-a-date",
        timestamp: "also-not-a-date",
      }),
    ],
    sentMessagesByMailboxId: {
      main: [
        sentMessage({
          id: "sent-1",
          threadId: "provider-thread-1",
        }),
      ],
    },
  });

  assert.equal(signals["main:inbound-1"].returnedReplyEvidence.hasEvidence, false);
});

test("imapUid-only similarity fails closed", () => {
  const signals = buildPriorityRuntimeSignalsForCandidates({
    ownEmailAddresses,
    candidateMessages: [
      message({
        id: "inbound-1",
        imapUid: "123",
        threadId: undefined,
        subject: "Re: Licensing question",
      }),
    ],
    sentMessagesByMailboxId: {
      main: [
        sentMessage({
          id: "sent-1",
          imapUid: "123",
          threadId: undefined,
          subject: "Different question",
        }),
      ],
    },
  });

  assert.equal(signals["main:inbound-1"].returnedReplyEvidence.hasEvidence, false);
});

test("prioritySource returns returned_reply only when returned-reply evidence is present", () => {
  const signals = buildPriorityRuntimeSignalsForCandidates({
    ownEmailAddresses,
    candidateMessages: [
      message({ id: "inbound-1", threadId: "provider-thread-1" }),
      message({ id: "inbound-2", threadId: "provider-thread-2" }),
    ],
    sentMessagesByMailboxId: {
      main: [
        sentMessage({
          id: "sent-1",
          threadId: "provider-thread-1",
        }),
      ],
    },
  });

  assert.equal(signals["main:inbound-1"].prioritySource.source, "returned_reply");
  assert.equal(signals["main:inbound-2"].prioritySource.source, "none");
});

test("persisted waiting transition supplies high returned-reply evidence without Sent history", () => {
  const signals = buildPriorityRuntimeSignalsForCandidates({
    ownEmailAddresses,
    candidateMessages: [
      message({ id: "inbound-1", threadId: "provider-thread-1" }),
    ],
    returnedReplyEvidenceByMessageKey: {
      "main:inbound-1": {
        hasEvidence: true,
        confidence: "high",
        reason: "Authoritative waiting transition.",
        lastUserReplyAt: "2026-07-02T10:00:00.000Z",
        returnedReplyAt: "2026-07-02T12:00:00.000Z",
      },
    },
  });

  assert.equal(signals["main:inbound-1"].returnedReplyEvidence.hasEvidence, true);
  assert.equal(signals["main:inbound-1"].returnedReplyEvidence.confidence, "high");
  assert.equal(signals["main:inbound-1"].prioritySource.source, "returned_reply");
});

test("high waiting-transition evidence wins over medium subject fallback", () => {
  const signals = buildPriorityRuntimeSignalsForCandidates({
    ownEmailAddresses,
    candidateMessages: [
      message({
        id: "inbound-1",
        threadId: "licensing question",
      }),
    ],
    sentMessagesByMailboxId: {
      main: [
        sentMessage({
          id: "sent-1",
          threadId: "licensing question",
          to: "partner@example.com",
        }),
      ],
    },
    returnedReplyEvidenceByMessageKey: {
      "main:inbound-1": {
        hasEvidence: true,
        confidence: "high",
        reason: "Authoritative waiting transition.",
      },
    },
  });

  assert.equal(signals["main:inbound-1"].returnedReplyEvidence.confidence, "high");
  assert.equal(
    signals["main:inbound-1"].returnedReplyEvidence.reason,
    "Authoritative waiting transition.",
  );
});

test("waiting state is projected as a concrete runtime Priority source", () => {
  const signals = buildPriorityRuntimeSignalsForCandidates({
    ownEmailAddresses,
    candidateMessages: [
      message({ id: "inbound-1", threadId: "provider-thread-1" }),
    ],
    waitingOnOtherByMessageKey: {
      "main:inbound-1": true,
    },
  });

  assert.equal(signals["main:inbound-1"].prioritySource.source, "waiting_on_other");
  assert.equal(signals["main:inbound-1"].prioritySource.level, "priority");
});

test("manual removed beats runtime waiting state", () => {
  const signals = buildPriorityRuntimeSignalsForCandidates({
    ownEmailAddresses,
    candidateMessages: [
      message({ id: "inbound-1", threadId: "provider-thread-1" }),
    ],
    manualPriorityOverrides: {
      "main:inbound-1": "removed",
    },
    waitingOnOtherByMessageKey: {
      "main:inbound-1": true,
    },
  });

  assert.equal(signals["main:inbound-1"].prioritySource.source, "manual");
  assert.equal(signals["main:inbound-1"].prioritySource.level, "normal");
});

test("manual removed beats persisted returned-reply evidence", () => {
  const signals = buildPriorityRuntimeSignalsForCandidates({
    ownEmailAddresses,
    candidateMessages: [
      message({ id: "inbound-1", threadId: "provider-thread-1" }),
    ],
    manualPriorityOverrides: {
      "main:inbound-1": "removed",
    },
    returnedReplyEvidenceByMessageKey: {
      "main:inbound-1": {
        hasEvidence: true,
        confidence: "high",
        reason: "Authoritative waiting transition.",
      },
    },
  });

  assert.equal(signals["main:inbound-1"].prioritySource.source, "manual");
  assert.equal(signals["main:inbound-1"].prioritySource.level, "normal");
});

test("manual priority source takes precedence over returned_reply", () => {
  const signals = buildPriorityRuntimeSignalsForCandidates({
    ownEmailAddresses,
    candidateMessages: [
      message({
        id: "inbound-1",
        threadId: "provider-thread-1",
      }),
    ],
    manualPriorityOverrides: {
      "main:inbound-1": "priority",
    },
    sentMessagesByMailboxId: {
      main: [
        sentMessage({
          id: "sent-1",
          threadId: "provider-thread-1",
        }),
      ],
    },
  });

  assert.equal(signals["main:inbound-1"].returnedReplyEvidence.hasEvidence, true);
  assert.equal(signals["main:inbound-1"].prioritySource.source, "manual");
});

test("does not mutate input arrays or objects", () => {
  const candidateMessages = [
    message({
      id: "inbound-1",
      threadId: "licensing question",
    }),
  ];
  const sentMessagesByMailboxId = {
    main: [
      sentMessage({
        id: "sent-1",
        threadId: "licensing question",
      }),
    ],
  };
  const beforeCandidates = JSON.stringify(candidateMessages);
  const beforeSentMessages = JSON.stringify(sentMessagesByMailboxId);

  buildPriorityRuntimeSignalsForCandidates({
    ownEmailAddresses,
    candidateMessages,
    sentMessagesByMailboxId,
  });

  assert.equal(JSON.stringify(candidateMessages), beforeCandidates);
  assert.equal(JSON.stringify(sentMessagesByMailboxId), beforeSentMessages);
});

test("output keys are deterministic and can use a caller resolver", () => {
  const firstSignals = buildPriorityRuntimeSignalsForCandidates({
    ownEmailAddresses,
    candidateMessages: [
      message({ id: "inbound-1", mailboxId: "main" }),
      message({ id: "inbound-2", mailboxId: "promo" }),
    ],
    resolveMessageKey: (candidate) => `${candidate.mailboxId}:${candidate.id}:stable`,
  });
  const secondSignals = buildPriorityRuntimeSignalsForCandidates({
    ownEmailAddresses,
    candidateMessages: [
      message({ id: "inbound-1", mailboxId: "main" }),
      message({ id: "inbound-2", mailboxId: "promo" }),
    ],
    resolveMessageKey: (candidate) => `${candidate.mailboxId}:${candidate.id}:stable`,
  });

  assert.deepEqual(Object.keys(firstSignals), [
    "main:inbound-1:stable",
    "promo:inbound-2:stable",
  ]);
  assert.deepEqual(Object.keys(firstSignals), Object.keys(secondSignals));
});

test("WorkspaceShell imports selector for read-only computation only", () => {
  const workspaceShellSource = readFileSync(
    "src/components/workspace/WorkspaceShell.tsx",
    "utf8",
  );

  assert.equal(
    workspaceShellSource.includes("buildPriorityRuntimeSignalsForCandidates"),
    true,
  );
  assert.equal(
    workspaceShellSource.includes("void priorityRuntimeSignalsForCandidates"),
    true,
  );
  assert.equal(workspaceShellSource.includes("console.log(priorityRuntimeSignals"), false);
  assert.equal(workspaceShellSource.includes("console.info(priorityRuntimeSignals"), false);
});

if (failed > 0) {
  console.error(`\n${failed} priorityRuntimeSignals test${failed === 1 ? "" : "s"} failed.`);
  process.exit(1);
}

console.log(`\n${passed} priorityRuntimeSignals tests passed.`);
