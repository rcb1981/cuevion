export type ReviewWorkspaceTarget =
  | "demo-review"
  | "late-night-review"
  | "contract-review-note"
  | "release-copy-review"
  | "royalty-approval-review";

export type ReviewItemType =
  | "content_shortlist_review"
  | "business_context_review"
  | "approval_review"
  | "blocked_dependency_review";

export type ReviewStatus =
  | "needs_decision"
  | "blocked"
  | "queued"
  | "resolved"
  | "in_progress";

export type ReviewActionKind =
  | "open_full_review"
  | "queue_feedback"
  | "compare_shortlist"
  | "inspect_contract_context";

export type ReviewDecisionType =
  | "approve"
  | "reject"
  | "hold"
  | "resolve"
  | "escalate"
  | "request_info"
  | "queue";

export type ReviewRelatedItemKind =
  | "shortlist_note"
  | "contract_thread"
  | "comparable_reference"
  | "previous_review_note"
  | "linked_business_note"
  | "approval_dependency";

export type ReviewRelatedItemActionType =
  | "open_note"
  | "open_thread"
  | "open_reference"
  | "open_dependency";

export type ReviewActionDescriptor = {
  id: string;
  label: string;
  kind: ReviewActionKind;
};

export type ReviewRelatedItem = {
  id: string;
  label: string;
  kind: ReviewRelatedItemKind;
  actionType: ReviewRelatedItemActionType;
  targetId: string;
  previewText?: string;
};

export type ReviewItem = {
  id: string;
  target: ReviewWorkspaceTarget;
  type: ReviewItemType;
  title: string;
  subtitle: string;
  description: string;
  status: ReviewStatus;
  owner: string;
  nextStep: string;
  highlights: string[];
  relatedItems: ReviewRelatedItem[];
  primaryAction: ReviewActionDescriptor;
  secondaryAction?: ReviewActionDescriptor;
  contextAction?: ReviewActionDescriptor;
  sourceType: string;
  sourceId: string;
  linkedEntityIds: string[];
  createdAt: string;
  updatedAt: string;
};

export type ReviewDecisionPayload = {
  reviewId: string;
  decisionType: ReviewDecisionType;
  decisionNote?: string;
  actedBy?: string;
  actedAt: string;
  previousStatus: string;
  newStatus: string;
};

export type ReviewContextEntity = {
  id: string;
  title: string;
  eyebrow: string;
  summary: string;
  metadata: Array<{ label: string; value: string }>;
  sections: Array<{ title: string; body: string[] }>;
};

export type ReviewStore = {
  items: ReviewItem[];
  contexts: Record<string, ReviewContextEntity>;
  decisions: ReviewDecisionPayload[];
};

export type ReviewServiceResult =
  | { ok: true; store: ReviewStore; item: ReviewItem }
  | { ok: false; store: ReviewStore; error: string };
