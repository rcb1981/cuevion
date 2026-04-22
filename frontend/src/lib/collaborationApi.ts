export type CollaborationParticipant = {
  id: string;
  name: string;
  email: string;
  kind: "internal" | "external";
  status: "active" | "invited" | "declined";
  externalReviewToken?: string;
};

export type CollaborationMention = {
  id: string;
  name: string;
  email: string;
  handle: string;
  notify: boolean;
};

export type CollaborationMessage = {
  id: string;
  authorId: string;
  authorName: string;
  text: string;
  timestamp: number;
  visibility?: "internal" | "shared";
  mentions?: CollaborationMention[];
};

export type CollaborationThread = {
  v: 1;
  workspaceId: string;
  mailboxId: string;
  messageId: string;
  sourceMessage: {
    id: string;
    subject: string;
    sender: string;
    from: string;
    timestamp: string;
    snippet: string;
    body: string[];
    bodyHtml?: string;
  };
  isShared: boolean;
  collaboration: {
    state: "needs_review" | "needs_action" | "note_only" | "resolved";
    requestedBy: string;
    requestedUserId: string;
    requestedUserName: string;
    createdAt: number;
    updatedAt: number;
    participants: CollaborationParticipant[];
    resolvedAt?: number;
    resolvedByUserId?: string;
    resolvedByUserName?: string;
    previewText?: string;
    messages: CollaborationMessage[];
  };
};

export type CollaborationInvite = {
  v: 1;
  token: string;
  workspaceId: string;
  mailboxId: string;
  messageId: string;
  inviteeEmail: string;
  participantId: string;
  status: "active" | "revoked" | "expired";
  createdAt: number;
  updatedAt: number;
  createdByUserId: string;
  createdByUserName: string;
  expiresAt?: number;
};

export type FetchCollaborationThreadsGetManyRequest = {
  workspaceId: string;
  mailboxId?: string;
  messageIds: string[];
  messages?: Array<{
    id: string;
    imapUid?: string;
    subject?: string;
    from?: string;
    timestamp?: string;
  }>;
};

type FetchCollaborationThreadsGetManyResponse = {
  ok?: boolean;
  threadsByMessageId?: Record<string, CollaborationThread>;
};

type CreateCollaborationThreadRequest = {
  workspaceId: string;
  mailboxId: string;
  sourceMessage: CollaborationThread["sourceMessage"];
  collaboration: CollaborationThread["collaboration"];
  isShared: boolean;
};

type CreateCollaborationThreadResponse = {
  ok: boolean;
  thread?: CollaborationThread;
  error?: {
    code?: string;
    message?: string;
  };
};

type IssueCollaborationInviteRequest = {
  workspaceId: string;
  mailboxId: string;
  messageId: string;
  inviteeEmail: string;
  createdByUserId: string;
  createdByUserName: string;
};

type IssueCollaborationInviteResponse =
  | {
      ok: true;
      invite: CollaborationInvite;
      thread: CollaborationThread;
      inviteUrl: string;
    }
  | {
      ok: false;
      error?: {
        code?: string;
        message?: string;
      };
    };

type MutateCollaborationThreadRequest = {
  workspaceId: string;
  messageId: string;
  expectedUpdatedAt?: number;
  action:
    | {
        type: "reply";
        authorId: string;
        authorName: string;
        text: string;
        visibility: "internal" | "shared";
        mentions?: CollaborationMention[];
      }
    | {
        type: "participants_set";
        participants: CollaborationParticipant[];
      }
    | {
        type: "resolve";
        resolvedByUserId: string;
        resolvedByUserName: string;
      }
    | {
        type: "reopen";
      };
};

export type MutateCollaborationThreadResponse =
  | {
      ok: true;
      thread: CollaborationThread;
    }
  | {
      ok: false;
      code: "stale_thread";
      thread: CollaborationThread;
    }
  | {
      ok: false;
      code: "unavailable";
    };

type FetchCollaborationInviteOptions = {
  viewer?: "workspace" | "external";
};

type FetchCollaborationInviteResponse =
  | {
      ok: true;
      invite: CollaborationInvite;
      thread: CollaborationThread;
    }
  | {
      ok: false;
      code: "invalid_invite" | "expired_invite" | "unavailable";
    };

type MutateCollaborationInviteRequest = {
  token: string;
  expectedUpdatedAt?: number;
  action: {
    type: "reply";
    text: string;
    authorName?: string;
    mentions?: CollaborationMention[];
  };
};

export type MutateCollaborationInviteResponse =
  | {
      ok: true;
      thread: CollaborationThread;
    }
  | {
      ok: false;
      code: "stale_thread";
      thread: CollaborationThread;
    }
  | {
      ok: false;
      code: "invalid_invite" | "expired_invite" | "unavailable";
    };

