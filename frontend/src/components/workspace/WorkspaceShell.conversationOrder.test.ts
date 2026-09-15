import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { transform } from "sucrase";
import {
  createUserAccountConfigConflictRetryQueue,
  loadUserAccountConfig,
  normalizeConversationOrder,
  projectWorkspaceUserAccountConfigForSave,
  saveUserAccountConfig,
  setUserAccountConfigHydrationEchoExpectation,
  type ConversationOrder,
  type UserAccountConfig,
} from "../../lib/userConfigApi";

const workspaceSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"), "utf8",
);
const appSource = readFileSync(resolve(process.cwd(), "src/App.tsx"), "utf8");
let assertionCount = 0;
let testCount = 0;
function equal(actual: unknown, expected: unknown, message: string) {
  assertionCount += 1;
  assert.deepEqual(actual, expected, message);
}
function check(condition: unknown, message: string): asserts condition {
  assertionCount += 1;
  assert.ok(condition, message);
}
function extract(source: string, start: string, end: string) {
  const first = source.indexOf(start);
  const last = source.indexOf(end, first + start.length);
  if (first < 0 || last <= first) throw new Error(`Missing source boundary: ${start} / ${end}`);
  return source.slice(first, last);
}
// Compile the real small renderers/helpers instead of importing WorkspaceShell's
// unrelated application dependencies. Only surrounding data and leaf UI are stubbed.
function evaluate(source: string, expression: string, dependencies: Record<string, unknown> = {}) {
  const compiled = transform(source.replace(/^export /gm, ""), {
    transforms: ["typescript", "jsx"], production: true,
  }).code;
  return new Function(...Object.keys(dependencies), `${compiled}\nreturn ${expression};`)(
    ...Object.values(dependencies),
  );
}
type Element = { type: unknown; props: Record<string, any> };
const React = {
  createElement(type: unknown, props: Record<string, unknown> | null, ...children: unknown[]): Element {
    return { type, props: { ...props, children: children.flat(Infinity) } };
  },
};
function elements(node: any): Element[] {
  if (!node || typeof node !== "object") return [];
  if (Array.isArray(node)) return node.flatMap(elements);
  return [node, ...elements(node.props?.children)];
}
function textContent(node: any): string {
  if (Array.isArray(node)) return node.map(textContent).join("");
  if (node === null || node === undefined || typeof node === "boolean") return "";
  return typeof node === "object" ? textContent(node.props?.children) : String(node);
}
type Message = {
  id: string;
  subject: string;
  timestamp: string;
  createdAt?: string;
  unread?: boolean;
  attachments?: Array<{ id: string }>;
};
const dateSource = extract(workspaceSource, "function resolveMailDateMs(", "const PRIORITY_QUEUE_RECENT_WINDOW_MS");
const resolveMailDateMs = evaluate(dateSource, "resolveMailDateMs");
const getThreadMessagesForDisplay = evaluate(
  extract(workspaceSource, "function getThreadMessagesForDisplay(", "function resolveInitialExpandedThreadMessageIds("),
  "getThreadMessagesForDisplay", { resolveMailDateMs },
);
const getConversationDisplaySubject = evaluate(
  extract(workspaceSource, "function normalizeConversationDisplaySubject(", "function hasReliableQuotedContent("),
  "getConversationDisplaySubject",
);
const resolveInitialExpandedThreadMessageIds = evaluate(
  extract(workspaceSource, "function resolveInitialExpandedThreadMessageIds(", "function toggleDisclosureId("),
  "resolveInitialExpandedThreadMessageIds",
);
const canonicalSource = extract(workspaceSource, "const getThreadMessages =", "const selectedMessageThreadMessages =");
const timelineSource = extract(workspaceSource, "const renderThreadTimeline =", "const activeStoredCollaborationMessage =");
function canonicalMessages(selected: Message, source: readonly Message[]): Message[] {
  const getThreadMessages = evaluate(canonicalSource, "getThreadMessages", {
    primaryMessageSelection: null,
    currentMessageLocationByMessage: new Map(),
    currentMessageLocationById: {},
    resolveAuthoritativeMessageLocation: () => null,
    mailboxThreadMessages: source,
    getRecentThreadMessages: () => source,
    mailbox: { id: "owner-inbox" },
    productAccess: {}, showBundleOrganizerManagedMail: false,
    shouldHideBundleOrganizerManagedMessage: () => false,
    getVisibleCategoryLabelForMessage: () => null,
    resolveMailDateMs,
  });
  return getThreadMessages(selected);
}
function renderTimeline(
  canonical: readonly Message[], order: ConversationOrder,
  density: "split" | "full", expandedMemberIds: string[] = [],
  actionMessage: Message | null = canonical.at(-1) ?? null,
) {
  let canonicalCalls = 0;
  const renderThreadTimeline = evaluate(timelineSource, "renderThreadTimeline", {
    React, conversationOrder: order,
    getThreadMessagesForDisplay,
    getThreadMessages: () => { canonicalCalls += 1; return canonical; },
    resolveInitialExpandedThreadMessageIds,
    activeDesktopThreadDisclosureState: { expandedMemberIds },
    renderThreadMessage: (message: Message, context: string, options: unknown) =>
      React.createElement("article", { message, context, options }),
    renderMessageActions: (message: Message, context: string) => ({ message, context }),
  });
  const tree = renderThreadTimeline(canonical.at(-1) ?? null, density, actionMessage);
  return { tree, canonicalCalls, articles: elements(tree).filter((element) => element.type === "article") };
}
const ids = (articles: Element[]) => articles.map((article) => article.props.message.id);
const orders = ["newest-first", "oldest-first"] as const;
const physicalMessages: Message[] = [
  { id: "root", subject: "Original subject", timestamp: "", createdAt: "2026-01-01T10:00:00Z", attachments: [{ id: "root-file" }] },
  { id: "middle", subject: "Re: Original subject", timestamp: "", createdAt: "2026-01-02T10:00:00Z" },
  { id: "latest", subject: "Re: Re: Changed reply subject", timestamp: "", createdAt: "2026-01-03T10:00:00Z", unread: true },
];

