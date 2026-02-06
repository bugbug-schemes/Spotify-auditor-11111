"""
Genius API client for songwriter/producer credit lookups.

Requires a free access token from https://genius.com/api-clients
Used to check whether an artist has real songwriter credits — ghost/AI
artists typically have zero writing credits.
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

GENIUS_API = "https://api.genius.com"


@dataclass
class GeniusArtist:
    genius_id: int = 0
    name: str = ""
    url: str = ""
    image_url: str = ""
    # Populated by enrich()
    song_count: int = 0
    songwriting_credits: int = 0       # songs they wrote for others
    producer_credits: int = 0          # songs they produced
    featured_credits: int = 0          # featured appearances
    description_snippet: str = ""


class GeniusClient:
    """Thin wrapper around the Genius API for songwriter lookups."""

    def __init__(self, access_token: str, delay: float = 0.3) -> None:
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {access_token}"
        self.session.headers["Accept"] = "application/json"
        self.delay = delay
        self.enabled = bool(access_token)

    def _get(self, path: str, params: dict | None = None) -> dict:
        if not self.enabled:
            return {}
        url = f"{GENIUS_API}{path}"
        r = self.session.get(url, params=params, timeout=15)
        r.raise_for_status()
        time.sleep(self.delay)
        return r.json()

    def search_artist(self, name: str) -> GeniusArtist | None:
        """Search for an artist by name."""
        if not self.enabled:
            return None
        data = self._get("/search", {"q": name, "per_page": 5})
        hits = data.get("response", {}).get("hits", [])

        name_lower = name.lower().strip()
        for hit in hits:
            result = hit.get("result", {})
            primary = result.get("primary_artist", {})
            if primary.get("name", "").lower().strip() == name_lower:
                return GeniusArtist(
                    genius_id=primary.get("id", 0),
                    name=primary.get("name", ""),
                    url=primary.get("url", ""),
                    image_url=primary.get("image_url", ""),
                )
        return None

    def get_artist_songs_count(self, genius_id: int, sort: str = "popularity") -> int:
        """Get total number of songs for an artist."""
        if not self.enabled or genius_id == 0:
            return 0
        data = self._get(f"/artists/{genius_id}/songs", {"per_page": 1, "sort": sort})
        response = data.get("response", {})
        # The API doesn't return a total count directly; we check if songs exist
        songs = response.get("songs", [])
        if not songs:
            return 0
        # Paginate to count (limited to first page for speed)
        data = self._get(f"/artists/{genius_id}/songs", {"per_page": 50, "sort": sort})
        return len(data.get("response", {}).get("songs", []))

    def enrich(self, artist: GeniusArtist) -> GeniusArtist:
        """Populate song count and credit info."""
        if not self.enabled or artist.genius_id == 0:
            return artist

        artist.song_count = self.get_artist_songs_count(artist.genius_id)

        # Get artist metadata
        data = self._get(f"/artists/{artist.genius_id}")
        artist_data = data.get("response", {}).get("artist", {})

        # Description snippet for bio analysis
        desc = artist_data.get("description", {})
        if isinstance(desc, dict):
            artist.description_snippet = desc.get("plain", "")[:500]

        return artist
