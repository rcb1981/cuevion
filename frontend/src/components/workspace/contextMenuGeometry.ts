export const CONTEXT_MENU_VIEWPORT_PADDING = 12;

export function getContextMenuPosition({
  anchorX,
  anchorY,
  menuWidth,
  placementHeight,
  viewportHeight,
  viewportWidth,
}: {
  anchorX: number;
  anchorY: number;
  menuWidth: number;
  placementHeight: number;
  viewportHeight: number;
  viewportWidth: number;
}) {
  const viewportPadding = CONTEXT_MENU_VIEWPORT_PADDING;
  const availableHeight = Math.max(0, viewportHeight - viewportPadding * 2);
  const availableWidth = Math.max(0, viewportWidth - viewportPadding * 2);
  const maxHeight = Math.min(placementHeight, availableHeight);
  const width = Math.min(menuWidth, availableWidth);
  const maxLeft = Math.max(viewportPadding, viewportWidth - viewportPadding - width);
  const maxTop = Math.max(viewportPadding, viewportHeight - viewportPadding - maxHeight);
  const openDownward = anchorY + maxHeight <= viewportHeight - viewportPadding;
  const preferredTop = openDownward ? anchorY : anchorY - maxHeight;

  return {
    left: Math.max(viewportPadding, Math.min(anchorX, maxLeft)),
    top: Math.max(viewportPadding, Math.min(preferredTop, maxTop)),
    maxHeight,
    width,
  };
}

export function getAnchoredSubmenuPosition({
  parentLeft,
  parentWidth,
  anchorY,
  submenuWidth,
  submenuHeight,
  viewportHeight,
  viewportWidth,
  anchorOffsetY = -4,
}: {
  parentLeft: number;
  parentWidth: number;
  anchorY: number;
  submenuWidth: number;
  submenuHeight: number;
  viewportHeight: number;
  viewportWidth: number;
  anchorOffsetY?: number;
}) {
  const menuGap = 8;
  const viewportPadding = CONTEXT_MENU_VIEWPORT_PADDING;
  const availableHeight = Math.max(0, viewportHeight - viewportPadding * 2);
  const availableWidth = Math.max(0, viewportWidth - viewportPadding * 2);
  const maxHeight = Math.min(submenuHeight, availableHeight);
  const width = Math.min(submenuWidth, availableWidth);
  const parentMenuLeft = parentLeft;
  const parentMenuRight = parentLeft + parentWidth;
  const openRight =
    parentMenuRight + menuGap + width <= viewportWidth - viewportPadding;
  const left = openRight
    ? parentMenuRight + menuGap
    : parentMenuLeft - width - menuGap;
  const preferredTop = anchorY + anchorOffsetY;
  const maxLeft = Math.max(viewportPadding, viewportWidth - viewportPadding - width);
  const maxTop = Math.max(viewportPadding, viewportHeight - viewportPadding - maxHeight);

  return {
    left: Math.max(viewportPadding, Math.min(left, maxLeft)),
    top: Math.max(viewportPadding, Math.min(preferredTop, maxTop)),
    maxHeight,
    width,
  };
}
