import imaplib
import email
from email.header import decode_header
from email.utils import parseaddr
from dotenv import load_dotenv
import os
from openai import OpenAI
import re
import json
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

# =========================
# LOAD ENV
# =========================
load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

IMAP_SERVER = os.getenv("IMAP_SERVER")
EMAIL_ACCOUNT = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASS")

# Spotify client
spotify_client = None
spotify_client_id = os.getenv("SPOTIFY_CLIENT_ID")
spotify_client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")

if spotify_client_id and spotify_client_secret:
    try:
        spotify_client = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=spotify_client_id,
                client_secret=spotify_client_secret
            )
        )
    except Exception as e:
        print("Spotify init error:", e)
        spotify_client = None

# =========================
# LINK EXTRACTION
# =========================
def extract_instagram_url(text):
    if not text:
        return None

    url_match = re.search(r"https?://(www\.)?instagram\.com/[A-Za-z0-9._-]+", text)
    if url_match:
        return url_match.group(0)

    handle_match = re.search(r"(instagram|ig)[:\s]+@([A-Za-z0-9._]+)", text, re.IGNORECASE)
    if handle_match:
        return "@" + handle_match.group(2)

    return None


def extract_soundcloud_url(text):
    if not text:
        return None

    match = re.search(r"https?://(on\.)?soundcloud\.com/[A-Za-z0-9._/\-?=&]+", text)
    if match:
        return match.group(0)

    return None


def extract_dropbox_url(text):
    if not text:
        return None

    match = re.search(r"https?://(www\.)?dropbox\.com/[A-Za-z0-9._/\-?=&]+", text)
    if match:
        return match.group(0)

    return None


def extract_wetransfer_url(text):
    if not text:
        return None

    match = re.search(r"https?://(www\.)?(wetransfer\.com|we\.tl)/[A-Za-z0-9._/\-?=&]+", text)
    if match:
        return match.group(0)

    return None


def extract_spotify_url(text):
    if not text:
        return None

    match = re.search(r"https?://open\.spotify\.com/[A-Za-z0-9._/\-?=&]+", text)
    if match:
        return match.group(0)

    return None


def extract_all_urls(text):
    if not text:
        return []

    urls = re.findall(r"https?://[^\s<>\"]+", text)
    cleaned = []
    seen = set()

    for url in urls:
        url = url.strip().rstrip(").,>")
        if url not in seen:
            cleaned.append(url)
            seen.add(url)

    return cleaned


def get_usable_demo_links(body):
    usable = []

    soundcloud_url = extract_soundcloud_url(body)
    dropbox_url = extract_dropbox_url(body)

    if soundcloud_url:
        usable.append(soundcloud_url)

    if dropbox_url:
        usable.append(dropbox_url)

    return usable


def find_spotify_profile(artist_name):
    if not artist_name:
        return None

    if not spotify_client:
        return None

    try:
        results = spotify_client.search(q=artist_name, type="artist", limit=5)
        items = results.get("artists", {}).get("items", [])

        if not items:
            return None

        chosen_artist = None

        for artist in items:
            if artist.get("name", "").lower() == artist_name.lower():
                chosen_artist = artist
                break

        if not chosen_artist:
            chosen_artist = items[0]

        return chosen_artist.get("external_urls", {}).get("spotify")

    except Exception as e:
        print("Spotify search error:", e)
        return None


# =========================
# TEXT / HEADER HELPERS
# =========================
def decode_mime_text(value):
    if not value:
        return ""

    decoded_parts = decode_header(value)
    final_text = ""

    for part, encoding in decoded_parts:
        if isinstance(part, bytes):
            final_text += part.decode(encoding or "utf-8", errors="ignore")
        else:
            final_text += part

    return final_text


def get_email_body(msg):
    body = ""

    if msg.is_multipart():
        plain_body = ""
        html_body = ""

        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", "")).lower()

            if "attachment" in content_disposition:
                continue

            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue

                charset = part.get_content_charset() or "utf-8"
                decoded = payload.decode(charset, errors="ignore")
            except:
                decoded = ""

            if content_type == "text/plain" and decoded:
                plain_body += decoded + "\n"
            elif content_type == "text/html" and decoded:
                html_body += decoded + "\n"

        if plain_body.strip():
            body = plain_body
        else:
            body = re.sub(r"<[^>]+>", " ", html_body)
    else:
        try:
            body = msg.get_payload(decode=True).decode(errors="ignore")
        except:
            body = ""

    return body.strip()


