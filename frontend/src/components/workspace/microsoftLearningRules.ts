import type { LiveInboxMessageSnapshot } from "../../lib/inboxConnectionApi";
import { normalizeSenderLearningKey } from "../../lib/learningEngine";

export type MicrosoftLearningPriority = "important" | "normal" | "show_less";
export type MicrosoftLearningLabel = "Demo" | "Promo" | "Business" | "Finance" | "Update" | "Reply" | "Other";
export type MicrosoftLearningRule = {
  priority: MicrosoftLearningPriority;
  label: MicrosoftLearningLabel;
};
export type MicrosoftLearningRuleStore = Record<string, MicrosoftLearningRule>;

/** A single Graph Inbox message's explicit owner decisions (not a sender rule). */
export type MicrosoftMessageLearningRule = {
  priority?: "important" | "normal";
  label?: MicrosoftLearningLabel;
  placement?: "inbox" | "filtered";
};
export type MicrosoftMessageLearningStore = Record<string, MicrosoftMessageLearningRule>;

const MAX_RULES = 200;
const MAX_MESSAGE_RULES = 300;
const EMAIL = /^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$/;
const MESSAGE_ID = /^[\x21-\x7e]{1,1024}$/;
const PRIORITIES: MicrosoftLearningPriority[] = ["important", "normal", "show_less"];
const LABELS: MicrosoftLearningLabel[] = ["Demo", "Promo", "Business", "Finance", "Update", "Reply", "Other"];

export function parseMicrosoftMessageLearningRules(raw: string | null): MicrosoftMessageLearningStore {
  if (!raw || raw.length > 95000) return {};
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed) ||
        Object.keys(parsed).length > MAX_MESSAGE_RULES) return {};
    const result: MicrosoftMessageLearningStore = Object.create(null);
    for (const [id, value] of Object.entries(parsed)) {
      if (!MESSAGE_ID.test(id) || !value || typeof value !== "object" || Array.isArray(value)) return {};
      const item = value as Record<string, unknown>;
      if (Object.keys(item).length === 0 ||
          Object.keys(item).some((key) => !["priority", "label", "placement"].includes(key)) ||
          ("priority" in item && !["important", "normal"].includes(String(item.priority))) ||
          ("label" in item && !LABELS.includes(item.label as MicrosoftLearningLabel)) ||
          ("placement" in item && !["inbox", "filtered"].includes(String(item.placement)))) return {};
      result[id] = {
        ...(item.priority ? { priority: item.priority as "important" | "normal" } : {}),
        ...(item.label ? { label: item.label as MicrosoftLearningLabel } : {}),
        ...(item.placement ? { placement: item.placement as "inbox" | "filtered" } : {}),
      };
    }
    return result;
  } catch { return {}; }
}

export function saveMicrosoftMessageLearningRule(
  previous: MicrosoftMessageLearningStore, messageId: string, rule: MicrosoftMessageLearningRule,
): MicrosoftMessageLearningStore | null {
  if (!MESSAGE_ID.test(messageId) || !rule || Object.keys(rule).length === 0 ||
      Object.keys(rule).some((key) => !["priority", "label", "placement"].includes(key)) ||
      (rule.priority !== undefined && !["important", "normal"].includes(rule.priority)) ||
      (rule.label !== undefined && !LABELS.includes(rule.label)) ||
      (rule.placement !== undefined && !["inbox", "filtered"].includes(rule.placement)) ||
      (Object.keys(previous).length >= MAX_MESSAGE_RULES && !Object.prototype.hasOwnProperty.call(previous, messageId))) return null;
  const next = { ...previous, [messageId]: { ...rule } };
  return JSON.stringify(next).length <= 95000 ? next : null;
}

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
