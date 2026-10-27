import React, { useLayoutEffect, useRef, useState } from "react";
import { createDesktopComposeBodyDraft } from "./desktopComposeBodyDraft";
import { canonicalMicrosoftComposeHtml } from "../../lib/microsoftComposeHtml";

type Props = { value: string; disabled: boolean; onChange: (html: string) => void };
/** Reuses the existing Compose draft buffer and plain-paste/keyboard conventions.
 * Only validated inert fragments enter the DOM. Quotes are rendered by the parent.
 */
export function MicrosoftComposeBodyEditor({ value, disabled, onChange }: Props) {
  const element = useRef<HTMLDivElement>(null);
  const buffer = useRef(createDesktopComposeBodyDraft(value));
  const [invalid, setInvalid] = useState(false);
  useLayoutEffect(() => {
    const safe = canonicalMicrosoftComposeHtml(value);
    if (safe === null) { setInvalid(true); return; }
    if (element.current && (buffer.current.getBodyHtml() !== safe || !element.current.innerHTML)) element.current.innerHTML = safe;
    buffer.current.replaceBodyHtml(safe); setInvalid(false);
  }, [value]);
  const input = () => {
    if (disabled || !element.current) return;
    const raw = element.current.innerHTML, safe = canonicalMicrosoftComposeHtml(raw);
    setInvalid(safe === null);
    buffer.current.recordInput(safe ?? raw);
    onChange(safe ?? raw); // Invalid markup disables saving through the API validator.
  };
  const command = (name: string, argument?: string) => {
    if (disabled || !element.current) return;
    element.current.focus();
    const selection = window.getSelection();
    if (!selection?.anchorNode || !selection.focusNode || !element.current.contains(selection.anchorNode) || !element.current.contains(selection.focusNode)) return;
    document.execCommand(name, false, argument); input();
  };
  const actions = [["Bold", "bold"], ["Italic", "italic"], ["Underline", "underline"], ["Bulleted list", "insertUnorderedList"], ["Numbered list", "insertOrderedList"], ["Undo", "undo"], ["Redo", "redo"]];
  return <div className="py-3">
    <div role="toolbar" aria-label="Message formatting" className="flex flex-wrap gap-2">
      {actions.map(([label, action]) => <button key={action} type="button" disabled={disabled} aria-label={label}
        className="rounded border border-[var(--workspace-border)] px-2 py-1 text-xs disabled:opacity-50"
        onMouseDown={event => event.preventDefault()} onClick={() => command(action)}>{label}</button>)}
    </div>
    <div ref={element} role="textbox" aria-label="Compose formatted message body" aria-multiline="true" aria-disabled={disabled}
      contentEditable={!disabled} suppressContentEditableWarning spellCheck tabIndex={disabled ? -1 : 0}
      className="min-h-[230px] whitespace-pre-wrap py-5 text-[0.92rem] leading-7 outline-none [&_ul]:list-disc [&_ol]:list-decimal [&_li]:ml-6"
      onInput={input} onDrop={event => { event.preventDefault(); event.stopPropagation(); }}
      onPaste={event => { event.preventDefault(); command("insertText", event.clipboardData.getData("text/plain")); }}
      onKeyDown={event => {
        if ((event.ctrlKey || event.metaKey) && ["b", "i", "u"].includes(event.key.toLowerCase())) {
          event.preventDefault(); command({ b: "bold", i: "italic", u: "underline" }[event.key.toLowerCase()]!);
        }
      }} />
    {invalid && <p role="alert">This formatting cannot be saved safely. Undo the change or paste plain text.</p>}
  </div>;
}
