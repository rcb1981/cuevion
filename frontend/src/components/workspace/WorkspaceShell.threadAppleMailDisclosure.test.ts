import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { transform } from "sucrase";

const workspaceShellSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);

const failures: string[] = [];
const expectContract = (condition: boolean, message: string) => {
  if (!condition) {
    failures.push(message);
  }
};

const disclosureHelpersStart = workspaceShellSource.indexOf(
  "function hasReliableQuotedContent(",
);
const disclosureHelpersEnd = workspaceShellSource.indexOf(
  "function resolveMailDateMs(",
  disclosureHelpersStart,
);

expectContract(
  disclosureHelpersStart >= 0 && disclosureHelpersEnd > disclosureHelpersStart,
  "deterministic desktop thread disclosure helpers must exist",
);

if (disclosureHelpersStart >= 0 && disclosureHelpersEnd > disclosureHelpersStart) {
  const compiledDisclosureHelpers = transform(
    workspaceShellSource.slice(disclosureHelpersStart, disclosureHelpersEnd),
    { transforms: ["typescript"] },
  ).code;
  const loadDisclosureHarness = new Function(
    `${compiledDisclosureHelpers}\nreturn { buildReliableQuoteVisibilityStyle, hasReliableQuotedContent, reconcileDesktopThreadDisclosureState, resolveInitialExpandedThreadMessageIds, toggleDisclosureId };`,
  ) as () => {
    buildReliableQuoteVisibilityStyle: (expanded: boolean) => string;
    hasReliableQuotedContent: (html?: string | null) => boolean;
    reconcileDesktopThreadDisclosureState: (
      state: DisclosureState,
      threadKey: string | null,
    ) => DisclosureState;
    resolveInitialExpandedThreadMessageIds: (messageIds: string[]) => string[];
    toggleDisclosureId: (ids: string[], id: string) => string[];
  };
  const {
    buildReliableQuoteVisibilityStyle,
    hasReliableQuotedContent,
    reconcileDesktopThreadDisclosureState,
    resolveInitialExpandedThreadMessageIds,
    toggleDisclosureId,
  } = loadDisclosureHarness();

  for (const [label, html] of [
    ["Cuevion compose quote", '<div data-compose-quote="true">History</div>'],
    ["sanitized email quote", '<div data-email-quote="true">History</div>'],
    ["Gmail quote", '<div class="gmail_quote">History</div>'],
  ] as const) {
    expectContract(
      hasReliableQuotedContent(html),
      `${label} must be reliably collapsible`,
    );
  }
  expectContract(
    !hasReliableQuotedContent("<p>Authored body</p>"),
    "ordinary authored HTML must not gain quote disclosure",
  );
  expectContract(
    !hasReliableQuotedContent("<blockquote>Ambiguous prose</blockquote>"),
    "a generic blockquote alone must not be treated as reliable imported history",
  );
  expectContract(
    !hasReliableQuotedContent("On Friday, someone wrote: ambiguous plain text"),
    "ambiguous text-only prose must remain visible",
  );

  const collapsedQuoteStyle = buildReliableQuoteVisibilityStyle(false);
  expectContract(
    /data-compose-quote/.test(collapsedQuoteStyle) &&
      /data-email-quote/.test(collapsedQuoteStyle) &&
      /gmail_quote/.test(collapsedQuoteStyle) &&
      /display:\s*none\s*!important/.test(collapsedQuoteStyle),
    "reliable quote selectors must be hidden by default without deleting content",
  );
  expectContract(
    !/signature/i.test(collapsedQuoteStyle),
    "quote collapse CSS must not target authored signatures",
  );
  expectContract(
    buildReliableQuoteVisibilityStyle(true) === "",
    "revealed quotes must retain their complete rendered content",
  );

  expectContract(
    JSON.stringify(resolveInitialExpandedThreadMessageIds(["one"])) ===
      JSON.stringify(["one"]),
    "one-member threads must start expanded",
  );
  expectContract(
    JSON.stringify(resolveInitialExpandedThreadMessageIds(["one", "two"])) ===
      JSON.stringify(["one", "two"]),
    "two-member threads must both start expanded",
  );
  expectContract(
    JSON.stringify(resolveInitialExpandedThreadMessageIds(["one", "two", "three"])) ===
      JSON.stringify(["three"]),
    "three-member threads must start with only the latest expanded",
  );
  expectContract(
    JSON.stringify(
      resolveInitialExpandedThreadMessageIds(["one", "two", "three", "four", "five"]),
    ) === JSON.stringify(["five"]),
    "five-member threads must start with older summaries and the latest expanded",
  );

  const expandedHistoricalIds = toggleDisclosureId([], "two");
  expectContract(
    expandedHistoricalIds.includes("two") &&
      !toggleDisclosureId(expandedHistoricalIds, "two").includes("two"),
    "historical members must toggle collapsed to expanded and back",
  );
  const expandedQuoteIds = toggleDisclosureId([], "reply");
  expectContract(
    expandedQuoteIds.includes("reply") &&
      !toggleDisclosureId(expandedQuoteIds, "reply").includes("reply"),
    "quote disclosure must toggle show to hide per physical message ID",
  );

  const existingState: DisclosureState = {
    expandedMemberIds: ["older"],
    expandedQuoteMessageIds: ["reply"],
    threadKey: "thread-a",
  };
  expectContract(
    reconcileDesktopThreadDisclosureState(existingState, "thread-a") === existingState,
    "split and full presentation of the same thread must preserve disclosure state",
  );
  expectContract(
    JSON.stringify(reconcileDesktopThreadDisclosureState(existingState, "thread-b")) ===
      JSON.stringify({
        expandedMemberIds: [],
        expandedQuoteMessageIds: [],
        threadKey: "thread-b",
      }),
    "selecting another conversation must reset member and quote disclosure state",
  );
}

