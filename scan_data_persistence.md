# Scan Data Persistence — Implementation Spec

How the Playlist Authenticity Analyzer saves all artist data during a scan, enabling network analysis, blocklist promotion, and export/reporting across sessions.

---

## Architecture Decision: Hybrid SQLite + JSON

**SQLite** for structured, queryable data (verdicts, scores, relationships, blocklist status). This is where network analysis and cross-scan queries happen.

**JSON files** for raw API responses (bulky, variable-shape, rarely queried directly). These are your audit trail and reprocessing safety net.

```
data/
├── pfc_analyzer.db              # SQLite — all structured data
├── raw/                         # Raw API responses (JSON files)
│   └── {spotify_artist_id}/
│       ├── spotify.json
│       ├── musicbrainz.json
│       ├── deezer.json
│       ├── genius.json
│       ├── discogs.json
│       ├── lastfm.json
│       ├── setlistfm.json
│       └── youtube.json
└── exports/                     # Generated reports
    └── playlist_{id}_{date}.json
```

**Why hybrid instead of all-SQLite?** Raw API responses are deeply nested, vary per source, and can be 10-50KB each. Storing them as JSON blobs in SQLite works but makes the DB enormous and the blobs aren't queryable anyway. Flat files are simpler to inspect, diff, and version-control. Everything you'd actually *query* lives in SQLite.

**Why not all-JSON?** "Find every artist that shares a songwriter with a known bad actor" requires joining across thousands of records. That's what relational databases are for. JSON files can't do this without loading everything into memory.

---

## SQLite Schema

### `artists` — One row per unique artist across all scans

```sql
CREATE TABLE artists (
    spotify_id          TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    spotify_url         TEXT,

    -- Spotify metadata (snapshot at scan time)
    monthly_listeners   INTEGER,
    followers           INTEGER,
    verified            BOOLEAN DEFAULT FALSE,
    image_url           TEXT,
    genres              TEXT,               -- JSON array as string
    bio                 TEXT,

    -- Catalog summary
    track_count         INTEGER,
    album_count         INTEGER,
    single_count        INTEGER,
    avg_duration_sec    REAL,
    first_release_date  TEXT,               -- ISO date
    release_cadence     TEXT,               -- human-readable summary

    -- Cross-platform presence (boolean flags for quick filtering)
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
    verdict             TEXT,               -- enum: Verified Artist | Likely Authentic | Inconclusive | Suspicious | Likely Artificial
    confidence          TEXT,               -- enum: high | medium | low
    score               REAL,               -- 0-100, higher = more legitimate
    threat_category     TEXT,               -- enum: 1 | 1.5 | 2 | 3 | 4 (NULL if clean)
    threat_label        TEXT,               -- human-readable threat label
    matched_rule        TEXT,               -- which decision tree rule fired

    -- Radar chart scores (0-100 each, 100 = fully legitimate)
    radar_platform_presence   REAL,
    radar_fan_engagement      REAL,
    radar_creative_history    REAL,
    radar_irl_presence        REAL,
    radar_blocklist_status    REAL,
    radar_industry_signals    REAL,

    -- AI analysis (if enabled)
    ai_summary          TEXT,               -- Claude's holistic synthesis
    ai_image_analysis   TEXT,               -- Claude's image assessment

    -- Lifecycle
    first_scanned_at    TEXT NOT NULL,      -- ISO datetime
    last_scanned_at     TEXT NOT NULL,      -- ISO datetime
    scan_count          INTEGER DEFAULT 1,
    raw_data_path       TEXT,               -- relative path to raw/ directory

    -- Manual review
    manual_verdict      TEXT,               -- human override, NULL until reviewed
    manual_notes        TEXT,
    reviewed_at         TEXT,
    blocklist_status    TEXT DEFAULT 'none' -- none | pending_review | confirmed_bad | confirmed_clean
);

CREATE INDEX idx_artists_verdict ON artists(verdict);
CREATE INDEX idx_artists_threat ON artists(threat_category);
CREATE INDEX idx_artists_blocklist ON artists(blocklist_status);
CREATE INDEX idx_artists_score ON artists(score);
```

