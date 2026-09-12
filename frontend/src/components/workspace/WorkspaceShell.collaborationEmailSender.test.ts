import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { transform } from "sucrase";
import { getReturnedReplySenderAddress } from "../../lib/returnedReplyEvidence";

const source = fs.readFileSync(path.join(__dirname, "WorkspaceShell.tsx"), "utf8");
const start = source.indexOf("function formatCollaborationEmailSender(");
assert.ok(start > 0);
const code = transform(source.slice(start, source.indexOf("function formatCollaborationSourceTimestamp(", start)), { transforms: ["typescript"] }).code;
const format = new Function("getReturnedReplySenderAddress", `${code}; return formatCollaborationEmailSender;`)(getReturnedReplySenderAddress);
const email = "rutgerbaumer@gmail.com";
const name = "Rutger Baumer";
const cases = [
  [name, email, `${name} <${email}>`],
  [email, email, email],
  ["", email, email],
  [name, "", name],
  [name, name, name],
  ["", name, name],
  [name, `${name} <${email}>`, `${name} <${email}>`],
  [`${name} <${email}>`, `${name} <${email}>`, `${name} <${email}>`],
  [`${name} <${name} <${email}>>>`, `${name} <${name} <${email}>>>`, `${name} <${email}>`],
  ["", "", ""],
];
for (const [sender, from, expected] of cases) {
  const message = { sender, from, subject: "Source" };
  const before = JSON.stringify(message);
  assert.equal(format(message), expected);
  assert.equal(JSON.stringify(message), before, "presentation must not mutate source identity");
}
assert.ok(source.includes("{formatCollaborationEmailSender(threadMessage)}</dd>"));
console.log(`PASS ${cases.length} sender presentation cases, immutable source and reader integration`);
