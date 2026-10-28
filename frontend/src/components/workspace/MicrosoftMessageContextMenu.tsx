import React, { useState } from "react";
import { createPortal } from "react-dom";
import type { LiveInboxMessageSnapshot } from "../../lib/inboxConnectionApi";
import type { MicrosoftLearningLabel, MicrosoftLearningRule } from "./microsoftLearningRules";

const labels: MicrosoftLearningLabel[] = ["Promo", "Update", "Finance", "Business", "Demo", "Other"];
const itemClass =
  "flex w-full items-center rounded-[14px] px-3 py-2.5 text-left text-[0.82rem] text-[var(--workspace-text)] transition-[background-color,color] duration-150 hover:bg-[var(--workspace-menu-hover)] disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent";
const smallItemClass =
  "flex w-full items-center rounded-[14px] px-3 py-[0.42rem] text-left text-[0.76rem] text-[var(--workspace-text)] transition-[background-color,color] duration-150 hover:bg-[var(--workspace-menu-hover)] disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent";

const MAIN_MENU_WIDTH = 244;
const SUBMENU_WIDTH = 226;
const MENU_GAP = 8;

/** Keep flyouts beside the main menu, never over its Learning trigger. */
export function getMicrosoftContextSubmenuPosition({
  mainMenuX, triggerY, viewportWidth, viewportHeight, expectedHeight,
}: {
  mainMenuX: number;
  triggerY: number;
  viewportWidth: number;
  viewportHeight: number;
  expectedHeight: number;
}) {
  const rightX = mainMenuX + MAIN_MENU_WIDTH + MENU_GAP;
  const leftX = mainMenuX - SUBMENU_WIDTH - MENU_GAP;
  const rightFits = rightX + SUBMENU_WIDTH + MENU_GAP <= viewportWidth;
  const leftFits = leftX >= MENU_GAP;
  const preferLeft = !rightFits && (leftFits || mainMenuX > viewportWidth / 2);
  const candidateX = preferLeft ? leftX : rightX;
  return {
    left: Math.max(MENU_GAP, Math.min(candidateX, viewportWidth - SUBMENU_WIDTH - MENU_GAP)),
    top: Math.max(MENU_GAP, Math.min(triggerY, viewportHeight - Math.min(expectedHeight, viewportHeight - 16) - MENU_GAP)),
  };
}

/**
 * Same visible menu hierarchy as the Gmail/IMAP message menu. The menu never
 * bypasses the Microsoft-only release gates: unreleased actions stay disabled.
 * Its target is the row that was actually right-clicked, not the reader's
 * previously selected message.
 */
