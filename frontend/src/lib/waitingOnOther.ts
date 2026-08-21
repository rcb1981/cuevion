import {
  resolveCanonicalConversationIdentity,
  resolveMessageDateMs,
  type RenderedConversationMessage,
} from "./inboxEngine";
import {
  getReturnedReplySenderAddress,
  normalizeReturnedReplyEmailAddress,
  type ReturnedReplyEvidence,
} from "./returnedReplyEvidence";

export const WAITING_ON_OTHER_MAX_INACTIVITY_MS = 14 * 24 * 60 * 60 * 1000;

export type WaitingOnOtherState = {
  state: "waiting_on_other";
  mailboxId: string;
  conversationKey: string;
  transitionedAt: string;
};

export type WaitingReturnedReplyState = {
  state: "returned_reply";
  mailboxId: string;
  conversationKey: string;
  transitionedAt: string;
  returnedMessageKey: string;
  returnedReplyAt: string;
};

export type WaitingConversationState =
  | WaitingOnOtherState
  | WaitingReturnedReplyState;

export type WaitingOnOtherStore = Record<string, WaitingConversationState>;

export type WaitingOnOtherComposeMode =
  | "new"
  | "reply"
  | "reply_all"
  | "forward";

export type MailboxConversationEntry<T extends RenderedConversationMessage> = {
  mailboxId: string;
  message: T;
};

function buildWaitingOnOtherStoreKey(mailboxId: string, conversationKey: string) {
  return `${mailboxId}::${conversationKey}`;
}

function resolveAuthoritativeConversation(
  mailboxId: string,
  message: RenderedConversationMessage,
) {
  const attachedMailboxId = message.threadIdentityContext?.mailboxId?.trim();
  if (attachedMailboxId && attachedMailboxId !== mailboxId.trim()) {
    return null;
  }

  const identity = resolveCanonicalConversationIdentity(message, mailboxId);

  return identity.isAuthoritativeConversation ? identity : null;
}

function parseTransitionTime(value: string) {
  const transitionTime = new Date(value).getTime();
  return Number.isFinite(transitionTime) && transitionTime > 0
    ? transitionTime
    : null;
}

function isReturnedReplyStateActive(
  value: WaitingReturnedReplyState,
  nowMs = Date.now(),
) {
  const returnedReplyTime = parseTransitionTime(value.returnedReplyAt);

  return Boolean(
    returnedReplyTime &&
      nowMs >= returnedReplyTime &&
      nowMs - returnedReplyTime <= WAITING_ON_OTHER_MAX_INACTIVITY_MS,
  );
}

function isWaitingConversationStateActive(
  value: WaitingConversationState,
  nowMs = Date.now(),
) {
  return value.state === "waiting_on_other"
    ? isWaitingOnOtherStateActive(value, nowMs)
    : isReturnedReplyStateActive(value, nowMs);
}

function encodeReturnedMessageIdentityComponent(value: string) {
  return encodeURIComponent(value.trim());
}

function resolveReturnedMessageKey(
  mailboxId: string,
  message: RenderedConversationMessage & { rfcMessageId?: string | null },
) {
  const conversation = resolveAuthoritativeConversation(mailboxId, message);
  if (!conversation) {
    return null;
  }

  const provider =
    conversation.authority === "gmail"
      ? "google"
      : conversation.authority === "rfc"
        ? "custom_imap"
        : null;
  const attachedProvider = message.threadIdentityContext?.provider;
  if (!provider || (attachedProvider && attachedProvider !== provider)) {
    return null;
  }

  const messageIdentity =
    message.providerMessageId?.trim() ||
    message.rfcMessageId?.trim() ||
    (message.imapUid?.trim() && message.threadIdentityContext?.uidValidity?.trim()
      ? `${message.threadIdentityContext.uidValidity.trim()}:${message.imapUid.trim()}`
      : "") ||
    message.id?.trim();
  if (!messageIdentity) {
    return null;
  }

  return [
    "returned-message:v1",
    encodeReturnedMessageIdentityComponent(mailboxId),
    provider,
    encodeReturnedMessageIdentityComponent(messageIdentity),
  ].join(":");
}

