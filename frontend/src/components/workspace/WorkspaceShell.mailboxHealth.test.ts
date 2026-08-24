import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { getMailboxHealthPresentation } from "../../lib/mailboxProviderHealth";

const workspaceShellSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);

function sourceBetween(start: string, end: string) {
  const startIndex = workspaceShellSource.indexOf(start);
  const endIndex = workspaceShellSource.indexOf(end, startIndex + start.length);

  assert.notEqual(startIndex, -1, `${start} must exist`);
  assert.notEqual(endIndex, -1, `${end} must exist after ${start}`);
  return workspaceShellSource.slice(startIndex, endIndex);
}

function desktopActionButtonOpeningTagBefore(
  source: string,
  label: string,
) {
  const labelIndex = source.indexOf(label);
  assert.notEqual(labelIndex, -1, `${label} must exist`);

  const buttonStart = source.lastIndexOf("<DesktopActionButton", labelIndex);
  const buttonEnd = source.indexOf(">", buttonStart);
  const buttonClose = source.indexOf("</DesktopActionButton>", buttonEnd);
  assert.notEqual(buttonStart, -1, `${label} must use DesktopActionButton`);
  assert.ok(
    buttonEnd > buttonStart &&
      buttonEnd < labelIndex &&
      buttonClose > labelIndex,
    `${label} must be inside its DesktopActionButton`,
  );
  return source.slice(
    buttonStart,
    buttonClose + "</DesktopActionButton>".length,
  );
}

const mailboxHealthImportSource = sourceBetween(
  "import {\n  beginMailboxHealthOperation",
  '} from "../../lib/mailboxProviderHealth";',
);
const mailboxHealthDotSource = sourceBetween(
  "function mailboxHealthDotClass",
  "function mailboxHealthTextClass",
);
const mailboxConnectionStateSource = sourceBetween(
  "function MailboxConnectionState",
  "const inboxDisplayConfig",
);
const inboxesViewSource = sourceBetween(
  "function InboxesView",
  "const mailboxNavigationBackButtonClass",
);
const managedInboxHealthDerivationSource = sourceBetween(
  "function getCredentialAwareManagedInboxStatus",
  "function isCredentialAwareSelectablePrimaryManagedInbox",
);
const managedInboxEditorSource = sourceBetween(
  "function ManagedInboxEditor",
  "const ManageInboxesView",
);
const manageInboxesViewSource = sourceBetween(
  "const ManageInboxesView",
  "const SignatureBlock",
);
const settingsViewSource = sourceBetween(
  "function SettingsView",
  "function createContactRequestId",
);
const gmailAttentionSource = sourceBetween(
  "  const gmailOAuthReconnectRequired =",
  "  const customImapActionRequired =",
);
const gmailReconnectAvailabilitySource = sourceBetween(
  "  const canReconnectGmail =",
  "  const unsupportedProviderLabel =",
);
const connectionSettingsNavigationSource = sourceBetween(
  "  const openMailboxConnectionSettings =",
  "  const handleChangeSection =",
);
const customImapRecoveryRouteSource = sourceBetween(
  "  const handleReconnectInbox =",
  "  const pendingInboxRemoval =",
);
const authoritativeMailboxReloadSource = sourceBetween(
  "  const reloadAuthoritativeManagedMailbox = useCallback(",
  "  const orderedManagedInboxes = useMemo(",
);

