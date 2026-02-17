"""
Configuration and default scoring weights for spotify-audit.

All weights are configurable. The scoring system produces a 0-100 legitimacy
score where higher = more legitimate:
  80-100 = Verified Artist
  55-79  = Likely Authentic
  35-54  = Inconclusive
  15-34  = Suspicious
  0-14   = Likely Artificial
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PACKAGE_DIR = Path(__file__).resolve().parent
BLOCKLIST_DIR = PACKAGE_DIR / "blocklists"
DATA_DIR = PACKAGE_DIR / "data"
DEFAULT_DB_PATH = DATA_DIR / "cache.db"

# ---------------------------------------------------------------------------
# Threat categories
# ---------------------------------------------------------------------------
THREAT_CATEGORIES = {
    1:   "PFC Ghost Artist",
    1.5: "PFC + AI Hybrid",
    2:   "Independent AI Artist",
    3:   "AI Fraud Farm",
    4:   "AI Impersonation",
}

# ---------------------------------------------------------------------------
# Score range labels
# ---------------------------------------------------------------------------
SCORE_LABELS = {
    (80, 100): "Verified Artist",
    (55, 79):  "Likely Authentic",
    (35, 54):  "Inconclusive",
    (15, 34):  "Suspicious",
    (0, 14):   "Likely Artificial",
}


def score_label(score: int) -> str:
    for (lo, hi), label in SCORE_LABELS.items():
        if lo <= score <= hi:
            return label
    return "Unknown"


# ---------------------------------------------------------------------------
# Deep analysis threshold — artists scoring above this get Claude analysis
# ---------------------------------------------------------------------------
ESCALATE_TO_DEEP = 50       # Score > this -> run Deep analysis


# ---------------------------------------------------------------------------
# Quick-scan signal weights (must sum to 1.0)
# ---------------------------------------------------------------------------
@dataclass
class QuickWeights:
    """Weights for Quick-tier Spotify-only signals."""
    follower_listener_ratio: float = 0.15
    genre_absence: float = 0.10
    image_quality: float = 0.05
    external_url_absence: float = 0.10
    catalog_size: float = 0.10
    track_duration_uniformity: float = 0.10
    release_cadence: float = 0.15
    playlist_placement: float = 0.10
    popularity_follower_mismatch: float = 0.10
    name_pattern: float = 0.05

    def total(self) -> float:
        return sum(self.__dict__.values())

    def normalized(self) -> dict[str, float]:
        t = self.total()
        if t == 0:
            return self.__dict__.copy()
        return {k: v / t for k, v in self.__dict__.items()}


# ---------------------------------------------------------------------------
# Standard-scan signal weights (must sum to 1.0)
# ---------------------------------------------------------------------------
@dataclass
class StandardWeights:
    """Weights for Standard-tier signals (adds external DB lookups)."""
    quick_score: float = 0.40
    genius_credits: float = 0.12          # songwriter/producer credits
    discogs_physical: float = 0.12        # physical releases (vinyl/CD)
    live_show_history: float = 0.12       # concert history (setlist.fm)
    musicbrainz_presence: float = 0.08    # MusicBrainz metadata quality
    label_blocklist_match: float = 0.10   # PFC distributor/label match
    deezer_cross_check: float = 0.06      # Deezer presence & fan validation

    def total(self) -> float:
        return sum(self.__dict__.values())


# ---------------------------------------------------------------------------
# Deep-scan signal weights
# ---------------------------------------------------------------------------
@dataclass
class DeepWeights:
    """Weights for Deep-tier signals (adds Claude analysis)."""
    standard_score: float = 0.35
    social_media_analysis: float = 0.15
    image_ai_artifacts: float = 0.15
    bio_analysis: float = 0.10
    claude_synthesis: float = 0.25

    def total(self) -> float:
        return sum(self.__dict__.values())


# ---------------------------------------------------------------------------
# Master config
# ---------------------------------------------------------------------------
@dataclass
class AuditConfig:
    quick_weights: QuickWeights = field(default_factory=QuickWeights)
    standard_weights: StandardWeights = field(default_factory=StandardWeights)
    deep_weights: DeepWeights = field(default_factory=DeepWeights)

    escalate_to_deep: int = ESCALATE_TO_DEEP

    cache_ttl_days: int = 7
    db_path: Path = DEFAULT_DB_PATH

    anthropic_api_key: str = ""

    # External API keys (all free-tier)
    genius_token: str = ""              # Genius access token
    discogs_token: str = ""             # Discogs personal access token
    setlistfm_api_key: str = ""         # setlist.fm API key
    # Rate-limit / batching
    claude_batch_size: int = 5          # artists per Claude API call
    max_retries: int = 5
    backoff_base: float = 2.0           # exponential backoff base in seconds
    scrape_delay: float = 2.0           # seconds between Spotify embed requests


# ---------------------------------------------------------------------------
# Blocklist loader
# ---------------------------------------------------------------------------
def load_blocklist(name: str) -> list[str]:
    """Load a blocklist JSON file by name (without .json extension)."""
    path = BLOCKLIST_DIR / f"{name}.json"
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def _load_blocklist_set(name: str) -> frozenset[str]:
    """Load a blocklist as a frozenset for O(1) membership checks."""
    items = load_blocklist(name)
    return frozenset(item.lower() for item in items)


# Convenience loaders — cached so file I/O only happens once per process.
# Returns frozensets for O(1) membership testing instead of O(n) list scans.
@lru_cache(maxsize=None)
def pfc_distributors() -> frozenset[str]:
    return _load_blocklist_set("pfc_distributors")


@lru_cache(maxsize=None)
def pfc_playlists() -> frozenset[str]:
    return _load_blocklist_set("pfc_playlists")


@lru_cache(maxsize=None)
def known_ai_artists() -> frozenset[str]:
    return _load_blocklist_set("known_ai_artists")


@lru_cache(maxsize=None)
def pfc_songwriters() -> frozenset[str]:
    return _load_blocklist_set("pfc_songwriters")
