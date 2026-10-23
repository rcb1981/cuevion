import React from "react";
import { trustedMicrosoftAttachment, type MicrosoftAttachmentMetadata } from "../../lib/microsoftAttachmentReadApi";

// Match the reference mailbox cards without changing its protected components.
export function microsoftAttachmentName(value: string): string {
  return value.replace(/\\/g, "/").split("/").pop()?.replace(/[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]/g, "_").trim().slice(0, 256) || "Attachment";
}

export function microsoftAttachmentPresentation(item: MicrosoftAttachmentMetadata) {
  const name = microsoftAttachmentName(item.name);
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  const mime = item.contentType.toLowerCase();
  let label = "File", icon = "FILE";
  if (item.kind === "item") { label = "Outlook item"; icon = "MAIL"; }
  else if (item.kind === "reference") { label = "Linked file"; icon = "LINK"; }
  else if (mime.startsWith("application/pdf") || ext === "pdf") { label = "PDF"; icon = "PDF"; }
  else if (["txt", "md", "doc", "docx", "rtf"].includes(ext) || mime.startsWith("text/") || mime.includes("document")) { label = "Text"; icon = "TXT"; }
  else if (["jpg", "jpeg", "png", "gif", "webp", "heic"].includes(ext) || mime.startsWith("image/")) { label = "Image"; icon = "IMG"; }
  else if (["csv", "xlsx", "xls"].includes(ext) || /sheet|excel|csv/.test(mime)) { label = "Sheet"; icon = "XLS"; }
  else if (["wav", "mp3", "aiff", "m4a"].includes(ext) || mime.startsWith("audio/")) { label = "Audio"; icon = "AUD"; }
  else if (["mp4", "mov", "webm"].includes(ext) || mime.startsWith("video/")) { label = "Video"; icon = "VID"; }
  else if (["zip", "rar"].includes(ext) || mime.includes("zip")) { label = "Archive"; icon = "ZIP"; }
  const size = item.size >= 1024 * 1024 ? `${(item.size / (1024 * 1024)).toFixed(1)} MB`
    : item.size >= 1024 ? `${Math.round(item.size / 1024)} KB` : `${item.size} B`;
  return { name, label, icon, size };
}

export function MicrosoftAttachmentList({ attachments = [], busy = false, error, hasMore = false,
  folder, downloadBusy, downloadError, onDownload,
}: {
  attachments?: MicrosoftAttachmentMetadata[];
  busy?: boolean;
  error?: string | null;
  hasMore?: boolean;
  folder: string;
  downloadBusy?: string | null;
  downloadError?: string | null;
  onDownload: (attachment: MicrosoftAttachmentMetadata) => void;
}) {
  const invalid = attachments.length > 100 || !attachments.every(trustedMicrosoftAttachment) ||
    new Set(attachments.map(item => item.id)).size !== attachments.length;
  const ordinary = invalid ? [] : attachments.filter(item => !item.isInline && !item.contentId);
  if (!ordinary.length && !busy && !error && !invalid && !hasMore && !downloadError) return null;
  return (
    <section aria-label="Microsoft message attachments" aria-busy={busy} className="mt-4">
      <h3 className="mb-3 text-sm font-semibold text-[var(--workspace-text)]">Attachments</h3>
      {busy && <p role="status" className="text-xs text-[var(--workspace-text-muted)]">Loading attachment details…</p>}
      {(error || invalid) && <p role="alert" className="text-sm text-[var(--workspace-text-muted)]">Attachment details could not be loaded.</p>}
      {!busy && !error && !invalid && (
        <div className="flex flex-wrap gap-3">
          {ordinary.map(item => {
            const { name, label, icon, size } = microsoftAttachmentPresentation(item);
            const reason = folder === "Junk" ? "Download disabled for junk mail"
              : item.kind !== "file" ? "Download not supported for this attachment type"
              : item.size > 2 * 1024 * 1024 ? "Over 2 MB download limit" : null;
            return (
              <div key={item.id} className="min-w-0 max-w-full">
                <button type="button" title={reason ?? name}
                  aria-label={`Download ${name}`} disabled={Boolean(reason) || Boolean(downloadBusy)}
                  onClick={() => { if (!reason && !downloadBusy) onDownload(item); }}
                  className="flex min-w-[168px] max-w-full items-center gap-3 rounded-[18px] border border-[var(--workspace-border)] bg-[var(--workspace-card-subtle)] px-3.5 py-3 text-left transition-[background-color,border-color,box-shadow,transform] duration-150 hover:border-[var(--workspace-border-hover)] hover:bg-[var(--workspace-hover-surface)] hover:shadow-[0_8px_18px_rgba(48,38,29,0.08)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)] disabled:cursor-not-allowed disabled:opacity-50">
                  <span aria-hidden="true" className="inline-flex h-9 min-w-[2.75rem] items-center justify-center rounded-[12px] border border-[color:rgba(120,104,89,0.12)] bg-[color:rgba(255,252,247,0.82)] px-2 text-[0.62rem] font-medium uppercase tracking-[0.12em] text-[var(--workspace-text-soft)]">{icon}</span>
                  <span className="min-w-0 flex-1">
                    <span dir="ltr" className="block truncate text-[0.82rem] font-medium leading-6 text-[var(--workspace-text)]">{name}</span>
                    <span className="block truncate text-[0.68rem] uppercase tracking-[0.12em] text-[var(--workspace-text-faint)]">{label} • {size}</span>
                    {downloadBusy === item.id && <span role="status" className="text-xs">Downloading…</span>}
                  </span>
                </button>
                {reason && <p className="mt-1 text-xs text-[var(--workspace-text-muted)]">{reason}</p>}
              </div>
            );
          })}
        </div>
      )}
      <p className="mt-2 text-xs text-[var(--workspace-text-muted)]">{folder === "Junk" ? "Attachments are listed for reference. Downloads are disabled for safety."
        : "Read-only downloads of file attachments up to 2 MB. No messages are changed."}</p>
      {hasMore && <p role="status" className="mt-2 text-xs text-[var(--workspace-text-muted)]">Only the first 100 attachments are loaded. Open Outlook to see any remaining attachments.</p>}
      {downloadError && <p role="alert" className="mt-2 text-sm text-[var(--workspace-text-muted)]">{downloadError}</p>}
    </section>
  );
}
