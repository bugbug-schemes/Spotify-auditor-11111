"""
Wikipedia API client for artist enrichment.

Uses the MediaWiki Action API (free, no key required) and the Wikimedia
REST API for page views.  Provides much richer data than the binary
"has Wikipedia link" check currently derived from MusicBrainz URL relations:

- Article existence (independent of MusicBrainz having the link)
- Article length and summary extract
- Monthly page views (strong engagement signal)
- Categories (genre / era classification)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import requests

from spotify_audit.name_matching import (
    similarity_score, normalize_name, MatchResult, log_match,
)

logger = logging.getLogger(__name__)

MEDIAWIKI_API = "https://en.wikipedia.org/w/api.php"
WIKIMEDIA_REST = "https://wikimedia.org/api/rest_v1"


@dataclass
class WikipediaArticle:
    title: str = ""
    page_id: int = 0
    length: int = 0                     # article byte length
    extract: str = ""                   # plain-text intro extract
    description: str = ""               # Wikidata short description
    categories: list[str] = field(default_factory=list)
    monthly_views: int = 0              # average monthly page views (last 60 days)
    url: str = ""
    # Match quality metadata (from name_matching)
    match_confidence: float = 0.0
    match_method: str = ""


class WikipediaClient:
    """MediaWiki API client.  No API key required."""

    def __init__(self, delay: float = 0.2) -> None:
        self.delay = delay
        self.enabled = True              # always available (no key needed)
        self._session = requests.Session()
        self._session.headers["User-Agent"] = (
            "spotify-audit/0.7 (https://github.com/spotify-audit; research tool)"
        )
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10, pool_maxsize=10,
        )
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    # ------------------------------------------------------------------

    def _mw_get(self, **params) -> dict | None:
        """Query the MediaWiki Action API."""
        params.setdefault("format", "json")
        params.setdefault("formatversion", "2")
        try:
            resp = self._session.get(MEDIAWIKI_API, params=params, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.debug("Wikipedia API error: %s", exc)
            return None
        finally:
            time.sleep(self.delay)

    # ------------------------------------------------------------------

    def search_artist(self, name: str, wikipedia_title: str | None = None) -> WikipediaArticle | None:
        """Search Wikipedia for an artist page by name.

        Uses a two-pass approach:
        1. Direct page lookup (exact title match).
        2. Full-text search fallback with disambiguation filtering.

        Args:
            name: Artist name to search for.
            wikipedia_title: Optional Wikipedia page title from MusicBrainz URL.
        """
        if not self.enabled:
            return None

        # Platform ID bridging: direct lookup by known title
        if wikipedia_title:
            import urllib.parse
            decoded = urllib.parse.unquote(wikipedia_title).replace("_", " ")
            article = self._direct_lookup(decoded)
            if article:
                article.match_confidence = 1.0
                article.match_method = "platform_id"
                log_match("Wikipedia", name, MatchResult(
                    found=True, confidence=1.0,
                    matched_name=article.title,
                    platform_id=str(article.page_id),
                    match_method="platform_id",
                ))
                return article

        # Pass 1: direct page lookup — fastest, handles exact matches
        article = self._direct_lookup(name)
        if article:
            conf = similarity_score(name, article.title)
            article.match_confidence = conf
            article.match_method = "exact"
            log_match("Wikipedia", name, MatchResult(
                found=True,
                confidence=conf,
                matched_name=article.title,
                platform_id=str(article.page_id),
                match_method="exact",
            ))
            return article

        # Pass 2: search API
        article = self._search_lookup(name)
        if article:
            conf = similarity_score(name, article.title)
            article.match_confidence = conf
            article.match_method = "fuzzy"
            log_match("Wikipedia", name, MatchResult(
                found=True,
                confidence=conf,
                matched_name=article.title,
                platform_id=str(article.page_id),
                match_method="fuzzy",
            ))
        else:
            log_match("Wikipedia", name, MatchResult(found=False))
        return article

    def _direct_lookup(self, title: str) -> WikipediaArticle | None:
        """Try to load a page by exact title."""
        data = self._mw_get(
            action="query",
            titles=title,
            prop="extracts|info|categories|description",
            exintro=True,
            explaintext=True,
            exsentences="5",
            inprop="length",
            cllimit="20",
            clshow="!hidden",
            redirects="1",
        )
        if not data:
            return None

        pages = data.get("query", {}).get("pages", [])
        if not pages:
            return None

        page = pages[0]
        if page.get("missing"):
            return None

        return self._parse_page(page)

    def _search_lookup(self, name: str) -> WikipediaArticle | None:
        """Full-text search with musician/band disambiguation filtering."""
        # Search with music-related context
        for query in [f"{name} musician", f"{name} band", name]:
            data = self._mw_get(
                action="query",
                list="search",
                srsearch=query,
                srlimit="5",
            )
            if not data:
                continue

            results = data.get("query", {}).get("search", [])
            if not results:
                continue

            name_lower = name.lower().strip()
            for r in results:
                r_title = r.get("title", "")
                r_lower = r_title.lower().strip()

                # Skip disambiguation pages
                snippet = r.get("snippet", "").lower()
                if "disambiguation" in r_lower or "disambiguation" in snippet:
                    continue

                # Accept exact match or match with qualifier
                if r_lower == name_lower or r_lower.startswith(f"{name_lower} ("):
                    # Fetch full page data
                    return self._direct_lookup(r_title)

            # If first result is close enough and music-related
            if results and query != name:
                snippet = results[0].get("snippet", "").lower()
                music_terms = ["musician", "singer", "band", "rapper", "artist",
                               "composer", "album", "record", "songwriter", "DJ"]
                if any(term in snippet for term in music_terms):
                    return self._direct_lookup(results[0]["title"])

        return None

    def _parse_page(self, page: dict) -> WikipediaArticle:
        """Parse a MediaWiki page response into WikipediaArticle."""
        categories = []
        for cat in page.get("categories", []):
            cat_title = cat.get("title", "")
            # Strip "Category:" prefix
            if cat_title.startswith("Category:"):
                cat_title = cat_title[9:]
            if cat_title:
                categories.append(cat_title)

        title = page.get("title", "")
        return WikipediaArticle(
            title=title,
            page_id=page.get("pageid", 0),
            length=page.get("length", 0),
            extract=page.get("extract", ""),
            description=page.get("description", ""),
            categories=categories,
            url=f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
        )

    # ------------------------------------------------------------------

    def enrich(self, article: WikipediaArticle) -> WikipediaArticle:
        """Add page view data to an existing article."""
        if not article.title:
            return article

        article.monthly_views = self._get_page_views(article.title)
        return article

    def _get_page_views(self, title: str) -> int:
        """Fetch average monthly page views over the last 60 days."""
        # Wikimedia REST API: pageviews per article
        safe_title = title.replace(" ", "_")
        url = (
            f"{WIKIMEDIA_REST}/metrics/pageviews/per-article"
            f"/en.wikipedia/all-access/all-agents/{safe_title}/monthly"
        )

        # Last 2 months
        from datetime import datetime, timedelta
        end = datetime.now()
        start = end - timedelta(days=60)

        try:
            resp = self._session.get(
                url,
                params={
                    "start": start.strftime("%Y%m%d"),
                    "end": end.strftime("%Y%m%d"),
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.debug("Wikipedia page views failed for '%s': %s", title, exc)
            return 0
        finally:
            time.sleep(self.delay)

        items = data.get("items", [])
        if not items:
            return 0

        total = sum(i.get("views", 0) for i in items)
        months = len(items) or 1
        return total // months
