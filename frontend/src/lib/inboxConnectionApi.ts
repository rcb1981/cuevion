import {
  applyProviderDefaults,
  getProviderConnectionMethod,
  isOAuthConnectionProvider,
  usesEmailAsImapUsername,
} from "./inboxProviderDefaults";
import type {
  CustomImapSettings,
  CustomSmtpSettings,
  InboxConnectionMethod,
  InboxConnectionStatus,
  OnboardingState,
  ProviderId,
} from "../types/onboarding";

export type LiveInboxAttachmentSnapshot = {
  id: string;
  name: string;
  mimeType?: string;
  size?: number;
  contentId?: string;
  disposition?: string;
  inlineSrc?: string;
};

export type LiveInboxMessageSnapshot = {
  id: string;
  imapUid?: string;
  providerThreadId?: string;
  sender: string;
  subject: string;
  snippet: string;
  from: string;
  to: string;
  cc?: string;
  timestamp: string;
  createdAt: string;
  body: string[];
  bodyHtml?: string;
  attachments?: LiveInboxAttachmentSnapshot[];
  unread?: boolean;
  flagged?: boolean;
  category?: string;
  categorySource?: string;
  categoryConfidence?: string;
  signal?: string;
  ui_signal?: string;
  internalClassification?: string;
  final_visibility?: string;
  action?: string;
  v7_final_priority?: string;
  threadId?: string;
  classifierVersion?: string;
};

export type InitialConnectInboxRequest = {
  mode: "initial";
  mailboxId: string;
  connection: {
    provider: "custom_imap";
    email: string;
    imap: CustomImapSettings;
    smtp: CustomSmtpSettings;
  };
  internalRole?: string | null;
  focusPreferences?: OnboardingState["focusPreferences"] | null;
  limit?: number | null;
};

export type ReconnectInboxRequest = Omit<InitialConnectInboxRequest, "mode"> & {
  mode: "reconnect";
};

export type RefreshConnectInboxRequest = {
  mode: "refresh";
  mailboxId: string;
  focusPreferences?: OnboardingState["focusPreferences"] | null;
  limit?: number | null;
};

export type ConnectInboxRequest =
  | InitialConnectInboxRequest
  | ReconnectInboxRequest
  | RefreshConnectInboxRequest;

export type ConnectInboxResponse = {
  ok: boolean;
  messages?: LiveInboxMessageSnapshot[];
  inboxUidSet?: string[] | null;
  uidValidity?: string | null;
  warning?: {
    code?: string;
    stage?: string;
    message?: string;
    fetched_count?: number;
  } | null;
  warnings?: Array<{
    code?: string;
    stage?: string;
    message?: string;
    fetched_count?: number;
  }>;
  error?: {
    code?: string;
    stage?: string;
    message?: string;
    fetched_count?: number;
  };
};

export type OAuthInboxRequest = {
  provider: ProviderId;
  email: string;
  internalRole?: string | null;
  focusPreferences?: OnboardingState["focusPreferences"] | null;
  selectedInboxes?: string[] | null;
};

export type OAuthInboxResponse = {
  ok: boolean;
  connectionStatus: InboxConnectionStatus;
  connectionMethod: Extract<InboxConnectionMethod, "oauth">;
  authorizationUrl?: string | null;
  message?: string | null;
  error?: {
    code?: string;
    message?: string;
  };
};

export type FetchGmailInboxRequest = {
  mailboxId: string;
  focusPreferences?: OnboardingState["focusPreferences"] | null;
  limit?: number | null;
};

export type FetchGmailThreadRequest = {
  mailboxId: string;
  providerThreadId: string;
};

export type GmailThreadAttachment = {
  partId: string;
  providerAttachmentId: string | null;
  name: string;
  mimeType: string | null;
  size: number | null;
  contentId: string | null;
  disposition: string | null;
};

export type GmailThreadMessage = {
  providerMessageId: string;
  providerThreadId: string;
  rfcMessageId: string | null;
  internalDate: string;
  createdAt: string;
  dateHeader: string | null;
  sender: string;
  from: string;
  to: string;
  cc: string;
  subject: string;
  snippet: string;
  bodyText: string;
  bodyHtml: string | null;
  labelIds: string[];
  unread: boolean;
  flagged: boolean;
  attachments: GmailThreadAttachment[];
};

export type FetchGmailThreadSuccess = {
  ok: true;
  providerThreadId: string;
  messages: GmailThreadMessage[];
};

export type FetchGmailThreadError = {
  ok: false;
  error: {
    code: string;
    message: string;
  };
};

