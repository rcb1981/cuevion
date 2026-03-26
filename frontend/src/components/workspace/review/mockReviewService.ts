import type {
  ReviewActionDescriptor,
  ReviewContextEntity,
  ReviewDecisionPayload,
  ReviewDecisionType,
  ReviewItem,
  ReviewRelatedItem,
  ReviewServiceResult,
  ReviewStatus,
  ReviewStore,
  ReviewWorkspaceTarget,
} from "./types";

const reviewAction = (
  id: string,
  label: string,
  kind: ReviewActionDescriptor["kind"],
): ReviewActionDescriptor => ({
  id,
  label,
  kind,
});

const reviewContexts: Record<string, ReviewContextEntity> = {
  "ctx-mavez-shortlist-note": {
    id: "ctx-mavez-shortlist-note",
    eyebrow: "Shortlist note",
    title: "MAVEZ shortlist notes",
    summary: "First-pass screening notes from the demo inbox with arrangement and fit observations.",
    metadata: [
      { label: "Author", value: "Emma Stone" },
      { label: "Updated", value: "March 23, 2026 at 09:10" },
    ],
    sections: [
      {
        title: "Fit",
        body: [
          "Strong melodic tension for late-night sets with a clean topline entry after the drop.",
          "Arrangement feels release-ready apart from the current break length.",
        ],
      },
      {
        title: "Decision signal",
        body: ["Worth a second-pass decision if the shortlist still needs one crossover-leaning record."],
      },
    ],
  },
  "ctx-mavez-reference": {
    id: "ctx-mavez-reference",
    eyebrow: "Comparable reference",
    title: "Comparable club reference",
    summary: "Reference packet linked by A&R to compare energy curve and mix density.",
    metadata: [
      { label: "Reference", value: "After-hours / melodic club" },
      { label: "Used for", value: "Shortlist comparison" },
    ],
    sections: [
      {
        title: "Why it matters",
        body: [
          "The reference shows a tighter low-end pocket and a shorter second break.",
          "MAVEZ has a stronger topline hook but a less decisive final section.",
        ],
      },
    ],
  },
  "ctx-contract-thread": {
    id: "ctx-contract-thread",
    eyebrow: "Contract thread",
    title: "Universal contract thread",
    summary: "Active contract conversation with unresolved approval conditions blocking the item.",
    metadata: [
      { label: "Counterparty", value: "Universal Music" },
      { label: "Last reply", value: "March 24, 2026 at 08:44" },
    ],
    sections: [
      {
        title: "Open points",
        body: [
          "Approval language still leaves royalty escalation timing ambiguous.",
          "Legal asked whether the linked note is sufficient for the final sign-off.",
        ],
      },
      {
        title: "Current dependency",
        body: ["The business item cannot move forward until the approval owner confirms the fallback wording."],
      },
    ],
  },
  "ctx-business-note": {
    id: "ctx-business-note",
    eyebrow: "Business note",
    title: "Linked business note",
    summary: "Internal note attached to the item record to explain the contract-side dependency.",
    metadata: [
      { label: "Owner", value: "Business team" },
      { label: "Priority", value: "Today" },
    ],
    sections: [
      {
        title: "Summary",
        body: [
          "If approval timing slips beyond today, the current release communication needs to be held.",
          "Escalation is only necessary if legal cannot confirm the revised clause by the afternoon checkpoint.",
        ],
      },
    ],
  },
  "ctx-release-feedback": {
    id: "ctx-release-feedback",
    eyebrow: "Previous note",
    title: "Queued release feedback",
    summary: "Feedback was queued while release copy awaits a final editorial pass.",
    metadata: [
      { label: "Owner", value: "Promo team" },
      { label: "Queued", value: "March 23, 2026 at 17:20" },
    ],
    sections: [
      {
        title: "Why queued",
        body: [
          "The release copy is usable, but the title treatment still needs a coordinated check against the campaign calendar.",
          "Queueing keeps the item visible without forcing a premature approval.",
        ],
      },
    ],
  },
  "ctx-royalty-dependency": {
    id: "ctx-royalty-dependency",
    eyebrow: "Approval dependency",
    title: "Royalty approval dependency",
    summary: "Dependency record that was resolved after finance confirmed the split schedule.",
    metadata: [
      { label: "Resolved by", value: "Finance ops" },
      { label: "Resolved", value: "March 22, 2026 at 11:05" },
    ],
    sections: [
      {
        title: "Outcome",
        body: [
          "Approval dependency is closed and the item remains as a read-only audit trail.",
          "No further action is required unless the agreement changes again.",
        ],
      },
    ],
  },
};

