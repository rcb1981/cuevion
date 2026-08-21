declare const process: { exitCode?: number };

import assert from "node:assert/strict";
import {
  createScopedFreshTeamInviteUrls,
  createLatestTeamAuthorityRefreshCoordinator,
  findTeamMemberIndexByEmail,
  readScopedFreshTeamInviteUrls,
  resetScopedFreshTeamInviteUrls,
  updateScopedFreshTeamInviteUrls,
} from "./teamAuthorityUi";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

async function run() {
  const initialMembers = [
    { email: "alice@example.test" },
    { email: "bob@example.test" },
  ];
  const selectedEmail = "bob@example.test";
  assert.equal(findTeamMemberIndexByEmail(initialMembers, selectedEmail), 1);
  const reorderedMembers = [{ email: "bob@example.test" }];
  const reorderedIndex = findTeamMemberIndexByEmail(reorderedMembers, selectedEmail);
  assert.equal(reorderedIndex, 0);
  assert.equal(reorderedMembers[reorderedIndex ?? -1]?.email, selectedEmail);
  assert.equal(findTeamMemberIndexByEmail([], selectedEmail), null);

  const authorityA = "member:workspace-a";
  const authorityB = "member:workspace-b";
  const freshInviteUrl =
    "https://app.cuevion.com/?team_invite=tinv_one.one-time-secret";
  let scopedInviteUrls = createScopedFreshTeamInviteUrls(authorityA);
  scopedInviteUrls = updateScopedFreshTeamInviteUrls(
    scopedInviteUrls,
    authorityA,
    scopedInviteUrls.epoch,
    (current) => ({ ...current, invitationA: freshInviteUrl }),
  );
  assert.deepEqual(readScopedFreshTeamInviteUrls(scopedInviteUrls, authorityA), {
    invitationA: freshInviteUrl,
  });
  assert.deepEqual(
    readScopedFreshTeamInviteUrls(scopedInviteUrls, authorityA),
    { invitationA: freshInviteUrl },
    "a Team view unmount/remount in the same authority scope must retain the one-time URL",
  );
  assert.deepEqual(
    readScopedFreshTeamInviteUrls(scopedInviteUrls, authorityB),
    {},
    "a different authority scope must never observe the prior workspace bearer",
  );
  const staleScopeUpdate = updateScopedFreshTeamInviteUrls(
    scopedInviteUrls,
    authorityB,
    scopedInviteUrls.epoch,
    (current) => ({ ...current, invitationB: "must-not-be-applied" }),
  );
  assert.equal(staleScopeUpdate, scopedInviteUrls);
  const authorityAEpoch = scopedInviteUrls.epoch;
  scopedInviteUrls = resetScopedFreshTeamInviteUrls(scopedInviteUrls, authorityB);
  scopedInviteUrls = resetScopedFreshTeamInviteUrls(scopedInviteUrls, authorityA);
  const staleAbaUpdate = updateScopedFreshTeamInviteUrls(
    scopedInviteUrls,
    authorityA,
    authorityAEpoch,
    (current) => ({ ...current, resurrectedInvitation: freshInviteUrl }),
  );
  assert.equal(staleAbaUpdate, scopedInviteUrls);
  assert.deepEqual(
    readScopedFreshTeamInviteUrls(staleAbaUpdate, authorityA),
    {},
    "an updater from A1 must not repopulate a reset A2 scope after A→B→A",
  );
  assert.deepEqual(readScopedFreshTeamInviteUrls(scopedInviteUrls, authorityA), {});

  const coordinator = createLatestTeamAuthorityRefreshCoordinator();
  const older = deferred<string>();
  const newer = deferred<string>();
  const applied: string[] = [];
  const olderResult = coordinator.run(
    () => older.promise,
    (value) => {
      applied.push(value);
      return false;
    },
  );
  const newerResult = coordinator.run(
    () => newer.promise,
    (value) => {
      applied.push(value);
      return true;
    },
  );

  older.resolve("stale-before-mutation");
  await Promise.resolve();
  assert.deepEqual(applied, []);
  newer.resolve("authoritative-after-mutation");
  assert.equal(await newerResult, true);
  assert.equal(await olderResult, true);
  assert.deepEqual(applied, ["authoritative-after-mutation"]);

  const invalidatedLoad = deferred<string>();
  const invalidatedResult = coordinator.run(
    () => invalidatedLoad.promise,
    (value) => {
      applied.push(value);
      return true;
    },
  );
  coordinator.invalidate();
  invalidatedLoad.resolve("wrong-workspace");
  assert.equal(await invalidatedResult, false);
  assert.deepEqual(applied, ["authoritative-after-mutation"]);
}

void run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
