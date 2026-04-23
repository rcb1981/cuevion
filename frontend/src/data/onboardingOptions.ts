import type {
  InboxCountId,
  InboxId,
  OnboardingState,
  PresetInboxId,
  ProviderId,
  RoleId,
} from "../types/onboarding";
import { onboardingText } from "../copy/onboardingCopy";
import { createDefaultCustomSmtpSettings } from "../lib/inboxProviderDefaults";

function normalizeRoleOption(role: {
  id: RoleId;
  label: string;
  description: string;
}) {
  if (role.id === "admin" && role.label === "Admin") {
    return { ...role, label: "Operations Manager" };
  }

  return { ...role };
}

export const primaryRoleOptions: Array<{
  id: RoleId;
  label: string;
  description: string;
}> = onboardingText.roles.primary.map(normalizeRoleOption);

export const secondaryRoleOptions: Array<{
  id: RoleId;
  label: string;
  description: string;
}> = onboardingText.roles.secondary.map(normalizeRoleOption);

export const extraRoleOptions: Array<{
  id: RoleId;
  label: string;
  description: string;
}> = onboardingText.roles.extra.map(normalizeRoleOption);

export const allRoleOptions = [
  ...new Map(
    [...primaryRoleOptions, ...secondaryRoleOptions, ...extraRoleOptions].map(
      (role) => [role.id, role],
    ),
  ).values(),
];

export const inboxCountOptions: Array<{ id: InboxCountId; label: string }> =
  onboardingText.inboxCount.options.map((option) => ({ ...option }));

export const mainInboxOptions: Array<{ id: PresetInboxId; label: string }> =
  onboardingText.inboxSetup.main.map((option) => ({
    ...option,
    label: option.id === "main" ? "Main / Personal Inbox" : option.label,
  }));

export const specializedInboxOptions: Array<{ id: PresetInboxId; label: string }> =
  onboardingText.inboxSetup.specialized.map((option) => ({ ...option }));

export const providerOptions: Array<{ id: ProviderId; label: string }> =
  onboardingText.connect.providers.map((provider) => ({ ...provider }));

export const createInboxConnection = () => ({
  provider: null,
  email: "",
  connected: false,
  connectionMethod: null,
  connectionStatus: "not_connected" as const,
  connectionMessage: null,
  oauthAuthorizationUrl: null,
  customImap: {
    host: "",
    port: "",
    ssl: true,
    username: "",
    password: "",
  },
  customSmtp: createDefaultCustomSmtpSettings(),
});

export function createCustomInboxId(name: string): InboxId {
  const normalizedName = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");

  return `custom:${normalizedName || "inbox"}-${Date.now().toString(36)}`;
}

export const initialOnboardingState: OnboardingState = {
  primaryRole: null,
  internalRole: null,
  secondaryRole: null,
  primaryInbox: "main",
  primaryInboxType: null,
  focusPreferences: {
    demos: "medium",
    promo: "medium",
    finance: "medium",
    legal: "medium",
    business: "medium",
    updates: "medium",
    distribution: "medium",
    royalties: "medium",
    promoReminders: "medium",
    paymentReminders: "medium",
  },
  inboxCount: null,
  selectedInboxes: [],
  customInboxes: [],
  inboxConnections: {
    main: createInboxConnection(),
    demo: createInboxConnection(),
    business: createInboxConnection(),
    promo: createInboxConnection(),
    legal: createInboxConnection(),
    finance: createInboxConnection(),
    royalty: createInboxConnection(),
    sync: createInboxConnection(),
  },
};
