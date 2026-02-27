"""
Deezer API client for cross-validation.

Free, no authentication required. Used to verify whether an artist exists
outside Spotify and to check fan counts, discography, label info, and more.
"""

from __future__ import annotations

import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Any

import requests

from spotify_audit.name_matching import (
    pick_best_match, MatchResult, log_match,
)

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

    # Additional fields
    radio: bool = False                                        # has Deezer radio
    related_artist_fans: list[tuple[str, int]] = field(default_factory=list)  # [(name, nb_fan)]
    album_release_dates: list[str] = field(default_factory=list)  # release dates per album
    contributor_roles: dict[str, list[str]] = field(default_factory=dict)  # {name: [roles]}
    # ISRC data (Priority 7)
    track_isrcs: list[str] = field(default_factory=list)       # ISRCs from tracks
    isrc_registrants: list[str] = field(default_factory=list)  # unique registrant codes


class DeezerQuotaError(Exception):
    """Raised when Deezer returns a quota limit exceeded error."""
    pass


class DeezerClient:
    """Thin wrapper around the Deezer public API."""

    # Deezer allows ~50 requests per 5 seconds. Use a shared lock
    # so concurrent threads don't collectively exceed the limit.
    _rate_lock = threading.Lock()
    _request_times: list[float] = []
    _MAX_REQUESTS_PER_WINDOW = 40  # conservative (limit is 50)
    _WINDOW_SECONDS = 5.0

    def __init__(self, delay: float = 0.5) -> None:
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10, pool_maxsize=10,
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.delay = delay
        self._max_retries = 4

    def _wait_for_rate_limit(self) -> None:
        """Block until we're under the per-window request limit."""
        sleep_time = 0.0
        with DeezerClient._rate_lock:
            now = time.time()
            cutoff = now - DeezerClient._WINDOW_SECONDS
            # Prune old timestamps
            DeezerClient._request_times = [
                t for t in DeezerClient._request_times if t > cutoff
            ]
            if len(DeezerClient._request_times) >= DeezerClient._MAX_REQUESTS_PER_WINDOW:
                # Calculate sleep needed, but release lock before sleeping
                sleep_time = DeezerClient._request_times[0] - cutoff + 0.1
        # Sleep outside the lock so other threads aren't blocked
        if sleep_time > 0:
            logger.debug("Deezer rate limiter: sleeping %.1fs", sleep_time)
            time.sleep(sleep_time)
        # Re-acquire lock to record the request timestamp
        with DeezerClient._rate_lock:
            DeezerClient._request_times.append(time.time())

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{DEEZER_API}{path}"
        last_exc = None

        for attempt in range(self._max_retries + 1):
            self._wait_for_rate_limit()
            r = self.session.get(url, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()

            if "error" in data:
                error = data["error"]
                # Quota limit exceeded — retry with exponential backoff
                if error.get("code") == 4:
                    wait = min(2 ** attempt, 16)
                    logger.warning(
                        "Deezer quota exceeded (attempt %d/%d), retrying in %ds...",
                        attempt + 1, self._max_retries + 1, wait,
                    )
                    last_exc = DeezerQuotaError(str(error))
                    time.sleep(wait)
                    continue
                # Other API errors — log and return (non-retryable)
                logger.warning("Deezer API error: %s", error)

            time.sleep(self.delay)
            return data

        # All retries exhausted
        logger.error("Deezer quota limit exceeded after %d retries for %s", self._max_retries + 1, path)
        raise last_exc or DeezerQuotaError("Quota limit exceeded")

    def search_artist(self, name: str) -> DeezerArtist | None:
        """Search for an artist by name using shared name matching."""
        try:
            data = self._get("/search/artist", {"q": name, "limit": 5})
        except DeezerQuotaError:
            logger.warning("Deezer quota hit searching for '%s', skipping", name)
            return None
        results = data.get("data", [])
        if not results:
            log_match("Deezer", name, MatchResult(found=False))
            return None

        candidates = [{
            "name": r.get("name", ""),
            "id": r.get("id", 0),
            "nb_fan": r.get("nb_fan", 0),
        } for r in results]

        match = pick_best_match(name, candidates)
        log_match("Deezer", name, match)

        if match.found and match.platform_id:
            best_raw = next(
                (r for r in results if str(r.get("id", 0)) == match.platform_id),
                results[0],
            )
            return self._parse_artist(best_raw)

        # Fallback: return first result (Deezer sorts by relevance)
        return self._parse_artist(results[0])

    def get_artist(self, deezer_id: int) -> DeezerArtist | None:
        """Fetch artist by Deezer ID."""
        data = self._get(f"/artist/{deezer_id}")
        if "error" in data:
            return None
        return self._parse_artist(data)

    def enrich(self, artist: DeezerArtist) -> DeezerArtist:
        """Add albums, top tracks, related artists, and extract structured data.

        Fetches albums, top tracks, related artists, and full artist data
        concurrently to reduce total enrichment time.
        """
        if artist.deezer_id == 0:
            return artist

        from concurrent.futures import ThreadPoolExecutor, as_completed

        # Results containers for concurrent fetches
        full_data_result: dict = {}
        albums_data: list = []
        tracks_data: list = []
        related_data: list = []
        quota_error = False

        def _fetch_full() -> None:
            nonlocal full_data_result
            try:
                full_data_result = self._get(f"/artist/{artist.deezer_id}")
            except Exception as exc:
                logger.debug("Could not fetch full artist data for %s: %s", artist.name, exc)

        def _fetch_albums() -> None:
            nonlocal albums_data, quota_error
            try:
                data = self._get(f"/artist/{artist.deezer_id}/albums", {"limit": 100})
                albums_data = data.get("data", [])
            except DeezerQuotaError:
                logger.warning("Deezer quota hit fetching albums for %s", artist.name)
                quota_error = True

        def _fetch_tracks() -> None:
            nonlocal tracks_data, quota_error
            try:
                data = self._get(f"/artist/{artist.deezer_id}/top", {"limit": 25})
                tracks_data = data.get("data", [])
            except DeezerQuotaError:
                logger.warning("Deezer quota hit fetching top tracks for %s", artist.name)
                quota_error = True

        def _fetch_related() -> None:
            nonlocal related_data
            try:
                data = self._get(f"/artist/{artist.deezer_id}/related", {"limit": 10})
                related_data = data.get("data", [])
            except Exception as exc:
                logger.debug("Could not fetch related artists for %s: %s", artist.name, exc)

        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="dz-enrich") as pool:
            futures = [
                pool.submit(_fetch_full),
                pool.submit(_fetch_albums),
                pool.submit(_fetch_tracks),
                pool.submit(_fetch_related),
            ]
            for fut in as_completed(futures):
                fut.result()

        if quota_error:
            logger.warning("Returning partial data for %s due to Deezer quota", artist.name)
            return artist

        # Apply full artist data
        if full_data_result:
            artist.radio = bool(full_data_result.get("radio", False))

        # Process albums
        artist.albums = albums_data
        artist.nb_album = len(artist.albums)
        labels_seen: set[str] = set()
        type_counts: dict[str, int] = {}
        release_dates: list[str] = []
        for album in artist.albums:
            if not isinstance(album, dict):
                continue
            label = album.get("label", "")
            if label:
                labels_seen.add(label)
            rtype = album.get("record_type", "unknown")
            type_counts[rtype] = type_counts.get(rtype, 0) + 1
            rdate = album.get("release_date", "")
            if rdate:
                release_dates.append(rdate)
        artist.labels = sorted(labels_seen)
        artist.album_types = type_counts
        artist.album_release_dates = release_dates

        # Process top tracks
        artist.top_tracks = tracks_data
        titles: list[str] = []
        durations: list[int] = []
        ranks: list[int] = []
        contributors_seen: set[str] = set()
        contributor_roles: dict[str, list[str]] = {}
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
            for contrib in track.get("contributors", []):
                if isinstance(contrib, dict):
                    cname = contrib.get("name", "")
                    crole = contrib.get("role", "")
                    if cname and cname.lower() != artist.name.lower():
                        contributors_seen.add(cname)
                        if cname not in contributor_roles:
                            contributor_roles[cname] = []
                        if crole and crole not in contributor_roles[cname]:
                            contributor_roles[cname].append(crole)

        artist.track_titles = titles
        artist.track_durations = durations
        artist.track_ranks = ranks
        artist.has_explicit = has_explicit
        artist.contributors = sorted(contributors_seen)
        artist.contributor_roles = contributor_roles

        # ISRCs from top tracks (Priority 7)
        isrcs: list[str] = []
        for track in artist.top_tracks:
            if isinstance(track, dict):
                isrc = track.get("isrc", "")
                if isrc:
                    isrcs.append(isrc)
                elif track.get("id") and len(isrcs) < 10:
                    try:
                        track_data = self._get(f"/track/{track['id']}")
                        track_isrc = track_data.get("isrc", "")
                        if track_isrc:
                            isrcs.append(track_isrc)
                    except Exception:
                        pass
        artist.track_isrcs = isrcs
        if isrcs:
            registrants: set[str] = set()
            for isrc in isrcs:
                clean = isrc.replace("-", "")
                if len(clean) >= 5:
                    registrants.add(clean[2:5])
            artist.isrc_registrants = sorted(registrants)

        # Process related artists
        artist.related_artists = related_data
        artist.related_artist_fans = [
            (r.get("name", ""), r.get("nb_fan", 0))
            for r in artist.related_artists
            if isinstance(r, dict) and r.get("name")
        ]

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
