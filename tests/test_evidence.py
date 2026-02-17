"""Tests for spotify_audit.evidence — collectors, decision tree, evaluation."""

from __future__ import annotations

import pytest

from spotify_audit.spotify_client import ArtistInfo
from spotify_audit.evidence import (
    ArtistEvaluation,
    Evidence,
    ExternalData,
    PlatformPresence,
    Verdict,
    evaluate_artist,
    incorporate_deep_evidence,
    compute_category_scores,
    is_obviously_legitimate,
    fast_mode_evaluation,
    _collect_follower_evidence,
    _collect_catalog_evidence,
    _collect_duration_evidence,
    _collect_release_evidence,
    _collect_label_evidence,
    _collect_name_evidence,
    _collect_collaboration_evidence,
    _collect_genre_evidence,
    _collect_track_rank_evidence,
    _collect_genius_evidence,
    _collect_discogs_evidence,
    _collect_live_show_evidence,
    _collect_musicbrainz_evidence,
    _collect_lastfm_evidence,
    _collect_social_media_evidence,
    _collect_identity_evidence,
    _collect_touring_geography_evidence,
    _decide_verdict,
)


# ---------------------------------------------------------------------------
# PlatformPresence
# ---------------------------------------------------------------------------

class TestPlatformPresence:
    def test_count_empty(self):
        p = PlatformPresence()
        assert p.count() == 0

    def test_count_all(self):
        p = PlatformPresence(
            spotify=True, deezer=True, musicbrainz=True,
            genius=True, discogs=True, setlistfm=True, lastfm=True,
        )
        assert p.count() == 7

    def test_names(self):
        p = PlatformPresence(spotify=True, deezer=True, deezer_fans=1000)
        names = p.names()
        assert "Spotify" in names
        assert any("Deezer" in n for n in names)
        assert any("1,000" in n for n in names)


# ---------------------------------------------------------------------------
# Follower evidence
# ---------------------------------------------------------------------------

class TestFollowerEvidence:
    def test_high_fans_strong_green(self):
        artist = ArtistInfo(artist_id="a", name="A", deezer_fans=500_000)
        ev = _collect_follower_evidence(artist)
        greens = [e for e in ev if e.evidence_type == "green_flag"]
        assert any(e.strength == "strong" for e in greens)

    def test_moderate_fans(self):
        artist = ArtistInfo(artist_id="a", name="A", deezer_fans=50_000)
        ev = _collect_follower_evidence(artist)
        greens = [e for e in ev if e.evidence_type == "green_flag"]
        assert any(e.strength == "moderate" for e in greens)

    def test_tiny_fans_red(self):
        artist = ArtistInfo(artist_id="a", name="A", deezer_fans=10)
        ev = _collect_follower_evidence(artist)
        reds = [e for e in ev if e.evidence_type == "red_flag"]
        assert len(reds) >= 1

    def test_zero_fans_neutral(self):
        artist = ArtistInfo(artist_id="a", name="A")
        ev = _collect_follower_evidence(artist)
        neutrals = [e for e in ev if e.evidence_type == "neutral"]
        assert len(neutrals) >= 1

    def test_listener_follower_mismatch(self):
        artist = ArtistInfo(
            artist_id="a", name="A",
            monthly_listeners=100_000, followers=50,
        )
        ev = _collect_follower_evidence(artist)
        reds = [e for e in ev if e.evidence_type == "red_flag" and "ratio" in e.finding.lower()]
        assert len(reds) >= 1


# ---------------------------------------------------------------------------
# Catalog evidence
# ---------------------------------------------------------------------------

