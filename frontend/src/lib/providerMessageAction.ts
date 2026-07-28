import type {
  InboxMessageAction,
  InboxMessageActionRequest,
} from "./inboxConnectionApi";

const MAX_PROVIDER_IDENTIFIER_LENGTH = 256;

export type ProviderMessageActionCandidate = {
  provider: string | null;
  mailboxId: string;
  localFolder: string;
  isSharedView: boolean;
  providerMessageId?: unknown;
  imapUid?: unknown;
  imapFolder?: string;
  imapUidValidity?: string | null;
  action: InboxMessageAction;
};

export type ProviderMessageActionTargetResult =
  | {
      ok: true;
      request: InboxMessageActionRequest;
    }
  | {
      ok: false;
      reason:
        | "unsupported_context"
        | "missing_gmail_provider_message_id"
        | "missing_imap_uid"
        | "unsupported_provider";
    };

function normalizeConcreteGmailProviderMessageId(value: unknown) {
  if (typeof value !== "string") {
    return null;
  }

  const normalized = value.trim();
  if (
    normalized.length < 1 ||
    normalized.length > MAX_PROVIDER_IDENTIFIER_LENGTH ||
    !/^[\x20-\x7e]+$/.test(normalized) ||
    normalized.includes("@") ||
    normalized.includes("<") ||
    normalized.includes(">")
  ) {
    return null;
  }

  const lowered = normalized.toLowerCase();
  return ["imap-uid-", "rfc-", "thread-"].some((prefix) =>
    lowered.startsWith(prefix))
    ? null
    : normalized;
}

export function buildProviderMessageActionTarget(
  candidate: ProviderMessageActionCandidate,
): ProviderMessageActionTargetResult {
  if (
    candidate.isSharedView ||
    (candidate.localFolder !== "Inbox" && candidate.localFolder !== "Filtered")
  ) {
    return { ok: false, reason: "unsupported_context" };
  }

  if (candidate.provider === "google") {
    const providerMessageId = normalizeConcreteGmailProviderMessageId(
      candidate.providerMessageId,
    );
    if (!providerMessageId) {
      return { ok: false, reason: "missing_gmail_provider_message_id" };
    }

    return {
      ok: true,
      request: {
        mailboxId: candidate.mailboxId,
        messageId: providerMessageId,
        action: candidate.action,
      },
    };
  }

  if (candidate.provider === "custom_imap") {
    const imapUid =
      typeof candidate.imapUid === "string" ? candidate.imapUid.trim() : "";
    if (!imapUid) {
      return { ok: false, reason: "missing_imap_uid" };
    }

    return {
      ok: true,
      request: {
        mailboxId: candidate.mailboxId,
        folder: candidate.imapFolder ?? "INBOX",
        uid: imapUid,
        uidValidity: candidate.imapUidValidity ?? null,
        action: candidate.action,
      },
    };
  }

  return { ok: false, reason: "unsupported_provider" };
}