// N. The mailbox header consumes current health and can no longer manufacture a
// green Connected presentation from zero inputs.
assert.match(mailboxHealthImportSource, /\bgetMailboxHealthPresentation\b/);
assert.match(
  mailboxConnectionStateSource,
  /function MailboxConnectionState\(\{[\s\S]*?health,[\s\S]*?onActionRequired,/,
);
assert.match(mailboxConnectionStateSource, /if \(!health\) \{\s+return null;/);
assert.match(mailboxConnectionStateSource, /const status = health\.status/);
assert.match(
  mailboxConnectionStateSource,
  /const presentation = getMailboxHealthPresentation\(status\)/,
);
assert.match(mailboxConnectionStateSource, /\{presentation\.label\}/);
assert.doesNotMatch(
  mailboxConnectionStateSource,
  />\s*Connected\s*</i,
  "MailboxConnectionState must not retain a hard-coded Connected label",
);
assert.match(
  mailboxConnectionStateSource,
  /status === "action_required" && onActionRequired/,
  "only Action Required may turn the compact status into a recovery action",
);
assert.match(
  workspaceShellSource,
  /<MailboxConnectionState\s+health=\{mailboxHealth\}\s+onActionRequired=\{onOpenConnectionSettings\}/,
  "the active mailbox header must receive its current health and recovery route",
);

// O. All four labels come from the shared helper, while the status switch keeps
// four distinct visual tones (Checking is the safe default).
assert.match(mailboxHealthDotSource, /case "connected":/);
assert.match(mailboxHealthDotSource, /case "temporary_issue":/);
assert.match(mailboxHealthDotSource, /case "action_required":/);
assert.match(mailboxHealthDotSource, /default:[\s\S]*?mailboxCheckingDotClass/);
assert.deepEqual(
  ["checking", "connected", "temporary_issue", "action_required"].map(
    (status) =>
      getMailboxHealthPresentation(
        status as Parameters<typeof getMailboxHealthPresentation>[0],
      ).label,
  ),
  ["CHECKING", "CONNECTED", "TEMPORARY ISSUE", "ACTION REQUIRED"],
);

// P. The inbox overview resolves the exact row by mailbox ID.
assert.match(inboxesViewSource, /mailboxHealthById: MailboxHealthStore/);
assert.match(
  inboxesViewSource,
  /<MailboxConnectionState health=\{mailboxHealthById\[inbox\.id\]\} \/>/,
);
assert.doesNotMatch(
  inboxesViewSource,
  /\?\? "checking"|\? inbox\.detail/,
  "unconfigured rows must not manufacture Checking and Connected rows must not use static sync-age copy",
);
assert.match(
  inboxesViewSource,
  /: "Provider connection is not configured\."/,
);

// Q. Settings list and detail consume the same map and shared presentation
// derivation, preventing contradictory badges for one mailbox in one session.
assert.match(
  managedInboxHealthDerivationSource,
  /mailboxHealth\?: MailboxHealthRecord \| null/,
);
assert.match(
  managedInboxHealthDerivationSource,
  /const healthStatus = mailboxHealth\?\.status \?\? fallbackHealthStatus/,
);
assert.match(
  managedInboxHealthDerivationSource,
  /credentialStatus !== undefined[\s\S]*?getMailboxHealthPresentation\(healthStatus\)/,
  "only definitive missing IMAP credential evidence may override current health",
);
assert.match(
  managedInboxEditorSource,
  /getCredentialAwareManagedInboxStatus\([\s\S]*?mailbox,[\s\S]*?credentialStatuses,[\s\S]*?mailboxHealth,[\s\S]*?\)/,
);
assert.match(
  manageInboxesViewSource,
  /getCredentialAwareManagedInboxStatus\([\s\S]*?mailboxHealthById\[mailbox\.id\]/,
  "the Settings list must use the exact row mailbox health",
);
assert.match(
  manageInboxesViewSource,
  /mailboxHealth=\{[\s\S]*?mailboxHealthById\[selectedInbox\.id\][\s\S]*?\}/,
  "the Settings detail must use the exact selected mailbox health",
);
assert.match(
  settingsViewSource,
  /<ManageInboxesView[\s\S]*?mailboxHealthById=\{mailboxHealthById\}/,
  "Settings must pass the shared session health store to its inbox surface",
);
assert.match(managedInboxEditorSource, /\{managedInboxStatus\.label\}/);
assert.match(manageInboxesViewSource, /\{mailboxStatus\.label\}/);
assert.match(
  workspaceShellSource,
  /reconcileMailboxHealthStore\(mailboxHealthStateById, mailboxHealthSeeds\)/,
  "all same-render surfaces must consume the synchronously reconciled health map",
);

// R-S-T. Healthy configured Gmail keeps a quiet reconnect action; only current
// Action Required health activates the attention treatment. Temporary Issue is
// deliberately absent from that attention derivation.
assert.match(
  gmailReconnectAvailabilitySource,
  /mailbox\.connected && mailbox\.connectionStatus === "connected"/,
  "healthy/configured Gmail must retain Reconnect Gmail",
);
assert.match(
  gmailAttentionSource,
  /mailbox\.provider === "google" &&[\s\S]*?managedInboxStatus\.healthStatus === "action_required"/,
  "current Action Required health must enable Gmail attention",
);
assert.doesNotMatch(
  gmailAttentionSource,
  /temporary_issue|isGmailOAuthReconnectRequired/,
  "Temporary Issue and an older reconnect mirror must not force Gmail attention",
);
assert.match(
  managedInboxEditorSource,
  /\{gmailOAuthReconnectRequired \? \([\s\S]*?Connection needs attention/,
);
assert.match(
  managedInboxEditorSource,
  /\{canReconnectGmail && onReconnectAction \? \([\s\S]*?<DesktopActionButton[\s\S]*?Reconnect Gmail[\s\S]*?<\/DesktopActionButton>/,
  "the healthy/action availability derivation must directly guard the rendered Gmail button",
);

const reconnectGmailButton = desktopActionButtonOpeningTagBefore(
  managedInboxEditorSource,
  "Reconnect Gmail",
);
assert.match(reconnectGmailButton, /onClick=\{onReconnectAction\}/);
assert.match(reconnectGmailButton, /variant="secondary"/);
assert.match(reconnectGmailButton, /size="compact"/);

// U. Definitive custom-IMAP recovery reuses the existing exact mailbox
// Receiving settings route.
const checkConnectionSettingsButton = desktopActionButtonOpeningTagBefore(
  managedInboxEditorSource,
  "Check connection settings",
);
assert.match(checkConnectionSettingsButton, /onClick=\{onReconnectAction\}/);
assert.match(checkConnectionSettingsButton, /variant="secondary"/);
assert.match(checkConnectionSettingsButton, /size="compact"/);
assert.match(
  manageInboxesViewSource,
  /onReconnectAction=\{\(\) => handleReconnectInbox\(selectedInbox\.id\)\}/,
  "the selected editor must bind recovery to its exact mailbox ID",
);
assert.match(
  customImapRecoveryRouteSource,
  /mailbox\?\.provider === "custom_imap"[\s\S]*?setEditingInboxId\(inboxId\)[\s\S]*?setSelectedInboxId\(inboxId\)[\s\S]*?setActiveInboxEditorTab\("Receiving"\)/,
  "the custom-IMAP recovery button must open the existing exact Receiving editor",
);
assert.match(
  manageInboxesViewSource,
  /mailbox\.id === navigationRequest\.mailboxId[\s\S]*?setSelectedInboxId\(navigationRequest\.mailboxId\)[\s\S]*?setActiveInboxEditorTab\("Receiving"\)/,
  "the recovery request must select the exact mailbox and its Receiving tab",
);
assert.match(
  connectionSettingsNavigationSource,
  /setMailboxConnectionNavigationRequest\(\{\s*mailboxId,\s*requestKey: Date\.now\(\),\s*\}\)/,
);
assert.match(connectionSettingsNavigationSource, /setActiveSection\("Settings"\)/);
assert.match(
  workspaceShellSource,
  /onOpenConnectionSettings=\{\(\) =>\s*openMailboxConnectionSettings\(activeMailbox\.id\)\s*\}/,
  "the active header recovery action must preserve the exact mailbox ID",
);
assert.match(
  settingsViewSource,
  /mailboxConnectionNavigationRequest[\s\S]*?setActiveSettingsTab\("Inboxes"\)/,
  "a mailbox recovery request must open the Settings inbox surface",
);
assert.match(
  workspaceShellSource,
  /cancelMailboxHealthCheck\(mailboxHealthOperation\)[\s\S]*?syncingMailboxIdsRef\.current\.delete\(mailboxId\)/,
  "discarded or stale refreshes must restore the preceding health state",
);
assert.match(
  manageInboxesViewSource,
  /mailbox\.provider === "custom_imap"\s*\? onBeginMailboxHealthCheck\(mailbox\.id, "custom_imap"\)/,
  "an IMAP probe attempt must remain behind an existing action-required barrier",
);
assert.match(
  manageInboxesViewSource,
  /didReloadAuthoritativeMailbox[\s\S]*?onCompleteMailboxHealthCheck\(response\.mailboxHealthOperation, \{\s*ok: true,[\s\S]*?onBeginMailboxHealthCheck\(\s*mailboxForStorage\.id,\s*"custom_imap",\s*\{ actionRequiredRecovery: true \},[\s\S]*?provesProviderUsable: false/,
  "only verified saved IMAP credentials may establish a stable Checking recovery checkpoint",
);
assert.match(
  manageInboxesViewSource,
  /onBeginMailboxHealthCheck\(mailboxForConnection\.id, "google", \{\s*actionRequiredRecovery: true,/,
);
assert.match(
  workspaceShellSource,
  /credentialStatus\?\.imapPasswordSet === false[\s\S]*?\? "missing"[\s\S]*?: "not_missing"/,
  "only definitive missing credential evidence may change the health authority identity",
);
assert.doesNotMatch(
  workspaceShellSource,
  /credentialStatus\?\.imapPasswordSet \?\? "unknown"/,
  "credential-present evidence must not invalidate a cold-start provider result",
);
assert.match(
  workspaceShellSource,
  /mailboxIds\.every\([\s\S]*?hasOwnProperty\.call\([\s\S]*?sanitizedStatuses[\s\S]*?setMailboxCredentialStatuses\(sanitizedStatuses\)/,
  "a credential-status outage must preserve the last definitive evidence",
);
assert.doesNotMatch(
  authoritativeMailboxReloadSource,
  /setMailboxCredentialStatuses\(\{\}\)/,
  "an explicit authoritative reload must preserve credential evidence until replacement evidence succeeds",
);

console.log("workspace mailbox health presentation tests passed");