class TestCatalogEvidence:
    def test_empty_catalog_red(self):
        artist = ArtistInfo(artist_id="a", name="A", album_count=0, single_count=0)
        ev = _collect_catalog_evidence(artist)
        assert any(e.evidence_type == "red_flag" for e in ev)

    def test_albums_green(self):
        artist = ArtistInfo(artist_id="a", name="A", album_count=5, single_count=3)
        ev = _collect_catalog_evidence(artist)
        assert any(e.evidence_type == "green_flag" for e in ev)

    def test_singles_only_content_farm(self):
        artist = ArtistInfo(artist_id="a", name="A", album_count=0, single_count=25)
        ev = _collect_catalog_evidence(artist)
        reds = [e for e in ev if e.evidence_type == "red_flag" and "content farm" in e.finding.lower()]
        assert len(reds) == 1


# ---------------------------------------------------------------------------
# Duration evidence
# ---------------------------------------------------------------------------

class TestDurationEvidence:
    def test_very_short_tracks_red(self):
        artist = ArtistInfo(
            artist_id="a", name="A",
            track_durations=[45000, 50000, 48000, 46000, 47000],
        )
        ev = _collect_duration_evidence(artist)
        reds = [e for e in ev if e.evidence_type == "red_flag"]
        assert any(e.strength == "strong" for e in reds)

    def test_normal_durations_green(self):
        artist = ArtistInfo(
            artist_id="a", name="A",
            track_durations=[200000, 250000, 180000, 300000, 220000, 270000],
        )
        ev = _collect_duration_evidence(artist)
        greens = [e for e in ev if e.evidence_type == "green_flag"]
        assert len(greens) >= 1

    def test_uniform_durations_red(self):
        artist = ArtistInfo(
            artist_id="a", name="A",
            track_durations=[181000, 182000, 180000, 181000, 182000,
                             181000, 180000, 182000, 181000, 180000],
        )
        ev = _collect_duration_evidence(artist)
        reds = [e for e in ev if "uniform" in e.finding.lower()]
        assert len(reds) >= 1

    def test_too_few_tracks_skipped(self):
        artist = ArtistInfo(artist_id="a", name="A", track_durations=[200000, 210000])
        ev = _collect_duration_evidence(artist)
        assert len(ev) == 0


# ---------------------------------------------------------------------------
# Release evidence
# ---------------------------------------------------------------------------

class TestReleaseEvidence:
    def test_extreme_release_cadence(self):
        # 16 releases in a single month
        dates = [f"2024-01-{d:02d}" for d in range(1, 17)]
        artist = ArtistInfo(
            artist_id="a", name="A",
            release_dates=dates, album_count=0, single_count=16,
        )
        ev = _collect_release_evidence(artist)
        reds = [e for e in ev if e.evidence_type == "red_flag"]
        assert len(reds) >= 1

    def test_normal_cadence_green(self):
        dates = ["2020-01-15", "2020-06-20", "2021-03-10",
                 "2021-09-01", "2022-04-15", "2023-01-20"]
        artist = ArtistInfo(
            artist_id="a", name="A",
            release_dates=dates, album_count=3, single_count=3,
        )
        ev = _collect_release_evidence(artist)
        greens = [e for e in ev if e.evidence_type == "green_flag"]
        assert len(greens) >= 1

    def test_same_day_releases_red(self):
        dates = ["2024-01-15"] * 10
        artist = ArtistInfo(
            artist_id="a", name="A",
            release_dates=dates, album_count=0, single_count=10,
        )
        ev = _collect_release_evidence(artist)
        reds = [e for e in ev if e.evidence_type == "red_flag" and "same day" in e.finding.lower()]
        assert len(reds) == 1


# ---------------------------------------------------------------------------
# Label evidence
# ---------------------------------------------------------------------------

