import type { LiveInboxMessageSnapshot } from "../../lib/inboxConnectionApi";
import type { MicrosoftLearningRuleStore, MicrosoftMessageLearningStore } from "./microsoftLearningRules";
import { isMicrosoftPriorityMessage } from "./microsoftLearningRules";
import type { OnboardingState } from "../../types/onboarding";
import {
  normalizeSenderLearningKey,
  resolveSenderLearningEntry,
  type SenderCategoryLearningStore,
} from "../../lib/learningEngine";

export type MicrosoftSenderPlacement = "show_less" | "keep";
export type MicrosoftSenderPlacementStore = Record<string, MicrosoftSenderPlacement>;
export type MicrosoftFocusPreferences = OnboardingState["focusPreferences"];
export type MicrosoftFocusCategory = keyof MicrosoftFocusPreferences;

const STORAGE_PREFIX = "cuevion-microsoft-filtered:v1";
const MAX_SENDER_RULES = 200;
const EMAIL_PATTERN = /^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$/;

/**
 * Conservative equivalent of Gmail/IMAP's low-value newsletter and retail
 * marketing signals for the Microsoft Graph LIST projection. Graph provides
 * only the subject and body preview here: never request full message bodies,
 * change the Outlook folder, or treat a bare "promo" as sufficient evidence.
 */
export function isMicrosoftLikelyMarketingMessage(
  message: Pick<LiveInboxMessageSnapshot, "from" | "sender" | "subject" | "snippet">,
): boolean {
  const subject = message.subject.toLocaleLowerCase();
  const preview = message.snippet.toLocaleLowerCase();
  const searchable = `${subject} ${preview}`;
  const sender = normalizeSenderLearningKey(message.from);
  const senderLocalPart = sender.split("@")[0] ?? "";

  // Action-needed messages, real music demos/promos, security and financial
  // correspondence belong in Inbox even if their footer advertises something.
  if (
    /^(?:re|fw|fwd)\s*:/i.test(subject) ||
    /\b(?:actie vereist|action required|verification code|verificatiecode|security alert|beveiligingsmelding|password reset|wachtwoord|new sign-in|nieuwe aanmelding|nieuwe apps verbonden|invoice|factuur|receipt|ontvangstbewijs|payment|betaling|bank transfer|royalty statement|contract|agreement|overeenkomst|please review|please confirm|could you|can you|order confirmation|bestelbevestiging)\b/i.test(searchable) ||
    /\b(?:demo submission|demo voor|dj promo|dj support|track submission|music submission|promo download|promobox|inflyte|fatdrop)\b/i.test(searchable) ||
    (/(?:records|recordings|label|artist|dj)/i.test(message.sender) &&
      /\b(?:new track|new release|upcoming single|remix|ep release)\b/i.test(searchable))
  ) return false;

  // Explicit newsletter or retail offer wording is stronger than a generic
  // "update", "promo", "offer", or "actie" (which can be business mail).
  if (
    /\b(?:newsletter|nieuwsbrief|black friday|cyber monday|flash sale|uitverkoop|weekaanbieding(?:en)?|actieprijzen|kortingscode|promocode|couponcode|speciale aanbieding|special offer|limited.time offer|last chance sale|sale ends|sale starts|bespaar\s+\d{1,2}%|save\s+\d{1,2}%|\d{1,2}%\s*(?:off|korting|discount))\b/i.test(searchable)
  ) return true;

  // Cold retail/financial lead-generation is not the same as a real mortgage
  // statement or a personal finance request. Require both a sales topic and a
  // call to action, never just the word "hypotheek" or "offerte".
  if (
    /\b(?:overwaarde van uw hypotheek|gratis waardebepaling|claim uw voordeel|exclusieve deal|vrijblijvende berekening)\b/i.test(searchable) &&
    /\b(?:bereken|berekening|ontdek|bekijk|aanbod|bespaar|gratis|voordeel)\b/i.test(searchable)
  ) return true;

  const bulkSender = /(?:^|[._+-])(?:newsletter|nieuwsbrief|marketing|campaigns?|deals?|offers?|promotions?)(?:$|[._+-])/.test(senderLocalPart);
  const unsubscribe = /\b(?:unsubscribe|uitschrijven|afmelden|view in browser|bekijk online|read online)\b/i.test(searchable);
  const retailOffer = /\b(?:korting|aanbieding(?:en)?|discount|voucher|actieprijs|retail sale|shopping deal|limited time|shop now)\b/i.test(searchable);
  return (bulkSender && (unsubscribe || retailOffer || /\b(?:news|update|nieuws|aanbod|offer|deal)\b/i.test(subject))) ||
    (unsubscribe && retailOffer);
}

