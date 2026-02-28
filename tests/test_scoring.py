"""Tests for spotify_audit.scoring — verdict scoring, report building, threat inference."""

from __future__ import annotations

import pytest

from spotify_audit.evidence import (
    ArtistEvaluation,
    Evidence,
    ExternalData,
    PlatformPresence,
    Verdict,
)
from spotify_audit.scoring import (
    ArtistReport,
    PlaylistReport,
    _verdict_to_score,
    _infer_threat_category,
    finalize_artist_report,
    build_playlist_report,
    should_escalate_to_deep,
)
from spotify_audit.config import AuditConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_evaluation(
    verdict: Verdict,
    confidence: str = "medium",
    red_flags: list[Evidence] | None = None,
    green_flags: list[Evidence] | None = None,
) -> ArtistEvaluation:
    return ArtistEvaluation(
        artist_id="test",
        artist_name="Test Artist",
        verdict=verdict,
        confidence=confidence,
        platform_presence=PlatformPresence(deezer=True),
        red_flags=red_flags or [],
        green_flags=green_flags or [],
    )


def _make_flag(finding: str, source: str = "test", strength: str = "moderate",
               flag_type: str = "red_flag", detail: str = "",
               tags: list[str] | None = None) -> Evidence:
    return Evidence(
        finding=finding,
        source=source,
        evidence_type=flag_type,
        strength=strength,
        detail=detail,
        tags=tags or [],
    )


# ---------------------------------------------------------------------------
# _verdict_to_score
# ---------------------------------------------------------------------------

class TestVerdictToScore:
    def test_verified_artist_high_confidence(self):
        ev = _make_evaluation(Verdict.VERIFIED_ARTIST, "high")
        score = _verdict_to_score(ev)
        assert 82 <= score <= 100

    def test_likely_authentic_medium_confidence(self):
        ev = _make_evaluation(Verdict.LIKELY_AUTHENTIC, "medium")
        score = _verdict_to_score(ev)
        assert 58 <= score <= 81

    def test_inconclusive_low_confidence(self):
        ev = _make_evaluation(Verdict.INCONCLUSIVE, "low")
        score = _verdict_to_score(ev)
        assert 38 <= score <= 57

    def test_suspicious_medium_confidence(self):
        ev = _make_evaluation(Verdict.SUSPICIOUS, "medium")
        score = _verdict_to_score(ev)
        assert 18 <= score <= 37

    def test_likely_artificial_high_confidence(self):
        ev = _make_evaluation(Verdict.LIKELY_ARTIFICIAL, "high")
        score = _verdict_to_score(ev)
        assert 0 <= score <= 17

    def test_score_never_exceeds_100(self):
        ev = _make_evaluation(
            Verdict.VERIFIED_ARTIST, "high",
            green_flags=[_make_flag("g", strength="strong", flag_type="green_flag")] * 20,
        )
        score = _verdict_to_score(ev)
        assert score <= 100

    def test_score_never_below_0(self):
        ev = _make_evaluation(
            Verdict.LIKELY_ARTIFICIAL, "high",
            red_flags=[_make_flag("r", strength="strong")] * 20,
        )
        score = _verdict_to_score(ev)
        assert score >= 0

    def test_green_flags_push_score_up_within_range(self):
        ev_no_flags = _make_evaluation(Verdict.LIKELY_AUTHENTIC, "medium")
        ev_green = _make_evaluation(
            Verdict.LIKELY_AUTHENTIC, "medium",
            green_flags=[_make_flag("g", strength="strong", flag_type="green_flag")] * 5,
        )
        score_no = _verdict_to_score(ev_no_flags)
        score_green = _verdict_to_score(ev_green)
        assert score_green >= score_no

    def test_red_flags_push_score_down_within_range(self):
        ev_no_flags = _make_evaluation(Verdict.SUSPICIOUS, "medium")
        ev_red = _make_evaluation(
            Verdict.SUSPICIOUS, "medium",
            red_flags=[_make_flag("r", strength="strong")] * 5,
        )
        score_no = _verdict_to_score(ev_no_flags)
        score_red = _verdict_to_score(ev_red)
        assert score_red <= score_no

    def test_high_confidence_higher_than_low(self):
        ev_high = _make_evaluation(Verdict.VERIFIED_ARTIST, "high")
        ev_low = _make_evaluation(Verdict.VERIFIED_ARTIST, "low")
        assert _verdict_to_score(ev_high) >= _verdict_to_score(ev_low)


