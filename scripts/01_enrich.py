#!/usr/bin/env python3
"""
Phase 1: Data Collection & Entity Enrichment

Queries 7 APIs per seed artist and builds complete multi-platform profiles.
Resumable — checks for existing enriched files before querying.

Usage:
    python scripts/01_enrich.py [--limit 5] [--start 0]

Options:
    --limit N    Only process N artists (for testing)
    --start N    Start from artist index N (for batching)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

from spotify_audit.deezer_client import DeezerClient
from spotify_audit.musicbrainz_client import MusicBrainzClient
from spotify_audit.genius_client import GeniusClient
from spotify_audit.discogs_client import DiscogsClient
from spotify_audit.setlistfm_client import SetlistFmClient
from spotify_audit.bandsintown_client import BandsintownClient
from spotify_audit.lastfm_client import LastfmClient
from scripts.utils.rate_limiter import get_limiter, API_LIMITERS

logger = logging.getLogger("enrich")

DATA_DIR = PROJECT_ROOT / "data"
SEEDS_FILE = DATA_DIR / "seeds" / "artist_seeds.json"
ENRICHED_DIR = DATA_DIR / "enriched"
PROGRESS_FILE = ENRICHED_DIR / "_progress.json"
ERRORS_FILE = ENRICHED_DIR / "_errors.json"

MAX_RETRIES = 3


def _safe_id(name: str) -> str:
    """Create a filesystem-safe ID from an artist name."""
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in name)
    return safe.strip().replace(" ", "_").lower()[:80]


def _enriched_path(artist_name: str) -> Path:
    return ENRICHED_DIR / f"{_safe_id(artist_name)}.json"


def _is_enriched(artist_name: str) -> bool:
    """Check if an artist has already been fully enriched."""
    path = _enriched_path(artist_name)
    if not path.exists():
        return False
    try:
        with open(path) as f:
            data = json.load(f)
        # Check all 7 platforms have been attempted
        for platform in ["musicbrainz", "deezer", "genius", "discogs",
                         "setlistfm", "lastfm", "bandsintown"]:
            if platform not in data:
                return False
        return True
    except (json.JSONDecodeError, KeyError):
        return False


def _retry_call(fn, *args, api_name: str, **kwargs):
    """Call fn with retry logic. Returns result or None."""
    limiter = get_limiter(api_name)
    # Skip if circuit breaker tripped (API consistently failing)
    if limiter.is_tripped:
        return None
    for attempt in range(MAX_RETRIES):
        limiter.wait()
        try:
            result = fn(*args, **kwargs)
            limiter.success()
            return result
        except Exception as exc:
            is_429 = "429" in str(exc) or "rate" in str(exc).lower()
            limiter.error(is_rate_limit=is_429)
            if limiter.is_tripped:
                return None
            if attempt == MAX_RETRIES - 1:
                logger.debug("  %s: failed after %d retries: %s", api_name, MAX_RETRIES, exc)
                return None
    return None


def enrich_musicbrainz(mb: MusicBrainzClient, name: str) -> dict:
    """Query MusicBrainz for artist data."""
    result = {"found": False, "raw": None}
    artist = _retry_call(mb.search_artist, name, api_name="musicbrainz")
    if not artist:
        return result
    result["found"] = True
    artist = _retry_call(mb.enrich, artist, api_name="musicbrainz") or artist
    result.update({
        "mbid": artist.mbid,
        "type": artist.artist_type,
        "country": artist.country,
        "area": artist.area,
        "begin_date": artist.begin_date,
        "end_date": artist.end_date,
        "gender": artist.gender,
        "genres": artist.genres,
        "aliases": artist.aliases,
        "isnis": artist.isnis,
        "ipis": artist.ipis,
        "urls": artist.urls,
        "labels": artist.labels,
        "disambiguation_confidence": "high" if artist.mbid else "low",
    })
    return result


def enrich_deezer(dz: DeezerClient, name: str) -> dict:
    """Query Deezer for artist data."""
    result = {"found": False, "raw": None}
    artist = _retry_call(dz.search_artist, name, api_name="deezer")
    if not artist:
        return result
    # Basic name match check
    if name.lower() not in artist.name.lower() and artist.name.lower() not in name.lower():
        result["disambiguation_note"] = f"Name mismatch: searched '{name}', got '{artist.name}'"
        return result
    result["found"] = True
    artist = _retry_call(dz.enrich, artist, api_name="deezer") or artist
    result.update({
        "deezer_id": artist.deezer_id,
        "name": artist.name,
        "nb_fan": artist.nb_fan,
        "nb_album": artist.nb_album,
        "link": artist.link,
        "picture_url": artist.picture_url,
        "labels": artist.labels,
        "contributors": artist.contributors,
        "contributor_roles": artist.contributor_roles,
        "track_titles": artist.track_titles[:25],
        "track_durations": artist.track_durations[:25],
        "track_ranks": artist.track_ranks[:25],
        "has_explicit": artist.has_explicit,
        "album_types": artist.album_types,
        "albums": [
            {
                "title": a.get("title", ""),
                "label": a.get("label", {}).get("name", "") if isinstance(a.get("label"), dict) else str(a.get("label", "")),
                "release_date": a.get("release_date", ""),
                "nb_tracks": a.get("nb_tracks", 0),
                "type": a.get("record_type", a.get("type", "")),
            }
            for a in artist.albums[:30]
            if isinstance(a, dict)
        ],
        "related_artists": [
            r.get("name", "") for r in artist.related_artists[:20]
            if isinstance(r, dict) and r.get("name")
        ],
    })
    return result


def enrich_genius(genius: GeniusClient, name: str) -> dict:
    """Query Genius for artist data."""
    result = {"found": False, "raw": None}
    if not genius.enabled:
        result["status"] = "not_configured"
        return result
    artist = _retry_call(genius.search_artist, name, api_name="genius")
    if not artist:
        return result
    result["found"] = True
    artist = _retry_call(genius.enrich, artist, api_name="genius") or artist
    result.update({
        "genius_id": artist.genius_id,
        "name": artist.name,
        "image_url": artist.image_url,
        "song_count": artist.song_count,
        "description_snippet": artist.description_snippet,
        "facebook_name": artist.facebook_name,
        "instagram_name": artist.instagram_name,
        "twitter_name": artist.twitter_name,
        "is_verified": artist.is_verified,
        "followers_count": artist.followers_count,
        "alternate_names": artist.alternate_names,
    })
    return result


def enrich_discogs(discogs: DiscogsClient, name: str) -> dict:
    """Query Discogs for artist data."""
    result = {"found": False, "raw": None}
    artist = _retry_call(discogs.search_artist, name, api_name="discogs")
    if not artist:
        return result
    result["found"] = True
    artist = _retry_call(discogs.enrich, artist, api_name="discogs") or artist
    result.update({
        "discogs_id": artist.discogs_id,
        "name": artist.name,
        "profile": artist.profile,
        "realname": artist.realname,
        "social_urls": artist.social_urls,
        "members": artist.members,
        "groups": artist.groups,
        "data_quality": artist.data_quality,
        "physical_releases": artist.physical_releases,
        "digital_only_releases": artist.digital_only_releases,
        "total_releases": artist.total_releases,
        "formats": artist.formats,
        "labels": artist.labels,
    })
    return result


def enrich_setlistfm(setlistfm: SetlistFmClient, name: str, mbid: str = "") -> dict:
    """Query Setlist.fm for artist data."""
    result = {"found": False, "raw": None}
    if not setlistfm.enabled:
        result["status"] = "not_configured"
        return result
    artist = _retry_call(setlistfm.search_artist, name, api_name="setlistfm")
    if not artist:
        return result
    result["found"] = True
    artist = _retry_call(setlistfm.get_setlist_count, artist, api_name="setlistfm") or artist
    result.update({
        "total_setlists": artist.total_setlists,
        "first_show_date": artist.first_show_date,
        "last_show_date": artist.last_show_date,
        "top_venues": artist.top_venues,
        "venue_cities": artist.venue_cities,
        "venue_countries": artist.venue_countries,
        "tour_names": artist.tour_names,
    })
    return result


def enrich_lastfm(lastfm: LastfmClient, name: str) -> dict:
    """Query Last.fm for artist data."""
    result = {"found": False, "raw": None}
    if not lastfm.enabled:
        result["status"] = "not_configured"
        return result
    artist = _retry_call(lastfm.get_artist_info, name, api_name="lastfm")
    if not artist:
        return result
    result["found"] = True
    artist = _retry_call(lastfm.enrich, artist, api_name="lastfm") or artist
    # Compute listener-to-playcount ratio
    lp_ratio = (artist.playcount / artist.listeners) if artist.listeners > 0 else 0.0
    result.update({
        "name": artist.name,
        "mbid": artist.mbid,
        "listeners": artist.listeners,
        "playcount": artist.playcount,
        "listener_play_ratio": round(lp_ratio, 2),
        "bio_exists": bool(artist.bio and len(artist.bio) > 50),
        "bio_summary": artist.bio_summary[:500] if artist.bio_summary else "",
        "tags": artist.tags,
        "similar_artists": artist.similar_artists,
        "top_tracks": artist.top_tracks[:10],
        "url": artist.url,
    })
    return result


def enrich_bandsintown(bit: BandsintownClient, name: str) -> dict:
    """Query Bandsintown for artist data."""
    result = {"found": False, "raw": None}
    if not bit.enabled:
        result["status"] = "not_configured"
        return result
    artist = _retry_call(bit.get_artist, name, api_name="bandsintown")
    if not artist:
        return result
    result["found"] = True
    artist = _retry_call(bit.enrich, artist, api_name="bandsintown") or artist
    result.update({
        "tracker_count": artist.tracker_count,
        "past_events": artist.past_events,
        "upcoming_events": artist.upcoming_events,
        "facebook_page_url": artist.facebook_page_url,
        "social_links": artist.social_links,
        "on_tour": artist.on_tour,
    })
    return result


def enrich_artist(
    name: str,
    seed: dict,
    mb: MusicBrainzClient,
    dz: DeezerClient,
    genius: GeniusClient,
    discogs: DiscogsClient,
    setlistfm: SetlistFmClient,
    lastfm: LastfmClient,
    bit: BandsintownClient,
) -> dict:
    """Run all 7 API lookups for a single artist."""
    profile = {
        "artist_name": name,
        "artist_id": _safe_id(name),
        "seed_source": seed.get("seed_source", {}),
        "enrichment_timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Query in order per pipeline spec
    logger.debug("  [1/7] MusicBrainz...")
    profile["musicbrainz"] = enrich_musicbrainz(mb, name)

    logger.debug("  [2/7] Deezer...")
    profile["deezer"] = enrich_deezer(dz, name)

    logger.debug("  [3/7] Genius...")
    profile["genius"] = enrich_genius(genius, name)

    logger.debug("  [4/7] Discogs...")
    profile["discogs"] = enrich_discogs(discogs, name)

    # Use MBID from MusicBrainz for Setlist.fm if available
    mbid = profile["musicbrainz"].get("mbid", "")
    logger.debug("  [5/7] Setlist.fm...")
    profile["setlistfm"] = enrich_setlistfm(setlistfm, name, mbid=mbid)

    logger.debug("  [6/7] Last.fm...")
    profile["lastfm"] = enrich_lastfm(lastfm, name)

    logger.debug("  [7/7] Bandsintown...")
    profile["bandsintown"] = enrich_bandsintown(bit, name)

    # Compute summary
    platforms_found = []
    platforms_missing = []
    for p in ["musicbrainz", "deezer", "genius", "discogs", "setlistfm", "lastfm", "bandsintown"]:
        if profile[p].get("found", False):
            platforms_found.append(p)
        else:
            platforms_missing.append(p)

    profile["platforms_found"] = platforms_found
    profile["platforms_missing"] = platforms_missing
    profile["platform_count"] = len(platforms_found)

    return profile


def main():
    parser = argparse.ArgumentParser(description="Phase 1: Enrich seed artists")
    parser.add_argument("--limit", type=int, default=0, help="Max artists to process (0=all)")
    parser.add_argument("--start", type=int, default=0, help="Start index")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Load .env
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)

    # Load seeds
    if not SEEDS_FILE.exists():
        logger.error("Seeds file not found: %s", SEEDS_FILE)
        sys.exit(1)
    with open(SEEDS_FILE) as f:
        seeds = json.load(f)
    logger.info("Loaded %d seed artists", len(seeds))

    # Ensure output dir
    ENRICHED_DIR.mkdir(parents=True, exist_ok=True)

    # Initialize clients
    mb = MusicBrainzClient(delay=1.1)
    dz = DeezerClient(delay=0.15)
    genius = GeniusClient(access_token=os.getenv("GENIUS_TOKEN", ""), delay=0.25)
    discogs = DiscogsClient(token=os.getenv("DISCOGS_TOKEN", ""), delay=1.0)
    setlistfm = SetlistFmClient(api_key=os.getenv("SETLISTFM_API_KEY", ""), delay=0.6)
    lastfm = LastfmClient(api_key=os.getenv("LASTFM_API_KEY", ""), delay=0.25)
    bit = BandsintownClient(app_id=os.getenv("BANDSINTOWN_APP_ID", ""), delay=1.0)

    # Show configured APIs
    configured = ["MusicBrainz", "Deezer"]
    if genius.enabled:
        configured.append("Genius")
    configured.append("Discogs")  # always enabled
    if setlistfm.enabled:
        configured.append("Setlist.fm")
    if lastfm.enabled:
        configured.append("Last.fm")
    if bit.enabled:
        configured.append("Bandsintown")
    logger.info("APIs configured: %s", ", ".join(configured))

    # Process artists
    subset = seeds[args.start:]
    if args.limit:
        subset = subset[:args.limit]

    skipped = 0
    processed = 0
    errors_list = []
    start_time = time.time()

    for i, seed in enumerate(subset):
        idx = args.start + i
        name = seed["artist_name"]

        # Check if already enriched
        if _is_enriched(name):
            skipped += 1
            continue

        logger.info("[%d/%d] Enriching: %s", idx + 1, len(seeds), name)

        # Reset per-artist backoff so failed APIs don't accumulate wait time
        for limiter in API_LIMITERS.values():
            limiter.reset_for_new_artist()

        try:
            profile = enrich_artist(
                name, seed, mb, dz, genius, discogs, setlistfm, lastfm, bit,
            )

            # Save immediately
            out_path = _enriched_path(name)
            with open(out_path, "w") as f:
                json.dump(profile, f, indent=2, ensure_ascii=False)

            platforms = profile["platforms_found"]
            missing = 7 - len(platforms)
            logger.info(
                "  -> Found on: %s (%d missing)",
                ", ".join(platforms) if platforms else "NONE",
                missing,
            )
            processed += 1

        except Exception as exc:
            logger.error("  -> FAILED: %s", exc)
            errors_list.append({"artist": name, "error": str(exc), "index": idx})

        # Save progress every 10 artists
        if (processed + 1) % 10 == 0:
            _save_progress(processed, skipped, errors_list, start_time, len(seeds))

    # Final save
    _save_progress(processed, skipped, errors_list, start_time, len(seeds))

    # Save errors
    if errors_list:
        with open(ERRORS_FILE, "w") as f:
            json.dump(errors_list, f, indent=2)

    elapsed = time.time() - start_time
    logger.info("\n=== Enrichment Complete ===")
    logger.info("Processed: %d | Skipped (already done): %d | Errors: %d",
                processed, skipped, len(errors_list))
    logger.info("Time: %.1f minutes", elapsed / 60)
    logger.info("Output: %s", ENRICHED_DIR)


def _save_progress(processed: int, skipped: int, errors: list, start_time: float, total: int):
    """Save progress tracker."""
    progress = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_seeds": total,
        "processed_this_run": processed,
        "skipped_existing": skipped,
        "errors_this_run": len(errors),
        "elapsed_seconds": round(time.time() - start_time, 1),
    }
    # Count total enriched files
    if ENRICHED_DIR.exists():
        enriched_count = len([
            f for f in ENRICHED_DIR.glob("*.json")
            if not f.name.startswith("_")
        ])
        progress["total_enriched"] = enriched_count
        progress["remaining"] = total - enriched_count

    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)


if __name__ == "__main__":
    main()