/**
 * Microsoft Graph's LIST response has no Cuevion V7 classification. Match only
 * recognisable music-industry/workflow evidence from its short summary. The
 * same ten *effective* onboarding/Settings categories drive Gmail/IMAP; those
 * pipelines and their saved mail state are never changed here.
 *
 * A weak keyword such as "promo", "offer", "finance" or "release" is never
 * enough on its own to quiet an actual message.
 */
export function microsoftFocusCategory(
  message: LiveInboxMessageSnapshot,
): MicrosoftFocusCategory | null {
  // The real Graph projection requires these strings; fail open for a partial
  // or legacy snapshot rather than inventing a focus classification.
  if (typeof message.subject !== "string" ||
      typeof message.snippet !== "string" ||
      typeof message.sender !== "string" ||
      typeof message.from !== "string") return null;
  if (/^\s*(?:re|fw|fwd)\s*:/i.test(message.subject) ||
      message.internalClassification === "reply") return null;

  const trustedClassification: Record<string, MicrosoftFocusCategory> = {
    demo: "demos", high_priority_demo: "demos",
    promo: "promo", promo_reminder: "promoReminders",
    finance: "finance", royalty_statement: "royalties",
    business: "business", business_reminder: "paymentReminders",
    distributor_update: "distribution", workflow_update: "updates", info: "updates",
    legal: "legal",
  };
  const trusted = message.internalClassification && trustedClassification[message.internalClassification];
  if (trusted) return trusted;

  const subject = message.subject.toLocaleLowerCase();
  const content = `${subject} ${message.snippet.toLocaleLowerCase()}`;
  const sender = `${message.sender} ${message.from}`.toLocaleLowerCase();

  // Do not quietly file sign-in warnings, access/security incidents or
  // confirmation requests based on incidental finance/update keywords.
  if (/\b(?:verification code|verificatiecode|security alert|beveiligingsmelding|password reset|wachtwoord (?:wijzigen|herstellen)|new sign.in|nieuwe aanmelding|suspicious sign.in|account locked|actie vereist|action required)\b/i.test(content)) {
    return null;
  }
  // Commercial newsletters are a separate low-value rule. A mailer saying
  // "new promo" must not inherit the musician's Promo setting by accident.
  if (isMicrosoftLikelyMarketingMessage(message)) return null;

  const musicPromo =
    /\b(?:inflyte|fatdrop|promobox|dj promo|promo download|promo invite|promo access|promo servicing|for your sets|dj support|promos? for djs)\b/i.test(content) ||
    (/\b(?:promo|promotional|listen and download)\b/i.test(content) &&
      /\b(?:new track|new release|upcoming single|remix|ep|label|artist|record)\b/i.test(`${content} ${sender}`)) ||
    (/\b(?:inflyteapp|fatdrop|promobox)\b/i.test(sender) &&
      /\b(?:track|release|listen|download|promo|invite)\b/i.test(content));

  if (musicPromo &&
      /\b(?:promo reminder|reminder|herinnering|still available|waiting for you|last chance to download|access request)\b/i.test(content)) {
    return "promoReminders";
  }
  if (/\b(?:payment reminder|betalingsherinnering|overdue invoice|past due payment|invoice overdue|outstanding balance|openstaande factuur|unpaid invoice)\b/i.test(content)) {
    return "paymentReminders";
  }
  if (/\b(?:royalt(?:y|ies)(?: statement| report| payment| accounting| payout| settlement)?|royaltyworx|mechanical royalties|publishing statement)\b/i.test(content) &&
      !/\b(?:buy|subscribe|free trial|discount|special offer)\b/i.test(content)) {
    return "royalties";
  }
  if (/\b(?:invoice|factuur|credit note|creditnota|receipt|ontvangstbewijs|payment confirmation|betalingsbevestiging|tax statement|btw.aangifte|bank statement|financial statement)\b/i.test(content)) {
    return "finance";
  }
  if (/\b(?:license agreement|licence agreement|licensing contract|clearance request|sample clearance|master rights|publishing rights|signature requested|sign the agreement|contract for signature|deal memo|synchroni[sz]ation agreement|splits agreement)\b/i.test(content)) {
    return "legal";
  }
  if (/\b(?:labelworx|symphonic distribution|distribution report|distributor update|dsp delivery|delivery status|release delivered|release takedown|takedown request|isrc validation|beatport delivery|catalogue delivery|catalog delivery)\b/i.test(`${content} ${sender}`)) {
    return "distribution";
  }
  if (/\b(?:demo submission|track submission|music submission|submit(?:ting)? (?:my|our|a) (?:demo|track)|private soundcloud demo|listen to my demo|demo for (?:your|the) label|new demo|my demo)\b/i.test(content) ||
      (/\b(?:demo|unreleased track)\b/i.test(subject) &&
        /\b(?:listen|private link|soundcloud|dropbox|artist|label|track|feedback)\b/i.test(content))) {
    return "demos";
  }
  if (musicPromo) return "promo";
  if (/\b(?:booking request|booking enquiry|partnership proposal|partnership request|meeting request|business proposal|collaboration request|a&r meeting|client enquiry|licensing enquiry|licensing inquiry)\b/i.test(content)) {
    return "business";
  }
  if (/\b(?:scheduled maintenance|service update|system update|platform notice|account update|workflow notification|service notification|status update|policy update)\b/i.test(content)) {
    return "updates";
  }
  return null;
}

