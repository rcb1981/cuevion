import assert from "node:assert/strict";
import "sucrase/register/tsx.js";

const {
  applyFocusPreferenceRoutingToMailboxCollections,
  getVisiblePriorityBadgeForWorkspaceMessage,
  normalizeMailMessage,
  resolveVisibleCategoryLabelForMessageInContext,
  workspaceShellConversationPerformanceTestSeam,
  workspaceShellPerformanceTestSeam,
} = require("./WorkspaceShell.tsx") as typeof import("./WorkspaceShell");
const {
  inboxEnginePerformanceTestSeam,
  resolveCanonicalConversationIdentity,
  resolveMessageDateMs,
} = require("../../lib/inboxEngine") as typeof import("../../lib/inboxEngine");
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

type ClassificationJoinCounter = { count: number };

type KeywordEvidenceCounter = {
  familyScans: number;
  includesAnyKeywordEvaluations: number;
  familyScansByName: Map<string, number>;
};

type ConversationIndexCounter = {
  indexBuilds: number;
  historyCandidateVisits: number;
  bucketCandidateVisits: number;
  canonicalIdentityResolutions: number;
  indexBuildsByMailbox: Map<string, number>;
};

function createConversationIndexCounter(): ConversationIndexCounter {
  return {
    indexBuilds: 0,
    historyCandidateVisits: 0,
    bucketCandidateVisits: 0,
    canonicalIdentityResolutions: 0,
    indexBuildsByMailbox: new Map(),
  };
}

function observeConversationIndex<T>(counter: ConversationIndexCounter, run: () => T) {
  return workspaceShellConversationPerformanceTestSeam.observeMailboxConversationIndex(
    {
      onIndexBuild(mailboxId) {
        counter.indexBuilds += 1;
        counter.indexBuildsByMailbox.set(
          mailboxId,
          (counter.indexBuildsByMailbox.get(mailboxId) ?? 0) + 1,
        );
      },
      onHistoryCandidateVisit() {
        counter.historyCandidateVisits += 1;
      },
      onBucketCandidateVisit() {
        counter.bucketCandidateVisits += 1;
      },
      onCanonicalIdentityResolution() {
        counter.canonicalIdentityResolutions += 1;
      },
    },
    run,
  );
}

function createKeywordEvidenceCounter(): KeywordEvidenceCounter {
  return {
    familyScans: 0,
    includesAnyKeywordEvaluations: 0,
    familyScansByName: new Map(),
  };
}

function observeKeywordEvidence<T>(counter: KeywordEvidenceCounter, run: () => T) {
  return workspaceShellPerformanceTestSeam.observeMessageKeywordEvidence(
    {
      onFamilyScan(familyName) {
        counter.familyScans += 1;
        counter.familyScansByName.set(
          familyName,
          (counter.familyScansByName.get(familyName) ?? 0) + 1,
        );
      },
      onIncludesAnyKeywordEvaluation() {
        counter.includesAnyKeywordEvaluations += 1;
      },
    },
    run,
  );
}

function getFamilyScanCount(counter: KeywordEvidenceCounter, familyName: string) {
  return counter.familyScansByName.get(familyName) ?? 0;
}

function createClassificationCountingBody(
  paragraphs: string[],
  counter: ClassificationJoinCounter,
) {
  const body = [...paragraphs];
  const nativeJoin = Array.prototype.join;

  Object.defineProperty(body, "join", {
    configurable: true,
    value(separator?: string) {
      if (separator === " ") {
        counter.count += 1;
      }
      return nativeJoin.call(this, separator);
    },
  });

  return body;
}

function withClassificationCountingBody(
  message: ReturnType<typeof normalizeMailMessage>,
  counter: ClassificationJoinCounter,
  paragraphs = message.body,
) {
  return {
    ...message,
    body: createClassificationCountingBody([...paragraphs], counter),
  };
}

