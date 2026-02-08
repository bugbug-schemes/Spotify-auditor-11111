#!/usr/bin/env python3
"""
Phase 3: Pattern Mining & Signal Discovery

Statistical analysis across the full corpus to discover patterns that
distinguish PFC from authentic music. Produces naming analysis, temporal
patterns, cross-platform presence matrix, and label cluster analysis.

Usage:
    python scripts/03_mine.py

Reads from:
    data/enriched/   - Enriched artist profiles
    data/entities/   - Entity graph from Phase 2

Outputs to:
    data/patterns/   - naming_analysis.json, temporal_patterns.json,
                       platform_profiles.json, label_clusters.json
"""

from __future__ import annotations

import json
import logging
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, stdev

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("mine")

DATA_DIR = PROJECT_ROOT / "data"
ENRICHED_DIR = DATA_DIR / "enriched"
ENTITIES_DIR = DATA_DIR / "entities"
PATTERNS_DIR = DATA_DIR / "patterns"

# Outputs
NAMING_FILE = PATTERNS_DIR / "naming_analysis.json"
TEMPORAL_FILE = PATTERNS_DIR / "temporal_patterns.json"
PLATFORM_FILE = PATTERNS_DIR / "platform_profiles.json"
LABEL_CLUSTERS_FILE = PATTERNS_DIR / "label_clusters.json"
MINING_SUMMARY_FILE = PATTERNS_DIR / "_mining_summary.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_stdev(values: list[float]) -> float:
    """Standard deviation, returns 0.0 for <2 values."""
    if len(values) < 2:
        return 0.0
    return stdev(values)


def _coefficient_of_variation(values: list[float]) -> float:
    """CV = stdev / mean. Returns 0.0 if mean is 0 or <2 values."""
    if len(values) < 2:
        return 0.0
    m = mean(values)
    if m == 0:
        return 0.0
    return stdev(values) / m


def _histogram(values: list[float | int], bins: list[tuple[float, float]]) -> dict[str, int]:
    """Create a histogram with named bins."""
    result = {}
    for lo, hi in bins:
        label = f"{lo}-{hi}" if hi != float("inf") else f"{lo}+"
        result[label] = sum(1 for v in values if lo <= v < hi)
    return result


# ---------------------------------------------------------------------------
# 3.1 Naming Convention Analysis
# ---------------------------------------------------------------------------

# Consonant and vowel classification for phonemic analysis
VOWELS = set("aeiouáéíóúàèìòùäëïöüâêîôû")
SOFT_CONSONANTS = set("lrsnvmwyhj")  # "Soft" phonemes common in ambient aesthetics