function microsoftFocusWantsLow(
  message: LiveInboxMessageSnapshot,
  preferences: MicrosoftFocusPreferences | undefined,
): boolean {
  if (!preferences) return false;
  const category = microsoftFocusCategory(message);
  return category !== null && preferences[category] === "low";
}

/** User-selected Important always wins; Low never leaks into Priority view. */
export function isMicrosoftPriorityUnderFocus(
  message: LiveInboxMessageSnapshot,
  rules: MicrosoftLearningRuleStore,
  preferences?: MicrosoftFocusPreferences,
  messageRules: MicrosoftMessageLearningStore = {},
): boolean {
  const exact = messageRules[message.providerMessageId ?? ""];
  if (exact?.priority === "important") return true;
  if (exact?.priority === "normal") return false;
  const senderRule = rules[normalizeSenderLearningKey(message.from)];
  if (senderRule) return isMicrosoftPriorityMessage(message, rules);
  // The user's Normal/Low level, not an older backend Priority flag, is
  // authoritative for a recognised category. Only a sender explicitly marked
  // Important can elevate it into Priority.
  if (preferences && microsoftFocusCategory(message) !== null) return false;
  return isMicrosoftPriorityMessage(message, rules);
}

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

/** Replies, uncertain IDs and account-security mail remain visible regardless
 * of a focus preference or a learned sender rule. */
