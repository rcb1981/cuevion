import { memo, useEffect, useRef, useState } from "react";
import {
  createMailboxTitleEditorState,
  transitionMailboxTitleEditor,
  type MailboxTitleEditorAction,
} from "./mailboxTitleEditorState";

export const MailboxTitleEditor = memo(function MailboxTitleEditor({
  canonicalTitle,
  displayTitle,
  canEdit,
  onCommit,
}: {
  canonicalTitle: string;
  displayTitle: string;
  canEdit: boolean;
  onCommit: (nextTitle: string) => void;
}) {
  const [editorState, setEditorState] = useState(() =>
    createMailboxTitleEditorState(canonicalTitle),
  );
  const editorStateRef = useRef(editorState);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const applyAction = (action: MailboxTitleEditorAction) => {
    const transition = transitionMailboxTitleEditor(
      editorStateRef.current,
      action,
    );
    editorStateRef.current = transition.state;
    setEditorState(transition.state);
    if (transition.commitTitle !== null) {
      onCommit(transition.commitTitle);
    }
  };

  useEffect(() => {
    if (editorState.isEditing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editorState.isEditing]);

  if (editorState.isEditing) {
    return (
      <input
        ref={inputRef}
        value={editorState.draft}
        onChange={(event) =>
          applyAction({ type: "change", value: event.target.value })
        }
        onBlur={() => applyAction({ type: "commit", canonicalTitle })}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            applyAction({ type: "commit", canonicalTitle });
          }

          if (event.key === "Escape") {
            event.preventDefault();
            applyAction({ type: "cancel", canonicalTitle });
          }
        }}
        className="min-w-0 bg-transparent text-[0.98rem] font-semibold tracking-[-0.01em] text-[var(--workspace-text)] outline-none md:text-[1.04rem] md:text-right"
      />
    );
  }

  return (
    <div className="group relative">
      <button
        type="button"
        onClick={() => {
          if (canEdit) {
            applyAction({ type: "open", canonicalTitle });
          }
        }}
        aria-label="Edit mailbox name"
        disabled={!canEdit}
        className={`inline-flex min-w-0 max-w-[min(70vw,32rem)] items-center gap-1.5 rounded-full text-[0.98rem] font-semibold tracking-[-0.01em] text-[var(--workspace-text)] transition-colors duration-200 focus-visible:outline-none md:text-[1.04rem] ${
          canEdit ? "cursor-pointer" : "cursor-default"
        }`}
      >
        <span className="block min-w-0 truncate">{displayTitle}</span>
        {canEdit ? (
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
      {canEdit ? (
        <div className="pointer-events-none absolute right-0 top-full z-10 mt-2 whitespace-nowrap rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-3.5 py-1 text-[0.62rem] font-medium tracking-[0.08em] text-[var(--workspace-text-soft)] opacity-0 shadow-panel transition-opacity duration-200 group-hover:opacity-100">
          Edit name
        </div>
      ) : null}
    </div>
  );
});
