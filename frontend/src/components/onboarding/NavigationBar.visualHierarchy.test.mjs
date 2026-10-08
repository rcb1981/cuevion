import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const source = readFileSync(
  resolve(process.cwd(), "src/components/onboarding/NavigationBar.tsx"),
  "utf8",
);

assert.doesNotMatch(
  source,
  /bg-pine|hover:bg-moss/,
  "onboarding navigation must not use the green action-button treatment",
);

assert.match(
  source,
  /const backNavigationClass =[\s\S]*?border-\[rgba\(199,166,104,0\.42\)\][\s\S]*?bg-white\/72/,
  "Back must use the quieter champagne-outline treatment",
);

assert.match(
  source,
  /const forwardNavigationClass =[\s\S]*?bg-\[linear-gradient\(180deg,rgba\(237,222,184,0\.98\),rgba\(199,166,104,0\.96\)\)\]/,
  "forward onboarding navigation must use the canonical champagne gradient",
);

assert.match(
  source,
  /className=\{backNavigationClass\}/,
  "Back must use the secondary navigation class",
);

assert.match(
  source,
  /className=\{forwardNavigationClass\}/,
  "Next / Finish / Open workspace must use the primary navigation class",
);

assert.match(
  source,
  /backNavigationClass =[\s\S]*?disabled:text-ink\/42/,
  "disabled Back must remain readable while visibly disabled",
);

assert.match(
  source,
  /forwardNavigationClass =[\s\S]*?disabled:text-\[rgba\(29,58,48,0\.52\)\]/,
  "disabled forward navigation must remain readable while visibly disabled",
);
