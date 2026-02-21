#!/usr/bin/env python3
"""
Phase 4: Statistical Validation & Feature Engineering

Converts Phase 1-3 data into a quantified feature matrix, then tests
PFC corpus features against a control group to measure discriminative power.

Usage:
    python scripts/04_validate.py

Reads from:
    data/enriched/      - Enriched artist profiles
    data/entities/       - Entity graph from Phase 2
    data/seeds/control_group.json  - Known-legitimate control artists

Outputs to:
    data/features/       - feature_matrix.csv, signal_importance.json,
                           revised_scoring_weights.json
"""

from __future__ import annotations

import csv
import json
import logging
import math
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import mean, median, stdev as _stdev

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("validate")

DATA_DIR = PROJECT_ROOT / "data"
ENRICHED_DIR = DATA_DIR / "enriched"
ENTITIES_DIR = DATA_DIR / "entities"
SEEDS_DIR = DATA_DIR / "seeds"
FEATURES_DIR = DATA_DIR / "features"

FEATURE_MATRIX_FILE = FEATURES_DIR / "feature_matrix.csv"
SIGNAL_IMPORTANCE_FILE = FEATURES_DIR / "signal_importance.json"
SCORING_WEIGHTS_FILE = FEATURES_DIR / "revised_scoring_weights.json"
VALIDATION_SUMMARY_FILE = FEATURES_DIR / "_validation_summary.json"

