import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { reviewMockService } from "./mockReviewService";
import type {
  ReviewContextEntity,
  ReviewDecisionPayload,
  ReviewDecisionType,
  ReviewItem,
  ReviewRelatedItem,
  ReviewStatus,
  ReviewStore,
  ReviewWorkspaceTarget,
} from "./types";

const primaryActionClass =
  "inline-flex h-10 items-center justify-center rounded-full bg-pine px-5 text-[0.72rem] font-medium uppercase tracking-[0.16em] text-white transition-[background-color,transform] duration-150 hover:bg-moss active:scale-[0.99] focus-visible:outline-none";
const secondaryActionClass =
  "inline-flex h-10 items-center justify-center rounded-full border border-[var(--workspace-border)] bg-[var(--workspace-card)] px-5 text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-soft)] transition-[background-color,border-color,color,transform] duration-150 hover:border-[var(--workspace-border-hover)] hover:bg-[var(--workspace-hover-surface-strong)] active:scale-[0.99] focus-visible:outline-none";
const tertiaryActionClass =
  "inline-flex h-10 items-center justify-center rounded-full border border-[var(--workspace-border-soft)] bg-transparent px-5 text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-soft)] transition-[background-color,border-color,color,transform] duration-150 hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-card-subtle)] hover:text-[var(--workspace-text)] active:scale-[0.99] focus-visible:outline-none";
const closeActionClass =
  "inline-flex h-10 items-center justify-center rounded-full border border-[var(--workspace-close-button-border)] bg-[var(--workspace-close-button-bg)] px-5 text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-close-button-text)] transition-[background-color,border-color,transform] duration-150 hover:border-[var(--workspace-close-button-border)] hover:bg-[var(--workspace-close-button-bg-hover)] active:scale-[0.99] focus-visible:outline-none";
const reviewModalShellClass =
  "w-full overflow-hidden rounded-[28px] border border-[var(--workspace-border)] bg-[var(--workspace-modal-bg)] p-6 shadow-[0_24px_70px_rgba(20,18,16,0.28),0_8px_20px_rgba(20,18,16,0.18)]";
const reviewModalPanelClass =
  "rounded-[24px] border border-[var(--workspace-modal-border-strong)] bg-[var(--workspace-modal-subtle)] shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]";
const reviewModalCardClass =
  "rounded-[20px] border border-[var(--workspace-modal-border-strong)] bg-[var(--workspace-modal-inner)] shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]";

type ReviewToast = {
  tone: "success" | "error";
  message: string;
};

export type ReviewModuleController = ReturnType<typeof useReviewModuleState>;

type PriorityDisplayFields = {
  sender: string;
  subject: string;
  context: string;
};

type ReviewListViewProps = {
  filter: "All priority" | "Priority";
  controller: ReviewModuleController;
  onOpenItem: (item: ReviewItem) => void;
  hiddenReviewIds?: string[];
  supplementalItems?: ReviewItem[];
  displayOverrides?: Partial<Record<string, PriorityDisplayFields>>;
};

type ReviewDetailViewProps = {
  target: ReviewWorkspaceTarget;
  controller: ReviewModuleController;
  onBack: () => void;
  onHandleNow: (item: ReviewItem) => void;
};

const activeReviewStatuses: ReviewStatus[] = ["needs_decision", "blocked", "queued", "in_progress"];

function formatTimestamp(date: Date) {
  return date.toISOString();
}

