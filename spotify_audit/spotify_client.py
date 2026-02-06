"""
Spotify data client — no official API key required.

Uses the SpotifyScraper library to extract data from Spotify's public
embed endpoints. Falls back to oEmbed for minimal data when scraping fails.

Data classes (TrackInfo, ArtistInfo, PlaylistMeta) are the canonical internal
representations consumed by analyzers and scoring.
"""

from __future__ import annotations

import re
import time
import logging
from dataclasses import dataclass, field
from typing import Any

import requests
from spotify_scraper import SpotifyClient as _ScraperClient
from spotify_scraper.core.exceptions import SpotifyScraperError

from spotify_audit.config import AuditConfig

logger = logging.getLogger(__name__)

SPOTIFY_BASE = "https://open.spotify.com"
OEMBED_URL = "https://open.spotify.com/oembed"

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
    artist_ids: list[str] = field(default_factory=list)
    artist_names: list[str] = field(default_factory=list)
    explicit: bool = False


@dataclass
class ArtistInfo:
    artist_id: str
    name: str
    genres: list[str] = field(default_factory=list)
    followers: int = 0
    monthly_listeners: int = 0
    popularity: int = 0
    verified: bool = False
    bio: str = ""
    image_url: str | None = None
    image_width: int | None = None
    image_height: int | None = None
    external_urls: dict[str, str] = field(default_factory=dict)

    # Discography
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
# URL helpers
# ---------------------------------------------------------------------------

def extract_id(url_or_id: str, resource: str = "playlist") -> str:
    """Extract a Spotify resource ID from a URL, URI, or raw ID."""
    m = re.search(rf"{resource}/([a-zA-Z0-9]+)", url_or_id)
    if m:
        return m.group(1)
    m = re.search(rf"spotify:{resource}:([a-zA-Z0-9]+)", url_or_id)
    if m:
        return m.group(1)
    return url_or_id.strip()


def _artist_url(artist_id: str) -> str:
    return f"{SPOTIFY_BASE}/artist/{artist_id}"


def _playlist_url(playlist_id: str) -> str:
    return f"{SPOTIFY_BASE}/playlist/{playlist_id}"


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------

def _retry(fn, *args, max_retries: int = 5, backoff_base: float = 2.0, **kwargs) -> Any:
    """Call *fn* with exponential backoff on transient errors."""
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if attempt == max_retries - 1:
                raise
            wait = backoff_base ** attempt
            logger.warning(
                "Attempt %d failed (%s). Retrying in %.1fs",
                attempt + 1, type(exc).__name__, wait,
            )
            time.sleep(wait)
    raise RuntimeError(f"Failed after {max_retries} retries")


# ---------------------------------------------------------------------------
# oEmbed fallback (always works, minimal data)
# ---------------------------------------------------------------------------

