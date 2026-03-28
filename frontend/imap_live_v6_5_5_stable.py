import imaplib
import email
import logging
from email.header import decode_header
from email.utils import parseaddr
from dotenv import load_dotenv
from openai import OpenAI
import os
import re
import json
import requests
import html
import time
from urllib.parse import urlparse, parse_qs, unquote
from v7_config import create_default_user_config, MailboxConnection, EngineResult
from v7_decision_layer import decide_message_behavior

# =========================
# LOAD ENV
# =========================
load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
logger = logging.getLogger(__name__)

# =========================
# GLOBAL SETTINGS
# =========================
IMAP_SERVER = os.getenv("IMAP_SERVER")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

TEST_LIMIT = 5
DEBUG_HTML_URLS = True

# =========================
# MULTI INBOX CONFIG V6.5.4
# =========================
INBOX_CONFIG = [
    {
        "name": "demo",
        "label": "demo@",
        "email": os.getenv("EMAIL_USER_DEMO"),
        "password": os.getenv("EMAIL_PASSWORD_DEMO"),
        "folder": "INBOX",
        "profile": "demo_first"
    },
    {
        "name": "info",
        "label": "info@",
        "email": os.getenv("EMAIL_USER_INFO"),
        "password": os.getenv("EMAIL_PASSWORD_INFO"),
        "folder": "INBOX",
        "profile": "business_mixed"
    },
    {
        "name": "personal",
        "label": "personal@",
        "email": os.getenv("EMAIL_USER_PERSONAL"),
        "password": os.getenv("EMAIL_PASSWORD_PERSONAL"),
        "folder": "INBOX",
        "profile": "personal_broad"
    },
    {
        "name": "promo",
        "label": "promo@",
        "email": os.getenv("EMAIL_USER_PROMO"),
        "password": os.getenv("EMAIL_PASSWORD_PROMO"),
        "folder": "INBOX",
        "profile": "promo_first"
    },
]

# =========================
# LINK RULES
# =========================
LINK_RULES = {
    "soundcloud": {
        "intake_default": True,
        "thread_enabled": True
    },
    "dropbox": {
        "intake_default": True,
        "thread_enabled": True
    },
    "disco": {
        "intake_default": True,
        "thread_enabled": True
    },
    "gdrive": {
        "intake_default": True,
        "thread_enabled": True
    },
    "onedrive": {
        "intake_default": True,
        "thread_enabled": True
    },
    "wetransfer": {
        "intake_default": False,
        "thread_enabled": True
    }
}

# =========================
# USER LINK SETTINGS
# =========================
USER_LINK_SETTINGS = {
    "soundcloud": LINK_RULES["soundcloud"]["intake_default"],
    "dropbox": LINK_RULES["dropbox"]["intake_default"],
    "disco": LINK_RULES["disco"]["intake_default"],
    "gdrive": LINK_RULES["gdrive"]["intake_default"],
    "onedrive": LINK_RULES["onedrive"]["intake_default"],
    "wetransfer": LINK_RULES["wetransfer"]["intake_default"],
}

# =========================
# USER REMINDER SETTINGS
# =========================
USER_REMINDER_SETTINGS = {
    "promo_reminders_mode": "show_low",
    "business_reminders_mode": "show_normal",
}

V7_USER_CONFIG = create_default_user_config(
    user_id="local_test",
    role="label_ar_manager",
    mailbox_connections=[
        MailboxConnection(
            email_address="demo@yourlabel.com",
            provider_type="custom_imap",
            connection_type="imap",
        ),
        MailboxConnection(
            email_address="info@yourlabel.com",
            provider_type="custom_imap",
            connection_type="imap",
        ),
        MailboxConnection(
            email_address="promo@yourlabel.com",
            provider_type="custom_imap",
            connection_type="imap",
        ),
        MailboxConnection(
            email_address="personal@yourlabel.com",
            provider_type="custom_imap",
            connection_type="imap",
        ),
    ],
)
for mailbox in V7_USER_CONFIG.mailboxes:
    if mailbox.email_address.lower() == "info@yourlabel.com":
        mailbox.preferences_override = {
            "promos_in_business_inbox_mode": "show_priority"
        }

# =========================
# HELPERS
# =========================
def decode_mime_words(s):
    if not s:
        return ""
    decoded_parts = decode_header(s)
    result = []
    for part, encoding in decoded_parts:
        if isinstance(part, bytes):
            result.append(part.decode(encoding or "utf-8", errors="ignore"))
        else:
            result.append(part)
    return "".join(result)


def clean_text(text):
    if not text:
        return ""
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def cleanup_extracted_url(url):
    if not url:
        return None

    for stopper in [
        "Beatport:",
        "Instagram:",
        "Spotify:",
        "Dropbox:",
        "SoundCloud:",
        "Soundcloud:",
        "DISCO:",
        "Disco:",
        "Google Drive:",
        "GoogleDrive:",
        "Drive:",
        "OneDrive:",
        "Onedrive:",
        "WeTransfer:",
        "Wetransfer:",
        "Facebook:",
        "YouTube:",
        "TikTok:",
        "Please",
        "please"
    ]:
        if stopper in url:
            url = url.split(stopper)[0]

    return url.rstrip(").,]>'\"")

def print_v7_summary(result: dict) -> None:
    print(f"V7 FINAL PRIORITY: {result.get('v7_final_priority')}")
    print(f"V7 FINAL VISIBILITY: {result.get('v7_final_visibility')}")
    print(f"V7 ACTION: {result.get('v7_action')}")
    print(f"UI SIGNAL: {result.get('ui_signal')}")

    explanation = result.get("v7_explanation")

    if isinstance(explanation, dict):
        summary = explanation.get("final_summary")
        hard_rules = explanation.get("hard_rule_adjustments", [])

        if summary:
            print(f"V7 EXPLANATION: {summary}")

        if hard_rules:
            print("V7 HARD RULES:")
            for rule in hard_rules:
                print(f"  - {rule}")
    else:
        print(f"V7 EXPLANATION: {explanation}")

