import assert from "node:assert/strict";
import "sucrase/register/tsx.js";

const {
  normalizeCuevionInternalClassification,
  normalizeMailMessage,
  resolveVisibleCategoryLabelForMessageInContext,
} = require("./WorkspaceShell.tsx") as typeof import("./WorkspaceShell");

type MessageSeed = Parameters<typeof normalizeMailMessage>[0];
type ProviderIdentity = Pick<
  MessageSeed,
  | "providerMessageId"
  | "providerThreadId"
  | "imapUid"
  | "uidValidity"
>;

const gmailIdentity: ProviderIdentity = {
  providerMessageId: "gmail-message-42",
  providerThreadId: "gmail-thread-9",
};
const customImapIdentity: ProviderIdentity = {
  imapUid: "42",
  uidValidity: "9001",
};

function projectMessage(
  rawInternalClassification: unknown,
  providerIdentity: ProviderIdentity = gmailIdentity,
  overrides: Partial<MessageSeed> = {},
) {
  const internalClassificationSeed =
    rawInternalClassification === undefined
      ? {}
      : {
          internalClassification: normalizeCuevionInternalClassification(
            rawInternalClassification,
          ),
        };
  const message = normalizeMailMessage(
    {
      id: "canonical-message",
      sender: "Canonical Sender",
      subject: "Canonical message",
      snippet: "Canonical message body",
      time: "10:00",
      ui_signal: "UPDATE",
      from: "sender@example.test",
      to: "recipient@example.test",
      timestamp: "2026-08-04T08:00:00.000Z",
      body: ["Canonical message body"],
      final_visibility: "show_low",
      action: "show_in_quiet_view",
      ...providerIdentity,
      ...overrides,
      ...internalClassificationSeed,
    },
    "main",
    {},
    {},
    "user-1",
  );

  return {
    message,
    visibleCategoryLabel: resolveVisibleCategoryLabelForMessageInContext(
      message,
      false,
    ),
  };
}

for (const classification of [
  "labelradar_update",
  "trackstack_submission",
] as const) {
  const gmail = projectMessage(classification, gmailIdentity);
  const customImap = projectMessage(classification, customImapIdentity);

  assert.equal(gmail.message.internalClassification, classification);
  assert.equal(gmail.message.category, "Updates");
  assert.equal(gmail.visibleCategoryLabel, "Update");
  assert.equal(gmail.message.priorityScore, "low");
  assert.equal(gmail.message.final_visibility, "show_low");
  assert.equal(gmail.message.action, "show_in_quiet_view");

  assert.equal(customImap.message.internalClassification, classification);
  assert.equal(customImap.message.category, "Updates");
  assert.equal(customImap.visibleCategoryLabel, "Update");

  assert.deepEqual(
    {
      internalClassification: gmail.message.internalClassification,
      category: gmail.message.category,
      visibleCategoryLabel: gmail.visibleCategoryLabel,
      priorityScore: gmail.message.priorityScore,
      final_visibility: gmail.message.final_visibility,
      action: gmail.message.action,
    },
    {
      internalClassification: customImap.message.internalClassification,
      category: customImap.message.category,
      visibleCategoryLabel: customImap.visibleCategoryLabel,
      priorityScore: customImap.message.priorityScore,
      final_visibility: customImap.message.final_visibility,
      action: customImap.message.action,
    },
  );
}

const malformedClassifications = [
  ["missing", undefined],
  ["empty", ""],
  ["null", null],
  ["unsupported", "unsupported_future_value"],
] as const;

for (const [name, rawInternalClassification] of malformedClassifications) {
  const { message, visibleCategoryLabel } = projectMessage(
    rawInternalClassification,
  );

  assert.notEqual(message.category, undefined, name);
  assert.equal(message.category, "Primary", name);
  assert.equal(visibleCategoryLabel, "Update", name);
}

const heuristicFallback = projectMessage("unsupported_future_value", gmailIdentity, {
  signal: "Update",
  ui_signal: undefined,
});
assert.equal(heuristicFallback.message.internalClassification, "workflow_update");
assert.equal(heuristicFallback.message.category, "Updates");
assert.equal(heuristicFallback.visibleCategoryLabel, "Update");

const supportedRegressions = [
  ["workflow_update", "UPDATE", "Updates", "Update"],
  ["distributor_update", "UPDATE", "Updates", "Update"],
  ["promo", "PROMO", "Promo", "Promo"],
  ["finance", "FINANCE", "Updates", "Finance"],
  ["reply", "REPLY", "Primary", "Reply"],
  ["demo", "DEMO", "Primary", "Demo"],
  ["unknown", "NEW", "Primary", "Other"],
] as const;

for (const [classification, uiSignal, category, visibleCategoryLabel] of supportedRegressions) {
  const projected = projectMessage(classification, gmailIdentity, {
    ui_signal: uiSignal,
  });

  assert.equal(projected.message.internalClassification, classification);
  assert.equal(projected.message.category, category);
  assert.equal(projected.visibleCategoryLabel, visibleCategoryLabel);
}

console.log("✓ WorkspaceShell classification contract");
