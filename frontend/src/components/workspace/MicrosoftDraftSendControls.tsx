import React, { useEffect, useRef, useState } from "react";
import { Glyph } from "./MicrosoftComposeDesignPreview";
import { createMicrosoftComposeDraftRequestId, type MicrosoftComposeDraftRecord } from "../../lib/microsoftComposeDraftApi";
import { MICROSOFT_DRAFT_SEND_RELEASED, listMicrosoftDraftSendHistory, sendMicrosoftExistingDraft, checkMicrosoftDraftSend, microsoftDraftSendReason, type MicrosoftDraftSendReceipt, type MicrosoftSendHistoryPage } from "../../lib/microsoftDraftSendApi";

type VisibleReceipt = MicrosoftDraftSendReceipt & { sequence?: number; createdAt?: string | null };
const retainMismatch = (old: VisibleReceipt | undefined, next: VisibleReceipt): VisibleReceipt =>
  old?.outcome === "performed_content_mismatch" ? { ...next, ...old } : { ...old, ...next };
const chronological = (rows: VisibleReceipt[]) => [...rows].sort((a, b) => (b.sequence ?? Number.MAX_SAFE_INTEGER) - (a.sequence ?? Number.MAX_SAFE_INTEGER));

type Props = {
  mailboxId: string; providerMessageId: string | null; connected: boolean; record: MicrosoftComposeDraftRecord | null;
  dirty: boolean; selectedFileCount: number; blocked: boolean; pending: React.MutableRefObject<boolean>;
  onLockedChange: (locked: boolean) => void; onOperationStart: () => void; onConfirmed: (id: string) => void;
};
const sendStyle = "inline-flex h-9 items-center gap-2 rounded-full border border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-accent-surface-start),var(--workspace-accent-surface-end))] px-5 text-xs font-semibold text-[var(--workspace-accent-text)] disabled:cursor-not-allowed disabled:opacity-55";
const uncertain = "Send outcome uncertain. Do not send again. Check Sent status.";

export function MicrosoftDraftSendControls(props: Props) {
  return <SendSession key={JSON.stringify([props.mailboxId, props.providerMessageId])} {...props} />;
}

