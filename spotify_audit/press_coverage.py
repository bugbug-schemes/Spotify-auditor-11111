"""
Press coverage search for artist legitimacy verification (Priority 6).

Searches for press coverage in recognized music publications.
Best implemented as part of Claude Deep Scan, but provides structured
queries for programmatic use when available.

Only runs for artists with existing red flags (conditional enrichment).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Recognized music publications for targeted searches
MUSIC_PUBLICATIONS = [
    "pitchfork.com",
    "stereogum.com",
    "consequenceofsound.net",
    "nme.com",
    "rollingstone.com",
    "spin.com",
    "billboard.com",
    "thequietus.com",
    "brooklynvegan.com",
    "tinymixtapes.com",
    "residentadvisor.net",
    "allmusic.com",
]


@dataclass
class PressCoverageResult:
    """Result of a press coverage search."""
    checked: bool = False
    publications_found: list[str] = field(default_factory=list)
    total_hits: int = 0
    search_queries_used: list[str] = field(default_factory=list)
    error: str = ""


def build_search_queries(artist_name: str) -> list[str]:
    """Build targeted search queries for press coverage.

    Returns queries that can be used with any web search capability.
    """
    quoted = f'"{artist_name}"'

    # Publication-specific site: search
    site_list = " OR ".join(f"site:{pub}" for pub in MUSIC_PUBLICATIONS[:6])

    return [
        f'{quoted} review {site_list}',
        f'{quoted} album review',
        f'{quoted} interview music',
        f'{quoted} concert review',
    ]


def build_claude_prompt(artist_name: str) -> str:
    """Build a Claude prompt for deep-scan press coverage analysis."""
    return (
        f'Search for press coverage of the artist "{artist_name}". '
        "Look for: album reviews, interviews, concert reviews, feature articles "
        "in music publications. "
        "Exclude: the artist's own website, streaming platform pages, "
        "auto-generated aggregator pages. "
        "Report: which publications covered them, what they wrote about, "
        "and whether the coverage appears genuine."
    )


def analyze_press_results(
    results: list[dict],
    artist_name: str,
) -> PressCoverageResult:
    """Analyze web search results for genuine press coverage.

    Args:
        results: List of search result dicts with 'title', 'url', 'snippet' keys.
        artist_name: Artist name for filtering.

    Returns:
        PressCoverageResult with publications found.
    """
    result = PressCoverageResult(checked=True)
    name_lower = artist_name.lower()
    seen_domains: set[str] = set()

    for r in results:
        url = r.get("url", "").lower()
        title = r.get("title", "").lower()
        snippet = r.get("snippet", "").lower()

        # Skip if artist name not mentioned
        if name_lower not in title and name_lower not in snippet:
            continue

        # Check if from a recognized publication
        for pub in MUSIC_PUBLICATIONS:
            if pub in url and pub not in seen_domains:
                result.publications_found.append(pub)
                seen_domains.add(pub)
                result.total_hits += 1
                break
        else:
            # Count non-publication hits too
            if name_lower in title:
                result.total_hits += 1

    return result
