export {};

declare function require(id: string): any;
declare const __dirname: string;

const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { runInNewContext } = require("node:vm");
const { transform } = require("sucrase");
const authApi = require("../../lib/authApi");

type ViewChild = ViewNode | string | number | boolean | null | undefined | ViewChild[];
type ViewNode = {
  type: string;
  props: {
    children?: ViewChild;
    className?: string;
    role?: string;
    type?: string;
    onClick?: () => void;
  };
};

// Execute the real component with an isolated location, without navigating a browser.
const source = readFileSync(join(__dirname, "Auth0LoginView.tsx"), "utf8");
const compiled = transform(source, {
  transforms: ["typescript", "jsx", "imports"],
  jsxRuntime: "automatic",
}).code;

function render(search?: string) {
  const assigned: string[] = [];
  const viewExports = {} as { Auth0LoginView: () => ViewNode };
  runInNewContext(compiled, {
    exports: viewExports,
    require: (id: string) => id === "../../lib/authApi" ? authApi : require(id),
    ...(search === undefined ? {} : {
      window: {
        location: {
          search,
          assign: (url: string) => assigned.push(url),
        },
      },
    }),
  });
  return { tree: viewExports.Auth0LoginView(), assigned };
}

function nodes(child: ViewChild): ViewNode[] {
  if (Array.isArray(child)) return child.flatMap(nodes);
  if (!child || typeof child !== "object") return [];
  return [child, ...nodes(child.props.children)];
}

function textContent(child: ViewChild): string {
  if (Array.isArray(child)) return child.map(textContent).join("");
  if (typeof child === "string" || typeof child === "number") return String(child);
  return child && typeof child === "object" ? textContent(child.props.children) : "";
}

function only(tree: ViewNode, predicate: (node: ViewNode) => boolean): ViewNode {
  const matches = nodes(tree).filter(predicate);
  assert.equal(matches.length, 1);
  return matches[0];
}

function button(tree: ViewNode) {
  return only(tree, (node) => node.type === "button");
}

function classes(node: ViewNode) {
  return new Set((node.props.className ?? "").split(/\s+/));
}

test("login keeps its heading and uses exact method-neutral copy", () => {
  const { tree } = render("");
  assert.equal(textContent(only(tree, (node) => node.type === "h1")), "Sign in to Cuevion");
  assert.equal(
    textContent(only(tree, (node) => node.type === "p")),
    "Continue securely to access your Cuevion workspace.",
  );
  assert.equal(textContent(button(tree)), "Sign in");
  assert.doesNotMatch(textContent(tree), /email|\botp\b|sign-in code|password|passkey/i);
});

test("login button has compact, normally tracked title-case typography", () => {
  const tokens = classes(button(render("").tree));
  for (const expected of ["h-10", "px-5", "text-[0.8125rem]", "font-medium", "tracking-normal"]) {
    assert.ok(tokens.has(expected), `Missing button class: ${expected}`);
  }
  assert.ok(!tokens.has("uppercase"));
  assert.ok(!tokens.has("font-semibold"));
  assert.deepEqual([...tokens].filter((token) => token.startsWith("tracking-")), ["tracking-normal"]);
});

test("login button retains its premium gold pill and hover/active treatment", () => {
  const tokens = classes(button(render("").tree));
  for (const expected of [
    "rounded-full",
    "border-[rgba(218,194,142,0.56)]",
    "bg-[linear-gradient(180deg,rgba(237,222,184,0.98),rgba(199,166,104,0.96))]",
    "text-[rgba(29,58,48,0.96)]",
    "hover:-translate-y-px",
    "hover:border-[rgba(231,207,156,0.66)]",
    "hover:bg-[linear-gradient(180deg,rgba(242,228,192,0.98),rgba(184,149,88,0.98))]",
    "active:translate-y-0",
    "active:scale-[0.99]",
  ]) {
    assert.ok(tokens.has(expected), `Missing button class: ${expected}`);
  }
  assert.ok([...tokens].some((token) => token.startsWith("shadow-[")));
});

test("login button retains visible keyboard focus in light and dark themes", () => {
  const tokens = classes(button(render("").tree));
  for (const expected of [
    "focus-visible:ring-2",
    "focus-visible:ring-[#264238]",
    "focus-visible:ring-offset-2",
    "dark:focus-visible:ring-[#e9d8b4]",
    "dark:focus-visible:ring-offset-[#211c18]",
  ]) {
    assert.ok(tokens.has(expected), `Missing focus class: ${expected}`);
  }
});

test("login still assigns the unchanged Auth0 endpoint only when clicked", () => {
  const { tree, assigned } = render("");
  const action = button(tree);
  assert.equal(action.props.type, "button");
  assert.deepEqual(assigned, []);
  assert.equal(typeof action.props.onClick, "function");
  action.props.onClick!();
  assert.equal(authApi.AUTH0_LOGIN_ENDPOINT, "/api/auth/login");
  assert.deepEqual(assigned, [authApi.AUTH0_LOGIN_ENDPOINT]);
});

test("both callback error parameters retain the same alert and login action", () => {
  for (const query of ["?error=denied", "?error=", "?auth_error=denied", "?auth_error="]) {
    const { tree, assigned } = render(query);
    const alert = only(tree, (node) => node.props.role === "alert");
    assert.equal(textContent(alert), "Sign-in could not be completed. Please try again.");
    assert.ok(classes(alert).has("dark:bg-[rgba(92,54,45,0.28)]"));
    assert.ok(classes(alert).has("dark:text-[rgba(244,186,168,0.84)]"));
    button(tree).props.onClick!();
    assert.deepEqual(assigned, [authApi.AUTH0_LOGIN_ENDPOINT]);
  }
});

test("normal, unrelated-query and server renders do not show a callback alert", () => {
  for (const query of ["", "?other=error", undefined]) {
    const { tree, assigned } = render(query);
    assert.equal(nodes(tree).filter((node) => node.props.role === "alert").length, 0);
    assert.equal(textContent(button(tree)), "Sign in");
    assert.deepEqual(assigned, []);
  }
});

test("login retains its centered responsive card, Cuevion mark and dark appearance", () => {
  const { tree } = render("");
  const card = only(tree, (node) => node.type === "section");
  for (const expected of ["w-full", "rounded-[32px]", "p-8", "dark:bg-[rgba(33,28,24,0.82)]"]) {
    assert.ok(classes(card).has(expected));
  }
  assert.ok(classes(tree).has("min-h-screen"));
  assert.ok(classes(tree).has("dark:bg-[linear-gradient(180deg,#171411_0%,#221c17_100%)]"));
  assert.ok(nodes(tree).some((node) => classes(node).has("max-w-[560px]")));
  assert.ok(nodes(tree).some((node) => classes(node).has("text-center")));
  assert.equal(nodes(tree).filter((node) => classes(node).has("bg-[#264238]")).length, 1);
});
