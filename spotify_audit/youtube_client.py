"""
YouTube Data API v3 client for cross-referencing artist presence (Priority 4).

Free tier: 10,000 units/day. Search = 100 units, channel/video detail = 1 unit.
That allows ~49 full artist lookups per day on the free tier.

Only runs for artists with existing red flags (conditional enrichment).
Env var: YOUTUBE_API_KEY
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import requests

logger = logging.getLogger(__name__)

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"


@dataclass
class YouTubeArtistData:
    """YouTube presence data for an artist."""
    channel_found: bool = False
    channel_id: str = ""
    channel_name: str = ""
    subscriber_count: int = 0
    video_count: int = 0
    view_count: int = 0
    channel_description: str = ""
    # Music video search
    music_videos_found: int = 0
    # Matching quality
    match_confidence: float = 0.0    # 0-1, how well channel name matches artist name


class YouTubeClient:
    """YouTube Data API v3 client.  Requires YOUTUBE_API_KEY."""

    def __init__(self, api_key: str = "", delay: float = 0.3):
        self.api_key = api_key
        self.delay = delay
        self.enabled = bool(api_key)
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "spotify-audit/0.7 (research tool)"
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10, pool_maxsize=10,
        )
        self._session.mount("https://", adapter)
        # Track quota usage
        self._quota_used = 0

    @property
    def quota_used(self) -> int:
        return self._quota_used

    def _get(self, endpoint: str, params: dict) -> dict | None:
        """Make a YouTube API call."""
        if not self.enabled:
            return None
        params["key"] = self.api_key
        url = f"{YOUTUBE_API}/{endpoint}"
        try:
            resp = self._session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as exc:
            if exc.response and exc.response.status_code == 403:
                logger.warning("YouTube API quota exceeded or forbidden")
                self.enabled = False  # Disable for remainder of session
            else:
                logger.debug("YouTube API error for %s: %s", endpoint, exc)
            return None
        except Exception as exc:
            logger.debug("YouTube API error for %s: %s", endpoint, exc)
            return None
        finally:
            time.sleep(self.delay)

    # ------------------------------------------------------------------
    # Channel search
    # ------------------------------------------------------------------

    def search_artist(
        self,
        name: str,
        youtube_channel_url: str | None = None,
    ) -> YouTubeArtistData | None:
        """Search for an artist on YouTube.

        If youtube_channel_url is provided (e.g. from MusicBrainz), skip the
        search step and directly fetch channel stats (saves 100 quota units).
        """
        if not self.enabled:
            return None

        result = YouTubeArtistData()

        # If we have a direct channel URL, extract the channel ID
        if youtube_channel_url:
            channel_id = self._extract_channel_id(youtube_channel_url)
            if channel_id:
                result.channel_id = channel_id
                result.channel_found = True
                result.match_confidence = 1.0
                self._fetch_channel_stats(result)
                return result

        # Search for artist channel (100 units)
        data = self._get("search", {
            "part": "snippet",
            "q": f"{name} music",
            "type": "channel",
            "maxResults": "3",
        })
        self._quota_used += 100

        if data:
            items = data.get("items", [])
            name_lower = name.lower().strip()
            for item in items:
                snippet = item.get("snippet", {})
                channel_title = snippet.get("title", "")
                channel_desc = snippet.get("description", "").lower()

                # Fuzzy match channel name to artist name
                ratio = SequenceMatcher(
                    None, name_lower, channel_title.lower().strip()
                ).ratio()

                # Also accept if artist name appears in description
                if name_lower in channel_desc:
                    ratio = max(ratio, 0.8)

                if ratio >= 0.7:
                    result.channel_found = True
                    result.channel_id = item.get("snippet", {}).get("channelId", "") or item.get("id", {}).get("channelId", "")
                    result.channel_name = channel_title
                    result.match_confidence = ratio
                    break

        # Fetch channel statistics (1 unit)
        if result.channel_found and result.channel_id:
            self._fetch_channel_stats(result)

        # Search for music videos (100 units)
        video_data = self._get("search", {
            "part": "snippet",
            "q": f'"{name}" official',
            "type": "video",
            "videoCategoryId": "10",  # Music category
            "maxResults": "5",
        })
        self._quota_used += 100

        if video_data:
            videos = video_data.get("items", [])
            # Filter to videos that actually mention the artist
            name_lower = name.lower()
            matching_videos = [
                v for v in videos
                if name_lower in v.get("snippet", {}).get("title", "").lower()
                or name_lower in v.get("snippet", {}).get("channelTitle", "").lower()
            ]
            result.music_videos_found = len(matching_videos)

        return result if (result.channel_found or result.music_videos_found > 0) else result

    def _fetch_channel_stats(self, result: YouTubeArtistData) -> None:
        """Fetch channel statistics (1 quota unit)."""
        data = self._get("channels", {
            "part": "statistics,snippet",
            "id": result.channel_id,
        })
        self._quota_used += 1

        if data:
            items = data.get("items", [])
            if items:
                stats = items[0].get("statistics", {})
                result.subscriber_count = int(stats.get("subscriberCount", 0) or 0)
                result.video_count = int(stats.get("videoCount", 0) or 0)
                result.view_count = int(stats.get("viewCount", 0) or 0)
                snippet = items[0].get("snippet", {})
                result.channel_description = snippet.get("description", "")
                if not result.channel_name:
                    result.channel_name = snippet.get("title", "")

    def _extract_channel_id(self, url: str) -> str:
        """Extract YouTube channel ID from various URL formats."""
        import re
        # https://www.youtube.com/channel/UC...
        m = re.search(r"youtube\.com/channel/(UC[a-zA-Z0-9_-]+)", url)
        if m:
            return m.group(1)
        # Could also be /c/ or /@username — would need another API call
        return ""
