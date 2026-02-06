"""
Scoring engine.

Combines signal results from each analysis tier into a final artist score,
maps to threat categories, and handles escalation decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from spotify_audit.config import (
    AuditConfig,
    THREAT_CATEGORIES,
    score_label,
)
from spotify_audit.analyzers.quick import QuickScanResult


@dataclass
class ArtistReport:
    """Aggregated report for a single artist across all tiers that ran."""
    artist_id: str
    artist_name: str

    quick_score: int | None = None
    standard_score: int | None = None
    deep_score: int | None = None

    final_score: int = 0
    label: str = ""

    # Most likely threat category (number) and name
    threat_category: float | None = None
    threat_category_name: str = ""

    # Which tiers actually ran
    tiers_completed: list[str] = field(default_factory=list)

    # Carry forward signal details for reporting
    quick_signals: list[dict] = field(default_factory=list)
    standard_signals: list[dict] = field(default_factory=list)
    deep_signals: list[dict] = field(default_factory=list)


@dataclass
class PlaylistReport:
    """Aggregated report for the entire playlist."""
    playlist_name: str
    playlist_id: str
    owner: str
    total_tracks: int
    total_unique_artists: int
    is_spotify_owned: bool

    health_score: int = 0           # 0 = all fake, 100 = all legit
    artists: list[ArtistReport] = field(default_factory=list)

    # Breakdown counts
    verified_legit: int = 0
    probably_fine: int = 0
    suspicious: int = 0
    likely_non_authentic: int = 0


# ---------------------------------------------------------------------------
# Score aggregation
# ---------------------------------------------------------------------------

def finalize_artist_report(
    artist_id: str,
    artist_name: str,
    quick_result: QuickScanResult | None = None,
    standard_result: dict | None = None,
    deep_result: dict | None = None,
) -> ArtistReport:
    """Build an ArtistReport from whichever tiers completed."""
    report = ArtistReport(artist_id=artist_id, artist_name=artist_name)

    if quick_result:
        report.quick_score = quick_result.score
        report.tiers_completed.append("quick")
        report.quick_signals = [
            {
                "name": s.name,
                "raw_score": s.raw_score,
                "weight": s.weight,
                "weighted_score": s.weighted_score,
                "detail": s.detail,
            }
            for s in quick_result.signals
        ]

    # Standard and Deep are placeholders for now
    if standard_result:
        report.standard_score = standard_result.get("score")
        report.tiers_completed.append("standard")
        report.standard_signals = standard_result.get("signals", [])

    if deep_result:
        report.deep_score = deep_result.get("score")
        report.tiers_completed.append("deep")
        report.deep_signals = deep_result.get("signals", [])

    # Final score = most advanced tier that ran
    if report.deep_score is not None:
        report.final_score = report.deep_score
    elif report.standard_score is not None:
        report.final_score = report.standard_score
    elif report.quick_score is not None:
        report.final_score = report.quick_score

    report.label = score_label(report.final_score)
    report.threat_category = _infer_threat_category(report)
    if report.threat_category is not None:
        report.threat_category_name = THREAT_CATEGORIES.get(report.threat_category, "")

    return report


def _infer_threat_category(report: ArtistReport) -> float | None:
    """Best-effort threat category assignment from Quick signals alone.
    Standard/Deep tiers will refine this with label and web data."""
    if report.final_score < 30:
        return None  # Not suspicious enough to categorize

    signals = {s["name"]: s for s in report.quick_signals}

    # Heuristic classification based on available Quick signals
    catalog = signals.get("catalog_size", {})
    cadence = signals.get("release_cadence", {})
    name_sig = signals.get("name_pattern", {})
    duration = signals.get("track_duration_uniformity", {})

    catalog_raw = catalog.get("raw_score", 0)
    cadence_raw = cadence.get("raw_score", 0)
    name_raw = name_sig.get("raw_score", 0)
    duration_raw = duration.get("raw_score", 0)

    # Known AI artist by name
    if name_raw >= 100:
        return 2  # Independent AI Artist

    # Extreme output + uniform durations -> fraud farm
    if cadence_raw >= 65 and duration_raw >= 50:
        return 3  # AI Fraud Farm

    # High catalog suspicion + playlist-driven -> PFC ghost
    if catalog_raw >= 50 and report.final_score >= 40:
        return 1  # PFC Ghost Artist

    # Generic high suspicion without strong indicators
    if report.final_score >= 50:
        return 1  # Default to PFC Ghost (most common)

    return None


# ---------------------------------------------------------------------------
# Playlist-level aggregation
# ---------------------------------------------------------------------------

def build_playlist_report(
    playlist_name: str,
    playlist_id: str,
    owner: str,
    total_tracks: int,
    is_spotify_owned: bool,
    artist_reports: list[ArtistReport],
) -> PlaylistReport:
    """Aggregate individual artist reports into a playlist health report."""
    pr = PlaylistReport(
        playlist_name=playlist_name,
        playlist_id=playlist_id,
        owner=owner,
        total_tracks=total_tracks,
        total_unique_artists=len(artist_reports),
        is_spotify_owned=is_spotify_owned,
        artists=sorted(artist_reports, key=lambda a: a.final_score, reverse=True),
    )

    for a in artist_reports:
        if a.final_score <= 20:
            pr.verified_legit += 1
        elif a.final_score <= 40:
            pr.probably_fine += 1
        elif a.final_score <= 70:
            pr.suspicious += 1
        else:
            pr.likely_non_authentic += 1

    # Health score: inverse of average suspicion
    if artist_reports:
        avg_suspicion = sum(a.final_score for a in artist_reports) / len(artist_reports)
        pr.health_score = int(max(0, min(100, 100 - avg_suspicion)))
    else:
        pr.health_score = 100

    return pr


def should_escalate_to_standard(score: int, config: AuditConfig) -> bool:
    return score > config.escalate_to_standard


def should_escalate_to_deep(score: int, config: AuditConfig) -> bool:
    return score > config.escalate_to_deep
