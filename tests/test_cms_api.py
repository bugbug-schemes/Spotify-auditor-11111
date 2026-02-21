"""Tests for the CMS API routes (web/api.py).

Uses a real in-memory EntityDB to exercise the full Flask → EntityDB stack.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from spotify_audit.entity_db import (
    EntityDB,
    PENDING_REVIEW,
    DEFERRED,
    REVIEWED,
    ACTION_CONFIRM,
    ACTION_DISMISS,
    ACTION_DEFER,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    """Fresh EntityDB on a temp file."""
    return EntityDB(tmp_path / "test_entities.db")


@pytest.fixture()
def app(db):
    """Flask test app with CMS API wired to the test DB."""
    from flask import Flask
    from web.api import cms_api, _get_db
    import web.api as api_module

    app = Flask(__name__)
    app.register_blueprint(cms_api)
    app.config["TESTING"] = True

    # Inject our test DB into the module
    api_module._db = db

    yield app

    api_module._db = None


@pytest.fixture()
def client(app):
    return app.test_client()


def _seed_label(db, name="Chill Records", threat_status="suspected",
                review_status=PENDING_REVIEW, artist_count=5):
    """Insert a label and set its review columns."""
    lid = db.upsert_label(name, threat_status=threat_status)
    db._conn.execute(
        """UPDATE labels SET review_status = ?, artist_count = ?,
           threshold_crossed_at = datetime('now')
           WHERE id = ?""",
        (review_status, artist_count, lid),
    )
    db._conn.commit()
    return lid


def _seed_songwriter(db, name="Ghost Writer", threat_status="suspected",
                     review_status=PENDING_REVIEW, artist_count=3):
    lid = db.upsert_songwriter(name, threat_status=threat_status)
    db._conn.execute(
        """UPDATE songwriters SET review_status = ?, artist_count = ?,
           threshold_crossed_at = datetime('now')
           WHERE id = ?""",
        (review_status, artist_count, lid),
    )
    db._conn.commit()
    return lid


def _seed_artist(db, name="Ambient Dreamer", threat_status="suspected"):
    return db.upsert_artist(name, threat_status=threat_status)


# ---------------------------------------------------------------------------
# Review Queue
# ---------------------------------------------------------------------------

class TestReviewQueue:
    def test_empty_queue(self, client):
        resp = client.get("/api/cms/queue")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["items"] == []
        assert data["count"] == 0

    def test_queue_returns_pending_items(self, client, db):
        _seed_label(db, "Label A")
        _seed_label(db, "Label B")
        resp = client.get("/api/cms/queue")
        data = resp.get_json()
        assert data["count"] == 2

    def test_queue_filters_by_entity_type(self, client, db):
        _seed_label(db, "Label A")
        _seed_songwriter(db, "Writer A")
        resp = client.get("/api/cms/queue?entity_type=label")
        data = resp.get_json()
        assert data["count"] == 1
        assert data["items"][0]["entity_type"] == "label"

    def test_queue_filters_by_status(self, client, db):
        _seed_label(db, "Pending Label", review_status=PENDING_REVIEW)
        _seed_label(db, "Deferred Label", review_status=DEFERRED)
        resp = client.get("/api/cms/queue?status=deferred")
        data = resp.get_json()
        assert data["count"] == 1
        assert "Deferred Label" in data["items"][0]["name"]

    def test_queue_stats(self, client, db):
        _seed_label(db, "Pending1")
        _seed_label(db, "Pending2")
        _seed_label(db, "Deferred1", review_status=DEFERRED)
        resp = client.get("/api/cms/queue/stats")
        data = resp.get_json()
        assert data["total_pending"] == 2
        assert data["total_deferred"] == 1
        assert data["pending"]["label"] == 2


# ---------------------------------------------------------------------------
# Entity Detail
# ---------------------------------------------------------------------------

class TestEntityDetail:
    def test_entity_not_found(self, client):
        resp = client.get("/api/cms/entities/label/999")
        assert resp.status_code == 404

    def test_invalid_entity_type(self, client):
        resp = client.get("/api/cms/entities/invalid_type/1")
        assert resp.status_code == 400

    def test_label_detail(self, client, db):
        lid = _seed_label(db, "Shady Records")
        # Link an artist
        aid = _seed_artist(db, "Sleep Waves")
        db.link_artist_label(aid, lid, source="deezer")

        resp = client.get(f"/api/cms/entities/label/{lid}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "Shady Records"
        assert data["entity_type"] == "label"
        assert len(data["connected_artists"]) == 1
        assert data["connected_artists"][0]["name"] == "Sleep Waves"
        assert "investigation_links" in data
        assert "context_clues" in data

    def test_songwriter_detail(self, client, db):
        sid = _seed_songwriter(db, "Fake Writer")
        aid = _seed_artist(db, "Artist X")
        db.link_artist_songwriter(aid, sid, role="writer", source="genius")

        resp = client.get(f"/api/cms/entities/songwriter/{sid}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "Fake Writer"
        assert len(data["connected_artists"]) == 1


# ---------------------------------------------------------------------------
# Review Actions
# ---------------------------------------------------------------------------

class TestReviewActions:
    def test_confirm_review(self, client, db):
        lid = _seed_label(db, "Bad Label")
        resp = client.post(
            f"/api/cms/entities/label/{lid}/review",
            json={"action": "confirmed_bad", "note": "Definitely PFC"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["action"] == "confirmed_bad"
        assert data["blocklist_updated"] == "pfc_distributors.json"

        # Verify entity was updated
        detail = db.get_entity_detail("label", lid)
        assert detail["threat_status"] == "confirmed_bad"
        assert detail["review_status"] == "reviewed"

    def test_dismiss_review(self, client, db):
        lid = _seed_label(db, "Legit Label")
        resp = client.post(
            f"/api/cms/entities/label/{lid}/review",
            json={"action": "dismissed", "note": "False positive"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["action"] == "dismissed"

        detail = db.get_entity_detail("label", lid)
        assert detail["review_status"] == "reviewed"
        assert detail["review_action"] == "dismissed"

    def test_defer_review(self, client, db):
        lid = _seed_label(db, "Unclear Label")
        resp = client.post(
            f"/api/cms/entities/label/{lid}/review",
            json={"action": "deferred", "note": "Need more data"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

        detail = db.get_entity_detail("label", lid)
        assert detail["review_status"] == "deferred"

    def test_invalid_action_rejected(self, client, db):
        lid = _seed_label(db, "Some Label")
        resp = client.post(
            f"/api/cms/entities/label/{lid}/review",
            json={"action": "invalid_action"},
        )
        assert resp.status_code == 400

    def test_review_nonexistent_entity(self, client):
        resp = client.post(
            "/api/cms/entities/label/9999/review",
            json={"action": "confirmed_bad"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data.get("success") is not True

    def test_add_note(self, client, db):
        lid = _seed_label(db, "Noted Label")
        resp = client.post(
            f"/api/cms/entities/label/{lid}/note",
            json={"note": "Interesting pattern here"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_add_empty_note_rejected(self, client, db):
        lid = _seed_label(db, "No Note Label")
        resp = client.post(
            f"/api/cms/entities/label/{lid}/note",
            json={"note": ""},
        )
        assert resp.status_code == 400

    def test_add_note_nonexistent_entity(self, client):
        resp = client.post(
            "/api/cms/entities/label/9999/note",
            json={"note": "Does not exist"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Batch Review
# ---------------------------------------------------------------------------

class TestBatchReview:
    def test_batch_confirm(self, client, db):
        lid1 = _seed_label(db, "Bad Label 1")
        lid2 = _seed_label(db, "Bad Label 2")
        resp = client.post("/api/cms/batch-review", json={
            "action": "confirmed_bad",
            "note": "Batch confirm",
            "entities": [
                {"entity_type": "label", "entity_id": lid1},
                {"entity_type": "label", "entity_id": lid2},
            ],
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["processed"] == 2
        assert data["succeeded"] == 2

    def test_batch_invalid_action(self, client):
        resp = client.post("/api/cms/batch-review", json={
            "action": "invalid",
            "entities": [{"entity_type": "label", "entity_id": 1}],
        })
        assert resp.status_code == 400

    def test_batch_empty_entities(self, client):
        resp = client.post("/api/cms/batch-review", json={
            "action": "dismissed",
            "entities": [],
        })
        assert resp.status_code == 400

    def test_batch_mixed_types(self, client, db):
        lid = _seed_label(db, "Label X")
        sid = _seed_songwriter(db, "Writer X")
        resp = client.post("/api/cms/batch-review", json={
            "action": "deferred",
            "note": "Mixed batch",
            "entities": [
                {"entity_type": "label", "entity_id": lid},
                {"entity_type": "songwriter", "entity_id": sid},
            ],
        })
        assert resp.status_code == 200
        assert resp.get_json()["succeeded"] == 2


# ---------------------------------------------------------------------------
# Review History / Audit Log
# ---------------------------------------------------------------------------

class TestReviewHistory:
    def test_empty_history(self, client):
        resp = client.get("/api/cms/history")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["entries"] == []

    def test_history_after_review(self, client, db):
        lid = _seed_label(db, "Reviewed Label")
        client.post(
            f"/api/cms/entities/label/{lid}/review",
            json={"action": "confirmed_bad", "note": "Test"},
        )
        resp = client.get("/api/cms/history")
        data = resp.get_json()
        assert data["count"] == 1
        assert data["entries"][0]["action"] == "confirmed_bad"

    def test_history_filter_by_type(self, client, db):
        lid = _seed_label(db, "RL")
        sid = _seed_songwriter(db, "RS")
        client.post(f"/api/cms/entities/label/{lid}/review",
                     json={"action": "dismissed"})
        client.post(f"/api/cms/entities/songwriter/{sid}/review",
                     json={"action": "confirmed_bad"})

        resp = client.get("/api/cms/history?entity_type=songwriter")
        data = resp.get_json()
        assert data["count"] == 1
        assert data["entries"][0]["entity_type"] == "songwriter"


# ---------------------------------------------------------------------------
# Scan History
# ---------------------------------------------------------------------------

class TestScanHistory:
    def test_empty_scans(self, client):
        resp = client.get("/api/cms/scans")
        assert resp.status_code == 200
        assert resp.get_json()["scans"] == []

    def test_scan_not_found(self, client):
        resp = client.get("/api/cms/scans/999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Blocklist Management
# ---------------------------------------------------------------------------

class TestBlocklistManagement:
    def test_list_blocklists(self, client):
        resp = client.get("/api/cms/blocklists")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "pfc_distributors.json" in data
        assert "known_ai_artists.json" in data
        assert "pfc_songwriters.json" in data

    def test_browse_blocklist(self, client):
        resp = client.get("/api/cms/blocklists/pfc_distributors.json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "pfc_distributors.json"
        assert data["entity_type"] == "label"

    def test_browse_unknown_blocklist(self, client):
        resp = client.get("/api/cms/blocklists/nonexistent.json")
        assert resp.status_code == 404

    def test_add_to_blocklist(self, client):
        resp = client.post("/api/cms/blocklists/pfc_distributors.json/add", json={
            "name": "Shady Distro LLC",
            "note": "Known PFC operation",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["blocklist"] == "pfc_distributors.json"

    def test_add_to_blocklist_empty_name(self, client):
        resp = client.post("/api/cms/blocklists/pfc_distributors.json/add", json={
            "name": "",
        })
        assert resp.status_code == 400

    def test_remove_from_blocklist(self, client, db):
        # First add an entity
        db.upsert_label("Remove Me", threat_status="confirmed_bad")
        resp = client.post("/api/cms/blocklists/pfc_distributors.json/remove", json={
            "name": "Remove Me",
            "note": "False positive",
        })
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_sync_blocklists(self, client):
        resp = client.post("/api/cms/blocklists/sync")
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_export_blocklist(self, client):
        resp = client.get("/api/cms/blocklists/pfc_distributors.json/export")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# API Health
# ---------------------------------------------------------------------------

class TestApiHealth:
    def test_api_health_returns_list(self, client):
        resp = client.get("/api/cms/api-health")
        assert resp.status_code == 200
        # Should be a list (possibly empty)
        assert isinstance(resp.get_json(), list)

    def test_api_health_custom_hours(self, client):
        resp = client.get("/api/cms/api-health?hours=48")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Network Graph
# ---------------------------------------------------------------------------

class TestNetworkGraph:
    def test_empty_network(self, client):
        resp = client.get("/api/cms/network")
        assert resp.status_code == 200

    def test_entity_network(self, client, db):
        lid = _seed_label(db, "Network Label")
        resp = client.get(f"/api/cms/entities/label/{lid}/network")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Threshold Check
# ---------------------------------------------------------------------------

class TestThresholds:
    def test_check_thresholds(self, client):
        resp = client.post("/api/cms/check-thresholds")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "newly_queued" in data


# ---------------------------------------------------------------------------
# Entity Alias
# ---------------------------------------------------------------------------

class TestEntityAlias:
    def test_create_alias(self, client, db):
        lid1 = _seed_label(db, "Label Alpha")
        lid2 = _seed_label(db, "Label Alpha Records")
        resp = client.post(f"/api/cms/entities/label/{lid1}/alias", json={
            "target_type": "label",
            "target_id": lid2,
            "relationship": "alias",
            "note": "Same entity, different name",
        })
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_create_alias_missing_target(self, client, db):
        lid = _seed_label(db, "Lonely Label")
        resp = client.post(f"/api/cms/entities/label/{lid}/alias", json={
            "target_type": "",
            "note": "Missing target",
        })
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Flask CMS SPA serving
# ---------------------------------------------------------------------------

class TestCmsSpaServing:
    """Test the /cms route that serves the React SPA."""

    @pytest.fixture()
    def spa_app(self, tmp_path, db):
        """Flask app with a mock CMS static directory."""
        from flask import Flask, send_from_directory
        from web.api import cms_api
        import web.api as api_module

        cms_dir = tmp_path / "static" / "cms"
        cms_dir.mkdir(parents=True)
        (cms_dir / "index.html").write_text("<html>CMS</html>")
        assets = cms_dir / "assets"
        assets.mkdir()
        (assets / "app.js").write_text("console.log('hi')")

        app = Flask(__name__)
        app.register_blueprint(cms_api)
        app.config["TESTING"] = True
        api_module._db = db

        @app.route("/cms")
        @app.route("/cms/<path:subpath>")
        def serve_cms(subpath=""):
            if subpath:
                file_path = cms_dir / subpath
                if file_path.is_file():
                    return send_from_directory(cms_dir, subpath)
            return send_from_directory(cms_dir, "index.html")

        yield app
        api_module._db = None

    def test_cms_root(self, spa_app):
        c = spa_app.test_client()
        resp = c.get("/cms")
        assert resp.status_code == 200
        assert b"CMS" in resp.data

    def test_cms_client_route_fallback(self, spa_app):
        c = spa_app.test_client()
        resp = c.get("/cms/queue")
        assert resp.status_code == 200
        assert b"CMS" in resp.data  # Falls back to index.html

    def test_cms_static_asset(self, spa_app):
        c = spa_app.test_client()
        resp = c.get("/cms/assets/app.js")
        assert resp.status_code == 200
        assert b"console.log" in resp.data
