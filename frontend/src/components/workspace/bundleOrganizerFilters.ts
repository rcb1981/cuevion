export type BundleOrganizerVisibleCategory =
  | "demo"
  | "high_priority_demo"
  | "promo"
  | "promo_reminder";

export type BundleOrganizerActiveWorkStatus =
  | "none"
  | "review"
  | "active"
  | "waiting"
  | "needs_reply"
  | "follow_up"
  | "closed";

export type BundleOrganizerMessageLike = {
  manualCategory?: "demo" | "promo" | null;
  manualPriority?: boolean | null;
  internalClassification?: string | null;
  category?: string | null;
  ui_signal?: string | null;
  v7_final_priority?: string | null;
  active_work_status?: BundleOrganizerActiveWorkStatus | string | null;
  organizerFollowUp?: boolean | null;
  unread?: boolean;
};

export const ORGANIZER_VISIBLE_CATEGORIES = new Set<BundleOrganizerVisibleCategory>([
  "demo",
  "high_priority_demo",
  "promo",
  "promo_reminder",
]);

export const ACTIVE_WORK_PRIORITY_STATUSES = new Set([
  "active",
  "waiting",
  "needs_reply",
  "follow_up",
]);

const normalizeSignal = (value: unknown) =>
  typeof value === "string" ? value.trim().toLowerCase() : "";

export function isOrganizerVisibleCategory(
  category: unknown,
): category is BundleOrganizerVisibleCategory {
  return ORGANIZER_VISIBLE_CATEGORIES.has(
    normalizeSignal(category) as BundleOrganizerVisibleCategory,
  );
}

function resolveOrganizerSignalFallback(
  value: unknown,
): BundleOrganizerVisibleCategory | null {
  const normalizedValue = normalizeSignal(value);

  if (
    normalizedValue === "demo" ||
    normalizedValue === "for review" ||
    normalizedValue === "shortlist"
  ) {
    return "demo";
  }

  if (normalizedValue === "promo") {
    return "promo";
  }

  return null;
}

export function resolveOrganizerCategory(
  message: BundleOrganizerMessageLike,
): BundleOrganizerVisibleCategory | null {
  const manualCategory = normalizeSignal(message.manualCategory);
  if (manualCategory === "demo" || manualCategory === "promo") {
    return manualCategory;
  }

  const internalClassification = normalizeSignal(message.internalClassification);
  if (internalClassification) {
    return isOrganizerVisibleCategory(internalClassification)
      ? internalClassification
      : null;
  }

  const category = normalizeSignal(message.category);
  if (category) {
    return isOrganizerVisibleCategory(category) ? category : null;
  }

  return resolveOrganizerSignalFallback(message.ui_signal);
}

export function shouldShowInDemoInbox(message: BundleOrganizerMessageLike) {
  const category = resolveOrganizerCategory(message);
  return category === "demo" || category === "high_priority_demo";
}

export function shouldShowInPromoInbox(message: BundleOrganizerMessageLike) {
  const category = resolveOrganizerCategory(message);
  return category === "promo" || category === "promo_reminder";
}

export function shouldShowInOrganizerPriority(message: BundleOrganizerMessageLike) {
  if (message.organizerFollowUp === true) {
    return true;
  }

  const category = resolveOrganizerCategory(message);

  if (category === null) {
    return false;
  }

  if (message.manualPriority === true) {
    return true;
  }

  if (ACTIVE_WORK_PRIORITY_STATUSES.has(normalizeSignal(message.active_work_status))) {
    return true;
  }

  return normalizeSignal(message.v7_final_priority) === "priority";
}

export function countUnreadMessages(messages: BundleOrganizerMessageLike[]) {
  return messages.filter((message) => message.unread === true).length;
}

export function formatOrganizerSignal(value: unknown) {
  return normalizeSignal(value);
}
