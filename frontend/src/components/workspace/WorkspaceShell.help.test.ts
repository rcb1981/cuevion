export {};

declare const require: (name: string) => any;
declare const __dirname: string;

const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { createHash } = require("node:crypto");
const vm = require("node:vm");
const ts = require("typescript");
const React = require("react");

// Evaluate only the Help declarations and UtilityView, never WorkspaceShell's
// imports. In particular this test must not load inboxConnectionApi or a backend.
const shellSource = readFileSync(resolve(__dirname, "WorkspaceShell.tsx"), "utf8");
const appSource = readFileSync(resolve(__dirname, "../../App.tsx"), "utf8");
const configSource = readFileSync(resolve(__dirname, "../../lib/userConfigApi.ts"), "utf8");
const parse = (source: string) =>
  ts.createSourceFile("subject.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const shellAst = parse(shellSource);
const appAst = parse(appSource);
const configAst = parse(configSource);
const namedFunction = (ast: any, name: string) => {
  const node = ast.statements.find(
    (item: any) => ts.isFunctionDeclaration(item) && item.name?.text === name,
  );
  if (!node) throw new Error(`Missing function: ${name}`);
  return node;
};
const namedVariable = (statements: any, name: string) => {
  for (const statement of statements) {
    if (!ts.isVariableStatement(statement)) continue;
    const declaration = statement.declarationList.declarations.find(
      (item: any) => ts.isIdentifier(item.name) && item.name.text === name,
    );
    if (declaration) return declaration;
  }
  throw new Error(`Missing variable: ${name}`);
};
const evaluate = (source: string, scope: Record<string, any> = {}) => {
  const context = { exports: {}, React, ...scope };
  const compiled = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React },
  }).outputText;
  vm.runInNewContext(compiled, context, { timeout: 5_000 });
  return context.exports as any;
};
const utilityNode = namedFunction(shellAst, "UtilityView");
const utilitySource = utilityNode.getText(shellAst);
const helpSource = ["helpTopics", "helpTopicSections"]
  .map((name) => `const ${namedVariable(shellAst.statements, name).getText(shellAst)};`)
  .join("\n");
const { topics } = evaluate(`${helpSource}\nexports.topics = helpTopics;`);
type HelpTopic = {
  id: string;
  title: string;
  section: string;
  intro: string;
  points: string[];
  tip?: string;
  keywords: string[];
  previousTitles?: string[];
};
const helpTopics = topics as HelpTopic[];
const currentTitles = [
  "How Cuevion organizes your inbox",
  "Working with Priority",
  "Understanding AI suggestions",
  "Working with conversations",
  "Collaborating with your team",
  "Managing your inbox settings",
  "Fixing sync or connection issues",
];
const priorTitles = [
  "Getting started",
  "Navigation overview",
  "Managing connected inboxes",
  "Gmail and Google reconnect",
  "Custom IMAP setup",
  "Working with messages",
  "Private beta mailbox actions",
  "Priority, For You, and AI suggestions",
  "Smart Folders",
  "Team and collaboration",
  "Settings and signatures",
  "Troubleshooting sync and connection issues",
  "Gmail needs reconnecting",
  "Custom IMAP will not connect",
  "Messages are not showing",
  "An email appears in the wrong category",
  "I changed inbox settings but do not see the result",
  "Testing Cuevion",
];
const priorIds = new Set([
  "getting-started", "navigation-overview", "connected-inboxes", "gmail-google-reconnect",
  "custom-imap", "working-with-messages", "private-beta-mailbox-actions", "priority-for-you-ai",
  "smart-folders", "team-collaboration", "settings-signatures", "troubleshooting-sync",
  "gmail-needs-reconnecting", "custom-imap-will-not-connect", "messages-not-showing",
  "wrong-category", "settings-change-not-visible", "testing-cuevion",
]);
const topic = (title: string) => {
  const result = helpTopics.find((item) => item.title === title);
  if (!result) throw new Error(`Missing Help topic: ${title}`);
  return result;
};
const copy = (item: HelpTopic) => [item.title, item.intro, ...item.points, item.tip ?? ""].join(" ");
const allHelpCopy = helpTopics.map(copy).join(" ");
const elements = (node: any): any[] => {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!node || typeof node !== "object" || !("props" in node)) return [];
  return [node, ...elements(node.props.children)];
};
const textContent = (node: any): string => {
  if (Array.isArray(node)) return node.map(textContent).join(" ").replace(/\s+/g, " ").trim();
  if (typeof node === "string" || typeof node === "number") return String(node);
  return node?.props ? textContent(node.props.children) : "";
};
const isCard = (node: any) =>
  node.type === "div" && node.props.className?.includes("bg-[var(--workspace-help-guidance-surface)]");
