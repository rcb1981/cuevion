from dataclasses import asdict
from typing import List, Dict, Any, Optional

from v7_config import (
    EngineResult,
    MailboxConnection,
    MailboxConfig,
    UserConfig,
    UserPreferences,
    create_default_user_config,
    build_onboarding_start_state,
    build_preferences_from_role_and_onboarding,
    apply_onboarding_answers_to_user_config,
    create_user_config_from_onboarding,
    build_onboarding_role_picker,
    build_onboarding_entry_payload,
    build_mailbox_setup_payload,
    get_role_based_inbox_suggestion,
)
from v7_decision_layer import decide_message_behavior, decision_to_dict


# =========================================================
# V7 VALIDATION MATRIX
# Label Inbox AI
# Tests expected product behavior on top of V6.5.5 stable
# =========================================================


class ValidationCase:
    def __init__(
        self,
        name: str,
        role: str,
        mailbox_email: str,
        engine_result: EngineResult,
        expected: Dict[str, Any],
        note: Optional[str] = None,
        preferences_override: Optional[Dict[str, Any]] = None,
    ):
        self.name = name
        self.role = role
        self.mailbox_email = mailbox_email
        self.engine_result = engine_result
        self.expected = expected
        self.note = note
        self.preferences_override = preferences_override

class OnboardingValidationCase:
    def __init__(
        self,
        name: str,
        role: str,
        mailbox_emails: List[str],
        expected: Dict[str, Any],
        note: Optional[str] = None,
    ):
        self.name = name
        self.role = role
        self.mailbox_emails = mailbox_emails
        self.expected = expected
        self.note = note

class InboxSuggestionValidationCase:
    def __init__(
        self,
        name: str,
        role: str,
        mailbox_count: int,
        expected: List[str],
        note: Optional[str] = None,
    ):
        self.name = name
        self.role = role
        self.mailbox_count = mailbox_count
        self.expected = expected
        self.note = note

# ---------------------------------------------------------
# USER / MAILBOX BUILDERS
# ---------------------------------------------------------

def build_user_config_for_onboarding_test(
    role: str,
    mailbox_emails: List[str],
) -> UserConfig:
    mailbox_connections = [
        MailboxConnection(
            email_address=email,
            provider_type="custom_imap",
            connection_type="imap",
            connected=True,
            enabled=True,
        )
        for email in mailbox_emails
    ]

    return create_default_user_config(
        user_id="onboarding_validation_user",
        role=role,  # type: ignore
        mailbox_connections=mailbox_connections,
    )

def build_user_config_for_test(role: str, mailbox_email: str) -> UserConfig:
    mailbox_connections = [
        MailboxConnection(
            email_address=mailbox_email,
            provider_type="custom_imap",
            connection_type="imap",
            connected=True,
            enabled=True,
        )
    ]

    user_config = create_default_user_config(
    user_id="validation_user",
    role=role,  # type: ignore
    mailbox_connections=mailbox_connections,
    )

    if mailbox_email.lower() == "info@yourlabel.com":
        user_config.mailboxes[0].preferences_override = {
            "promos_in_business_inbox_mode": "show_priority"
        }

    return user_config

def get_mailbox_config(user_config: UserConfig, mailbox_email: str) -> MailboxConfig:
    for mailbox in user_config.mailboxes:
        if mailbox.email_address.lower() == mailbox_email.lower():
            return mailbox
    raise ValueError(f"Mailbox config not found for {mailbox_email}")

def build_user_config_from_onboarding_answers(
    role: str,
    mailbox_email: str,
    onboarding_answers: Dict[str, str],
) -> UserConfig:
    user_config = build_user_config_for_test(role, mailbox_email)

    user_config.preferences = build_preferences_from_role_and_onboarding(
        role=role,  # type: ignore
        answers=onboarding_answers,
    )

    return user_config

# ---------------------------------------------------------
# DECISION EXTRACTION
# ---------------------------------------------------------

def normalize_decision(decision: Any) -> Dict[str, Any]:
    """
    Converts your decision output into a predictable dict.
    Uses decision_to_dict if available, otherwise falls back to __dict__.
    """
    try:
        data = decision_to_dict(decision)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    if hasattr(decision, "__dict__"):
        return dict(decision.__dict__)

    if isinstance(decision, dict):
        return decision

    raise TypeError("Could not normalize decision output to dict.")


def run_case(case: ValidationCase) -> Dict[str, Any]:
    user_config = build_user_config_for_test(case.role, case.mailbox_email)

    if case.preferences_override:
       user_config.preferences = UserPreferences(
           **{
               **user_config.preferences.__dict__,
               **case.preferences_override,
           }
        )
       
    mailbox_config = get_mailbox_config(user_config, case.mailbox_email)

    decision = decide_message_behavior(
        engine_result=case.engine_result,
        user_config=user_config,
        mailbox_config=mailbox_config,
    )

    return normalize_decision(decision)

def run_onboarding_decision_case(
    case: ValidationCase,
    onboarding_answers: Dict[str, str],
) -> Dict[str, Any]:
    user_config = build_user_config_from_onboarding_answers(
        role=case.role,
        mailbox_email=case.mailbox_email,
        onboarding_answers=onboarding_answers,
    )

    mailbox_config = get_mailbox_config(user_config, case.mailbox_email)

    decision = decide_message_behavior(
        engine_result=case.engine_result,
        user_config=user_config,
        mailbox_config=mailbox_config,
    )

    return normalize_decision(decision)

def run_onboarding_case(case: OnboardingValidationCase) -> Dict[str, Any]:
    user_config = build_user_config_for_onboarding_test(
        role=case.role,
        mailbox_emails=case.mailbox_emails,
    )

    return build_onboarding_start_state(user_config)

