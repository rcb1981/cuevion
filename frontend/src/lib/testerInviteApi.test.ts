import assert from "node:assert/strict";
import { fetchTesterInvite } from "./testerInviteApi";

(async () => {
  const token = "tsti_" + "A".repeat(22) + "." + "B".repeat(43);
  let seenUrl = "";
  let seenInit: RequestInit | undefined;

  const result = await fetchTesterInvite(
    token,
    (async (url: RequestInfo | URL, init?: RequestInit) => {
      seenUrl = String(url);
      seenInit = init;
      return new Response(
        JSON.stringify({
          ok: true,
          invite: {
            inviteeName: "Tester",
            status: "invited",
            expiresAt: 123,
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }) as typeof fetch,
  );

  assert.deepEqual(result, {
    ok: true,
    invite: {
      inviteeName: "Tester",
      status: "invited",
      expiresAt: 123,
    },
  });
  assert.equal(seenUrl, "/api/tester/invite?op=lookup");
  assert.equal(seenInit?.method, "POST");
  assert.equal(JSON.parse(String(seenInit?.body)).token, token);
  assert.doesNotMatch(seenUrl, /tsti_|tester_invite=/);
  assert.deepEqual(
    await fetchTesterInvite("bad", (async () => {
      throw new Error("must not fetch");
    }) as typeof fetch),
    { ok: false, status: 400, code: "invalid_invite" },
  );

  console.log("testerInviteApi tests passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
