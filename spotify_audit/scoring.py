"""
Scoring engine.

Combines evidence-based evaluations with legacy signal results into
artist and playlist-level reports.

The primary evaluation is now evidence-based (see evidence.py), which
produces a Verdict + explanation instead of a simple 0-100 score.
The legacy Quick/Standard weighted scores are retained as supplementary data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from spotify_audit.config import (
    AuditConfig,
    THREAT_CATEGORIES,
    score_label,
)
from spotify_audit.analyzers.quick import QuickScanResult
from spotify_audit.analyzers.standard import StandardScanResult
from spotify_audit.evidence import ArtistEvaluation, Verdict


# Map verdicts to sort order (most concerning first)
_VERDICT_ORDER = {
    Verdict.LIKELY_ARTIFICIAL: 0,
    Verdict.SUSPICIOUS: 1,
    Verdict.INCONCLUSIVE: 2,
    Verdict.LIKELY_AUTHENTIC: 3,
    Verdict.VERIFIED_ARTIST: 4,
}


@dataclass
class ArtistReport:
    """Aggregated report for a single artist across all tiers that ran."""
    artist_id: str
    artist_name: str

    # Evidence-based evaluation (primary)
    evaluation: ArtistEvaluation | None = None

    # Legacy weighted scores (supplementary)
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

    @property
    def verdict(self) -> str:
        if self.evaluation:
            return self.evaluation.verdict.value
        return self.label

    @property
    def verdict_enum(self) -> Verdict:
        if self.evaluation:
            return self.evaluation.verdict
        return Verdict.INCONCLUSIVE


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

    # Evidence-based breakdown
    verified_artists: int = 0
    likely_authentic: int = 0
    inconclusive: int = 0
    suspicious: int = 0
    likely_artificial: int = 0

    # Legacy breakdown (kept for backward compatibility)
    verified_legit: int = 0
    probably_fine: int = 0
    likely_non_authentic: int = 0


# ---------------------------------------------------------------------------
# Score aggregation
# ---------------------------------------------------------------------------

def finalize_artist_report(
    artist_id: str,
    artist_name: str,
    evaluation: ArtistEvaluation | None = None,
    quick_result: QuickScanResult | None = None,
    standard_result: StandardScanResult | None = None,
    deep_result: dict | None = None,
) -> ArtistReport:
    """Build an ArtistReport from evidence evaluation + scan tiers."""
    report = ArtistReport(artist_id=artist_id, artist_name=artist_name)

    # Evidence-based evaluation (primary)
    report.evaluation = evaluation

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

    if standard_result:
        report.standard_score = standard_result.score
        report.tiers_completed.append("standard")
        report.standard_signals = [
            {
                "name": s.name,
                "raw_score": s.raw_score,
                "weight": s.weight,
                "weighted_score": s.weighted_score,
                "detail": s.detail,
            }
            for s in standard_result.signals
        ]

    if deep_result:
        report.deep_score = deep_result.get("score")
        report.tiers_completed.append("deep")
        report.deep_signals = deep_result.get("signals", [])

    # Final score: derive from evidence verdict when available
    if report.evaluation:
        report.final_score = _verdict_to_score(report.evaluation)
        report.label = report.evaluation.verdict.value
    else:
        # Legacy fallback: invert old tier scores so higher = more legit
        tier_score = None
        if report.deep_score is not None:
            tier_score = report.deep_score
        elif report.standard_score is not None:
            tier_score = report.standard_score
        elif report.quick_score is not None:
            tier_score = report.quick_score
        report.final_score = max(0, 100 - (tier_score or 0))
        report.label = score_label(report.final_score)
    report.threat_category = _infer_threat_category(report)
    if report.threat_category is not None:
        report.threat_category_name = THREAT_CATEGORIES.get(report.threat_category, "")

    return report


def _verdict_to_score(ev: ArtistEvaluation) -> int:
    """Convert evidence verdict + flag balance into a 0-100 legitimacy score.

    Score ranges:
        Verified Artist:    80-100
        Likely Authentic:   55-79
        Inconclusive:       35-54
        Suspicious:         15-34
        Likely Artificial:  0-14
    """
    # Base score from verdict
    base_ranges = {
        Verdict.VERIFIED_ARTIST: (80, 100),
        Verdict.LIKELY_AUTHENTIC: (55, 79),
        Verdict.INCONCLUSIVE: (35, 54),
        Verdict.SUSPICIOUS: (15, 34),
        Verdict.LIKELY_ARTIFICIAL: (0, 14),
    }
    lo, hi = base_ranges.get(ev.verdict, (35, 54))

    # Confidence shifts within range
    conf_frac = {"high": 0.85, "medium": 0.55, "low": 0.25}.get(ev.confidence, 0.5)

    # Adjust based on green/red flag balance
    strong_greens = len(ev.strong_green_flags)
    strong_reds = len(ev.strong_red_flags)
    green_total = len(ev.green_flags)
    red_total = len(ev.red_flags)

    # Net signal: positive = more green, negative = more red
    net = (strong_greens * 3 + green_total) - (strong_reds * 3 + red_total)
    # Normalize net to [-1, 1] range
    max_possible = max(strong_greens * 3 + green_total + strong_reds * 3 + red_total, 1)
    net_frac = max(-1.0, min(1.0, net / max_possible))

    # Blend: 70% confidence, 30% net signal
    position = conf_frac * 0.7 + (net_frac + 1) / 2 * 0.3

    score = int(lo + position * (hi - lo))
    return max(0, min(100, score))


def _infer_threat_category(report: ArtistReport) -> float | None:
    """Assign threat category using evidence-based verdict as primary signal.

    Only assigns a threat category when the evidence verdict is Suspicious
    or Likely Artificial.  Uses red flag content to distinguish between
    PFC Ghost (1), AI Hybrid (1.5), Independent AI (2), AI Fraud Farm (3),
    and AI Impersonation (4).  Falls back to legacy quick signals when no
    evidence evaluation is available.
    """
    ev = report.evaluation

    # If we have an evidence-based verdict, use it as the gate
    if ev:
        # Verified / Likely Authentic / Inconclusive → no threat category
        if ev.verdict in (Verdict.VERIFIED_ARTIST, Verdict.LIKELY_AUTHENTIC,
                          Verdict.INCONCLUSIVE):
            return None

        # Suspicious or Likely Artificial — dig into the red flags
        red_findings = " ".join(
            (e.finding + " " + e.detail).lower() for e in ev.red_flags
        )

        has_pfc = "pfc" in red_findings or "content farm" in red_findings
        has_ai = ("ai generat" in red_findings or "ai_generated" in red_findings
                  or "ai-generated" in red_findings)
        has_ghost = "ghost" in red_findings or "pfc_ghost" in red_findings
        has_impersonation = "impersonat" in red_findings

        # Check Claude synthesis if present
        synth_findings = " ".join(
            (e.finding + " " + e.detail).lower()
            for e in ev.red_flags if e.source == "Claude synthesis"
        )
        synth_pfc = "pfc" in synth_findings
        synth_ai = "ai" in synth_findings

        if has_impersonation:
            return 4   # AI Impersonation
        if has_pfc and has_ai:
            return 1.5  # PFC + AI Hybrid
        if synth_ai and not synth_pfc:
            return 2   # Independent AI Artist
        if has_ai and not has_pfc and not has_ghost:
            return 2   # Independent AI Artist
        if has_pfc or has_ghost:
            return 1   # PFC Ghost Artist

        # Fall through: look at pattern signals for fraud farm
        signals = {s["name"]: s for s in report.quick_signals}
        cadence_raw = signals.get("release_cadence", {}).get("raw_score", 0)
        duration_raw = signals.get("track_duration_uniformity", {}).get("raw_score", 0)
        if cadence_raw >= 65 and duration_raw >= 50:
            return 3   # AI Fraud Farm

        # Default: PFC Ghost for Suspicious/Likely Artificial without specifics
        return 1

    # No evidence evaluation — legacy fallback using quick signals only
    if report.final_score < 30:
        return None

    signals = {s["name"]: s for s in report.quick_signals}
    catalog_raw = signals.get("catalog_size", {}).get("raw_score", 0)
    cadence_raw = signals.get("release_cadence", {}).get("raw_score", 0)
    name_raw = signals.get("name_pattern", {}).get("raw_score", 0)
    duration_raw = signals.get("track_duration_uniformity", {}).get("raw_score", 0)

    if name_raw >= 100:
        return 2   # Independent AI Artist
    if cadence_raw >= 65 and duration_raw >= 50:
        return 3   # AI Fraud Farm
    if catalog_raw >= 50 and report.final_score >= 40:
        return 1   # PFC Ghost Artist
    if report.final_score >= 50:
        return 1
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
    # Sort by verdict severity (most concerning first), then by score
    sorted_reports = sorted(
        artist_reports,
        key=lambda a: (_VERDICT_ORDER.get(a.verdict_enum, 2), -a.final_score),
    )

    pr = PlaylistReport(
        playlist_name=playlist_name,
        playlist_id=playlist_id,
        owner=owner,
        total_tracks=total_tracks,
        total_unique_artists=len(artist_reports),
        is_spotify_owned=is_spotify_owned,
        artists=sorted_reports,
    )

    # Evidence-based breakdown
    for a in artist_reports:
        v = a.verdict_enum
        if v == Verdict.VERIFIED_ARTIST:
            pr.verified_artists += 1
        elif v == Verdict.LIKELY_AUTHENTIC:
            pr.likely_authentic += 1
        elif v == Verdict.INCONCLUSIVE:
            pr.inconclusive += 1
        elif v == Verdict.SUSPICIOUS:
            pr.suspicious += 1
        elif v == Verdict.LIKELY_ARTIFICIAL:
            pr.likely_artificial += 1

    # Legacy breakdown (from legitimacy score) — uses separate counters
    for a in artist_reports:
        if a.final_score >= 80:
            pr.verified_legit += 1
        elif a.final_score >= 55:
            pr.probably_fine += 1
        else:
            pr.likely_non_authentic += 1

    # Health score: based on evidence verdicts
    if artist_reports:
        # Weight: Verified=100, LikelyAuth=85, Inconclusive=50, Suspicious=25, LikelyArtificial=0
        verdict_health = {
            Verdict.VERIFIED_ARTIST: 100,
            Verdict.LIKELY_AUTHENTIC: 85,
            Verdict.INCONCLUSIVE: 50,
            Verdict.SUSPICIOUS: 25,
            Verdict.LIKELY_ARTIFICIAL: 0,
        }
        total_health = sum(
            verdict_health.get(a.verdict_enum, 50) for a in artist_reports
        )
        pr.health_score = int(total_health / len(artist_reports))
    else:
        pr.health_score = 100

    return pr


def should_escalate_to_standard(score: int, config: AuditConfig) -> bool:
    return score > config.escalate_to_standard


def should_escalate_to_deep(score: int, config: AuditConfig) -> bool:
    return score > config.escalate_to_deep
