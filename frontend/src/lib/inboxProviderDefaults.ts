import type { CustomImapSettings, ProviderId } from "../types/onboarding";

export function isImapCredentialsProvider(provider: ProviderId | null) {
  return (
    provider === "custom_imap" ||
    provider === "google" ||
    provider === "microsoft"
  );
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
  return provider === "google" || provider === "microsoft";
}

export function applyProviderDefaults(
  provider: ProviderId | null,
  currentSettings: CustomImapSettings,
  email = "",
): CustomImapSettings {
  if (provider === "google") {
    return {
      ...createDefaultGoogleImapSettings(email),
      password: currentSettings.password,
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
