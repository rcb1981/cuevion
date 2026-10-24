import React, { useCallback, useEffect, useRef, useState } from "react";
import { updateMicrosoftEditableDraft, validMicrosoftDraftEdit, type MicrosoftDraftEdit } from "../../lib/inboxConnectionApi";

import { MicrosoftDraftAttachments } from "./MicrosoftDraftAttachments";
import { MICROSOFT_DRAFT_ATTACHMENTS_RELEASED } from "../../lib/microsoftDraftAttachmentPolicy";

/** Microsoft only. Exposed only behind a separate source-level release gate. */
export function MicrosoftDraftEditor({
  mailboxId, providerMessageId, onClose, onSaved, onAttachmentStart,
}: {
  mailboxId: string;
  providerMessageId: string;
  onClose: () => void;
  onSaved: () => void;
  onAttachmentStart?: () => void;
}) {
  const [edit, setEdit] = useState<MicrosoftDraftEdit | null>(null);
  const [etag, setEtag] = useState<string | null>(null);
  const [to, setTo] = useState("");
  const [cc, setCc] = useState("");
  const [busy, setBusy] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [attachmentLocked, setAttachmentLocked] = useState(MICROSOFT_DRAFT_ATTACHMENTS_RELEASED);
  const attachmentBlocked = useRef(MICROSOFT_DRAFT_ATTACHMENTS_RELEASED);
  const onAttachmentBlocked = useCallback((value: boolean) => {
    attachmentBlocked.current = value;
    setAttachmentLocked(value);
  }, []);
  const pending = useRef(false);
  const ticket = useRef(0);
  const original = useRef<MicrosoftDraftEdit | null>(null);

  useEffect(() => {
    const next = ++ticket.current;
    setError(null);
    setEdit(null);
    setEtag(null);
    setBusy(true);
    void updateMicrosoftEditableDraft({ action: "load", mailboxId, providerMessageId }).then(result => {
      if (ticket.current !== next) return;
      if (!result.ok) {
        setError(result.error.message);
        return;
      }
      setEdit(result.edit);
      original.current = result.edit;
      setTo(result.edit.to.join(", "));
      setCc(result.edit.cc.join(", "));
      setEtag(result.etag);
    }).catch(() => {
      if (ticket.current === next) setError("The draft could not be loaded.");
    }).finally(() => {
      if (ticket.current === next) setBusy(false);
    });
    return () => { ticket.current++; };
  }, [mailboxId, providerMessageId]);

  const save = async () => {
    if (!edit || !etag || pending.current || attempted || attachmentBlocked.current) return;
    const split = (s: string) => s.split(",").map(v => v.trim().toLowerCase()).filter(Boolean);
    const proposed = { ...edit, to:split(to), cc:split(cc) };
    if (!validMicrosoftDraftEdit(proposed)) {
      setError("Check your draft recipients, subject and message before saving.");
      return;
    }
    const next = ++ticket.current;
    pending.current = true;
    setAttempted(true); // Once submitted, never blindly retry an uncertain Graph PATCH.
    setBusy(true);
    setError(null);
    try {
      const result = await updateMicrosoftEditableDraft({
        action: "save", mailboxId, providerMessageId, etag,
        edit: proposed,
      });
      if (ticket.current !== next) return;
      if (!result.ok) { setError(result.error.message); return; }
      onSaved();
    } catch {
      if (ticket.current === next) setError("Save status is uncertain. Reopen Drafts before trying again.");
    } finally {
      pending.current = false;
      if (ticket.current === next) setBusy(false);
    }
  };
  const hasUnsavedChanges = Boolean(edit && original.current && (
    JSON.stringify(edit) !== JSON.stringify(original.current) ||
    to !== original.current.to.join(", ") || cc !== original.current.cc.join(", ")
  ));

  return (
    <section aria-label="Edit Microsoft draft" className="h-full overflow-y-auto p-5 sm:p-8">
      <div className="mx-auto max-w-2xl space-y-4">
        <header className="flex items-center justify-between gap-3">
          <h2 className="text-xl font-semibold">Edit draft</h2>
          <button type="button" disabled={busy} onClick={onClose} className="rounded-xl border border-[var(--workspace-border)] px-4 py-2 text-sm disabled:opacity-50">Close</button>
        </header>
        <p className="text-xs text-[var(--workspace-text-muted)]">Only supported existing plain-text drafts can be edited. Saving preserves existing attachments and never sends mail.</p>
        {edit && <>
          <label className="block space-y-1 text-sm"><span>To</span>
            <input aria-label="Draft recipients" value={to} onChange={e => setTo(e.target.value)} disabled={attempted || attachmentLocked || busy} className="w-full rounded-xl border border-[var(--workspace-border)] bg-transparent p-3 disabled:opacity-50" />
          </label>
          <label className="block space-y-1 text-sm"><span>Cc</span>
            <input aria-label="Draft carbon copy recipients" value={cc} onChange={e => setCc(e.target.value)} disabled={attempted || attachmentLocked || busy} className="w-full rounded-xl border border-[var(--workspace-border)] bg-transparent p-3 disabled:opacity-50" />
          </label>
          <label className="block space-y-1 text-sm"><span>Subject</span>
            <input aria-label="Draft subject" value={edit.subject} maxLength={250} onChange={e => setEdit({ ...edit, subject:e.target.value })} disabled={attempted || attachmentLocked || busy} className="w-full rounded-xl border border-[var(--workspace-border)] bg-transparent p-3 disabled:opacity-50" />
          </label>
          <label className="block space-y-1 text-sm"><span>Message</span>
            <textarea aria-label="Draft message" rows={12} maxLength={32000} value={edit.body} onChange={e => setEdit({ ...edit, body:e.target.value })} disabled={attempted || attachmentLocked || busy} className="w-full rounded-xl border border-[var(--workspace-border)] bg-transparent p-3 disabled:opacity-50" />
          </label>
          <button type="button" disabled={busy || attempted || attachmentLocked} onClick={() => {void save();}} className="rounded-xl border border-[var(--workspace-border)] px-5 py-2.5 text-sm font-semibold disabled:opacity-50">
            {busy ? "Saving…" : attempted ? "Reopen to verify save" : "Save draft"}
          </button>
        </>}
        <MicrosoftDraftAttachments mailboxId={mailboxId} providerMessageId={providerMessageId}
          disabled={busy || attempted} hasUnsavedChanges={hasUnsavedChanges} onOperationStart={onAttachmentStart} editorPending={pending} onBlockedChange={onAttachmentBlocked} onConfirmed={onSaved} />
        {busy && !edit && <p role="status">Loading draft…</p>}
        {error && <p role="alert" className="text-sm">{error}</p>}
      </div>
    </section>
  );
}
