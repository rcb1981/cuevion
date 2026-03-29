import {
  allRoleOptions,
  extraRoleOptions,
  primaryRoleOptions,
  secondaryRoleOptions,
} from "../../data/onboardingOptions";
import { onboardingText } from "../../copy/onboardingCopy";
import type { RoleId } from "../../types/onboarding";
import { useMemo, useRef, useState } from "react";

interface StepRoleSelectionProps {
  primaryRole: RoleId | null;
  secondaryRole: RoleId | null;
  onPrimaryChange: (role: RoleId) => void;
  onSecondaryChange: (role: RoleId | null) => void;
}

type VisibleRoleOption = {
  optionId: string;
  id: RoleId;
  label: string;
  description: string;
};

function RoleCard({
  label,
  description,
  selected,
  compact = false,
  infoOpen = false,
  onClick,
  onToggleInfo,
}: {
  label: string;
  description: string;
  selected: boolean;
  compact?: boolean;
  infoOpen?: boolean;
  onClick: () => void;
  onToggleInfo: () => void;
}) {
  return (
    <div
      className={`rounded-3xl border text-left transition ${
        selected
          ? "border-pine bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))] text-ink shadow-panel"
          : "border-ink/10 bg-white/78 text-ink hover:border-moss/30 hover:bg-white"
      } ${compact ? "px-3 py-3" : "px-4 py-3.5"}`}
    >
      <div className="flex items-start gap-3">
        <button
          type="button"
          onClick={onClick}
          className="flex min-w-0 flex-1 items-start gap-3 rounded-[18px] text-left outline-none focus-visible:text-ink"
        >
          <span
            className={`mt-0.5 flex h-6 w-6 flex-none items-center justify-center rounded-full border text-xs font-semibold transition ${
              selected
                ? "border-moss bg-moss text-white"
                : "border-ink/15 bg-white/80 text-transparent"
            }`}
            aria-hidden="true"
          >
            ✓
          </span>
          <span className="min-w-0 flex-1 pt-0.5">
            <span
              className={`block font-semibold tracking-tight ${
                compact ? "text-[0.95rem]" : "text-base"
              }`}
            >
              {label}
            </span>
          </span>
        </button>
        <button
          type="button"
          onClick={onToggleInfo}
          aria-label={`More info about ${label}`}
          className={`mt-0.5 inline-flex h-6 w-6 flex-none items-center justify-center rounded-full border text-[0.72rem] font-semibold transition ${
            infoOpen
              ? "border-pine bg-[rgba(226,236,229,0.92)] text-pine"
              : "border-ink/12 bg-white/70 text-ink/58 hover:border-moss/24 hover:text-pine"
          }`}
        >
          i
        </button>
      </div>
      {infoOpen ? (
        <p
          className={`pl-9 pr-1 pt-2 leading-6 ${
            compact ? "text-[13px]" : "text-sm"
          } ${selected ? "text-ink/72" : "text-ink/58"}`}
        >
          {description}
        </p>
      ) : null}
    </div>
  );
}

function RoleGroup({
  title,
  roles,
  selectedRole,
  selectedOptionId,
  infoOpenId,
  compact = false,
  onSelect,
  onToggleInfo,
}: {
  title: string;
  roles: VisibleRoleOption[];
  selectedRole: RoleId | null;
  selectedOptionId: string | null;
  infoOpenId: string | null;
  compact?: boolean;
  onSelect: (role: VisibleRoleOption) => void;
  onToggleInfo: (optionId: string) => void;
}) {
  if (roles.length === 0) {
    return null;
  }

  return (
    <div className="space-y-2.5">
      <div className="px-1 text-[0.68rem] font-medium uppercase tracking-[0.16em] text-ink/44">
        {title}
      </div>
      <div className="grid gap-2 md:grid-cols-2">
        {roles.map((role) => (
          <RoleCard
            key={role.optionId}
            label={role.label}
            description={role.description}
            selected={isVisibleRoleSelected(selectedRole, selectedOptionId, role)}
            infoOpen={infoOpenId === role.optionId}
            compact={compact}
            onClick={() => onSelect(role)}
            onToggleInfo={() => onToggleInfo(role.optionId)}
          />
        ))}
      </div>
    </div>
  );
}

