import imaplib
import email
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
IMAP_SERVER = os.getenv("IMAP_SERVER")
IMAP_PORT = int(os.getenv("IMAP_PORT"))

client = OpenAI()

mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
mail.login(EMAIL_USER, EMAIL_PASS)

mail.select("inbox")

status, messages = mail.search(None, "ALL")

mail_ids = messages[0].split()
latest = mail_ids[-5:]

for num in latest:
    status, data = mail.fetch(num, "(RFC822)")

    for response_part in data:
        if isinstance(response_part, tuple):
            msg = email.message_from_bytes(response_part[1])

            print("-----")
            print("Van:", msg["from"])
            print("Onderwerp:", msg["subject"])

            body_text = ""

            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True)
                        body_text = body.decode(errors="ignore")[:500]
                        break
            else:
                body = msg.get_payload(decode=True)
                body_text = body.decode(errors="ignore")[:500]

            print("Body:", body_text)

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "user",
                        "content": f"Classificeer deze email als demo, promo, spam of reply. Antwoord met slechts één woord:\n\n{body_text}"
                    }
                ]
            )

            print("Categorie:", response.choices[0].message.content)