function isMicrosoftHardFilteredProtectionActive(
  message: LiveInboxMessageSnapshot,
): boolean {
  return (
    message.providerFolder !== "INBOX" ||
    !message.providerMessageId ||
    message.internalClassification === "reply" ||
    /^\s*(?:re|fw|fwd)\s*:/i.test(message.subject) ||
    message.categoryConfidence === "low" ||
    /\b(?:verification code|verificatiecode|security alert|beveiligingsmelding|password reset|suspicious sign.in|account locked|new sign.in|nieuwe aanmelding)\b/i.test(`${message.subject} ${message.snippet}`) ||
    // Account security notifications must not disappear through an unsafe
    // learned sender preference or a stale Filtered rule.
    /^(?:accountprotection\.microsoft\.com|accounts\.google\.com|account\.microsoft\.com)$/.test(
      normalizeSenderLearningKey(message.from).split("@")[1] ?? "",
    )
  );
}

/** Backend priority remains protected from automatic/learned quieting unless
 * the owner explicitly set Low for a recognised type of mail. */
export function isMicrosoftFilteredProtectionActive(
  message: LiveInboxMessageSnapshot,
): boolean {
  return isMicrosoftHardFilteredProtectionActive(message) ||
    message.signal === "Priority" ||
    message.signal === "Active" ||
    message.signal === "For review" ||
    message.signal === "Shortlist" ||
    message.final_visibility === "show_priority" ||
    message.v7_final_priority?.trim().toUpperCase() === "PRIORITY";
}

export function classifyMicrosoftFilteredMessage(
  message: LiveInboxMessageSnapshot,
  learned: SenderCategoryLearningStore,
  placements: MicrosoftSenderPlacementStore,
  rules: MicrosoftLearningRuleStore = {},
  focusPreferences?: MicrosoftFocusPreferences,
  messageRules: MicrosoftMessageLearningStore = {},
): "Inbox" | "Filtered" {
  // The owner's Low choice can supersede a stale automated Priority signal,
  // but cannot supersede a reply, uncertain identity or security protection.
  const ownerSelectedLow = microsoftFocusWantsLow(message, focusPreferences);
  if (isMicrosoftHardFilteredProtectionActive(message) ||
      (isMicrosoftFilteredProtectionActive(message) && !ownerSelectedLow)) return "Inbox";

  // An explicit decision on this exact Graph message must not silently teach
  // the whole sender, and must beat historical sender-wide Low/Show Less rules.
  const exact = messageRules[message.providerMessageId ?? ""];
  if (exact?.priority === "important" || exact?.placement === "inbox") return "Inbox";
  if (exact?.placement === "filtered" && !isMicrosoftFilteredProtectionActive(message)) return "Filtered";

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

  // The effective mailbox preferences from onboarding and Settings have the
  // same authority for Microsoft as for Gmail/IMAP. Never auto-quiet an
  // unrecognised message merely because an unrelated category is set Low.
  if (ownerSelectedLow) return "Filtered";

  // Reuse explicit Cuevion low-value routing when future Microsoft
  // classification supplies it.
  if (
    message.final_visibility === "show_low" &&
    message.action === "show_in_quiet_view" &&
    message.v7_final_priority?.trim().toUpperCase() === "LOW"
  ) return "Filtered";
  if (isMicrosoftLikelyMarketingMessage(message)) return "Filtered";
  return "Inbox";
}

export function microsoftVisibleFolderRows(
  rows: ReadonlyArray<LiveInboxMessageSnapshot>,
  folder: string,
  learned: SenderCategoryLearningStore,
  placements: MicrosoftSenderPlacementStore,
  ready: boolean,
  rules: MicrosoftLearningRuleStore = {},
  focusPreferences?: MicrosoftFocusPreferences,
  messageRules: MicrosoftMessageLearningStore = {},
): LiveInboxMessageSnapshot[] {
  if (folder !== "Inbox" && folder !== "Filtered") return [...rows];
  if (!ready) return folder === "Filtered" ? [] : [...rows];
  return rows.filter((message) =>
    classifyMicrosoftFilteredMessage(message, learned, placements, rules, focusPreferences, messageRules) === folder
  );
}