def run_onboarding_persistence_case(
    role: str,
    mailbox_email: str,
    onboarding_answers: Dict[str, str],
    selected_style: Optional[str] = None,
) -> Dict[str, Any]:
    user_config = build_user_config_for_test(role, mailbox_email)

    user_config = apply_onboarding_answers_to_user_config(
        user_config=user_config,
        answers=onboarding_answers,
        selected_style=selected_style,
    )

    return {
        "preferences": asdict(user_config.preferences),
        "onboarding": asdict(user_config.onboarding) if user_config.onboarding else None,
    }

def run_create_user_config_from_onboarding_case(
    role: str,
    mailbox_email: Any,
    onboarding_answers: Dict[str, str],
    selected_style: Optional[str] = None,
) -> Dict[str, Any]:
    mailbox_emails = (
        mailbox_email
        if isinstance(mailbox_email, list)
        else [mailbox_email]
    )

    mailbox_connections = [
        MailboxConnection(
            email_address=email,
            provider_type="custom_imap",
            connection_type="imap",
            connected=True,
            enabled=True,
        )
        for email in mailbox_emails
    ]

    user_config = create_user_config_from_onboarding(
        user_id="onboarding_created_user",
        role=role,  # type: ignore
        mailbox_connections=mailbox_connections,
        answers=onboarding_answers,
        selected_style=selected_style,
    )

    return {
    "preferences": asdict(user_config.preferences),
    "onboarding": asdict(user_config.onboarding) if user_config.onboarding else None,
    "mailbox_count": len(user_config.mailboxes),
    "mailbox_profiles": {
        mailbox.email_address: mailbox.inbox_profile
        for mailbox in user_config.mailboxes
    },
}

def run_created_user_decision_case(
    role: str,
    mailbox_email: Any,
    decision_mailbox_email: str,
    onboarding_answers: Dict[str, str],
    engine_result: EngineResult,
    selected_style: Optional[str] = None,
) -> Dict[str, Any]:
    mailbox_emails = (
        mailbox_email
        if isinstance(mailbox_email, list)
        else [mailbox_email]
    )

    mailbox_connections = [
        MailboxConnection(
            email_address=email,
            provider_type="custom_imap",
            connection_type="imap",
            connected=True,
            enabled=True,
        )
        for email in mailbox_emails
    ]

    user_config = create_user_config_from_onboarding(
        user_id="decision_created_user",
        role=role,  # type: ignore
        mailbox_connections=mailbox_connections,
        answers=onboarding_answers,
        selected_style=selected_style,
    )

    mailbox_config = get_mailbox_config(user_config, decision_mailbox_email)

    decision = decide_message_behavior(
        engine_result=engine_result,
        user_config=user_config,
        mailbox_config=mailbox_config,
    )

    return normalize_decision(decision)

# ---------------------------------------------------------
# ASSERTIONS
# ---------------------------------------------------------

def compare_expected(expected: Dict[str, Any], actual: Dict[str, Any]) -> List[str]:
    errors = []

    for key, expected_value in expected.items():
        actual_value = actual.get(key)
        if actual_value != expected_value:
            errors.append(
                f"{key}: expected={expected_value!r}, actual={actual_value!r}"
            )

    return errors


def print_case_result(case: ValidationCase, actual: Dict[str, Any], errors: List[str]) -> None:
    print("=" * 100)
    print(f"TEST: {case.name}")

    if case.note:
        print(f"NOTE: {case.note}")

    print(f"ROLE: {case.role}")
    print(f"MAILBOX: {case.mailbox_email}")
    print(f"ENGINE RESULT: {asdict(case.engine_result)}")
    print("EXPECTED:")
    for k, v in case.expected.items():
        print(f"  - {k}: {v}")

    print("ACTUAL:")
    for k in [
        "final_category",
        "final_priority",
        "final_visibility",
        "action",
        "explanation",
    ]:
        if k in actual:
            print(f"  - {k}: {actual.get(k)}")

    if not errors:
        print("STATUS: PASS")
    else:
        print("STATUS: FAIL")
        print("DIFF:")
        for err in errors:
            print(f"  - {err}")


# ---------------------------------------------------------
# VALIDATION CASES
# ---------------------------------------------------------

