export const FULL_MESSAGE_MODAL_VIEWPORT = {
  height: "88dvh",
  maxHeight: "calc(100dvh - 2rem)",
  maxWidth: "calc(100vw - 2rem)",
  width: "88vw",
} as const;

export type FullMessageModalInteractionState = {
  isOpen: boolean;
  selectedMessageId: string | null;
};

type FullMessageModalInteraction =
  | { type: "single-click"; messageId: string }
  | { type: "double-click"; messageId: string }
  | { type: "context-menu" }
  | { type: "escape" }
  | { type: "close" };

export function reduceFullMessageModalInteraction(
  state: FullMessageModalInteractionState,
  interaction: FullMessageModalInteraction,
): FullMessageModalInteractionState {
  switch (interaction.type) {
    case "single-click":
      return {
        isOpen: false,
        selectedMessageId: interaction.messageId,
      };
    case "double-click":
      return {
        isOpen: true,
        selectedMessageId: interaction.messageId,
      };
    case "context-menu":
    case "escape":
    case "close":
      return {
        isOpen: false,
        selectedMessageId: state.selectedMessageId,
      };
  }
}

export function resolveFullMessageModalMessageId(
  state: FullMessageModalInteractionState,
  resolvedMessageId: string | null,
): string | null {
  if (
    !state.isOpen ||
    state.selectedMessageId === null ||
    resolvedMessageId === null
  ) {
    return null;
  }

  return resolvedMessageId;
}
