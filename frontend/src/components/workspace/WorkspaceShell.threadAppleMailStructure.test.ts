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

const subjectHelpersStart = workspaceShellSource.indexOf(
  "function normalizeConversationDisplaySubject(",
);
const subjectHelpersEnd = workspaceShellSource.indexOf(
  "function resolveMailDateMs(",
  subjectHelpersStart,
);

expectContract(
  subjectHelpersStart >= 0 && subjectHelpersEnd > subjectHelpersStart,
  "a presentation-only canonical conversation subject helper must exist",
);

if (subjectHelpersStart >= 0 && subjectHelpersEnd > subjectHelpersStart) {
  const compiledSubjectHelpers = transform(
    workspaceShellSource.slice(subjectHelpersStart, subjectHelpersEnd),
    { transforms: ["typescript"] },
  ).code;
  const loadSubjectHarness = new Function(
    `${compiledSubjectHelpers}\nreturn { getConversationDisplaySubject, normalizeConversationDisplaySubject };`,
  ) as () => {
    getConversationDisplaySubject: (
      messages: Array<{ subject?: string | null }>,
      selectedSubject?: string | null,
    ) => string;
    normalizeConversationDisplaySubject: (subject?: string | null) => string;
  };
  const { getConversationDisplaySubject, normalizeConversationDisplaySubject } =
    loadSubjectHarness();

  expectContract(
    getConversationDisplaySubject(
      [
        { subject: "Cuevion Custom Thread Test 1" },
        { subject: "Re: Cuevion Custom Thread Test 1" },
      ],
      "Re: Cuevion Custom Thread Test 1",
    ) === "Cuevion Custom Thread Test 1",
    "the earliest chronological root subject must win over a selected reply subject",
  );
  expectContract(
    normalizeConversationDisplaySubject("  Fwd:   Re:  Re:  Demo   Submission  ") ===
      "Demo Submission",
    "repeated Re/Fw/Fwd prefixes and excess whitespace must be stripped",
  );
  expectContract(
    getConversationDisplaySubject([{ subject: "  " }], " Fw:   ") === "No subject",
    "an empty root and selected subject must fall back to No subject",
  );
}

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
  /<article[\s\S]*data-thread-message-id=\{threadMessage\.id\}/.test(
    threadMessageSource,
  ),
  "each physical message must be an article with its own identity",
);
expectContract(
  /<time\s+dateTime=\{resolvedTimestamp\.dateTime\}>\{resolvedTimestamp\.label\}<\/time>/.test(
    threadMessageSource,
  ),
  "valid createdAt values must be exposed through a semantic time element",
);
expectContract(
  /const visibleAttachments\s*=\s*\(threadMessage\.attachments \?\? \[\]\)\s*\.filter\(shouldShowInAttachmentList\)/.test(
    threadMessageSource,
  ),
  "each physical message must derive its own visible attachments",
);
expectContract(
  /visibleAttachments\.length > 0[\s\S]*renderAttachmentItem\(attachment, \{[\s\S]*message: threadMessage/.test(
    threadMessageSource,
  ),
  "real and older-member attachments must render under their owning message",
);
expectContract(
  !/No attachments/.test(threadMessageSource),
  "zero-visible and inline-only messages must omit attachment empty state",
);
expectContract(
  !/isCurrentUser \? "pl-4 md:pl-8"/.test(threadMessageSource),
  "outgoing messages must not receive additional left indentation",
);
expectContract(
  /const messageBlockClassName\s*=\s*"[^"]*rounded-\[14px\][^"]*border border-\[var\(--workspace-border-soft\)\][^"]*bg-\[var\(--workspace-card-subtle\)\][^"]*px-4 py-3\.5[^"]*md:px-5 md:py-4[^"]*"/.test(
    threadMessageSource,
  ),
  "physical messages must use one calm rounded, bordered, subtly tinted block style",
);
expectContract(
  (threadMessageSource.match(/className=\{messageBlockClassName\}/g) ?? [])
    .length === 2,
  "collapsed and expanded articles must share the same message-block surface",
);
expectContract(
  /data-thread-message-actions[\s\S]*\{options\.actions\}/.test(
    threadMessageSource,
  ) &&
    /isLatestThreadMessage && actionMessage[\s\S]*renderMessageActions\(actionMessage, density\)/.test(
      threadTimelineSource,
    ),
  "the existing action row must render inside the latest physical message block",
);

expectContract(
  /<section\s+aria-labelledby=\{labelledById\}[\s\S]*className="space-y-3\.5"/.test(
    threadTimelineSource,
  ),
  "the shared conversation timeline must be an accessible labelled section",
);
expectContract(
  !/data-thread-message-divider|role="separator"/.test(threadTimelineSource),
  "message-block spacing must replace the old cross-member divider",
);
expectContract(
  !/historical:/.test(threadTimelineSource),
  "the divider contract must not use historical-message border heuristics",
);

const splitReadingStart = workspaceShellSource.indexOf(
  "ref={readingPaneViewportRef}",
);
const splitReadingEnd = workspaceShellSource.indexOf(
  "{isComposeOpen &&",
  splitReadingStart,
);
const splitReadingSource = workspaceShellSource.slice(
  splitReadingStart,
  splitReadingEnd,
);
const fullMessageStart = workspaceShellSource.indexOf(
  "{fullMessageModalMessage && typeof document",
);
const fullMessageEnd = workspaceShellSource.indexOf(
  "data-full-message-modal-resize-handle",
  fullMessageStart,
);
const fullMessageSource = workspaceShellSource.slice(fullMessageStart, fullMessageEnd);

for (const [surface, source] of [
  ["split", splitReadingSource],
  ["full", fullMessageSource],
] as const) {
  expectContract(
    /renderThreadTimeline\(/.test(source) && !/renderThreadMessage\(/.test(source),
    `${surface} must always use the shared thread timeline, including one-member conversations`,
  );
  expectContract(
    !/>From:</.test(source) &&
      !/>To:</.test(source) &&
      !/>Received:</.test(source),
    `${surface} must not retain the conversation-wide selected-message metadata shell`,
  );
  expectContract(
    !/No attachments/.test(source),
    `${surface} must not retain a conversation-wide empty attachment shell`,
  );
}

assert.equal(
  failures.length,
  0,
  `Apple Mail thread structure contract failures:\n${failures
    .map((failure) => `- ${failure}`)
    .join("\n")}`,
);
