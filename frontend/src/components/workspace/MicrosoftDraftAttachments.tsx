import React, { useEffect, useRef, useState } from "react";
import {
  inspectMicrosoftDraftAttachmentStatus, inspectMicrosoftDraftAttachmentRequest,
  loadMicrosoftDraftAttachments, mutateMicrosoftDraftAttachments, newMicrosoftAttachmentRequestId,
  type MicrosoftDraftAttachmentSnapshot, type MicrosoftDraftAttachmentReceipt,
} from "../../lib/microsoftDraftAttachmentApi";
import {
  MICROSOFT_DRAFT_ATTACHMENTS_RELEASED, MICROSOFT_ATTACHMENT_MAX_FILES,
  MICROSOFT_ATTACHMENT_MAX_FILE_BYTES, MICROSOFT_ATTACHMENT_MAX_TOTAL_BYTES,
  prepareMicrosoftDraftFiles, validMicrosoftDraftFileMetadata,
} from "../../lib/microsoftDraftAttachmentPolicy";
import { microsoftAttachmentPresentation } from "./MicrosoftAttachmentList";

type Props = {
  mailboxId: string; providerMessageId: string; disabled: boolean;
  onBlockedChange: (blocked: boolean) => void;
  onConfirmed: () => void;
  onOperationStart?: () => void;
  hasUnsavedChanges?: boolean;
  hideSelection?: boolean;
  editorPending: React.MutableRefObject<boolean>;
};
type Phase = "loading" | "ready" | "preparing" | "uploading" | "removing" | "checking" | "blocked";
const uncertain = "The attachment outcome is uncertain. Do not repeat it. Check status or verify the draft in Outlook.";
const selectionError = "Select at most 10 files, 2 MiB each and 8 MiB in total, with valid file names and types.";
const actionStyle = "rounded-xl border border-[var(--workspace-border)] px-4 py-2 text-sm disabled:opacity-50";
const removeStyle = "absolute right-2 top-2 inline-flex h-6 w-6 items-center justify-center rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] text-[0.78rem] leading-none text-[var(--workspace-text-faint)] transition hover:border-[var(--workspace-border-hover)] hover:text-[var(--workspace-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)] disabled:opacity-50 sm:opacity-0 sm:group-hover:opacity-100 sm:focus-visible:opacity-100";
const cardStyle = "group relative flex min-w-[168px] max-w-full items-center gap-3 rounded-[18px] border border-[var(--workspace-border)] bg-[var(--workspace-card-subtle)] pl-3.5 pr-11 py-3 text-left";

/** Key the session so late responses can never change another draft's interface. */
export function MicrosoftDraftAttachments(props: Props) {
  return MICROSOFT_DRAFT_ATTACHMENTS_RELEASED
    ? <DraftAttachmentSession key={JSON.stringify([props.mailboxId, props.providerMessageId])} {...props} /> : null;
}

