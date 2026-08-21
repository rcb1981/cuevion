"""Deterministic, provider-neutral assessment of message noise evidence.

The public result is intentionally limited to closed enums. RFC header values are
used only as bounded supporting evidence and are never copied into the result.
"""

from __future__ import annotations

import re
import unicodedata
from html.parser import HTMLParser
from email.message import Message
from email.utils import parseaddr
from typing import Final, Literal, TypedDict


NoiseDisposition = Literal[
    "none",
    "bulk_marketing",
    "unsolicited_low_value",
    "strong_spam",
]
NoiseConfidence = Literal["low", "medium", "high"]
NoiseReason = Literal[
    "provider_spam_evidence",
    "authentication_failure_evidence",
    "phishing_credential_request",
    "unsolicited_financial_solicitation",
    "unsolicited_investment_solicitation",
    "cold_sales_outreach",
    "cold_recruitment_outreach",
    "cold_call_to_action",
    "bulk_mail_evidence",
    "mailbox_relevance_mismatch",
    "no_conversation_evidence",
    "automated_sender_evidence",
]


class MessageNoiseAssessment(TypedDict):
    noiseDisposition: NoiseDisposition
    noiseConfidence: NoiseConfidence
    noiseReasons: list[NoiseReason]


NOISE_DISPOSITION_VALUES: Final[tuple[NoiseDisposition, ...]] = (
    "none",
    "bulk_marketing",
    "unsolicited_low_value",
    "strong_spam",
)
NOISE_CONFIDENCE_VALUES: Final[tuple[NoiseConfidence, ...]] = (
    "low",
    "medium",
    "high",
)
NOISE_REASON_VALUES: Final[tuple[NoiseReason, ...]] = (
    "provider_spam_evidence",
    "authentication_failure_evidence",
    "phishing_credential_request",
    "unsolicited_financial_solicitation",
    "unsolicited_investment_solicitation",
    "cold_sales_outreach",
    "cold_recruitment_outreach",
    "cold_call_to_action",
    "bulk_mail_evidence",
    "mailbox_relevance_mismatch",
    "no_conversation_evidence",
    "automated_sender_evidence",
)

MAX_SUBJECT_LENGTH: Final = 4_096
MAX_BODY_LENGTH: Final = 100_000
MAX_IDENTITY_LENGTH: Final = 2_048
MAX_HEADER_INSTANCES: Final = 16
MAX_HEADER_VALUE_LENGTH: Final = 8_192
MAX_TOTAL_HEADER_LENGTH: Final = 32_768
MAX_HTML_EVIDENCE_LENGTH: Final = 200_000
MAX_HTML_ATTRIBUTE_VALUE_LENGTH: Final = 2_048
MAX_HTML_ATTRIBUTE_TEXT_LENGTH: Final = 32_768


def _patterns(*values: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(value, re.IGNORECASE) for value in values)


