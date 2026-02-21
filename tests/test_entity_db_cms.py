"""Tests for EntityDB CMS methods — review queue, submit_review, context clues, etc."""

from __future__ import annotations

import pytest

from spotify_audit.entity_db import (
    EntityDB,
    PENDING_REVIEW,
    DEFERRED,
    REVIEWED,
    NOT_QUEUED,
    ACTION_CONFIRM,
    ACTION_DISMISS,
    ACTION_DEFER,
    REVIEW_THRESHOLDS,
)


@pytest.fixture()
def db(tmp_path):
    """Fresh EntityDB on a temp file.  sync_blocklists is stubbed to avoid
    overwriting the real blocklist JSON files during tests."""
    _db = EntityDB(tmp_path / "test.db")
    _db.sync_blocklists = lambda: {}  # no-op stub
    return _db


def _add_label(db, name, threat_status="suspected", review_status=PENDING_REVIEW,
               artist_count=5):
    lid = db.upsert_label(name, threat_status=threat_status)
    db._conn.execute(
        """UPDATE labels SET review_status = ?, artist_count = ?,
           threshold_crossed_at = datetime('now')
           WHERE id = ?""",
        (review_status, artist_count, lid),
    )
    db._conn.commit()
    return lid


def _add_songwriter(db, name, review_status=PENDING_REVIEW, artist_count=3):
    sid = db.upsert_songwriter(name, threat_status="suspected")
    db._conn.execute(
        """UPDATE songwriters SET review_status = ?, artist_count = ?,
           threshold_crossed_at = datetime('now')
           WHERE id = ?""",
        (review_status, artist_count, sid),
    )
    db._conn.commit()
    return sid


def _add_publisher(db, name, review_status=PENDING_REVIEW, artist_count=2):
    pid = db.upsert_publisher(name, threat_status="suspected")
    db._conn.execute(
        """UPDATE publishers SET review_status = ?, artist_count = ?,
           threshold_crossed_at = datetime('now')
           WHERE id = ?""",
        (review_status, artist_count, pid),
    )
    db._conn.commit()
    return pid


# ---------------------------------------------------------------------------
# Review Queue
# ---------------------------------------------------------------------------

class TestGetReviewQueue:
    def test_empty(self, db):
        assert db.get_review_queue() == []

    def test_pending_items_returned(self, db):
        _add_label(db, "L1")
        _add_label(db, "L2")
        items = db.get_review_queue()
        assert len(items) == 2

    def test_deferred_items_filtered(self, db):
        _add_label(db, "Pending", review_status=PENDING_REVIEW)
        _add_label(db, "Deferred", review_status=DEFERRED)
        items = db.get_review_queue(review_status=PENDING_REVIEW)
        assert len(items) == 1
        assert items[0]["name"] == "Pending"

    def test_filter_by_entity_type(self, db):
        _add_label(db, "L1")
        _add_songwriter(db, "S1")
        items = db.get_review_queue(entity_type="songwriter")
        assert len(items) == 1
        assert items[0]["entity_type"] == "songwriter"

    def test_sort_by_connection_count(self, db):
        _add_label(db, "Small", artist_count=2)
        _add_label(db, "Big", artist_count=20)
        items = db.get_review_queue(sort_by="connection_count")
        assert items[0]["name"] == "Big"

    def test_min_max_count_filters(self, db):
        _add_label(db, "Small", artist_count=2)
        _add_label(db, "Medium", artist_count=5)
        _add_label(db, "Big", artist_count=20)
        items = db.get_review_queue(min_count=3, max_count=10)
        assert len(items) == 1
        assert items[0]["name"] == "Medium"

    def test_pagination(self, db):
        for i in range(5):
            _add_label(db, f"Label {i}", artist_count=10 - i)
        items = db.get_review_queue(limit=2, offset=0)
        assert len(items) == 2
        items2 = db.get_review_queue(limit=2, offset=2)
        assert len(items2) == 2
        items3 = db.get_review_queue(limit=2, offset=4)
        assert len(items3) == 1

    def test_multi_entity_types_merged(self, db):
        _add_label(db, "L1", artist_count=10)
        _add_songwriter(db, "S1", artist_count=3)
        _add_publisher(db, "P1", artist_count=7)
        items = db.get_review_queue()
        assert len(items) == 3
        # Sorted by connection_count desc
        assert items[0]["artist_count"] == 10
        assert items[1]["artist_count"] == 7
        assert items[2]["artist_count"] == 3


# ---------------------------------------------------------------------------
# Queue Stats
# ---------------------------------------------------------------------------

