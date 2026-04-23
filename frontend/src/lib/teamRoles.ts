export const TEAM_ROLES = ["Limited", "Shared", "Editor", "Admin"] as const;

export type TeamRole = (typeof TEAM_ROLES)[number];

export type TeamInviteLifecycleStatus =
  | "Invited"
  | "Active"
  | "Declined"
  | "Invite cancelled"
  | "Access removed";

export function normalizeTeamRole(value: unknown): TeamRole {
  if (value === "Review" || value === "Shared") {
    return "Shared";
  }

  if (value === "Admin" || value === "Editor" || value === "Limited") {
    return value;
  }

  return "Limited";
}

export function getTeamRoleLabel(role: TeamRole) {
  return role === "Limited" ? "Invite-only" : role;
}

export function canViewSharedCollaborations(role: TeamRole) {
  return role === "Shared" || role === "Editor" || role === "Admin";
}

export function canManageTeam(role: TeamRole) {
  return role === "Admin";
}

export function canAccessInboxes(_role: TeamRole) {
  return false;
}

export function canUseInternalCollaboration(_role: TeamRole) {
  return false;
}

export function canManageWorkspace(_role: TeamRole) {
  return false;
}
