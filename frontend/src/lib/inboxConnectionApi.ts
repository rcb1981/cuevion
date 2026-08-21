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
import {
  resolveMessageNoiseAssessment,
  type MessageNoiseAssessment,
} from "./messageNoiseGate";
import {
  buildPrioritySemanticAssessmentWireRequest,
  buildPrioritySemanticCurrentLookupWireRequest,
  normalizePrioritySemanticEventRef,
  parsePrioritySemanticAssessmentResponse,
  type PrioritySemanticAssessmentRequest,
  type PrioritySemanticAssessmentResponse,
  type PrioritySemanticCurrentLookupRequest,
} from "./prioritySemanticState";

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
  serverMailboxId?: string;
  providerFolder?: string;
  imapUid?: string;
  uidValidity?: string;
  providerMessageId?: string;
  providerThreadId?: string;
  rfcMessageId?: string;
  labelIds?: string[];
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
} & Partial<MessageNoiseAssessment>;

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
  replyContext?: {
    sourceProviderMessageId: string;
  };
  imapReplyContext?: {
    sourceProviderFolder: string;
    sourceImapUid: string;
    sourceUidValidity: string;
  };
};

export const GMAIL_PROVIDER_IDENTIFIER_MAX_LENGTH = 256;

export function isValidGmailProviderIdentifier(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= GMAIL_PROVIDER_IDENTIFIER_MAX_LENGTH &&
    value === value.trim() &&
    /^[\x20-\x7e]+$/.test(value)
  );
}

export type GmailReplyComposeMode = "new" | "reply" | "reply_all" | "forward";

export function buildGmailReplyContext(options: {
  sendProvider: string | null | undefined;
  composeMode: GmailReplyComposeMode;
  mailboxId: string;
  sourceMessage?: {
    providerMessageId?: string | null;
    threadIdentityAuthority?: string | null;
    threadIdentityContext?: {
      provider?: string | null;
      mailboxId?: string | null;
    } | null;
  } | null;
}): SendGmailMessageRequest["replyContext"] {
  const rawSourceProviderMessageId = options.sourceMessage?.providerMessageId;
  const sourceProviderMessageId = rawSourceProviderMessageId?.trim();

  return options.sendProvider === "google" &&
    (options.composeMode === "reply" || options.composeMode === "reply_all") &&
    options.sourceMessage?.threadIdentityAuthority === "gmail" &&
    options.sourceMessage.threadIdentityContext?.provider === "google" &&
    options.sourceMessage.threadIdentityContext.mailboxId === options.mailboxId &&
    sourceProviderMessageId === rawSourceProviderMessageId &&
    isValidGmailProviderIdentifier(sourceProviderMessageId)
    ? { sourceProviderMessageId }
    : undefined;
}

export type ImapReplyContext = NonNullable<
  SendGmailMessageRequest["imapReplyContext"]
>;

function isValidImapReplyContext(value: unknown): value is ImapReplyContext {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }

  const context = value as Record<string, unknown>;
  return (
    isArchiveFolder(context.sourceProviderFolder) &&
    isCanonicalImapUid(context.sourceImapUid) &&
    isCanonicalUidValidity(context.sourceUidValidity)
  );
}

export function buildImapReplyContext(options: {
  sendProvider: string | null | undefined;
  composeMode: GmailReplyComposeMode;
  mailboxId: string;
  sourceMessage?: {
    serverMailboxId?: string | null;
    providerFolder?: string | null;
    imapUid?: string | null;
    uidValidity?: string | null;
    threadIdentityContext?: {
      provider?: string | null;
      mailboxId?: string | null;
    } | null;
  } | null;
}): SendGmailMessageRequest["imapReplyContext"] {
  const sourceMessage = options.sourceMessage;
  const mailboxId = options.mailboxId.trim();
  const threadIdentityContext = sourceMessage?.threadIdentityContext;
  const contextMatchesMailbox =
    threadIdentityContext === null ||
    threadIdentityContext === undefined ||
    (
      threadIdentityContext.provider === "custom_imap" &&
      threadIdentityContext.mailboxId === mailboxId
    );
  const replyContext = {
    sourceProviderFolder: sourceMessage?.providerFolder,
    sourceImapUid: sourceMessage?.imapUid,
    sourceUidValidity: sourceMessage?.uidValidity,
  };

  return options.sendProvider === "custom_imap" &&
    (options.composeMode === "reply" || options.composeMode === "reply_all") &&
    Boolean(mailboxId) &&
    mailboxId === options.mailboxId &&
    sourceMessage?.serverMailboxId === mailboxId &&
    contextMatchesMailbox &&
    isValidImapReplyContext(replyContext)
    ? replyContext
    : undefined;
}

