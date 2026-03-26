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
  baseRoles: Array<{ id: RoleId; label: string; description: string }>,
  selectedExtraRole: { id: RoleId; label: string; description: string } | null,
) {
  if (!selectedExtraRole) {
    return baseRoles;
  }

  return [selectedExtraRole, ...baseRoles].slice(0, baseRoles.length);
}

function mergeUniqueRoles(
  ...roleGroups: Array<Array<{ id: RoleId; label: string; description: string }>>
) {
  return [...new Map(roleGroups.flat().map((role) => [role.id, role])).values()];
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
  const primarySectionRef = useRef<HTMLElement | null>(null);

  const primaryExtraRole = useMemo(
    () => extraRoleOptions.find((role) => role.id === primaryRole) ?? null,
    [primaryRole],
  );
  const secondaryExtraRole = useMemo(
    () => extraRoleOptions.find((role) => role.id === secondaryRole) ?? null,
    [secondaryRole],
  );

  const secondaryOptions = allRoleOptions.filter((role) => role.id !== primaryRole);
  const primaryVisibleRoles = buildVisibleRoleGrid(
    primaryRoleOptions,
    primaryExtraRole,
  );
  const secondaryBaseRoles = secondaryRoleOptions.filter(
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
        extraRoleOptions.some((extraRole) => extraRole.id === role.id),
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
            key={role.id}
            label={role.label}
            description={role.description}
            selected={primaryRole === role.id}
            onClick={() => {
              setShowPrimaryExtraRoles(false);
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
          {extraRoleOptions.map((role) => (
            <RoleCard
              key={role.id}
              label={role.label}
              description={role.description}
              selected={primaryRole === role.id}
              compact
              onClick={() => {
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
                key={role.id}
                label={role.label}
                description={role.description}
                selected={secondaryRole === role.id}
                compact
                onClick={() => onSecondaryChange(role.id)}
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