const isSelectedCard = (node: any) =>
  node.type === "div" && node.props.className?.includes("bg-[var(--workspace-help-guidance-surface-selected)]");
const displayedTitles = (tree: any) => elements(tree).filter(isCard).map((node) =>
  helpTopics.find((item) => textContent(node).startsWith(item.title))?.title,
).sort();
let externalCalls = 0;
const rejectExternalCall = () => {
  externalCalls += 1;
  throw new Error("Help attempted an external call");
};

// A small synchronous hook harness rerenders the real component after each
// actual handler. Contact storage is an empty fixture; its effects do not run.
// This tests React element structure and handlers, not browser rendering.
const mountHelp = (lastViewedGuidance: string | null = null, initialSelection?: string) => {
  const state: any[] = [];
  if (initialSelection !== undefined) state[1] = initialSelection;
  let hook = 0;
  const viewed: string[] = [];
  const { UtilityView } = evaluate(`${helpSource}\n${utilitySource}\nexports.UtilityView = UtilityView;`, {
    useState(initial: any) {
      const index = hook++;
      if (!(index in state)) state[index] = typeof initial === "function" ? initial() : initial;
      return [state[index], (next: any) => {
        state[index] = typeof next === "function" ? next(state[index]) : next;
      }];
    },
    useMemo: (factory: () => any) => factory(),
    useEffect: () => undefined,
    inputFieldClass: "existing-input",
    buildContactRequestsStorageKey: () => "help-test-contact-fixture",
    resolveContactSubmittedBy: () => "Help test",
    readStoredContactRequests: () => [],
    fetch: rejectExternalCall,
    XMLHttpRequest: rejectExternalCall,
    WebSocket: rejectExternalCall,
    sendContactSupportRequest: rejectExternalCall,
    window: { fetch: rejectExternalCall, localStorage: { setItem: rejectExternalCall } },
  });
  let tree: any;
  const render = () => {
    hook = 0;
    tree = UtilityView({
      section: "Help", lastViewedGuidance,
      onSetLastViewedGuidance: (title: string) => viewed.push(title),
      primaryWorkspaceEmail: "help-test@example.invalid", workspaceName: "Help test",
      authenticatedUserName: "Help test", themeMode: "light",
    });
    return tree;
  };
  const input = () => elements(tree).find((node) => node.type === "input");
  const fire = (handler: () => void) => { handler(); return render(); };
  render();
  return {
    get tree() { return tree; }, viewed, input, fire,
    search: (value: string) => fire(() => input().props.onChange({ target: { value } })),
    focus: () => fire(() => input().props.onFocus()),
    click(label: string) {
      const button = elements(tree).find((node) => node.type === "button" && textContent(node) === label);
      if (!button) throw new Error(`Missing button: ${label}`);
      return fire(() => button.props.onClick());
    },
  };
};

let testCount = 0;
let assertionCount = 0;
const failures: string[] = [];
const check = (value: unknown, message: string) => { assertionCount += 1; assert.ok(value, message); };
const equal = (actual: unknown, expected: unknown, message: string) => {
  assertionCount += 1;
  assert.deepEqual(actual, expected, message);
};
const matches = (actual: string, expected: RegExp, message: string) => {
  assertionCount += 1;
  assert.match(actual, expected, message);
};
const test = (name: string, run: () => void) => {
  testCount += 1;
  try { run(); console.log(`PASS Help ${testCount}: ${name}`); }
  catch (error) { failures.push(name); console.error(`FAIL Help ${testCount}: ${name}`, error); }
};
const sha = (value: string) => createHash("sha256").update(value).digest("hex");

test("Help remains a sidebar utility destination", () => {
  matches(namedVariable(shellAst.statements, "utilityNavigationItems").getText(shellAst),
    /section: "Help", label: "Help", shortLabel: "Help", icon: "help"/, "Help utility entry is preserved");
  matches(shellSource, /utilityNavigationItems\.map\(renderItem\)/, "Sidebar renders utility entries");
  matches(shellSource, /<UtilityView\s+section=\{activeSection\}/, "Shell still opens UtilityView");
  equal(elements(mountHelp().tree).filter((node) => node.type === "h1").map(textContent), ["Help"], "Help title remains");
});