async function run(name: string, test: () => void | Promise<void>) {
  try { await test(); testCount += 1; }
  catch (error) { throw new Error(`${name}: ${error instanceof Error ? error.message : error}`, { cause: error }); }
}

async function main() {
  await run("strict defaults and valid values", () => {
    equal(normalizeConversationOrder(undefined), "newest-first", "absence defaults to newest first");
    for (const order of orders) equal(normalizeConversationOrder(order), order, `accept exact ${order}`);
    const cleanConfig = evaluate(
      extract(appSource, "function createCleanUserAccountConfig(", "function writeOnboardingSessionMirror("),
      "createCleanUserAccountConfig()",
    );
    equal(normalizeConversationOrder(cleanConfig.uiPreferences.conversationOrder), "newest-first", "new account starts newest first");
  });

  for (const density of ["split", "full"] as const) {
    for (const order of orders) {
      await run(`${density} ${order} physical order and semantic identity`, () => {
        const canonical = Object.freeze(canonicalMessages(physicalMessages[2], Object.freeze([...physicalMessages])));
        const before = JSON.stringify(canonical);
        const rendered = renderTimeline(canonical, order, density);
        equal(rendered.canonicalCalls, 1, "timeline derives canonical chronology once");
        equal(ids(rendered.articles), order === "newest-first" ? ["latest", "middle", "root"] : ["root", "middle", "latest"], "physical blocks obey preference");
        equal(rendered.tree.props["aria-labelledby"], density === "full" ? "full-message-modal-title" : "conversation-title", "surface retains accessible label");
        equal(JSON.stringify(canonical), before, "canonical messages and metadata remain unchanged");
        equal(getConversationDisplaySubject(canonical, physicalMessages[2].subject), "Original subject", "root subject does not follow display order");
        for (const article of rendered.articles) {
          const message = article.props.message as Message;
          const options = article.props.options;
          check(message === physicalMessages.find((candidate) => candidate.id === message.id), "each physical message keeps its original identity and attachments");
          equal(options.collapsed, message.id !== "latest", "initial disclosure follows canonical latest identity");
          equal(options.canCollapse, message.id !== "latest", "latest stays expanded whichever end it renders at");
          if (density === "split" && message.id === "latest") {
            check(options.actions?.message === physicalMessages[2], "actions target the chronological latest message");
          } else {
            equal(options.actions, undefined, "older blocks and full timeline do not acquire split actions");
          }
        }
        const expanded = renderTimeline(canonical, order, density, ["root"]);
        equal(expanded.articles.find((article) => article.props.message.id === "root")?.props.options.collapsed, false, "explicitly expanded older message remains expanded after ordering");
      });
    }
  }

  await run("existing action target remains distinct from placement", () => {
    const actionTarget = { ...physicalMessages[2], id: "existing-action-target" };
    for (const order of orders) {
      const rendered = renderTimeline(physicalMessages, order, "split", [], actionTarget);
      const actions = rendered.articles.filter((article) => article.props.options.actions);
      equal(actions.length, 1, "exactly one physical message owns the split action row");
      equal(actions[0].props.message.id, "latest", "action row belongs to canonical latest");
      check(actions[0].props.options.actions.message === actionTarget, "existing actionMessage argument is preserved");
    }
    check(/actions=\{renderMessageActions\(fullMessageModalMessage, "full"\)\}/.test(workspaceSource), "full modal keeps its established toolbar action target");
  });

  await run("equal and missing timestamps remain deterministic", () => {
    for (const createdAt of ["2026-01-01T10:00:00Z", undefined]) {
      const fixture: Message[] = ["a", "b", "c"].map((id) => ({ id, subject: id, timestamp: "", createdAt }));
      const canonical = canonicalMessages(fixture[0], fixture);
      equal(canonical.map((message) => message.id), ["a", "b", "c"], "canonical chronology preserves ties and unavailable timestamp order");
      for (const order of orders) {
        const first = renderTimeline(Object.freeze(canonical), order, "split");
        const second = renderTimeline(canonical, order, "full");
        equal(ids(first.articles), ["a", "b", "c"], "equal/missing timestamp groups keep existing relative order in both preferences");
        equal(ids(first.articles), ids(second.articles), "equal/missing timestamp order agrees between surfaces");
        equal(ids(first.articles), ids(renderTimeline(canonical, order, "split").articles), "rendering is deterministic");
        equal(canonical.map((message) => message.id), ["a", "b", "c"], "ties remain unchanged in canonical data");
        equal(first.articles.find((article) => article.props.options.actions)?.props.message.id, "c", "canonical latest tie identity still owns actions");
      }
    }
  });

  await run("mixed chronology reverses groups while preserving ties", () => {
    const fixture: Message[] = [
      { id: "missing-a", subject: "Root", timestamp: "" },
      { id: "missing-b", subject: "Re: Root", timestamp: "unavailable" },
      { id: "dated-a", subject: "Re: Root", timestamp: "", createdAt: "2026-01-01T10:00:00Z" },
      { id: "dated-b", subject: "Re: Root", timestamp: "", createdAt: "2026-01-01T10:00:00Z" },
      { id: "recent-a", subject: "Re: Root", timestamp: "2 minutes ago" },
      { id: "recent-b", subject: "Re: Root", timestamp: "2 minutes ago" },
    ];
    const canonical = Object.freeze(canonicalMessages(fixture[0], fixture));
    for (const density of ["split", "full"] as const) {
      equal(ids(renderTimeline(canonical, "newest-first", density).articles), ["recent-a", "recent-b", "dated-a", "dated-b", "missing-a", "missing-b"], "newest group comes first with stable missing/absolute/relative timestamp ties");
      equal(ids(renderTimeline(canonical, "oldest-first", density).articles), fixture.map((message) => message.id), "oldest-first retains complete canonical order");
    }
    check(getThreadMessagesForDisplay(canonical, "oldest-first") !== canonical, "oldest display array is also a copy");
  });

  await run("single and empty conversations", () => {
    for (const density of ["split", "full"] as const) {
      const newest = renderTimeline([physicalMessages[0]], "newest-first", density);
      const oldest = renderTimeline([physicalMessages[0]], "oldest-first", density);
      equal(newest.tree, oldest.tree, "single message renders identically for both orders");
      equal(newest.articles[0].props.options.collapsed, false, "single message is expanded");
      equal(newest.articles[0].props.options.canCollapse, false, "single message cannot collapse");
      equal(renderTimeline([], "newest-first", density).tree, null, "empty thread stays empty");
      for (const order of orders) {
        const two = renderTimeline(physicalMessages.slice(0, 2), order, density);
        equal(two.articles.map((article) => article.props.options.collapsed), [false, false], "two-message conversation keeps both messages expanded");
        equal(two.articles.map((article) => article.props.options.canCollapse), [false, false], "two-message conversation cannot collapse either member");
      }
    }
  });

  await run("Mail settings accessible selection and immediate callback", () => {
    const settingsPillButtonClass = evaluate(
      extract(workspaceSource, "function settingsPillButtonClass(", "function settingsTabButtonClass("),
      "settingsPillButtonClass",
    );
    const MailSettingsCard = evaluate(
      extract(workspaceSource, "const MailSettingsCard =", "const InboxBehaviorSettingsCard ="),
      "MailSettingsCard", {
        React, memo: (component: unknown) => component,
        settingsSectionLabelClass: "", settingsCardSectionClass: "", settingsCardClass: () => "",
        settingsPillButtonClass,
      },
    );
    let selected = normalizeConversationOrder(undefined);
    const renderSettings = () => MailSettingsCard({
      managedInboxes: [], inboxOutOfOffice: {}, themeMode: "light", showOutOfOfficeSettings: false,
      conversationOrder: selected, onChangeConversationOrder: (next: ConversationOrder) => { selected = next; },
    });
    const defaultTree = renderSettings();
    const copy = textContent(defaultTree);
    for (const label of ["Conversation order", "Newest first", "Oldest first", "Choose which message appears at the top of a conversation."]) {
      check(copy.includes(label), `Mail settings shows ${label}`);
    }
    const group = elements(defaultTree).find((element) => element.props.role === "group")!;
    equal(group.props["aria-labelledby"], "conversation-order-label", "choice group is associated with its visible label");
    equal(group.props["aria-describedby"], "conversation-order-description", "supporting copy describes the choice group");
    const buttonFor = (tree: Element, label: string) => elements(tree).find((element) => element.type === "button" && textContent(element) === label)!;
    equal(buttonFor(defaultTree, "Newest first").props["aria-pressed"], true, "newest is semantically selected by default");
    equal(buttonFor(defaultTree, "Oldest first").props["aria-pressed"], false, "oldest starts unselected");
    for (const label of ["Newest first", "Oldest first"]) {
      const button = buttonFor(defaultTree, label);
      equal(button.props.type, "button", "choice uses a keyboard-operable semantic button");
      check(button.props.className.includes("focus-visible:ring-2"), "choice keeps visible keyboard focus");
      check(!/uppercase|tracking-\[/.test(button.props.className), "choice has no uppercase or wide tracking");
    }
    buttonFor(defaultTree, "Oldest first").props.onClick();
    equal(selected, "oldest-first", "click updates preference immediately");
    equal(buttonFor(renderSettings(), "Oldest first").props["aria-pressed"], true, "oldest exposes selected state after click");
    buttonFor(renderSettings(), "Newest first").props.onClick();
    equal(selected, "newest-first", "user can switch back");
  });

  await run("both surfaces and settings receive the same preference state", () => {
    check(/renderThreadTimeline\(\s*selectedMessage,\s*"split",\s*fullWidthMessage \?\? selectedMessage,/.test(workspaceSource), "split uses shared timeline and preserves existing action target");
    check(/renderThreadTimeline\(\s*fullMessageModalMessage,\s*"full",\s*null,/.test(workspaceSource), "full modal uses shared timeline");
    check(/getConversationDisplaySubject\(\s*selectedMessageThreadMessages,/.test(workspaceSource), "split title reads canonical chronology");
    check(/getConversationDisplaySubject\(\s*fullMessageModalThreadMessages,/.test(workspaceSource), "full title reads canonical chronology");
    check(/<MailSettingsCard[\s\S]*?conversationOrder=\{conversationOrder\}[\s\S]*?onChangeConversationOrder=\{onChangeConversationOrder\}/.test(workspaceSource), "SettingsView forwards the preference and setter to Mail settings");
    check(/<SettingsView[\s\S]*?conversationOrder=\{conversationOrder\}[\s\S]*?onChangeConversationOrder=\{setConversationOrder\}/.test(workspaceSource), "Workspace owns the immediate Settings preference state");
  });

  await run("server hydration overrides stale local order", () => {
    const hydrationSource = extract(appSource, "  const uiPreferences = config.uiPreferences ?? {};", "\nfunction prepareMissingAccountConfigForFirstExplicitSave(");
    const hydrate = evaluate(`function hydrate(config, storage) {${hydrationSource}`, "hydrate", {
      normalizeConversationOrder,
      normalizeStoredWorkspaceThemeMode: () => null,
      CONVERSATION_ORDER_STORAGE_KEY: "cuevion-conversation-order",
      WORKSPACE_THEME_MODE_STORAGE_KEY: "theme", AI_SUGGESTIONS_STORAGE_KEY: "ai",
      INBOX_CHANGES_STORAGE_KEY: "inbox", TEAM_ACTIVITY_STORAGE_KEY: "team",
    });
    const initializeOrder = (storage: unknown) => evaluate(
      extract(workspaceSource, "  const [conversationOrder, setConversationOrder] =", "  const [aiSuggestionsEnabled, setAiSuggestionsEnabled] ="),
      "conversationOrder", {
        normalizeConversationOrder, CONVERSATION_ORDER_STORAGE_KEY: "cuevion-conversation-order",
        window: { localStorage: storage }, useState: (initialize: () => unknown) => [initialize(), () => undefined],
      },
    );
    const stored = new Map<string, string>([["cuevion-conversation-order", "oldest-first"]]);
    const storage = {
      setItem: (key: string, value: string) => stored.set(key, value),
      getItem: (key: string) => stored.get(key) ?? null,
      removeItem: (key: string) => stored.delete(key),
    };
    for (const order of [undefined, ...orders]) {
      hydrate({ uiPreferences: { conversationOrder: order } }, storage);
      equal(normalizeConversationOrder(stored.get("cuevion-conversation-order")), order ?? "newest-first", "server preference (including absence) replaces stale account mirror");
      equal(initializeOrder(storage), order ?? "newest-first", "Workspace state initializes from the current account's hydrated preference");
    }
    const prepareMissing = evaluate(
      extract(appSource, "function prepareMissingAccountConfigForFirstExplicitSave(", "function applyLoadedUserAccountConfig("),
      "prepareMissingAccountConfigForFirstExplicitSave", {
        writeFoundAccountConfigToLocalStorage: (config: UserAccountConfig, _account: string, targetStorage: unknown) => hydrate(config, targetStorage),
        createCleanUserAccountConfig: evaluate(extract(appSource, "function createCleanUserAccountConfig(", "function writeOnboardingSessionMirror("), "createCleanUserAccountConfig"),
      },
    );
    prepareMissing({ accountStorageOwnerKey: "new-account", storage });
    equal(initializeOrder(storage), "newest-first", "missing account's first explicit save clears preceding account's oldest-first mirror");
    check(/conversationOrder:\s*normalizeConversationOrder\(\s*storage.getItem\(CONVERSATION_ORDER_STORAGE_KEY\)/.test(appSource), "existing App save/echo projection includes hydrated order");
  });

  await run("MEMBER startup mounts no owner config authority", () => {
    const route = evaluate(
      extract(appSource, "export function Auth0SessionRoute(", "function Auth0SessionBoundary("),
      "Auth0SessionRoute", {
        React, parseTeamInviteContinuationRoute: () => null, parseTeamInviteRoute: () => null,
        parseCollaborationInviteRoute: () => null,
        Suspense: "Suspense", WorkspaceLoadingFallback: "Loading", TeamMemberShell: "TeamMemberShell",
        OwnerAppStartup: "OwnerAppStartup", Auth0LoginView: "Login", OnboardingPreviewRoute: "Preview",
      },
    );
    for (const workspaceRole of ["member", "owner", "admin"]) {
      const tree = route({ session: { status: "authenticated", user: { workspaceRole, workspaceId: "team", userId: "user" } }, appRoute: "app", onExitPreview: () => undefined });
      const types = elements(tree).map((element) => element.type);
      equal(types.includes("OwnerAppStartup"), workspaceRole !== "member", "owner startup remains exclusive to owner/admin");
      equal(types.includes("TeamMemberShell"), workspaceRole === "member", "Team MEMBER stays in isolated shell");
    }
  });

  const originalFetch = globalThis.fetch;
  let serverConfig: UserAccountConfig = {};
  const requests: Array<{ url: string; method: string; body: any; credentials: unknown }> = [];
  globalThis.fetch = (async (url: unknown, request: RequestInit = {}) => {
    const body = request.body ? JSON.parse(String(request.body)) : null;
    requests.push({ url: String(url), method: request.method ?? "GET", body, credentials: request.credentials });
    if (request.method === "POST") serverConfig = body.config;
    return { status: 200, json: async () => ({ ok: true, configState: "found", config: serverConfig }) } as Response;
  }) as typeof fetch;
  try {
    await run("invalid values cannot become server config authority", async () => {
      setUserAccountConfigHydrationEchoExpectation("owner-test", null);
      for (const value of [null, false, 1, {}, [], "", "NEWEST-FIRST", " newest-first", "oldest-first ", "latest-first"]) {
        const config = { uiPreferences: { conversationOrder: value } } as unknown as UserAccountConfig;
        const previousRequests = requests.length;
        equal((await saveUserAccountConfig(config)).status, "invalid", `reject malformed save value ${JSON.stringify(value)}`);
        equal(requests.length, previousRequests, "invalid config never posts");
        serverConfig = config;
        equal((await loadUserAccountConfig()).status, "malformed_response", "malformed server value is not accepted as authoritative config");
      }
      for (const order of [undefined, ...orders]) {
        serverConfig = { uiPreferences: { conversationOrder: order } };
        equal((await loadUserAccountConfig()).status, "found", "missing or exact supported order remains valid owner config");
      }
    });

    await run("Workspace preference persists through existing queue and server API", async () => {
      const snapshotSource = extract(workspaceSource, "      const nextAccountConfig: UserAccountConfig =", "      workspaceAccountConfigSaveQueueRef.current?.enqueue(nextAccountConfig");
      for (const order of orders) {
        const snapshot = evaluate(snapshotSource, "nextAccountConfig", {
          projectWorkspaceUserAccountConfigForSave, conversationOrder: order,
          savedManagedInboxes: [], sanitizeManagedWorkspaceInboxForAccountConfig: (value: unknown) => value,
          mailboxTitleOverrides: {}, primaryManagedInboxId: null, mailboxFocusPreferenceOverrides: {},
          inboxSignatures: {}, smartFolders: [], workspaceMode: "Light",
          aiSuggestionsEnabled: true, inboxChangesEnabled: true, teamActivityEnabled: true,
        });
        equal(snapshot.uiPreferences.conversationOrder, order, "Workspace save snapshot includes preference");
        const queue = createUserAccountConfigConflictRetryQueue({ scheduleRetry: () => { throw new Error("unexpected retry"); }, cancelRetry: () => undefined });
        const result = await new Promise<any>((resolveResult) => queue.enqueue(snapshot, { onSettled: resolveResult }));
        equal(result.status, "found", "existing save queue settles server-backed preference");
        equal(queue.isDirty(), false, "successful preference save clears queue dirty state");
        const post = requests.at(-1)!;
        equal([post.url, post.method, post.credentials], ["/api/user/config", "POST", "include"], "save uses authenticated existing user-config endpoint");
        equal(post.body.config.uiPreferences.conversationOrder, order, "POST contains exact preference");
        const loaded = await loadUserAccountConfig();
        equal(loaded.status, "found", "saved preference reloads successfully");
        if (loaded.status === "found") equal(loaded.config.uiPreferences?.conversationOrder, order, "server reload restores exact saved value");
      }
      const saveEffect = extract(workspaceSource, "    const pendingTitleRenames = Object.values(", "  const handleConfirmLogout =");
      check(/\}, \[[\s\S]*?\bconversationOrder,/.test(saveEffect), "changing order schedules existing persistence effect");
    });

    await run("hydration echo and account changes use existing save authority", async () => {
      const config = { uiPreferences: { conversationOrder: "oldest-first" as const } };
      setUserAccountConfigHydrationEchoExpectation("hydrated-owner", config);
      const requestCount = requests.length;
      equal((await saveUserAccountConfig(config)).status, "found", "unchanged hydrated preference settles normally");
      equal(requests.length, requestCount, "hydration does not introduce a duplicate preference write");
      const changed = saveUserAccountConfig({ uiPreferences: { conversationOrder: "newest-first" } });
      setUserAccountConfigHydrationEchoExpectation("different-owner", null);
      equal((await changed).status, "unavailable", "queued preference save is cancelled when account changes");
      equal(requests.length, requestCount, "previous account's preference cannot post under the next account");
    });
  } finally {
    globalThis.fetch = originalFetch;
    setUserAccountConfigHydrationEchoExpectation(null, null);
  }
  await run("backend strict preference validation", () => {
    // Extract only the allowlisted backend contract, leaving unrelated imports,
    // credentials and services untouched. Exercise the actual HTTP rejection path.
    const backendOutput = execFileSync("python3", ["-c", String.raw`
import ast
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
source_path = Path('api/user/config.py')
module = ast.parse(source_path.read_text())
names = {'_is_valid_conversation_order', '_sanitize_user_config', '_has_valid_known_stored_config_shapes', '_UiPreferencesValidationError', '_OnboardingSessionValidationError', 'handler'}
selected = [node for node in module.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
assertions = 0
def check(condition):
    global assertions
    assertions += 1
    assert condition
check(len(selected) == len(names))
class OnboardingState(Enum):
    NOT_STARTED = 'not_started'
    INVALID = 'invalid'
responses, store_calls = [], []
request_payload = {}
namespace = {
    'datetime': datetime, 'timezone': timezone, 'BaseHTTPRequestHandler': object,
    'USER_CONFIG_SCHEMA_VERSION': 1, 'ALLOWED_CONFIG_FIELDS': {'uiPreferences'},
    'normalize_auth_email': lambda email: email.strip().lower(),
    '_strip_sensitive_fields': lambda value: value.copy(),
    '_validate_json_structure': lambda record: None,
    '_classify_stored_onboarding_session': lambda session: (OnboardingState.NOT_STARTED, None),
    '_StoredOnboardingSessionState': OnboardingState,
    'resolve_authenticated_user': lambda headers: ({'email': 'owner@example.test', 'userType': 'member'}, None),
    '_read_json_body': lambda request: (request_payload, None, None),
    '_contains_server_only_credential_field': lambda payload: False,
    '_send_json': lambda request, status, body: responses.append((status, body)),
    '_build_error': lambda code, message: {'error': {'code': code, 'message': message}},
    'resolve_user_config_store': lambda: (store_calls.append('resolve'), None),
}
exec(compile(ast.Module(body=selected, type_ignores=[]), str(source_path), 'exec'), namespace)
valid = namespace['_is_valid_conversation_order']
sanitize = namespace['_sanitize_user_config']
stored_valid = namespace['_has_valid_known_stored_config_shapes']
for preference in ['newest-first', 'oldest-first']:
    check(valid(preference))
    check(sanitize({'config': {'uiPreferences': {'conversationOrder': preference}}}, 'OWNER@example.test')['uiPreferences']['conversationOrder'] == preference)
    check(stored_valid({'onboardingSession': {}, 'uiPreferences': {'conversationOrder': preference}}))
check(sanitize({'config': {'uiPreferences': {}}}, 'OWNER@example.test')['uiPreferences'] == {})
check(stored_valid({'onboardingSession': {}, 'uiPreferences': {}}))
check(stored_valid({'onboardingSession': {}}))
for value in [None, '', 'Newest first', 'Newest-first', 'newest-first ', ' oldest-first', 'oldest_first', 'unknown', 0, 1, True, False, [], {}, ['newest-first']]:
    check(not valid(value))
    check(not stored_valid({'onboardingSession': {}, 'uiPreferences': {'conversationOrder': value}}))
    try:
        sanitize({'config': {'uiPreferences': {'conversationOrder': value}}}, 'OWNER@example.test')
    except namespace['_UiPreferencesValidationError']:
        check(True)
    else:
        check(False)
    request_payload = {'config': {'uiPreferences': {'conversationOrder': value}}}
    responses.clear()
    store_calls.clear()
    namespace['handler'].do_POST(SimpleNamespace(headers={}))
    check(responses[0][0] == 400)
    check(responses[0][1]['error']['code'] == 'config_invalid')
    check(store_calls == [])
print(f'Backend conversation-order validation: {assertions} assertions passed.')
`], { encoding: "utf8" });
    check(backendOutput.includes("100 assertions passed"), "backend strict valid/missing/malformed contract passes all 100 assertions");
    console.log(backendOutput.trim());
  });
  console.log(`Conversation order: ${testCount} tests, ${assertionCount} assertions passed.`);
}
void main().catch((error) => { console.error(error); process.exitCode = 1; });
