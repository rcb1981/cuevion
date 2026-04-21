import type {
  CustomImapSettings,
  InboxConnectionMethod,
  InboxConnectionStatus,
  ProviderId,
} from "../types/onboarding";

export function isImapCredentialsProvider(provider: ProviderId | null) {
  return provider === "custom_imap" || provider === "microsoft";
}

export function isOAuthConnectionProvider(provider: ProviderId | null) {
  return provider === "google";
}

export function getProviderConnectionMethod(
  provider: ProviderId | null,
): InboxConnectionMethod | null {
  if (!provider) {
    return null;
  }

  return isOAuthConnectionProvider(provider) ? "oauth" : "imap";
}

export function getDefaultConnectionStatus(
  provider: ProviderId | null,
): InboxConnectionStatus {
  if (provider === "google") {
    return "oauth_required";
  }

  return "not_connected";
}

export function createDefaultGoogleImapSettings(
  email = "",
): CustomImapSettings {
  return {
    host: "imap.gmail.com",
    port: "993",
    ssl: true,
    username: email.trim(),
    password: "",
  };
}

export function createDefaultMicrosoftImapSettings(
  email = "",
): CustomImapSettings {
  return {
    host: "outlook.office365.com",
    port: "993",
    ssl: true,
    username: email.trim(),
    password: "",
  };
}

export function usesEmailAsImapUsername(provider: ProviderId | null) {
  return provider === "microsoft";
}

export function applyProviderDefaults(
  provider: ProviderId | null,
  currentSettings: CustomImapSettings,
  email = "",
): CustomImapSettings {
  if (provider === "google") {
    return {
      host: "",
      port: "",
      ssl: true,
      username: "",
      password: "",
    };
  }

  if (provider === "microsoft") {
    return {
      ...createDefaultMicrosoftImapSettings(email),
      password: currentSettings.password,
    };
  }

  if (provider === "custom_imap") {
    return currentSettings;
  }

  return {
    host: "",
    port: "",
    ssl: true,
    username: "",
    password: "",
  };
}

export function getPasswordLabel(provider: ProviderId | null) {
  return provider === "google" ? "App password" : "Password";
}
