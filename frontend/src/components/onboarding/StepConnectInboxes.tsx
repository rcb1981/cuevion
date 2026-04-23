import { useState } from "react";
import {
  createInboxConnection,
  mainInboxOptions,
  providerOptions,
  specializedInboxOptions,
} from "../../data/onboardingOptions";
import type {
  InboxConnectionAttemptResult,
  LiveInboxMessageSnapshot,
} from "../../lib/inboxConnectionApi";
import { beginInboxConnection } from "../../lib/inboxConnectionApi";
import {
  createDefaultCustomSmtpSettings,
  getPasswordLabel,
  getProviderConnectionMethod,
  isImapCredentialsProvider,
  isOAuthConnectionProvider,
} from "../../lib/inboxProviderDefaults";
import { onboardingText } from "../../copy/onboardingCopy";
import type {
  CustomInboxDefinition,
  CustomImapSettings,
  CustomSmtpSettings,
  InboxConnection,
  InboxConnectionStatus,
  InboxId,
  OnboardingState,
  ProviderId,
} from "../../types/onboarding";

interface ConnectionFeedback {
  email?: string;
  host?: string;
  password?: string;
  general?: string;
}

interface StepConnectInboxesProps {
  selectedInboxes: InboxId[];
  customInboxes: CustomInboxDefinition[];
  inboxConnections: Record<string, InboxConnection>;
  internalRole?: string | null;
  focusPreferences?: OnboardingState["focusPreferences"] | null;
  onProviderChange: (inboxId: InboxId, provider: ProviderId) => void;
  onEmailChange: (inboxId: InboxId, email: string) => void;
  onCustomImapChange: (
    inboxId: InboxId,
    field: keyof CustomImapSettings,
    value: string | boolean,
  ) => void;
  onCustomSmtpChange: (
    inboxId: InboxId,
    field: keyof CustomSmtpSettings,
    value: string | boolean,
  ) => void;
  onReuseCustomImap: (inboxId: InboxId, settings: CustomImapSettings) => void;
  onConnectInbox: (
    inboxId: InboxId,
    result: {
      connected: boolean;
      connectionMethod: ReturnType<typeof getProviderConnectionMethod>;
      connectionStatus: InboxConnectionStatus;
      connectionMessage?: string | null;
      oauthAuthorizationUrl?: string | null;
    },
    messages?: LiveInboxMessageSnapshot[],
  ) => void;
}

const presetInboxLabelMap = Object.fromEntries(
  [...mainInboxOptions, ...specializedInboxOptions].map((option) => [
    option.id,
    option.label,
  ]),
) as Record<string, string>;

function hasReusableSettings(settings: CustomImapSettings) {
  return Boolean(settings.host && settings.port && settings.username);
}

function getConnectionStatusLabel(connection: InboxConnection) {
  if (connection.connectionStatus === "connected") {
    return onboardingText.connect.connected;
  }

  if (connection.connectionStatus === "oauth_required") {
    return onboardingText.connect.oauthRequired;
  }

  if (connection.connectionStatus === "waiting_for_authentication") {
    return onboardingText.connect.waitingForAuthentication;
  }

  if (connection.connectionStatus === "authenticated_pending_activation") {
    return onboardingText.connect.authenticatedPendingActivation;
  }

  if (connection.connectionStatus === "connection_failed") {
    return onboardingText.connect.connectionFailed;
  }

  return onboardingText.connect.notConnected;
}

function getConnectionStatusClassName(connection: InboxConnection) {
  if (connection.connectionStatus === "connected") {
    return "border-[var(--workspace-status-success-border)] bg-[var(--workspace-status-success-bg)] text-[var(--workspace-status-success-text)]";
  }

  if (
    connection.connectionStatus === "oauth_required" ||
    connection.connectionStatus === "waiting_for_authentication" ||
    connection.connectionStatus === "authenticated_pending_activation"
  ) {
    return "border-moss/18 bg-sand/55 text-moss";
  }

  if (connection.connectionStatus === "connection_failed") {
    return "border-amber-900/16 bg-amber-50/70 text-amber-900/75";
  }

  return "border-ink/10 bg-white/72 text-ink/55";
}

