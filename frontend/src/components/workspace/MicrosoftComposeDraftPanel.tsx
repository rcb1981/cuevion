import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  MICROSOFT_COMPOSE_DRAFTS_RELEASED, createMicrosoftComposeDraftRequestId,
  inspectMicrosoftComposeDrafts, loadMicrosoftComposeDraft, saveMicrosoftComposeDraft,
  prepareMicrosoftComposeDraftEdit,
  type MicrosoftComposeDraftEdit, type MicrosoftComposeDraftRecord, type MicrosoftComposeDraftReceipt,
} from "../../lib/microsoftComposeDraftApi";
import {
  prepareMicrosoftDraftFiles, validMicrosoftDraftFileMetadata,
  MICROSOFT_ATTACHMENT_MAX_FILE_BYTES, MICROSOFT_ATTACHMENT_MAX_FILES, MICROSOFT_ATTACHMENT_MAX_TOTAL_BYTES,
} from "../../lib/microsoftDraftAttachmentPolicy";
import { mutateMicrosoftDraftAttachments, inspectMicrosoftDraftAttachmentRequest } from "../../lib/microsoftDraftAttachmentApi";
import { MicrosoftDraftAttachments } from "./MicrosoftDraftAttachments";
import { microsoftAttachmentPresentation } from "./MicrosoftAttachmentList";
import { Glyph } from "./MicrosoftComposeDesignPreview";

type Props = {
  mailboxId: string; mailboxEmail: string; connected: boolean; providerMessageId?: string;
  onClose: () => void; onSaved: (providerMessageId: string) => void; onOpenDrafts: () => void;
  onOperationStart?: (providerMessageId: string | null) => void;
};
type Phase = "loading" | "ready" | "preparing" | "saving" | "uploading" | "checking" | "blocked";
const sameEdit = (a: MicrosoftComposeDraftEdit, b: MicrosoftComposeDraftEdit) => JSON.stringify([a.subject, a.body, a.to, a.cc, a.bcc]) === JSON.stringify([b.subject, b.body, b.to, b.cc, b.bcc]);
const empty: MicrosoftComposeDraftEdit = { subject: "", body: "", to: [], cc: [], bcc: [] };
const inputStyle = "min-w-0 flex-1 bg-transparent py-3 text-sm text-[var(--workspace-text)] placeholder:text-[var(--workspace-text-faint)] outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--workspace-accent-border)] disabled:opacity-50";
const iconStyle = "inline-flex h-9 w-9 items-center justify-center rounded-full text-[var(--workspace-text-soft)] hover:bg-[var(--workspace-hover-surface)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)] disabled:opacity-50";
const buttonStyle = "rounded-full border border-[var(--workspace-border)] px-4 py-2 text-xs font-medium disabled:opacity-50";
const uncertain = "The draft result is uncertain. Check status before creating or saving it again.";
const fileError = "Select at most 10 files, 2 MiB each and 8 MiB in total, with valid names and types.";

/** Separate from outbound sending. The source gate is OFF and Send has no handler. */
export function MicrosoftComposeDraftPanel(props: Props) {
  return MICROSOFT_COMPOSE_DRAFTS_RELEASED
    ? <ComposeDraftSession key={JSON.stringify([props.mailboxId, props.providerMessageId ?? null])} {...props} /> : null;
}

