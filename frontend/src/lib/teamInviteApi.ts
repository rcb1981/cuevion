export type TeamAccessLevel = "Shared" | "Limited";

export type TeamInviteStatus = "pending" | "accepted" | "declined" | "cancelled";

export type TeamInvite = {
  invitationId: string;
  inviteeEmail: string;
  inviteeName: string;
  accessLevel: TeamAccessLevel;
  status: TeamInviteStatus;
  expiresAt: number;
};

export type PublicTeamInvite = Pick<
  TeamInvite,
  "inviteeName" | "accessLevel" | "status" | "expiresAt"
>;

export type TeamMemberRecord = {
  email: string;
  displayName: string;
  accessLevel: TeamAccessLevel;
  status: "active";
};

export type TeamLifecycleFailureStatus =
  | "unauthorized"
  | "forbidden"
  | "invalid"
  | "expired"
  | "used"
  | "conflict"
  | "unavailable";

export type TeamInviteError = {
  code?: string;
  message?: string;
};

type TeamLifecycleFailure = {
  ok: false;
  status: TeamLifecycleFailureStatus;
  error?: TeamInviteError;
};

type IssueTeamInviteRequest = {
  inviteeEmail: string;
  inviteeName: string;
  accessLevel: TeamAccessLevel;
};

type IssueTeamInviteResponse =
  | {
      ok: true;
      invite: TeamInvite;
      inviteUrl: string;
    }
  | TeamLifecycleFailure;

type FetchTeamInviteResponse =
  | {
      ok: true;
      invite: PublicTeamInvite;
    }
  | TeamLifecycleFailure;

type FetchTeamMembersResponse =
  | {
      ok: true;
      members: TeamMemberRecord[];
    }
  | {
      ok: false;
      status: "unauthorized" | "forbidden" | "unavailable";
      error?: TeamInviteError;
    };

type FetchPendingTeamInvitesResponse =
  | {
      ok: true;
      invitations: TeamInvite[];
    }
  | TeamLifecycleFailure;

type RemoveTeamMemberRequest = {
  memberEmail: string;
};

type RemoveTeamMemberResponse =
  | {
      ok: true;
      member: {
        email: string;
        status: "removed";
        removedAt?: number;
      };
    }
  | TeamLifecycleFailure;

type ChangeTeamMemberAccessRequest = {
  memberEmail: string;
  accessLevel: TeamAccessLevel;
};

type ChangeTeamMemberAccessResponse =
  | {
      ok: true;
      member: TeamMemberRecord;
    }
  | TeamLifecycleFailure;

type MutateTeamInviteRequest = {
  token: string;
  action: {
    type: "accept" | "decline";
  };
};

type MutateTeamInviteResponse =
  | {
      ok: true;
      invite: PublicTeamInvite;
    }
  | TeamLifecycleFailure;

type CancelTeamInviteRequest = {
  invitationId: string;
};

type CancelTeamInviteResponse =
  | {
      ok: true;
      invitation: TeamInvite;
    }
  | TeamLifecycleFailure;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseTeamInviteError(value: unknown): TeamInviteError | undefined {
  if (!isRecord(value) || !isRecord(value.error)) {
    return undefined;
  }

  const code = typeof value.error.code === "string" ? value.error.code : undefined;
  const message = typeof value.error.message === "string" ? value.error.message : undefined;
  return code || message ? { code, message } : undefined;
}

function normalizeErrorCode(error?: TeamInviteError) {
  return error?.code?.trim().toLowerCase().replace(/[\s-]+/g, "_") ?? "";
}

