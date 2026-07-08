import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  ACTIVE_WORK_PRIORITY_STATUSES,
  countUnreadMessages,
  formatOrganizerSignal,
  resolveOrganizerCategory,
  shouldShowInDemoInbox,
  shouldShowInOrganizerPriority,
  shouldShowInPromoInbox,
  type BundleOrganizerActiveWorkStatus,
} from "./bundleOrganizerFilters";
import {
  BUNDLE_ORGANIZER_WORKFLOW_STATE_CHANGED_EVENT,
  getBundleOrganizerWorkflowIdentityKey,
  readBundleOrganizerWorkflowState,
  writeBundleOrganizerWorkflowState,
  type BundleOrganizerWorkflowState,
} from "./bundleOrganizerWorkflowState";

type BundleOrganizerView = "priority" | "shortlist" | "demo" | "promo" | "trash";
type BundleOrganizerDemoStatusFilter =
  | "all"
  | "unread"
  | "shortlisted"
  | "priority"
  | "replied"
  | "declined";
type BundleOrganizerPromoStatusFilter =
  | "all"
  | "unread"
  | "read"
  | "shortlisted"
  | "priority";
type BundleOrganizerPromoFilter = "new" | "reminders" | "all";
type BundleOrganizerDateSort = "newest" | "oldest";

type BundleOrganizerContextMenuState = {
  anchorX: number;
  anchorY: number;
  messageId: string;
  sourceView: BundleOrganizerView;
  x: number;
  y: number;
};

type BundleOrganizerContextMenuIconName =
  | "category"
  | "decline"
  | "forward"
  | "interest"
  | "mail"
  | "mailOpen"
  | "priority"
  | "priorityOff"
  | "reply"
  | "restore"
  | "rule"
  | "shortlist"
  | "shortlistOff"
  | "smartView"
  | "trash";

type BundleOrganizerContextMenuAction = {
  disabled?: boolean;
  disabledReason?: string;
  icon?: BundleOrganizerContextMenuIconName;
  label: string;
  onSelect?: () => void;
};

type BundleOrganizerReadState = "read" | "unread";
const getWorkflowIdentityKey = getBundleOrganizerWorkflowIdentityKey;

type BundleOrganizerSearchRow = {
  message: BundleOrganizerMessage;
  sourceView: BundleOrganizerView;
};

type BundleOrganizerMessage = {
  id: string;
  sender: string;
  from?: string;
  to?: string;
  cc?: string;
  subject: string;
  snippet: string;
  body: string[];
  bodyHtml?: string;
  timestamp: string;
  createdAt?: string;
  threadId?: string;
  threadGroupingKey?: string;
  sourceMailbox: string;
  manualCategory?: "demo" | "promo";
  manualCategoryAt?: string | null;
  manualLabelCategory?: "demo" | "promo";
  learnedLabelCategory?: "demo" | "promo";
  internalClassification?: BundleOrganizerInternalClassification;
  category?: string;
  signal?: string;
  ui_signal?: string;
  uiSignal?: string;
  unread?: boolean;
  readState?: BundleOrganizerReadState;
  readAt?: string | null;
  unreadAt?: string | null;
  shortlisted?: boolean;
  shortlistedAt?: string | null;
  trashed?: boolean;
  trashedAt?: string | null;
  replied?: boolean;
  repliedAt?: string | null;
  replyHistory?: unknown[];
  declined?: boolean;
  declinedAt?: string | null;
  manualPriority?: boolean | null;
  manualPriorityAt?: string | null;
  active_work_status?: BundleOrganizerActiveWorkStatus | string | null;
  v7_final_priority?: string;
  priorityBadge?: string;
  reason?: string;
  identityKey?: string;
  sortTimestamp?: number;
};

export type BundleOrganizerInternalClassification =
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
  | "incomplete_demo"
  | "unknown";

export type BundleOrganizerWorkspaceMessage = {
  id: string;
  sender: string;
  from?: string;
  to?: string;
  cc?: string;
  subject: string;
  snippet: string;
  body: string[];
  bodyHtml?: string;
  timestamp: string;
  createdAt?: string;
  threadId?: string;
  threadGroupingKey?: string;
  sourceMailbox: string;
  manualCategory?: "demo" | "promo";
  manualCategoryAt?: string | null;
  manualLabelCategory?: "demo" | "promo";
  learnedLabelCategory?: "demo" | "promo";
  internalClassification?: BundleOrganizerInternalClassification;
  category?: string;
  signal?: string;
  uiSignal?: string;
  unread?: boolean;
  readState?: BundleOrganizerReadState;
  readAt?: string | null;
  unreadAt?: string | null;
  shortlisted?: boolean;
  shortlistedAt?: string | null;
  trashed?: boolean;
  trashedAt?: string | null;
  replied?: boolean;
  repliedAt?: string | null;
  replyHistory?: unknown[];
  declined?: boolean;
  declinedAt?: string | null;
  manualPriority?: boolean | null;
  manualPriorityAt?: string | null;
  active_work_status?: BundleOrganizerActiveWorkStatus | string | null;
  v7_final_priority?: string;
  priorityBadge?: string;
  reason?: string;
  identityKey?: string;
  sortTimestamp?: number;
};

type BundleOrganizerSurfaceProps = {
  liveMessages?: BundleOrganizerWorkspaceMessage[];
  hasLiveWorkspaceData?: boolean;
  connectedInboxCount?: number;
  showLocalPriorityNav?: boolean;
};

const navItems: Array<{
  id: BundleOrganizerView;
  label: string;
  icon: BundleOrganizerView;
}> = [
  { id: "priority", label: "Priority", icon: "priority" },
  { id: "shortlist", label: "Shortlist", icon: "shortlist" },
  { id: "demo", label: "Demo Inbox", icon: "demo" },
  { id: "promo", label: "Promo Inbox", icon: "promo" },
  { id: "trash", label: "Trash", icon: "trash" },
];

const allSourceFilterId = "all";
const demoStatusFilterOptions: Array<{
  id: BundleOrganizerDemoStatusFilter;
  label: string;
}> = [
  { id: "all", label: "All status" },
  { id: "unread", label: "Unread" },
  { id: "shortlisted", label: "Shortlisted" },
  { id: "priority", label: "Priority" },
  { id: "replied", label: "Replied" },
  { id: "declined", label: "Declined" },
];
const promoStatusFilterOptions: Array<{
  id: BundleOrganizerPromoStatusFilter;
  label: string;
}> = [
  { id: "all", label: "All status" },
  { id: "unread", label: "Unread" },
  { id: "read", label: "Read" },
  { id: "shortlisted", label: "Shortlisted" },
  { id: "priority", label: "Priority" },
];
const dateSortOptions: Array<{ id: BundleOrganizerDateSort; label: string }> = [
  { id: "newest", label: "Newest first" },
  { id: "oldest", label: "Oldest first" },
];

const viewCopy: Record<
  BundleOrganizerView,
  {
    title: string;
    eyebrow: string;
    description: string;
    emptyTitle: string;
    emptyDescription: string;
  }
> = {
  priority: {
    title: "Priority",
    eyebrow: "Active Work Queue",
    description:
      "Active follow-ups and manually prioritized messages. Counts reflect Organizer unread state.",
    emptyTitle: "No priority messages.",
    emptyDescription: "Priority demo and promo messages will appear here.",
  },
  shortlist: {
    title: "Shortlist",
    eyebrow: "Saved for follow-up",
    description:
      "Messages saved for follow-up. Counts reflect Organizer unread state.",
    emptyTitle: "No shortlisted messages yet.",
    emptyDescription: "Shortlisted demos and promos will appear here.",
  },
  demo: {
    title: "Demo Inbox",
    eyebrow: "Unified Demo Intake",
    description:
      "Focused demo queue across connected inboxes. Counts reflect Organizer unread state.",
    emptyTitle: "No demo messages.",
    emptyDescription: "Demo messages from connected inboxes will appear here.",
  },
  promo: {
    title: "Promo Inbox",
    eyebrow: "Unified Promo Review",
    description:
      "Focused promo queue across connected inboxes. Counts reflect Organizer unread state.",
    emptyTitle: "No promo messages.",
    emptyDescription: "Promo messages from connected inboxes will appear here.",
  },
  trash: {
    title: "Trash",
    eyebrow: "Organizer-local trash",
    description:
      "Messages removed from Organizer views. This does not delete mailbox email.",
    emptyTitle: "Trash is empty.",
    emptyDescription: "Messages moved out of Organizer views will appear here.",
  },
};

const bundleModeDisabledReason = "Not connected in Bundle mode yet";
const contextMenuGap = 8;
const contextMenuViewportPadding = 12;
const contextMenuWidth = 190;
const contextMenuEstimatedActionHeight = 37;
const contextMenuVerticalChrome = 12;
const shortlistedPillClass =
  "rounded-full border border-[rgba(246,183,91,0.24)] bg-[rgba(214,137,45,0.14)] px-2 py-0.5 text-[0.68rem] font-medium uppercase tracking-[0.1em] text-[rgba(255,204,125,0.9)]";
const trashedPillClass =
  "rounded-full border border-white/15 bg-white/8 px-2 py-0.5 text-[0.68rem] font-medium uppercase tracking-[0.1em] text-[rgba(245,239,229,0.66)]";
const manualPillClass =
  "rounded-full border border-white/12 bg-white/6 px-2 py-0.5 text-[0.68rem] font-medium uppercase tracking-[0.1em] text-[rgba(245,239,229,0.6)]";
const allowedEmailTags = new Set([
  "a",
  "b",
  "blockquote",
  "br",
  "div",
  "em",
  "h1",
  "h2",
  "h3",
  "h4",
  "hr",
  "i",
  "li",
  "ol",
  "p",
  "span",
  "strong",
  "table",
  "tbody",
  "td",
  "th",
  "thead",
  "tr",
  "u",
  "ul",
]);
const unsafeEmailTags = new Set([
  "applet",
  "base",
  "button",
  "embed",
  "form",
  "frame",
  "frameset",
  "iframe",
  "input",
  "link",
  "math",
  "meta",
  "object",
  "script",
  "select",
  "style",
  "svg",
  "textarea",
]);
const linkPattern = /(https?:\/\/[^\s<>"']+|www\.[^\s<>"']+)/gi;
const trailingLinkPunctuation = /[.,!?;:)\]}]+$/;
const maxEmbeddedLinkPreviews = 3;
const fallbackPlaylistSoundCloudHeight = 360;
const fallbackTrackSoundCloudHeight = 166;
const maxPlaylistSoundCloudHeight = 520;
const maxTrackSoundCloudHeight = 190;
const minPlaylistSoundCloudHeight = 260;
const minTrackSoundCloudHeight = 166;
const soundCloudResolveEndpoint = "/api/organizer/soundcloud-resolve";
const soundCloudLinkPattern =
  /(?:https?:\/\/(?:www\.)?soundcloud\.com\/[^\s<>"']+|https?:\/\/on\.soundcloud\.com\/[^\s<>"']+|www\.soundcloud\.com\/[^\s<>"']+)/gi;
