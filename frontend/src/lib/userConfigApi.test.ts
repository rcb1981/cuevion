declare const require: (id: string) => any;
declare const process: { exitCode?: number };

const assert = require("node:assert/strict");
const {
  createUserAccountConfigConflictRetryQueue,
  loadUserAccountConfig,
  projectWorkspaceUserAccountConfigForSave,
  saveUserAccountConfig,
  setUserAccountConfigHydrationEchoExpectation,
} = require("./userConfigApi") as typeof import("./userConfigApi");
type UserAccountConfig = import("./userConfigApi").UserAccountConfig;

async function flushAsyncWork(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

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
      const controller = new AbortController();
      useFetch(async (url, init) => {
        request = { url, init };
        return response(200, { ok: true, configState: "found", config });
      });
      assert.deepEqual(await loadUserAccountConfig(controller.signal), {
        status: "found",
        config,
      });
      assert.deepEqual(
        [
          request.url,
          request.init?.method,
          request.init?.credentials,
          request.init?.cache,
          request.init?.signal,
        ],
        [
          "/api/user/config",
          "GET",
          "include",
          "no-store",
          controller.signal,
        ],
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

    await test("workspace POST omits server-owned onboarding and owner fields", async () => {
      let postedBody: unknown = null;
      useFetch(async (_url, init) => {
        postedBody = JSON.parse(String(init?.body));
        return savedResponse(init);
      });
      const projected = projectWorkspaceUserAccountConfigForSave({
        v: 1,
        email: "owner@example.com",
        updatedAt: "2026-07-23T12:00:00Z",
        onboardingSession: {
          completed: true,
          state: { selectedInboxes: ["demo"] },
        },
        managedInboxes: [
          {
            id: "imap-server-1",
            onboardingInboxId: "demo",
          },
        ],
        uiPreferences: { themeMode: "Dark" },
      });

      assert.deepEqual(projected, {
        managedInboxes: [
          {
            id: "imap-server-1",
            onboardingInboxId: "demo",
          },
        ],
        uiPreferences: { themeMode: "Dark" },
      });
      assert.equal((await saveUserAccountConfig(projected)).status, "found");
      assert.deepEqual(postedBody, { config: projected });
      assert.equal(JSON.stringify(postedBody).includes("onboardingSession"), false);
      assert.equal(JSON.stringify(postedBody).includes("owner@example.com"), false);
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

    await test("workspace save queue retries conflicts boundedly and supersedes stale retry state", async () => {
      const conflictResult = {
        status: "conflict" as const,
        error: {
          code: "user_config_write_conflict",
          message: "retry",
        },
      };
      const scheduled: Array<{
        handle: number;
        callback: () => void;
        delayMs: number;
        cancelled: boolean;
      }> = [];
      let nextHandle = 1;
      const scheduleRetry = (callback: () => void, delayMs: number) => {
        const handle = nextHandle;
        nextHandle += 1;
        scheduled.push({ handle, callback, delayMs, cancelled: false });
        return handle;
      };
      const cancelRetry = (handle: number) => {
        const pending = scheduled.find((entry) => entry.handle === handle);
        if (pending) pending.cancelled = true;
      };
      let conflictCalls = 0;
      const boundedQueue = createUserAccountConfigConflictRetryQueue({
        save: async () => {
          conflictCalls += 1;
          return conflictResult;
        },
        scheduleRetry,
        cancelRetry,
      });
      boundedQueue.enqueue({ onboardingSession: {} });
      await flushAsyncWork();
      assert.equal(conflictCalls, 1);
      assert.equal(scheduled[0].delayMs, 120);
      scheduled[0].callback();
      await flushAsyncWork();
      assert.equal(conflictCalls, 2);
      assert.equal(scheduled[1].delayMs, 320);
      scheduled[1].callback();
      await flushAsyncWork();
      assert.equal(conflictCalls, 3);
      assert.equal(scheduled.length, 2);
      assert.equal(boundedQueue.isDirty(), true);

      const supersedingScheduled: typeof scheduled = [];
      const savedConfigs: UserAccountConfig[] = [];
      const supersedingQueue = createUserAccountConfigConflictRetryQueue({
        save: async (config) => {
          savedConfigs.push(config);
          return savedConfigs.length === 1
            ? conflictResult
            : { status: "found" as const, config };
        },
        scheduleRetry: (callback, delayMs) => {
          const handle = nextHandle;
          nextHandle += 1;
          supersedingScheduled.push({
            handle,
            callback,
            delayMs,
            cancelled: false,
          });
          return handle;
        },
        cancelRetry: (handle) => {
          const pending = supersedingScheduled.find(
            (entry) => entry.handle === handle,
          );
          if (pending) pending.cancelled = true;
        },
      });
      supersedingQueue.enqueue({
        onboardingSession: { currentStep: 1 },
      });
      await flushAsyncWork();
      assert.equal(supersedingScheduled.length, 1);
      supersedingQueue.supersede();
      assert.equal(supersedingScheduled[0].cancelled, true);
      supersedingQueue.enqueue({
        onboardingSession: { currentStep: 3 },
        managedInboxes: [{ id: "imap-server-1" }],
      });
      supersedingScheduled[0].callback();
      await flushAsyncWork();
      assert.equal(savedConfigs.length, 2);
      assert.deepEqual(savedConfigs[1], {
        onboardingSession: { currentStep: 3 },
        managedInboxes: [{ id: "imap-server-1" }],
      });
      assert.equal(JSON.stringify(savedConfigs).includes("password"), false);
      assert.equal(supersedingQueue.isDirty(), false);
    });

    await test("failed POST responses", async () => {
      useFetch(async () => response(409, {
        ok: false,
        error: {
          code: "user_config_write_conflict",
          message: "retry",
        },
      }));
      assert.deepEqual(await saveUserAccountConfig({}), {
        status: "conflict",
        error: {
          code: "user_config_write_conflict",
          message: "retry",
        },
      });
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
