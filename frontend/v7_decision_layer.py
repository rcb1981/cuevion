from dataclasses import dataclass, asdict
from typing import List, Dict, Any

from v7_config import (
    UserPreferences,
    UserConfig,
    MailboxConfig,
    EngineResult,
    PriorityLevel,
    VisibilityMode,
    BASE_CATEGORY_VISIBILITY,
    HARD_RULES,
    get_role_weight,
    get_inbox_profile_weight,
)


@dataclass
class DecisionExplanation:
    raw_engine_category: str
    raw_engine_priority: PriorityLevel
    role_adjustments: List[str]
    inbox_adjustments: List[str]
    preference_adjustments: List[str]
    hard_rule_adjustments: List[str]
    final_summary: str


@dataclass
class FinalDecision:
    final_category: str
    final_priority: PriorityLevel
    final_visibility: VisibilityMode
    action: str
    explanation: DecisionExplanation


def priority_to_score(priority: PriorityLevel) -> int:
    mapping = {
        "LOW": 0,
        "NORMAL": 1,
        "REVIEW": 2,
        "PRIORITY": 3,
    }
    return mapping.get(priority, 1)


def score_to_priority(score: int) -> PriorityLevel:
    if score >= 3:
        return "PRIORITY"
    if score == 2:
        return "REVIEW"
    if score <= 0:
        return "LOW"
    return "NORMAL"


def shift_visibility(current: VisibilityMode, direction: int) -> VisibilityMode:
    scale = ["hide", "show_low", "show_normal", "show_priority"]

    if current == "delete":
        return "delete"

    try:
        idx = scale.index(current)
    except ValueError:
        idx = 2

    new_idx = max(0, min(len(scale) - 1, idx + direction))
    return scale[new_idx]  # type: ignore


def max_visibility(a: VisibilityMode, b: VisibilityMode) -> VisibilityMode:
    rank = {
        "hide": 0,
        "show_low": 1,
        "show_normal": 2,
        "show_priority": 3,
        "delete": -1,
    }
    return a if rank[a] >= rank[b] else b


def min_visibility(a: VisibilityMode, b: VisibilityMode) -> VisibilityMode:
    rank = {
        "hide": 0,
        "show_low": 1,
        "show_normal": 2,
        "show_priority": 3,
        "delete": -1,
    }
    return a if rank[a] <= rank[b] else b


def apply_preference_override(
    category: str,
    current_visibility: VisibilityMode,
    preferences: UserPreferences,
    mailbox_profile: str,
) -> tuple[VisibilityMode, List[str]]:
    notes: List[str] = []
    visibility = current_visibility

    if category == "promo_reminder":
        if mailbox_profile == "business_mixed":
            visibility = min_visibility(
            preferences.promo_reminders_mode,
            "show_low"
            )
        else:
            visibility = preferences.promo_reminders_mode

        notes.append(f"promo reminders preference -> {visibility}")

    elif category == "business_reminder":
        visibility = preferences.business_reminders_mode
        notes.append(f"business reminders preference -> {visibility}")

    elif category in {"royalty_statement", "finance"}:
        visibility = preferences.finance_visibility
        notes.append(f"finance visibility preference -> {visibility}")

    elif category == "distributor_update":
        visibility = preferences.distributor_visibility
        notes.append(f"distributor visibility preference -> {visibility}")

    elif category == "bulk_demo":
        visibility = preferences.bulk_demo_mode
        notes.append(f"bulk demo preference -> {visibility}")

    elif category == "promo":
        if mailbox_profile == "business_mixed":
            visibility = max_visibility(
            current_visibility,
            preferences.promos_in_business_inbox_mode,
        )
        notes.append(
            f"promos in business inbox preference applied -> {visibility}")

    return visibility, notes


def visibility_to_action(visibility: VisibilityMode) -> str:
    mapping = {
        "show_priority": "show_in_priority",
        "show_normal": "show_in_main_feed",
        "show_low": "show_in_quiet_view",
        "hide": "archive_candidate",
        "delete": "delete_or_archive",
    }
    return mapping.get(visibility, "show_in_main_feed")


def is_demo_like(category: str) -> bool:
    return category in {"demo", "high_priority_demo", "bulk_demo", "weak_demo"}


def is_business_like(category: str) -> bool:
    return category in {
        "business",
        "business_reminder",
        "royalty_statement",
        "distributor_update",
        "finance",
    }


