import os
from dotenv import load_dotenv
import requests

load_dotenv()

client_id = os.getenv("SOUNDCLOUD_CLIENT_ID")

artist_name = "Thanra"

url = "https://api.soundcloud.com/users"

params = {
    "q": artist_name,
    "client_id": client_id,
    "limit": 1
}

response = requests.get(url, params=params)

print(response.json())