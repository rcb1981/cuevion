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

type UserAccountConfigResponse =
  | {
      ok: true;
      config: UserAccountConfig | null;
    }
  | {
      ok: false;
      config?: null;
      error?: {
        code?: string;
        message?: string;
      };
    };

export async function getUserAccountConfig(): Promise<UserAccountConfigResponse> {
  try {
    const response = await fetch("/api/user/config", {
      method: "GET",
      credentials: "include",
      cache: "no-store",
    });

    const payload = (await response.json()) as UserAccountConfigResponse;
    if (!response.ok || !payload.ok) {
      return {
        ok: false,
        error: payload.ok === false ? payload.error : undefined,
      };
    }

    return {
      ok: true,
      config: payload.config ?? null,
    };
  } catch {
    return {
      ok: false,
      error: {
        code: "user_config_unavailable",
        message: "User config could not be loaded.",
      },
    };
  }
}

export async function saveUserAccountConfig(
  config: UserAccountConfig,
): Promise<UserAccountConfigResponse> {
  try {
    const response = await fetch("/api/user/config", {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ config }),
    });

    const payload = (await response.json()) as UserAccountConfigResponse;
    if (!response.ok || !payload.ok) {
      return {
        ok: false,
        error: payload.ok === false ? payload.error : undefined,
      };
    }

    return {
      ok: true,
      config: payload.config ?? null,
    };
  } catch {
    return {
      ok: false,
      error: {
        code: "user_config_unavailable",
        message: "User config could not be saved.",
      },
    };
  }
}
