import assert from "node:assert/strict";
import {
  beginMailboxHealthOperation,
  cancelMailboxHealthOperation,
  completeMailboxHealthOperation,
  createInitialMailboxHealthStore,
  createMailboxHealthOperationClock,
  getMailboxHealthPresentation,
  reconcileMailboxHealthStore,
  type MailboxHealthBeginOptions,
  type MailboxHealthOperation,
  type MailboxHealthProvider,
  type MailboxHealthStore,
} from "./mailboxProviderHealth";

function begin(
  store: MailboxHealthStore,
  clock: ReturnType<typeof createMailboxHealthOperationClock>,
  mailboxId: string,
  provider: MailboxHealthProvider,
  options?: MailboxHealthBeginOptions,
): [MailboxHealthStore, MailboxHealthOperation] {
  const operation = clock.begin(mailboxId, provider, options);
  return [beginMailboxHealthOperation(store, operation), operation];
}

function complete(
  store: MailboxHealthStore,
  operation: MailboxHealthOperation,
  ok: boolean,
  errorCode?: string,
): MailboxHealthStore {
  return completeMailboxHealthOperation(store, operation, {
    ok,
    errorCode,
    completedAt: `2026-08-24T00:00:${String(operation.token).padStart(2, "0")}.000Z`,
  });
}

const clock = createMailboxHealthOperationClock();

// A. Configured cold mailboxes are checking, never green from config alone.
let store = createInitialMailboxHealthStore([
  { mailboxId: "gmail-a", provider: "google" },
  { mailboxId: "imap-a", provider: "custom_imap" },
]);
assert.equal(store["gmail-a"].status, "checking");
assert.equal(store["imap-a"].status, "checking");
assert.ok(Object.isFrozen(store));
assert.ok(Object.isFrozen(store["gmail-a"]));

// B. A successful authenticated Gmail provider operation proves Connected.
let gmailOperation: MailboxHealthOperation;
[store, gmailOperation] = begin(store, clock, "gmail-a", "google");
const gmailCheckingStore = store;
store = complete(store, gmailOperation, true);
assert.equal(store["gmail-a"].status, "connected");
assert.equal(gmailCheckingStore["gmail-a"].status, "checking");

// C. A successful authenticated custom-IMAP operation proves Connected.
let imapOperation: MailboxHealthOperation;
[store, imapOperation] = begin(store, clock, "imap-a", "custom_imap");
store = complete(store, imapOperation, true);
assert.equal(store["imap-a"].status, "connected");

// D. Current Gmail retryable provider/store/rate-limit failures remain amber.
for (const code of [
  "gmail_token_store_unavailable",
  "gmail_refresh_not_configured",
  "gmail_refresh_rate_limited",
  "gmail_refresh_unavailable",
  "gmail_refresh_conflict",
  "gmail_rate_limited",
  "gmail_unavailable",
  "gmail_response_invalid",
  "gmail_response_too_large",
  "gmail_permission_denied",
  "gmail_fetch_failed",
] as const) {
  [store, gmailOperation] = begin(store, clock, "gmail-a", "google");
  store = complete(store, gmailOperation, false, code);
  assert.equal(store["gmail-a"].status, "temporary_issue", code);
}

// E. Only definitive Gmail authorization evidence becomes Action Required.
for (const code of [
  "reconnect_required",
  "gmail_connection_not_ready",
  "gmail_authorization_revoked",
  "invalid_grant",
  "gmail_refresh_invalid_grant",
  "gmail_refresh_token_missing",
  "gmail_token_record_malformed",
  "gmail_provider_credential_mismatch",
] as const) {
  [store, gmailOperation] = begin(store, clock, "gmail-a", "google");
  store = complete(store, gmailOperation, false, code);
  assert.equal(store["gmail-a"].status, "action_required", code);
}

// F. A temporary Gmail failure is cleared automatically by a later success.
[store, gmailOperation] = begin(store, clock, "gmail-a", "google", {
  actionRequiredRecovery: true,
});
store = complete(store, gmailOperation, false, "gmail_refresh_unavailable");
assert.equal(store["gmail-a"].status, "temporary_issue");
[store, gmailOperation] = begin(store, clock, "gmail-a", "google");
store = complete(store, gmailOperation, true);
assert.equal(store["gmail-a"].status, "connected");

