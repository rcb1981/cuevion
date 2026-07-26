import { useEffect, useRef, useState } from "react";
import {
  createInboxConnection,
  providerOptions,
} from "../../data/onboardingOptions";
import type {
  InboxConnectionAttemptResult,
  LiveInboxMessageSnapshot,
} from "../../lib/inboxConnectionApi";
import {
  beginInboxConnection,
  beginOnboardingInboxConnection,
} from "../../lib/inboxConnectionApi";
import {
  createCustomImapOnboardingAttemptCoordinator,
  createCustomImapOnboardingAttemptSnapshot,
  createCustomImapOnboardingFingerprint,
  createCustomImapSelectedPositionIdentity,
  isCustomImapOnboardingInteractionLocked,
  type CustomImapOnboardingAttemptCoordinator,
  type CustomImapOnboardingAttemptGuard,
  type CustomImapOnboardingAttemptSnapshot,
  type CustomImapOnboardingReconciliationResult,
} from "../../lib/customImapOnboardingAttempt";
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
  smtpHost?: string;
  smtpPassword?: string;
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
  onReloadAccountConfig: (
    snapshot: CustomImapOnboardingAttemptSnapshot,
    signal: AbortSignal,
  ) => Promise<CustomImapOnboardingReconciliationResult>;
  onApplyAuthoritativeCustomImapConnection: (
    snapshot: CustomImapOnboardingAttemptSnapshot,
    result: Extract<
      CustomImapOnboardingReconciliationResult,
      { status: "matched" }
    >,
  ) => void;
  customImapAttemptGuard: CustomImapOnboardingAttemptGuard | null;
  onCustomImapAttemptGuardChange: (
    guard: CustomImapOnboardingAttemptGuard | null,
  ) => void;
  canRemoveInbox?: (inboxId: InboxId) => boolean;
  onRemoveInbox?: (inboxId: InboxId) => void;
  onAddInbox: () => void;
  isPreviewMode?: boolean;
}

function hasReusableSettings(settings: CustomImapSettings) {
  return Boolean(settings.host && settings.port && settings.username);
}

export function isConnectionReady(
  connection: InboxConnection,
  imapPassword = connection.customImap.password,
  smtpPassword = connection.customSmtp.password,
) {
  if (!connection.provider) {
    return false;
  }

  if (isOAuthConnectionProvider(connection.provider)) {
    return true;
  }

  if (!connection.email.trim()) {
    return false;
  }

  if (!isImapCredentialsProvider(connection.provider)) {
    return true;
  }

  const smtp = connection.customSmtp;
  const smtpIsComplete = Boolean(
    smtp.host.trim() &&
      ((smtp.security === "ssl" && smtp.port.trim() === "465") ||
        (smtp.security === "starttls" && smtp.port.trim() === "587")) &&
      (smtp.useSameCredentials ||
        (smtp.username.trim() && smtpPassword.trim())),
  );
  if (!smtpIsComplete) {
    return false;
  }

  if (isAuthoritativeIncomingConnected(connection)) {
    return true;
  }

  const { host, port, ssl, username } = connection.customImap;
  return Boolean(
    host.trim() &&
      port.trim() &&
      ssl === true &&
      username.trim() &&
      imapPassword.trim(),
  );
}

export function isAuthoritativeIncomingConnected(
  connection: InboxConnection,
) {
  return Boolean(
    connection.provider === "custom_imap" &&
      connection.serverMailboxId?.trim() &&
      connection.connected === true &&
      connection.connectionStatus === "connected" &&
      connection.imapConnectionStatus === "connected",
  );
}

export function isOnboardingInboxFullyConnected(
  connection: InboxConnection | null | undefined,
) {
  if (!connection) {
    return false;
  }
  if (isOAuthConnectionProvider(connection.provider)) {
    return (
      connection.connected === true &&
      connection.connectionStatus === "connected"
    );
  }
  return Boolean(
    isAuthoritativeIncomingConnected(connection) &&
      connection.smtpConnectionStatus === "connected" &&
      connection.fullyConnected === true,
  );
}

