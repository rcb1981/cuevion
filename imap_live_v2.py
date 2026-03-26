import imaplib
import email
from email.header import decode_header
from dotenv import load_dotenv
import os
from openai import OpenAI

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
                        break
            else:
                body = msg.get_payload(decode=True).decode(errors="ignore")

            print("\n-----------------------------")
            print("FROM:", from_)
            print("SUBJECT:", subject)

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
            mail_results.append({
                "from": from_,
                "subject": subject,
                "score": score,
                "priority": priority
            })

sorted_results = sorted(mail_results, key=lambda x: x["score"], reverse=True)

print("\nMAIL RESULTS:")
for item in sorted_results:
    print(item)

mail.logout()