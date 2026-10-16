import assert from "node:assert/strict";
import { getPersistedMicrosoftReconnectTarget } from "./microsoftReconnectSettings";

const saved = [
  { id: "microsoft-0123456789abcdefabcd12345678", provider: "microsoft", email: "owner@example.com" },
  { id: "google-01", provider: "google", email: "other@example.com" },
];

const microsoft = saved[0];
assert.deepEqual(
  getPersistedMicrosoftReconnectTarget(saved, microsoft),
  { id: microsoft.id, email: microsoft.email },
);
assert.deepEqual(
  getPersistedMicrosoftReconnectTarget(
    [{ ...microsoft, email: " Owner@Example.com " }],
    { ...microsoft, email: "owner@example.com" },
  ),
  { id: microsoft.id, email: "Owner@Example.com" },
);
assert.equal(getPersistedMicrosoftReconnectTarget(saved, null), null);
assert.equal(getPersistedMicrosoftReconnectTarget(saved, saved[1]), null);
assert.equal(
  getPersistedMicrosoftReconnectTarget(saved, { ...microsoft, email: "attacker@example.com" }),
  null, "A modified draft email cannot authorize reconnection",
);
assert.equal(
  getPersistedMicrosoftReconnectTarget(saved, { ...microsoft, provider: "google" }),
  null,
);
assert.equal(
  getPersistedMicrosoftReconnectTarget(saved, { ...microsoft, id: "draft-123" }),
  null, "Unsaved mailbox IDs must not authorize reconnection",
);
assert.equal(
  getPersistedMicrosoftReconnectTarget([{ ...microsoft }, { ...microsoft }], microsoft),
  null, "Duplicate exact saved mailbox IDs fail closed",
);
assert.equal(
  getPersistedMicrosoftReconnectTarget([{ ...microsoft, email: "" }], microsoft),
  null,
);
// A persisted mailbox remains eligible even when its connection is unhealthy.
assert.deepEqual(
  getPersistedMicrosoftReconnectTarget([{ ...microsoft }], { ...microsoft }),
  { id: microsoft.id, email: microsoft.email },
);

console.log("Microsoft Settings reconnect selection: exact saved owner mailbox, drafts rejected PASS");