def analyze_naming(profiles: list[dict]) -> dict:
    """Analyze naming conventions across the corpus."""
    names = [p.get("artist_name", "") for p in profiles if p.get("artist_name")]

    # Character count distribution
    char_counts = [len(n) for n in names]
    # Word count distribution
    word_counts = [len(n.split()) for n in names]

    # Name structure patterns
    patterns = Counter()
    for n in names:
        words = n.split()
        wc = len(words)
        if wc == 1:
            patterns["single_word"] += 1
        elif wc == 2:
            patterns["two_words"] += 1
        elif wc == 3:
            patterns["three_words"] += 1
        else:
            patterns["four_plus_words"] += 1

        # Check for "The X" pattern
        if n.lower().startswith("the "):
            patterns["the_prefix"] += 1
        # Check for "DJ X" pattern
        if n.lower().startswith("dj "):
            patterns["dj_prefix"] += 1

    # Phonemic analysis: ratio of soft consonants
    soft_ratios = []
    for n in names:
        letters = [c.lower() for c in n if c.isalpha()]
        if not letters:
            continue
        consonants = [c for c in letters if c not in VOWELS]
        if not consonants:
            soft_ratios.append(1.0)  # All vowels
            continue
        soft = sum(1 for c in consonants if c in SOFT_CONSONANTS)
        soft_ratios.append(soft / len(consonants))

    # Capitalization patterns
    cap_patterns = Counter()
    for n in names:
        if n == n.upper():
            cap_patterns["ALL_CAPS"] += 1
        elif n == n.lower():
            cap_patterns["all_lower"] += 1
        elif n == n.title():
            cap_patterns["Title_Case"] += 1
        else:
            cap_patterns["Mixed"] += 1

    # Name collision: how many names have duplicates in the corpus
    name_lower = [n.lower() for n in names]
    collision_counter = Counter(name_lower)
    duplicates = {name: count for name, count in collision_counter.items() if count > 1}

    # Character count histogram
    char_bins = [(1, 5), (5, 10), (10, 15), (15, 20), (20, 25), (25, 30), (30, float("inf"))]
    char_histogram = _histogram(char_counts, char_bins)

    # Word count histogram
    word_bins = [(1, 2), (2, 3), (3, 4), (4, float("inf"))]
    word_histogram = _histogram(word_counts, word_bins)

    return {
        "total_names": len(names),
        "character_count": {
            "mean": round(mean(char_counts), 1) if char_counts else 0,
            "median": median(char_counts) if char_counts else 0,
            "stdev": round(_safe_stdev([float(x) for x in char_counts]), 1),
            "min": min(char_counts) if char_counts else 0,
            "max": max(char_counts) if char_counts else 0,
            "histogram": char_histogram,
        },
        "word_count": {
            "mean": round(mean(word_counts), 1) if word_counts else 0,
            "median": median(word_counts) if word_counts else 0,
            "distribution": dict(Counter(word_counts).most_common()),
            "histogram": word_histogram,
        },
        "name_structure": dict(patterns.most_common()),
        "capitalization": dict(cap_patterns.most_common()),
        "phonemic_analysis": {
            "soft_consonant_ratio_mean": round(mean(soft_ratios), 3) if soft_ratios else 0,
            "soft_consonant_ratio_median": round(median(soft_ratios), 3) if soft_ratios else 0,
            "interpretation": "Higher soft consonant ratios suggest names designed to evoke "
                             "calm/ambient aesthetics (l, r, s, n, v sounds).",
        },
        "name_collisions": {
            "duplicate_count": len(duplicates),
            "duplicates": duplicates,
        },
    }


# ---------------------------------------------------------------------------
# 3.2 Release Pattern Analysis
# ---------------------------------------------------------------------------

