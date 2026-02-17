"""
Deezer AI content tag detection (Priority 2).

Deezer is the only streaming platform that actively tags AI-generated content.
Their public API doesn't expose the tag, so we scrape the Deezer web player
album pages looking for AI content indicators.

Only runs for artists with existing red flags (conditional enrichment).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEEZER_API = "https://api.deezer.com"
DEEZER_WEB = "https://www.deezer.com"


@dataclass
class DeezerAIResult:
    """Result of Deezer AI tag check for an artist."""
    checked: bool = False           # Did we actually check?
    ai_tagged_albums: list[str] = field(default_factory=list)  # Album titles flagged as AI
    albums_checked: int = 0
    error: str = ""


class DeezerAIChecker:
    """Check Deezer album pages for AI-generated content tags."""

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
        """Check an artist's album pages for AI content tags.

        Args:
            deezer_artist_id: Deezer artist ID (from existing Deezer lookup)
            max_albums: Maximum album pages to scrape (rate limit protection)
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

        # 2. Check each album page for AI tags
        for album in albums[:max_albums]:
            album_id = album.get("id")
            album_title = album.get("title", "Unknown")
            if not album_id:
                continue

            time.sleep(self.delay)
            is_ai = self._check_album_page(album_id)
            result.albums_checked += 1

            if is_ai:
                result.ai_tagged_albums.append(album_title)
                logger.info("Deezer AI tag FOUND on album '%s' (id=%d)", album_title, album_id)

        return result

    def _check_album_page(self, album_id: int) -> bool:
        """Scrape a Deezer album page and check for AI content indicators."""
        url = f"{DEEZER_WEB}/album/{album_id}"
        try:
            resp = self._session.get(url, timeout=15)
            resp.raise_for_status()
            html = resp.text
        except Exception as exc:
            logger.debug("Deezer AI check: page fetch failed for album %d: %s", album_id, exc)
            return False

        # Check for AI content indicators in the HTML
        # Deezer shows a visible badge/popup for AI-generated content
        ai_patterns = [
            r"AI[\s-]generated",
            r"artificially[\s-]generated",
            r"AI[\s-]generated[\s-]content",
            r"generated[\s-]by[\s-]AI",
            r"contenu\s+généré\s+par\s+IA",   # French version
            r"artificial[\s-]intelligence[\s-]generated",
        ]

        html_lower = html.lower()
        for pattern in ai_patterns:
            if re.search(pattern, html_lower):
                return True

        # Also check structured data / meta tags
        try:
            soup = BeautifulSoup(html, "html.parser")

            # Check for data attributes
            for tag in soup.find_all(attrs={"data-ai": True}):
                return True
            for tag in soup.find_all(attrs={"data-ai-generated": True}):
                return True

            # Check meta tags
            for meta in soup.find_all("meta"):
                content = (meta.get("content") or "").lower()
                if "ai-generated" in content or "ai generated" in content:
                    return True

            # Check for AI badge/label elements
            for cls_pattern in ["ai-badge", "ai-label", "ai-tag", "ai-content"]:
                if soup.find(class_=re.compile(cls_pattern, re.I)):
                    return True

        except Exception:
            pass

        return False
