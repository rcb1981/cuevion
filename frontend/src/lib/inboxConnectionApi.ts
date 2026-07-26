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

export type ImapConnectWireSettings = {
  host: string;
  port: string;
  ssl: true;
  username: string;
};

export type SmtpConnectWireSettings = {
  host: string;
  port: string;
  security: "ssl" | "starttls";
  username: string;
  useSameCredentials: boolean;
  password?: string;
};

export function resolveManagedMailboxIdentity({
  onboardingInboxId,
  serverMailboxId,
}: {
  onboardingInboxId: string;
  serverMailboxId?: string | null;
}) {
  const normalizedOnboardingInboxId = onboardingInboxId.trim();
  if (serverMailboxId === null || serverMailboxId === undefined) {
    return {
      onboardingInboxId: normalizedOnboardingInboxId,
      mailboxId: normalizedOnboardingInboxId,
    };
  }

  const normalizedServerMailboxId = serverMailboxId.trim();
  return {
    onboardingInboxId: normalizedOnboardingInboxId,
    mailboxId:
      normalizedServerMailboxId && normalizedServerMailboxId === serverMailboxId
        ? normalizedServerMailboxId
        : "",
  };
}

export function projectManagedMailboxAccountConfigIdentity({
  mailboxId,
  onboardingInboxId,
}: {
  mailboxId: string;
  onboardingInboxId?: string | null;
}) {
  const normalizedMailboxId = mailboxId.trim();
  const normalizedOnboardingInboxId = onboardingInboxId?.trim() ?? "";
  return {
    id: normalizedMailboxId === mailboxId ? normalizedMailboxId : "",
    ...(normalizedOnboardingInboxId &&
    normalizedOnboardingInboxId === onboardingInboxId
      ? { onboardingInboxId: normalizedOnboardingInboxId }
      : {}),
  };
}

export type InitialConnectInboxRequest = {
  mode: "initial";
  mailboxId: string;
  connection: {
    provider: "custom_imap";
    email: string;
    imap: ImapConnectWireSettings & {
      password: string;
    };
    smtp?: SmtpConnectWireSettings;
  };
  internalRole?: string | null;
  focusPreferences?: OnboardingState["focusPreferences"] | null;
  limit?: number | null;
};

export type OnboardingConnectInboxRequest = {
  mode: "onboarding";
  onboardingInboxId: string;
  serverMailboxId?: string;
  connection: {
    provider: "custom_imap";
    email: string;
    imap?: ImapConnectWireSettings & {
      password?: string;
    };
    smtp: SmtpConnectWireSettings;
  };
};

export type ReconnectInboxRequest = {
  mode: "reconnect";
  mailboxId: string;
  connection: {
    provider: "custom_imap";
    email: string;
    imap: ImapConnectWireSettings & {
      password?: string;
    };
    smtp?: SmtpConnectWireSettings;
  };
  internalRole?: string | null;
  focusPreferences?: OnboardingState["focusPreferences"] | null;
  limit?: number | null;
};

export type RefreshConnectInboxRequest = {
  mode: "refresh";
  mailboxId: string;
  focusPreferences?: OnboardingState["focusPreferences"] | null;
  limit?: number | null;
};

export type ConnectInboxRequest =
  | OnboardingConnectInboxRequest
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
  provider: Extract<ProviderId, "google" | "microsoft">;
  email?: string;
  inboxPosition?: string;
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

const MASKED_PASSWORD_PATTERN = /^[*•●]{6,}$/u;
const TEXT_PASSWORD_PLACEHOLDERS = new Set([
  "stored securely",
  "stored securely — leave blank to reuse",
]);

type ConnectRequestValidationCode =
  | "forbidden_client_authority"
  | "imap_password_required"
  | "smtp_configuration_incomplete"
  | "smtp_password_required";

class ConnectRequestValidationError extends Error {
  readonly code: ConnectRequestValidationCode;

  constructor(code: ConnectRequestValidationCode, message: string) {
    super(message);
    this.name = "ConnectRequestValidationError";
    this.code = code;
  }
}

function normalizeOneTimePassword(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const placeholderCandidate = value.trim();
  if (
    !placeholderCandidate ||
    MASKED_PASSWORD_PATTERN.test(placeholderCandidate) ||
    TEXT_PASSWORD_PLACEHOLDERS.has(placeholderCandidate.toLowerCase())
  ) {
    return undefined;
  }
  return value;
}

