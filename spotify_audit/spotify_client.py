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

    # Labels / distributors (from Deezer or MusicBrainz)
    labels: list[str] = field(default_factory=list)

    # Deezer-enriched fields
    track_titles: list[str] = field(default_factory=list)
    track_ranks: list[int] = field(default_factory=list)
    has_explicit: bool = False
    contributors: list[str] = field(default_factory=list)  # collaborator names
    contributor_roles: dict[str, list[str]] = field(default_factory=dict)  # {name: [roles]}
    related_artist_names: list[str] = field(default_factory=list)
    deezer_fans: int = 0
    deezer_isrcs: list[str] = field(default_factory=list)           # ISRCs from Deezer tracks
    deezer_isrc_registrants: list[str] = field(default_factory=list) # unique registrant codes


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


def _extract_artist_id(artist_data: dict) -> str:
    """Extract artist ID from various formats the scraper may return."""
    # Direct ID field
    aid = artist_data.get("id", "")
    if aid:
        return aid

    # Extract from URI (spotify:artist:XXXX)
    uri = artist_data.get("uri", "")
    if uri and "artist:" in uri:
        return uri.split(":")[-1]

    # Extract from external_urls or link
    for key in ("external_urls", "link", "url"):
        val = artist_data.get(key, "")
        if isinstance(val, dict):
            val = val.get("spotify", "")
        if isinstance(val, str) and "artist/" in val:
            m = re.search(r"artist/([a-zA-Z0-9]+)", val)
            if m:
                return m.group(1)

    return ""


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

        logger.debug("Raw playlist keys: %s", list(raw.keys()))
        if raw.get("tracks"):
            sample = raw["tracks"][0] if raw["tracks"] else {}
            logger.debug("First track keys: %s", list(sample.keys()) if sample else "empty")
            if sample.get("artists"):
                logger.debug("First track artist data: %s", sample["artists"][:2])

        owner = raw.get("owner", {})
        owner_name = owner if isinstance(owner, str) else owner.get("display_name", owner.get("name", ""))
        owner_id = owner if isinstance(owner, str) else owner.get("id", "")

        # SpotifyScraper uses "track_count"; fall back to "total_tracks" or len
        track_count = (
            raw.get("track_count", 0)
            or raw.get("total_tracks", 0)
            or len(raw.get("tracks", []))
        )

        meta = PlaylistMeta(
            playlist_id=pid,
            name=raw.get("name", ""),
            owner=owner_name,
            description=raw.get("description", ""),
            followers=_safe_int(raw.get("followers")),
            total_tracks=track_count,
            is_spotify_owned=(owner_id == "spotify"),
        )

        tracks: list[TrackInfo] = []
        for t in raw.get("tracks", []):
            raw_artists = t.get("artists", [])
            # Handle artists as list of dicts or list of strings
            artist_ids: list[str] = []
            artist_names: list[str] = []
            for a in raw_artists:
                if isinstance(a, dict):
                    aid = _extract_artist_id(a)
                    if aid:
                        artist_ids.append(aid)
                    name = a.get("name", "")
                    if name:
                        artist_names.append(name)
                elif isinstance(a, str):
                    # Some formats just return artist names as strings
                    artist_names.append(a)

            album = t.get("album", {})
            tracks.append(TrackInfo(
                track_id=t.get("id", ""),
                name=t.get("name", ""),
                duration_ms=_safe_int(t.get("duration_ms")),
                popularity=_safe_int(t.get("popularity")),
                album_name=album.get("name", "") if isinstance(album, dict) else str(album or ""),
                album_type=album.get("album_type", "") if isinstance(album, dict) else "",
                release_date=album.get("release_date", "") if isinstance(album, dict) else "",
                artist_ids=artist_ids,
                artist_names=artist_names,
                explicit=t.get("explicit", False) or t.get("is_explicit", False),
            ))

        # Log what we found
        all_ids = {aid for t in tracks for aid in t.artist_ids}
        all_names = {name for t in tracks for name in t.artist_names}
        logger.debug(
            "Extracted %d artist IDs and %d artist names from %d tracks",
            len(all_ids), len(all_names), len(tracks),
        )

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
            logger.debug("Artist %s raw keys: %s", artist_id, list(raw.keys()))
            return self._parse_artist(artist_id, raw)
        except (SpotifyScraperError, Exception) as exc:
            logger.warning("Scraper failed for %s (%s), falling back to oEmbed", artist_id, exc)
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

        # Discography breakdown — scraper may use different keys
        albums_list = raw.get("albums", [])
        singles_list = raw.get("singles", [])
        compilations = raw.get("compilations", [])

        # Also check for "discography" or "popular_releases" keys
        if not albums_list and not singles_list:
            popular = raw.get("popular_releases", [])
            for item in popular:
                if isinstance(item, dict):
                    rtype = item.get("type", "").lower()
                    if rtype == "album":
                        albums_list.append(item)
                    elif rtype == "single":
                        singles_list.append(item)

        release_dates: list[str] = []
        for item in albums_list + singles_list + compilations:
            if isinstance(item, dict):
                date = item.get("release_date", "") or item.get("date", "")
                if date:
                    release_dates.append(date)

        # Top tracks
        top_tracks = raw.get("top_tracks", [])
        durations = []
        popularities = []
        for t in top_tracks:
            if isinstance(t, dict):
                dur = _safe_int(t.get("duration_ms"))
                if dur:
                    durations.append(dur)
                pop = _safe_int(t.get("popularity"))
                if pop:
                    popularities.append(pop)

        # External URLs
        ext_urls = raw.get("external_urls", {})
        if isinstance(ext_urls, str):
            ext_urls = {"spotify": ext_urls}
        # Also check social links
        social = raw.get("social", {})
        if isinstance(social, dict):
            for k, v in social.items():
                if v and isinstance(v, str):
                    ext_urls[k] = v

        return ArtistInfo(
            artist_id=artist_id,
            name=raw.get("name", ""),
            genres=raw.get("genres", []),
            followers=_safe_int(raw.get("followers")),
            monthly_listeners=_safe_int(raw.get("monthly_listeners")),
            popularity=_safe_int(raw.get("popularity")),
            verified=bool(raw.get("verified", False) or raw.get("is_verified", False)),
            bio=raw.get("bio", "") or "",
            image_url=best_img.get("url") if isinstance(best_img, dict) else None,
            image_width=best_img.get("width") if isinstance(best_img, dict) else None,
            image_height=best_img.get("height") if isinstance(best_img, dict) else None,
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
