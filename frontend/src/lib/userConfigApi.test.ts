declare const require: (id: string) => any;
declare const process: { exitCode?: number };

const assert = require("node:assert/strict");
const {
  loadUserAccountConfig,
  saveUserAccountConfig,
  setUserAccountConfigHydrationEchoExpectation,
} = require("./userConfigApi") as typeof import("./userConfigApi");

function response(status: number, payload: unknown): Response {
  return { status, json: async () => payload } as Response;
}

function savedResponse(init?: RequestInit): Response {
  return response(200, {
    ok: true,
    config: JSON.parse(String(init?.body)).config,
  });
}

function malformedJsonResponse(): Response {
  return {
    status: 200,
    json: async () => Promise.reject(new SyntaxError("invalid JSON")),
  } as unknown as Response;
}

function useFetch(handler: typeof fetch): void {
  globalThis.fetch = handler as typeof fetch;
}

async function loadWith(status: number, payload: unknown) {
  useFetch(async () => response(status, payload));
  return loadUserAccountConfig();
}

let passed = 0;
async function test(name: string, run: () => void | Promise<void>): Promise<void> {
  setUserAccountConfigHydrationEchoExpectation(null, null);
  try {
    await run();
    passed += 1;
  } catch (error) {
    if (error instanceof Error) error.message = `${name}: ${error.message}`;
    throw error;
  }
}

