import type { InternalRole } from "../lib/roleMapping";
import type {
  InboxCountId,
  InboxId,
  OnboardingState,
  RoleId,
} from "./onboarding";

export interface UserConfig {
  primaryRole: RoleId | null;
  internalRole: InternalRole | null;
  focusPreferences: OnboardingState["focusPreferences"];
  inboxCount: InboxCountId | null;
  selectedInboxes: InboxId[];
  primaryInboxType: OnboardingState["primaryInboxType"];
}
