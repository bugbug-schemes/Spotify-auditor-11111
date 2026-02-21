"""
Known entity pre-check — runs BEFORE evidence collectors.

Checks artist against blocklists and entity database for early termination
or pre-seeding of evidence. This is Priority 1 in the detection pipeline.

Order of checks:
1. Known AI artist name → short-circuit LIKELY ARTIFICIAL
2. Entity DB prior verdict → short-circuit or pre-seed
3. PFC distributor/label match → pre-seed strong red flag
4. PFC songwriter match → pre-seed strong red flag
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from spotify_audit.config import BLOCKLIST_DIR

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structured blocklist loading (supports both old flat arrays and new format)
# ---------------------------------------------------------------------------

@dataclass
class BlocklistEntity:
    """A single entry from a structured blocklist file."""
    name: str
    aliases: list[str] = field(default_factory=list)
    entity_type: str = ""        # "label", "artist", "songwriter"
    source: str = ""             # Attribution (e.g., "Harper's Magazine")
    confirmed: bool = True
    notes: str = ""


def _load_structured_blocklist(name: str) -> list[BlocklistEntity]:
    """Load a blocklist, supporting both old (flat array) and new (structured) formats."""
    path = BLOCKLIST_DIR / f"{name}.json"
    if not path.exists():
        return []

    with open(path) as f:
        data = json.load(f)

    # New format: {"entities": [...]}
    if isinstance(data, dict) and "entities" in data:
        entities = []
        for item in data["entities"]:
            entities.append(BlocklistEntity(
                name=item.get("name", ""),
                aliases=item.get("aliases", []),
                entity_type=item.get("type", ""),
                source=item.get("source", ""),
                confirmed=item.get("confirmed", True),
                notes=item.get("notes", ""),
            ))
        return entities

    # Old format: simple flat array of strings
    if isinstance(data, list):
        return [BlocklistEntity(name=n) for n in data if isinstance(n, str)]

    return []


@lru_cache(maxsize=None)
def _known_ai_artists_structured() -> dict[str, BlocklistEntity]:
    """Load known AI artists as {normalized_name: entity}."""
    entities = _load_structured_blocklist("known_ai_artists")
    result: dict[str, BlocklistEntity] = {}
    for e in entities:
        result[e.name.lower().strip()] = e
        for alias in e.aliases:
            result[alias.lower().strip()] = e
    return result


@lru_cache(maxsize=None)
def _pfc_distributors_structured() -> dict[str, BlocklistEntity]:
    """Load PFC distributors as {normalized_name: entity}."""
    entities = _load_structured_blocklist("pfc_distributors")
    result: dict[str, BlocklistEntity] = {}
    for e in entities:
        result[e.name.lower().strip()] = e
        for alias in e.aliases:
            result[alias.lower().strip()] = e
    return result


@lru_cache(maxsize=None)
def _pfc_songwriters_structured() -> dict[str, BlocklistEntity]:
    """Load PFC songwriters as {normalized_name: entity}."""
    entities = _load_structured_blocklist("pfc_songwriters")
    result: dict[str, BlocklistEntity] = {}
    for e in entities:
        result[e.name.lower().strip()] = e
        for alias in e.aliases:
            result[alias.lower().strip()] = e
    return result


# ---------------------------------------------------------------------------
# Pre-check result
# ---------------------------------------------------------------------------

@dataclass
class PreCheckResult:
    """Result of the known-entity pre-check."""
    short_circuit: bool = False          # True → skip all further analysis
    verdict: str = ""                    # e.g. "Likely Artificial"
    confidence: str = ""                 # "high", "medium", "low"
    reason: str = ""                     # Human-readable explanation
    pre_seeded_evidence: list[dict] = field(default_factory=list)
    pfc_label_match: bool = False        # For downstream use


# ---------------------------------------------------------------------------
# Pre-check logic
# ---------------------------------------------------------------------------

def run_pre_check(
    artist_name: str,
    labels: list[str],
    contributors: list[str],
    entity_db: "EntityDB | None" = None,
) -> PreCheckResult:
    """Run the known-entity pre-check pipeline.

    Args:
        artist_name: Artist display name
        labels: Labels/distributors from Deezer/Spotify/MusicBrainz
        contributors: Credited songwriters/producers
        entity_db: Optional entity intelligence database

    Returns:
        PreCheckResult with short_circuit=True if we can stop early.
    """
    result = PreCheckResult()
    name_lower = artist_name.lower().strip()

    # ------------------------------------------------------------------
    # Check 1: Known AI artist name (exact match, case-insensitive)
    # ------------------------------------------------------------------
    ai_db = _known_ai_artists_structured()
    if name_lower in ai_db:
        entity = ai_db[name_lower]
        source_note = f" (source: {entity.source})" if entity.source else ""
        notes_note = f" {entity.notes}" if entity.notes else ""

        result.short_circuit = True
        result.verdict = "Likely Artificial"
        result.confidence = "high"
        result.reason = (
            f"Artist name '{artist_name}' matches known AI artist database"
            f"{source_note}.{notes_note}"
        )
        logger.info(
            "PRE-CHECK: Artist name '%s' matches known AI artist database%s",
            artist_name, source_note,
        )
        return result

    # ------------------------------------------------------------------
    # Check 2: Entity database prior scan results
    # ------------------------------------------------------------------
    if entity_db:
        db_artist = entity_db.get_artist(artist_name)
        if db_artist:
            status = db_artist.get("threat_status", "unknown")
            scan_count = db_artist.get("scan_count", 0)
            prior_verdict = db_artist.get("latest_verdict", "")
            notes = db_artist.get("notes", "")

            if status == "confirmed_bad":
                result.short_circuit = True
                result.verdict = "Likely Artificial"
                result.confidence = "high"
                result.reason = (
                    f"Artist '{artist_name}' is confirmed bad in entity database "
                    f"(scanned {scan_count} time(s), prior verdict: {prior_verdict}). "
                    f"Notes: {notes}"
                )
                logger.info(
                    "PRE-CHECK: '%s' is confirmed_bad in entity DB", artist_name,
                )
                return result

            if status == "cleared":
                result.pre_seeded_evidence.append({
                    "finding": f"Previously cleared in entity database ({scan_count} scan(s))",
                    "source": "Entity DB",
                    "evidence_type": "green_flag",
                    "strength": "moderate",
                    "detail": (
                        f"Artist '{artist_name}' was previously cleared as legitimate. "
                        f"Prior verdict: {prior_verdict}. Notes: {notes}"
                    ),
                    "tags": ["entity_cleared"],
                })

            elif status == "suspected":
                result.pre_seeded_evidence.append({
                    "finding": f"Previously flagged as suspected ({scan_count} scan(s))",
                    "source": "Entity DB",
                    "evidence_type": "red_flag",
                    "strength": "moderate",
                    "detail": (
                        f"Artist '{artist_name}' was previously flagged as suspected. "
                        f"Prior verdict: {prior_verdict}. Notes: {notes}"
                    ),
                    "tags": ["entity_suspected"],
                })

    # ------------------------------------------------------------------
    # Check 3: PFC distributor/label match
    # ------------------------------------------------------------------
    pfc_db = _pfc_distributors_structured()
    for label in labels:
        label_lower = label.lower().strip()
        if label_lower in pfc_db:
            entity = pfc_db[label_lower]
            source_note = f" ({entity.source})" if entity.source else ""
            notes_note = entity.notes if entity.notes else ""

            result.pfc_label_match = True
            result.pre_seeded_evidence.append({
                "finding": f"Label '{label}' is a confirmed PFC provider{source_note}",
                "source": "Blocklist",
                "evidence_type": "red_flag",
                "strength": "strong",
                "detail": (
                    f"Label '{label}' matches PFC distributor database. "
                    f"{notes_note}"
                ),
                "tags": ["pfc_label"],
            })
            logger.info(
                "PRE-CHECK: Label '%s' matches PFC distributor database%s",
                label, source_note,
            )

    # ------------------------------------------------------------------
    # Check 4: PFC songwriter/producer match
    # ------------------------------------------------------------------
    sw_db = _pfc_songwriters_structured()
    for name in contributors:
        name_norm = name.lower().strip()
        if name_norm in sw_db:
            entity = sw_db[name_norm]
            source_note = f" ({entity.source})" if entity.source else ""

            result.pre_seeded_evidence.append({
                "finding": f"Songwriter '{name}' appears in PFC songwriter database{source_note}",
                "source": "Blocklist",
                "evidence_type": "red_flag",
                "strength": "strong",
                "detail": (
                    f"Credited songwriter/producer '{name}' matches PFC songwriter database. "
                    f"{entity.notes if entity.notes else ''}"
                ),
                "tags": ["pfc_songwriter"],
            })
            logger.info(
                "PRE-CHECK: Songwriter '%s' matches PFC songwriter database%s",
                name, source_note,
            )

    return result


# ---------------------------------------------------------------------------
# Entity DB auto-promotion after scan
# ---------------------------------------------------------------------------

def auto_promote_entity(
    entity_db: "EntityDB",
    artist_name: str,
    verdict: str,
    confidence: str,
) -> str | None:
    """Update entity DB after a scan. Returns new status if promoted, else None.

    Rules:
    - 2+ scans with LIKELY ARTIFICIAL at high confidence → confirmed_bad
    - 2+ scans with VERIFIED ARTIST at high confidence → cleared
    - SUSPICIOUS or LIKELY ARTIFICIAL → suspected
    """
    db_artist = entity_db.get_artist(artist_name)
    if not db_artist:
        return None

    current_status = db_artist.get("threat_status", "unknown")
    scan_count = db_artist.get("scan_count", 0)

    # Don't downgrade manual overrides
    if current_status in ("confirmed_bad", "cleared"):
        return None

    new_status = None

    if verdict == "Likely Artificial" and confidence == "high" and scan_count >= 2:
        new_status = "confirmed_bad"
    elif verdict == "Verified Artist" and confidence == "high" and scan_count >= 2:
        new_status = "cleared"
    elif verdict in ("Suspicious", "Likely Artificial") and current_status == "unknown":
        new_status = "suspected"

    if new_status and new_status != current_status:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        entity_db.upsert_artist(
            artist_name,
            threat_status=new_status,
            latest_verdict=verdict,
            latest_confidence=confidence,
            notes=f"Auto-promoted to {new_status} at {now} after {scan_count} scan(s)",
        )
        logger.info(
            "AUTO-PROMOTE: '%s' → %s (verdict=%s, confidence=%s, scans=%d)",
            artist_name, new_status, verdict, confidence, scan_count,
        )
        return new_status

    return None