ONBOARDING_VALIDATION_CASES: List[OnboardingValidationCase] = [
    OnboardingValidationCase(
        name="onboarding_case_1_dj_single_inbox",
        role="dj",
        mailbox_emails=["info@yourlabel.com"],
        expected={
            "is_multi_inbox": False,
            "recommended_mode": "single_inbox",
            "suggested_style": "active",
            "default_answers": {
                "promos_in_business_inbox": "high",
                "promo_reminders": "normal",
                "finance_visibility": "high",
                "business_reminders": "normal",
                "distributor_visibility": "normal",
            },
        },
        note="Single inbox DJ should default to active onboarding style.",
    ),
    OnboardingValidationCase(
        name="onboarding_case_2_dj_multi_inbox",
        role="dj",
        mailbox_emails=["promo@yourlabel.com", "info@yourlabel.com"],
        expected={
             "is_multi_inbox": True,
             "recommended_mode": "multi_inbox",
             "suggested_style": "active",
             "default_answers": {
                 "promos_in_business_inbox": "high",
                 "promo_reminders": "normal",
                 "finance_visibility": "high",
                 "business_reminders": "normal",
                 "distributor_visibility": "normal",
            },
        },
        note="Multi inbox DJ should still default to active onboarding style.",
    ),
    OnboardingValidationCase(
        name="onboarding_case_3_ar_manager_single_inbox",
        role="ar_manager",
        mailbox_emails=["info@yourlabel.com"],
        expected={
            "is_multi_inbox": False,
            "recommended_mode": "single_inbox",
            "suggested_style": "balanced",
            "default_answers": {
                "promos_in_business_inbox": "normal",
                "promo_reminders": "normal",
                "finance_visibility": "normal",
                "business_reminders": "normal",
                "distributor_visibility": "normal",
            },
        },
        note="Single inbox A&R manager should shift from quiet to balanced.",
    ),
    OnboardingValidationCase(
        name="onboarding_case_4_label_ar_manager_multi_inbox",
        role="label_ar_manager",
        mailbox_emails=[
            "demo@yourlabel.com",
            "info@yourlabel.com",
            "promo@yourlabel.com",
        ],
        expected={
             "is_multi_inbox": True,
             "recommended_mode": "multi_inbox",
             "suggested_style": "balanced",
             "default_answers": {
                 "promos_in_business_inbox": "normal",
                 "promo_reminders": "normal",
                 "finance_visibility": "normal",
                 "business_reminders": "normal",
                 "distributor_visibility": "normal",
            },
        },    
        note="Multi inbox label & A&R manager should default to balanced.",
    ),
    OnboardingValidationCase(
        name="onboarding_case_5_label_ar_manager_single_inbox",
        role="label_ar_manager",
        mailbox_emails=["info@yourlabel.com"],
        expected={
            "is_multi_inbox": False,
            "recommended_mode": "single_inbox",
            "suggested_style": "balanced",
            "default_answers": {
                "promos_in_business_inbox": "normal",
                "promo_reminders": "normal",
                "finance_visibility": "normal",
                "business_reminders": "normal",
                "distributor_visibility": "normal",
            },
        },
    note="Single inbox label & A&R manager should still default to balanced.",
    ),
    OnboardingValidationCase(
        name="onboarding_case_6_ar_manager_multi_inbox",
        role="ar_manager",
        mailbox_emails=[
             "demo@yourlabel.com",
             "info@yourlabel.com",
        ],
        expected={
        "is_multi_inbox": True,
        "recommended_mode": "multi_inbox",
        "suggested_style": "quiet",
        "default_answers": {
            "promos_in_business_inbox": "quiet",
            "promo_reminders": "quiet",
            "finance_visibility": "normal",
            "business_reminders": "normal",
            "distributor_visibility": "normal",
           },
        },    
    note="Multi inbox A&R manager should return to quiet role suggestion.",
    ),
    OnboardingValidationCase(
    name="onboarding_case_7_marketing_manager_alias_single_inbox",
    role="marketing_manager",
    mailbox_emails=["info@yourlabel.com"],
    expected={
        "is_multi_inbox": False,
        "recommended_mode": "single_inbox",
        "suggested_style": "balanced",
        "default_answers": {
            "promos_in_business_inbox": "normal",
            "promo_reminders": "normal",
            "finance_visibility": "normal",
            "business_reminders": "normal",
            "distributor_visibility": "normal",
        },
    },
    note="Marketing manager alias should inherit label manager onboarding style.",
    ),
    OnboardingValidationCase(
    name="onboarding_case_8_ar_manager_alias_single_inbox",
    role="a&r_manager",
    mailbox_emails=["info@yourlabel.com"],
    expected={
        "is_multi_inbox": False,
        "recommended_mode": "single_inbox",
        "suggested_style": "balanced",
        "default_answers": {
            "promos_in_business_inbox": "normal",
            "promo_reminders": "normal",
            "finance_visibility": "normal",
            "business_reminders": "normal",
            "distributor_visibility": "normal",
        },
    },
    note="A&R manager alias should inherit ar_manager single inbox onboarding style.",
    ),
    OnboardingValidationCase(
    name="onboarding_case_9_legal_rights_manager_alias_single_inbox",
    role="legal_rights_manager",
    mailbox_emails=["info@yourlabel.com"],
    expected={
        "is_multi_inbox": False,
        "recommended_mode": "single_inbox",
        "suggested_style": "balanced",
        "default_answers": {
            "promos_in_business_inbox": "normal",
            "promo_reminders": "normal",
            "finance_visibility": "normal",
            "business_reminders": "normal",
            "distributor_visibility": "normal",
        },
    },
    note="Legal rights manager alias should inherit management onboarding style.",
    ),
    OnboardingValidationCase(
    name="onboarding_case_10_sync_manager_alias_single_inbox",
    role="sync_manager",
    mailbox_emails=["info@yourlabel.com"],
    expected={
        "is_multi_inbox": False,
        "recommended_mode": "single_inbox",
        "suggested_style": "balanced",
        "default_answers": {
            "promos_in_business_inbox": "normal",
            "promo_reminders": "normal",
            "finance_visibility": "normal",
            "business_reminders": "normal",
            "distributor_visibility": "normal",
        },
    },
    note="Sync manager alias should inherit product manager onboarding style.",
),
]

ONBOARDING_PERSISTENCE_CASES: List[Dict[str, Any]] = [
    {
        "name": "onboarding_persistence_case_1_label_ar_balanced_defaults",
        "role": "label_ar_manager",
        "mailbox_email": "info@yourlabel.com",
        "selected_style": "balanced",
        "onboarding_answers": {
            "promos_in_business_inbox": "normal",
            "promo_reminders": "normal",
            "finance_visibility": "normal",
            "business_reminders": "normal",
            "distributor_visibility": "normal",
        },
        "expected": {
            "preferences": {
                "promo_reminders_mode": "show_low",
                "business_reminders_mode": "show_normal",
                "finance_visibility": "show_normal",
                "distributor_visibility": "show_normal",
                "wetransfer_intake_enabled": True,
                "bulk_demo_mode": "show_low",
                "promos_in_business_inbox_mode": "show_normal",
            },
            "onboarding": {
                "suggested_style": "balanced",
                "selected_style": "balanced",
                "answers": {
                    "promos_in_business_inbox": "normal",
                    "promo_reminders": "normal",
                    "finance_visibility": "normal",
                    "business_reminders": "normal",
                    "distributor_visibility": "normal",
                },
                "completed": True,
            },
        },
    },
]

