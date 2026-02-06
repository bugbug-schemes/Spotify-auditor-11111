"""
Bandsintown API client for live show / touring history.

Free read-only access with an app ID.
Register at https://www.artists.bandsintown.com/support/api-installation
Used as a secondary live show signal alongside setlist.fm.
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

BANDSINTOWN_API = "https://rest.bandsintown.com"


@dataclass
class BandsintownArtist:
    name: str = ""
    url: str = ""
    image_url: str = ""
    tracker_count: int = 0             # fans tracking this artist
    upcoming_events: int = 0           # scheduled future shows
    past_events: int = 0               # historical shows
    on_tour: bool = False
    # Social links
    facebook_page_url: str = ""
    mbid: str = ""                     # MusicBrainz ID
    social_links: list[dict] = field(default_factory=list)  # [{type, url}]


class BandsintownClient:
    """Thin wrapper around the Bandsintown API for touring data."""

    def __init__(self, app_id: str = "", delay: float = 0.3) -> None:
        self.app_id = app_id
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"
        self.delay = delay
        self.enabled = bool(app_id)

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        if not self.enabled:
            return {}
        url = f"{BANDSINTOWN_API}{path}"
        params = {**(params or {}), "app_id": self.app_id}
        r = self.session.get(url, params=params, timeout=15)
        r.raise_for_status()
        time.sleep(self.delay)
        return r.json()

    def get_artist(self, name: str) -> BandsintownArtist | None:
        """Look up an artist by name."""
        if not self.enabled:
            return None
        try:
            data = self._get(f"/artists/{requests.utils.quote(name)}")
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise

        if not isinstance(data, dict) or "error" in data:
            return None

        # Parse social links
        links = data.get("links", [])
        social_links = []
        if isinstance(links, list):
            for link in links:
                if isinstance(link, dict) and link.get("url"):
                    social_links.append({
                        "type": link.get("type", ""),
                        "url": link.get("url", ""),
                    })

        return BandsintownArtist(
            name=data.get("name", ""),
            url=data.get("url", ""),
            image_url=data.get("image_url", ""),
            tracker_count=data.get("tracker_count", 0),
            upcoming_events=data.get("upcoming_event_count", 0),
            on_tour=data.get("on_tour", False),
            facebook_page_url=data.get("facebook_page_url", "") or "",
            mbid=data.get("mbid", "") or "",
            social_links=social_links,
        )

    def get_past_events_count(self, name: str) -> int:
        """Get count of past events for an artist."""
        if not self.enabled:
            return 0
        try:
            data = self._get(
                f"/artists/{requests.utils.quote(name)}/events",
                {"date": "past"},
            )
            if isinstance(data, list):
                return len(data)
        except requests.HTTPError:
            pass
        return 0

    def enrich(self, artist: BandsintownArtist) -> BandsintownArtist:
        """Add past event count."""
        if not self.enabled:
            return artist
        artist.past_events = self.get_past_events_count(artist.name)
        return artist
