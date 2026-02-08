#!/usr/bin/env python3
"""
Phase 2: Entity Expansion — Building the Knowledge Graph

Reads enriched artist profiles from data/enriched/ and extracts entities
(producers, labels, distributors, co-writers, similar artists) to build
a knowledge graph mapping relationships across the PFC corpus.

Usage:
    python scripts/02_expand.py [--min-connections 5]

Options:
    --min-connections N   Flag entities with N+ artist connections (default 5)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("expand")

DATA_DIR = PROJECT_ROOT / "data"
ENRICHED_DIR = DATA_DIR / "enriched"
ENTITIES_DIR = DATA_DIR / "entities"
BLOCKLISTS_DIR = PROJECT_ROOT / "spotify_audit" / "blocklists"

# Output files
PRODUCERS_FILE = ENTITIES_DIR / "producers.json"
LABELS_FILE = ENTITIES_DIR / "labels.json"
DISTRIBUTORS_FILE = ENTITIES_DIR / "distributors.json"
COWRITERS_FILE = ENTITIES_DIR / "cowriters.json"
SIMILAR_ARTISTS_FILE = ENTITIES_DIR / "similar_artists.json"
NEW_BAD_ACTORS_FILE = ENTITIES_DIR / "new_bad_actors.json"
EXPANSION_SUMMARY_FILE = ENTITIES_DIR / "_expansion_summary.json"


# ---------------------------------------------------------------------------
# Blocklist loading
# ---------------------------------------------------------------------------

def _load_json_list(path: Path) -> list[str]:
    """Load a JSON list file, return empty list if missing."""
    if not path.exists():
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _load_blocklists() -> dict[str, set[str]]:
    """Load all blocklists for cross-referencing."""
    return {
        "known_ai_artists": set(
            n.lower() for n in _load_json_list(BLOCKLISTS_DIR / "known_ai_artists.json")
        ),
        "pfc_distributors": set(
            n.lower() for n in _load_json_list(BLOCKLISTS_DIR / "pfc_distributors.json")
        ),
        "pfc_songwriters": set(
            n.lower() for n in _load_json_list(BLOCKLISTS_DIR / "pfc_songwriters.json")
        ),
    }


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

_LABEL_SUFFIXES = re.compile(
    r"\s+(records?|music|entertainment|studios?|productions?|publishing|"
    r"group|ab|llc|inc|gmbh|ltd)\.?$",
    re.IGNORECASE,
)


def _normalize_label(name: str) -> str:
    """Normalize a label name for fuzzy matching.

    Strips common suffixes, trims whitespace, lowercases.
    """
    name = name.strip()
    if not name:
        return ""
    normalized = name.lower().strip()
    # Remove common suffixes for matching but keep original for display
    return normalized


def _normalize_label_key(name: str) -> str:
    """Create a fuzzy key for label deduplication."""
    key = name.lower().strip()
    key = _LABEL_SUFFIXES.sub("", key).strip()
    # Remove common noise
    key = key.replace(".", "").replace(",", "").replace("'", "").replace('"', '')
    return key


def _normalize_name(name: str) -> str:
    """Normalize a person/entity name."""
    return name.strip()


# ---------------------------------------------------------------------------
# Entity extraction from enriched profiles
# ---------------------------------------------------------------------------

def _extract_producers(profile: dict) -> list[tuple[str, str]]:
    """Extract producer/songwriter names from a profile.

    Returns list of (name, source) tuples.
    """
    producers = []
    artist_name = profile.get("artist_name", "")

    # From Deezer contributor_roles
    dz = profile.get("deezer", {})
    roles = dz.get("contributor_roles", {})
    if isinstance(roles, dict):
        for track_or_role, people in roles.items():
            if isinstance(people, list):
                for person in people:
                    if isinstance(person, str) and person.lower() != artist_name.lower():
                        producers.append((_normalize_name(person), "deezer_contributors"))
            elif isinstance(people, dict):
                # roles might be {role: [names]}
                for role, names in people.items():
                    if isinstance(names, list):
                        for person in names:
                            if isinstance(person, str) and person.lower() != artist_name.lower():
                                producers.append((_normalize_name(person), "deezer_contributors"))

    # From Deezer contributors list
    contributors = dz.get("contributors", [])
    if isinstance(contributors, list):
        for c in contributors:
            name = c if isinstance(c, str) else (c.get("name", "") if isinstance(c, dict) else "")
            if name and name.lower() != artist_name.lower():
                producers.append((_normalize_name(name), "deezer_contributors"))

    # From Genius (songwriter/producer credits would need song-level data,
    # but we have limited data from the search endpoint)
    genius = profile.get("genius", {})
    # Note: Full song credits require additional API calls in Phase 2 investigations

    # From MusicBrainz relations (if stored)
    mb = profile.get("musicbrainz", {})
    # MB URL relations might contain producer links but are stored as URLs

    return producers


def _extract_labels(profile: dict) -> list[tuple[str, str]]:
    """Extract label names from a profile.

    Returns list of (label_name, source) tuples.
    """
    labels = []
    seen = set()

    # From Deezer albums
    dz = profile.get("deezer", {})
    for album in dz.get("albums", []):
        if isinstance(album, dict):
            label = album.get("label", "")
            if isinstance(label, str) and label and label.lower() not in seen:
                seen.add(label.lower())
                labels.append((label.strip(), "deezer_albums"))

    # From Deezer top-level labels
    dz_labels = dz.get("labels", [])
    if isinstance(dz_labels, list):
        for label in dz_labels:
            if isinstance(label, str) and label and label.lower() not in seen:
                seen.add(label.lower())
                labels.append((label.strip(), "deezer_labels"))

    # From Discogs
    discogs = profile.get("discogs", {})
    disc_labels = discogs.get("labels", [])
    if isinstance(disc_labels, list):
        for label in disc_labels:
            if isinstance(label, str) and label and label.lower() not in seen:
                seen.add(label.lower())
                labels.append((label.strip(), "discogs_labels"))

    # From MusicBrainz
    mb = profile.get("musicbrainz", {})
    mb_labels = mb.get("labels", [])
    if isinstance(mb_labels, list):
        for label in mb_labels:
            if isinstance(label, str) and label and label.lower() not in seen:
                seen.add(label.lower())
                labels.append((label.strip(), "musicbrainz_labels"))

    return labels


def _extract_similar_artists(profile: dict) -> list[tuple[str, str]]:
    """Extract similar/related artist names.

    Returns list of (artist_name, source) tuples.
    """
    similar = []
    seen = set()

    # From Last.fm similar artists
    lastfm = profile.get("lastfm", {})
    for name in lastfm.get("similar_artists", []):
        if isinstance(name, str) and name and name.lower() not in seen:
            seen.add(name.lower())
            similar.append((name, "lastfm_similar"))

    # From Deezer related artists
    dz = profile.get("deezer", {})
    for name in dz.get("related_artists", []):
        if isinstance(name, str) and name and name.lower() not in seen:
            seen.add(name.lower())
            similar.append((name, "deezer_related"))

    return similar


# ---------------------------------------------------------------------------
# Graph building
# ---------------------------------------------------------------------------

def build_entity_graph(profiles: list[dict], blocklists: dict[str, set[str]]) -> dict:
    """Build the full entity graph from enriched profiles.

    Returns dict with producers, labels, distributors, cowriters, similar_artists graphs.
    """
    # Collect corpus artist names for overlap calculation
    corpus_names = set()
    for p in profiles:
        name = p.get("artist_name", "")
        if name:
            corpus_names.add(name.lower())
    corpus_size = len(corpus_names)

    # Entity accumulators: entity_name -> {artists: set, sources: set, ...}
    producer_graph: dict[str, dict] = defaultdict(lambda: {
        "artists": set(), "sources": set(),
    })
    label_graph: dict[str, dict] = defaultdict(lambda: {
        "artists": set(), "sources": set(), "raw_names": set(),
    })
    similar_graph: dict[str, dict] = defaultdict(lambda: {
        "connected_to": set(), "sources": set(), "in_corpus": False,
    })
    cowriter_edges: dict[str, set] = defaultdict(set)  # artist -> set of co-artists via shared producers

    # Process each profile
    for profile in profiles:
        artist_name = profile.get("artist_name", "")
        if not artist_name:
            continue

        # Extract producers
        for prod_name, source in _extract_producers(profile):
            key = prod_name.lower()
            producer_graph[prod_name]["artists"].add(artist_name)
            producer_graph[prod_name]["sources"].add(source)

        # Extract labels
        for label_name, source in _extract_labels(profile):
            # Use fuzzy key for grouping
            fuzzy_key = _normalize_label_key(label_name)
            if not fuzzy_key:
                continue
            label_graph[fuzzy_key]["artists"].add(artist_name)
            label_graph[fuzzy_key]["sources"].add(source)
            label_graph[fuzzy_key]["raw_names"].add(label_name)

        # Extract similar artists
        for sim_name, source in _extract_similar_artists(profile):
            similar_graph[sim_name]["connected_to"].add(artist_name)
            similar_graph[sim_name]["sources"].add(source)
            if sim_name.lower() in corpus_names:
                similar_graph[sim_name]["in_corpus"] = True

    # Build co-writer edges from shared producers
    for prod_name, data in producer_graph.items():
        artists = list(data["artists"])
        if len(artists) >= 2:
            for i, a1 in enumerate(artists):
                for a2 in artists[i + 1:]:
                    cowriter_edges[a1].add(a2)
                    cowriter_edges[a2].add(a1)

    return {
        "producers": producer_graph,
        "labels": label_graph,
        "similar": similar_graph,
        "cowriters": cowriter_edges,
        "corpus_names": corpus_names,
        "corpus_size": corpus_size,
    }


def _serialize_producers(
    producer_graph: dict,
    corpus_size: int,
    blocklists: dict[str, set[str]],
) -> dict:
    """Serialize producer graph to JSON-safe dict with metadata."""
    result = {}
    pfc_writers = blocklists.get("pfc_songwriters", set())

    for name, data in sorted(producer_graph.items(), key=lambda x: len(x[1]["artists"]), reverse=True):
        artist_count = len(data["artists"])
        corpus_pct = round(artist_count / corpus_size, 4) if corpus_size > 0 else 0
        is_known = name.lower() in pfc_writers

        result[name] = {
            "artist_count": artist_count,
            "artists": sorted(data["artists"]),
            "pfc_corpus_percentage": corpus_pct,
            "known_bad_actor": is_known,
            "sources": sorted(data["sources"]),
        }

    return result


def _serialize_labels(
    label_graph: dict,
    corpus_size: int,
    blocklists: dict[str, set[str]],
) -> dict:
    """Serialize label graph to JSON-safe dict with metadata."""
    result = {}
    pfc_labels = blocklists.get("pfc_distributors", set())

    for fuzzy_key, data in sorted(label_graph.items(), key=lambda x: len(x[1]["artists"]), reverse=True):
        artist_count = len(data["artists"])
        corpus_pct = round(artist_count / corpus_size, 4) if corpus_size > 0 else 0
        raw_names = sorted(data["raw_names"])
        display_name = raw_names[0] if raw_names else fuzzy_key

        # Check against blocklist (try both fuzzy key and all raw names)
        is_known = fuzzy_key in pfc_labels or any(
            rn.lower() in pfc_labels for rn in raw_names
        )

        result[display_name] = {
            "artist_count": artist_count,
            "artists": sorted(data["artists"]),
            "pfc_corpus_percentage": corpus_pct,
            "known_bad_actor": is_known,
            "all_name_variants": raw_names,
            "fuzzy_key": fuzzy_key,
            "sources": sorted(data["sources"]),
            "exclusivity_score": None,  # Filled in label investigation phase
        }

    return result


def _serialize_similar(
    similar_graph: dict,
    corpus_names: set[str],
    corpus_size: int,
) -> dict:
    """Serialize similar artist graph."""
    result = {}

    for name, data in sorted(similar_graph.items(), key=lambda x: len(x[1]["connected_to"]), reverse=True):
        connection_count = len(data["connected_to"])
        in_corpus = name.lower() in corpus_names

        result[name] = {
            "connection_count": connection_count,
            "connected_to": sorted(data["connected_to"]),
            "in_corpus": in_corpus,
            "sources": sorted(data["sources"]),
        }

    return result


def _serialize_cowriters(cowriter_edges: dict) -> dict:
    """Serialize co-writer network edges."""
    result = {}
    for artist, co_artists in sorted(cowriter_edges.items(), key=lambda x: len(x[1]), reverse=True):
        result[artist] = {
            "shared_producer_connections": len(co_artists),
            "connected_artists": sorted(co_artists),
        }
    return result


# ---------------------------------------------------------------------------
# Bad actor discovery
# ---------------------------------------------------------------------------

def find_new_bad_actors(
    producers: dict,
    labels: dict,
    blocklists: dict[str, set[str]],
    min_connections: int,
) -> dict:
    """Identify entities NOT in blocklists but with suspicious connectivity.

    An entity with 5+ artist connections that isn't already known is a
    potential new discovery.
    """
    new_actors = {
        "producers": [],
        "labels": [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "min_connection_threshold": min_connections,
    }

    pfc_writers = blocklists.get("pfc_songwriters", set())
    pfc_labels = blocklists.get("pfc_distributors", set())

    # Flag producers
    for name, data in producers.items():
        if data["artist_count"] >= min_connections and not data.get("known_bad_actor", False):
            if name.lower() not in pfc_writers:
                new_actors["producers"].append({
                    "name": name,
                    "artist_count": data["artist_count"],
                    "pfc_corpus_percentage": data["pfc_corpus_percentage"],
                    "sample_artists": data["artists"][:10],
                    "sources": data["sources"],
                    "investigation_priority": "high" if data["artist_count"] >= 10 else "medium",
                })

    # Flag labels
    for name, data in labels.items():
        if data["artist_count"] >= min_connections and not data.get("known_bad_actor", False):
            all_names = data.get("all_name_variants", [name])
            if not any(n.lower() in pfc_labels for n in all_names):
                new_actors["labels"].append({
                    "name": name,
                    "all_variants": all_names,
                    "artist_count": data["artist_count"],
                    "pfc_corpus_percentage": data["pfc_corpus_percentage"],
                    "sample_artists": data["artists"][:10],
                    "sources": data["sources"],
                    "investigation_priority": "high" if data["artist_count"] >= 10 else "medium",
                })

    # Sort by artist count descending
    new_actors["producers"].sort(key=lambda x: x["artist_count"], reverse=True)
    new_actors["labels"].sort(key=lambda x: x["artist_count"], reverse=True)

    return new_actors


# ---------------------------------------------------------------------------
# Corpus overlap analysis
# ---------------------------------------------------------------------------

def compute_similar_artist_overlap(
    similar_graph: dict,
    corpus_names: set[str],
    profiles: list[dict],
) -> dict:
    """For each corpus artist, compute what % of their similar artists are also in the corpus.

    High overlap = algorithmic clustering signal.
    """
    overlap_scores = {}

    for profile in profiles:
        artist_name = profile.get("artist_name", "")
        if not artist_name:
            continue

        similar_list = []
        # Gather all similar artists for this artist
        lastfm = profile.get("lastfm", {})
        for name in lastfm.get("similar_artists", []):
            if isinstance(name, str) and name:
                similar_list.append(name)
        dz = profile.get("deezer", {})
        for name in dz.get("related_artists", []):
            if isinstance(name, str) and name:
                similar_list.append(name)

        if not similar_list:
            continue

        # Deduplicate
        unique_similar = list({n.lower(): n for n in similar_list}.values())
        in_corpus = [n for n in unique_similar if n.lower() in corpus_names]
        overlap_pct = round(len(in_corpus) / len(unique_similar), 4) if unique_similar else 0

        if in_corpus:  # Only record if there's some overlap
            overlap_scores[artist_name] = {
                "similar_count": len(unique_similar),
                "in_corpus_count": len(in_corpus),
                "overlap_percentage": overlap_pct,
                "in_corpus_names": sorted(in_corpus),
            }

    return dict(sorted(overlap_scores.items(), key=lambda x: x[1]["overlap_percentage"], reverse=True))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Phase 2: Entity Expansion")
    parser.add_argument(
        "--min-connections", type=int, default=5,
        help="Flag entities with this many+ artist connections (default 5)",
    )
    args = parser.parse_args()

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
        logger.error("Run 01_enrich.py first.")
        sys.exit(1)

    logger.info("Loading %d enriched profiles...", len(profile_files))
    profiles = []
    load_errors = 0
    for f in profile_files:
        try:
            with open(f) as fh:
                profiles.append(json.load(fh))
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("Failed to load %s: %s", f.name, exc)
            load_errors += 1

    logger.info("Loaded %d profiles (%d errors)", len(profiles), load_errors)

    if not profiles:
        logger.error("No valid profiles loaded.")
        sys.exit(1)

    # Load blocklists
    blocklists = _load_blocklists()
    logger.info("Blocklists loaded: %d AI artists, %d PFC labels, %d PFC songwriters",
                len(blocklists["known_ai_artists"]),
                len(blocklists["pfc_distributors"]),
                len(blocklists["pfc_songwriters"]))

    # Build entity graph
    logger.info("Building entity graph...")
    graph = build_entity_graph(profiles, blocklists)

    # Serialize
    logger.info("Serializing entity graphs...")
    producers = _serialize_producers(graph["producers"], graph["corpus_size"], blocklists)
    labels = _serialize_labels(graph["labels"], graph["corpus_size"], blocklists)
    similar = _serialize_similar(graph["similar"], graph["corpus_names"], graph["corpus_size"])
    cowriters = _serialize_cowriters(graph["cowriters"])

    # Compute similar artist overlap
    logger.info("Computing similar artist corpus overlap...")
    overlap = compute_similar_artist_overlap(graph["similar"], graph["corpus_names"], profiles)

    # Find new bad actors
    logger.info("Identifying potential new bad actors (threshold: %d connections)...", args.min_connections)
    new_actors = find_new_bad_actors(producers, labels, blocklists, args.min_connections)

    # Write outputs
    ENTITIES_DIR.mkdir(parents=True, exist_ok=True)

    outputs = [
        (PRODUCERS_FILE, producers),
        (LABELS_FILE, labels),
        (SIMILAR_ARTISTS_FILE, similar),
        (COWRITERS_FILE, cowriters),
        (NEW_BAD_ACTORS_FILE, new_actors),
    ]

    for path, data in outputs:
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=list)
        logger.info("  Wrote %s", path.name)

    # Save overlap data into similar artists file (append)
    overlap_file = ENTITIES_DIR / "similar_artist_overlap.json"
    with open(overlap_file, "w") as f:
        json.dump(overlap, f, indent=2, ensure_ascii=False)
    logger.info("  Wrote similar_artist_overlap.json")

    # Summary
    producer_count = len(producers)
    label_count = len(labels)
    similar_count = len(similar)
    cowriter_count = len(cowriters)

    high_connectivity_producers = sum(1 for p in producers.values() if p["artist_count"] >= args.min_connections)
    high_connectivity_labels = sum(1 for l in labels.values() if l["artist_count"] >= args.min_connections)
    known_bad_producers = sum(1 for p in producers.values() if p.get("known_bad_actor"))
    known_bad_labels = sum(1 for l in labels.values() if l.get("known_bad_actor"))

    similar_in_corpus = sum(1 for s in similar.values() if s.get("in_corpus"))
    high_overlap_artists = sum(1 for o in overlap.values() if o["overlap_percentage"] >= 0.5)

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "profiles_processed": len(profiles),
        "corpus_size": graph["corpus_size"],
        "entities": {
            "producers": {
                "total": producer_count,
                "high_connectivity": high_connectivity_producers,
                "known_bad_actors": known_bad_producers,
                "new_suspects": len(new_actors["producers"]),
            },
            "labels": {
                "total": label_count,
                "high_connectivity": high_connectivity_labels,
                "known_bad_actors": known_bad_labels,
                "new_suspects": len(new_actors["labels"]),
            },
            "similar_artists": {
                "total": similar_count,
                "in_corpus": similar_in_corpus,
                "corpus_overlap_pct": round(similar_in_corpus / similar_count, 4) if similar_count else 0,
            },
            "cowriter_network": {
                "artists_with_shared_producers": cowriter_count,
            },
        },
        "similar_artist_overlap": {
            "artists_with_overlap": len(overlap),
            "high_overlap_50pct_plus": high_overlap_artists,
        },
        "new_bad_actors": {
            "producers": len(new_actors["producers"]),
            "labels": len(new_actors["labels"]),
        },
        "min_connection_threshold": args.min_connections,
    }

    with open(EXPANSION_SUMMARY_FILE, "w") as f:
        json.dump(summary, f, indent=2)

    # Print summary
    logger.info("\n=== Phase 2: Entity Expansion Complete ===")
    logger.info("Profiles processed: %d", len(profiles))
    logger.info("")
    logger.info("Entities discovered:")
    logger.info("  Producers/songwriters: %d (%d high-connectivity, %d known bad actors)",
                producer_count, high_connectivity_producers, known_bad_producers)
    logger.info("  Labels: %d (%d high-connectivity, %d known bad actors)",
                label_count, high_connectivity_labels, known_bad_labels)
    logger.info("  Similar artists: %d (%d also in PFC corpus = %.1f%%)",
                similar_count, similar_in_corpus,
                (similar_in_corpus / similar_count * 100) if similar_count else 0)
    logger.info("  Co-writer network: %d artists connected via shared producers",
                cowriter_count)
    logger.info("")
    logger.info("NEW potential bad actors (not in blocklists):")
    logger.info("  Producers: %d", len(new_actors["producers"]))
    logger.info("  Labels: %d", len(new_actors["labels"]))

    if new_actors["producers"]:
        logger.info("")
        logger.info("Top new suspect producers:")
        for p in new_actors["producers"][:10]:
            logger.info("  %s — %d artists (%.1f%% of corpus) [%s]",
                        p["name"], p["artist_count"],
                        p["pfc_corpus_percentage"] * 100,
                        p["investigation_priority"])

    if new_actors["labels"]:
        logger.info("")
        logger.info("Top new suspect labels:")
        for l in new_actors["labels"][:10]:
            logger.info("  %s — %d artists (%.1f%% of corpus) [%s]",
                        l["name"], l["artist_count"],
                        l["pfc_corpus_percentage"] * 100,
                        l["investigation_priority"])

    logger.info("")
    logger.info("Output: %s", ENTITIES_DIR)


if __name__ == "__main__":
    main()