// G. An explicit reconnect from healthy starts at Checking. A retryable first
// sync stays amber and a later ordinary provider success recovers without OAuth.
assert.equal(store["gmail-a"].status, "connected");
[store, gmailOperation] = begin(store, clock, "gmail-a", "google", {
  actionRequiredRecovery: true,
});
assert.equal(store["gmail-a"].status, "checking");
store = complete(store, gmailOperation, false, "gmail_token_store_unavailable");
assert.equal(store["gmail-a"].status, "temporary_issue");
[store, gmailOperation] = begin(store, clock, "gmail-a", "google");
store = complete(store, gmailOperation, true);
assert.equal(store["gmail-a"].status, "connected");

// Action-required recovery also remains Checking until provider use succeeds.
[store, gmailOperation] = begin(store, clock, "gmail-a", "google");
store = complete(store, gmailOperation, false, "reconnect_required");
assert.equal(store["gmail-a"].status, "action_required");
[store, gmailOperation] = begin(store, clock, "gmail-a", "google", {
  actionRequiredRecovery: true,
});
assert.equal(store["gmail-a"].status, "checking");
store = complete(store, gmailOperation, true);
assert.equal(store["gmail-a"].status, "connected");

// Routine probes cannot clear a definitive action condition. Only an explicit
// recovery generation may move red to Checking and authorize later recovery.
[store, gmailOperation] = begin(store, clock, "gmail-a", "google");
store = complete(store, gmailOperation, false, "reconnect_required");
const actionRequiredBeforeRoutineProbe = store;
[store, gmailOperation] = begin(store, clock, "gmail-a", "google");
assert.equal(store["gmail-a"].status, "action_required");
store = complete(store, gmailOperation, true);
assert.equal(store["gmail-a"].status, "action_required");
assert.equal(
  store["gmail-a"].errorCode,
  actionRequiredBeforeRoutineProbe["gmail-a"].errorCode,
);
[store, gmailOperation] = begin(store, clock, "gmail-a", "google", {
  actionRequiredRecovery: true,
});
assert.equal(store["gmail-a"].status, "checking");
store = complete(store, gmailOperation, false, "gmail_refresh_unavailable");
assert.equal(store["gmail-a"].status, "temporary_issue");
[store, gmailOperation] = begin(store, clock, "gmail-a", "google");
store = complete(store, gmailOperation, true);
assert.equal(store["gmail-a"].status, "connected");

// H. Custom-IMAP network/provider failures remain Temporary Issue.
[store, imapOperation] = begin(store, clock, "imap-a", "custom_imap");
store = complete(store, imapOperation, false, "network_timeout");
assert.equal(store["imap-a"].status, "temporary_issue");

// I. Definitive missing stored credentials/config corruption require action.
for (const code of [
  "reconnect_required",
  "imap_host_invalid",
  "imap_credentials_missing",
  "imap_credentials_unavailable",
  "mailbox_credential_generation_invalid",
  "mailbox_configuration_malformed",
  "mailbox_secret_malformed",
] as const) {
  [store, imapOperation] = begin(store, clock, "imap-a", "custom_imap", {
    actionRequiredRecovery: true,
  });
  store = complete(store, imapOperation, false, code);
  assert.equal(store["imap-a"].status, "action_required", code);
}

// J. Unproven IMAP authentication and unknown failures fail safe to amber.
for (const code of [
  "invalid_credentials",
  "imap_connection_failed",
  "connection_failed",
  "unexpected_provider_detail",
] as const) {
  [store, imapOperation] = begin(store, clock, "imap-a", "custom_imap", {
    actionRequiredRecovery: true,
  });
  store = complete(store, imapOperation, false, code);
  assert.equal(store["imap-a"].status, "temporary_issue", code);
}

// K. Each exact mailbox ID owns an isolated record.
const isolatedClock = createMailboxHealthOperationClock();
let isolatedStore = createInitialMailboxHealthStore([
  { mailboxId: "gmail-k", provider: "google" },
  { mailboxId: "imap-k", provider: "custom_imap" },
]);
let isolatedOperation: MailboxHealthOperation;
[isolatedStore, isolatedOperation] = begin(
  isolatedStore,
  isolatedClock,
  "gmail-k",
  "google",
);
isolatedStore = complete(
  isolatedStore,
  isolatedOperation,
  false,
  "reconnect_required",
);
assert.equal(isolatedStore["gmail-k"].status, "action_required");
assert.equal(isolatedStore["imap-k"].status, "checking");

