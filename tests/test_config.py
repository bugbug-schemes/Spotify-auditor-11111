"""Tests for spotify_audit.config — weights, labels, blocklists."""

from spotify_audit.config import (
    AuditConfig,
    QuickWeights,
    StandardWeights,
    DeepWeights,
    SCORE_LABELS,
    THREAT_CATEGORIES,
    score_label,
    load_blocklist,
    pfc_distributors,
    known_ai_artists,
    pfc_songwriters,
)


# ---------------------------------------------------------------------------
# Score labels
# ---------------------------------------------------------------------------

class TestScoreLabel:
    def test_verified_artist(self):
        assert score_label(100) == "Verified Artist"
        assert score_label(82) == "Verified Artist"
        assert score_label(90) == "Verified Artist"

    def test_likely_authentic(self):
        assert score_label(81) == "Likely Authentic"
        assert score_label(58) == "Likely Authentic"
        assert score_label(65) == "Likely Authentic"

    def test_inconclusive(self):
        assert score_label(57) == "Inconclusive"
        assert score_label(38) == "Inconclusive"
        assert score_label(44) == "Inconclusive"

    def test_suspicious(self):
        assert score_label(37) == "Suspicious"
        assert score_label(18) == "Suspicious"
        assert score_label(25) == "Suspicious"

    def test_likely_artificial(self):
        assert score_label(17) == "Likely Artificial"
        assert score_label(0) == "Likely Artificial"
        assert score_label(7) == "Likely Artificial"

    def test_boundary_values(self):
        """Test exact boundaries between score ranges."""
        assert score_label(82) == "Verified Artist"
        assert score_label(81) == "Likely Authentic"
        assert score_label(58) == "Likely Authentic"
        assert score_label(57) == "Inconclusive"
        assert score_label(38) == "Inconclusive"
        assert score_label(37) == "Suspicious"
        assert score_label(18) == "Suspicious"
        assert score_label(17) == "Likely Artificial"

    def test_out_of_range(self):
        assert score_label(-1) == "Unknown"
        assert score_label(101) == "Unknown"


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------

class TestWeights:
    def test_quick_weights_sum_to_one(self):
        w = QuickWeights()
        assert abs(w.total() - 1.0) < 0.01

    def test_standard_weights_sum_to_one(self):
        w = StandardWeights()
        assert abs(w.total() - 1.0) < 0.01

    def test_deep_weights_sum_to_one(self):
        w = DeepWeights()
        assert abs(w.total() - 1.0) < 0.01

    def test_quick_weights_normalized(self):
        w = QuickWeights()
        normed = w.normalized()
        assert abs(sum(normed.values()) - 1.0) < 0.001


# ---------------------------------------------------------------------------
# AuditConfig
# ---------------------------------------------------------------------------

class TestAuditConfig:
    def test_defaults(self):
        config = AuditConfig()
        assert config.escalate_to_deep == 50
        assert config.cache_ttl_days == 7
        assert config.max_retries == 5

    def test_custom_config(self):
        config = AuditConfig(escalate_to_deep=60)
        assert config.escalate_to_deep == 60


# ---------------------------------------------------------------------------
# Threat categories
# ---------------------------------------------------------------------------

class TestThreatCategories:
    def test_all_categories_present(self):
        assert 1 in THREAT_CATEGORIES
        assert 1.5 in THREAT_CATEGORIES
        assert 2 in THREAT_CATEGORIES
        assert 3 in THREAT_CATEGORIES
        assert 4 in THREAT_CATEGORIES

    def test_category_names(self):
        assert THREAT_CATEGORIES[1] == "PFC Ghost Artist"
        assert THREAT_CATEGORIES[4] == "AI Impersonation"


# ---------------------------------------------------------------------------
# Blocklists
# ---------------------------------------------------------------------------

class TestBlocklists:
    def test_load_existing_blocklist(self):
        pfc = pfc_distributors()
        assert isinstance(pfc, frozenset)
        assert len(pfc) > 0

    def test_load_known_ai(self):
        ai = known_ai_artists()
        assert isinstance(ai, frozenset)
        assert len(ai) > 0

    def test_load_pfc_songwriters(self):
        writers = pfc_songwriters()
        assert isinstance(writers, frozenset)

    def test_load_nonexistent_blocklist(self):
        result = load_blocklist("does_not_exist")
        assert result == []

    def test_blocklist_entries_are_strings(self):
        for entry in pfc_distributors():
            assert isinstance(entry, str)
