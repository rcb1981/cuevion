export type CustomSmtpAvailabilityInput = {
  provider: string | null;
  connected: boolean;
  connectionStatus: string;
  customImap: {
    username: string;
  };
  customSmtp: {
    host: string;
    port: string;
    security: string;
    username: string;
    useSameCredentials: boolean;
  };
};

export type CustomImapCredentialStatus = {
  imapPasswordSet?: boolean;
  smtpPasswordSet?: boolean;
};

const AUTHORITATIVE_SMTP_CONFIG_FIELDS = [
  "host",
  "port",
  "security",
  "username",
  "useSameCredentials",
] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function isValidSmtpPort(value: string) {
  const normalizedValue = value.trim();
  if (!/^\d+$/.test(normalizedValue)) {
    return false;
  }

  const port = Number(normalizedValue);
  return Number.isInteger(port) && port >= 1 && port <= 65535;
}

export function hasAuthoritativeCustomSmtpConfiguration(value: unknown) {
  if (!isRecord(value)) {
    return false;
  }

  return Object.entries(value).some(
    ([key, fieldValue]) =>
      key !== "password" ||
      (typeof fieldValue === "string" && fieldValue.length > 0),
  );
}

export function isCompleteAuthoritativeCustomSmtpConfiguration(
  value: unknown,
) {
  if (!isRecord(value)) {
    return false;
  }

  const configFields = Object.keys(value)
    .filter((key) => key !== "password")
    .sort();
  const expectedFields = [...AUTHORITATIVE_SMTP_CONFIG_FIELDS].sort();
  const host = value.host;
  const port = value.port;
  const username = value.username;
  const password = value.password;

  return Boolean(
    JSON.stringify(configFields) === JSON.stringify(expectedFields) &&
      typeof host === "string" &&
      host.length > 0 &&
      host === host.trim() &&
      typeof port === "string" &&
      port === port.trim() &&
      isValidSmtpPort(port) &&
      (value.security === "ssl" || value.security === "starttls") &&
      typeof username === "string" &&
      username === username.trim() &&
      typeof value.useSameCredentials === "boolean" &&
      (value.useSameCredentials || username.length > 0) &&
      (password === undefined || password === ""),
  );
}

export function isAuthoritativeCustomImapIncomingConnected(
  mailbox: Pick<
    CustomSmtpAvailabilityInput,
    "provider" | "connected" | "connectionStatus"
  >,
  credentialStatus: CustomImapCredentialStatus | null | undefined,
) {
  return Boolean(
    mailbox.provider === "custom_imap" &&
      mailbox.connected === true &&
      mailbox.connectionStatus === "connected" &&
      credentialStatus?.imapPasswordSet === true,
  );
}

export function isCustomSmtpSendAvailable(
  mailbox: CustomSmtpAvailabilityInput,
) {
  if (
    mailbox.provider !== "custom_imap" ||
    mailbox.connected !== true ||
    mailbox.connectionStatus !== "connected"
  ) {
    return false;
  }

  const smtpHost = mailbox.customSmtp.host.trim();
  const smtpSecurity = mailbox.customSmtp.security;
  const effectiveUsername = mailbox.customSmtp.useSameCredentials
    ? mailbox.customImap.username.trim()
    : mailbox.customSmtp.username.trim();

  return Boolean(
    smtpHost &&
      isValidSmtpPort(mailbox.customSmtp.port) &&
      (smtpSecurity === "ssl" || smtpSecurity === "starttls") &&
      effectiveUsername,
  );
}