function classifyTeamLifecycleFailure(
  httpStatus: number | null,
  error?: TeamInviteError,
): TeamLifecycleFailureStatus {
  const code = normalizeErrorCode(error);

  if (httpStatus === 401 || code === "unauthorized" || code === "authentication_required") {
    return "unauthorized";
  }

  if (httpStatus === 403 || code === "forbidden") {
    return "forbidden";
  }

  if (code.includes("expired")) {
    return "expired";
  }

  if (
    code === "used" ||
    code.endsWith("_used") ||
    code.includes("already_used") ||
    code.includes("already_handled") ||
    code.includes("already_accepted") ||
    code.includes("consumed") ||
    code === "used_invite" ||
    code === "cancelled_invite" ||
    code === "declined_invite" ||
    code === "accepted_invite"
  ) {
    return "used";
  }

  if (code === "conflict" || code.includes("duplicate") || code.includes("already_exists")) {
    return "conflict";
  }

  if (
    code === "invalid" ||
    code.includes("invalid_invite") ||
    code.includes("invalid_invitation") ||
    code === "not_found"
  ) {
    return "invalid";
  }

  if (httpStatus === 409) {
    return "conflict";
  }

  if (httpStatus === 410) {
    return "expired";
  }

  if (httpStatus === 400 || httpStatus === 404 || httpStatus === 422) {
    return "invalid";
  }

  return "unavailable";
}

function lifecycleFailure(
  httpStatus: number | null,
  payload?: unknown,
  fallback?: TeamInviteError,
): TeamLifecycleFailure {
  const error = parseTeamInviteError(payload) ?? fallback;
  return {
    ok: false,
    status: classifyTeamLifecycleFailure(httpStatus, error),
    ...(error ? { error } : {}),
  };
}

function invalidRequest(message: string): TeamLifecycleFailure {
  return {
    ok: false,
    status: "invalid",
    error: {
      code: "invalid",
      message,
    },
  };
}

async function readJsonResponse(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

function buildApiUrl(pathname: string, params: Record<string, string>) {
  const search = new URLSearchParams(params);
  return `${pathname}?${search.toString()}`;
}

function isTeamAccessLevel(value: unknown): value is TeamAccessLevel {
  return value === "Shared" || value === "Limited";
}

function normalizeTeamIdentifier(value: string) {
  return value.trim().toLowerCase();
}

function parseFreshTeamInviteUrl(
  value: unknown,
  invitationId: string,
): string | null {
  if (
    typeof value !== "string" ||
    !value ||
    value !== value.trim() ||
    typeof window === "undefined"
  ) {
    return null;
  }
  try {
    const parsed = new URL(value);
    const tokenValues = parsed.searchParams.getAll("team_invite");
    const parameterNames = [...parsed.searchParams.keys()];
    const token = tokenValues[0] ?? "";
    const separatorIndex = token.indexOf(".");
    const tokenInvitationId = token.slice(0, separatorIndex);
    const tokenSecret = token.slice(separatorIndex + 1);
    if (
      parsed.origin !== window.location.origin ||
      parsed.pathname !== "/" ||
      parsed.hash ||
      parsed.username ||
      parsed.password ||
      parameterNames.length !== 1 ||
      parameterNames[0] !== "team_invite" ||
      tokenValues.length !== 1 ||
      separatorIndex <= 0 ||
      token.indexOf(".", separatorIndex + 1) !== -1 ||
      tokenInvitationId !== invitationId ||
      !/^[A-Za-z0-9_-]{43}$/.test(tokenSecret)
    ) {
      return null;
    }
    return value;
  } catch {
    return null;
  }
}

function parseInviteStatus(value: unknown): TeamInviteStatus | null {
  if (value === "pending" || value === "invited") {
    return "pending";
  }

  if (value === "accepted" || value === "declined" || value === "cancelled") {
    return value;
  }

  return null;
}

function readStringAlias(value: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const candidate = value[key];
    if (typeof candidate === "string" && candidate.length > 0) {
      return candidate;
    }
  }

  return null;
}

function parseTeamInvite(value: unknown): TeamInvite | null {
  if (!isRecord(value)) {
    return null;
  }

  const invitationId = readStringAlias(value, ["invitationId", "id"]);
  const inviteeEmail = readStringAlias(value, ["inviteeEmail", "recipientEmail"]);
  const inviteeName = readStringAlias(value, ["inviteeName", "displayName"]);
  const status = parseInviteStatus(value.status);

  if (
    !invitationId ||
    !inviteeEmail ||
    !inviteeName ||
    !isTeamAccessLevel(value.accessLevel) ||
    !status ||
    typeof value.expiresAt !== "number" ||
    !Number.isFinite(value.expiresAt)
  ) {
    return null;
  }

  return {
    invitationId,
    inviteeEmail,
    inviteeName,
    accessLevel: value.accessLevel,
    status,
    expiresAt: value.expiresAt,
  };
}

