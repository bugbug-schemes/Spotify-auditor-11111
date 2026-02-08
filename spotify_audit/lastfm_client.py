"""
Last.fm API client for artist enrichment.

Free API, requires API key. Provides listener counts, playcounts,
bio text, similar artists, and tags — unique data not available
from other sources. The listener-to-playcount ratio is a key fraud signal.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

LASTFM_BASE = "https://ws.audioscrobbler.com/2.0/"


@dataclass
class LastfmArtist:
    name: str = ""
    mbid: str = ""
    listeners: int = 0
    playcount: int = 0
    bio: str = ""
    bio_summary: str = ""
    tags: list[str] = field(default_factory=list)
    similar_artists: list[str] = field(default_factory=list)
    url: str = ""
    image_url: str = ""
    # Top tracks with listener counts
    top_tracks: list[dict] = field(default_factory=list)


class LastfmClient:
    """Last.fm API client. Requires LASTFM_API_KEY."""

    def __init__(self, api_key: str, delay: float = 0.2):
        self.api_key = api_key
        self.delay = delay
        self.enabled = bool(api_key)
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "spotify-audit/0.3 (research tool)"

    def _get(self, method: str, **params) -> dict | None:
        """Make a Last.fm API call."""
        params.update({
            "method": method,
            "api_key": self.api_key,
            "format": "json",
        })
        try:
            resp = self._session.get(LASTFM_BASE, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                logger.debug("Last.fm error for %s: %s", method, data.get("message", ""))
                return None
            return data
        except Exception as exc:
            logger.debug("Last.fm %s failed: %s", method, exc)
            return None
        finally:
            time.sleep(self.delay)

    def get_artist_info(self, name: str) -> LastfmArtist | None:
        """Get artist info including listeners, playcount, bio, tags."""
        if not self.enabled:
            return None

        data = self._get("artist.getinfo", artist=name, autocorrect="1")
        if not data or "artist" not in data:
            return None

        a = data["artist"]
        artist = LastfmArtist(
            name=a.get("name", name),
            mbid=a.get("mbid", ""),
            url=a.get("url", ""),
        )

        # Stats
        stats = a.get("stats", {})
        artist.listeners = int(stats.get("listeners", 0) or 0)
        artist.playcount = int(stats.get("playcount", 0) or 0)

        # Bio
        bio = a.get("bio", {})
        artist.bio = bio.get("content", "")
        artist.bio_summary = bio.get("summary", "")

        # Tags
        tags = a.get("tags", {}).get("tag", [])
        if isinstance(tags, list):
            artist.tags = [t.get("name", "") for t in tags if isinstance(t, dict)]

        # Similar artists
        similar = a.get("similar", {}).get("artist", [])
        if isinstance(similar, list):
            artist.similar_artists = [
                s.get("name", "") for s in similar if isinstance(s, dict) and s.get("name")
            ]

        # Image
        images = a.get("image", [])
        if isinstance(images, list):
            for img in reversed(images):
                if isinstance(img, dict) and img.get("#text"):
                    artist.image_url = img["#text"]
                    break

        return artist

    def get_top_tracks(self, name: str, limit: int = 10) -> list[dict]:
        """Get top tracks with listener counts."""
        if not self.enabled:
            return []

        data = self._get("artist.gettoptracks", artist=name, limit=str(limit), autocorrect="1")
        if not data:
            return []

        tracks_data = data.get("toptracks", {}).get("track", [])
        if not isinstance(tracks_data, list):
            return []

        tracks = []
        for t in tracks_data:
            if not isinstance(t, dict):
                continue
            tracks.append({
                "name": t.get("name", ""),
                "listeners": int(t.get("listeners", 0) or 0),
                "playcount": int(t.get("playcount", 0) or 0),
            })
        return tracks

    def enrich(self, artist: LastfmArtist) -> LastfmArtist:
        """Enrich with top tracks data."""
        artist.top_tracks = self.get_top_tracks(artist.name)
        return artist
