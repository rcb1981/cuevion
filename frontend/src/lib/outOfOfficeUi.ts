import type { OutOfOfficeDraft, OutOfOfficeSettings } from "./outOfOfficeApi";

export type OutOfOfficeUiDraft = OutOfOfficeDraft;

export function createEmptyOutOfOfficeDraft(): OutOfOfficeUiDraft {
  return {
    enabled: false,
    startsAt: null,
    endsAt: null,
    subject: "Out of office",
    message: "",
  };
}

export function normalizeOutOfOfficeUiDraft(
  value?: Partial<OutOfOfficeUiDraft> | OutOfOfficeSettings | null,
): OutOfOfficeUiDraft {
  const enabled = value?.enabled === true;
  const startsAt =
    typeof value?.startsAt === "string" && value.startsAt.trim()
      ? value.startsAt.trim()
      : null;
  const endsAt =
    typeof value?.endsAt === "string" && value.endsAt.trim()
      ? value.endsAt.trim()
      : null;
  const subject =
    typeof value?.subject === "string" && value.subject.trim()
      ? value.subject.trim()
      : "Out of office";
  const message = typeof value?.message === "string" ? value.message : "";

  return { enabled, startsAt, endsAt, subject, message };
}

function pad(value: number) {
  return String(value).padStart(2, "0");
}

export function isoToLocalDateTimeValue(value: string | null | undefined) {
  if (!value) return "";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  return [
    date.getFullYear(),
    "-",
    pad(date.getMonth() + 1),
    "-",
    pad(date.getDate()),
    "T",
    pad(date.getHours()),
    ":",
    pad(date.getMinutes()),
  ].join("");
}

export function localDateTimeValueToIso(value: string) {
  const text = value.trim();
  if (!text) return null;
  const date = new Date(text);
  if (!Number.isFinite(date.getTime())) return null;
  return date.toISOString();
}

export function validateOutOfOfficeUiDraft(draft: OutOfOfficeUiDraft) {
  if (!draft.enabled) return null;
  if (!draft.subject.trim()) return "Add a subject for the automatic reply.";
  if (!draft.message.trim()) return "Add a message for the automatic reply.";

  const startsAt = draft.startsAt ? Date.parse(draft.startsAt) : null;
  const endsAt = draft.endsAt ? Date.parse(draft.endsAt) : null;
  if (draft.startsAt && !Number.isFinite(startsAt)) return "Start date and time is invalid.";
  if (draft.endsAt && !Number.isFinite(endsAt)) return "End date and time is invalid.";
  if (
    typeof startsAt === "number" &&
    typeof endsAt === "number" &&
    endsAt <= startsAt
  ) {
    return "End date and time must be later than the start.";
  }
  return null;
}