export type FetchGmailThreadResponse = FetchGmailThreadSuccess | FetchGmailThreadError;

export type InboxConnectionAttemptResult = {
  ok: boolean;
  connected: boolean;
  connectionStatus: InboxConnectionStatus;
  connectionMethod: InboxConnectionMethod | null;
  connectionMessage?: string | null;
  oauthAuthorizationUrl?: string | null;
  messages?: LiveInboxMessageSnapshot[];
  uidValidity?: string | null;
  warning?: {
    code?: string;
    stage?: string;
    message?: string;
    fetched_count?: number;
  } | null;
  error?: {
    code?: string;
    stage?: string;
    message?: string;
    fetched_count?: number;
  };
};

export function buildConnectInboxRequest(options: {
  mode: "initial" | "reconnect";
  mailboxId: string;
  provider: ProviderId;
  email: string;
  customImap: CustomImapSettings;
  customSmtp: CustomSmtpSettings;
  internalRole?: string | null;
  focusPreferences?: OnboardingState["focusPreferences"] | null;
  selectedInboxes?: string[] | null;
  limit?: number | null;
}): InitialConnectInboxRequest | ReconnectInboxRequest {
  const email = options.email.trim();
  const resolvedImapSettings = applyProviderDefaults(
    options.provider,
    options.customImap,
    email,
  );

  return {
    mode: options.mode,
    mailboxId: options.mailboxId,
    connection: {
      provider: "custom_imap",
      email,
      imap: {
        host: resolvedImapSettings.host.trim(),
        port: resolvedImapSettings.port.trim(),
        ssl: resolvedImapSettings.ssl,
        username: usesEmailAsImapUsername(options.provider)
          ? email
          : resolvedImapSettings.username.trim(),
        password: resolvedImapSettings.password,
      },
      smtp: {
        ...options.customSmtp,
        host: options.customSmtp.host.trim(),
        port: options.customSmtp.port.trim(),
        username: options.customSmtp.username.trim(),
      },
    },
    internalRole: options.internalRole,
    focusPreferences: options.focusPreferences,
    limit: options.limit,
  };
}

export function buildRefreshInboxRequest(options: {
  mailboxId: string;
  focusPreferences?: OnboardingState["focusPreferences"] | null;
  limit?: number | null;
}): RefreshConnectInboxRequest {
  return {
    mode: "refresh",
    mailboxId: options.mailboxId,
    focusPreferences: options.focusPreferences,
    limit: options.limit,
  };
}

export function buildOAuthInboxRequest(options: {
  provider: ProviderId;
  email: string;
  internalRole?: string | null;
  focusPreferences?: OnboardingState["focusPreferences"] | null;
  selectedInboxes?: string[] | null;
}): OAuthInboxRequest {
  return {
    provider: options.provider,
    email: options.email.trim(),
    internalRole: options.internalRole,
    focusPreferences: options.focusPreferences,
    selectedInboxes: options.selectedInboxes,
  };
}

export type SendInboxAttachmentRequest = {
  name: string;
  mimeType?: string;
  contentBase64: string;
};

export type DownloadAttachmentGmailRequest = {
  mailboxId: string;
  messageId: string;
  attachmentId: string;
};

export type DownloadAttachmentImapRequest = {
  mailboxId: string;
  folder: string;
  uid: string;
  uidValidity?: string | null;
  attachmentId: string;
};

export type DownloadAttachmentRequest =
  | DownloadAttachmentGmailRequest
  | DownloadAttachmentImapRequest;

export type SendGmailMessageRequest = {
  mailboxId: string;
  to: string;
  cc?: string;
  bcc?: string;
  subject: string;
  bodyHtml: string;
  bodyText: string;
  attachments?: SendInboxAttachmentRequest[];
};

export function buildSendInboxWireRequest(
  request: SendGmailMessageRequest,
): SendGmailMessageRequest {
  return {
    mailboxId: request.mailboxId,
    to: request.to,
    cc: request.cc,
    bcc: request.bcc,
    subject: request.subject,
    bodyHtml: request.bodyHtml,
    bodyText: request.bodyText,
    attachments: request.attachments,
  };
}

export type InboxMessageAction = "mark_read" | "mark_unread" | "star" | "unstar";

export type GmailInboxMessageActionRequest = {
  mailboxId: string;
  messageId: string;
  action: InboxMessageAction;
};

export type ImapInboxMessageActionRequest = {
  mailboxId: string;
  folder: string;
  uid: string;
  uidValidity?: string | null;
  action: InboxMessageAction;
};

export type InboxMessageActionRequest =
  | GmailInboxMessageActionRequest
  | ImapInboxMessageActionRequest;

