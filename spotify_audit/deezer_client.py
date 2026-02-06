"""
Deezer API client for cross-validation.

Free, no authentication required. Used to verify whether an artist exists
outside Spotify and to check fan counts, discography, label info, and more.
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEEZER_API = "https://api.deezer.com"


@dataclass
class DeezerArtist:
    deezer_id: int = 0
    name: str = ""
    nb_fan: int = 0
    nb_album: int = 0
    picture_url: str = ""
    link: str = ""

    # Populated by enrich()
    albums: list[dict] = field(default_factory=list)
    top_tracks: list[dict] = field(default_factory=list)
    related_artists: list[dict] = field(default_factory=list)

    # Extracted from albums by enrich()
    labels: list[str] = field(default_factory=list)          # unique label names
    album_types: dict[str, int] = field(default_factory=dict) # {"album": 3, "single": 12, "ep": 1}

    # Extracted from top_tracks by enrich()
    track_titles: list[str] = field(default_factory=list)
    track_durations: list[int] = field(default_factory=list)  # seconds
    track_ranks: list[int] = field(default_factory=list)
    has_explicit: bool = False
    contributors: list[str] = field(default_factory=list)     # unique collaborator names


class DeezerClient:
    """Thin wrapper around the Deezer public API."""

    def __init__(self, delay: float = 0.5) -> None:
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"
        self.delay = delay

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{DEEZER_API}{path}"
        r = self.session.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            logger.warning("Deezer API error: %s", data["error"])
        time.sleep(self.delay)
        return data

    def search_artist(self, name: str) -> DeezerArtist | None:
        """Search for an artist by name. Returns best match or None."""
        data = self._get("/search/artist", {"q": name, "limit": 5})
        results = data.get("data", [])
        if not results:
            return None

        # Exact name match first, then best match
        name_lower = name.lower().strip()
        for r in results:
            if r.get("name", "").lower().strip() == name_lower:
                return self._parse_artist(r)
        return self._parse_artist(results[0])

    def get_artist(self, deezer_id: int) -> DeezerArtist | None:
        """Fetch artist by Deezer ID."""
        data = self._get(f"/artist/{deezer_id}")
        if "error" in data:
            return None
        return self._parse_artist(data)

    def enrich(self, artist: DeezerArtist) -> DeezerArtist:
        """Add albums, top tracks, related artists, and extract structured data."""
        if artist.deezer_id == 0:
            return artist

        # --- Albums (with label info) ---
        data = self._get(f"/artist/{artist.deezer_id}/albums", {"limit": 100})
        artist.albums = data.get("data", [])
        artist.nb_album = len(artist.albums)

        # Extract labels and album type breakdown
        labels_seen: set[str] = set()
        type_counts: dict[str, int] = {}
        for album in artist.albums:
            if not isinstance(album, dict):
                continue
            label = album.get("label", "")
            if label:
                labels_seen.add(label)
            rtype = album.get("record_type", "unknown")
            type_counts[rtype] = type_counts.get(rtype, 0) + 1
        artist.labels = sorted(labels_seen)
        artist.album_types = type_counts

        # --- Top tracks (with duration, rank, contributors) ---
        data = self._get(f"/artist/{artist.deezer_id}/top", {"limit": 25})
        artist.top_tracks = data.get("data", [])

        titles: list[str] = []
        durations: list[int] = []
        ranks: list[int] = []
        contributors_seen: set[str] = set()
        has_explicit = False

        for track in artist.top_tracks:
            if not isinstance(track, dict):
                continue
            title = track.get("title", "")
            if title:
                titles.append(title)
            dur = track.get("duration", 0)
            if dur:
                durations.append(dur)
            rank = track.get("rank", 0)
            if rank:
                ranks.append(rank)
            if track.get("explicit_lyrics", False):
                has_explicit = True
            # Contributors (featured artists, producers)
            for contrib in track.get("contributors", []):
                if isinstance(contrib, dict):
                    cname = contrib.get("name", "")
                    if cname and cname.lower() != artist.name.lower():
                        contributors_seen.add(cname)

        artist.track_titles = titles
        artist.track_durations = durations
        artist.track_ranks = ranks
        artist.has_explicit = has_explicit
        artist.contributors = sorted(contributors_seen)

        # --- Related artists ---
        try:
            data = self._get(f"/artist/{artist.deezer_id}/related", {"limit": 10})
            artist.related_artists = data.get("data", [])
        except Exception as exc:
            logger.debug("Could not fetch related artists for %s: %s", artist.name, exc)

        return artist

    def _parse_artist(self, raw: dict) -> DeezerArtist:
        return DeezerArtist(
            deezer_id=raw.get("id", 0),
            name=raw.get("name", ""),
            nb_fan=raw.get("nb_fan", 0),
            nb_album=raw.get("nb_album", 0),
            picture_url=raw.get("picture_medium", raw.get("picture", "")),
            link=raw.get("link", ""),
        )
