import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { transform } from "sucrase";
import { captureDraftComposition, createComposeSafety, mobileComposeBodyHtml } from "../../lib/composeSafety";
import { sendGmailMessage } from "../../lib/inboxConnectionApi";
import { EMAIL_REQUEST_TOO_LARGE_MESSAGE } from "../../lib/emailRequestSize";

const source = readFileSync(resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"), "utf8");
// Execute the actual compose callback with mocked dependencies; no mounted app,
// accounts, browser storage, or provider transports are involved.
const begin = source.indexOf("  const sendMessage = async (options?:");
const finish = source.indexOf("  const composeSendFeedback =", begin);
assert(begin > 0 && finish > begin);
const callback = transform(source.slice(begin, finish), { transforms: ["typescript"] }).code;
const createSend = new Function("scope", `with (scope) { ${callback}; return sendMessage; }`);

function harness(response: unknown) {
  const effects: Array<[string, unknown]> = [];
  const capture = (name: string) => (value?: unknown) => effects.push([name, value]);
  const compose = {
    composeTo: "to@example.com", composeCc: "cc@example.com", composeBcc: "hidden@example.com",
    composeSubject: "Subject", composeBody: "<p>Body and signature</p>",
    composeAttachments: [{ id: "attachment", name: "sample.txt", contentBase64: "c2FtcGxl" }],
    composeMode: "new", composeSourceMessage: null, composeReplyAnchorMessage: null,
    composePresentation: "workspace", composeMailboxId: "mailbox",
  };
  const requests: unknown[] = [];
  const scope: any = {
    ...compose, composeSendAttemptRef: { current: "idle" },
    composeSafety: createComposeSafety({
      owner: { userId: "owner", workspaceId: "workspace" }, epoch: "session", persistent: false,
      signal: new AbortController().signal, isActive: () => true,
    }, () => scope.connectionIdentity), connectionIdentity: "connection",
    activeComposeMailbox: { id: "mailbox", email: "sender@example.com" },
    managedInboxes: [{ id: "mailbox", provider: "google" }], credentialStatuses: {},
    canSendFromManagedMailbox: () => true,
    getCurrentComposeBodyHtml: () => compose.composeBody,
    buildImapReplyContext: () => undefined, buildGmailReplyContext: () => undefined,
    serializeComposeAttachment: async (value: unknown) => value,
    extractComposePlainText: () => "Body and signature",
    extractComposeParagraphs: () => ["Body and signature"],
    sendGmailMessage: async (request: unknown) => { requests.push(request); return response; },
    setComposeSendError: capture("error"), setComposeSendUncertain: capture("uncertain"),
    setIsSendingCompose: capture("sending"), rememberSentRecipients: capture("remember"),
    resolveDesktopThreadTimestamp: () => ({ label: "Now" }), isValidGmailProviderIdentifier: () => false,
    normalizeMailMessage: (value: unknown) => value, senderCategoryLearning: {},
    messageOwnershipInteractions: {}, currentUserId: "synthetic-user", mailboxStore: {},
    setMailboxStore: capture("sentProjection"), setActiveFolder: capture("folder"),
    setIsFullMessageOpen: capture("fullMessage"), setIsComposeOpen: capture("composeOpen"),
    setComposePresentation: capture("presentation"), setModalComposeSourceSelection: capture("selection"),
    resetComposeState: capture("reset"),
  };
  scope.captureCurrentComposition = (bodyHtml: string) => captureDraftComposition(
    scope.composeSafety.identity(), scope.composeSafety.scope("mailbox"), {
      to: compose.composeTo, cc: compose.composeCc, bcc: compose.composeBcc, subject: compose.composeSubject,
      bodyHtml, bodyText: "Body and signature", mobileBodyText: "", baseBodyHtml: bodyHtml,
      signatureSelection: "none", signature: null, mode: "new", source: null, replyAnchor: null,
      attachments: compose.composeAttachments,
    },
  );
  scope.resetComposeState = () => { scope.composeSafety.edit(); capture("reset")(); capture("sending")(false); };
  return { scope, compose, effects, requests, send: createSend(scope) };
}

async function run() {
  const uncertain = harness({ ok: false, sendOutcome: "uncertain" });
  const original = JSON.stringify(uncertain.compose);
  let resolveSend!: (value: unknown) => void;
  uncertain.scope.sendGmailMessage = (request: unknown) => {
    uncertain.requests.push(request);
    return new Promise(resolve => { resolveSend = resolve; });
  };
  const first = uncertain.send();
  await uncertain.send(); // Same render, before attachment serialization finishes.
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(uncertain.requests.length, 1);
  await uncertain.send(); // Response still pending.
  assert.equal(uncertain.requests.length, 1);
  resolveSend({ ok: false, sendOutcome: "uncertain" });
  assert.equal(await first, undefined);
  assert.equal(uncertain.scope.composeSendAttemptRef.current, "uncertain");
  assert.deepEqual(uncertain.effects, [["error", null], ["sending", true], ["uncertain", true], ["sending", false]]);
  assert.equal(JSON.stringify(uncertain.compose), original);
  assert.equal((uncertain.requests[0] as any).bcc, "hidden@example.com");
  assert.deepEqual((uncertain.requests[0] as any).attachments, uncertain.compose.composeAttachments);
  await uncertain.send();
  assert.equal(uncertain.requests.length, 1, "Uncertainty must not allow another send before acknowledgement");

  // Execute the actual acknowledgement callback. It unlocks but cannot send.
  const notice = source.slice(finish, source.indexOf("  const closeMenus =", finish));
  const acknowledgement = notice.slice(notice.indexOf("onClick={() => {") + "onClick={() => {".length, notice.indexOf("\n        }}"));
  new Function("scope", `with(scope) { ${acknowledgement} }`)(uncertain.scope);
  assert.equal(uncertain.scope.composeSendAttemptRef.current, "idle");
  assert.equal(uncertain.requests.length, 1);

  const rejected = harness({ ok: false, sendOutcome: "not_sent", error: { message: "Recipient rejected." } });
  await rejected.send();
  assert.equal(rejected.scope.composeSendAttemptRef.current, "idle");
  assert.deepEqual(rejected.effects, [["error", null], ["sending", true], ["error", "Recipient rejected."], ["sending", false]]);
  assert.equal(rejected.effects.some(([name]) => name === "reset"), false);

  const accepted = harness({ ok: true });
  assert.equal(await accepted.send(), "sent");
  assert.equal(accepted.requests.length, 1);
  assert.equal(accepted.effects.filter(([name]) => name === "sentProjection").length, 1);
  assert.equal(accepted.effects.filter(([name]) => name === "reset").length, 1);
  assert(accepted.effects.some(([name, value]) => name === "composeOpen" && value === false));
  assert.equal(accepted.scope.composeSendAttemptRef.current, "idle");

  const preparation = harness({ ok: true });
  preparation.scope.serializeComposeAttachment = async () => { throw new Error("Attachment unavailable."); };
  await preparation.send();
  assert.equal(preparation.requests.length, 0);
  assert.equal(preparation.scope.composeSendAttemptRef.current, "idle");
  assert(preparation.effects.some(([name, value]) => name === "error" && value === "Attachment unavailable."));

  // Exercise the actual API preflight from the real compose callback. The
  // selection, including Bcc/signature, must survive without a fetch or reset.
  for (const provider of ["google", "custom_imap"]) {
    const oversized = harness({ ok: true });
    oversized.scope.managedInboxes[0].provider = provider;
    oversized.compose.composeAttachments[0].contentBase64 = "eHh4".repeat(1_000_000);
    oversized.scope.sendGmailMessage = sendGmailMessage;
    const before = JSON.stringify(oversized.compose);
    const originalFetch = globalThis.fetch;
    let calls = 0;
    globalThis.fetch = (async () => { calls += 1; throw Error("Network forbidden"); }) as typeof fetch;
    try { await oversized.send(); } finally { globalThis.fetch = originalFetch; }
    assert.equal(calls, 0);
    assert.equal(JSON.stringify(oversized.compose), before);
    assert.equal(oversized.scope.composeSendAttemptRef.current, "idle");
    assert.deepEqual(oversized.effects, [["error", null], ["sending", true], ["error", EMAIL_REQUEST_TOO_LARGE_MESSAGE], ["sending", false]]);
  }

  assert.equal((source.match(/disabled=\{isSendingCompose \|\| composeSendUncertain\}/g) ?? []).length, 3);
  assert.equal((source.match(/\{composeSendFeedback\}/g) ?? []).length, 3);
  const mobile = source.slice(source.indexOf("  const handleMobileComposeSend ="), source.indexOf("  const composeCloseConfirmation ="));
  let mobileCloses = 0;
  const mobileScope = { mobileReplyBodyText: "Mobile body", composeBody: "<p>Signature and quote</p>",
    mailbox: { id: "mailbox" }, mobileComposeBodyHtml, sendMessage: async () => undefined,
    onMobileComposeSent: () => { mobileCloses += 1; }, onComposeOpenChange: () => { mobileCloses += 1; } };
  const mobileFn = new Function("scope", `with(scope) { ${transform(mobile, { transforms: ["typescript"] }).code}; return handleMobileComposeSend; }`)(mobileScope);
  await mobileFn();
  assert.equal(mobileCloses, 0);
  assert.equal(mobileScope.mobileReplyBodyText, "Mobile body");
  for (const response of [{ ok: true }, { ok: false, sendOutcome: "uncertain" }, { ok: false, error: { message: "old failure" } }]) {
    const stale = harness(response);
    let finish!: (value: unknown) => void;
    stale.scope.sendGmailMessage = () => new Promise(resolve => { finish = resolve; });
    const task = stale.send();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(stale.scope.composeSafety.edit(), false, "Pending operation locks editor changes");
    stale.scope.composeSafety.invalidate();
    stale.scope.composeSafety.activate();
    stale.scope.composeSafety.startComposition();
    const effectsBeforeResponse = [...stale.effects];
    finish(response);
    await task;
    assert.deepEqual(stale.effects, effectsBeforeResponse, "Old result/finally cannot touch the new composition");
  }
  const reconnected = harness({ ok: true });
  let finishReconnected!: (value: unknown) => void;
  reconnected.scope.sendGmailMessage = () => new Promise(resolve => { finishReconnected = resolve; });
  const reconnectTask = reconnected.send();
  await new Promise(resolve => setImmediate(resolve));
  reconnected.scope.connectionIdentity = "new-connection";
  finishReconnected({ ok: true });
  await reconnectTask;
  assert.equal(reconnected.scope.composeSafety.isPending(), false, "A connection change cannot leave the editor permanently locked");
  assert.equal(reconnected.scope.composeSendAttemptRef.current, "uncertain");
  assert.equal(reconnected.effects.some(([name]) => name === "reset" || name === "sentProjection"), false);
  assert(reconnected.effects.some(([name, value]) => name === "uncertain" && value === true));

  const changedBeforeSend = harness({ ok: true });
  let prepared!: (value: unknown) => void;
  changedBeforeSend.scope.serializeComposeAttachment = () => new Promise(resolve => { prepared = resolve; });
  const prepareTask = changedBeforeSend.send();
  changedBeforeSend.scope.connectionIdentity = "new-connection";
  prepared({ name: "file" });
  await prepareTask;
  assert.equal(changedBeforeSend.requests.length, 0);
  assert.equal(changedBeforeSend.scope.composeSafety.isPending(), false);
  assert.equal(changedBeforeSend.scope.composeSendAttemptRef.current, "idle");
  assert(changedBeforeSend.effects.some(([name, value]) => name === "error" && String(value).includes("connection changed before sending")));
  const revokedDuringAttachments = harness({ ok: true });
  let finishAttachment!: (value: unknown) => void;
  revokedDuringAttachments.scope.serializeComposeAttachment = () => new Promise(resolve => { finishAttachment = resolve; });
  const task = revokedDuringAttachments.send();
  revokedDuringAttachments.scope.composeSafety.invalidate();
  finishAttachment({ name: "sample.txt" });
  await task;
  assert.equal(revokedDuringAttachments.requests.length, 0, "Session revocation before attachment completion prevents sending");

  console.log("Compose send outcomes, preservation, mobile and repeated interaction checks passed");
}
void run().catch(error => { console.error(error); process.exitCode = 1; });
