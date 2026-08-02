export type PersistedMessageIdentityContext = {
  mailboxId?: string | null;
  provider?: string | null;
  folder?: string | null;
  uidValidity?: string | null;
};

export type PersistedMessageIdentitySource = {
  id?: string | null;
  serverMailboxId?: string | null;
  providerFolder?: string | null;
  providerMessageId?: string | null;
  rfcMessageId?: string | null;
  imapUid?: string | null;
  uidValidity?: string | null;
  threadIdentityContext?: PersistedMessageIdentityContext | null;
  subject?: string | null;
  from?: string | null;
  timestamp?: string | null;
};

export type PersistedMessageIdentityCandidate = {
  message: PersistedMessageIdentitySource;
  context?: PersistedMessageIdentityContext;
};

export type LegacyImapMigrationOptions = {
  knownImapMailboxIds?: readonly string[];
  knownNonImapMailboxIds?: readonly string[];
};

type ResolvedIdentityComponent = {
  conflicted: boolean;
  value: string | null;
};

type ResolvedPersistedMessageIdentity = {
  imapUid: string | null;
  imapUidSignals: string[];
  isImap: boolean;
  mailboxId: string | null;
  ownershipKeys: string[];
  stateKeys: string[];
  technicalKey: string | null;
};

type LegacyImapMigrationTarget = {
  mailboxId: string;
  ownershipKey: string;
  stateKey: string;
};

const LEGACY_GLOBAL_IMAP_PREFIX = "imap:";
const LEGACY_IMAP_OWNERSHIP_PREFIX = "imap-uid-";
const SCOPED_IMAP_IDENTITY_PREFIX = "imap-scoped:v2";
const SEMANTIC_IMAP_IDENTITY_PREFIX = "imap-semantic:v2";

function normalizeIdentityComponent(value?: string | null) {
  const normalized = value?.trim();
  return normalized || null;
}

function normalizeImapUid(value?: string | null) {
  const normalized = normalizeIdentityComponent(value);
  return normalized && /^[1-9]\d*$/.test(normalized) ? normalized : null;
}

function resolveConsistentIdentityComponent(
  values: Array<string | null | undefined>,
): ResolvedIdentityComponent {
  const normalizedValues = Array.from(
    new Set(values.map((value) => normalizeIdentityComponent(value)).filter(Boolean)),
  ) as string[];

  if (normalizedValues.length > 1) {
    return { conflicted: true, value: null };
  }

  return {
    conflicted: false,
    value: normalizedValues[0] ?? null,
  };
}

function normalizeRfcMessageId(value?: string | null) {
  let normalized = normalizeIdentityComponent(value);
  if (!normalized) {
    return null;
  }

  const hasOpeningBracket = normalized.startsWith("<");
  const hasClosingBracket = normalized.endsWith(">");
  if (hasOpeningBracket !== hasClosingBracket) {
    return null;
  }
  if (hasOpeningBracket && hasClosingBracket) {
    normalized = normalized.slice(1, -1);
  }

  if (
    !normalized ||
    /[\s<>\u0000-\u001f\u007f]/.test(normalized) ||
    normalized.indexOf("@") <= 0 ||
    normalized.indexOf("@") !== normalized.lastIndexOf("@") ||
    normalized.endsWith("@")
  ) {
    return null;
  }

  return normalized;
}

function resolveSemanticRfcMessageId(message: PersistedMessageIdentitySource) {
  // Only the explicit RFC field is semantic authority. `id` can contain a
  // provider/UI fallback such as `imap-uid-1`, or an email-shaped value that is
  // not known to be the RFC Message-ID.
  return normalizeRfcMessageId(message.rfcMessageId);
}

function encodeScopedIdentityComponent(value: string) {
  return encodeURIComponent(value);
}

