"""
Deezer AI content tag detection (Priority 2).

Deezer is the first major streaming platform to actively tag AI-generated
content. This module checks for AI indicators via:

1. Deezer public API album/track responses — checks for any AI-related
   fields that Deezer may expose (they've been rolling out AI labeling).
2. Server-rendered page data — Deezer's SPA includes JSON state in the
   initial HTML. This contains album metadata that may include AI flags
   not yet in the public API.

Only runs for artists with existing red flags (conditional enrichment).
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

DEEZER_API = "https://api.deezer.com"
DEEZER_WEB = "https://www.deezer.com"

# Known AI-related fields that Deezer may include in API responses.
# Updated as Deezer evolves their API.
_AI_API_FIELDS = [
    "ai_generated", "is_ai", "content_type", "ai_content",
    "generated_by_ai", "artificial",
]

# Patterns in Deezer's SSR JSON state that indicate AI tagging.
_AI_SSR_PATTERNS = [
    re.compile(r'"ai[_-]?generated"\s*:\s*true', re.I),
    re.compile(r'"content[_-]?type"\s*:\s*"ai', re.I),
    re.compile(r'"isAi"\s*:\s*true', re.I),
    re.compile(r'"is_ai_generated"\s*:\s*true', re.I),
]

# Text patterns visible in the rendered page (badges, labels).
_AI_TEXT_PATTERNS = [
    re.compile(r"AI[\s-]+generated", re.I),
    re.compile(r"generated\s+by\s+AI", re.I),
    re.compile(r"contenu\s+généré\s+par\s+(?:l[''])?IA", re.I),  # French
    re.compile(r"artificial(?:ly)?[\s-]+(?:intelligence[\s-]+)?generated", re.I),
]


@dataclass
class DeezerAIResult:
    """Result of Deezer AI tag check for an artist."""
    checked: bool = False           # Did we actually check?
    ai_tagged_albums: list[str] = field(default_factory=list)  # Album titles flagged as AI
    albums_checked: int = 0
    detection_method: str = ""      # How AI was detected: "api_field", "ssr_data", "page_text"
    error: str = ""


class DeezerAIChecker:
    """Check Deezer for AI-generated content tags on an artist's albums."""

    def __init__(self, delay: float = 1.5):
        self.delay = delay
        self.enabled = True
        self._session = requests.Session()
        self._session.headers["User-Agent"] = (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

    def check_artist(
        self,
        deezer_artist_id: int,
        max_albums: int = 3,
    ) -> DeezerAIResult:
        """Check an artist's albums for AI content tags.

        Three-pass detection:
        1. Check album API response for AI-related fields
        2. Check album page SSR JSON state for AI flags
        3. Check visible page text for AI badges/labels

        Args:
            deezer_artist_id: Deezer artist ID (from existing Deezer lookup)
            max_albums: Maximum albums to check (rate limit protection)
        """
        result = DeezerAIResult()

        if not deezer_artist_id:
            result.error = "No Deezer artist ID available"
            return result

        # 1. Get artist's albums from the public API
        try:
            resp = self._session.get(
                f"{DEEZER_API}/artist/{deezer_artist_id}/albums",
                params={"limit": str(max_albums * 2)},
                timeout=10,
            )
            resp.raise_for_status()
            albums = resp.json().get("data", [])
        except Exception as exc:
            result.error = f"Failed to fetch albums: {exc}"
            logger.debug("Deezer AI check: album fetch failed for artist %d: %s",
                         deezer_artist_id, exc)
            return result

        if not albums:
            result.error = "No albums found"
            return result

        result.checked = True

        # 2. Check each album
        for album in albums[:max_albums]:
            album_id = album.get("id")
            album_title = album.get("title", "Unknown")
            if not album_id:
                continue

            result.albums_checked += 1
            method = self._check_album(album_id, album)
            if method:
                result.ai_tagged_albums.append(album_title)
                result.detection_method = method
                logger.info("Deezer AI tag FOUND on album '%s' (id=%d) via %s",
                            album_title, album_id, method)

            time.sleep(self.delay)

        return result

    def _check_album(self, album_id: int, album_list_data: dict) -> str:
        """Check a single album for AI indicators. Returns detection method or ''."""

        # Pass 1: Check the album list data we already have
        for field_name in _AI_API_FIELDS:
            val = album_list_data.get(field_name)
            if val and val is not False:
                return "api_field"

        # Pass 2: Fetch detailed album data from API
        try:
            resp = self._session.get(f"{DEEZER_API}/album/{album_id}", timeout=10)
            resp.raise_for_status()
            detail = resp.json()

            for field_name in _AI_API_FIELDS:
                val = detail.get(field_name)
                if val and val is not False:
                    return "api_field"

            # Check genre name for "AI" category
            genres = detail.get("genres", {}).get("data", [])
            for g in genres:
                name = (g.get("name") or "").lower()
                if "ai generated" in name or "ai-generated" in name:
                    return "api_genre"

        except Exception as exc:
            logger.debug("Deezer AI: album detail fetch failed for %d: %s", album_id, exc)

        # Pass 3: Check SSR page data and visible text (more expensive)
        try:
            page_resp = self._session.get(f"{DEEZER_WEB}/album/{album_id}", timeout=15)
            page_resp.raise_for_status()
            html = page_resp.text

            # Check SSR JSON state embedded in the page
            for pattern in _AI_SSR_PATTERNS:
                if pattern.search(html):
                    return "ssr_data"

            # Try to extract and parse __NEXT_DATA__ or similar SSR payloads
            for script_pattern in [
                r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                r'window\.__DZR_APP_STATE__\s*=\s*(\{.*?\})\s*;',
            ]:
                m = re.search(script_pattern, html, re.DOTALL)
                if m:
                    try:
                        state = json.loads(m.group(1))
                        if self._check_json_for_ai(state):
                            return "ssr_data"
                    except (json.JSONDecodeError, ValueError):
                        pass

            # Check visible text for AI labels/badges
            for pattern in _AI_TEXT_PATTERNS:
                if pattern.search(html):
                    return "page_text"

        except Exception as exc:
            logger.debug("Deezer AI: page fetch failed for album %d: %s", album_id, exc)

        return ""

    def _check_json_for_ai(self, data: dict | list, depth: int = 0) -> bool:
        """Recursively check JSON state for AI-related flags."""
        if depth > 5:
            return False

        if isinstance(data, dict):
            for key, val in data.items():
                key_lower = key.lower()
                if any(f in key_lower for f in ("ai_gen", "is_ai", "aigen", "ai_content")):
                    if val is True or val == "true" or val == 1:
                        return True
                if isinstance(val, (dict, list)):
                    if self._check_json_for_ai(val, depth + 1):
                        return True
        elif isinstance(data, list):
            for item in data[:20]:  # limit to avoid huge arrays
                if isinstance(item, (dict, list)):
                    if self._check_json_for_ai(item, depth + 1):
                        return True

        return False