PLATFORMS = ["musicbrainz", "deezer", "genius", "discogs", "setlistfm", "lastfm"]


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def extract_features(profile: dict, entity_data: dict, is_control: bool = False) -> dict:
    """Extract all features from a single enriched profile.

    Returns a flat dict of numeric/binary features suitable for CSV output.
    """
    artist_name = profile.get("artist_name", "")
    features = {
        "artist_name": artist_name,
        "is_pfc_corpus": 0 if is_control else 1,
    }

    # --- Platform presence ---
    for p in PLATFORMS:
        features[f"has_{p}"] = 1 if profile.get(p, {}).get("found", False) else 0
    features["platform_count"] = sum(features.get(f"has_{p}", 0) for p in PLATFORMS)

    # --- Deezer metrics ---
    dz = profile.get("deezer", {})
    features["deezer_fan_count"] = dz.get("nb_fan", 0) or 0
    features["deezer_fan_count_log"] = round(math.log10(max(features["deezer_fan_count"], 1)), 3)
    features["deezer_album_count"] = dz.get("nb_album", 0) or 0

    # Album type breakdown
    albums = dz.get("albums", [])
    singles = sum(1 for a in albums if isinstance(a, dict) and a.get("type", "").lower() in ("single", "ep"))
    full_albums = sum(1 for a in albums if isinstance(a, dict) and a.get("type", "").lower() == "album")
    total_releases = singles + full_albums
    features["singles_ratio"] = round(singles / total_releases, 3) if total_releases > 0 else 0.0

    # Track durations
    durations = dz.get("track_durations", [])
    valid_durations = [d for d in durations if isinstance(d, (int, float)) and d > 0]
    features["avg_track_duration_sec"] = round(mean(valid_durations), 1) if valid_durations else 0.0

    # Release cadence
    release_dates = []
    for a in albums:
        if isinstance(a, dict) and a.get("release_date"):
            try:
                dt = datetime.strptime(a["release_date"], "%Y-%m-%d")
                release_dates.append(dt)
            except ValueError:
                pass
    release_dates.sort()
    if len(release_dates) >= 2:
        intervals = [(release_dates[i] - release_dates[i - 1]).days
                      for i in range(1, len(release_dates)) if (release_dates[i] - release_dates[i - 1]).days > 0]
        if intervals and mean(intervals) > 0:
            features["release_cadence_cv"] = round(
                (_stdev(intervals) / mean(intervals)) if len(intervals) >= 2 else 0.0, 3
            )
        else:
            features["release_cadence_cv"] = 0.0
        features["career_lifespan_days"] = (release_dates[-1] - release_dates[0]).days

        # Burst detection: count releases per month
        month_counts = Counter(dt.strftime("%Y-%m") for dt in release_dates)
        features["has_burst_releases"] = 1 if any(c >= 10 for c in month_counts.values()) else 0
    else:
        features["release_cadence_cv"] = 0.0
        features["career_lifespan_days"] = 0
        features["has_burst_releases"] = 0

    # --- Discogs ---
    discogs = profile.get("discogs", {})
    features["has_discogs_physical"] = 1 if (discogs.get("physical_releases", 0) or 0) > 0 else 0
    features["discogs_physical_count"] = discogs.get("physical_releases", 0) or 0

    # --- Last.fm ---
    lastfm = profile.get("lastfm", {})
    features["lastfm_listeners"] = lastfm.get("listeners", 0) or 0
    features["lastfm_listeners_log"] = round(math.log10(max(features["lastfm_listeners"], 1)), 3)
    features["lastfm_listener_play_ratio"] = lastfm.get("listener_play_ratio", 0.0) or 0.0
    features["lastfm_bio_exists"] = 1 if lastfm.get("bio_exists", False) else 0

    # Similar artist corpus overlap
    similar = lastfm.get("similar_artists", [])
    dz_related = dz.get("related_artists", [])
    all_similar = list(set(
        [s for s in similar if isinstance(s, str)] +
        [r for r in dz_related if isinstance(r, str)]
    ))
    corpus_names = entity_data.get("corpus_names", set())
    in_corpus = sum(1 for s in all_similar if s.lower() in corpus_names)
    features["similar_artist_corpus_overlap"] = round(
        in_corpus / len(all_similar), 3
    ) if all_similar else 0.0

    # --- Genius ---
    genius = profile.get("genius", {})
    features["genius_song_count"] = genius.get("song_count", 0) or 0
    features["genius_followers"] = genius.get("followers_count", 0) or 0

    # --- Setlist.fm ---
    setlistfm = profile.get("setlistfm", {})
    features["setlist_count"] = setlistfm.get("total_setlists", 0) or 0
    features["setlist_country_count"] = len(setlistfm.get("venue_countries", []) or [])
    features["has_setlists"] = 1 if features["setlist_count"] > 0 else 0

    # --- MusicBrainz ---
    mb = profile.get("musicbrainz", {})
    features["has_isni"] = 1 if mb.get("isnis") else 0
    features["has_ipi"] = 1 if mb.get("ipis") else 0

    # --- Name features ---
    words = artist_name.split()
    features["name_word_count"] = len(words)
    features["name_char_count"] = len(artist_name)

    # --- Entity graph features ---
    producers = entity_data.get("producers", {})
    labels_entity = entity_data.get("labels", {})
    pfc_writers = entity_data.get("pfc_songwriters", set())
    pfc_labels = entity_data.get("pfc_distributors", set())

    # Max producer corpus percentage for this artist
    max_prod_pct = 0.0
    known_bad_producer = 0
    for prod_name, prod_data in producers.items():
        if isinstance(prod_data, dict) and artist_name in prod_data.get("artists", []):
            pct = prod_data.get("pfc_corpus_percentage", 0)
            if pct > max_prod_pct:
                max_prod_pct = pct
            if prod_data.get("known_bad_actor"):
                known_bad_producer = 1
    features["max_producer_corpus_pct"] = round(max_prod_pct, 4)
    features["known_bad_actor_producer"] = known_bad_producer

    # Label features
    label_exclusivity = 0.0
    known_bad_label = 0
    for label_name, label_data in labels_entity.items():
        if isinstance(label_data, dict) and artist_name in label_data.get("artists", []):
            pct = label_data.get("pfc_corpus_percentage", 0)
            if pct > label_exclusivity:
                label_exclusivity = pct
            if label_data.get("known_bad_actor"):
                known_bad_label = 1
    features["label_exclusivity_score"] = round(label_exclusivity, 4)
    features["known_bad_actor_label"] = known_bad_label

    # Disambiguation confidence
    mb_conf = mb.get("disambiguation_confidence", "low")
    features["disambiguation_high"] = 1 if mb_conf == "high" else 0
    features["disambiguation_ambiguous"] = 1 if mb_conf == "ambiguous" else 0

    return features


