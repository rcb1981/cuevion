import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  FULL_MESSAGE_MODAL_VIEWPORT,
  reduceFullMessageModalInteraction,
  resolveFullMessageModalMessageId,
  type FullMessageModalInteractionState,
} from "./fullMessageModalState";

const initialState: FullMessageModalInteractionState = {
  isOpen: false,
  selectedMessageId: "message-a",
};

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

assert.deepEqual(FULL_MESSAGE_MODAL_VIEWPORT, {
  height: "88dvh",
  maxHeight: "calc(100dvh - 2rem)",
  maxWidth: "calc(100vw - 2rem)",
  width: "88vw",
});

const workspaceShellSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
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
  /data-full-message-modal-message-id=\{fullMessageModalMessage\.id\}[\s\S]*renderMessageActions\(fullMessageModalMessage, "full"\)[\s\S]*renderThread(?:Timeline|Message)\(fullMessageModalMessage, "full"\)/,
  "the modal must reuse the established action and message renderers",
);
assert.match(
  workspaceShellSource,
  /renderMessageActions\(fullWidthMessage \?\? selectedMessage, "split"\)/,
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