const dropboxLinkPattern =
  /(?:https?:\/\/(?:www\.)?dropbox\.com\/[^\s<>"']+|https?:\/\/dl\.dropboxusercontent\.com\/[^\s<>"']+|(?:www\.)?dropbox\.com\/[^\s<>"']+|dl\.dropboxusercontent\.com\/[^\s<>"']+)/gi;
const allowedSoundCloudHosts = new Set([
  "soundcloud.com",
  "www.soundcloud.com",
  "on.soundcloud.com",
]);
const finalSoundCloudHosts = new Set(["soundcloud.com", "www.soundcloud.com"]);
const reservedSoundCloudPaths = new Set([
  "about",
  "charts",
  "discover",
  "for",
  "imprint",
  "jobs",
  "pages",
  "popular",
  "premium",
  "search",
  "settings",
  "stream",
  "terms-of-use",
  "upload",
  "you",
]);
const allowedDropboxHosts = new Set([
  "dropbox.com",
  "www.dropbox.com",
  "dl.dropboxusercontent.com",
]);
const dropboxAudioExtensions = new Set([
  "aac",
  "aif",
  "aiff",
  "flac",
  "m4a",
  "mp3",
  "ogg",
  "wav",
]);
const soundCloudResolveCache = new Map<string, Promise<SoundCloudResolveResponse>>();
const quoteMarkerPatterns = [
  /^\s*>/,
  /^\s*On .+ wrote:\s*$/i,
  /^\s*Op .+ schreef:\s*$/i,
  /^\s*Il giorno .+ ha scritto:\s*$/i,
  /^\s*From:\s+/i,
  /^\s*Sent:\s+/i,
  /^\s*Subject:\s+/i,
  /^\s*-{2,}\s*Original Message\s*-{2,}\s*$/i,
  /^\s*-{2,}\s*Forwarded message\s*-{2,}\s*$/i,
  /^\s*Forwarded message\s*$/i,
];

type MessageBodySegment = {
  kind: "message" | "quote";
  lines: string[];
};

type SanitizedEmailHtml = {
  blockedImageCount: number;
  html: string | null;
};

type SoundCloudPreviewCandidate = {
  href: string;
};

type DropboxPreviewLink = {
  audioSrc?: string;
  href: string;
  isAudio: boolean;
  label: string;
  typeLabel: "Dropbox audio" | "Dropbox file" | "Dropbox folder";
  urlLabel: string;
};

type SoundCloudPreviewLink = {
  canonicalUrl: string;
  height: number;
  href: string;
  iframeSrc: string;
  title?: string;
};

type SoundCloudResolveResponse = {
  ok: boolean;
  canonicalUrl?: string;
  height?: number | null;
  iframeSrc?: string;
  originalUrl?: string;
  reason?: string;
  title?: string | null;
};

type SoundCloudResolutionState = {
  loading: boolean;
  resolvedPreviews: Record<string, SoundCloudPreviewLink | null>;
  signature: string;
};

function resolvePriorityReason(message: BundleOrganizerMessage) {
  if (message.reason) {
    return message.reason;
  }

  if (message.manualPriority === true) {
    return "Manual priority.";
  }

  if (
    message.active_work_status &&
    ACTIVE_WORK_PRIORITY_STATUSES.has(formatOrganizerSignal(message.active_work_status))
  ) {
    return message.active_work_status.replace(/_/g, " ");
  }

  if (formatOrganizerSignal(message.v7_final_priority) === "priority") {
    return "Priority signal.";
  }

  return null;
}

function isSafeUrl(value: string) {
  const normalizedValue = value.trim();

  if (!normalizedValue) {
    return false;
  }

  if (/^(javascript:|vbscript:|data:|file:)/i.test(normalizedValue)) {
    return false;
  }

  if (/^mailto:/i.test(normalizedValue)) {
    return true;
  }

  return /^https?:\/\//i.test(normalizedValue);
}

function normalizeHref(value: string) {
  const trimmedValue = value.trim();

  if (/^www\./i.test(trimmedValue)) {
    return `https://${trimmedValue}`;
  }

  return trimmedValue;
}

function normalizePotentialUrl(value: string) {
  const trimmedValue = value.trim().replace(/&amp;/gi, "&");
  const withoutTrailingPunctuation = trimmedValue.replace(
    trailingLinkPunctuation,
    "",
  );

  if (
    /^www\./i.test(withoutTrailingPunctuation) ||
    /^dropbox\.com\//i.test(withoutTrailingPunctuation) ||
    /^dl\.dropboxusercontent\.com\//i.test(withoutTrailingPunctuation)
  ) {
    return `https://${withoutTrailingPunctuation}`;
  }

  return withoutTrailingPunctuation;
}

function stripSoundCloudEmailSuffix(value: string) {
  const commonEmailSuffixes = [
    "Best",
    "Regards",
    "Thanks",
    "ThankYou",
    "Cheers",
    "Sincerely",
  ];

  try {
    const url = new URL(value);
    const hostname = url.hostname.toLowerCase();
    if (!allowedSoundCloudHosts.has(hostname)) {
      return value;
    }

    const pathParts = url.pathname.split("/").filter(Boolean);
    const lastPathPart = pathParts[pathParts.length - 1] ?? "";
    if (!lastPathPart) {
      return value;
    }

    const suffix = commonEmailSuffixes.find(
      (candidate) =>
        lastPathPart.endsWith(candidate) &&
        lastPathPart.length > candidate.length + 12,
    );
    if (!suffix) {
      return value;
    }

    pathParts[pathParts.length - 1] = lastPathPart.slice(0, -suffix.length);
    url.pathname = `/${pathParts.join("/")}`;
    return url.toString();
  } catch {
    return value;
  }
}

function normalizePotentialSoundCloudUrl(value: string) {
  const normalizedValue = normalizePotentialUrl(value);
  return stripSoundCloudEmailSuffix(normalizedValue);
}

function collectPlainBodyUrls(
  body: string[] | string | undefined,
  pattern: RegExp,
) {
  const bodyText = Array.isArray(body) ? body.join("\n\n") : body ?? "";
  return Array.from(bodyText.matchAll(pattern), (match) => match[0]);
}

function collectHtmlUrls(bodyHtml: string | undefined, pattern: RegExp) {
  const html = bodyHtml?.trim() ?? "";
  if (!html) {
    return [];
  }

  if (typeof DOMParser === "undefined") {
    return Array.from(html.matchAll(pattern), (match) => match[0]);
  }

  const parsedDocument = new DOMParser().parseFromString(html, "text/html");
  const hrefs = Array.from(
    parsedDocument.querySelectorAll<HTMLAnchorElement>("a[href]"),
    (link) => link.getAttribute("href") ?? "",
  ).filter((href) => href.trim().length > 0);
  const visibleUrls = Array.from(
    (parsedDocument.body.textContent ?? "").matchAll(pattern),
    (match) => match[0],
  );

  return [...hrefs, ...visibleUrls];
}

function collectPreviewItems<T>(
  body: string[] | string | undefined,
  bodyHtml: string | undefined,
  snippet: string | undefined,
  pattern: RegExp,
  buildItem: (value: string) => T | null,
  getKey: (item: T) => string,
) {
  const seen = new Set<string>();
  const items: T[] = [];

  [
    ...collectHtmlUrls(bodyHtml, pattern),
    ...collectPlainBodyUrls(body, pattern),
    ...collectPlainBodyUrls(snippet, pattern),
  ].forEach((value) => {
    const item = buildItem(value);
    if (!item) {
      return;
    }

    const key = getKey(item);
    if (seen.has(key)) {
      return;
    }

    seen.add(key);
    items.push(item);
  });

  return items;
}

function safeDecodeURIComponent(value: string) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function getUrlLabel(url: URL) {
  const pathLabel = url.pathname.replace(/\/+$/, "") || "/";
  return `${url.hostname}${pathLabel}`;
}

function sanitizeDimension(value: string | null) {
  const trimmedValue = value?.trim() ?? "";

  if (!trimmedValue) {
    return null;
  }

  if (/^\d{1,4}%?$/.test(trimmedValue)) {
    return trimmedValue;
  }

  return null;
}

function sanitizeSpanValue(value: string | null) {
  const trimmedValue = value?.trim() ?? "";

  if (!/^\d{1,2}$/.test(trimmedValue)) {
    return null;
  }

  const parsedValue = Number.parseInt(trimmedValue, 10);
  return parsedValue > 0 && parsedValue <= 20 ? String(parsedValue) : null;
}

function moveChildrenBeforeElement(element: Element) {
  const parent = element.parentNode;

  if (!parent) {
    return;
  }

  while (element.firstChild) {
    parent.insertBefore(element.firstChild, element);
  }

  parent.removeChild(element);
}

function replaceImageWithPlaceholder(
  documentRef: Document,
  element: Element,
  options: { label?: string } = {},
) {
  const altText = options.label || element.getAttribute("alt")?.trim() || "Image";
  const placeholder = documentRef.createElement("div");
  placeholder.setAttribute("data-bundle-organizer-email-image-placeholder", "true");
  placeholder.setAttribute(
    "style",
    "display:inline-flex;max-width:100%;min-width:180px;white-space:normal;word-break:normal;overflow-wrap:anywhere;",
  );
  placeholder.textContent = `Image blocked: ${altText}`;
  element.replaceWith(placeholder);
}

function sanitizeElement(
  documentRef: Document,
  element: Element,
  stats: { blockedImageCount: number },
) {
  const tagName = element.tagName.toLowerCase();

  if (tagName === "img") {
    stats.blockedImageCount += 1;
    replaceImageWithPlaceholder(documentRef, element);
    return;
  }

  if (unsafeEmailTags.has(tagName)) {
    element.remove();
    return;
  }

  if (!allowedEmailTags.has(tagName)) {
    moveChildrenBeforeElement(element);
    return;
  }

  Array.from(element.attributes).forEach((attribute) => {
    const attributeName = attribute.name.toLowerCase();
    const attributeValue = attribute.value;

    if (
      attributeName.startsWith("on") ||
      attributeName === "style" ||
      attributeName === "class" ||
      attributeName.startsWith("data-")
    ) {
      element.removeAttribute(attribute.name);
      return;
    }

    if (tagName === "a" && attributeName === "href") {
      const href = normalizeHref(attributeValue);
      if (isSafeUrl(href)) {
        element.setAttribute("href", href);
      } else {
        element.removeAttribute(attribute.name);
      }
      return;
    }

    if (attributeName === "title") {
      element.setAttribute("title", attributeValue.slice(0, 240));
      return;
    }

    if (
      ["table", "td", "th"].includes(tagName) &&
      ["width", "height"].includes(attributeName)
    ) {
      const safeDimension = sanitizeDimension(attributeValue);
      if (safeDimension) {
        element.setAttribute(attributeName, safeDimension);
      } else {
        element.removeAttribute(attribute.name);
      }
      return;
    }

    if (
      ["td", "th"].includes(tagName) &&
      ["colspan", "rowspan"].includes(attributeName)
    ) {
      const safeSpanValue = sanitizeSpanValue(attributeValue);
      if (safeSpanValue) {
        element.setAttribute(attributeName, safeSpanValue);
      } else {
        element.removeAttribute(attribute.name);
      }
      return;
    }

    element.removeAttribute(attribute.name);
  });

  if (element instanceof HTMLAnchorElement) {
    const href = element.getAttribute("href");
    if (!href || !isSafeUrl(href)) {
      element.removeAttribute("href");
    }
    element.setAttribute("target", "_blank");
    element.setAttribute("rel", "noopener noreferrer nofollow");
  }
}

function sanitizeEmailHtml(bodyHtml?: string): SanitizedEmailHtml {
  const normalizedHtml = bodyHtml?.trim() ?? "";

  if (!normalizedHtml || typeof DOMParser === "undefined") {
    return { blockedImageCount: 0, html: null };
  }

  const parsedDocument = new DOMParser().parseFromString(normalizedHtml, "text/html");
  const body = parsedDocument.body;
  const stats = { blockedImageCount: 0 };

  body.querySelectorAll("*").forEach((element) => {
    sanitizeElement(parsedDocument, element, stats);
  });

  const sanitizedHtml = body.innerHTML.trim();
  const hasRenderableContent =
    Boolean(body.textContent?.replace(/\s+/g, "").length) ||
    Boolean(body.querySelector("a, blockquote, hr, li, table, td, th"));

  return {
    blockedImageCount: stats.blockedImageCount,
    html: hasRenderableContent ? sanitizedHtml : null,
  };
}

function isQuoteMarkerLine(line: string) {
  return quoteMarkerPatterns.some((pattern) => pattern.test(line));
}

function splitParagraphIntoSegments(paragraph: string): MessageBodySegment[] {
  const lines = paragraph.split(/\r?\n/);
  const segments: MessageBodySegment[] = [];
  let quoteStarted = false;

  for (const line of lines) {
    if (isQuoteMarkerLine(line)) {
      quoteStarted = true;
    }

    const kind: MessageBodySegment["kind"] = quoteStarted ? "quote" : "message";
    const currentSegment = segments[segments.length - 1];
    if (currentSegment?.kind === kind) {
      currentSegment.lines.push(line);
    } else {
      segments.push({ kind, lines: [line] });
    }
  }

  return segments;
}

function renderLinkedText(line: string, keyPrefix: string) {
  const parts: ReactNode[] = [];
  let lastIndex = 0;

  for (const match of line.matchAll(linkPattern)) {
    const rawUrl = match[0];
    const matchIndex = match.index ?? 0;
    const trailingMatch = rawUrl.match(trailingLinkPunctuation);
    const trailingText = trailingMatch?.[0] ?? "";
    const displayUrl = trailingText
      ? rawUrl.slice(0, -trailingText.length)
      : rawUrl;

    if (matchIndex > lastIndex) {
      parts.push(line.slice(lastIndex, matchIndex));
    }

    if (displayUrl) {
      const href = normalizeHref(displayUrl);
      parts.push(
        <a
          key={`${keyPrefix}-link-${matchIndex}`}
          href={href}
          target="_blank"
          rel="noopener noreferrer nofollow"
          className="break-all font-medium text-[rgba(48,72,61,0.88)] underline decoration-[rgba(48,72,61,0.28)] underline-offset-4 transition-colors hover:text-[rgba(35,58,47,0.96)]"
        >
          {displayUrl}
        </a>,
      );
    }

    if (trailingText) {
      parts.push(trailingText);
    }

    lastIndex = matchIndex + rawUrl.length;
  }

  if (lastIndex < line.length) {
    parts.push(line.slice(lastIndex));
  }

  return parts.length > 0 ? parts : line;
}

function renderBodyLines(lines: string[], keyPrefix: string) {
  return lines.map((line, lineIndex) => (
    <span key={`${keyPrefix}-line-${lineIndex}`}>
      {lineIndex > 0 ? <br /> : null}
      {renderLinkedText(line, `${keyPrefix}-${lineIndex}`)}
    </span>
  ));
}

function normalizePlainBody(body?: string[] | string) {
  if (Array.isArray(body) && body.some((line) => line.trim().length > 0)) {
    return body;
  }

  if (typeof body === "string" && body.trim()) {
    return body.split(/\n{2,}/);
  }

  return ["No text body available."];
}

function isClearlySoundCloudTrackOrPlaylist(url: URL) {
  const hostname = url.hostname.toLowerCase();
  if (!finalSoundCloudHosts.has(hostname)) {
    return false;
  }

  if (url.protocol !== "https:" && url.protocol !== "http:") {
    return false;
  }

  if (url.username || url.password) {
    return false;
  }

  const pathParts = url.pathname.split("/").filter(Boolean);
  const [profileOrCollection, secondPart] = pathParts.map((part) =>
    part.toLowerCase(),
  );

  if (
    pathParts.length < 2 ||
    !profileOrCollection ||
    reservedSoundCloudPaths.has(profileOrCollection)
  ) {
    return false;
  }

  if (secondPart === "sets") {
    return pathParts.length >= 3;
  }

  return Boolean(secondPart);
}

function buildSoundCloudPreviewCandidate(
  value: string,
): SoundCloudPreviewCandidate | null {
  const normalizedValue = normalizePotentialSoundCloudUrl(value);

  try {
    const soundCloudUrl = new URL(normalizedValue);
    const hostname = soundCloudUrl.hostname.toLowerCase();
    if (
      (soundCloudUrl.protocol !== "https:" && soundCloudUrl.protocol !== "http:") ||
      !allowedSoundCloudHosts.has(hostname) ||
      soundCloudUrl.username ||
      soundCloudUrl.password
    ) {
      return null;
    }

    if (hostname === "on.soundcloud.com") {
      if (!soundCloudUrl.pathname.split("/").filter(Boolean).length) {
        return null;
      }
    } else if (!isClearlySoundCloudTrackOrPlaylist(soundCloudUrl)) {
      return null;
    }

    return {
      href: soundCloudUrl.toString(),
    };
  } catch {
    return null;
  }
}

function isSafeSoundCloudIframeSrc(value?: string) {
  try {
    const iframeUrl = new URL(value ?? "");
    return (
      iframeUrl.protocol === "https:" &&
      iframeUrl.hostname.toLowerCase() === "w.soundcloud.com" &&
      iframeUrl.pathname.startsWith("/player/")
    );
  } catch {
    return false;
  }
}

function soundCloudUrlContainsSet(value?: string | null) {
  if (!value) {
    return false;
  }

  try {
    return new URL(value).pathname
      .split("/")
      .filter(Boolean)
      .some((part) => part.toLowerCase() === "sets");
  } catch {
    return /(^|\/)sets(\/|$)/i.test(value);
  }
}

function getSoundCloudIframeTargetUrl(iframeSrc?: string) {
  try {
    return new URL(iframeSrc ?? "").searchParams.get("url");
  } catch {
    return null;
  }
}

function isPlaylistPreview(input: {
  canonicalUrl?: string;
  height?: number | null;
  href: string;
  iframeSrc?: string;
}) {
  return (
    soundCloudUrlContainsSet(input.href) ||
    soundCloudUrlContainsSet(input.canonicalUrl) ||
    soundCloudUrlContainsSet(getSoundCloudIframeTargetUrl(input.iframeSrc)) ||
    Boolean(input.height && input.height > 200)
  );
}

function clampSoundCloudHeight(value?: number | null, isPlaylist = false) {
  const fallbackHeight = isPlaylist
    ? fallbackPlaylistSoundCloudHeight
    : fallbackTrackSoundCloudHeight;
  if (!Number.isFinite(value ?? NaN)) {
    return fallbackHeight;
  }

  const minHeight = isPlaylist ? minPlaylistSoundCloudHeight : minTrackSoundCloudHeight;
  const maxHeight = isPlaylist ? maxPlaylistSoundCloudHeight : maxTrackSoundCloudHeight;
  return Math.min(
    maxHeight,
    Math.max(minHeight, Math.round(value ?? fallbackHeight)),
  );
}

function buildSoundCloudPreviewLinks(
  candidates: SoundCloudPreviewCandidate[],
  resolvedPreviews: Record<string, SoundCloudPreviewLink | null>,
) {
  const seen = new Set<string>();
  const previews: SoundCloudPreviewLink[] = [];

  for (const candidate of candidates) {
    const preview = resolvedPreviews[candidate.href];
    if (!preview) {
      continue;
    }

    const previewKey = preview.iframeSrc || preview.canonicalUrl;
    if (seen.has(previewKey)) {
      continue;
    }

    seen.add(previewKey);
    previews.push(preview);
    if (previews.length >= maxEmbeddedLinkPreviews) {
      break;
    }
  }

  return previews;
}

async function resolveSoundCloudCandidate(
  href: string,
): Promise<SoundCloudResolveResponse> {
  const cachedResolve = soundCloudResolveCache.get(href);
  if (cachedResolve) {
    return cachedResolve;
  }

  const resolvePromise = fetch(soundCloudResolveEndpoint, {
    method: "POST",
    credentials: "include",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ url: href }),
  })
    .then(async (response) => {
      const payload = (await response.json()) as SoundCloudResolveResponse;
      if (!response.ok || payload.ok !== true || !payload.canonicalUrl) {
        return {
          ok: false,
          originalUrl: href,
          reason: payload.reason ?? "resolve_failed",
        };
      }
      if (!isSafeSoundCloudIframeSrc(payload.iframeSrc)) {
        return {
          ok: false,
          originalUrl: href,
          reason: "invalid_iframe_src",
        };
      }
      return payload;
    })
    .catch(() => ({
      ok: false,
      originalUrl: href,
      reason: "resolve_failed",
    }));

  soundCloudResolveCache.set(href, resolvePromise);
  return resolvePromise;
}

