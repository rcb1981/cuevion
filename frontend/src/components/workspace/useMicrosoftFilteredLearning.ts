import { useCallback, useEffect, useState } from "react";
import { loadStartupSession } from "../../lib/authApi";
import {
  buildLearningStorageKey,
  hydrateScopedSenderCategoryLearning,
  selectScopedSenderCategoryLearning,
} from "../../lib/learningPersistence";
import type { SenderCategoryLearningStore } from "../../lib/learningEngine";
import {
  microsoftFilteredStorageKey,
  parseMicrosoftSenderPlacements,
  setMicrosoftSenderPlacement,
  type MicrosoftSenderPlacement,
  type MicrosoftSenderPlacementStore,
} from "./microsoftVirtualFiltered";

type ScopedMicrosoftLearning = {
  mailboxId: string;
  storageKey: string;
  learningKey: string;
  learned: SenderCategoryLearningStore;
  placements: MicrosoftSenderPlacementStore;
};
const EMPTY_LEARNING: SenderCategoryLearningStore = {};
const EMPTY_PLACEMENTS: MicrosoftSenderPlacementStore = {};

/**
 * Read existing Cuevion sender learning WITHOUT changing Gmail/IMAP.
 * Own Microsoft-specific placement decisions remain isolated to the exact
 * authenticated owner + workspace + mailbox ID. Never trust an email address
 * or locally supplied mailbox ID as proof of authentication.
 */
export function useMicrosoftFilteredLearning(mailboxId: string, connected: boolean) {
  const [scoped, setScoped] = useState<ScopedMicrosoftLearning | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setScoped(null);
    setError(null);
    if (!connected) return () => { active = false; };

    void loadStartupSession().then((session) => {
      if (!active) return;
      if (
        session.status !== "authenticated" ||
        session.authSource !== "auth0" ||
        session.user.userType !== "member"
      ) {
        setError("Microsoft Filtered preferences are unavailable. Inbox is unchanged.");
        return;
      }
      const key = microsoftFilteredStorageKey(
        session.user.workspaceId, session.user.userId, mailboxId,
      );
      const learningKey = buildLearningStorageKey(
        session.user.workspaceId, session.user.userId,
      );
      if (!key || !learningKey) {
        setError("Microsoft Filtered preferences are unavailable. Inbox is unchanged.");
        return;
      }
      const reload = () => {
        if (!active) return;
        try {
          const storage = window.localStorage;
          const learned = selectScopedSenderCategoryLearning(
            hydrateScopedSenderCategoryLearning(storage, learningKey),
            learningKey,
          );
          const placements = parseMicrosoftSenderPlacements(storage.getItem(key));
          setScoped({ mailboxId, storageKey: key, learningKey, learned, placements });
          setError(null);
        } catch {
          setScoped(null);
          setError("Microsoft Filtered preferences are unavailable. Inbox is unchanged.");
        }
      };
      reload();
      const onStorage = (event: StorageEvent) => {
        if (event.key === null || event.key === key || event.key === learningKey) reload();
      };
      const onFocus = () => reload();
      window.addEventListener("storage", onStorage);
      window.addEventListener("focus", onFocus);
      // Replace cleanup after async session fetch, without installing any
      // event listener for a stale mailbox or user identity.
      cleanupListeners = () => {
        window.removeEventListener("storage", onStorage);
        window.removeEventListener("focus", onFocus);
      };
    }).catch(() => {
      if (active) {
        setError("Microsoft Filtered preferences are unavailable. Inbox is unchanged.");
      }
    });

    let cleanupListeners = () => {};
    return () => {
      active = false;
      cleanupListeners();
    };
  }, [mailboxId, connected]);

  const current = connected && scoped?.mailboxId === mailboxId ? scoped : null;
  const savePlacement = useCallback((sender: string, placement: MicrosoftSenderPlacement): boolean => {
    if (!current) return false;
    const next = setMicrosoftSenderPlacement(current.placements, sender, placement);
    if (!next) {
      setError("This sender cannot be added to Microsoft Filtered.");
      return false;
    }
    try {
      window.localStorage.setItem(current.storageKey, JSON.stringify(next));
      setScoped({ ...current, placements: next });
      setError(null);
      return true;
    } catch {
      setError("Microsoft Filtered preference could not be saved.");
      return false;
    }
  }, [current]);

  return {
    ready: Boolean(current),
    learned: current?.learned ?? EMPTY_LEARNING,
    placements: current?.placements ?? EMPTY_PLACEMENTS,
    error,
    savePlacement,
  };
}
