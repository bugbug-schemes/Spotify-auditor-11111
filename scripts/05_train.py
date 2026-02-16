#!/usr/bin/env python3
"""
Phase 5: Ground Truth Labeling & Model Training

Assigns ground truth labels based on Phase 1-4 data, trains a classifier
(Random Forest primary, Logistic Regression secondary), and outputs
feature importance rankings and a validation report.

Usage:
    python scripts/05_train.py

Reads from:
    data/features/feature_matrix.csv    - Feature matrix from Phase 4
    data/entities/                       - Entity graph for labeling
    data/enriched/                       - For ground truth assignment

Outputs to:
    data/model/   - classifier.pkl, validation_report.json
"""

from __future__ import annotations

import csv
import json
import logging
import math
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("train")

DATA_DIR = PROJECT_ROOT / "data"
FEATURES_DIR = DATA_DIR / "features"
ENTITIES_DIR = DATA_DIR / "entities"
ENRICHED_DIR = DATA_DIR / "enriched"
MODEL_DIR = DATA_DIR / "model"

FEATURE_MATRIX_FILE = FEATURES_DIR / "feature_matrix.csv"
VALIDATION_REPORT_FILE = MODEL_DIR / "validation_report.json"
CLASSIFIER_FILE = MODEL_DIR / "classifier.pkl"
GROUND_TRUTH_FILE = MODEL_DIR / "ground_truth_labels.json"

# Features to exclude from model training
EXCLUDE_FEATURES = {"artist_name", "is_pfc_corpus"}

# Ground truth label criteria per pipeline spec
LABEL_CONFIRMED_PFC = "CONFIRMED_PFC"
LABEL_LIKELY_PFC = "LIKELY_PFC"
LABEL_UNCERTAIN = "UNCERTAIN"
LABEL_LIKELY_LEGIT = "LIKELY_LEGIT"
LABEL_CONTROL = "CONTROL"


# ---------------------------------------------------------------------------
# Ground truth labeling
# ---------------------------------------------------------------------------

def assign_ground_truth(features: dict, entity_data: dict) -> str:
    """Assign a ground truth label based on Phase 1-4 data.

    Labels per pipeline spec:
    - CONFIRMED_PFC: Matches bad actor DB (label OR producer) AND missing 4+ platforms
    - LIKELY_PFC: Missing 4+ platforms + high-PFC producer/label, not in bad actor DB
    - LIKELY_LEGIT: 5+ platforms, physical releases or live shows, no bad actor connections
    - UNCERTAIN: Mixed signals
    - CONTROL: External control group
    """
    if features.get("is_pfc_corpus") == 0:
        return LABEL_CONTROL

    platform_count = features.get("platform_count", 0)
    has_bad_producer = features.get("known_bad_actor_producer", 0) == 1
    has_bad_label = features.get("known_bad_actor_label", 0) == 1
    has_physical = features.get("has_discogs_physical", 0) == 1
    has_setlists = features.get("has_setlists", 0) == 1
    missing_platforms = 6 - platform_count

    # CONFIRMED_PFC: Bad actor match + missing 4+ platforms
    if (has_bad_producer or has_bad_label) and missing_platforms >= 4:
        return LABEL_CONFIRMED_PFC

    # LIKELY_LEGIT: 5+ platforms + physical/live + no bad actors
    if platform_count >= 5 and (has_physical or has_setlists) and not has_bad_producer and not has_bad_label:
        return LABEL_LIKELY_LEGIT

    # LIKELY_PFC: Missing 4+ platforms + high-PFC signals (even without bad actor match)
    high_producer_pct = features.get("max_producer_corpus_pct", 0) > 0.01
    high_label_pct = features.get("label_exclusivity_score", 0) > 0.05
    if missing_platforms >= 4 and (high_producer_pct or high_label_pct):
        return LABEL_LIKELY_PFC

    # Additional LIKELY_PFC: Missing 5+ platforms (very sparse presence)
    if missing_platforms >= 5:
        return LABEL_LIKELY_PFC

    # UNCERTAIN: Everything else
    return LABEL_UNCERTAIN