class TestLabelEvidence:
    def test_pfc_label_strong_red(self):
        from spotify_audit.config import pfc_distributors
        pfc = pfc_distributors()
        if not pfc:
            pytest.skip("No PFC distributors in blocklist")
        artist = ArtistInfo(artist_id="a", name="A", labels=[next(iter(pfc))])
        ev = _collect_label_evidence(artist)
        reds = [e for e in ev if e.evidence_type == "red_flag" and "PFC" in e.finding]
        assert len(reds) >= 1

    def test_clean_label_neutral(self):
        artist = ArtistInfo(artist_id="a", name="A", labels=["Sony Music"])
        ev = _collect_label_evidence(artist)
        reds = [e for e in ev if e.evidence_type == "red_flag"]
        assert len(reds) == 0

    def test_no_labels_no_evidence(self):
        artist = ArtistInfo(artist_id="a", name="A", labels=[])
        ev = _collect_label_evidence(artist)
        assert len(ev) == 0


# ---------------------------------------------------------------------------
# Name evidence
# ---------------------------------------------------------------------------

class TestNameEvidence:
    def test_known_ai_name_red(self):
        from spotify_audit.config import known_ai_artists
        ai_names = known_ai_artists()
        if not ai_names:
            pytest.skip("No known AI artists in blocklist")
        artist = ArtistInfo(artist_id="a", name=next(iter(ai_names)))
        ev = _collect_name_evidence(artist)
        reds = [e for e in ev if e.evidence_type == "red_flag"]
        assert any(e.strength == "strong" for e in reds)

    def test_generic_two_word_name(self):
        artist = ArtistInfo(artist_id="a", name="Gentle Waves")
        ev = _collect_name_evidence(artist)
        reds = [e for e in ev if "generic" in e.finding.lower()]
        assert len(reds) >= 1

    def test_mood_word_titles(self):
        artist = ArtistInfo(
            artist_id="a", name="Test",
            track_titles=["Morning Calm", "Peaceful Rain", "Gentle Breeze",
                           "Soft Light", "Quiet Dawn"],
        )
        ev = _collect_name_evidence(artist)
        reds = [e for e in ev if "mood" in e.finding.lower()]
        assert len(reds) >= 1


# ---------------------------------------------------------------------------
# Collaboration evidence
# ---------------------------------------------------------------------------

class TestCollaborationEvidence:
    def test_many_collaborators_green(self):
        artist = ArtistInfo(
            artist_id="a", name="A",
            contributors=["B", "C", "D", "E"],
        )
        ev = _collect_collaboration_evidence(artist)
        greens = [e for e in ev if e.evidence_type == "green_flag"]
        assert len(greens) >= 1
        assert any(e.strength == "moderate" for e in greens)

    def test_no_collaborators_no_evidence(self):
        artist = ArtistInfo(artist_id="a", name="A", contributors=[])
        ev = _collect_collaboration_evidence(artist)
        assert len(ev) == 0


# ---------------------------------------------------------------------------
# Genre evidence
# ---------------------------------------------------------------------------

class TestGenreEvidence:
    def test_no_genres_red(self):
        artist = ArtistInfo(artist_id="a", name="A", genres=[])
        ev = _collect_genre_evidence(artist)
        reds = [e for e in ev if e.evidence_type == "red_flag"]
        assert len(reds) >= 1

    def test_many_genres_green(self):
        artist = ArtistInfo(artist_id="a", name="A",
                             genres=["rock", "alternative", "indie", "post-punk"])
        ev = _collect_genre_evidence(artist)
        greens = [e for e in ev if e.evidence_type == "green_flag"]
        assert len(greens) >= 1


# ---------------------------------------------------------------------------
# Track rank evidence
# ---------------------------------------------------------------------------

class TestTrackRankEvidence:
    def test_high_ranks_green(self):
        artist = ArtistInfo(
            artist_id="a", name="A",
            track_ranks=[800000, 750000, 700000, 650000, 600000],
        )
        ev = _collect_track_rank_evidence(artist)
        greens = [e for e in ev if e.evidence_type == "green_flag"]
        assert len(greens) >= 1

    def test_top_heavy_concentration_red(self):
        artist = ArtistInfo(
            artist_id="a", name="A",
            track_ranks=[10000, 8000, 10, 5, 2],
        )
        ev = _collect_track_rank_evidence(artist)
        reds = [e for e in ev if "top 2" in e.finding.lower()]
        assert len(reds) >= 1


