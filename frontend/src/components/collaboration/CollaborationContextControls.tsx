import { useEffect, useRef, useState } from "react";

export type CollaborationContextTab = "conversation" | "email";

export function CollaborationContextTabs({ selected, onSelect }: {
  selected: CollaborationContextTab;
  onSelect: (tab: CollaborationContextTab) => void;
}) {
  const tabs = ["conversation", "email"] as const;
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  return <div role="tablist" aria-label="Collaboration context" className="flex shrink-0 gap-5 border-b border-[var(--workspace-border)]">
    {tabs.map((tab, index) => <button
      key={tab}
      ref={node => { refs.current[index] = node; }}
      id={`collaboration-tab-${tab}`}
      role="tab"
      type="button"
      aria-selected={selected === tab}
      aria-controls={`collaboration-panel-${tab}`}
      tabIndex={selected === tab ? 0 : -1}
      onClick={() => onSelect(tab)}
      onKeyDown={event => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === "Home" ? 0 : event.key === "End" ? 1 : 1 - index;
        onSelect(tabs[next]);
        refs.current[next]?.focus();
      }}
      className={`min-h-11 border-b-2 px-1 text-sm transition-colors ${selected === tab ? "border-[var(--workspace-accent-primary)] font-medium text-[var(--workspace-text)]" : "border-transparent text-[var(--workspace-text-muted)] hover:text-[var(--workspace-text)]"}`}
    >{tab === "conversation" ? "Conversation" : "Email"}</button>)}
  </div>;
}

export function CollaborationLifecycleMenu({ resolved, pending, themeMode, onTransition }: {
  resolved: boolean;
  pending: boolean;
  themeMode: "light" | "dark";
  onTransition: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const itemRef = useRef<HTMLButtonElement | null>(null);
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const resolveRef = useRef<HTMLButtonElement | null>(null);
  const confirmedRef = useRef(false);

  useEffect(() => {
    if (!menuOpen) return;
    itemRef.current?.focus();
    const dismiss = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [menuOpen]);

  useEffect(() => {
    if (!confirmOpen) return;
    confirmedRef.current = false;
    const dialog = dialogRef.current;
    dialog?.showModal();
    cancelRef.current?.focus();
    return () => { dialog?.close(); triggerRef.current?.focus(); };
  }, [confirmOpen]);

  return <div ref={rootRef} className="relative shrink-0">
    <button ref={triggerRef} type="button" aria-label="Collaboration actions" aria-haspopup="menu" aria-expanded={menuOpen} disabled={pending}
      onClick={() => setMenuOpen(open => !open)}
      onKeyDown={event => { if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); setMenuOpen(true); } }}
      className="inline-flex min-h-10 min-w-10 items-center justify-center rounded-full text-lg text-[var(--workspace-text-muted)] hover:bg-[var(--workspace-hover-surface)] disabled:opacity-60">···</button>
    {menuOpen ? <div role="menu" aria-label="Collaboration actions" className="absolute right-0 top-full z-10 w-56 rounded-2xl border border-[var(--workspace-border)] bg-[var(--workspace-menu-bg)] p-1.5 shadow-panel"
      onKeyDown={event => {
        if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setMenuOpen(false); triggerRef.current?.focus(); }
        if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) { event.preventDefault(); itemRef.current?.focus(); }
        if (event.key === "Tab") setMenuOpen(false);
      }}>
      <button ref={itemRef} role="menuitem" type="button" disabled={pending}
        onClick={() => {
          setMenuOpen(false);
          if (resolved) { triggerRef.current?.focus(); onTransition(); }
          else setConfirmOpen(true);
        }}
        className="min-h-11 w-full rounded-xl px-3 text-left text-sm text-[var(--workspace-text)] hover:bg-[var(--workspace-hover-surface)]">
        {resolved ? "Reopen collaboration" : "Resolve collaboration"}
      </button>
    </div> : null}
    {confirmOpen ? <dialog ref={dialogRef} data-theme={themeMode} aria-labelledby="collaboration-resolve-title" aria-describedby="collaboration-resolve-description" aria-modal="true"
      onCancel={event => { event.preventDefault(); setConfirmOpen(false); }}
      onKeyDown={event => {
        if (event.key === "Escape") event.stopPropagation();
        if (event.key !== "Tab") return;
        if (event.shiftKey && document.activeElement === cancelRef.current) {
          event.preventDefault();
          (pending ? cancelRef : resolveRef).current?.focus();
        } else if (!event.shiftKey && (document.activeElement === resolveRef.current || pending)) {
          event.preventDefault();
          cancelRef.current?.focus();
        }
      }}
      className="w-[calc(100%_-_2rem)] max-w-sm rounded-[24px] border border-[var(--workspace-border)] bg-[var(--workspace-modal-bg)] p-6 text-[var(--workspace-text)] shadow-panel backdrop:bg-black/40">
      <h2 id="collaboration-resolve-title" className="text-lg font-medium">Resolve this collaboration?</h2>
      <p id="collaboration-resolve-description" className="mt-2 text-sm text-[var(--workspace-text-muted)]">You can reopen it later.</p>
      <div className="mt-6 flex justify-end gap-2">
        <button ref={cancelRef} type="button" onClick={() => setConfirmOpen(false)} className="min-h-11 rounded-full border border-[var(--workspace-border)] px-4 text-sm hover:bg-[var(--workspace-hover-surface)]">Cancel</button>
        <button ref={resolveRef} type="button" disabled={pending} onClick={() => {
          if (confirmedRef.current || pending) return;
          confirmedRef.current = true;
          setConfirmOpen(false);
          onTransition();
        }} className="min-h-11 rounded-full bg-pine px-5 text-sm text-[#fbf8f2] hover:bg-moss disabled:opacity-60">Resolve</button>
      </div>
    </dialog> : null}
  </div>;
}