function SendSession({ mailboxId, providerMessageId, connected, record, dirty, selectedFileCount, blocked, pending, onLockedChange, onOperationStart, onConfirmed }: Props) {
  const [attempts, setAttempts] = useState<VisibleReceipt[]>([]);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [busy, setBusy] = useState(true);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(false);
  const ownPending = useRef(false);
  const ownAttempt = useRef<string | null>(null);
  const announced = useRef(false);
  const current = attempts.filter(r => r.providerMessageId === providerMessageId);
  const barrier = current.some(r => r.outcome !== "confirmed_not_started");

  useEffect(() => {
    if (!announced.current && providerMessageId && current.some(r => r.outcome === "confirmed_performed")) {
      announced.current = true;
      onConfirmed(providerMessageId);
    }
  }, [attempts, providerMessageId, onConfirmed]);
  useEffect(() => { onLockedChange(!ready || barrier || busy); }, [ready, barrier, busy, onLockedChange]);
  const acceptHistory = (result: MicrosoftSendHistoryPage) => {
    const rows: VisibleReceipt[] = [...result.entries, ...(result.currentDraft ? [result.currentDraft] : [])].map(e => ({ ...e.receipt, sequence: e.sequence, createdAt: e.createdAt }));
    setAttempts(old => {
      const merged = new Map(old.map(r => [r.attemptId, r]));
      for (const row of rows) merged.set(row.attemptId, retainMismatch(merged.get(row.attemptId), row));
      return chronological([...merged.values()]);
    });
    setNextCursor(result.nextCursor); setReady(true);
  };
  useEffect(() => {
    alive.current = true;
    let cancelled = false;
    setReady(false); setBusy(true);
    void listMicrosoftDraftSendHistory({ mailboxId, providerMessageId }).then(result => {
      if (cancelled) return;
      if (!result.ok) { setError(result.error.message); return; }
      acceptHistory(result); setError(null);
    }).catch(() => { if (!cancelled) setError(uncertain); }).finally(() => { if (!cancelled) setBusy(false); });
    return () => { cancelled = true; alive.current = false; };
  }, [mailboxId, providerMessageId, connected]);

  const merge = (r: MicrosoftDraftSendReceipt) => setAttempts(all => chronological([
    ...all.filter(x => x.attemptId !== r.attemptId), retainMismatch(all.find(x => x.attemptId === r.attemptId), r),
  ]));
  const send = async () => {
    if (!MICROSOFT_DRAFT_SEND_RELEASED || !connected || !ready || busy || ownPending.current || pending.current || ownAttempt.current || barrier || blocked || dirty || selectedFileCount || !record || record.providerMessageId !== providerMessageId) return;
    if (!(record.edit.to.length + record.edit.cc.length + record.edit.bcc.length) || !record.edit.subject.trim() || !record.edit.body.trim()) {
      setError("Save valid recipients, a subject and message before sending."); return;
    }
    ownPending.current = true; pending.current = true; onLockedChange(true); setBusy(true); setError(null);
    try {
      const attemptId = createMicrosoftComposeDraftRequestId();
      ownAttempt.current = attemptId;
      // Show a provisional uncertainty immediately, then replace it only with a validated receipt.
      merge({ attemptId, providerMessageId: record.providerMessageId, outcome: "uncertain", providerAccepted: false, sentCopyConfirmed: false, deliveryConfirmed: false, reason: null });
      onOperationStart();
      const result = await sendMicrosoftExistingDraft({ mailboxId, attemptId, providerMessageId: record.providerMessageId, etag: record.etag, snapshotHash: record.snapshotHash });
      if (!alive.current) return;
      if (!result.ok) { setError(result.error.message); return; }
      merge(result.receipt);
    } catch { if (alive.current) setError(uncertain); }
    finally { ownPending.current = false; pending.current = false; if (alive.current) setBusy(false); }
  };
  const check = async (r: MicrosoftDraftSendReceipt) => {
    if (!connected || ownPending.current || pending.current) return;
    ownPending.current = true; pending.current = true; setBusy(true); setError(null);
    try {
      const result = await checkMicrosoftDraftSend({ mailboxId, attemptId: r.attemptId });
      if (!alive.current) return;
      if (!result.ok || result.receipt.providerMessageId !== r.providerMessageId) { setError(!result.ok ? result.error.message : uncertain); return; }
      merge(result.receipt);
    } catch { if (alive.current) setError(uncertain); }
    finally { ownPending.current = false; pending.current = false; if (alive.current) setBusy(false); }
  };
  const reloadStatus = async (older = false) => {
    if (ownPending.current || pending.current || !connected || older && nextCursor === null) return;
    ownPending.current = true; setBusy(true); setError(null);
    try {
      const result = await listMicrosoftDraftSendHistory({ mailboxId, providerMessageId, before: older ? nextCursor : null });
      if (!alive.current) return;
      if (!result.ok) { setReady(false); setError(result.error.message); return; }
      // Never discard already visible blockers or provisional local attempts.
      acceptHistory(result);
    } catch { if (alive.current) { setReady(false); setError(uncertain); } }
    finally { ownPending.current = false; if (alive.current) setBusy(false); }
  };
  const eligible = record && (record.edit.to.length + record.edit.cc.length + record.edit.bcc.length) > 0 && record.edit.subject.trim() && record.edit.body.trim();
  const visible = chronological(attempts);
  return <div className="max-w-full space-y-2">
    <button type="button" onClick={MICROSOFT_DRAFT_SEND_RELEASED ? () => { void send(); } : undefined} disabled={!MICROSOFT_DRAFT_SEND_RELEASED || !connected || !ready || busy || barrier || Boolean(ownAttempt.current) || blocked || dirty || selectedFileCount > 0 || !eligible}
      title={dirty || selectedFileCount ? "Save all changes and attachments before sending" : "Send this saved Microsoft draft"} className={sendStyle}><Glyph name="send" />{busy && ownAttempt.current ? "Checking send…" : "Send"}</button>
    {selectedFileCount > 0 && <p className="text-xs">Save selected attachments before sending.</p>}
    <section aria-label="Microsoft send status" aria-live="polite" className="max-h-40 space-y-2 overflow-y-auto text-xs">
      {busy && <p role="status">Checking send status…</p>}
      {visible.map(r => <div key={r.attemptId} data-send-attempt={r.attemptId} className="space-y-1">
        <p>{r.sequence ? `Send attempt ${r.sequence}` : "New send attempt"}{r.providerMessageId === providerMessageId ? " · This draft" : ""}
          {r.createdAt ? <> · <time dateTime={r.createdAt}>{new Date(r.createdAt).toLocaleString()}</time></> : r.sequence ? " · Original time unavailable" : ""}</p>
        <p role="status">{r.outcome === "confirmed_performed" ? "Sending content confirmed by comparison with the approved manifest and exact Sent Items copy. Recipient delivery is not confirmed."
          : r.outcome === "performed_content_mismatch" ? "Sent copy found, but its content differs from the approved message. Do not send again. This draft remains locked."
          : r.outcome === "confirmed_not_started" ? "Sending confirmed not started. Review and reopen the draft before a new send request."
            : r.sentCopyConfirmed ? "Sent copy found, but approved content cannot be verified. Do not send again."
            : r.providerAccepted ? "Microsoft accepted the request; the Sent copy is not yet confirmed. Do not send again." : uncertain}</p>
        {microsoftDraftSendReason(r.reason) && <p>{microsoftDraftSendReason(r.reason)}</p>}
        {r.outcome === "uncertain" && <button type="button" disabled={busy || !connected} onClick={() => { void check(r); }} className="rounded-full border border-[var(--workspace-border)] px-3 py-1.5">Check Sent status</button>}
      </div>)}
      {nextCursor !== null && <><p>Older attempts may include unresolved sends.</p><button type="button" disabled={busy || !connected} onClick={() => { void reloadStatus(true); }} className="rounded-full border border-[var(--workspace-border)] px-3 py-1.5">Load older send history</button></>}
      {error && <p role="alert">{error}</p>}
      <button type="button" disabled={busy || !connected} onClick={() => { void reloadStatus(); }} className="rounded-full border border-[var(--workspace-border)] px-3 py-1.5">Reload send status</button>
    </section>
  </div>;
}
