import React, { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import type { LiveInboxMessageSnapshot, MicrosoftAttachmentMetadata } from "../../lib/inboxConnectionApi";
import { MicrosoftInertHtmlStage } from "./MicrosoftInertHtmlStage";
import { MicrosoftLinkifiedText } from "./MicrosoftLinkifiedText";
import { MicrosoftLearningControls } from "./MicrosoftLearningControls";
import type { MicrosoftLearningRule } from "./microsoftLearningRules";

export function MicrosoftFullMessageModal({
  message, mailboxId, folder, attachments, attachmentsBusy, learningRule,
  learningDisabled = false, canShowLess = false, onSaveLearning, onClose,
}: {
  message: LiveInboxMessageSnapshot;
  mailboxId: string;
  folder: "Inbox" | "Filtered" | "Sent" | "Archive" | "Drafts" | "Deleted" | "Junk";
  attachments?: MicrosoftAttachmentMetadata[];
  attachmentsBusy?: boolean;
  learningRule?: MicrosoftLearningRule;
  learningDisabled?: boolean;
  canShowLess?: boolean;
  onSaveLearning?: (rule: MicrosoftLearningRule) => boolean;
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    closeRef.current?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return createPortal(
    <div className="fixed inset-0 z-[310] flex items-center justify-center bg-[var(--workspace-modal-scrim)] p-4 backdrop-blur-sm"
      data-microsoft-full-message-backdrop
      onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section role="dialog" aria-modal="true" aria-label={"Full message: " + message.subject}
        className="flex min-h-[420px] min-w-[min(320px,calc(100vw-2rem))] max-h-[calc(100dvh-2rem)] max-w-[calc(100vw-2rem)] resize overflow-hidden rounded-[28px] border border-[var(--workspace-border)] bg-[var(--workspace-modal-bg)] shadow-[0_28px_80px_rgba(20,18,16,0.35)]"
        style={{ width: "min(850px, 56vw)", height: "62dvh" }}>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="flex shrink-0 items-center justify-between gap-3 border-b border-[var(--workspace-border-soft)] px-5 py-4">
            <div className="min-w-0">
              <h2 className="truncate text-base font-semibold text-[var(--workspace-text)]">{message.subject || "(No subject)"}</h2>
              <p className="truncate text-xs text-[var(--workspace-text-muted)]">{message.from}</p>
            </div>
            {(folder === "Inbox" || folder === "Filtered") && onSaveLearning && (
              <MicrosoftLearningControls
                from={message.from}
                rule={learningRule}
                disabled={learningDisabled}
                canShowLess={canShowLess}
                onSave={onSaveLearning}
              />
            )}
            <button type="button" ref={closeRef} onClick={onClose} aria-label="Close full message"
              className="h-8 w-8 shrink-0 rounded-full border border-[var(--workspace-border)] text-[var(--workspace-text)]">×</button>
          </div>
          <div className="min-h-0 flex-1 overflow-auto px-5 py-5 text-[var(--workspace-text)]">
            <p className="mb-4 text-xs text-[var(--workspace-text-muted)]">{new Date(message.createdAt).toLocaleString()}</p>
            {message.bodyHtml && folder !== "Junk" ? (
              <MicrosoftInertHtmlStage
                key={mailboxId + ":" + message.providerMessageId + ":modal"}
                html={message.bodyHtml}
                messageKey={mailboxId + ":" + message.providerMessageId + ":modal"}
                fallbackText={message.body.join("\n\n") || message.snippet}
                mailboxId={mailboxId}
                providerMessageId={message.providerMessageId}
                folder={folder === "Filtered" ? "Inbox" : folder}
                attachments={attachments}
                attachmentsBusy={attachmentsBusy}
              />
            ) : (
              <div className="space-y-4 whitespace-pre-wrap break-words text-sm leading-7">
                {message.body.map((paragraph, index) => (
                  <p key={index}>{folder === "Junk" ? paragraph : <MicrosoftLinkifiedText text={paragraph} />}</p>
                ))}
              </div>
            )}
          </div>
          <span className="sr-only">Resize this window using the bottom-right corner.</span>
        </div>
      </section>
    </div>,
    document.body,
  );
}
