declare const process: { exitCode?: number };

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

try {
  const workspaceSource = fs.readFileSync(
    path.resolve(__dirname, "./WorkspaceShell.tsx"),
    "utf8",
  );
  const controllerCallIndex = workspaceSource.indexOf(
    "const pollingController = createActiveMailboxPollingController({",
  );
  assert.notEqual(controllerCallIndex, -1);
  const effectStart = workspaceSource.lastIndexOf(
    "useEffect(() => {",
    controllerCallIndex,
  );
  const effectEnd = workspaceSource.indexOf(
    "const workspaceShellPaddingClass",
    controllerCallIndex,
  );
  assert.notEqual(effectStart, -1);
  assert.notEqual(effectEnd, -1);
  const pollingEffect = workspaceSource.slice(effectStart, effectEnd);

  assert.match(
    workspaceSource,
    /createActiveMailboxPollingController,[\s\S]*from "\.\.\/\.\.\/lib\/mailboxRefreshSemantics"/,
  );
  assert.match(
    pollingEffect,
    /if \(isMobileWorkspaceViewport\) \{\s*return;\s*\}/,
  );
  assert.match(
    pollingEffect,
    /document\.visibilityState === "visible"/,
  );
  assert.match(pollingEffect, /window\.performance\.now\(\)/);
  assert.match(pollingEffect, /window\.setTimeout\(callback, delayMs\)/);
  assert.doesNotMatch(pollingEffect, /setInterval/);
  assert.match(
    pollingEffect,
    /syncingMailboxIdsRef\.current\.has\(activeMailbox\.id\)/,
  );
  assert.match(
    pollingEffect,
    /refreshMailboxById\(activeMailbox\.id, \{ reason: "interval" \}\)/,
  );
  assert.match(pollingEffect, /return pollingController\.stop;/);
  assert.match(
    pollingEffect,
    /activeMailbox,[\s\S]*hasAuthenticatedMemberAuthority,[\s\S]*isMobileWorkspaceViewport,[\s\S]*savedManagedInboxes/,
  );

  const mailboxOpenCall =
    'void refreshMailboxById(activeMailbox.id, { reason: "mailbox_open" });';
  assert.equal(workspaceSource.split(mailboxOpenCall).length - 1, 1);
  assert.match(
    workspaceSource,
    /await refreshMailboxById\(activeMailbox\.id, \{ reason: "manual" \}\)/,
  );
  assert.match(workspaceSource, /reason: "startup"/);
  assert.match(
    workspaceSource,
    /if \(canUseGmailOAuthFetch && !isProviderReconciliation\) \{\s*await refreshProviderTrashById\(mailboxId\);/,
  );

  console.log("WorkspaceShell visibility-aware mailbox polling wiring tests passed");
} catch (error) {
  console.error(error);
  process.exitCode = 1;
}
