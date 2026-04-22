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