function getDropboxPathParts(url: URL) {
  return url.pathname
    .split("/")
    .map((part) => part.trim())
    .filter(Boolean);
}

function isDropboxSharedFileOrFolder(url: URL) {
  const hostname = url.hostname.toLowerCase();
  if (
    !allowedDropboxHosts.has(hostname) ||
    (url.protocol !== "https:" && url.protocol !== "http:") ||
    url.username ||
    url.password
  ) {
    return false;
  }

  const pathParts = getDropboxPathParts(url);
  if (pathParts.length === 0) {
    return false;
  }

  const [root, shareType] = pathParts.map((part) => part.toLowerCase());
  if (root === "scl") {
    return (
      (shareType === "fi" && pathParts.length >= 4) ||
      (shareType === "fo" && pathParts.length >= 3)
    );
  }

  if (root === "s" || root === "sh") {
    return pathParts.length >= 2;
  }

  return hostname === "dl.dropboxusercontent.com" && pathParts.length >= 2;
}

function getDropboxLinkType(url: URL): "Dropbox file" | "Dropbox folder" {
  const [root, shareType] = getDropboxPathParts(url).map((part) =>
    part.toLowerCase(),
  );
  if ((root === "scl" && shareType === "fo") || root === "sh") {
    return "Dropbox folder";
  }

  return "Dropbox file";
}

function getDropboxFilename(url: URL) {
  const pathParts = getDropboxPathParts(url);
  const hostname = url.hostname.toLowerCase();
  const [root, shareType] = pathParts.map((part) => part.toLowerCase());
  const filenameIndex =
    root === "scl" && shareType === "fi" ? 3 : root === "s" ? 2 : -1;

  if (hostname === "dl.dropboxusercontent.com" && pathParts.length > 0) {
    return pathParts[pathParts.length - 1];
  }

  return filenameIndex >= 0 && pathParts.length > filenameIndex
    ? pathParts[filenameIndex]
    : null;
}

function getDropboxFileExtension(url: URL) {
  const filename = getDropboxFilename(url);
  const extensionMatch = filename?.match(/\.([a-z0-9]+)$/i);
  return extensionMatch?.[1]?.toLowerCase() ?? "";
}

function buildDropboxAudioSrc(url: URL) {
  const audioUrl = new URL(url.toString());
  const hostname = audioUrl.hostname.toLowerCase();
  if (hostname === "dropbox.com" || hostname === "www.dropbox.com") {
    audioUrl.searchParams.delete("dl");
    audioUrl.searchParams.set("raw", "1");
  }
  return audioUrl.toString();
}

function buildDropboxPreviewLink(value: string): DropboxPreviewLink | null {
  const normalizedValue = normalizePotentialUrl(value);

  try {
    const dropboxUrl = new URL(normalizedValue);
    if (!isDropboxSharedFileOrFolder(dropboxUrl)) {
      return null;
    }

    const typeLabel = getDropboxLinkType(dropboxUrl);
    const maybeFilename = getDropboxFilename(dropboxUrl);
    const isAudio =
      typeLabel === "Dropbox file" &&
      dropboxAudioExtensions.has(getDropboxFileExtension(dropboxUrl));

    return {
      audioSrc: isAudio ? buildDropboxAudioSrc(dropboxUrl) : undefined,
      href: dropboxUrl.toString(),
      isAudio,
      label:
        typeLabel === "Dropbox folder"
          ? "Shared folder"
          : maybeFilename
          ? safeDecodeURIComponent(maybeFilename)
          : "Shared file",
      typeLabel: isAudio ? "Dropbox audio" : typeLabel,
      urlLabel: getUrlLabel(dropboxUrl),
    };
  } catch {
    return null;
  }
}

function normalizeWorkspaceMessages(
  liveMessages: BundleOrganizerWorkspaceMessage[],
): BundleOrganizerMessage[] {
  const messagesByIdentity = new Map<string, BundleOrganizerMessage>();

  liveMessages
    .map((message): BundleOrganizerMessage => ({
      ...message,
      id: `workspace-${message.id}`,
      ui_signal: message.uiSignal,
      priorityBadge:
        message.priorityBadge ??
        (message.internalClassification === "high_priority_demo"
          ? "High-priority demo"
          : undefined),
    }))
    .forEach((message) => {
      const identityKey = message.identityKey ?? message.id;
      const existingMessage = messagesByIdentity.get(identityKey);

      if (
        !existingMessage ||
        (message.sortTimestamp ?? 0) >= (existingMessage.sortTimestamp ?? 0)
      ) {
        messagesByIdentity.set(identityKey, message);
      }
    });

  return Array.from(messagesByIdentity.values()).sort(
    (first, second) => (second.sortTimestamp ?? 0) - (first.sortTimestamp ?? 0),
  );
}

function applyBundleWorkflowState(
  messages: BundleOrganizerMessage[],
  workflowState: BundleOrganizerWorkflowState,
) {
  return messages.map((message) => {
    const workflowEntry = workflowState[getWorkflowIdentityKey(message)];

    if (!workflowEntry) {
      return message;
    }

    return {
      ...message,
      shortlisted:
        typeof workflowEntry.shortlisted === "boolean"
          ? workflowEntry.shortlisted
          : message.shortlisted,
      shortlistedAt: workflowEntry.shortlistedAt ?? message.shortlistedAt,
      unread:
        workflowEntry.readState === "read"
          ? false
          : workflowEntry.readState === "unread"
          ? true
          : message.unread,
      readState:
        workflowEntry.readState === "read" ||
        workflowEntry.readState === "unread"
          ? workflowEntry.readState
          : message.readState,
      readAt: workflowEntry.readAt ?? message.readAt,
      unreadAt: workflowEntry.unreadAt ?? message.unreadAt,
      manualCategory:
        workflowEntry.manualCategory === "demo" ||
        workflowEntry.manualCategory === "promo"
          ? workflowEntry.manualCategory
          : message.manualCategory,
      manualCategoryAt:
        workflowEntry.manualCategoryAt ?? message.manualCategoryAt,
      manualPriority:
        typeof workflowEntry.manualPriority === "boolean"
          ? workflowEntry.manualPriority
          : message.manualPriority,
      manualPriorityAt:
        workflowEntry.manualPriorityAt ?? message.manualPriorityAt,
      trashed:
        typeof workflowEntry.trashed === "boolean"
          ? workflowEntry.trashed
          : message.trashed,
      trashedAt: workflowEntry.trashedAt ?? message.trashedAt,
    };
  });
}

function getMessagesForView(
  view: BundleOrganizerView,
  liveMessages: BundleOrganizerMessage[],
) {
  if (view === "trash") {
    return liveMessages.filter((message) => message.trashed === true);
  }

  const activeMessages = liveMessages.filter((message) => message.trashed !== true);

  if (view === "priority") {
    return activeMessages.filter(shouldShowInOrganizerPriority);
  }

  if (view === "shortlist") {
    return activeMessages.filter((message) => message.shortlisted === true);
  }

  if (view === "demo") {
    return activeMessages.filter(shouldShowInDemoInbox);
  }

  return activeMessages.filter(shouldShowInPromoInbox);
}

function getCounts(liveMessages: BundleOrganizerMessage[]) {
  return navItems.reduce<Partial<Record<BundleOrganizerView, number>>>((counts, item) => {
    counts[item.id] = countUnreadMessages(getMessagesForView(item.id, liveMessages));
    return counts;
  }, {});
}

function getViewLabel(view: BundleOrganizerView) {
  return navItems.find((item) => item.id === view)?.label ?? viewCopy[view].title;
}

function resolveMessageSourceFilterId(message: BundleOrganizerMessage) {
  return message.sourceMailbox.trim() || "";
}

function buildSourceFilterOptions(messages: BundleOrganizerMessage[]) {
  const optionsById = new Map<
    string,
    { count: number; id: string; label: string }
  >();

  messages.forEach((message) => {
    const id = resolveMessageSourceFilterId(message);

    if (!id) {
      return;
    }

    const existingOption = optionsById.get(id);

    if (existingOption) {
      existingOption.count += 1;
      return;
    }

    optionsById.set(id, {
      count: 1,
      id,
      label: message.sourceMailbox.trim() || "Unknown inbox",
    });
  });

  return Array.from(optionsById.values()).sort((first, second) =>
    first.label.localeCompare(second.label),
  );
}

function filterMessagesBySource(
  messages: BundleOrganizerMessage[],
  sourceFilterId: string,
) {
  if (sourceFilterId === allSourceFilterId) {
    return messages;
  }

  return messages.filter(
    (message) => resolveMessageSourceFilterId(message) === sourceFilterId,
  );
}

function filterMessagesByDemoStatus(
  messages: BundleOrganizerMessage[],
  statusFilter: BundleOrganizerDemoStatusFilter,
) {
  if (statusFilter === "all") {
    return messages;
  }

  return messages.filter((message) => {
    if (statusFilter === "unread") {
      return message.unread === true;
    }

    if (statusFilter === "shortlisted") {
      return message.shortlisted === true;
    }

    if (statusFilter === "priority") {
      return shouldShowInOrganizerPriority(message);
    }

    if (statusFilter === "replied") {
      return (
        message.replied === true ||
        Boolean(message.repliedAt) ||
        Boolean(message.replyHistory?.length)
      );
    }

    if (statusFilter === "declined") {
      return message.declined === true || Boolean(message.declinedAt);
    }

    return true;
  });
}

function filterMessagesByPromoStatus(
  messages: BundleOrganizerMessage[],
  statusFilter: BundleOrganizerPromoStatusFilter,
) {
  return messages.filter((message) => {
    if (statusFilter === "unread") {
      return message.unread === true;
    }

    if (statusFilter === "read") {
      return message.unread !== true;
    }

    if (statusFilter === "shortlisted") {
      return message.shortlisted === true;
    }

    if (statusFilter === "priority") {
      return shouldShowInOrganizerPriority(message);
    }

    return true;
  });
}

function sortMessagesByDate(
  messages: BundleOrganizerMessage[],
  sortMode: BundleOrganizerDateSort,
) {
  return [...messages].sort((left, right) => {
    const leftTime = left.sortTimestamp ?? 0;
    const rightTime = right.sortTimestamp ?? 0;

    return sortMode === "oldest"
      ? leftTime - rightTime
      : rightTime - leftTime;
  });
}

function groupMessagesByExplicitThread(
  messages: BundleOrganizerMessage[],
): BundleOrganizerMessage[] {
  const representativeByThread = new Map<string, BundleOrganizerMessage>();

  messages.forEach((message) => {
    const threadGroupingKey = message.threadGroupingKey?.trim();

    if (!threadGroupingKey) {
      return;
    }

    const existingMessage = representativeByThread.get(threadGroupingKey);

    if (
      !existingMessage ||
      (message.sortTimestamp ?? 0) >= (existingMessage.sortTimestamp ?? 0)
    ) {
      representativeByThread.set(threadGroupingKey, message);
    }
  });

  const emittedThreadKeys = new Set<string>();
  return messages.flatMap((message) => {
    const threadGroupingKey = message.threadGroupingKey?.trim();

    if (!threadGroupingKey) {
      return [message];
    }

    if (emittedThreadKeys.has(threadGroupingKey)) {
      return [];
    }

    emittedThreadKeys.add(threadGroupingKey);
    return [representativeByThread.get(threadGroupingKey) ?? message];
  });
}

function buildGlobalSearchSourceRows(
  liveMessages: BundleOrganizerMessage[],
  includeTrash: boolean,
): BundleOrganizerSearchRow[] {
  const messagesById = new Map<string, BundleOrganizerSearchRow>();
  const sourceViews: BundleOrganizerView[] = includeTrash
    ? ["trash"]
    : ["demo", "promo", "priority", "shortlist"];

  sourceViews.forEach((view) => {
    getMessagesForView(view, liveMessages).forEach((message) => {
      if (!messagesById.has(message.id)) {
        messagesById.set(message.id, { message, sourceView: view });
      }
    });
  });

  return Array.from(messagesById.values()).sort(
    (first, second) =>
      (second.message.sortTimestamp ?? 0) - (first.message.sortTimestamp ?? 0),
  );
}

function doesMessageMatchSearch(message: BundleOrganizerMessage, searchQuery: string) {
  const normalizedSearchQuery = searchQuery.trim().toLowerCase();

  if (!normalizedSearchQuery) {
    return true;
  }

  const searchableFields = [
    message.sender,
    message.subject,
    message.snippet,
    message.sourceMailbox,
    message.priorityBadge,
    message.reason,
    message.internalClassification,
    message.category,
    message.manualCategory,
    resolveOrganizerCategory(message),
    resolveOrganizerCategory(message)?.replace(/_/g, " "),
    message.ui_signal,
    message.shortlisted ? "shortlisted" : null,
    message.manualPriority ? "manual priority" : null,
    message.manualPriority ? "priority" : null,
    message.trashed ? "trashed" : null,
    ...(Array.isArray(message.body) ? message.body : []),
  ];

  return searchableFields.some((value) =>
    (value ?? "").toLowerCase().includes(normalizedSearchQuery),
  );
}

