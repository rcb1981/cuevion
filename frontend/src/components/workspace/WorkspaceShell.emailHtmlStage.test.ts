import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { transform } from "sucrase";

const workspaceShellSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);
const emailHtmlStageSource = workspaceShellSource.slice(
  workspaceShellSource.indexOf("function EmailHtmlStage("),
  workspaceShellSource.indexOf("const allowedImportedEmailInlineStyleProperties"),
);

assert.ok(emailHtmlStageSource.length > 0, "EmailHtmlStage source must be present");
assert.match(
  emailHtmlStageSource,
  /height:\s*"clamp\(320px, 50dvh, 600px\)"/,
  "rich external HTML must use the bounded responsive stage height",
);
assert.match(
  emailHtmlStageSource,
  /maxHeight:\s*"calc\(100dvh - 96px\)"/,
  "very small viewports must override the normal 320px minimum",
);
assert.doesNotMatch(
  emailHtmlStageSource,
  /fallbackHeight|measureContentHeight|updateHeight|scheduleMeasure|scrollHeight|iframeDoc\.images|handleImageEvent|\b2200\b/,
  "opaque rich HTML must not retain content-height measurement or its 2200px fallback authority",
);
assert.match(
  workspaceShellSource,
  /overflow-y:\s*auto !important/,
  "long rich HTML must retain native vertical scrolling inside the iframe document",
);

const sandboxMatch = emailHtmlStageSource.match(/sandbox="([^"]+)"/);
assert.ok(sandboxMatch, "EmailHtmlStage must declare an iframe sandbox");
const sandboxTokens = sandboxMatch[1].split(/\s+/).sort();
assert.deepEqual(sandboxTokens, [
  "allow-popups",
  "allow-popups-to-escape-sandbox",
]);
assert.ok(!sandboxTokens.includes("allow-same-origin"));
assert.ok(!sandboxTokens.includes("allow-scripts"));

const resolveStageHeight = (viewportHeight: number) =>
  Math.min(Math.min(600, Math.max(320, viewportHeight * 0.5)), viewportHeight - 96);

[
  { viewport: "1920x1080", viewportHeight: 1080, expected: 540 },
  { viewport: "1440x900", viewportHeight: 900, expected: 450 },
  { viewport: "1280x720", viewportHeight: 720, expected: 360 },
  { viewport: "1024x700", viewportHeight: 700, expected: 350 },
  { viewport: "480x360", viewportHeight: 360, expected: 264 },
].forEach(({ viewport, viewportHeight, expected }) => {
  assert.equal(
    resolveStageHeight(viewportHeight),
    expected,
    `${viewport} must resolve to a ${expected}px email stage`,
  );
});

const resourcePolicyStart = workspaceShellSource.indexOf(
  "type ImportedEmailResourceUrlType",
);
const resourcePolicyEnd = workspaceShellSource.indexOf(
  "function sanitizeMessageBodyHtml(",
);

assert.notEqual(
  resourcePolicyStart,
  -1,
  "imported email HTML must define a shared outbound-resource URL policy",
);
assert.ok(
  resourcePolicyEnd > resourcePolicyStart,
  "the imported email resource policy must be defined before the HTML sanitizer",
);

const importedEmailResourcePolicySource = workspaceShellSource.slice(
  resourcePolicyStart,
  resourcePolicyEnd,
);
const compiledImportedEmailResourcePolicy = transform(
  importedEmailResourcePolicySource,
  { transforms: ["typescript"] },
).code;
const loadImportedEmailResourcePolicy = new Function(
  `${compiledImportedEmailResourcePolicy}
return {
  resolveImportedEmailResourceUrlType,
  resolveEmailImageSourceType,
  normalizeImportedEmailCssResourceSyntax,
  sanitizeEmailStyleContent,
  sanitizeImportedEmailCssResourceAttributeValue,
  shouldPreserveImportedEmailInlineStyle,
  shouldRemoveImportedEmailResourceAttribute,
  isImportedEmailClickOnlyLinkTagName,
};`,
) as () => {
  resolveImportedEmailResourceUrlType: (value: string | null) => string;
  resolveEmailImageSourceType: (value: string | null) => string;
  normalizeImportedEmailCssResourceSyntax: (value: string) => string;
  sanitizeEmailStyleContent: (value: string) => string;
  sanitizeImportedEmailCssResourceAttributeValue: (
    attributeName: string,
    attributeValue: string,
  ) => string;
  shouldPreserveImportedEmailInlineStyle: (
    propertyName: string,
    propertyValue: string,
    isSvgElement?: boolean,
  ) => boolean;
  shouldRemoveImportedEmailResourceAttribute: (
    tagName: string,
    attributeName: string,
    attributeValue: string,
  ) => boolean;
  isImportedEmailClickOnlyLinkTagName: (tagName: string) => boolean;
};
const importedEmailResourcePolicy = loadImportedEmailResourcePolicy();

