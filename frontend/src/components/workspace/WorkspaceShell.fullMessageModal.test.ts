import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  COMPOSE_MODAL_VIEWPORT,
  FULL_MESSAGE_MODAL_VIEWPORT,
  clampFullMessageModalSize,
  createDefaultComposeModalSize,
  createDefaultFullMessageModalSize,
  planFullMessageModalComposeAction,
  planMessageComposeAction,
  reduceFullMessageModalInteraction,
  resizeComposeModalSize,
  resizeFullMessageModalSize,
  resolveModalComposeReturnMessageId,
  resolveFullMessageModalMessageId,
  type FullMessageModalInteractionState,
} from "./fullMessageModalState";

const initialState: FullMessageModalInteractionState = {
  isOpen: false,
  selectedMessageId: "message-a",
};

{
  const expectedReadingReplyPlan = {
    composePresentation: "modal",
    isComposeOpen: true,
    isFullMessageOpen: false,
    mode: "reply",
    sourceMessageId: null,
  };

  assert.deepEqual(
    planMessageComposeAction("message-a", "reply", "workspace"),
    expectedReadingReplyPlan,
    "reading-pane Reply must promote workspace origin to modal compose",
  );
  assert.deepEqual(
    planMessageComposeAction("message-a", "reply"),
    expectedReadingReplyPlan,
    "toolbar Reply must promote its default workspace origin to modal compose",
  );
  assert.deepEqual(
    planMessageComposeAction("message-a", "reply_all", "workspace"),
    {
      ...expectedReadingReplyPlan,
      mode: "reply_all",
    },
    "reading-pane and toolbar Reply All must promote workspace origin to modal compose",
  );
  assert.deepEqual(
    planMessageComposeAction("message-a", "reply", "modal"),
    {
      ...expectedReadingReplyPlan,
      sourceMessageId: "message-a",
    },
    "full-message Reply must retain its return target while opening modal compose",
  );
  assert.deepEqual(
    planMessageComposeAction("message-a", "reply_all", "modal"),
    {
      ...expectedReadingReplyPlan,
      mode: "reply_all",
      sourceMessageId: "message-a",
    },
    "full-message Reply All must retain its return target while opening modal compose",
  );
  assert.deepEqual(
    planMessageComposeAction("message-a", "forward", "workspace"),
    {
      composePresentation: "workspace",
      isComposeOpen: true,
      isFullMessageOpen: false,
      mode: "forward",
      sourceMessageId: null,
    },
    "reading-pane and toolbar Forward presentation must remain unchanged",
  );
  assert.deepEqual(
    planMessageComposeAction("message-a", "forward", "modal"),
    {
      composePresentation: "modal",
      isComposeOpen: true,
      isFullMessageOpen: false,
      mode: "forward",
      sourceMessageId: "message-a",
    },
    "full-message Forward presentation must remain unchanged",
  );
}

{
  const state = reduceFullMessageModalInteraction(initialState, {
    type: "single-click",
    messageId: "message-b",
  });

  assert.deepEqual(state, {
    isOpen: false,
    selectedMessageId: "message-b",
  });
  assert.equal(resolveFullMessageModalMessageId(state, "message-b"), null);
}

{
  const state = reduceFullMessageModalInteraction(initialState, {
    type: "double-click",
    messageId: "message-b",
  });

  assert.deepEqual(state, {
    isOpen: true,
    selectedMessageId: "message-b",
  });
  assert.equal(
    resolveFullMessageModalMessageId(state, "message-b"),
    "message-b",
  );
  assert.equal(
    resolveFullMessageModalMessageId(state, null),
    null,
    "a removed message must not leave a stale modal target",
  );
}

{
  const openState: FullMessageModalInteractionState = {
    isOpen: true,
    selectedMessageId: "message-b",
  };
  const state = reduceFullMessageModalInteraction(openState, { type: "escape" });

  assert.deepEqual(state, {
    isOpen: false,
    selectedMessageId: "message-b",
  });
}

{
  const openState: FullMessageModalInteractionState = {
    isOpen: true,
    selectedMessageId: "message-b",
  };
  const state = reduceFullMessageModalInteraction(openState, { type: "close" });

  assert.deepEqual(state, {
    isOpen: false,
    selectedMessageId: "message-b",
  });
}

