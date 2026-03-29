import {
  memo,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type ChangeEvent,
  type ClipboardEvent as ReactClipboardEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type DragEvent,
  type MouseEvent,
  type ReactNode,
  type SetStateAction,
} from "react";
import { createPortal } from "react-dom";
import { onboardingText } from "../../copy/onboardingCopy";
import {
  ReviewDetailView as ReviewModuleDetailView,
  ReviewListView as ReviewModuleListView,
  getReviewTargetEyebrow,
  isReviewWorkspaceTarget,
  useReviewModuleState,
} from "./review/ReviewModule";
import type { ReviewItem, ReviewWorkspaceTarget } from "./review/types";
import type {
  CustomInboxDefinition,
  CustomImapSettings,
  InboxId,
  OnboardingState,
  PresetInboxId,
  ProviderId,
} from "../../types/onboarding";
import { NavigationBar } from "../onboarding/NavigationBar";
import {
  applyProviderDefaults,
  getPasswordLabel,
  isImapCredentialsProvider,
} from "../../lib/inboxProviderDefaults";
import {
  readLiveInboxSnapshots,
  saveLiveInboxSnapshot,
} from "../../lib/liveInboxSnapshots";
import {
  connectInboxWithImap,
  sendGmailMessage,
  type SendInboxAttachmentRequest,
  type LiveInboxMessageSnapshot,
} from "../../lib/inboxConnectionApi";

const primaryNavigationItems = [
  { section: "Dashboard", label: "Dashboard", shortLabel: "Dash" },
  { section: "For You", label: "For You", shortLabel: "For" },
  { section: "Priority", label: "Priority", shortLabel: "Pri" },
  { section: "Inboxes", label: "Inboxes", shortLabel: "Box" },
  { section: "Activity", label: "Activity", shortLabel: "Act" },
  { section: "Notifications", label: "Notifications", shortLabel: "Note" },
  { section: "Team", label: "Team", shortLabel: "Team" },
] as const;

const utilityNavigationItems = [
  { section: "Settings", label: "Settings", shortLabel: "Set" },
  { section: "Help", label: "Help", shortLabel: "Help" },
  { section: "Contact", label: "Contact", shortLabel: "Talk" },
] as const;

type WorkspaceSection =
  | "Dashboard"
  | "For You"
  | "Priority"
  | "Inboxes"
  | "Activity"
  | "Notifications"
  | "Team"
  | "Settings"
  | "Help"
  | "Contact";
type WorkspaceDataMode = "demo" | "live";
type ReviewFilter = "All priority" | "Priority";
type InboxFilter = "All inboxes" | "Connected";
type ForYouContext = "Main" | "Promo";
type WorkbenchSection = "Activity" | "Notifications" | "Team";
type UtilitySection = "Help" | "Contact";
type TeamAccessLevel = "Admin" | "Editor" | "Review" | "Limited";
type AuthenticatedCuevionUser = {
  email: string;
  name: string;
  userType: "member" | "guest";
};
type CollaborationInviteRoute = {
  inviteToken: string;
  messageId?: string;
  inviteeEmail?: string;
  status?: string;
};
type PendingTeamInvitation = {
  inviter: string;
  accessLevel: TeamAccessLevel;
  selectedInboxes: string[];
} | null;
type TeamMemberStatus =
  | "Active"
  | "Invited"
  | "Access removed"
  | "Invite cancelled";
type TeamMemberEntry = {
  name: string;
  email: string;
  accessLevel: TeamAccessLevel;
  selectedInboxes: string[];
  status: TeamMemberStatus;
};
type TeamMembershipEntry = {
  name: string;
  email: string;
  accessLevel: TeamAccessLevel;
  selectedInboxes: string[];
  status: "Active";
};
type ContactTicketStatus = "Open" | "In progress" | "Resolved" | "Cancelled";
type ContactTicketMessage = {
  senderType: "user" | "cuevion";
  body: string;
  timestamp: string;
};
type ContactTicket = {
  id: string;
  subject: string;
  status: ContactTicketStatus;
  updatedAt: string;
  messages: ContactTicketMessage[];
};

const getUnreadPreviewIds = (messages: Array<{ id: string; unread?: boolean }>) =>
  messages.filter((message) => message.unread).map((message) => message.id).slice(0, 8);

type MessageIdentitySource = {
  id?: string | null;
  imapUid?: string | null;
  subject?: string | null;
  from?: string | null;
  timestamp?: string | null;
};

type MessageUnreadOverrideStore = Record<string, boolean>;

function buildStablePreviewIdentity(message: MessageIdentitySource) {
  return `${message.subject ?? ""}|${message.from ?? ""}|${message.timestamp ?? ""}`;
}

function getCanonicalMessageIdentityKey(message: MessageIdentitySource) {
  if (message.imapUid) {
    return `imap:${message.imapUid}`;
  }

  if (message.id) {
    return `id:${message.id}`;
  }

  return `preview:${buildStablePreviewIdentity(message)}`;
}

function getCanonicalMessageIdentityKeys(message: MessageIdentitySource) {
  const keys: string[] = [];

  if (message.imapUid) {
    keys.push(`imap:${message.imapUid}`);
  }

  if (message.id) {
    keys.push(`id:${message.id}`);
  }

  keys.push(`preview:${buildStablePreviewIdentity(message)}`);

  return keys;
}

function findMatchingMessageByIdentity<T>(
  message: MessageIdentitySource,
  indexes: {
    byId: Map<string, T>;
    byImapUid: Map<string, T>;
    byPreviewIdentity: Map<string, T>;
  },
) {
  if (message.imapUid) {
    const matchedByImapUid = indexes.byImapUid.get(message.imapUid);

    if (matchedByImapUid) {
      return matchedByImapUid;
    }
  }

  if (message.id) {
    const matchedById = indexes.byId.get(message.id);

    if (matchedById) {
      return matchedById;
    }
  }

  return indexes.byPreviewIdentity.get(buildStablePreviewIdentity(message));
}

function resolveUnreadOverride(
  overrides: MessageUnreadOverrideStore,
  message: MessageIdentitySource,
) {
  for (const key of getCanonicalMessageIdentityKeys(message)) {
    if (Object.prototype.hasOwnProperty.call(overrides, key)) {
      return overrides[key];
    }
  }

  return undefined;
}

type LearningLaunchRequest =
  | {
      key: string;
      modal:
        | "review-uncertain"
        | "refine-cuevion"
        | "recent-decisions"
        | "edit-recent-decision";
      recentDecisionIndex?: number;
    }
  | null;
type WorkspaceTarget =
  | ReviewWorkspaceTarget
  | "priority-queue"
  | "contract-thread"
  | "promo-context"
  | "reply-conversation"
  | "universal-reply";
type MailboxReturnContext = {
  section: WorkspaceSection;
  reviewFilter: ReviewFilter;
  inboxFilter: InboxFilter;
  forYouContext: ForYouContext;
  target: WorkspaceTarget | null;
  mailboxId: InboxId | null;
};

type OrderedMailbox = {
  id: InboxId;
  title: string;
  email: string;
  detail: string;
  state: string;
};
type ComposeMode = "new" | "reply" | "reply_all" | "forward";
type SignatureLayoutMode = "text-only" | "logo-below" | "logo-left";
type InboxSignatureSettings = {
  html: string;
  useByDefault: boolean;
  logoImageUrl: string | null;
  layout: SignatureLayoutMode;
  showDivider: boolean;
};
type InboxSignatureStore = Partial<Record<InboxId, InboxSignatureSettings>>;
type InboxOutOfOfficeSettings = {
  enabled: boolean;
  message: string;
};
type InboxOutOfOfficeStore = Partial<Record<InboxId, InboxOutOfOfficeSettings>>;
type OutOfOfficeReplyLogStore = Partial<Record<InboxId, Record<string, number>>>;
type SmartFolderRuleField = "From" | "Subject" | "Domain";
type SmartFolderRule = {
  id: string;
  field: SmartFolderRuleField;
  operator: "contains";
  value: string;
};
type SmartFolderDefinition = {
  id: string;
  name: string;
  scope: "all" | "selected";
  selectedInboxIds: InboxId[];
  rules: SmartFolderRule[];
};

type CuevionMessageCategory = "Primary" | "Promo" | "Updates";
type CuevionCategorySource = "system" | "user" | "learned";
type CuevionCategoryConfidence = "low" | "medium" | "high";
type MailMessageSuggestion = {
  type: "confirm_category";
  proposedCategory: CuevionMessageCategory;
};
type MailMessageBehaviorSuggestion = {
  type: "auto_category";
  sender: string;
  category: CuevionMessageCategory;
};
type MailMessageOwner = {
  userId: string;
  confidence: "low" | "medium" | "high";
  source: "implicit";
};
type MailMessagePriorityScore = "low" | "medium" | "high";
type MailMessageFocusSignal = "attention" | null;
type ManualPriorityOverride = "priority" | "removed";
type LearningDecisionSourceContext =
  | "refine"
  | "uncertain"
  | "paste_sender_or_domain";
type LearningDecisionPrioritySelection = "Important" | "Normal" | "Show Less" | "Spam";
type SenderCategoryLearningEntry = {
  learnedCategory: CuevionMessageCategory;
  learnedFromCount: number;
  autoCategoryEnabled?: boolean;
  mailboxAction?: "keep" | "move";
  sourceContext?: LearningDecisionSourceContext;
  sourcePrioritySelection?: LearningDecisionPrioritySelection;
  sourceMailboxId?: InboxId | null;
  sourceCurrentMailboxId?: InboxId | null;
  updatedAt?: string;
};
type SenderCategoryLearningStore = Record<string, SenderCategoryLearningEntry>;
type MessageOwnershipInteractionEntry = {
  userId: string;
  count: number;
};
type MessageOwnershipInteractionStore = Record<string, MessageOwnershipInteractionEntry>;
type SharedContextReason =
  | "team_activity"
  | "multiple_viewers"
  | "assigned"
  | "unknown";
type MailMessageSharedContext = {
  reason: SharedContextReason;
};
type MailMessageCollaborationVisibility = "internal" | "shared";
type MailMessageCollaborationParticipant = {
  id: string;
  name: string;
  email: string;
  kind: "internal" | "external";
  status: "active" | "invited" | "declined";
};
type MailMessageCollaborationMention = {
  id: string;
  name: string;
  email: string;
  handle: string;
  notify: boolean;
};
type MailMessageCollaborationMessage = {
  id: string;
  authorId: string;
  authorName: string;
  text: string;
  timestamp: number;
  visibility?: MailMessageCollaborationVisibility;
  mentions?: MailMessageCollaborationMention[];
};
type MailMessageCollaboration = {
  state: "needs_review" | "needs_action" | "note_only" | "resolved";
  requestedBy: string;
  requestedUserId: string;
  requestedUserName: string;
  createdAt: number;
  updatedAt: number;
  participants?: MailMessageCollaborationParticipant[];
  resolvedAt?: number;
  resolvedByUserId?: string;
  resolvedByUserName?: string;
  previewText?: string;
  messages: MailMessageCollaborationMessage[];
};
type CuevionInternalClassification =
  | "promo"
  | "promo_reminder"
  | "workflow_update"
  | "distributor_update"
  | "business_reminder"
  | "royalty_statement"
  | "finance"
  | "info"
  | "reply"
  | "business"
  | "demo"
  | "high_priority_demo"
  | "unknown";

type MailAttachment = {
  id: string;
  name: string;
  mimeType?: string;
  size?: number;
  file?: File;
};
type MailAttachmentInput = string | MailAttachment;
type ComposeRecipientField = "to" | "cc" | "bcc";

type MailMessage = {
  id: string;
  threadId?: string;
  sender: string;
  subject: string;
  snippet: string;
  time: string;
  createdAt?: string;
  imapUid?: string;
  unread?: boolean;
  flagged?: boolean;
  signal?: string;
  ui_signal?: string;
  from: string;
  to: string;
  cc?: string;
  timestamp: string;
  body: string[];
  bodyHtml?: string;
  signature?: InboxSignatureSettings;
  isAutoReply?: boolean;
  autoReplyType?: "out_of_office";
  attachments?: MailAttachment[];
  internalClassification?: CuevionInternalClassification;
  isShared?: boolean;
  sharedContext?: MailMessageSharedContext;
  collaboration?: MailMessageCollaboration;
  owner?: MailMessageOwner;
  priorityScore: MailMessagePriorityScore;
  focusSignal?: MailMessageFocusSignal;
  suggestionDismissed?: boolean;
  behaviorSuggestionDismissed?: boolean;
  category: CuevionMessageCategory;
  categorySource: CuevionCategorySource;
  categoryConfidence: CuevionCategoryConfidence;
  final_visibility?: string;
  action?: string;
  suggestion?: MailMessageSuggestion;
  behaviorSuggestion?: MailMessageBehaviorSuggestion;
};

type MailMessageSeed = Omit<
  MailMessage,
  | "category"
  | "categorySource"
  | "categoryConfidence"
  | "priorityScore"
  | "behaviorSuggestion"
  | "attachments"
> & {
  threadId?: string;
  attachments?: MailAttachmentInput[];
  internalClassification?: CuevionInternalClassification;
  category?: CuevionMessageCategory;
  categorySource?: CuevionCategorySource;
  categoryConfidence?: CuevionCategoryConfidence;
  final_visibility?: string;
  action?: string;
  suggestion?: MailMessageSuggestion;
  behaviorSuggestion?: MailMessageBehaviorSuggestion;
};
type CollaborationInviteTokenPayload = {
  version: 1;
  inviteeEmail: string;
  message: MailMessage;
};

type MailFilter = "All" | "Unread" | "Priority" | "Review";
type MailFolder = "Inbox" | "Drafts" | "Sent" | "Archive" | "Filtered" | "Spam" | "Trash";
type MailSortOrder = "desc" | "asc";
type MailboxCollections = Record<MailFolder, MailMessage[]>;
type MailboxStore = Record<string, MailboxCollections>;
type ForYouLearningSuggestion = {
  key: string;
  sender: string;
  senderAddress: string;
  subject: string;
  createdAt: string;
  uncertainty: number;
  senderFrequency: number;
  snippet: string[];
  reason: string;
  visualLabel?: string;
  mailboxId: InboxId | null;
};
type ForYouUncertainEmail = {
  key: string;
  sender: string;
  senderAddress: string;
  mailboxId: InboxId | null;
  subject: string;
  preview: string[];
  reason: string;
  currentMailboxLabel: string;
};
type NotificationNavigationRequest = {
  mailboxId: InboxId;
  messageId: string;
  type: "invite" | "reply" | "mention";
  source?: "priority";
  collaborationMessageId?: string;
  inviteeEmail?: string;
  focusReplyComposer?: boolean;
  openFullMessage?: boolean;
  requestKey: number;
};
type ReviewInboxHandoff = {
  reviewId: string;
  messageId: string;
  mailboxId: InboxId;
  threadId: string | null;
  initialFolder: MailFolder | null;
  initialCategory: CuevionMessageCategory | null;
  startedAt: string;
  source: "priority-list" | "review-detail";
};
const canonicalFolderOrder: MailFolder[] = [
  "Trash",
  "Spam",
  "Filtered",
  "Archive",
  "Sent",
  "Drafts",
  "Inbox",
];
const AI_SUGGESTIONS_STORAGE_KEY = "cuevion-ai-suggestions-enabled";
const INBOX_CHANGES_STORAGE_KEY = "cuevion-inbox-changes-enabled";
const TEAM_ACTIVITY_STORAGE_KEY = "cuevion-team-activity-enabled";
const WORKSPACE_THEME_MODE_STORAGE_KEY = "cuevion-workspace-theme-mode";
const CATEGORY_LEARNING_STORAGE_KEY = "cuevion-sender-category-learning";
const MESSAGE_OWNERSHIP_STORAGE_KEY = "cuevion-message-ownership";
const CUEVION_MESSAGE_UNREAD_OVERRIDES_STORAGE_KEY = "cuevion-message-unread-overrides";
const CUEVION_SENT_MESSAGES_STORAGE_KEY = "cuevion-sent-messages";
const CUEVION_TRASH_MESSAGES_STORAGE_KEY = "cuevion-trash-messages";
const CUEVION_SPAM_MESSAGES_STORAGE_KEY = "cuevion-spam-messages";
const CUEVION_ARCHIVE_MESSAGES_STORAGE_KEY = "cuevion-archive-messages";
const CUEVION_MANUAL_PRIORITY_OVERRIDES_STORAGE_KEY = "cuevion-manual-priority-overrides";
const MAIL_SIGNATURES_STORAGE_KEY = "cuevion-mail-signatures";
const MAIL_OUT_OF_OFFICE_STORAGE_KEY = "cuevion-mail-out-of-office";
const OUT_OF_OFFICE_REPLY_LOG_STORAGE_KEY = "cuevion-out-of-office-reply-log";
const MANAGED_INBOXES_STORAGE_KEY = "cuevion-managed-inboxes";
const MAILBOX_TITLE_OVERRIDES_STORAGE_KEY = "cuevion-mailbox-title-overrides";
const OUT_OF_OFFICE_SUPPRESSION_WINDOW_MS = 24 * 60 * 60 * 1000;
const SMART_FOLDERS_STORAGE_KEY = "cuevion-smart-folders";
const MAIL_LIST_PANE_WIDTH_STORAGE_KEY = "cuevion-mail-list-pane-width";
const COMPOSE_RECIPIENT_MEMORY_STORAGE_KEY = "cuevion-compose-recipient-memory";
const ACTIVE_MAILBOX_AUTO_REFRESH_INTERVAL_MS = 3 * 60 * 1000;
const MAIL_FOLDER_COLUMN_WIDTH = 180;
const MAIL_SPLIT_GAP = 24;
const MIN_MAIL_LIST_PANE_WIDTH = 320;
const MIN_MAIL_DETAIL_PANE_WIDTH = 400;
const MAIL_SPLIT_DIVIDER_WIDTH = 24;
const MAIL_LIST_PREVIEW_CHARACTER_CAP = 128;

function buildMessageUnreadOverridesStorageKey(
  workspaceUserId: string,
  orderedMailboxKey: string,
) {
  return `${CUEVION_MESSAGE_UNREAD_OVERRIDES_STORAGE_KEY}:${workspaceUserId}:${orderedMailboxKey}`;
}

function buildSentMessagesStorageKey(
  workspaceUserId: string,
  orderedMailboxKey: string,
) {
  return `${CUEVION_SENT_MESSAGES_STORAGE_KEY}:${workspaceUserId}:${orderedMailboxKey}`;
}

function buildTrashMessagesStorageKey(
  workspaceUserId: string,
  orderedMailboxKey: string,
) {
  return `${CUEVION_TRASH_MESSAGES_STORAGE_KEY}:${workspaceUserId}:${orderedMailboxKey}`;
}

function buildSpamMessagesStorageKey(
  workspaceUserId: string,
  orderedMailboxKey: string,
) {
  return `${CUEVION_SPAM_MESSAGES_STORAGE_KEY}:${workspaceUserId}:${orderedMailboxKey}`;
}

function buildArchiveMessagesStorageKey(
  workspaceUserId: string,
  orderedMailboxKey: string,
) {
  return `${CUEVION_ARCHIVE_MESSAGES_STORAGE_KEY}:${workspaceUserId}:${orderedMailboxKey}`;
}

function buildManualPriorityOverridesStorageKey(
  workspaceUserId: string,
  orderedMailboxKey: string,
) {
  return `${CUEVION_MANUAL_PRIORITY_OVERRIDES_STORAGE_KEY}:${workspaceUserId}:${orderedMailboxKey}`;
}

function createEmptySignatureSettings(): InboxSignatureSettings {
  return {
    html: "",
    useByDefault: true,
    logoImageUrl: null,
    layout: "text-only",
    showDivider: false,
  };
}

function normalizeSignatureLink(href: string) {
  const trimmedHref = href.trim();

  if (!trimmedHref) {
    return "";
  }

  if (/^https?:\/\//i.test(trimmedHref)) {
    return trimmedHref;
  }

  if (/^www\./i.test(trimmedHref)) {
    return `https://${trimmedHref}`;
  }

  return "";
}

function appendLinkifiedText(
  documentRef: Document,
  parent: Node,
  text: string,
) {
  const linkPattern = /((?:https?:\/\/|www\.)[^\s<]+)/gi;
  let lastIndex = 0;

  text.replace(linkPattern, (match, _group, offset: number) => {
    if (offset > lastIndex) {
      parent.appendChild(documentRef.createTextNode(text.slice(lastIndex, offset)));
    }

    const anchor = documentRef.createElement("a");
    anchor.href = normalizeSignatureLink(match);
    anchor.textContent = match;
    anchor.target = "_blank";
    anchor.rel = "noreferrer";
    parent.appendChild(anchor);
    lastIndex = offset + match.length;
    return match;
  });

  if (lastIndex < text.length) {
    parent.appendChild(documentRef.createTextNode(text.slice(lastIndex)));
  }
}

function sanitizeSignatureHtml(input: string) {
  if (typeof document === "undefined") {
    return input;
  }

  const documentRef = document.implementation.createHTMLDocument("");
  const sourceRoot = documentRef.createElement("div");
  const targetRoot = documentRef.createElement("div");
  sourceRoot.innerHTML = input;

  const appendSanitizedNode = (node: Node, parent: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      appendLinkifiedText(documentRef, parent, node.textContent ?? "");
      return;
    }

    if (node.nodeType !== Node.ELEMENT_NODE) {
      return;
    }

    const element = node as HTMLElement;
    const tagName = element.tagName.toLowerCase();

    if (tagName === "br") {
      parent.appendChild(documentRef.createElement("br"));
      return;
    }

    if (tagName === "a") {
      const href = normalizeSignatureLink(element.getAttribute("href") ?? element.textContent ?? "");

      if (!href) {
        appendLinkifiedText(documentRef, parent, element.textContent ?? "");
        return;
      }

      const anchor = documentRef.createElement("a");
      anchor.href = href;
      anchor.target = "_blank";
      anchor.rel = "noreferrer";
      parent.appendChild(anchor);
      Array.from(element.childNodes).forEach((child) => appendSanitizedNode(child, anchor));
      return;
    }

    const allowedTag =
      tagName === "b" || tagName === "strong"
        ? "strong"
        : tagName === "i" || tagName === "em"
          ? "em"
          : tagName === "div" || tagName === "p"
            ? "div"
            : null;

    if (!allowedTag) {
      Array.from(element.childNodes).forEach((child) => appendSanitizedNode(child, parent));
      return;
    }

    const nextElement = documentRef.createElement(allowedTag);
    parent.appendChild(nextElement);
    Array.from(element.childNodes).forEach((child) => appendSanitizedNode(child, nextElement));
  };

  Array.from(sourceRoot.childNodes).forEach((child) => appendSanitizedNode(child, targetRoot));

  return targetRoot.innerHTML.replace(/<div><\/div>/g, "<div><br></div>");
}

function signatureHtmlToPlainText(html: string) {
  if (!html || typeof document === "undefined") {
    return "";
  }

  const container = document.createElement("div");
  container.innerHTML = html
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/div>/gi, "\n")
    .replace(/<\/p>/gi, "\n")
    .replace(/<div>/gi, "")
    .replace(/<p>/gi, "");

  return (container.textContent ?? "").trimEnd();
}

function normalizeInboxSignatureSettings(
  settings?: Partial<InboxSignatureSettings> | null,
): InboxSignatureSettings {
  return {
    html: sanitizeSignatureHtml(settings?.html ?? ""),
    useByDefault: settings?.useByDefault ?? true,
    logoImageUrl: settings?.logoImageUrl ?? null,
    layout: settings?.layout ?? "text-only",
    showDivider: settings?.showDivider ?? false,
  };
}

function normalizeInboxOutOfOfficeSettings(
  settings?: Partial<InboxOutOfOfficeSettings> | null,
): InboxOutOfOfficeSettings {
  return {
    enabled: settings?.enabled ?? false,
    message: settings?.message ?? "",
  };
}

function normalizeOutOfOfficeReplyLogStore(
  store?: OutOfOfficeReplyLogStore | null,
): OutOfOfficeReplyLogStore {
  if (!store) {
    return {};
  }

  return Object.fromEntries(
    Object.entries(store).map(([inboxId, senderLog]) => [
      inboxId,
      Object.fromEntries(
        Object.entries(senderLog ?? {}).filter(([, timestamp]) =>
          typeof timestamp === "number" && Number.isFinite(timestamp),
        ),
      ),
    ]),
  ) as OutOfOfficeReplyLogStore;
}

function isNoReplyAddress(email: string) {
  const normalized = normalizeSenderLearningKey(email);
  return (
    normalized.includes("noreply") ||
    normalized.includes("no-reply") ||
    normalized.includes("donotreply")
  );
}

function buildOutOfOfficeReplySubject(subject: string) {
  const trimmedSubject = subject.trim();

  if (trimmedSubject.toLowerCase().startsWith("re:")) {
    return trimmedSubject;
  }

  return `Re: ${trimmedSubject || "Untitled message"}`;
}

function createEmptySmartFolderRule(): SmartFolderRule {
  return {
    id: `rule-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    field: "From",
    operator: "contains",
    value: "",
  };
}

function getSmartFolderRuleMatchValue(message: MailMessage, field: SmartFolderRuleField) {
  if (field === "From") {
    return message.from;
  }

  if (field === "Subject") {
    return message.subject;
  }

  const normalizedSender = message.from.trim().toLowerCase();
  const atIndex = normalizedSender.lastIndexOf("@");

  return atIndex === -1 ? normalizedSender : normalizedSender.slice(atIndex + 1);
}

function doesMessageMatchSmartFolderRule(message: MailMessage, rule: SmartFolderRule) {
  const matchValue = getSmartFolderRuleMatchValue(message, rule.field)
    .trim()
    .toLowerCase();
  const ruleValue = rule.value.trim().toLowerCase();

  if (!ruleValue) {
    return false;
  }

  return matchValue.includes(ruleValue);
}

function doesMessageMatchSmartFolder(message: MailMessage, folder: SmartFolderDefinition) {
  return folder.rules.some((rule) => doesMessageMatchSmartFolderRule(message, rule));
}

function hasSignatureContent(signature: InboxSignatureSettings) {
  return Boolean(signature.html.trim() || signature.logoImageUrl);
}

function escapeComposeHtml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function buildComposeLineHtml(line: string) {
  return line.length > 0 ? escapeComposeHtml(line) : "<br>";
}

function buildComposeParagraphsHtml(value: string) {
  const normalizedValue = value.replace(/\r\n/g, "\n");
  const lines = normalizedValue.split("\n");

  if (lines.length === 0) {
    return "";
  }

  return lines
    .map((line) => `<div>${buildComposeLineHtml(line)}</div>`)
    .join("");
}

function buildComposeQuoteHtml(mode: ComposeMode, sourceMessage: MailMessage) {
  const quotedMessage =
    mode === "reply" || mode === "reply_all"
      ? `On ${sourceMessage.timestamp}, ${sourceMessage.from} wrote:\n${sourceMessage.body
          .map((paragraph) => `> ${paragraph}`)
          .join("\n>\n")}`
      : `Forwarded message:\n\nFrom: ${sourceMessage.from}\nTo: ${sourceMessage.to}${
          sourceMessage.cc ? `\nCc: ${sourceMessage.cc}` : ""
        }\nTime: ${sourceMessage.timestamp}\nSubject: ${sourceMessage.subject}\n\n${sourceMessage.body.join("\n\n")}`;

  return `<div data-compose-quote="true">${buildComposeParagraphsHtml(quotedMessage)}</div>`;
}

function buildComposeSignatureMarkup(signature: InboxSignatureSettings | null) {
  if (!signature) {
    return "";
  }

  const normalizedSignature = normalizeInboxSignatureSettings(signature);

  if (!hasSignatureContent(normalizedSignature)) {
    return "";
  }

  const hasText = normalizedSignature.html.trim().length > 0;
  const showLogo =
    Boolean(normalizedSignature.logoImageUrl) &&
    normalizedSignature.layout !== "text-only";
  const shouldShowDivider = normalizedSignature.showDivider && showLogo;
  const logoMarkup =
    showLogo && normalizedSignature.logoImageUrl
      ? `<div data-compose-signature-logo="true"><img src="${escapeComposeHtml(
          normalizedSignature.logoImageUrl,
        )}" alt=""></div>`
      : "";
  const textMarkup = hasText
    ? `<div data-compose-signature-text="true">${normalizedSignature.html}</div>`
    : "";
  const dividerMarkup = shouldShowDivider
    ? `<div data-compose-signature-divider="true"></div>`
    : "";

  let contentMarkup = textMarkup;

  if (normalizedSignature.layout === "logo-left" && showLogo) {
    contentMarkup = `${dividerMarkup}<div data-compose-signature-row="true">${logoMarkup}<div data-compose-signature-right="true">${textMarkup}</div></div>`;
  } else if (normalizedSignature.layout === "logo-below" && showLogo) {
    contentMarkup = `${textMarkup}${dividerMarkup}${logoMarkup}`;
  }

  return `<div data-compose-signature="true">${contentMarkup}</div>`;
}

function buildComposeSignatureSpacerMarkup() {
  return `<div data-compose-signature-spacer="true"><br></div><div data-compose-signature-spacer="true"><br></div>`;
}

function withComposeSignatureMarkup(
  bodyHtml: string,
  signature: InboxSignatureSettings | null,
) {
  if (typeof document === "undefined") {
    return bodyHtml;
  }

  const container = document.createElement("div");
  container.innerHTML = bodyHtml;

  container.querySelectorAll("[data-compose-signature-spacer]").forEach((node) => {
    node.remove();
  });
  container.querySelectorAll("[data-compose-signature]").forEach((node) => {
    node.remove();
  });

  const signatureMarkup = buildComposeSignatureMarkup(signature);

  if (!signatureMarkup) {
    return container.innerHTML;
  }

  const fragment = document
    .createRange()
    .createContextualFragment(
      `${buildComposeSignatureSpacerMarkup()}${signatureMarkup}`,
    );
  const quoteNode = container.querySelector("[data-compose-quote]");

  if (quoteNode?.parentNode) {
    quoteNode.parentNode.insertBefore(fragment, quoteNode);
  } else {
    container.append(fragment);
  }

  return container.innerHTML;
}

function buildComposeBody({
  mode,
  sourceMessage,
  signature,
}: {
  mode: ComposeMode;
  sourceMessage: MailMessage | null;
  signature?: InboxSignatureSettings | null;
}) {
  const quoteHtml = sourceMessage
    ? `<div><br></div><div><br></div>${buildComposeQuoteHtml(mode, sourceMessage)}`
    : "";
  return withComposeSignatureMarkup(quoteHtml, signature ?? null);
}

function extractComposePlainText(html: string) {
  if (typeof document === "undefined") {
    return html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
  }

  const container = document.createElement("div");
  container.innerHTML = html;

  const textContent = container.innerText.replace(/\u00a0/g, " ");
  return textContent.replace(/\n{3,}/g, "\n\n").trim();
}

function extractComposeParagraphs(html: string) {
  const plainText = extractComposePlainText(html);

  if (!plainText) {
    return [];
  }

  return plainText
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);
}

function getQuotedParagraphStartIndex(paragraphs: string[]) {
  return paragraphs.findIndex(
    (paragraph) =>
      /^On .+ wrote:$/.test(paragraph) || paragraph === "Forwarded message:",
  );
}
const LEARNED_CATEGORY_MIN_COUNT = 2;
const HIGH_CONFIDENCE_LEARNING_COUNT = 3;
const AUTO_CATEGORY_BEHAVIOR_MIN_COUNT = 2;
const categoryConfidenceRank: Record<CuevionCategoryConfidence, number> = {
  low: 0,
  medium: 1,
  high: 2,
};
let workspaceModalLockCount = 0;
let workspaceModalLockScrollY = 0;
let workspaceModalBodyStyleSnapshot: {
  overflow: string;
  position: string;
  top: string;
  left: string;
  right: string;
  width: string;
} | null = null;
let workspaceModalHtmlOverflowSnapshot = "";

const weekdayIndexMap: Record<string, number> = {
  Sunday: 0,
  Monday: 1,
  Tuesday: 2,
  Wednesday: 3,
  Thursday: 4,
  Friday: 5,
  Saturday: 6,
};

const cuevionCategoryByInternalClassification: Record<
  CuevionInternalClassification,
  CuevionMessageCategory
> = {
  promo: "Promo",
  promo_reminder: "Promo",
  workflow_update: "Updates",
  distributor_update: "Updates",
  business_reminder: "Updates",
  royalty_statement: "Updates",
  finance: "Updates",
  info: "Updates",
  reply: "Primary",
  business: "Primary",
  demo: "Primary",
  high_priority_demo: "Primary",
  unknown: "Primary",
};

function normalizeSenderLearningKey(value: string) {
  const normalizedValue = value.trim().toLowerCase();
  const emailMatch = normalizedValue.match(/([a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,})/i);

  return emailMatch?.[1] ?? normalizedValue;
}

function normalizeSenderLearningDomain(value: string) {
  const normalizedValue = value.trim().toLowerCase();
  const domainValue = normalizedValue.includes("@")
    ? normalizedValue.split("@")[1] ?? ""
    : normalizedValue;

  return domainValue.trim();
}

function buildSenderLearningStoreKey(
  value: string,
  matchType: "sender" | "domain" = "sender",
) {
  if (matchType === "domain") {
    const domainKey = normalizeSenderLearningDomain(value);
    return domainKey ? `domain:${domainKey}` : "";
  }

  return normalizeSenderLearningKey(value);
}

function resolveSenderLearningEntry(
  senderAddress: string,
  senderCategoryLearning: SenderCategoryLearningStore,
) {
  const senderKey = buildSenderLearningStoreKey(senderAddress, "sender");
  const senderEntry = senderCategoryLearning[senderKey];

  if (senderEntry) {
    return {
      entry: senderEntry,
      key: senderKey,
      matchType: "sender" as const,
    };
  }

  const domainKey = buildSenderLearningStoreKey(senderAddress, "domain");
  const domainEntry = senderCategoryLearning[domainKey];

  if (domainEntry) {
    return {
      entry: domainEntry,
      key: domainKey,
      matchType: "domain" as const,
    };
  }

  return null;
}

function formatSharedContextHint(sharedContext?: MailMessageSharedContext) {
  switch (sharedContext?.reason) {
    case "team_activity":
      return "Active with your team";
    case "multiple_viewers":
      return "Viewed by multiple people";
    case "assigned":
      return "Being handled with your team";
    default:
      return "Shared with your team";
  }
}

function formatSharedContextDetail(sharedContext?: MailMessageSharedContext) {
  switch (sharedContext?.reason) {
    case "team_activity":
      return "Shared context: Active with your team";
    case "multiple_viewers":
      return "Shared context: Viewed by multiple people";
    case "assigned":
      return "Shared context: Assigned for follow-up";
    default:
      return "Shared context: Shared with your team";
  }
}

function formatCollaborationStatusTimestamp(timestamp: number) {
  return `${new Date(timestamp).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  })} at ${new Date(timestamp).toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
  })}`;
}

function getCollaborationMessageVisibility(
  entry: MailMessageCollaborationMessage,
): MailMessageCollaborationVisibility {
  return entry.visibility ?? "shared";
}

function canViewerSeeCollaborationMessage(
  entry: MailMessageCollaborationMessage,
  viewerType: "workspace" | "external",
) {
  return viewerType === "workspace" || getCollaborationMessageVisibility(entry) === "shared";
}

function buildCollaborationMentionHandle(name: string, email: string) {
  const normalizedName = name.toLowerCase().replace(/[^a-z0-9]+/g, "");

  if (normalizedName) {
    return normalizedName;
  }

  return email
    .toLowerCase()
    .split("@")[0]
    .replace(/[^a-z0-9]+/g, "");
}

function getMentionQueryAtCursor(value: string, cursorPosition: number | null) {
  if (cursorPosition === null) {
    return null;
  }

  const prefix = value.slice(0, cursorPosition);
  const match = prefix.match(/(^|[\s([{])@([a-z0-9._-]*)$/i);

  if (!match) {
    return null;
  }

  return {
    query: match[2] ?? "",
    start: cursorPosition - (match[2]?.length ?? 0) - 1,
    end: cursorPosition,
  };
}

function renderTextWithMentions(
  value: string,
  mentionMap: Map<string, MailMessageCollaborationMention>,
  themeMode: "light" | "dark",
) {
  const segments: ReactNode[] = [];
  const mentionPattern = /@([a-z0-9._-]+)/gi;
  let lastIndex = 0;
  let match: RegExpExecArray | null = mentionPattern.exec(value);

  while (match) {
    const matchedValue = match[0];
    const matchedHandle = match[1]?.toLowerCase() ?? "";
    const matchedMention = mentionMap.get(matchedHandle);
    const matchStart = match.index;

    if (matchStart > lastIndex) {
      segments.push(value.slice(lastIndex, matchStart));
    }

    if (matchedMention) {
      segments.push(
        <span
          key={`${matchedMention.id}-${matchStart}`}
          className="rounded-[8px] px-1 py-0.5"
          style={
            themeMode === "light"
              ? {
                  backgroundColor: "rgba(171, 198, 177, 0.34)",
                  color: "rgba(42, 74, 50, 0.98)",
                  WebkitTextFillColor: "rgba(42, 74, 50, 0.98)",
                }
              : {
                  backgroundColor: "rgba(112, 150, 118, 0.18)",
                  color: "rgba(220, 235, 223, 0.96)",
                  WebkitTextFillColor: "rgba(220, 235, 223, 0.96)",
                }
          }
        >
          {matchedValue}
        </span>,
      );
    } else {
      segments.push(matchedValue);
    }

    lastIndex = matchStart + matchedValue.length;
    match = mentionPattern.exec(value);
  }

  if (lastIndex < value.length) {
    segments.push(value.slice(lastIndex));
  }

  return segments;
}

function isValidInviteEmail(value: string) {
  return /^[^\s@]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$/.test(
    value.trim(),
  );
}

function encodeBase64Url(value: string) {
  const bytes = new TextEncoder().encode(value);
  let binary = "";

  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });

  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function decodeBase64Url(value: string) {
  const normalizedValue = value.replace(/-/g, "+").replace(/_/g, "/");
  const paddedValue =
    normalizedValue + "=".repeat((4 - (normalizedValue.length % 4 || 4)) % 4);
  const binary = atob(paddedValue);
  const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));

  return new TextDecoder().decode(bytes);
}

function buildCollaborationInviteToken(message: MailMessage, email: string) {
  const payload: CollaborationInviteTokenPayload = {
    version: 1,
    inviteeEmail: email.toLowerCase(),
    message,
  };

  return encodeBase64Url(JSON.stringify(payload));
}

function decodeCollaborationInviteToken(inviteToken: string) {
  try {
    const decodedValue = JSON.parse(
      decodeBase64Url(inviteToken),
    ) as CollaborationInviteTokenPayload;

    if (
      decodedValue.version !== 1 ||
      !decodedValue.message ||
      typeof decodedValue.inviteeEmail !== "string"
    ) {
      return null;
    }

    return decodedValue;
  } catch {
    return null;
  }
}

function buildCollaborationInviteLink(message: MailMessage, email: string) {
  if (typeof window === "undefined" || !message.collaboration) {
    return "";
  }

  const inviteUrl = new URL(window.location.pathname, window.location.origin);
  inviteUrl.searchParams.set(
    "collab_invite",
    buildCollaborationInviteToken(message, email),
  );
  inviteUrl.searchParams.set("message_id", message.id);
  inviteUrl.searchParams.set("invitee", email.toLowerCase());

  return inviteUrl.toString();
}

function isLocalDevelopmentEnvironment() {
  if (typeof window === "undefined") {
    return false;
  }

  return (
    window.location.hostname === "localhost" ||
    window.location.hostname === "127.0.0.1"
  );
}

function getCollaborationParticipants(collaboration: MailMessageCollaboration) {
  const participantsByKey = new Map<string, MailMessageCollaborationParticipant>();
  const primaryParticipant: MailMessageCollaborationParticipant = {
    id: collaboration.requestedUserId,
    name: collaboration.requestedUserName,
    email: "",
    kind: "internal",
    status: "active",
  };

  const sourceParticipants =
    collaboration.participants !== undefined
      ? collaboration.participants
      : [primaryParticipant];

  sourceParticipants.forEach((participant) => {
    if (participant.status === "declined") {
      return;
    }

    const key = participant.id || participant.email.toLowerCase();
    participantsByKey.set(key, participant);
  });

  return Array.from(participantsByKey.values());
}

function getCollaborationMentionTargets(
  participants: MailMessageCollaborationParticipant[],
  teamPeople: Array<{ id: string; name: string; email: string }>,
) {
  const targets = new Map<
    string,
    MailMessageCollaborationParticipant & { handle: string }
  >();

  teamPeople.forEach((person) => {
    const handle = buildCollaborationMentionHandle(person.name, person.email);
    targets.set(handle, {
      id: person.id,
      name: person.name,
      email: person.email,
      kind: "internal",
      status: "active",
      handle,
    });
  });

  participants.forEach((participant) => {
    const handle = buildCollaborationMentionHandle(participant.name, participant.email);
    targets.set(handle, {
      ...participant,
      handle,
    });
  });

  return Array.from(targets.values()).filter((target) => target.status !== "declined");
}

function extractCollaborationMentions(
  value: string,
  candidates: ReturnType<typeof getCollaborationMentionTargets>,
  authorId: string,
) {
  const handleMap = new Map(
    candidates.map((candidate) => [candidate.handle.toLowerCase(), candidate]),
  );
  const seenHandles = new Set<string>();
  const mentions: MailMessageCollaborationMention[] = [];
  const mentionPattern = /(^|[\s([{])@([a-z0-9._-]+)/gi;
  let match: RegExpExecArray | null = mentionPattern.exec(value);

  while (match) {
    const handle = match[2]?.toLowerCase() ?? "";
    const candidate = handleMap.get(handle);

    if (candidate && !seenHandles.has(handle)) {
      mentions.push({
        id: candidate.id,
        name: candidate.name,
        email: candidate.email,
        handle: candidate.handle,
        notify: candidate.status !== "declined" && candidate.id !== authorId,
      });
      seenHandles.add(handle);
    }

    match = mentionPattern.exec(value);
  }

  return mentions;
}

function getCollaborationReasonLabel(collaboration: MailMessageCollaboration) {
  switch (collaboration.state) {
    case "needs_action":
      return "Needs action";
    case "note_only":
      return "Note only";
    default:
      return "Needs input";
  }
}

function isMessageInSharedView(message: MailMessage) {
  if (message.collaboration?.state && message.collaboration.state !== "resolved") {
    return true;
  }

  return Boolean(message.isShared && !message.sharedContext);
}

function shouldShowMessageSummary(snippet: string, body: string[]) {
  const normalizedSnippet = snippet.trim().toLowerCase();

  if (!normalizedSnippet) {
    return false;
  }

  const firstBodyParagraph = body[0]?.trim().toLowerCase() ?? "";

  return normalizedSnippet !== firstBodyParagraph;
}

function getAIDecisionCopy(message: MailMessage) {
  const subjectText = message.subject.toLowerCase();
  const snippetText = message.snippet.toLowerCase();
  const bodyText = message.body.join(" ").toLowerCase();
  const searchableText = `${subjectText} ${snippetText} ${bodyText}`.replace(/\s+/g, " ");
  const compactText = searchableText.trim();
  const bodyLength = bodyText.trim().length;
  const questionCount = (searchableText.match(/\?/g) ?? []).length;
  const attachmentCount = message.attachments?.length ?? 0;

  const countMatches = (patterns: string[]) =>
    patterns.reduce(
      (score, pattern) => score + (searchableText.includes(pattern) ? 1 : 0),
      0,
    );

  const replySignals = [
    "can you",
    "could you",
    "please confirm",
    "let me know",
    "please send",
    "are you able",
    "do you approve",
    "can we",
    "waiting on",
    "before friday",
    "deadline",
    "cutoff",
    "as soon as possible",
    "today",
    "confirm",
    "reply",
    "please advise",
    "get back to",
    "need your response",
    "approve",
    "able to",
    "when can",
    "please share",
    "schedule",
    "timing",
  ];
  const reviewSignals = [
    "review",
    "attached",
    "agreement",
    "contract",
    "draft",
    "version",
    "changes",
    "revised",
    "legal",
    "terms",
    "comments",
    "please check",
    "see attached",
    "markup",
    "asset package",
    "materials",
    "one-sheet",
    "split sheet",
    "package",
    "document",
  ];
  const informationalSignals = [
    "update",
    "snapshot",
    "fyi",
    "for your information",
    "report",
    "performance",
    "summary",
    "recap",
    "status",
    "confirmed",
    "confirmation",
    "completed",
    "queued",
    "accepted",
    "delivered",
    "went live",
    "now live",
    "tracking",
    "results",
  ];

  let replyScore = countMatches(replySignals);
  let reviewScore = countMatches(reviewSignals);
  let informationalScore = countMatches(informationalSignals);

  if (questionCount > 0) {
    replyScore += Math.min(questionCount, 2);
  }

  if (
    attachmentCount > 0 &&
    (reviewScore > 0 || /attached|attachment|please check|review|revised|draft/.test(searchableText))
  ) {
    reviewScore += 1;
  }

  if (
    informationalScore > 0 &&
    !/can you|could you|please confirm|please send|let me know|\?/.test(searchableText)
  ) {
    informationalScore += 1;
  }

  if (compactText.length < 36 || bodyLength < 18) {
    return {
      primary: "Cuevion isn't fully sure",
      secondary: "Not enough context to decide yet",
    };
  }

  const topScore = Math.max(replyScore, reviewScore, informationalScore);
  const sortedScores = [replyScore, reviewScore, informationalScore].sort((a, b) => b - a);
  const secondScore = sortedScores[1] ?? 0;
  const hasMixedSignals =
    topScore > 0 &&
    secondScore > 0 &&
    topScore - secondScore <= 1 &&
    !(replyScore >= 3 && replyScore > reviewScore);

  if (hasMixedSignals || topScore === 0) {
    return {
      primary: "Not completely clear what this needs",
      secondary: "This seems a bit mixed - a quick check may help",
    };
  }

  if (replyScore >= reviewScore && replyScore >= informationalScore && replyScore >= 2) {
    if (/confirm|timing|schedule|deadline|cutoff|today|before friday/.test(searchableText)) {
      return {
        primary: "You might want to reply to confirm timing",
        secondary: "It looks like they are waiting for confirmation",
      };
    }

    return {
      primary: "A quick reply may help move this forward",
      secondary: "This message seems to expect a response",
    };
  }

  if (reviewScore >= informationalScore && reviewScore >= 2) {
    if (/agreement|contract|terms|legal|draft|revised|version/.test(searchableText)) {
      return {
        primary: "This looks like something to inspect before taking action",
        secondary: "There are details here that likely need your input",
      };
    }

    return {
      primary: "You may want to take a quick look at this first",
      secondary: "This appears to include material that needs checking",
    };
  }

  if (informationalScore >= 2) {
    if (/update|snapshot|report|summary|recap|performance/.test(searchableText)) {
      return {
        primary: "This appears to be an update",
        secondary: "This reads more like a status update than a request",
      };
    }

    return {
      primary: "No immediate action seems necessary",
      secondary: "There is no clear request or next step here",
    };
  }

  return {
    primary: "This could use a quick look before deciding",
    secondary: "It is not obvious what the next step is yet",
  };
}

function resolvePasteRuleInputType(value: string) {
  const trimmedValue = value.trim().toLowerCase();

  if (!trimmedValue) {
    return null;
  }

  const emailPattern = /^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$/i;
  const domainPattern = /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$/i;

  if (emailPattern.test(trimmedValue)) {
    return "sender" as const;
  }

  if (domainPattern.test(trimmedValue)) {
    return "domain" as const;
  }

  return "invalid" as const;
}

function formatLearningRuleLabel(learningKey: string) {
  if (learningKey.startsWith("domain:")) {
    return `Domain: ${learningKey.replace("domain:", "")}`;
  }

  return learningKey;
}

function formatLearningRuleAction(entry: SenderCategoryLearningEntry) {
  if (entry.learnedCategory === "Promo") {
    return "future emails to Promo";
  }

  if (entry.learnedCategory === "Updates") {
    return entry.mailboxAction === "keep"
      ? "future emails to Updates"
      : "moved out of Inbox";
  }

  return entry.mailboxAction === "move" ? "future emails to Primary" : "kept in Inbox";
}

function getForYouCategoryLabel(
  category: "Important" | "Review" | "Promo" | "Demo" | "Spam",
) {
  return category === "Review" ? "Hold" : category;
}

function getTeamAccessLevelLabel(level: TeamAccessLevel) {
  return level === "Review" ? "Shared" : level;
}

function formatLearningRuleTimestamp(value?: string) {
  if (!value) {
    return "Recently";
  }

  const timestamp = new Date(value).getTime();

  if (Number.isNaN(timestamp)) {
    return "Recently";
  }

  const diffMs = Date.now() - timestamp;
  const diffMinutes = Math.max(0, Math.round(diffMs / (60 * 1000)));

  if (diffMinutes < 60) {
    return diffMinutes <= 1 ? "NOW" : `${diffMinutes} MIN AGO`;
  }

  const diffHours = Math.round(diffMinutes / 60);

  if (diffHours < 24) {
    return `${diffHours} HOUR${diffHours === 1 ? "" : "S"} AGO`;
  }

  return new Date(value).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  }).toUpperCase();
}

function resolveForYouCategoryFromLearningEntry(
  entry: SenderCategoryLearningEntry,
): "Important" | "Review" | "Promo" | "Demo" | "Spam" {
  if (entry.learnedCategory === "Promo") {
    return "Promo";
  }

  if (entry.learnedCategory === "Updates") {
    return entry.mailboxAction === "move" ? "Review" : "Review";
  }

  return "Important";
}

function resolveCuevionCategoryFromForYouSelection(
  selection: "Important" | "Review" | "Promo" | "Demo" | "Spam" | null,
): CuevionMessageCategory {
  if (selection === "Promo") {
    return "Promo";
  }

  if (selection === "Review" || selection === "Spam") {
    return "Updates";
  }

  return "Primary";
}

function resolveMailboxActionFromForYouSelection(
  selection: "Important" | "Normal" | "Show Less" | "Spam" | "Review" | "Promo" | "Demo" | null,
  category: CuevionMessageCategory,
  explicitMailboxAction?: "keep" | "move" | null,
) {
  if (explicitMailboxAction) {
    return explicitMailboxAction;
  }

  if (selection === "Spam" || selection === "Show Less" || selection === "Review") {
    return "move" as const;
  }

  if (category === "Promo" || category === "Updates") {
    return "move" as const;
  }

  return "keep" as const;
}

function resolveCuevionCategoryFromMailboxId(mailboxId: InboxId | null): CuevionMessageCategory {
  if (mailboxId === "promo") {
    return "Promo";
  }

  if (mailboxId === "demo" || mailboxId === "business") {
    return "Updates";
  }

  return "Primary";
}

function resolveMailboxActionFromMailboxId(mailboxId: InboxId | null): "keep" | "move" {
  return mailboxId === "main" ? "keep" : "move";
}

function inferLearningDecisionSourceContext(
  entry: SenderCategoryLearningEntry,
  ruleType: "sender" | "domain",
): LearningDecisionSourceContext | null {
  if (entry.sourceContext) {
    return entry.sourceContext;
  }

  if (entry.sourceCurrentMailboxId !== undefined) {
    return "uncertain";
  }

  if (ruleType === "domain") {
    return "paste_sender_or_domain";
  }

  return "refine";
}

function inferLearningDecisionMailboxId(
  entry: SenderCategoryLearningEntry,
): InboxId | null {
  if (entry.sourceMailboxId !== undefined) {
    return entry.sourceMailboxId;
  }

  if (entry.learnedCategory === "Promo") {
    return "promo";
  }

  if (entry.mailboxAction === "keep") {
    return "main";
  }

  return null;
}

function inferLearningDecisionPrioritySelection(
  entry: SenderCategoryLearningEntry,
): LearningDecisionPrioritySelection | null {
  if (entry.sourcePrioritySelection) {
    return entry.sourcePrioritySelection;
  }

  if (entry.learnedCategory === "Promo") {
    return "Normal";
  }

  if (entry.learnedCategory === "Updates" && entry.mailboxAction === "move") {
    return "Show Less";
  }

  if (entry.learnedCategory === "Primary" && entry.mailboxAction === "keep") {
    return "Important";
  }

  return null;
}

function formatForYouReason(message: MailMessage, mailboxLabel: string) {
  if (message.category === "Promo") {
    return `Cuevion placed this in ${mailboxLabel}, but is not confident yet.`;
  }

  if (message.category === "Updates") {
    return `Cuevion thinks this belongs in ${mailboxLabel}, but still needs confirmation.`;
  }

  return `Cuevion placed this in ${mailboxLabel}, but still needs confirmation.`;
}

function resolveOrderedMailboxTitle(
  orderedMailboxes: OrderedMailbox[],
  mailboxId: InboxId | null,
) {
  if (!mailboxId) {
    return orderedMailboxes[0]?.title ?? inboxDisplayConfig.main.title;
  }

  return (
    orderedMailboxes.find((mailbox) => mailbox.id === mailboxId)?.title ??
    (isPresetInboxId(mailboxId)
      ? inboxDisplayConfig[mailboxId].title
      : "Custom Inbox")
  );
}

function getMailboxFolderBadgeCount(
  mailboxCollections: MailboxStore[InboxId] | undefined,
  folder: MailFolder,
) {
  if (!mailboxCollections) {
    return 0;
  }

  if (folder === "Inbox") {
    return mailboxCollections.Inbox.filter((message) => message.unread).length;
  }

  return mailboxCollections[folder].length;
}

function resolveMailboxTitleForCategory(
  category: CuevionMessageCategory,
  orderedMailboxes: OrderedMailbox[],
  fallbackMailboxId: InboxId | null,
) {
  if (category === "Primary") {
    return resolveOrderedMailboxTitle(orderedMailboxes, "main");
  }

  if (category === "Promo") {
    return resolveOrderedMailboxTitle(orderedMailboxes, "promo");
  }

  return resolveOrderedMailboxTitle(orderedMailboxes, fallbackMailboxId);
}

function buildForYouLearningPools(
  mailboxStore: MailboxStore,
  orderedMailboxes: OrderedMailbox[],
): {
  learningSuggestionPool: ForYouLearningSuggestion[];
  uncertainEmailPool: ForYouUncertainEmail[];
} {
  const inboxMessages = Object.entries(mailboxStore).flatMap(([mailboxId, collections]) =>
    collections.Inbox.map((message) => ({
      mailboxId: mailboxId as InboxId,
      message,
    })),
  );
  const senderFrequencyByKey = inboxMessages.reduce<Record<string, number>>(
    (frequencyMap, entry) => {
      const senderKey = normalizeSenderLearningKey(entry.message.from);
      return {
        ...frequencyMap,
        [senderKey]: (frequencyMap[senderKey] ?? 0) + 1,
      };
    },
    {},
  );
  const realUncertainMessages = inboxMessages
    .filter(({ message }) => isRefineCuevionEligible(message) || isReviewUncertainEligible(message))
    .sort((firstEntry, secondEntry) => {
      const firstLow = firstEntry.message.categoryConfidence === "low" ? 1 : 0;
      const secondLow = secondEntry.message.categoryConfidence === "low" ? 1 : 0;

      if (secondLow !== firstLow) {
        return secondLow - firstLow;
      }

      return (
        resolveMailDateMs(secondEntry.message) - resolveMailDateMs(firstEntry.message)
      );
    });
  const learningSuggestionPool = realUncertainMessages
    .filter(({ message }) => isRefineCuevionEligible(message))
    .map(({ mailboxId, message }): ForYouLearningSuggestion => {
      const senderFrequency =
        senderFrequencyByKey[normalizeSenderLearningKey(message.from)] ?? 1;
      const mailboxLabel = resolveMailboxTitleForCategory(
        message.category,
        orderedMailboxes,
        mailboxId,
      );

      return {
        key: message.id,
        sender: message.sender,
        senderAddress: message.from,
        subject: message.subject,
        createdAt: message.createdAt ?? new Date(resolveMailDateMs(message)).toISOString(),
        uncertainty: 94,
        senderFrequency,
        snippet: message.body.slice(0, 2).length > 0 ? message.body.slice(0, 2) : [message.snippet],
        reason: formatForYouReason(message, mailboxLabel),
        mailboxId,
      };
    });
  const uncertainEmailPool = realUncertainMessages
    .filter(({ message }) => isReviewUncertainEligible(message))
    .slice(0, 5)
    .map(({ mailboxId, message }): ForYouUncertainEmail => {
      const mailboxLabel = resolveMailboxTitleForCategory(
        message.category,
        orderedMailboxes,
        mailboxId,
      );

      return {
        key: message.id,
        sender: message.sender,
        senderAddress: message.from,
        mailboxId,
        subject: message.subject,
        preview: message.body.slice(0, 2).length > 0 ? message.body.slice(0, 2) : [message.snippet],
        reason: formatForYouReason(message, mailboxLabel),
        currentMailboxLabel: mailboxLabel,
      };
    });

  return {
    learningSuggestionPool,
    uncertainEmailPool,
  };
}

function resolveImplicitOwner(
  messageId: string,
  messageOwnershipInteractions: MessageOwnershipInteractionStore,
): MailMessageOwner | undefined {
  const ownershipEntry = messageOwnershipInteractions[messageId];

  if (!ownershipEntry || ownershipEntry.count <= 0) {
    return undefined;
  }

  return {
    userId: ownershipEntry.userId,
    confidence:
      ownershipEntry.count >= 3
        ? "high"
        : ownershipEntry.count >= 2
          ? "medium"
          : "low",
    source: "implicit",
  };
}

function resolveMessagePriorityScore(
  category: CuevionMessageCategory,
  owner?: MailMessageOwner,
  sharedContext?: MailMessageSharedContext,
): MailMessagePriorityScore {
  if (
    category === "Primary" &&
    (sharedContext ||
      owner?.confidence === "medium" ||
      owner?.confidence === "high")
  ) {
    return "high";
  }

  if (category === "Primary") {
    return "medium";
  }

  return "low";
}

function resolveMessageFocusSignal(
  priorityScore: MailMessagePriorityScore,
  owner: MailMessageOwner | undefined,
  currentUserId: string,
): MailMessageFocusSignal {
  if (
    priorityScore === "high" &&
    owner?.userId === currentUserId &&
    (owner.confidence === "medium" || owner.confidence === "high")
  ) {
    return "attention";
  }

  return null;
}

function includesAnyKeyword(value: string, keywords: string[]) {
  return keywords.some((keyword) => value.includes(keyword));
}

function inferHeuristicSignal(
  message: Pick<
    MailMessageSeed,
    "signal" | "sender" | "subject" | "snippet" | "from" | "body" | "isAutoReply"
  >,
) {
  if (message.signal?.trim()) {
    return message.signal;
  }

  const normalizedSender = normalizeSenderLearningKey(message.from || message.sender);
  const searchableText = [
    message.subject,
    message.snippet,
    ...(message.body ?? []),
  ]
    .join(" ")
    .toLowerCase();
  const automatedSenderHints = [
    "no-reply",
    "noreply",
    "notifications@",
    "notification@",
    "newsletter@",
    "updates@",
    "mailer-daemon",
    "donotreply",
    "do-not-reply",
  ];
  const priorityKeywords = [
    "urgent",
    "asap",
    "action required",
    "approval",
    "approve",
    "contract",
    "invoice",
    "payment",
    "deadline",
    "due today",
    "due tomorrow",
    "please review",
    "signature",
    "confirm",
    "wire",
  ];
  const updateKeywords = [
    "update",
    "updated",
    "status",
    "fyi",
    "recap",
    "summary",
    "confirmed",
    "scheduled",
    "completed",
    "resolved",
    "sent",
    "delivered",
    "receipt",
  ];
  const promoKeywords = [
    "unsubscribe",
    "newsletter",
    "promotion",
    "promo",
    "offer",
    "discount",
    "sale",
    "campaign",
    "webinar",
    "announcement",
    "register now",
    "limited time",
  ];
  const isAutomatedSender = includesAnyKeyword(normalizedSender, automatedSenderHints);
  const isPromo = includesAnyKeyword(searchableText, promoKeywords);
  const isPriority =
    includesAnyKeyword(searchableText, priorityKeywords) && !isPromo;
  const isUpdate =
    message.isAutoReply ||
    includesAnyKeyword(searchableText, updateKeywords) ||
    (isAutomatedSender && !isPromo);

  if (isPriority) {
    return "Priority";
  }

  if (isPromo) {
    return "Promo";
  }

  if (isUpdate) {
    return "Update";
  }

  return "Other";
}

function inferInternalClassification(
  message: Pick<MailMessageSeed, "signal" | "internalClassification">,
  mailboxId: InboxId,
): CuevionInternalClassification {
  if (message.internalClassification) {
    return message.internalClassification;
  }

  switch (message.signal) {
    case "Update":
      return "workflow_update";
    case "Finance":
      return "finance";
    case "For review":
      return "high_priority_demo";
    case "Shortlist":
      return "demo";
    case "Timing":
      return mailboxId === "promo" ? "promo_reminder" : "workflow_update";
    case "Follow-up":
      return mailboxId === "promo" ? "promo" : "reply";
    case "Priority":
    case "Active":
      return mailboxId === "promo" ? "promo" : "business";
    case "Promo":
      return "promo";
    default:
      return "unknown";
  }
}

function resolveVisiblePrioritySignal(
  message: Pick<MailMessage, "signal">,
  override?: ManualPriorityOverride,
) {
  if (override === "priority") {
    return "Priority";
  }

  if (override === "removed" && message.signal === "Priority") {
    return null;
  }

  if (message.signal === "For review") {
    return null;
  }

  return message.signal ?? null;
}

function isMessageVisiblePriority(
  message: Pick<MailMessage, "signal" | "priorityScore">,
  override?: ManualPriorityOverride,
) {
  if (override === "priority") {
    return true;
  }

  if (override === "removed") {
    return false;
  }

  return message.signal === "Priority" || message.priorityScore === "high";
}

function isLiveInboxPriorityMessage(
  message: Pick<MailMessage, "signal" | "priorityScore">,
  override?: ManualPriorityOverride,
) {
  return isMessageVisiblePriority(message, override);
}

function normalizeThreadSubject(subject: string) {
  return subject
    .trim()
    .toLowerCase()
    .replace(/^(re|fwd|fw):\s*/gi, "")
    .replace(/\s+/g, " ");
}

function resolveMailThreadId(message: Pick<MailMessageSeed, "threadId" | "subject">) {
  return message.threadId?.trim() || normalizeThreadSubject(message.subject);
}

function maxCategoryConfidence(
  first: CuevionCategoryConfidence,
  second: CuevionCategoryConfidence,
): CuevionCategoryConfidence {
  return categoryConfidenceRank[first] >= categoryConfidenceRank[second]
    ? first
    : second;
}

function lowerCategoryConfidence(
  confidence: CuevionCategoryConfidence,
): CuevionCategoryConfidence {
  if (confidence === "high") {
    return "medium";
  }

  return "low";
}

type ThreadCategorizationCandidate = Pick<
  MailMessageSeed,
  "id" | "threadId" | "subject" | "createdAt" | "timestamp"
> &
  Partial<
    Pick<MailMessage, "category" | "categorySource" | "categoryConfidence">
  >;

function getRecentThreadMessages<T extends ThreadCategorizationCandidate>(
  message: Pick<MailMessageSeed, "id" | "threadId" | "subject" | "createdAt" | "timestamp">,
  candidates: T[],
) {
  const threadId = resolveMailThreadId(message);
  const messageDateMs = resolveMailDateMs({
    id: message.id,
    threadId,
    sender: "",
    subject: message.subject,
    snippet: "",
    time: "",
    createdAt: message.createdAt,
    from: "",
    to: "",
    timestamp: message.timestamp,
    body: [],
    priorityScore: "medium",
    category: "Primary",
    categorySource: "system",
    categoryConfidence: "medium",
  });
  const recentWindowMs = 30 * 24 * 60 * 60 * 1000;

  return candidates.filter((candidate) => {
    if (candidate.id === message.id) {
      return false;
    }

    if (resolveMailThreadId(candidate) !== threadId) {
      return false;
    }

    const candidateDateMs = resolveMailDateMs({
      id: candidate.id,
      threadId: resolveMailThreadId(candidate),
      sender: "",
      subject: candidate.subject,
      snippet: "",
      time: "",
      createdAt: candidate.createdAt,
      from: "",
      to: "",
      timestamp: candidate.timestamp,
      body: [],
      priorityScore: "medium",
      category: "Primary",
      categorySource: "system",
      categoryConfidence: "medium",
    });

    return (
      messageDateMs === 0 ||
      candidateDateMs === 0 ||
      Math.abs(messageDateMs - candidateDateMs) <= recentWindowMs
    );
  });
}

function resolveThreadDominantCategorization(
  message: Pick<MailMessageSeed, "id" | "threadId" | "subject" | "createdAt" | "timestamp">,
  mailboxStore: MailboxStore,
) {
  const threadHistory = Object.values(mailboxStore).flatMap((collections) =>
    canonicalFolderOrder.flatMap((folder) => collections[folder]),
  );
  const recentThreadMessages = getRecentThreadMessages(message, threadHistory).filter(
    (
      candidate,
    ): candidate is MailMessage &
      Required<
        Pick<MailMessage, "category" | "categorySource" | "categoryConfidence">
      > => Boolean(candidate.category && candidate.categorySource && candidate.categoryConfidence),
  );

  if (recentThreadMessages.length === 0) {
    return null;
  }

  const categoryScores = recentThreadMessages.reduce<
    Record<CuevionMessageCategory, number>
  >(
    (scores, candidate) => {
      const weight =
        candidate.categorySource === "user"
          ? 4
          : candidate.categorySource === "learned"
            ? 3
            : candidate.categoryConfidence === "high"
              ? 2
              : candidate.categoryConfidence === "medium"
                ? 1.25
                : 0.5;

      return {
        ...scores,
        [candidate.category]: (scores[candidate.category] ?? 0) + weight,
      };
    },
    {
      Primary: 0,
      Promo: 0,
      Updates: 0,
    },
  );
  const rankedCategories = Object.entries(categoryScores)
    .sort((firstEntry, secondEntry) => secondEntry[1] - firstEntry[1]) as Array<
    [CuevionMessageCategory, number]
  >;
  const [topCategory, topScore] = rankedCategories[0] ?? [];
  const secondScore = rankedCategories[1]?.[1] ?? 0;

  if (!topCategory || topScore < 2 || topScore - secondScore < 1.25) {
    return null;
  }

  return {
    category: topCategory,
    confidence: topScore >= 4 ? ("high" as const) : ("medium" as const),
  };
}

function resolveCuevionCategorization(
  message: MailMessageSeed,
  mailboxId: InboxId,
  senderCategoryLearning: SenderCategoryLearningStore,
  mailboxStore?: MailboxStore,
): Pick<MailMessage, "category" | "categorySource" | "categoryConfidence"> {
  if (
    message.category &&
    message.categorySource === "user" &&
    message.categoryConfidence
  ) {
    return {
      category: message.category,
      categorySource: message.categorySource,
      categoryConfidence: message.categoryConfidence,
    };
  }

  const senderLearningMatch = resolveSenderLearningEntry(
    message.from,
    senderCategoryLearning,
  );
  const senderLearning = senderLearningMatch?.entry;

  if (senderLearning && senderLearning.learnedFromCount >= LEARNED_CATEGORY_MIN_COUNT) {
    const learnedCategorization: Pick<
      MailMessage,
      "category" | "categorySource" | "categoryConfidence"
    > = {
      category: senderLearning.learnedCategory,
      categorySource: "learned",
      categoryConfidence:
        senderLearning.learnedFromCount >= HIGH_CONFIDENCE_LEARNING_COUNT
          ? "high"
          : "medium",
    };

    if (!mailboxStore) {
      return learnedCategorization;
    }

    const threadDominantCategorization = resolveThreadDominantCategorization(
      message,
      mailboxStore,
    );

    if (
      threadDominantCategorization &&
      threadDominantCategorization.category === learnedCategorization.category
    ) {
      return {
        ...learnedCategorization,
        categoryConfidence: maxCategoryConfidence(
          learnedCategorization.categoryConfidence,
          threadDominantCategorization.confidence,
        ),
      };
    }

    return learnedCategorization;
  }

  const internalClassification = inferInternalClassification(message, mailboxId);
  const systemCategorization: Pick<
    MailMessage,
    "category" | "categorySource" | "categoryConfidence"
  > = {
    category: cuevionCategoryByInternalClassification[internalClassification],
    categorySource: "system",
    categoryConfidence:
      internalClassification === "unknown"
        ? "low"
        : message.internalClassification
          ? "high"
          : "medium",
  };

  if (!mailboxStore) {
    return systemCategorization;
  }

  const threadDominantCategorization = resolveThreadDominantCategorization(
    message,
    mailboxStore,
  );

  if (!threadDominantCategorization) {
    return systemCategorization;
  }

  if (threadDominantCategorization.category === systemCategorization.category) {
    return {
      ...systemCategorization,
      categoryConfidence: maxCategoryConfidence(
        systemCategorization.categoryConfidence,
        threadDominantCategorization.confidence,
      ),
    };
  }

  if (systemCategorization.categoryConfidence === "low") {
    return {
      category: threadDominantCategorization.category,
      categorySource: "system",
      categoryConfidence: threadDominantCategorization.confidence,
    };
  }

  return {
    ...systemCategorization,
    categoryConfidence: lowerCategoryConfidence(systemCategorization.categoryConfidence),
  };
}

function isReviewUncertainEligible(message: MailMessage) {
  return (
    message.categorySource === "system" &&
    message.suggestion?.type === "confirm_category"
  );
}

function isRefineCuevionEligible(message: MailMessage) {
  if (message.categorySource !== "system") {
    return false;
  }

  if (message.categoryConfidence === "low") {
    return true;
  }

  return (
    message.categoryConfidence === "medium" &&
    (message.priorityScore === "high" || message.unread || message.isShared)
  );
}

function resolveMailMessageSuggestion(
  message: MailMessageSeed,
  categorization: Pick<MailMessage, "category" | "categorySource" | "categoryConfidence">,
): MailMessageSuggestion | undefined {
  if (message.suggestionDismissed) {
    return undefined;
  }

  if (message.suggestion) {
    return message.suggestion;
  }

  if (
    categorization.categorySource !== "system" ||
    categorization.categoryConfidence !== "low"
  ) {
    return undefined;
  }

  if (
    message.signal === "Draft" ||
    message.signal === "Sent" ||
    message.signal === "Archived" ||
    message.signal === "Spam" ||
    message.signal === "Trash"
  ) {
    return undefined;
  }

  return {
    type: "confirm_category",
    proposedCategory: categorization.category,
  };
}

function resolveMailMessageBehaviorSuggestion(
  message: MailMessageSeed,
  categorization: Pick<MailMessage, "category" | "categorySource" | "categoryConfidence">,
  senderCategoryLearning: SenderCategoryLearningStore,
): MailMessageBehaviorSuggestion | undefined {
  if (message.behaviorSuggestionDismissed) {
    return undefined;
  }

  const senderLearningMatch = resolveSenderLearningEntry(
    message.from,
    senderCategoryLearning,
  );
  const senderLearning = senderLearningMatch?.entry;

  if (
    !senderLearning ||
    senderLearning.learnedFromCount < AUTO_CATEGORY_BEHAVIOR_MIN_COUNT ||
    senderLearning.autoCategoryEnabled
  ) {
    return undefined;
  }

  if (
    categorization.categorySource !== "user" &&
    categorization.categorySource !== "learned"
  ) {
    return undefined;
  }

  return {
    type: "auto_category",
    sender: senderLearningMatch?.key ?? normalizeSenderLearningKey(message.from),
    category: senderLearning.learnedCategory,
  };
}

function normalizeMailMessage(
  message: MailMessageSeed,
  mailboxId: InboxId,
  senderCategoryLearning: SenderCategoryLearningStore,
  messageOwnershipInteractions: MessageOwnershipInteractionStore,
  currentUserId: string,
  mailboxStore?: MailboxStore,
): MailMessage {
  const {
    category: _category,
    categorySource: _categorySource,
    categoryConfidence: _categoryConfidence,
    suggestion: _suggestion,
    suggestionDismissed: _suggestionDismissed,
    behaviorSuggestion: _behaviorSuggestion,
    behaviorSuggestionDismissed: _behaviorSuggestionDismissed,
    ...baseMessage
  } = message;
  const categorization = resolveCuevionCategorization(
    message,
    mailboxId,
    senderCategoryLearning,
    mailboxStore,
  );
  const resolvedSignal = inferHeuristicSignal(message);
  const owner = resolveImplicitOwner(message.id, messageOwnershipInteractions);
  const priorityScore = resolveMessagePriorityScore(
    categorization.category,
    owner,
    message.sharedContext,
  );
  const internalClassification =
    message.internalClassification ??
    inferInternalClassification({ ...message, signal: resolvedSignal }, mailboxId);
  const normalizedAttachments = (message.attachments ?? []).map((attachment) =>
    normalizeMailAttachment(attachment),
  );

  return {
    ...baseMessage,
    threadId: resolveMailThreadId(message),
    signal: resolvedSignal,
    attachments: normalizedAttachments,
    internalClassification,
    suggestionDismissed: message.suggestionDismissed,
    behaviorSuggestionDismissed: message.behaviorSuggestionDismissed,
    owner,
    priorityScore,
    focusSignal: resolveMessageFocusSignal(priorityScore, owner, currentUserId),
    ...categorization,
    suggestion: resolveMailMessageSuggestion(message, categorization),
    behaviorSuggestion: resolveMailMessageBehaviorSuggestion(
      message,
      categorization,
      senderCategoryLearning,
    ),
  };
}

function hasLearnedShowLessBehavior(
  senderCategoryLearning: SenderCategoryLearningStore,
) {
  return Object.values(senderCategoryLearning).some(
    (entry) =>
      entry.learnedCategory === "Updates" &&
      entry.mailboxAction === "move" &&
      (entry.sourcePrioritySelection === "Show Less" ||
        entry.learnedFromCount > 0),
  );
}

function shouldRouteMessageToFilteredFolder(
  message: MailMessage,
  senderCategoryLearning: SenderCategoryLearningStore,
) {
  if (
    message.categoryConfidence === "low" ||
    message.isShared ||
    message.signal === "Priority" ||
    message.signal === "Active" ||
    message.signal === "For review" ||
    message.signal === "Shortlist"
  ) {
    return false;
  }

  const senderLearning = resolveSenderLearningEntry(
    message.from,
    senderCategoryLearning,
  )?.entry;

  if (
    senderLearning &&
    senderLearning.learnedCategory === "Updates" &&
    senderLearning.mailboxAction === "move"
  ) {
    return true;
  }

  const hasLearnedFilteredBehavior = hasLearnedShowLessBehavior(
    senderCategoryLearning,
  );

  if (!hasLearnedFilteredBehavior) {
    return false;
  }

  const hasExplicitQuietViewRouting =
    message.final_visibility === "show_low" ||
    message.action === "show_in_quiet_view";
  const isSafeLowValueClassification =
    message.internalClassification === "promo_reminder" ||
    message.internalClassification === "business_reminder" ||
    message.internalClassification === "info";

  return (
    hasExplicitQuietViewRouting &&
    isSafeLowValueClassification &&
    message.priorityScore === "low"
  );
}

function applyFilteredLearningFromMessages(
  messages: MailMessage[],
  onSaveLearningRule: (
    ruleValue: string,
    ruleType: "sender" | "domain",
    category: CuevionMessageCategory,
    mailboxAction?: "keep" | "move",
    options?: {
      sourceContext?: LearningDecisionSourceContext;
      sourcePrioritySelection?: LearningDecisionPrioritySelection | null;
      sourceMailboxId?: InboxId | null;
      sourceCurrentMailboxId?: InboxId | null;
    },
  ) => void,
) {
  if (messages.length === 0) {
    return;
  }

  const senderAddresses = Array.from(
    new Set(messages.map((message) => message.from).filter((value) => value.trim().length > 0)),
  );

  senderAddresses.forEach((senderAddress) => {
    onSaveLearningRule(senderAddress, "sender", "Updates", "move", {
      sourcePrioritySelection: "Show Less",
    });
  });
}

function createMailAttachmentId(name: string, size?: number, mimeType?: string) {
  return `${normalizeSenderLearningKey(name)}-${size ?? 0}-${normalizeSenderLearningKey(mimeType ?? "file")}`;
}

function normalizeMailAttachment(attachment: MailAttachmentInput): MailAttachment {
  if (typeof attachment === "string") {
    return {
      id: createMailAttachmentId(attachment),
      name: attachment,
    };
  }

  return {
    ...attachment,
    id: attachment.id || createMailAttachmentId(attachment.name, attachment.size, attachment.mimeType),
  };
}

function resolveMailDateMs(message: MailMessage) {
  if (message.createdAt) {
    const directDate = new Date(message.createdAt).getTime();

    if (!Number.isNaN(directDate)) {
      return directDate;
    }
  }

  const now = new Date();
  const timestamp = message.timestamp.trim();
  const relativeMinuteMatch = timestamp.match(/(\d+)\s+minutes?\s+ago/i);

  if (relativeMinuteMatch) {
    return now.getTime() - Number(relativeMinuteMatch[1]) * 60 * 1000;
  }

  const relativeHourMatch = timestamp.match(/(\d+)\s+hours?\s+ago/i);

  if (relativeHourMatch) {
    return now.getTime() - Number(relativeHourMatch[1]) * 60 * 60 * 1000;
  }

  if (/just now/i.test(timestamp)) {
    return now.getTime();
  }

  const todayMatch = timestamp.match(/^Today,\s*(\d{1,2}):(\d{2})$/i);

  if (todayMatch) {
    const resolvedDate = new Date(now);
    resolvedDate.setHours(Number(todayMatch[1]), Number(todayMatch[2]), 0, 0);
    return resolvedDate.getTime();
  }

  const yesterdayMatch = timestamp.match(/^Yesterday,\s*(\d{1,2}):(\d{2})$/i);

  if (yesterdayMatch) {
    const resolvedDate = new Date(now);
    resolvedDate.setDate(resolvedDate.getDate() - 1);
    resolvedDate.setHours(
      Number(yesterdayMatch[1]),
      Number(yesterdayMatch[2]),
      0,
      0,
    );
    return resolvedDate.getTime();
  }

  const weekdayMatch = timestamp.match(
    /^(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday),\s*(\d{1,2}):(\d{2})$/i,
  );

  if (weekdayMatch) {
    const resolvedDate = new Date(now);
    const targetIndex = weekdayIndexMap[weekdayMatch[1]];
    let dayOffset = (resolvedDate.getDay() - targetIndex + 7) % 7;

    if (dayOffset === 0) {
      dayOffset = 7;
    }

    resolvedDate.setDate(resolvedDate.getDate() - dayOffset);
    resolvedDate.setHours(Number(weekdayMatch[2]), Number(weekdayMatch[3]), 0, 0);
    return resolvedDate.getTime();
  }

  const monthMatch = timestamp.match(
    /^(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})\s+at\s+(\d{1,2}):(\d{2})$/i,
  );

  if (monthMatch) {
    const parsedDate = new Date(
      `${monthMatch[1]} ${monthMatch[2]}, ${now.getFullYear()} ${monthMatch[3]}:${monthMatch[4]}`,
    ).getTime();

    if (!Number.isNaN(parsedDate)) {
      return parsedDate;
    }
  }

  return 0;
}

const mailboxSecondaryActionButtonClass =
  "inline-flex h-9 w-9 items-center justify-center rounded-full border border-[var(--workspace-border)] bg-[var(--workspace-card-subtle)] text-[var(--workspace-text-soft)] transition-[background-color,border-color,color,transform] duration-150 hover:border-[var(--workspace-border-hover)] hover:bg-[var(--workspace-hover-surface)] active:scale-[0.98] focus-visible:outline-none";

const primaryActionSurfaceClass =
  "border border-[color:rgba(66,99,69,0.52)] bg-[linear-gradient(180deg,rgba(103,141,103,0.98),rgba(69,103,72,0.98))] text-[color:rgba(251,248,242,0.98)] shadow-[inset_0_1px_0_rgba(255,255,255,0.1),0_8px_18px_rgba(66,99,69,0.12)] transition-[background-image,border-color,transform,box-shadow] duration-150 hover:border-[color:rgba(58,88,62,0.6)] hover:bg-[linear-gradient(180deg,rgba(93,130,95,0.98),rgba(61,95,65,0.98))] active:scale-[0.99] focus-visible:outline-none";

const closeActionButtonClass =
  `inline-flex items-center gap-2 rounded-full px-4 py-2 text-[0.68rem] font-medium uppercase tracking-[0.16em] ${primaryActionSurfaceClass}`;

const mailboxPrimaryActionButtonClass =
  `inline-flex h-9 items-center justify-center rounded-full px-4 text-[0.68rem] font-medium uppercase tracking-[0.18em] ${primaryActionSurfaceClass}`;
const subtleSecondaryActionButtonClass =
  "inline-flex w-fit items-center rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-2 text-[0.66rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-soft)] transition-[background-color,border-color,color,transform] duration-150 hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)] active:scale-[0.99] focus-visible:outline-none";
const modalSecondaryActionButtonClass =
  "inline-flex h-10 items-center justify-center rounded-full border border-[var(--workspace-border)] bg-[var(--workspace-card-subtle)] px-5 text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.06)] transition-[background-color,border-color,color,transform] duration-150 hover:border-[var(--workspace-border-hover)] hover:bg-[var(--workspace-hover-surface-strong)] active:scale-[0.99] focus-visible:outline-none";
const modalTertiaryActionButtonClass =
  "inline-flex h-10 items-center justify-center rounded-full border border-[var(--workspace-border-soft)] bg-transparent px-5 text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-soft)] transition-[background-color,border-color,color,transform] duration-150 hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-card-subtle)] hover:text-[var(--workspace-text)] active:scale-[0.99] focus-visible:outline-none";
const learningModalPrimaryActionButtonClass =
  `inline-flex h-10 items-center justify-center rounded-full px-5 text-[0.72rem] font-medium uppercase tracking-[0.16em] ${primaryActionSurfaceClass}`;

const premiumGreenDotClass =
  "h-2.5 w-2.5 rounded-full bg-[radial-gradient(circle_at_30%_30%,rgba(192,225,188,0.98),rgba(118,170,112,0.96)_55%,rgba(72,118,72,0.96)_100%)] shadow-[0_0_0_3px_rgba(174,214,168,0.14),0_0_14px_rgba(142,194,132,0.24)]";

const primaryBadgeClass =
  "rounded-full bg-[linear-gradient(180deg,rgba(208,232,201,0.92),rgba(146,189,132,0.94)_58%,rgba(94,141,89,0.96))] px-2.5 py-1 text-[0.62rem] font-medium uppercase tracking-[0.18em] text-[color:rgba(41,73,45,0.96)] shadow-[inset_0_1px_0_rgba(255,255,255,0.36),0_8px_24px_rgba(118,170,112,0.14)]";

const unreadAttentionDotClass =
  "h-2 w-2 rounded-full bg-[#4E2070] shadow-[0_0_0_2px_rgba(78,32,112,0.08)]";
const contextMenuHoverSurfaceClass =
  "hover:bg-[var(--workspace-menu-hover)] hover:shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]";
const contextMenuItemClass =
  `flex w-full items-center rounded-[14px] px-3 py-[0.42rem] text-left text-[0.76rem] text-[var(--workspace-text)] transition-[background-color,color] duration-150 ${contextMenuHoverSurfaceClass}`;
const contextMenuMainItemClass =
  `flex w-full items-center rounded-[14px] px-3 py-2.5 text-left text-[0.82rem] text-[var(--workspace-text)] transition-[background-color,color] duration-150 ${contextMenuHoverSurfaceClass}`;
const contextMenuActiveItemClass =
  "bg-[var(--workspace-menu-hover)] shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]";

const contactMockTickets: ContactTicket[] = [
  {
    id: "#2418",
    subject: "Workspace setup question for shared inbox routing",
    status: "Open",
    updatedAt: "March 20, 2026",
    messages: [
      {
        senderType: "user",
        body:
          "Hi Cuevion, could you help me understand how to route a shared inbox so the workspace stays consistent for the team?",
        timestamp: "March 20, 2026 at 10:14",
      },
      {
        senderType: "cuevion",
        body:
          "Of course. We are reviewing the setup context from your workspace and will come back with the cleanest structure for shared routing.",
        timestamp: "March 20, 2026 at 11:02",
      },
    ],
  },
  {
    id: "#2394",
    subject: "Clarification on mailbox connection settings",
    status: "In progress",
    updatedAt: "March 18, 2026",
    messages: [
      {
        senderType: "user",
        body:
          "I want to confirm whether the current mailbox connection settings are the right ones for our secondary inbox before we change anything.",
        timestamp: "March 18, 2026 at 09:26",
      },
      {
        senderType: "cuevion",
        body:
          "We are checking the current connection details and comparing them against the intended setup so we can advise you precisely.",
        timestamp: "March 18, 2026 at 12:41",
      },
    ],
  },
  {
    id: "#2317",
    subject: "Resolved request about notification visibility",
    status: "Resolved",
    updatedAt: "March 12, 2026",
    messages: [
      {
        senderType: "user",
        body:
          "Could you confirm why some notification updates are only visible to part of the team?",
        timestamp: "March 11, 2026 at 16:08",
      },
      {
        senderType: "cuevion",
        body:
          "This was caused by workspace notification preferences. We adjusted the setup and confirmed the visibility issue is resolved.",
        timestamp: "March 12, 2026 at 08:54",
      },
    ],
  },
];

function MailToolbarIconButton({
  label,
  children,
  onClick,
  disabled,
  active,
}: {
  label: string;
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  active?: boolean;
}) {
  return (
    <div className="group relative">
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        className={`${mailboxSecondaryActionButtonClass} ${
          disabled
            ? "cursor-default opacity-40 hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-card-subtle)]"
            : active
              ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_6px_14px_rgba(118,170,112,0.08)] hover:bg-[linear-gradient(180deg,var(--workspace-accent-surface-hover-start),var(--workspace-accent-surface-hover-end))]"
              : ""
        }`}
        aria-label={label}
        aria-pressed={active}
      >
        {children}
      </button>
      <div className="pointer-events-none absolute left-1/2 top-full z-10 mt-2 -translate-x-1/2 rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-3 py-1 text-[0.62rem] font-medium tracking-[0.08em] text-[var(--workspace-text-soft)] opacity-0 shadow-panel transition-opacity duration-200 group-hover:opacity-100">
        {label}
      </div>
    </div>
  );
}

function CloseActionButton({
  onClick,
}: {
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={closeActionButtonClass}
    >
      Close
    </button>
  );
}

function ReadingLearningButton({
  open,
  onClick,
  triggerId,
}: {
  open: boolean;
  onClick: (event: MouseEvent<HTMLButtonElement>) => void;
  triggerId: "reading-pane" | "full-message";
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-reading-learning-trigger={triggerId}
      className={`inline-flex h-9 flex-none items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.14em] transition-[background-color,border-color,color,transform,box-shadow] duration-150 focus-visible:outline-none ${
        open
          ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-panel"
          : "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.16),0_8px_24px_rgba(118,170,112,0.08)] hover:bg-[linear-gradient(180deg,var(--workspace-accent-surface-hover-start),var(--workspace-accent-surface-hover-end))]"
      }`}
      aria-haspopup="menu"
      aria-expanded={open}
    >
      Learning
    </button>
  );
}

function WorkspaceModalLayer({ children }: { children: ReactNode }) {
  useLayoutEffect(() => {
    workspaceModalLockCount += 1;

    const { body, documentElement } = document;

    if (workspaceModalLockCount === 1) {
      workspaceModalLockScrollY = window.scrollY;
      workspaceModalBodyStyleSnapshot = {
        overflow: body.style.overflow,
        position: body.style.position,
        top: body.style.top,
        left: body.style.left,
        right: body.style.right,
        width: body.style.width,
      };
      workspaceModalHtmlOverflowSnapshot = documentElement.style.overflow;

      body.style.overflow = "hidden";
      body.style.position = "fixed";
      body.style.top = `-${workspaceModalLockScrollY}px`;
      body.style.left = "0";
      body.style.right = "0";
      body.style.width = "100%";
      documentElement.style.overflow = "hidden";
    }

    return () => {
      workspaceModalLockCount -= 1;

      if (workspaceModalLockCount === 0) {
        if (workspaceModalBodyStyleSnapshot) {
          body.style.overflow = workspaceModalBodyStyleSnapshot.overflow;
          body.style.position = workspaceModalBodyStyleSnapshot.position;
          body.style.top = workspaceModalBodyStyleSnapshot.top;
          body.style.left = workspaceModalBodyStyleSnapshot.left;
          body.style.right = workspaceModalBodyStyleSnapshot.right;
          body.style.width = workspaceModalBodyStyleSnapshot.width;
        }

        documentElement.style.overflow = workspaceModalHtmlOverflowSnapshot;
        window.scrollTo(0, workspaceModalLockScrollY);
      }
    };
  }, []);

  return (
    <div className="pointer-events-auto fixed inset-0 z-[321] bg-[var(--workspace-modal-scrim)] backdrop-blur-[2px]">
      <div className="flex min-h-dvh w-full items-center justify-center overflow-y-auto p-6">
        {children}
      </div>
    </div>
  );
}

function SettingsModalShell({
  open,
  themeMode,
  maxWidthClass = "max-w-[420px]",
  children,
}: {
  open: boolean;
  themeMode: "light" | "dark";
  maxWidthClass?: string;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) {
      return;
    }

    const isEditableTarget = (target: EventTarget | null) => {
      if (!(target instanceof HTMLElement)) {
        return false;
      }

      if (target.isContentEditable) {
        return true;
      }

      const tagName = target.tagName.toLowerCase();
      return tagName === "input" || tagName === "textarea" || tagName === "select";
    };

    const blockedKeys = new Set([
      "ArrowUp",
      "ArrowDown",
      "PageUp",
      "PageDown",
      "Home",
      "End",
      " ",
    ]);

    const preventWindowScroll = (event: Event) => {
      event.preventDefault();
    };

    const preventScrollKeys = (event: KeyboardEvent) => {
      if (isEditableTarget(event.target)) {
        return;
      }

      if (blockedKeys.has(event.key)) {
        event.preventDefault();
      }
    };

    window.addEventListener("wheel", preventWindowScroll, { passive: false });
    window.addEventListener("touchmove", preventWindowScroll, { passive: false });
    window.addEventListener("keydown", preventScrollKeys, { passive: false });

    return () => {
      window.removeEventListener("wheel", preventWindowScroll);
      window.removeEventListener("touchmove", preventWindowScroll);
      window.removeEventListener("keydown", preventScrollKeys);
    };
  }, [open]);

  if (!open) {
    return null;
  }

  const overlayStyle =
    themeMode === "dark"
      ? {
          background:
            "radial-gradient(circle at top, rgba(246,239,231,0.04), transparent 42%), rgba(10,10,9,0.22)",
          backdropFilter: "blur(2px)",
          WebkitBackdropFilter: "blur(2px)",
        }
      : {
          background:
            "radial-gradient(circle at top, rgba(255,252,247,0.14), transparent 46%), rgba(92,78,65,0.1)",
          backdropFilter: "blur(2px)",
          WebkitBackdropFilter: "blur(2px)",
        };

  return createPortal(
    <div
      data-theme={themeMode}
      className="fixed inset-0 z-[321] flex min-h-dvh items-center justify-center p-6"
      style={overlayStyle}
      onWheel={(event) => event.preventDefault()}
      onTouchMove={(event) => event.preventDefault()}
    >
      <div
        className={`w-full ${maxWidthClass} overflow-hidden rounded-[26px] border border-[var(--workspace-border)] bg-[var(--workspace-modal-bg)] p-6 shadow-[0_24px_70px_rgba(61,44,32,0.16),0_8px_20px_rgba(61,44,32,0.08)]`}
        onMouseDown={(event) => event.stopPropagation()}
        onWheel={(event) => event.stopPropagation()}
        onTouchMove={(event) => event.stopPropagation()}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}

function SettingsConfirmationModal({
  open,
  themeMode,
  title,
  description,
  cancelLabel = "Cancel",
  confirmLabel,
  confirmClassName,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  themeMode: "light" | "dark";
  title: string;
  description: string;
  cancelLabel?: string;
  confirmLabel: string;
  confirmClassName?: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <SettingsModalShell open={open} themeMode={themeMode}>
      <div className="space-y-2">
        <h2 className="text-[1.25rem] font-medium tracking-tight text-[var(--workspace-text)]">
          {title}
        </h2>
        <p className="text-[0.9rem] leading-7 text-[var(--workspace-text-soft)]">
          {description}
        </p>
      </div>

      <div className="mt-6 flex items-center justify-end gap-3">
        <button
          type="button"
          onClick={onCancel}
          className={settingsSubtleActionClass}
        >
          {cancelLabel}
        </button>
        <button
          type="button"
          onClick={onConfirm}
          className={confirmClassName ?? settingsPrimaryActionClass}
        >
          {confirmLabel}
        </button>
      </div>
    </SettingsModalShell>
  );
}

function ContextSubmenuTriggerRow({
  label,
  active,
  onClick,
  onMouseEnter,
  onMouseLeave,
}: {
  label: string;
  active?: boolean;
  onClick: (event: MouseEvent<HTMLButtonElement>) => void;
  onMouseEnter: (event: MouseEvent<HTMLButtonElement>) => void;
  onMouseLeave?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      className={`group ${contextMenuMainItemClass} justify-between ${
        active ? contextMenuActiveItemClass : ""
      }`}
    >
      <span>{label}</span>
      <span
        className={`text-[0.72rem] transition-colors duration-200 ${
          active
            ? "text-[var(--workspace-text-soft)]"
            : "text-[var(--workspace-text-faint)] group-hover:text-[var(--workspace-text-soft)]"
        }`}
      >
        ›
      </span>
    </button>
  );
}

function getAnchoredSubmenuPosition({
  parentLeft,
  parentWidth,
  anchorY,
  anchorHeight,
  submenuWidth,
  submenuHeight,
  interactionRect,
  anchorOffsetY = -4,
}: {
  parentLeft: number;
  parentWidth: number;
  anchorY: number;
  anchorHeight: number;
  submenuWidth: number;
  submenuHeight: number;
  interactionRect?: DOMRect;
  anchorOffsetY?: number;
}) {
  const menuGap = 8;
  const viewportPadding = 12;
  const boundsLeft = interactionRect?.left ?? viewportPadding;
  const boundsTop = interactionRect?.top ?? viewportPadding;
  const boundsRight = interactionRect?.right ?? window.innerWidth - viewportPadding;
  const boundsBottom = interactionRect?.bottom ?? window.innerHeight - viewportPadding;
  const parentMenuLeft = parentLeft;
  const parentMenuRight = parentLeft + parentWidth;
  const availableRight = boundsRight - parentMenuRight - viewportPadding - menuGap;
  const openRight = availableRight >= submenuWidth;
  const left = openRight
    ? parentMenuRight + menuGap
    : parentMenuLeft - submenuWidth - menuGap;
  const preferredTop = anchorY + anchorOffsetY;
  const minTop = boundsTop + viewportPadding;
  const maxTop = Math.max(minTop, boundsBottom - submenuHeight - viewportPadding);
  const top = Math.max(minTop, Math.min(preferredTop, maxTop));

  return {
    left: Math.max(
      boundsLeft + viewportPadding,
      Math.min(left, boundsRight - submenuWidth - viewportPadding),
    ),
    top,
  };
}

function MailboxConnectionState() {
  return (
    <span className="inline-flex items-center gap-2 text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[color:rgba(98,92,84,0.78)]">
      <span className={premiumGreenDotClass} />
      Connected
    </span>
  );
}

const inboxDisplayConfig: Record<
  PresetInboxId,
  Omit<OrderedMailbox, "id" | "email"> & { fallbackEmail: string }
> = {
  main: {
    title: "Main Inbox",
    fallbackEmail: "main@hysteriarecs.com",
    detail: "Connected and synced 2 minutes ago",
    state: "CONNECTED",
  },
  promo: {
    title: "Promo",
    fallbackEmail: "promo@hysteriarecs.com",
    detail: "Connected and synced 2 minutes ago",
    state: "CONNECTED",
  },
  demo: {
    title: "Demo",
    fallbackEmail: "demo@hysteriarecs.com",
    detail: "Connected with 14 unread categorized items",
    state: "CONNECTED",
  },
  business: {
    title: "Business",
    fallbackEmail: "rutger@hysteriarecs.com",
    detail: "Connected with 3 priority threads",
    state: "CONNECTED",
  },
  legal: {
    title: "Legal",
    fallbackEmail: "legal@hysteriarecs.com",
    detail: "Connected with active contract and rights threads",
    state: "CONNECTED",
  },
  finance: {
    title: "Finance",
    fallbackEmail: "finance@hysteriarecs.com",
    detail: "Connected with payout and statement activity",
    state: "CONNECTED",
  },
  royalty: {
    title: "Royalty",
    fallbackEmail: "royalty@hysteriarecs.com",
    detail: "Connected with catalog and royalty follow-up",
    state: "CONNECTED",
  },
  sync: {
    title: "Licensing",
    fallbackEmail: "sync@hysteriarecs.com",
    detail: "Connected with licensing opportunities in progress",
    state: "CONNECTED",
  },
};

const presetInboxIds = Object.keys(inboxDisplayConfig) as PresetInboxId[];

function isPresetInboxId(inboxId: InboxId): inboxId is PresetInboxId {
  return presetInboxIds.includes(inboxId as PresetInboxId);
}

function findCustomInboxDefinition(
  onboardingState: OnboardingState,
  inboxId: InboxId,
): CustomInboxDefinition | undefined {
  return onboardingState.customInboxes.find((inbox) => inbox.id === inboxId);
}

function buildCustomInboxFallbackEmail(name: string) {
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ".")
    .replace(/^\.+|\.+$/g, "");

  return `${slug || "custom"}@cuevion.local`;
}

function resolveOnboardingInboxDisplayInfo(
  onboardingState: OnboardingState,
  inboxId: InboxId,
) {
  if (isPresetInboxId(inboxId)) {
    return inboxDisplayConfig[inboxId];
  }

  const customInboxName =
    findCustomInboxDefinition(onboardingState, inboxId)?.name ?? "Custom Inbox";

  return {
    title: customInboxName,
    fallbackEmail: buildCustomInboxFallbackEmail(customInboxName),
    detail: "Connected custom inbox",
    state: "CONNECTED",
  };
}

function createSeedMessagesForMailbox(
  inboxId: InboxId,
  mailboxTitle: string,
  mailboxEmail: string,
): MailMessageSeed[] {
  if (isPresetInboxId(inboxId)) {
    return mailboxMessages[inboxId];
  }

  const normalizedDomain =
    mailboxTitle
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "") || "custom";

  return [
    {
      id: `${inboxId}-1`,
      sender: `${mailboxTitle} Contact`,
      subject: `${mailboxTitle} thread ready for review`,
      snippet: "Initial messages for this inbox are ready to triage.",
      time: "Today",
      unread: true,
      signal: "Active",
      from: `team@${normalizedDomain}mail.com`,
      to: mailboxEmail,
      timestamp: "Today, 10:12",
      body: [
        `This is the first active thread in ${mailboxTitle}.`,
        "Use it like any other inbox after onboarding completes.",
      ],
    },
    {
      id: `${inboxId}-2`,
      sender: "Operations",
      subject: `${mailboxTitle} setup summary`,
      snippet: "Connection details are in place and the inbox is ready.",
      time: "Yesterday",
      signal: "Update",
      from: "ops@cuevion.com",
      to: mailboxEmail,
      timestamp: "Yesterday, 17:40",
      body: [
        `${mailboxTitle} has been added to the workspace.`,
        "You can now process messages here with the same workflow as the preset inboxes.",
      ],
    },
  ];
}

function createInitialMailboxStore(
  orderedMailboxes: OrderedMailbox[],
  senderCategoryLearning: SenderCategoryLearningStore,
  messageOwnershipInteractions: MessageOwnershipInteractionStore,
  currentUserId: string,
  workspaceDataMode: WorkspaceDataMode,
): MailboxStore {
  const orderedMailboxMap = new Map(
    orderedMailboxes.map((mailbox) => [mailbox.id, mailbox] as const),
  );
  const inboxIds = Array.from(
    new Set<InboxId>([...presetInboxIds, ...orderedMailboxes.map((mailbox) => mailbox.id)]),
  );

  const initialStore = inboxIds.reduce<MailboxStore>((store, inboxId) => {
    const mailboxInfo = orderedMailboxMap.get(inboxId);
    const fallbackInfo = isPresetInboxId(inboxId)
      ? inboxDisplayConfig[inboxId]
      : {
          title: mailboxInfo?.title ?? "Custom Inbox",
          fallbackEmail: buildCustomInboxFallbackEmail(
            mailboxInfo?.title ?? "custom",
          ),
          detail: mailboxInfo?.detail ?? "Connected custom inbox",
          state: mailboxInfo?.state ?? "CONNECTED",
        };
    const mailboxTitle = mailboxInfo?.title ?? fallbackInfo.title;
    const mailboxEmail = mailboxInfo?.email ?? fallbackInfo.fallbackEmail;

    if (workspaceDataMode === "live") {
      store[inboxId] = createEmptyMailboxCollections();
      return store;
    }

    const normalizedInboxMessages = createSeedMessagesForMailbox(
      inboxId,
      mailboxTitle,
      mailboxEmail,
    ).map((message) =>
      normalizeMailMessage(
        message,
        inboxId,
        senderCategoryLearning,
        messageOwnershipInteractions,
        currentUserId,
      ),
    );
    const inboxMessagesByFolder = normalizedInboxMessages.reduce<{
      inbox: MailMessage[];
      filtered: MailMessage[];
    }>(
      (messagesByFolder, message) => {
        if (shouldRouteMessageToFilteredFolder(message, senderCategoryLearning)) {
          messagesByFolder.filtered.push(message);
        } else {
          messagesByFolder.inbox.push(message);
        }

        return messagesByFolder;
      },
      {
        inbox: [],
        filtered: [],
      },
    );

    store[inboxId] = {
      Inbox: inboxMessagesByFolder.inbox,
      Drafts: [
        normalizeMailMessage({
          id: `${inboxId}-draft-1`,
          sender: "Draft",
          subject: `Pending reply in ${mailboxTitle}`,
          snippet: "Holding this draft until the release timing is confirmed internally.",
          time: "Today",
          signal: "Draft",
          from: mailboxEmail,
          to: "team@hysteriarecs.com",
          timestamp: "Today, 09:28",
          body: [
            "Holding this draft until the release timing is confirmed internally.",
            "Once the team signs off, this can be sent immediately.",
          ],
          attachments: ["notes.txt"],
        }, inboxId, senderCategoryLearning, messageOwnershipInteractions, currentUserId),
      ],
      Sent: [
        normalizeMailMessage({
          id: `${inboxId}-sent-1`,
          sender: "You",
          subject: `Follow-up sent from ${mailboxTitle}`,
          snippet: "Shared the latest notes and asked for a final sign-off on timing.",
          time: "Yesterday",
          signal: "Sent",
          from: mailboxEmail,
          to: "team@hysteriarecs.com",
          timestamp: "Yesterday at 17:12",
          body: [
            "Shared the latest notes here and asked the team for a final sign-off on timing.",
            "Once everyone confirms, this thread is ready to move forward.",
          ],
          attachments: ["release-notes.pdf"],
        }, inboxId, senderCategoryLearning, messageOwnershipInteractions, currentUserId),
      ],
      Archive: [
        normalizeMailMessage({
          id: `${inboxId}-archive-1`,
          sender: "Distribution",
          subject: "Assets delivered to DSP partners",
          snippet: "Everything has been delivered and archived for the release window.",
          time: "Mar 16",
          signal: "Archived",
          from: "distribution@partner.net",
          to: mailboxEmail,
          timestamp: "March 16 at 12:05",
          body: [
            "All assets were delivered successfully to DSP partners and are now archived.",
          ],
          attachments: ["delivery-report.pdf"],
        }, inboxId, senderCategoryLearning, messageOwnershipInteractions, currentUserId),
      ],
      Filtered: inboxMessagesByFolder.filtered,
      Spam: [
        normalizeMailMessage({
          id: `${inboxId}-spam-1`,
          sender: "Cold Outreach",
          subject: "Guaranteed playlist boost this week",
          snippet: "Moved out of active workflow after repeated low-value unsolicited outreach.",
          time: "Mar 15",
          signal: "Spam",
          from: "growth@unknownreach.co",
          to: mailboxEmail,
          timestamp: "March 15 at 11:06",
          body: [
            "This message was moved out of active workflow and treated as unwanted future outreach.",
          ],
          attachments: [],
        }, inboxId, senderCategoryLearning, messageOwnershipInteractions, currentUserId),
      ],
      Trash: [
        normalizeMailMessage({
          id: `${inboxId}-trash-1`,
          sender: "Old Outreach",
          subject: "Outdated promo contact list",
          snippet: "Superseded by the current contact list and moved out of active workflow.",
          time: "Mar 14",
          signal: "Trash",
          from: "archive@hysteriarecs.com",
          to: mailboxEmail,
          timestamp: "March 14 at 09:18",
          body: [
            "This thread was removed from the active workflow after the updated list replaced it.",
          ],
          attachments: [],
        }, inboxId, senderCategoryLearning, messageOwnershipInteractions, currentUserId),
      ],
    };

    return store;
  }, {} as MailboxStore);

  return normalizeMailboxStore(
    initialStore,
    orderedMailboxes,
    senderCategoryLearning,
    messageOwnershipInteractions,
    currentUserId,
  );
}

function createEmptyMailboxCollections(): MailboxCollections {
  return {
    Inbox: [],
    Drafts: [],
    Sent: [],
    Archive: [],
    Filtered: [],
    Spam: [],
    Trash: [],
  };
}

function getMessageSignature(message: MailMessage) {
  return [
    message.sender.trim().toLowerCase(),
    message.subject.trim().toLowerCase(),
    message.timestamp.trim().toLowerCase(),
    message.snippet.trim().toLowerCase(),
  ].join("::");
}

function normalizeMailboxStore(
  store: MailboxStore,
  orderedMailboxes: OrderedMailbox[],
  senderCategoryLearning: SenderCategoryLearningStore,
  messageOwnershipInteractions: MessageOwnershipInteractionStore,
  currentUserId: string,
): MailboxStore {
  const mailboxOrder = [
    ...orderedMailboxes.map((mailbox) => mailbox.id),
    ...presetInboxIds.filter(
      (mailboxId) => !orderedMailboxes.some((mailbox) => mailbox.id === mailboxId),
    ),
  ];
  const signatureOwner = new Map<string, { mailboxId: InboxId; folder: MailFolder }>();
  const nextStore = {} as MailboxStore;

  for (const mailboxId of mailboxOrder) {
    const mailboxCollections = store[mailboxId];

    if (!mailboxCollections) {
      continue;
    }

    nextStore[mailboxId] = {
      Inbox: [],
      Drafts: [],
      Sent: [],
      Archive: [],
      Filtered: [],
      Spam: [],
      Trash: [],
    };
  }

  for (const folder of canonicalFolderOrder) {
    for (const mailboxId of mailboxOrder) {
      const mailboxCollections = store[mailboxId];

      if (!mailboxCollections) {
        continue;
      }

      const seenInFolder = new Set<string>();

      for (const message of mailboxCollections[folder]) {
        const signature = message.id || getMessageSignature(message);
        const fallbackSignature = getMessageSignature(message);
        const uniquenessKey = `${signature}::${fallbackSignature}`;
        const hasStableId = Boolean(message.id);

        if (
          seenInFolder.has(uniquenessKey) ||
          signatureOwner.has(signature) ||
          (!hasStableId && signatureOwner.has(fallbackSignature))
        ) {
          continue;
        }

        seenInFolder.add(uniquenessKey);
        signatureOwner.set(signature, { mailboxId, folder });
        if (!hasStableId) {
          signatureOwner.set(fallbackSignature, { mailboxId, folder });
        }
        nextStore[mailboxId][folder].push(
          normalizeMailMessage(
            message,
            mailboxId,
            senderCategoryLearning,
            messageOwnershipInteractions,
            currentUserId,
            store,
          ),
        );
      }
    }
  }

  return nextStore;
}

function formatMailboxIdentityTitle(inboxId: InboxId, email: string, fallbackTitle: string) {
  if (inboxId !== "main") {
    return fallbackTitle;
  }

  const trimmedFallbackTitle = fallbackTitle.trim();

  if (
    trimmedFallbackTitle &&
    trimmedFallbackTitle !== inboxDisplayConfig.main.title
  ) {
    return trimmedFallbackTitle;
  }

  const localPart = email.split("@")[0]?.trim();

  if (!localPart) {
    return "Main";
  }

  return localPart
    .split(/[._-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function getOrderedMailboxes(onboardingState: OnboardingState): OrderedMailbox[] {
  const connectedMailboxIds = onboardingState.selectedInboxes.filter(
    (inboxId) => onboardingState.inboxConnections[inboxId].connected,
  );
  const orderedMailboxIds =
    connectedMailboxIds.length > 0
      ? connectedMailboxIds
      : onboardingState.selectedInboxes;

  return orderedMailboxIds.map((inboxId) => {
    const config = resolveOnboardingInboxDisplayInfo(onboardingState, inboxId);
    const connection = onboardingState.inboxConnections[inboxId];
    const normalizedEmail = (
      connection.email.trim() || config.fallbackEmail
    ).toLowerCase();

    return {
      id: inboxId,
      title: formatMailboxIdentityTitle(inboxId, normalizedEmail, config.title),
      email: normalizedEmail,
      detail: config.detail,
      state: config.state,
    };
  });
}

const mailboxMessages: Record<PresetInboxId, MailMessageSeed[]> = {
  main: [
    {
      id: "main-1",
      sender: "Universal Music",
      subject: "Updated agreement and final comments",
      snippet: "Sharing the revised agreement here with the commercial note attached.",
      time: "11:18",
      unread: true,
      signal: "Priority",
      from: "legal@universalmusic.com",
      to: "main@hysteriarecs.com",
      timestamp: "Today, 11:18",
      isShared: true,
      sharedContext: {
        reason: "team_activity",
      },
      body: [
        "Hi Rutger,",
        "We attached the revised agreement and highlighted the clauses that changed after yesterday's discussion.",
        "Commercially, the counterparty is aligned on the revised fee structure, but they still want written confirmation on the fallback term if the campaign window shifts by more than seven days.",
        "We also added a short summary of the changes for management, including the narrower exclusivity wording and the proposed schedule for countersignature.",
        "If the agreement is approved this afternoon, the production team can continue with delivery and the release note can move into the final review pass.",
        "Separately, artwork and social cutdowns are now ready. We included preview frames below so you can check the visual direction against the updated contract language.",
        "Can you confirm whether this version can move forward on your side today?",
      ],
      attachments: [
        "contract-v2.pdf",
        "commercial-note.txt",
        "campaign-brief.pdf",
        "artwork-01.jpg",
        "artwork-02.jpg",
      ],
    },
    {
      id: "main-2",
      sender: "Armada",
      subject: "Re: rollout timing for next Friday",
      snippet: "The release timing still works, but we need final confirmation on assets.",
      time: "09:42",
      unread: true,
      signal: "Active",
      from: "team@armadamusic.com",
      to: "main@hysteriarecs.com",
      timestamp: "Today, 09:42",
      body: [
        "Morning,",
        "The rollout timing still looks good from our side. We only need the final asset package before end of day.",
        "Let us know if anything has shifted in the release plan.",
      ],
      attachments: ["release-checklist.pdf"],
    },
    {
      id: "main-3",
      sender: "Beatport",
      subject: "Playlist placement recap",
      snippet: "Your latest campaign picked up traction across the weekend window.",
      time: "Yesterday",
      signal: "Active",
      from: "updates@beatport.com",
      to: "main@hysteriarecs.com",
      timestamp: "Yesterday, 18:07",
      body: [
        "Hi,",
        "Sharing a quick recap of the latest playlist traction and engagement performance from the weekend.",
      ],
      attachments: ["playlist-recap.xlsx"],
    },
    {
      id: "main-4",
      sender: "Artist Manager",
      subject: "Follow-up on revised split sheet",
      snippet: "Can you confirm whether the revised split sheet is approved from legal and finance?",
      time: "08:17",
      unread: true,
      signal: "Priority",
      from: "manager@northlineartists.com",
      to: "main@hysteriarecs.com",
      timestamp: "Today, 08:17",
      body: [
        "Morning Rutger,",
        "Checking whether the revised split sheet is approved from your legal and finance side.",
        "If confirmed today, we can lock the delivery package immediately.",
      ],
      attachments: ["split-sheet-v3.xlsx"],
    },
    {
      id: "main-5",
      sender: "Sony Orchard",
      subject: "DSP delivery note for Friday release",
      snippet: "All core assets are queued but we still need the final radio edit before cutoff.",
      time: "07:54",
      signal: "Timing",
      from: "delivery@theorchard.com",
      to: "main@hysteriarecs.com",
      timestamp: "Today, 07:54",
      body: [
        "Hi team,",
        "Core assets are queued for DSP delivery, but the final radio edit is still missing before the cutoff window.",
        "Please send the approved version as soon as it is available.",
      ],
      attachments: ["delivery-window.pdf"],
      collaboration: {
        state: "needs_action",
        requestedBy: "Rutger",
        requestedUserId: "david_cole",
        requestedUserName: "David Cole",
        createdAt: new Date("2026-03-23T08:02:00").getTime(),
        updatedAt: new Date("2026-03-23T08:09:00").getTime(),
        participants: [
          {
            id: "david_cole",
            name: "David Cole",
            email: "david@cuevion.com",
            kind: "internal",
            status: "invited",
          },
          {
            id: "emma_stone",
            name: "Emma Stone",
            email: "emma@cuevion.com",
            kind: "internal",
            status: "active",
          },
        ],
        previewText: "Can someone confirm when the final radio edit can be delivered?",
        messages: [
          {
            id: "main-5-collaboration-reply",
            authorId: "emma_stone",
            authorName: "Emma Stone",
            text: "I can check whether the approved file lands before the cutoff.",
            timestamp: new Date("2026-03-23T08:09:00").getTime(),
            visibility: "shared",
          },
        ],
      },
    },
    {
      id: "main-6",
      sender: "Hypeddit",
      subject: "Promo campaign performance snapshot",
      snippet: "Your latest pre-save campaign improved conversion in Germany and the UK.",
      time: "07:08",
      signal: "Update",
      from: "reports@hypeddit.com",
      to: "main@hysteriarecs.com",
      timestamp: "Today, 07:08",
      body: [
        "Hi,",
        "Your latest pre-save campaign improved conversion in Germany and the UK overnight.",
        "A full export is attached if you want to compare this against the previous campaign.",
      ],
      attachments: ["campaign-snapshot.csv"],
    },
    {
      id: "main-7",
      sender: "MAVEZ",
      subject: "Checking if the demo link came through",
      snippet: "Following up once on the private demo link in case the first message landed in promo.",
      time: "06:42",
      unread: true,
      signal: "For review",
      from: "mavez.artist@gmail.com",
      to: "main@hysteriarecs.com",
      timestamp: "Today, 06:42",
      body: [
        "Hi Hysteria team,",
        "Following up once on the private demo link in case the first message landed in promo or spam.",
        "Happy to resend a different stream link if needed.",
      ],
      attachments: ["private-link.txt"],
    },
    {
      id: "main-8",
      sender: "Legal Counsel",
      subject: "Counterparty wants indemnity language tightened",
      snippet: "Their lawyer proposed a narrower indemnity carve-out and wants a same-day response.",
      time: "Yesterday",
      unread: true,
      signal: "Priority",
      from: "counsel@rightsfirm.nl",
      to: "main@hysteriarecs.com",
      timestamp: "Yesterday, 21:14",
      isShared: true,
      sharedContext: {
        reason: "multiple_viewers",
      },
      body: [
        "Hi Rutger,",
        "Counterparty counsel proposed a narrower indemnity carve-out and is asking for a same-day response.",
        "Please review the markup and confirm whether we can accept their position.",
      ],
      attachments: ["markup-v5.docx"],
    },
    {
      id: "main-9",
      sender: "Spotify for Artists",
      subject: "Pitch accepted for editorial review",
      snippet: "The release pitch is now in editorial review and can still be updated for 24 hours.",
      time: "Yesterday",
      signal: "Active",
      from: "no-reply@spotify.com",
      to: "main@hysteriarecs.com",
      timestamp: "Yesterday, 19:52",
      body: [
        "Good news,",
        "The release pitch is now in editorial review and can still be updated for another 24 hours.",
        "If you need to revise the story angle, do it before the review window closes.",
      ],
    },
    {
      id: "main-10",
      sender: "Demo Team",
      subject: "Late-night club cut shortlisted internally",
      snippet: "This one may fit the Friday lane if A&R wants a second listen on the topline.",
      time: "Yesterday",
      signal: "Shortlist",
      from: "demo@hysteriarecs.com",
      to: "main@hysteriarecs.com",
      timestamp: "Yesterday, 18:49",
      body: [
        "Internal note,",
        "This shortlisted demo may fit the Friday lane if A&R wants a second listen on the topline.",
        "Worth checking before the next release planning call.",
      ],
      attachments: ["private-link.txt"],
    },
    {
      id: "main-11",
      sender: "Royalty Support",
      subject: "Question on Q1 recoupment line",
      snippet: "The artist team is asking why the recoupment line moved after the latest statement revision.",
      time: "Yesterday",
      unread: true,
      signal: "Priority",
      from: "support@royaltyhub.io",
      to: "main@hysteriarecs.com",
      timestamp: "Yesterday, 16:33",
      body: [
        "Hi,",
        "The artist team is asking why the recoupment line moved after the latest statement revision.",
        "Can someone from finance or label ops clarify before we reply back?",
      ],
      attachments: ["statement-q1.pdf"],
    },
    {
      id: "main-12",
      sender: "Artist Liaison",
      subject: "Manager wants updated release one-sheet",
      snippet: "Can you send the latest one-sheet with confirmed socials and editorial notes included?",
      time: "Yesterday",
      signal: "Active",
      from: "liaison@artistteam.com",
      to: "main@hysteriarecs.com",
      timestamp: "Yesterday, 15:24",
      body: [
        "Hi team,",
        "The manager wants the latest release one-sheet with confirmed socials and editorial notes included.",
        "Please send the current version once updated.",
      ],
      attachments: ["release-one-sheet.pdf"],
    },
    {
      id: "main-13",
      sender: "The Orchard",
      subject: "UPC mismatch detected on delivery",
      snippet: "One UPC in the metadata package does not match the approved master sheet.",
      time: "Yesterday",
      unread: true,
      signal: "Priority",
      from: "support@orchard.com",
      to: "main@hysteriarecs.com",
      timestamp: "Yesterday, 14:58",
      body: [
        "Hi,",
        "One UPC in the metadata package does not match the approved master sheet.",
        "Please confirm the correct code so delivery can continue without delay.",
      ],
      attachments: ["metadata-diff.csv"],
    },
    {
      id: "main-14",
      sender: "Press Contact",
      subject: "Interview window still available on Thursday",
      snippet: "We can still slot a short interview if the artist can confirm by this afternoon.",
      time: "Yesterday",
      signal: "Timing",
      from: "editor@clubculturemag.com",
      to: "main@hysteriarecs.com",
      timestamp: "Yesterday, 13:18",
      body: [
        "Hi,",
        "We can still slot a short interview on Thursday if the artist can confirm by this afternoon.",
        "Let us know and we will reserve the slot.",
      ],
    },
    {
      id: "main-15",
      sender: "Distribution Ops",
      subject: "YouTube Content ID claim resolved",
      snippet: "The claim on the teaser asset has been cleared and the video can go back live.",
      time: "Yesterday",
      signal: "Archived",
      from: "ops@distributor.net",
      to: "main@hysteriarecs.com",
      timestamp: "Yesterday, 12:41",
      body: [
        "Update,",
        "The claim on the teaser asset has been cleared and the video can go back live.",
        "No further action is needed from your side.",
      ],
    },
    {
      id: "main-16",
      sender: "A&R Notes",
      subject: "Demo shortlist needs final yes/no before Friday",
      snippet: "Three cuts are still undecided and should be cleared before release planning.",
      time: "Yesterday",
      unread: true,
      signal: "For review",
      from: "ar@hysteriarecs.com",
      to: "main@hysteriarecs.com",
      timestamp: "Yesterday, 11:57",
      isShared: true,
      sharedContext: {
        reason: "assigned",
      },
      body: [
        "Internal reminder,",
        "Three shortlisted cuts are still undecided and should be cleared before Friday planning.",
        "Please review the notes and leave a final yes or no.",
      ],
      attachments: ["shortlist-notes.pdf"],
    },
    {
      id: "main-17",
      sender: "Distributor Note",
      subject: "Mastering delivery accepted",
      snippet: "All WAV masters were accepted and release assets are now queued for ingestion.",
      time: "Yesterday",
      signal: "Update",
      from: "delivery@fuga.com",
      to: "main@hysteriarecs.com",
      timestamp: "Yesterday, 10:26",
      body: [
        "Hi,",
        "All WAV masters were accepted and release assets are now queued for ingestion.",
        "We will send a follow-up if any platform-specific warnings appear.",
      ],
    },
    {
      id: "main-18",
      sender: "Artist Manager",
      subject: "Can we push artwork sign-off one day?",
      snippet: "The artist wants one final artwork adjustment before the release deck goes out.",
      time: "Tue",
      unread: true,
      signal: "Timing",
      from: "manager@afterhoursartists.com",
      to: "main@hysteriarecs.com",
      timestamp: "Tuesday, 18:44",
      body: [
        "Hi Rutger,",
        "The artist wants one final artwork adjustment before the release deck goes out.",
        "Can we push sign-off by one day without affecting the wider timeline?",
      ],
      attachments: ["artwork-v4.jpg"],
      collaboration: {
        state: "needs_review",
        requestedBy: "Rutger",
        requestedUserId: "emma_stone",
        requestedUserName: "Emma Stone",
        createdAt: new Date("2026-03-22T17:58:00").getTime(),
        updatedAt: new Date("2026-03-22T18:10:00").getTime(),
        participants: [
          {
            id: "emma_stone",
            name: "Emma Stone",
            email: "emma@cuevion.com",
            kind: "internal",
            status: "active",
          },
          {
            id: "david_cole",
            name: "David Cole",
            email: "david@cuevion.com",
            kind: "internal",
            status: "active",
          },
        ],
        previewText: "Need a quick call on timing before we answer the artist team.",
        messages: [
          {
            id: "main-18-collaboration-start",
            authorId: "rutger",
            authorName: "Rutger",
            text: "Could one of you sanity check the sign-off timing here?",
            timestamp: new Date("2026-03-22T17:58:00").getTime(),
            visibility: "internal",
          },
          {
            id: "main-18-collaboration-mention",
            authorId: "david_cole",
            authorName: "David Cole",
            text: "@emmastone can you check whether shifting sign-off by a day still works?",
            timestamp: new Date("2026-03-22T18:10:00").getTime(),
            visibility: "internal",
            mentions: [
              {
                id: "emma_stone",
                name: "Emma Stone",
                email: "emma@cuevion.com",
                handle: "emmastone",
                notify: true,
              },
            ],
          },
        ],
      },
    },
    {
      id: "main-19",
      sender: "Legal Follow-up",
      subject: "Need final approval on neighboring rights clause",
      snippet: "This is the only open legal point before we can countersign the agreement.",
      time: "Tue",
      signal: "Priority",
      from: "claudia@rightsfirm.nl",
      to: "main@hysteriarecs.com",
      timestamp: "Tuesday, 16:15",
      body: [
        "Hi,",
        "The neighboring rights clause is the final open point before countersignature.",
        "Please confirm whether we accept the revised wording.",
      ],
      attachments: ["rights-clause.docx"],
    },
    {
      id: "main-20",
      sender: "TikTok Music",
      subject: "Clip usage trend for current single",
      snippet: "The current single is gaining clip usage faster than forecast in Benelux.",
      time: "Tue",
      signal: "Update",
      from: "insights@tiktokmusic.com",
      to: "main@hysteriarecs.com",
      timestamp: "Tuesday, 14:03",
      body: [
        "Hi,",
        "The current single is gaining clip usage faster than forecast in Benelux.",
        "A chart snapshot is attached if you want to compare against previous campaigns.",
      ],
      attachments: ["clip-trend.pdf"],
    },
    {
      id: "main-21",
      sender: "Noctra",
      subject: "Updated version on the shortlisted demo",
      snippet: "Sending the tightened drop version after the note about the mid-section energy.",
      time: "Mon",
      unread: true,
      signal: "Shortlist",
      from: "noctra.music@gmail.com",
      to: "main@hysteriarecs.com",
      timestamp: "Monday, 20:09",
      body: [
        "Hey team,",
        "Sending the tightened drop version after your note about the mid-section energy.",
        "Would love to know whether this keeps the track in shortlist territory.",
      ],
      attachments: ["private-link.txt", "version-notes.pdf"],
    },
    {
      id: "main-22",
      sender: "Finance",
      subject: "Advance payment confirmed",
      snippet: "The artist advance has been scheduled and should clear in the next banking run.",
      time: "Mon",
      signal: "Active",
      from: "finance@hysteriarecs.com",
      to: "main@hysteriarecs.com",
      timestamp: "Monday, 17:36",
      body: [
        "Hi,",
        "The artist advance has been scheduled and should clear in the next banking run.",
        "No further action is needed unless the manager asks for confirmation.",
      ],
    },
    {
      id: "main-23",
      sender: "Promo Team",
      subject: "Need shortlist of priority replies for tomorrow",
      snippet: "Please flag the threads that should be answered before the campaign handoff call.",
      time: "Mon",
      unread: true,
      signal: "Priority",
      from: "promo@hysteriarecs.com",
      to: "main@hysteriarecs.com",
      timestamp: "Monday, 15:42",
      body: [
        "Internal request,",
        "Please flag the threads that should be answered before tomorrow's campaign handoff call.",
        "That will help us line up the reply queue for the morning.",
      ],
    },
    {
      id: "main-24",
      sender: "Manager Reply",
      subject: "Artist approved the final radio edit",
      snippet: "You can now send the approved radio edit to distribution and press.",
      time: "Mon",
      signal: "Archived",
      from: "reply@artistmgmt.com",
      to: "main@hysteriarecs.com",
      timestamp: "Monday, 11:08",
      body: [
        "Hi team,",
        "The artist approved the final radio edit, so you can send it to distribution and press.",
        "Thanks for turning this around quickly.",
      ],
      attachments: ["final-radio-edit.wav"],
    },
    {
      id: "main-25",
      sender: "Luna Grey",
      subject: "New single + possible campaign discussion",
      snippet: "Sharing a private stream, but also wondering whether there is interest in release planning and rollout support.",
      time: "Sun",
      unread: true,
      signal: "For review",
      from: "lunagrey.music@gmail.com",
      to: "main@hysteriarecs.com",
      timestamp: "Sunday, 16:28",
      internalClassification: "unknown",
      categorySource: "system",
      categoryConfidence: "low",
      suggestion: {
        type: "confirm_category",
        proposedCategory: "Primary",
      },
      body: [
        "Hi Hysteria team,",
        "I am sharing a private stream of my new single in case it fits what you are looking for on the demo side.",
        "At the same time, I would love to understand whether there could also be a release conversation or rollout support if the song connects with you.",
        "Happy to send campaign ideas, visual references, and the full asset pack if that helps.",
      ],
      attachments: ["private-stream-link.txt", "moodboard.pdf"],
    },
  ],
  promo: [
    {
      id: "promo-1",
      sender: "Release Radar",
      subject: "Friday release reminder and promo timing",
      snippet: "The campaign reminder is ready and the next send window looks optimal.",
      time: "10:12",
      unread: true,
      signal: "Timing",
      from: "workflow@hysteriarecs.com",
      to: "promo@hysteriarecs.com",
      timestamp: "Today, 10:12",
      body: [
        "Hi team,",
        "The Friday release reminder is now staged and the outreach timing is lined up for the next quiet window.",
        "Review the suggested send sequence before final approval.",
      ],
      attachments: ["artwork.jpg", "promo-copy.docx"],
    },
    {
      id: "promo-2",
      sender: "Spotify Editorial",
      subject: "Need final release metadata",
      snippet: "We can lock the pitch once the final metadata lands on our side.",
      time: "08:34",
      signal: "Follow-up",
      from: "editorial@spotify.com",
      to: "promo@hysteriarecs.com",
      timestamp: "Today, 08:34",
      isShared: true,
      sharedContext: {
        reason: "multiple_viewers",
      },
      body: [
        "Hello,",
        "We are ready to lock the editorial pitch, but still need the final release metadata and asset confirmation.",
      ],
      attachments: ["metadata-sheet.csv"],
    },
    {
      id: "promo-3",
      sender: "Press Contact",
      subject: "Short feature placement opportunity",
      snippet: "There is room for a short feature if the final press note is available today.",
      time: "Yesterday",
      internalClassification: "unknown",
      from: "editor@musicpress.com",
      to: "promo@hysteriarecs.com",
      timestamp: "Yesterday, 16:21",
      body: [
        "Hi,",
        "We still have room for a short feature placement if the updated press note is ready today.",
      ],
    },
    {
      id: "promo-3b",
      sender: "Mara Voss",
      subject: "Premiere request or early artist intro",
      snippet: "This could be a premiere pitch, but it also reads like an artist introduction with an unreleased track.",
      time: "Yesterday",
      unread: true,
      signal: "For review",
      from: "mara@nightdriveartist.com",
      to: "promo@hysteriarecs.com",
      timestamp: "Yesterday, 15:42",
      internalClassification: "unknown",
      categorySource: "system",
      categoryConfidence: "low",
      suggestion: {
        type: "confirm_category",
        proposedCategory: "Promo",
      },
      body: [
        "Hi there,",
        "I wanted to reach out about a possible premiere around my upcoming release, but I am also introducing myself properly because I have not sent music to your team before.",
        "If this is better treated as a demo conversation first, I am happy to send over a private link and more background.",
      ],
      attachments: ["press-note.docx"],
    },
    {
      id: "promo-4",
      sender: "Press Contact",
      subject: "Checking whether the press note is still coming",
      snippet: "Following up before we release the remaining feature space later today.",
      time: "Yesterday",
      internalClassification: "unknown",
      from: "editor@musicpress.com",
      to: "promo@hysteriarecs.com",
      timestamp: "Yesterday, 13:14",
      body: [
        "Hi,",
        "Checking whether the updated press note is still coming through today.",
        "We can hold the space a little longer if needed.",
      ],
    },
    {
      id: "promo-5",
      sender: "Press Contact",
      subject: "Last call before feature space closes",
      snippet: "One last nudge before we close the available slot on our side.",
      time: "Mon",
      internalClassification: "unknown",
      from: "editor@musicpress.com",
      to: "promo@hysteriarecs.com",
      timestamp: "Monday, 11:46",
      body: [
        "Hi,",
        "One last nudge before we close the available slot on our side.",
        "If the note is still relevant, send it over and we can review quickly.",
      ],
    },
  ],
  demo: [
    {
      id: "demo-1",
      sender: "MAVEZ",
      subject: "Private SoundCloud demo submission",
      snippet: "Sharing a new record that feels aligned with your recent late-night releases.",
      time: "14m",
      unread: true,
      signal: "For review",
      from: "mavez.artist@gmail.com",
      to: "demo@hysteriarecs.com",
      timestamp: "Today, 14 minutes ago",
      body: [
        "Hi Hysteria team,",
        "I wanted to share a new unreleased record that feels close to the darker club direction in your recent catalog.",
        "Private link is below and I would love to hear whether it fits the label.",
      ],
      attachments: ["private-link.txt"],
    },
    {
      id: "demo-2",
      sender: "Noctra",
      subject: "Late-night club cut shortlist",
      snippet: "Following up on the shortlist note from last week with an updated version.",
      time: "53m",
      signal: "Shortlist",
      from: "noctra.music@gmail.com",
      to: "demo@hysteriarecs.com",
      timestamp: "Today, 53 minutes ago",
      body: [
        "Hey,",
        "Following up on the shortlist note from last week with a tightened second-pass version.",
        "Would love to know if this should stay in the running.",
      ],
      attachments: ["private-link.txt", "version-notes.pdf"],
    },
    {
      id: "demo-3",
      sender: "Saffron Waves",
      subject: "New melodic house submission",
      snippet: "Sending over a melodic record that may fit your quieter Friday lane.",
      time: "Yesterday",
      from: "saffronwaves@gmail.com",
      to: "demo@hysteriarecs.com",
      timestamp: "Yesterday, 17:02",
      body: [
        "Hi,",
        "Please find my latest melodic house demo below. I think it could work well for your quieter Friday lane.",
      ],
    },
    {
      id: "demo-4",
      sender: "Kite Choir",
      subject: "Demo submission with quick feedback request",
      snippet: "Sending a new track for consideration, but also asking whether the team would share a steer if it is not a fit.",
      time: "Yesterday",
      unread: true,
      signal: "For review",
      from: "kitechoir.music@gmail.com",
      to: "demo@hysteriarecs.com",
      timestamp: "Yesterday, 14:11",
      internalClassification: "unknown",
      categorySource: "system",
      categoryConfidence: "low",
      suggestion: {
        type: "confirm_category",
        proposedCategory: "Primary",
      },
      body: [
        "Hi Hysteria team,",
        "I am sending over a new demo that I think could fit your lane, but I would also really value a quick steer if it is better aimed elsewhere.",
        "If there is any interest I can send stems, alt versions, and a more complete release outline.",
      ],
      attachments: ["private-link.txt"],
    },
  ],
  business: [
    {
      id: "business-1",
      sender: "Universal Music",
      subject: "Contract-linked review note and approval timing",
      snippet: "We need confirmation on the linked review note before legal can close this.",
      time: "32m",
      unread: true,
      signal: "Priority",
      from: "contracts@universalmusic.com",
      to: "rutger@hysteriarecs.com",
      timestamp: "Today, 32 minutes ago",
      body: [
        "Hi Rutger,",
        "We need confirmation on the linked review note before the legal side can close the current agreement revision.",
        "Can you review the note and come back with approval timing today?",
      ],
      attachments: ["contract-v2.pdf", "review-note.pdf"],
    },
    {
      id: "business-2",
      sender: "Collection Society",
      subject: "Statement reconciliation follow-up",
      snippet: "There are three items still open in the latest quarterly statement.",
      time: "2h",
      signal: "Finance",
      from: "support@collectionsociety.eu",
      to: "rutger@hysteriarecs.com",
      timestamp: "Today, 2 hours ago",
      body: [
        "Hello,",
        "Three items remain open in the latest statement reconciliation. We flagged the exact rows in the attachment.",
      ],
      attachments: ["statement-reconciliation.xlsx"],
    },
    {
      id: "business-3",
      sender: "Lawyer",
      subject: "Rights clarification needed on artwork",
      snippet: "Before publishing, we need confirmation that the artwork usage is cleared.",
      time: "Yesterday",
      from: "counsel@labellegal.nl",
      to: "rutger@hysteriarecs.com",
      timestamp: "Yesterday, 13:48",
      body: [
        "Hi,",
        "Before publication we need a final confirmation that the artwork usage is cleared across all territories.",
      ],
      attachments: ["artwork.jpg"],
    },
  ],
  legal: [],
  finance: [],
  royalty: [],
  sync: [],
};

const targetContent: Record<
  WorkspaceTarget,
  {
    eyebrow: string;
    title: string;
    summary: string;
    status: string;
    owner: string;
    primaryAction: string;
    secondaryAction: string;
    highlights: string[];
    relatedItems: string[];
    nextStep: string;
  }
> = {
  "priority-queue": {
    eyebrow: "Priority queue",
    title: "Priority items needing attention",
    summary:
      "Filtered priority workspace with the most urgent business, promo, and reply items surfaced first.",
    status: "12 items active",
    owner: "Priority routing",
    primaryAction: "Open queue",
    secondaryAction: "Open next",
    highlights: [
      "Urgent items are ranked by timing and business impact",
      "Priority inbox traffic is mixed with active conversation risk",
      "Routing rules already grouped the highest-signal threads",
    ],
    relatedItems: [
      "Universal Music contract update",
      "MAVEZ demo submission",
      "Replies pending — Armada",
    ],
    nextStep: "Work through the queue from highest priority to lowest without leaving the workspace.",
  },
  "demo-review": {
    eyebrow: "Item detail",
    title: "MAVEZ demo submission",
    summary:
      "Private SoundCloud demo from the submissions inbox with a strong fit against recent accepted records.",
    status: "Ready now",
    owner: "Demo inbox",
    primaryAction: "Open details",
    secondaryAction: "Queue note",
    highlights: [
      "Private link verified and playable",
      "Genre match confidence is high",
      "Artist profile resolved from prior submissions",
    ],
    relatedItems: [
      "Audio preview attached to the item record",
      "Prior MAVEZ submission history available",
      "Recommended feedback template prepared",
    ],
    nextStep: "Inspect the track and decide whether to shortlist it for follow-up.",
  },
  "late-night-review": {
    eyebrow: "Item detail",
    title: "Late-night club cut shortlist",
    summary:
      "Shortlisted club cut in second-pass evaluation with strong after-hours potential and a pending decision from the current queue.",
    status: "Needs decision",
    owner: "A&R shortlist",
    primaryAction: "Open details",
    secondaryAction: "Compare shortlist",
    highlights: [
      "Shortlist status was applied after first-pass screening",
      "Energy profile fits late-night club programming",
      "A&R requested a decision in the current cycle",
    ],
    relatedItems: [
      "Shortlist notes from first pass are attached",
      "Comparable club references are already linked",
      "Decision window remains open for this round",
    ],
    nextStep: "Inspect the shortlisted material and decide whether it advances beyond second pass.",
  },
  "contract-review-note": {
    eyebrow: "Item detail",
    title: "Contract-linked note",
    summary:
      "Linked item connected to an active business thread, combining approval context with follow-up notes that affect the next decision.",
    status: "Blocked",
    owner: "Business team",
    primaryAction: "Open linked note",
    secondaryAction: "Inspect contract context",
    highlights: [
      "Business follow-up is attached to the current item",
      "Approval timing depends on linked contract questions",
      "Status remains blocked until thread context is cleared",
    ],
    relatedItems: [
      "Universal contract thread remains active",
      "Linked business note is part of the item record",
      "Next approval step depends on contract clarification",
    ],
    nextStep: "Inspect the linked note and resolve the business dependency before moving forward.",
  },
  "release-copy-review": {
    eyebrow: "Item detail",
    title: "Release copy timing",
    summary:
      "Queued item for release copy timing, kept visible until the editorial checkpoint is ready.",
    status: "Queued",
    owner: "Promo team",
    primaryAction: "Open details",
    secondaryAction: "Queue note",
    highlights: [
      "Feedback remains queued in the current cycle",
      "Release timing still depends on a coordinated editorial check",
      "The item remains visible until it is reopened or resolved",
    ],
    relatedItems: ["Queued release feedback note", "Editorial checkpoint", "Campaign timing note"],
    nextStep: "Reopen when the release copy is ready for a final decision.",
  },
  "royalty-approval-review": {
    eyebrow: "Item detail",
    title: "Royalty approval dependency",
    summary:
      "Resolved approval record that remains available as a read-only history trail for finance context.",
    status: "Resolved",
    owner: "Finance team",
    primaryAction: "Open details",
    secondaryAction: "Queue note",
    highlights: [
      "Finance confirmed the split schedule",
      "Approval dependency was closed without escalation",
      "The item remains available as a stable audit record",
    ],
    relatedItems: ["Resolved approval dependency", "Finance confirmation", "Royalty schedule context"],
    nextStep: "No further action is required unless the item is reopened.",
  },
  "contract-thread": {
    eyebrow: "Thread detail",
    title: "Universal Music contract update",
    summary:
      "Priority business thread with a revised agreement attached and open questions still waiting on response.",
    status: "Needs thread check",
    owner: "Business inbox",
    primaryAction: "Open thread",
    secondaryAction: "Draft reply",
    highlights: [
      "Latest message contains updated contract terms",
      "Counterparty requested confirmation this afternoon",
      "Attachment was flagged as business critical",
    ],
    relatedItems: [
      "Latest reply draft is available",
      "Contract PDF linked to the thread",
      "Business urgency elevated by deadline timing",
    ],
    nextStep: "Inspect the thread details and respond to the outstanding contract questions.",
  },
  "promo-context": {
    eyebrow: "Release context",
    title: "Release Friday promo reminder",
    summary:
      "Upcoming release activity with timing-sensitive context, planned outreach, and pending promotional checkpoints.",
    status: "Context ready",
    owner: "Promo workflow",
    primaryAction: "Open release context",
    secondaryAction: "View schedule",
    highlights: [
      "Reminder linked to Friday release window",
      "Promo follow-up draft is already prepared",
      "Key timing signals were detected from recent activity",
    ],
    relatedItems: [
      "Release campaign timeline is attached",
      "Promo reminder was auto-generated from workflow rules",
      "Follow-up checkpoints are already suggested",
    ],
    nextStep: "Inspect the release context before scheduling or sending the next promo step.",
  },
  "reply-conversation": {
    eyebrow: "Conversation detail",
    title: "Armada reply conversation",
    summary:
      "Active conversation with a pending reply and recent context that should be checked before responding.",
    status: "Reply pending",
    owner: "Replies queue",
    primaryAction: "Open conversation",
    secondaryAction: "Prepare response",
    highlights: [
      "Conversation has been active for the last two hours",
      "Recent message indicates the thread is still live",
      "Reply assistant already identified likely next steps",
    ],
    relatedItems: [
      "Pending draft is ready for editing",
      "Conversation summary is up to date",
      "Last participant response is highlighted",
    ],
    nextStep: "Open the conversation and send or refine the pending reply.",
  },
  "universal-reply": {
    eyebrow: "Reply detail",
    title: "Universal thread reply detail",
    summary:
      "Reply-focused conversation surface for the Universal thread, with the active thread context and next response step in view.",
    status: "Reply suggested",
    owner: "Universal thread",
    primaryAction: "Open reply",
    secondaryAction: "Refine draft",
    highlights: [
      "Conversation context is active for the current thread",
      "A reply draft is ready for inspection and adjustment",
      "Recent messages are prioritized for fast response handling",
    ],
    relatedItems: [
      "Universal thread still waiting on response",
      "Latest reply suggestion is available",
      "Conversation timing remains active",
    ],
    nextStep: "Inspect the thread context and send or refine the suggested reply.",
  },
};

function CuevionMark({ compact = false }: { compact?: boolean }) {
  return (
    <div className="inline-flex items-center gap-3 text-[var(--workspace-sidebar-text)]">
      <span
        aria-hidden="true"
        className="flex h-8 w-8 items-center justify-center rounded-full border border-[var(--workspace-shell-border)] bg-[var(--workspace-sidebar-hover)]"
      >
        <span className="h-2.5 w-2.5 rounded-full bg-[var(--workspace-sidebar-text)]" />
      </span>
      {!compact ? (
        <span className="text-[1.15rem] font-semibold tracking-[0.03em]">
          Cuevion
        </span>
      ) : null}
    </div>
  );
}

function WorkspaceSidebar({
  activeSection,
  activeMailboxId,
  orderedMailboxes,
  onChangeSection,
  onOpenMailbox,
}: {
  activeSection: WorkspaceSection;
  activeMailboxId: InboxId | null;
  orderedMailboxes: OrderedMailbox[];
  onChangeSection: (view: WorkspaceSection) => void;
  onOpenMailbox: (mailbox: OrderedMailbox) => void;
}) {
  const [isInboxesOpen, setIsInboxesOpen] = useState(false);
  const hasMultipleMailboxes = orderedMailboxes.length > 1;
  const singleMailbox = !hasMultipleMailboxes ? (orderedMailboxes[0] ?? null) : null;
  const activeSidebarInboxId = activeSection === "Inboxes" ? activeMailboxId : null;
  const shouldShowInboxChildren = hasMultipleMailboxes && isInboxesOpen;
  const inboxSidebarItems = useMemo(
    () =>
      orderedMailboxes.map((mailbox) => ({
        id: mailbox.id,
        label: mailbox.title,
      })),
    [orderedMailboxes],
  );

  useEffect(() => {
    if (activeSidebarInboxId !== null) {
      setIsInboxesOpen(true);
    }
  }, [activeSidebarInboxId]);

  const renderItem = (item: {
    section: WorkspaceSection;
    label: string;
    shortLabel: string;
  }) => {
    const isInboxesItem = item.section === "Inboxes";
    const active = item.section === activeSection;

    if (isInboxesItem) {
      if (hasMultipleMailboxes) {
        return (
          <li key={item.label}>
            <button
              type="button"
              onClick={() => setIsInboxesOpen((current) => !current)}
              className={`flex w-full items-center justify-center rounded-2xl px-3 py-3 text-center text-sm font-medium transition-[background-color,color,box-shadow] duration-100 focus:outline-none focus-visible:shadow-[inset_0_0_0_1px_rgba(255,255,255,0.12),0_0_0_1px_rgba(214,230,221,0.16)] xl:justify-start xl:px-4 xl:text-left ${
                active
                  ? "bg-[linear-gradient(180deg,var(--workspace-sidebar-active-start),var(--workspace-sidebar-active-end))] text-[var(--workspace-sidebar-text)]"
                  : "text-[var(--workspace-sidebar-text-muted)] hover:bg-[var(--workspace-sidebar-hover)] hover:text-[var(--workspace-sidebar-text)]"
              }`}
              aria-label={item.label}
              aria-expanded={isInboxesOpen}
            >
              <span className="hidden xl:inline">{item.label}</span>
              <span
                className={`ml-2 hidden xl:inline-flex transition-transform duration-150 ${
                  isInboxesOpen ? "rotate-90" : "rotate-0"
                }`}
                aria-hidden="true"
              >
                <svg
                  viewBox="0 0 12 12"
                  className="h-3 w-3"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M4 2.5 7.5 6 4 9.5" />
                </svg>
              </span>
              <span className="text-[11px] uppercase tracking-[0.18em] xl:hidden">
                {item.shortLabel}
              </span>
            </button>
            {shouldShowInboxChildren ? (
              <ul className="mt-2 space-y-2">
                {inboxSidebarItems.map((mailbox) => {
                  const activeInbox = mailbox.id === activeSidebarInboxId;

                  return (
                    <li key={mailbox.id}>
                      <button
                        type="button"
                        onClick={() => {
                          const targetMailbox =
                            orderedMailboxes.find((candidate) => candidate.id === mailbox.id) ?? null;

                          if (!targetMailbox) {
                            return;
                          }

                          onOpenMailbox(targetMailbox);
                        }}
                        className={`hidden w-full rounded-2xl px-4 py-3 text-left text-sm font-medium transition-[background-color,color,box-shadow] duration-100 focus:outline-none focus-visible:shadow-[inset_0_0_0_1px_rgba(255,255,255,0.12),0_0_0_1px_rgba(214,230,221,0.16)] xl:block ${
                          activeInbox
                            ? "bg-[linear-gradient(180deg,var(--workspace-sidebar-active-start),var(--workspace-sidebar-active-end))] text-[var(--workspace-sidebar-text)]"
                            : "text-[var(--workspace-sidebar-text-muted)] hover:bg-[var(--workspace-sidebar-hover)] hover:text-[var(--workspace-sidebar-text)]"
                        }`}
                        aria-label={mailbox.label}
                      >
                        <span className="block pl-4">{mailbox.label}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : null}
          </li>
        );
      }

      return (
        <li key={item.label} className="pt-2">
          <div className="hidden items-center justify-between px-4 pb-2 xl:flex">
            <span className="text-[0.68rem] font-medium uppercase tracking-[0.18em] text-[var(--workspace-sidebar-text-muted)]">
              {item.label}
            </span>
          </div>
          {singleMailbox ? (
            <ul className="mt-2 space-y-2">
              <li>
                <button
                  type="button"
                  onClick={() => onOpenMailbox(singleMailbox)}
                  className={`hidden w-full rounded-2xl px-4 py-3 text-left text-sm font-medium transition-[background-color,color,box-shadow] duration-100 focus:outline-none focus-visible:shadow-[inset_0_0_0_1px_rgba(255,255,255,0.12),0_0_0_1px_rgba(214,230,221,0.16)] xl:block ${
                    activeSidebarInboxId === singleMailbox.id
                      ? "bg-[linear-gradient(180deg,var(--workspace-sidebar-active-start),var(--workspace-sidebar-active-end))] text-[var(--workspace-sidebar-text)]"
                      : "text-[var(--workspace-sidebar-text-muted)] hover:bg-[var(--workspace-sidebar-hover)] hover:text-[var(--workspace-sidebar-text)]"
                  }`}
                  aria-label={singleMailbox.title}
                >
                  <span className="block pl-4">{singleMailbox.title}</span>
                </button>
              </li>
            </ul>
          ) : shouldShowInboxChildren ? (
            <ul className="mt-2 space-y-2">
              {inboxSidebarItems.map((mailbox) => {
                const activeInbox = mailbox.id === activeSidebarInboxId;

                return (
                  <li key={mailbox.id}>
                    <button
                      type="button"
                      onClick={() => {
                        const targetMailbox =
                          orderedMailboxes.find((candidate) => candidate.id === mailbox.id) ?? null;

                        if (!targetMailbox) {
                          return;
                        }

                        onOpenMailbox(targetMailbox);
                      }}
                      className={`hidden w-full rounded-2xl px-4 py-3 text-left text-sm font-medium transition-[background-color,color,box-shadow] duration-100 focus:outline-none focus-visible:shadow-[inset_0_0_0_1px_rgba(255,255,255,0.12),0_0_0_1px_rgba(214,230,221,0.16)] xl:block ${
                        activeInbox
                          ? "bg-[linear-gradient(180deg,var(--workspace-sidebar-active-start),var(--workspace-sidebar-active-end))] text-[var(--workspace-sidebar-text)]"
                          : "text-[var(--workspace-sidebar-text-muted)] hover:bg-[var(--workspace-sidebar-hover)] hover:text-[var(--workspace-sidebar-text)]"
                      }`}
                      aria-label={mailbox.label}
                    >
                      <span className="block pl-4">{mailbox.label}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : null}
        </li>
      );
    }

    return (
      <li key={item.label}>
        <button
          type="button"
          onClick={() => {
            onChangeSection(item.section);
          }}
          className={`flex w-full items-center justify-center rounded-2xl px-3 py-3 text-center text-sm font-medium transition-[background-color,color,box-shadow] duration-100 focus:outline-none focus-visible:shadow-[inset_0_0_0_1px_rgba(255,255,255,0.12),0_0_0_1px_rgba(214,230,221,0.16)] xl:justify-start xl:px-4 xl:text-left ${
            active
              ? "bg-[linear-gradient(180deg,var(--workspace-sidebar-active-start),var(--workspace-sidebar-active-end))] text-[var(--workspace-sidebar-text)]"
              : "text-[var(--workspace-sidebar-text-muted)] hover:bg-[var(--workspace-sidebar-hover)] hover:text-[var(--workspace-sidebar-text)]"
          }`}
          aria-label={item.label}
        >
          <span className="hidden xl:inline">{item.label}</span>
          {item.section === "Team" ? (
            <span className="ml-2 hidden xl:inline-flex">
              <span className="h-[7px] w-[7px] rounded-full bg-[radial-gradient(circle_at_30%_30%,#DCCFBB_0%,#D2C2A8_48%,#C3B091_100%)] shadow-[0_0_5px_rgba(210,194,168,0.28)]" />
            </span>
          ) : null}
          <span className="text-[11px] uppercase tracking-[0.18em] xl:hidden">
            {item.shortLabel}
          </span>
        </button>
      </li>
    );
  };

  return (
    <aside className="fixed inset-y-0 left-0 hidden overflow-y-auto w-24 border-r border-[var(--workspace-sidebar-border)] bg-[var(--workspace-sidebar)] px-4 py-10 text-[var(--workspace-sidebar-text)] md:block xl:w-[320px] xl:px-8">
      <div className="absolute inset-0 bg-[var(--workspace-sidebar-glow)]" />
      <div className="relative flex h-full flex-col">
        <div className="flex h-full flex-col">
          <span className="hidden rounded-full border border-[var(--workspace-sidebar-border)] bg-[var(--workspace-sidebar-hover)] px-4 py-2 text-xs uppercase tracking-[0.28em] text-[var(--workspace-sidebar-text-muted)] xl:inline-flex">
            Workspace
          </span>
          <div className="mt-8 flex justify-center xl:justify-start">
            <div className="xl:hidden">
              <CuevionMark compact />
            </div>
            <div className="hidden xl:block">
              <CuevionMark />
            </div>
          </div>
          <nav aria-label="Workspace navigation" className="mt-8 flex flex-1 flex-col">
            <ul className="space-y-2">{primaryNavigationItems.map(renderItem)}</ul>
            <div className="mt-auto pt-8">
              <ul className="space-y-2">{utilityNavigationItems.map(renderItem)}</ul>
              <div className="pt-5 text-center text-[0.68rem] font-medium tracking-[0.05em] text-[color:rgba(146,122,98,0.78)] xl:px-4 xl:text-left">
                Version 1.0.0
              </div>
            </div>
          </nav>
        </div>
      </div>
    </aside>
  );
}

function TopCards({
  onOpenPriority,
  onOpenNewEmails,
  onOpenInboxes,
  primaryInboxTitle,
  primaryInboxEmailCount,
  priorityInboxCount,
  connectedInboxCount,
}: {
  onOpenPriority: () => void;
  onOpenNewEmails: () => void;
  onOpenInboxes: () => void;
  primaryInboxTitle: string;
  primaryInboxEmailCount: number;
  priorityInboxCount: number;
  connectedInboxCount: number;
}) {
  const cards = [
    {
      label: "Priority",
      value: String(priorityInboxCount),
      context:
        priorityInboxCount > 0
          ? `${priorityInboxCount} item${priorityInboxCount === 1 ? "" : "s"} ready in Inbox`
          : "No items yet",
      actionLabel: "Open queue",
      onClick: onOpenPriority,
    },
    {
      label: "New Emails",
      value: String(primaryInboxEmailCount),
      context: `Fresh messages ready in ${primaryInboxTitle}`,
      actionLabel: "Open inbox",
      onClick: onOpenNewEmails,
    },
    {
      label: "Connected Inboxes",
      value: String(connectedInboxCount),
      context: "All systems connected",
      actionLabel: "Open inboxes",
      onClick: onOpenInboxes,
    },
  ];

  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {cards.map((card) => (
        <button
          key={card.label}
          type="button"
          onClick={card.onClick}
          className="cursor-pointer rounded-[28px] border border-[var(--workspace-border)] bg-[linear-gradient(180deg,var(--workspace-card-featured-start),var(--workspace-card-featured-end))] px-5 py-4 text-left shadow-panel transition-[background-image,border-color,transform] duration-150 hover:border-[var(--workspace-border-hover)] hover:bg-[linear-gradient(180deg,var(--workspace-card-featured-hover-start),var(--workspace-card-featured-hover-end))]"
          aria-label={`${card.actionLabel}: ${card.label}`}
        >
          <div className="space-y-4">
            <div className="text-[10px] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
              {card.label}
            </div>
            <div className="text-[2.25rem] font-semibold leading-none tracking-[-0.03em] text-[var(--workspace-text)]">
              {card.value}
            </div>
            <p className="max-w-[14rem] text-sm leading-6 text-[var(--workspace-text-soft)]">
              {card.context}
            </p>
            <div className="text-[0.66rem] font-medium uppercase tracking-[0.08em] text-[var(--workspace-text-faint)]">
              {card.actionLabel}
            </div>
          </div>
        </button>
      ))}
    </div>
  );
}

function buildVisibleNotificationItems({
  teamActivityEnabled,
  onOpenNotificationNavigation,
}: {
  teamActivityEnabled: boolean;
  onOpenNotificationNavigation: (
    request: Omit<NotificationNavigationRequest, "requestKey">,
  ) => void;
}) {
  const notificationItems = [
    {
      eventType: "collaboration_invite_received" as const,
      category: "team-activity" as const,
      title: "You were invited to a collaboration",
      detail: "Emma invited you to inspect DSP delivery note",
      time: "NOW",
      action: () =>
        onOpenNotificationNavigation({
          type: "invite",
          mailboxId: "main",
          messageId: "main-5",
          inviteeEmail: "david@cuevion.com",
        }),
    },
    {
      eventType: "collaboration_reply_received" as const,
      category: "team-activity" as const,
      title: "Emma replied",
      detail: "DSP delivery note",
      time: "7 MIN AGO",
      action: () =>
        onOpenNotificationNavigation({
          type: "reply",
          mailboxId: "main",
          messageId: "main-5",
        }),
    },
    {
      eventType: "collaboration_mention_received" as const,
      category: "team-activity" as const,
      title: "You were mentioned by David",
      detail: "Artwork confirmation for vinyl repress",
      time: "12 MIN AGO",
      action: () =>
        onOpenNotificationNavigation({
          type: "mention",
          mailboxId: "main",
          messageId: "main-18",
          collaborationMessageId: "main-18-collaboration-mention",
        }),
    },
  ];

  return notificationItems.filter((item, index, items) => {
    if (item.category === "team-activity" && !teamActivityEnabled) {
      return false;
    }

    const dedupeKey = `${item.title}:${item.detail}`;

    return (
      items.findIndex(
        (candidate) =>
          `${candidate.title}:${candidate.detail}` === dedupeKey,
      ) === index
    );
  });
}

function NotificationsPreviewBlock({
  items,
}: {
  items: ReturnType<typeof buildVisibleNotificationItems>;
}) {
  return (
    <section className="rounded-[30px] border border-[var(--workspace-border)] bg-[var(--workspace-card)] p-6 shadow-panel">
      <div className="mb-5 flex items-center justify-between">
        <h2 className="text-xl font-semibold tracking-tight text-[var(--workspace-text)]">
          Notifications
        </h2>
        <div className="h-2 w-14 rounded-full bg-[var(--workspace-accent-soft)]" />
      </div>
      {items.length > 0 ? (
        <div className="space-y-2.5">
          {items.slice(0, 5).map((item) => (
            <button
              key={`${item.title}-${item.time}`}
              type="button"
              onClick={item.action}
              className="flex w-full items-start justify-between gap-4 rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-3.5 text-left transition-[background-color,background-image,border-color,transform] duration-150 hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface)] focus-visible:border-[var(--workspace-border-hover)] focus-visible:bg-[linear-gradient(180deg,var(--workspace-card-featured-start),var(--workspace-card-featured-end))] focus-visible:outline-none"
            >
              <div className="min-w-0 space-y-1">
                <div className="truncate text-[0.95rem] font-medium tracking-[-0.012em] text-[var(--workspace-text)]">
                  {item.title}
                </div>
                <div className="truncate text-[0.82rem] leading-6 text-[var(--workspace-text-soft)]">
                  {item.detail}
                </div>
              </div>
              <div className="flex-none pt-0.5 text-[0.66rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-text-faint)]">
                {item.time}
              </div>
            </button>
          ))}
        </div>
      ) : (
        <div className="rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-6 text-[0.88rem] leading-7 text-[var(--workspace-text-soft)]">
          No notifications yet.
        </div>
      )}
    </section>
  );
}

function ContentBlock({
  title,
  showDemoContent,
  onOpenPriority,
  onOpenInboxes,
  onOpenForYou,
  onOpenConversation,
}: {
  title: string;
  showDemoContent: boolean;
  onOpenPriority: () => void;
  onOpenInboxes: () => void;
  onOpenForYou: () => void;
  onOpenConversation: () => void;
}) {
  const items = showDemoContent
    ? [
        {
          title: "Armada inbox synced successfully",
          detail: "2 new messages categorized",
          actionLabel: "Open inboxes",
          onClick: onOpenInboxes,
        },
        {
          title: "Demo submission auto-tagged",
          detail: "Artist profile matched successfully",
          actionLabel: "Open priority",
          onClick: onOpenPriority,
        },
        {
          title: "Reminder triggered for Friday release",
          detail: "Promo follow-up prepared",
          actionLabel: "Open For You",
          onClick: onOpenForYou,
        },
        {
          title: "Universal thread updated",
          detail: "Reply detected in active conversation",
          actionLabel: "Open conversation",
          onClick: onOpenConversation,
        },
      ]
    : [];

  return (
    <section className="rounded-[30px] border border-[var(--workspace-border)] bg-[var(--workspace-card)] p-6 shadow-panel">
      <div className="mb-5 flex items-center justify-between">
        <h2 className="text-xl font-semibold tracking-tight text-[var(--workspace-text)]">
          {title}
        </h2>
        <div className="h-2 w-14 rounded-full bg-[var(--workspace-accent-soft)]" />
      </div>
      {items.length > 0 ? (
        <div className="space-y-2.5">
          {items.map((item) => (
            <button
              key={item.title}
              type="button"
              onClick={item.onClick}
              className="w-full cursor-pointer rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-3.5 text-left transition-[background-color,background-image,border-color,transform] duration-150 hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface)] focus-visible:border-[var(--workspace-border-hover)] focus-visible:bg-[linear-gradient(180deg,var(--workspace-card-featured-start),var(--workspace-card-featured-end))] focus-visible:outline-none"
              aria-label={`${item.actionLabel}: ${item.title}`}
            >
              <div className="space-y-1.5">
                <div className="text-[0.95rem] font-medium tracking-[-0.012em] text-[var(--workspace-text-soft)]">
                  {item.title}
                </div>
                <div className="text-[0.84rem] leading-6 text-[var(--workspace-text-faint)]">
                  {item.detail}
                </div>
                <div className="text-[0.66rem] font-medium uppercase tracking-[0.08em] text-[var(--workspace-text-faint)]">
                  {item.actionLabel}
                </div>
              </div>
            </button>
          ))}
        </div>
      ) : (
        <div className="rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-6 text-[0.88rem] leading-7 text-[var(--workspace-text-soft)]">
          Nothing new yet.
        </div>
      )}
    </section>
  );
}

function DashboardView({
  onOpenPriority,
  onOpenPrimaryInbox,
  onOpenInboxes,
  onOpenForYou,
  onOpenNotificationNavigation,
  teamActivityEnabled,
  primaryInboxTitle,
  primaryInboxEmailCount,
  priorityInboxCount,
  connectedInboxCount,
  showDemoContent,
}: {
  onOpenPriority: () => void;
  onOpenPrimaryInbox: () => void;
  onOpenInboxes: () => void;
  onOpenForYou: () => void;
  onOpenNotificationNavigation: (
    request: Omit<NotificationNavigationRequest, "requestKey">,
  ) => void;
  teamActivityEnabled: boolean;
  primaryInboxTitle: string;
  primaryInboxEmailCount: number;
  priorityInboxCount: number;
  connectedInboxCount: number;
  showDemoContent: boolean;
}) {
  const userName: string | null = null;
  const currentHour = new Date().getHours();
  const dayPeriod: "morning" | "afternoon" | "evening" =
  currentHour < 12 ? "morning" : currentHour < 18 ? "afternoon" : "evening";
  const greetingLabel = {
  morning: "Good morning",
  afternoon: "Good afternoon",
  evening: "Good evening",
}[dayPeriod];
  const greeting = userName ? `${greetingLabel}, ${userName}` : greetingLabel;
  const notificationPreviewItems = showDemoContent
    ? buildVisibleNotificationItems({
        teamActivityEnabled,
        onOpenNotificationNavigation,
      })
    : [];

  return (
    <div className="space-y-8">
      <header className="space-y-3">
        <h1 className="text-[1.85rem] font-medium tracking-tight text-[var(--workspace-text)] md:text-[2.25rem]">
          {greeting}
        </h1>
        <p className="text-lg leading-8 text-[var(--workspace-text-muted)]">
          Here&apos;s what needs attention today
        </p>
      </header>

      <TopCards
        onOpenPriority={onOpenPriority}
        onOpenNewEmails={onOpenPrimaryInbox}
        onOpenInboxes={onOpenInboxes}
        primaryInboxTitle={primaryInboxTitle}
        primaryInboxEmailCount={primaryInboxEmailCount}
        priorityInboxCount={priorityInboxCount}
        connectedInboxCount={connectedInboxCount}
      />

      <div className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
        <NotificationsPreviewBlock items={notificationPreviewItems} />
        <ContentBlock
          title="Recent updates"
          showDemoContent={showDemoContent}
          onOpenPriority={onOpenPriority}
          onOpenInboxes={onOpenInboxes}
          onOpenForYou={onOpenForYou}
          onOpenConversation={onOpenInboxes}
        />
      </div>
    </div>
  );
}

function WorkspaceTargetView({
  target,
  onBack,
  themeMode,
}: {
  target: WorkspaceTarget;
  onBack: () => void;
  themeMode: "light" | "dark";
}) {
  const content = targetContent[target];

  return (
    <div className="space-y-8">
      <div className="flex items-center gap-4">
        <button
          type="button"
          onClick={onBack}
          className={settingsPrimaryActionClass}
        >
          Back
        </button>
      </div>

      <section className="rounded-[30px] border border-[var(--workspace-border)] bg-[linear-gradient(180deg,var(--workspace-card-featured-start),var(--workspace-card-featured-end))] p-6 shadow-panel md:p-7">
        <div className="space-y-4">
          <div className="text-[0.72rem] font-medium uppercase tracking-[0.24em] text-[var(--workspace-text-faint)]">
            {content.eyebrow}
          </div>
          <div className="space-y-3">
            <h1 className="text-[1.8rem] font-medium tracking-tight text-[var(--workspace-text)] md:text-[2.15rem]">
              {content.title}
            </h1>
            <p className="max-w-3xl text-[1rem] leading-7 text-[var(--workspace-text-muted)]">
              {content.summary}
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              className="rounded-full bg-pine px-5 py-3 text-[0.72rem] font-medium uppercase tracking-[0.18em] text-white transition-[background-color,transform] duration-150 hover:bg-moss active:scale-[0.99] focus-visible:outline-none"
            >
              {content.primaryAction}
            </button>
            <button
              type="button"
              className="rounded-full border border-[var(--workspace-border)] bg-[var(--workspace-card)] px-5 py-3 text-[0.72rem] font-medium uppercase tracking-[0.18em] text-[var(--workspace-text-soft)] transition-[background-color,border-color,color,transform] duration-150 hover:border-[var(--workspace-border-hover)] hover:bg-[var(--workspace-hover-surface-strong)] active:scale-[0.99] focus-visible:outline-none"
            >
              {content.secondaryAction}
            </button>
          </div>
        </div>
      </section>

      <div className="grid gap-6 xl:grid-cols-[0.82fr_1.18fr]">
        <section className="rounded-[30px] border border-[var(--workspace-border)] bg-[var(--workspace-card)] p-6 shadow-panel">
          <div className="mb-5 flex items-center justify-between">
            <h2 className="text-xl font-semibold tracking-tight text-[var(--workspace-text)]">
              Current status
            </h2>
            <div className="h-2 w-14 rounded-full bg-[var(--workspace-accent-soft)]" />
          </div>
          <div className="space-y-3">
            <div className="rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-3.5">
              <div className="text-[0.7rem] font-medium uppercase tracking-[0.18em] text-[var(--workspace-text-faint)]">
                Status
              </div>
              <div className="mt-2 text-[1rem] font-medium tracking-[-0.012em] text-[var(--workspace-text)]">
                {content.status}
              </div>
            </div>
            <div className="rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-3.5">
              <div className="text-[0.7rem] font-medium uppercase tracking-[0.18em] text-[var(--workspace-text-faint)]">
                Owner
              </div>
              <div className="mt-2 text-[1rem] font-medium tracking-[-0.012em] text-[var(--workspace-text)]">
                {content.owner}
              </div>
            </div>
            <div className="rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-3.5">
              <div className="text-[0.7rem] font-medium uppercase tracking-[0.18em] text-[var(--workspace-text-faint)]">
                Next step
              </div>
              <div className="mt-2 text-[0.92rem] leading-6 text-[var(--workspace-text-soft)]">
                {content.nextStep}
              </div>
            </div>
          </div>
        </section>

        <section className="rounded-[30px] border border-[var(--workspace-border)] bg-[var(--workspace-card)] p-6 shadow-panel">
          <div className="mb-5 flex items-center justify-between">
            <h2 className="text-xl font-semibold tracking-tight text-[var(--workspace-text)]">
              Highlights
            </h2>
            <div className="h-2 w-14 rounded-full bg-[var(--workspace-accent-soft)]" />
          </div>
          <div className="space-y-2.5">
            {content.highlights.map((highlight) => (
              <div
                key={highlight}
                className="rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-3.5"
              >
                <div className="text-[0.92rem] leading-6 text-[var(--workspace-text-soft)]">
                  {highlight}
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>

      <section className="rounded-[30px] border border-[var(--workspace-border)] bg-[var(--workspace-card)] p-6 shadow-panel">
        <div className="mb-5 flex items-center justify-between">
          <h2 className="text-xl font-semibold tracking-tight text-[var(--workspace-text)]">
            Related items
          </h2>
          <div className="h-2 w-14 rounded-full bg-[var(--workspace-accent-soft)]" />
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          {content.relatedItems.map((item) => (
            <div
              key={item}
              className="rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-3.5"
            >
              <div className="text-[0.9rem] leading-6 text-[var(--workspace-text-soft)]">
                {item}
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function ReviewView({
  filter,
  onOpenTarget,
}: {
  filter: ReviewFilter;
  onOpenTarget: (target: WorkspaceTarget) => void;
}) {
  const items = [
    {
      title: "MAVEZ — Demo Submission",
      detail: "Private SoundCloud demo link detected",
      state: "Ready now",
      target: "demo-review" as const,
    },
    {
      title: "Late-night club cut shortlist",
      detail: "Second-pass decision requested by A&R",
      state: "Needs decision",
      target: "late-night-review" as const,
    },
    {
      title: "Contract-linked note",
      detail: "Business follow-up attached to approval flow",
      state: "Blocked",
      target: "contract-review-note" as const,
    },
  ];

  return (
    <div className="space-y-8">
      <header className="space-y-3">
        <div className="text-[0.72rem] font-medium uppercase tracking-[0.24em] text-[var(--workspace-text-faint)]">
          Priority items
        </div>
        <h1 className="text-[1.85rem] font-medium tracking-tight text-[var(--workspace-text)] md:text-[2.25rem]">
          {filter}
        </h1>
        <p className="text-lg leading-8 text-[var(--workspace-text-muted)]">
          Priority queue with the relevant items already filtered into the current state.
        </p>
      </header>

      <section className="rounded-[30px] border border-[var(--workspace-border)] bg-[var(--workspace-card)] p-6 shadow-panel">
        <div className="space-y-3">
          {items.map((item) => (
            <button
              key={item.title}
              type="button"
              onClick={() => onOpenTarget(item.target)}
              className="grid w-full cursor-pointer grid-cols-[minmax(0,1.2fr)_minmax(0,0.9fr)_auto] items-center gap-4 rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-4 text-left transition-[background-color,background-image,border-color,transform] duration-150 hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface)] focus-visible:border-[var(--workspace-border-hover)] focus-visible:bg-[linear-gradient(180deg,var(--workspace-card-featured-start),var(--workspace-card-featured-end))] focus-visible:outline-none"
            >
              <div className="min-w-0">
                <div className="text-[1rem] font-medium tracking-[-0.014em] text-[var(--workspace-text)]">
                  {item.title}
                </div>
              </div>
              <div className="min-w-0 text-[0.88rem] leading-6 text-[var(--workspace-text-soft)]">
                {item.detail}
              </div>
              <div className="justify-self-end text-right">
                <div className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                  {item.state}
                </div>
              </div>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}

function InboxesView({
  filter,
  orderedMailboxes,
  onOpenMailbox,
}: {
  filter: InboxFilter;
  orderedMailboxes: OrderedMailbox[];
  onOpenMailbox: (mailbox: OrderedMailbox) => void;
}) {
  return (
    <div className="space-y-8">
      <header className="space-y-3">
        <div className="text-[0.72rem] font-medium uppercase tracking-[0.24em] text-[var(--workspace-text-faint)]">
          Inboxes
        </div>
        <h1 className="text-[1.85rem] font-medium tracking-tight text-[var(--workspace-text)] md:text-[2.25rem]">
          {filter}
        </h1>
        <p className="text-lg leading-8 text-[var(--workspace-text-muted)]">
          Connected inboxes with direct access to sync context, linked items, and active business threads.
        </p>
      </header>

      <section className="rounded-[30px] border border-[var(--workspace-border)] bg-[var(--workspace-card)] p-6 shadow-panel">
        <div className="space-y-3">
          {orderedMailboxes.map((inbox, index) => (
            <button
              key={inbox.id}
              type="button"
              onClick={() => onOpenMailbox(inbox)}
              className="grid w-full cursor-pointer grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_auto] items-center gap-4 rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-4 text-left transition-[background-color,background-image,border-color,transform] duration-150 hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface)] focus-visible:border-[var(--workspace-border-hover)] focus-visible:bg-[linear-gradient(180deg,var(--workspace-card-featured-start),var(--workspace-card-featured-end))] focus-visible:outline-none"
            >
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <div className="text-[1rem] font-medium tracking-[-0.014em] text-[var(--workspace-text)]">
                    {inbox.title}
                  </div>
                  {index === 0 ? (
                    <span className={primaryBadgeClass}>
                      Primary
                    </span>
                  ) : null}
                </div>
                <div className="text-[0.82rem] leading-6 text-[var(--workspace-text-faint)]">
                  {inbox.email}
                </div>
              </div>
              <div className="text-[0.88rem] leading-6 text-[var(--workspace-text-soft)]">
                {inbox.detail}
              </div>
              <div className="justify-self-end">
                <MailboxConnectionState />
              </div>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}

function MailboxView({
  mailbox,
  orderedMailboxes,
  managedInboxes,
  smartFolders,
  onOpenSmartFolderModal,
  onEditSmartFolder,
  onDeleteSmartFolder,
  onBack,
  onOpenMailbox,
  onRenameMailbox,
  onSaveLearningRule,
  onLearnCategoryDecision,
  onEnableAutoCategoryForSender,
  onRecordMessageOwnershipInteraction,
  senderCategoryLearning,
  messageOwnershipInteractions,
  currentUserId,
  currentUserEmail,
  mailboxStore,
  setMailboxStore,
  inboxSignatures,
  themeMode,
  aiSuggestionsEnabled,
  notificationNavigationRequest,
  onConsumeNotificationNavigation,
  manualPriorityOverrides,
  onSetManualPriority,
  getLinkedReviewForMessage,
  getLinkedReviewBadgeLabel,
  onOpenLinkedReview,
  onSyncMailbox,
  isSyncingMailbox,
  onSyncUnreadOverrides,
}: {
  mailbox: OrderedMailbox;
  orderedMailboxes: OrderedMailbox[];
  managedInboxes: ManagedWorkspaceInbox[];
  smartFolders: SmartFolderDefinition[];
  onOpenSmartFolderModal: () => void;
  onEditSmartFolder: (folderId: string) => void;
  onDeleteSmartFolder: (folderId: string) => void;
  onBack: () => void;
  onOpenMailbox: (mailbox: OrderedMailbox) => void;
  onRenameMailbox: (mailboxId: InboxId, nextTitle: string) => void;
  onSaveLearningRule: (
    ruleValue: string,
    ruleType: "sender" | "domain",
    category: CuevionMessageCategory,
    mailboxAction?: "keep" | "move",
    options?: {
      sourceContext?: LearningDecisionSourceContext;
      sourcePrioritySelection?: LearningDecisionPrioritySelection | null;
      sourceMailboxId?: InboxId | null;
      sourceCurrentMailboxId?: InboxId | null;
    },
  ) => void;
  onLearnCategoryDecision: (senderAddress: string, category: CuevionMessageCategory) => void;
  onEnableAutoCategoryForSender: (senderAddress: string) => void;
  onRecordMessageOwnershipInteraction: (messageId: string) => void;
  senderCategoryLearning: SenderCategoryLearningStore;
  messageOwnershipInteractions: MessageOwnershipInteractionStore;
  currentUserId: string;
  currentUserEmail: string;
  mailboxStore: MailboxStore;
  setMailboxStore: Dispatch<SetStateAction<MailboxStore>>;
  inboxSignatures: InboxSignatureStore;
  themeMode: "light" | "dark";
  aiSuggestionsEnabled: boolean;
  notificationNavigationRequest?: NotificationNavigationRequest | null;
  onConsumeNotificationNavigation?: (requestKey: number) => void;
  manualPriorityOverrides: Partial<Record<string, ManualPriorityOverride>>;
  onSetManualPriority: (messageId: string, shouldBePriority: boolean) => void;
  getLinkedReviewForMessage: (messageId: string) => ReviewItem | null;
  getLinkedReviewBadgeLabel: (messageId: string) => string | null;
  onOpenLinkedReview: (target: ReviewWorkspaceTarget) => void;
  onSyncMailbox: () => void;
  isSyncingMailbox: boolean;
  onSyncUnreadOverrides: (messages: MessageIdentitySource[], unread: boolean) => void;
}) {
  const [activeFilter, setActiveFilter] = useState<MailFilter>("All");
  const [sortOrder, setSortOrder] = useState<MailSortOrder>("desc");
  const [activeFolder, setActiveFolder] = useState<MailFolder>("Inbox");
  const [isSharedView, setIsSharedView] = useState(false);
  const [activeSmartFolderId, setActiveSmartFolderId] = useState<string | null>(null);
  const [preferredMailListPaneWidth, setPreferredMailListPaneWidth] = useState(() => {
    if (typeof window === "undefined") {
      return 520;
    }

    const storedValue = window.localStorage.getItem(MAIL_LIST_PANE_WIDTH_STORAGE_KEY);
    const parsedValue = storedValue ? Number.parseInt(storedValue, 10) : Number.NaN;

    return Number.isFinite(parsedValue) ? parsedValue : 520;
  });
  const [splitPaneWidth, setSplitPaneWidth] = useState(0);
  const [isWideSplitView, setIsWideSplitView] = useState(() => {
    if (typeof window === "undefined") {
      return false;
    }

    return window.matchMedia("(min-width: 1280px)").matches;
  });
  const [searchQuery, setSearchQuery] = useState("");
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const mailListViewportRef = useRef<HTMLDivElement | null>(null);
  const inboxInteractionViewportRef = useRef<HTMLDivElement | null>(null);
  const splitPaneContainerRef = useRef<HTMLDivElement | null>(null);
  const [isComposeOpen, setIsComposeOpen] = useState(false);
  const [isFullMessageOpen, setIsFullMessageOpen] = useState(false);
  const [lastNavigationSource, setLastNavigationSource] = useState<"priority" | null>(null);
  const [isCloseModalOpen, setIsCloseModalOpen] = useState(false);
  const [isMoreMenuOpen, setIsMoreMenuOpen] = useState(false);
  const [isSortMenuOpen, setIsSortMenuOpen] = useState(false);
  const [smartFolderMenuId, setSmartFolderMenuId] = useState<string | null>(null);
  const [smartFolderDeleteId, setSmartFolderDeleteId] = useState<string | null>(null);
  const [isReadingLearningMenuOpen, setIsReadingLearningMenuOpen] = useState(false);
  const [activeReadingLearningTrigger, setActiveReadingLearningTrigger] = useState<
    "reading-pane" | "full-message" | null
  >(null);
  const readingLearningMenuRef = useRef<HTMLDivElement | null>(null);
  const detailActionsMenuRef = useRef<HTMLDivElement | null>(null);
  const [detailActionsMenuState, setDetailActionsMenuState] = useState<{
    messageId: string;
    placement: "split" | "full";
  } | null>(null);
  const [readingLearningMenuAnchor, setReadingLearningMenuAnchor] = useState<{
    top: number;
    left: number;
    width: number;
    height: number;
  } | null>(null);
  const [contextMenuState, setContextMenuState] = useState<{
    messageId: string;
    x: number;
    y: number;
    moveMenuOpen: boolean;
    moveAnchorX: number | null;
    moveAnchorY: number | null;
    moveAnchorHeight: number | null;
    learningMenuOpen: boolean;
    learningAnchorX: number | null;
    learningAnchorY: number | null;
    learningAnchorHeight: number | null;
    learningChooserOpen: boolean;
    learningChooserMode: "type" | "sender" | null;
  } | null>(null);
  const [selectedMessageId, setSelectedMessageId] = useState<string | null>(
    mailboxStore[mailbox.id]?.Inbox[0]?.id ?? null,
  );
  const [selectedMessageIds, setSelectedMessageIds] = useState<string[]>(
    mailboxStore[mailbox.id]?.Inbox[0]?.id ? [mailboxStore[mailbox.id].Inbox[0].id] : [],
  );
  const [selectionAnchorId, setSelectionAnchorId] = useState<string | null>(
    mailboxStore[mailbox.id]?.Inbox[0]?.id ?? null,
  );
  const [resolvingSuggestionIds, setResolvingSuggestionIds] = useState<string[]>([]);
  const [resolvingBehaviorSuggestionIds, setResolvingBehaviorSuggestionIds] = useState<
    string[]
  >([]);
  const [dragTargetKey, setDragTargetKey] = useState<string | null>(null);
  const [dragPayload, setDragPayload] = useState<{
    sourceMailboxId: InboxId;
    sourceFolder: MailFolder;
    messageIds: string[];
  } | null>(null);
  const [isEmptyTrashConfirmationOpen, setIsEmptyTrashConfirmationOpen] = useState(false);
  const [trashEmptiedToastMessage, setTrashEmptiedToastMessage] = useState<string | null>(null);
  const dragPreviewCleanupRef = useRef<(() => void) | null>(null);
  const [composeTo, setComposeTo] = useState("");
  const [composeCc, setComposeCc] = useState("");
  const [composeBcc, setComposeBcc] = useState("");
  const [rememberedRecipients, setRememberedRecipients] = useState<string[]>(() => {
    if (typeof window === "undefined") {
      return [];
    }

    const storedValue = window.localStorage.getItem(COMPOSE_RECIPIENT_MEMORY_STORAGE_KEY);

    if (!storedValue) {
      return [];
    }

    try {
      const parsed = JSON.parse(storedValue) as string[];

      return parsed.filter((value) => typeof value === "string");
    } catch {
      return [];
    }
  });
  const [activeRecipientSuggestionField, setActiveRecipientSuggestionField] =
    useState<ComposeRecipientField | null>(null);
  const [showComposeBcc, setShowComposeBcc] = useState(false);
  const [composeSubject, setComposeSubject] = useState("");
  const [composeBody, setComposeBody] = useState("");
  const [composeSignatureSelection, setComposeSignatureSelection] = useState<string>("none");
  const [composeMailboxId, setComposeMailboxId] = useState<InboxId>(mailbox.id);
  const [composeMode, setComposeMode] = useState<ComposeMode>("new");
  const [composeSourceMessage, setComposeSourceMessage] = useState<MailMessage | null>(null);
  const [composeAttachments, setComposeAttachments] = useState<MailAttachment[]>([]);
  const [composeSendError, setComposeSendError] = useState<string | null>(null);
  const [isSendingCompose, setIsSendingCompose] = useState(false);
  const [pendingComposeAttachmentPickerOpen, setPendingComposeAttachmentPickerOpen] =
    useState(false);
  const composeToInputRef = useRef<HTMLInputElement | null>(null);
  const composeBodyInputRef = useRef<HTMLDivElement | null>(null);
  const composeAttachmentInputRef = useRef<HTMLInputElement | null>(null);
  const [isEditingMailboxTitle, setIsEditingMailboxTitle] = useState(false);
  const [mailboxTitleDraft, setMailboxTitleDraft] = useState(mailbox.title);
  const mailboxTitleInputRef = useRef<HTMLInputElement | null>(null);
  const [shareCollaborationMessageId, setShareCollaborationMessageId] = useState<
    string | null
  >(null);
  const [collaborationRequestType, setCollaborationRequestType] = useState<
    "needs_review" | "needs_action" | "note_only"
  >("needs_review");
  const [collaborationPersonId, setCollaborationPersonId] = useState<string>("");
  const [isInlineCollaborationInviteOpen, setIsInlineCollaborationInviteOpen] =
    useState(false);
  const [collaborationInviteDraft, setCollaborationInviteDraft] = useState("");
  const [collaborationInviteOptions, setCollaborationInviteOptions] = useState<
    MailMessageCollaborationParticipant[]
  >([]);
  const [collaborationNote, setCollaborationNote] = useState("");
  const [activeCollaborationMessageId, setActiveCollaborationMessageId] = useState<
    string | null
  >(null);
  const [collaborationReplyDraft, setCollaborationReplyDraft] = useState("");
  const [collaborationReplyVisibility, setCollaborationReplyVisibility] =
    useState<MailMessageCollaborationVisibility>("internal");
  const [isInviteParticipantOpen, setIsInviteParticipantOpen] = useState(false);
  const [collaborationInviteEmail, setCollaborationInviteEmail] = useState("");
  const [collaborationInvitePersonId, setCollaborationInvitePersonId] = useState("");
  const [copiedInviteLinkKey, setCopiedInviteLinkKey] = useState<string | null>(null);
  const collaborationReplyInputRef = useRef<HTMLTextAreaElement | null>(null);
  const collaborationMessageRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const [focusCollaborationComposer, setFocusCollaborationComposer] = useState(false);
  const [highlightedCollaborationMessageId, setHighlightedCollaborationMessageId] = useState<
    string | null
  >(null);
  const [collaborationMentionIndex, setCollaborationMentionIndex] = useState(0);
  const [collaborationReplySelection, setCollaborationReplySelection] = useState<number | null>(
    null,
  );

  const collaborationPeople = [
    {
      id: "emma_stone",
      name: "Emma Stone",
      email: "emma@cuevion.com",
    },
    {
      id: "david_cole",
      name: "David Cole",
      email: "david@cuevion.com",
    },
    {
      id: "mila_hart",
      name: "Mila Hart",
      email: "mila@cuevion.com",
    },
  ];
  const collaborationSelectablePeople = [
    ...collaborationPeople.map((person) => ({
      id: person.id,
      name: person.name,
      email: person.email,
      kind: "internal" as const,
      status: "active" as const,
    })),
    ...collaborationInviteOptions,
  ];
  const isCollaborationInviteDraftValid = isValidInviteEmail(collaborationInviteDraft);
  const currentUserName = orderedMailboxes[0]?.title ?? "You";
  const activeComposeMailbox =
    orderedMailboxes.find((candidate) => candidate.id === composeMailboxId) ?? mailbox;
  const composeSignatureOptions = orderedMailboxes
    .map((candidate) => {
      const signature = normalizeInboxSignatureSettings(inboxSignatures[candidate.id]);

      if (!hasSignatureContent(signature)) {
        return null;
      }

      return {
        id: candidate.id,
        email: candidate.email,
        signature,
      };
    })
    .filter((option): option is { id: InboxId; email: string; signature: InboxSignatureSettings } =>
      option !== null,
    );
  const resetComposeState = () => {
    setComposeTo("");
    setComposeCc("");
    setComposeBcc("");
    setActiveRecipientSuggestionField(null);
    setShowComposeBcc(false);
    setComposeSubject("");
    setComposeBody("");
    setComposeSignatureSelection("none");
    setComposeMailboxId(mailbox.id);
    setComposeMode("new");
    setComposeSourceMessage(null);
    setComposeAttachments([]);
    setComposeSendError(null);
    setIsSendingCompose(false);
  };

  const normalizeRememberedRecipient = (value: string) => value.trim().toLowerCase();

  const extractRecipientEmails = (value: string) =>
    value
      .split(/[,;]+/)
      .map((entry) => normalizeRememberedRecipient(entry))
      .filter((entry) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(entry));

  const rememberSentRecipients = (values: Array<string | undefined>) => {
    const nextRecipients = values.flatMap((value) => extractRecipientEmails(value ?? ""));

    if (nextRecipients.length === 0) {
      return;
    }

    setRememberedRecipients((current) => {
      const merged = [...nextRecipients, ...current].filter(
        (value, index, array) => array.indexOf(value) === index,
      );

      return merged.slice(0, 50);
    });
  };

  const getComposeRecipientValue = (field: ComposeRecipientField) => {
    if (field === "to") {
      return composeTo;
    }

    if (field === "cc") {
      return composeCc;
    }

    return composeBcc;
  };

  const setComposeRecipientValue = (field: ComposeRecipientField, value: string) => {
    if (field === "to") {
      setComposeTo(value);
      return;
    }

    if (field === "cc") {
      setComposeCc(value);
      return;
    }

    setComposeBcc(value);
  };

  const getComposeRecipientQuery = (value: string) => {
    const segments = value.split(/[,;]+/);
    return normalizeRememberedRecipient(segments[segments.length - 1] ?? "");
  };

  const getRecipientSuggestions = (field: ComposeRecipientField) => {
    const value = getComposeRecipientValue(field);
    const query = getComposeRecipientQuery(value);

    if (!query) {
      return [];
    }

    const existingRecipients = new Set(extractRecipientEmails(value));

    return rememberedRecipients
      .filter(
        (recipient) =>
          !existingRecipients.has(recipient) &&
          (recipient.startsWith(query) || recipient.includes(query)),
      )
      .slice(0, 6);
  };

  const applyRecipientSuggestion = (field: ComposeRecipientField, recipient: string) => {
    const currentValue = getComposeRecipientValue(field);
    const lastCommaIndex = currentValue.lastIndexOf(",");
    const lastSemicolonIndex = currentValue.lastIndexOf(";");
    const lastSeparatorIndex = Math.max(lastCommaIndex, lastSemicolonIndex);

    if (lastSeparatorIndex === -1) {
      setComposeRecipientValue(field, recipient);
    } else {
      const prefix = currentValue.slice(0, lastSeparatorIndex + 1);
      const spacer = /\s$/.test(prefix) ? "" : " ";
      setComposeRecipientValue(field, `${prefix}${spacer}${recipient}`);
    }

    setActiveRecipientSuggestionField(null);
  };

  const handleComposeRecipientBlur = () => {
    window.setTimeout(() => {
      setActiveRecipientSuggestionField(null);
    }, 120);
  };

  const renderComposeRecipientSuggestions = (field: ComposeRecipientField) => {
    if (activeRecipientSuggestionField !== field) {
      return null;
    }

    const suggestions = getRecipientSuggestions(field);

    if (suggestions.length === 0) {
      return null;
    }

    return (
      <div className="absolute left-0 right-0 top-full z-20 mt-2 rounded-[16px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] p-1 shadow-panel">
        {suggestions.map((recipient) => (
          <button
            key={recipient}
            type="button"
            onMouseDown={(event) => {
              event.preventDefault();
              applyRecipientSuggestion(field, recipient);
            }}
            className="flex w-full items-center rounded-[12px] px-3 py-2 text-left text-[0.82rem] text-[var(--workspace-text-soft)] transition-[background-color,color] duration-150 hover:bg-[var(--workspace-card-subtle)] hover:text-[var(--workspace-text)] focus-visible:outline-none"
          >
            {recipient}
          </button>
        ))}
      </div>
    );
  };

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    window.localStorage.setItem(
      COMPOSE_RECIPIENT_MEMORY_STORAGE_KEY,
      JSON.stringify(rememberedRecipients),
    );
  }, [rememberedRecipients]);

  const clampMailListPaneWidth = (nextWidth: number) => {
    if (!splitPaneWidth) {
      return Math.max(nextWidth, MIN_MAIL_LIST_PANE_WIDTH);
    }

    const availableSplitWidth =
      splitPaneWidth - MAIL_FOLDER_COLUMN_WIDTH - MAIL_SPLIT_GAP * 2;
    const maxWidth = Math.max(
      MIN_MAIL_LIST_PANE_WIDTH,
      availableSplitWidth - MIN_MAIL_DETAIL_PANE_WIDTH,
    );

    return Math.min(Math.max(nextWidth, MIN_MAIL_LIST_PANE_WIDTH), maxWidth);
  };

  const effectiveMailListPaneWidth =
    isWideSplitView && splitPaneWidth > 0
      ? clampMailListPaneWidth(preferredMailListPaneWidth)
      : null;

  const openCompose = () => {
    resetComposeState();
    const nextMailboxId = mailbox.id;
    const nextMailboxSignature = normalizeInboxSignatureSettings(
      inboxSignatures[nextMailboxId],
    );
    const nextComposeSignature =
      nextMailboxSignature.useByDefault && hasSignatureContent(nextMailboxSignature)
        ? nextMailboxSignature
        : null;

    setComposeMailboxId(nextMailboxId);
    setComposeSignatureSelection(nextComposeSignature ? nextMailboxId : "none");
    setComposeBody(
      buildComposeBody({
        mode: "new",
        sourceMessage: null,
        signature: nextComposeSignature,
      }),
    );
    setIsCloseModalOpen(false);
    setIsFullMessageOpen(false);
    setIsComposeOpen(true);
  };

  const openComposeAttachmentPicker = () => {
    if (isComposeOpen) {
      composeAttachmentInputRef.current?.click();
      return;
    }

    setPendingComposeAttachmentPickerOpen(true);
    openCompose();
  };

  const handleComposeAttachmentSelection = (event: ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = Array.from(event.target.files ?? []);

    if (!selectedFiles.length) {
      return;
    }

    const nextAttachments = selectedFiles.map((file) =>
      normalizeMailAttachment({
        id: createMailAttachmentId(file.name, file.size, file.type),
        name: file.name,
        mimeType: file.type || undefined,
        size: file.size,
        file,
      }),
    );

    setComposeAttachments((current) => {
      const mergedAttachments = [...current];

      nextAttachments.forEach((attachment) => {
        if (!mergedAttachments.some((entry) => entry.id === attachment.id)) {
          mergedAttachments.push(attachment);
        }
      });

      return mergedAttachments;
    });

    event.target.value = "";
  };

  const openComposeFromMessage = (
    message: MailMessage,
    mode: ComposeMode,
  ) => {
    const sourceMailboxId = currentMessageLocationById[message.id]?.mailboxId ?? mailbox.id;
    const originalSender = message.from.trim();
    const originalToRecipients = message.to
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
    const originalCcRecipients = (message.cc ?? "")
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
    const replyAllCcRecipients: string[] = [];

    if (mode === "reply_all") {
      const originalRecipientsExcludingCurrentUser = [
        ...originalToRecipients,
        ...originalCcRecipients,
      ].filter(
        (recipient) =>
          normalizeSenderLearningKey(recipient) !==
          normalizeSenderLearningKey(currentUserEmail),
      );

      if (originalRecipientsExcludingCurrentUser.length > 1) {
        [...originalToRecipients, ...originalCcRecipients].forEach((recipient) => {
          const normalizedRecipient = normalizeSenderLearningKey(recipient);

          if (
            normalizedRecipient === normalizeSenderLearningKey(currentUserEmail) ||
            normalizedRecipient === normalizeSenderLearningKey(originalSender) ||
            replyAllCcRecipients.some(
              (existingRecipient) =>
                normalizeSenderLearningKey(existingRecipient) === normalizedRecipient,
            )
          ) {
            return;
          }

          replyAllCcRecipients.push(recipient);
        });
      }

      console.debug("cuevion_reply_all_cc", replyAllCcRecipients);
    }

    setDetailActionsMenuState(null);
    resetComposeState();
    setIsCloseModalOpen(false);
    setIsFullMessageOpen(false);
    setComposeMailboxId(sourceMailboxId);
    const sourceInboxSignature = normalizeInboxSignatureSettings(
      inboxSignatures[sourceMailboxId],
    );
    const nextComposeSignature =
      sourceInboxSignature.useByDefault && hasSignatureContent(sourceInboxSignature)
        ? sourceInboxSignature
        : null;
    setComposeMode(mode);
    setComposeSourceMessage(message);
    setComposeTo(mode === "forward" ? "" : originalSender);
    setComposeCc(mode === "reply_all" ? replyAllCcRecipients.join(", ") : "");
    setComposeSubject(
      mode === "reply" || mode === "reply_all"
        ? message.subject.startsWith("Re:")
          ? message.subject
          : `Re: ${message.subject}`
        : message.subject.startsWith("Fwd:")
          ? message.subject
          : `Fwd: ${message.subject}`,
    );
    setComposeSignatureSelection(nextComposeSignature ? sourceMailboxId : "none");
    setComposeBody(
      buildComposeBody({
        mode,
        sourceMessage: message,
        signature: nextComposeSignature,
      }),
    );
    setComposeAttachments((message.attachments ?? []).map((attachment) => normalizeMailAttachment(attachment)));
    setIsComposeOpen(true);
  };

  useEffect(() => {
    setMailboxTitleDraft(mailbox.title);
  }, [mailbox.title]);

  useEffect(() => {
    if (isEditingMailboxTitle) {
      mailboxTitleInputRef.current?.focus();
      mailboxTitleInputRef.current?.select();
    }
  }, [isEditingMailboxTitle]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    window.localStorage.setItem(
      MAIL_LIST_PANE_WIDTH_STORAGE_KEY,
      String(preferredMailListPaneWidth),
    );
  }, [preferredMailListPaneWidth]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const mediaQuery = window.matchMedia("(min-width: 1280px)");
    const handleChange = (event: MediaQueryListEvent) => {
      setIsWideSplitView(event.matches);
    };

    setIsWideSplitView(mediaQuery.matches);
    mediaQuery.addEventListener("change", handleChange);

    return () => {
      mediaQuery.removeEventListener("change", handleChange);
    };
  }, []);

  useLayoutEffect(() => {
    const container = splitPaneContainerRef.current;

    if (!container || typeof ResizeObserver === "undefined") {
      return;
    }

    const observer = new ResizeObserver(([entry]) => {
      setSplitPaneWidth(entry.contentRect.width);
    });

    observer.observe(container);
    setSplitPaneWidth(container.getBoundingClientRect().width);

    return () => {
      observer.disconnect();
    };
  }, [isWideSplitView]);

  useEffect(() => {
    if (aiSuggestionsEnabled) {
      return;
    }

    setIsReadingLearningMenuOpen(false);
    setActiveReadingLearningTrigger(null);
    setReadingLearningMenuAnchor(null);
    setContextMenuState((current) =>
      current
        ? {
            ...current,
            learningMenuOpen: false,
            learningChooserOpen: false,
            learningChooserMode: null,
          }
        : current,
    );
  }, [aiSuggestionsEnabled]);

  useEffect(() => {
    if (!isComposeOpen) {
      return;
    }

    requestAnimationFrame(() => {
      if (composeMode === "new" || composeMode === "forward") {
        composeToInputRef.current?.focus();
        return;
      }

      const editor = composeBodyInputRef.current;

      if (!editor) {
        return;
      }

      editor.focus();

      const selection = window.getSelection();

      if (!selection) {
        return;
      }

      const range = document.createRange();
      range.setStart(editor, 0);
      range.collapse(true);
      selection.removeAllRanges();
      selection.addRange(range);
    });
  }, [composeMode, isComposeOpen]);

  useEffect(() => {
    const editor = composeBodyInputRef.current;

    if (!editor || editor.innerHTML === composeBody) {
      return;
    }

    editor.innerHTML = composeBody;
  }, [composeBody]);

  const syncComposeBodyValue = () => {
    const editor = composeBodyInputRef.current;

    if (!editor) {
      return;
    }

    const nextValue = editor.innerHTML;
    setComposeBody(nextValue);
    const signatureNode = editor.querySelector("[data-compose-signature]");
    const hasSignatureNodeContent =
      Boolean(signatureNode?.querySelector("img")) ||
      Boolean(signatureNode?.textContent?.replace(/\u00a0/g, " ").trim());

    if (!signatureNode || !hasSignatureNodeContent) {
      setComposeSignatureSelection("none");
    }
  };

  const handleComposeBodyKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (!(event.metaKey || event.ctrlKey)) {
      return;
    }

    const key = event.key.toLowerCase();

    if (key === "b" || key === "i") {
      event.preventDefault();
      document.execCommand(key === "b" ? "bold" : "italic");
      syncComposeBodyValue();
    }
  };

  const handleComposeBodyPaste = (event: ReactClipboardEvent<HTMLDivElement>) => {
    event.preventDefault();
    const pastedText = event.clipboardData.getData("text/plain");

    if (!pastedText) {
      return;
    }

    pastedText.split("\n").forEach((line, index) => {
      if (index > 0) {
        document.execCommand("insertLineBreak");
      }

      if (line.length > 0) {
        document.execCommand("insertText", false, line);
      }
    });

    syncComposeBodyValue();
  };

  const handleComposeSignatureSelectionChange = (nextValue: string) => {
    const nextSignature =
      nextValue === "none"
        ? null
        : composeSignatureOptions.find((option) => option.id === nextValue)?.signature ?? null;

    setComposeSignatureSelection(nextSignature ? nextValue : "none");
    setComposeBody((current) => withComposeSignatureMarkup(current, nextSignature));
  };

  useEffect(() => {
    if (!isComposeOpen || !pendingComposeAttachmentPickerOpen) {
      return;
    }

    requestAnimationFrame(() => {
      composeAttachmentInputRef.current?.click();
      setPendingComposeAttachmentPickerOpen(false);
    });
  }, [isComposeOpen, pendingComposeAttachmentPickerOpen]);

  const mailboxCollections = mailboxStore[mailbox.id] ?? createEmptyMailboxCollections();
  const hasLearnedFilteredBehavior = hasLearnedShowLessBehavior(
    senderCategoryLearning,
  );
  const visibleMailboxCollections: Record<MailFolder, MailMessage[]> =
    hasLearnedFilteredBehavior
      ? {
          Inbox: mailboxCollections.Inbox,
          Drafts: mailboxCollections.Drafts,
          Sent: mailboxCollections.Sent,
          Archive: mailboxCollections.Archive,
          Filtered: mailboxCollections.Filtered,
          Spam: mailboxCollections.Spam,
          Trash: mailboxCollections.Trash,
        }
      : {
          Inbox: [...mailboxCollections.Inbox, ...mailboxCollections.Filtered],
          Drafts: mailboxCollections.Drafts,
          Sent: mailboxCollections.Sent,
          Archive: mailboxCollections.Archive,
          Filtered: [],
          Spam: mailboxCollections.Spam,
          Trash: mailboxCollections.Trash,
        };
  const messageCollections: Record<MailFolder, MailMessage[]> = {
    Inbox: visibleMailboxCollections.Inbox,
    Drafts: visibleMailboxCollections.Drafts,
    Sent: visibleMailboxCollections.Sent,
    Archive: visibleMailboxCollections.Archive,
    Filtered: visibleMailboxCollections.Filtered,
    Spam: visibleMailboxCollections.Spam,
    Trash: visibleMailboxCollections.Trash,
  };
  const mailboxThreadMessages = canonicalFolderOrder.flatMap((folder) => messageCollections[folder]);
  const workspaceSharedEntries = Object.entries(mailboxStore).flatMap(
    ([entryMailboxId, collections]) =>
      canonicalFolderOrder.flatMap((folder) =>
        collections[folder].flatMap((message) =>
          isMessageInSharedView(message)
            ? [
                {
                  mailboxId: entryMailboxId as InboxId,
                  folder,
                  message,
                },
              ]
            : [],
        ),
      ),
  );
  const workspaceSharedMessages = workspaceSharedEntries.map((entry) => entry.message);
  const workspaceMessageLocationById = workspaceSharedEntries.reduce<
    Record<string, { mailboxId: InboxId; folder: MailFolder }>
  >((locations, entry) => {
    locations[entry.message.id] = {
      mailboxId: entry.mailboxId,
      folder: entry.folder,
    };
    return locations;
  }, {});
  const activeSmartFolder =
    smartFolders.find((folder) => folder.id === activeSmartFolderId) ?? null;
  const smartFolderScopeMailboxIds =
    activeSmartFolder?.scope === "selected" && activeSmartFolder.selectedInboxIds.length > 0
      ? activeSmartFolder.selectedInboxIds
      : orderedMailboxes.map((candidate) => candidate.id);
  const smartFolderEntries = activeSmartFolder
    ? smartFolderScopeMailboxIds.flatMap((mailboxId) =>
        ((mailboxId === mailbox.id
          ? messageCollections.Inbox
          : mailboxStore[mailboxId]?.Inbox) ?? [])
          .filter((message) => doesMessageMatchSmartFolder(message, activeSmartFolder))
          .map((message) => ({
            mailboxId,
            folder: "Inbox" as MailFolder,
            message,
          })),
      )
    : [];
  const smartFolderMessages = smartFolderEntries.map((entry) => entry.message);
  const smartFolderMessageLocationById = smartFolderEntries.reduce<
    Record<string, { mailboxId: InboxId; folder: MailFolder }>
  >((locations, entry) => {
    locations[entry.message.id] = {
      mailboxId: entry.mailboxId,
      folder: entry.folder,
    };
    return locations;
  }, {});
  const currentMessageLocationById = activeSmartFolder
    ? smartFolderMessageLocationById
    : workspaceMessageLocationById;
  const getManualPriorityOverride = (messageId: string) =>
    manualPriorityOverrides[messageId];
  const getVisibleMessageSignal = (message: MailMessage) =>
    resolveVisiblePrioritySignal(message, getManualPriorityOverride(message.id));
  const isVisiblePriorityMessage = (message: MailMessage) =>
    isMessageVisiblePriority(message, getManualPriorityOverride(message.id));
  const folderMessages = activeSmartFolder
    ? smartFolderMessages
    : isSharedView
      ? workspaceSharedMessages
      : messageCollections[activeFolder];
  const isFilteredViewEmpty = !isSharedView && !activeSmartFolder && activeFolder === "Filtered" && folderMessages.length === 0;
  const isSharedViewEmpty = isSharedView && workspaceSharedMessages.length === 0;
  const isSmartFolderViewEmpty = Boolean(activeSmartFolder) && smartFolderMessages.length === 0;
  const normalizedSearchQuery = searchQuery.trim().toLowerCase();
  const visibleMessages = folderMessages.filter((message) => {
    const matchesSearch =
      normalizedSearchQuery.length === 0 ||
      `${message.sender} ${message.subject} ${message.snippet} ${message.from} ${message.to}`
        .toLowerCase()
        .includes(normalizedSearchQuery);

    if (!matchesSearch) {
      return false;
    }

    if (activeFilter === "Unread") {
      return Boolean(message.unread);
    }

    if (activeFilter === "Priority") {
      return isVisiblePriorityMessage(message);
    }

    if (activeFilter === "Review") {
      return message.signal === "For review" || message.signal === "Shortlist";
    }

    return true;
  });
  const sortedMessages = [...visibleMessages].sort((firstMessage, secondMessage) => {
    const firstTime = resolveMailDateMs(firstMessage);
    const secondTime = resolveMailDateMs(secondMessage);

    return sortOrder === "desc"
      ? secondTime - firstTime
      : firstTime - secondTime;
  });
  const visibleSelectedMessageIds = selectedMessageIds.filter((messageId) =>
    sortedMessages.some((message) => message.id === messageId),
  );
  const isMultiSelectActive = visibleSelectedMessageIds.length > 1;
  const selectedMessage =
    sortedMessages.find(
      (message) =>
        message.id === selectedMessageId &&
        visibleSelectedMessageIds.includes(message.id),
    ) ??
    sortedMessages.find((message) =>
      visibleSelectedMessageIds.includes(message.id),
    ) ??
    sortedMessages.find((message) => message.id === selectedMessageId) ??
    sortedMessages[0] ??
    null;
  const fullWidthMessage =
    folderMessages.find((message) => message.id === selectedMessageId) ??
    selectedMessage;
  const advanceSelectionAfterAction = (processedMessageIds: string[]) => {
    if (processedMessageIds.length === 0) {
      return;
    }

    const processedIdSet = new Set(processedMessageIds);
    const currentIndex = sortedMessages.findIndex(
      (message) => message.id === selectedMessageId && processedIdSet.has(message.id),
    );
    const fallbackIndex = sortedMessages.findIndex((message) =>
      processedIdSet.has(message.id),
    );
    const anchorIndex = currentIndex >= 0 ? currentIndex : fallbackIndex;
    const remainingMessages = sortedMessages.filter(
      (message) => !processedIdSet.has(message.id),
    );
    const nextMessage =
      anchorIndex >= 0
        ? remainingMessages[Math.min(anchorIndex, remainingMessages.length - 1)] ?? null
        : remainingMessages[0] ?? null;

    setSelectionState(
      nextMessage ? [nextMessage.id] : [],
      nextMessage?.id ?? null,
      nextMessage?.id ?? null,
    );

    if (!nextMessage) {
      setIsFullMessageOpen(false);
    }
  };
  const getThreadMessages = (message: MailMessage | null) => {
    if (!message) {
      return [];
    }

    const messageLocation = currentMessageLocationById[message.id];
    const threadSourceMessages = messageLocation
      ? canonicalFolderOrder.flatMap(
          (folder) => mailboxStore[messageLocation.mailboxId][folder],
        )
      : mailboxThreadMessages;

    return [message, ...getRecentThreadMessages(message, threadSourceMessages)]
      .filter(
        (candidate, index, candidates) =>
          candidates.findIndex((entry) => entry.id === candidate.id) === index,
      )
      .sort((firstMessage, secondMessage) => {
        return resolveMailDateMs(firstMessage) - resolveMailDateMs(secondMessage);
      });
  };
  const renderThreadTimeline = (message: MailMessage | null, density: "split" | "full") => {
    const threadMessages = getThreadMessages(message);

    if (threadMessages.length === 0) {
      return null;
    }

    return (
      <div className={density === "full" ? "space-y-4" : "space-y-3"}>
        {threadMessages.map((threadMessage) => {
          const isCurrentUser =
            normalizeSenderLearningKey(threadMessage.from) ===
              normalizeSenderLearningKey(mailbox.email) ||
            threadMessage.signal === "Sent" ||
            threadMessage.sender === "You";
          const quoteStartIndex = getQuotedParagraphStartIndex(threadMessage.body);
          const leadingParagraphs =
            quoteStartIndex === -1
              ? threadMessage.body
              : threadMessage.body.slice(0, quoteStartIndex);
          const quotedParagraphs =
            quoteStartIndex === -1 ? [] : threadMessage.body.slice(quoteStartIndex);

          return (
            <div
              key={threadMessage.id}
              className={`max-w-[92%] rounded-[20px] border px-4 py-4 ${
                density === "full" ? "md:px-5 md:py-5" : ""
              } ${
                isCurrentUser
                  ? "ml-auto border-[color:rgba(117,152,123,0.26)] bg-[linear-gradient(180deg,rgba(235,244,236,0.96),rgba(224,237,227,0.92))]"
                  : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)]"
              }`}
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <div className="text-[0.84rem] font-medium tracking-[-0.012em] text-[var(--workspace-text)]">
                    {isCurrentUser ? "You" : threadMessage.sender}
                  </div>
                  {threadMessage.isAutoReply &&
                  threadMessage.autoReplyType === "out_of_office" ? (
                    <span className="inline-flex items-center justify-center rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-2.5 py-1 text-[0.6rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-text-faint)]">
                      Automatic reply
                    </span>
                  ) : null}
                </div>
                <div className="text-[0.7rem] uppercase tracking-[0.12em] text-[var(--workspace-text-faint)]">
                  {threadMessage.timestamp}
                </div>
              </div>
              <div className="mt-3 space-y-3">
                {threadMessage.bodyHtml ? (
                  <div
                    className={`whitespace-pre-wrap text-[0.94rem] ${
                      density === "full" ? "leading-8" : "leading-7"
                    } text-[var(--workspace-text-soft)] [&_a]:text-[color:rgba(70,109,73,0.96)] [&_a]:underline [&_div[data-compose-signature-divider='true']]:my-2 [&_div[data-compose-signature-divider='true']]:h-px [&_div[data-compose-signature-divider='true']]:w-full [&_div[data-compose-signature-divider='true']]:bg-[color:rgba(121,151,120,0.18)] [&_div[data-compose-signature-logo='true']]:pt-1 [&_div[data-compose-signature-logo='true']_img]:max-h-[76px] [&_div[data-compose-signature-logo='true']_img]:w-auto [&_div[data-compose-signature-logo='true']_img]:max-w-full [&_div[data-compose-signature-logo='true']_img]:object-contain [&_div[data-compose-signature-row='true']]:flex [&_div[data-compose-signature-row='true']]:items-start [&_div[data-compose-signature-row='true']]:gap-4 [&_div[data-compose-signature-right='true']]:min-w-0 [&_div[data-compose-signature-right='true']]:flex-1 [&_div[data-compose-signature-spacer='true']]:min-h-[1.75rem] [&_div[data-compose-signature-text='true']]:whitespace-pre-wrap [&_div[data-compose-signature-text='true']]:text-[0.86rem] [&_div[data-compose-signature-text='true']]:leading-[1.45] [&_div[data-compose-signature-text='true']_div]:min-h-[1.2rem] [&_div[data-compose-signature-text='true']_p]:min-h-[1.2rem] [&_div[data-compose-quote='true']]:pt-3`}
                    dangerouslySetInnerHTML={{ __html: threadMessage.bodyHtml }}
                  />
                ) : (
                  <>
                    {leadingParagraphs.map((paragraph) => (
                      <p
                        key={`${threadMessage.id}-${paragraph}`}
                        className={`text-[0.94rem] ${
                          density === "full" ? "leading-8" : "leading-7"
                        } text-[var(--workspace-text-soft)]`}
                      >
                        {paragraph}
                      </p>
                    ))}
                    {threadMessage.signature ? (
                      <div className="pt-1">
                        <SignatureBlock signature={threadMessage.signature} />
                      </div>
                    ) : null}
                    {quotedParagraphs.map((paragraph) => (
                      <p
                        key={`${threadMessage.id}-quoted-${paragraph}`}
                        className={`text-[0.94rem] ${
                          density === "full" ? "leading-8" : "leading-7"
                        } text-[var(--workspace-text-soft)]`}
                      >
                        {paragraph}
                      </p>
                    ))}
                  </>
                )}
              </div>
            </div>
          );
        })}
      </div>
    );
  };
  const shareCollaborationMessage = getMessageById(shareCollaborationMessageId);
  const activeCollaborationMessage = getMessageById(activeCollaborationMessageId);
  const activeCollaborationParticipants = activeCollaborationMessage?.collaboration
    ? getCollaborationParticipants(activeCollaborationMessage.collaboration)
    : [];
  const collaborationMentionCandidates = getCollaborationMentionTargets(
    activeCollaborationParticipants,
    collaborationPeople,
  );
  const collaborationMentionQuery = getMentionQueryAtCursor(
    collaborationReplyDraft,
    collaborationReplySelection,
  );
  const visibleCollaborationMentionCandidates = collaborationMentionQuery
    ? collaborationMentionCandidates.filter((candidate) =>
        candidate.handle
          .toLowerCase()
          .includes(collaborationMentionQuery.query.toLowerCase()),
      )
    : [];
  const visibleCollaborationMessages = activeCollaborationMessage?.collaboration
    ? activeCollaborationMessage.collaboration.messages.filter(
        (entry) => canViewerSeeCollaborationMessage(entry, "workspace"),
      )
    : [];

  useEffect(() => {
    if (!activeCollaborationMessageId || !focusCollaborationComposer) {
      return;
    }

    collaborationReplyInputRef.current?.focus();
    setFocusCollaborationComposer(false);
  }, [activeCollaborationMessageId, focusCollaborationComposer]);

  useEffect(() => {
    if (
      !notificationNavigationRequest ||
      notificationNavigationRequest.mailboxId !== mailbox.id
    ) {
      return;
    }

    const targetFolder = canonicalFolderOrder.find((folder) =>
      mailboxStore[mailbox.id][folder].some(
        (message) => message.id === notificationNavigationRequest.messageId,
      ),
    );
    const targetMessage = targetFolder
      ? mailboxStore[mailbox.id][targetFolder].find(
          (message) => message.id === notificationNavigationRequest.messageId,
        ) ?? null
      : null;

    if (!targetFolder || !targetMessage) {
      onConsumeNotificationNavigation?.(notificationNavigationRequest.requestKey);
      return;
    }

    setActiveSmartFolderId(null);
    setActiveFolder(targetFolder);
    setIsSharedView(false);
    setIsFullMessageOpen(Boolean(notificationNavigationRequest.openFullMessage));
    setLastNavigationSource(notificationNavigationRequest.source ?? null);
    setSelectionState(
      [notificationNavigationRequest.messageId],
      notificationNavigationRequest.messageId,
      notificationNavigationRequest.messageId,
    );
    requestAnimationFrame(() => {
      const targetRow = mailListViewportRef.current?.querySelector<HTMLElement>(
        `[data-message-row-id="${CSS.escape(notificationNavigationRequest.messageId)}"]`,
      );

      targetRow?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
    if (notificationNavigationRequest.focusReplyComposer) {
      closeCollaborationOverlay();
      openComposeFromMessage(targetMessage, "reply");
    } else if (notificationNavigationRequest.openFullMessage) {
      closeCollaborationOverlay();
      setHighlightedCollaborationMessageId(null);
    } else {
      setHighlightedCollaborationMessageId(
        notificationNavigationRequest.collaborationMessageId ?? null,
      );
      openCollaborationOverlay(notificationNavigationRequest.messageId);
    }
    onConsumeNotificationNavigation?.(notificationNavigationRequest.requestKey);
  }, [
    mailboxStore,
    mailbox.id,
    notificationNavigationRequest,
    onConsumeNotificationNavigation,
  ]);

  useEffect(() => {
    if (!activeCollaborationMessageId || !highlightedCollaborationMessageId) {
      return;
    }

    const highlightedMessage = collaborationMessageRefs.current[highlightedCollaborationMessageId];

    if (!highlightedMessage) {
      return;
    }

    highlightedMessage.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [activeCollaborationMessageId, highlightedCollaborationMessageId]);

  const renderBehaviorSuggestion = (message: MailMessage) => {
    void message;
    return null;
  };

  const renderMessageCollaboration = (message: MailMessage) => {
    const collaboration = message.collaboration;

    if (!collaboration) {
      return null;
    }

    const collaborationButtonClass =
      collaboration.state === "resolved"
        ? "inline-flex items-center rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-3 py-1 text-[0.76rem] leading-6 text-[color:rgba(106,98,89,0.86)] transition-[background-color,border-color,color] duration-150 hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface)] hover:text-[var(--workspace-text-soft)] focus-visible:outline-none"
        : "inline-flex items-center rounded-full border border-[color:rgba(95,138,91,0.4)] bg-[linear-gradient(180deg,rgba(183,216,174,0.44),rgba(127,169,121,0.32))] px-3 py-1 text-[0.76rem] leading-6 shadow-[0_9px_20px_rgba(103,143,98,0.14),inset_0_1px_0_rgba(255,255,255,0.24)] transition-[background-color,border-color,color,box-shadow] duration-150 hover:border-[color:rgba(86,126,82,0.48)] hover:bg-[linear-gradient(180deg,rgba(183,216,174,0.54),rgba(127,169,121,0.4))] focus-visible:outline-none dark:border-[color:rgba(128,167,124,0.34)] dark:bg-[linear-gradient(180deg,rgba(94,126,97,0.34),rgba(67,92,70,0.28))] dark:text-[color:rgba(227,239,223,0.96)] dark:shadow-[0_10px_22px_rgba(18,28,20,0.22),inset_0_1px_0_rgba(255,255,255,0.06)] dark:hover:border-[color:rgba(144,184,140,0.4)] dark:hover:bg-[linear-gradient(180deg,rgba(104,138,107,0.42),rgba(73,100,76,0.34))] dark:hover:text-[color:rgba(240,247,237,0.98)]";
    const collaborationButtonStyle =
      collaboration.state === "resolved" || themeMode === "dark"
        ? undefined
        : {
            color: "rgba(24,34,24,0.99)",
            WebkitTextFillColor: "rgba(24,34,24,0.99)",
          };

    return (
      <div className="w-[94%] space-y-0.5 pt-1">
        <div className="text-[0.82rem] leading-6 text-[color:rgba(120,111,100,0.68)]">
          Participants:{" "}
          {getCollaborationParticipants(collaboration)
            .map((participant) => participant.name || participant.email)
            .join(", ")}{" "}
          · by {collaboration.requestedBy}
        </div>
        <button
          type="button"
          onClick={() => openCollaborationOverlay(message.id)}
          className={collaborationButtonClass}
          style={collaborationButtonStyle}
        >
          Open collaboration
        </button>
      </div>
    );
  };

  const renderAIDecisionBlock = (message: MailMessage) => {
    const decision = getAIDecisionCopy(message);
    const isLightMode = themeMode === "light";

    return (
      <div
        className="rounded-[20px] px-4 py-3.5"
        style={
          isLightMode
            ? {
                background: "rgba(255, 252, 248, 0.96)",
                border: "1px solid rgba(196, 186, 173, 0.24)",
                boxShadow: "none",
              }
            : {
                background:
                  "linear-gradient(180deg,rgba(57,52,46,0.8),rgba(49,45,40,0.76))",
                border: "1px solid rgba(122,114,103,0.28)",
              }
        }
      >
        <div className="flex items-start gap-3">
          <div
            className="mt-0.5 inline-flex h-7 w-7 flex-none items-center justify-center rounded-full"
            style={
              isLightMode
                ? {
                    background: "rgba(239, 233, 224, 0.44)",
                    color: "rgba(147, 130, 104, 0.64)",
                  }
                : {
                    background: "rgba(84,76,66,0.72)",
                    color: "rgba(223,205,176,0.82)",
                  }
            }
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 16 16"
              className="h-3.5 w-3.5"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M8 2.25a3.5 3.5 0 0 0-2.05 6.34c.33.24.55.61.55 1.02v.14h3v-.14c0-.41.22-.78.55-1.02A3.5 3.5 0 0 0 8 2.25Z" />
              <path d="M6.7 11.25h2.6" />
              <path d="M6.95 13h2.1" />
            </svg>
          </div>
          <div className="min-w-0 space-y-1">
            <div
              className="text-[0.92rem] font-medium leading-6"
              style={
                isLightMode
                  ? { color: "rgba(95, 86, 77, 0.82)" }
                  : { color: "rgba(238,231,223,0.94)" }
              }
            >
              {decision.primary}
            </div>
            <div
              className="text-[0.8rem] leading-6"
              style={
                isLightMode
                  ? { color: "rgba(132, 122, 111, 0.62)" }
                  : { color: "rgba(188,178,166,0.76)" }
              }
            >
              {decision.secondary}
            </div>
          </div>
        </div>
      </div>
    );
  };

  const getAttachmentType = (attachment: MailAttachment) => {
    const extension = attachment.name.split(".").pop()?.toLowerCase() ?? "";
    const mimeType = attachment.mimeType?.toLowerCase() ?? "";

    switch (true) {
      case mimeType.startsWith("application/pdf") || extension === "pdf":
        return { label: "PDF", icon: "PDF", extension: "pdf" };
      case ["txt", "md", "doc", "docx", "rtf"].includes(extension) ||
        mimeType.startsWith("text/") ||
        mimeType.includes("document"):
        return { label: "Text", icon: "TXT", extension: extension || "txt" };
      case ["jpg", "jpeg", "png", "gif", "webp", "heic"].includes(extension) ||
        mimeType.startsWith("image/"):
        return { label: "Image", icon: "IMG", extension: extension || "img" };
      case ["csv", "xlsx", "xls"].includes(extension) ||
        mimeType.includes("sheet") ||
        mimeType.includes("excel") ||
        mimeType.includes("csv"):
        return { label: "Sheet", icon: "XLS", extension: extension || "xls" };
      case ["wav", "mp3", "aiff", "m4a"].includes(extension) ||
        mimeType.startsWith("audio/"):
        return { label: "Audio", icon: "AUD", extension: extension || "aud" };
      case ["zip", "rar"].includes(extension) || mimeType.includes("zip"):
        return { label: "Archive", icon: "ZIP", extension: extension || "zip" };
      default:
        return { label: "File", icon: "FILE", extension: extension || "file" };
    }
  };

  const formatAttachmentSize = (size?: number) => {
    if (!size || size <= 0) {
      return null;
    }

    if (size >= 1024 * 1024) {
      return `${(size / (1024 * 1024)).toFixed(1)} MB`;
    }

    if (size >= 1024) {
      return `${Math.round(size / 1024)} KB`;
    }

    return `${size} B`;
  };

  const handleAttachmentOpen = (attachment: MailAttachment) => {
    console.debug("cuevion_attachment_open", {
      name: attachment.name,
      mimeType: attachment.mimeType,
      size: attachment.size,
    });

    if (attachment.file) {
      const objectUrl = URL.createObjectURL(attachment.file);
      const downloadAnchor = document.createElement("a");

      downloadAnchor.href = objectUrl;
      downloadAnchor.download = attachment.name;
      downloadAnchor.rel = "noopener";
      downloadAnchor.click();

      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      return;
    }

    window.alert(`Opening ${attachment.name}`);
  };

  const renderAttachmentItem = (
    attachment: MailAttachment,
    options?: { removable?: boolean; onRemove?: () => void },
  ) => {
    const attachmentType = getAttachmentType(attachment);
    const attachmentSize = formatAttachmentSize(attachment.size);

    return (
      <div
        key={attachment.id}
        className="group relative"
      >
        <button
          type="button"
          onClick={() => handleAttachmentOpen(attachment)}
          className="flex min-w-[168px] items-center gap-3 rounded-[18px] border border-[var(--workspace-border)] bg-[var(--workspace-card-subtle)] px-3.5 py-3 text-left transition-[background-color,border-color,box-shadow,transform] duration-150 hover:border-[var(--workspace-border-hover)] hover:bg-[var(--workspace-hover-surface)] hover:shadow-[0_8px_18px_rgba(48,38,29,0.08)] focus-visible:outline-none"
        >
          <span className="inline-flex h-9 min-w-[2.75rem] items-center justify-center rounded-[12px] border border-[color:rgba(120,104,89,0.12)] bg-[color:rgba(255,252,247,0.82)] px-2 text-[0.62rem] font-medium uppercase tracking-[0.12em] text-[var(--workspace-text-soft)]">
            {attachmentType.icon}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[0.82rem] font-medium leading-6 text-[var(--workspace-text)]">
              {attachment.name}
            </span>
            <span className="block truncate text-[0.68rem] uppercase tracking-[0.12em] text-[var(--workspace-text-faint)]">
              {attachmentType.label}
              {attachmentSize ? ` • ${attachmentSize}` : ""}
            </span>
          </span>
        </button>
        {options?.removable && options.onRemove ? (
          <button
            type="button"
            aria-label={`Remove ${attachment.name}`}
            onClick={(event) => {
              event.stopPropagation();
              options.onRemove?.();
            }}
            className="absolute right-2 top-2 inline-flex h-6 w-6 items-center justify-center rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[0.78rem] leading-none text-[var(--workspace-text-faint)] opacity-0 transition-[opacity,color,border-color,background-color] duration-150 group-hover:opacity-100 hover:border-[var(--workspace-border-hover)] hover:text-[var(--workspace-text)] focus-visible:opacity-100 focus-visible:outline-none"
          >
            ×
          </button>
        ) : null}
      </div>
    );
  };

  const renderMessageActions = (
    message: MailMessage,
    placement: "split" | "full",
  ) => {
    const menuOpen =
      detailActionsMenuState?.messageId === message.id &&
      detailActionsMenuState.placement === placement;
    const messageIsVisiblePriority = isVisiblePriorityMessage(message);
    const actionClass =
      "inline-flex h-8 cursor-pointer items-center justify-center rounded-full border border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] px-3 text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.16),0_8px_24px_rgba(118,170,112,0.08)] transition-[background-image,border-color,color,transform,box-shadow] duration-150 hover:bg-[linear-gradient(180deg,var(--workspace-accent-surface-hover-start),var(--workspace-accent-surface-hover-end))] active:translate-y-[0.5px] focus-visible:border-[var(--workspace-accent-border)] focus-visible:bg-[linear-gradient(180deg,var(--workspace-accent-surface-hover-start),var(--workspace-accent-surface-hover-end))] focus-visible:outline-none";
    const menuItemClass =
      "flex w-full cursor-pointer items-center rounded-[14px] px-3 py-2.5 text-left text-[0.82rem] text-[var(--workspace-text-soft)] transition-colors duration-150 hover:bg-[var(--workspace-menu-hover)] focus-visible:outline-none";

    return (
      <div
        className={`relative flex flex-wrap items-center ${
          placement === "full"
            ? "gap-x-2.5 gap-y-2 text-[0.76rem] font-medium uppercase tracking-[0.14em] text-[color:rgba(86,79,71,0.9)]"
            : "gap-x-2.5 gap-y-2 pt-5 text-[0.76rem] font-medium uppercase tracking-[0.14em] text-[color:rgba(86,79,71,0.9)]"
        }`}
      >
        <button
          type="button"
          onClick={() => openComposeFromMessage(message, "reply")}
          className={actionClass}
        >
          Reply
        </button>
        <button
          type="button"
          onClick={() => openComposeFromMessage(message, "reply_all")}
          className={actionClass}
        >
          Reply all
        </button>
        <button
          type="button"
          onClick={() => openComposeFromMessage(message, "forward")}
          className={actionClass}
        >
          Forward
        </button>
        {!isReadOnlySmartFolderView ? (
          <div className="relative">
            <button
              type="button"
              data-detail-actions-trigger
              onClick={() =>
                setDetailActionsMenuState((current) =>
                  current?.messageId === message.id && current.placement === placement
                    ? null
                    : { messageId: message.id, placement },
                )
              }
              className={actionClass}
            >
              More ▾
            </button>
            {menuOpen ? (
              <div
                ref={detailActionsMenuRef}
                className={`absolute top-full z-20 mt-2 w-[188px] rounded-[18px] border border-[var(--workspace-menu-border)] bg-[var(--workspace-menu-bg)] p-2 shadow-[0_14px_32px_rgba(41,34,27,0.10)] ${
                  placement === "full" ? "right-0" : "left-0"
                }`}
              >
                <button
                  type="button"
                  onClick={() => {
                    openShareCollaboration(message.id);
                  }}
                  className={menuItemClass}
                >
                  Start collaboration…
                </button>
                <button
                  type="button"
                  onClick={() => {
                    onSetManualPriority(message.id, !messageIsVisiblePriority);
                    setDetailActionsMenuState(null);
                  }}
                  className={menuItemClass}
                >
                  {messageIsVisiblePriority ? "Remove priority" : "Mark as priority"}
                </button>
                {messageIsVisiblePriority ? (
                  <button
                    type="button"
                    onClick={() => {
                      onSetManualPriority(message.id, false);
                      setDetailActionsMenuState(null);
                    }}
                    className={menuItemClass}
                  >
                    Mark as done
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={() => {
                    if (isSharedView) {
                      moveMessagesAcrossWorkspace(mailbox.id, "Archive", [message.id]);
                      return;
                    }

                    moveMessages(mailbox.id, activeFolder, mailbox.id, "Archive", [message.id]);
                  }}
                  className={menuItemClass}
                >
                  Archive
                </button>
                <button
                  type="button"
                  onClick={() => {
                    deleteMessages([message.id]);
                  }}
                  className={menuItemClass}
                >
                  Delete
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setMessageUnreadState(activeFolder, message.id, !message.unread);
                  }}
                  className={menuItemClass}
                >
                  {message.unread ? "Mark as read" : "Mark as unread"}
                </button>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    );
  };

  const serializeComposeAttachment = async (attachment: MailAttachment) => {
    if (!attachment.file) {
      return null;
    }

    const bytes = new Uint8Array(await attachment.file.arrayBuffer());
    let binary = "";

    bytes.forEach((byte) => {
      binary += String.fromCharCode(byte);
    });

    return {
      name: attachment.name,
      mimeType: attachment.mimeType,
      contentBase64: btoa(binary),
    };
  };

  const saveDraftAndClose = () => {
    const draftId = `${activeComposeMailbox.id}-draft-${Date.now()}`;
    const bodyPreview = extractComposePlainText(composeBody);
    const bodyParagraphs = extractComposeParagraphs(composeBody);
    const draftMessage = normalizeMailMessage({
      id: draftId,
      sender: "Draft",
      subject: composeSubject.trim() || "Untitled draft",
      snippet:
        bodyPreview.length > 0
          ? bodyPreview.replace(/\s+/g, " ").slice(0, 96)
          : "Draft message",
      time: "Just now",
      createdAt: new Date().toISOString(),
      signal: "Draft",
      from: activeComposeMailbox.email,
      to: composeTo.trim() || "No recipient yet",
      timestamp: "Saved just now",
      body: bodyParagraphs.length > 0 ? bodyParagraphs : ["Draft message"],
      bodyHtml: composeBody,
      signature: undefined,
      attachments: composeAttachments,
      cc: composeCc.trim() || undefined,
    }, activeComposeMailbox.id, senderCategoryLearning, messageOwnershipInteractions, currentUserId, mailboxStore);

    setMailboxStore((currentStore) => ({
      ...currentStore,
      [activeComposeMailbox.id]: {
        ...currentStore[activeComposeMailbox.id],
        Drafts: [draftMessage, ...currentStore[activeComposeMailbox.id].Drafts],
      },
    }));
    if (activeComposeMailbox.id === mailbox.id) {
      setActiveFolder("Drafts");
    }
    setSelectionState([draftId], draftId, draftId);
    setIsFullMessageOpen(false);
    setIsCloseModalOpen(false);
    setIsComposeOpen(false);
    resetComposeState();
  };

  const discardCompose = () => {
    setIsCloseModalOpen(false);
    setIsComposeOpen(false);
    resetComposeState();
  };

  const sendMessage = async () => {
    if (isSendingCompose) {
      return;
    }

    const managedMailbox = managedInboxes.find(
      (candidate) => candidate.id === activeComposeMailbox.id,
    );

    if (
      !managedMailbox ||
      !managedMailbox.connected ||
      managedMailbox.provider !== "google"
    ) {
      setComposeSendError("This mailbox is not ready for Gmail sending.");
      return;
    }

    const resolvedImapSettings = applyProviderDefaults(
      managedMailbox.provider,
      managedMailbox.customImap,
      managedMailbox.email,
    );
    const toRecipients = composeTo.trim();

    if (!toRecipients) {
      setComposeSendError("Add at least one recipient before sending.");
      return;
    }

    if (!resolvedImapSettings.password.trim()) {
      setComposeSendError("Gmail credentials are missing for this mailbox.");
      return;
    }

    setComposeSendError(null);
    setIsSendingCompose(true);

    try {
      const serializedAttachments: SendInboxAttachmentRequest[] = [];
      const attachmentPayloads = await Promise.all(
        composeAttachments.map(serializeComposeAttachment),
      );

      attachmentPayloads.forEach((attachment) => {
        if (attachment) {
          serializedAttachments.push(attachment);
        }
      });

      const bodyPreview = extractComposePlainText(composeBody);
      const sendResponse = await sendGmailMessage({
        provider: managedMailbox.provider,
        email: managedMailbox.email.trim(),
        username:
          managedMailbox.provider === "google"
            ? managedMailbox.email.trim()
            : resolvedImapSettings.username.trim(),
        password: resolvedImapSettings.password,
        from: activeComposeMailbox.email,
        to: toRecipients,
        cc: composeCc.trim() || undefined,
        bcc: composeBcc.trim() || undefined,
        subject: composeSubject.trim() || "Untitled message",
        bodyHtml: composeBody,
        bodyText: bodyPreview || " ",
        attachments: serializedAttachments,
      });

      if (!sendResponse.ok) {
        setComposeSendError(sendResponse.error?.message ?? "Could not send email.");
        return;
      }

      rememberSentRecipients([
        toRecipients,
        composeCc.trim() || undefined,
        composeBcc.trim() || undefined,
      ]);

      const sentId = `${activeComposeMailbox.id}-sent-${Date.now()}`;
      const bodyParagraphs = extractComposeParagraphs(composeBody);
      const sentMessage = normalizeMailMessage({
        id: sentId,
        threadId:
          composeMode === "reply" || composeMode === "reply_all"
            ? composeSourceMessage?.threadId ??
              resolveMailThreadId({
                subject:
                  composeSourceMessage?.subject ??
                  (composeSubject.trim() || "Untitled message"),
              })
            : undefined,
        sender: "You",
        subject: composeSubject.trim() || "Untitled message",
        snippet:
          bodyPreview.length > 0
            ? bodyPreview.replace(/\s+/g, " ").slice(0, 96)
            : "Message sent",
        time: "Now",
        createdAt: new Date().toISOString(),
        signal: "Sent",
        from: activeComposeMailbox.email,
        to: composeTo.trim() || "No recipient yet",
        timestamp: "Sent just now",
        body: bodyParagraphs.length > 0 ? bodyParagraphs : ["Message sent"],
        bodyHtml: composeBody,
        signature: undefined,
        attachments: composeAttachments,
        cc: composeCc.trim() || undefined,
      }, activeComposeMailbox.id, senderCategoryLearning, messageOwnershipInteractions, currentUserId, mailboxStore);

      setMailboxStore((currentStore) => ({
        ...currentStore,
        [activeComposeMailbox.id]: {
          ...currentStore[activeComposeMailbox.id],
          Sent: [sentMessage, ...currentStore[activeComposeMailbox.id].Sent],
        },
      }));

      if (
        composeSourceMessage &&
        (composeMode === "reply" ||
          composeMode === "reply_all" ||
          composeMode === "forward") &&
        isVisiblePriorityMessage(composeSourceMessage)
      ) {
        onSetManualPriority(composeSourceMessage.id, false);
      }

      if ((composeMode === "reply" || composeMode === "reply_all") && composeSourceMessage) {
        setIsComposeOpen(false);
        setSelectionState(
          [composeSourceMessage.id],
          composeSourceMessage.id,
          composeSourceMessage.id,
        );
        resetComposeState();
        return;
      }

      setActiveFolder("Sent");
      setSelectionState([sentId], sentId, sentId);
      setIsFullMessageOpen(false);
      setIsComposeOpen(false);
      resetComposeState();
    } catch (error) {
      setComposeSendError(
        error instanceof Error ? error.message : "Could not prepare email for sending.",
      );
    } finally {
      setIsSendingCompose(false);
    }
  };

  const closeMenus = () => {
    setIsSortMenuOpen(false);
    setIsMoreMenuOpen(false);
    setIsReadingLearningMenuOpen(false);
    setActiveReadingLearningTrigger(null);
    setDetailActionsMenuState(null);
    setContextMenuState(null);
    setSmartFolderMenuId(null);
  };

  const toggleReadingLearningMenu = (
    trigger: "reading-pane" | "full-message",
    anchor: { top: number; left: number; width: number; height: number },
  ) => {
    setIsMoreMenuOpen(false);
    setIsSortMenuOpen(false);
    setContextMenuState(null);

    if (isReadingLearningMenuOpen && activeReadingLearningTrigger === trigger) {
      setIsReadingLearningMenuOpen(false);
      setActiveReadingLearningTrigger(null);
      return;
    }

    setReadingLearningMenuAnchor(anchor);
    setActiveReadingLearningTrigger(trigger);
    setIsReadingLearningMenuOpen(true);
  };

  useEffect(() => {
    if (
      !isMoreMenuOpen &&
      !isSortMenuOpen &&
      !isReadingLearningMenuOpen &&
      !detailActionsMenuState &&
      !contextMenuState &&
      !smartFolderMenuId &&
      !shareCollaborationMessageId &&
      !activeCollaborationMessageId
    ) {
      return;
    }

    const handleDismiss = (event: globalThis.MouseEvent) => {
      if (
        isReadingLearningMenuOpen &&
        event.target instanceof Element &&
        (
          readingLearningMenuRef.current?.contains(event.target) ||
          event.target.closest("[data-reading-learning-trigger]")
        )
      ) {
        return;
      }

      if (
        event.target instanceof Element &&
        (
          detailActionsMenuRef.current?.contains(event.target) ||
          event.target.closest("[data-detail-actions-trigger]")
        )
      ) {
        return;
      }

      if (
        event.target instanceof Element &&
        (
          event.target.closest("[data-share-collaboration-modal]") ||
          event.target.closest("[data-collaboration-thread-modal]")
        )
      ) {
        return;
      }

      closeMenus();
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeMenus();
      }
    };

    window.addEventListener("mousedown", handleDismiss);
    window.addEventListener("keydown", handleEscape);

    return () => {
      window.removeEventListener("mousedown", handleDismiss);
      window.removeEventListener("keydown", handleEscape);
    };
  }, [
    contextMenuState,
    detailActionsMenuState,
    activeCollaborationMessageId,
    isMoreMenuOpen,
    isReadingLearningMenuOpen,
    shareCollaborationMessageId,
    smartFolderMenuId,
    isSortMenuOpen,
  ]);

  useEffect(() => {
    const availableMessageIds = new Set(folderMessages.map((message) => message.id));
    const nextSelectedMessageIds = selectedMessageIds.filter((messageId) =>
      availableMessageIds.has(messageId),
    );
    if (nextSelectedMessageIds.length !== selectedMessageIds.length) {
      setSelectedMessageIds(nextSelectedMessageIds);
    }

    if (selectedMessageId && !availableMessageIds.has(selectedMessageId)) {
      setSelectedMessageId(nextSelectedMessageIds[0] ?? null);
    }

    if (selectionAnchorId && !availableMessageIds.has(selectionAnchorId)) {
      setSelectionAnchorId(nextSelectedMessageIds[0] ?? null);
    }
  }, [
    folderMessages,
    selectedMessageId,
    selectedMessageIds,
    selectionAnchorId,
  ]);

  useEffect(() => {
    if (selectedMessageIds.length === 1 && selectionAnchorId !== selectedMessageIds[0]) {
      setSelectionAnchorId(selectedMessageIds[0]);
    }

    if (selectedMessageIds.length === 0 && selectionAnchorId !== null) {
      setSelectionAnchorId(null);
    }
  }, [selectedMessageIds, selectionAnchorId]);

  useEffect(() => {
    return () => {
      dragPreviewCleanupRef.current?.();
    };
  }, []);

  useEffect(() => {
    if (
      !isMoreMenuOpen &&
      !isSortMenuOpen &&
      !isReadingLearningMenuOpen &&
      !contextMenuState
    ) {
      return;
    }

    const originalBodyOverflow = document.body.style.overflow;
    const originalHtmlOverflow = document.documentElement.style.overflow;

    document.body.style.overflow = "hidden";
    document.documentElement.style.overflow = "hidden";

    return () => {
      document.body.style.overflow = originalBodyOverflow;
      document.documentElement.style.overflow = originalHtmlOverflow;
    };
  }, [
    contextMenuState,
    isMoreMenuOpen,
    isReadingLearningMenuOpen,
    isSortMenuOpen,
  ]);

  const updateFolderMessages = (
    folder: MailFolder,
    updater: (messages: MailMessage[]) => MailMessage[],
  ) => {
    setMailboxStore((currentStore) => ({
      ...currentStore,
      [mailbox.id]: {
        ...currentStore[mailbox.id],
        [folder]: updater(currentStore[mailbox.id][folder]),
      },
    }));
  };

  const getMessagesByIds = (messageIds: string[]) => {
    if (messageIds.length === 0) {
      return [];
    }

    const messageIdSet = new Set(messageIds);

    return Object.values(mailboxStore).flatMap((collections) =>
      canonicalFolderOrder.flatMap((folder) =>
        collections[folder].filter((message) => messageIdSet.has(message.id)),
      ),
    );
  };

  const updateMessageById = (
    messageId: string,
    updater: (message: MailMessage) => MailMessage,
  ) => {
    setMailboxStore((currentStore) =>
      Object.entries(currentStore).reduce<MailboxStore>((nextStore, [mailboxId, collections]) => {
        nextStore[mailboxId as InboxId] = {
          Inbox: collections.Inbox.map((message) =>
            message.id === messageId ? updater(message) : message,
          ),
          Drafts: collections.Drafts.map((message) =>
            message.id === messageId ? updater(message) : message,
          ),
          Sent: collections.Sent.map((message) =>
            message.id === messageId ? updater(message) : message,
          ),
          Archive: collections.Archive.map((message) =>
            message.id === messageId ? updater(message) : message,
          ),
          Filtered: collections.Filtered.map((message) =>
            message.id === messageId ? updater(message) : message,
          ),
          Spam: collections.Spam.map((message) =>
            message.id === messageId ? updater(message) : message,
          ),
          Trash: collections.Trash.map((message) =>
            message.id === messageId ? updater(message) : message,
          ),
        };

        return nextStore;
      }, {} as MailboxStore),
    );
  };

  function getMessageById(messageId: string | null) {
    if (!messageId) {
      return null;
    }

    return Object.values(mailboxStore)
      .flatMap((collections) => canonicalFolderOrder.flatMap((folder) => collections[folder]))
      .find((message) => message.id === messageId) ?? null;
  }

  const setSelectionState = (
    nextSelectedMessageIds: string[],
    nextPrimaryMessageId: string | null,
    nextAnchorId?: string | null,
  ) => {
    setSelectedMessageIds(nextSelectedMessageIds);
    setSelectedMessageId(nextPrimaryMessageId);
    if (nextAnchorId !== undefined) {
      setSelectionAnchorId(nextAnchorId);
    }
  };

  const getEffectiveSelectionIds = (messageId?: string | null) => {
    if (messageId && selectedMessageIds.includes(messageId)) {
      return selectedMessageIds;
    }

    return messageId ? [messageId] : selectedMessageIds;
  };

  const openShareCollaboration = (messageId: string) => {
    const message = getMessageById(messageId);

    if (message?.collaboration && message.collaboration.state !== "resolved") {
      openCollaborationOverlay(messageId);
      return;
    }

    setSelectionState([messageId], messageId, messageId);
    setContextMenuState(null);
    setDetailActionsMenuState(null);
    setIsMoreMenuOpen(false);
    setIsSortMenuOpen(false);
    setShareCollaborationMessageId(messageId);
    setCollaborationRequestType("needs_review");
    setCollaborationPersonId(collaborationPeople[0]?.id ?? "");
    setIsInlineCollaborationInviteOpen(false);
    setCollaborationInviteDraft("");
    setCollaborationInviteOptions([]);
    setCollaborationNote("");
  };

  const closeShareCollaboration = () => {
    setShareCollaborationMessageId(null);
    setIsInlineCollaborationInviteOpen(false);
    setCollaborationInviteDraft("");
    setCollaborationInviteOptions([]);
    setCollaborationNote("");
  };

  const openCollaborationOverlay = (
    messageId: string,
    options?: { focusComposer?: boolean },
  ) => {
    setSelectionState([messageId], messageId, messageId);
    setActiveCollaborationMessageId(messageId);
    setFocusCollaborationComposer(Boolean(options?.focusComposer));
    setContextMenuState(null);
    setDetailActionsMenuState(null);
  };

  const closeCollaborationOverlay = () => {
    setActiveCollaborationMessageId(null);
    setCollaborationReplyDraft("");
    setCollaborationReplyVisibility("internal");
    setCollaborationReplySelection(null);
    setHighlightedCollaborationMessageId(null);
    setIsInviteParticipantOpen(false);
    setCollaborationInviteEmail("");
    setCollaborationInvitePersonId("");
    setFocusCollaborationComposer(false);
  };

  const syncCollaborationMentionState = (
    value: string,
    textarea: HTMLTextAreaElement | null,
  ) => {
    setCollaborationReplySelection(textarea?.selectionStart ?? null);
    const nextQuery = getMentionQueryAtCursor(value, textarea?.selectionStart ?? null);

    if (!nextQuery) {
      setCollaborationMentionIndex(0);
      return;
    }

    const nextMatches = collaborationMentionCandidates.filter((candidate) =>
      candidate.handle.toLowerCase().includes(nextQuery.query.toLowerCase()),
    );

    if (nextMatches.length === 0) {
      setCollaborationMentionIndex(0);
      return;
    }

    setCollaborationMentionIndex((current) => Math.min(current, nextMatches.length - 1));
  };

  const applyCollaborationMention = (
    candidate: (typeof collaborationMentionCandidates)[number],
  ) => {
    const textarea = collaborationReplyInputRef.current;
    const selection = getMentionQueryAtCursor(
      collaborationReplyDraft,
      textarea?.selectionStart ?? null,
    );

    if (!selection) {
      return;
    }

    const nextValue = `${collaborationReplyDraft.slice(0, selection.start)}@${
      candidate.handle
    } ${collaborationReplyDraft.slice(selection.end)}`;

    setCollaborationReplyDraft(nextValue);
    setCollaborationMentionIndex(0);
    setCollaborationReplySelection(selection.start + candidate.handle.length + 2);

    requestAnimationFrame(() => {
      if (!textarea) {
        return;
      }

      const nextCursorPosition = selection.start + candidate.handle.length + 2;
      textarea.focus();
      textarea.setSelectionRange(nextCursorPosition, nextCursorPosition);
    });
  };

  const markMessageCollaborationDone = (messageId: string) => {
    const nextTimestamp = Date.now();

    updateMessageById(messageId, (message) =>
      message.collaboration
        ? {
            ...message,
            isShared: false,
            collaboration: {
              ...message.collaboration,
              state: "resolved",
              updatedAt: nextTimestamp,
              resolvedAt: nextTimestamp,
              resolvedByUserId: currentUserId,
              resolvedByUserName: currentUserName,
            },
          }
        : message,
    );
  };

  const createMessageCollaboration = () => {
    if (!shareCollaborationMessageId || !collaborationPersonId) {
      return;
    }

    const selectedPerson = collaborationSelectablePeople.find(
      (person) => person.id === collaborationPersonId,
    );

    if (!selectedPerson) {
      return;
    }

    const nextTimestamp = Date.now();
    const trimmedNote = collaborationNote.trim();
    const initialMentionCandidates = getCollaborationMentionTargets(
      [
        {
          id: selectedPerson.id,
          name: selectedPerson.name,
          email: selectedPerson.email,
          kind: selectedPerson.kind,
          status: selectedPerson.status,
        },
      ],
      collaborationPeople,
    );
    const initialMessages = trimmedNote
      ? [
          {
            id: `${shareCollaborationMessageId}-collaboration-${nextTimestamp}`,
            authorId: currentUserId,
            authorName: currentUserName,
            text: trimmedNote,
            timestamp: nextTimestamp,
            visibility: "shared" as const,
            mentions: extractCollaborationMentions(
              trimmedNote,
              initialMentionCandidates,
              currentUserId,
            ),
          },
        ]
      : [];

    updateMessageById(shareCollaborationMessageId, (message) => ({
      ...message,
      isShared: true,
      collaboration: {
        state: collaborationRequestType,
        requestedBy: currentUserName,
        requestedUserId: selectedPerson.id,
        requestedUserName: selectedPerson.name,
        createdAt: nextTimestamp,
        updatedAt: nextTimestamp,
        participants: [
          {
            id: selectedPerson.id,
            name: selectedPerson.name,
            email: selectedPerson.email,
            kind: selectedPerson.kind,
            status: selectedPerson.status,
          },
        ],
        previewText: trimmedNote || undefined,
        messages: initialMessages,
      },
    }));
    closeShareCollaboration();
  };

  const sendCollaborationReply = (messageId: string) => {
    const trimmedReply = collaborationReplyDraft.trim();

    if (!trimmedReply) {
      return;
    }

    const nextTimestamp = Date.now();
    const mentions = extractCollaborationMentions(
      trimmedReply,
      collaborationMentionCandidates,
      currentUserId,
    );

    updateMessageById(messageId, (message) =>
      message.collaboration
        ? {
            ...message,
            isShared: true,
            collaboration: {
              ...message.collaboration,
              state:
                message.collaboration.state === "resolved"
                  ? "needs_review"
                  : message.collaboration.state,
              updatedAt: nextTimestamp,
              previewText: trimmedReply,
              messages: [
                ...message.collaboration.messages,
                {
                  id: `${messageId}-collaboration-reply-${nextTimestamp}`,
                  authorId: currentUserId,
                  authorName: currentUserName,
                  text: trimmedReply,
                  timestamp: nextTimestamp,
                  visibility: collaborationReplyVisibility,
                  mentions,
                },
              ],
            },
          }
        : message,
    );
    setCollaborationReplyDraft("");
    setCollaborationReplyVisibility("internal");
    setCollaborationMentionIndex(0);
    setCollaborationReplySelection(null);
  };

  const addParticipantToCollaboration = (messageId: string) => {
    const trimmedEmail = collaborationInviteEmail.trim().toLowerCase();
    const selectedPerson = collaborationPeople.find(
      (person) => person.id === collaborationInvitePersonId,
    );

    if (!selectedPerson && !trimmedEmail) {
      return;
    }

    const nextTimestamp = Date.now();

    updateMessageById(messageId, (message) => {
      if (!message.collaboration) {
        return message;
      }

      const existingParticipants = getCollaborationParticipants(message.collaboration);
      const matchedPersonByEmail = trimmedEmail
        ? collaborationPeople.find((person) => person.email.toLowerCase() === trimmedEmail)
        : null;
      const candidateParticipant: MailMessageCollaborationParticipant | null = selectedPerson
        ? {
            id: selectedPerson.id,
            name: selectedPerson.name,
            email: selectedPerson.email,
            kind: "internal",
            status: "active",
          }
        : trimmedEmail
          ? {
              id: matchedPersonByEmail?.id ?? `invite-${trimmedEmail}`,
              name: matchedPersonByEmail?.name ?? trimmedEmail,
              email: trimmedEmail,
              kind: matchedPersonByEmail || trimmedEmail.endsWith("@cuevion.com")
                ? "internal"
                : "external",
              status: matchedPersonByEmail
                ? "active"
                : "invited",
            }
          : null;

      if (!candidateParticipant) {
        return message;
      }

      const alreadyExists = existingParticipants.some((participant) =>
        participant.id === candidateParticipant.id ||
        (Boolean(participant.email) &&
          participant.email.toLowerCase() === candidateParticipant.email.toLowerCase()),
      );

      if (alreadyExists) {
        return message;
      }

      return {
        ...message,
        collaboration: {
          ...message.collaboration,
          updatedAt: nextTimestamp,
          participants: [...(message.collaboration.participants ?? []), candidateParticipant],
        },
      };
    });

    setIsInviteParticipantOpen(false);
    setCollaborationInviteEmail("");
    setCollaborationInvitePersonId("");
  };

  const removeParticipantFromCollaboration = (
    messageId: string,
    participantId: string,
  ) => {
    updateMessageById(messageId, (message) =>
      message.collaboration
        ? {
            ...message,
            collaboration: {
              ...message.collaboration,
              updatedAt: Date.now(),
              participants: (message.collaboration.participants ?? []).filter(
                (participant) => participant.id !== participantId,
              ),
            },
          }
        : message,
    );
  };

  const addInlineCollaborationInvite = () => {
    const trimmedEmail = collaborationInviteDraft.trim().toLowerCase();

    if (!trimmedEmail) {
      return;
    }

    const matchedPerson = collaborationPeople.find(
      (person) => person.email.toLowerCase() === trimmedEmail,
    );

    if (matchedPerson) {
      setCollaborationPersonId(matchedPerson.id);
      setIsInlineCollaborationInviteOpen(false);
      setCollaborationInviteDraft("");
      return;
    }

    const inviteId = `invite-${trimmedEmail}`;
    const alreadyExists = collaborationInviteOptions.some(
      (person) => person.email.toLowerCase() === trimmedEmail || person.id === inviteId,
    );

    if (!alreadyExists) {
      setCollaborationInviteOptions((current) => [
        ...current,
        {
          id: inviteId,
          name: trimmedEmail,
          email: trimmedEmail,
          kind: "external",
          status: "invited",
        },
      ]);
    }

    setCollaborationPersonId(inviteId);
    setIsInlineCollaborationInviteOpen(false);
    setCollaborationInviteDraft("");
  };

  const copyInviteLink = async (message: MailMessage, email: string) => {
    const inviteLink = buildCollaborationInviteLink(message, email);

    if (!inviteLink) {
      return;
    }

    try {
      await navigator.clipboard.writeText(inviteLink);
      const copyKey = `${message.id}:${email.toLowerCase()}`;
      if (isLocalDevelopmentEnvironment()) {
        console.debug("cuevion_invite_link_copied", {
          inviteLink,
          inviteToken: buildCollaborationInviteToken(message, email),
          inviteeEmail: email.toLowerCase(),
          messageId: message.id,
        });
      }
      setCopiedInviteLinkKey(copyKey);
      window.setTimeout(() => {
        setCopiedInviteLinkKey((current) => (current === copyKey ? null : current));
      }, 1800);
    } catch {
      setCopiedInviteLinkKey(null);
    }
  };

  const setMessagesUnreadState = (
    folder: MailFolder,
    messageIds: string[],
    unread: boolean,
  ) => {
    if (messageIds.length === 0) {
      closeMenus();
      return;
    }

    onSyncUnreadOverrides(getMessagesByIds(messageIds), unread);

    if (isSharedView || activeSmartFolder) {
      const messageIdSet = new Set(messageIds);

      setMailboxStore((currentStore) =>
        Object.entries(currentStore).reduce<MailboxStore>((nextStore, [mailboxId, collections]) => {
          nextStore[mailboxId as InboxId] = {
            Inbox: collections.Inbox.map((message) =>
              messageIdSet.has(message.id) ? { ...message, unread } : message,
            ),
            Drafts: collections.Drafts.map((message) =>
              messageIdSet.has(message.id) ? { ...message, unread } : message,
            ),
            Sent: collections.Sent.map((message) =>
              messageIdSet.has(message.id) ? { ...message, unread } : message,
            ),
            Archive: collections.Archive.map((message) =>
              messageIdSet.has(message.id) ? { ...message, unread } : message,
            ),
            Filtered: collections.Filtered.map((message) =>
              messageIdSet.has(message.id) ? { ...message, unread } : message,
            ),
            Spam: collections.Spam.map((message) =>
              messageIdSet.has(message.id) ? { ...message, unread } : message,
            ),
            Trash: collections.Trash.map((message) =>
              messageIdSet.has(message.id) ? { ...message, unread } : message,
            ),
          };
          return nextStore;
        }, {} as MailboxStore),
      );
      if (activeFilter === "Unread" && unread === false) {
        advanceSelectionAfterAction(messageIds);
      }
      closeMenus();
      return;
    }

    const messageIdSet = new Set(messageIds);
    updateFolderMessages(folder, (messages) =>
      messages.map((message) =>
        messageIdSet.has(message.id) ? { ...message, unread } : message,
      ),
    );
    if (activeFilter === "Unread" && unread === false) {
      advanceSelectionAfterAction(messageIds);
    }
    closeMenus();
  };

  const setMessageUnreadState = (
    folder: MailFolder,
    messageId: string,
    unread: boolean,
  ) => {
    setMessagesUnreadState(folder, [messageId], unread);
  };

  const markInboxMessageReadOnOpen = (message: MailMessage) => {
    if (activeFolder !== "Inbox" || isSharedView || activeSmartFolder) {
      return;
    }

    onSyncUnreadOverrides([message], false);
    updateFolderMessages("Inbox", (messages) =>
      messages.map((entry) =>
        entry.id === message.id
          ? {
              ...entry,
              imapUid: message.imapUid ?? entry.imapUid,
              unread: false,
            }
          : entry,
      ),
    );
  };

  const toggleMessageFlagState = (messageId: string) => {
    updateMessageById(messageId, (message) => ({
      ...message,
      flagged: !message.flagged,
    }));
    closeMenus();
  };

  const acceptCategorySuggestion = (
    folder: MailFolder,
    messageId: string,
    senderAddress: string,
    proposedCategory: CuevionMessageCategory,
  ) => {
    setResolvingSuggestionIds((current) =>
      current.includes(messageId) ? current : [...current, messageId],
    );
    window.setTimeout(() => {
      updateMessageById(messageId, (message) => ({
        ...message,
        category: proposedCategory,
        categorySource: "user",
        categoryConfidence: "high",
        suggestion: undefined,
      }));
      onLearnCategoryDecision(senderAddress, proposedCategory);
      onRecordMessageOwnershipInteraction(messageId);
      setResolvingSuggestionIds((current) =>
        current.filter((currentMessageId) => currentMessageId !== messageId),
      );
    }, 120);
  };

  const dismissCategorySuggestion = (folder: MailFolder, messageId: string) => {
    setResolvingSuggestionIds((current) =>
      current.includes(messageId) ? current : [...current, messageId],
    );
    window.setTimeout(() => {
      updateMessageById(messageId, (message) => ({
        ...message,
        suggestionDismissed: true,
        suggestion: undefined,
      }));
      setResolvingSuggestionIds((current) =>
        current.filter((currentMessageId) => currentMessageId !== messageId),
      );
    }, 120);
  };

  const enableAutoCategoryBehavior = (
    folder: MailFolder,
    messageId: string,
    senderAddress: string,
  ) => {
    setResolvingBehaviorSuggestionIds((current) =>
      current.includes(messageId) ? current : [...current, messageId],
    );
    window.setTimeout(() => {
      updateMessageById(messageId, (message) => ({
        ...message,
        behaviorSuggestionDismissed: true,
      }));
      onEnableAutoCategoryForSender(senderAddress);
      onRecordMessageOwnershipInteraction(messageId);
      setResolvingBehaviorSuggestionIds((current) =>
        current.filter((currentMessageId) => currentMessageId !== messageId),
      );
    }, 120);
  };

  const dismissBehaviorSuggestion = (folder: MailFolder, messageId: string) => {
    setResolvingBehaviorSuggestionIds((current) =>
      current.includes(messageId) ? current : [...current, messageId],
    );
    window.setTimeout(() => {
      updateMessageById(messageId, (message) => ({
        ...message,
        behaviorSuggestionDismissed: true,
      }));
      setResolvingBehaviorSuggestionIds((current) =>
        current.filter((currentMessageId) => currentMessageId !== messageId),
      );
    }, 120);
  };

  const handleSelectMessage = (
    _folder: MailFolder,
    messageId: string,
    options?: {
      openFull?: boolean;
      isToggle?: boolean;
      isRange?: boolean;
      triggerAutoRead?: boolean;
    },
  ) => {
    const sortedMessageIds = sortedMessages.map((message) => message.id);
    if (options?.isRange) {
      const anchorId =
        selectionAnchorId && sortedMessageIds.includes(selectionAnchorId)
          ? selectionAnchorId
          : selectedMessageId && sortedMessageIds.includes(selectedMessageId)
            ? selectedMessageId
            : messageId;
      const anchorIndex = sortedMessageIds.indexOf(anchorId);
      const targetIndex = sortedMessageIds.indexOf(messageId);

      if (anchorIndex !== -1 && targetIndex !== -1) {
        const [startIndex, endIndex] =
          anchorIndex <= targetIndex
            ? [anchorIndex, targetIndex]
            : [targetIndex, anchorIndex];
        const rangeIds = sortedMessageIds.slice(startIndex, endIndex + 1);

        setSelectionState(rangeIds, messageId, anchorId);
      } else {
        setSelectionState([messageId], messageId, messageId);
      }
      setIsFullMessageOpen(false);
      return;
    }

    if (options?.isToggle) {
      const alreadySelected = selectedMessageIds.includes(messageId);

      if (alreadySelected) {
        const remainingMessageIds = selectedMessageIds.filter(
          (selectedId) => selectedId !== messageId,
        );
        const nextPrimaryMessageId = remainingMessageIds.includes(
          selectedMessageId ?? "",
        )
          ? selectedMessageId
          : remainingMessageIds[remainingMessageIds.length - 1] ?? null;

        setSelectionState(
          remainingMessageIds,
          nextPrimaryMessageId,
          remainingMessageIds.length > 0
            ? selectionAnchorId && remainingMessageIds.includes(selectionAnchorId)
              ? selectionAnchorId
              : nextPrimaryMessageId
            : null,
        );
      } else {
        setSelectionState(
          [...selectedMessageIds, messageId],
          messageId,
          messageId,
        );
      }
      setIsFullMessageOpen(false);
      return;
    }

    // Normal click must always leave multi-select mode immediately and reset the
    // range anchor to the clicked message.
    setSelectionState([messageId], messageId, messageId);
    setIsFullMessageOpen(Boolean(options?.openFull));
    onRecordMessageOwnershipInteraction(messageId);
  };

  const moveMessagesAcrossWorkspace = (
    targetMailboxId: InboxId,
    targetFolder: MailFolder,
    messageIds: string[],
  ) => {
    if (messageIds.length === 0) {
      closeMenus();
      return;
    }

    const messageIdSet = new Set(messageIds);
    const messagesToMove = Object.values(mailboxStore).flatMap((collections) =>
      canonicalFolderOrder.flatMap((folder) =>
        collections[folder].filter((message) => messageIdSet.has(message.id)),
      ),
    );

    if (targetFolder === "Filtered") {
      applyFilteredLearningFromMessages(messagesToMove, onSaveLearningRule);
    }

    setMailboxStore((currentStore) => {
      const currentMessagesToMove = Object.values(currentStore).flatMap((collections) =>
        canonicalFolderOrder.flatMap((folder) =>
          collections[folder].filter((message) => messageIdSet.has(message.id)),
        ),
      );

      if (currentMessagesToMove.length === 0) {
        return currentStore;
      }

      return Object.entries(currentStore).reduce<MailboxStore>((nextStore, [mailboxId, collections]) => {
        const typedMailboxId = mailboxId as InboxId;

        nextStore[typedMailboxId] = {
          Inbox:
            typedMailboxId === targetMailboxId && targetFolder === "Inbox"
              ? [
                  ...currentMessagesToMove,
                  ...collections.Inbox.filter((message) => !messageIdSet.has(message.id)),
                ]
              : collections.Inbox.filter((message) => !messageIdSet.has(message.id)),
          Drafts:
            typedMailboxId === targetMailboxId && targetFolder === "Drafts"
              ? [
                  ...currentMessagesToMove,
                  ...collections.Drafts.filter((message) => !messageIdSet.has(message.id)),
                ]
              : collections.Drafts.filter((message) => !messageIdSet.has(message.id)),
          Sent:
            typedMailboxId === targetMailboxId && targetFolder === "Sent"
              ? [
                  ...currentMessagesToMove,
                  ...collections.Sent.filter((message) => !messageIdSet.has(message.id)),
                ]
              : collections.Sent.filter((message) => !messageIdSet.has(message.id)),
          Archive:
            typedMailboxId === targetMailboxId && targetFolder === "Archive"
              ? [
                  ...currentMessagesToMove,
                  ...collections.Archive.filter((message) => !messageIdSet.has(message.id)),
                ]
              : collections.Archive.filter((message) => !messageIdSet.has(message.id)),
          Filtered:
            typedMailboxId === targetMailboxId && targetFolder === "Filtered"
              ? [
                  ...currentMessagesToMove,
                  ...collections.Filtered.filter((message) => !messageIdSet.has(message.id)),
                ]
              : collections.Filtered.filter((message) => !messageIdSet.has(message.id)),
          Spam:
            typedMailboxId === targetMailboxId && targetFolder === "Spam"
              ? [
                  ...currentMessagesToMove,
                  ...collections.Spam.filter((message) => !messageIdSet.has(message.id)),
                ]
              : collections.Spam.filter((message) => !messageIdSet.has(message.id)),
          Trash:
            typedMailboxId === targetMailboxId && targetFolder === "Trash"
              ? [
                  ...currentMessagesToMove,
                  ...collections.Trash.filter((message) => !messageIdSet.has(message.id)),
                ]
              : collections.Trash.filter((message) => !messageIdSet.has(message.id)),
        };

        return nextStore;
      }, {} as MailboxStore);
    });

    if (isSharedView || activeSmartFolder) {
      advanceSelectionAfterAction(messageIds);
    }

    closeMenus();
  };

  const moveMessagesToFolderAcrossWorkspace = (
    targetFolder: MailFolder,
    messageIds: string[],
  ) => {
    if (messageIds.length === 0) {
      closeMenus();
      return;
    }

    const groupedMessageIds = messageIds.reduce<Partial<Record<InboxId, string[]>>>(
      (groups, messageId) => {
        const messageLocation = currentMessageLocationById[messageId];

        if (!messageLocation) {
          return groups;
        }

        groups[messageLocation.mailboxId] = [
          ...(groups[messageLocation.mailboxId] ?? []),
          messageId,
        ];
        return groups;
      },
      {},
    );

    if (targetFolder === "Filtered") {
      const groupedMessagesToMove = Object.entries(groupedMessageIds).flatMap(([mailboxId, ids]) => {
        const typedMailboxId = mailboxId as InboxId;
        const messageIdSet = new Set(ids ?? []);
        const mailboxCollections = mailboxStore[typedMailboxId];

        if (!mailboxCollections) {
          return [];
        }

        return canonicalFolderOrder.flatMap((folder) =>
          mailboxCollections[folder].filter((message) => messageIdSet.has(message.id)),
        );
      });

      applyFilteredLearningFromMessages(groupedMessagesToMove, onSaveLearningRule);
    }

    setMailboxStore((currentStore) => {
      const nextStore = { ...currentStore };

      Object.entries(groupedMessageIds).forEach(([mailboxId, ids]) => {
        const typedMailboxId = mailboxId as InboxId;
        const messageIdSet = new Set(ids ?? []);
        const mailboxCollections = currentStore[typedMailboxId];
        const messagesToMove = canonicalFolderOrder.flatMap((folder) =>
          mailboxCollections[folder].filter((message) => messageIdSet.has(message.id)),
        );

        if (messagesToMove.length === 0) {
          return;
        }

        nextStore[typedMailboxId] = {
          Inbox:
            targetFolder === "Inbox"
              ? [
                  ...messagesToMove,
                  ...mailboxCollections.Inbox.filter((message) => !messageIdSet.has(message.id)),
                ]
              : mailboxCollections.Inbox.filter((message) => !messageIdSet.has(message.id)),
          Drafts:
            targetFolder === "Drafts"
              ? [
                  ...messagesToMove,
                  ...mailboxCollections.Drafts.filter((message) => !messageIdSet.has(message.id)),
                ]
              : mailboxCollections.Drafts.filter((message) => !messageIdSet.has(message.id)),
          Sent:
            targetFolder === "Sent"
              ? [
                  ...messagesToMove,
                  ...mailboxCollections.Sent.filter((message) => !messageIdSet.has(message.id)),
                ]
              : mailboxCollections.Sent.filter((message) => !messageIdSet.has(message.id)),
          Archive:
            targetFolder === "Archive"
              ? [
                  ...messagesToMove,
                  ...mailboxCollections.Archive.filter((message) => !messageIdSet.has(message.id)),
                ]
              : mailboxCollections.Archive.filter((message) => !messageIdSet.has(message.id)),
          Filtered:
            targetFolder === "Filtered"
              ? [
                  ...messagesToMove,
                  ...mailboxCollections.Filtered.filter((message) => !messageIdSet.has(message.id)),
                ]
              : mailboxCollections.Filtered.filter((message) => !messageIdSet.has(message.id)),
          Spam:
            targetFolder === "Spam"
              ? [
                  ...messagesToMove,
                  ...mailboxCollections.Spam.filter((message) => !messageIdSet.has(message.id)),
                ]
              : mailboxCollections.Spam.filter((message) => !messageIdSet.has(message.id)),
          Trash:
            targetFolder === "Trash"
              ? [
                  ...messagesToMove,
                  ...mailboxCollections.Trash.filter((message) => !messageIdSet.has(message.id)),
                ]
              : mailboxCollections.Trash.filter((message) => !messageIdSet.has(message.id)),
        };
      });

      return nextStore;
    });
    closeMenus();
  };

  const moveMessages = (
    sourceMailboxId: InboxId,
    sourceFolder: MailFolder,
    targetMailboxId: InboxId,
    targetFolder: MailFolder,
    messageIds: string[],
  ) => {
    if (sourceMailboxId === targetMailboxId && sourceFolder === targetFolder) {
      closeMenus();
      return;
    }

    if (messageIds.length === 0) {
      closeMenus();
      return;
    }

    const sourceMessages = mailboxStore[sourceMailboxId][sourceFolder];
    const messageIdSet = new Set(messageIds);
    const targetMessages = sourceMessages.filter((message) =>
      messageIdSet.has(message.id),
    );

    if (targetMessages.length === 0) {
      closeMenus();
      return;
    }

    if (targetFolder === "Filtered") {
      applyFilteredLearningFromMessages(targetMessages, onSaveLearningRule);
    }

    setMailboxStore((currentStore) => {
      const nextStore = { ...currentStore };

      if (sourceMailboxId === targetMailboxId) {
        const currentMailboxCollections = currentStore[sourceMailboxId];
        const nextSourceMessages = currentMailboxCollections[sourceFolder].filter(
          (message) => !messageIdSet.has(message.id),
        );
        const nextTargetMessages = [
          ...targetMessages,
          ...currentMailboxCollections[targetFolder].filter(
            (message) => !messageIdSet.has(message.id),
          ),
        ];

        nextStore[sourceMailboxId] = {
          ...currentMailboxCollections,
          [sourceFolder]: nextSourceMessages,
          [targetFolder]: nextTargetMessages,
        };

        return nextStore;
      }

      nextStore[sourceMailboxId] = {
        ...currentStore[sourceMailboxId],
        [sourceFolder]: currentStore[sourceMailboxId][sourceFolder].filter(
          (message) => !messageIdSet.has(message.id),
        ),
      };
      nextStore[targetMailboxId] = {
        ...currentStore[targetMailboxId],
        [targetFolder]: [
          ...targetMessages,
          ...currentStore[targetMailboxId][targetFolder].filter(
            (message) => !messageIdSet.has(message.id),
          ),
        ],
      };

      return nextStore;
    });
    if (mailbox.id === sourceMailboxId && activeFolder === sourceFolder) {
      advanceSelectionAfterAction(messageIds);
    }
    closeMenus();
  };

  const removeMessagesFromTrash = (
    targetMailboxId: InboxId,
    messageIds: string[],
    feedbackMessage?: string,
  ) => {
    if (messageIds.length === 0) {
      closeMenus();
      return;
    }

    const messageIdSet = new Set(messageIds);

    setMailboxStore((currentStore) => {
      const currentMailboxCollections =
        currentStore[targetMailboxId] ?? createEmptyMailboxCollections();
      const nextTrashMessages = currentMailboxCollections.Trash.filter(
        (message) => !messageIdSet.has(message.id),
      );

      if (nextTrashMessages.length === currentMailboxCollections.Trash.length) {
        return currentStore;
      }

      return {
        ...currentStore,
        [targetMailboxId]: {
          ...currentMailboxCollections,
          Trash: nextTrashMessages,
        },
      };
    });

    if (mailbox.id === targetMailboxId && activeFolder === "Trash") {
      advanceSelectionAfterAction(messageIds);
    }

    if (feedbackMessage) {
      setTrashEmptiedToastMessage(feedbackMessage);
    }

    closeMenus();
  };

  const deleteMessages = (messageIds: string[]) => {
    if (!isSharedView && !activeSmartFolder && activeFolder === "Trash") {
      removeMessagesFromTrash(mailbox.id, messageIds, "Message removed from Trash");
      return;
    }

    if (isSharedView) {
      moveMessagesAcrossWorkspace(mailbox.id, "Trash", messageIds);
      return;
    }

    if (activeSmartFolder) {
      moveMessagesToFolderAcrossWorkspace("Trash", messageIds);
      return;
    }

    moveMessages(mailbox.id, activeFolder, mailbox.id, "Trash", messageIds);
  };

  const emptyTrash = () => {
    setIsEmptyTrashConfirmationOpen(false);
    removeMessagesFromTrash(
      mailbox.id,
      mailboxCollections.Trash.map((message) => message.id),
      "Trash emptied",
    );
  };

  const getFolderBadgeCount = (folder: MailFolder) =>
    getMailboxFolderBadgeCount(
      {
        ...mailboxCollections,
        Inbox: messageCollections.Inbox,
        Filtered: messageCollections.Filtered,
      },
      folder,
    );

  const getSharedMessageCount = () => workspaceSharedMessages.length;

  useEffect(() => {
    if (!trashEmptiedToastMessage) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      setTrashEmptiedToastMessage(null);
    }, 2400);

    return () => window.clearTimeout(timeoutId);
  }, [trashEmptiedToastMessage]);

  const contextMenuMessage = contextMenuState
    ? folderMessages.find((message) => message.id === contextMenuState.messageId) ?? null
    : null;
  const contextMenuSelectionIds = contextMenuState
    ? getEffectiveSelectionIds(contextMenuState.messageId)
    : [];
  const selectedMessages = sortedMessages.filter((message) =>
    visibleSelectedMessageIds.includes(message.id),
  );
  const selectedUnreadCount = selectedMessages.filter((message) => message.unread).length;
  const shouldBulkMarkAsRead = selectedUnreadCount > 0;
  const selectedCount = visibleSelectedMessageIds.length;
  const actionableSelectionIds =
    visibleSelectedMessageIds.length > 0
      ? visibleSelectedMessageIds
      : selectedMessageId
        ? [selectedMessageId]
        : [];
  const hasSelection = selectedCount > 0;
  const hasSingleSelection = selectedCount === 1;
  const contextMenuPosition = contextMenuState
    ? (() => {
        const menuWidth = 238;
        const viewportPadding = 12;
        const mailListRect = mailListViewportRef.current?.getBoundingClientRect();
        const boundsLeft = mailListRect?.left ?? viewportPadding;
        const boundsTop = mailListRect?.top ?? viewportPadding;
        const boundsRight = mailListRect?.right ?? window.innerWidth - viewportPadding;
        const boundsBottom = mailListRect?.bottom ?? window.innerHeight - viewportPadding;
        const maxMenuHeight = Math.max(
          180,
          boundsBottom - boundsTop - viewportPadding * 2,
        );
        const menuHeight = Math.min(520, maxMenuHeight);
        const openDownward =
          contextMenuState.y + menuHeight + viewportPadding <= boundsBottom;
        const preferredTop = openDownward
          ? contextMenuState.y
          : contextMenuState.y - menuHeight;
        const preferredLeft = contextMenuState.x;

        return {
          left: Math.max(
            boundsLeft + viewportPadding,
            Math.min(
              preferredLeft,
              boundsRight - menuWidth - viewportPadding,
            ),
          ),
          top: Math.max(
            boundsTop + viewportPadding,
            Math.min(
              preferredTop,
              boundsBottom - menuHeight - viewportPadding,
            ),
          ),
          maxHeight: maxMenuHeight,
        };
      })()
    : null;
  const activeInboxMoveLabel =
    mailbox.title.trim().length > 0
      ? mailbox.title.endsWith("Inbox")
        ? mailbox.title
        : `${mailbox.title} Inbox`
      : "Inbox";
  const moveTargets = [
    { label: activeInboxMoveLabel, type: "folder" as const, folder: "Inbox" as MailFolder },
    { label: "Filtered", type: "folder" as const, folder: "Filtered" as MailFolder },
    { label: "Drafts", type: "folder" as const, folder: "Drafts" as MailFolder },
    { label: "Sent", type: "folder" as const, folder: "Sent" as MailFolder },
    { label: "Archive", type: "folder" as const, folder: "Archive" as MailFolder },
    { label: "Spam", type: "folder" as const, folder: "Spam" as MailFolder },
    { label: "Trash", type: "folder" as const, folder: "Trash" as MailFolder },
    ...orderedMailboxes
      .filter((candidate) => candidate.id !== mailbox.id)
      .map((candidate) => ({
        label: candidate.title.endsWith("Inbox")
          ? candidate.title
          : `${candidate.title} Inbox`,
        type: "mailbox" as const,
        mailboxId: candidate.id,
      })),
  ];
  const moveSubmenuPosition =
    contextMenuState?.moveMenuOpen &&
    contextMenuPosition &&
    contextMenuState.moveAnchorX !== null &&
    contextMenuState.moveAnchorY !== null &&
    contextMenuState.moveAnchorHeight !== null
      ? (() => {
          const interactionRect =
            inboxInteractionViewportRef.current?.getBoundingClientRect() ??
            mailListViewportRef.current?.getBoundingClientRect();
          return getAnchoredSubmenuPosition({
            parentLeft: contextMenuPosition.left,
            parentWidth: 238,
            anchorY: contextMenuState.moveAnchorY,
            anchorHeight: contextMenuState.moveAnchorHeight,
            submenuWidth: 210,
            submenuHeight: Math.min(moveTargets.length * 32 + 20, 360),
            interactionRect,
          });
        })()
      : null;
  const learningMailboxTargets = orderedMailboxes.map((candidate) => {
    const displayTitle =
      candidate.id === "main"
        ? "Inbox"
        : candidate.title.endsWith("Inbox")
          ? candidate.title.replace(/\s+Inbox$/i, "")
          : candidate.title;

    return {
      mailboxId: candidate.id,
      chooserLabel: candidate.id === "main" ? "Inbox" : `${displayTitle} Inbox`,
      belongsLabel:
        candidate.id === "main"
          ? "Keep this in Inbox"
          : `This belongs in ${displayTitle}`,
      logValue: displayTitle
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "_")
        .replace(/^_|_$/g, ""),
    };
  });
  const learningSubmenuPosition =
    contextMenuState?.learningMenuOpen &&
    contextMenuPosition &&
    contextMenuState.learningAnchorX !== null &&
    contextMenuState.learningAnchorY !== null &&
    contextMenuState.learningAnchorHeight !== null
      ? (() => {
          const interactionRect =
            inboxInteractionViewportRef.current?.getBoundingClientRect() ??
            mailListViewportRef.current?.getBoundingClientRect();
          return getAnchoredSubmenuPosition({
            parentLeft: contextMenuPosition.left,
            parentWidth: 238,
            anchorY: contextMenuState.learningAnchorY,
            anchorHeight: contextMenuState.learningAnchorHeight,
            submenuWidth: 210,
            submenuHeight: Math.min((learningMailboxTargets.length + 6) * 32 + 40, 360),
            interactionRect,
          });
        })()
      : null;
  const learningChooserPosition =
    contextMenuState?.learningChooserOpen &&
    contextMenuPosition &&
    contextMenuState.learningAnchorY !== null &&
    contextMenuState.learningAnchorHeight !== null
      ? (() => {
          const interactionRect =
            inboxInteractionViewportRef.current?.getBoundingClientRect() ??
            mailListViewportRef.current?.getBoundingClientRect();
          return getAnchoredSubmenuPosition({
            parentLeft: contextMenuPosition.left,
            parentWidth: 238,
            anchorY: contextMenuState.learningAnchorY,
            anchorHeight: contextMenuState.learningAnchorHeight,
            submenuWidth: 228,
            submenuHeight: Math.min(learningMailboxTargets.length * 32 + 48, 320),
            interactionRect,
          });
        })()
      : null;
  const readingLearningMenuPosition =
    isReadingLearningMenuOpen && readingLearningMenuAnchor
      ? (() => {
          const interactionRect =
            inboxInteractionViewportRef.current?.getBoundingClientRect();
          const menuWidth = 244;
          const menuHeight = 320;
          const viewportPadding = 12;
          const boundsLeft = interactionRect?.left ?? viewportPadding;
          const boundsTop = interactionRect?.top ?? viewportPadding;
          const boundsRight = interactionRect?.right ?? window.innerWidth - viewportPadding;
          const boundsBottom = interactionRect?.bottom ?? window.innerHeight - viewportPadding;
          const preferredLeft =
            readingLearningMenuAnchor.left + readingLearningMenuAnchor.width - menuWidth;
          const preferredTop =
            readingLearningMenuAnchor.top + readingLearningMenuAnchor.height + 10;

          return {
            left: Math.max(
              boundsLeft + viewportPadding,
              Math.min(preferredLeft, boundsRight - menuWidth - viewportPadding),
            ),
            top: Math.max(
              boundsTop + viewportPadding,
              Math.min(preferredTop, boundsBottom - menuHeight - viewportPadding),
            ),
          };
        })()
      : null;
  const activeDestinationTitle = isSharedView
    ? mailbox.title.endsWith("Inbox")
      ? mailbox.title
      : `${mailbox.title} Inbox`
    : activeFolder === "Filtered"
      ? "Filtered"
    : activeSmartFolder
      ? activeSmartFolder.name
      : mailbox.title.endsWith("Inbox")
        ? mailbox.title
        : `${mailbox.title} Inbox`;
  const isReadOnlySmartFolderView = Boolean(activeSmartFolder);

  useEffect(() => {
    setIsReadingLearningMenuOpen(false);
    setActiveReadingLearningTrigger(null);
  }, [
    activeFolder,
    isComposeOpen,
    isFullMessageOpen,
    isMultiSelectActive,
    isSharedView,
    selectedMessageId,
  ]);

  const archiveSelectedMessages = () => {
    if (actionableSelectionIds.length === 0) {
      return;
    }

    if (isSharedView) {
      moveMessagesAcrossWorkspace(mailbox.id, "Archive", actionableSelectionIds);
      return;
    }

    if (activeSmartFolder) {
      moveMessagesToFolderAcrossWorkspace("Archive", actionableSelectionIds);
      return;
    }

    moveMessages(mailbox.id, activeFolder, mailbox.id, "Archive", actionableSelectionIds);
  };

  const deleteSelectedMessages = () => {
    if (actionableSelectionIds.length === 0) {
      return;
    }

    if (isSharedView) {
      moveMessagesAcrossWorkspace(mailbox.id, "Trash", actionableSelectionIds);
      return;
    }

    deleteMessages(actionableSelectionIds);
  };

  const toggleSelectedUnreadState = () => {
    if (actionableSelectionIds.length === 0) {
      return;
    }

    setMessagesUnreadState(
      activeFolder,
      actionableSelectionIds,
      !shouldBulkMarkAsRead,
    );
  };

  const handleDragStart = (
    event: DragEvent<HTMLButtonElement>,
    messageId: string,
  ) => {
    const messageIds =
      selectedMessageIds.includes(messageId) && selectedMessageIds.length > 1
        ? selectedMessageIds
        : [messageId];

    if (!selectedMessageIds.includes(messageId)) {
      setSelectionState([messageId], messageId, messageId);
    }

    setDragPayload({
      sourceMailboxId:
        isSharedView || activeSmartFolder
          ? currentMessageLocationById[messageId]?.mailboxId ?? mailbox.id
          : mailbox.id,
      sourceFolder:
        isSharedView || activeSmartFolder
          ? currentMessageLocationById[messageId]?.folder ?? "Inbox"
          : activeFolder,
      messageIds,
    });
    dragPreviewCleanupRef.current?.();

    const dragPreview = document.createElement("div");
    dragPreview.style.position = "fixed";
    dragPreview.style.top = "-9999px";
    dragPreview.style.left = "-9999px";
    dragPreview.style.pointerEvents = "none";
    dragPreview.style.zIndex = "9999";
    dragPreview.style.display = "inline-flex";
    dragPreview.style.alignItems = "center";
    dragPreview.style.gap = "10px";
    dragPreview.style.padding = "10px 14px";
    dragPreview.style.borderRadius = "18px";
    dragPreview.style.border = "1px solid var(--workspace-border-hover)";
    dragPreview.style.background =
      "linear-gradient(180deg, var(--workspace-selected-surface-start), var(--workspace-selected-surface-end))";
    dragPreview.style.boxShadow =
      "0 16px 34px rgba(31,42,36,0.12), inset 0 1px 0 rgba(255,255,255,0.08)";
    dragPreview.style.color = "var(--workspace-text)";
    dragPreview.style.fontFamily = "inherit";
    dragPreview.style.fontSize = "12px";
    dragPreview.style.fontWeight = "600";
    dragPreview.style.letterSpacing = "0.08em";
    dragPreview.style.textTransform = "uppercase";

    const dragLabel = document.createElement("span");
    dragLabel.textContent =
      messageIds.length > 1 ? "Messages" : "Message";
    dragPreview.appendChild(dragLabel);

    if (messageIds.length > 1) {
      const dragCountBadge = document.createElement("span");
      dragCountBadge.textContent = String(messageIds.length);
      dragCountBadge.style.display = "inline-flex";
      dragCountBadge.style.alignItems = "center";
      dragCountBadge.style.justifyContent = "center";
      dragCountBadge.style.minWidth = "20px";
      dragCountBadge.style.height = "20px";
      dragCountBadge.style.padding = "0 6px";
      dragCountBadge.style.borderRadius = "999px";
      dragCountBadge.style.background = "rgba(255,255,255,0.72)";
      dragCountBadge.style.border = "1px solid rgba(176, 155, 133, 0.22)";
      dragCountBadge.style.boxShadow = "inset 0 1px 0 rgba(255,255,255,0.32)";
      dragCountBadge.style.fontSize = "11px";
      dragCountBadge.style.fontWeight = "700";
      dragCountBadge.style.letterSpacing = "0";
      dragCountBadge.style.textTransform = "none";
      dragPreview.appendChild(dragCountBadge);
    }

    document.body.appendChild(dragPreview);
    event.dataTransfer.setDragImage(dragPreview, 20, 20);
    dragPreviewCleanupRef.current = () => {
      dragPreview.remove();
      dragPreviewCleanupRef.current = null;
    };
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", messageIds.join(","));
  };

  const handleDragEnd = () => {
    setDragPayload(null);
    setDragTargetKey(null);
    dragPreviewCleanupRef.current?.();
  };

  const handleDropToTarget = (target: { type: "folder"; folder: MailFolder } | { type: "mailbox"; mailboxId: InboxId }) => {
    if (!dragPayload) {
      return;
    }

    if (target.type === "folder") {
      moveMessages(
        dragPayload.sourceMailboxId,
        dragPayload.sourceFolder,
        dragPayload.sourceMailboxId,
        target.folder,
        dragPayload.messageIds,
      );
    } else {
      moveMessages(
        dragPayload.sourceMailboxId,
        dragPayload.sourceFolder,
        target.mailboxId,
        "Inbox",
        dragPayload.messageIds,
      );
    }

    setDragPayload(null);
    setDragTargetKey(null);
    dragPreviewCleanupRef.current?.();
  };

  const openSmartFolder = (folderId: string) => {
    const folder = smartFolders.find((entry) => entry.id === folderId);
    const scopeMailboxIds =
      folder?.scope === "selected" && folder.selectedInboxIds.length > 0
        ? folder.selectedInboxIds
        : orderedMailboxes.map((candidate) => candidate.id);
    const nextMessageId =
      folder
        ? scopeMailboxIds
            .flatMap((mailboxId) =>
              (mailboxStore[mailboxId]?.Inbox ?? [])
                .filter((message) => doesMessageMatchSmartFolder(message, folder))
                .map((message) => message.id),
            )[0] ?? null
        : null;

    setActiveSmartFolderId(folderId);
    setIsSharedView(false);
    setActiveFolder("Inbox");
    setIsFullMessageOpen(false);
    setSelectionState(
      nextMessageId ? [nextMessageId] : [],
      nextMessageId,
      nextMessageId,
    );
    closeMenus();
  };

  const switchToFolder = (folder: MailFolder) => {
    const nextMessageId = messageCollections[folder][0]?.id ?? null;

    setActiveSmartFolderId(null);
    setIsSharedView(false);
    setActiveFolder(folder);
    setIsFullMessageOpen(false);
    setSelectionState(
      nextMessageId ? [nextMessageId] : [],
      nextMessageId,
      nextMessageId,
    );
    closeMenus();
  };

  const switchToSharedView = () => {
    const nextMessageId = workspaceSharedMessages[0]?.id ?? null;

    setActiveSmartFolderId(null);
    setIsSharedView(true);
    setActiveFolder("Inbox");
    setIsFullMessageOpen(false);
    setSelectionState(
      nextMessageId ? [nextMessageId] : [],
      nextMessageId,
      nextMessageId,
    );
    closeMenus();
  };

  const commitMailboxTitleEdit = () => {
    const nextTitle = mailboxTitleDraft.trim();

    if (nextTitle.length > 0 && nextTitle !== mailbox.title) {
      onRenameMailbox(mailbox.id, nextTitle);
    } else {
      setMailboxTitleDraft(mailbox.title);
    }

    setIsEditingMailboxTitle(false);
  };


  useEffect(() => {
    const isTypingTarget = (target: EventTarget | null) => {
      if (!(target instanceof HTMLElement)) {
        return false;
      }

      const tagName = target.tagName;

      return (
        target.isContentEditable ||
        tagName === "INPUT" ||
        tagName === "TEXTAREA" ||
        tagName === "SELECT"
      );
    };

    const handleMailboxKeydown = (event: KeyboardEvent) => {
      if (isTypingTarget(event.target) || isComposeOpen || isCloseModalOpen) {
        return;
      }

      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "u") {
        if (isReadOnlySmartFolderView) {
          return;
        }
        event.preventDefault();
        toggleSelectedUnreadState();
        return;
      }

      if (!sortedMessages.length) {
        return;
      }

      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();

        const currentIndex = selectedMessage
          ? sortedMessages.findIndex((message) => message.id === selectedMessage.id)
          : -1;
        const fallbackIndex = event.key === "ArrowDown" ? 0 : sortedMessages.length - 1;
        const targetIndex =
          currentIndex === -1
            ? fallbackIndex
            : event.key === "ArrowDown"
              ? Math.min(currentIndex + 1, sortedMessages.length - 1)
              : Math.max(currentIndex - 1, 0);
        const targetMessage = sortedMessages[targetIndex];

        if (targetMessage) {
          handleSelectMessage(activeFolder, targetMessage.id);
        }
        return;
      }

      if (event.key === "Enter") {
        if (!selectedMessage) {
          return;
        }

        event.preventDefault();
        handleSelectMessage(activeFolder, selectedMessage.id);
        return;
      }

      if (!event.metaKey && !event.ctrlKey && !event.altKey && event.key.toLowerCase() === "e") {
        if (isReadOnlySmartFolderView) {
          return;
        }
        event.preventDefault();
        archiveSelectedMessages();
        return;
      }

      if (event.key === "Delete" || event.key === "Backspace") {
        if (isReadOnlySmartFolderView) {
          return;
        }
        event.preventDefault();
        deleteSelectedMessages();
      }
    };

    window.addEventListener("keydown", handleMailboxKeydown);

    return () => {
      window.removeEventListener("keydown", handleMailboxKeydown);
    };
  }, [
    activeFolder,
    activeSmartFolder,
    deleteSelectedMessages,
    hasSelection,
    isReadOnlySmartFolderView,
    isCloseModalOpen,
    isComposeOpen,
    selectedMessage,
    shouldBulkMarkAsRead,
    sortedMessages,
    toggleSelectedUnreadState,
    visibleSelectedMessageIds,
  ]);

  const handleMailboxBack = () => {
    if (isComposeOpen) {
      setIsCloseModalOpen(true);
      return;
    }

    if (isFullMessageOpen) {
      if (lastNavigationSource === "priority") {
        setLastNavigationSource(null);
        onBack();
        return;
      }

      setIsFullMessageOpen(false);
      return;
    }

    onBack();
  };

  const handleMailSplitResizeStart = (event: MouseEvent<HTMLButtonElement>) => {
    if (!isWideSplitView || !splitPaneContainerRef.current) {
      return;
    }

    event.preventDefault();

    const containerRect = splitPaneContainerRef.current.getBoundingClientRect();

    const handlePointerMove = (moveEvent: globalThis.MouseEvent) => {
      const nextWidth = clampMailListPaneWidth(
        moveEvent.clientX - containerRect.left - MAIL_FOLDER_COLUMN_WIDTH - MAIL_SPLIT_GAP,
      );
      setPreferredMailListPaneWidth(nextWidth);
    };

    const handlePointerUp = () => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", handlePointerMove);
      window.removeEventListener("mouseup", handlePointerUp);
    };

    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    window.addEventListener("mousemove", handlePointerMove);
    window.addEventListener("mouseup", handlePointerUp);
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-hidden md:gap-4">
      <header className="flex-none">
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between md:gap-6">
          <div className="flex min-w-0 flex-col items-start gap-2">
            <div className="min-w-0 space-y-1">
              <div className="text-[0.72rem] font-medium uppercase tracking-[0.24em] text-[var(--workspace-text-faint)]">
                {activeSmartFolder ? "Smart folder" : "Inbox"}
              </div>
            </div>
            <button
              type="button"
              onClick={handleMailboxBack}
              className={settingsPrimaryActionClass}
            >
              Back
            </button>
          </div>
          <div className="flex flex-none items-start md:justify-end">
            <div className="flex flex-col items-start gap-0.5 text-[0.82rem] leading-5 text-[var(--workspace-text-faint)] md:items-end">
              {activeFolder === "Inbox" && !isSharedView && isEditingMailboxTitle ? (
                <input
                  ref={mailboxTitleInputRef}
                  value={mailboxTitleDraft}
                  onChange={(event) => setMailboxTitleDraft(event.target.value)}
                  onBlur={commitMailboxTitleEdit}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      commitMailboxTitleEdit();
                    }

                    if (event.key === "Escape") {
                      event.preventDefault();
                      setMailboxTitleDraft(mailbox.title);
                      setIsEditingMailboxTitle(false);
                    }
                  }}
                  className="min-w-0 bg-transparent text-[0.98rem] font-semibold tracking-[-0.01em] text-[var(--workspace-text)] outline-none md:text-[1.04rem] md:text-right"
                />
              ) : (
                <div className="group relative">
                  <button
                    type="button"
                    onClick={() => {
                      if (activeFolder === "Inbox" && !isSharedView && !activeSmartFolder) {
                        setIsEditingMailboxTitle(true);
                      }
                    }}
                    aria-label="Edit mailbox name"
                    disabled={activeFolder !== "Inbox" || isSharedView || Boolean(activeSmartFolder)}
                    className={`inline-flex items-center gap-1.5 rounded-full text-[0.98rem] font-semibold tracking-[-0.01em] text-[var(--workspace-text)] transition-colors duration-200 focus-visible:outline-none md:text-[1.04rem] ${
                      activeFolder === "Inbox" && !isSharedView && !activeSmartFolder
                        ? "cursor-pointer"
                        : "cursor-default"
                    }`}
                  >
                    <span>{activeDestinationTitle}</span>
                    {activeFolder === "Inbox" && !isSharedView && !activeSmartFolder ? (
                      <span className="text-[var(--workspace-text-faint)] opacity-45 transition-opacity duration-200 group-hover:opacity-100">
                        <svg
                          aria-hidden="true"
                          viewBox="0 0 16 16"
                          className="h-3.5 w-3.5"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="1.75"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        >
                          <path d="M3.25 12.75 3.6 10.25 10.9 2.95a1.15 1.15 0 0 1 1.65 0l.5.5a1.15 1.15 0 0 1 0 1.65l-7.3 7.3z" />
                          <path d="M9.95 3.9 12.1 6.05" />
                        </svg>
                      </span>
                    ) : null}
                  </button>
                  {activeFolder === "Inbox" && !isSharedView && !activeSmartFolder ? (
                    <div className="pointer-events-none absolute right-0 top-full z-10 mt-2 whitespace-nowrap rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-3.5 py-1 text-[0.62rem] font-medium tracking-[0.08em] text-[var(--workspace-text-soft)] opacity-0 shadow-panel transition-opacity duration-200 group-hover:opacity-100">
                      Edit name
                    </div>
                  ) : null}
                </div>
              )}
              <div className="text-[var(--workspace-text-faint)]">{mailbox.email}</div>
              <MailboxConnectionState />
            </div>
          </div>
        </div>
      </header>

      <section className="relative flex h-0 min-h-0 flex-1 flex-col overflow-hidden rounded-[30px] border border-[var(--workspace-border)] bg-[var(--workspace-card)] p-5 shadow-panel md:p-6">
        <div className="mb-4 flex flex-none flex-wrap items-center gap-2">
          <button
            type="button"
            className={mailboxPrimaryActionButtonClass}
            onClick={openCompose}
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 16 16"
              className="h-4 w-4"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M8 3v10" />
              <path d="M3 8h10" />
            </svg>
            Compose
          </button>
          <MailToolbarIconButton
            label={isSyncingMailbox ? "Syncing" : "Sync"}
            onClick={onSyncMailbox}
            disabled={isSyncingMailbox}
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 16 16"
              className={`h-4 w-4 ${isSyncingMailbox ? "animate-spin" : ""}`}
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M13 5.5A5 5 0 0 0 4.5 3L3 4.5" />
              <path d="M3.5 2.5v2h2" />
              <path d="M3 10.5A5 5 0 0 0 11.5 13L13 11.5" />
              <path d="M12.5 13.5v-2h-2" />
            </svg>
          </MailToolbarIconButton>
          <MailToolbarIconButton
            label="Reply"
            disabled={!hasSingleSelection}
            onClick={() => {
              if (selectedMessage) {
                openComposeFromMessage(selectedMessage, "reply");
              }
            }}
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 16 16"
              className="h-4 w-4"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M6 4 2.5 8 6 12" />
              <path d="M3 8h7c2.3 0 4 1.2 4 3.5" />
            </svg>
          </MailToolbarIconButton>
          <MailToolbarIconButton
            label="Reply All"
            disabled={!hasSingleSelection}
            onClick={() => {
              if (selectedMessage) {
                openComposeFromMessage(selectedMessage, "reply_all");
              }
            }}
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 16 16"
              className="h-4 w-4"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.75"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M5.75 4 2.5 8l3.25 4" />
              <path d="M10.75 4 7.5 8l3.25 4" />
              <path d="M3 8h7c2.1 0 3.75 1.1 3.75 3.3" />
            </svg>
          </MailToolbarIconButton>
          <MailToolbarIconButton
            label="Forward"
            disabled={!hasSingleSelection}
            onClick={() => {
              if (selectedMessage) {
                openComposeFromMessage(selectedMessage, "forward");
              }
            }}
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 16 16"
              className="h-4 w-4"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M10 4 13.5 8 10 12" />
              <path d="M13 8H6.5C4.2 8 2.5 9.2 2.5 11.5" />
            </svg>
          </MailToolbarIconButton>
          <MailToolbarIconButton
            label="Archive"
            disabled={!hasSelection || isReadOnlySmartFolderView}
            onClick={archiveSelectedMessages}
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 16 16"
              className="h-4 w-4"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.9"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M2.5 4.5h11v3h-11z" />
              <path d="M4 7.5v4.25h8V7.5" />
              <path d="M6 9.5h4" />
            </svg>
          </MailToolbarIconButton>
          <MailToolbarIconButton
            label="Attachments"
            onClick={openComposeAttachmentPicker}
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 16 16"
              className="h-4 w-4"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M6 6.5v4.5a2 2 0 0 0 4 0v-6a3 3 0 1 0-6 0v6.5a4 4 0 0 0 8 0V6.5" />
            </svg>
          </MailToolbarIconButton>
          <MailToolbarIconButton
            label="Delete"
            disabled={!hasSelection || isReadOnlySmartFolderView}
            onClick={deleteSelectedMessages}
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 16 16"
              className="h-4 w-4"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.9"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M3.5 4.5h9" />
              <path d="M5 4.5 5.5 13h5l.5-8.5" />
              <path d="M6 2.75h4" />
              <path d="M6.5 6v5" />
              <path d="M9.5 6v5" />
            </svg>
          </MailToolbarIconButton>
          <MailToolbarIconButton
            label="Flag"
            disabled={!hasSingleSelection}
            active={Boolean(selectedMessage?.flagged)}
            onClick={() => {
              if (selectedMessage) {
                toggleMessageFlagState(selectedMessage.id);
              }
            }}
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 16 16"
              className="h-4 w-4"
              fill={selectedMessage?.flagged ? "currentColor" : "none"}
              stroke="currentColor"
              strokeWidth="1.75"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M5 3.25h6v9.25L8 10.75 5 12.5z" />
            </svg>
          </MailToolbarIconButton>
          <div className="relative">
            <button
              type="button"
              disabled={!hasSelection || isReadOnlySmartFolderView}
              aria-label="More"
              onClick={(event) => {
                if (!hasSelection || isReadOnlySmartFolderView) {
                  return;
                }
                event.stopPropagation();
                setIsMoreMenuOpen((open) => !open);
                setContextMenuState(null);
              }}
              className={`${mailboxSecondaryActionButtonClass} ${
                !hasSelection || isReadOnlySmartFolderView
                  ? "cursor-default opacity-40 hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-card-subtle)]"
                  : ""
              }`}
            >
              <svg
                aria-hidden="true"
                viewBox="0 0 16 16"
                className="h-4 w-4"
                fill="currentColor"
              >
                <circle cx="3.5" cy="8" r="1.1" />
                <circle cx="8" cy="8" r="1.1" />
                <circle cx="12.5" cy="8" r="1.1" />
              </svg>
            </button>
            <div className="pointer-events-none absolute left-1/2 top-full z-10 mt-2 -translate-x-1/2 rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-3 py-1 text-[0.62rem] font-medium tracking-[0.08em] text-[var(--workspace-text-soft)] opacity-0 shadow-panel transition-opacity duration-200 hover:opacity-100">
              More
            </div>
            {isMoreMenuOpen && visibleSelectedMessageIds.length > 0 && !isReadOnlySmartFolderView ? (
              <div
                className="absolute right-0 top-full z-20 mt-3 min-w-[220px] rounded-[20px] border border-[var(--workspace-menu-border)] bg-[var(--workspace-menu-bg)] p-2 shadow-panel"
                onMouseDown={(event) => event.stopPropagation()}
              >
                <button
                  type="button"
                  onClick={() =>
                    setMessagesUnreadState(
                      activeFolder,
                      visibleSelectedMessageIds,
                      !shouldBulkMarkAsRead,
                    )
                  }
                  className={contextMenuMainItemClass}
                >
                  {shouldBulkMarkAsRead ? "Mark as read" : "Mark as unread"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    const primaryMessageId =
                      visibleSelectedMessageIds[0] ?? selectedMessageId ?? null;

                    if (!primaryMessageId) {
                      return;
                    }

                    openShareCollaboration(primaryMessageId);
                  }}
                  className={contextMenuMainItemClass}
                >
                  Start collaboration…
                </button>
                {hasSingleSelection && selectedMessage ? (
                  <>
                    <button
                      type="button"
                      onClick={() => {
                        onSetManualPriority(
                          selectedMessage.id,
                          !isVisiblePriorityMessage(selectedMessage),
                        );
                        closeMenus();
                      }}
                      className={contextMenuMainItemClass}
                    >
                      {isVisiblePriorityMessage(selectedMessage)
                        ? "Remove priority"
                        : "Mark as priority"}
                    </button>
                    {isVisiblePriorityMessage(selectedMessage) ? (
                      <button
                        type="button"
                        onClick={() => {
                          onSetManualPriority(selectedMessage.id, false);
                          closeMenus();
                        }}
                        className={contextMenuMainItemClass}
                      >
                        Mark as done
                      </button>
                    ) : null}
                  </>
                ) : null}
                {!isSharedView &&
                !activeSmartFolder &&
                activeFolder === "Trash" &&
                mailboxCollections.Trash.length > 0 ? (
                  <button
                    type="button"
                    onClick={() => {
                      setIsEmptyTrashConfirmationOpen(true);
                      setIsMoreMenuOpen(false);
                    }}
                    className={contextMenuMainItemClass}
                  >
                    Empty Trash
                  </button>
                ) : null}
                <div className="my-2 h-px bg-[color:rgba(120,104,89,0.12)]" />
                {moveTargets.map((target) => (
                  <button
                    key={`toolbar-${target.type}-${target.label}`}
                    type="button"
                    onClick={() => {
                      if (target.type === "folder") {
                        if (isSharedView) {
                          moveMessagesAcrossWorkspace(
                            mailbox.id,
                            target.folder,
                            visibleSelectedMessageIds,
                          );
                          return;
                        }

                        if (activeSmartFolder) {
                          moveMessagesToFolderAcrossWorkspace(
                            target.folder,
                            visibleSelectedMessageIds,
                          );
                          return;
                        }

                        moveMessages(
                          mailbox.id,
                          activeFolder,
                          mailbox.id,
                          target.folder,
                          visibleSelectedMessageIds,
                        );
                        return;
                      }

                      if (isSharedView) {
                        moveMessagesAcrossWorkspace(
                          target.mailboxId,
                          "Inbox",
                          visibleSelectedMessageIds,
                        );
                        return;
                      }

                      moveMessages(
                        mailbox.id,
                        activeFolder,
                        target.mailboxId,
                        "Inbox",
                        visibleSelectedMessageIds,
                      );
                    }}
                    className={contextMenuMainItemClass}
                  >
                    Move to {target.label}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
        </div>

        {isComposeOpen ? (
          <div className="min-h-0 flex-1 overflow-y-auto rounded-[24px] border border-[var(--workspace-border-soft)] bg-[linear-gradient(180deg,var(--workspace-card-featured-start),var(--workspace-card-featured-end))] p-5 md:p-6">
            <div className="space-y-5">
              <div className="flex items-center justify-between gap-4">
                <h2 className="text-[1.3rem] font-medium tracking-tight text-[var(--workspace-text)] md:text-[1.45rem]">
                  New Message
                </h2>
                <button
                  type="button"
                  onClick={() => setIsCloseModalOpen(true)}
                  className={closeActionButtonClass}
                >
                  Close
                </button>
              </div>

              <div className="space-y-3">
                <label className="grid grid-cols-[72px_minmax(0,1fr)] items-center gap-4 rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3">
                  <span className="text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                    To
                  </span>
                  <div className="relative">
                    <input
                      ref={composeToInputRef}
                      value={composeTo}
                      onChange={(event) => {
                        setComposeTo(event.target.value);
                        setActiveRecipientSuggestionField("to");
                      }}
                      onFocus={() => setActiveRecipientSuggestionField("to")}
                      onBlur={handleComposeRecipientBlur}
                      placeholder="Add recipient"
                      autoCorrect="on"
                      autoComplete="email"
                      autoCapitalize="none"
                      spellCheck
                      className="w-full bg-transparent text-[0.9rem] leading-6 text-[var(--workspace-text-soft)] outline-none placeholder:text-[var(--workspace-text-faint)]"
                    />
                    {renderComposeRecipientSuggestions("to")}
                  </div>
                </label>
                <label className="grid grid-cols-[72px_minmax(0,1fr)] items-center gap-4 rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3">
                  <span className="text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                    CC
                  </span>
                  <div className="relative">
                    <input
                      value={composeCc}
                      onChange={(event) => {
                        setComposeCc(event.target.value);
                        setActiveRecipientSuggestionField("cc");
                      }}
                      onFocus={() => setActiveRecipientSuggestionField("cc")}
                      onBlur={handleComposeRecipientBlur}
                      placeholder="Add recipient"
                      autoCorrect="on"
                      autoComplete="email"
                      autoCapitalize="none"
                      spellCheck
                      className="w-full bg-transparent text-[0.9rem] leading-6 text-[var(--workspace-text-soft)] outline-none placeholder:text-[var(--workspace-text-faint)]"
                    />
                    {renderComposeRecipientSuggestions("cc")}
                  </div>
                </label>
                {showComposeBcc ? (
                  <label className="grid grid-cols-[72px_minmax(0,1fr)] items-center gap-4 rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3">
                    <span className="text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                      BCC
                    </span>
                    <div className="relative">
                      <input
                        value={composeBcc}
                        onChange={(event) => {
                          setComposeBcc(event.target.value);
                          setActiveRecipientSuggestionField("bcc");
                        }}
                        onFocus={() => setActiveRecipientSuggestionField("bcc")}
                        onBlur={handleComposeRecipientBlur}
                        placeholder="Add recipient"
                        autoCorrect="on"
                        autoComplete="email"
                        autoCapitalize="none"
                        spellCheck
                        className="w-full bg-transparent text-[0.9rem] leading-6 text-[var(--workspace-text-soft)] outline-none placeholder:text-[var(--workspace-text-faint)]"
                      />
                      {renderComposeRecipientSuggestions("bcc")}
                    </div>
                  </label>
                ) : (
                  <button
                    type="button"
                    onClick={() => setShowComposeBcc(true)}
                    className={subtleSecondaryActionButtonClass}
                  >
                    Add BCC
                  </button>
                )}
                <label className="grid grid-cols-[72px_minmax(0,1fr)] items-center gap-4 rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3">
                  <span className="text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                    Subject
                  </span>
                  <input
                    value={composeSubject}
                    onChange={(event) => setComposeSubject(event.target.value)}
                    placeholder="Subject"
                    autoCorrect="on"
                    autoComplete="on"
                    autoCapitalize="sentences"
                    spellCheck
                    className="w-full bg-transparent text-[0.9rem] leading-6 text-[var(--workspace-text-soft)] outline-none placeholder:text-[var(--workspace-text-faint)]"
                  />
                </label>
              </div>

              <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_280px]">
                <div className="rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-4">
                  <span className="sr-only">Message body</span>
                  <div
                    ref={composeBodyInputRef}
                    contentEditable
                    suppressContentEditableWarning
                    role="textbox"
                    aria-multiline="true"
                    dir="ltr"
                    style={{
                      direction: "ltr",
                      unicodeBidi: "plaintext",
                      textAlign: "left",
                    }}
                    onInput={syncComposeBodyValue}
                    onKeyDown={handleComposeBodyKeyDown}
                    onPaste={handleComposeBodyPaste}
                    spellCheck
                    className="min-h-[360px] w-full whitespace-pre-wrap bg-transparent text-[0.94rem] leading-7 text-[var(--workspace-text-soft)] outline-none [&_a]:text-[color:rgba(70,109,73,0.96)] [&_a]:underline [&_div]:min-h-[1.75rem] [&_div[data-compose-quote='true']]:pt-3 [&_div[data-compose-signature='true']]:space-y-0 [&_div[data-compose-signature-divider='true']]:my-2 [&_div[data-compose-signature-divider='true']]:h-px [&_div[data-compose-signature-divider='true']]:w-full [&_div[data-compose-signature-divider='true']]:bg-[color:rgba(121,151,120,0.18)] [&_div[data-compose-signature-logo='true']]:pt-1 [&_div[data-compose-signature-logo='true']_img]:max-h-[76px] [&_div[data-compose-signature-logo='true']_img]:w-auto [&_div[data-compose-signature-logo='true']_img]:max-w-full [&_div[data-compose-signature-logo='true']_img]:object-contain [&_div[data-compose-signature-right='true']]:min-w-0 [&_div[data-compose-signature-right='true']]:flex-1 [&_div[data-compose-signature-row='true']]:flex [&_div[data-compose-signature-row='true']]:items-start [&_div[data-compose-signature-row='true']]:gap-4 [&_div[data-compose-signature-spacer='true']]:min-h-[1.75rem] [&_div[data-compose-signature-text='true']]:whitespace-pre-wrap [&_div[data-compose-signature-text='true']]:text-[0.86rem] [&_div[data-compose-signature-text='true']]:leading-[1.45] [&_div[data-compose-signature-text='true']_div]:min-h-[1.2rem] [&_div[data-compose-signature-text='true']_p]:min-h-[1.2rem]"
                  />
                </div>

                <div className="space-y-4">
                  <div className="rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-4">
                    <select
                      aria-label="Select signature"
                      value={composeSignatureSelection}
                      onChange={(event) =>
                        handleComposeSignatureSelectionChange(event.target.value)
                      }
                      className="w-full rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-3 py-2 text-[0.8rem] text-[var(--workspace-text-soft)] outline-none"
                    >
                      <option value="none">No signature</option>
                      {composeSignatureOptions.map((option) => (
                        <option key={option.id} value={option.id}>
                          {option.email}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="rounded-[20px] border border-dashed border-[var(--workspace-border)] bg-[var(--workspace-card)] px-4 py-4">
                    <input
                      ref={composeAttachmentInputRef}
                      type="file"
                      multiple
                      className="sr-only"
                      onChange={handleComposeAttachmentSelection}
                    />
                    <div className="mb-2 flex items-center gap-2 text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                      <svg
                        aria-hidden="true"
                        viewBox="0 0 16 16"
                        className="h-4 w-4"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.6"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <path d="M6 6.5v4.5a2 2 0 0 0 4 0v-6a3 3 0 1 0-6 0v6.5a4 4 0 0 0 8 0V6.5" />
                      </svg>
                      Attachments
                    </div>
                    <div className="text-[0.84rem] leading-6 text-[var(--workspace-text-soft)]">
                      Drop files here or add via paperclip
                    </div>
                    <button
                      type="button"
                      onClick={openComposeAttachmentPicker}
                      className="mt-4 inline-flex h-9 items-center justify-center gap-2 rounded-full border border-[var(--workspace-border)] bg-[var(--workspace-card-subtle)] px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-soft)] transition-[background-color,border-color,color,transform] duration-150 hover:border-[var(--workspace-border-hover)] hover:bg-[var(--workspace-hover-surface)] active:scale-[0.99] focus-visible:outline-none"
                    >
                      <svg
                        aria-hidden="true"
                        viewBox="0 0 16 16"
                        className="h-3.5 w-3.5"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.6"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <path d="M6 6.5v4.5a2 2 0 0 0 4 0v-6a3 3 0 1 0-6 0v6.5a4 4 0 0 0 8 0V6.5" />
                      </svg>
                      Add attachment
                    </button>
                  </div>

                  <div className="rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-4">
                    <div className="mb-3 text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                      Added files
                    </div>
                    <div className="flex flex-wrap gap-3">
                      {composeAttachments.length > 0 ? (
                        composeAttachments.map((attachment) =>
                          renderAttachmentItem(attachment, {
                            removable: true,
                            onRemove: () =>
                              setComposeAttachments((current) =>
                                current.filter((entry) => entry.id !== attachment.id),
                              ),
                          }),
                        )
                      ) : (
                        <div className="text-[0.82rem] leading-6 text-[var(--workspace-text-faint)]">
                          No attachments yet
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-4">
                    <div className="flex items-center justify-between gap-4">
                      <div>
                        <div className="text-[0.82rem] leading-6 text-[var(--workspace-text-faint)]">
                          {composeAttachments.length > 0
                            ? `${composeAttachments.length} attachment${composeAttachments.length === 1 ? "" : "s"} ready`
                            : "Message ready to send"}
                        </div>
                        {composeSendError ? (
                          <div className="mt-1 text-[0.8rem] leading-5 text-[color:rgba(148,63,38,0.96)]">
                            {composeSendError}
                          </div>
                        ) : null}
                      </div>
                      <button
                        type="button"
                        onClick={sendMessage}
                        disabled={isSendingCompose}
                        className="inline-flex h-10 min-w-[7.4rem] items-center justify-center rounded-full bg-pine px-6 text-[0.72rem] font-medium uppercase tracking-[0.18em] text-white transition-[background-color,transform] duration-150 hover:bg-moss active:scale-[0.99] focus-visible:outline-none disabled:cursor-not-allowed disabled:bg-[color:rgba(101,124,103,0.72)] disabled:hover:bg-[color:rgba(101,124,103,0.72)]"
                      >
                        {isSendingCompose ? "Sending..." : "Send"}
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        ) : isFullMessageOpen && fullWidthMessage ? (
	          <div className="min-h-0 flex-1 overflow-y-auto rounded-[24px] border border-[var(--workspace-border-soft)] bg-[linear-gradient(180deg,var(--workspace-card-featured-start),var(--workspace-card-featured-end))] p-5 md:p-6">
	            <div className="space-y-6">
	              {(() => {
	                const linkedReview = getLinkedReviewForMessage(fullWidthMessage.id);
	                const linkedReviewLabel = getLinkedReviewBadgeLabel(fullWidthMessage.id);

	                return (
	              <div className="flex items-start justify-between gap-4">
	                <div className="min-w-0 flex-1 space-y-1">
	                  <div className="text-[0.68rem] font-medium uppercase tracking-[0.18em] text-[var(--workspace-text-faint)]">
	                    Message
	                  </div>
	                  <h2 className="text-[1.3rem] font-medium tracking-tight text-[var(--workspace-text)] md:text-[1.45rem]">
	                    {fullWidthMessage.subject}
	                  </h2>
	                  {linkedReview && linkedReviewLabel ? (
	                    <button
	                      type="button"
	                      onClick={() => onOpenLinkedReview(linkedReview.target)}
	                      className="inline-flex rounded-full border border-[var(--workspace-border)] bg-[var(--workspace-hover-surface)] px-3 py-1 text-[0.68rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-text)] transition-[background-color,border-color,color] duration-150 hover:border-[var(--workspace-border-hover)] hover:bg-[var(--workspace-hover-surface-strong)] focus-visible:outline-none"
	                    >
	                      {linkedReviewLabel}
	                    </button>
	                  ) : null}
	                </div>
	                <div className="flex flex-none flex-wrap items-start justify-end gap-4 self-start">
	                  {renderMessageActions(fullWidthMessage, "full")}
                  {aiSuggestionsEnabled ? (
                  <ReadingLearningButton
                    open={isReadingLearningMenuOpen}
                    triggerId="full-message"
                    onClick={(event) => {
                      event.stopPropagation();
                      const rect = event.currentTarget.getBoundingClientRect();
                      toggleReadingLearningMenu("full-message", {
                        top: rect.top,
                        left: rect.left,
                        width: rect.width,
                        height: rect.height,
                      });
                    }}
                  />
                  ) : null}
                  <button
                    type="button"
                    onClick={() => setIsFullMessageOpen(false)}
                    className={closeActionButtonClass}
                  >
                    Close
	                  </button>
	                </div>
	              </div>
	                );
	              })()}

	              <div className="rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-5 py-4">
                <div className="space-y-0.5 text-[0.9rem] leading-[1.5] text-[var(--workspace-text-soft)]">
                  <div>
                    <span className="text-[var(--workspace-text-faint)]">Subject</span>{" "}
                    {fullWidthMessage.subject}
                  </div>
                  <div>
                    <span className="text-[var(--workspace-text-faint)]">From</span>{" "}
                    {fullWidthMessage.from}
                  </div>
                  <div>
                    <span className="text-[var(--workspace-text-faint)]">To</span>{" "}
                    {fullWidthMessage.to}
                  </div>
                  <div>
                    <span className="text-[var(--workspace-text-faint)]">Time</span>{" "}
                    {fullWidthMessage.timestamp}
                  </div>
                </div>
              </div>

              {aiSuggestionsEnabled ? renderAIDecisionBlock(fullWidthMessage) : null}

              {fullWidthMessage.focusSignal === "attention" ? (
                <div className="text-[0.88rem] leading-6 text-[color:rgba(88,82,74,0.92)]">
                  Needs your attention
                </div>
              ) : null}

              <div className="space-y-2">
                {shouldShowMessageSummary(fullWidthMessage.snippet, fullWidthMessage.body) ? (
                  <div className="mt-3 w-[94%] text-[0.9rem] leading-[1.95] text-[color:rgba(95,88,80,0.9)]">
                    {fullWidthMessage.snippet}
                  </div>
                ) : null}

                {renderMessageCollaboration(fullWidthMessage)}

                {renderBehaviorSuggestion(fullWidthMessage)}

                    {fullWidthMessage.isShared && fullWidthMessage.sharedContext ? (
                      <div className="w-[94%] text-[0.82rem] leading-6 text-[color:rgba(120,111,100,0.68)]">
                        {formatSharedContextDetail(fullWidthMessage.sharedContext)}
                      </div>
                    ) : null}

                {renderThreadTimeline(fullWidthMessage, "full")}
              </div>

              <div className="rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-5 py-4">
                <div className="mb-3 text-[0.72rem] font-medium uppercase tracking-[0.18em] text-[var(--workspace-text-faint)]">
                  Attachments
                </div>
                <div className="flex flex-wrap gap-3">
                  {(fullWidthMessage.attachments ?? []).map((attachment) =>
                    renderAttachmentItem(attachment),
                  )}
                  {!fullWidthMessage.attachments?.length ? (
                    <div className="text-[0.82rem] leading-6 text-[var(--workspace-text-faint)]">
                      No attachments
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div
            ref={(node) => {
              inboxInteractionViewportRef.current = node;
              splitPaneContainerRef.current = node;
            }}
            className="grid h-0 min-h-0 flex-1 items-stretch gap-6 overflow-hidden xl:grid-cols-[180px_minmax(0,0.92fr)_minmax(0,1.28fr)]"
            style={
              isWideSplitView && effectiveMailListPaneWidth !== null
                ? {
                    gridTemplateColumns: `${MAIL_FOLDER_COLUMN_WIDTH}px minmax(${MIN_MAIL_LIST_PANE_WIDTH}px, ${effectiveMailListPaneWidth}px) minmax(${MIN_MAIL_DETAIL_PANE_WIDTH}px, 1fr)`,
                  }
                : undefined
            }
          >
            <div className="flex min-h-0 min-w-0 flex-col overflow-hidden">
              <div className="min-h-0 flex-1 space-y-1 overflow-y-auto">
              {(["Inbox", "Drafts", "Sent", "Archive", "Spam", "Trash"] as MailFolder[]).map(
                (folder) => {
                  const active =
                    !isSharedView &&
                    !activeSmartFolder &&
                    folder === activeFolder;
                      const count = getFolderBadgeCount(folder);
                      const shouldShowFolderCount = folder === "Inbox";
                  const dragTargetId = `folder-${folder}`;
                  const isDragTargetActive = dragTargetKey === dragTargetId;
                  const folderLabel =
                    folder === "Inbox"
                      ? mailbox.title.endsWith("Inbox")
                        ? mailbox.title
                        : `${mailbox.title} Inbox`
                      : folder;

                  return (
                    <div key={folder} className="space-y-1">
                      <button
                        type="button"
                        onDragOver={(event) => {
                          if (!dragPayload) {
                            return;
                          }
                          event.preventDefault();
                          setDragTargetKey(dragTargetId);
                        }}
                        onDragLeave={() => {
                          if (dragTargetKey === dragTargetId) {
                            setDragTargetKey(null);
                          }
                        }}
                        onDrop={(event) => {
                          event.preventDefault();
                          handleDropToTarget({ type: "folder", folder });
                        }}
                        onClick={() => switchToFolder(folder)}
                        className={`flex w-full items-center justify-between rounded-[18px] px-4 py-3 text-left transition-[background-color,border-color,color,box-shadow,transform] duration-150 ${
                          active
                            ? "bg-[linear-gradient(180deg,var(--workspace-card-featured-start),var(--workspace-card-featured-end))] text-[var(--workspace-text)]"
                            : isDragTargetActive
                              ? "border border-[var(--workspace-border-hover)] bg-[linear-gradient(180deg,var(--workspace-selected-surface-start),var(--workspace-selected-surface-end))] text-[var(--workspace-text)] shadow-[0_10px_24px_rgba(31,42,36,0.08),inset_0_1px_0_rgba(255,255,255,0.08)]"
                              : "text-[var(--workspace-text-soft)] hover:bg-[var(--workspace-card-subtle)]"
                        } focus-visible:outline-none`}
                      >
                        <span className="text-[0.8rem] font-medium uppercase tracking-[0.14em]">
                          {folderLabel}
                        </span>
                        {shouldShowFolderCount ? (
                          <span className="text-[0.72rem] text-[var(--workspace-text-faint)]">
                            {count}
                          </span>
                        ) : null}
                      </button>
                      {folder === "Inbox" ? (
                        <>
                          <button
                            type="button"
                            onDragOver={(event) => {
                              if (!dragPayload) {
                                return;
                              }
                              event.preventDefault();
                              setDragTargetKey("folder-Filtered");
                            }}
                            onDragLeave={() => {
                              if (dragTargetKey === "folder-Filtered") {
                                setDragTargetKey(null);
                              }
                            }}
                            onDrop={(event) => {
                              event.preventDefault();
                              handleDropToTarget({ type: "folder", folder: "Filtered" });
                            }}
                            onClick={() => switchToFolder("Filtered")}
                            className={`flex w-full items-center justify-between rounded-[18px] pl-6 pr-4 py-3 text-left transition-[background-color,border-color,color,box-shadow,transform] duration-150 ${
                              !isSharedView &&
                              !activeSmartFolder &&
                              activeFolder === "Filtered"
                                ? "bg-[linear-gradient(180deg,var(--workspace-card-featured-start),var(--workspace-card-featured-end))] text-[var(--workspace-text)]"
                                : dragTargetKey === "folder-Filtered"
                                  ? "border border-[var(--workspace-border-hover)] bg-[linear-gradient(180deg,var(--workspace-selected-surface-start),var(--workspace-selected-surface-end))] text-[var(--workspace-text)] shadow-[0_10px_24px_rgba(31,42,36,0.08),inset_0_1px_0_rgba(255,255,255,0.08)]"
                                  : "text-[color:rgba(108,99,89,0.92)] hover:bg-[var(--workspace-card-subtle)] hover:text-[var(--workspace-text)]"
                            } focus-visible:outline-none`}
                          >
                            <span
                              className={`text-[0.8rem] font-medium uppercase tracking-[0.14em] ${
                                !isSharedView &&
                                !activeSmartFolder &&
                                activeFolder === "Filtered"
                                  ? ""
                                  : "opacity-95"
                              }`}
                            >
                              Filtered
                            </span>
                            <span
                              className={`text-[0.72rem] ${
                                !isSharedView &&
                                !activeSmartFolder &&
                                activeFolder === "Filtered"
                                  ? "text-[var(--workspace-text-faint)]"
                                  : "text-[color:rgba(116,107,97,0.74)]"
                              }`}
                            >
                              {getFolderBadgeCount("Filtered")}
                            </span>
                          </button>
                          <button
                            type="button"
                            onClick={switchToSharedView}
                            className={`flex w-full items-center justify-between rounded-[18px] px-4 py-3 text-left transition-[background-color,border-color,color,box-shadow,transform] duration-150 ${
                              isSharedView
                                ? "bg-[linear-gradient(180deg,var(--workspace-card-featured-start),var(--workspace-card-featured-end))] text-[var(--workspace-text)]"
                                : "text-[var(--workspace-text-soft)] hover:bg-[var(--workspace-card-subtle)]"
                            } focus-visible:outline-none`}
                          >
                            <span className="text-[0.8rem] font-medium uppercase tracking-[0.14em]">
                              Shared
                            </span>
                            <span className="text-[0.72rem] text-[var(--workspace-text-faint)]">
                              {getSharedMessageCount()}
                            </span>
                          </button>
                        </>
                      ) : null}
                    </div>
                  );
                },
              )}
              {orderedMailboxes
                .filter((candidate) => candidate.id !== mailbox.id)
                .map((candidate, index) => {
                  const dragTargetId = `mailbox-${candidate.id}`;
                  const isDragTargetActive = dragTargetKey === dragTargetId;
                  const mailboxLabel = candidate.title.endsWith("Inbox")
                    ? candidate.title
                    : `${candidate.title} Inbox`;

                  return (
                    <div key={candidate.id} className={index === 0 ? "pt-4" : ""}>
                      {index === 0 ? (
                        <div className="mb-2 px-4">
                          <div className="text-[0.62rem] font-medium uppercase tracking-[0.14em] text-[color:rgba(104,95,84,0.82)]">
                            Mailboxes
                          </div>
                          <div className="mt-1.5 h-1 w-[calc(100%-0.5rem)] rounded-full bg-[linear-gradient(90deg,rgba(212,192,168,0.9),rgba(224,208,188,0.68),rgba(235,224,209,0.38))]" />
                        </div>
                      ) : null}
                      <button
                        type="button"
                        onDragOver={(event) => {
                          if (!dragPayload) {
                            return;
                          }
                          event.preventDefault();
                          setDragTargetKey(dragTargetId);
                        }}
                        onDragLeave={() => {
                          if (dragTargetKey === dragTargetId) {
                            setDragTargetKey(null);
                          }
                        }}
                        onDrop={(event) => {
                          event.preventDefault();
                          handleDropToTarget({
                            type: "mailbox",
                            mailboxId: candidate.id,
                          });
                        }}
                        onClick={() => onOpenMailbox(candidate)}
                        className={`flex w-full items-center justify-between rounded-[18px] px-4 py-3 text-left transition-[background-color,border-color,color,box-shadow,transform] duration-150 ${
                          isDragTargetActive
                            ? "border border-[var(--workspace-border-hover)] bg-[linear-gradient(180deg,var(--workspace-selected-surface-start),var(--workspace-selected-surface-end))] text-[var(--workspace-text)] shadow-[0_10px_24px_rgba(31,42,36,0.08),inset_0_1px_0_rgba(255,255,255,0.08)]"
                            : "text-[var(--workspace-text-soft)] hover:bg-[var(--workspace-card-subtle)]"
                        } focus-visible:outline-none`}
                      >
                        <span className="text-[0.8rem] font-medium uppercase tracking-[0.14em]">
                          {mailboxLabel}
                        </span>
                      </button>
                    </div>
                  );
                })}
              <div className="pt-4">
                <div className="mb-2 px-4">
                  <div className="text-[0.62rem] font-medium uppercase tracking-[0.14em] text-[color:rgba(104,95,84,0.82)]">
                    Smart folders
                  </div>
                  <div className="mt-1.5 h-1 w-[calc(100%-0.5rem)] rounded-full bg-[linear-gradient(90deg,rgba(212,192,168,0.9),rgba(224,208,188,0.68),rgba(235,224,209,0.38))]" />
                </div>
                <div className="space-y-1">
                  {smartFolders.length > 0 ? (
                    smartFolders.map((folder) => {
                      const active = activeSmartFolderId === folder.id;
                      const isMenuOpen = smartFolderMenuId === folder.id;

                      return (
                        <div key={folder.id} className="group relative">
                          <button
                            type="button"
                            onClick={() => openSmartFolder(folder.id)}
                            className={`flex w-full items-center justify-between rounded-[18px] px-4 py-3 pr-12 text-left text-[0.8rem] font-medium uppercase tracking-[0.14em] transition-[background-color,border-color,color,box-shadow] duration-150 focus-visible:outline-none ${
                              active
                                ? "bg-[linear-gradient(180deg,var(--workspace-card-featured-start),var(--workspace-card-featured-end))] text-[var(--workspace-text)] shadow-[0_10px_24px_rgba(31,42,36,0.08),inset_0_1px_0_rgba(255,255,255,0.08)]"
                                : "text-[var(--workspace-text-soft)] hover:bg-[var(--workspace-card-subtle)]"
                            }`}
                          >
                            <span className="truncate">{folder.name}</span>
                          </button>
                          <button
                            type="button"
                            aria-label={`Manage ${folder.name}`}
                            onClick={(event) => {
                              event.stopPropagation();
                              setSmartFolderMenuId((current) =>
                                current === folder.id ? null : folder.id,
                              );
                              setContextMenuState(null);
                              setIsMoreMenuOpen(false);
                              setIsSortMenuOpen(false);
                            }}
                            className={`absolute right-2 top-1/2 inline-flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-faint)] transition-[opacity,background-color,border-color,color] duration-150 focus-visible:outline-none ${
                              isMenuOpen
                                ? "opacity-100"
                                : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100"
                            } hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface)] hover:text-[var(--workspace-text-soft)]`}
                          >
                            <svg
                              aria-hidden="true"
                              viewBox="0 0 16 16"
                              className="h-3.5 w-3.5"
                              fill="currentColor"
                            >
                              <circle cx="3.5" cy="8" r="1.1" />
                              <circle cx="8" cy="8" r="1.1" />
                              <circle cx="12.5" cy="8" r="1.1" />
                            </svg>
                          </button>
                          {isMenuOpen ? (
                            <div
                              className="absolute right-2 top-full z-20 mt-2 min-w-[180px] rounded-[18px] border border-[var(--workspace-menu-border)] bg-[var(--workspace-menu-bg)] p-2 shadow-panel"
                              onMouseDown={(event) => event.stopPropagation()}
                            >
                              <button
                                type="button"
                                onClick={() => {
                                  setSmartFolderMenuId(null);
                                  onEditSmartFolder(folder.id);
                                }}
                                className={contextMenuMainItemClass}
                              >
                                Edit folder
                              </button>
                              <button
                                type="button"
                                onClick={() => {
                                  setSmartFolderMenuId(null);
                                  setSmartFolderDeleteId(folder.id);
                                }}
                                className={contextMenuMainItemClass}
                              >
                                Delete folder
                              </button>
                            </div>
                          ) : null}
                        </div>
                      );
                    })
                  ) : (
                    <div className="px-4 py-3 text-[0.74rem] leading-6 text-[var(--workspace-text-faint)]">
                      Create lightweight views without moving email.
                    </div>
                  )}
                  <button
                    type="button"
                    onClick={onOpenSmartFolderModal}
                    className="flex w-full items-center rounded-[18px] px-4 py-3 text-left text-[0.74rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-text-faint)] transition-[background-color,color] duration-150 hover:bg-[var(--workspace-card-subtle)] hover:text-[var(--workspace-text)] focus-visible:outline-none"
                  >
                    + Add smart folder
                  </button>
                </div>
              </div>
              </div>
            </div>

            {isWideSplitView && effectiveMailListPaneWidth !== null ? (
              <button
                type="button"
                aria-label="Resize mail list and message detail panels"
                onMouseDown={handleMailSplitResizeStart}
                className="group absolute bottom-0 top-0 z-20 hidden w-6 cursor-col-resize xl:flex xl:items-stretch xl:justify-center"
                style={{
                  left:
                    MAIL_FOLDER_COLUMN_WIDTH +
                    MAIL_SPLIT_GAP +
                    effectiveMailListPaneWidth,
                }}
              >
                <span className="h-full w-px bg-transparent transition-colors duration-150 group-hover:bg-[var(--workspace-border)]" />
              </button>
            ) : null}

            <div className="flex min-h-0 min-w-0 flex-col overflow-hidden">
              <div className="mb-4 flex-none space-y-3">
                <label className="flex h-11 items-center gap-3 rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 text-[var(--workspace-text-soft)] transition-[background-color,border-color,color] duration-150 focus-within:border-[var(--workspace-border)] focus-within:bg-[var(--workspace-hover-surface)]">
                  <svg
                    aria-hidden="true"
                    viewBox="0 0 16 16"
                    className="h-3.5 w-3.5 flex-none"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.4"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <circle cx="7" cy="7" r="4.5" />
                    <path d="M10.5 10.5 14 14" />
                  </svg>
                  <input
                    ref={searchInputRef}
                    value={searchQuery}
                    onChange={(event) => setSearchQuery(event.target.value)}
                    placeholder="Search mail"
                    className="w-full bg-transparent text-[0.88rem] text-[var(--workspace-text)] outline-none placeholder:text-[var(--workspace-text-faint)]"
                  />
                  {searchQuery.length > 0 ? (
                    <button
                      type="button"
                      onClick={() => {
                        setSearchQuery("");
                        searchInputRef.current?.focus();
                      }}
                      className="inline-flex h-6 w-6 flex-none items-center justify-center rounded-full text-[var(--workspace-text-faint)] transition-colors duration-200 hover:bg-[var(--workspace-card)] hover:text-[var(--workspace-text-soft)] focus-visible:outline-none"
                      aria-label="Clear search"
                    >
                      <svg
                        aria-hidden="true"
                        viewBox="0 0 16 16"
                        className="h-3 w-3"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.5"
                        strokeLinecap="round"
                      >
                        <path d="M4 4l8 8" />
                        <path d="M12 4 4 12" />
                      </svg>
                    </button>
                  ) : null}
                </label>
                <div className="flex flex-wrap items-center gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    {(["All", "Unread", "Priority"] as MailFilter[]).map(
                      (filter) => {
                        const active = filter === activeFilter;

                        return (
                          <button
                            key={filter}
                            type="button"
                            onClick={() => setActiveFilter(filter)}
                            className={`inline-flex h-9 items-center justify-center rounded-full px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 focus-visible:outline-none ${
                              active
                                ? "bg-[linear-gradient(180deg,var(--workspace-card-featured-start),var(--workspace-card-featured-end))] text-[var(--workspace-text)]"
                                : "border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface)]"
                            }`}
                          >
                            {filter}
                          </button>
                        );
                      },
                    )}
                  </div>
                  <div className="relative">
                    <button
                      type="button"
                      aria-label="Sort messages"
                      onClick={(event) => {
                        event.stopPropagation();
                        setIsMoreMenuOpen(false);
                        setContextMenuState(null);
                        setIsSortMenuOpen((open) => !open);
                      }}
                      className="inline-flex h-9 w-10 items-center justify-center rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] text-[var(--workspace-text-faint)] transition-[background-color,border-color,color,transform] duration-150 hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface)] hover:text-[var(--workspace-text-soft)] active:scale-[0.98] focus-visible:outline-none"
                    >
                      <svg
                        aria-hidden="true"
                        viewBox="0 0 16 16"
                        className="h-4 w-4"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.55"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <path d="M5.25 2.75v10.5" />
                        <path d="M3.75 4.25 5.25 2.75l1.5 1.5" />
                        <path d="M10.75 13.25V2.75" />
                        <path d="M9.25 11.75 10.75 13.25l1.5-1.5" />
                      </svg>
                    </button>
                    {isSortMenuOpen ? (
                      <div
                        className="absolute right-0 top-full z-20 mt-2 w-[172px] rounded-[18px] border border-[var(--workspace-menu-border)] bg-[var(--workspace-menu-bg)] p-2 shadow-panel"
                        onMouseDown={(event) => event.stopPropagation()}
                      >
                        {([
                          { value: "desc", label: "Newest first" },
                          { value: "asc", label: "Oldest first" },
                        ] as const).map((option) => (
                          <button
                            key={option.value}
                            type="button"
                            onClick={() => {
                              setSortOrder(option.value);
                              setIsSortMenuOpen(false);
                            }}
                            className="flex w-full items-center justify-between rounded-[14px] px-3 py-2.5 text-left text-[0.82rem] text-[var(--workspace-text-soft)] transition-colors duration-200 hover:bg-[var(--workspace-menu-hover)]"
                          >
                            <span>{option.label}</span>
                            {sortOrder === option.value ? (
                              <svg
                                aria-hidden="true"
                                viewBox="0 0 16 16"
                                className="h-3.5 w-3.5 text-[var(--workspace-text-faint)]"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="1.7"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                              >
                                <path d="M3.5 8.5 6.5 11.5 12.5 5.5" />
                              </svg>
                            ) : null}
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </div>
              </div>

              {/* Native macOS overlay scrollbars can ignore custom thumb styling; keep the real
                  mail-list scroller dark and opt it into dark color-scheme directly. */}
              <div
                ref={mailListViewportRef}
                className="cuevion-dark-scroll cuevion-soft-scroll min-h-0 flex-1 overflow-y-auto bg-[var(--workspace-card-featured-end)] pr-1"
                style={{
                  backgroundColor: "var(--workspace-card-featured-end)",
                  colorScheme: themeMode,
                  scrollbarWidth: "thin",
                  scrollbarColor:
                    "var(--workspace-scrollbar-thumb) var(--workspace-scrollbar-track)",
                }}
              >
                {isFilteredViewEmpty ? (
                  <div className="flex min-h-full items-center justify-center px-6 pb-14 pt-4">
                    <div className="max-w-[18rem] text-center">
                      <div className="text-[1.08rem] font-semibold tracking-[-0.026em] text-[var(--workspace-text)]">
                        No filtered emails yet
                      </div>
                      <div className="mt-3 text-[0.85rem] leading-[1.9] text-[var(--workspace-text-muted)]">
                        Filtered is available in the sidebar and ready for future rules.
                      </div>
                    </div>
                  </div>
                ) : isSharedViewEmpty ? (
                  <div className="flex min-h-full items-center justify-center px-6 pb-14 pt-4">
                    <div className="max-w-[18rem] text-center">
                      <div
                        className="text-[1.08rem] font-semibold tracking-[-0.026em]"
                        style={{
                          color:
                            themeMode === "dark"
                              ? "rgba(247,244,239,0.98)"
                              : "rgba(64,58,52,0.98)",
                        }}
                      >
                        No active collaborations
                      </div>
                      <div
                        className="mt-3 text-[0.85rem] leading-[1.9]"
                        style={{
                          color:
                            themeMode === "dark"
                              ? "rgba(194,186,176,0.78)"
                              : "rgba(121,112,101,0.78)",
                        }}
                      >
                        Turn emails into shared decisions.
                        <br />
                        Start a collaboration from any message.
                      </div>
                    </div>
                  </div>
                ) : isSmartFolderViewEmpty ? (
                  <div className="flex min-h-full items-center justify-center px-6 pb-14 pt-4">
                    <div className="max-w-[18rem] text-center">
                      <div className="text-[1.08rem] font-semibold tracking-[-0.026em] text-[var(--workspace-text)]">
                        No matching emails
                      </div>
                      <div className="mt-3 text-[0.85rem] leading-[1.9] text-[var(--workspace-text-muted)]">
                        Emails stay in their inbox and appear here only when they match this folder.
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {sortedMessages.map((message) => {
                      const active = visibleSelectedMessageIds.includes(message.id);
                      const compactSnippet =
                        message.snippet.length > MAIL_LIST_PREVIEW_CHARACTER_CAP
                          ? message.snippet
                              .slice(0, MAIL_LIST_PREVIEW_CHARACTER_CAP)
                              .trimEnd()
                          : message.snippet;
                      const sharedContextHint =
                        isSharedView && message.isShared
                          ? formatSharedContextHint(message.sharedContext)
                          : null;
                      const visibleSignal = getVisibleMessageSignal(message);
                      const signal =
                        message.ui_signal ?? (message.signal === "Sent" ? "" : "NEW");
                      const senderTextClass =
                        themeMode === "dark"
                          ? message.unread
                            ? "text-[color:rgba(247,244,239,0.98)]"
                            : "text-[color:rgba(231,224,215,0.94)]"
                          : message.unread
                            ? "text-[var(--workspace-text)]"
                            : "text-[color:rgba(67,62,56,0.94)]";
                      const subjectPriorityClass =
                        themeMode === "dark"
                          ? isVisiblePriorityMessage(message)
                            ? "text-[color:rgba(250,246,240,0.99)]"
                            : "text-[color:rgba(241,235,227,0.96)]"
                          : isVisiblePriorityMessage(message)
                            ? "text-[color:rgba(67,62,56,0.98)]"
                            : "text-[color:rgba(61,56,50,0.96)]";
                      const subjectReadabilityClass = message.unread
                        ? "font-semibold"
                        : "font-medium";
                      const signalTextClass = visibleSignal
                        ? "text-[color:rgba(120,111,100,0.72)]"
                        : "text-[color:rgba(120,111,100,0.34)]";
                      const snippetTextClass = "text-[color:rgba(111,103,94,0.82)]";
                      const timeTextClass = "text-[color:rgba(120,111,100,0.74)]";
                      return (
                        <button
                          key={message.id}
                          type="button"
                          data-message-row-id={message.id}
                          draggable={!isReadOnlySmartFolderView}
                          onDragStart={(event) => {
                            if (isReadOnlySmartFolderView) {
                              event.preventDefault();
                              return;
                            }

                            handleDragStart(event, message.id);
                          }}
                          onDragEnd={() => {
                            if (isReadOnlySmartFolderView) {
                              return;
                            }

                            handleDragEnd();
                          }}
                          onClick={(event) => {
                            if (event.shiftKey) {
                              handleSelectMessage(activeFolder, message.id, {
                                isRange: true,
                              });
                              return;
                            }

                            if (event.metaKey || event.ctrlKey) {
                              handleSelectMessage(activeFolder, message.id, {
                                isToggle: true,
                              });
                              return;
                            }

                            handleSelectMessage(activeFolder, message.id);
                            markInboxMessageReadOnOpen(message);
                          }}
                          onDoubleClick={() => {
                            handleSelectMessage(activeFolder, message.id, {
                              openFull: true,
                            });
                          }}
                          onContextMenu={(event) => {
                            event.preventDefault();
                            setIsFullMessageOpen(false);
                            setIsMoreMenuOpen(false);
                            setContextMenuState({
                              messageId: message.id,
                              x: event.clientX,
                              y: event.clientY,
                              moveMenuOpen: false,
                              moveAnchorX: null,
                              moveAnchorY: null,
                              moveAnchorHeight: null,
                              learningMenuOpen: false,
                              learningAnchorX: null,
                              learningAnchorY: null,
                              learningAnchorHeight: null,
                              learningChooserOpen: false,
                              learningChooserMode: null,
                            });
                          }}
                          className={`grid min-h-[92px] w-full cursor-pointer grid-cols-[minmax(0,1fr)_max-content] gap-3 rounded-[18px] border px-4 py-2.5 text-left transition-[background-color,background-image,border-color,box-shadow,transform] duration-150 ${
                            active
                              ? "border-[var(--workspace-border-hover)] bg-[linear-gradient(180deg,var(--workspace-selected-surface-start),var(--workspace-selected-surface-end))] shadow-[0_10px_24px_rgba(31,42,36,0.08),inset_0_1px_0_rgba(255,255,255,0.08)]"
                              : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface)]"
                          } focus-visible:border-[var(--workspace-border-hover)] focus-visible:outline-none`}
                        >
                          <div className="flex min-w-0 flex-1 flex-col justify-between text-[var(--workspace-text)] opacity-100">
                            <div className="min-w-0 space-y-0.5">
                              <div className="flex items-center gap-2">
                                {active ? (
                                  <span className="inline-flex h-4 w-4 flex-none items-center justify-center rounded-full border border-[var(--workspace-selection-indicator-border)] bg-[var(--workspace-selection-indicator-bg)] text-[var(--workspace-selection-indicator-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.12)]">
                                    <svg
                                      aria-hidden="true"
                                      viewBox="0 0 16 16"
                                      className="h-2.5 w-2.5"
                                      fill="none"
                                      stroke="currentColor"
                                      strokeWidth="1.8"
                                      strokeLinecap="round"
                                      strokeLinejoin="round"
                                    >
                                      <path d="M3.5 8.5 6.5 11.5 12.5 5.5" />
                                    </svg>
                                  </span>
                                ) : null}
                                {message.unread ? (
                                  <span className={unreadAttentionDotClass} />
                                ) : null}
                                {message.flagged ? (
                                  <span
                                    aria-hidden="true"
                                    className="inline-flex h-4 w-4 flex-none items-center justify-center rounded-full border border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.12)]"
                                  >
                                    <svg
                                      viewBox="0 0 16 16"
                                      className="h-2.5 w-2.5"
                                      fill="currentColor"
                                      stroke="currentColor"
                                      strokeWidth="1.6"
                                      strokeLinecap="round"
                                      strokeLinejoin="round"
                                    >
                                      <path d="M5 3.25h6v9.25L8 10.75 5 12.5z" />
                                    </svg>
                                  </span>
                                ) : null}
                                <div className={`truncate text-[0.96rem] font-semibold tracking-[-0.014em] ${senderTextClass}`}>
                                  {message.sender}
                                </div>
                              </div>
                              <div
                                className={`truncate text-[0.97rem] leading-5 tracking-[-0.015em] ${subjectPriorityClass} ${subjectReadabilityClass}`}
                              >
                                {message.subject}
                              </div>
                              <div className={`truncate text-[0.78rem] leading-5 ${snippetTextClass}`}>
                                {compactSnippet}
                              </div>
                              {signal ? (
                                <div
                                  className={`pt-0.5 text-[0.6rem] font-medium uppercase tracking-[0.12em] ${signalTextClass}`}
                                >
                                  <span className="text-xs opacity-70">
                                    {signal}
                                  </span>
                                </div>
                              ) : null}
                              {sharedContextHint ? (
                                <div className="pt-1 text-[0.68rem] leading-5 text-[color:rgba(120,111,100,0.72)]">
                                  {sharedContextHint}
                                </div>
                              ) : null}
                            </div>
                          </div>
                          <div className={`flex-none self-start pt-0 text-right text-[0.64rem] font-medium uppercase tracking-[0.12em] ${timeTextClass}`}>
                            {message.time}
                          </div>
                        </button>
                      );
                    })}
                    {visibleMessages.length === 0 ? (
                      <div className="rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-6 text-[0.84rem] leading-6 text-[var(--workspace-text-faint)]">
                        {activeSmartFolder
                          ? "No messages in this smart folder match the current search or filter."
                          : activeFolder === "Drafts"
                          ? "No drafts in this mailbox yet."
                          : "No messages match the current search or filter."}
                      </div>
                    ) : null}
                  </div>
                )}
              </div>
            </div>

            <div className="flex h-full min-h-0 min-w-0 self-stretch flex-col overflow-hidden rounded-[24px] border border-[var(--workspace-border-soft)] bg-[linear-gradient(180deg,var(--workspace-card-featured-start),var(--workspace-card-featured-end))]">
              {/* Native macOS overlay scrollbars can ignore custom thumb styling; keep the real
                  reading-pane scroller dark and opt it into dark color-scheme directly. */}
              <div
                className="cuevion-dark-scroll cuevion-soft-scroll min-h-0 flex-1 overflow-y-auto bg-[var(--workspace-card-featured-end)] p-5 pr-4 md:p-6 md:pr-5"
                style={{
                  backgroundColor: "var(--workspace-card-featured-end)",
                  colorScheme: themeMode,
                  scrollbarWidth: "thin",
                  scrollbarColor:
                    "var(--workspace-scrollbar-thumb) var(--workspace-scrollbar-track)",
                }}
              >
                {isMultiSelectActive ? (
                  <div className="space-y-3">
                    <div className="text-[0.72rem] font-medium uppercase tracking-[0.18em] text-[var(--workspace-text-faint)]">
                      Selection
                    </div>
                    <div className="text-[1.3rem] font-medium tracking-tight text-[var(--workspace-text)] md:text-[1.45rem]">
                      {visibleSelectedMessageIds.length} messages selected
                    </div>
                    <div className="text-[0.92rem] leading-7 text-[var(--workspace-text-soft)]">
                      Bulk actions from the toolbar and context menu apply to the current selection.
                    </div>
                  </div>
                ) : selectedMessage ? (
	                  <div className="space-y-6">
	                    {(() => {
	                      const linkedReview = getLinkedReviewForMessage(selectedMessage.id);
	                      const linkedReviewLabel = getLinkedReviewBadgeLabel(selectedMessage.id);

	                      return (
	                    <div className="flex items-start justify-between gap-4">
	                      <div className="space-y-3">
	                        <h2 className="text-[1.3rem] font-medium tracking-tight text-[var(--workspace-text)] md:text-[1.45rem]">
	                          {selectedMessage.subject}
	                        </h2>
	                        {linkedReview && linkedReviewLabel ? (
	                          <button
	                            type="button"
		                            onClick={() => onOpenLinkedReview(linkedReview.target)}
		                            className="inline-flex rounded-full border border-[var(--workspace-border)] bg-[var(--workspace-hover-surface)] px-3 py-1 text-[0.68rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-text)] transition-[background-color,border-color,color] duration-150 hover:border-[var(--workspace-border-hover)] hover:bg-[var(--workspace-hover-surface-strong)] focus-visible:outline-none"
	                          >
	                            {linkedReviewLabel}
	                          </button>
	                        ) : null}
	                        <div className="space-y-0.5 text-[0.88rem] leading-[1.5] text-[var(--workspace-text-soft)]">
	                          <div>
	                            <span className="text-[var(--workspace-text-faint)]">Subject</span>{" "}
                            {selectedMessage.subject}
                          </div>
                          <div>
                            <span className="text-[var(--workspace-text-faint)]">From</span>{" "}
                            {selectedMessage.from}
                          </div>
                          <div>
                            <span className="text-[var(--workspace-text-faint)]">To</span>{" "}
                            {selectedMessage.to}
                          </div>
                          <div>
                            <span className="text-[var(--workspace-text-faint)]">Time</span>{" "}
                            {selectedMessage.timestamp}
                          </div>
                        </div>
                      </div>
	                      <div className="flex items-center gap-4">
	                        {aiSuggestionsEnabled ? (
	                        <ReadingLearningButton
                          open={isReadingLearningMenuOpen}
                          triggerId="reading-pane"
                          onClick={(event) => {
                            event.stopPropagation();
                            const rect = event.currentTarget.getBoundingClientRect();
                            toggleReadingLearningMenu("reading-pane", {
                              top: rect.top,
                              left: rect.left,
                              width: rect.width,
                              height: rect.height,
                            });
                          }}
	                        />
	                        ) : null}
		                      </div>
		                    </div>
	                      );
	                    })()}

	                  {aiSuggestionsEnabled ? renderAIDecisionBlock(selectedMessage) : null}

	                  {selectedMessage.focusSignal === "attention" ? (
	                    <div className="text-[0.88rem] leading-6 text-[color:rgba(88,82,74,0.92)]">
	                      Needs your attention
                    </div>
                  ) : null}

                  <div className="space-y-2">
                    {shouldShowMessageSummary(selectedMessage.snippet, selectedMessage.body) ? (
                      <div className="mt-3 w-[94%] text-[0.9rem] leading-[1.95] text-[color:rgba(95,88,80,0.9)]">
                        {selectedMessage.snippet}
                      </div>
                    ) : null}

                    {renderMessageCollaboration(selectedMessage)}

                    {renderBehaviorSuggestion(selectedMessage)}

                    {selectedMessage.isShared && selectedMessage.sharedContext ? (
                      <div className="w-[94%] text-[0.82rem] leading-6 text-[color:rgba(120,111,100,0.68)]">
                        {formatSharedContextDetail(selectedMessage.sharedContext)}
                      </div>
                    ) : null}

                    {renderThreadTimeline(selectedMessage, "split")}
                    {renderMessageActions(selectedMessage, "split")}
                  </div>

                  {selectedMessage.id === "main-1" ? (
                    <div className="grid gap-4 md:grid-cols-2">
                      <div className="overflow-hidden rounded-[20px] border border-[var(--workspace-border-soft)] bg-[linear-gradient(180deg,var(--workspace-preview-surface-start),var(--workspace-preview-surface-end))]">
                        <div className="flex h-[180px] items-end bg-[radial-gradient(circle_at_top_left,rgba(255,255,255,0.34),transparent_42%),linear-gradient(135deg,rgba(184,165,146,0.32),rgba(138,111,89,0.16))] p-4">
                          <div className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                            Artwork Preview 01
                          </div>
                        </div>
                      </div>
                      <div className="overflow-hidden rounded-[20px] border border-[var(--workspace-border-soft)] bg-[linear-gradient(180deg,var(--workspace-preview-surface-start),var(--workspace-preview-surface-end))]">
                        <div className="flex h-[180px] items-end bg-[radial-gradient(circle_at_top_right,rgba(255,255,255,0.34),transparent_42%),linear-gradient(135deg,rgba(169,145,122,0.28),rgba(120,96,78,0.14))] p-4">
                          <div className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                            Artwork Preview 02
                          </div>
                        </div>
                      </div>
                    </div>
                  ) : null}

                  <div className="space-y-3">
                    <div className="text-[0.72rem] font-medium uppercase tracking-[0.18em] text-[var(--workspace-text-faint)]">
                      Attachments
                    </div>
                    <div className="flex flex-wrap gap-3">
                      {(selectedMessage.attachments ?? []).map((attachment) =>
                        renderAttachmentItem(attachment),
                      )}
                      {!selectedMessage.attachments?.length ? (
                        <div className="text-[0.82rem] leading-6 text-[var(--workspace-text-faint)]">
                          No attachments
                        </div>
                      ) : null}
                    </div>
                  </div>
                  </div>
                ) : !isSharedView && !activeSmartFolder && activeFolder === "Filtered" ? (
                  <div className="text-[0.92rem] leading-7 text-[var(--workspace-text-soft)]">
                    No filtered emails yet.
                  </div>
                ) : isSharedView ? null : activeSmartFolder ? (
                  <div className="text-[0.92rem] leading-7 text-[var(--workspace-text-soft)]">
                    No messages in this smart folder yet.
                  </div>
                ) : (
                  <div className="text-[0.92rem] leading-7 text-[var(--workspace-text-soft)]">
                    {`No messages in ${activeFolder.toLowerCase()} for this mailbox yet.`}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        <SettingsConfirmationModal
          open={smartFolderDeleteId !== null}
          themeMode={themeMode}
          title="Delete this smart folder?"
          description="Emails will not be removed."
          confirmLabel="Delete"
          onCancel={() => setSmartFolderDeleteId(null)}
          onConfirm={() => {
            const deletingActiveFolder = smartFolderDeleteId === activeSmartFolderId;

            if (smartFolderDeleteId) {
              onDeleteSmartFolder(smartFolderDeleteId);
            }

            if (deletingActiveFolder) {
              setActiveSmartFolderId(null);
              setActiveFolder("Inbox");
              const nextMessageId = messageCollections.Inbox[0]?.id ?? null;
              setSelectionState(
                nextMessageId ? [nextMessageId] : [],
                nextMessageId,
                nextMessageId,
              );
            }

            setSmartFolderDeleteId(null);
          }}
        />

        <SettingsConfirmationModal
          open={isEmptyTrashConfirmationOpen}
          themeMode={themeMode}
          title="Empty trash?"
          description="This will remove all items from Trash in Cuevion. Your original emails remain unchanged in your email provider for now."
          confirmLabel="Empty Trash"
          onCancel={() => setIsEmptyTrashConfirmationOpen(false)}
          onConfirm={emptyTrash}
        />

        {contextMenuState && contextMenuMessage && contextMenuPosition
          ? createPortal(
              <div
                data-theme={themeMode}
                className="cuevion-dark-scroll cuevion-soft-scroll fixed z-30 min-w-[238px] overflow-y-auto rounded-[20px] border border-[var(--workspace-menu-border)] bg-[var(--workspace-menu-bg)] p-2 shadow-panel"
                style={contextMenuPosition}
                onMouseDown={(event) => event.stopPropagation()}
              >
                <div className="space-y-1">
                  <button
                    type="button"
                    onClick={() => {
                      handleSelectMessage(activeFolder, contextMenuMessage.id);
                      closeMenus();
                    }}
                    className={contextMenuMainItemClass}
                  >
                    Open
                  </button>
                  <button
                    type="button"
                    onClick={closeMenus}
                    className={contextMenuMainItemClass}
                  >
                    Reply
                  </button>
                  <button
                    type="button"
                    onClick={closeMenus}
                    className={contextMenuMainItemClass}
                  >
                    Reply All
                  </button>
                  <button
                    type="button"
                    onClick={closeMenus}
                    className={contextMenuMainItemClass}
                  >
                    Forward
                  </button>
                  {!isReadOnlySmartFolderView ? (
                    <button
                      type="button"
                      onClick={() => {
                        setSelectionState(
                          [contextMenuMessage.id],
                          contextMenuMessage.id,
                          contextMenuMessage.id,
                        );
                        openShareCollaboration(contextMenuMessage.id);
                      }}
                      className={contextMenuMainItemClass}
                    >
                      Start collaboration…
                    </button>
                  ) : null}
                </div>

                <div className="my-2 h-px bg-[var(--workspace-divider)]" />

                <div className="space-y-1">
                  <button
                    type="button"
                    onClick={() => {
                      const primaryContextMessageId =
                        contextMenuMessage?.id ?? contextMenuSelectionIds[0] ?? null;

                      if (primaryContextMessageId) {
                        toggleMessageFlagState(primaryContextMessageId);
                        return;
                      }

                      closeMenus();
                    }}
                    className={contextMenuMainItemClass}
                  >
                    {Boolean(contextMenuMessage?.flagged) ? "Remove flag" : "Flag"}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      onSetManualPriority(
                        contextMenuMessage.id,
                        !isVisiblePriorityMessage(contextMenuMessage),
                      );
                      closeMenus();
                    }}
                    className={contextMenuMainItemClass}
                  >
                    {isVisiblePriorityMessage(contextMenuMessage)
                      ? "Remove priority"
                      : "Mark as priority"}
                  </button>
                  {isVisiblePriorityMessage(contextMenuMessage) ? (
                    <button
                      type="button"
                      onClick={() => {
                        onSetManualPriority(contextMenuMessage.id, false);
                        closeMenus();
                      }}
                      className={contextMenuMainItemClass}
                    >
                      Mark as done
                    </button>
                  ) : null}
                </div>

                {!isReadOnlySmartFolderView ? (
                  <>
                    <div className="my-2 h-px bg-[var(--workspace-divider)]" />

                    <div className="space-y-1">
                      <button
                        type="button"
                        onClick={() =>
                          setMessagesUnreadState(
                            activeFolder,
                            contextMenuSelectionIds,
                            !contextMenuSelectionIds.some((messageId) =>
                              folderMessages.find((message) => message.id === messageId)?.unread,
                            ),
                          )
                        }
                        className={contextMenuMainItemClass}
                      >
                        {contextMenuSelectionIds.some((messageId) =>
                          folderMessages.find((message) => message.id === messageId)?.unread,
                        )
                          ? "Mark as read"
                          : "Mark as unread"}
                      </button>
                    </div>

                    <div className="my-2 h-px bg-[var(--workspace-divider)]" />

                    <div className="space-y-1">
                      <button
                        type="button"
                        onClick={() => {
                          if (isSharedView) {
                            moveMessagesAcrossWorkspace(
                              mailbox.id,
                              "Archive",
                              contextMenuSelectionIds,
                            );
                            return;
                          }

                          moveMessages(
                            mailbox.id,
                            activeFolder,
                            mailbox.id,
                            "Archive",
                            contextMenuSelectionIds,
                          );
                        }}
                        className={contextMenuMainItemClass}
                      >
                        Archive
                      </button>
                      <button
                        type="button"
                        onClick={() => deleteMessages(contextMenuSelectionIds)}
                        className={contextMenuMainItemClass}
                      >
                        Delete
                      </button>
                    </div>

                    <div className="my-2 h-px bg-[var(--workspace-divider)]" />

                    <div className="space-y-1">
                      <ContextSubmenuTriggerRow
                        label="Move to..."
                        active={Boolean(contextMenuState?.moveMenuOpen)}
                        onClick={(event) => {
                          const rect = event.currentTarget.getBoundingClientRect();
                          setContextMenuState((current) =>
                            current
                              ? {
                                  ...current,
                                  moveMenuOpen: !current.moveMenuOpen,
                                  moveAnchorX: rect.right,
                                  moveAnchorY: rect.top,
                                  moveAnchorHeight: rect.height,
                                  learningMenuOpen: false,
                                  learningChooserOpen: false,
                                  learningChooserMode: null,
                                }
                              : current,
                          );
                        }}
                        onMouseEnter={(event) => {
                          const rect = event.currentTarget.getBoundingClientRect();
                          setContextMenuState((current) =>
                            current
                              ? {
                                  ...current,
                                  moveMenuOpen: true,
                                  moveAnchorX: rect.right,
                                  moveAnchorY: rect.top,
                                  moveAnchorHeight: rect.height,
                                  learningMenuOpen: false,
                                  learningChooserOpen: false,
                                  learningChooserMode: null,
                                }
                              : current,
                          );
                        }}
                      />
                      {aiSuggestionsEnabled ? (
                        <ContextSubmenuTriggerRow
                          label="Learning"
                          active={Boolean(contextMenuState?.learningMenuOpen)}
                          onClick={(event) => {
                            const rect = event.currentTarget.getBoundingClientRect();
                            setContextMenuState((current) =>
                              current
                                ? {
                                    ...current,
                                    learningMenuOpen: !current.learningMenuOpen,
                                    learningAnchorX: rect.right,
                                    learningAnchorY: rect.top,
                                    learningAnchorHeight: rect.height,
                                    moveMenuOpen: false,
                                    learningChooserOpen: false,
                                    learningChooserMode: null,
                                  }
                                : current,
                            );
                          }}
                          onMouseEnter={(event) => {
                            const rect = event.currentTarget.getBoundingClientRect();
                            setContextMenuState((current) =>
                              current
                                ? {
                                    ...current,
                                    learningMenuOpen: true,
                                    learningAnchorX: rect.right,
                                    learningAnchorY: rect.top,
                                    learningAnchorHeight: rect.height,
                                    moveMenuOpen: false,
                                    learningChooserOpen: false,
                                    learningChooserMode: null,
                                  }
                                : current,
                            );
                          }}
                        />
                      ) : null}
                    </div>
                  </>
                ) : null}
              </div>,
              document.body,
            )
          : null}
        {contextMenuState &&
        contextMenuMessage &&
        contextMenuState.moveMenuOpen &&
        moveSubmenuPosition
          ? createPortal(
              <div
                data-theme={themeMode}
                className="cuevion-dark-scroll cuevion-soft-scroll fixed z-[31] w-[210px] max-h-[360px] overflow-y-auto rounded-[20px] border border-[var(--workspace-menu-border)] bg-[var(--workspace-menu-bg)] p-2 shadow-panel"
                style={{
                  ...moveSubmenuPosition,
                  colorScheme: themeMode,
                  scrollbarWidth: "thin",
                  scrollbarColor:
                    "var(--workspace-scrollbar-thumb) var(--workspace-scrollbar-track)",
                }}
                onMouseDown={(event) => event.stopPropagation()}
              >
                <div className="space-y-1">
                  {moveTargets.map((target, index) => (
                    <button
                      key={`${target.type}-${target.label}`}
                      type="button"
                      onClick={() => {
                        if (target.type === "folder") {
                          if (isSharedView) {
                            moveMessagesAcrossWorkspace(
                              mailbox.id,
                              target.folder,
                              contextMenuSelectionIds,
                            );
                            return;
                          }

                          if (activeSmartFolder) {
                            moveMessagesToFolderAcrossWorkspace(
                              target.folder,
                              contextMenuSelectionIds,
                            );
                            return;
                          }

                          moveMessages(
                            mailbox.id,
                            activeFolder,
                            mailbox.id,
                            target.folder,
                            contextMenuSelectionIds,
                          );
                          return;
                        }

                        if (isSharedView) {
                          moveMessagesAcrossWorkspace(
                            target.mailboxId,
                            "Inbox",
                            contextMenuSelectionIds,
                          );
                          return;
                        }

                        moveMessages(
                          mailbox.id,
                          activeFolder,
                          target.mailboxId,
                          "Inbox",
                          contextMenuSelectionIds,
                        );
                      }}
                      className={contextMenuItemClass}
                    >
                      {target.label}
                    </button>
                  ))}
                </div>
              </div>,
              document.body,
            )
          : null}
        {aiSuggestionsEnabled &&
        contextMenuState &&
        contextMenuMessage &&
        contextMenuState.learningMenuOpen &&
        learningSubmenuPosition
          ? createPortal(
              <div
                data-theme={themeMode}
                className="cuevion-dark-scroll cuevion-soft-scroll fixed z-[31] w-[210px] max-h-[360px] overflow-y-auto rounded-[20px] border border-[var(--workspace-menu-border)] bg-[var(--workspace-menu-bg)] p-2 shadow-panel"
                style={{
                  ...learningSubmenuPosition,
                  colorScheme: themeMode,
                  scrollbarWidth: "thin",
                  scrollbarColor:
                    "var(--workspace-scrollbar-thumb) var(--workspace-scrollbar-track)",
                }}
                onMouseDown={(event) => event.stopPropagation()}
              >
                <div className="space-y-1">
                  <button
                    type="button"
                    onClick={() => {
                      console.log("learning_show_less");
                      closeMenus();
                    }}
                    className={contextMenuItemClass}
                  >
                    Show less like this
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      console.log("learning_show_more");
                      closeMenus();
                    }}
                    className={contextMenuItemClass}
                  >
                    Show more like this
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      console.log("learning_this_is_important");
                      closeMenus();
                    }}
                    className={contextMenuItemClass}
                  >
                    This is important
                  </button>
                </div>
                <div className="my-2 h-px bg-[var(--workspace-divider)]" />
                <div className="space-y-1">
                  {learningMailboxTargets.map((target) => (
                    <button
                      key={`context-learning-${target.mailboxId}`}
                      type="button"
                      onClick={() => {
                        console.log(`learning_belongs_${target.logValue}`);
                        closeMenus();
                      }}
                      className={contextMenuItemClass}
                    >
                      {target.belongsLabel}
                    </button>
                  ))}
                </div>
                <div className="my-2 h-px bg-[var(--workspace-divider)]" />
                <div className="space-y-1">
                  <button
                    type="button"
                    onClick={() => {
                      console.log("learning_this_is_spam");
                      closeMenus();
                    }}
                    className={contextMenuItemClass}
                  >
                    This is spam
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      console.log("learning_show_fewer_sender");
                      closeMenus();
                    }}
                    className={contextMenuItemClass}
                  >
                    Show fewer emails from this sender
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      console.log("learning_move_future_to_spam");
                      closeMenus();
                    }}
                    className={contextMenuItemClass}
                  >
                    Move future emails like this to Spam
                  </button>
                </div>
              </div>,
              document.body,
            )
          : null}
        {aiSuggestionsEnabled &&
        contextMenuState &&
        contextMenuMessage &&
        contextMenuState.learningChooserOpen &&
        contextMenuState.learningChooserMode &&
        learningChooserPosition
          ? createPortal(
              <div
                data-theme={themeMode}
                className="cuevion-dark-scroll cuevion-soft-scroll fixed z-[31] w-[228px] max-h-[320px] overflow-y-auto rounded-[20px] border border-[var(--workspace-menu-border)] bg-[var(--workspace-menu-bg)] p-2 shadow-panel"
                style={{
                  ...learningChooserPosition,
                  colorScheme: themeMode,
                  scrollbarWidth: "thin",
                  scrollbarColor:
                    "var(--workspace-scrollbar-thumb) var(--workspace-scrollbar-track)",
                }}
                onMouseDown={(event) => event.stopPropagation()}
              >
                <div className="px-3 pb-2 pt-1 text-[0.66rem] font-medium uppercase tracking-[0.12em] text-[var(--workspace-text-faint)]">
                  {contextMenuState.learningChooserMode === "type"
                    ? "Move similar emails to"
                    : "Move emails from this sender to"}
                </div>
                <div className="space-y-1">
                  {learningMailboxTargets.map((target) => (
                    <button
                      key={`${contextMenuState.learningChooserMode}-${target.mailboxId}`}
                      type="button"
                      onClick={() => {
                        console.log(
                          contextMenuState.learningChooserMode === "type"
                            ? `learning_move_type_${target.logValue}`
                            : `learning_move_sender_${target.logValue}`,
                        );
                        closeMenus();
                      }}
                      className={contextMenuItemClass}
                    >
                      {target.chooserLabel}
                    </button>
                  ))}
                </div>
              </div>,
              document.body,
            )
          : null}
        {aiSuggestionsEnabled && isReadingLearningMenuOpen && readingLearningMenuPosition
          ? createPortal(
              <div
                ref={readingLearningMenuRef}
                data-theme={themeMode}
                className="fixed z-[31] w-[244px] rounded-[20px] border border-[var(--workspace-menu-border)] bg-[var(--workspace-menu-bg)] p-2 shadow-panel"
                style={readingLearningMenuPosition}
                onMouseDown={(event) => event.stopPropagation()}
              >
                <div className="space-y-1">
                  <button
                    type="button"
                    onClick={() => {
                      console.log("reading_learning_show_less");
                      closeMenus();
                    }}
                    className={contextMenuItemClass}
                  >
                    Show less like this
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      console.log("reading_learning_show_more");
                      closeMenus();
                    }}
                    className={contextMenuItemClass}
                  >
                    Show more like this
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      console.log("reading_learning_this_is_important");
                      closeMenus();
                    }}
                    className={contextMenuItemClass}
                  >
                    This is important
                  </button>
                </div>

                <div className="my-2 h-px bg-[var(--workspace-divider)]" />

                <div className="space-y-1">
                  {learningMailboxTargets.map((target) => (
                    <button
                      key={`reading-learning-${target.mailboxId}`}
                      type="button"
                      onClick={() => {
                        console.log(`reading_learning_belongs_${target.logValue}`);
                        closeMenus();
                      }}
                      className={contextMenuItemClass}
                    >
                      {target.belongsLabel}
                    </button>
                  ))}
                </div>

                <div className="my-2 h-px bg-[var(--workspace-divider)]" />

                <div className="space-y-1">
                  <button
                    type="button"
                    onClick={() => {
                      console.log("reading_learning_this_is_spam");
                      closeMenus();
                    }}
                    className={contextMenuItemClass}
                  >
                    This is spam
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      console.log("reading_learning_show_fewer_sender");
                      closeMenus();
                    }}
                    className={contextMenuItemClass}
                  >
                    Show fewer emails from this sender
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      console.log("reading_learning_move_future_to_spam");
                      closeMenus();
                    }}
                    className={contextMenuItemClass}
                  >
                    Move future emails like this to Spam
                  </button>
                </div>
              </div>,
              document.body,
            )
          : null}

        {shareCollaborationMessage
          ? createPortal(
              <WorkspaceModalLayer>
                <div
                  data-theme={themeMode}
                  data-share-collaboration-modal
                  className="w-full max-w-[620px] overflow-hidden rounded-[28px] border border-[var(--workspace-border)] bg-[var(--workspace-modal-bg)] p-6 shadow-[0_28px_80px_rgba(61,44,32,0.18),0_10px_26px_rgba(61,44,32,0.1)]"
                  onMouseDown={(event) => event.stopPropagation()}
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="space-y-2">
                      <h2 className="text-[1.45rem] font-medium tracking-tight text-[var(--workspace-text)]">
                        Start collaboration
                      </h2>
                      <p className="max-w-[30rem] text-[0.92rem] leading-7 text-[var(--workspace-text-soft)]">
                        {`on "${shareCollaborationMessage.subject}"`}
                      </p>
                    </div>
                    <CloseActionButton onClick={closeShareCollaboration} />
                  </div>

                  <div className="mt-6 space-y-6">
                    <label className="block space-y-2">
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                          Participants
                        </span>
                        <button
                          type="button"
                          onClick={() =>
                            setIsInlineCollaborationInviteOpen((current) => !current)
                          }
                          className="inline-flex items-center rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-3 py-1 text-[0.66rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-text-soft)] transition-[background-color,border-color,color] duration-150 hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)] hover:text-[var(--workspace-text)] focus-visible:outline-none"
                        >
                          + Invite
                        </button>
                      </div>
                      {isInlineCollaborationInviteOpen ? (
                        <div className="relative">
                          <input
                            value={collaborationInviteDraft}
                            onChange={(event) => setCollaborationInviteDraft(event.target.value)}
                            onKeyDown={(event) => {
                              if (event.key === "Enter" && isCollaborationInviteDraftValid) {
                                event.preventDefault();
                                addInlineCollaborationInvite();
                              }
                            }}
                            placeholder="Enter email..."
                            autoCorrect="off"
                            autoCapitalize="none"
                            spellCheck={false}
                            className="w-full rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3 pr-28 text-[0.9rem] leading-6 text-[var(--workspace-text-soft)] outline-none placeholder:text-[var(--workspace-text-faint)]"
                          />
                          {isCollaborationInviteDraftValid ? (
                            <button
                              type="button"
                              onClick={addInlineCollaborationInvite}
                              className="absolute right-2 top-1/2 inline-flex h-9 -translate-y-1/2 items-center rounded-full border border-[color:rgba(109,154,105,0.34)] bg-[linear-gradient(180deg,rgba(193,221,186,0.92),rgba(143,185,136,0.94))] px-3 text-[0.72rem] font-medium text-[color:rgba(45,74,45,0.98)] shadow-[inset_0_1px_0_rgba(255,255,255,0.22),0_8px_18px_rgba(108,148,102,0.14)] transition-[background-color,border-color,color,box-shadow] duration-150 hover:border-[color:rgba(95,136,92,0.42)] hover:bg-[linear-gradient(180deg,rgba(201,227,194,0.96),rgba(151,193,144,0.96))] focus-visible:outline-none"
                            >
                              + Invite
                            </button>
                          ) : null}
                        </div>
                      ) : null}
                      <div className="grid gap-2.5 md:grid-cols-3">
                        {collaborationSelectablePeople.map((person) => (
                          <div key={person.id} className="relative min-w-0">
                            <button
                              type="button"
                              onClick={() => setCollaborationPersonId(person.id)}
                              className={`w-full min-w-0 rounded-[18px] border px-4 py-3 pr-10 text-left transition-[background-color,border-color,color] duration-150 focus-visible:outline-none ${
                                collaborationPersonId === person.id
                                  ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-card-featured-start),var(--workspace-card-featured-end))]"
                                  : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface)]"
                              }`}
                            >
                              <div className="truncate pr-1 text-[0.92rem] font-medium leading-6 text-[var(--workspace-text)]">
                                {person.name}
                              </div>
                              <div
                                title={person.email}
                                className="mt-0.5 truncate pr-1 text-[0.8rem] leading-5 text-[color:rgba(120,111,100,0.72)]"
                              >
                                {person.email}
                                {person.status === "invited" ? " (invited)" : ""}
                              </div>
                            </button>
                            {person.status === "invited" || collaborationPersonId === person.id ? (
                              <button
                                type="button"
                                aria-label={`Remove ${person.name}`}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  if (person.status === "invited") {
                                    setCollaborationInviteOptions((current) =>
                                      current.filter((entry) => entry.id !== person.id),
                                    );
                                  }
                                  if (collaborationPersonId === person.id) {
                                    setCollaborationPersonId("");
                                  }
                                }}
                                className="absolute right-2 top-2 inline-flex h-6 w-6 items-center justify-center rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] text-[0.82rem] leading-none text-[var(--workspace-text-faint)] transition-[background-color,border-color,color] duration-150 hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)] hover:text-[var(--workspace-text)] focus-visible:outline-none"
                              >
                                ×
                              </button>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    </label>

                    <div className="space-y-2.5">
                      <div className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                        What needs to happen
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {([
                          { value: "needs_review", label: "Needs input" },
                          { value: "needs_action", label: "Take action" },
                          { value: "note_only", label: "Just a note" },
                        ] as const).map((option) => (
                          <button
                            key={option.value}
                            type="button"
                            onClick={() => setCollaborationRequestType(option.value)}
                            className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color] duration-150 focus-visible:outline-none ${
                              collaborationRequestType === option.value
                                ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)]"
                                : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                            }`}
                          >
                            {option.label}
                          </button>
                        ))}
                      </div>
                    </div>

                    <label className="block space-y-2.5">
                      <span className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                        Add a note
                      </span>
                      <textarea
                        value={collaborationNote}
                        onChange={(event) => setCollaborationNote(event.target.value)}
                        rows={4}
                        placeholder="Add context if helpful"
                        className="w-full resize-none rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3 text-[0.92rem] leading-7 text-[var(--workspace-text-soft)] outline-none placeholder:text-[var(--workspace-text-faint)]"
                      />
                      <div className="text-[0.78rem] leading-6 text-[color:rgba(120,111,100,0.68)]">
                        This note is internal only
                      </div>
                    </label>

                    <div className="text-[0.82rem] leading-6 text-[var(--workspace-text-faint)]">
                      This email will appear in Shared until the collaboration is marked as done.
                    </div>
                  </div>

                  <div className="mt-6 flex items-center justify-end gap-3">
                    <button
                      type="button"
                      onClick={createMessageCollaboration}
                      disabled={!collaborationPersonId}
                      className={
                        collaborationPersonId
                          ? "inline-flex h-10 items-center justify-center rounded-full bg-pine px-5 text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[color:rgba(251,248,242,0.98)] transition-[background-color,transform] duration-150 hover:bg-moss active:scale-[0.99] focus-visible:outline-none"
                          : "inline-flex h-10 cursor-not-allowed items-center justify-center rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-5 text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-soft)] opacity-45 transition-[opacity] duration-150 focus-visible:outline-none"
                      }
                    >
                      Start collaboration
                    </button>
                  </div>
                </div>
              </WorkspaceModalLayer>,
              document.body,
            )
          : null}

        {activeCollaborationMessage?.collaboration
          ? createPortal(
              <WorkspaceModalLayer>
                <div
                  data-theme={themeMode}
                  data-collaboration-thread-modal
                  className="w-full max-w-[700px] overflow-hidden rounded-[28px] border border-[var(--workspace-border)] bg-[var(--workspace-modal-bg)] p-6 shadow-[0_28px_80px_rgba(61,44,32,0.18),0_10px_26px_rgba(61,44,32,0.1)]"
                  onMouseDown={(event) => event.stopPropagation()}
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="space-y-2">
                      <h2 className="text-[1.45rem] font-medium tracking-tight text-[var(--workspace-text)]">
                        Collaboration
                      </h2>
                      <div className="space-y-2">
                        <div className="flex items-center justify-between gap-3">
                          <div className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                            Participants
                          </div>
                          <button
                            type="button"
                            onClick={() => setIsInviteParticipantOpen((current) => !current)}
                            className="inline-flex items-center rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-3 py-1 text-[0.72rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-text-soft)] transition-[background-color,border-color,color] duration-150 hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)] hover:text-[var(--workspace-text)] focus-visible:outline-none"
                          >
                            + Invite
                          </button>
                        </div>
                        <div className="flex flex-wrap gap-2">
                          {activeCollaborationParticipants.map((participant) => (
                            <div
                              key={`participant-${participant.id}`}
                              className="inline-flex max-w-full items-center gap-2 rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-3 py-1.5 text-[0.82rem] leading-6 text-[var(--workspace-text-soft)]"
                            >
                              <span className="truncate pr-0.5 text-[var(--workspace-text)]">
                                {participant.name || participant.email}
                                {participant.status === "invited" ? " (invited)" : ""}
                              </span>
                              <button
                                type="button"
                                aria-label={`Remove ${participant.name || participant.email}`}
                                onClick={() =>
                                  removeParticipantFromCollaboration(
                                    activeCollaborationMessage.id,
                                    participant.id,
                                  )
                                }
                                className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[0.78rem] leading-none text-[var(--workspace-text-faint)] transition-colors duration-150 hover:text-[var(--workspace-text)] focus-visible:outline-none"
                              >
                                ×
                              </button>
                            </div>
                          ))}
                        </div>
                      </div>
                      <p className="text-[0.84rem] leading-6 text-[var(--workspace-text-faint)]">
                        Regarding: {activeCollaborationMessage.subject}
                      </p>
                    </div>
                    <CloseActionButton onClick={closeCollaborationOverlay} />
                  </div>

                  {isInviteParticipantOpen ? (
                    <div className="mt-4 rounded-[22px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] p-4">
                      <div className="space-y-4">
                        <div className="space-y-2">
                          <div className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                            Existing users
                          </div>
                          <div className="flex flex-wrap gap-2">
                            {collaborationPeople.map((person) => (
                              <button
                                key={person.id}
                                type="button"
                                onClick={() => {
                                  setCollaborationInvitePersonId(person.id);
                                  setCollaborationInviteEmail("");
                                }}
                                className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color] duration-150 focus-visible:outline-none ${
                                  collaborationInvitePersonId === person.id
                                    ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)]"
                                    : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                                }`}
                              >
                                {person.name}
                              </button>
                            ))}
                          </div>
                        </div>
                        <label className="block space-y-2">
                          <span className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                            Email address
                          </span>
                          <input
                            value={collaborationInviteEmail}
                            onChange={(event) => {
                              setCollaborationInviteEmail(event.target.value);
                              setCollaborationInvitePersonId("");
                            }}
                            placeholder="name@example.com"
                            autoCorrect="off"
                            autoCapitalize="none"
                            spellCheck={false}
                            className="w-full rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-3 text-[0.9rem] leading-6 text-[var(--workspace-text-soft)] outline-none placeholder:text-[var(--workspace-text-faint)]"
                          />
                        </label>
                        <div className="flex items-center justify-end gap-3">
                          <button
                            type="button"
                            onClick={() => {
                              setIsInviteParticipantOpen(false);
                              setCollaborationInviteEmail("");
                              setCollaborationInvitePersonId("");
                            }}
                            className={modalTertiaryActionButtonClass}
                          >
                            Close
                          </button>
                          <button
                            type="button"
                            onClick={() =>
                              addParticipantToCollaboration(activeCollaborationMessage.id)
                            }
                            disabled={
                              !collaborationInvitePersonId &&
                              collaborationInviteEmail.trim().length === 0
                            }
                            className={
                              collaborationInvitePersonId ||
                              collaborationInviteEmail.trim().length > 0
                                ? `${mailboxPrimaryActionButtonClass} h-10 px-5 text-[0.72rem] tracking-[0.16em]`
                                : "inline-flex h-10 cursor-not-allowed items-center justify-center rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-5 text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-soft)] opacity-45 transition-[opacity] duration-150 focus-visible:outline-none"
                            }
                          >
                            Add participant
                          </button>
                        </div>
                        {getCollaborationParticipants(activeCollaborationMessage.collaboration)
                          .filter((participant) => participant.status === "invited")
                          .map((participant) => {
                            const copyKey = `${activeCollaborationMessage.id}:${participant.email.toLowerCase()}`;

                            return (
                              <div
                                key={`active-invite-link-${participant.id}`}
                                className="flex flex-wrap items-center justify-between gap-3 rounded-[16px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-3"
                              >
                                <div className="space-y-1">
                                  <div className="text-[0.68rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-text-faint)]">
                                    Dev only
                                  </div>
                                  <div className="text-[0.84rem] leading-6 text-[var(--workspace-text-soft)]">
                                    {participant.email}
                                  </div>
                                </div>
                                <button
                                  type="button"
                                  onClick={() => copyInviteLink(activeCollaborationMessage, participant.email)}
                                  className="inline-flex h-8 items-center justify-center rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-3 text-[0.68rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-text-soft)] transition-[background-color,border-color,color] duration-150 hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)] hover:text-[var(--workspace-text)] focus-visible:outline-none"
                                >
                                  {copiedInviteLinkKey === copyKey
                                    ? "Copied"
                                    : "Copy invite link"}
                                </button>
                              </div>
                            );
                          })}
                      </div>
                    </div>
                  ) : null}

                  <div className="mt-6 space-y-6">
                    <div className="space-y-4">
                      {visibleCollaborationMessages.map((entry) => (
                        <div
                          key={entry.id}
                          ref={(node) => {
                            collaborationMessageRefs.current[entry.id] = node;
                          }}
                          className={`space-y-1 rounded-[14px] px-2 py-1.5 transition-colors duration-200 ${
                            highlightedCollaborationMessageId === entry.id
                              ? "bg-[color:rgba(126,155,128,0.12)]"
                              : ""
                          }`}
                        >
                          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.82rem] leading-6 text-[var(--workspace-text)]">
                            <span>{entry.authorName}</span>
                            <span
                              className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[0.64rem] font-medium uppercase tracking-[0.14em] ${
                                getCollaborationMessageVisibility(entry) === "internal"
                                  ? "border-[color:rgba(115,132,118,0.24)] bg-[color:rgba(126,155,128,0.12)] text-[color:rgba(82,97,85,0.86)]"
                                  : "border-[color:rgba(123,116,106,0.18)] bg-[color:rgba(136,127,115,0.08)] text-[color:rgba(126,117,106,0.78)]"
                              }`}
                            >
                              {getCollaborationMessageVisibility(entry) === "internal"
                                ? "Internal"
                                : "Shared"}
                            </span>
                          </div>
                          <div className="text-[0.92rem] leading-7 text-[var(--workspace-text-soft)]">
                            {renderTextWithMentions(
                              entry.text,
                              new Map(
                                (entry.mentions ?? []).map((mention) => [
                                  mention.handle.toLowerCase(),
                                  mention,
                                ]),
                              ),
                              themeMode,
                            )}
                          </div>
                        </div>
                      ))}
                      {visibleCollaborationMessages.length === 0 ? (
                        <div className="text-[0.88rem] leading-7 text-[var(--workspace-text-faint)]">
                          {collaborationReplyVisibility === "internal"
                            ? "Start an internal discussion with your team"
                            : "Send a message to all participants"}
                        </div>
                      ) : null}
                      {activeCollaborationMessage.collaboration.resolvedAt &&
                      activeCollaborationMessage.collaboration.resolvedByUserName ? (
                        <div className="pt-1 text-[0.82rem] leading-6 text-[color:rgba(118,110,100,0.76)]">
                          {`✓ Marked as done by ${
                            activeCollaborationMessage.collaboration.resolvedByUserName
                          } · ${formatCollaborationStatusTimestamp(
                            activeCollaborationMessage.collaboration.resolvedAt,
                          )}`}
                        </div>
                      ) : null}
                    </div>

                    <label className="block space-y-2.5">
                      <span className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                        Reply
                      </span>
                      <div className="flex flex-wrap gap-2">
                        {([
                          { value: "internal", label: "Internal" },
                          { value: "shared", label: "Shared" },
                        ] as const).map((option) => (
                          <button
                            key={option.value}
                            type="button"
                            onClick={() => setCollaborationReplyVisibility(option.value)}
                            className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color] duration-150 focus-visible:outline-none ${
                              collaborationReplyVisibility === option.value
                                ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)]"
                                : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                            }`}
                          >
                            {option.label}
                          </button>
                        ))}
                      </div>
                      <div className="pt-0.5 text-[0.8rem] leading-6 text-[color:rgba(120,111,100,0.76)]">
                        {collaborationReplyVisibility === "internal"
                          ? "Only visible to your team"
                          : `Visible to: ${
                              activeCollaborationParticipants
                                .map((participant) => participant.name || participant.email)
                                .join(", ") || "all participants"
                            }`}
                      </div>
                      <textarea
                        ref={collaborationReplyInputRef}
                        value={collaborationReplyDraft}
                        onChange={(event) => {
                          setCollaborationReplyDraft(event.target.value);
                          syncCollaborationMentionState(event.target.value, event.target);
                        }}
                        onClick={(event) =>
                          syncCollaborationMentionState(
                            event.currentTarget.value,
                            event.currentTarget,
                          )
                        }
                        onKeyUp={(event) =>
                          syncCollaborationMentionState(
                            event.currentTarget.value,
                            event.currentTarget,
                          )
                        }
                        onKeyDown={(event) => {
                          if (visibleCollaborationMentionCandidates.length === 0) {
                            return;
                          }

                          if (event.key === "ArrowDown") {
                            event.preventDefault();
                            setCollaborationMentionIndex((current) =>
                              current >= visibleCollaborationMentionCandidates.length - 1
                                ? 0
                                : current + 1,
                            );
                            return;
                          }

                          if (event.key === "ArrowUp") {
                            event.preventDefault();
                            setCollaborationMentionIndex((current) =>
                              current <= 0
                                ? visibleCollaborationMentionCandidates.length - 1
                                : current - 1,
                            );
                            return;
                          }

                          if (event.key === "Enter" || event.key === "Tab") {
                            event.preventDefault();
                            applyCollaborationMention(
                              visibleCollaborationMentionCandidates[collaborationMentionIndex] ??
                                visibleCollaborationMentionCandidates[0],
                            );
                            return;
                          }

                          if (event.key === "Escape") {
                            setCollaborationMentionIndex(0);
                          }
                        }}
                        rows={4}
                        placeholder={
                          collaborationReplyVisibility === "internal"
                            ? "Add an internal note for your team"
                            : "Reply to everyone in this collaboration"
                        }
                        className="w-full resize-none rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3 text-[0.92rem] leading-7 text-[var(--workspace-text-soft)] outline-none placeholder:text-[var(--workspace-text-faint)]"
                      />
                      {visibleCollaborationMentionCandidates.length > 0 ? (
                        <div className="rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] p-2">
                          {visibleCollaborationMentionCandidates.map((candidate, index) => (
                            <button
                              key={`collaboration-mention-${candidate.id}`}
                              type="button"
                              onMouseDown={(event) => {
                                event.preventDefault();
                                applyCollaborationMention(candidate);
                              }}
                              className={`flex w-full items-center justify-between rounded-[12px] px-3 py-2 text-left text-[0.82rem] transition-colors duration-150 focus-visible:outline-none ${
                                index === collaborationMentionIndex
                                  ? "bg-[var(--workspace-hover-surface-strong)] text-[var(--workspace-text)]"
                                  : "text-[var(--workspace-text-soft)] hover:bg-[var(--workspace-hover-surface)]"
                              }`}
                            >
                              <span>{candidate.name}</span>
                              <span className="text-[0.76rem] text-[var(--workspace-text-faint)]">
                                @{candidate.handle}
                              </span>
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </label>
                  </div>

                  <div className="mt-6 flex items-center justify-end gap-3">
                    <button
                      type="button"
                      onClick={() => sendCollaborationReply(activeCollaborationMessage.id)}
                      disabled={!collaborationReplyDraft.trim()}
                      className={
                        collaborationReplyDraft.trim()
                          ? `${mailboxPrimaryActionButtonClass} h-10 px-5 text-[0.72rem] tracking-[0.16em]`
                          : "inline-flex h-10 cursor-not-allowed items-center justify-center rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-5 text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-soft)] opacity-45 transition-[opacity] duration-150 focus-visible:outline-none"
                      }
                    >
                      Send reply
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        markMessageCollaborationDone(activeCollaborationMessage.id);
                      }}
                      className={modalSecondaryActionButtonClass}
                    >
                      Mark as done
                    </button>
                  </div>
                </div>
              </WorkspaceModalLayer>,
              document.body,
            )
          : null}

        {isCloseModalOpen ? (
          <div className="absolute inset-0 flex items-center justify-center rounded-[30px] bg-[color:rgba(83,67,54,0.22)] p-6 backdrop-blur-[3px]">
            <div
              className="w-full max-w-[440px] rounded-[24px] border p-6 shadow-[0_24px_60px_rgba(43,31,22,0.18),0_8px_24px_rgba(43,31,22,0.12)]"
              style={
                themeMode === "dark"
                  ? {
                      background: "rgba(34, 30, 27, 0.96)",
                      borderColor: "rgba(99, 90, 80, 0.48)",
                    }
                  : {
                      background: "rgba(255, 252, 248, 0.98)",
                      borderColor: "rgba(199, 186, 170, 0.62)",
                    }
              }
            >
              <div className="space-y-2">
                <div className="text-[0.68rem] font-medium uppercase tracking-[0.18em] text-[var(--workspace-text-faint)]">
                  Draft
                </div>
                <h3 className="text-[1.18rem] font-medium tracking-tight text-[var(--workspace-text)]">
                  Save draft before closing?
                </h3>
                <p className="text-[0.88rem] leading-6 text-[var(--workspace-text-soft)]">
                  Keep this message in Drafts, discard it, or continue editing.
                </p>
              </div>
              <div className="mt-6 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={saveDraftAndClose}
                  className={mailboxPrimaryActionButtonClass}
                >
                  Save to Drafts
                </button>
                <button
                  type="button"
                  onClick={discardCompose}
                  className="inline-flex h-9 items-center justify-center rounded-full border border-[var(--workspace-border)] bg-[var(--workspace-card-subtle)] px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-soft)] transition-[background-color,border-color,color,transform] duration-150 hover:border-[var(--workspace-border-hover)] hover:bg-[var(--workspace-hover-surface)] active:scale-[0.99] focus-visible:outline-none"
                >
                  Discard
                </button>
                <button
                  type="button"
                  onClick={() => setIsCloseModalOpen(false)}
                  className="inline-flex h-9 items-center justify-center rounded-full border border-transparent bg-transparent px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)] transition-[color,transform] duration-150 hover:text-[var(--workspace-text-soft)] active:scale-[0.99] focus-visible:outline-none"
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        ) : null}

        {trashEmptiedToastMessage ? (
          <div className="pointer-events-none fixed bottom-6 right-6 z-[220]">
            <div className="rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3 text-[0.86rem] font-medium text-[var(--workspace-text)] shadow-panel">
              {trashEmptiedToastMessage}
            </div>
          </div>
        ) : null}
      </section>
    </div>
  );
}

function WorkbenchView({
  section,
  onOpenDemoInbox,
  onOpenLearningRequest,
  onOpenSenderContext,
  onOpenNotificationNavigation,
  aiSuggestionsEnabled,
  inboxChangesEnabled,
  teamActivityEnabled,
  modalHost,
  pendingTeamInvitation,
  memberOfEntries,
  onAddMemberOfEntry,
  onAcceptPendingTeamInvitation,
  onDeclinePendingTeamInvitation,
  showDemoContent,
}: {
  section: WorkbenchSection;
  onOpenDemoInbox: () => void;
  onOpenLearningRequest: (request: NonNullable<LearningLaunchRequest>) => void;
  onOpenSenderContext: () => void;
  onOpenNotificationNavigation: (
    request: Omit<NotificationNavigationRequest, "requestKey">,
  ) => void;
  aiSuggestionsEnabled: boolean;
  inboxChangesEnabled: boolean;
  teamActivityEnabled: boolean;
  modalHost: HTMLElement | null;
  pendingTeamInvitation: PendingTeamInvitation;
  memberOfEntries: TeamMembershipEntry[];
  onAddMemberOfEntry: (entry: TeamMembershipEntry) => void;
  onAcceptPendingTeamInvitation: () => void;
  onDeclinePendingTeamInvitation: () => void;
  showDemoContent: boolean;
}) {
  const content: Record<
    WorkbenchSection,
    {
      eyebrow: string;
      title: string;
      summary: string;
    }
  > = {
    Activity: {
      eyebrow: "Workspace activity",
      title: "Activity",
      summary:
        "A dedicated activity surface inside the existing shell for recent operational changes and workflow movement.",
    },
    Notifications: {
      eyebrow: "Workspace notifications",
      title: "Notifications",
      summary:
        "A calm notification surface that keeps alerts and updates inside the same premium workspace context.",
    },
    Team: {
      eyebrow: "Team workspace",
      title: "Team",
      summary:
        "Shared workspace access for inbox collaboration and coordination.",
    },
  };

  const view = content[section];
  const activityItems = showDemoContent ? [
    {
      eventType: "collaboration_created" as const,
      type: "COLLABORATION",
      category: "team-activity" as const,
      title: "Rutger started the collaboration",
      detail: "DSP delivery note for Friday release",
      time: "Now",
    },
    {
      eventType: "collaboration_invited_user" as const,
      type: "COLLABORATION",
      category: "team-activity" as const,
      title: "Emma invited David to the collaboration",
      detail: "DSP delivery note for Friday release",
      time: "Now",
    },
    {
      eventType: "collaboration_user_joined" as const,
      type: "COLLABORATION",
      category: "team-activity" as const,
      title: "David joined the collaboration",
      detail: "DSP delivery note for Friday release",
      time: "6 min ago",
    },
    {
      eventType: "collaboration_invite_accepted" as const,
      type: "COLLABORATION",
      category: "team-activity" as const,
      title: "David accepted the invitation",
      detail: "DSP delivery note for Friday release",
      time: "9 min ago",
    },
    {
      eventType: "collaboration_reply_added" as const,
      type: "COLLABORATION",
      category: "team-activity" as const,
      title: "Emma replied in the collaboration",
      detail: "DSP delivery note for Friday release",
      time: "14 min ago",
    },
    {
      eventType: "collaboration_internal_note_added" as const,
      type: "COLLABORATION",
      category: "team-activity" as const,
      title: "Rutger replied internally",
      detail: "DSP delivery note for Friday release",
      time: "22 min ago",
    },
    {
      eventType: "collaboration_shared_message_sent" as const,
      type: "COLLABORATION",
      category: "team-activity" as const,
      title: "Emma replied to all participants",
      detail: "DSP delivery note for Friday release",
      time: "31 min ago",
    },
    {
      eventType: "collaboration_mention_added" as const,
      type: "COLLABORATION",
      category: "team-activity" as const,
      title: "David mentioned Emma",
      detail: "Artwork confirmation for vinyl repress",
      time: "45 min ago",
    },
    {
      eventType: "collaboration_marked_done" as const,
      type: "COLLABORATION",
      category: "team-activity" as const,
      title: "Rutger marked this as done",
      detail: "Global tour routing sign-off",
      time: "2 hours ago",
    },
    {
      eventType: "collaboration_reopened" as const,
      type: "COLLABORATION",
      category: "team-activity" as const,
      title: "Emma reopened the collaboration",
      detail: "Global tour routing sign-off",
      time: "Yesterday",
    },
    {
      eventType: "collaboration_invite_declined" as const,
      type: "COLLABORATION",
      category: "team-activity" as const,
      title: "Alex declined the invitation",
      detail: "Artwork confirmation for vinyl repress",
      time: "Yesterday",
    },
  ] : [];
  const visibleActivityItems = activityItems.filter((item) => {
    if (item.category === "team-activity" && !teamActivityEnabled) {
      return false;
    }

    return true;
  });
  const visibleNotificationItems = showDemoContent
    ? buildVisibleNotificationItems({
        teamActivityEnabled,
        onOpenNotificationNavigation,
      })
    : [];
  const [teamMembers, setTeamMembers] = useState<TeamMemberEntry[]>(() => showDemoContent ? [
    {
      name: "Emma Stone",
      email: "emma@cuevion.com",
      accessLevel: "Admin" as const,
      selectedInboxes: ["Primary inbox", "Demo inbox", "Promo inbox"],
      status: "Active",
    },
    {
      name: "David Cole",
      email: "david@cuevion.com",
      accessLevel: "Review" as const,
      selectedInboxes: ["Demo inbox"],
      status: "Invited",
    },
    {
      name: "Mila Hart",
      email: "mila@cuevion.com",
      accessLevel: "Editor" as const,
      selectedInboxes: ["Promo inbox"],
      status: "Active",
    },
  ] : []);
  const [activeTeamMemberIndex, setActiveTeamMemberIndex] = useState<number | null>(null);
  const [isChangeAccessOpen, setIsChangeAccessOpen] = useState(false);
  const [isInviteMemberOpen, setIsInviteMemberOpen] = useState(false);
  const [activeTeamConfirmation, setActiveTeamConfirmation] = useState<
    "invite" | "revoke" | "cancel-invite" | "resend-invite" | "remove-member" | null
  >(null);
  const [teamFeedbackMessage, setTeamFeedbackMessage] = useState<string | null>(null);
  const activeTeamMember =
    activeTeamMemberIndex !== null ? teamMembers[activeTeamMemberIndex] : null;
  const [selectedTeamAccessLevel, setSelectedTeamAccessLevel] = useState<TeamAccessLevel>(
    "Admin",
  );
  const [selectedTeamInboxAccess, setSelectedTeamInboxAccess] = useState<string[]>(() =>
    showDemoContent ? ["Primary inbox", "Demo inbox", "Promo inbox"] : [],
  );
  const [inviteFullName, setInviteFullName] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteAccessLevel, setInviteAccessLevel] = useState<TeamAccessLevel>("Editor");
  const [inviteInboxAccess, setInviteInboxAccess] = useState<string[]>(() =>
    showDemoContent ? ["Primary inbox"] : [],
  );
  const getInitials = (name: string) =>
    name
      .split(" ")
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase() ?? "")
      .join("");
  const teamInboxOptions = ["Primary inbox", "Demo inbox", "Promo inbox"];
  const formatInboxSelectionLabel = (inboxes: string[]) => {
    if (inboxes.length === 0) {
      return "no inboxes";
    }

    if (inboxes.length === 1) {
      return inboxes[0];
    }

    if (inboxes.length === 2) {
      return `${inboxes[0]} and ${inboxes[1]}`;
    }

    return `${inboxes.slice(0, -1).join(", ")} and ${inboxes[inboxes.length - 1]}`;
  };
  const getTeamRoleDescription = (member: TeamMemberEntry) => {
    if (member.accessLevel === "Limited") {
      return "Limited access for shared collaborations only";
    }

    const formattedInboxes = formatInboxSelectionLabel(member.selectedInboxes);
    return `${getTeamAccessLevelLabel(member.accessLevel)} access for ${formattedInboxes}`;
  };
  const getTeamInboxAccessLabel = (member: TeamMemberEntry) =>
    member.selectedInboxes.length > 0 ? member.selectedInboxes.join(", ") : "No inbox access";
  const defaultTeamAccessState = activeTeamMember
    ? {
        level: activeTeamMember.accessLevel,
        inboxes: activeTeamMember.selectedInboxes,
      }
    : {
        level: "Admin" as TeamAccessLevel,
        inboxes: ["Primary inbox"],
      };
  const hasTeamAccessChanges = activeTeamMember
    ? selectedTeamAccessLevel !== defaultTeamAccessState.level ||
      [...(selectedTeamAccessLevel === "Limited" ? [] : selectedTeamInboxAccess)]
        .sort()
        .join("|") !==
        [...defaultTeamAccessState.inboxes].sort().join("|")
    : false;
  const canSubmitInvite =
    inviteFullName.trim().length > 0 &&
    inviteEmail.trim().length > 0 &&
    (inviteAccessLevel === "Limited" || inviteInboxAccess.length > 0);

  useEffect(() => {
    if (!teamFeedbackMessage) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      setTeamFeedbackMessage(null);
    }, 2600);

    return () => window.clearTimeout(timeoutId);
  }, [teamFeedbackMessage]);

  useEffect(() => {
    if (!activeTeamMember) {
      setIsChangeAccessOpen(false);
      return;
    }

    setSelectedTeamAccessLevel(activeTeamMember.accessLevel);
    setSelectedTeamInboxAccess(activeTeamMember.selectedInboxes);
  }, [activeTeamMemberIndex]);

  return (
    <>
    <div className="space-y-8">
      <header className="space-y-3">
        <div className="text-[0.72rem] font-medium uppercase tracking-[0.24em] text-[var(--workspace-text-faint)]">
          {view.eyebrow}
        </div>
        <h1 className="text-[1.85rem] font-medium tracking-tight text-[var(--workspace-text)] md:text-[2.25rem]">
          {view.title}
        </h1>
        <p className="max-w-3xl text-lg leading-8 text-[var(--workspace-text-muted)]">
          {view.summary}
        </p>
      </header>

      <section className="rounded-[30px] border border-[var(--workspace-border)] bg-[var(--workspace-card)] p-6 shadow-panel">
        {section === "Activity" ? (
          visibleActivityItems.length > 0 ? (
            <div className="divide-y divide-[var(--workspace-divider)]">
              {visibleActivityItems.map((item, index) => (
                <div
                  key={`${item.title}-${item.time}`}
                  className={`flex items-start justify-between gap-4 rounded-[18px] px-2 py-4 text-left first:pt-1 last:pb-1 ${
                    index === 0
                      ? "bg-[var(--workspace-surface-selected)]"
                      : "bg-transparent"
                  }`}
                >
                  <div className="min-w-0 space-y-1">
                    <div className="text-[0.62rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-accent-text)]">
                      {item.type}
                    </div>
                    <div className="text-[0.98rem] font-medium tracking-[-0.014em] text-[var(--workspace-text)]">
                      {item.title}
                    </div>
                    <div className="text-[0.84rem] leading-6 text-[var(--workspace-text-soft)]">
                      {item.detail}
                    </div>
                  </div>
                  <div className="flex-none pt-0.5 text-[0.68rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-text-faint)]">
                    {item.time}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-[0.92rem] leading-7 text-[var(--workspace-text-soft)]">
              No activity yet.
            </div>
          )
        ) : section === "Notifications" ? (
          visibleNotificationItems.length > 0 ? (
            <div className="divide-y divide-[var(--workspace-divider)]">
              {visibleNotificationItems.map((item) => (
                <button
                  key={`${item.title}-${item.time}`}
                  type="button"
                  onClick={item.action}
                  className="flex w-full items-start justify-between gap-4 rounded-[18px] px-2 py-3 text-left transition-colors duration-200 first:mt-[-0.25rem] first:pt-3 last:mb-[-0.25rem] hover:bg-[var(--workspace-surface-hover)] focus-visible:bg-[var(--workspace-surface-selected)] focus-visible:outline-none"
                >
                  <div className="min-w-0 space-y-0.5">
                    <div className="text-[0.92rem] font-medium tracking-[-0.014em] text-[var(--workspace-text)]">
                      {item.title}
                    </div>
                    <div className="text-[0.78rem] leading-6 text-[var(--workspace-text-soft)]">
                      {item.detail}
                    </div>
                  </div>
                  <div className="flex-none pt-0.5 text-[0.66rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-text-faint)]">
                    {item.time}
                  </div>
                </button>
              ))}
            </div>
          ) : (
            <div className="text-[0.92rem] leading-7 text-[var(--workspace-text-soft)]">
              No notifications yet.
            </div>
          )
        ) : section === "Team" ? (
          <div className="space-y-6">
            {pendingTeamInvitation ? (
              <div className="rounded-[22px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-5 py-5">
                <div className="space-y-1">
                  <div className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                    Pending invitation
                  </div>
                  <div className="text-[0.92rem] font-medium tracking-[-0.014em] text-[var(--workspace-text)]">
                    Shared by {pendingTeamInvitation.inviter}
                  </div>
                  <div className="text-[0.82rem] leading-6 text-[var(--workspace-text-soft)]">
                    {pendingTeamInvitation.accessLevel === "Limited"
                      ? "Limited access for shared collaborations only"
                      : `${getTeamAccessLevelLabel(pendingTeamInvitation.accessLevel)} access for ${formatInboxSelectionLabel(
                          pendingTeamInvitation.selectedInboxes,
                        )}`}
                  </div>
                </div>
                <div className="mt-4 flex flex-wrap items-center gap-3">
                  <button
                    type="button"
                    onClick={() => {
                      onAddMemberOfEntry({
                        name: pendingTeamInvitation.inviter,
                        email: "member@cuevion.com",
                        accessLevel: pendingTeamInvitation.accessLevel,
                        selectedInboxes: [...pendingTeamInvitation.selectedInboxes],
                        status: "Active",
                      });
                      onAcceptPendingTeamInvitation();
                      setTeamFeedbackMessage(
                        pendingTeamInvitation.accessLevel === "Limited"
                          ? "You now have collaboration-only access"
                          : `You now have access to ${formatInboxSelectionLabel(
                              pendingTeamInvitation.selectedInboxes,
                            )}`,
                      );
                    }}
                    className={teamInvitationPrimaryActionClass}
                  >
                    Accept
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      onDeclinePendingTeamInvitation();
                    }}
                    className={teamInvitationSecondaryActionClass}
                  >
                    Decline
                  </button>
                </div>
              </div>
            ) : null}

            <div className="space-y-4">
              <div className="flex items-center justify-between gap-4">
                <div className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                  Team members
                </div>
                <button
                  type="button"
                  onClick={() => setIsInviteMemberOpen(true)}
                  className={closeActionButtonClass}
                >
                  Invite member
                </button>
              </div>

              <div className="divide-y divide-[var(--workspace-divider)]">
              {teamMembers.map((member, index) => (
                <button
                  key={member.name}
                  type="button"
                  onClick={() => setActiveTeamMemberIndex(index)}
                  className={`flex w-full items-start justify-between gap-4 rounded-[18px] px-2 py-4 text-left transition-colors duration-200 first:mt-[-0.25rem] first:pt-[1.15rem] last:mb-[-0.25rem] last:pb-[0.4rem] ${
                    activeTeamMemberIndex === index
                      ? "bg-[var(--workspace-surface-selected)]"
                      : "hover:bg-[var(--workspace-surface-hover)]"
                  } focus-visible:bg-[var(--workspace-surface-selected)] focus-visible:outline-none`}
                >
                  <div className="min-w-0 flex items-start gap-3">
                    <div className="flex h-10 w-10 flex-none items-center justify-center rounded-full bg-[color:rgba(78,32,112,0.12)] text-[0.74rem] font-medium uppercase tracking-[0.08em] text-[#4E2070]">
                      {getInitials(member.name)}
                    </div>
                    <div className="min-w-0 space-y-0.5">
                      <div className="text-[0.94rem] font-medium tracking-[-0.014em] text-[var(--workspace-text)]">
                        {member.name}
                      </div>
                      <div className="text-[0.8rem] leading-6 text-[var(--workspace-text-soft)]">
                        {getTeamRoleDescription(member)}
                      </div>
                    </div>
                  </div>
                  <div className="flex-none pt-0.5 text-[0.68rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-text-faint)]">
                    {member.status}
                  </div>
                </button>
              ))}
              </div>
              {teamMembers.length === 0 ? (
                <div className="text-[0.92rem] leading-7 text-[var(--workspace-text-soft)]">
                  No team members yet.
                </div>
              ) : null}
            </div>

            {memberOfEntries.length > 0 ? (
              <div className="space-y-5 pt-1">
                <div className="h-px w-full bg-[var(--workspace-divider)]" />
                <div className="space-y-3">
                  <div className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                    Member of
                  </div>
                  <div className="divide-y divide-[var(--workspace-divider)]">
                    {memberOfEntries.map((member) => (
                      <div
                        key={`${member.name}-${member.accessLevel}-${member.selectedInboxes.join("|")}`}
                        className="flex items-start justify-between gap-4 py-4 first:pt-1 last:pb-1"
                      >
                        <div className="min-w-0 space-y-0.5">
                          <div className="text-[0.94rem] font-medium tracking-[-0.014em] text-[var(--workspace-text)]">
                            {member.name}
                          </div>
                          <div className="text-[0.8rem] leading-6 text-[var(--workspace-text-soft)]">
                            {getTeamRoleDescription(member)}
                          </div>
                        </div>
                        <div className="flex-none pt-0.5 text-[0.68rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-text-faint)]">
                          {member.status}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            ) : null}

            <div className="pt-1">
              {teamFeedbackMessage ? (
                <div className="mt-3 text-[0.72rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-text-faint)]">
                  {teamFeedbackMessage}
                </div>
              ) : null}
            </div>
          </div>
        ) : (
          <div className="text-[0.95rem] leading-7 text-[var(--workspace-text-soft)]">
            This section remains part of the current workspace shell and is positioned in the daily work area of the sidebar.
          </div>
        )}
      </section>
    </div>
    {isInviteMemberOpen && !activeTeamConfirmation && modalHost
      ? createPortal(
          <WorkspaceModalLayer>
            <div
              className="w-full max-w-[620px] overflow-hidden rounded-[28px] border border-[var(--workspace-border)] bg-[var(--workspace-modal-bg)] p-6 shadow-[0_28px_80px_rgba(61,44,32,0.18),0_10px_26px_rgba(61,44,32,0.1)]"
              onMouseDown={(event) => event.stopPropagation()}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="space-y-2">
                  <h2 className="text-[1.45rem] font-medium tracking-tight text-[var(--workspace-text)]">
                    Invite member
                  </h2>
                </div>
                <button
                  type="button"
                  onClick={() => setIsInviteMemberOpen(false)}
                  className={closeActionButtonClass}
                >
                  Close
                </button>
              </div>

              <div className="mt-6 rounded-[24px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-modal-subtle)] px-6 pb-6 pt-7 shadow-[inset_0_1px_0_rgba(255,255,255,0.12)]">
                <div className="space-y-4">
                  <label className="space-y-2">
                    <div className="text-[0.7rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                      Full name
                    </div>
                    <input
                      value={inviteFullName}
                      onChange={(event) => setInviteFullName(event.target.value)}
                      className="w-full rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3 text-[0.9rem] leading-6 text-[var(--workspace-text-soft)] outline-none"
                    />
                  </label>

                  <label className="space-y-2">
                    <div className="text-[0.7rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                      Email address
                    </div>
                    <input
                      value={inviteEmail}
                      onChange={(event) => setInviteEmail(event.target.value)}
                      className="w-full rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3 text-[0.9rem] leading-6 text-[var(--workspace-text-soft)] outline-none"
                    />
                  </label>
                </div>

                <div className="mt-5 space-y-3">
                  <div className="text-[0.7rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                    Access level
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {(["Admin", "Editor", "Review", "Limited"] as const).map((level) => (
                      <button
                        key={`invite-access-${level}`}
                        type="button"
                        onClick={() => setInviteAccessLevel(level)}
                        className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 focus-visible:outline-none ${
                          inviteAccessLevel === level
                            ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_24px_rgba(118,170,112,0.08)]"
                            : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                        }`}
                      >
                        {getTeamAccessLevelLabel(level)}
                      </button>
                    ))}
                  </div>
                </div>

                {inviteAccessLevel === "Limited" ? null : (
                  <div className="mt-5 space-y-3">
                    <div className="text-[0.7rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                      Inbox access
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {teamInboxOptions.map((inboxLabel) => {
                        const isSelected = inviteInboxAccess.includes(inboxLabel);

                        return (
                          <button
                            key={`invite-inbox-${inboxLabel}`}
                            type="button"
                            onClick={() => {
                              setInviteInboxAccess((current) => {
                                if (current.includes(inboxLabel)) {
                                  if (current.length === 1) {
                                    return current;
                                  }

                                  return current.filter((item) => item !== inboxLabel);
                                }

                                return [...current, inboxLabel];
                              });
                            }}
                            className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 focus-visible:outline-none ${
                              isSelected
                                ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_24px_rgba(118,170,112,0.08)]"
                                : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                            }`}
                          >
                            {inboxLabel}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}

                <div className="mt-6 flex items-center justify-end gap-3">
                  <button
                    type="button"
                    onClick={() => setIsInviteMemberOpen(false)}
                    className={subtleSecondaryActionButtonClass}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    disabled={!canSubmitInvite}
                    onClick={() => setActiveTeamConfirmation("invite")}
                    className={
                      canSubmitInvite
                        ? mailboxPrimaryActionButtonClass
                        : `${learningModalPrimaryActionButtonClass} cursor-default border-[color:rgba(66,99,69,0.3)] bg-[linear-gradient(180deg,rgba(122,150,122,0.82),rgba(88,116,90,0.82))] text-[color:rgba(251,248,242,0.92)] opacity-60 shadow-[inset_0_1px_0_rgba(255,255,255,0.08),0_10px_24px_rgba(66,99,69,0.1)]`
                    }
                  >
                    Invite
                  </button>
                </div>
              </div>
            </div>
          </WorkspaceModalLayer>,
          modalHost,
        )
      : null}
    {activeTeamMember &&
    !isInviteMemberOpen &&
    !isChangeAccessOpen &&
    !activeTeamConfirmation &&
    modalHost
      ? createPortal(
          <WorkspaceModalLayer>
            <div
              className="w-full max-w-[620px] overflow-hidden rounded-[28px] border border-[var(--workspace-border)] bg-[var(--workspace-modal-bg)] p-6 shadow-[0_28px_80px_rgba(61,44,32,0.18),0_10px_26px_rgba(61,44,32,0.1)]"
              onMouseDown={(event) => event.stopPropagation()}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="space-y-2">
                  <h2 className="text-[1.45rem] font-medium tracking-tight text-[var(--workspace-text)]">
                    Team member details
                  </h2>
                </div>
                <button
                  type="button"
                  onClick={() => setActiveTeamMemberIndex(null)}
                  className={learningModalPrimaryActionButtonClass}
                >
                  Close
                </button>
              </div>

              <div className="mt-6 rounded-[24px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-modal-subtle)] px-6 pb-6 pt-7 shadow-[inset_0_1px_0_rgba(255,255,255,0.12)]">
                <div className="space-y-5">
                  <div className="flex flex-col items-start gap-2">
                    <div className="flex h-14 w-14 items-center justify-center rounded-full bg-[color:rgba(78,32,112,0.12)] text-[0.92rem] font-medium uppercase tracking-[0.08em] text-[#4E2070]">
                      {getInitials(activeTeamMember.name)}
                    </div>
                    <button
                      type="button"
                      onClick={() => console.log(`upload_photo_${activeTeamMember.name}`)}
                      className="inline-flex items-center rounded-full border border-transparent bg-transparent px-0 py-0 text-[0.68rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-text-faint)] transition-colors duration-200 hover:text-[var(--workspace-text-soft)] focus-visible:outline-none"
                    >
                      Upload photo
                    </button>
                  </div>

                <div className="space-y-4">
                  <div className="space-y-1">
                    <div className="text-[0.7rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                      Member name
                    </div>
                    <div className="text-[0.98rem] font-medium tracking-[-0.014em] text-[var(--workspace-text)]">
                      {activeTeamMember.name}
                    </div>
                  </div>

                  <div className="space-y-1">
                    <div className="text-[0.7rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                      Access description
                    </div>
                    <div className="text-[0.88rem] leading-7 text-[var(--workspace-emphasis-text)]">
                      {getTeamRoleDescription(activeTeamMember)}
                    </div>
                  </div>

                  <div className="space-y-1">
                    <div className="text-[0.7rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                      Current inbox access
                    </div>
                    <div className="text-[0.88rem] leading-7 text-[var(--workspace-emphasis-text)]">
                      {getTeamInboxAccessLabel(activeTeamMember)}
                    </div>
                  </div>

                  <div className="space-y-1">
                    <div className="text-[0.7rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                      Status
                    </div>
                    <div className="text-[0.88rem] leading-7 text-[var(--workspace-emphasis-text)]">
                      {activeTeamMember.status}
                    </div>
                  </div>
                </div>
                </div>

                <div className="mt-6 flex flex-wrap items-center gap-3">
                  {activeTeamMember.status === "Active" ? (
                    <>
                      <button
                        type="button"
                        onClick={() => setIsChangeAccessOpen(true)}
                        className={learningModalPrimaryActionButtonClass}
                      >
                        Change access
                      </button>
                      <button
                        type="button"
                        onClick={() => setActiveTeamConfirmation("revoke")}
                        className={learningModalPrimaryActionButtonClass}
                      >
                        Revoke access
                      </button>
                    </>
                  ) : activeTeamMember.status === "Access removed" ? (
                    <>
                      <button
                        type="button"
                        onClick={() => {
                          setTeamMembers((current) =>
                            current.map((member, index) =>
                              index === activeTeamMemberIndex
                                ? {
                                    ...member,
                                    status: "Active",
                                  }
                                : member,
                            ),
                          );
                          setTeamFeedbackMessage("Access restored");
                        }}
                        className={learningModalPrimaryActionButtonClass}
                      >
                        Restore access
                      </button>
                      <button
                        type="button"
                        onClick={() => setActiveTeamConfirmation("remove-member")}
                        className={learningModalPrimaryActionButtonClass}
                      >
                        Remove member
                      </button>
                    </>
                  ) : activeTeamMember.status === "Invite cancelled" ? (
                    <>
                      <button
                        type="button"
                        onClick={() => setActiveTeamConfirmation("resend-invite")}
                        className={learningModalPrimaryActionButtonClass}
                      >
                        Re-send invite
                      </button>
                      <button
                        type="button"
                        onClick={() => setActiveTeamConfirmation("remove-member")}
                        className={learningModalPrimaryActionButtonClass}
                      >
                        Remove member
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        type="button"
                        onClick={() => setActiveTeamConfirmation("resend-invite")}
                        className={learningModalPrimaryActionButtonClass}
                      >
                        Resend invite
                      </button>
                      <button
                        type="button"
                        onClick={() => setActiveTeamConfirmation("cancel-invite")}
                        className={learningModalPrimaryActionButtonClass}
                      >
                        Cancel invite
                      </button>
                    </>
                  )}
                </div>
                {teamFeedbackMessage ? (
                  <div className="mt-3 text-[0.72rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-text-faint)]">
                    {teamFeedbackMessage}
                  </div>
                ) : null}
              </div>
            </div>
          </WorkspaceModalLayer>,
          modalHost,
        )
      : null}
    {activeTeamConfirmation && modalHost
      ? createPortal(
          <WorkspaceModalLayer>
            <div
              className="w-full max-w-[460px] overflow-hidden rounded-[26px] border border-[var(--workspace-border)] bg-[var(--workspace-modal-bg)] p-6 shadow-[0_24px_70px_rgba(61,44,32,0.16),0_8px_20px_rgba(61,44,32,0.08)]"
              onMouseDown={(event) => event.stopPropagation()}
            >
              <div className="space-y-2">
                <h2 className="text-[1.25rem] font-medium tracking-tight text-[var(--workspace-text)]">
                  {activeTeamConfirmation === "revoke"
                    ? "Revoke access"
                    : activeTeamConfirmation === "remove-member"
                      ? "Remove member"
                    : activeTeamConfirmation === "cancel-invite"
                      ? "Cancel invite"
                      : activeTeamConfirmation === "resend-invite"
                        ? "Resend invite"
                        : "Confirm invite"}
                </h2>
                <p className="text-[0.9rem] leading-7 text-[var(--workspace-text-soft)]">
                  {activeTeamConfirmation === "revoke"
                    ? "Are you sure you want to remove this member’s workspace access?"
                    : activeTeamConfirmation === "remove-member"
                      ? "Are you sure you want to remove this member from the workspace?"
                    : activeTeamConfirmation === "cancel-invite"
                      ? "Are you sure you want to cancel this invitation?"
                      : activeTeamConfirmation === "resend-invite"
                        ? "Send this workspace invitation again?"
                        : "Send workspace invitation to this member?"}
                </p>
              </div>

              <div className="mt-6 flex items-center justify-end gap-3">
                <button
                  type="button"
                  onClick={() => setActiveTeamConfirmation(null)}
                  className="inline-flex h-10 items-center justify-center rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-5 text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-soft)] transition-[background-color,border-color,color,transform] duration-150 hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)] active:scale-[0.99] focus-visible:outline-none"
                >
                  No
                </button>
                <button
                  type="button"
                  onClick={() => {
                    if (activeTeamConfirmation === "revoke" && activeTeamMemberIndex !== null) {
                      setTeamMembers((current) =>
                        current.map((member, index) =>
                          index === activeTeamMemberIndex
                            ? {
                                ...member,
                                status: "Access removed",
                              }
                            : member,
                        ),
                      );
                    }

                    if (
                      activeTeamConfirmation === "remove-member" &&
                      activeTeamMemberIndex !== null
                    ) {
                      setTeamMembers((current) =>
                        current.filter((_, index) => index !== activeTeamMemberIndex),
                      );
                      setActiveTeamMemberIndex(null);
                      setTeamFeedbackMessage("Member removed");
                    }

                    if (activeTeamConfirmation === "invite") {
                      setTeamMembers((current) => [
                        ...current,
                        {
                          name: inviteFullName.trim(),
                          email: inviteEmail.trim(),
                          accessLevel: inviteAccessLevel,
                          selectedInboxes:
                            inviteAccessLevel === "Limited" ? [] : [...inviteInboxAccess],
                          status: "Invited",
                        },
                      ]);
                      setInviteFullName("");
                      setInviteEmail("");
                      setInviteAccessLevel("Editor");
                      setInviteInboxAccess(["Primary inbox"]);
                      setIsInviteMemberOpen(false);
                      setTeamFeedbackMessage("Invitation sent");
                      console.log("confirm_invite_team_member");
                    } else if (
                      activeTeamConfirmation === "cancel-invite" &&
                      activeTeamMemberIndex !== null
                    ) {
                      setTeamMembers((current) =>
                        current.map((member, index) =>
                          index === activeTeamMemberIndex
                            ? {
                                ...member,
                                status: "Invite cancelled",
                              }
                            : member,
                        ),
                      );
                      console.log(`confirm_cancel_invite_${activeTeamMember?.name ?? "member"}`);
                    } else if (
                      activeTeamConfirmation === "resend-invite" &&
                      activeTeamMember
                    ) {
                      setTeamFeedbackMessage("Invitation resent");
                      console.log(`confirm_resend_invite_${activeTeamMember.name}`);
                    } else if (
                      activeTeamConfirmation === "remove-member" &&
                      activeTeamMember
                    ) {
                      console.log(`confirm_remove_member_${activeTeamMember.name}`);
                    } else if (activeTeamMember) {
                      console.log(`confirm_revoke_access_${activeTeamMember.name}`);
                    }

                    setActiveTeamConfirmation(null);
                  }}
                  className={mailboxPrimaryActionButtonClass}
                >
                  Confirm
                </button>
              </div>
            </div>
          </WorkspaceModalLayer>,
          modalHost,
        )
      : null}
    {activeTeamMember && isChangeAccessOpen && !activeTeamConfirmation && modalHost
      ? createPortal(
          <WorkspaceModalLayer>
            <div
              className="w-full max-w-[620px] overflow-hidden rounded-[28px] border border-[var(--workspace-border)] bg-[var(--workspace-modal-bg)] p-6 shadow-[0_28px_80px_rgba(61,44,32,0.18),0_10px_26px_rgba(61,44,32,0.1)]"
              onMouseDown={(event) => event.stopPropagation()}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="space-y-2">
                  <h2 className="text-[1.45rem] font-medium tracking-tight text-[var(--workspace-text)]">
                    Change access
                  </h2>
                  <p className="max-w-[30rem] text-[0.9rem] leading-7 text-[var(--workspace-text-soft)]">
                    Adjust workspace access and inbox visibility.
                  </p>
                </div>
              </div>

              <div className="mt-6 rounded-[24px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-modal-subtle)] px-6 pb-6 pt-7 shadow-[inset_0_1px_0_rgba(255,255,255,0.12)]">
                <div className="space-y-3">
                  <div className="text-[0.7rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                    Access level
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {(["Admin", "Editor", "Review", "Limited"] as const).map((level) => (
                      <button
                        key={`team-access-level-${level}`}
                        type="button"
                        onClick={() => setSelectedTeamAccessLevel(level)}
                        className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 focus-visible:outline-none ${
                          selectedTeamAccessLevel === level
                            ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_24px_rgba(118,170,112,0.08)]"
                            : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                        }`}
                      >
                        {getTeamAccessLevelLabel(level)}
                      </button>
                    ))}
                  </div>
                </div>

                {selectedTeamAccessLevel === "Limited" ? null : (
                  <div className="mt-5 space-y-3">
                    <div className="text-[0.7rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                      Inbox access
                    </div>
                    <div className="flex flex-wrap gap-2 transition-opacity duration-200">
                      {teamInboxOptions.map((inboxLabel) => {
                        const isSelected = selectedTeamInboxAccess.includes(inboxLabel);

                        return (
                          <button
                            key={`team-inbox-access-${inboxLabel}`}
                            type="button"
                            onClick={() => {
                              setSelectedTeamInboxAccess((current) => {
                                if (current.includes(inboxLabel)) {
                                  if (current.length === 1) {
                                    return current;
                                  }

                                  return current.filter((item) => item !== inboxLabel);
                                }

                                return [...current, inboxLabel];
                              });
                            }}
                            className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 focus-visible:outline-none ${
                              isSelected
                                ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_24px_rgba(118,170,112,0.08)]"
                                : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                            }`}
                          >
                            {inboxLabel}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}

                {hasTeamAccessChanges ? (
                  <div className="mt-3 text-[0.72rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-text-faint)]">
                    Unsaved changes
                  </div>
                ) : null}

                <div className="mt-6 flex items-center justify-end gap-3">
                  <button
                    type="button"
                    onClick={() => setIsChangeAccessOpen(false)}
                    className={learningModalPrimaryActionButtonClass}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setTeamMembers((current) =>
                        current.map((member, index) =>
                          index === activeTeamMemberIndex
                            ? {
                                ...member,
                                accessLevel: selectedTeamAccessLevel,
                                selectedInboxes:
                                  selectedTeamAccessLevel === "Limited"
                                    ? []
                                    : [...selectedTeamInboxAccess],
                              }
                            : member,
                        ),
                      );
                      console.log(
                        `save_team_access_${activeTeamMember.name}_${selectedTeamAccessLevel.toLowerCase()}_${selectedTeamInboxAccess.join("_").replace(/\s+/g, "-").toLowerCase()}`,
                      );
                      setIsChangeAccessOpen(false);
                    }}
                    className={`${learningModalPrimaryActionButtonClass} ${
                      hasTeamAccessChanges
                        ? ""
                        : "border-[color:rgba(66,99,69,0.3)] bg-[linear-gradient(180deg,rgba(122,150,122,0.82),rgba(88,116,90,0.82))] text-[color:rgba(251,248,242,0.92)] shadow-[inset_0_1px_0_rgba(255,255,255,0.08),0_10px_24px_rgba(66,99,69,0.1)]"
                    }`}
                  >
                    Save changes
                  </button>
                </div>
              </div>
            </div>
          </WorkspaceModalLayer>,
          modalHost,
        )
      : null}
    </>
  );
}

const settingsSectionLabelClass =
  "text-[0.68rem] font-medium uppercase tracking-[0.22em] text-[var(--workspace-text-faint)]";
const settingsInfoRowClass =
  "flex items-center justify-between gap-4 rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-2.5";
const settingsCardSectionClass =
  "rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-3";
const settingsSubtleActionClass =
  "inline-flex h-9 items-center justify-center rounded-full border border-[var(--workspace-border)] bg-[var(--workspace-card)] px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-soft)] shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] transition-[background-color,border-color,color,transform,box-shadow] duration-150 hover:border-[color:rgba(120,104,89,0.22)] hover:bg-[color:rgba(245,238,229,0.86)] hover:text-[var(--workspace-text)] hover:shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_18px_rgba(120,104,89,0.08)] active:scale-[0.99] focus-visible:outline-none dark:hover:border-[var(--workspace-border-hover)] dark:hover:bg-[var(--workspace-hover-surface-strong)] dark:hover:text-[var(--workspace-text)] dark:hover:shadow-[inset_0_1px_0_rgba(255,255,255,0.06),0_8px_18px_rgba(0,0,0,0.16)]";
const settingsPairedSecondaryActionClass =
  "inline-flex h-10 w-[7.5rem] items-center justify-center rounded-full border border-[var(--workspace-border)] bg-[var(--workspace-card)] px-5 text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-soft)] shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] transition-[background-color,border-color,color,transform,box-shadow] duration-150 hover:border-[color:rgba(120,104,89,0.22)] hover:bg-[color:rgba(245,238,229,0.86)] hover:text-[var(--workspace-text)] hover:shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_18px_rgba(120,104,89,0.08)] active:scale-[0.99] focus-visible:outline-none dark:hover:border-[var(--workspace-border-hover)] dark:hover:bg-[var(--workspace-hover-surface-strong)] dark:hover:text-[var(--workspace-text)] dark:hover:shadow-[inset_0_1px_0_rgba(255,255,255,0.06),0_8px_18px_rgba(0,0,0,0.16)]";
const settingsAccentSecondaryActionClass =
  "inline-flex h-9 items-center justify-center rounded-full border border-[color:rgba(103,141,103,0.34)] bg-[linear-gradient(180deg,rgba(223,235,219,0.78),rgba(240,245,237,0.92))] px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[color:rgba(74,108,75,0.96)] transition-[background-color,border-color,color,transform,box-shadow] duration-150 hover:border-[color:rgba(93,130,95,0.48)] hover:bg-[linear-gradient(180deg,rgba(211,229,206,0.92),rgba(233,241,229,0.98))] hover:shadow-[0_6px_14px_rgba(118,170,112,0.08)] active:scale-[0.99] focus-visible:outline-none";
const settingsGhostActionClass =
  "inline-flex h-8 items-center justify-center rounded-full border border-[var(--workspace-border)] bg-[var(--workspace-card)] px-3 text-[0.65rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-text-soft)] shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] transition-[background-color,border-color,color,transform,box-shadow] duration-150 hover:border-[color:rgba(120,104,89,0.22)] hover:bg-[color:rgba(245,238,229,0.9)] hover:text-[var(--workspace-text)] hover:shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_6px_14px_rgba(120,104,89,0.08)] active:scale-[0.99] focus-visible:outline-none dark:hover:border-[var(--workspace-border-hover)] dark:hover:bg-[var(--workspace-hover-surface-strong)] dark:hover:text-[var(--workspace-text)] dark:hover:shadow-[inset_0_1px_0_rgba(255,255,255,0.06),0_6px_14px_rgba(0,0,0,0.14)]";
const settingsSecondaryGhostActionClass =
  "inline-flex h-8 items-center justify-center rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-3 text-[0.65rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-text-soft)] shadow-[inset_0_1px_0_rgba(255,255,255,0.03)] transition-[background-color,border-color,color,transform,box-shadow] duration-150 hover:border-[color:rgba(120,104,89,0.2)] hover:bg-[color:rgba(241,233,223,0.82)] hover:text-[var(--workspace-text)] hover:shadow-[inset_0_1px_0_rgba(255,255,255,0.12),0_6px_14px_rgba(120,104,89,0.07)] active:scale-[0.99] focus-visible:outline-none dark:hover:border-[var(--workspace-border)] dark:hover:bg-[var(--workspace-hover-surface)] dark:hover:text-[var(--workspace-text)] dark:hover:shadow-[inset_0_1px_0_rgba(255,255,255,0.05),0_6px_14px_rgba(0,0,0,0.14)]";
const settingsPrimaryActionClass =
  `inline-flex h-10 items-center justify-center rounded-full px-5 text-[0.72rem] font-medium uppercase tracking-[0.16em] ${primaryActionSurfaceClass}`;
const settingsDangerActionClass =
  "inline-flex h-10 items-center justify-center rounded-full border border-[color:rgba(146,82,73,0.34)] bg-[linear-gradient(180deg,rgba(170,103,93,0.96),rgba(138,76,67,0.98))] px-5 text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[color:rgba(255,248,244,0.98)] shadow-[inset_0_1px_0_rgba(255,255,255,0.08),0_8px_18px_rgba(123,70,61,0.14)] transition-[background-color,border-color,color,transform,box-shadow] duration-150 hover:border-[color:rgba(132,72,64,0.42)] hover:bg-[linear-gradient(180deg,rgba(156,91,82,0.98),rgba(126,67,60,0.98))] active:scale-[0.99] focus-visible:outline-none";
const teamInvitationPrimaryActionClass =
  settingsPrimaryActionClass;
const teamInvitationSecondaryActionClass =
  "inline-flex h-10 items-center justify-center rounded-full border border-[var(--workspace-border)] bg-[var(--workspace-card-subtle)] px-5 text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-soft)] transition-[background-color,border-color,color,transform] duration-150 hover:border-[var(--workspace-border-hover)] hover:bg-[var(--workspace-hover-surface)] active:scale-[0.99] focus-visible:outline-none";
function settingsCardClass(themeMode: "light" | "dark") {
  return `flex h-full flex-col rounded-[28px] border px-4 py-4 sm:px-5 ${
    themeMode === "dark"
      ? "border-[color:rgba(255,255,255,0.06)] bg-[linear-gradient(180deg,rgba(43,40,36,0.98),rgba(36,33,30,0.98))] shadow-[0_14px_30px_rgba(0,0,0,0.22),inset_0_1px_0_rgba(255,255,255,0.04)]"
      : "border-[var(--workspace-border)] bg-[color:rgba(255,252,247,0.98)] shadow-[0_6px_18px_rgba(61,44,32,0.04),inset_0_1px_0_rgba(255,255,255,0.55)]"
  }`;
}

function settingsPageSurfaceClass(themeMode: "light" | "dark") {
  return `space-y-6 rounded-[30px] px-3 py-3 md:px-4 md:py-4 ${
    themeMode === "dark"
      ? "bg-[radial-gradient(circle_at_top_left,rgba(255,255,255,0.03),transparent_34%),linear-gradient(180deg,rgba(26,24,22,0.92),rgba(18,17,15,0.96))] shadow-[inset_0_1px_0_rgba(255,255,255,0.03)]"
      : "bg-[linear-gradient(180deg,rgba(250,247,242,0.72),rgba(247,243,237,0.56))] shadow-[inset_0_1px_0_rgba(255,255,255,0.2)]"
  }`;
}

function settingsPillButtonClass(selected: boolean) {
  return `inline-flex h-8 items-center justify-center rounded-full border px-3.5 text-[0.65rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 active:scale-[0.99] focus-visible:outline-none ${
    selected
      ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_4px_12px_rgba(118,170,112,0.06)]"
      : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
  }`;
}

function settingsToggleButtonClass(enabled: boolean) {
  return `relative inline-flex h-7 w-[3rem] items-center rounded-full border transition-[background-color,border-color,box-shadow,transform] duration-150 active:scale-[0.98] focus-visible:outline-none ${
    enabled
      ? "justify-end border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-toggle-on-start),var(--workspace-toggle-on-end))] px-[0.2rem] shadow-[inset_0_1px_0_rgba(255,255,255,0.12),0_4px_10px_rgba(66,99,69,0.1)]"
      : "justify-start border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-[0.2rem]"
  }`;
}

const SettingsInfoRow = memo(function SettingsInfoRow({
  label,
  value,
  onClick,
  actionLabel,
  onActionClick,
}: {
  label: string;
  value: string;
  onClick?: () => void;
  actionLabel?: string;
  onActionClick?: () => void;
}) {
  if (onActionClick) {
    return (
      <div className={settingsInfoRowClass}>
        <div className="text-[0.86rem] text-[var(--workspace-text-soft)]">{label}</div>
        <div className="flex items-center gap-3 text-right">
          <div className="text-[0.86rem] font-medium text-[var(--workspace-text)]">
            {value}
          </div>
          <button
            type="button"
            onClick={onActionClick}
            className="text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)] transition-colors duration-150 hover:text-[var(--workspace-text)] focus-visible:outline-none"
          >
            {actionLabel ?? "Manage"}
          </button>
        </div>
      </div>
    );
  }

  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        className={`${settingsInfoRowClass} w-full text-left transition-[background-color,border-color,transform,box-shadow] duration-150 hover:border-[color:rgba(120,104,89,0.18)] hover:bg-[color:rgba(245,238,229,0.76)] hover:shadow-[inset_0_1px_0_rgba(255,255,255,0.12),0_8px_18px_rgba(120,104,89,0.06)] active:scale-[0.995] dark:hover:border-[var(--workspace-border)] dark:hover:bg-[var(--workspace-hover-surface)]`}
      >
        <div className="text-[0.86rem] text-[var(--workspace-text-soft)]">{label}</div>
        <div className="flex items-center gap-3 text-right">
          <div className="text-[0.86rem] font-medium text-[var(--workspace-text)]">
            {value}
          </div>
          {actionLabel ? (
            <div className="text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
              {actionLabel}
            </div>
          ) : null}
        </div>
      </button>
    );
  }

  return (
    <div className={settingsInfoRowClass}>
      <div className="text-[0.86rem] text-[var(--workspace-text-soft)]">{label}</div>
      <div className="text-right text-[0.86rem] font-medium text-[var(--workspace-text)]">
        {value}
      </div>
    </div>
  );
});

const SettingsToggleRow = memo(function SettingsToggleRow({
  label,
  enabled,
  onToggle,
}: {
  label: string;
  enabled: boolean;
  onToggle: () => void;
}) {
  return (
    <div className={settingsInfoRowClass}>
      <div className="text-[0.86rem] text-[var(--workspace-text)]">{label}</div>
      <button
        type="button"
        role="switch"
        aria-checked={enabled}
        aria-label={label}
        onClick={onToggle}
        className={settingsToggleButtonClass(enabled)}
      >
        <span
          className={`h-[1.15rem] w-[1.15rem] rounded-full bg-[rgba(255,252,247,0.98)] shadow-[0_3px_8px_rgba(31,42,36,0.12)] transition-[transform,box-shadow,background-color] duration-150 ${
            enabled ? "ring-1 ring-white/40" : ""
          }`}
        />
      </button>
    </div>
  );
});

type SettingsMode = "Light" | "Dark" | "System";
type ManagedWorkspaceInbox = {
  id: string;
  title: string;
  email: string;
  provider: ProviderId | null;
  connected: boolean;
  customImap: CustomImapSettings;
};

function createManagedCustomImapSettings(): CustomImapSettings {
  return {
    host: "",
    port: "",
    ssl: true,
    username: "",
    password: "",
  };
}

function normalizeStoredWorkspaceThemeMode(value: unknown): SettingsMode | null {
  return value === "Light" || value === "Dark" || value === "System" ? value : null;
}

function cloneManagedWorkspaceInbox(mailbox: ManagedWorkspaceInbox): ManagedWorkspaceInbox {
  return {
    ...mailbox,
    customImap: {
      ...mailbox.customImap,
    },
  };
}

function buildManagedWorkspaceInboxes(
  onboardingState: OnboardingState,
): ManagedWorkspaceInbox[] {
  return getOrderedMailboxes(onboardingState).map((mailbox) =>
    toManagedWorkspaceInbox(mailbox, onboardingState),
  );
}

function isManagedInboxReady(mailbox: ManagedWorkspaceInbox) {
  if (!mailbox.provider || !mailbox.email.trim()) {
    return false;
  }

  if (!isImapCredentialsProvider(mailbox.provider)) {
    return true;
  }

  const { host, port, username, password } = mailbox.customImap;
  return Boolean(host.trim() && port.trim() && username.trim() && password.trim());
}

function toManagedWorkspaceInbox(
  mailbox: OrderedMailbox,
  onboardingState: OnboardingState,
): ManagedWorkspaceInbox {
  const connection = onboardingState.inboxConnections[mailbox.id];

  return {
    id: mailbox.id,
    title: mailbox.title,
    email: connection.email.trim() || mailbox.email,
    provider: connection.provider,
    connected: connection.connected,
    customImap: {
      ...createManagedCustomImapSettings(),
      ...connection.customImap,
    },
  };
}

function toOrderedMailboxFromManagedInbox(
  mailbox: ManagedWorkspaceInbox,
): OrderedMailbox {
  const fallbackInfo = isPresetInboxId(mailbox.id as InboxId)
    ? inboxDisplayConfig[mailbox.id as PresetInboxId]
    : {
        title: mailbox.title.trim() || "Custom Inbox",
        fallbackEmail: buildCustomInboxFallbackEmail(
          mailbox.title.trim() || mailbox.email.trim() || "custom",
        ),
        detail: "Connected custom inbox",
        state: "CONNECTED",
      };
  const normalizedEmail = (
    mailbox.email.trim() || fallbackInfo.fallbackEmail
  ).toLowerCase();

  return {
    id: mailbox.id as InboxId,
    title: formatMailboxIdentityTitle(
      mailbox.id as InboxId,
      normalizedEmail,
      mailbox.title.trim() || fallbackInfo.title,
    ),
    email: normalizedEmail,
    detail: fallbackInfo.detail,
    state: fallbackInfo.state,
  };
}
const WorkspaceSettingsCard = memo(function WorkspaceSettingsCard({
  savedWorkspaceName,
  managedInboxCount,
  themeMode,
  appliedMode,
  aiSuggestionsEnabled,
  onToggleAiSuggestions,
  onChangeMode,
  onSaveWorkspaceName,
  onManageInboxes,
}: {
  savedWorkspaceName: string;
  managedInboxCount: number;
  themeMode: "light" | "dark";
  appliedMode: SettingsMode;
  aiSuggestionsEnabled: boolean;
  onToggleAiSuggestions: () => void;
  onChangeMode: (mode: SettingsMode) => void;
  onSaveWorkspaceName: (name: string) => void;
  onManageInboxes: () => void;
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [savedAutomationLevel, setSavedAutomationLevel] = useState<
    "Conservative" | "Balanced" | "Proactive"
  >("Balanced");
  const [draftAutomationLevel, setDraftAutomationLevel] = useState(savedAutomationLevel);
  const hasUnsavedAutomationLevelChanges =
    draftAutomationLevel !== savedAutomationLevel;

  const handleCloseWorkspaceSettings = () => {
    setIsEditing(false);
  };

  const handleOpenWorkspaceSettings = () => {
    setIsEditing(true);
  };

  return (
    <section className="flex h-full flex-col space-y-2.5">
      <div className={settingsSectionLabelClass}>Workspace</div>
      <div className={settingsCardClass(themeMode)}>
        <div className="mb-3 flex items-center justify-between gap-4">
          <h2 className="text-[1.1rem] font-medium tracking-tight text-[var(--workspace-text)]">
            Workspace
          </h2>
          <div className="ml-auto flex flex-none items-center gap-2.5 self-start">
            {isEditing ? (
              <CloseActionButton onClick={handleCloseWorkspaceSettings} />
            ) : (
              <button
                type="button"
                onClick={handleOpenWorkspaceSettings}
                className={settingsPrimaryActionClass}
              >
                Manage
              </button>
            )}
          </div>
        </div>
        {isEditing ? (
          <div className="space-y-3.5">
            <div className={settingsCardSectionClass}>
              <label className="mb-2 block text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                Workspace name
              </label>
              <input
                type="text"
                value={savedWorkspaceName}
                onChange={(event) => onSaveWorkspaceName(event.target.value)}
                className="w-full rounded-[16px] border border-[var(--workspace-border)] bg-[var(--workspace-input-bg)] px-4 py-3 text-[0.94rem] text-[var(--workspace-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.08)] outline-none transition-[background-color,border-color,color] duration-150 placeholder:text-[var(--workspace-text-faint)] focus:border-[color:rgba(103,141,103,0.5)] focus:bg-[var(--workspace-input-focus-bg)]"
              />
            </div>

            <div className={settingsCardSectionClass}>
              <div className="flex items-center justify-between gap-4">
                <div>
                  <div className="text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                    Connected inboxes
                  </div>
                  <div className="mt-1 text-[0.86rem] text-[var(--workspace-text-muted)]">
                    Keep connected inboxes aligned with this workspace.
                  </div>
                </div>
                <button
                  type="button"
                  onClick={onManageInboxes}
                  className={settingsSubtleActionClass}
                >
                  Manage inboxes
                </button>
              </div>
            </div>

            <div className={settingsCardSectionClass}>
              <div className="mb-2 text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                Theme
              </div>
              <div className="flex flex-wrap gap-2">
                {(["Light", "Dark", "System"] as const).map((option) => (
                  <button
                    key={option}
                    type="button"
                    onClick={() => onChangeMode(option)}
                    className={settingsPillButtonClass(appliedMode === option)}
                  >
                    {option}
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            <SettingsInfoRow label="Workspace name" value={savedWorkspaceName} />
            <SettingsInfoRow
              label="Connected inboxes"
              value={`${managedInboxCount} inboxes`}
            />
            <SettingsInfoRow label="Theme" value={appliedMode} />
          </div>
        )}

        <div className="mt-3 space-y-3.5">
          <div className={settingsCardSectionClass}>
            <div className="mb-3 text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
              AI Behavior
            </div>
            <div className="space-y-3">
              <SettingsToggleRow
                label="AI Suggestions"
                enabled={aiSuggestionsEnabled}
                onToggle={onToggleAiSuggestions}
              />
              <div className="rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3">
                <div className="mb-2 text-[0.86rem] text-[var(--workspace-text)]">
                  Automation level
                </div>
                <div className="flex flex-wrap gap-2">
                  {(["Conservative", "Balanced", "Proactive"] as const).map((option) => (
                    <button
                      key={option}
                      type="button"
                      onClick={() => setDraftAutomationLevel(option)}
                      className={settingsPillButtonClass(draftAutomationLevel === option)}
                    >
                      {option}
                    </button>
                  ))}
                </div>
              </div>

              {hasUnsavedAutomationLevelChanges ? (
                <div className="flex justify-end gap-3 pt-1">
                  <button
                    type="button"
                    onClick={() => setDraftAutomationLevel(savedAutomationLevel)}
                    className={settingsPairedSecondaryActionClass}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={() => setSavedAutomationLevel(draftAutomationLevel)}
                    className={`${settingsPrimaryActionClass} w-[7.5rem]`}
                  >
                    Apply
                  </button>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
});

const inputFieldClass =
  "w-full rounded-[16px] border border-[var(--workspace-border)] bg-[var(--workspace-input-bg)] px-4 py-3 text-[0.94rem] text-[var(--workspace-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.08)] outline-none transition-[background-color,border-color,color] duration-150 placeholder:text-[var(--workspace-text-faint)] focus:border-[color:rgba(103,141,103,0.5)] focus:bg-[var(--workspace-input-focus-bg)]";

function ManagedInboxEditor({
  mailbox,
  editable,
  isExisting,
  isPrimary = false,
  onEditAction,
  onRemoveAction,
  removeDisabled = false,
  onApplyAction,
  onCancelAction,
  onChange,
}: {
  mailbox: ManagedWorkspaceInbox;
  editable: boolean;
  isExisting: boolean;
  isPrimary?: boolean;
  onEditAction?: () => void;
  onRemoveAction?: () => void;
  removeDisabled?: boolean;
  onApplyAction?: () => void;
  onCancelAction?: () => void;
  onChange: (
    inboxId: string,
    field: "title" | "email" | "provider" | keyof CustomImapSettings,
    value: string | boolean | ProviderId | null,
  ) => void;
}) {
  return (
    <section
      className={`rounded-[30px] border bg-[var(--workspace-card)] p-6 shadow-panel transition ${
        editable
          ? "border-[var(--workspace-border-hover)] shadow-[inset_0_1px_0_rgba(255,255,255,0.1),0_10px_24px_rgba(61,44,32,0.06)]"
          : "border-[var(--workspace-border-soft)]"
      }`}
    >
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-xl font-semibold text-[var(--workspace-text)]">
              {mailbox.title.trim().length > 0 ? mailbox.title : "New inbox"}
            </h3>
            {isPrimary ? (
              <span className="rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-2.5 py-1 text-[0.62rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-text-faint)]">
                Primary
              </span>
            ) : null}
          </div>
          <p className="mt-1 text-sm text-[var(--workspace-text-muted)]">
            {onboardingText.connect.inboxHint}
          </p>
        </div>
        {mailbox.connected ? (
          <span className="rounded-full border border-[var(--workspace-status-success-border)] bg-[var(--workspace-status-success-bg)] px-3 py-1 text-xs font-medium text-[var(--workspace-status-success-text)]">
            Connected
          </span>
        ) : null}
      </div>

      <div className="mb-5">
        <label className="mb-2 block text-sm font-medium text-[var(--workspace-text-soft)]">
          Inbox title
        </label>
        {editable ? (
          <input
            type="text"
            value={mailbox.title}
            onChange={(event) => onChange(mailbox.id, "title", event.target.value)}
            placeholder="Primary inbox"
            className={inputFieldClass}
          />
        ) : (
          <div className="rounded-2xl border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-3 text-[0.94rem] text-[var(--workspace-text)]">
            {mailbox.title.trim() || "Not set"}
          </div>
        )}
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {onboardingText.connect.providers.map((provider) => {
          const selected = mailbox.provider === provider.id;

          return (
            <button
              key={provider.id}
              type="button"
              onClick={() =>
                editable ? onChange(mailbox.id, "provider", provider.id) : undefined
              }
              disabled={!editable}
              className={`rounded-3xl border px-4 py-3 text-left transition outline-none ${
                selected
                  ? "border-[var(--workspace-provider-selected-border)] bg-[var(--workspace-provider-selected-surface)] text-[var(--workspace-provider-selected-text)] shadow-panel"
                  : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] text-[var(--workspace-text)]"
              } ${editable ? "hover:border-[var(--workspace-border-hover)] hover:bg-[var(--workspace-hover-surface)]" : "cursor-default opacity-90"}`}
            >
              <span className="text-sm font-semibold">{provider.label}</span>
            </button>
          );
        })}
      </div>

      <div className="mt-5">
        <label className="mb-2 block text-sm font-medium text-[var(--workspace-text-soft)]">
          {onboardingText.connect.email}
        </label>
        {editable ? (
          <input
            type="email"
            value={mailbox.email}
            onChange={(event) => onChange(mailbox.id, "email", event.target.value)}
            placeholder="name@company.com"
            className={inputFieldClass}
          />
        ) : (
          <div className="rounded-2xl border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-3 text-[0.94rem] text-[var(--workspace-text)]">
            {mailbox.email.trim() || "Not set"}
          </div>
        )}
      </div>

      {isImapCredentialsProvider(mailbox.provider) ? (
        <div className="mt-6 space-y-4 rounded-[24px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] p-5">
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <label className="mb-2 block text-sm font-medium text-[var(--workspace-text-soft)]">
                {onboardingText.connect.host}
              </label>
              {editable ? (
                <input
                  type="text"
                  value={mailbox.customImap.host}
                  onChange={(event) => onChange(mailbox.id, "host", event.target.value)}
                  className={inputFieldClass}
                />
              ) : (
                <div className="rounded-2xl border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3 text-[0.94rem] text-[var(--workspace-text)]">
                  {mailbox.customImap.host.trim() || "Not set"}
                </div>
              )}
            </div>
            <div>
              <label className="mb-2 block text-sm font-medium text-[var(--workspace-text-soft)]">
                {onboardingText.connect.port}
              </label>
              {editable ? (
                <input
                  type="text"
                  value={mailbox.customImap.port}
                  onChange={(event) => onChange(mailbox.id, "port", event.target.value)}
                  className={inputFieldClass}
                />
              ) : (
                <div className="rounded-2xl border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3 text-[0.94rem] text-[var(--workspace-text)]">
                  {mailbox.customImap.port.trim() || "Not set"}
                </div>
              )}
            </div>
            {mailbox.provider === "custom_imap" ? (
              <div>
                <label className="mb-2 block text-sm font-medium text-[var(--workspace-text-soft)]">
                  {onboardingText.connect.username}
                </label>
                {editable ? (
                  <input
                    type="text"
                    value={mailbox.customImap.username}
                    onChange={(event) => onChange(mailbox.id, "username", event.target.value)}
                    className={inputFieldClass}
                  />
                ) : (
                  <div className="rounded-2xl border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3 text-[0.94rem] text-[var(--workspace-text)]">
                    {mailbox.customImap.username.trim() || "Not set"}
                  </div>
                )}
              </div>
            ) : (
              <div>
                <label className="mb-2 block text-sm font-medium text-[var(--workspace-text-soft)]">
                  {onboardingText.connect.username}
                </label>
                <div className="rounded-2xl border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3 text-[0.94rem] text-[var(--workspace-text)]">
                  {mailbox.email.trim() || "Uses the Gmail address above"}
                </div>
              </div>
            )}
            <div>
              <label className="mb-2 block text-sm font-medium text-[var(--workspace-text-soft)]">
                {getPasswordLabel(mailbox.provider)}
              </label>
              {editable ? (
                <input
                  type="password"
                  value={mailbox.customImap.password}
                  onChange={(event) => onChange(mailbox.id, "password", event.target.value)}
                  className={inputFieldClass}
                />
              ) : (
                <div className="rounded-2xl border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3 text-[0.94rem] text-[var(--workspace-text)]">
                  {mailbox.customImap.password.trim().length > 0 ? "Saved" : "Not set"}
                </div>
              )}
            </div>
          </div>

          <label className="flex items-center gap-3 text-sm font-medium text-[var(--workspace-text-soft)]">
            <span className="relative flex h-4 w-4 items-center justify-center">
              <input
                type="checkbox"
                checked={mailbox.customImap.ssl}
                onChange={(event) =>
                  editable ? onChange(mailbox.id, "ssl", event.target.checked) : undefined
                }
                disabled={!editable}
                className={`peer absolute inset-0 m-0 h-full w-full appearance-none rounded-[5px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-input-bg)] outline-none transition checked:border-moss/55 checked:bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))] ${editable ? "cursor-pointer" : "cursor-default"}`}
              />
              <span className="pointer-events-none absolute inset-0 flex items-center justify-center text-[10px] font-semibold leading-none text-moss opacity-0 transition peer-checked:opacity-100">
                ✓
              </span>
            </span>
            {onboardingText.connect.ssl}
          </label>
        </div>
      ) : null}

      <div className="mt-6 flex justify-end gap-3">
        {editable && onCancelAction && onApplyAction ? (
          <>
            <button
              type="button"
              onClick={onCancelAction}
              className={settingsPairedSecondaryActionClass}
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={onApplyAction}
              disabled={!isManagedInboxReady(mailbox)}
              className={`${settingsPrimaryActionClass} w-[7.5rem]`}
            >
              Apply
            </button>
          </>
        ) : onEditAction ? (
          <>
            {isExisting && !isPrimary && onRemoveAction ? (
              <button
                type="button"
                onClick={onRemoveAction}
                disabled={removeDisabled}
                className={`${settingsSubtleActionClass} ${
                  removeDisabled
                    ? "cursor-default opacity-45 hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-card)] hover:text-[var(--workspace-text-soft)] hover:shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]"
                    : "border-[color:rgba(146,82,73,0.18)] text-[color:rgba(134,79,71,0.92)] hover:border-[color:rgba(146,82,73,0.28)] hover:bg-[color:rgba(249,238,235,0.92)] hover:text-[color:rgba(116,63,56,0.96)]"
                }`}
              >
                Remove
              </button>
            ) : null}
            <button
              type="button"
              onClick={onEditAction}
              className={settingsSubtleActionClass}
            >
              Edit
            </button>
          </>
        ) : null}
      </div>
    </section>
  );
}

const ManageInboxesView = memo(function ManageInboxesView({
  savedManagedInboxes,
  onBack,
  onApply,
  themeMode,
}: {
  savedManagedInboxes: ManagedWorkspaceInbox[];
  onBack: () => void;
  onApply: (nextMailboxes: ManagedWorkspaceInbox[]) => boolean;
  themeMode: "light" | "dark";
}) {
  const [draftManagedInboxes, setDraftManagedInboxes] = useState<ManagedWorkspaceInbox[]>(
    savedManagedInboxes.map(cloneManagedWorkspaceInbox),
  );
  const [isDiscardConfirmationOpen, setIsDiscardConfirmationOpen] = useState(false);
  const [editingInboxId, setEditingInboxId] = useState<string | null>(null);
  const [pendingInboxApplyId, setPendingInboxApplyId] = useState<string | null>(null);
  const [pendingInboxRemovalId, setPendingInboxRemovalId] = useState<string | null>(null);
  const [successToastMessage, setSuccessToastMessage] = useState<string | null>(null);

  useEffect(() => {
    setDraftManagedInboxes(savedManagedInboxes.map(cloneManagedWorkspaceInbox));
    setEditingInboxId(null);
    setPendingInboxApplyId(null);
    setPendingInboxRemovalId(null);
  }, [savedManagedInboxes]);

  useEffect(() => {
    if (!successToastMessage) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      setSuccessToastMessage(null);
    }, 2600);

    return () => window.clearTimeout(timeoutId);
  }, [successToastMessage]);

  const hasUnsavedChanges =
    JSON.stringify(draftManagedInboxes) !== JSON.stringify(savedManagedInboxes);

  const updateDraftInbox = (
    inboxId: string,
    field: "title" | "email" | "provider" | keyof CustomImapSettings,
    value: string | boolean | ProviderId | null,
  ) => {
    setDraftManagedInboxes((current) =>
      current.map((mailbox) => {
        if (mailbox.id !== inboxId) {
          return mailbox;
        }

        if (field === "provider") {
          return {
            ...mailbox,
            provider: value as ProviderId | null,
            connected: false,
            customImap: applyProviderDefaults(
              value as ProviderId | null,
              mailbox.customImap,
              mailbox.email,
            ),
          };
        }

        if (
          field === "host" ||
          field === "port" ||
          field === "username" ||
          field === "password" ||
          field === "ssl"
        ) {
          return {
            ...mailbox,
            connected: false,
            customImap: {
              ...mailbox.customImap,
              [field]: value,
            },
          };
        }

        if (field === "title") {
          return {
            ...mailbox,
            title: value as string,
            connected: false,
          };
        }

        if (field === "email") {
          return {
            ...mailbox,
            email: value as string,
            connected: false,
            customImap:
              mailbox.provider === "google"
                ? {
                    ...mailbox.customImap,
                    username: String(value).trim(),
                  }
                : mailbox.customImap,
          };
        }

        return { ...mailbox, [field]: value, connected: false };
      }),
    );
  };

  const handleStartAddInbox = () => {
    const nextId = `draft-${Date.now()}`;
    setDraftManagedInboxes((current) => [
      ...current,
      {
        id: nextId,
        title: "",
        email: "",
        provider: null,
        connected: false,
        customImap: createManagedCustomImapSettings(),
      },
    ]);
    setEditingInboxId(nextId);
  };

  const handleClose = () => {
    if (hasUnsavedChanges) {
      setIsDiscardConfirmationOpen(true);
      return;
    }

    onBack();
  };

  const handleApplyInbox = (inboxId: string) => {
    if (inboxId.startsWith("draft-")) {
      setDraftManagedInboxes((current) =>
        current.map((mailbox) =>
          mailbox.id === inboxId ? { ...mailbox, connected: true } : mailbox,
        ),
      );
      setEditingInboxId(null);
      return;
    }

    setPendingInboxApplyId(inboxId);
  };

  const handleCancelInbox = (inboxId: string) => {
    if (inboxId.startsWith("draft-")) {
      setDraftManagedInboxes((current) =>
        current.filter((mailbox) => mailbox.id !== inboxId),
      );
      setEditingInboxId((current) => (current === inboxId ? null : current));
      return;
    }

    const savedMailbox = savedManagedInboxes.find((mailbox) => mailbox.id === inboxId);

    if (savedMailbox) {
      setDraftManagedInboxes((current) =>
        current.map((mailbox) =>
          mailbox.id === inboxId ? cloneManagedWorkspaceInbox(savedMailbox) : mailbox,
        ),
      );
    }

    setEditingInboxId((current) => (current === inboxId ? null : current));
  };

  const pendingInboxRemoval =
    pendingInboxRemovalId === null
      ? null
      : savedManagedInboxes.find((mailbox) => mailbox.id === pendingInboxRemovalId) ?? null;

  return (
    <div className="flex min-h-[720px] flex-col">
      <div className="flex-1 space-y-8">
        <section className="space-y-8">
          <div className="space-y-3">
            <h1 className="text-3xl font-semibold tracking-tight text-[var(--workspace-text)]">
              Connected inboxes
            </h1>
            <p className="text-base text-[var(--workspace-text-muted)]">
              Manage the inboxes connected to this workspace with the same guided flow used during setup.
            </p>
          </div>

          <div className="space-y-6">
            {draftManagedInboxes.map((mailbox) => (
              <ManagedInboxEditor
                key={mailbox.id}
                mailbox={mailbox}
                editable={editingInboxId === mailbox.id}
                isExisting={!mailbox.id.startsWith("draft-")}
                isPrimary={mailbox.id === "main"}
                onEditAction={
                  editingInboxId === mailbox.id
                    ? undefined
                    : () => setEditingInboxId(mailbox.id)
                }
                onRemoveAction={
                  editingInboxId === mailbox.id || !mailbox.id || mailbox.id.startsWith("draft-")
                    ? undefined
                    : () => setPendingInboxRemovalId(mailbox.id)
                }
                removeDisabled={savedManagedInboxes.length <= 1}
                onApplyAction={() => handleApplyInbox(mailbox.id)}
                onCancelAction={() => handleCancelInbox(mailbox.id)}
                onChange={updateDraftInbox}
              />
            ))}

            <div className="pt-2 pb-4">
              <button
                type="button"
                onClick={handleStartAddInbox}
                className={settingsAccentSecondaryActionClass}
              >
                Add inbox
              </button>
            </div>
          </div>
        </section>
      </div>

        <NavigationBar
          canGoBack
          onBack={handleClose}
          onNext={() => {
            const nextMailboxes = draftManagedInboxes
              .filter(
                (mailbox) =>
                  !mailbox.id.startsWith("draft-") ||
                  Boolean(mailbox.provider) ||
                  mailbox.title.trim().length > 0 ||
                  mailbox.email.trim().length > 0,
              )
              .map((mailbox, index) => ({
                ...mailbox,
                id: mailbox.id.startsWith("draft-")
                  ? `custom:${(mailbox.title.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "inbox")}-${Date.now().toString(36)}-${index}`
                  : mailbox.id,
              }));
            const addedMailboxes = nextMailboxes.filter(
              (mailbox) =>
                !savedManagedInboxes.some((savedMailbox) => savedMailbox.id === mailbox.id),
            );
            const didApply = onApply(nextMailboxes);

            if (!didApply) {
              return;
            }

            if (addedMailboxes.length === 1) {
              setSuccessToastMessage(
                `Inbox '${addedMailboxes[0].title.trim() || "Inbox"}' added`,
              );
              return;
            }

            if (addedMailboxes.length > 1) {
              setSuccessToastMessage(`${addedMailboxes.length} inboxes added`);
              return;
            }

            setSuccessToastMessage("Inbox changes applied");
          }}
          nextLabel="Apply"
        />
      <SettingsConfirmationModal
        open={isDiscardConfirmationOpen}
        themeMode={themeMode}
        title="Discard changes?"
        description="Your unsaved inbox changes will be lost."
        confirmLabel="Discard"
        onCancel={() => setIsDiscardConfirmationOpen(false)}
        onConfirm={() => {
          setDraftManagedInboxes(savedManagedInboxes.map(cloneManagedWorkspaceInbox));
          setIsDiscardConfirmationOpen(false);
          onBack();
        }}
      />
      <SettingsConfirmationModal
        open={Boolean(pendingInboxApplyId)}
        themeMode={themeMode}
        title="Apply changes to this inbox?"
        description="This will keep your edits for this inbox and return the card to its read-only view."
        confirmLabel="Apply"
        onCancel={() => setPendingInboxApplyId(null)}
        onConfirm={() => {
          setEditingInboxId(null);
          setPendingInboxApplyId(null);
        }}
      />
      <SettingsConfirmationModal
        open={Boolean(pendingInboxRemoval)}
        themeMode={themeMode}
        title="Remove inbox"
        description="Are you sure you want to remove this inbox? This action cannot be undone."
        cancelLabel="Cancel"
        confirmLabel="Remove inbox"
        confirmClassName={settingsDangerActionClass}
        onCancel={() => setPendingInboxRemovalId(null)}
        onConfirm={() => {
          if (!pendingInboxRemoval) {
            setPendingInboxRemovalId(null);
            return;
          }

          const didRemove = onApply(
            savedManagedInboxes.filter((mailbox) => mailbox.id !== pendingInboxRemoval.id),
          );

          if (didRemove) {
            setSuccessToastMessage(
              `Inbox '${pendingInboxRemoval.title.trim() || "Inbox"}' removed`,
            );
          }

          setPendingInboxRemovalId(null);
        }}
      />
      {successToastMessage ? (
        <div className="pointer-events-none fixed bottom-6 right-6 z-[220]">
          <div className="rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3 text-[0.86rem] font-medium text-[var(--workspace-text)] shadow-panel">
            {successToastMessage}
          </div>
        </div>
      ) : null}
    </div>
  );
});

const signatureEditorClass =
  "min-h-[220px] w-full overflow-y-auto whitespace-pre-wrap rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-4 text-[0.9rem] leading-6 text-[var(--workspace-text)] outline-none transition-[border-color,box-shadow,background-color] duration-150 empty:before:pointer-events-none empty:before:text-[var(--workspace-text-faint)] empty:before:content-[attr(data-placeholder)] focus:border-[color:rgba(103,141,103,0.42)] focus:bg-[var(--workspace-input-focus-bg)] focus:shadow-[0_0_0_4px_rgba(103,141,103,0.08)] [&_div]:min-h-[1.5rem] [&_p]:min-h-[1.5rem]";

const signatureLayoutLabels: Array<{
  value: SignatureLayoutMode;
  label: string;
}> = [
  { value: "text-only", label: "Text only" },
  { value: "logo-below", label: "Text + logo below" },
  { value: "logo-left", label: "Logo left / details right" },
];

const SignatureBlock = memo(function SignatureBlock({
  signature,
  className = "",
}: {
  signature: InboxSignatureSettings;
  className?: string;
}) {
  const normalizedSignature = normalizeInboxSignatureSettings(signature);

  if (!hasSignatureContent(normalizedSignature)) {
    return null;
  }

  const hasText = normalizedSignature.html.trim().length > 0;
  const showLogo =
    Boolean(normalizedSignature.logoImageUrl) &&
    normalizedSignature.layout !== "text-only";
  const shouldShowDivider = normalizedSignature.showDivider && showLogo;
  const textBlock = hasText ? (
    <div
      className="whitespace-pre-wrap text-[0.86rem] leading-[1.45] text-[var(--workspace-text-soft)] [&_a]:text-[color:rgba(70,109,73,0.96)] [&_a]:underline [&_div]:min-h-[1.35rem] [&_em]:italic [&_p]:min-h-[1.35rem] [&_strong]:font-semibold"
      dangerouslySetInnerHTML={{ __html: normalizedSignature.html }}
    />
  ) : null;
  const logo = showLogo ? (
    <img
      src={normalizedSignature.logoImageUrl ?? undefined}
      alt=""
      className="max-h-[76px] w-auto max-w-full object-contain"
    />
  ) : null;

  if (normalizedSignature.layout === "logo-left" && showLogo) {
    return (
      <div className={`space-y-2.5 ${className}`}>
        {shouldShowDivider ? (
          <div className="h-px w-full bg-[color:rgba(121,151,120,0.18)]" />
        ) : null}
        <div className="flex items-start gap-4">
          <div className="flex min-h-[3.25rem] min-w-[96px] items-center">
            {logo}
          </div>
          <div className="min-w-0 flex-1">{textBlock}</div>
        </div>
      </div>
    );
  }

  if (normalizedSignature.layout === "logo-below" && showLogo) {
    return (
      <div className={`space-y-2.5 ${className}`}>
        {textBlock}
        {shouldShowDivider ? (
          <div className="h-px w-full bg-[color:rgba(121,151,120,0.18)]" />
        ) : null}
        <div className="pt-0.5">{logo}</div>
      </div>
    );
  }

  return <div className={className}>{textBlock}</div>;
});

const SignatureRichTextInput = memo(function SignatureRichTextInput({
  value,
  onChange,
}: {
  value: string;
  onChange: (nextValue: string) => void;
}) {
  const editorRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const editor = editorRef.current;

    if (!editor || editor.innerHTML === value) {
      return;
    }

    editor.innerHTML = value;
  }, [value]);

  const syncEditorValue = () => {
    const editor = editorRef.current;

    if (!editor) {
      return;
    }

    onChange(editor.innerHTML);
  };

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (!(event.metaKey || event.ctrlKey)) {
      return;
    }

    if (event.key.toLowerCase() === "b" || event.key.toLowerCase() === "i") {
      event.preventDefault();
      document.execCommand(event.key.toLowerCase() === "b" ? "bold" : "italic");
      syncEditorValue();
    }
  };

  return (
    <div
      ref={editorRef}
      contentEditable
      suppressContentEditableWarning
      role="textbox"
      aria-multiline="true"
      data-placeholder="Write your signature"
      className={signatureEditorClass}
      onInput={syncEditorValue}
      onPaste={(event) => {
        event.preventDefault();
        const pastedText = event.clipboardData.getData("text/plain");

        if (!pastedText) {
          return;
        }

        pastedText.split("\n").forEach((line, index) => {
          if (index > 0) {
            document.execCommand("insertLineBreak");
          }

          if (line.length > 0) {
            document.execCommand("insertText", false, line);
          }
        });

        syncEditorValue();
      }}
      onBlur={() => {
        const editor = editorRef.current;

        if (!editor) {
          return;
        }

        const sanitizedValue = sanitizeSignatureHtml(editor.innerHTML);
        editor.innerHTML = sanitizedValue;
        onChange(sanitizedValue);
      }}
      onKeyDown={handleKeyDown}
    />
  );
});

const SignatureSettingsModal = memo(function SignatureSettingsModal({
  open,
  themeMode,
  inboxEmail,
  signature,
  onChangeSignatureHtml,
  onChangeUseByDefault,
  onChangeLayout,
  onChangeShowDivider,
  onChangeLogoImageUrl,
  onCancel,
  onSave,
}: {
  open: boolean;
  themeMode: "light" | "dark";
  inboxEmail: string;
  signature: InboxSignatureSettings;
  onChangeSignatureHtml: (nextValue: string) => void;
  onChangeUseByDefault: (nextValue: boolean) => void;
  onChangeLayout: (nextValue: SignatureLayoutMode) => void;
  onChangeShowDivider: (nextValue: boolean) => void;
  onChangeLogoImageUrl: (nextValue: string | null) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  return (
    <SettingsModalShell
      open={open}
      themeMode={themeMode}
      maxWidthClass="max-w-[760px]"
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-[1.25rem] font-medium tracking-tight text-[var(--workspace-text)]">
            Signature — {inboxEmail}
          </h2>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onCancel}
            className={settingsPairedSecondaryActionClass}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onSave}
            className={`${settingsPrimaryActionClass} w-[7.5rem]`}
          >
            Save
          </button>
        </div>
      </div>

      <div className="mt-6 space-y-5">
        <div className="space-y-3">
          <SignatureRichTextInput
            value={signature.html}
            onChange={onChangeSignatureHtml}
          />
          <p className="text-[0.76rem] leading-6 text-[var(--workspace-text-faint)]">
            Use Cmd/Ctrl+B for bold and Cmd/Ctrl+I for italic. Links are detected
            automatically.
          </p>
        </div>

        <div className={settingsCardSectionClass}>
          <div className="mb-2 text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
            Layout
          </div>
          <div className="flex flex-wrap gap-2">
            {signatureLayoutLabels.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => onChangeLayout(option.value)}
                className={settingsPillButtonClass(signature.layout === option.value)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>

        <div className={settingsCardSectionClass}>
          <div className="mb-3 flex items-start justify-between gap-4">
            <div>
              <div className="text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                Logo
              </div>
              <div className="mt-1 text-[0.82rem] leading-6 text-[var(--workspace-text-muted)]">
                Add one logo or mark for a clean professional signature.
              </div>
            </div>
            <label className={`${settingsGhostActionClass} cursor-pointer`}>
              {signature.logoImageUrl ? "Replace image" : "Upload image"}
              <input
                type="file"
                accept="image/*"
                className="sr-only"
                onChange={(event) => {
                  const file = event.target.files?.[0];

                  if (!file) {
                    return;
                  }

                  const reader = new FileReader();
                  reader.onload = () => {
                    if (typeof reader.result === "string") {
                      onChangeLogoImageUrl(reader.result);
                    }
                  };
                  reader.readAsDataURL(file);
                  event.target.value = "";
                }}
              />
            </label>
          </div>

          {signature.logoImageUrl ? (
            <div className="flex items-center justify-between gap-4 rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3">
              <img
                src={signature.logoImageUrl}
                alt=""
                className="max-h-[56px] w-auto max-w-[160px] object-contain"
              />
              <button
                type="button"
                onClick={() => onChangeLogoImageUrl(null)}
                className={settingsSecondaryGhostActionClass}
              >
                Remove
              </button>
            </div>
          ) : (
            <div className="rounded-[18px] border border-dashed border-[var(--workspace-border)] bg-[var(--workspace-card)] px-4 py-4 text-[0.84rem] leading-6 text-[var(--workspace-text-muted)]">
              No image uploaded
            </div>
          )}
        </div>

        <label className="flex items-center gap-3 text-[0.88rem] text-[var(--workspace-text)]">
          <span className="relative flex h-4 w-4 items-center justify-center">
            <input
              type="checkbox"
              checked={signature.useByDefault}
              onChange={(event) => onChangeUseByDefault(event.target.checked)}
              className="peer absolute inset-0 m-0 h-full w-full appearance-none rounded-[5px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-input-bg)] outline-none transition checked:border-moss/55 checked:bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))] cursor-pointer"
            />
            <span className="pointer-events-none absolute inset-0 flex items-center justify-center text-[10px] font-semibold leading-none text-moss opacity-0 transition peer-checked:opacity-100">
              ✓
            </span>
          </span>
          Use this signature by default
        </label>

        <label className="flex items-center gap-3 text-[0.88rem] text-[var(--workspace-text)]">
          <span className="relative flex h-4 w-4 items-center justify-center">
            <input
              type="checkbox"
              checked={signature.showDivider}
              onChange={(event) => onChangeShowDivider(event.target.checked)}
              className="peer absolute inset-0 m-0 h-full w-full appearance-none rounded-[5px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-input-bg)] outline-none transition checked:border-moss/55 checked:bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))] cursor-pointer"
            />
            <span className="pointer-events-none absolute inset-0 flex items-center justify-center text-[10px] font-semibold leading-none text-moss opacity-0 transition peer-checked:opacity-100">
              ✓
            </span>
          </span>
          Show divider above lower section
        </label>

        <div className="border-t border-[var(--workspace-border-soft)] pt-5">
          <div className="mb-3 text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
            Preview
          </div>
          <div className="rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-4">
            {hasSignatureContent(signature) ? (
              <SignatureBlock signature={signature} />
            ) : (
              <div className="text-[0.88rem] leading-6 text-[var(--workspace-text-muted)]">
                Your signature preview will appear here.
              </div>
            )}
          </div>
        </div>
      </div>
    </SettingsModalShell>
  );
});

const OutOfOfficeSettingsModal = memo(function OutOfOfficeSettingsModal({
  open,
  themeMode,
  inboxEmail,
  outOfOffice,
  reuseOptions,
  onChangeEnabled,
  onChangeMessage,
  onReuseMessage,
  onCancel,
  onSave,
}: {
  open: boolean;
  themeMode: "light" | "dark";
  inboxEmail: string;
  outOfOffice: InboxOutOfOfficeSettings;
  reuseOptions: Array<{ inboxEmail: string; message: string }>;
  onChangeEnabled: (nextValue: boolean) => void;
  onChangeMessage: (nextValue: string) => void;
  onReuseMessage: (message: string) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  return (
    <SettingsModalShell
      open={open}
      themeMode={themeMode}
      maxWidthClass="max-w-[760px]"
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-[1.25rem] font-medium tracking-tight text-[var(--workspace-text)]">
            Out of office — {inboxEmail}
          </h2>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onCancel}
            className={settingsPairedSecondaryActionClass}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onSave}
            className={`${settingsPrimaryActionClass} w-[7.5rem]`}
          >
            Save
          </button>
        </div>
      </div>

      <div className="mt-6 space-y-5">
        <div className={settingsCardSectionClass}>
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="text-[0.88rem] font-medium text-[var(--workspace-text)]">
                Out of office enabled
              </div>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={outOfOffice.enabled}
              aria-label="Out of office enabled"
              onClick={() => onChangeEnabled(!outOfOffice.enabled)}
              className={settingsToggleButtonClass(outOfOffice.enabled)}
            >
              <span
                className={`h-[1.15rem] w-[1.15rem] rounded-full bg-[rgba(255,252,247,0.98)] shadow-[0_3px_8px_rgba(31,42,36,0.12)] transition-[transform,box-shadow,background-color] duration-150 ${
                  outOfOffice.enabled ? "ring-1 ring-white/40" : ""
                }`}
              />
            </button>
          </div>
        </div>

        {reuseOptions.length > 0 ? (
          <div className="space-y-2">
            <div className="text-[0.76rem] leading-6 text-[var(--workspace-text-faint)]">
              {reuseOptions.length === 1
                ? "Use message from:"
                : "Use message from:"}
            </div>
            <div className="flex flex-wrap gap-2">
              {reuseOptions.map((option) => (
                <button
                  key={`${inboxEmail}-${option.inboxEmail}`}
                  type="button"
                  onClick={() => onReuseMessage(option.message)}
                  className={settingsPillButtonClass(false)}
                >
                  {option.inboxEmail}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        {outOfOffice.enabled ? (
          <div className="space-y-3">
            <div className={settingsCardSectionClass}>
              <label className="mb-2 block text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                Auto-reply message
              </label>
              <textarea
                value={outOfOffice.message}
                onChange={(event) => onChangeMessage(event.target.value)}
                placeholder="Write your automatic reply..."
                className="min-h-[220px] w-full resize-none rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-4 text-[0.92rem] leading-7 text-[var(--workspace-text)] outline-none transition-[border-color,box-shadow,background-color] duration-150 placeholder:text-[var(--workspace-text-faint)] focus:border-[color:rgba(103,141,103,0.42)] focus:bg-[var(--workspace-input-focus-bg)] focus:shadow-[0_0_0_4px_rgba(103,141,103,0.08)]"
              />
            </div>
            <p className="text-[0.82rem] leading-6 text-[var(--workspace-text-muted)]">
              This reply will be sent automatically for this inbox only.
            </p>
          </div>
        ) : (
          <div className="rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-4 text-[0.88rem] leading-6 text-[var(--workspace-text-muted)]">
            Auto-replies are currently turned off for this inbox.
          </div>
        )}
      </div>
    </SettingsModalShell>
  );
});

const SmartFolderModal = memo(function SmartFolderModal({
  open,
  themeMode,
  connectedInboxes,
  isEditing,
  draftName,
  draftScope,
  draftSelectedInboxIds,
  draftRules,
  onChangeName,
  onChangeScope,
  onToggleInbox,
  onChangeRuleField,
  onChangeRuleValue,
  onAddRule,
  onRemoveRule,
  onDelete,
  onCancel,
  onSave,
}: {
  open: boolean;
  themeMode: "light" | "dark";
  connectedInboxes: OrderedMailbox[];
  isEditing: boolean;
  draftName: string;
  draftScope: "all" | "selected";
  draftSelectedInboxIds: InboxId[];
  draftRules: SmartFolderRule[];
  onChangeName: (nextValue: string) => void;
  onChangeScope: (nextValue: "all" | "selected") => void;
  onToggleInbox: (inboxId: InboxId) => void;
  onChangeRuleField: (ruleId: string, nextField: SmartFolderRuleField) => void;
  onChangeRuleValue: (ruleId: string, nextValue: string) => void;
  onAddRule: () => void;
  onRemoveRule: (ruleId: string) => void;
  onDelete?: () => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  const [isDeleteConfirmationOpen, setIsDeleteConfirmationOpen] = useState(false);
  const hasValidName = draftName.trim().length > 0;
  const hasValidRule = draftRules.some((rule) => rule.value.trim().length > 0);
  const hasValidInboxSelection =
    draftScope === "all" || draftSelectedInboxIds.length > 0;
  const canSave = hasValidName && hasValidRule && hasValidInboxSelection;

  return (
    <SettingsModalShell
      open={open}
      themeMode={themeMode}
      maxWidthClass="max-w-[760px]"
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-[1.25rem] font-medium tracking-tight text-[var(--workspace-text)]">
            Smart folder
          </h2>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onCancel}
            className={settingsPairedSecondaryActionClass}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onSave}
            disabled={!canSave}
            className={`${settingsPrimaryActionClass} w-[7.5rem] disabled:cursor-not-allowed disabled:border-[color:rgba(120,104,89,0.14)] disabled:bg-[linear-gradient(180deg,rgba(167,174,167,0.42),rgba(131,137,131,0.52))] disabled:text-[color:rgba(251,248,242,0.78)] disabled:shadow-none`}
          >
            Save
          </button>
        </div>
      </div>

      <div className="mt-6 space-y-5">
        <div className={settingsCardSectionClass}>
          <label className="mb-2 block text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
            Folder name
          </label>
          <input
            type="text"
            value={draftName}
            onChange={(event) => onChangeName(event.target.value)}
            className={inputFieldClass}
            placeholder="e.g. Royalties"
          />
        </div>

        <div className={settingsCardSectionClass}>
          <div className="mb-3 text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
            Applies to
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => onChangeScope("all")}
              className={settingsPillButtonClass(draftScope === "all")}
            >
              All inboxes
            </button>
            <button
              type="button"
              onClick={() => onChangeScope("selected")}
              className={settingsPillButtonClass(draftScope === "selected")}
            >
              Selected inboxes
            </button>
          </div>

          {draftScope === "selected" ? (
            <div className="mt-4 space-y-2">
              {connectedInboxes.map((mailbox) => {
                const selected = draftSelectedInboxIds.includes(mailbox.id);

                return (
                  <label
                    key={`smart-folder-inbox-${mailbox.id}`}
                    className="flex items-center gap-3 rounded-[16px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3 text-[0.88rem] text-[var(--workspace-text)]"
                  >
                    <span className="relative flex h-4 w-4 items-center justify-center">
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={() => onToggleInbox(mailbox.id)}
                        className="peer absolute inset-0 m-0 h-full w-full appearance-none rounded-[5px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-input-bg)] outline-none transition checked:border-moss/55 checked:bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))] cursor-pointer"
                      />
                      <span className="pointer-events-none absolute inset-0 flex items-center justify-center text-[10px] font-semibold leading-none text-moss opacity-0 transition peer-checked:opacity-100">
                        ✓
                      </span>
                    </span>
                    {mailbox.email}
                  </label>
                );
              })}
            </div>
          ) : null}
        </div>

        <div className={settingsCardSectionClass}>
          <div className="mb-3 text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
            Match emails where
          </div>
          <div className="space-y-3">
            {draftRules.map((rule) => (
              <div
                key={rule.id}
                className="grid gap-3 rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3 md:grid-cols-[140px_120px_minmax(0,1fr)_auto]"
              >
                <select
                  value={rule.field}
                  onChange={(event) =>
                    onChangeRuleField(rule.id, event.target.value as SmartFolderRuleField)
                  }
                  className={inputFieldClass}
                >
                  <option value="From">From</option>
                  <option value="Subject">Subject</option>
                  <option value="Domain">Domain</option>
                </select>
                <div className="flex items-center rounded-[16px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 text-[0.84rem] text-[var(--workspace-text-soft)]">
                  contains
                </div>
                <input
                  type="text"
                  value={rule.value}
                  onChange={(event) => onChangeRuleValue(rule.id, event.target.value)}
                  className={inputFieldClass}
                  placeholder={
                    rule.field === "From"
                      ? "e.g. artist@label.com"
                      : rule.field === "Subject"
                        ? "e.g. statement"
                        : "e.g. beatport.com"
                  }
                />
                {draftRules.length > 1 ? (
                  <button
                    type="button"
                    onClick={() => onRemoveRule(rule.id)}
                    className={settingsSecondaryGhostActionClass}
                  >
                    Remove
                  </button>
                ) : null}
              </div>
            ))}
          </div>

          <button
            type="button"
            onClick={onAddRule}
            className={`${settingsGhostActionClass} mt-4`}
          >
            + Add rule
          </button>

          <p className="mt-4 text-[0.82rem] leading-6 text-[var(--workspace-text-muted)]">
            Matching emails will automatically appear in this folder.
          </p>
        </div>

        {isEditing && onDelete ? (
          <div className="border-t border-[var(--workspace-border-soft)] pt-4">
            <button
              type="button"
              onClick={() => setIsDeleteConfirmationOpen(true)}
              className="text-[0.78rem] font-medium text-[color:rgba(146,88,74,0.92)] transition-colors duration-150 hover:text-[color:rgba(131,76,63,0.98)] focus-visible:outline-none"
            >
              Delete folder
            </button>
          </div>
        ) : null}
      </div>
      <SettingsConfirmationModal
        open={isDeleteConfirmationOpen}
        themeMode={themeMode}
        title="Delete this smart folder?"
        description="Emails will not be removed."
        confirmLabel="Delete"
        onCancel={() => setIsDeleteConfirmationOpen(false)}
        onConfirm={() => {
          setIsDeleteConfirmationOpen(false);
          onDelete?.();
        }}
      />
    </SettingsModalShell>
  );
});

const MailSettingsCard = memo(function MailSettingsCard({
  managedInboxes,
  inboxOutOfOffice,
  themeMode,
  onManageSignature,
  onManageOutOfOffice,
}: {
  managedInboxes: ManagedWorkspaceInbox[];
  inboxOutOfOffice: InboxOutOfOfficeStore;
  themeMode: "light" | "dark";
  onManageSignature: (inbox: ManagedWorkspaceInbox) => void;
  onManageOutOfOffice: (inbox: ManagedWorkspaceInbox) => void;
}) {
  const connectedInboxes = managedInboxes.filter(
    (mailbox) => mailbox.email.trim().length > 0,
  );

  return (
    <section className="flex h-full flex-col space-y-2.5">
      <div className={settingsSectionLabelClass}>Mail settings</div>
      <div className={settingsCardClass(themeMode)}>
        <div className="mb-3 space-y-2">
          <h2 className="text-[1.1rem] font-medium tracking-tight text-[var(--workspace-text)]">
            Mail settings
          </h2>
          <p className="max-w-xl text-[0.9rem] leading-6 text-[var(--workspace-text-muted)]">
            Control how each inbox behaves when sending and receiving emails.
          </p>
        </div>

        <div className="space-y-3.5">
          <div className={settingsCardSectionClass}>
            <div className="mb-3 text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
              Signatures
            </div>

            {connectedInboxes.length > 0 ? (
              <div className="overflow-hidden rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)]">
                {connectedInboxes.map((mailbox, index) => (
                  <div
                    key={mailbox.id}
                    className={`flex items-center justify-between gap-4 px-4 py-3.5 ${
                      index > 0
                        ? "border-t border-[var(--workspace-border-soft)]"
                        : ""
                    }`}
                  >
                    <div className="min-w-0">
                      <div className="truncate text-[0.92rem] font-medium text-[var(--workspace-text)]">
                        {mailbox.email}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => onManageSignature(mailbox)}
                      className={settingsSubtleActionClass}
                    >
                      Manage signature
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-6 text-[0.9rem] text-[var(--workspace-text-muted)]">
                No inboxes connected
              </div>
            )}
          </div>

          <div className={settingsCardSectionClass}>
            <div className="mb-3 text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
              Out of office
            </div>

            {connectedInboxes.length > 0 ? (
              <div className="overflow-hidden rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)]">
                {connectedInboxes.map((mailbox, index) => {
                  const outOfOfficeEnabled = normalizeInboxOutOfOfficeSettings(
                    inboxOutOfOffice[mailbox.id as InboxId],
                  ).enabled;

                  return (
                    <div
                      key={`out-of-office-${mailbox.id}`}
                      className={`flex items-center justify-between gap-4 px-4 py-3.5 ${
                        index > 0
                          ? "border-t border-[var(--workspace-border-soft)]"
                          : ""
                      }`}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-[0.92rem] font-medium text-[var(--workspace-text)]">
                          {mailbox.email}
                        </div>
                      </div>
                      <div className="flex flex-none items-center gap-3">
                        <span
                          className={`inline-flex min-w-[3.25rem] items-center justify-center rounded-full border px-3 py-1 text-[0.66rem] font-medium uppercase tracking-[0.14em] ${
                            outOfOfficeEnabled
                              ? "border-[var(--workspace-status-success-border)] bg-[var(--workspace-status-success-bg)] text-[var(--workspace-status-success-text)]"
                              : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] text-[var(--workspace-text-soft)]"
                          }`}
                        >
                          {outOfOfficeEnabled ? "On" : "Off"}
                        </span>
                        <button
                          type="button"
                          onClick={() => onManageOutOfOffice(mailbox)}
                          className={settingsSubtleActionClass}
                        >
                          Manage
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-6 text-[0.9rem] text-[var(--workspace-text-muted)]">
                No inboxes connected
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
});

const InboxBehaviorSettingsCard = memo(function InboxBehaviorSettingsCard({
  themeMode,
}: {
  themeMode: "light" | "dark";
}) {
  const [savedLearningSuggestionsEnabled, setSavedLearningSuggestionsEnabled] = useState(true);
  const [savedUnknownSendersChoice, setSavedUnknownSendersChoice] = useState<
    "keep" | "review"
  >("review");
  const [savedAutomationLevel, setSavedAutomationLevel] = useState<
    "Conservative" | "Balanced" | "Proactive"
  >("Balanced");
  const [draftLearningSuggestionsEnabled, setDraftLearningSuggestionsEnabled] = useState(
    savedLearningSuggestionsEnabled,
  );
  const [draftUnknownSendersChoice, setDraftUnknownSendersChoice] = useState<
    "keep" | "review"
  >(savedUnknownSendersChoice);
  const [draftAutomationLevel, setDraftAutomationLevel] = useState(savedAutomationLevel);

  const hasUnsavedChanges =
    draftLearningSuggestionsEnabled !== savedLearningSuggestionsEnabled ||
    draftUnknownSendersChoice !== savedUnknownSendersChoice ||
    draftAutomationLevel !== savedAutomationLevel;

  const handleCancel = () => {
    setDraftLearningSuggestionsEnabled(savedLearningSuggestionsEnabled);
    setDraftUnknownSendersChoice(savedUnknownSendersChoice);
    setDraftAutomationLevel(savedAutomationLevel);
  };

  const handleApply = () => {
    setSavedLearningSuggestionsEnabled(draftLearningSuggestionsEnabled);
    setSavedUnknownSendersChoice(draftUnknownSendersChoice);
    setSavedAutomationLevel(draftAutomationLevel);
  };

  return (
    <section className="flex h-full flex-col space-y-2.5">
      <div className={settingsSectionLabelClass}>Inbox behavior</div>
      <div className={settingsCardClass(themeMode)}>
        <div className="mb-3 flex items-center justify-between gap-4">
          <h2 className="text-[1.1rem] font-medium tracking-tight text-[var(--workspace-text)]">
            Inbox behavior
          </h2>
        </div>

        <div className="space-y-3.5">
          <SettingsToggleRow
            label="Learning suggestions"
            enabled={draftLearningSuggestionsEnabled}
            onToggle={() => setDraftLearningSuggestionsEnabled((current) => !current)}
          />

          <div className={settingsCardSectionClass}>
            <div className="mb-2 flex items-center justify-between gap-4">
              <div className="text-[0.86rem] text-[var(--workspace-text)]">Unknown senders</div>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => setDraftUnknownSendersChoice("keep")}
                className={settingsPillButtonClass(draftUnknownSendersChoice === "keep")}
              >
                Keep in Inbox
              </button>
              <button
                type="button"
                onClick={() => setDraftUnknownSendersChoice("review")}
                className={settingsPillButtonClass(draftUnknownSendersChoice === "review")}
              >
                Move out of Inbox
              </button>
            </div>
          </div>

          <div className={settingsCardSectionClass}>
            <div className="mb-2 text-[0.86rem] text-[var(--workspace-text)]">
              Automation level
            </div>
            <div className="flex flex-wrap gap-2">
              {(["Conservative", "Balanced", "Proactive"] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => setDraftAutomationLevel(option)}
                  className={settingsPillButtonClass(draftAutomationLevel === option)}
                >
                  {option}
                </button>
              ))}
            </div>
          </div>

          {hasUnsavedChanges ? (
            <div className="flex justify-end gap-3 pt-1">
              <button
                type="button"
                onClick={handleCancel}
                className={settingsPairedSecondaryActionClass}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleApply}
                className={`${settingsPrimaryActionClass} w-[7.5rem]`}
              >
                Apply
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
});

const NotificationsSettingsCard = memo(function NotificationsSettingsCard({
  themeMode,
  inboxChangesEnabled,
  onToggleInboxChanges,
  teamActivityEnabled,
  onToggleTeamActivity,
}: {
  themeMode: "light" | "dark";
  inboxChangesEnabled: boolean;
  onToggleInboxChanges: () => void;
  teamActivityEnabled: boolean;
  onToggleTeamActivity: () => void;
}) {
  return (
    <section className="flex h-full flex-col space-y-2.5">
      <div className={settingsSectionLabelClass}>Notifications</div>
      <div className={settingsCardClass(themeMode)}>
        <div className="mb-3 flex items-center justify-between gap-4">
          <h2 className="text-[1.1rem] font-medium tracking-tight text-[var(--workspace-text)]">
            Notifications
          </h2>
        </div>
        <div className="space-y-2">
          <SettingsToggleRow
            label="Inbox changes"
            enabled={inboxChangesEnabled}
            onToggle={onToggleInboxChanges}
          />
          <SettingsToggleRow
            label="Team activity"
            enabled={teamActivityEnabled}
            onToggle={onToggleTeamActivity}
          />
        </div>
      </div>
    </section>
  );
});

const AccountSettingsCard = memo(function AccountSettingsCard({
  themeMode,
}: {
  themeMode: "light" | "dark";
}) {
  const [isManaging, setIsManaging] = useState(false);
  const [isLogoutConfirmationOpen, setIsLogoutConfirmationOpen] = useState(false);
  const [savedName, setSavedName] = useState("Milan Vermeer");
  const [savedEmail, setSavedEmail] = useState("milan@cuevion.com");
  const [savedPlan, setSavedPlan] = useState<"Single User" | "Team" | "Enterprise">(
    "Single User",
  );
  const [draftName, setDraftName] = useState(savedName);
  const [isChangeEmailOpen, setIsChangeEmailOpen] = useState(false);
  const [isResetPasswordOpen, setIsResetPasswordOpen] = useState(false);
  const [isResetPasswordSent, setIsResetPasswordSent] = useState(false);
  const [isManagePlanOpen, setIsManagePlanOpen] = useState(false);
  const [draftPlan, setDraftPlan] = useState(savedPlan);
  const [nextEmail, setNextEmail] = useState("");
  const [confirmNextEmail, setConfirmNextEmail] = useState("");
  const [password, setPassword] = useState("");

  useEffect(() => {
    if (!isManaging) {
      setDraftName(savedName);
    }
  }, [isManaging, savedName]);

  const hasUnsavedChanges = draftName !== savedName;

  const handleCloseManage = () => {
    setDraftName(savedName);
    setIsManaging(false);
  };

  const handleCancel = () => {
    setDraftName(savedName);
  };

  const handleApply = () => {
    setSavedName(draftName);
  };

  const handleCloseChangeEmail = () => {
    setIsChangeEmailOpen(false);
    setNextEmail("");
    setConfirmNextEmail("");
    setPassword("");
  };

  const handleCloseResetPassword = () => {
    setIsResetPasswordOpen(false);
    setIsResetPasswordSent(false);
  };

  const handleCloseManagePlan = () => {
    setDraftPlan(savedPlan);
    setIsManagePlanOpen(false);
  };

  const normalizedNextEmail = nextEmail.trim();
  const normalizedConfirmEmail = confirmNextEmail.trim();
  const normalizedNextEmailForComparison = normalizedNextEmail.toLowerCase();
  const normalizedConfirmEmailForComparison = normalizedConfirmEmail.toLowerCase();
  const emailFormatValid =
    normalizedNextEmail.length === 0 || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedNextEmail);
  const emailsMatch =
    normalizedConfirmEmail.length === 0 ||
    normalizedNextEmailForComparison === normalizedConfirmEmailForComparison;
  const emailChanged =
    normalizedNextEmail.length > 0 &&
    normalizedNextEmailForComparison !== savedEmail.trim().toLowerCase();
  const canSubmitEmailChange =
    emailChanged &&
    emailFormatValid &&
    normalizedConfirmEmail.length > 0 &&
    emailsMatch &&
    password.trim().length > 0;

  const newEmailFieldClass = `${inputFieldClass} ${
    normalizedNextEmail.length > 0 && !emailFormatValid
      ? "border-[color:rgba(146,88,74,0.38)] focus:border-[color:rgba(146,88,74,0.52)]"
      : ""
  }`;
  const confirmEmailFieldClass = `${inputFieldClass} ${
    normalizedConfirmEmail.length > 0 && !emailsMatch
      ? "border-[color:rgba(146,88,74,0.38)] focus:border-[color:rgba(146,88,74,0.52)]"
      : ""
  }`;

  return (
    <section className="flex h-full flex-col space-y-2.5">
      <div className={settingsSectionLabelClass}>Account</div>
      <div className={settingsCardClass(themeMode)}>
        <div className="mb-3 flex items-center justify-between gap-4">
          <h2 className="text-[1.1rem] font-medium tracking-tight text-[var(--workspace-text)]">
            Account
          </h2>
          <div className="ml-auto flex flex-none items-center gap-2.5 self-start">
            {isManaging ? (
              <CloseActionButton onClick={handleCloseManage} />
            ) : (
              <button
                type="button"
                onClick={() => setIsManaging(true)}
                className={settingsPrimaryActionClass}
              >
                Manage
              </button>
            )}
          </div>
        </div>

        <div className="space-y-2">
          {isManaging ? (
            <div className={settingsCardSectionClass}>
              <label className="mb-2 block text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                Name
              </label>
              <input
                type="text"
                value={draftName}
                onChange={(event) => setDraftName(event.target.value)}
                className={inputFieldClass}
              />
            </div>
          ) : (
            <SettingsInfoRow label="Name" value={savedName} />
          )}
          <SettingsInfoRow
            label="Email"
            value={savedEmail}
            actionLabel={isManaging ? "Change email" : undefined}
            onActionClick={isManaging ? () => setIsChangeEmailOpen(true) : undefined}
          />
          <SettingsInfoRow
            label="Plan"
            value={savedPlan}
            actionLabel={isManaging ? "Manage" : undefined}
            onActionClick={isManaging ? () => setIsManagePlanOpen(true) : undefined}
          />

          {isManaging ? (
            <div className={settingsCardSectionClass}>
              <div className="flex items-center justify-between gap-4">
                <div>
                  <div className="text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                    Password
                  </div>
                  <div className="mt-1 text-[0.86rem] text-[var(--workspace-text-muted)]">
                    Update your sign-in password securely.
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setIsResetPasswordOpen(true)}
                  className={settingsSubtleActionClass}
                >
                  Reset password
                </button>
              </div>
            </div>
          ) : (
            <SettingsInfoRow label="Password" value="Reset password" />
          )}

          {isManaging && hasUnsavedChanges ? (
            <div className="flex justify-end gap-3 pt-1">
              <button
                type="button"
                onClick={handleCancel}
                className={settingsPairedSecondaryActionClass}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleApply}
                className={`${settingsPrimaryActionClass} w-[7.5rem]`}
              >
                Apply
              </button>
            </div>
          ) : null}
        </div>

        <div className="mt-4 border-t border-[var(--workspace-border-soft)] pt-4">
          <button
            type="button"
            onClick={() => setIsLogoutConfirmationOpen(true)}
            className={settingsSubtleActionClass}
          >
            Log out
          </button>
        </div>
      </div>

      <SettingsConfirmationModal
        open={isLogoutConfirmationOpen}
        themeMode={themeMode}
        title="Log out?"
        description="You'll be signed out of Cuevion on this device."
        confirmLabel="Log out"
        onCancel={() => setIsLogoutConfirmationOpen(false)}
        onConfirm={() => {
          setIsLogoutConfirmationOpen(false);
          console.log("settings_log_out_confirm");
        }}
      />
      <SettingsModalShell
        open={isChangeEmailOpen}
        themeMode={themeMode}
        maxWidthClass="max-w-[520px]"
      >
        <div className="space-y-2">
          <h2 className="text-[1.25rem] font-medium tracking-tight text-[var(--workspace-text)]">
            Change email
          </h2>
          <p className="text-[0.9rem] leading-7 text-[var(--workspace-text-soft)]">
            Confirm your new email and password before continuing.
          </p>
        </div>

        <div className="mt-6 space-y-4">
          <div>
            <label className="mb-2 block text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
              New email
            </label>
            <input
              type="email"
              value={nextEmail}
              onChange={(event) => setNextEmail(event.target.value)}
              className={newEmailFieldClass}
              placeholder="name@company.com"
            />
            {normalizedNextEmail.length > 0 && !emailFormatValid ? (
              <p className="mt-2 text-[0.78rem] text-[color:rgba(146,88,74,0.92)]">
                Enter a valid email address.
              </p>
            ) : null}
          </div>

          <div>
            <label className="mb-2 block text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
              Confirm new email
            </label>
            <input
              type="email"
              value={confirmNextEmail}
              onChange={(event) => setConfirmNextEmail(event.target.value)}
              className={confirmEmailFieldClass}
              placeholder="Repeat your new email"
            />
            {normalizedConfirmEmail.length > 0 && !emailsMatch ? (
              <p className="mt-2 text-[0.78rem] text-[color:rgba(146,88,74,0.92)]">
                Email addresses must match.
              </p>
            ) : null}
          </div>

          <div>
            <label className="mb-2 block text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className={inputFieldClass}
              placeholder="Enter your password"
            />
          </div>

          {!emailChanged && normalizedNextEmail.length > 0 && emailFormatValid ? (
            <p className="text-[0.78rem] text-[var(--workspace-text-soft)]">
              Enter an email address different from your current one.
            </p>
          ) : null}
        </div>

        <div className="mt-6 flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={handleCloseChangeEmail}
            className={settingsPairedSecondaryActionClass}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => {
              if (!canSubmitEmailChange) {
                return;
              }

              setSavedEmail(normalizedNextEmail);
              handleCloseChangeEmail();
              console.log("settings_change_email_continue");
            }}
            disabled={!canSubmitEmailChange}
            className={`${settingsPrimaryActionClass} w-[7.5rem] disabled:cursor-not-allowed disabled:border-[color:rgba(120,104,89,0.14)] disabled:bg-[linear-gradient(180deg,rgba(167,174,167,0.42),rgba(131,137,131,0.52))] disabled:text-[color:rgba(251,248,242,0.78)] disabled:shadow-none`}
          >
            Continue
          </button>
        </div>
      </SettingsModalShell>
      <SettingsModalShell
        open={isResetPasswordOpen}
        themeMode={themeMode}
        maxWidthClass="max-w-[460px]"
      >
        {isResetPasswordSent ? (
          <>
            <div className="space-y-2">
              <h2 className="text-[1.25rem] font-medium tracking-tight text-[var(--workspace-text)]">
                Reset password
              </h2>
              <p className="text-[0.9rem] leading-7 text-[var(--workspace-text-soft)]">
                Reset link sent.
              </p>
            </div>

            <div className="mt-6 flex items-center justify-end">
              <button
                type="button"
                onClick={handleCloseResetPassword}
                className={settingsPrimaryActionClass}
              >
                Done
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="space-y-2">
              <h2 className="text-[1.25rem] font-medium tracking-tight text-[var(--workspace-text)]">
                Reset password
              </h2>
              <p className="text-[0.9rem] leading-7 text-[var(--workspace-text-soft)]">
                We&apos;ll send you a secure link to reset your password.
              </p>
            </div>

            <div className="mt-5 rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-3">
              <div className="text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                Account email
              </div>
              <div className="mt-1 text-[0.92rem] font-medium text-[var(--workspace-text)]">
                {savedEmail}
              </div>
            </div>

            <div className="mt-6 flex items-center justify-end gap-3">
              <button
                type="button"
                onClick={handleCloseResetPassword}
                className={settingsPairedSecondaryActionClass}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  setIsResetPasswordSent(true);
                  console.log("settings_send_reset_password_link");
                }}
                className={settingsPrimaryActionClass}
              >
                Send reset link
              </button>
            </div>
          </>
        )}
      </SettingsModalShell>
      <SettingsModalShell
        open={isManagePlanOpen}
        themeMode={themeMode}
        maxWidthClass="max-w-[520px]"
      >
        <div className="space-y-2">
          <h2 className="text-[1.25rem] font-medium tracking-tight text-[var(--workspace-text)]">
            Manage plan
          </h2>
          <p className="text-[0.9rem] leading-7 text-[var(--workspace-text-soft)]">
            See available plans for your workspace.
          </p>
        </div>

        <div className="mt-6 space-y-3">
          {(["Single User", "Team", "Enterprise"] as const).map((plan) => {
            const current = plan === savedPlan;
            const selected = plan === draftPlan;

            return (
              <button
                key={plan}
                type="button"
                onClick={() => setDraftPlan(plan)}
                className={`w-full rounded-[20px] border px-4 py-4 text-left transition-[background-color,border-color,box-shadow,transform] duration-150 active:scale-[0.995] focus-visible:outline-none ${
                  current
                    ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] shadow-[inset_0_1px_0_rgba(255,255,255,0.12),0_6px_14px_rgba(118,170,112,0.08)]"
                    : selected
                      ? "border-[color:rgba(111,148,111,0.34)] bg-[color:rgba(243,238,229,0.92)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_18px_rgba(120,104,89,0.06)] dark:border-[var(--workspace-accent-border)] dark:bg-[var(--workspace-hover-surface)] dark:shadow-[inset_0_1px_0_rgba(255,255,255,0.06),0_8px_18px_rgba(0,0,0,0.16)]"
                      : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] hover:border-[color:rgba(120,104,89,0.18)] hover:bg-[color:rgba(245,238,229,0.76)] hover:shadow-[inset_0_1px_0_rgba(255,255,255,0.12),0_8px_18px_rgba(120,104,89,0.06)] dark:hover:border-[var(--workspace-border)] dark:hover:bg-[var(--workspace-hover-surface)]"
                }`}
              >
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <div
                      className={`text-[0.94rem] font-medium ${
                        current
                          ? "text-[var(--workspace-accent-text)]"
                          : selected
                            ? "text-[var(--workspace-text)]"
                          : "text-[var(--workspace-text)]"
                      }`}
                    >
                      {plan}
                    </div>
                    <div
                      className={`mt-1 text-[0.82rem] ${
                        current
                          ? "text-[var(--workspace-accent-text)]/80"
                          : selected
                            ? "text-[var(--workspace-text-muted)]"
                          : "text-[var(--workspace-text-muted)]"
                      }`}
                    >
                      {plan === "Single User"
                        ? "Best for one workspace owner."
                        : plan === "Team"
                          ? "Shared access and collaboration for growing teams."
                          : "Custom support and controls for larger organizations."}
                    </div>
                  </div>
                  {current ? (
                    <span className="rounded-full border border-[var(--workspace-status-success-border)] bg-[var(--workspace-status-success-bg)] px-3 py-1 text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-status-success-text)]">
                      Current
                    </span>
                  ) : selected ? (
                    <span className="rounded-full border border-[var(--workspace-border)] bg-[var(--workspace-card)] px-3 py-1 text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-soft)] dark:border-[var(--workspace-border-hover)] dark:bg-[var(--workspace-card-subtle)] dark:text-[var(--workspace-text)]">
                      Selected
                    </span>
                  ) : null}
                </div>
              </button>
            );
          })}
        </div>

        {draftPlan !== savedPlan ? (
          <div className="mt-6 flex items-center justify-end gap-3">
            <button
              type="button"
              onClick={() => setDraftPlan(savedPlan)}
              className={settingsPairedSecondaryActionClass}
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => {
                setSavedPlan(draftPlan);
                setIsManagePlanOpen(false);
                console.log("settings_change_plan");
              }}
              className={settingsPrimaryActionClass}
            >
              Continue
            </button>
          </div>
        ) : (
          <div className="mt-6 flex items-center justify-end">
            <button
              type="button"
              onClick={handleCloseManagePlan}
              className={settingsSubtleActionClass}
            >
              Close
            </button>
          </div>
        )}
      </SettingsModalShell>
    </section>
  );
});

function SettingsView({
  workspaceName,
  savedManagedInboxes,
  themeMode,
  workspaceMode,
  inboxSignatures,
  inboxOutOfOffice,
  onChangeWorkspaceMode,
  aiSuggestionsEnabled,
  onToggleAiSuggestions,
  inboxChangesEnabled,
  onToggleInboxChanges,
  teamActivityEnabled,
  onToggleTeamActivity,
  onSaveWorkspaceName,
  onApplyManagedInboxes,
  onSaveInboxSignature,
  onSaveInboxOutOfOffice,
}: {
  workspaceName: string;
  savedManagedInboxes: ManagedWorkspaceInbox[];
  themeMode: "light" | "dark";
  workspaceMode: SettingsMode;
  inboxSignatures: InboxSignatureStore;
  inboxOutOfOffice: InboxOutOfOfficeStore;
  onChangeWorkspaceMode: (mode: SettingsMode) => void;
  aiSuggestionsEnabled: boolean;
  onToggleAiSuggestions: () => void;
  inboxChangesEnabled: boolean;
  onToggleInboxChanges: () => void;
  teamActivityEnabled: boolean;
  onToggleTeamActivity: () => void;
  onSaveWorkspaceName: (name: string) => void;
  onApplyManagedInboxes: (nextMailboxes: ManagedWorkspaceInbox[]) => boolean;
  onSaveInboxSignature: (inboxId: InboxId, signature: InboxSignatureSettings) => void;
  onSaveInboxOutOfOffice: (
    inboxId: InboxId,
    outOfOffice: InboxOutOfOfficeSettings,
  ) => void;
}) {
  const [settingsPage, setSettingsPage] = useState<"root" | "manage-inboxes">("root");
  const [activeSignatureInboxId, setActiveSignatureInboxId] = useState<string | null>(null);
  const [signatureDraft, setSignatureDraft] = useState<InboxSignatureSettings>(
    createEmptySignatureSettings(),
  );
  const [activeOutOfOfficeInboxId, setActiveOutOfOfficeInboxId] = useState<string | null>(null);
  const [outOfOfficeDraft, setOutOfOfficeDraft] = useState<InboxOutOfOfficeSettings>(
    normalizeInboxOutOfOfficeSettings(),
  );

  const activeSignatureInbox =
    activeSignatureInboxId === null
      ? null
      : savedManagedInboxes.find((mailbox) => mailbox.id === activeSignatureInboxId) ?? null;
  const activeOutOfOfficeInbox =
    activeOutOfOfficeInboxId === null
      ? null
      : savedManagedInboxes.find((mailbox) => mailbox.id === activeOutOfOfficeInboxId) ?? null;
  const outOfOfficeReuseOptions =
    activeOutOfOfficeInboxId === null
      ? []
      : savedManagedInboxes
          .filter((mailbox) => mailbox.id !== activeOutOfOfficeInboxId)
          .map((mailbox) => ({
            inboxEmail: mailbox.email,
            settings: normalizeInboxOutOfOfficeSettings(
              inboxOutOfOffice[mailbox.id as InboxId],
            ),
          }))
          .filter((entry) => entry.settings.message.trim().length > 0)
          .map((entry) => ({
            inboxEmail: entry.inboxEmail,
            message: entry.settings.message,
          }));

  useEffect(() => {
    if (!activeSignatureInboxId) {
      return;
    }

    const savedSignature = normalizeInboxSignatureSettings(
      inboxSignatures[activeSignatureInboxId as InboxId],
    );

    setSignatureDraft(savedSignature);
  }, [activeSignatureInboxId, inboxSignatures]);

  useEffect(() => {
    if (!activeOutOfOfficeInboxId) {
      return;
    }

    const savedOutOfOffice = normalizeInboxOutOfOfficeSettings(
      inboxOutOfOffice[activeOutOfOfficeInboxId as InboxId],
    );

    setOutOfOfficeDraft(savedOutOfOffice);
  }, [activeOutOfOfficeInboxId, inboxOutOfOffice]);

  if (settingsPage === "manage-inboxes") {
    return (
      <ManageInboxesView
        savedManagedInboxes={savedManagedInboxes}
        onBack={() => setSettingsPage("root")}
        onApply={onApplyManagedInboxes}
        themeMode={themeMode}
      />
    );
  }

  return (
    <div className={settingsPageSurfaceClass(themeMode)}>
      <header className="space-y-3">
        <h1 className="text-[1.85rem] font-medium tracking-tight text-[var(--workspace-text)] md:text-[2.25rem]">
          Settings
        </h1>
        <p className="max-w-3xl text-lg leading-8 text-[var(--workspace-text-muted)]">
          Control how your workspace feels and behaves.
        </p>
      </header>

      <div className="grid gap-6 xl:grid-cols-2 xl:items-start">
        <div className="space-y-6">
          <WorkspaceSettingsCard
            savedWorkspaceName={workspaceName}
            managedInboxCount={savedManagedInboxes.length}
            themeMode={themeMode}
            appliedMode={workspaceMode}
            aiSuggestionsEnabled={aiSuggestionsEnabled}
            onToggleAiSuggestions={onToggleAiSuggestions}
            onChangeMode={onChangeWorkspaceMode}
            onSaveWorkspaceName={onSaveWorkspaceName}
            onManageInboxes={() => setSettingsPage("manage-inboxes")}
          />
          <AccountSettingsCard themeMode={themeMode} />
        </div>
        <div className="space-y-6">
          <MailSettingsCard
            managedInboxes={savedManagedInboxes}
            inboxOutOfOffice={inboxOutOfOffice}
            themeMode={themeMode}
            onManageSignature={(mailbox) => {
              setActiveSignatureInboxId(mailbox.id);
            }}
            onManageOutOfOffice={(mailbox) => {
              setActiveOutOfOfficeInboxId(mailbox.id);
            }}
          />
          <NotificationsSettingsCard
            themeMode={themeMode}
            inboxChangesEnabled={inboxChangesEnabled}
            onToggleInboxChanges={onToggleInboxChanges}
            teamActivityEnabled={teamActivityEnabled}
            onToggleTeamActivity={onToggleTeamActivity}
          />
        </div>
      </div>

      <SignatureSettingsModal
        open={Boolean(activeSignatureInbox)}
        themeMode={themeMode}
        inboxEmail={activeSignatureInbox?.email ?? ""}
        signature={signatureDraft}
        onChangeSignatureHtml={(nextValue) =>
          setSignatureDraft((current) => ({
            ...current,
            html: nextValue,
          }))
        }
        onChangeUseByDefault={(nextValue) =>
          setSignatureDraft((current) => ({
            ...current,
            useByDefault: nextValue,
          }))
        }
        onChangeLayout={(nextValue) =>
          setSignatureDraft((current) => ({
            ...current,
            layout: nextValue,
          }))
        }
        onChangeShowDivider={(nextValue) =>
          setSignatureDraft((current) => ({
            ...current,
            showDivider: nextValue,
          }))
        }
        onChangeLogoImageUrl={(nextValue) =>
          setSignatureDraft((current) => ({
            ...current,
            logoImageUrl: nextValue,
          }))
        }
        onCancel={() => setActiveSignatureInboxId(null)}
        onSave={() => {
          if (!activeSignatureInboxId) {
            return;
          }

          onSaveInboxSignature(activeSignatureInboxId as InboxId, {
            ...signatureDraft,
            html: sanitizeSignatureHtml(signatureDraft.html),
          });
          setActiveSignatureInboxId(null);
        }}
      />
      <OutOfOfficeSettingsModal
        open={Boolean(activeOutOfOfficeInbox)}
        themeMode={themeMode}
        inboxEmail={activeOutOfOfficeInbox?.email ?? ""}
        outOfOffice={outOfOfficeDraft}
        reuseOptions={outOfOfficeReuseOptions}
        onChangeEnabled={(nextValue) =>
          setOutOfOfficeDraft((current) => ({
            ...current,
            enabled: nextValue,
          }))
        }
        onChangeMessage={(nextValue) =>
          setOutOfOfficeDraft((current) => ({
            ...current,
            message: nextValue,
          }))
        }
        onReuseMessage={(message) =>
          setOutOfOfficeDraft((current) => ({
            ...current,
            enabled: true,
            message,
          }))
        }
        onCancel={() => setActiveOutOfOfficeInboxId(null)}
        onSave={() => {
          if (!activeOutOfOfficeInboxId) {
            return;
          }

          onSaveInboxOutOfOffice(activeOutOfOfficeInboxId as InboxId, {
            enabled: outOfOfficeDraft.enabled,
            message: outOfOfficeDraft.message,
          });
          setActiveOutOfOfficeInboxId(null);
        }}
      />
    </div>
  );
}

function UtilityView({
  section,
  lastViewedGuidance,
  onSetLastViewedGuidance,
  primaryWorkspaceEmail,
}: {
  section: UtilitySection;
  lastViewedGuidance: string | null;
  onSetLastViewedGuidance: (item: string) => void;
  primaryWorkspaceEmail: string;
}) {
  const [helpSuggestionsVisible, setHelpSuggestionsVisible] = useState(false);
  const [selectedHelpSuggestion, setSelectedHelpSuggestion] = useState<string | null>(
    null,
  );
  const [contactSubject, setContactSubject] = useState("");
  const [contactMessage, setContactMessage] = useState("");
  const [contactRequestSent, setContactRequestSent] = useState(false);
  const [contactTickets, setContactTickets] = useState<ContactTicket[]>(contactMockTickets);
  const [activeContactTicketId, setActiveContactTicketId] = useState<string | null>(null);
  const [contactReplyDraft, setContactReplyDraft] = useState("");
  const [isContactCancelConfirmOpen, setIsContactCancelConfirmOpen] = useState(false);
  const content: Record<
    UtilitySection,
    {
      eyebrow: string;
      title: string;
      summary: string;
      items: string[];
    }
  > = {
    Help: {
      eyebrow: "Support guidance",
      title: "Help",
      summary:
        "Find product guidance, usage help, and operational pointers without breaking out of the workspace context.",
      items: [
        "Workflow guidance is available close to where work happens",
        "Support references stay accessible from the utility area",
        "Help content opens as a calm continuation of the shell",
      ],
    },
    Contact: {
      eyebrow: "Contact",
      title: "Contact",
      summary:
        "Reach the Cuevion team for support, setup questions, or workspace assistance from within the existing shell.",
      items: [
        "Product support responds to workspace setup questions",
        "Operational issues can be routed without leaving the UI",
        "Contact stays available as a stable utility destination",
      ],
    },
  };

  const view = content[section];
  const helpSearchFieldClass = `${inputFieldClass} border-[color:rgba(121,151,120,0.2)] bg-[color:rgba(255,253,249,0.97)] px-4 py-[0.92rem] shadow-none placeholder:text-[color:rgba(31,42,36,0.3)] focus:border-[color:rgba(93,126,94,0.44)] focus:bg-[color:rgba(255,253,249,0.995)] focus:shadow-[0_0_0_4px_rgba(93,126,94,0.1)] transition-[background-color,border-color,box-shadow,color]`;
  const helpSuggestions = [
    "How Cuevion organizes your inbox",
    "Understanding AI suggestions",
    "Managing your inbox settings",
    "Fixing sync or connection issues",
  ];
  const helpGuidanceCardClass =
    "rounded-[20px] border border-[var(--workspace-help-guidance-border)] bg-[var(--workspace-help-guidance-surface)] px-4 py-2.5";
  const helpGuidanceButtonClass =
    "w-full rounded-[14px] px-1 py-1.5 text-left text-[var(--workspace-help-guidance-text)] transition-[background-color,color] duration-150 hover:bg-[var(--workspace-help-guidance-hover)] focus:bg-[var(--workspace-help-guidance-hover)] focus:outline-none";
  const helpGuidanceSelectedCardClass =
    "rounded-[20px] border border-[var(--workspace-help-guidance-border)] bg-[var(--workspace-help-guidance-surface-selected)] px-5 py-4";
  const fallbackHelpSuggestions = helpSuggestions.slice(0, 3);
  const helpSuggestionDetails: Record<
    string,
    {
      intro: string;
      points: string[];
    }
  > = {
    "How Cuevion organizes your inbox": {
      intro:
        "Cuevion keeps the workspace readable by grouping incoming conversations into clear working lanes.",
      points: [
        "Primary keeps active, time-sensitive threads close at hand.",
        "Promo collects campaign and release-related outreach that needs structured handling.",
        "Other categories stay separated so your core inbox remains easier to scan.",
      ],
    },
    "Understanding AI suggestions": {
      intro:
        "AI suggestions are lightweight recommendations designed to support decisions, not replace your judgment.",
      points: [
        "Cuevion highlights patterns it detects across incoming messages and past decisions.",
        "Suggestions stay contextual so you can compare them against the thread before acting.",
        "You remain in control of whether a suggestion should be applied or ignored.",
      ],
    },
    "Managing your inbox settings": {
      intro:
        "Inbox settings let you shape how each connected mailbox behaves inside the workspace.",
      points: [
        "Adjust naming and connection details from the Settings area.",
        "Control how inbox behavior and workspace preferences support your daily flow.",
      ],
    },
    "Fixing sync or connection issues": {
      intro:
        "Most connection issues can be resolved by checking mailbox details and verifying the current setup.",
      points: [
        "Check the saved inbox configuration and provider information.",
        "Confirm credentials or server details if sync stops behaving as expected.",
        "Use Help and Contact as a stable place to troubleshoot inside the workspace.",
      ],
    },
  };
  const contactStatusClassNames: Record<ContactTicketStatus, string> = {
    Open:
      "border-[color:rgba(118,170,112,0.26)] bg-[color:rgba(118,170,112,0.14)] text-[color:rgba(70,109,73,0.94)]",
    "In progress":
      "border-[color:rgba(184,163,120,0.24)] bg-[color:rgba(184,163,120,0.14)] text-[color:rgba(118,95,58,0.94)]",
    Resolved:
      "border-[color:rgba(121,151,120,0.18)] bg-[color:rgba(121,151,120,0.1)] text-[color:rgba(83,108,84,0.94)]",
    Cancelled:
      "border-[color:rgba(154,145,133,0.2)] bg-[color:rgba(154,145,133,0.12)] text-[color:rgba(108,100,91,0.92)]",
  };
  const isContactRequestReady =
    contactSubject.trim().length > 0 && contactMessage.trim().length > 0;
  const resetContactForm = () => {
    setContactSubject("");
    setContactMessage("");
  };
  const activeContactTicket =
    activeContactTicketId === null
      ? null
      : contactTickets.find((ticket) => ticket.id === activeContactTicketId) ?? null;
  const isContactReplyReady = contactReplyDraft.trim().length > 0;

  useEffect(() => {
    if (!contactRequestSent) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      setContactRequestSent(false);
    }, 3200);

    return () => window.clearTimeout(timeoutId);
  }, [contactRequestSent]);

  useEffect(() => {
    setContactReplyDraft("");
  }, [activeContactTicketId]);

  useEffect(() => {
    if (activeContactTicketId !== null) {
      return;
    }

    setIsContactCancelConfirmOpen(false);
  }, [activeContactTicketId]);

  if (section === "Contact") {
    return (
      <div className="space-y-8">
        <header className="space-y-3">
          <h1 className="text-[1.85rem] font-medium tracking-tight text-[var(--workspace-text)] md:text-[2.25rem]">
            {content.Contact.title}
          </h1>
          <p className="max-w-3xl text-lg leading-8 text-[var(--workspace-text-muted)]">
            {content.Contact.summary}
          </p>
        </header>

        <section className="rounded-[30px] border border-[var(--workspace-border)] bg-[var(--workspace-card)] p-6 shadow-panel">
          <div className="space-y-6">
            <div className="grid gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(300px,0.85fr)]">
              <form
                className="rounded-[22px] border border-[var(--workspace-border-soft)] bg-[linear-gradient(180deg,var(--workspace-card-featured-start),var(--workspace-card-featured-end))] p-5"
                onSubmit={(event) => {
                  event.preventDefault();

                  if (!isContactRequestReady) {
                    return;
                  }

                  const submittedSubject = contactSubject.trim();
                  const nextTicketId = `#${2400 + contactTickets.length + 1}`;
                  const updatedAt = new Date().toLocaleDateString("en-US", {
                    month: "long",
                    day: "numeric",
                    year: "numeric",
                  });

                  setContactTickets((current) => [
                    {
                      id: nextTicketId,
                      subject: submittedSubject,
                      status: "Open",
                      updatedAt,
                      messages: [
                        {
                          senderType: "user",
                          body: contactMessage.trim(),
                          timestamp: `${updatedAt} at ${new Date().toLocaleTimeString("en-US", {
                            hour: "numeric",
                            minute: "2-digit",
                          })}`,
                        },
                      ],
                    },
                    ...current,
                  ]);
                  resetContactForm();
                  setContactRequestSent(true);
                }}
              >
                <div className="mb-5 flex items-center justify-between gap-4">
                  <div>
                    <h2 className="text-xl font-semibold tracking-tight text-[var(--workspace-text)]">
                      Send a request
                    </h2>
                    <p className="mt-1 text-[0.92rem] leading-6 text-[var(--workspace-text-soft)]">
                      Your request is handled by the Cuevion team personally.
                    </p>
                  </div>
                </div>

                <div className="space-y-3">
                  <label className="grid grid-cols-[88px_minmax(0,1fr)] items-center gap-4 rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3">
                    <span className="text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                      Send from
                    </span>
                    <input
                      value={primaryWorkspaceEmail}
                      readOnly
                      className="w-full cursor-default bg-transparent text-[0.9rem] leading-6 text-[var(--workspace-text-soft)] outline-none"
                    />
                  </label>
                  <label className="grid grid-cols-[88px_minmax(0,1fr)] items-center gap-4 rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3">
                    <span className="text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                      Subject
                    </span>
                    <input
                      value={contactSubject}
                      onChange={(event) => {
                        setContactSubject(event.target.value);
                        setContactRequestSent(false);
                      }}
                      placeholder="Briefly describe your request"
                      className="w-full bg-transparent text-[0.9rem] leading-6 text-[var(--workspace-text-soft)] outline-none placeholder:text-[var(--workspace-text-faint)]"
                    />
                  </label>
                  <label className="block rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3">
                    <span className="text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                      Message
                    </span>
                    <textarea
                      value={contactMessage}
                      onChange={(event) => {
                        setContactMessage(event.target.value);
                        setContactRequestSent(false);
                      }}
                      placeholder="Write your request for the Cuevion team"
                      className="mt-3 block min-h-[220px] w-full resize-none bg-transparent text-[0.94rem] leading-7 text-[var(--workspace-text-soft)] outline-none placeholder:text-[var(--workspace-text-faint)]"
                    />
                  </label>
                </div>

                {contactRequestSent ? (
                  <div className="mt-4 rounded-[18px] border border-[color:rgba(121,151,120,0.14)] bg-[color:rgba(255,252,247,0.6)] px-4 py-3 text-[0.9rem] leading-6 text-[var(--workspace-text-soft)]">
                    Your request has been sent. The Cuevion team will reply from your workspace context.
                  </div>
                ) : null}

                <div className="mt-5 flex items-center justify-between gap-4">
                  <div className="max-w-[26rem] text-[0.88rem] leading-6 text-[var(--workspace-text-muted)]">
                    Messages from this workspace go directly to Cuevion so a person can respond with context.
                  </div>
                  <div className="flex items-center gap-3">
                    <button
                      type="button"
                      onClick={() => {
                        resetContactForm();
                        setContactRequestSent(false);
                      }}
                      className={subtleSecondaryActionButtonClass}
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      disabled={!isContactRequestReady}
                      className={`${mailboxPrimaryActionButtonClass} ${
                        isContactRequestReady
                          ? ""
                          : "cursor-default bg-[var(--workspace-card-subtle)] text-[var(--workspace-text-faint)] opacity-55 hover:bg-[var(--workspace-card-subtle)]"
                      }`}
                    >
                      Send request
                    </button>
                  </div>
                </div>
              </form>

              <div className="rounded-[22px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] p-5">
                <div className="mb-4">
                  <h2 className="text-xl font-semibold tracking-tight text-[var(--workspace-text)]">
                    Ticket overview
                  </h2>
                  <p className="mt-1 text-[0.9rem] leading-6 text-[var(--workspace-text-soft)]">
                    Recent contact history from this workspace.
                  </p>
                </div>
                <div className="max-h-[420px] space-y-2 overflow-y-auto pr-1">
                  {contactTickets.map((ticket) => (
                    <div
                      key={ticket.id}
                      role="button"
                      tabIndex={0}
                      onClick={() => setActiveContactTicketId(ticket.id)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          setActiveContactTicketId(ticket.id);
                        }
                      }}
                      className="cursor-pointer rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3 transition-colors duration-150 hover:bg-[var(--workspace-hover-surface)] focus:outline-none focus:bg-[var(--workspace-hover-surface)]"
                    >
                      <div className="flex items-start justify-between gap-4">
                        <div className="min-w-0">
                          <div className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                            {ticket.id}
                          </div>
                          <div className="mt-1 text-[0.92rem] leading-6 text-[var(--workspace-text-soft)]">
                            {ticket.subject}
                          </div>
                        </div>
                        <div
                          className={`rounded-full border px-3 py-1 text-[0.64rem] font-medium uppercase tracking-[0.14em] ${contactStatusClassNames[ticket.status]}`}
                        >
                          {ticket.status}
                        </div>
                      </div>
                      <div className="mt-2 text-[0.8rem] leading-5 text-[var(--workspace-text-faint)]">
                        Updated {ticket.updatedAt}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {activeContactTicket
              ? createPortal(
                  <WorkspaceModalLayer>
                    <div
                      className="w-full max-w-[860px] overflow-hidden rounded-[26px] border border-[var(--workspace-border)] bg-[var(--workspace-modal-bg)] p-6 shadow-[0_24px_70px_rgba(61,44,32,0.16),0_8px_20px_rgba(61,44,32,0.08)]"
                      onMouseDown={(event) => event.stopPropagation()}
                      onWheel={(event) => event.stopPropagation()}
                      onTouchMove={(event) => event.stopPropagation()}
                    >
                      <div className="flex items-start justify-between gap-4">
                        <div>
                          <div className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                            {activeContactTicket.id}
                          </div>
                          <h2 className="mt-2 text-[1.45rem] font-medium tracking-tight text-[var(--workspace-text)]">
                            {activeContactTicket.subject}
                          </h2>
                        </div>
                        <button
                          type="button"
                          onClick={() => setActiveContactTicketId(null)}
                          className={closeActionButtonClass}
                        >
                          Close
                        </button>
                      </div>

                      <div className="mt-5 flex flex-wrap items-center gap-3">
                        <div
                          className={`rounded-full border px-3 py-1 text-[0.64rem] font-medium uppercase tracking-[0.14em] ${contactStatusClassNames[activeContactTicket.status]}`}
                        >
                          {activeContactTicket.status}
                        </div>
                        <div className="text-[0.82rem] leading-6 text-[var(--workspace-text-faint)]">
                          Updated {activeContactTicket.updatedAt}
                        </div>
                      </div>

                      <div className="mt-6 rounded-[24px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] p-5">
                        <div className="mb-4 text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                          Conversation
                        </div>
                        <div className="space-y-3">
                          {activeContactTicket.messages.map((message, index) => (
                            <div
                              key={`${activeContactTicket.id}-${index}-${message.timestamp}`}
                              className={`rounded-[20px] border px-4 py-4 ${
                                message.senderType === "cuevion"
                                  ? "border-[color:rgba(121,151,120,0.14)] bg-[color:rgba(255,252,247,0.64)]"
                                  : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)]"
                              }`}
                            >
                              <div className="flex items-center justify-between gap-4">
                                <div
                                  className={`text-[0.72rem] font-medium uppercase tracking-[0.16em] ${
                                    message.senderType === "cuevion"
                                      ? "text-[var(--workspace-text-muted)]"
                                      : "text-[var(--workspace-text-faint)]"
                                  }`}
                                >
                                  {message.senderType === "cuevion" ? "Cuevion" : "You"}
                                </div>
                                <div className="text-[0.78rem] leading-5 text-[var(--workspace-text-faint)]">
                                  {message.timestamp}
                                </div>
                              </div>
                              <div className="mt-3 text-[0.94rem] leading-7 text-[var(--workspace-text-soft)]">
                                {message.body}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>

                      <div className="mt-6 rounded-[24px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] p-5">
                        <div className="mb-3 text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                          Reply
                        </div>
                        <label className="block rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3">
                          <textarea
                            value={contactReplyDraft}
                            onChange={(event) => setContactReplyDraft(event.target.value)}
                            placeholder="Reply to the Cuevion team..."
                            className="block min-h-[132px] w-full resize-none bg-transparent text-[0.92rem] leading-7 text-[var(--workspace-text-soft)] outline-none placeholder:text-[var(--workspace-text-faint)]"
                          />
                        </label>
                        <div className="mt-3 flex items-center justify-end gap-3">
                            {activeContactTicket.status === "Open" ? (
                              <button
                                type="button"
                                onClick={() => setIsContactCancelConfirmOpen(true)}
                                className={subtleSecondaryActionButtonClass}
                              >
                                Cancel request
                              </button>
                            ) : null}
                            <button
                              type="button"
                              disabled={!isContactReplyReady}
                              onClick={() => {
                                if (!isContactReplyReady) {
                                  return;
                                }

                                const updatedAt = new Date().toLocaleDateString("en-US", {
                                  month: "long",
                                  day: "numeric",
                                  year: "numeric",
                                });
                                const replyTimestamp = `${updatedAt} at ${new Date().toLocaleTimeString(
                                  "en-US",
                                  {
                                    hour: "numeric",
                                    minute: "2-digit",
                                  },
                                )}`;

                                setContactTickets((current) =>
                                  current.map((ticket) =>
                                    ticket.id === activeContactTicket.id
                                      ? {
                                          ...ticket,
                                          updatedAt,
                                          status:
                                            ticket.status === "Open"
                                              ? "In progress"
                                              : ticket.status,
                                          messages: [
                                            ...ticket.messages,
                                            {
                                              senderType: "user",
                                              body: contactReplyDraft.trim(),
                                              timestamp: replyTimestamp,
                                            },
                                          ],
                                        }
                                      : ticket,
                                  ),
                                );
                                setContactReplyDraft("");
                              }}
                              className={`${mailboxPrimaryActionButtonClass} ${
                                isContactReplyReady
                                  ? ""
                                  : "cursor-default bg-[var(--workspace-card-subtle)] text-[var(--workspace-text-faint)] opacity-55 hover:bg-[var(--workspace-card-subtle)]"
                              }`}
                            >
                              Reply
                            </button>
                        </div>
                      </div>
                    </div>
                  </WorkspaceModalLayer>,
                  document.body,
                )
              : null}

            {activeContactTicket && isContactCancelConfirmOpen
              ? createPortal(
                  <WorkspaceModalLayer>
                    <div
                      className="w-full max-w-[420px] overflow-hidden rounded-[26px] border border-[var(--workspace-border)] bg-[var(--workspace-modal-bg)] p-6 shadow-[0_24px_70px_rgba(61,44,32,0.16),0_8px_20px_rgba(61,44,32,0.08)]"
                      onMouseDown={(event) => event.stopPropagation()}
                      onWheel={(event) => event.stopPropagation()}
                      onTouchMove={(event) => event.stopPropagation()}
                    >
                      <div className="space-y-2">
                        <h2 className="text-[1.25rem] font-medium tracking-tight text-[var(--workspace-text)]">
                          Cancel this request?
                        </h2>
                        <p className="text-[0.9rem] leading-7 text-[var(--workspace-text-soft)]">
                          The Cuevion team will no longer respond to this thread.
                        </p>
                      </div>

                      <div className="mt-6 flex items-center justify-end gap-3">
                        <button
                          type="button"
                          onClick={() => setIsContactCancelConfirmOpen(false)}
                          className={subtleSecondaryActionButtonClass}
                        >
                          Keep request
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setContactTickets((current) =>
                              current.map((ticket) =>
                                ticket.id === activeContactTicket.id
                                  ? { ...ticket, status: "Cancelled" }
                                  : ticket,
                              ),
                            );
                            setIsContactCancelConfirmOpen(false);
                          }}
                          className={mailboxPrimaryActionButtonClass}
                        >
                          Confirm cancel
                        </button>
                      </div>
                    </div>
                  </WorkspaceModalLayer>,
                  document.body,
                )
              : null}

            <div className="rounded-[22px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-5 py-5">
              <div className="mb-4 flex items-center justify-between gap-4">
                <h2 className="text-xl font-semibold tracking-tight text-[var(--workspace-text)]">
                  Contact details
                </h2>
                <div className="h-2 w-14 rounded-full bg-[var(--workspace-accent-soft)]" />
              </div>
              <div className="grid gap-5 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)]">
                <div className="space-y-3">
                  <div className="text-[1rem] font-medium tracking-[-0.014em] text-[var(--workspace-text)]">
                    Cuevion B.V.
                  </div>
                  <div className="space-y-1 text-[0.94rem] leading-7 text-[var(--workspace-text-soft)]">
                    <div>Herengracht 482</div>
                    <div>1017 CB Amsterdam</div>
                    <div>The Netherlands</div>
                  </div>
                  <div className="pt-1 text-[0.94rem] leading-7 text-[var(--workspace-text-soft)]">
                    hello@cuevion.com
                  </div>
                </div>
                <div className="rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-4">
                  <div className="mb-2 text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                    Business details
                  </div>
                  <div className="space-y-1 text-[0.9rem] leading-6 text-[var(--workspace-text-soft)]">
                    <div>KvK: 00000000</div>
                    <div>VAT: NL000000000B00</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <header className="space-y-3">
        <div className="text-[0.72rem] font-medium uppercase tracking-[0.24em] text-[var(--workspace-text-faint)]">
          {view.eyebrow}
        </div>
        <h1 className="text-[1.85rem] font-medium tracking-tight text-[var(--workspace-text)] md:text-[2.25rem]">
          {view.title}
        </h1>
        <p className="max-w-3xl text-lg leading-8 text-[var(--workspace-text-muted)]">
          {view.summary}
        </p>
        {section === "Help" ? (
          <div
            className="max-w-3xl pt-2"
            onBlur={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                setHelpSuggestionsVisible(false);
              }
            }}
          >
            <input
              type="text"
              placeholder="Search for guidance, workflows, or questions"
              className={helpSearchFieldClass}
              onFocus={() => {
                setHelpSuggestionsVisible(true);
                setSelectedHelpSuggestion(null);
              }}
            />
            {helpSuggestionsVisible && !selectedHelpSuggestion ? (
              <div className="mt-1.5 rounded-[20px] rounded-t-[14px] border border-[color:rgba(121,151,120,0.1)] bg-[color:rgba(255,252,247,0.72)] p-2">
                {lastViewedGuidance ? (
                  <>
                    <div className="px-4 pb-1 pt-2 text-[0.72rem] font-medium tracking-[0.02em] text-[var(--workspace-text-faint)]">
                      Continue where you left off
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        setSelectedHelpSuggestion(lastViewedGuidance);
                        onSetLastViewedGuidance(lastViewedGuidance);
                        setHelpSuggestionsVisible(false);
                      }}
                      className="w-full rounded-[16px] px-4 py-3 text-left text-[0.94rem] leading-6 text-[var(--workspace-text-soft)] transition-colors duration-150 hover:bg-[color:rgba(121,151,120,0.045)] focus:bg-[color:rgba(121,151,120,0.045)] focus:outline-none"
                    >
                      {lastViewedGuidance}
                    </button>
                  </>
                ) : (
                  fallbackHelpSuggestions.map((item) => (
                    <button
                      key={item}
                      type="button"
                      onClick={() => {
                        setSelectedHelpSuggestion(item);
                        onSetLastViewedGuidance(item);
                        setHelpSuggestionsVisible(false);
                      }}
                      className="w-full rounded-[16px] px-4 py-3 text-left text-[0.94rem] leading-6 text-[var(--workspace-text-soft)] transition-colors duration-150 hover:bg-[color:rgba(121,151,120,0.045)] focus:bg-[color:rgba(121,151,120,0.045)] focus:outline-none"
                    >
                      {item}
                    </button>
                  ))
                )}
              </div>
            ) : null}
          </div>
        ) : null}
      </header>

      <section className="rounded-[30px] border border-[var(--workspace-border)] bg-[var(--workspace-card)] p-6 shadow-panel">
        <div className="mb-5 flex items-center justify-between">
          <h2 className="text-xl font-semibold tracking-tight text-[var(--workspace-text)]">
            {selectedHelpSuggestion ? "Selected guidance" : "Common guidance"}
          </h2>
          {selectedHelpSuggestion ? (
            <button
              type="button"
              onClick={() => {
                setSelectedHelpSuggestion(null);
                setHelpSuggestionsVisible(false);
              }}
              className="cursor-pointer text-[0.78rem] font-medium text-[var(--workspace-accent-primary)] underline-offset-4 transition-[color,text-decoration-color] duration-150 hover:text-[color:rgba(76,132,94,0.98)] hover:underline focus:outline-none focus:text-[color:rgba(76,132,94,0.98)] focus:underline"
            >
              Clear
            </button>
          ) : (
            <div className="h-2 w-14 rounded-full bg-[var(--workspace-help-guidance-accent)]" />
          )}
        </div>
        {selectedHelpSuggestion ? (
          <div className={helpGuidanceSelectedCardClass}>
            <div className="text-[0.94rem] font-medium leading-6 text-[var(--workspace-text)]">
              {selectedHelpSuggestion}
            </div>
            <p className="mt-2 text-[0.92rem] leading-6 text-[var(--workspace-text-soft)]">
              {helpSuggestionDetails[selectedHelpSuggestion].intro}
            </p>
            <ul className="mt-2.5 space-y-2 text-[0.9rem] leading-6 text-[var(--workspace-text-muted)]">
              {helpSuggestionDetails[selectedHelpSuggestion].points.map((point) => (
                <li key={point} className="flex gap-3">
                  <span className="pt-[0.5rem] text-[0.5rem] text-[var(--workspace-text-faint)]">
                    •
                  </span>
                  <span>{point}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <div className="space-y-2">
            {helpSuggestions.map((item) => (
              <div
                key={item}
                className={helpGuidanceCardClass}
              >
                <button
                  type="button"
                  onClick={() => {
                    setSelectedHelpSuggestion(item);
                    setHelpSuggestionsVisible(false);
                    onSetLastViewedGuidance(item);
                  }}
                  className={helpGuidanceButtonClass}
                >
                  <div className="text-[0.92rem] leading-6 text-[var(--workspace-help-guidance-text)]">
                    {item}
                  </div>
                </button>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function ForYouView({
  context = "Main",
  onOpenTarget,
  onSaveLearningRule,
  senderCategoryLearning,
  mailboxStore,
  orderedMailboxes,
  modalHost,
  learningLaunchRequest,
  onConsumeLearningLaunchRequest,
  aiSuggestionsEnabled,
}: {
  context?: ForYouContext;
  onOpenTarget: (target: WorkspaceTarget) => void;
  onSaveLearningRule: (
    ruleValue: string,
    ruleType: "sender" | "domain",
    category: CuevionMessageCategory,
    mailboxAction?: "keep" | "move",
    options?: {
      sourceContext?: LearningDecisionSourceContext;
      sourcePrioritySelection?: LearningDecisionPrioritySelection | null;
      sourceMailboxId?: InboxId | null;
      sourceCurrentMailboxId?: InboxId | null;
    },
  ) => void;
  senderCategoryLearning: SenderCategoryLearningStore;
  mailboxStore: MailboxStore;
  orderedMailboxes: OrderedMailbox[];
  modalHost: HTMLElement | null;
  learningLaunchRequest: LearningLaunchRequest;
  onConsumeLearningLaunchRequest: () => void;
  aiSuggestionsEnabled: boolean;
}) {
  const [activeLearningModal, setActiveLearningModal] = useState<
    | "paste-rule"
    | "refine-cuevion"
    | "review-uncertain"
    | "recent-decisions"
    | "edit-recent-decision"
    | null
  >(null);
  const [activeLearningSuggestionIndex, setActiveLearningSuggestionIndex] = useState(0);
  const [activeUncertainEmailIndex, setActiveUncertainEmailIndex] = useState(0);
  const [activeRecentDecisionIndex, setActiveRecentDecisionIndex] = useState(0);
  const [activeLearningSessionSuggestions, setActiveLearningSessionSuggestions] = useState<
    ForYouLearningSuggestion[]
  >([]);
  const [reviewedLearningSuggestionKeys, setReviewedLearningSuggestionKeys] = useState<
    string[]
  >([]);
  const [selectedLearningPriority, setSelectedLearningPriority] = useState<
    "Important" | "Normal" | "Show Less" | "Spam"
  | null>(null);
  const [selectedLearningMailboxId, setSelectedLearningMailboxId] = useState<InboxId | null>(
    orderedMailboxes[0]?.id ?? null,
  );
  const [pastedRuleValue, setPastedRuleValue] = useState("");
  const [pasteRuleSaveFeedback, setPasteRuleSaveFeedback] = useState<"idle" | "saved">(
    "idle",
  );
  const [selectedPasteRulePriority, setSelectedPasteRulePriority] = useState<
    "Important" | "Normal" | "Show Less" | "Spam" | null
  >(null);
  const [selectedPasteRuleMailboxId, setSelectedPasteRuleMailboxId] = useState<InboxId | null>(
    orderedMailboxes[0]?.id ?? null,
  );
  const [selectedUncertainMailboxId, setSelectedUncertainMailboxId] = useState<InboxId | null>(
    null,
  );
  const [isUncertainMovePickerOpen, setIsUncertainMovePickerOpen] = useState(false);
  const [selectedRecentDecisionCategory, setSelectedRecentDecisionCategory] = useState<
    "Important" | "Review" | "Promo" | "Demo" | "Spam" | null
  >(null);
  const [selectedRecentDecisionInboxAction, setSelectedRecentDecisionInboxAction] =
    useState<"keep" | "move" | null>("keep");
  const [selectedRecentDecisionPriority, setSelectedRecentDecisionPriority] = useState<
    LearningDecisionPrioritySelection | null
  >(null);
  const [selectedRecentDecisionMailboxId, setSelectedRecentDecisionMailboxId] = useState<
    InboxId | null
  >(orderedMailboxes[0]?.id ?? null);
  const [selectedRecentDecisionUncertainMailboxId, setSelectedRecentDecisionUncertainMailboxId] =
    useState<InboxId | null>(null);
  const [isRecentDecisionMovePickerOpen, setIsRecentDecisionMovePickerOpen] = useState(false);
  const pasteRuleSaveTimeoutRef = useRef<number | null>(null);
  const reviewUncertainCompletionTimeoutRef = useRef<number | null>(null);
  const [reviewUncertainCompletionFeedback, setReviewUncertainCompletionFeedback] =
    useState<"idle" | "done">("idle");
  const closeLearningModal = () => {
    if (pasteRuleSaveTimeoutRef.current !== null) {
      window.clearTimeout(pasteRuleSaveTimeoutRef.current);
      pasteRuleSaveTimeoutRef.current = null;
    }
    if (reviewUncertainCompletionTimeoutRef.current !== null) {
      window.clearTimeout(reviewUncertainCompletionTimeoutRef.current);
      reviewUncertainCompletionTimeoutRef.current = null;
    }
    setPasteRuleSaveFeedback("idle");
    setReviewUncertainCompletionFeedback("idle");
    setActiveLearningSessionSuggestions([]);
    setActiveLearningModal(null);
  };
  const openReviewUncertainModal = () => {
    setActiveUncertainEmailIndex(0);
    setSelectedUncertainMailboxId(null);
    setIsUncertainMovePickerOpen(false);
    setReviewUncertainCompletionFeedback("idle");
    setActiveLearningModal("review-uncertain");
  };
  const openRefineCuevionModal = () => {
    setActiveLearningSuggestionIndex(0);
    setActiveLearningSessionSuggestions(pendingLearningSuggestions.slice(0, learningBatchSize));
    setActiveLearningModal("refine-cuevion");
  };
  const teachCuevionActions = [
    {
      title: "Paste sender or domain",
      subtitle:
        "Train how Cuevion should treat future emails from a sender or domain",
      handler: () => setActiveLearningModal("paste-rule"),
    },
    {
      title: "Check uncertain emails",
      subtitle: "Resolve messages Cuevion is not fully sure about",
      handler: openReviewUncertainModal,
    },
    {
      title: "Recent learning decisions",
      subtitle: "Revisit recent choices Cuevion learned from.",
      handler: () => setActiveLearningModal("recent-decisions"),
    },
  ];
  const { learningSuggestionPool, uncertainEmailPool } = buildForYouLearningPools(
    mailboxStore,
    orderedMailboxes,
  );
  const learningBatchSize = 10;
  const pendingLearningSuggestions = learningSuggestionPool.filter(
    (suggestion) => !reviewedLearningSuggestionKeys.includes(suggestion.key),
  );
  const activeLearningSuggestions = activeLearningSessionSuggestions.filter(
    (suggestion) => !reviewedLearningSuggestionKeys.includes(suggestion.key),
  );
  const activeLearningSuggestion =
    activeLearningSuggestions[activeLearningSuggestionIndex] ??
    activeLearningSuggestions[0];
  const learningMailboxOptions = orderedMailboxes.map((mailbox) => ({
    id: mailbox.id,
    label: mailbox.title,
  }));
  const uncertainDestinationOptions = orderedMailboxes.map((mailbox) => ({
    id: mailbox.id,
    label: mailbox.id === "main" ? "Inbox" : mailbox.title,
  }));
  const trimmedPastedRuleValue = pastedRuleValue.trim();
  const pasteRuleInputType = resolvePasteRuleInputType(trimmedPastedRuleValue);
  const pasteRuleType =
    pasteRuleInputType === "sender" || pasteRuleInputType === "domain"
      ? pasteRuleInputType
      : null;
  const pasteRuleValidationMessage =
    trimmedPastedRuleValue.length > 0 && pasteRuleInputType === "invalid"
      ? "Enter a valid email or domain"
      : null;
  const canSavePasteRule =
    pasteRuleType !== null &&
    selectedPasteRulePriority !== null &&
    (selectedPasteRulePriority === "Spam" || selectedPasteRuleMailboxId !== null);
  const resolvedPasteRuleCategory: CuevionMessageCategory | null =
    selectedPasteRulePriority === null
      ? null
      : selectedPasteRulePriority === "Spam" || selectedPasteRulePriority === "Show Less"
        ? "Updates"
        : selectedPasteRuleMailboxId === "promo"
          ? "Promo"
          : "Primary";
  const persistActiveLearningSuggestionDecision = () => {
    if (!activeLearningSuggestion || !selectedLearningPriority) {
      return false;
    }

    const category =
      selectedLearningPriority === "Spam" || selectedLearningPriority === "Show Less"
        ? "Updates"
        : selectedLearningMailboxId === "promo"
          ? "Promo"
          : "Primary";

    onSaveLearningRule(
      activeLearningSuggestion.senderAddress,
      "sender",
      category,
      resolveMailboxActionFromForYouSelection(
        selectedLearningPriority,
        category,
        category === "Primary" ? "keep" : "move",
      ),
      {
        sourceContext: "refine",
        sourcePrioritySelection: selectedLearningPriority,
        sourceMailboxId:
          selectedLearningPriority === "Spam" ? null : selectedLearningMailboxId,
      },
    );
    setReviewedLearningSuggestionKeys((current) =>
      current.includes(activeLearningSuggestion.key)
        ? current
        : [...current, activeLearningSuggestion.key],
    );

    return true;
  };
  const persistActiveUncertainDecision = () => {
    if (!activeUncertainEmail || !selectedUncertainMailboxId) {
      return false;
    }

    const category = resolveCuevionCategoryFromMailboxId(selectedUncertainMailboxId);
    const mailboxAction = resolveMailboxActionFromMailboxId(selectedUncertainMailboxId);

    onSaveLearningRule(
      activeUncertainEmail.senderAddress,
      "sender",
      category,
      mailboxAction,
      {
        sourceContext: "uncertain",
        sourceMailboxId: selectedUncertainMailboxId,
        sourceCurrentMailboxId: activeUncertainEmail.mailboxId,
      },
    );

    return true;
  };

  const totalLearningSuggestions = activeLearningSessionSuggestions.length;
  const completedLearningSuggestionsCount = activeLearningSessionSuggestions.filter((suggestion) =>
    reviewedLearningSuggestionKeys.includes(suggestion.key),
  ).length;
  const safeLearningSuggestionIndex =
    activeLearningSuggestions.length === 0
      ? 0
      : Math.min(activeLearningSuggestionIndex, activeLearningSuggestions.length - 1);
  const currentLearningSuggestionNumber =
    activeLearningSuggestions.length === 0
      ? 0
      : completedLearningSuggestionsCount + safeLearningSuggestionIndex + 1;
  const isLastLearningSuggestion = activeLearningSuggestions.length === 1;
  const hasValidLearningSelection = selectedLearningPriority !== null;
  const hasPendingLearningSuggestions = pendingLearningSuggestions.length > 0;
  const totalUncertainEmails = uncertainEmailPool.length;
  const safeUncertainEmailIndex =
    totalUncertainEmails === 0
      ? 0
      : Math.min(activeUncertainEmailIndex, totalUncertainEmails - 1);
  const activeUncertainEmail = uncertainEmailPool[safeUncertainEmailIndex];
  const isLastUncertainEmail = safeUncertainEmailIndex === totalUncertainEmails - 1;
  const hasValidUncertainSelection = selectedUncertainMailboxId !== null;
  const recentLearningDecisions = Object.entries(senderCategoryLearning)
    .map(([learningKey, entry]) => ({
      key: learningKey,
      sender: formatLearningRuleLabel(learningKey),
      action: formatLearningRuleAction(entry),
      timestamp: formatLearningRuleTimestamp(entry.updatedAt),
      ruleType: learningKey.startsWith("domain:") ? ("domain" as const) : ("sender" as const),
      ruleValue: learningKey.startsWith("domain:")
        ? learningKey.replace("domain:", "")
        : learningKey,
      learnedCategory: entry.learnedCategory,
      mailboxAction: entry.mailboxAction ?? (entry.learnedCategory === "Primary" ? "keep" : "move"),
      sourceContext: inferLearningDecisionSourceContext(
        entry,
        learningKey.startsWith("domain:") ? "domain" : "sender",
      ),
      sourcePrioritySelection: inferLearningDecisionPrioritySelection(entry),
      sourceMailboxId: inferLearningDecisionMailboxId(entry),
      sourceCurrentMailboxId: entry.sourceCurrentMailboxId ?? null,
      updatedAt: entry.updatedAt,
    }))
    .sort((firstDecision, secondDecision) => {
      const firstTime = firstDecision.updatedAt ? new Date(firstDecision.updatedAt).getTime() : 0;
      const secondTime = secondDecision.updatedAt
        ? new Date(secondDecision.updatedAt).getTime()
        : 0;

      return secondTime - firstTime;
    });
  const activeRecentDecision = recentLearningDecisions[activeRecentDecisionIndex];
  const activeRecentDecisionCurrentMailboxLabel =
    activeRecentDecision?.sourceCurrentMailboxId === "main"
      ? "Inbox"
      : orderedMailboxes.find(
          (mailbox) => mailbox.id === activeRecentDecision?.sourceCurrentMailboxId,
        )?.title ?? "Inbox";
  const initializeRecentDecisionEditor = (
    decision:
      | (typeof recentLearningDecisions)[number]
      | undefined,
  ) => {
    if (!decision) {
      setSelectedRecentDecisionCategory(null);
      setSelectedRecentDecisionInboxAction("keep");
      setSelectedRecentDecisionPriority(null);
      setSelectedRecentDecisionMailboxId(orderedMailboxes[0]?.id ?? null);
      setSelectedRecentDecisionUncertainMailboxId(null);
      setIsRecentDecisionMovePickerOpen(false);
      return;
    }

    setSelectedRecentDecisionPriority(decision.sourcePrioritySelection);
    setSelectedRecentDecisionMailboxId(
      decision.sourceMailboxId ?? orderedMailboxes[0]?.id ?? null,
    );
    setSelectedRecentDecisionUncertainMailboxId(decision.sourceMailboxId ?? null);
    setIsRecentDecisionMovePickerOpen(
      decision.sourceContext === "uncertain" &&
        decision.sourceMailboxId !== (decision.sourceCurrentMailboxId ?? null),
    );
    setSelectedRecentDecisionCategory(
      resolveForYouCategoryFromLearningEntry({
        learnedCategory: decision.learnedCategory,
        learnedFromCount: 0,
        mailboxAction: decision.mailboxAction,
      }),
    );
    setSelectedRecentDecisionInboxAction(decision.mailboxAction);
  };

  useEffect(() => {
    return () => {
      if (pasteRuleSaveTimeoutRef.current !== null) {
        window.clearTimeout(pasteRuleSaveTimeoutRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (activeLearningModal !== "paste-rule" && pasteRuleSaveFeedback !== "idle") {
      setPasteRuleSaveFeedback("idle");
    }
  }, [activeLearningModal, pasteRuleSaveFeedback]);

  useEffect(() => {
    if (pasteRuleSaveFeedback === "saved") {
      return;
    }

    if (pasteRuleSaveTimeoutRef.current !== null) {
      window.clearTimeout(pasteRuleSaveTimeoutRef.current);
      pasteRuleSaveTimeoutRef.current = null;
    }
  }, [pasteRuleSaveFeedback]);

  useEffect(() => {
    if (activeLearningSuggestionIndex >= activeLearningSuggestions.length) {
      setActiveLearningSuggestionIndex(0);
    }
  }, [activeLearningSuggestionIndex, activeLearningSuggestions.length]);

  useEffect(() => {
    setSelectedLearningPriority(null);
    setSelectedLearningMailboxId(activeLearningSuggestion?.mailboxId ?? orderedMailboxes[0]?.id ?? null);
  }, [activeLearningSuggestion?.mailboxId, orderedMailboxes]);

  useEffect(() => {
    setSelectedPasteRuleMailboxId(orderedMailboxes[0]?.id ?? null);
  }, [orderedMailboxes]);

  useEffect(() => {
    setSelectedUncertainMailboxId(null);
    setIsUncertainMovePickerOpen(false);
    setReviewUncertainCompletionFeedback("idle");
  }, [activeUncertainEmailIndex]);

  useEffect(() => {
    if (activeUncertainEmailIndex >= uncertainEmailPool.length) {
      setActiveUncertainEmailIndex(0);
    }
  }, [activeUncertainEmailIndex, uncertainEmailPool.length]);

  useEffect(() => {
    if (!aiSuggestionsEnabled) {
      setActiveLearningModal(null);
      return;
    }
  }, [aiSuggestionsEnabled]);

  useEffect(() => {
    if (!aiSuggestionsEnabled || !learningLaunchRequest) {
      return;
    }

    if (
      typeof learningLaunchRequest.recentDecisionIndex === "number" &&
      recentLearningDecisions[learningLaunchRequest.recentDecisionIndex]
    ) {
      setActiveRecentDecisionIndex(learningLaunchRequest.recentDecisionIndex);
    }

    if (learningLaunchRequest.modal === "review-uncertain") {
      openReviewUncertainModal();
      onConsumeLearningLaunchRequest();
      return;
    }

    if (learningLaunchRequest.modal === "refine-cuevion") {
      openRefineCuevionModal();
      onConsumeLearningLaunchRequest();
      return;
    }

    setActiveLearningModal(learningLaunchRequest.modal);
    onConsumeLearningLaunchRequest();
  }, [
    aiSuggestionsEnabled,
    learningLaunchRequest,
    onConsumeLearningLaunchRequest,
    recentLearningDecisions,
  ]);

  useEffect(() => {
    if (activeLearningModal !== "edit-recent-decision") {
      return;
    }

    initializeRecentDecisionEditor(activeRecentDecision);
  }, [activeLearningModal, activeRecentDecisionIndex]);

  useEffect(() => {
    if (activeRecentDecisionIndex >= recentLearningDecisions.length) {
      setActiveRecentDecisionIndex(0);
    }
  }, [activeRecentDecisionIndex, recentLearningDecisions.length]);

  return (
    <div className="space-y-8">
      <header className="space-y-4 px-1 pt-2">
        <div className="text-[0.72rem] font-medium uppercase tracking-[0.24em] text-[var(--workspace-text-faint)]">
          For You
        </div>
        <div className="flex items-center justify-between gap-6">
          <h1 className="text-[1.85rem] font-medium tracking-tight text-[var(--workspace-text)] md:text-[2.25rem]">
            Learning
          </h1>
        </div>
        <p className="max-w-[34rem] text-lg leading-8 text-[var(--workspace-text-muted)]">
          Shape how Cuevion thinks across your inboxes
        </p>
      </header>

      <section className="rounded-[30px] border border-[var(--workspace-border)] bg-[var(--workspace-card)] p-6 shadow-panel">
        <div className="flex flex-col items-center justify-center gap-5 px-4 py-8 text-center md:px-8 md:py-10">
          <div className="max-w-[34rem] space-y-2">
            <div className="text-[0.92rem] leading-7 text-[var(--workspace-text-soft)]">
              Run a fast active learning session to shape how Cuevion classifies, prioritizes and routes future email.
            </div>
          </div>
          {aiSuggestionsEnabled ? (
            <button
              type="button"
              onClick={openRefineCuevionModal}
              className="inline-flex h-11 items-center justify-center rounded-full border border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] px-7 text-[0.74rem] font-medium uppercase tracking-[0.18em] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_24px_rgba(118,170,112,0.08)] transition-[background-image,border-color,color,box-shadow,transform] duration-150 hover:bg-[linear-gradient(180deg,var(--workspace-accent-surface-hover-start),var(--workspace-accent-surface-hover-end))] active:scale-[0.99] focus-visible:outline-none"
            >
              Refine Cuevion
            </button>
          ) : null}
        </div>
      </section>

      {aiSuggestionsEnabled ? (
      <section className="rounded-[30px] border border-[var(--workspace-border)] bg-[var(--workspace-card)] p-6 shadow-panel">
        <div className="space-y-5">
          <div className="space-y-2">
            <div className="text-[0.72rem] font-medium uppercase tracking-[0.18em] text-[var(--workspace-text-faint)]">
              Teach Cuevion
            </div>
            <p className="max-w-[34rem] text-[0.92rem] leading-7 text-[var(--workspace-text-soft)]">
              Teach Cuevion how to treat future email with a few direct training actions.
            </p>
          </div>

          <div className="grid gap-3 xl:grid-cols-3">
            {teachCuevionActions.map((action) => (
              <button
                key={action.title}
                type="button"
                onClick={action.handler}
                className="w-full cursor-pointer rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-5 pb-4 pt-7 text-left transition-[background-color,background-image,border-color,transform] duration-150 hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface)] focus-visible:border-[var(--workspace-border-hover)] focus-visible:bg-[linear-gradient(180deg,var(--workspace-card-featured-start),var(--workspace-card-featured-end))] focus-visible:outline-none"
              >
                <div className="flex min-h-[8.5rem] flex-col pt-1">
                  <div className="text-[0.98rem] font-medium tracking-[-0.014em] text-[var(--workspace-text)]">
                    {action.title}
                  </div>
                  <div className="mt-2 text-[0.84rem] leading-6 text-[var(--workspace-text-faint)]">
                    {action.subtitle}
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>
      </section>
      ) : null}

      {aiSuggestionsEnabled && activeLearningModal === "paste-rule" && modalHost
        ? createPortal(
        <WorkspaceModalLayer>
            <div
              className="w-full max-w-[680px] overflow-hidden rounded-[28px] border border-[var(--workspace-border)] bg-[var(--workspace-modal-bg)] p-6 shadow-[0_28px_80px_rgba(61,44,32,0.18),0_10px_26px_rgba(61,44,32,0.1)]"
              onMouseDown={(event) => event.stopPropagation()}
            >
            <div className="flex items-start justify-between gap-4">
              <div className="space-y-2">
                <h2 className="text-[1.45rem] font-medium tracking-tight text-[var(--workspace-text)]">
                  Paste sender or domain
                </h2>
                <p className="max-w-[34rem] text-[0.92rem] leading-7 text-[var(--workspace-text-soft)]">
                  Train Cuevion for all future emails from a sender or domain
                </p>
              </div>
              <CloseActionButton onClick={closeLearningModal} />
            </div>

            <div className="mt-6 rounded-[24px] border border-[var(--workspace-modal-border-strong)] bg-[var(--workspace-modal-inner)] px-6 pb-6 pt-7 shadow-[inset_0_1px_0_rgba(255,255,255,0.14)]">
              <div className="space-y-3">
                <label className="space-y-2">
                  <div className="text-[0.7rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                    Sender or domain
                  </div>
                  <input
                    value={pastedRuleValue}
                    onChange={(event) => {
                      setPastedRuleValue(event.target.value);
                      setPasteRuleSaveFeedback("idle");
                    }}
                    placeholder="e.g. promo@label.com or universalmusic.com"
                    className="w-full rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3 text-[0.9rem] leading-6 text-[var(--workspace-text-soft)] outline-none placeholder:text-[var(--workspace-text-faint)]"
                  />
                </label>
                <div
                  className={`text-[0.74rem] leading-6 ${
                    pasteRuleValidationMessage
                      ? "text-[color:rgba(146,88,74,0.82)]"
                      : "text-[var(--workspace-text-faint)]"
                  }`}
                >
                  {pasteRuleValidationMessage
                    ? pasteRuleValidationMessage
                    : pasteRuleType === "sender"
                    ? "Detected as sender rule"
                    : pasteRuleType === "domain"
                      ? "Detected as domain rule"
                      : "Enter a full email or a domain"}
                </div>
              </div>

              <div className="mt-6 flex flex-wrap gap-2">
                {(["Important", "Normal", "Show Less", "Spam"] as const).map(
                  (action) => (
                    <button
                      key={`paste-rule-${action}`}
                      type="button"
                      onClick={() => {
                        setSelectedPasteRulePriority(action);
                        console.log(
                          `paste_rule_priority_${action.toLowerCase().replace(/\s+/g, "_")}`,
                        );
                      }}
                      className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 focus-visible:outline-none ${
                        selectedPasteRulePriority === action
                          ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_24px_rgba(118,170,112,0.08)]"
                          : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                      }`}
                    >
                      {action}
                    </button>
                  ),
                )}
              </div>

              <div className={`mt-4 flex flex-wrap gap-2 transition-opacity duration-200 ${selectedPasteRulePriority === "Spam" ? "pointer-events-none opacity-45" : ""}`}>
                {learningMailboxOptions.map((mailboxOption) => (
                  <button
                    key={`paste-rule-mailbox-${mailboxOption.id}`}
                    type="button"
                    disabled={selectedPasteRulePriority === "Spam"}
                    onClick={() => {
                      setSelectedPasteRuleMailboxId(mailboxOption.id);
                      console.log(`paste_rule_mailbox_${mailboxOption.id}`);
                    }}
                    className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 focus-visible:outline-none ${
                      selectedPasteRulePriority === "Spam"
                        ? "border-[color:rgba(176,155,133,0.16)] bg-[color:rgba(245,239,232,0.72)] text-[color:rgba(127,113,98,0.72)] shadow-none"
                        : selectedPasteRuleMailboxId === mailboxOption.id
                          ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_24px_rgba(118,170,112,0.08)]"
                          : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                    }`}
                  >
                    {mailboxOption.label}
                  </button>
                ))}
              </div>
              {selectedPasteRulePriority === "Spam" ? (
                <div className="mt-2 px-1 text-[0.72rem] leading-6 text-[color:rgba(127,113,98,0.72)]">
                  Uses central spam handling
                </div>
              ) : null}

              <div className="mt-6 flex items-center justify-between gap-4">
                <div
                  className={`text-[0.74rem] leading-6 transition-[opacity,transform,color] duration-200 ${
                    pasteRuleSaveFeedback === "saved"
                      ? "translate-y-0 text-[color:rgba(74,120,82,0.86)] opacity-100"
                      : "translate-y-0 text-[var(--workspace-text-faint)] opacity-100"
                  }`}
                >
                  {pasteRuleSaveFeedback === "saved"
                    ? "Rule saved"
                    : "Applies to future mail only"}
                </div>
                <button
                  type="button"
                  disabled={!canSavePasteRule}
                  onClick={() => {
                    if (!pasteRuleType || !resolvedPasteRuleCategory) {
                      return;
                    }

                    onSaveLearningRule(
                      trimmedPastedRuleValue,
                      pasteRuleType,
                      resolvedPasteRuleCategory,
                      resolveMailboxActionFromForYouSelection(
                        selectedPasteRulePriority,
                        resolvedPasteRuleCategory,
                        resolvedPasteRuleCategory === "Primary" ? "keep" : "move",
                      ),
                      {
                        sourceContext: "paste_sender_or_domain",
                        sourcePrioritySelection: selectedPasteRulePriority,
                        sourceMailboxId:
                          selectedPasteRulePriority === "Spam"
                            ? null
                            : selectedPasteRuleMailboxId,
                      },
                    );
                    const normalizedRuleValue = trimmedPastedRuleValue.toLowerCase();
                    const matchedLearningKeys = learningSuggestionPool
                      .filter((suggestion) => {
                        const normalizedSenderAddress =
                          suggestion.senderAddress.toLowerCase();

                        if (pasteRuleType === "sender") {
                          return normalizedSenderAddress === normalizedRuleValue;
                        }

                        if (pasteRuleType === "domain") {
                          return (
                            normalizedSenderAddress.split("@")[1] === normalizedRuleValue
                          );
                        }

                        return false;
                      })
                      .map((suggestion) => suggestion.key);

                    if (matchedLearningKeys.length > 0) {
                      setReviewedLearningSuggestionKeys((current) => [
                        ...current,
                        ...matchedLearningKeys.filter((key) => !current.includes(key)),
                      ]);
                    }
                    console.log(
                      `save_learning_rule_${pasteRuleType}_${selectedPasteRulePriority?.toLowerCase().replace(/\s+/g, "_") ?? "unset"}`,
                    );
                    setPasteRuleSaveFeedback("saved");
                    if (pasteRuleSaveTimeoutRef.current !== null) {
                      window.clearTimeout(pasteRuleSaveTimeoutRef.current);
                    }
                    pasteRuleSaveTimeoutRef.current = window.setTimeout(() => {
                      setPastedRuleValue("");
                      setSelectedPasteRulePriority(null);
                      setSelectedPasteRuleMailboxId(orderedMailboxes[0]?.id ?? null);
                      setPasteRuleSaveFeedback("idle");
                      closeLearningModal();
                      pasteRuleSaveTimeoutRef.current = null;
                    }, 420);
                  }}
                  className={
                    canSavePasteRule
                      ? closeActionButtonClass
                      : `${learningModalPrimaryActionButtonClass} cursor-default border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] text-[var(--workspace-text-faint)] opacity-55`
                  }
                >
                  Save learning rule
                </button>
              </div>
              </div>
            </div>
        </WorkspaceModalLayer>,
        modalHost,
      ) : null}

      {aiSuggestionsEnabled &&
      activeLearningModal === "review-uncertain" &&
      modalHost
        ? createPortal(
        <WorkspaceModalLayer>
            <div
              className="w-full max-w-[680px] overflow-hidden rounded-[28px] border border-[var(--workspace-border)] bg-[var(--workspace-modal-bg)] p-6 shadow-[0_28px_80px_rgba(61,44,32,0.18),0_10px_26px_rgba(61,44,32,0.1)]"
              onMouseDown={(event) => event.stopPropagation()}
            >
            <div className="flex items-start justify-between gap-4">
              <div className="space-y-2">
                <h2 className="text-[1.45rem] font-medium tracking-tight text-[var(--workspace-text)]">
                  Check uncertain emails
                </h2>
                <p className="max-w-[34rem] text-[0.92rem] leading-7 text-[var(--workspace-text-soft)]">
                  Help Cuevion decide where this message belongs when confidence is low.
                </p>
              </div>
              <button
                type="button"
                onClick={closeLearningModal}
                className={closeActionButtonClass}
              >
                Close
              </button>
            </div>

            <div className="mt-6 rounded-[24px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-modal-subtle)] px-6 pb-6 pt-7 shadow-[inset_0_1px_0_rgba(255,255,255,0.12)]">
              {activeUncertainEmail ? (
                <>
              <div className="rounded-[20px] border border-[var(--workspace-modal-border-strong)] bg-[var(--workspace-modal-inner)] px-5 py-5 shadow-[inset_0_1px_0_rgba(255,255,255,0.14)]">
                <div className="max-h-[22rem] overflow-y-auto pr-2">
                  <div className="space-y-4">
                    <div className="space-y-1">
                      <div className="text-[0.7rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                        Sender
                      </div>
                      <div className="text-[0.98rem] font-medium tracking-[-0.014em] text-[var(--workspace-text)]">
                        {activeUncertainEmail.sender}
                      </div>
                    </div>
                    <div className="space-y-1">
                      <div className="text-[0.7rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                        Subject
                      </div>
                      <div className="text-[0.98rem] font-medium tracking-[-0.014em] text-[var(--workspace-text)]">
                        {activeUncertainEmail.subject}
                      </div>
                    </div>
                    <div className="space-y-1">
                      <div className="text-[0.7rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                        Preview
                      </div>
                      <div className="space-y-3">
                        {activeUncertainEmail.preview.map((paragraph) => (
                          <div
                            key={paragraph}
                            className="text-[0.9rem] leading-7 text-[var(--workspace-text-soft)]"
                          >
                            {paragraph}
                          </div>
                        ))}
                      </div>
                    </div>
                    <div className="text-[0.82rem] leading-6 text-[var(--workspace-text-faint)]">
                      Currently in: {activeUncertainEmail.currentMailboxLabel} — low confidence
                    </div>
                  </div>
                </div>
              </div>

              <div className="mt-6 h-px w-full bg-[var(--workspace-divider)]" />

              <div className="mt-4 space-y-1 px-1">
                <div className="text-[0.7rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                  Cuevion is unsure because:
                </div>
                <div className="text-[0.88rem] leading-7 text-[var(--workspace-emphasis-text)]">
                  {activeUncertainEmail.reason}
                </div>
              </div>

              <div className="mt-5 space-y-3">
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      if (!activeUncertainEmail.mailboxId) {
                        return;
                      }

                      setSelectedUncertainMailboxId(activeUncertainEmail.mailboxId);
                      setIsUncertainMovePickerOpen(false);
                      console.log(
                        `review_uncertain_keep_${activeUncertainEmail.mailboxId}`,
                      );
                    }}
                    className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 focus-visible:outline-none ${
                      selectedUncertainMailboxId === activeUncertainEmail.mailboxId
                        ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_24px_rgba(118,170,112,0.08)]"
                        : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                    }`}
                  >
                    {`Keep in ${activeUncertainEmail.currentMailboxLabel}`}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setIsUncertainMovePickerOpen((current) => !current);
                      if (selectedUncertainMailboxId === activeUncertainEmail.mailboxId) {
                        setSelectedUncertainMailboxId(null);
                      }
                    }}
                    className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 focus-visible:outline-none ${
                      isUncertainMovePickerOpen
                        ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_24px_rgba(118,170,112,0.08)]"
                        : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                    }`}
                  >
                    Move to...
                  </button>
                </div>

                {isUncertainMovePickerOpen ? (
                  <div className="flex flex-wrap gap-2">
                    {uncertainDestinationOptions
                      .filter((option) => option.id !== activeUncertainEmail.mailboxId)
                      .map((option) => (
                        <button
                          key={option.id}
                          type="button"
                          onClick={() => {
                            setSelectedUncertainMailboxId(option.id);
                            console.log(`review_uncertain_move_${option.id}`);
                          }}
                          className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 focus-visible:outline-none ${
                            selectedUncertainMailboxId === option.id
                              ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_24px_rgba(118,170,112,0.08)]"
                              : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                          }`}
                        >
                          {option.label}
                        </button>
                      ))}
                  </div>
                ) : null}
              </div>

              <div className="mt-6 grid grid-cols-[auto_1fr_auto_auto] items-center gap-3">
                <button
                  type="button"
                  onClick={() =>
                    setActiveUncertainEmailIndex((current) =>
                      current === 0 ? totalUncertainEmails - 1 : current - 1,
                    )
                  }
                  className={closeActionButtonClass}
                >
                  Back
                </button>
                <div className="text-center text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)] transition-[color,opacity] duration-200">
                  {reviewUncertainCompletionFeedback === "done"
                    ? "Cuevion learned from your decisions"
                    : `Email ${safeUncertainEmailIndex + 1} of ${totalUncertainEmails}`}
                </div>
                <button
                  type="button"
                  disabled={!hasValidUncertainSelection}
                  onClick={() => {
                    if (!hasValidUncertainSelection || !persistActiveUncertainDecision()) {
                      return;
                    }
                    if (isLastUncertainEmail) {
                      setReviewUncertainCompletionFeedback("done");
                      if (reviewUncertainCompletionTimeoutRef.current !== null) {
                        window.clearTimeout(reviewUncertainCompletionTimeoutRef.current);
                      }
                      reviewUncertainCompletionTimeoutRef.current = window.setTimeout(() => {
                        closeLearningModal();
                        setActiveUncertainEmailIndex(0);
                        reviewUncertainCompletionTimeoutRef.current = null;
                      }, 420);
                      return;
                    }
                    setActiveUncertainEmailIndex((current) => current + 1);
                  }}
                  className={
                    hasValidUncertainSelection
                      ? `${closeActionButtonClass} animate-fade-in`
                      : `${learningModalPrimaryActionButtonClass} cursor-default border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] text-[var(--workspace-text-faint)] opacity-55`
                  }
                >
                  {isLastUncertainEmail ? "Finish" : "Next Email"}
                </button>
                {isLastUncertainEmail ? null : (
                  <button
                    type="button"
                    onClick={() => {
                      closeLearningModal();
                      setActiveUncertainEmailIndex(0);
                    }}
                    className={closeActionButtonClass}
                  >
                    Finish
                  </button>
                )}
              </div>
                </>
              ) : (
                <div className="rounded-[20px] border border-[var(--workspace-modal-border-strong)] bg-[var(--workspace-modal-inner)] px-5 py-6 shadow-[inset_0_1px_0_rgba(255,255,255,0.14)]">
                  <div className="text-[0.95rem] leading-7 text-[var(--workspace-text-soft)]">
                    There are no uncertain emails to check right now.
                  </div>
                </div>
              )}
            </div>
            </div>
        </WorkspaceModalLayer>,
        modalHost,
      ) : null}

      {aiSuggestionsEnabled && activeLearningModal === "recent-decisions" && modalHost
        ? createPortal(
        <WorkspaceModalLayer>
            <div
              className="w-full max-w-[680px] overflow-hidden rounded-[28px] border border-[var(--workspace-border)] bg-[var(--workspace-modal-bg)] p-6 shadow-[0_28px_80px_rgba(61,44,32,0.18),0_10px_26px_rgba(61,44,32,0.1)]"
              onMouseDown={(event) => event.stopPropagation()}
            >
            <div className="flex items-start justify-between gap-4">
              <div className="space-y-2">
                <h2 className="text-[1.45rem] font-medium tracking-tight text-[var(--workspace-text)]">
                  Recent learning decisions
                </h2>
                <p className="max-w-[34rem] text-[0.92rem] leading-7 text-[var(--workspace-text-soft)]">
                  See recent learning actions across your inboxes.
                </p>
              </div>
              <button
                type="button"
                onClick={closeLearningModal}
                className={closeActionButtonClass}
              >
                Close
              </button>
            </div>

            <div className="mt-6 rounded-[24px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-modal-subtle)] px-6 pb-6 pt-7 shadow-[inset_0_1px_0_rgba(255,255,255,0.12)]">
              <div className="rounded-[20px] border border-[var(--workspace-modal-border-strong)] bg-[var(--workspace-modal-inner)] px-5 py-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.14)]">
                <div
                  className="cuevion-soft-scroll max-h-[19rem] overflow-y-auto pr-1"
                  style={{
                    scrollbarWidth: "thin",
                    scrollbarColor:
                      "var(--workspace-scrollbar-thumb) var(--workspace-scrollbar-track)",
                  }}
                >
                <div className="divide-y divide-[color:rgba(120,104,89,0.12)]">
                  {recentLearningDecisions.map((decision, index) => (
                    <button
                      key={`${decision.sender}-${decision.action}-${decision.timestamp}`}
                      type="button"
                      onClick={() => {
                        setActiveRecentDecisionIndex(index);
                        initializeRecentDecisionEditor(decision);
                        setActiveLearningModal("edit-recent-decision");
                      }}
                      className="flex w-full items-center justify-between gap-4 py-3 text-left transition-colors duration-200 first:pt-1 first:pb-3 last:pt-3 last:pb-1 hover:text-[var(--workspace-text)] focus-visible:outline-none"
                    >
                      <div className="min-w-0 text-[0.9rem] leading-6 text-[var(--workspace-text-soft)]">
                        <span className="font-medium text-[var(--workspace-text)]">
                          {decision.sender}
                        </span>{" "}
                        — {decision.action}
                      </div>
                      <div className="flex-none text-[0.72rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-text-faint)]">
                        {decision.timestamp}
                      </div>
                    </button>
                  ))}
                </div>
                </div>
              </div>
              </div>
            </div>
        </WorkspaceModalLayer>,
        modalHost,
      ) : null}

      {aiSuggestionsEnabled && activeLearningModal === "edit-recent-decision" && modalHost
        ? createPortal(
        <WorkspaceModalLayer>
            <div
              className="w-full max-w-[620px] overflow-hidden rounded-[28px] border border-[var(--workspace-border)] bg-[var(--workspace-modal-bg)] p-6 shadow-[0_28px_80px_rgba(61,44,32,0.18),0_10px_26px_rgba(61,44,32,0.1)]"
              onMouseDown={(event) => event.stopPropagation()}
            >
            <div className="flex items-start justify-between gap-4">
              <div className="space-y-2">
                <h2 className="text-[1.45rem] font-medium tracking-tight text-[var(--workspace-text)]">
                  Edit learning decision
                </h2>
              </div>
              <button
                type="button"
                onClick={closeLearningModal}
                className={closeActionButtonClass}
              >
                Close
              </button>
            </div>

            <div className="mt-6 rounded-[24px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-modal-subtle)] px-6 pb-6 pt-7 shadow-[inset_0_1px_0_rgba(255,255,255,0.12)]">
              <div className="space-y-1">
                <div className="text-[0.7rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                  Sender name
                </div>
                <div className="text-[0.98rem] font-medium tracking-[-0.014em] text-[var(--workspace-text)]">
                  {activeRecentDecision.sender}
                </div>
              </div>

              <div className="mt-4 space-y-1">
                <div className="text-[0.7rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                  Current learning rule
                </div>
                <div className="text-[0.88rem] leading-7 text-[var(--workspace-emphasis-text)]">
                  {activeRecentDecision.action}
                </div>
              </div>

              {activeRecentDecision?.sourceContext === "refine" ? (
                <>
                  <div className="mt-5 flex flex-wrap gap-2">
                    {(["Important", "Normal", "Show Less", "Spam"] as const).map((action) => (
                      <button
                        key={`recent-edit-refine-${action}`}
                        type="button"
                        onClick={() => setSelectedRecentDecisionPriority(action)}
                        className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 focus-visible:outline-none ${
                          selectedRecentDecisionPriority === action
                            ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_24px_rgba(118,170,112,0.08)]"
                            : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                        }`}
                      >
                        {action}
                      </button>
                    ))}
                  </div>

                  <div className={`mt-4 flex flex-wrap gap-2 transition-opacity duration-200 ${selectedRecentDecisionPriority === "Spam" ? "pointer-events-none opacity-45" : ""}`}>
                    {learningMailboxOptions.map((mailboxOption) => (
                      <button
                        key={`recent-edit-refine-mailbox-${mailboxOption.id}`}
                        type="button"
                        disabled={selectedRecentDecisionPriority === "Spam"}
                        onClick={() => setSelectedRecentDecisionMailboxId(mailboxOption.id)}
                        className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 focus-visible:outline-none ${
                          selectedRecentDecisionPriority === "Spam"
                            ? "border-[color:rgba(176,155,133,0.16)] bg-[color:rgba(245,239,232,0.72)] text-[color:rgba(127,113,98,0.72)] shadow-none"
                            : selectedRecentDecisionMailboxId === mailboxOption.id
                              ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_24px_rgba(118,170,112,0.08)]"
                              : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                        }`}
                      >
                        {mailboxOption.label}
                      </button>
                    ))}
                  </div>
                </>
              ) : activeRecentDecision?.sourceContext === "uncertain" ? (
                <div className="mt-5 space-y-3">
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        setSelectedRecentDecisionUncertainMailboxId(
                          activeRecentDecision.sourceCurrentMailboxId ?? null,
                        );
                        setIsRecentDecisionMovePickerOpen(false);
                      }}
                      className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 focus-visible:outline-none ${
                        selectedRecentDecisionUncertainMailboxId ===
                        (activeRecentDecision.sourceCurrentMailboxId ?? null)
                          ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_24px_rgba(118,170,112,0.08)]"
                          : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                      }`}
                    >
                      {`Keep in ${activeRecentDecisionCurrentMailboxLabel}`}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setIsRecentDecisionMovePickerOpen((current) => !current);
                        if (
                          selectedRecentDecisionUncertainMailboxId ===
                          (activeRecentDecision.sourceCurrentMailboxId ?? null)
                        ) {
                          setSelectedRecentDecisionUncertainMailboxId(null);
                        }
                      }}
                      className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 focus-visible:outline-none ${
                        isRecentDecisionMovePickerOpen
                          ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_24px_rgba(118,170,112,0.08)]"
                          : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                      }`}
                    >
                      Move to...
                    </button>
                  </div>

                  {isRecentDecisionMovePickerOpen ? (
                    <div className="flex flex-wrap gap-2">
                      {uncertainDestinationOptions
                        .filter(
                          (option) =>
                            option.id !== (activeRecentDecision.sourceCurrentMailboxId ?? null),
                        )
                        .map((option) => (
                          <button
                            key={`recent-edit-uncertain-${option.id}`}
                            type="button"
                            onClick={() => {
                              setSelectedRecentDecisionUncertainMailboxId(option.id);
                            }}
                            className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 focus-visible:outline-none ${
                              selectedRecentDecisionUncertainMailboxId === option.id
                                ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_24px_rgba(118,170,112,0.08)]"
                                : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                            }`}
                          >
                            {option.label}
                          </button>
                        ))}
                    </div>
                  ) : null}
                </div>
              ) : activeRecentDecision?.sourceContext === "paste_sender_or_domain" ? (
                <>
                  <div className="mt-5 space-y-3">
                    <div className="space-y-2">
                      <div className="text-[0.7rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                        Sender or domain
                      </div>
                      <div className="rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3 text-[0.9rem] leading-6 text-[var(--workspace-text-soft)]">
                        {activeRecentDecision.ruleValue}
                      </div>
                    </div>
                    <div className="text-[0.74rem] leading-6 text-[var(--workspace-text-faint)]">
                      {activeRecentDecision.ruleType === "domain"
                        ? "Detected as domain rule"
                        : "Detected as sender rule"}
                    </div>
                  </div>

                  <div className="mt-6 flex flex-wrap gap-2">
                    {(["Important", "Normal", "Show Less", "Spam"] as const).map((action) => (
                      <button
                        key={`recent-edit-paste-${action}`}
                        type="button"
                        onClick={() => setSelectedRecentDecisionPriority(action)}
                        className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 focus-visible:outline-none ${
                          selectedRecentDecisionPriority === action
                            ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_24px_rgba(118,170,112,0.08)]"
                            : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                        }`}
                      >
                        {action}
                      </button>
                    ))}
                  </div>

                  <div className={`mt-4 flex flex-wrap gap-2 transition-opacity duration-200 ${selectedRecentDecisionPriority === "Spam" ? "pointer-events-none opacity-45" : ""}`}>
                    {learningMailboxOptions.map((mailboxOption) => (
                      <button
                        key={`recent-edit-paste-mailbox-${mailboxOption.id}`}
                        type="button"
                        disabled={selectedRecentDecisionPriority === "Spam"}
                        onClick={() => setSelectedRecentDecisionMailboxId(mailboxOption.id)}
                        className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 focus-visible:outline-none ${
                          selectedRecentDecisionPriority === "Spam"
                            ? "border-[color:rgba(176,155,133,0.16)] bg-[color:rgba(245,239,232,0.72)] text-[color:rgba(127,113,98,0.72)] shadow-none"
                            : selectedRecentDecisionMailboxId === mailboxOption.id
                              ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_24px_rgba(118,170,112,0.08)]"
                              : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                        }`}
                      >
                        {mailboxOption.label}
                      </button>
                    ))}
                  </div>
                </>
              ) : (
                <>
                  <div className="mt-5 flex flex-wrap gap-2">
                    {(["Important", "Review", "Promo", "Demo", "Spam"] as const).map(
                      (category) => (
                        <button
                          key={`recent-edit-${category}`}
                          type="button"
                          onClick={() => setSelectedRecentDecisionCategory(category)}
                          className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 focus-visible:outline-none ${
                            selectedRecentDecisionCategory === category
                              ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_24px_rgba(118,170,112,0.08)]"
                              : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                          }`}
                        >
                          {getForYouCategoryLabel(category)}
                        </button>
                      ),
                    )}
                  </div>

                  <div className="mt-4 flex flex-wrap gap-2">
                    {(
                      [
                        { value: "keep", label: "Keep in Inbox" },
                        { value: "move", label: "Move out of Inbox" },
                      ] as const
                    ).map((option) => (
                      <button
                        key={`recent-edit-inbox-${option.value}`}
                        type="button"
                        onClick={() => setSelectedRecentDecisionInboxAction(option.value)}
                        className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 focus-visible:outline-none ${
                          selectedRecentDecisionInboxAction === option.value
                            ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_24px_rgba(118,170,112,0.08)]"
                            : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                        }`}
                      >
                        {option.label}
                      </button>
                    ))}
                  </div>
                </>
              )}

              <div className="mt-6 flex items-center justify-end gap-3">
                <button
                  type="button"
                  onClick={() => setActiveLearningModal("recent-decisions")}
                  className={subtleSecondaryActionButtonClass}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => {
                    if (!activeRecentDecision) {
                      return;
                    }

                    console.log("save_recent_learning_decision_changes");
                    if (activeRecentDecision.sourceContext === "refine") {
                      if (!selectedRecentDecisionPriority) {
                        return;
                      }

                      const category =
                        selectedRecentDecisionPriority === "Spam" ||
                        selectedRecentDecisionPriority === "Show Less"
                          ? "Updates"
                          : selectedRecentDecisionMailboxId === "promo"
                            ? "Promo"
                            : "Primary";

                      onSaveLearningRule(
                        activeRecentDecision.ruleValue,
                        activeRecentDecision.ruleType,
                        category,
                        resolveMailboxActionFromForYouSelection(
                          selectedRecentDecisionPriority,
                          category,
                          category === "Primary" ? "keep" : "move",
                        ),
                        {
                          sourceContext: "refine",
                          sourcePrioritySelection: selectedRecentDecisionPriority,
                          sourceMailboxId:
                            selectedRecentDecisionPriority === "Spam"
                              ? null
                              : selectedRecentDecisionMailboxId,
                        },
                      );
                    } else if (activeRecentDecision.sourceContext === "uncertain") {
                      if (!selectedRecentDecisionUncertainMailboxId) {
                        return;
                      }

                      onSaveLearningRule(
                        activeRecentDecision.ruleValue,
                        activeRecentDecision.ruleType,
                        resolveCuevionCategoryFromMailboxId(
                          selectedRecentDecisionUncertainMailboxId,
                        ),
                        resolveMailboxActionFromMailboxId(
                          selectedRecentDecisionUncertainMailboxId,
                        ),
                        {
                          sourceContext: "uncertain",
                          sourceMailboxId: selectedRecentDecisionUncertainMailboxId,
                          sourceCurrentMailboxId:
                            activeRecentDecision.sourceCurrentMailboxId ?? null,
                        },
                      );
                    } else if (
                      activeRecentDecision.sourceContext === "paste_sender_or_domain"
                    ) {
                      if (!selectedRecentDecisionPriority) {
                        return;
                      }

                      const category =
                        selectedRecentDecisionPriority === "Spam" ||
                        selectedRecentDecisionPriority === "Show Less"
                          ? "Updates"
                          : selectedRecentDecisionMailboxId === "promo"
                            ? "Promo"
                            : "Primary";

                      onSaveLearningRule(
                        activeRecentDecision.ruleValue,
                        activeRecentDecision.ruleType,
                        category,
                        resolveMailboxActionFromForYouSelection(
                          selectedRecentDecisionPriority,
                          category,
                          category === "Primary" ? "keep" : "move",
                        ),
                        {
                          sourceContext: "paste_sender_or_domain",
                          sourcePrioritySelection: selectedRecentDecisionPriority,
                          sourceMailboxId:
                            selectedRecentDecisionPriority === "Spam"
                              ? null
                              : selectedRecentDecisionMailboxId,
                        },
                      );
                    } else {
                      onSaveLearningRule(
                        activeRecentDecision.ruleValue,
                        activeRecentDecision.ruleType,
                        resolveCuevionCategoryFromForYouSelection(
                          selectedRecentDecisionCategory,
                        ),
                        selectedRecentDecisionInboxAction ?? "keep",
                      );
                    }
                    setActiveLearningModal("recent-decisions");
                  }}
                  className={closeActionButtonClass}
                >
                  Save changes
                </button>
              </div>
              </div>
            </div>
        </WorkspaceModalLayer>,
        modalHost,
      ) : null}

      {aiSuggestionsEnabled && activeLearningModal === "refine-cuevion" && modalHost
        ? createPortal(
        <WorkspaceModalLayer>
            <div
              className="w-full max-w-[680px] overflow-hidden rounded-[28px] border border-[var(--workspace-border)] bg-[var(--workspace-modal-bg)] p-6 shadow-[0_28px_80px_rgba(61,44,32,0.18),0_10px_26px_rgba(61,44,32,0.1)]"
              onMouseDown={(event) => event.stopPropagation()}
            >
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-[1.45rem] font-medium tracking-tight text-[var(--workspace-text)]">
                  Refine Cuevion
                </h2>
              </div>
              <button
                type="button"
                onClick={closeLearningModal}
                className={closeActionButtonClass}
              >
                Close
              </button>
            </div>

            <div className="mt-6 rounded-[24px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-modal-subtle)] px-6 pb-6 pt-7 shadow-[inset_0_1px_0_rgba(255,255,255,0.12)]">
              {!hasPendingLearningSuggestions ? (
                <div className="flex min-h-[20rem] items-center justify-center text-center">
                  <div className="max-w-[24rem] space-y-3">
                    <div className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                      Queue clear
                    </div>
                    <div className="text-[1.02rem] font-medium tracking-[-0.014em] text-[var(--workspace-text)]">
                      Cuevion has no pending learning suggestions right now
                    </div>
                  </div>
                </div>
              ) : (
                <>
                <div className="rounded-[20px] border border-[var(--workspace-modal-border-strong)] bg-[var(--workspace-modal-inner)] px-5 py-5 shadow-[inset_0_1px_0_rgba(255,255,255,0.14)]">
                  <div className="max-h-[22rem] overflow-y-auto pr-2">
                    <div className="space-y-4">
                    <div className="space-y-1">
                      <div className="text-[0.7rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                        Sender
                      </div>
                      <div className="text-[0.98rem] font-medium tracking-[-0.014em] text-[var(--workspace-text)]">
                        {activeLearningSuggestion.sender}
                      </div>
                    </div>
                    <div className="space-y-1">
                      <div className="text-[0.7rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                        Subject
                      </div>
                      <div className="text-[0.98rem] font-medium tracking-[-0.014em] text-[var(--workspace-text)]">
                        {activeLearningSuggestion.subject}
                      </div>
                    </div>
                    <div className="space-y-1">
                      <div className="text-[0.7rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                        Preview
                      </div>
                    <div className="space-y-3">
                      {activeLearningSuggestion.snippet.map((paragraph) => (
                        <div
                          key={paragraph}
                          className="text-[0.9rem] leading-7 text-[var(--workspace-text-soft)]"
                        >
                          {paragraph}
                        </div>
                      ))}
                    </div>
                    </div>
                    {activeLearningSuggestion.visualLabel ? (
                      <div className="overflow-hidden rounded-[20px] border border-[var(--workspace-border-soft)] bg-[linear-gradient(180deg,var(--workspace-preview-surface-start),var(--workspace-preview-surface-end))]">
                        <div className="flex h-[180px] items-end bg-[radial-gradient(circle_at_top_left,rgba(255,255,255,0.34),transparent_42%),linear-gradient(135deg,rgba(184,165,146,0.32),rgba(138,111,89,0.16))] p-4">
                          <div className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                            {activeLearningSuggestion.visualLabel}
                          </div>
                        </div>
                      </div>
                    ) : null}
                  </div>
                </div>
              </div>

              <div className="mt-6 h-px w-full bg-[var(--workspace-divider)]" />
              <div className="mt-4 space-y-1 px-1">
                <div className="text-[0.7rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                  Cuevion reason
                </div>
                <div className="text-[0.88rem] leading-7 text-[var(--workspace-emphasis-text)]">
                  {activeLearningSuggestion.reason}
                </div>
              </div>

              <div className="mt-5 flex flex-wrap gap-2">
                {(["Important", "Normal", "Show Less", "Spam"] as const).map(
                  (action) => (
                    <button
                      key={action}
                      type="button"
                      onClick={() => {
                        setSelectedLearningPriority(action);
                        console.log(
                          `refine_cuevion_priority_${action.toLowerCase().replace(/\s+/g, "_")}`,
                        );
                      }}
                      className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 focus-visible:outline-none ${
                        selectedLearningPriority === action
                          ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_24px_rgba(118,170,112,0.08)]"
                          : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                      }`}
                    >
                      {action}
                    </button>
                  ),
                )}
              </div>

              <div className={`mt-4 flex flex-wrap gap-2 transition-opacity duration-200 ${selectedLearningPriority === "Spam" ? "pointer-events-none opacity-45" : ""}`}>
                {learningMailboxOptions.map((mailboxOption) => (
                  <button
                    key={mailboxOption.id}
                    type="button"
                    disabled={selectedLearningPriority === "Spam"}
                    onClick={() => {
                      setSelectedLearningMailboxId(mailboxOption.id);
                      console.log(`refine_cuevion_mailbox_${mailboxOption.id}`);
                    }}
                    className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color,box-shadow,transform] duration-150 focus-visible:outline-none ${
                      selectedLearningPriority === "Spam"
                        ? "border-[color:rgba(176,155,133,0.16)] bg-[color:rgba(245,239,232,0.72)] text-[color:rgba(127,113,98,0.72)] shadow-none"
                        : selectedLearningMailboxId === mailboxOption.id
                        ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_8px_24px_rgba(118,170,112,0.08)]"
                        : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                    }`}
                  >
                    {mailboxOption.label}
                  </button>
                ))}
              </div>
              {selectedLearningPriority === "Spam" ? (
                <div className="mt-2 px-1 text-[0.72rem] leading-6 text-[color:rgba(127,113,98,0.72)]">
                  Uses central spam handling
                </div>
              ) : null}
                </>
              )}

              <div className="mt-6 grid grid-cols-[auto_1fr_auto] items-center gap-3">
                <button
                  type="button"
                  disabled={!hasPendingLearningSuggestions}
                  onClick={() =>
                    setActiveLearningSuggestionIndex((current) =>
                      current === 0 ? totalLearningSuggestions - 1 : current - 1,
                    )
                  }
                  className={`${closeActionButtonClass} ${
                    hasPendingLearningSuggestions
                      ? ""
                      : "cursor-default border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] text-[var(--workspace-text-faint)] opacity-55"
                  }`}
                >
                  Back
                </button>
                <div className="text-center text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                  {activeLearningSuggestions.length > 0
                    ? `${currentLearningSuggestionNumber} of ${totalLearningSuggestions}`
                    : "0 of 0"}
                </div>
                <button
                  type="button"
                  disabled={!hasPendingLearningSuggestions || !hasValidLearningSelection}
                  onClick={() => {
                    if (
                      !hasPendingLearningSuggestions ||
                      !hasValidLearningSelection ||
                      !persistActiveLearningSuggestionDecision()
                    ) {
                      return;
                    }

                    if (isLastLearningSuggestion) {
                      setActiveLearningSuggestionIndex(0);
                      closeLearningModal();
                      return;
                    }

                    setActiveLearningSuggestionIndex((current) =>
                      Math.min(current, Math.max(activeLearningSuggestions.length - 2, 0)),
                    );
                  }}
                  className={
                    hasPendingLearningSuggestions && hasValidLearningSelection
                      ? closeActionButtonClass
                      : `${learningModalPrimaryActionButtonClass} cursor-default border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] text-[var(--workspace-text-faint)] opacity-55`
                  }
                >
                  {isLastLearningSuggestion ? "Finish" : "Next suggestion"}
                </button>
              </div>
              </div>
            </div>
        </WorkspaceModalLayer>,
        modalHost,
      ) : null}
    </div>
  );
}

export function WorkspaceShell({
  theme = "light",
  onboardingState,
  authenticatedUser = null,
  collaborationInviteRoute = null,
  workspaceDataMode = "live",
}: {
  theme?: "light" | "dark";
  onboardingState: OnboardingState;
  authenticatedUser?: AuthenticatedCuevionUser | null;
  collaborationInviteRoute?: CollaborationInviteRoute | null;
  workspaceDataMode?: WorkspaceDataMode;
}) {
  const isDemoWorkspace = workspaceDataMode === "demo";
  const [workspaceMode, setWorkspaceMode] = useState<SettingsMode>(() => {
    if (typeof window === "undefined") {
      return theme === "dark" ? "Dark" : "Light";
    }

    const storedValue = window.localStorage.getItem(WORKSPACE_THEME_MODE_STORAGE_KEY);
    const storedMode = normalizeStoredWorkspaceThemeMode(storedValue);

    return storedMode ?? (theme === "dark" ? "Dark" : "Light");
  });
  const [systemColorMode, setSystemColorMode] = useState<"light" | "dark">(() =>
    typeof window !== "undefined" &&
    window.matchMedia &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light",
  );
  const [mailboxTitleOverrides, setMailboxTitleOverrides] = useState<
    Partial<Record<InboxId, string>>
  >(() => {
    if (typeof window === "undefined") {
      return {};
    }

    const storedValue = window.localStorage.getItem(MAILBOX_TITLE_OVERRIDES_STORAGE_KEY);

    if (!storedValue) {
      return {};
    }

    try {
      return JSON.parse(storedValue) as Partial<Record<InboxId, string>>;
    } catch {
      return {};
    }
  });
  const [savedManagedInboxes, setSavedManagedInboxes] = useState<ManagedWorkspaceInbox[]>(() => {
    if (typeof window === "undefined") {
      return buildManagedWorkspaceInboxes(onboardingState);
    }

    const storedValue = window.localStorage.getItem(MANAGED_INBOXES_STORAGE_KEY);

    if (!storedValue) {
      return buildManagedWorkspaceInboxes(onboardingState);
    }

    try {
      return (JSON.parse(storedValue) as ManagedWorkspaceInbox[]).map(
        cloneManagedWorkspaceInbox,
      );
    } catch {
      return buildManagedWorkspaceInboxes(onboardingState);
    }
  });
  const onboardingMailboxSeedKey = JSON.stringify({
    selectedInboxes: onboardingState.selectedInboxes,
    customInboxes: onboardingState.customInboxes,
    inboxConnections: onboardingState.selectedInboxes.map((inboxId) => ({
      id: inboxId,
      email: onboardingState.inboxConnections[inboxId]?.email ?? "",
      provider: onboardingState.inboxConnections[inboxId]?.provider ?? null,
      connected: onboardingState.inboxConnections[inboxId]?.connected ?? false,
    })),
  });
  const lastOnboardingMailboxSeedKeyRef = useRef(onboardingMailboxSeedKey);
  const orderedMailboxes = savedManagedInboxes.map((mailbox) => ({
    ...toOrderedMailboxFromManagedInbox(mailbox),
  })).map((mailbox) => ({
    ...mailbox,
    title: mailboxTitleOverrides[mailbox.id]?.trim() || mailbox.title,
  }));
  const primaryInboxTitle = orderedMailboxes[0]?.title ?? inboxDisplayConfig.main.title;
  const primaryWorkspaceEmail = orderedMailboxes[0]?.email ?? "team@cuevion.com";
  const activeWorkspaceEmail = authenticatedUser?.email ?? primaryWorkspaceEmail;
  const currentWorkspaceUserId = normalizeSenderLearningKey(activeWorkspaceEmail);
  const mailboxOrderKey = orderedMailboxes.map((mailbox) => mailbox.id).join("|");
  const messageUnreadOverridesStorageKey = buildMessageUnreadOverridesStorageKey(
    currentWorkspaceUserId,
    mailboxOrderKey,
  );
  const sentMessagesStorageKey = buildSentMessagesStorageKey(
    currentWorkspaceUserId,
    mailboxOrderKey,
  );
  const trashMessagesStorageKey = buildTrashMessagesStorageKey(
    currentWorkspaceUserId,
    mailboxOrderKey,
  );
  const spamMessagesStorageKey = buildSpamMessagesStorageKey(
    currentWorkspaceUserId,
    mailboxOrderKey,
  );
  const archiveMessagesStorageKey = buildArchiveMessagesStorageKey(
    currentWorkspaceUserId,
    mailboxOrderKey,
  );
  const manualPriorityOverridesStorageKey = buildManualPriorityOverridesStorageKey(
    currentWorkspaceUserId,
    mailboxOrderKey,
  );
  const liveMailboxSyncKey = orderedMailboxes
    .map((mailbox) => `${mailbox.id}:${mailbox.email}:${mailbox.title}`)
    .join("|");
  const [senderCategoryLearning, setSenderCategoryLearning] =
    useState<SenderCategoryLearningStore>(() => {
      if (typeof window === "undefined") {
        return {};
      }

      const storedValue = window.localStorage.getItem(CATEGORY_LEARNING_STORAGE_KEY);

      if (!storedValue) {
        return {};
      }

      try {
        return JSON.parse(storedValue) as SenderCategoryLearningStore;
      } catch {
        return {};
      }
    });
  const [messageOwnershipInteractions, setMessageOwnershipInteractions] =
    useState<MessageOwnershipInteractionStore>(() => {
      if (typeof window === "undefined") {
        return {};
      }

      const storedValue = window.localStorage.getItem(MESSAGE_OWNERSHIP_STORAGE_KEY);

      if (!storedValue) {
        return {};
      }

      try {
        return JSON.parse(storedValue) as MessageOwnershipInteractionStore;
      } catch {
        return {};
      }
    });
  const [mailboxStore, setMailboxStore] = useState<MailboxStore>(() =>
    normalizeMailboxStore(
      createInitialMailboxStore(
        orderedMailboxes,
        senderCategoryLearning,
        messageOwnershipInteractions,
        currentWorkspaceUserId,
        workspaceDataMode,
      ),
      orderedMailboxes,
      senderCategoryLearning,
      messageOwnershipInteractions,
      currentWorkspaceUserId,
    ),
  );
  const [messageUnreadOverrides, setMessageUnreadOverrides] =
    useState<MessageUnreadOverrideStore>(() => {
      if (typeof window === "undefined") {
        return {};
      }

      const storedValue = window.localStorage.getItem(messageUnreadOverridesStorageKey);

      if (!storedValue) {
        return {};
      }

      try {
        return JSON.parse(storedValue) as MessageUnreadOverrideStore;
      } catch {
        return {};
      }
    });
  const syncUnreadOverrides = (
    messages: MessageIdentitySource[],
    unread: boolean,
  ) => {
    if (messages.length === 0) {
      return;
    }

    setMessageUnreadOverrides((current: MessageUnreadOverrideStore) => {
      const nextOverrides = { ...current };

      messages.forEach((message) => {
        getCanonicalMessageIdentityKeys(message).forEach((key) => {
          nextOverrides[key] = unread;
        });
      });

      return nextOverrides;
    });
  };
  const buildInboxIdentityIndexes = (messages: MailMessage[]) => ({
    byId: new Map(messages.map((message) => [message.id, message])),
    byImapUid: new Map(
      messages
        .filter((message) => Boolean(message.imapUid))
        .map((message) => [message.imapUid as string, message]),
    ),
    byPreviewIdentity: new Map(
      messages.map((message) => [buildStablePreviewIdentity(message), message]),
    ),
  });
  const mergeLiveInboxMessages = (
    mailboxId: InboxId,
    incomingMessages: LiveInboxMessageSnapshot[],
    currentInboxMessages: MailMessage[],
    currentStore: MailboxStore,
  ) => {
    const currentInboxIndexes = buildInboxIdentityIndexes(currentInboxMessages);

    return incomingMessages.map((message) => {
      const existingMessage = findMatchingMessageByIdentity(message, currentInboxIndexes);
      const unread =
        resolveUnreadOverride(messageUnreadOverrides, message) ??
        existingMessage?.unread ??
        message.unread;

      return normalizeMailMessage(
        {
          id: message.id,
          sender: message.sender,
          subject: message.subject,
          snippet: message.snippet,
          time: message.timestamp,
          createdAt: message.createdAt,
          imapUid: message.imapUid,
          unread,
          ui_signal: message.ui_signal,
          from: message.from,
          to: message.to,
          cc: message.cc,
          timestamp: message.timestamp,
          body: message.body,
        },
        mailboxId,
        senderCategoryLearning,
        messageOwnershipInteractions,
        currentWorkspaceUserId,
        currentStore,
      );
    });
  };
  const primaryInboxEmailCount = getMailboxFolderBadgeCount(
    mailboxStore[orderedMailboxes[0]?.id ?? "main"],
    "Inbox",
  );
  const connectedInboxCount = savedManagedInboxes.filter(
    (mailbox) => mailbox.connected,
  ).length;
  const [mailboxResetToken, setMailboxResetToken] = useState(0);
  const [activeSection, setActiveSection] =
    useState<WorkspaceSection>("Dashboard");
  const [reviewFilter, setReviewFilter] = useState<ReviewFilter>("All priority");
  const reviewController = useReviewModuleState(workspaceDataMode);
  const [inboxFilter, setInboxFilter] = useState<InboxFilter>("All inboxes");
  const [forYouContext, setForYouContext] = useState<ForYouContext>("Main");
  const [activeTarget, setActiveTarget] = useState<WorkspaceTarget | null>(null);
  const [activeMailbox, setActiveMailbox] = useState<OrderedMailbox | null>(null);
  const [mailboxReturnContext, setMailboxReturnContext] =
    useState<MailboxReturnContext | null>(null);
  const [learningLaunchRequest, setLearningLaunchRequest] =
    useState<LearningLaunchRequest>(null);
  const [syncingMailboxId, setSyncingMailboxId] = useState<InboxId | null>(null);
  const [workspaceName, setWorkspaceName] = useState("Cuevion Studio");
  const [inboxSignatures, setInboxSignatures] = useState<InboxSignatureStore>(() => {
    if (typeof window === "undefined") {
      return {};
    }

    const storedValue = window.localStorage.getItem(MAIL_SIGNATURES_STORAGE_KEY);

    if (!storedValue) {
      return {};
    }

    try {
      const parsed = JSON.parse(storedValue) as InboxSignatureStore;

      return Object.fromEntries(
        Object.entries(parsed).map(([inboxId, value]) => [
          inboxId,
          normalizeInboxSignatureSettings(value),
        ]),
      ) as InboxSignatureStore;
    } catch {
      return {};
    }
  });
  const [inboxOutOfOffice, setInboxOutOfOffice] = useState<InboxOutOfOfficeStore>(() => {
    if (typeof window === "undefined") {
      return {};
    }

    const storedValue = window.localStorage.getItem(MAIL_OUT_OF_OFFICE_STORAGE_KEY);

    if (!storedValue) {
      return {};
    }

    try {
      const parsed = JSON.parse(storedValue) as InboxOutOfOfficeStore;

      return Object.fromEntries(
        Object.entries(parsed).map(([inboxId, value]) => [
          inboxId,
          normalizeInboxOutOfOfficeSettings(value),
        ]),
      ) as InboxOutOfOfficeStore;
    } catch {
      return {};
    }
  });
  const [outOfOfficeReplyLog, setOutOfOfficeReplyLog] = useState<OutOfOfficeReplyLogStore>(() => {
    if (typeof window === "undefined") {
      return {};
    }

    const storedValue = window.localStorage.getItem(OUT_OF_OFFICE_REPLY_LOG_STORAGE_KEY);

    if (!storedValue) {
      return {};
    }

    try {
      return normalizeOutOfOfficeReplyLogStore(
        JSON.parse(storedValue) as OutOfOfficeReplyLogStore,
      );
    } catch {
      return {};
    }
  });
  const [smartFolders, setSmartFolders] = useState<SmartFolderDefinition[]>(() => {
    if (typeof window === "undefined") {
      return [];
    }

    const storedValue = window.localStorage.getItem(SMART_FOLDERS_STORAGE_KEY);

    if (!storedValue) {
      return [];
    }

    try {
      return JSON.parse(storedValue) as SmartFolderDefinition[];
    } catch {
      return [];
    }
  });
  const [lastViewedGuidance, setLastViewedGuidance] = useState<string | null>(null);
  const [aiSuggestionsEnabled, setAiSuggestionsEnabled] = useState(() => {
    if (typeof window === "undefined") {
      return true;
    }

    const storedValue = window.localStorage.getItem(AI_SUGGESTIONS_STORAGE_KEY);

    if (storedValue === null) {
      return true;
    }

    return storedValue === "true";
  });
  const [inboxChangesEnabled, setInboxChangesEnabled] = useState(() => {
    if (typeof window === "undefined") {
      return true;
    }

    const storedValue = window.localStorage.getItem(INBOX_CHANGES_STORAGE_KEY);

    if (storedValue === null) {
      return true;
    }

    return storedValue === "true";
  });
  const [teamActivityEnabled, setTeamActivityEnabled] = useState(() => {
    if (typeof window === "undefined") {
      return true;
    }

    const storedValue = window.localStorage.getItem(TEAM_ACTIVITY_STORAGE_KEY);

    if (storedValue === null) {
      return true;
    }

    return storedValue === "true";
  });
  const [pendingTeamInvitation, setPendingTeamInvitation] =
    useState<PendingTeamInvitation>(() =>
      isDemoWorkspace
        ? {
            inviter: "Emma Stone",
            accessLevel: "Review",
            selectedInboxes: ["Demo inbox"],
          }
        : null,
    );
  const [memberOfEntries, setMemberOfEntries] = useState<TeamMembershipEntry[]>([]);
  const [isSmartFolderModalOpen, setIsSmartFolderModalOpen] = useState(false);
  const [editingSmartFolderId, setEditingSmartFolderId] = useState<string | null>(null);
  const [smartFolderDraftName, setSmartFolderDraftName] = useState("");
  const [smartFolderDraftScope, setSmartFolderDraftScope] = useState<"all" | "selected">("all");
  const [smartFolderDraftSelectedInboxIds, setSmartFolderDraftSelectedInboxIds] = useState<
    InboxId[]
  >([]);
  const [smartFolderDraftRules, setSmartFolderDraftRules] = useState<SmartFolderRule[]>([
    createEmptySmartFolderRule(),
  ]);
  const workspaceModalHostRef = useRef<HTMLDivElement | null>(null);
  const seenIncomingMessageIdsRef = useRef<Set<string>>(new Set());
  const isInboxView = activeMailbox !== null;
  const usesExpandedInboxWorkspaceLayout =
    isInboxView || activeSection === "Inboxes" || activeSection === "Priority";
  const workspaceOuterShellClass = usesExpandedInboxWorkspaceLayout
    ? "px-0.5 pt-0 pb-0 sm:px-1.5 sm:pt-0.5 sm:pb-0.5 md:px-2.5 md:pt-1 md:pb-1 xl:px-3 xl:pt-1.5 xl:pb-1"
    : "px-4 py-8 md:px-8 md:py-10";
  const workspaceContentRailClass = usesExpandedInboxWorkspaceLayout
    ? "max-w-[2280px] md:pl-[100px] xl:pl-[324px]"
    : "max-w-[1880px] md:pl-[112px] xl:pl-[344px]";

  useEffect(() => {
    if (lastOnboardingMailboxSeedKeyRef.current === onboardingMailboxSeedKey) {
      return;
    }

    lastOnboardingMailboxSeedKeyRef.current = onboardingMailboxSeedKey;
    setSavedManagedInboxes(buildManagedWorkspaceInboxes(onboardingState));
  }, [onboardingMailboxSeedKey, onboardingState]);

  useEffect(() => {
    window.localStorage.setItem(
      MANAGED_INBOXES_STORAGE_KEY,
      JSON.stringify(savedManagedInboxes),
    );
  }, [savedManagedInboxes]);

  useEffect(() => {
    window.localStorage.setItem(
      MAILBOX_TITLE_OVERRIDES_STORAGE_KEY,
      JSON.stringify(mailboxTitleOverrides),
    );
  }, [mailboxTitleOverrides]);

  useEffect(() => {
    if (!activeMailbox) {
      return;
    }

    const managedMailbox = savedManagedInboxes.find(
      (mailbox) => mailbox.id === activeMailbox.id,
    );

    if (
      !managedMailbox ||
      !managedMailbox.connected ||
      !managedMailbox.provider ||
      !isImapCredentialsProvider(managedMailbox.provider)
    ) {
      return;
    }

    void refreshMailboxById(activeMailbox.id);
  }, [activeMailbox, savedManagedInboxes]);

  useEffect(() => {
    if (!activeMailbox) {
      return;
    }

    const managedMailbox = savedManagedInboxes.find(
      (mailbox) => mailbox.id === activeMailbox.id,
    );

    if (
      !managedMailbox ||
      !managedMailbox.connected ||
      !managedMailbox.provider ||
      !isImapCredentialsProvider(managedMailbox.provider)
    ) {
      return;
    }

    const intervalId = window.setInterval(() => {
      void refreshMailboxById(activeMailbox.id);
    }, ACTIVE_MAILBOX_AUTO_REFRESH_INTERVAL_MS);

    return () => window.clearInterval(intervalId);
  }, [activeMailbox, savedManagedInboxes, syncingMailboxId]);
  const workspaceShellPaddingClass = usesExpandedInboxWorkspaceLayout
    ? "p-3 md:p-4 lg:p-5"
    : "p-6 md:p-8 lg:p-10";
  const workspaceShellSurfaceClass = usesExpandedInboxWorkspaceLayout
    ? "rounded-[30px] border border-transparent bg-transparent shadow-none"
    : "rounded-[36px] border border-[var(--workspace-shell-border)] bg-[var(--workspace-shell)] shadow-panel";
  const [inviteDecisionState, setInviteDecisionState] = useState<
    "pending" | "declined" | "left"
  >("pending");
  const [inviteReplyDraft, setInviteReplyDraft] = useState("");
  const [inviteReplyVisibility, setInviteReplyVisibility] =
    useState<MailMessageCollaborationVisibility>(
      authenticatedUser?.userType === "guest" ? "shared" : "internal",
    );
  const [inviteMentionIndex, setInviteMentionIndex] = useState(0);
  const [inviteReplySelection, setInviteReplySelection] = useState<number | null>(null);
  const inviteReplyInputRef = useRef<HTMLTextAreaElement | null>(null);
  const [notificationNavigationRequest, setNotificationNavigationRequest] =
    useState<NotificationNavigationRequest | null>(null);
  const [reviewInboxHandoff, setReviewInboxHandoff] = useState<ReviewInboxHandoff | null>(null);
  const [reviewInboxHandoffFeedback, setReviewInboxHandoffFeedback] = useState<string | null>(null);
  const [completedPriorityReviewIds, setCompletedPriorityReviewIds] = useState<string[]>([]);
  const [manualPriorityOverrides, setManualPriorityOverrides] = useState<
    Partial<Record<string, ManualPriorityOverride>>
  >(() => {
    if (typeof window === "undefined") {
      return {};
    }

    const storedValue = window.localStorage.getItem(manualPriorityOverridesStorageKey);

    if (!storedValue) {
      return {};
    }

    try {
      return JSON.parse(storedValue) as Partial<Record<string, ManualPriorityOverride>>;
    } catch {
      return {};
    }
  });
  const isGuestInviteUser = authenticatedUser?.userType === "guest";

  const updateWorkspaceMessageById = (
    messageId: string,
    updater: (message: MailMessage) => MailMessage,
  ) => {
    setMailboxStore((currentStore) =>
      Object.entries(currentStore).reduce<MailboxStore>((nextStore, [mailboxId, collections]) => {
        nextStore[mailboxId as InboxId] = {
          Inbox: collections.Inbox.map((message) =>
            message.id === messageId ? updater(message) : message,
          ),
          Drafts: collections.Drafts.map((message) =>
            message.id === messageId ? updater(message) : message,
          ),
          Sent: collections.Sent.map((message) =>
            message.id === messageId ? updater(message) : message,
          ),
          Archive: collections.Archive.map((message) =>
            message.id === messageId ? updater(message) : message,
          ),
          Filtered: collections.Filtered.map((message) =>
            message.id === messageId ? updater(message) : message,
          ),
          Spam: collections.Spam.map((message) =>
            message.id === messageId ? updater(message) : message,
          ),
          Trash: collections.Trash.map((message) =>
            message.id === messageId ? updater(message) : message,
          ),
        };

        return nextStore;
      }, {} as MailboxStore),
    );
  };

  const getWorkspaceMessageById = (messageId: string) =>
    Object.values(mailboxStore)
      .flatMap((collections) => canonicalFolderOrder.flatMap((folder) => collections[folder]))
      .find((message) => message.id === messageId) ?? null;
  const getWorkspaceMessageLocationById = (messageId: string) => {
    for (const mailbox of orderedMailboxes) {
      const mailboxCollections = mailboxStore[mailbox.id];

      if (!mailboxCollections) {
        continue;
      }

      for (const folder of canonicalFolderOrder) {
        if (mailboxCollections[folder].some((message) => message.id === messageId)) {
          return { mailboxId: mailbox.id, folder };
        }
      }
    }

    return null;
  };
  const getLinkedReviewForMessage = (messageId: string) =>
    reviewController.getReviewBySourceId(messageId);
  const getLinkedReviewBadgeLabel = (_messageId: string) => null;
  const livePriorityInboxEntries = orderedMailboxes.flatMap((candidate) =>
    (mailboxStore[candidate.id]?.Inbox ?? [])
      .filter((message) =>
        isLiveInboxPriorityMessage(message, manualPriorityOverrides[message.id])
      )
      .map((message) => ({
        mailboxId: candidate.id,
        mailboxTitle: candidate.title,
        message,
      })),
  );
  const livePriorityInboxItems: ReviewItem[] = livePriorityInboxEntries.map(
    ({ mailboxId, mailboxTitle, message }) => ({
      id: `live-priority-${mailboxId}-${message.id}`,
      target: "demo-review",
      type: "business_context_review",
      title: message.subject,
      subtitle: `${message.sender} · ${message.from}`,
      description: "Priority message from your live Inbox.",
      status: "needs_decision",
      owner: mailboxTitle,
      nextStep: "Open this thread in Inbox.",
      highlights: [message.snippet],
      relatedItems: [],
      primaryAction: {
        id: `live-priority-open-${mailboxId}-${message.id}`,
        label: "Open email",
        kind: "open_full_review",
      },
      sourceType: "mail_message",
      sourceId: message.id,
      linkedEntityIds: [],
      createdAt: message.createdAt ?? message.timestamp,
      updatedAt: message.createdAt ?? message.timestamp,
    }),
  );
  const hiddenPriorityReviewIds = reviewController.store.items.map((item) => item.id);
  const priorityDisplayOverrides = Object.fromEntries(
    livePriorityInboxEntries.map(({ mailboxId, message }) => [
      `live-priority-${mailboxId}-${message.id}`,
      {
        sender: `${message.sender} · ${message.from}`,
        subject: message.subject,
        context: message.snippet,
      },
    ]),
  );

  const decodedInvitePayload = collaborationInviteRoute
    ? decodeCollaborationInviteToken(collaborationInviteRoute.inviteToken)
    : null;
  const decodedInviteMessage = decodedInvitePayload?.message ?? null;
  const storedInviteMessage = collaborationInviteRoute
    ? Object.values(mailboxStore)
        .flatMap((collections) => canonicalFolderOrder.flatMap((folder) => collections[folder]))
        .find((message) => message.id === collaborationInviteRoute.messageId) ?? null
    : null;
  const inviteMessage = storedInviteMessage?.collaboration
    ? storedInviteMessage
    : decodedInviteMessage ?? storedInviteMessage;
  const inviteCollaboration = inviteMessage?.collaboration ?? null;
  const inviteParticipants = inviteCollaboration
    ? getCollaborationParticipants(inviteCollaboration)
    : [];
  const acceptedInviteParticipant = authenticatedUser
    ? (inviteCollaboration?.participants ?? []).find(
        (participant) =>
          participant.email.toLowerCase() === authenticatedUser.email.toLowerCase() &&
          participant.status === "active",
      ) ?? null
    : null;
  const declinedInviteParticipant = authenticatedUser
    ? (inviteCollaboration?.participants ?? []).find(
        (participant) =>
          participant.email.toLowerCase() === authenticatedUser.email.toLowerCase() &&
          participant.status === "declined",
      ) ?? null
    : null;
  const hasAlreadyJoinedInvite = Boolean(
    authenticatedUser && acceptedInviteParticipant,
  );
  const inviteRouteState = !collaborationInviteRoute
    ? null
    : collaborationInviteRoute.status === "expired"
      ? "expired"
      : !collaborationInviteRoute.messageId || !collaborationInviteRoute.inviteeEmail
        ? "invalid"
        : !inviteMessage || !inviteCollaboration
          ? "unavailable"
          : !authenticatedUser ||
              authenticatedUser.email.toLowerCase() !==
                collaborationInviteRoute.inviteeEmail.toLowerCase()
            ? "forbidden"
            : inviteDecisionState === "left"
              ? "left"
              : inviteDecisionState === "declined" || declinedInviteParticipant
                ? "declined"
              : isGuestInviteUser
                ? "joined"
                : hasAlreadyJoinedInvite
                    ? "joined"
                    : "accept";

  const joinCollaborationFromInvite = () => {
    if (!collaborationInviteRoute || !authenticatedUser || !inviteMessage?.collaboration) {
      return;
    }

    const nextTimestamp = Date.now();

    updateWorkspaceMessageById(inviteMessage.id, (message) => {
      if (!message.collaboration) {
        return message;
      }

      return {
        ...message,
        isShared: true,
        collaboration: {
          ...message.collaboration,
          updatedAt: nextTimestamp,
          participants: (() => {
            const existingParticipants = message.collaboration?.participants ?? [];
            const existingIndex = existingParticipants.findIndex(
              (participant) =>
                participant.email.toLowerCase() === authenticatedUser.email.toLowerCase(),
            );

            if (existingIndex >= 0) {
              return existingParticipants.map((participant, index) =>
                index === existingIndex
                  ? {
                      ...participant,
                      id:
                        participant.id || normalizeSenderLearningKey(authenticatedUser.email),
                      name: authenticatedUser.name,
                      email: authenticatedUser.email,
                      kind:
                        authenticatedUser.userType === "guest" ? "external" : "internal",
                      status: "active",
                    }
                  : participant,
              );
            }

            return [
              ...existingParticipants,
              {
                id: normalizeSenderLearningKey(authenticatedUser.email),
                name: authenticatedUser.name,
                email: authenticatedUser.email,
                kind: authenticatedUser.userType === "guest" ? "external" : "internal",
                status: "active",
              },
            ];
          })(),
        },
      };
    });
  };

  const declineCollaborationInvite = () => {
    if (!inviteMessage?.collaboration || !authenticatedUser) {
      setInviteDecisionState("declined");
      return;
    }

    updateWorkspaceMessageById(inviteMessage.id, (message) =>
      message.collaboration
        ? {
            ...message,
            collaboration: {
              ...message.collaboration,
              updatedAt: Date.now(),
              participants: (message.collaboration.participants ?? []).map((participant) =>
                participant.email.toLowerCase() === authenticatedUser.email.toLowerCase()
                  ? {
                      ...participant,
                      status: "declined",
                    }
                  : participant,
              ),
            },
          }
        : message,
    );

    setInviteDecisionState("declined");
  };

  const sendInviteFlowReply = () => {
    const trimmedReply = inviteReplyDraft.trim();

    if (!trimmedReply || !inviteMessage?.collaboration || !authenticatedUser) {
      return;
    }

    const nextTimestamp = Date.now();
    const mentionCandidates = getCollaborationMentionTargets(inviteParticipants, []);
    const mentions = extractCollaborationMentions(
      trimmedReply,
      mentionCandidates,
      normalizeSenderLearningKey(authenticatedUser.email),
    );

    updateWorkspaceMessageById(inviteMessage.id, (message) =>
      message.collaboration
        ? {
            ...message,
            isShared: true,
            collaboration: {
              ...message.collaboration,
              state:
                message.collaboration.state === "resolved"
                  ? "needs_review"
                  : message.collaboration.state,
              updatedAt: nextTimestamp,
              previewText: trimmedReply,
              messages: [
                ...message.collaboration.messages,
                {
                  id: `${inviteMessage.id}-invite-reply-${nextTimestamp}`,
                  authorId: normalizeSenderLearningKey(authenticatedUser.email),
                  authorName: authenticatedUser.name,
                  text: trimmedReply,
                  timestamp: nextTimestamp,
                  visibility: inviteReplyVisibility,
                  mentions,
                },
              ],
            },
          }
        : message,
    );

    setInviteReplyDraft("");
    setInviteReplyVisibility(isGuestInviteUser ? "shared" : "internal");
    setInviteMentionIndex(0);
    setInviteReplySelection(null);
  };

  const syncInviteMentionState = (
    value: string,
    textarea: HTMLTextAreaElement | null,
  ) => {
    setInviteReplySelection(textarea?.selectionStart ?? null);
    const nextQuery = getMentionQueryAtCursor(value, textarea?.selectionStart ?? null);

    if (!nextQuery) {
      setInviteMentionIndex(0);
      return;
    }

    const nextMatches = getCollaborationMentionTargets(inviteParticipants, []).filter(
      (candidate) => candidate.handle.toLowerCase().includes(nextQuery.query.toLowerCase()),
    );

    if (nextMatches.length === 0) {
      setInviteMentionIndex(0);
      return;
    }

    setInviteMentionIndex((current) => Math.min(current, nextMatches.length - 1));
  };

  const applyInviteMention = (candidate: ReturnType<typeof getCollaborationMentionTargets>[number]) => {
    const textarea = inviteReplyInputRef.current;
    const selection = getMentionQueryAtCursor(inviteReplyDraft, textarea?.selectionStart ?? null);

    if (!selection) {
      return;
    }

    const nextValue = `${inviteReplyDraft.slice(0, selection.start)}@${
      candidate.handle
    } ${inviteReplyDraft.slice(selection.end)}`;

    setInviteReplyDraft(nextValue);
    setInviteMentionIndex(0);
    setInviteReplySelection(selection.start + candidate.handle.length + 2);

    requestAnimationFrame(() => {
      if (!textarea) {
        return;
      }

      const nextCursorPosition = selection.start + candidate.handle.length + 2;
      textarea.focus();
      textarea.setSelectionRange(nextCursorPosition, nextCursorPosition);
    });
  };

  const markInviteFlowDone = () => {
    if (!inviteMessage?.collaboration || !authenticatedUser) {
      return;
    }

    const nextTimestamp = Date.now();

    updateWorkspaceMessageById(inviteMessage.id, (message) =>
      message.collaboration
        ? {
            ...message,
            isShared: false,
            collaboration: {
              ...message.collaboration,
              state: "resolved",
              updatedAt: nextTimestamp,
              resolvedAt: nextTimestamp,
              resolvedByUserId: normalizeSenderLearningKey(authenticatedUser.email),
              resolvedByUserName: authenticatedUser.name,
            },
          }
        : message,
    );
  };

  const leaveCollaborationFromInvite = () => {
    if (!inviteMessage?.collaboration || !authenticatedUser) {
      return;
    }

    updateWorkspaceMessageById(inviteMessage.id, (message) =>
      message.collaboration
        ? {
            ...message,
            collaboration: {
              ...message.collaboration,
              participants: (message.collaboration.participants ?? []).filter(
                (participant) =>
                  participant.email.toLowerCase() !== authenticatedUser.email.toLowerCase(),
              ),
            },
          }
        : message,
    );

    setInviteDecisionState("left");
    setInviteReplyDraft("");
    setInviteReplyVisibility(isGuestInviteUser ? "shared" : "internal");
    setInviteReplySelection(null);
  };

  useEffect(() => {
    if (
      !collaborationInviteRoute ||
      storedInviteMessage ||
      !decodedInvitePayload?.message ||
      !orderedMailboxes[0]
    ) {
      return;
    }

    if (isLocalDevelopmentEnvironment()) {
      console.debug("cuevion_invite_token_resolved", {
        inviteToken: collaborationInviteRoute.inviteToken,
        inviteeEmail: decodedInvitePayload.inviteeEmail,
        messageId: decodedInvitePayload.message.id,
      });
    }

    setMailboxStore((currentStore) => {
      const mailboxId = orderedMailboxes[0].id;
      const mailboxCollections = currentStore[mailboxId];

      if (!mailboxCollections) {
        return currentStore;
      }

      const alreadyExists = canonicalFolderOrder.some((folder) =>
        currentStore[mailboxId][folder].some(
          (message) => message.id === decodedInvitePayload.message.id,
        ),
      );

      if (alreadyExists) {
        return currentStore;
      }

      return {
        ...currentStore,
        [mailboxId]: {
          ...mailboxCollections,
          Inbox: [decodedInvitePayload.message, ...mailboxCollections.Inbox],
        },
      };
    });
  }, [collaborationInviteRoute, decodedInvitePayload, orderedMailboxes, storedInviteMessage]);

  useEffect(() => {
    if (!activeMailbox) {
      return;
    }

    const syncedMailbox = orderedMailboxes.find((mailbox) => mailbox.id === activeMailbox.id);

    if (!syncedMailbox) {
      return;
    }

    if (
      syncedMailbox.title !== activeMailbox.title ||
      syncedMailbox.email !== activeMailbox.email
    ) {
      setActiveMailbox(syncedMailbox);
    }
  }, [activeMailbox, orderedMailboxes]);

  useEffect(() => {
    if (!reviewInboxHandoffFeedback) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      setReviewInboxHandoffFeedback(null);
    }, 2800);

    return () => window.clearTimeout(timeoutId);
  }, [reviewInboxHandoffFeedback]);

  useEffect(() => {
    if (!reviewInboxHandoff) {
      return;
    }

    const sourceMessage = getWorkspaceMessageById(reviewInboxHandoff.messageId);
    const sourceLocation = getWorkspaceMessageLocationById(reviewInboxHandoff.messageId);

    if (!sourceMessage || !sourceLocation) {
      return;
    }

    const replyDetected =
      reviewInboxHandoff.threadId !== null &&
      (mailboxStore[reviewInboxHandoff.mailboxId]?.Sent ?? []).some(
        (message) =>
          message.threadId === reviewInboxHandoff.threadId &&
          Date.parse(message.createdAt ?? "") >= Date.parse(reviewInboxHandoff.startedAt),
      );
    const archiveDetected =
      sourceLocation.folder === "Archive" && reviewInboxHandoff.initialFolder !== "Archive";
    const categoryChanged = (sourceMessage.category ?? null) !== reviewInboxHandoff.initialCategory;
    const completionDetected =
      reviewInboxHandoff.source === "priority-list"
        ? replyDetected
        : replyDetected || archiveDetected || categoryChanged;

    if (!completionDetected) {
      return;
    }

    const linkedReview = reviewController.store.items.find(
      (item) => item.id === reviewInboxHandoff.reviewId,
    );

    if (linkedReview) {
      const reviewClosed = reviewController.closeFromInboxAction(reviewInboxHandoff.reviewId);

      if (!reviewClosed) {
        return;
      }
    }

    if (reviewInboxHandoff.source === "priority-list") {
      setCompletedPriorityReviewIds((current) =>
        current.includes(reviewInboxHandoff.reviewId)
          ? current
          : [...current, reviewInboxHandoff.reviewId],
      );
    }

    setReviewInboxHandoff(null);
    setReviewInboxHandoffFeedback(
      reviewInboxHandoff.source === "priority-list"
        ? "Reply sent. Removed from Priority."
        : "Action detected. Item closed.",
    );
  }, [mailboxStore, reviewController, reviewInboxHandoff]);

  useEffect(() => {
    if (isGuestInviteUser && inviteReplyVisibility !== "shared") {
      setInviteReplyVisibility("shared");
    }
  }, [inviteReplyVisibility, isGuestInviteUser]);

  useEffect(() => {
    if (
      !isGuestInviteUser ||
      inviteDecisionState === "left" ||
      !authenticatedUser ||
      !inviteMessage?.collaboration ||
      !collaborationInviteRoute?.inviteeEmail ||
      collaborationInviteRoute.status === "expired" ||
      authenticatedUser.email.toLowerCase() !==
        collaborationInviteRoute.inviteeEmail.toLowerCase() ||
      hasAlreadyJoinedInvite
    ) {
      return;
    }

    joinCollaborationFromInvite();
  }, [
    authenticatedUser,
    collaborationInviteRoute,
    hasAlreadyJoinedInvite,
    inviteDecisionState,
    inviteMessage,
    isGuestInviteUser,
  ]);

  const captureMailboxReturnContext = (): MailboxReturnContext => ({
    section: activeSection,
    reviewFilter,
    inboxFilter,
    forYouContext,
    target: activeTarget,
    mailboxId: activeMailbox?.id ?? null,
  });

  const openMailboxFromContext = (
    targetMailbox: OrderedMailbox,
    options?: { preserveCurrentContext?: boolean },
  ) => {
    if (options?.preserveCurrentContext ?? true) {
      setMailboxReturnContext(captureMailboxReturnContext());
    }

    setActiveTarget(null);
    setActiveSection("Inboxes");
    setActiveMailbox(targetMailbox);
  };

  const handleReturnFromMailbox = () => {
    if (!mailboxReturnContext) {
      setActiveMailbox(null);
      return;
    }

    setReviewFilter(mailboxReturnContext.reviewFilter);
    setInboxFilter(mailboxReturnContext.inboxFilter);
    setForYouContext(mailboxReturnContext.forYouContext);
    setActiveSection(mailboxReturnContext.section);
    setActiveTarget(mailboxReturnContext.target);
    setActiveMailbox(
      mailboxReturnContext.mailboxId
        ? orderedMailboxes.find((mailbox) => mailbox.id === mailboxReturnContext.mailboxId) ??
            null
        : null,
    );
    setMailboxReturnContext(null);
  };

  const handleChangeSection = (section: WorkspaceSection) => {
    setActiveSection(section);
    setActiveTarget(null);
    setActiveMailbox(null);
    setMailboxReturnContext(null);
  };

  const handleOpenPriority = (filter: ReviewFilter) => {
    setReviewFilter(filter);
    setActiveSection("Priority");
    setActiveTarget(null);
    setActiveMailbox(null);
    setMailboxReturnContext(null);
  };

  const handleSetManualPriority = (messageId: string, shouldBePriority: boolean) => {
    const sourceMessage = getWorkspaceMessageById(messageId);

    if (!sourceMessage) {
      return;
    }

    const baseIsPriority = isMessageVisiblePriority(sourceMessage);

    setManualPriorityOverrides((current) => {
      const next = { ...current };

      if (shouldBePriority) {
        if (baseIsPriority) {
          delete next[messageId];
        } else {
          next[messageId] = "priority";
        }
      } else if (baseIsPriority) {
        next[messageId] = "removed";
      } else {
        delete next[messageId];
      }

      return next;
    });

    if (shouldBePriority) {
      const linkedReviewId = reviewController.getReviewBySourceId(messageId)?.id ?? null;

      setCompletedPriorityReviewIds((current) =>
        current.filter(
          (reviewId) =>
            reviewId !== `manual-priority-${messageId}` && reviewId !== linkedReviewId,
        ),
      );
    }
  };

  const handleOpenInboxes = (filter: InboxFilter) => {
    setInboxFilter(filter);
    setActiveSection("Inboxes");
    setActiveTarget(null);
    setActiveMailbox(null);
    setMailboxReturnContext(null);
  };

  const handleOpenForYou = (context: ForYouContext) => {
    setForYouContext(context);
    setActiveSection("For You");
    setActiveTarget(null);
    setActiveMailbox(null);
    setMailboxReturnContext(null);
  };

  const handleOpenLearningRequest = (
    request: NonNullable<LearningLaunchRequest>,
  ) => {
    setForYouContext("Main");
    setActiveSection("For You");
    setActiveTarget(null);
    setActiveMailbox(null);
    setMailboxReturnContext(null);
    setLearningLaunchRequest(request);
  };

  const handleOpenDemoInbox = () => {
    const demoMailbox = orderedMailboxes.find((mailbox) => mailbox.id === "demo");

    if (!demoMailbox) {
      return;
    }

    openMailboxFromContext(demoMailbox);
  };

  const handleOpenSenderContext = () => {
    const primaryMailbox = orderedMailboxes[0];

    if (!primaryMailbox) {
      return;
    }

    openMailboxFromContext(primaryMailbox);
  };

  const handleOpenNotificationNavigation = (
    request: Omit<NotificationNavigationRequest, "requestKey">,
  ) => {
    const targetMailbox = orderedMailboxes.find((mailbox) => mailbox.id === request.mailboxId);
    const targetMessage = getWorkspaceMessageById(request.messageId);

    if (!targetMailbox || !targetMessage) {
      return;
    }

    if (
      request.type === "invite" &&
      request.inviteeEmail &&
      authenticatedUser?.email.toLowerCase() === request.inviteeEmail.toLowerCase() &&
      targetMessage.collaboration &&
      !getCollaborationParticipants(targetMessage.collaboration).some(
        (participant) =>
          participant.email.toLowerCase() === request.inviteeEmail?.toLowerCase() &&
          participant.status === "active",
      )
    ) {
      window.location.assign(
        buildCollaborationInviteLink(targetMessage, request.inviteeEmail),
      );
      return;
    }

    openMailboxFromContext(targetMailbox);
    setNotificationNavigationRequest({
      ...request,
      requestKey: Date.now(),
    });
  };

  const openReviewItemInInbox = (
    reviewItem: ReviewItem,
    options?: {
      focusReplyComposer?: boolean;
      source?: ReviewInboxHandoff["source"];
    },
  ) => {
    const sourceMessage = getWorkspaceMessageById(reviewItem.sourceId);
    const sourceLocation = getWorkspaceMessageLocationById(reviewItem.sourceId);
    const targetMailbox = sourceLocation
      ? orderedMailboxes.find((mailbox) => mailbox.id === sourceLocation.mailboxId) ?? null
      : null;

    if (!sourceMessage || !sourceLocation || !targetMailbox) {
      setReviewInboxHandoffFeedback("Linked source thread is unavailable.");
      return;
    }

    setReviewInboxHandoff({
      reviewId: reviewItem.id,
      messageId: reviewItem.sourceId,
      mailboxId: sourceLocation.mailboxId,
      threadId: sourceMessage.threadId ?? null,
      initialFolder: sourceLocation.folder,
      initialCategory: sourceMessage.category ?? null,
      startedAt: new Date().toISOString(),
      source: options?.source ?? "review-detail",
    });
    openMailboxFromContext(targetMailbox);
    setNotificationNavigationRequest({
      mailboxId: sourceLocation.mailboxId,
      messageId: reviewItem.sourceId,
      type: "reply",
      focusReplyComposer: options?.focusReplyComposer ?? false,
      requestKey: Date.now(),
    });
  };

  const handleReviewHandleNow = (reviewItem: ReviewItem) => {
    openReviewItemInInbox(reviewItem, {
      focusReplyComposer: true,
      source: "review-detail",
    });
  };

  const handleOpenPriorityItem = (reviewItem: ReviewItem) => {
    if (reviewItem.id.startsWith("live-priority-")) {
      const sourceLocation = getWorkspaceMessageLocationById(reviewItem.sourceId);
      const targetMailbox = sourceLocation
        ? orderedMailboxes.find((mailbox) => mailbox.id === sourceLocation.mailboxId) ?? null
        : null;

      if (!sourceLocation || !targetMailbox) {
        return;
      }

      openMailboxFromContext(targetMailbox);
      setNotificationNavigationRequest({
        mailboxId: sourceLocation.mailboxId,
        messageId: reviewItem.sourceId,
        type: "reply",
        source: "priority",
        focusReplyComposer: false,
        openFullMessage: true,
        requestKey: Date.now(),
      });
      return;
    }

    openReviewItemInInbox(reviewItem, { source: "priority-list" });
  };

  const handleRenameMailbox = (mailboxId: InboxId, nextTitle: string) => {
    setMailboxTitleOverrides((current) => ({
      ...current,
      [mailboxId]: nextTitle.trim(),
    }));
  };

  const handleLearnCategoryDecision = (
    senderAddress: string,
    category: CuevionMessageCategory,
  ) => {
    const senderKey = buildSenderLearningStoreKey(senderAddress, "sender");

    if (!senderKey) {
      return;
    }

    setSenderCategoryLearning((current) => {
      const existingEntry = current[senderKey];

      if (existingEntry?.learnedCategory === category) {
        return {
          ...current,
          [senderKey]: {
            learnedCategory: category,
            learnedFromCount: existingEntry.learnedFromCount + 1,
            autoCategoryEnabled: existingEntry.autoCategoryEnabled,
            mailboxAction: existingEntry.mailboxAction,
            updatedAt: new Date().toISOString(),
          },
        };
      }

      return {
        ...current,
        [senderKey]: {
          learnedCategory: category,
          learnedFromCount: 1,
          autoCategoryEnabled: existingEntry?.autoCategoryEnabled,
          mailboxAction: existingEntry?.mailboxAction,
          updatedAt: new Date().toISOString(),
        },
      };
    });
  };

  const handleEnableAutoCategoryForSender = (senderAddress: string) => {
    const senderKey = buildSenderLearningStoreKey(senderAddress, "sender");

    if (!senderKey) {
      return;
    }

    setSenderCategoryLearning((current) => {
      const existingEntry = current[senderKey];

      if (!existingEntry) {
        return current;
      }

      return {
        ...current,
        [senderKey]: {
          ...existingEntry,
          autoCategoryEnabled: true,
        },
      };
    });
  };

  const handleSaveLearningRule = (
    ruleValue: string,
    ruleType: "sender" | "domain",
    category: CuevionMessageCategory,
    mailboxAction: "keep" | "move" = category === "Primary" ? "keep" : "move",
    options?: {
      sourceContext?: LearningDecisionSourceContext;
      sourcePrioritySelection?: LearningDecisionPrioritySelection | null;
      sourceMailboxId?: InboxId | null;
      sourceCurrentMailboxId?: InboxId | null;
    },
  ) => {
    const learningKey = buildSenderLearningStoreKey(ruleValue, ruleType);

    if (!learningKey) {
      return;
    }

    setSenderCategoryLearning((current) => {
      const existingEntry = current[learningKey];

      return {
        ...current,
        [learningKey]: {
          learnedCategory: category,
          learnedFromCount: Math.max(
            existingEntry?.learnedFromCount ?? 0,
            HIGH_CONFIDENCE_LEARNING_COUNT,
          ),
          autoCategoryEnabled: existingEntry?.autoCategoryEnabled ?? true,
          mailboxAction,
          sourceContext: options?.sourceContext ?? existingEntry?.sourceContext,
          sourcePrioritySelection:
            options?.sourcePrioritySelection ?? existingEntry?.sourcePrioritySelection,
          sourceMailboxId:
            options?.sourceMailboxId !== undefined
              ? options.sourceMailboxId
              : existingEntry?.sourceMailboxId,
          sourceCurrentMailboxId:
            options?.sourceCurrentMailboxId !== undefined
              ? options.sourceCurrentMailboxId
              : existingEntry?.sourceCurrentMailboxId,
          updatedAt: new Date().toISOString(),
        },
      };
    });
  };

  const handleRecordMessageOwnershipInteraction = (messageId: string) => {
    if (!messageId || !currentWorkspaceUserId) {
      return;
    }

    setMessageOwnershipInteractions((current) => {
      const existingEntry = current[messageId];

      if (existingEntry?.userId === currentWorkspaceUserId) {
        return {
          ...current,
          [messageId]: {
            userId: currentWorkspaceUserId,
            count: existingEntry.count + 1,
          },
        };
      }

      return {
        ...current,
        [messageId]: {
          userId: currentWorkspaceUserId,
          count: 1,
        },
      };
    });
  };

  const applyLiveInboxMessagesToMailboxStore = (
    mailboxId: InboxId,
    messages: LiveInboxMessageSnapshot[],
  ) => {
    const targetMailbox = orderedMailboxes.find((entry) => entry.id === mailboxId);

    if (!targetMailbox) {
      return;
    }

    setMailboxStore((currentStore) => {
      const currentCollections =
        currentStore[targetMailbox.id] ?? createEmptyMailboxCollections();

      const nextStore = {
        ...currentStore,
        [targetMailbox.id]: {
          ...currentCollections,
          Inbox: mergeLiveInboxMessages(
            targetMailbox.id,
            messages,
            currentCollections.Inbox,
            currentStore,
          ),
        },
      };

      return normalizeMailboxStore(
        nextStore,
        orderedMailboxes,
        senderCategoryLearning,
        messageOwnershipInteractions,
        currentWorkspaceUserId,
      );
    });
  };

  const refreshMailboxById = async (mailboxId: InboxId) => {
    if (syncingMailboxId === mailboxId) {
      return;
    }

    const managedMailbox = savedManagedInboxes.find(
      (mailbox) => mailbox.id === mailboxId,
    );

    if (
      !managedMailbox ||
      !managedMailbox.connected ||
      !managedMailbox.provider ||
      !isImapCredentialsProvider(managedMailbox.provider)
    ) {
      return;
    }

    const resolvedImapSettings = applyProviderDefaults(
      managedMailbox.provider,
      managedMailbox.customImap,
      managedMailbox.email,
    );

    setSyncingMailboxId(mailboxId);

    try {
      const syncStartedAt = performance.now();
      const response = await connectInboxWithImap({
        provider: managedMailbox.provider,
        email: managedMailbox.email.trim(),
        host: resolvedImapSettings.host.trim(),
        port: resolvedImapSettings.port.trim(),
        ssl: resolvedImapSettings.ssl,
        username:
          managedMailbox.provider === "google"
            ? managedMailbox.email.trim()
            : resolvedImapSettings.username.trim(),
        password: resolvedImapSettings.password,
      });
      const requestDurationMs = performance.now() - syncStartedAt;

      if (!response.ok) {
        console.info("[SYNC-TIMING] refreshMailboxById failed", {
          mailboxId,
          email: managedMailbox.email.trim(),
          requestDurationMs: Math.round(requestDurationMs),
          error: response.error?.message ?? response.error?.code ?? "unknown",
        });
        return;
      }

      const messages = response.messages ?? [];
      const mergeStartedAt = performance.now();
      saveLiveInboxSnapshot({
        inboxId: managedMailbox.id,
        email: managedMailbox.email.trim().toLowerCase(),
        fetchedAt: new Date().toISOString(),
        messages,
      });
      applyLiveInboxMessagesToMailboxStore(managedMailbox.id as InboxId, messages);
      console.info("[SYNC-TIMING] refreshMailboxById complete", {
        mailboxId,
        email: managedMailbox.email.trim(),
        requestDurationMs: Math.round(requestDurationMs),
        mergeDurationMs: Math.round(performance.now() - mergeStartedAt),
        totalDurationMs: Math.round(performance.now() - syncStartedAt),
        messageCount: messages.length,
      });
    } finally {
      setSyncingMailboxId(null);
    }
  };

  const handleSyncActiveMailbox = async () => {
    if (!activeMailbox) {
      return;
    }

    await refreshMailboxById(activeMailbox.id);
  };

  const handleApplyManagedInboxes = (nextMailboxes: ManagedWorkspaceInbox[]) => {
    const validMailboxes = nextMailboxes
      .filter((mailbox) => isManagedInboxReady(mailbox))
      .map((mailbox) => ({
        ...cloneManagedWorkspaceInbox(mailbox),
        id: mailbox.id.trim(),
        title: mailbox.title.trim() || mailbox.email.trim() || "Custom Inbox",
        email: mailbox.email.trim(),
        connected: true,
      }))
      .filter((mailbox) => mailbox.id.length > 0);

    if (validMailboxes.length === 0) {
      return false;
    }

    setMailboxStore((currentStore) => {
      const nextStore = { ...currentStore };

      Object.keys(nextStore).forEach((mailboxId) => {
        if (!validMailboxes.some((mailbox) => mailbox.id === mailboxId)) {
          delete nextStore[mailboxId];
        }
      });

      validMailboxes.forEach((mailbox) => {
        if (!nextStore[mailbox.id]) {
          nextStore[mailbox.id] = createEmptyMailboxCollections();
        }
      });

      return nextStore;
    });
    setActiveMailbox((current) => {
      if (!current) {
        return current;
      }

      const matchingMailbox = validMailboxes.find((mailbox) => mailbox.id === current.id);

      if (matchingMailbox) {
        return {
          ...toOrderedMailboxFromManagedInbox(matchingMailbox),
          title:
            mailboxTitleOverrides[matchingMailbox.id as InboxId]?.trim() ||
            toOrderedMailboxFromManagedInbox(matchingMailbox).title,
        };
      }

      const fallbackMailbox = validMailboxes[0];

      return fallbackMailbox ? toOrderedMailboxFromManagedInbox(fallbackMailbox) : null;
    });
    setSavedManagedInboxes(validMailboxes);
    return true;
  };

  useEffect(() => {
    setMailboxStore((currentStore) => {
      const nextStore = { ...currentStore };

      orderedMailboxes.forEach((mailbox) => {
        if (!nextStore[mailbox.id]) {
          nextStore[mailbox.id] = createEmptyMailboxCollections();
        }
      });

      return normalizeMailboxStore(
        nextStore,
        orderedMailboxes,
        senderCategoryLearning,
        messageOwnershipInteractions,
        currentWorkspaceUserId,
      );
    });
    setMailboxResetToken((current) => current + 1);
  }, [mailboxOrderKey]);

  useEffect(() => {
    const snapshots = readLiveInboxSnapshots();
    const connectedSnapshots = orderedMailboxes
      .map((mailbox) => ({
        mailbox,
        snapshot: snapshots[mailbox.id],
      }))
      .filter(
        (entry): entry is {
          mailbox: OrderedMailbox;
          snapshot: NonNullable<(typeof snapshots)[string]>;
        } => Boolean(entry.snapshot?.messages.length),
      );

    if (connectedSnapshots.length === 0) {
      return;
    }

    setMailboxStore((currentStore) => {
      const nextStore = { ...currentStore };

      connectedSnapshots.forEach(({ mailbox, snapshot }) => {
        const currentCollections =
          nextStore[mailbox.id] ?? createEmptyMailboxCollections();

        nextStore[mailbox.id] = {
          ...currentCollections,
          Inbox: mergeLiveInboxMessages(
            mailbox.id,
            snapshot.messages,
            currentCollections.Inbox,
            currentStore,
          ),
        };
      });

      return normalizeMailboxStore(
        nextStore,
        orderedMailboxes,
        senderCategoryLearning,
        messageOwnershipInteractions,
        currentWorkspaceUserId,
      );
    });
  }, [
    liveMailboxSyncKey,
    messageUnreadOverrides,
    senderCategoryLearning,
    messageOwnershipInteractions,
    currentWorkspaceUserId,
  ]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const storedValue = window.localStorage.getItem(sentMessagesStorageKey);

    if (!storedValue) {
      return;
    }

    try {
      const parsed = JSON.parse(storedValue) as Partial<Record<string, MailMessage[]>>;

      setMailboxStore((currentStore) => {
        const nextStore = { ...currentStore };

        orderedMailboxes.forEach((mailbox) => {
          const storedMessages = Array.isArray(parsed[mailbox.id]) ? parsed[mailbox.id] ?? [] : [];

          if (storedMessages.length === 0) {
            return;
          }

          const currentCollections = nextStore[mailbox.id] ?? createEmptyMailboxCollections();

          nextStore[mailbox.id] = {
            ...currentCollections,
            Sent: [...storedMessages, ...currentCollections.Sent],
          };
        });

        return normalizeMailboxStore(
          nextStore,
          orderedMailboxes,
          senderCategoryLearning,
          messageOwnershipInteractions,
          currentWorkspaceUserId,
        );
      });
    } catch {
      return;
    }
  }, [
    sentMessagesStorageKey,
    mailboxOrderKey,
    senderCategoryLearning,
    messageOwnershipInteractions,
    currentWorkspaceUserId,
  ]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const storedValue = window.localStorage.getItem(trashMessagesStorageKey);

    if (!storedValue) {
      return;
    }

    try {
      const parsed = JSON.parse(storedValue) as Partial<Record<string, MailMessage[]>>;

      setMailboxStore((currentStore) => {
        const nextStore = { ...currentStore };

        orderedMailboxes.forEach((mailbox) => {
          const storedMessages = Array.isArray(parsed[mailbox.id]) ? parsed[mailbox.id] ?? [] : [];

          if (storedMessages.length === 0) {
            return;
          }

          const currentCollections = nextStore[mailbox.id] ?? createEmptyMailboxCollections();

          nextStore[mailbox.id] = {
            ...currentCollections,
            Trash: [...storedMessages, ...currentCollections.Trash],
          };
        });

        return normalizeMailboxStore(
          nextStore,
          orderedMailboxes,
          senderCategoryLearning,
          messageOwnershipInteractions,
          currentWorkspaceUserId,
        );
      });
    } catch {
      return;
    }
  }, [
    trashMessagesStorageKey,
    mailboxOrderKey,
    senderCategoryLearning,
    messageOwnershipInteractions,
    currentWorkspaceUserId,
  ]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const storedValue = window.localStorage.getItem(spamMessagesStorageKey);

    if (!storedValue) {
      return;
    }

    try {
      const parsed = JSON.parse(storedValue) as Partial<Record<string, MailMessage[]>>;

      setMailboxStore((currentStore) => {
        const nextStore = { ...currentStore };

        orderedMailboxes.forEach((mailbox) => {
          const storedMessages = Array.isArray(parsed[mailbox.id]) ? parsed[mailbox.id] ?? [] : [];

          if (storedMessages.length === 0) {
            return;
          }

          const currentCollections = nextStore[mailbox.id] ?? createEmptyMailboxCollections();

          nextStore[mailbox.id] = {
            ...currentCollections,
            Spam: [...storedMessages, ...currentCollections.Spam],
          };
        });

        return normalizeMailboxStore(
          nextStore,
          orderedMailboxes,
          senderCategoryLearning,
          messageOwnershipInteractions,
          currentWorkspaceUserId,
        );
      });
    } catch {
      return;
    }
  }, [
    spamMessagesStorageKey,
    mailboxOrderKey,
    senderCategoryLearning,
    messageOwnershipInteractions,
    currentWorkspaceUserId,
  ]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const storedValue = window.localStorage.getItem(archiveMessagesStorageKey);

    if (!storedValue) {
      return;
    }

    try {
      const parsed = JSON.parse(storedValue) as Partial<Record<string, MailMessage[]>>;

      setMailboxStore((currentStore) => {
        const nextStore = { ...currentStore };

        orderedMailboxes.forEach((mailbox) => {
          const storedMessages = Array.isArray(parsed[mailbox.id]) ? parsed[mailbox.id] ?? [] : [];

          if (storedMessages.length === 0) {
            return;
          }

          const currentCollections = nextStore[mailbox.id] ?? createEmptyMailboxCollections();

          nextStore[mailbox.id] = {
            ...currentCollections,
            Archive: [...storedMessages, ...currentCollections.Archive],
          };
        });

        return normalizeMailboxStore(
          nextStore,
          orderedMailboxes,
          senderCategoryLearning,
          messageOwnershipInteractions,
          currentWorkspaceUserId,
        );
      });
    } catch {
      return;
    }
  }, [
    archiveMessagesStorageKey,
    mailboxOrderKey,
    senderCategoryLearning,
    messageOwnershipInteractions,
    currentWorkspaceUserId,
  ]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const storedValue = window.localStorage.getItem(messageUnreadOverridesStorageKey);

    if (!storedValue) {
      setMessageUnreadOverrides({});
      return;
    }

    try {
      setMessageUnreadOverrides(JSON.parse(storedValue) as MessageUnreadOverrideStore);
    } catch {
      setMessageUnreadOverrides({});
    }
  }, [messageUnreadOverridesStorageKey]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const storedValue = window.localStorage.getItem(manualPriorityOverridesStorageKey);

    if (!storedValue) {
      setManualPriorityOverrides({});
      return;
    }

    try {
      setManualPriorityOverrides(
        JSON.parse(storedValue) as Partial<Record<string, ManualPriorityOverride>>,
      );
    } catch {
      setManualPriorityOverrides({});
    }
  }, [manualPriorityOverridesStorageKey]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    window.localStorage.setItem(
      messageUnreadOverridesStorageKey,
      JSON.stringify(messageUnreadOverrides),
    );
  }, [messageUnreadOverrides, messageUnreadOverridesStorageKey]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    window.localStorage.setItem(
      manualPriorityOverridesStorageKey,
      JSON.stringify(manualPriorityOverrides),
    );
  }, [manualPriorityOverrides, manualPriorityOverridesStorageKey]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const sentMessagesByMailbox = Object.fromEntries(
      orderedMailboxes.map((mailbox) => [
        mailbox.id,
        (mailboxStore[mailbox.id]?.Sent ?? []).slice(0, 100),
      ]),
    );

    window.localStorage.setItem(
      sentMessagesStorageKey,
      JSON.stringify(sentMessagesByMailbox),
    );
  }, [mailboxStore, orderedMailboxes, sentMessagesStorageKey]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const trashMessagesByMailbox = Object.fromEntries(
      orderedMailboxes.map((mailbox) => [
        mailbox.id,
        (mailboxStore[mailbox.id]?.Trash ?? []).slice(0, 100),
      ]),
    );

    window.localStorage.setItem(
      trashMessagesStorageKey,
      JSON.stringify(trashMessagesByMailbox),
    );
  }, [mailboxStore, orderedMailboxes, trashMessagesStorageKey]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const spamMessagesByMailbox = Object.fromEntries(
      orderedMailboxes.map((mailbox) => [
        mailbox.id,
        (mailboxStore[mailbox.id]?.Spam ?? []).slice(0, 100),
      ]),
    );

    window.localStorage.setItem(
      spamMessagesStorageKey,
      JSON.stringify(spamMessagesByMailbox),
    );
  }, [mailboxStore, orderedMailboxes, spamMessagesStorageKey]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const archiveMessagesByMailbox = Object.fromEntries(
      orderedMailboxes.map((mailbox) => [
        mailbox.id,
        (mailboxStore[mailbox.id]?.Archive ?? []).slice(0, 100),
      ]),
    );

    window.localStorage.setItem(
      archiveMessagesStorageKey,
      JSON.stringify(archiveMessagesByMailbox),
    );
  }, [mailboxStore, orderedMailboxes, archiveMessagesStorageKey]);

  useEffect(() => {
    window.localStorage.setItem(
      CATEGORY_LEARNING_STORAGE_KEY,
      JSON.stringify(senderCategoryLearning),
    );
  }, [senderCategoryLearning]);

  useEffect(() => {
    window.localStorage.setItem(
      MESSAGE_OWNERSHIP_STORAGE_KEY,
      JSON.stringify(messageOwnershipInteractions),
    );
  }, [messageOwnershipInteractions]);

  useEffect(() => {
    const currentInboxMessageIds = new Set(
      Object.values(mailboxStore).flatMap((collections) =>
        collections.Inbox.map((message) => message.id),
      ),
    );

    if (seenIncomingMessageIdsRef.current.size === 0) {
      seenIncomingMessageIdsRef.current = currentInboxMessageIds;
      return;
    }

    const ownInboxAddresses = new Set(
      orderedMailboxes.map((mailbox) => normalizeSenderLearningKey(mailbox.email)),
    );
    const pendingReplies: Array<{ inboxId: InboxId; message: MailMessage }> = [];
    const nextReplyLog = normalizeOutOfOfficeReplyLogStore(outOfOfficeReplyLog);
    const now = Date.now();

    for (const mailbox of orderedMailboxes) {
      const inboxMessages = mailboxStore[mailbox.id]?.Inbox ?? [];
      const outOfOfficeSettings = normalizeInboxOutOfOfficeSettings(
        inboxOutOfOffice[mailbox.id],
      );

      for (const message of inboxMessages) {
        if (seenIncomingMessageIdsRef.current.has(message.id)) {
          continue;
        }

        seenIncomingMessageIdsRef.current.add(message.id);

        if (
          !outOfOfficeSettings.enabled ||
          outOfOfficeSettings.message.trim().length === 0 ||
          message.isAutoReply
        ) {
          continue;
        }

        const normalizedSender = normalizeSenderLearningKey(message.from);

        if (
          ownInboxAddresses.has(normalizedSender) ||
          isNoReplyAddress(message.from)
        ) {
          continue;
        }

        const lastReplyTimestamp = nextReplyLog[mailbox.id]?.[normalizedSender];

        if (
          typeof lastReplyTimestamp === "number" &&
          now - lastReplyTimestamp < OUT_OF_OFFICE_SUPPRESSION_WINDOW_MS
        ) {
          continue;
        }

        const autoReplyId = `${mailbox.id}-ooo-${message.id}`;
        const autoReplyBody = outOfOfficeSettings.message
          .replace(/\r\n/g, "\n")
          .split("\n")
          .filter((paragraph) => paragraph.length > 0);

        pendingReplies.push({
          inboxId: mailbox.id,
          message: normalizeMailMessage(
            {
              id: autoReplyId,
              threadId: message.threadId,
              sender: "You",
              subject: buildOutOfOfficeReplySubject(message.subject),
              snippet: outOfOfficeSettings.message.replace(/\s+/g, " ").trim().slice(0, 96),
              time: "Now",
              createdAt: new Date(now).toISOString(),
              signal: "Auto-reply",
              from: mailbox.email,
              to: message.from,
              timestamp: "Sent just now",
              body:
                autoReplyBody.length > 0
                  ? autoReplyBody
                  : ["Automatic reply"],
              isAutoReply: true,
              autoReplyType: "out_of_office",
            },
            mailbox.id,
            senderCategoryLearning,
            messageOwnershipInteractions,
            currentWorkspaceUserId,
            mailboxStore,
          ),
        });

        nextReplyLog[mailbox.id] = {
          ...(nextReplyLog[mailbox.id] ?? {}),
          [normalizedSender]: now,
        };
      }
    }

    if (pendingReplies.length === 0) {
      return;
    }

    setMailboxStore((currentStore) => {
      const nextStore = { ...currentStore };

      for (const { inboxId, message } of pendingReplies) {
        const collections = nextStore[inboxId];

        if (!collections || collections.Sent.some((entry) => entry.id === message.id)) {
          continue;
        }

        nextStore[inboxId] = {
          ...collections,
          Sent: [message, ...collections.Sent],
        };
      }

      return normalizeMailboxStore(
        nextStore,
        orderedMailboxes,
        senderCategoryLearning,
        messageOwnershipInteractions,
        currentWorkspaceUserId,
      );
    });
    setOutOfOfficeReplyLog(nextReplyLog);
  }, [
    currentWorkspaceUserId,
    inboxOutOfOffice,
    mailboxStore,
    messageOwnershipInteractions,
    orderedMailboxes,
    outOfOfficeReplyLog,
    senderCategoryLearning,
  ]);

  useEffect(() => {
    setMailboxStore((currentStore) =>
      normalizeMailboxStore(
        currentStore,
        orderedMailboxes,
        senderCategoryLearning,
        messageOwnershipInteractions,
        currentWorkspaceUserId,
      ),
    );
  }, [currentWorkspaceUserId, mailboxOrderKey, messageOwnershipInteractions, senderCategoryLearning]);

  useEffect(() => {
    if (!window.matchMedia) {
      return;
    }

    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const handleChange = (event: MediaQueryListEvent) => {
      setSystemColorMode(event.matches ? "dark" : "light");
    };

    setSystemColorMode(mediaQuery.matches ? "dark" : "light");
    mediaQuery.addEventListener("change", handleChange);

    return () => mediaQuery.removeEventListener("change", handleChange);
  }, []);

  const resolvedTheme: "light" | "dark" =
    workspaceMode === "System"
      ? systemColorMode
      : workspaceMode === "Dark"
        ? "dark"
        : "light";
  const resetSmartFolderDraft = () => {
    setEditingSmartFolderId(null);
    setSmartFolderDraftName("");
    setSmartFolderDraftScope("all");
    setSmartFolderDraftSelectedInboxIds([]);
    setSmartFolderDraftRules([createEmptySmartFolderRule()]);
  };

  useEffect(() => {
    document.documentElement.style.colorScheme = resolvedTheme;
    document.body.style.colorScheme = resolvedTheme;

    return () => {
      document.documentElement.style.colorScheme = "";
      document.body.style.colorScheme = "";
    };
  }, [resolvedTheme]);

  useEffect(() => {
    window.localStorage.setItem(WORKSPACE_THEME_MODE_STORAGE_KEY, workspaceMode);
  }, [workspaceMode]);

  useEffect(() => {
    window.localStorage.setItem(
      AI_SUGGESTIONS_STORAGE_KEY,
      String(aiSuggestionsEnabled),
    );
  }, [aiSuggestionsEnabled]);

  useEffect(() => {
    window.localStorage.setItem(
      INBOX_CHANGES_STORAGE_KEY,
      String(inboxChangesEnabled),
    );
  }, [inboxChangesEnabled]);

  useEffect(() => {
    window.localStorage.setItem(
      TEAM_ACTIVITY_STORAGE_KEY,
      String(teamActivityEnabled),
    );
  }, [teamActivityEnabled]);

  useEffect(() => {
    window.localStorage.setItem(
      MAIL_SIGNATURES_STORAGE_KEY,
      JSON.stringify(inboxSignatures),
    );
  }, [inboxSignatures]);

  useEffect(() => {
    window.localStorage.setItem(
      MAIL_OUT_OF_OFFICE_STORAGE_KEY,
      JSON.stringify(inboxOutOfOffice),
    );
  }, [inboxOutOfOffice]);

  useEffect(() => {
    window.localStorage.setItem(
      OUT_OF_OFFICE_REPLY_LOG_STORAGE_KEY,
      JSON.stringify(outOfOfficeReplyLog),
    );
  }, [outOfOfficeReplyLog]);

  useEffect(() => {
    window.localStorage.setItem(
      SMART_FOLDERS_STORAGE_KEY,
      JSON.stringify(smartFolders),
    );
  }, [smartFolders]);

  if (collaborationInviteRoute) {
    const inviteViewerType = isGuestInviteUser ? "external" : "workspace";
    const inviteVisibleMessages = inviteCollaboration
      ? inviteCollaboration.messages.filter((entry) =>
          canViewerSeeCollaborationMessage(entry, inviteViewerType),
        )
      : [];
    const inviteMentionCandidates = getCollaborationMentionTargets(inviteParticipants, []);
    const inviteMentionQuery = getMentionQueryAtCursor(
      inviteReplyDraft,
      inviteReplySelection,
    );
    const visibleInviteMentionCandidates = inviteMentionQuery
      ? inviteMentionCandidates.filter((candidate) =>
          candidate.handle.toLowerCase().includes(inviteMentionQuery.query.toLowerCase()),
        )
      : [];
    const isInviteErrorState =
      inviteRouteState === "expired" ||
      inviteRouteState === "invalid" ||
      inviteRouteState === "unavailable" ||
      inviteRouteState === "forbidden";
    const inviteDebugPayload = isInviteErrorState
      ? {
          currentUrl:
            typeof window !== "undefined"
              ? `${window.location.origin}${window.location.pathname}${window.location.search}${window.location.hash}`
              : "",
          parsedRoute: collaborationInviteRoute,
          parsedQueryParams:
            typeof window !== "undefined"
              ? Object.fromEntries(new URLSearchParams(window.location.search).entries())
              : {},
          resolvedInviteToken: collaborationInviteRoute.inviteToken,
          decodedInvitePayload: decodedInvitePayload
            ? {
                inviteeEmail: decodedInvitePayload.inviteeEmail,
                messageId: decodedInvitePayload.message.id,
                collaborationState: decodedInvitePayload.message.collaboration?.state ?? null,
              }
            : null,
          resolvedMessageId: inviteMessage?.id ?? collaborationInviteRoute.messageId ?? null,
          resolvedCollaborationState: inviteCollaboration?.state ?? null,
          failureReason: inviteRouteState,
        }
      : null;

    const inviteStateTitle =
      inviteRouteState === "expired"
        ? "This invite has expired"
        : inviteRouteState === "invalid"
          ? "This invite link is invalid"
          : inviteRouteState === "unavailable"
            ? "This collaboration is no longer available"
            : inviteRouteState === "forbidden"
              ? "You can’t join this collaboration"
              : inviteRouteState === "left"
                ? "You left this collaboration"
              : inviteRouteState === "declined"
                ? "Invitation declined"
                : "You’ve been invited to collaborate";
    const inviteStateDescription =
      inviteRouteState === "expired"
        ? "Ask the sender to share a fresh collaboration invite."
        : inviteRouteState === "invalid"
          ? "The link is missing required collaboration details."
          : inviteRouteState === "unavailable"
            ? "The collaboration may have been removed or is no longer active."
            : inviteRouteState === "forbidden"
              ? "This invite is no longer available for your account."
              : inviteRouteState === "left"
                ? "You can reopen this invite later if you want to come back."
              : inviteRouteState === "declined"
                ? "You can close this screen now, or reopen the invite later if needed."
                : null;

    if (inviteRouteState !== "joined") {
      return (
        <main
          data-theme={resolvedTheme}
          className="box-border min-h-dvh animate-fade-in px-4 py-8 md:px-8 md:py-10"
          style={{ background: "var(--workspace-bg)", colorScheme: resolvedTheme }}
        >
          <div className="mx-auto flex min-h-[calc(100dvh-5rem)] max-w-[720px] items-center justify-center">
            <div className="w-full rounded-[36px] border border-[var(--workspace-shell-border)] bg-[var(--workspace-shell)] p-8 shadow-panel md:p-10">
              <div className="space-y-4 text-center">
                <div className="text-[0.72rem] font-medium uppercase tracking-[0.22em] text-[var(--workspace-text-faint)]">
                  Cuevion collaboration
                </div>
                <h1 className="text-[1.9rem] font-medium tracking-[-0.03em] text-[var(--workspace-text)]">
                  {inviteStateTitle}
                </h1>
                {inviteRouteState === "accept" && inviteMessage && inviteCollaboration ? (
                  <div className="mx-auto max-w-[32rem] space-y-4 text-left">
                    <div className="rounded-[24px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-6 py-5">
                      <div className="space-y-3">
                        <div>
                          <div className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                            Invited by
                          </div>
                          <div className="mt-1 text-[0.96rem] text-[var(--workspace-text)]">
                            {inviteCollaboration.requestedBy}
                          </div>
                        </div>
                        <div>
                          <div className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                            Collaboration
                          </div>
                          <div className="mt-1 text-[1.04rem] font-medium tracking-[-0.02em] text-[var(--workspace-text)]">
                            {inviteMessage.subject}
                          </div>
                        </div>
                        <div>
                          <div className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                            Reason
                          </div>
                          <div className="mt-1 text-[0.92rem] leading-7 text-[var(--workspace-text-soft)]">
                            {getCollaborationReasonLabel(inviteCollaboration)}
                          </div>
                        </div>
                        {inviteCollaboration.previewText ? (
                          <div>
                            <div className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                              Note
                            </div>
                            <div className="mt-1 text-[0.92rem] leading-7 text-[var(--workspace-text-soft)]">
                              {inviteCollaboration.previewText}
                            </div>
                          </div>
                        ) : null}
                      </div>
                    </div>
                    <div className="flex items-center justify-center gap-3">
                      <button
                        type="button"
                        onClick={joinCollaborationFromInvite}
                        className={mailboxPrimaryActionButtonClass}
                      >
                        Join collaboration
                      </button>
                      <button
                        type="button"
                        onClick={declineCollaborationInvite}
                        className={modalTertiaryActionButtonClass}
                      >
                        Decline
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="mx-auto max-w-[28rem] space-y-3">
                    <p className="text-[0.96rem] leading-7 text-[var(--workspace-text-soft)]">
                      {inviteStateDescription}
                    </p>
                    {isLocalDevelopmentEnvironment() && inviteDebugPayload ? (
                      <div className="rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] p-4 text-left">
                        <div className="text-[0.68rem] font-medium uppercase tracking-[0.14em] text-[var(--workspace-text-faint)]">
                          Dev only debug
                        </div>
                        <pre className="mt-3 overflow-x-auto whitespace-pre-wrap break-words text-[0.75rem] leading-6 text-[var(--workspace-text-soft)]">
                          {JSON.stringify(inviteDebugPayload, null, 2)}
                        </pre>
                      </div>
                    ) : null}
                  </div>
                )}
              </div>
            </div>
          </div>
        </main>
      );
    }

    return (
      <main
        data-theme={resolvedTheme}
        className="box-border min-h-dvh animate-fade-in px-4 py-8 md:px-8 md:py-10"
        style={{ background: "var(--workspace-bg)", colorScheme: resolvedTheme }}
      >
        <div className="mx-auto flex min-h-[calc(100dvh-5rem)] max-w-[820px] items-center justify-center">
            <div className="w-full rounded-[36px] border border-[var(--workspace-shell-border)] bg-[var(--workspace-shell)] p-8 shadow-panel md:p-10">
              <div className="flex items-start justify-between gap-4">
                <div className="space-y-2">
                <div className="text-[0.72rem] font-medium uppercase tracking-[0.22em] text-[var(--workspace-text-faint)]">
                  Cuevion collaboration
                </div>
                <h1 className="text-[1.65rem] font-medium tracking-[-0.03em] text-[var(--workspace-text)]">
                  {inviteMessage?.subject ?? "Collaboration"}
                </h1>
                <div className="text-[0.92rem] leading-7 text-[var(--workspace-text-soft)]">
                  Participants:{" "}
                  {inviteParticipants
                    .map((participant) => participant.name || participant.email)
                    .join(", ")}
                </div>
              </div>
            </div>

            <div className="mt-8 space-y-5">
              {isGuestInviteUser && inviteCollaboration ? (
                <div className="rounded-[24px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-5 py-4">
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div className="space-y-2">
                      <div className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                        You’re using Cuevion as a guest
                      </div>
                      <div className="text-[0.96rem] leading-7 text-[var(--workspace-text)]">
                        {`You were invited by ${inviteCollaboration.requestedBy}`}
                      </div>
                      <div className="space-y-1 text-[0.88rem] leading-7 text-[var(--workspace-text-soft)]">
                        <div>{getCollaborationReasonLabel(inviteCollaboration)}</div>
                        {inviteCollaboration.previewText ? (
                          <div>{inviteCollaboration.previewText}</div>
                        ) : null}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={leaveCollaborationFromInvite}
                      className={modalTertiaryActionButtonClass}
                    >
                      Leave collaboration
                    </button>
                  </div>
                </div>
              ) : null}
              <div className="space-y-4">
                {inviteVisibleMessages.map((entry) => (
                  <div key={entry.id} className="space-y-1">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.82rem] leading-6 text-[var(--workspace-text)]">
                      <span>{entry.authorName}</span>
                      <span
                        className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[0.64rem] font-medium uppercase tracking-[0.14em] ${
                          getCollaborationMessageVisibility(entry) === "internal"
                            ? "border-[color:rgba(115,132,118,0.24)] bg-[color:rgba(126,155,128,0.12)] text-[color:rgba(82,97,85,0.86)]"
                            : "border-[color:rgba(123,116,106,0.18)] bg-[color:rgba(136,127,115,0.08)] text-[color:rgba(126,117,106,0.78)]"
                        }`}
                      >
                        {getCollaborationMessageVisibility(entry) === "internal"
                          ? "Internal"
                          : "Shared"}
                      </span>
                    </div>
                    <div className="text-[0.92rem] leading-7 text-[var(--workspace-text-soft)]">
                      {renderTextWithMentions(
                        entry.text,
                        new Map(
                          (entry.mentions ?? []).map((mention) => [
                            mention.handle.toLowerCase(),
                            mention,
                          ]),
                        ),
                        resolvedTheme,
                      )}
                    </div>
                  </div>
                ))}
                {inviteVisibleMessages.length === 0 ? (
                  <div className="text-[0.88rem] leading-7 text-[var(--workspace-text-faint)]">
                    {inviteReplyVisibility === "internal"
                      ? "Start an internal discussion with your team"
                      : "Send a message to all participants"}
                  </div>
                ) : null}
                {inviteCollaboration?.resolvedAt && inviteCollaboration.resolvedByUserName ? (
                  <div className="pt-1 text-[0.82rem] leading-6 text-[color:rgba(118,110,100,0.76)]">
                    {`✓ Marked as done by ${
                      inviteCollaboration.resolvedByUserName
                    } · ${formatCollaborationStatusTimestamp(inviteCollaboration.resolvedAt)}`}
                  </div>
                ) : null}
              </div>

              <label className="block space-y-2.5">
                <span className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                  Reply
                </span>
                <div className="flex flex-wrap gap-2">
                  {(
                    isGuestInviteUser
                      ? ([{ value: "shared", label: "Shared" }] as const)
                      : ([
                          { value: "internal", label: "Internal" },
                          { value: "shared", label: "Shared" },
                        ] as const)
                  ).map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() => setInviteReplyVisibility(option.value)}
                      className={`inline-flex h-9 items-center justify-center rounded-full border px-4 text-[0.68rem] font-medium uppercase tracking-[0.16em] transition-[background-color,border-color,color] duration-150 focus-visible:outline-none ${
                        inviteReplyVisibility === option.value
                          ? "border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] text-[var(--workspace-accent-text)]"
                          : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[var(--workspace-text-soft)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface-strong)]"
                      }`}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
                <div className="pt-0.5 text-[0.8rem] leading-6 text-[color:rgba(120,111,100,0.76)]">
                  {inviteReplyVisibility === "internal"
                    ? "Only visible to your team"
                    : `Visible to: ${
                        inviteParticipants
                          .map((participant) => participant.name || participant.email)
                          .join(", ") || "all participants"
                      }`}
                </div>
                <textarea
                  ref={inviteReplyInputRef}
                  value={inviteReplyDraft}
                  onChange={(event) => {
                    setInviteReplyDraft(event.target.value);
                    syncInviteMentionState(event.target.value, event.target);
                  }}
                  onClick={(event) =>
                    syncInviteMentionState(event.currentTarget.value, event.currentTarget)
                  }
                  onKeyUp={(event) =>
                    syncInviteMentionState(event.currentTarget.value, event.currentTarget)
                  }
                  onKeyDown={(event) => {
                    if (visibleInviteMentionCandidates.length === 0) {
                      return;
                    }

                    if (event.key === "ArrowDown") {
                      event.preventDefault();
                      setInviteMentionIndex((current) =>
                        current >= visibleInviteMentionCandidates.length - 1
                          ? 0
                          : current + 1,
                      );
                      return;
                    }

                    if (event.key === "ArrowUp") {
                      event.preventDefault();
                      setInviteMentionIndex((current) =>
                        current <= 0 ? visibleInviteMentionCandidates.length - 1 : current - 1,
                      );
                      return;
                    }

                    if (event.key === "Enter" || event.key === "Tab") {
                      event.preventDefault();
                      applyInviteMention(
                        visibleInviteMentionCandidates[inviteMentionIndex] ??
                          visibleInviteMentionCandidates[0],
                      );
                      return;
                    }

                    if (event.key === "Escape") {
                      setInviteMentionIndex(0);
                    }
                  }}
                  rows={4}
                  placeholder={
                    inviteReplyVisibility === "internal"
                      ? "Add an internal note for your team"
                      : "Reply to everyone in this collaboration"
                  }
                  className="w-full resize-none rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 py-3 text-[0.92rem] leading-7 text-[var(--workspace-text-soft)] outline-none placeholder:text-[var(--workspace-text-faint)]"
                />
                {visibleInviteMentionCandidates.length > 0 ? (
                  <div className="rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] p-2">
                    {visibleInviteMentionCandidates.map((candidate, index) => (
                      <button
                        key={`invite-mention-${candidate.id}`}
                        type="button"
                        onMouseDown={(event) => {
                          event.preventDefault();
                          applyInviteMention(candidate);
                        }}
                        className={`flex w-full items-center justify-between rounded-[12px] px-3 py-2 text-left text-[0.82rem] transition-colors duration-150 focus-visible:outline-none ${
                          index === inviteMentionIndex
                            ? "bg-[var(--workspace-hover-surface-strong)] text-[var(--workspace-text)]"
                            : "text-[var(--workspace-text-soft)] hover:bg-[var(--workspace-hover-surface)]"
                        }`}
                      >
                        <span>{candidate.name}</span>
                        <span className="text-[0.76rem] text-[var(--workspace-text-faint)]">
                          @{candidate.handle}
                        </span>
                      </button>
                    ))}
                  </div>
                ) : null}
              </label>
            </div>

            <div className="mt-6 flex items-center justify-between gap-3">
              <div className="text-[0.8rem] leading-6 text-[var(--workspace-text-faint)]">
                {isGuestInviteUser
                  ? inviteReplyVisibility === "shared"
                    ? "This message will be sent via Cuevion"
                    : "Internal notes stay inside Cuevion"
                  : null}
              </div>
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={sendInviteFlowReply}
                  disabled={!inviteReplyDraft.trim()}
                  className={
                    inviteReplyDraft.trim()
                      ? `${mailboxPrimaryActionButtonClass} h-10 px-5 text-[0.72rem] tracking-[0.16em]`
                      : "inline-flex h-10 cursor-not-allowed items-center justify-center rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-5 text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-soft)] opacity-45 transition-[opacity] duration-150 focus-visible:outline-none"
                  }
                >
                  Send reply
                </button>
                <button
                  type="button"
                  onClick={markInviteFlowDone}
                  className={modalSecondaryActionButtonClass}
                >
                  Mark as done
                </button>
              </div>
            </div>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main
      className={`box-border h-dvh overflow-hidden animate-fade-in ${workspaceOuterShellClass}`}
      data-theme={resolvedTheme}
      style={{ background: "var(--workspace-bg)", colorScheme: resolvedTheme }}
    >
      <WorkspaceSidebar
        activeSection={activeSection}
        activeMailboxId={activeMailbox?.id ?? null}
        orderedMailboxes={orderedMailboxes}
        onChangeSection={handleChangeSection}
        onOpenMailbox={(mailbox) =>
          openMailboxFromContext(mailbox, { preserveCurrentContext: false })
        }
      />

      <div className={`mx-auto h-full ${workspaceContentRailClass}`}>
        <div
          className={`relative h-full overflow-hidden ${workspaceShellSurfaceClass}`}
        >
          <div
            ref={workspaceModalHostRef}
            className="pointer-events-none fixed inset-0 z-[190]"
          />
          <div className={`flex h-full min-h-0 flex-col ${workspaceShellPaddingClass}`}>
            <div className="mb-8 flex items-center justify-between md:hidden">
              <CuevionMark />
	              <span className="rounded-full border border-[var(--workspace-border)] bg-[var(--workspace-card)] px-4 py-2 text-xs uppercase tracking-[0.24em] text-[var(--workspace-text-faint)]">
	                {activeTarget
	                  ? isReviewWorkspaceTarget(activeTarget)
	                    ? getReviewTargetEyebrow()
	                    : targetContent[activeTarget].eyebrow
	                  : activeSection}
	              </span>
	            </div>
	            {activeTarget ? (
	              isReviewWorkspaceTarget(activeTarget) ? (
	                <ReviewModuleDetailView
	                  target={activeTarget}
	                  controller={reviewController}
	                  onBack={() => setActiveTarget(null)}
	                  onHandleNow={handleReviewHandleNow}
	                />
	              ) : (
	                <WorkspaceTargetView
	                  target={activeTarget}
	                  onBack={() => setActiveTarget(null)}
	                  themeMode={resolvedTheme}
	                />
	              )
	            ) : activeMailbox ? (
              <div className="h-0 min-h-0 flex-1 overflow-hidden">
	                <MailboxView
	                  key={`${activeMailbox.id}-${mailboxResetToken}`}
	                  mailbox={activeMailbox}
	                  orderedMailboxes={orderedMailboxes}
	                  managedInboxes={savedManagedInboxes}
	                  smartFolders={smartFolders}
                  onOpenSmartFolderModal={() => {
                    resetSmartFolderDraft();
                    setIsSmartFolderModalOpen(true);
                  }}
                  onEditSmartFolder={(folderId) => {
                    const folder = smartFolders.find((entry) => entry.id === folderId);

                    if (!folder) {
                      return;
                    }

                    setEditingSmartFolderId(folder.id);
                    setSmartFolderDraftName(folder.name);
                    setSmartFolderDraftScope(folder.scope);
                    setSmartFolderDraftSelectedInboxIds(folder.selectedInboxIds);
                    setSmartFolderDraftRules(
                      folder.rules.length > 0
                        ? folder.rules
                        : [createEmptySmartFolderRule()],
                    );
                    setIsSmartFolderModalOpen(true);
                  }}
                  onDeleteSmartFolder={(folderId) => {
                    setSmartFolders((current) =>
                      current.filter((folder) => folder.id !== folderId),
                    );
                  }}
                  onBack={handleReturnFromMailbox}
                  onOpenMailbox={setActiveMailbox}
                  onRenameMailbox={handleRenameMailbox}
                  onSaveLearningRule={handleSaveLearningRule}
                  onLearnCategoryDecision={handleLearnCategoryDecision}
                  onEnableAutoCategoryForSender={handleEnableAutoCategoryForSender}
                  onRecordMessageOwnershipInteraction={handleRecordMessageOwnershipInteraction}
                  senderCategoryLearning={senderCategoryLearning}
                  messageOwnershipInteractions={messageOwnershipInteractions}
                  currentUserId={currentWorkspaceUserId}
                  currentUserEmail={activeWorkspaceEmail}
                  mailboxStore={mailboxStore}
                  setMailboxStore={setMailboxStore}
                  inboxSignatures={inboxSignatures}
                  themeMode={resolvedTheme}
                  aiSuggestionsEnabled={aiSuggestionsEnabled}
	                  notificationNavigationRequest={notificationNavigationRequest}
	                  onConsumeNotificationNavigation={(requestKey) =>
	                    setNotificationNavigationRequest((current) =>
	                      current?.requestKey === requestKey ? null : current,
	                    )
	                  }
                  manualPriorityOverrides={manualPriorityOverrides}
                  onSetManualPriority={handleSetManualPriority}
                  getLinkedReviewForMessage={getLinkedReviewForMessage}
                  getLinkedReviewBadgeLabel={getLinkedReviewBadgeLabel}
                  onOpenLinkedReview={(target) => setActiveTarget(target)}
                  onSyncMailbox={handleSyncActiveMailbox}
                  isSyncingMailbox={syncingMailboxId === activeMailbox.id}
                  onSyncUnreadOverrides={syncUnreadOverrides}
                />
              </div>
            ) : activeSection === "Dashboard" ? (
              <div className="min-h-0 flex-1 overflow-y-auto pr-1">
                <DashboardView
                  onOpenPriority={() => handleOpenPriority("Priority")}
                  onOpenPrimaryInbox={handleOpenSenderContext}
                  onOpenInboxes={() => handleOpenInboxes("Connected")}
                  onOpenForYou={() => handleOpenForYou("Promo")}
                  onOpenNotificationNavigation={handleOpenNotificationNavigation}
                  teamActivityEnabled={teamActivityEnabled}
                  primaryInboxTitle={primaryInboxTitle}
                  primaryInboxEmailCount={primaryInboxEmailCount}
                  priorityInboxCount={livePriorityInboxItems.length}
                  connectedInboxCount={connectedInboxCount}
                  showDemoContent={isDemoWorkspace}
                />
              </div>
	            ) : activeSection === "Priority" ? (
	              <div className="min-h-0 flex-1 overflow-y-auto pr-1">
	                <ReviewModuleListView
	                  filter={reviewFilter}
	                controller={reviewController}
	                onOpenItem={handleOpenPriorityItem}
	                hiddenReviewIds={hiddenPriorityReviewIds}
	                supplementalItems={livePriorityInboxItems}
	                displayOverrides={priorityDisplayOverrides}
	              />
	            </div>
            ) : activeSection === "Inboxes" ? (
              <InboxesView
                filter={inboxFilter}
                orderedMailboxes={orderedMailboxes}
                onOpenMailbox={openMailboxFromContext}
              />
            ) : activeSection === "Activity" ||
              activeSection === "Notifications" ||
              activeSection === "Team" ? (
              <div className="min-h-0 flex-1 overflow-y-auto pr-1">
                <WorkbenchView
                  section={activeSection}
                  onOpenDemoInbox={handleOpenDemoInbox}
                  onOpenLearningRequest={handleOpenLearningRequest}
                  onOpenSenderContext={handleOpenSenderContext}
                  onOpenNotificationNavigation={handleOpenNotificationNavigation}
                  aiSuggestionsEnabled={aiSuggestionsEnabled}
                  inboxChangesEnabled={inboxChangesEnabled}
                  teamActivityEnabled={teamActivityEnabled}
                  modalHost={workspaceModalHostRef.current}
                  pendingTeamInvitation={pendingTeamInvitation}
                  memberOfEntries={memberOfEntries}
                  onAddMemberOfEntry={(entry) => {
                    setMemberOfEntries((current) => [...current, entry]);
                  }}
                  onAcceptPendingTeamInvitation={() => {
                    setPendingTeamInvitation(null);
                  }}
                  onDeclinePendingTeamInvitation={() => {
                    setPendingTeamInvitation(null);
                  }}
                  showDemoContent={isDemoWorkspace}
                />
              </div>
            ) : activeSection === "Settings" ? (
              <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-1 md:px-2 md:pb-2">
                <SettingsView
                  workspaceName={workspaceName}
                  savedManagedInboxes={savedManagedInboxes}
                  themeMode={resolvedTheme}
                  workspaceMode={workspaceMode}
                  inboxSignatures={inboxSignatures}
                  inboxOutOfOffice={inboxOutOfOffice}
                  onChangeWorkspaceMode={setWorkspaceMode}
                  aiSuggestionsEnabled={aiSuggestionsEnabled}
                  onToggleAiSuggestions={() =>
                    setAiSuggestionsEnabled((current) => !current)
                  }
                  inboxChangesEnabled={inboxChangesEnabled}
                  onToggleInboxChanges={() =>
                    setInboxChangesEnabled((current) => !current)
                  }
                  teamActivityEnabled={teamActivityEnabled}
                  onToggleTeamActivity={() =>
                    setTeamActivityEnabled((current) => !current)
                  }
                  onSaveWorkspaceName={setWorkspaceName}
                  onApplyManagedInboxes={handleApplyManagedInboxes}
                  onSaveInboxSignature={(inboxId, signature) => {
                    setInboxSignatures((current) => ({
                      ...current,
                      [inboxId]: normalizeInboxSignatureSettings(signature),
                    }));
                  }}
                  onSaveInboxOutOfOffice={(inboxId, outOfOffice) => {
                    setInboxOutOfOffice((current) => ({
                      ...current,
                      [inboxId]: normalizeInboxOutOfOfficeSettings(outOfOffice),
                    }));
                  }}
                />
              </div>
            ) : activeSection === "Help" || activeSection === "Contact" ? (
              <div className="min-h-0 flex-1 overflow-y-auto pr-1">
                <UtilityView
                  section={activeSection}
                  lastViewedGuidance={lastViewedGuidance}
                  onSetLastViewedGuidance={setLastViewedGuidance}
                  primaryWorkspaceEmail={primaryWorkspaceEmail}
                />
              </div>
            ) : (
              <div className="min-h-0 flex-1 overflow-y-auto pr-1">
                <ForYouView
                  context={forYouContext}
                  onOpenTarget={setActiveTarget}
                  onSaveLearningRule={handleSaveLearningRule}
                  senderCategoryLearning={senderCategoryLearning}
                  mailboxStore={mailboxStore}
                  orderedMailboxes={orderedMailboxes}
                  modalHost={workspaceModalHostRef.current}
                  learningLaunchRequest={learningLaunchRequest}
                  onConsumeLearningLaunchRequest={() => setLearningLaunchRequest(null)}
                  aiSuggestionsEnabled={aiSuggestionsEnabled}
                />
              </div>
	            )}
	          </div>
	        </div>
	        {reviewInboxHandoffFeedback ? (
	          <div className="pointer-events-none fixed bottom-6 right-6 z-[340]">
	            <div className="rounded-[18px] border border-[color:rgba(111,148,111,0.26)] bg-[color:rgba(34,32,28,0.94)] px-4 py-3 text-[0.84rem] leading-6 text-[color:rgba(214,232,218,0.96)] shadow-panel">
	              {reviewInboxHandoffFeedback}
	            </div>
	          </div>
	        ) : null}
	      </div>
	      <SmartFolderModal
	        open={isSmartFolderModalOpen}
        themeMode={resolvedTheme}
        connectedInboxes={orderedMailboxes}
        isEditing={editingSmartFolderId !== null}
        draftName={smartFolderDraftName}
        draftScope={smartFolderDraftScope}
        draftSelectedInboxIds={smartFolderDraftSelectedInboxIds}
        draftRules={smartFolderDraftRules}
        onChangeName={setSmartFolderDraftName}
        onChangeScope={setSmartFolderDraftScope}
        onToggleInbox={(inboxId) =>
          setSmartFolderDraftSelectedInboxIds((current) =>
            current.includes(inboxId)
              ? current.filter((entry) => entry !== inboxId)
              : [...current, inboxId],
          )
        }
        onChangeRuleField={(ruleId, nextField) =>
          setSmartFolderDraftRules((current) =>
            current.map((rule) =>
              rule.id === ruleId ? { ...rule, field: nextField } : rule,
            ),
          )
        }
        onChangeRuleValue={(ruleId, nextValue) =>
          setSmartFolderDraftRules((current) =>
            current.map((rule) =>
              rule.id === ruleId ? { ...rule, value: nextValue } : rule,
            ),
          )
        }
        onAddRule={() =>
          setSmartFolderDraftRules((current) => [...current, createEmptySmartFolderRule()])
        }
        onRemoveRule={(ruleId) =>
          setSmartFolderDraftRules((current) =>
            current.filter((rule) => rule.id !== ruleId),
          )
        }
        onDelete={
          editingSmartFolderId
            ? () => {
                setSmartFolders((current) =>
                  current.filter((folder) => folder.id !== editingSmartFolderId),
                );
                setIsSmartFolderModalOpen(false);
                resetSmartFolderDraft();
              }
            : undefined
        }
        onCancel={() => {
          setIsSmartFolderModalOpen(false);
          resetSmartFolderDraft();
        }}
        onSave={() => {
          const trimmedName = smartFolderDraftName.trim();
          const validRules = smartFolderDraftRules
            .map((rule) => ({ ...rule, value: rule.value.trim() }))
            .filter((rule) => rule.value.length > 0);

          if (
            trimmedName.length === 0 ||
            validRules.length === 0 ||
            (smartFolderDraftScope === "selected" &&
              smartFolderDraftSelectedInboxIds.length === 0)
          ) {
            return;
          }

          setSmartFolders((current) => {
            if (editingSmartFolderId) {
              return current.map((folder) =>
                folder.id === editingSmartFolderId
                  ? {
                      ...folder,
                      name: trimmedName,
                      scope: smartFolderDraftScope,
                      selectedInboxIds:
                        smartFolderDraftScope === "selected"
                          ? smartFolderDraftSelectedInboxIds
                          : [],
                      rules: validRules,
                    }
                  : folder,
              );
            }

            return [
              {
                id: `smart-folder-${Date.now()}`,
                name: trimmedName,
                scope: smartFolderDraftScope,
                selectedInboxIds:
                  smartFolderDraftScope === "selected"
                    ? smartFolderDraftSelectedInboxIds
                    : [],
                rules: validRules,
              },
              ...current,
            ];
          });
          setIsSmartFolderModalOpen(false);
          resetSmartFolderDraft();
        }}
      />
    </main>
  );
}