function buildOwnAddressSet(ownEmailAddresses: readonly string[]) {
  return new Set(
    ownEmailAddresses
      .map(normalizeReturnedReplyEmailAddress)
      .filter(Boolean),
  );
}

function isGenuineExternalInbound(
  message: RenderedConversationMessage,
  ownAddressSet: ReadonlySet<string>,
) {
  const sender = getReturnedReplySenderAddress(message);
  const hasValidEmailIdentity =
    /^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$/i.test(sender);

  return Boolean(
    ownAddressSet.size > 0 &&
      hasValidEmailIdentity &&
      !ownAddressSet.has(sender),
  );
}

export function isWaitingOnOtherStateActive(
  value: WaitingOnOtherState,
  nowMs = Date.now(),
) {
  const transitionTime = parseTransitionTime(value.transitionedAt);

  return Boolean(
    transitionTime &&
      nowMs >= transitionTime &&
      nowMs - transitionTime <= WAITING_ON_OTHER_MAX_INACTIVITY_MS,
  );
}

export function normalizeWaitingOnOtherStore(
  value: unknown,
  nowMs = Date.now(),
): WaitingOnOtherStore {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }

  return Object.values(value as Record<string, unknown>).reduce<WaitingOnOtherStore>(
    (store, candidate) => {
      if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) {
        return store;
      }

      const record = candidate as Partial<WaitingConversationState> & {
        returnedMessageKey?: unknown;
        returnedReplyAt?: unknown;
      };
      if (
        (record.state !== "waiting_on_other" &&
          record.state !== "returned_reply") ||
        typeof record.mailboxId !== "string" ||
        !record.mailboxId.trim() ||
        typeof record.conversationKey !== "string" ||
        !record.conversationKey.trim() ||
        typeof record.transitionedAt !== "string"
      ) {
        return store;
      }

      const normalizedRecord: WaitingConversationState =
        record.state === "returned_reply"
          ? {
              state: "returned_reply",
              mailboxId: record.mailboxId.trim(),
              conversationKey: record.conversationKey.trim(),
              transitionedAt: record.transitionedAt,
              returnedMessageKey:
                typeof record.returnedMessageKey === "string"
                  ? record.returnedMessageKey.trim()
                  : "",
              returnedReplyAt:
                typeof record.returnedReplyAt === "string"
                  ? record.returnedReplyAt
                  : "",
            }
          : {
              state: "waiting_on_other",
              mailboxId: record.mailboxId.trim(),
              conversationKey: record.conversationKey.trim(),
              transitionedAt: record.transitionedAt,
            };

      const transitionTime = parseTransitionTime(normalizedRecord.transitionedAt);
      const returnedReplyTime =
        normalizedRecord.state === "returned_reply"
          ? parseTransitionTime(normalizedRecord.returnedReplyAt)
          : null;
      if (
        !transitionTime ||
        (normalizedRecord.state === "returned_reply" &&
          (!normalizedRecord.returnedMessageKey ||
            !returnedReplyTime ||
            returnedReplyTime <= transitionTime)) ||
        !isWaitingConversationStateActive(normalizedRecord, nowMs)
      ) {
        return store;
      }

      store[
        buildWaitingOnOtherStoreKey(
          normalizedRecord.mailboxId,
          normalizedRecord.conversationKey,
        )
      ] = normalizedRecord;
      return store;
    },
    {},
  );
}

