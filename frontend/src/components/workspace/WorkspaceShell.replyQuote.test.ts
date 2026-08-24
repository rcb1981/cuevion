import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { transform } from "sucrase";
import { applyLiveThreadIdentity } from "../../lib/inboxEngine";

const workspaceShellSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);
const desktopComposeBodyEditorSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/DesktopComposeBodyEditor.tsx"),
  "utf8",
);

const quoteHelpersStart = workspaceShellSource.indexOf("function escapeComposeHtml(");
const quoteHelpersEnd = workspaceShellSource.indexOf(
  "function buildComposeSignatureMarkup(",
  quoteHelpersStart,
);
const stripQuotePrefixStart = workspaceShellSource.indexOf(
  "function stripQuoteParagraphPrefix(",
);
const stripQuotePrefixEnd = workspaceShellSource.indexOf(
  "interface ParsedQuoteSection",
  stripQuotePrefixStart,
);

assert.ok(
  quoteHelpersStart >= 0 && quoteHelpersEnd > quoteHelpersStart,
  "compose quote helper source markers must be present and ordered",
);
assert.ok(
  stripQuotePrefixStart >= 0 && stripQuotePrefixEnd > stripQuotePrefixStart,
  "quote-prefix helper source markers must be present and ordered",
);

const quoteHelpersSource = workspaceShellSource.slice(quoteHelpersStart, quoteHelpersEnd);
const compiledQuoteHelpers = transform(
  `${workspaceShellSource.slice(stripQuotePrefixStart, stripQuotePrefixEnd)}\n${quoteHelpersSource}`,
  { transforms: ["typescript"] },
).code;
const loadQuoteHarness = new Function(
  `function resolveMessageBodyRenderMode(message) {
    return message.bodyHtml
      ? { mode: "html", html: message.bodyHtml }
      : { mode: "plain" };
  }
  ${compiledQuoteHelpers}
  return {
    buildComposeQuoteHtml,
    buildComposeParagraphsHtml,
    resolveComposeQuoteHeaderTimestamp,
  };`,
) as () => {
  buildComposeQuoteHtml: (mode: string, sourceMessage: QuoteMessage) => string;
  buildComposeParagraphsHtml: (value: string) => string;
  resolveComposeQuoteHeaderTimestamp: (
    sourceMessage: Pick<QuoteMessage, "createdAt" | "timestamp">,
    nowMs?: number,
  ) => string;
};
const quoteHarness = loadQuoteHarness();

const extractComposePlainTextStart = workspaceShellSource.indexOf(
  "function extractComposePlainText(",
);
const extractComposePlainTextEnd = workspaceShellSource.indexOf(
  "function extractComposeParagraphs(",
  extractComposePlainTextStart,
);
assert.ok(
  extractComposePlainTextStart >= 0 &&
    extractComposePlainTextEnd > extractComposePlainTextStart,
  "compose plain-text serializer source markers must be present and ordered",
);
const compiledComposePlainTextExtractor = transform(
  workspaceShellSource.slice(extractComposePlainTextStart, extractComposePlainTextEnd),
  { transforms: ["typescript"] },
).code;
const loadComposePlainTextExtractor = new Function(
  `${compiledComposePlainTextExtractor}\nreturn extractComposePlainText;`,
) as () => (html: string) => string;
const extractComposePlainText = loadComposePlainTextExtractor();

type QuoteMessage = {
  body: string[];
  bodyHtml?: string;
  cc?: string;
  createdAt?: string;
  from: string;
  subject: string;
  timestamp: string;
  to: string;
};

const conversationalMessage: QuoteMessage = {
  body: [
    "Hi Rutger,",
    "Can you confirm the timing?",
    "Thanks,\nSophie & Co.",
  ],
  from: "Sophie <sophie@example.com>",
  subject: "Timing & launch",
  timestamp: "Fri, Aug 14, 2026 at 10:15 AM",
  to: "Rutger <rutger@example.com>",
};

const expectedConversationalReply =
  '<div data-compose-quote="true"><div>On Fri, Aug 14, 2026 at 10:15 AM, Sophie &lt;sophie@example.com&gt; wrote:</div><div><br></div><div>Hi Rutger,</div><div><br></div><div>Can you confirm the timing?</div><div><br></div><div>Thanks,</div><div>Sophie &amp; Co.</div></div>';

assert.equal(
  quoteHarness.buildComposeQuoteHtml("reply", conversationalMessage),
  expectedConversationalReply,
  "plain Reply must retain attribution, paragraphs, safe escaping, and the quote marker",
);

const syntheticSentMessage: QuoteMessage = {
  ...conversationalMessage,
  body: ["Reply vanuit Cuevion Custom IMAP."],
  createdAt: "2026-08-16T13:12:00+02:00",
  from: "promo@hysteriarecs.com",
  timestamp: "Sent just now",
};
const syntheticSentReplyQuote = quoteHarness.buildComposeQuoteHtml(
  "reply",
  syntheticSentMessage,
);