function ComposeDraftSession({ mailboxId, mailboxEmail, connected, providerMessageId, onClose, onSaved, onOpenDrafts, onOperationStart }: Props) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [savedId, setSavedId] = useState<string | null>(providerMessageId ?? null);
  const [record, setRecord] = useState<MicrosoftComposeDraftRecord | null>(null);
  const [edit, setEdit] = useState<MicrosoftComposeDraftEdit>(empty);
  const [to, setTo] = useState("");
  const [cc, setCc] = useState("");
  const [bcc, setBcc] = useState("");
  const [showCc, setShowCc] = useState(false);
  const [showBcc, setShowBcc] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [operations, setOperations] = useState<MicrosoftComposeDraftReceipt[]>([]);
  const [attachmentLocked, setAttachmentLocked] = useState(Boolean(providerMessageId));
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const pending = useRef(false);
  const ready = useRef(false);
  const alive = useRef(false);
  const submission = useRef<string | null>(null);
  const attachmentRequest = useRef<{ requestId: string; providerMessageId: string } | null>(null);
  const attachmentBlocked = useRef(Boolean(providerMessageId));
  const picker = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLElement>(null);
  const toField = useRef<HTMLInputElement>(null);
  const focused = useRef(false);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const busy = ["loading", "preparing", "saving", "uploading", "checking"].includes(phase);
  const locked = !connected || phase !== "ready" || attachmentLocked;
  const proposed = prepareMicrosoftComposeDraftEdit(edit.subject, edit.body, to, cc, bcc);
  const dirty = record !== null && (!proposed || !sameEdit(proposed, record.edit));

  const onAttachmentBlocked = useCallback((blocked: boolean) => {
    attachmentBlocked.current = blocked;
    setAttachmentLocked(blocked);
  }, []);

  const loadFresh = async (id: string, publish: boolean) => {
    setRecord(null); // No stale editable content or attachment inventory during readback.
    setEdit(empty); setTo(""); setCc(""); setBcc("");
    const result = await loadMicrosoftComposeDraft({ mailboxId, providerMessageId: id });
    if (!alive.current) return false;
    if (!result.ok) { setError(result.error.message); setPhase("blocked"); return false; }
    setSavedId(id); setRecord(result.draft); setEdit(result.draft.edit);
    setTo(result.draft.edit.to.join(", ")); setCc(result.draft.edit.cc.join(", ")); setBcc(result.draft.edit.bcc.join(", "));
    setShowCc(result.draft.edit.cc.length > 0); setShowBcc(result.draft.edit.bcc.length > 0);
    ready.current = true; setPhase("ready");
    if (publish) { setNotice("Saved to Microsoft Drafts. Nothing was sent."); onSaved(id); }
    return true;
  };

  // Only read requests are issued by mount/reload and explicit status checks.
  const recover = async (target = savedId) => {
    if (pending.current || !connected) return;
    pending.current = true; ready.current = false;
    setPhase("checking"); setError(null);
    try {
      const status = await inspectMicrosoftComposeDrafts({ mailboxId });
      if (!alive.current) return;
      if (!status.ok) { setPhase("blocked"); setError(status.error.message); return; }
      setOperations(status.operations);
      const current = submission.current ? status.operations.find(r => r.requestId === submission.current) : null;
      if (submission.current && !current) { setPhase("blocked"); setError(uncertain); return; }
      if (current?.blocked || status.operations.some(r => r.blocked && (target ? r.providerMessageId === target : r.action === "create"))) {
        setPhase("blocked"); setError(uncertain); return;
      }
      if (current?.outcome === "confirmed_performed") target = current.providerMessageId;
      if (attachmentRequest.current) {
        const result = await inspectMicrosoftDraftAttachmentRequest({ mailboxId, ...attachmentRequest.current });
        if (!alive.current) return;
        if (!result.ok || result.blocked) {
          setPhase("blocked"); setError(!result.ok ? result.error.message : `Attachment result uncertain: ${result.completed}/${result.count} confirmed. Do not upload again.`); return;
        }
        attachmentRequest.current = null;
      }
      submission.current = null;
      if (target) await loadFresh(target, current?.outcome === "confirmed_performed");
      else { ready.current = true; setPhase("ready"); }
    } catch {
      if (alive.current) { setError(uncertain); setPhase("blocked"); }
    } finally { pending.current = false; }
  };

  useEffect(() => {
    alive.current = true;
    if (connected) void recover(providerMessageId ?? null);
    return () => { alive.current = false; ready.current = false; };
  }, [connected]);

  useEffect(() => {
    if (phase === "ready" && !focused.current && typeof document !== "undefined") {
      toField.current?.focus();
      focused.current = true;
    }
  }, [phase]);

  useEffect(() => {
    if (typeof document === "undefined") return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    toField.current?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !pending.current) { event.preventDefault(); closeRef.current(); }
      if (event.key !== "Tab") return;
      const controls = Array.from(dialog.current?.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled]):not([hidden]), textarea:not([disabled])") ?? []);
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", keydown);
    return () => { document.removeEventListener("keydown", keydown); previous?.focus(); };
  }, []);

  const selectFiles = (incoming: File[]) => {
    if (!connected || !ready.current || pending.current || attachmentBlocked.current) return;
    const next = [...files, ...incoming];
    if (!incoming.length) return;
    const identity = (file: File) => JSON.stringify([file.name, file.size, file.type, file.lastModified ?? 0]);
    if (new Set(next.map(identity)).size !== next.length) {
      setError("This file is already selected. Remove it before adding it again."); return;
    }
    if (next.length > MICROSOFT_ATTACHMENT_MAX_FILES || next.some(f =>
      !Number.isSafeInteger(f.size) || f.size < 0 || f.size > MICROSOFT_ATTACHMENT_MAX_FILE_BYTES ||
      !validMicrosoftDraftFileMetadata(f.name, f.type || "application/octet-stream")) ||
      next.reduce((sum, f) => sum + f.size, 0) > MICROSOFT_ATTACHMENT_MAX_TOTAL_BYTES) {
      setError(fileError); return;
    }
    setFiles(next); setError(null);
  };

  const save = async () => {
    if (!connected || !ready.current || pending.current || attachmentBlocked.current || (savedId && !record)) return;
    if (!proposed) { setError("Check To, Cc and Bcc, the subject and message. Use at most 20 distinct recipients and 32000 message characters."); return; }
    pending.current = true; ready.current = false;
    setPhase("preparing"); setError(null); setNotice(null);
    let prepared;
    try {
      prepared = files.length ? await prepareMicrosoftDraftFiles(files) : [];
    } catch {
      pending.current = false;
      if (alive.current) { ready.current = true; setPhase("ready"); setError(fileError); }
      return;
    }
    try {
      if (!alive.current) return;
      const requestId = createMicrosoftComposeDraftRequestId();
      submission.current = requestId;
      setPhase("saving");
      onOperationStart?.(savedId);
      const result = await saveMicrosoftComposeDraft({ mailboxId, requestId, edit: proposed,
        ...(savedId && record ? { action: "update", providerMessageId: savedId, etag: record.etag } : { action: "create" }) });
      if (!alive.current) return;
      if (!result.ok) { setPhase("blocked"); setError(result.error.message); return; }
      setOperations(current => [...current.filter(r => r.requestId !== requestId), result.receipt]);
      if (result.receipt.outcome !== "confirmed_performed" || !result.receipt.providerMessageId) {
        setPhase("blocked"); setError(result.receipt.blocked ? uncertain : "Microsoft confirmed that no draft was changed. Check status before choosing a new operation."); return;
      }
      const id = result.receipt.providerMessageId;
      setSavedId(id); setRecord(null);
      if (prepared.length) {
        const loaded = await loadMicrosoftComposeDraft({ mailboxId, providerMessageId: id });
        if (!alive.current) return;
        if (!loaded.ok || !sameEdit(loaded.draft.edit, proposed)) {
          setPhase("blocked"); setError("The saved draft changed before upload. Reopen it before selecting attachments again."); return;
        }
        const uploadId = createMicrosoftComposeDraftRequestId();
        attachmentRequest.current = { requestId: uploadId, providerMessageId: id };
        setFiles([]); setPhase("uploading");
        const uploaded = await mutateMicrosoftDraftAttachments({ action: "add", mailboxId, providerMessageId: id,
          requestId: uploadId, snapshotHash: loaded.draft.snapshotHash, files: prepared });
        if (!alive.current) return;
        if (!uploaded.ok || uploaded.outcome !== "confirmed_performed") {
          setPhase("blocked"); setError(!uploaded.ok ? uploaded.error.message : `Attachment result uncertain: ${uploaded.completed}/${uploaded.count} confirmed. Do not upload again.`); return;
        }
        attachmentRequest.current = null;
      }
      submission.current = null;
      await loadFresh(id, true);
    } catch {
      if (alive.current) { setPhase("blocked"); setError(uncertain); }
    } finally { pending.current = false; }
  };

  const attachmentChanged = () => {
    if (!savedId || pending.current) return;
    pending.current = true; ready.current = false; setPhase("loading");
    void loadFresh(savedId, true).catch(() => {
      if (alive.current) { setPhase("blocked"); setError(uncertain); }
    }).finally(() => { pending.current = false; });
  };

  return (
    <div className="fixed inset-0 z-[350] flex items-center justify-center bg-[var(--workspace-modal-scrim)] p-3 backdrop-blur-[2px] sm:p-6">
      <section ref={dialog} role="dialog" aria-modal="true" aria-label="Microsoft draft composer" aria-busy={busy}
        className="flex h-[min(760px,calc(100dvh-24px))] w-full max-w-[810px] flex-col overflow-hidden rounded-[24px] border border-[var(--workspace-modal-border-strong)] bg-[var(--workspace-modal-bg)] text-[var(--workspace-text)] shadow-[0_30px_90px_rgba(0,0,0,0.30)]"
        onDragOver={event => { event.preventDefault(); if (!locked) setDragging(true); }} onDragLeave={() => setDragging(false)}
        onDrop={event => { event.preventDefault(); setDragging(false); selectFiles(Array.from(event.dataTransfer.files)); }}>
        <header className="flex shrink-0 items-center justify-between gap-4 border-b border-[var(--workspace-border-soft)] px-5 py-4 sm:px-6">
          <div><h2 className="text-[1.1rem] font-semibold tracking-tight">{savedId ? "Edit draft" : "New message"}</h2>
            <p className="mt-1 text-xs text-[var(--workspace-text-muted)]">Save to Microsoft Drafts. Sending is not enabled.</p></div>
          <button type="button" aria-label="Close Microsoft composer" className={iconStyle} disabled={busy} onClick={onClose}><Glyph name="close" /></button>
        </header>
        <div className={`min-h-0 flex-1 overflow-y-auto px-5 sm:px-6 ${dragging ? "ring-2 ring-inset ring-[var(--workspace-accent-border)]" : ""}`}>
          <div className="flex min-h-[44px] items-center gap-3 border-b border-[var(--workspace-border-soft)] py-2 text-sm"><span className="w-14 shrink-0 text-[var(--workspace-text-faint)]">From</span><span className="min-w-0 truncate">{mailboxEmail}</span></div>
          <div className="flex min-h-[48px] items-center gap-2 border-b border-[var(--workspace-border-soft)] py-1">
            <label htmlFor="ms-compose-to" className="w-14 shrink-0 text-sm text-[var(--workspace-text-faint)]">To</label>
            <input ref={toField} id="ms-compose-to" aria-label="Compose To" inputMode="email" autoComplete="off" className={inputStyle} disabled={locked} value={to} onChange={e => setTo(e.target.value)} placeholder="Recipients" />
            <button type="button" aria-expanded={showCc} className={iconStyle} onClick={() => setShowCc(v => !v)}>Cc</button>
            <button type="button" aria-expanded={showBcc} className={iconStyle} onClick={() => setShowBcc(v => !v)}>Bcc</button>
          </div>
          {showCc && <div className="flex min-h-[48px] items-center gap-2 border-b border-[var(--workspace-border-soft)] py-1"><label htmlFor="ms-compose-cc" className="w-14 shrink-0 text-sm text-[var(--workspace-text-faint)]">Cc</label><input id="ms-compose-cc" aria-label="Compose Cc" inputMode="email" autoComplete="off" className={inputStyle} disabled={locked} value={cc} onChange={e => setCc(e.target.value)} /></div>}
          {showBcc && <div className="flex min-h-[48px] items-center gap-2 border-b border-[var(--workspace-border-soft)] py-1"><label htmlFor="ms-compose-bcc" className="w-14 shrink-0 text-sm text-[var(--workspace-text-faint)]">Bcc</label><input id="ms-compose-bcc" aria-label="Compose Bcc" inputMode="email" autoComplete="off" className={inputStyle} disabled={locked} value={bcc} onChange={e => setBcc(e.target.value)} /></div>}
          <div className="border-b border-[var(--workspace-border-soft)] py-1"><label htmlFor="ms-compose-subject" className="sr-only">Subject</label><input id="ms-compose-subject" aria-label="Compose subject" autoComplete="off" className={"w-full " + inputStyle} disabled={locked} value={edit.subject} maxLength={250} onChange={e => setEdit({ ...edit, subject: e.target.value })} placeholder="Subject" /></div>
          <label htmlFor="ms-compose-body" className="sr-only">Message</label><textarea id="ms-compose-body" aria-label="Compose message body" className="min-h-[230px] w-full resize-none bg-transparent py-5 text-[0.92rem] leading-7 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--workspace-accent-border)] disabled:opacity-50" disabled={locked} value={edit.body} maxLength={32000} onChange={e => setEdit({ ...edit, body: e.target.value })} placeholder="Write your message…" spellCheck />
          <input ref={picker} type="file" multiple hidden aria-label="Choose Compose attachments" disabled={locked} onChange={event => { selectFiles(Array.from(event.currentTarget.files ?? [])); event.currentTarget.value = ""; }} />
          <p className="text-xs text-[var(--workspace-text-muted)]">Plain text. Drop up to 10 files here: 2 MiB per file, 8 MiB total. Selected files are uploaded when you save.</p>
          {!!files.length && <div aria-label="Local Compose attachments" className="my-3 flex flex-wrap gap-3">{files.map((file, index) => {
            const p = microsoftAttachmentPresentation({ id: String(index), name: file.name, size: file.size, contentType: file.type || "application/octet-stream", kind: "file", isInline: false });
            return <div key={index} className="group relative flex min-w-[168px] max-w-full items-center gap-3 rounded-[18px] border border-[var(--workspace-border)] bg-[var(--workspace-card-subtle)] py-3 pl-3.5 pr-11">
              <span aria-hidden="true" className="inline-flex h-9 min-w-[2.75rem] items-center justify-center rounded-[12px] border border-[color:rgba(120,104,89,0.12)] bg-[color:rgba(255,252,247,0.82)] px-2 text-[0.62rem] font-medium uppercase tracking-[0.12em] text-[var(--workspace-text-soft)]">{p.icon}</span>
              <span className="min-w-0"><span dir="ltr" className="block truncate text-[0.82rem] font-medium leading-6">{p.name}</span><span className="block text-[0.68rem] uppercase tracking-[0.12em] text-[var(--workspace-text-faint)]">{p.label} • {p.size} • Not uploaded</span></span>
              <button type="button" disabled={locked} aria-label={`Remove local ${p.name}`} className="absolute right-2 top-2 inline-flex h-6 w-6 items-center justify-center rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] disabled:opacity-50" onClick={() => { if (ready.current && !pending.current) setFiles(current => current.filter((_, i) => i !== index)); }}>×</button>
            </div>;
          })}</div>}
          {savedId && record && <div className="my-4"><p className="mb-2 text-xs font-medium">Saved Graph attachments</p><MicrosoftDraftAttachments
            key={record.etag} mailboxId={mailboxId} providerMessageId={savedId} disabled={busy || !connected} editorPending={pending}
            hasUnsavedChanges={dirty} hideSelection onBlockedChange={onAttachmentBlocked} onConfirmed={attachmentChanged}
            onOperationStart={() => onOperationStart?.(savedId)} /></div>}
          <div aria-live="polite" className="space-y-2 py-3">
            {busy && <p role="status" className="text-sm">{phase === "preparing" ? "Preparing attachments…" : phase === "saving" ? "Saving draft and verifying…" : phase === "uploading" ? "Uploading attachments and verifying…" : "Checking draft status…"}</p>}
            {error && <p role="alert" className="text-sm">{error}</p>}{notice && <p role="status" className="text-sm">{notice}</p>}
            {phase === "blocked" && <button type="button" className={buttonStyle} disabled={busy || !connected} onClick={() => { void recover(); }}>Check draft status</button>}
          </div>
          {!savedId && operations.filter(r => r.action === "create" && r.outcome === "confirmed_performed" && r.providerMessageId).slice(-3).map(r => <button key={r.requestId} type="button" className={buttonStyle} disabled={busy || phase === "blocked"} onClick={() => { if (!pending.current) void recover(r.providerMessageId); }}>Open saved draft</button>)}
        </div>
        <footer className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-[var(--workspace-border-soft)] px-5 py-4 sm:px-6">
          <div className="flex items-center gap-2">
            <button type="button" disabled title="Sending will be handled in a separate phase" className="inline-flex h-9 items-center gap-2 rounded-full border border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] px-5 text-xs font-semibold text-[var(--workspace-accent-text)] disabled:cursor-not-allowed disabled:opacity-55"><Glyph name="send" />Send</button>
            <button type="button" aria-label="Add Compose attachments" className={iconStyle} disabled={locked} onClick={() => picker.current?.click()}><Glyph name="attach" /></button>
            <button type="button" className={buttonStyle} disabled={locked} onClick={() => { void save(); }}>Save draft</button>
          </div>
          <button type="button" className={buttonStyle} disabled={busy} onClick={onOpenDrafts}>View Drafts</button>
        </footer>
      </section>
    </div>
  );
}