function resolvePersistedMessageIdentity(
  message: PersistedMessageIdentitySource,
  explicitContext?: PersistedMessageIdentityContext,
): ResolvedPersistedMessageIdentity {
  const attachedContext = message.threadIdentityContext ?? undefined;
  const rawImapUid = normalizeIdentityComponent(message.imapUid);
  const idImapUid = parseLegacyImapOwnershipKey(
    normalizeIdentityComponent(message.id) ?? "",
  );
  const providerSignals = [
    explicitContext?.provider,
    attachedContext?.provider,
  ]
    .map((value) => normalizeIdentityComponent(value))
    .filter(Boolean) as string[];
  const hasCustomImapProviderSignal = providerSignals.includes("custom_imap");
  const hasImapIdentitySignal = Boolean(
    rawImapUid || hasCustomImapProviderSignal,
  );
  const imapUidSignals = Array.from(
    new Set(
      [normalizeImapUid(message.imapUid), hasImapIdentitySignal ? idImapUid : null]
        .filter(Boolean) as string[],
    ),
  );
  const resolvedImapUid = resolveConsistentIdentityComponent(imapUidSignals);
  const imapUid =
    rawImapUid && !normalizeImapUid(rawImapUid)
      ? null
      : resolvedImapUid.value;
  const provider = resolveConsistentIdentityComponent([
    explicitContext?.provider,
    attachedContext?.provider,
    hasImapIdentitySignal ? "custom_imap" : null,
  ]);
  const isImap = hasImapIdentitySignal;

  // Gmail and non-IMAP identities deliberately keep their existing key contract.
  if (!isImap) {
    const stateKeys: string[] = [];
    const id = normalizeIdentityComponent(message.id);
    if (id) {
      stateKeys.push(`id:${id}`);
    }
    stateKeys.push(
      `preview:${message.subject ?? ""}|${message.from ?? ""}|${message.timestamp ?? ""}`,
    );

    return {
      imapUid: null,
      imapUidSignals: [],
      isImap: false,
      mailboxId: null,
      ownershipKeys: id ? [id] : [],
      stateKeys,
      technicalKey: null,
    };
  }

  const mailboxId = resolveConsistentIdentityComponent([
    explicitContext?.mailboxId,
    message.serverMailboxId,
    attachedContext?.mailboxId,
  ]);
  const folder = resolveConsistentIdentityComponent([
    explicitContext?.folder,
    message.providerFolder,
    attachedContext?.folder,
  ]);
  const uidValidity = resolveConsistentIdentityComponent([
    explicitContext?.uidValidity,
    message.uidValidity,
    attachedContext?.uidValidity,
  ]);
  const invalidImapUid = Boolean(
    rawImapUid && !normalizeImapUid(rawImapUid),
  );
  const hasIdentityContextConflict = Boolean(
    invalidImapUid ||
      resolvedImapUid.conflicted ||
      provider.conflicted ||
      mailboxId.conflicted ||
      folder.conflicted ||
      uidValidity.conflicted,
  );
  const semanticRfcMessageId = resolveSemanticRfcMessageId(message);
  const semanticKey =
    semanticRfcMessageId &&
    provider.value === "custom_imap" &&
    mailboxId.value &&
    !hasIdentityContextConflict
      ? [
          SEMANTIC_IMAP_IDENTITY_PREFIX,
          encodeScopedIdentityComponent(mailboxId.value),
          encodeScopedIdentityComponent(semanticRfcMessageId),
        ].join(":")
      : null;
  const hasCompleteTechnicalContext =
    Boolean(imapUid) &&
    provider.value === "custom_imap" &&
    Boolean(mailboxId.value) &&
    Boolean(folder.value) &&
    Boolean(uidValidity.value) &&
    !hasIdentityContextConflict;
  const technicalKey = hasCompleteTechnicalContext
    ? [
        SCOPED_IMAP_IDENTITY_PREFIX,
        encodeScopedIdentityComponent(mailboxId.value as string),
        encodeScopedIdentityComponent(provider.value as string),
        encodeScopedIdentityComponent(folder.value as string),
        encodeScopedIdentityComponent(uidValidity.value as string),
        encodeScopedIdentityComponent(imapUid as string),
      ].join(":")
    : null;

  if (semanticKey) {
    return {
      imapUid,
      imapUidSignals,
      isImap: true,
      mailboxId: mailboxId.value,
      ownershipKeys: [semanticKey],
      stateKeys: [semanticKey],
      technicalKey,
    };
  }

  return {
    imapUid,
    imapUidSignals,
    isImap: true,
    mailboxId: mailboxId.value,
    ownershipKeys: technicalKey ? [technicalKey] : [],
    stateKeys: technicalKey ? [technicalKey] : [],
    technicalKey,
  };
}

