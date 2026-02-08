"""
Adaptive per-API rate limiter with exponential backoff.

Each API gets its own rate limit configuration. On 429 or timeout,
backs off exponentially (1s → 2s → 4s → ... → 60s max).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class APILimiter:
    """Rate limiter for a single API."""
    name: str
    min_delay: float          # Minimum seconds between requests
    max_backoff: float = 60.0 # Maximum backoff in seconds
    _last_call: float = 0.0
    _backoff: float = 0.0     # Current backoff (0 = no backoff)
    _consecutive_errors: int = 0
    # Stats
    total_calls: int = 0
    total_errors: int = 0
    total_429s: int = 0

    def wait(self):
        """Wait appropriate time before next API call."""
        now = time.time()
        elapsed = now - self._last_call
        needed = self.min_delay + self._backoff
        if elapsed < needed:
            sleep_time = needed - elapsed
            if sleep_time > 1.0:
                logger.debug("[%s] Rate limiting: waiting %.1fs", self.name, sleep_time)
            time.sleep(sleep_time)
        self._last_call = time.time()
        self.total_calls += 1

    def success(self):
        """Record a successful call — reset backoff."""
        self._consecutive_errors = 0
        self._backoff = 0.0

    def error(self, is_rate_limit: bool = False):
        """Record an error — increase backoff."""
        self._consecutive_errors += 1
        self.total_errors += 1
        if is_rate_limit:
            self.total_429s += 1
        # Exponential backoff: 1, 2, 4, 8, 16, 32, 60 (capped)
        self._backoff = min(
            2 ** (self._consecutive_errors - 1),
            self.max_backoff,
        )
        logger.warning(
            "[%s] Error #%d — backoff now %.1fs",
            self.name, self._consecutive_errors, self._backoff,
        )

    def stats(self) -> dict:
        return {
            "api": self.name,
            "total_calls": self.total_calls,
            "total_errors": self.total_errors,
            "total_429s": self.total_429s,
            "current_backoff": self._backoff,
        }


# Pre-configured limiters per the pipeline spec
API_LIMITERS: dict[str, APILimiter] = {
    "musicbrainz": APILimiter(name="MusicBrainz", min_delay=1.1),
    "deezer": APILimiter(name="Deezer", min_delay=0.15),
    "genius": APILimiter(name="Genius", min_delay=0.25),
    "discogs": APILimiter(name="Discogs", min_delay=1.1),
    "setlistfm": APILimiter(name="Setlist.fm", min_delay=0.6),
    "lastfm": APILimiter(name="Last.fm", min_delay=0.25),
    "bandsintown": APILimiter(name="Bandsintown", min_delay=1.1),
}


def get_limiter(api_name: str) -> APILimiter:
    """Get the rate limiter for an API."""
    return API_LIMITERS[api_name]