### `evidence` — Every flag/signal produced by collectors

```sql
CREATE TABLE evidence (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    artist_id           TEXT NOT NULL REFERENCES artists(spotify_id),
    scan_id             TEXT NOT NULL REFERENCES scans(id),

    finding             TEXT NOT NULL,       -- short display text
    source              TEXT NOT NULL,       -- which API or system
    evidence_type       TEXT NOT NULL,       -- red_flag | green_flag | neutral
    strength            TEXT NOT NULL,       -- strong | moderate | weak
    detail              TEXT,                -- longer explanation
    tags                TEXT,                -- JSON array of tag strings
    weight              REAL,                -- signed score contribution
    collector           TEXT,                -- which evidence collector produced this
    category            TEXT,                -- which radar category this maps to

    created_at          TEXT NOT NULL
);

CREATE INDEX idx_evidence_artist ON evidence(artist_id);
CREATE INDEX idx_evidence_type ON evidence(evidence_type);
CREATE INDEX idx_evidence_tags ON evidence(tags);
CREATE INDEX idx_evidence_scan ON evidence(scan_id);
```

### `entities` — Labels, songwriters, producers, distributors

```sql
CREATE TABLE entities (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL,
    normalized_name     TEXT NOT NULL,       -- lowercase, trimmed, standardized
    entity_type         TEXT NOT NULL,       -- label | songwriter | producer | distributor
    blocklist_status    TEXT DEFAULT 'none', -- none | pending_review | confirmed_bad | confirmed_clean

    -- Network stats (updated after each scan)
    connected_artist_count  INTEGER DEFAULT 0,
    flagged_artist_count    INTEGER DEFAULT 0,  -- how many connected artists are Suspicious or worse

    first_seen_at       TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL,
    notes               TEXT
);

CREATE UNIQUE INDEX idx_entities_unique ON entities(normalized_name, entity_type);
CREATE INDEX idx_entities_type ON entities(entity_type);
CREATE INDEX idx_entities_blocklist ON entities(blocklist_status);
```

### `artist_entities` — Many-to-many: which artists connect to which entities

```sql
CREATE TABLE artist_entities (
    artist_id           TEXT NOT NULL REFERENCES artists(spotify_id),
    entity_id           INTEGER NOT NULL REFERENCES entities(id),
    relationship        TEXT NOT NULL,       -- credited_songwriter | credited_producer | label | distributor
    source              TEXT,                -- which API provided this connection
    created_at          TEXT NOT NULL,

    PRIMARY KEY (artist_id, entity_id, relationship)
);

CREATE INDEX idx_ae_artist ON artist_entities(artist_id);
CREATE INDEX idx_ae_entity ON artist_entities(entity_id);
```

### `scans` — One row per playlist scan

```sql
CREATE TABLE scans (
    id                  TEXT PRIMARY KEY,    -- UUID
    playlist_id         TEXT NOT NULL,
    playlist_name       TEXT,
    playlist_owner      TEXT,
    playlist_url        TEXT,
    total_tracks        INTEGER,
    unique_artists      INTEGER,
    is_editorial        BOOLEAN,

    -- Results summary
    health_score        REAL,
    verdict_breakdown   TEXT,                -- JSON object
    threat_breakdown    TEXT,                -- JSON object

    -- Execution metadata
    started_at          TEXT NOT NULL,
    completed_at        TEXT,
    duration_seconds    REAL,
    api_usage           TEXT,                -- JSON array of {name, calls, errors}
    scan_tier           TEXT,                -- quick | standard | deep
    status              TEXT DEFAULT 'running', -- running | completed | failed | partial

    -- Cache stats
    artists_from_cache  INTEGER DEFAULT 0,
    artists_freshly_scanned INTEGER DEFAULT 0
);

CREATE INDEX idx_scans_playlist ON scans(playlist_id);
CREATE INDEX idx_scans_date ON scans(started_at);
```