# ---------------------------------------------------------------------------
# External API evidence: Genius
# ---------------------------------------------------------------------------

class TestGeniusEvidence:
    def test_not_found_red(self):
        ext = ExternalData(genius_found=False)
        ev = _collect_genius_evidence(ext)
        reds = [e for e in ev if e.evidence_type == "red_flag"]
        assert len(reds) >= 1

    def test_many_songs_strong_green(self):
        ext = ExternalData(genius_found=True, genius_song_count=30)
        ev = _collect_genius_evidence(ext)
        greens = [e for e in ev if e.evidence_type == "green_flag"]
        assert any(e.strength == "strong" for e in greens)

    def test_found_zero_songs_red(self):
        ext = ExternalData(genius_found=True, genius_song_count=0)
        ev = _collect_genius_evidence(ext)
        reds = [e for e in ev if e.evidence_type == "red_flag"]
        assert len(reds) >= 1


# ---------------------------------------------------------------------------
# External API evidence: Discogs
# ---------------------------------------------------------------------------

class TestDiscogsEvidence:
    def test_not_found_red(self):
        ext = ExternalData(discogs_found=False)
        ev = _collect_discogs_evidence(ext)
        reds = [e for e in ev if e.evidence_type == "red_flag"]
        assert len(reds) >= 1

    def test_many_physical_strong_green(self):
        ext = ExternalData(
            discogs_found=True,
            discogs_physical_releases=15,
            discogs_total_releases=20,
            discogs_formats=["Vinyl", "CD"],
        )
        ev = _collect_discogs_evidence(ext)
        greens = [e for e in ev if e.evidence_type == "green_flag"]
        assert any(e.strength == "strong" for e in greens)


# ---------------------------------------------------------------------------
# External API evidence: Live shows
# ---------------------------------------------------------------------------

class TestLiveShowEvidence:
    def test_many_shows_strong_green(self):
        ext = ExternalData(setlistfm_found=True, setlistfm_total_shows=100)
        ev = _collect_live_show_evidence(ext)
        greens = [e for e in ev if e.evidence_type == "green_flag"]
        assert any(e.strength == "strong" for e in greens)

    def test_no_shows_red(self):
        ext = ExternalData(setlistfm_found=False)
        ev = _collect_live_show_evidence(ext)
        reds = [e for e in ev if e.evidence_type == "red_flag"]
        assert len(reds) >= 1


# ---------------------------------------------------------------------------
# External API evidence: MusicBrainz
# ---------------------------------------------------------------------------

class TestMusicBrainzEvidence:
    def test_not_found_red(self):
        ext = ExternalData(musicbrainz_found=False)
        ev = _collect_musicbrainz_evidence(ext)
        reds = [e for e in ev if e.evidence_type == "red_flag"]
        assert len(reds) >= 1

    def test_rich_profile_green(self):
        ext = ExternalData(
            musicbrainz_found=True,
            musicbrainz_type="Group",
            musicbrainz_country="GB",
            musicbrainz_begin_date="1985",
            musicbrainz_labels=["XL Recordings"],
        )
        ev = _collect_musicbrainz_evidence(ext)
        greens = [e for e in ev if e.evidence_type == "green_flag"]
        assert any(e.strength == "moderate" for e in greens)


# ---------------------------------------------------------------------------
# External API evidence: Last.fm
# ---------------------------------------------------------------------------

