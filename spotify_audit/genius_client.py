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

from spotify_audit.name_matching import (
    similarity_score, min_confidence_for_length, MatchResult, log_match,
)

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
    # Match quality metadata (from name_matching)
    match_confidence: float = 0.0
    match_method: str = ""


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

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self.session.close()

    def _get(self, path: str, params: dict | None = None) -> dict:
        if not self.enabled:
            return {}
        url = f"{GENIUS_API}{path}"
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                r = self.session.get(url, params=params, timeout=15)
            except requests.RequestException as exc:
                last_exc = exc
                wait = 2 ** (attempt + 1)
                logger.debug("Genius request failed (attempt %d): %s", attempt + 1, exc)
                time.sleep(wait)
                continue
            if r.status_code in (401, 403):
                logger.warning("Genius API key invalid (HTTP %d) — disabling client", r.status_code)
                self.enabled = False
                return {}
            if r.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.debug("Genius 429 rate-limited, backing off %ds", wait)
                time.sleep(wait)
                continue
            if r.status_code in (500, 502, 503):
                wait = 2 ** (attempt + 1)
                logger.debug("Genius %d server error, backing off %ds", r.status_code, wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            time.sleep(self.delay)
            try:
                return r.json()
            except (ValueError, requests.exceptions.JSONDecodeError):
                logger.warning("Genius returned non-JSON for %s", path)
                return {}
        if last_exc:
            raise last_exc
        r.raise_for_status()  # raise on final 429
        return {}

    def search_artist(self, name: str, genius_id: str | None = None) -> GeniusArtist | None:
        """Search for an artist by name using shared name matching.

        Args:
            name: Artist name to search for.
            genius_id: Optional Genius artist ID from MusicBrainz URL bridging.
                       If provided, skips name search entirely.
        """
        if not self.enabled:
            return None

        # Platform ID bridging: skip search if we have a direct ID
        if genius_id:
            try:
                data = self._get(f"/artists/{genius_id}")
                artist_data = data.get("response", {}).get("artist", {})
                if artist_data:
                    mr = MatchResult(
                        found=True, confidence=1.0,
                        matched_name=artist_data.get("name", ""),
                        platform_id=str(genius_id),
                        match_method="platform_id",
                    )
                    log_match("Genius", name, mr)
                    return GeniusArtist(
                        genius_id=int(genius_id) if str(genius_id).isdigit() else 0,
                        name=artist_data.get("name", ""),
                        url=artist_data.get("url", ""),
                        image_url=artist_data.get("image_url", ""),
                        match_confidence=mr.confidence,
                        match_method=mr.match_method,
                    )
            except Exception as exc:
                logger.debug("Genius ID lookup failed for %s: %s", genius_id, exc)

        # Try multiple search variants to improve matching.
        # Genius /search finds songs, not artists directly, so the artist name
        # might not appear in song titles. Trying normalized/variant forms helps.
        from spotify_audit.name_matching import (
            pick_best_match, generate_candidates, normalize_name,
        )

        search_variants = [name]
        # Add normalized variants (strips accents, punctuation, etc.)
        for variant in generate_candidates(name):
            if variant not in search_variants:
                search_variants.append(variant)
        # Limit to 3 API calls to avoid rate limiting
        search_variants = search_variants[:3]

        seen_ids: set[int] = set()
        all_candidates: list[dict] = []

        for query in search_variants:
            data = self._get("/search", {"q": query, "per_page": 15})
            hits = data.get("response", {}).get("hits", [])

            for hit in hits:
                result = hit.get("result", {})
                primary = result.get("primary_artist", {})
                pid = primary.get("id", 0)
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    all_candidates.append({
                        "name": primary.get("name", ""),
                        "id": pid,
                        "url": primary.get("url", ""),
                        "image_url": primary.get("image_url", ""),
                        "aliases": primary.get("alternate_names", []) or [],
                    })

            # If we already found a strong match, skip remaining variants
            if all_candidates:
                match = pick_best_match(name, all_candidates)
                if match.found and match.confidence >= 0.95:
                    break

        if not all_candidates:
            log_match("Genius", name, MatchResult(found=False))
            return None

        match = pick_best_match(name, all_candidates)
        log_match("Genius", name, match)

        if match.found and match.platform_id:
            best = next(
                (c for c in all_candidates if str(c["id"]) == match.platform_id),
                all_candidates[0],
            )
            return GeniusArtist(
                genius_id=best["id"],
                name=best["name"],
                url=best.get("url", ""),
                image_url=best.get("image_url", ""),
                match_confidence=match.confidence,
                match_method=match.match_method,
            )

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
