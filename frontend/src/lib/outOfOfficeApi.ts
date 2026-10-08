export type OutOfOfficeSettings = {
  schemaVersion: 1;
  enabled: boolean;
  startsAt: string | null;
  endsAt: string | null;
  activatedAt: string | null;
  subject: string;
  message: string;
  updatedAt: string;
};

export type OutOfOfficeDraft = Omit<
  OutOfOfficeSettings,
  "schemaVersion" | "updatedAt" | "activatedAt"
>;

type OutOfOfficeFailure = {
  ok: false;
  error?: {
    code?: string;
    message?: string;
  };
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizeSettings(value: unknown): OutOfOfficeSettings | null {
  if (!isRecord(value) || value.schemaVersion !== 1) return null;
  if (typeof value.enabled !== "boolean") return null;
  if (value.startsAt !== null && typeof value.startsAt !== "string") return null;
  if (value.endsAt !== null && typeof value.endsAt !== "string") return null;
  if (value.activatedAt !== null && typeof value.activatedAt !== "string") return null;
  if (typeof value.subject !== "string" || typeof value.message !== "string") {
    return null;
  }
  if (typeof value.updatedAt !== "string") return null;

  return {
    schemaVersion: 1,
    enabled: value.enabled,
    startsAt: value.startsAt,
    endsAt: value.endsAt,
    activatedAt: value.activatedAt,
    subject: value.subject,
    message: value.message,
    updatedAt: value.updatedAt,
  };
}

async function parseResponse(response: Response): Promise<OutOfOfficeSettings> {
  let payload: unknown;

  try {
    payload = await response.json();
  } catch {
    throw new Error("Out of office settings returned an invalid response.");
  }

  if (!response.ok) {
    const failure = isRecord(payload) ? (payload as OutOfOfficeFailure) : null;
    const message =
      failure &&
      isRecord(failure.error) &&
      typeof failure.error.message === "string"
        ? failure.error.message
        : "Out of office settings are temporarily unavailable.";
    throw new Error(message);
  }

  if (
    !isRecord(payload) ||
    payload.ok !== true ||
    typeof payload.mailboxId !== "string"
  ) {
    throw new Error("Out of office settings returned an invalid response.");
  }

  const settings = normalizeSettings(payload.settings);
  if (!settings) {
    throw new Error("Out of office settings returned an invalid response.");
  }

  return settings;
}

export async function loadOutOfOfficeSettings(
  mailboxId: string,
  signal?: AbortSignal,
): Promise<OutOfOfficeSettings> {
  const response = await fetch(
    "/api/out-of-office?mailboxId=" + encodeURIComponent(mailboxId),
    {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      cache: "no-store",
      signal,
    },
  );

  return parseResponse(response);
}

export async function saveOutOfOfficeSettings(
  mailboxId: string,
  settings: OutOfOfficeDraft,
  signal?: AbortSignal,
): Promise<OutOfOfficeSettings> {
  const response = await fetch("/api/out-of-office", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    cache: "no-store",
    signal,
    body: JSON.stringify({ mailboxId, settings }),
  });

  return parseResponse(response);
}
