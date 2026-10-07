export type PublicTesterInvite = {
  inviteeName: string;
  status: "invited" | "provisioned" | "cancelled";
  expiresAt: number;
};

export type TesterInviteLookupResult =
  | { ok: true; invite: PublicTesterInvite }
  | { ok: false; status: number; code: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export async function fetchTesterInvite(
  token: string,
  fetchImplementation: typeof fetch = fetch,
): Promise<TesterInviteLookupResult> {
  if (!/^tsti_[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}$/.test(token)) {
    return { ok: false, status: 400, code: "invalid_invite" };
  }

  try {
    const response = await fetchImplementation("/api/tester/invite?op=lookup", {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({ token }),
    });
    const payload = await response.json().catch(() => null);
    if (
      !response.ok ||
      !isRecord(payload) ||
      payload.ok !== true ||
      !isRecord(payload.invite) ||
      typeof payload.invite.inviteeName !== "string" ||
      (payload.invite.status !== "invited" &&
        payload.invite.status !== "provisioned" &&
        payload.invite.status !== "cancelled") ||
      typeof payload.invite.expiresAt !== "number"
    ) {
      const code =
        isRecord(payload) &&
        isRecord(payload.error) &&
        typeof payload.error.code === "string"
          ? payload.error.code
          : "unavailable";
      return { ok: false, status: response.status, code };
    }
    return {
      ok: true,
      invite: {
        inviteeName: payload.invite.inviteeName,
        status: payload.invite.status,
        expiresAt: payload.invite.expiresAt,
      },
    };
  } catch {
    return { ok: false, status: 0, code: "unavailable" };
  }
}
