from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Literal, Any


# =========================================================
# V7 CONFIG BLUEPRINT
# Label Inbox AI
# Product layer above V6.5.5 stable engine
# =========================================================


# ---------------------------------------------------------
# LITERALS
# ---------------------------------------------------------

RoleName = Literal[
    "label_manager",
    "ar_manager",
    "label_ar_manager",
    "product_manager",
    "dj",
    "producer",
    "artist_manager",
    "management",
]

InboxProfileName = Literal[
    "demo_first",
    "business_mixed",
    "promo_first",
    "personal_broad",
]

VisibilityMode = Literal[
    "show_priority",
    "show_normal",
    "show_low",
    "hide",
    "delete",
]

PriorityLevel = Literal[
    "PRIORITY",
    "REVIEW",
    "NORMAL",
    "LOW",
]

PRIORITY_WEIGHTS = {
    "PRIORITY": 100,
    "REVIEW": 70,
    "NORMAL": 50,
    "LOW": 10,
}

ConnectionType = Literal[
    "google",
    "microsoft",
    "imap",
]

ProviderType = Literal[
    "gmail",
    "google_workspace",
    "microsoft_365",
    "outlook",
    "custom_imap",
    "other",
]


# ---------------------------------------------------------
# CATEGORY CANON
# These should match / align with your V6.5.5 engine output
# ---------------------------------------------------------

ALL_CATEGORIES = [
    "demo",
    "bulk_demo",
    "weak_demo",
    "high_priority_demo",
    "promo",
    "promo_reminder",
    "reply",
    "workflow_update",
    "business",
    "business_reminder",
    "royalty_statement",
    "distributor_update",
    "finance",
    "unknown",
    "info",
]


# ---------------------------------------------------------
# HARD RULES
# Product safety rules that should not easily be broken
# ---------------------------------------------------------

HARD_RULES = {
    "replies_always_priority": True,
    "workflow_links_always_visible": True,
    "promo_links_not_demo_links": True,
    "keep_raw_engine_classification": True,
}


# ---------------------------------------------------------
# DATA CLASSES
# ---------------------------------------------------------

@dataclass
class UserPreferences:
    promo_reminders_mode: VisibilityMode = "show_low"
    business_reminders_mode: VisibilityMode = "show_normal"
    finance_visibility: VisibilityMode = "show_normal"
    distributor_visibility: VisibilityMode = "show_normal"
    wetransfer_intake_enabled: bool = True
    bulk_demo_mode: VisibilityMode = "show_low"
    promos_in_business_inbox_mode: VisibilityMode = "show_normal"


@dataclass
class MailboxConnection:
    email_address: str
    provider_type: ProviderType
    connection_type: ConnectionType
    connected: bool = True
    enabled: bool = True


@dataclass
class MailboxConfig:
    email_address: str
    inbox_profile: InboxProfileName
    provider_type: ProviderType
    connection_type: ConnectionType
    connected: bool = True
    enabled: bool = True
    display_name: Optional[str] = None
    preferences_override: Optional[Dict[str, Any]] = None


@dataclass
class OnboardingState:
    suggested_style: Optional[str] = None
    selected_style: Optional[str] = None
    answers: Dict[str, str] = field(default_factory=dict)
    completed: bool = False    


@dataclass
class UserConfig:
    user_id: str
    role: RoleName
    preferences: UserPreferences = field(default_factory=UserPreferences)
    mailboxes: List[MailboxConfig] = field(default_factory=list)
    onboarding: Optional[OnboardingState] = None


@dataclass
class EngineResult:
    inbox_name: str
    category: str
    priority: PriorityLevel
    reminder_mode: Optional[VisibilityMode] = None
    workflow_links: List[str] = field(default_factory=list)
    usable_demo_links: List[str] = field(default_factory=list)
    reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

# ---------------------------------------------------------
# ROLE PRESETS
# category_weights:
#   positive = more important
#   negative = less important
# ---------------------------------------------------------

