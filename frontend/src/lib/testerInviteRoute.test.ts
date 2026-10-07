import assert from "node:assert/strict";
import { consumeTesterInviteRoute } from "./testerInviteRoute";

const token = "tsti_" + "A".repeat(22) + "." + "B".repeat(43);

function run(pathname: string, search: string, hash: string) {
  const replaced: string[] = [];
  const result = consumeTesterInviteRoute(
    { pathname, search, hash },
    { replaceState(_data: unknown, _unused: string, url?: string | URL | null) {
      replaced.push(String(url ?? ""));
    }},
  );
  return { result, replaced };
}

assert.deepEqual(run("/", "", "#tester_invite=" + token), {
  result: { inviteToken: token }, replaced: ["/"],
});
assert.deepEqual(run("/", "?tester_invite=" + token, ""), {
  result: { inviteToken: null }, replaced: ["/"],
});
assert.deepEqual(run("/", "", "#tester_invite=" + token + "&tester_invite=" + token), {
  result: { inviteToken: null }, replaced: ["/"],
});
assert.deepEqual(run("/", "", "#tester_invite=" + token + "&x=1"), {
  result: { inviteToken: null }, replaced: ["/#x=1"],
});
assert.deepEqual(run("/other", "", "#tester_invite=" + token), {
  result: { inviteToken: null }, replaced: ["/other"],
});
console.log("testerInviteRoute tests passed");
