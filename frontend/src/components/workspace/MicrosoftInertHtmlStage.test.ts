import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import "sucrase/register/tsx.js";

const {
  MicrosoftInertHtmlStage,
  isSafeMicrosoftRemoteImageUrl,
  isSafeMicrosoftLegacyHttpsCandidate,
  sanitizeMicrosoftInertHtmlForStage,
} = require(
  resolve(process.cwd(), "src/components/workspace/MicrosoftInertHtmlStage.tsx"),
) as typeof import("./MicrosoftInertHtmlStage");

for (const value of [
  "http://example.org/tracker.png",
  "javascript:alert(1)",
  "data:image/svg+xml;base64,123",
  "https://user:secret@example.com/a",
  "https://example.com#fragment",
  "https://example.com/path whitespace",
  "https://example.com\\foo",
  "/internal/path",
  "//example.com/image",
  "",
]) {
  assert.equal(isSafeMicrosoftRemoteImageUrl(value), false, value);
}
assert.equal(
  isSafeMicrosoftRemoteImageUrl("https://cdn.example.com/picture.png?id=abc"),
  true,
);
assert.equal(
  isSafeMicrosoftRemoteImageUrl("https://cdn.example.com:443/picture.png"),
  true,
);

const dangerous = [
  '<img src="https://tracking.example/pixel">',
  '<script>alert(1)</script>',
  '<iframe src="https://tracking.example/frame"></iframe>',
  '<style>@import url(https://tracking.example/css)</style>',
  '<svg><script>alert(1)</script></svg>',
  '<p onclick="alert(1)">hello</p>',
  '<link rel="stylesheet" href="https://tracking.example/css">',
  '<p style="background:url(https://tracking.example/pixel)">bad</p>',
];
for (const payload of dangerous) {
  const res = sanitizeMicrosoftInertHtmlForStage(payload, false);
  assert.equal(res.html, null, payload);
  assert.equal(res.blockedReason, "invalid", payload);
}

const serverInert = [
  '<p style="color: red">Hello</p>',
  '<img alt="Cover" data-microsoft-remote-src="https://cdn.example.com/cover.png">',
].join("");
const serverResult = sanitizeMicrosoftInertHtmlForStage(serverInert, false);
// DOMParser is unavailable in Node SSR; do not dangerously insert HTML.
assert.equal(serverResult.html, null);
assert.equal(serverResult.blockedReason, "unavailable");

const fallback = renderToStaticMarkup(createElement(MicrosoftInertHtmlStage, {
  html: serverInert,
  fallbackText: 'Plain version <img src="evil">',
  messageKey: "microsoft-mailbox-unique-message-id",
}));
assert.match(fallback, /Plain version &lt;img src=&quot;evil&quot;&gt;/);
assert.doesNotMatch(fallback, /<iframe|srcDoc|https:\/\/cdn.example.com/);
assert.doesNotMatch(fallback, /<script/);

const source = readFileSync(
  resolve(process.cwd(), "src/components/workspace/MicrosoftInertHtmlStage.tsx"), "utf8",
);
assert.match(source, /sandbox="allow-popups allow-popups-to-escape-sandbox"/);
assert.doesNotMatch(source, /allow-scripts|allow-same-origin/);
assert.match(source, /referrerPolicy="no-referrer"/);
assert.match(source, /Content-Security-Policy/);
assert.match(source, /default-src 'none'/);
assert.match(source, /script-src 'none'/);
assert.doesNotMatch(source, /const imgPolicy = remoteImagesAllowed/);
assert.match(source, /img-src https: data:/);
assert.match(source, /connect-src 'none'/);
assert.match(source, /form-action 'none'/);
assert.match(source, /base-uri 'none'/);
assert.match(source, /<iframe/);
assert.match(source, /sanitizeMicrosoftInertHtmlForStage\(html, showRemoteImages, cidImages\)/);
assert.match(source, /waitingForCid \? null : sanitizeMicrosoftInertHtmlForStage/);
assert.match(source, /allowRemoteImages \? "img-src https: data:;" : "img-src data:;"/);
assert.match(source, /attachmentsBusy \|\| \(Boolean\(attachments\?\.length\) && !currentCidResult\)/);
assert.match(source, /Loading inline images/);