assert.match(
  syntheticSentReplyQuote,
  /On August 16 at 13:12, promo@hysteriarecs\.com wrote:/,
  "Reply must format a synthetic Sent quote header from its real createdAt",
);
assert.doesNotMatch(
  syntheticSentReplyQuote,
  /Sent just now/,
  "a stale synthetic Sent label must not remain quote-header authority",
);
assert.equal(
  quoteHarness.buildComposeQuoteHtml("reply_all", syntheticSentMessage),
  syntheticSentReplyQuote,
  "Reply All must use the same createdAt-backed quote header as Reply",
);
assert.equal(
  quoteHarness.resolveComposeQuoteHeaderTimestamp(
    {
      createdAt: "2025-08-16T13:12:00+02:00",
      timestamp: "stale label",
    },
    new Date("2026-08-16T12:00:00+02:00").getTime(),
  ),
  "August 16, 2025 at 13:12",
  "quote dates from another year must include that year",
);
assert.equal(
  quoteHarness.resolveComposeQuoteHeaderTimestamp(
    { createdAt: "not-a-date", timestamp: "Existing timestamp" },
    new Date("2026-08-16T12:00:00+02:00").getTime(),
  ),
  "Existing timestamp",
  "invalid createdAt values must preserve the existing timestamp fallback",
);

const simpleHtmlMessage: QuoteMessage = {
  ...conversationalMessage,
  body: ["Hi Rutger,", "Can you confirm the timing?"],
  bodyHtml:
    "<p>Hi Rutger,</p><p><strong>Can you confirm the timing?</strong></p>",
};
const newsletterMessage: QuoteMessage = {
  ...conversationalMessage,
  body: [
    "Summer campaign",
    "A very large launch",
    "Recognizable campaign details and an offer for the recipient.",
    "View offer",
    "Footer and unsubscribe details",
  ],
  bodyHtml: `
    <div class="newsletter" style="display:block;background:#111">
      <style>
        .newsletter .hero { font-size: 36px; margin: 48px; padding: 56px; }
        .newsletter table { table-layout: fixed; }
      </style>
      <table width="720" height="900" style="width:720px;height:900px">
        <tr><td>
          <div class="hero">Summer campaign</div>
          <table width="640"><tr><td>A very large launch</td></tr></table>
          <img src="https://example.com/hero.png" width="600" height="400" alt="Campaign hero">
          <div style="height:180px">Spacer section</div>
          <div>Footer and unsubscribe details</div>
        </td></tr>
      </table>
    </div>
  `,
};
const styledHtmlMessage: QuoteMessage = {
  ...conversationalMessage,
  body: ["Hi Rutger,", "Styled but still recognizable."],
  bodyHtml: `
    <style>.sender-card { font-size: 42px; margin: 60px; }</style>
    <div class="sender-card" style="display:grid;padding:48px;width:700px;height:500px;background:red">
      Hi Rutger,<br>Styled but still recognizable.
    </div>
  `,
};

