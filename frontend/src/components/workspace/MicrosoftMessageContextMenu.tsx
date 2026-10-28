import React, { useState } from "react";
import type { LiveInboxMessageSnapshot } from "../../lib/inboxConnectionApi";
import type { MicrosoftLearningLabel, MicrosoftLearningRule } from "./microsoftLearningRules";

const labels: MicrosoftLearningLabel[] = ["Promo", "Update", "Finance", "Business", "Demo", "Other"];
const itemClass =
  "flex w-full items-center rounded-[14px] px-3 py-2.5 text-left text-[0.82rem] text-[var(--workspace-text)] transition-[background-color,color] duration-150 hover:bg-[var(--workspace-menu-hover)] disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent";
const smallItemClass =
  "flex w-full items-center rounded-[14px] px-3 py-[0.42rem] text-left text-[0.76rem] text-[var(--workspace-text)] transition-[background-color,color] duration-150 hover:bg-[var(--workspace-menu-hover)] disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent";

/**
 * Same visible menu hierarchy as the Gmail/IMAP message menu. The menu never
 * bypasses the Microsoft-only release gates: unreleased actions stay disabled.
 * Its target is the row that was actually right-clicked, not the reader's
 * previously selected message.
 */
export function MicrosoftMessageContextMenu({
  message, x, y, busy, canChangeInbox, canLearn, learningRule,
  onOpen, onReadState, onArchive, onDelete, onSaveLearning,
}: {
  message: LiveInboxMessageSnapshot;
  x: number;
  y: number;
  busy: boolean;
  canChangeInbox: boolean;
  canLearn: boolean;
  learningRule?: MicrosoftLearningRule;
  onOpen: () => void;
  onReadState: (nextUnread: boolean) => void;
  onArchive: () => void;
  onDelete: () => void;
  onSaveLearning: (rule: MicrosoftLearningRule) => void;
}) {
  const [submenu, setSubmenu] = useState<"move" | "learning" | null>(null);
  const [labelChooserOpen, setLabelChooserOpen] = useState(false);
  const currentPriority = learningRule?.priority ?? "normal";
  const currentLabel = learningRule?.label ?? "Other";
  const changeLearning = (priority: MicrosoftLearningRule["priority"], label: MicrosoftLearningLabel = currentLabel) => {
    if (canLearn && !busy) onSaveLearning({ priority, label });
  };

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
          onClick={() => { setSubmenu(submenu === "move" ? null : "move"); setLabelChooserOpen(false); }}
          onMouseEnter={() => { setSubmenu("move"); setLabelChooserOpen(false); }}
          className={itemClass + " justify-between"}>Move to... <span aria-hidden="true">›</span></button>
        {submenu === "move" && (
          <div role="menu" aria-label="Microsoft move destinations" className="space-y-1 border-l border-[var(--workspace-divider)] pl-2">
            <button type="button" role="menuitem" disabled={!canChangeInbox || busy} onClick={onArchive} className={smallItemClass}>Archive</button>
            <button type="button" role="menuitem" disabled={!canChangeInbox || busy} onClick={onDelete} className={smallItemClass}>Trash</button>
            <button type="button" role="menuitem" disabled title="Moving to Spam is not yet released for Microsoft" className={smallItemClass}>Spam</button>
          </div>
        )}
        <button type="button" role="menuitem" aria-haspopup="menu" aria-expanded={submenu === "learning"}
          disabled={!canLearn || busy}
          onClick={() => { setSubmenu(submenu === "learning" ? null : "learning"); setLabelChooserOpen(false); }}
          onMouseEnter={() => { if (canLearn && !busy) { setSubmenu("learning"); setLabelChooserOpen(false); } }}
          className={itemClass + " justify-between"}>Learning <span aria-hidden="true">›</span></button>
        {submenu === "learning" && canLearn && !busy && (
          <div role="menu" aria-label="Microsoft Learning options" className="space-y-1 border-l border-[var(--workspace-divider)] pl-2">
            <button type="button" role="menuitem" onClick={() => changeLearning("important")} className={smallItemClass}>Mark as important</button>
            <button type="button" role="menuitem" onClick={() => changeLearning("normal")} className={smallItemClass}>Mark as not important</button>
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
        )}
      </div>
    </div>
  );
}