function exerciseClassificationPaths(message: ReturnType<typeof normalizeMailMessage>) {
  return {
    normalLabel: resolveVisibleCategoryLabelForMessageInContext(message, false),
    promoContextLabel: resolveVisibleCategoryLabelForMessageInContext(message, true),
    priorityBadge: getVisiblePriorityBadgeForWorkspaceMessage(
      message,
      undefined,
      mediumFocusPreferences,
    ),
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

const classificationEquivalenceFixtures: Array<{
  name: string;
  overrides: Partial<MessageSeed>;
  expectedVisibleLabel: ReturnType<
    typeof resolveVisibleCategoryLabelForMessageInContext
  >;
  expectedSignal?: ReturnType<typeof normalizeMailMessage>["signal"];
  expectedInternalClassification?: ReturnType<
    typeof normalizeMailMessage
  >["internalClassification"];
}> = [
  {
    name: "strong demo submission",
    overrides: {
      subject: "[DEMO] Artist - Unreleased Track",
      snippet: "Demo submission for feedback",
      to: "demos@example.test",
      body: ["Listen to my new track", "Private SoundCloud link"],
    },
    expectedVisibleLabel: "Demo",
  },
  {
    name: "music promo",
    overrides: {
      subject: "[PROMO] Artist - Club Track",
      snippet: "New release for your sets",
      body: ["DJ support is appreciated", "Promo download page"],
    },
    expectedVisibleLabel: "Promo",
  },
  {
    name: "protected LabelWorx promo context",
    overrides: {
      internalClassification: "workflow_update",
      subject: "Artist - Club Track",
      from: "LabelWorx <promobox-reply@label-worx.com>",
      body: ["Your limited promo download package is ready"],
    },
    expectedVisibleLabel: "Promo",
  },
  {
    name: "promo access request",
    overrides: {
      internalClassification: "promo",
      subject: "Please add me to your mailing list",
      snippet: "Please add me to your promo list",
      body: ["I would like to receive promos for my radio show"],
    },
    expectedVisibleLabel: "Business",
  },
  {
    name: "explicit promo sendout",
    overrides: {
      internalClassification: "workflow_update",
      subject: "PROMO: Artist - New House Track",
      snippet: "New track for your sets",
      body: ["DJ support is appreciated within your DJ sets"],
    },
    expectedVisibleLabel: "Promo",
  },
  {
    name: "broadcast promo",
    overrides: {
      internalClassification: "business",
      subject: "Monthly label news",
      snippet: "Read this email online",
      body: ["View in browser", "Unsubscribe"],
    },
    expectedVisibleLabel: "Update",
  },
  {
    name: "generic retail marketing",
    overrides: {
      internalClassification: "business",
      subject: "Last chance sale",
      snippet: "Save with this coupon",
      body: ["Limited time discount", "Shop now", "Unsubscribe"],
    },
    expectedVisibleLabel: "Update",
  },
  {
    name: "newsletter update",
    overrides: {
      internalClassification: "finance",
      subject: "In brief newsletter",
      snippet: "Watch the summit on demand",
      body: ["Register for the webinar", "Join us"],
    },
    expectedVisibleLabel: "Update",
  },
  {
    name: "low-value event update",
    overrides: {
      internalClassification: "business",
      subject: "Conference passes available",
      snippet: "Register now for the event",
      body: ["Save 40% on show passes", "Unsubscribe"],
    },
    expectedVisibleLabel: "Update",
  },
  {
    name: "cold sales outreach",
    overrides: {
      internalClassification: "finance",
      subject: "Grow your content in a new market",
      snippet: "We work with creators",
      body: [
        "I came across your content and see a huge audience in China.",
        "We handle account setup, content translation and localization.",
      ],
    },
    expectedVisibleLabel: "Other",
  },
  {
    name: "royalty statement",
    overrides: {
      subject: "Your royalty statement is ready",
      snippet: "Statement availability notification",
      from: "Warner Music <royalties@example.test>",
      body: ["Sign in to the artist royalties portal"],
    },
    expectedVisibleLabel: "Finance",
    expectedInternalClassification: "royalty_statement",
  },
  {
    name: "finance heuristic",
    overrides: {
      subject: "Invoice payment due",
      snippet: "Please review the invoice",
      body: ["Payment is due tomorrow"],
    },
    expectedVisibleLabel: "Finance",
    expectedSignal: "Finance",
    expectedInternalClassification: "finance",
  },
  {
    name: "business heuristic",
    overrides: {
      subject: "Contract approval required",
      snippet: "Please review the agreement",
      body: ["Please confirm before the deadline"],
    },
    expectedVisibleLabel: "Business",
    expectedSignal: "Priority",
    expectedInternalClassification: "business",
  },
  {
    name: "update heuristic",
    overrides: {
      subject: "Status update",
      snippet: "The delivery is completed",
      body: ["Delivery completed successfully"],
    },
    expectedVisibleLabel: "Update",
    expectedSignal: "Update",
    expectedInternalClassification: "workflow_update",
  },
  {
    name: "unknown refinement to demo",
    overrides: {
      subject: "Demo submission",
      snippet: "Unreleased track submission",
      body: ["A private recording for consideration"],
    },
    expectedVisibleLabel: "Demo",
    expectedSignal: "Other",
    expectedInternalClassification: "demo",
  },
];

for (const fixture of classificationEquivalenceFixtures) {
  const message = normalizeMailMessage(
    createSeed(`classification-equivalence-${fixture.name}`, {
      signal: undefined,
      ui_signal: undefined,
      internalClassification: undefined,
      final_visibility: undefined,
      action: undefined,
      bodyHtml: undefined,
      attachments: [],
      ...fixture.overrides,
    }),
    "main",
    {},
    {},
    "user-1",
  );

  assert.equal(
    resolveVisibleCategoryLabelForMessageInContext(message, false),
    fixture.expectedVisibleLabel,
    fixture.name,
  );
  if (fixture.expectedSignal !== undefined) {
    assert.equal(message.signal, fixture.expectedSignal, fixture.name);
  }
  if (fixture.expectedInternalClassification !== undefined) {
    assert.equal(
      message.internalClassification,
      fixture.expectedInternalClassification,
      fixture.name,
    );
  }
}

function createKeywordEvidenceSeed(
  id: string,
  overrides: Partial<MessageSeed> = {},
): MessageSeed {
  return createSeed(id, {
    signal: undefined,
    ui_signal: undefined,
    internalClassification: "business",
    subject: `Opaque subject ${id}`,
    snippet: `Neutral snippet ${id}`,
    sender: `Neutral Sender ${id}`,
    from: `${id}@example.test`,
    to: `owner-${id}@example.test`,
    body: [`Neutral body ${id}`],
    bodyHtml: undefined,
    attachments: [],
    ...overrides,
  });
}

type NormalizedMessage = ReturnType<typeof normalizeMailMessage>;
type TestMailboxStore = Parameters<
  typeof workspaceShellConversationPerformanceTestSeam.normalizeMailboxStore
>[0];
type TestOrderedMailbox = Parameters<
  typeof workspaceShellConversationPerformanceTestSeam.normalizeMailboxStore
>[1][number];

function createConversationCollections(
  overrides: Partial<{
    Inbox: NormalizedMessage[];
    Drafts: NormalizedMessage[];
    Sent: NormalizedMessage[];
    Archive: NormalizedMessage[];
    Filtered: NormalizedMessage[];
    Spam: NormalizedMessage[];
    Trash: NormalizedMessage[];
  }> = {},
) {
  return {
    Inbox: [],
    Drafts: [],
    Sent: [],
    Archive: [],
    Filtered: [],
    Spam: [],
    Trash: [],
    ...overrides,
  };
}

function createConversationOrderedMailbox(mailboxId: string) {
  return { id: mailboxId } as TestOrderedMailbox;
}

function createConversationMessage(
  id: string,
  options: {
    mailboxId?: "main" | "promo";
    threadId?: string;
    subject?: string;
    from?: string;
    to?: string;
    createdAt?: string;
    folder?: string;
    internalClassification?: MessageSeed["internalClassification"];
    category?: NormalizedMessage["category"];
    categorySource?: NormalizedMessage["categorySource"];
    categoryConfidence?: NormalizedMessage["categoryConfidence"];
  } = {},
): NormalizedMessage {
  const mailboxId = options.mailboxId ?? "main";
  const providerThreadId = options.threadId ?? `thread-${id}`;
  const normalized = normalizeMailMessage(
    createSeed(id, {
      serverMailboxId: mailboxId,
      providerMessageId: `provider-${mailboxId}-${id}`,
      providerThreadId,
      threadId: `gmail:${mailboxId}:${providerThreadId}`,
      threadIdentityContext: {
        mailboxId,
        provider: "google",
        folder: options.folder ?? "INBOX",
        uidValidity: "gmail-api",
      },
      threadIdentityAuthority: "gmail",
      subject: options.subject ?? `Conversation ${id}`,
      snippet: `Conversation snippet ${id}`,
      sender: `Conversation Sender ${id}`,
      from: options.from ?? `sender-${id}@example.test`,
      to: options.to ?? `owner-${id}@example.test`,
      createdAt: options.createdAt ?? "2026-08-30T08:00:00.000Z",
      timestamp: options.createdAt ?? "2026-08-30T08:00:00.000Z",
      body: [`Conversation body ${id}`],
      bodyHtml: undefined,
      attachments: [],
      internalClassification: options.internalClassification ?? "business",
      category: undefined,
      categorySource: undefined,
      categoryConfidence: undefined,
    }),
    mailboxId,
    {},
    {},
    "user-1",
  );

  return {
    ...normalized,
    ...(options.category ? { category: options.category } : {}),
    ...(options.categorySource ? { categorySource: options.categorySource } : {}),
    ...(options.categoryConfidence
      ? { categoryConfidence: options.categoryConfidence }
      : {}),
  };
}

function resolveLegacyThreadDominantCategorizationForTest(
  message: NormalizedMessage,
  mailboxId: "main" | "promo",
  mailboxStore: TestMailboxStore,
) {
  const messageIdentity = resolveCanonicalConversationIdentity(message, mailboxId);
  if (message.threadIdentityContext && !messageIdentity.isAuthoritativeConversation) {
    return null;
  }
  const collections = mailboxStore[mailboxId];
  if (!collections) {
    return null;
  }
  const messageDateMs = resolveMessageDateMs(message);
  const recentWindowMs = 30 * 24 * 60 * 60 * 1000;
  const recent = [
    ...collections.Trash,
    ...collections.Spam,
    ...collections.Filtered,
    ...collections.Archive,
    ...collections.Sent,
    ...collections.Drafts,
    ...collections.Inbox,
  ].filter((candidate) => {
    if (candidate.id === message.id) {
      return false;
    }
    if (
      resolveCanonicalConversationIdentity(candidate, mailboxId).key !==
      messageIdentity.key
    ) {
      return false;
    }
    const candidateDateMs = resolveMessageDateMs(candidate);
    return (
      (messageDateMs === 0 ||
        candidateDateMs === 0 ||
        Math.abs(messageDateMs - candidateDateMs) <= recentWindowMs) &&
      Boolean(
        candidate.category &&
          candidate.categorySource &&
          candidate.categoryConfidence,
      )
    );
  });
  if (recent.length === 0) {
    return null;
  }
  const scores = recent.reduce<Record<NormalizedMessage["category"], number>>(
    (current, candidate) => {
      const weight =
        candidate.categorySource === "user"
          ? 4
          : candidate.categorySource === "learned"
            ? 3
            : candidate.categoryConfidence === "high"
              ? 2
              : candidate.categoryConfidence === "medium"
                ? 1.25
                : 0.5;
      return {
        ...current,
        [candidate.category]: (current[candidate.category] ?? 0) + weight,
      };
    },
    { Primary: 0, Promo: 0, Updates: 0 },
  );
  const ranked = Object.entries(scores).sort(
    (first, second) => second[1] - first[1],
  ) as Array<[NormalizedMessage["category"], number]>;
  const [topCategory, topScore] = ranked[0] ?? [];
  const secondScore = ranked[1]?.[1] ?? 0;
  if (!topCategory || topScore < 2 || topScore - secondScore < 1.25) {
    return null;
  }
  return {
    category: topCategory,
    confidence: topScore >= 4 ? ("high" as const) : ("medium" as const),
  };
}

{
  const identityFixtures = [
    {
      name: "Gmail provider thread",
      message: {
        id: "gmail",
        subject: "Provider thread",
        threadId: "gmail:main:provider-thread",
        threadIdentityAuthority: "gmail" as const,
      },
      expected: {
        key: "thread:main|gmail:main:provider-thread",
        authority: "gmail",
        isAuthoritativeConversation: true,
      },
    },
    {
      name: "RFC Message-ID thread",
      message: {
        id: "rfc",
        subject: "RFC thread",
        threadId: "imap:rfc:root%40example.com",
        threadIdentityAuthority: "rfc" as const,
      },
      expected: {
        key: "thread:main|imap:rfc:root%40example.com",
        authority: "rfc",
        isAuthoritativeConversation: true,
      },
    },
    {
      name: "IMAP UID unique message",
      message: {
        id: "imap",
        subject: "Unique IMAP",
        threadId: "imap:uid:main:INBOX:900:42",
        threadIdentityAuthority: "unique_message" as const,
      },
      expected: {
        key: "thread:main|imap:uid:main:INBOX:900:42",
        authority: "unique_message",
        isAuthoritativeConversation: false,
      },
    },
    {
      name: "heuristic reply",
      message: {
        id: "reply",
        subject: "Re: Project Alpha",
        from: "Alice <alice@example.test>",
        to: "Bob <bob@example.test>",
      },
      expected: {
        key: "conversation:main|project alpha|alice@example.test,bob@example.test",
        authority: "heuristic",
        isAuthoritativeConversation: false,
      },
    },
    {
      name: "generic subject unique message",
      message: {
        id: "generic-demo",
        imapUid: "51",
        subject: "Demo",
        from: "artist@example.test",
      },
      expected: {
        key: "message:main|51",
        authority: "unique_message",
        isAuthoritativeConversation: false,
      },
    },
    {
      name: "participant identity",
      message: {
        id: "participant",
        subject: "Project Alpha",
        from: "Alice <alice@example.test>",
        to: "Bob <bob@example.test>",
      },
      expected: {
        key: "conversation:main|project alpha|alice@example.test,bob@example.test",
        authority: "heuristic",
        isAuthoritativeConversation: false,
      },
    },
    {
      name: "sender fallback",
      message: {
        id: "fallback",
        subject: "Project Alpha",
        sender: "Fallback Sender",
      },
      expected: {
        key: "fallback:main|project alpha|fallback sender",
        authority: "heuristic",
        isAuthoritativeConversation: false,
      },
    },
  ];

  identityFixtures.forEach((fixture) => {
    assert.deepEqual(
      resolveCanonicalConversationIdentity(fixture.message, "main"),
      fixture.expected,
      fixture.name,
    );
  });
  assert.notEqual(
    resolveCanonicalConversationIdentity(
      {
        id: "participant-a",
        subject: "Same subject",
        from: "alice@example.test",
      },
      "main",
    ).key,
    resolveCanonicalConversationIdentity(
      {
        id: "participant-b",
        subject: "Same subject",
        from: "bob@example.test",
      },
      "main",
    ).key,
  );
}

{
  let identityResolutions = 0;
  let participantKeyBuilds = 0;
  inboxEnginePerformanceTestSeam.observeCanonicalConversationIdentity(
    {
      onIdentityResolution() {
        identityResolutions += 1;
      },
      onParticipantKeyBuild() {
        participantKeyBuilds += 1;
      },
    },
    () => {
      resolveCanonicalConversationIdentity(
        {
          id: "lazy-provider",
          subject: "Provider",
          threadId: "gmail:main:lazy-provider",
          threadIdentityAuthority: "gmail",
          from: "alice@example.test",
          to: "bob@example.test",
        },
        "main",
      );
      resolveCanonicalConversationIdentity(
        {
          id: "lazy-heuristic",
          subject: "Heuristic",
          from: "alice@example.test",
          to: "bob@example.test",
        },
        "main",
      );
      resolveCanonicalConversationIdentity(
        {
          id: "lazy-generic",
          subject: "Demo",
          from: "artist@example.test",
          to: "demos@example.test",
        },
        "main",
      );
    },
  );

  assert.equal(identityResolutions, 3);
  assert.equal(
    participantKeyBuilds,
    1,
    "authoritative thread branches must bypass participant-key construction",
  );
}

{
  const counter = createConversationIndexCounter();
  const messages = Array.from({ length: 100 }, (_, index) =>
    createConversationMessage(`perf-n4-${index}`),
  );
  const store = {
    main: createConversationCollections({ Inbox: messages }),
  } as TestMailboxStore;
  const normalized = observeConversationIndex(counter, () =>
    workspaceShellConversationPerformanceTestSeam.normalizeMailboxStore(
      store,
      [createConversationOrderedMailbox("main")],
      {},
      {},
      "user-1",
    ),
  );
  const legacyIdentityLowerBound = messages.length * messages.length;
  const legacyHistoryVisitCount = messages.length * messages.length;

  assert.equal(normalized.main.Inbox.length, messages.length);
  assert.equal(counter.indexBuilds, 1);
  assert.equal(counter.historyCandidateVisits, 100);
  assert.equal(counter.canonicalIdentityResolutions, 100);
  assert.equal(counter.bucketCandidateVisits, 100);
  assert.ok(legacyIdentityLowerBound > counter.canonicalIdentityResolutions);
  assert.ok(legacyHistoryVisitCount > counter.historyCandidateVisits);
  process.stdout.write(
    `PERF-N4 100x100: identity before>=${legacyIdentityLowerBound}, after=${counter.canonicalIdentityResolutions}; history visits before=${legacyHistoryVisitCount}, index=${counter.historyCandidateVisits}, buckets=${counter.bucketCandidateVisits}\n`,
  );
}

{
  const counter = createConversationIndexCounter();
  const messages = [
    createConversationMessage("matching-a", { threadId: "matching-thread" }),
    createConversationMessage("matching-b", { threadId: "matching-thread" }),
  ];
  const store = {
    main: createConversationCollections({ Inbox: messages }),
  } as TestMailboxStore;
  observeConversationIndex(counter, () =>
    workspaceShellConversationPerformanceTestSeam.normalizeMailboxStore(
      store,
      [createConversationOrderedMailbox("main")],
      {},
      {},
      "user-1",
    ),
  );

  assert.equal(counter.indexBuilds, 1);
  assert.equal(
    counter.canonicalIdentityResolutions,
    2,
    "matching history identities must be resolved once during index construction",
  );
  assert.equal(counter.bucketCandidateVisits, 4);
}

{
  const counter = createConversationIndexCounter();
  const messages = [
    createConversationMessage("repeat-a", { threadId: "repeat-thread" }),
    createConversationMessage("repeat-b", { threadId: "repeat-thread" }),
  ];
  const store = {
    main: createConversationCollections({ Inbox: messages }),
  } as TestMailboxStore;
  const context =
    workspaceShellConversationPerformanceTestSeam.createMailboxNormalizationContext(
      store,
    );
  const [first, second] = observeConversationIndex(counter, () => [
    workspaceShellConversationPerformanceTestSeam.resolveThreadDominantCategorization(
      messages[0],
      "main",
      store,
      context,
    ),
    workspaceShellConversationPerformanceTestSeam.resolveThreadDominantCategorization(
      messages[0],
      "main",
      store,
      context,
    ),
  ]);

  assert.deepEqual(second, first);
  assert.equal(counter.indexBuilds, 1);
  assert.equal(counter.historyCandidateVisits, 2);
  assert.equal(counter.canonicalIdentityResolutions, 2);
  assert.equal(counter.bucketCandidateVisits, 4);
}

{
  const counter = createConversationIndexCounter();
  const mainMessage = createConversationMessage("mailbox-main", {
    mailboxId: "main",
    threadId: "shared-provider-thread",
  });
  const promoMessage = createConversationMessage("mailbox-promo", {
    mailboxId: "promo",
    threadId: "shared-provider-thread",
  });
  const store = {
    main: createConversationCollections({ Inbox: [mainMessage] }),
    promo: createConversationCollections({ Inbox: [promoMessage] }),
  } as TestMailboxStore;
  const normalized = observeConversationIndex(counter, () =>
    workspaceShellConversationPerformanceTestSeam.normalizeMailboxStore(
      store,
      [
        createConversationOrderedMailbox("main"),
        createConversationOrderedMailbox("promo"),
      ],
      {},
      {},
      "user-1",
    ),
  );

  assert.equal(normalized.main.Inbox.length, 1);
  assert.equal(normalized.promo.Inbox.length, 1);
  assert.equal(counter.indexBuilds, 2);
  assert.equal(counter.indexBuildsByMailbox.get("main"), 1);
  assert.equal(counter.indexBuildsByMailbox.get("promo"), 1);
  assert.equal(counter.canonicalIdentityResolutions, 2);
}

{
  const counter = createConversationIndexCounter();
  const emptyStore = {
    main: createConversationCollections(),
  } as TestMailboxStore;
  observeConversationIndex(counter, () =>
    workspaceShellConversationPerformanceTestSeam.normalizeMailboxStore(
      emptyStore,
      [createConversationOrderedMailbox("main")],
      {},
      {},
      "user-1",
    ),
  );
  assert.equal(counter.indexBuilds, 0);
  assert.equal(counter.historyCandidateVisits, 0);
  assert.equal(counter.canonicalIdentityResolutions, 0);
}

{
  const counter = createConversationIndexCounter();
  const message = createConversationMessage("single-thread");
  const store = {
    main: createConversationCollections({ Inbox: [message] }),
  } as TestMailboxStore;
  const normalized = observeConversationIndex(counter, () =>
    workspaceShellConversationPerformanceTestSeam.normalizeMailboxStore(
      store,
      [createConversationOrderedMailbox("main")],
      {},
      {},
      "user-1",
    ),
  );
  assert.equal(normalized.main.Inbox[0]?.category, message.category);
  assert.equal(counter.indexBuilds, 1);
  assert.equal(counter.historyCandidateVisits, 1);
  assert.equal(counter.canonicalIdentityResolutions, 1);
  assert.equal(counter.bucketCandidateVisits, 1);
}

{
  const counter = createConversationIndexCounter();
  const messages = Array.from({ length: 100 }, (_, index) =>
    createConversationMessage(`bucket-${index}`, {
      threadId: index < 40 ? "large-shared-thread" : `unique-thread-${index}`,
    }),
  );
  const store = {
    main: createConversationCollections({ Inbox: messages }),
  } as TestMailboxStore;
  observeConversationIndex(counter, () =>
    workspaceShellConversationPerformanceTestSeam.normalizeMailboxStore(
      store,
      [createConversationOrderedMailbox("main")],
      {},
      {},
      "user-1",
    ),
  );

  assert.equal(counter.historyCandidateVisits, 100);
  assert.equal(counter.canonicalIdentityResolutions, 100);
  assert.equal(
    counter.bucketCandidateVisits,
    40 * 40 + 60,
    "post-index evaluation must visit only each message's matching bucket",
  );
}

{
  const current = createConversationMessage("dominant-current", {
    threadId: "dominant-thread",
    internalClassification: "unknown",
  });
  const primaryHistory = createConversationMessage("dominant-primary", {
    threadId: "dominant-thread",
    category: "Primary",
    categorySource: "user",
    categoryConfidence: "high",
  });
  const promoHistory = createConversationMessage("dominant-promo", {
    threadId: "dominant-thread",
    category: "Promo",
    categorySource: "user",
    categoryConfidence: "high",
  });
  const promoSystemHistory = createConversationMessage("dominant-promo-system", {
    threadId: "dominant-thread",
    category: "Promo",
    categorySource: "system",
    categoryConfidence: "high",
  });
  const primarySystemHistory = createConversationMessage(
    "dominant-primary-system",
    {
      threadId: "dominant-thread",
      category: "Primary",
      categorySource: "system",
      categoryConfidence: "high",
    },
  );
  const outsideWindow = createConversationMessage("dominant-old", {
    threadId: "dominant-thread",
    createdAt: "2026-06-01T08:00:00.000Z",
    category: "Promo",
    categorySource: "user",
    categoryConfidence: "high",
  });
  const differentThread = createConversationMessage("dominant-different", {
    threadId: "different-thread",
    category: "Promo",
    categorySource: "user",
    categoryConfidence: "high",
  });
  const fixtures: Array<{
    name: string;
    store: TestMailboxStore;
    expected: { category: NormalizedMessage["category"]; confidence: "high" | "medium" } | null;
  }> = [
    {
      name: "dominant Primary and Demo coarse category",
      store: {
        main: createConversationCollections({
          Inbox: [current, primaryHistory, primarySystemHistory],
        }),
      } as TestMailboxStore,
      expected: { category: "Primary", confidence: "high" },
    },
    {
      name: "dominant Promo across folders",
      store: {
        main: createConversationCollections({
          Inbox: [current],
          Archive: [promoHistory],
          Sent: [promoSystemHistory],
        }),
      } as TestMailboxStore,
      expected: { category: "Promo", confidence: "high" },
    },
    {
      name: "mixed category",
      store: {
        main: createConversationCollections({
          Inbox: [current, primaryHistory, promoSystemHistory],
        }),
      } as TestMailboxStore,
      expected: { category: "Primary", confidence: "high" },
    },
    {
      name: "tied weights",
      store: {
        main: createConversationCollections({
          Inbox: [current, primaryHistory, promoHistory],
        }),
      } as TestMailboxStore,
      expected: null,
    },
    {
      name: "outside 30-day window",
      store: {
        main: createConversationCollections({ Inbox: [current, outsideWindow] }),
      } as TestMailboxStore,
      expected: null,
    },
    {
      name: "no matching history",
      store: {
        main: createConversationCollections({ Inbox: [current, differentThread] }),
      } as TestMailboxStore,
      expected: null,
    },
    {
      name: "single-message thread",
      store: {
        main: createConversationCollections({ Inbox: [current] }),
      } as TestMailboxStore,
      expected: null,
    },
  ];

  fixtures.forEach((fixture) => {
    const context =
      workspaceShellConversationPerformanceTestSeam.createMailboxNormalizationContext(
        fixture.store,
      );
    const indexed =
      workspaceShellConversationPerformanceTestSeam.resolveThreadDominantCategorization(
        current,
        "main",
        fixture.store,
        context,
      );
    assert.deepEqual(indexed, fixture.expected, fixture.name);
    assert.deepEqual(
      indexed,
      resolveLegacyThreadDominantCategorizationForTest(
        current,
        "main",
        fixture.store,
      ),
      `${fixture.name} legacy equivalence`,
    );
  });

  const isolatedStore = {
    main: createConversationCollections({ Inbox: [current] }),
    promo: createConversationCollections({ Inbox: [promoHistory] }),
  } as TestMailboxStore;
  const isolatedContext =
    workspaceShellConversationPerformanceTestSeam.createMailboxNormalizationContext(
      isolatedStore,
    );
  assert.equal(
    workspaceShellConversationPerformanceTestSeam.resolveThreadDominantCategorization(
      current,
      "main",
      isolatedStore,
      isolatedContext,
    ),
    null,
    "thread-dominant history must not cross mailbox boundaries",
  );

  const demoCurrent = createConversationMessage("dominant-demo-current", {
    threadId: "dominant-demo-thread",
    internalClassification: "demo",
  });
  const demoHistory = createConversationMessage("dominant-demo-history", {
    threadId: "dominant-demo-thread",
    internalClassification: "demo",
    category: "Primary",
    categorySource: "user",
    categoryConfidence: "high",
  });
  const demoStore = {
    main: createConversationCollections({ Inbox: [demoHistory] }),
  } as TestMailboxStore;
  const normalizedDemo = normalizeMailMessage(
    demoCurrent,
    "main",
    {},
    {},
    "user-1",
    demoStore,
  );
  assert.equal(normalizedDemo.internalClassification, "demo");
  assert.equal(normalizedDemo.category, "Primary");
}

{
  const counter = createKeywordEvidenceCounter();
  const message = createKeywordEvidenceSeed("perf-n3-same-live");
  const firstResult = observeKeywordEvidence(counter, () =>
    workspaceShellPerformanceTestSeam.inferHeuristicSignal(message),
  );
  const scansAfterFirstCall = counter.familyScans;
  const includesAfterFirstCall = counter.includesAnyKeywordEvaluations;

  for (let pass = 0; pass < 10; pass += 1) {
    assert.equal(
      observeKeywordEvidence(counter, () =>
        workspaceShellPerformanceTestSeam.inferHeuristicSignal(message),
      ),
      firstResult,
    );
  }

  assert.ok(scansAfterFirstCall > 0, "the first heuristic call must request evidence");
  assert.equal(counter.familyScans, scansAfterFirstCall);
  assert.equal(counter.includesAnyKeywordEvaluations, includesAfterFirstCall);
  assert.ok(
    [...counter.familyScansByName.values()].every((count) => count === 1),
    "each requested family must scan once for the same live message",
  );
}

{
  const counter = createKeywordEvidenceCounter();
  const message = createKeywordEvidenceSeed("perf-n3-recreated");
  const firstResult = observeKeywordEvidence(counter, () =>
    workspaceShellPerformanceTestSeam.inferHeuristicSignal(message),
  );
  const scansAfterFirstCall = counter.familyScans;
  const includesAfterFirstCall = counter.includesAnyKeywordEvaluations;
  const recreatedResult = observeKeywordEvidence(counter, () =>
    workspaceShellPerformanceTestSeam.inferHeuristicSignal(cloneSeed(message)),
  );

  assert.equal(recreatedResult, firstResult);
  assert.equal(counter.familyScans, scansAfterFirstCall);
  assert.equal(counter.includesAnyKeywordEvaluations, includesAfterFirstCall);
}

{
  const counter = createKeywordEvidenceCounter();
  const source = createKeywordEvidenceSeed("perf-n3-normalized-transfer");
  const normalized = observeKeywordEvidence(counter, () =>
    normalizeMailMessage(source, "main", {}, {}, "user-1"),
  );
  const scansAfterNormalization = counter.familyScans;
  const includesAfterNormalization = counter.includesAnyKeywordEvaluations;
  normalized.signal = undefined;

  observeKeywordEvidence(counter, () =>
    workspaceShellPerformanceTestSeam.inferHeuristicSignal(normalized),
  );

  assert.ok(scansAfterNormalization > 0);
  assert.equal(
    counter.familyScans,
    scansAfterNormalization,
    "an exactly equivalent normalized output identity must inherit keyword evidence",
  );
  assert.equal(counter.includesAnyKeywordEvaluations, includesAfterNormalization);
}

{
  const counter = createKeywordEvidenceCounter();
  const baseline = createKeywordEvidenceSeed("perf-n3-invalidation");
  observeKeywordEvidence(counter, () =>
    workspaceShellPerformanceTestSeam.inferHeuristicSignal(baseline),
  );

  const marketingAfterBaseline = getFamilyScanCount(
    counter,
    "heuristic.marketingNewsletter",
  );
  const automatedAfterBaseline = getFamilyScanCount(
    counter,
    "heuristic.automatedSender",
  );
  observeKeywordEvidence(counter, () =>
    workspaceShellPerformanceTestSeam.inferHeuristicSignal(
      cloneSeed({ ...baseline, subject: "Changed opaque subject" }),
    ),
  );
  assert.equal(
    getFamilyScanCount(counter, "heuristic.marketingNewsletter"),
    marketingAfterBaseline + 1,
    "subject changes must invalidate subject-bearing evidence",
  );
  assert.equal(
    getFamilyScanCount(counter, "heuristic.automatedSender"),
    automatedAfterBaseline,
    "subject changes must not invalidate sender-only evidence",
  );

  const marketingBeforeSnippet = getFamilyScanCount(
    counter,
    "heuristic.marketingNewsletter",
  );
  observeKeywordEvidence(counter, () =>
    workspaceShellPerformanceTestSeam.inferHeuristicSignal(
      cloneSeed({ ...baseline, snippet: "Changed opaque snippet" }),
    ),
  );
  assert.equal(
    getFamilyScanCount(counter, "heuristic.marketingNewsletter"),
    marketingBeforeSnippet + 1,
    "snippet changes must invalidate snippet-bearing evidence",
  );

  const automatedBeforeSender = getFamilyScanCount(
    counter,
    "heuristic.automatedSender",
  );
  observeKeywordEvidence(counter, () =>
    workspaceShellPerformanceTestSeam.inferHeuristicSignal(
      cloneSeed({ ...baseline, sender: "Changed Neutral Sender" }),
    ),
  );
  assert.equal(
    getFamilyScanCount(counter, "heuristic.automatedSender"),
    automatedBeforeSender + 1,
    "sender changes must invalidate sender-derived evidence",
  );

  const automatedBeforeFrom = getFamilyScanCount(
    counter,
    "heuristic.automatedSender",
  );
  observeKeywordEvidence(counter, () =>
    workspaceShellPerformanceTestSeam.inferHeuristicSignal(
      cloneSeed({ ...baseline, from: "changed-from@example.test" }),
    ),
  );
  assert.equal(
    getFamilyScanCount(counter, "heuristic.automatedSender"),
    automatedBeforeFrom + 1,
    "from changes must invalidate sender-derived evidence",
  );

  const promoAccessBeforeTo = getFamilyScanCount(counter, "promoAccess.request");
  const marketingBeforeTo = getFamilyScanCount(
    counter,
    "heuristic.marketingNewsletter",
  );
  observeKeywordEvidence(counter, () =>
    workspaceShellPerformanceTestSeam.inferHeuristicSignal(
      cloneSeed({ ...baseline, to: "changed-owner@example.test" }),
    ),
  );
  assert.equal(
    getFamilyScanCount(counter, "promoAccess.request"),
    promoAccessBeforeTo + 1,
    "to changes must invalidate recipient-bearing evidence",
  );
  assert.equal(
    getFamilyScanCount(counter, "heuristic.marketingNewsletter"),
    marketingBeforeTo,
    "to changes must not invalidate subject/snippet/body-only evidence",
  );

  const marketingBeforeBody = getFamilyScanCount(
    counter,
    "heuristic.marketingNewsletter",
  );
  observeKeywordEvidence(counter, () =>
    workspaceShellPerformanceTestSeam.inferHeuristicSignal(
      cloneSeed({ ...baseline, body: ["Changed opaque body"] }),
    ),
  );
  assert.equal(
    getFamilyScanCount(counter, "heuristic.marketingNewsletter"),
    marketingBeforeBody + 1,
    "body changes must invalidate body-bearing evidence",
  );

  const scansBeforeAttachment = new Map(counter.familyScansByName);
  observeKeywordEvidence(counter, () =>
    workspaceShellPerformanceTestSeam.inferHeuristicSignal(
      cloneSeed({
        ...baseline,
        attachments: [{ id: "attachment", name: "changed-name.pdf" }],
      }),
    ),
  );
  const attachmentInvalidatedFamilies = [...counter.familyScansByName]
    .filter(([familyName, count]) => count > (scansBeforeAttachment.get(familyName) ?? 0))
    .map(([familyName]) => familyName);
  assert.ok(attachmentInvalidatedFamilies.length > 0);
  assert.ok(
    attachmentInvalidatedFamilies.every((familyName) =>
      familyName.startsWith("coldOutreach."),
    ),
    "attachment names must invalidate only attachment-bearing evidence",
  );
}

{
  const counter = createKeywordEvidenceCounter();
  const message = createKeywordEvidenceSeed("perf-n3-policy-version");
  observeKeywordEvidence(counter, () =>
    workspaceShellPerformanceTestSeam.inferHeuristicSignal(message),
  );
  const initialScans = counter.familyScans;

  workspaceShellPerformanceTestSeam.withMessageKeywordEvidencePolicyVersion(
    "perf-n3-test-policy-v2",
    () =>
      observeKeywordEvidence(counter, () =>
        workspaceShellPerformanceTestSeam.inferHeuristicSignal(message),
      ),
  );

  assert.equal(
    counter.familyScans,
    initialScans * 2,
    "a PERF-N3 policy-version change must miss every requested evidence family",
  );
}

{
  const counter = createKeywordEvidenceCounter();
  const message = createKeywordEvidenceSeed("perf-n3-explicit-signal", {
    signal: "Priority",
  });
  const signal = observeKeywordEvidence(counter, () =>
    workspaceShellPerformanceTestSeam.inferHeuristicSignal(message),
  );

  assert.equal(signal, "Priority");
  assert.equal(counter.familyScans, 0);
  assert.equal(counter.includesAnyKeywordEvaluations, 0);
}

{
  const counter = createKeywordEvidenceCounter();
  const source = createKeywordEvidenceSeed("perf-n3-dynamic-state");
  const withoutOwner = observeKeywordEvidence(counter, () =>
    normalizeMailMessage(source, "main", {}, {}, "user-1"),
  );
  const scansAfterFirstNormalization = counter.familyScans;
  const ownership = writePersistedMessageOwnershipStateValue(
    {},
    source,
    { userId: "user-1", count: 3 },
    { mailboxId: "main" },
  );
  const ownedByCurrentUser = observeKeywordEvidence(counter, () =>
    normalizeMailMessage(cloneSeed(source), "main", {}, ownership, "user-1"),
  );
  const viewedByOtherUser = observeKeywordEvidence(counter, () =>
    normalizeMailMessage(cloneSeed(source), "main", {}, ownership, "user-2"),
  );

  assert.equal(counter.familyScans, scansAfterFirstNormalization);
  assert.equal(withoutOwner.owner, undefined);
  assert.equal(ownedByCurrentUser.focusSignal, "attention");
  assert.equal(viewedByOtherUser.focusSignal, null);

  const normalCollections = {
    Inbox: [ownedByCurrentUser],
    Drafts: [],
    Sent: [],
    Archive: [],
    Filtered: [],
    Spam: [],
    Trash: [],
  };
  const normal = observeKeywordEvidence(counter, () =>
    applyFocusPreferenceRoutingToMailboxCollections(
      normalCollections,
      mediumFocusPreferences,
      {},
    ),
  );
  const low = observeKeywordEvidence(counter, () =>
    applyFocusPreferenceRoutingToMailboxCollections(
      normalCollections,
      { ...mediumFocusPreferences, business: "low" },
      {},
    ),
  );
  assert.equal(counter.familyScans, scansAfterFirstNormalization);
  assert.deepEqual(normal.Inbox.map((entry) => entry.id), [source.id]);
  assert.deepEqual(low.Filtered.map((entry) => entry.id), [source.id]);
}

{
  const counter = createKeywordEvidenceCounter();
  const source = createKeywordEvidenceSeed("perf-n3-provider-scalars", {
    unread: true,
    flagged: false,
  });
  observeKeywordEvidence(counter, () =>
    normalizeMailMessage(source, "main", {}, {}, "user-1"),
  );
  const scansAfterFirstNormalization = counter.familyScans;
  const changedScalars = observeKeywordEvidence(counter, () =>
    normalizeMailMessage(
      cloneSeed({ ...source, unread: false, flagged: true, labelIds: ["STARRED"] }),
      "main",
      {},
      {},
      "user-1",
    ),
  );

  assert.equal(counter.familyScans, scansAfterFirstNormalization);
  assert.equal(changedScalars.unread, false);
  assert.equal(changedScalars.flagged, true);
}

{
  const counter = createKeywordEvidenceCounter();
  const source = createKeywordEvidenceSeed("perf-n3-learning", {
    internalClassification: "unknown",
  });
  const beforeLearning = observeKeywordEvidence(counter, () =>
    normalizeMailMessage(source, "main", {}, {}, "user-1"),
  );
  const scansAfterFirstNormalization = counter.familyScans;
  const afterLearning = observeKeywordEvidence(counter, () =>
    normalizeMailMessage(
      cloneSeed(source),
      "main",
      {
        [`${source.id}@example.test`]: {
          learnedCategory: "Promo",
          learnedFromCount: 2,
          autoCategoryEnabled: false,
          mailboxAction: "move",
        },
      },
      {},
      "user-1",
    ),
  );

  assert.equal(counter.familyScans, scansAfterFirstNormalization);
  assert.equal(beforeLearning.category, "Primary");
  assert.equal(afterLearning.category, "Promo");
  assert.equal(afterLearning.categorySource, "learned");
}

{
  const counter = createKeywordEvidenceCounter();
  const messages = Array.from({ length: 100 }, (_, index) =>
    createKeywordEvidenceSeed(`perf-n3-synthetic-${index}`, {
      subject: `Opaque synthetic subject ${index}`,
      snippet: `Neutral synthetic snippet ${index}`,
      sender: `Synthetic Sender ${index}`,
      from: `synthetic-${index}@example.test`,
      to: `synthetic-owner-${index}@example.test`,
      body: [`Neutral synthetic body ${index}`],
    }),
  );
  let scansAfterFirstPass = 0;
  let includesAfterFirstPass = 0;

  for (let pass = 0; pass < 10; pass += 1) {
    const normalized = observeKeywordEvidence(counter, () =>
      messages.map((message) =>
        normalizeMailMessage(
          message,
          "main",
          {},
          {},
          pass % 2 === 0 ? "user-1" : "user-2",
        ),
      ),
    );
    assert.equal(normalized.length, messages.length);
    if (pass === 0) {
      scansAfterFirstPass = counter.familyScans;
      includesAfterFirstPass = counter.includesAnyKeywordEvaluations;
    }
  }

  const historicalRepeatedScanLowerBound = scansAfterFirstPass * 10;
  assert.ok(scansAfterFirstPass > 0);
  assert.equal(counter.familyScans, scansAfterFirstPass);
  assert.equal(counter.includesAnyKeywordEvaluations, includesAfterFirstPass);
  assert.ok(historicalRepeatedScanLowerBound > counter.familyScans);
  process.stdout.write(
    `PERF-N3 synthetic keyword-family scans: before>=${historicalRepeatedScanLowerBound}, after=${counter.familyScans}\n`,
  );
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

{
  const counter = { count: 0 };
  const normalized = normalizeMailMessage(
    createSeed("classification-same-object-setup", { bodyHtml: undefined }),
    "main",
    {},
    {},
    "user-1",
  );
  const seed = withClassificationCountingBody(
    {
      ...normalized,
      id: "classification-same-object",
      internalClassification: "unknown",
      subject: "Newsletter update",
      snippet: "Please review the latest campaign",
      body: ["Register now", "Unsubscribe"],
    },
    counter,
  );
  const firstResult = exerciseClassificationPaths(seed);

  for (let pass = 0; pass < 10; pass += 1) {
    assert.deepEqual(exerciseClassificationPaths(seed), firstResult);
  }

  assert.equal(
    counter.count,
    1,
    "the same message must join classification body text once",
  );
}

{
  const counter = { count: 0 };
  const normalized = normalizeMailMessage(
    createSeed("classification-recreated-setup", { bodyHtml: undefined }),
    "main",
    {},
    {},
    "user-1",
  );
  const seed = {
    ...normalized,
    id: "classification-recreated",
    internalClassification: "unknown" as const,
    subject: "Classification recreated subject",
    body: ["A stable first paragraph", "A stable second paragraph"],
  };
  const first = withClassificationCountingBody(seed, counter);
  const recreated = withClassificationCountingBody(seed, counter);

  assert.deepEqual(
    exerciseClassificationPaths(recreated),
    exerciseClassificationPaths(first),
  );
  assert.equal(
    counter.count,
    1,
    "an exact recreated message must reuse classification text structurally",
  );
}

{
  const counter = { count: 0 };
  const normalized = normalizeMailMessage(
    createSeed("classification-invalidation-setup", { bodyHtml: undefined }),
    "main",
    {},
    {},
    "user-1",
  );
  const baseline = {
    ...normalized,
    id: "classification-invalidation",
    internalClassification: "unknown",
    subject: "Classification invalidation subject",
    body: ["First paragraph", "Second paragraph"],
  } as ReturnType<typeof normalizeMailMessage>;
  const variants: Array<ReturnType<typeof normalizeMailMessage>> = [
    baseline,
    { ...baseline, subject: "Changed subject" },
    { ...baseline, snippet: "Changed snippet" },
    { ...baseline, sender: "Changed sender" },
    { ...baseline, from: "changed-from@example.test" },
    { ...baseline, to: "changed-to@example.test" },
    { ...baseline, body: ["Changed body", "Second paragraph"] },
    { ...baseline, body: ["Second paragraph", "First paragraph"] },
    {
      ...baseline,
      attachments: [{ id: "changed-name", name: "changed-name.pdf", size: 128 }],
    },
  ];

  variants.forEach((variant) => {
    exerciseClassificationPaths(withClassificationCountingBody(variant, counter));
  });

  assert.equal(
    counter.count,
    variants.length,
    "every classification-relevant text change must invalidate the projection",
  );
}

{
  const counter = { count: 0 };
  const normalized = normalizeMailMessage(
    createSeed("classification-eviction-setup", { bodyHtml: undefined }),
    "main",
    {},
    {},
    "user-1",
  );
  const oldest = {
    ...normalized,
    id: "classification-eviction-oldest",
    subject: "Classification eviction oldest",
  };
  exerciseClassificationPaths(withClassificationCountingBody(oldest, counter));
  for (let index = 0; index < 64; index += 1) {
    exerciseClassificationPaths(
      withClassificationCountingBody(
        {
          ...normalized,
          id: `classification-eviction-fill-${index}`,
          subject: `Classification eviction fill ${index}`,
        },
        counter,
      ),
    );
  }
  exerciseClassificationPaths(withClassificationCountingBody(oldest, counter));

  assert.equal(
    counter.count,
    66,
    "the oldest classification projection must evict after 64 structural entries",
  );
}

{
  const counter = { count: 0 };
  const normalized = normalizeMailMessage(
    createSeed("classification-pathological-setup", { bodyHtml: undefined }),
    "main",
    {},
    {},
    "user-1",
  );
  const messages = Array.from({ length: 64 }, (_, index) =>
    withClassificationCountingBody(
      {
        ...normalized,
        id: `classification-pathological-${index}`,
        internalClassification: "unknown" as const,
        subject:
          index % 2 === 0 ? "Newsletter update" : "Please review contract",
        body: [`Synthetic body ${index}`, "Unsubscribe or reply with approval"],
      },
      counter,
    ),
  );
  const collections = {
    Inbox: messages.map((message) => ({
      ...message,
      body: [...message.body],
    })),
    Drafts: [],
    Sent: [],
    Archive: [],
    Filtered: [],
    Spam: [],
    Trash: [],
  };

  for (let pass = 0; pass < 10; pass += 1) {
    messages.forEach(exerciseClassificationPaths);
    const routed = applyFocusPreferenceRoutingToMailboxCollections(
      collections,
      pass % 2 === 0
        ? mediumFocusPreferences
        : { ...mediumFocusPreferences, updates: "low" },
      {},
    );
    assert.equal(routed.Inbox.length + routed.Filtered.length, messages.length);
  }

  assert.equal(
    counter.count,
    64,
    "64 messages across repeated classification and routing passes must join 64 bodies",
  );
}

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