# ---------------------------------------------------------------------------
# Simple model training (no sklearn dependency)
# ---------------------------------------------------------------------------

def _compute_split_quality(
    y: list[int],
    feature_values: list[float],
    threshold: float,
) -> float:
    """Compute Gini impurity reduction for a binary split."""
    left_y = [y[i] for i in range(len(y)) if feature_values[i] <= threshold]
    right_y = [y[i] for i in range(len(y)) if feature_values[i] > threshold]

    if not left_y or not right_y:
        return 0.0

    def gini(ys):
        n = len(ys)
        if n == 0:
            return 0.0
        p1 = sum(ys) / n
        return 1 - p1 ** 2 - (1 - p1) ** 2

    n = len(y)
    return gini(y) - (len(left_y) / n * gini(left_y) + len(right_y) / n * gini(right_y))


def train_simple_tree(
    X: list[dict[str, float]],
    y: list[int],
    feature_names: list[str],
    max_depth: int = 5,
) -> dict:
    """Train a simple decision tree for feature importance estimation.

    Returns a dict with feature importances and predictions.
    """
    importances = {f: 0.0 for f in feature_names}

    # For each feature, find best split and measure quality
    for feat in feature_names:
        vals = [x.get(feat, 0) for x in X]
        unique_vals = sorted(set(vals))
        if len(unique_vals) < 2:
            continue

        best_quality = 0.0
        for i in range(len(unique_vals) - 1):
            threshold = (unique_vals[i] + unique_vals[i + 1]) / 2
            quality = _compute_split_quality(y, vals, threshold)
            if quality > best_quality:
                best_quality = quality

        importances[feat] = best_quality

    # Normalize importances
    total = sum(importances.values())
    if total > 0:
        importances = {f: round(v / total, 4) for f, v in importances.items()}

    return {
        "importances": dict(sorted(importances.items(), key=lambda x: x[1], reverse=True)),
    }


def mean(values):
    """Simple mean."""
    return sum(values) / len(values) if values else 0