### `scan_artists` — Which artists appeared in which scans

```sql
CREATE TABLE scan_artists (
    scan_id             TEXT NOT NULL REFERENCES scans(id),
    artist_id           TEXT NOT NULL REFERENCES artists(spotify_id),
    track_name          TEXT,                -- which track triggered inclusion
    track_position      INTEGER,             -- position in playlist

    PRIMARY KEY (scan_id, artist_id)
);
```

---

## Cache Logic: Skip Previously Scanned Artists

When a scan encounters an artist that already exists in `artists`:

```python
def should_scan_artist(spotify_id: str, db: sqlite3.Connection) -> bool:
    """Returns True if this artist needs a fresh scan."""
    row = db.execute(
        "SELECT last_scanned_at FROM artists WHERE spotify_id = ?",
        (spotify_id,)
    ).fetchone()

    if row is None:
        return True  # Never scanned — must scan

    return False  # Cache hit — skip API calls
```

The artist's existing data is reused, and a new row is added to `scan_artists` linking them to the current scan. Evidence is reused from the most recent scan via `evidence.scan_id`.

### Future option: TTL-based expiry

If you later want to add staleness-based re-scanning, the `last_scanned_at` field is already there:

```python
# Example: re-scan if data is older than N days
from datetime import datetime, timedelta

CACHE_TTL_DAYS = 30

def should_rescan(last_scanned_at: str) -> bool:
    scanned = datetime.fromisoformat(last_scanned_at)
    return datetime.utcnow() - scanned > timedelta(days=CACHE_TTL_DAYS)
```

This is off by default (pure skip-if-exists) but the schema supports it whenever you want it.

---

## Data Flow: What Happens During a Scan

```
User provides playlist URL
         │
         ▼
┌─────────────────────────────┐
│  1. CREATE SCAN RECORD      │
│                             │
│  Insert into `scans` table  │
│  status = 'running'         │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  2. FETCH PLAYLIST TRACKS   │
│                             │
│  Spotify API → track list   │
│  Extract unique artist IDs  │
└─────────────┬───────────────┘
              │
              ▼
    ┌─────────────────────┐
    │  For each artist:   │
    │                     │
    │  EXISTS in DB? ─────┼──YES──► Link to scan via `scan_artists`
    │       │             │         Reuse existing verdict & evidence
    │       NO            │         Increment `artists_from_cache`
    │       │             │
    │       ▼             │
    │  PHASE 1: COLLECT   │
    │                     │
    │  Call all APIs       │
    │  Save raw JSON to   │
    │  raw/{artist_id}/   │
    │                     │
    │       │             │
    │       ▼             │
    │  PHASE 2: EVALUATE  │
    │                     │
    │  Run decision tree   │
    │  Produce verdict,    │
    │  evidence, scores    │
    │                     │
    │       │             │
    │       ▼             │
    │  SAVE TO SQLITE     │
    │                     │
    │  INSERT artists     │
    │  INSERT evidence    │
    │  UPSERT entities    │
    │  INSERT artist_     │
    │    entities          │
    │  INSERT scan_       │
    │    artists           │
    └─────────────────────┘
              │
              ▼
┌─────────────────────────────┐
│  3. FINALIZE SCAN           │
│                             │
│  Compute playlist summary   │
│  Update `scans` record      │
│  status = 'completed'       │
│                             │
│  Update entity network      │
│  stats (connected counts)   │
└─────────────────────────────┘
```

---

## Saving Raw API Responses

Every API call's response is saved to disk, keyed by artist ID and source:

