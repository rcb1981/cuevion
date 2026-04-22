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

export type FetchCollaborationThreadsGetManyRequest = {
  workspaceId: string;
  mailboxId?: string;
  messageIds: string[];
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

export async function fetchCollaborationThreadsGetMany(
  request: FetchCollaborationThreadsGetManyRequest,
): Promise<Record<string, CollaborationThread>> {
  try {
    const response = await fetch("/api/collaboration/threads/get-many", {
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
    const response = await fetch("/api/collaboration/thread/create", {
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
    const response = await fetch("/api/collaboration/thread/action", {
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