ROLE_PRESETS: Dict[RoleName, Dict[str, Any]] = {
    "label_manager": {
        "label": "Label Manager",
        "category_weights": {
            "reply": 4,
            "workflow_update": 3,
            "business": 3,
            "business_reminder": 3,
            "royalty_statement": 4,
            "distributor_update": 4,
            "finance": 3,
            "promo": -1,
            "promo_reminder": -2,
            "weak_demo": -1,
        },
        "default_preferences": {
            "promo_reminders_mode": "show_low",
            "business_reminders_mode": "show_priority",
            "finance_visibility": "show_priority",
            "distributor_visibility": "show_priority",
            "wetransfer_intake_enabled": True,
            "bulk_demo_mode": "show_low",
            "promos_in_business_inbox_mode": "show_normal",
        },
    },
    "ar_manager": {
        "label": "A&R Manager",
        "category_weights": {
            "reply": 4,
            "workflow_update": 3,
            "demo": 3,
            "high_priority_demo": 5,
            "bulk_demo": 0,
            "weak_demo": -1,
            "promo": -1,
            "promo_reminder": -2,
            "royalty_statement": -1,
            "distributor_update": -1,
            "finance": -1,
        },
        "default_preferences": {
            "promo_reminders_mode": "show_low",
            "business_reminders_mode": "show_normal",
            "finance_visibility": "show_low",
            "distributor_visibility": "show_low",
            "wetransfer_intake_enabled": True,
            "bulk_demo_mode": "show_low",
            "promos_in_business_inbox_mode": "show_normal",
        },
    },
    "label_ar_manager": {
        "label": "Label & A&R Manager",
        "category_weights": {
            "reply": 4,
            "workflow_update": 3,
            "demo": 3,
            "high_priority_demo": 5,
            "business": 2,
            "business_reminder": 3,
            "royalty_statement": 2,
            "distributor_update": 2,
            "finance": 2,
            "promo": 0,
            "promo_reminder": -1,
        },
        "default_preferences": {
            "promo_reminders_mode": "show_low",
            "business_reminders_mode": "show_priority",
            "finance_visibility": "show_normal",
            "distributor_visibility": "show_normal",
            "wetransfer_intake_enabled": True,
            "bulk_demo_mode": "show_low",
            "promos_in_business_inbox_mode": "show_normal",
        },
    },
    "product_manager": {
        "label": "Product Manager",
        "category_weights": {
            "reply": 4,
            "workflow_update": 4,
            "business": 2,
            "business_reminder": 4,
            "royalty_statement": 2,
            "distributor_update": 5,
            "finance": 2,
            "demo": -1,
            "weak_demo": -2,
            "promo": -1,
            "promo_reminder": -2,
        },
        "default_preferences": {
            "promo_reminders_mode": "hide",
            "business_reminders_mode": "show_priority",
            "finance_visibility": "show_normal",
            "distributor_visibility": "show_priority",
            "wetransfer_intake_enabled": False,
            "bulk_demo_mode": "show_low",
            "promos_in_business_inbox_mode": "hide",
        },
    },
    "dj": {
        "label": "DJ",
        "category_weights": {
            "reply": 4,
            "workflow_update": 2,
            "promo": 4,
            "promo_reminder": 2,
            "demo": -1,
            "weak_demo": -2,
            "royalty_statement": 0,
            "distributor_update": -1,
            "finance": 0,
        },
        "default_preferences": {
            "promo_reminders_mode": "show_normal",
            "business_reminders_mode": "show_low",
            "finance_visibility": "show_normal",
            "distributor_visibility": "show_low",
            "wetransfer_intake_enabled": True,
            "bulk_demo_mode": "hide",
            "promos_in_business_inbox_mode": "show_normal",
        },
    },
    "producer": {
        "label": "Producer",
        "category_weights": {
            "reply": 4,
            "workflow_update": 5,
            "demo": 1,
            "high_priority_demo": 2,
            "business": 1,
            "royalty_statement": 1,
            "distributor_update": 1,
            "finance": 1,
            "promo": -1,
            "promo_reminder": -2,
        },
        "default_preferences": {
            "promo_reminders_mode": "show_low",
            "business_reminders_mode": "show_normal",
            "finance_visibility": "show_normal",
            "distributor_visibility": "show_normal",
            "wetransfer_intake_enabled": True,
            "bulk_demo_mode": "show_low",
            "promos_in_business_inbox_mode": "show_normal",
        },
    },
    "artist_manager": {
        "label": "Artist Manager",
        "category_weights": {
            "reply": 4,
            "workflow_update": 4,
            "business": 3,
            "business_reminder": 3,
            "royalty_statement": 2,
            "distributor_update": 2,
            "finance": 2,
            "promo": 0,
            "promo_reminder": -1,
            "weak_demo": -1,
        },
        "default_preferences": {
            "promo_reminders_mode": "show_low",
            "business_reminders_mode": "show_priority",
            "finance_visibility": "show_normal",
            "distributor_visibility": "show_normal",
            "wetransfer_intake_enabled": True,
            "bulk_demo_mode": "show_low",
            "promos_in_business_inbox_mode": "show_normal",
        },
    },
    "management": {
        "label": "Management",
        "category_weights": {
            "reply": 4,
            "workflow_update": 3,
            "business": 3,
            "business_reminder": 3,
            "royalty_statement": 3,
            "distributor_update": 2,
            "finance": 3,
            "promo": -1,
            "promo_reminder": -2,
            "weak_demo": -1,
        },
        "default_preferences": {
            "promo_reminders_mode": "show_low",
            "business_reminders_mode": "show_priority",
            "finance_visibility": "show_priority",
            "distributor_visibility": "show_normal",
            "wetransfer_intake_enabled": True,
            "bulk_demo_mode": "show_low",
            "promos_in_business_inbox_mode": "show_normal",
        },
    },
}