def map_to_ui_signal(result):
    priority = result.get("v7_final_priority")
    category = result.get("category")

    if priority == "PRIORITY":
        return "PRIORITY"

    if category in ["promo"]:
        return "PROMO"

    if category in [
        "distributor_update",
        "labelradar_update",
        "trackstack_submission",
        "royalty_statement",
        "business_reminder"
    ]:
        return "UPDATE"

    return "NEW"

def normalize_url(url):
    if not url:
        return None

    url = html.unescape(url).strip()
    url = cleanup_extracted_url(url)

    if not url:
        return None

    return url


def unwrap_tracking_url(url):
    """
    Probeert echte target-URL terug te halen uit tracking/redirect links.
    """
    if not url:
        return None

    url = normalize_url(url)
    if not url:
        return None

    try:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)

        candidate_keys = [
            "url", "u", "redirect", "redirect_url", "redirect_uri",
            "target", "dest", "destination", "redir", "r"
        ]

        for key in candidate_keys:
            if key in query and query[key]:
                candidate = query[key][0]
                candidate = unquote(candidate)
                candidate = normalize_url(candidate)
                if candidate and candidate.startswith(("http://", "https://")):
                    return candidate
    except:
        pass

    return url


def extract_urls_from_text(text):
    if not text:
        return []

    urls = re.findall(r'https?://[^\s<>"\')]+', text, re.IGNORECASE)
    cleaned = []

    for url in urls:
        normalized = normalize_url(url)
        if normalized:
            cleaned.append(normalized)

    return list(dict.fromkeys(cleaned))


def extract_text_and_html_from_message(msg):
    text_body = ""
    html_body = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition") or "")

            if "attachment" in content_disposition.lower():
                continue

            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue

                decoded = payload.decode(
                    part.get_content_charset() or "utf-8",
                    errors="ignore"
                )

                if content_type == "text/plain" and not text_body.strip():
                    text_body = decoded

                elif content_type == "text/html" and not html_body.strip():
                    html_body = decoded

            except:
                pass

    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                decoded = payload.decode(
                    msg.get_content_charset() or "utf-8",
                    errors="ignore"
                )
                if msg.get_content_type() == "text/html":
                    html_body = decoded
                else:
                    text_body = decoded
        except:
            pass

    if not text_body and html_body:
        html_as_text = re.sub(r"<br\s*/?>", "\n", html_body, flags=re.I)
        html_as_text = re.sub(r"</p>", "\n", html_as_text, flags=re.I)
        html_as_text = re.sub(r"<[^>]+>", "", html_as_text)
        text_body = html_as_text

    return clean_text(text_body), html_body


def extract_body_from_message(msg):
    text_body, _ = extract_text_and_html_from_message(msg)
    return text_body


def count_links(text):
    if not text:
        return 0
    urls = re.findall(r'https?://[^\s<>"\')]+', text, re.IGNORECASE)
    return len(urls)


def is_noise_url(url):
    if not url:
        return True

    lower = url.lower()

    noise_patterns = [
        "unsubscribe",
        "/preferences/",
        "email-unsubscribe",
        "utm_",
        "trk=",
        "trkemail=",
        "lipi=",
        "otptoken=",
        "help/linkedin",
        "linkedin.com/comm/",
        "schema.org",
        "play.google.com/store/apps",
        "itunes.apple.com/us/app/",
        "apps.apple.com/",
        "flodesk.com",
        "appspot.com/",
        "fm55.fdske.com/",
        "disco-tracking.net/",
        "campaign_redir",
        "view-campaign-email",
    ]

    if any(pattern in lower for pattern in noise_patterns):
        return True

    return False


def clean_debug_urls(urls):
    cleaned = []
    for url in urls:
        if not url:
            continue
        if is_noise_url(url):
            continue
        cleaned.append(url)
    return list(dict.fromkeys(cleaned))


def extract_urls_from_html(html_content):
    if not html_content:
        return [], []

    html_content = html.unescape(html_content)

    hrefs = re.findall(
        r'href=[\'"]([^\'"]+)[\'"]',
        html_content,
        re.IGNORECASE
    )

    visible_text = re.sub(r"<br\s*/?>", "\n", html_content, flags=re.I)
    visible_text = re.sub(r"</p>", "\n", visible_text, flags=re.I)
    visible_text = re.sub(r"<[^>]+>", " ", visible_text)
    visible_urls = extract_urls_from_text(visible_text)

    raw_urls = []

    for href in hrefs:
        href = href.strip()
        if href.lower().startswith(("http://", "https://")):
            raw_urls.append(unwrap_tracking_url(href))

    raw_urls.extend(visible_urls)
    raw_urls = list(dict.fromkeys([x for x in raw_urls if x]))

    debug_urls = clean_debug_urls(raw_urls)

    return raw_urls, debug_urls


# =========================
# LINK QUALITY HELPERS
# =========================
def is_soundcloud_profile_url(url):
    if not url:
        return False

    lower = url.lower()
    if "soundcloud.com/" not in lower and "m.soundcloud.com/" not in lower:
        return False
    if "on.soundcloud.com/" in lower:
        return False

    path = urlparse(lower).path.strip("/")

    if not path:
        return False

    parts = [p for p in path.split("/") if p]
    return len(parts) == 1


