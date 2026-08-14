import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { transform } from "sucrase";

const workspaceShellSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
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
  return { buildComposeQuoteHtml, buildComposeParagraphsHtml };`,
) as () => {
  buildComposeQuoteHtml: (mode: string, sourceMessage: QuoteMessage) => string;
  buildComposeParagraphsHtml: (value: string) => string;
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
assert.match(openComposeSource, /const ownAddressSet = new Set<string>/);
assert.match(openComposeSource, /const senderIsOwn = ownAddressSet\.has/);
assert.match(openComposeSource, /if \(mode === "reply_all"\)/);
assert.match(openComposeSource, /ownAddressSet\.has\(normalizedRecipient\)/);
assert.match(openComposeSource, /replyToNormalized\.has\(normalizedRecipient\)/);
assert.match(openComposeSource, /replyAllCcRecipients\.some/);
assert.match(
  openComposeSource,
  /setComposeCc\(mode === "reply_all" \? replyAllCcRecipients\.join\(", "\) : ""\)/,
  "Reply All must retain own-address exclusion and recipient de-duplication",
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
assert.match(sendSource, /const activeBodyHtml = options\?\.bodyHtml \?\? composeBody/);
assert.match(sendSource, /const bodyPreview = extractComposePlainText\(activeBodyHtml\)/);
assert.match(sendSource, /bodyHtml: activeBodyHtml/);
assert.match(sendSource, /bodyText: bodyPreview \|\| " "/);

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
