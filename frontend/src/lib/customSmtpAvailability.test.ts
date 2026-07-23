import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {
  hasAuthoritativeCustomSmtpConfiguration,
  isAuthoritativeCustomImapIncomingConnected,
  isCompleteAuthoritativeCustomSmtpConfiguration,
  isCustomSmtpSendAvailable,
  type CustomSmtpAvailabilityInput,
} from "./customSmtpAvailability";

function createMailbox(
  overrides: Partial<CustomSmtpAvailabilityInput> = {},
): CustomSmtpAvailabilityInput {
  return {
    provider: "custom_imap",
    connected: true,
    connectionStatus: "connected",
    customImap: {
      username: "imap-user@example.com",
    },
    customSmtp: {
      host: "smtp.example.com",
      port: "587",
      security: "starttls",
      username: "smtp-user@example.com",
      useSameCredentials: false,
    },
    ...overrides,
  };
}

const sameCredentialsMailbox = createMailbox({
  customImap: { username: " imap-user@example.com " },
  customSmtp: {
    host: " smtp.example.com ",
    port: " 587 ",
    security: "starttls",
    username: "",
    useSameCredentials: true,
  },
});
assert.equal(isCustomSmtpSendAvailable(sameCredentialsMailbox), true);

const distinctCredentialsMailbox = createMailbox({
  customSmtp: {
    host: "smtp.example.com",
    port: "465",
    security: "ssl",
    username: " smtp-user@example.com ",
    useSameCredentials: false,
  },
});
assert.equal(isCustomSmtpSendAvailable(distinctCredentialsMailbox), true);

const sanitizedMailbox = {
  ...sameCredentialsMailbox,
  customImap: {
    ...sameCredentialsMailbox.customImap,
    password: "",
  },
  customSmtp: {
    ...sameCredentialsMailbox.customSmtp,
    password: "",
  },
  credentialStatuses: {},
};
assert.equal(isCustomSmtpSendAvailable(sanitizedMailbox), true);

assert.equal(
  isCustomSmtpSendAvailable(createMailbox({ provider: "google" })),
  false,
);
assert.equal(
  isCustomSmtpSendAvailable(createMailbox({ connected: false })),
  false,
);
assert.equal(
  isCustomSmtpSendAvailable(createMailbox({ connectionStatus: "reconnect_required" })),
  false,
);
assert.equal(
  isCustomSmtpSendAvailable(createMailbox({
    customSmtp: {
      ...distinctCredentialsMailbox.customSmtp,
      host: " ",
    },
  })),
  false,
);

for (const port of ["", "0", "65536", "1.5", "587x"]) {
  assert.equal(
    isCustomSmtpSendAvailable(createMailbox({
      customSmtp: {
        ...distinctCredentialsMailbox.customSmtp,
        port,
      },
    })),
    false,
  );
}

assert.equal(
  isCustomSmtpSendAvailable(createMailbox({
    customSmtp: {
      ...distinctCredentialsMailbox.customSmtp,
      security: "tls",
    },
  })),
  false,
);
assert.equal(
  isCustomSmtpSendAvailable(createMailbox({
    customImap: { username: " " },
    customSmtp: {
      ...sameCredentialsMailbox.customSmtp,
      useSameCredentials: true,
    },
  })),
  false,
);
assert.equal(
  isCustomSmtpSendAvailable(createMailbox({
    customSmtp: {
      ...distinctCredentialsMailbox.customSmtp,
      username: " ",
      useSameCredentials: false,
    },
  })),
  false,
);

const immutableMailbox = createMailbox();
const immutableSnapshot = JSON.stringify(immutableMailbox);
assert.equal(isCustomSmtpSendAvailable(immutableMailbox), true);
assert.equal(JSON.stringify(immutableMailbox), immutableSnapshot);

const incomingOnlyMailbox = createMailbox({
  customSmtp: {
    host: "",
    port: "",
    security: "starttls",
    username: "",
    useSameCredentials: false,
  },
});
assert.equal(
  isAuthoritativeCustomImapIncomingConnected(incomingOnlyMailbox, {
    imapPasswordSet: true,
    smtpPasswordSet: false,
  }),
  true,
);
assert.equal(isCustomSmtpSendAvailable(incomingOnlyMailbox), false);
assert.equal(hasAuthoritativeCustomSmtpConfiguration({}), false);
assert.equal(
  hasAuthoritativeCustomSmtpConfiguration({ password: "" }),
  false,
);

