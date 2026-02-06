"""
Spotify API client wrapper.

Handles authentication, playlist fetching, artist detail enrichment,
and rate-limit retries with exponential backoff.
"""

from __future__ import annotations

import re
import time
import logging
from dataclasses import dataclass, field
from typing import Any

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

from spotify_audit.config import AuditConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes for clean internal representation
# ---------------------------------------------------------------------------

@dataclass
class TrackInfo:
    track_id: str
    name: str
    duration_ms: int
    popularity: int
    album_name: str
    album_type: str          # "album", "single", "compilation"
    release_date: str        # ISO date string
    artist_ids: list[str]
    explicit: bool = False


@dataclass
class ArtistInfo:
    artist_id: str
    name: str
    genres: list[str] = field(default_factory=list)
    followers: int = 0
    popularity: int = 0
    image_url: str | None = None
    image_width: int | None = None
    image_height: int | None = None
    external_urls: dict[str, str] = field(default_factory=dict)

    # Enriched from albums / top-tracks
    album_count: int = 0
    single_count: int = 0
    total_tracks: int = 0
    release_dates: list[str] = field(default_factory=list)
    track_durations: list[int] = field(default_factory=list)
    top_track_popularities: list[int] = field(default_factory=list)


@dataclass
class PlaylistMeta:
    playlist_id: str
    name: str
    owner: str
    description: str
    followers: int
    total_tracks: int
    is_spotify_owned: bool


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class SpotifyClient:
    """Thin wrapper around spotipy with retries and data normalization."""

    def __init__(self, config: AuditConfig) -> None:
        self.config = config
        auth_manager = SpotifyClientCredentials(
            client_id=config.spotify_client_id,
            client_secret=config.spotify_client_secret,
        )
        self.sp = spotipy.Spotify(auth_manager=auth_manager)

    # -- helpers -------------------------------------------------------------

    def _retry(self, fn, *args, **kwargs) -> Any:
        """Call *fn* with exponential backoff on rate-limit / transient errors."""
        for attempt in range(self.config.max_retries):
            try:
                return fn(*args, **kwargs)
            except spotipy.SpotifyException as exc:
                if exc.http_status == 429:
                    retry_after = int(exc.headers.get("Retry-After", 1))
                    wait = max(retry_after, self.config.backoff_base ** attempt)
                    logger.warning("Rate limited. Waiting %.1fs (attempt %d)", wait, attempt + 1)
                    time.sleep(wait)
                elif exc.http_status >= 500:
                    wait = self.config.backoff_base ** attempt
                    logger.warning("Server error %d. Retrying in %.1fs", exc.http_status, wait)
                    time.sleep(wait)
                else:
                    raise
            except Exception:
                if attempt == self.config.max_retries - 1:
                    raise
                wait = self.config.backoff_base ** attempt
                logger.warning("Transient error. Retrying in %.1fs", wait)
                time.sleep(wait)
        raise RuntimeError(f"Failed after {self.config.max_retries} retries")

    @staticmethod
    def extract_playlist_id(url_or_id: str) -> str:
        """Accept a Spotify playlist URL or raw ID."""
        # Full URL: https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=...
        m = re.search(r"playlist/([a-zA-Z0-9]+)", url_or_id)
        if m:
            return m.group(1)
        # Spotify URI: spotify:playlist:37i9dQZF1DXcBWIGoYBM5M
        m = re.search(r"spotify:playlist:([a-zA-Z0-9]+)", url_or_id)
        if m:
            return m.group(1)
        # Assume raw ID
        return url_or_id.strip()

    # -- playlist -----------------------------------------------------------

    def get_playlist(self, url_or_id: str) -> tuple[PlaylistMeta, list[TrackInfo]]:
        """Fetch playlist metadata and all tracks (handles pagination)."""
        pid = self.extract_playlist_id(url_or_id)
        raw = self._retry(self.sp.playlist, pid)

        owner = raw.get("owner", {})
        meta = PlaylistMeta(
            playlist_id=pid,
            name=raw.get("name", ""),
            owner=owner.get("display_name", owner.get("id", "")),
            description=raw.get("description", ""),
            followers=raw.get("followers", {}).get("total", 0),
            total_tracks=raw.get("tracks", {}).get("total", 0),
            is_spotify_owned=owner.get("id", "") == "spotify",
        )

        tracks = self._collect_playlist_tracks(raw["tracks"])
        return meta, tracks

    def _collect_playlist_tracks(self, page: dict) -> list[TrackInfo]:
        tracks: list[TrackInfo] = []
        while page:
            for item in page.get("items", []):
                t = item.get("track")
                if not t or t.get("id") is None:
                    continue  # local files / unavailable tracks
                tracks.append(TrackInfo(
                    track_id=t["id"],
                    name=t.get("name", ""),
                    duration_ms=t.get("duration_ms", 0),
                    popularity=t.get("popularity", 0),
                    album_name=t.get("album", {}).get("name", ""),
                    album_type=t.get("album", {}).get("album_type", ""),
                    release_date=t.get("album", {}).get("release_date", ""),
                    artist_ids=[a["id"] for a in t.get("artists", []) if a.get("id")],
                    explicit=t.get("explicit", False),
                ))
            page = self._retry(self.sp.next, page) if page.get("next") else None
        return tracks

    # -- artists ------------------------------------------------------------

    def get_artists(self, artist_ids: list[str]) -> dict[str, ArtistInfo]:
        """Batch-fetch artist info (Spotify allows up to 50 per call)."""
        result: dict[str, ArtistInfo] = {}
        for i in range(0, len(artist_ids), 50):
            batch = artist_ids[i : i + 50]
            raw = self._retry(self.sp.artists, batch)
            for a in raw.get("artists", []):
                if a is None:
                    continue
                images = a.get("images", [])
                best = images[0] if images else {}
                result[a["id"]] = ArtistInfo(
                    artist_id=a["id"],
                    name=a.get("name", ""),
                    genres=a.get("genres", []),
                    followers=a.get("followers", {}).get("total", 0),
                    popularity=a.get("popularity", 0),
                    image_url=best.get("url"),
                    image_width=best.get("width"),
                    image_height=best.get("height"),
                    external_urls=a.get("external_urls", {}),
                )
        return result

    def enrich_artist(self, artist: ArtistInfo) -> ArtistInfo:
        """Add album/track-level signals needed for Quick scan."""
        # Albums (paginate up to 50 at a time)
        albums = self._collect_albums(artist.artist_id)
        artist.album_count = sum(1 for a in albums if a["album_type"] == "album")
        artist.single_count = sum(1 for a in albums if a["album_type"] == "single")
        artist.total_tracks = sum(a.get("total_tracks", 0) for a in albums)
        artist.release_dates = [a["release_date"] for a in albums if a.get("release_date")]

        # Top tracks for duration / popularity spread
        top = self._retry(self.sp.artist_top_tracks, artist.artist_id)
        for t in top.get("tracks", []):
            artist.track_durations.append(t.get("duration_ms", 0))
            artist.top_track_popularities.append(t.get("popularity", 0))

        return artist

    def _collect_albums(self, artist_id: str) -> list[dict]:
        albums: list[dict] = []
        page = self._retry(
            self.sp.artist_albums,
            artist_id,
            album_type="album,single",
            limit=50,
        )
        while page:
            albums.extend(page.get("items", []))
            page = self._retry(self.sp.next, page) if page.get("next") else None
        return albums