class TestGetReviewQueueStats:
    def test_empty_stats(self, db):
        stats = db.get_review_queue_stats()
        assert stats["total_pending"] == 0
        assert stats["total_deferred"] == 0
        assert stats["total_reviewed"] == 0

    def test_counts_match(self, db):
        _add_label(db, "P1", review_status=PENDING_REVIEW)
        _add_label(db, "P2", review_status=PENDING_REVIEW)
        _add_label(db, "D1", review_status=DEFERRED)
        _add_songwriter(db, "S1", review_status=PENDING_REVIEW)
        stats = db.get_review_queue_stats()
        assert stats["total_pending"] == 3
        assert stats["total_deferred"] == 1
        assert stats["pending"]["label"] == 2
        assert stats["pending"]["songwriter"] == 1
        assert stats["deferred"]["label"] == 1


# ---------------------------------------------------------------------------
# Entity Detail
# ---------------------------------------------------------------------------

class TestGetEntityDetail:
    def test_not_found(self, db):
        assert db.get_entity_detail("label", 9999) is None

    def test_label_detail_structure(self, db):
        lid = _add_label(db, "Test Label")
        detail = db.get_entity_detail("label", lid)
        assert detail is not None
        assert detail["name"] == "Test Label"
        assert detail["entity_type"] == "label"
        assert "connected_artists" in detail
        assert "context_clues" in detail
        assert "observations" in detail
        assert "review_history" in detail
        assert "aliases" in detail
        assert "investigation_links" in detail

    def test_connected_artists_populated(self, db):
        lid = _add_label(db, "Multi Artist Label")
        a1 = db.upsert_artist("Artist One", threat_status="suspected")
        a2 = db.upsert_artist("Artist Two", threat_status="confirmed_bad")
        db.link_artist_label(a1, lid, source="deezer")
        db.link_artist_label(a2, lid, source="spotify")

        detail = db.get_entity_detail("label", lid)
        assert detail["total_artist_count"] == 2
        names = {a["name"] for a in detail["connected_artists"]}
        assert names == {"Artist One", "Artist Two"}

    def test_flagged_count(self, db):
        lid = _add_label(db, "Flagged Label")
        a1 = db.upsert_artist("Good", threat_status="cleared")
        a2 = db.upsert_artist("Bad", threat_status="confirmed_bad")
        a3 = db.upsert_artist("Sus", threat_status="suspected")
        db.link_artist_label(a1, lid, source="test")
        db.link_artist_label(a2, lid, source="test")
        db.link_artist_label(a3, lid, source="test")

        detail = db.get_entity_detail("label", lid)
        assert detail["total_artist_count"] == 3
        assert detail["flagged_artist_count"] == 2  # confirmed_bad + suspected

    def test_investigation_links_for_label(self, db):
        lid = _add_label(db, "Shady Label")
        detail = db.get_entity_detail("label", lid)
        link_labels = [l["label"] for l in detail["investigation_links"]]
        assert "Google" in link_labels
        assert "Discogs" in link_labels
        assert "MusicBrainz" in link_labels

    def test_investigation_links_for_songwriter(self, db):
        sid = _add_songwriter(db, "Fake Writer")
        detail = db.get_entity_detail("songwriter", sid)
        link_labels = [l["label"] for l in detail["investigation_links"]]
        assert "Genius" in link_labels
        assert "ASCAP Repertory" in link_labels


# ---------------------------------------------------------------------------
# Submit Review
# ---------------------------------------------------------------------------

class TestSubmitReview:
    def test_confirm_sets_confirmed_bad(self, db):
        lid = _add_label(db, "Bad Label")
        result = db.submit_review("label", lid, ACTION_CONFIRM, "Known PFC")
        assert result["success"] is True
        assert result["action"] == ACTION_CONFIRM
        assert result["blocklist_updated"] == "pfc_distributors.json"

        # Verify DB state
        detail = db.get_entity_detail("label", lid)
        assert detail["threat_status"] == "confirmed_bad"
        assert detail["review_status"] == "reviewed"

    def test_dismiss_sets_reviewed(self, db):
        lid = _add_label(db, "OK Label")
        result = db.submit_review("label", lid, ACTION_DISMISS, "False positive")
        assert result["success"] is True

        detail = db.get_entity_detail("label", lid)
        assert detail["review_status"] == "reviewed"
        assert detail["review_action"] == "dismissed"
        assert detail["dismiss_requeue_threshold"] is not None

    def test_defer_sets_deferred(self, db):
        lid = _add_label(db, "Maybe Label")
        result = db.submit_review("label", lid, ACTION_DEFER, "Wait for more data")
        assert result["success"] is True

        detail = db.get_entity_detail("label", lid)
        assert detail["review_status"] == "deferred"
        assert detail["review_action"] == "deferred"

    def test_invalid_action(self, db):
        lid = _add_label(db, "Some Label")
        result = db.submit_review("label", lid, "invalid_action")
        assert result["success"] is False

    def test_nonexistent_entity(self, db):
        result = db.submit_review("label", 9999, ACTION_CONFIRM)
        assert result["success"] is False

    def test_creates_audit_log_entry(self, db):
        lid = _add_label(db, "Logged Label")
        db.submit_review("label", lid, ACTION_CONFIRM, "Test note")
        history = db.get_review_history(entity_type="label", entity_id=lid)
        assert len(history) == 1
        assert history[0]["action"] == ACTION_CONFIRM
        assert history[0]["note"] == "Test note"

    def test_songwriter_confirm_updates_blocklist(self, db):
        sid = _add_songwriter(db, "Bad Writer")
        result = db.submit_review("songwriter", sid, ACTION_CONFIRM)
        assert result["blocklist_updated"] == "pfc_songwriters.json"


