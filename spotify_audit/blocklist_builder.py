"""
Blocklist builder — analyzes scan results to suggest blocklist additions.

After scanning a playlist, this module examines labels, contributors,
and patterns across all evaluated artists to identify:
  - Labels that appear on multiple suspicious artists
  - Contributors that frequently collaborate with flagged artists
  - Artists whose evidence strongly suggests artificial origin

This is the data-driven blocklist generation the user requested:
"use ALL of those sources to generate blacklists."
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from spotify_audit.evidence import ArtistEvaluation, Verdict
from spotify_audit.config import pfc_distributors, known_ai_artists

logger = logging.getLogger(__name__)


@dataclass
class BlocklistSuggestion:
    """A suggested addition to a blocklist."""
    value: str             # The label name, artist name, etc.
    blocklist: str         # Which blocklist ("pfc_distributors", "known_ai_artists", etc.)
    reason: str            # Why this is being suggested
    confidence: str        # "high", "medium", "low"
    seen_on: list[str] = field(default_factory=list)  # Artist names where it was found


@dataclass
class BlocklistReport:
    """Summary of blocklist intelligence gathered from a scan."""
    suggestions: list[BlocklistSuggestion] = field(default_factory=list)

    # Stats
    labels_seen: dict[str, list[str]] = field(default_factory=dict)  # label → [artist names]
    contributors_seen: dict[str, list[str]] = field(default_factory=dict)  # contributor → [artist names]

    # Labels on suspicious artists
    suspicious_labels: dict[str, list[str]] = field(default_factory=dict)  # label → [suspicious artist names]
    suspicious_contributors: dict[str, list[str]] = field(default_factory=dict)

    @property
    def has_suggestions(self) -> bool:
        return len(self.suggestions) > 0


def analyze_for_blocklist(evaluations: list[ArtistEvaluation]) -> BlocklistReport:
    """Analyze all artist evaluations to generate blocklist intelligence.

    Examines labels and contributors across the entire playlist,
    cross-referencing with which artists are flagged as suspicious or artificial.
    """
    report = BlocklistReport()

    # Current blocklists for comparison
    current_pfc = pfc_distributors()  # already lowercased frozenset
    current_ai = known_ai_artists()  # already lowercased frozenset

    # Collect all labels and contributors across artists
    for ev in evaluations:
        for label in ev.labels:
            if label not in report.labels_seen:
                report.labels_seen[label] = []
            report.labels_seen[label].append(ev.artist_name)

        for contributor in ev.contributors:
            if contributor not in report.contributors_seen:
                report.contributors_seen[contributor] = []
            report.contributors_seen[contributor].append(ev.artist_name)

    # Identify suspicious/artificial artists
    flagged_names = set()
    for ev in evaluations:
        if ev.verdict in (Verdict.SUSPICIOUS, Verdict.LIKELY_ARTIFICIAL):
            flagged_names.add(ev.artist_name)

    # Find labels that appear on suspicious artists
    for ev in evaluations:
        if ev.artist_name in flagged_names:
            for label in ev.labels:
                if label not in report.suspicious_labels:
                    report.suspicious_labels[label] = []
                report.suspicious_labels[label].append(ev.artist_name)

    # Find contributors that appear on suspicious artists
    for ev in evaluations:
        if ev.artist_name in flagged_names:
            for contributor in ev.contributors:
                if contributor not in report.suspicious_contributors:
                    report.suspicious_contributors[contributor] = []
                report.suspicious_contributors[contributor].append(ev.artist_name)

    # Generate suggestions

    # 1. Labels seen on multiple suspicious artists (not already on blocklist)
    for label, artists in report.suspicious_labels.items():
        if label.lower() in current_pfc:
            continue  # Already on blocklist
        if len(artists) >= 2:
            report.suggestions.append(BlocklistSuggestion(
                value=label,
                blocklist="pfc_distributors",
                reason=f"Found on {len(artists)} suspicious artists in this playlist",
                confidence="medium" if len(artists) >= 3 else "low",
                seen_on=artists,
            ))

    # 2. Artists flagged as Likely Artificial (not already on blocklist)
    for ev in evaluations:
        if ev.verdict == Verdict.LIKELY_ARTIFICIAL and ev.artist_name.lower() not in current_ai:
            report.suggestions.append(BlocklistSuggestion(
                value=ev.artist_name,
                blocklist="known_ai_artists",
                reason=f"Evaluated as Likely Artificial ({ev.confidence} confidence)",
                confidence=ev.confidence,
                seen_on=[ev.artist_name],
            ))

    # 3. Contributors who only appear on suspicious artists
    for contributor, artists in report.contributors_seen.items():
        suspicious_count = sum(1 for a in artists if a in flagged_names)
        if suspicious_count >= 2 and suspicious_count == len(artists):
            report.suggestions.append(BlocklistSuggestion(
                value=contributor,
                blocklist="suspicious_contributors",
                reason=f"Appears exclusively on {suspicious_count} suspicious artists",
                confidence="low",
                seen_on=artists,
            ))

    return report