export function getConnectionFeedback(
  connection: InboxConnection,
): ConnectionFeedback | null {
  const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  const normalizedEmail = connection.email.trim().toLowerCase();

  if (isOAuthConnectionProvider(connection.provider)) {
    return normalizedEmail && !emailPattern.test(normalizedEmail)
      ? { email: onboardingText.connect.invalidEmail }
      : null;
  }

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

  const { host, port, ssl } = connection.customImap;
  const parsedPort = Number(port);

  if (!/^[a-z0-9.-]+\.[a-z]{2,}$/i.test(host.trim())) {
    return { host: onboardingText.connect.invalidHost };
  }

  if (!Number.isInteger(parsedPort) || parsedPort < 1 || parsedPort > 65535) {
    return { general: onboardingText.connect.couldNotConnect };
  }

  if (ssl !== true) {
    return { general: onboardingText.connect.couldNotConnect };
  }

  const smtp = connection.customSmtp;
  if (!smtp.host.trim() || /\s/.test(smtp.host.trim())) {
    return { smtpHost: "Enter a valid SMTP host." };
  }
  if (
    !(
      (smtp.security === "ssl" && smtp.port.trim() === "465") ||
      (smtp.security === "starttls" && smtp.port.trim() === "587")
    )
  ) {
    return {
      general:
        "Use port 465 with SSL/TLS or port 587 with STARTTLS.",
    };
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

function buildSafeCustomImapConnectionError(
  result: InboxConnectionAttemptResult,
): ConnectionFeedback {
  if (result.error?.code === "invalid_credentials") {
    return { password: onboardingText.connect.incorrectPassword };
  }

  return { general: onboardingText.connect.couldNotConnect };
}

export function buildOnboardingInboxConnectionOptions({
  inboxId,
  connection,
  internalRole,
  focusPreferences,
  selectedInboxes,
}: {
  inboxId: InboxId;
  connection: InboxConnection;
  internalRole?: string | null;
  focusPreferences?: OnboardingState["focusPreferences"] | null;
  selectedInboxes: InboxId[];
}): Parameters<typeof beginInboxConnection>[0] {
  return {
    imapMode: "initial",
    mailboxId: inboxId,
    inboxPosition: inboxId,
    provider: connection.provider as ProviderId,
    email: connection.email,
    customImap: connection.customImap,
    customSmtp: connection.customSmtp,
    internalRole,
    focusPreferences,
    selectedInboxes,
  };
}

export function buildCustomImapOnboardingConnectionOptions({
  inboxId,
  connection,
  imapPassword,
  smtpPassword,
}: {
  inboxId: InboxId;
  connection: InboxConnection;
  imapPassword: string;
  smtpPassword: string;
}): Parameters<typeof beginOnboardingInboxConnection>[0] {
  return {
    onboardingInboxId: inboxId,
    serverMailboxId: connection.serverMailboxId,
    email: connection.email,
    customImap: {
      ...connection.customImap,
      password: "",
    },
    customSmtp: {
      ...connection.customSmtp,
      password: "",
    },
    imapPassword,
    smtpPassword,
  };
}

export function buildSuccessfulOnboardingConnectionUpdate(
  result: InboxConnectionAttemptResult,
) {
  const isOAuthStart = result.connectionMethod === "oauth";
  return {
    connected: isOAuthStart ? false : result.connected,
    connectionMethod: result.connectionMethod,
    connectionStatus: result.connectionStatus,
    connectionMessage: result.connectionMessage ?? null,
    oauthAuthorizationUrl: null,
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
  onReloadAccountConfig,
  onApplyAuthoritativeCustomImapConnection,
  customImapAttemptGuard,
  onCustomImapAttemptGuardChange,
  canRemoveInbox,
  onRemoveInbox,
  onAddInbox,
  isPreviewMode = false,
}: StepConnectInboxesProps) {
  const [loadingInboxId, setLoadingInboxId] = useState<InboxId | null>(null);
  const [imapPasswords, setImapPasswords] = useState<
    Partial<Record<InboxId, string>>
  >({});
  const [smtpPasswords, setSmtpPasswords] = useState<
    Partial<Record<InboxId, string>>
  >({});
  const [connectionErrors, setConnectionErrors] = useState<
    Partial<Record<InboxId, ConnectionFeedback>>
  >({});
  const mountedRef = useRef(false);
  const selectedInboxesRef = useRef(selectedInboxes);
  const inboxConnectionsRef = useRef(inboxConnections);
  const imapPasswordsRef = useRef<Partial<Record<InboxId, string>>>({});
  const smtpPasswordsRef = useRef<Partial<Record<InboxId, string>>>({});
  const passwordRevisionsRef = useRef<Partial<Record<InboxId, number>>>({});
  const smtpPasswordRevisionsRef =
    useRef<Partial<Record<InboxId, number>>>({});
  const attemptCoordinatorRef =
    useRef<CustomImapOnboardingAttemptCoordinator | null>(null);
  const attemptCallbacksRef = useRef({
    onConnectInbox,
    onReloadAccountConfig,
    onApplyAuthoritativeCustomImapConnection,
    onCustomImapAttemptGuardChange,
  });
  selectedInboxesRef.current = selectedInboxes;
  inboxConnectionsRef.current = inboxConnections;
  attemptCallbacksRef.current = {
    onConnectInbox,
    onReloadAccountConfig,
    onApplyAuthoritativeCustomImapConnection,
    onCustomImapAttemptGuardChange,
  };

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      const coordinator = attemptCoordinatorRef.current;
      coordinator?.dispose();
      if (attemptCoordinatorRef.current === coordinator) {
        attemptCoordinatorRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    setImapPasswords((current) => {
      const next = Object.fromEntries(
        Object.entries(current).filter(([inboxId]) =>
          selectedInboxes.includes(inboxId as InboxId),
        ),
      );
      imapPasswordsRef.current = next;
      return next;
    });
    setSmtpPasswords((current) => {
      const next = Object.fromEntries(
        Object.entries(current).filter(([inboxId]) =>
          selectedInboxes.includes(inboxId as InboxId),
        ),
      );
      smtpPasswordsRef.current = next;
      return next;
    });
  }, [selectedInboxes]);

  const clearImapPassword = (
    inboxId: InboxId,
    expectedRevision?: number,
  ) => {
    const currentRevision = passwordRevisionsRef.current[inboxId] ?? 0;
    if (
      expectedRevision !== undefined &&
      currentRevision !== expectedRevision
    ) {
      return null;
    }
    const nextRevision = currentRevision + 1;
    passwordRevisionsRef.current[inboxId] = nextRevision;
    const nextPasswords = { ...imapPasswordsRef.current };
    delete nextPasswords[inboxId];
    imapPasswordsRef.current = nextPasswords;
    if (mountedRef.current) {
      setImapPasswords(nextPasswords);
    }
    return nextRevision;
  };

  const setImapPassword = (inboxId: InboxId, password: string) => {
    passwordRevisionsRef.current[inboxId] =
      (passwordRevisionsRef.current[inboxId] ?? 0) + 1;
    const nextPasswords = {
      ...imapPasswordsRef.current,
      [inboxId]: password,
    };
    imapPasswordsRef.current = nextPasswords;
    setImapPasswords(nextPasswords);
  };

  const clearSmtpPassword = (
    inboxId: InboxId,
    expectedRevision?: number,
  ) => {
    const currentRevision =
      smtpPasswordRevisionsRef.current[inboxId] ?? 0;
    if (
      expectedRevision !== undefined &&
      currentRevision !== expectedRevision
    ) {
      return null;
    }
    const nextRevision = currentRevision + 1;
    smtpPasswordRevisionsRef.current[inboxId] = nextRevision;
    const nextPasswords = { ...smtpPasswordsRef.current };
    delete nextPasswords[inboxId];
    smtpPasswordsRef.current = nextPasswords;
    if (mountedRef.current) {
      setSmtpPasswords(nextPasswords);
    }
    return nextRevision;
  };

  const setSmtpPassword = (inboxId: InboxId, password: string) => {
    smtpPasswordRevisionsRef.current[inboxId] =
      (smtpPasswordRevisionsRef.current[inboxId] ?? 0) + 1;
    const nextPasswords = {
      ...smtpPasswordsRef.current,
      [inboxId]: password,
    };
    smtpPasswordsRef.current = nextPasswords;
    setSmtpPasswords(nextPasswords);
  };

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

  const getInboxLabel = (inboxId: InboxId, index: number) =>
    customInboxes.find((inbox) => inbox.id === inboxId)?.name ??
    `Inbox ${index + 1}`;

  const getAttemptCoordinator = () => {
    if (attemptCoordinatorRef.current) {
      return attemptCoordinatorRef.current;
    }

    attemptCoordinatorRef.current =
      createCustomImapOnboardingAttemptCoordinator({
        getCurrentContext: (snapshot) => {
          const currentConnection =
            inboxConnectionsRef.current[snapshot.onboardingInboxId];
          const currentSelectedInboxes = selectedInboxesRef.current;
          return {
            mounted: mountedRef.current,
            onboardingInboxId: currentSelectedInboxes.includes(
              snapshot.onboardingInboxId,
            )
              ? snapshot.onboardingInboxId
              : null,
            provider: currentConnection?.provider ?? null,
            selectedPositionIdentity: currentConnection
              ? createCustomImapSelectedPositionIdentity(
                  currentSelectedInboxes,
                  snapshot.onboardingInboxId,
                )
              : null,
            fingerprint: currentConnection
              ? createCustomImapOnboardingFingerprint({
                  onboardingInboxId: snapshot.onboardingInboxId,
                  selectedInboxes: currentSelectedInboxes,
                  connection: currentConnection,
                })
              : null,
            passwordRevision:
              passwordRevisionsRef.current[
                snapshot.onboardingInboxId
              ] ?? 0,
            smtpPasswordRevision:
              smtpPasswordRevisionsRef.current[
                snapshot.onboardingInboxId
              ] ?? 0,
          };
        },
        post: (snapshot, imapPassword, signal, smtpPassword) =>
          beginOnboardingInboxConnection(
            {
              onboardingInboxId: snapshot.onboardingInboxId,
              serverMailboxId: snapshot.serverMailboxId,
              email: snapshot.normalizedEmail,
              customImap: {
                host: snapshot.normalizedHost,
                port: snapshot.port,
                ssl: true,
                username: snapshot.normalizedUsername,
                password: "",
              },
              customSmtp: {
                host: snapshot.normalizedSmtpHost,
                port: snapshot.smtpPort,
                security: snapshot.smtpSecurity,
                username: snapshot.normalizedSmtpUsername,
                password: "",
                useSameCredentials: snapshot.useSameCredentials,
              },
              imapPassword,
              smtpPassword,
            },
            signal,
          ),
        reconcile: (snapshot, signal) =>
          attemptCallbacksRef.current.onReloadAccountConfig(
            snapshot,
            signal,
          ),
        consumePassword: (snapshot, expectedRevision) =>
          clearImapPassword(
            snapshot.onboardingInboxId,
            expectedRevision,
          ),
        consumeSmtpPassword: (snapshot, expectedRevision) =>
          clearSmtpPassword(
            snapshot.onboardingInboxId,
            expectedRevision,
          ),
        applyMatched: (snapshot, result, postResult) => {
          if (
            result.connection.fullyConnected === true ||
            !postResult ||
            postResult.ok
          ) {
            clearConnectionFeedback(snapshot.onboardingInboxId);
          } else {
            setConnectionErrors((current) => ({
              ...current,
              [snapshot.onboardingInboxId]: {
                general: onboardingText.connect.couldNotConnect,
              },
            }));
          }
          attemptCallbacksRef.current
            .onApplyAuthoritativeCustomImapConnection(snapshot, result);
        },
        applyAbsent: (snapshot, postResult) => {
          setConnectionErrors((current) => ({
            ...current,
            [snapshot.onboardingInboxId]:
              postResult && !postResult.ok
                ? buildSafeCustomImapConnectionError(postResult)
                : {
                    general:
                      onboardingText.connect.couldNotConnect,
                  },
          }));
          attemptCallbacksRef.current.onConnectInbox(
            snapshot.onboardingInboxId,
            {
              connected: false,
              connectionMethod: "imap",
              connectionStatus: "connection_failed",
              connectionMessage:
                onboardingText.connect.couldNotConnect,
              oauthAuthorizationUrl: null,
            },
          );
        },
        applyReconciliationRequired: (snapshot) => {
          setConnectionErrors((current) => ({
            ...current,
            [snapshot.onboardingInboxId]: {
              general:
                "Connection status could not be confirmed. Check connection status before trying again.",
            },
          }));
        },
        onGuardChange: (guard) => {
          attemptCallbacksRef.current
            .onCustomImapAttemptGuardChange(guard);
        },
      });
    return attemptCoordinatorRef.current;
  };

  useEffect(() => {
    if (
      !customImapAttemptGuard ||
      attemptCoordinatorRef.current?.getGuard()
    ) {
      return;
    }
    getAttemptCoordinator().adoptReconciliationGuard(
      customImapAttemptGuard,
    );
  }, [customImapAttemptGuard]);

  const customImapMutationIsLocked = () =>
    isCustomImapOnboardingInteractionLocked(
      customImapAttemptGuard,
    ) ||
    Boolean(attemptCoordinatorRef.current?.getGuard());

  const handleRemoveInbox = (inboxId: InboxId) => {
    if (
      !onRemoveInbox ||
      loadingInboxId === inboxId ||
      customImapMutationIsLocked()
    ) {
      return;
    }

    clearConnectionFeedback(inboxId);
    clearImapPassword(inboxId);
    clearSmtpPassword(inboxId);
    onRemoveInbox(inboxId);
  };

  const handleConnectInbox = async (
    inboxId: InboxId,
    connection: InboxConnection,
  ) => {
    const imapPassword = imapPasswordsRef.current[inboxId] ?? "";
    const smtpPassword = smtpPasswordsRef.current[inboxId] ?? "";
    if (
      !isConnectionReady(connection, imapPassword, smtpPassword) ||
      loadingInboxId !== null ||
      customImapMutationIsLocked()
    ) {
      return;
    }

    const feedback = getConnectionFeedback(connection);

    if (feedback) {
      setConnectionErrors((current) => ({
        ...current,
        [inboxId]: feedback,
      }));
      return;
    }

    if (isPreviewMode) {
      setLoadingInboxId(inboxId);
      clearConnectionFeedback(inboxId);
      if (isImapCredentialsProvider(connection.provider)) {
        clearImapPassword(inboxId);
        clearSmtpPassword(inboxId);
        setConnectionErrors((current) => ({
          ...current,
          [inboxId]: { general: onboardingText.connect.couldNotConnect },
        }));
        setLoadingInboxId(null);
        return;
      }

      onConnectInbox(inboxId, {
        connected: true,
        connectionMethod: getProviderConnectionMethod(connection.provider),
        connectionStatus: "connected",
        connectionMessage: "Preview connection only. No mailbox settings were saved.",
        oauthAuthorizationUrl: null,
      });
      clearConnectionFeedback(inboxId);
      setLoadingInboxId(null);
      return;
    }

    if (isImapCredentialsProvider(connection.provider)) {
      const snapshot = createCustomImapOnboardingAttemptSnapshot({
        onboardingInboxId: inboxId,
        selectedInboxes: selectedInboxesRef.current,
        connection,
        passwordRevision:
          passwordRevisionsRef.current[inboxId] ?? 0,
        smtpPasswordRevision:
          smtpPasswordRevisionsRef.current[inboxId] ?? 0,
      });
      if (
        getAttemptCoordinator().start(
          snapshot,
          imapPassword,
          smtpPassword,
        )
      ) {
        clearConnectionFeedback(inboxId);
      }
      return;
    }

    setLoadingInboxId(inboxId);
    clearConnectionFeedback(inboxId);
    const result = await beginInboxConnection(
      buildOnboardingInboxConnectionOptions({
        inboxId,
        connection,
        internalRole,
        focusPreferences,
        selectedInboxes,
      }),
    );

    const authorizationUrl =
      result.connectionStatus === "waiting_for_authentication"
        ? result.oauthAuthorizationUrl
        : null;

    if (result.ok) {
      onConnectInbox(
        inboxId,
        buildSuccessfulOnboardingConnectionUpdate(result),
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

  const effectiveCustomImapAttemptGuard =
    attemptCoordinatorRef.current?.getGuard() ??
    customImapAttemptGuard;
  const customImapInteractionLocked =
    isCustomImapOnboardingInteractionLocked(
      effectiveCustomImapAttemptGuard,
    );

  return (
    <section className="space-y-8">
      <div className="space-y-3">
        <h2 className="text-3xl font-semibold tracking-tight text-ink">
          {onboardingText.connect.title}
        </h2>
        <p className="text-base text-ink/68">
          {onboardingText.connect.description}
        </p>
        <p className="max-w-2xl text-sm leading-6 text-ink/54">
          Connect every selected Gmail / Google Workspace or Custom IMAP
          account. You can add more inboxes later in Settings &gt; Inboxes.
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
          const imapPassword = imapPasswords[inboxId] ?? "";
          const smtpPassword = smtpPasswords[inboxId] ?? "";
          const readyToConnect = isConnectionReady(
            connection,
            imapPassword,
            smtpPassword,
          );
          const isCustomImapAttemptTarget =
            effectiveCustomImapAttemptGuard?.onboardingInboxId ===
            inboxId;
          const isReconciliationRequired =
            isCustomImapAttemptTarget &&
            effectiveCustomImapAttemptGuard?.phase ===
              "reconciliation_required";
          const requiresServerReload =
            isReconciliationRequired &&
            effectiveCustomImapAttemptGuard?.recovery ===
              "reload";
          const isCheckingConnectionStatus =
            isCustomImapAttemptTarget &&
            effectiveCustomImapAttemptGuard?.phase === "checking";
          const isLoading =
            loadingInboxId === inboxId ||
            (isCustomImapAttemptTarget &&
              !isReconciliationRequired);
          const incomingIsConnected =
            isAuthoritativeIncomingConnected(connection);
          const connectionIsConnected =
            isOnboardingInboxFullyConnected(connection);
          const outgoingIsConnected =
            connection.provider === "custom_imap" &&
            connection.smtpConnectionStatus === "connected" &&
            connection.fullyConnected === true;
          const identityIsLocked =
            incomingIsConnected || connectionIsConnected;
          const canRemoveThisInbox =
            Boolean(onRemoveInbox && canRemoveInbox?.(inboxId)) &&
            !isLoading &&
            !customImapInteractionLocked;
          const errorMessage = connectionErrors[inboxId];
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
                connectionIsConnected
                  ? "border-pine/28"
                  : "border-ink/10"
              }`}
            >
              <div className="mb-5 flex items-start justify-between gap-4">
                <div>
                  <h3 className="text-xl font-semibold text-ink">
                    {getInboxLabel(inboxId, index)}
                  </h3>
                  <p className="mt-1 text-sm text-ink/60">
                    {onboardingText.connect.inboxHint}
                  </p>
                </div>
                {canRemoveThisInbox ? (
                  <button
                    type="button"
                    onClick={() => handleRemoveInbox(inboxId)}
                    className="mt-1 shrink-0 text-xs font-medium text-ink/45 underline-offset-4 transition hover:text-ink/70 hover:underline"
                  >
                    Remove inbox
                  </button>
                ) : null}
              </div>

              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {providerOptions.map((provider) => {
                  const selected = connection.provider === provider.id;
                  return (
                    <button
                      key={provider.id}
                      type="button"
                      data-attempt-control={`provider-${inboxId}-${provider.id}`}
                      disabled={
                        isLoading ||
                        customImapInteractionLocked ||
                        identityIsLocked
                      }
                      onClick={() => {
                        if (customImapMutationIsLocked()) {
                          return;
                        }
                        clearConnectionFeedback(inboxId);
                        clearImapPassword(inboxId);
                        clearSmtpPassword(inboxId);
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
                  data-attempt-control={`email-${inboxId}`}
                  value={connection.email}
                  disabled={
                    customImapInteractionLocked || identityIsLocked
                  }
                  onChange={(event) => {
                    if (customImapMutationIsLocked()) {
                      return;
                    }
                    clearConnectionFeedback(inboxId);
                    clearImapPassword(inboxId);
                    clearSmtpPassword(inboxId);
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
                <>
                <div className="mt-6 space-y-4 rounded-[24px] border border-ink/8 bg-sand/20 p-5">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-ink">
                        Incoming mail
                      </p>
                      <p className="mt-1 text-sm text-ink/58">
                        Secure IMAP receiving
                      </p>
                    </div>
                    <span
                      className={`inline-flex rounded-full border px-3 py-1 text-xs font-medium ${
                        incomingIsConnected
                          ? "border-[var(--workspace-status-success-border)] bg-[var(--workspace-status-success-bg)] text-[var(--workspace-status-success-text)]"
                          : "border-ink/10 bg-white/72 text-ink/52"
                      }`}
                    >
                      {incomingIsConnected
                        ? "Incoming connected"
                        : "Incoming not connected"}
                    </span>
                  </div>

                  {connection.provider === "custom_imap" &&
                  reusableSettings &&
                  !incomingIsConnected ? (
                    <div className="flex items-center justify-between gap-4 rounded-2xl border border-moss/10 bg-white/72 px-4 py-3">
                      <p className="text-sm text-ink/70">
                        {onboardingText.connect.reusePreviousServerSettings}
                      </p>
                      <button
                        type="button"
                        data-attempt-control={`reuse-${inboxId}`}
                        disabled={customImapInteractionLocked}
                        onClick={() => {
                          if (customImapMutationIsLocked()) {
                            return;
                          }
                          clearConnectionFeedback(inboxId);
                          clearImapPassword(inboxId);
                          clearSmtpPassword(inboxId);
                          onReuseCustomImap(inboxId, {
                            ...reusableSettings,
                            password: "",
                          });
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
                        data-attempt-control={`host-${inboxId}`}
                        value={connection.customImap.host}
                        disabled={
                          customImapInteractionLocked ||
                          incomingIsConnected
                        }
                        onChange={(event) => {
                          if (customImapMutationIsLocked()) {
                            return;
                          }
                          clearConnectionFeedback(inboxId);
                          clearImapPassword(inboxId);
                          clearSmtpPassword(inboxId);
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
                        data-attempt-control={`port-${inboxId}`}
                        value={connection.customImap.port}
                        disabled={
                          customImapInteractionLocked ||
                          incomingIsConnected
                        }
                        onChange={(event) => {
                          if (customImapMutationIsLocked()) {
                            return;
                          }
                          clearConnectionFeedback(inboxId);
                          clearImapPassword(inboxId);
                          clearSmtpPassword(inboxId);
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
                          data-attempt-control={`username-${inboxId}`}
                          value={connection.customImap.username}
                          disabled={
                            customImapInteractionLocked ||
                            incomingIsConnected
                          }
                          onChange={(event) => {
                            if (customImapMutationIsLocked()) {
                              return;
                            }
                            clearConnectionFeedback(inboxId);
                            clearImapPassword(inboxId);
                            clearSmtpPassword(inboxId);
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
                    {!incomingIsConnected ? (
                      <div>
                        <label className="mb-2 block text-sm font-medium text-ink/75">
                          {getPasswordLabel(connection.provider)}
                        </label>
                        <input
                          type="password"
                          data-attempt-control={`password-${inboxId}`}
                          value={imapPassword}
                          disabled={customImapInteractionLocked}
                          onChange={(event) => {
                            if (customImapMutationIsLocked()) {
                              return;
                            }
                            clearConnectionFeedback(inboxId);
                            setImapPassword(
                              inboxId,
                              event.target.value,
                            );
                          }}
                          autoComplete="new-password"
                          className="w-full rounded-2xl border border-ink/10 bg-white px-4 py-3 outline-none transition focus:border-moss"
                        />
                        <div className="mt-2 min-h-[18px] text-sm text-amber-900/60">
                          {errorMessage?.password ?? ""}
                        </div>
                      </div>
                    ) : (
                      <div className="flex items-end">
                        <p className="w-full rounded-2xl border border-moss/12 bg-white/72 px-4 py-3 text-sm text-ink/58">
                          Incoming credentials stored securely
                        </p>
                      </div>
                    )}
                  </div>

                  <label className="flex items-center gap-3 text-sm font-medium text-ink/75">
                    <span className="relative flex h-4 w-4 items-center justify-center">
                      <input
                        type="checkbox"
                        data-attempt-control={`ssl-${inboxId}`}
                        checked={connection.customImap.ssl}
                        disabled
                        className="peer absolute inset-0 m-0 h-full w-full cursor-not-allowed appearance-none rounded-[5px] border border-ink/18 bg-white/80 outline-none transition checked:border-moss/55 checked:bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))]"
                      />
                      <span className="pointer-events-none absolute inset-0 flex items-center justify-center text-[10px] font-semibold leading-none text-moss opacity-0 transition peer-checked:opacity-100">
                        ✓
                      </span>
                    </span>
                    {onboardingText.connect.ssl} required
                  </label>
                </div>
                <div className="mt-4 space-y-4 rounded-[24px] border border-ink/8 bg-sand/20 p-5">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-ink">
                        Outgoing mail
                      </p>
                      <p className="mt-1 text-sm text-ink/58">
                        Secure SMTP submission
                      </p>
                    </div>
                    <span
                      className={`inline-flex rounded-full border px-3 py-1 text-xs font-medium ${
                        outgoingIsConnected
                          ? "border-[var(--workspace-status-success-border)] bg-[var(--workspace-status-success-bg)] text-[var(--workspace-status-success-text)]"
                          : "border-ink/10 bg-white/72 text-ink/52"
                      }`}
                    >
                      {outgoingIsConnected
                        ? "Outgoing connected"
                        : "Outgoing mail not configured"}
                    </span>
                  </div>

                  {!outgoingIsConnected ? (
                    <div className="grid gap-4 md:grid-cols-2">
                      <div>
                        <label className="mb-2 block text-sm font-medium text-ink/75">
                          SMTP host
                        </label>
                        <input
                          type="text"
                          data-attempt-control={`smtp-host-${inboxId}`}
                          value={connection.customSmtp.host}
                          disabled={customImapInteractionLocked}
                          onChange={(event) => {
                            if (customImapMutationIsLocked()) return;
                            clearConnectionFeedback(inboxId);
                            clearSmtpPassword(inboxId);
                            onCustomSmtpChange(
                              inboxId,
                              "host",
                              event.target.value,
                            );
                          }}
                          className="w-full rounded-2xl border border-ink/10 bg-white px-4 py-3 outline-none transition focus:border-moss"
                        />
                        <div className="mt-2 min-h-[18px] text-sm text-amber-900/60">
                          {errorMessage?.smtpHost ?? ""}
                        </div>
                      </div>
                      <div>
                        <label className="mb-2 block text-sm font-medium text-ink/75">
                          SMTP port
                        </label>
                        <input
                          type="text"
                          data-attempt-control={`smtp-port-${inboxId}`}
                          value={connection.customSmtp.port}
                          disabled={customImapInteractionLocked}
                          onChange={(event) => {
                            if (customImapMutationIsLocked()) return;
                            clearConnectionFeedback(inboxId);
                            clearSmtpPassword(inboxId);
                            onCustomSmtpChange(
                              inboxId,
                              "port",
                              event.target.value,
                            );
                          }}
                          className="w-full rounded-2xl border border-ink/10 bg-white px-4 py-3 outline-none transition focus:border-moss"
                        />
                      </div>
                      <div>
                        <label className="mb-2 block text-sm font-medium text-ink/75">
                          Security
                        </label>
                        <select
                          data-attempt-control={`smtp-security-${inboxId}`}
                          value={connection.customSmtp.security}
                          disabled={customImapInteractionLocked}
                          onChange={(event) => {
                            if (customImapMutationIsLocked()) return;
                            clearConnectionFeedback(inboxId);
                            clearSmtpPassword(inboxId);
                            const security =
                              event.target.value === "ssl"
                                ? "ssl"
                                : "starttls";
                            onCustomSmtpChange(
                              inboxId,
                              "security",
                              security,
                            );
                            onCustomSmtpChange(
                              inboxId,
                              "port",
                              security === "ssl" ? "465" : "587",
                            );
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
                            data-attempt-control={`smtp-same-credentials-${inboxId}`}
                            checked={
                              connection.customSmtp.useSameCredentials
                            }
                            disabled={customImapInteractionLocked}
                            onChange={(event) => {
                              if (customImapMutationIsLocked()) return;
                              clearConnectionFeedback(inboxId);
                              clearSmtpPassword(inboxId);
                              onCustomSmtpChange(
                                inboxId,
                                "useSameCredentials",
                                event.target.checked,
                              );
                              if (event.target.checked) {
                                onCustomSmtpChange(
                                  inboxId,
                                  "username",
                                  "",
                                );
                              }
                            }}
                            className="peer absolute inset-0 m-0 h-full w-full appearance-none rounded-[5px] border border-ink/18 bg-white/80 outline-none transition checked:border-moss/55 checked:bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))]"
                          />
                          <span className="pointer-events-none absolute inset-0 flex items-center justify-center text-[10px] font-semibold leading-none text-moss opacity-0 transition peer-checked:opacity-100">
                            ✓
                          </span>
                        </span>
                        Use same credentials
                      </label>
                      {!connection.customSmtp.useSameCredentials ? (
                        <>
                          <div>
                            <label className="mb-2 block text-sm font-medium text-ink/75">
                              SMTP username
                            </label>
                            <input
                              type="text"
                              data-attempt-control={`smtp-username-${inboxId}`}
                              value={connection.customSmtp.username}
                              disabled={customImapInteractionLocked}
                              onChange={(event) => {
                                if (customImapMutationIsLocked()) return;
                                clearConnectionFeedback(inboxId);
                                clearSmtpPassword(inboxId);
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
                              data-attempt-control={`smtp-password-${inboxId}`}
                              value={smtpPassword}
                              disabled={customImapInteractionLocked}
                              onChange={(event) => {
                                if (customImapMutationIsLocked()) return;
                                clearConnectionFeedback(inboxId);
                                setSmtpPassword(
                                  inboxId,
                                  event.target.value,
                                );
                              }}
                              autoComplete="new-password"
                              className="w-full rounded-2xl border border-ink/10 bg-white px-4 py-3 outline-none transition focus:border-moss"
                            />
                            <div className="mt-2 min-h-[18px] text-sm text-amber-900/60">
                              {errorMessage?.smtpPassword ?? ""}
                            </div>
                          </div>
                        </>
                      ) : null}
                    </div>
                  ) : (
                    <p className="rounded-2xl border border-moss/12 bg-white/72 px-4 py-3 text-sm text-ink/58">
                      Outgoing credentials stored securely
                    </p>
                  )}
                </div>
                </>
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
                  data-attempt-control={`connect-${inboxId}`}
                  onClick={() => {
                    if (isReconciliationRequired) {
                      if (requiresServerReload) {
                        window.location.reload();
                        return;
                      }
                      getAttemptCoordinator().retryReconciliation();
                      return;
                    }
                    void handleConnectInbox(inboxId, connection);
                  }}
                  disabled={
                    connectionIsConnected ||
                    (isReconciliationRequired
                      ? false
                      : !readyToConnect ||
                        isLoading ||
                        customImapInteractionLocked)
                  }
                  className={`rounded-full border px-4 py-2 text-sm font-medium transition ${
                    connectionIsConnected
                      ? "cursor-default border-[var(--workspace-status-success-border)] bg-[var(--workspace-status-success-bg)] text-[var(--workspace-status-success-text)]"
                      : "border-moss/16 bg-white/72 text-moss hover:border-moss/28 hover:bg-white disabled:cursor-not-allowed disabled:border-ink/10 disabled:text-ink/35"
                  }`}
                >
                  {connectionIsConnected
                    ? onboardingText.connect.connected
                    : isReconciliationRequired
                      ? requiresServerReload
                        ? "Reload setup from server"
                        : "Check connection status"
                      : isLoading
                      ? isCheckingConnectionStatus
                        ? "Checking connection status..."
                        : onboardingText.connect.testingConnection
                      : isOAuthConnectionProvider(connection.provider)
                        ? onboardingText.connect.continueWithGoogle
                        : incomingIsConnected
                          ? "Connect outgoing mail"
                          : onboardingText.connect.connectInbox}
                </button>
              </div>

              <div className="mt-3 min-h-[20px] text-sm text-ink/52">
                {isLoading ? (
                  <span>{onboardingText.connect.testingConnection}</span>
                ) : requiresServerReload ? (
                  <span className="inline-flex items-center gap-2 text-amber-900/60">
                    <span aria-hidden="true" className="text-xs">
                      !
                    </span>
                    Setup changed while the connection was pending. Reload
                    setup to reconcile with the server.
                  </span>
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
      <div className="rounded-[26px] border border-dashed border-moss/22 bg-[linear-gradient(180deg,rgba(255,255,255,0.74),rgba(246,248,241,0.68))] px-5 py-4 shadow-[0_12px_32px_rgba(32,28,24,0.045)]">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div className="space-y-1">
            <h3 className="text-[0.98rem] font-semibold tracking-[-0.015em] text-ink">
              More inboxes to include?
            </h3>
            <p className="max-w-xl text-sm leading-6 text-ink/58">
              Connect another inbox now, or add more later in Settings &gt;
              Inboxes.
            </p>
          </div>
          <button
            type="button"
            data-attempt-control="add-inbox"
            disabled={customImapInteractionLocked}
            onClick={() => {
              if (!customImapMutationIsLocked()) {
                onAddInbox();
              }
            }}
            className="inline-flex h-11 shrink-0 items-center justify-center rounded-full border border-moss/22 bg-white/84 px-5 text-sm font-semibold text-moss shadow-[0_10px_24px_rgba(32,28,24,0.06)] transition hover:border-moss/36 hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss/20 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <span className="mr-2 text-base leading-none" aria-hidden="true">
              +
            </span>
            Connect another inbox
          </button>
        </div>
      </div>
    </section>
  );
}
