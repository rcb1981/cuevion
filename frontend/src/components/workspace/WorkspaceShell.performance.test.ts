import assert from "node:assert/strict";
import "sucrase/register/tsx.js";

const {
  applyFocusPreferenceRoutingToMailboxCollections,
  normalizeMailMessage,
} = require("./WorkspaceShell.tsx") as typeof import("./WorkspaceShell");
const {
  writePersistedMessageOwnershipStateValue,
} = require("../../lib/mailboxMessageIdentity") as typeof import("../../lib/mailboxMessageIdentity");

type MessageSeed = Parameters<typeof normalizeMailMessage>[0];
type FocusPreferences = Parameters<
  typeof applyFocusPreferenceRoutingToMailboxCollections
>[1];

const mediumFocusPreferences: FocusPreferences = {
  demos: "medium",
  promo: "medium",
  finance: "medium",
  legal: "medium",
  business: "medium",
  updates: "medium",
  distribution: "medium",
  royalties: "medium",
  promoReminders: "medium",
  paymentReminders: "medium",
};

function createSeed(
  id: string,
  overrides: Partial<MessageSeed> = {},
): MessageSeed {
  return {
    id,
    serverMailboxId: "main",
    providerMessageId: `provider-${id}`,
    sender: "Source Cache Sender",
    subject: `Source cache ${id}`,
    snippet: "Cached message body",
    time: "10:00",
    from: "sender@example.test",
    to: "owner@example.test",
    timestamp: "2026-08-30T08:00:00.000Z",
    body: ["Cached & body"],
    bodyHtml: "<p>Cached &amp; body</p>",
    internalClassification: "business",
    attachments: [
      {
        id: `attachment-${id}`,
        name: "invoice.pdf",
        mimeType: "application/pdf",
        size: 128,
        receivedSource: {
          mailboxId: "main",
          messageId: id,
          providerFolder: "INBOX",
        },
      },
    ],
    ...overrides,
  };
}

function cloneSeed(seed: MessageSeed): MessageSeed {
  return {
    ...seed,
    body: [...seed.body],
    attachments: seed.attachments?.map((attachment) =>
      typeof attachment === "string"
        ? attachment
        : {
            ...attachment,
            receivedSource: attachment.receivedSource
              ? { ...attachment.receivedSource }
              : undefined,
          },
    ),
    threadIdentityContext: seed.threadIdentityContext
      ? { ...seed.threadIdentityContext }
      : undefined,
  };
}

function decodeTestHtmlEntities(value: string) {
  return value
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&nbsp;/gi, "\u00a0")
    .replace(/&amp;/gi, "&");
}

function withCountingDomDecoder<T>(
  run: (getDecoderInvocationCount: () => number) => T,
): T {
  const globalRecord = globalThis as unknown as Record<string, unknown>;
  const previousDocumentDescriptor = Object.getOwnPropertyDescriptor(
    globalRecord,
    "document",
  );
  let decoderInvocationCount = 0;
  const testDocument = {
    createElement(tagName: string) {
      assert.equal(tagName, "textarea");
      decoderInvocationCount += 1;
      let decodedValue = "";

      return {
        set innerHTML(value: string) {
          decodedValue = decodeTestHtmlEntities(value);
        },
        get value() {
          return decodedValue;
        },
      };
    },
  };

  Object.defineProperty(globalRecord, "document", {
    configurable: true,
    writable: true,
    value: testDocument,
  });

  try {
    return run(() => decoderInvocationCount);
  } finally {
    if (previousDocumentDescriptor) {
      Object.defineProperty(globalRecord, "document", previousDocumentDescriptor);
    } else {
      delete globalRecord.document;
    }
  }
}

function withoutDocument<T>(run: () => T): T {
  const globalRecord = globalThis as unknown as Record<string, unknown>;
  const previousDocumentDescriptor = Object.getOwnPropertyDescriptor(
    globalRecord,
    "document",
  );
  delete globalRecord.document;

  try {
    return run();
  } finally {
    if (previousDocumentDescriptor) {
      Object.defineProperty(globalRecord, "document", previousDocumentDescriptor);
    }
  }
}

withCountingDomDecoder((getCount) => {
  const seed = createSeed("same-object");
  const first = normalizeMailMessage(seed, "main", {}, {}, "user-1");
  const second = normalizeMailMessage(seed, "main", {}, {}, "user-1");

  assert.equal(getCount(), 1, "the same source object must project once");
  assert.deepEqual(second.body, first.body);
  assert.equal(second.bodyHtml, first.bodyHtml);
});

withCountingDomDecoder((getCount) => {
  const seed = createSeed("recreated-object");
  normalizeMailMessage(seed, "main", {}, {}, "user-1");
  normalizeMailMessage(cloneSeed(seed), "main", {}, {}, "user-1");

  assert.equal(getCount(), 1, "an exact recreated source must use structural reuse");
});