def extract_artist_candidate(subject, body):
    artist_candidate = subject

    if "-" in subject:
        parts = subject.split("-")
        if len(parts) >= 2:
            artist_candidate = parts[0].replace("demo:", "").replace("Demo:", "").strip()

    low = artist_candidate.lower().strip()
    generic_subjects = [
        "demo submission via website",
        "re: demo",
        "demo submission",
        "demo",
    ]

    if low in generic_subjects:
        if "name" in body.lower():
            try:
                artist_candidate = body.split("name")[1].split("email")[0].strip(" :\n\r\t-")
            except:
                pass

    return artist_candidate.strip()


# =========================
# SIGNAL DETECTION
# =========================
def is_reply(subject):
    subject_lower = subject.lower().strip()
    return (
        subject_lower.startswith("re:")
        or subject_lower.startswith("aw:")
        or subject_lower.startswith("sv:")
    )


def detect_spam(subject, body):
    text = (subject + " " + body).lower()

    spam_keywords = [
        "crypto",
        "bitcoin",
        "casino",
        "seo",
        "backlinks",
        "guest post",
        "forex",
        "loan",
        "betting",
        "adult",
        "viagra",
        "web traffic",
    ]

    spam_hits = 0
    for word in spam_keywords:
        if word in text:
            spam_hits += 1

    return spam_hits >= 2


def detect_promo(subject, body, headers_text):
    text = (subject + " " + body + " " + headers_text).lower()

    promo_keywords = [
        "unsubscribe",
        "view in browser",
        "mailing list",
        "newsletter",
        "out now",
        "promo",
        "press release",
        "pre-save",
        "presave",
        "support list",
        "topline pack",
        "sample pack",
        "bootleg pack",
        "available now",
        "check out my track",
        "out soon",
        "bootleg",
        "remix",
    ]

    promo_hits = 0
    for word in promo_keywords:
        if word in text:
            promo_hits += 1

    if "list-unsubscribe" in headers_text.lower():
        promo_hits += 2

    return promo_hits >= 1


def detect_bulk_submission(body, to_header, sender_email, link_count):
    bulk_score = 0
    body_lower = body.lower()
    to_lower = (to_header or "").lower()

    bulk_keywords = [
        "catalogue",
        "catalog",
        "many genres",
        "more demos",
        "all my tracks",
        "all my music",
        "check my catalog",
        "check my catalogue",
        "music catalog",
        "music catalogue",
    ]

    generic_pitch_keywords = [
        "dear label",
        "hi team",
        "hello team",
        "dear sir",
        "dear madam",
        "hope you are well",
        "please listen",
    ]

    if sender_email and sender_email.lower() in to_lower:
        bulk_score += 2

    for word in bulk_keywords:
        if word in body_lower:
            bulk_score += 2
            break

    if link_count >= 4:
        bulk_score += 1

    generic_hits = 0
    for word in generic_pitch_keywords:
        if word in body_lower:
            generic_hits += 1

    if generic_hits >= 2:
        bulk_score += 1

    return bulk_score >= 3, bulk_score


def detect_incomplete_demo(body):
    usable_links = get_usable_demo_links(body)
    return len(usable_links) == 0