```python
import json
import os

def save_raw_response(artist_id: str, source: str, response_data: dict, base_dir: str = "data/raw"):
    """Save raw API response to disk."""
    artist_dir = os.path.join(base_dir, artist_id)
    os.makedirs(artist_dir, exist_ok=True)

    filepath = os.path.join(artist_dir, f"{source}.json")
    with open(filepath, "w") as f:
        json.dump({
            "fetched_at": datetime.utcnow().isoformat() + "Z",
            "source": source,
            "artist_id": artist_id,
            "status": "found",        # or "not_found", "error", "timeout"
            "http_status": 200,
            "response": response_data  # full raw response body
        }, f, indent=2)

    return filepath
```

When an API returns 404 (artist not found), **still save the response** — absence of data is itself evidence:

```python
save_raw_response(artist_id, "setlistfm", {
    "fetched_at": "...",
    "source": "setlistfm",
    "artist_id": artist_id,
    "status": "not_found",
    "http_status": 404,
    "response": None
})
```

---

## Saving Processed Results to SQLite

### Artist insert/update

```python
def save_artist(db: sqlite3.Connection, artist: dict, raw_data_path: str):
    """Insert or update an artist record after analysis."""
    now = datetime.utcnow().isoformat() + "Z"

    db.execute("""
        INSERT INTO artists (
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
            monthly_listeners = excluded.monthly_listeners,
            followers = excluded.followers,
            verdict = excluded.verdict,
            confidence = excluded.confidence,
            score = excluded.score,
            threat_category = excluded.threat_category,
            last_scanned_at = excluded.last_scanned_at,
            scan_count = scan_count + 1
    """, (
        artist["spotify_id"], artist["name"], artist["url"],
        artist["monthly_listeners"], artist["followers"], artist["verified"],
        artist["image_url"], json.dumps(artist.get("genres", [])), artist.get("bio"),
        artist["track_count"], artist["album_count"], artist["single_count"],
        artist["avg_duration_sec"], artist.get("first_release_date"),
        artist.get("release_cadence"),
        artist["found_musicbrainz"], artist["found_deezer"], artist["found_genius"],
        artist["found_discogs"], artist["found_lastfm"], artist["found_setlistfm"],
        artist["found_youtube"],
        artist.get("deezer_fans"), artist.get("lastfm_listeners"), artist.get("lastfm_playcount"),
        artist["verdict"], artist["confidence"], artist["score"],
        artist.get("threat_category"), artist.get("threat_label"), artist.get("matched_rule"),
        artist["radar"]["platform_presence"], artist["radar"]["fan_engagement"],
        artist["radar"]["creative_history"], artist["radar"]["irl_presence"],
        artist["radar"]["blocklist_status"], artist["radar"]["industry_signals"],
        artist.get("ai_summary"), artist.get("ai_image_analysis"),
        now, now, raw_data_path
    ))
```

### Entity extraction and linking

```python
def save_entities(db: sqlite3.Connection, artist_id: str, raw_data: dict):
    """Extract entities from raw API data and save relationships."""
    now = datetime.utcnow().isoformat() + "Z"
    entities_found = []

    # Labels — from Deezer, MusicBrainz, Spotify
    for label_name in extract_labels(raw_data):
        entity_id = upsert_entity(db, label_name, "label", now)
        link_artist_entity(db, artist_id, entity_id, "label", now)
        entities_found.append(("label", label_name))

    # Songwriters/Producers — from Genius credits
    for credit in extract_credits(raw_data):
        entity_id = upsert_entity(db, credit["name"], credit["role"], now)
        link_artist_entity(db, artist_id, entity_id, f"credited_{credit['role']}", now)
        entities_found.append((credit["role"], credit["name"]))

    # Distributors — from ISRC codes, copyright lines
    for dist_name in extract_distributors(raw_data):
        entity_id = upsert_entity(db, dist_name, "distributor", now)
        link_artist_entity(db, artist_id, entity_id, "distributor", now)
        entities_found.append(("distributor", dist_name))

    return entities_found


def upsert_entity(db, name: str, entity_type: str, now: str) -> int:
    """Insert entity if new, return its ID."""
    normalized = normalize_entity_name(name)

    row = db.execute(
        "SELECT id FROM entities WHERE normalized_name = ? AND entity_type = ?",
        (normalized, entity_type)
    ).fetchone()

    if row:
        db.execute(
            "UPDATE entities SET last_seen_at = ?, connected_artist_count = connected_artist_count + 1 WHERE id = ?",
            (now, row[0])
        )
        return row[0]
    else:
        cursor = db.execute(
            "INSERT INTO entities (name, normalized_name, entity_type, first_seen_at, last_seen_at, connected_artist_count) VALUES (?, ?, ?, ?, ?, 1)",
            (name, normalized, entity_type, now, now)
        )
        return cursor.lastrowid
```

