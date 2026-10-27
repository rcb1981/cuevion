/** Inert Compose fragment only. Never a Send equivalence or MIME proof. */
const tags = new Set(["div", "p", "br", "strong", "em", "u", "s", "ul", "ol", "li", "blockquote", "pre", "a"]);
const aliases: Record<string, string> = { b: "strong", i: "em", strike: "s" };
function decode(value: string): string | null {
  let valid = true;
  const named: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: "\u00a0" };
  const result = value.replace(/&([a-zA-Z][a-zA-Z0-9]+|#(?:[0-9]+|x[0-9a-fA-F]+));/g, (_whole, entity: string) => {
    if (Object.prototype.hasOwnProperty.call(named, entity)) return named[entity];
    if (entity.startsWith("#")) {
      const number = entity[1] === "x" ? parseInt(entity.slice(2), 16) : Number(entity.slice(1));
      if (number >= 32 && number <= 0x10ffff && !(number >= 0xd800 && number <= 0xdfff) && !(number >= 127 && number <= 159)) return String.fromCodePoint(number);
    }
    valid = false; return "";
  });
  return valid ? result : null;
}
const escape = (text: string) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
function canonicalText(text: string): string | null {
  const decoded = decode(text); return decoded === null ? null : escape(decoded);
}
export function microsoftPlainToHtml(text: string): string {
  return "<div>" + text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\r?\n/g, "<br>") + "</div>";
}
export function canonicalMicrosoftComposeHtml(html: string): string | null {
  if (typeof html !== "string" || html.length > 32000 || html.includes("\0") || new TextEncoder().encode(html).length > 128000) return null;
  const stack: string[] = [];
  let result = "", position = 0;
  for (const match of html.matchAll(/<[^>]*>/g)) {
    const text = html.slice(position, match.index);
    if (text.includes("<")) return null;
    const safeText = canonicalText(text);
    if (safeText === null) return null;
    result += safeText;
    const token = /^<(\/?)([a-z]+)(?: href="([^"<>]*)")?(\/?)>$/.exec(match[0]);
    if (!token) return null;
    const [, close, raw, href, self] = token, tag = aliases[raw] ?? raw;
    if (!tags.has(tag) || stack.length >= 64 || self && tag !== "br" || close && (self || href !== undefined)) return null;
    const link = href === undefined ? undefined : decode(href);
    if (link === null || link !== undefined && (tag !== "a" || link.length > 2048 || !/^(https?:\/\/|mailto:)[^\s<>"\x00-\x20\x7f]+$/i.test(link))) return null;
    if (close) {
      if (stack.pop() !== tag) return null;
      result += "</" + tag + ">";
    } else {
      result += "<" + tag + (link !== undefined ? ' href="' + escape(link).replace(/"/g, "&quot;").replace(/'/g, "&#x27;") + '"' : "") + ">";
      if (tag !== "br") stack.push(tag);
    }
    position = match.index! + match[0].length;
  }
  const tail = html.slice(position);
  if (tail.includes("<") || stack.length) return null;
  const safeTail = canonicalText(tail);
  if (safeTail === null) return null;
  result += safeTail;
  return result.length <= 32000 ? result : null;
}