{
  const openState: FullMessageModalInteractionState = {
    isOpen: true,
    selectedMessageId: "message-a",
  };
  const state = reduceFullMessageModalInteraction(openState, {
    type: "context-menu",
  });

  assert.deepEqual(
    state,
    {
      isOpen: false,
      selectedMessageId: "message-a",
    },
    "right click must not mutate the current selection",
  );
}

const workspaceShellSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);

const correctionFailures: string[] = [];
const recordCorrectionExpectation = (name: string, expectation: () => void) => {
  try {
    expectation();
  } catch (error) {
    correctionFailures.push(
      `${name}: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
};

recordCorrectionExpectation("modal-origin compose presentation", () => {
  const renderMessageActionsSource = workspaceShellSource.slice(
    workspaceShellSource.indexOf("const renderMessageActions ="),
    workspaceShellSource.indexOf("const serializeAttachmentBlob ="),
  );
  const hasModalOriginWiring =
    /const originPresentation\s*=\s*placement === "full" \? "modal" : "workspace"/.test(
      renderMessageActionsSource,
    ) &&
    /isComposeOpen\s*&&\s*composePresentation === "modal"[\s\S]{0,800}WorkspaceModalLayer/.test(
      workspaceShellSource,
    );

  assert.equal(
    hasModalOriginWiring ? "modal" : "workspace",
    "modal",
    "baseline currently routes full-message actions to the workspace compose presentation",
  );
});

recordCorrectionExpectation("modal compose action plan", () => {
  (["reply", "reply_all", "forward"] as const).forEach((mode) => {
    assert.deepEqual(
      planFullMessageModalComposeAction("message-root", mode),
      {
        composePresentation: "modal",
        isComposeOpen: true,
        isFullMessageOpen: false,
        mode,
        sourceMessageId: "message-root",
      },
      `${mode} must replace the message presentation with modal compose`,
    );
  });
});

recordCorrectionExpectation("modal compose return target", () => {
  assert.equal(
    resolveModalComposeReturnMessageId("message-root", ["message-root"]),
    "message-root",
    "a valid modal source must reopen after discard or send",
  );
  assert.equal(
    resolveModalComposeReturnMessageId("message-root", ["message-other"]),
    null,
    "a removed modal source must fall back to the inbox",
  );
});

recordCorrectionExpectation("default modal width contract", () => {
  assert.deepEqual(FULL_MESSAGE_MODAL_VIEWPORT, {
    height: "66dvh",
    maxHeight: "calc(100dvh - 2rem)",
    maxWidth: "min(1040px, calc(100vw - 2rem))",
    width: "60vw",
  });
  assert.deepEqual(createDefaultFullMessageModalSize({ width: 1440, height: 900 }), {
    width: 864,
    height: 594,
  });
  assert.deepEqual(createDefaultFullMessageModalSize({ width: 1920, height: 1080 }), {
    width: 1040,
    height: 712.8000000000001,
  });
  assert.deepEqual(createDefaultFullMessageModalSize({ width: 1024, height: 700 }), {
    width: 720,
    height: 480,
  });
});

recordCorrectionExpectation("user resize geometry contract", () => {
  assert.match(
    workspaceShellSource,
    /data-full-message-modal-resize-handle/,
    "baseline currently has no user-resize handle",
  );
  const viewport = { width: 1280, height: 720 };

  assert.deepEqual(
    resizeFullMessageModalSize(
      { width: 1000, height: 650 },
      { width: -500, height: -400 },
      viewport,
    ),
    { width: 720, height: 480 },
    "desktop product minimums must clamp a smaller resize",
  );
  assert.deepEqual(
    resizeFullMessageModalSize(
      { width: 1000, height: 650 },
      { width: 1000, height: 1000 },
      viewport,
    ),
    { width: 1248, height: 688 },
    "a larger resize must stop at the 16px viewport margin",
  );
  assert.deepEqual(
    resizeFullMessageModalSize(
      { width: 1320, height: 800 },
      { width: 1000, height: 0 },
      { width: 1920, height: 1080 },
    ),
    { width: 1888, height: 800 },
    "the 1320px cap belongs to the default, not to the user's larger resize",
  );
  assert.deepEqual(
    clampFullMessageModalSize(
      { width: 720, height: 480 },
      { width: 650, height: 430 },
    ),
    { width: 618, height: 398 },
    "viewport safety must win when the viewport is below the product minimum",
  );
  assert.deepEqual(
    clampFullMessageModalSize(
      { width: 1200, height: 800 },
      { width: 1024, height: 700 },
    ),
    { width: 992, height: 668 },
    "an already resized modal must recontain after viewport shrink",
  );
  assert.deepEqual(
    createDefaultFullMessageModalSize(viewport),
    { width: 768, height: 480 },
    "a fresh opening must reset rather than reuse custom geometry",
  );
});

recordCorrectionExpectation("modal action wiring", () => {
  const renderMessageActionsSource = workspaceShellSource.slice(
    workspaceShellSource.indexOf("const renderMessageActions ="),
    workspaceShellSource.indexOf("const serializeAttachmentBlob ="),
  );

  assert.match(
    renderMessageActionsSource,
    /const originPresentation\s*=\s*placement === "full" \? "modal" : "workspace"/,
    "full placement must preserve modal origin while split placement stays workspace",
  );
  (["reply", "reply_all", "forward"] as const).forEach((mode) => {
    assert.match(
      renderMessageActionsSource,
      new RegExp(
        `openComposeFromMessage\\(message, "${mode}", originPresentation\\)`,
      ),
      `${mode} must pass the presentation selected from the action placement`,
    );
  });
  assert.match(
    workspaceShellSource,
    /isComposeOpen\s*&&\s*composePresentation === "modal"[\s\S]{0,800}WorkspaceModalLayer/,
    "modal-origin compose must render inside the workspace modal layer",
  );
});

recordCorrectionExpectation("fixed full-message toolbar ordering", () => {
  const fullMessageModalSource = workspaceShellSource.slice(
    workspaceShellSource.indexOf("{fullMessageModalMessage && typeof document"),
    workspaceShellSource.indexOf("data-full-message-modal-resize-handle"),
  );
  const toolbarIndex = fullMessageModalSource.indexOf("<DesktopWindowToolbar");
  const scrollRegionIndex = fullMessageModalSource.indexOf(
    "data-full-message-modal-scroll-region",
  );

  assert.match(
    workspaceShellSource,
    /function DesktopWindowToolbar\([\s\S]{0,1200}data-desktop-window-toolbar/,
    "full-message chrome must use the small shared desktop window toolbar component",
  );
  assert.match(
    workspaceShellSource,
    /data-desktop-window-toolbar[\s\S]{0,120}flex-none[\s\S]{0,900}title=\{titleText\}[\s\S]{0,150}truncate[\s\S]{0,800}data-desktop-window-toolbar-actions[\s\S]{0,300}data-desktop-window-toolbar-close/,
    "the fixed toolbar must expose accessible truncating title, action, and close slots",
  );
  assert.ok(
    toolbarIndex >= 0 && toolbarIndex < scrollRegionIndex,
    "the non-scrolling toolbar must precede the sole conversation scroll region",
  );
});

recordCorrectionExpectation("full-message toolbar action authority", () => {
  const fullMessageModalSource = workspaceShellSource.slice(
    workspaceShellSource.indexOf("{fullMessageModalMessage && typeof document"),
    workspaceShellSource.indexOf("data-full-message-modal-resize-handle"),
  );
  const toolbarSource = fullMessageModalSource.slice(
    fullMessageModalSource.indexOf("<DesktopWindowToolbar"),
    fullMessageModalSource.indexOf("data-full-message-modal-scroll-region"),
  );

  assert.match(
    toolbarSource,
    /actions=\{renderMessageActions\(fullMessageModalMessage, "full"\)\}/,
    "Reply, Reply All, Forward, and More must retain fullMessageModalMessage as their source",
  );
  assert.doesNotMatch(
    toolbarSource,
    /threadMessages\.at\(-1\)|expandedMemberIds/,
    "toolbar authority must not follow the latest physical block or disclosure state",
  );
  assert.match(
    workspaceShellSource,
    /placement === "full" \? \([\s\S]{0,220}<DesktopMessageActionIcon name="reply-all"[\s\S]{0,120}<span>Reply All<\/span>[\s\S]{0,100}: \(\s*"Reply all"/,
    "the full toolbar must expose the requested Reply All label without changing split-view copy",
  );
});

recordCorrectionExpectation("compact full-message toolbar icon contract", () => {
  const renderMessageActionsSource = workspaceShellSource.slice(
    workspaceShellSource.indexOf("const renderMessageActions ="),
    workspaceShellSource.indexOf(
      "const serializeAttachmentBlob =",
      workspaceShellSource.indexOf("const renderMessageActions ="),
    ),
  );
  const fullMessageModalSource = workspaceShellSource.slice(
    workspaceShellSource.indexOf("{fullMessageModalMessage && typeof document"),
    workspaceShellSource.indexOf("data-full-message-modal-resize-handle"),
  );

  for (const [action, icon] of [
    ["Reply", "reply"],
    ["Reply All", "reply-all"],
    ["Forward", "forward"],
  ] as const) {
    assert.match(
      renderMessageActionsSource,
      new RegExp(
        `placement === "full"[\\s\\S]{0,180}<DesktopMessageActionIcon name="${icon}"[\\s\\S]{0,180}${action}`,
      ),
      `full-message ${action} must pair its label with the existing ${icon} icon`,
    );
  }
  assert.match(
    renderMessageActionsSource,
    /aria-label=\{placement === "full" \? "More" : undefined\}[\s\S]{0,1200}placement === "full" \? \([\s\S]{0,120}<DesktopMessageActionIcon name="more"/,
    "full-message More must be icon-only with an accessible name",
  );
  assert.match(
    fullMessageModalSource,
    /aria-label="Close full message"[\s\S]{0,900}<DesktopMessageActionIcon name="close"/,
    "full-message Close must be a compact icon-only window control",
  );
  assert.doesNotMatch(
    fullMessageModalSource,
    /aria-label="Close full message"[\s\S]{0,260}>\s*Close\s*<\/button>/,
    "full-message Close must not retain its pill label",
  );
  assert.match(
    renderMessageActionsSource,
    /placement === "full" \? \([\s\S]{0,220}<DesktopMessageActionIcon name="reply"/,
    "icons must remain gated to the full-message placement",
  );
  assert.match(
    renderMessageActionsSource,
    /const actionHeight = "h-7"/,
    "compact full-message sizing must preserve the existing direct-action height",
  );
  for (const splitLabel of ["Reply", "Reply all", "Forward"]) {
    assert.match(
      renderMessageActionsSource,
      new RegExp(`\\) : \\(\\s*"${splitLabel}"\\s*\\)}`),
      `split-view ${splitLabel} text must retain the accepted renderer branch`,
    );
  }
});

recordCorrectionExpectation("full-message action extraction and split preservation", () => {
  const threadTimelineSource = workspaceShellSource.slice(
    workspaceShellSource.indexOf("const renderThreadTimeline ="),
    workspaceShellSource.indexOf(
      "const activeStoredCollaborationMessage =",
      workspaceShellSource.indexOf("const renderThreadTimeline ="),
    ),
  );

  assert.match(
    threadTimelineSource,
    /density === "split" &&\s*isLatestThreadMessage &&\s*actionMessage\s*\? renderMessageActions\(actionMessage, density\)/,
    "only split view may inject the existing action row into the latest message block",
  );
  assert.match(
    workspaceShellSource,
    /renderThreadTimeline\(\s*fullMessageModalMessage,\s*"full",\s*null/,
    "the full-message conversation renderer must own content only",
  );
  assert.match(
    workspaceShellSource,
    /renderThreadTimeline\(\s*selectedMessage,\s*"split",\s*fullWidthMessage \?\? selectedMessage/,
    "split view must retain its existing action source and placement",
  );
});

recordCorrectionExpectation("shared composer and normal compose regression", () => {
  assert.equal(
    workspaceShellSource.match(/data-desktop-composer/g)?.length,
    1,
    "workspace and modal placements must render the same desktop composer element",
  );
  assert.equal(
    workspaceShellSource.match(/const sendMessage\s*=/g)?.length,
    1,
    "modal compose must keep the existing send authority",
  );
  assert.match(
    workspaceShellSource,
    /const openComposeFromMessage\s*=\s*\([\s\S]{0,180}originPresentation: ComposePresentation = "workspace"/,
    "toolbar actions must keep a deterministic default origin",
  );
  assert.match(
    workspaceShellSource,
    /const composePlan = planMessageComposeAction\([\s\S]{0,160}originPresentation/,
    "every normal message compose action must use the centralized presentation plan",
  );
  const openComposeSource = workspaceShellSource.slice(
    workspaceShellSource.indexOf("const openCompose ="),
    workspaceShellSource.indexOf("const openComposeAttachmentPicker ="),
  );
  assert.match(
    openComposeSource,
    /setComposePresentation\("modal"\)/,
    "the desktop Compose button must open the shared composer in modal presentation",
  );
  assert.match(
    workspaceShellSource,
    /data-modal-compose[\s\S]{0,700}composeModalSize[\s\S]{0,400}COMPOSE_MODAL_VIEWPORT/,
    "modal compose must use its own compact, viewport-safe desktop size",
  );
});

recordCorrectionExpectation("compose title contract", () => {
  const titleSource = workspaceShellSource.slice(
    workspaceShellSource.indexOf('id="desktop-compose-title"'),
    workspaceShellSource.indexOf("</h2>", workspaceShellSource.indexOf('id="desktop-compose-title"')),
  );

  assert.match(titleSource, /composeMode === "reply"[\s\S]{0,80}\? "Reply"/);
  assert.match(titleSource, /composeMode === "reply_all"[\s\S]{0,100}\? "Reply All"/);
  assert.match(titleSource, /composeMode === "forward"[\s\S]{0,100}\? "Forward"/);
  assert.match(titleSource, /: "New Message"/);
});

recordCorrectionExpectation("modal compose close confirmation layer", () => {
  const confirmationSource = workspaceShellSource.slice(
    workspaceShellSource.indexOf("const composeCloseConfirmation ="),
    workspaceShellSource.indexOf("\n\n  return (", workspaceShellSource.indexOf("const composeCloseConfirmation =")),
  );

  assert.match(
    workspaceShellSource,
    /ref=\{composeModalCloseButtonRef\}[\s\S]{0,150}setIsCloseModalOpen\(true\)/,
    "clicking Close must request the established compose confirmation",
  );
  assert.match(
    workspaceShellSource,
    /isCloseModalOpen\s*\?\s*composePresentation === "modal"\s*&&\s*typeof document !== "undefined"[\s\S]{0,160}createPortal\(composeCloseConfirmation, document\.body\)/,
    "modal compose confirmation must be portalled above the active workspace modal layer",
  );
  assert.match(
    workspaceShellSource,
    /data-compose-close-confirmation-layer[\s\S]{0,250}z-\[340\]/,
    "the portalled compose confirmation must remain above WorkspaceModalLayer z-[321]",
  );
  assert.match(
    confirmationSource,
    /data-compose-close-confirmation-layer[\s\S]{0,120}data-theme=\{themeMode\}[\s\S]{0,180}colorScheme: themeMode/,
    "the body-portalled confirmation must carry the active theme variables and color scheme",
  );
  assert.match(
    workspaceShellSource,
    /data-compose-close-confirmation-layer[\s\S]{0,500}role="alertdialog"[\s\S]{0,1600}Save to Drafts[\s\S]{0,800}Discard[\s\S]{0,800}Cancel/,
    "the visible confirmation must retain Save, Discard, and Cancel",
  );
});

recordCorrectionExpectation("modal compose resize geometry", () => {
  assert.deepEqual(COMPOSE_MODAL_VIEWPORT, {
    height: "76dvh",
    maxHeight: "calc(100dvh - 2rem)",
    maxWidth: "min(1180px, calc(100vw - 2rem))",
    width: "70vw",
  });
  assert.deepEqual(
    createDefaultComposeModalSize({ width: 1280, height: 720 }),
    { width: 896, height: 547.2 },
    "a fresh compose opening must use the approved default geometry",
  );
  assert.deepEqual(
    resizeComposeModalSize(
      { width: 900, height: 600 },
      { width: -1000, height: -1000 },
      { width: 1280, height: 720 },
    ),
    { width: 760, height: 520 },
    "compose resizing must preserve a safe writing layout minimum",
  );
  assert.deepEqual(
    resizeComposeModalSize(
      { width: 900, height: 600 },
      { width: 1000, height: 1000 },
      { width: 1280, height: 720 },
    ),
    { width: 1248, height: 688 },
    "compose resizing must stop at the shared 16px viewport margin",
  );
  assert.deepEqual(
    resizeComposeModalSize(
      { width: 760, height: 520 },
      { width: -1000, height: -1000 },
      { width: 650, height: 430 },
    ),
    { width: 618, height: 398 },
    "viewport safety must win below the compose product minimum",
  );
});

recordCorrectionExpectation("modal compose resize interaction wiring", () => {
  assert.match(
    workspaceShellSource,
    /const \[composeModalSize, setComposeModalSize\]/,
    "modal compose needs isolated in-memory resize geometry",
  );
  assert.match(
    workspaceShellSource,
    /data-compose-modal-resize-handle/,
    "every desktop compose mode must expose the shared modal resize handle",
  );
  assert.match(
    workspaceShellSource,
    /modalResizeSessionRef[\s\S]{0,120}kind: "compose" \| "full-message"/,
    "message and compose must share one resize interaction session",
  );
  assert.match(
    workspaceShellSource,
    /data-compose-modal-resize-handle[\s\S]{0,500}onPointerMove=\{handleModalResizePointerMove\}[\s\S]{0,400}handleModalResizeKeyDown\(event, "compose"\)/,
    "compose pointer and keyboard resizing must use the shared lifecycle",
  );
  assert.match(
    workspaceShellSource,
    /isComposeOpen[\s\S]{0,500}createDefaultComposeModalSize[\s\S]{0,800}window\.addEventListener\("resize"/,
    "opening must reset compose geometry and browser resize must recontain it",
  );
});

recordCorrectionExpectation("modal compose completion wiring", () => {
  const discardSource = workspaceShellSource.slice(
    workspaceShellSource.indexOf("const discardCompose ="),
    workspaceShellSource.indexOf("const sendMessage ="),
  );
  const sendSource = workspaceShellSource.slice(
    workspaceShellSource.indexOf("const sendMessage ="),
    workspaceShellSource.indexOf("const closeMenus ="),
  );

  assert.match(
    discardSource,
    /composePresentation === "modal"[\s\S]{0,350}restoreFullMessageModalAfterCompose\(\)/,
    "discarding modal compose must return to its valid source message",
  );
  assert.match(
    sendSource,
    /if \(composePresentation === "modal"\)[\s\S]{0,250}restoreFullMessageModalAfterCompose\(\)/,
    "successful modal send must use the same source-return path for every mode",
  );
  assert.match(
    workspaceShellSource,
    /function restoreFullMessageModalAfterCompose\(\)[\s\S]{0,500}resolveModalComposeReturnMessageId[\s\S]{0,600}setIsFullMessageOpen\(true\)/,
    "the return path must validate the source before reopening the message modal",
  );
  assert.match(
    workspaceShellSource,
    /function restoreFullMessageModalAfterCompose\(\)[\s\S]{0,350}latestMailboxStoreRef\.current/,
    "async send completion must validate against the latest mailbox store",
  );
  assert.match(
    workspaceShellSource,
    /ref=\{composeModalCloseButtonRef\}[\s\S]{0,150}setIsCloseModalOpen\(true\)/,
    "Close must retain the established save/discard confirmation",
  );
  assert.match(
    workspaceShellSource,
    /ref=\{composeCloseConfirmationCancelRef\}[\s\S]{0,150}onClick=\{continueEditingCompose\}/,
    "confirmation Cancel must keep modal compose open and restore focus",
  );
});

recordCorrectionExpectation("resize interaction wiring", () => {
  assert.match(
    workspaceShellSource,
    /setPointerCapture\(event\.pointerId\)/,
    "the resize handle must capture its pointer",
  );
  assert.match(
    workspaceShellSource,
    /data-full-message-modal-resize-handle[\s\S]{0,500}onPointerCancel=\{handleModalResizePointerEnd\}/,
    "pointer cancellation must share resize cleanup",
  );
  assert.match(
    workspaceShellSource,
    /releasePointerCapture\(pointerId\)/,
    "resize completion must release pointer capture",
  );
  assert.match(
    workspaceShellSource,
    /window\.addEventListener\("resize", keepFullMessageModalInViewport\)[\s\S]{0,250}window\.removeEventListener\("resize", keepFullMessageModalInViewport\)/,
    "viewport shrink handling must be installed and cleaned up",
  );
  assert.match(
    workspaceShellSource,
    /if \(options\?\.openFull\) \{\s*resetFullMessageModalSize\(\)/,
    "each fresh double-click opening must reset to default geometry",
  );
});

assert.equal(
  correctionFailures.length,
  0,
  `full-message modal correction expectations failed:\n${correctionFailures
    .map((failure) => `- ${failure}`)
    .join("\n")}`,
);

assert.doesNotMatch(
  workspaceShellSource,
  /:\s*isFullMessageOpen\s*&&\s*fullWidthMessage\s*\?\s*\(/,
  "full message must not replace the split-view branch",
);
assert.match(
  workspaceShellSource,
  /data-full-message-modal-message-id=\{fullMessageModalMessage\.id\}/,
);
assert.match(
  workspaceShellSource,
  /const fullMessageModalResolvedMessage\s*=\s*selectedMessageFromFolder \?\? selectedMessageFromCurrentId/,
  "the modal target must never use the reading pane's top-message fallback",
);
assert.match(workspaceShellSource, /role="dialog"/);
assert.match(workspaceShellSource, /aria-modal="true"/);
assert.match(
  workspaceShellSource,
  /aria-labelledby="full-message-modal-title"/,
);
assert.match(
  workspaceShellSource,
  /ref=\{fullMessageModalCloseButtonRef\}[\s\S]{0,300}aria-label="Close full message"/,
);
assert.match(
  workspaceShellSource,
  /event\.key === "Escape"[\s\S]{0,300}closeFullMessageModal\("escape"\)/,
);
assert.match(
  workspaceShellSource,
  /event\.key !== "Tab"[\s\S]{0,1800}firstFocusableElement\.focus\(\)/,
  "keyboard focus must remain inside the open dialog",
);
assert.match(
  workspaceShellSource,
  /returnFocusTarget\.focus\(\{ preventScroll: true \}\)/,
  "closing the modal must not move the inbox list viewport",
);
assert.match(
  workspaceShellSource,
  /if \(!isFullMessageOpen\) \{\s*fullMessageModalReturnFocusRef\.current = null/,
  "non-Close modal exits must not retain a stale opener",
);
assert.match(
  workspaceShellSource,
  /data-message-row-id=\{message\.id\}[\s\S]{0,1800}onClick=\{\(event\)[\s\S]{0,900}handleSelectMessage\(activeFolder, message\.id\);[\s\S]{0,300}onDoubleClick=\{\(event\)[\s\S]{0,500}openFull: true/,
);
assert.match(
  workspaceShellSource,
  /onContextMenu=\{\(event\)[\s\S]{0,120}event\.preventDefault\(\)[\s\S]{0,300}type: "context-menu"/,
);
assert.match(
  workspaceShellSource,
  /data-full-message-modal-scroll-region[\s\S]{0,300}overflow-y-auto/,
);
assert.match(
  workspaceShellSource,
  /data-full-message-modal-message-id=\{fullMessageModalMessage\.id\}[\s\S]*actions=\{renderMessageActions\(fullMessageModalMessage, "full"\)\}[\s\S]*renderThreadTimeline\(\s*fullMessageModalMessage,\s*"full",\s*null/,
  "the modal toolbar and content must reuse the established action and message renderers",
);
assert.match(
  workspaceShellSource,
  /renderThreadTimeline\(\s*selectedMessage,\s*"split",\s*fullWidthMessage \?\? selectedMessage/,
  "the split pane must keep using the same action renderer",
);
assert.equal(
  workspaceShellSource.match(/const renderThreadMessage\s*=/g)?.length,
  1,
  "there must be one email body renderer",
);
assert.equal(
  workspaceShellSource.match(/const renderMessageActions\s*=/g)?.length,
  1,
  "there must be one message action renderer",
);