def _parse_date(date_str: str) -> datetime | None:
    """Try to parse a date string in common formats."""
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def analyze_temporal(profiles: list[dict]) -> dict:
    """Analyze release timing patterns across the corpus."""

    # Per-artist release data
    cadences = []  # Days between consecutive releases per artist
    career_lifespans = []  # Days from first to last release
    singles_ratios = []
    track_durations = []
    release_day_of_week = Counter()
    burst_artists = []  # Artists with 10+ releases in one month
    releases_per_month = defaultdict(Counter)  # artist -> {YYYY-MM: count}
    all_release_dates = []

    for profile in profiles:
        artist_name = profile.get("artist_name", "")
        dz = profile.get("deezer", {})
        if not dz.get("found"):
            continue

        albums = dz.get("albums", [])
        if not isinstance(albums, list):
            continue

        # Parse release dates
        dates = []
        album_types = []
        for album in albums:
            if not isinstance(album, dict):
                continue
            dt = _parse_date(album.get("release_date", ""))
            if dt:
                dates.append(dt)
                all_release_dates.append(dt)
                release_day_of_week[dt.strftime("%A")] += 1
                ym = dt.strftime("%Y-%m")
                releases_per_month[artist_name][ym] += 1
            atype = album.get("type", "").lower()
            if atype:
                album_types.append(atype)

        # Career lifespan
        if len(dates) >= 2:
            dates_sorted = sorted(dates)
            lifespan = (dates_sorted[-1] - dates_sorted[0]).days
            career_lifespans.append(lifespan)

            # Release cadence (days between consecutive releases)
            intervals = []
            for i in range(1, len(dates_sorted)):
                interval = (dates_sorted[i] - dates_sorted[i - 1]).days
                if interval > 0:
                    intervals.append(interval)
            if intervals:
                cadences.append({
                    "artist": artist_name,
                    "mean_interval": round(mean(intervals), 1),
                    "cv": round(_coefficient_of_variation([float(x) for x in intervals]), 3),
                    "num_releases": len(dates_sorted),
                })

        # Singles ratio
        if album_types:
            single_count = sum(1 for t in album_types if t in ("single", "ep"))
            album_count = sum(1 for t in album_types if t == "album")
            total = single_count + album_count
            if total > 0:
                singles_ratios.append(single_count / total)

        # Track durations
        durations = dz.get("track_durations", [])
        if isinstance(durations, list):
            for d in durations:
                if isinstance(d, (int, float)) and d > 0:
                    track_durations.append(d)

        # Burst detection: 10+ releases in any single month
        for ym, count in releases_per_month.get(artist_name, {}).items():
            if count >= 10:
                burst_artists.append({
                    "artist": artist_name,
                    "month": ym,
                    "release_count": count,
                })

    # Cadence analysis: separate low-CV (regular) from high-CV (irregular)
    cv_values = [c["cv"] for c in cadences if c["cv"] > 0]
    low_cv_artists = [c for c in cadences if c["cv"] < 0.3]  # Very regular
    high_cv_artists = [c for c in cadences if c["cv"] > 1.0]  # Very irregular

    # Duration clustering
    duration_bins = [
        (0, 60), (60, 120), (120, 180), (180, 240), (240, 300), (300, float("inf")),
    ]
    duration_histogram = _histogram(track_durations, duration_bins)

    # Career lifespan histogram (in days)
    lifespan_bins = [
        (0, 90), (90, 365), (365, 730), (730, 1825), (1825, float("inf")),
    ]
    lifespan_labels = {
        "0-90": "< 3 months",
        "90-365": "3-12 months",
        "365-730": "1-2 years",
        "730-1825": "2-5 years",
        "1825+": "5+ years",
    }
    lifespan_histogram = _histogram(career_lifespans, lifespan_bins)

    return {
        "total_artists_with_releases": len([c for c in cadences]),
        "release_cadence": {
            "artists_analyzed": len(cadences),
            "mean_interval_days": round(mean([c["mean_interval"] for c in cadences]), 1) if cadences else 0,
            "cv_mean": round(mean(cv_values), 3) if cv_values else 0,
            "cv_median": round(median(cv_values), 3) if cv_values else 0,
            "very_regular_cv_under_03": len(low_cv_artists),
            "very_irregular_cv_over_1": len(high_cv_artists),
            "interpretation": "Low CV (coefficient of variation) = suspiciously regular release schedule. "
                             "Industrial operations release on fixed 7/14/28-day cycles.",
        },
        "day_of_week": {
            "distribution": dict(release_day_of_week.most_common()),
            "friday_percentage": round(
                release_day_of_week.get("Friday", 0) / sum(release_day_of_week.values()) * 100, 1
            ) if sum(release_day_of_week.values()) > 0 else 0,
            "interpretation": "Friday clustering suggests alignment with Spotify editorial refresh cycle.",
        },
        "catalog_shape": {
            "singles_ratio_mean": round(mean(singles_ratios), 3) if singles_ratios else 0,
            "singles_ratio_median": round(median(singles_ratios), 3) if singles_ratios else 0,
            "high_singles_ratio_over_08": sum(1 for r in singles_ratios if r > 0.8),
            "interpretation": "PFC artists overwhelmingly release singles. "
                             "High singles ratio (>0.8) = mostly singles, few or no albums.",
        },
        "track_duration": {
            "total_tracks": len(track_durations),
            "mean_seconds": round(mean(track_durations), 1) if track_durations else 0,
            "median_seconds": round(median(track_durations), 1) if track_durations else 0,
            "histogram_seconds": duration_histogram,
            "interpretation": "PFC may cluster around payout-optimized durations (120-180 sec for ambient).",
        },
        "career_lifespan": {
            "artists_analyzed": len(career_lifespans),
            "mean_days": round(mean(career_lifespans), 0) if career_lifespans else 0,
            "median_days": round(median(career_lifespans), 0) if career_lifespans else 0,
            "histogram_days": lifespan_histogram,
            "lifespan_labels": lifespan_labels,
            "interpretation": "Short or fixed-length lifespans suggest manufactured artist identities.",
        },
        "burst_releases": {
            "artists_with_burst": len(set(b["artist"] for b in burst_artists)),
            "total_burst_events": len(burst_artists),
            "examples": burst_artists[:20],
            "interpretation": "10+ releases in a single month matches commissioned batch production.",
        },
    }


# ---------------------------------------------------------------------------
# 3.3 Cross-Platform Presence Matrix
# ---------------------------------------------------------------------------

PLATFORMS = ["musicbrainz", "deezer", "genius", "discogs", "setlistfm", "lastfm", "bandsintown"]