export function isPersistedMessageIdentityImap(
  message: PersistedMessageIdentitySource,
  explicitContext?: PersistedMessageIdentityContext,
) {
  return resolvePersistedMessageIdentity(message, explicitContext).isImap;
}

export function getPersistedMessageIdentityKeys(
  message: PersistedMessageIdentitySource,
  explicitContext?: PersistedMessageIdentityContext,
) {
  return resolvePersistedMessageIdentity(message, explicitContext).stateKeys;
}

export function getPersistedMessageOwnershipIdentityKeys(
  message: PersistedMessageIdentitySource,
  explicitContext?: PersistedMessageIdentityContext,
) {
  return resolvePersistedMessageIdentity(message, explicitContext).ownershipKeys;
}

export function resolvePersistedMessageStateValue<T>(
  state: Readonly<Record<string, T>>,
  message: PersistedMessageIdentitySource,
  explicitContext?: PersistedMessageIdentityContext,
) {
  for (const key of getPersistedMessageIdentityKeys(message, explicitContext)) {
    if (Object.prototype.hasOwnProperty.call(state, key)) {
      return state[key];
    }
  }

  return undefined;
}

function parseLegacyGlobalImapIdentityKey(key: string) {
  if (!key.startsWith(LEGACY_GLOBAL_IMAP_PREFIX)) {
    return null;
  }

  return normalizeImapUid(key.slice(LEGACY_GLOBAL_IMAP_PREFIX.length));
}

function parseLegacyImapOwnershipKey(key: string) {
  if (!key.startsWith(LEGACY_IMAP_OWNERSHIP_PREFIX)) {
    return null;
  }

  return normalizeImapUid(key.slice(LEGACY_IMAP_OWNERSHIP_PREFIX.length));
}

export function writePersistedMessageStateValue<T>(
  state: Readonly<Record<string, T>>,
  message: PersistedMessageIdentitySource,
  value: T,
  explicitContext?: PersistedMessageIdentityContext,
) {
  // Unrelated Gmail/non-IMAP writes preserve pending legacy values. An explicit
  // IMAP mutation consumes the legacy value for that same UID so a later
  // hydration pass cannot resurrect stale state after the user's action.
  const resolvedIdentity = resolvePersistedMessageIdentity(
    message,
    explicitContext,
  );
  const nextState = { ...state };
  if (resolvedIdentity.isImap && resolvedIdentity.imapUid) {
    delete nextState[`${LEGACY_GLOBAL_IMAP_PREFIX}${resolvedIdentity.imapUid}`];
  }

  resolvedIdentity.stateKeys.forEach((key) => {
    nextState[key] = value;
  });

  return nextState;
}

export function removePersistedMessageStateValue<T>(
  state: Readonly<Record<string, T>>,
  message: PersistedMessageIdentitySource,
  explicitContext?: PersistedMessageIdentityContext,
) {
  const resolvedIdentity = resolvePersistedMessageIdentity(
    message,
    explicitContext,
  );
  const keysToRemove = new Set(resolvedIdentity.stateKeys);
  if (resolvedIdentity.isImap && resolvedIdentity.imapUid) {
    keysToRemove.add(
      `${LEGACY_GLOBAL_IMAP_PREFIX}${resolvedIdentity.imapUid}`,
    );
  }

  return Object.fromEntries(
    Object.entries(state).filter(([key]) => !keysToRemove.has(key)),
  ) as Record<string, T>;
}

