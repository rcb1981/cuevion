import React from "react";

const URL_PATTERN = /https?:\/\/[^\s<>"']+/gi;

export function safeMicrosoftLink(value: string): string | null {
  if (!value || value.length > 2048 || value.includes("\\") || /\s/.test(value)) {
    return null;
  }
  try {
    const parsed = new URL(value);
    if (
      !["https:", "http:"].includes(parsed.protocol) ||
      !parsed.hostname ||
      parsed.username ||
      parsed.password
    ) return null;
    return parsed.href;
  } catch {
    return null;
  }
}

export function MicrosoftLinkifiedText({ text }: { text: string }) {
  const result: React.ReactNode[] = [];
  let offset = 0;
  for (const match of text.matchAll(URL_PATTERN)) {
    const index = match.index ?? 0;
    const original = match[0];
    if (index > offset) result.push(text.slice(offset, index));
    let candidate = original.replace(/[.,;:!?]+$/g, "");
    while (candidate.endsWith(")") &&
      (candidate.match(/\)/g) ?? []).length > (candidate.match(/\(/g) ?? []).length) {
      candidate = candidate.slice(0, -1);
    }
    const href = safeMicrosoftLink(candidate);
    if (href) {
      result.push(<a key={index} href={href} target="_blank"
        rel="noopener noreferrer" referrerPolicy="no-referrer"
        className="break-all underline underline-offset-2">{candidate}</a>);
      result.push(original.slice(candidate.length));
    } else {
      result.push(original);
    }
    offset = index + original.length;
  }
  if (offset < text.length) result.push(text.slice(offset));
  return <>{result}</>;
}
