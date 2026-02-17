"""
Quick Scan tier — Spotify API data only.

Analyzes each artist using these signals:
  - follower / monthly-listener ratio proxy
  - genre absence
  - profile image quality
  - external URL absence
  - catalog size (albums + singles)
  - track duration uniformity
  - release cadence (releases per month)
  - playlist-heavy placement (popularity vs followers)
  - popularity-follower mismatch
  - name pattern heuristics (generic two-word names, etc.)

Each signal produces a raw 0-100 suspicion sub-score.
The final Quick score is a weighted combination.
"""

from __future__ import annotations

import re
import statistics
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from spotify_audit.config import QuickWeights, known_ai_artists
from spotify_audit.spotify_client import ArtistInfo

logger = logging.getLogger(__name__)


@dataclass
class SignalResult:
    """One signal's contribution."""
    name: str
    raw_score: float          # 0-100
    weight: float             # from config
    weighted_score: float     # raw * weight (after normalization)
    detail: str = ""          # human-readable explanation


@dataclass
class QuickScanResult:
    artist_id: str
    artist_name: str
    score: int                  # 0-100 composite
    signals: list[SignalResult] = field(default_factory=list)
    tier: str = "quick"


# ---------------------------------------------------------------------------
# Individual signal scorers (each returns 0-100, higher = more suspicious)
# ---------------------------------------------------------------------------

def _score_follower_listener_ratio(artist: ArtistInfo) -> tuple[float, str]:
    """
    Compare followers to monthly listeners. Ghost/PFC artists typically have
    very high monthly listeners (from playlist placement) but almost no
    followers (no organic fanbase). If monthly_listeners is unavailable,
    falls back to popularity as a proxy.
    """
    ml = artist.monthly_listeners
    followers = artist.followers

    # If we have monthly listeners data (from SpotifyScraper), use the real ratio
    if ml > 0:
        if followers == 0:
            return 90.0, f"{ml:,} monthly listeners but 0 followers"
        ratio = followers / ml
        # Healthy artists: ~5-20% of listeners are followers
        # Ghost artists: <0.5% of listeners are followers
        if ratio < 0.001:
            return 90.0, f"follower/listener ratio={ratio:.4f} ({followers:,}/{ml:,})"
        if ratio < 0.005:
            return 70.0, f"follower/listener ratio={ratio:.4f} ({followers:,}/{ml:,})"
        if ratio < 0.01:
            return 45.0, f"follower/listener ratio={ratio:.3f} ({followers:,}/{ml:,})"
        if ratio < 0.03:
            return 25.0, f"follower/listener ratio={ratio:.3f} (low-ish)"
        return 5.0, f"follower/listener ratio={ratio:.3f} (healthy)"

    # Fallback: use popularity as a proxy for monthly listeners
    if followers == 0 and artist.popularity > 20:
        return 90.0, f"0 followers but popularity={artist.popularity}"
    if followers == 0:
        return 60.0, "0 followers (no monthly listener data)"
    ratio = artist.popularity / max(followers, 1)
    if ratio > 1.0:
        score = min(90, 50 + ratio * 10)
        return score, f"popularity/followers ratio={ratio:.2f} (high, no ML data)"
    if ratio > 0.1:
        return 30.0, f"popularity/followers ratio={ratio:.2f} (moderate, no ML data)"
    return 5.0, f"popularity/followers ratio={ratio:.2f} (healthy, no ML data)"


def _score_genre_absence(artist: ArtistInfo) -> tuple[float, str]:
    """Artists with no genres are more suspicious — Spotify assigns genres to
    established artists automatically."""
    if not artist.genres:
        return 70.0, "No genres assigned by Spotify"
    if len(artist.genres) == 1:
        return 20.0, f"Single genre: {artist.genres[0]}"
    return 0.0, f"{len(artist.genres)} genres assigned"


def _score_image_quality(artist: ArtistInfo) -> tuple[float, str]:
    """No image or very small image is suspicious."""
    if not artist.image_url:
        return 80.0, "No profile image"
    if artist.image_width and artist.image_width < 300:
        return 40.0, f"Low-res image ({artist.image_width}x{artist.image_height})"
    return 0.0, "Profile image present"