# ---------------------------------------------------------
# INBOX PROFILE PRESETS
# ---------------------------------------------------------

INBOX_PROFILE_PRESETS: Dict[InboxProfileName, Dict[str, Any]] = {
    "demo_first": {
        "label": "Demo First",
        "category_weights": {
            "demo": 3,
            "high_priority_demo": 4,
            "bulk_demo": 1,
            "weak_demo": 0,
            "reply": 2,
            "workflow_update": 2,
            "promo": -2,
            "promo_reminder": -3,
            "business": -1,
        }
    },
    "business_mixed": {
        "label": "Business Mixed",
        "category_weights": {
            "reply": 2,
            "workflow_update": 2,
            "business": 3,
            "business_reminder": 3,
            "royalty_statement": 3,
            "distributor_update": 3,
            "finance": 3,
            "demo": 0,
            "promo": -1,
            "promo_reminder": -1,
        }
    },
    "promo_first": {
        "label": "Promo First",
        "category_weights": {
            "promo": 4,
            "promo_reminder": 3,
            "reply": 2,
            "workflow_update": 1,
            "demo": -2,
            "bulk_demo": -2,
            "weak_demo": -2,
            "business": -1,
        }
    },
    "personal_broad": {
        "label": "Personal Broad",
        "category_weights": {
            "reply": 2,
            "workflow_update": 2,
            "business": 1,
            "demo": 1,
            "promo": 1,
            "promo_reminder": 0,
            "royalty_statement": 1,
            "distributor_update": 1,
        }
    }
}

# First onboarding questions used to shape initial user preferences.

ONBOARDING_V1_QUESTIONS = [
    "promos_in_business_inbox",
    "promo_reminders",
    "finance_visibility",
    "business_reminders",
    "distributor_visibility",
]

ONBOARDING_PRESET_OVERRIDES = {
      "promos_in_business_inbox": {
        "high": {"promos_in_business_inbox_mode": "show_priority"},
        "normal": {"promos_in_business_inbox_mode": "show_normal"},
        "quiet": {"promos_in_business_inbox_mode": "show_low"},
    },
      "finance_visibility": {
        "high": {"finance_visibility": "show_priority"},
        "normal": {"finance_visibility": "show_normal"},
        "quiet": {"finance_visibility": "show_low"},
    },
      "promo_reminders": {
        "high": {"promo_reminders_mode": "show_normal"},
        "normal": {"promo_reminders_mode": "show_low"},
        "quiet": {"promo_reminders_mode": "show_low"},
    },
      "business_reminders": {
        "high": {"business_reminders_mode": "show_priority"},
        "normal": {"business_reminders_mode": "show_normal"},
        "quiet": {"business_reminders_mode": "show_low"},
    },
      "distributor_visibility": {
        "high": {"distributor_visibility": "show_priority"},
        "normal": {"distributor_visibility": "show_normal"},
        "quiet": {"distributor_visibility": "show_low"},
    },
}    

# Fast onboarding styles that map to grouped onboarding answers.