def analyze_platform_presence(profiles: list[dict]) -> dict:
    """Build cross-platform presence matrix and analyze patterns."""

    matrix_rows = []
    platform_counts = Counter()
    presence_patterns = Counter()  # fingerprint -> count

    for profile in profiles:
        artist_name = profile.get("artist_name", "")
        if not artist_name:
            continue

        row = {"artist": artist_name}
        fingerprint_parts = []
        for platform in PLATFORMS:
            found = profile.get(platform, {}).get("found", False)
            row[platform] = 1 if found else 0
            if found:
                platform_counts[platform] += 1
                fingerprint_parts.append(platform)

        row["platform_count"] = sum(row.get(p, 0) for p in PLATFORMS)
        fingerprint = "+".join(sorted(fingerprint_parts)) if fingerprint_parts else "NONE"
        row["fingerprint"] = fingerprint
        presence_patterns[fingerprint] += 1
        matrix_rows.append(row)

    total = len(matrix_rows)

    # Platform presence rates
    platform_rates = {}
    for p in PLATFORMS:
        count = platform_counts[p]
        platform_rates[p] = {
            "count": count,
            "percentage": round(count / total * 100, 1) if total > 0 else 0,
        }

    # Platform count distribution
    count_dist = Counter(r["platform_count"] for r in matrix_rows)

    # Top fingerprints
    top_fingerprints = [
        {"pattern": fp, "count": cnt, "percentage": round(cnt / total * 100, 1)}
        for fp, cnt in presence_patterns.most_common(20)
    ]

    # Absence scoring: for each platform, what % of corpus is missing it
    absence_rates = {}
    for p in PLATFORMS:
        absent = total - platform_counts[p]
        absence_rates[p] = {
            "absent_count": absent,
            "absent_percentage": round(absent / total * 100, 1) if total > 0 else 0,
        }

    # Platform co-occurrence: which platforms tend to appear together
    cooccurrence = {}
    for p1 in PLATFORMS:
        for p2 in PLATFORMS:
            if p1 >= p2:
                continue
            both = sum(1 for r in matrix_rows if r.get(p1, 0) == 1 and r.get(p2, 0) == 1)
            if both > 0:
                key = f"{p1}+{p2}"
                cooccurrence[key] = {
                    "count": both,
                    "percentage": round(both / total * 100, 1) if total > 0 else 0,
                }

    return {
        "total_artists": total,
        "platform_presence_rates": platform_rates,
        "platform_count_distribution": dict(sorted(count_dist.items())),
        "mean_platform_count": round(
            mean([r["platform_count"] for r in matrix_rows]), 2
        ) if matrix_rows else 0,
        "median_platform_count": median(
            [r["platform_count"] for r in matrix_rows]
        ) if matrix_rows else 0,
        "top_fingerprints": top_fingerprints,
        "absence_rates": dict(sorted(absence_rates.items(),
                                      key=lambda x: x[1]["absent_percentage"], reverse=True)),
        "platform_cooccurrence": dict(sorted(cooccurrence.items(),
                                              key=lambda x: x[1]["count"], reverse=True)),
        "interpretation": {
            "hypothesis": "PFC artists expected to show [deezer=1, lastfm=1, everything else=0] "
                         "because Deezer auto-ingests and Last.fm passively scrobbles.",
            "strongest_absence": "Discogs absence likely strongest legitimacy signal "
                                "(physical releases are near-impossible to fake).",
        },
    }


# ---------------------------------------------------------------------------
# 3.4 Label Network Analysis
# ---------------------------------------------------------------------------