export function buildSendInboxWireRequest(
  request: SendGmailMessageRequest,
): SendGmailMessageRequest {
  const sourceProviderMessageId =
    typeof request.replyContext?.sourceProviderMessageId === "string"
      ? request.replyContext.sourceProviderMessageId.trim()
      : "";
  const imapReplyContext = isValidImapReplyContext(request.imapReplyContext)
    ? {
        sourceProviderFolder: request.imapReplyContext.sourceProviderFolder,
        sourceImapUid: request.imapReplyContext.sourceImapUid,
        sourceUidValidity: request.imapReplyContext.sourceUidValidity,
      }
    : undefined;

  return {
    mailboxId: request.mailboxId,
    to: request.to,
    cc: request.cc,
    bcc: request.bcc,
    subject: request.subject,
    bodyHtml: request.bodyHtml,
    bodyText: request.bodyText,
    attachments: request.attachments,
    ...(sourceProviderMessageId
      ? { replyContext: { sourceProviderMessageId } }
      : {}),
    ...(imapReplyContext ? { imapReplyContext } : {}),
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

export type SendGmailMessageResponse = {
  ok: boolean;
  providerMessageId?: string;
  providerThreadId?: string;
  labelIds?: string[];
  semanticEventRef?: string;
  providerIdentityConfirmed?: boolean;
  threadContinuityConfirmed?: boolean;
  warning?: {
    code?: string;
    message?: string;
  };
  error?: {
    code?: string;
    message?: string;
  };
};

export const PRIORITY_SEMANTIC_ASSESSMENT_TIMEOUT_MS = 60_000;

function sentIdentityUnconfirmedResponse(): SendGmailMessageResponse {
  return {
    ok: true,
    providerIdentityConfirmed: false,
    threadContinuityConfirmed: false,
    warning: {
      code: "send_identity_unconfirmed",
      message: "The message was sent, but its provider identity could not be confirmed.",
    },
  };
}

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

export type ProviderTrashMessageRequest = {
  mailboxId: string;
  action: "trash";
  providerMessageId: string;
  sourceFolder: "INBOX";
};

export type ProviderTrashMessageReadback = {
  inSource: false;
  inTrash: true;
};

export type ProviderTrashMessageSuccess = {
  ok: true;
  action: "trash";
  provider: "gmail";
  mailboxId: string;
  providerMessageId: string;
  sourceFolder: "INBOX";
  destinationFolder: "TRASH";
  readback: ProviderTrashMessageReadback;
  error?: never;
};

export type ProviderTrashMessageMutationUnconfirmedResponse = {
  ok: false;
  status: "mutation_unconfirmed";
  action: "trash";
  provider: "gmail";
  mailboxId: string;
  providerMessageId: string;
  sourceFolder: "INBOX";
  destinationFolder: "TRASH";
  error: {
    code: "trash_mutation_unconfirmed";
    message: string;
  };
  readback?: never;
};

export type ProviderTrashMessageUncertainResponse =
  ProviderTrashMessageMutationUnconfirmedResponse;

export type ProviderTrashMessageFailureResponse = {
  ok: false;
  status?: never;
  action?: never;
  provider?: never;
  mailboxId?: never;
  providerMessageId?: never;
  sourceFolder?: never;
  destinationFolder?: never;
  readback?: never;
  error: {
    code: string;
    message: string;
  };
};

export type ProviderTrashMessageResponse =
  | ProviderTrashMessageSuccess
  | ProviderTrashMessageUncertainResponse
  | ProviderTrashMessageFailureResponse;

export type GmailArchiveMutationRequest = {
  mailboxId: string;
  messageId: string;
  action: "archive";
};

export type ImapArchiveMutationRequest = {
  mailboxId: string;
  folder: "INBOX";
  uid: string;
  uidValidity: string;
  action: "archive";
};

export type ArchiveMutationRequest =
  | GmailArchiveMutationRequest
  | ImapArchiveMutationRequest;

export type ArchiveMessagePreviewSnapshot = {
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
  bodyHtml?: string;
  attachments?: LiveInboxAttachmentSnapshot[];
  unread?: boolean;
  flagged?: boolean;
  category?: string | null;
  categorySource?: string | null;
  categoryConfidence?: string | null;
  signal?: string | null;
  ui_signal?: string | null;
  internalClassification?: string | null;
  final_visibility?: string | null;
  action?: string | null;
  v7_final_priority?: string | null;
  classifierVersion?: string | null;
} & Partial<MessageNoiseAssessment>;

export type GmailArchiveMessageSnapshot = ArchiveMessagePreviewSnapshot & {
  serverMailboxId: string;
  providerFolder: "Inbox" | "Archive";
  providerMessageId: string;
  providerThreadId: string;
  rfcMessageId?: string;
  labelIds: string[];
};

export type ImapArchiveMessageSnapshot = ArchiveMessagePreviewSnapshot & {
  serverMailboxId: string;
  providerFolder: string;
  imapUid: string;
  uidValidity: string;
  threadId: string;
  rfcMessageId?: string;
};

export type GmailProviderFolderSnapshot<
  Folder extends "Inbox" | "Archive" = "Inbox" | "Archive",
> = {
  serverMailboxId: string;
  providerFolder: Folder;
  uidValidity: "gmail-api";
  messages: Array<GmailArchiveMessageSnapshot & { providerFolder: Folder }>;
};

export type ImapProviderFolderSnapshot<Folder extends string = string> = {
  serverMailboxId: string;
  providerFolder: Folder;
  uidValidity: string;
  imapUidSet: string[];
  messages: Array<ImapArchiveMessageSnapshot & { providerFolder: Folder }>;
};

export type GmailInboxSnapshot = GmailProviderFolderSnapshot<"Inbox">;
export type GmailArchiveSnapshot = GmailProviderFolderSnapshot<"Archive">;
export type GmailTrashMessageSnapshot = ArchiveMessagePreviewSnapshot & {
  serverMailboxId: string;
  providerFolder: "Trash";
  providerMessageId: string;
  providerThreadId: string;
  rfcMessageId?: string;
  labelIds: string[];
};
export type GmailTrashSnapshot = {
  serverMailboxId: string;
  providerFolder: "Trash";
  uidValidity: "gmail-api";
  messages: GmailTrashMessageSnapshot[];
};
export type ImapInboxSnapshot = ImapProviderFolderSnapshot<"INBOX">;
export type ImapArchiveSnapshot = ImapProviderFolderSnapshot;
export type ArchiveFolderSnapshot =
  | GmailArchiveSnapshot
  | ImapArchiveSnapshot;

export type GmailTrashFetchSuccess = {
  ok: true;
  status: "ok";
  mailboxId: string;
  folder: GmailTrashSnapshot;
  error?: never;
};

export type GmailTrashFetchFailure = {
  ok: false;
  status?: never;
  mailboxId?: never;
  folder?: never;
  error: {
    code: string;
    message: string;
  };
};

export type GmailTrashFetchResponse =
  | GmailTrashFetchSuccess
  | GmailTrashFetchFailure;

export type GmailArchivedMessageIdentity = {
  serverMailboxId: string;
  providerMessageId: string;
  providerThreadId: string;
  providerFolder: "Archive";
  rfcMessageId?: string;
};

export type ImapArchivedMessageIdentity = {
  serverMailboxId: string;
  sourceProviderFolder: "INBOX";
  sourceImapUid: string;
  sourceUidValidity: string;
  providerFolder: string;
  imapUid: string;
  uidValidity: string;
  rfcMessageId?: string;
};

export type ArchivedMessageIdentity =
  | GmailArchivedMessageIdentity
  | ImapArchivedMessageIdentity;

export type GmailArchiveMutationIdentity = {
  serverMailboxId: string;
  providerMessageId: string;
  providerFolder: "Archive";
};

export type ImapArchiveMutationIdentity = {
  serverMailboxId: string;
  sourceProviderFolder: "INBOX";
  sourceImapUid: string;
  sourceUidValidity: string;
};

export type ArchiveMutationIdentity =
  | GmailArchiveMutationIdentity
  | ImapArchiveMutationIdentity;

export type ArchiveClientError = {
  code: string;
  message: string;
};

export type ArchiveFailureResponse = {
  ok: false;
  status?: never;
  action?: never;
  mailboxId?: never;
  archivedMessageIdentity?: never;
  folders?: never;
  folder?: never;
  error: ArchiveClientError;
};

export type GmailArchiveMutationDelta = {
  Inbox: {
    removeProviderMessageId: string;
  };
  Archive: {
    upsertMessage: GmailArchiveMessageSnapshot & {
      providerFolder: "Archive";
    };
  };
};

export type GmailArchiveMutationSuccess = {
  ok: true;
  status: "ok";
  action: "archive";
  mailboxId: string;
  archivedMessageIdentity: GmailArchivedMessageIdentity;
  delta: GmailArchiveMutationDelta;
  error?: never;
};

export type ImapArchiveMutationSuccess = {
  ok: true;
  status: "ok";
  action: "archive";
  mailboxId: string;
  archivedMessageIdentity: ImapArchivedMessageIdentity;
  folders: {
    Inbox: ImapInboxSnapshot;
    Archive: ImapArchiveSnapshot;
  };
  error?: never;
};

export type ArchiveMutationConfirmedReadbackUncertainResponse = {
  ok: false;
  status: "mutation_confirmed_readback_failed";
  action: "archive";
  mailboxId: string;
  archivedMessageIdentity: ArchiveMutationIdentity;
  error: ArchiveClientError & {
    code: "archive_readback_failed";
  };
  folders?: never;
  folder?: never;
};

export type GmailArchiveMutationUnconfirmedResponse = {
  ok: false;
  status: "mutation_unconfirmed";
  action: "archive";
  mailboxId: string;
  archivedMessageIdentity: GmailArchiveMutationIdentity;
  error: ArchiveClientError & {
    code: "gmail_archive_unconfirmed";
  };
  delta?: never;
  folders?: never;
  folder?: never;
};

export type ArchiveMutationUncertainResponse =
  | ArchiveMutationConfirmedReadbackUncertainResponse
  | GmailArchiveMutationUnconfirmedResponse;

export type ArchiveMutationResponse =
  | GmailArchiveMutationSuccess
  | ImapArchiveMutationSuccess
  | ArchiveMutationUncertainResponse
  | ArchiveFailureResponse;

export type GmailArchiveFetchSuccess = {
  ok: true;
  status: "ok";
  mailboxId: string;
  folder: GmailArchiveSnapshot;
  error?: never;
};

export type ImapArchiveFetchSuccess = {
  ok: true;
  status: "ok";
  mailboxId: string;
  folder: ImapArchiveSnapshot;
  error?: never;
};

export type ArchiveFetchResponse =
  | GmailArchiveFetchSuccess
  | ImapArchiveFetchSuccess
  | ArchiveFailureResponse;

const MAX_TRASH_RESPONSE_BYTES = 64 * 1024;
const MAX_GMAIL_TRASH_FETCH_RESPONSE_BYTES = 10 * 1024 * 1024;
const MAX_GMAIL_TRASH_SNAPSHOT_MESSAGES = 100;
const PUBLIC_TRASH_ERROR_CODES = new Set([
  "gmail_connection_not_found",
  "gmail_connection_not_ready",
  "gmail_modify_scope_required",
  "gmail_permission_denied",
  "gmail_rate_limited",
  "gmail_refresh_unavailable",
  "gmail_token_store_unavailable",
  "gmail_trash_failed",
  "internal_error",
  "invalid_trash_request",
  "mailbox_ownership_unavailable",
  "reconnect_required",
  "trash_provider_not_supported",
  "trash_source_invalid",
  "trash_source_unconfirmed",
  "unauthorized",
  "unsupported_provider",
  "user_config_store_unavailable",
]);
const PUBLIC_GMAIL_TRASH_FETCH_ERROR_CODES = new Set([
  "gmail_connection_not_found",
  "gmail_connection_not_ready",
  "gmail_fetch_failed",
  "gmail_permission_denied",
  "gmail_rate_limited",
  "gmail_refresh_unavailable",
  "gmail_response_invalid",
  "gmail_response_too_large",
  "gmail_token_store_unavailable",
  "gmail_unavailable",
  "internal_error",
  "invalid_mailbox_id",
  "invalid_request",
  "mailbox_ownership_unavailable",
  "reconnect_required",
  "trash_snapshot_failed",
  "unauthorized",
  "unsupported_provider",
  "user_config_store_unavailable",
]);
const SAFE_TRASH_ERROR_MESSAGE =
  "Could not complete this Trash request safely.";
const SAFE_TRASH_MUTATION_UNCONFIRMED_MESSAGE =
  "Trash may have completed, but provider confirmation was not definitive.";
const SAFE_GMAIL_TRASH_FETCH_ERROR_MESSAGE =
  "Could not load Trash from this Gmail mailbox safely.";

const MAX_ARCHIVE_RESPONSE_BYTES = 10 * 1024 * 1024;
const MAX_ARCHIVE_SNAPSHOT_MESSAGES = 100;
const MAX_IMAP_UID_SET_SIZE = 100_000;
const MAX_IMAP_UID = "4294967295";
const MAX_ARCHIVE_IDENTIFIER_LENGTH = 512;
const GMAIL_ARCHIVE_EXCLUDED_LABELS = new Set([
  "INBOX",
  "TRASH",
  "SPAM",
  "DRAFT",
  "SENT",
]);
const PUBLIC_ARCHIVE_ERROR_CODES = new Set([
  "archive_folder_ambiguous",
  "archive_folder_unavailable",
  "archive_message_not_found",
  "archive_move_failed",
  "archive_move_unconfirmed",
  "archive_move_unsupported",
  "archive_snapshot_failed",
  "forbidden_connection_fields",
  "gmail_archive_failed",
  "gmail_archive_unconfirmed",
  "gmail_fetch_failed",
  "gmail_modify_scope_required",
  "gmail_permission_denied",
  "gmail_rate_limited",
  "gmail_response_invalid",
  "gmail_response_too_large",
  "gmail_unavailable",
  "imap_archive_failed",
  "invalid_credentials",
  "invalid_imap_uid",
  "invalid_request",
  "invalid_source_folder",
  "invalid_uid_validity",
  "mailbox_configuration_malformed",
  "mailbox_not_found",
  "reconnect_required",
  "source_folder_unavailable",
  "uid_validity_changed",
  "uid_validity_unavailable",
  "unsupported_provider",
]);
const SAFE_ARCHIVE_ERROR_MESSAGE =
  "Could not complete this Archive request safely.";
const SAFE_ARCHIVE_UNCERTAIN_MESSAGE =
  "Archive was confirmed, but the latest mailbox state could not be verified.";
const SAFE_GMAIL_ARCHIVE_UNCONFIRMED_MESSAGE =
  "Archive may have completed; mailbox status is being refreshed.";
const ARCHIVE_PREVIEW_FIELDS = new Set([
  "id",
  "sender",
  "subject",
  "snippet",
  "from",
  "to",
  "cc",
  "timestamp",
  "createdAt",
  "body",
  "bodyHtml",
  "attachments",
  "unread",
  "flagged",
  "category",
  "categorySource",
  "categoryConfidence",
  "signal",
  "ui_signal",
  "internalClassification",
  "final_visibility",
  "action",
  "v7_final_priority",
  "classifierVersion",
  "noiseDisposition",
  "noiseConfidence",
  "noiseReasons",
]);
const ARCHIVE_NULLABLE_PREVIEW_FIELDS = new Set([
  "category",
  "categorySource",
  "categoryConfidence",
  "signal",
  "ui_signal",
  "internalClassification",
  "final_visibility",
  "action",
  "v7_final_priority",
  "classifierVersion",
]);
const ARCHIVE_ATTACHMENT_FIELDS = new Set([
  "id",
  "name",
  "mimeType",
  "size",
  "contentId",
  "disposition",
  "inlineSrc",
]);
const GMAIL_ARCHIVE_MESSAGE_FIELDS = new Set([
  ...ARCHIVE_PREVIEW_FIELDS,
  "serverMailboxId",
  "providerFolder",
  "providerMessageId",
  "providerThreadId",
  "rfcMessageId",
  "labelIds",
]);
const GMAIL_TRASH_MESSAGE_FIELDS = new Set([
  ...ARCHIVE_PREVIEW_FIELDS,
  "serverMailboxId",
  "providerFolder",
  "providerMessageId",
  "providerThreadId",
  "rfcMessageId",
  "labelIds",
]);
const IMAP_ARCHIVE_MESSAGE_FIELDS = new Set([
  ...ARCHIVE_PREVIEW_FIELDS,
  "serverMailboxId",
  "providerFolder",
  "imapUid",
  "uidValidity",
  "threadId",
  "rfcMessageId",
]);
const FORBIDDEN_ARCHIVE_RESPONSE_KEYS = new Set([
  "authorization",
  "connection",
  "cookie",
  "fingerprint",
  "headers",
  "host",
  "identities",
  "identity",
  "mailboxconfig",
  "mailboxemail",
  "owneremail",
  "password",
  "payload",
  "port",
  "providererror",
  "providerpayload",
  "raw",
  "rawproviderresponse",
  "rawresponse",
  "session",
  "ssl",
  "userid",
  "username",
]);
const FORBIDDEN_ARCHIVE_RESPONSE_KEY_FRAGMENTS = [
  "credential",
  "password",
  "secret",
  "token",
];

function archiveFailure(code: string, message: string): ArchiveFailureResponse {
  return {
    ok: false,
    error: {
      code,
      message,
    },
  };
}

function isArchiveRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function hasExactArchiveKeys(
  value: Record<string, unknown>,
  required: readonly string[],
  optional: readonly string[] = [],
) {
  const keys = Object.keys(value);
  const allowed = new Set([...required, ...optional]);
  return (
    required.every((key) => Object.prototype.hasOwnProperty.call(value, key)) &&
    keys.every((key) => allowed.has(key))
  );
}

function hasOnlyArchiveKeys(
  value: Record<string, unknown>,
  allowed: ReadonlySet<string>,
) {
  return Object.keys(value).every((key) => allowed.has(key));
}

function compactArchiveKey(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function containsForbiddenArchiveResponseField(
  value: unknown,
  visited = new Set<object>(),
): boolean {
  if (!value || typeof value !== "object") return false;
  if (visited.has(value)) return false;
  visited.add(value);
  if (Array.isArray(value)) {
    return value.some((item) =>
      containsForbiddenArchiveResponseField(item, visited));
  }
  return Object.entries(value).some(([key, item]) => {
    const compactKey = compactArchiveKey(key);
    return (
      FORBIDDEN_ARCHIVE_RESPONSE_KEYS.has(compactKey) ||
      FORBIDDEN_ARCHIVE_RESPONSE_KEY_FRAGMENTS.some((fragment) =>
        compactKey.includes(fragment)) ||
      containsForbiddenArchiveResponseField(item, visited)
    );
  });
}

function isArchiveIdentifier(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length >= 1 &&
    value.length <= MAX_ARCHIVE_IDENTIFIER_LENGTH &&
    value === value.trim() &&
    ![...value].some((character) => {
      const code = character.charCodeAt(0);
      return code < 32 || code === 127;
    })
  );
}

function isGmailArchiveMessageId(value: unknown): value is string {
  if (!isArchiveIdentifier(value) || !/^[\x20-\x7e]+$/.test(value)) return false;
  const lowered = value.toLowerCase();
  return (
    !value.includes("@") &&
    !value.includes("<") &&
    !value.includes(">") &&
    !["imap-uid-", "rfc-", "thread-"].some((prefix) =>
      lowered.startsWith(prefix))
  );
}

function isArchiveFolder(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length >= 1 &&
    value.length <= 16_384 &&
    value === value.trim() &&
    !value.includes("\r") &&
    !value.includes("\n") &&
    !value.includes("\0")
  );
}

function isCanonicalUidValidity(value: unknown): value is string {
  return typeof value === "string" && /^[1-9][0-9]{0,19}$/.test(value);
}

function isCanonicalImapUid(value: unknown): value is string {
  if (typeof value !== "string" || !/^[1-9][0-9]*$/.test(value)) return false;
  return (
    value.length < MAX_IMAP_UID.length ||
    (value.length === MAX_IMAP_UID.length && value <= MAX_IMAP_UID)
  );
}

function isArchiveAttachment(value: unknown): value is LiveInboxAttachmentSnapshot {
  if (
    !isArchiveRecord(value) ||
    !hasOnlyArchiveKeys(value, ARCHIVE_ATTACHMENT_FIELDS) ||
    typeof value.id !== "string" ||
    typeof value.name !== "string"
  ) {
    return false;
  }
  for (const key of ["mimeType", "contentId", "disposition", "inlineSrc"]) {
    if (value[key] !== undefined && typeof value[key] !== "string") return false;
  }
  return (
    value.size === undefined ||
    (
      typeof value.size === "number" &&
      Number.isFinite(value.size) &&
      value.size >= 0
    )
  );
}

function archivePreviewFieldsAreValid(value: Record<string, unknown>) {
  for (const key of [
    "id",
    "sender",
    "subject",
    "snippet",
    "from",
    "to",
    "timestamp",
    "createdAt",
  ]) {
    if (typeof value[key] !== "string") {
      return false;
    }
  }
  if (
    !Array.isArray(value.body) ||
    value.body.some((part) => typeof part !== "string")
  ) {
    return false;
  }

  const noiseAssessmentFieldNames = [
    "noiseDisposition",
    "noiseConfidence",
    "noiseReasons",
  ] as const;
  const hasNoiseAssessment = noiseAssessmentFieldNames.some((key) =>
    Object.prototype.hasOwnProperty.call(value, key),
  );
  if (
    hasNoiseAssessment &&
    !resolveMessageNoiseAssessment({
      noiseDisposition: Object.prototype.hasOwnProperty.call(
        value,
        "noiseDisposition",
      )
        ? value.noiseDisposition
        : undefined,
      noiseConfidence: Object.prototype.hasOwnProperty.call(
        value,
        "noiseConfidence",
      )
        ? value.noiseConfidence
        : undefined,
      noiseReasons: Object.prototype.hasOwnProperty.call(value, "noiseReasons")
        ? value.noiseReasons
        : undefined,
    })
  ) {
    return false;
  }

  for (const [key, item] of Object.entries(value)) {
    if (!ARCHIVE_PREVIEW_FIELDS.has(key)) continue;
    if ((noiseAssessmentFieldNames as readonly string[]).includes(key)) {
      continue;
    }
    if (key === "body") {
      if (!Array.isArray(item) || item.some((part) => typeof part !== "string")) {
        return false;
      }
      continue;
    }
    if (key === "attachments") {
      if (
        !Array.isArray(item) ||
        item.length > 1_000 ||
        item.some((attachment) => !isArchiveAttachment(attachment))
      ) {
        return false;
      }
      continue;
    }
    if (key === "unread" || key === "flagged") {
      if (typeof item !== "boolean") return false;
      continue;
    }
    if (
      typeof item !== "string" &&
      !(item === null && ARCHIVE_NULLABLE_PREVIEW_FIELDS.has(key))
    ) {
      return false;
    }
  }
  return true;
}

function gmailArchiveMessageIsValid(
  value: unknown,
  mailboxId: string,
  providerFolder: "Inbox" | "Archive",
  archiveLabelPolicy: "folder_membership" | "inbox_removal" =
    "folder_membership",
): value is GmailArchiveMessageSnapshot {
  if (
    !isArchiveRecord(value) ||
    !hasOnlyArchiveKeys(value, GMAIL_ARCHIVE_MESSAGE_FIELDS) ||
    !archivePreviewFieldsAreValid(value) ||
    value.serverMailboxId !== mailboxId ||
    value.providerFolder !== providerFolder ||
    !isArchiveIdentifier(value.providerMessageId) ||
    !isArchiveIdentifier(value.providerThreadId) ||
    !Array.isArray(value.labelIds) ||
    value.labelIds.length > 1_000 ||
    value.labelIds.some((labelId) => !isArchiveIdentifier(labelId)) ||
    new Set(value.labelIds).size !== value.labelIds.length ||
    (
      value.rfcMessageId !== undefined &&
      !isArchiveIdentifier(value.rfcMessageId)
    )
  ) {
    return false;
  }
  const labels = new Set(value.labelIds.map((labelId) => labelId.toUpperCase()));
  return providerFolder === "Inbox"
    ? labels.has("INBOX")
    : archiveLabelPolicy === "inbox_removal"
      ? !labels.has("INBOX")
      : ![...GMAIL_ARCHIVE_EXCLUDED_LABELS].some((label) => labels.has(label));
}

function gmailFolderSnapshotIsValid<Folder extends "Inbox" | "Archive">(
  value: unknown,
  mailboxId: string,
  providerFolder: Folder,
): value is GmailProviderFolderSnapshot<Folder> {
  if (
    !isArchiveRecord(value) ||
    !hasExactArchiveKeys(
      value,
      ["serverMailboxId", "providerFolder", "uidValidity", "messages"],
    ) ||
    value.serverMailboxId !== mailboxId ||
    value.providerFolder !== providerFolder ||
    value.uidValidity !== "gmail-api" ||
    !Array.isArray(value.messages) ||
    value.messages.length > MAX_ARCHIVE_SNAPSHOT_MESSAGES
  ) {
    return false;
  }
  const providerMessageIds = new Set<string>();
  for (const message of value.messages) {
    if (
      !gmailArchiveMessageIsValid(message, mailboxId, providerFolder) ||
      providerMessageIds.has(message.providerMessageId)
    ) {
      return false;
    }
    providerMessageIds.add(message.providerMessageId);
  }
  return true;
}

function gmailTrashMessageIsValid(
  value: unknown,
  mailboxId: string,
): value is GmailTrashMessageSnapshot {
  if (
    !isArchiveRecord(value) ||
    !hasOnlyArchiveKeys(value, GMAIL_TRASH_MESSAGE_FIELDS) ||
    !archivePreviewFieldsAreValid(value) ||
    value.serverMailboxId !== mailboxId ||
    value.providerFolder !== "Trash" ||
    !isGmailArchiveMessageId(value.providerMessageId) ||
    !isArchiveIdentifier(value.providerThreadId) ||
    !Array.isArray(value.labelIds) ||
    value.labelIds.length > 1_000 ||
    value.labelIds.some((labelId) => !isArchiveIdentifier(labelId)) ||
    new Set(value.labelIds).size !== value.labelIds.length ||
    (
      value.rfcMessageId !== undefined &&
      !isArchiveIdentifier(value.rfcMessageId)
    )
  ) {
    return false;
  }

  const labels = new Set(value.labelIds.map((labelId) => labelId.toUpperCase()));
  return labels.has("TRASH") && !labels.has("INBOX");
}

function gmailTrashSnapshotIsValid(
  value: unknown,
  mailboxId: string,
): value is GmailTrashSnapshot {
  if (
    !isArchiveRecord(value) ||
    !hasExactArchiveKeys(
      value,
      ["serverMailboxId", "providerFolder", "uidValidity", "messages"],
    ) ||
    value.serverMailboxId !== mailboxId ||
    value.providerFolder !== "Trash" ||
    value.uidValidity !== "gmail-api" ||
    !Array.isArray(value.messages) ||
    value.messages.length > MAX_GMAIL_TRASH_SNAPSHOT_MESSAGES
  ) {
    return false;
  }

  const providerMessageIds = new Set<string>();
  for (const message of value.messages) {
    if (
      !gmailTrashMessageIsValid(message, mailboxId) ||
      providerMessageIds.has(message.providerMessageId)
    ) {
      return false;
    }
    providerMessageIds.add(message.providerMessageId);
  }
  return true;
}

function imapArchiveMessageIsValid(
  value: unknown,
  mailboxId: string,
  providerFolder: string,
  uidValidity: string,
  knownUids: ReadonlySet<string>,
): value is ImapArchiveMessageSnapshot {
  return (
    isArchiveRecord(value) &&
    hasOnlyArchiveKeys(value, IMAP_ARCHIVE_MESSAGE_FIELDS) &&
    archivePreviewFieldsAreValid(value) &&
    value.serverMailboxId === mailboxId &&
    value.providerFolder === providerFolder &&
    value.uidValidity === uidValidity &&
    isCanonicalImapUid(value.imapUid) &&
    knownUids.has(value.imapUid) &&
    isArchiveIdentifier(value.threadId) &&
    (
      value.rfcMessageId === undefined ||
      isArchiveIdentifier(value.rfcMessageId)
    )
  );
}

function imapFolderSnapshotIsValid<Folder extends string>(
  value: unknown,
  mailboxId: string,
  providerFolder: Folder,
): value is ImapProviderFolderSnapshot<Folder> {
  if (
    !isArchiveRecord(value) ||
    !hasExactArchiveKeys(
      value,
      [
        "serverMailboxId",
        "providerFolder",
        "uidValidity",
        "imapUidSet",
        "messages",
      ],
    ) ||
    value.serverMailboxId !== mailboxId ||
    value.providerFolder !== providerFolder ||
    !isCanonicalUidValidity(value.uidValidity) ||
    !Array.isArray(value.imapUidSet) ||
    !Array.isArray(value.messages) ||
    value.messages.length > MAX_ARCHIVE_SNAPSHOT_MESSAGES
  ) {
    return false;
  }
  const imapUidSet = value.imapUidSet;
  if (
    imapUidSet.length > MAX_IMAP_UID_SET_SIZE ||
    imapUidSet.some((uid) => !isCanonicalImapUid(uid)) ||
    new Set(imapUidSet).size !== imapUidSet.length ||
    !imapUidSet.every(
      (uid, index) =>
        index === 0 || Number(imapUidSet[index - 1]) < Number(uid),
    )
  ) {
    return false;
  }
  const knownUids = new Set(imapUidSet);
  const messageUids = new Set<string>();
  for (const message of value.messages) {
    if (
      !imapArchiveMessageIsValid(
        message,
        mailboxId,
        providerFolder,
        value.uidValidity,
        knownUids,
      ) ||
      messageUids.has(message.imapUid)
    ) {
      return false;
    }
    messageUids.add(message.imapUid);
  }
  return true;
}

function gmailArchivedIdentityIsValid(
  value: unknown,
  request: GmailArchiveMutationRequest,
): value is GmailArchivedMessageIdentity {
  return (
    isArchiveRecord(value) &&
    hasExactArchiveKeys(
      value,
      [
        "serverMailboxId",
        "providerMessageId",
        "providerThreadId",
        "providerFolder",
      ],
      ["rfcMessageId"],
    ) &&
    value.serverMailboxId === request.mailboxId &&
    value.providerMessageId === request.messageId &&
    isArchiveIdentifier(value.providerThreadId) &&
    value.providerFolder === "Archive" &&
    (
      value.rfcMessageId === undefined ||
      isArchiveIdentifier(value.rfcMessageId)
    )
  );
}

function imapArchivedIdentityIsValid(
  value: unknown,
  request: ImapArchiveMutationRequest,
): value is ImapArchivedMessageIdentity {
  return (
    isArchiveRecord(value) &&
    hasExactArchiveKeys(
      value,
      [
        "serverMailboxId",
        "sourceProviderFolder",
        "sourceImapUid",
        "sourceUidValidity",
        "providerFolder",
        "imapUid",
        "uidValidity",
      ],
      ["rfcMessageId"],
    ) &&
    value.serverMailboxId === request.mailboxId &&
    value.sourceProviderFolder === request.folder &&
    value.sourceImapUid === request.uid &&
    value.sourceUidValidity === request.uidValidity &&
    isArchiveFolder(value.providerFolder) &&
    value.providerFolder !== request.folder &&
    isCanonicalImapUid(value.imapUid) &&
    isCanonicalUidValidity(value.uidValidity) &&
    (
      value.rfcMessageId === undefined ||
      isArchiveIdentifier(value.rfcMessageId)
    )
  );
}

function providerTrashFailure(
  code: string,
  message: string,
): ProviderTrashMessageFailureResponse {
  return {
    ok: false,
    error: {
      code,
      message,
    },
  };
}

function providerTrashMessageRequestBody(
  request: ProviderTrashMessageRequest,
): ProviderTrashMessageRequest | null {
  if (
    !isArchiveRecord(request) ||
    !hasExactArchiveKeys(request, [
      "mailboxId",
      "action",
      "providerMessageId",
      "sourceFolder",
    ]) ||
    !isArchiveIdentifier(request.mailboxId) ||
    request.action !== "trash" ||
    !isGmailArchiveMessageId(request.providerMessageId) ||
    request.sourceFolder !== "INBOX"
  ) {
    return null;
  }

  return {
    mailboxId: request.mailboxId,
    action: "trash",
    providerMessageId: request.providerMessageId,
    sourceFolder: "INBOX",
  };
}

function isSafeTrashErrorMessage(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.trim().length >= 1 &&
    value.length <= 2_048
  );
}

