import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const source = readFileSync(
  resolve(process.cwd(), "src/components/onboarding/StepConnectInboxes.tsx"),
  "utf8",
);

assert.match(
  source,
  /id: "icloud", label: "iCloud Mail"/,
  "iCloud must be a first-class onboarding choice",
);
assert.match(
  source,
  /provider\.id === "icloud"[\s\S]*?createICloudMailPreset\(connection\.email\)/,
  "iCloud selection must apply the canonical Apple preset",
);
assert.match(
  source,
  /provider\.id === "icloud"[\s\S]*?onProviderChange\(inboxId, "custom_imap"\)/,
  "iCloud must keep using the proven Custom IMAP runtime path",
);
assert.match(
  source,
  /provider\.id === "custom_imap" && iCloudSelected[\s\S]*?createInboxConnection\(\)\.customImap/,
  "switching from iCloud to Custom IMAP must clear Apple-specific settings",
);
assert.match(
  source,
  /isICloudOnboardingConnection\(connection\)[\s\S]*?"username"[\s\S]*?nextEmail\.trim\(\)/,
  "iCloud IMAP username must follow later email edits",
);
assert.match(
  source,
  /app-specific password from your Apple Account/,
  "iCloud must explain the app-specific password requirement",
);
assert.doesNotMatch(
  source,
  /Use iCloud settings/,
  "the old duplicate iCloud quick-setup button must be removed",
);
