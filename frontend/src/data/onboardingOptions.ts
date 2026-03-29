import type {
  InboxCountId,
  InboxId,
  OnboardingState,
  PresetInboxId,
  ProviderId,
  RoleId,
  WorkflowStyleId,
} from "../types/onboarding";
import { onboardingText } from "../copy/onboardingCopy";

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
  onboardingText.inboxSetup.main.map((option) => ({ ...option }));

export const specializedInboxOptions: Array<{ id: PresetInboxId; label: string }> =
  onboardingText.inboxSetup.specialized.map((option) => ({ ...option }));

export const workflowStyleOptions: Array<{
  id: WorkflowStyleId;
  label: string;
  description: string;
  tooltip: string;
  recommended?: boolean;
}> = onboardingText.workflowStyle.options.map((option) => ({ ...option }));

export const providerOptions: Array<{ id: ProviderId; label: string }> =
  onboardingText.connect.providers.map((provider) => ({ ...provider }));

export const createInboxConnection = () => ({
  provider: null,
  email: "",
  connected: false,
  customImap: {
    host: "",
    port: "",
    ssl: true,
    username: "",
    password: "",
  },
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
  workflowStyle: null,
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
