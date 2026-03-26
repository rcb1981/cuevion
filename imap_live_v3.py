import imaplib
import email
from email.header import decode_header
from dotenv import load_dotenv
import os
from openai import OpenAI
import re

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

# Load environment variables
load_dotenv()

# OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# IMAP settings
IMAP_SERVER = os.getenv("IMAP_SERVER")
EMAIL_ACCOUNT = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASS")

# Connect to mailbox
mail = imaplib.IMAP4_SSL(IMAP_SERVER)
mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
mail.select("inbox")

# Fetch latest emails
status, messages = mail.search(None, "ALL")
email_ids = messages[0].split()
mail_results = []

# Read last 5 emails
for email_id in email_ids[-5:]:

    status, msg_data = mail.fetch(email_id, "(RFC822)")

    for response_part in msg_data:
        if isinstance(response_part, tuple):

            msg = email.message_from_bytes(response_part[1])

            subject, encoding = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding if encoding else "utf-8")

            from_ = msg.get("From")
            body = ""

            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition"))

                    if content_type == "text/plain" and "attachment" not in content_disposition:
                        body = part.get_payload(decode=True).decode(errors="ignore")
                        instagram_url = extract_instagram_url(body)
                        soundcloud_url = extract_soundcloud_url(body)
                        dropbox_url = extract_dropbox_url(body)
                        print("Instagram:", instagram_url)
                        print("SoundCloud:", soundcloud_url)
                        print("Dropbox:", dropbox_url)
                        break
            else:
                body = msg.get_payload(decode=True).decode(errors="ignore")

            print("\n-----------------------------")
            print("FROM:", from_)
            print("SUBJECT:", subject)
            artist_candidate = subject

            if "-" in subject:
                parts = subject.split("-")
                if len(parts) >= 2:
                    artist_candidate = parts[0].replace("demo:", "").strip()

            if artist_candidate.lower() in ["demo submission via website", "re: demo", "demo submission"]:
                if "name" in body.lower():
                    try:
                        artist_candidate = body.split("name")[1].split("email")[0].strip()
                    except:
                        pass
            print("ARTIST CANDIDATE:", artist_candidate)

            clean_body = body.replace("\n", " ").replace("\r", " ")
            print("BODY:", clean_body[:200])

            # AI prompt
            prompt = f"""
You are an expert email classifier for an electronic music record label.

Classify this email into one category:

- high_priority_demo
- demo
- promo
- reply
- spam

Also give a score from 0 to 100.

Scoring logic:

high_priority_demo:
Artist presents professionally, mentions releases, labels, achievements, strong personal introduction, clear serious pitch.

demo:
Standard demo submission with music link but limited context.

promo:
Already released music, PR campaigns, label promo, press release.

reply:
Existing conversation, answers, short back-and-forth email.

spam:
Unrelated sales, junk, irrelevant content.

Important:

If artist mentions:
- previous labels
- professional background
- radio support
- known releases
- strong personal intro

Then prefer high_priority_demo.

Return only:

Category: ...
Score: ...

Email:
{body}
"""

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )

            ai_result = response.choices[0].message.content

            print("\nAI RESULT:")
            print(ai_result)

            # Safe priority logic
            try:
                score_line = ai_result.split("Score:")[1].strip().split("\n")[0]
                score = int(score_line)

                if score >= 85:
                    priority = "PRIORITY"
                elif score >= 60:
                    priority = "REVIEW"
                else:
                    priority = "LOW"

                print("Priority:", priority)

            except:
                print("Priority: UNKNOWN")

            print("-----------------------------")
            credibility_bonus = 0

            known_labels = ["sony", "trax", "records", "king street"]

            for label in known_labels:
                if "re:" not in subject.lower():

                   for label in known_labels:
                       if label in body.lower():
                           credibility_bonus = 10
                           break

            score = score + credibility_bonus
            print("Credibility bonus:", credibility_bonus)

            mail_results.append({
                "artist": artist_candidate,
                "from": from_,
                "subject": subject,
                "score": score,
                "priority": priority,
                "spotify": None,
                "soundcloud": None,
                "soundcloud_track": soundcloud_url,
                "dropbox": dropbox_url,
                "instagram": instagram_url
            })

sorted_results = sorted(mail_results, key=lambda x: x["score"], reverse=True)

print("\nMAIL RESULTS:")
for item in sorted_results:
    print(item)

mail.logout()