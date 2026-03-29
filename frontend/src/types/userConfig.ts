import type { InternalRole } from "../lib/roleMapping";
import type {
  InboxCountId,
  InboxId,
  OnboardingState,
  RoleId,
  WorkflowStyleId,
} from "./onboarding";

export interface UserConfig {
  primaryRole: RoleId | null;
  internalRole: InternalRole | null;
  focusPreferences: OnboardingState["focusPreferences"];
  workflowStyle: WorkflowStyleId | null;
  inboxCount: InboxCountId | null;
  selectedInboxes: InboxId[];
}