def decide_message_behavior(
    engine_result: EngineResult,
    user_config: UserConfig,
    mailbox_config: MailboxConfig,
    internal_role: str | None = None,
) -> FinalDecision:
    category = engine_result.category or "unknown"
    base_visibility = BASE_CATEGORY_VISIBILITY.get(category, "show_low")

    explanation = DecisionExplanation(
        raw_engine_category=category,
        raw_engine_priority=engine_result.priority,
        role_adjustments=[],
        inbox_adjustments=[],
        preference_adjustments=[],
        hard_rule_adjustments=[],
        final_summary="",
    )

    base_score = priority_to_score(engine_result.priority)

    # -----------------------------------------------------
    # HARD RULE: replies always priority
    # -----------------------------------------------------
    if HARD_RULES["replies_always_priority"] and category == "reply":
        final_priority = "PRIORITY"
        final_visibility = "show_priority"
        explanation.hard_rule_adjustments.append("reply forced to priority")
        explanation.final_summary = "Reply thread is always surfaced as priority."
        return FinalDecision(
            final_category=engine_result.category,
            final_priority=final_priority,
            final_visibility=final_visibility,
            action=visibility_to_action(final_visibility),
            explanation=explanation,
        )

    # -----------------------------------------------------
    # HARD RULE: workflow links stay visible
    # -----------------------------------------------------
    if HARD_RULES["workflow_links_always_visible"] and engine_result.workflow_links:
        base_visibility = max_visibility(base_visibility, "show_normal")
        explanation.hard_rule_adjustments.append("workflow links kept visible")

    role_weight = get_role_weight(user_config.role, category)
    if role_weight != 0:
        explanation.role_adjustments.append(
            f"role '{user_config.role}' adjusted '{category}' by {role_weight}"
        )

    inbox_weight = get_inbox_profile_weight(mailbox_config.inbox_profile, category)
    if inbox_weight != 0:
        explanation.inbox_adjustments.append(
            f"inbox profile '{mailbox_config.inbox_profile}' adjusted '{category}' by {inbox_weight}"
        )

    total_weight = role_weight + inbox_weight

    if total_weight >= 4:
        priority_shift = 1
        visibility_shift = 1
    elif total_weight >= 2:
        priority_shift = 1
        visibility_shift = 0
    elif total_weight <= -4:
        priority_shift = -1
        visibility_shift = -1
    elif total_weight <= -2:
        priority_shift = 0
        visibility_shift = -1
    else:
        priority_shift = 0
        visibility_shift = 0

    final_priority = score_to_priority(base_score + priority_shift)
    final_visibility = shift_visibility(base_visibility, visibility_shift)

    # -----------------------------------------------------
    # PRODUCT RULE 1:
    # Review demo-like content should not auto-jump to PRIORITY
    # -----------------------------------------------------
    if is_demo_like(category) and engine_result.priority == "REVIEW" and final_priority == "PRIORITY":
        final_priority = "REVIEW"
        explanation.hard_rule_adjustments.append(
            "review demo-like content capped at REVIEW"
        )

    # -----------------------------------------------------
    # PRODUCT RULE 2:
    # Demo-like content in business inbox can be highly visible,
    # but should not automatically become PRIORITY from REVIEW
    # -----------------------------------------------------
    if mailbox_config.inbox_profile == "business_mixed" and is_demo_like(category):
        if engine_result.priority == "REVIEW":
            final_priority = "REVIEW"
            final_visibility = max_visibility(final_visibility, "show_priority")
            explanation.hard_rule_adjustments.append(
                "demo-like mail in business inbox kept highly visible but capped at REVIEW"
            )

    # -----------------------------------------------------
    # PRODUCT RULE 3:
    # Demo inbox should suppress business-like categories
    # -----------------------------------------------------
    if mailbox_config.inbox_profile == "demo_first" and is_business_like(category):
        final_priority = "LOW"
        final_visibility = "hide"
        explanation.hard_rule_adjustments.append(
            "business-like mail suppressed in demo_first inbox"
        )

    # -----------------------------------------------------
    # PRODUCT RULE 4:
    # Promo reminders stay quiet even in promo inbox
    # -----------------------------------------------------
    if category == "promo_reminder":
        final_priority = "LOW"
        explanation.hard_rule_adjustments.append(
            "promo_reminder priority capped at LOW"
        )

    mailbox_override = mailbox_config.preferences_override or {}

    effective_preferences = UserPreferences(
    **{
        **user_config.preferences.__dict__,
        **mailbox_override,
    }
)

    final_visibility, pref_notes = apply_preference_override(
    category=category,
    current_visibility=final_visibility,
    preferences=effective_preferences,
    mailbox_profile=mailbox_config.inbox_profile,
)
    explanation.preference_adjustments.extend(pref_notes)

    if category in {"finance", "royalty_statement"} and mailbox_config.inbox_profile == "business_mixed":
        final_visibility = min_visibility(final_visibility, "show_normal")

    # Extra safety: keep demo_first suppression intact after preferences
    if mailbox_config.inbox_profile == "demo_first" and is_business_like(category):
        final_visibility = "hide"

    # Extra safety: promo reminders should not exceed quiet visibility by preference accident
    if category == "promo_reminder":
        final_visibility = min_visibility(final_visibility, "show_low")

    if category == "demo":
        explanation.final_summary = "Shown prominently because this looks like a real demo submission."

    elif category == "promo":
        if mailbox_config.inbox_profile == "promo_first":
            explanation.final_summary = "Shown prominently because promo inbox prefers playable music promos."
        else:
            explanation.final_summary = "Shown because this looks like a music promo email."

    elif category == "promo_reminder":
        explanation.final_summary = "Kept low because promo reminders should stay visible without dominating the inbox."

    elif category == "info":
        explanation.final_summary = "Shown quietly because this looks like a general information or business message."

    elif category == "royalty_statement":
        explanation.final_summary = "Kept visible because royalty and finance emails should remain accessible."

    else:
        explanation.final_summary = (
            f"Final behavior set to priority={final_priority}, "
            f"visibility={final_visibility} based on engine output, "
            f"role bias, inbox profile, preferences, and product rules."
    )

    return FinalDecision(
        final_category=category,
        final_priority=final_priority,
        final_visibility=final_visibility,
        action=visibility_to_action(final_visibility),
        explanation=explanation,
    )


def decision_to_dict(decision: FinalDecision) -> Dict[str, Any]:
    return asdict(decision)