function providerTrashErrorFromPayload(
  value: unknown,
): ProviderTrashMessageFailureResponse | null {
  if (
    containsForbiddenArchiveResponseField(value) ||
    !isArchiveRecord(value) ||
    !hasExactArchiveKeys(value, ["ok", "error"]) ||
    value.ok !== false ||
    !isArchiveRecord(value.error) ||
    !hasExactArchiveKeys(value.error, ["code", "message"]) ||
    typeof value.error.code !== "string" ||
    !PUBLIC_TRASH_ERROR_CODES.has(value.error.code) ||
    !isSafeTrashErrorMessage(value.error.message)
  ) {
    return null;
  }

  return providerTrashFailure(value.error.code, SAFE_TRASH_ERROR_MESSAGE);
}

function providerTrashUncertainResponse(
  value: unknown,
  request: ProviderTrashMessageRequest,
): ProviderTrashMessageUncertainResponse | null {
  if (
    containsForbiddenArchiveResponseField(value) ||
    !isArchiveRecord(value) ||
    !hasExactArchiveKeys(value, [
      "ok",
      "status",
      "action",
      "provider",
      "mailboxId",
      "providerMessageId",
      "sourceFolder",
      "destinationFolder",
      "error",
    ]) ||
    value.ok !== false ||
    value.action !== "trash" ||
    value.provider !== "gmail" ||
    value.mailboxId !== request.mailboxId ||
    value.providerMessageId !== request.providerMessageId ||
    value.sourceFolder !== "INBOX" ||
    value.destinationFolder !== "TRASH" ||
    !isArchiveRecord(value.error) ||
    !hasExactArchiveKeys(value.error, ["code", "message"]) ||
    !isSafeTrashErrorMessage(value.error.message)
  ) {
    return null;
  }

  if (
    value.status === "mutation_unconfirmed" &&
    value.error.code === "trash_mutation_unconfirmed"
  ) {
    return providerTrashUnconfirmed(request);
  }

  return null;
}