function OrganizerIcon({ className = "" }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 72 72"
      className={className}
      fill="none"
    >
      <path
        d="M47.2 12.2C40.6 8.6 32.5 8.2 25.4 11.2C15.5 15.4 9.1 25 9.1 36C9.1 47 15.5 56.6 25.4 60.8C32.5 63.8 40.6 63.4 47.2 59.8"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="7.5"
      />
      <path d="M38.5 31.5V40.5" stroke="currentColor" strokeLinecap="round" strokeWidth="4.8" />
      <path d="M46 26.5V45.5" stroke="currentColor" strokeLinecap="round" strokeWidth="4.8" />
      <path d="M53.5 22.5V49.5" stroke="currentColor" strokeLinecap="round" strokeWidth="4.8" />
      <path d="M61 29.5V42.5" stroke="currentColor" strokeLinecap="round" strokeWidth="4.8" />
    </svg>
  );
}

function OrganizerNavIcon({ name }: { name: BundleOrganizerView }) {
  return (
    <svg
      aria-hidden="true"
      className="h-[18px] w-[18px] shrink-0"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.8"
      viewBox="0 0 24 24"
    >
      {name === "priority" ? (
        <>
          <path d="m13 2-2 8h7l-7 12 2-8H6l7-12Z" />
          <path d="M5 19h4" />
        </>
      ) : null}
      {name === "shortlist" ? (
        <path d="M6.5 4.75A2.25 2.25 0 0 1 8.75 2.5h6.5a2.25 2.25 0 0 1 2.25 2.25v16l-5.5-3.2-5.5 3.2v-16Z" />
      ) : null}
      {name === "demo" ? (
        <>
          <path d="M4 7.5A2.5 2.5 0 0 1 6.5 5h11A2.5 2.5 0 0 1 20 7.5v9A2.5 2.5 0 0 1 17.5 19h-11A2.5 2.5 0 0 1 4 16.5v-9Z" />
          <path d="M4 11h4.1a2.75 2.75 0 0 0 2.55 1.72h2.7A2.75 2.75 0 0 0 15.9 11H20" />
          <path d="M14.5 7.4v3.8" />
          <path d="M14.5 7.4 17 6.75" />
        </>
      ) : null}
      {name === "promo" ? (
        <>
          <path d="M4 13.5h3.25l8.25 4.25V6.25L7.25 10.5H4v3Z" />
          <path d="M7.25 13.5 8.5 19" />
          <path d="M18 9.1a3.25 3.25 0 0 1 0 5.8" />
          <path d="M20.25 7a6 6 0 0 1 0 10" />
        </>
      ) : null}
      {name === "trash" ? (
        <>
          <path d="M4 6.5h16" />
          <path d="M9.5 6.5v-2h5v2" />
          <path d="M7 6.5 8 20h8l1-13.5" />
          <path d="M10.5 10.5v5.5" />
          <path d="M13.5 10.5v5.5" />
        </>
      ) : null}
    </svg>
  );
}

function ContextMenuIcon({ name }: { name: BundleOrganizerContextMenuIconName }) {
  const paths: Record<BundleOrganizerContextMenuIconName, ReactNode> = {
    category: (
      <>
        <path d="M4 7h10" />
        <path d="M4 12h16" />
        <path d="M4 17h10" />
      </>
    ),
    decline: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="m9 9 6 6" />
        <path d="m15 9-6 6" />
      </>
    ),
    forward: <path d="M15 7l5 5-5 5M20 12H8a4 4 0 0 0-4 4v1" />,
    interest: (
      <path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21l7.8-7.6 1-1a5.5 5.5 0 0 0 0-7.8Z" />
    ),
    mail: (
      <>
        <rect height="14" rx="2" width="18" x="3" y="5" />
        <path d="m3 7 9 6 9-6" />
      </>
    ),
    mailOpen: (
      <>
        <path d="M3 9 12 3l9 6" />
        <path d="M21 9v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9" />
        <path d="m3 9 9 6 9-6" />
      </>
    ),
    priority: (
      <>
        <path d="M5 21V4" />
        <path d="M5 4h11l-1.5 4L16 12H5" />
      </>
    ),
    priorityOff: (
      <>
        <path d="M5 21V4" />
        <path d="M5 4h11l-1.5 4L16 12H5" />
        <path d="M4 4 20 20" />
      </>
    ),
    reply: <path d="M9 17 4 12l5-5M4 12h12a4 4 0 0 1 4 4v1" />,
    restore: (
      <>
        <path d="M3 12a9 9 0 1 0 3-6.7" />
        <path d="M3 4v6h6" />
      </>
    ),
    rule: (
      <>
        <path d="M4 6h16" />
        <path d="M7 12h10" />
        <path d="M10 18h4" />
      </>
    ),
    shortlist: (
      <path d="M6.5 4.75A2.25 2.25 0 0 1 8.75 2.5h6.5a2.25 2.25 0 0 1 2.25 2.25v16l-5.5-3.2-5.5 3.2v-16Z" />
    ),
    shortlistOff: (
      <>
        <path d="M6.5 4.75A2.25 2.25 0 0 1 8.75 2.5h6.5a2.25 2.25 0 0 1 2.25 2.25v16l-5.5-3.2-5.5 3.2v-16Z" />
        <path d="M4 4 20 20" />
      </>
    ),
    smartView: (
      <>
        <circle cx="11" cy="11" r="7" />
        <path d="m16 16 4 4" />
        <path d="M8.5 11h5" />
        <path d="M11 8.5v5" />
      </>
    ),
    trash: (
      <>
        <path d="M3 6h18" />
        <path d="M8 6V4h8v2" />
        <path d="M19 6 18 20H6L5 6" />
        <path d="M10 11v5" />
        <path d="M14 11v5" />
      </>
    ),
  };

  return (
    <svg
      aria-hidden="true"
      className="h-3.5 w-3.5 shrink-0"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.9"
      viewBox="0 0 24 24"
    >
      {paths[name]}
    </svg>
  );
}

function isPromoReminderMessage(message: BundleOrganizerMessage) {
  return (
    formatOrganizerSignal(message.internalClassification) === "promo_reminder" ||
    formatOrganizerSignal(message.category) === "promo_reminder" ||
    formatOrganizerSignal(message.ui_signal) === "promo_reminder"
  );
}

function buildDisabledMenuAction(
  action: Omit<BundleOrganizerContextMenuAction, "disabled" | "disabledReason">,
): BundleOrganizerContextMenuAction {
  return {
    ...action,
    disabled: true,
    disabledReason: bundleModeDisabledReason,
  };
}

function estimateContextMenuHeight(actionCount: number) {
  return contextMenuVerticalChrome + actionCount * contextMenuEstimatedActionHeight;
}

function resolveContextMenuPosition(
  anchorX: number,
  anchorY: number,
  menuHeight: number,
  menuWidth = contextMenuWidth,
) {
  if (typeof window === "undefined") {
    return { x: anchorX, y: anchorY };
  }

  const maxX = Math.max(
    contextMenuViewportPadding,
    window.innerWidth - menuWidth - contextMenuViewportPadding,
  );
  const x = Math.min(
    Math.max(contextMenuViewportPadding, anchorX),
    maxX,
  );
  const availableMenuHeight = Math.min(
    menuHeight,
    Math.max(0, window.innerHeight - contextMenuViewportPadding * 2),
  );
  const maxY = Math.max(
    contextMenuViewportPadding,
    window.innerHeight - availableMenuHeight - contextMenuViewportPadding,
  );
  const opensPastBottom =
    anchorY + menuHeight > window.innerHeight - contextMenuViewportPadding;
  const preferredY = opensPastBottom
    ? anchorY - menuHeight - contextMenuGap * 2
    : anchorY;
  const y = Math.min(
    Math.max(contextMenuViewportPadding, preferredY),
    maxY,
  );

  return { x, y };
}

function getContextMenuActions(
  message: BundleOrganizerMessage,
  sourceView: BundleOrganizerView,
  onMoveToCategory: (
    message: BundleOrganizerMessage,
    manualCategory: "demo" | "promo",
  ) => void,
  onSetReadState: (
    message: BundleOrganizerMessage,
    readState: BundleOrganizerReadState,
  ) => void,
  onToggleShortlist: (message: BundleOrganizerMessage) => void,
  onTogglePriority: (message: BundleOrganizerMessage) => void,
  onToggleTrash: (message: BundleOrganizerMessage) => void,
): BundleOrganizerContextMenuAction[] {
  const actions: BundleOrganizerContextMenuAction[] = [
    buildDisabledMenuAction({
      icon: "forward",
      label: "Forward",
    }),
    {
      icon: message.unread ? "mailOpen" : "mail",
      label: message.unread ? "Mark as read" : "Mark as unread",
      onSelect: () =>
        onSetReadState(message, message.unread ? "read" : "unread"),
    },
  ];

  if (sourceView === "trash" || message.trashed === true) {
    actions.push(
      {
        icon: "restore",
        label: "Restore",
        onSelect: () => onToggleTrash(message),
      },
      buildDisabledMenuAction({
        icon: "trash",
        label: "Delete permanently",
      }),
    );

    return actions;
  }

  const resolvedCategory = resolveOrganizerCategory(message);

  if (resolvedCategory !== "demo") {
    actions.push(
      {
        icon: "category",
        label: "Move to Demo",
        onSelect: () => onMoveToCategory(message, "demo"),
      },
    );
  }

  if (resolvedCategory !== "promo") {
    actions.push(
      {
        icon: "category",
        label: "Move to Promo",
        onSelect: () => onMoveToCategory(message, "promo"),
      },
    );
  }

  actions.push(
    {
      icon: message.shortlisted === true ? "shortlistOff" : "shortlist",
      label:
        message.shortlisted === true ? "Remove from Shortlist" : "Shortlist",
      onSelect: () => onToggleShortlist(message),
    },
    {
      icon: message.manualPriority === true ? "priorityOff" : "priority",
      label: message.manualPriority === true ? "Remove Priority" : "Mark as Priority",
      onSelect: () => onTogglePriority(message),
    },
  );

  if (sourceView === "promo" || (sourceView === "shortlist" && shouldShowInPromoInbox(message))) {
    if (isPromoReminderMessage(message)) {
      actions.push(
        buildDisabledMenuAction({
          icon: "rule",
          label: "Hide future reminders from this sender",
        }),
      );
    }
    actions.push(
      buildDisabledMenuAction({
        icon: "rule",
        label: "Hide future promos from this sender",
      }),
    );
  }

  actions.push(
    buildDisabledMenuAction({
      icon: "rule",
      label: "Always prioritize this sender",
    }),
    buildDisabledMenuAction({
      icon: "smartView",
      label: "Create Smart View for this sender",
    }),
    {
      icon: "trash",
      label: "Move to Trash",
      onSelect: () => onToggleTrash(message),
    },
  );

  return actions;
}

function MessagePills({ message }: { message: BundleOrganizerMessage }) {
  const resolvedCategory = resolveOrganizerCategory(message);

  return (
    <>
      {message.sourceMailbox ? (
        <span className="max-w-full truncate rounded-full bg-[rgba(120,104,89,0.1)] px-2 py-0.5 text-[0.68rem] font-medium text-[rgba(64,56,48,0.62)] dark:bg-white/5 dark:text-[rgba(245,239,229,0.56)]">
          {message.sourceMailbox}
        </span>
      ) : null}
      {message.priorityBadge ? (
        <span className="rounded-full bg-[rgba(48,72,61,0.1)] px-2 py-0.5 text-[0.68rem] font-medium text-[rgba(48,72,61,0.86)] dark:bg-[rgba(143,179,159,0.14)] dark:text-[rgba(167,203,181,0.9)]">
          {message.priorityBadge}
        </span>
      ) : null}
      {message.manualPriority === true ? (
        <span className={manualPillClass}>Manual Priority</span>
      ) : null}
      {message.shortlisted ? (
        <span className={shortlistedPillClass}>Shortlisted</span>
      ) : null}
      {message.trashed ? (
        <span className={trashedPillClass}>Trashed</span>
      ) : null}
      {resolvedCategory ? (
        <span className="rounded-full bg-[rgba(120,104,89,0.1)] px-2 py-0.5 text-[0.68rem] font-medium text-[rgba(64,56,48,0.62)] dark:bg-white/5 dark:text-[rgba(245,239,229,0.56)]">
          {resolvedCategory.replace(/_/g, " ")}
        </span>
      ) : null}
    </>
  );
}

function DetailMetadata({
  label,
  value,
}: {
  label: string;
  value?: string | null;
}) {
  const normalizedValue = value?.trim();

  if (!normalizedValue) {
    return null;
  }

  return (
    <div className="min-w-0">
      <dt className="text-[0.62rem] font-semibold uppercase tracking-[0.14em] text-[rgba(217,203,184,0.42)]">
        {label}
      </dt>
      <dd className="mt-1 truncate text-[0.78rem] font-medium text-[rgba(245,239,229,0.68)]">
        {normalizedValue}
      </dd>
    </div>
  );
}

