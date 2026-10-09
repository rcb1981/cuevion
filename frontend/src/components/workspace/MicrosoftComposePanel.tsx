import React, { useEffect, useRef, useState } from "react";
import {
  createMicrosoftSendAttemptId,
  sendMicrosoftMessage,
  type MicrosoftOutboundCompose,
} from "../../lib/inboxConnectionApi";

type MicrosoftComposeProps = {
  mailboxId: string;
  mode: "new" | "reply";
  sourceProviderMessageId?: string;
  sourceDisplay?: string;
  onClose: () => void;
  onOpenSent: () => void;
};

export function MicrosoftComposePanel({
  mailboxId, mode, sourceProviderMessageId, sourceDisplay,
  onClose, onOpenSent,
}: MicrosoftComposeProps) {
  const [to, setTo] = useState("");
  const [cc, setCc] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const pending = useRef(false);
  const operationId = useRef<string | null>(null);
  const liveTicket = useRef(0);

  useEffect(() => () => {
    // This invalidates stale UI callbacks only. A send already accepted by
    // the server continues; navigating away does not cancel an email.
    liveTicket.current += 1;
  }, []);

  const send = async () => {
    // A second click must not create a new operation ID or network request.
    if (pending.current || attempted || accepted) return;
    pending.current = true;
    setAttempted(true);
    setSending(true);
    setNotice(null);
    const ticket = ++liveTicket.current;
    let compose: MicrosoftOutboundCompose;
    if (mode === "reply") {
      if (!sourceProviderMessageId) {
        pending.current = false;
        setSending(false);
        setNotice("The original message cannot be verified.");
        return;
      }
      compose = {
        mode: "reply", sourceProviderMessageId, body,
      };
    } else {
      const parse = (value: string) => value
        .split(",")
        .map((address) => address.trim().toLowerCase())
        .filter(Boolean);
      compose = {
        mode: "new", to: parse(to), cc: parse(cc),
        subject: subject.trim(), body,
      };
    }
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
              {mode === "reply" ? "Reply" : "New message"}
            </h2>
            <p className="mt-1 text-xs text-[var(--workspace-text-muted)]">
              {mode === "reply"
                ? "Microsoft will determine the reply recipient and preserve the conversation."
                : "Compose a new Microsoft 365 email. No attachments in this version."}
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

        {mode === "reply" ? (
          <div className="rounded-xl border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] p-4 text-sm">
            Reply to: {sourceDisplay || "selected Inbox message"}
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
            <label className="block space-y-2 text-sm">
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
            </label>
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
        <div className="flex flex-wrap gap-3">
          {!accepted && (
            <button
              type="button"
              disabled={attempted || !body.trim() ||
                (mode === "new" && (!to.trim() || !subject.trim()))}
              onClick={() => { void send(); }}
              className="rounded-xl border border-[var(--workspace-border)] px-5 py-2.5 text-sm font-semibold disabled:opacity-45"
            >
              {sending ? "Sending…" : attempted ? "Submission recorded" : "Send"}
            </button>
          )}
          {accepted && (
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
