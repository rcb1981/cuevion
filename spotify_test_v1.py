import os
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

load_dotenv()

client_id = os.getenv("SPOTIFY_CLIENT_ID")
client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")

sp = spotipy.Spotify(
    auth_manager=SpotifyClientCredentials(
        client_id=client_id,
        client_secret=client_secret
    )
)

try:
    results = sp.search(q="Thanra", type="artist", limit=1)

    artist = results["artists"]["items"][0]

    print("Naam:", artist["name"])
    print("Followers:", artist["followers"]["total"])
    print("Popularity:", artist["popularity"])

    bonus = 0

    if artist["followers"]["total"] > 10000:
        bonus = 10
    elif artist["followers"]["total"] > 1000:
        bonus = 5

    print("Spotify bonus:", bonus)

except Exception as e:
    print("Spotify fout:", e)