function isConnectionReady(connection: InboxConnection) {
  if (!connection.provider || !connection.email.trim()) {
    return false;
  }

  if (isOAuthConnectionProvider(connection.provider)) {
    return true;
  }

  if (!isImapCredentialsProvider(connection.provider)) {
    return true;
  }

  const { host, port, username, password } = connection.customImap;
  return Boolean(host.trim() && port.trim() && username.trim() && password.trim());
}

function getConnectionFeedback(connection: InboxConnection): ConnectionFeedback | null {
  const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  const normalizedEmail = connection.email.trim().toLowerCase();

  if (!emailPattern.test(connection.email.trim())) {
    return { email: onboardingText.connect.invalidEmail };
  }

  if (!isImapCredentialsProvider(connection.provider)) {
    if (normalizedEmail.includes("timeout")) {
      return { general: onboardingText.connect.connectionTimedOut };
    }

    if (
      normalizedEmail.includes("server") ||
      normalizedEmail.includes("offline") ||
      normalizedEmail.includes("unreachable") ||
      normalizedEmail.includes("fail")
    ) {
      return { general: onboardingText.connect.couldNotConnect };
    }

    return null;
  }

  const { host, port, password } = connection.customImap;
  const normalizedHost = host.trim().toLowerCase();
  const normalizedPassword = password.trim().toLowerCase();
  const parsedPort = Number(port);

  if (!/^[a-z0-9.-]+\.[a-z]{2,}$/i.test(host.trim())) {
    return { host: onboardingText.connect.invalidHost };
  }

  if (
    normalizedPassword.includes("wrong") ||
    normalizedPassword.includes("incorrect") ||
    normalizedPassword.includes("invalid")
  ) {
    return { password: onboardingText.connect.incorrectPassword };
  }

  if (!Number.isInteger(parsedPort) || parsedPort < 1 || parsedPort > 65535) {
    return { general: onboardingText.connect.couldNotConnect };
  }

  if (normalizedHost.includes("timeout")) {
    return { general: onboardingText.connect.connectionTimedOut };
  }

  if (
    normalizedHost.includes("server") ||
    normalizedHost.includes("offline") ||
    normalizedHost.includes("unreachable") ||
    normalizedHost.includes("fail")
  ) {
    return { general: onboardingText.connect.couldNotConnect };
  }

  return null;
}

function buildConnectionError(
  result: InboxConnectionAttemptResult,
): ConnectionFeedback {
  if (result.error?.code === "invalid_credentials") {
    return {
      password:
        result.error?.message || onboardingText.connect.incorrectPassword,
    };
  }

  return {
    general:
      result.error?.message ||
      result.connectionMessage ||
      onboardingText.connect.couldNotConnect,
  };
}

