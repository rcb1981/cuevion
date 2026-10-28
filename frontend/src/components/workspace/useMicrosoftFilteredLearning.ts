import { useCallback, useEffect, useRef, useState } from "react";
import {
  microsoftLearningRulesStorageKey,
  parseMicrosoftLearningRules,
  saveMicrosoftLearningRule,
  type MicrosoftLearningRule,
  type MicrosoftLearningRuleStore,
} from "./microsoftLearningRules";
import { loadStartupSession } from "../../lib/authApi";
import { requestMicrosoftFilteredLearning } from "../../lib/microsoftFilteredLearningApi";
import {
  buildLearningStorageKey,
  hydrateScopedSenderCategoryLearning,
  selectScopedSenderCategoryLearning,
} from "../../lib/learningPersistence";
import { normalizeSenderLearningKey, type SenderCategoryLearningStore } from "../../lib/learningEngine";
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
  generation: number;
  learned: SenderCategoryLearningStore;
  placements: MicrosoftSenderPlacementStore;
  rules: MicrosoftLearningRuleStore;
};
const EMPTY_LEARNING: SenderCategoryLearningStore = {};
const EMPTY_PLACEMENTS: MicrosoftSenderPlacementStore = {};
const EMPTY_RULES: MicrosoftLearningRuleStore = {};

/**
 * Read existing Cuevion sender learning WITHOUT changing Gmail/IMAP.
 * Own Microsoft-specific placement decisions remain isolated to the exact
 * authenticated owner + workspace + mailbox ID. Never trust an email address
 * or locally supplied mailbox ID as proof of authentication.
 */
export function useMicrosoftFilteredLearning(mailboxId: string, connected: boolean) {
  const [scoped, setScoped] = useState<ScopedMicrosoftLearning | null>(null);
  const [error, setError] = useState<string | null>(null);
  const activeGeneration = useRef(0);
  const writeTail = useRef<Promise<void>>(Promise.resolve());

  useEffect(() => {
    let active = true;
    const generation = ++activeGeneration.current;
    writeTail.current = Promise.resolve();
    setScoped(null);
    setError(null);
    if (!connected) return () => { active = false; };

    void loadStartupSession().then(async (session) => {
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
      const reload = async () => {
        if (!active || activeGeneration.current !== generation) return;
        try {
          const storage = window.localStorage;
          const learned = selectScopedSenderCategoryLearning(
            hydrateScopedSenderCategoryLearning(storage, learningKey),
            learningKey,
          );
          let remote = await requestMicrosoftFilteredLearning({ mailboxId, action: "read" });
          if (!active || activeGeneration.current !== generation) return;
          if (!remote) throw new Error("Microsoft Learning storage unavailable");
          if (!remote.exists) {
            // One-time import of earlier browser-only decisions. CAS ensures a
            // second browser never overwrites data already saved on the server.
            const localPlacements = parseMicrosoftSenderPlacements(storage.getItem(key));
            const localRules = parseMicrosoftLearningRules(storage.getItem(microsoftLearningRulesStorageKey(key) ?? ""));
            remote = await requestMicrosoftFilteredLearning({
              mailboxId, action: "bootstrap", placements: localPlacements, rules: localRules,
            });
            if (!active || activeGeneration.current !== generation) return;
            if (!remote) throw new Error("Microsoft Learning import unavailable");
          }
          setScoped({ mailboxId, storageKey: key, learningKey, generation,
            learned, placements: remote.placements, rules: remote.rules });
          setError(null);
        } catch {
          setScoped(null);
          setError("Microsoft Learning could not load. Inbox is unchanged; saved choices were not modified.");
        }
      };
      void reload();
      const onStorage = (event: StorageEvent) => {
        if (event.key === null || event.key === key || event.key === learningKey ||
            event.key === microsoftLearningRulesStorageKey(key)) void reload();
      };
      const onFocus = () => { void writeTail.current.then(reload); };
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
    const generation = current.generation;
    setScoped({ ...current, placements: next });
    setError(null);
    writeTail.current = writeTail.current.then(async () => {
      const saved = await requestMicrosoftFilteredLearning({
        mailboxId, action: "setPlacement", sender: normalizeSenderLearningKey(sender), placement,
      });
      if (activeGeneration.current !== generation) return;
      if (!saved) {
        setError("Microsoft Filtered preference could not be saved. Please try again.");
        return;
      }
      try { window.localStorage.setItem(current.storageKey, JSON.stringify(saved.placements)); } catch { /* optional migration mirror */ }
    });
    return true;
  }, [current]);

  const saveRule = useCallback((sender: string, rule: MicrosoftLearningRule): boolean => {
    if (!current) return false;
    const next = saveMicrosoftLearningRule(current.rules, sender, rule);
    const key = microsoftLearningRulesStorageKey(current.storageKey);
    if (!next || !key) {
      setError("This sender cannot be saved to Microsoft Learning.");
      return false;
    }
    const generation = current.generation;
    setScoped({ ...current, rules: next });
    setError(null);
    writeTail.current = writeTail.current.then(async () => {
      const saved = await requestMicrosoftFilteredLearning({
        mailboxId, action: "setRule", sender: normalizeSenderLearningKey(sender), rule,
      });
      if (activeGeneration.current !== generation) return;
      if (!saved) {
        setError("The Microsoft Learning rule could not be saved. Please try again.");
        return;
      }
      try { window.localStorage.setItem(key, JSON.stringify(saved.rules)); } catch { /* optional migration mirror */ }
    });
    return true;
  }, [current]);

  return {
    rules: current?.rules ?? EMPTY_RULES,
    saveRule,
    ready: Boolean(current),
    learned: current?.learned ?? EMPTY_LEARNING,
    placements: current?.placements ?? EMPTY_PLACEMENTS,
    error,
    savePlacement,
  };
}