# ---------------------------------------------------------------------------
# _infer_threat_category
# ---------------------------------------------------------------------------

class TestInferThreatCategory:
    def test_verified_artist_no_threat(self):
        report = ArtistReport(
            artist_id="t", artist_name="T",
            evaluation=_make_evaluation(Verdict.VERIFIED_ARTIST),
        )
        assert _infer_threat_category(report) is None

    def test_likely_authentic_no_threat(self):
        report = ArtistReport(
            artist_id="t", artist_name="T",
            evaluation=_make_evaluation(Verdict.LIKELY_AUTHENTIC),
        )
        assert _infer_threat_category(report) is None

    def test_inconclusive_no_threat(self):
        report = ArtistReport(
            artist_id="t", artist_name="T",
            evaluation=_make_evaluation(Verdict.INCONCLUSIVE),
        )
        assert _infer_threat_category(report) is None

    def test_suspicious_pfc_ghost(self):
        report = ArtistReport(
            artist_id="t", artist_name="T",
            evaluation=_make_evaluation(
                Verdict.SUSPICIOUS,
                red_flags=[_make_flag("Label matches PFC blocklist",
                                      tags=["pfc_label"])],
            ),
        )
        assert _infer_threat_category(report) == 1  # PFC Ghost Artist

    def test_suspicious_ai_generated(self):
        report = ArtistReport(
            artist_id="t", artist_name="T",
            evaluation=_make_evaluation(
                Verdict.SUSPICIOUS,
                red_flags=[_make_flag("AI-generated content",
                                      tags=["ai_generated_image"])],
            ),
        )
        assert _infer_threat_category(report) == 2  # Independent AI Artist

    def test_suspicious_impersonation(self):
        report = ArtistReport(
            artist_id="t", artist_name="T",
            evaluation=_make_evaluation(
                Verdict.SUSPICIOUS,
                red_flags=[_make_flag("Possible impersonation",
                                      tags=["impersonation"])],
            ),
        )
        assert _infer_threat_category(report) == 4  # AI Impersonation

    def test_suspicious_pfc_ai_hybrid(self):
        report = ArtistReport(
            artist_id="t", artist_name="T",
            evaluation=_make_evaluation(
                Verdict.SUSPICIOUS,
                red_flags=[
                    _make_flag("PFC label", tags=["pfc_label"]),
                    _make_flag("AI image", tags=["ai_generated_image"]),
                ],
            ),
        )
        assert _infer_threat_category(report) == 1.5  # PFC + AI Hybrid


# ---------------------------------------------------------------------------
# finalize_artist_report
# ---------------------------------------------------------------------------

class TestFinalizeArtistReport:
    def test_with_evidence_evaluation(self):
        ev = _make_evaluation(Verdict.VERIFIED_ARTIST, "high")
        report = finalize_artist_report("a1", "Artist One", evaluation=ev)
        assert report.artist_id == "a1"
        assert report.artist_name == "Artist One"
        assert report.label == "Verified Artist"
        assert 82 <= report.final_score <= 100
        assert report.evaluation is ev

    def test_legacy_fallback_without_evaluation(self):
        """When no evidence evaluation exists, use inverted tier scores."""
        from spotify_audit.analyzers.quick import QuickScanResult, SignalResult
        qr = QuickScanResult(
            artist_id="a2", artist_name="Artist Two",
            score=70,
            signals=[SignalResult(name="test", raw_score=70, weight=1.0,
                                  weighted_score=70, detail="test")],
        )
        report = finalize_artist_report("a2", "Artist Two", quick_result=qr)
        # Score should be 100 - 70 = 30 (legitimacy scale)
        assert report.final_score == 30
        assert "quick" in report.tiers_completed

    def test_tiers_completed_tracking(self):
        from spotify_audit.analyzers.quick import QuickScanResult, SignalResult
        from spotify_audit.analyzers.standard import StandardScanResult
        qr = QuickScanResult(
            artist_id="a3", artist_name="Artist Three",
            score=50,
            signals=[SignalResult(name="t", raw_score=50, weight=1.0,
                                  weighted_score=50, detail="")],
        )
        sr = StandardScanResult(
            artist_id="a3", artist_name="Artist Three",
            score=60,
            signals=[SignalResult(name="t", raw_score=60, weight=1.0,
                                  weighted_score=60, detail="")],
        )
        report = finalize_artist_report(
            "a3", "Artist Three",
            quick_result=qr,
            standard_result=sr,
        )
        assert "quick" in report.tiers_completed
        assert "standard" in report.tiers_completed


