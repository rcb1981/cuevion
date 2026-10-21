import React from "react";

export type MicrosoftConnectionStatus =
  | "checking" | "connected" | "temporary_issue" | "action_required";

const presentation: Record<MicrosoftConnectionStatus, {label:string; description:string; color:string; dot:string}> = {
  checking: {
    label:"CHECKING", description:"Checking the Microsoft mailbox with Microsoft Graph.",
    color:"text-[color:rgba(98,92,84,0.78)]",
    dot:"bg-[radial-gradient(circle_at_30%_30%,rgba(228,214,178,0.98),rgba(184,163,120,0.96)_58%,rgba(132,111,72,0.96)_100%)] shadow-[0_0_0_3px_rgba(184,163,120,0.12),0_0_12px_rgba(184,163,120,0.18)]",
  },
  connected: {
    label:"CONNECTED", description:"The latest Microsoft Graph mailbox check succeeded.",
    color:"text-[color:rgba(98,92,84,0.78)]",
    dot:"bg-[radial-gradient(circle_at_30%_30%,rgba(192,225,188,0.98),rgba(118,170,112,0.96)_55%,rgba(72,118,72,0.96)_100%)] shadow-[0_0_0_3px_rgba(174,214,168,0.14),0_0_14px_rgba(142,194,132,0.24)]",
  },
  temporary_issue: {
    label:"TEMPORARY ISSUE", description:"The latest Microsoft Graph check failed. Try Sync again.",
    color:"text-[color:rgba(148,107,42,0.94)] dark:text-[color:rgba(230,198,132,0.9)]",
    dot:"bg-[radial-gradient(circle_at_30%_30%,rgba(244,213,146,0.98),rgba(205,159,76,0.96)_58%,rgba(154,108,42,0.98)_100%)] shadow-[0_0_0_3px_rgba(205,159,76,0.14),0_0_12px_rgba(205,159,76,0.22)]",
  },
  action_required: {
    label:"ACTION REQUIRED", description:"Reconnect this Microsoft mailbox in connection settings.",
    color:"text-[color:rgba(146,82,73,0.96)] dark:text-[color:rgba(244,186,168,0.9)]",
    dot:"bg-[radial-gradient(circle_at_30%_30%,rgba(236,177,165,0.98),rgba(170,103,93,0.98)_58%,rgba(126,67,60,0.98)_100%)] shadow-[0_0_0_3px_rgba(170,103,93,0.14),0_0_12px_rgba(170,103,93,0.22)]",
  },
};

export function MicrosoftConnectionState({
  status, onActionRequired,
}: {
  status: MicrosoftConnectionStatus;
  onActionRequired?: () => void;
}) {
  const p = presentation[status];
  const content = <>
    <span className={"h-2.5 w-2.5 shrink-0 rounded-full " + p.dot} />
    {p.label}
  </>;
  const className = "inline-flex items-center gap-2 text-[0.72rem] font-medium uppercase tracking-[0.16em] " + p.color;
  if (status === "action_required" && onActionRequired) {
    return <button type="button" className={className + " rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)]"}
      title={p.description} onClick={onActionRequired}>{content}</button>;
  }
  return <span role="status" title={p.description} className={className}>{content}</span>;
}

export function describeMicrosoftConnectionStatus(status: MicrosoftConnectionStatus): string {
  return presentation[status].description;
}
