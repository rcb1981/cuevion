import imaplib
import email
from email.header import decode_header
from email.utils import parseaddr
from dotenv import load_dotenv
from openai import OpenAI
import os
import re
import json
import requests

# =========================
# LOAD ENV
# =========================
load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# =========================
# GLOBAL SETTINGS
# =========================
IMAP_SERVER = os.getenv("IMAP_SERVER")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

# =========================
# MULTI INBOX CONFIG V5
# =========================
MAILBOXES = [
    {
        "label": "demo@",
        "email": os.getenv("EMAIL_USER_DEMO"),
        "password": os.getenv("EMAIL_PASSWORD_DEMO"),
        "folder": "INBOX"
    },
    {
        "label": "info@",
        "email": os.getenv("EMAIL_USER_INFO"),
        "password": os.getenv("EMAIL_PASSWORD_INFO"),
        "folder": "INBOX"
    },
    {
        "label": "personal@",
        "email": os.getenv("EMAIL_USER_PERSONAL"),
        "password": os.getenv("EMAIL_PASSWORD_PERSONAL"),
        "folder": "INBOX"
    },
    {
        "label": "promo@",
        "email": os.getenv("EMAIL_USER_PROMO"),
        "password": os.getenv("EMAIL_PASSWORD_PROMO"),
        "folder": "INBOX"
    },
]

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


def extract_body_from_message(msg):
    body = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition") or "")

            if "attachment" in content_disposition.lower():
                continue

            if content_type == "text/plain":
                try:
                    body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="ignore")
                    if body.strip():
                        return clean_text(body)
                except:
                    pass

        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition") or "")

            if "attachment" in content_disposition.lower():
                continue

            if content_type == "text/html":
                try:
                    html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="ignore")
                    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
                    html = re.sub(r"</p>", "\n", html, flags=re.I)
                    html = re.sub(r"<[^>]+>", "", html)
                    return clean_text(html)
                except:
                    pass
    else:
        try:
            body = msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", errors="ignore")
        except:
            body = ""

    return clean_text(body)


def count_links(text):
    if not text:
        return 0
    urls = re.findall(r'https?://[^\s<>"\')]+', text, re.IGNORECASE)
    return len(urls)


def extract_soundcloud_url(text):
    if not text:
        return None

    patterns = [
        r"https?://on\.soundcloud\.com/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+",
        r"https?://(?:www\.)?soundcloud\.com/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+",
        r"https?://m\.soundcloud\.com/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            url = match.group(0)

            # strip common junk if fields are glued together
            for stopper in [
                "Beatport:",
                "Instagram:",
                "Spotify:",
                "Dropbox:",
                "SoundCloud:",
                "Facebook:",
                "YouTube:",
                "TikTok:",
                "Please",
                "please"
            ]:
                if stopper in url:
                    url = url.split(stopper)[0]

            return url.rstrip(").,]>'\"")

    return None


def extract_dropbox_url(text):
    if not text:
        return None
    match = re.search(r"https?://(www\.)?dropbox\.com/[^\s<>\"]+", text, re.IGNORECASE)
    return match.group(0) if match else None


def extract_wetransfer_url(text):
    if not text:
        return None
    match = re.search(r"https?://(www\.)?wetransfer\.com/[^\s<>\"]+", text, re.IGNORECASE)
    return match.group(0) if match else None


def extract_instagram_url(text):
    if not text:
        return None

    url_match = re.search(r"https?://(www\.)?instagram\.com/[A-Za-z0-9._-]+", text, re.IGNORECASE)
    if url_match:
        return url_match.group(0)

    handle_match = re.search(r"(instagram|ig)[:\s]+@([A-Za-z0-9._]+)", text, re.IGNORECASE)
    if handle_match:
        return "@" + handle_match.group(2)

    return None


def extract_spotify_url(text):
    if not text:
        return None
    match = re.search(r"https?://open\.spotify\.com/[^\s<>\"]+", text, re.IGNORECASE)
    return match.group(0) if match else None


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
            params={"q": artist_name, "type": "artist", "limit": 1},
            timeout=15
        )
        data = response.json()
        artists = data.get("artists", {}).get("items", [])
        if not artists:
            return None

        artist = artists[0]
        return {
            "name": artist.get("name"),
            "followers": artist.get("followers", {}).get("total"),
            "spotify_url": artist.get("external_urls", {}).get("spotify"),
            "popularity": artist.get("popularity")
        }
    except:
        return None


def extract_possible_artist_name(from_name, subject, body):
    if from_name and from_name.strip():
        return from_name.strip()
    if subject:
        return subject.strip()[:80]
    return None