function providerTrashUnconfirmed(
  request: ProviderTrashMessageRequest,
): ProviderTrashMessageUncertainResponse {
  return {
    ok: false,
    status: "mutation_unconfirmed",
    action: "trash",
    provider: "gmail",
    mailboxId: request.mailboxId,
    providerMessageId: request.providerMessageId,
    sourceFolder: "INBOX",
    destinationFolder: "TRASH",
    error: {
      code: "trash_mutation_unconfirmed",
      message: SAFE_TRASH_MUTATION_UNCONFIRMED_MESSAGE,
    },
  };
}

function providerTrashSuccessResponse(
  value: unknown,
  request: ProviderTrashMessageRequest,
): ProviderTrashMessageSuccess | null {
  if (
    containsForbiddenArchiveResponseField(value) ||
    !isArchiveRecord(value) ||
    !hasExactArchiveKeys(value, [
      "ok",
      "action",
      "provider",
      "mailboxId",
      "providerMessageId",
      "sourceFolder",
      "destinationFolder",
      "readback",
    ]) ||
    value.ok !== true ||
    value.action !== "trash" ||
    value.provider !== "gmail" ||
    value.mailboxId !== request.mailboxId ||
    value.providerMessageId !== request.providerMessageId ||
    value.sourceFolder !== "INBOX" ||
    value.destinationFolder !== "TRASH" ||
    !isArchiveRecord(value.readback) ||
    !hasExactArchiveKeys(value.readback, ["inSource", "inTrash"]) ||
    value.readback.inSource !== false ||
    value.readback.inTrash !== true
  ) {
    return null;
  }

  return {
    ok: true,
    action: "trash",
    provider: "gmail",
    mailboxId: request.mailboxId,
    providerMessageId: request.providerMessageId,
    sourceFolder: "INBOX",
    destinationFolder: "TRASH",
    readback: {
      inSource: false,
      inTrash: true,
    },
  };
}

