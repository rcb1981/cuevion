import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const source = readFileSync(
  resolve(process.cwd(), "src/components/onboarding/StepConnectInboxes.tsx"),
  "utf8",
);

assert.match(
  source,
  /createICloudMailPreset/,
  "the connect-inboxes step must use the canonical iCloud preset helper",
);
assert.match(
  source,
  /Use iCloud settings/,
  "Custom IMAP onboarding must expose an iCloud quick-setup action",
);
assert.match(
  source,
  /app-specific password from your Apple Account/,
  "iCloud onboarding must tell users to use an app-specific password",
);
assert.match(
  source,
  /customImapInteractionLocked \|\| !connection\.email\.trim\(\)/,
  "the iCloud preset must require an email identity and respect the IMAP mutation lock",
);
assert.match(
  source,
  /onCustomImapChange\(inboxId, "host", preset\.imap\.host\)[\s\S]*?onCustomImapChange\(inboxId, "port", preset\.imap\.port\)[\s\S]*?onCustomImapChange\(inboxId, "ssl", preset\.imap\.ssl\)[\s\S]*?onCustomImapChange\(inboxId, "username", preset\.imap\.username\)/,
  "the iCloud preset must populate all incoming-mail settings",
);
assert.match(
  source,
  /onCustomSmtpChange\(inboxId, "host", preset\.smtp\.host\)[\s\S]*?onCustomSmtpChange\(inboxId, "port", preset\.smtp\.port\)[\s\S]*?"username"[\s\S]*?preset\.smtp\.username[\s\S]*?"security"[\s\S]*?preset\.smtp\.security[\s\S]*?"useSameCredentials"[\s\S]*?preset\.smtp\.useSameCredentials/,
  "the iCloud preset must populate secure SMTP settings using the same credentials",
);