CREATE_USER_CONFIG_FROM_ONBOARDING_CASES: List[Dict[str, Any]] = [
    {
        "name": "create_user_config_case_1_label_ar_balanced",
        "role": "label_ar_manager",
        "mailbox_email": "info@yourlabel.com",
        "selected_style": "balanced",
        "onboarding_answers": {
            "promos_in_business_inbox": "normal",
            "promo_reminders": "normal",
            "finance_visibility": "normal",
            "business_reminders": "normal",
            "distributor_visibility": "normal",
        },
       "expected": {
            "preferences": {
            "promo_reminders_mode": "show_low",
            "business_reminders_mode": "show_normal",
            "finance_visibility": "show_normal",
            "distributor_visibility": "show_normal",
            "wetransfer_intake_enabled": True,
            "bulk_demo_mode": "show_low",
            "promos_in_business_inbox_mode": "show_normal",
        },
        "onboarding": {
            "suggested_style": "balanced",
            "selected_style": "balanced",
            "answers": {
                "promos_in_business_inbox": "normal",
                "promo_reminders": "normal",
                "finance_visibility": "normal",
                "business_reminders": "normal",
                "distributor_visibility": "normal",
            },
            "completed": True,
            },
            "mailbox_count": 1,
        },
    },
    {
        "name": "create_user_config_case_2_label_ar_multi_inbox_balanced",
        "role": "label_ar_manager",
        "mailbox_email": [
                "demo@yourlabel.com",
                "info@yourlabel.com",
                "promo@yourlabel.com",
            ],
        "selected_style": "balanced",
        "onboarding_answers": {
                "promos_in_business_inbox": "normal",
                "promo_reminders": "normal",
                "finance_visibility": "normal",
                "business_reminders": "normal",
                "distributor_visibility": "normal",
        },
        "expected": {
             "preferences": {
                "promo_reminders_mode": "show_low",
                "business_reminders_mode": "show_normal",
                "finance_visibility": "show_normal",
                "distributor_visibility": "show_normal",
                "wetransfer_intake_enabled": True,
                "bulk_demo_mode": "show_low",
                "promos_in_business_inbox_mode": "show_normal",
            },
        "mailbox_profiles": {
             "demo@yourlabel.com": "demo_first",
             "info@yourlabel.com": "business_mixed",
             "promo@yourlabel.com": "promo_first",
            },
        },
        "onboarding": {
             "suggested_style": "balanced",
             "selected_style": "balanced",
             "answers": {
                 "promos_in_business_inbox": "normal",
                 "promo_reminders": "normal",
                 "finance_visibility": "normal",
                 "business_reminders": "normal",
                 "distributor_visibility": "normal",
             },
             "completed": True,
             },
             "mailbox_count": 3,
        },
    {
        "name": "create_user_config_case_3_marketing_manager_alias_to_label_manager",
        "role": "marketing_manager",
        "mailbox_email": "info@yourlabel.com",
        "selected_style": "balanced",
        "onboarding_answers": {
                "promos_in_business_inbox": "normal",
                "promo_reminders": "normal",
                "finance_visibility": "normal",
                "business_reminders": "normal",
                "distributor_visibility": "normal",
        },
        "expected": {
            "preferences": {
            "promo_reminders_mode": "show_low",
            "business_reminders_mode": "show_normal",
            "finance_visibility": "show_normal",
            "distributor_visibility": "show_normal",
            "wetransfer_intake_enabled": True,
            "bulk_demo_mode": "show_low",
            "promos_in_business_inbox_mode": "show_normal",
        },
        "onboarding": {
            "suggested_style": "balanced",
            "selected_style": "balanced",
            "answers": {
                "promos_in_business_inbox": "normal",
                "promo_reminders": "normal",
                "finance_visibility": "normal",
                "business_reminders": "normal",
                "distributor_visibility": "normal",
            },
            "completed": True,
        },
        "mailbox_count": 1,
        },
    }
]

CREATED_USER_DECISION_CASES: List[Dict[str, Any]] = [
    {
        "name": "created_user_decision_case_1_multi_inbox_demo_priority",
        "role": "label_ar_manager",
        "mailbox_email": [
            "demo@yourlabel.com",
            "info@yourlabel.com",
            "promo@yourlabel.com",
        ],
        "decision_mailbox_email": "demo@yourlabel.com",
        "selected_style": "balanced",
        "onboarding_answers": {
            "promos_in_business_inbox": "normal",
            "promo_reminders": "normal",
            "finance_visibility": "normal",
            "business_reminders": "normal",
            "distributor_visibility": "normal",
        },
        "engine_result": EngineResult(
            inbox_name="demo@yourlabel.com",
            category="high_priority_demo",
            priority="PRIORITY",
            workflow_links=["https://soundcloud.com/private-demo-link"],
            usable_demo_links=["https://soundcloud.com/private-demo-link"],
            reason="Strong personalized demo with usable private link.",
        ),
        "expected": {
            "final_priority": "PRIORITY",
            "final_visibility": "show_priority",
            "action": "show_in_priority",
        },
    },
    {
        "name": "created_user_decision_case_2_multi_inbox_promo_reminder",
        "role": "label_ar_manager",
        "mailbox_email": [
            "demo@yourlabel.com",
            "info@yourlabel.com",
            "promo@yourlabel.com",
        ],
        "decision_mailbox_email": "promo@yourlabel.com",
        "selected_style": "balanced",
        "onboarding_answers": {
            "promos_in_business_inbox": "normal",
            "promo_reminders": "normal",
            "finance_visibility": "normal",
            "business_reminders": "normal",
            "distributor_visibility": "normal",
        },
        "engine_result": EngineResult(
            inbox_name="promo@yourlabel.com",
            category="promo_reminder",
            priority="LOW",
            workflow_links=[],
            usable_demo_links=[],
            reason="Promo reminder follow-up.",
        ),
        "expected": {
            "final_priority": "LOW",
            "final_visibility": "show_low",
            "action": "show_in_quiet_view",
        },
    },
]

