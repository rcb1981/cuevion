/**
 * Microsoft-only presentation helpers. Gmail/IMAP owns the existing layout
 * preference; we may read it so Microsoft looks the same, but never write it.
 * Neither message identifiers nor mailbox contents are persisted here.
 */
const MAIL_LIST_PANE_WIDTH_STORAGE_KEY = "cuevion-mail-list-pane-width";
const FOLDER_COLUMN_WIDTH = 152;
const MIN_LIST_WIDTH = 320;
const MIN_DETAIL_WIDTH = 400;

export function readMicrosoftMailListPaneWidth(): number {
  if (typeof window === "undefined") return 520;
  try {
    const stored = window.localStorage.getItem(MAIL_LIST_PANE_WIDTH_STORAGE_KEY);
    const parsed = stored ? Number.parseInt(stored, 10) : Number.NaN;
    return Number.isFinite(parsed) ? parsed : 520;
  } catch {
    return 520;
  }
}

export function microsoftEffectiveListPaneWidth(
  preferredWidth: number,
  splitPaneWidth: number,
): number {
  const maxWidth = Math.max(
    MIN_LIST_WIDTH,
    splitPaneWidth - FOLDER_COLUMN_WIDTH - MIN_DETAIL_WIDTH,
  );
  return Math.min(Math.max(preferredWidth, MIN_LIST_WIDTH), maxWidth);
}

export function restoreMicrosoftMessageId<T extends { providerMessageId?: string | null }>(
  visibleRows: readonly T[],
  rememberedId: string | null | undefined,
): string | null {
  if (rememberedId && visibleRows.some((row) => row.providerMessageId === rememberedId)) {
    return rememberedId;
  }
  return visibleRows.find((row) => Boolean(row.providerMessageId))?.providerMessageId ?? null;
}