---

## Network Analysis Queries

These are the queries that make SQLite worth it — impossible with flat JSON files.

### Find all artists sharing a songwriter with known bad actors

```sql
SELECT DISTINCT a.name, a.spotify_id, a.verdict, a.score
FROM artists a
JOIN artist_entities ae ON a.spotify_id = ae.artist_id
JOIN entities e ON ae.entity_id = e.id
WHERE e.entity_type IN ('songwriter', 'producer')
  AND e.id IN (
      SELECT ae2.entity_id
      FROM artist_entities ae2
      JOIN artists a2 ON ae2.artist_id = a2.spotify_id
      WHERE a2.blocklist_status = 'confirmed_bad'
  )
  AND a.blocklist_status != 'confirmed_bad'
ORDER BY a.score ASC;
```

### Find suspiciously connected entities (potential PFC operations)

```sql
SELECT e.name, e.entity_type, e.connected_artist_count, e.flagged_artist_count,
       ROUND(100.0 * e.flagged_artist_count / e.connected_artist_count, 1) AS pct_flagged
FROM entities e
WHERE e.connected_artist_count >= 3
  AND e.flagged_artist_count >= 2
ORDER BY pct_flagged DESC, e.flagged_artist_count DESC;
```

### Cross-scan overlap: artists appearing on multiple editorial playlists

```sql
SELECT a.name, a.verdict, COUNT(DISTINCT s.playlist_id) AS playlist_count,
       GROUP_CONCAT(DISTINCT s.playlist_name) AS playlists
FROM artists a
JOIN scan_artists sa ON a.spotify_id = sa.artist_id
JOIN scans s ON sa.scan_id = s.id
WHERE s.is_editorial = TRUE
GROUP BY a.spotify_id
HAVING playlist_count >= 2
ORDER BY playlist_count DESC;
```

---

## Export: Generating Shareable Reports

The frontend still consumes the JSON format we already designed. Export generates it from SQLite:

```python
def export_scan_results(db: sqlite3.Connection, scan_id: str) -> dict:
    """Generate the full playlist_results JSON from SQLite for a given scan."""
    scan = db.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()

    artists = db.execute("""
        SELECT a.* FROM artists a
        JOIN scan_artists sa ON a.spotify_id = sa.artist_id
        WHERE sa.scan_id = ?
        ORDER BY a.score ASC
    """, (scan_id,)).fetchall()

    result = {
        "schema_version": "1.0",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "playlist": serialize_scan_metadata(scan),
        "summary": compute_summary_from_artists(artists),
        "api_usage": json.loads(scan["api_usage"] or "[]"),
        "artists": [serialize_artist_with_evidence(db, a, scan_id) for a in artists]
    }

    # Save to exports directory
    filename = f"playlist_{scan['playlist_id']}_{datetime.utcnow().strftime('%Y%m%d')}.json"
    filepath = os.path.join("data/exports", filename)
    os.makedirs("data/exports", exist_ok=True)
    with open(filepath, "w") as f:
        json.dump(result, f, indent=2)

    return result
```

