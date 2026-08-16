import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { transform } from "sucrase";

const workspaceShellSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);
const openComposeStart = workspaceShellSource.indexOf(
  "const openComposeFromMessage =",
);
const openComposeEnd = workspaceShellSource.indexOf(
  "useEffect(() =>",
  openComposeStart,
);

assert.ok(
  openComposeStart >= 0 && openComposeEnd > openComposeStart,
  "message compose source markers must be present and ordered",
);

const openComposeSource = workspaceShellSource.slice(
  openComposeStart,
  openComposeEnd,
);
const attachmentInitializationStart = openComposeSource.indexOf(
  "setComposeAttachments(",
);
const attachmentInitializationEndMarker = "\n    );";
const attachmentInitializationEnd =
  openComposeSource.indexOf(
    attachmentInitializationEndMarker,
    attachmentInitializationStart,
  ) + attachmentInitializationEndMarker.length;

assert.ok(
  attachmentInitializationStart >= 0 &&
    attachmentInitializationEnd > attachmentInitializationStart,
  "source-attachment initialization markers must be present and ordered",
);

const attachmentInitializationSource = openComposeSource.slice(
  attachmentInitializationStart,
  attachmentInitializationEnd,
);
const compiledAttachmentInitialization = transform(
  `function resolveInitialComposeAttachments(mode, effectiveMessage) {
    const sourceMailboxId = "mailbox-google";
    const currentMessageLocationById = {
      [effectiveMessage.id]: { folder: "Inbox" },
    };
    const normalizeMailAttachment = (attachment) => attachment;
    let composeAttachments = [{ id: "stale", name: "stale.txt" }];
    const setComposeAttachments = (attachments) => {
      composeAttachments = attachments;
    };
    ${attachmentInitializationSource}
    return composeAttachments;
  }`,
  { transforms: ["typescript"] },
).code;
const loadAttachmentInitializationHarness = new Function(
  `${compiledAttachmentInitialization}\nreturn resolveInitialComposeAttachments;`,
) as () => (
  mode: "reply" | "reply_all" | "forward",
  effectiveMessage: {
    id: string;
    imapUid?: string;
    attachments?: SourceAttachment[];
  },
) => SourceAttachment[];
const resolveInitialComposeAttachments =
  loadAttachmentInitializationHarness();

type SourceAttachment = {
  id: string;
  name: string;
  contentId?: string;
  disposition?: string;
};

const inlineAttachment: SourceAttachment = {
  id: "inline-logo",
  name: "logo.png",
  disposition: "inline",
  contentId: "logo@example.test",
};
const pdfAttachment: SourceAttachment = {
  id: "invoice-pdf",
  name: "invoice.pdf",
};
const sourceMessage = (attachments: SourceAttachment[]) => ({
  id: "source-message",
  attachments,
});

for (const mode of ["reply", "reply_all"] as const) {
  assert.deepEqual(
    resolveInitialComposeAttachments(mode, sourceMessage([inlineAttachment])),
    [],
    `${mode} must not inherit an inline/CID source attachment`,
  );
  assert.deepEqual(
    resolveInitialComposeAttachments(mode, sourceMessage([pdfAttachment])),
    [],
    `${mode} must not inherit an ordinary source attachment`,
  );
  assert.deepEqual(
    resolveInitialComposeAttachments(mode, sourceMessage([])),
    [],
    `${mode} without source attachments must remain empty`,
  );
}

assert.deepEqual(
  resolveInitialComposeAttachments("forward", sourceMessage([pdfAttachment])).map(
    ({ id, name }) => ({ id, name }),
  ),
  [pdfAttachment],
  "Forward must retain ordinary source-attachment inheritance",
);
assert.deepEqual(
  resolveInitialComposeAttachments(
    "forward",
    sourceMessage([inlineAttachment]),
  ).map(({ id, name, disposition, contentId }) => ({
    id,
    name,
    disposition,
    contentId,
  })),
  [inlineAttachment],
  "Forward must retain inline/CID source-attachment inheritance",
);

const addFilesStart = workspaceShellSource.indexOf(
  "const addFilesToComposeAttachments =",
);
const addFilesEnd = workspaceShellSource.indexOf(
  "const handleComposeAttachmentSelection =",
  addFilesStart,
);

assert.ok(
  addFilesStart >= 0 && addFilesEnd > addFilesStart,
  "manual compose-attachment source markers must be present and ordered",
);

const addFilesSource = workspaceShellSource.slice(addFilesStart, addFilesEnd);
const compiledAddFilesHarness = transform(
  `function addManualComposeFiles(initialAttachments, files) {
    let composeAttachments = initialAttachments;
    const normalizeMailAttachment = (attachment) => attachment;
    const createMailAttachmentId = (name, size, mimeType) =>
      [name, size || 0, mimeType || "file"].join("-");
    const setComposeAttachments = (update) => {
      composeAttachments = typeof update === "function"
        ? update(composeAttachments)
        : update;
    };
    ${addFilesSource}
    addFilesToComposeAttachments(files);
    return composeAttachments;
  }`,
  { transforms: ["typescript"] },
).code;
const loadAddFilesHarness = new Function(
  `${compiledAddFilesHarness}\nreturn addManualComposeFiles;`,
) as () => (
  initialAttachments: SourceAttachment[],
  files: Array<{ name: string; size: number; type: string }>,
) => Array<SourceAttachment & { file?: unknown; mimeType?: string; size?: number }>;
const addManualComposeFiles = loadAddFilesHarness();
const manualFile = {
  name: "reply-notes.txt",
  size: 14,
  type: "text/plain",
};
const replyWithManualAttachment = addManualComposeFiles(
  resolveInitialComposeAttachments("reply", sourceMessage([pdfAttachment])),
  [manualFile],
);

assert.equal(replyWithManualAttachment.length, 1);
assert.equal(replyWithManualAttachment[0]?.file, manualFile);
assert.equal(replyWithManualAttachment[0]?.name, manualFile.name);

const resetComposeStart = workspaceShellSource.indexOf(
  "const resetComposeState =",
);
const resetComposeEnd = workspaceShellSource.indexOf(
  "const normalizeRememberedRecipient =",
  resetComposeStart,
);
const resetComposeSource = workspaceShellSource.slice(
  resetComposeStart,
  resetComposeEnd,
);

assert.match(
  resetComposeSource,
  /setComposeAttachments\(\[\]\)/,
  "compose reset must continue clearing previous attachments",
);
assert.match(
  workspaceShellSource.slice(
    workspaceShellSource.indexOf("const sendMessage = async"),
    workspaceShellSource.indexOf("const closeMenus ="),
  ),
  /composeAttachments\.map\(serializeComposeAttachment\)/,
  "send serialization must continue using only the initialized/current compose attachments",
);