function getExpandedRoleGroup(role: VisibleRoleOption) {
  switch (role.optionId) {
    case "artist":
    case "songwriter":
    case "mixing_mastering_engineer":
      return "Creative";
    case "social_media_manager":
    case "label_owner":
    case "promo_manager":
    case "admin":
      return "Business";
    case "finance":
    case "legal":
      return "Finance & Legal";
    case "streaming_manager":
    case "distribution":
    case "royalty":
    case "sync_licensing":
      return "Distribution / Publishing / Sync";
    default:
      return "Other";
  }
}

function buildRoleGroups(roles: VisibleRoleOption[]) {
  const groupOrder = [
    "Creative",
    "Business",
    "Finance & Legal",
    "Distribution / Publishing / Sync",
    "Other",
  ] as const;

  return groupOrder
    .map((title) => ({
      title,
      roles: roles.filter((role) => getExpandedRoleGroup(role) === title),
    }))
    .filter((group) => group.roles.length > 0);
}

function NoSecondaryRoleRow({
  selected,
  infoOpen,
  onClick,
  onToggleInfo,
}: {
  selected: boolean;
  infoOpen: boolean;
  onClick: () => void;
  onToggleInfo: () => void;
}) {
  return (
    <div
      className={`rounded-3xl border px-4 py-3.5 transition ${
        selected
          ? "border-pine bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))] text-ink shadow-panel"
          : "border-ink/10 bg-white/78 text-ink hover:border-moss/30 hover:bg-white"
      }`}
    >
      <div className="flex items-start gap-3">
        <button
          type="button"
          onClick={onClick}
          className="flex min-w-0 flex-1 items-start gap-3 rounded-[18px] text-left outline-none"
        >
          <span
            className={`mt-0.5 flex h-6 w-6 flex-none items-center justify-center rounded-full border text-xs font-semibold ${
              selected
                ? "border-moss bg-moss text-white"
                : "border-ink/15 bg-white/80 text-transparent"
            }`}
            aria-hidden="true"
          >
            ✓
          </span>
          <span className="min-w-0 flex-1 pt-0.5">
            <span className="block text-base font-semibold tracking-tight">
              {onboardingText.roles.noSecondaryRole}
            </span>
          </span>
        </button>
        <button
          type="button"
          onClick={onToggleInfo}
          aria-label="More info about no secondary role"
          className={`mt-0.5 inline-flex h-6 w-6 flex-none items-center justify-center rounded-full border text-[0.72rem] font-semibold transition ${
            infoOpen
              ? "border-pine bg-[rgba(226,236,229,0.92)] text-pine"
              : "border-ink/12 bg-white/70 text-ink/58 hover:border-moss/24 hover:text-pine"
          }`}
        >
          i
        </button>
      </div>
      {infoOpen ? (
        <p className={`pl-9 pr-1 pt-2 text-sm leading-6 ${selected ? "text-ink/72" : "text-ink/58"}`}>
          {onboardingText.roles.noSecondaryRoleDescription}
        </p>
      ) : null}
    </div>
  );
}

function buildVisibleRoleGrid(
  baseRoles: VisibleRoleOption[],
  selectedExtraRole: VisibleRoleOption | null,
) {
  if (!selectedExtraRole) {
    return baseRoles;
  }

  return [selectedExtraRole, ...baseRoles].slice(0, baseRoles.length);
}

function mergeUniqueRoles(
  ...roleGroups: VisibleRoleOption[][]
) {
  return [...new Map(roleGroups.flat().map((role) => [role.optionId, role])).values()];
}

