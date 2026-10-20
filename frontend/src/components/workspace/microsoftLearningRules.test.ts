import assert from "node:assert/strict";
import {
  microsoftLearningRulesStorageKey, parseMicrosoftLearningRules,
  saveMicrosoftLearningRule, isMicrosoftPriorityMessage,
} from "./microsoftLearningRules";
import type { LiveInboxMessageSnapshot } from "../../lib/inboxConnectionApi";

assert.equal(microsoftLearningRulesStorageKey("scope:a:b:box"), "scope:a:b:box:learning-v1");
assert.equal(microsoftLearningRulesStorageKey(null), null);
const saved = saveMicrosoftLearningRule({}, "Artist <Artist@Example.com>", { priority: "important", label: "Demo" });
assert.deepEqual(saved, { "artist@example.com": { priority: "important", label: "Demo" } });
assert.equal(saveMicrosoftLearningRule({}, "not-an-email", { priority: "important", label: "Demo" }), null);
assert.deepEqual({ ...parseMicrosoftLearningRules(JSON.stringify(saved)) }, saved);
assert.deepEqual(parseMicrosoftLearningRules('{"__proto__":{"priority":"important","label":"Demo"}}'), {});
assert.deepEqual(parseMicrosoftLearningRules('{"x@example.com":{"priority":"important","label":"Spam"}}'), {});
assert.deepEqual(parseMicrosoftLearningRules("{"), {});
const row = { from: "Artist <artist@example.com>", signal: "Normal" } as LiveInboxMessageSnapshot;
assert.equal(isMicrosoftPriorityMessage(row, saved!), true);
assert.equal(isMicrosoftPriorityMessage(row, {}), false);
assert.equal(isMicrosoftPriorityMessage({ ...row, signal: "Priority" }, {}), true);
assert.equal(isMicrosoftPriorityMessage({ ...row, signal: "Priority" }, {
  "artist@example.com": { priority: "show_less", label: "Promo" },
}), false);
console.log("Microsoft per-mailbox Learning rules and priority selection PASS");