ONBOARDING_STYLE_PRESETS = {
    "quiet": {
        "promos_in_business_inbox": "quiet",
        "promo_reminders": "quiet",
        "finance_visibility": "normal",
        "business_reminders": "normal",
        "distributor_visibility": "normal",
    },
    "balanced": {
        "promos_in_business_inbox": "normal",
        "promo_reminders": "normal",
        "finance_visibility": "normal",
        "business_reminders": "normal",
        "distributor_visibility": "normal",
    },
    "active": {
        "promos_in_business_inbox": "high",
        "promo_reminders": "normal",
        "finance_visibility": "high",
        "business_reminders": "normal",
        "distributor_visibility": "normal",
    },
}

# Suggested onboarding style per role for a smart default setup.

ROLE_STYLE_SUGGESTIONS = {
    "label_manager": "balanced",
    "ar_manager": "quiet",
    "label_ar_manager": "balanced",
    "product_manager": "balanced",
    "dj": "active",
    "producer": "balanced",
    "artist_manager": "balanced",
    "management": "balanced",
}
ROLE_ALIASES = {
    "streaming_manager": "label_manager",
    "marketing_manager": "label_manager",
    "social_media_manager": "product_manager",
    "sync_manager": "product_manager",
    "legal_department": "management",
    "a&r_manager": "ar_manager",
    "legal_rights_manager": "management",
}

ONBOARDING_ROLE_OPTIONS = [
    {"id": "label_ar_manager", "label": "A&R & Label Manager", "description": "Handles demos, business mail, and artist contact", "canonical_role": "label_ar_manager"},
    {"id": "ar_manager", "label": "A&R Manager", "description": "Demo intake and artist discovery", "canonical_role": "ar_manager"},
    {"id": "label_manager", "label": "Label Manager", "description": "Releases, finance, and operations", "canonical_role": "label_manager"},
    {"id": "product_manager", "label": "Product Manager", "description": "Release planning and delivery follow-up", "canonical_role": "product_manager"},
    {"id": "dj", "label": "DJ", "description": "Promo listening and music intake", "canonical_role": "dj"},
    {"id": "producer", "label": "Producer", "description": "Creative files and project communication", "canonical_role": "producer"},
    {"id": "marketing_manager", "label": "Marketing Manager", "description": "Campaigns, promo planning, and partner contact", "canonical_role": "label_manager"},
    {"id": "artist_manager", "label": "Artist Manager", "description": "Bookings, coordination, and business follow-up", "canonical_role": "artist_manager"},
    {"id": "social_media_manager", "label": "Social Media Manager", "description": "Content planning and social campaigns", "canonical_role": "product_manager"},
    {"id": "streaming_manager", "label": "Streaming Manager", "description": "DSP follow-up and playlist pitching", "canonical_role": "label_manager"},
    {"id": "sync_manager", "label": "Sync Manager", "description": "Licensing, placements, and sync coordination", "canonical_role": "product_manager"},
    {"id": "legal_rights_manager", "label": "Legal / Rights Manager", "description": "Contracts, rights, royalties, and approvals", "canonical_role": "management"},
]

FEATURED_ONBOARDING_ROLE_IDS = [
    "label_ar_manager",
    "ar_manager",
    "label_manager",
    "product_manager",
    "dj",
    "producer",
]

def get_onboarding_role_labels() -> List[Dict[str, str]]:
    return ONBOARDING_ROLE_OPTIONS

def is_onboarding_role_supported(role: str) -> bool:
    return any(option["id"] == role for option in ONBOARDING_ROLE_OPTIONS)

def normalize_onboarding_role(role: str) -> str:
    return resolve_role_alias(role)

# ---------------------------------------------------------
# MAILBOX NAME -> PROFILE SUGGESTIONS
# ---------------------------------------------------------

MAILBOX_PROFILE_SUGGESTIONS = {
    "demo": "demo_first",
    "demos": "demo_first",
    "info": "business_mixed",
    "office": "business_mixed",
    "contact": "business_mixed",
    "promo": "promo_first",
    "promos": "promo_first",
    "press": "promo_first",
    "personal": "personal_broad",
}


# ---------------------------------------------------------
# DEFAULT CATEGORY -> BASE VISIBILITY
# This is before role/profile/preference shaping
# ---------------------------------------------------------

