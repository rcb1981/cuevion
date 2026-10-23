import React, { useEffect, useRef, useState } from "react";
import {
  checkMicrosoftSentStatus,
  createMicrosoftSendAttemptId,
  sendMicrosoftMessage,
  type MicrosoftOutboundCompose,
} from "../../lib/inboxConnectionApi";
import { prepareMicrosoftComposeRequest, type MicrosoftSelectedSource } from "../../lib/microsoftComposeRequest";

type MicrosoftComposeProps = {
  mailboxId: string;
  mode: "new" | "reply" | "reply_all" | "forward";
  sourceProviderMessageId?: string;
  sourceDisplay?: string;
  source?: MicrosoftSelectedSource | null;
  selectedProviderMessageId?: string | null;
  sourceIsExact?: boolean;
  connected: boolean;
  onClose: () => void;
  onOpenSent: () => void;
};

export function MicrosoftComposePanel({
  mailboxId, mode, sourceProviderMessageId, sourceDisplay,
  source = null, selectedProviderMessageId = null, sourceIsExact = false, connected,
  onClose, onOpenSent,
}: MicrosoftComposeProps) {
  const [to, setTo] = useState("");
  const [cc, setCc] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [sending, setSending] = useState(false);
  const [checkingSent, setCheckingSent] = useState(false);
  const [sentStatusNotice, setSentStatusNotice] = useState<string | null>(null);
  const [confirmedSentCopy, setConfirmedSentCopy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const pending = useRef(false);
  const operationId = useRef<string | null>(null);
  const liveTicket = useRef(0);
  const statusTicket = useRef(0);
  const statusInFlight = useRef(false);

  useEffect(() => () => {
    // This invalidates stale UI callbacks only. A send already accepted by
    // the server continues; navigating away does not cancel an email.
    liveTicket.current += 1;
    statusTicket.current += 1;
    statusInFlight.current = false;
  }, []);

  const checkSentStatus = async () => {
    const attemptId = operationId.current;
    if (!attemptId || pending.current || statusInFlight.current) {
      return;
    }
    statusInFlight.current = true;
    const ticket = ++statusTicket.current;
    setCheckingSent(true);
    setSentStatusNotice(null);
    try {
      // Read-only: the service never creates a draft or sends again.
      const result = await checkMicrosoftSentStatus({ mailboxId, attemptId });
      if (ticket !== statusTicket.current) {
        return;
      }
      if (!result.ok) {
        setSentStatusNotice(
          result.error.message +
          " This status check never resends a message.",
        );
        return;
      }
      if (result.status === "sent_copy_confirmed") {
        setConfirmedSentCopy(true);
        setSentStatusNotice(
          "The message is confirmed in Microsoft Sent Items. " +
          "Delivery to the recipient is not confirmed.",
        );
      } else if (result.status === "sent_copy_pending") {
        setSentStatusNotice(
          "The message is not yet visible in Sent Items. " +
          "Microsoft may still be processing it. Wait before checking again; do not resend.",
        );
      } else {
        setSentStatusNotice(
          "The stored send attempt has not reached the send stage. " +
          "Check Microsoft Drafts before creating another message.",
        );
      }
    } catch {
      if (ticket === statusTicket.current) {
        setSentStatusNotice(
          "Sent Items could not be checked. The status remains uncertain; do not resend.",
        );
      }
    } finally {
      if (ticket === statusTicket.current) {
        statusInFlight.current = false;
        setCheckingSent(false);
      }
    }
  };

  const send = async () => {
    if (pending.current || attempted || accepted) return;
    // Reject invalid requests BEFORE assigning an attempt ID or disabling Send.
    // Graph, not visible From text, remains authority for reply recipients.
    const prepared = prepareMicrosoftComposeRequest({
      mode, mailboxId, body, to, cc, subject, source,
      selectedProviderMessageId, sourceIsExact, connected,
      busy: sending || checkingSent,
    });
    if (!prepared.ok) {
      setNotice("Please check the message and recipients (" + prepared.code + ").");
      return;
    }
    const compose: MicrosoftOutboundCompose = prepared.compose;
    pending.current = true;
    setAttempted(true);
    setSending(true);
    setNotice(null);
    const ticket = ++liveTicket.current;
    try {
      const id = createMicrosoftSendAttemptId();
      operationId.current = id;
      const result = await sendMicrosoftMessage({
        mailboxId, attemptId: id, compose,
      });
      if (ticket !== liveTicket.current) return;
      if (result.ok) {
        setAccepted(true);
        setNotice(
          "Microsoft accepted the send request. Delivery is not confirmed. " +
          "Check Sent to verify the saved copy.",
        );
      } else {
        const uncertain = (
          result.error?.code === "microsoft_send_unconfirmed" ||
          result.error?.code === "microsoft_send_attempt_already_started" ||
          result.error?.code === "microsoft_draft_unconfirmed"
        );
        setNotice(
          (result.error?.message ?? "Microsoft could not confirm the send request.") +
          (uncertain ? " Do not send this message again before checking Sent and Drafts." : ""),
        );
      }
    } catch {
      if (ticket === liveTicket.current) {
        setNotice(
          "The send request could not be confirmed. Check Sent and Drafts " +
          "before creating another message.",
        );
      }
    } finally {
      if (ticket === liveTicket.current) {
        pending.current = false;
        setSending(false);
      }
    }
  };

  return (
    <section
      aria-label="Microsoft message composer"
      className="h-full min-h-0 overflow-y-auto p-5 sm:p-8"
    >
      <div className="mx-auto max-w-2xl space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-xl font-semibold">
              {mode === "reply" ? "Reply" : mode === "reply_all" ? "Reply All" : mode === "forward" ? "Forward" : "New message"}
            </h2>
            <p className="mt-1 text-xs text-[var(--workspace-text-muted)]">
              {mode === "reply" || mode === "reply_all"
                ? "Microsoft determines reply recipients, including Reply-To, and preserves the conversation."
                : mode === "forward"
                  ? "Enter forwarding recipients. Microsoft will build the forwarded draft."
                  : "Compose a new Microsoft 365 email. No new attachments in this version."}
            </p>
          </div>
          <button
            type="button"
            disabled={sending}
            onClick={onClose}
            className="rounded-lg border border-[var(--workspace-border)] px-3 py-2 text-xs disabled:opacity-50"
          >
            Close
          </button>
        </div>

        {(mode === "reply" || mode === "reply_all") ? (
          <div className="rounded-xl border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] p-4 text-sm">
            {mode === "reply_all" ? "Reply all to" : "Reply to"}: {sourceDisplay || "selected Inbox message"}
            <p className="mt-1 text-xs text-[var(--workspace-text-muted)]">
              The reply address is determined by Microsoft, including any Reply-To header.
            </p>
          </div>
        ) : (
          <>
            <label className="block space-y-2 text-sm">
              <span className="font-medium">To</span>
              <input
                value={to}
                onChange={(event) => setTo(event.target.value)}
                disabled={attempted}
                type="text"
                autoComplete="off"
                placeholder="artist@example.com, manager@example.com"
                className="w-full rounded-xl border border-[var(--workspace-border)] bg-transparent p-3 outline-none focus-visible:outline-2 disabled:opacity-50"
              />
            </label>
            <label className="block space-y-2 text-sm">
              <span className="font-medium">Cc (optional)</span>
              <input
                value={cc}
                onChange={(event) => setCc(event.target.value)}
                disabled={attempted}
                type="text"
                autoComplete="off"
                placeholder="additional@example.com"
                className="w-full rounded-xl border border-[var(--workspace-border)] bg-transparent p-3 outline-none focus-visible:outline-2 disabled:opacity-50"
              />
            </label>
            {mode === "new" && <label className="block space-y-2 text-sm">
              <span className="font-medium">Subject</span>
              <input
                value={subject}
                onChange={(event) => setSubject(event.target.value)}
                disabled={attempted}
                maxLength={250}
                type="text"
                placeholder="Subject"
                className="w-full rounded-xl border border-[var(--workspace-border)] bg-transparent p-3 outline-none focus-visible:outline-2 disabled:opacity-50"
              />
            </label>}
          </>
        )}

        <label className="block space-y-2 text-sm">
          <span className="font-medium">Message</span>
          <textarea
            value={body}
            onChange={(event) => setBody(event.target.value)}
            disabled={attempted}
            rows={12}
            maxLength={64_000}
            placeholder="Write your message…"
            className="w-full resize-y rounded-xl border border-[var(--workspace-border)] bg-transparent p-4 text-sm leading-6 outline-none focus-visible:outline-2 disabled:opacity-50"
          />
        </label>
        <p className="text-xs text-[var(--workspace-text-muted)]">
          Plain text only. Sent status and delivery are separate confirmations.
        </p>
        {notice && (
          <div
            role={accepted ? "status" : "alert"}
            className="rounded-xl border border-[var(--workspace-border)] p-4 text-sm"
          >
            {notice}
          </div>
        )}
        {sentStatusNotice && (
          <div role="status" className="rounded-xl border border-[var(--workspace-border)] p-4 text-sm">
            {sentStatusNotice}
          </div>
        )}
        <div className="flex flex-wrap gap-3">
          {attempted && operationId.current !== null && (
            <button
              type="button"
              disabled={sending || checkingSent}
              onClick={() => { void checkSentStatus(); }}
              className="rounded-xl border border-[var(--workspace-border)] px-5 py-2.5 text-sm font-semibold disabled:opacity-45"
            >
              {checkingSent ? "Checking Sent…" : "Check Sent status"}
            </button>
          )}
          {!accepted && (
            <button
              type="button"
              disabled={attempted || !body.trim() ||
                (mode === "new" && (!to.trim() || !subject.trim())) ||
                (mode === "forward" && !to.trim())}
              onClick={() => { void send(); }}
              className="rounded-xl border border-[var(--workspace-border)] px-5 py-2.5 text-sm font-semibold disabled:opacity-45"
            >
              {sending ? "Sending…" : attempted ? "Submission recorded" : "Send"}
            </button>
          )}
          {(accepted || confirmedSentCopy) && (
            <button
              type="button"
              onClick={onOpenSent}
              className="rounded-xl border border-[var(--workspace-border)] px-5 py-2.5 text-sm font-semibold"
            >
              View Sent
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