assert.match(source, /Show images/);
assert.match(source, /useState\(false\)/);
assert.match(source, /onClick=\{\(\) => setShowRemoteImages\(true\)\}/);
assert.match(source, /data-microsoft-remote-src/);
assert.match(source, /srcDoc=\{buildStageDocument\(rendered.html, showRemoteImages\)\}/);
assert.doesNotMatch(source, /dangerouslySetInnerHTML|fetch\(|localStorage|sessionStorage|window\.location\.assign/);

// The production reader remains plain text while the exact Graph contract
// still rejects bodyHtml. This prevents a half-implemented, unsafe rollout.
const view = readFileSync(
  resolve(process.cwd(), "src/components/workspace/MicrosoftMailboxReadOnlyView.tsx"), "utf8",
);
assert.match(view, /<MicrosoftInertHtmlStage/);
assert.match(view, /folder !== "Junk"/);
assert.match(view, /key=\{mailbox.id \+ ":" \+ folder \+ ":" \+ selectedMessage.providerMessageId\}/);
const api = readFileSync(resolve(process.cwd(), "src/lib/inboxConnectionApi.ts"), "utf8");
assert.match(api, /isTrustedMicrosoftInertHtmlTransport\(message.bodyHtml\)/);

console.log("Microsoft HTML iframe foundation: isolated sandbox, HTTPS opt-in images, CID owner gate, SSR fallback PASS");


// The CID bridge must never dereference raw cid: references, and can only add
// locally validated data URLs AFTER both server transport and DOM sanitization.
const stageSource = readFileSync(resolve(process.cwd(),
  "src/components/workspace/MicrosoftInertHtmlStage.tsx"), "utf8");
const mailboxSource = readFileSync(resolve(process.cwd(),
  "src/components/workspace/MicrosoftMailboxReadOnlyView.tsx"), "utf8");
assert.ok(stageSource.includes('loadMicrosoftInlineCidImages('));
// Regression for Paulshine-style images >512 KiB: the initial email HTML
// remains bounded, but inserting verified data:src can enlarge the final
// string. Cutting it to MAX_HTML corrupts the img element in Chromium.
assert.ok(stageSource.includes('const renderedHtml = parsed.body.innerHTML'));
assert.ok(stageSource.includes('restoreMicrosoftEmailTextContrast(parsed.body)'));
assert.ok(stageSource.includes('if (!hasText && !hasImages && remoteImageCount === 0)'));
assert.ok(stageSource.includes('Loading inline images'));

assert.ok(!stageSource.includes('parsed.body.innerHTML.slice(0, MAX_HTML)'));
assert.ok(stageSource.includes('MAX_MICROSOFT_CID_TOTAL_BYTES'));

assert.ok(stageSource.includes('el.setAttribute("src", dataUrl)'));
assert.ok(stageSource.includes('Object.prototype.hasOwnProperty.call(cidImages, value)'));
assert.ok(stageSource.includes('return () => { current = false; }'));
assert.ok(stageSource.includes('isMicrosoftInlineCidFolder(folder)'));
assert.ok(mailboxSource.includes('response.message.bodyHtml.includes("data-microsoft-cid=")'));
assert.ok(mailboxSource.includes("attachments={attachmentRows}"));
assert.ok(mailboxSource.includes('folder={folder === "Filtered" ? "Inbox" : folder}'));

// Invalid or absent image sources are rendered as inert placeholders; a
// disabled source never becomes a misleading Show images control.
assert.ok(stageSource.includes('unavailableImageCount += 1'));
assert.ok(stageSource.includes('tag === "img" && !el.hasAttribute("src")'));
assert.ok(stageSource.includes('placeholder.textContent = imageWasCid'));
assert.ok(stageSource.includes('el.replaceWith(placeholder)'));
assert.ok(stageSource.includes('image source unavailable'));
assert.ok(stageSource.includes('Show images is available only for supported HTTPS images.'));
assert.ok(stageSource.includes('imageHasSupportedSource = true'));
assert.ok(stageSource.includes('if (showRemoteImages) {'));

// Legacy HTTP images are never rendered over HTTP. Only an HTTPS candidate
// validated independently by the client can be used after explicit consent.
for (const url of [
  "https://cdn.publisher.com/cover.jpg",
  "https://cdn.publisher.com:443/pic.jpg?id=100",
]) assert.equal(isSafeMicrosoftLegacyHttpsCandidate(url), true);
for (const url of [
  "http://cdn.publisher.com/cover.jpg",
  "https://127.0.0.1/cover.png",
  "https://localhost/cover.png",
  "https://internal.local/cover.png",
  "https://files.internal/cover.png",
  "https://cdn.publisher.com:8443/cover.png",
  "https://user:pw@cdn.publisher.com/cover.png",
  "https://site..com/cover.png",
  "https://site.test/cover.png",
  "javascript:alert(1)",
]) assert.equal(isSafeMicrosoftLegacyHttpsCandidate(url), false, url);

assert.match(stageSource, /data-microsoft-legacy-https-src/);
assert.match(stageSource, /legacySecureAttemptCount \+= 1/);
assert.match(stageSource, /isSafeMicrosoftLegacyHttpsCandidate\(value\)/);
assert.match(stageSource, /if \(showRemoteImages\) \{/);
assert.match(stageSource, /el\.setAttribute\("referrerpolicy", "no-referrer"\)/);
assert.match(stageSource, /Show images/);
assert.match(stageSource, /only try HTTPS/);
assert.match(stageSource, /Secure HTTPS image loading was requested/);
assert.match(stageSource, /"img-src https: data:;"/);
assert.doesNotMatch(stageSource, /img-src http:/);
