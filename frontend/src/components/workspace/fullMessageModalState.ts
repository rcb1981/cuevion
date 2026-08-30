export const FULL_MESSAGE_MODAL_VIEWPORT = {
  height: "66dvh",
  maxHeight: "calc(100dvh - 2rem)",
  maxWidth: "min(1040px, calc(100vw - 2rem))",
  width: "60vw",
} as const;

export const COMPOSE_MODAL_VIEWPORT = {
  height: "76dvh",
  maxHeight: "calc(100dvh - 2rem)",
  maxWidth: "min(1180px, calc(100vw - 2rem))",
  width: "min(840px, calc(100vw - 2rem))",
} as const;

const FULL_MESSAGE_MODAL_DEFAULT_WIDTH_RATIO = 0.6;
const FULL_MESSAGE_MODAL_DEFAULT_HEIGHT_RATIO = 0.66;
const FULL_MESSAGE_MODAL_DEFAULT_MAX_WIDTH = 1040;
const FULL_MESSAGE_MODAL_MIN_WIDTH = 720;
const FULL_MESSAGE_MODAL_MIN_HEIGHT = 480;
const COMPOSE_MODAL_DEFAULT_WIDTH_RATIO = 1;
const COMPOSE_MODAL_DEFAULT_HEIGHT_RATIO = 0.76;
const COMPOSE_MODAL_DEFAULT_MAX_WIDTH = 840;
const COMPOSE_MODAL_MIN_WIDTH = 760;
const COMPOSE_MODAL_MIN_HEIGHT = 520;
const MODAL_VIEWPORT_MARGIN = 16;

export type FullMessageModalSize = {
  width: number;
  height: number;
};

type ModalSizeContract = {
  defaultHeightRatio: number;
  defaultMaxWidth: number;
  defaultWidthRatio: number;
  minimumHeight: number;
  minimumWidth: number;
};

const fullMessageModalSizeContract: ModalSizeContract = {
  defaultHeightRatio: FULL_MESSAGE_MODAL_DEFAULT_HEIGHT_RATIO,
  defaultMaxWidth: FULL_MESSAGE_MODAL_DEFAULT_MAX_WIDTH,
  defaultWidthRatio: FULL_MESSAGE_MODAL_DEFAULT_WIDTH_RATIO,
  minimumHeight: FULL_MESSAGE_MODAL_MIN_HEIGHT,
  minimumWidth: FULL_MESSAGE_MODAL_MIN_WIDTH,
};

const composeModalSizeContract: ModalSizeContract = {
  defaultHeightRatio: COMPOSE_MODAL_DEFAULT_HEIGHT_RATIO,
  defaultMaxWidth: COMPOSE_MODAL_DEFAULT_MAX_WIDTH,
  defaultWidthRatio: COMPOSE_MODAL_DEFAULT_WIDTH_RATIO,
  minimumHeight: COMPOSE_MODAL_MIN_HEIGHT,
  minimumWidth: COMPOSE_MODAL_MIN_WIDTH,
};

type MessageComposeMode = "reply" | "reply_all" | "forward";
type MessageComposePresentation = "workspace" | "modal";

export function planMessageComposeAction(
  sourceMessageId: string,
  mode: MessageComposeMode,
  originPresentation: MessageComposePresentation = "workspace",
) {
  const hasFullMessageOrigin = originPresentation === "modal";

  return {
    composePresentation: "modal" as const,
    isComposeOpen: true,
    isFullMessageOpen: false,
    mode,
    sourceMessageId: hasFullMessageOrigin ? sourceMessageId : null,
  };
}

export function planFullMessageModalComposeAction(
  sourceMessageId: string,
  mode: MessageComposeMode,
) {
  return planMessageComposeAction(sourceMessageId, mode, "modal");
}

export function resolveModalComposeReturnMessageId(
  sourceMessageId: string | null,
  availableMessageIds: readonly string[],
): string | null {
  return sourceMessageId !== null && availableMessageIds.includes(sourceMessageId)
    ? sourceMessageId
    : null;
}

function clampModalSize(
  size: FullMessageModalSize,
  viewport: FullMessageModalSize,
  contract: ModalSizeContract,
): FullMessageModalSize {
  const availableWidth = Math.max(
    0,
    viewport.width - MODAL_VIEWPORT_MARGIN * 2,
  );
  const availableHeight = Math.max(
    0,
    viewport.height - MODAL_VIEWPORT_MARGIN * 2,
  );
  const minimumWidth = Math.min(contract.minimumWidth, availableWidth);
  const minimumHeight = Math.min(contract.minimumHeight, availableHeight);

  return {
    width: Math.min(availableWidth, Math.max(minimumWidth, size.width)),
    height: Math.min(availableHeight, Math.max(minimumHeight, size.height)),
  };
}

function createDefaultModalSize(
  viewport: FullMessageModalSize,
  contract: ModalSizeContract,
): FullMessageModalSize {
  return clampModalSize(
    {
      width: Math.min(
        contract.defaultMaxWidth,
        viewport.width * contract.defaultWidthRatio,
      ),
      height: viewport.height * contract.defaultHeightRatio,
    },
    viewport,
    contract,
  );
}

function resizeModalSize(
  startSize: FullMessageModalSize,
  delta: FullMessageModalSize,
  viewport: FullMessageModalSize,
  contract: ModalSizeContract,
): FullMessageModalSize {
  return clampModalSize(
    {
      width: startSize.width + delta.width,
      height: startSize.height + delta.height,
    },
    viewport,
    contract,
  );
}

export function clampFullMessageModalSize(
  size: FullMessageModalSize,
  viewport: FullMessageModalSize,
): FullMessageModalSize {
  return clampModalSize(size, viewport, fullMessageModalSizeContract);
}

export function createDefaultFullMessageModalSize(
  viewport: FullMessageModalSize,
): FullMessageModalSize {
  return createDefaultModalSize(viewport, fullMessageModalSizeContract);
}

export function resizeFullMessageModalSize(
  startSize: FullMessageModalSize,
  delta: FullMessageModalSize,
  viewport: FullMessageModalSize,
): FullMessageModalSize {
  return resizeModalSize(
    startSize,
    delta,
    viewport,
    fullMessageModalSizeContract,
  );
}

export function clampComposeModalSize(
  size: FullMessageModalSize,
  viewport: FullMessageModalSize,
): FullMessageModalSize {
  return clampModalSize(size, viewport, composeModalSizeContract);
}

export function createDefaultComposeModalSize(
  viewport: FullMessageModalSize,
): FullMessageModalSize {
  return createDefaultModalSize(viewport, composeModalSizeContract);
}

export function resizeComposeModalSize(
  startSize: FullMessageModalSize,
  delta: FullMessageModalSize,
  viewport: FullMessageModalSize,
): FullMessageModalSize {
  return resizeModalSize(startSize, delta, viewport, composeModalSizeContract);
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