# =========================
# V4 CLASSIFIER
# =========================
def classify_email_with_ai(subject, body, sender_name, sender_email, to_header):
    prompt = f"""
You classify music industry emails.

Return ONLY valid JSON with this structure:
{{
  "artist": "...",
  "category": "high_priority_demo|demo|incomplete_demo|promo|info|bulk_submission|spam|reply|trackstack_submission|labelradar_update",
  "score": 0,
  "priority": "PRIORITY|REVIEW|LOW",
  "reason": "...",
  "spotify": "...",
  "soundcloud_track": "...",
  "dropbox": "...",
  "wetransfer": "...",
  "instagram": "...",
  "usable_demo_links": [],
  "bulk_score": 0
}}

Rules:
- high_priority_demo = strong demo with usable link + strong signals
- demo = normal demo with usable SoundCloud or Dropbox link
- incomplete_demo = no usable demo link
- WeTransfer is NOT a usable demo link
- promo = only real music promo, such as out now / remix / bootleg / check out my track / out soon / new one by me
- promo wins before demo if clearly promo
- info = business/platform/company intro mail
- trackstack_submission = trackstack submission/update mail
- labelradar_update = LabelRadar update mail
- bulk_submission only when several bulk signals combine
- reply = actual reply/conversation mail
- spam = obvious junk

Important:
- In the music industry, "promo" means promotional music sent for DJ/radio/live use, not a general platform promotion.
- A platform/company introduction should be "info", not "promo".
- Forwarded trackstack mails should still become trackstack_submission when clear.
- Replies should stay PRIORITY.
"""

    try:
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

        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"^```\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        return data

    except Exception as e:
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
            "instagram": None,
            "usable_demo_links": [],
            "bulk_score": 0
        }
    
def is_reliable_spotify_artist_name(name):
    if not name:
        return False

    name = name.strip()

    if len(name) < 4:
        return False

    if "&" in name:
        return False

    words = name.split()

    # maak Spotify-search veel strenger:
    # alleen stage names / artist aliases van 1 woord
    if len(words) != 1:
        return False

    blocked_terms = [
        "records", "recordings", "music", "demo", "submission", "promo",
        "label", "labelradar", "trackstack", "spotify", "linkedin",
        "hysteriarecs.com", "hysteriarecs", "hysteria", "soundcloud",
        "dropbox", "wetransfer", "official"
    ]

    lower_name = name.lower()
    for term in blocked_terms:
        if term in lower_name:
            return False

    return True

def enrich_email_result(result, subject, body, sender_name, sender_email, to_header):
    body_lower = (body or "").lower()
    subject_lower = (subject or "").lower()
    sender_lower = (sender_email or "").lower()
    to_lower = (to_header or "").lower()

    soundcloud = extract_soundcloud_url(body)
    dropbox = extract_dropbox_url(body)
    wetransfer = extract_wetransfer_url(body)
    instagram = extract_instagram_url(body)
    spotify_url = extract_spotify_url(body)

    usable_demo_links = []
    if soundcloud:
        usable_demo_links.append(soundcloud)
    if dropbox:
        usable_demo_links.append(dropbox)

    # bulk score V4 logic
    bulk_score = 0
    if sender_lower and sender_lower in to_lower:
        bulk_score += 2
    if "catalogue" in body_lower:
        bulk_score += 2
    if count_links(body) >= 4:
        bulk_score += 1

    # trackstack override
    if "trackstack" in sender_lower or "trackstack" in subject_lower or "trackstack" in body_lower:
        result["category"] = "trackstack_submission"
        result["priority"] = "REVIEW"
        result["score"] = 72
        result["reason"] = "Trackstack detected"

    # labelradar override
    elif "labelradar" in sender_lower or "labelradar" in subject_lower or "labelradar" in body_lower:
        result["category"] = "labelradar_update"
        result["priority"] = "LOW"
        result["score"] = 25
        result["reason"] = "LabelRadar detected"

    # bulk override
    elif bulk_score >= 3:
        result["category"] = "bulk_submission"
        result["priority"] = "LOW"
        result["bulk_score"] = bulk_score
        if not result.get("reason"):
            result["reason"] = "Bulk signals detected"

    result["soundcloud_track"] = soundcloud
    result["dropbox"] = dropbox
    result["wetransfer"] = wetransfer
    result["instagram"] = instagram

    if result.get("category") in ["demo", "high_priority_demo", "reply"]:
        result["usable_demo_links"] = usable_demo_links
    else:
        result["usable_demo_links"] = []

    result["bulk_score"] = bulk_score

    # Spotify info tonen, niet scorebepalend
    artist_name = result.get("artist") or extract_possible_artist_name(sender_name, subject, body)

    spotify_info = None
    category = result.get("category", "")

    if category in ["demo", "high_priority_demo"]:
        if spotify_url:
            spotify_info = spotify_url
        elif is_reliable_spotify_artist_name(artist_name):
            spotify_search = search_spotify_artist(artist_name)
            if spotify_search:
                spotify_info = spotify_search

    result["spotify"] = spotify_info

    return result

