export type SendContactSupportRequest = {
  id: string;
  subject: string;
  message: string;
  fromInbox: string;
  workspaceName: string;
  userName: string;
  userEmail: string;
  appSection: string;
  createdAt: string;
};

type ContactSupportError = {
  code?: string;
  message?: string;
};

type ContactSupportResponse =
  | {
      ok: true;
    }
  | {
      ok: false;
      error?: ContactSupportError;
    };

export async function sendContactSupportRequest(
  request: SendContactSupportRequest,
): Promise<ContactSupportResponse> {
  try {
    const response = await fetch("/api/contact/support", {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    });
    const payload = (await response.json()) as ContactSupportResponse;

    if (!response.ok || !payload.ok) {
      return {
        ok: false,
        error: payload.ok === false ? payload.error : undefined,
      };
    }

    return { ok: true };
  } catch {
    return {
      ok: false,
      error: {
        code: "support_request_unavailable",
        message: "Support request could not be sent.",
      },
    };
  }
}
