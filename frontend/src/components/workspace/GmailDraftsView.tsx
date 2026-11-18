import { useState, type ReactNode } from "react";
import type { useGmailDrafts } from "../../lib/useGmailDrafts";

type LocalPreview = { id: string; subject: string; to?: string; cc?: string; body: string[]; bodyHtml?: string };
export function GmailDraftsView({ drafts, localPreviews, connected, mobile, renderHtml }: {
  drafts: ReturnType<typeof useGmailDrafts>;
  localPreviews: readonly LocalPreview[];
  connected: boolean; mobile: boolean;
  renderHtml: (html: string, origin: "local" | "provider") => ReactNode;
}) {
  const [localId, setLocalId] = useState<string | null>(null);
  const [showText, setShowText] = useState(false);
  const [mobileReading, setMobileReading] = useState(false);
  const local = localPreviews.find(item => item.id === localId) ?? null;
  const detail = local ? null : drafts.detail;
  const selection = local ?? detail;
  const button = "shrink-0 rounded-lg border border-[var(--workspace-border-soft)] px-3 py-1 disabled:opacity-50";
  const row = "flex w-full flex-col gap-1 border-b border-[var(--workspace-border-soft)] px-4 py-3 text-left text-[0.84rem] text-[var(--workspace-text-soft)] hover:bg-[var(--workspace-hover-surface)]";
  return <>
    <div data-gmail-drafts-list className={mobile && mobileReading ? "hidden" : "flex min-h-0 min-w-0 flex-col overflow-hidden border-r border-[var(--workspace-border-soft)]"}>
      <div role="status" aria-live="polite" className="flex-none space-y-3 border-b border-[var(--workspace-border-soft)] px-3 pb-3 pt-4 text-[0.8rem] text-[var(--workspace-text-soft)]">
        <div>Gmail drafts · View only</div>
        <div>{!connected ? "Gmail is disconnected. Check the mailbox connection to view provider drafts."
          : drafts.error ?? (drafts.loading ? "Loading drafts from Gmail…" : drafts.loaded && !drafts.drafts.length ? "No drafts in Gmail." : "Saved in Gmail. Editing and saving are not available here yet.")}</div>
        {drafts.nextPageToken && connected ? <button type="button" className={button} disabled={drafts.loading} onClick={() => { void drafts.loadMore(); }}>Load more</button> : null}
      </div>
      <div className="cuevion-dark-scroll cuevion-soft-scroll min-h-0 flex-1 overflow-y-auto">
        {drafts.drafts.map(draft => <button type="button" key={draft.draftId} data-gmail-draft-id={draft.draftId}
          aria-pressed={!local && drafts.selectedId === draft.draftId} className={`${row} ${!local && drafts.selectedId === draft.draftId ? "bg-[var(--workspace-hover-surface)]" : ""}`}
          onClick={() => { setLocalId(null); setMobileReading(true); void drafts.select(draft.draftId); }}>
          <span className="font-medium text-[var(--workspace-text)]">{draft.subject || "(No subject)"}</span>
          <span className="truncate">To: {draft.to || "No recipients"}</span>
          <span className="text-[0.72rem] text-[var(--workspace-text-faint)]">Gmail draft</span>
        </button>)}
        {localPreviews.length ? <>
          <div className="border-b border-[var(--workspace-border-soft)] px-4 py-3 text-[0.8rem] text-[var(--workspace-text-faint)]">Temporary local previews · Not saved to Gmail</div>
          {localPreviews.map(item => <button type="button" key={item.id} data-local-draft-id={item.id}
            aria-pressed={localId === item.id} className={`${row} ${localId === item.id ? "bg-[var(--workspace-hover-surface)]" : ""}`} onClick={() => { setLocalId(item.id); setMobileReading(true); }}>
            <span className="font-medium text-[var(--workspace-text)]">{item.subject || "(No subject)"}</span>
            <span className="truncate">To: {item.to || "No recipients"}</span><span className="text-[0.72rem]">Temporary local preview</span>
          </button>)}
        </> : null}
      </div>
    </div>
    <div data-gmail-drafts-reader className={mobile && !mobileReading ? "hidden" : "cuevion-dark-scroll cuevion-soft-scroll flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto overflow-x-auto bg-transparent"}>
      <div className="space-y-4 p-5 pr-4 text-[0.9rem] text-[var(--workspace-text-soft)] md:p-6 md:pr-5">
        {mobile && mobileReading ? <button type="button" className={button} onClick={() => setMobileReading(false)}>Back to drafts</button> : null}
        {local ? <p role="status">Temporary local preview only. Not saved to Gmail and not guaranteed to survive a browser reload. The original Compose remains separate.</p>
          : <p>Gmail draft · View only. Editing, saving and sending are not available in this view.</p>}
        {!local && drafts.detailLoading ? <p role="status">Loading draft preview…</p> : null}
        {!local && drafts.detailError ? <p role="alert">{drafts.detailError}</p> : null}
        {selection ? <>
          <h2 className="text-[1.3rem] font-medium tracking-tight text-[var(--workspace-text)] md:text-[1.45rem]">{selection.subject || "(No subject)"}</h2>
          <div className="space-y-1 break-words">
            {detail ? <div>From: {detail.from || "Not specified"}</div> : null}
            <div>To: {selection.to || "Not specified"}</div>
            {selection.cc ? <div>Cc: {selection.cc}</div> : null}
            {detail?.bcc ? <div data-gmail-draft-bcc>Bcc: {detail.bcc}</div> : null}
          </div>
          {detail?.previewStatus === "unavailable" ? <p role="status">Preview unavailable ({detail.unavailableReason?.replace(/_/g, " ")}). View this draft in Gmail. Its content has not been changed.</p> : <>
            {detail?.bodyHtml !== null && detail?.bodyText !== null && detail ? <button type="button" className={button} onClick={() => setShowText(value => !value)}>{showText ? "Show HTML" : "Show plain text"}</button> : null}
            {local ? local.bodyHtml ? renderHtml(local.bodyHtml, "local") : <div className="whitespace-pre-wrap break-words">{local.body.join("\n\n")}</div>
              : detail && detail.bodyHtml !== null && (!showText || detail.bodyText === null) ? renderHtml(detail.bodyHtml, "provider")
                : <div className="whitespace-pre-wrap break-words">{detail?.bodyText ?? (detail?.bodyHtml ? "Select Show HTML to view this draft." : "This draft has an empty body.")}</div>}
          </>}
          {detail?.attachments.length ? <div className="space-y-2">
            <h3 className="font-medium">Attachments</h3>
            <p>Attachment inventory only. Files and embedded images are not downloaded in this view.</p>
            <ul>{detail.attachments.map(file => <li key={file.partId}>{file.filename || "Unnamed attachment"} · {file.mimeType} · {file.size.toLocaleString()} bytes</li>)}</ul>
          </div> : null}
          {detail && !detail.inventoryComplete ? <p>Attachment inventory is unavailable for this preview.</p> : null}
        </> : !drafts.detailLoading && !drafts.detailError ? <p>Select a draft to view it.</p> : null}
      </div>
    </div>
  </>;
}