export function markConversationWaitingOnOther(
  store: WaitingOnOtherStore,
  input: {
    mailboxId: string;
    message: RenderedConversationMessage;
    transitionedAt?: string;
  },
): WaitingOnOtherStore {
  const mailboxId = input.mailboxId.trim();
  const conversation = mailboxId
    ? resolveAuthoritativeConversation(mailboxId, input.message)
    : null;
  const transitionedAt = input.transitionedAt ?? new Date().toISOString();

  if (!conversation || !parseTransitionTime(transitionedAt)) {
    return store;
  }

  const record: WaitingOnOtherState = {
    state: "waiting_on_other",
    mailboxId,
    conversationKey: conversation.key,
    transitionedAt,
  };

  return {
    ...store,
    [buildWaitingOnOtherStoreKey(mailboxId, conversation.key)]: record,
  };
}

export function transitionWaitingOnOtherAfterSend(
  store: WaitingOnOtherStore,
  input: {
    mailboxId: string;
    message: RenderedConversationMessage;
    composeMode: WaitingOnOtherComposeMode;
    sendSucceeded: boolean;
    transitionedAt?: string;
  },
): WaitingOnOtherStore {
  if (
    !input.sendSucceeded ||
    (input.composeMode !== "reply" && input.composeMode !== "reply_all")
  ) {
    return store;
  }

  return markConversationWaitingOnOther(store, input);
}

export function clearConversationWaitingOnOther(
  store: WaitingOnOtherStore,
  input: {
    mailboxId: string;
    message: RenderedConversationMessage;
  },
): WaitingOnOtherStore {
  const conversation = resolveAuthoritativeConversation(
    input.mailboxId,
    input.message,
  );

  if (!conversation) {
    return store;
  }

  const storeKey = buildWaitingOnOtherStoreKey(
    input.mailboxId,
    conversation.key,
  );
  if (!Object.prototype.hasOwnProperty.call(store, storeKey)) {
    return store;
  }

  const nextStore = { ...store };
  delete nextStore[storeKey];
  return nextStore;
}

export function resolveWaitingOnOtherState(
  store: WaitingOnOtherStore,
  mailboxId: string,
  message: RenderedConversationMessage,
  nowMs = Date.now(),
) {
  const conversation = resolveAuthoritativeConversation(mailboxId, message);
  if (!conversation) {
    return null;
  }

  const record =
    store[buildWaitingOnOtherStoreKey(mailboxId, conversation.key)] ?? null;

  return record?.state === "waiting_on_other" &&
    isWaitingOnOtherStateActive(record, nowMs)
    ? record
    : null;
}

export function resolveWaitingReturnedReplyEvidence(
  store: WaitingOnOtherStore,
  mailboxId: string,
  message: RenderedConversationMessage & { rfcMessageId?: string | null },
  ownEmailAddresses: readonly string[],
  nowMs = Date.now(),
): ReturnedReplyEvidence | null {
  const conversation = resolveAuthoritativeConversation(mailboxId, message);
  if (!conversation) {
    return null;
  }

  const record =
    store[buildWaitingOnOtherStoreKey(mailboxId, conversation.key)] ?? null;
  const returnedMessageKey = resolveReturnedMessageKey(mailboxId, message);
  const messageDate = resolveMessageDateMs(message);
  const ownAddressSet = buildOwnAddressSet(ownEmailAddresses);

  if (
    record?.state !== "returned_reply" ||
    !isReturnedReplyStateActive(record, nowMs) ||
    !returnedMessageKey ||
    returnedMessageKey !== record.returnedMessageKey ||
    messageDate !== parseTransitionTime(record.returnedReplyAt) ||
    !isGenuineExternalInbound(message, ownAddressSet)
  ) {
    return null;
  }

  return {
    hasEvidence: true,
    confidence: "high",
    reason:
      "A newer external inbound message matches the authoritative mailbox conversation Cuevion was waiting on.",
    lastUserReplyAt: record.transitionedAt,
    returnedReplyAt: record.returnedReplyAt,
  };
}

