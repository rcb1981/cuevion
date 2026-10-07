import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const html = readFileSync(resolve("public/tester-access.html"), "utf8");
const js = readFileSync(resolve("public/tester-access.js"), "utf8");
const css = readFileSync(resolve("public/tester-access.css"), "utf8");

assert.match(html, /Tester Access/);
assert.match(html, /Content-Security-Policy/);
assert.match(html, /script-src 'self'/);
assert.match(html, /id="admin-panel" class="hidden"/);
assert.match(html, /id="invite-form"/);
assert.match(html, /id="invite-link" type="text" readonly/);

assert.match(js, /request\("capability", \{\}\)/);
assert.match(js, /request\("issue"/);
assert.match(js, /request\("cancel"/);
assert.match(js, /credentials: "include"/);
assert.match(js, /https:\/\/app\.cuevion\.com\/#tester_invite=/);
assert.doesNotMatch(js, /localStorage|sessionStorage/);
assert.doesNotMatch(js, /console\./);
assert.doesNotMatch(js, /\?tester_invite=/);
assert.ok(js.indexOf('request("capability", {})') < js.indexOf('request("issue"'));
assert.ok(css.length > 1000);

console.log("tester access admin page tests passed");
