#!/usr/bin/env python3
"""
Analyze PFC playlist track data against blocklists and known patterns.

Reads pfc_playlist_tracks.json and cross-references artists against
the known_ai_artists, pfc_distributors, and pfc_songwriters blocklists.
Reports matches, multi-playlist artists, and suspicious patterns.

Usage:
    python -m spotify_audit.analyze_pfc_data [--output report.txt]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path

from spotify_audit.config import known_ai_artists, pfc_distributors, pfc_songwriters

logger = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_DIR / "data"
TRACKS_FILE = DATA_DIR / "pfc_playlist_tracks.json"


def load_tracks(path: Path) -> dict:
    """Load the PFC playlist tracks JSON."""
    with open(path) as f:
        return json.load(f)


def analyze(data: dict) -> str:
    """Run full analysis and return report text."""
    lines: list[str] = []

    def out(s: str = ""):
        lines.append(s)

    playlists = data["playlists"]
    meta = data["metadata"]

    # Build indexes
    artist_playlists: dict[str, list[str]] = defaultdict(list)   # artist -> [playlist names]
    artist_tracks: dict[str, list[str]] = defaultdict(list)      # artist -> [track names]
    artist_ids: dict[str, str] = {}                               # artist name -> id
    playlist_artists: dict[str, set[str]] = defaultdict(set)     # playlist -> {artist names}

    for p in playlists:
        pname = p["playlist_name"]
        for t in p["tracks"]:
            for a in t["artists"]:
                name = a.get("name", "")
                if not name:
                    continue
                artist_playlists[name].append(pname)
                artist_tracks[name].append(t["track_name"])
                if a.get("id"):
                    artist_ids[name] = a["id"]
                playlist_artists[pname].add(name)

    all_artists = sorted(artist_playlists.keys())

    # Load blocklists
    known_ai = known_ai_artists()  # already lowercased frozenset
    known_sw = pfc_songwriters()  # already lowercased frozenset

    # === Header ===
    out("=" * 70)
    out("PFC PLAYLIST ANALYSIS REPORT")
    out("=" * 70)
    out()
    out(f"Playlists analyzed: {len(playlists)}")
    out(f"Total tracks: {meta['total_tracks']}")
    out(f"Unique artists: {meta['unique_artists']}")
    out()

    # === Section 1: Blocklist matches ===
    out("-" * 70)
    out("1. KNOWN FAKE ARTIST MATCHES")
    out("-" * 70)
    out()

    blocklist_hits = []
    for name in all_artists:
        if name.lower() in known_ai:
            pls = sorted(set(artist_playlists[name]))
            track_count = len(artist_tracks[name])
            blocklist_hits.append((name, track_count, pls))

    if blocklist_hits:
        out(f"Found {len(blocklist_hits)} artists matching known_ai_artists blocklist:\n")
        for name, tc, pls in sorted(blocklist_hits):
            out(f"  * {name} — {tc} track(s) across {len(pls)} playlist(s)")
            for pl in pls:
                out(f"      - {pl}")
    else:
        out("No matches against known_ai_artists blocklist.")
    out()

    # === Section 2: Multi-playlist artists ===
    out("-" * 70)
    out("2. ARTISTS ON MULTIPLE PFC PLAYLISTS")
    out("-" * 70)
    out()
    out("Artists appearing on 3+ PFC playlists (high PFC correlation):\n")

    multi = []
    for name in all_artists:
        unique_pls = sorted(set(artist_playlists[name]))
        if len(unique_pls) >= 3:
            multi.append((name, len(unique_pls), unique_pls))

    multi.sort(key=lambda x: -x[1])
    for name, count, pls in multi:
        marker = " [BLOCKLISTED]" if name.lower() in known_ai else ""
        out(f"  {name} — {count} playlists{marker}")
        for pl in pls:
            out(f"      - {pl}")

    out(f"\n  Total artists on 3+ playlists: {len(multi)}")

    # Also show 2-playlist artists count
    two_pl = [n for n in all_artists if len(set(artist_playlists[n])) == 2]
    out(f"  Artists on exactly 2 playlists: {len(two_pl)}")
    one_pl = [n for n in all_artists if len(set(artist_playlists[n])) == 1]
    out(f"  Artists on exactly 1 playlist: {len(one_pl)}")
    out()

    # === Section 3: Playlist overlap analysis ===
    out("-" * 70)
    out("3. PLAYLIST OVERLAP MATRIX (shared artist counts)")
    out("-" * 70)
    out()

    # Find pairs of playlists with most shared artists
    overlap_pairs = []
    pnames = sorted(playlist_artists.keys())
    for i, p1 in enumerate(pnames):
        for p2 in pnames[i + 1:]:
            shared = playlist_artists[p1] & playlist_artists[p2]
            if len(shared) >= 5:
                overlap_pairs.append((p1, p2, len(shared)))

    overlap_pairs.sort(key=lambda x: -x[2])
    for p1, p2, count in overlap_pairs[:30]:
        out(f"  {count:3d} shared: {p1} <-> {p2}")
    out()

    # === Section 4: Playlist composition ===
    out("-" * 70)
    out("4. PLAYLIST BREAKDOWN")
    out("-" * 70)
    out()

    for p in sorted(playlists, key=lambda x: x["playlist_name"]):
        pname = p["playlist_name"]
        artists_in_pl = playlist_artists[pname]
        bl_count = sum(1 for a in artists_in_pl if a.lower() in known_ai)
        multi_count = sum(1 for a in artists_in_pl
                         if len(set(artist_playlists[a])) >= 3)

        out(f"  {pname}")
        out(f"    Tracks: {p['track_count']}")
        out(f"    Unique artists: {len(artists_in_pl)}")
        out(f"    Blocklisted artists: {bl_count}")
        out(f"    Artists on 3+ PFC playlists: {multi_count}")
        out()

    # === Section 5: Full artist list with flags ===
    out("-" * 70)
    out("5. COMPLETE ARTIST INDEX")
    out("-" * 70)
    out()
    out(f"{'Artist':<40} {'Tracks':>6} {'PLists':>6} {'Flags'}")
    out(f"{'-'*40} {'-'*6} {'-'*6} {'-'*20}")

    for name in all_artists:
        unique_pls = set(artist_playlists[name])
        tc = len(artist_tracks[name])
        flags = []
        if name.lower() in known_ai:
            flags.append("BLOCKLISTED")
        if len(unique_pls) >= 5:
            flags.append("5+ playlists")
        elif len(unique_pls) >= 3:
            flags.append("3+ playlists")
        flag_str = ", ".join(flags) if flags else ""
        out(f"  {name:<40} {tc:>6} {len(unique_pls):>6} {flag_str}")

    out()
    out("=" * 70)
    out("END OF REPORT")
    out("=" * 70)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Analyze PFC playlist tracks")
    parser.add_argument("--input", type=str, default=str(TRACKS_FILE),
                        help="Path to pfc_playlist_tracks.json")
    parser.add_argument("--output", type=str, default="",
                        help="Save report to file (default: print to stdout)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    input_path = Path(args.input)
    if not input_path.exists():
        logger.error("Track data not found: %s", input_path)
        logger.error("Run 'python -m spotify_audit.fetch_pfc_tracks' first.")
        sys.exit(1)

    data = load_tracks(input_path)
    report = analyze(data)

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(report, encoding="utf-8")
        logger.info("Report saved to %s", out_path)
    else:
        print(report)


if __name__ == "__main__":
    main()
