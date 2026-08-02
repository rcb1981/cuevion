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
};

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
export type ImapInboxSnapshot = ImapProviderFolderSnapshot<"INBOX">;
export type ImapArchiveSnapshot = ImapProviderFolderSnapshot;
export type ArchiveFolderSnapshot =
  | GmailArchiveSnapshot
  | ImapArchiveSnapshot;

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

  for (const [key, item] of Object.entries(value)) {
    if (!ARCHIVE_PREVIEW_FIELDS.has(key)) continue;
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