class TestLastfmEvidence:
    def test_not_found_red(self):
        ext = ExternalData(lastfm_found=False)
        ev = _collect_lastfm_evidence(ext)
        reds = [e for e in ev if e.evidence_type == "red_flag"]
        assert len(reds) >= 1

    def test_strong_engagement_green(self):
        ext = ExternalData(
            lastfm_found=True,
            lastfm_listeners=100_000,
            lastfm_playcount=5_000_000,
            lastfm_listener_play_ratio=50.0,
        )
        ev = _collect_lastfm_evidence(ext)
        greens = [e for e in ev if e.evidence_type == "green_flag"]
        assert any(e.strength == "strong" for e in greens)

    def test_low_engagement_red(self):
        ext = ExternalData(
            lastfm_found=True,
            lastfm_listeners=500,
            lastfm_playcount=600,
            lastfm_listener_play_ratio=1.2,
        )
        ev = _collect_lastfm_evidence(ext)
        reds = [e for e in ev if e.evidence_type == "red_flag" and "engagement" in e.finding.lower()]
        assert len(reds) >= 1


# ---------------------------------------------------------------------------
# Social media evidence
# ---------------------------------------------------------------------------

class TestSocialMediaEvidence:
    def test_many_social_links_strong(self):
        ext = ExternalData(
            genius_found=True,
            genius_facebook_name="test",
            genius_instagram_name="test",
            genius_twitter_name="test",
            musicbrainz_found=True,
            musicbrainz_urls={"youtube": "https://youtube.com/test"},
        )
        ev = _collect_social_media_evidence(ext)
        greens = [e for e in ev if e.evidence_type == "green_flag"]
        assert any(e.strength == "strong" for e in greens)

    def test_no_social_links_red(self):
        ext = ExternalData(
            genius_found=True,
            discogs_found=True,
            musicbrainz_found=True,
        )
        ev = _collect_social_media_evidence(ext)
        reds = [e for e in ev if e.evidence_type == "red_flag"]
        assert len(reds) >= 1

    def test_wikipedia_strong_green(self):
        ext = ExternalData(
            musicbrainz_found=True,
            musicbrainz_urls={"wikipedia": "https://en.wikipedia.org/wiki/Test"},
        )
        ev = _collect_social_media_evidence(ext)
        greens = [e for e in ev if "Wikipedia" in e.finding]
        assert any(e.strength == "strong" for e in greens)


# ---------------------------------------------------------------------------
# Identity evidence
# ---------------------------------------------------------------------------

class TestIdentityEvidence:
    def test_isni_strong_green(self):
        ext = ExternalData(musicbrainz_isnis=["0000000121268987"])
        ev = _collect_identity_evidence(ext)
        greens = [e for e in ev if "ISNI" in e.finding]
        assert any(e.strength == "strong" for e in greens)

    def test_ipi_strong_green(self):
        ext = ExternalData(musicbrainz_ipis=["00123456789"])
        ev = _collect_identity_evidence(ext)
        greens = [e for e in ev if "IPI" in e.finding]
        assert any(e.strength == "strong" for e in greens)

    def test_detailed_bio_with_career(self):
        ext = ExternalData(
            discogs_found=True,
            discogs_profile="Born in 1975 in Manchester, formed in 1990. "
                            "Toured extensively with Grammy-nominated albums. "
                            "Collaborated with many artists over three decades.",
        )
        ev = _collect_identity_evidence(ext)
        greens = [e for e in ev if "career" in e.finding.lower() or "bio" in e.finding.lower()]
        assert len(greens) >= 1

    def test_real_name_green(self):
        ext = ExternalData(discogs_realname="John Smith")
        ev = _collect_identity_evidence(ext)
        greens = [e for e in ev if "Real name" in e.finding]
        assert len(greens) == 1

    def test_group_members_green(self):
        ext = ExternalData(discogs_members=["Alice", "Bob", "Charlie"])
        ev = _collect_identity_evidence(ext)
        greens = [e for e in ev if "member" in e.finding.lower()]
        assert len(greens) >= 1


# ---------------------------------------------------------------------------
# Touring geography evidence
# ---------------------------------------------------------------------------