const createRelatedItem = (
  id: string,
  label: ReviewRelatedItem["label"],
  kind: ReviewRelatedItem["kind"],
  actionType: ReviewRelatedItem["actionType"],
  targetId: string,
  previewText?: string,
): ReviewRelatedItem => ({
  id,
  label,
  kind,
  actionType,
  targetId,
  previewText,
});

const initialItems: ReviewItem[] = [
  {
    id: "review-mavez-demo",
    target: "demo-review",
    type: "content_shortlist_review",
    title: "MAVEZ demo submission",
    subtitle: "Private SoundCloud demo from the submissions inbox",
    description:
      "A second-pass content item is ready for a human decision before the shortlist can move forward.",
    status: "needs_decision",
    owner: "Demo inbox",
    nextStep: "Listen against the shortlist notes and decide whether this moves forward or should be declined.",
    highlights: [
      "Private link verified and playable",
      "Shortlist notes already capture the first-pass fit",
      "Comparable club references are linked for a faster second decision",
    ],
    relatedItems: [
      createRelatedItem(
        "rel-mavez-shortlist-note",
        "Shortlist notes from first pass",
        "shortlist_note",
        "open_note",
        "ctx-mavez-shortlist-note",
        "Strong melodic tension and release-ready topline.",
      ),
      createRelatedItem(
        "rel-mavez-reference",
        "Comparable club reference",
        "comparable_reference",
        "open_reference",
        "ctx-mavez-reference",
        "Reference packet for energy curve and mix density.",
      ),
    ],
    primaryAction: reviewAction("act-mavez-open", "Open Details", "open_full_review"),
    secondaryAction: reviewAction("act-mavez-queue", "Queue Note", "queue_feedback"),
    contextAction: reviewAction("act-mavez-compare", "Compare Shortlist", "compare_shortlist"),
    sourceType: "demo_inbox_message",
    sourceId: "demo-1",
    linkedEntityIds: ["artist-mavez", "shortlist-late-night-march"],
    createdAt: "2026-03-22T08:15:00.000Z",
    updatedAt: "2026-03-24T08:50:00.000Z",
  },
  {
    id: "review-contract-context",
    target: "contract-review-note",
    type: "business_context_review",
    title: "Contract-linked note",
    subtitle: "Business follow-up attached to the current approval flow",
    description:
      "The item cannot move forward until linked contract context is inspected and the dependency is addressed.",
    status: "blocked",
    owner: "Business team",
    nextStep: "Inspect the contract thread and clear the dependency before deciding whether this can be resolved or escalated.",
    highlights: [
      "Approval timing depends on linked contract questions",
      "Business note is already attached to the item record",
      "The blocking dependency is visible and actionable in this workspace",
    ],
    relatedItems: [
      createRelatedItem(
        "rel-contract-thread",
        "Universal contract thread",
        "contract_thread",
        "open_thread",
        "ctx-contract-thread",
        "Royalty escalation timing is still ambiguous.",
      ),
      createRelatedItem(
        "rel-business-note",
        "Linked business note",
        "linked_business_note",
        "open_note",
        "ctx-business-note",
        "Escalate only if legal cannot confirm the fallback clause.",
      ),
    ],
    primaryAction: reviewAction("act-contract-open", "Open Details", "open_full_review"),
    secondaryAction: reviewAction("act-contract-queue", "Queue Note", "queue_feedback"),
    contextAction: reviewAction(
      "act-contract-context",
      "Inspect Contract Context",
      "inspect_contract_context",
    ),
    sourceType: "contract_thread",
    sourceId: "business-1",
    linkedEntityIds: ["contract-umg-v2", "business-note-approval-block"],
    createdAt: "2026-03-21T14:30:00.000Z",
    updatedAt: "2026-03-24T07:44:00.000Z",
  },
  {
    id: "review-late-night-shortlist",
    target: "late-night-review",
    type: "content_shortlist_review",
    title: "Late-night club cut shortlist",
    subtitle: "Second-pass shortlist decision requested by A&R",
    description:
      "The shortlist is ready for a clear human call before it can advance beyond second pass.",
    status: "needs_decision",
    owner: "A&R shortlist",
    nextStep: "Compare the shortlist references and decide whether this cut advances, holds, or drops out.",
    highlights: [
      "Shortlist status was applied after first-pass screening",
      "Energy profile fits late-night club programming",
      "A&R requested a decision in the current cycle",
    ],
    relatedItems: [
      createRelatedItem(
        "rel-late-night-note",
        "Shortlist notes from first pass",
        "shortlist_note",
        "open_note",
        "ctx-mavez-shortlist-note",
        "Strong topline, but the second break still needs a tighter call.",
      ),
      createRelatedItem(
        "rel-late-night-reference",
        "Comparable club references",
        "comparable_reference",
        "open_reference",
        "ctx-mavez-reference",
        "Compare the energy profile against the current shortlist.",
      ),
    ],
    primaryAction: reviewAction("act-late-night-open", "Open Details", "open_full_review"),
    secondaryAction: reviewAction("act-late-night-queue", "Queue Note", "queue_feedback"),
    contextAction: reviewAction(
      "act-late-night-compare",
      "Compare Shortlist",
      "compare_shortlist",
    ),
    sourceType: "shortlist_record",
    sourceId: "demo-2",
    linkedEntityIds: ["shortlist-late-night-march", "reference-pack-club-cuts"],
    createdAt: "2026-03-23T12:05:00.000Z",
    updatedAt: "2026-03-24T08:12:00.000Z",
  },
  {
    id: "review-release-copy",
    target: "release-copy-review",
    type: "approval_review",
    title: "Release copy timing",
    subtitle: "Queued feedback for release copy before final promo send",
    description:
      "Feedback has already been queued, so the item stays visible until someone reopens or resolves it.",
    status: "queued",
    owner: "Promo team",
    nextStep: "Reopen when the campaign title treatment is ready, or resolve once the queued feedback has been applied.",
    highlights: [
      "Feedback was intentionally queued instead of forcing a decision",
      "Promo timing remains linked to the release calendar",
      "The item stays visible until a human reopens or resolves it",
    ],
    relatedItems: [
      createRelatedItem(
        "rel-release-feedback",
        "Queued release feedback note",
        "previous_review_note",
        "open_note",
        "ctx-release-feedback",
        "Queued while title treatment waits for final editorial pass.",
      ),
    ],
    primaryAction: reviewAction("act-release-open", "Open Details", "open_full_review"),
    secondaryAction: reviewAction("act-release-queue", "Queue Note", "queue_feedback"),
    contextAction: reviewAction("act-release-compare", "Compare Shortlist", "compare_shortlist"),
    sourceType: "promo_review_note",
    sourceId: "promo-1",
    linkedEntityIds: ["release-friday-campaign"],
    createdAt: "2026-03-20T16:25:00.000Z",
    updatedAt: "2026-03-23T17:20:00.000Z",
  },
  {
    id: "review-royalty-approval",
    target: "royalty-approval-review",
    type: "blocked_dependency_review",
    title: "Royalty approval dependency",
    subtitle: "Resolved approval chain for the current release agreement",
    description:
      "This item is complete and remains available as a stable read-only record of the decision trail.",
    status: "resolved",
    owner: "Finance team",
    nextStep: "No further action is required unless the agreement changes and the item needs to be reopened.",
    highlights: [
      "Finance confirmed the split schedule",
      "The approval dependency was cleared without escalation",
      "The item now serves as a read-only decision record",
    ],
    relatedItems: [
      createRelatedItem(
        "rel-royalty-dependency",
        "Resolved approval dependency",
        "approval_dependency",
        "open_dependency",
        "ctx-royalty-dependency",
        "Approval dependency closed after finance confirmation.",
      ),
    ],
    primaryAction: reviewAction("act-royalty-open", "Open Details", "open_full_review"),
    sourceType: "finance_approval",
    sourceId: "main-11",
    linkedEntityIds: ["royalty-split-spring-2026"],
    createdAt: "2026-03-18T10:05:00.000Z",
    updatedAt: "2026-03-22T11:05:00.000Z",
  },
];