ONBOARDING_DECISION_CASES: List[Dict[str, Any]] = [
    {
        "name": "onboarding_decision_case_1_dj_quiet_promo_reminders",
        "role": "dj",
        "mailbox_email": "promo@yourlabel.com",
        "onboarding_answers": {
            "promos_in_business_inbox": "quiet",
            "promo_reminders": "quiet",
            "finance_visibility": "normal",
            "business_reminders": "normal",
            "distributor_visibility": "normal",
        },
        "engine_result": EngineResult(
            inbox_name="promo@yourlabel.com",
            category="promo_reminder",
            priority="LOW",
            workflow_links=[],
            usable_demo_links=[],
            reason="Onboarding answer lowered promo reminders.",
        ),
        "expected": {
            "final_priority": "LOW",
            "final_visibility": "show_low",
            "action": "show_in_quiet_view",
        },
    },
]

VALIDATION_CASES: List[ValidationCase] = [
    ValidationCase(
        name="case_1_high_priority_demo_on_demo_inbox",
        role="label_ar_manager",
        mailbox_email="demo@yourlabel.com",
        engine_result=EngineResult(
            inbox_name="demo@yourlabel.com",
            category="high_priority_demo",
            priority="PRIORITY",
            workflow_links=["https://soundcloud.com/private-demo-link"],
            usable_demo_links=["https://soundcloud.com/private-demo-link"],
            reason="Strong personalized demo with usable private link.",
        ),
        expected={
            "final_priority": "PRIORITY",
            "final_visibility": "show_priority",
            "action": "show_in_priority",
        },
        note="Strong demo should surface at the top in demo inbox.",
    ),
    ValidationCase(
        name="case_2_review_demo_on_demo_inbox",
        role="label_ar_manager",
        mailbox_email="demo@yourlabel.com",
        engine_result=EngineResult(
            inbox_name="demo@yourlabel.com",
            category="demo",
            priority="REVIEW",
            workflow_links=["https://dropbox.com/demo-link"],
            usable_demo_links=["https://dropbox.com/demo-link"],
            reason="Decent demo that needs manual review.",
        ),
        expected={
            "final_priority": "REVIEW",
            "final_visibility": "show_priority",
            "action": "show_in_priority",
        },
        note="Review demo must stay visible in demo inbox.",
    ),
    ValidationCase(
        name="case_3_business_mail_on_demo_inbox",
        role="label_ar_manager",
        mailbox_email="demo@yourlabel.com",
        engine_result=EngineResult(
            inbox_name="demo@yourlabel.com",
            category="business",
            priority="NORMAL",
            workflow_links=[],
            usable_demo_links=[],
            reason="General business/admin email.",
        ),
        expected={
            "final_priority": "LOW",
            "final_visibility": "hide",
            "action": "archive_candidate",
        },
        note="Demo inbox should suppress ordinary business mail.",
    ),
    ValidationCase(
        name="case_4_demo_on_info_inbox_with_role_bias",
        role="label_ar_manager",
        mailbox_email="info@yourlabel.com",
        engine_result=EngineResult(
            inbox_name="info@yourlabel.com",
            category="demo",
            priority="REVIEW",
            workflow_links=["https://soundcloud.com/demo-link"],
            usable_demo_links=["https://soundcloud.com/demo-link"],
            reason="Demo submitted to info inbox.",
        ),
        expected={
            "final_priority": "REVIEW",
            "final_visibility": "show_priority",
            "action": "show_in_priority",
        },
        note="Label & A&R role should elevate demo mail in info inbox.",
    ),
    ValidationCase(
        name="case_5_business_mail_on_info_inbox",
        role="label_ar_manager",
        mailbox_email="info@yourlabel.com",
        engine_result=EngineResult(
            inbox_name="info@yourlabel.com",
            category="business",
            priority="NORMAL",
            workflow_links=[],
            usable_demo_links=[],
            reason="Normal business email.",
        ),
        expected={
            "final_priority": "REVIEW",
            "final_visibility": "show_priority",
            "action": "show_in_priority",
        },
        note="Regular business mail belongs in the main feed.",
    ),
    ValidationCase(
        name="case_6_reply_on_info_inbox",
        role="label_ar_manager",
        mailbox_email="info@yourlabel.com",
        engine_result=EngineResult(
            inbox_name="info@yourlabel.com",
            category="reply",
            priority="NORMAL",
            workflow_links=["https://thread-link.example.com"],
            usable_demo_links=[],
            reason="Detected as a reply.",
        ),
        expected={
            "final_priority": "PRIORITY",
            "final_visibility": "show_priority",
            "action": "show_in_priority",
        },
        note="Hard rule: replies always priority.",
    ),
    ValidationCase(
        name="case_7_promo_reminder_on_promo_inbox",
        role="label_ar_manager",
        mailbox_email="promo@yourlabel.com",
        engine_result=EngineResult(
            inbox_name="promo@yourlabel.com",
            category="promo_reminder",
            priority="LOW",
            workflow_links=[],
            usable_demo_links=[],
            reason="Promo reminder follow-up.",
        ),
        expected={
            "final_priority": "LOW",
            "final_visibility": "show_low",
            "action": "show_in_quiet_view",
        },
        note="Promo reminders should remain visible but quiet.",
    ),
    ValidationCase(
        name="case_8_priority_promo_on_promo_inbox",
        role="dj",
        mailbox_email="promo@yourlabel.com",
        engine_result=EngineResult(
            inbox_name="promo@yourlabel.com",
            category="promo",
            priority="PRIORITY",
            workflow_links=["https://wetransfer.com/example-link"],
            usable_demo_links=[],
            reason="Important promo mail for DJ usage.",
        ),
        expected={
            "final_priority": "PRIORITY",
            "final_visibility": "show_priority",
            "action": "show_in_priority",
        },
        note="A strong promo should be top-level in promo inbox.",
    ),
    ValidationCase(
        name="case_9_business_reminder_on_info_inbox",
        role="label_ar_manager",
        mailbox_email="info@yourlabel.com",
        engine_result=EngineResult(
            inbox_name="info@yourlabel.com",
            category="business_reminder",
            priority="NORMAL",
            workflow_links=[],
            usable_demo_links=[],
            reason="Reminder for pending business task.",
        ),
        expected={
            "final_priority": "REVIEW",
            "final_visibility": "show_priority",
            "action": "show_in_priority",
        },
        note="Business reminders should stay highly visible in info inbox.",
    ),
    ValidationCase(
        name="case_10_distributor_update_on_info_inbox",
        role="label_ar_manager",
        mailbox_email="info@yourlabel.com",
        engine_result=EngineResult(
            inbox_name="info@yourlabel.com",
            category="distributor_update",
            priority="NORMAL",
            workflow_links=[],
            usable_demo_links=[],
            reason="Distributor update received.",
        ),
        expected={
            "final_priority": "REVIEW",
            "final_visibility": "show_normal",
            "action": "show_in_main_feed",
        },
        note="Distributor updates should remain visible but not over-promoted.",
    ),
    ValidationCase(
        name="case_11_promo_on_personal_inbox",
        role="label_ar_manager",
        mailbox_email="personal@yourlabel.com",
        engine_result=EngineResult(
            inbox_name="personal@yourlabel.com",
            category="promo",
            priority="NORMAL",
            workflow_links=["https://wetransfer.com/promo-link"],
            usable_demo_links=[],
            reason="Promo mail in personal inbox.",
        ),
        expected={
            "final_priority": "NORMAL",
            "final_visibility": "show_normal",
            "action": "show_in_main_feed",
        },
        note="Promo on personal inbox should stay normal visibility.",
    ),
    ValidationCase(
        name="case_12_royalty_statement_on_info_inbox",
        role="label_ar_manager",
        mailbox_email="info@yourlabel.com",
        engine_result=EngineResult(
            inbox_name="info@yourlabel.com",
            category="royalty_statement",
            priority="NORMAL",
            workflow_links=[],
            usable_demo_links=[],
            reason="Royalty statement delivered.",
        ),
        expected={
            "final_priority": "REVIEW",
            "final_visibility": "show_normal",
            "action": "show_in_main_feed",
        },
        note="Royalty statements should remain clearly visible.",
    ),
    ValidationCase(
        name="case_13_dj_override_promo_reminders_to_quiet",
        role="dj",
        mailbox_email="promo@yourlabel.com",
        engine_result=EngineResult(
            inbox_name="promo@yourlabel.com",
            category="promo_reminder",
            priority="LOW",
            workflow_links=[],
            usable_demo_links=[],
            reason="DJ manually lowered promo reminders.",
        ),
        expected={
            "final_priority": "LOW",
            "final_visibility": "show_low",
            "action": "show_in_quiet_view",
        },
        preferences_override={
            "promo_reminders_mode": "show_low",
        },
        note="Manual DJ preference override should keep promo reminders quiet.",
    ),
    ValidationCase(
        name="case_14_label_ar_override_business_reminders_to_normal",
        role="label_ar_manager",
        mailbox_email="info@yourlabel.com",
        engine_result=EngineResult(
            inbox_name="info@yourlabel.com",
            category="business_reminder",
            priority="NORMAL",
            workflow_links=[],
            usable_demo_links=[],
            reason="User lowered business reminder visibility.",
        ),
        expected={
            "final_priority": "REVIEW",
            "final_visibility": "show_normal",
            "action": "show_in_main_feed",
        },
        preferences_override={
            "business_reminders_mode": "show_normal",
        },
        note="Manual override should reduce business reminders from priority to normal visibility.",
    ),
    ValidationCase(
        name="case_15_label_ar_override_finance_to_priority",
        role="label_ar_manager",
        mailbox_email="info@yourlabel.com",
        engine_result=EngineResult(
            inbox_name="info@yourlabel.com",
            category="royalty_statement",
            priority="NORMAL",
            workflow_links=[],
            usable_demo_links=[],
            reason="User wants finance emails surfaced more strongly.",
        ),
        expected={
            "final_priority": "REVIEW",
            "final_visibility": "show_normal",
            "action": "show_in_main_feed",
        },
        preferences_override={
            "finance_visibility": "show_priority",
        },
        note="Finance override should be applied, but business inbox safety may still cap royalty visibility at normal.",
    ),
    ValidationCase(
        name="case_16_label_ar_override_promos_in_business_to_priority",
        role="label_ar_manager",
        mailbox_email="info@yourlabel.com",
        engine_result=EngineResult(
            inbox_name="info@yourlabel.com",
            category="promo",
            priority="NORMAL",
            workflow_links=["https://wetransfer.com/promo-link"],
            usable_demo_links=[],
            reason="User wants promos in business inbox surfaced more strongly.",
        ),
        expected={
            "final_priority": "NORMAL",
            "final_visibility": "show_priority",
            "action": "show_in_priority",
        },
        preferences_override={
            "promos_in_business_inbox_mode": "show_priority",
        },
        note="Promo in business inbox should respect the manual visibility override.",
    ),
    ValidationCase(
        name="case_17_label_ar_override_distributor_to_priority",
        role="label_ar_manager",
        mailbox_email="info@yourlabel.com",
        engine_result=EngineResult(
            inbox_name="info@yourlabel.com",
            category="distributor_update",
            priority="NORMAL",
            workflow_links=[],
            usable_demo_links=[],
            reason="User wants distributor updates surfaced more strongly.",
        ),
        expected={
            "final_priority": "REVIEW",
            "final_visibility": "show_priority",
            "action": "show_in_priority",
        },
        preferences_override={
            "distributor_visibility": "show_priority",
        },
        note="Distributor visibility override should raise distributor updates to priority visibility.",
    ),
    
]
INBOX_SUGGESTION_VALIDATION_CASES = [
    InboxSuggestionValidationCase(
        name="suggestion_case_1_ar_manager_3_inboxes",
        role="ar_manager",
        mailbox_count=3,
        expected=["personal", "demo", "info"],
        note="A&R manager with 3 inboxes should suggest personal, demo, info.",
    ),
    InboxSuggestionValidationCase(
        name="suggestion_case_2_dj_2_inboxes",
        role="dj",
        mailbox_count=2,
        expected=["personal", "promo"],
        note="DJ with 2 inboxes should suggest personal and promo.",
    ),
    InboxSuggestionValidationCase(
        name="suggestion_case_3_label_manager_2_inboxes",
        role="label_manager",
        mailbox_count=2,
        expected=["personal", "info"],
        note="Label manager with 2 inboxes should suggest personal and info.",
    ),
]

