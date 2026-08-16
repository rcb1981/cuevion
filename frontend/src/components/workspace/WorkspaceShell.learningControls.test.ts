import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const workspaceShellSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);

function sourceBetween(start: string, end: string) {
  const startIndex = workspaceShellSource.indexOf(start);
  const endIndex = workspaceShellSource.indexOf(end, startIndex + start.length);

  assert.notEqual(startIndex, -1, `${start} must exist`);
  assert.notEqual(endIndex, -1, `${end} must exist after ${start}`);
  return workspaceShellSource.slice(startIndex, endIndex);
}

const learningChoiceButtonSource = sourceBetween(
  "function LearningChoiceButton",
  "function ForYouView",
);
const forYouViewSource = sourceBetween(
  "function ForYouView",
  "export function WorkspaceShell",
);

assert.match(
  forYouViewSource,
  /<DesktopActionButton[\s\S]*?onClick=\{openRefineCuevionModal\}[\s\S]*?variant="primary"[\s\S]*?size="regular"[\s\S]*?>[\s\S]*?Refine Cuevion[\s\S]*?<\/DesktopActionButton>/,
  "Refine Cuevion must be a regular primary DesktopActionButton with its handler preserved",
);

const teachCardsSource = sourceBetween(
  "{teachCuevionActions.map((action) => (",
  "{aiSuggestionsEnabled && activeLearningModal === \"paste-rule\"",
);
assert.match(teachCardsSource, /<button\b/, "Teach Cuevion entries must remain native cards");
assert.doesNotMatch(
  teachCardsSource,
  /<DesktopActionButton\b/,
  "Teach Cuevion cards must not become ordinary action buttons",
);
assert.match(teachCardsSource, /onClick=\{action\.handler\}/);
assert.match(teachCardsSource, /min-h-\[8\.5rem\]/, "Teach card geometry must remain intact");

assert.match(learningChoiceButtonSource, /<button\b/, "Learning choices must stay native buttons");
assert.match(learningChoiceButtonSource, /aria-pressed=\{selected\}/);
assert.match(learningChoiceButtonSource, /h-8/, "Learning choices must use the compact 32px scale");
assert.match(learningChoiceButtonSource, /tracking-normal/);
assert.doesNotMatch(learningChoiceButtonSource, /\buppercase\b/);
assert.doesNotMatch(learningChoiceButtonSource, /tracking-\[/);
assert.match(learningChoiceButtonSource, /focus-visible:ring-2/);
assert.match(learningChoiceButtonSource, /disabled:pointer-events-none/);

for (const selectedExpression of [
  "selectedPasteRuleLabel === label",
  "selectedPasteRulePriority === priority",
  "selectedPasteRuleSenderBehavior === option.value",
  "selectedUncertainLabel === label",
  "selectedUncertainPriority === priority",
  "selectedRecentDecisionLabel === label",
  "selectedRecentDecisionPriority === priority",
  "selectedRecentDecisionSenderBehavior === option.value",
  "selectedLearningLabel === label",
  "selectedLearningPriority === priority",
  "selectedLearningSenderBehavior === option.value",
] as const) {
  assert.match(
    forYouViewSource,
    new RegExp(`<LearningChoiceButton[\\s\\S]*?selected=\\{${selectedExpression.replaceAll(".", "\\.")}\\}`),
    `${selectedExpression} must control semantic selected state`,
  );
}

assert.match(
  forYouViewSource,
  /<DesktopActionButton[\s\S]*?onClick=\{closeLearningModal\}[\s\S]*?variant="(?:secondary|tertiary)"[\s\S]*?size="compact"[\s\S]*?>[\s\S]*?Close[\s\S]*?<\/DesktopActionButton>/,
  "Learning header Close must be a compact non-primary action",
);
assert.match(
  forYouViewSource,
  /<DesktopActionButton[\s\S]*?disabled=\{!canSavePasteRule\}[\s\S]*?variant="primary"[\s\S]*?size="regular"[\s\S]*?onClick=\{\(\) => \{[\s\S]*?onSaveLearningRule\([\s\S]*?>[\s\S]*?Save learning rule[\s\S]*?<\/DesktopActionButton>/,
  "Save learning rule must be a regular primary action with validation and save authority preserved",
);
assert.match(
  forYouViewSource,
  /onClick=\{\(\) => setIsLearningDecisionEditorOpen\(true\)\}[\s\S]*?aria-expanded=\{isLearningDecisionEditorOpen\}/,
  "the Refine decision editor disclosure must expose expanded state",
);
assert.doesNotMatch(forYouViewSource, /aria-haspopup=/, "Learning has no menu or popover controls");

for (const handler of [
  "openRefineCuevionModal",
  "closeLearningModal",
  "persistActiveLearningSuggestionDecision",
  "persistActiveUncertainDecision",
  "onSaveLearningRule",
] as const) {
  assert.match(forYouViewSource, new RegExp(`\\b${handler}\\b`));
}
