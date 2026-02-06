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
        self.delay = delay
        self.enabled = bool(api_key)

    def _get(self, path: str, params: dict | None = None) -> dict:
        if not self.enabled:
            return {}
        url = f"{SETLIST_API}{path}"
        r = self.session.get(url, params=params, timeout=15)
        r.raise_for_status()
        time.sleep(self.delay)
        return r.json()

    def search_artist(self, name: str) -> SetlistArtist | None:
        """Search for an artist by name."""
        if not self.enabled:
            return None
        try:
            data = self._get("/search/artists", {"artistName": name, "p": 1, "sort": "relevance"})
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise

        artists = data.get("artist", [])
        if not artists:
            return None

        name_lower = name.lower().strip()
        for a in artists:
            if a.get("name", "").lower().strip() == name_lower:
                return SetlistArtist(
                    mbid=a.get("mbid", ""),
                    name=a.get("name", ""),
                )
        return None

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