# ---------------------------------------------------------------------------
# Statistical tests
# ---------------------------------------------------------------------------

def _mann_whitney_u(x: list[float], y: list[float]) -> tuple[float, float, float]:
    """Simple Mann-Whitney U test implementation.

    Returns (U statistic, approximate p-value, rank-biserial effect size).
    Uses normal approximation for p-value (suitable for n > 20).
    """
    combined = [(v, 0) for v in x] + [(v, 1) for v in y]
    combined.sort(key=lambda t: t[0])

    # Assign ranks (average for ties)
    n = len(combined)
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n - 1 and combined[j + 1][0] == combined[j][0]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        i = j + 1

    n1, n2 = len(x), len(y)
    r1 = sum(ranks[i] for i in range(n) if combined[i][1] == 0)

    u1 = r1 - n1 * (n1 + 1) / 2
    u2 = n1 * n2 - u1

    u = min(u1, u2)

    # Normal approximation for p-value
    mu = n1 * n2 / 2
    sigma = math.sqrt(n1 * n2 * (n1 + n2 + 1) / 12)
    if sigma == 0:
        return u, 1.0, 0.0

    z = abs(u - mu) / sigma
    # Approximate two-tailed p-value using standard normal
    p = 2 * _normal_cdf(-z)

    # Rank-biserial effect size
    effect_size = 1 - (2 * u) / (n1 * n2) if n1 * n2 > 0 else 0.0

    return u, p, round(effect_size, 4)


def _normal_cdf(z: float) -> float:
    """Approximate standard normal CDF using Abramowitz and Stegun."""
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _fisher_exact_approx(a: int, b: int, c: int, d: int) -> tuple[float, float]:
    """Approximate Fisher's exact test using chi-squared for 2x2 table.

    Returns (p_value, odds_ratio).
    Table:  [[a, b], [c, d]]
    """
    n = a + b + c + d
    if n == 0:
        return 1.0, 1.0

    # Odds ratio
    if b == 0 or c == 0:
        odds_ratio = float("inf") if (a > 0 and d > 0) else 0.0
    else:
        odds_ratio = round((a * d) / (b * c), 2)

    # Chi-squared with Yates correction
    expected = [
        (a + b) * (a + c) / n,
        (a + b) * (b + d) / n,
        (c + d) * (a + c) / n,
        (c + d) * (b + d) / n,
    ]
    observed = [a, b, c, d]

    chi2 = 0
    for o, e in zip(observed, expected):
        if e > 0:
            chi2 += (abs(o - e) - 0.5) ** 2 / e

    # P-value from chi-squared with 1 df
    p = 1 - _chi2_cdf(chi2, 1)
    return round(p, 8), odds_ratio


def _chi2_cdf(x: float, k: int) -> float:
    """Approximate chi-squared CDF for k degrees of freedom."""
    if x <= 0:
        return 0.0
    # For k=1, chi2 CDF = 2*Phi(sqrt(x)) - 1
    if k == 1:
        return 2 * _normal_cdf(math.sqrt(x)) - 1
    # General approximation using Wilson-Hilferty
    z = ((x / k) ** (1 / 3) - (1 - 2 / (9 * k))) / math.sqrt(2 / (9 * k))
    return _normal_cdf(z)


# ---------------------------------------------------------------------------
# Signal validation
# ---------------------------------------------------------------------------

BINARY_FEATURES = [
    "has_musicbrainz", "has_deezer", "has_genius", "has_discogs",
    "has_setlistfm", "has_lastfm",
    "has_discogs_physical", "lastfm_bio_exists", "has_setlists",
    "has_burst_releases", "has_isni", "has_ipi",
    "known_bad_actor_producer", "known_bad_actor_label",
    "disambiguation_high", "disambiguation_ambiguous",
]

