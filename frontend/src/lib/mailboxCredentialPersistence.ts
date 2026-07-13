type JsonRecord = Record<string, unknown>;

export type SanitizedStoredJson = {
  value: unknown;
  serialized: string | null;
  rewriteRequired: boolean;
  valid: boolean;
};

function isRecord(value: unknown): value is JsonRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function sanitizeCredentialSettings(value: unknown): unknown {
  if (!isRecord(value)) {
    return value;
  }

  if (!("password" in value)) {
    return { ...value };
  }

  return { ...value, password: "" };
}

export function sanitizeMailboxConnectionCredentials(value: unknown): unknown {
  if (!isRecord(value)) {
    return value;
  }

  const sanitized: JsonRecord = { ...value };
  if ("customImap" in sanitized) {
    sanitized.customImap = sanitizeCredentialSettings(sanitized.customImap);
  }
  if ("customSmtp" in sanitized) {
    sanitized.customSmtp = sanitizeCredentialSettings(sanitized.customSmtp);
  }
  return sanitized;
}

export function sanitizeManagedInboxCredentials(value: unknown): unknown {
  if (!Array.isArray(value)) {
    return value;
  }

  return value.map((mailbox) => sanitizeMailboxConnectionCredentials(mailbox));
}

function sanitizeInboxConnections(value: unknown): unknown {
  if (!isRecord(value)) {
    return value;
  }

  return Object.fromEntries(
    Object.entries(value).map(([mailboxId, connection]) => [
      mailboxId,
      sanitizeMailboxConnectionCredentials(connection),
    ]),
  );
}

function sanitizeOnboardingState(value: unknown): unknown {
  if (!isRecord(value)) {
    return value;
  }

  const sanitized: JsonRecord = { ...value };
  if ("inboxConnections" in sanitized) {
    sanitized.inboxConnections = sanitizeInboxConnections(sanitized.inboxConnections);
  }
  return sanitized;
}

export function sanitizeAccountConfigCredentials(value: unknown): unknown {
  if (Array.isArray(value)) {
    return sanitizeManagedInboxCredentials(value);
  }
  if (!isRecord(value)) {
    return value;
  }

  const sanitized: JsonRecord = { ...value };
  if ("state" in sanitized) {
    sanitized.state = sanitizeOnboardingState(sanitized.state);
  }
  if ("inboxConnections" in sanitized) {
    sanitized.inboxConnections = sanitizeInboxConnections(sanitized.inboxConnections);
  }
  if ("managedInboxes" in sanitized) {
    sanitized.managedInboxes = sanitizeManagedInboxCredentials(sanitized.managedInboxes);
  }
  if ("onboardingSession" in sanitized) {
    sanitized.onboardingSession = sanitizeAccountConfigCredentials(
      sanitized.onboardingSession,
    );
  }
  return sanitized;
}

export function sanitizeStoredMailboxCredentialJson(rawValue: string): SanitizedStoredJson {
  try {
    const parsed = JSON.parse(rawValue) as unknown;
    const sanitized = sanitizeAccountConfigCredentials(parsed);
    const serialized = JSON.stringify(sanitized);
    return {
      value: sanitized,
      serialized,
      rewriteRequired: serialized !== rawValue,
      valid: true,
    };
  } catch {
    return {
      value: null,
      serialized: null,
      rewriteRequired: false,
      valid: false,
    };
  }
}
