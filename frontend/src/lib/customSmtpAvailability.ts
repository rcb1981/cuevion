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

function isValidSmtpPort(value: string) {
  const normalizedValue = value.trim();
  if (!/^\d+$/.test(normalizedValue)) {
    return false;
  }

  const port = Number(normalizedValue);
  return Number.isInteger(port) && port >= 1 && port <= 65535;
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
