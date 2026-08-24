import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  createMailboxTitleEditorState,
  transitionMailboxTitleEditor,
  type MailboxTitleEditorState,
} from "./mailboxTitleEditorState";

function openEditor(canonicalTitle: string) {
  return transitionMailboxTitleEditor(
    createMailboxTitleEditorState(canonicalTitle),
    { type: "open", canonicalTitle },
  ).state;
}

function typeTitle(state: MailboxTitleEditorState, value: string) {
  return transitionMailboxTitleEditor(state, { type: "change", value });
}

const heavyMailboxRenderCount = 1;
let state = openEditor("Carltricks Music");
const typed = typeTitle(state, "Carltricks Music Group");
state = typed.state;
assert.equal(typed.commitTitle, null, "typing must remain local-only");
assert.equal(state.draft, "Carltricks Music Group");
assert.equal(
  heavyMailboxRenderCount,
  1,
  "local title input must not schedule the heavy mailbox render boundary",
);

const enter = transitionMailboxTitleEditor(state, {
  type: "commit",
  canonicalTitle: "Carltricks Music",
});
assert.equal(enter.commitTitle, "Carltricks Music Group");
const blurAfterEnter = transitionMailboxTitleEditor(enter.state, {
  type: "commit",
  canonicalTitle: "Carltricks Music",
});
assert.equal(
  blurAfterEnter.commitTitle,
  null,
  "Enter followed by blur must commit exactly once",
);

state = typeTitle(openEditor("Carltricks Music"), "Carltricks Records").state;
const blur = transitionMailboxTitleEditor(state, {
  type: "commit",
  canonicalTitle: "Carltricks Music",
});
assert.equal(blur.commitTitle, "Carltricks Records", "blur must commit once");
assert.equal(
  transitionMailboxTitleEditor(blur.state, {
    type: "commit",
    canonicalTitle: "Carltricks Music",
  }).commitTitle,
  null,
);

state = typeTitle(openEditor("Carltricks Music"), "Discard me").state;
const escape = transitionMailboxTitleEditor(state, {
  type: "cancel",
  canonicalTitle: "Carltricks Music",
});
assert.equal(escape.commitTitle, null);
assert.deepEqual(escape.state, {
  isEditing: false,
  draft: "Carltricks Music",
});

assert.equal(
  openEditor("Current canonical title").draft,
  "Current canonical title",
  "opening must prefill the current canonical title",
);
assert.equal(
  openEditor("Later authoritative title").draft,
  "Later authoritative title",
  "a later title must be used the next time the editor opens",
);

const workspaceSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);
const mailboxViewStart = workspaceSource.indexOf("function MailboxView(");
const mailboxViewEnd = workspaceSource.indexOf(
  "function WorkbenchView(",
  mailboxViewStart,
);
const mailboxViewSource = workspaceSource.slice(mailboxViewStart, mailboxViewEnd);
assert.match(mailboxViewSource, /<MailboxTitleEditor/);
assert.doesNotMatch(
  mailboxViewSource,
  /mailboxTitleDraft|setMailboxTitleDraft|isEditingMailboxTitle/,
  "MailboxView must not own ordinary title-editor keystroke state",
);
assert.match(
  mailboxViewSource,
  /onCommit=\{\(nextTitle\) =>\s*onRenameMailbox\(mailbox\.id, nextTitle\)/,
  "the existing rename callback must remain the commit boundary",
);

const editorSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/MailboxTitleEditor.tsx"),
  "utf8",
);
assert.match(editorSource, /const \[editorState, setEditorState\] = useState/);
assert.match(editorSource, /onChange=\{\(event\) =>\s*applyAction/);
assert.doesNotMatch(
  editorSource,
  /mailboxStore|managedInboxes|custom_imap|google|provider|setTimeout|debounce/,
  "the isolated editor must not depend on mailbox data or provider behavior",
);

console.log("Mailbox title editor isolation tests passed");
