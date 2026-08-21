import {
  resolveCanonicalConversationIdentity,
  resolveMessageDateMs,
  type RenderedConversationMessage,
} from "./inboxEngine";

export const WAITING_ON_OTHER_MAX_INACTIVITY_MS = 14 * 24 * 60 * 60 * 1000;

export type WaitingOnOtherState = {
  state: "waiting_on_other";
  mailboxId: string;
  conversationKey: string;
  transitionedAt: string;
};

export type WaitingOnOtherStore = Record<string, WaitingOnOtherState>;

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

      const record = candidate as Partial<WaitingOnOtherState>;
      if (
        record.state !== "waiting_on_other" ||
        typeof record.mailboxId !== "string" ||
        !record.mailboxId.trim() ||
        typeof record.conversationKey !== "string" ||
        !record.conversationKey.trim() ||
        typeof record.transitionedAt !== "string"
      ) {
        return store;
      }

      const normalizedRecord: WaitingOnOtherState = {
        state: "waiting_on_other",
        mailboxId: record.mailboxId.trim(),
        conversationKey: record.conversationKey.trim(),
        transitionedAt: record.transitionedAt,
      };

      if (!isWaitingOnOtherStateActive(normalizedRecord, nowMs)) {
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

  return record && isWaitingOnOtherStateActive(record, nowMs) ? record : null;
}

export function reconcileWaitingOnOtherStore<T extends RenderedConversationMessage>(
  store: WaitingOnOtherStore,
  externalInboundEntries: MailboxConversationEntry<T>[],
  nowMs = Date.now(),
): WaitingOnOtherStore {
  const activeStore = normalizeWaitingOnOtherStore(store, nowMs);
  let nextStore = activeStore;

  externalInboundEntries.forEach(({ mailboxId, message }) => {
    const record = resolveWaitingOnOtherState(
      activeStore,
      mailboxId,
      message,
      nowMs,
    );
    if (!record) {
      return;
    }

    const inboundTime = resolveMessageDateMs(message);
    const transitionTime = parseTransitionTime(record.transitionedAt);
    if (!transitionTime || inboundTime <= transitionTime) {
      return;
    }

    nextStore = clearConversationWaitingOnOther(nextStore, {
      mailboxId,
      message,
    });
  });

  return nextStore;
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