function containsClientCredentialGeneration(
  value: unknown,
  visited = new Set<object>(),
): boolean {
  if (!value || typeof value !== "object") return false;
  if (visited.has(value)) return false;
  visited.add(value);

  if (Array.isArray(value)) {
    return value.some((item) => containsClientCredentialGeneration(item, visited));
  }

  return Object.entries(value).some(([key, item]) => {
    const compactKey = key
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]/g, "");
    const isCredentialGeneration =
      (compactKey.includes("credential") || compactKey.includes("secret")) &&
      (
        compactKey.includes("version") ||
        compactKey.includes("generation") ||
        compactKey.includes("revision")
      );
    return isCredentialGeneration || containsClientCredentialGeneration(item, visited);
  });
}

function trimmedString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function isSecureSmtpSubmissionPair(
  port: string,
  security: SmtpConnectWireSettings["security"],
) {
  return (
    (security === "ssl" && port === "465") ||
    (security === "starttls" && port === "587")
  );
}

function normalizeSmtpWireSettings(
  value: CustomSmtpSettings | null | undefined,
): SmtpConnectWireSettings | undefined {
  if (value === null || value === undefined) return undefined;
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new ConnectRequestValidationError(
      "smtp_configuration_incomplete",
      "SMTP settings are incomplete.",
    );
  }

  const raw = value as unknown as Record<string, unknown>;
  const host = trimmedString(raw.host);
  const port = trimmedString(raw.port);
  const username = trimmedString(raw.username);
  const password = normalizeOneTimePassword(raw.password);
  const security =
    raw.security === "ssl" || raw.security === "starttls"
      ? raw.security
      : undefined;
  const useSameCredentials =
    typeof raw.useSameCredentials === "boolean"
      ? raw.useSameCredentials
      : undefined;
  const hasRecognizedInput = Boolean(host || port || username || password);

  if (!hasRecognizedInput) return undefined;
  if (
    !host ||
    !port ||
    !security ||
    useSameCredentials === undefined ||
    !isSecureSmtpSubmissionPair(port, security) ||
    (!useSameCredentials && !username)
  ) {
    throw new ConnectRequestValidationError(
      "smtp_configuration_incomplete",
      "SMTP settings are incomplete.",
    );
  }
  if (!useSameCredentials && !password) {
    throw new ConnectRequestValidationError(
      "smtp_password_required",
      "An SMTP password is required.",
    );
  }

  return {
    host,
    port,
    security,
    username: useSameCredentials ? "" : username,
    useSameCredentials,
    ...(!useSameCredentials && password ? { password } : {}),
  };
}

type ConnectInboxRequestOptions = {
  mode: "initial" | "reconnect";
  mailboxId: string;
  provider: ProviderId;
  email: string;
  customImap: CustomImapSettings;
  customSmtp?: CustomSmtpSettings;
  internalRole?: string | null;
  focusPreferences?: OnboardingState["focusPreferences"] | null;
  selectedInboxes?: string[] | null;
  limit?: number | null;
};

export function buildConnectInboxRequest(
  options: ConnectInboxRequestOptions,
): InitialConnectInboxRequest | ReconnectInboxRequest {
  if (containsClientCredentialGeneration(options)) {
    throw new ConnectRequestValidationError(
      "forbidden_client_authority",
      "Credential generation is server-owned.",
    );
  }

  const email = options.email.trim();
  const resolvedImapSettings = applyProviderDefaults(
    options.provider,
    options.customImap,
    email,
  );
  const imapPassword = normalizeOneTimePassword(resolvedImapSettings.password);
  if (options.mode === "initial" && !imapPassword) {
    throw new ConnectRequestValidationError(
      "imap_password_required",
      "An IMAP password is required.",
    );
  }
  const smtp = normalizeSmtpWireSettings(options.customSmtp);
  const imap: ImapConnectWireSettings = {
    host: trimmedString(resolvedImapSettings.host),
    port: trimmedString(resolvedImapSettings.port),
    ssl: true,
    username: usesEmailAsImapUsername(options.provider)
      ? email
      : trimmedString(resolvedImapSettings.username),
  };
  const requestMetadata = {
    mailboxId: options.mailboxId,
    internalRole: options.internalRole,
    focusPreferences: options.focusPreferences,
    limit: options.limit,
  };
  const connectionMetadata = {
    provider: "custom_imap" as const,
    email,
    ...(smtp ? { smtp } : {}),
  };

  if (options.mode === "initial") {
    return {
      mode: "initial",
      ...requestMetadata,
      connection: {
        ...connectionMetadata,
        imap: {
          ...imap,
          password: imapPassword as string,
        },
      },
    };
  }

  return {
    mode: "reconnect",
    ...requestMetadata,
    connection: {
      ...connectionMetadata,
      imap: {
        ...imap,
        ...(imapPassword ? { password: imapPassword } : {}),
      },
    },
  };
}