const authoritativeSmtpConfiguration = {
  host: "smtp.example.com",
  port: "587",
  security: "starttls",
  username: "smtp-user@example.com",
  useSameCredentials: false,
  password: "",
};
assert.equal(
  hasAuthoritativeCustomSmtpConfiguration(authoritativeSmtpConfiguration),
  true,
);
assert.equal(
  isCompleteAuthoritativeCustomSmtpConfiguration(
    authoritativeSmtpConfiguration,
  ),
  true,
);
assert.equal(
  isCompleteAuthoritativeCustomSmtpConfiguration({
    ...authoritativeSmtpConfiguration,
    username: "",
    useSameCredentials: true,
  }),
  true,
);
assert.equal(
  isCompleteAuthoritativeCustomSmtpConfiguration({
    ...authoritativeSmtpConfiguration,
    username: "",
    useSameCredentials: false,
  }),
  false,
);
for (const partialSmtp of [
  { host: "smtp.example.com", password: "" },
  { security: "ssl", password: "" },
  {
    ...authoritativeSmtpConfiguration,
    port: "",
  },
  {
    ...authoritativeSmtpConfiguration,
    unknownAuthority: "must-fail-closed",
  },
]) {
  assert.equal(hasAuthoritativeCustomSmtpConfiguration(partialSmtp), true);
  assert.equal(
    isCompleteAuthoritativeCustomSmtpConfiguration(partialSmtp),
    false,
  );
}
assert.equal(
  isAuthoritativeCustomImapIncomingConnected(incomingOnlyMailbox, {
    imapPasswordSet: false,
    smtpPasswordSet: false,
  }),
  false,
);
assert.equal(
  isAuthoritativeCustomImapIncomingConnected(incomingOnlyMailbox, {}),
  false,
);

const workspaceSource = fs.readFileSync(
  path.resolve(__dirname, "../components/workspace/WorkspaceShell.tsx"),
  "utf8",
);

function sourceBetween(startMarker: string, endMarker: string) {
  const start = workspaceSource.indexOf(startMarker);
  assert.notEqual(start, -1, `Missing start marker: ${startMarker}`);
  const end = workspaceSource.indexOf(endMarker, start);
  assert.notEqual(end, -1, `Missing end marker: ${endMarker}`);
  return workspaceSource.slice(start, end);
}

const sendCallRegions = [
  sourceBetween(
    "const sendMessage = async",
    "setComposeSendError(null)",
  ),
  sourceBetween(
    "const sendExternalReviewInviteToEmail = async",
    "let inviteLink =",
  ),
  sourceBetween(
    "const getPrimaryTeamInviteMailbox =",
    "const escapeInviteHtml =",
  ),
];

for (const region of sendCallRegions) {
  assert.notEqual(region.length, 0);
  assert.equal(region.includes("canSendFromManagedMailbox"), true);
  assert.equal(region.includes("credentialStatuses"), true);
  for (const forbidden of [
    "customImap.password",
    "customSmtp.password",
    "imapPasswordSet",
    "smtpPasswordSet",
    "hasUsableSmtpPassword",
  ]) {
    assert.equal(region.includes(forbidden), false);
  }
}

const authoritativeSendReadinessRegion = sourceBetween(
  "function canSendFromManagedMailbox",
  "function isManagedInboxSyncCapable",
);
assert.equal(
  authoritativeSendReadinessRegion.includes("smtpPasswordSet === true"),
  true,
);
assert.equal(
  authoritativeSendReadinessRegion.includes(
    "isCompleteManagedCustomSmtpSettings",
  ),
  true,
);
assert.equal(
  authoritativeSendReadinessRegion.includes("imapPasswordSet"),
  false,
);
assert.equal(
  authoritativeSendReadinessRegion.includes("customImap.password"),
  false,
);
assert.equal(
  authoritativeSendReadinessRegion.includes("customSmtp.password"),
  false,
);

const managedInboxEditorRegion = sourceBetween(
  "function ManagedInboxEditor",
  "const ManageInboxesView",
);
for (const required of [
  "Stored securely — leave blank to reuse",
  "Outgoing mail configured",
  "Outgoing mail not configured",
  "SSL/TLS required",
  "hasAuthoritativeImapPassword",
  "hasAuthoritativeSmtpPassword",
  "customImapCredentialUnavailable",
  "getCredentialAwareManagedInboxStatus",
]) {
  assert.equal(
    managedInboxEditorRegion.includes(required),
    true,
    `Managed inbox editor must contain ${required}`,
  );
}
assert.equal(managedInboxEditorRegion.includes("Ready to send"), false);
assert.equal(
  managedInboxEditorRegion.includes(
    'type="checkbox"\n                    checked\n                    disabled',
  ),
  true,
);

const credentialAwareStatusRegion = sourceBetween(
  "function getCredentialAwareManagedInboxStatus",
  "function ManagedInboxEditor",
);
for (const required of [
  "isAuthoritativeCustomImapIncomingConnected",
  "Reconnect required",
  "isCredentialAwareSelectablePrimaryManagedInbox",
]) {
  assert.equal(
    credentialAwareStatusRegion.includes(required),
    true,
    `Credential-aware Settings status must contain ${required}`,
  );
}

