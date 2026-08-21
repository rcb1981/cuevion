import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync(
  "src/components/workspace/WorkspaceShell.tsx",
  "utf8",
);
const sendStart = source.indexOf("const sendMessage = async");
const sendEnd = source.indexOf("const closeMenus =", sendStart);
const sendSource = source.slice(sendStart, sendEnd);

assert.ok(sendStart >= 0 && sendEnd > sendStart, "sendMessage source must be found");
assert.match(
  sendSource,
  /if \(!sendResponse\.ok\)[\s\S]*?return;[\s\S]*?onSuccessfulConversationReply\(/,
  "waiting transition must occur only after confirmed provider send success",
);
assert.match(
  sendSource,
  /if \(isReplyComposeMode && composeSourceMessage\) \{[\s\S]*?onSuccessfulConversationReply\([\s\S]*?composeMode,[\s\S]*?sentAt/,
  "Reply and Reply All must share the successful waiting transition",
);
assert.match(
  sendSource,
  /composeSourceMessage &&\s*composeMode === "forward" &&\s*isVisiblePriorityMessage/,
  "only Forward may retain the legacy automatic Priority removal",
);
assert.doesNotMatch(
  sendSource,
  /\(composeMode === "reply" \|\|\s*composeMode === "reply_all"\)[\s\S]{0,180}onSetManualPriority/,
  "successful Reply and Reply All must not automatically write manual removed",
);
assert.match(
  source,
  /waitingOnOtherStorageKey[\s\S]*?window\.localStorage\.setItem\([\s\S]*?waitingOnOtherStorageKey,[\s\S]*?JSON\.stringify\(waitingOnOtherStore\)/,
  "waiting state must persist through the scoped local Priority architecture",
);
assert.match(
  source,
  /waitingOnOtherByMessageKey: runtimeWaitingOnOtherEvidence/,
  "waiting evidence must enter the central Priority runtime source and gate",
);
assert.match(
  source,
  /waitingOnOtherRepresentativeEntries\.forEach\(addUniqueEntry\)/,
  "one physical waiting representative must join central Priority candidates",
);

console.log("\nWorkspaceShell waiting_on_other integration tests passed.");
