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

def get_spotify_profile(artist_name):
    results = sp.search(q=artist_name, type="artist", limit=5)
    items = results.get("artists", {}).get("items", [])

    if not items:
        print("Geen Spotify artiest gevonden")
        return None

    chosen_artist = None

    # Eerst exacte naam-match
    for artist in items:
        if artist.get("name", "").lower() == artist_name.lower():
            chosen_artist = artist
            break

    # Anders eerste resultaat
    if not chosen_artist:
        chosen_artist = items[0]

    spotify_url = chosen_artist.get("external_urls", {}).get("spotify")
    artist_name_found = chosen_artist.get("name", "Unknown")
    artist_id = chosen_artist.get("id")

    print("Spotify artiest gevonden:")
    print("Naam:", artist_name_found)
    print("Artist ID:", artist_id)
    print("Spotify profiel:", spotify_url)

    return spotify_url

get_spotify_profile("Oomloud")