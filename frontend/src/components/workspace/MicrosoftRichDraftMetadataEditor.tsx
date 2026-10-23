import React, { useEffect, useRef, useState } from "react";
import {
  updateMicrosoftRichDraftMetadata,
  validMicrosoftRichDraftMetadata,
  type MicrosoftRichDraftMetadata,
} from "../../lib/inboxConnectionApi";

/** Microsoft-only rich Draft header editor. HTML remains read-only at all times. */
export function MicrosoftRichDraftMetadataEditor({
  mailboxId, providerMessageId, onClose, onSaved,
}: {
  mailboxId: string;
  providerMessageId: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [metadata, setMetadata] = useState<MicrosoftRichDraftMetadata | null>(null);
  const [bodyPreview, setBodyPreview] = useState("");
  const [etag, setEtag] = useState<string | null>(null);
  const [to, setTo] = useState("");
  const [cc, setCc] = useState("");
  const [busy, setBusy] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef(false);
  const ticket = useRef(0);

  useEffect(() => {
    const next = ++ticket.current;
    setError(null);
    setMetadata(null);
    setEtag(null);
    setBusy(true);
    void updateMicrosoftRichDraftMetadata({ action: "load_rich", mailboxId, providerMessageId })
      .then(result => {
        if (ticket.current !== next) return;
        if (!result.ok) { setError(result.error.message); return; }
        setMetadata(result.metadata);
        setBodyPreview(result.bodyPreview);
        setTo(result.metadata.to.join(", "));
        setCc(result.metadata.cc.join(", "));
        setEtag(result.etag);
      }).catch(() => {
        if (ticket.current === next) setError("The HTML draft could not be loaded.");
      }).finally(() => { if (ticket.current === next) setBusy(false); });
    return () => { ticket.current += 1; };
  }, [mailboxId, providerMessageId]);

  const save = async () => {
    if (!metadata || !etag || attempted || pending.current) return;
    const split = (value: string) => value.split(",")
      .map(entry => entry.trim().toLowerCase()).filter(Boolean);
    const proposed = { ...metadata, to: split(to), cc: split(cc) };
    if (!validMicrosoftRichDraftMetadata(proposed)) {
      setError("Check your recipients and subject before saving.");
      return;
    }
    const next = ++ticket.current;
    pending.current = true;
    setAttempted(true); // Never retry a potentially completed Graph PATCH.
    setBusy(true);
    setError(null);
    try {
      const result = await updateMicrosoftRichDraftMetadata({
        action: "save_rich", mailboxId, providerMessageId, etag, metadata: proposed,
      });
      if (ticket.current !== next) return;
      if (!result.ok) { setError(result.error.message); return; }
      onSaved();
    } catch {
      if (ticket.current === next) {
        setError("Save status is uncertain. Reopen Drafts before trying again.");
      }
    } finally {
      pending.current = false;
      if (ticket.current === next) setBusy(false);
    }
  };

  const fieldStyle = "w-full rounded-xl border border-[var(--workspace-border)] bg-transparent p-3 disabled:opacity-50";
  return (
    <section aria-label="Edit Microsoft HTML draft header" className="h-full overflow-y-auto p-5 sm:p-8">
      <div className="mx-auto max-w-2xl space-y-4">
        <header className="flex items-center justify-between gap-3">
          <h2 className="text-xl font-semibold">Edit draft details</h2>
          <button type="button" disabled={busy} onClick={onClose}
            className="rounded-xl border border-[var(--workspace-border)] px-4 py-2 text-sm disabled:opacity-50">Close</button>
        </header>
        <p className="text-xs text-[var(--workspace-text-muted)]">
          HTML formatting is preserved. Only the subject and recipients can be edited here.
          Drafts with attachments or Bcc recipients are not supported. Saving never sends mail.
        </p>
        {metadata && <>
          <label className="block space-y-1 text-sm"><span>To</span>
            <input aria-label="HTML draft recipients" value={to} onChange={e => setTo(e.target.value)}
              disabled={attempted} className={fieldStyle} />
          </label>
          <label className="block space-y-1 text-sm"><span>Cc</span>
            <input aria-label="HTML draft carbon copy recipients" value={cc} onChange={e => setCc(e.target.value)}
              disabled={attempted} className={fieldStyle} />
          </label>
          <label className="block space-y-1 text-sm"><span>Subject</span>
            <input aria-label="HTML draft subject" value={metadata.subject} maxLength={250}
              onChange={e => setMetadata({ ...metadata, subject: e.target.value })}
              disabled={attempted} className={fieldStyle} />
          </label>
          <div className="space-y-1 text-sm">
            <p>Message preview (read-only)</p>
            <div aria-label="Original HTML draft preview" className="min-h-24 whitespace-pre-wrap break-words rounded-xl border border-[var(--workspace-border)] p-3 text-[var(--workspace-text-muted)]">
              {bodyPreview || "No readable text in this HTML draft."}
            </div>
          </div>
          <button type="button" disabled={busy || attempted} onClick={() => { void save(); }}
            className="rounded-xl border border-[var(--workspace-border)] px-5 py-2.5 text-sm font-semibold disabled:opacity-50">
            {busy ? "Saving…" : attempted ? "Reopen to verify save" : "Save draft details"}
          </button>
        </>}
        {busy && !metadata && <p role="status">Loading draft…</p>}
        {error && <p role="alert" className="text-sm">{error}</p>}
      </div>
    </section>
  );
}