assert.equal(
  importedEmailResourcePolicy.resolveImportedEmailResourceUrlType(
    "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==",
  ),
  "safe-embedded",
);
assert.equal(
  importedEmailResourcePolicy.resolveImportedEmailResourceUrlType("#local-svg-resource"),
  "safe-embedded",
);
[
  "https://tracker.example/pixel",
  "http://tracker.example/pixel",
  "//tracker.example/pixel",
  "/relative-to-app-origin.png",
  "relative-to-document.png",
].forEach((resourceUrl) => {
  assert.equal(
    importedEmailResourcePolicy.resolveImportedEmailResourceUrlType(resourceUrl),
    "remote",
    `${resourceUrl} must be treated as automatically fetchable`,
  );
});

[
  "https://tracker.example/image.png",
  "//tracker.example/image.png",
  "/relative-to-app-origin.png",
  "relative-to-document.png",
  "#document-fragment",
].forEach((imageSource) => {
  assert.equal(
    importedEmailResourcePolicy.resolveEmailImageSourceType(imageSource),
    "remote",
    `${imageSource} must participate in ordinary-image blocking/count/reveal`,
  );
});
assert.equal(
  importedEmailResourcePolicy.resolveEmailImageSourceType(
    "data:image/gif;base64,R0lGODlhAQABAAAAACw=",
  ),
  "inline",
);
assert.equal(importedEmailResourcePolicy.resolveEmailImageSourceType("cid:logo"), "cid");
assert.equal(importedEmailResourcePolicy.resolveEmailImageSourceType("javascript:alert(1)"), "invalid");
[
  "javascript:alert(1)",
  "java\tscript:alert(1)",
  "vbscript:msgbox(1)",
  "data:text/html,<script>alert(1)</script>",
].forEach((resourceUrl) => {
  assert.equal(
    importedEmailResourcePolicy.resolveImportedEmailResourceUrlType(resourceUrl),
    "invalid",
    `${resourceUrl} must remain executable/invalid`,
  );
});