class TestTouringGeographyEvidence:
    def test_international_touring_strong_green(self):
        ext = ExternalData(
            setlistfm_found=True,
            setlistfm_venue_countries=["US", "UK", "Japan", "France", "Germany"],
        )
        ev = _collect_touring_geography_evidence(ext)
        greens = [e for e in ev if e.evidence_type == "green_flag"]
        assert any(e.strength == "strong" for e in greens)

    def test_not_found_empty(self):
        ext = ExternalData(setlistfm_found=False)
        ev = _collect_touring_geography_evidence(ext)
        assert len(ev) == 0


# ---------------------------------------------------------------------------
# Decision tree
# ---------------------------------------------------------------------------

class TestDecideVerdict:
    def _flag(self, finding: str, strength: str, flag_type: str) -> Evidence:
        return Evidence(
            finding=finding, source="test",
            evidence_type=flag_type, strength=strength, detail="",
        )

    def test_known_ai_name_likely_artificial(self):
        reds = [Evidence(
            finding="Name matches known AI artist blocklist",
            source="Blocklist",
            evidence_type="red_flag", strength="strong", detail="",
            tags=["known_ai_artist"],
        )]
        path: list[str] = []
        verdict, conf = _decide_verdict(reds, [], PlatformPresence(), path)
        assert verdict == Verdict.LIKELY_ARTIFICIAL
        assert conf == "high"

    def test_pfc_plus_farm_likely_artificial(self):
        reds = [
            Evidence(finding="Label matches PFC blocklist", source="Blocklist",
                     evidence_type="red_flag", strength="strong", detail="",
                     tags=["pfc_label"]),
            Evidence(finding="content farm pattern", source="Deezer",
                     evidence_type="red_flag", strength="strong", detail="",
                     tags=["content_farm"]),
        ]
        path: list[str] = []
        verdict, conf = _decide_verdict(reds, [], PlatformPresence(), path)
        assert verdict == Verdict.LIKELY_ARTIFICIAL

    def test_strong_greens_verified(self):
        greens = [
            self._flag("Many fans", "strong", "green_flag"),
            self._flag("Many concerts", "strong", "green_flag"),
        ]
        path: list[str] = []
        verdict, conf = _decide_verdict([], greens, PlatformPresence(), path)
        assert verdict == Verdict.VERIFIED_ARTIST
        assert conf == "high"

    def test_multi_platform_plus_fans_verified(self):
        presence = PlatformPresence(
            spotify=True, deezer=True, genius=True, deezer_fans=100_000,
        )
        greens = [Evidence(
            finding="100,000 fans", source="Deezer",
            evidence_type="green_flag", strength="strong", detail="",
            tags=["genuine_fans"],
        )]
        path: list[str] = []
        verdict, conf = _decide_verdict([], greens, presence, path)
        assert verdict == Verdict.VERIFIED_ARTIST

    def test_few_balanced_flags_insufficient_data(self):
        """Two balanced flags → too few signals to judge."""
        reds = [self._flag("r", "moderate", "red_flag")]
        greens = [self._flag("g", "moderate", "green_flag")]
        path: list[str] = []
        verdict, _ = _decide_verdict(reds, greens, PlatformPresence(), path)
        assert verdict == Verdict.INSUFFICIENT_DATA

    def test_many_balanced_flags_conflicting_signals(self):
        """Many substantial flags on both sides → conflicting signals."""
        reds = [self._flag(f"r{i}", "moderate", "red_flag") for i in range(3)]
        greens = [self._flag(f"g{i}", "moderate", "green_flag") for i in range(3)]
        path: list[str] = []
        verdict, _ = _decide_verdict(reds, greens, PlatformPresence(), path)
        assert verdict == Verdict.CONFLICTING_SIGNALS

    def test_moderate_balanced_flags_inconclusive(self):
        """Equal strengths, 5+ flags, both strengths < 4 → generic Inconclusive."""
        # 3 weak reds = strength 3, 3 weak greens = strength 3
        # Total flags = 6 (≥5), both strengths < 4 → Inconclusive
        reds = [self._flag(f"r{i}", "weak", "red_flag") for i in range(3)]
        greens = [self._flag(f"g{i}", "weak", "green_flag") for i in range(3)]
        path: list[str] = []
        verdict, _ = _decide_verdict(reds, greens, PlatformPresence(), path)
        assert verdict == Verdict.INCONCLUSIVE

    def test_more_green_likely_authentic(self):
        greens = [
            self._flag("g1", "moderate", "green_flag"),
            self._flag("g2", "moderate", "green_flag"),
            self._flag("g3", "weak", "green_flag"),
        ]
        reds = [self._flag("r1", "weak", "red_flag")]
        path: list[str] = []
        verdict, _ = _decide_verdict(reds, greens, PlatformPresence(), path)
        assert verdict == Verdict.LIKELY_AUTHENTIC

    def test_more_red_suspicious(self):
        reds = [
            self._flag("r1", "moderate", "red_flag"),
            self._flag("r2", "moderate", "red_flag"),
            self._flag("r3", "weak", "red_flag"),
        ]
        greens = [self._flag("g1", "weak", "green_flag")]
        path: list[str] = []
        verdict, _ = _decide_verdict(reds, greens, PlatformPresence(), path)
        assert verdict == Verdict.SUSPICIOUS