// L. An older failure cannot overwrite a newer successful provider result.
const staleFailureClock = createMailboxHealthOperationClock();
let staleFailureStore = createInitialMailboxHealthStore([
  { mailboxId: "gmail-l", provider: "google" },
]);
const staleFailure = staleFailureClock.begin("gmail-l", "google");
staleFailureStore = beginMailboxHealthOperation(staleFailureStore, staleFailure);
const newerSuccess = staleFailureClock.begin("gmail-l", "google");
staleFailureStore = beginMailboxHealthOperation(staleFailureStore, newerSuccess);
staleFailureStore = complete(staleFailureStore, newerSuccess, true);
const connectedAfterNewerSuccess = staleFailureStore;
staleFailureStore = complete(
  staleFailureStore,
  staleFailure,
  false,
  "reconnect_required",
);
assert.strictEqual(staleFailureStore, connectedAfterNewerSuccess);
assert.equal(staleFailureStore["gmail-l"].status, "connected");

// M. An older success cannot erase a newer definitive provider failure.
const staleSuccessClock = createMailboxHealthOperationClock();
let staleSuccessStore = createInitialMailboxHealthStore([
  { mailboxId: "gmail-m", provider: "google" },
]);
const staleSuccess = staleSuccessClock.begin("gmail-m", "google");
staleSuccessStore = beginMailboxHealthOperation(staleSuccessStore, staleSuccess);
const newerFailure = staleSuccessClock.begin("gmail-m", "google");
staleSuccessStore = beginMailboxHealthOperation(staleSuccessStore, newerFailure);
staleSuccessStore = complete(
  staleSuccessStore,
  newerFailure,
  false,
  "reconnect_required",
);
const actionRequiredAfterNewerFailure = staleSuccessStore;
staleSuccessStore = complete(staleSuccessStore, staleSuccess, true);
assert.strictEqual(staleSuccessStore, actionRequiredAfterNewerFailure);
assert.equal(staleSuccessStore["gmail-m"].status, "action_required");

// A discarded current operation restores the preceding authoritative state
// and remains fenced against any later completion from that request.
const cancelledClock = createMailboxHealthOperationClock();
let cancelledStore = createInitialMailboxHealthStore([
  { mailboxId: "gmail-cancel", provider: "google" },
]);
let cancelledOperation: MailboxHealthOperation;
[cancelledStore, cancelledOperation] = begin(
  cancelledStore,
  cancelledClock,
  "gmail-cancel",
  "google",
);
cancelledStore = complete(cancelledStore, cancelledOperation, true);
[cancelledStore, cancelledOperation] = begin(
  cancelledStore,
  cancelledClock,
  "gmail-cancel",
  "google",
);
assert.equal(cancelledStore["gmail-cancel"].status, "checking");
cancelledStore = cancelMailboxHealthOperation(
  cancelledStore,
  cancelledOperation,
);
assert.equal(cancelledStore["gmail-cancel"].status, "connected");
assert.equal(cancelledStore["gmail-cancel"].operationPending, false);
const restoredAfterCancellation = cancelledStore;
cancelledStore = complete(
  cancelledStore,
  cancelledOperation,
  false,
  "reconnect_required",
);
assert.strictEqual(cancelledStore, restoredAfterCancellation);

// Configuration reconciliation prunes missing IDs, resets a provider/config
// identity change to Checking, and keeps every produced record/store frozen.
const reconciled = reconcileMailboxHealthStore(cancelledStore, [
  {
    mailboxId: "gmail-cancel",
    provider: "custom_imap",
    authorityKey: "custom-imap-generation-2",
  },
]);
assert.deepEqual(Object.keys(reconciled), ["gmail-cancel"]);
assert.equal(reconciled["gmail-cancel"].provider, "custom_imap");
assert.equal(reconciled["gmail-cancel"].status, "checking");
assert.ok(Object.isFrozen(reconciled));
assert.ok(Object.isFrozen(reconciled["gmail-cancel"]));
const pruned = reconcileMailboxHealthStore(reconciled, []);
assert.deepEqual(pruned, {});
assert.ok(Object.isFrozen(pruned));

// A same-generation stored reconnect mirror cannot reinstate red after an
// explicit recovery has yielded a newer temporary provider result.
const recoverySeed = [{
  mailboxId: "gmail-recovery-seed",
  provider: "google" as const,
  authorityKey: "gmail-config-generation",
  status: "action_required" as const,
}];
let recoverySeedStore = createInitialMailboxHealthStore(recoverySeed);
let recoverySeedOperation: MailboxHealthOperation;
[recoverySeedStore, recoverySeedOperation] = begin(
  recoverySeedStore,
  createMailboxHealthOperationClock(),
  "gmail-recovery-seed",
  "google",
  { actionRequiredRecovery: true },
);
recoverySeedStore = complete(
  recoverySeedStore,
  recoverySeedOperation,
  false,
  "gmail_refresh_unavailable",
);
assert.equal(recoverySeedStore["gmail-recovery-seed"].status, "temporary_issue");
recoverySeedStore = reconcileMailboxHealthStore(
  recoverySeedStore,
  recoverySeed,
);
assert.equal(recoverySeedStore["gmail-recovery-seed"].status, "temporary_issue");
recoverySeedStore = reconcileMailboxHealthStore(recoverySeedStore, [
  {
    ...recoverySeed[0],
    authorityKey: "gmail-config-generation-with-new-credential-evidence",
  },
]);
assert.equal(recoverySeedStore["gmail-recovery-seed"].status, "action_required");

