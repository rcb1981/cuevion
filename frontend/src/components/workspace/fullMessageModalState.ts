export const FULL_MESSAGE_MODAL_VIEWPORT = {
  height: "66dvh",
  maxHeight: "calc(100dvh - 2rem)",
  maxWidth: "min(1040px, calc(100vw - 2rem))",
  width: "60vw",
} as const;

const FULL_MESSAGE_MODAL_DEFAULT_WIDTH_RATIO = 0.6;
const FULL_MESSAGE_MODAL_DEFAULT_HEIGHT_RATIO = 0.66;
const FULL_MESSAGE_MODAL_DEFAULT_MAX_WIDTH = 1040;
const FULL_MESSAGE_MODAL_MIN_WIDTH = 720;
const FULL_MESSAGE_MODAL_MIN_HEIGHT = 480;
const FULL_MESSAGE_MODAL_VIEWPORT_MARGIN = 16;

export type FullMessageModalSize = {
  width: number;
  height: number;
};

type FullMessageModalComposeMode = "reply" | "reply_all" | "forward";

export function planFullMessageModalComposeAction(
  sourceMessageId: string,
  mode: FullMessageModalComposeMode,
) {
  return {
    composePresentation: "modal" as const,
    isComposeOpen: true,
    isFullMessageOpen: false,
    mode,
    sourceMessageId,
  };
}

export function resolveModalComposeReturnMessageId(
  sourceMessageId: string | null,
  availableMessageIds: readonly string[],
): string | null {
  return sourceMessageId !== null && availableMessageIds.includes(sourceMessageId)
    ? sourceMessageId
    : null;
}

export function clampFullMessageModalSize(
  size: FullMessageModalSize,
  viewport: FullMessageModalSize,
): FullMessageModalSize {
  const availableWidth = Math.max(
    0,
    viewport.width - FULL_MESSAGE_MODAL_VIEWPORT_MARGIN * 2,
  );
  const availableHeight = Math.max(
    0,
    viewport.height - FULL_MESSAGE_MODAL_VIEWPORT_MARGIN * 2,
  );
  const minimumWidth = Math.min(FULL_MESSAGE_MODAL_MIN_WIDTH, availableWidth);
  const minimumHeight = Math.min(FULL_MESSAGE_MODAL_MIN_HEIGHT, availableHeight);

  return {
    width: Math.min(availableWidth, Math.max(minimumWidth, size.width)),
    height: Math.min(availableHeight, Math.max(minimumHeight, size.height)),
  };
}

export function createDefaultFullMessageModalSize(
  viewport: FullMessageModalSize,
): FullMessageModalSize {
  return clampFullMessageModalSize(
    {
      width: Math.min(
        FULL_MESSAGE_MODAL_DEFAULT_MAX_WIDTH,
        viewport.width * FULL_MESSAGE_MODAL_DEFAULT_WIDTH_RATIO,
      ),
      height: viewport.height * FULL_MESSAGE_MODAL_DEFAULT_HEIGHT_RATIO,
    },
    viewport,
  );
}

export function resizeFullMessageModalSize(
  startSize: FullMessageModalSize,
  delta: FullMessageModalSize,
  viewport: FullMessageModalSize,
): FullMessageModalSize {
  return clampFullMessageModalSize(
    {
      width: startSize.width + delta.width,
      height: startSize.height + delta.height,
    },
    viewport,
  );
}

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
