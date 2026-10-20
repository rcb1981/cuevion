import type { LiveInboxMessageSnapshot } from "../../lib/inboxConnectionApi";
import { normalizeSenderLearningKey } from "../../lib/learningEngine";

export type MicrosoftLearningPriority = "important" | "normal" | "show_less";
export type MicrosoftLearningLabel = "Demo" | "Promo" | "Business" | "Finance" | "Update" | "Reply" | "Other";
export type MicrosoftLearningRule = {
  priority: MicrosoftLearningPriority;
  label: MicrosoftLearningLabel;
};
export type MicrosoftLearningRuleStore = Record<string, MicrosoftLearningRule>;

const MAX_RULES = 200;
const EMAIL = /^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$/;
const PRIORITIES: MicrosoftLearningPriority[] = ["important", "normal", "show_less"];
const LABELS: MicrosoftLearningLabel[] = ["Demo", "Promo", "Business", "Finance", "Update", "Reply", "Other"];

export function microsoftLearningRulesStorageKey(filteredStorageKey: string | null): string | null {
  return filteredStorageKey ? filteredStorageKey + ":learning-v1" : null;
}

export function parseMicrosoftLearningRules(raw: string | null): MicrosoftLearningRuleStore {
  if (!raw || raw.length > 26000) return {};
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed) ||
        Object.keys(parsed).length > MAX_RULES) return {};
    const result: MicrosoftLearningRuleStore = Object.create(null);
    for (const [key, value] of Object.entries(parsed)) {
      if (!EMAIL.test(key) || normalizeSenderLearningKey(key) !== key ||
          !value || typeof value !== "object" || Array.isArray(value)) return {};
      const item = value as Record<string, unknown>;
      if (Object.keys(item).length !== 2 ||
          !PRIORITIES.includes(item.priority as MicrosoftLearningPriority) ||
          !LABELS.includes(item.label as MicrosoftLearningLabel)) return {};
      result[key] = { priority: item.priority as MicrosoftLearningPriority, label: item.label as MicrosoftLearningLabel };
    }
    return result;
  } catch { return {}; }
}

export function saveMicrosoftLearningRule(
  previous: MicrosoftLearningRuleStore, from: string, rule: MicrosoftLearningRule,
): MicrosoftLearningRuleStore | null {
  const sender = normalizeSenderLearningKey(from);
  if (!EMAIL.test(sender) || !PRIORITIES.includes(rule.priority) || !LABELS.includes(rule.label) ||
      (Object.keys(previous).length >= MAX_RULES && !Object.prototype.hasOwnProperty.call(previous, sender))) return null;
  return { ...previous, [sender]: { priority: rule.priority, label: rule.label } };
}

export function isMicrosoftPriorityMessage(
  message: LiveInboxMessageSnapshot, rules: MicrosoftLearningRuleStore,
): boolean {
  const sender = normalizeSenderLearningKey(message.from);
  const rule = rules[sender];
  if (rule?.priority === "important") return true;
  if (rule?.priority === "normal" || rule?.priority === "show_less") return false;
  return message.signal === "Priority" || message.final_visibility === "show_priority" ||
    message.v7_final_priority?.trim().toUpperCase() === "PRIORITY";
}
