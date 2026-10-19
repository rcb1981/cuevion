import React, { useEffect, useMemo, useState } from "react";
import { isTrustedMicrosoftInertHtmlTransport } from "../../lib/microsoftInertHtmlTransport";
import {
  loadMicrosoftInlineCidImages,
  isMicrosoftInlineCidFolder,
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
};

function isSafeMicrosoftCidDataUrl(value: unknown): value is string {
  return typeof value === "string" && value.length < 750_000 &&
    /^data:image\/(?:png|jpeg|gif|webp);base64,(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value);
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
          if (isSafeMicrosoftCidDataUrl(dataUrl)) el.setAttribute("src", dataUrl);
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
  return {
    html: parsed.body.innerHTML.slice(0, MAX_HTML),
    remoteImageCount,
    cidImageCount,
  };
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/"/g, "&quot;")
    .replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function buildStageDocument(bodyHtml: string, remoteImagesAllowed: boolean): string {
  const imgPolicy = remoteImagesAllowed ? "img-src https: data:;" : "img-src data:;";
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
  html, fallbackText, messageKey, mailboxId, providerMessageId, folder, attachments,
}: {
  html: string; fallbackText: string; messageKey: string;
  mailboxId?: string; providerMessageId?: string; folder?: string;
  attachments?: MicrosoftAttachmentMetadata[];
}) {
  // A keyed component must be mounted for each exact provider message identity
  // so that per-message remote-image consent does not cross message boundaries.
  const [showImages, setShowImages] = useState(false);
  const [cidImages, setCidImages] = useState<MicrosoftCidImageMap>({});
  useEffect(() => {
    let current = true;
    setCidImages({});
    if (isMicrosoftInlineCidFolder(folder) && mailboxId && providerMessageId && attachments?.length) {
      void loadMicrosoftInlineCidImages(
        { html, mailboxId, providerMessageId, folder, attachments },
        () => current,
      ).then((images) => {
        if (current) setCidImages(images);
      });
    }
    return () => { current = false; };
  }, [html, mailboxId, providerMessageId, folder, attachments]);
  const rendered = useMemo(
    () => sanitizeMicrosoftInertHtmlForStage(html, showImages, cidImages),
    [html, showImages, cidImages],
  );

  if (!rendered.html) {
    return (
      <div role="note" className="whitespace-pre-wrap break-words text-sm leading-7 text-[var(--workspace-text)]">
        {fallbackText}
      </div>
    );
  }
  return (
    <section aria-label="Microsoft HTML message content" data-message-key={messageKey}>
      {rendered.remoteImageCount > 0 && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-3 py-2 text-xs text-[var(--workspace-text-soft)]">
          <span>
            {showImages
              ? "Remote images enabled for this message."
              : rendered.remoteImageCount + " remote image(s) blocked for privacy."}
          </span>
          <button type="button" onClick={() => setShowImages((v) => !v)}
            className="rounded-full border border-[var(--workspace-border)] px-3 py-1.5 font-medium text-[var(--workspace-text)] hover:bg-[var(--workspace-hover-surface)]">
            {showImages ? "Hide images" : "Show images"}
          </button>
        </div>
      )}
      {rendered.cidImageCount > 0 && (
        <p className="mb-3 text-xs text-[var(--workspace-text-muted)]">
          {Object.keys(cidImages).length < rendered.cidImageCount
            ? "Some inline images could not be displayed securely."
            : "Inline images are provided by this email, without loading external sources."}
        </p>
      )}
      <iframe
        title="Microsoft HTML email content"
        sandbox="allow-popups allow-popups-to-escape-sandbox"
        referrerPolicy="no-referrer"
        srcDoc={buildStageDocument(rendered.html, showImages)}
        className="block w-full border-0 bg-white"
        style={{ height: "clamp(320px, 50dvh, 600px)", maxHeight: "calc(100dvh - 96px)" }}
      />
    </section>
  );
}