def _oembed_artist(artist_id: str) -> dict:
    """Fetch minimal artist info via oEmbed (name + thumbnail)."""
    url = f"{SPOTIFY_BASE}/artist/{artist_id}"
    r = requests.get(OEMBED_URL, params={"url": url}, timeout=10)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class SpotifyClient:
    """Scrape Spotify embed endpoints for playlist/artist data.
    No API key or authentication required."""

    def __init__(self, config: AuditConfig) -> None:
        self.config = config
        self._scraper = _ScraperClient()

    def close(self) -> None:
        try:
            self._scraper.close()
        except Exception:
            pass

    # -- playlist -----------------------------------------------------------

    def get_playlist(self, url_or_id: str) -> tuple[PlaylistMeta, list[TrackInfo]]:
        """Fetch playlist metadata and all tracks."""
        pid = extract_id(url_or_id, "playlist")
        raw = _retry(
            self._scraper.get_playlist_info,
            _playlist_url(pid),
            max_retries=self.config.max_retries,
            backoff_base=self.config.backoff_base,
        )

        owner = raw.get("owner", {})
        owner_name = owner if isinstance(owner, str) else owner.get("display_name", owner.get("name", ""))
        owner_id = owner if isinstance(owner, str) else owner.get("id", "")

        meta = PlaylistMeta(
            playlist_id=pid,
            name=raw.get("name", ""),
            owner=owner_name,
            description=raw.get("description", ""),
            followers=raw.get("followers", {}).get("total", 0) if isinstance(raw.get("followers"), dict) else int(raw.get("followers", 0) or 0),
            total_tracks=raw.get("total_tracks", 0) or len(raw.get("tracks", [])),
            is_spotify_owned=(owner_id == "spotify"),
        )

        tracks: list[TrackInfo] = []
        for t in raw.get("tracks", []):
            artists = t.get("artists", [])
            tracks.append(TrackInfo(
                track_id=t.get("id", ""),
                name=t.get("name", ""),
                duration_ms=t.get("duration_ms", 0),
                popularity=t.get("popularity", 0),
                album_name=t.get("album", {}).get("name", "") if isinstance(t.get("album"), dict) else str(t.get("album", "")),
                album_type=t.get("album", {}).get("album_type", "") if isinstance(t.get("album"), dict) else "",
                release_date=t.get("album", {}).get("release_date", "") if isinstance(t.get("album"), dict) else "",
                artist_ids=[a.get("id", "") for a in artists if isinstance(a, dict)],
                artist_names=[a.get("name", "") for a in artists if isinstance(a, dict)],
                explicit=t.get("explicit", False),
            ))
        return meta, tracks

    # -- artists ------------------------------------------------------------

    def get_artist_info(self, artist_id: str) -> ArtistInfo:
        """Fetch full artist info via embed scraping, with oEmbed fallback."""
        url = _artist_url(artist_id)
        try:
            raw = _retry(
                self._scraper.get_artist_info,
                url,
                max_retries=self.config.max_retries,
                backoff_base=self.config.backoff_base,
            )
            return self._parse_artist(artist_id, raw)
        except SpotifyScraperError:
            logger.warning("Scraper failed for %s, falling back to oEmbed", artist_id)
            return self._oembed_fallback(artist_id)

    def get_artists(self, artist_ids: list[str]) -> dict[str, ArtistInfo]:
        """Fetch info for multiple artists (sequential with delay)."""
        result: dict[str, ArtistInfo] = {}
        for i, aid in enumerate(artist_ids):
            result[aid] = self.get_artist_info(aid)
            # Polite delay between requests
            if i < len(artist_ids) - 1:
                time.sleep(self.config.scrape_delay)
        return result

    def _parse_artist(self, artist_id: str, raw: dict) -> ArtistInfo:
        """Normalize SpotifyScraper output into ArtistInfo."""
        images = raw.get("images", [])
        best_img = images[0] if images else {}
        if isinstance(best_img, str):
            best_img = {"url": best_img}

        # Discography breakdown
        albums_list = raw.get("albums", [])
        singles_list = raw.get("singles", [])
        compilations = raw.get("compilations", [])

        release_dates: list[str] = []
        for item in albums_list + singles_list + compilations:
            if isinstance(item, dict) and item.get("release_date"):
                release_dates.append(item["release_date"])

        # Top tracks
        top_tracks = raw.get("top_tracks", [])
        durations = []
        popularities = []
        for t in top_tracks:
            if isinstance(t, dict):
                if t.get("duration_ms"):
                    durations.append(t["duration_ms"])
                if t.get("popularity"):
                    popularities.append(t["popularity"])

        # External URLs
        ext_urls = raw.get("external_urls", {})
        if isinstance(ext_urls, str):
            ext_urls = {"spotify": ext_urls}

        return ArtistInfo(
            artist_id=artist_id,
            name=raw.get("name", ""),
            genres=raw.get("genres", []),
            followers=_safe_int(raw.get("followers")),
            monthly_listeners=_safe_int(raw.get("monthly_listeners")),
            popularity=_safe_int(raw.get("popularity")),
            verified=bool(raw.get("verified", False)),
            bio=raw.get("bio", "") or "",
            image_url=best_img.get("url"),
            image_width=best_img.get("width"),
            image_height=best_img.get("height"),
            external_urls=ext_urls if isinstance(ext_urls, dict) else {},
            album_count=len(albums_list),
            single_count=len(singles_list),
            total_tracks=sum(
                _safe_int(item.get("total_tracks", 0))
                for item in albums_list + singles_list + compilations
                if isinstance(item, dict)
            ),
            release_dates=release_dates,
            track_durations=durations,
            top_track_popularities=popularities,
        )

    def _oembed_fallback(self, artist_id: str) -> ArtistInfo:
        """Minimal artist info from oEmbed when full scraping fails."""
        try:
            raw = _retry(
                _oembed_artist,
                artist_id,
                max_retries=self.config.max_retries,
                backoff_base=self.config.backoff_base,
            )
            return ArtistInfo(
                artist_id=artist_id,
                name=raw.get("title", "Unknown"),
                image_url=raw.get("thumbnail_url"),
                image_width=raw.get("thumbnail_width"),
                image_height=raw.get("thumbnail_height"),
                external_urls={"spotify": f"{SPOTIFY_BASE}/artist/{artist_id}"},
            )
        except Exception:
            logger.error("oEmbed also failed for %s", artist_id)
            return ArtistInfo(artist_id=artist_id, name="Unknown")


def _safe_int(val: Any) -> int:
    """Coerce a value to int, handling dicts like {'total': N}, strings, None."""
    if val is None:
        return 0
    if isinstance(val, dict):
        return int(val.get("total", 0) or 0)
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0