def choose_best_soundcloud_url(text, subject="", artist_name=""):
    if not text:
        return None

    patterns = [
        r"https?://on\.soundcloud\.com/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+",
        r"https?://(?:www\.)?soundcloud\.com/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+",
        r"https?://m\.soundcloud\.com/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+",
    ]

    matches = []
    for pattern in patterns:
        found = re.findall(pattern, text, re.IGNORECASE)
        matches.extend(found)

    matches = list(dict.fromkeys([cleanup_extracted_url(x) for x in matches if x]))
    if not matches:
        return None

    subject_low = (subject or "").lower()
    artist_low = (artist_name or "").lower()

    def score_soundcloud(url):
        lower = url.lower()
        score = 0

        # Private links are strongest
        if "/s-" in lower:
            score += 100

        # Track-like links preferred over pure profile
        path = urlparse(lower).path.strip("/")
        parts = [p for p in path.split("/") if p]

        if "on.soundcloud.com" in lower:
            score += 35

        if len(parts) >= 2:
            score += 40

        if "/sets/" in lower:
            score += 20

        if is_soundcloud_profile_url(url):
            score -= 120

        # Subject/title match
        subject_tokens = [t for t in re.findall(r"[a-z0-9]+", subject_low) if len(t) >= 4]
        for token in subject_tokens[:8]:
            if token in lower:
                score += 8

        # Artist hint
        artist_tokens = [t for t in re.findall(r"[a-z0-9]+", artist_low) if len(t) >= 4]
        for token in artist_tokens[:4]:
            if token in lower:
                score += 4

        # Prefer obvious track/share paths
        if len(parts) >= 2 and "/sets/" not in lower:
            score += 10

        return score

    ranked = sorted(matches, key=score_soundcloud, reverse=True)
    return ranked[0]


# =========================
# LINK EXTRACTORS
# =========================
def extract_soundcloud_url(text, subject="", artist_name=""):
    return choose_best_soundcloud_url(text, subject=subject, artist_name=artist_name)