# ---------------------------------------------------------------------------
# build_playlist_report
# ---------------------------------------------------------------------------

class TestBuildPlaylistReport:
    def _make_artist_report(self, verdict: Verdict, score: int) -> ArtistReport:
        return ArtistReport(
            artist_id="t", artist_name="T",
            evaluation=_make_evaluation(verdict),
            final_score=score,
            label=verdict.value,
        )

    def test_health_score_all_verified(self):
        artists = [self._make_artist_report(Verdict.VERIFIED_ARTIST, 95) for _ in range(5)]
        pr = build_playlist_report("Test", "p1", "owner", 50, False, artists)
        assert pr.health_score == 100
        assert pr.verified_artists == 5

    def test_health_score_all_artificial(self):
        artists = [self._make_artist_report(Verdict.LIKELY_ARTIFICIAL, 5) for _ in range(5)]
        pr = build_playlist_report("Test", "p1", "owner", 50, False, artists)
        assert pr.health_score == 0
        assert pr.likely_artificial == 5

    def test_health_score_mixed(self):
        artists = [
            self._make_artist_report(Verdict.VERIFIED_ARTIST, 95),
            self._make_artist_report(Verdict.LIKELY_ARTIFICIAL, 5),
        ]
        pr = build_playlist_report("Test", "p1", "owner", 50, False, artists)
        assert pr.health_score == 50  # (100 + 0) / 2

    def test_empty_playlist(self):
        pr = build_playlist_report("Empty", "p2", "owner", 0, False, [])
        assert pr.health_score == 100
        assert pr.total_unique_artists == 0

    def test_artists_sorted_by_verdict_severity(self):
        artists = [
            self._make_artist_report(Verdict.VERIFIED_ARTIST, 95),
            self._make_artist_report(Verdict.LIKELY_ARTIFICIAL, 5),
            self._make_artist_report(Verdict.SUSPICIOUS, 25),
        ]
        pr = build_playlist_report("Test", "p3", "owner", 50, False, artists)
        # Most concerning first
        assert pr.artists[0].verdict_enum == Verdict.LIKELY_ARTIFICIAL
        assert pr.artists[1].verdict_enum == Verdict.SUSPICIOUS
        assert pr.artists[2].verdict_enum == Verdict.VERIFIED_ARTIST

    def test_legacy_breakdown_counts(self):
        artists = [
            self._make_artist_report(Verdict.VERIFIED_ARTIST, 90),
            self._make_artist_report(Verdict.LIKELY_AUTHENTIC, 65),
            self._make_artist_report(Verdict.LIKELY_ARTIFICIAL, 5),
        ]
        pr = build_playlist_report("Test", "p4", "owner", 30, False, artists)
        assert pr.verified_legit == 1   # score >= 82
        assert pr.probably_fine == 1    # score 58-81
        assert pr.needs_review == 1  # score < 58


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------

class TestEscalation:
    def test_escalate_to_deep(self):
        config = AuditConfig()
        assert should_escalate_to_deep(51, config) is True
        assert should_escalate_to_deep(50, config) is False
        assert should_escalate_to_deep(49, config) is False


# ---------------------------------------------------------------------------
# ArtistReport properties
# ---------------------------------------------------------------------------

class TestArtistReportProperties:
    def test_verdict_from_evaluation(self):
        ev = _make_evaluation(Verdict.SUSPICIOUS)
        report = ArtistReport(artist_id="t", artist_name="T", evaluation=ev)
        assert report.verdict == "Suspicious"
        assert report.verdict_enum == Verdict.SUSPICIOUS

    def test_verdict_fallback_to_label(self):
        report = ArtistReport(artist_id="t", artist_name="T", label="Inconclusive")
        assert report.verdict == "Inconclusive"
        assert report.verdict_enum == Verdict.INCONCLUSIVE
