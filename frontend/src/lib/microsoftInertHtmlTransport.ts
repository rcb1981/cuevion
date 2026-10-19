/** Narrow Microsoft exact-message HTML boundary, independent of Gmail/IMAP. */
export const MAX_MICROSOFT_INERT_HTML_LENGTH = 294_912;

const DISALLOWED_RENDER_MARKUP =
  /<(?:script|style|svg|math|iframe|object|embed|form|input|meta|base|link|template|noscript)\b|(?:\s)(?:src|srcset|poster|on[a-z]+|action|formaction|background)\s*=|url\s*\(|@import|expression\s*\(|javascript\s*:|vbscript\s*:|data\s*:|\\|<\s*\/\s*(?:script|iframe|svg|style)\b/i;

const SAFE_TAGS = new Set([
  "p", "div", "span", "br", "b", "strong", "em", "i", "u", "s",
  "small", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "code",
  "blockquote", "ul", "ol", "li", "table", "thead", "tbody",
  "tfoot", "tr", "td", "th", "hr", "a", "img", "center",
]);

/**
 * This is a transport-level prefilter, not an HTML sanitizer.
 * It rejects unsolicited raw HTML and any resource-triggering attributes
 * *before* the iframe stage may call DOMParser. The backend projector and
 * the stage's second sanitizer are both independently required.
 */
export function isTrustedMicrosoftInertHtmlTransport(value: unknown): value is string {
  if (typeof value !== "string" ||
      !value.trim() ||
      value.length > MAX_MICROSOFT_INERT_HTML_LENGTH ||
      DISALLOWED_RENDER_MARKUP.test(value)) return false;

  const tagPattern = /<\/?([a-z][a-z0-9-]*)\b[^>]*>/gi;
  for (const tag of value.matchAll(tagPattern)) {
    if (!SAFE_TAGS.has(tag[1].toLowerCase())) return false;
  }
  return true;
}