BASE_CATEGORY_VISIBILITY: Dict[str, VisibilityMode] = {
    "reply": "show_priority",
    "workflow_update": "show_priority",
    "high_priority_demo": "show_priority",
    "demo": "show_normal",
    "bulk_demo": "show_low",
    "weak_demo": "show_low",
    "promo": "show_normal",
    "promo_reminder": "show_low",
    "business": "show_normal",
    "business_reminder": "show_normal",
    "royalty_statement": "show_normal",
    "distributor_update": "show_normal",
    "finance": "show_normal",
    "unknown": "show_low",
    "info": "show_low",
}


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def build_preferences_from_role(role: RoleName) -> UserPreferences:
    preset = ROLE_PRESETS[role]
    return UserPreferences(**preset["default_preferences"])


def suggest_inbox_profile(email_address: str) -> InboxProfileName:
    local_part = email_address.split("@")[0].lower().strip()

    if local_part in MAILBOX_PROFILE_SUGGESTIONS:
        return MAILBOX_PROFILE_SUGGESTIONS[local_part]  # type: ignore

    if "demo" in local_part:
        return "demo_first"
    if "promo" in local_part or "press" in local_part:
        return "promo_first"
    if local_part in {"info", "office", "contact"}:
        return "business_mixed"

    return "personal_broad"


def make_mailbox_config(
    email_address: str,
    provider_type: ProviderType,
    connection_type: ConnectionType,
    display_name: Optional[str] = None,
) -> MailboxConfig:
    return MailboxConfig(
        email_address=email_address,
        inbox_profile=suggest_inbox_profile(email_address),
        provider_type=provider_type,
        connection_type=connection_type,
        connected=True,
        enabled=True,
        display_name=display_name,
    )


def create_default_user_config(
    user_id: str,
    role: RoleName,
    mailbox_connections: List[MailboxConnection],
) -> UserConfig:
    resolved_role = resolve_selected_onboarding_role(role)

    preferences = build_preferences_from_role(resolved_role)  # type: ignore

    mailboxes = [
        MailboxConfig(
            email_address=conn.email_address,
            inbox_profile=suggest_inbox_profile(conn.email_address),
            provider_type=conn.provider_type,
            connection_type=conn.connection_type,
            connected=conn.connected,
            enabled=conn.enabled,
        )
        for conn in mailbox_connections
    ]

    return UserConfig(
        user_id=user_id,
        role=resolved_role,  # type: ignore
        preferences=preferences,
        mailboxes=mailboxes,
    )


def get_role_weight(role: RoleName, category: str) -> int:
    return ROLE_PRESETS[role]["category_weights"].get(category, 0)


def get_inbox_profile_weight(profile: InboxProfileName, category: str) -> int:
    return INBOX_PROFILE_PRESETS[profile]["category_weights"].get(category, 0)

def apply_onboarding_override(
    preferences: UserPreferences,
    question_key: str,
    answer_key: str,
) -> UserPreferences:
    overrides = ONBOARDING_PRESET_OVERRIDES.get(question_key, {}).get(answer_key, {})
    return UserPreferences(
        **{
            **preferences.__dict__,
            **overrides,
        }
    )

def get_default_onboarding_answers(style_key: str) -> Dict[str, str]:
    return ONBOARDING_STYLE_PRESETS.get(
        style_key,
        ONBOARDING_STYLE_PRESETS["balanced"],
    ).copy()

# Build the initial onboarding payload for frontend or product flows.

def build_onboarding_start_state(user_config: UserConfig) -> Dict[str, Any]:
    suggested_style = get_suggested_onboarding_style(user_config)
    is_multi_inbox = is_multi_inbox_user(user_config)

    return {
        "role": user_config.role,
        "is_multi_inbox": is_multi_inbox,
        "recommended_mode": "multi_inbox" if is_multi_inbox else "single_inbox",
        "suggested_style": suggested_style,
        "default_answers": get_default_onboarding_answers(suggested_style),
    }

# Apply multiple validated onboarding answers on top of base user preferences.

def build_preferences_from_onboarding_answers(
    base_preferences: UserPreferences,
    answers: Dict[str, str],
) -> UserPreferences:
    updated_preferences = base_preferences

    for question_key, answer_key in answers.items():
        if question_key not in ONBOARDING_V1_QUESTIONS:
            continue

        updated_preferences = apply_onboarding_override(
            updated_preferences,
            question_key,
            answer_key,
        )

    return updated_preferences

# Build final user preferences from role defaults plus onboarding answers.

def build_preferences_from_role_and_onboarding(
    role: RoleName,
    answers: Dict[str, str],
) -> UserPreferences:
    base_preferences = build_preferences_from_role(role)

    return build_preferences_from_onboarding_answers(
        base_preferences,
        answers,
    )

