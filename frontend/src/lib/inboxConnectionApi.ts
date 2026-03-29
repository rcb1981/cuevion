import type { OnboardingState, ProviderId } from "../types/onboarding";

export type LiveInboxMessageSnapshot = {
  id: string;
  imapUid?: string;
  sender: string;
  subject: string;
  snippet: string;
  from: string;
  to: string;
  cc?: string;
  timestamp: string;
  createdAt: string;
  body: string[];
  unread?: boolean;
  ui_signal?: string;
};

export type ConnectInboxRequest = {
  provider: ProviderId;
  email: string;
  host: string;
  port: string;
  ssl: boolean;
  username: string;
  password: string;
  internalRole?: string | null;
  focusPreferences?: OnboardingState["focusPreferences"] | null;
  selectedInboxes?: string[] | null;
};

type ConnectInboxResponse = {
  ok: boolean;
  messages?: LiveInboxMessageSnapshot[];
  error?: {
    code?: string;
    message?: string;
  };
};

export type SendInboxAttachmentRequest = {
  name: string;
  mimeType?: string;
  contentBase64: string;
};

export type SendGmailMessageRequest = {
  provider: ProviderId;
  email: string;
  username: string;
  password: string;
  from: string;
  to: string;
  cc?: string;
  bcc?: string;
  subject: string;
  bodyHtml: string;
  bodyText: string;
  attachments?: SendInboxAttachmentRequest[];
};

type SendGmailMessageResponse = {
  ok: boolean;
  error?: {
    code?: string;
    message?: string;
  };
};

export async function connectInboxWithImap(
  request: ConnectInboxRequest,
): Promise<ConnectInboxResponse> {
  const requestStartedAt = performance.now();
  try {
    const response = await fetch("/api/inboxes/connect-imap", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    });
    
    const payload = (await response.json()) as ConnectInboxResponse;
    console.info("[SYNC-TIMING] connectInboxWithImap response", {
      ok: response.ok,
      email: request.email,
      durationMs: Math.round(performance.now() - requestStartedAt),
      messageCount: payload.messages?.length ?? 0,
    });

    if (!response.ok) {
      return {
        ok: false,
        error: payload.error ?? {
          code: "connection_failed",
          message: "Could not connect to inbox.",
        },
      };
    }

    return payload;
  } catch (error) {
    console.error("[SYNC-TIMING] connectInboxWithImap failed", {
      email: request.email,
      durationMs: Math.round(performance.now() - requestStartedAt),
      error: error instanceof Error ? error.message : String(error),
    });
    return {
      ok: false,
      error: {
        code: "connection_failed",
        message:
          error instanceof Error ? error.message : "Could not connect to inbox.",
      },
    };
  }
}

export async function sendGmailMessage(
  request: SendGmailMessageRequest,
): Promise<SendGmailMessageResponse> {
  const abortController = new AbortController();
  const timeoutId = window.setTimeout(() => {
    abortController.abort();
  }, 45000);

  try {
    const response = await fetch("/api/inboxes/send-gmail", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      signal: abortController.signal,
      body: JSON.stringify(request),
    });
    const rawPayload = await response.text();
    let payload: SendGmailMessageResponse | null = null;

    if (rawPayload.trim()) {
      try {
        payload = JSON.parse(rawPayload) as SendGmailMessageResponse;
      } catch {
        payload = null;
      }
    }

    if (!response.ok) {
      return {
        ok: false,
        error: payload?.error ?? {
          code: "send_failed",
          message: `Could not send email${response.status ? ` (${response.status})` : ""}.`,
        },
      };
    }

    return payload ?? { ok: true };
  } catch (error) {
    return {
      ok: false,
      error: {
        code: error instanceof DOMException && error.name === "AbortError"
          ? "timeout"
          : "send_failed",
        message:
          error instanceof DOMException && error.name === "AbortError"
            ? "Sending timed out. Please try again."
            : error instanceof Error
              ? error.message
              : "Could not send email.",
      },
    };
  } finally {
    window.clearTimeout(timeoutId);
  }
}
