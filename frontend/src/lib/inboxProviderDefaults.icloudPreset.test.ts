import assert from "node:assert/strict";
import { createICloudMailPreset, isICloudMailPreset } from "./inboxProviderDefaults";

const preset = createICloudMailPreset("  test.user@icloud.com  ");

assert.deepEqual(preset.imap, {
  host: "imap.mail.me.com",
  port: "993",
  ssl: true,
  username: "test.user@icloud.com",
  password: "",
});
assert.deepEqual(preset.smtp, {
  host: "smtp.mail.me.com",
  port: "587",
  security: "starttls",
  username: "",
  password: "",
  useSameCredentials: true,
});

assert.equal(isICloudMailPreset(preset.imap, preset.smtp), true);
assert.equal(
  isICloudMailPreset({ ...preset.imap, host: "imap.example.com" }, preset.smtp),
  false,
);
assert.equal(
  isICloudMailPreset(preset.imap, { ...preset.smtp, useSameCredentials: false }),
  false,
);
