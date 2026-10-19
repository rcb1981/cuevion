import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {resolve} from "node:path";
import {createElement} from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {
  MicrosoftReconnectConsentButton,
  isTrustedMicrosoftReconnectUrl,
} from "./MicrosoftReconnectConsentButton";

const origin = "https://app.cuevion.com";
const email = "carltricks@live.nl";
const good = new URL("https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize");
good.searchParams.set("client_id", "some-app-id");
good.searchParams.set("state", "opaque-auth-state");
good.searchParams.set("response_type", "code");
good.searchParams.set("code_challenge", "opaque-pkce");
good.searchParams.set("code_challenge_method", "S256");
good.searchParams.set("login_hint", email);
good.searchParams.set("redirect_uri", origin + "/api/inboxes/oauth-callback");
good.searchParams.set("scope",
  "openid profile offline_access https://graph.microsoft.com/User.Read " +
  "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite");
assert.equal(isTrustedMicrosoftReconnectUrl(good.href, origin, email), true);

function changed(mutator: (link: URL) => void): string {
  const u = new URL(good.href);
  mutator(u);
  return u.href;
}
for (const untrusted of [
  "https://login.microsoftonline.com.evil.example/consumers/oauth2/v2.0/authorize",
  "https://evil.example/consumers/oauth2/v2.0/authorize",
  "http://login.microsoftonline.com/consumers/oauth2/v2.0/authorize",
  changed((u) => {u.username = "user";}),
  changed((u) => {u.hash = "#payload";}),
  changed((u) => {u.pathname = "/consumers/oauth2/v2.0/token";}),
  changed((u) => {u.searchParams.set("redirect_uri", "https://evil.example/callback");}),
  changed((u) => {u.searchParams.delete("state");}),
  changed((u) => {u.searchParams.append("state", "another");}),
  changed((u) => {u.searchParams.delete("code_challenge");}),
  changed((u) => {u.searchParams.set("response_type", "token");}),
  changed((u) => {u.searchParams.set("login_hint", "other@example.com");}),
  changed((u) => {u.searchParams.set("scope", "https://graph.microsoft.com/Mail.Read");}),
  changed((u) => {u.searchParams.set("scope", good.searchParams.get("scope")! +
    " https://graph.microsoft.com/Mail.Send");}),
]) {
  assert.equal(isTrustedMicrosoftReconnectUrl(untrusted, origin, email), false);
}
assert.equal(isTrustedMicrosoftReconnectUrl(null, origin, email), false);
assert.equal(isTrustedMicrosoftReconnectUrl("invalid URL", origin, email), false);

const markup = renderToStaticMarkup(createElement(MicrosoftReconnectConsentButton, {
  mailboxId: "microsoft-0123456789abcdefabcd12345678",
  email,
}));
assert.match(markup, /Reconnect Microsoft/);
assert.match(markup, /title="Reconnect this Microsoft account/);
assert.ok(markup.includes("text-[var(--workspace-text)]"),
  "Reconnect text must use explicit theme-aware foreground color");
assert.ok(markup.includes("bg-[var(--workspace-card-subtle)]"),
  "Reconnect background must be theme-aware");
assert.doesNotMatch(markup, /Remove mailbox|Add mailbox/);

const code = readFileSync(
  resolve(process.cwd(), "src/components/workspace/MicrosoftReconnectConsentButton.tsx"),
  "utf8",
);
assert.match(code, /buildOAuthInboxRequest\(\{/);
assert.match(code, /provider: "microsoft"/);
assert.match(code, /mode: "reconnect"/);
assert.match(code, /mailboxId,/);
assert.match(code, /connectInboxWithOAuth\(request\)/);
assert.match(code, /window\.location\.assign\(result\.authorizationUrl\)/);
assert.doesNotMatch(code, /localStorage|deleteInbox|removeInbox|createNewInbox/);

const view = readFileSync(
  resolve(process.cwd(), "src/components/workspace/MicrosoftMailboxReadOnlyView.tsx"),
  "utf8",
);
assert.doesNotMatch(view, /<MicrosoftReconnectConsentButton/,
  "Microsoft reconnect must not appear in the mailbox header");
assert.match(view, /onClick=\{onOpenConnectionSettings\}/,
  "Disconnected Microsoft mailboxes must retain the route to Settings");
const settingsSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);
assert.match(settingsSource, /import \{ MicrosoftReconnectConsentButton \}/);
assert.match(settingsSource, /<MicrosoftReconnectConsentButton\s+mailboxId=\{microsoftReconnectTarget\.id\}\s+email=\{microsoftReconnectTarget\.email\}/);
assert.match(settingsSource, /getPersistedMicrosoftReconnectTarget\(\s*savedManagedInboxes,\s*selectedInbox/);
assert.match(settingsSource, /isExisting && !editable && mailbox\.provider === "microsoft"/,
  "Unsaved or editable Microsoft mailbox drafts must not expose reconnect");
assert.ok(settingsSource.includes('label: linked ? "ACCOUNT LINKED"'));
assert.match(settingsSource, /healthStatus: null/,
  "Microsoft must not show a fabricated or perpetual live-health CHECKING state");
assert.ok(settingsSource.includes("hasRecordedMicrosoftOAuthConnection(mailbox)"));
assert.match(settingsSource, /canReconnectGmail && onReconnectAction/,
  "Gmail's existing reconnect action must remain intact");
assert.match(view, /const MICROSOFT_READ_ONLY_PILOT = true/);
assert.match(view, /const MICROSOFT_AUTO_MARK_READ_RELEASED = true/);

console.log("Microsoft exact-owner OAuth reconnection in Settings: PASS");