function toVisibleRoleOption(role: {
  id: RoleId;
  label: string;
  description: string;
}): VisibleRoleOption {
  switch (role.id) {
    case "label_owner":
      return {
        optionId: role.id,
        id: role.id,
        label: "CEO / Founder",
        description: "Leadership, strategy and company direction",
      };
    case "promo_manager":
      return {
        optionId: role.id,
        id: role.id,
        label: "Marketing Manager",
        description: "Campaign planning, marketing and release outreach",
      };
    case "distribution":
      return {
        optionId: role.id,
        id: role.id,
        label: "Distribution Manager",
        description: "Delivery, release logistics and distribution operations",
      };
    case "royalty":
      return {
        optionId: role.id,
        id: role.id,
        label: "Publishing Manager",
        description: "Catalog administration, statements and publishing follow-up",
      };
    case "sync_licensing":
      return {
        optionId: role.id,
        id: role.id,
        label: "Sync / Licensing Manager",
        description: "Placements, licensing and sync opportunity handling",
      };
    case "finance":
      return {
        optionId: role.id,
        id: role.id,
        label: "Finance Manager",
        description: "Payments, reporting and financial oversight",
      };
    case "legal":
      return {
        optionId: role.id,
        id: role.id,
        label: "Legal / Rights Manager",
        description: "Contracts, approvals and rights management",
      };
    default:
      return {
        optionId: role.id,
        id: role.id,
        label: role.label,
        description: role.description,
      };
  }
}

const extraVisibleRoleAliases: VisibleRoleOption[] = [
  {
    optionId: "streaming_manager",
    id: "distribution",
    label: "Streaming Manager",
    description: "DSP performance, playlist strategy and streaming coordination",
  },
  {
    optionId: "artist",
    id: "dj",
    label: "Artist",
    description: "Artist workflow, releases, promo and live activity",
  },
  {
    optionId: "songwriter",
    id: "producer",
    label: "Songwriter",
    description: "Writing, sessions and creative development",
  },
  {
    optionId: "mixing_mastering_engineer",
    id: "producer",
    label: "Mixing / Mastering Engineer",
    description: "Mix, master and delivery-ready production work",
  },
];

function isVisibleRoleSelected(
  selectedRole: RoleId | null,
  selectedOptionId: string | null,
  role: VisibleRoleOption,
) {
  if (selectedRole !== role.id) {
    return false;
  }

  if (selectedOptionId) {
    return selectedOptionId === role.optionId;
  }

  return role.optionId === role.id;
}

function isOverlappingMusicRole(primaryRole: RoleId | null, secondaryRole: RoleId) {
  if (primaryRole === "label_ar_manager") {
    return (
      secondaryRole === "label_ar_manager" ||
      secondaryRole === "label_manager" ||
      secondaryRole === "ar_manager"
    );
  }

  if (primaryRole === "label_manager") {
    return (
      secondaryRole === "label_manager" ||
      secondaryRole === "label_ar_manager"
    );
  }

  if (primaryRole === "ar_manager") {
    return (
      secondaryRole === "ar_manager" ||
      secondaryRole === "label_ar_manager"
    );
  }

  if (primaryRole === "dj") {
    return secondaryRole === "dj" || secondaryRole === "dj_producer";
  }

  if (primaryRole === "producer") {
    return secondaryRole === "producer" || secondaryRole === "dj_producer";
  }

  if (primaryRole === "dj_producer") {
    return (
      secondaryRole === "dj" ||
      secondaryRole === "producer" ||
      secondaryRole === "dj_producer"
    );
  }

  return false;
}