CONTINUOUS_FEATURES = [
    "platform_count", "deezer_fan_count_log", "deezer_album_count",
    "singles_ratio", "avg_track_duration_sec", "release_cadence_cv",
    "career_lifespan_days", "lastfm_listeners_log",
    "lastfm_listener_play_ratio", "genius_song_count", "genius_followers",
    "setlist_count", "setlist_country_count",
    "max_producer_corpus_pct", "label_exclusivity_score",
    "similar_artist_corpus_overlap", "name_word_count", "name_char_count",
    "discogs_physical_count",
]


def validate_signals(pfc_features: list[dict], control_features: list[dict]) -> list[dict]:
    """Run statistical tests comparing PFC corpus to control group.

    Tests binary features with Fisher's exact (approximation) and
    continuous features with Mann-Whitney U.
    """
    results = []

    # Binary features: Fisher's exact test
    for feat in BINARY_FEATURES:
        pfc_pos = sum(1 for f in pfc_features if f.get(feat, 0) == 1)
        pfc_neg = len(pfc_features) - pfc_pos
        ctrl_pos = sum(1 for f in control_features if f.get(feat, 0) == 1)
        ctrl_neg = len(control_features) - ctrl_pos

        pfc_rate = pfc_pos / len(pfc_features) if pfc_features else 0
        ctrl_rate = ctrl_pos / len(control_features) if control_features else 0

        p_val, odds = _fisher_exact_approx(pfc_pos, pfc_neg, ctrl_pos, ctrl_neg)

        results.append({
            "feature": feat,
            "test": "fisher_exact_approx",
            "p_value": p_val,
            "odds_ratio": odds,
            "pfc_rate": round(pfc_rate, 4),
            "control_rate": round(ctrl_rate, 4),
            "significant": p_val < 0.05,
            "direction": "higher_in_pfc" if pfc_rate > ctrl_rate else "lower_in_pfc",
        })

    # Continuous features: Mann-Whitney U test
    for feat in CONTINUOUS_FEATURES:
        pfc_vals = [f.get(feat, 0) for f in pfc_features if isinstance(f.get(feat, 0), (int, float))]
        ctrl_vals = [f.get(feat, 0) for f in control_features if isinstance(f.get(feat, 0), (int, float))]

        if len(pfc_vals) < 5 or len(ctrl_vals) < 5:
            results.append({
                "feature": feat,
                "test": "mann_whitney_u",
                "p_value": 1.0,
                "effect_size": 0.0,
                "pfc_median": median(pfc_vals) if pfc_vals else 0,
                "control_median": median(ctrl_vals) if ctrl_vals else 0,
                "significant": False,
                "note": "Insufficient data for test",
            })
            continue

        u, p_val, effect = _mann_whitney_u(pfc_vals, ctrl_vals)

        pfc_med = median(pfc_vals)
        ctrl_med = median(ctrl_vals)

        results.append({
            "feature": feat,
            "test": "mann_whitney_u",
            "p_value": round(p_val, 8),
            "effect_size": effect,
            "pfc_median": round(pfc_med, 3) if isinstance(pfc_med, float) else pfc_med,
            "control_median": round(ctrl_med, 3) if isinstance(ctrl_med, float) else ctrl_med,
            "significant": p_val < 0.05,
            "direction": "higher_in_pfc" if pfc_med > ctrl_med else "lower_in_pfc",
        })

    # Sort by significance and effect size
    results.sort(key=lambda r: (not r["significant"], -abs(r.get("effect_size", r.get("odds_ratio", 0)))))

    return results


