const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const React = require("react");
const { act, create } = require("react-test-renderer");
const { transform } = require("sucrase");
require("sucrase/register/ts");
require("sucrase/register/tsx");

// Exercise the actual view, editors and HTTP clients. Only the unrelated
// learning-storage hook is replaced; every HTTP request stays in this fixture.
const learning = require("./useMicrosoftFilteredLearning");
learning.useMicrosoftFilteredLearning = () => ({
  learned: {}, placements: {}, rules: {}, ready: true, error: null,
});
const filename = path.join(__dirname, "MicrosoftMailboxReadOnlyView.tsx");
const source = readFileSync(filename, "utf8");
const gate = "const MICROSOFT_DRAFT_EDIT_ACTIONS_RELEASED = false;";
assert.equal(source.split(gate).length, 2, "the real edit release gate must remain false");

// The enabled variant exists only in memory, with mocked HTTP. No release gate
// is changed on disk or introduced as a runtime/environment override.
const fixtureModule = new Module(filename, module);
fixtureModule.filename = filename;
fixtureModule.paths = Module._nodeModulePaths(__dirname);
fixtureModule._compile(transform(source.replace(gate,
  "const MICROSOFT_DRAFT_EDIT_ACTIONS_RELEASED = true;"), {
  transforms: ["typescript", "jsx", "imports"], filePath: filename,
}).code, filename);
const EnabledView = fixtureModule.exports.MicrosoftMailboxReadOnlyView;
const discardGate = "const MICROSOFT_DRAFT_DISCARD_ACTIONS_RELEASED = false;";
assert.equal(source.split(discardGate).length, 2, "real discard gate stays false");
const discardModule = new Module(filename, module);
discardModule.filename = filename;
discardModule.paths = Module._nodeModulePaths(__dirname);
discardModule._compile(transform(source.replace(discardGate,
  "const MICROSOFT_DRAFT_DISCARD_ACTIONS_RELEASED = true;"), {
  transforms: ["typescript", "jsx", "imports"], filePath: filename,
}).code, filename);
const DiscardView = discardModule.exports.MicrosoftMailboxReadOnlyView;
const DisabledView = require("./MicrosoftMailboxReadOnlyView").MicrosoftMailboxReadOnlyView;
const { MicrosoftMessageListRow } = require("./MicrosoftMessageListRow");
const { MicrosoftMailboxFolderRail } = require("./MicrosoftMailboxFolderRail");
const { MicrosoftDraftEditor } = require("./MicrosoftDraftEditor");
const { MicrosoftRichDraftMetadataEditor } = require("./MicrosoftRichDraftMetadataEditor");