export function MicrosoftMessageContextMenu({
  message, x, y, busy, canChangeInbox, canLearn, canShowLess, learningRule,
  onOpen, onReadState, onArchive, onDelete, onSaveLearning,
}: {
  message: LiveInboxMessageSnapshot;
  x: number;
  y: number;
  busy: boolean;
  canChangeInbox: boolean;
  canLearn: boolean;
  canShowLess: boolean;
  learningRule?: MicrosoftLearningRule;
  onOpen: () => void;
  onReadState: (nextUnread: boolean) => void;
  onArchive: () => void;
  onDelete: () => void;
  onSaveLearning: (rule: MicrosoftLearningRule) => void;
}) {
  const [submenu, setSubmenu] = useState<"move" | "learning" | null>(null);
  const [submenuTriggerY, setSubmenuTriggerY] = useState<number | null>(null);
  const [submenuTheme, setSubmenuTheme] = useState<string | undefined>(undefined);
  const [labelChooserOpen, setLabelChooserOpen] = useState(false);
  const currentPriority = learningRule?.priority ?? "normal";
  const currentLabel = learningRule?.label ?? "Other";
  const changeLearning = (priority: MicrosoftLearningRule["priority"], label: MicrosoftLearningLabel = currentLabel) => {
    if (canLearn && !busy) onSaveLearning({ priority, label });
  };
  const showSubmenu = (next: "move" | "learning", trigger: HTMLButtonElement) => {
    setSubmenu(next);
    setSubmenuTriggerY(trigger.getBoundingClientRect().top);
    setSubmenuTheme(trigger.closest("[data-theme]")?.getAttribute("data-theme") ?? undefined);
    setLabelChooserOpen(false);
  };
  const flyoutPosition = submenu && submenuTriggerY !== null && typeof window !== "undefined"
    ? getMicrosoftContextSubmenuPosition({
      mainMenuX: x,
      triggerY: submenuTriggerY,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      expectedHeight: submenu === "move" ? 154 : labelChooserOpen ? 485 : 308,
    })
    : null;

  return (
    <div
      role="menu"
      aria-label="Microsoft message context menu"
      className="cuevion-dark-scroll cuevion-soft-scroll fixed z-[330] max-h-[calc(100dvh-16px)] w-[244px] overflow-y-auto rounded-[20px] border border-[var(--workspace-menu-border)] bg-[var(--workspace-menu-bg)] p-2 shadow-panel"
      style={{ left: x, top: y }}
      onContextMenu={(event) => event.preventDefault()}
      onMouseDown={(event) => event.stopPropagation()}
    >
      <div className="space-y-1">
        <button type="button" role="menuitem" onClick={onOpen} className={itemClass}>Open</button>
        {(["Reply", "Reply All", "Forward", "Collaboration…"] as const).map((label) => (
          <button key={label} type="button" role="menuitem" disabled
            title={label + " is not yet released for Microsoft"} className={itemClass}>{label}</button>
        ))}
      </div>

      <div className="my-2 h-px bg-[var(--workspace-divider)]" />
      <div className="space-y-1">
        <button type="button" role="menuitem" disabled title="Flag is not yet released for Microsoft" className={itemClass}>Flag</button>
        <button type="button" role="menuitem" disabled title="Per-message priority is not yet released for Microsoft" className={itemClass}>Mark as priority</button>
      </div>

      <div className="my-2 h-px bg-[var(--workspace-divider)]" />
      <button type="button" role="menuitem" disabled={!canChangeInbox || busy}
        title={!canChangeInbox ? "Read-state changes are currently available only for Microsoft Inbox and Filtered" : undefined}
        onClick={() => onReadState(!message.unread)} className={itemClass}>
        {message.unread ? "Mark as read" : "Mark as unread"}
      </button>

      <div className="my-2 h-px bg-[var(--workspace-divider)]" />
      <div className="space-y-1">
        <button type="button" role="menuitem" disabled={!canChangeInbox || busy}
          onClick={onArchive} className={itemClass}>Archive</button>
        <button type="button" role="menuitem" disabled={!canChangeInbox || busy}
          onClick={onDelete} className={itemClass}>Delete</button>
      </div>

      <div className="my-2 h-px bg-[var(--workspace-divider)]" />
      <div className="space-y-1">
        <button type="button" role="menuitem" aria-haspopup="menu" aria-expanded={submenu === "move"}
          onClick={(event) => submenu === "move" ? setSubmenu(null) : showSubmenu("move", event.currentTarget)}
          onMouseEnter={(event) => showSubmenu("move", event.currentTarget)}
          className={itemClass + " justify-between"}>Move to... <span aria-hidden="true">›</span></button>
        <button type="button" role="menuitem" aria-haspopup="menu" aria-expanded={submenu === "learning"}
          disabled={!canLearn || busy}
          onClick={(event) => submenu === "learning" ? setSubmenu(null) : showSubmenu("learning", event.currentTarget)}
          onMouseEnter={(event) => { if (canLearn && !busy) showSubmenu("learning", event.currentTarget); }}
          className={itemClass + " justify-between"}>Learning <span aria-hidden="true">›</span></button>
      </div>
      {flyoutPosition && createPortal(
        <div data-microsoft-context-submenu data-theme={submenuTheme} role="menu"
          aria-label={submenu === "move" ? "Microsoft move destinations" : "Microsoft Learning options"}
          className="cuevion-dark-scroll cuevion-soft-scroll fixed z-[331] max-h-[calc(100dvh-16px)] w-[226px] overflow-y-auto rounded-[20px] border border-[var(--workspace-menu-border)] bg-[var(--workspace-menu-bg)] p-2 shadow-panel"
          style={flyoutPosition}
          onMouseDown={(event) => event.stopPropagation()}
          onContextMenu={(event) => event.preventDefault()}>
          {submenu === "move" ? (
            <div className="space-y-1">
              <button type="button" role="menuitem" disabled={!canChangeInbox || busy} onClick={onArchive} className={smallItemClass}>Archive</button>
              <button type="button" role="menuitem" disabled={!canChangeInbox || busy} onClick={onDelete} className={smallItemClass}>Trash</button>
              <button type="button" role="menuitem" disabled title="Moving to Spam is not yet released for Microsoft" className={smallItemClass}>Spam</button>
            </div>
          ) : canLearn && !busy ? (
            <div className="space-y-1">
            <button type="button" role="menuitem" onClick={() => changeLearning("important")} className={smallItemClass}>Mark as important</button>
            <button type="button" role="menuitem" onClick={() => changeLearning("normal")} className={smallItemClass}>Mark as not important</button>
            <button type="button" role="menuitem" disabled={!canShowLess}
              title={!canShowLess ? "This message is protected from automatic filtering" : undefined}
              onClick={() => changeLearning("show_less")} className={smallItemClass}>Show Less</button>
            <button type="button" role="menuitem" aria-expanded={labelChooserOpen} onClick={() => setLabelChooserOpen(!labelChooserOpen)} className={smallItemClass}>Change label...</button>
            {labelChooserOpen && (
              <div role="group" aria-label="Microsoft label choices" className="space-y-1 border-l border-[var(--workspace-divider)] pl-2">
                {labels.map((label) => (
                  <button type="button" role="menuitem" key={label}
                    onClick={() => changeLearning(currentPriority, label)} className={smallItemClass}>{label}</button>
                ))}
              </div>
            )}
            <button type="button" role="menuitem" onClick={() => changeLearning("normal")} className={smallItemClass}>Keep in inbox</button>
            <button type="button" role="menuitem" disabled title="Moving to Spam is not yet released for Microsoft" className={smallItemClass}>Move to spam</button>
            </div>
          ) : null}
        </div>, document.body,
      )}
    </div>
  );
}