// A verified recovery checkpoint is stable Checking, not a fake success. If a
// later provider result is discarded, cancellation returns to that checkpoint.
const checkpointClock = createMailboxHealthOperationClock();
let checkpointStore = createInitialMailboxHealthStore([
  {
    mailboxId: "imap-checkpoint",
    provider: "custom_imap",
    status: "action_required",
  },
]);
let checkpointOperation: MailboxHealthOperation;
[checkpointStore, checkpointOperation] = begin(
  checkpointStore,
  checkpointClock,
  "imap-checkpoint",
  "custom_imap",
  { actionRequiredRecovery: true },
);
checkpointStore = completeMailboxHealthOperation(
  checkpointStore,
  checkpointOperation,
  {
    ok: true,
    provesProviderUsable: false,
    completedAt: "2026-08-24T01:00:00.000Z",
  },
);
assert.equal(checkpointStore["imap-checkpoint"].status, "checking");
assert.equal(checkpointStore["imap-checkpoint"].operationPending, false);
[checkpointStore, checkpointOperation] = begin(
  checkpointStore,
  checkpointClock,
  "imap-checkpoint",
  "custom_imap",
);
checkpointStore = cancelMailboxHealthOperation(
  checkpointStore,
  checkpointOperation,
);
assert.equal(checkpointStore["imap-checkpoint"].status, "checking");
[checkpointStore, checkpointOperation] = begin(
  checkpointStore,
  checkpointClock,
  "imap-checkpoint",
  "custom_imap",
);
checkpointStore = complete(checkpointStore, checkpointOperation, true);
assert.equal(checkpointStore["imap-checkpoint"].status, "connected");

// Credential lookup may resolve unknown to present while the cold-start
// provider operation is in flight. Equivalent non-missing authority must keep
// that operation current; only definitive missing evidence starts a new
// authority generation.
const coldStartAuthorityClock = createMailboxHealthOperationClock();
const availableImapAuthority = "imap-config:credentials-not-missing";
let coldStartAuthorityStore = createInitialMailboxHealthStore([
  {
    mailboxId: "imap-cold-authority",
    provider: "custom_imap",
    authorityKey: availableImapAuthority,
  },
]);
let coldStartAuthorityOperation: MailboxHealthOperation;
[coldStartAuthorityStore, coldStartAuthorityOperation] = begin(
  coldStartAuthorityStore,
  coldStartAuthorityClock,
  "imap-cold-authority",
  "custom_imap",
);
const storeBeforeCredentialPresenceArrives = coldStartAuthorityStore;
coldStartAuthorityStore = reconcileMailboxHealthStore(
  coldStartAuthorityStore,
  [
    {
      mailboxId: "imap-cold-authority",
      provider: "custom_imap",
      authorityKey: availableImapAuthority,
    },
  ],
);
assert.strictEqual(
  coldStartAuthorityStore,
  storeBeforeCredentialPresenceArrives,
);
coldStartAuthorityStore = complete(
  coldStartAuthorityStore,
  coldStartAuthorityOperation,
  true,
);
assert.equal(coldStartAuthorityStore["imap-cold-authority"].status, "connected");
coldStartAuthorityStore = reconcileMailboxHealthStore(
  coldStartAuthorityStore,
  [
    {
      mailboxId: "imap-cold-authority",
      provider: "custom_imap",
      authorityKey: "imap-config:credentials-missing",
      status: "action_required",
    },
  ],
);
assert.equal(
  coldStartAuthorityStore["imap-cold-authority"].status,
  "action_required",
);

assert.deepEqual(getMailboxHealthPresentation("checking"), {
  label: "CHECKING",
  tone: "neutral",
  description: "Mailbox provider access is being checked.",
});
assert.equal(getMailboxHealthPresentation("connected").label, "CONNECTED");
assert.equal(
  getMailboxHealthPresentation("temporary_issue").label,
  "TEMPORARY ISSUE",
);
assert.equal(
  getMailboxHealthPresentation("action_required").label,
  "ACTION REQUIRED",
);

console.log("mailbox provider health tests passed");