function archiveMutationRequestBody(
  request: ArchiveMutationRequest,
): GmailArchiveMutationRequest | ImapArchiveMutationRequest | null {
  if (!isArchiveRecord(request) || request.action !== "archive") return null;
  if ("messageId" in request) {
    if (
      !hasExactArchiveKeys(request, ["mailboxId", "messageId", "action"]) ||
      !isArchiveIdentifier(request.mailboxId) ||
      !isGmailArchiveMessageId(request.messageId)
    ) {
      return null;
    }
    return {
      mailboxId: request.mailboxId,
      messageId: request.messageId,
      action: "archive",
    };
  }
  if (
    !hasExactArchiveKeys(
      request,
      ["mailboxId", "folder", "uid", "uidValidity", "action"],
    ) ||
    !isArchiveIdentifier(request.mailboxId) ||
    request.folder !== "INBOX" ||
    !isCanonicalImapUid(request.uid) ||
    !isCanonicalUidValidity(request.uidValidity)
  ) {
    return null;
  }
  return {
    mailboxId: request.mailboxId,
    folder: "INBOX",
    uid: request.uid,
    uidValidity: request.uidValidity,
    action: "archive",
  };
}

function archiveErrorFromPayload(
  value: unknown,
): ArchiveFailureResponse | null {
  if (
    containsForbiddenArchiveResponseField(value) ||
    !isArchiveRecord(value) ||
    !hasExactArchiveKeys(value, ["ok", "error"]) ||
    value.ok !== false ||
    !isArchiveRecord(value.error) ||
    !hasExactArchiveKeys(value.error, ["code", "message"]) ||
    typeof value.error.code !== "string" ||
    !PUBLIC_ARCHIVE_ERROR_CODES.has(value.error.code) ||
    typeof value.error.message !== "string" ||
    value.error.message.length < 1 ||
    value.error.message.length > 2_048
  ) {
    return null;
  }
  return archiveFailure(
    value.error.code,
    value.error.code === "reconnect_required"
      ? "Reconnect this mailbox to continue."
      : SAFE_ARCHIVE_ERROR_MESSAGE,
  );
}

