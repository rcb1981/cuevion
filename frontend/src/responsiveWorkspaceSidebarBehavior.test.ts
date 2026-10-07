import assert from "node:assert/strict";
import { shouldAutoCollapseResponsiveSidebarAction } from "./responsiveWorkspaceSidebarBehavior";

const decide = (
  overrides: Partial<Parameters<typeof shouldAutoCollapseResponsiveSidebarAction>[0]> = {},
) =>
  shouldAutoCollapseResponsiveSidebarAction({
    isPointerClick: true,
    isMediumViewport: true,
    ariaExpanded: null,
    ariaLabel: "Dashboard",
    ...overrides,
  });

assert.equal(decide(), true, "ordinary pointer navigation should collapse the rail");
assert.equal(
  decide({ ariaLabel: "Carl Tricks Mail" }),
  true,
  "choosing a concrete mailbox should collapse the rail",
);
assert.equal(
  decide({ ariaExpanded: "false", ariaLabel: "Inboxes" }),
  false,
  "the Inboxes disclosure must stay open while choosing a mailbox",
);
assert.equal(
  decide({ ariaExpanded: "true", ariaLabel: "Smart Folders" }),
  false,
  "the Smart Folders disclosure must stay open while choosing a folder",
);
assert.equal(
  decide({ ariaLabel: "Manage Demo submissions" }),
  false,
  "Smart Folder management must keep the rail available",
);
assert.equal(
  decide({ isPointerClick: false }),
  false,
  "keyboard activation must preserve focus-driven expansion",
);
assert.equal(
  decide({ isMediumViewport: false }),
  false,
  "desktop and mobile navigation must remain unchanged",
);