export function reconcileWaitingOnOtherStore<T extends RenderedConversationMessage>(
  store: WaitingOnOtherStore,
  externalInboundEntries: MailboxConversationEntry<T>[],
  options: {
    ownEmailAddresses: readonly string[];
    nowMs?: number;
  },
): WaitingOnOtherStore {
  const nowMs = options.nowMs ?? Date.now();
  const activeStore = normalizeWaitingOnOtherStore(store, nowMs);
  const ownAddressSet = buildOwnAddressSet(options.ownEmailAddresses);
  const newestReturnedReplyByStoreKey = new Map<
    string,
    {
      record: WaitingReturnedReplyState;
      inboundTime: number;
      isAmbiguous: boolean;
    }
  >();

  externalInboundEntries.forEach(({ mailboxId, message }) => {
    const conversation = resolveAuthoritativeConversation(mailboxId, message);
    if (!conversation) {
      return;
    }

    const storeKey = buildWaitingOnOtherStoreKey(mailboxId, conversation.key);
    const record = activeStore[storeKey] ?? null;
    if (!record) {
      return;
    }

    const inboundTime = resolveMessageDateMs(message);
    const transitionTime = parseTransitionTime(record.transitionedAt);
    const returnedMessageKey = resolveReturnedMessageKey(mailboxId, message);
    if (
      !transitionTime ||
      inboundTime <= transitionTime ||
      !returnedMessageKey ||
      !isGenuineExternalInbound(message, ownAddressSet)
    ) {
      return;
    }

    const existingReturnedTime =
      record.state === "returned_reply"
        ? parseTransitionTime(record.returnedReplyAt) ?? 0
        : 0;
    const pendingReturnedReply = newestReturnedReplyByStoreKey.get(storeKey);
    if (inboundTime <= existingReturnedTime) {
      return;
    }

    if (pendingReturnedReply) {
      if (inboundTime < pendingReturnedReply.inboundTime) {
        return;
      }

      if (inboundTime === pendingReturnedReply.inboundTime) {
        if (
          returnedMessageKey !==
          pendingReturnedReply.record.returnedMessageKey
        ) {
          newestReturnedReplyByStoreKey.set(storeKey, {
            ...pendingReturnedReply,
            isAmbiguous: true,
          });
        }
        return;
      }
    }

    newestReturnedReplyByStoreKey.set(storeKey, {
      record: {
        state: "returned_reply",
        mailboxId,
        conversationKey: conversation.key,
        transitionedAt: record.transitionedAt,
        returnedMessageKey,
        returnedReplyAt: new Date(inboundTime).toISOString(),
      },
      inboundTime,
      isAmbiguous: false,
    });
  });

  const unambiguousReturnedReplies = Array.from(
    newestReturnedReplyByStoreKey,
  ).flatMap(([storeKey, pending]) =>
    pending.isAmbiguous ? [] : [[storeKey, pending.record] as const],
  );
  if (unambiguousReturnedReplies.length === 0) {
    return activeStore;
  }

  return {
    ...activeStore,
    ...Object.fromEntries(unambiguousReturnedReplies),
  };
}

export function selectWaitingOnOtherRepresentatives<
  T extends RenderedConversationMessage,
  TEntry extends MailboxConversationEntry<T>,
>(
  store: WaitingOnOtherStore,
  entries: TEntry[],
  nowMs = Date.now(),
): TEntry[] {
  const representatives = new Map<string, TEntry>();

  entries.forEach((entry) => {
    const record = resolveWaitingOnOtherState(
      store,
      entry.mailboxId,
      entry.message,
      nowMs,
    );
    if (!record) {
      return;
    }

    const storeKey = buildWaitingOnOtherStoreKey(
      record.mailboxId,
      record.conversationKey,
    );
    const current = representatives.get(storeKey);
    if (
      !current ||
      resolveMessageDateMs(entry.message) > resolveMessageDateMs(current.message)
    ) {
      representatives.set(storeKey, entry);
    }
  });

  return Array.from(representatives.values());
}