# ---------------------------------------------------------
# TEST RUNNER
# ---------------------------------------------------------

def run_onboarding_validation_suite() -> None:
    print("\n" + "#" * 100)
    print("RUNNING ONBOARDING VALIDATION SUITE")
    print("#" * 100 + "\n")

    for case in ONBOARDING_VALIDATION_CASES:
        actual = run_onboarding_case(case)
        errors = compare_expected(case.expected, actual)

        print("=" * 100)
        print(f"TEST: {case.name}")

        if case.note:
            print(f"NOTE: {case.note}")

        print(f"ROLE: {case.role}")
        print(f"MAILBOXES: {case.mailbox_emails}")

        print("EXPECTED:")
        for k, v in case.expected.items():
            print(f"  - {k}: {v}")

        print("ACTUAL:")
        for k in [
            "is_multi_inbox",
            "recommended_mode",
            "suggested_style",
            "default_answers",
        ]:
            print(f"  - {k}: {actual.get(k)}")

        if not errors:
            print("STATUS: PASS")
        else:
            print("STATUS: FAIL")
            for err in errors:
                print(f"  - {err}")

def run_validation_suite() -> None:
    total = len(VALIDATION_CASES)
    passed = 0
    failed = 0

    print("\n" + "#" * 100)
    print("RUNNING V7 VALIDATION SUITE")
    print("#" * 100 + "\n")

    for case in VALIDATION_CASES:
        try:
            actual = run_case(case)
            errors = compare_expected(case.expected, actual)
            print_case_result(case, actual, errors)

            if errors:
                failed += 1
            else:
                passed += 1

        except Exception as e:
            failed += 1
            print("=" * 100)
            print(f"TEST: {case.name}")
            print("STATUS: ERROR")
            print(f"ERROR: {e}")

    print("\n" + "#" * 100)
    print("VALIDATION SUMMARY")
    print("#" * 100)
    print(f"TOTAL:  {total}")
    print(f"PASSED: {passed}")
    print(f"FAILED: {failed}")
    print("#" * 100 + "\n")