FINANCIAL_PRODUCT_PATTERNS = _patterns(
    r"\b(?:personal|business|commercial|instant|quick|fast)\s+(?:or\s+(?:personal|business|commercial)\s+)?loans?\b",
    r"\bapply\s+for\s+(?:a\s+)?loans?\b",
    r"\b(?:loan|financing|credit|funding)\s+(?:offer|offers|option|options|solution|solutions|facility|facilities|program|programs|application|applications)\b",
    r"\b(?:business|personal|commercial)\s+(?:financing|funding|credit)\b",
    r"\bworking\s+capital\s+(?:loan|financing|funding)\b",
)
FINANCIAL_PITCH_PATTERNS = _patterns(
    r"\b(?:we|our\s+(?:company|group|team))\s+(?:offer|offers|provide|provides|arrange|arranges)\b",
    r"\b(?:simple|easy|fast|quick|streamlined)\s+application\s+process\b",
    r"\b(?:apply|application)\s+(?:now|today|online)\b",
    r"\bapproval\s+(?:is\s+)?subject\s+to\s+eligibility\b",
    r"\bflexible\s+(?:loan|financing|repayment|payment)\s+(?:option|options|term|terms|plan|plans)\b",
    r"\b(?:pre[ -]?approved|guaranteed\s+approval|no\s+credit\s+check)\b",
)
INVESTMENT_PRODUCT_PATTERNS = _patterns(
    r"\b(?:crypto|cryptocurrency|bitcoin|forex)\s+(?:investment|investing|trading|opportunity|opportunities)\b",
    r"\b(?:investment|investing|trading)\s+(?:opportunity|opportunities|program|programs|platform|platforms)\b",
    r"\b(?:recover|recovery\s+of)\s+(?:your\s+)?(?:lost\s+)?(?:funds|investment|investments|crypto)\b",
)
INVESTMENT_PITCH_PATTERNS = _patterns(
    r"\b(?:guaranteed|high|quick|daily|weekly|risk[ -]?free)\s+(?:return|returns|profit|profits)\b",
    r"\b(?:double|grow|multiply)\s+(?:your\s+)?(?:money|capital|investment|investments)\b",
    r"\b(?:passive|consistent)\s+(?:income|profit|profits|returns)\b",
    r"\bstart\s+(?:earning|investing|trading)\b",
)
COLD_SERVICE_PATTERNS = _patterns(
    r"\bsearch\s+engine\s+optimization\b",
    r"\bseo\s+(?:service|services|agency|audit|campaign|campaigns|package|packages)\b",
    r"\b(?:website|web)\s+(?:design|development|redesign|optimization)\b",
    r"\blead[ -]?generation\s+(?:service|services|agency|campaign|campaigns|solution|solutions)?\b",
    r"\bqualified\s+(?:sales\s+)?leads\b",
    r"\b(?:digital\s+)?marketing\s+(?:agency|service|services|company|campaign|campaigns)\b",
    r"\bsocial\s+media\s+marketing\b",
    r"\b(?:outsourcing|appointment[ -]?setting|link[ -]?building)\s+(?:service|services|team|agency)?\b",
    r"\b(?:sales|growth)\s+(?:service|services|agency|pipeline|solution|solutions)\b",
)
COLD_SALES_PITCH_PATTERNS = _patterns(
    r"\bwe\s+(?:offer|offers|provide|provides|specialize|specialise)\b",
    r"\bour\s+(?:agency|team|company|service|services)\b",
    r"\bhelp\s+(?:you|your\s+(?:company|business|team))\s+(?:grow|scale|increase|improve|generate|rank)\b",
    r"\b(?:increase|boost|improve)\s+your\s+(?:traffic|sales|leads|revenue|rankings|visibility|conversions)\b",
    r"\bgenerate\s+(?:more\s+|qualified\s+)?(?:leads|sales|appointments)\b",
    r"\b(?:free|complimentary)\s+(?:audit|proposal|consultation|strategy\s+session)\b",
)
RECRUITMENT_CONTEXT_PATTERNS = _patterns(
    r"\b(?:recruiter|recruitment|headhunter|head[ -]?hunter|talent\s+acquisition)\b",
    r"\b(?:job\s+opening|job\s+opportunity|career\s+opportunity|open\s+position|vacant\s+role|vacancy)\b",
    r"\b(?:candidate|applicant)\s+for\s+(?:a|the|this)\s+(?:job|position|role)\b",
)
RECRUITMENT_PITCH_PATTERNS = _patterns(
    r"\b(?:we\s+are|i(?:'m|\s+am))\s+(?:recruiting|hiring|looking\s+for)\b",
    r"\bi\s+(?:came\s+across|found|reviewed)\s+your\s+(?:profile|resume|cv|background)\b",
    r"\byour\s+(?:profile|experience|background)\s+(?:looks|seems|would\s+be)\b",
    r"\b(?:great|strong|good)\s+fit\s+for\s+(?:a|the|this)\s+(?:job|position|role)\b",
    r"\binterested\s+in\s+(?:a|the|this)\s+(?:job|position|role|opportunity)\b",
)
COLD_CALL_TO_ACTION_PATTERNS = _patterns(
    r"\bsend\s+(?:us|me)\s+(?:a\s+)?message\b",
    r"\bcontact\s+(?:us|me)\b",
    r"\bapply\s+(?:now|today|here|online)\b",
    r"\bget\s+started\b",
    r"\breply\s+(?:now|today|to\s+this\s+(?:email|message)|if\s+(?:you(?:'re|\s+are)\s+)?interested)\b",
    r"\b(?:book|schedule|arrange)\s+(?:(?:a|an)\s+)?(?:call|meeting|consultation|demo|interview)\b",
    r"\bclick\s+(?:here|below|the\s+link|this\s+link)\b",
    r"\b(?:verify|confirm|validate|update|unlock|restore)\s+your\s+(?:account|identity|credentials|password|payment)\b",
    r"\b(?:log|sign)\s+in\s+(?:now|here|to\s+your\s+account)\b",
    r"\breach\s+out\s+(?:to\s+us|to\s+me|today|if\s+interested)\b",
    r"\blet\s+(?:us|me)\s+know\s+if\s+(?:you(?:'re|\s+are)\s+)?interested\b",
)
PHISHING_TARGET_PATTERNS = _patterns(
    r"\b(?:account|mailbox|identity)\s+(?:credentials|password|login|verification)\b",
    r"\b(?:password|credentials|login\s+details|security\s+code|verification\s+code)\b",
    r"\b(?:account|mailbox)\s+(?:suspended|locked|disabled|compromised)\b",
)
PHISHING_ACTION_PATTERNS = _patterns(
    r"\b(?:verify|confirm|validate|update|unlock|restore)\s+(?:your\s+)?(?:account|mailbox|identity|credentials|password|payment)\b",
    r"\b(?:submit|provide|enter|send)\s+(?:your\s+)?(?:password|credentials|login\s+details|security\s+code|verification\s+code)\b",
    r"\b(?:log|sign)\s+in\s+(?:using|through|via|at|with)\s+(?:the\s+)?(?:link|button|portal)\b",
)
PAYMENT_CONTEXT_PATTERNS = _patterns(
    r"\b(?:invoice|payment|bank\s+transfer|wire\s+transfer)\b",
)
PAYMENT_DIVERSION_PATTERNS = _patterns(
    r"\b(?:new|changed|updated|different)\s+(?:bank|payment|account)\s+(?:detail|details|account|instructions)\b",
    r"\b(?:redirect|send|wire|transfer)\s+(?:the\s+)?(?:payment|funds|money)\s+to\s+(?:a\s+)?(?:new|different|updated)\s+account\b",
)
PHISHING_URGENCY_PATTERNS = _patterns(
    r"\b(?:urgent|urgently|immediately|final\s+warning|action\s+required)\b",
    r"\bwithin\s+(?:12|24|48)\s+hours?\b",
    r"\b(?:will\s+be|has\s+been)\s+(?:suspended|locked|disabled|terminated|cancelled|canceled)\b",
    r"\b(?:expires?|expiring)\s+(?:today|soon|within)\b",
)
MUSIC_RELEVANCE_PATTERNS = _patterns(
    r"\b(?:new|unreleased|original)\s+(?:track|release|single|album|ep)\b",
    r"\b(?:track|remix|artist|label|dj|radio|playlist|music|mastering|premiere)\b",
    r"\b(?:release|radio|dj|playlist)\s+(?:campaign|support|date|plan|budget|report)\b",
    r"\bpromo\s+(?:servicing|mailout|release|track|download|support)\b",
)
BULK_CONTENT_PATTERNS = _patterns(
    r"\b(?:newsletter|weekly\s+digest|monthly\s+digest|mailing\s+list)\b",
    r"\b(?:unsubscribe|email\s+preferences|view\s+in\s+(?:your\s+)?browser)\b",
)
COMMERCIAL_PRICE_PATTERNS = _patterns(
    r"(?:[$€£]\s*\d|\b\d+(?:[.,]\d{2})?\s*(?:usd|eur|gbp)\b)",
    r"\b(?:save\s+(?:up\s+to\s+)?|off\s+)\d{1,3}\s*%",
)
COMMERCIAL_CTA_PATTERNS = _patterns(
    r"\b(?:shop|buy|order)\s+(?:now|today|the\s+(?:offer|sale|collection))\b",
    r"\b(?:special\s+offer|limited\s+time|discount|coupon|sale)\b",
)
COMMERCIAL_CONTEXT_PATTERNS = _patterns(
    r"\b(?:premium|product|products|collection|catalogue|catalog|storefront)\b",
    r"\b(?:marketing|campaign|promotion|promotional)\b",
)
PROTECTED_ACTION_PATTERNS = _patterns(
    r"\b(?:invoice|receipt|payment|billing|overdue|amount\s+due|royalt(?:y|ies)|earnings|payout|transaction)\b",
    r"\b(?:contract|agreement|signature|approval|deadline|cutoff)\b",
    r"\b(?:master|publishing|recording|music|sync|licensing|usage)\s+rights\b",
    r"\b(?:copyright|rights)\s+clearance\b",
    r"\brights\s+(?:issue|claim|request|ownership|dispute)\b",
    r"\blegal\s+(?:approval|request|issue|action|review)\b",
    r"\b(?:please\s+(?:review|confirm|send|approve|sign)|can\s+you|could\s+you|need\s+your|let\s+me\s+know)\b",
    r"\b(?:release\s+(?:delivery|status|ingestion)|metadata\s+(?:issue|warning)|store\s+delivery|content\s+id|takedown|dsp\s+(?:warning|delivery))\b",
    r"\b(?:security\s+alert|new\s+sign[ -]?in|suspicious\s+activity|verification\s+code|account\s+(?:alert|locked|suspended))\b",
    r"\b(?:order\s*#|order\s+(?:status|confirmed)|booking\s+(?:status|confirmed)|shipping\s+(?:status|confirmation)|subscription\s+(?:will\s+)?renew|service\s+(?:incident|outage))\b",
)
MESSAGE_ID_REFERENCE_PATTERN = re.compile(
    r"<[^<>\s@]+@[^<>\s@]+>",
    re.IGNORECASE,
)
AUTH_FAILURE_PATTERN = re.compile(
    r"\b(?:spf|dkim|dmarc)\s*=\s*(?:fail|softfail|temperror|permerror)\b",
    re.IGNORECASE,
)


