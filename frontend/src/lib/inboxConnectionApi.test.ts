import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

let fetchCalls: Array<{ url: string; init?: RequestInit }> = [];
const originalFetch = globalThis.fetch;

globalThis.fetch = (async (url: string, init?: RequestInit) => {
  fetchCalls.push({ url, init });
  return {
    ok: true,
    json: async () => ({
      ok: true,
      providerThreadId: "thread-1",
      messages: [],
    }),
  } as Response;
}) as typeof fetch;

const api = require("./inboxConnectionApi") as typeof import("./inboxConnectionApi");
assert.equal(fetchCalls.length, 0, "importing the API client must not fetch");

async function run() {
  const request = { mailboxId: "mailbox-1", providerThreadId: "thread-1" };
  const success = await api.fetchGmailThread(request);
  assert.deepEqual(success, { ok: true, providerThreadId: "thread-1", messages: [] });
  assert.equal(fetchCalls.length, 1);
  assert.equal(fetchCalls[0].url, "/api/inboxes/fetch-gmail-thread");
  assert.equal(fetchCalls[0].init?.method, "POST");
  assert.equal(fetchCalls[0].init?.credentials, "include");
  assert.equal(fetchCalls[0].init?.cache, "no-store");
  assert.deepEqual(fetchCalls[0].init?.headers, { "Content-Type": "application/json" });
  assert.equal(fetchCalls[0].init?.body, JSON.stringify(request));

  globalThis.fetch = (async () => ({
    ok: false,
    json: async () => ({
      ok: false,
      error: { code: "gmail_thread_not_found", message: "Not found." },
    }),
  })) as typeof fetch;
  assert.deepEqual(await api.fetchGmailThread(request), {
    ok: false,
    error: { code: "gmail_thread_not_found", message: "Not found." },
  });

  let networkAttempts = 0;
  globalThis.fetch = (async () => {
    networkAttempts += 1;
    throw new Error("offline");
  }) as typeof fetch;
  assert.deepEqual(await api.fetchGmailThread(request), {
    ok: false,
    error: { code: "gmail_thread_fetch_failed", message: "offline" },
  });
  assert.equal(networkAttempts, 1, "the client must not retry");

  const sourceRoot = path.resolve("src");
  const productionFiles: string[] = [];
  const collect = (directory: string) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const fullPath = path.join(directory, entry.name);
      if (entry.isDirectory()) collect(fullPath);
      else if (/\.(ts|tsx)$/.test(entry.name) && !/\.test\.(ts|tsx)$/.test(entry.name)) {
        productionFiles.push(fullPath);
      }
    }
  };
  collect(sourceRoot);
  const productionSources = productionFiles.map((file) => ({
    file,
    source: fs
      .readFileSync(file, "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/\/\/.*$/gm, ""),
  }));
  const callLikeOccurrences = productionSources.flatMap(({ file, source }) =>
    [...source.matchAll(/\bfetchGmailThread\s*\(/g)].map((match) => ({
      file,
      index: match.index,
    })),
  );
  const clientFile = path.join(sourceRoot, "lib", "inboxConnectionApi.ts");
  const clientSource = productionSources.find(({ file }) => file === clientFile)!.source;
  const declarationMatch = /export\s+async\s+function\s+fetchGmailThread\s*\(/.exec(
    clientSource,
  );
  assert.ok(declarationMatch, "fetchGmailThread declaration must exist");
  const declarationNameIndex =
    declarationMatch.index + declarationMatch[0].indexOf("fetchGmailThread");
  assert.deepEqual(
    callLikeOccurrences,
    [{ file: clientFile, index: declarationNameIndex }],
    "fetchGmailThread must have exactly one production call-like occurrence",
  );

  const importSites = productionSources.filter(({ source }) =>
    [...source.matchAll(
      /\bimport\s+(?:type\s+)?(?:\{[\s\S]*?\}|\*\s+as\s+\w+|\w+)\s+from\s+["'][^"']+["']/g,
    )].some((match) => /\bfetchGmailThread\b/.test(match[0])),
  );
  assert.deepEqual(importSites, [], "fetchGmailThread must not be imported in production");
}

run()
  .then(() => console.log("\n✓ inboxConnectionApi Gmail thread client tests passed."))
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  })
  .finally(() => {
    globalThis.fetch = originalFetch;
  });
