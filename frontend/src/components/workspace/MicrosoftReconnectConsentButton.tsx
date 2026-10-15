import React, { useRef, useState } from "react";
import {
  buildOAuthInboxRequest,
  connectInboxWithOAuth,
} from "../../lib/inboxConnectionApi";

/** Only a genuine Microsoft authorization page with this app's callback is safe. */
export function isTrustedMicrosoftReconnectUrl(
  value: unknown,
  expectedOrigin: string,
  accountEmail: string,
): value is string {
  if (typeof value !== "string" || !value || value.length > 8192) return false;
  try {
    const url = new URL(value);
    if (
      url.origin !== "https://login.microsoftonline.com" ||
      url.username || url.password || url.port ||
      !/^\/[A-Za-z0-9-]{1,64}\/oauth2\/v2\.0\/authorize$/.test(url.pathname) ||
      url.hash
    ) return false;
    const params = url.searchParams;
    const required = ["state", "client_id", "redirect_uri", "code_challenge", "scope"];
    if (required.some((key) =>
      params.getAll(key).length !== 1 || !params.get(key)
    )) return false;
    if (
      params.get("redirect_uri") !== expectedOrigin + "/api/inboxes/oauth-callback" ||
      params.get("response_type") !== "code" ||
      params.get("code_challenge_method") !== "S256" ||
      params.get("login_hint")?.toLowerCase() !== accountEmail.toLowerCase()
    ) return false;
    const scopes = params.get("scope")!.split(/\s+/);
    return scopes.includes("https://graph.microsoft.com/Mail.ReadWrite") &&
      !scopes.includes("https://graph.microsoft.com/Mail.Send") &&
      !scopes.includes("Mail.Send");
  } catch {
    return false;
  }
}

export function MicrosoftReconnectConsentButton({
  mailboxId,
  email,
}: {
  mailboxId: string;
  email: string;
}) {
  const busyRef = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reconnect = async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    try {
      // Backend enforces current authenticated owner, exact saved mailbox
      // ID and OAuth credential generation. No new mailbox is created.
      const request = buildOAuthInboxRequest({
        provider: "microsoft",
        email,
        mode: "reconnect",
        mailboxId,
      });
      const result = await connectInboxWithOAuth(request);
      if (
        result.ok !== true ||
        result.connectionStatus !== "waiting_for_authentication" ||
        !isTrustedMicrosoftReconnectUrl(
          result.authorizationUrl,
          window.location.origin,
          email,
        )
      ) {
        setError("Microsoft authorization could not be started. Please try again.");
        return;
      }
      window.location.assign(result.authorizationUrl);
    } catch {
      setError("Microsoft authorization could not be started. Please try again.");
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <button
        type="button"
        onClick={() => void reconnect()}
        disabled={busy}
        title="Reconnect this Microsoft account to allow automatic mark-as-read."
        className="rounded-lg border border-[var(--workspace-border)] px-3 py-2 text-xs font-medium hover:bg-[var(--workspace-hover-surface)] disabled:opacity-50"
      >
        {busy ? "Opening Microsoft…" : "Reconnect Microsoft"}
      </button>
      {error && <span role="alert" className="text-xs text-[var(--workspace-text-muted)]">{error}</span>}
    </div>
  );
}
