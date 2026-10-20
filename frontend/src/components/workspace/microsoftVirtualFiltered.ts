import type { LiveInboxMessageSnapshot } from "../../lib/inboxConnectionApi";
import type { MicrosoftLearningRuleStore } from "./microsoftLearningRules";
import {
  normalizeSenderLearningKey,
  resolveSenderLearningEntry,
  type SenderCategoryLearningStore,
} from "../../lib/learningEngine";

export type MicrosoftSenderPlacement = "show_less" | "keep";
export type MicrosoftSenderPlacementStore = Record<string, MicrosoftSenderPlacement>;

const STORAGE_PREFIX = "cuevion-microsoft-filtered:v1";
const MAX_SENDER_RULES = 200;
const EMAIL_PATTERN = /^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$/;

function plainRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" &&
    !Array.isArray(value) &&
    (Object.getPrototypeOf(value) === Object.prototype ||
      Object.getPrototypeOf(value) === null);
}

/** An exact authenticated member + one mailbox; never an email-only key. */
export function microsoftFilteredStorageKey(
  workspaceId: string | null | undefined,
  userId: string | null | undefined,
  mailboxId: string | null | undefined,
): string | null {
  if (
    typeof workspaceId !== "string" || !workspaceId.trim() ||
    typeof userId !== "string" || !userId.trim() ||
    typeof mailboxId !== "string" ||
    !/^[A-Za-z0-9][A-Za-z0-9:_-]{0,159}$/.test(mailboxId)
  ) return null;
  return [STORAGE_PREFIX, workspaceId.trim(), userId.trim(), mailboxId]
    .map((part) => encodeURIComponent(part)).join(":");
}

/** Reject malformed or oversized persisted state, not just bad entries. */
export function parseMicrosoftSenderPlacements(value: string | null): MicrosoftSenderPlacementStore {
  if (!value || value.length > 24_000) return {};
  try {
    const raw: unknown = JSON.parse(value);
    if (!plainRecord(raw) || Object.keys(raw).length > MAX_SENDER_RULES) return {};
    const result: MicrosoftSenderPlacementStore = Object.create(null);
    for (const [key, mode] of Object.entries(raw)) {
      if (
        !EMAIL_PATTERN.test(key) ||
        key !== normalizeSenderLearningKey(key) ||
        (mode !== "show_less" && mode !== "keep")
      ) return {};
      result[key] = mode;
    }
    return result;
  } catch {
    return {};
  }
}

export function setMicrosoftSenderPlacement(
  previous: MicrosoftSenderPlacementStore,
  from: string,
  mode: MicrosoftSenderPlacement,
): MicrosoftSenderPlacementStore | null {
  const sender = normalizeSenderLearningKey(from);
  if (!EMAIL_PATTERN.test(sender) || !["show_less", "keep"].includes(mode)) {
    return null;
  }
  if (previous[sender] === mode) return previous;
  if (Object.keys(previous).length >= MAX_SENDER_RULES && !(sender in previous)) {
    return null;
  }
  return { ...previous, [sender]: mode };
}

/** Protected or uncertain items cannot be hidden, even by an older rule. */
export function isMicrosoftFilteredProtectionActive(
  message: LiveInboxMessageSnapshot,
): boolean {
  return (
    message.providerFolder !== "INBOX" ||
    !message.providerMessageId ||
    message.signal === "Priority" ||
    message.signal === "Active" ||
    message.signal === "For review" ||
    message.signal === "Shortlist" ||
    message.final_visibility === "show_priority" ||
    message.internalClassification === "reply" ||
    message.v7_final_priority?.trim().toUpperCase() === "PRIORITY" ||
    message.categoryConfidence === "low"
  );
}

export function classifyMicrosoftFilteredMessage(
  message: LiveInboxMessageSnapshot,
  learned: SenderCategoryLearningStore,
  placements: MicrosoftSenderPlacementStore,
  rules: MicrosoftLearningRuleStore = {},
): "Inbox" | "Filtered" {
  // Fail-open: an unclear or protected item always stays in the visible Inbox.
  if (isMicrosoftFilteredProtectionActive(message)) return "Inbox";

  const sender = normalizeSenderLearningKey(message.from);
  if (!EMAIL_PATTERN.test(sender)) return "Inbox";
  const rule = rules[sender];
  if (rule?.priority === "important" || rule?.priority === "normal") return "Inbox";
  if (rule?.priority === "show_less") return "Filtered";
  const explicit = placements[sender];
  if (explicit === "keep") return "Inbox";
  if (explicit === "show_less") return "Filtered";

  const learnedEntry = resolveSenderLearningEntry(sender, learned)?.entry;
  if (learnedEntry) {
    if (
      learnedEntry.senderBehavior === "always_prioritize" ||
      learnedEntry.senderBehavior === "normal" ||
      learnedEntry.senderBehavior === "do_not_learn" ||
      learnedEntry.sourcePrioritySelection === "Important"
    ) return "Inbox";
    if (
      learnedEntry.senderBehavior === "show_less" ||
      learnedEntry.sourcePrioritySelection === "Show Less" ||
      (learnedEntry.learnedCategory === "Updates" &&
        learnedEntry.mailboxAction === "move")
    ) return "Filtered";
  }

  // Reuse explicit Cuevion low-value routing when future Microsoft
  // classification supplies it. Never guess merely from an email subject.
  if (
    message.final_visibility === "show_low" &&
    message.action === "show_in_quiet_view" &&
    message.v7_final_priority?.trim().toUpperCase() === "LOW"
  ) return "Filtered";
  return "Inbox";
}

export function microsoftVisibleFolderRows(
  rows: ReadonlyArray<LiveInboxMessageSnapshot>,
  folder: string,
  learned: SenderCategoryLearningStore,
  placements: MicrosoftSenderPlacementStore,
  ready: boolean,
  rules: MicrosoftLearningRuleStore = {},
): LiveInboxMessageSnapshot[] {
  if (folder !== "Inbox" && folder !== "Filtered") return [...rows];
  if (!ready) return folder === "Filtered" ? [] : [...rows];
  return rows.filter((message) =>
    classifyMicrosoftFilteredMessage(message, learned, placements, rules) === folder
  );
}