export function addPersistedMessageIdentityKeys(
  keys: readonly string[],
  messages: readonly PersistedMessageIdentitySource[],
) {
  const nextKeys = new Set(keys);
  messages.forEach((message) => {
    const resolvedIdentity = resolvePersistedMessageIdentity(message);
    if (resolvedIdentity.isImap && resolvedIdentity.imapUid) {
      nextKeys.delete(
        `${LEGACY_GLOBAL_IMAP_PREFIX}${resolvedIdentity.imapUid}`,
      );
    }
    resolvedIdentity.stateKeys.forEach((key) => nextKeys.add(key));
  });
  return Array.from(nextKeys);
}

export function removePersistedMessageIdentityKeys(
  keys: readonly string[],
  messages: readonly PersistedMessageIdentitySource[],
) {
  const keysToRemove = new Set<string>();
  messages.forEach((message) => {
    const resolvedIdentity = resolvePersistedMessageIdentity(message);
    resolvedIdentity.stateKeys.forEach((key) => keysToRemove.add(key));
    if (resolvedIdentity.isImap && resolvedIdentity.imapUid) {
      keysToRemove.add(
        `${LEGACY_GLOBAL_IMAP_PREFIX}${resolvedIdentity.imapUid}`,
      );
    }
  });
  return keys.filter(
    (key) => !keysToRemove.has(key),
  );
}

export function resolvePersistedMessageOwnershipStateValue<T>(
  state: Readonly<Record<string, T>>,
  message: PersistedMessageIdentitySource,
  explicitContext?: PersistedMessageIdentityContext,
) {
  for (const key of getPersistedMessageOwnershipIdentityKeys(message, explicitContext)) {
    if (Object.prototype.hasOwnProperty.call(state, key)) {
      return state[key];
    }
  }

  return undefined;
}

export function writePersistedMessageOwnershipStateValue<T>(
  state: Readonly<Record<string, T>>,
  message: PersistedMessageIdentitySource,
  value: T,
  explicitContext?: PersistedMessageIdentityContext,
) {
  const nextState = { ...state };
  const [identityKey] = getPersistedMessageOwnershipIdentityKeys(
    message,
    explicitContext,
  );
  if (identityKey) {
    nextState[identityKey] = value;
  }
  return nextState;
}

type LegacyImapMigrationAnalysis = {
  allUidsAmbiguous: boolean;
  ambiguousUids: Set<string>;
  hasKnownNonImapMailbox: boolean;
  protectedNonImapOwnershipKeys: Set<string>;
  targets: Map<string, LegacyImapMigrationTarget>;
};

type LegacyImapMigrationResolution =
  | { kind: "ambiguous" }
  | { kind: "none" }
  | { kind: "unique"; target: LegacyImapMigrationTarget };

