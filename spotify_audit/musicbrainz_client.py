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

from spotify_audit.name_matching import (
    normalize_name, similarity_score, min_confidence_for_length,
    pick_best_match, MatchResult, log_match,
)

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
    # Expanded metadata
    genres: list[str] = field(default_factory=list)       # community genres/tags
    aliases: list[str] = field(default_factory=list)      # alternate names
    isnis: list[str] = field(default_factory=list)        # International Standard Name Identifiers
    ipis: list[str] = field(default_factory=list)         # Interested Parties Information codes
    area: str = ""                                        # origin area (more specific than country)
    gender: str = ""                                      # for Person type
    # ISRC data (from recording-level lookups)
    isrcs: list[str] = field(default_factory=list)        # ISRCs from recordings
    isrc_registrants: list[str] = field(default_factory=list)  # unique registrant codes
    # Enhanced URL categorization
    youtube_url: str = ""                                 # direct YouTube channel URL
    bandcamp_url: str = ""                                # Bandcamp page URL
    official_website: str = ""                            # official homepage
    social_urls: dict[str, str] = field(default_factory=dict)  # platform -> URL


class MusicBrainzClient:
    """Query MusicBrainz for artist metadata and label/release info."""

    def __init__(self, delay: float = 1.1) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.session.headers["Accept"] = "application/json"
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10, pool_maxsize=10,
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
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
        """Search for an artist by name using shared name matching."""
        data = self._get("/artist", {"query": f'artist:"{name}"', "limit": "5"})
        artists = data.get("artists", [])
        if not artists:
            log_match("MusicBrainz", name, MatchResult(found=False))
            return None

        # Build candidate dicts for the matcher
        candidates = []
        for a in artists:
            aliases = [
                al["name"] for al in a.get("aliases", [])
                if isinstance(al, dict) and al.get("name")
            ]
            genres = [
                t["name"] for t in a.get("tags", [])
                if isinstance(t, dict) and t.get("name")
            ]
            candidates.append({
                "name": a.get("name", ""),
                "id": a.get("id", ""),
                "aliases": aliases,
                "genres": genres,
                "country": a.get("country", ""),
                "_raw": a,
            })

        match = pick_best_match(name, candidates)
        log_match("MusicBrainz", name, match)

        if match.found and match.platform_id:
            # Find the original raw data for the matched candidate
            best = next(
                (c["_raw"] for c in candidates if c["id"] == match.platform_id),
                artists[0],
            )
        else:
            # Fallback to first result (MusicBrainz returns relevance-sorted)
            best = artists[0]

        # Extract aliases from search result
        aliases = []
        for alias in best.get("aliases", []):
            if isinstance(alias, dict) and alias.get("name"):
                aliases.append(alias["name"])

        # Extract genres/tags from search result
        genres = []
        for tag in best.get("tags", []):
            if isinstance(tag, dict) and tag.get("name"):
                genres.append(tag["name"])

        # Extract ISNIs and IPIs
        isnis = best.get("isnis", []) or []
        ipis = best.get("ipis", []) or []

        return MBArtist(
            mbid=best.get("id", ""),
            name=best.get("name", ""),
            country=best.get("country", ""),
            disambiguation=best.get("disambiguation", ""),
            begin_date=best.get("life-span", {}).get("begin", ""),
            end_date=best.get("life-span", {}).get("end", ""),
            artist_type=best.get("type", ""),
            gender=best.get("gender", "") or "",
            area=best.get("area", {}).get("name", "") if isinstance(best.get("area"), dict) else "",
            aliases=aliases,
            genres=genres,
            isnis=isnis if isinstance(isnis, list) else [],
            ipis=ipis if isinstance(ipis, list) else [],
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

    def get_url_relations(self, mbid: str) -> dict[str, str]:
        """Fetch URL relations for an artist (social media, official site, etc.)."""
        try:
            data = self._get(f"/artist/{mbid}", {"inc": "url-rels"})
        except requests.HTTPError:
            return {}

        urls: dict[str, str] = {}
        for rel in data.get("relations", []):
            if rel.get("type") and rel.get("url", {}).get("resource"):
                rel_type = rel["type"]
                url = rel["url"]["resource"]
                urls[rel_type] = url
        return urls

    def categorize_urls(self, urls: dict[str, str]) -> dict[str, str]:
        """Categorize URL relations into typed buckets (Priority 5).

        Returns dict with keys: youtube, bandcamp, soundcloud, official_website,
        and social media platform names.
        """
        categorized: dict[str, str] = {}

        for rel_type, url in urls.items():
            url_lower = url.lower()
            rel_lower = rel_type.lower()

            if "youtube" in url_lower or "youtube" in rel_lower:
                categorized["youtube"] = url
            elif "bandcamp" in url_lower or "bandcamp" in rel_lower:
                categorized["bandcamp"] = url
            elif "soundcloud" in url_lower or "soundcloud" in rel_lower:
                categorized["soundcloud"] = url
            elif "instagram" in url_lower:
                categorized["instagram"] = url
            elif "twitter.com" in url_lower or "x.com" in url_lower:
                categorized["twitter"] = url
            elif "facebook" in url_lower:
                categorized["facebook"] = url
            elif "wikipedia" in url_lower:
                categorized["wikipedia"] = url
            elif "wikidata" in url_lower:
                categorized["wikidata"] = url
            elif "allmusic" in url_lower:
                categorized["allmusic"] = url
            elif rel_lower == "official homepage":
                categorized["official_website"] = url

        return categorized

    def get_recording_isrcs(self, mbid: str, limit: int = 25) -> list[str]:
        """Fetch ISRCs from an artist's recordings (Priority 7).

        Args:
            mbid: Artist MusicBrainz ID
            limit: Max recordings to check (ISRC lookups are rate-limited)

        Returns:
            List of ISRC strings (e.g., 'USRC11700001')
        """
        try:
            data = self._get(
                "/recording",
                {"artist": mbid, "limit": str(limit), "inc": "isrcs"},
            )
        except (requests.HTTPError, Exception) as exc:
            logger.debug("MusicBrainz ISRC lookup failed for %s: %s", mbid, exc)
            return []

        isrcs: list[str] = []
        for rec in data.get("recordings", []):
            rec_isrcs = rec.get("isrcs", [])
            if isinstance(rec_isrcs, list):
                isrcs.extend(rec_isrcs)
        return isrcs

    @staticmethod
    def parse_isrc_registrants(isrcs: list[str]) -> list[str]:
        """Extract unique registrant codes from ISRCs.

        ISRC format: CC-XXX-YY-NNNNN
        - CC = country code (2 chars)
        - XXX = registrant code (3 chars)
        - YY = year (2 chars)
        - NNNNN = designation (5 chars)
        """
        registrants: set[str] = set()
        for isrc in isrcs:
            # Remove dashes if present
            clean = isrc.replace("-", "")
            if len(clean) >= 5:
                registrant = clean[2:5]
                registrants.add(registrant)
        return sorted(registrants)

    def get_genres(self, mbid: str) -> list[str]:
        """Fetch genres/tags for an artist."""
        try:
            data = self._get(f"/artist/{mbid}", {"inc": "genres"})
        except requests.HTTPError:
            return []

        genres = []
        for g in data.get("genres", []):
            if isinstance(g, dict) and g.get("name"):
                genres.append(g["name"])
        return genres

    def enrich(self, artist: MBArtist) -> MBArtist:
        """Populate releases, labels, URL relations, genres, ISRCs, and categorized URLs."""
        if not artist.mbid:
            return artist

        # Releases and labels
        artist.releases = self.get_releases(artist.mbid)
        artist.labels = list({r.label for r in artist.releases if r.label})

        # URL relations (social media, official site, Wikipedia, etc.)
        try:
            artist.urls = self.get_url_relations(artist.mbid)
        except Exception as exc:
            logger.debug("MusicBrainz URL relations failed for %s: %s", artist.mbid, exc)

        # Categorize URLs for downstream consumers (Priority 5)
        if artist.urls:
            categorized = self.categorize_urls(artist.urls)
            artist.youtube_url = categorized.get("youtube", "")
            artist.bandcamp_url = categorized.get("bandcamp", "")
            artist.official_website = categorized.get("official_website", "")
            # Collect social URLs
            for platform in ("instagram", "twitter", "facebook", "soundcloud"):
                if platform in categorized:
                    artist.social_urls[platform] = categorized[platform]

        # Genres (if not already populated from search)
        if not artist.genres:
            try:
                artist.genres = self.get_genres(artist.mbid)
            except Exception as exc:
                logger.debug("MusicBrainz genres failed for %s: %s", artist.mbid, exc)

        # ISRCs from recordings (Priority 7)
        try:
            artist.isrcs = self.get_recording_isrcs(artist.mbid)
            if artist.isrcs:
                artist.isrc_registrants = self.parse_isrc_registrants(artist.isrcs)
        except Exception as exc:
            logger.debug("MusicBrainz ISRC lookup failed for %s: %s", artist.mbid, exc)

        return artist