export function StepConnectInboxes({
  selectedInboxes,
  customInboxes,
  inboxConnections,
  internalRole,
  focusPreferences,
  onProviderChange,
  onEmailChange,
  onCustomImapChange,
  onCustomSmtpChange,
  onReuseCustomImap,
  onConnectInbox,
}: StepConnectInboxesProps) {
  const [loadingInboxId, setLoadingInboxId] = useState<InboxId | null>(null);
  const [connectionErrors, setConnectionErrors] = useState<
    Partial<Record<InboxId, ConnectionFeedback>>
  >({});

  const clearConnectionFeedback = (inboxId: InboxId) => {
    setConnectionErrors((current) => {
      if (!current[inboxId]) {
        return current;
      }

      const next = { ...current };
      delete next[inboxId];
      return next;
    });
  };

  const getInboxLabel = (inboxId: InboxId) =>
    customInboxes.find((inbox) => inbox.id === inboxId)?.name ??
    presetInboxLabelMap[inboxId] ??
    "Custom Inbox";

  const handleConnectInbox = async (
    inboxId: InboxId,
    connection: InboxConnection,
  ) => {
    if (!isConnectionReady(connection) || loadingInboxId === inboxId) {
      return;
    }

    setLoadingInboxId(inboxId);
    clearConnectionFeedback(inboxId);

    const feedback = getConnectionFeedback(connection);

    if (feedback) {
      setConnectionErrors((current) => ({
        ...current,
        [inboxId]: feedback,
      }));
      setLoadingInboxId(null);
      return;
    }

    console.log("[DEBUG] StepConnectInboxes selectedInboxes:", selectedInboxes);
    console.log("[DEBUG] Sending to backend selectedInboxes:", selectedInboxes);

    const result = await beginInboxConnection({
      provider: connection.provider as ProviderId,
      email: connection.email,
      customImap: connection.customImap,
      internalRole,
      focusPreferences,
      selectedInboxes,
    });

    const authorizationUrl =
      result.connectionStatus === "waiting_for_authentication"
        ? result.oauthAuthorizationUrl
        : null;

    if (result.ok) {
      onConnectInbox(
        inboxId,
        {
          connected: result.connected,
          connectionMethod: result.connectionMethod,
          connectionStatus: result.connectionStatus,
          connectionMessage: result.connectionMessage ?? null,
          oauthAuthorizationUrl: result.oauthAuthorizationUrl ?? null,
        },
        result.messages ?? [],
      );
      clearConnectionFeedback(inboxId);
    } else {
      setConnectionErrors((current) => ({
        ...current,
        [inboxId]: buildConnectionError(result),
      }));
      onConnectInbox(inboxId, {
        connected: false,
        connectionMethod: result.connectionMethod,
        connectionStatus: "connection_failed",
        connectionMessage: result.connectionMessage ?? null,
        oauthAuthorizationUrl: null,
      });
    }

    setLoadingInboxId(null);

    if (authorizationUrl) {
      window.location.assign(authorizationUrl);
    }
  };

  return (
    <section className="space-y-8">
      <div className="space-y-3">
        <h2 className="text-3xl font-semibold tracking-tight text-ink">
          {onboardingText.connect.title}
        </h2>
        <p className="text-base text-ink/68">
          {onboardingText.connect.description}
        </p>
      </div>

      <div className="space-y-6">
        {selectedInboxes.map((inboxId, index) => {
          const rawConnection = inboxConnections[inboxId] ?? createInboxConnection();
          const connection = {
            ...rawConnection,
            customSmtp: {
              ...createDefaultCustomSmtpSettings(),
              ...rawConnection.customSmtp,
            },
          };
          const readyToConnect = isConnectionReady(connection);
          const isLoading = loadingInboxId === inboxId;
          const errorMessage = connectionErrors[inboxId];
          const smtpSettings = connection.customSmtp ?? createDefaultCustomSmtpSettings();
          const reusableSettings = selectedInboxes
            .slice(0, index)
            .map((previousInboxId) => inboxConnections[previousInboxId])
            .find(
              (previousConnection) =>
                previousConnection.provider === "custom_imap" &&
                hasReusableSettings(previousConnection.customImap),
            )?.customImap;

          return (
            <section
              key={inboxId}
              className={`rounded-[30px] border bg-white/85 p-6 shadow-panel transition ${
                connection.connected
                  ? "border-pine/28"
                  : "border-ink/10"
              }`}
            >
              <div className="mb-5 flex items-start justify-between gap-4">
                <div>
                  <h3 className="text-xl font-semibold text-ink">
                    {getInboxLabel(inboxId)}
                  </h3>
                  <p className="mt-1 text-sm text-ink/60">
                    {onboardingText.connect.inboxHint}
                  </p>
                </div>
                <span
                  className={`rounded-full border px-3 py-1 text-xs font-medium ${getConnectionStatusClassName(connection)}`}
                >
                  {getConnectionStatusLabel(connection)}
                </span>
              </div>

              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {providerOptions.map((provider) => {
                  const selected = connection.provider === provider.id;
                  return (
                    <button
                      key={provider.id}
                      type="button"
                      onClick={() => {
                        clearConnectionFeedback(inboxId);
                        onProviderChange(inboxId, provider.id);
                      }}
                      className={`rounded-3xl border px-4 py-3 text-left transition ${
                        selected
                          ? "border-[var(--workspace-provider-selected-border)] bg-[var(--workspace-provider-selected-surface)] text-[var(--workspace-provider-selected-text)] shadow-panel"
                          : "border-ink/10 bg-sand/35 text-ink hover:border-moss/35 dark:border-[var(--workspace-border-soft)] dark:bg-[var(--workspace-card-subtle)] dark:text-[var(--workspace-text)] dark:hover:border-[var(--workspace-border-hover)] dark:hover:bg-[var(--workspace-hover-surface)]"
                      } outline-none focus-visible:border-[var(--workspace-provider-selected-border)] focus-visible:bg-[var(--workspace-provider-selected-surface)] focus-visible:text-[var(--workspace-provider-selected-text)] focus-visible:shadow-panel`}
                    >
                      <span className="text-sm font-semibold">
                        {provider.label}
                      </span>
                    </button>
                  );
                })}
              </div>

              <div className="mt-5">
                <label className="mb-2 block text-sm font-medium text-ink/75">
                  {onboardingText.connect.email}
                </label>
                <input
                  type="email"
                  value={connection.email}
                  onChange={(event) => {
                    clearConnectionFeedback(inboxId);
                    onEmailChange(inboxId, event.target.value);
                  }}
                  placeholder="name@company.com"
                  className="w-full rounded-2xl border border-ink/10 bg-white px-4 py-3 text-ink outline-none transition focus:border-moss"
                />
                <div className="mt-2 min-h-[18px] text-sm text-amber-900/60">
                  {errorMessage?.email ?? ""}
                </div>
              </div>

              {isImapCredentialsProvider(connection.provider) ? (
                <div className="mt-6 space-y-4 rounded-[24px] border border-ink/8 bg-sand/20 p-5">
                  {connection.provider === "custom_imap" && reusableSettings ? (
                    <div className="flex items-center justify-between gap-4 rounded-2xl border border-moss/10 bg-white/72 px-4 py-3">
                      <p className="text-sm text-ink/70">
                        {onboardingText.connect.reusePreviousServerSettings}
                      </p>
                      <button
                        type="button"
                        onClick={() => {
                          clearConnectionFeedback(inboxId);
                          onReuseCustomImap(inboxId, reusableSettings);
                        }}
                        className="rounded-full border border-moss/20 px-4 py-2 text-sm font-medium text-moss transition hover:border-moss/35 hover:bg-sand"
                      >
                        {onboardingText.connect.reuse}
                      </button>
                    </div>
                  ) : null}

                  <div className="grid gap-4 md:grid-cols-2">
                    <div>
                      <label className="mb-2 block text-sm font-medium text-ink/75">
                        {onboardingText.connect.host}
                      </label>
                      <input
                        type="text"
                        value={connection.customImap.host}
                        onChange={(event) => {
                          clearConnectionFeedback(inboxId);
                          onCustomImapChange(inboxId, "host", event.target.value);
                        }}
                        className="w-full rounded-2xl border border-ink/10 bg-white px-4 py-3 outline-none transition focus:border-moss"
                      />
                      <div className="mt-2 min-h-[18px] text-sm text-amber-900/60">
                        {errorMessage?.host ?? ""}
                      </div>
                    </div>
                    <div>
                      <label className="mb-2 block text-sm font-medium text-ink/75">
                        {onboardingText.connect.port}
                      </label>
                      <input
                        type="text"
                        value={connection.customImap.port}
                        onChange={(event) => {
                          clearConnectionFeedback(inboxId);
                          onCustomImapChange(inboxId, "port", event.target.value);
                        }}
                        className="w-full rounded-2xl border border-ink/10 bg-white px-4 py-3 outline-none transition focus:border-moss"
                      />
                    </div>
                    {connection.provider === "custom_imap" ? (
                      <div>
                        <label className="mb-2 block text-sm font-medium text-ink/75">
                          {onboardingText.connect.username}
                        </label>
                        <input
                          type="text"
                          value={connection.customImap.username}
                          onChange={(event) => {
                            clearConnectionFeedback(inboxId);
                            onCustomImapChange(
                              inboxId,
                              "username",
                              event.target.value,
                            );
                          }}
                          className="w-full rounded-2xl border border-ink/10 bg-white px-4 py-3 outline-none transition focus:border-moss"
                        />
                      </div>
                    ) : (
                      <div>
                        <label className="mb-2 block text-sm font-medium text-ink/75">
                          {onboardingText.connect.username}
                        </label>
                        <div className="w-full rounded-2xl border border-ink/10 bg-white px-4 py-3 text-ink/70">
                          {connection.email.trim() || "Uses the inbox email above"}
                        </div>
                      </div>
                    )}
                    <div>
                      <label className="mb-2 block text-sm font-medium text-ink/75">
                        {getPasswordLabel(connection.provider)}
                      </label>
                      <input
                        type="password"
                        value={connection.customImap.password}
                        onChange={(event) => {
                          clearConnectionFeedback(inboxId);
                          onCustomImapChange(
                            inboxId,
                            "password",
                            event.target.value,
                          );
                        }}
                        className="w-full rounded-2xl border border-ink/10 bg-white px-4 py-3 outline-none transition focus:border-moss"
                      />
                      <div className="mt-2 min-h-[18px] text-sm text-amber-900/60">
                        {errorMessage?.password ?? ""}
                      </div>
                    </div>
                  </div>

                  <label className="flex items-center gap-3 text-sm font-medium text-ink/75">
                    <span className="relative flex h-4 w-4 items-center justify-center">
                      <input
                        type="checkbox"
                        checked={connection.customImap.ssl}
                        onChange={(event) => {
                          clearConnectionFeedback(inboxId);
                          onCustomImapChange(inboxId, "ssl", event.target.checked);
                        }}
                        className="peer absolute inset-0 m-0 h-full w-full cursor-pointer appearance-none rounded-[5px] border border-ink/18 bg-white/80 outline-none transition checked:border-moss/55 checked:bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))] focus-visible:border-pine"
                      />
                      <span className="pointer-events-none absolute inset-0 flex items-center justify-center text-[10px] font-semibold leading-none text-moss opacity-0 transition peer-checked:opacity-100">
                        ✓
                      </span>
                    </span>
                    {onboardingText.connect.ssl}
                  </label>
                  {connection.provider === "custom_imap" ? (
                    <div className="space-y-4 border-t border-ink/8 pt-4">
                      <div>
                        <p className="text-sm font-semibold text-ink/80">
                          Outgoing SMTP
                        </p>
                        <p className="mt-1 text-sm text-ink/60">
                          Enter explicit SMTP send settings. Cuevion does not infer these from IMAP.
                        </p>
                      </div>

                      <div className="grid gap-4 md:grid-cols-2">
                        <div>
                          <label className="mb-2 block text-sm font-medium text-ink/75">
                            SMTP host
                          </label>
                          <input
                            type="text"
                            value={smtpSettings.host}
                            onChange={(event) => {
                              clearConnectionFeedback(inboxId);
                              onCustomSmtpChange(inboxId, "host", event.target.value);
                            }}
                            className="w-full rounded-2xl border border-ink/10 bg-white px-4 py-3 outline-none transition focus:border-moss"
                          />
                        </div>
                        <div>
                          <label className="mb-2 block text-sm font-medium text-ink/75">
                            SMTP port
                          </label>
                          <input
                            type="text"
                            value={smtpSettings.port}
                            onChange={(event) => {
                              clearConnectionFeedback(inboxId);
                              onCustomSmtpChange(inboxId, "port", event.target.value);
                            }}
                            className="w-full rounded-2xl border border-ink/10 bg-white px-4 py-3 outline-none transition focus:border-moss"
                          />
                        </div>
                        <div>
                          <label className="mb-2 block text-sm font-medium text-ink/75">
                            SMTP security
                          </label>
                          <select
                            value={smtpSettings.security}
                            onChange={(event) => {
                              clearConnectionFeedback(inboxId);
                              onCustomSmtpChange(inboxId, "security", event.target.value);
                            }}
                            className="w-full rounded-2xl border border-ink/10 bg-white px-4 py-3 outline-none transition focus:border-moss"
                          >
                            <option value="starttls">STARTTLS</option>
                            <option value="ssl">SSL/TLS</option>
                          </select>
                        </div>
                        <label className="flex items-center gap-3 pt-8 text-sm font-medium text-ink/75">
                          <span className="relative flex h-4 w-4 items-center justify-center">
                            <input
                              type="checkbox"
                              checked={smtpSettings.useSameCredentials}
                              onChange={(event) => {
                                clearConnectionFeedback(inboxId);
                                onCustomSmtpChange(
                                  inboxId,
                                  "useSameCredentials",
                                  event.target.checked,
                                );
                              }}
                              className="peer absolute inset-0 m-0 h-full w-full cursor-pointer appearance-none rounded-[5px] border border-ink/18 bg-white/80 outline-none transition checked:border-moss/55 checked:bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))] focus-visible:border-pine"
                            />
                            <span className="pointer-events-none absolute inset-0 flex items-center justify-center text-[10px] font-semibold leading-none text-moss opacity-0 transition peer-checked:opacity-100">
                              ✓
                            </span>
                          </span>
                          Use same credentials as incoming
                        </label>
                        {!smtpSettings.useSameCredentials ? (
                          <>
                            <div>
                              <label className="mb-2 block text-sm font-medium text-ink/75">
                                SMTP username
                              </label>
                              <input
                                type="text"
                                value={smtpSettings.username}
                                onChange={(event) => {
                                  clearConnectionFeedback(inboxId);
                                  onCustomSmtpChange(
                                    inboxId,
                                    "username",
                                    event.target.value,
                                  );
                                }}
                                className="w-full rounded-2xl border border-ink/10 bg-white px-4 py-3 outline-none transition focus:border-moss"
                              />
                            </div>
                            <div>
                              <label className="mb-2 block text-sm font-medium text-ink/75">
                                SMTP password
                              </label>
                              <input
                                type="password"
                                value={smtpSettings.password}
                                onChange={(event) => {
                                  clearConnectionFeedback(inboxId);
                                  onCustomSmtpChange(
                                    inboxId,
                                    "password",
                                    event.target.value,
                                  );
                                }}
                                className="w-full rounded-2xl border border-ink/10 bg-white px-4 py-3 outline-none transition focus:border-moss"
                              />
                            </div>
                          </>
                        ) : null}
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : isOAuthConnectionProvider(connection.provider) ? (
                <div className="mt-6 space-y-3 rounded-[24px] border border-moss/10 bg-[linear-gradient(180deg,rgba(246,248,241,0.88),rgba(255,252,247,0.96))] p-5">
                  <div className="space-y-1">
                    <p className="text-sm font-semibold text-ink">
                      {onboardingText.connect.googleOAuthTitle}
                    </p>
                    <p className="text-sm text-ink/68">
                      {onboardingText.connect.googleOAuthDescription}
                    </p>
                  </div>
                  <div className="rounded-2xl border border-moss/10 bg-white/72 px-4 py-3 text-sm text-ink/64">
                    {connection.connectionMessage?.trim() ||
                      (connection.connectionStatus ===
                      "authenticated_pending_activation"
                        ? onboardingText.connect.googleOAuthActivationPending
                        : onboardingText.connect.googleOAuthPending)}
                  </div>
                </div>
              ) : null}

              <div className="mt-6 flex justify-end">
                <button
                  type="button"
                  onClick={() => handleConnectInbox(inboxId, connection)}
                  disabled={!readyToConnect || isLoading}
                  className="rounded-full border border-moss/16 bg-white/72 px-4 py-2 text-sm font-medium text-moss transition hover:border-moss/28 hover:bg-white disabled:cursor-not-allowed disabled:border-ink/10 disabled:text-ink/35"
                >
                  {isLoading
                    ? onboardingText.connect.testingConnection
                    : isOAuthConnectionProvider(connection.provider)
                      ? onboardingText.connect.continueWithGoogle
                      : onboardingText.connect.connectInbox}
                </button>
              </div>

              <div className="mt-3 min-h-[20px] text-sm text-ink/52">
                {isLoading ? (
                  <span>{onboardingText.connect.testingConnection}</span>
                ) : errorMessage?.general ? (
                  <span className="inline-flex items-center gap-2 text-amber-900/60">
                    <span aria-hidden="true" className="text-xs">
                      !
                    </span>
                    {errorMessage.general}
                  </span>
                ) : null}
              </div>
            </section>
          );
        })}
      </div>
    </section>
  );
}
