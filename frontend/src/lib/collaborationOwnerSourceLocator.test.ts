declare const process: { exitCode?: number };

import assert from "node:assert/strict";
import {
  deriveCollaborationOwnerSourceLocator,
  isCanonicalCollaborationOwnerSourceLocator,
  isTrustedCollaborationOwnerSourceLocator,
} from "./collaborationOwnerSourceLocator";

const googleMailbox = {
  id: "mailbox-google",
  provider: "google",
  connected: true,
  connectionStatus: "connected",
};
const imapMailbox = {
  id: "mailbox-imap",
  provider: "custom_imap",
  connected: true,
  connectionStatus: "connected",
};

function googleInput(overrides: Record<string, unknown> = {}) {
  return {
    workspaceDataMode: "live",
    hasAuthenticatedMemberAuthority: true,
    managedMailbox: googleMailbox,
    sourceMailboxId: googleMailbox.id,
    trustedFolder: "INBOX",
    message: {
      id: "local-message-id-must-not-be-used",
      serverMailboxId: googleMailbox.id,
      providerMessageId: "provider-message-id",
      threadIdentityContext: {
        mailboxId: googleMailbox.id,
        provider: "google",
        folder: "INBOX",
        uidValidity: null,
      },
    },
    ...overrides,
  };
}

function imapInput(overrides: Record<string, unknown> = {}) {
  return {
    workspaceDataMode: "live",
    hasAuthenticatedMemberAuthority: true,
    managedMailbox: imapMailbox,
    sourceMailboxId: imapMailbox.id,
    trustedFolder: "INBOX",
    message: {
      id: "local-message-id",
      serverMailboxId: imapMailbox.id,
      providerFolder: "INBOX",
      uidValidity: "9001",
      imapUid: "42",
      threadIdentityContext: {
        mailboxId: imapMailbox.id,
        provider: "custom_imap",
        folder: "INBOX",
        uidValidity: "9001",
      },
    },
    ...overrides,
  };
}

function run(name: string, callback: () => void) {
  try {
    callback();
  } catch (error) {
    process.exitCode = 1;
    console.error(`FAIL: ${name}`);
    console.error(error);
  }
}

run("uses the exact Gmail providerMessageId and never the app message id", () => {
  const locator = deriveCollaborationOwnerSourceLocator(googleInput());
  assert.deepEqual(locator, {
    mailboxId: googleMailbox.id,
    sourceRef: { providerMessageId: "provider-message-id" },
  });
  assert.equal(isCanonicalCollaborationOwnerSourceLocator(locator), true);
  assert.equal(isTrustedCollaborationOwnerSourceLocator(locator), true);
  assert.equal(Object.isFrozen(locator), true);
  assert.equal(Object.isFrozen(locator?.sourceRef), true);
  assert.equal(
    isTrustedCollaborationOwnerSourceLocator({
      mailboxId: googleMailbox.id,
      sourceRef: { providerMessageId: "provider-message-id" },
    }),
    false,
  );
  assert.equal(
    deriveCollaborationOwnerSourceLocator(
      googleInput({
        message: {
          id: "provider-looking-local-id",
          serverMailboxId: googleMailbox.id,
          threadIdentityContext: {
            mailboxId: googleMailbox.id,
            provider: "google",
            folder: "INBOX",
            uidValidity: null,
          },
        },
      }),
    ),
    null,
  );
});

run("rejects missing, whitespace, coerced, and cross-mailbox Gmail identity", () => {
  for (const providerMessageId of [undefined, " provider-id", "provider id", 123]) {
    assert.equal(
      deriveCollaborationOwnerSourceLocator(
        googleInput({
          message: {
            ...(googleInput().message as object),
            providerMessageId,
          },
        }),
      ),
      null,
    );
  }
  assert.equal(
    deriveCollaborationOwnerSourceLocator(
      googleInput({ sourceMailboxId: "mailbox-other" }),
    ),
    null,
  );
});

run("accepts only exact canonical custom IMAP Inbox identity", () => {
  assert.deepEqual(deriveCollaborationOwnerSourceLocator(imapInput()), {
    mailboxId: imapMailbox.id,
    sourceRef: { folder: "INBOX", uidValidity: "9001", imapUid: "42" },
  });

  for (const invalid of ["0", "01", " 1", "1 ", "+1", "-1", 1, undefined]) {
    assert.equal(
      deriveCollaborationOwnerSourceLocator(
        imapInput({
          message: {
            ...(imapInput().message as object),
            imapUid: invalid,
          },
        }),
      ),
      null,
    );
    assert.equal(
      deriveCollaborationOwnerSourceLocator(
        imapInput({
          message: {
            ...(imapInput().message as object),
            uidValidity: invalid,
            threadIdentityContext: {
              ...(imapInput().message as { threadIdentityContext: object })
                .threadIdentityContext,
              uidValidity: invalid,
            },
          },
        }),
      ),
      null,
    );
  }
});

run("uses trusted UIDVALIDITY fallback only when every trusted value agrees", () => {
  assert.deepEqual(
    deriveCollaborationOwnerSourceLocator(
      imapInput({
        trustedUidValidity: "9001",
        message: {
          ...(imapInput().message as object),
          uidValidity: undefined,
        },
      }),
    ),
    {
      mailboxId: imapMailbox.id,
      sourceRef: { folder: "INBOX", uidValidity: "9001", imapUid: "42" },
    },
  );
  assert.equal(
    deriveCollaborationOwnerSourceLocator(
      imapInput({ trustedUidValidity: "9002" }),
    ),
    null,
  );
  assert.equal(
    deriveCollaborationOwnerSourceLocator(
      imapInput({
        message: {
          ...(imapInput().message as object),
          uidValidity: undefined,
          threadIdentityContext: {
            ...(imapInput().message as { threadIdentityContext: object })
              .threadIdentityContext,
            uidValidity: undefined,
          },
        },
      }),
    ),
    null,
  );
});

run("rejects IMAP Archive, Trash, and any non-Inbox provider context", () => {
  for (const folder of ["ARCHIVE", "Archive", "TRASH", "Trash"]) {
    assert.equal(
      deriveCollaborationOwnerSourceLocator(
        imapInput({ trustedFolder: folder }),
      ),
      null,
    );
    assert.equal(
      deriveCollaborationOwnerSourceLocator(
        imapInput({
          message: {
            ...(imapInput().message as object),
            providerFolder: folder,
          },
        }),
      ),
      null,
    );
  }
});

run("fails closed without live member and real connected mailbox authority", () => {
  const cases = [
    googleInput({ workspaceDataMode: "demo" }),
    googleInput({ hasAuthenticatedMemberAuthority: false }),
    googleInput({ managedMailbox: null }),
    googleInput({ managedMailbox: { ...googleMailbox, connected: false } }),
    googleInput({
      managedMailbox: { ...googleMailbox, connectionStatus: "reconnect_required" },
    }),
    googleInput({ managedMailbox: { ...googleMailbox, provider: "microsoft" } }),
    googleInput({ sourceMailboxId: "collaboration-shared" }),
  ];
  for (const value of cases) {
    assert.equal(deriveCollaborationOwnerSourceLocator(value), null);
  }
});

run("cannot reuse one app message id across mailbox authority boundaries", () => {
  const first = deriveCollaborationOwnerSourceLocator(googleInput());
  const second = deriveCollaborationOwnerSourceLocator(
    googleInput({
      managedMailbox: { ...googleMailbox, id: "mailbox-google-2" },
      sourceMailboxId: "mailbox-google-2",
    }),
  );
  assert.notEqual(first, null);
  assert.equal(second, null);
});
