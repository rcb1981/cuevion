export type PriorityWorkState =
  | "needs_user_action"
  | "other_priority"
  | "waiting_on_other";

export type PriorityChronologicalSortOrder = "newest" | "oldest";

export type PriorityWorkStateByItemId = Readonly<
  Partial<Record<string, PriorityWorkState>>
>;

const priorityWorkStateRank: Record<PriorityWorkState, number> = {
  needs_user_action: 0,
  other_priority: 1,
  waiting_on_other: 2,
};

export function resolvePriorityWorkState(input: {
  hasSemanticNeedsUserAction: boolean;
  hasReturnedReplyEvidence: boolean;
  hasWaitingOnOtherEvidence: boolean;
}): PriorityWorkState {
  if (input.hasWaitingOnOtherEvidence) {
    return "waiting_on_other";
  }

  if (input.hasSemanticNeedsUserAction || input.hasReturnedReplyEvidence) {
    return "needs_user_action";
  }

  return "other_priority";
}

export function rankPriorityItems<T>(
  items: readonly T[],
  options: {
    sortOrder: PriorityChronologicalSortOrder;
    workStateByItemId: PriorityWorkStateByItemId;
    getItemId: (item: T) => string;
    getTimestamp: (item: T) => number;
  },
) {
  return items
    .map((item, canonicalIndex) => ({ item, canonicalIndex }))
    .sort((firstEntry, secondEntry) => {
      const firstState =
        options.workStateByItemId[options.getItemId(firstEntry.item)] ??
        "other_priority";
      const secondState =
        options.workStateByItemId[options.getItemId(secondEntry.item)] ??
        "other_priority";
      const workStateDifference =
        priorityWorkStateRank[firstState] - priorityWorkStateRank[secondState];

      if (workStateDifference !== 0) {
        return workStateDifference;
      }

      const firstTimestamp = options.getTimestamp(firstEntry.item);
      const secondTimestamp = options.getTimestamp(secondEntry.item);
      const chronologicalDifference =
        options.sortOrder === "newest"
          ? secondTimestamp - firstTimestamp
          : firstTimestamp - secondTimestamp;

      return (
        chronologicalDifference ||
        firstEntry.canonicalIndex - secondEntry.canonicalIndex
      );
    })
    .map(({ item }) => item);
}