function parsePublicTeamInvite(value: unknown): PublicTeamInvite | null {
  if (!isRecord(value)) {
    return null;
  }

  const inviteeName = readStringAlias(value, ["inviteeName", "displayName"]);
  const status = parseInviteStatus(value.status);

  if (
    !inviteeName ||
    !isTeamAccessLevel(value.accessLevel) ||
    !status ||
    typeof value.expiresAt !== "number" ||
    !Number.isFinite(value.expiresAt)
  ) {
    return null;
  }

  return {
    inviteeName,
    accessLevel: value.accessLevel,
    status,
    expiresAt: value.expiresAt,
  };
}

function parseTeamMemberRecord(value: unknown): TeamMemberRecord | null {
  if (
    !isRecord(value) ||
    typeof value.email !== "string" ||
    typeof value.displayName !== "string" ||
    !isTeamAccessLevel(value.accessLevel) ||
    value.status !== "active"
  ) {
    return null;
  }

  return {
    email: value.email,
    displayName: value.displayName,
    accessLevel: value.accessLevel,
    status: value.status,
  };
}

function parseRemovedTeamMember(value: unknown): {
  email: string;
  status: "removed";
  removedAt?: number;
} | null {
  if (!isRecord(value) || typeof value.email !== "string" || value.status !== "removed") {
    return null;
  }

  if (
    value.removedAt !== undefined &&
    (typeof value.removedAt !== "number" || !Number.isFinite(value.removedAt))
  ) {
    return null;
  }

  return {
    email: value.email,
    status: value.status,
    ...(typeof value.removedAt === "number" ? { removedAt: value.removedAt } : {}),
  };
}

function unavailableTeamMembersResponse(
  error?: TeamInviteError,
): FetchTeamMembersResponse {
  return {
    ok: false,
    status: "unavailable",
    error: error ?? {
      code: "unavailable",
      message: "Could not load team members.",
    },
  };
}

export async function issueTeamInvite(
  request: IssueTeamInviteRequest,
): Promise<IssueTeamInviteResponse> {
  if (
    typeof request.inviteeEmail !== "string" ||
    typeof request.inviteeName !== "string" ||
    !isTeamAccessLevel(request.accessLevel)
  ) {
    return invalidRequest("Enter a valid team invitation.");
  }

  let response: Response;
  try {
    response = await fetch("/api/team/invite?op=issue", {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        inviteeEmail: request.inviteeEmail,
        inviteeName: request.inviteeName,
        accessLevel: request.accessLevel,
      }),
    });
  } catch {
    return lifecycleFailure(null, undefined, {
      code: "unavailable",
      message: "Could not issue team invite.",
    });
  }

  const payload = await readJsonResponse(response);
  if (!response.ok || !isRecord(payload) || payload.ok !== true) {
    return lifecycleFailure(response.status, payload);
  }

  const invite = parseTeamInvite(payload.invite ?? payload.invitation);
  const inviteUrl = invite
    ? parseFreshTeamInviteUrl(payload.inviteUrl, invite.invitationId)
    : null;
  if (
    !invite ||
    invite.status !== "pending" ||
    normalizeTeamIdentifier(invite.inviteeEmail) !==
      normalizeTeamIdentifier(request.inviteeEmail) ||
    invite.inviteeName !== request.inviteeName.trim() ||
    invite.accessLevel !== request.accessLevel ||
    !inviteUrl
  ) {
    return lifecycleFailure(response.status, payload, {
      code: "unavailable",
      message: "Could not issue team invite.",
    });
  }

  return {
    ok: true,
    invite,
    inviteUrl,
  };
}

