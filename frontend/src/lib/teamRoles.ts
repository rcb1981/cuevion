export const TEAM_ROLES = ["Shared", "Limited"] as const;

export type TeamRole = (typeof TEAM_ROLES)[number];

export type TeamInviteLifecycleStatus =
  | "Invited"
  | "Active"
  | "Declined"
  | "Invite cancelled"
  | "Access removed";

export function normalizeTeamRole(value: unknown): TeamRole {
  if (value === "Review" || value === "Admin" || value === "Editor" || value === "Shared") {
    return "Shared";
  }

  if (value === "Limited") {
    return "Limited";
  }

  return "Limited";
}

export function getTeamRoleLabel(role: TeamRole) {
  return role;
}

export function canViewSharedCollaborations(role: TeamRole) {
  return role === "Shared";
}

export function canManageTeam(_role: TeamRole) {
  return false;
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
