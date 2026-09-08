import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
import { createCollaborationSummaryStore } from "./collaborationSummaryStore";
import { isActiveCollaborationSummary } from "./collaborationSummaryApi";

// Deterministic hook scheduling seam: render before effects, then commit/cleanup.
// Execute the real hook and store so identity changes are tested before hydration.
const slots: any[] = [];
let slot = 0;
let effects: (() => void)[] = [];
let calls = 0;
const pending: ((value: any) => void)[] = [];
const equal = (a: any[], b: any[]) => a?.length === b.length && a.every((v, i) => Object.is(v, b[i]));
const react = {
  useMemo: (fn: () => any, deps: any[]) => {
    const key = slot++;
    if (!slots[key] || !equal(slots[key].deps, deps)) slots[key] = { deps, value: fn() };
    return slots[key].value;
  },
  useRef: (value: any) => { const key = slot++; return slots[key] ?? (slots[key] = { current: value }); },
  useSyncExternalStore: (_: any, getSnapshot: () => any) => { slot++; return getSnapshot(); },
  useEffect: (fn: () => any, deps: any[]) => {
    const key = slot++;
    if (!slots[key] || !equal(slots[key].deps, deps)) {
      const previous = slots[key]; slots[key] = { deps };
      effects.push(() => { previous?.cleanup?.(); slots[key].cleanup = fn(); });
    }
  },
};
const source = readFileSync("src/lib/useCollaborationSummaries.ts", "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const context = { exports: {} as any, require: (name: string) => name === "react" ? react : {
  isActiveCollaborationSummary,
  createCollaborationSummaryStore: (workspace: string | null) => createCollaborationSummaryStore(workspace, async () => {
    calls++; return new Promise(resolve => pending.push(resolve));
  }),
} };
vm.runInNewContext(compiled, context);
const workspaceId = `wsp_${"W".repeat(22)}`;
const user = `usr_${"U".repeat(22)}`;
const summary = { collaborationId: "A".repeat(22), workspaceId, mailboxId: "mailbox-a",
  sourceRef: { provider: "google", providerMessageId: "gmail-exact" }, state: "note_only", updatedAt: 1_800_000_000_000, viewerAccess: "owner" };
const response = { status: "success", page: { v: 1, workspaceId, summaries: [summary], nextCursor: null } };
const render = (workspace: string | null, viewer: string | null) => { slot = 0; return context.exports.useCollaborationSummaries(workspace, viewer); };
const commit = () => { const work = effects; effects = []; work.forEach(fn => fn()); };
const settle = () => new Promise(resolve => setImmediate(resolve));

async function run() {
  let view = render(workspaceId, user);
  assert.equal(view.index.size, 0); assert.equal(calls, 0);
  commit(); assert.equal(calls, 1);
  pending.shift()!(response); await settle();
  view = render(workspaceId, user); assert.equal(view.index.size, 1);
  view.acceptMutation({ ...summary, state: "resolved", updatedAt: summary.updatedAt + 1 });
  view = render(workspaceId, user); assert.equal(view.index.size, 0, "resolved summaries leave the active lookup index");
  view.acceptMutation({ ...summary, state: "note_only", updatedAt: summary.updatedAt + 2 });
  view = render(workspaceId, user); assert.equal(view.index.size, 1, "reopen projects without another list request");
  assert.equal(calls, 1);
  const oldMutationCallback = view.acceptMutation;
  for (let i = 0; i < 100; i++) { render(workspaceId, user); commit(); }
  assert.equal(calls, 1, "row selection/render does not hydrate again");
  const otherUser = `usr_${"V".repeat(22)}`;
  view = render(workspaceId, otherUser);
  assert.equal(view.index.size, 0, "account switch clears during render, before effects");
  oldMutationCallback({ ...summary, collaborationId: "B".repeat(22) });
  assert.equal(calls, 1, "old mutation callback cannot refresh after scope changes");
  commit(); assert.equal(calls, 2);
  const stale = pending.shift()!;
  const otherWorkspace = `wsp_${"X".repeat(22)}`;
  view = render(otherWorkspace, otherUser); assert.equal(view.index.size, 0);
  commit(); assert.equal(calls, 3);
  stale(response); await settle();
  assert.equal(render(otherWorkspace, otherUser).index.size, 0, "stale in-flight account response discarded");
  pending.shift()!({ status: "network_failure" }); await settle();
  assert.equal(render(otherWorkspace, otherUser).index.size, 0);
  view = render(workspaceId, user); assert.equal(view.index.size, 0, "A→B→A does not resurrect cached authority");
  commit(); assert.equal(calls, 4);
  pending.shift()!(response); await settle();
  assert.equal(render(workspaceId, user).index.size, 1);
  view = render(null, null); assert.equal(view.index.size, 0); commit(); assert.equal(calls, 4);
  assert.doesNotMatch(source, /setInterval|setTimeout|addEventListener|localStorage|sessionStorage|startTransition|useDeferredValue/);
  console.log("PASS Collaboration hydration hook: synchronous identity reset, A→B→A, stale callbacks/responses, one hydration, no polling");
}
run().catch(error => { console.error(error); process.exitCode = 1; });
