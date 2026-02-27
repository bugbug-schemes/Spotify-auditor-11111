"""
Discogs API client for checking physical releases.

Requires a free personal access token from https://www.discogs.com/settings/developers
Used to check whether an artist has physical releases (vinyl, CD, cassette) —
ghost/AI artists almost never have physical pressings.
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field

import requests

from spotify_audit.name_matching import (
    pick_best_match, MatchResult, log_match,
)

logger = logging.getLogger(__name__)

DISCOGS_API = "https://api.discogs.com"


@dataclass
class DiscogsArtist:
    discogs_id: int = 0
    name: str = ""
    url: str = ""
    # Populated by enrich()
    physical_releases: int = 0         # vinyl, CD, cassette releases
    digital_only_releases: int = 0     # digital-only releases
    total_releases: int = 0
    formats: list[str] = field(default_factory=list)  # unique formats found
    labels: list[str] = field(default_factory=list)    # labels they've released on
    # Populated by enrich_profile()
    profile: str = ""                  # bio text from Discogs
    realname: str = ""                 # real name (for solo artists)
    social_urls: list[str] = field(default_factory=list)  # external URLs (social, website)
    members: list[str] = field(default_factory=list)      # group members (if group)
    groups: list[str] = field(default_factory=list)        # groups this artist belongs to
    name_variations: list[str] = field(default_factory=list)
    data_quality: str = ""             # "Correct", "Needs Vote", etc.
    # Match quality metadata (from name_matching)
    match_confidence: float = 0.0
    match_method: str = ""


class DiscogsClient:
    """Thin wrapper around the Discogs API for physical release checks."""

    def __init__(self, token: str = "", delay: float = 1.0) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "spotify-audit/0.1.0"
        self.session.headers["Accept"] = "application/json"
        if token:
            self.session.headers["Authorization"] = f"Discogs token={token}"
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10, pool_maxsize=10,
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.delay = delay
        self.enabled = True  # Discogs works without a token (lower rate limit)

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self.session.close()

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{DISCOGS_API}{path}"
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                r = self.session.get(url, params=params, timeout=15)
            except requests.RequestException as exc:
                last_exc = exc
                wait = 2 ** (attempt + 1)
                logger.debug("Discogs request failed (attempt %d): %s", attempt + 1, exc)
                time.sleep(wait)
                continue
            if r.status_code in (401, 403):
                logger.warning("Discogs API key invalid (HTTP %d) — disabling client", r.status_code)
                self.enabled = False
                return {}
            if r.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.debug("Discogs 429 rate-limited, backing off %ds", wait)
                time.sleep(wait)
                continue
            if r.status_code in (500, 502, 503):
                wait = 2 ** (attempt + 1)
                logger.debug("Discogs %d server error, backing off %ds", r.status_code, wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            time.sleep(self.delay)
            try:
                return r.json()
            except (ValueError, requests.exceptions.JSONDecodeError):
                logger.warning("Discogs returned non-JSON for %s", path)
                return {}
        if last_exc:
            raise last_exc
        r.raise_for_status()
        return {}

    def search_artist(self, name: str, discogs_id: str | None = None) -> DiscogsArtist | None:
        """Search for an artist by name using shared name matching.

        Args:
            name: Artist name to search for.
            discogs_id: Optional Discogs artist ID from MusicBrainz URL bridging.
        """
        # Platform ID bridging: skip search if we have a direct ID
        if discogs_id:
            try:
                data = self._get(f"/artists/{discogs_id}")
                if data.get("name"):
                    mr = MatchResult(
                        found=True, confidence=1.0,
                        matched_name=data["name"],
                        platform_id=str(discogs_id),
                        match_method="platform_id",
                    )
                    log_match("Discogs", name, mr)
                    return DiscogsArtist(
                        discogs_id=int(discogs_id),
                        name=data["name"],
                        url=data.get("resource_url", ""),
                        match_confidence=mr.confidence,
                        match_method=mr.match_method,
                    )
            except Exception as exc:
                logger.debug("Discogs ID lookup failed for %s: %s", discogs_id, exc)

        data = self._get("/database/search", {"q": name, "type": "artist", "per_page": 5})
        results = data.get("results", [])
        if not results:
            log_match("Discogs", name, MatchResult(found=False))
            return None

        candidates = [{
            "name": r.get("title", ""),
            "id": r.get("id", 0),
            "url": r.get("resource_url", ""),
        } for r in results]

        match = pick_best_match(name, candidates)
        log_match("Discogs", name, match)

        if match.found and match.platform_id:
            best = next(
                (c for c in candidates if str(c["id"]) == match.platform_id),
                candidates[0],
            )
            return DiscogsArtist(
                discogs_id=best["id"],
                name=best["name"],
                url=best.get("url", ""),
                match_confidence=match.confidence,
                match_method=match.match_method,
            )

        # Fallback: return first result (Discogs sorts by relevance)
        first = candidates[0]
        return DiscogsArtist(
            discogs_id=first["id"],
            name=first["name"],
            url=first.get("url", ""),
            match_confidence=0.5,
            match_method="fallback",
        )

    def enrich_profile(self, artist: DiscogsArtist) -> DiscogsArtist:
        """Fetch artist profile: bio, real name, social URLs, members."""
        if artist.discogs_id == 0:
            return artist

        data = self._get(f"/artists/{artist.discogs_id}")

        artist.profile = (data.get("profile", "") or "")[:1000]
        artist.realname = data.get("realname", "") or ""
        artist.data_quality = data.get("data_quality", "") or ""

        # External URLs (social media, official website, etc.)
        urls = data.get("urls", [])
        if isinstance(urls, list):
            artist.social_urls = [u for u in urls if isinstance(u, str)]

        # Members (for groups)
        members = data.get("members", [])
        if isinstance(members, list):
            artist.members = [
                m.get("name", "") for m in members
                if isinstance(m, dict) and m.get("name")
            ]

        # Groups this artist belongs to
        groups = data.get("groups", [])
        if isinstance(groups, list):
            artist.groups = [
                g.get("name", "") for g in groups
                if isinstance(g, dict) and g.get("name")
            ]

        # Name variations
        namevariations = data.get("namevariations", [])
        if isinstance(namevariations, list):
            artist.name_variations = [n for n in namevariations if isinstance(n, str)]

        return artist

    def enrich(self, artist: DiscogsArtist) -> DiscogsArtist:
        """Check for physical releases and fetch profile data."""
        if artist.discogs_id == 0:
            return artist

        # Fetch profile (bio, social URLs, members) first
        try:
            self.enrich_profile(artist)
        except Exception as exc:
            logger.debug("Discogs profile fetch failed for %s: %s", artist.name, exc)

        data = self._get(f"/artists/{artist.discogs_id}/releases", {
            "per_page": 100,
            "sort": "year",
            "sort_order": "desc",
        })
        releases = data.get("releases", [])
        artist.total_releases = len(releases)

        physical_formats = {"Vinyl", "CD", "Cassette", "LP", "12\"", "7\"", "10\"", "Box Set"}
        formats_seen: set[str] = set()
        labels_seen: set[str] = set()

        for rel in releases:
            fmt = rel.get("format", "")
            label = rel.get("label", "")
            if label:
                labels_seen.add(label)

            # Check if any physical format
            if isinstance(fmt, str):
                formats_seen.add(fmt)
                if any(pf.lower() in fmt.lower() for pf in physical_formats):
                    artist.physical_releases += 1
                else:
                    artist.digital_only_releases += 1

        artist.formats = sorted(formats_seen)
        artist.labels = sorted(labels_seen)
        return artist
