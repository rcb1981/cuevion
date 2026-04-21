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
  | "connected"
  | "connection_failed";
export type FocusPreferenceLevel = "high" | "medium" | "low";

export interface CustomImapSettings {
  host: string;
  port: string;
  ssl: boolean;
  username: string;
  password: string;
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
}

export interface CustomInboxDefinition {
  id: InboxId;
  name: string;
}

export interface OnboardingState {
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
  inboxConnections: Record<string, InboxConnection>;
}
