import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const { MicrosoftLinkifiedText, safeMicrosoftLink } = require(
  "./MicrosoftLinkifiedText.tsx",
) as typeof import("./MicrosoftLinkifiedText");

assert.equal(safeMicrosoftLink("javascript:alert(1)"), null);
assert.equal(safeMicrosoftLink("https://person:password@example.com"), null);
assert.equal(safeMicrosoftLink("https://example.com/track"), "https://example.com/track");

const html = renderToStaticMarkup(createElement(MicrosoftLinkifiedText, {
  text: "Listen (https://soundcloud.com/artist/track?s=abc). Or https://disco.ac/set!",
}));
assert.match(html, /href="https:\/\/soundcloud\.com\/artist\/track\?s=abc"/);
assert.match(html, /href="https:\/\/disco\.ac\/set"/);
assert.match(html, /rel="noopener noreferrer"/);
assert.match(html, /target="_blank"/);
assert.match(html, /no-referrer/);
console.log("Microsoft linkified read-only text tests passed");