test("Help retains its header, section, card grid, and selected card styling", () => {
  const view = mountHelp();
  equal(view.tree.props.className, "space-y-8", "Root spacing is unchanged");
  check(elements(view.tree).some((node) => node.type === "section" && node.props.className ===
    "rounded-[30px] border border-[var(--workspace-border)] bg-[var(--workspace-card)] p-6 shadow-panel"), "Existing main panel remains");
  check(elements(view.tree).some((node) => node.props.className === "grid gap-3 lg:grid-cols-2"), "Guidance stays in the existing responsive grid");
  equal(elements(view.tree).filter(isCard).length, 7, "Seven normal guidance cards render");
  const card = elements(view.tree).find(isCard);
  view.fire(() => elements(card).find((node) => node.type === "button").props.onClick());
  equal(elements(view.tree).filter(isSelectedCard).length, 1, "Normal selected guidance card renders");
  matches(elements(view.tree).find(isSelectedCard).props.className, /rounded-\[24px\].*px-5 py-5/, "Selected card spacing and radius remain");
});

test("Seven current topics include the four requested legacy names and retain source IDs", () => {
  equal([...helpTopics.map((item) => item.title)].sort(), [...currentTitles].sort(), "Only the seven audited topics are exposed");
  equal(new Set(helpTopics.map((item) => item.id)).size, 7, "Topic IDs are unique");
  for (const item of helpTopics) {
    check(priorIds.has(item.id) || (item.title === "Working with Priority" && item.id === "working-with-priority"), `${item.title} retains its baseline ID or receives the dedicated new Priority ID`);
    check(item.points.length >= 2 && item.points.length <= 4, `${item.title} has concise guidance`);
  }
});

test("Priority explains manual attention controls and returned replies", () => {
  const value = copy(topic("Working with Priority"));
  matches(value, /attention/i, "Priority is described as attention guidance");
  matches(value, /manual|Mark as priority|Remove priority|mark.*priority/i, "Manual Priority controls are documented");
  matches(value, /repl(?:y|ies)|respond/i, "Returned-reply behavior is described");
  check(!/score|ranking engine|algorithm|semantic assessment/i.test(value), "Priority copy avoids internals");
});

test("Conversations describe related messages and usable mail actions", () => {
  const value = copy(topic("Working with conversations"));
  matches(value, /related messages|groups.*messages/i, "Related messages form conversations");
  matches(value, /reply|forward/i, "Current conversation actions are covered");
});

test("Conversation order documents the real Settings → Mail controls", () => {
  const value = copy(topic("Working with conversations"));
  for (const label of ["Settings", "Mail", "Conversation order", "Newest first", "Oldest first"]) {
    check(value.includes(label), `Conversation guidance includes ${label}`);
    check(shellSource.includes(label), `Current workspace source contains ${label}`);
  }
  matches(shellSource, /value: "newest-first", label: "Newest first"/, "Settings exposes Newest first");
  matches(shellSource, /value: "oldest-first", label: "Oldest first"/, "Settings exposes Oldest first");
});

test("Newest first is the documented and actual default", () => {
  matches(copy(topic("Working with conversations")), /(?:default[^.]*Newest first|Newest first[^.]*default)/i, "Default is explicit");
  const normalizeSource = namedFunction(configAst, "normalizeConversationOrder").getText(configAst);
  const { normalizeConversationOrder } = evaluate(normalizeSource);
  for (const value of [undefined, null, "invalid", "newest-first"]) {
    equal(normalizeConversationOrder(value), "newest-first", "Missing or unsupported preference falls back to newest first");
  }
  equal(normalizeConversationOrder("oldest-first"), "oldest-first", "Explicit oldest first is retained");
});

