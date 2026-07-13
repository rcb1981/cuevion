import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {
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

const sendReadinessRegions = [
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
  sourceBetween(
    "function canSendFromManagedMailbox",
    "function isManagedInboxSyncCapable",
  ),
];

for (const region of sendReadinessRegions) {
  assert.notEqual(region.length, 0);
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

console.log("customSmtpAvailability tests passed");
