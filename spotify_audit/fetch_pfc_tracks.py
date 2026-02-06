#!/usr/bin/env python3
"""
Fetch all tracks from PFC-documented Spotify playlists.

Reads playlist IDs from pfc_playlist_ids.json, scrapes each playlist
via SpotifyScraper, and writes all tracks to data/pfc_playlist_tracks.json.

Usage:
    python -m spotify_audit.fetch_pfc_tracks [--delay 3] [--output data/pfc_playlist_tracks.json]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from spotify_scraper import SpotifyClient as _ScraperClient
from spotify_scraper.core.exceptions import SpotifyScraperError

logger = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_DIR / "data"
PLAYLIST_IDS_FILE = DATA_DIR / "pfc_playlist_ids.json"
DEFAULT_OUTPUT = DATA_DIR / "pfc_playlist_tracks.json"


def fetch_playlist_tracks(scraper: _ScraperClient, playlist_id: str) -> dict | None:
    """Fetch a single playlist's metadata and tracks."""
    url = f"https://open.spotify.com/playlist/{playlist_id}"
    try:
        raw = scraper.get_playlist_info(url)
    except (SpotifyScraperError, Exception) as exc:
        logger.error("Failed to fetch playlist %s: %s", playlist_id, exc)
        return None

    tracks = []
    for t in raw.get("tracks", []):
        artist_data = t.get("artists", [])
        artists = []
        for a in artist_data:
            if isinstance(a, dict):
                artists.append({
                    "name": a.get("name", ""),
                    "id": a.get("id", ""),
                })
            elif isinstance(a, str):
                artists.append({"name": a, "id": ""})

        album = t.get("album", {})
        tracks.append({
            "track_name": t.get("name", ""),
            "track_id": t.get("id", ""),
            "duration_ms": t.get("duration_ms", 0),
            "popularity": t.get("popularity", 0),
            "explicit": t.get("explicit", False),
            "artists": artists,
            "album_name": album.get("name", "") if isinstance(album, dict) else str(album or ""),
            "album_type": album.get("album_type", "") if isinstance(album, dict) else "",
            "release_date": album.get("release_date", "") if isinstance(album, dict) else "",
        })

    return {
        "playlist_id": playlist_id,
        "playlist_name": raw.get("name", ""),
        "owner": raw.get("owner", ""),
        "description": raw.get("description", ""),
        "followers": raw.get("followers", 0),
        "track_count": len(tracks),
        "tracks": tracks,
    }


def main():
    parser = argparse.ArgumentParser(description="Fetch PFC playlist tracks")
    parser.add_argument("--delay", type=float, default=3.0,
                        help="Seconds between playlist fetches (default: 3)")
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT),
                        help="Output JSON file path")
    parser.add_argument("--ids-file", type=str, default=str(PLAYLIST_IDS_FILE),
                        help="Path to playlist IDs JSON file")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    ids_path = Path(args.ids_file)
    if not ids_path.exists():
        logger.error("Playlist IDs file not found: %s", ids_path)
        logger.error("Create %s with format: {\"Playlist Name\": \"spotify_id\", ...}", ids_path)
        sys.exit(1)

    with open(ids_path) as f:
        playlist_ids: dict[str, str] = json.load(f)

    logger.info("Loaded %d playlist IDs from %s", len(playlist_ids), ids_path)

    # Load existing results to allow resuming
    output_path = Path(args.output)
    existing: dict[str, dict] = {}
    if output_path.exists():
        with open(output_path) as f:
            data = json.load(f)
            for p in data.get("playlists", []):
                existing[p["playlist_id"]] = p
        logger.info("Loaded %d existing playlists from %s", len(existing), output_path)

    scraper = _ScraperClient()
    results = list(existing.values())
    fetched_ids = {p["playlist_id"] for p in results}

    try:
        for i, (name, pid) in enumerate(playlist_ids.items()):
            if pid in fetched_ids:
                logger.info("[%d/%d] Skipping %s (already fetched)",
                            i + 1, len(playlist_ids), name)
                continue

            logger.info("[%d/%d] Fetching: %s (%s)",
                        i + 1, len(playlist_ids), name, pid)
            playlist_data = fetch_playlist_tracks(scraper, pid)

            if playlist_data:
                results.append(playlist_data)
                fetched_ids.add(pid)
                logger.info("  -> %d tracks", playlist_data["track_count"])
            else:
                logger.warning("  -> FAILED")

            # Save after each playlist (allows resuming)
            _save_results(results, output_path)

            if i < len(playlist_ids) - 1:
                time.sleep(args.delay)
    except KeyboardInterrupt:
        logger.info("\nInterrupted. Saving progress...")
    finally:
        scraper.close()
        _save_results(results, output_path)

    # Print summary
    all_artists = set()
    all_tracks = 0
    for p in results:
        all_tracks += p["track_count"]
        for t in p["tracks"]:
            for a in t["artists"]:
                if a["name"]:
                    all_artists.add(a["name"])

    logger.info("\n=== Summary ===")
    logger.info("Playlists fetched: %d / %d", len(results), len(playlist_ids))
    logger.info("Total tracks: %d", all_tracks)
    logger.info("Unique artists: %d", len(all_artists))
    logger.info("Output: %s", output_path)


def _save_results(results: list[dict], output_path: Path):
    """Save results to JSON with summary stats."""
    all_artists = set()
    all_tracks = 0
    for p in results:
        all_tracks += p["track_count"]
        for t in p["tracks"]:
            for a in t["artists"]:
                if a["name"]:
                    all_artists.add(a["name"])

    output = {
        "metadata": {
            "playlists_fetched": len(results),
            "total_tracks": all_tracks,
            "unique_artists": len(all_artists),
        },
        "playlists": results,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
