"""
Flask Blueprint — Entity Review CMS API

All /api/cms/* routes for the review queue, entity detail, review actions,
scan history, blocklist management, and API health monitoring.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from flask import Blueprint, jsonify, request

from spotify_audit.entity_db import (
    EntityDB,
    DEFAULT_DB_PATH,
    PENDING_REVIEW,
    DEFERRED,
    REVIEWED,
    ACTION_CONFIRM,
    ACTION_DISMISS,
    ACTION_DEFER,
)

logger = logging.getLogger("spotify_audit.web.api")

cms_api = Blueprint("cms_api", __name__, url_prefix="/api/cms")

VALID_ENTITY_TYPES = ("artist", "label", "songwriter", "publisher")


def _validate_entity_type(entity_type: str):
    """Return a 400 JSON error response if entity_type is invalid, else None."""
    if entity_type not in VALID_ENTITY_TYPES:
        return jsonify({"error": f"Invalid entity type: {entity_type}"}), 400
    return None

# ---------------------------------------------------------------------------
# DB helper — one EntityDB instance per app (lazy init)
# ---------------------------------------------------------------------------

_db: EntityDB | None = None


def _get_db() -> EntityDB:
    global _db
    if _db is None:
        _db = EntityDB(DEFAULT_DB_PATH)
    return _db


def init_db(db_path: str | Path | None = None) -> None:
    """Initialize the shared EntityDB instance (call from app setup)."""
    global _db
    _db = EntityDB(db_path or DEFAULT_DB_PATH)


# ---------------------------------------------------------------------------
# Review Queue
# ---------------------------------------------------------------------------

@cms_api.route("/queue", methods=["GET"])
def review_queue():
    """Get entities in the review queue.

    Query params:
        entity_type: label | songwriter | publisher
        status: pending_review | deferred  (default: pending_review)
        min_count: minimum artist_count
        max_count: maximum artist_count
        sort: connection_count | threshold_date
        limit: int (default 100)
        offset: int (default 0)
    """
    db = _get_db()
    items = db.get_review_queue(
        entity_type=request.args.get("entity_type"),
        review_status=request.args.get("status"),
        min_count=_int_param("min_count"),
        max_count=_int_param("max_count"),
        sort_by=request.args.get("sort", "connection_count"),
        limit=_int_param("limit") or 100,
        offset=_int_param("offset") or 0,
    )
    return jsonify({"items": items, "count": len(items)})


@cms_api.route("/queue/stats", methods=["GET"])
def queue_stats():
    """Dashboard summary counts for the review queue."""
    db = _get_db()
    return jsonify(db.get_review_queue_stats())


# ---------------------------------------------------------------------------
# Entity Detail
# ---------------------------------------------------------------------------

@cms_api.route("/entities/<entity_type>/<int:entity_id>", methods=["GET"])
def entity_detail(entity_type: str, entity_id: int):
    """Full entity detail with connected artists, context clues, etc."""
    err = _validate_entity_type(entity_type)
    if err:
        return err

    db = _get_db()

    # Optionally recompute context clues before returning
    if request.args.get("refresh_clues") == "1":
        db.compute_context_clues(entity_type, entity_id)

    detail = db.get_entity_detail(entity_type, entity_id)
    if not detail:
        return jsonify({"error": "Entity not found"}), 404

    return jsonify(detail)


@cms_api.route("/entities/<entity_type>/<int:entity_id>/network", methods=["GET"])
def entity_network(entity_type: str, entity_id: int):
    """Network graph data (nodes + edges) centered on an entity."""
    err = _validate_entity_type(entity_type)
    if err:
        return err
    db = _get_db()
    graph = db.get_network_graph(entity_type=entity_type, entity_id=entity_id)
    return jsonify(graph)


# ---------------------------------------------------------------------------
# Review Actions
# ---------------------------------------------------------------------------

@cms_api.route("/entities/<entity_type>/<int:entity_id>/review", methods=["POST"])
def submit_review(entity_type: str, entity_id: int):
    """Submit a review decision.

    JSON body:
        action: confirmed_bad | dismissed | deferred
        note: optional free-text justification
    """
    err = _validate_entity_type(entity_type)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    action = data.get("action", "")
    note = data.get("note", "")

    if action not in (ACTION_CONFIRM, ACTION_DISMISS, ACTION_DEFER):
        return jsonify({"error": f"Invalid action: {action}. Must be one of: confirmed_bad, dismissed, deferred"}), 400

    db = _get_db()
    result = db.submit_review(entity_type, entity_id, action, note)

    if not result.get("success"):
        return jsonify(result), 400

    # If confirmed, sync the relevant blocklist
    if action == ACTION_CONFIRM:
        db.sync_blocklists()

    return jsonify(result)


@cms_api.route("/entities/<entity_type>/<int:entity_id>/note", methods=["POST"])
def add_note(entity_type: str, entity_id: int):
    """Add a note to an entity.

    JSON body:
        note: the note text
    """
    err = _validate_entity_type(entity_type)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    note = data.get("note", "").strip()
    if not note:
        return jsonify({"error": "Note text is required"}), 400

    db = _get_db()
    ok = db.add_entity_note(entity_type, entity_id, note)
    if not ok:
        return jsonify({"error": "Entity not found"}), 404
    return jsonify({"success": True})


@cms_api.route("/entities/<entity_type>/<int:entity_id>/alias", methods=["POST"])
def create_alias(entity_type: str, entity_id: int):
    """Link two entities as aliases.

    JSON body:
        target_type: entity type of the other entity
        target_id: id of the other entity
        relationship: alias | subsidiary | same_person (default: alias)
        note: optional
    """
    err = _validate_entity_type(entity_type)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    target_type = data.get("target_type", "")
    target_id = data.get("target_id")
    relationship = data.get("relationship", "alias")
    note = data.get("note", "")

    if not target_type or target_id is None:
        return jsonify({"error": "target_type and target_id are required"}), 400
    err = _validate_entity_type(target_type)
    if err:
        return err

    db = _get_db()
    alias_id = db.link_entity_alias(
        entity_type, entity_id,
        target_type, int(target_id),
        relationship=relationship,
        note=note,
    )
    return jsonify({"success": True, "alias_id": alias_id})


# ---------------------------------------------------------------------------
# Batch Review
# ---------------------------------------------------------------------------

@cms_api.route("/batch-review", methods=["POST"])
def batch_review():
    """Submit review decisions for multiple entities at once.

    JSON body:
        action: confirmed_bad | dismissed | deferred
        note: optional
        entities: list of {entity_type, entity_id}
    """
    data = request.get_json(silent=True) or {}
    action = data.get("action", "")
    note = data.get("note", "")
    entities = data.get("entities", [])

    if action not in (ACTION_CONFIRM, ACTION_DISMISS, ACTION_DEFER):
        return jsonify({"error": f"Invalid action: {action}"}), 400
    if not entities:
        return jsonify({"error": "No entities provided"}), 400

    db = _get_db()
    results = []
    for ent in entities:
        etype = ent.get("entity_type", "")
        eid = ent.get("entity_id")
        if etype and eid is not None:
            r = db.submit_review(etype, int(eid), action, note)
            results.append(r)

    # Sync blocklists if any were confirmed
    if action == ACTION_CONFIRM:
        db.sync_blocklists()

    succeeded = sum(1 for r in results if r.get("success"))
    return jsonify({
        "success": True,
        "processed": len(results),
        "succeeded": succeeded,
        "results": results,
    })


# ---------------------------------------------------------------------------
# Review History / Audit Log
# ---------------------------------------------------------------------------

@cms_api.route("/history", methods=["GET"])
def review_history():
    """Review audit log.

    Query params:
        entity_type: optional filter
        limit: int (default 100)
        offset: int (default 0)
    """
    db = _get_db()
    entries = db.get_review_history(
        entity_type=request.args.get("entity_type"),
        limit=_int_param("limit") or 100,
        offset=_int_param("offset") or 0,
    )
    return jsonify({"entries": entries, "count": len(entries)})


# ---------------------------------------------------------------------------
# Scan History
# ---------------------------------------------------------------------------

@cms_api.route("/scans", methods=["GET"])
def scan_history():
    """List past scans with summary info.

    Query params:
        limit: int (default 50)
        offset: int (default 0)
    """
    db = _get_db()
    scans = db.get_scan_history(
        limit=_int_param("limit") or 50,
        offset=_int_param("offset") or 0,
    )
    return jsonify({"scans": scans, "count": len(scans)})


@cms_api.route("/scans/<int:scan_id>", methods=["GET"])
def scan_detail(scan_id: int):
    """Get a scan with all its artist results."""
    db = _get_db()
    scan = db.get_scan_detail(scan_id)
    if not scan:
        return jsonify({"error": "Scan not found"}), 404
    return jsonify(scan)


@cms_api.route("/scans/artist/<artist_name>", methods=["GET"])
def artist_scan_history(artist_name: str):
    """Get all past scan results for a specific artist."""
    db = _get_db()
    results = db.get_artist_scan_history(artist_name)
    return jsonify({"results": results, "count": len(results)})


# ---------------------------------------------------------------------------
# Blocklist Management
# ---------------------------------------------------------------------------

@cms_api.route("/blocklists", methods=["GET"])
def list_blocklists():
    """List all blocklists with entry counts."""
    db = _get_db()
    blocklists = {}
    for etype, fname in [
        ("label", "pfc_distributors.json"),
        ("artist", "known_ai_artists.json"),
        ("songwriter", "pfc_songwriters.json"),
    ]:
        entries = db.export_blocklist(etype)
        blocklists[fname] = {
            "entity_type": etype,
            "count": len(entries),
        }
    return jsonify(blocklists)


@cms_api.route("/blocklists/<name>", methods=["GET"])
def browse_blocklist(name: str):
    """Browse entries in a specific blocklist."""
    etype = _blocklist_entity_type(name)
    if not etype:
        return jsonify({"error": f"Unknown blocklist: {name}"}), 404

    db = _get_db()
    entries = db.export_blocklist(etype)
    return jsonify({"name": name, "entity_type": etype, "entries": entries, "count": len(entries)})


@cms_api.route("/blocklists/<name>/add", methods=["POST"])
def add_to_blocklist(name: str):
    """Manually add an entry to a blocklist.

    JSON body:
        name: entity name to add
        note: optional reason
    """
    etype = _blocklist_entity_type(name)
    if not etype:
        return jsonify({"error": f"Unknown blocklist: {name}"}), 404

    data = request.get_json(silent=True) or {}
    entity_name = data.get("name", "").strip()
    note = data.get("note", "Manual addition")
    if not entity_name:
        return jsonify({"error": "Name is required"}), 400

    db = _get_db()
    upsert = {
        "label": lambda: db.upsert_label(entity_name, threat_status="confirmed_bad", notes=note),
        "artist": lambda: db.upsert_artist(entity_name, threat_status="confirmed_bad", notes=note),
        "songwriter": lambda: db.upsert_songwriter(entity_name, threat_status="confirmed_bad", notes=note),
    }[etype]
    eid = upsert()
    db.sync_blocklists()

    return jsonify({"success": True, "entity_id": eid, "blocklist": name})


@cms_api.route("/blocklists/<name>/remove", methods=["POST"])
def remove_from_blocklist(name: str):
    """Remove an entry from a blocklist (clears the entity).

    JSON body:
        name: entity name to remove
        note: reason for removal
    """
    etype = _blocklist_entity_type(name)
    if not etype:
        return jsonify({"error": f"Unknown blocklist: {name}"}), 404

    data = request.get_json(silent=True) or {}
    entity_name = data.get("name", "").strip()
    note = data.get("note", "Removed from blocklist")
    if not entity_name:
        return jsonify({"error": "Name is required"}), 400

    db = _get_db()
    # Clear the entity's threat_status
    upsert = {
        "label": lambda: db.upsert_label(entity_name, threat_status="cleared", notes=note),
        "artist": lambda: db.upsert_artist(entity_name, threat_status="cleared", notes=note),
        "songwriter": lambda: db.upsert_songwriter(entity_name, threat_status="cleared", notes=note),
    }[etype]
    eid = upsert()

    # Log it
    db.add_observation(etype, eid, "note",
                       f"Removed from {name}", detail=note, source="cms")
    db.sync_blocklists()

    return jsonify({"success": True, "entity_id": eid, "blocklist": name})


@cms_api.route("/blocklists/<name>/export", methods=["GET"])
def export_blocklist(name: str):
    """Download a blocklist as a JSON file."""
    etype = _blocklist_entity_type(name)
    if not etype:
        return jsonify({"error": f"Unknown blocklist: {name}"}), 404

    db = _get_db()
    entries = db.export_blocklist(etype)
    response = jsonify(sorted(set(entries)))
    response.headers["Content-Disposition"] = f"attachment; filename={name}"
    return response


@cms_api.route("/blocklists/sync", methods=["POST"])
def sync_blocklists():
    """Regenerate all blocklist JSON files from confirmed entities."""
    db = _get_db()
    result = db.sync_blocklists()
    return jsonify({"success": True, "blocklists": result})


# ---------------------------------------------------------------------------
# API Health Monitor
# ---------------------------------------------------------------------------

@cms_api.route("/api-health", methods=["GET"])
def api_health():
    """API health summary.

    Query params:
        hours: lookback period (default 24)
    """
    db = _get_db()
    hours = _int_param("hours") or 24
    return jsonify(db.get_api_health(hours))


# ---------------------------------------------------------------------------
# Network Graph
# ---------------------------------------------------------------------------

@cms_api.route("/network", methods=["GET"])
def full_network():
    """Full network graph of suspicious entities.

    Query params:
        min_connections: minimum artist count (default 2)
    """
    db = _get_db()
    graph = db.get_network_graph(
        min_connections=_int_param("min_connections") or 2,
    )
    return jsonify(graph)


# ---------------------------------------------------------------------------
# Threshold Management
# ---------------------------------------------------------------------------

@cms_api.route("/check-thresholds", methods=["POST"])
def check_thresholds():
    """Re-check all entity thresholds and queue any that cross."""
    db = _get_db()
    db.refresh_entity_counts()
    counts = db.check_all_thresholds()
    return jsonify({"success": True, "newly_queued": counts})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _int_param(name: str) -> int | None:
    """Parse an optional integer query parameter."""
    val = request.args.get(name)
    if val is not None:
        try:
            return int(val)
        except ValueError:
            pass
    return None


def _blocklist_entity_type(name: str) -> str | None:
    """Map blocklist filename to entity type."""
    return {
        "pfc_distributors.json": "label",
        "known_ai_artists.json": "artist",
        "pfc_songwriters.json": "songwriter",
    }.get(name)
