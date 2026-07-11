import assert from "node:assert/strict";
import { resolveOrganizerCategory } from "../components/workspace/bundleOrganizerFilters";

let passed = 0;
let failed = 0;

function test(name: string, fn: () => void) {
  try {
    fn();
    console.log(`  ✓ ${name}`);
    passed++;
  } catch (error) {
    console.error(`  ✗ ${name}`);
    console.error(`    ${(error as Error).message}`);
    failed++;
  }
}

console.log("\nbundleOrganizerFilters");

test("canonical Demo reply thread wins over learned and per-message Promo fallbacks", () => {
  assert.equal(
    resolveOrganizerCategory({
      canonicalThreadCategory: "demo",
      learnedLabelCategory: "promo",
      internalClassification: "reply",
      category: "promo",
    }),
    "demo",
  );
});

test("canonical Promo reply thread wins over learned and per-message Demo fallbacks", () => {
  assert.equal(
    resolveOrganizerCategory({
      canonicalThreadCategory: "promo",
      learnedLabelCategory: "demo",
      internalClassification: "reply",
      category: "demo",
    }),
    "promo",
  );
});

test("explicit Organizer manual category remains above canonical thread category", () => {
  assert.equal(
    resolveOrganizerCategory({
      manualCategory: "promo",
      canonicalThreadCategory: "demo",
    }),
    "promo",
  );
});

test("explicit manual content label remains above canonical thread category", () => {
  assert.equal(
    resolveOrganizerCategory({
      manualLabelCategory: "promo",
      canonicalThreadCategory: "demo",
    }),
    "promo",
  );
});

test("existing learned and per-message fallback remains when no canonical category exists", () => {
  assert.equal(
    resolveOrganizerCategory({
      learnedLabelCategory: "promo",
      internalClassification: "reply",
      category: "demo",
    }),
    "promo",
  );
});

if (failed > 0) {
  console.error(`\n${failed} bundleOrganizerFilters test(s) failed.`);
  process.exitCode = 1;
} else {
  console.log(`\n${passed} bundleOrganizerFilters tests passed.`);
}