def compute_revised_weights(signal_results: list[dict]) -> dict:
    """Convert signal importance into revised scoring weights.

    Uses effect sizes to weight features for the scoring model.
    """
    weights = {}
    for r in signal_results:
        if not r.get("significant"):
            continue

        feat = r["feature"]
        if r["test"] == "fisher_exact_approx":
            # Use log odds ratio as weight
            odds = r.get("odds_ratio", 1.0)
            if odds == float("inf"):
                weight = 10.0
            elif odds == 0:
                weight = -10.0
            else:
                weight = round(math.log(odds) if odds > 0 else 0, 3)
        else:
            # Use effect size as weight
            weight = r.get("effect_size", 0)
            # Flip sign if feature is lower in PFC
            if r.get("direction") == "lower_in_pfc":
                weight = -abs(weight)
            else:
                weight = abs(weight)

        weights[feat] = {
            "weight": round(weight, 3),
            "direction": r["direction"],
            "p_value": r["p_value"],
            "test": r["test"],
        }

    return dict(sorted(weights.items(), key=lambda x: abs(x[1]["weight"]), reverse=True))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Load enriched profiles
    if not ENRICHED_DIR.exists():
        logger.error("Enriched directory not found: %s", ENRICHED_DIR)
        sys.exit(1)

    profile_files = sorted([
        f for f in ENRICHED_DIR.glob("*.json")
        if not f.name.startswith("_")
    ])
    if not profile_files:
        logger.error("No enriched profiles found in %s", ENRICHED_DIR)
        sys.exit(1)

    logger.info("Loading %d enriched profiles...", len(profile_files))
    profiles = []
    for f in profile_files:
        try:
            with open(f) as fh:
                profiles.append(json.load(fh))
        except (json.JSONDecodeError, OSError):
            pass

    # Load entity data for graph features
    entity_data = {"producers": {}, "labels": {}, "corpus_names": set()}

    producers_path = ENTITIES_DIR / "producers.json"
    if producers_path.exists():
        with open(producers_path) as f:
            entity_data["producers"] = json.load(f)
        logger.info("Loaded producer entity graph (%d entries)", len(entity_data["producers"]))

    labels_path = ENTITIES_DIR / "labels.json"
    if labels_path.exists():
        with open(labels_path) as f:
            entity_data["labels"] = json.load(f)
        logger.info("Loaded label entity graph (%d entries)", len(entity_data["labels"]))

    # Build corpus name set
    entity_data["corpus_names"] = set(
        p.get("artist_name", "").lower() for p in profiles if p.get("artist_name")
    )

    # Load blocklists for entity matching
    blocklists_dir = PROJECT_ROOT / "spotify_audit" / "blocklists"
    try:
        with open(blocklists_dir / "pfc_songwriters.json") as f:
            entity_data["pfc_songwriters"] = set(n.lower() for n in json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        entity_data["pfc_songwriters"] = set()

    try:
        with open(blocklists_dir / "pfc_distributors.json") as f:
            entity_data["pfc_distributors"] = set(n.lower() for n in json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        entity_data["pfc_distributors"] = set()

    # Extract features for PFC corpus
    logger.info("Extracting features for %d PFC corpus artists...", len(profiles))
    pfc_features = []
    for p in profiles:
        try:
            feats = extract_features(p, entity_data, is_control=False)
            pfc_features.append(feats)
        except Exception as exc:
            logger.debug("Feature extraction failed for %s: %s", p.get("artist_name", "?"), exc)

    logger.info("Extracted features for %d PFC artists", len(pfc_features))

    # Load control group if available
    control_group_file = SEEDS_DIR / "control_group.json"
    control_features = []
    if control_group_file.exists():
        logger.info("Loading control group...")
        with open(control_group_file) as f:
            control_seeds = json.load(f)

        for seed in control_seeds:
            name = seed.get("artist_name", "")
            # Look for enriched profile
            safe_id = "".join(c if c.isalnum() or c in " -_" else "_" for c in name)
            safe_id = safe_id.strip().replace(" ", "_").lower()[:80]
            ctrl_path = ENRICHED_DIR / f"{safe_id}.json"
            if ctrl_path.exists():
                try:
                    with open(ctrl_path) as f:
                        profile = json.load(f)
                    feats = extract_features(profile, entity_data, is_control=True)
                    control_features.append(feats)
                except Exception as exc:
                    logger.debug("Control feature extraction failed for %s: %s", name, exc)

        logger.info("Extracted features for %d control artists", len(control_features))
    else:
        logger.warning("No control group found at %s", control_group_file)
        logger.warning("Statistical validation requires a control group. "
                       "Create data/seeds/control_group.json with known-legitimate artists "
                       "and enrich them through 01_enrich.py first.")

    # Write feature matrix
    FEATURES_DIR.mkdir(parents=True, exist_ok=True)

    all_features = pfc_features + control_features
    if all_features:
        # Determine column order
        columns = sorted(set().union(*(f.keys() for f in all_features)))
        # Put artist_name first, is_pfc_corpus last
        columns = (
            ["artist_name"] +
            [c for c in columns if c not in ("artist_name", "is_pfc_corpus")] +
            ["is_pfc_corpus"]
        )

        with open(FEATURE_MATRIX_FILE, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_features)

        logger.info("Wrote feature matrix: %d rows × %d columns → %s",
                    len(all_features), len(columns), FEATURE_MATRIX_FILE.name)

    # Run statistical validation
    signal_results = []
    if control_features:
        logger.info("Running statistical validation (PFC vs control)...")
        signal_results = validate_signals(pfc_features, control_features)

        with open(SIGNAL_IMPORTANCE_FILE, "w") as f:
            json.dump(signal_results, f, indent=2)

        significant = [r for r in signal_results if r["significant"]]
        logger.info("Signal importance: %d/%d features are statistically significant (p < 0.05)",
                    len(significant), len(signal_results))

        # Top 10 most discriminating features
        logger.info("\nTop discriminating features:")
        for r in signal_results[:10]:
            if r["test"] == "fisher_exact_approx":
                logger.info("  %s: OR=%.1f, PFC=%.0f%% vs Control=%.0f%% (p=%.4f)",
                            r["feature"], r.get("odds_ratio", 0),
                            r["pfc_rate"] * 100, r["control_rate"] * 100, r["p_value"])
            else:
                logger.info("  %s: effect=%.3f, PFC median=%.1f vs Control median=%.1f (p=%.4f)",
                            r["feature"], r.get("effect_size", 0),
                            r.get("pfc_median", 0), r.get("control_median", 0), r["p_value"])

        # Compute revised weights
        revised = compute_revised_weights(signal_results)
        with open(SCORING_WEIGHTS_FILE, "w") as f:
            json.dump(revised, f, indent=2)
        logger.info("Wrote revised scoring weights: %d significant features", len(revised))

    else:
        logger.info("Skipping statistical validation (no control group)")
        # Still compute PFC corpus-only statistics
        corpus_stats = []
        for feat in BINARY_FEATURES:
            rate = mean([f.get(feat, 0) for f in pfc_features]) if pfc_features else 0
            corpus_stats.append({
                "feature": feat,
                "type": "binary",
                "pfc_rate": round(rate, 4),
                "note": "No control group — rate only, no significance test",
            })
        for feat in CONTINUOUS_FEATURES:
            vals = [f.get(feat, 0) for f in pfc_features if isinstance(f.get(feat, 0), (int, float))]
            corpus_stats.append({
                "feature": feat,
                "type": "continuous",
                "pfc_median": round(median(vals), 3) if vals else 0,
                "pfc_mean": round(mean(vals), 3) if vals else 0,
                "note": "No control group — descriptive stats only",
            })

        with open(SIGNAL_IMPORTANCE_FILE, "w") as f:
            json.dump(corpus_stats, f, indent=2)
        logger.info("Wrote PFC corpus descriptive stats (no validation without control group)")

    # Summary
    from datetime import datetime, timezone
    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pfc_artists": len(pfc_features),
        "control_artists": len(control_features),
        "total_features": len(BINARY_FEATURES) + len(CONTINUOUS_FEATURES),
        "binary_features": len(BINARY_FEATURES),
        "continuous_features": len(CONTINUOUS_FEATURES),
        "significant_features": len([r for r in signal_results if r.get("significant")]),
        "control_group_available": bool(control_features),
    }
    with open(VALIDATION_SUMMARY_FILE, "w") as f:
        json.dump(summary, f, indent=2)

    logger.info("\n=== Phase 4: Validation Complete ===")
    logger.info("PFC corpus: %d | Control: %d | Features: %d",
                len(pfc_features), len(control_features),
                len(BINARY_FEATURES) + len(CONTINUOUS_FEATURES))
    logger.info("Output: %s", FEATURES_DIR)


if __name__ == "__main__":
    main()