test("Changing conversation order is presentation only", () => {
  const value = copy(topic("Working with conversations"));
  matches(value, /display|presentation|shown|appear|read/i, "Ordering is described as presentation");
  matches(value, /(?:does not|doesn't|without|not)[^.]*chang[^.]*messages|messages[^.]*unchanged/i, "Messages themselves do not change");
});

test("Collaboration documents discussion controls and scoped MEMBER access", () => {
  const value = copy(topic("Collaborating with your team"));
  for (const pattern of [/internal/i, /shared/i, /@[^.]*mention/i, /resolv/i, /reopen/i]) {
    matches(value, pattern, `Collaboration covers ${pattern}`);
  }
  matches(value, /(?:do not|don't|does not|doesn't|without|not)[^.]*(?:full mailbox|inbox access)|full mailbox[^.]*(?:not|never)/i, "Team membership does not grant full mailbox access");
  matches(value, /explicitly shared conversations/i, "Team access is scoped to explicitly shared conversations");
});

test("Email Client Help excludes unsupported products, future features, and internals", () => {
  check(!/\bOrganizer\b|Label Copy|passkeys|coming soon|release management/i.test(allHelpCopy), "Other products and future features are absent");
  check(!/semantic assessment|authority store|hydration coordinator|ranking engine|canonical identity|server authority/i.test(allHelpCopy), "Internal terms are absent");
});

test("Old Primary and Promo navigation guidance is absent", () => {
  check(!/\bPrimary\b|\bPromo\b/i.test(allHelpCopy), "Stale lane labels are absent from displayed Help");
  const value = copy(topic("How Cuevion organizes your inbox"));
  for (const label of ["Inboxes", "Priority", "For You"]) check(value.includes(label), `${label} is covered`);
});

test("Settings and AI guidance name current controls and preserve user choice", () => {
  const value = copy(topic("Managing your inbox settings"));
  for (const label of ["Mail", "Conversation order"]) check(value.includes(label), `Settings includes ${label}`);
  for (const pattern of [/appearance|theme/i, /notification/i, /AI/i, /inbox|mailbox/i]) matches(value, pattern, `Settings covers ${pattern}`);
  const ai = copy(topic("Understanding AI suggestions"));
  matches(ai, /suggestion|recommendation/i, "AI assists the user");
  matches(ai, /learn|future/i, "Learning is described");
  matches(ai, /Settings/i, "AI settings path remains discoverable");
  matches(ai, /disable|turn[^.]*off|switch[^.]*off/i, "AI suggestions can be disabled");
});

test("Connection help distinguishes Gmail, IMAP, SMTP and the Contact path", () => {
  const value = copy(topic("Fixing sync or connection issues"));
  for (const label of ["Gmail", "IMAP", "SMTP", "Contact"]) check(value.includes(label), `Connection help includes ${label}`);
  matches(value, /Reconnect Gmail/, "The visible Gmail action is named");
  matches(value, /incoming|receiv/i, "Incoming mail is distinguished");
  matches(value, /sending|send mail|outgoing/i, "Sending configuration is distinguished");
  check(!/password\s*[:=]|token\s*[:=]|secret\s*[:=]|https?:\/\/[^\s]+:[^\s]+@/i.test(value), "No credential values are exposed");
});

test("Last-viewed titles are safe, compatible, and updated after opening", () => {
  for (const unknown of [null, "Removed guidance title"]) {
    const view = mountHelp(unknown);
    view.focus();
    equal(displayedTitles(view.tree), [...currentTitles].sort(), "Unknown last-viewed guidance leaves normal topics available");
    check(!textContent(view.tree).includes("Continue where you left off"), "Invalid stored title is not offered");
  }
  const staleSelection = mountHelp(null, "Removed guidance title");
  equal(displayedTitles(staleSelection.tree), [...currentTitles].sort(), "Invalid selected title is safe");
  for (const title of [...priorTitles, ...currentTitles]) {
    const matching = helpTopics.filter((item) => item.title === title || item.previousTitles?.includes(title));
    equal(matching.length, 1, `${title} resolves to one current topic`);
    const view = mountHelp(title);
    view.focus();
    check(textContent(view.tree).includes("Continue where you left off"), `${title} remains resumable`);
    view.click(matching[0].title);
    const selected = elements(view.tree).find(isSelectedCard);
    check(Boolean(selected), `${title} opens a normal guidance card`);
    check(textContent(selected).includes(matching[0].intro), `${title} opens updated content`);
    equal(view.viewed, [matching[0].title], "Viewing stores the current canonical title");
  }
});

test("Contact form, support copy, and persistence helpers remain untouched", () => {
  // Fingerprints captured from the authorized baseline c8bfc971. These cover
  // Contact only; Help copy and presentation can evolve independently.
  const branch = utilityNode.body.statements.find((node: any) =>
    ts.isIfStatement(node) && node.expression.getText(shellAst) === 'section === "Contact"');
  equal(sha(branch.getText(shellAst)), "b316c0dfa209157cb629b2d3a0ee331a547a6193b6762c11fb860f9330d7a00c", "Contact JSX and send handler are unchanged");
  const contact = namedVariable(utilityNode.body.statements, "content").initializer.properties.find(
    (node: any) => node.name.getText(shellAst) === "Contact",
  );
  equal(sha(contact.getText(shellAst)), "040e89e6331763b7336a7dc6162070d42ddd9a40490419216756421bd4dbdefc", "Contact header and copy are unchanged");
  const helperNames = ["buildContactRequestsStorageKey", "createContactRequestId", "formatContactRequestTimestamp", "isContactRequestTopic", "resolveContactSubmittedBy", "normalizeContactRequest", "readStoredContactRequests", "persistContactRequests"];
  const helpers = shellAst.statements.filter((node: any) => ts.isFunctionDeclaration(node) && helperNames.includes(node.name?.text));
  equal(helpers.length, helperNames.length, "All original Contact helpers remain");
  equal(sha(helpers.map((node: any) => node.getText(shellAst)).join("\n")), "a8b7ce016fb97b6342e0933267b58d84e222ff2e5130050ca6d221bc54c0f3fe", "Contact persistence and support helpers are unchanged");
});

test("MEMBER authority and isolation remain unchanged and execute correctly", () => {
  const routeSource = namedFunction(appAst, "Auth0SessionRoute").getText(appAst);
  equal(sha(routeSource), "10f5eef326f74a2866ff48b0f76ebf4d84115a40cc1de592447a77d937bae94f", "The baseline authority boundary is unchanged");
  const { Auth0SessionRoute } = evaluate(routeSource, {
    parseTeamInviteContinuationRoute: () => null, parseTeamInviteRoute: () => null,
    parseCollaborationInviteRoute: () => null, Suspense: "Suspense",
    TeamMemberShell: "TeamMemberShell", OwnerAppStartup: "OwnerAppStartup",
    WorkspaceLoadingFallback: "WorkspaceLoadingFallback", Auth0LoginView: "Auth0LoginView",
  });
  const renderRole = (role: string) => Auth0SessionRoute({
    session: { status: "authenticated", user: { workspaceRole: role, workspaceId: "test-workspace", userId: "test-user" } },
    appRoute: "workspace", onExitPreview: () => undefined,
  });
  const member = elements(renderRole("member"));
  check(member.some((node) => node.type === "TeamMemberShell"), "MEMBER gets TeamMemberShell");
  check(!member.some((node) => node.type === "OwnerAppStartup"), "MEMBER never mounts owner mailboxes");
  for (const role of ["owner", "admin"]) equal(renderRole(role).type, "OwnerAppStartup", `${role} retains owner Email Client access`);
  check(textContent(renderRole("unknown")).includes("Sign-in is temporarily unavailable"), "Unsupported roles do not gain owner access");
});

test("Empty and whitespace-only queries show normal guidance", () => {
  const view = mountHelp();
  equal(displayedTitles(view.tree), [...currentTitles].sort(), "Initial guidance shows all topics");
  view.search("   \t  ");
  equal(displayedTitles(view.tree), [...currentTitles].sort(), "Whitespace query shows all topics");
  check(!textContent(view.tree).includes("Clear search"), "Whitespace is treated as empty");
  view.search("");
  equal(displayedTitles(view.tree), [...currentTitles].sort(), "Empty query restores normal guidance");
});

test("Search is case-insensitive and trims surrounding whitespace", () => {
  const view = mountHelp();
  view.search("working with conversations");
  const expected = displayedTitles(view.tree);
  equal(expected, ["Working with conversations"], "Lowercase query finds the conversation topic");
  view.search("  WoRkInG WiTh CoNvErSaTiOnS  ");
  equal(displayedTitles(view.tree), expected, "Case and surrounding whitespace do not affect matching");
});

test("Title searches filter the actual guidance cards", () => {
  const view = mountHelp();
  for (const title of currentTitles) {
    view.search(title);
    equal(displayedTitles(view.tree), [title], `${title} matches its title only`);
  }
});

test("Search finds intro, bullet, tip and keyword text", () => {
  const view = mountHelp();
  const conversations = topic("Working with conversations");
  for (const [field, value] of [
    ["intro", conversations.intro], ["bullet", conversations.points[0]],
    ["keyword", conversations.keywords[0]],
  ]) {
    view.search(value);
    check(displayedTitles(view.tree).includes(conversations.title), `${field} search finds its topic`);
  }
  const withTip = helpTopics.find((item) => item.tip);
  if (withTip?.tip) {
    view.search(withTip.tip);
    check(displayedTitles(view.tree).includes(withTip.title), "Existing tip search remains functional");
  }
});

test("Unmatched search has understandable no-results feedback", () => {
  const view = mountHelp();
  view.search("zz-no-help-topic-7319");
  equal(displayedTitles(view.tree), [], "No unrelated guidance cards remain in filtered results");
  matches(textContent(view.tree), /No help topic found/, "Main panel explains there are no matches");
  matches(textContent(view.tree), /Try searching|Try a broader search/i, "No-results feedback suggests recovery");
  check(textContent(view.tree).includes("Clear search"), "Clear is available from no results");
});

test("Search result buttons open normal selected guidance and preserve keyboard focus behavior", () => {
  const view = mountHelp();
  equal(view.input().props["aria-label"], "Search Help", "Search has an accessible name");
  view.focus();
  view.search("Working with conversations");
  const result = elements(view.tree).find((node) => node.type === "button" &&
    textContent(node).startsWith("Working with conversations") && !elements(node).some((child) => child.type === "p"));
  check(Boolean(result), "A matching suggestion is available as a native button");
  equal(result.props.type, "button", "Keyboard activation uses native button behavior");
  check(result.props.tabIndex !== -1, "Result remains keyboard reachable");
  matches(result.props.className, /focus:/, "Focused result has a visible style");
  const wrapper = elements(view.tree).find((node) => typeof node.props.onBlur === "function");
  view.fire(() => wrapper.props.onBlur({ currentTarget: { contains: () => true }, relatedTarget: result }));
  check(textContent(view.tree).includes("Search matches"), "Moving focus within search preserves suggestions");
  view.fire(() => result.props.onClick());
  const selected = elements(view.tree).find(isSelectedCard);
  check(Boolean(selected), "Search selection opens the existing guidance card");
  const selectedText = textContent(selected);
  check(selectedText.includes(topic("Working with conversations").intro), "Selected intro is the topic's actual content");
  for (const point of topic("Working with conversations").points) check(selectedText.includes(point), "Selected card renders each bullet");
  equal(view.viewed, ["Working with conversations"], "Result selection records last-viewed guidance");
  check(!textContent(view.tree).includes("Search matches"), "Selecting hides suggestions");
  view.focus();
  const blurredWrapper = elements(view.tree).find((node) => typeof node.props.onBlur === "function");
  view.fire(() => blurredWrapper.props.onBlur({ currentTarget: { contains: () => false }, relatedTarget: null }));
  check(!textContent(view.tree).includes("Search matches"), "Leaving the search region dismisses suggestions");
});

test("Clear search restores all topics and back navigation preserves selected-card interaction", () => {
  const view = mountHelp();
  view.search("Working with conversations");
  const card = elements(view.tree).find(isCard);
  view.fire(() => elements(card).find((node) => node.type === "button").props.onClick());
  check(Boolean(elements(view.tree).find(isSelectedCard)), "Filtered card opens guidance");
  view.click("Clear search");
  equal(view.input().props.value, "", "Clearing empties the search field");
  equal(displayedTitles(view.tree), [...currentTitles].sort(), "Clearing restores all topics");
  check(!elements(view.tree).some(isSelectedCard), "Clearing resets selected guidance");
  const normalCard = elements(view.tree).find(isCard);
  view.fire(() => elements(normalCard).find((node) => node.type === "button").props.onClick());
  view.click("Back to all topics");
  equal(displayedTitles(view.tree), [...currentTitles].sort(), "Existing back action restores normal guidance");
});

test("Help search remains local without new persistence or network work", () => {
  const helpReturn = utilityNode.body.statements.filter((node: any) => ts.isReturnStatement(node)).at(-1).getText(shellAst);
  const searchDeclaration = namedVariable(utilityNode.body.statements, "filteredHelpTopics").getText(shellAst);
  check(!/\bfetch\s*\(|XMLHttpRequest|WebSocket|sendBeacon|axios|localStorage|sessionStorage/i.test(helpReturn + searchDeclaration), "Help rendering and search do not introduce external requests or persistence");
  matches(searchDeclaration, /helpTopics\.filter/, "Search filters local topic data");
  equal(externalCalls, 0, "All exercised Help interactions make zero external calls");
});

console.log(`Help tests: ${testCount - failures.length}/${testCount} passed; ${assertionCount} assertions.`);
if (failures.length > 0) throw new Error(`Help tests failed: ${failures.join("; ")}`);