function trustedUncertainIdentity(
  request: ArchiveMutationRequest,
): ArchiveMutationIdentity {
  if ("messageId" in request) {
    return {
      serverMailboxId: request.mailboxId,
      providerMessageId: request.messageId,
      providerFolder: "Archive",
    };
  }
  return {
    serverMailboxId: request.mailboxId,
    sourceProviderFolder: request.folder,
    sourceImapUid: request.uid,
    sourceUidValidity: request.uidValidity,
  };
}

function archiveUncertainResponse(
  value: unknown,
  request: ArchiveMutationRequest,
): ArchiveMutationUncertainResponse | null {
  if (!isArchiveRecord(value)) {
    return null;
  }

  if (
    value.ok === false &&
    value.status === "mutation_confirmed_readback_failed" &&
    value.action === "archive" &&
    value.mailboxId === request.mailboxId &&
    isArchiveRecord(value.error) &&
    value.error.code === "archive_readback_failed"
  ) {
    return {
      ok: false,
      status: "mutation_confirmed_readback_failed",
      action: "archive",
      mailboxId: request.mailboxId,
      archivedMessageIdentity: trustedUncertainIdentity(request),
      error: {
        code: "archive_readback_failed",
        message: SAFE_ARCHIVE_UNCERTAIN_MESSAGE,
      },
    };
  }

  if (
    !("messageId" in request) ||
    containsForbiddenArchiveResponseField(value) ||
    !hasExactArchiveKeys(
      value,
      ["ok", "status", "action", "mailboxId", "error"],
      ["archivedMessageIdentity"],
    ) ||
    value.ok !== false ||
    value.status !== "mutation_unconfirmed" ||
    value.action !== "archive" ||
    value.mailboxId !== request.mailboxId ||
    !isArchiveRecord(value.error) ||
    !hasExactArchiveKeys(value.error, ["code", "message"]) ||
    value.error.code !== "gmail_archive_unconfirmed" ||
    typeof value.error.message !== "string" ||
    value.error.message.trim().length < 1 ||
    value.error.message.length > 2_048
  ) {
    return null;
  }

  return {
    ok: false,
    status: "mutation_unconfirmed",
    action: "archive",
    mailboxId: request.mailboxId,
    archivedMessageIdentity: {
      serverMailboxId: request.mailboxId,
      providerMessageId: request.messageId,
      providerFolder: "Archive",
    },
    error: {
      code: "gmail_archive_unconfirmed",
      message: SAFE_GMAIL_ARCHIVE_UNCONFIRMED_MESSAGE,
    },
  };
}

function gmailArchiveMutationSuccess(
  value: Record<string, unknown>,
  request: GmailArchiveMutationRequest,
): GmailArchiveMutationSuccess | null {
  if (
    !gmailArchivedIdentityIsValid(value.archivedMessageIdentity, request) ||
    !isArchiveRecord(value.delta) ||
    !hasExactArchiveKeys(value.delta, ["Inbox", "Archive"]) ||
    !isArchiveRecord(value.delta.Inbox) ||
    !hasExactArchiveKeys(value.delta.Inbox, ["removeProviderMessageId"]) ||
    value.delta.Inbox.removeProviderMessageId !== request.messageId ||
    !isArchiveRecord(value.delta.Archive) ||
    !hasExactArchiveKeys(value.delta.Archive, ["upsertMessage"]) ||
    !gmailArchiveMessageIsValid(
      value.delta.Archive.upsertMessage,
      request.mailboxId,
      "Archive",
      "inbox_removal",
    )
  ) {
    return null;
  }
  const archivedMessageIdentity = value.archivedMessageIdentity;
  const upsertMessage = value.delta.Archive.upsertMessage;
  if (
    upsertMessage.providerMessageId !== request.messageId ||
    upsertMessage.providerThreadId !==
      archivedMessageIdentity.providerThreadId ||
    (
      archivedMessageIdentity.rfcMessageId !== undefined &&
      upsertMessage.rfcMessageId !== archivedMessageIdentity.rfcMessageId
    )
  ) {
    return null;
  }
  return value as GmailArchiveMutationSuccess;
}

function imapArchiveMutationSuccess(
  value: Record<string, unknown>,
  request: ImapArchiveMutationRequest,
): ImapArchiveMutationSuccess | null {
  if (
    !imapArchivedIdentityIsValid(value.archivedMessageIdentity, request) ||
    !isArchiveRecord(value.folders) ||
    !hasExactArchiveKeys(value.folders, ["Inbox", "Archive"])
  ) {
    return null;
  }
  const archivedMessageIdentity = value.archivedMessageIdentity;
  if (
    !imapFolderSnapshotIsValid(
      value.folders.Inbox,
      request.mailboxId,
      "INBOX",
    ) ||
    !imapFolderSnapshotIsValid(
      value.folders.Archive,
      request.mailboxId,
      archivedMessageIdentity.providerFolder,
    ) ||
    value.folders.Inbox.uidValidity !== request.uidValidity ||
    value.folders.Inbox.imapUidSet.includes(request.uid) ||
    value.folders.Archive.uidValidity !== archivedMessageIdentity.uidValidity ||
    !value.folders.Archive.imapUidSet.includes(
      archivedMessageIdentity.imapUid,
    )
  ) {
    return null;
  }
  const archiveMatches = value.folders.Archive.messages.filter(
    (message) => message.imapUid === archivedMessageIdentity.imapUid,
  );
  if (
    archiveMatches.length !== 1 ||
    (
      archivedMessageIdentity.rfcMessageId !== undefined &&
      archiveMatches[0].rfcMessageId !== archivedMessageIdentity.rfcMessageId
    )
  ) {
    return null;
  }
  return value as ImapArchiveMutationSuccess;
}

function archiveMutationSuccess(
  value: unknown,
  request: ArchiveMutationRequest,
): GmailArchiveMutationSuccess | ImapArchiveMutationSuccess | null {
  const resultField = "messageId" in request ? "delta" : "folders";
  if (
    containsForbiddenArchiveResponseField(value) ||
    !isArchiveRecord(value) ||
    !hasExactArchiveKeys(
      value,
      [
        "ok",
        "status",
        "action",
        "mailboxId",
        "archivedMessageIdentity",
        resultField,
      ],
    ) ||
    value.ok !== true ||
    value.status !== "ok" ||
    value.action !== "archive" ||
    value.mailboxId !== request.mailboxId
  ) {
    return null;
  }
  return "messageId" in request
    ? gmailArchiveMutationSuccess(value, request)
    : imapArchiveMutationSuccess(value, request);
}

export function isProviderArchiveMutationSuccessResponse(
  value: unknown,
  request: ArchiveMutationRequest,
): value is GmailArchiveMutationSuccess | ImapArchiveMutationSuccess {
  const wireRequest = archiveMutationRequestBody(request);
  return Boolean(
    wireRequest &&
      archiveMutationSuccess(value, wireRequest),
  );
}

export function sanitizeProviderArchiveMutationUncertainResponse(
  value: unknown,
  request: ArchiveMutationRequest,
): ArchiveMutationUncertainResponse | null {
  const wireRequest = archiveMutationRequestBody(request);
  return wireRequest
    ? archiveUncertainResponse(value, wireRequest)
    : null;
}

function gmailTrashFetchFailure(
  code: string,
  message: string,
): GmailTrashFetchFailure {
  return {
    ok: false,
    error: {
      code,
      message,
    },
  };
}

function gmailTrashFetchErrorFromPayload(
  value: unknown,
): GmailTrashFetchFailure | null {
  if (
    containsForbiddenArchiveResponseField(value) ||
    !isArchiveRecord(value) ||
    !hasExactArchiveKeys(value, ["ok", "error"]) ||
    value.ok !== false ||
    !isArchiveRecord(value.error) ||
    !hasExactArchiveKeys(value.error, ["code", "message"]) ||
    typeof value.error.code !== "string" ||
    !PUBLIC_GMAIL_TRASH_FETCH_ERROR_CODES.has(value.error.code) ||
    !isSafeTrashErrorMessage(value.error.message)
  ) {
    return null;
  }

  return gmailTrashFetchFailure(
    value.error.code,
    SAFE_GMAIL_TRASH_FETCH_ERROR_MESSAGE,
  );
}

