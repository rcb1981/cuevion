declare const process: { exitCode?: number };

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  consumeTesterInviteRoute,
  TESTER_INVITE_TOKEN_PATTERN,
} from "./testerInviteRoute";

type HistoryCall = [unknown, string, string];

function fakeHistory(calls: HistoryCall[]) {
  return {
    replaceState(state: unknown, title: string, url?: string | URL | null) {
      calls.push([state, title, String(url ?? "")]);
    },
  } as Pick<History, "replaceState">;
}

function testLocation(
  search: string,
  hash: string,
  pathname = "/",
): Pick<Location, "pathname" | "search" | "hash"> {
  return { pathname, search, hash };
}

function test(name: string, callback: () => void) {
  try {
    callback();
  } catch (error) {
    process.exitCode = 1;
    console.error(`FAIL: ${name}`);
    console.error(error);
  }
}

const token = `tsti_${"A".repeat(22)}.${"B".repeat(43)}`;
assert.equal(TESTER_INVITE_TOKEN_PATTERN.test(token), true);

test("canonical fragment token is returned in memory and scrubbed immediately", () => {
  const calls: HistoryCall[] = [];
  const result = consumeTesterInviteRoute(
    testLocation("", `#tester_invite=${token}`),
    fakeHistory(calls),
  );

  assert.deepEqual(result, { inviteToken: token });
  assert.deepEqual(calls, [[null, "", "/"]]);
  assert.equal(JSON.stringify(calls).includes(token), false);
});

test("query-string tester bearer is scrubbed and rejected", () => {
  const calls: HistoryCall[] = [];
  const result = consumeTesterInviteRoute(
    testLocation(`?tester_invite=${token}`, ""),
    fakeHistory(calls),
  );

  assert.deepEqual(result, { inviteToken: "" });
  assert.deepEqual(calls, [[null, "", "/"]]);
  assert.equal(JSON.stringify(calls).includes(token), false);
});

test("duplicate or mixed tester credentials fail closed after scrubbing", () => {
  for (const candidate of [
    testLocation("", `#tester_invite=${token}&tester_invite=${token}`),
    testLocation(`?tester_invite=${token}`, `#tester_invite=${token}`),
    testLocation("", `#tester_invite=${token}&other=1`),
  ]) {
    const calls: HistoryCall[] = [];
    const result = consumeTesterInviteRoute(candidate, fakeHistory(calls));
    assert.deepEqual(result, { inviteToken: "" });
    assert.equal(calls.length, 1);
    assert.equal(calls[0]?.[2].includes("tester_invite"), false);
    assert.equal(calls[0]?.[2].includes(token), false);
  }
});

test("malformed fragment token is scrubbed and rejected", () => {
  const calls: HistoryCall[] = [];
  const result = consumeTesterInviteRoute(
    testLocation("", "#tester_invite=not-a-token"),
    fakeHistory(calls),
  );

  assert.deepEqual(result, { inviteToken: "" });
  assert.deepEqual(calls, [[null, "", "/"]]);
});

test("unrelated routes are untouched", () => {
  const calls: HistoryCall[] = [];
  const result = consumeTesterInviteRoute(
    testLocation("?workspace_mode=live", "#other=1"),
    fakeHistory(calls),
  );

  assert.equal(result, null);
  assert.deepEqual(calls, []);
});

test("route foundation contains no browser persistence or pre-scrub network call", () => {
  const source = readFileSync(
    resolve(process.cwd(), "src/lib/testerInviteRoute.ts"),
    "utf8",
  );
  assert.equal(source.includes("localStorage"), false);
  assert.equal(source.includes("sessionStorage"), false);
  assert.equal(source.includes("fetch("), false);
  assert.match(source, /history\.replaceState\(null, "", scrubbedUrl\)/);
});
