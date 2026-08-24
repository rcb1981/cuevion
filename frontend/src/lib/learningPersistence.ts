import {
  normalizeSenderLearningDomain,
  normalizeSenderLearningKey,
  type SenderCategoryLearningStore,
} from "./learningEngine";

const LEARNING_STORAGE_KEY_PREFIX = "cuevion-sender-category-learning:v2";
const EMPTY_LEARNING_STORE = Object.freeze({}) as SenderCategoryLearningStore;

export type LearningStorage = Pick<Storage, "getItem" | "setItem">;

export type ScopedSenderCategoryLearningState = {
  storageKey: string | null;
  store: SenderCategoryLearningStore;
};

type SenderCategoryLearningUpdate =
  | SenderCategoryLearningStore
  | ((current: SenderCategoryLearningStore) => SenderCategoryLearningStore);

function isObjectRecord(value: unknown): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }

  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function isCanonicalLearningKey(key: string) {
  if (
    !key ||
    key === "__proto__" ||
    key === "constructor" ||
    key === "prototype"
  ) {
    return false;
  }

  if (key.startsWith("domain:")) {
    const domain = key.slice("domain:".length);
    return Boolean(domain) && normalizeSenderLearningDomain(domain) === domain;
  }

  return normalizeSenderLearningKey(key) === key;
}

function isOptionalValue(
  value: unknown,
  predicate: (candidate: unknown) => boolean,
) {
  return value === undefined || predicate(value);
}

function isLearningMailboxProvenance(value: unknown) {
  return value === null || (typeof value === "string" && Boolean(value.trim()));
}

function isValidLearningStore(
  value: unknown,
): value is SenderCategoryLearningStore {
  if (!isObjectRecord(value)) {
    return false;
  }

  return Object.entries(value).every(([key, entry]) => {
    if (!isCanonicalLearningKey(key) || !isObjectRecord(entry)) {
      return false;
    }

    // The v2 payload intentionally stays the existing flat sender/domain store.
    // Validate only that shape and its current fields; policy, defaults, pruning,
    // and do_not_learn interpretation remain owned by the existing engines.
    return (
      (entry.learnedCategory === "Primary" ||
        entry.learnedCategory === "Promo" ||
        entry.learnedCategory === "Updates") &&
      typeof entry.learnedFromCount === "number" &&
      Number.isSafeInteger(entry.learnedFromCount) &&
      entry.learnedFromCount >= 0 &&
      isOptionalValue(
        entry.learnedLabel,
        (candidate) =>
          candidate === "Demo" ||
          candidate === "Promo" ||
          candidate === "Business" ||
          candidate === "Finance" ||
          candidate === "Update" ||
          candidate === "Reply" ||
          candidate === "Other" ||
          candidate === "Spam",
      ) &&
      isOptionalValue(
        entry.autoCategoryEnabled,
        (candidate) => typeof candidate === "boolean",
      ) &&
      isOptionalValue(
        entry.mailboxAction,
        (candidate) => candidate === "keep" || candidate === "move",
      ) &&
      isOptionalValue(
        entry.senderBehavior,
        (candidate) =>
          candidate === "always_prioritize" ||
          candidate === "normal" ||
          candidate === "show_less" ||
          candidate === "spam" ||
          candidate === "do_not_learn",
      ) &&
      isOptionalValue(
        entry.sourceContext,
        (candidate) =>
          candidate === "refine" ||
          candidate === "uncertain" ||
          candidate === "paste_sender_or_domain",
      ) &&
      isOptionalValue(
        entry.sourcePrioritySelection,
        (candidate) =>
          candidate === "Important" ||
          candidate === "Normal" ||
          candidate === "Show Less" ||
          candidate === "Spam",
      ) &&
      isOptionalValue(entry.sourceMailboxId, isLearningMailboxProvenance) &&
      isOptionalValue(entry.sourceCurrentMailboxId, isLearningMailboxProvenance) &&
      isOptionalValue(
        entry.updatedAt,
        (candidate) => typeof candidate === "string",
      )
    );
  });
}

function parseLearningStore(rawValue: string | null): SenderCategoryLearningStore {
  if (!rawValue) {
    return {};
  }

  try {
    const parsedValue: unknown = JSON.parse(rawValue);
    return isValidLearningStore(parsedValue) ? parsedValue : {};
  } catch {
    return {};
  }
}

export function buildLearningStorageKey(
  workspaceId: string | null | undefined,
  userId: string | null | undefined,
) {
  const stableWorkspaceId = workspaceId?.trim();
  const stableUserId = userId?.trim();

  if (!stableWorkspaceId || !stableUserId) {
    return null;
  }

  return `${LEARNING_STORAGE_KEY_PREFIX}:${encodeURIComponent(
    stableWorkspaceId,
  )}:${encodeURIComponent(stableUserId)}`;
}

export function hydrateScopedSenderCategoryLearning(
  storage: LearningStorage | null,
  storageKey: string | null,
): ScopedSenderCategoryLearningState {
  if (!storage || !storageKey) {
    return { storageKey, store: {} };
  }

  try {
    return {
      storageKey,
      store: parseLearningStore(storage.getItem(storageKey)),
    };
  } catch {
    return { storageKey, store: {} };
  }
}

export function selectScopedSenderCategoryLearning(
  state: ScopedSenderCategoryLearningState,
  activeStorageKey: string | null,
) {
  return activeStorageKey && state.storageKey === activeStorageKey
    ? state.store
    : EMPTY_LEARNING_STORE;
}

export function updateScopedSenderCategoryLearning(
  state: ScopedSenderCategoryLearningState,
  activeStorageKey: string | null,
  update: SenderCategoryLearningUpdate,
): ScopedSenderCategoryLearningState {
  if (!activeStorageKey || state.storageKey !== activeStorageKey) {
    return state;
  }

  const nextStore = typeof update === "function" ? update(state.store) : update;

  return nextStore === state.store || !isValidLearningStore(nextStore)
    ? state
    : { storageKey: activeStorageKey, store: nextStore };
}

export function persistScopedSenderCategoryLearning(
  storage: LearningStorage | null,
  state: ScopedSenderCategoryLearningState,
  activeStorageKey: string | null,
) {
  if (
    !storage ||
    !activeStorageKey ||
    state.storageKey !== activeStorageKey ||
    !isValidLearningStore(state.store)
  ) {
    return false;
  }

  try {
    storage.setItem(activeStorageKey, JSON.stringify(state.store));
    return true;
  } catch {
    return false;
  }
}
