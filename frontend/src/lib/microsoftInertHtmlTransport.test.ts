import assert from "node:assert/strict";
import { isTrustedMicrosoftInertHtmlTransport } from "./microsoftInertHtmlTransport";

const valid = [
  '<p>Hello <b>world</b></p>',
  '<table><tr><td style="color: red">News</td></tr></table>',
  '<img alt="Cover" data-microsoft-remote-src="https://cdn.example.com/cover.jpg">',
  '<img alt="Older image" data-microsoft-legacy-https-src="https://cdn.publisher.com/image.png">',
  '<img alt="Artwork" data-microsoft-cid="image.123@example.com">',
];
for (const source of valid) {
  assert.equal(isTrustedMicrosoftInertHtmlTransport(source), true, source);
}
for (const source of [
  "",
  "<script>alert(1)</script>",
  '<img src="https://tracker.example/pixel">',
  '<img srcset="https://tracker.example/1x 1x">',
  '<a href="javascript:alert(1)">Unsafe</a>',
  '<p onload="alert(1)">Unsafe</p>',
  '<meta http-equiv="refresh" content="0">',
  "<svg><image href='https://tracker.example/a'></svg>",
  '<div style="background:url(https://tracker.example/a)">Test</div>',
  '<span>bad background-image: url(https://tracker.example/a)</span>',
  '<form><input name="secret"></form>',
  '<iframe src="https://tracker.example/frame"></iframe>',
  '<a href="https://example.com">hello</a><link rel="preload">',
]) {
  assert.equal(isTrustedMicrosoftInertHtmlTransport(source), false, source);
}
assert.equal(isTrustedMicrosoftInertHtmlTransport(null), false);
assert.equal(isTrustedMicrosoftInertHtmlTransport("a".repeat(294_913)), false);

console.log("Microsoft exact HTML transport: size limited, inert resource schema and unsafe tags rejected PASS");