const statusLabels: Record<ReviewStatus, string> = {
  needs_decision: "Needs decision",
  blocked: "Blocked",
  queued: "Queued",
  resolved: "Resolved",
  in_progress: "In progress",
};

const transitionStatus = (
  currentStatus: ReviewStatus,
  decisionType: ReviewDecisionType,
): ReviewStatus | null => {
  switch (currentStatus) {
    case "needs_decision":
      if (decisionType === "approve" || decisionType === "reject") {
        return "resolved";
      }
      if (decisionType === "hold" || decisionType === "queue") {
        return "queued";
      }
      if (decisionType === "escalate") {
        return "blocked";
      }
      return null;
    case "blocked":
      if (decisionType === "resolve") {
        return "resolved";
      }
      if (decisionType === "request_info") {
        return "blocked";
      }
      if (decisionType === "escalate") {
        return "blocked";
      }
      if (decisionType === "queue") {
        return "queued";
      }
      return null;
    case "queued":
      if (decisionType === "resolve") {
        return "resolved";
      }
      return null;
    case "in_progress":
      if (decisionType === "resolve") {
        return "resolved";
      }
      if (decisionType === "escalate") {
        return "blocked";
      }
      if (decisionType === "queue") {
        return "queued";
      }
      return null;
    case "resolved":
      return null;
    default:
      return null;
  }
};

