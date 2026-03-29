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
  onClick,
}: {
  label: string;
  description: string;
  selected: boolean;
  compact?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-3xl border text-left transition ${
        selected
          ? "border-pine bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))] text-ink shadow-panel"
          : "border-ink/10 bg-white/78 text-ink hover:border-moss/30 hover:bg-white"
      } ${compact ? "min-h-[132px] p-4" : "min-h-[156px] p-5"} outline-none focus-visible:border-pine focus-visible:bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))] focus-visible:text-ink focus-visible:shadow-panel`}
    >
      <div className="flex h-full items-start justify-between gap-4">
        <div className="space-y-2 pt-0.5">
          <div
            className={`font-semibold tracking-tight ${
              compact ? "text-base" : "text-lg"
            }`}
          >
            {label}
          </div>
          <p
            className={`max-w-xs leading-6 ${
              compact ? "text-[13px]" : "text-sm"
            } ${
              selected ? "text-ink/72" : "text-ink/58"
            }`}
          >
            {description}
          </p>
        </div>
        <span
          className={`mt-1 flex h-7 w-7 items-center justify-center rounded-full border text-sm font-semibold transition ${
            selected
              ? "border-moss bg-moss text-white"
              : "border-ink/15 bg-white/80 text-transparent"
          }`}
          aria-hidden="true"
        >
          ✓
        </span>
      </div>
    </button>
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
  return {
    optionId: role.id,
    id: role.id,
    label: role.label,
    description: role.description,
  };
}

const extraVisibleRoleAliases: VisibleRoleOption[] = [
  {
    optionId: "ceo_founder",
    id: "label_owner",
    label: "CEO / Founder",
    description: "Leadership, strategy and company direction",
  },
  {
    optionId: "marketing_manager",
    id: "promo_manager",
    label: "Marketing Manager",
    description: "Campaign planning, marketing and release outreach",
  },
  {
    optionId: "streaming_manager",
    id: "distribution",
    label: "Streaming Manager",
    description: "DSP performance, playlist strategy and streaming coordination",
  },
  {
    optionId: "distribution_manager",
    id: "distribution",
    label: "Distribution Manager",
    description: "Delivery, release logistics and distribution operations",
  },
  {
    optionId: "publishing_manager",
    id: "royalty",
    label: "Publishing Manager",
    description: "Catalog administration, statements and publishing follow-up",
  },
  {
    optionId: "sync_licensing_manager",
    id: "sync_licensing",
    label: "Sync / Licensing Manager",
    description: "Placements, licensing and sync opportunity handling",
  },
  {
    optionId: "finance_manager",
    id: "finance",
    label: "Finance Manager",
    description: "Payments, reporting and financial oversight",
  },
  {
    optionId: "legal_rights_manager",
    id: "legal",
    label: "Legal / Rights Manager",
    description: "Contracts, approvals and rights management",
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
  const secondaryMergedRoles = mergeUniqueRoles(
    secondaryDefaultVisibleRoles,
    secondaryExpandedRoles,
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

      <div className="grid gap-4 md:grid-cols-2">
        {primaryVisibleRoles.map((role) => (
          <RoleCard
            key={role.optionId}
            label={role.label}
            description={role.description}
            selected={isVisibleRoleSelected(primaryRole, selectedPrimaryOptionId, role)}
            onClick={() => {
              setShowPrimaryExtraRoles(false);
              setSelectedPrimaryOptionId(role.optionId);
              onPrimaryChange(role.id);
            }}
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
            ? onboardingText.roles.hideMoreRoles
            : onboardingText.roles.showMoreRoles}
        </button>
      </div>

      {showPrimaryExtraRoles ? (
        <div className="grid gap-3 md:grid-cols-2">
          {visibleExtraRoleOptions.map((role) => (
            <RoleCard
              key={role.optionId}
              label={role.label}
              description={role.description}
              selected={isVisibleRoleSelected(primaryRole, selectedPrimaryOptionId, role)}
              compact
              onClick={() => {
                setSelectedPrimaryOptionId(role.optionId);
                onPrimaryChange(role.id);
                setShowPrimaryExtraRoles(false);
                scrollToPrimaryGrid();
              }}
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
          <div className="mt-5 grid gap-4 md:grid-cols-2">
            <button
              type="button"
              onClick={() => onSecondaryChange(null)}
              className={`rounded-3xl border p-4 text-left transition ${
                secondaryRole === null
                  ? "border-pine bg-[linear-gradient(180deg,rgba(226,236,229,0.72),rgba(255,255,255,0.98))] text-ink"
                  : "border-ink/10 bg-white/70 text-ink hover:border-moss/30 hover:bg-white"
              } outline-none focus-visible:border-pine focus-visible:bg-[linear-gradient(180deg,rgba(226,236,229,0.92),rgba(246,249,246,0.98))] focus-visible:text-ink focus-visible:shadow-panel`}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="space-y-2">
                  <div className="text-base font-semibold tracking-tight">
                    {onboardingText.roles.noSecondaryRole}
                  </div>
                  <p className="max-w-xs text-[13px] leading-6 text-ink/58">
                    {onboardingText.roles.noSecondaryRoleDescription}
                  </p>
                </div>
                <span
                  className={`mt-1 flex h-7 w-7 items-center justify-center rounded-full border text-sm font-semibold ${
                    secondaryRole === null
                      ? "border-moss bg-moss text-white"
                      : "border-ink/15 bg-white/80 text-transparent"
                  }`}
                  aria-hidden="true"
                >
                  ✓
                </span>
              </div>
            </button>
            {secondaryMergedRoles.map((role) => (
              <RoleCard
                key={role.optionId}
                label={role.label}
                description={role.description}
                selected={isVisibleRoleSelected(secondaryRole, selectedSecondaryOptionId, role)}
                compact
                onClick={() => {
                  setSelectedSecondaryOptionId(role.optionId);
                  onSecondaryChange(role.id);
                }}
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
                ? onboardingText.roles.hideMoreRoles
                : onboardingText.roles.showMoreRoles}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
