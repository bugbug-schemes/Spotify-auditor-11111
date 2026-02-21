"""
Songkick API client for concert and touring history.

Requires a free API key from https://www.songkick.com/api_key_requests/new
Complements Setlist.fm with different data coverage — Songkick tracks
upcoming events, past gigography, venue details, and on-tour status.

Env var: SONGKICK_API_KEY
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import requests

from spotify_audit.name_matching import (
    pick_best_match, MatchResult, log_match,
)

logger = logging.getLogger(__name__)

SONGKICK_API = "https://api.songkick.com/api/3.0"


@dataclass
class SongkickArtist:
    songkick_id: int = 0
    name: str = ""
    on_tour: bool = False               # currently touring
    on_tour_until: str = ""             # end date of current tour
    total_past_events: int = 0          # gigography count
    total_upcoming_events: int = 0
    first_event_date: str = ""          # earliest recorded event
    last_event_date: str = ""           # most recent event
    venue_names: list[str] = field(default_factory=list)
    venue_cities: list[str] = field(default_factory=list)
    venue_countries: list[str] = field(default_factory=list)
    event_types: list[str] = field(default_factory=list)   # Concert, Festival
    uri: str = ""                       # Songkick artist page URL


class SongkickClient:
    """Songkick API client.  Requires SONGKICK_API_KEY."""

    def __init__(self, api_key: str = "", delay: float = 0.5) -> None:
        self.api_key = api_key
        self.delay = delay
        self.enabled = bool(api_key)
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "spotify-audit/0.7 (research tool)"
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10, pool_maxsize=10,
        )
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def _get(self, path: str, params: dict | None = None) -> dict | None:
        """Make a Songkick API call."""
        if not self.enabled:
            return None
        params = dict(params or {})
        params["apikey"] = self.api_key
        url = f"{SONGKICK_API}{path}"
        try:
            resp = self._session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.debug("Songkick API error for %s: %s", path, exc)
            return None
        finally:
            time.sleep(self.delay)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search_artist(self, name: str, songkick_id: str | None = None) -> SongkickArtist | None:
        """Search for an artist by name using shared name matching.

        Args:
            name: Artist name to search for.
            songkick_id: Optional Songkick artist ID from MusicBrainz URL bridging.
        """
        if not self.enabled:
            return None

        # Platform ID bridging
        if songkick_id:
            data = self._get(f"/artists/{songkick_id}.json")
            if data:
                artist_data = data.get("resultsPage", {}).get("results", {}).get("artist", {})
                if artist_data:
                    log_match("Songkick", name, MatchResult(
                        found=True, confidence=1.0,
                        matched_name=artist_data.get("displayName", ""),
                        platform_id=str(songkick_id),
                        match_method="platform_id",
                    ))
                    return self._parse_artist(artist_data)

        data = self._get("/search/artists.json", {"query": name})
        if not data:
            log_match("Songkick", name, MatchResult(found=False))
            return None

        results_page = data.get("resultsPage", {})
        results = results_page.get("results", {})
        artists = results.get("artist", [])

        if not artists:
            log_match("Songkick", name, MatchResult(found=False))
            return None

        candidates = [{
            "name": a.get("displayName", ""),
            "id": a.get("id", 0),
        } for a in artists]

        match = pick_best_match(name, candidates)
        log_match("Songkick", name, match)

        if match.found and match.platform_id:
            best_raw = next(
                (a for a in artists if str(a.get("id", 0)) == match.platform_id),
                artists[0],
            )
            return self._parse_artist(best_raw)

        return None

    def _parse_artist(self, raw: dict) -> SongkickArtist:
        """Parse a search result into SongkickArtist."""
        on_tour_until = raw.get("onTourUntil", "") or ""
        return SongkickArtist(
            songkick_id=raw.get("id", 0),
            name=raw.get("displayName", ""),
            on_tour=bool(on_tour_until),
            on_tour_until=on_tour_until,
            uri=raw.get("uri", ""),
        )

    # ------------------------------------------------------------------
    # Enrichment
    # ------------------------------------------------------------------

    def enrich(self, artist: SongkickArtist) -> SongkickArtist:
        """Fetch gigography (past events) to populate event history."""
        if not artist.songkick_id:
            return artist

        # Past events (gigography) — first page
        data = self._get(
            f"/artists/{artist.songkick_id}/gigography.json",
            {"per_page": "50"},
        )
        if data:
            rp = data.get("resultsPage", {})
            artist.total_past_events = rp.get("totalEntries", 0)
            events = rp.get("results", {}).get("event", [])
            self._extract_event_details(artist, events)

            # If many events, also fetch last page for earliest date
            total_pages = (artist.total_past_events + 49) // 50
            if total_pages > 1:
                last_data = self._get(
                    f"/artists/{artist.songkick_id}/gigography.json",
                    {"per_page": "50", "page": str(total_pages)},
                )
                if last_data:
                    last_events = (
                        last_data.get("resultsPage", {})
                        .get("results", {})
                        .get("event", [])
                    )
                    if last_events:
                        start_date = last_events[-1].get("start", {})
                        artist.first_event_date = start_date.get("date", "") if isinstance(start_date, dict) else ""

        # Upcoming events count
        upcoming = self._get(
            f"/artists/{artist.songkick_id}/calendar.json",
            {"per_page": "1"},
        )
        if upcoming:
            artist.total_upcoming_events = (
                upcoming.get("resultsPage", {}).get("totalEntries", 0)
            )

        return artist

    def _extract_event_details(
        self, artist: SongkickArtist, events: list[dict],
    ) -> None:
        """Extract venue/city/country details from event list."""
        venues: list[str] = []
        cities: list[str] = []
        countries: list[str] = []
        event_types: list[str] = []

        for ev in events[:30]:  # sample first 30 for variety
            # Venue
            venue = ev.get("venue", {})
            if isinstance(venue, dict):
                vname = venue.get("displayName", "")
                if vname and vname not in venues:
                    venues.append(vname)

            # Location
            location = ev.get("location", {})
            if isinstance(location, dict):
                city = location.get("city", "")
                if city:
                    # Songkick format: "City, Country" or "City, State, Country"
                    parts = [p.strip() for p in city.split(",")]
                    city_name = parts[0]
                    country_name = parts[-1] if len(parts) > 1 else ""
                    if city_name and city_name not in cities:
                        cities.append(city_name)
                    if country_name and country_name not in countries:
                        countries.append(country_name)

            # Event type
            etype = ev.get("type", "")
            if etype and etype not in event_types:
                event_types.append(etype)

            # Date tracking
            start = ev.get("start", {})
            date = start.get("date", "") if isinstance(start, dict) else ""
            if date:
                # Events come most-recent-first in gigography
                if not artist.last_event_date:
                    artist.last_event_date = date
                artist.first_event_date = date  # keeps getting overwritten

        artist.venue_names = venues
        artist.venue_cities = cities
        artist.venue_countries = countries
        artist.event_types = event_types