export type InboxMessageActionResponse = {
  ok: boolean;
  action?: InboxMessageAction;
  error?: {
    code?: string;
    message?: string;
  };
};

type SendGmailMessageResponse = {
  ok: boolean;
  error?: {
    code?: string;
    message?: string;
  };
};

type AttachmentDownloadErrorPayload = {
  error?: {
    code?: string;
    message?: string;
  };
};

export async function mutateInboxMessageAction(
  request: InboxMessageActionRequest,
): Promise<InboxMessageActionResponse> {
  const wireRequest = "messageId" in request
    ? {
        mailboxId: request.mailboxId,
        messageId: request.messageId,
        action: request.action,
      }
    : request;
  try {
    const response = await fetch("/api/inboxes/message-action", {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(wireRequest),
    });
    const rawPayload = await response.text();
    let payload: InboxMessageActionResponse | null = null;

    if (rawPayload.trim()) {
      try {
        payload = JSON.parse(rawPayload) as InboxMessageActionResponse;
      } catch {
        payload = null;
      }
    }

    if (!response.ok || payload?.ok === false) {
      return {
        ok: false,
        error: payload?.error ?? {
          code: "message_action_failed",
          message: "Could not update this message in the connected mailbox.",
        },
      };
    }

    return payload ?? { ok: true, action: request.action };
  } catch (error) {
    return {
      ok: false,
      error: {
        code: "message_action_failed",
        message:
          error instanceof Error
            ? error.message
            : "Could not update this message in the connected mailbox.",
      },
    };
  }
}

export type MailboxCredentialStatus = {
  imapPasswordSet: boolean;
  smtpPasswordSet: boolean;
};

export type MailboxCredentialStatusStore = Record<string, MailboxCredentialStatus>;

type MailboxCredentialStatusResponse = {
  ok: boolean;
  credentials?: MailboxCredentialStatusStore;
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
      credentials: "include",
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
          stage: "request",
          fetched_count: 0,
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
        stage: "request",
        fetched_count: 0,
      },
    };
  }
}

export async function connectInboxWithOAuth(
  request: OAuthInboxRequest,
): Promise<OAuthInboxResponse> {
  try {
    const response = await fetch("/api/inboxes/connect-oauth", {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    });

    const rawPayload = await response.text();
    let payload: Partial<OAuthInboxResponse> | null = null;

    if (rawPayload.trim()) {
      try {
        payload = JSON.parse(rawPayload) as Partial<OAuthInboxResponse>;
      } catch {
        payload = null;
      }
    }

    if (!response.ok) {
      return {
        ok: false,
        connectionMethod: "oauth",
        connectionStatus: "oauth_required",
        authorizationUrl: null,
        message: null,
        error:
          payload?.error ?? {
            code: "oauth_unavailable",
            message: rawPayload.trim() || "OAuth could not be started.",
          },
      };
    }

    return {
      ok: true,
      connectionMethod: "oauth",
      connectionStatus: payload?.connectionStatus ?? "oauth_required",
      authorizationUrl: payload?.authorizationUrl ?? null,
      message: payload?.message ?? null,
      error: payload?.error,
    };
  } catch (error) {
    return {
      ok: false,
      connectionMethod: "oauth",
      connectionStatus: "connection_failed",
      authorizationUrl: null,
      message: null,
      error: {
        code: "oauth_unavailable",
        message:
          error instanceof Error ? error.message : "OAuth could not be started.",
      },
    };
  }
}

