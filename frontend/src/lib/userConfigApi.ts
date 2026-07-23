export type UserAccountConfig = {
  v?: number;
  email?: string;
  updatedAt?: string;
  onboardingSession?: unknown;
  managedInboxes?: unknown[];
  mailboxTitleOverrides?: Record<string, unknown>;
  primaryManagedInboxId?: string | null;
  mailboxFocusPreferenceOverrides?: Record<string, unknown>;
  inboxSignatures?: Record<string, unknown>;
  smartFolders?: unknown[];
  uiPreferences?: {
    themeMode?: "Light" | "Dark" | "System" | "light" | "dark";
    aiSuggestionsEnabled?: boolean;
    inboxChangesEnabled?: boolean;
    teamActivityEnabled?: boolean;
  };
  displayNameOverrides?: Record<string, string>;
};

type UserAccountConfigError = {
  code: string;
  message: string;
};

type UserAccountConfigErrorStatus =
  | "unavailable"
  | "conflict"
  | "invalid"
  | "unauthorized"
  | "authentication_unavailable"
  | "malformed_response"
  | "network_error";

export type UserAccountConfigReadResult =
  | { status: "found"; config: UserAccountConfig }
  | { status: "missing"; config: null }
  | { status: UserAccountConfigErrorStatus; error: UserAccountConfigError };

export type UserAccountConfigSaveResult = Exclude<
  UserAccountConfigReadResult,
  { status: "missing" }
>;

type UserConfigOperation = "loaded" | "saved";