const BOX = "microsoft-0123456789abcdefabcd12345678";
const ID = "AAMkDraftMessage.1/";
const OTHER = "AAMkDraftMessage.2/";
const ETAG = 'W/"original-version"';
const HTML = "<p>Original <strong>HTML body</strong>.</p>";
const response = (payload, status = 200) => ({
  ok: status >= 200 && status < 300, status, json: async () => payload,
});
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}
function transport(kind, options = {}) {
  const calls = [];
  const save = deferred();
  const exact = deferred();
  const discard = deferred();
  let submitted;
  let saved = false;
  const original = { subject: "Original subject", to: ["artist@example.com"], cc: options.cc ?? [], body: "Original plain body." };
  function snapshot(id, full) {
    const edit = id === ID && saved ? submitted.edit : original;
    return {
      id, providerMessageId: id, providerThreadId: id + "thread",
      serverMailboxId: BOX, providerFolder: "DRAFTS", labelIds: ["DRAFTS"],
      sender: "To: " + edit.to.join(", "), from: "", to: edit.to.join(", "), cc: edit.cc.join(", "),
      subject: id === OTHER ? "Other selected draft" : edit.subject, snippet: "",
      timestamp: "October 22 at 12:00", createdAt: "2026-10-22T12:00:00+00:00",
      body: full ? [kind === "rich" ? "Original HTML body." : edit.body] : [],
      ...(full && kind === "rich" ? { bodyHtml: HTML } : {}),
      unread: false, flagged: false, attachments: [], hasAttachments: false,
    };
  }
  function editorPayload(edit = original, changed) {
    return {
      ok: true, providerMessageId: ID, etag: changed === undefined ? ETAG : 'W/"saved-version"',
      ...(kind === "rich"
        ? { metadata: { subject: edit.subject, to: edit.to, cc: edit.cc }, bodyPreview: "Original HTML body." }
        : { edit }),
      ...(changed === undefined ? {} : { changed }),
    };
  }
  const fetch = async (url, init) => {
    const payload = JSON.parse(init.body);
    calls.push({ url, init, payload });
    assert.equal(init.method, "POST");
    assert.equal(init.credentials, "include");
    assert.equal(payload.mailboxId, BOX);
    if (url === "/api/inboxes/fetch-microsoft") {
      return response({ ok: true, messages: [], inboxUidSet: [], uidValidity: "microsoft-graph-immutable", hasMore: false });
    }
    if (url === "/api/inboxes/fetch-microsoft-drafts") {
      return response({ ok: true, messages: [snapshot(ID, false), snapshot(OTHER, false)], draftMessageIds: [ID, OTHER], hasMore: false });
    }
    if (url === "/api/inboxes/fetch-microsoft-draft-message") {
      assert.ok([ID, OTHER].includes(payload.providerMessageId));
      if (saved && payload.providerMessageId === ID && options.holdSavedDetail) return exact.promise;
      return response({ ok: true, message: snapshot(payload.providerMessageId, true) });
    }
    if (url === "/api/inboxes/discard-microsoft-draft") {
      assert.equal(payload.providerMessageId, ID);
      return discard.promise;
    }
    if (url === "/api/inboxes/edit-microsoft-draft") {
      assert.equal(payload.providerMessageId, ID);
      if (payload.action === (kind === "rich" ? "load_rich" : "load")) return response(editorPayload());
      assert.equal(payload.action, kind === "rich" ? "save_rich" : "save");
      assert.equal(submitted, undefined, "never submit a second mutation");
      submitted = payload;
      return save.promise;
    }
    assert.fail("Unexpected HTTP endpoint: " + url);
  };
  return {
    fetch, calls,
    completeDiscard(code, status = 503) {
      discard.resolve(code
        ? response({ ok: false, error: { code, message: "untrusted provider text" } }, status)
        : response({ ok: true, providerMessageId: ID, discarded: true }));
    },
    get submitted() { return submitted; },
    completeSave(code) {
      if (code) save.resolve(response({ ok: false, error: { code } }, 409));
      else {
        saved = true;
        save.resolve(response(editorPayload(submitted.edit, true)));
      }
    },
    completeDetail(code) {
      exact.resolve(code
        ? response({ ok: false, error: { code } }, 502)
        : response({ ok: true, message: snapshot(ID, true) }));
    },
  };
}
const textOf = node => typeof node === "string" ? node : (node.children || []).map(textOf).join("");
const renderedText = tree => textOf(tree.toJSON());
const button = (tree, label) => tree.root.findAllByType("button").find(node => textOf(node) === label);
const editType = kind => kind === "rich" ? MicrosoftRichDraftMetadataEditor : MicrosoftDraftEditor;
const fieldLabels = kind => kind === "rich"
  ? ["HTML draft recipients", "HTML draft carbon copy recipients", "HTML draft subject"]
  : ["Draft recipients", "Draft carbon copy recipients", "Draft subject"];
const saveLabel = kind => kind === "rich" ? "Save draft details" : "Save draft";
const edits = fixture => fixture.calls.filter(call => call.payload.action?.startsWith("save"));
const details = fixture => fixture.calls.filter(call => call.url.endsWith("fetch-microsoft-draft-message"));

async function withMailbox(kind, options, check, View = EnabledView) {
  const oldFetch = globalThis.fetch;
  const oldWindow = globalThis.window;
  const fixture = transport(kind, options);
  globalThis.fetch = fixture.fetch;
  globalThis.window = { setInterval: () => 1, clearInterval: () => {} };
  let tree;
  try {
    await act(async () => { tree = create(React.createElement(View, {
      mailbox: { id: BOX, title: "Microsoft test mailbox", email: "owner@example.com" },
      connected: true, onBack() {}, onOpenConnectionSettings() {},
    })); });
    await act(async () => tree.root.findAllByType(MicrosoftMailboxFolderRail)[0].props.onSelectFolder("Drafts"));
    await act(async () => tree.root.findAllByType(MicrosoftMessageListRow)[0].props.onOpenMessage(ID));
    assert.equal(details(fixture).length, 1);
    await check(tree, fixture);
  } finally {
    if (tree) await act(async () => tree.unmount());
    globalThis.fetch = oldFetch;
    globalThis.window = oldWindow;
  }
}
async function openEditor(tree, kind) {
  await act(async () => button(tree, kind === "rich" ? "Edit subject and recipients" : "Edit draft").props.onClick());
  const labels = fieldLabels(kind);
  assert.equal(tree.root.findByProps({ "aria-label": labels[2] }).props.value, "Original subject");
  assert.equal(tree.root.findAllByType(editType(kind)).length, 1);
}
async function changeFields(tree, kind) {
  const labels = fieldLabels(kind);
  for (const [label, value] of [[labels[0], "updated@example.com"], [labels[1], "copy@example.com"], [labels[2], "Saved subject"]]) {
    await act(async () => tree.root.findByProps({ "aria-label": label }).props.onChange({ target: { value } }));
  }
  if (kind === "plain") await act(async () => tree.root.findByProps({ "aria-label": "Draft message" }).props.onChange({ target: { value: "Saved plain body." } }));
}

