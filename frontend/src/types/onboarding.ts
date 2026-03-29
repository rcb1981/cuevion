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

export type WorkflowStyleId = "quiet" | "balanced" | "active";

export type ProviderId =
  | "google"
  | "microsoft"
  | "icloud"
  | "yahoo"
  | "custom_imap";
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
  focusPreferences: {
    demos: FocusPreferenceLevel;
    promo: FocusPreferenceLevel;
    finance: FocusPreferenceLevel;
    legal: FocusPreferenceLevel;
  };
  inboxCount: InboxCountId | null;
  selectedInboxes: InboxId[];
  workflowStyle: WorkflowStyleId | null;
  customInboxes: CustomInboxDefinition[];
  inboxConnections: Record<string, InboxConnection>;
}