def run_onboarding_persistence_validation_suite() -> None:
    print("\n" + "#" * 100)
    print("RUNNING ONBOARDING PERSISTENCE VALIDATION SUITE")
    print("#" * 100 + "\n")

    for case in ONBOARDING_PERSISTENCE_CASES:
        actual = run_onboarding_persistence_case(
            role=case["role"],
            mailbox_email=case["mailbox_email"],
            onboarding_answers=case["onboarding_answers"],
            selected_style=case.get("selected_style"),
        )

        errors = compare_expected(case["expected"], actual)

        print("=" * 100)
        print(f"TEST: {case['name']}")
        print("EXPECTED:")
        print(case["expected"])
        print("ACTUAL:")
        print(actual)

        if not errors:
            print("STATUS: PASS")
        else:
            print("STATUS: FAIL")
            for err in errors:
                print(f"  - {err}")

def run_create_user_config_from_onboarding_validation_suite() -> None:
    print("\n" + "#" * 100)
    print("RUNNING CREATE USER CONFIG FROM ONBOARDING VALIDATION SUITE")
    print("#" * 100 + "\n")

    for case in CREATE_USER_CONFIG_FROM_ONBOARDING_CASES:
        actual = run_create_user_config_from_onboarding_case(
            role=case["role"],
            mailbox_email=case["mailbox_email"],
            onboarding_answers=case["onboarding_answers"],
            selected_style=case.get("selected_style"),
        )

        errors = compare_expected(case["expected"], actual)

        print("=" * 100)
        print(f"TEST: {case['name']}")
        print("EXPECTED:")
        print(case["expected"])
        print("ACTUAL:")
        print(actual)

        if not errors:
            print("STATUS: PASS")
        else:
            print("STATUS: FAIL")
            for err in errors:
                print(f"  - {err}")

