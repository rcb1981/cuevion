import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import ts from "typescript";
import postcss from "postcss";
import tailwindcss from "tailwindcss";
import selectorParser from "postcss-selector-parser";

// Evaluate the actual class expressions without mounting the workspace or calling APIs.
const buttonSource = readFileSync(resolve("src/components/ui/DesktopActionButton.tsx"), "utf8");
const workspaceSource = readFileSync(resolve("src/components/workspace/WorkspaceShell.tsx"), "utf8");
const buttonAst = ts.createSourceFile("button.tsx", buttonSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const workspaceAst = ts.createSourceFile("workspace.tsx", workspaceSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const context: Record<string, unknown> = {};

function declaration(ast: ts.SourceFile, name: string) {
  let expression = "";
  function visit(node: ts.Node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(ast) === name && node.initializer) {
      expression = node.initializer.getText(ast);
    }
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.ok(expression, `Missing declaration: ${name}`);
  return runInNewContext(`(${expression})`, context);
}

for (const name of ["baseClassName", "desktopActionButtonGeometry", "variantClassNames"]) {
  context[name] = declaration(buttonAst, name);
}
for (const name of ["primaryActionSurfaceClass", "desktopConfirmationPrimaryActionClass", "modalSecondaryActionButtonClass"]) {
  context[name] = declaration(workspaceAst, name);
}

function actionClass(onClickText: string, state: Record<string, unknown> = {}) {
  const matches: ts.JsxAttribute[] = [];
  function visit(node: ts.Node) {
    if (ts.isJsxOpeningElement(node) && node.tagName.getText(workspaceAst) === "button") {
      const attrs = node.attributes.properties.filter(ts.isJsxAttribute);
      if (attrs.some(attr => attr.name.getText(workspaceAst) === "onClick" && attr.getText(workspaceAst).includes(onClickText))) {
        const attr = attrs.find(attr => attr.name.getText(workspaceAst) === "className");
        if (attr) matches.push(attr);
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(workspaceAst);
  assert.equal(matches.length, 1, `Expected exactly one action: ${onClickText}`);
  const initializer = matches[0].initializer!;
  const expression = ts.isJsxExpression(initializer) ? initializer.expression!.getText(workspaceAst) : initializer.getText(workspaceAst);
  return runInNewContext(`(${expression})`, { ...context, ...state }) as string;
}

const variants = context.variantClassNames as Record<string, string>;
let sharedButtonClassExpression = "";
function findSharedButton(node: ts.Node) {
  if (ts.isJsxSelfClosingElement(node) && node.tagName.getText(buttonAst) === "button") {
    const attr = node.attributes.properties.filter(ts.isJsxAttribute).find(attr => attr.name.getText(buttonAst) === "className");
    assert.ok(attr?.initializer && ts.isJsxExpression(attr.initializer));
    sharedButtonClassExpression = attr.initializer.expression!.getText(buttonAst);
  }
  ts.forEachChild(node, findSharedButton);
}
findSharedButton(buttonAst);
assert.ok(sharedButtonClassExpression);
export const ctaGeometryCases: Record<string, string> = {};
for (const size of ["regular", "compact"]) {
  for (const variant of ["primary", "secondary", "tertiary", "destructive"]) {
    ctaGeometryCases[`${size}-${variant}`] = runInNewContext(`(${sharedButtonClassExpression})`, {
      ...context, size, variant, className: undefined,
    });
  }
}
for (const [name, handler] of Object.entries({
  "change-applied-ok": "setManualChangeConfirmationMessage(null)",
  "failure-close": "setPriorityWorkflowFailure(null)",
  "failure-retry": "priorityWorkflowFailure?.retry()",
  "removal-failure-ok": "setPrioritySemanticNewInboundDismissalFailure(null)",
  "team-no": "onClick={() => setActiveTeamConfirmation(null)}",
})) {
  ctaGeometryCases[name] = actionClass(handler);
}
for (const pending of [false, true]) {
  ctaGeometryCases[`team-confirm-${pending ? "loading" : "idle"}`] = actionClass('if (activeTeamConfirmation === "revoke"', { isSendingTeamInvite: pending });
}
for (const mobile of [false, true]) {
  for (const state of ["enabled", "disabled", "loading"]) {
    ctaGeometryCases[`invite-${mobile ? "mobile" : "desktop"}-${state}`] = actionClass("sendInviteFlowReply", {
      inviteReplyDraft: state === "disabled" ? "" : "Reply",
      isMobileWorkspaceViewport: mobile,
    });
  }
  ctaGeometryCases[`invite-${mobile ? "mobile" : "desktop"}-done`] = actionClass("markInviteFlowDone", { isMobileWorkspaceViewport: mobile });
}

export async function getCtaGeometryCss() {
  const result = await postcss([tailwindcss({ content: [{ raw: buttonSource + workspaceSource }] })])
    .process("@tailwind base; @tailwind utilities;", { from: undefined });
  return result.css;
}

function declarationsFor(css: string, className: string) {
  const classes = new Set(className.split(/\s+/));
  const result: Record<string, string> = {};
  postcss.parse(css).walkRules(rule => {
    if (rule.parent?.type !== "root") return;
    const selectors = selectorParser().astSync(rule.selector);
    if (selectors.nodes.length !== 1 || selectors.first.nodes.length !== 1) return;
    const selector = selectors.first.first;
    if (selector.type !== "class" || !classes.has(selector.value)) return;
    rule.walkDecls(decl => { result[decl.prop] = decl.value; });
  });
  return result;
}

test("desktop CTA geometry uses the generated CSS cascade, across variants and action states", async () => {
  const css = await getCtaGeometryCss();
  for (const [name, classes] of Object.entries(ctaGeometryCases)) {
    const actual = declarationsFor(css, classes);
    if (name.includes("mobile")) {
      // This slice preserves the pre-existing narrow-screen sizes rather than shrinking them.
      assert.equal(actual.height, name.endsWith("enabled") || name.endsWith("loading") ? "2.25rem" : "2.5rem", name);
      assert.equal(actual["padding-left"], "1.25rem", name);
      continue;
    }
    const compact = name.startsWith("compact-");
    const expected = {
      height: compact ? "2rem" : "2.25rem",
      "padding-left": compact ? "0.75rem" : "1rem",
      "padding-right": compact ? "0.75rem" : "1rem",
      "border-radius": "9999px",
      "font-size": compact ? "0.75rem" : "0.8125rem",
      "line-height": compact ? "1rem" : "1.25rem",
      "font-weight": "500",
      "letter-spacing": "0em",
    };
    for (const [property, value] of Object.entries(expected)) assert.equal(actual[property], value, `${name}: ${property}`);
    assert.notEqual(actual["text-transform"], "uppercase", name);
  }
  const ok = declarationsFor(css, ctaGeometryCases["change-applied-ok"]);
  assert.equal(ok.width, undefined, "OK remains content-driven");
  assert.equal(ok["min-width"], undefined, "OK has no imposed minimum width");
  assert.equal(ok["max-width"], undefined, "OK has no imposed maximum width");
  assert.equal(declarationsFor(css, ctaGeometryCases["failure-close"]).width, "7.5rem", "Preserve the existing explicit Close width");
  assert.equal(ctaGeometryCases["invite-desktop-enabled"], ctaGeometryCases["invite-desktop-loading"]);
  for (const name of Object.keys(variants)) {
    assert.match(ctaGeometryCases[`regular-${name}`], /disabled:pointer-events-none/);
    assert.match(ctaGeometryCases[`regular-${name}`], /focus-visible:ring-2/);
  }
});