withCountingDomDecoder((getCount) => {
  const seed = createSeed("content-invalidation");
  normalizeMailMessage(seed, "main", {}, {}, "user-1");
  normalizeMailMessage(
    cloneSeed({ ...seed, body: ["Changed body"] }),
    "main",
    {},
    {},
    "user-1",
  );
  normalizeMailMessage(
    cloneSeed({ ...seed, bodyHtml: "<p>Changed &amp; HTML</p>" }),
    "main",
    {},
    {},
    "user-1",
  );

  assert.equal(getCount(), 3, "body and bodyHtml changes must each miss");
});

withCountingDomDecoder((getCount) => {
  const seed = createSeed("thread-invalidation", { threadId: undefined });
  const first = normalizeMailMessage(seed, "main", {}, {}, "user-1");
  const changedSubject = normalizeMailMessage(
    cloneSeed({ ...seed, subject: "Changed thread subject" }),
    "main",
    {},
    {},
    "user-1",
  );
  const changedThread = normalizeMailMessage(
    cloneSeed({ ...seed, threadId: "provider-thread-2" }),
    "main",
    {},
    {},
    "user-1",
  );

  assert.equal(getCount(), 3, "subject and raw thread ID changes must each miss");
  assert.notEqual(changedSubject.threadId, first.threadId);
  assert.equal(changedThread.threadId, "provider-thread-2");
});

withCountingDomDecoder((getCount) => {
  const seed = createSeed("attachment-invalidation");
  normalizeMailMessage(seed, "main", {}, {}, "user-1");
  normalizeMailMessage(
    cloneSeed({
      ...seed,
      attachments: [{ id: "changed", name: "changed.pdf", size: 256 }],
    }),
    "main",
    {},
    {},
    "user-1",
  );

  const firstFile = { name: "master.wav", size: 512 } as unknown as File;
  const secondFile = { name: "master.wav", size: 512 } as unknown as File;
  const fileSeed = createSeed("file-identity", {
    attachments: [{ id: "audio", name: "master.wav", file: firstFile }],
  });
  normalizeMailMessage(fileSeed, "main", {}, {}, "user-1");
  normalizeMailMessage(
    cloneSeed({
      ...fileSeed,
      attachments: [{ id: "audio", name: "master.wav", file: secondFile }],
    }),
    "main",
    {},
    {},
    "user-1",
  );

  assert.equal(
    getCount(),
    4,
    "attachment changes and unrelated File identities must miss",
  );
});

withCountingDomDecoder((getCount) => {
  const seed = createSeed("scope-isolation");
  normalizeMailMessage(seed, "main", {}, {}, "user-1");
  normalizeMailMessage(cloneSeed(seed), "promo", {}, {}, "user-1");
  normalizeMailMessage(
    cloneSeed({ ...seed, providerMessageId: "different-provider-message" }),
    "main",
    {},
    {},
    "user-1",
  );

  assert.equal(getCount(), 3, "mailbox and provider identities must stay isolated");
});

withCountingDomDecoder((getCount) => {
  const seed = createSeed("learning-freshness", {
    internalClassification: "unknown",
  });
  const beforeLearning = normalizeMailMessage(seed, "main", {}, {}, "user-1");
  const afterLearning = normalizeMailMessage(
    cloneSeed(seed),
    "main",
    {
      "sender@example.test": {
        learnedCategory: "Promo",
        learnedFromCount: 2,
        autoCategoryEnabled: false,
        mailboxAction: "move",
      },
    },
    {},
    "user-1",
  );

  assert.equal(getCount(), 1, "learning changes must reuse immutable source work");
  assert.equal(beforeLearning.category, "Primary");
  assert.equal(afterLearning.category, "Promo");
  assert.equal(afterLearning.categorySource, "learned");
});

withCountingDomDecoder((getCount) => {
  const seed = createSeed("ownership-freshness");
  const withoutOwner = normalizeMailMessage(seed, "main", {}, {}, "user-1");
  const ownership = writePersistedMessageOwnershipStateValue(
    {},
    seed,
    { userId: "user-1", count: 3 },
    { mailboxId: "main" },
  );
  const ownedByCurrentUser = normalizeMailMessage(
    cloneSeed(seed),
    "main",
    {},
    ownership,
    "user-1",
  );
  const viewedByOtherUser = normalizeMailMessage(
    cloneSeed(seed),
    "main",
    {},
    ownership,
    "user-2",
  );

  assert.equal(getCount(), 1, "ownership and current-user changes must reuse source work");
  assert.equal(withoutOwner.owner, undefined);
  assert.equal(ownedByCurrentUser.owner?.userId, "user-1");
  assert.equal(ownedByCurrentUser.focusSignal, "attention");
  assert.equal(viewedByOtherUser.focusSignal, null);
});

