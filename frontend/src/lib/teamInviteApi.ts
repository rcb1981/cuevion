export type TeamInviteStatus = "invited" | "accepted" | "declined" | "cancelled";

export type TeamInvite = {
  v: 1;
  token: string;
  workspaceId: string;
  inviteeEmail: string;
  inviteeName: string;
  accessLevel: "Limited";
  status: TeamInviteStatus;
  createdAt: number;
  updatedAt: number;
  createdByUserId: string;
  createdByUserName: string;
};

type IssueTeamInviteRequest = {
  workspaceId: string;
  inviteeEmail: string;
  inviteeName: string;
  accessLevel: "Limited";
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