type DisclosureState = {
  expandedMemberIds: string[];
  expandedQuoteMessageIds: string[];
  threadKey: string | null;
};

const threadMessageStart = workspaceShellSource.indexOf("const renderThreadMessage =");
const threadTimelineStart = workspaceShellSource.indexOf(
  "const renderThreadTimeline =",
  threadMessageStart,
);
const threadTimelineEnd = workspaceShellSource.indexOf(
  "const activeStoredCollaborationMessage =",
  threadTimelineStart,
);
const threadMessageSource = workspaceShellSource.slice(
  threadMessageStart,
  threadTimelineStart,
);
const threadTimelineSource = workspaceShellSource.slice(
  threadTimelineStart,
  threadTimelineEnd,
);

expectContract(
  /aria-expanded=\{quoteExpanded\}[\s\S]*aria-controls=\{quoteContentId\}[\s\S]*Show quoted content/.test(
    threadMessageSource,
  ) && /Hide quoted content/.test(threadMessageSource),
  "reliable quote history must expose an accessible Show/Hide quoted content control",
);
expectContract(
  /aria-expanded=\{!collapsed\}[\s\S]*aria-controls=\{messageContentId\}/.test(
    threadMessageSource,
  ),
  "collapsed member summaries must expose accessible disclosure state",
);
expectContract(
  /visibleAttachments\.length > 0[\s\S]*aria-label=\{`\$\{visibleAttachments\.length\} visible/.test(
    threadMessageSource,
  ),
  "collapsed members must show an attachment indicator only for real visible attachments",
);
expectContract(
  /resolveInitialExpandedThreadMessageIds\(\s*threadMessages\.map/.test(
    threadTimelineSource,
  ) &&
    /threadIndex\s*===\s*threadMessages\.length - 1/.test(threadTimelineSource),
  "the shared timeline must apply the one/two/three-plus initial member policy",
);
expectContract(
  /setDesktopThreadDisclosureState/.test(threadMessageSource + threadTimelineSource),
  "split and full must mutate one shared disclosure state inside MailboxView",
);
expectContract(
  (threadTimelineSource.match(/data-thread-message-divider/g) ?? []).length === 1 &&
    /threadIndex > 0/.test(threadTimelineSource),
  "disclosure must retain the Slice A N-1 divider contract",
);

const emailStageDocumentStart = workspaceShellSource.indexOf(
  "function buildEmailStageDocument(",
);
const emailStageDocumentEnd = workspaceShellSource.indexOf(
  "type RgbColor =",
  emailStageDocumentStart,
);
const emailStageDocumentSource = workspaceShellSource.slice(
  emailStageDocumentStart,
  emailStageDocumentEnd,
);
expectContract(
  /buildReliableQuoteVisibilityStyle\(\s*options\?\.quotedContentExpanded/.test(
    emailStageDocumentSource,
  ),
  "iframe quote disclosure must use React-owned safe CSS in sanitized srcDoc",
);

assert.equal(
  failures.length,
  0,
  `Apple Mail thread disclosure contract failures:\n${failures
    .map((failure) => `- ${failure}`)
    .join("\n")}`,
);