function analyzeLegacyImapMigrationTargets(
  candidates: readonly PersistedMessageIdentityCandidate[],
  options?: LegacyImapMigrationOptions,
): LegacyImapMigrationAnalysis {
  const candidatesByUid = new Map<
    string,
    Map<string, LegacyImapMigrationTarget>
  >();
  const unresolvedUids = new Set<string>();
  const knownImapMailboxIds = new Set(
    (options?.knownImapMailboxIds ?? [])
      .map((mailboxId) => normalizeIdentityComponent(mailboxId))
      .filter(Boolean) as string[],
  );
  const knownNonImapMailboxIds = new Set(
    (options?.knownNonImapMailboxIds ?? [])
      .map((mailboxId) => normalizeIdentityComponent(mailboxId))
      .filter(Boolean) as string[],
  );
  const protectedNonImapOwnershipKeys = new Set<string>();

  candidates.forEach(({ message, context }) => {
    const resolvedIdentity = resolvePersistedMessageIdentity(message, context);
    if (!resolvedIdentity.isImap) {
      resolvedIdentity.ownershipKeys.forEach((key) =>
        protectedNonImapOwnershipKeys.add(key),
      );
      return;
    }

    if (resolvedIdentity.mailboxId) {
      knownImapMailboxIds.add(resolvedIdentity.mailboxId);
    }

    if (!resolvedIdentity.imapUid) {
      resolvedIdentity.imapUidSignals.forEach((uid) =>
        unresolvedUids.add(uid),
      );
      return;
    }

    const stateKey = resolvedIdentity.stateKeys[0];
    const ownershipKey = resolvedIdentity.ownershipKeys[0];
    if (
      !resolvedIdentity.mailboxId ||
      !resolvedIdentity.technicalKey ||
      !stateKey ||
      !ownershipKey
    ) {
      // An incomplete candidate with this UID is itself a possible legacy
      // target. Ignoring it could make a different complete candidate appear
      // falsely unique.
      unresolvedUids.add(resolvedIdentity.imapUid);
      return;
    }

    const candidateFingerprint = [
      resolvedIdentity.technicalKey,
      stateKey,
      ownershipKey,
    ].join("\u0000");
    const targetsForUid =
      candidatesByUid.get(resolvedIdentity.imapUid) ??
      new Map<string, LegacyImapMigrationTarget>();
    targetsForUid.set(candidateFingerprint, {
      mailboxId: resolvedIdentity.mailboxId,
      ownershipKey,
      stateKey,
    });
    candidatesByUid.set(resolvedIdentity.imapUid, targetsForUid);
  });

  const targets = new Map<string, LegacyImapMigrationTarget>();
  const ambiguousUids = new Set(unresolvedUids);
  candidatesByUid.forEach((candidateTargets, uid) => {
    if (candidateTargets.size !== 1 || unresolvedUids.has(uid)) {
      ambiguousUids.add(uid);
      return;
    }

    // Exact duplicate copies in the local UI collapse to one fingerprint. A
    // different mailbox, provider folder, UIDVALIDITY generation, semantic
    // identity, or ownership identity produces another target and blocks the
    // migration.
    const [target] = Array.from(candidateTargets.values());
    if (target) {
      targets.set(uid, target);
    }
  });

  return {
    // A UID-only key can belong to any IMAP account. With more than one known
    // account there is no safe automatic account choice, even when only one
    // account currently has that UID loaded.
    allUidsAmbiguous: knownImapMailboxIds.size > 1,
    ambiguousUids,
    hasKnownNonImapMailbox: knownNonImapMailboxIds.size > 0,
    protectedNonImapOwnershipKeys,
    targets,
  };
}

function resolveLegacyImapMigration(
  analysis: LegacyImapMigrationAnalysis,
  uid: string,
): LegacyImapMigrationResolution {
  if (analysis.allUidsAmbiguous || analysis.ambiguousUids.has(uid)) {
    return { kind: "ambiguous" };
  }

  const target = analysis.targets.get(uid);
  return target ? { kind: "unique", target } : { kind: "none" };
}

export function migrateLegacyImapStateRecord<T>(
  state: Readonly<Record<string, T>>,
  candidates: readonly PersistedMessageIdentityCandidate[],
  options?: LegacyImapMigrationOptions,
) {
  const legacyEntries = Object.entries(state).flatMap(([key, value]) => {
    const uid = parseLegacyGlobalImapIdentityKey(key);
    return uid ? [{ key, uid, value }] : [];
  });
  if (legacyEntries.length === 0) {
    return state as Record<string, T>;
  }

  const analysis = analyzeLegacyImapMigrationTargets(candidates, options);
  const nextState = { ...state };
  let changed = false;
  legacyEntries.forEach(({ key, uid, value }) => {
    const resolution = resolveLegacyImapMigration(analysis, uid);
    if (resolution.kind === "none") {
      // No candidate is active, so the legacy value remains stored but is never
      // read by the new identity resolver. A later fully hydrated workspace can
      // still migrate it safely.
      return;
    }

    delete nextState[key];
    changed = true;
    if (
      resolution.kind === "unique" &&
      !Object.prototype.hasOwnProperty.call(
        nextState,
        resolution.target.stateKey,
      )
    ) {
      nextState[resolution.target.stateKey] = value;
    }
  });

  return changed ? nextState : (state as Record<string, T>);
}

