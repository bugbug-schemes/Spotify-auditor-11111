"""
MusicBrainz API client for artist enrichment.

Free, no authentication required (just a polite User-Agent).
Rate limit: 1 request per second.

Used to cross-reference Spotify artist IDs, check label/distributor info,
and validate release history.
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

MB_API = "https://musicbrainz.org/ws/2"
USER_AGENT = "spotify-audit/0.1.0 (https://github.com/spotify-audit)"


@dataclass
class MBRelease:
    title: str = ""
    date: str = ""
    release_type: str = ""       # "Album", "Single", "EP", etc.
    label: str = ""
    catalog_number: str = ""


@dataclass
class MBArtist:
    mbid: str = ""
    name: str = ""
    country: str = ""
    disambiguation: str = ""
    begin_date: str = ""
    end_date: str = ""
    artist_type: str = ""        # "Person", "Group", "Other"
    labels: list[str] = field(default_factory=list)
    releases: list[MBRelease] = field(default_factory=list)
    urls: dict[str, str] = field(default_factory=dict)  # relation type -> url


class MusicBrainzClient:
    """Query MusicBrainz for artist metadata and label/release info."""

    def __init__(self, delay: float = 1.1) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.session.headers["Accept"] = "application/json"
        self.delay = delay

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{MB_API}{path}"
        r = self.session.get(url, params={**(params or {}), "fmt": "json"}, timeout=15)
        r.raise_for_status()
        time.sleep(self.delay)
        return r.json()

    def lookup_by_spotify_url(self, spotify_artist_id: str) -> MBArtist | None:
        """Find a MusicBrainz artist from a Spotify artist ID."""
        spotify_url = f"https://open.spotify.com/artist/{spotify_artist_id}"
        try:
            data = self._get("/url", {"resource": spotify_url, "inc": "artist-rels"})
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise

        # Navigate the relations to find the artist MBID
        relations = data.get("relations", [])
        for rel in relations:
            if rel.get("type") == "streaming" and "artist" in rel:
                artist_data = rel["artist"]
                return MBArtist(
                    mbid=artist_data.get("id", ""),
                    name=artist_data.get("name", ""),
                    disambiguation=artist_data.get("disambiguation", ""),
                )
        return None

    def search_artist(self, name: str) -> MBArtist | None:
        """Search for an artist by name."""
        data = self._get("/artist", {"query": f'artist:"{name}"', "limit": "5"})
        artists = data.get("artists", [])
        if not artists:
            return None

        # Prefer exact match
        name_lower = name.lower().strip()
        best = artists[0]
        for a in artists:
            if a.get("name", "").lower().strip() == name_lower:
                best = a
                break

        return MBArtist(
            mbid=best.get("id", ""),
            name=best.get("name", ""),
            country=best.get("country", ""),
            disambiguation=best.get("disambiguation", ""),
            begin_date=best.get("life-span", {}).get("begin", ""),
            end_date=best.get("life-span", {}).get("end", ""),
            artist_type=best.get("type", ""),
        )

    def get_releases(self, mbid: str) -> list[MBRelease]:
        """Get all releases for an artist by MBID."""
        releases: list[MBRelease] = []
        offset = 0
        while True:
            data = self._get(
                f"/release",
                {"artist": mbid, "limit": "100", "offset": str(offset), "inc": "labels"},
            )
            batch = data.get("releases", [])
            if not batch:
                break
            for r in batch:
                label_info = r.get("label-info", [])
                label_name = ""
                catalog = ""
                if label_info:
                    li = label_info[0]
                    label_name = li.get("label", {}).get("name", "") if li.get("label") else ""
                    catalog = li.get("catalog-number", "")

                releases.append(MBRelease(
                    title=r.get("title", ""),
                    date=r.get("date", ""),
                    release_type=r.get("release-group", {}).get("primary-type", ""),
                    label=label_name,
                    catalog_number=catalog,
                ))
            offset += len(batch)
            if offset >= data.get("release-count", 0):
                break
        return releases

    def enrich(self, artist: MBArtist) -> MBArtist:
        """Populate releases and extract labels."""
        if not artist.mbid:
            return artist
        artist.releases = self.get_releases(artist.mbid)
        artist.labels = list({r.label for r in artist.releases if r.label})
        return artist