test("real source gates keep both draft editors unavailable", async () => {
  for (const kind of ["plain", "rich"]) await withMailbox(kind, {}, async (tree, fixture) => {
    assert.equal(button(tree, "Discard draft"), undefined);
    assert.equal(button(tree, "Edit draft"), undefined);
    assert.equal(button(tree, "Edit subject and recipients"), undefined);
    assert.equal(fixture.calls.some(call => call.url.includes("edit-microsoft")), false);
  }, DisabledView);
});

for (const kind of ["plain", "rich"]) {
  test(kind + " editor saves once with ETag and reloads the selected detail", async () => {
    await withMailbox(kind, { holdSavedDetail: true }, async (tree, fixture) => {
      await openEditor(tree, kind);
      await changeFields(tree, kind);
      const click = button(tree, saveLabel(kind)).props.onClick;
      await act(async () => { click(); click(); });
      assert.equal(edits(fixture).length, 1);
      assert.deepEqual(fixture.submitted, {
        action: kind === "rich" ? "save_rich" : "save", mailboxId: BOX, providerMessageId: ID, etag: ETAG,
        edit: { subject: "Saved subject", to: ["updated@example.com"], cc: ["copy@example.com"],
          ...(kind === "plain" ? { body: "Saved plain body." } : {}) },
      });
      assert.equal(button(tree, "Saving…").props.disabled, true);
      await act(async () => fixture.completeSave());
      assert.equal(tree.root.findAllByType(editType(kind)).length, 0);
      assert.equal(details(fixture).length, 2, "a list refresh alone must not retain the stale reader");
      assert.doesNotMatch(renderedText(tree), /Original subject|Original plain body/);
      await act(async () => fixture.completeDetail());
      const heading = tree.root.findAll(node => (node.type === "h2" || node.type === "h3") && textOf(node) === "Saved subject")[0];
      assert.ok(heading, "selected reader displays the confirmed saved subject");
      assert.match(renderedText(tree), /updated@example.com/);
      assert.match(renderedText(tree), /Cc: copy@example.com/);
      if (kind === "plain") assert.match(renderedText(tree), /Saved plain body/);
      else {
        assert.equal(fixture.submitted.edit.body, undefined, "HTML is never submitted by metadata editor");
        assert.match(renderedText(tree), /Original HTML body/);
      }
      assert.equal(edits(fixture).length, 1);
    });
  });

  test(kind + " editor rejects invalid input before submitting", async () => {
    await withMailbox(kind, {}, async (tree, fixture) => {
      await openEditor(tree, kind);
      await act(async () => tree.root.findByProps({ "aria-label": fieldLabels(kind)[0] }).props.onChange({ target: { value: "not an email" } }));
      await act(async () => button(tree, saveLabel(kind)).props.onClick());
      assert.equal(edits(fixture).length, 0);
      assert.equal(button(tree, saveLabel(kind)).props.disabled, false);
      assert.match(renderedText(tree), /Check your/);
    });
  });

  for (const code of ["microsoft_draft_edit_conflict", "microsoft_draft_edit_unconfirmed"]) {
    test(kind + " editor stays locked without retry after " + code, async () => {
      await withMailbox(kind, {}, async (tree, fixture) => {
        await openEditor(tree, kind);
        await act(async () => button(tree, saveLabel(kind)).props.onClick());
        await act(async () => fixture.completeSave(code));
        const locked = button(tree, "Reopen to verify save");
        assert.equal(locked.props.disabled, true);
        await act(async () => locked.props.onClick());
        assert.equal(edits(fixture).length, 1);
        assert.equal(details(fixture).length, 1);
        assert.match(renderedText(tree), code.endsWith("conflict") ? /changed in Outlook/ : /Save status is uncertain/);
      });
    });
  }

  test(kind + " save completion does not replace a newer selection", async () => {
    await withMailbox(kind, {}, async (tree, fixture) => {
      await openEditor(tree, kind);
      await act(async () => button(tree, saveLabel(kind)).props.onClick());
      await act(async () => tree.root.findAllByType(MicrosoftMessageListRow)[1].props.onOpenMessage(OTHER));
      await act(async () => fixture.completeSave());
      assert.deepEqual(details(fixture).map(call => call.payload.providerMessageId), [ID, OTHER]);
      assert.match(renderedText(tree), /Other selected draft/);
      assert.equal(tree.root.findAllByType(MicrosoftMessageListRow)[1].props.selected, true);
      assert.equal(edits(fixture).length, 1);
    });
  });

  test(kind + " late save completion cannot reopen an editor after a folder switch", async () => {
    await withMailbox(kind, {}, async (tree, fixture) => {
      await openEditor(tree, kind);
      await act(async () => button(tree, saveLabel(kind)).props.onClick());
      await act(async () => tree.root.findAllByType(MicrosoftMailboxFolderRail)[0].props.onSelectFolder("Inbox"));
      await act(async () => fixture.completeSave());
      assert.equal(tree.root.findAllByType(editType(kind)).length, 0);
      assert.equal(details(fixture).length, 1);
      assert.equal(edits(fixture).length, 1);
      assert.equal(tree.root.findAllByType(MicrosoftMailboxFolderRail)[0].props.folder, "Inbox");
    });
  });

  test(kind + " failed detail reload leaves no old body and never repeats the save", async () => {
    await withMailbox(kind, { holdSavedDetail: true }, async (tree, fixture) => {
      await openEditor(tree, kind);
      await changeFields(tree, kind);
      await act(async () => button(tree, saveLabel(kind)).props.onClick());
      await act(async () => fixture.completeSave());
      await act(async () => fixture.completeDetail("microsoft_graph_unavailable"));
      assert.doesNotMatch(renderedText(tree), /Original subject|Original plain body/);
      assert.match(renderedText(tree), /could not be opened/);
      assert.equal(details(fixture).length, 2);
      assert.equal(edits(fixture).length, 1);
    });
  });
}


