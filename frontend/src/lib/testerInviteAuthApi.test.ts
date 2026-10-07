import assert from "node:assert/strict";
import {
  AUTH0_TESTER_LOGIN_ENDPOINT,
  buildTesterInviteAuthenticationSubmission,
} from "./authApi";

const token = "tsti_" + "A".repeat(22) + "." + "B".repeat(43);
const submission = buildTesterInviteAuthenticationSubmission(token);

assert.deepEqual(submission, {
  action: AUTH0_TESTER_LOGIN_ENDPOINT,
  method: "POST",
  enctype: "application/x-www-form-urlencoded",
  fieldName: "tester_invite",
  fieldValue: token,
});
assert.equal(AUTH0_TESTER_LOGIN_ENDPOINT, "/api/auth/tester-login");
assert.equal(buildTesterInviteAuthenticationSubmission("bad"), null);
assert.equal(
  buildTesterInviteAuthenticationSubmission(token + " "),
  null,
);
assert.doesNotMatch(AUTH0_TESTER_LOGIN_ENDPOINT, /tester_invite|tsti_/);

console.log("testerInviteAuthApi tests passed");
