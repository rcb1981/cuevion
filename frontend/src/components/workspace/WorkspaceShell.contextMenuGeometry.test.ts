import assert from "node:assert/strict";
import {
  CONTEXT_MENU_VIEWPORT_PADDING,
  getAnchoredSubmenuPosition,
  getContextMenuPosition,
} from "./contextMenuGeometry";

const mainMenuWidth = 238;
const mainMenuPlacementHeight = 520;

function getRenderedMainMenuBounds({
  anchorX,
  anchorY,
  naturalHeight,
  viewportHeight,
  viewportWidth,
}: {
  anchorX: number;
  anchorY: number;
  naturalHeight: number;
  viewportHeight: number;
  viewportWidth: number;
}) {
  const position = getContextMenuPosition({
    anchorX,
    anchorY,
    menuWidth: mainMenuWidth,
    placementHeight: mainMenuPlacementHeight,
    viewportHeight,
    viewportWidth,
  });
  const renderedHeight = Math.min(naturalHeight, position.maxHeight);

  return {
    bottom: position.top + renderedHeight,
    left: position.left,
    right: position.left + position.width,
    top: position.top,
    maxHeight: position.maxHeight,
  };
}

function assertWithinViewport(
  bounds: { bottom: number; left: number; right: number; top: number },
  viewportWidth: number,
  viewportHeight: number,
) {
  assert.ok(
    bounds.left >= CONTEXT_MENU_VIEWPORT_PADDING,
    `left ${bounds.left} must be at least the viewport padding`,
  );
  assert.ok(
    bounds.top >= CONTEXT_MENU_VIEWPORT_PADDING,
    `top ${bounds.top} must be at least the viewport padding`,
  );
  assert.ok(
    bounds.right <= viewportWidth - CONTEXT_MENU_VIEWPORT_PADDING,
    `right ${bounds.right} must stay inside the viewport`,
  );
  assert.ok(
    bounds.bottom <= viewportHeight - CONTEXT_MENU_VIEWPORT_PADDING,
    `bottom ${bounds.bottom} must stay inside the viewport`,
  );
}

{
  const viewportWidth = 1440;
  const viewportHeight = 900;
  const bounds = getRenderedMainMenuBounds({
    anchorX: 1435,
    anchorY: 895,
    naturalHeight: 640,
    viewportHeight,
    viewportWidth,
  });

  assertWithinViewport(bounds, viewportWidth, viewportHeight);
  assert.equal(bounds.right, viewportWidth - CONTEXT_MENU_VIEWPORT_PADDING);
  assert.equal(bounds.bottom, viewportHeight - CONTEXT_MENU_VIEWPORT_PADDING);
}

{
  const viewportWidth = 1440;
  const viewportHeight = 900;
  const bounds = getRenderedMainMenuBounds({
    anchorX: 720,
    anchorY: 895,
    naturalHeight: 640,
    viewportHeight,
    viewportWidth,
  });

  assertWithinViewport(bounds, viewportWidth, viewportHeight);
  assert.equal(bounds.left, 720);
  assert.equal(bounds.bottom, viewportHeight - CONTEXT_MENU_VIEWPORT_PADDING);
}

{
  const viewportWidth = 1440;
  const viewportHeight = 900;
  const bounds = getRenderedMainMenuBounds({
    anchorX: 1435,
    anchorY: 80,
    naturalHeight: 420,
    viewportHeight,
    viewportWidth,
  });

  assertWithinViewport(bounds, viewportWidth, viewportHeight);
  assert.equal(bounds.right, viewportWidth - CONTEXT_MENU_VIEWPORT_PADDING);
  assert.equal(bounds.top, 80);
}

{
  const viewportWidth = 320;
  const viewportHeight = 300;
  const bounds = getRenderedMainMenuBounds({
    anchorX: 1,
    anchorY: 1,
    naturalHeight: 200,
    viewportHeight,
    viewportWidth,
  });

  assertWithinViewport(bounds, viewportWidth, viewportHeight);
  assert.equal(bounds.left, CONTEXT_MENU_VIEWPORT_PADDING);
  assert.equal(bounds.top, CONTEXT_MENU_VIEWPORT_PADDING);
}

{
  const viewportWidth = 800;
  const viewportHeight = 160;
  const bounds = getRenderedMainMenuBounds({
    anchorX: 400,
    anchorY: 155,
    naturalHeight: 900,
    viewportHeight,
    viewportWidth,
  });

  assertWithinViewport(bounds, viewportWidth, viewportHeight);
  assert.equal(
    bounds.maxHeight,
    viewportHeight - CONTEXT_MENU_VIEWPORT_PADDING * 2,
  );
}

{
  const viewportWidth = 1440;
  const viewportHeight = 900;
  const parentLeft = 1190;
  const submenuWidth = 210;
  const position = getAnchoredSubmenuPosition({
    parentLeft,
    parentWidth: mainMenuWidth,
    anchorY: 200,
    submenuWidth,
    submenuHeight: 230,
    viewportHeight,
    viewportWidth,
  });

  assert.ok(position.left < parentLeft, "submenu should open inward to the left");
  assertWithinViewport(
    {
      bottom: position.top + 230,
      left: position.left,
      right: position.left + position.width,
      top: position.top,
    },
    viewportWidth,
    viewportHeight,
  );
}

{
  const viewportWidth = 800;
  const viewportHeight = 180;
  const submenuHeight = 360;
  const position = getAnchoredSubmenuPosition({
    parentLeft: 300,
    parentWidth: mainMenuWidth,
    anchorY: 150,
    submenuWidth: 210,
    submenuHeight,
    viewportHeight,
    viewportWidth,
  });
  const maxHeight = position.maxHeight;

  assert.equal(
    maxHeight,
    viewportHeight - CONTEXT_MENU_VIEWPORT_PADDING * 2,
  );
  assertWithinViewport(
    {
      bottom: position.top + Math.min(submenuHeight, maxHeight ?? submenuHeight),
      left: position.left,
      right: position.left + position.width,
      top: position.top,
    },
    viewportWidth,
    viewportHeight,
  );
}
