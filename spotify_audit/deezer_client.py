"""
Deezer API client for cross-validation.

Free, no authentication required. Used to verify whether an artist exists
outside Spotify and to check fan counts, discography, and label info.
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
        """Add albums and top tracks."""
        if artist.deezer_id == 0:
            return artist

        # Albums
        data = self._get(f"/artist/{artist.deezer_id}/albums", {"limit": 100})
        artist.albums = data.get("data", [])
        artist.nb_album = len(artist.albums)

        # Top tracks
        data = self._get(f"/artist/{artist.deezer_id}/top", {"limit": 25})
        artist.top_tracks = data.get("data", [])

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