async function run(): Promise<void> {
  const originalFetch = globalThis.fetch;
  try {
    await test("found GET", async () => {
      const config = {
        v: 2,
        onboardingSession: {},
        managedInboxes: [],
        uiPreferences: { themeMode: "System" as const },
      };
      let request: { url?: RequestInfo | URL; init?: RequestInit } = {};
      useFetch(async (url, init) => {
        request = { url, init };
        return response(200, { ok: true, configState: "found", config });
      });
      assert.deepEqual(await loadUserAccountConfig(), { status: "found", config });
      assert.deepEqual(
        [request.url, request.init?.method, request.init?.credentials, request.init?.cache],
        ["/api/user/config", "GET", "include", "no-store"],
      );
    });

    await test("missing and HTTP failures", async () => {
      assert.deepEqual(
        await loadWith(200, { ok: true, configState: "missing", config: null }),
        { status: "missing", config: null },
      );
      for (const [status, code, expected] of [
        [503, "config_unavailable", "unavailable"],
        [503, "config_invalid", "invalid"],
        [401, "unauthorized", "unauthorized"],
        [503, "authentication_unavailable", "authentication_unavailable"],
      ] as const) {
        const result = await loadWith(status, {
          ok: false,
          error: { code, message: code },
        });
        assert.equal(result.status, expected, code);
      }
      assert.equal(
        (
          await loadWith(500, {
            ok: true,
            configState: "missing",
            config: null,
          })
        ).status,
        "malformed_response",
      );
    });

    await test("malformed responses", async () => {
      for (const payload of [
        { ok: true, config: null },
        { ok: true, configState: "missing", config: {} },
        { ok: true, configState: "found", config: { onboardingSession: "bad" } },
        { ok: true, configState: "found", config: { managedInboxes: ["bad"] } },
        { ok: true, configState: "found", config: { uiPreferences: "bad" } },
      ]) {
        assert.equal((await loadWith(200, payload)).status, "malformed_response");
      }
      useFetch(async () => malformedJsonResponse());
      assert.equal((await loadUserAccountConfig()).status, "malformed_response");
      useFetch(async () => {
        throw new TypeError("network down");
      });
      assert.equal((await loadUserAccountConfig()).status, "network_error");
    });

    await test("POST compatibility and partial configs", async () => {
      const requests: RequestInit[] = [];
      useFetch(async (_url, init) => {
        requests.push(init ?? {});
        return savedResponse(init);
      });
      assert.equal((await saveUserAccountConfig({})).status, "found");
      assert.equal((await saveUserAccountConfig({ managedInboxes: [] })).status, "found");
      assert.equal(requests.length, 2);
      assert.deepEqual(
        [requests[0]?.method, requests[0]?.credentials, requests[0]?.headers],
        ["POST", "include", { "Content-Type": "application/json" }],
      );
      assert.deepEqual(
        requests.map(({ body }) => JSON.parse(String(body))),
        [{ config: {} }, { config: { managedInboxes: [] } }],
      );
    });

    await test("one-shot exact hydration echo", async () => {
      const full = { v: 1, onboardingSession: {}, managedInboxes: [] };
      let fetches = 0;
      useFetch(async (_url, init) => {
        fetches += 1;
        return savedResponse(init);
      });
      setUserAccountConfigHydrationEchoExpectation("account-a", full);
      assert.deepEqual(await saveUserAccountConfig(full), { status: "found", config: full });
      assert.equal(fetches, 0);
      assert.equal((await saveUserAccountConfig(full)).status, "found");
      assert.equal(fetches, 1);

      setUserAccountConfigHydrationEchoExpectation("account-a", full);
      assert.equal((await saveUserAccountConfig({})).status, "found");
      assert.equal((await saveUserAccountConfig(full)).status, "found");
      assert.equal(fetches, 3);

      const latest = { email: "latest@example.com" };
      setUserAccountConfigHydrationEchoExpectation("account-a", full);
      setUserAccountConfigHydrationEchoExpectation("account-b", latest);
      assert.equal((await saveUserAccountConfig(latest)).status, "found");
      assert.equal(fetches, 3);
      setUserAccountConfigHydrationEchoExpectation("account-b", latest);
      setUserAccountConfigHydrationEchoExpectation(null, null);
      assert.equal((await saveUserAccountConfig(latest)).status, "found");
      assert.equal(fetches, 4);
    });

    await test("serialized POST transport respects account lifecycle", async () => {
      let started = 0;
      let activeFetches = 0;
      let maximumActiveFetches = 0;
      const release: Array<() => void> = [];
      useFetch(async (_url, init) => {
        started += 1;
        const requestNumber = started;
        activeFetches += 1;
        maximumActiveFetches = Math.max(maximumActiveFetches, activeFetches);
        try {
          await new Promise<void>((resolve) => release.push(resolve));
          if (requestNumber === 3) throw new TypeError("network down");
          return savedResponse(init);
        } finally {
          activeFetches -= 1;
        }
      });
      setUserAccountConfigHydrationEchoExpectation("account-a", null);
      const active = saveUserAccountConfig({ email: "active-a@example.com" });
      const stale = saveUserAccountConfig({ email: "queued-a@example.com" });
      await Promise.resolve();
      assert.equal(started, 1);
      setUserAccountConfigHydrationEchoExpectation("account-b", null);
      release.shift()?.();
      assert.equal((await active).status, "found");
      assert.equal((await stale).status, "unavailable");
      assert.equal(started, 1, "the stale account request must not reach fetch");

      const current = saveUserAccountConfig({ email: "active-b@example.com" });
      await Promise.resolve();
      assert.equal(started, 2);
      release.shift()?.();
      assert.equal((await current).status, "found");

      const failed = saveUserAccountConfig({ email: "failed-b@example.com" });
      const queued = saveUserAccountConfig({ email: "queued-b@example.com" });
      await Promise.resolve();
      assert.equal(started, 3);
      setUserAccountConfigHydrationEchoExpectation("account-b", { email: "echo" });
      release.shift()?.();
      assert.equal((await failed).status, "network_error");
      await Promise.resolve();
      assert.equal(started, 4, "the same-account queue must recover after failure");
      release.shift()?.();
      assert.equal((await queued).status, "found");
      assert.equal(maximumActiveFetches, 1);
    });

    await test("failed POST responses", async () => {
      useFetch(async () => response(503, {
        ok: false,
        error: { code: "config_invalid", message: "invalid" },
      }));
      assert.equal((await saveUserAccountConfig({})).status, "invalid");
      useFetch(async () => response(200, { ok: true, config: null }));
      assert.equal((await saveUserAccountConfig({})).status, "malformed_response");
      useFetch(async () => {
        throw new TypeError("network down");
      });
      assert.equal((await saveUserAccountConfig({})).status, "network_error");
      useFetch(async () => malformedJsonResponse());
      assert.equal((await saveUserAccountConfig({})).status, "malformed_response");

      const serializationChangingConfig = {
        email: "member@example.com",
        toJSON: () => null,
      };
      setUserAccountConfigHydrationEchoExpectation("account-a", serializationChangingConfig);
      assert.equal((await saveUserAccountConfig(serializationChangingConfig)).status, "invalid");
    });

    console.log(`userConfigApi: ${passed} tests passed`);
  } finally {
    setUserAccountConfigHydrationEchoExpectation(null, null);
    globalThis.fetch = originalFetch;
  }
}

void run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
