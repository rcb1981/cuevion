import assert from "node:assert/strict";
import { getPersistedMicrosoftReconnectTarget, hasRecordedMicrosoftOAuthConnection } from "./microsoftReconnectSettings";

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

// A saved OAuth link is a known account state, not an ongoing live Graph test.
const microsoftLinked = { provider: "microsoft", connected: true, connectionStatus: "connected" };
assert.equal(hasRecordedMicrosoftOAuthConnection(microsoftLinked), true);
assert.equal(hasRecordedMicrosoftOAuthConnection({ ...microsoftLinked, connected: false }), false);
assert.equal(hasRecordedMicrosoftOAuthConnection({ ...microsoftLinked, connectionStatus: "connection_failed" }), false);
assert.equal(hasRecordedMicrosoftOAuthConnection({ ...microsoftLinked, connectionStatus: "waiting_for_authentication" }), false);
assert.equal(hasRecordedMicrosoftOAuthConnection({ ...microsoftLinked, provider: "google" }), false);
assert.equal(hasRecordedMicrosoftOAuthConnection({ ...microsoftLinked, provider: "custom_imap" }), false);

console.log("Microsoft Settings reconnect selection and recorded OAuth label: PASS");
