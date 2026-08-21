import {
  forwardRef,
  memo,
  useCallback,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
  type ClipboardEvent as ReactClipboardEvent,
  type Dispatch,
  type KeyboardEvent as ReactKeyboardEvent,
  type SetStateAction,
} from "react";
import {
  createDesktopComposeBodyDraft,
  type DesktopComposeBodyDraft,
} from "./desktopComposeBodyDraft";

export type DesktopComposeBodyEditorHandle = {
  applyBodyTransform: (transform: (bodyHtml: string) => string) => string;
  focusAtStart: () => void;
  getBodyHtml: () => string;
};

type DesktopComposeBodyEditorProps = {
  bodyHtml: string;
  className: string;
  quoteExpanded: boolean;
  setQuoteExpanded: Dispatch<SetStateAction<boolean>>;
  showQuoteDisclosure: boolean;
  signatureSelection: string;
  setSignatureSelection: Dispatch<SetStateAction<string>>;
};

function hasComposeQuote(bodyHtml: string) {
  return bodyHtml.includes('data-compose-quote="true"');
}

export const DesktopComposeBodyEditor = memo(
  forwardRef<DesktopComposeBodyEditorHandle, DesktopComposeBodyEditorProps>(
    function DesktopComposeBodyEditor(
      {
        bodyHtml,
        className,
        quoteExpanded,
        setQuoteExpanded,
        showQuoteDisclosure,
        signatureSelection,
        setSignatureSelection,
      },
      forwardedRef,
    ) {
      const editorRef = useRef<HTMLDivElement | null>(null);
      const draftRef = useRef<DesktopComposeBodyDraft | null>(null);
      if (!draftRef.current) {
        draftRef.current = createDesktopComposeBodyDraft(bodyHtml);
      }
      const hasQuoteRef = useRef(hasComposeQuote(bodyHtml));
      const [hasQuote, setHasQuote] = useState(hasQuoteRef.current);

      const publishMarkerChanges = useCallback(
        (nextBodyHtml: string, editor: HTMLDivElement) => {
          const nextHasQuote = hasComposeQuote(nextBodyHtml);
          if (nextHasQuote !== hasQuoteRef.current) {
            hasQuoteRef.current = nextHasQuote;
            setHasQuote(nextHasQuote);
          }

          if (signatureSelection === "none") {
            return;
          }

          const signatureNode = editor.querySelector("[data-compose-signature]");
          const hasSignatureNodeContent =
            Boolean(signatureNode?.querySelector("img")) ||
            Boolean(signatureNode?.textContent?.replace(/\u00a0/g, " ").trim());

          if (!signatureNode || !hasSignatureNodeContent) {
            setSignatureSelection("none");
          }
        },
        [setSignatureSelection, signatureSelection],
      );

      const recordEditorInput = useCallback(() => {
        const editor = editorRef.current;
        const draft = draftRef.current;

        if (!editor || !draft) {
          return;
        }

        const nextBodyHtml = editor.innerHTML;
        draft.recordInput(nextBodyHtml);
        publishMarkerChanges(nextBodyHtml, editor);
      }, [publishMarkerChanges]);

      useLayoutEffect(() => {
        const editor = editorRef.current;
        const draft = draftRef.current;

        if (!editor || !draft) {
          return;
        }

        draft.replaceBodyHtml(bodyHtml);
        if (editor.innerHTML !== bodyHtml) {
          editor.innerHTML = bodyHtml;
        }
        publishMarkerChanges(bodyHtml, editor);
      }, [bodyHtml]);

      useImperativeHandle(
        forwardedRef,
        () => ({
          applyBodyTransform: (transform) => {
            const editor = editorRef.current;
            const draft = draftRef.current;
            const nextBodyHtml = transform(draft?.getBodyHtml() ?? bodyHtml);

            draft?.replaceBodyHtml(nextBodyHtml);
            if (editor && editor.innerHTML !== nextBodyHtml) {
              editor.innerHTML = nextBodyHtml;
            }
            if (editor) {
              publishMarkerChanges(nextBodyHtml, editor);
            }
            return nextBodyHtml;
          },
          focusAtStart: () => {
            const editor = editorRef.current;

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
          },
          getBodyHtml: () => draftRef.current?.getBodyHtml() ?? bodyHtml,
        }),
        [bodyHtml, publishMarkerChanges],
      );

      const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
        if (!(event.metaKey || event.ctrlKey)) {
          return;
        }

        const key = event.key.toLowerCase();

        if (key === "b" || key === "i") {
          event.preventDefault();
          document.execCommand(key === "b" ? "bold" : "italic");
          recordEditorInput();
        }
      };

      const handlePaste = (event: ReactClipboardEvent<HTMLDivElement>) => {
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

        recordEditorInput();
      };

      return (
        <>
          <div
            id="desktop-compose-body"
            ref={editorRef}
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
            onInput={recordEditorInput}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            spellCheck
            className={className}
          />
          {showQuoteDisclosure && hasQuote ? (
            <button
              type="button"
              aria-expanded={quoteExpanded}
              aria-controls="desktop-compose-body"
              onClick={() => setQuoteExpanded((expanded) => !expanded)}
              className="mt-3 inline-flex items-center gap-1.5 rounded-md px-1 py-1 text-[0.78rem] text-[var(--workspace-text-faint)] transition-colors duration-150 hover:text-[var(--workspace-text-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--workspace-card)]"
            >
              <span aria-hidden="true">{quoteExpanded ? "⌄" : "›"}</span>
              {quoteExpanded ? "Hide quoted content" : "Show quoted content"}
            </button>
          ) : null}
        </>
      );
    },
  ),
);