test("ordinary text and HTML Drafts readers show existing Cc with all write gates disabled", async () => {
  for (const kind of ["plain", "rich"]) await withMailbox(kind,
    { cc: ["manager@example.com", "copy@example.com"] }, async (tree, fixture) => {
      assert.match(renderedText(tree), /To: artist@example.com/);
      assert.match(renderedText(tree), /Cc: manager@example.com, copy@example.com/);
      assert.equal(button(tree, "Edit draft"), undefined);
      assert.equal(button(tree, "Discard draft"), undefined);
      assert.equal(fixture.calls.some(call => /edit-microsoft|discard-microsoft/.test(call.url)), false);
    }, DisabledView);
});

test("Drafts reader omits Cc when absent", async () => {
  await withMailbox("plain", {}, async tree => {
    assert.doesNotMatch(renderedText(tree), /Cc:/);
  }, DisabledView);
});

const discards = fixture => fixture.calls.filter(call => call.url.endsWith("discard-microsoft-draft"));
test("Discard confirms a move once before removing the row and showing success", async () => {
  await withMailbox("plain", {}, async (tree, fixture) => {
    const click = button(tree, "Discard draft").props.onClick;
    await act(async () => { click(); click(); });
    assert.equal(discards(fixture).length, 1);
    assert.equal(button(tree, "Discarding…").props.disabled, true);
    assert.match(renderedText(tree), /Original subject/);
    await act(async () => fixture.completeDiscard());
    assert.doesNotMatch(renderedText(tree), /Original subject/);
    assert.match(renderedText(tree), /Draft moved to Deleted Items/);
    assert.equal(tree.root.findAllByType(MicrosoftMessageListRow).length, 1);
    assert.equal(discards(fixture).length, 1);
  }, DiscardView);
});

for (const [code, status, uncertain] of [
  ["microsoft_draft_discard_not_performed", 409, false],
  ["microsoft_mutation_unconfirmed", 503, true],
  ["microsoft_graph_unavailable", 503, true],
]) test("Discard feedback and retry guard for " + code, async () => {
  await withMailbox("plain", {}, async (tree, fixture) => {
    await act(async () => button(tree, "Discard draft").props.onClick());
    await act(async () => fixture.completeDiscard(code, status));
    assert.equal(discards(fixture).length, 1);
    assert.match(renderedText(tree), /Original subject/);
    assert.match(renderedText(tree), uncertain ? /Discard status is uncertain/ : /Microsoft did not discard the draft/);
    assert.doesNotMatch(renderedText(tree), /untrusted provider text|Draft moved to Deleted Items/);
    assert.equal(button(tree, "Discard draft").props.disabled, uncertain);
    if (uncertain) {
      // A direct handler call, list refresh or leaving/reopening the folder
      // must not silently clear an uncertain operation or repeat its POST.
      await act(async () => button(tree, "Discard draft").props.onClick());
      await act(async () => tree.root.findAllByType(MicrosoftMailboxFolderRail)[0].props.onSelectFolder("Inbox"));
      await act(async () => tree.root.findAllByType(MicrosoftMailboxFolderRail)[0].props.onSelectFolder("Drafts"));
      await act(async () => tree.root.findAllByType(MicrosoftMessageListRow)[0].props.onOpenMessage(ID));
      assert.equal(button(tree, "Discard draft").props.disabled, true);
      assert.match(renderedText(tree), /Check Drafts and Deleted Items in Outlook/);
      await act(async () => button(tree, "Discard draft").props.onClick());
      assert.equal(discards(fixture).length, 1);
    }
  }, DiscardView);
});