def _score_external_url_absence(artist: ArtistInfo) -> tuple[float, str]:
    """Only having a Spotify URL (no website, socials) is a weak signal."""
    urls = artist.external_urls
    non_spotify = {k: v for k, v in urls.items() if k != "spotify"}
    if not non_spotify:
        return 50.0, "No external URLs besides Spotify"
    return 0.0, f"External URLs: {', '.join(non_spotify.keys())}"


def _score_catalog_size(artist: ArtistInfo) -> tuple[float, str]:
    """Very small catalog is neutral; massive singles-only catalog is suspicious
    (possible content farm). One album + a few singles is normal for a new artist."""
    albums = artist.album_count
    singles = artist.single_count

    if albums == 0 and singles == 0:
        return 60.0, "Empty catalog"
    if albums == 0 and singles > 20:
        return 70.0, f"0 albums, {singles} singles (singles-only farm pattern)"
    if albums == 0 and singles <= 5:
        return 30.0, f"0 albums, {singles} singles (could be new artist)"
    if singles > 50 and albums < 2:
        return 75.0, f"{albums} albums, {singles} singles (extreme singles output)"
    return 5.0, f"{albums} albums, {singles} singles"


def _score_track_duration_uniformity(artist: ArtistInfo) -> tuple[float, str]:
    """If most tracks are ~60-90s with very low variance, it's a stream-farm
    signal (short tracks = more plays = more royalties)."""
    durations = artist.track_durations
    if len(durations) < 3:
        return 20.0, f"Only {len(durations)} tracks to analyze"

    avg_ms = statistics.mean(durations)
    stdev_ms = statistics.stdev(durations)
    avg_s = avg_ms / 1000
    stdev_s = stdev_ms / 1000

    score = 0.0
    notes: list[str] = []

    # Very short average (under 2 min) is suspicious
    if avg_s < 90:
        score += 40
        notes.append(f"avg duration {avg_s:.0f}s (<90s)")
    elif avg_s < 120:
        score += 20
        notes.append(f"avg duration {avg_s:.0f}s (short)")

    # Very low standard deviation means cookie-cutter tracks
    if stdev_s < 10 and len(durations) >= 5:
        score += 35
        notes.append(f"stdev {stdev_s:.1f}s (very uniform)")
    elif stdev_s < 20:
        score += 15
        notes.append(f"stdev {stdev_s:.1f}s (somewhat uniform)")

    detail = "; ".join(notes) if notes else f"avg={avg_s:.0f}s stdev={stdev_s:.1f}s"
    return min(score, 100), detail