def analyze_label_clusters(
    profiles: list[dict],
    labels_entity: dict | None = None,
) -> dict:
    """Analyze label concentration and clustering in the corpus."""

    # Load entity graph labels if available
    if labels_entity is None:
        labels_path = ENTITIES_DIR / "labels.json"
        if labels_path.exists():
            with open(labels_path) as f:
                labels_entity = json.load(f)
        else:
            labels_entity = {}

    # Extract label → artists from profiles directly
    label_artists: dict[str, set[str]] = defaultdict(set)
    artist_labels: dict[str, list[str]] = defaultdict(list)

    for profile in profiles:
        artist_name = profile.get("artist_name", "")
        if not artist_name:
            continue

        seen_labels = set()
        dz = profile.get("deezer", {})
        for album in dz.get("albums", []):
            if isinstance(album, dict):
                label = album.get("label", "")
                if isinstance(label, str) and label and label.lower() not in seen_labels:
                    seen_labels.add(label.lower())
                    label_artists[label].add(artist_name)
                    artist_labels[artist_name].append(label)

        # From Deezer top-level
        for label in dz.get("labels", []):
            if isinstance(label, str) and label and label.lower() not in seen_labels:
                seen_labels.add(label.lower())
                label_artists[label].add(artist_name)
                artist_labels[artist_name].append(label)

    total_artists = len([p for p in profiles if p.get("artist_name")])

    # Label concentration: what % of corpus is covered by top N labels
    sorted_labels = sorted(label_artists.items(), key=lambda x: len(x[1]), reverse=True)
    top_10_coverage = sum(len(artists) for _, artists in sorted_labels[:10])
    top_20_coverage = sum(len(artists) for _, artists in sorted_labels[:20])

    # Label size distribution
    label_sizes = [len(artists) for _, artists in sorted_labels]
    size_bins = [(1, 2), (2, 5), (5, 10), (10, 25), (25, 50), (50, float("inf"))]
    size_histogram = _histogram(label_sizes, size_bins)

    # Load PFC label blocklist for bad actor matching
    pfc_labels_path = PROJECT_ROOT / "spotify_audit" / "blocklists" / "pfc_distributors.json"
    pfc_labels = set()
    if pfc_labels_path.exists():
        try:
            with open(pfc_labels_path) as f:
                pfc_labels = set(n.lower() for n in json.load(f))
        except (json.JSONDecodeError, OSError):
            pass

    # Bad actor label matches
    bad_actor_matches = []
    for label, artists in sorted_labels:
        if label.lower() in pfc_labels:
            bad_actor_matches.append({
                "label": label,
                "artist_count": len(artists),
                "corpus_percentage": round(len(artists) / total_artists * 100, 2) if total_artists else 0,
            })

    # Label co-occurrence: which labels appear on the same playlists/artists
    label_cooccurrence: dict[str, int] = Counter()
    for artist, labels in artist_labels.items():
        unique_labels = list(set(labels))
        for i, l1 in enumerate(unique_labels):
            for l2 in unique_labels[i + 1:]:
                pair = tuple(sorted([l1, l2]))
                label_cooccurrence[f"{pair[0]} + {pair[1]}"] += 1

    top_cooccurrences = [
        {"pair": pair, "count": count}
        for pair, count in label_cooccurrence.most_common(20)
    ]

    # Top labels by artist count
    top_labels = [
        {
            "label": label,
            "artist_count": len(artists),
            "corpus_percentage": round(len(artists) / total_artists * 100, 2) if total_artists else 0,
            "known_bad_actor": label.lower() in pfc_labels,
            "sample_artists": sorted(list(artists))[:10],
        }
        for label, artists in sorted_labels[:30]
    ]

    return {
        "total_labels": len(sorted_labels),
        "total_artists_with_labels": sum(1 for a, labels in artist_labels.items() if labels),
        "concentration": {
            "top_10_labels_cover": top_10_coverage,
            "top_10_percentage": round(top_10_coverage / total_artists * 100, 1) if total_artists else 0,
            "top_20_labels_cover": top_20_coverage,
            "top_20_percentage": round(top_20_coverage / total_artists * 100, 1) if total_artists else 0,
            "interpretation": "If 10 labels cover 60%+ of 2,600 artists → extreme industrial concentration.",
        },
        "label_size_distribution": size_histogram,
        "top_labels": top_labels,
        "bad_actor_matches": bad_actor_matches,
        "label_cooccurrence": top_cooccurrences,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Load enriched profiles
    if not ENRICHED_DIR.exists():
        logger.error("Enriched directory not found: %s", ENRICHED_DIR)
        logger.error("Run 01_enrich.py first.")
        sys.exit(1)

    profile_files = sorted([
        f for f in ENRICHED_DIR.glob("*.json")
        if not f.name.startswith("_")
    ])

    if not profile_files:
        logger.error("No enriched profiles found in %s", ENRICHED_DIR)
        sys.exit(1)

    logger.info("Loading %d enriched profiles...", len(profile_files))
    profiles = []
    for f in profile_files:
        try:
            with open(f) as fh:
                profiles.append(json.load(fh))
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("Failed to load %s: %s", f.name, exc)

    logger.info("Loaded %d profiles", len(profiles))

    # Ensure output directory
    PATTERNS_DIR.mkdir(parents=True, exist_ok=True)

    # 3.1 Naming Analysis
    logger.info("Analyzing naming conventions...")
    naming = analyze_naming(profiles)
    with open(NAMING_FILE, "w") as f:
        json.dump(naming, f, indent=2, ensure_ascii=False)
    logger.info("  -> %d names analyzed, median %d chars, %d words",
                naming["total_names"],
                naming["character_count"]["median"],
                naming["word_count"]["median"])

    # 3.2 Temporal Pattern Analysis
    logger.info("Analyzing release patterns...")
    temporal = analyze_temporal(profiles)
    with open(TEMPORAL_FILE, "w") as f:
        json.dump(temporal, f, indent=2, ensure_ascii=False)
    logger.info("  -> %d artists with cadence data, %d burst events",
                temporal["release_cadence"]["artists_analyzed"],
                temporal["burst_releases"]["total_burst_events"])

    # 3.3 Cross-Platform Presence Matrix
    logger.info("Building cross-platform presence matrix...")
    platform = analyze_platform_presence(profiles)
    with open(PLATFORM_FILE, "w") as f:
        json.dump(platform, f, indent=2, ensure_ascii=False)
    logger.info("  -> Mean platform count: %.2f, top fingerprint: %s (%s)",
                platform["mean_platform_count"],
                platform["top_fingerprints"][0]["pattern"] if platform["top_fingerprints"] else "N/A",
                f"{platform['top_fingerprints'][0]['percentage']}%" if platform["top_fingerprints"] else "N/A")

    # 3.4 Label Cluster Analysis
    logger.info("Analyzing label clusters...")
    label_clusters = analyze_label_clusters(profiles)
    with open(LABEL_CLUSTERS_FILE, "w") as f:
        json.dump(label_clusters, f, indent=2, ensure_ascii=False)
    logger.info("  -> %d labels, top 10 cover %d artists (%.1f%%)",
                label_clusters["total_labels"],
                label_clusters["concentration"]["top_10_labels_cover"],
                label_clusters["concentration"]["top_10_percentage"])

    # Summary
    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "profiles_analyzed": len(profiles),
        "naming": {
            "total_names": naming["total_names"],
            "median_char_count": naming["character_count"]["median"],
            "median_word_count": naming["word_count"]["median"],
            "two_word_percentage": round(
                naming["name_structure"].get("two_words", 0) / naming["total_names"] * 100, 1
            ) if naming["total_names"] > 0 else 0,
        },
        "temporal": {
            "artists_with_releases": temporal["total_artists_with_releases"],
            "mean_cadence_cv": temporal["release_cadence"]["cv_mean"],
            "regular_cadence_artists": temporal["release_cadence"]["very_regular_cv_under_03"],
            "burst_artists": temporal["burst_releases"]["artists_with_burst"],
            "friday_release_pct": temporal["day_of_week"]["friday_percentage"],
        },
        "platform": {
            "mean_count": platform["mean_platform_count"],
            "median_count": platform["median_platform_count"],
            "top_fingerprint": platform["top_fingerprints"][0]["pattern"] if platform["top_fingerprints"] else "N/A",
        },
        "labels": {
            "total_labels": label_clusters["total_labels"],
            "top_10_coverage_pct": label_clusters["concentration"]["top_10_percentage"],
            "bad_actor_matches": len(label_clusters["bad_actor_matches"]),
        },
    }

    with open(MINING_SUMMARY_FILE, "w") as f:
        json.dump(summary, f, indent=2)

    logger.info("\n=== Phase 3: Pattern Mining Complete ===")
    logger.info("Profiles analyzed: %d", len(profiles))
    logger.info("Output: %s", PATTERNS_DIR)
    logger.info("")
    logger.info("Key findings:")
    logger.info("  Naming: %.0f%% two-word names, median %d chars",
                summary["naming"]["two_word_percentage"],
                summary["naming"]["median_char_count"])
    logger.info("  Temporal: %d artists with very regular cadence (CV < 0.3)",
                summary["temporal"]["regular_cadence_artists"])
    logger.info("  Platform: mean %.1f platforms per artist",
                summary["platform"]["mean_count"])
    logger.info("  Labels: top 10 cover %.1f%% of corpus, %d bad actor matches",
                summary["labels"]["top_10_coverage_pct"],
                summary["labels"]["bad_actor_matches"])


if __name__ == "__main__":
    main()