const managedInboxSidebarRegion = sourceBetween(
  "Connected inboxes",
  "{selectedInbox ?",
);
assert.equal(
  managedInboxSidebarRegion.includes(
    "getCredentialAwareManagedInboxStatus",
  ),
  true,
  "The Settings inbox selector must use the credential-aware status",
);

const authoritativeReloadRegion = sourceBetween(
  "const reloadAuthoritativeManagedMailbox",
  "const orderedManagedInboxes",
);
for (const required of [
  "loadUserAccountConfig",
  "matchingTargets.length !== 1",
  "isExactAuthoritativeCustomImapTarget",
  "hasAuthoritativeCustomSmtpConfiguration",
  "isCompleteAuthoritativeCustomSmtpConfiguration",
  "getMailboxCredentialStatuses",
  "imapPasswordSet !== true",
  "setSavedManagedInboxes",
]) {
  assert.equal(
    authoritativeReloadRegion.includes(required),
    true,
    `Authoritative mailbox reload must contain ${required}`,
  );
}

const authoritativeTargetValidationRegion = sourceBetween(
  "function isExactAuthoritativeCustomImapTarget",
  "function mergeOnboardingSeedWithSavedInboxes",
);
assert.equal(
  authoritativeTargetValidationRegion.includes("customImap.ssl === true"),
  true,
);

const managedInboxAccountConfigProjectionRegion = sourceBetween(
  "function sanitizeManagedWorkspaceInboxForAccountConfig",
  "function sanitizeOnboardingStateForAccountConfig",
);
assert.equal(
  managedInboxAccountConfigProjectionRegion.includes(
    "projectManagedMailboxAccountConfigIdentity",
  ),
  true,
);
assert.equal(
  managedInboxAccountConfigProjectionRegion.includes(
    "mailboxId: sanitizedMailbox.id",
  ),
  true,
);
assert.equal(
  managedInboxAccountConfigProjectionRegion.includes(
    "onboardingInboxId: sanitizedMailbox.onboardingInboxId",
  ),
  true,
);

const managedInboxNormalizationRegion = sourceBetween(
  "function normalizeManagedInboxForStorage",
  "function normalizeStoredManagedInboxList",
);
assert.equal(
  managedInboxNormalizationRegion.includes("const safeMailbox = {"),
  true,
);
assert.equal(
  managedInboxNormalizationRegion.includes("const safeMailbox = {\n    ...mailbox"),
  false,
);

const managedInboxIdentityProjectionRegion = sourceBetween(
  "function toManagedWorkspaceInbox",
  "function toOrderedMailboxFromManagedInbox",
);
assert.equal(
  managedInboxIdentityProjectionRegion.includes(
    "resolveManagedMailboxIdentity",
  ),
  true,
);
assert.equal(
  managedInboxIdentityProjectionRegion.includes(
    "serverMailboxId: connection.serverMailboxId",
  ),
  true,
);
assert.equal(
  managedInboxIdentityProjectionRegion.includes(
    "onboardingInboxId: identity.onboardingInboxId",
  ),
  true,
);

const managedInboxValidationRequestRegion = sourceBetween(
  "const validateManagedInbox = async",
  "const isMailboxPersistedWithoutChanges",
);
for (const required of [
  "shouldIncludeCustomSmtp",
  "hasExplicitManagedCustomSmtpChange",
  "isCompleteManagedCustomSmtpSettings",
]) {
  assert.equal(
    managedInboxValidationRequestRegion.includes(required),
    true,
    `Managed inbox validation request must contain ${required}`,
  );
}

const managedInboxCommitRegion = sourceBetween(
  "const commitSingleInboxChanges = async",
  "const connectManagedInbox = async",
);
assert.equal(
  managedInboxCommitRegion.includes("draftMailbox ?? mailboxForStorage"),
  true,
  "Apply must validate the unmerged SMTP draft before persisted values can fill blanks",
);

const managedInboxConnectRegion = sourceBetween(
  "const connectManagedInbox = async",
  "const updateDraftInbox =",
);
assert.match(
  managedInboxConnectRegion,
  /getManagedCustomSmtpDraftError\(\s*mailbox \?\? mailboxForConnection,\s*savedMailbox,\s*\)/,
  "Reconnect must validate the unmerged SMTP draft",
);

const workspaceAccountConfigSaveRegion = sourceBetween(
  "const nextAccountConfig: UserAccountConfig =",
  "const handleConfirmLogout = async",
);
assert.equal(
  workspaceAccountConfigSaveRegion.includes(
    "workspaceAccountConfigSaveQueueRef.current?.enqueue(nextAccountConfig)",
  ),
  true,
);
assert.equal(
  workspaceAccountConfigSaveRegion.includes(
    "workspaceAccountConfigSaveQueueRef.current?.supersede()",
  ),
  true,
);

console.log("customSmtpAvailability tests passed");
