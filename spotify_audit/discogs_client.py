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


class DiscogsClient:
    """Thin wrapper around the Discogs API for physical release checks."""

    def __init__(self, token: str = "", delay: float = 1.0) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "spotify-audit/0.1.0"
        self.session.headers["Accept"] = "application/json"
        if token:
            self.session.headers["Authorization"] = f"Discogs token={token}"
        self.delay = delay
        self.enabled = True  # Discogs works without a token (lower rate limit)

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{DISCOGS_API}{path}"
        r = self.session.get(url, params=params, timeout=15)
        r.raise_for_status()
        time.sleep(self.delay)
        return r.json()

    def search_artist(self, name: str) -> DiscogsArtist | None:
        """Search for an artist by name."""
        data = self._get("/database/search", {"q": name, "type": "artist", "per_page": 5})
        results = data.get("results", [])
        if not results:
            return None

        name_lower = name.lower().strip()
        for r in results:
            if r.get("title", "").lower().strip() == name_lower:
                return DiscogsArtist(
                    discogs_id=r.get("id", 0),
                    name=r.get("title", ""),
                    url=r.get("resource_url", ""),
                )
        # Fall back to first result
        first = results[0]
        return DiscogsArtist(
            discogs_id=first.get("id", 0),
            name=first.get("title", ""),
            url=first.get("resource_url", ""),
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