function DraftAttachmentSession({ mailboxId, providerMessageId, disabled, onBlockedChange, onConfirmed, editorPending, onOperationStart, hasUnsavedChanges, hideSelection }: Props) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [snapshot, setSnapshot] = useState<MicrosoftDraftAttachmentSnapshot | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [receipts, setReceipts] = useState<MicrosoftDraftAttachmentReceipt[]>([]);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const alive = useRef(false);
  const pending = useRef(false);
  const ready = useRef(false);
  const submitted = useRef<string | null>(null);
  const context = { mailboxId, providerMessageId };
  const busy = ["loading", "preparing", "uploading", "removing", "checking"].includes(phase);
  const locked = disabled || Boolean(hasUnsavedChanges) || phase !== "ready";

  useEffect(() => { onBlockedChange(phase !== "ready"); }, [phase, onBlockedChange]);

  // Reads only. Never poll, resume a selection or retry a mutation after reload.
  const recover = async () => {
    if (pending.current) return;
    pending.current = true;
    onBlockedChange(true);
    ready.current = false;
    setPhase("checking");
    setSnapshot(null);
    setError(null);
    try {
      if (submitted.current) {
        const result = await inspectMicrosoftDraftAttachmentRequest({ ...context, requestId: submitted.current });
        if (!alive.current) return;
        if (!result.ok) { setPhase("blocked"); setError(`${result.error.message} ${uncertain}`); return; }
        setReceipts([result]);
        if (result.blocked) { setPhase("blocked"); setError(uncertain); return; }
        if (result.outcome === "confirmed_performed") { onConfirmed(); return; }
        submitted.current = null;
      }
      const status = await inspectMicrosoftDraftAttachmentStatus(context);
      if (!alive.current) return;
      if (!status.ok) { setPhase("blocked"); setError(status.error.message); return; }
      setReceipts(status.requests);
      if (status.requests.some(r => r.blocked)) { setPhase("blocked"); setError(uncertain); return; }
      const loaded = await loadMicrosoftDraftAttachments(context);
      if (!alive.current) return;
      if (!loaded.ok) { setPhase("blocked"); setError(loaded.error.message); return; }
      setSnapshot(loaded);
      ready.current = true;
      setPhase("ready");
    } catch {
      if (alive.current) { setPhase("blocked"); setError(uncertain); }
    } finally { pending.current = false; }
  };

  useEffect(() => {
    alive.current = true;
    void recover();
    return () => { alive.current = false; ready.current = false; };
  }, []);

  const select = (incoming: File[]) => {
    if (disabled || hasUnsavedChanges || editorPending.current || !ready.current || pending.current) return;
    const next = [...files, ...incoming];
    if (!incoming.length) return;
    if (next.length > MICROSOFT_ATTACHMENT_MAX_FILES || next.some(f =>
      !Number.isSafeInteger(f.size) || f.size < 0 || f.size > MICROSOFT_ATTACHMENT_MAX_FILE_BYTES ||
      !validMicrosoftDraftFileMetadata(f.name, f.type || "application/octet-stream")) ||
      next.reduce((sum, f) => sum + f.size, 0) > MICROSOFT_ATTACHMENT_MAX_TOTAL_BYTES) {
      setError(selectionError); return;
    }
    setFiles(next);
    setError(null);
  };

  const mutate = async (attachmentId?: string) => {
    if (disabled || hasUnsavedChanges || editorPending.current || pending.current || !ready.current || !snapshot || (!attachmentId && !files.length)) return;
    if (attachmentId && !snapshot.attachments.some(a => a.id === attachmentId && a.removable && !a.isInline && !a.contentId && a.kind === "file")) return;
    onBlockedChange(true);
    pending.current = true; // Synchronous barrier covers double clicks and file preparation.
    ready.current = false;
    setError(null);
    setPhase(attachmentId ? "removing" : "preparing");
    let prepared;
    try {
      if (!attachmentId) prepared = await prepareMicrosoftDraftFiles(files);
      if (!alive.current) return;
    } catch {
      if (alive.current) { setError(selectionError); setPhase("ready"); ready.current = true; }
      pending.current = false;
      return;
    }
    try {
      const requestId = newMicrosoftAttachmentRequestId();
      submitted.current = requestId;
      onOperationStart?.();
      setSnapshot(null); // Potentially obsolete inventory must disappear before any write.
      setFiles([]); // File contents exist only in this operation's memory.
      setPhase(attachmentId ? "removing" : "uploading");
      const common = { ...context, requestId, snapshotHash: snapshot.snapshotHash };
      const result = await mutateMicrosoftDraftAttachments(attachmentId
        ? { ...common, action: "remove", attachmentId }
        : { ...common, action: "add", files: prepared! });
      if (!alive.current) return;
      if (!result.ok) { setPhase("blocked"); setError(`${result.error.message} ${uncertain}`); return; }
      setReceipts([result]);
      if (result.blocked) { setPhase("blocked"); setError(uncertain); return; }
      if (result.outcome === "confirmed_performed") { onConfirmed(); return; }
      setPhase("blocked");
      setError("Microsoft confirmed that no attachment was changed. Check status to reload before choosing a new operation.");
    } catch {
      if (alive.current) { setPhase("blocked"); setError(uncertain); }
    } finally { pending.current = false; }
  };

  const ordinary = snapshot?.attachments.filter(a => !a.isInline && !a.contentId) ?? [];
  const protectedCount = (snapshot?.attachments.length ?? 0) - ordinary.length;
  return (
    <section aria-label="Microsoft draft attachments" aria-busy={busy} className="space-y-3 border-t border-[var(--workspace-border-soft)] pt-4"
      onDragOver={event => { event.preventDefault(); if (!locked) setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={event => { event.preventDefault(); setDragging(false); select(Array.from(event.dataTransfer.files)); }}>
      <h3 className="text-sm font-semibold">Attachments</h3>
      <p className="text-xs text-[var(--workspace-text-muted)]">Save text or recipient changes first. Confirmed attachment changes reload this draft.</p>
      <p className="text-xs text-[var(--workspace-text-muted)]">Up to 10 new files, 2 MiB per file and 8 MiB per selection. Inline images are protected.</p>
      {!hideSelection && <>
      <input ref={input} type="file" multiple hidden aria-label="Choose draft attachments" disabled={locked}
        onChange={event => { select(Array.from(event.currentTarget.files ?? [])); event.currentTarget.value = ""; }} />
      {hasUnsavedChanges && <p role="status" className="text-xs text-[var(--workspace-text-muted)]">Save your draft changes before changing attachments.</p>}
      <div className={`rounded-[18px] border border-dashed p-4 ${dragging ? "border-[var(--workspace-accent-border)]" : "border-[var(--workspace-border)]"}`}>
        <button type="button" className={actionStyle} disabled={locked} onClick={() => input.current?.click()}>Add attachments</button>
        <span className="ml-3 text-xs text-[var(--workspace-text-muted)]">or drop files here</span>
      </div>
      </>}
      {!!ordinary.length && <div className="flex flex-wrap gap-3">{ordinary.map(item => {
        const { name, label, icon, size } = microsoftAttachmentPresentation(item);
        return <div key={item.id} className={cardStyle}>
          <span aria-hidden="true" className="inline-flex h-9 min-w-[2.75rem] items-center justify-center rounded-[12px] border border-[color:rgba(120,104,89,0.12)] bg-[color:rgba(255,252,247,0.82)] px-2 text-[0.62rem] font-medium uppercase tracking-[0.12em] text-[var(--workspace-text-soft)]">{icon}</span>
          <span className="min-w-0 flex-1"><span dir="ltr" className="block truncate text-[0.82rem] font-medium leading-6 text-[var(--workspace-text)]">{name}</span><span className="block truncate text-[0.68rem] uppercase tracking-[0.12em] text-[var(--workspace-text-faint)]">{label} • {size}</span></span>
          {item.removable ? <button type="button" aria-label={`Remove ${name}`} disabled={locked} className={removeStyle} onClick={() => { void mutate(item.id); }}>×</button>
            : <span className="text-xs text-[var(--workspace-text-muted)]">Protected</span>}
        </div>;
      })}</div>}
      {protectedCount > 0 && <p className="text-xs text-[var(--workspace-text-muted)]">{protectedCount} inline/CID attachment(s) preserved. Removal is disabled.</p>}
      {!!files.length && <>
        <div aria-label="Selected attachments" className="flex flex-wrap gap-3">{files.map((file, index) => {
          const { name, label, icon, size } = microsoftAttachmentPresentation({ id: String(index), name: file.name, contentType: file.type || "application/octet-stream", size: file.size, kind: "file", isInline: false, contentId: null });
          return <div key={index} className={cardStyle}>
            <span aria-hidden="true" className="inline-flex h-9 min-w-[2.75rem] items-center justify-center rounded-[12px] border border-[color:rgba(120,104,89,0.12)] bg-[color:rgba(255,252,247,0.82)] px-2 text-[0.62rem] font-medium uppercase tracking-[0.12em] text-[var(--workspace-text-soft)]">{icon}</span>
            <span className="min-w-0 flex-1"><span dir="ltr" className="block truncate text-[0.82rem] font-medium leading-6 text-[var(--workspace-text)]">{name}</span><span className="block truncate text-[0.68rem] uppercase tracking-[0.12em] text-[var(--workspace-text-faint)]">{label} • {size} • Not uploaded</span></span>
            <button type="button" aria-label={`Remove selected ${name}`} disabled={locked} className={removeStyle} onClick={() => { if (!pending.current && ready.current && !disabled && !hasUnsavedChanges) setFiles(current => current.filter((_, i) => i !== index)); }}>×</button>
          </div>;
        })}</div>
        <button type="button" disabled={locked} className={actionStyle} onClick={() => { void mutate(); }}>Upload {files.length} attachment(s)</button>
      </>}
      <div aria-live="polite">
        {busy && <p role="status" className="text-sm">{phase === "preparing" ? "Preparing selected files…" : phase === "uploading" ? "Uploading attachments and verifying…" : phase === "removing" ? "Removing attachment and verifying…" : "Checking attachment status…"}</p>}
        {receipts.map(r => <p key={r.requestId} role="status" className="text-xs">{r.outcome === "confirmed_performed" ? "Confirmed" : r.outcome === "confirmed_not_performed" ? "No changes confirmed" : "Uncertain operation"}: {r.completed}/{r.count} attachment(s) confirmed.</p>)}
        {error && <p role="alert" className="text-sm">{error}</p>}
      </div>
      {phase === "blocked" && <button type="button" className={actionStyle} disabled={disabled || busy} onClick={() => { void recover(); }}>Check attachment status</button>}
    </section>
  );
}
