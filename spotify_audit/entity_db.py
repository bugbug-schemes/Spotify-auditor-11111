"""
Relational database for suspicious / bad entities.

Tracks artists, labels, songwriters, and publishers as separate tables
with many-to-many relationships and observation logs.  Backed by SQLite.

Usage:
    db = EntityDB("path/to/entities.db")
    artist_id = db.upsert_artist("Calm Sleep", threat_status="suspected")
    label_id = db.upsert_label("Chill Records")
    db.link_artist_label(artist_id, label_id, source="deezer")
    db.add_observation("artist", artist_id, "red_flag", "PFC blocklist match", ...)
    db.close()
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default DB location
DEFAULT_DB_PATH = Path(__file__).parent / "data" / "entities.db"

# Threat status values
UNKNOWN = "unknown"
SUSPECTED = "suspected"
CONFIRMED_BAD = "confirmed_bad"
CLEARED = "cleared"

# Review statuses
NOT_QUEUED = "not_queued"
PENDING_REVIEW = "pending_review"
DEFERRED = "deferred"
REVIEWED = "reviewed"

# Review actions
ACTION_CONFIRM = "confirmed_bad"
ACTION_DISMISS = "dismissed"
ACTION_DEFER = "deferred"

_VALID_STATUSES = {UNKNOWN, SUSPECTED, CONFIRMED_BAD, CLEARED}
_VALID_REVIEW_STATUSES = {NOT_QUEUED, PENDING_REVIEW, DEFERRED, REVIEWED}
_VALID_REVIEW_ACTIONS = {ACTION_CONFIRM, ACTION_DISMISS, ACTION_DEFER}
_ENTITY_TYPES = {"artist", "label", "songwriter", "publisher"}

# Promotion thresholds — flagged artist connections before entering review queue
REVIEW_THRESHOLDS = {
    "songwriter": 3,
    "label": 5,
    "publisher": 2,
}
# Distributors use the label threshold
REVIEW_THRESHOLDS["distributor"] = 5

# Common label suffixes to strip for normalization
_LABEL_SUFFIXES = re.compile(
    r"\s*\b(records|recordings|music|entertainment|publishing|productions?|"
    r"group|llc|ltd|inc|gmbh|co|corp|intl|international)\b\.?",
    re.IGNORECASE,
)


def _normalize(name: str) -> str:
    """Normalize a name for fuzzy matching."""
    n = name.strip().lower()
    n = _LABEL_SUFFIXES.sub("", n)
    n = re.sub(r"[^a-z0-9\s]", "", n)
    return " ".join(n.split())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
-- Core entity tables ---------------------------------------------------------

CREATE TABLE IF NOT EXISTS artists (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    spotify_id      TEXT,
    deezer_id       INTEGER,
    musicbrainz_id  TEXT,
    genius_id       INTEGER,
    discogs_id      INTEGER,
    setlistfm_id    TEXT,
    lastfm_url      TEXT,
    -- Per-API found flags
    found_spotify   INTEGER DEFAULT 0,
    found_deezer    INTEGER DEFAULT 0,
    found_musicbrainz INTEGER DEFAULT 0,
    found_genius    INTEGER DEFAULT 0,
    found_discogs   INTEGER DEFAULT 0,
    found_setlistfm INTEGER DEFAULT 0,
    found_lastfm    INTEGER DEFAULT 0,
    platform_count  INTEGER DEFAULT 0,
    -- Threat & verdict
    threat_status   TEXT NOT NULL DEFAULT 'unknown',
    threat_category REAL,            -- 1, 1.5, 2, 3, 4
    latest_verdict  TEXT,            -- e.g. "Likely Artificial"
    latest_confidence TEXT,
    scan_count      INTEGER DEFAULT 0,  -- number of times scanned
    auto_promoted_at TEXT,               -- ISO timestamp of auto-promotion
    country         TEXT,
    genres          TEXT,             -- JSON array
    deezer_fans     INTEGER,
    lastfm_listeners INTEGER,
    lastfm_playcount INTEGER,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    notes           TEXT DEFAULT '',
    UNIQUE(normalized_name)
);

CREATE TABLE IF NOT EXISTS labels (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    threat_status   TEXT NOT NULL DEFAULT 'unknown',
    artist_count    INTEGER DEFAULT 0,  -- cached count
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    notes           TEXT DEFAULT '',
    UNIQUE(normalized_name)
);

CREATE TABLE IF NOT EXISTS songwriters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    threat_status   TEXT NOT NULL DEFAULT 'unknown',
    artist_count    INTEGER DEFAULT 0,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    notes           TEXT DEFAULT '',
    UNIQUE(normalized_name)
);

CREATE TABLE IF NOT EXISTS publishers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    threat_status   TEXT NOT NULL DEFAULT 'unknown',
    artist_count    INTEGER DEFAULT 0,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    notes           TEXT DEFAULT '',
    UNIQUE(normalized_name)
);

-- Relationship tables --------------------------------------------------------

CREATE TABLE IF NOT EXISTS artist_labels (
    artist_id   INTEGER NOT NULL REFERENCES artists(id),
    label_id    INTEGER NOT NULL REFERENCES labels(id),
    source      TEXT DEFAULT '',      -- e.g. "deezer", "discogs", "musicbrainz"
    first_seen  TEXT NOT NULL,
    PRIMARY KEY (artist_id, label_id)
);

CREATE TABLE IF NOT EXISTS artist_songwriters (
    artist_id       INTEGER NOT NULL REFERENCES artists(id),
    songwriter_id   INTEGER NOT NULL REFERENCES songwriters(id),
    role            TEXT DEFAULT '',   -- "producer", "writer", "composer", "lyricist"
    source          TEXT DEFAULT '',
    first_seen      TEXT NOT NULL,
    PRIMARY KEY (artist_id, songwriter_id, role)
);

CREATE TABLE IF NOT EXISTS artist_publishers (
    artist_id       INTEGER NOT NULL REFERENCES artists(id),
    publisher_id    INTEGER NOT NULL REFERENCES publishers(id),
    source          TEXT DEFAULT '',
    first_seen      TEXT NOT NULL,
    PRIMARY KEY (artist_id, publisher_id)
);

CREATE TABLE IF NOT EXISTS artist_similar (
    artist_id           INTEGER NOT NULL REFERENCES artists(id),
    similar_artist_id   INTEGER NOT NULL REFERENCES artists(id),
    source              TEXT DEFAULT '',   -- "lastfm", "deezer"
    first_seen          TEXT NOT NULL,
    PRIMARY KEY (artist_id, similar_artist_id, source)
);

-- Intelligence tables --------------------------------------------------------

CREATE TABLE IF NOT EXISTS observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type     TEXT NOT NULL,     -- "artist", "label", "songwriter", "publisher"
    entity_id       INTEGER NOT NULL,
    obs_type        TEXT NOT NULL,     -- "red_flag", "green_flag", "note", "blocklist_hit"
    finding         TEXT NOT NULL,
    detail          TEXT DEFAULT '',
    source          TEXT DEFAULT '',   -- API or analysis that produced it
    strength        TEXT DEFAULT '',   -- "strong", "moderate", "weak"
    scan_id         INTEGER REFERENCES scans(id),
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id     TEXT,
    playlist_name   TEXT,
    scan_tier       TEXT,
    artist_count    INTEGER DEFAULT 0,
    started_at      TEXT NOT NULL,
    completed_at    TEXT
);

-- Review audit log ----------------------------------------------------------

CREATE TABLE IF NOT EXISTS review_log (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type             TEXT NOT NULL,
    entity_id               INTEGER NOT NULL,
    action                  TEXT NOT NULL,
    note                    TEXT DEFAULT '',
    connection_count_at_review INTEGER,
    timestamp               TEXT NOT NULL,
    blocklist_updated       TEXT DEFAULT ''
);

-- Entity aliases / relationships --------------------------------------------

CREATE TABLE IF NOT EXISTS entity_aliases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type_1   TEXT NOT NULL,
    entity_id_1     INTEGER NOT NULL,
    entity_type_2   TEXT NOT NULL,
    entity_id_2     INTEGER NOT NULL,
    relationship    TEXT DEFAULT 'alias',
    created_at      TEXT NOT NULL,
    note            TEXT DEFAULT '',
    UNIQUE(entity_type_1, entity_id_1, entity_type_2, entity_id_2)
);

-- Pre-computed context clues for review -------------------------------------

CREATE TABLE IF NOT EXISTS entity_context_clues (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type     TEXT NOT NULL,
    entity_id       INTEGER NOT NULL,
    clue_type       TEXT NOT NULL,
    clue_text       TEXT NOT NULL,
    severity        TEXT DEFAULT 'info',
    computed_at     TEXT NOT NULL
);

-- Per-artist scan results ---------------------------------------------------

CREATE TABLE IF NOT EXISTS scan_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER REFERENCES scans(id),
    artist_name     TEXT NOT NULL,
    verdict         TEXT NOT NULL,
    score           INTEGER,
    confidence      TEXT,
    threat_category TEXT,
    evidence_json   TEXT,
    UNIQUE(scan_id, artist_name)
);

-- API call logging ----------------------------------------------------------

CREATE TABLE IF NOT EXISTS api_calls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    api_name        TEXT NOT NULL,
    endpoint        TEXT DEFAULT '',
    artist_name     TEXT DEFAULT '',
    status_code     INTEGER,
    response_time_ms INTEGER,
    error_message   TEXT DEFAULT '',
    timestamp       TEXT NOT NULL
);

-- Indexes for common queries -------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_artists_threat ON artists(threat_status);
CREATE INDEX IF NOT EXISTS idx_artists_verdict ON artists(latest_verdict);
CREATE INDEX IF NOT EXISTS idx_labels_threat ON labels(threat_status);
CREATE INDEX IF NOT EXISTS idx_songwriters_threat ON songwriters(threat_status);
CREATE INDEX IF NOT EXISTS idx_observations_entity ON observations(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_observations_type ON observations(obs_type);
CREATE INDEX IF NOT EXISTS idx_artist_labels_label ON artist_labels(label_id);
CREATE INDEX IF NOT EXISTS idx_artist_songwriters_sw ON artist_songwriters(songwriter_id);
CREATE INDEX IF NOT EXISTS idx_review_log_entity ON review_log(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_context_clues_entity ON entity_context_clues(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_scan_results_artist ON scan_results(artist_name);
CREATE INDEX IF NOT EXISTS idx_scan_results_verdict ON scan_results(verdict);
CREATE INDEX IF NOT EXISTS idx_scan_results_scan ON scan_results(scan_id);
CREATE INDEX IF NOT EXISTS idx_api_calls_name ON api_calls(api_name);
CREATE INDEX IF NOT EXISTS idx_api_calls_timestamp ON api_calls(timestamp);
"""