def extract_dropbox_url(text):
    if not text:
        return None

    patterns = [
        r"https?://(?:www\.)?dropbox\.com/[^\s<>\"]+",
        r"https?://dropbox\.com/[^\s<>\"]+",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return cleanup_extracted_url(match.group(0))

    return None


def extract_wetransfer_url(text):
    if not text:
        return None

    patterns = [
        r"https?://(?:www\.)?wetransfer\.com/[^\s<>\"]+",
        r"https?://we\.tl/[^\s<>\"]+",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return cleanup_extracted_url(match.group(0))

    return None


def extract_disco_url(text):
    if not text:
        return None

    patterns = [
        r"https?://(?:www\.)?disco\.ac/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+",
        r"https?://[A-Za-z0-9-]+\.disco\.ac/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return cleanup_extracted_url(match.group(0))

    return None


def extract_gdrive_url(text):
    if not text:
        return None

    patterns = [
        r"https?://drive\.google\.com/[^\s<>\"]+",
        r"https?://docs\.google\.com/[^\s<>\"]+",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return cleanup_extracted_url(match.group(0))

    return None


def extract_onedrive_url(text):
    if not text:
        return None

    patterns = [
        r"https?://1drv\.ms/[^\s<>\"]+",
        r"https?://(?:www\.)?onedrive\.live\.com/[^\s<>\"]+",
        r"https?://[A-Za-z0-9.-]*sharepoint\.com/[^\s<>\"]+",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return cleanup_extracted_url(match.group(0))

    return None


def extract_instagram_url(text):
    if not text:
        return None

    url_match = re.search(
        r"https?://(www\.)?instagram\.com/[A-Za-z0-9._-]+",
        text,
        re.IGNORECASE
    )
    if url_match:
        return cleanup_extracted_url(url_match.group(0))

    handle_match = re.search(r"(instagram|ig)[:\s]+@([A-Za-z0-9._]+)", text, re.IGNORECASE)
    if handle_match:
        return "@" + handle_match.group(2)

    return None


def extract_spotify_url(text):
    if not text:
        return None

    match = re.search(r"https?://open\.spotify\.com/[^\s<>\"]+", text, re.IGNORECASE)
    return cleanup_extracted_url(match.group(0)) if match else None


# =========================
# CENTRAL LINK LAYER
# =========================
def extract_all_links(text, html_content="", subject="", artist_name=""):
    text_urls = extract_urls_from_text(text or "")
    raw_html_urls, debug_html_urls = extract_urls_from_html(html_content or "")

    combined_urls = list(dict.fromkeys(text_urls + raw_html_urls))
    combined_text = f"{text or ''}\n{' '.join(combined_urls)}"

    soundcloud_best = extract_soundcloud_url(
        combined_text,
        subject=subject,
        artist_name=artist_name
    )

    return {
        "soundcloud": soundcloud_best,
        "dropbox": extract_dropbox_url(combined_text),
        "wetransfer": extract_wetransfer_url(combined_text),
        "disco": extract_disco_url(combined_text),
        "gdrive": extract_gdrive_url(combined_text),
        "onedrive": extract_onedrive_url(combined_text),
        "instagram": extract_instagram_url(combined_text),
        "spotify_url": extract_spotify_url(combined_text),
        "all_html_urls_raw": combined_urls,
        "all_html_urls_debug": debug_html_urls,
    }


def get_intake_enabled_link_types(user_link_settings=None):
    if user_link_settings is None:
        user_link_settings = USER_LINK_SETTINGS

    enabled = []
    for link_type, rule in LINK_RULES.items():
        if user_link_settings.get(link_type, rule["intake_default"]):
            enabled.append(link_type)
    return enabled


def get_detected_workflow_links(extracted_links):
    workflow_links = []

    for link_type, value in extracted_links.items():
        if not value:
            continue

        if link_type in LINK_RULES and LINK_RULES[link_type]["thread_enabled"]:
            workflow_links.append(value)

    return list(dict.fromkeys(workflow_links))


def get_usable_demo_links(extracted_links, category, user_link_settings=None):
    if user_link_settings is None:
        user_link_settings = USER_LINK_SETTINGS

    if category not in ["demo", "high_priority_demo", "reply"]:
        return []

    enabled_types = get_intake_enabled_link_types(user_link_settings)

    usable_links = []
    for link_type in enabled_types:
        value = extracted_links.get(link_type)
        if not value:
            continue

        # SoundCloud profile pages should not count as intake-usable demo links
        if link_type == "soundcloud" and is_soundcloud_profile_url(value):
            continue

        usable_links.append(value)

    return list(dict.fromkeys(usable_links))


# =========================
# SPOTIFY
# =========================
def get_spotify_access_token():
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None

    try:
        response = requests.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            auth=(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET),
            timeout=15
        )
        data = response.json()
        return data.get("access_token")
    except:
        return None


def normalize_compare_name(name):
    if not name:
        return ""
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9]+", "", name)
    return name


def spotify_artist_name_matches(requested_name, found_name):
    req = normalize_compare_name(requested_name)
    found = normalize_compare_name(found_name)

    if not req or not found:
        return False

    if req == found:
        return True

    if req in found or found in req:
        shorter = min(len(req), len(found))
        longer = max(len(req), len(found))
        if shorter >= 5 and shorter / max(longer, 1) >= 0.7:
            return True

    return False


def search_spotify_artist(artist_name):
    if not artist_name:
        return None

    token = get_spotify_access_token()
    if not token:
        return None

    try:
        response = requests.get(
            "https://api.spotify.com/v1/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": artist_name, "type": "artist", "limit": 5},
            timeout=15
        )
        data = response.json()
        artists = data.get("artists", {}).get("items", [])
        if not artists:
            return None

        for artist in artists:
            found_name = artist.get("name")
            if spotify_artist_name_matches(artist_name, found_name):
                return {
                    "name": found_name,
                    "followers": artist.get("followers", {}).get("total"),
                    "spotify_url": artist.get("external_urls", {}).get("spotify"),
                    "popularity": artist.get("popularity")
                }

        return None
    except:
        return None


def extract_possible_artist_name(from_name, subject, body):
    if from_name and from_name.strip():
        return from_name.strip()
    if subject:
        return subject.strip()[:80]
    return None


def is_reliable_spotify_artist_name(name):
    if not name:
        return False

    name = name.strip()

    if len(name) < 4:
        return False

    if "&" in name:
        return False

    words = name.split()

    if len(words) != 1:
        return False

    blocked_terms = [
        "records", "recordings", "music", "demo", "submission", "promo",
        "label", "labelradar", "trackstack", "spotify", "linkedin",
        "hysteriarecs.com", "hysteriarecs", "hysteria", "soundcloud",
        "dropbox", "wetransfer", "disco", "drive", "onedrive", "official"
    ]

    lower_name = name.lower()
    for term in blocked_terms:
        if term in lower_name:
            return False

    return True


# =========================
# DETECTION HELPERS
# =========================
def is_royalty_statement_email(subject, body, sender_email):
    text = f"{subject or ''} {body or ''}".lower()
    sender = (sender_email or "").lower()

    strong_phrases = [
        "royalty statement",
        "royalty report",
        "monthly statement",
        "payment statement",
        "earnings statement",
        "sales statement",
        "revenue statement",
        "payout report",
        "payment report",
        "statement attached",
        "statement available",
        "your statement is ready",
        "your royalty statement",
        "monthly royalties",
        "royalties available",
        "earnings report",
        "revenue report",
        "sales report",
        "payout available",
        "payment available",
    ]

    medium_keywords = [
        "royalty",
        "royalties",
        "statement",
        "earnings",
        "revenue",
        "payout",
        "payments",
        "sales report",
        "net revenue",
        "accounting period",
    ]

    known_sources = [
        "beatport",
        "label engine",
        "label-engine",
        "labelengine",
        "curve",
        "fuga",
        "symphonic",
        "distrokid",
        "tunecore",
        "cd baby",
        "stem",
        "amuse",
        "ditto",
    ]

    strong_hit = any(phrase in text for phrase in strong_phrases)
    medium_hits = sum(1 for kw in medium_keywords if kw in text)
    source_hit = any(src in sender or src in text for src in known_sources)

    if strong_hit:
        return True

    if source_hit and medium_hits >= 1:
        return True

    if medium_hits >= 3:
        return True

    return False


def is_promo_reminder_email(subject, body, sender_email):
    subject_low = (subject or "").lower()
    body_low = (body or "").lower()
    sender_low = (sender_email or "").lower()
    text = f"{subject_low} {body_low}"

    reminder_subject_signals = [
        "(reminder)",
        "[reminder]",
        "promo reminder",
        "invite reminder",
        "reminder promo",
    ]

    reminder_text_signals = [
        "friendly reminder",
        "just a reminder",
        "quick reminder",
        "sending a reminder",
        "wanted to remind you",
        "reminder",
    ]

    promo_context_signals = [
        "promo",
        "promos",
        "promo invite",
        "receive digital promos",
        "listen and download",
        "download promo",
        "check out this release",
        "check this release",
        "new release",
        "out now",
        "radio support",
        "dj support",
        "playlist support",
        "play in your sets",
        "support this track",
        "support the release",
        "future cuts",
        "promoclub",
        "promo pool",
        "inflyte",
        "fatdrop",
    ]

    promo_sender_signals = [
        "inflyte",
        "promoclub",
        "promo",
    ]

    reminder_hit = (
        any(x in subject_low for x in reminder_subject_signals)
        or any(x in text for x in reminder_text_signals)
        or subject_low.startswith("reminder")
    )

    promo_context_hit = (
        any(x in text for x in promo_context_signals)
        or any(x in sender_low for x in promo_sender_signals)
    )

    return reminder_hit and promo_context_hit


def is_business_reminder_email(subject, body, sender_email):
    subject_low = (subject or "").lower()
    body_low = (body or "").lower()
    sender_low = (sender_email or "").lower()
    text = f"{subject_low} {body_low}"

    strong_phrases = [
        "payment reminder",
        "invoice reminder",
        "reminder: payment due",
        "overdue invoice",
        "payment overdue",
        "invoice overdue",
        "outstanding payment",
        "outstanding invoice",
        "reminder to pay",
        "friendly payment reminder",
        "subscription payment failed",
        "payment failed",
        "billing reminder",
        "past due",
        "past-due",
    ]

    business_keywords = [
        "invoice",
        "payment due",
        "amount due",
        "outstanding",
        "overdue",
        "billing",
        "subscription",
        "unpaid",
        "due date",
        "settlement",
    ]

    confirmation_exclusions = [
        "order confirmation",
        "bestelbevestiging",
        "receipt",
        "payment receipt",
        "purchase confirmation",
        "thank you for your purchase",
        "confirmation of your order",
        "subscription confirmed",
        "invoice paid",
        "payment successful",
        "payment received",
    ]

    if any(x in text for x in confirmation_exclusions):
        return False

    strong_hit = any(phrase in text for phrase in strong_phrases)
    keyword_hits = sum(1 for kw in business_keywords if kw in text)

    reminder_signal = "reminder" in text or "overdue" in text or "past due" in text
    business_sender = any(x in sender_low for x in [
        "billing",
        "invoice",
        "accounts",
        "accounting",
        "finance",
        "stripe",
        "paypal",
    ])

    if strong_hit:
        return True

    if reminder_signal and keyword_hits >= 2:
        return True

    if business_sender and reminder_signal and keyword_hits >= 1:
        return True

    return False


def is_distributor_update_email(subject, body, sender_email):
    subject_low = (subject or "").lower()
    body_low = (body or "").lower()
    sender_low = (sender_email or "").lower()
    text = f"{subject_low} {body_low}"

    strong_distributor_sources = [
        "label-worx",
        "labelworx",
        "fuga",
        "symphonic",
        "theorchard",
        "the orchard",
        "believe",
        "ingrooves",
        "virginmusic",
        "virgin music",
        "stem.is",
        "stem",
        "vydia",
        "distrokid",
        "tunecore",
        "cdbaby",
        "cd baby",
        "amuse",
        "ditto",
        "awal",
        "label-engine",
        "labelengine",
        "curve",
        "onerpm",
        "revelator",
    ]

    strong_update_phrases = [
        "release approved",
        "release rejected",
        "delivery failed",
        "delivery completed",
        "delivery update",
        "distribution update",
        "release delivered",
        "store delivery",
        "stores updated",
        "content id update",
        "youtube claim",
        "release taken down",
        "takedown update",
        "metadata update",
        "release scheduled",
        "release went live",
        "release status",
        "release processed",
        "release ingested",
    ]

    source_hit = any(x in sender_low for x in strong_distributor_sources)
    phrase_hit = any(x in text for x in strong_update_phrases)

    return source_hit and phrase_hit


def looks_like_personal_demo_submission(subject, body, sender_name, sender_email, extracted_links):
    subject_low = (subject or "").lower()
    body_low = (body or "").lower()
    sender_low = (sender_email or "").lower()
    sender_name_low = (sender_name or "").lower()

    demo_terms = [
        "demo",
        "demo submission",
        "exclusive demo",
        "track to sign",
        "please consider",
        "would love your feedback",
        "for your label",
        "for hysteria",
        "sign this track",
        "unreleased track",
    ]

    artist_sender_markers = [
        "gmail.com",
        "outlook.com",
        "hotmail.com",
        "icloud.com",
        "orange.fr",
        "yahoo.com",
        "yahoo.fr",
        "live.com",
        "proton.me",
        "protonmail.com",
    ]

    has_demo_language = any(x in subject_low or x in body_low for x in demo_terms)
    has_usable_artist_link = bool(
        extracted_links.get("soundcloud") or
        extracted_links.get("dropbox") or
        extracted_links.get("disco") or
        extracted_links.get("gdrive") or
        extracted_links.get("onedrive")
    )
    looks_personal_sender = any(x in sender_low for x in artist_sender_markers) or (
        sender_name_low and sender_name_low not in ["", "no-reply", "noreply"]
    )

    return has_demo_language and has_usable_artist_link and looks_personal_sender


def is_bulk_submission_email(subject, body, sender_email, to_header, extracted_links, sender_name=""):
    subject_low = (subject or "").lower()
    body_low = (body or "").lower()
    sender_low = (sender_email or "").lower()
    to_low = (to_header or "").lower()
    text = f"{subject_low} {body_low}"

    bulk_score = 0

    if sender_low and sender_low in to_low:
        bulk_score += 2

    if "catalogue" in text or "catalog" in text:
        bulk_score += 2

    if count_links(body) >= 4:
        bulk_score += 1

    strong_bulk_phrases = [
        "first come first served",
        "bcc",
        "sent to multiple labels",
        "for all labels",
        "for interested labels",
        "mass email",
        "mailing list",
        "promo pool",
        "available for labels",
        "shopping this track",
        "shopping this release",
    ]

    generic_bulk_language = [
        "dear label",
        "dear labels",
        "dear sir",
        "dear madam",
        "hello labels",
        "attention labels",
    ]

    if any(x in text for x in strong_bulk_phrases):
        bulk_score += 3

    if any(x in text for x in generic_bulk_language):
        bulk_score += 2

    if looks_like_personal_demo_submission(subject, body, sender_name, sender_email, extracted_links):
        return False, bulk_score

    if bulk_score >= 4:
        return True, bulk_score

    return False, bulk_score


def get_reminder_mode(category, user_reminder_settings=None):
    if user_reminder_settings is None:
        user_reminder_settings = USER_REMINDER_SETTINGS

    if category == "promo_reminder":
        return user_reminder_settings.get("promo_reminders_mode", "show_low")

    if category == "business_reminder":
        return user_reminder_settings.get("business_reminders_mode", "show_normal")

    return None


def get_suggested_action(category, user_reminder_settings=None):
    mode = get_reminder_mode(category, user_reminder_settings)

    if mode == "hide":
        return "HIDE"
    if mode == "delete":
        return "DELETE"
    if mode in ["show_low", "show_normal"]:
        return "SHOW"
    return "SHOW"


# =========================
# CLASSIFIER
# =========================
def classify_email_with_ai(subject, body, sender_name, sender_email, to_header, inbox_profile=""):
    prompt = f"""
You classify music industry emails.

Inbox profile: {inbox_profile}

Return ONLY valid JSON with this structure:
{{
  "artist": "...",
  "category": "high_priority_demo|demo|incomplete_demo|promo|info|bulk_submission|spam|reply|trackstack_submission|labelradar_update|royalty_statement|promo_reminder|business_reminder|distributor_update",
  "score": 0,
  "priority": "PRIORITY|REVIEW|LOW",
  "reason": "...",
  "spotify": "...",
  "soundcloud_track": "...",
  "dropbox": "...",
  "wetransfer": "...",
  "disco": "...",
  "gdrive": "...",
  "onedrive": "...",
  "instagram": "...",
  "usable_demo_links": [],
  "bulk_score": 0
}}

Rules:
- high_priority_demo = strong demo with usable link + strong signals
- demo = normal demo with usable SoundCloud or Dropbox or DISCO or Google Drive or OneDrive link
- incomplete_demo = demo intent but no usable demo link
- WeTransfer is NOT a usable demo link by default
- DISCO is a usable demo link
- Google Drive is a usable demo link
- OneDrive is a usable demo link
- promo = only real music promo, such as out now / remix / bootleg / check out my track / out soon / new one by me
- promo wins before demo if clearly promo
- info = business/platform/company intro mail
- distributor_update = distributor / delivery / release status / metadata / store / takedown / content ID / catalog operational update
- royalty_statement = payout / royalty / sales / earnings / statement mail
- promo_reminder = repeated promo push or promo invite reminder
- business_reminder = real payment / invoice / overdue / billing reminder, not receipts or order confirmations
- trackstack_submission = trackstack submission/update mail
- labelradar_update = LabelRadar update mail
- bulk_submission only when there are strong mass-mailing signals
- reply = actual reply/conversation mail
- spam = obvious junk

Important:
- In the music industry, "promo" means promotional music sent for DJ/radio/live use, not a general platform promotion.
- A platform/company introduction should be "info", not "promo".
- Forwarded trackstack mails should still become trackstack_submission when clear.
- Replies should stay PRIORITY.
- Distributor operational mails should become distributor_update only when clearly coming from a distributor/platform or clearly using operational distributor language.
- Personal artist demo mails should not become distributor_update.
"""

    try:
        openai_start = time.perf_counter()
        response = client.chat.completions.create(
            model="gpt-4.1",
            temperature=0.1,
            messages=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": f"""
Subject: {subject}
From name: {sender_name}
From email: {sender_email}
To: {to_header}
Body:
{body[:12000]}
"""
                }
            ]
        )
        openai_duration_ms = (time.perf_counter() - openai_start) * 1000
        logger.warning(
            "Preview classify_email_with_ai complete openai_ms=%.1f subject=%s sender=%s",
            openai_duration_ms,
            subject[:120],
            sender_email,
        )

        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"^```\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        return data

    except Exception as e:
        logger.exception(
            "Preview classify_email_with_ai failed after %.1f ms subject=%s sender=%s",
            (time.perf_counter() - openai_start) * 1000,
            subject[:120],
            sender_email,
        )
        return {
            "artist": sender_name or "",
            "category": "info",
            "score": 0,
            "priority": "LOW",
            "reason": f"AI parse failed: {str(e)}",
            "spotify": None,
            "soundcloud_track": None,
            "dropbox": None,
            "wetransfer": None,
            "disco": None,
            "gdrive": None,
            "onedrive": None,
            "instagram": None,
            "usable_demo_links": [],
            "bulk_score": 0
        }


# =========================
# ENRICHMENT
# =========================
def enrich_email_result(
    result,
    subject,
    body,
    sender_name,
    sender_email,
    to_header,
    user_link_settings=None,
    user_reminder_settings=None,
    preview_mode=False
):
    body_lower = (body or "").lower()
    subject_lower = (subject or "").lower()
    sender_lower = (sender_email or "").lower()

    raw_html = result.get("raw_html", "")
    artist_hint = result.get("artist") or sender_name

    extracted_links = extract_all_links(
        body,
        raw_html,
        subject=subject,
        artist_name=artist_hint
    )

    soundcloud = extracted_links.get("soundcloud")
    dropbox = extracted_links.get("dropbox")
    wetransfer = extracted_links.get("wetransfer")
    disco = extracted_links.get("disco")
    gdrive = extracted_links.get("gdrive")
    onedrive = extracted_links.get("onedrive")
    instagram = extracted_links.get("instagram")
    spotify_url = extracted_links.get("spotify_url")

    is_bulk, bulk_score = is_bulk_submission_email(
        subject=subject,
        body=body,
        sender_email=sender_email,
        to_header=to_header,
        extracted_links=extracted_links,
        sender_name=sender_name
    )

    if "trackstack" in sender_lower or "trackstack" in subject_lower or "trackstack" in body_lower:
        result["category"] = "trackstack_submission"
        result["priority"] = "REVIEW"
        result["score"] = 72
        result["reason"] = "Trackstack detected"

    elif "labelradar" in sender_lower or "labelradar" in subject_lower or "labelradar" in body_lower:
        result["category"] = "labelradar_update"
        result["priority"] = "LOW"
        result["score"] = 25
        result["reason"] = "LabelRadar detected"

    elif is_distributor_update_email(subject, body, sender_email):
        result["category"] = "distributor_update"
        result["priority"] = "REVIEW"
        result["score"] = max(int(float(result.get("score", 0) or 0)), 70)
        result["reason"] = "Distributor update detected"

    elif is_business_reminder_email(subject, body, sender_email):
        result["category"] = "business_reminder"
        result["priority"] = "REVIEW"
        result["score"] = max(int(float(result.get("score", 0) or 0)), 82)
        result["reason"] = "Business reminder detected"

    elif is_royalty_statement_email(subject, body, sender_email):
        result["category"] = "royalty_statement"
        result["priority"] = "REVIEW"
        result["score"] = max(int(float(result.get("score", 0) or 0)), 80)
        result["reason"] = "Royalty statement detected"

    elif is_promo_reminder_email(subject, body, sender_email):
        result["category"] = "promo_reminder"
        result["priority"] = "LOW"
        result["score"] = min(max(int(float(result.get("score", 0) or 0)), 20), 55)
        result["reason"] = "Promo reminder detected"

    elif is_bulk:
        if result.get("category") not in ["demo", "high_priority_demo", "reply"]:
            result["category"] = "bulk_submission"
            result["priority"] = "LOW"
            if not result.get("reason"):
                result["reason"] = "Bulk signals detected"

    result["soundcloud_track"] = soundcloud
    result["dropbox"] = dropbox
    result["wetransfer"] = wetransfer
    result["disco"] = disco
    result["gdrive"] = gdrive
    result["onedrive"] = onedrive
    result["instagram"] = instagram
    result["workflow_links"] = get_detected_workflow_links(extracted_links)
    result["usable_demo_links"] = get_usable_demo_links(
        extracted_links=extracted_links,
        category=result.get("category"),
        user_link_settings=user_link_settings
    )
    result["bulk_score"] = bulk_score
    result["reminder_mode"] = get_reminder_mode(
        result.get("category"),
        user_reminder_settings=user_reminder_settings
    )
    result["suggested_action"] = get_suggested_action(
        result.get("category"),
        user_reminder_settings=user_reminder_settings
    )

    if DEBUG_HTML_URLS:
        result["all_html_urls"] = extracted_links.get("all_html_urls_debug", [])
        result["all_html_urls_raw"] = extracted_links.get("all_html_urls_raw", [])
    else:
        result["all_html_urls"] = []

    artist_name = result.get("artist") or extract_possible_artist_name(sender_name, subject, body)

    spotify_info = None
    category = result.get("category", "")

    if not preview_mode and category in ["demo", "high_priority_demo"]:
        if spotify_url:
            spotify_info = spotify_url
        elif is_reliable_spotify_artist_name(artist_name):
            spotify_search = search_spotify_artist(artist_name)
            if spotify_search:
                spotify_info = spotify_search

    result["spotify"] = spotify_info

    return result


# =========================
# PRIORITY NORMALIZATION
# =========================
def normalize_priority(result, inbox_profile="", user_reminder_settings=None):
    category = result.get("category", "")
    inbox_profile = (inbox_profile or "").lower().strip()

    if category == "reply":
        result["priority"] = "PRIORITY"

    elif category == "high_priority_demo":
        if inbox_profile == "demo_first":
            result["priority"] = "PRIORITY"
        elif inbox_profile in ["business_mixed", "personal_broad"]:
            result["priority"] = "REVIEW"
        elif inbox_profile == "promo_first":
            result["priority"] = "REVIEW"
        else:
            result["priority"] = "REVIEW"

    elif category == "trackstack_submission":
        if inbox_profile in ["demo_first", "business_mixed"]:
            result["priority"] = "REVIEW"
        else:
            result["priority"] = "LOW"

    elif category == "labelradar_update":
        if inbox_profile == "demo_first":
            result["priority"] = "REVIEW"
        else:
            result["priority"] = "LOW"

    elif category == "distributor_update":
        if inbox_profile in ["business_mixed", "personal_broad"]:
            result["priority"] = "REVIEW"
        elif inbox_profile == "demo_first":
            result["priority"] = "LOW"
        elif inbox_profile == "promo_first":
            result["priority"] = "LOW"
        else:
            result["priority"] = "REVIEW"

    elif category == "royalty_statement":
        if inbox_profile in ["business_mixed", "personal_broad"]:
            result["priority"] = "REVIEW"
        elif inbox_profile == "demo_first":
            result["priority"] = "LOW"
        elif inbox_profile == "promo_first":
            result["priority"] = "LOW"
        else:
            result["priority"] = "REVIEW"

    elif category == "business_reminder":
        mode = get_reminder_mode(category, user_reminder_settings)
        if mode == "show_low":
            result["priority"] = "LOW"
        elif inbox_profile in ["business_mixed", "personal_broad"]:
            result["priority"] = "REVIEW"
        elif inbox_profile == "demo_first":
            result["priority"] = "LOW"
        elif inbox_profile == "promo_first":
            result["priority"] = "LOW"
        else:
            result["priority"] = "REVIEW"

    elif category == "promo_reminder":
        mode = get_reminder_mode(category, user_reminder_settings)
        if mode == "show_normal":
            result["priority"] = "REVIEW"
        else:
            result["priority"] = "LOW"

    elif category == "demo":
        if inbox_profile == "demo_first":
            result["priority"] = "PRIORITY"
        elif inbox_profile in ["business_mixed", "personal_broad"]:
            result["priority"] = "REVIEW"
        elif inbox_profile == "promo_first":
            result["priority"] = "LOW"
        else:
            result["priority"] = "REVIEW"

    elif category == "incomplete_demo":
        if inbox_profile == "demo_first":
            result["priority"] = "REVIEW"
        else:
            result["priority"] = "LOW"

    elif category == "promo":
        if inbox_profile == "promo_first":
            result["priority"] = "REVIEW"
        else:
            result["priority"] = "LOW"

    elif category == "info":
        if inbox_profile in ["business_mixed", "personal_broad"]:
            result["priority"] = "REVIEW"
        else:
            result["priority"] = "LOW"

    elif category == "bulk_submission":
        result["priority"] = "LOW"

    elif category == "spam":
        result["priority"] = "LOW"

    else:
        result["priority"] = "LOW"

    return result


# =========================
# ANALYZE EMAIL
# =========================
def analyze_email(
    msg,
    inbox_name="",
    inbox_profile="",
    user_link_settings=None,
    user_reminder_settings=None,
    preview_mode=False
):
    analyze_start = time.perf_counter()
    subject = decode_mime_words(msg.get("Subject", ""))
    from_header = decode_mime_words(msg.get("From", ""))
    to_header = decode_mime_words(msg.get("To", ""))
    sender_name, sender_email = parseaddr(from_header)
    sender_name = decode_mime_words(sender_name)
    body, raw_html = extract_text_and_html_from_message(msg)

    result = classify_email_with_ai(
        subject=subject,
        body=body,
        sender_name=sender_name,
        sender_email=sender_email,
        to_header=to_header,
        inbox_profile=inbox_profile
    )

    result["raw_html"] = raw_html

    result = enrich_email_result(
        result=result,
        subject=subject,
        body=body,
        sender_name=sender_name,
        sender_email=sender_email,
        to_header=to_header,
        user_link_settings=user_link_settings,
        user_reminder_settings=user_reminder_settings,
        preview_mode=preview_mode
    )

    result = normalize_priority(
        result,
        inbox_profile=inbox_profile,
        user_reminder_settings=user_reminder_settings
    )
    result["subject"] = subject
    result["from"] = from_header
    result["inbox_name"] = inbox_name
    result["inbox_profile"] = inbox_profile
    logger.warning(
        "Preview analyze_email complete subject=%s sender=%s preview_mode=%s total_ms=%.1f category=%s priority=%s",
        subject[:120],
        sender_email,
        preview_mode,
        (time.perf_counter() - analyze_start) * 1000,
        result.get("category"),
        result.get("priority"),
    )
    return result


# =========================
# IMAP
# =========================
def connect_mailbox(email_address, password):
    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    mail.login(email_address, password)
    return mail


def fetch_recent_messages(mail, folder="INBOX", limit=TEST_LIMIT):
    mail.select(folder)
    status, messages = mail.search(None, "ALL")
    if status != "OK":
        return []

    mail_ids = messages[0].split()
    latest_ids = mail_ids[-limit:]

    results = []
    for mail_id in reversed(latest_ids):
        status, msg_data = mail.fetch(mail_id, "(RFC822)")
        if status != "OK":
            continue

        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)
        results.append(msg)

    return results


# =========================
# OUTPUT
# =========================
def print_result(result, mailbox_label):
    print("=" * 80)
    print(f"INBOX: {mailbox_label}")
    print(f"PROFILE: {result.get('inbox_profile', '')}")
    print(f"FROM: {result.get('from', '')}")
    print(f"SUBJECT: {result.get('subject', '')}")
    print(f"ARTIST: {result.get('artist', '')}")
    print(f"CATEGORY: {result.get('category', '')}")
    print(f"SCORE: {result.get('score', '')}")
    print(f"PRIORITY: {result.get('priority', '')}")
    print(f"REASON: {result.get('reason', '')}")
    print(f"SUGGESTED ACTION: {result.get('suggested_action', 'SHOW')}")
    print(f"REMINDER MODE: {result.get('reminder_mode', '')}")
    print(f"SPOTIFY: {json.dumps(result.get('spotify'), ensure_ascii=False) if isinstance(result.get('spotify'), dict) else result.get('spotify')}")
    print(f"SOUNDCLOUD: {result.get('soundcloud_track', '')}")
    print(f"DROPBOX: {result.get('dropbox', '')}")
    print(f"WETRANSFER: {result.get('wetransfer', '')}")
    print(f"DISCO: {result.get('disco', '')}")
    print(f"GOOGLE DRIVE: {result.get('gdrive', '')}")
    print(f"ONEDRIVE: {result.get('onedrive', '')}")
    print(f"INSTAGRAM: {result.get('instagram', '')}")
    print(f"USABLE DEMO LINKS: {result.get('usable_demo_links', [])}")
    print(f"WORKFLOW LINKS: {result.get('workflow_links', [])}")
    print(f"HTML URLS: {result.get('all_html_urls', [])}")
    print(f"BULK SCORE: {result.get('bulk_score', 0)}")
    
    mailbox_match = next(
        (
            m for m in V7_USER_CONFIG.mailboxes
            if m.email_address.split("@")[0].lower() == mailbox_label.replace("@", "").lower()
        ),
        None,
    )

    if mailbox_match:
        engine_result = EngineResult(
            inbox_name=mailbox_label,
            category=result.get("category", "unknown"),
            priority=result.get("priority", "NORMAL"),
            workflow_links=result.get("workflow_links", []),
            usable_demo_links=result.get("usable_demo_links", []),
            reason=result.get("reason", ""),
        )

        v7_decision = decide_message_behavior(
            engine_result=engine_result,
            user_config=V7_USER_CONFIG,
            mailbox_config=mailbox_match,
        )

        result["v7_final_priority"] = v7_decision.final_priority
        result["v7_final_visibility"] = v7_decision.final_visibility
        result["v7_action"] = v7_decision.action
        result["v7_explanation"] = v7_decision.explanation.final_summary
        result["ui_signal"] = map_to_ui_signal(result)

        print_v7_summary(result)
        print("-" * 80)

def process_mailbox(
    mailbox_config,
    limit=TEST_LIMIT,
    user_link_settings=None,
    user_reminder_settings=None
):
    label = mailbox_config["label"]
    inbox_name = mailbox_config.get("name", label)
    email_address = mailbox_config["email"]
    password = mailbox_config["password"]
    folder = mailbox_config.get("folder", "INBOX")
    inbox_profile = mailbox_config.get("profile", "business_mixed")

    if not email_address or not password:
        print("=" * 80)
        print(f"INBOX: {label}")
        print(f"PROFILE: {inbox_profile}")
        print("SKIPPED: missing email or password in .env")
        return

    try:
        print("\n" + "#" * 80)
        print(f"STARTING INBOX: {label}")
        print(f"PROFILE: {inbox_profile}")
        print("#" * 80)

        mail = connect_mailbox(email_address, password)
        messages = fetch_recent_messages(mail, folder=folder, limit=limit)

        if not messages:
            print(f"INBOX: {label} - no messages found")
            mail.logout()
            return

        for msg in messages:
            result = analyze_email(
                msg,
                inbox_name=inbox_name,
                inbox_profile=inbox_profile,
                user_link_settings=user_link_settings,
                user_reminder_settings=user_reminder_settings
            )
            print_result(result, label)

        mail.logout()

    except Exception as e:
        print("=" * 80)
        print(f"INBOX: {label}")
        print(f"PROFILE: {inbox_profile}")
        print(f"ERROR: {str(e)}")


# =========================
# MAIN
# =========================
def main():
    for mailbox in INBOX_CONFIG:
        process_mailbox(
            mailbox,
            limit=TEST_LIMIT,
            user_link_settings=USER_LINK_SETTINGS,
            user_reminder_settings=USER_REMINDER_SETTINGS
        )


if __name__ == "__main__":
    main()
