"""
Setlist.fm API client for live show history.

Requires a free API key from https://api.setlist.fm/docs/1.0/ui/index.html
Used to check whether an artist has performed live — ghost/AI artists
have zero concert history.
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass

import requests

from spotify_audit.name_matching import (
    pick_best_match, MatchResult, log_match,
)

logger = logging.getLogger(__name__)

SETLIST_API = "https://api.setlist.fm/rest/1.0"


@dataclass
class SetlistArtist:
    mbid: str = ""                     # MusicBrainz ID (setlist.fm uses these)
    name: str = ""
    total_setlists: int = 0            # total recorded live performances
    first_show_date: str = ""          # earliest known show
    last_show_date: str = ""           # most recent show
    top_venues: list[str] = None       # notable venues
    venue_cities: list[str] = None     # cities where they've played
    venue_countries: list[str] = None  # countries where they've played
    tour_names: list[str] = None       # named tours
    # Match quality metadata (from name_matching)
    match_confidence: float = 0.0
    match_method: str = ""

    def __post_init__(self):
        if self.top_venues is None:
            self.top_venues = []
        if self.venue_cities is None:
            self.venue_cities = []
        if self.venue_countries is None:
            self.venue_countries = []
        if self.tour_names is None:
            self.tour_names = []


class SetlistFmClient:
    """Thin wrapper around the setlist.fm API for concert history."""

    def __init__(self, api_key: str = "", delay: float = 0.5) -> None:
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"
        if api_key:
            self.session.headers["x-api-key"] = api_key
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10, pool_maxsize=10,
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.delay = delay
        self.enabled = bool(api_key)

    def _get(self, path: str, params: dict | None = None) -> dict:
        if not self.enabled:
            return {}
        url = f"{SETLIST_API}{path}"
        for attempt in range(3):
            r = self.session.get(url, params=params, timeout=15)
            if r.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.debug("Setlist.fm 429 rate-limited, backing off %ds", wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            time.sleep(self.delay)
            return r.json()
        r.raise_for_status()
        return {}

    def search_artist(self, name: str, setlistfm_url: str | None = None) -> SetlistArtist | None:
        """Search for an artist by name using shared name matching.

        Args:
            name: Artist name to search for.
            setlistfm_url: Optional setlist.fm URL from MusicBrainz URL bridging.
        """
        if not self.enabled:
            return None

        # Platform ID bridging: extract MBID from setlist.fm URL
        if setlistfm_url:
            import re
            # URLs look like: https://www.setlist.fm/setlists/artist-name-mbid.html
            m = re.search(r"-([0-9a-f]{8})\.html", setlistfm_url)
            if m:
                mbid = m.group(1)
                # Convert short hex to full MBID format used by setlist.fm
                mr = MatchResult(
                    found=True, confidence=1.0,
                    matched_name=name,
                    platform_id=mbid,
                    match_method="platform_id",
                )
                log_match("Setlist.fm", name, mr)
                return SetlistArtist(
                    mbid=mbid, name=name,
                    match_confidence=mr.confidence, match_method=mr.match_method,
                )

        try:
            data = self._get("/search/artists", {"artistName": name, "p": 1, "sort": "relevance"})
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                log_match("Setlist.fm", name, MatchResult(found=False))
                return None
            raise

        artists = data.get("artist", [])
        if not artists:
            log_match("Setlist.fm", name, MatchResult(found=False))
            return None

        candidates = [{
            "name": a.get("name", ""),
            "id": a.get("mbid", ""),
            "country": a.get("country", {}).get("code", "") if isinstance(a.get("country"), dict) else "",
        } for a in artists]

        match = pick_best_match(name, candidates)
        log_match("Setlist.fm", name, match)

        if match.found and match.platform_id:
            return SetlistArtist(
                mbid=match.platform_id,
                name=match.matched_name or name,
                match_confidence=match.confidence,
                match_method=match.match_method,
            )

        # Fallback: return first result
        first = artists[0]
        return SetlistArtist(
            mbid=first.get("mbid", ""),
            name=first.get("name", ""),
            match_confidence=0.5,
            match_method="fallback",
        )

    def get_setlist_count(self, artist: SetlistArtist) -> SetlistArtist:
        """Get total setlist count and date range for an artist."""
        if not self.enabled or not artist.mbid:
            return artist

        try:
            data = self._get(f"/artist/{artist.mbid}/setlists", {"p": 1})
        except requests.HTTPError:
            return artist

        artist.total_setlists = data.get("total", 0)

        setlists = data.get("setlist", [])
        if setlists:
            # Most recent first
            artist.last_show_date = setlists[0].get("eventDate", "")
            # Get venue names, cities, countries, and tour names
            venues = []
            cities = []
            countries = []
            tours = []
            for s in setlists[:20]:
                venue = s.get("venue", {})
                vname = venue.get("name", "")
                if vname and vname not in venues:
                    venues.append(vname)
                city_obj = venue.get("city", {})
                if isinstance(city_obj, dict):
                    city_name = city_obj.get("name", "")
                    if city_name and city_name not in cities:
                        cities.append(city_name)
                    country_obj = city_obj.get("country", {})
                    if isinstance(country_obj, dict):
                        country_name = country_obj.get("name", "")
                        if country_name and country_name not in countries:
                            countries.append(country_name)
                tour_name = s.get("tour", {}).get("name", "") if isinstance(s.get("tour"), dict) else ""
                if tour_name and tour_name not in tours:
                    tours.append(tour_name)
            artist.top_venues = venues
            artist.venue_cities = cities
            artist.venue_countries = countries
            artist.tour_names = tours

        # Get oldest show (last page)
        total_pages = (artist.total_setlists + 19) // 20  # 20 per page
        if total_pages > 1:
            try:
                last_page = self._get(f"/artist/{artist.mbid}/setlists", {"p": total_pages})
                last_setlists = last_page.get("setlist", [])
                if last_setlists:
                    artist.first_show_date = last_setlists[-1].get("eventDate", "")
            except requests.HTTPError:
                pass

        return artist
