#!/usr/bin/env python3
"""
Generate a cached demo report from enriched artist data.

Reads a sample of enriched artist JSON files from data/enriched/,
runs them through the evidence evaluator and scoring pipeline,
and writes both JSON and HTML cached reports to data/demo/.

Usage:
    python scripts/generate_demo_cache.py [--count 80]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from spotify_audit.spotify_client import ArtistInfo
from spotify_audit.evidence import (
    ExternalData,
    PlatformPresence,
    evaluate_artist,
)
from spotify_audit.scoring import (
    ArtistReport,
    PlaylistReport,
    build_playlist_report,
    finalize_artist_report,
)
from spotify_audit.reports.formatter import to_html, to_json


ENRICHED_DIR = PROJECT_ROOT / "data" / "enriched"
DEMO_DIR = PROJECT_ROOT / "data" / "demo"


def load_enriched(path: Path) -> dict:
    """Load an enriched artist JSON file."""
    with open(path) as f:
        return json.load(f)


def enriched_to_artist_info(data: dict) -> ArtistInfo:
    """Map enriched JSON to an ArtistInfo dataclass."""
    dz = data.get("deezer") or {}
    mb = data.get("musicbrainz") or {}

    # Build release dates from deezer albums
    release_dates = []
    albums = dz.get("albums") or []
    for alb in albums:
        rd = alb.get("release_date", "")
        if rd:
            release_dates.append(rd)

    album_types = dz.get("album_types") or {}

    return ArtistInfo(
        artist_id=data.get("artist_id", data.get("artist_name", "unknown")),
        name=data.get("artist_name", "Unknown"),
        genres=mb.get("genres", [])[:10],
        followers=dz.get("nb_fan", 0),
        monthly_listeners=0,
        popularity=0,
        verified=False,
        bio="",
        album_count=album_types.get("album", 0),
        single_count=album_types.get("single", 0),
        total_tracks=len(dz.get("track_titles", [])),
        release_dates=release_dates,
        track_durations=dz.get("track_durations", []),
        top_track_popularities=[],
        labels=mb.get("labels", [])[:20],
        track_titles=dz.get("track_titles", []),
        track_ranks=dz.get("track_ranks", []),
        has_explicit=dz.get("has_explicit", False),
        contributors=dz.get("contributors", []),
        contributor_roles=dz.get("contributor_roles", {}),
    )


def enriched_to_external_data(data: dict) -> ExternalData:
    """Map enriched JSON to an ExternalData dataclass."""
    mb = data.get("musicbrainz") or {}
    dz = data.get("deezer") or {}
    genius = data.get("genius") or {}
    discogs = data.get("discogs") or {}
    setlistfm = data.get("setlistfm") or {}
    lastfm = data.get("lastfm") or {}

    ext = ExternalData(
        artist_name=data.get("artist_name", ""),
        # MusicBrainz
        musicbrainz_found=mb.get("found", False),
        musicbrainz_type=mb.get("type", ""),
        musicbrainz_country=mb.get("country", ""),
        musicbrainz_begin_date=mb.get("begin_date", ""),
        musicbrainz_labels=mb.get("labels", [])[:20],
        musicbrainz_urls=mb.get("urls", {}),
        musicbrainz_genres=mb.get("genres", [])[:10],
        musicbrainz_aliases=mb.get("aliases", []),
        musicbrainz_isnis=mb.get("isnis", []),
        musicbrainz_ipis=mb.get("ipis", []),
        musicbrainz_gender=mb.get("gender", ""),
        musicbrainz_area=mb.get("area", ""),
        musicbrainz_relationship_count=len(mb.get("urls", {})),
        # Genius
        genius_found=genius.get("found", False),
        genius_song_count=genius.get("song_count", 0),
        genius_description=genius.get("description", ""),
        genius_facebook_name=genius.get("facebook_name", ""),
        genius_instagram_name=genius.get("instagram_name", ""),
        genius_twitter_name=genius.get("twitter_name", ""),
        genius_is_verified=genius.get("is_verified", False),
        genius_followers_count=genius.get("followers_count", 0),
        genius_alternate_names=genius.get("alternate_names", []),
        # Discogs
        discogs_found=discogs.get("found", False),
        discogs_physical_releases=discogs.get("physical_releases", 0),
        discogs_digital_releases=discogs.get("digital_releases", 0),
        discogs_total_releases=discogs.get("total_releases", 0),
        discogs_formats=discogs.get("formats", []),
        discogs_labels=discogs.get("labels", []),
        discogs_profile=discogs.get("profile", ""),
        discogs_realname=discogs.get("realname", ""),
        discogs_social_urls=discogs.get("social_urls", []),
        discogs_members=discogs.get("members", []),
        discogs_groups=discogs.get("groups", []),
        discogs_data_quality=discogs.get("data_quality", ""),
        # Setlist.fm
        setlistfm_found=setlistfm.get("found", False),
        setlistfm_total_shows=setlistfm.get("total_shows", 0),
        setlistfm_first_show=setlistfm.get("first_show", ""),
        setlistfm_last_show=setlistfm.get("last_show", ""),
        setlistfm_venues=setlistfm.get("venues", []),
        setlistfm_venue_cities=setlistfm.get("venue_cities", []),
        setlistfm_venue_countries=setlistfm.get("venue_countries", []),
        setlistfm_tour_names=setlistfm.get("tour_names", []),
        # Last.fm
        lastfm_found=lastfm.get("found", False),
        lastfm_listeners=lastfm.get("listeners", 0),
        lastfm_playcount=lastfm.get("playcount", 0),
        lastfm_listener_play_ratio=lastfm.get("listener_play_ratio", 0.0),
        lastfm_tags=lastfm.get("tags", []),
        lastfm_similar_artists=lastfm.get("similar_artists", []),
        lastfm_bio_exists=lastfm.get("bio_exists", False),
        # Deezer track ranks for display
        deezer_track_ranks=[
            {"title": t, "rank": r}
            for t, r in zip(
                dz.get("track_titles", [])[:10],
                dz.get("track_ranks", [])[:10],
            )
        ],
        # MusicBrainz enhanced URLs
        musicbrainz_youtube_url=mb.get("urls", {}).get("youtube", ""),
        musicbrainz_bandcamp_url=mb.get("urls", {}).get("bandcamp", ""),
        musicbrainz_official_website=mb.get("urls", {}).get("official homepage", ""),
    )

    # Extract social URLs from MusicBrainz URLs
    social_map = {}
    for key, url in mb.get("urls", {}).items():
        kl = key.lower()
        if "facebook" in kl or "instagram" in kl or "twitter" in kl or "tiktok" in kl:
            social_map[key] = url
    ext.musicbrainz_social_urls = social_map

    return ext


def select_artists(count: int) -> list[Path]:
    """Select a diverse sample of enriched artist files."""
    all_files = sorted(ENRICHED_DIR.glob("*.json"))
    # Exclude progress file
    all_files = [f for f in all_files if f.name != "_progress.json"]

    if len(all_files) <= count:
        return all_files

    # Pick a deterministic but diverse sample using seed
    rng = random.Random(42)
    # Ensure we get some well-known artists for demo quality
    priority_names = {
        "abba", "air", "adele", "radiohead", "coldplay", "daft_punk",
        "the_beatles", "beyonce", "taylor_swift", "drake", "eminem",
        "billie_eilish", "ed_sheeran", "arctic_monkeys", "nirvana",
        "david_bowie", "queen", "pink_floyd", "bob_marley", "bob_dylan",
        "elton_john", "madonna", "michael_jackson", "prince", "stevie_wonder",
        "kendrick_lamar", "kanye_west", "jay_z", "rihanna", "lady_gaga",
    }
    priority = [f for f in all_files if f.stem in priority_names]
    remaining = [f for f in all_files if f.stem not in priority_names]
    rng.shuffle(remaining)
    selected = priority + remaining[:count - len(priority)]
    return selected[:count]


def main():
    parser = argparse.ArgumentParser(description="Generate demo report cache")
    parser.add_argument("--count", type=int, default=80,
                        help="Number of artists to include (default: 80)")
    args = parser.parse_args()

    DEMO_DIR.mkdir(parents=True, exist_ok=True)

    files = select_artists(args.count)
    print(f"Selected {len(files)} enriched artist files")

    artist_reports = []
    skipped = []
    start = time.time()

    for i, path in enumerate(files):
        try:
            data = load_enriched(path)
            artist_info = enriched_to_artist_info(data)
            external_data = enriched_to_external_data(data)
            evaluation = evaluate_artist(artist_info, external_data)

            report = finalize_artist_report(
                artist_id=artist_info.artist_id,
                artist_name=artist_info.name,
                evaluation=evaluation,
            )
            artist_reports.append(report)

            if (i + 1) % 10 == 0:
                print(f"  Evaluated {i + 1}/{len(files)} artists...")
        except Exception as exc:
            skipped.append({
                "name": data.get("artist_name", path.stem),
                "reason": f"Evaluation error: {exc}",
            })

    elapsed = time.time() - start
    print(f"Evaluated {len(artist_reports)} artists in {elapsed:.1f}s "
          f"({len(skipped)} skipped)")

    # Build playlist report
    playlist_report = build_playlist_report(
        playlist_name="Demo Playlist — PFC Editorial Mix",
        playlist_id="demo-playlist-001",
        owner="Spotify Editorial",
        total_tracks=len(artist_reports) * 3,
        is_spotify_owned=True,
        artist_reports=artist_reports,
        skipped_artists=skipped,
    )
    playlist_report.scan_duration_seconds = elapsed
    playlist_report.api_source_counts = {
        "Deezer": len(artist_reports),
        "MusicBrainz": len(artist_reports),
        "Last.fm": sum(1 for a in artist_reports
                       if a.evaluation and a.evaluation.external_data
                       and a.evaluation.external_data.lastfm_found),
        "Genius": sum(1 for a in artist_reports
                      if a.evaluation and a.evaluation.external_data
                      and a.evaluation.external_data.genius_found),
        "Discogs": sum(1 for a in artist_reports
                       if a.evaluation and a.evaluation.external_data
                       and a.evaluation.external_data.discogs_found),
        "Setlist.fm": sum(1 for a in artist_reports
                          if a.evaluation and a.evaluation.external_data
                          and a.evaluation.external_data.setlistfm_found),
    }

    # Write JSON
    json_path = DEMO_DIR / "cached_report.json"
    json_path.write_text(to_json(playlist_report))
    print(f"Wrote {json_path} ({json_path.stat().st_size:,} bytes)")

    # Write HTML
    html_path = DEMO_DIR / "cached_report.html"
    html_path.write_text(to_html(playlist_report))
    print(f"Wrote {html_path} ({html_path.stat().st_size:,} bytes)")

    # Summary
    print(f"\nDemo report summary:")
    print(f"  Verified Artist:   {playlist_report.verified_artists}")
    print(f"  Likely Authentic:  {playlist_report.likely_authentic}")
    print(f"  Inconclusive:      {playlist_report.inconclusive}")
    print(f"  Suspicious:        {playlist_report.suspicious}")
    print(f"  Likely Artificial: {playlist_report.likely_artificial}")
    print(f"  Health Score:      {playlist_report.health_score}/100")


if __name__ == "__main__":
    main()