def run_created_user_decision_validation_suite() -> None:
    print("\n" + "#" * 100)
    print("RUNNING CREATED USER DECISION VALIDATION SUITE")
    print("#" * 100 + "\n")

    for case in CREATED_USER_DECISION_CASES:
        actual = run_created_user_decision_case(
            role=case["role"],
            mailbox_email=case["mailbox_email"],
            decision_mailbox_email=case["decision_mailbox_email"],
            onboarding_answers=case["onboarding_answers"],
            engine_result=case["engine_result"],
            selected_style=case.get("selected_style"),
        )

        errors = compare_expected(case["expected"], actual)

        print("=" * 100)
        print(f"TEST: {case['name']}")
        print("EXPECTED:")
        print(case["expected"])
        print("ACTUAL:")
        print(actual)

        if not errors:
            print("STATUS: PASS")
        else:
            print("STATUS: FAIL")
            for err in errors:
                print(f"  - {err}")

def run_onboarding_decision_validation_suite() -> None:
    print("\n" + "#" * 100)
    print("RUNNING ONBOARDING DECISION VALIDATION SUITE")
    print("#" * 100 + "\n")

    for case in ONBOARDING_DECISION_CASES:
        validation_case = ValidationCase(
            name=case["name"],
            role=case["role"],
            mailbox_email=case["mailbox_email"],
            engine_result=case["engine_result"],
            expected=case["expected"],
        )

        actual = run_onboarding_decision_case(
            case=validation_case,
            onboarding_answers=case["onboarding_answers"],
        )

        errors = compare_expected(case["expected"], actual)

        print("=" * 100)
        print(f"TEST: {case['name']}")
        print("EXPECTED:")
        print(case["expected"])
        print("ACTUAL:")
        print(actual)

        if not errors:
            print("STATUS: PASS")
        else:
            print("STATUS: FAIL")
            for err in errors:
                print(f"  - {err}")

def run_onboarding_role_picker_validation() -> None:
    print("\n" + "#" * 100)
    print("RUNNING ONBOARDING ROLE PICKER VALIDATION")
    print("#" * 100 + "\n")

    actual = build_onboarding_role_picker()

    expected = {
        "title": "Choose your role",
        "subtitle": "This helps us tailor your inbox setup",
        "total_roles": 12,
    }

    errors = compare_expected(expected, actual)

    print("EXPECTED:")
    print(expected)
    print("ACTUAL:")
    print(actual)

    if not errors:
        print("STATUS: PASS")
    else:
        print("STATUS: FAIL")

def run_onboarding_entry_payload_validation() -> None:
    print("\n" + "#" * 100)
    print("RUNNING ONBOARDING ENTRY PAYLOAD VALIDATION")
    print("#" * 100 + "\n")

    mailbox_connections = [
        MailboxConnection(
            email_address="demo@yourlabel.com",
            provider_type="custom_imap",
            connection_type="imap",
            connected=True,
            enabled=True,
        ),
        MailboxConnection(
            email_address="info@yourlabel.com",
            provider_type="google_workspace",
            connection_type="google",
            connected=True,
            enabled=True,
        ),
        MailboxConnection(
            email_address="promo@yourlabel.com",
            provider_type="google_workspace",
            connection_type="google",
            connected=True,
            enabled=True,
        ),
    ]

    user_config = create_default_user_config(
        user_id="entry_payload_validation_user",
        role="label_ar_manager",  # type: ignore
        mailbox_connections=mailbox_connections,
    )

    actual = build_onboarding_entry_payload(user_config)

    expected = {
        "role_picker": build_onboarding_role_picker(),
        "start_state": build_onboarding_start_state(user_config),
        "mailbox_setup": build_mailbox_setup_payload(user_config),
    }

    errors = compare_expected(expected, actual)

    print("EXPECTED:")
    print(expected)
    print("ACTUAL:")
    print(actual)

    if not errors:
        print("STATUS: PASS")
    else:
        print("STATUS: FAIL")
        for err in errors:
            print(f"  - {err}")

def run_inbox_suggestion_validation_suite():
    print("\n" + "=" * 100)
    print("RUNNING INBOX SUGGESTION VALIDATION")
    print("=" * 100)

    passed = 0

    for case in INBOX_SUGGESTION_VALIDATION_CASES:
        actual = get_role_based_inbox_suggestion(case.role, case.mailbox_count)

        print(f"\nTEST: {case.name}")
        print(f"NOTE: {case.note}")
        print(f"ROLE: {case.role}")
        print(f"MAILBOX COUNT: {case.mailbox_count}")
        print(f"EXPECTED: {case.expected}")
        print(f"ACTUAL:   {actual}")

        if actual == case.expected:
            print("PASS")
            passed += 1
        else:
            print("FAIL")

    print("\n" + "-" * 100)
    print(f"RESULT: {passed}/{len(INBOX_SUGGESTION_VALIDATION_CASES)} PASS")
    print("-" * 100)

if __name__ == "__main__":
    run_onboarding_validation_suite()
    run_onboarding_persistence_validation_suite()
    run_create_user_config_from_onboarding_validation_suite()
    run_onboarding_decision_validation_suite()
    run_validation_suite()
    run_onboarding_entry_payload_validation()
    run_onboarding_role_picker_validation()
    run_created_user_decision_validation_suite()
    run_inbox_suggestion_validation_suite()
