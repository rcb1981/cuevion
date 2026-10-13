import type { CustomImapSettings, CustomSmtpSettings, ProviderId } from "../types/onboarding";
import {
  createDefaultCustomSmtpSettings,
  createICloudMailPreset,
  getDefaultConnectionStatus,
  getProviderConnectionMethod,
  isICloudMailPreset,
} from "./inboxProviderDefaults";

/**
 * UI choices for adding a new inbox. iCloud is stored as Custom IMAP with
 * Apple's preset, not as an unsupported standalone provider.
 */
export const newInboxProviderChoices = [
  { id: "google", label: "Gmail / Google Workspace", description: "Connect securely with Google." },
  { id: "microsoft", label: "Microsoft 365 / Outlook", description: "Read-only pilot: Inbox and Sent." },
  { id: "icloud", label: "iCloud Mail", description: "Connect using an Apple app-specific password." },
  { id: "custom_imap", label: "Custom IMAP", description: "Set up a secure IMAP mail server." },
] as const;

export type NewInboxProviderChoiceId = (typeof newInboxProviderChoices)[number]["id"];

function emptyImapSettings(): CustomImapSettings {
  return {
    host: "",
    port: "",
    ssl: true,
    username: "",
    password: "",
  };
}

export function getNewInboxProviderSelection(
  choice: NewInboxProviderChoiceId,
  email: string,
) {
  const provider: ProviderId = choice === "icloud" ? "custom_imap" : choice;
  const preset = choice === "icloud" ? createICloudMailPreset(email) : null;

  return {
    provider,
    connectionMethod: getProviderConnectionMethod(provider),
    connectionStatus: getDefaultConnectionStatus(provider),
    customImap: preset?.imap ?? emptyImapSettings(),
    customSmtp: preset?.smtp ?? createDefaultCustomSmtpSettings(),
  };
}

export function getNewInboxProviderChoice(
  provider: ProviderId | null,
  imap: CustomImapSettings,
  smtp: CustomSmtpSettings,
): NewInboxProviderChoiceId | null {
  if (provider === "custom_imap" && isICloudMailPreset(imap, smtp)) {
    return "icloud";
  }

  if (provider === "google" || provider === "microsoft" || provider === "custom_imap") {
    return provider;
  }

  return null;
}

export function updateNewICloudPresetEmail(
  provider: ProviderId | null,
  imap: CustomImapSettings,
  smtp: CustomSmtpSettings,
  email: string,
): CustomImapSettings {
  // Preserve passwords and any user-customized configuration.
  if (provider !== "custom_imap" || !isICloudMailPreset(imap, smtp)) {
    return imap;
  }
  return { ...imap, username: email.trim() };
}
