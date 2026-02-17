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

_VALID_STATUSES = {UNKNOWN, SUSPECTED, CONFIRMED_BAD, CLEARED}
_ENTITY_TYPES = {"artist", "label", "songwriter", "publisher"}

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

-- Indexes for common queries -------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_artists_threat ON artists(threat_status);
CREATE INDEX IF NOT EXISTS idx_artists_verdict ON artists(latest_verdict);
CREATE INDEX IF NOT EXISTS idx_labels_threat ON labels(threat_status);
CREATE INDEX IF NOT EXISTS idx_songwriters_threat ON songwriters(threat_status);
CREATE INDEX IF NOT EXISTS idx_observations_entity ON observations(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_observations_type ON observations(obs_type);
CREATE INDEX IF NOT EXISTS idx_artist_labels_label ON artist_labels(label_id);
CREATE INDEX IF NOT EXISTS idx_artist_songwriters_sw ON artist_songwriters(songwriter_id);
"""


# ---------------------------------------------------------------------------
# EntityDB class
# ---------------------------------------------------------------------------

class EntityDB:
    """SQLite-backed relational store for suspicious entities."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._in_batch = False
        self._init_schema()

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
        ]:
            try:
                self._conn.execute(f"ALTER TABLE artists ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass  # column already exists
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

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