const withUpdatedItem = (
  store: ReviewStore,
  reviewId: string,
  updater: (item: ReviewItem) => ReviewItem,
): ReviewServiceResult => {
  const existingItem = store.items.find((item) => item.id === reviewId);

  if (!existingItem) {
    return { ok: false, store, error: "Item was not found." };
  }

  const nextItem = updater(existingItem);
  return {
    ok: true,
    item: nextItem,
    store: {
      ...store,
      items: store.items.map((item) => (item.id === reviewId ? nextItem : item)),
    },
  };
};

const nextStepByStatus = (status: ReviewStatus, item: ReviewItem) => {
  switch (status) {
    case "needs_decision":
      return item.type === "content_shortlist_review"
        ? "Inspect the shortlist context and make a clear human decision so the item can move forward."
        : "Open the workspace and make the next decision with the linked context in view.";
    case "blocked":
      return "Inspect the blocking dependency and clear it before the item can move forward.";
    case "queued":
      return "Keep the item on hold until someone reopens it or resolves it from the queued state.";
    case "in_progress":
      return "Continue work on the item and either resolve it, re-queue it, or block it with a clear reason.";
    case "resolved":
      return "No further action is required unless the item needs to be reopened.";
    default:
      return item.nextStep;
  }
};

export const reviewMockService = {
  createInitialReviewStore(): ReviewStore {
    return {
      items: initialItems.map((item) => ({
        ...item,
        relatedItems: item.relatedItems.map((relatedItem) => ({ ...relatedItem })),
      })),
      contexts: { ...reviewContexts },
      decisions: [],
    };
  },

  getReviewItems(store: ReviewStore) {
    return [...store.items].sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
  },

  getReviewItemById(store: ReviewStore, id: string) {
    return store.items.find((item) => item.id === id) ?? null;
  },

  getReviewItemByTarget(store: ReviewStore, target: ReviewWorkspaceTarget) {
    return store.items.find((item) => item.target === target) ?? null;
  },

  getRelatedItems(store: ReviewStore, reviewId: string): ReviewRelatedItem[] {
    return this.getReviewItemById(store, reviewId)?.relatedItems ?? [];
  },

  getContextEntity(store: ReviewStore, contextId: string) {
    return store.contexts[contextId] ?? null;
  },

  openReview(store: ReviewStore, id: string) {
    return this.getReviewItemById(store, id);
  },

  getStatusLabel(status: ReviewStatus) {
    return statusLabels[status];
  },

  getPrimaryDecisionOptions(item: ReviewItem): ReviewDecisionType[] {
    if (item.status === "resolved") {
      return [];
    }

    switch (item.type) {
      case "content_shortlist_review":
        return item.status === "needs_decision" ? ["approve", "reject", "hold"] : ["resolve", "queue"];
      case "business_context_review":
      case "blocked_dependency_review":
        return ["resolve", "escalate", "request_info", "queue"];
      case "approval_review":
        return item.status === "needs_decision"
          ? ["approve", "reject", "hold"]
          : ["resolve", "queue"];
      default:
        return [];
    }
  },

  submitReviewDecision(store: ReviewStore, payload: ReviewDecisionPayload): ReviewServiceResult {
    const item = this.getReviewItemById(store, payload.reviewId);

    if (!item) {
      return { ok: false, store, error: "Item was not found." };
    }

    const allowedStatus = transitionStatus(item.status, payload.decisionType);

    if (!allowedStatus || allowedStatus !== payload.newStatus) {
      return { ok: false, store, error: "That decision is not valid for the current item status." };
    }

    const note = payload.decisionNote?.trim();
    const result = withUpdatedItem(store, payload.reviewId, (currentItem) => ({
      ...currentItem,
      status: allowedStatus,
      updatedAt: payload.actedAt,
      nextStep: nextStepByStatus(allowedStatus, currentItem),
      description:
        note && note.length > 0
          ? `${currentItem.description} Latest note: ${note}`
          : currentItem.description,
    }));

    if (!result.ok) {
      return result;
    }

    return {
      ok: true,
      item: result.item,
      store: {
        ...result.store,
        decisions: [...result.store.decisions, payload],
      },
    };
  },

  updateReviewStatus(
    store: ReviewStore,
    reviewId: string,
    status: ReviewStatus,
    actedAt: string,
  ): ReviewServiceResult {
    const item = this.getReviewItemById(store, reviewId);

    if (!item) {
      return { ok: false, store, error: "Item was not found." };
    }

    const isValidTransition =
      (item.status === "needs_decision" && (status === "queued" || status === "in_progress")) ||
      (item.status === "blocked" && (status === "queued" || status === "in_progress")) ||
      (item.status === "queued" && (status === "in_progress" || status === "resolved")) ||
      (item.status === "in_progress" && (status === "queued" || status === "blocked")) ||
      status === item.status;

    if (!isValidTransition) {
      return { ok: false, store, error: "That status change is not valid for the current item." };
    }

    return withUpdatedItem(store, reviewId, (currentItem) => ({
      ...currentItem,
      status,
      updatedAt: actedAt,
      nextStep: nextStepByStatus(status, currentItem),
    }));
  },

  reopenReview(store: ReviewStore, reviewId: string, actedAt: string): ReviewServiceResult {
    const item = this.getReviewItemById(store, reviewId);

    if (!item) {
      return { ok: false, store, error: "Item was not found." };
    }

    if (item.status !== "queued") {
      return { ok: false, store, error: "Only queued items can be reopened." };
    }

    return withUpdatedItem(store, reviewId, (currentItem) => ({
      ...currentItem,
      status: "needs_decision",
      updatedAt: actedAt,
      nextStep: nextStepByStatus("needs_decision", currentItem),
    }));
  },

  closeReviewFromInboxAction(
    store: ReviewStore,
    reviewId: string,
    actedAt: string,
  ): ReviewServiceResult {
    const item = this.getReviewItemById(store, reviewId);

    if (!item) {
      return { ok: false, store, error: "Item was not found." };
    }

    if (item.status === "resolved") {
      return { ok: true, store, item };
    }

    return withUpdatedItem(store, reviewId, (currentItem) => ({
      ...currentItem,
      status: "resolved",
      updatedAt: actedAt,
      nextStep: nextStepByStatus("resolved", currentItem),
    }));
  },
};
