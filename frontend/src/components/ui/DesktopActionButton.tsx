import type { ButtonHTMLAttributes } from "react";

export type DesktopActionButtonVariant =
  | "primary"
  | "secondary"
  | "destructive";

export interface DesktopActionButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: DesktopActionButtonVariant;
}

const baseClassName =
  "inline-flex h-9 items-center justify-center rounded-full px-4 text-[0.8125rem] font-medium tracking-normal transition-[background-color,background-image,border-color,color,transform,box-shadow] duration-150 active:scale-[0.99] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-text-soft)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--workspace-card)] disabled:pointer-events-none disabled:cursor-not-allowed disabled:scale-100 disabled:opacity-50 disabled:shadow-none";

const variantClassNames: Record<DesktopActionButtonVariant, string> = {
  primary:
    "border border-[color:rgba(66,99,69,0.52)] bg-[linear-gradient(180deg,rgba(103,141,103,0.98),rgba(69,103,72,0.98))] text-[color:rgba(251,248,242,0.98)] shadow-[inset_0_1px_0_rgba(255,255,255,0.1),0_8px_18px_rgba(66,99,69,0.12)] hover:border-[color:rgba(58,88,62,0.6)] hover:bg-[linear-gradient(180deg,rgba(93,130,95,0.98),rgba(61,95,65,0.98))]",
  secondary:
    "border border-[var(--workspace-border)] bg-[var(--workspace-card)] text-[var(--workspace-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] hover:border-[var(--workspace-border-hover)] hover:bg-[var(--workspace-hover-surface)]",
  destructive:
    "border border-[color:rgba(146,82,73,0.34)] bg-[linear-gradient(180deg,rgba(170,103,93,0.96),rgba(138,76,67,0.98))] text-[color:rgba(255,248,244,0.98)] shadow-[inset_0_1px_0_rgba(255,255,255,0.08),0_8px_18px_rgba(123,70,61,0.14)] hover:border-[color:rgba(132,72,64,0.42)] hover:bg-[linear-gradient(180deg,rgba(156,91,82,0.98),rgba(126,67,60,0.98))]",
};

export function DesktopActionButton({
  className,
  type = "button",
  variant = "secondary",
  ...buttonProps
}: DesktopActionButtonProps) {
  return (
    <button
      type={type}
      className={`${baseClassName} ${variantClassNames[variant]} ${className ?? ""}`.trim()}
      {...buttonProps}
    />
  );
}
