export type TeamInviteStatus = "invited" | "accepted" | "declined" | "cancelled";

export type TeamInvite = {
  v: 1;
  token: string;
  workspaceId: string;
  inviteeEmail: string;
  inviteeName: string;
  accessLevel: "Shared" | "Limited";
  status: TeamInviteStatus;
  createdAt: number;
  updatedAt: number;
  createdByUserId: string;
  createdByUserName: string;
};

export type TeamMemberRecord = {
  email: string;
  displayName: string;
  accessLevel: "Shared" | "Limited";
  status: "active";
};

type IssueTeamInviteRequest = {
  workspaceId: string;
  inviteeEmail: string;
  inviteeName: string;
  accessLevel: "Shared" | "Limited";
  createdByUserId: string;
  createdByUserName: string;
};

type TeamInviteError = {
  code?: string;
  message?: string;
};

type IssueTeamInviteResponse =
  | {
      ok: true;
      invite: TeamInvite;
      inviteUrl: string;
    }
  | {
      ok: false;
      error?: TeamInviteError;
    };

type FetchTeamInviteResponse =
  | {
      ok: true;
      invite: TeamInvite;
    }
  | {
      ok: false;
      error?: TeamInviteError;
    };

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

type RemoveTeamMemberRequest = {
  workspaceId: string;
  memberEmail: string;
};

type RemoveTeamMemberResponse =
  | {
      ok: true;
      member: {
        workspaceId: string;
        email: string;
        status: "removed";
        removedAt?: number;
      };
    }
  | {
      ok: false;
      error?: TeamInviteError;
    };

type MutateTeamInviteRequest = {
  token: string;
  action: {
    type: "accept" | "decline" | "cancel";
  };
};

type MutateTeamInviteResponse =
  | {
      ok: true;
      invite: TeamInvite;
    }
  | {
      ok: false;
      error?: TeamInviteError;
    };

export async function issueTeamInvite(
  request: IssueTeamInviteRequest,
): Promise<IssueTeamInviteResponse> {
  try {
    const response = await fetch("/api/team/invite?op=issue", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    });
    const payload = (await response.json()) as IssueTeamInviteResponse;

    if (
      !response.ok ||
      !payload.ok ||
      !payload.invite ||
      typeof payload.inviteUrl !== "string" ||
      payload.inviteUrl.length === 0
    ) {
      return {
        ok: false,
        error: payload && "error" in payload ? payload.error : undefined,
      };
    }

    return payload;
  } catch {
    return {
      ok: false,
      error: {
        code: "unavailable",
        message: "Could not issue team invite.",
      },
    };
  }
}

export async function fetchTeamInvite(token: string): Promise<FetchTeamInviteResponse> {
  try {
    const url = new URL("/api/team/invite", window.location.origin);
    url.searchParams.set("op", "lookup");
    url.searchParams.set("token", token);

    const response = await fetch(`${url.pathname}${url.search}`, {
      method: "GET",
      headers: {
        "Content-Type": "application/json",
      },
    });
    const payload = (await response.json()) as FetchTeamInviteResponse;

    if (!response.ok || !payload.ok || !payload.invite) {
      return {
        ok: false,
        error: payload && "error" in payload ? payload.error : undefined,
      };
    }

    return payload;
  } catch {
    return {
      ok: false,
      error: {
        code: "unavailable",
        message: "Could not load team invite.",
      },
    };
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseTeamMemberRecord(value: unknown): TeamMemberRecord | null {
  if (
    !isRecord(value) ||
    typeof value.email !== "string" ||
    typeof value.displayName !== "string" ||
    (value.accessLevel !== "Shared" && value.accessLevel !== "Limited") ||
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

function parseTeamMembersError(value: unknown): TeamInviteError | undefined {
  if (!isRecord(value) || !isRecord(value.error)) {
    return undefined;
  }

  const code = typeof value.error.code === "string" ? value.error.code : undefined;
  const message = typeof value.error.message === "string" ? value.error.message : undefined;
  return code || message ? { code, message } : undefined;
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

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = undefined;
  }

  const error = parseTeamMembersError(payload);

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

export async function removeTeamMember(
  request: RemoveTeamMemberRequest,
): Promise<RemoveTeamMemberResponse> {
  try {
    const url = new URL("/api/team/members", window.location.origin);
    url.searchParams.set("op", "remove");

    const response = await fetch(`${url.pathname}${url.search}`, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    });
    const payload = (await response.json()) as RemoveTeamMemberResponse;

    if (!response.ok || !payload.ok || !payload.member) {
      return {
        ok: false,
        error: payload && "error" in payload ? payload.error : undefined,
      };
    }

    return payload;
  } catch {
    return {
      ok: false,
      error: {
        code: "unavailable",
        message: "Could not remove team member.",
      },
    };
  }
}

export async function mutateTeamInvite(
  request: MutateTeamInviteRequest,
): Promise<MutateTeamInviteResponse> {
  try {
    const url = new URL("/api/team/invite", window.location.origin);
    url.searchParams.set("op", "action");
    url.searchParams.set("token", request.token);

    const response = await fetch(`${url.pathname}${url.search}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        action: request.action,
      }),
    });
    const payload = (await response.json()) as MutateTeamInviteResponse;

    if (!response.ok || !payload.ok || !payload.invite) {
      return {
        ok: false,
        error: payload && "error" in payload ? payload.error : undefined,
      };
    }

    return payload;
  } catch {
    return {
      ok: false,
      error: {
        code: "unavailable",
        message: "Could not update team invite.",
      },
    };
  }
}