withCountingDomDecoder((getCount) => {
  const seed = createSeed("current-scalars", { unread: true, flagged: false });
  normalizeMailMessage(seed, "main", {}, {}, "user-1");
  const changedScalars = normalizeMailMessage(
    cloneSeed({ ...seed, unread: false, flagged: true, labelIds: ["STARRED"] }),
    "main",
    {},
    {},
    "user-1",
  );

  assert.equal(getCount(), 1, "mutable provider scalars must not invalidate source work");
  assert.equal(changedScalars.unread, false);
  assert.equal(changedScalars.flagged, true);
  assert.deepEqual(changedScalars.labelIds, ["STARRED"]);
});

withCountingDomDecoder((getCount) => {
  const seed = createSeed("focus-freshness");
  const message = normalizeMailMessage(seed, "main", {}, {}, "user-1");
  const collections = {
    Inbox: [message],
    Drafts: [],
    Sent: [],
    Archive: [],
    Filtered: [],
    Spam: [],
    Trash: [],
  };
  const normal = applyFocusPreferenceRoutingToMailboxCollections(
    collections,
    mediumFocusPreferences,
    {},
  );
  const low = applyFocusPreferenceRoutingToMailboxCollections(
    collections,
    { ...mediumFocusPreferences, business: "low" },
    {},
  );

  assert.equal(getCount(), 1, "focus routing must not repeat source normalization");
  assert.deepEqual(normal.Inbox.map((entry) => entry.id), [seed.id]);
  assert.deepEqual(low.Filtered.map((entry) => entry.id), [seed.id]);
});

const ssrSeed = createSeed("dom-environment", {
  body: ["SSR source body"],
  bodyHtml: "&lt;p&gt;SSR &amp; body&lt;/p&gt;",
});
const ssrProjection = withoutDocument(() =>
  normalizeMailMessage(ssrSeed, "main", {}, {}, "user-1"),
);
withCountingDomDecoder((getCount) => {
  const browserProjection = normalizeMailMessage(
    cloneSeed(ssrSeed),
    "main",
    {},
    {},
    "user-1",
  );

  assert.equal(getCount(), 1, "browser mode must not reuse an SSR projection");
  assert.equal(ssrProjection.bodyHtml, "&lt;p&gt;SSR &amp; body&lt;/p&gt;");
  assert.equal(browserProjection.bodyHtml, "<p>SSR & body</p>");
  assert.deepEqual(browserProjection.body, ["SSR & body"]);
});
const repeatedSsrProjection = withoutDocument(() =>
  normalizeMailMessage(cloneSeed(ssrSeed), "main", {}, {}, "user-1"),
);
assert.equal(repeatedSsrProjection.bodyHtml, ssrProjection.bodyHtml);

withCountingDomDecoder((getCount) => {
  const entitySeed = createSeed("entity-equivalence", {
    body: ["Alpha & Beta Gamma"],
    bodyHtml: "<p>Alpha &amp; Beta&nbsp;Gamma</p>",
  });
  const projected = normalizeMailMessage(entitySeed, "main", {}, {}, "user-1");

  assert.equal(getCount(), 1);
  assert.equal(projected.bodyHtml, "<p>Alpha &amp; Beta&nbsp;Gamma</p>");
  assert.deepEqual(projected.body, ["Alpha & Beta Gamma"]);
});

withCountingDomDecoder((getCount) => {
  const oldest = createSeed("eviction-oldest");
  normalizeMailMessage(oldest, "main", {}, {}, "user-1");
  for (let index = 0; index < 64; index += 1) {
    normalizeMailMessage(
      createSeed(`eviction-fill-${index}`),
      "main",
      {},
      {},
      "user-1",
    );
  }
  normalizeMailMessage(cloneSeed(oldest), "main", {}, {}, "user-1");

  assert.equal(getCount(), 66, "the oldest structural entry must evict after 64 entries");
});

withCountingDomDecoder((getCount) => {
  let messages: MessageSeed[] = Array.from({ length: 64 }, (_, index) =>
    createSeed(`pathological-${index}`, {
      internalClassification: "unknown",
    }),
  );

  for (let pass = 0; pass < 10; pass += 1) {
    const learning: Parameters<typeof normalizeMailMessage>[2] =
      pass % 2 === 0
        ? {}
        : {
            "sender@example.test": {
              learnedCategory: "Promo" as const,
              learnedFromCount: 2,
              autoCategoryEnabled: false,
              mailboxAction: "move" as const,
            },
          };
    messages = messages.map((message) =>
      normalizeMailMessage(message, "main", learning, {}, "user-1"),
    );

    assert.equal(messages[0]?.category, pass % 2 === 0 ? "Primary" : "Promo");
  }

  assert.equal(
    getCount(),
    64,
    "64 messages across 10 policy passes must project 64 sources, not 640",
  );
});
