import assert from "node:assert/strict";
import {
  buildCollaborationGuestInviteLink,
  isValidCollaborationGuestBearer,
  parseCollaborationGuestEntryRoute,
  parseCollaborationGuestRoute,
} from "./collaborationGuestInviteLink";

const TOKEN = "A".repeat(43);
const expected = `https://app.cuevion.com/#collab_guest=${TOKEN}`;

assert.equal(isValidCollaborationGuestBearer(TOKEN), true);
assert.equal(buildCollaborationGuestInviteLink(TOKEN, "https://app.cuevion.com"), expected);
assert.equal(buildCollaborationGuestInviteLink(TOKEN, "https://app.cuevion.com/"), expected);
assert.equal(buildCollaborationGuestInviteLink(TOKEN, "https://app.cuevion.com"), expected);

const built = new URL(expected);
assert.equal(built.pathname, "/");
assert.equal(built.search, "");
assert.equal(built.hash, `#collab_guest=${TOKEN}`);
assert.equal(built.pathname.includes(TOKEN), false);
assert.equal(built.search.includes(TOKEN), false);

for (const invalidToken of [
  "",
  "A".repeat(42),
  "A".repeat(129),
  `${"A".repeat(42)}=`,
  `${"A".repeat(42)}%`,
  `${"A".repeat(42)} `,
  `${"A".repeat(42)},`,
]) {
  assert.equal(isValidCollaborationGuestBearer(invalidToken), false);
  assert.equal(
    buildCollaborationGuestInviteLink(invalidToken, "https://app.cuevion.com"),
    null,
  );
}

for (const invalidOrigin of [
  "not a url",
  "http://app.cuevion.com",
  "https://user:pass@app.cuevion.com",
  "https://app.cuevion.com/path",
  "https://app.cuevion.com/?next=anything",
  "https://app.cuevion.com/#anything",
  "ftp://app.cuevion.com",
]) {
  assert.equal(buildCollaborationGuestInviteLink(TOKEN, invalidOrigin), null);
}
assert.equal(
  buildCollaborationGuestInviteLink(TOKEN, "http://localhost:5173"),
  `http://localhost:5173/#collab_guest=${TOKEN}`,
);

assert.deepEqual(parseCollaborationGuestRoute(`#collab_guest=${TOKEN}`), {
  mode: "collaboration_guest",
  token: TOKEN,
});
assert.deepEqual(parseCollaborationGuestEntryRoute("#collab_guest"), {
  mode: "collaboration_guest",
  token: null,
});
assert.equal(parseCollaborationGuestRoute("#collab_guest"), null);
assert.equal(
  parseCollaborationGuestEntryRoute("#collab_guest", "?anything=1"),
  null,
);

for (const invalidHash of [
  "",
  `collab_guest=${TOKEN}`,
  `#collab_guest=${TOKEN}&extra=1`,
  `#collab_guest=${TOKEN}&collab_guest=${TOKEN}`,
  `#extra=1&collab_guest=${TOKEN}`,
  `#collab_guest=${encodeURIComponent(`${TOKEN}=`)}`,
  `#collab_guest=${TOKEN},`,
  `#collab_guest=${TOKEN} `,
  `#external_review=${TOKEN}`,
  `#collab_invite=${TOKEN}`,
]) {
  assert.equal(parseCollaborationGuestRoute(invalidHash), null);
}

for (const unsafeSearch of [
  `?collab_guest=${TOKEN}`,
  `?external_review=${TOKEN}`,
  `?collab_invite=${TOKEN}`,
  "?preview=onboarding",
]) {
  assert.equal(
    parseCollaborationGuestRoute(`#collab_guest=${TOKEN}`, unsafeSearch),
    null,
  );
}