const sanitizedRemoteCss = importedEmailResourcePolicy.sanitizeEmailStyleContent(String.raw`
  @im\70 ort/**/"//tracker.example/imported.css";
  @font-face {
    font-family: Probe;
    src: url(https://tracker.example/font.woff2);
  }
  .card {
    color: #f7f7f7;
    background-color: #171717;
    background-image: url("https://tracker.example/background.png");
    list-style-image: u\72l/**/(/relative-list-marker.png);
    border: 1px solid #2f2f2f;
    padding: 20px;
  }
`);
const nestedImageSetCss = importedEmailResourcePolicy.sanitizeEmailStyleContent(`
  .nested-responsive {
    background-image: image-set(
      linear-gradient(rgb(0 0 0), rgb(1 1 1)) 1x,
      "https://tracker.example/two.png" 2x
    );
  }
  .webkit-responsive {
    background-image: -webkit-image-set(
      linear-gradient(rgb(2 2 2), rgb(3 3 3)) 1x,
      "https://tracker.example/webkit-two.png" 2x
    );
  }
`);
assert.equal(
  importedEmailResourcePolicy.normalizeImportedEmailCssResourceSyntax(
    String.raw`u\72l/**/(/relative-list-marker.png)`,
  ),
  "url(/relative-list-marker.png)",
);
assert.doesNotMatch(sanitizedRemoteCss, /tracker\.example/);
assert.doesNotMatch(sanitizedRemoteCss, /relative-list-marker/);
assert.doesNotMatch(nestedImageSetCss, /tracker\.example|image-set/i);
assert.match(sanitizedRemoteCss, /color:\s*#f7f7f7/);
assert.match(sanitizedRemoteCss, /background-color:\s*#171717/);
assert.match(sanitizedRemoteCss, /border:\s*1px solid #2f2f2f/);
assert.match(sanitizedRemoteCss, /padding:\s*20px/);

const safeEmbeddedCss = importedEmailResourcePolicy.sanitizeEmailStyleContent(
  `.logo::before { content: "/* safe text */"; background-image: url("data:image/gif;base64,R0lGODlhAQABAAAAACw="); }`,
);
assert.match(safeEmbeddedCss, /data:image\/gif;base64,R0lGODlhAQABAAAAACw=/);
assert.match(safeEmbeddedCss, /content:\s*"\/\* safe text \*\/"/);
const harmlessCssResourceText = importedEmailResourcePolicy.sanitizeEmailStyleContent(
  `.copy::before { content: "url(https://example.test/literal) image-set(offer) @import"; }`,
);
assert.match(
  harmlessCssResourceText,
  /content:\s*"url\(https:\/\/example\.test\/literal\) image-set\(offer\) @import"/,
  "resource-like text inside a CSS string must remain text",
);
const escapedQuoteCss = importedEmailResourcePolicy.sanitizeEmailStyleContent(
  String.raw`.escaped\"{color:#123;background:u\72l(https://tracker.example/escaped.png)}`,
);
assert.doesNotMatch(
  escapedQuoteCss,
  /tracker\.example|u\\72l/i,
  "an escaped quote outside a CSS string must not hide an escaped url() function",
);
["\n", "\r", "\f"].forEach((badStringNewline) => {
  const badStringCss = importedEmailResourcePolicy.sanitizeEmailStyleContent(
    `.bad-string { content: "${badStringNewline}; background-image: url(https://tracker.example/bad-string.png); color: red; }`,
  );

  assert.doesNotMatch(
    badStringCss,
    /tracker\.example|url\s*\(/i,
    "a CSS bad-string newline must resume resource scanning",
  );
});
assert.doesNotThrow(() =>
  importedEmailResourcePolicy.sanitizeEmailStyleContent(
    String.raw`.invalid-escape { filter: \FFFFFF(1); color: #123456; }`,
  ),
);
assert.equal(
  importedEmailResourcePolicy.sanitizeImportedEmailCssResourceAttributeValue(
    "filter",
    "url(https://tracker.example/filter.svg#blur)",
  ),
  "none",
);
assert.equal(
  importedEmailResourcePolicy.sanitizeImportedEmailCssResourceAttributeValue(
    "fill",
    "url(#local-gradient)",
  ),
  "url(#local-gradient)",
);
[
  ["fill", "#ffffff"],
  ["stroke", "#000000"],
  ["stroke-width", "2"],
  ["transform", "translate(4 8)"],
].forEach(([propertyName, propertyValue]) => {
  assert.equal(
    importedEmailResourcePolicy.shouldPreserveImportedEmailInlineStyle(
      propertyName,
      propertyValue,
      true,
    ),
    true,
    `${propertyName} must remain available for safe inline SVG fidelity`,
  );
});
assert.equal(
  importedEmailResourcePolicy.shouldPreserveImportedEmailInlineStyle(
    "transform",
    "translate(4 8)",
    false,
  ),
  false,
  "SVG-only styles must not broaden ordinary HTML inline-style policy",
);

const shouldRemoveResourceAttribute =
  importedEmailResourcePolicy.shouldRemoveImportedEmailResourceAttribute;
[
  ["IMG", "srcset", "data:image/gif;base64,R0lGODlhAQABAAAAACw=", true],
  ["SOURCE", "src", "data:image/gif;base64,R0lGODlhAQABAAAAACw=", true],
  ["AUDIO", "src", "https://tracker.example/audio.mp3", true],
  ["VIDEO", "src", "https://tracker.example/video.mp4", true],
  ["TRACK", "src", "https://tracker.example/subtitles.vtt", true],
  ["MGLYPH", "src", "https://tracker.example/math-glyph.png", true],
  ["VIDEO", "poster", "https://tracker.example/poster.png", true],
  ["VIDEO", "poster", "data:image/gif;base64,R0lGODlhAQABAAAAACw=", false],
  ["VIDEO", "poster", "#document-fragment", true],
  ["TABLE", "background", "https://tracker.example/background.png", true],
  ["TABLE", "background", "data:image/gif;base64,R0lGODlhAQABAAAAACw=", false],
  ["TABLE", "background", "#document-fragment", true],
  ["IMG", "dynsrc", "https://tracker.example/dynamic.png", true],
  ["IMG", "lowsrc", "https://tracker.example/low.png", true],
  ["A", "href", "https://example.com/click-only", false],
  ["AREA", "href", "https://example.com/image-map", false],
  ["USE", "href", "https://tracker.example/sprite.svg#icon", true],
  ["USE", "href", "#local-icon", false],
  ["USE", "xlink:href", "https://tracker.example/sprite.svg#icon", true],
  ["USE", "xlink:href", "#local-icon", false],
  ["A", "ping", "https://tracker.example/click-ping", true],
  ["A", "attributionsrc", "https://tracker.example/click-attribution", true],
  ["IMG", "attributionsrc", "https://tracker.example/image-attribution", true],
  ["svg", "xml:base", "https://tracker.example/svg-base/", true],
].forEach(([tagName, attributeName, attributeValue, expected]) => {
  assert.equal(
    shouldRemoveResourceAttribute(
      tagName as string,
      attributeName as string,
      attributeValue as string,
    ),
    expected,
    `${tagName as string}[${attributeName as string}] resource policy mismatch`,
  );
});

assert.equal(importedEmailResourcePolicy.isImportedEmailClickOnlyLinkTagName("A"), true);
assert.equal(importedEmailResourcePolicy.isImportedEmailClickOnlyLinkTagName("area"), true);
assert.equal(
  importedEmailResourcePolicy.isImportedEmailClickOnlyLinkTagName("svg:a"),
  false,
);

const importedHtmlSanitizerStart = workspaceShellSource.indexOf(
  "function sanitizeMessageBodyHtml(",
);
const importedHtmlSanitizerEnd = workspaceShellSource.indexOf(
  "function isComposeGeneratedHtml(",
);
const composeHtmlSanitizerStart = workspaceShellSource.indexOf(
  "function sanitizeComposeGeneratedHtml(",
);
const composeHtmlSanitizerEnd = workspaceShellSource.indexOf(
  "function resolveMessageBodyRenderMode(",
);

assert.ok(
  importedHtmlSanitizerStart >= 0 && importedHtmlSanitizerEnd > importedHtmlSanitizerStart,
  "imported HTML sanitizer source markers must be present and ordered",
);
assert.ok(
  composeHtmlSanitizerStart >= 0 && composeHtmlSanitizerEnd > composeHtmlSanitizerStart,
  "compose HTML sanitizer source markers must be present and ordered",
);

const importedHtmlSanitizerSource = workspaceShellSource.slice(
  importedHtmlSanitizerStart,
  importedHtmlSanitizerEnd,
);
const composeHtmlSanitizerSource = workspaceShellSource.slice(
  composeHtmlSanitizerStart,
  composeHtmlSanitizerEnd,
);

assert.match(
  importedHtmlSanitizerSource,
  /sanitizeImportedEmailResourceAttributes\(element\)/,
  "imported HTML elements must pass through the shared resource-attribute gate",
);
assert.match(
  composeHtmlSanitizerSource,
  /sanitizeImportedEmailResourceAttributes\(element\)/,
  "compose-marked HTML must not bypass the shared resource-attribute gate",
);
assert.match(
  importedHtmlSanitizerSource,
  /hardenImportedEmailClickOnlyLink\(element\)/,
  "HTML and SVG click-only links must be hardened after imported HTML sanitization",
);
assert.match(
  composeHtmlSanitizerSource,
  /hardenImportedEmailClickOnlyLink\(element\)/,
  "HTML and SVG click-only links must be hardened after compose HTML sanitization",
);
assert.match(
  importedHtmlSanitizerSource,
  /resolveEmailImageSourceType\(element\.getAttribute\("src"\)\)/,
  "ordinary imported images must use the shared request-capable URL classification",
);
assert.match(
  composeHtmlSanitizerSource,
  /resolveEmailImageSourceType\(element\.getAttribute\("src"\)\)/,
  "ordinary compose images must use the shared request-capable URL classification",
);

const unsafeSelectorStart = workspaceShellSource.indexOf(
  "const unsafeEmailHtmlSelectors = [",
);
const unsafeSelectorEnd = workspaceShellSource.indexOf(
  '].join(",");',
  unsafeSelectorStart,
);
assert.ok(
  unsafeSelectorStart >= 0 && unsafeSelectorEnd > unsafeSelectorStart,
  "unsafe email selector source markers must be present and ordered",
);
const unsafeSelectorSource = workspaceShellSource.slice(
  unsafeSelectorStart,
  unsafeSelectorEnd,
);
assert.match(
  unsafeSelectorSource,
  /"template"[\s\S]*"animate"[\s\S]*"set"/,
  "declarative shadow DOM and SVG animation elements must be removed before rendering",
);

const renderModeResolverStart = workspaceShellSource.indexOf(
  "function resolveMessageBodyRenderMode(",
);
const renderModeCacheStart = workspaceShellSource.indexOf(
  "type MessageBodyRenderMode =",
);
const renderModeResolverEnd = workspaceShellSource.indexOf(
  "function normalizeNativeMessageHtmlForPane(",
);
const renderModeCacheSource = workspaceShellSource.slice(
  renderModeCacheStart >= 0 ? renderModeCacheStart : renderModeResolverStart,
  renderModeResolverEnd,
);
const compiledRenderModeCacheSource = transform(renderModeCacheSource, {
  transforms: ["typescript"],
}).code;

type RenderModeCacheHarness = {
  resolveMessageBodyRenderMode: (
    message: {
      body: string[];
      bodyHtml?: string;
      attachments?: Array<{
        contentId?: string;
        inlineSrc?: string;
        mimeType?: string;
      }>;
    },
    options?: { allowRemoteImages?: boolean; themeMode?: "light" | "dark" },
  ) => {
    mode: string;
    html?: string;
    normalizedNativeHtml?: string;
    emailStyles?: string;
    remoteImageCount: number;
    cidImageCount: number;
    invalidImageCount: number;
  };
  getCounts: () => { imported: number; compose: number; nativeNormalization: number };
  getCacheSize: () => number;
  getNormalizedInputs: () => string[];
  expireOldestCachePolicy: () => void;
};

const loadRenderModeCacheHarness = () =>
  new Function(
    `let importedSanitizationCount = 0;
let composeSanitizationCount = 0;
let nativeNormalizationCount = 0;
const normalizedInputs = [];

function isComposeGeneratedHtml(value) {
  return /data-compose-(quote|signature)/i.test(value);
}

function normalizeNativeMessageHtmlForPane(html) {
  nativeNormalizationCount += 1;
  normalizedInputs.push(html);
  return \`<div data-native-normalized="true">\${html}</div>\`;
}

function buildStubSanitizedResult(bodyHtml, options, sourceType) {
  const sanitizedBodyHtml = bodyHtml.replace(/<script\\b[\\s\\S]*?<\\/script>/gi, "");
  const attachment = (options?.attachments ?? []).find(
    (candidate) => candidate.contentId === "logo",
  );
  const allowRemoteImages = options?.allowRemoteImages ?? false;
  const remoteImageCount = /https:\\/\\/tracker\\.example\\/image\\.png/.test(bodyHtml) ? 1 : 0;
  const cidMarkup = attachment
    ? \`<img data-cid-src="\${attachment.inlineSrc ?? ""}" data-cid-mime="\${attachment.mimeType ?? ""}">\`
    : "<div data-cid-missing></div>";
  const remoteMarkup = remoteImageCount
    ? allowRemoteImages
      ? '<img src="https://tracker.example/image.png">'
      : '<div data-email-image-placeholder="true"></div>'
    : "";
  const isNativeHtml = /^<p(?:\\s|>)/i.test(sanitizedBodyHtml);
  const sanitizedHtml = isNativeHtml
    ? sanitizedBodyHtml
    : \`<table data-source="\${sourceType}" data-theme="\${options?.themeMode ?? "unset"}"><tr><td>\${sanitizedBodyHtml}\${cidMarkup}\${remoteMarkup}</td></tr></table>\`;

  return {
    html: sanitizedHtml,
    emailStyles:
      sourceType === "imported" && !isNativeHtml ? ".newsletter { color: #123; }" : "",
    remoteImageCount,
    cidImageCount: /cid:logo/i.test(bodyHtml) && !attachment ? 1 : 0,
    invalidImageCount: 0,
  };
}

function sanitizeMessageBodyHtml(bodyHtml, options) {
  importedSanitizationCount += 1;
  return buildStubSanitizedResult(bodyHtml, options, "imported");
}

function sanitizeComposeGeneratedHtml(bodyHtml, options) {
  composeSanitizationCount += 1;
  return buildStubSanitizedResult(bodyHtml, options, "compose");
}

${compiledRenderModeCacheSource}

return {
  resolveMessageBodyRenderMode,
  getCounts: () => ({
    imported: importedSanitizationCount,
    compose: composeSanitizationCount,
    nativeNormalization: nativeNormalizationCount,
  }),
  getCacheSize: () =>
    typeof messageBodyRenderProjectionCache === "undefined"
      ? 0
      : messageBodyRenderProjectionCache.length,
  getNormalizedInputs: () => [...normalizedInputs],
  expireOldestCachePolicy: () => {
    if (messageBodyRenderProjectionCache[0]) {
      messageBodyRenderProjectionCache[0].policyVersion = "expired-policy";
    }
  },
};`,
  )() as RenderModeCacheHarness;

const identicalExternalHarness = loadRenderModeCacheHarness();
const identicalExternalMessage = {
  body: [],
  bodyHtml:
    '<style>.newsletter{color:#123}</style><table class="newsletter"><tr><td>Offer</td></tr></table>',
  attachments: [],
};
const identicalExternalOptions = {
  allowRemoteImages: false,
  themeMode: "light" as const,
};
const firstIdenticalExternalResult =
  identicalExternalHarness.resolveMessageBodyRenderMode(
    identicalExternalMessage,
    identicalExternalOptions,
  );
const secondIdenticalExternalResult =
  identicalExternalHarness.resolveMessageBodyRenderMode(
    identicalExternalMessage,
    identicalExternalOptions,
  );
const thirdIdenticalExternalResult =
  identicalExternalHarness.resolveMessageBodyRenderMode(
    {
      ...identicalExternalMessage,
      attachments: [...identicalExternalMessage.attachments],
    },
    identicalExternalOptions,
  );
assert.equal(
  identicalExternalHarness.getCounts().imported,
  1,
  "identical split/modal render inputs must sanitize imported HTML only once",
);
assert.strictEqual(firstIdenticalExternalResult, secondIdenticalExternalResult);
assert.strictEqual(secondIdenticalExternalResult, thirdIdenticalExternalResult);

const nativeProjectionHarness = loadRenderModeCacheHarness();
const nativeMessage = {
  body: [],
  bodyHtml: "<p>Native message</p>",
  attachments: [],
};
const firstNativeResult = nativeProjectionHarness.resolveMessageBodyRenderMode(
  nativeMessage,
  identicalExternalOptions,
);
const secondNativeResult = nativeProjectionHarness.resolveMessageBodyRenderMode(
  nativeMessage,
  identicalExternalOptions,
);
const recreatedNativeResult = nativeProjectionHarness.resolveMessageBodyRenderMode(
  { ...nativeMessage, attachments: [] },
  identicalExternalOptions,
);
assert.equal(firstNativeResult.mode, "native_html");
assert.equal(nativeProjectionHarness.getCounts().imported, 1);
assert.equal(
  nativeProjectionHarness.getCounts().nativeNormalization,
  1,
  "unchanged native HTML must normalize only once across rerenders and recreation",
);
assert.strictEqual(firstNativeResult, secondNativeResult);
assert.strictEqual(secondNativeResult, recreatedNativeResult);
assert.match(firstNativeResult.normalizedNativeHtml ?? "", /data-native-normalized/);

const sanitizerOrderHarness = loadRenderModeCacheHarness();
const sanitizerOrderResult = sanitizerOrderHarness.resolveMessageBodyRenderMode(
  {
    body: [],
    bodyHtml: '<p>Safe<script>alert("unsafe")</script></p>',
    attachments: [],
  },
  identicalExternalOptions,
);
assert.equal(sanitizerOrderResult.mode, "native_html");
assert.doesNotMatch(sanitizerOrderHarness.getNormalizedInputs()[0] ?? "", /script|unsafe/);
assert.doesNotMatch(sanitizerOrderResult.normalizedNativeHtml ?? "", /script|unsafe/);
assert.match(
  renderModeCacheSource,
  /normalizedNativeHtml:\s*normalizeNativeMessageHtmlForPane\(\s*sanitizedHtmlResult\.html,?\s*\)/,
  "native normalization must consume only sanitizer-derived HTML",
);
assert.doesNotMatch(
  renderModeCacheSource,
  /normalizeNativeMessageHtmlForPane\(\s*messageBodyHtml\s*\)/,
  "raw bodyHtml must never enter native normalization",
);

const bodyMutationHarness = loadRenderModeCacheHarness();
const originalBodyResult = bodyMutationHarness.resolveMessageBodyRenderMode(
  { body: [], bodyHtml: "<table><tr><td>Original</td></tr></table>", attachments: [] },
  identicalExternalOptions,
);
const changedBodyResult = bodyMutationHarness.resolveMessageBodyRenderMode(
  { body: [], bodyHtml: "<table><tr><td>Changed</td></tr></table>", attachments: [] },
  identicalExternalOptions,
);
assert.equal(bodyMutationHarness.getCounts().imported, 2);
assert.notDeepEqual(originalBodyResult, changedBodyResult);

const cidMutationHarness = loadRenderModeCacheHarness();
const cidBodyHtml = '<table><tr><td><img src="cid:logo"></td></tr></table>';
const firstCidResult = cidMutationHarness.resolveMessageBodyRenderMode(
  {
    body: [],
    bodyHtml: cidBodyHtml,
    attachments: [
      { contentId: "logo", inlineSrc: "data:image/png;base64,AAAA", mimeType: "image/png" },
    ],
  },
  identicalExternalOptions,
);
const changedCidSourceResult = cidMutationHarness.resolveMessageBodyRenderMode(
  {
    body: [],
    bodyHtml: cidBodyHtml,
    attachments: [
      { contentId: "logo", inlineSrc: "data:image/png;base64,BBBB", mimeType: "image/png" },
    ],
  },
  identicalExternalOptions,
);
const changedCidMimeResult = cidMutationHarness.resolveMessageBodyRenderMode(
  {
    body: [],
    bodyHtml: cidBodyHtml,
    attachments: [
      { contentId: "logo", inlineSrc: "data:image/png;base64,BBBB", mimeType: "image/webp" },
    ],
  },
  identicalExternalOptions,
);
const changedCidContentIdResult = cidMutationHarness.resolveMessageBodyRenderMode(
  {
    body: [],
    bodyHtml: cidBodyHtml,
    attachments: [
      { contentId: "other-logo", inlineSrc: "data:image/png;base64,BBBB", mimeType: "image/webp" },
    ],
  },
  identicalExternalOptions,
);
assert.equal(cidMutationHarness.getCounts().imported, 4);
assert.notDeepEqual(firstCidResult, changedCidSourceResult);
assert.notDeepEqual(changedCidSourceResult, changedCidMimeResult);
assert.notDeepEqual(changedCidMimeResult, changedCidContentIdResult);

const attachmentOrderHarness = loadRenderModeCacheHarness();
const orderedAttachments = [
  { contentId: "logo", inlineSrc: "data:image/png;base64,AAAA", mimeType: "image/png" },
  { contentId: "other", inlineSrc: "data:image/png;base64,BBBB", mimeType: "image/png" },
];
attachmentOrderHarness.resolveMessageBodyRenderMode(
  { body: [], bodyHtml: cidBodyHtml, attachments: orderedAttachments },
  identicalExternalOptions,
);
attachmentOrderHarness.resolveMessageBodyRenderMode(
  { body: [], bodyHtml: cidBodyHtml, attachments: [...orderedAttachments].reverse() },
  identicalExternalOptions,
);
assert.equal(
  attachmentOrderHarness.getCounts().imported,
  2,
  "attachment reordering must remain a cache miss",
);

const showImagesHarness = loadRenderModeCacheHarness();
const remoteBodyHtml = '<table><tr><td><img src="https://tracker.example/image.png"></td></tr></table>';
const remoteImagesOffResult = showImagesHarness.resolveMessageBodyRenderMode(
  { body: [], bodyHtml: remoteBodyHtml, attachments: [] },
  { allowRemoteImages: false, themeMode: "light" },
);
const remoteImagesOnResult = showImagesHarness.resolveMessageBodyRenderMode(
  { body: [], bodyHtml: remoteBodyHtml, attachments: [] },
  { allowRemoteImages: true, themeMode: "light" },
);
const remoteImagesOffAgainResult = showImagesHarness.resolveMessageBodyRenderMode(
  { body: [], bodyHtml: remoteBodyHtml, attachments: [] },
  { allowRemoteImages: false, themeMode: "light" },
);
assert.equal(showImagesHarness.getCounts().imported, 2);
assert.match(remoteImagesOffResult.html ?? "", /data-email-image-placeholder/);
assert.match(remoteImagesOnResult.html ?? "", /src="https:\/\/tracker\.example\/image\.png"/);
assert.strictEqual(remoteImagesOffResult, remoteImagesOffAgainResult);

const themeHarness = loadRenderModeCacheHarness();
const lightResult = themeHarness.resolveMessageBodyRenderMode(
  identicalExternalMessage,
  { allowRemoteImages: false, themeMode: "light" },
);
const darkResult = themeHarness.resolveMessageBodyRenderMode(
  identicalExternalMessage,
  { allowRemoteImages: false, themeMode: "dark" },
);
assert.equal(themeHarness.getCounts().imported, 2);
assert.notDeepEqual(lightResult, darkResult);

const sourceTypeHarness = loadRenderModeCacheHarness();
const importedResult = sourceTypeHarness.resolveMessageBodyRenderMode(
  { body: [], bodyHtml: "<table><tr><td>Imported</td></tr></table>", attachments: [] },
  identicalExternalOptions,
);
const composeResult = sourceTypeHarness.resolveMessageBodyRenderMode(
  {
    body: [],
    bodyHtml: '<div data-compose-signature="true">Compose</div>',
    attachments: [],
  },
  identicalExternalOptions,
);
assert.equal(sourceTypeHarness.getCounts().imported, 1);
assert.equal(sourceTypeHarness.getCounts().compose, 1);
assert.equal(importedResult.mode, "html");
assert.equal(composeResult.mode, "compose_html");

const policyVersionHarness = loadRenderModeCacheHarness();
policyVersionHarness.resolveMessageBodyRenderMode(
  identicalExternalMessage,
  identicalExternalOptions,
);
policyVersionHarness.expireOldestCachePolicy();
policyVersionHarness.resolveMessageBodyRenderMode(
  identicalExternalMessage,
  identicalExternalOptions,
);
assert.equal(
  policyVersionHarness.getCounts().imported,
  2,
  "a cache policy-version mismatch must force a fresh sanitization",
);

const evictionHarness = loadRenderModeCacheHarness();
const activeMessages = Array.from({ length: 65 }, (_, index) => ({
  body: [],
  bodyHtml: `<table><tr><td>Cache entry ${index}</td></tr></table>`,
  attachments: [],
}));
activeMessages.forEach((message) => {
  evictionHarness.resolveMessageBodyRenderMode(message, identicalExternalOptions);
});
assert.equal(evictionHarness.getCounts().imported, 65);
assert.equal(
  evictionHarness.getCacheSize(),
  64,
  "the structural render-projection cache must remain bounded",
);
activeMessages.forEach((message) => {
  evictionHarness.resolveMessageBodyRenderMode(message, identicalExternalOptions);
});
assert.equal(
  evictionHarness.getCounts().imported,
  65,
  "a live active set larger than eight must not cyclically self-evict",
);
evictionHarness.resolveMessageBodyRenderMode(
  { body: [], bodyHtml: "<table><tr><td>Cache entry 0</td></tr></table>", attachments: [] },
  identicalExternalOptions,
);
assert.equal(
  evictionHarness.getCounts().imported,
  66,
  "an evicted inactive structural entry must be recomputed for a recreated identity",
);

const renderThreadMessageStart = workspaceShellSource.indexOf(
  "const renderThreadMessage = (",
);
const renderThreadMessageEnd = workspaceShellSource.indexOf(
  "const renderThreadTimeline = (",
  renderThreadMessageStart,
);
const renderThreadMessageSource = workspaceShellSource.slice(
  renderThreadMessageStart,
  renderThreadMessageEnd,
);
const collapsedBranchIndex = renderThreadMessageSource.indexOf("if (collapsed)");
const bodyProjectionIndex = renderThreadMessageSource.indexOf(
  "resolveMessageBodyRenderMode(threadMessage",
);
assert.ok(
  collapsedBranchIndex >= 0 && bodyProjectionIndex > collapsedBranchIndex,
  "collapsed thread members must return before resolving a body projection",
);
assert.doesNotMatch(
  renderThreadMessageSource.slice(collapsedBranchIndex, bodyProjectionIndex),
  /resolveMessageBodyRenderMode|sanitizeMessageBodyHtml|sanitizeComposeGeneratedHtml/,
  "collapsed history must not sanitize or project hidden bodies",
);

assert.match(
  emailHtmlStageSource,
  /srcDoc=\{buildEmailStageDocument\(html, themeMode,/,
  "theme changes must continue to flow into iframe presentation",
);