export function buildOnboardingConnectInboxRequest(options: {
  onboardingInboxId: string;
  serverMailboxId?: string | null;
  email: string;
  customImap: CustomImapSettings;
  customSmtp: CustomSmtpSettings;
  imapPassword?: string;
  smtpPassword?: string;
}): OnboardingConnectInboxRequest {
  if (containsClientCredentialGeneration(options)) {
    throw new ConnectRequestValidationError(
      "forbidden_client_authority",
      "Credential generation is server-owned.",
    );
  }

  const onboardingInboxId = options.onboardingInboxId.trim();
  const rawServerMailboxId = options.serverMailboxId;
  const serverMailboxId = rawServerMailboxId?.trim() ?? "";
  if (
    !onboardingInboxId ||
    (rawServerMailboxId !== null &&
      rawServerMailboxId !== undefined &&
      (!serverMailboxId || serverMailboxId !== rawServerMailboxId))
  ) {
    throw new ConnectRequestValidationError(
      "forbidden_client_authority",
      "The mailbox identity is invalid.",
    );
  }

  const imapPassword = normalizeOneTimePassword(
    options.imapPassword ?? options.customImap.password,
  );
  if (!serverMailboxId && !imapPassword) {
    throw new ConnectRequestValidationError(
      "imap_password_required",
      "An IMAP password is required.",
    );
  }
  const smtp = normalizeSmtpWireSettings(
    options.customSmtp
      ? {
          ...options.customSmtp,
          password:
            options.smtpPassword ?? options.customSmtp.password,
        }
      : options.customSmtp,
  );
  if (!smtp) {
    throw new ConnectRequestValidationError(
      "smtp_configuration_incomplete",
      "SMTP settings are incomplete.",
    );
  }

  return {
    mode: "onboarding",
    onboardingInboxId,
    ...(serverMailboxId ? { serverMailboxId } : {}),
    connection: {
      provider: "custom_imap",
      email: options.email.trim(),
      ...(!serverMailboxId
        ? {
            imap: {
              host: options.customImap.host.trim(),
              port: options.customImap.port.trim(),
              ssl: true as const,
              username: options.customImap.username.trim(),
              password: imapPassword as string,
            },
          }
        : {}),
      smtp,
    },
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
  provider: Extract<ProviderId, "google" | "microsoft">;
  email: string;
  inboxPosition?: string | null;
}): OAuthInboxRequest {
  const email = options.email.trim();
  const inboxPosition = options.inboxPosition?.trim();

  return {
    provider: options.provider,
    ...(email ? { email } : {}),
    ...(inboxPosition ? { inboxPosition } : {}),
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

const SAFE_IMAP_CONNECTION_ERROR_MESSAGE = "Could not connect to inbox.";
const PUBLIC_IMAP_ERROR_CODES = new Set([
  "connection_failed",
  "forbidden_client_authority",
  "imap_connection_failed",
  "invalid_credentials",
  "invalid_request",
  "mailbox_configuration_malformed",
  "mailbox_connection_conflict",
  "mailbox_secret_store_unavailable",
  "reconnect_required",
  "smtp_connection_failed",
]);

function safeImapConnectionError(
  error: ConnectInboxResponse["error"] | null | undefined,
): NonNullable<ConnectInboxResponse["error"]> {
  const code =
    typeof error?.code === "string" && PUBLIC_IMAP_ERROR_CODES.has(error.code)
      ? error.code
      : "connection_failed";
  return {
    code,
    message: SAFE_IMAP_CONNECTION_ERROR_MESSAGE,
    ...(typeof error?.fetched_count === "number"
      ? { fetched_count: error.fetched_count }
      : {}),
  };
}

export async function connectInboxWithImap(
  request: ConnectInboxRequest,
  signal?: AbortSignal,
): Promise<ConnectInboxResponse> {
  try {
    const response = await fetch("/api/inboxes/connect-imap", {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
      ...(signal ? { signal } : {}),
    });

    const payload = (await response.json()) as Partial<ConnectInboxResponse> | null;
    if (!response.ok || payload?.ok !== true) {
      return {
        ok: false,
        error: safeImapConnectionError(payload?.error),
      };
    }

    return {
      ...payload,
      ok: true,
    };
  } catch {
    return {
      ok: false,
      error: safeImapConnectionError(undefined),
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
  customSmtp?: CustomSmtpSettings;
  inboxPosition?: string | null;
  internalRole?: string | null;
  focusPreferences?: OnboardingState["focusPreferences"] | null;
  selectedInboxes?: string[] | null;
}): Promise<InboxConnectionAttemptResult> {
  const connectionMethod = getProviderConnectionMethod(options.provider);

  if (isOAuthConnectionProvider(options.provider)) {
    const response = await connectInboxWithOAuth(
      buildOAuthInboxRequest({
        provider: options.provider as Extract<ProviderId, "google" | "microsoft">,
        email: options.email,
        inboxPosition: options.inboxPosition,
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

    const authorizationUrl = response.authorizationUrl?.trim() ?? "";
    if (
      response.connectionStatus !== "waiting_for_authentication" ||
      !authorizationUrl
    ) {
      return {
        ok: false,
        connected: false,
        connectionMethod,
        connectionStatus: "connection_failed",
        connectionMessage: "OAuth did not return a valid authorization step.",
        oauthAuthorizationUrl: null,
        error: {
          code: "oauth_invalid_start_response",
          message: "OAuth did not return a valid authorization step.",
        },
      };
    }

    return {
      ok: true,
      connected: false,
      connectionMethod,
      connectionStatus: response.connectionStatus,
      connectionMessage: response.message ?? null,
      oauthAuthorizationUrl: authorizationUrl,
      messages: [],
    };
  }

  let request: InitialConnectInboxRequest | ReconnectInboxRequest;
  try {
    request = buildConnectInboxRequest({
      ...options,
      mode: options.imapMode,
    });
  } catch (error) {
    const validationError =
      error instanceof ConnectRequestValidationError ? error : null;
    const message =
      validationError?.message ?? "Connection settings are invalid.";
    return {
      ok: false,
      connected: false,
      connectionMethod,
      connectionStatus: "connection_failed",
      connectionMessage: message,
      oauthAuthorizationUrl: null,
      error: {
        code: validationError?.code ?? "invalid_request",
        message,
      },
    };
  }

  const response = await connectInboxWithImap(request);

  if (!response.ok) {
    const safeError = safeImapConnectionError(response.error);
    return {
      ok: false,
      connected: false,
      connectionMethod,
      connectionStatus: "connection_failed",
      connectionMessage: safeError.message,
      oauthAuthorizationUrl: null,
      error: safeError,
    };
  }

  return {
    ok: true,
    connected: false,
    connectionMethod,
    connectionStatus: "not_connected",
    connectionMessage: null,
    oauthAuthorizationUrl: null,
    messages: response.messages ?? [],
    uidValidity: response.uidValidity ?? null,
    warning: response.warning ?? null,
  };
}

export async function beginOnboardingInboxConnection(
  options: {
    onboardingInboxId: string;
    serverMailboxId?: string | null;
    email: string;
    customImap: CustomImapSettings;
    customSmtp: CustomSmtpSettings;
    imapPassword?: string;
    smtpPassword?: string;
  },
  signal?: AbortSignal,
): Promise<InboxConnectionAttemptResult> {
  if (options.customImap.ssl !== true) {
    return {
      ok: false,
      connected: false,
      connectionMethod: "imap",
      connectionStatus: "connection_failed",
      connectionMessage: "A secure IMAP connection is required.",
      oauthAuthorizationUrl: null,
      error: {
        code: "tls_required",
        message: "A secure IMAP connection is required.",
      },
    };
  }

  let request: OnboardingConnectInboxRequest;
  try {
    request = buildOnboardingConnectInboxRequest(options);
  } catch (error) {
    const validationError =
      error instanceof ConnectRequestValidationError ? error : null;
    const message =
      validationError?.message ?? "Connection settings are invalid.";
    return {
      ok: false,
      connected: false,
      connectionMethod: "imap",
      connectionStatus: "connection_failed",
      connectionMessage: message,
      oauthAuthorizationUrl: null,
      error: {
        code: validationError?.code ?? "invalid_request",
        message,
      },
    };
  }

  const response = await connectInboxWithImap(request, signal);

  if (!response.ok) {
    const safeError = response.error
      ? {
          ...response.error,
          message: "Could not connect to inbox.",
        }
      : undefined;
    return {
      ok: false,
      connected: false,
      connectionMethod: "imap",
      connectionStatus: "connection_failed",
      connectionMessage: "Could not connect to inbox.",
      oauthAuthorizationUrl: null,
      error: safeError,
    };
  }

  return {
    ok: true,
    connected: false,
    connectionMethod: "imap",
    connectionStatus: "not_connected",
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