export async function fetchCollaborationThreadsGetMany(
  request: FetchCollaborationThreadsGetManyRequest,
): Promise<Record<string, CollaborationThread>> {
  try {
    const response = await fetch("/api/collaboration/thread?op=get-many", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    });

    const payload = (await response.json()) as FetchCollaborationThreadsGetManyResponse;

    if (!response.ok || !payload.threadsByMessageId || typeof payload.threadsByMessageId !== "object") {
      return {};
    }

    return payload.threadsByMessageId;
  } catch {
    return {};
  }
}

export async function createCollaborationThread(
  request: CreateCollaborationThreadRequest,
): Promise<CreateCollaborationThreadResponse> {
  try {
    const response = await fetch("/api/collaboration/thread?op=create", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    });

    const payload = (await response.json()) as CreateCollaborationThreadResponse;

    if (!response.ok || !payload.ok || !payload.thread) {
      return {
        ok: false,
        error: payload.error,
      };
    }

    return {
      ok: true,
      thread: payload.thread,
    };
  } catch {
    return {
      ok: false,
      error: {
        code: "unavailable",
        message: "Could not create canonical collaboration thread.",
      },
    };
  }
}

export async function mutateCollaborationThread(
  request: MutateCollaborationThreadRequest,
): Promise<MutateCollaborationThreadResponse> {
  try {
    const response = await fetch("/api/collaboration/thread?op=action", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    });

    const payload = (await response.json()) as
      | {
          ok?: boolean;
          code?: string;
          thread?: CollaborationThread;
        }
      | undefined;

    if (response.ok && payload?.ok && payload.thread) {
      return {
        ok: true,
        thread: payload.thread,
      };
    }

    if (payload?.code === "stale_thread" && payload.thread) {
      return {
        ok: false,
        code: "stale_thread",
        thread: payload.thread,
      };
    }

    return {
      ok: false,
      code: "unavailable",
    };
  } catch {
    return {
      ok: false,
      code: "unavailable",
    };
  }
}

export async function issueCollaborationInvite(
  request: IssueCollaborationInviteRequest,
): Promise<IssueCollaborationInviteResponse> {
  try {
    const response = await fetch("/api/collaboration/invite?op=issue", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    });

    const payload = (await response.json()) as IssueCollaborationInviteResponse;

    if (
      !response.ok ||
      !payload.ok ||
      !payload.invite ||
      !payload.thread ||
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
        message: "Could not issue collaboration invite.",
      },
    };
  }
}

export async function fetchCollaborationInvite(
  token: string,
  options?: FetchCollaborationInviteOptions,
): Promise<FetchCollaborationInviteResponse> {
  try {
    const url = new URL("/api/collaboration/invite", window.location.origin);
    url.searchParams.set("op", "lookup");
    url.searchParams.set("token", token);
    if (options?.viewer) {
      url.searchParams.set("viewer", options.viewer);
    }

    const response = await fetch(`${url.pathname}${url.search}`, {
      method: "GET",
      headers: {
        "Content-Type": "application/json",
      },
    });

    const payload = (await response.json()) as
      | {
          ok?: boolean;
          invite?: CollaborationInvite;
          thread?: CollaborationThread;
          error?: {
            code?: string;
          };
        }
      | undefined;

    if (response.ok && payload?.ok && payload.invite && payload.thread) {
      return {
        ok: true,
        invite: payload.invite,
        thread: payload.thread,
      };
    }

    const errorCode = payload?.error?.code;
    if (errorCode === "invalid_invite" || errorCode === "expired_invite") {
      return {
        ok: false,
        code: errorCode,
      };
    }

    return {
      ok: false,
      code: "unavailable",
    };
  } catch {
    return {
      ok: false,
      code: "unavailable",
    };
  }
}

export async function mutateCollaborationInvite(
  request: MutateCollaborationInviteRequest,
): Promise<MutateCollaborationInviteResponse> {
  try {
    const url = new URL("/api/collaboration/invite", window.location.origin);
    url.searchParams.set("op", "action");
    url.searchParams.set("token", request.token);
    const response = await fetch(
      `${url.pathname}${url.search}`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          expectedUpdatedAt: request.expectedUpdatedAt,
          action: request.action,
        }),
      },
    );

    const payload = (await response.json()) as
      | {
          ok?: boolean;
          code?: string;
          thread?: CollaborationThread;
          error?: {
            code?: string;
          };
        }
      | undefined;

    if (response.ok && payload?.ok && payload.thread) {
      return {
        ok: true,
        thread: payload.thread,
      };
    }

    if (payload?.code === "stale_thread" && payload.thread) {
      return {
        ok: false,
        code: "stale_thread",
        thread: payload.thread,
      };
    }

    const errorCode = payload?.error?.code;
    if (errorCode === "invalid_invite" || errorCode === "expired_invite") {
      return {
        ok: false,
        code: errorCode,
      };
    }

    return {
      ok: false,
      code: "unavailable",
    };
  } catch {
    return {
      ok: false,
      code: "unavailable",
    };
  }
}