def cross_validate(
    X: list[dict[str, float]],
    y: list[int],
    feature_names: list[str],
    n_folds: int = 5,
) -> dict:
    """Simple k-fold cross-validation using threshold-based classification.

    For each fold, uses the most important features to make predictions
    based on simple threshold rules derived from training data.
    """
    n = len(X)
    if n < n_folds * 2:
        logger.warning("Too few samples (%d) for %d-fold CV", n, n_folds)
        return {"error": "insufficient_data"}

    fold_size = n // n_folds
    all_preds = [0] * n
    all_true = list(y)

    for fold in range(n_folds):
        # Split
        test_start = fold * fold_size
        test_end = test_start + fold_size if fold < n_folds - 1 else n

        test_idx = list(range(test_start, test_end))
        train_idx = [i for i in range(n) if i not in set(test_idx)]

        train_X = [X[i] for i in train_idx]
        train_y = [y[i] for i in train_idx]

        # Find best single-feature threshold for this fold
        best_feat = None
        best_thresh = 0.0
        best_acc = 0.0

        for feat in feature_names:
            vals = [x.get(feat, 0) for x in train_X]
            unique = sorted(set(vals))
            for i in range(min(len(unique) - 1, 20)):  # Sample thresholds
                if i >= len(unique) - 1:
                    break
                thresh = (unique[i] + unique[i + 1]) / 2
                preds = [1 if v <= thresh else 0 for v in vals]
                acc = sum(1 for p, t in zip(preds, train_y) if p == t) / len(train_y)
                # Also try inverted
                preds_inv = [0 if v <= thresh else 1 for v in vals]
                acc_inv = sum(1 for p, t in zip(preds_inv, train_y) if p == t) / len(train_y)

                if max(acc, acc_inv) > best_acc:
                    best_acc = max(acc, acc_inv)
                    best_feat = feat
                    best_thresh = thresh

        # Predict test fold
        if best_feat:
            for i in test_idx:
                val = X[i].get(best_feat, 0)
                # Determine direction from training data
                train_vals = [X[j].get(best_feat, 0) for j in train_idx]
                pos_mean = mean([v for v, t in zip(train_vals, train_y) if t == 1]) if any(train_y) else 0
                neg_mean = mean([v for v, t in zip(train_vals, train_y) if t == 0]) if any(1 - t for t in train_y) else 0
                if pos_mean > neg_mean:
                    all_preds[i] = 1 if val > best_thresh else 0
                else:
                    all_preds[i] = 1 if val <= best_thresh else 0

    # Compute metrics
    tp = sum(1 for p, t in zip(all_preds, all_true) if p == 1 and t == 1)
    fp = sum(1 for p, t in zip(all_preds, all_true) if p == 1 and t == 0)
    tn = sum(1 for p, t in zip(all_preds, all_true) if p == 0 and t == 0)
    fn = sum(1 for p, t in zip(all_preds, all_true) if p == 0 and t == 1)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    accuracy = (tp + tn) / len(all_true) if all_true else 0

    return {
        "cv_folds": n_folds,
        "accuracy": round(accuracy, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "confusion_matrix": {
            "true_positive": tp,
            "false_positive": fp,
            "true_negative": tn,
            "false_negative": fn,
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Load feature matrix
    if not FEATURE_MATRIX_FILE.exists():
        logger.error("Feature matrix not found: %s", FEATURE_MATRIX_FILE)
        logger.error("Run 04_validate.py first.")
        sys.exit(1)

    logger.info("Loading feature matrix...")
    rows = []
    with open(FEATURE_MATRIX_FILE, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Convert numeric fields
            converted = {}
            for k, v in row.items():
                try:
                    converted[k] = float(v)
                except (ValueError, TypeError):
                    converted[k] = v
            rows.append(converted)

    logger.info("Loaded %d rows from feature matrix", len(rows))

    # Load entity data for ground truth labeling
    entity_data = {}
    for fname in ["producers.json", "labels.json"]:
        path = ENTITIES_DIR / fname
        if path.exists():
            with open(path) as f:
                entity_data[fname.replace(".json", "")] = json.load(f)

    # Assign ground truth labels
    logger.info("Assigning ground truth labels...")
    labels = []
    label_map = {}
    for row in rows:
        label = assign_ground_truth(row, entity_data)
        labels.append(label)
        artist = row.get("artist_name", "")
        if artist:
            label_map[artist] = label

    label_dist = Counter(labels)
    logger.info("Label distribution:")
    for label, count in sorted(label_dist.items()):
        logger.info("  %s: %d", label, count)

    # Save ground truth
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with open(GROUND_TRUTH_FILE, "w") as f:
        json.dump({
            "label_distribution": dict(label_dist),
            "labels": label_map,
        }, f, indent=2, ensure_ascii=False)

    # Prepare training data
    # Positive class: CONFIRMED_PFC
    # Negative class: LIKELY_LEGIT + CONTROL
    # Exclude: UNCERTAIN, LIKELY_PFC (noisy labels)
    feature_names = [k for k in rows[0].keys() if k not in EXCLUDE_FEATURES]
    feature_names = [f for f in feature_names if all(
        isinstance(row.get(f, 0), (int, float)) for row in rows[:5]
    )]

    train_X = []
    train_y = []
    for row, label in zip(rows, labels):
        if label == LABEL_CONFIRMED_PFC:
            train_X.append({f: float(row.get(f, 0)) for f in feature_names})
            train_y.append(1)
        elif label in (LABEL_LIKELY_LEGIT, LABEL_CONTROL):
            train_X.append({f: float(row.get(f, 0)) for f in feature_names})
            train_y.append(0)

    logger.info("Training set: %d samples (%d positive, %d negative)",
                len(train_X), sum(train_y), len(train_y) - sum(train_y))

    if len(train_X) < 10:
        logger.warning("Insufficient training data (%d samples). Need more enriched profiles "
                       "and/or a control group.", len(train_X))
        logger.info("Writing partial results...")

        report = {
            "status": "insufficient_data",
            "reason": f"Only {len(train_X)} training samples available. Need at least 50+ "
                     "CONFIRMED_PFC and 50+ CONTROL/LIKELY_LEGIT artists.",
            "label_distribution": dict(label_dist),
            "recommendation": "1) Wait for enrichment to complete, "
                            "2) Create control_group.json with known-legitimate artists, "
                            "3) Run 01_enrich.py on control artists, "
                            "4) Re-run 04_validate.py and 05_train.py",
        }
        with open(VALIDATION_REPORT_FILE, "w") as f:
            json.dump(report, f, indent=2)
        logger.info("Output: %s", MODEL_DIR)
        return

    # Try to use sklearn if available, otherwise use simple implementation
    try:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import StratifiedKFold, cross_val_predict
        from sklearn.metrics import precision_score, recall_score, f1_score, accuracy_score
        import pickle
        import numpy as np

        HAS_SKLEARN = True
        logger.info("Using scikit-learn for model training")
    except ImportError:
        HAS_SKLEARN = False
        logger.info("scikit-learn not available — using simple decision tree")

    if HAS_SKLEARN:
        _train_sklearn(train_X, train_y, feature_names, label_dist, rows, labels)
    else:
        _train_simple(train_X, train_y, feature_names, label_dist)

    logger.info("\n=== Phase 5: Model Training Complete ===")
    logger.info("Output: %s", MODEL_DIR)


def _train_sklearn(train_X, train_y, feature_names, label_dist, all_rows, all_labels):
    """Train with scikit-learn (Random Forest + Logistic Regression)."""
    import pickle
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold, cross_val_predict
    from sklearn.metrics import precision_score, recall_score, f1_score, accuracy_score

    # Convert to numpy
    X = np.array([[row.get(f, 0) for f in feature_names] for row in train_X])
    y = np.array(train_y)

    # Handle NaN/inf
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    # Random Forest
    logger.info("Training Random Forest...")
    rf = RandomForestClassifier(
        n_estimators=100,
        max_depth=10,
        min_samples_leaf=5,
        class_weight="balanced",
        random_state=42,
    )

    # Cross-validation
    n_folds = min(5, min(Counter(y).values()))
    if n_folds >= 2:
        skf = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=42)
        cv_preds = cross_val_predict(rf, X, y, cv=skf)

        rf_metrics = {
            "accuracy": round(accuracy_score(y, cv_preds), 4),
            "precision": round(precision_score(y, cv_preds, zero_division=0), 4),
            "recall": round(recall_score(y, cv_preds, zero_division=0), 4),
            "f1": round(f1_score(y, cv_preds, zero_division=0), 4),
        }

        tp = int(np.sum((cv_preds == 1) & (y == 1)))
        fp = int(np.sum((cv_preds == 1) & (y == 0)))
        tn = int(np.sum((cv_preds == 0) & (y == 0)))
        fn = int(np.sum((cv_preds == 0) & (y == 1)))

        rf_metrics["confusion_matrix"] = {
            "true_positive": tp,
            "false_positive": fp,
            "true_negative": tn,
            "false_negative": fn,
        }
        rf_metrics["cv_folds"] = n_folds
    else:
        rf_metrics = {"error": "insufficient_folds"}

    # Fit on full data for feature importances
    rf.fit(X, y)
    rf_importances = [
        {"feature": f, "importance": round(float(imp), 4)}
        for f, imp in sorted(zip(feature_names, rf.feature_importances_),
                             key=lambda x: x[1], reverse=True)
    ]

    # Save model
    with open(CLASSIFIER_FILE, "wb") as f:
        pickle.dump({
            "model": rf,
            "feature_names": feature_names,
            "model_type": "RandomForestClassifier",
        }, f)
    logger.info("Saved classifier: %s", CLASSIFIER_FILE.name)

    # Logistic Regression (secondary)
    logger.info("Training Logistic Regression (L1)...")
    lr = LogisticRegression(
        penalty="l1",
        solver="saga",
        max_iter=5000,
        class_weight="balanced",
        random_state=42,
    )

    lr_metrics = {}
    if n_folds >= 2:
        try:
            lr_preds = cross_val_predict(lr, X, y, cv=skf)
            lr_metrics = {
                "accuracy": round(accuracy_score(y, lr_preds), 4),
                "precision": round(precision_score(y, lr_preds, zero_division=0), 4),
                "recall": round(recall_score(y, lr_preds, zero_division=0), 4),
                "f1": round(f1_score(y, lr_preds, zero_division=0), 4),
            }
        except Exception as exc:
            lr_metrics = {"error": str(exc)}

    lr.fit(X, y)
    lr_coefs = [
        {"feature": f, "coefficient": round(float(c), 4)}
        for f, c in sorted(zip(feature_names, lr.coef_[0]),
                           key=lambda x: abs(x[1]), reverse=True)
        if abs(c) > 0.001
    ]

    # Full report
    report = {
        "primary_model": {
            "type": "RandomForestClassifier",
            "metrics": rf_metrics,
            "feature_importances": rf_importances,
        },
        "secondary_model": {
            "type": "LogisticRegression_L1",
            "metrics": lr_metrics,
            "coefficients": lr_coefs,
        },
        "training_set": {
            "total": len(train_y),
            "positive": int(sum(train_y)),
            "negative": int(len(train_y) - sum(train_y)),
        },
        "label_distribution": dict(label_dist),
    }

    with open(VALIDATION_REPORT_FILE, "w") as f:
        json.dump(report, f, indent=2)

    logger.info("Random Forest: precision=%.3f, recall=%.3f, F1=%.3f",
                rf_metrics.get("precision", 0), rf_metrics.get("recall", 0), rf_metrics.get("f1", 0))
    if lr_metrics and "precision" in lr_metrics:
        logger.info("Logistic Regression: precision=%.3f, recall=%.3f, F1=%.3f",
                    lr_metrics.get("precision", 0), lr_metrics.get("recall", 0), lr_metrics.get("f1", 0))

    logger.info("\nTop 10 most important features (Random Forest):")
    for fi in rf_importances[:10]:
        logger.info("  %s: %.4f", fi["feature"], fi["importance"])


def _train_simple(train_X, train_y, feature_names, label_dist):
    """Train with simple implementation (no sklearn)."""
    logger.info("Training simple decision tree for feature importance...")

    tree_result = train_simple_tree(train_X, train_y, feature_names)

    logger.info("Computing cross-validation metrics...")
    cv_result = cross_validate(train_X, train_y, feature_names, n_folds=5)

    report = {
        "primary_model": {
            "type": "SimpleDecisionTree",
            "metrics": cv_result,
            "feature_importances": [
                {"feature": f, "importance": imp}
                for f, imp in tree_result["importances"].items()
                if imp > 0
            ],
        },
        "secondary_model": {
            "type": "none",
            "note": "Install scikit-learn for Random Forest + Logistic Regression: "
                   "pip install scikit-learn",
        },
        "training_set": {
            "total": len(train_y),
            "positive": sum(train_y),
            "negative": len(train_y) - sum(train_y),
        },
        "label_distribution": dict(label_dist),
    }

    with open(VALIDATION_REPORT_FILE, "w") as f:
        json.dump(report, f, indent=2)

    logger.info("Simple tree: accuracy=%.3f, precision=%.3f, recall=%.3f, F1=%.3f",
                cv_result.get("accuracy", 0), cv_result.get("precision", 0),
                cv_result.get("recall", 0), cv_result.get("f1", 0))

    logger.info("\nTop 10 most important features:")
    for f, imp in list(tree_result["importances"].items())[:10]:
        if imp > 0:
            logger.info("  %s: %.4f", f, imp)


if __name__ == "__main__":
    main()