def normalize_priority(result, inbox_name=""):
    category = result.get("category", "")
    inbox_name = (inbox_name or "").lower().strip()

    if category == "reply":
        result["priority"] = "PRIORITY"

    elif category == "high_priority_demo":
        result["priority"] = "PRIORITY"

    elif category == "trackstack_submission":
        if inbox_name == "demo@":
            result["priority"] = "REVIEW"
        else:
            result["priority"] = "LOW"

    elif category == "labelradar_update":
        result["priority"] = "LOW"

    elif category == "demo":
        if inbox_name == "demo@":
            result["priority"] = "PRIORITY"
        else:
            result["priority"] = "REVIEW"

    elif category == "incomplete_demo":
        result["priority"] = "LOW"

    elif category == "promo":
        if inbox_name == "promo@":
            result["priority"] = "REVIEW"
        else:
            result["priority"] = "LOW"

    elif category == "info":
        if inbox_name in ["info@", "personal@"]:
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

def analyze_email(msg, inbox_name=""):
    subject = decode_mime_words(msg.get("Subject", ""))
    from_header = decode_mime_words(msg.get("From", ""))
    to_header = decode_mime_words(msg.get("To", ""))
    sender_name, sender_email = parseaddr(from_header)
    sender_name = decode_mime_words(sender_name)
    body = extract_body_from_message(msg)

    result = classify_email_with_ai(
        subject=subject,
        body=body,
        sender_name=sender_name,
        sender_email=sender_email,
        to_header=to_header
    )

    result = enrich_email_result(
        result=result,
        subject=subject,
        body=body,
        sender_name=sender_name,
        sender_email=sender_email,
        to_header=to_header
        
    )
    result = normalize_priority(result, inbox_name)
    result["subject"] = subject
    result["from"] = from_header
    return result


# =========================
# IMAP
# =========================
def connect_mailbox(email_address, password):
    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    mail.login(email_address, password)
    return mail


def fetch_recent_messages(mail, folder="INBOX", limit=10):
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
    print(f"FROM: {result.get('from', '')}")
    print(f"SUBJECT: {result.get('subject', '')}")
    print(f"ARTIST: {result.get('artist', '')}")
    print(f"CATEGORY: {result.get('category', '')}")
    print(f"SCORE: {result.get('score', '')}")
    print(f"PRIORITY: {result.get('priority', '')}")
    print(f"REASON: {result.get('reason', '')}")
    print(f"SPOTIFY: {json.dumps(result.get('spotify'), ensure_ascii=False) if isinstance(result.get('spotify'), dict) else result.get('spotify')}")
    print(f"SOUNDCLOUD: {result.get('soundcloud_track', '')}")
    print(f"DROPBOX: {result.get('dropbox', '')}")
    print(f"WETRANSFER: {result.get('wetransfer', '')}")
    print(f"INSTAGRAM: {result.get('instagram', '')}")
    print(f"USABLE DEMO LINKS: {result.get('usable_demo_links', [])}")
    print(f"BULK SCORE: {result.get('bulk_score', 0)}")


def process_mailbox(mailbox_config, limit=10):
    label = mailbox_config["label"]
    email_address = mailbox_config["email"]
    password = mailbox_config["password"]
    folder = mailbox_config.get("folder", "INBOX")

    if not email_address or not password:
        print("=" * 80)
        print(f"INBOX: {label}")
        print("SKIPPED: missing email or password in .env")
        return

    try:
        print("\n" + "#" * 80)
        print(f"STARTING INBOX: {label}")
        print("#" * 80)

        mail = connect_mailbox(email_address, password)
        messages = fetch_recent_messages(mail, folder=folder, limit=limit)

        if not messages:
            print(f"INBOX: {label} - no messages found")
            mail.logout()
            return

        for msg in messages:
            result = analyze_email(msg, label)
            print_result(result, label)

        mail.logout()

    except Exception as e:
        print("=" * 80)
        print(f"INBOX: {label}")
        print(f"ERROR: {str(e)}")


# =========================
# MAIN
# =========================
def main():
    for mailbox in MAILBOXES:
        process_mailbox(mailbox, limit=10)


if __name__ == "__main__":
    main()