function gmailTrashFetchSuccessResponse(
  value: unknown,
  mailboxId: string,
): GmailTrashFetchSuccess | null {
  if (
    containsForbiddenArchiveResponseField(value) ||
    !isArchiveRecord(value) ||
    !hasExactArchiveKeys(value, ["ok", "status", "mailboxId", "folder"]) ||
    value.ok !== true ||
    value.status !== "ok" ||
    value.mailboxId !== mailboxId ||
    !gmailTrashSnapshotIsValid(value.folder, mailboxId)
  ) {
    return null;
  }

  return value as GmailTrashFetchSuccess;
}

async function readGmailTrashFetchResponsePayload(
  response: Response,
): Promise<unknown> {
  const rawPayload = await response.text();
  if (
    !rawPayload.trim() ||
    new TextEncoder().encode(rawPayload).byteLength >
      MAX_GMAIL_TRASH_FETCH_RESPONSE_BYTES
  ) {
    return null;
  }

  try {
    return JSON.parse(rawPayload) as unknown;
  } catch {
    return null;
  }
}

export async function fetchGmailTrash(
  mailboxId: string,
): Promise<GmailTrashFetchResponse> {
  if (!isArchiveIdentifier(mailboxId)) {
    return gmailTrashFetchFailure(
      "invalid_trash_fetch_request",
      "A valid Gmail mailbox identity is required.",
    );
  }

  try {
    const response = await fetch("/api/inboxes/fetch-trash", {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ mailboxId }),
    });
    const payload = await readGmailTrashFetchResponsePayload(response);
    if (response.status !== 200) {
      return gmailTrashFetchErrorFromPayload(payload) ?? gmailTrashFetchFailure(
        "gmail_trash_fetch_response_invalid",
        "Trash did not return a valid Gmail provider snapshot.",
      );
    }

    const success = gmailTrashFetchSuccessResponse(payload, mailboxId);
    if (success) {
      return success;
    }

    return gmailTrashFetchErrorFromPayload(payload) ?? gmailTrashFetchFailure(
      "gmail_trash_fetch_response_invalid",
      "Trash did not return a valid Gmail provider snapshot.",
    );
  } catch {
    return gmailTrashFetchFailure(
      "gmail_trash_fetch_failed",
      "Could not load Trash from this Gmail mailbox.",
    );
  }
}

async function readProviderTrashResponsePayload(
  response: Response,
): Promise<unknown> {
  const rawPayload = await response.text();
  if (
    !rawPayload.trim() ||
    new TextEncoder().encode(rawPayload).byteLength > MAX_TRASH_RESPONSE_BYTES
  ) {
    return null;
  }

  try {
    return JSON.parse(rawPayload) as unknown;
  } catch {
    return null;
  }
}

export async function mutateProviderTrashMessage(
  request: ProviderTrashMessageRequest,
): Promise<ProviderTrashMessageResponse> {
  const wireRequest = providerTrashMessageRequestBody(request);
  if (!wireRequest) {
    return providerTrashFailure(
      "invalid_trash_request",
      "Trash requires one valid Gmail provider message identity.",
    );
  }

  try {
    const response = await fetch("/api/inboxes/message-action", {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(wireRequest),
    });
    const payload = await readProviderTrashResponsePayload(response);

    if (response.status === 502) {
      const uncertain = providerTrashUncertainResponse(payload, wireRequest);
      if (uncertain) {
        return uncertain;
      }
    }

    if (response.status !== 200) {
      const failure = providerTrashErrorFromPayload(payload);
      if (failure) {
        return failure;
      }

      return providerTrashFailure(
        "trash_response_invalid",
        "Trash did not return a valid provider-confirmed mailbox state.",
      );
    }

    const success = providerTrashSuccessResponse(payload, wireRequest);
    if (success) {
      return success;
    }

    return providerTrashErrorFromPayload(payload) ?? providerTrashFailure(
      "trash_response_invalid",
      "Trash did not return a valid provider-confirmed mailbox state.",
    );
  } catch {
    return providerTrashUnconfirmed(wireRequest);
  }
}

async function readArchiveResponsePayload(response: Response): Promise<unknown> {
  const rawPayload = await response.text();
  if (
    !rawPayload.trim() ||
    new TextEncoder().encode(rawPayload).byteLength > MAX_ARCHIVE_RESPONSE_BYTES
  ) {
    return null;
  }
  try {
    return JSON.parse(rawPayload) as unknown;
  } catch {
    return null;
  }
}

export async function mutateProviderArchiveMessage(
  request: ArchiveMutationRequest,
): Promise<ArchiveMutationResponse> {
  const wireRequest = archiveMutationRequestBody(request);
  if (!wireRequest) {
    return archiveFailure(
      "invalid_archive_request",
      "Archive requires one valid provider message identity.",
    );
  }
  try {
    const response = await fetch("/api/inboxes/message-action", {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(wireRequest),
    });
    const payload = await readArchiveResponsePayload(response);
    if (response.status === 502) {
      const uncertain = archiveUncertainResponse(payload, wireRequest);
      if (uncertain) return uncertain;
    }
    if (response.status !== 200) {
      return archiveErrorFromPayload(payload) ?? archiveFailure(
        "archive_mutation_failed",
        "Could not archive this message in the connected mailbox.",
      );
    }
    const success = archiveMutationSuccess(payload, wireRequest);
    if (success) return success;
    return archiveErrorFromPayload(payload) ?? archiveFailure(
      "archive_response_invalid",
      "Archive did not return a valid provider-confirmed mailbox state.",
    );
  } catch {
    return archiveFailure(
      "archive_mutation_failed",
      "Could not archive this message in the connected mailbox.",
    );
  }
}

function archiveFetchSuccess(
  value: unknown,
  mailboxId: string,
): GmailArchiveFetchSuccess | ImapArchiveFetchSuccess | null {
  if (
    containsForbiddenArchiveResponseField(value) ||
    !isArchiveRecord(value) ||
    !hasExactArchiveKeys(value, ["ok", "status", "mailboxId", "folder"]) ||
    value.ok !== true ||
    value.status !== "ok" ||
    value.mailboxId !== mailboxId
  ) {
    return null;
  }
  if (gmailFolderSnapshotIsValid(value.folder, mailboxId, "Archive")) {
    return value as GmailArchiveFetchSuccess;
  }
  if (
    isArchiveRecord(value.folder) &&
    isArchiveFolder(value.folder.providerFolder) &&
    value.folder.providerFolder !== "INBOX" &&
    imapFolderSnapshotIsValid(
      value.folder,
      mailboxId,
      value.folder.providerFolder,
    )
  ) {
    return value as ImapArchiveFetchSuccess;
  }
  return null;
}

