import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import "sucrase/register/tsx.js";

const {
  buildInitialTeamMembers,
  getPublishedTeamMembers,
  getTeamRosterPresentation,
  replaceWithAuthoritativeTeamMembers,
  shouldAllowLocalTeamMemberMutation,
} = require("./WorkspaceShell.tsx") as typeof import("./WorkspaceShell");

const phantomLocalMember = {
  name: "Phantom Local Member",
  email: "phantom@example.test",
  accessLevel: "Limited" as const,
  selectedInboxes: [],
  status: "Active" as const,
};
const serverMember = {
  email: "server@example.test",
  displayName: "Server Member",
  accessLevel: "Shared" as const,
  status: "active" as const,
};

assert.deepEqual(
  replaceWithAuthoritativeTeamMembers([phantomLocalMember], []),
  [],
  "an authoritative empty roster must clear phantom local rows",
);
assert.deepEqual(
  replaceWithAuthoritativeTeamMembers([phantomLocalMember], [serverMember]),
  [
    {
      name: "Server Member",
      email: "server@example.test",
      accessLevel: "Shared",
      selectedInboxes: [],
      status: "Active",
      teamInviteStatus: "accepted",
    },
  ],
  "an authoritative populated roster must replace rather than union local rows",
);

assert.deepEqual(
  getTeamRosterPresentation("loading", [phantomLocalMember]),
  { kind: "loading", message: "Loading team members…", canRetry: false },
  "loading must not render stale rows or the successful empty state",
);
assert.deepEqual(
  getTeamRosterPresentation("success", []),
  { kind: "empty", message: "No team members yet.", canRetry: false },
);
assert.deepEqual(
  getTeamRosterPresentation("unavailable", [phantomLocalMember]),
  {
    kind: "unavailable",
    message: "Team members are temporarily unavailable.",
    canRetry: true,
  },
  "server failure must hide stale rows and remain distinct from genuine empty",
);
assert.deepEqual(
  getTeamRosterPresentation("forbidden", [phantomLocalMember]),
  {
    kind: "forbidden",
    message: "You do not have permission to view team members.",
    canRetry: true,
  },
);
assert.deepEqual(
  getTeamRosterPresentation("unauthorized", [phantomLocalMember]),
  {
    kind: "unauthorized",
    message: "Sign in to view team members.",
    canRetry: true,
  },
);

assert.deepEqual(buildInitialTeamMembers(false, null), []);
assert.deepEqual(
  buildInitialTeamMembers(false, JSON.stringify([phantomLocalMember])),
  [],
  "production must ignore browser-local roster rows before the server responds",
);
assert.equal(
  buildInitialTeamMembers(false, null).some((member) => member.email.endsWith("@cuevion.com")),
  false,
  "production must not inject demo members",
);
assert.equal(shouldAllowLocalTeamMemberMutation(false), false);
assert.equal(shouldAllowLocalTeamMemberMutation(true), true);
assert.deepEqual(
  getPublishedTeamMembers("unavailable", [phantomLocalMember]),
  [],
  "a failed same-workspace refresh must remove stale roster rows from downstream consumers",
);
assert.deepEqual(
  getPublishedTeamMembers("success", [phantomLocalMember]),
  [phantomLocalMember],
);

const workspaceShellSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);
assert.match(
  workspaceShellSource,
  /leftMember\.teamInviteToken === rightMember\.teamInviteToken/,
  "a stale local token must force replacement by the token-free authoritative row",
);
const rosterEffectStart = workspaceShellSource.indexOf(
  "const loadBackendTeamMembers = async () =>",
);
const rosterEffectEnd = workspaceShellSource.indexOf(
  "useEffect(() => {\n    if (showDemoContent) {\n      void syncInviteOnlyTeamInviteStatuses();",
  rosterEffectStart,
);
assert.notEqual(rosterEffectStart, -1);
assert.notEqual(rosterEffectEnd, -1);
const rosterEffect = workspaceShellSource.slice(rosterEffectStart, rosterEffectEnd);
assert.match(rosterEffect, /await fetchTeamMembers\(\)/);
assert.match(rosterEffect, /backendTeamMembersRefreshKey/);
assert.match(
  rosterEffect,
  /workspacePersistenceKey/,
  "a session-workspace change must reset and refetch without becoming request authority",
);
assert.match(
  workspaceShellSource,
  /<WorkbenchView[\s\S]*?key=\{teamRosterAuthorityKey\}/,
  "a workspace or authentication-authority switch must remount the roster state",
);
assert.match(
  workspaceShellSource,
  /onClick=\{\(\) => setBackendTeamMembersRefreshKey\(\(current\) => current \+ 1\)\}[\s\S]*?>\s*Retry\s*</,
  "Retry must trigger the refresh key consumed by the real roster fetch effect",
);