export async function beginInboxConnection(options: {
  imapMode: "initial" | "reconnect";
  mailboxId: string;
  provider: ProviderId;
  email: string;
  customImap: CustomImapSettings;
  customSmtp: CustomSmtpSettings;
  internalRole?: string | null;
  focusPreferences?: OnboardingState["focusPreferences"] | null;
  selectedInboxes?: string[] | null;
}): Promise<InboxConnectionAttemptResult> {
  const connectionMethod = getProviderConnectionMethod(options.provider);

  if (isOAuthConnectionProvider(options.provider)) {
    const response = await connectInboxWithOAuth(
      buildOAuthInboxRequest({
        provider: options.provider,
        email: options.email,
        internalRole: options.internalRole,
        focusPreferences: options.focusPreferences,
        selectedInboxes: options.selectedInboxes,
      }),
    );

    if (!response.ok) {
      return {
        ok: false,
        connected: false,
        connectionMethod,
        connectionStatus: "connection_failed",
        connectionMessage: response.error?.message ?? "OAuth could not be started.",
        oauthAuthorizationUrl: null,
        error: response.error,
      };
    }

    return {
      ok: true,
      connected: response.connectionStatus === "connected",
      connectionMethod,
      connectionStatus: response.connectionStatus,
      connectionMessage: response.message ?? null,
      oauthAuthorizationUrl: response.authorizationUrl ?? null,
      messages: [],
    };
  }

  const response = await connectInboxWithImap(
    buildConnectInboxRequest({
      ...options,
      mode: options.imapMode,
    }),
  );

  if (!response.ok) {
    return {
      ok: false,
      connected: false,
      connectionMethod,
      connectionStatus: "connection_failed",
      connectionMessage: response.error?.message ?? "Could not connect to inbox.",
      oauthAuthorizationUrl: null,
      error: response.error,
    };
  }

  return {
    ok: true,
    connected: true,
    connectionMethod,
    connectionStatus: "connected",
    connectionMessage: null,
    oauthAuthorizationUrl: null,
    messages: response.messages ?? [],
    uidValidity: response.uidValidity ?? null,
    warning: response.warning ?? null,
  };
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
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
      },
      signal: abortController.signal,
      body: JSON.stringify(buildSendInboxWireRequest(request)),
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

async function readAttachmentDownloadError(
  response: Response,
  fallbackMessage: string,
) {
  const rawPayload = await response.text();

  if (rawPayload.trim()) {
    try {
      const payload = JSON.parse(rawPayload) as AttachmentDownloadErrorPayload;
      return payload.error?.message ?? fallbackMessage;
    } catch {
      return fallbackMessage;
    }
  }

  return fallbackMessage;
}

export async function downloadAttachment(
  request: DownloadAttachmentRequest,
): Promise<Blob> {
  const wireRequest = "messageId" in request
    ? {
        mailboxId: request.mailboxId,
        messageId: request.messageId,
        attachmentId: request.attachmentId,
      }
    : request;
  const response = await fetch("/api/inboxes/download-attachment", {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(wireRequest),
  });

  if (!response.ok) {
    throw new Error(
      await readAttachmentDownloadError(
        response,
        "Could not download this attachment.",
      ),
    );
  }

  return response.blob();
}

export async function getMailboxCredentialStatuses(
  mailboxIds: string[],
): Promise<MailboxCredentialStatusStore> {
  const normalizedMailboxIds = mailboxIds
    .map((mailboxId) => mailboxId.trim())
    .filter(Boolean);

  if (normalizedMailboxIds.length === 0) {
    return {};
  }

  try {
    const params = new URLSearchParams({
      mailboxIds: normalizedMailboxIds.join(","),
    });
    const response = await fetch(`/api/inboxes/credentials?${params.toString()}`, {
      method: "GET",
      credentials: "include",
      cache: "no-store",
    });
    const payload = (await response.json()) as MailboxCredentialStatusResponse;

    if (!response.ok || !payload.ok) {
      return {};
    }

    return payload.credentials ?? {};
  } catch {
    return {};
  }
}


export async function fetchGmailInbox(
  request: FetchGmailInboxRequest,
): Promise<ConnectInboxResponse> {
  try {
    const response = await fetch("/api/inboxes/fetch-gmail", {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        mailboxId: request.mailboxId,
        focusPreferences: request.focusPreferences,
        limit: request.limit,
      }),
    });

    const payload = (await response.json()) as ConnectInboxResponse;
    if (!response.ok) {
      return {
        ok: false,
        error: payload.error ?? {
          code: "gmail_fetch_failed",
          message: "Could not fetch Gmail inbox.",
        },
      };
    }

    return payload;
  } catch (error) {
    return {
      ok: false,
      error: {
        code: "gmail_fetch_failed",
        message:
          error instanceof Error ? error.message : "Could not fetch Gmail inbox.",
      },
    };
  }
}

export async function fetchGmailThread(
  request: FetchGmailThreadRequest,
): Promise<FetchGmailThreadResponse> {
  try {
    const response = await fetch("/api/inboxes/fetch-gmail-thread", {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    });
    const payload = (await response.json()) as FetchGmailThreadResponse;

    if (!response.ok || payload.ok === false) {
      return payload.ok === false
        ? payload
        : {
            ok: false,
            error: {
              code: "gmail_thread_fetch_failed",
              message: "Could not fetch this Gmail conversation.",
            },
          };
    }

    return payload;
  } catch (error) {
    return {
      ok: false,
      error: {
        code: "gmail_thread_fetch_failed",
        message:
          error instanceof Error
            ? error.message
            : "Could not fetch this Gmail conversation.",
      },
    };
  }
}