# =========================
# AI ANALYSIS
# =========================
def analyze_email_with_ai(subject, from_, artist_candidate, body, usable_demo_links):
    prompt = f"""
You are an expert A&R assistant for an electronic music label.

Your job:
Judge whether this is a serious, usable, targeted demo submission.

Important rules:
- SoundCloud or Dropbox presence is NOT a credibility bonus by itself.
- If there is no usable demo link, the email is incomplete.
- WeTransfer is NOT considered a usable demo link.
- Multiple links alone do NOT automatically mean bulk submission.
- Promo/newsletter style should be treated as promo, not demo.
- Replies should stay separate from demo categories.

Return ONLY valid JSON in this format:

{{
  "demo_quality_score": 0,
  "professionalism_score": 0,
  "label_fit_score": 0,
  "credibility_bonus": 0,
  "reason": ""
}}

Scoring meaning:
- demo_quality_score: 0-10
- professionalism_score: 0-10
- label_fit_score: 0-10
- credibility_bonus: 0-10

Only give credibility bonus when there are real credibility signals like:
- known labels
- previous releases
- support
- achievements
- clear professional background

Email details:
Subject: {subject}
From: {from_}
Artist candidate: {artist_candidate}
Usable demo links: {usable_demo_links}

Email body:
{body[:6000]}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        raw = response.choices[0].message.content.strip()
        data = json.loads(raw)

        return {
            "demo_quality_score": max(0, min(10, int(data.get("demo_quality_score", 0)))),
            "professionalism_score": max(0, min(10, int(data.get("professionalism_score", 0)))),
            "label_fit_score": max(0, min(10, int(data.get("label_fit_score", 0)))),
            "credibility_bonus": max(0, min(10, int(data.get("credibility_bonus", 0)))),
            "reason": str(data.get("reason", "")).strip()
        }

    except Exception as e:
        print("AI JSON parse / request error:", e)
        return {
            "demo_quality_score": 0,
            "professionalism_score": 0,
            "label_fit_score": 0,
            "credibility_bonus": 0,
            "reason": "AI analysis failed"
        }


# =========================
# CATEGORY ENGINE
# =========================
def determine_category(subject, body, headers_text, to_header, sender_email, ai_scores):
    all_urls = extract_all_urls(body)
    usable_demo_links = get_usable_demo_links(body)

    reply_flag = is_reply(subject)
    spam_flag = detect_spam(subject, body)
    promo_flag = detect_promo(subject, body, headers_text)
    bulk_flag, bulk_score = detect_bulk_submission(body, to_header, sender_email, len(all_urls))
    incomplete_flag = detect_incomplete_demo(body)

    if reply_flag:
        return "reply", bulk_score

    if spam_flag:
        return "spam", bulk_score
    
    if "trackstack" in sender_email.lower() or "trackstack" in subject.lower() or "trackstack" in body.lower():
        return "trackstack_submission", bulk_score

    if "labelradar" in sender_email.lower() or "labelradar" in subject.lower() or "labelradar" in body.lower():
        return "labelradar_update", bulk_score
    
    if "introducing" in body.lower() or "platform designed" in body.lower() or "founder" in body.lower():
        return "info", bulk_score

    if promo_flag:
        return "promo", bulk_score

    if bulk_flag:
        return "bulk_submission", bulk_score

    if incomplete_flag:
        return "incomplete_demo", bulk_score

    total_strength = (
        ai_scores["demo_quality_score"]
        + ai_scores["professionalism_score"]
        + ai_scores["label_fit_score"]
        + ai_scores["credibility_bonus"]
    )

    if detect_promo(subject, body, headers_text):
        return "promo", bulk_score

    if (
        len(usable_demo_links) >= 1
        and ai_scores["demo_quality_score"] >= 7
        and ai_scores["professionalism_score"] >= 7
        and ai_scores["label_fit_score"] >= 7
        and total_strength >= 24
    ):
        return "high_priority_demo", bulk_score

    return "demo", bulk_score


# =========================
# SCORE + PRIORITY
# =========================
def calculate_score_and_priority(category, ai_scores):
    if category == "spam":
        score = 5
        priority = "LOW"

    elif category == "trackstack_submission":
        score = 72
        priority = "REVIEW"

    elif category == "labelradar_update":
        score = 25
        priority = "LOW"

    elif category == "info":
        score = 30
        priority = "LOW"

    elif category == "promo":
        score = 15
        priority = "LOW"

    elif category == "bulk_submission":
        score = 20
        priority = "LOW"

    elif category == "incomplete_demo":
        score = 18
        priority = "LOW"

    elif category == "reply":
        score = 80 + ai_scores["professionalism_score"]
        if score > 95:
            score = 95
        priority = "PRIORITY"

    elif category == "demo":
        score = (
            45
            + ai_scores["demo_quality_score"] * 2
            + ai_scores["professionalism_score"] * 2
            + ai_scores["label_fit_score"] * 2
            + ai_scores["credibility_bonus"]
        )
        if score > 89:
            score = 89

        if score >= 70:
            priority = "REVIEW"
        else:
            priority = "LOW"

    elif category == "high_priority_demo":
        score = (
            78
            + ai_scores["demo_quality_score"]
            + ai_scores["professionalism_score"]
            + ai_scores["label_fit_score"]
            + ai_scores["credibility_bonus"]
        )
        if score > 98:
            score = 98
        priority = "PRIORITY"

    else:
        score = 25
        priority = "LOW"

    return score, priority


# =========================
# CONNECT TO MAILBOX
# =========================
mail = imaplib.IMAP4_SSL(IMAP_SERVER)
mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
mail.select("inbox")

status, messages = mail.search(None, "ALL")
email_ids = messages[0].split()
mail_results = []

# Read last 5 emails
for email_id in email_ids[-5:]:
    status, msg_data = mail.fetch(email_id, "(RFC822)")

    for response_part in msg_data:
        if not isinstance(response_part, tuple):
            continue

        msg = email.message_from_bytes(response_part[1])

        subject = decode_mime_text(msg.get("Subject", ""))
        from_ = decode_mime_text(msg.get("From", ""))
        to_ = decode_mime_text(msg.get("To", ""))
        headers_text = "\n".join([f"{k}: {v}" for k, v in msg.items()])

        sender_name, sender_email = parseaddr(from_)
        body = get_email_body(msg)

        instagram_url = extract_instagram_url(body)
        soundcloud_url = extract_soundcloud_url(body)
        dropbox_url = extract_dropbox_url(body)
        wetransfer_url = extract_wetransfer_url(body)

        artist_candidate = extract_artist_candidate(subject, body)

        # Eerst spotify link uit mail zelf proberen, anders profiel zoeken
        spotify_url = extract_spotify_url(body)
        if not spotify_url:
            spotify_url = find_spotify_profile(artist_candidate)

        usable_demo_links = get_usable_demo_links(body)
        all_urls = extract_all_urls(body)

        print("\n-----------------------------")
        print("FROM:", from_)
        print("TO:", to_)
        print("SUBJECT:", subject)
        print("ARTIST CANDIDATE:", artist_candidate)

        clean_body = body.replace("\n", " ").replace("\r", " ")
        print("BODY:", clean_body[:300])

        print("Instagram:", instagram_url)
        print("SoundCloud track:", soundcloud_url)
        print("Dropbox:", dropbox_url)
        print("WeTransfer:", wetransfer_url)
        print("Spotify:", spotify_url)
        print("Usable demo links:", usable_demo_links)

        ai_scores = analyze_email_with_ai(
            subject=subject,
            from_=from_,
            artist_candidate=artist_candidate,
            body=body,
            usable_demo_links=usable_demo_links
        )

        category, bulk_score = determine_category(
            subject=subject,
            body=body,
            headers_text=headers_text,
            to_header=to_,
            sender_email=sender_email,
            ai_scores=ai_scores
        )

        score, priority = calculate_score_and_priority(category, ai_scores)

        print("\nAI SCORES:")
        print(ai_scores)
        print("Category:", category)
        print("Score:", score)
        print("Priority:", priority)
        print("Bulk score:", bulk_score)
        print("-----------------------------")

        mail_results.append({
            "artist": artist_candidate,
            "from": from_,
            "subject": subject,
            "category": category,
            "score": score,
            "priority": priority,
            "reason": ai_scores["reason"],
            "spotify": spotify_url,
            "soundcloud": None,
            "soundcloud_track": soundcloud_url,
            "dropbox": dropbox_url,
            "wetransfer": wetransfer_url,
            "instagram": instagram_url,
            "usable_demo_links": usable_demo_links,
            "all_url_count": len(all_urls),
            "bulk_score": bulk_score
        })

sorted_results = sorted(mail_results, key=lambda x: x["score"], reverse=True)

print("\nMAIL RESULTS:")
for item in sorted_results:
    print(item)

mail.logout()