def build_preferences_from_role_and_style(
    role: RoleName,
    style_key: str,
) -> UserPreferences:
    answers = ONBOARDING_STYLE_PRESETS.get(style_key, {})

    return build_preferences_from_role_and_onboarding(
        role,
        answers,
    )

def build_preferences_from_role_suggestion(
    role: RoleName,
) -> UserPreferences:
    style_key = ROLE_STYLE_SUGGESTIONS.get(role, "balanced")

    return build_preferences_from_role_and_style(
        role,
        style_key,
    )

def is_multi_inbox_user(user_config: UserConfig) -> bool:
    enabled_mailboxes = [
        mailbox
        for mailbox in user_config.mailboxes
        if mailbox.enabled and mailbox.connected
    ]

    return len(enabled_mailboxes) > 1

def get_suggested_onboarding_style(user_config: UserConfig) -> str:
    resolved_role = resolve_role_alias(user_config.role)

    if is_multi_inbox_user(user_config):
        return ROLE_STYLE_SUGGESTIONS.get(resolved_role, "balanced")

    if resolved_role == "dj":
        return "active"

    if resolved_role == "ar_manager":
        return "balanced"

    return ROLE_STYLE_SUGGESTIONS.get(resolved_role, "balanced")

def build_preferences_from_suggested_setup(
    user_config: UserConfig,
) -> UserPreferences:
    suggested_style = get_suggested_onboarding_style(user_config)

    return build_preferences_from_role_and_style(
        user_config.role,
        suggested_style,
    )

def apply_onboarding_answers_to_user_config(
    user_config: UserConfig,
    answers: Dict[str, str],
    selected_style: Optional[str] = None,
) -> UserConfig:
    updated_preferences = build_preferences_from_role_and_onboarding(
        role=user_config.role,
        answers=answers,
    )

    user_config.preferences = updated_preferences
    user_config.onboarding = OnboardingState(
        suggested_style=get_suggested_onboarding_style(user_config),
        selected_style=selected_style,
        answers={
            key: value
            for key, value in answers.items()
            if key in ONBOARDING_V1_QUESTIONS
        },
        completed=True,
    )

    return user_config

def create_user_config_from_onboarding(
    user_id: str,
    role: RoleName,
    mailbox_connections: List[MailboxConnection],
    answers: Dict[str, str],
    selected_style: Optional[str] = None,
) -> UserConfig:
    user_config = create_default_user_config(
        user_id=user_id,
        role=resolve_role_alias(role),
        mailbox_connections=mailbox_connections,
    )

    return apply_onboarding_answers_to_user_config(
        user_config=user_config,
        answers=answers,
        selected_style=selected_style,
    )

def resolve_role_alias(role: str) -> str:
    return ROLE_ALIASES.get(role, role)

def build_onboarding_role_picker() -> Dict[str, Any]:
    split_roles = split_onboarding_roles()

    return {
        "title": "Choose your role",
        "subtitle": "This helps us tailor your inbox setup",
        "featured_roles": split_roles["featured_roles"],
        "more_roles": split_roles["more_roles"],
        "total_roles": len(ONBOARDING_ROLE_OPTIONS),
    }

def resolve_selected_onboarding_role(role_id: str) -> str:
    for role in ONBOARDING_ROLE_OPTIONS:
        if role["id"] == role_id:
            return role["canonical_role"]
    return resolve_role_alias(role_id)

def build_onboarding_entry_payload(user_config: UserConfig) -> Dict[str, Any]:
    return {
        "role_picker": build_onboarding_role_picker(),
        "start_state": build_onboarding_start_state(user_config),
        "mailbox_setup": build_mailbox_setup_payload(user_config),
    }

def build_mailbox_setup_payload(user_config: UserConfig) -> Dict[str, Any]:
    return {
        "mailbox_count": len(user_config.mailboxes),
        "mailboxes": [
            {
                "email_address": mailbox.email_address,
                "inbox_profile": mailbox.inbox_profile,
                "connected": mailbox.connected,
                "enabled": mailbox.enabled,
            }
            for mailbox in user_config.mailboxes
        ],
    }
