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
    # Social links & identity (from GET /artists/{id})
    facebook_name: str = ""
    instagram_name: str = ""
    twitter_name: str = ""
    is_verified: bool = False
    followers_count: int = 0
    alternate_names: list[str] = field(default_factory=list)


class GeniusClient:
    """Thin wrapper around the Genius API for songwriter lookups."""

    def __init__(self, access_token: str, delay: float = 0.3) -> None:
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {access_token}"
        self.session.headers["Accept"] = "application/json"
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10, pool_maxsize=10,
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
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
        """Search for an artist by name.

        Uses the /search endpoint (which returns songs) and extracts the
        primary_artist from each hit.  First tries exact name match, then
        falls back to case-insensitive containment so we handle slight
        name variations between Spotify/Deezer and Genius.
        """
        if not self.enabled:
            return None
        data = self._get("/search", {"q": name, "per_page": 15})
        hits = data.get("response", {}).get("hits", [])

        if not hits:
            logger.warning("Genius: 0 search hits for '%s'", name)
            return None

        name_lower = name.lower().strip()

        # Pass 1: exact match
        seen_ids: set[int] = set()
        candidates: list[dict] = []
        for hit in hits:
            result = hit.get("result", {})
            primary = result.get("primary_artist", {})
            pid = primary.get("id", 0)
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                candidates.append(primary)
                if primary.get("name", "").lower().strip() == name_lower:
                    logger.debug("Genius: exact match for '%s' → id %d", name, pid)
                    return GeniusArtist(
                        genius_id=pid,
                        name=primary.get("name", ""),
                        url=primary.get("url", ""),
                        image_url=primary.get("image_url", ""),
                    )

        # Pass 2: containment match (handles "The National" vs "National")
        for primary in candidates:
            pname = primary.get("name", "").lower().strip()
            if name_lower in pname or pname in name_lower:
                pid = primary.get("id", 0)
                logger.debug("Genius: partial match for '%s' → '%s' (id %d)",
                             name, primary.get("name", ""), pid)
                return GeniusArtist(
                    genius_id=pid,
                    name=primary.get("name", ""),
                    url=primary.get("url", ""),
                    image_url=primary.get("image_url", ""),
                )

        logger.warning("Genius: no name match for '%s' in %d candidates: %s",
                        name, len(candidates),
                        [c.get("name", "") for c in candidates[:5]])
        return None

    def get_artist_songs_count(self, genius_id: int, sort: str = "popularity") -> int:
        """Get total number of songs for an artist."""
        if not self.enabled or genius_id == 0:
            return 0
        data = self._get(f"/artists/{genius_id}/songs", {"per_page": 1, "sort": sort})
        response = data.get("response", {})
        songs = response.get("songs", [])
        if not songs:
            return 0
        # Use next_page to infer total: fetch page with per_page=50
        data = self._get(f"/artists/{genius_id}/songs", {"per_page": 50, "sort": sort})
        page_songs = data.get("response", {}).get("songs", [])
        next_page = data.get("response", {}).get("next_page")
        if next_page:
            # More than 50 songs — estimate conservatively
            return max(len(page_songs) * 2, 50)
        return len(page_songs)

    def enrich(self, artist: GeniusArtist) -> GeniusArtist:
        """Populate song count, credit info, social links, and identity data."""
        if not self.enabled or artist.genius_id == 0:
            return artist

        artist.song_count = self.get_artist_songs_count(artist.genius_id)

        # Get artist metadata (includes social links, verified status, etc.)
        data = self._get(f"/artists/{artist.genius_id}")
        artist_data = data.get("response", {}).get("artist", {})

        # Description snippet for bio analysis
        desc = artist_data.get("description", {})
        if isinstance(desc, dict):
            artist.description_snippet = desc.get("plain", "")[:500]

        # Social links
        artist.facebook_name = artist_data.get("facebook_name", "") or ""
        artist.instagram_name = artist_data.get("instagram_name", "") or ""
        artist.twitter_name = artist_data.get("twitter_name", "") or ""

        # Verified status and followers
        artist.is_verified = bool(artist_data.get("is_verified", False))
        artist.followers_count = artist_data.get("followers_count", 0) or 0

        # Alternate names
        alt_names = artist_data.get("alternate_names", [])
        if isinstance(alt_names, list):
            artist.alternate_names = [n for n in alt_names if isinstance(n, str)]

        return artist
