import assert from "node:assert/strict";
import { getMicrosoftConversationRows } from "./microsoftConversationRows";
import type { LiveInboxMessageSnapshot } from "../../lib/inboxConnectionApi";

const row = (id: string, thread: string | undefined, time: string, extras: Record<string, unknown> = {}) => ({
  providerMessageId: id, providerThreadId: thread, serverMailboxId: "microsoft-owner-1",
  providerFolder: "INBOX", createdAt: time, sender: "Artist",
  subject: "Original topic", snippet: "A preview",
  ...extras,
}) as LiveInboxMessageSnapshot;

const records = [
  row("m2", "trusted-conv", "2026-10-20T12:00:00Z"),
  row("m1", "trusted-conv", "2026-10-19T12:00:00Z"),
  row("other-thread", "other", "2026-10-20T13:00:00Z"),
  row("other-mailbox", "trusted-conv", "2026-10-20T14:00:00Z", {serverMailboxId:"microsoft-owner-2"}),
  row("other-folder", "trusted-conv", "2026-10-20T15:00:00Z", {providerFolder:"SENT"}),
  row("m2", "trusted-conv", "2026-10-20T12:00:00Z"),
];
assert.deepEqual(getMicrosoftConversationRows(records, "m1", "microsoft-owner-1").map(x => x.providerMessageId), ["m1","m2"]);
assert.deepEqual(getMicrosoftConversationRows(records, "other-mailbox", "microsoft-owner-1"), []);
assert.deepEqual(getMicrosoftConversationRows(records, "not-listed", "microsoft-owner-1"), []);
assert.deepEqual(getMicrosoftConversationRows([row("no-thread",undefined,"2026-10-20T12:00:00Z")], "no-thread", "microsoft-owner-1"), []);
assert.deepEqual(getMicrosoftConversationRows([row("m1","trusted-conv","2026-10-20T12:00:00Z")], "m1", "microsoft-owner-1").map(x=>x.providerMessageId), ["m1"]);
assert.deepEqual(getMicrosoftConversationRows(Array.from({length:26},(_,i)=>row("id"+i,"trusted-conv","2026-10-20T12:00:00Z")),"id0","microsoft-owner-1"),[]);
assert.deepEqual(getMicrosoftConversationRows([row("m1","", "2026-10-20T12:00:00Z")],"m1","microsoft-owner-1"),[]);
assert.deepEqual(getMicrosoftConversationRows([row("m1","t".repeat(513),"2026-10-20T12:00:00Z")],"m1","microsoft-owner-1"),[]);
console.log("Microsoft Graph conversation ID: scoped page-only grouping, stable order, dedupe and reject mixed providers PASS");
