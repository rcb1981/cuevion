import React, { useEffect, useMemo, useState } from "react";
import { isTrustedMicrosoftInertHtmlTransport } from "../../lib/microsoftInertHtmlTransport";
import {
  loadMicrosoftInlineCidImages,
  discoverMicrosoftInlineCids,
  isMicrosoftInlineCidFolder,
  MAX_MICROSOFT_CID_TOTAL_BYTES,
  type MicrosoftCidImageMap,
} from "../../lib/microsoftInlineCidImages";
import type { MicrosoftAttachmentMetadata } from "../../lib/inboxConnectionApi";

/**
 * Microsoft-only inert rich HTML viewer.
 *
 * WIP on a local branch: Graph exact Inbox HTML is server-sanitized and
 * validated at the client DTO boundary. This viewer applies independent DOM
 * sanitization and an opaque, scriptless iframe before displaying it.
 * Browser-DOM / CSP testing and live mailbox acceptance are not complete.
 */
const MAX_HTML = 294_912;
const MAX_IMAGES = 128;
const TAGS = new Set([
  "p", "div", "span", "br", "b", "strong", "em", "i", "u", "s",
  "small", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "code",
  "blockquote", "ul", "ol", "li", "table", "thead", "tbody",
  "tfoot", "tr", "td", "th", "hr", "a", "img", "center",
]);
const STYLES = new Set([
  "color", "background-color", "font-size", "font-weight", "font-style",
  "font-family", "line-height", "text-align", "text-decoration", "vertical-align",
  "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
  "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
  "border", "border-color", "border-width", "border-style", "border-collapse",
  "width", "max-width", "height",
  "min-width", "min-height", "max-height", "border-radius",
  "border-top", "border-right", "border-bottom", "border-left",
  "letter-spacing", "text-transform", "white-space", "word-break",
  "overflow-wrap", "table-layout", "box-sizing", "display",
]);
const PRESENTATION = new Set([
  "style", "width", "height", "colspan", "rowspan", "align", "valign", "bgcolor",
]);
const SAFE_CID = /^[a-zA-Z0-9_.@+%\-]{1,200}$/;
const SAFE_STYLE_VALUE = /^[a-zA-Z0-9\s#.,%_+/\-]{1,160}$/;
const SAFE_RGB_COLOR = /^(?:rgb\(\s*\d{1,3}(?:\s*,\s*\d{1,3}){2}\s*\)|rgba\(\s*\d{1,3}(?:\s*,\s*\d{1,3}){2}\s*,\s*(?:0(?:\.\d{1,3})?|1(?:\.0{1,3})?)\s*\))$/;
const COLOR_STYLES = new Set(["color", "background-color", "border-color"]);
const UNSAFE_SOURCE =
  /<(?:script|style|svg|math|iframe|object|embed|form|input|meta|base|link|template|noscript)\b|(?:\s)(?:src|srcset|poster|on[a-z]+|action|formaction|background)\s*=|url\s*\(|@import|expression\s*\(|javascript\s*:|vbscript\s*:|data\s*:|\\|<\s*\/\s*(?:script|iframe|svg|style)\b/i;

export function isSafeMicrosoftRemoteImageUrl(value: string): boolean {
  if (!value || value.length > 2048 || /[\x00-\x20\x7f\\]/.test(value)) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && Boolean(url.hostname) &&
      !url.username && !url.password && !url.hash &&
      (!url.port || url.port === "443");
  } catch {
    return false;
  }
}

function sanitizeInlineStyle(style: string): string {
  if (!style || style.length > 2000 || /\/\*|\*\/|\\|@|[{}<>]/.test(style)) return "";
  const parts: string[] = [];
  for (const item of style.split(";")) {
    const colon = item.indexOf(":");
    if (colon < 1) continue;
    const name = item.slice(0, colon).trim().toLowerCase();
    const value = item.slice(colon + 1).trim();
    if (!STYLES.has(name) || !(SAFE_STYLE_VALUE.test(value) ||
      (COLOR_STYLES.has(name) && SAFE_RGB_COLOR.test(value))) ||
      /url|expression|javascript|vbscript|import|behavior/i.test(value)) continue;
    parts.push(name + ": " + value);
    if (parts.length >= 24) break;
  }
  return parts.join("; ");
}

export type MicrosoftSafeHtmlResult = {
  html: string | null;
  remoteImageCount: number;
  cidImageCount: number;
  blockedReason?: "invalid" | "unavailable";
  suppressedCidImages?: number;
};

function isSafeMicrosoftCidDataUrl(value: unknown): value is string {
  return typeof value === "string" && value.length <= 2_800_000 &&
    /^data:image\/(?:png|jpeg|gif|webp);base64,(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value);
}

function cssBrightness(value: string): number | null {
  const color = value.trim().toLowerCase();
  const named: Record<string, number> = {white: 255, black: 0};
  if (Object.prototype.hasOwnProperty.call(named, color)) return named[color];
  const match = /^#([0-9a-f]{3}|[0-9a-f]{6})$/.exec(color);
  if (match) {
    const hex = match[1].length === 3
      ? [...match[1]].map((c) => c + c).join("") : match[1];
    const r = Number.parseInt(hex.slice(0, 2), 16);
    const g = Number.parseInt(hex.slice(2, 4), 16);
    const b = Number.parseInt(hex.slice(4, 6), 16);
    return (r * 299 + g * 587 + b * 114) / 1000;
  }
  const rgb = /^rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$/.exec(color);
  if (!rgb) return null;
  const [r, g, b] = rgb.slice(1).map(Number);
  return r <= 255 && g <= 255 && b <= 255
    ? (r * 299 + g * 587 + b * 114) / 1000 : null;
}

function cssProperty(style: string | null, property: string): string {
  const values = (style ?? "").split(";")
    .map((declaration) => declaration.split(":"))
    .filter(([name, value]) => name?.trim().toLowerCase() === property && value)
    .map(([, value]) => value.trim());
  return values.length > 0 ? values[values.length - 1] : "";
}

/**
 * Gmail/IMAP correct low-contrast provider HTML after load. Microsoft's
 * opaque origin forbids touching iframe DOM after mount; conservatively fix
 * very light text on the white default background before building srcDoc.
 */
function restoreMicrosoftEmailTextContrast(root: HTMLElement): void {
  for (const element of Array.from(root.querySelectorAll<HTMLElement>("[style]"))) {
    const ownStyle = element.getAttribute("style");
    const brightness = cssBrightness(cssProperty(ownStyle, "color"));
    if (brightness === null || brightness < 225) continue;
    let darkBackground = false;
    for (let parent: HTMLElement | null = element; parent && parent !== root; parent = parent.parentElement) {
      const bg = cssProperty(parent.getAttribute("style"), "background-color") ||
        (parent.getAttribute("bgcolor") ?? "");
      const brightnessBg = cssBrightness(bg);
      if (brightnessBg !== null) {
        darkBackground = brightnessBg < 140;
        break;
      }
    }
    if (!darkBackground) {
      const updated = (ownStyle ?? "").split(";").filter((part) =>
        part.split(":")[0]?.trim().toLowerCase() !== "color"
      ).join(";").trim();
      if (updated) element.setAttribute("style", updated);
      else element.removeAttribute("style");
    }
  }
}

export function sanitizeMicrosoftInertHtmlForStage(
  source: string, showRemoteImages: boolean,
  cidImages: MicrosoftCidImageMap = {},
): MicrosoftSafeHtmlResult {
  if (!isTrustedMicrosoftInertHtmlTransport(source) || source.length > MAX_HTML || UNSAFE_SOURCE.test(source)) {
    return { html: null, remoteImageCount: 0, cidImageCount: 0, blockedReason: "invalid" };
  }
  if (typeof DOMParser === "undefined") {
    return { html: null, remoteImageCount: 0, cidImageCount: 0, blockedReason: "unavailable" };
  }

  // The backend guarantees no live src/remote CSS before this inactive parse.
  const parsed = new DOMParser().parseFromString(source, "text/html");
  const all = Array.from(parsed.body.querySelectorAll("*"));
  if (all.length > 10_000) {
    return { html: null, remoteImageCount: 0, cidImageCount: 0, blockedReason: "invalid" };
  }
  let remoteImageCount = 0;
  let cidImageCount = 0;
  let suppressedCidImages = 0;
  let embeddedImageChars = 0;
  const maxEmbeddedImageChars =
    4 * Math.ceil(MAX_MICROSOFT_CID_TOTAL_BYTES / 3) + 4096;
  const cidUrlValidationCache = new Map<string, boolean>();
  for (const el of all) {
    const tag = el.tagName.toLowerCase();
    if (!TAGS.has(tag)) {
      el.replaceWith(...Array.from(el.childNodes));
      continue;
    }
    for (const attr of Array.from(el.attributes)) {
      const key = attr.name.toLowerCase(), value = attr.value;
      if (key === "style") {
        const safe = sanitizeInlineStyle(value);
        if (safe) el.setAttribute("style", safe);
        else el.removeAttribute(key);
      } else if (PRESENTATION.has(key)) {
        if (!/^[a-zA-Z0-9\s#.,%_+/\-]{1,160}$/.test(value)) el.removeAttribute(key);
      } else if (tag === "a" && key === "href") {
        if (!isSafeMicrosoftRemoteImageUrl(value)) el.removeAttribute(key);
      } else if (tag === "img" && key === "alt") {
        el.setAttribute("alt", value.slice(0, 180));
      } else if (tag === "img" && key === "data-microsoft-cid") {
        if (SAFE_CID.test(value)) {
          cidImageCount += 1;
          // Only authenticated, size-bounded PNG/JPEG/GIF/WebP bytes may enter
          // the opaque iframe. Never resolve a raw cid: URL in the browser.
          const dataUrl = Object.prototype.hasOwnProperty.call(cidImages, value)
            ? cidImages[value] : undefined;
          if (dataUrl) {
            let verified = cidUrlValidationCache.get(value);
            if (verified === undefined) {
              verified = isSafeMicrosoftCidDataUrl(dataUrl);
              cidUrlValidationCache.set(value, verified);
            }
            if (verified && embeddedImageChars + dataUrl.length <= maxEmbeddedImageChars) {
              embeddedImageChars += dataUrl.length;
              el.setAttribute("src", dataUrl);
            } else {
              suppressedCidImages += 1;
            }
          }
        } else el.removeAttribute(key);
        el.removeAttribute(key);
      } else if (tag === "img" && key === "data-microsoft-remote-src") {
        if (isSafeMicrosoftRemoteImageUrl(value) && remoteImageCount < MAX_IMAGES) {
          remoteImageCount += 1;
          if (showRemoteImages) {
            el.setAttribute("src", value);
            el.setAttribute("referrerpolicy", "no-referrer");
            el.setAttribute("loading", "lazy");
          }
        }
        el.removeAttribute(key);
      } else {
        el.removeAttribute(attr.name);
      }
    }
    if (tag === "a" && el.hasAttribute("href")) {
      el.setAttribute("target", "_blank");
      el.setAttribute("rel", "noopener noreferrer");
      el.setAttribute("referrerpolicy", "no-referrer");
    }
  }
  restoreMicrosoftEmailTextContrast(parsed.body);
  // Never display an apparently successful, blank white iframe. The stable
  // Gmail/IMAP renderer also falls back when sanitization leaves no content.
  const hasText = Boolean(parsed.body.textContent?.replace(/\s+/g, ""));
  const hasImages = Boolean(parsed.body.querySelector('img[src]'));
  if (!hasText && !hasImages) {
    return {html: null, remoteImageCount, cidImageCount, blockedReason: "unavailable"};
  }

  // MAX_HTML bounds the original sanitized mail body. Authorized CID bytes
  // are embedded later as base64. NEVER slice the final HTML: that truncates
  // src="data:..." halfway and can silently remove whole image/table nodes.
  const renderedHtml = parsed.body.innerHTML;
  const maxRenderedHtml = MAX_HTML +
    4 * Math.ceil(MAX_MICROSOFT_CID_TOTAL_BYTES / 3) + 4096;
  if (renderedHtml.length > maxRenderedHtml) {
    return {html: null, remoteImageCount: 0, cidImageCount: 0, blockedReason: "invalid"};
  }
  return {
    html: renderedHtml,
    remoteImageCount,
    cidImageCount,
    suppressedCidImages,
  };
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/"/g, "&quot;")
    .replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function buildStageDocument(bodyHtml: string): string {
  // Microsoft-only product choice: display HTTPS remote images automatically.
  // Unlike Gmail's image proxy, this exposes the open to remote image hosts.
  // Scripts, HTTP images, forms, frames and network JS remain blocked.
  const imgPolicy = "img-src https: data:;";
  const csp = [
    "default-src 'none';",
    "script-src 'none';",
    "style-src 'unsafe-inline';",
    imgPolicy,
    "connect-src 'none';",
    "object-src 'none';",
    "font-src 'none';",
    "frame-src 'none';",
    "form-action 'none';",
    "base-uri 'none';",
  ].join(" ");

  return (
    '<!doctype html><html><head><meta charset="utf-8">' +
    '<meta http-equiv="Content-Security-Policy" content="' + escapeHtml(csp) + '">' +
    '<meta name="referrer" content="no-referrer">' +
    '<style>html,body{margin:0;padding:0;background:#fff;color:#242424;' +
    'font:14px/1.55 Arial,Helvetica,sans-serif;overflow-x:auto;overflow-y:auto}' +
    'body{padding:12px 6px 18px}table{max-width:100%;border-collapse:collapse}' +
    'img{max-width:100%;height:auto}a{color:#286a52;overflow-wrap:anywhere}' +
    'pre{white-space:pre-wrap}</style></head><body>' + bodyHtml + '</body></html>'
  );
}

export function MicrosoftInertHtmlStage({
  html, fallbackText, messageKey, mailboxId, providerMessageId,
  folder, attachments, attachmentsBusy = false,
}: {
  html: string; fallbackText: string; messageKey: string;
  mailboxId?: string; providerMessageId?: string; folder?: string;
  attachments?: MicrosoftAttachmentMetadata[];
  attachmentsBusy?: boolean;
}) {
  const [cidResult, setCidResult] = useState<{
    html: string;
    attachments: MicrosoftAttachmentMetadata[];
    images: MicrosoftCidImageMap;
  } | null>(null);
  const expectedCids = useMemo(() => discoverMicrosoftInlineCids(html), [html]);
  const currentCidResult = cidResult?.html === html &&
    cidResult.attachments === attachments ? cidResult : null;
  const cidImages: MicrosoftCidImageMap = currentCidResult?.images ?? {};
  const waitingForCid = expectedCids.length > 0 &&
    (attachmentsBusy || (Boolean(attachments?.length) && !currentCidResult));
  useEffect(() => {
    let current = true;
    setCidResult(null);
    if (isMicrosoftInlineCidFolder(folder) && mailboxId && providerMessageId &&
        attachments?.length && expectedCids.length > 0) {
      void loadMicrosoftInlineCidImages(
        { html, mailboxId, providerMessageId, folder, attachments },
        () => current,
      ).then((images) => {
        if (current) setCidResult({ html, attachments, images });
      }).catch(() => {
        if (current) setCidResult({ html, attachments, images: {} });
      });
    }
    return () => { current = false; };
  }, [html, mailboxId, providerMessageId, folder, attachments, expectedCids]);
  // Wait for the authenticated exact-CID reads before mounting the remote
  // image iframe. Otherwise a CID result changes srcDoc and requests HTTPS
  // tracking images a second time for the same message.
  const rendered = useMemo(
    () => waitingForCid ? null : sanitizeMicrosoftInertHtmlForStage(html, true, cidImages),
    [html, cidImages, waitingForCid],
  );
  const unresolvedCidCount = expectedCids.filter((cid) =>
    !Object.prototype.hasOwnProperty.call(cidImages, cid)
  ).length;

  if (waitingForCid) {
    return (
      <section aria-label="Microsoft HTML message content" data-message-key={messageKey}>
        <div className="block w-full bg-white px-3 py-3 text-[#242424]"
          style={{ minHeight: 320, maxHeight: "calc(100dvh - 96px)" }}>
          <p role="status" className="mb-3 text-xs text-[#666666]">
            Loading inline images…
          </p>
          <div className="whitespace-pre-wrap break-words text-sm leading-7">
            {fallbackText}
          </div>
        </div>
      </section>
    );
  }
  if (!rendered?.html) {
    return (
      <div role="note" className="whitespace-pre-wrap break-words text-sm leading-7 text-[var(--workspace-text)]">
        {fallbackText}
      </div>
    );
  }
  return (
    <section aria-label="Microsoft HTML message content" data-message-key={messageKey}>
      {rendered.cidImageCount > 0 &&
        (unresolvedCidCount > 0 || (rendered.suppressedCidImages ?? 0) > 0) && (
        <p className="mb-3 text-xs text-[var(--workspace-text-muted)]" role="status">
          Some inline images could not be displayed securely.
        </p>
      )}
      <iframe
        title="Microsoft HTML email content"
        sandbox="allow-popups allow-popups-to-escape-sandbox"
        referrerPolicy="no-referrer"
        srcDoc={buildStageDocument(rendered.html)}
        className="block w-full border-0 bg-white"
        style={{ height: "clamp(320px, 50dvh, 600px)", maxHeight: "calc(100dvh - 96px)" }}
      />
    </section>
  );
}
