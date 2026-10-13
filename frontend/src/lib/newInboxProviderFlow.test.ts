import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  getNewInboxProviderChoice,
  getNewInboxProviderSelection,
  newInboxProviderChoices,
  updateNewICloudPresetEmail,
} from "./newInboxProviderFlow";

assert.deepEqual(
  newInboxProviderChoices.map((item) => item.id),
  ["google", "microsoft", "icloud", "custom_imap"],
);

const google = getNewInboxProviderSelection("google", "gmail@example.com");
assert.equal(google.provider, "google");
assert.equal(google.connectionMethod, "oauth");
assert.equal(google.connectionStatus, "oauth_required");

const microsoft = getNewInboxProviderSelection("microsoft", "outlook@example.com");
assert.equal(microsoft.provider, "microsoft");
assert.equal(microsoft.connectionMethod, "oauth");
assert.equal(microsoft.connectionStatus, "oauth_required");
assert.equal(microsoft.customImap.password, "");
assert.equal(microsoft.customSmtp.password, "");

const icloud = getNewInboxProviderSelection("icloud", "test@icloud.com");
assert.equal(icloud.provider, "custom_imap");
assert.equal(icloud.connectionMethod, "imap");
assert.equal(icloud.customImap.host, "imap.mail.me.com");
assert.equal(icloud.customImap.port, "993");
assert.equal(icloud.customImap.ssl, true);
assert.equal(icloud.customImap.username, "test@icloud.com");
assert.equal(icloud.customSmtp.host, "smtp.mail.me.com");
assert.equal(icloud.customSmtp.port, "587");
assert.equal(icloud.customSmtp.security, "starttls");
assert.equal(icloud.customSmtp.useSameCredentials, true);
assert.equal(getNewInboxProviderChoice(icloud.provider, icloud.customImap, icloud.customSmtp), "icloud");

const authorizedImap = { ...icloud.customImap, password: "example-app-specific-password" };
assert.deepEqual(
  updateNewICloudPresetEmail(icloud.provider, authorizedImap, icloud.customSmtp, " updated@icloud.com "),
  { ...authorizedImap, username: "updated@icloud.com" },
);
assert.strictEqual(
  updateNewICloudPresetEmail("custom_imap", { ...authorizedImap, host: "mail.example.com" }, icloud.customSmtp, "wrong@example.com")
    .username,
  authorizedImap.username,
);

const generic = getNewInboxProviderSelection("custom_imap", "custom@example.com");
assert.equal(generic.provider, "custom_imap");
assert.equal(generic.customImap.host, "");
assert.equal(generic.customSmtp.host, "");
assert.equal(getNewInboxProviderChoice(generic.provider, generic.customImap, generic.customSmtp), "custom_imap");

const workspace = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);
const copy = readFileSync(
  resolve(process.cwd(), "src/copy/onboardingCopy.ts"),
  "utf8",
);
const newEditor = workspace.slice(
  workspace.indexOf("function ManagedInboxEditor"),
  workspace.indexOf("const ManageInboxesView"),
);
const addAction = workspace.slice(
  workspace.indexOf("const handleStartAddInbox"),
  workspace.indexOf("const handleClose", workspace.indexOf("const handleStartAddInbox")),
);
const oauthApply = workspace.slice(
  workspace.indexOf("const commitSingleInboxChanges"),
  workspace.indexOf("const connectManagedInbox"),
);

assert.match(addAction, /setActiveInboxEditorTab\("Provider"\)/);
assert.match(newEditor, /activeTab === "Provider" && !isExisting/);
assert.match(newEditor, /newInboxProviderChoices\.map/);
assert.match(newEditor, /onChooseProvider\(mailbox\.id, choice\.id\)/);
assert.match(newEditor, /onTabChange\("Provider"\)/);
assert.match(newEditor, /isExisting\s*\?\s*managedInboxEditorTabs/);
assert.match(newEditor, /disabled=\{!isExisting && tab !== "Provider" && !selectedNewProvider\}/);
assert.match(newEditor, /mailbox\.provider === "microsoft" \? \[\] :/);
assert.match(workspace, /mailbox\.id\.startsWith\("draft-"\)[\s\S]*?\? mailbox\.provider/);
assert.match(newEditor, /Microsoft Graph — read-only pilot/);
assert.match(newEditor, /Microsoft is read-only during this pilot/);
assert.match(newEditor, /app-specific/);
assert.match(workspace, /return provider === "google" \|\| provider === "microsoft" \|\| provider === "custom_imap"/);
assert.match(workspace, /getNewInboxProviderSelection\(choice, mailbox\.email\)/);
assert.match(workspace, /updateNewICloudPresetEmail/);
assert.match(oauthApply, /if \(mailboxForStorage\.provider === "google"\) \{[\s\S]*?savePendingOAuthManagedInbox/);
assert.match(copy, /id: "microsoft", label: "Microsoft 365 \/ Outlook"/);

console.log("Provider-first settings and integration checks: PASS");