function BundleOrganizerEmailBody({
  body,
  bodyHtml,
  className = "",
}: {
  body: string[];
  bodyHtml?: string;
  className?: string;
}) {
  const sanitizedEmail = useMemo(() => sanitizeEmailHtml(bodyHtml), [bodyHtml]);

  if (sanitizedEmail.html) {
    return (
      <div className={`space-y-3 ${className}`}>
        {sanitizedEmail.blockedImageCount > 0 ? (
          <div className="rounded-[14px] border border-[rgba(143,179,159,0.16)] bg-[rgba(143,179,159,0.08)] px-3 py-2.5 text-[0.78rem] leading-5 text-[rgba(167,203,181,0.82)]">
            Images are hidden for privacy.
          </div>
        ) : null}
        <div
          className="bundle-organizer-email-html max-w-full overflow-x-auto rounded-[16px] border border-[rgba(120,104,89,0.12)] bg-[color:#fffaf2] p-4 text-[0.94rem] leading-7 text-[color:#302a24] shadow-[inset_0_1px_0_rgba(255,255,255,0.7)] [overflow-wrap:anywhere] sm:p-5 [&_*]:max-w-full [&_a]:font-semibold [&_a]:text-[color:#245b43] [&_a]:underline [&_a]:decoration-[rgba(36,91,67,0.26)] [&_a]:underline-offset-4 [&_blockquote]:my-4 [&_blockquote]:rounded-[14px] [&_blockquote]:border-l-4 [&_blockquote]:border-[rgba(120,104,89,0.24)] [&_blockquote]:bg-[rgba(120,104,89,0.08)] [&_blockquote]:px-4 [&_blockquote]:py-3 [&_h1]:mb-3 [&_h1]:text-[1.45rem] [&_h1]:font-semibold [&_h1]:leading-tight [&_h2]:mb-3 [&_h2]:text-[1.25rem] [&_h2]:font-semibold [&_h2]:leading-tight [&_h3]:mb-2 [&_h3]:text-[1.12rem] [&_h3]:font-semibold [&_h4]:mb-2 [&_h4]:font-semibold [&_hr]:my-5 [&_hr]:border-[rgba(120,104,89,0.18)] [&_li]:my-1 [&_ol]:my-3 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-3 [&_table]:my-4 [&_table]:w-auto [&_table]:max-w-full [&_table]:border-collapse [&_td]:align-top [&_td]:leading-6 [&_td]:[overflow-wrap:anywhere] [&_th]:align-top [&_th]:font-semibold [&_ul]:my-3 [&_ul]:list-disc [&_ul]:pl-5 [&_[data-bundle-organizer-email-image-placeholder='true']]:my-3 [&_[data-bundle-organizer-email-image-placeholder='true']]:rounded-[12px] [&_[data-bundle-organizer-email-image-placeholder='true']]:border [&_[data-bundle-organizer-email-image-placeholder='true']]:border-[rgba(120,104,89,0.16)] [&_[data-bundle-organizer-email-image-placeholder='true']]:bg-[rgba(120,104,89,0.07)] [&_[data-bundle-organizer-email-image-placeholder='true']]:px-3 [&_[data-bundle-organizer-email-image-placeholder='true']]:py-2 [&_[data-bundle-organizer-email-image-placeholder='true']]:text-[0.78rem] [&_[data-bundle-organizer-email-image-placeholder='true']]:font-medium [&_[data-bundle-organizer-email-image-placeholder='true']]:text-[rgba(64,56,48,0.58)]"
          dangerouslySetInnerHTML={{ __html: sanitizedEmail.html }}
        />
      </div>
    );
  }

  return (
    <div className={`space-y-5 rounded-[16px] border border-[rgba(120,104,89,0.12)] bg-[color:#fffaf2] p-4 text-[0.94rem] leading-7 text-[color:#302a24] shadow-[inset_0_1px_0_rgba(255,255,255,0.7)] sm:p-5 ${className}`}>
      {normalizePlainBody(body).flatMap((paragraph, paragraphIndex) =>
        splitParagraphIntoSegments(paragraph).map((segment, segmentIndex) =>
          segment.kind === "quote" ? (
            <blockquote
              key={`${paragraphIndex}-${segmentIndex}-quote`}
              className="break-words rounded-[14px] border-l-4 border-[rgba(120,104,89,0.24)] bg-[rgba(120,104,89,0.08)] px-4 py-3 text-[0.92rem] leading-7 text-[rgba(64,56,48,0.6)] [overflow-wrap:anywhere]"
            >
              {renderBodyLines(
                segment.lines,
                `${paragraphIndex}-${segmentIndex}-quote`,
              )}
            </blockquote>
          ) : (
            <p
              key={`${paragraphIndex}-${segmentIndex}-message`}
              className="break-words [overflow-wrap:anywhere]"
            >
              {renderBodyLines(
                segment.lines,
                `${paragraphIndex}-${segmentIndex}-message`,
              )}
            </p>
          ),
        ),
      )}
    </div>
  );
}

function FilterSelect({
  ariaLabel,
  children,
  onChange,
  value,
}: {
  ariaLabel: string;
  children: ReactNode;
  onChange: (value: string) => void;
  value: string;
}) {
  return (
    <label className="relative inline-flex">
      <span className="sr-only">{ariaLabel}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-9 max-w-[220px] appearance-none rounded-full border border-white/10 bg-white/5 px-3.5 pr-10 text-[0.74rem] font-medium uppercase tracking-[0.1em] text-[rgba(245,239,229,0.66)] outline-none transition-colors hover:border-[rgba(143,179,159,0.24)] hover:bg-[rgba(143,179,159,0.1)] hover:text-[rgba(198,228,209,0.84)] focus:border-[rgba(143,179,159,0.34)]"
      >
        {children}
      </select>
      <svg
        aria-hidden="true"
        className="pointer-events-none absolute right-3.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[rgba(245,239,229,0.52)]"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
        viewBox="0 0 24 24"
      >
        <path d="m6 9 6 6 6-6" />
      </svg>
    </label>
  );
}

function BundleOrganizerDropboxLinkPreview({
  links,
  className = "",
}: {
  links: DropboxPreviewLink[];
  className?: string;
}) {
  const visibleLinks = links.slice(0, maxEmbeddedLinkPreviews);
  const remainingLinkCount = Math.max(links.length - visibleLinks.length, 0);

  if (visibleLinks.length === 0) {
    return null;
  }

  return (
    <section className={className} aria-label="Dropbox links">
      <div className="space-y-3 rounded-[18px] border border-[rgba(48,72,61,0.16)] bg-[rgba(255,252,247,0.62)] p-3.5 shadow-[0_14px_34px_rgba(61,44,32,0.07)] dark:border-[rgba(143,179,159,0.18)] dark:bg-[rgba(143,179,159,0.08)] dark:shadow-[0_14px_34px_rgba(0,0,0,0.16)]">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-[0.72rem] font-semibold uppercase tracking-[0.14em] text-[rgba(48,72,61,0.76)] dark:text-[rgba(167,203,181,0.88)]">
              {visibleLinks.length === 1 ? "Dropbox link" : "Dropbox links"}
            </p>
            <p className="mt-1 text-[0.78rem] leading-5 text-[rgba(64,56,48,0.54)] dark:text-[rgba(245,239,229,0.5)]">
              Open the shared files in Dropbox.
            </p>
          </div>
        </div>
        <div className="grid gap-3">
          {visibleLinks.map((link) => (
            <div
              key={link.href}
              className="overflow-hidden rounded-[14px] border border-[rgba(120,104,89,0.12)] bg-[color:#fffaf2] dark:border-white/10 dark:bg-[rgba(11,18,15,0.28)]"
            >
              <div className="flex min-w-0 flex-col gap-2 px-3 py-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <p className="text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-[rgba(48,72,61,0.58)] dark:text-[rgba(167,203,181,0.66)]">
                    {link.typeLabel}
                  </p>
                  <p
                    className="mt-1 truncate text-[0.86rem] font-semibold text-[rgba(64,56,48,0.8)] dark:text-[rgba(245,239,229,0.78)]"
                    title={link.label}
                  >
                    {link.label}
                  </p>
                  <p
                    className="mt-1 truncate text-[0.72rem] leading-4 text-[rgba(64,56,48,0.46)] dark:text-[rgba(245,239,229,0.44)]"
                    title={link.urlLabel}
                  >
                    {link.urlLabel}
                  </p>
                </div>
                {!link.isAudio ? (
                  <a
                    href={link.href}
                    target="_blank"
                    rel="noopener noreferrer nofollow"
                    className="inline-flex h-8 w-fit shrink-0 items-center justify-center rounded-full border border-[rgba(48,72,61,0.16)] bg-[rgba(48,72,61,0.06)] px-3 text-[0.7rem] font-semibold text-[rgba(48,72,61,0.8)] transition-colors hover:border-[rgba(48,72,61,0.26)] hover:bg-[rgba(48,72,61,0.1)] dark:border-[rgba(143,179,159,0.2)] dark:bg-[rgba(143,179,159,0.08)] dark:text-[rgba(167,203,181,0.88)] dark:hover:border-[rgba(143,179,159,0.3)] dark:hover:bg-[rgba(143,179,159,0.12)]"
                  >
                    Open in Dropbox
                  </a>
                ) : null}
              </div>
              {link.isAudio && link.audioSrc ? (
                <>
                  <div className="px-3 pb-3">
                    <audio
                      controls
                      preload="none"
                      src={link.audioSrc}
                      className="h-10 w-full rounded-[10px]"
                    >
                      <a href={link.href}>Open audio in Dropbox</a>
                    </audio>
                  </div>
                  <div className="flex flex-col gap-1.5 border-t border-[rgba(120,104,89,0.1)] px-3 py-2.5 text-[0.72rem] leading-5 dark:border-white/10 sm:flex-row sm:items-center sm:justify-between">
                    <span className="font-medium text-[rgba(64,56,48,0.48)] dark:text-[rgba(245,239,229,0.46)]">
                      Can&apos;t play inline?
                    </span>
                    <a
                      href={link.href}
                      target="_blank"
                      rel="noopener noreferrer nofollow"
                      className="w-fit font-semibold text-[rgba(48,72,61,0.8)] underline decoration-[rgba(48,72,61,0.24)] underline-offset-4 transition-colors hover:text-[rgba(35,58,47,0.94)] dark:text-[rgba(167,203,181,0.88)] dark:decoration-[rgba(167,203,181,0.28)] dark:hover:text-[rgba(198,228,209,0.98)]"
                    >
                      Open in Dropbox
                    </a>
                  </div>
                </>
              ) : null}
            </div>
          ))}
        </div>
        {remainingLinkCount > 0 ? (
          <p className="text-[0.72rem] leading-5 text-[rgba(64,56,48,0.46)] dark:text-[rgba(245,239,229,0.44)]">
            +{remainingLinkCount} more Dropbox{" "}
            {remainingLinkCount === 1 ? "link" : "links"} in email body
          </p>
        ) : null}
      </div>
    </section>
  );
}