# ---------------------------------------------------------------------------
# Full evaluation (integration)
# ---------------------------------------------------------------------------

class TestEvaluateArtist:
    def test_legitimate_artist_verdict(self, legitimate_artist, rich_external_data):
        ev = evaluate_artist(legitimate_artist, rich_external_data)
        assert ev.verdict in (Verdict.VERIFIED_ARTIST, Verdict.LIKELY_AUTHENTIC)
        assert ev.confidence in ("high", "medium")
        assert ev.green_flag_count > ev.red_flag_count

    def test_ghost_artist_verdict(self, ghost_artist, empty_external_data):
        ev = evaluate_artist(ghost_artist, empty_external_data)
        assert ev.verdict in (Verdict.SUSPICIOUS, Verdict.LIKELY_ARTIFICIAL)
        assert ev.red_flag_count > ev.green_flag_count

    def test_evaluation_has_decision_path(self, legitimate_artist):
        ev = evaluate_artist(legitimate_artist)
        assert len(ev.decision_path) >= 1

    def test_platform_presence_updated_from_external(self, legitimate_artist, rich_external_data):
        ev = evaluate_artist(legitimate_artist, rich_external_data)
        assert ev.platform_presence.genius is True
        assert ev.platform_presence.discogs is True
        assert ev.platform_presence.setlistfm is True
        assert ev.platform_presence.musicbrainz is True
        assert ev.platform_presence.lastfm is True

    def test_sources_reached(self, legitimate_artist, rich_external_data):
        ev = evaluate_artist(legitimate_artist, rich_external_data)
        sources = ev.sources_reached
        assert sources["Genius"] is True
        assert sources["Discogs"] is True
        assert sources["Last.fm"] is True


# ---------------------------------------------------------------------------
# incorporate_deep_evidence
# ---------------------------------------------------------------------------

class TestIncorporateDeepEvidence:
    def test_deep_evidence_shifts_verdict(self, ghost_artist):
        """Adding many strong green deep-evidence flags can upgrade a verdict."""
        ev = evaluate_artist(ghost_artist)
        original_verdict = ev.verdict

        deep_flags = [
            Evidence(
                finding=f"Claude confirms legitimate ({i})",
                source="Claude synthesis",
                evidence_type="green_flag",
                strength="strong",
                detail="Confirmed by AI analysis",
            )
            for i in range(10)
        ]
        updated = incorporate_deep_evidence(ev, deep_flags)
        # The verdict should improve (or at least not worsen) with strong green evidence
        assert updated.green_flag_count > ev.green_flag_count
        assert "Deep tier" in updated.decision_path[0]

    def test_empty_deep_evidence_no_change(self, legitimate_artist):
        ev = evaluate_artist(legitimate_artist)
        updated = incorporate_deep_evidence(ev, [])
        assert updated is ev  # should return same object