---

## Blocklist Promotion Pipeline

Moving an artist from "detected" to "confirmed bad" is a distinct workflow:

```python
def promote_to_blocklist(db: sqlite3.Connection, spotify_id: str, notes: str = ""):
    """Confirm an artist as a bad actor and update related entities."""
    now = datetime.utcnow().isoformat() + "Z"

    # Update artist status
    db.execute("""
        UPDATE artists
        SET blocklist_status = 'confirmed_bad', manual_verdict = verdict,
            manual_notes = ?, reviewed_at = ?
        WHERE spotify_id = ?
    """, (notes, now, spotify_id))

    # Update flagged counts on all connected entities
    db.execute("""
        UPDATE entities SET flagged_artist_count = (
            SELECT COUNT(DISTINCT ae.artist_id)
            FROM artist_entities ae
            JOIN artists a ON ae.artist_id = a.spotify_id
            WHERE ae.entity_id = entities.id
              AND a.blocklist_status = 'confirmed_bad'
        )
        WHERE id IN (
            SELECT entity_id FROM artist_entities WHERE artist_id = ?
        )
    """, (spotify_id,))

    db.commit()
```

---

## Database Initialization

```python
import sqlite3

def init_database(db_path: str = "data/pfc_analyzer.db") -> sqlite3.Connection:
    """Create database and all tables if they don't exist."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")       # better concurrent read performance
    db.execute("PRAGMA foreign_keys=ON")

    db.executescript("""
        CREATE TABLE IF NOT EXISTS artists ( ... );
        CREATE TABLE IF NOT EXISTS evidence ( ... );
        CREATE TABLE IF NOT EXISTS entities ( ... );
        CREATE TABLE IF NOT EXISTS artist_entities ( ... );
        CREATE TABLE IF NOT EXISTS scans ( ... );
        CREATE TABLE IF NOT EXISTS scan_artists ( ... );
        -- All indexes from schema above
    """)

    db.commit()
    return db
```

Place the full `CREATE TABLE` statements from the schema section above into the `executescript` call. They're separated in this doc for readability.

---

## Integration Checklist

When implementing, wire these into the existing pipeline:

1. **At scan start**: Call `init_database()`, insert a `scans` row with `status='running'`
2. **Per-artist, before API calls**: Call `should_scan_artist()` — if cached, skip to step 5
3. **Per-artist, during Phase 1 (Collect)**: Call `save_raw_response()` for each API response
4. **Per-artist, after Phase 2 (Evaluate)**: Call `save_artist()`, `save_evidence()`, `save_entities()`
5. **Per-artist, always**: Insert `scan_artists` row linking artist to current scan
6. **At scan end**: Update `scans` row with summary stats, `status='completed'`
7. **For frontend**: Call `export_scan_results()` to generate the JSON the React app expects
8. **For blocklist work**: Use network queries + `promote_to_blocklist()` as needed

---

## Migration Path

If you already have cached `playlist_*_results.json` files from earlier development:

```python
def import_cached_json(db: sqlite3.Connection, json_path: str):
    """Import a previously cached playlist results JSON into SQLite."""
    with open(json_path) as f:
        data = json.load(f)

    # Create a scan record
    scan_id = str(uuid.uuid4())
    db.execute("INSERT INTO scans (...) VALUES (...)", (...))

    # Import each artist
    for artist in data["artists"]:
        save_artist(db, artist, raw_data_path=None)
        for ev in artist.get("evidence", []):
            save_evidence(db, artist["artist_id"], scan_id, ev)
        # Link to scan
        db.execute(
            "INSERT OR IGNORE INTO scan_artists (scan_id, artist_id) VALUES (?, ?)",
            (scan_id, artist["artist_id"])
        )

    db.commit()
```

This lets you bootstrap the database from existing work without re-running any scans.