const VALID_THEME_MODES = new Set(["Light", "Dark", "System", "light", "dark"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isOptionalRecord(value: unknown): boolean {
  return value === undefined || isRecord(value);
}

function isUiPreferences(value: unknown): boolean {
  if (value === undefined) return true;
  if (!isRecord(value)) return false;

  return (
    (value.themeMode === undefined ||
      (typeof value.themeMode === "string" && VALID_THEME_MODES.has(value.themeMode))) &&
    (value.aiSuggestionsEnabled === undefined ||
      typeof value.aiSuggestionsEnabled === "boolean") &&
    (value.inboxChangesEnabled === undefined ||
      typeof value.inboxChangesEnabled === "boolean") &&
    (value.teamActivityEnabled === undefined ||
      typeof value.teamActivityEnabled === "boolean")
  );
}

function isUserAccountConfig(value: unknown): value is UserAccountConfig {
  if (!isRecord(value)) return false;

  return (
    (value.v === undefined ||
      (typeof value.v === "number" && Number.isFinite(value.v))) &&
    (value.email === undefined || typeof value.email === "string") &&
    (value.updatedAt === undefined || typeof value.updatedAt === "string") &&
    (value.onboardingSession === undefined || isRecord(value.onboardingSession)) &&
    (value.managedInboxes === undefined ||
      (Array.isArray(value.managedInboxes) && value.managedInboxes.every(isRecord))) &&
    isOptionalRecord(value.mailboxTitleOverrides) &&
    (value.primaryManagedInboxId === undefined ||
      value.primaryManagedInboxId === null ||
      typeof value.primaryManagedInboxId === "string") &&
    isOptionalRecord(value.mailboxFocusPreferenceOverrides) &&
    isOptionalRecord(value.inboxSignatures) &&
    (value.smartFolders === undefined || Array.isArray(value.smartFolders)) &&
    isUiPreferences(value.uiPreferences) &&
    (value.displayNameOverrides === undefined ||
      (isRecord(value.displayNameOverrides) &&
        Object.values(value.displayNameOverrides).every(
          (entry) => typeof entry === "string",
        )))
  );
}

type UserAccountConfigHydrationEchoExpectation = {
  accountKey: string;
  serializedBody: string;
};

type PendingUserAccountConfigPost = {
  accountKey: string | null;
  cancelled: boolean;
};

let hydrationEchoExpectation: UserAccountConfigHydrationEchoExpectation | null = null;
let activeUserAccountConfigAccountKey: string | null = null;
let userAccountConfigPostTail: Promise<void> = Promise.resolve();
const pendingUserAccountConfigPosts = new Set<PendingUserAccountConfigPost>();

function serializeUserAccountConfig(config: UserAccountConfig): {
  serializedBody: string;
  serializedConfig: UserAccountConfig;
} | null {
  if (!isUserAccountConfig(config)) {
    return null;
  }

  try {
    const serializedBody = JSON.stringify({ config });
    const payload = JSON.parse(serializedBody) as unknown;
    if (!isRecord(payload) || !isUserAccountConfig(payload.config)) {
      return null;
    }
    return { serializedBody, serializedConfig: payload.config };
  } catch {
    return null;
  }
}

export function projectWorkspaceUserAccountConfigForSave(
  config: UserAccountConfig,
): UserAccountConfig {
  const {
    v: _schemaVersion,
    email: _ownerEmail,
    updatedAt: _updatedAt,
    onboardingSession: _onboardingSession,
    ...clientWritableConfig
  } = config;
  return clientWritableConfig;
}

export function setUserAccountConfigHydrationEchoExpectation(
  accountKey: string | null,
  config: UserAccountConfig | null,
): void {
  if (accountKey !== activeUserAccountConfigAccountKey) {
    pendingUserAccountConfigPosts.forEach((post) => {
      post.cancelled = true;
    });
  }
  activeUserAccountConfigAccountKey = accountKey;
  hydrationEchoExpectation = null;
  if (accountKey === null || config === null) {
    return;
  }

  const serialized = serializeUserAccountConfig(config);
  if (serialized) {
    hydrationEchoExpectation = {
      accountKey,
      serializedBody: serialized.serializedBody,
    };
  }
}

function accountChangedBeforeSave(): UserAccountConfigSaveResult {
  return {
    status: "unavailable",
    error: {
      code: "account_changed",
      message: "User config was not saved because the active account changed.",
    },
  };
}

function enqueueUserAccountConfigPost(
  accountKey: string | null,
  operation: () => Promise<UserAccountConfigSaveResult>,
): Promise<UserAccountConfigSaveResult> {
  const pendingPost: PendingUserAccountConfigPost = {
    accountKey,
    cancelled: false,
  };
  pendingUserAccountConfigPosts.add(pendingPost);
  const result = userAccountConfigPostTail.then(() => {
    pendingUserAccountConfigPosts.delete(pendingPost);
    if (
      pendingPost.cancelled ||
      pendingPost.accountKey !== activeUserAccountConfigAccountKey
    ) {
      return accountChangedBeforeSave();
    }
    return operation();
  });
  userAccountConfigPostTail = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}

function malformedResponse(
  operation: UserConfigOperation,
): UserAccountConfigSaveResult {
  return {
    status: "malformed_response",
    error: {
      code: "malformed_response",
      message: `User config could not be ${operation} because the server response was malformed.`,
    },
  };
}

function networkError(operation: UserConfigOperation): UserAccountConfigSaveResult {
  return {
    status: "network_error",
    error: {
      code: "network_error",
      message: `User config could not be ${operation} because the network request failed.`,
    },
  };
}

function parseServerError(payload: unknown): UserAccountConfigError | null {
  if (
    !isRecord(payload) ||
    payload.ok !== false ||
    !isRecord(payload.error) ||
    typeof payload.error.code !== "string" ||
    typeof payload.error.message !== "string"
  ) {
    return null;
  }

  return {
    code: payload.error.code,
    message: payload.error.message,
  };
}

function classifyHttpError(
  status: number,
  error: UserAccountConfigError,
): UserAccountConfigSaveResult {
  if (status === 401 || status === 403 || error.code === "unauthorized") {
    return { status: "unauthorized", error };
  }

  if (
    error.code === "authentication_unavailable" ||
    error.code === "session_auth_unavailable"
  ) {
    return { status: "authentication_unavailable", error };
  }

  if (
    error.code === "config_unavailable" ||
    error.code === "user_config_unavailable"
  ) {
    return { status: "unavailable", error };
  }

  if (status === 409 && error.code === "user_config_write_conflict") {
    return { status: "conflict", error };
  }

  if (
    error.code === "config_invalid" ||
    status === 400 ||
    status === 409 ||
    status === 422
  ) {
    return { status: "invalid", error };
  }

  return { status: "unavailable", error };
}

function parseLoadSuccess(payload: unknown): UserAccountConfigReadResult {
  if (!isRecord(payload) || payload.ok !== true) {
    return malformedResponse("loaded");
  }

  if (payload.configState === "found" && isUserAccountConfig(payload.config)) {
    return { status: "found", config: payload.config };
  }

  if (payload.configState === "missing" && payload.config === null) {
    return { status: "missing", config: null };
  }

  return malformedResponse("loaded");
}

function parseSaveSuccess(payload: unknown): UserAccountConfigSaveResult {
  return isRecord(payload) && payload.ok === true && isUserAccountConfig(payload.config)
    ? { status: "found", config: payload.config }
    : malformedResponse("saved");
}

async function requestUserAccountConfig(
  method: "GET",
  serializedBody?: undefined,
  signal?: AbortSignal,
): Promise<UserAccountConfigReadResult>;
async function requestUserAccountConfig(
  method: "POST",
  serializedBody: string,
  signal?: undefined,
): Promise<UserAccountConfigSaveResult>;
async function requestUserAccountConfig(
  method: "GET" | "POST",
  serializedBody?: string,
  signal?: AbortSignal,
): Promise<UserAccountConfigReadResult> {
  const operation: UserConfigOperation = method === "GET" ? "loaded" : "saved";
  const request: RequestInit = {
    method,
    credentials: "include",
    ...(method === "GET" ? { cache: "no-store" as const } : {}),
    ...(signal ? { signal } : {}),
  };

  if (method === "POST") {
    request.headers = { "Content-Type": "application/json" };
    request.body = serializedBody;
  }

  let response: Response;
  try {
    response = await fetch("/api/user/config", request);
  } catch {
    return networkError(operation);
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return malformedResponse(operation);
  }

  if (response.status !== 200) {
    const serverError = parseServerError(payload);
    return serverError
      ? classifyHttpError(response.status, serverError)
      : malformedResponse(operation);
  }

  return method === "GET" ? parseLoadSuccess(payload) : parseSaveSuccess(payload);
}

export function loadUserAccountConfig(
  signal?: AbortSignal,
): Promise<UserAccountConfigReadResult> {
  return requestUserAccountConfig("GET", undefined, signal);
}

export function createUserAccountConfigConflictRetryQueue({
  save = saveUserAccountConfig,
  scheduleRetry = (callback, delayMs) => window.setTimeout(callback, delayMs),
  cancelRetry = (handle) => window.clearTimeout(handle),
}: {
  save?: (config: UserAccountConfig) => Promise<UserAccountConfigSaveResult>;
  scheduleRetry?: (callback: () => void, delayMs: number) => number;
  cancelRetry?: (handle: number) => void;
} = {}) {
  const conflictRetryDelaysMs = [120, 320] as const;
  let generation = 0;
  let operationTail: Promise<void> = Promise.resolve();
  let retryHandle: number | null = null;
  let dirty = false;

  const clearRetry = () => {
    if (retryHandle === null) {
      return;
    }
    cancelRetry(retryHandle);
    retryHandle = null;
  };

  const queueAttempt = (
    config: UserAccountConfig,
    requestGeneration: number,
    conflictRetryCount: number,
  ) => {
    operationTail = operationTail.then(async () => {
      if (requestGeneration !== generation) {
        return;
      }

      let result: UserAccountConfigSaveResult;
      try {
        result = await save(config);
      } catch {
        return;
      }
      if (requestGeneration !== generation) {
        return;
      }
      if (result.status === "found") {
        dirty = false;
        return;
      }

      dirty = true;
      const delayMs = conflictRetryDelaysMs[conflictRetryCount];
      if (
        result.status !== "conflict" ||
        result.error.code !== "user_config_write_conflict" ||
        delayMs === undefined
      ) {
        return;
      }

      clearRetry();
      const handle = scheduleRetry(() => {
        if (
          retryHandle !== handle ||
          requestGeneration !== generation
        ) {
          return;
        }
        retryHandle = null;
        queueAttempt(config, requestGeneration, conflictRetryCount + 1);
      }, delayMs);
      retryHandle = handle;
    });
  };

  return {
    enqueue(config: UserAccountConfig) {
      generation += 1;
      clearRetry();
      dirty = true;
      queueAttempt(config, generation, 0);
    },
    supersede() {
      generation += 1;
      clearRetry();
    },
    cancel() {
      generation += 1;
      clearRetry();
      dirty = false;
    },
    isDirty() {
      return dirty;
    },
  };
}

export function saveUserAccountConfig(
  config: UserAccountConfig,
): Promise<UserAccountConfigSaveResult> {
  const serialized = serializeUserAccountConfig(config);
  if (!serialized) {
    return Promise.resolve({
      status: "invalid",
      error: {
        code: "invalid_user_config",
        message: "User config could not be saved because it was invalid.",
      },
    });
  }

  const expectedEcho = hydrationEchoExpectation;
  hydrationEchoExpectation = null;
  if (
    expectedEcho?.accountKey === activeUserAccountConfigAccountKey &&
    expectedEcho.serializedBody === serialized.serializedBody
  ) {
    return Promise.resolve({
      status: "found",
      config: serialized.serializedConfig,
    });
  }

  return enqueueUserAccountConfigPost(activeUserAccountConfigAccountKey, () =>
    requestUserAccountConfig("POST", serialized.serializedBody),
  );
}
