import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const workspaceSource = fs.readFileSync(
  path.resolve(__dirname, "../components/workspace/WorkspaceShell.tsx"),
  "utf8",
);
const apiSource = fs.readFileSync(
  path.resolve(__dirname, "./imapFolderMappingApi.ts"),
  "utf8",
);

function section(source: string, start: string, end: string) {
  const startIndex = source.indexOf(start);
  const endIndex = source.indexOf(end, startIndex + start.length);
  assert.notEqual(startIndex, -1, `missing source marker: ${start}`);
  assert.notEqual(endIndex, -1, `missing source marker: ${end}`);
  return source.slice(startIndex, endIndex);
}

const mappingCard = section(
  workspaceSource,
  "function CustomImapTrashFolderMappingCard",
  "function ManagedInboxEditor",
);
const receivingPanel = section(
  workspaceSource,
  '{activeTab === "Receiving"',
  '{activeTab === "Sending"',
);
let passedCases = 0;

assert.match(
  receivingPanel,
  /isExisting &&\s+mailbox\.provider === "custom_imap" &&\s+mailbox\.connected &&\s+mailbox\.connectionStatus === "connected" &&\s+!customImapCredentialUnavailable[\s\S]*<CustomImapTrashFolderMappingCard mailboxId=\{mailbox\.id\} key=\{mailbox\.id\}/,
  "folder mapping must be limited to an existing authoritative connected custom IMAP mailbox",
);
assert.doesNotMatch(
  receivingPanel,
  /mailbox\.provider === "google"[\s\S]{0,240}<CustomImapTrashFolderMappingCard/,
  "Gmail settings must not render the folder mapping card",
);
passedCases += 1;

assert.match(mappingCard, /Folder mapping/);
assert.match(mappingCard, /Trash folder/);
assert.match(
  mappingCard,
  /inventory\?\.trash\.mode === "automatic"[\s\S]*Automatically detected: \{inventory\.trash\.currentFolder\}/,
);
assert.match(
  mappingCard,
  /inventory\.trash\.mode === "configured"[\s\S]*Mapped to \$\{inventory\.trash\.currentFolder\}[\s\S]*Needs selection/,
);
passedCases += 1;

assert.match(
  mappingCard,
  /<select[\s\S]*aria-label="Trash folder"[\s\S]*inventory\.folders\.map\(\(folder\) => \([\s\S]*value=\{folder\.providerFolder\}[\s\S]*\{folder\.providerFolder\}/,
  "dropdown values must come only from the strict server inventory",
);
assert.doesNotMatch(
  mappingCard,
  /<input[^>]+(?:Trash|folder)|type="text"/,
  "folder mapping must not provide free-text authority",
);
passedCases += 1;

assert.match(
  mappingCard,
  /const exactSelectedFolder = inventory\?\.folders\.find\(\s*\(folder\) => folder\.providerFolder === selectedFolder,\s*\)\?\.providerFolder/,
  "Save must project the selection back through the current server-returned options",
);
assert.match(
  mappingCard,
  /inventory\.mailboxId !== mailboxId/,
  "a prior mailbox inventory must never authorize a newly selected mailbox",
);
assert.match(
  mappingCard,
  /saveImapTrashFolderMapping\(\{\s+mailboxId,\s+role: "trash",\s+selectedFolder: exactSelectedFolder,\s+\}\)/,
  "Save must send only mailboxId, the Trash role, and the exact selected folder",
);
passedCases += 1;

const refreshStart = mappingCard.indexOf(
  "const refreshFolders = useCallback(async () =>",
);
const refreshRequest = mappingCard.indexOf(
  "const response = await listImapTrashFolders({ mailboxId });",
  refreshStart,
);
const clearInventory = mappingCard.indexOf("setInventory(null);", refreshStart);
const clearSelection = mappingCard.indexOf('setSelectedFolder("");', refreshStart);
assert.ok(refreshStart >= 0);
assert.ok(clearInventory > refreshStart && clearInventory < refreshRequest);
assert.ok(clearSelection > refreshStart && clearSelection < refreshRequest);
assert.match(
  mappingCard.slice(refreshRequest),
  /operationGeneration !== operationGenerationRef\.current[\s\S]*return;/,
  "stale inventory responses must be fenced before publication",
);
passedCases += 1;

assert.match(
  mappingCard,
  /if \(!response\.ok\) \{\s+\/\/ Save performs a fresh server LIST\.[\s\S]*setInventory\(null\);\s+setSelectedFolder\(""\);/,
  "a failed save-time LIST must invalidate all prior options",
);
assert.match(
  mappingCard,
  /applyAuthoritativeInventory\(response\);\s+setSuccessMessage/,
  "save success must render only the endpoint's authoritative reread",
);
passedCases += 1;

assert.match(mappingCard, /Refresh folders/);
assert.match(mappingCard, /phase === "loading" \? "Loading folders\.\.\."/);
assert.match(workspaceSource, /Provider folders could not be verified safely/);
assert.match(mappingCard, /No safe selectable provider folders are available/);
passedCases += 1;

assert.doesNotMatch(
  mappingCard,
  /localStorage|sessionStorage|indexedDB/,
  "mapping state must remain ephemeral React state",
);
assert.doesNotMatch(
  mappingCard,
  /(?:host|port|password|username|credentialVersion|secret|token)\s*:/,
  "folder mapping must not carry connection or credential authority",
);
assert.doesNotMatch(
  mappingCard,
  /connectInboxWithImap|onReconnectAction|Reconnect mailbox/,
  "an existing connected mailbox must not reconnect to configure a folder",
);
passedCases += 1;

assert.match(
  apiSource,
  /endpoint: "\/api\/inboxes\/list-imap-folders"[\s\S]*requestBody: \{ mailboxId \}/,
);
assert.match(
  apiSource,
  /endpoint: "\/api\/inboxes\/save-imap-folder-mapping"[\s\S]*requestBody: \{\s+mailboxId: request\.mailboxId,\s+role: request\.role,\s+selectedFolder: request\.selectedFolder/,
);
passedCases += 1;

assert.match(apiSource, /credentials: "include"/);
assert.match(apiSource, /cache: "no-store"/);
assert.match(
  apiSource,
  /hasExactKeys\(value, \["ok", "mailboxId", "trash", "folders"\]\)/,
);
assert.match(
  apiSource,
  /hasExactKeys\(entry, \["providerFolder"\]\)/,
);
assert.match(
  apiSource,
  /value\.toLowerCase\(\) !== "inbox"/,
  "INBOX and its case variants must never become a Trash target",
);
assert.match(
  apiSource,
  /hasUnpairedSurrogate\(value\)/,
  "unpaired UTF-16 surrogates must fail before transport or rendering",
);
assert.doesNotMatch(
  apiSource,
  /localStorage|sessionStorage|targetFolder|archiveFolder/,
);
passedCases += 1;

assert.equal(passedCases, 10);
console.log(
  `IMAP folder mapping Workspace wiring tests passed (${passedCases} cases)`,
);