export function migrateLegacyImapStateKeys(
  keys: readonly string[],
  candidates: readonly PersistedMessageIdentityCandidate[],
  options?: LegacyImapMigrationOptions,
) {
  const analysis = analyzeLegacyImapMigrationTargets(candidates, options);
  const nextKeys = new Set(keys);

  keys.forEach((key) => {
    const uid = parseLegacyGlobalImapIdentityKey(key);
    if (!uid) {
      return;
    }

    const resolution = resolveLegacyImapMigration(analysis, uid);
    if (resolution.kind === "none") {
      return;
    }

    nextKeys.delete(key);
    if (resolution.kind === "unique") {
      nextKeys.add(resolution.target.stateKey);
    }
  });

  return Array.from(nextKeys);
}

export function migrateLegacyImapOwnershipStateRecord<T>(
  state: Readonly<Record<string, T>>,
  candidates: readonly PersistedMessageIdentityCandidate[],
  options?: LegacyImapMigrationOptions,
) {
  const legacyEntries = Object.entries(state).flatMap(([key, value]) => {
    const uid = parseLegacyImapOwnershipKey(key);
    return uid ? [{ key, uid, value }] : [];
  });
  if (legacyEntries.length === 0) {
    return state as Record<string, T>;
  }

  const analysis = analyzeLegacyImapMigrationTargets(candidates, options);
  const nextState = { ...state };
  let changed = false;
  legacyEntries.forEach(({ key, uid, value }) => {
    if (
      analysis.hasKnownNonImapMailbox ||
      analysis.protectedNonImapOwnershipKeys.has(key)
    ) {
      return;
    }

    const resolution = resolveLegacyImapMigration(analysis, uid);
    if (resolution.kind === "none") {
      return;
    }

    delete nextState[key];
    changed = true;
    if (
      resolution.kind === "unique" &&
      !Object.prototype.hasOwnProperty.call(
        nextState,
        resolution.target.ownershipKey,
      )
    ) {
      nextState[resolution.target.ownershipKey] = value;
    }
  });

  return changed ? nextState : (state as Record<string, T>);
}

function parseLegacyMailboxPrefixedImapKey(key: string) {
  if (!key.startsWith("mailbox:")) {
    return null;
  }

  const separatorIndex = key.lastIndexOf(":imap:");
  if (separatorIndex <= "mailbox:".length) {
    return null;
  }
  const mailboxId = key.slice("mailbox:".length, separatorIndex).trim();
  const uid = normalizeImapUid(
    key.slice(separatorIndex + ":imap:".length),
  );
  if (!mailboxId || !uid) {
    return null;
  }
  return { mailboxId, uid };
}

export function migrateLegacyMailboxPrefixedImapStateKeys(
  keys: readonly string[],
  candidates: readonly PersistedMessageIdentityCandidate[],
  options?: LegacyImapMigrationOptions,
) {
  const analysis = analyzeLegacyImapMigrationTargets(candidates, options);
  const nextKeys = new Set(keys);

  keys.forEach((key) => {
    const legacyIdentity = parseLegacyMailboxPrefixedImapKey(key);
    if (!legacyIdentity) {
      return;
    }

    const resolution = resolveLegacyImapMigration(
      analysis,
      legacyIdentity.uid,
    );
    if (resolution.kind === "none") {
      return;
    }

    nextKeys.delete(key);
    if (
      resolution.kind === "unique" &&
      resolution.target.mailboxId === legacyIdentity.mailboxId
    ) {
      nextKeys.add(
        `mailbox:${resolution.target.mailboxId}:${resolution.target.stateKey}`,
      );
    }
  });

  return Array.from(nextKeys);
}
