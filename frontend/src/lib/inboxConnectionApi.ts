import type { ProviderId } from "../types/onboarding";

export type LiveInboxMessageSnapshot = {
  id: string;
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
  try {
    const response = await fetch("/api/inboxes/connect-imap", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    });
    
    const payload = (await response.json()) as ConnectInboxResponse;

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
