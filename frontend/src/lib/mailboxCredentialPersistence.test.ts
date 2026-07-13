import assert from "node:assert/strict";
import {
  sanitizeAccountConfigCredentials,
  sanitizeManagedInboxCredentials,
  sanitizeStoredMailboxCredentialJson,
} from "./mailboxCredentialPersistence";
import {
  buildConnectInboxRequest,
  buildRefreshInboxRequest,
  buildSendInboxWireRequest,
} from "./inboxConnectionApi";

const legacy = {
  completed: true,
  state: {
    inboxConnections: {
      demo: {
        email: "demo@example.com",
        customImap: { host: "imap.example.com", password: "imap-secret" },
        customSmtp: { host: "smtp.example.com", password: "smtp-secret" },
      },
    },
    messages: [{ id: "m1", body: "The password policy stays in this message." }],
    labels: ["Demo"],
    settings: { compact: true },
    learningState: { sender: "normal" },
    snapshots: [{ mailboxId: "demo", count: 4 }],
  },
};

const rawLegacy = JSON.stringify(legacy);
const migrated = sanitizeStoredMailboxCredentialJson(rawLegacy);
assert.equal(migrated.valid, true);
assert.equal(migrated.rewriteRequired, true);
assert.equal(
  (migrated.value as any).state.inboxConnections.demo.customImap.password,
  "",
);
assert.equal(
  (migrated.value as any).state.inboxConnections.demo.customSmtp.password,
  "",
);
assert.deepEqual((migrated.value as any).state.messages, legacy.state.messages);
assert.deepEqual((migrated.value as any).state.labels, legacy.state.labels);
assert.deepEqual((migrated.value as any).state.settings, legacy.state.settings);
assert.deepEqual((migrated.value as any).state.learningState, legacy.state.learningState);
assert.deepEqual((migrated.value as any).state.snapshots, legacy.state.snapshots);

const managed = sanitizeManagedInboxCredentials([
  {
    id: "stable-id",
    customImap: { username: "demo", password: "secret" },
    customSmtp: { username: "demo", password: "other" },
    message: { password: "message-content-must-remain" },
  },
]) as any[];
assert.equal(managed[0].id, "stable-id");
assert.equal(managed[0].customImap.password, "");
assert.equal(managed[0].customSmtp.password, "");
assert.equal(managed[0].message.password, "message-content-must-remain");

const original = { inboxConnections: { demo: { customImap: { password: "x" } } } };
const sanitized = sanitizeAccountConfigCredentials(original) as any;
assert.equal((original as any).inboxConnections.demo.customImap.password, "x");
assert.equal(sanitized.inboxConnections.demo.customImap.password, "");
assert.equal(sanitizeStoredMailboxCredentialJson("not-json").valid, false);

const initialRequest = buildConnectInboxRequest({
  mode: "initial",
  mailboxId: "demo",
  provider: "custom_imap",
  email: " demo@example.com ",
  customImap: {
    host: "imap.example.com",
    port: "993",
    ssl: true,
    username: "demo@example.com",
    password: "one-time-secret",
  },
  customSmtp: {
    host: "smtp.example.com",
    port: "587",
    security: "starttls",
    username: "",
    password: "",
    useSameCredentials: true,
  },
});
assert.equal(initialRequest.mode, "initial");
assert.equal(initialRequest.connection.imap.password, "one-time-secret");

const refreshRequest = buildRefreshInboxRequest({
  mailboxId: "demo",
  limit: 20,
  focusPreferences: null,
});
assert.deepEqual(Object.keys(refreshRequest).sort(), [
  "focusPreferences",
  "limit",
  "mailboxId",
  "mode",
]);
assert.equal(JSON.stringify(refreshRequest).includes("password"), false);
assert.equal(JSON.stringify(refreshRequest).includes("imap.example.com"), false);

const customSmtpWireRequest = buildSendInboxWireRequest({
  provider: "custom_imap",
  mailboxId: "demo",
  authMode: "smtp",
  email: "spoof@example.com",
  username: "spoof-user",
  password: "must-not-send",
  smtpHost: "evil.example.com",
  smtpPort: "465",
  smtpSecurity: "ssl",
  from: "spoof@example.com",
  to: "recipient@example.com",
  subject: "Subject",
  bodyHtml: "<p>Body</p>",
  bodyText: "Body",
});
const customSmtpWireJson = JSON.stringify(customSmtpWireRequest);
assert.equal(customSmtpWireJson.includes("must-not-send"), false);
assert.equal(customSmtpWireJson.includes("evil.example.com"), false);
assert.equal(customSmtpWireJson.includes("spoof@example.com"), false);

console.log("mailboxCredentialPersistence tests passed");
