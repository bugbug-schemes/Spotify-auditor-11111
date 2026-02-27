"""
Scan data persistence — hybrid SQLite + JSON storage.

SQLite for structured, queryable data (verdicts, scores, relationships,
blocklist status). JSON files for raw API responses (audit trail).

This module implements the scan_data_persistence.md specification:
- Full artist data persistence across scans
- Evidence tracking per artist per scan
- Entity extraction (labels, songwriters, producers, distributors)
- Network analysis queries
- Blocklist promotion pipeline
- Export and migration utilities

Usage:
    db = init_database()
    scan_id = create_scan(db, playlist_id="abc", playlist_name="My Playlist")
    save_artist(db, artist_data, raw_data_path="data/raw/xyz/")
    save_evidence(db, artist_id, scan_id, evidence_list)
    ...
    finalize_scan(db, scan_id, summary_data)
    db.close()
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default database path — alongside existing data directory
_PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = _PACKAGE_DIR / "data" / "pfc_analyzer.db"
DEFAULT_RAW_DIR = _PACKAGE_DIR / "data" / "raw"
DEFAULT_EXPORT_DIR = _PACKAGE_DIR / "data" / "exports"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
-- Artists: one row per unique artist across all scans
CREATE TABLE IF NOT EXISTS scan_artists_data (
    spotify_id          TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    spotify_url         TEXT,

    -- Spotify metadata (snapshot at scan time)
    monthly_listeners   INTEGER,
    followers           INTEGER,
    verified            BOOLEAN DEFAULT FALSE,
    image_url           TEXT,
    genres              TEXT,
    bio                 TEXT,

    -- Catalog summary
    track_count         INTEGER,
    album_count         INTEGER,
    single_count        INTEGER,
    avg_duration_sec    REAL,
    first_release_date  TEXT,
    release_cadence     TEXT,

    -- Cross-platform presence
    found_musicbrainz   BOOLEAN,
    found_deezer        BOOLEAN,
    found_genius        BOOLEAN,
    found_discogs       BOOLEAN,
    found_lastfm        BOOLEAN,
    found_setlistfm     BOOLEAN,
    found_youtube       BOOLEAN,

    -- Cross-platform metrics
    deezer_fans         INTEGER,
    lastfm_listeners    INTEGER,
    lastfm_playcount    INTEGER,

    -- Verdict & scoring
    verdict             TEXT,
    confidence          TEXT,
    score               REAL,
    threat_category     TEXT,
    threat_label        TEXT,
    matched_rule        TEXT,

    -- Radar chart scores (0-100 each)
    radar_platform_presence   REAL,
    radar_fan_engagement      REAL,
    radar_creative_history    REAL,
    radar_irl_presence        REAL,
    radar_blocklist_status    REAL,
    radar_industry_signals    REAL,

    -- AI analysis
    ai_summary          TEXT,
    ai_image_analysis   TEXT,

    -- Lifecycle
    first_scanned_at    TEXT NOT NULL,
    last_scanned_at     TEXT NOT NULL,
    scan_count          INTEGER DEFAULT 1,
    raw_data_path       TEXT,

    -- Manual review
    manual_verdict      TEXT,
    manual_notes        TEXT,
    reviewed_at         TEXT,
    blocklist_status    TEXT DEFAULT 'none'
);

CREATE INDEX IF NOT EXISTS idx_sad_verdict ON scan_artists_data(verdict);
CREATE INDEX IF NOT EXISTS idx_sad_threat ON scan_artists_data(threat_category);
CREATE INDEX IF NOT EXISTS idx_sad_blocklist ON scan_artists_data(blocklist_status);
CREATE INDEX IF NOT EXISTS idx_sad_score ON scan_artists_data(score);

-- Evidence: every flag/signal produced by collectors
CREATE TABLE IF NOT EXISTS scan_evidence (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    artist_id           TEXT NOT NULL REFERENCES scan_artists_data(spotify_id),
    scan_id             TEXT NOT NULL REFERENCES scan_log(id),

    finding             TEXT NOT NULL,
    source              TEXT NOT NULL,
    evidence_type       TEXT NOT NULL,
    strength            TEXT NOT NULL,
    detail              TEXT,
    tags                TEXT,
    weight              REAL,
    collector           TEXT,
    category            TEXT,

    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_se_artist ON scan_evidence(artist_id);
CREATE INDEX IF NOT EXISTS idx_se_type ON scan_evidence(evidence_type);
CREATE INDEX IF NOT EXISTS idx_se_tags ON scan_evidence(tags);
CREATE INDEX IF NOT EXISTS idx_se_scan ON scan_evidence(scan_id);

-- Entities: labels, songwriters, producers, distributors
CREATE TABLE IF NOT EXISTS scan_entities (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL,
    normalized_name     TEXT NOT NULL,
    entity_type         TEXT NOT NULL,
    blocklist_status    TEXT DEFAULT 'none',

    connected_artist_count  INTEGER DEFAULT 0,
    flagged_artist_count    INTEGER DEFAULT 0,

    first_seen_at       TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL,
    notes               TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sent_unique ON scan_entities(normalized_name, entity_type);
CREATE INDEX IF NOT EXISTS idx_sent_type ON scan_entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_sent_blocklist ON scan_entities(blocklist_status);

-- Artist-Entity relationships (many-to-many)
CREATE TABLE IF NOT EXISTS scan_artist_entities (
    artist_id           TEXT NOT NULL REFERENCES scan_artists_data(spotify_id),
    entity_id           INTEGER NOT NULL REFERENCES scan_entities(id),
    relationship        TEXT NOT NULL,
    source              TEXT,
    created_at          TEXT NOT NULL,

    PRIMARY KEY (artist_id, entity_id, relationship)
);

CREATE INDEX IF NOT EXISTS idx_sae_artist ON scan_artist_entities(artist_id);
CREATE INDEX IF NOT EXISTS idx_sae_entity ON scan_artist_entities(entity_id);

-- Scans: one row per playlist scan
CREATE TABLE IF NOT EXISTS scan_log (
    id                  TEXT PRIMARY KEY,
    playlist_id         TEXT NOT NULL,
    playlist_name       TEXT,
    playlist_owner      TEXT,
    playlist_url        TEXT,
    total_tracks        INTEGER,
    unique_artists      INTEGER,
    is_editorial        BOOLEAN,

    health_score        REAL,
    verdict_breakdown   TEXT,
    threat_breakdown    TEXT,

    started_at          TEXT NOT NULL,
    completed_at        TEXT,
    duration_seconds    REAL,
    api_usage           TEXT,
    scan_tier           TEXT,
    status              TEXT DEFAULT 'running',

    artists_from_cache  INTEGER DEFAULT 0,
    artists_freshly_scanned INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sl_playlist ON scan_log(playlist_id);
CREATE INDEX IF NOT EXISTS idx_sl_date ON scan_log(started_at);

-- Scan-Artist linkage: which artists appeared in which scans
CREATE TABLE IF NOT EXISTS scan_artist_link (
    scan_id             TEXT NOT NULL REFERENCES scan_log(id),
    artist_id           TEXT NOT NULL REFERENCES scan_artists_data(spotify_id),
    track_name          TEXT,
    track_position      INTEGER,

    PRIMARY KEY (scan_id, artist_id)
);
"""