def split_onboarding_roles() -> Dict[str, List[Dict[str, str]]]:
    featured_roles = []
    more_roles = []

    for role in ONBOARDING_ROLE_OPTIONS:
        if role["id"] in FEATURED_ONBOARDING_ROLE_IDS:
            featured_roles.append(role)
        else:
            more_roles.append(role)

    return {
        "featured_roles": featured_roles,
        "more_roles": more_roles,
    }
ROLE_BASED_INBOX_SUGGESTIONS = {
    "label_ar_manager": {
        1: ["personal"],
        2: ["personal", "demo"],
        3: ["personal", "demo", "info"],
    },
    "ar_manager": {
        1: ["personal"],
        2: ["personal", "demo"],
        3: ["personal", "demo", "info"],
    },
    "label_manager": {
        1: ["personal"],
        2: ["personal", "info"],
        3: ["personal", "info", "demo"],
    },
    "product_manager": {
        1: ["personal"],
        2: ["personal", "info"],
        3: ["personal", "info", "promo"],
    },
    "dj": {
        1: ["personal"],
        2: ["personal", "promo"],
        3: ["personal", "info", "promo"],
    },
    "producer": {
        1: ["personal"],
        2: ["personal", "demo"],
        3: ["personal", "demo", "info"],
    },
}

INBOX_LABEL_TO_PROFILE = {
    "personal": "business_mixed",
    "demo": "demo_first",
    "info": "business_mixed",
    "promo": "promo_first",
    "legal": "business_mixed",
    "finance": "business_mixed",
    "royalty": "business_mixed",
    "statements": "business_mixed",
    "sync": "business_mixed",
}

def get_role_based_inbox_suggestion(role: str, mailbox_count: int) -> List[str]:
    canonical_role = resolve_selected_onboarding_role(role)

    role_suggestions = ROLE_BASED_INBOX_SUGGESTIONS.get(canonical_role)

    if not role_suggestions:
        return ["personal"]

    if mailbox_count in role_suggestions:
        return role_suggestions[mailbox_count]

    return role_suggestions.get(3, ["personal"])

def build_role_based_inbox_suggestion_payload(role: str, mailbox_count: int) -> Dict[str, Any]:
    suggestion = get_role_based_inbox_suggestion(role, mailbox_count)

    return {
        "role": role,
        "mailbox_count": mailbox_count,
        "suggested_inboxes": suggestion,
    }

# ---------------------------------------------------------
# SERIALIZATION HELPERS
# ---------------------------------------------------------

def user_config_to_dict(user_config: UserConfig) -> Dict[str, Any]:
    return asdict(user_config)


# ---------------------------------------------------------
# EXAMPLE USAGE
# ---------------------------------------------------------

if __name__ == "__main__":
    from v7_decision_layer import decide_message_behavior, decision_to_dict

    mailbox_connections = [
        MailboxConnection(
            email_address="demo@yourlabel.com",
            provider_type="custom_imap",
            connection_type="imap",
        ),
        MailboxConnection(
            email_address="info@yourlabel.com",
            provider_type="google_workspace",
            connection_type="google",
        ),
        MailboxConnection(
            email_address="promo@yourlabel.com",
            provider_type="google_workspace",
            connection_type="google",
        ),
    ]

    preview_user_config = create_default_user_config(
        user_id="user_001",
        role="label_ar_manager",
        mailbox_connections=mailbox_connections,
    )

    onboarding_start_state = build_onboarding_start_state(preview_user_config)

    user_config = create_user_config_from_onboarding(
        user_id="user_001",
        role="label_ar_manager",
        mailbox_connections=mailbox_connections,
        answers=onboarding_start_state["default_answers"],
        selected_style=onboarding_start_state["suggested_style"],
    )
    
    engine_result = EngineResult(
        inbox_name="demo@yourlabel.com",
        category="high_priority_demo",
        priority="PRIORITY",
        workflow_links=[],
        usable_demo_links=["https://soundcloud.com/private-demo-link"],
        reason="Private listening link and strong personalized submission.",
    )

    mailbox_config = user_config.mailboxes[0]

    decision = decide_message_behavior(
        engine_result=engine_result,
        user_config=user_config,
        mailbox_config=mailbox_config,
    )

    print("USER CONFIG:")
    print(user_config_to_dict(user_config))

    print("\nFINAL DECISION:")
    print(decision_to_dict(decision))

    print("ONBOARDING ENTRY PAYLOAD:")
    print(build_onboarding_entry_payload(preview_user_config))
