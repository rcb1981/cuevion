const MEDIUM_WORKSPACE_QUERY = "(min-width: 768px) and (max-width: 1279px)";
const WORKSPACE_NAV_SELECTOR = 'nav[aria-label="Workspace navigation"]';
const COLLAPSED_ATTRIBUTE = "data-responsive-sidebar-collapsed";

export function shouldAutoCollapseResponsiveSidebarAction({
  isPointerClick,
  isMediumViewport,
  ariaExpanded,
  ariaLabel,
}: {
  isPointerClick: boolean;
  isMediumViewport: boolean;
  ariaExpanded: string | null;
  ariaLabel: string | null;
}) {
  if (!isPointerClick || !isMediumViewport) {
    return false;
  }

  if (ariaExpanded !== null) {
    return false;
  }

  if (ariaLabel?.startsWith("Manage ")) {
    return false;
  }

  return true;
}

export function installResponsiveWorkspaceSidebarBehavior() {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return () => {};
  }

  const mediumViewport = window.matchMedia(MEDIUM_WORKSPACE_QUERY);

  const handleClick = (event: MouseEvent) => {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }

    const button = target.closest<HTMLButtonElement>("button");
    const navigation = button?.closest<HTMLElement>(WORKSPACE_NAV_SELECTOR);
    const sidebar = navigation?.closest<HTMLElement>("aside");
    if (!button || !navigation || !sidebar) {
      return;
    }

    if (
      !shouldAutoCollapseResponsiveSidebarAction({
        isPointerClick: event.detail > 0,
        isMediumViewport: mediumViewport.matches,
        ariaExpanded: button.getAttribute("aria-expanded"),
        ariaLabel: button.getAttribute("aria-label"),
      })
    ) {
      return;
    }

    sidebar.setAttribute(COLLAPSED_ATTRIBUTE, "true");
    button.blur();
  };

  const handlePointerOut = (event: PointerEvent) => {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }

    const sidebar = target.closest<HTMLElement>(
      'aside[' + COLLAPSED_ATTRIBUTE + '="true"]',
    );
    if (!sidebar) {
      return;
    }

    const relatedTarget = event.relatedTarget;
    if (relatedTarget instanceof Node && sidebar.contains(relatedTarget)) {
      return;
    }

    sidebar.removeAttribute(COLLAPSED_ATTRIBUTE);
  };

  document.addEventListener("click", handleClick);
  document.addEventListener("pointerout", handlePointerOut);

  return () => {
    document.removeEventListener("click", handleClick);
    document.removeEventListener("pointerout", handlePointerOut);
  };
}
