from dotenv import load_dotenv
from openai import OpenAI
import os

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

email_text = """
Subject: New techno demo submission

Hi label team,

Please find attached my latest techno EP for possible release.
Looking forward to your feedback.

Best,
Artist
"""

response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[
        {
            "role": "user",
            "content": f"""
Classificeer deze email in één categorie:
- demo
- promo
- reminder
- other

Email:
{email_text}
"""
        }
    ]
)

print(response.choices[0].message.content)