export async function fetchTeamInvite(token: string): Promise<FetchTeamInviteResponse> {
  if (typeof token !== "string" || token.trim().length === 0) {
    return invalidRequest("This team invite link is invalid.");
  }

  let response: Response;
  try {
    response = await fetch(
      buildApiUrl("/api/team/invite", {
        op: "lookup",
        token,
      }),
      {
        method: "GET",
        credentials: "include",
        cache: "no-store",
      },
    );
  } catch {
    return lifecycleFailure(null, undefined, {
      code: "unavailable",
      message: "Could not load team invite.",
    });
  }

  const payload = await readJsonResponse(response);
  if (!response.ok || !isRecord(payload) || payload.ok !== true) {
    return lifecycleFailure(response.status, payload);
  }

  const invite = parsePublicTeamInvite(payload.invite ?? payload.invitation);
  if (!invite) {
    return lifecycleFailure(response.status, payload, {
      code: "unavailable",
      message: "Could not load team invite.",
    });
  }

  return { ok: true, invite };
}

export async function fetchTeamMembers(): Promise<FetchTeamMembersResponse> {
  let response: Response;
  try {
    response = await fetch("/api/team/members?op=list", {
      method: "GET",
      credentials: "include",
      cache: "no-store",
    });
  } catch {
    return unavailableTeamMembersResponse();
  }

  const payload = await readJsonResponse(response);
  const error = parseTeamInviteError(payload);

  if (response.status === 401) {
    return { ok: false, status: "unauthorized", error };
  }

  if (response.status === 403) {
    return { ok: false, status: "forbidden", error };
  }

  if (
    !response.ok ||
    !isRecord(payload) ||
    payload.ok !== true ||
    !Array.isArray(payload.members)
  ) {
    return unavailableTeamMembersResponse(error);
  }

  const members = payload.members.map(parseTeamMemberRecord);
  if (members.some((member) => member === null)) {
    return unavailableTeamMembersResponse();
  }

  return {
    ok: true,
    members: members as TeamMemberRecord[],
  };
}

export async function fetchPendingTeamInvites(): Promise<FetchPendingTeamInvitesResponse> {
  let response: Response;
  try {
    response = await fetch("/api/team/invite?op=pending", {
      method: "GET",
      credentials: "include",
      cache: "no-store",
    });
  } catch {
    return lifecycleFailure(null, undefined, {
      code: "unavailable",
      message: "Could not load pending team invitations.",
    });
  }

  const payload = await readJsonResponse(response);
  if (!response.ok || !isRecord(payload) || payload.ok !== true) {
    return lifecycleFailure(response.status, payload);
  }

  if (!Array.isArray(payload.invitations)) {
    return lifecycleFailure(response.status, payload, {
      code: "unavailable",
      message: "Could not load pending team invitations.",
    });
  }

  const invitations = payload.invitations.map(parseTeamInvite);
  if (
    invitations.some(
      (invitation) => invitation === null || invitation.status !== "pending",
    )
  ) {
    return lifecycleFailure(response.status, payload, {
      code: "unavailable",
      message: "Could not load pending team invitations.",
    });
  }

  return {
    ok: true,
    invitations: invitations as TeamInvite[],
  };
}

export async function removeTeamMember(
  request: RemoveTeamMemberRequest,
): Promise<RemoveTeamMemberResponse> {
  if (typeof request.memberEmail !== "string" || request.memberEmail.trim().length === 0) {
    return invalidRequest("Choose a valid team member.");
  }

  let response: Response;
  try {
    response = await fetch("/api/team/members?op=remove", {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        memberEmail: request.memberEmail,
      }),
    });
  } catch {
    return lifecycleFailure(null, undefined, {
      code: "unavailable",
      message: "Could not remove team member.",
    });
  }

  const payload = await readJsonResponse(response);
  if (!response.ok || !isRecord(payload) || payload.ok !== true) {
    return lifecycleFailure(response.status, payload);
  }

  const member = parseRemovedTeamMember(payload.member);
  if (
    !member ||
    normalizeTeamIdentifier(member.email) !==
      normalizeTeamIdentifier(request.memberEmail)
  ) {
    return lifecycleFailure(response.status, payload, {
      code: "unavailable",
      message: "Could not remove team member.",
    });
  }

  return { ok: true, member };
}

