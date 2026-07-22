import type { InternalRole } from "../lib/roleMapping";

export type RoleId =
  | "label_ar_manager"
  | "label_manager"
  | "ar_manager"
  | "dj"
  | "producer"
  | "dj_producer"
  | "label_owner"
  | "legal"
  | "finance"
  | "royalty"
  | "sync_licensing"
  | "social_media_manager"
  | "promo_manager"
  | "distribution"
  | "admin";

export type InboxCountId = "1" | "2" | "3" | "4+" | "not_sure";

export type PresetInboxId =
  | "main"
  | "demo"
  | "business"
  | "promo"
  | "legal"
  | "finance"
  | "royalty"
  | "sync";

export type InboxId = PresetInboxId | `custom:${string}`;
export type PrimaryInboxType = "personal" | "work";

export type ProviderId =
  | "google"
  | "microsoft"
  | "icloud"
  | "yahoo"
  | "custom_imap";
export type InboxConnectionMethod = "imap" | "oauth";
export type InboxConnectionStatus =
  | "not_connected"
  | "oauth_required"
  | "waiting_for_authentication"
  | "authenticated_pending_activation"
  | "connected"
  | "connection_failed";
export type FocusPreferenceLevel = "high" | "medium" | "low";
export type SelectableFocusPreferenceLevel = "medium" | "low";

export interface CustomImapSettings {
  host: string;
  port: string;
  ssl: boolean;
  username: string;
  password: string;
}

export type CustomSmtpSecurity = "ssl" | "starttls";

export interface CustomSmtpSettings {
  host: string;
  port: string;
  security: CustomSmtpSecurity;
  username: string;
  password: string;
  useSameCredentials: boolean;
}

export interface InboxConnection {
  provider: ProviderId | null;
  email: string;
  connected: boolean;
  connectionMethod: InboxConnectionMethod | null;
  connectionStatus: InboxConnectionStatus;
  connectionMessage?: string | null;
  oauthAuthorizationUrl?: string | null;
  customImap: CustomImapSettings;
  customSmtp: CustomSmtpSettings;
}

export interface CustomInboxDefinition {
  id: InboxId;
  name: string;
}

export interface OnboardingChoices {
  primaryRole: RoleId | null;
  internalRole: InternalRole | null;
  secondaryRole: RoleId | null;
  primaryInbox: InboxId | null;
  primaryInboxType: PrimaryInboxType | null;
  focusPreferences: {
    demos: FocusPreferenceLevel;
    promo: FocusPreferenceLevel;
    finance: FocusPreferenceLevel;
    legal: FocusPreferenceLevel;
    business: FocusPreferenceLevel;
    updates: FocusPreferenceLevel;
    distribution: FocusPreferenceLevel;
    royalties: FocusPreferenceLevel;
    promoReminders: FocusPreferenceLevel;
    paymentReminders: FocusPreferenceLevel;
  };
  inboxCount: InboxCountId | null;
  selectedInboxes: InboxId[];
  customInboxes: CustomInboxDefinition[];
}

interface OnboardingSessionV1Base {
  schemaVersion: 1;
  currentStep: number;
  choices: OnboardingChoices;
}

export type OnboardingSessionV1 =
  | (OnboardingSessionV1Base & { completed: false })
  | (OnboardingSessionV1Base & { completed: true });

export const ONBOARDING_STEP_MIN = 0;
export const ONBOARDING_STEP_MAX = 3;

export function clampOnboardingStep(step: number): number {
  if (!Number.isFinite(step)) {
    return ONBOARDING_STEP_MIN;
  }

  return Math.min(
    ONBOARDING_STEP_MAX,
    Math.max(ONBOARDING_STEP_MIN, Math.trunc(step)),
  );
}

export interface OnboardingState extends OnboardingChoices {
  inboxConnections: Record<string, InboxConnection>;
}

export function normalizeFocusPreferenceLevel(
  value: unknown,
): SelectableFocusPreferenceLevel {
  return value === "low" ? "low" : "medium";
}

export function normalizeFocusPreferences(
  value: Partial<OnboardingState["focusPreferences"]> | null | undefined,
): OnboardingState["focusPreferences"] {
  return {
    demos: normalizeFocusPreferenceLevel(value?.demos),
    promo: normalizeFocusPreferenceLevel(value?.promo),
    finance: normalizeFocusPreferenceLevel(value?.finance),
    legal: normalizeFocusPreferenceLevel(value?.legal),
    business: normalizeFocusPreferenceLevel(value?.business),
    updates: normalizeFocusPreferenceLevel(value?.updates),
    distribution: normalizeFocusPreferenceLevel(value?.distribution),
    royalties: normalizeFocusPreferenceLevel(value?.royalties),
    promoReminders: normalizeFocusPreferenceLevel(value?.promoReminders),
    paymentReminders: normalizeFocusPreferenceLevel(value?.paymentReminders),
  };
}