export function StepRoleSelection({
  primaryRole,
  secondaryRole,
  onPrimaryChange,
  onSecondaryChange,
}: StepRoleSelectionProps) {
  const [showPrimaryExtraRoles, setShowPrimaryExtraRoles] = useState(false);
  const [showSecondaryExtraRoles, setShowSecondaryExtraRoles] = useState(false);
  const [selectedPrimaryOptionId, setSelectedPrimaryOptionId] = useState<string | null>(null);
  const [selectedSecondaryOptionId, setSelectedSecondaryOptionId] = useState<string | null>(null);
  const [openPrimaryInfoId, setOpenPrimaryInfoId] = useState<string | null>(null);
  const [openSecondaryInfoId, setOpenSecondaryInfoId] = useState<string | null>(null);
  const primarySectionRef = useRef<HTMLElement | null>(null);
  const visiblePrimaryRoleOptions = useMemo(
    () => primaryRoleOptions.map(toVisibleRoleOption),
    [],
  );
  const visibleSecondaryRoleOptions = useMemo(
    () => secondaryRoleOptions.map(toVisibleRoleOption),
    [],
  );
  const visibleExtraRoleOptions = useMemo(
    () => [...extraRoleOptions.map(toVisibleRoleOption), ...extraVisibleRoleAliases],
    [],
  );
  const visibleAllRoleOptions = useMemo(
    () => [...allRoleOptions.map(toVisibleRoleOption), ...extraVisibleRoleAliases],
    [],
  );

  const primaryExtraRole = useMemo(
    () =>
      (selectedPrimaryOptionId
        ? visibleExtraRoleOptions.find((role) => role.optionId === selectedPrimaryOptionId)
        : null) ??
      visibleExtraRoleOptions.find((role) => role.optionId === primaryRole) ??
      null,
    [primaryRole, selectedPrimaryOptionId, visibleExtraRoleOptions],
  );
  const secondaryExtraRole = useMemo(
    () =>
      (selectedSecondaryOptionId
        ? visibleExtraRoleOptions.find((role) => role.optionId === selectedSecondaryOptionId)
        : null) ??
      visibleExtraRoleOptions.find((role) => role.optionId === secondaryRole) ??
      null,
    [secondaryRole, selectedSecondaryOptionId, visibleExtraRoleOptions],
  );

  const secondaryOptions = visibleAllRoleOptions.filter((role) => role.id !== primaryRole);
  const primaryVisibleRoles = buildVisibleRoleGrid(
    visiblePrimaryRoleOptions,
    primaryExtraRole,
  );
  const secondaryBaseRoles = visibleSecondaryRoleOptions.filter(
    (role) =>
      role.id !== primaryRole && !isOverlappingMusicRole(primaryRole, role.id),
  );
  const secondaryDefaultVisibleRoles = buildVisibleRoleGrid(
    secondaryBaseRoles,
    secondaryExtraRole &&
      !isOverlappingMusicRole(primaryRole, secondaryExtraRole.id)
      ? secondaryExtraRole
      : null,
  );
  const secondaryExpandedRoles = showSecondaryExtraRoles
    ? secondaryOptions.filter(
        (role) =>
          !isOverlappingMusicRole(primaryRole, role.id) &&
          visibleExtraRoleOptions.some((extraRole) => extraRole.id === role.id),
      )
    : [];
  const primaryExpandedRoleGroups = buildRoleGroups(
    visibleExtraRoleOptions.filter((role) => role.optionId !== primaryExtraRole?.optionId),
  );
  const secondaryExpandedRoleGroups = buildRoleGroups(
    mergeUniqueRoles(secondaryExpandedRoles).filter(
      (role) => role.optionId !== secondaryExtraRole?.optionId,
    ),
  );

  const scrollToPrimaryGrid = () => {
    window.requestAnimationFrame(() => {
      const elementTop = primarySectionRef.current?.getBoundingClientRect().top;

      if (elementTop === undefined) {
        return;
      }

      const offsetTop = window.scrollY + elementTop - 80;

      window.scrollTo({
        top: Math.max(offsetTop, 0),
        behavior: "smooth",
      });
    });
  };

  return (
    <section ref={primarySectionRef} className="space-y-8">
      <div className="space-y-3">
        <h2 className="text-3xl font-semibold tracking-tight text-ink">
          {onboardingText.roles.title}
        </h2>
        <p className="text-base text-ink/68">{onboardingText.roles.primaryRequired}</p>
      </div>

      <div className="grid gap-2 md:grid-cols-2">
        {primaryVisibleRoles.map((role) => (
          <RoleCard
            key={role.optionId}
            label={role.label}
            description={role.description}
            selected={isVisibleRoleSelected(primaryRole, selectedPrimaryOptionId, role)}
            infoOpen={openPrimaryInfoId === role.optionId}
            onClick={() => {
              setShowPrimaryExtraRoles(false);
              setSelectedPrimaryOptionId(role.optionId);
              onPrimaryChange(role.id);
            }}
            onToggleInfo={() =>
              setOpenPrimaryInfoId((current) =>
                current === role.optionId ? null : role.optionId,
              )
            }
          />
        ))}
      </div>

      <div className="space-y-3 py-1">
        <button
          type="button"
          onClick={() => setShowPrimaryExtraRoles((current) => !current)}
          className="inline-flex w-fit rounded-full border border-ink/10 bg-white/55 px-4 py-2 text-sm font-medium text-moss transition hover:border-moss/20 hover:bg-white hover:text-pine"
        >
          {showPrimaryExtraRoles
            ? "Hide roles"
            : "Show all roles"}
        </button>
      </div>

      {showPrimaryExtraRoles ? (
        <div className="space-y-5">
          {primaryExpandedRoleGroups.map((group) => (
            <RoleGroup
              key={group.title}
              title={group.title}
              roles={group.roles}
              selectedRole={primaryRole}
              selectedOptionId={selectedPrimaryOptionId}
              infoOpenId={openPrimaryInfoId}
              compact
              onSelect={(role) => {
                setSelectedPrimaryOptionId(role.optionId);
                onPrimaryChange(role.id);
                setShowPrimaryExtraRoles(false);
                scrollToPrimaryGrid();
              }}
              onToggleInfo={(optionId) =>
                setOpenPrimaryInfoId((current) => (current === optionId ? null : optionId))
              }
            />
          ))}
        </div>
      ) : null}

      {primaryRole ? (
        <div className="space-y-3 border-t border-ink/8 pt-6">
          <div className="space-y-1">
            <h3 className="text-xl font-semibold text-ink">
              {onboardingText.roles.secondaryTitle}
            </h3>
            <p className="text-sm text-ink/68">{onboardingText.roles.secondaryOptional}</p>
          </div>
          <div className="mt-5 grid gap-2 md:grid-cols-2">
            <div className="md:col-span-2">
              <NoSecondaryRoleRow
                selected={secondaryRole === null}
                infoOpen={openSecondaryInfoId === "no_secondary"}
                onClick={() => onSecondaryChange(null)}
                onToggleInfo={() =>
                  setOpenSecondaryInfoId((current) =>
                    current === "no_secondary" ? null : "no_secondary",
                  )
                }
              />
            </div>
            {secondaryDefaultVisibleRoles.map((role) => (
              <RoleCard
                key={role.optionId}
                label={role.label}
                description={role.description}
                selected={isVisibleRoleSelected(secondaryRole, selectedSecondaryOptionId, role)}
                infoOpen={openSecondaryInfoId === role.optionId}
                compact
                onClick={() => {
                  setSelectedSecondaryOptionId(role.optionId);
                  onSecondaryChange(role.id);
                }}
                onToggleInfo={() =>
                  setOpenSecondaryInfoId((current) =>
                    current === role.optionId ? null : role.optionId,
                  )
                }
              />
            ))}
          </div>

          <div className="space-y-3 py-2">
            <button
              type="button"
              onClick={() => setShowSecondaryExtraRoles((current) => !current)}
              className="inline-flex w-fit rounded-full border border-ink/10 bg-white/55 px-4 py-2 text-sm font-medium text-moss transition hover:border-moss/20 hover:bg-white hover:text-pine"
            >
              {showSecondaryExtraRoles
                ? "Hide roles"
                : "Show all roles"}
            </button>
          </div>
          {showSecondaryExtraRoles ? (
            <div className="space-y-5">
              {secondaryExpandedRoleGroups.map((group) => (
                <RoleGroup
                  key={group.title}
                  title={group.title}
                  roles={group.roles}
                  selectedRole={secondaryRole}
                  selectedOptionId={selectedSecondaryOptionId}
                  infoOpenId={openSecondaryInfoId}
                  compact
                  onSelect={(role) => {
                    setSelectedSecondaryOptionId(role.optionId);
                    onSecondaryChange(role.id);
                  }}
                  onToggleInfo={(optionId) =>
                    setOpenSecondaryInfoId((current) =>
                      current === optionId ? null : optionId,
                    )
                  }
                />
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
