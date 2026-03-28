import type { ProviderId } from "../types/onboarding";

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

type ConnectInboxRequest = {
  provider: ProviderId;
  email: string;
  host: string;
  port: string;
  ssl: boolean;
  username: string;
  password: string;
};

type ConnectInboxResponse = {
  ok: boolean;
  messages?: LiveInboxMessageSnapshot[];
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
