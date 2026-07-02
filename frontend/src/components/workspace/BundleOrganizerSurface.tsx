const organizerModules = [
  {
    title: "Priority",
    description: "High-intent demos, promos, and follow-ups gathered for review.",
  },
  {
    title: "Shortlist",
    description: "A focused holding area for music that is worth a closer listen.",
  },
  {
    title: "Demo Inbox",
    description: "Incoming demos will appear here in the bundle-managed experience.",
  },
  {
    title: "Promo Inbox",
    description: "Promo sends and campaign updates will live here when the pilot expands.",
  },
  {
    title: "Settings",
    description: "Organizer preferences will be managed from the shared workspace.",
  },
] as const;

export function BundleOrganizerSurface() {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-1 md:px-2 md:pb-2">
      <section className="min-h-full rounded-[28px] border border-[color:rgba(218,194,142,0.16)] bg-[radial-gradient(circle_at_20%_0%,rgba(238,224,190,0.12),transparent_34%),linear-gradient(180deg,rgba(11,30,25,0.94),rgba(6,18,15,0.98))] p-5 shadow-panel md:p-7">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
          <div className="max-w-3xl">
            <span className="inline-flex items-center rounded-full border border-[color:rgba(218,194,142,0.22)] bg-[color:rgba(238,224,190,0.09)] px-3 py-1 text-[0.68rem] font-medium uppercase tracking-[0.18em] text-[color:rgba(232,222,198,0.76)]">
              Bundle Pilot
            </span>
            <h1 className="mt-5 text-3xl font-semibold tracking-[0.01em] text-[var(--workspace-foreground)] md:text-4xl">
              Demo &amp; Promo Organizer
            </h1>
            <p className="mt-3 max-w-2xl text-base leading-7 text-[var(--workspace-muted)]">
              A dedicated music workflow for demos, promos, and active follow-ups.
            </p>
          </div>
          <div className="w-full rounded-[22px] border border-[color:rgba(218,194,142,0.16)] bg-[color:rgba(238,224,190,0.07)] p-4 shadow-[inset_0_1px_0_rgba(255,248,226,0.08)] lg:max-w-sm">
            <p className="text-sm font-medium text-[var(--workspace-foreground)]">
              Inbox access is managed by Cuevion Workspace.
            </p>
            <p className="mt-2 text-sm leading-6 text-[var(--workspace-muted)]">
              Connected inboxes will be shared with Organizer in the bundle experience.
            </p>
          </div>
        </div>

        <div className="mt-8 grid gap-3 md:grid-cols-2 xl:grid-cols-5">
          {organizerModules.map((module) => (
            <article
              key={module.title}
              className="rounded-[22px] border border-[color:rgba(218,194,142,0.14)] bg-[linear-gradient(180deg,rgba(238,224,190,0.08),rgba(33,58,49,0.24))] p-4 shadow-[inset_0_1px_0_rgba(255,248,226,0.08)]"
            >
              <h2 className="text-sm font-semibold text-[var(--workspace-foreground)]">
                {module.title}
              </h2>
              <p className="mt-2 text-sm leading-6 text-[var(--workspace-muted)]">
                {module.description}
              </p>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
