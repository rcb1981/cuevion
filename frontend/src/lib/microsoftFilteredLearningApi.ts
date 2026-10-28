import {
  parseMicrosoftLearningRules,
  type MicrosoftLearningRule,
  type MicrosoftLearningRuleStore,
} from "../components/workspace/microsoftLearningRules";
import {
  parseMicrosoftSenderPlacements,
  type MicrosoftSenderPlacement,
  type MicrosoftSenderPlacementStore,
} from "../components/workspace/microsoftVirtualFiltered";

export type MicrosoftFilteredLearningState = {
  exists: boolean;
  placements: MicrosoftSenderPlacementStore;
  rules: MicrosoftLearningRuleStore;
};

type LearningRequest =
  | { mailboxId: string; action: "read" }
  | { mailboxId: string; action: "bootstrap"; placements: MicrosoftSenderPlacementStore; rules: MicrosoftLearningRuleStore }
  | { mailboxId: string; action: "setPlacement"; sender: string; placement: MicrosoftSenderPlacement }
  | { mailboxId: string; action: "setRule"; sender: string; rule: MicrosoftLearningRule };

/** No localStorage fallback: the server's authenticated CAS record is authority. */
export async function requestMicrosoftFilteredLearning(
  payload: LearningRequest,
): Promise<MicrosoftFilteredLearningState | null> {
  if (!/^[A-Za-z0-9][A-Za-z0-9:_-]{0,159}$/.test(payload.mailboxId)) return null;
  try {
    const response = await fetch("/api/inboxes/microsoft-filtered-learning", {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) return null;
    const result: unknown = await response.json();
    if (!result || typeof result !== "object" || Array.isArray(result)) return null;
    const value = result as Record<string, unknown>;
    if (value.ok !== true || typeof value.exists !== "boolean" ||
        !value.placements || typeof value.placements !== "object" || Array.isArray(value.placements) ||
        !value.rules || typeof value.rules !== "object" || Array.isArray(value.rules)) return null;
    const placements = parseMicrosoftSenderPlacements(JSON.stringify(value.placements));
    const rules = parseMicrosoftLearningRules(JSON.stringify(value.rules));
    // Reject invalid or truncated server state rather than treating it as an
    // empty response that might overwrite the owner's existing decisions.
    if (Object.keys(placements).length !== Object.keys(value.placements).length ||
        Object.keys(rules).length !== Object.keys(value.rules).length) return null;
    return { exists: value.exists, placements, rules };
  } catch {
    return null;
  }
}