# ---------------------------------------------------------------------------
# EntityDB class
# ---------------------------------------------------------------------------

class EntityDB:
    """SQLite-backed relational store for suspicious entities.

    Thread-safe: each thread gets its own SQLite connection via threading.local().
    """

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._in_batch = False
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create a thread-local SQLite connection."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    @property
    def _conn(self) -> sqlite3.Connection:
        """Thread-local connection property — drop-in replacement for self._conn."""
        return self._get_conn()

    def _init_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        # Migrations: add columns that may be missing on older DBs
        for col, typ in [
            ("setlistfm_id", "TEXT"),
            ("lastfm_url", "TEXT"),
            ("found_spotify", "INTEGER DEFAULT 0"),
            ("found_deezer", "INTEGER DEFAULT 0"),
            ("found_musicbrainz", "INTEGER DEFAULT 0"),
            ("found_genius", "INTEGER DEFAULT 0"),
            ("found_discogs", "INTEGER DEFAULT 0"),
            ("found_setlistfm", "INTEGER DEFAULT 0"),
            ("found_lastfm", "INTEGER DEFAULT 0"),
            ("platform_count", "INTEGER DEFAULT 0"),
            ("deezer_fans", "INTEGER"),
            ("lastfm_listeners", "INTEGER"),
            ("lastfm_playcount", "INTEGER"),
            ("scan_count", "INTEGER DEFAULT 0"),
            ("auto_promoted_at", "TEXT"),
        ]:
            try:
                self._conn.execute(f"ALTER TABLE artists ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass  # column already exists

        # Review columns on entity tables (labels, songwriters, publishers)
        for table in ("labels", "songwriters", "publishers"):
            for col, typ in [
                ("review_status", "TEXT DEFAULT 'not_queued'"),
                ("reviewed_at", "TEXT"),
                ("review_action", "TEXT"),
                ("review_note", "TEXT DEFAULT ''"),
                ("threshold_crossed_at", "TEXT"),
                ("dismiss_requeue_threshold", "INTEGER"),
            ]:
                try:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
                except sqlite3.OperationalError:
                    pass

        self._conn.commit()

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    @contextmanager
    def _tx(self):
        """Transaction context manager."""
        if self._in_batch:
            # Inside a batch — skip per-call commit
            yield self._conn
            return
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    @contextmanager
    def batch(self):
        """Wrap multiple operations in a single transaction for performance.

        Usage:
            with entity_db.batch():
                entity_db.upsert_artist(...)
                entity_db.upsert_label(...)
                entity_db.link_artist_label(...)
        """
        self._in_batch = True
        try:
            yield
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            self._in_batch = False

    # ------------------------------------------------------------------
    # Artists
    # ------------------------------------------------------------------

    def upsert_artist(
        self,
        name: str,
        *,
        spotify_id: str | None = None,
        deezer_id: int | None = None,
        musicbrainz_id: str | None = None,
        genius_id: int | None = None,
        discogs_id: int | None = None,
        setlistfm_id: str | None = None,
        lastfm_url: str | None = None,
        # Per-API found flags
        found_spotify: bool | None = None,
        found_deezer: bool | None = None,
        found_musicbrainz: bool | None = None,
        found_genius: bool | None = None,
        found_discogs: bool | None = None,
        found_setlistfm: bool | None = None,
        found_lastfm: bool | None = None,
        platform_count: int | None = None,
        # Metrics
        deezer_fans: int | None = None,
        lastfm_listeners: int | None = None,
        lastfm_playcount: int | None = None,
        # Threat & verdict
        threat_status: str = UNKNOWN,
        threat_category: float | None = None,
        latest_verdict: str | None = None,
        latest_confidence: str | None = None,
        country: str | None = None,
        genres: list[str] | None = None,
        notes: str = "",
    ) -> int:
        """Insert or update an artist. Returns the artist row id."""
        norm = _normalize(name)
        now = _now_iso()
        import json as _json

        # Build dict of non-None optional fields for update/insert
        optional: dict[str, Any] = {}
        if spotify_id: optional["spotify_id"] = spotify_id
        if deezer_id: optional["deezer_id"] = deezer_id
        if musicbrainz_id: optional["musicbrainz_id"] = musicbrainz_id
        if genius_id: optional["genius_id"] = genius_id
        if discogs_id: optional["discogs_id"] = discogs_id
        if setlistfm_id: optional["setlistfm_id"] = setlistfm_id
        if lastfm_url: optional["lastfm_url"] = lastfm_url
        if found_spotify is not None: optional["found_spotify"] = int(found_spotify)
        if found_deezer is not None: optional["found_deezer"] = int(found_deezer)
        if found_musicbrainz is not None: optional["found_musicbrainz"] = int(found_musicbrainz)
        if found_genius is not None: optional["found_genius"] = int(found_genius)
        if found_discogs is not None: optional["found_discogs"] = int(found_discogs)
        if found_setlistfm is not None: optional["found_setlistfm"] = int(found_setlistfm)
        if found_lastfm is not None: optional["found_lastfm"] = int(found_lastfm)
        if platform_count is not None: optional["platform_count"] = platform_count
        if deezer_fans is not None: optional["deezer_fans"] = deezer_fans
        if lastfm_listeners is not None: optional["lastfm_listeners"] = lastfm_listeners
        if lastfm_playcount is not None: optional["lastfm_playcount"] = lastfm_playcount
        if threat_status != UNKNOWN: optional["threat_status"] = threat_status
        if threat_category is not None: optional["threat_category"] = threat_category
        if latest_verdict: optional["latest_verdict"] = latest_verdict
        if latest_confidence: optional["latest_confidence"] = latest_confidence
        if country: optional["country"] = country
        if genres: optional["genres"] = _json.dumps(genres)
        if notes: optional["notes"] = notes

        with self._tx():
            row = self._conn.execute(
                "SELECT id FROM artists WHERE normalized_name = ?", (norm,)
            ).fetchone()

            if row:
                aid = row["id"]
                updates = ["last_seen = ?"]
                params: list[Any] = [now]
                for col, val in optional.items():
                    updates.append(f"{col} = ?")
                    params.append(val)
                params.append(aid)
                self._conn.execute(
                    f"UPDATE artists SET {', '.join(updates)} WHERE id = ?", params
                )
                return aid
            else:
                cols = ["name", "normalized_name", "first_seen", "last_seen"]
                vals: list[Any] = [name, norm, now, now]
                for col, val in optional.items():
                    cols.append(col)
                    vals.append(val)
                placeholders = ", ".join("?" for _ in cols)
                cur = self._conn.execute(
                    f"INSERT INTO artists ({', '.join(cols)}) VALUES ({placeholders})",
                    vals,
                )
                return cur.lastrowid

    def get_artist(self, name: str) -> dict | None:
        """Look up an artist by name (normalized match)."""
        row = self._conn.execute(
            "SELECT * FROM artists WHERE normalized_name = ?",
            (_normalize(name),),
        ).fetchone()
        return dict(row) if row else None

    def get_artist_by_id(self, artist_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM artists WHERE id = ?", (artist_id,)
        ).fetchone()
        return dict(row) if row else None

    def increment_scan_count(self, name: str, verdict: str = "", confidence: str = "") -> int:
        """Increment scan_count for an artist and update verdict. Returns new count."""
        norm = _normalize(name)
        now = _now_iso()
        with self._tx():
            row = self._conn.execute(
                "SELECT id, scan_count FROM artists WHERE normalized_name = ?",
                (norm,),
            ).fetchone()
            if row:
                new_count = (row["scan_count"] or 0) + 1
                updates = ["scan_count = ?", "last_seen = ?"]
                params: list = [new_count, now]
                if verdict:
                    updates.append("latest_verdict = ?")
                    params.append(verdict)
                if confidence:
                    updates.append("latest_confidence = ?")
                    params.append(confidence)
                params.append(row["id"])
                self._conn.execute(
                    f"UPDATE artists SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
                return new_count
        return 0

    def get_cowriter_overlap(self, artist_name: str, min_shared: int = 1) -> list[dict]:
        """Find artists sharing credited producers/songwriters with flagged artists.

        Returns list of {songwriter, flagged_artists: [...], status} dicts.
        """
        norm = _normalize(artist_name)
        row = self._conn.execute(
            "SELECT id FROM artists WHERE normalized_name = ?", (norm,)
        ).fetchone()
        if not row:
            return []

        artist_id = row["id"]

        # Get this artist's songwriters
        sws = self._conn.execute(
            "SELECT songwriter_id FROM artist_songwriters WHERE artist_id = ?",
            (artist_id,),
        ).fetchall()
        sw_ids = [r["songwriter_id"] for r in sws]

        if not sw_ids:
            return []

        # Single query to find all flagged artists sharing any songwriter
        placeholders = ",".join("?" * len(sw_ids))
        rows = self._conn.execute(
            f"""SELECT s.id as sw_id, s.name as sw_name,
                       a.name, a.threat_status
                FROM artist_songwriters asw
                JOIN artists a ON a.id = asw.artist_id
                JOIN songwriters s ON s.id = asw.songwriter_id
                WHERE asw.songwriter_id IN ({placeholders})
                  AND asw.artist_id != ?
                  AND a.threat_status IN ('confirmed_bad', 'suspected')""",
            (*sw_ids, artist_id),
        ).fetchall()

        # Group by songwriter
        from collections import defaultdict
        by_sw: dict[int, list[dict]] = defaultdict(list)
        sw_names: dict[int, str] = {}
        for r in rows:
            by_sw[r["sw_id"]].append({"name": r["name"], "status": r["threat_status"]})
            sw_names[r["sw_id"]] = r["sw_name"]

        overlaps: list[dict] = []
        for sw_id, flagged in by_sw.items():
            if len(flagged) >= min_shared:
                overlaps.append({
                    "songwriter": sw_names[sw_id],
                    "flagged_artists": flagged,
                })

        return overlaps

    # ------------------------------------------------------------------
    # Labels
    # ------------------------------------------------------------------

    def upsert_label(
        self,
        name: str,
        *,
        threat_status: str = UNKNOWN,
        notes: str = "",
    ) -> int:
        """Insert or update a label. Returns the label row id."""
        norm = _normalize(name)
        now = _now_iso()
        with self._tx():
            row = self._conn.execute(
                "SELECT id FROM labels WHERE normalized_name = ?", (norm,)
            ).fetchone()
            if row:
                lid = row["id"]
                if threat_status != UNKNOWN:
                    self._conn.execute(
                        "UPDATE labels SET threat_status=?, last_seen=? WHERE id=?",
                        (threat_status, now, lid),
                    )
                else:
                    self._conn.execute(
                        "UPDATE labels SET last_seen=? WHERE id=?", (now, lid)
                    )
                return lid
            else:
                cur = self._conn.execute(
                    """INSERT INTO labels
                       (name, normalized_name, threat_status, first_seen, last_seen, notes)
                       VALUES (?,?,?,?,?,?)""",
                    (name, norm, threat_status, now, now, notes),
                )
                return cur.lastrowid

    def get_label(self, name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM labels WHERE normalized_name = ?",
            (_normalize(name),),
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Songwriters
    # ------------------------------------------------------------------

    def upsert_songwriter(
        self,
        name: str,
        *,
        threat_status: str = UNKNOWN,
        notes: str = "",
    ) -> int:
        norm = _normalize(name)
        now = _now_iso()
        with self._tx():
            row = self._conn.execute(
                "SELECT id FROM songwriters WHERE normalized_name = ?", (norm,)
            ).fetchone()
            if row:
                sid = row["id"]
                if threat_status != UNKNOWN:
                    self._conn.execute(
                        "UPDATE songwriters SET threat_status=?, last_seen=? WHERE id=?",
                        (threat_status, now, sid),
                    )
                else:
                    self._conn.execute(
                        "UPDATE songwriters SET last_seen=? WHERE id=?", (now, sid)
                    )
                return sid
            else:
                cur = self._conn.execute(
                    """INSERT INTO songwriters
                       (name, normalized_name, threat_status, first_seen, last_seen, notes)
                       VALUES (?,?,?,?,?,?)""",
                    (name, norm, threat_status, now, now, notes),
                )
                return cur.lastrowid

    def get_songwriter(self, name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM songwriters WHERE normalized_name = ?",
            (_normalize(name),),
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Publishers
    # ------------------------------------------------------------------

    def upsert_publisher(
        self,
        name: str,
        *,
        threat_status: str = UNKNOWN,
        notes: str = "",
    ) -> int:
        norm = _normalize(name)
        now = _now_iso()
        with self._tx():
            row = self._conn.execute(
                "SELECT id FROM publishers WHERE normalized_name = ?", (norm,)
            ).fetchone()
            if row:
                pid = row["id"]
                if threat_status != UNKNOWN:
                    self._conn.execute(
                        "UPDATE publishers SET threat_status=?, last_seen=? WHERE id=?",
                        (threat_status, now, pid),
                    )
                else:
                    self._conn.execute(
                        "UPDATE publishers SET last_seen=? WHERE id=?", (now, pid)
                    )
                return pid
            else:
                cur = self._conn.execute(
                    """INSERT INTO publishers
                       (name, normalized_name, threat_status, first_seen, last_seen, notes)
                       VALUES (?,?,?,?,?,?)""",
                    (name, norm, threat_status, now, now, notes),
                )
                return cur.lastrowid

    # ------------------------------------------------------------------
    # Relationship links
    # ------------------------------------------------------------------

    def _maybe_commit(self) -> None:
        """Commit unless inside a batch()."""
        if not self._in_batch:
            self._conn.commit()

    def link_artist_label(
        self, artist_id: int, label_id: int, source: str = ""
    ) -> None:
        now = _now_iso()
        self._conn.execute(
            """INSERT OR IGNORE INTO artist_labels
               (artist_id, label_id, source, first_seen)
               VALUES (?,?,?,?)""",
            (artist_id, label_id, source, now),
        )
        self._maybe_commit()

    def link_artist_songwriter(
        self, artist_id: int, songwriter_id: int,
        role: str = "", source: str = "",
    ) -> None:
        now = _now_iso()
        self._conn.execute(
            """INSERT OR IGNORE INTO artist_songwriters
               (artist_id, songwriter_id, role, source, first_seen)
               VALUES (?,?,?,?,?)""",
            (artist_id, songwriter_id, role, source, now),
        )
        self._maybe_commit()

    def link_artist_publisher(
        self, artist_id: int, publisher_id: int, source: str = ""
    ) -> None:
        now = _now_iso()
        self._conn.execute(
            """INSERT OR IGNORE INTO artist_publishers
               (artist_id, publisher_id, source, first_seen)
               VALUES (?,?,?,?)""",
            (artist_id, publisher_id, source, now),
        )
        self._maybe_commit()

    def link_artist_similar(
        self, artist_id: int, similar_artist_id: int, source: str = ""
    ) -> None:
        now = _now_iso()
        self._conn.execute(
            """INSERT OR IGNORE INTO artist_similar
               (artist_id, similar_artist_id, source, first_seen)
               VALUES (?,?,?,?)""",
            (artist_id, similar_artist_id, source, now),
        )
        self._maybe_commit()

    # ------------------------------------------------------------------
    # Observations
    # ------------------------------------------------------------------

    def add_observation(
        self,
        entity_type: str,
        entity_id: int,
        obs_type: str,
        finding: str,
        detail: str = "",
        source: str = "",
        strength: str = "",
        scan_id: int | None = None,
    ) -> int:
        now = _now_iso()
        cur = self._conn.execute(
            """INSERT INTO observations
               (entity_type, entity_id, obs_type, finding, detail,
                source, strength, scan_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (entity_type, entity_id, obs_type, finding, detail,
             source, strength, scan_id, now),
        )
        self._maybe_commit()
        return cur.lastrowid

    def get_observations(
        self, entity_type: str, entity_id: int
    ) -> list[dict]:
        rows = self._conn.execute(
            """SELECT * FROM observations
               WHERE entity_type = ? AND entity_id = ?
               ORDER BY created_at DESC""",
            (entity_type, entity_id),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Scans
    # ------------------------------------------------------------------

    def start_scan(
        self,
        playlist_id: str = "",
        playlist_name: str = "",
        scan_tier: str = "",
        artist_count: int = 0,
    ) -> int:
        now = _now_iso()
        cur = self._conn.execute(
            """INSERT INTO scans
               (playlist_id, playlist_name, scan_tier, artist_count, started_at)
               VALUES (?,?,?,?,?)""",
            (playlist_id, playlist_name, scan_tier, artist_count, now),
        )
        self._maybe_commit()
        return cur.lastrowid

    def complete_scan(self, scan_id: int) -> None:
        now = _now_iso()
        self._conn.execute(
            "UPDATE scans SET completed_at = ? WHERE id = ?", (now, scan_id)
        )
        self._maybe_commit()

    # ------------------------------------------------------------------
    # Queries — entity networks
    # ------------------------------------------------------------------

    def get_artist_labels(self, artist_id: int) -> list[dict]:
        """Get all labels for an artist."""
        rows = self._conn.execute(
            """SELECT l.*, al.source, al.first_seen AS linked_at
               FROM labels l
               JOIN artist_labels al ON l.id = al.label_id
               WHERE al.artist_id = ?
               ORDER BY l.name""",
            (artist_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_label_artists(self, label_id: int) -> list[dict]:
        """Get all artists on a label."""
        rows = self._conn.execute(
            """SELECT a.*, al.source, al.first_seen AS linked_at
               FROM artists a
               JOIN artist_labels al ON a.id = al.artist_id
               WHERE al.label_id = ?
               ORDER BY a.name""",
            (label_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_artist_songwriters(self, artist_id: int) -> list[dict]:
        """Get all songwriters/producers for an artist."""
        rows = self._conn.execute(
            """SELECT s.*, asw.role, asw.source, asw.first_seen AS linked_at
               FROM songwriters s
               JOIN artist_songwriters asw ON s.id = asw.songwriter_id
               WHERE asw.artist_id = ?
               ORDER BY s.name""",
            (artist_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_songwriter_artists(self, songwriter_id: int) -> list[dict]:
        """Get all artists a songwriter has worked with."""
        rows = self._conn.execute(
            """SELECT a.*, asw.role, asw.source, asw.first_seen AS linked_at
               FROM artists a
               JOIN artist_songwriters asw ON a.id = asw.artist_id
               WHERE asw.songwriter_id = ?
               ORDER BY a.name""",
            (songwriter_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_similar_artists(self, artist_id: int) -> list[dict]:
        """Get artists flagged as similar/related."""
        rows = self._conn.execute(
            """SELECT a.*, asm.source, asm.first_seen AS linked_at
               FROM artists a
               JOIN artist_similar asm ON a.id = asm.similar_artist_id
               WHERE asm.artist_id = ?
               ORDER BY a.name""",
            (artist_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Queries — threat intelligence
    # ------------------------------------------------------------------

    def get_bad_entities(self, entity_type: str) -> list[dict]:
        """Get all confirmed_bad or suspected entities of a given type."""
        table = {"artist": "artists", "label": "labels",
                 "songwriter": "songwriters", "publisher": "publishers"}[entity_type]
        rows = self._conn.execute(
            f"SELECT * FROM {table} WHERE threat_status IN ('confirmed_bad', 'suspected') "
            f"ORDER BY threat_status, name",
        ).fetchall()
        return [dict(r) for r in rows]

    def get_shared_producers(self, min_artists: int = 3) -> list[dict]:
        """Find songwriters/producers who work with many artists.

        Returns rows with songwriter info + artist_count.
        """
        rows = self._conn.execute(
            """SELECT s.*, COUNT(DISTINCT asw.artist_id) AS linked_artist_count
               FROM songwriters s
               JOIN artist_songwriters asw ON s.id = asw.songwriter_id
               GROUP BY s.id
               HAVING linked_artist_count >= ?
               ORDER BY linked_artist_count DESC""",
            (min_artists,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_shared_labels(self, min_artists: int = 3) -> list[dict]:
        """Find labels that appear on many artists."""
        rows = self._conn.execute(
            """SELECT l.*, COUNT(DISTINCT al.artist_id) AS linked_artist_count
               FROM labels l
               JOIN artist_labels al ON l.id = al.label_id
               GROUP BY l.id
               HAVING linked_artist_count >= ?
               ORDER BY linked_artist_count DESC""",
            (min_artists,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_cowriter_network(self, artist_id: int) -> list[dict]:
        """Find other artists who share a producer/songwriter with this artist.

        Returns artists connected via shared songwriters, with the
        shared songwriter names.
        """
        rows = self._conn.execute(
            """SELECT DISTINCT a2.id, a2.name, a2.threat_status, a2.latest_verdict,
                      s.name AS shared_songwriter, asw2.role
               FROM artist_songwriters asw1
               JOIN artist_songwriters asw2 ON asw1.songwriter_id = asw2.songwriter_id
               JOIN artists a2 ON asw2.artist_id = a2.id
               JOIN songwriters s ON asw1.songwriter_id = s.id
               WHERE asw1.artist_id = ? AND asw2.artist_id != ?
               ORDER BY a2.name""",
            (artist_id, artist_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_label_network(self, artist_id: int) -> list[dict]:
        """Find other artists who share a label with this artist."""
        rows = self._conn.execute(
            """SELECT DISTINCT a2.id, a2.name, a2.threat_status, a2.latest_verdict,
                      l.name AS shared_label
               FROM artist_labels al1
               JOIN artist_labels al2 ON al1.label_id = al2.label_id
               JOIN artists a2 ON al2.artist_id = a2.id
               JOIN labels l ON al1.label_id = l.id
               WHERE al1.artist_id = ? AND al2.artist_id != ?
               ORDER BY a2.name""",
            (artist_id, artist_id),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        """Return summary counts for all tables."""
        result = {}
        for table in ("artists", "labels", "songwriters", "publishers",
                       "artist_labels", "artist_songwriters",
                       "artist_publishers", "artist_similar",
                       "observations", "scans"):
            row = self._conn.execute(f"SELECT COUNT(*) as n FROM {table}").fetchone()
            result[table] = row["n"]

        # Threat breakdowns
        for table in ("artists", "labels", "songwriters", "publishers"):
            rows = self._conn.execute(
                f"SELECT threat_status, COUNT(*) as n FROM {table} GROUP BY threat_status"
            ).fetchall()
            result[f"{table}_by_status"] = {r["threat_status"]: r["n"] for r in rows}

        return result

    # ------------------------------------------------------------------
    # Bulk import helpers
    # ------------------------------------------------------------------

    def import_blocklist_artists(self, names: list[str]) -> int:
        """Import known-bad artist names from blocklist."""
        count = 0
        for name in names:
            self.upsert_artist(name, threat_status=CONFIRMED_BAD,
                               notes="Imported from known_ai_artists blocklist")
            count += 1
        return count

    def import_blocklist_labels(self, names: list[str]) -> int:
        """Import known-bad label/distributor names from blocklist."""
        count = 0
        for name in names:
            self.upsert_label(name, threat_status=CONFIRMED_BAD,
                              notes="Imported from pfc_distributors blocklist")
            count += 1
        return count

    def import_blocklist_songwriters(self, names: list[str]) -> int:
        """Import known-bad songwriter names from blocklist."""
        count = 0
        for name in names:
            self.upsert_songwriter(name, threat_status=CONFIRMED_BAD,
                                   notes="Imported from pfc_songwriters blocklist")
            count += 1
        return count

    def import_enriched_profile(self, profile: dict) -> int | None:
        """Import a single enriched artist profile from Phase 1 output.

        Extracts: artist (with all API IDs + found flags), labels,
        contributors, similar artists.
        Returns the artist row id, or None on failure.
        """
        name = profile.get("artist_name", "")
        if not name:
            return None

        # Extract per-API data
        deezer = profile.get("deezer", {})
        mb = profile.get("musicbrainz", {})
        genius = profile.get("genius", {})
        discogs = profile.get("discogs", {})
        setlistfm = profile.get("setlistfm", {})
        lastfm = profile.get("lastfm", {})

        # Per-API found flags
        f_deezer = bool(deezer.get("found"))
        f_mb = bool(mb.get("found"))
        f_genius = bool(genius.get("found"))
        f_discogs = bool(discogs.get("found"))
        f_setlistfm = bool(setlistfm.get("found"))
        f_lastfm = bool(lastfm.get("found"))

        platform_count = profile.get("platform_count") or sum([
            f_deezer, f_mb, f_genius, f_discogs,
            f_setlistfm, f_lastfm,
        ])

        artist_id = self.upsert_artist(
            name,
            # IDs
            deezer_id=deezer.get("deezer_id") if f_deezer else None,
            musicbrainz_id=mb.get("mbid") if f_mb else None,
            genius_id=genius.get("genius_id") if f_genius else None,
            discogs_id=discogs.get("discogs_id") if f_discogs else None,
            setlistfm_id=setlistfm.get("setlistfm_id") or setlistfm.get("mbid") if f_setlistfm else None,
            lastfm_url=lastfm.get("url") if f_lastfm else None,
            # Found flags
            found_deezer=f_deezer,
            found_musicbrainz=f_mb,
            found_genius=f_genius,
            found_discogs=f_discogs,
            found_setlistfm=f_setlistfm,
            found_lastfm=f_lastfm,
            platform_count=platform_count,
            # Metrics
            deezer_fans=deezer.get("nb_fan") if f_deezer else None,
            lastfm_listeners=lastfm.get("listeners") if f_lastfm else None,
            lastfm_playcount=lastfm.get("playcount") if f_lastfm else None,
            # Metadata
            country=mb.get("country", ""),
            genres=mb.get("genres", []),
        )

        # Labels — from Deezer, Discogs, MusicBrainz
        all_labels: set[str] = set()
        if deezer.get("found"):
            for lbl in deezer.get("labels", []):
                if lbl:
                    all_labels.add(lbl)
            for album in deezer.get("albums", []):
                if isinstance(album, dict) and album.get("label"):
                    all_labels.add(album["label"])
        if discogs.get("found"):
            for lbl in discogs.get("labels", []):
                if lbl:
                    all_labels.add(lbl)
        if mb.get("found"):
            for lbl in mb.get("labels", []):
                if lbl:
                    all_labels.add(lbl)

        for lbl in all_labels:
            label_id = self.upsert_label(lbl)
            self.link_artist_label(artist_id, label_id, source="enrichment")

        # Contributors — from Deezer
        if deezer.get("found"):
            roles = deezer.get("contributor_roles", {})
            for role, names in roles.items():
                if not isinstance(names, list):
                    continue
                for sw_name in names:
                    if sw_name and sw_name.lower() != name.lower():
                        sw_id = self.upsert_songwriter(sw_name)
                        self.link_artist_songwriter(
                            artist_id, sw_id, role=role, source="deezer"
                        )

            # Flat contributors list as fallback
            if not roles:
                for sw_name in deezer.get("contributors", []):
                    if isinstance(sw_name, str) and sw_name.lower() != name.lower():
                        sw_id = self.upsert_songwriter(sw_name)
                        self.link_artist_songwriter(
                            artist_id, sw_id, source="deezer"
                        )

        # Similar / related artists
        similar_names: set[str] = set()
        lastfm = profile.get("lastfm", {})
        if lastfm.get("found"):
            for s in lastfm.get("similar_artists", []):
                if isinstance(s, str) and s:
                    similar_names.add(s)
        if deezer.get("found"):
            for r in deezer.get("related_artists", []):
                if isinstance(r, str) and r:
                    similar_names.add(r)
                elif isinstance(r, dict) and r.get("name"):
                    similar_names.add(r["name"])

        for sim_name in similar_names:
            sim_id = self.upsert_artist(sim_name)
            self.link_artist_similar(artist_id, sim_id, source="enrichment")

        return artist_id

    def refresh_entity_counts(self) -> None:
        """Update cached artist_count on labels and songwriters."""
        self._conn.execute(
            """UPDATE labels SET artist_count = (
                   SELECT COUNT(DISTINCT artist_id)
                   FROM artist_labels WHERE label_id = labels.id
               )"""
        )
        self._conn.execute(
            """UPDATE songwriters SET artist_count = (
                   SELECT COUNT(DISTINCT artist_id)
                   FROM artist_songwriters WHERE songwriter_id = songwriters.id
               )"""
        )
        self._conn.execute(
            """UPDATE publishers SET artist_count = (
                   SELECT COUNT(DISTINCT artist_id)
                   FROM artist_publishers WHERE publisher_id = publishers.id
               )"""
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Review queue — threshold checking
    # ------------------------------------------------------------------

    def _entity_table(self, entity_type: str) -> str:
        """Map entity_type to table name."""
        return {"artist": "artists", "label": "labels",
                "songwriter": "songwriters", "publisher": "publishers"}[entity_type]

    def _flagged_artist_count(self, entity_type: str, entity_id: int) -> int:
        """Count how many flagged artists are connected to this entity.

        'Flagged' means the artist has a verdict of Suspicious or Likely Artificial,
        OR a threat_status of confirmed_bad or suspected.
        """
        if entity_type == "label":
            row = self._conn.execute(
                """SELECT COUNT(DISTINCT a.id) AS cnt
                   FROM artist_labels al
                   JOIN artists a ON a.id = al.artist_id
                   WHERE al.label_id = ?
                     AND (a.threat_status IN ('confirmed_bad', 'suspected')
                          OR a.latest_verdict IN ('Suspicious', 'Likely Artificial'))""",
                (entity_id,),
            ).fetchone()
        elif entity_type == "songwriter":
            row = self._conn.execute(
                """SELECT COUNT(DISTINCT a.id) AS cnt
                   FROM artist_songwriters asw
                   JOIN artists a ON a.id = asw.artist_id
                   WHERE asw.songwriter_id = ?
                     AND (a.threat_status IN ('confirmed_bad', 'suspected')
                          OR a.latest_verdict IN ('Suspicious', 'Likely Artificial'))""",
                (entity_id,),
            ).fetchone()
        elif entity_type == "publisher":
            row = self._conn.execute(
                """SELECT COUNT(DISTINCT a.id) AS cnt
                   FROM artist_publishers ap
                   JOIN artists a ON a.id = ap.artist_id
                   WHERE ap.publisher_id = ?
                     AND (a.threat_status IN ('confirmed_bad', 'suspected')
                          OR a.latest_verdict IN ('Suspicious', 'Likely Artificial'))""",
                (entity_id,),
            ).fetchone()
        else:
            return 0
        return row["cnt"] if row else 0

    def check_threshold_and_queue(
        self, entity_type: str, entity_id: int
    ) -> bool:
        """Check if an entity has crossed its review threshold.

        If it has, set review_status to pending_review.
        Auto-confirms exact matches against the existing bad actor database
        (entities already marked confirmed_bad skip the queue).

        Returns True if the entity was queued (or was already queued).
        """
        if entity_type not in REVIEW_THRESHOLDS:
            return False

        table = self._entity_table(entity_type)
        row = self._conn.execute(
            f"SELECT * FROM {table} WHERE id = ?", (entity_id,)
        ).fetchone()
        if not row:
            return False

        entity = dict(row)

        # Already confirmed — no need to queue
        if entity.get("threat_status") == CONFIRMED_BAD:
            return False

        # Already in the review queue or reviewed
        if entity.get("review_status") in (PENDING_REVIEW, REVIEWED):
            return True

        # Dismissed entities only re-queue if count exceeds their threshold
        if entity.get("review_status") == "dismissed":
            requeue_threshold = entity.get("dismiss_requeue_threshold") or 0
            flagged_count = self._flagged_artist_count(entity_type, entity_id)
            if flagged_count <= requeue_threshold:
                return False
        else:
            flagged_count = self._flagged_artist_count(entity_type, entity_id)

        threshold = REVIEW_THRESHOLDS[entity_type]
        if flagged_count >= threshold:
            now = _now_iso()
            with self._tx():
                self._conn.execute(
                    f"""UPDATE {table}
                        SET review_status = ?, threshold_crossed_at = ?
                        WHERE id = ?""",
                    (PENDING_REVIEW, now, entity_id),
                )
            logger.info(
                "Entity %s #%d (%s) queued for review — %d flagged connections",
                entity_type, entity_id, entity.get("name", "?"), flagged_count,
            )
            return True

        return False

    def check_all_thresholds(self) -> dict[str, int]:
        """Re-check thresholds for all entities. Returns counts of newly queued."""
        result: dict[str, int] = {}
        for etype in ("label", "songwriter", "publisher"):
            table = self._entity_table(etype)
            rows = self._conn.execute(
                f"""SELECT id FROM {table}
                    WHERE threat_status != 'confirmed_bad'
                      AND review_status NOT IN ('pending_review', 'reviewed')"""
            ).fetchall()
            count = 0
            for row in rows:
                if self.check_threshold_and_queue(etype, row["id"]):
                    count += 1
            result[etype] = count
        return result

    # ------------------------------------------------------------------
    # Review queue — queries
    # ------------------------------------------------------------------

    def get_review_queue(
        self,
        entity_type: str | None = None,
        review_status: str | None = None,
        min_count: int | None = None,
        max_count: int | None = None,
        sort_by: str = "connection_count",
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Get entities in the review queue with filtering and sorting.

        Args:
            entity_type: Filter by type (label, songwriter, publisher).
            review_status: Filter by review_status (pending_review, deferred).
                           Defaults to pending_review if not specified.
            min_count: Minimum artist_count filter.
            max_count: Maximum artist_count filter.
            sort_by: 'connection_count' (desc) or 'threshold_date' (newest first).
            limit: Max results.
            offset: Pagination offset.
        """
        if review_status is None:
            review_status = PENDING_REVIEW

        types_to_query = [entity_type] if entity_type else ["label", "songwriter", "publisher"]
        results: list[dict] = []

        for etype in types_to_query:
            table = self._entity_table(etype)
            conditions = ["review_status = ?"]
            params: list[Any] = [review_status]

            if min_count is not None:
                conditions.append("artist_count >= ?")
                params.append(min_count)
            if max_count is not None:
                conditions.append("artist_count <= ?")
                params.append(max_count)

            where = " AND ".join(conditions)
            order = "artist_count DESC" if sort_by == "connection_count" else "threshold_crossed_at DESC"

            rows = self._conn.execute(
                f"SELECT *, '{etype}' AS entity_type FROM {table} WHERE {where} ORDER BY {order}",
                params,
            ).fetchall()
            results.extend(dict(r) for r in rows)

        # Sort merged results
        if sort_by == "connection_count":
            results.sort(key=lambda r: r.get("artist_count", 0), reverse=True)
        else:
            results.sort(key=lambda r: r.get("threshold_crossed_at", ""), reverse=True)

        return results[offset:offset + limit]

    def get_review_queue_stats(self) -> dict:
        """Summary counts for the review queue dashboard."""
        stats: dict[str, Any] = {"pending": {}, "deferred": {}, "reviewed": {}}
        for etype in ("label", "songwriter", "publisher"):
            table = self._entity_table(etype)
            for status_key, status_val in [
                ("pending", PENDING_REVIEW),
                ("deferred", DEFERRED),
                ("reviewed", REVIEWED),
            ]:
                row = self._conn.execute(
                    f"SELECT COUNT(*) AS cnt FROM {table} WHERE review_status = ?",
                    (status_val,),
                ).fetchone()
                stats[status_key][etype] = row["cnt"] if row else 0

        # Total pending
        stats["total_pending"] = sum(stats["pending"].values())
        stats["total_deferred"] = sum(stats["deferred"].values())
        stats["total_reviewed"] = sum(stats["reviewed"].values())

        # Recent review velocity
        row = self._conn.execute(
            """SELECT COUNT(*) AS cnt FROM review_log
               WHERE timestamp >= datetime('now', '-7 days')"""
        ).fetchone()
        stats["reviews_last_7_days"] = row["cnt"] if row else 0

        row = self._conn.execute(
            """SELECT COUNT(*) AS cnt FROM review_log
               WHERE timestamp >= datetime('now', '-30 days')"""
        ).fetchone()
        stats["reviews_last_30_days"] = row["cnt"] if row else 0

        # Confirm / dismiss / defer breakdown for recent reviews
        rows = self._conn.execute(
            """SELECT action, COUNT(*) AS cnt FROM review_log
               WHERE timestamp >= datetime('now', '-30 days')
               GROUP BY action"""
        ).fetchall()
        stats["action_breakdown_30d"] = {r["action"]: r["cnt"] for r in rows}

        return stats

    # ------------------------------------------------------------------
    # Review queue — entity detail for CMS
    # ------------------------------------------------------------------

    def get_entity_detail(self, entity_type: str, entity_id: int) -> dict | None:
        """Full entity detail for the review CMS, including connected artists."""
        table = self._entity_table(entity_type)
        row = self._conn.execute(
            f"SELECT * FROM {table} WHERE id = ?", (entity_id,)
        ).fetchone()
        if not row:
            return None

        detail = dict(row)
        detail["entity_type"] = entity_type

        # Connected artists with their verdicts
        if entity_type == "label":
            artists = self._conn.execute(
                """SELECT a.id, a.name, a.threat_status, a.latest_verdict,
                          a.latest_confidence, a.threat_category, a.platform_count,
                          al.source, al.first_seen AS linked_at
                   FROM artists a
                   JOIN artist_labels al ON a.id = al.artist_id
                   WHERE al.label_id = ?
                   ORDER BY a.latest_verdict, a.name""",
                (entity_id,),
            ).fetchall()
        elif entity_type == "songwriter":
            artists = self._conn.execute(
                """SELECT a.id, a.name, a.threat_status, a.latest_verdict,
                          a.latest_confidence, a.threat_category, a.platform_count,
                          asw.role, asw.source, asw.first_seen AS linked_at
                   FROM artists a
                   JOIN artist_songwriters asw ON a.id = asw.artist_id
                   WHERE asw.songwriter_id = ?
                   ORDER BY a.latest_verdict, a.name""",
                (entity_id,),
            ).fetchall()
        elif entity_type == "publisher":
            artists = self._conn.execute(
                """SELECT a.id, a.name, a.threat_status, a.latest_verdict,
                          a.latest_confidence, a.threat_category, a.platform_count,
                          ap.source, ap.first_seen AS linked_at
                   FROM artists a
                   JOIN artist_publishers ap ON a.id = ap.artist_id
                   WHERE ap.publisher_id = ?
                   ORDER BY a.latest_verdict, a.name""",
                (entity_id,),
            ).fetchall()
        else:
            artists = []

        detail["connected_artists"] = [dict(a) for a in artists]

        # Flagged vs total counts
        total = len(detail["connected_artists"])
        flagged = sum(
            1 for a in detail["connected_artists"]
            if a.get("threat_status") in ("confirmed_bad", "suspected")
            or a.get("latest_verdict") in ("Suspicious", "Likely Artificial")
        )
        detail["total_artist_count"] = total
        detail["flagged_artist_count"] = flagged

        # Context clues
        detail["context_clues"] = self.get_context_clues(entity_type, entity_id)

        # Observations
        detail["observations"] = self.get_observations(entity_type, entity_id)

        # Review history
        detail["review_history"] = self.get_review_history(entity_type, entity_id)

        # Aliases
        detail["aliases"] = self.get_entity_aliases(entity_type, entity_id)

        # External investigation links
        detail["investigation_links"] = self._build_investigation_links(
            entity_type, detail.get("name", "")
        )

        return detail

    def _build_investigation_links(self, entity_type: str, name: str) -> list[dict]:
        """Build external investigation URLs for quick reviewer lookup."""
        import urllib.parse
        q = urllib.parse.quote_plus(name)
        links = [
            {"label": "Google", "url": f"https://www.google.com/search?q=%22{q}%22+music"},
        ]
        if entity_type in ("songwriter", "publisher"):
            links.extend([
                {"label": "Genius", "url": f"https://genius.com/search?q={q}"},
                {"label": "MusicBrainz", "url": f"https://musicbrainz.org/search?query={q}&type=artist"},
                {"label": "ASCAP Repertory", "url": f"https://www.ascap.com/repertory#/ace/search/writer/{q}"},
                {"label": "BMI Repertory", "url": f"https://repertoire.bmi.com/Search/Search?Main_Search_Text={q}&Main_Search=Catalog"},
            ])
        elif entity_type == "label":
            links.extend([
                {"label": "Discogs", "url": f"https://www.discogs.com/search/?q={q}&type=label"},
                {"label": "MusicBrainz", "url": f"https://musicbrainz.org/search?query={q}&type=label"},
            ])
        return links

    # ------------------------------------------------------------------
    # Review actions
    # ------------------------------------------------------------------

    def submit_review(
        self,
        entity_type: str,
        entity_id: int,
        action: str,
        note: str = "",
    ) -> dict:
        """Submit a review decision for an entity.

        Args:
            action: 'confirmed_bad', 'dismissed', or 'deferred'.
            note: Free-text justification.

        Returns dict with 'success', 'blocklist_updated', and summary info.
        """
        if action not in _VALID_REVIEW_ACTIONS:
            return {"success": False, "error": f"Invalid action: {action}"}

        table = self._entity_table(entity_type)
        row = self._conn.execute(
            f"SELECT * FROM {table} WHERE id = ?", (entity_id,)
        ).fetchone()
        if not row:
            return {"success": False, "error": "Entity not found"}

        entity = dict(row)
        now = _now_iso()
        flagged_count = self._flagged_artist_count(entity_type, entity_id)
        blocklist_updated = ""

        with self._tx():
            if action == ACTION_CONFIRM:
                # Promote to confirmed_bad in the entity table
                self._conn.execute(
                    f"""UPDATE {table}
                        SET threat_status = 'confirmed_bad',
                            review_status = 'reviewed',
                            reviewed_at = ?,
                            review_action = ?,
                            review_note = ?
                        WHERE id = ?""",
                    (now, action, note, entity_id),
                )
                blocklist_updated = self._blocklist_for_entity_type(entity_type)

            elif action == ACTION_DISMISS:
                # Set a requeue threshold at 150% of current count
                requeue_at = int(flagged_count * 1.5) + 1
                self._conn.execute(
                    f"""UPDATE {table}
                        SET review_status = 'reviewed',
                            reviewed_at = ?,
                            review_action = 'dismissed',
                            review_note = ?,
                            dismiss_requeue_threshold = ?
                        WHERE id = ?""",
                    (now, note, requeue_at, entity_id),
                )

            elif action == ACTION_DEFER:
                self._conn.execute(
                    f"""UPDATE {table}
                        SET review_status = 'deferred',
                            reviewed_at = ?,
                            review_action = 'deferred',
                            review_note = ?
                        WHERE id = ?""",
                    (now, note, entity_id),
                )

            # Write audit log
            self._conn.execute(
                """INSERT INTO review_log
                   (entity_type, entity_id, action, note,
                    connection_count_at_review, timestamp, blocklist_updated)
                   VALUES (?,?,?,?,?,?,?)""",
                (entity_type, entity_id, action, note,
                 flagged_count, now, blocklist_updated),
            )

        return {
            "success": True,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "entity_name": entity.get("name", ""),
            "action": action,
            "flagged_count": flagged_count,
            "blocklist_updated": blocklist_updated,
        }

    @staticmethod
    def _blocklist_for_entity_type(entity_type: str) -> str:
        """Map entity type to the blocklist JSON file it belongs to."""
        return {
            "label": "pfc_distributors.json",
            "songwriter": "pfc_songwriters.json",
            "publisher": "pfc_distributors.json",
            "artist": "known_ai_artists.json",
        }.get(entity_type, "")

    def add_entity_note(
        self, entity_type: str, entity_id: int, note: str
    ) -> bool:
        """Append a note to an entity's review_note field."""
        table = self._entity_table(entity_type)
        row = self._conn.execute(
            f"SELECT review_note FROM {table} WHERE id = ?", (entity_id,)
        ).fetchone()
        if not row:
            return False

        existing = row["review_note"] or ""
        now = _now_iso()
        updated = f"{existing}\n[{now}] {note}".strip()
        with self._tx():
            self._conn.execute(
                f"UPDATE {table} SET review_note = ? WHERE id = ?",
                (updated, entity_id),
            )
        return True

    # ------------------------------------------------------------------
    # Review history / audit log
    # ------------------------------------------------------------------

    def get_review_history(
        self,
        entity_type: str | None = None,
        entity_id: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Get review audit log entries, optionally filtered."""
        conditions = []
        params: list[Any] = []
        if entity_type:
            conditions.append("rl.entity_type = ?")
            params.append(entity_type)
        if entity_id:
            conditions.append("rl.entity_id = ?")
            params.append(entity_id)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._conn.execute(
            f"""SELECT rl.*
                FROM review_log rl
                {where}
                ORDER BY rl.timestamp DESC
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Entity aliases
    # ------------------------------------------------------------------

    def link_entity_alias(
        self,
        entity_type_1: str,
        entity_id_1: int,
        entity_type_2: str,
        entity_id_2: int,
        relationship: str = "alias",
        note: str = "",
    ) -> int:
        """Create an alias/relationship link between two entities."""
        now = _now_iso()
        with self._tx():
            cur = self._conn.execute(
                """INSERT OR IGNORE INTO entity_aliases
                   (entity_type_1, entity_id_1, entity_type_2, entity_id_2,
                    relationship, created_at, note)
                   VALUES (?,?,?,?,?,?,?)""",
                (entity_type_1, entity_id_1, entity_type_2, entity_id_2,
                 relationship, now, note),
            )
            return cur.lastrowid

    def get_entity_aliases(self, entity_type: str, entity_id: int) -> list[dict]:
        """Get all aliases for an entity (in either direction)."""
        rows = self._conn.execute(
            """SELECT * FROM entity_aliases
               WHERE (entity_type_1 = ? AND entity_id_1 = ?)
                  OR (entity_type_2 = ? AND entity_id_2 = ?)
               ORDER BY created_at DESC""",
            (entity_type, entity_id, entity_type, entity_id),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Context clues
    # ------------------------------------------------------------------

    def compute_context_clues(self, entity_type: str, entity_id: int) -> list[dict]:
        """Auto-generate context clues for an entity to help reviewers.

        Returns the list of generated clues.
        """
        table = self._entity_table(entity_type)
        row = self._conn.execute(
            f"SELECT * FROM {table} WHERE id = ?", (entity_id,)
        ).fetchone()
        if not row:
            return []

        entity = dict(row)
        clues: list[dict] = []
        now = _now_iso()

        # Get connected artists
        if entity_type == "label":
            artists = self._conn.execute(
                """SELECT a.* FROM artists a
                   JOIN artist_labels al ON a.id = al.artist_id
                   WHERE al.label_id = ?""",
                (entity_id,),
            ).fetchall()
        elif entity_type == "songwriter":
            artists = self._conn.execute(
                """SELECT a.* FROM artists a
                   JOIN artist_songwriters asw ON a.id = asw.artist_id
                   WHERE asw.songwriter_id = ?""",
                (entity_id,),
            ).fetchall()
        elif entity_type == "publisher":
            artists = self._conn.execute(
                """SELECT a.* FROM artists a
                   JOIN artist_publishers ap ON a.id = ap.artist_id
                   WHERE ap.publisher_id = ?""",
                (entity_id,),
            ).fetchall()
        else:
            artists = []

        artists = [dict(a) for a in artists]
        flagged = [a for a in artists if a.get("threat_status") in ("confirmed_bad", "suspected")
                   or a.get("latest_verdict") in ("Suspicious", "Likely Artificial")]
        total = len(artists)

        if not artists:
            return clues

        # Genre clustering
        import json as _json
        all_genres: list[str] = []
        for a in artists:
            try:
                genres = _json.loads(a.get("genres") or "[]")
                all_genres.extend(genres)
            except (ValueError, TypeError):
                pass
        if all_genres:
            from collections import Counter
            genre_counts = Counter(all_genres)
            top = genre_counts.most_common(3)
            if top:
                top_str = ", ".join(f"{g} ({c})" for g, c in top)
                clues.append({
                    "clue_type": "genre_clustering",
                    "clue_text": f"Connected artists cluster in genres: {top_str}",
                    "severity": "info",
                })

        # Platform absence
        if total > 0:
            avg_platforms = sum(a.get("platform_count", 0) for a in artists) / total
            if avg_platforms < 2.5:
                clues.append({
                    "clue_type": "platform_absence",
                    "clue_text": f"Connected artists average only {avg_platforms:.1f} platforms (low)",
                    "severity": "warning",
                })

        # Flagged ratio
        if total >= 3:
            pct = len(flagged) / total * 100
            if pct >= 50:
                clues.append({
                    "clue_type": "flagged_ratio",
                    "clue_text": f"{len(flagged)} of {total} connected artists ({pct:.0f}%) are flagged",
                    "severity": "critical",
                })

        # Shared label co-occurrence (for songwriter entities)
        if entity_type == "songwriter" and flagged:
            label_counts: dict[str, int] = {}
            for a in flagged:
                labels = self._conn.execute(
                    """SELECT l.name FROM labels l
                       JOIN artist_labels al ON l.id = al.label_id
                       WHERE al.artist_id = ?""",
                    (a["id"],),
                ).fetchall()
                for lbl in labels:
                    label_counts[lbl["name"]] = label_counts.get(lbl["name"], 0) + 1
            for lname, lcount in label_counts.items():
                if lcount >= 2:
                    clues.append({
                        "clue_type": "label_cooccurrence",
                        "clue_text": f"{lcount} of {len(flagged)} flagged artists share label: {lname}",
                        "severity": "warning",
                    })

        # Network proximity to confirmed bad actors
        if entity_type in ("label", "songwriter"):
            bad_neighbors = self._conn.execute(
                """SELECT DISTINCT e.name, e.entity_type
                   FROM entity_aliases ea
                   JOIN (
                       SELECT id, name, 'label' AS entity_type FROM labels WHERE threat_status = 'confirmed_bad'
                       UNION ALL
                       SELECT id, name, 'songwriter' AS entity_type FROM songwriters WHERE threat_status = 'confirmed_bad'
                   ) e ON (ea.entity_id_2 = e.id AND ea.entity_type_2 = e.entity_type)
                   WHERE ea.entity_type_1 = ? AND ea.entity_id_1 = ?""",
                (entity_type, entity_id),
            ).fetchall()
            for bn in bad_neighbors:
                clues.append({
                    "clue_type": "network_proximity",
                    "clue_text": f"Aliased to confirmed bad actor: {bn['name']} ({bn['entity_type']})",
                    "severity": "critical",
                })

        # Store computed clues
        with self._tx():
            # Clear old clues
            self._conn.execute(
                "DELETE FROM entity_context_clues WHERE entity_type = ? AND entity_id = ?",
                (entity_type, entity_id),
            )
            for clue in clues:
                self._conn.execute(
                    """INSERT INTO entity_context_clues
                       (entity_type, entity_id, clue_type, clue_text, severity, computed_at)
                       VALUES (?,?,?,?,?,?)""",
                    (entity_type, entity_id, clue["clue_type"],
                     clue["clue_text"], clue["severity"], now),
                )

        return clues

    def get_context_clues(self, entity_type: str, entity_id: int) -> list[dict]:
        """Get stored context clues for an entity."""
        rows = self._conn.execute(
            """SELECT * FROM entity_context_clues
               WHERE entity_type = ? AND entity_id = ?
               ORDER BY severity DESC, computed_at DESC""",
            (entity_type, entity_id),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Scan results
    # ------------------------------------------------------------------

    def store_scan_result(
        self,
        scan_id: int,
        artist_name: str,
        verdict: str,
        score: int,
        confidence: str = "",
        threat_category: str = "",
        evidence_json: str = "",
    ) -> int:
        """Store a per-artist result from a scan."""
        with self._tx():
            cur = self._conn.execute(
                """INSERT OR REPLACE INTO scan_results
                   (scan_id, artist_name, verdict, score, confidence,
                    threat_category, evidence_json)
                   VALUES (?,?,?,?,?,?,?)""",
                (scan_id, artist_name, verdict, score, confidence,
                 threat_category, evidence_json),
            )
            return cur.lastrowid

    def get_scan_results(self, scan_id: int) -> list[dict]:
        """Get all artist results for a scan."""
        rows = self._conn.execute(
            """SELECT * FROM scan_results
               WHERE scan_id = ?
               ORDER BY score ASC""",
            (scan_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_scan_history(
        self, limit: int = 50, offset: int = 0
    ) -> list[dict]:
        """Get recent scans with summary info."""
        rows = self._conn.execute(
            """SELECT s.*,
                      COUNT(sr.id) AS result_count,
                      SUM(CASE WHEN sr.verdict IN ('Suspicious', 'Likely Artificial') THEN 1 ELSE 0 END) AS flagged_count
               FROM scans s
               LEFT JOIN scan_results sr ON sr.scan_id = s.id
               GROUP BY s.id
               ORDER BY s.started_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_scan_detail(self, scan_id: int) -> dict | None:
        """Get a scan with its full results."""
        row = self._conn.execute(
            "SELECT * FROM scans WHERE id = ?", (scan_id,)
        ).fetchone()
        if not row:
            return None
        scan = dict(row)
        scan["results"] = self.get_scan_results(scan_id)
        return scan

    def get_artist_scan_history(self, artist_name: str, limit: int = 20) -> list[dict]:
        """Get all past scan results for a specific artist."""
        rows = self._conn.execute(
            """SELECT sr.*, s.playlist_name, s.started_at AS scan_date
               FROM scan_results sr
               JOIN scans s ON s.id = sr.scan_id
               WHERE sr.artist_name = ?
               ORDER BY s.started_at DESC
               LIMIT ?""",
            (artist_name, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # API call logging
    # ------------------------------------------------------------------

    def log_api_call(
        self,
        api_name: str,
        endpoint: str = "",
        artist_name: str = "",
        status_code: int | None = None,
        response_time_ms: int | None = None,
        error_message: str = "",
    ) -> None:
        """Log an external API call for health monitoring."""
        now = _now_iso()
        self._conn.execute(
            """INSERT INTO api_calls
               (api_name, endpoint, artist_name, status_code,
                response_time_ms, error_message, timestamp)
               VALUES (?,?,?,?,?,?,?)""",
            (api_name, endpoint, artist_name, status_code,
             response_time_ms, error_message, now),
        )
        self._maybe_commit()

    def get_api_health(self, hours: int = 24) -> list[dict]:
        """Get API health summary for the last N hours."""
        rows = self._conn.execute(
            """SELECT
                   api_name,
                   COUNT(*) AS total_calls,
                   SUM(CASE WHEN status_code >= 200 AND status_code < 300 THEN 1 ELSE 0 END) AS success_count,
                   SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) AS error_count,
                   SUM(CASE WHEN error_message != '' THEN 1 ELSE 0 END) AS failure_count,
                   ROUND(AVG(response_time_ms), 0) AS avg_response_ms,
                   MAX(timestamp) AS last_call
               FROM api_calls
               WHERE timestamp >= datetime('now', ?)
               GROUP BY api_name
               ORDER BY api_name""",
            (f"-{hours} hours",),
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            total = d["total_calls"] or 1
            d["success_rate"] = round((d["success_count"] or 0) / total * 100, 1)
            d["error_rate"] = round(((d["error_count"] or 0) + (d["failure_count"] or 0)) / total * 100, 1)
            # Health status
            if d["error_rate"] > 20:
                d["health"] = "red"
            elif d["error_rate"] > 5:
                d["health"] = "yellow"
            else:
                d["health"] = "green"
            results.append(d)
        return results

    # ------------------------------------------------------------------
    # Blocklist export / sync
    # ------------------------------------------------------------------

    def export_blocklist(self, entity_type: str) -> list[str]:
        """Export confirmed_bad entity names for a given type, for blocklist JSON."""
        table = self._entity_table(entity_type)
        rows = self._conn.execute(
            f"SELECT name FROM {table} WHERE threat_status = 'confirmed_bad' ORDER BY name"
        ).fetchall()
        return [r["name"] for r in rows]

    def sync_blocklists(self) -> dict[str, int]:
        """Regenerate all blocklist JSON files from confirmed entities.

        Returns dict of {filename: count} for each blocklist written.
        """
        import json as _json
        blocklist_dir = Path(__file__).parent / "blocklists"
        blocklist_dir.mkdir(exist_ok=True)

        result: dict[str, int] = {}

        # pfc_distributors.json ← confirmed_bad labels
        labels = self.export_blocklist("label")
        path = blocklist_dir / "pfc_distributors.json"
        path.write_text(_json.dumps(sorted(set(labels)), indent=2) + "\n")
        result["pfc_distributors.json"] = len(labels)

        # known_ai_artists.json ← confirmed_bad artists
        artists = self.export_blocklist("artist")
        path = blocklist_dir / "known_ai_artists.json"
        path.write_text(_json.dumps(sorted(set(artists)), indent=2) + "\n")
        result["known_ai_artists.json"] = len(artists)

        # pfc_songwriters.json ← confirmed_bad songwriters
        songwriters = self.export_blocklist("songwriter")
        path = blocklist_dir / "pfc_songwriters.json"
        path.write_text(_json.dumps(sorted(set(songwriters)), indent=2) + "\n")
        result["pfc_songwriters.json"] = len(songwriters)

        return result

    # ------------------------------------------------------------------
    # Network graph data (for visualization)
    # ------------------------------------------------------------------

    def get_network_graph(
        self,
        entity_type: str | None = None,
        entity_id: int | None = None,
        min_connections: int = 2,
    ) -> dict:
        """Build network graph data for visualization (nodes + edges).

        If entity_type/entity_id are provided, returns the subgraph
        around that entity. Otherwise returns the full graph of entities
        meeting the min_connections threshold.
        """
        nodes: list[dict] = []
        edges: list[dict] = []
        seen_nodes: set[str] = set()

        def add_node(ntype: str, nid: int, name: str, status: str, **extra: Any) -> str:
            key = f"{ntype}:{nid}"
            if key not in seen_nodes:
                seen_nodes.add(key)
                node = {"id": key, "type": ntype, "db_id": nid,
                        "name": name, "status": status}
                node.update(extra)
                nodes.append(node)
            return key

        if entity_type and entity_id:
            # Subgraph around a specific entity
            detail = self.get_entity_detail(entity_type, entity_id)
            if not detail:
                return {"nodes": [], "edges": []}

            center = add_node(entity_type, entity_id, detail["name"],
                              detail.get("threat_status", "unknown"))

            for a in detail.get("connected_artists", []):
                akey = add_node("artist", a["id"], a["name"],
                                a.get("threat_status", "unknown"),
                                verdict=a.get("latest_verdict", ""))
                edges.append({"source": center, "target": akey})
        else:
            # Full graph: entities with enough connections
            for etype in ("label", "songwriter", "publisher"):
                table = self._entity_table(etype)
                rows = self._conn.execute(
                    f"""SELECT id, name, threat_status, artist_count
                        FROM {table}
                        WHERE artist_count >= ?
                          AND (threat_status IN ('confirmed_bad', 'suspected')
                               OR review_status = 'pending_review')
                        ORDER BY artist_count DESC
                        LIMIT 200""",
                    (min_connections,),
                ).fetchall()

                for row in rows:
                    ekey = add_node(etype, row["id"], row["name"],
                                    row["threat_status"],
                                    artist_count=row["artist_count"])

                    # Get connected artists (limit per entity)
                    if etype == "label":
                        artists = self._conn.execute(
                            """SELECT a.id, a.name, a.threat_status, a.latest_verdict
                               FROM artists a
                               JOIN artist_labels al ON a.id = al.artist_id
                               WHERE al.label_id = ?
                               LIMIT 50""",
                            (row["id"],),
                        ).fetchall()
                    elif etype == "songwriter":
                        artists = self._conn.execute(
                            """SELECT a.id, a.name, a.threat_status, a.latest_verdict
                               FROM artists a
                               JOIN artist_songwriters asw ON a.id = asw.artist_id
                               WHERE asw.songwriter_id = ?
                               LIMIT 50""",
                            (row["id"],),
                        ).fetchall()
                    else:
                        artists = self._conn.execute(
                            """SELECT a.id, a.name, a.threat_status, a.latest_verdict
                               FROM artists a
                               JOIN artist_publishers ap ON a.id = ap.artist_id
                               WHERE ap.publisher_id = ?
                               LIMIT 50""",
                            (row["id"],),
                        ).fetchall()

                    for a in artists:
                        akey = add_node("artist", a["id"], a["name"],
                                        a["threat_status"],
                                        verdict=a.get("latest_verdict", ""))
                        edges.append({"source": ekey, "target": akey})

        return {"nodes": nodes, "edges": edges}

    # ------------------------------------------------------------------
    # Feedback loop — update entity connections after flagging an artist
    # ------------------------------------------------------------------

    def update_entity_connections_for_artist(self, artist_name: str) -> list[dict]:
        """After flagging an artist, check all connected entities for threshold crossings.

        Returns list of entities that were newly queued for review.
        """
        artist = self.get_artist(artist_name)
        if not artist:
            return []

        artist_id = artist["id"]
        newly_queued: list[dict] = []

        # Check connected labels
        labels = self.get_artist_labels(artist_id)
        for lbl in labels:
            if self.check_threshold_and_queue("label", lbl["id"]):
                newly_queued.append({"entity_type": "label", "id": lbl["id"],
                                     "name": lbl["name"]})

        # Check connected songwriters
        songwriters = self.get_artist_songwriters(artist_id)
        for sw in songwriters:
            if self.check_threshold_and_queue("songwriter", sw["id"]):
                newly_queued.append({"entity_type": "songwriter", "id": sw["id"],
                                     "name": sw["name"]})

        return newly_queued