export async function changeTeamMemberAccess(
  request: ChangeTeamMemberAccessRequest,
): Promise<ChangeTeamMemberAccessResponse> {
  if (
    typeof request.memberEmail !== "string" ||
    request.memberEmail.trim().length === 0 ||
    !isTeamAccessLevel(request.accessLevel)
  ) {
    return invalidRequest("Choose a valid team access level.");
  }

  let response: Response;
  try {
    response = await fetch("/api/team/members?op=update-access", {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        memberEmail: request.memberEmail,
        accessLevel: request.accessLevel,
      }),
    });
  } catch {
    return lifecycleFailure(null, undefined, {
      code: "unavailable",
      message: "Could not change team member access.",
    });
  }

  const payload = await readJsonResponse(response);
  if (!response.ok || !isRecord(payload) || payload.ok !== true) {
    return lifecycleFailure(response.status, payload);
  }

  const member = parseTeamMemberRecord(payload.member);
  if (
    !member ||
    normalizeTeamIdentifier(member.email) !==
      normalizeTeamIdentifier(request.memberEmail) ||
    member.accessLevel !== request.accessLevel
  ) {
    return lifecycleFailure(response.status, payload, {
      code: "unavailable",
      message: "Could not change team member access.",
    });
  }

  return { ok: true, member };
}

export async function mutateTeamInvite(
  request: MutateTeamInviteRequest,
): Promise<MutateTeamInviteResponse> {
  if (
    typeof request.token !== "string" ||
    request.token.trim().length === 0 ||
    (request.action?.type !== "accept" && request.action?.type !== "decline")
  ) {
    return invalidRequest("Choose a valid team invitation action.");
  }

  let response: Response;
  try {
    response = await fetch(
      buildApiUrl("/api/team/invite", {
        op: "action",
        token: request.token,
      }),
      {
        method: "POST",
        credentials: "include",
        cache: "no-store",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          action: {
            type: request.action.type,
          },
        }),
      },
    );
  } catch {
    return lifecycleFailure(null, undefined, {
      code: "unavailable",
      message: "Could not update team invite.",
    });
  }

  const payload = await readJsonResponse(response);
  if (!response.ok || !isRecord(payload) || payload.ok !== true) {
    return lifecycleFailure(response.status, payload);
  }

  const invite = parsePublicTeamInvite(payload.invite ?? payload.invitation);
  const expectedStatus = request.action.type === "accept" ? "accepted" : "declined";
  if (!invite || invite.status !== expectedStatus) {
    return lifecycleFailure(response.status, payload, {
      code: "unavailable",
      message: "Could not update team invite.",
    });
  }

  return { ok: true, invite };
}

export async function cancelTeamInvite(
  request: CancelTeamInviteRequest,
): Promise<CancelTeamInviteResponse> {
  if (typeof request.invitationId !== "string" || request.invitationId.trim().length === 0) {
    return invalidRequest("Choose a valid pending invitation.");
  }

  let response: Response;
  try {
    response = await fetch("/api/team/invite?op=cancel", {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        invitationId: request.invitationId,
      }),
    });
  } catch {
    return lifecycleFailure(null, undefined, {
      code: "unavailable",
      message: "Could not cancel team invite.",
    });
  }

  const payload = await readJsonResponse(response);
  if (!response.ok || !isRecord(payload) || payload.ok !== true) {
    return lifecycleFailure(response.status, payload);
  }

  const invitation = parseTeamInvite(payload.invitation ?? payload.invite);
  if (
    !invitation ||
    invitation.status !== "cancelled" ||
    invitation.invitationId !== request.invitationId.trim()
  ) {
    return lifecycleFailure(response.status, payload, {
      code: "unavailable",
      message: "Could not cancel team invite.",
    });
  }

  return { ok: true, invitation };
}