# ---------------------------------------------------------------------------
# Add Entity Note
# ---------------------------------------------------------------------------

class TestAddEntityNote:
    def test_add_note(self, db):
        lid = _add_label(db, "Noted")
        assert db.add_entity_note("label", lid, "First note") is True
        detail = db.get_entity_detail("label", lid)
        assert "First note" in detail["review_note"]

    def test_append_multiple_notes(self, db):
        lid = _add_label(db, "Multi Note")
        db.add_entity_note("label", lid, "Note 1")
        db.add_entity_note("label", lid, "Note 2")
        detail = db.get_entity_detail("label", lid)
        assert "Note 1" in detail["review_note"]
        assert "Note 2" in detail["review_note"]

    def test_note_nonexistent_entity(self, db):
        assert db.add_entity_note("label", 9999, "Ghost note") is False


# ---------------------------------------------------------------------------
# Review History
# ---------------------------------------------------------------------------

class TestReviewHistory:
    def test_empty_history(self, db):
        assert db.get_review_history() == []

    def test_history_populated_after_review(self, db):
        lid = _add_label(db, "HL")
        db.submit_review("label", lid, ACTION_CONFIRM, "Test")
        history = db.get_review_history()
        assert len(history) == 1
        assert history[0]["entity_type"] == "label"

    def test_history_filter_by_type(self, db):
        lid = _add_label(db, "HL")
        sid = _add_songwriter(db, "HS")
        db.submit_review("label", lid, ACTION_CONFIRM)
        db.submit_review("songwriter", sid, ACTION_DISMISS)
        history = db.get_review_history(entity_type="songwriter")
        assert len(history) == 1
        assert history[0]["entity_type"] == "songwriter"

    def test_history_pagination(self, db):
        for i in range(5):
            lid = _add_label(db, f"H{i}")
            db.submit_review("label", lid, ACTION_CONFIRM, f"Note {i}")
        history = db.get_review_history(limit=2)
        assert len(history) == 2


# ---------------------------------------------------------------------------
# Entity Aliases
# ---------------------------------------------------------------------------

class TestEntityAliases:
    def test_create_alias(self, db):
        lid1 = _add_label(db, "Label A")
        lid2 = _add_label(db, "Label A Records")
        alias_id = db.link_entity_alias("label", lid1, "label", lid2,
                                         relationship="alias", note="Same company")
        assert alias_id > 0

    def test_get_aliases(self, db):
        lid1 = _add_label(db, "L1")
        lid2 = _add_label(db, "L2")
        db.link_entity_alias("label", lid1, "label", lid2)
        aliases = db.get_entity_aliases("label", lid1)
        assert len(aliases) == 1

    def test_alias_bidirectional_lookup(self, db):
        lid1 = _add_label(db, "L1")
        lid2 = _add_label(db, "L2")
        db.link_entity_alias("label", lid1, "label", lid2)
        # Should find it from either side
        assert len(db.get_entity_aliases("label", lid1)) == 1
        assert len(db.get_entity_aliases("label", lid2)) == 1


# ---------------------------------------------------------------------------
# Blocklist Export/Sync
# ---------------------------------------------------------------------------

class TestBlocklists:
    def test_export_empty_blocklist(self, db):
        entries = db.export_blocklist("label")
        assert entries == []

    def test_export_after_confirm(self, db):
        lid = _add_label(db, "Evil Label")
        db.submit_review("label", lid, ACTION_CONFIRM)
        entries = db.export_blocklist("label")
        assert "Evil Label" in entries

    def test_sync_blocklists(self, db):
        result = db.sync_blocklists()
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Threshold Checking
# ---------------------------------------------------------------------------

class TestThresholds:
    def test_check_all_thresholds_empty(self, db):
        counts = db.check_all_thresholds()
        assert isinstance(counts, dict)

    def test_label_crosses_threshold(self, db):
        """When a label has enough artist connections, it should be queued."""
        lid = db.upsert_label("Growing Label")
        threshold = REVIEW_THRESHOLDS["label"]
        # Link enough artists
        for i in range(threshold):
            aid = db.upsert_artist(f"Artist {i}", threat_status="suspected")
            db.link_artist_label(aid, lid, source="test")
        db.refresh_entity_counts()
        counts = db.check_all_thresholds()
        # Verify the label was queued
        detail = db.get_entity_detail("label", lid)
        assert detail["review_status"] == PENDING_REVIEW