const correctionFailures: string[] = [];
const recordCorrectionExpectation = (name: string, expectation: () => void) => {
  try {
    expectation();
  } catch (error) {
    correctionFailures.push(
      `${name}: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
};

recordCorrectionExpectation("simple HTML Reply uses normalized body text", () => {
  const quote = quoteHarness.buildComposeQuoteHtml("reply", simpleHtmlMessage);

  assert.match(quote, /Hi Rutger,/);
  assert.match(quote, /Can you confirm the timing\?/);
  assert.doesNotMatch(quote, /<(?:p|strong)\b/i);
  assert.match(quote, /data-compose-quote="true"/);
});

recordCorrectionExpectation("newsletter Reply strips rich layout authority", () => {
  const quote = quoteHarness.buildComposeQuoteHtml("reply", newsletterMessage);

  assert.match(quote, /Summer campaign/);
  assert.match(quote, /A very large launch/);
  assert.match(quote, /Footer and unsubscribe details/);
  assert.doesNotMatch(quote, /<(?:table|style|img)\b/i);
  assert.doesNotMatch(quote, /\b(?:width|height|class|style)=/i);
  assert.match(quote, /data-compose-quote="true"/);
});

recordCorrectionExpectation("styled HTML Reply strips sender styling", () => {
  const quote = quoteHarness.buildComposeQuoteHtml("reply", styledHtmlMessage);

  assert.match(quote, /Styled but still recognizable\./);
  assert.doesNotMatch(
    quote,
    /<(?:style|table|img)\b|\b(?:class|style|width|height)=|font-size|margin|padding|display|table-layout|background/i,
  );
});

recordCorrectionExpectation("Reply All shares the normalized Reply quote", () => {
  assert.equal(
    quoteHarness.buildComposeQuoteHtml("reply_all", newsletterMessage),
    quoteHarness.buildComposeQuoteHtml("reply", newsletterMessage),
  );
});

recordCorrectionExpectation("Reply quote generation does not use reading HTML", () => {
  const buildComposeQuoteSource = quoteHelpersSource.slice(
    quoteHelpersSource.indexOf("function buildComposeQuoteHtml("),
  );

  assert.doesNotMatch(
    buildComposeQuoteSource,
    /resolveMessageBodyRenderMode|sourceRenderMode\.html|sanitizeComposeQuotedHtmlTextColors/,
  );
});

assert.deepEqual(
  correctionFailures,
  [],
  `compact Reply quote expectations failed:\n${correctionFailures
    .map((failure) => `- ${failure}`)
    .join("\n")}`,
);

const forwardMessage: QuoteMessage = {
  ...conversationalMessage,
  cc: "Team <team@example.com>",
  body: ["Hi Rutger,", "Can you confirm the timing?"],
};
const expectedForwardQuote =
  '<div data-compose-quote="true"><div>Forwarded message:</div><div><br></div><div>From: Sophie &lt;sophie@example.com&gt;</div><div>To: Rutger &lt;rutger@example.com&gt;</div><div>Cc: Team &lt;team@example.com&gt;</div><div>Time: Fri, Aug 14, 2026 at 10:15 AM</div><div>Subject: Timing &amp; launch</div><div><br></div><div>Hi Rutger,</div><div><br></div><div>Can you confirm the timing?</div></div>';

assert.equal(
  quoteHarness.buildComposeQuoteHtml("forward", forwardMessage),
  expectedForwardQuote,
  "Forward quote output must remain byte-for-byte unchanged",
);

const openComposeSource = workspaceShellSource.slice(
  workspaceShellSource.indexOf("const openComposeFromMessage ="),
  workspaceShellSource.indexOf("useEffect(() =>", workspaceShellSource.indexOf("const openComposeFromMessage =")),
);
const replyRecipientPlanSource = workspaceShellSource.slice(
  workspaceShellSource.indexOf("type ReplyRecipientToken ="),
  workspaceShellSource.indexOf("function normalizeSenderLearningKey("),
);
assert.match(replyRecipientPlanSource, /function normalizeReplyRecipientKey/);
assert.match(replyRecipientPlanSource, /function deriveReplyRecipientPlan/);
assert.match(
  openComposeSource,
  /deriveReplyRecipientPlan\([\s\S]*ownAddresses: orderedMailboxes\.map/,
  "Reply ownership must continue using connected mailbox identities only",
);
const replyRecipientSource = openComposeSource.slice(
  openComposeSource.indexOf("const replyRecipientPlan ="),
  openComposeSource.indexOf("closeReadingLearningMenu();"),
);
assert.match(
  replyRecipientSource,
  /originalSender: effectiveMessage\.from,[\s\S]*originalTo: effectiveMessage\.to,[\s\S]*originalCc: effectiveMessage\.cc \?\? ""/,
  "Reply-after-Sent recipients must come from the selected presentation source",
);
assert.doesNotMatch(
  replyRecipientSource,
  /replyAnchorMessage/,
  "the provider-only reply anchor must not affect Reply or Reply All recipients",
);
assert.match(
  openComposeSource,
  /selectLatestAuthoritativeConversationMessage\(/,
  "normal Reply source must use canonical authoritative conversation membership",
);
for (const folder of ["Inbox", "Archive", "Filtered", "Sent"]) {
  assert.match(
    openComposeSource,
    new RegExp(`"${folder}"`),
    `${folder} must remain a qualifying normal Reply source folder`,
  );
}
const normalReplyFolderSource = openComposeSource.slice(
  openComposeSource.indexOf("const normalReplySourceFolders"),
  openComposeSource.indexOf("]);", openComposeSource.indexOf("const normalReplySourceFolders")) + 3,
);
assert.doesNotMatch(
  normalReplyFolderSource,
  /Drafts|Trash|Spam/,
  "Drafts, Trash, and Spam must not replace a normal Reply source",
);
assert.match(
  openComposeSource,
  /selectedSourceFolder === "Trash" \|\| selectedSourceFolder === "Spam"/,
  "explicit Trash and Spam Reply behavior must remain folder-specific",
);
assert.match(
  openComposeSource,
  /createReplyModeSession\([\s\S]*setComposeSourceMessage\(effectiveMessage\)[\s\S]*setComposeTo\(initialReplyModeState\?\.to \?\? ""\)[\s\S]*setComposeCc\(initialReplyModeState\?\.cc \?\? ""\)/,
  "Reply and Reply All must initialize recipients from one immutable session plan",
);

const signatureSource = workspaceShellSource.slice(
  workspaceShellSource.indexOf("function buildComposeSignatureSpacerMarkup("),
  workspaceShellSource.indexOf("const unsafeEmailHtmlSelectors"),
);
assert.match(signatureSource, /data-compose-signature-spacer="true"/);
assert.match(signatureSource, /container\.querySelector\("\[data-compose-quote\]"\)/);
assert.match(
  signatureSource,
  /quoteNode\.parentNode\.insertBefore\(fragment, quoteNode\)/,
  "signature spacers and signature must remain immediately before quoted history",
);

const sendSource = workspaceShellSource.slice(
  workspaceShellSource.indexOf("const sendMessage = async"),
  workspaceShellSource.indexOf("const closeMenus ="),
);
assert.match(
  sendSource,
  /const activeBodyHtml = options\?\.bodyHtml \?\? getCurrentComposeBodyHtml\(\)/,
);
assert.match(sendSource, /const bodyPreview = extractComposePlainText\(activeBodyHtml\)/);
assert.match(sendSource, /bodyHtml: activeBodyHtml/);
assert.match(sendSource, /bodyText: bodyPreview \|\| " "/);

const composeStateSource = workspaceShellSource.slice(
  workspaceShellSource.indexOf('const [composeTo, setComposeTo]'),
  workspaceShellSource.indexOf('const [pendingComposeAttachmentPickerOpen'),
);
const resetComposeSource = workspaceShellSource.slice(
  workspaceShellSource.indexOf("const resetComposeState ="),
  workspaceShellSource.indexOf("const normalizeRememberedRecipient ="),
);
const desktopComposerSource = workspaceShellSource.slice(
  workspaceShellSource.indexOf("const desktopComposeContent ="),
  workspaceShellSource.indexOf(
    "return (",
    workspaceShellSource.indexOf("const desktopComposer ="),
  ),
);

assert.match(
  desktopComposerSource,
  /<DesktopComposeBodyEditor[\s\S]*className=\{`min-h-\[260px\]/,
  "the shared desktop compose writing area must use the compact minimum height",
);
assert.match(desktopComposeBodyEditorSource, /id="desktop-compose-body"/);
assert.doesNotMatch(
  desktopComposerSource,
  /<DesktopComposeBodyEditor[\s\S]*className=\{`min-h-\[360px\]/,
  "the desktop compose writing area must not reserve the old 360px minimum",
);

assert.match(
  composeStateSource,
  /const \[composeQuoteExpanded, setComposeQuoteExpanded\] = useState\(false\)/,
  "desktop compose quote disclosure must start collapsed",
);
assert.match(
  resetComposeSource,
  /setComposeQuoteExpanded\(false\)/,
  "every compose session must reset quote disclosure to collapsed",
);
assert.match(
  desktopComposerSource,
  /composeMode === "reply" \|\| composeMode === "reply_all"/,
  "quote disclosure must be limited to Reply and Reply All",
);
assert.match(
  desktopComposeBodyEditorSource,
  /aria-expanded=\{quoteExpanded\}[\s\S]*aria-controls="desktop-compose-body"[\s\S]*Hide quoted content[\s\S]*Show quoted content/,
  "Reply quote disclosure must expose accessible Show/Hide state",
);
assert.match(
  desktopComposerSource,
  /!composeQuoteExpanded[\s\S]*\[&_\[data-compose-quote='true'\]\]:hidden/,
  "collapsed quote presentation must hide the retained quote from the editor parent",
);
const quoteToggleSource = desktopComposeBodyEditorSource.slice(
  desktopComposeBodyEditorSource.indexOf("aria-expanded={quoteExpanded}"),
  desktopComposeBodyEditorSource.indexOf(
    "</button>",
    desktopComposeBodyEditorSource.indexOf("aria-expanded={quoteExpanded}"),
  ),
);
assert.doesNotMatch(
  quoteToggleSource,
  /setComposeBody|innerHTML|querySelector/,
  "quote disclosure must never rewrite composeBody or the editable quote node",
);

const phase3B1Failures: string[] = [];
const recordPhase3B1Expectation = (name: string, expectation: () => void) => {
  try {
    expectation();
  } catch (error) {
    phase3B1Failures.push(
      `${name}: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
};

const sendRequestStart = sendSource.indexOf(
  "const sendResponse = await sendGmailMessage({",
);
const sendRequestEnd = sendSource.indexOf("\n      });", sendRequestStart);
assert.ok(
  sendRequestStart >= 0 && sendRequestEnd > sendRequestStart,
  "compose Gmail send request source markers must be present and ordered",
);
const sendRequestSource = sendSource.slice(sendRequestStart, sendRequestEnd);

recordPhase3B1Expectation(
  "Reply and Reply All send only authoritative Gmail source context",
  () => {
    assert.match(sendRequestSource, /replyContext\s*:/);

    const replyContextIndex = sendSource.indexOf(
      "buildGmailReplyContext",
      sendRequestStart - 1600,
    );
    assert.ok(replyContextIndex >= 0 && replyContextIndex <= sendRequestEnd);
    const replyContextGuardSource = sendSource.slice(
      Math.max(0, replyContextIndex - 1600),
      sendRequestEnd,
    );
    assert.match(replyContextGuardSource, /buildGmailReplyContext\s*\(/);
    assert.match(replyContextGuardSource, /sendProvider\s*,/);
    assert.match(replyContextGuardSource, /composeMode\s*,/);
    assert.match(replyContextGuardSource, /mailboxId\s*:\s*managedMailbox\.id/);
    assert.match(
      replyContextGuardSource,
      /sourceMessage\s*:\s*composeSourceMessage/,
    );
    assert.doesNotMatch(
      sendRequestSource,
      /\b(?:providerThreadId|threadId|rfcMessageId|References|In-Reply-To)\s*:/,
      "the compose request must not submit client-controlled thread or RFC authority",
    );
  },
);

recordPhase3B1Expectation(
  "local Gmail Sent identity comes from the provider response",
  () => {
    const sentIdentityStart = sendSource.indexOf("const sentId =", sendRequestEnd);
    const sentIdentityEnd = sendSource.indexOf(
      "setMailboxStore((currentStore)",
      sentIdentityStart,
    );
    assert.ok(
      sentIdentityStart >= 0 && sentIdentityEnd > sentIdentityStart,
      "local Sent identity source markers must be present and ordered",
    );
    const sentIdentitySource = sendSource.slice(sentIdentityStart, sentIdentityEnd);

    assert.match(sentIdentitySource, /sendProvider\s*===\s*"google"/);
    assert.match(sentIdentitySource, /applyLiveThreadIdentity\s*\(/);
    assert.match(
      sentIdentitySource,
      /providerMessageId\s*:\s*sendResponse\.providerMessageId/,
    );
    assert.match(
      sentIdentitySource,
      /providerThreadId\s*:\s*sendResponse\.providerThreadId/,
    );
    assert.match(sentIdentitySource, /mailboxId\s*:\s*activeComposeMailbox\.id/);
    assert.match(sentIdentitySource, /provider\s*:\s*"google"/);
    assert.match(sentIdentitySource, /sendProvider\s*===\s*"custom_imap"/);
    assert.match(
      sentIdentitySource,
      /composeSourceMessage(?:\?\.|\.)threadId/,
      "custom SMTP replies must preserve their existing local source-thread fallback",
    );
  },
);

const gmailReplySourceIdentity = applyLiveThreadIdentity(
  {
    id: "source-local-id",
    providerMessageId: "source-provider-message",
    providerThreadId: "thread-123",
  },
  {
    mailboxId: "gmail-1",
    provider: "google",
    folder: "INBOX",
    uidValidity: "gmail-api",
  },
);
const gmailLocalSentIdentity = applyLiveThreadIdentity(
  {
    id: "gmail-1-sent-local-id",
    providerMessageId: "sent-provider-message",
    providerThreadId: "thread-123",
  },
  {
    mailboxId: "gmail-1",
    provider: "google",
    folder: "SENT",
    uidValidity: "gmail-api",
  },
);
assert.equal(gmailLocalSentIdentity.threadIdentityAuthority, "gmail");
assert.equal(
  gmailLocalSentIdentity.threadId,
  gmailReplySourceIdentity.threadId,
  "a local Sent record built from the returned provider thread must resolve to the source canonical Gmail conversation",
);
const persistedGmailLocalSentIdentity = JSON.parse(
  JSON.stringify(gmailLocalSentIdentity),
) as typeof gmailLocalSentIdentity;
assert.equal(persistedGmailLocalSentIdentity.id, "gmail-1-sent-local-id");
assert.equal(
  persistedGmailLocalSentIdentity.providerMessageId,
  "sent-provider-message",
);
assert.equal(persistedGmailLocalSentIdentity.providerThreadId, "thread-123");
assert.equal(persistedGmailLocalSentIdentity.threadIdentityAuthority, "gmail");
assert.deepEqual(
  persistedGmailLocalSentIdentity.threadIdentityContext,
  gmailLocalSentIdentity.threadIdentityContext,
  "whole-object Sent persistence must retain provider-authoritative identity",
);

const gmailMismatchedSentIdentity = applyLiveThreadIdentity(
  {
    id: "gmail-1-sent-mismatch-local-id",
    providerMessageId: "sent-provider-message-mismatch",
    providerThreadId: "provider-returned-different-thread",
  },
  {
    mailboxId: "gmail-1",
    provider: "google",
    folder: "SENT",
    uidValidity: "gmail-api",
  },
);
assert.equal(gmailMismatchedSentIdentity.threadIdentityAuthority, "gmail");
assert.notEqual(
  gmailMismatchedSentIdentity.threadId,
  gmailReplySourceIdentity.threadId,
  "an unconfirmed send must use Gmail's returned thread rather than falsely copying the source thread",
);

assert.deepEqual(
  phase3B1Failures,
  [],
  `Phase 3B1 Gmail compose expectations failed:\n${phase3B1Failures
    .map((failure) => `- ${failure}`)
    .join("\n")}`,
);

const serializedReplyHtml = `<div>Confirmed.</div><div data-compose-signature="true"><div data-compose-signature-text="true">Rutger Cuevion</div></div>${expectedConversationalReply}`;
const serializedReplyText = extractComposePlainText(serializedReplyHtml);
const countOccurrences = (value: string, needle: string) => value.split(needle).length - 1;

[
  ["Confirmed.", "user reply"],
  ["Rutger Cuevion", "signature"],
  ["On Fri, Aug 14, 2026 at 10:15 AM", "attribution"],
  ["Can you confirm the timing?", "normalized quote"],
].forEach(([needle, label]) => {
  assert.equal(
    countOccurrences(serializedReplyHtml, needle),
    1,
    `serialized bodyHtml must contain the ${label} exactly once`,
  );
  assert.equal(
    countOccurrences(serializedReplyText, needle),
    1,
    `serialized bodyText must contain the ${label} exactly once`,
  );
});
assert.equal(countOccurrences(serializedReplyHtml, 'data-compose-quote="true"'), 1);
assert.match(serializedReplyText, /Confirmed\.\n\s*Rutger Cuevion\n\s*On /);
assert.match(serializedReplyText, /Hi Rutger,\n\s*Can you confirm the timing\?/);

const mobileSendSource = workspaceShellSource.slice(
  workspaceShellSource.indexOf("const handleMobileComposeSend = async"),
  workspaceShellSource.indexOf("const composeCloseConfirmation ="),
);
assert.match(
  mobileSendSource,
  /sendMessage\(\{ bodyHtml: userHtml \+ composeBody \}\)/,
  "mobile typed paragraphs must remain prepended to the normalized stored quote",
);

assert.match(
  workspaceShellSource,
  /\[&_\[data-compose-quote='true'\]_\*\]:text-\[inherit\]/,
  "quoted history must remain Cuevion-colored in Light and Dark modes",
);

const replyModeContractFailures: string[] = [];
const recordReplyModeExpectation = (name: string, expectation: () => void) => {
  try {
    expectation();
  } catch (error) {
    replyModeContractFailures.push(
      `${name}: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
};

const replyModeHelpersStart = workspaceShellSource.indexOf(
  "type ReplyRecipientToken =",
);
const replyModeHelpersEnd = workspaceShellSource.indexOf(
  "function normalizeSenderLearningKey(",
  replyModeHelpersStart,
);
let replyModeHarness:
  | {
      deriveReplyRecipientPlan: (options: any) => any;
      createReplyModeSession: (options: any) => any;
      switchReplyRecipientMode: (options: any) => any;
      reconcileReplyModeSessionRecipientEdit: (session: any, edit: any) => any;
    }
  | null = null;

if (replyModeHelpersStart >= 0 && replyModeHelpersEnd > replyModeHelpersStart) {
  const compiledReplyModeHelpers = transform(
    workspaceShellSource.slice(replyModeHelpersStart, replyModeHelpersEnd),
    { transforms: ["typescript"] },
  ).code;
  replyModeHarness = new Function(
    `${compiledReplyModeHelpers}\nreturn { deriveReplyRecipientPlan, createReplyModeSession, switchReplyRecipientMode, reconcileReplyModeSessionRecipientEdit };`,
  )() as NonNullable<typeof replyModeHarness>;
}

const requireReplyModeHarness = () => {
  assert.ok(
    replyModeHarness,
    "reply recipient planning and provenance helpers must exist",
  );
  return replyModeHarness;
};
const derivePlan = (overrides: Record<string, unknown> = {}) =>
  requireReplyModeHarness().deriveReplyRecipientPlan({
    originalSender: "Alice <alice@example.com>",
    originalTo: "owner@example.com",
    originalCc: "",
    ownAddresses: ["owner@example.com"],
    ...overrides,
  });

recordReplyModeExpectation("one-to-one zero-delta switching", () => {
  const harness = requireReplyModeHarness();
  const opened = harness.createReplyModeSession({
    sourceMessageId: "source-one-to-one",
    plan: derivePlan(),
    initialMode: "reply",
  });
  assert.equal(opened.to, "Alice <alice@example.com>");
  assert.equal(opened.cc, "");
  const replyAll = harness.switchReplyRecipientMode({
    targetMode: "reply_all",
    ...opened,
  });
  assert.deepEqual(
    { mode: replyAll.mode, to: replyAll.to, cc: replyAll.cc, bcc: replyAll.bcc },
    {
      mode: "reply_all",
      to: opened.to,
      cc: opened.cc,
      bcc: opened.bcc,
    },
  );
  const reply = harness.switchReplyRecipientMode({
    targetMode: "reply",
    ...replyAll,
  });
  assert.deepEqual(
    { mode: reply.mode, to: reply.to, cc: reply.cc, bcc: reply.bcc },
    { mode: "reply", to: opened.to, cc: opened.cc, bcc: opened.bcc },
  );
});

recordReplyModeExpectation("single meaningful Reply All delta", () => {
  const plan = derivePlan({ originalCc: "Carol <carol@example.com>" });
  assert.deepEqual(
    plan.replyAllDelta,
    [{ key: "carol@example.com", renderedValue: "Carol <carol@example.com>" }],
    "one additional external recipient must be sufficient for Reply All",
  );
  const source = workspaceShellSource.slice(
    workspaceShellSource.indexOf("const openComposeFromMessage ="),
    workspaceShellSource.indexOf(
      "useEffect(() =>",
      workspaceShellSource.indexOf("const openComposeFromMessage ="),
    ),
  );
  assert.doesNotMatch(source, /originalRecipientsExcludingOwn\.length > 1/);
});

recordReplyModeExpectation("manual Cc and pre-existing recipients survive", () => {
  const harness = requireReplyModeHarness();
  const opened = harness.createReplyModeSession({
    sourceMessageId: "source-manual",
    plan: derivePlan({ originalCc: "Carol <carol@example.com>" }),
    initialMode: "reply",
  });
  const withManualBefore = { ...opened, cc: "Bob <bob@example.com>" };
  const replyAll = harness.switchReplyRecipientMode({
    targetMode: "reply_all",
    ...withManualBefore,
  });
  assert.equal(replyAll.cc, "Bob <bob@example.com>, Carol <carol@example.com>");
  const editedCc = `${replyAll.cc}, Dana <dana@example.com>`;
  const editedSession = harness.reconcileReplyModeSessionRecipientEdit(
    replyAll.session,
    { field: "cc", previousValue: replyAll.cc, nextValue: editedCc },
  );
  const reply = harness.switchReplyRecipientMode({
    targetMode: "reply",
    ...replyAll,
    cc: editedCc,
    session: editedSession,
  });
  assert.equal(reply.cc, "Bob <bob@example.com>, Dana <dana@example.com>");
});

recordReplyModeExpectation("suppressed automatic recipient stays suppressed", () => {
  const harness = requireReplyModeHarness();
  const opened = harness.createReplyModeSession({
    sourceMessageId: "source-suppressed",
    plan: derivePlan({ originalCc: "Carol <carol@example.com>" }),
    initialMode: "reply",
  });
  const replyAll = harness.switchReplyRecipientMode({
    targetMode: "reply_all",
    ...opened,
  });
  const suppressedSession = harness.reconcileReplyModeSessionRecipientEdit(
    replyAll.session,
    { field: "cc", previousValue: replyAll.cc, nextValue: "" },
  );
  const reply = harness.switchReplyRecipientMode({
    targetMode: "reply",
    ...replyAll,
    cc: "",
    session: suppressedSession,
  });
  const replyAllAgain = harness.switchReplyRecipientMode({
    targetMode: "reply_all",
    ...reply,
  });
  assert.equal(replyAllAgain.cc, "");
  assert.equal(replyAllAgain.session.suppressed.has("carol@example.com"), true);
});

recordReplyModeExpectation("manual move releases automatic ownership", () => {
  const harness = requireReplyModeHarness();
  const opened = harness.createReplyModeSession({
    sourceMessageId: "source-move",
    plan: derivePlan({ originalCc: "Carol <carol@example.com>" }),
    initialMode: "reply_all",
  });
  const releasedSession = harness.reconcileReplyModeSessionRecipientEdit(
    opened.session,
    { field: "cc", previousValue: opened.cc, nextValue: "" },
  );
  const manualTo = `${opened.to}, Carol <carol@example.com>`;
  const reply = harness.switchReplyRecipientMode({
    targetMode: "reply",
    ...opened,
    to: manualTo,
    cc: "",
    session: releasedSession,
  });
  assert.equal(reply.to, manualTo);
  assert.equal(reply.cc, "");
});

recordReplyModeExpectation("initial Reply All provenance", () => {
  const harness = requireReplyModeHarness();
  const opened = harness.createReplyModeSession({
    sourceMessageId: "source-initial-all",
    plan: derivePlan({ originalCc: "Carol <carol@example.com>" }),
    initialMode: "reply_all",
  });
  assert.equal(opened.cc, "Carol <carol@example.com>");
  assert.equal(opened.session.managed.has("carol@example.com"), true);
  const reply = harness.switchReplyRecipientMode({
    targetMode: "reply",
    ...opened,
  });
  assert.equal(reply.cc, "");
  const replyAll = harness.switchReplyRecipientMode({
    targetMode: "reply_all",
    ...reply,
  });
  assert.equal(replyAll.cc, "Carol <carol@example.com>");
});

recordReplyModeExpectation("owned-address exclusion and normalized dedupe", () => {
  const harness = requireReplyModeHarness();
  const replyCases = [
    {
      originalSender: "Rutger Bäumer <rutger@hysteriarecs.com>",
      originalTo: "carltricksmusic@gmail.com",
      ownAddresses: ["carltricksmusic@gmail.com"],
      expected: ["Rutger Bäumer <rutger@hysteriarecs.com>"],
      label: "login-only sender remains external",
    },
    {
      originalSender: "external@example.com",
      originalTo: "carltricksmusic@gmail.com",
      ownAddresses: ["carltricksmusic@gmail.com"],
      expected: ["external@example.com"],
      label: "external sender remains Reply target",
    },
    {
      originalSender: '"Doe, Alice" <alice@example.com>',
      originalTo: "carltricksmusic@gmail.com",
      ownAddresses: ["carltricksmusic@gmail.com"],
      expected: ['"Doe, Alice" <alice@example.com>'],
      label: "formatted sender remains intact",
    },
    {
      originalSender: "carltricksmusic@gmail.com",
      originalTo: "carltricksmusic@gmail.com",
      ownAddresses: ["carltricksmusic@gmail.com"],
      expected: [],
      label: "connected self-to-self remains empty",
    },
    {
      originalSender: "mailbox-a@example.com",
      originalTo: "mailbox-b@example.com",
      ownAddresses: ["mailbox-a@example.com", "mailbox-b@example.com"],
      expected: [],
      label: "connected mailbox A-to-B remains owned",
    },
  ];
  replyCases.forEach(({ expected, label, ...options }) => {
    const plan = harness.deriveReplyRecipientPlan({
      ...options,
      originalCc: "",
    });
    assert.deepEqual(
      plan.replyTo.map((token: { renderedValue: string }) => token.renderedValue),
      expected,
      label,
    );
  });

  const ownedPlan = derivePlan({
    originalTo: "owner@example.com, second-owner@example.com",
    originalCc: "login-only@example.com",
    ownAddresses: ["owner@example.com", "second-owner@example.com"],
  });
  assert.deepEqual(ownedPlan.replyAllDelta, [
    { key: "login-only@example.com", renderedValue: "login-only@example.com" },
  ]);

  const duplicatePlan = derivePlan({
    originalTo: "owner@example.com, alice@example.com",
    originalCc:
      "Alice <ALICE@example.com>, Carol <carol@example.com>, CAROL@example.com",
  });
  assert.deepEqual(duplicatePlan.replyAllDelta, [
    { key: "carol@example.com", renderedValue: "Carol <carol@example.com>" },
  ]);
});

recordReplyModeExpectation("Reply after own Sent row targets external participants", () => {
  const sentRowPlan = derivePlan({
    originalSender: "owner@example.com",
    originalTo: "Partner <partner@example.com>",
    originalCc: "Team <team@example.com>, owner@example.com",
    ownAddresses: ["owner@example.com"],
  });

  assert.deepEqual(sentRowPlan.replyTo, [
    {
      key: "partner@example.com",
      renderedValue: "Partner <partner@example.com>",
    },
  ]);
  assert.deepEqual(sentRowPlan.replyAllDelta, [
    {
      key: "team@example.com",
      renderedValue: "Team <team@example.com>",
    },
  ]);
});

recordReplyModeExpectation("Bcc and draft state remain untouched", () => {
  const harness = requireReplyModeHarness();
  const attachment = { id: "attachment-1" };
  const sourceMessage = { id: "source-draft" };
  const draft = {
    ...harness.createReplyModeSession({
      sourceMessageId: sourceMessage.id,
      plan: derivePlan({ originalCc: "Carol <carol@example.com>" }),
      initialMode: "reply",
    }),
    bcc: "Private <private@example.com>",
    composeSubject: "Re: Contract",
    composeBody: '<div>Draft</div><div data-compose-signature="true">Sig</div><div data-compose-quote="true">Quote</div>',
    editorInnerHtml: "editor-byte-state",
    composeAttachments: [attachment],
    composeSignatureSelection: "mailbox-1",
    composeQuoteExpanded: true,
    composeSourceMessage: sourceMessage,
    composeMailboxId: "mailbox-1",
  };
  const switched = {
    ...draft,
    ...harness.switchReplyRecipientMode({
      targetMode: "reply_all",
      to: draft.to,
      cc: draft.cc,
      bcc: draft.bcc,
      session: draft.session,
    }),
  };
  for (const key of [
    "bcc",
    "composeSubject",
    "composeBody",
    "editorInnerHtml",
    "composeAttachments",
    "composeSignatureSelection",
    "composeQuoteExpanded",
    "composeSourceMessage",
    "composeMailboxId",
  ] as const) {
    assert.equal(switched[key], draft[key], `${key} must retain identity/value`);
  }
  assert.equal(switched.composeAttachments[0], attachment);
});

recordReplyModeExpectation("local-only switch handler", () => {
  const handlerSource = workspaceShellSource.slice(
    workspaceShellSource.indexOf("const handleReplyModeSwitch ="),
    workspaceShellSource.indexOf(
      "useEffect(() =>",
      workspaceShellSource.indexOf("const handleReplyModeSwitch ="),
    ),
  );
  assert.match(handlerSource, /switchReplyRecipientMode\(/);
  assert.doesNotMatch(
    handlerSource,
    /openComposeFromMessage|resetComposeState|sendMessage|sendGmailMessage|fetch|mutate|buildComposeBody|buildComposeQuoteHtml/,
  );
});

assert.deepEqual(
  replyModeContractFailures,
  [],
  `Reply mode switch expectations failed:\n${replyModeContractFailures
    .map((failure) => `- ${failure}`)
    .join("\n")}`,
);