# ---------------------------------------------------------------------------
# Category scores
# ---------------------------------------------------------------------------

class TestCategoryScores:
    def test_category_keys(self, legitimate_artist, rich_external_data):
        ev = evaluate_artist(legitimate_artist, rich_external_data)
        scores = compute_category_scores(ev)
        expected_keys = {
            "Platform Presence", "Fan Engagement", "Creative History",
            "Live Performance", "Online Identity", "Industry Signals",
        }
        assert set(scores.keys()) == expected_keys

    def test_scores_in_range(self, legitimate_artist, rich_external_data):
        ev = evaluate_artist(legitimate_artist, rich_external_data)
        scores = compute_category_scores(ev)
        for key, val in scores.items():
            assert 0 <= val <= 100, f"{key} out of range: {val}"

    def test_legitimate_artist_high_scores(self, legitimate_artist, rich_external_data):
        ev = evaluate_artist(legitimate_artist, rich_external_data)
        scores = compute_category_scores(ev)
        # A well-known artist should score well on most categories
        assert scores["Platform Presence"] >= 50
        assert scores["Live Performance"] >= 50
        assert scores["Industry Signals"] >= 30


# ---------------------------------------------------------------------------
# Fast Mode
# ---------------------------------------------------------------------------

class TestFastMode:
    def test_obviously_legitimate(self):
        artist = ArtistInfo(
            artist_id="a", name="Big Star",
            followers=1_000_000,
            genres=["rock", "alternative", "indie"],
            album_count=10, single_count=5,
            external_urls={"wikipedia": "https://en.wikipedia.org/wiki/Big_Star"},
            labels=["Sony Music"],
        )
        assert is_obviously_legitimate(artist) is True

    def test_not_legitimate_low_followers(self):
        artist = ArtistInfo(
            artist_id="a", name="Small Artist",
            followers=1_000,
            genres=["rock", "alternative", "indie"],
            album_count=10,
            external_urls={"wikipedia": "https://en.wikipedia.org/wiki/Test"},
        )
        assert is_obviously_legitimate(artist) is False

    def test_not_legitimate_no_wikipedia(self):
        artist = ArtistInfo(
            artist_id="a", name="No Wiki",
            followers=1_000_000,
            genres=["rock", "alternative", "indie"],
            album_count=10,
        )
        assert is_obviously_legitimate(artist) is False

    def test_not_legitimate_few_genres(self):
        artist = ArtistInfo(
            artist_id="a", name="Few Genres",
            followers=1_000_000,
            genres=["rock"],
            album_count=10,
            external_urls={"wikipedia": "https://en.wikipedia.org/wiki/Test"},
        )
        assert is_obviously_legitimate(artist) is False

    def test_not_legitimate_few_albums(self):
        artist = ArtistInfo(
            artist_id="a", name="Few Albums",
            followers=1_000_000,
            genres=["rock", "alternative", "indie"],
            album_count=2,
            external_urls={"wikipedia": "https://en.wikipedia.org/wiki/Test"},
        )
        assert is_obviously_legitimate(artist) is False

    def test_fast_mode_evaluation_returns_verified(self):
        artist = ArtistInfo(
            artist_id="a", name="Big Star",
            followers=1_000_000,
            genres=["rock", "alternative", "indie"],
            album_count=10,
            external_urls={"wikipedia": "https://en.wikipedia.org/wiki/Big_Star"},
        )
        ev = fast_mode_evaluation(artist)
        assert ev.verdict == Verdict.VERIFIED_ARTIST
        assert ev.confidence == "high"
        assert len(ev.green_flags) >= 1
        assert len(ev.red_flags) == 0
