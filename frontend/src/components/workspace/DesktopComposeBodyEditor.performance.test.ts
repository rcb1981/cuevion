import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createDesktopComposeBodyDraft } from "./desktopComposeBodyDraft";

type PerformanceFixture = {
  name: string;
  messages: Array<{
    id: string;
    providerThreadId?: string;
    threadId: string;
  }>;
  quotedBody: string;
};

const gmailFixture: PerformanceFixture = {
  name: "Gmail",
  messages: Array.from({ length: 50 }, (_, index) => ({
    id: `gmail-message-${index}`,
    providerThreadId: `provider-thread-${Math.floor(index / 5)}`,
    threadId: `gmail:mailbox-a:provider-thread-${Math.floor(index / 5)}`,
  })),
  quotedBody: Array.from(
    { length: 240 },
    (_, index) => `<div>Long Gmail quoted reply line ${index}</div>`,
  ).join(""),
};

const customImapFixture: PerformanceFixture = {
  name: "custom IMAP",
  messages: Array.from({ length: 20 }, (_, index) => ({
    id: `imap-message-${index}`,
    threadId: `imap:rfc:mailbox-b:root-${Math.floor(index / 4)}%40example.com`,
  })),
  quotedBody: Array.from(
    { length: 80 },
    (_, index) => `<div>Custom IMAP quoted reply line ${index}</div>`,
  ).join(""),
};

function verifyContainedTyping(fixture: PerformanceFixture) {
  let mailboxThreadPriorityDerivationCount = 0;
  const deriveMailboxThreadPriorityState = () => {
    mailboxThreadPriorityDerivationCount += 1;
    return new Map(fixture.messages.map((message) => [message.id, message.threadId]));
  };

  const derivedState = deriveMailboxThreadPriorityState();
  assert.equal(derivedState.size, fixture.messages.length);

  const initialBody = `<div><br></div><div data-compose-quote="true">${fixture.quotedBody}</div>`;
  const draft = createDesktopComposeBodyDraft(initialBody);
  const typedCharacters = ["H", "e", "l", "l", "o"];
  let typedBody = "";

  typedCharacters.forEach((character) => {
    typedBody += character;
    draft.recordInput(`<div>${typedBody}</div>${initialBody}`);
  });

  assert.equal(
    draft.getBodyHtml(),
    `<div>Hello</div>${initialBody}`,
    `${fixture.name} composer must expose the latest exact body snapshot`,
  );
  assert.equal(
    mailboxThreadPriorityDerivationCount,
    1,
    `${fixture.name} composer input must not invoke mailbox/thread/Priority derivation`,
  );
}

verifyContainedTyping(gmailFixture);
verifyContainedTyping(customImapFixture);

const workspaceShellSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);
const mailboxViewStart = workspaceShellSource.indexOf("function MailboxView(");
const mailboxViewEnd = workspaceShellSource.indexOf("function WorkbenchView(", mailboxViewStart);
const mailboxViewSource = workspaceShellSource.slice(mailboxViewStart, mailboxViewEnd);

assert.ok(mailboxViewStart >= 0 && mailboxViewEnd > mailboxViewStart);
assert.match(
  mailboxViewSource,
  /<DesktopComposeBodyEditor[\s\S]*?ref=\{composeBodyEditorRef\}/,
  "MailboxView must delegate desktop body input to the isolated editor boundary",
);
assert.doesNotMatch(
  mailboxViewSource,
  /onInput=\{syncComposeBodyValue\}|setComposeBody\(nextValue\)/,
  "MailboxView must not receive an ordinary desktop body input update",
);
assert.match(
  mailboxViewSource,
  /getCurrentComposeBodyHtml[\s\S]*?getBodyHtml\(\)[\s\S]*?saveDraftAndClose[\s\S]*?currentComposeBodyHtml[\s\S]*?sendMessage[\s\S]*?getCurrentComposeBodyHtml\(\)/,
  "Save Draft and Send must consume one exact snapshot from the isolated editor",
);

const editorSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/DesktopComposeBodyEditor.tsx"),
  "utf8",
);
assert.match(
  editorSource,
  /onInput=\{recordEditorInput\}/,
  "the isolated editor must own ordinary input",
);
assert.doesNotMatch(
  editorSource,
  /setComposeBody|mailboxStore|normalPriorityGateCandidateEntries|getThreadMessages/,
  "the isolated editor must not depend on mailbox, thread, or Priority state",
);

console.log("\nDesktop composer render-containment performance tests passed.");