def _normalize_text(value: object, maximum_length: int) -> str:
    if not isinstance(value, str) or not value:
        return ""

    bounded = value[:maximum_length]
    normalized = unicodedata.normalize("NFKC", bounded).casefold()
    normalized = normalized.replace("\u200b", " ").replace("\ufeff", " ")
    return re.sub(r"\s+", " ", normalized).strip()


def _matches(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return bool(text) and any(pattern.search(text) for pattern in patterns)


def _bounded_header_values(message: Message, header_name: str) -> tuple[str, ...]:
    try:
        raw_values = message.get_all(header_name, [])
    except Exception:
        return ()

    if not isinstance(raw_values, list) or len(raw_values) > MAX_HEADER_INSTANCES:
        return ()

    values: list[str] = []
    total_length = 0
    for raw_value in raw_values:
        try:
            value = str(raw_value)
        except Exception:
            return ()
        if len(value) > MAX_HEADER_VALUE_LENGTH:
            return ()
        total_length += len(value)
        if total_length > MAX_TOTAL_HEADER_LENGTH:
            return ()
        values.append(_normalize_text(value, MAX_HEADER_VALUE_LENGTH))
    return tuple(values)


def _has_conversation_evidence(message: Message) -> bool:
    for header_name in ("In-Reply-To", "References"):
        for header_value in _bounded_header_values(message, header_name):
            if MESSAGE_ID_REFERENCE_PATTERN.search(header_value):
                return True
    return False


def _has_provider_spam_evidence(message: Message) -> bool:
    for header_value in _bounded_header_values(message, "X-Spam-Flag"):
        if header_value.strip() in {"yes", "true", "1", "spam"}:
            return True
    for header_value in _bounded_header_values(message, "X-Spam-Status"):
        if re.match(r"^yes(?:\b|,)", header_value):
            return True
    return False


def _has_authentication_failure_evidence(message: Message) -> bool:
    return any(
        AUTH_FAILURE_PATTERN.search(header_value)
        for header_value in _bounded_header_values(message, "Authentication-Results")
    )


def _has_bulk_mail_evidence(message: Message, text: str) -> bool:
    has_list_unsubscribe = any(
        value.strip()
        for value in _bounded_header_values(message, "List-Unsubscribe")
    )
    has_bulk_precedence = any(
        value.strip() in {"bulk", "list", "junk"}
        for value in _bounded_header_values(message, "Precedence")
    )
    has_bulk_content = _matches(text, BULK_CONTENT_PATTERNS)
    return bool(
        has_list_unsubscribe
        or has_bulk_precedence
        or (
            has_bulk_content
            and any(
                value and value != "no"
                for value in _bounded_header_values(message, "Auto-Submitted")
            )
        )
    )


def _has_automated_sender_evidence(message: Message, sender_email: str) -> bool:
    auto_submitted = any(
        value and value != "no"
        for value in _bounded_header_values(message, "Auto-Submitted")
    )
    safe_sender_email = sender_email if isinstance(sender_email, str) else ""
    try:
        parsed_sender = parseaddr(safe_sender_email)[1] or safe_sender_email
    except (TypeError, ValueError):
        parsed_sender = ""
    sender_local_part = parsed_sender.partition("@")[0].casefold()
    automated_local_part = bool(
        re.search(
            r"(?:^|[._+-])(?:no-?reply|notifications?|mailer-daemon)(?:$|[._+-])",
            sender_local_part,
        )
    )
    return auto_submitted or automated_local_part


class _CommercialTemplateEvidenceParser(HTMLParser):
    """Collect bounded structural and attribute evidence from one HTML body."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.remote_image_count = 0
        self.remote_link_count = 0
        self.attribute_text_parts: list[str] = []
        self.attribute_text_length = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        normalized_attributes = {
            name.casefold(): value
            for name, value in attrs
            if isinstance(name, str) and isinstance(value, str)
        }
        source = normalized_attributes.get("src", "").strip().casefold()
        href = normalized_attributes.get("href", "").strip().casefold()

        if tag.casefold() == "img" and source.startswith(("https://", "http://")):
            self.remote_image_count += 1
        if tag.casefold() == "a" and href.startswith(("https://", "http://")):
            self.remote_link_count += 1

        for attribute_name in ("alt", "title", "aria-label", "href"):
            value = normalized_attributes.get(attribute_name, "")
            if (
                not value
                or len(value) > MAX_HTML_ATTRIBUTE_VALUE_LENGTH
                or self.attribute_text_length + len(value)
                > MAX_HTML_ATTRIBUTE_TEXT_LENGTH
            ):
                continue
            self.attribute_text_parts.append(value)
            self.attribute_text_length += len(value)


def _commercial_html_template_evidence(message: Message) -> tuple[str, bool]:
    """Return bounded commercial attribute text and strong template structure."""

    parser = _CommercialTemplateEvidenceParser()
    remaining_length = MAX_HTML_EVIDENCE_LENGTH

    try:
        parts = message.walk()
    except Exception:
        return "", False

    for part in parts:
        if remaining_length <= 0 or part.get_content_type() != "text/html":
            continue
        try:
            payload = part.get_payload(decode=True)
            if not isinstance(payload, bytes):
                continue
            charset = part.get_content_charset() or "utf-8"
            html_body = payload[:remaining_length].decode(charset, errors="ignore")
            remaining_length -= len(html_body)
            parser.feed(html_body)
        except Exception:
            continue

    attribute_text = _normalize_text(
        " ".join(parser.attribute_text_parts),
        MAX_HTML_ATTRIBUTE_TEXT_LENGTH,
    )
    has_strong_template_structure = (
        parser.remote_image_count >= 3 and parser.remote_link_count >= 2
    )
    return attribute_text, has_strong_template_structure


def is_low_value_commercial_newsletter(
    *,
    message: Message,
    subject: str,
    body: str,
    sender_email: str,
    semantic_classification: str | None,
    noise_assessment: MessageNoiseAssessment,
    has_workflow_links: bool = False,
) -> bool:
    """Combine normalized bulk evidence with bounded commercial evidence.

    Bulk evidence is necessary but never sufficient. Protected semantic classes,
    RFC conversation state, actionable business/finance/operations language, and
    workflow links all veto the quiet-newsletter authority.
    """

    if (
        noise_assessment.get("noiseDisposition") != "bulk_marketing"
        or semantic_classification not in {"workflow_update", "info", "unknown"}
        or has_workflow_links
        or _has_conversation_evidence(message)
    ):
        return False

    html_attribute_text, has_strong_template_structure = (
        _commercial_html_template_evidence(message)
    )
    text = " ".join(
        value
        for value in (
            _normalize_text(subject, MAX_SUBJECT_LENGTH),
            _normalize_text(body, MAX_BODY_LENGTH),
            _normalize_text(sender_email, MAX_IDENTITY_LENGTH),
            html_attribute_text,
        )
        if value
    )
    if _matches(text, PROTECTED_ACTION_PATTERNS):
        return False

    commercial_support_count = sum(
        (
            _matches(text, COMMERCIAL_PRICE_PATTERNS),
            _matches(text, COMMERCIAL_CTA_PATTERNS),
            _matches(text, COMMERCIAL_CONTEXT_PATTERNS),
        )
    )
    structured_campaign_support_count = sum(
        (
            any(value.strip() for value in _bounded_header_values(message, "List-Unsubscribe")),
            any(
                value.strip() in {"bulk", "list"}
                for value in _bounded_header_values(message, "Precedence")
            ),
            any(
                value and value != "no"
                for value in _bounded_header_values(message, "Auto-Submitted")
            ),
            has_strong_template_structure,
        )
    )

    return commercial_support_count >= 2 or (
        structured_campaign_support_count >= 3 and commercial_support_count >= 1
    )


def _has_mailbox_relevance_mismatch(
    recipient_email: str,
    *,
    has_noise_intent: bool,
    has_music_relevance: bool,
) -> bool:
    safe_recipient_email = recipient_email if isinstance(recipient_email, str) else ""
    try:
        parsed_recipient = parseaddr(safe_recipient_email)[1] or safe_recipient_email
    except (TypeError, ValueError):
        parsed_recipient = ""
    local_part = parsed_recipient.partition("@")[0].casefold().split("+", 1)[0]
    mailbox_tokens = {token for token in re.split(r"[._-]+", local_part) if token}
    is_music_intake_mailbox = bool(
        mailbox_tokens.intersection({"promo", "press", "servicing"})
    )
    return is_music_intake_mailbox and has_noise_intent and not has_music_relevance


def _ordered_reasons(reasons: set[NoiseReason]) -> list[NoiseReason]:
    return [reason for reason in NOISE_REASON_VALUES if reason in reasons]


def assess_message_noise(
    *,
    message: Message,
    subject: str,
    sender_name: str,
    sender_email: str,
    recipient_email: str,
    body: str,
    semantic_classification: str | None = None,
) -> MessageNoiseAssessment:
    """Return a bounded assessment without mutating or contacting a provider.

    ``semantic_classification`` is deliberately not conversation proof. In
    particular, a semantic ``reply`` derived from a bare ``Re:`` subject cannot
    bypass the noise rules; only RFC reply-reference headers are considered.
    """
    del semantic_classification

    normalized_subject = _normalize_text(subject, MAX_SUBJECT_LENGTH)
    normalized_body = _normalize_text(body, MAX_BODY_LENGTH)
    normalized_sender_name = _normalize_text(sender_name, MAX_IDENTITY_LENGTH)
    normalized_sender_email = _normalize_text(sender_email, MAX_IDENTITY_LENGTH)
    text = " ".join(
        value
        for value in (
            normalized_subject,
            normalized_body,
            normalized_sender_name,
            normalized_sender_email,
        )
        if value
    )

    has_financial_solicitation = _matches(text, FINANCIAL_PRODUCT_PATTERNS) and _matches(
        text,
        FINANCIAL_PITCH_PATTERNS,
    )
    has_investment_solicitation = _matches(text, INVESTMENT_PRODUCT_PATTERNS) and _matches(
        text,
        INVESTMENT_PITCH_PATTERNS,
    )
    has_cold_sales = _matches(text, COLD_SERVICE_PATTERNS) and _matches(
        text,
        COLD_SALES_PITCH_PATTERNS,
    )
    has_cold_recruitment = _matches(text, RECRUITMENT_CONTEXT_PATTERNS) and _matches(
        text,
        RECRUITMENT_PITCH_PATTERNS,
    )
    has_phishing = (
        _matches(text, PHISHING_TARGET_PATTERNS)
        and _matches(text, PHISHING_ACTION_PATTERNS)
        and _matches(text, PHISHING_URGENCY_PATTERNS)
    ) or (
        _matches(text, PAYMENT_CONTEXT_PATTERNS)
        and _matches(text, PAYMENT_DIVERSION_PATTERNS)
        and _matches(text, PHISHING_URGENCY_PATTERNS)
    )
    has_cold_call_to_action = _matches(text, COLD_CALL_TO_ACTION_PATTERNS)
    has_music_relevance = _matches(text, MUSIC_RELEVANCE_PATTERNS)
    has_conversation_evidence = _has_conversation_evidence(message)
    has_provider_spam_evidence = _has_provider_spam_evidence(message)
    has_auth_failure_evidence = _has_authentication_failure_evidence(message)
    has_bulk_mail_evidence = _has_bulk_mail_evidence(message, text)
    has_automated_sender_evidence = _has_automated_sender_evidence(
        message,
        sender_email,
    )
    has_noise_intent = any(
        (
            has_financial_solicitation,
            has_investment_solicitation,
            has_cold_sales,
            has_cold_recruitment,
            has_phishing,
        )
    )
    has_mailbox_mismatch = _has_mailbox_relevance_mismatch(
        recipient_email,
        has_noise_intent=has_noise_intent,
        has_music_relevance=has_music_relevance,
    )

    reasons: set[NoiseReason] = set()
    if has_provider_spam_evidence:
        reasons.add("provider_spam_evidence")
    if has_auth_failure_evidence:
        reasons.add("authentication_failure_evidence")
    if has_phishing:
        reasons.add("phishing_credential_request")
    if has_financial_solicitation:
        reasons.add("unsolicited_financial_solicitation")
    if has_investment_solicitation:
        reasons.add("unsolicited_investment_solicitation")
    if has_cold_sales:
        reasons.add("cold_sales_outreach")
    if has_cold_recruitment:
        reasons.add("cold_recruitment_outreach")
    if has_noise_intent and has_cold_call_to_action:
        reasons.add("cold_call_to_action")
    if has_bulk_mail_evidence:
        reasons.add("bulk_mail_evidence")
    if has_mailbox_mismatch:
        reasons.add("mailbox_relevance_mismatch")
    if has_noise_intent and not has_conversation_evidence:
        reasons.add("no_conversation_evidence")
    if has_automated_sender_evidence:
        reasons.add("automated_sender_evidence")

    extra_strong_support_count = sum(
        (
            has_provider_spam_evidence,
            has_auth_failure_evidence,
            has_mailbox_mismatch,
            has_bulk_mail_evidence,
            has_automated_sender_evidence,
        )
    )
    direct_solicitation = has_financial_solicitation or has_investment_solicitation

    disposition: NoiseDisposition
    confidence: NoiseConfidence
    if has_phishing and (
        has_cold_call_to_action
        or not has_conversation_evidence
        or extra_strong_support_count > 0
    ):
        disposition = "strong_spam"
        confidence = "high" if extra_strong_support_count >= 2 else "medium"
    elif (
        direct_solicitation
        and has_cold_call_to_action
        and not has_conversation_evidence
        and extra_strong_support_count > 0
    ):
        disposition = "strong_spam"
        confidence = "high" if extra_strong_support_count >= 2 else "medium"
    elif (
        (direct_solicitation or has_cold_sales or has_cold_recruitment)
        and has_cold_call_to_action
        and not has_conversation_evidence
    ):
        disposition = "unsolicited_low_value"
        confidence = "high" if extra_strong_support_count >= 2 else "medium"
    elif has_bulk_mail_evidence:
        disposition = "bulk_marketing"
        confidence = "medium"
    else:
        disposition = "none"
        confidence = "low"

    return {
        "noiseDisposition": disposition,
        "noiseConfidence": confidence,
        "noiseReasons": _ordered_reasons(reasons),
    }