# ---------------------------------------------------------------------------
# Entity name normalization
# ---------------------------------------------------------------------------

_LABEL_SUFFIXES = re.compile(
    r"\s*\b(records|recordings|music|entertainment|publishing|productions?|"
    r"group|llc|ltd|inc|gmbh|co|corp|intl|international)\b\.?",
    re.IGNORECASE,
)


def normalize_entity_name(name: str) -> str:
    """Normalize an entity name for deduplication."""
    n = name.strip().lower()
    n = _LABEL_SUFFIXES.sub("", n)
    n = re.sub(r"[^a-z0-9\s]", "", n)
    return " ".join(n.split())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_scan_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Database initialization
# ---------------------------------------------------------------------------

def init_database(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Create database and all tables if they don't exist."""
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(_SCHEMA)
    db.commit()

    logger.info("Scan database initialized at %s", db_path)
    return db


# ---------------------------------------------------------------------------
# Scan lifecycle
# ---------------------------------------------------------------------------

def create_scan(
    db: sqlite3.Connection,
    playlist_id: str,
    playlist_name: str = "",
    playlist_owner: str = "",
    playlist_url: str = "",
    total_tracks: int = 0,
    unique_artists: int = 0,
    is_editorial: bool = False,
    scan_tier: str = "standard",
) -> str:
    """Create a new scan record. Returns the scan_id (UUID)."""
    scan_id = _new_scan_id()
    now = _now_iso()

    db.execute("""
        INSERT INTO scan_log (
            id, playlist_id, playlist_name, playlist_owner, playlist_url,
            total_tracks, unique_artists, is_editorial,
            started_at, scan_tier, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running')
    """, (
        scan_id, playlist_id, playlist_name, playlist_owner, playlist_url,
        total_tracks, unique_artists, is_editorial, now, scan_tier,
    ))
    db.commit()
    return scan_id


def finalize_scan(
    db: sqlite3.Connection,
    scan_id: str,
    health_score: float = 0.0,
    verdict_breakdown: dict | None = None,
    threat_breakdown: dict | None = None,
    duration_seconds: float = 0.0,
    api_usage: list[dict] | None = None,
    artists_from_cache: int = 0,
    artists_freshly_scanned: int = 0,
    status: str = "completed",
) -> None:
    """Update scan record with final results."""
    now = _now_iso()
    db.execute("""
        UPDATE scan_log SET
            health_score = ?,
            verdict_breakdown = ?,
            threat_breakdown = ?,
            completed_at = ?,
            duration_seconds = ?,
            api_usage = ?,
            artists_from_cache = ?,
            artists_freshly_scanned = ?,
            status = ?
        WHERE id = ?
    """, (
        health_score,
        json.dumps(verdict_breakdown or {}),
        json.dumps(threat_breakdown or {}),
        now,
        duration_seconds,
        json.dumps(api_usage or []),
        artists_from_cache,
        artists_freshly_scanned,
        status,
        scan_id,
    ))
    db.commit()


# ---------------------------------------------------------------------------
# Artist cache check
# ---------------------------------------------------------------------------

def should_scan_artist(spotify_id: str, db: sqlite3.Connection) -> bool:
    """Returns True if this artist needs a fresh scan."""
    row = db.execute(
        "SELECT last_scanned_at FROM scan_artists_data WHERE spotify_id = ?",
        (spotify_id,)
    ).fetchone()

    if row is None:
        return True  # Never scanned — must scan

    return False  # Cache hit — skip API calls


# ---------------------------------------------------------------------------
# Save artist data
# ---------------------------------------------------------------------------

def save_artist(db: sqlite3.Connection, artist: dict, raw_data_path: str | None = None) -> None:
    """Insert or update an artist record after analysis.

    The ``artist`` dict should contain keys matching the schema columns.
    Missing keys default to None/0.
    """
    now = _now_iso()

    # Extract radar scores (nested dict or flat keys)
    radar = artist.get("radar", {})

    db.execute("""
        INSERT INTO scan_artists_data (
            spotify_id, name, spotify_url,
            monthly_listeners, followers, verified, image_url, genres, bio,
            track_count, album_count, single_count, avg_duration_sec,
            first_release_date, release_cadence,
            found_musicbrainz, found_deezer, found_genius, found_discogs,
            found_lastfm, found_setlistfm, found_youtube,
            deezer_fans, lastfm_listeners, lastfm_playcount,
            verdict, confidence, score, threat_category, threat_label, matched_rule,
            radar_platform_presence, radar_fan_engagement, radar_creative_history,
            radar_irl_presence, radar_blocklist_status, radar_industry_signals,
            ai_summary, ai_image_analysis,
            first_scanned_at, last_scanned_at, scan_count, raw_data_path
        ) VALUES (
            ?, ?, ?,
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?, 1, ?
        )
        ON CONFLICT(spotify_id) DO UPDATE SET
            name = excluded.name,
            monthly_listeners = excluded.monthly_listeners,
            followers = excluded.followers,
            verified = excluded.verified,
            image_url = excluded.image_url,
            genres = excluded.genres,
            track_count = excluded.track_count,
            album_count = excluded.album_count,
            single_count = excluded.single_count,
            avg_duration_sec = excluded.avg_duration_sec,
            found_musicbrainz = excluded.found_musicbrainz,
            found_deezer = excluded.found_deezer,
            found_genius = excluded.found_genius,
            found_discogs = excluded.found_discogs,
            found_lastfm = excluded.found_lastfm,
            found_setlistfm = excluded.found_setlistfm,
            found_youtube = excluded.found_youtube,
            deezer_fans = excluded.deezer_fans,
            lastfm_listeners = excluded.lastfm_listeners,
            lastfm_playcount = excluded.lastfm_playcount,
            verdict = excluded.verdict,
            confidence = excluded.confidence,
            score = excluded.score,
            threat_category = excluded.threat_category,
            threat_label = excluded.threat_label,
            matched_rule = excluded.matched_rule,
            radar_platform_presence = excluded.radar_platform_presence,
            radar_fan_engagement = excluded.radar_fan_engagement,
            radar_creative_history = excluded.radar_creative_history,
            radar_irl_presence = excluded.radar_irl_presence,
            radar_blocklist_status = excluded.radar_blocklist_status,
            radar_industry_signals = excluded.radar_industry_signals,
            ai_summary = excluded.ai_summary,
            ai_image_analysis = excluded.ai_image_analysis,
            last_scanned_at = excluded.last_scanned_at,
            scan_count = scan_artists_data.scan_count + 1,
            raw_data_path = COALESCE(excluded.raw_data_path, scan_artists_data.raw_data_path)
    """, (
        artist.get("spotify_id"), artist.get("name"), artist.get("url"),
        artist.get("monthly_listeners"), artist.get("followers"),
        artist.get("verified", False), artist.get("image_url"),
        json.dumps(artist.get("genres", [])) if isinstance(artist.get("genres"), list) else artist.get("genres"),
        artist.get("bio"),
        artist.get("track_count"), artist.get("album_count"), artist.get("single_count"),
        artist.get("avg_duration_sec"), artist.get("first_release_date"),
        artist.get("release_cadence"),
        artist.get("found_musicbrainz"), artist.get("found_deezer"),
        artist.get("found_genius"), artist.get("found_discogs"),
        artist.get("found_lastfm"), artist.get("found_setlistfm"),
        artist.get("found_youtube"),
        artist.get("deezer_fans"), artist.get("lastfm_listeners"), artist.get("lastfm_playcount"),
        artist.get("verdict"), artist.get("confidence"), artist.get("score"),
        artist.get("threat_category"), artist.get("threat_label"), artist.get("matched_rule"),
        radar.get("platform_presence", artist.get("radar_platform_presence")),
        radar.get("fan_engagement", artist.get("radar_fan_engagement")),
        radar.get("creative_history", artist.get("radar_creative_history")),
        radar.get("irl_presence", artist.get("radar_irl_presence")),
        radar.get("blocklist_status", artist.get("radar_blocklist_status")),
        radar.get("industry_signals", artist.get("radar_industry_signals")),
        artist.get("ai_summary"), artist.get("ai_image_analysis"),
        now, now, raw_data_path,
    ))


def save_artist_from_report(
    db: sqlite3.Connection,
    artist_id: str,
    artist_name: str,
    artist_info: Any,
    evaluation: Any,
    report: Any,
    raw_data_path: str | None = None,
) -> None:
    """Save an artist from the existing pipeline data structures.

    Bridges the gap between ArtistInfo/ArtistEvaluation/ArtistReport
    and the flat dict expected by save_artist().
    """
    ext = evaluation.external_data if evaluation and hasattr(evaluation, 'external_data') else None
    cat_scores = evaluation.category_scores if evaluation else {}

    # Compute average duration from track_durations
    avg_duration = None
    if hasattr(artist_info, 'track_durations') and artist_info.track_durations:
        avg_duration = sum(artist_info.track_durations) / len(artist_info.track_durations) / 1000.0

    # First release date
    first_release = None
    if hasattr(artist_info, 'release_dates') and artist_info.release_dates:
        sorted_dates = sorted(d for d in artist_info.release_dates if d)
        if sorted_dates:
            first_release = sorted_dates[0]

    # Build URL
    url = None
    if hasattr(artist_info, 'external_urls') and artist_info.external_urls:
        url = artist_info.external_urls.get("spotify") or artist_info.external_urls.get("deezer")

    artist_dict = {
        "spotify_id": artist_id,
        "name": artist_name,
        "url": url,
        "monthly_listeners": getattr(artist_info, 'monthly_listeners', None),
        "followers": getattr(artist_info, 'followers', 0),
        "verified": getattr(artist_info, 'verified', False),
        "image_url": getattr(artist_info, 'image_url', None),
        "genres": getattr(artist_info, 'genres', []),
        "bio": getattr(artist_info, 'bio', None),
        "track_count": getattr(artist_info, 'total_tracks', 0),
        "album_count": getattr(artist_info, 'album_count', 0),
        "single_count": getattr(artist_info, 'single_count', 0),
        "avg_duration_sec": avg_duration,
        "first_release_date": first_release,
        "release_cadence": None,
        "found_musicbrainz": ext.musicbrainz_found if ext else False,
        "found_deezer": getattr(artist_info, 'deezer_fans', 0) > 0,
        "found_genius": ext.genius_found if ext else False,
        "found_discogs": ext.discogs_found if ext else False,
        "found_lastfm": ext.lastfm_found if ext else False,
        "found_setlistfm": ext.setlistfm_found if ext else False,
        "found_youtube": ext.youtube_channel_found if ext else False,
        "deezer_fans": getattr(artist_info, 'deezer_fans', 0),
        "lastfm_listeners": ext.lastfm_listeners if ext else None,
        "lastfm_playcount": ext.lastfm_playcount if ext else None,
        "verdict": evaluation.verdict.value if evaluation else None,
        "confidence": evaluation.confidence if evaluation else None,
        "score": report.final_score if report else None,
        "threat_category": str(report.threat_category) if report and report.threat_category else None,
        "threat_label": report.threat_category_name if report else None,
        "matched_rule": evaluation.matched_rule if evaluation else None,
        "radar": {
            "platform_presence": cat_scores.get("Platform Presence", 0),
            "fan_engagement": cat_scores.get("Fan Engagement", 0),
            "creative_history": cat_scores.get("Creative History", 0),
            "irl_presence": cat_scores.get("IRL Presence", 0),
            "blocklist_status": cat_scores.get("Blocklist Status", 0),
            "industry_signals": cat_scores.get("Industry Signals", 0),
        },
        "ai_summary": None,
        "ai_image_analysis": None,
    }

    save_artist(db, artist_dict, raw_data_path)


# ---------------------------------------------------------------------------
# Save evidence
# ---------------------------------------------------------------------------

def save_evidence(
    db: sqlite3.Connection,
    artist_id: str,
    scan_id: str,
    evidence_list: list[dict],
) -> None:
    """Save all evidence items for an artist in a scan."""
    now = _now_iso()
    for ev in evidence_list:
        tags = ev.get("tags", [])
        tags_json = json.dumps(tags) if isinstance(tags, list) else tags

        db.execute("""
            INSERT INTO scan_evidence (
                artist_id, scan_id, finding, source, evidence_type,
                strength, detail, tags, weight, collector, category,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            artist_id, scan_id,
            ev.get("finding", ""),
            ev.get("source", ""),
            ev.get("evidence_type", ev.get("type", "")),
            ev.get("strength", ""),
            ev.get("detail", ""),
            tags_json,
            ev.get("weight"),
            ev.get("collector"),
            ev.get("category"),
            now,
        ))


def save_evidence_from_evaluation(
    db: sqlite3.Connection,
    artist_id: str,
    scan_id: str,
    evaluation: Any,
) -> None:
    """Save evidence from an ArtistEvaluation object."""
    evidence_list = []
    all_evidence = (
        list(evaluation.red_flags)
        + list(evaluation.green_flags)
        + list(evaluation.neutral_notes)
    )
    for e in all_evidence:
        evidence_list.append({
            "finding": e.finding,
            "source": e.source,
            "evidence_type": e.evidence_type,
            "strength": e.strength,
            "detail": e.detail,
            "tags": e.tags,
        })
    save_evidence(db, artist_id, scan_id, evidence_list)


# ---------------------------------------------------------------------------
# Entity management
# ---------------------------------------------------------------------------

def upsert_entity(
    db: sqlite3.Connection,
    name: str,
    entity_type: str,
) -> int:
    """Insert entity if new, return its ID."""
    now = _now_iso()
    normalized = normalize_entity_name(name)

    row = db.execute(
        "SELECT id FROM scan_entities WHERE normalized_name = ? AND entity_type = ?",
        (normalized, entity_type)
    ).fetchone()

    if row:
        db.execute(
            "UPDATE scan_entities SET last_seen_at = ?, connected_artist_count = connected_artist_count + 1 WHERE id = ?",
            (now, row["id"])
        )
        return row["id"]
    else:
        cursor = db.execute(
            "INSERT INTO scan_entities (name, normalized_name, entity_type, first_seen_at, last_seen_at, connected_artist_count) VALUES (?, ?, ?, ?, ?, 1)",
            (name, normalized, entity_type, now, now)
        )
        return cursor.lastrowid


def link_artist_entity(
    db: sqlite3.Connection,
    artist_id: str,
    entity_id: int,
    relationship: str,
    source: str = "",
) -> None:
    """Link an artist to an entity."""
    now = _now_iso()
    db.execute("""
        INSERT OR IGNORE INTO scan_artist_entities
            (artist_id, entity_id, relationship, source, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (artist_id, entity_id, relationship, source, now))


def save_entities_from_evaluation(
    db: sqlite3.Connection,
    artist_id: str,
    evaluation: Any,
    ext: Any = None,
) -> list[tuple[str, str]]:
    """Extract entities from evaluation and external data, save relationships.

    Returns list of (entity_type, name) tuples for tracking.
    """
    entities_found: list[tuple[str, str]] = []

    # Labels from evaluation
    if hasattr(evaluation, 'labels') and evaluation.labels:
        for label_name in evaluation.labels:
            entity_id = upsert_entity(db, label_name, "label")
            link_artist_entity(db, artist_id, entity_id, "label", "scan")
            entities_found.append(("label", label_name))

    # Additional labels from external data
    if ext:
        # MusicBrainz labels
        if hasattr(ext, 'musicbrainz_labels') and ext.musicbrainz_labels:
            for label_name in ext.musicbrainz_labels:
                entity_id = upsert_entity(db, label_name, "label")
                link_artist_entity(db, artist_id, entity_id, "label", "musicbrainz")
                entities_found.append(("label", label_name))

        # Discogs labels
        if hasattr(ext, 'discogs_labels') and ext.discogs_labels:
            for label_name in ext.discogs_labels:
                entity_id = upsert_entity(db, label_name, "label")
                link_artist_entity(db, artist_id, entity_id, "label", "discogs")
                entities_found.append(("label", label_name))

    # Contributors/songwriters from evaluation
    if hasattr(evaluation, 'contributors') and evaluation.contributors:
        for contrib_name in evaluation.contributors:
            entity_id = upsert_entity(db, contrib_name, "songwriter")
            link_artist_entity(db, artist_id, entity_id, "credited_songwriter", "scan")
            entities_found.append(("songwriter", contrib_name))

    # ISRC registrants as distributors
    if ext and hasattr(ext, 'isrc_registrants') and ext.isrc_registrants:
        for registrant in ext.isrc_registrants:
            entity_id = upsert_entity(db, registrant, "distributor")
            link_artist_entity(db, artist_id, entity_id, "distributor", "isrc")
            entities_found.append(("distributor", registrant))

    return entities_found


# ---------------------------------------------------------------------------
# Scan-artist linkage
# ---------------------------------------------------------------------------

def link_scan_artist(
    db: sqlite3.Connection,
    scan_id: str,
    artist_id: str,
    track_name: str | None = None,
    track_position: int | None = None,
) -> None:
    """Link an artist to a scan."""
    db.execute("""
        INSERT OR IGNORE INTO scan_artist_link
            (scan_id, artist_id, track_name, track_position)
        VALUES (?, ?, ?, ?)
    """, (scan_id, artist_id, track_name, track_position))


# ---------------------------------------------------------------------------
# Update entity network stats
# ---------------------------------------------------------------------------

def update_entity_flagged_counts(db: sqlite3.Connection) -> None:
    """Recompute flagged_artist_count for all entities based on connected artist verdicts."""
    db.execute("""
        UPDATE scan_entities SET flagged_artist_count = (
            SELECT COUNT(DISTINCT sae.artist_id)
            FROM scan_artist_entities sae
            JOIN scan_artists_data a ON sae.artist_id = a.spotify_id
            WHERE sae.entity_id = scan_entities.id
              AND a.verdict IN ('Suspicious', 'Likely Artificial')
        )
    """)
    db.execute("""
        UPDATE scan_entities SET connected_artist_count = (
            SELECT COUNT(DISTINCT sae.artist_id)
            FROM scan_artist_entities sae
            WHERE sae.entity_id = scan_entities.id
        )
    """)
    db.commit()


# ---------------------------------------------------------------------------
# Raw API response saving
# ---------------------------------------------------------------------------

def save_raw_response(
    artist_id: str,
    source: str,
    response_data: Any,
    status: str = "found",
    http_status: int = 200,
    base_dir: str | Path | None = None,
) -> str:
    """Save a raw API response to disk as JSON.

    Returns the filepath of the saved response.
    """
    if base_dir is None:
        base_dir = DEFAULT_RAW_DIR
    base_dir = Path(base_dir)

    # Sanitize artist_id for filesystem
    safe_id = re.sub(r'[^\w\-.]', '_', artist_id)
    artist_dir = base_dir / safe_id
    artist_dir.mkdir(parents=True, exist_ok=True)

    filepath = artist_dir / f"{source}.json"
    payload = {
        "fetched_at": _now_iso(),
        "source": source,
        "artist_id": artist_id,
        "status": status,
        "http_status": http_status,
        "response": response_data,
    }

    with open(filepath, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    return str(filepath)


# ---------------------------------------------------------------------------
# Network analysis queries
# ---------------------------------------------------------------------------

def find_artists_sharing_songwriter_with_bad_actors(db: sqlite3.Connection) -> list[dict]:
    """Find all artists sharing a songwriter/producer with known bad actors."""
    rows = db.execute("""
        SELECT DISTINCT a.name, a.spotify_id, a.verdict, a.score
        FROM scan_artists_data a
        JOIN scan_artist_entities ae ON a.spotify_id = ae.artist_id
        JOIN scan_entities e ON ae.entity_id = e.id
        WHERE e.entity_type IN ('songwriter', 'producer')
          AND e.id IN (
              SELECT ae2.entity_id
              FROM scan_artist_entities ae2
              JOIN scan_artists_data a2 ON ae2.artist_id = a2.spotify_id
              WHERE a2.blocklist_status = 'confirmed_bad'
          )
          AND a.blocklist_status != 'confirmed_bad'
        ORDER BY a.score ASC
    """).fetchall()
    return [dict(r) for r in rows]


def find_suspicious_entities(db: sqlite3.Connection, min_connections: int = 3, min_flagged: int = 2) -> list[dict]:
    """Find entities with many flagged artist connections (potential PFC operations)."""
    rows = db.execute("""
        SELECT e.name, e.entity_type, e.connected_artist_count, e.flagged_artist_count,
               ROUND(100.0 * e.flagged_artist_count / e.connected_artist_count, 1) AS pct_flagged
        FROM scan_entities e
        WHERE e.connected_artist_count >= ?
          AND e.flagged_artist_count >= ?
        ORDER BY pct_flagged DESC, e.flagged_artist_count DESC
    """, (min_connections, min_flagged)).fetchall()
    return [dict(r) for r in rows]


def find_cross_scan_overlap(db: sqlite3.Connection, editorial_only: bool = True) -> list[dict]:
    """Find artists appearing on multiple playlists across scans."""
    where = "WHERE s.is_editorial = TRUE" if editorial_only else ""
    rows = db.execute(f"""
        SELECT a.name, a.verdict, COUNT(DISTINCT s.playlist_id) AS playlist_count,
               GROUP_CONCAT(DISTINCT s.playlist_name) AS playlists
        FROM scan_artists_data a
        JOIN scan_artist_link sa ON a.spotify_id = sa.artist_id
        JOIN scan_log s ON sa.scan_id = s.id
        {where}
        GROUP BY a.spotify_id
        HAVING playlist_count >= 2
        ORDER BY playlist_count DESC
    """).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Blocklist promotion
# ---------------------------------------------------------------------------

def promote_to_blocklist(
    db: sqlite3.Connection,
    spotify_id: str,
    notes: str = "",
) -> None:
    """Confirm an artist as a bad actor and update related entity counts."""
    now = _now_iso()

    db.execute("""
        UPDATE scan_artists_data
        SET blocklist_status = 'confirmed_bad',
            manual_verdict = verdict,
            manual_notes = ?,
            reviewed_at = ?
        WHERE spotify_id = ?
    """, (notes, now, spotify_id))

    # Recompute flagged counts for connected entities
    db.execute("""
        UPDATE scan_entities SET flagged_artist_count = (
            SELECT COUNT(DISTINCT sae.artist_id)
            FROM scan_artist_entities sae
            JOIN scan_artists_data a ON sae.artist_id = a.spotify_id
            WHERE sae.entity_id = scan_entities.id
              AND a.blocklist_status = 'confirmed_bad'
        )
        WHERE id IN (
            SELECT entity_id FROM scan_artist_entities WHERE artist_id = ?
        )
    """, (spotify_id,))

    db.commit()


def mark_confirmed_clean(
    db: sqlite3.Connection,
    spotify_id: str,
    notes: str = "",
) -> None:
    """Mark an artist as confirmed clean (manual review override)."""
    now = _now_iso()
    db.execute("""
        UPDATE scan_artists_data
        SET blocklist_status = 'confirmed_clean',
            manual_notes = ?,
            reviewed_at = ?
        WHERE spotify_id = ?
    """, (notes, now, spotify_id))
    db.commit()


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_scan_results(db: sqlite3.Connection, scan_id: str, export_dir: str | Path | None = None) -> dict:
    """Generate the full playlist results JSON from SQLite for a given scan."""
    if export_dir is None:
        export_dir = DEFAULT_EXPORT_DIR
    export_dir = Path(export_dir)

    scan = db.execute("SELECT * FROM scan_log WHERE id = ?", (scan_id,)).fetchone()
    if not scan:
        raise ValueError(f"Scan {scan_id} not found")

    artists = db.execute("""
        SELECT a.* FROM scan_artists_data a
        JOIN scan_artist_link sa ON a.spotify_id = sa.artist_id
        WHERE sa.scan_id = ?
        ORDER BY a.score ASC
    """, (scan_id,)).fetchall()

    def _serialize_artist(a: sqlite3.Row) -> dict:
        artist_dict = dict(a)

        # Fetch evidence for this artist in this scan
        evidence = db.execute("""
            SELECT * FROM scan_evidence
            WHERE artist_id = ? AND scan_id = ?
            ORDER BY evidence_type, strength DESC
        """, (a["spotify_id"], scan_id)).fetchall()

        red_flags = []
        green_flags = []
        neutral_notes = []
        for ev in evidence:
            ev_dict = {
                "finding": ev["finding"],
                "source": ev["source"],
                "type": ev["evidence_type"],
                "strength": ev["strength"],
                "detail": ev["detail"],
                "tags": json.loads(ev["tags"]) if ev["tags"] else [],
            }
            if ev["evidence_type"] == "red_flag":
                red_flags.append(ev_dict)
            elif ev["evidence_type"] == "green_flag":
                green_flags.append(ev_dict)
            else:
                neutral_notes.append(ev_dict)

        artist_dict["red_flags"] = red_flags
        artist_dict["green_flags"] = green_flags
        artist_dict["neutral_notes"] = neutral_notes

        # Parse genres JSON
        if artist_dict.get("genres"):
            try:
                artist_dict["genres"] = json.loads(artist_dict["genres"])
            except (json.JSONDecodeError, TypeError):
                pass

        # Radar scores
        artist_dict["radar"] = {
            "labels": ["Platform Presence", "Fan Engagement", "Creative History",
                       "IRL Presence", "Industry Signals", "Blocklist Status"],
            "scores": [
                artist_dict.get("radar_platform_presence", 0),
                artist_dict.get("radar_fan_engagement", 0),
                artist_dict.get("radar_creative_history", 0),
                artist_dict.get("radar_irl_presence", 0),
                artist_dict.get("radar_industry_signals", 0),
                artist_dict.get("radar_blocklist_status", 0),
            ],
        }

        # Connected entities
        entities = db.execute("""
            SELECT e.name, e.entity_type, sae.relationship
            FROM scan_entities e
            JOIN scan_artist_entities sae ON e.id = sae.entity_id
            WHERE sae.artist_id = ?
        """, (a["spotify_id"],)).fetchall()
        artist_dict["entities"] = [
            {"name": e["name"], "type": e["entity_type"], "relationship": e["relationship"]}
            for e in entities
        ]

        return artist_dict

    # Compute summary
    verdict_counts = {}
    for a in artists:
        v = a["verdict"] or "Unknown"
        verdict_counts[v] = verdict_counts.get(v, 0) + 1

    result = {
        "schema_version": "1.0",
        "generated_at": _now_iso(),
        "playlist": {
            "id": scan["playlist_id"],
            "name": scan["playlist_name"],
            "owner": scan["playlist_owner"],
            "url": scan["playlist_url"],
            "total_tracks": scan["total_tracks"],
            "unique_artists": scan["unique_artists"],
            "is_editorial": bool(scan["is_editorial"]),
        },
        "summary": {
            "health_score": scan["health_score"],
            "verdict_breakdown": verdict_counts,
            "scan_tier": scan["scan_tier"],
            "duration_seconds": scan["duration_seconds"],
            "artists_from_cache": scan["artists_from_cache"],
            "artists_freshly_scanned": scan["artists_freshly_scanned"],
        },
        "api_usage": json.loads(scan["api_usage"] or "[]"),
        "artists": [_serialize_artist(a) for a in artists],
    }

    # Save to exports directory
    export_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    filename = f"playlist_{scan['playlist_id']}_{date_str}.json"
    filepath = export_dir / filename

    with open(filepath, "w") as f:
        json.dump(result, f, indent=2, default=str)

    logger.info("Exported scan results to %s", filepath)
    return result


# ---------------------------------------------------------------------------
# Migration: import existing cached JSON files
# ---------------------------------------------------------------------------

def import_cached_json(db: sqlite3.Connection, json_path: str | Path) -> str:
    """Import a previously cached playlist results JSON into SQLite.

    Returns the scan_id of the imported scan.
    """
    with open(json_path) as f:
        data = json.load(f)

    playlist = data.get("playlist", {})
    scan_id = create_scan(
        db,
        playlist_id=playlist.get("id", "imported"),
        playlist_name=playlist.get("name", ""),
        playlist_owner=playlist.get("owner", ""),
        total_tracks=playlist.get("total_tracks", 0),
        unique_artists=playlist.get("total_unique_artists", len(data.get("artists", []))),
        scan_tier="imported",
    )

    for artist in data.get("artists", []):
        artist_id = artist.get("artist_id", artist.get("spotify_id", ""))
        if not artist_id:
            continue

        # Map JSON keys to our schema
        artist_dict = {
            "spotify_id": artist_id,
            "name": artist.get("artist_name", artist.get("name", "")),
            "verdict": artist.get("verdict"),
            "confidence": artist.get("confidence"),
            "score": artist.get("final_score", artist.get("score")),
            "threat_category": str(artist.get("threat_category", "")) if artist.get("threat_category") else None,
            "threat_label": artist.get("threat_category_name", artist.get("threat_label")),
        }

        # Copy radar scores if available
        radar = artist.get("radar", {})
        if isinstance(radar, dict) and "scores" in radar:
            scores = radar["scores"]
            labels = ["platform_presence", "fan_engagement", "creative_history",
                       "irl_presence", "industry_signals", "blocklist_status"]
            artist_dict["radar"] = {
                labels[i]: scores[i] for i in range(min(len(scores), len(labels)))
            }
        elif isinstance(radar, dict):
            artist_dict["radar"] = radar

        # Copy category_scores as radar if available
        cat_scores = artist.get("category_scores", {})
        if cat_scores and not artist_dict.get("radar"):
            artist_dict["radar"] = {
                "platform_presence": cat_scores.get("Platform Presence", 0),
                "fan_engagement": cat_scores.get("Fan Engagement", 0),
                "creative_history": cat_scores.get("Creative History", 0),
                "irl_presence": cat_scores.get("IRL Presence", 0),
                "blocklist_status": cat_scores.get("Blocklist Status", 0),
                "industry_signals": cat_scores.get("Industry Signals", 0),
            }

        save_artist(db, artist_dict)

        # Import evidence
        evidence_list = []
        for flag in artist.get("red_flags", []):
            evidence_list.append({
                "finding": flag.get("finding", ""),
                "source": flag.get("source", ""),
                "evidence_type": "red_flag",
                "strength": flag.get("strength", ""),
                "detail": flag.get("detail", ""),
                "tags": flag.get("tags", []),
            })
        for flag in artist.get("green_flags", []):
            evidence_list.append({
                "finding": flag.get("finding", ""),
                "source": flag.get("source", ""),
                "evidence_type": "green_flag",
                "strength": flag.get("strength", ""),
                "detail": flag.get("detail", ""),
                "tags": flag.get("tags", []),
            })
        for note in artist.get("neutral_notes", []):
            evidence_list.append({
                "finding": note.get("finding", ""),
                "source": note.get("source", ""),
                "evidence_type": "neutral",
                "strength": note.get("strength", ""),
                "detail": note.get("detail", ""),
                "tags": note.get("tags", []),
            })

        if evidence_list:
            save_evidence(db, artist_id, scan_id, evidence_list)

        # Link to scan
        link_scan_artist(db, scan_id, artist_id)

    # Finalize
    summary = data.get("summary", {})
    finalize_scan(
        db, scan_id,
        health_score=summary.get("health_score", 0),
        verdict_breakdown=summary.get("verdict_breakdown"),
        api_usage=data.get("api_usage"),
        status="completed",
    )

    db.commit()
    logger.info("Imported %d artists from %s (scan_id=%s)", len(data.get("artists", [])), json_path, scan_id)
    return scan_id


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_scan_history(db: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Get recent scan history."""
    rows = db.execute("""
        SELECT id, playlist_id, playlist_name, status, health_score,
               started_at, completed_at, duration_seconds,
               artists_from_cache, artists_freshly_scanned
        FROM scan_log
        ORDER BY started_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_artist_by_id(db: sqlite3.Connection, spotify_id: str) -> dict | None:
    """Get a single artist's full record."""
    row = db.execute(
        "SELECT * FROM scan_artists_data WHERE spotify_id = ?",
        (spotify_id,)
    ).fetchone()
    return dict(row) if row else None


def get_artists_by_verdict(db: sqlite3.Connection, verdict: str, limit: int = 100) -> list[dict]:
    """Get all artists with a specific verdict."""
    rows = db.execute(
        "SELECT * FROM scan_artists_data WHERE verdict = ? ORDER BY score ASC LIMIT ?",
        (verdict, limit)
    ).fetchall()
    return [dict(r) for r in rows]


def get_entity_network(db: sqlite3.Connection, entity_id: int) -> list[dict]:
    """Get all artists connected to a specific entity."""
    rows = db.execute("""
        SELECT a.name, a.spotify_id, a.verdict, a.score, a.blocklist_status,
               sae.relationship
        FROM scan_artists_data a
        JOIN scan_artist_entities sae ON a.spotify_id = sae.artist_id
        WHERE sae.entity_id = ?
        ORDER BY a.score ASC
    """, (entity_id,)).fetchall()
    return [dict(r) for r in rows]


def get_db_stats(db: sqlite3.Connection) -> dict:
    """Get summary statistics for the persistence database."""
    artists_count = db.execute("SELECT COUNT(*) FROM scan_artists_data").fetchone()[0]
    scans_count = db.execute("SELECT COUNT(*) FROM scan_log").fetchone()[0]
    evidence_count = db.execute("SELECT COUNT(*) FROM scan_evidence").fetchone()[0]
    entities_count = db.execute("SELECT COUNT(*) FROM scan_entities").fetchone()[0]

    verdict_counts = {}
    for row in db.execute("SELECT verdict, COUNT(*) as cnt FROM scan_artists_data GROUP BY verdict"):
        verdict_counts[row["verdict"] or "Unknown"] = row["cnt"]

    return {
        "artists": artists_count,
        "scans": scans_count,
        "evidence_records": evidence_count,
        "entities": entities_count,
        "verdict_breakdown": verdict_counts,
    }