function formatDisplayTimestamp(value: string) {
  return new Date(value).toLocaleString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function getStatusTone(status: ReviewStatus) {
  switch (status) {
    case "needs_decision":
      return "border-[color:rgba(111,148,111,0.22)] bg-[color:rgba(111,148,111,0.12)] text-[color:rgba(78,119,84,0.96)]";
    case "blocked":
      return "border-[color:rgba(184,163,120,0.24)] bg-[color:rgba(184,163,120,0.12)] text-[color:rgba(133,109,71,0.96)]";
    case "queued":
      return "border-[color:rgba(140,129,115,0.2)] bg-[color:rgba(140,129,115,0.12)] text-[color:rgba(108,99,91,0.94)]";
    case "resolved":
      return "border-[color:rgba(121,151,120,0.18)] bg-[color:rgba(121,151,120,0.1)] text-[color:rgba(83,108,84,0.94)]";
    case "in_progress":
      return "border-[color:rgba(106,128,156,0.24)] bg-[color:rgba(106,128,156,0.12)] text-[color:rgba(88,106,131,0.96)]";
    default:
      return "border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] text-[var(--workspace-text-soft)]";
  }
}

function getDecisionButtonLabel(decision: ReviewDecisionType) {
  switch (decision) {
    case "approve":
      return "Approve";
    case "reject":
      return "Reject";
    case "hold":
      return "Hold";
    case "resolve":
      return "Resolve";
    case "escalate":
      return "Escalate";
    case "request_info":
      return "Request Info";
    case "queue":
      return "Queue";
    default:
      return decision;
  }
}

function getPriorityStatusLabel(status: ReviewStatus) {
  switch (status) {
    case "needs_decision":
      return "Ready now";
    case "blocked":
      return "Needs context";
    case "queued":
      return "Scheduled";
    case "resolved":
      return "Handled";
    case "in_progress":
      return "In progress";
    default:
      return status;
  }
}

function getPrioritySenderLine(
  item: ReviewItem,
  displayOverride?: PriorityDisplayFields,
) {
  return displayOverride?.sender ?? (item.sourceType === "mail_message" ? item.subtitle : item.owner);
}

function getPrioritySubjectLine(
  item: ReviewItem,
  displayOverride?: PriorityDisplayFields,
) {
  return displayOverride?.subject ?? item.title;
}

function getPriorityContextLine(
  item: ReviewItem,
  displayOverride?: PriorityDisplayFields,
) {
  return displayOverride?.context ?? (item.sourceType === "mail_message" ? item.nextStep : item.subtitle);
}

function getDecisionTargetCopy(item: ReviewItem) {
  switch (item.type) {
    case "content_shortlist_review":
      return {
        title: "Decision target",
        body: "Decide if this demo should be added to the shortlist or rejected.",
      };
    case "business_context_review":
      return {
        title: "Decision target",
        body: "Decide if this blocked item can be resolved now or still needs more information.",
      };
    case "blocked_dependency_review":
      return {
        title: "Decision target",
        body: "Decide if this dependency is cleared, still blocked, or should be queued for later.",
      };
    case "approval_review":
      return {
        title: "Decision target",
        body: "Decide if this approval item is ready to move forward or should stay on hold.",
      };
    default:
      return {
        title: "Decision target",
        body: "Decide what should happen with this item.",
      };
  }
}

function getDecisionActionCopy(item: ReviewItem, decision: ReviewDecisionType) {
  if (item.type === "content_shortlist_review") {
    switch (decision) {
      case "approve":
        return {
          label: "Approve for shortlist",
          hint: "Moves this item forward into the next shortlist stage.",
        };
      case "reject":
        return {
          label: "Reject submission",
          hint: "Closes this item and marks the submission as not advancing.",
        };
      case "hold":
        return {
          label: "Hold item",
          hint: "Keeps this item in the current cycle without advancing it.",
        };
      case "resolve":
        return {
          label: "Resolve item",
          hint: "Closes this item from the current working state.",
        };
      case "queue":
        return {
          label: "Queue for later",
          hint: "Keeps the item visible but removes it from the active decision queue.",
        };
    }
  }

  switch (decision) {
    case "resolve":
      return {
        label: "Resolve item",
        hint: "Closes this item and marks the current decision path as complete.",
      };
    case "request_info":
      return {
        label: "Request more info",
        hint: "Keeps the item blocked while more context is gathered.",
      };
    case "queue":
      return {
        label: "Queue for later",
        hint: "Keeps the item on hold but moves it out of the active decision flow.",
      };
    case "escalate":
      return {
        label: "Escalate dependency",
        hint: "Keeps this item blocked and signals that a higher-level follow-up is needed.",
      };
    case "approve":
      return {
        label: "Approve item",
        hint: "Moves this item forward and closes the current decision.",
      };
    case "reject":
      return {
        label: "Reject item",
        hint: "Closes this item without moving it forward.",
      };
    case "hold":
      return {
        label: "Hold item",
        hint: "Keeps this item active without closing it yet.",
      };
    default:
      return {
        label: getDecisionButtonLabel(decision),
        hint: "",
      };
  }
}

function ReviewModalLayer({ children }: { children: ReactNode }) {
  const themeMode =
    typeof document !== "undefined" &&
    (document.body.style.colorScheme === "dark" ||
      document.documentElement.style.colorScheme === "dark")
      ? "dark"
      : "light";

  return (
    <div
      data-theme={themeMode}
      className="pointer-events-auto fixed inset-0 z-[321] bg-[var(--workspace-modal-scrim)] backdrop-blur-[2px]"
    >
      <div className="flex min-h-dvh w-full items-center justify-center overflow-y-auto p-6">
        {children}
      </div>
    </div>
  );
}

function ReviewToastView({ toast }: { toast: ReviewToast | null }) {
  if (!toast) {
    return null;
  }

  return (
    <div className="pointer-events-none fixed bottom-6 right-6 z-[340]">
      <div
        className={`rounded-[18px] border px-4 py-3 text-[0.84rem] leading-6 shadow-panel ${
          toast.tone === "success"
            ? "border-[color:rgba(111,148,111,0.26)] bg-[color:rgba(34,32,28,0.94)] text-[color:rgba(214,232,218,0.96)]"
            : "border-[color:rgba(184,163,120,0.26)] bg-[color:rgba(34,32,28,0.94)] text-[color:rgba(236,226,207,0.96)]"
        }`}
      >
        {toast.message}
      </div>
    </div>
  );
}

function ReviewContextModal({
  entity,
  onClose,
}: {
  entity: ReviewContextEntity;
  onClose: () => void;
}) {
  return createPortal(
    <ReviewModalLayer>
      <div
        className={`${reviewModalShellClass} max-w-[760px]`}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-2">
            <div className="text-[0.72rem] font-medium uppercase tracking-[0.22em] text-[var(--workspace-text-faint)]">
              {entity.eyebrow}
            </div>
            <h2 className="text-[1.45rem] font-medium tracking-tight text-[var(--workspace-text)]">
              {entity.title}
            </h2>
            <p className="max-w-2xl text-[0.92rem] leading-7 text-[var(--workspace-text-soft)]">
              {entity.summary}
            </p>
          </div>
          <button type="button" onClick={onClose} className={closeActionClass}>
            CLOSE
          </button>
        </div>
        <div className="mt-6 grid gap-4 md:grid-cols-2">
          {entity.metadata.map((entry) => (
            <div
              key={entry.label}
              className={`${reviewModalCardClass} px-4 py-3.5`}
            >
              <div className="text-[0.68rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                {entry.label}
              </div>
              <div className="mt-1.5 text-[0.92rem] leading-6 text-[var(--workspace-text)]">
                {entry.value}
              </div>
            </div>
          ))}
        </div>
        <div className="mt-6 space-y-4">
          {entity.sections.map((section) => (
            <div
              key={section.title}
              className={`${reviewModalPanelClass} px-5 py-4`}
            >
              <div className="text-[0.76rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                {section.title}
              </div>
              <div className="mt-2.5 space-y-2 text-[0.92rem] leading-7 text-[var(--workspace-text-soft)]">
                {section.body.map((paragraph) => (
                  <p key={paragraph}>{paragraph}</p>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </ReviewModalLayer>,
    document.body,
  );
}

function ReviewDecisionModal({
  item,
  controller,
  onClose,
  onHandleNow,
}: {
  item: ReviewItem;
  controller: ReviewModuleController;
  onClose: () => void;
  onHandleNow: (item: ReviewItem) => void;
}) {
  const [decisionNote, setDecisionNote] = useState("");
  const decisionOptions = controller.getDecisionOptions(item);
  const decisionTarget = getDecisionTargetCopy(item);

  return createPortal(
    <ReviewModalLayer>
      <div
        className={`${reviewModalShellClass} max-w-[820px]`}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-2">
            <div className="text-[0.72rem] font-medium uppercase tracking-[0.22em] text-[var(--workspace-text-faint)]">
              Full details
            </div>
            <h2 className="text-[1.45rem] font-medium tracking-tight text-[var(--workspace-text)]">
              {item.title}
            </h2>
            <p className="max-w-3xl text-[0.92rem] leading-7 text-[var(--workspace-text-soft)]">
              {item.description}
            </p>
          </div>
          <button type="button" onClick={onClose} className={closeActionClass}>
            CLOSE
          </button>
        </div>
        <div className="mt-6 grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
          <div className="space-y-4">
            <div className={`${reviewModalPanelClass} p-5`}>
              <div className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                Current state
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <span
                  className={`inline-flex rounded-full border px-3 py-1 text-[0.68rem] font-medium uppercase tracking-[0.14em] ${getStatusTone(
                    item.status,
                  )}`}
                >
                  {controller.getStatusLabel(item.status)}
                </span>
                <span className="text-[0.82rem] leading-6 text-[var(--workspace-text-soft)]">
                  Updated {formatDisplayTimestamp(item.updatedAt)}
                </span>
              </div>
              <div className="mt-4 text-[0.88rem] leading-7 text-[var(--workspace-text-soft)]">
                {item.nextStep}
              </div>
            </div>
            <div className={`${reviewModalPanelClass} p-5`}>
              <div className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                Highlights
              </div>
              <div className="mt-3 space-y-2">
                {item.highlights.map((highlight) => (
                  <div key={highlight} className="text-[0.88rem] leading-7 text-[var(--workspace-text-soft)]">
                    {highlight}
                  </div>
                ))}
              </div>
            </div>
          </div>
          <div className={`${reviewModalPanelClass} p-5`}>
            <div className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
              Decision workspace
            </div>
            <div className="mt-3 text-[0.92rem] leading-7 text-[var(--workspace-text-soft)]">
              Choose the next outcome for this item.
            </div>
            <div className={`mt-5 ${reviewModalCardClass} px-4 py-4`}>
              <div className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                {decisionTarget.title}
              </div>
              <div className="mt-2 text-[0.9rem] leading-7 text-[var(--workspace-text-soft)]">
                {decisionTarget.body}
              </div>
            </div>
            <label className="mt-5 block">
              <span className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">
                Decision note
              </span>
              <textarea
                value={decisionNote}
                onChange={(event) => setDecisionNote(event.target.value)}
                placeholder="Add the reasoning behind this decision"
                className={`mt-3 min-h-[160px] w-full resize-none ${reviewModalCardClass} px-4 py-3 text-[0.92rem] leading-7 text-[var(--workspace-text)] outline-none placeholder:text-[var(--workspace-text-faint)]`}
              />
            </label>
            <div className="mt-4 text-[0.8rem] leading-6 text-[var(--workspace-text-faint)]">
              This decision does not notify the sender. You can respond separately from the inbox.
            </div>
            <div className="mt-5 flex flex-wrap gap-3">
              {decisionOptions.map((decision) => (
                <div key={decision} className="min-w-[220px] flex-1">
                  <button
                    type="button"
                    onClick={() => {
                      controller.submitDecision(item.id, decision, decisionNote);
                      onClose();
                    }}
                    className={`${decision === decisionOptions[0] ? primaryActionClass : secondaryActionClass} w-full justify-center`}
                  >
                    {getDecisionActionCopy(item, decision).label}
                  </button>
                  {getDecisionActionCopy(item, decision).hint ? (
                    <div className="mt-2 px-1 text-[0.78rem] leading-6 text-[var(--workspace-text-faint)]">
                      {getDecisionActionCopy(item, decision).hint}
                    </div>
                  ) : null}
                </div>
              ))}
              {decisionOptions.length === 0 ? (
                <div className="text-[0.88rem] leading-7 text-[var(--workspace-text-soft)]">
                  This item is resolved and remains available as a read-only record.
                </div>
              ) : null}
            </div>
            <div className="mt-6 flex flex-wrap gap-3">
              {controller.canStartWork(item) ? (
                <div>
                  <button
                    type="button"
                    onClick={() => {
                      onHandleNow(item);
                      onClose();
                    }}
                    className={secondaryActionClass}
                  >
                    Handle Now
                  </button>
                  <div className="mt-2 px-1 text-[0.78rem] leading-6 text-[var(--workspace-text-faint)]">
                    Open this item in your inbox to take action.
                  </div>
                </div>
              ) : null}
              {controller.canReopen(item) ? (
                <button
                  type="button"
                  onClick={() => {
                    controller.reopen(item.id);
                    onClose();
                  }}
                  className={tertiaryActionClass}
                >
                  Reopen
                </button>
              ) : null}
              {controller.canBlock(item) ? (
                <button
                  type="button"
                  onClick={() => {
                    controller.block(item.id);
                    onClose();
                  }}
                  className={tertiaryActionClass}
                >
                  Block
                </button>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </ReviewModalLayer>,
    document.body,
  );
}

function ReviewHeaderCard({
  item,
  controller,
  onOpenDecision,
  onOpenContext,
}: {
  item: ReviewItem;
  controller: ReviewModuleController;
  onOpenDecision: () => void;
  onOpenContext: (relatedItem: ReviewRelatedItem | null) => void;
}) {
  return (
    <section className="rounded-[30px] border border-[var(--workspace-border)] bg-[linear-gradient(180deg,var(--workspace-card-featured-start),var(--workspace-card-featured-end))] p-6 shadow-panel md:p-7">
      <div className="space-y-4">
        <div className="text-[0.72rem] font-medium uppercase tracking-[0.24em] text-[var(--workspace-text-faint)]">
          Priority detail
        </div>
        <div className="space-y-3">
          <h1 className="text-[1.8rem] font-medium tracking-tight text-[var(--workspace-text)] md:text-[2.15rem]">
            {item.title}
          </h1>
          <p className="max-w-3xl text-[1rem] leading-7 text-[var(--workspace-text-muted)]">
            {item.description}
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          {item.status !== "resolved" ? (
            <button type="button" onClick={onOpenDecision} className={primaryActionClass}>
              {item.primaryAction.label}
            </button>
          ) : null}
          {item.status !== "resolved" && item.secondaryAction ? (
            <button
              type="button"
              onClick={() => controller.queueFeedback(item.id)}
              className={secondaryActionClass}
            >
              {item.secondaryAction.label}
            </button>
          ) : null}
          {item.contextAction ? (
            <button
              type="button"
              onClick={() => onOpenContext(item.relatedItems[0] ?? null)}
              className={tertiaryActionClass}
            >
              {item.contextAction.label}
            </button>
          ) : null}
        </div>
      </div>
    </section>
  );
}

export function useReviewModuleState(workspaceDataMode: "demo" | "live" = "demo") {
  const [store, setStore] = useState<ReviewStore>(() =>
    workspaceDataMode === "demo"
      ? reviewMockService.createInitialReviewStore()
      : {
          items: [],
          contexts: {},
          decisions: [],
        },
  );
  const [toast, setToast] = useState<ReviewToast | null>(null);

  useEffect(() => {
    if (!toast) {
      return;
    }

    const timeoutId = window.setTimeout(() => setToast(null), 2800);
    return () => window.clearTimeout(timeoutId);
  }, [toast]);

  const commitResult = (result: ReturnType<typeof reviewMockService.updateReviewStatus>) => {
    if (!result.ok) {
      setToast({ tone: "error", message: result.error });
      return false;
    }

    setStore(result.store);
    return result.item;
  };

  const submitDecision = (reviewId: string, decisionType: ReviewDecisionType, decisionNote?: string) => {
    const item = reviewMockService.getReviewItemById(store, reviewId);

    if (!item) {
      setToast({ tone: "error", message: "Item was not found." });
      return;
    }

    const nextStatus =
      decisionType === "approve" || decisionType === "reject"
        ? "resolved"
        : decisionType === "hold" || decisionType === "queue"
          ? "queued"
          : decisionType === "resolve"
            ? "resolved"
            : decisionType === "escalate" || decisionType === "request_info"
              ? "blocked"
            : item.status;
    const payload: ReviewDecisionPayload = {
      reviewId,
      decisionType,
      decisionNote,
      actedBy: "Cuevion reviewer",
      actedAt: formatTimestamp(new Date()),
      previousStatus: item.status,
      newStatus: nextStatus,
    };
    const result = reviewMockService.submitReviewDecision(store, payload);

    if (!result.ok) {
      setToast({ tone: "error", message: result.error });
      return;
    }

    setStore(result.store);
    setToast({
      tone: "success",
      message: `${result.item.title} updated to ${reviewMockService.getStatusLabel(result.item.status)}.`,
    });
  };

  return {
    store,
    toast,
    getItems(filter: "All priority" | "Priority") {
      const items = reviewMockService.getReviewItems(store);
      return filter === "Priority"
        ? items.filter((item) => activeReviewStatuses.includes(item.status))
        : items;
    },
    getItemByTarget(target: ReviewWorkspaceTarget) {
      return reviewMockService.getReviewItemByTarget(store, target);
    },
    getReviewBySourceId(sourceId: string) {
      return store.items.find((item) => item.sourceId === sourceId) ?? null;
    },
    getStatusLabel(status: ReviewStatus) {
      return reviewMockService.getStatusLabel(status);
    },
    getInboxStatusLabel(sourceId: string) {
      const item = store.items.find((entry) => entry.sourceId === sourceId);

      if (!item) {
        return null;
      }

      const latestDecision = [...store.decisions]
        .reverse()
        .find((decision) => decision.reviewId === item.id);

      if (item.status === "resolved" && latestDecision?.decisionType === "approve") {
        return "Approved for shortlist";
      }

      if (item.status === "resolved" && latestDecision?.decisionType === "reject") {
        return "Rejected";
      }

      if (item.status === "queued" && latestDecision?.decisionType === "hold") {
        return "On hold";
      }

      return "Needs input";
    },
    getDecisionOptions(item: ReviewItem) {
      return reviewMockService.getPrimaryDecisionOptions(item);
    },
    getContext(contextId: string) {
      return reviewMockService.getContextEntity(store, contextId);
    },
    queueFeedback(reviewId: string) {
      const result = reviewMockService.updateReviewStatus(
        store,
        reviewId,
        "queued",
        formatTimestamp(new Date()),
      );
      const item = commitResult(result);

      if (!item) {
        return;
      }

      setToast({ tone: "success", message: `${item.title} queued for follow-up.` });
    },
    block(reviewId: string) {
      const result = reviewMockService.updateReviewStatus(
        store,
        reviewId,
        "blocked",
        formatTimestamp(new Date()),
      );
      const item = commitResult(result);

      if (!item) {
        return;
      }

      setToast({ tone: "success", message: `${item.title} is blocked until dependency context is cleared.` });
    },
    reopen(reviewId: string) {
      const result = reviewMockService.reopenReview(store, reviewId, formatTimestamp(new Date()));

      if (!result.ok) {
        setToast({ tone: "error", message: result.error });
        return;
      }

      setStore(result.store);
      setToast({ tone: "success", message: `${result.item.title} reopened and ready for decision.` });
    },
    closeFromInboxAction(reviewId: string) {
      const result = reviewMockService.closeReviewFromInboxAction(
        store,
        reviewId,
        formatTimestamp(new Date()),
      );

      if (!result.ok) {
        setToast({ tone: "error", message: result.error });
        return false;
      }

      setStore(result.store);
      return true;
    },
    canStartWork(item: ReviewItem) {
      return item.status === "needs_decision" || item.status === "blocked" || item.status === "queued";
    },
    canReopen(item: ReviewItem) {
      return item.status === "queued";
    },
    canBlock(item: ReviewItem) {
      return item.status === "in_progress";
    },
    submitDecision,
  };
}

export function isReviewWorkspaceTarget(target: string): target is ReviewWorkspaceTarget {
  return [
    "demo-review",
    "late-night-review",
    "contract-review-note",
    "release-copy-review",
    "royalty-approval-review",
  ].includes(target);
}

export function getReviewTargetEyebrow() {
  return "Priority detail";
}

export function ReviewListView({
  filter,
  controller,
  onOpenItem,
  hiddenReviewIds = [],
  supplementalItems = [],
  displayOverrides = {},
}: ReviewListViewProps) {
  const hiddenReviewIdSet = new Set(hiddenReviewIds);
  const items = controller
    .getItems(filter)
    .concat(supplementalItems)
    .filter((item) => !hiddenReviewIdSet.has(item.id));

  return (
    <>
      <div className="min-h-full space-y-8">
        <header className="space-y-3">
          <div className="text-[0.72rem] font-medium uppercase tracking-[0.24em] text-[var(--workspace-text-faint)]">
            Priority
          </div>
          <h1 className="text-[1.85rem] font-medium tracking-tight text-[var(--workspace-text)] md:text-[2.25rem]">
            {filter}
          </h1>
          <p className="text-lg leading-8 text-[var(--workspace-text-muted)]">
            Cross-inbox priority items collected in one place so you can open the right thread and act immediately.
          </p>
        </header>

        <section className="rounded-[30px] border border-[var(--workspace-border)] bg-[var(--workspace-card)] p-6 shadow-panel">
          <div className="space-y-3">
            {items.map((item) => {
              const displayOverride = displayOverrides[item.id];

              return (
              <button
                key={item.id}
                type="button"
                onClick={() => onOpenItem(item)}
                className={`w-full cursor-pointer rounded-[20px] border px-4 py-4 text-left transition-[background-color,background-image,border-color,transform] duration-150 focus-visible:outline-none ${
                  item.status === "resolved"
                    ? "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-card-hover)]"
                    : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface)] focus-visible:border-[var(--workspace-border-hover)] focus-visible:bg-[linear-gradient(180deg,var(--workspace-card-featured-start),var(--workspace-card-featured-end))]"
                }`}
              >
                <div className="min-w-0">
                  <div className="truncate text-[0.96rem] font-medium tracking-[-0.014em] text-[var(--workspace-text)]">
                    {getPrioritySenderLine(item, displayOverride)}
                  </div>
                  <div className="mt-0.5 truncate text-[0.84rem] leading-6 text-[var(--workspace-text-soft)]">
                    {getPrioritySubjectLine(item, displayOverride)}
                  </div>
                  <div className="mt-0.5 truncate text-[0.8rem] leading-6 text-[var(--workspace-text-faint)]">
                    {getPriorityContextLine(item, displayOverride)}
                  </div>
                </div>
              </button>
              );
            })}
          </div>
        </section>
      </div>
      <ReviewToastView toast={controller.toast} />
    </>
  );
}

export function ReviewDetailView({ target, controller, onBack, onHandleNow }: ReviewDetailViewProps) {
  const item = controller.getItemByTarget(target);
  const [isDecisionOpen, setIsDecisionOpen] = useState(false);
  const [activeContextId, setActiveContextId] = useState<string | null>(null);

  useEffect(() => {
    setIsDecisionOpen(false);
    setActiveContextId(null);
  }, [target]);

  if (!item) {
    return (
      <>
        <div className="space-y-8">
          <div className="flex items-center gap-4">
            <button type="button" onClick={onBack} className={primaryActionClass}>
              Back
            </button>
          </div>
          <section className="rounded-[30px] border border-[var(--workspace-border)] bg-[var(--workspace-card)] p-6 shadow-panel">
            <h1 className="text-[1.45rem] font-medium tracking-tight text-[var(--workspace-text)]">
              Priority item unavailable
            </h1>
            <p className="mt-3 text-[0.92rem] leading-7 text-[var(--workspace-text-soft)]">
              This priority item could not be loaded. Return to Priority and try another item.
            </p>
          </section>
        </div>
        <ReviewToastView toast={controller.toast} />
      </>
    );
  }

  const activeContext = activeContextId ? controller.getContext(activeContextId) : null;
  const contextActionTarget =
    item.contextAction?.kind === "inspect_contract_context"
      ? item.relatedItems.find((relatedItem) => relatedItem.kind === "contract_thread") ??
        item.relatedItems[0] ??
        null
      : item.relatedItems.find((relatedItem) => relatedItem.kind === "comparable_reference") ??
        item.relatedItems[0] ??
        null;

  return (
    <>
      <div className="space-y-8">
        <div className="flex items-center gap-4">
          <button type="button" onClick={onBack} className={primaryActionClass}>
            Back
          </button>
        </div>

        <ReviewHeaderCard
          item={item}
          controller={controller}
          onOpenDecision={() => setIsDecisionOpen(true)}
          onOpenContext={(relatedItem) =>
            setActiveContextId((relatedItem ?? contextActionTarget)?.targetId ?? null)
          }
        />

        <div className="grid gap-6 xl:grid-cols-[0.82fr_1.18fr]">
          <section className="rounded-[30px] border border-[var(--workspace-border)] bg-[var(--workspace-card)] p-6 shadow-panel">
            <div className="mb-5 flex items-center justify-between">
              <h2 className="text-xl font-semibold tracking-tight text-[var(--workspace-text)]">
                Current status
              </h2>
              <div className="h-2 w-14 rounded-full bg-[var(--workspace-accent-soft)]" />
            </div>
            <div className="space-y-3">
              <div className="rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-3.5">
                <div className="text-[0.7rem] font-medium uppercase tracking-[0.18em] text-[var(--workspace-text-faint)]">
                  Status
                </div>
                <div className="mt-2">
                  <span
                    className={`inline-flex rounded-full border px-3 py-1 text-[0.7rem] font-medium uppercase tracking-[0.16em] ${getStatusTone(
                      item.status,
                    )}`}
                  >
                    {controller.getStatusLabel(item.status)}
                  </span>
                </div>
              </div>
              <div className="rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-3.5">
                <div className="text-[0.7rem] font-medium uppercase tracking-[0.18em] text-[var(--workspace-text-faint)]">
                  Owner
                </div>
                <div className="mt-2 text-[1rem] font-medium tracking-[-0.012em] text-[var(--workspace-text)]">
                  {item.owner}
                </div>
              </div>
              <div className="rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-3.5">
                <div className="text-[0.7rem] font-medium uppercase tracking-[0.18em] text-[var(--workspace-text-faint)]">
                  Next step
                </div>
                <div className="mt-2 text-[0.92rem] leading-6 text-[var(--workspace-text-soft)]">
                  {item.nextStep}
                </div>
              </div>
              <div className="rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-3.5">
                <div className="text-[0.7rem] font-medium uppercase tracking-[0.18em] text-[var(--workspace-text-faint)]">
                  Updated
                </div>
                <div className="mt-2 text-[0.92rem] leading-6 text-[var(--workspace-text-soft)]">
                  {formatDisplayTimestamp(item.updatedAt)}
                </div>
              </div>
            </div>
          </section>

          <section className="rounded-[30px] border border-[var(--workspace-border)] bg-[var(--workspace-card)] p-6 shadow-panel">
            <div className="mb-5 flex items-center justify-between">
              <h2 className="text-xl font-semibold tracking-tight text-[var(--workspace-text)]">
                Highlights
              </h2>
              <div className="h-2 w-14 rounded-full bg-[var(--workspace-accent-soft)]" />
            </div>
            <div className="space-y-2.5">
              {item.highlights.map((highlight) => (
                <div
                  key={highlight}
                  className="rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-3.5"
                >
                  <div className="text-[0.92rem] leading-6 text-[var(--workspace-text-soft)]">
                    {highlight}
                  </div>
                </div>
              ))}
            </div>
          </section>
        </div>

        <section className="rounded-[30px] border border-[var(--workspace-border)] bg-[var(--workspace-card)] p-6 shadow-panel">
          <div className="mb-5 flex items-center justify-between">
            <h2 className="text-xl font-semibold tracking-tight text-[var(--workspace-text)]">
              Related items
            </h2>
            <div className="h-2 w-14 rounded-full bg-[var(--workspace-accent-soft)]" />
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {item.relatedItems.map((relatedItem) => (
              <button
                key={relatedItem.id}
                type="button"
                onClick={() => setActiveContextId(relatedItem.targetId)}
                className="rounded-[20px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card-subtle)] px-4 py-3.5 text-left transition-[background-color,border-color] duration-150 hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface)] focus-visible:outline-none"
              >
                <div className="text-[0.88rem] font-medium leading-6 text-[var(--workspace-text)]">
                  {relatedItem.label}
                </div>
                {relatedItem.previewText ? (
                  <div className="mt-1.5 text-[0.82rem] leading-6 text-[var(--workspace-text-faint)]">
                    {relatedItem.previewText}
                  </div>
                ) : null}
              </button>
            ))}
          </div>
        </section>
      </div>
      {isDecisionOpen ? (
        <ReviewDecisionModal
          item={item}
          controller={controller}
          onClose={() => setIsDecisionOpen(false)}
          onHandleNow={onHandleNow}
        />
      ) : null}
      {activeContext ? (
        <ReviewContextModal entity={activeContext} onClose={() => setActiveContextId(null)} />
      ) : null}
      <ReviewToastView toast={controller.toast} />
    </>
  );
}