function BundleOrganizerSoundCloudPreview({
  candidates,
  className = "",
}: {
  candidates: SoundCloudPreviewCandidate[];
  className?: string;
}) {
  const candidateSignature = useMemo(
    () => candidates.map((candidate) => candidate.href).join("|"),
    [candidates],
  );
  const [resolutionState, setResolutionState] = useState<SoundCloudResolutionState>({
    loading: false,
    resolvedPreviews: {},
    signature: "",
  });
  const activeResolvedPreviews =
    resolutionState.signature === candidateSignature
      ? resolutionState.resolvedPreviews
      : {};
  const previews = useMemo(
    () => buildSoundCloudPreviewLinks(candidates, activeResolvedPreviews),
    [candidates, activeResolvedPreviews],
  );
  const candidateHrefs = useMemo(
    () =>
      candidates
        .map((candidate) => candidate.href)
        .filter((href, index, hrefs) => hrefs.indexOf(href) === index),
    [candidates],
  );

  useEffect(() => {
    if (candidateHrefs.length === 0) {
      setResolutionState({
        loading: false,
        resolvedPreviews: {},
        signature: candidateSignature,
      });
      return;
    }

    let isCancelled = false;
    setResolutionState((currentState) => ({
      loading: true,
      resolvedPreviews:
        currentState.signature === candidateSignature
          ? currentState.resolvedPreviews
          : {},
      signature: candidateSignature,
    }));

    Promise.all(
      candidateHrefs.map(async (href) => {
        const result = await resolveSoundCloudCandidate(href);
        const isPlaylist =
          result.ok &&
          isPlaylistPreview({
            canonicalUrl: result.canonicalUrl,
            height: result.height,
            href,
            iframeSrc: result.iframeSrc,
          });
        const preview =
          result.ok && result.canonicalUrl && isSafeSoundCloudIframeSrc(result.iframeSrc)
            ? {
                canonicalUrl: result.canonicalUrl,
                height: clampSoundCloudHeight(result.height, isPlaylist),
                href,
                iframeSrc: result.iframeSrc ?? "",
                title: result.title ?? undefined,
              }
            : null;
        return [href, preview] as const;
      }),
    ).then((resolvedEntries) => {
      if (isCancelled) {
        return;
      }

      setResolutionState({
        loading: false,
        resolvedPreviews: Object.fromEntries(resolvedEntries),
        signature: candidateSignature,
      });
    });

    return () => {
      isCancelled = true;
    };
  }, [candidateHrefs, candidateSignature]);

  const isResolving =
    resolutionState.signature === candidateSignature && resolutionState.loading;
  const unresolvedHrefs = useMemo(
    () =>
      resolutionState.signature === candidateSignature && !isResolving
        ? candidateHrefs.filter((href) =>
            Object.prototype.hasOwnProperty.call(activeResolvedPreviews, href) &&
            activeResolvedPreviews[href] === null,
          )
        : [],
    [
      activeResolvedPreviews,
      candidateHrefs,
      candidateSignature,
      isResolving,
      resolutionState.signature,
    ],
  );

  if (candidates.length === 0) {
    return null;
  }

  if (previews.length === 0 && unresolvedHrefs.length === 0 && !isResolving) {
    return null;
  }

  return (
    <section className={className} aria-label="SoundCloud preview">
      <div className="space-y-3 rounded-[18px] border border-[rgba(48,72,61,0.16)] bg-[rgba(255,252,247,0.62)] p-3.5 shadow-[0_14px_34px_rgba(61,44,32,0.07)] dark:border-[rgba(143,179,159,0.18)] dark:bg-[rgba(143,179,159,0.08)] dark:shadow-[0_14px_34px_rgba(0,0,0,0.16)]">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-[0.72rem] font-semibold uppercase tracking-[0.14em] text-[rgba(48,72,61,0.76)] dark:text-[rgba(167,203,181,0.88)]">
            SoundCloud preview
          </p>
        </div>
        {isResolving && previews.length === 0 ? (
          <p className="rounded-[14px] border border-[rgba(120,104,89,0.12)] bg-[rgba(255,252,247,0.42)] px-3 py-2 text-[0.78rem] leading-5 text-[rgba(64,56,48,0.58)] dark:border-white/10 dark:bg-white/[0.035] dark:text-[rgba(245,239,229,0.54)]">
            Resolving SoundCloud preview...
          </p>
        ) : null}
        {previews.length > 0 ? (
          <div className="grid gap-3">
            {previews.map((preview) => (
              <div
                key={preview.canonicalUrl}
                className="overflow-hidden rounded-[14px] border border-[rgba(120,104,89,0.12)] bg-[color:#fffaf2] dark:border-white/10 dark:bg-[rgba(11,18,15,0.28)]"
              >
                <iframe
                  title={preview.title || "SoundCloud audio player"}
                  src={preview.iframeSrc}
                  allow="autoplay"
                  height={preview.height}
                  width="100%"
                  loading="lazy"
                  referrerPolicy="no-referrer"
                  className="block w-full border-0"
                />
                <div className="flex flex-col gap-1.5 border-t border-[rgba(120,104,89,0.1)] px-3 py-2.5 text-[0.72rem] leading-5 dark:border-white/10 sm:flex-row sm:items-center sm:justify-between">
                  <span className="font-medium text-[rgba(64,56,48,0.48)] dark:text-[rgba(245,239,229,0.46)]">
                    Playback unavailable?
                  </span>
                  <a
                    href={preview.href}
                    target="_blank"
                    rel="noopener noreferrer nofollow"
                    className="w-fit font-semibold text-[rgba(48,72,61,0.8)] underline decoration-[rgba(48,72,61,0.24)] underline-offset-4 transition-colors hover:text-[rgba(35,58,47,0.94)] dark:text-[rgba(167,203,181,0.88)] dark:decoration-[rgba(167,203,181,0.28)] dark:hover:text-[rgba(198,228,209,0.98)]"
                  >
                    Open in SoundCloud
                  </a>
                </div>
              </div>
            ))}
          </div>
        ) : null}
        {unresolvedHrefs.length > 0 ? (
          <div className="grid gap-3">
            {unresolvedHrefs.map((href) => (
              <div
                key={href}
                className="rounded-[14px] border border-[rgba(120,104,89,0.12)] bg-[rgba(255,252,247,0.44)] px-3 py-3 dark:border-white/10 dark:bg-white/[0.035]"
              >
                <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0">
                    <p className="text-[0.78rem] font-semibold text-[rgba(64,56,48,0.74)] dark:text-[rgba(245,239,229,0.74)]">
                      SoundCloud preview unavailable
                    </p>
                    <p className="mt-1 text-[0.74rem] leading-5 text-[rgba(64,56,48,0.54)] dark:text-[rgba(245,239,229,0.5)]">
                      SoundCloud could not create an embedded preview for this
                      link. The track may still open and play on SoundCloud.
                    </p>
                  </div>
                  <a
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex h-8 w-fit shrink-0 items-center justify-center rounded-full border border-[rgba(48,72,61,0.16)] bg-[rgba(48,72,61,0.06)] px-3 text-[0.7rem] font-semibold text-[rgba(48,72,61,0.78)] transition-colors hover:border-[rgba(48,72,61,0.26)] hover:bg-[rgba(48,72,61,0.1)] dark:border-[rgba(143,179,159,0.2)] dark:bg-[rgba(143,179,159,0.08)] dark:text-[rgba(167,203,181,0.84)] dark:hover:border-[rgba(143,179,159,0.3)] dark:hover:bg-[rgba(143,179,159,0.12)]"
                  >
                    Open in SoundCloud
                  </a>
                </div>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </section>
  );
}

function DetailFooterActionButton({
  active = false,
  children,
  disabled = false,
  icon,
  onClick,
  title,
}: {
  active?: boolean;
  disabled?: boolean;
  children: ReactNode;
  icon: BundleOrganizerContextMenuIconName;
  onClick?: () => void;
  title?: string;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      title={title}
      className={`inline-flex h-10 items-center justify-center gap-2 rounded-full border px-4 text-[0.74rem] font-medium uppercase tracking-[0.12em] transition-colors disabled:cursor-not-allowed disabled:opacity-55 ${
        active
          ? "border-[rgba(48,72,61,0.22)] bg-[rgba(48,72,61,0.08)] text-[rgba(48,72,61,0.86)] hover:bg-[rgba(48,72,61,0.12)] dark:border-[rgba(143,179,159,0.28)] dark:bg-[rgba(143,179,159,0.12)] dark:text-[rgba(198,228,209,0.92)] dark:hover:bg-[rgba(143,179,159,0.16)]"
          : "border-[rgba(120,104,89,0.16)] bg-[rgba(246,239,231,0.62)] text-[rgba(64,56,48,0.72)] hover:bg-[rgba(238,227,215,0.78)] dark:border-white/10 dark:bg-white/5 dark:text-[rgba(245,239,229,0.7)] dark:hover:bg-white/10"
      }`}
    >
      <ContextMenuIcon name={icon} />
      <span>{children}</span>
    </button>
  );
}

function MessageDetail({
  message,
  onBack,
  onSetReadState,
  onToggleShortlist,
  onToggleTrash,
}: {
  message: BundleOrganizerMessage;
  onBack: () => void;
  onSetReadState: (
    message: BundleOrganizerMessage,
    readState: BundleOrganizerReadState,
  ) => void;
  onToggleShortlist: (message: BundleOrganizerMessage) => void;
  onToggleTrash: (message: BundleOrganizerMessage) => void;
}) {
  const activeReason = resolvePriorityReason(message);
  const bodyLines = useMemo(
    () => (message.body.length > 0 ? message.body : [message.snippet]),
    [message.body, message.snippet],
  );
  const soundCloudLinks = useMemo(
    () =>
      collectPreviewItems(
        bodyLines,
        message.bodyHtml,
        message.snippet,
        soundCloudLinkPattern,
        buildSoundCloudPreviewCandidate,
        (candidate) => candidate.href,
      ),
    [bodyLines, message.bodyHtml, message.snippet],
  );
  const dropboxLinks = useMemo(
    () =>
      collectPreviewItems(
        bodyLines,
        message.bodyHtml,
        message.snippet,
        dropboxLinkPattern,
        buildDropboxPreviewLink,
        (link) => link.href,
      ),
    [bodyLines, message.bodyHtml, message.snippet],
  );

  return (
    <div>
      <button
        type="button"
        onClick={onBack}
        className="mt-4 inline-flex h-9 items-center justify-center rounded-full border border-[rgba(143,179,159,0.34)] bg-[rgba(143,179,159,0.12)] px-3.5 text-[0.72rem] font-semibold uppercase tracking-[0.12em] text-[rgba(198,228,209,0.96)] shadow-[0_8px_18px_rgba(0,0,0,0.18)] transition-[background-color,border-color,color,box-shadow,transform] duration-150 hover:border-[rgba(143,179,159,0.48)] hover:bg-[rgba(143,179,159,0.18)] hover:text-[rgba(226,246,233,0.98)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(143,179,159,0.3)] active:scale-[0.99]"
      >
        &larr; Back to list
      </button>

      <article className="mt-5 rounded-[20px] border border-white/10 bg-white/5 p-6 shadow-[0_24px_70px_rgba(0,0,0,0.22)] sm:p-7">
        <div className="flex flex-col gap-5 border-b border-white/10 pb-6 md:flex-row md:items-start md:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[0.92rem] font-semibold tracking-[-0.01em] text-[color:#f5efe5]">
                {message.sender}
              </span>
              {message.sourceMailbox ? (
                <span className="rounded-full bg-white/5 px-2 py-0.5 text-[0.68rem] font-medium text-[rgba(245,239,229,0.56)]">
                  {message.sourceMailbox}
                </span>
              ) : null}
            </div>
            {message.from ? (
              <div className="mt-2 grid max-w-[780px] gap-1 text-[0.76rem] leading-5 text-[rgba(245,239,229,0.5)]">
                <div className="flex min-w-0 gap-1.5">
                  <span className="shrink-0 font-medium text-[rgba(245,239,229,0.42)]">
                    From:
                  </span>
                  <span className="min-w-0 truncate" title={message.from}>
                    {message.from}
                  </span>
                </div>
              </div>
            ) : null}
            <h3 className="mt-4 max-w-[780px] text-[1.65rem] font-semibold leading-tight tracking-[-0.035em] text-[color:#f5efe5] sm:text-[2rem]">
              {message.subject}
            </h3>
            <div className="mt-4 flex flex-wrap gap-2">
              <MessagePills message={message} />
            </div>
            {activeReason ? (
              <p className="mt-3 w-fit rounded-[14px] border border-[rgba(143,179,159,0.18)] bg-[rgba(143,179,159,0.08)] px-3 py-2 text-[0.78rem] font-medium text-[rgba(167,203,181,0.84)]">
                Prioritized: {activeReason}
              </p>
            ) : null}
          </div>
          <div className="shrink-0 text-[0.78rem] font-medium text-[rgba(245,239,229,0.48)]">
            {message.timestamp}
          </div>
        </div>

        <dl className="mt-5 grid max-w-[780px] gap-3 rounded-[16px] border border-white/10 bg-white/[0.035] p-3 sm:grid-cols-2 lg:grid-cols-4">
          <DetailMetadata label="From" value={message.sender} />
          <DetailMetadata label="Email" value={message.from} />
          <DetailMetadata label="Mailbox" value={message.sourceMailbox} />
          <DetailMetadata label="Date" value={message.timestamp} />
        </dl>

        <p className="mt-5 max-w-[780px] text-[0.9rem] leading-6 text-[rgba(245,239,229,0.68)]">
          {message.snippet}
        </p>
        <BundleOrganizerEmailBody
          body={bodyLines}
          bodyHtml={message.bodyHtml}
          className="max-w-[780px] py-7"
        />

        <BundleOrganizerSoundCloudPreview
          candidates={soundCloudLinks}
          className="mb-6 max-w-[780px]"
        />

        <BundleOrganizerDropboxLinkPreview
          links={dropboxLinks}
          className="mb-6 max-w-[780px]"
        />

        <div className="flex flex-wrap gap-3 border-t border-white/10 pt-6">
          <DetailFooterActionButton
            disabled
            icon="reply"
            title={bundleModeDisabledReason}
          >
            Reply
          </DetailFooterActionButton>
          <DetailFooterActionButton
            disabled
            icon="forward"
            title={bundleModeDisabledReason}
          >
            Forward
          </DetailFooterActionButton>
          <DetailFooterActionButton
            icon={message.unread ? "mailOpen" : "mail"}
            onClick={() =>
              onSetReadState(message, message.unread ? "read" : "unread")
            }
          >
            {message.unread ? "Mark as read" : "Mark as unread"}
          </DetailFooterActionButton>
          <DetailFooterActionButton
            disabled
            icon="interest"
            title={bundleModeDisabledReason}
          >
            Interested
          </DetailFooterActionButton>
          <DetailFooterActionButton
            active={message.shortlisted === true}
            icon={message.shortlisted ? "shortlistOff" : "shortlist"}
            onClick={() => onToggleShortlist(message)}
          >
            {message.shortlisted ? "Remove from shortlist" : "Shortlist"}
          </DetailFooterActionButton>
          <DetailFooterActionButton
            active={message.trashed === true}
            icon={message.trashed ? "restore" : "trash"}
            onClick={() => onToggleTrash(message)}
          >
            {message.trashed ? "Restore" : "Move to Trash"}
          </DetailFooterActionButton>
          <DetailFooterActionButton
            disabled
            icon="decline"
            title={bundleModeDisabledReason}
          >
            Decline
          </DetailFooterActionButton>
        </div>
      </article>
    </div>
  );
}

export function BundleOrganizerSurface({
  liveMessages = [],
  hasLiveWorkspaceData = false,
  connectedInboxCount = 0,
  showLocalPriorityNav = true,
}: BundleOrganizerSurfaceProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [activeView, setActiveView] = useState<BundleOrganizerView>(
    showLocalPriorityNav ? "priority" : "demo",
  );
  const [selectedMessageIdentityKey, setSelectedMessageIdentityKey] =
    useState<string | null>(null);
  const [contextMenu, setContextMenu] =
    useState<BundleOrganizerContextMenuState | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [demoSourceFilterId, setDemoSourceFilterId] = useState(allSourceFilterId);
  const [promoSourceFilterId, setPromoSourceFilterId] = useState(allSourceFilterId);
  const [demoStatusFilter, setDemoStatusFilter] =
    useState<BundleOrganizerDemoStatusFilter>("all");
  const [promoFilter, setPromoFilter] =
    useState<BundleOrganizerPromoFilter>("new");
  const [promoStatusFilter, setPromoStatusFilter] =
    useState<BundleOrganizerPromoStatusFilter>("all");
  const [demoSort, setDemoSort] = useState<BundleOrganizerDateSort>("newest");
  const [promoSort, setPromoSort] = useState<BundleOrganizerDateSort>("newest");
  const [workflowState, setWorkflowState] =
    useState<BundleOrganizerWorkflowState>(readBundleOrganizerWorkflowState);
  const baseWorkspaceMessages = useMemo(
    () => normalizeWorkspaceMessages(liveMessages),
    [liveMessages],
  );
  const workspaceMessages = useMemo(
    () => applyBundleWorkflowState(baseWorkspaceMessages, workflowState),
    [baseWorkspaceMessages, workflowState],
  );
  const visibleNavItems = useMemo(
    () =>
      showLocalPriorityNav
        ? navItems
        : navItems.filter((item) => item.id !== "priority"),
    [showLocalPriorityNav],
  );
  useEffect(() => {
    const handleWorkflowStateChanged = () => {
      setWorkflowState(readBundleOrganizerWorkflowState());
    };

    window.addEventListener(
      BUNDLE_ORGANIZER_WORKFLOW_STATE_CHANGED_EVENT,
      handleWorkflowStateChanged,
    );

    return () => {
      window.removeEventListener(
        BUNDLE_ORGANIZER_WORKFLOW_STATE_CHANGED_EVENT,
        handleWorkflowStateChanged,
      );
    };
  }, []);
  useEffect(() => {
    if (showLocalPriorityNav || activeView !== "priority") {
      return;
    }

    const fallbackView =
      visibleNavItems.find((item) => item.id === "demo")?.id ??
      visibleNavItems[0]?.id ??
      "demo";

    setActiveView(fallbackView);
    setSelectedMessageIdentityKey(null);
    setContextMenu(null);
  }, [activeView, showLocalPriorityNav, visibleNavItems]);
  const selectedMessage = useMemo(
    () =>
      selectedMessageIdentityKey
        ? workspaceMessages.find(
            (message) =>
              getWorkflowIdentityKey(message) === selectedMessageIdentityKey,
          ) ?? null
        : null,
    [selectedMessageIdentityKey, workspaceMessages],
  );
  const counts = useMemo(() => getCounts(workspaceMessages), [workspaceMessages]);
  const demoAllMessages = useMemo(
    () => getMessagesForView("demo", workspaceMessages),
    [workspaceMessages],
  );
  const promoAllMessages = useMemo(
    () => getMessagesForView("promo", workspaceMessages),
    [workspaceMessages],
  );
  const demoSourceFilterOptions = useMemo(
    () => buildSourceFilterOptions(demoAllMessages),
    [demoAllMessages],
  );
  const promoSourceFilterOptions = useMemo(
    () => buildSourceFilterOptions(promoAllMessages),
    [promoAllMessages],
  );
  const sourceFilteredDemoMessages = useMemo(
    () => filterMessagesBySource(demoAllMessages, demoSourceFilterId),
    [demoAllMessages, demoSourceFilterId],
  );
  const sortedVisibleDemoMessages = useMemo(
    () =>
      sortMessagesByDate(
        groupMessagesByExplicitThread(
          filterMessagesByDemoStatus(sourceFilteredDemoMessages, demoStatusFilter),
        ),
        demoSort,
      ),
    [demoSort, demoStatusFilter, sourceFilteredDemoMessages],
  );
  const sourceFilteredPromoMessages = useMemo(
    () => filterMessagesBySource(promoAllMessages, promoSourceFilterId),
    [promoAllMessages, promoSourceFilterId],
  );
  const promoReminderMessages = useMemo(
    () => sourceFilteredPromoMessages.filter(isPromoReminderMessage),
    [sourceFilteredPromoMessages],
  );
  const promoNewMessages = useMemo(
    () =>
      sourceFilteredPromoMessages.filter(
        (message) => !isPromoReminderMessage(message),
      ),
    [sourceFilteredPromoMessages],
  );
  const visiblePromoMessages = useMemo(
    () =>
      promoFilter === "reminders"
        ? promoReminderMessages
        : promoFilter === "all"
        ? sourceFilteredPromoMessages
        : promoNewMessages,
    [
      promoFilter,
      promoNewMessages,
      promoReminderMessages,
      sourceFilteredPromoMessages,
    ],
  );
  const sortedVisiblePromoMessages = useMemo(
    () =>
      sortMessagesByDate(
        groupMessagesByExplicitThread(
          filterMessagesByPromoStatus(visiblePromoMessages, promoStatusFilter),
        ),
        promoSort,
      ),
    [promoSort, promoStatusFilter, visiblePromoMessages],
  );
  const promoFilterOptions: Array<{
    count: number;
    id: BundleOrganizerPromoFilter;
    label: string;
  }> = [
    { id: "new", label: "Promos", count: promoNewMessages.length },
    { id: "reminders", label: "Reminders", count: promoReminderMessages.length },
    { id: "all", label: "All", count: sourceFilteredPromoMessages.length },
  ];
  const rawActiveMessages = useMemo(
    () =>
      activeView === "demo"
        ? sortedVisibleDemoMessages
        : activeView === "promo"
        ? sortedVisiblePromoMessages
        : getMessagesForView(activeView, workspaceMessages),
    [
      activeView,
      sortedVisibleDemoMessages,
      sortedVisiblePromoMessages,
      workspaceMessages,
    ],
  );
  const globalSearchSourceRows = useMemo(
    () => buildGlobalSearchSourceRows(workspaceMessages, activeView === "trash"),
    [activeView, workspaceMessages],
  );
  const visibleRows = useMemo(
    () =>
      searchQuery.trim()
        ? globalSearchSourceRows.filter(({ message }) =>
            doesMessageMatchSearch(message, searchQuery),
          )
        : rawActiveMessages.map((message) => ({
            message,
            sourceView: activeView,
          })),
    [activeView, globalSearchSourceRows, rawActiveMessages, searchQuery],
  );
  const isSearchActive = searchQuery.trim().length > 0;
  const activeCopy = isSearchActive
      ? {
        title: "Search results",
        eyebrow: "Global Organizer search",
        description:
          activeView === "trash"
            ? "Matching messages in embedded Organizer Trash."
            : "Matching messages across embedded Organizer views.",
        emptyTitle: "No messages match your search.",
        emptyDescription: "Clear the search to return to the current view.",
      }
    : viewCopy[activeView];
  const hasOrganizerData = hasLiveWorkspaceData || workspaceMessages.length > 0;
  const contextMenuMessage = contextMenu
    ? visibleRows.find(
        ({ message, sourceView }) =>
          message.id === contextMenu.messageId &&
          sourceView === contextMenu.sourceView,
      ) ?? null
    : null;
  const contextMenuActions = contextMenuMessage
    ? getContextMenuActions(
        contextMenuMessage.message,
        contextMenuMessage.sourceView,
        moveMessageToCategory,
        setMessageReadState,
        toggleMessageShortlist,
        toggleMessagePriority,
        toggleMessageTrash,
      )
    : [];
  const isViewFilterActive =
    !isSearchActive &&
    ((activeView === "demo" &&
      (demoSourceFilterId !== allSourceFilterId ||
        demoStatusFilter !== "all" ||
        demoSort !== "newest")) ||
      (activeView === "promo" &&
        (promoSourceFilterId !== allSourceFilterId ||
          promoStatusFilter !== "all" ||
          promoFilter !== "new" ||
          promoSort !== "newest")));
  const shouldShowControlBar =
    !selectedMessage &&
    !isSearchActive &&
    (activeView === "demo" || activeView === "promo");

  const selectView = (view: BundleOrganizerView) => {
    setActiveView(view);
    setSelectedMessageIdentityKey(null);
    setContextMenu(null);
  };

  function moveMessageToCategory(
    message: BundleOrganizerMessage,
    manualCategory: "demo" | "promo",
  ) {
    const identityKey = getWorkflowIdentityKey(message);
    const manualCategoryAt = new Date().toISOString();

    setWorkflowState((currentState) => {
      const nextState = {
        ...currentState,
        [identityKey]: {
          ...currentState[identityKey],
          manualCategory,
          manualCategoryAt,
        },
      };

      writeBundleOrganizerWorkflowState(nextState, { notify: true });
      return nextState;
    });
    setContextMenu(null);
  }

  function setMessageReadState(
    message: BundleOrganizerMessage,
    readState: BundleOrganizerReadState,
  ) {
    const identityKey = getWorkflowIdentityKey(message);
    const timestamp = new Date().toISOString();
    const readAt = readState === "read" ? timestamp : undefined;
    const unreadAt = readState === "unread" ? timestamp : undefined;

    setWorkflowState((currentState) => {
      const nextState = {
        ...currentState,
        [identityKey]: {
          ...currentState[identityKey],
          readState,
          readAt,
          unreadAt,
        },
      };

      if (readState === "read") {
        delete nextState[identityKey].unreadAt;
      } else {
        delete nextState[identityKey].readAt;
      }

      writeBundleOrganizerWorkflowState(nextState, { notify: true });
      return nextState;
    });
    setContextMenu(null);
  }

  function openMessage(message: BundleOrganizerMessage) {
    setContextMenu(null);
    const identityKey = getWorkflowIdentityKey(message);

    if (message.unread === true) {
      const timestamp = new Date().toISOString();

      setWorkflowState((currentState) => {
        const nextState = {
          ...currentState,
          [identityKey]: {
            ...currentState[identityKey],
            readState: "read" as const,
            readAt: timestamp,
          },
        };

        delete nextState[identityKey].unreadAt;
        writeBundleOrganizerWorkflowState(nextState, { notify: true });
        return nextState;
      });
      setSelectedMessageIdentityKey(identityKey);
      return;
    }

    setSelectedMessageIdentityKey(identityKey);
  }

  function toggleMessageShortlist(message: BundleOrganizerMessage) {
    const identityKey = getWorkflowIdentityKey(message);
    const nextShortlisted = message.shortlisted !== true;
    const shortlistedAt = nextShortlisted
      ? new Date().toISOString()
      : undefined;

    setWorkflowState((currentState) => {
      const nextState = {
        ...currentState,
        [identityKey]: {
          ...currentState[identityKey],
          shortlisted: nextShortlisted,
          shortlistedAt,
        },
      };

      if (!nextShortlisted) {
        delete nextState[identityKey].shortlistedAt;
      }

      writeBundleOrganizerWorkflowState(nextState, { notify: true });
      return nextState;
    });
    setContextMenu(null);
  }

  function toggleMessagePriority(message: BundleOrganizerMessage) {
    const identityKey = getWorkflowIdentityKey(message);
    const nextManualPriority = message.manualPriority !== true;
    const manualPriorityAt = nextManualPriority
      ? new Date().toISOString()
      : undefined;

    setWorkflowState((currentState) => {
      const nextState = {
        ...currentState,
        [identityKey]: {
          ...currentState[identityKey],
          manualPriority: nextManualPriority,
          manualPriorityAt,
        },
      };

      if (!nextManualPriority) {
        delete nextState[identityKey].manualPriorityAt;
      }

      writeBundleOrganizerWorkflowState(nextState, { notify: true });
      return nextState;
    });
    setContextMenu(null);
  }

  function toggleMessageTrash(message: BundleOrganizerMessage) {
    const identityKey = getWorkflowIdentityKey(message);
    const nextTrashed = message.trashed !== true;
    const trashedAt = nextTrashed ? new Date().toISOString() : undefined;

    setWorkflowState((currentState) => {
      const nextState = {
        ...currentState,
        [identityKey]: {
          ...currentState[identityKey],
          trashed: nextTrashed,
          trashedAt,
        },
      };

      if (!nextTrashed) {
        delete nextState[identityKey].trashedAt;
      }

      writeBundleOrganizerWorkflowState(nextState, { notify: true });
      return nextState;
    });
    setContextMenu(null);
  }

  const openContextMenu = (
    message: BundleOrganizerMessage,
    sourceView: BundleOrganizerView,
    x: number,
    y: number,
  ) => {
    const estimatedActionCount = getContextMenuActions(
      message,
      sourceView,
      moveMessageToCategory,
      setMessageReadState,
      toggleMessageShortlist,
      toggleMessagePriority,
      toggleMessageTrash,
    ).length;
    const position = resolveContextMenuPosition(
      x,
      y,
      estimateContextMenuHeight(estimatedActionCount),
    );

    setContextMenu({
      anchorX: x,
      anchorY: y,
      messageId: message.id,
      sourceView,
      x: position.x,
      y: position.y,
    });
  };

  useEffect(() => {
    setContextMenu(null);
  }, [activeView, isSearchActive]);

  useLayoutEffect(() => {
    if (!contextMenu || !menuRef.current) {
      return;
    }

    const rect = menuRef.current.getBoundingClientRect();
    const position = resolveContextMenuPosition(
      contextMenu.anchorX,
      contextMenu.anchorY,
      rect.height,
      rect.width,
    );

    if (position.x === contextMenu.x && position.y === contextMenu.y) {
      return;
    }

    setContextMenu((currentContextMenu) =>
      currentContextMenu
        ? {
            ...currentContextMenu,
            x: position.x,
            y: position.y,
          }
        : currentContextMenu,
    );
  }, [contextMenu, contextMenuActions.length]);

  useEffect(() => {
    if (
      demoSourceFilterId !== allSourceFilterId &&
      !demoSourceFilterOptions.some((option) => option.id === demoSourceFilterId)
    ) {
      setDemoSourceFilterId(allSourceFilterId);
    }
  }, [demoSourceFilterId, demoSourceFilterOptions]);

  useEffect(() => {
    if (
      promoSourceFilterId !== allSourceFilterId &&
      !promoSourceFilterOptions.some((option) => option.id === promoSourceFilterId)
    ) {
      setPromoSourceFilterId(allSourceFilterId);
    }
  }, [promoSourceFilterId, promoSourceFilterOptions]);

  useEffect(() => {
    if (!contextMenu) {
      return;
    }

    if (!contextMenuMessage) {
      setContextMenu(null);
    }
  }, [contextMenu, contextMenuMessage]);

  useEffect(() => {
    if (!contextMenu) {
      return;
    }

    const closeOnOutsideMouseDown = (event: MouseEvent) => {
      if (
        event.target instanceof Node &&
        menuRef.current?.contains(event.target)
      ) {
        return;
      }
      if (
        event.target instanceof Element &&
        event.target.closest("[data-organizer-context-menu-surface='true']")
      ) {
        return;
      }

      setContextMenu(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setContextMenu(null);
      }
    };

    document.addEventListener("mousedown", closeOnOutsideMouseDown);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutsideMouseDown);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [contextMenu]);

  return (
    <div className="min-h-0 flex-1 overflow-hidden">
      <section className="dark flex h-full min-h-0 flex-col overflow-hidden rounded-[18px] bg-[linear-gradient(180deg,#111a16_0%,#19241f_100%)] px-3 py-3 text-[color:#f5efe5] sm:px-4 lg:px-5">
        <header className="border-b border-white/10 pb-4">
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                <div className="flex items-center gap-2.5">
                  <OrganizerIcon className="h-8 w-8 shrink-0 text-[color:#8fb39f]" />
                  <p className="text-[0.72rem] font-medium uppercase tracking-[0.18em] text-[rgba(217,203,184,0.68)]">
                    Cuevion
                  </p>
                </div>
                <h1 className="mt-2.5 text-[1.7rem] font-semibold tracking-[-0.03em] text-[color:#f5efe5] sm:text-[2rem]">
                  Demo &amp; Promo Organizer
                </h1>
              </div>
            </div>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="max-w-[520px] text-[0.96rem] leading-7 text-[rgba(245,239,229,0.66)]">
                A dedicated music inbox for demos, promos, and active follow-ups.
              </p>
              <div className="relative w-full sm:max-w-[320px]">
                <span className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-[rgba(245,239,229,0.42)]">
                  <svg
                    aria-hidden="true"
                    className="h-4 w-4"
                    fill="none"
                    stroke="currentColor"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth="2"
                    viewBox="0 0 24 24"
                  >
                    <path d="m21 21-4.34-4.34" />
                    <circle cx="11" cy="11" r="8" />
                  </svg>
                </span>
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(event) => {
                    setSearchQuery(event.target.value);
                    setSelectedMessageIdentityKey(null);
                  }}
                  placeholder="Search messages..."
                  className="h-10 w-full rounded-full border border-white/10 bg-white/5 pl-10 pr-10 text-[0.86rem] font-medium text-[rgba(245,239,229,0.84)] outline-none transition-colors placeholder:text-[rgba(245,239,229,0.38)] hover:border-[rgba(143,179,159,0.24)] hover:bg-white/8 focus:border-[rgba(143,179,159,0.34)] focus:bg-white/10 focus:ring-2 focus:ring-[rgba(143,179,159,0.14)]"
                />
                {searchQuery ? (
                  <button
                    type="button"
                    onClick={() => {
                      setSearchQuery("");
                      setSelectedMessageIdentityKey(null);
                    }}
                    className="absolute right-2 top-1/2 inline-flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-full text-[0.78rem] font-semibold leading-none text-[rgba(245,239,229,0.46)] transition-colors hover:bg-[rgba(143,179,159,0.12)] hover:text-[rgba(198,228,209,0.9)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(143,179,159,0.2)]"
                    aria-label="Clear message search"
                  >
                    X
                  </button>
                ) : null}
              </div>
            </div>
          </div>
        </header>

        <div className="mt-3 grid min-h-0 flex-1 gap-4 overflow-hidden lg:grid-cols-[210px_minmax(0,1fr)] xl:grid-cols-[220px_minmax(0,1fr)]">
          <aside className="flex min-w-0 flex-col gap-3 overflow-y-auto pb-2.5 lg:pb-0">
            <section className="w-full rounded-[15px] border border-white/10 bg-white/5 px-3 py-2.5 shadow-[0_10px_24px_rgba(0,0,0,0.16)]">
              <p className="text-[0.66rem] font-medium uppercase tracking-[0.15em] text-[rgba(217,203,184,0.55)]">
                Connected Inboxes
              </p>
              <div className="mt-1.5 flex items-center justify-between gap-2">
                <span className="text-[1.45rem] font-semibold leading-none tracking-[-0.04em] text-[color:#f5efe5]">
                  {connectedInboxCount}
                </span>
                <span className="inline-flex h-7 shrink-0 items-center justify-center rounded-full border border-white/10 bg-white/5 px-2.5 text-[0.64rem] font-medium uppercase tracking-[0.1em] text-[rgba(245,239,229,0.5)]">
                  Live
                </span>
              </div>
              <p className="mt-1 truncate text-[0.68rem] font-medium leading-4 text-[rgba(167,203,181,0.72)]">
                Workspace messages only
              </p>
              <p className="mt-1 text-[0.66rem] leading-4 text-[rgba(245,239,229,0.42)]">
                Organizer counts may differ from mailbox unread counts.
              </p>
            </section>

            <nav
              aria-label="Organizer sections"
              className="flex gap-2 overflow-x-auto border-b border-white/10 pb-3 lg:block lg:overflow-visible lg:border-b-0 lg:border-r lg:bg-transparent lg:pb-0 lg:pr-4 xl:pr-5"
            >
              {visibleNavItems.map((item) => {
                const isActive = item.id === activeView;
                const count = counts[item.id] ?? 0;

                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => selectView(item.id)}
                    className={`flex h-10 shrink-0 items-center justify-between gap-3 rounded-full px-3.5 text-left text-[0.82rem] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(143,179,159,0.28)] lg:mb-2 lg:w-full ${
                      isActive
                        ? "bg-[color:#8fb39f] text-[color:#14201a]"
                        : "text-[rgba(245,239,229,0.72)] hover:bg-white/5"
                    }`}
                    aria-current={isActive ? "page" : undefined}
                  >
                    <span className="flex min-w-0 items-center gap-2.5">
                      <OrganizerNavIcon name={item.icon} />
                      <span className="truncate">{item.label}</span>
                    </span>
                    {item.id !== "trash" && count > 0 ? (
                      <span
                        className={`rounded-full px-2 py-0.5 text-[0.72rem] ${
                          isActive
                            ? "bg-[rgba(20,32,26,0.18)] text-[rgba(20,32,26,0.72)]"
                            : "bg-white/5 text-[rgba(245,239,229,0.52)]"
                        }`}
                      >
                        {count}
                      </span>
                    ) : null}
                  </button>
                );
              })}
            </nav>
          </aside>

          <main className="min-h-0 min-w-0 overflow-y-auto pr-1">
            <section className="rounded-[22px] border border-white/10 bg-[rgba(25,34,30,0.82)] p-4 shadow-[0_18px_48px_rgba(0,0,0,0.26)] sm:p-5 xl:p-6">
              <div className="flex items-center gap-2.5">
                <p className="text-[0.72rem] font-semibold uppercase tracking-[0.16em] text-[rgba(217,203,184,0.58)]">
                  {activeCopy.eyebrow}
                </p>
              </div>
              <div className="mt-1 border-b border-white/10 pb-4">
                <h2 className="text-[1.45rem] font-semibold tracking-[-0.03em] text-[color:#f5efe5]">
                  {activeCopy.title}
                </h2>
                <p className="mt-1.5 max-w-[660px] text-[0.86rem] leading-6 text-[rgba(245,239,229,0.58)]">
                  {activeCopy.description}
                </p>
              </div>

              {shouldShowControlBar ? (
                <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    {activeView === "promo"
                      ? promoFilterOptions.map((option) => {
                          const isSelected = option.id === promoFilter;

                          return (
                            <button
                              key={option.id}
                              type="button"
                              onClick={() => {
                                setPromoFilter(option.id);
                                setSelectedMessageIdentityKey(null);
                                setContextMenu(null);
                              }}
                              className={`inline-flex h-9 items-center justify-center gap-2 rounded-full border px-3.5 text-[0.74rem] font-medium uppercase tracking-[0.1em] transition-colors ${
                                isSelected
                                  ? "border-[rgba(143,179,159,0.34)] bg-[rgba(143,179,159,0.16)] text-[rgba(198,228,209,0.94)]"
                                  : "border-white/10 bg-white/5 text-[rgba(245,239,229,0.62)] hover:border-[rgba(143,179,159,0.24)] hover:bg-[rgba(143,179,159,0.1)] hover:text-[rgba(198,228,209,0.84)]"
                              }`}
                            >
                              <span>{option.label}</span>
                              <span
                                className={`rounded-full px-2 py-0.5 text-[0.68rem] ${
                                  isSelected
                                    ? "bg-[rgba(143,179,159,0.16)] text-[rgba(198,228,209,0.92)]"
                                    : "bg-white/5 text-[rgba(245,239,229,0.5)]"
                                }`}
                              >
                                {option.count}
                              </span>
                            </button>
                          );
                        })
                      : null}
                    <FilterSelect
                      ariaLabel="Filter by source inbox"
                      value={
                        activeView === "demo"
                          ? demoSourceFilterId
                          : promoSourceFilterId
                      }
                      onChange={(value) => {
                        if (activeView === "demo") {
                          setDemoSourceFilterId(value);
                        } else {
                          setPromoSourceFilterId(value);
                        }
                        setSelectedMessageIdentityKey(null);
                        setContextMenu(null);
                      }}
                    >
                      <option value={allSourceFilterId}>All inboxes</option>
                      {(activeView === "demo"
                        ? demoSourceFilterOptions
                        : promoSourceFilterOptions
                      ).map((option) => (
                        <option key={option.id} value={option.id}>
                          {option.label} ({option.count})
                        </option>
                      ))}
                    </FilterSelect>
                  </div>

                  {activeView === "demo" ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <FilterSelect
                        ariaLabel="Filter by demo status"
                        value={demoStatusFilter}
                        onChange={(value) => {
                          setDemoStatusFilter(value as BundleOrganizerDemoStatusFilter);
                          setSelectedMessageIdentityKey(null);
                          setContextMenu(null);
                        }}
                      >
                        {demoStatusFilterOptions.map((option) => (
                          <option key={option.id} value={option.id}>
                            {option.label}
                          </option>
                        ))}
                      </FilterSelect>
                      <FilterSelect
                        ariaLabel="Sort demo messages"
                        value={demoSort}
                        onChange={(value) => {
                          setDemoSort(value as BundleOrganizerDateSort);
                          setSelectedMessageIdentityKey(null);
                          setContextMenu(null);
                        }}
                      >
                        {dateSortOptions.map((option) => (
                          <option key={option.id} value={option.id}>
                            {option.label}
                          </option>
                        ))}
                      </FilterSelect>
                    </div>
                  ) : null}

                  {activeView === "promo" ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <FilterSelect
                        ariaLabel="Filter promo status"
                        value={promoStatusFilter}
                        onChange={(value) => {
                          setPromoStatusFilter(value as BundleOrganizerPromoStatusFilter);
                          setSelectedMessageIdentityKey(null);
                          setContextMenu(null);
                        }}
                      >
                        {promoStatusFilterOptions.map((option) => (
                          <option key={option.id} value={option.id}>
                            {option.label}
                          </option>
                        ))}
                      </FilterSelect>
                      <FilterSelect
                        ariaLabel="Sort promo messages"
                        value={promoSort}
                        onChange={(value) => {
                          setPromoSort(value as BundleOrganizerDateSort);
                          setSelectedMessageIdentityKey(null);
                          setContextMenu(null);
                        }}
                      >
                        {dateSortOptions.map((option) => (
                          <option key={option.id} value={option.id}>
                            {option.label}
                          </option>
                        ))}
                      </FilterSelect>
                    </div>
                  ) : null}
                </div>
              ) : null}

              {selectedMessage ? (
                <MessageDetail
                  message={selectedMessage}
                  onBack={() => setSelectedMessageIdentityKey(null)}
                  onSetReadState={setMessageReadState}
                  onToggleShortlist={toggleMessageShortlist}
                  onToggleTrash={toggleMessageTrash}
                />
              ) : visibleRows.length === 0 ? (
                <div className="mt-4 rounded-[18px] border border-white/10 bg-white/5 px-5 py-10 text-center">
                  <h3 className="text-[1rem] font-semibold tracking-[-0.02em] text-[color:#f5efe5]">
                    {isSearchActive
                      ? "No matching messages."
                      : isViewFilterActive
                      ? "No messages match this filter."
                      : hasOrganizerData
                      ? activeCopy.emptyTitle
                      : "No messages loaded."}
                  </h3>
                  <p className="mx-auto mt-2 max-w-[460px] text-[0.86rem] leading-6 text-[rgba(245,239,229,0.58)]">
                    {isSearchActive
                      ? "Try a different sender, subject, snippet, or source mailbox."
                      : isViewFilterActive
                      ? "Try another inbox, status, or filter."
                      : hasOrganizerData
                      ? activeCopy.emptyDescription
                      : "Connected inbox messages will appear here after sync."}
                  </p>
                </div>
              ) : (
                <ul className="mt-4 overflow-hidden rounded-[16px] border border-white/10 bg-white/5">
                  {visibleRows.map(({ message, sourceView }) => (
                    <li
                      key={`${isSearchActive ? "search" : activeView}-${sourceView}-${message.id}`}
                      className="border-b border-white/10 last:border-b-0"
                    >
                      <div
                        className="relative border-l-2 border-transparent transition-[background-color,border-color,box-shadow] hover:bg-white/5"
                      >
                        <button
                          type="button"
                          onContextMenu={(event) => {
                            event.preventDefault();
                            event.stopPropagation();
                            openContextMenu(
                              message,
                              sourceView,
                              event.clientX,
                              event.clientY,
                            );
                          }}
                          onClick={() => {
                            openMessage(message);
                          }}
                          className="grid w-full gap-3 px-3 py-3.5 pr-12 text-left transition-[background-color,border-color,box-shadow] sm:grid-cols-[minmax(150px,0.55fr)_minmax(0,2.6fr)_minmax(72px,auto)] sm:px-4 sm:pr-14 lg:grid-cols-[minmax(170px,0.46fr)_minmax(0,3fr)_minmax(82px,auto)] xl:px-5 xl:pr-14"
                        >
                          <div className="grid min-w-0 grid-cols-[0.5rem_minmax(0,1fr)] items-start gap-2">
                            <span
                              aria-hidden="true"
                              className={`mt-[0.36rem] h-2 w-2 rounded-full ${
                                message.unread
                                  ? "bg-[#a78bfa] shadow-[0_0_0_2px_rgba(167,139,250,0.2)]"
                                  : "bg-transparent"
                              }`}
                            />
                            <div className="min-w-0">
                              <div className="truncate text-[0.92rem] font-semibold tracking-[-0.01em] text-[color:#f5efe5]">
                                {message.sender}
                              </div>
                              <div className="mt-1 flex flex-wrap gap-1.5">
                                {isSearchActive ? (
                                  <span className="rounded-full bg-[rgba(143,179,159,0.14)] px-2 py-0.5 text-[0.68rem] font-medium text-[rgba(167,203,181,0.9)]">
                                    {getViewLabel(sourceView)}
                                  </span>
                                ) : null}
                                <MessagePills message={message} />
                              </div>
                            </div>
                          </div>
                          <div className="min-w-0">
                            <div className="truncate text-[0.95rem] font-medium tracking-[-0.01em] text-[rgba(245,239,229,0.88)]">
                              {message.subject}
                            </div>
                            <p className="mt-1 line-clamp-2 text-[0.84rem] leading-5 text-[rgba(245,239,229,0.6)]">
                              {message.snippet}
                            </p>
                            {sourceView === "priority" && resolvePriorityReason(message) ? (
                              <p className="mt-2 text-[0.76rem] font-medium uppercase tracking-[0.12em] text-[rgba(143,179,159,0.78)]">
                                {resolvePriorityReason(message)}
                              </p>
                            ) : null}
                          </div>
                          <div className="text-[0.78rem] font-medium text-[rgba(245,239,229,0.45)] sm:pt-0.5 sm:text-right">
                            {message.timestamp}
                          </div>
                        </button>
                        <button
                          type="button"
                          aria-expanded={
                            contextMenu?.messageId === message.id &&
                            contextMenu.sourceView === sourceView
                          }
                          aria-haspopup="menu"
                          aria-label="Message actions"
                          data-organizer-context-menu-surface="true"
                          data-organizer-list-control="true"
                          onMouseDown={(event) => {
                            event.preventDefault();
                            event.stopPropagation();
                          }}
                          onClick={(event) => {
                            event.preventDefault();
                            event.stopPropagation();
                            const rect = event.currentTarget.getBoundingClientRect();
                            openContextMenu(
                              message,
                              sourceView,
                              rect.right - contextMenuWidth,
                              rect.bottom + contextMenuGap,
                            );
                          }}
                          className={`absolute right-3 top-3 inline-flex h-8 w-8 items-center justify-center rounded-full border text-[1rem] leading-none transition-colors sm:right-4 ${
                            contextMenu?.messageId === message.id &&
                            contextMenu.sourceView === sourceView
                              ? "border-[rgba(143,179,159,0.28)] bg-[rgba(143,179,159,0.12)] text-[rgba(198,228,209,0.9)]"
                              : "border-white/10 bg-white/5 text-[rgba(245,239,229,0.48)] hover:border-[rgba(143,179,159,0.24)] hover:bg-[rgba(143,179,159,0.1)] hover:text-[rgba(198,228,209,0.86)]"
                          }`}
                        >
                          <span aria-hidden="true">...</span>
                        </button>
                        {activeView === "shortlist" || activeView === "trash" ? (
                          <div className="flex justify-end px-3 pb-3 sm:px-4 xl:px-5">
                            <button
                              type="button"
                              onClick={(event) => {
                                event.preventDefault();
                                event.stopPropagation();
                                if (activeView === "trash") {
                                  toggleMessageTrash(message);
                                  return;
                                }
                                toggleMessageShortlist(message);
                              }}
                              data-organizer-list-control="true"
                              className="inline-flex h-8 items-center justify-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-3 text-[0.68rem] font-medium uppercase tracking-[0.1em] text-[rgba(245,239,229,0.56)] transition-colors hover:border-[rgba(143,179,159,0.24)] hover:bg-[rgba(143,179,159,0.1)] hover:text-[rgba(198,228,209,0.88)]"
                            >
                              <ContextMenuIcon
                                name={
                                  activeView === "trash"
                                    ? "restore"
                                    : "shortlistOff"
                                }
                              />
                              <span>
                                {activeView === "trash"
                                  ? "Restore"
                                  : "Remove from shortlist"}
                              </span>
                            </button>
                          </div>
                        ) : null}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              {contextMenu && contextMenuActions.length > 0 ? (
                <div
                  ref={menuRef}
                  role="menu"
                  data-organizer-context-menu-surface="true"
                  onContextMenu={(event) => event.preventDefault()}
                  onMouseDown={(event) => event.stopPropagation()}
                  style={{
                    left: contextMenu.x,
                    maxHeight: `calc(100vh - ${contextMenuViewportPadding * 2}px)`,
                    top: contextMenu.y,
                  }}
                  className="fixed z-50 min-w-[190px] overflow-y-auto rounded-[14px] border border-white/10 bg-[rgba(25,34,30,0.96)] p-1.5 shadow-[0_18px_40px_rgba(0,0,0,0.34)] backdrop-blur-sm"
                >
                  {contextMenuActions.map((action) => (
                    <button
                      key={action.label}
                      type="button"
                      role="menuitem"
                      disabled={action.disabled}
                      title={action.disabledReason}
                      onClick={() => {
                        if (action.disabled) {
                          return;
                        }
                        setContextMenu(null);
                        action.onSelect?.();
                      }}
                      className={`flex w-full items-center gap-2.5 rounded-[10px] px-3 py-2 text-left text-[0.82rem] font-medium transition-colors ${
                        action.disabled
                          ? "cursor-not-allowed text-[rgba(245,239,229,0.34)]"
                          : "text-[rgba(245,239,229,0.72)] hover:bg-[rgba(143,179,159,0.12)] hover:text-[rgba(198,228,209,0.94)]"
                      }`}
                    >
                      {action.icon ? <ContextMenuIcon name={action.icon} /> : null}
                      <span>{action.label}</span>
                    </button>
                  ))}
                </div>
              ) : null}
            </section>
          </main>
        </div>
      </section>
    </div>
  );
}
