"""Shared test fixtures for spotify-audit tests."""

from __future__ import annotations

import pytest

from spotify_audit.spotify_client import ArtistInfo
from spotify_audit.evidence import ExternalData


@pytest.fixture
def legitimate_artist() -> ArtistInfo:
    """A well-known legitimate artist with strong signals."""
    return ArtistInfo(
        artist_id="abc123",
        name="Radiohead",
        genres=["alternative rock", "art rock", "experimental rock"],
        followers=50_000,
        monthly_listeners=1_000_000,
        popularity=75,
        verified=True,
        image_url="https://example.com/img.jpg",
        image_width=640,
        image_height=640,
        album_count=9,
        single_count=5,
        total_tracks=120,
        release_dates=[
            "1993-02-22", "1995-03-13", "1997-06-16", "2000-10-02",
            "2003-06-09", "2007-10-10", "2011-02-18", "2016-05-08",
            "2024-01-15",
        ],
        track_durations=[240000, 280000, 210000, 310000, 265000,
                         195000, 350000, 220000, 290000, 230000],
        top_track_popularities=[80, 75, 70, 65, 60],
        labels=["XL Recordings", "Parlophone"],
        track_titles=["Creep", "Karma Police", "No Surprises",
                       "Everything In Its Right Place", "Paranoid Android"],
        track_ranks=[800000, 750000, 700000, 650000, 600000],
        has_explicit=True,
        contributors=["Thom Yorke", "Jonny Greenwood", "Ed O'Brien",
                       "Colin Greenwood", "Philip Selway"],
        related_artist_names=["Muse", "Portishead", "Massive Attack",
                               "Blur", "Pixies"],
        deezer_fans=500_000,
    )


@pytest.fixture
def ghost_artist() -> ArtistInfo:
    """A suspicious PFC-style ghost artist."""
    return ArtistInfo(
        artist_id="ghost001",
        name="Gentle Morning Waves",
        genres=[],
        followers=12,
        monthly_listeners=50_000,
        popularity=25,
        verified=False,
        album_count=0,
        single_count=45,
        total_tracks=45,
        release_dates=[
            "2024-01-01", "2024-01-05", "2024-01-10", "2024-01-15",
            "2024-01-20", "2024-01-25", "2024-02-01", "2024-02-05",
            "2024-02-10", "2024-02-15", "2024-02-20", "2024-02-25",
            "2024-03-01", "2024-03-05", "2024-03-10", "2024-03-15",
        ],
        track_durations=[62000, 65000, 61000, 63000, 64000,
                         62000, 60000, 63000, 61000, 64000],
        top_track_popularities=[20, 18, 15, 12, 10],
        labels=[],
        track_titles=["Morning Calm", "Peaceful Rain", "Gentle Breeze",
                       "Soft Light", "Quiet Dawn", "Serene Flow",
                       "Tranquil Night", "Dreamy Clouds"],
        track_ranks=[1000, 800, 500, 300, 200],
        deezer_fans=5,
    )


@pytest.fixture
def pfc_artist() -> ArtistInfo:
    """An artist with PFC distributor label."""
    return ArtistInfo(
        artist_id="pfc001",
        name="Ambient Dreamer",
        genres=[],
        followers=8,
        monthly_listeners=30_000,
        popularity=15,
        album_count=0,
        single_count=30,
        total_tracks=30,
        release_dates=[
            "2024-01-01", "2024-01-03", "2024-01-06", "2024-01-09",
            "2024-01-12", "2024-01-15", "2024-01-18", "2024-01-21",
            "2024-01-24", "2024-01-27", "2024-02-01", "2024-02-04",
        ],
        track_durations=[45000, 48000, 46000, 47000, 44000,
                         46000, 45000, 47000, 44000, 48000],
        labels=["Firefly Entertainment"],
        track_titles=["Rain Whisper", "Sleep Meditation", "Forest Solitude",
                       "Ocean Drift", "Calm Reflection"],
        track_ranks=[200, 150, 100, 50, 30],
        deezer_fans=2,
    )


@pytest.fixture
def rich_external_data() -> ExternalData:
    """External API data for a well-known, legitimate artist."""
    return ExternalData(
        genius_found=True,
        genius_song_count=45,
        genius_description="Radiohead are an English rock band formed in 1985...",
        genius_facebook_name="radiohead",
        genius_instagram_name="radiohead",
        genius_twitter_name="radiohead",
        genius_is_verified=True,
        genius_followers_count=5000,
        discogs_found=True,
        discogs_physical_releases=50,
        discogs_digital_releases=10,
        discogs_total_releases=60,
        discogs_formats=["Vinyl", "CD", "Cassette"],
        discogs_labels=["XL Recordings", "Parlophone"],
        discogs_profile="Radiohead are an English rock band formed in Abingdon, Oxfordshire, in 1985...",
        discogs_realname="",
        discogs_members=["Thom Yorke", "Jonny Greenwood", "Ed O'Brien",
                          "Colin Greenwood", "Philip Selway"],
        discogs_data_quality="Correct",
        setlistfm_found=True,
        setlistfm_total_shows=500,
        setlistfm_first_show="1992-10-22",
        setlistfm_last_show="2024-06-15",
        setlistfm_venues=["Madison Square Garden", "Glastonbury Festival"],
        setlistfm_venue_cities=["New York", "London", "Tokyo", "Paris", "Berlin"],
        setlistfm_venue_countries=["United States", "United Kingdom", "Japan",
                                     "France", "Germany", "Australia"],
        setlistfm_tour_names=["A Moon Shaped Pool Tour", "In Rainbows Tour"],
        musicbrainz_found=True,
        musicbrainz_type="Group",
        musicbrainz_country="GB",
        musicbrainz_begin_date="1985",
        musicbrainz_labels=["XL Recordings", "Parlophone"],
        musicbrainz_urls={
            "official homepage": "https://radiohead.com",
            "wikipedia": "https://en.wikipedia.org/wiki/Radiohead",
        },
        musicbrainz_genres=["alternative rock", "art rock"],
        musicbrainz_isnis=["0000000121268987"],
        musicbrainz_ipis=["00123456789"],
        lastfm_found=True,
        lastfm_listeners=3_000_000,
        lastfm_playcount=500_000_000,
        lastfm_listener_play_ratio=166.7,
        lastfm_tags=["alternative", "rock", "radiohead"],
        lastfm_bio_exists=True,
    )


@pytest.fixture
def empty_external_data() -> ExternalData:
    """External data for an artist not found on any platform."""
    return ExternalData()