export async function fetchProviderArchive(
  mailboxId: string,
): Promise<ArchiveFetchResponse> {
  if (!isArchiveIdentifier(mailboxId)) {
    return archiveFailure(
      "invalid_archive_request",
      "A valid mailbox identity is required.",
    );
  }
  try {
    const response = await fetch("/api/inboxes/fetch-archive", {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ mailboxId }),
    });
    const payload = await readArchiveResponsePayload(response);
    if (response.status !== 200) {
      return archiveErrorFromPayload(payload) ?? archiveFailure(
        "archive_fetch_failed",
        "Could not load Archive from the connected mailbox.",
      );
    }
    const success = archiveFetchSuccess(payload, mailboxId);
    if (success) return success;
    return archiveErrorFromPayload(payload) ?? archiveFailure(
      "archive_response_invalid",
      "Archive did not return a valid provider mailbox snapshot.",
    );
  } catch {
    return archiveFailure(
      "archive_fetch_failed",
      "Could not load Archive from the connected mailbox.",
    );
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

function applyTrustedCustomImapInboxSourceEnvelope(
  message: LiveInboxMessageSnapshot,
  mailboxId: string,
  uidValidity: string | null | undefined,
): LiveInboxMessageSnapshot {
  return {
    ...message,
    serverMailboxId:
      message.serverMailboxId === undefined
        ? mailboxId
        : message.serverMailboxId,
    providerFolder:
      message.providerFolder === undefined
        ? "INBOX"
        : message.providerFolder,
    uidValidity: uidValidity ?? undefined,
  };
}

const MESSAGE_NOISE_ASSESSMENT_FIELDS = [
  "noiseDisposition",
  "noiseConfidence",
  "noiseReasons",
] as const;

function normalizeLiveInboxMessageNoiseAssessment(
  message: LiveInboxMessageSnapshot,
): LiveInboxMessageSnapshot {
  if (!message || typeof message !== "object" || Array.isArray(message)) {
    return message;
  }

  const messageRecord = message as LiveInboxMessageSnapshot &
    Record<string, unknown>;
  const hasNoiseAssessment = MESSAGE_NOISE_ASSESSMENT_FIELDS.some((key) =>
    Object.prototype.hasOwnProperty.call(messageRecord, key),
  );

  if (!hasNoiseAssessment) {
    return message;
  }

  const normalizedMessage = { ...messageRecord };
  MESSAGE_NOISE_ASSESSMENT_FIELDS.forEach((key) => {
    delete normalizedMessage[key];
  });
  const noiseAssessment = resolveMessageNoiseAssessment({
    noiseDisposition: Object.prototype.hasOwnProperty.call(
      messageRecord,
      "noiseDisposition",
    )
      ? messageRecord.noiseDisposition
      : undefined,
    noiseConfidence: Object.prototype.hasOwnProperty.call(
      messageRecord,
      "noiseConfidence",
    )
      ? messageRecord.noiseConfidence
      : undefined,
    noiseReasons: Object.prototype.hasOwnProperty.call(
      messageRecord,
      "noiseReasons",
    )
      ? messageRecord.noiseReasons
      : undefined,
  });

  return {
    ...normalizedMessage,
    ...(noiseAssessment ?? {}),
  } as LiveInboxMessageSnapshot;
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

    const normalizedMessages = Array.isArray(payload.messages)
      ? payload.messages.map((message) => {
          const normalizedMessage = normalizeLiveInboxMessageNoiseAssessment(message);

          return request.mode === "refresh"
            ? applyTrustedCustomImapInboxSourceEnvelope(
                normalizedMessage,
                request.mailboxId,
                payload.uidValidity,
              )
            : normalizedMessage;
        })
      : null;

    return {
      ...payload,
      ...(normalizedMessages ? { messages: normalizedMessages } : {}),
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
  if (
    request.replyContext !== undefined &&
    !isValidGmailProviderIdentifier(
      request.replyContext?.sourceProviderMessageId,
    )
  ) {
    return {
      ok: false,
      error: {
        code: "invalid_reply_context",
        message: "Reply context must identify a valid Gmail source message.",
      },
    };
  }
  if (
    request.imapReplyContext !== undefined &&
    !isValidImapReplyContext(request.imapReplyContext)
  ) {
    return {
      ok: false,
      error: {
        code: "invalid_imap_reply_context",
        message: "Reply context must identify a valid custom IMAP source message.",
      },
    };
  }
  if (
    request.replyContext !== undefined &&
    request.imapReplyContext !== undefined
  ) {
    return {
      ok: false,
      error: {
        code: "invalid_reply_context",
        message: "Reply context must identify exactly one provider source message.",
      },
    };
  }

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
    let rawPayload: string;
    try {
      rawPayload = await response.text();
    } catch (error) {
      if (response.ok) {
        return sentIdentityUnconfirmedResponse();
      }
      throw error;
    }
    let payload: unknown = null;

    if (rawPayload.trim()) {
      try {
        payload = JSON.parse(rawPayload) as unknown;
      } catch {
        payload = null;
      }
    }

    if (!response.ok) {
      const responseError =
        payload &&
        typeof payload === "object" &&
        !Array.isArray(payload) &&
        "error" in payload &&
        payload.error &&
        typeof payload.error === "object" &&
        !Array.isArray(payload.error)
          ? payload.error as { code?: string; message?: string }
          : null;
      return {
        ok: false,
        error: responseError ?? {
          code: "send_failed",
          message: `Could not send email${response.status ? ` (${response.status})` : ""}.`,
        },
      };
    }

    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return sentIdentityUnconfirmedResponse();
    }

    const successPayload = payload as Record<string, unknown>;
    if (successPayload.ok !== true) {
      return sentIdentityUnconfirmedResponse();
    }
    if (request.imapReplyContext !== undefined) {
      // Generic SMTP has no Gmail-authoritative sent identity or semantic
      // ticket. Ignore hostile/legacy success metadata at this boundary.
      return { ok: true };
    }

    const providerMessageId = successPayload.providerMessageId;
    const providerThreadId = successPayload.providerThreadId;
    const providerIdentityWasRejected =
      successPayload.providerIdentityConfirmed === false;
    const hasProviderIdentity =
      !providerIdentityWasRejected &&
      isValidGmailProviderIdentifier(providerMessageId) &&
      isValidGmailProviderIdentifier(providerThreadId);
    const hasAnyProviderIdentity =
      providerMessageId !== undefined ||
      providerThreadId !== undefined ||
      "providerIdentityConfirmed" in successPayload ||
      "threadContinuityConfirmed" in successPayload;
    const labelIds = successPayload.labelIds;
    const safeLabelIds =
      Array.isArray(labelIds) &&
      labelIds.length <= 100 &&
      labelIds.every(isValidGmailProviderIdentifier) &&
      new Set(labelIds).size === labelIds.length
        ? labelIds
        : undefined;
    const threadContinuityConfirmed =
      typeof successPayload.threadContinuityConfirmed === "boolean"
        ? successPayload.threadContinuityConfirmed
        : undefined;
    const providerIdentityConfirmed =
      typeof successPayload.providerIdentityConfirmed === "boolean"
        ? successPayload.providerIdentityConfirmed
        : undefined;
    const semanticEventRef =
      request.replyContext !== undefined &&
      request.imapReplyContext === undefined &&
      hasProviderIdentity &&
      providerIdentityConfirmed !== false &&
      threadContinuityConfirmed === true
        ? normalizePrioritySemanticEventRef(successPayload.semanticEventRef)
        : "";
    const warningValue = successPayload.warning;
    const safeWarning =
      warningValue &&
      typeof warningValue === "object" &&
      !Array.isArray(warningValue) &&
      typeof (warningValue as Record<string, unknown>).code === "string" &&
      typeof (warningValue as Record<string, unknown>).message === "string"
        ? {
            code: (warningValue as Record<string, string>).code,
            message: (warningValue as Record<string, string>).message,
          }
        : undefined;

    if (hasAnyProviderIdentity && !hasProviderIdentity) {
      return sentIdentityUnconfirmedResponse();
    }

    return {
      ok: true,
      ...(hasProviderIdentity
        ? { providerMessageId, providerThreadId }
        : {}),
      ...(safeLabelIds ? { labelIds: safeLabelIds } : {}),
      ...(semanticEventRef ? { semanticEventRef } : {}),
      ...(providerIdentityConfirmed !== undefined
        ? { providerIdentityConfirmed }
        : {}),
      ...(threadContinuityConfirmed !== undefined
        ? { threadContinuityConfirmed }
        : {}),
      ...(safeWarning ? { warning: safeWarning } : {}),
    };
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

export async function requestPrioritySemanticAssessment(
  request:
    | PrioritySemanticAssessmentRequest
    | PrioritySemanticCurrentLookupRequest,
): Promise<PrioritySemanticAssessmentResponse> {
  const wireRequest =
    buildPrioritySemanticAssessmentWireRequest(request) ??
    buildPrioritySemanticCurrentLookupWireRequest(request);
  if (!wireRequest) {
    return {
      ok: false,
      error: {
        code: "invalid_semantic_request",
        message: "Semantic assessment request is invalid.",
      },
    };
  }

  const abortController = new AbortController();
  const timeoutId = globalThis.setTimeout(() => {
    abortController.abort();
  }, PRIORITY_SEMANTIC_ASSESSMENT_TIMEOUT_MS);

  try {
    const response = await fetch("/api/priority/semantic-assessment", {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
      },
      signal: abortController.signal,
      body: JSON.stringify(wireRequest),
    });
    let rawPayload = "";
    try {
      rawPayload = await response.text();
    } catch {
      return {
        ok: false,
        error: {
          code: "semantic_response_unreadable",
          message: "Semantic assessment response could not be read.",
        },
      };
    }

    let payload: unknown = null;
    if (rawPayload.trim()) {
      try {
        payload = JSON.parse(rawPayload) as unknown;
      } catch {
        payload = null;
      }
    }
    const parsedResponse = parsePrioritySemanticAssessmentResponse(payload);

    if (!response.ok) {
      return parsedResponse && !parsedResponse.ok
        ? parsedResponse
        : {
            ok: false,
            error: {
              code: "semantic_request_failed",
              message: `Semantic assessment failed${
                response.status ? ` (${response.status})` : ""
              }.`,
            },
          };
    }

    return parsedResponse ?? {
      ok: false,
      error: {
        code: "invalid_semantic_response",
        message: "Semantic assessment returned an invalid response.",
      },
    };
  } catch (error) {
    const didTimeout =
      error instanceof DOMException && error.name === "AbortError";
    return {
      ok: false,
      error: {
        code: didTimeout ? "semantic_timeout" : "semantic_request_failed",
        message: didTimeout
          ? "Semantic assessment timed out."
          : "Semantic assessment could not be reached.",
      },
    };
  } finally {
    globalThis.clearTimeout(timeoutId);
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

    return {
      ...payload,
      ...(Array.isArray(payload.messages)
        ? {
            messages: payload.messages.map(
              normalizeLiveInboxMessageNoiseAssessment,
            ),
          }
        : {}),
    };
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