def _score_release_cadence(artist: ArtistInfo) -> tuple[float, str]:
    """Abnormally high release frequency (e.g., multiple releases per week)
    suggests automated production."""
    dates = artist.release_dates
    if len(dates) < 2:
        return 10.0, f"Only {len(dates)} releases"

    parsed: list[datetime] = []
    for d in dates:
        try:
            if len(d) == 4:  # year only
                parsed.append(datetime(int(d), 7, 1, tzinfo=timezone.utc))
            elif len(d) == 7:  # YYYY-MM
                parsed.append(datetime.strptime(d + "-15", "%Y-%m-%d").replace(tzinfo=timezone.utc))
            else:
                parsed.append(datetime.strptime(d[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc))
        except ValueError:
            continue

    if len(parsed) < 2:
        return 10.0, "Could not parse enough release dates"

    parsed.sort()
    span_days = (parsed[-1] - parsed[0]).days
    if span_days == 0:
        return 80.0, f"{len(parsed)} releases on the same day"

    span_months = max(span_days / 30.0, 1)
    releases_per_month = len(parsed) / span_months

    if releases_per_month > 8:
        return 90.0, f"{releases_per_month:.1f} releases/month (extreme)"
    if releases_per_month > 4:
        return 65.0, f"{releases_per_month:.1f} releases/month (high)"
    if releases_per_month > 2:
        return 35.0, f"{releases_per_month:.1f} releases/month (elevated)"
    return 5.0, f"{releases_per_month:.1f} releases/month"


def _score_playlist_placement(artist: ArtistInfo) -> tuple[float, str]:
    """High popularity but low followers suggests playlist-driven streams
    rather than organic fan base. This is a proxy since we can't see
    playlist placements directly."""
    pop = artist.popularity
    followers = artist.followers

    if pop >= 40 and followers < 500:
        return 80.0, f"popularity={pop} with only {followers} followers"
    if pop >= 30 and followers < 200:
        return 70.0, f"popularity={pop} with only {followers} followers"
    if pop >= 20 and followers < 50:
        return 60.0, f"popularity={pop} with only {followers} followers"
    return 5.0, f"popularity={pop}, followers={followers}"


def _score_popularity_follower_mismatch(artist: ArtistInfo) -> tuple[float, str]:
    """Top tracks have high popularity but overall artist popularity or
    follower count is low."""
    pops = artist.top_track_popularities
    if not pops:
        return 20.0, "No top tracks data"

    max_track_pop = max(pops)
    avg_track_pop = statistics.mean(pops)

    if max_track_pop > 50 and artist.followers < 300:
        return 80.0, (
            f"Top track popularity={max_track_pop} but only "
            f"{artist.followers} followers"
        )
    if avg_track_pop > 30 and artist.followers < 500:
        return 55.0, (
            f"Avg track popularity={avg_track_pop:.0f} but only "
            f"{artist.followers} followers"
        )
    return 5.0, f"Track pop avg={avg_track_pop:.0f}, followers={artist.followers}"


def _score_name_pattern(artist: ArtistInfo) -> tuple[float, str]:
    """Heuristic name analysis.
    Ghost/AI artists often have generic two-word names
    (adjective + noun), no special characters, or follow patterns like
    'The Adjective Noun'."""
    name = artist.name

    # Check against known AI artist blocklist
    if name.lower() in known_ai_artists():
        return 100.0, "Name matches known AI artist blocklist"

    score = 0.0
    notes: list[str] = []

    # Generic pattern: "The Adjective Noun" or "Adjective Noun"
    if re.match(r"^(The\s+)?[A-Z][a-z]+\s+[A-Z][a-z]+s?$", name):
        score += 25
        notes.append("Generic two-word name pattern")

    # All-lowercase single word (common in functional music)
    if re.match(r"^[a-z]{3,15}$", name):
        score += 20
        notes.append("Single lowercase word")

    # Very short name (1-3 chars)
    if len(name.strip()) <= 3:
        score += 15
        notes.append("Very short name")

    detail = "; ".join(notes) if notes else "Name appears distinctive"
    return min(score, 100), detail


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

SIGNAL_FUNCTIONS = [
    ("follower_listener_ratio",      _score_follower_listener_ratio),
    ("genre_absence",                _score_genre_absence),
    ("image_quality",                _score_image_quality),
    ("external_url_absence",         _score_external_url_absence),
    ("catalog_size",                 _score_catalog_size),
    ("track_duration_uniformity",    _score_track_duration_uniformity),
    ("release_cadence",              _score_release_cadence),
    ("playlist_placement",           _score_playlist_placement),
    ("popularity_follower_mismatch", _score_popularity_follower_mismatch),
    ("name_pattern",                 _score_name_pattern),
]


def quick_scan(artist: ArtistInfo, weights: QuickWeights | None = None) -> QuickScanResult:
    """Run all Quick-tier signals on a single enriched artist."""
    if weights is None:
        weights = QuickWeights()

    norm = weights.normalized()
    signals: list[SignalResult] = []
    total = 0.0

    for name, fn in SIGNAL_FUNCTIONS:
        raw, detail = fn(artist)
        w = norm.get(name, 0)
        weighted = raw * w
        total += weighted
        signals.append(SignalResult(
            name=name,
            raw_score=round(raw, 1),
            weight=round(w, 4),
            weighted_score=round(weighted, 2),
            detail=detail,
        ))

    composite = int(min(max(round(total), 0), 100))
    return QuickScanResult(
        artist_id=artist.artist_id,
        artist_name=artist.name,
        score=composite,
        signals=signals,
    )
