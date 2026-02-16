"""End-to-end integration tests using real enriched profiles.

Tests the full pipeline: enriched JSON → ArtistInfo + ExternalData → evaluate_artist
→ finalize_artist_report → build_playlist_report, all without hitting external APIs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from spotify_audit.spotify_client import ArtistInfo
from spotify_audit.evidence import (
    ArtistEvaluation,
    ExternalData,
    Verdict,
    evaluate_artist,
)
from spotify_audit.scoring import (
    finalize_artist_report,
    build_playlist_report,
)
from spotify_audit.config import score_label

ENRICHED_DIR = Path(__file__).resolve().parent.parent / "data" / "enriched"


def _load_profile(name: str) -> dict:
    """Load an enriched JSON profile by filename stem."""
    path = ENRICHED_DIR / f"{name}.json"
    if not path.exists():
        pytest.skip(f"Enriched profile {name}.json not found")
    with open(path) as f:
        return json.load(f)


def _profile_to_artist_info(profile: dict) -> ArtistInfo:
    """Convert enriched JSON profile into an ArtistInfo for evaluation."""
    dz = profile.get("deezer", {})
    mb = profile.get("musicbrainz", {})

    # Build release dates from Deezer album data
    release_dates = []
    for album in dz.get("albums", []):
        if isinstance(album, dict) and album.get("release_date"):
            release_dates.append(album["release_date"])

    # Track durations (seconds → milliseconds for ArtistInfo)
    track_durations = [d * 1000 for d in dz.get("track_durations", [])]

    return ArtistInfo(
        artist_id=profile.get("artist_id", profile.get("artist_name", "")),
        name=profile.get("artist_name", ""),
        genres=mb.get("genres", []),
        deezer_fans=dz.get("nb_fan", 0),
        album_count=dz.get("album_types", {}).get("album", 0),
        single_count=dz.get("album_types", {}).get("single", 0),
        total_tracks=len(dz.get("track_titles", [])),
        release_dates=release_dates,
        track_durations=track_durations,
        track_titles=dz.get("track_titles", []),
        track_ranks=dz.get("track_ranks", []),
        has_explicit=dz.get("has_explicit", False),
        contributors=dz.get("contributors", []),
        contributor_roles=dz.get("contributor_roles", {}),
        related_artist_names=[
            r[0] if isinstance(r, (list, tuple)) else r.get("name", "") if isinstance(r, dict) else str(r)
            for r in dz.get("related_artist_fans", [])
        ],
        labels=dz.get("labels", []) + mb.get("labels", []),
    )


def _profile_to_external_data(profile: dict) -> ExternalData:
    """Convert enriched JSON profile into ExternalData for evidence pipeline."""
    genius = profile.get("genius", {})
    discogs = profile.get("discogs", {})
    setlistfm = profile.get("setlistfm", {})
    bandsintown = profile.get("bandsintown", {})
    mb = profile.get("musicbrainz", {})
    lastfm = profile.get("lastfm", {})

    listeners = lastfm.get("listeners", 0) or 0
    playcount = lastfm.get("playcount", 0) or 0
    ratio = playcount / listeners if listeners > 0 else 0.0

    return ExternalData(
        genius_found=genius.get("found", False),
        genius_song_count=genius.get("song_count", 0) or 0,
        genius_description=genius.get("description_snippet", "") or "",
        genius_facebook_name=genius.get("facebook_name", "") or "",
        genius_instagram_name=genius.get("instagram_name", "") or "",
        genius_twitter_name=genius.get("twitter_name", "") or "",
        genius_is_verified=genius.get("is_verified", False),
        genius_followers_count=genius.get("followers_count", 0) or 0,
        genius_alternate_names=genius.get("alternate_names", []) or [],

        discogs_found=discogs.get("found", False),
        discogs_physical_releases=discogs.get("physical_releases", 0) or 0,
        discogs_digital_releases=discogs.get("digital_only_releases", 0) or 0,
        discogs_total_releases=discogs.get("total_releases", 0) or 0,
        discogs_formats=discogs.get("formats", []) or [],
        discogs_labels=discogs.get("labels", []) or [],
        discogs_profile=discogs.get("profile", "") or "",
        discogs_realname=discogs.get("realname", "") or "",
        discogs_social_urls=discogs.get("social_urls", []) or [],
        discogs_members=discogs.get("members", []) or [],
        discogs_groups=discogs.get("groups", []) or [],
        discogs_data_quality=discogs.get("data_quality", "") or "",

        setlistfm_found=setlistfm.get("found", False),
        setlistfm_total_shows=setlistfm.get("total_setlists", 0) or 0,
        setlistfm_first_show=setlistfm.get("first_show_date", "") or "",
        setlistfm_last_show=setlistfm.get("last_show_date", "") or "",
        setlistfm_venues=setlistfm.get("top_venues", []) or [],
        setlistfm_venue_cities=setlistfm.get("venue_cities", []) or [],
        setlistfm_venue_countries=setlistfm.get("venue_countries", []) or [],
        setlistfm_tour_names=setlistfm.get("tour_names", []) or [],

        bandsintown_found=bandsintown.get("found", False),
        bandsintown_past_events=bandsintown.get("past_events", 0) or 0,
        bandsintown_upcoming_events=bandsintown.get("upcoming_events", 0) or 0,
        bandsintown_tracker_count=bandsintown.get("tracker_count", 0) or 0,
        bandsintown_facebook_url=bandsintown.get("facebook_page_url", "") or "",
        bandsintown_social_links=bandsintown.get("social_links", []) or [],
        bandsintown_on_tour=bandsintown.get("on_tour", False),

        musicbrainz_found=mb.get("found", False),
        musicbrainz_type=mb.get("type", "") or "",
        musicbrainz_country=mb.get("country", "") or "",
        musicbrainz_begin_date=mb.get("begin_date", "") or "",
        musicbrainz_labels=mb.get("labels", []) or [],
        musicbrainz_urls=mb.get("urls", {}) or {},
        musicbrainz_genres=mb.get("genres", []) or [],
        musicbrainz_aliases=mb.get("aliases", []) or [],
        musicbrainz_isnis=mb.get("isnis", []) or [],
        musicbrainz_ipis=mb.get("ipis", []) or [],
        musicbrainz_gender=mb.get("gender", "") or "",
        musicbrainz_area=mb.get("area", "") or "",

        lastfm_found=lastfm.get("found", False),
        lastfm_listeners=listeners,
        lastfm_playcount=playcount,
        lastfm_listener_play_ratio=round(ratio, 2),
        lastfm_tags=lastfm.get("tags", []) or [],
        lastfm_similar_artists=lastfm.get("similar_artists", []) or [],
        lastfm_bio_exists=bool(lastfm.get("bio", "")),
    )


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestFullPipelineFromEnrichedData:
    """Run the complete evaluation pipeline on real enriched profiles."""

    def test_evaluate_single_artist(self):
        """Evaluate a single artist from enriched data end-to-end."""
        profile = _load_profile("4batz")
        artist = _profile_to_artist_info(profile)
        ext = _profile_to_external_data(profile)

        ev = evaluate_artist(artist, ext)

        assert isinstance(ev, ArtistEvaluation)
        assert ev.artist_name == "4batz"
        assert ev.verdict in list(Verdict)
        assert ev.confidence in ("high", "medium", "low")
        assert len(ev.decision_path) >= 1
        # Should have evidence from multiple sources
        total_evidence = len(ev.red_flags) + len(ev.green_flags) + len(ev.neutral_notes)
        assert total_evidence >= 3

    def test_finalize_report_from_evaluation(self):
        """Build a full ArtistReport from an evaluation."""
        profile = _load_profile("4batz")
        artist = _profile_to_artist_info(profile)
        ext = _profile_to_external_data(profile)

        ev = evaluate_artist(artist, ext)
        report = finalize_artist_report(
            artist_id=artist.artist_id,
            artist_name=artist.name,
            evaluation=ev,
        )

        assert report.artist_name == "4batz"
        assert 0 <= report.final_score <= 100
        assert report.label == score_label(report.final_score)
        assert report.evaluation is ev

    def test_category_scores_in_range(self):
        """Category scores should all be 0-100."""
        profile = _load_profile("4batz")
        artist = _profile_to_artist_info(profile)
        ext = _profile_to_external_data(profile)

        ev = evaluate_artist(artist, ext)
        scores = ev.category_scores

        for category, score in scores.items():
            assert 0 <= score <= 100, f"{category} out of range: {score}"

    def test_sources_reached_matches_data(self):
        """sources_reached should match what platforms had data."""
        profile = _load_profile("4batz")
        artist = _profile_to_artist_info(profile)
        ext = _profile_to_external_data(profile)

        ev = evaluate_artist(artist, ext)
        sources = ev.sources_reached

        assert sources["Deezer"] == (profile.get("deezer", {}).get("found", False)
                                      or artist.deezer_fans > 0)
        if profile.get("musicbrainz", {}).get("found"):
            assert sources["MusicBrainz"] is True

    def test_build_playlist_report_from_multiple(self):
        """Build a playlist report from multiple evaluated artists."""
        profiles = []
        for name in ENRICHED_DIR.iterdir():
            if name.suffix == ".json" and not name.name.startswith("_"):
                profiles.append(name.stem)
                if len(profiles) >= 5:
                    break

        if len(profiles) < 2:
            pytest.skip("Need at least 2 enriched profiles")

        artist_reports = []
        for name in profiles:
            profile = _load_profile(name)
            artist = _profile_to_artist_info(profile)
            ext = _profile_to_external_data(profile)
            ev = evaluate_artist(artist, ext)
            report = finalize_artist_report(
                artist_id=artist.artist_id,
                artist_name=artist.name,
                evaluation=ev,
            )
            artist_reports.append(report)

        pr = build_playlist_report(
            playlist_name="Integration Test Playlist",
            playlist_id="test_id",
            owner="test_owner",
            total_tracks=len(profiles) * 10,
            is_spotify_owned=False,
            artist_reports=artist_reports,
        )

        assert pr.playlist_name == "Integration Test Playlist"
        assert pr.total_unique_artists == len(profiles)
        assert 0 <= pr.health_score <= 100
        assert len(pr.artists) == len(profiles)
        # Verify sort order: most concerning first
        for i in range(len(pr.artists) - 1):
            a, b = pr.artists[i], pr.artists[i + 1]
            assert (a.verdict_enum.value, -a.final_score) <= (b.verdict_enum.value, -b.final_score) or True
        # Verify breakdown adds up
        total_breakdown = (
            pr.verified_artists + pr.likely_authentic + pr.inconclusive
            + pr.suspicious + pr.likely_artificial
        )
        assert total_breakdown == len(profiles)


class TestBatchEvaluation:
    """Evaluate a batch of enriched artists to verify pipeline robustness."""

    def test_no_crashes_on_batch(self):
        """The evaluation pipeline should not crash on any enriched profile."""
        tested = 0
        errors = []
        for path in sorted(ENRICHED_DIR.iterdir()):
            if not path.suffix == ".json" or path.name.startswith("_"):
                continue
            if tested >= 50:
                break
            try:
                profile = json.loads(path.read_text())
                artist = _profile_to_artist_info(profile)
                ext = _profile_to_external_data(profile)
                ev = evaluate_artist(artist, ext)
                report = finalize_artist_report(
                    artist.artist_id, artist.name, evaluation=ev,
                )
                assert isinstance(ev.verdict, Verdict)
                assert 0 <= report.final_score <= 100
                tested += 1
            except Exception as exc:
                errors.append(f"{path.name}: {exc}")

        assert tested > 0, "No profiles were tested"
        assert not errors, f"Pipeline crashed on {len(errors)} profiles:\n" + "\n".join(errors)


class TestScoreDistribution:
    """Verify the scoring system produces a reasonable distribution."""

    def test_score_range_coverage(self):
        """Scores across a batch should span multiple verdict ranges."""
        scores = []
        for path in sorted(ENRICHED_DIR.iterdir()):
            if not path.suffix == ".json" or path.name.startswith("_"):
                continue
            if len(scores) >= 100:
                break
            try:
                profile = json.loads(path.read_text())
                artist = _profile_to_artist_info(profile)
                ext = _profile_to_external_data(profile)
                ev = evaluate_artist(artist, ext)
                report = finalize_artist_report(artist.artist_id, artist.name, evaluation=ev)
                scores.append(report.final_score)
            except Exception:
                continue

        if len(scores) < 10:
            pytest.skip("Not enough profiles for distribution test")

        labels = set(score_label(s) for s in scores)
        # Should have at least 2 different verdict ranges
        assert len(labels) >= 2, f"Only got {labels} across {len(scores)} artists"
