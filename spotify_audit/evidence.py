"""
Evidence-based artist evaluation framework.

Replaces the simple weighted 0-100 score with a decision tree that
collects evidence (red flags, green flags) from all available data and
produces an explainable verdict per artist.

This is the core of the "media literacy" output: instead of a single
opaque number, users see *why* we think an artist is real or artificial,
with specific findings from each data source.
"""

from __future__ import annotations

import statistics
import re
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from spotify_audit.spotify_client import ArtistInfo
from spotify_audit.config import pfc_distributors, known_ai_artists

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class Verdict(Enum):
    """Artist authenticity verdict — ordered from most to least trustworthy."""
    VERIFIED_ARTIST = "Verified Artist"
    LIKELY_AUTHENTIC = "Likely Authentic"
    INCONCLUSIVE = "Inconclusive"
    SUSPICIOUS = "Suspicious"
    LIKELY_ARTIFICIAL = "Likely Artificial"


@dataclass
class Evidence:
    """A single piece of evidence about an artist."""
    finding: str          # Short summary (e.g. "Found on Deezer with 145,231 fans")
    source: str           # Data source (e.g. "Deezer", "Spotify", "Blocklist")
    evidence_type: str    # "red_flag", "green_flag", "neutral"
    strength: str         # "strong", "moderate", "weak"
    detail: str           # Longer explanation for the user


@dataclass
class PlatformPresence:
    """Where does this artist exist across music platforms?"""
    spotify: bool = False
    deezer: bool = False
    deezer_fans: int = 0
    musicbrainz: bool = False
    genius: bool = False
    discogs: bool = False
    setlistfm: bool = False
    bandsintown: bool = False

    def count(self) -> int:
        return sum([
            self.spotify, self.deezer, self.musicbrainz,
            self.genius, self.discogs, self.setlistfm, self.bandsintown,
        ])

    def names(self) -> list[str]:
        """Return list of platform names where artist was found."""
        platforms = []
        if self.spotify:
            platforms.append("Spotify")
        if self.deezer:
            platforms.append(f"Deezer ({self.deezer_fans:,} fans)" if self.deezer_fans else "Deezer")
        if self.musicbrainz:
            platforms.append("MusicBrainz")
        if self.genius:
            platforms.append("Genius")
        if self.discogs:
            platforms.append("Discogs")
        if self.setlistfm:
            platforms.append("Setlist.fm")
        if self.bandsintown:
            platforms.append("Bandsintown")
        return platforms


@dataclass
class ArtistEvaluation:
    """Complete evidence-based evaluation of a single artist."""
    artist_id: str
    artist_name: str
    verdict: Verdict
    confidence: str            # "high", "medium", "low"
    platform_presence: PlatformPresence
    red_flags: list[Evidence] = field(default_factory=list)
    green_flags: list[Evidence] = field(default_factory=list)
    neutral_notes: list[Evidence] = field(default_factory=list)
    decision_path: list[str] = field(default_factory=list)  # Steps the tree took

    # Keep labels/contributors for blocklist builder
    labels: list[str] = field(default_factory=list)
    contributors: list[str] = field(default_factory=list)

    @property
    def red_flag_count(self) -> int:
        return len(self.red_flags)

    @property
    def green_flag_count(self) -> int:
        return len(self.green_flags)

    @property
    def strong_red_flags(self) -> list[Evidence]:
        return [e for e in self.red_flags if e.strength == "strong"]

    @property
    def strong_green_flags(self) -> list[Evidence]:
        return [e for e in self.green_flags if e.strength == "strong"]


# ---------------------------------------------------------------------------
# Evidence collectors — each examines one aspect of the data
# ---------------------------------------------------------------------------

def _collect_platform_evidence(artist: ArtistInfo) -> tuple[PlatformPresence, list[Evidence]]:
    """Determine which platforms the artist exists on."""
    presence = PlatformPresence()
    evidence: list[Evidence] = []

    # Spotify — we always have at least a name
    if not artist.artist_id.startswith("name:"):
        presence.spotify = True

    # Deezer — check if we resolved via Deezer
    if artist.artist_id.startswith("deezer:") or artist.deezer_fans > 0:
        presence.deezer = True
        presence.deezer_fans = artist.deezer_fans

    # Check external URLs for platform hints
    urls = artist.external_urls
    if "deezer" in urls:
        presence.deezer = True

    platforms_found = presence.count()
    if platforms_found >= 3:
        evidence.append(Evidence(
            finding=f"Found on {platforms_found} platforms",
            source="Cross-platform",
            evidence_type="green_flag",
            strength="strong",
            detail=f"Artist exists on: {', '.join(presence.names())}. "
                   "Artists present on multiple platforms are very likely real.",
        ))
    elif platforms_found >= 2:
        evidence.append(Evidence(
            finding=f"Found on {platforms_found} platforms",
            source="Cross-platform",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Found on: {', '.join(presence.names())}.",
        ))
    elif platforms_found <= 1:
        evidence.append(Evidence(
            finding="Only found on 1 platform",
            source="Cross-platform",
            evidence_type="red_flag",
            strength="weak",
            detail="Artist only verified on a single platform. "
                   "Could be new or could be a fabricated artist.",
        ))

    return presence, evidence


def _collect_follower_evidence(artist: ArtistInfo) -> list[Evidence]:
    """Analyze follower/fan counts."""
    evidence: list[Evidence] = []
    fans = artist.deezer_fans or artist.followers

    if fans >= 100_000:
        evidence.append(Evidence(
            finding=f"{fans:,} fans",
            source="Deezer" if artist.deezer_fans else "Spotify",
            evidence_type="green_flag",
            strength="strong",
            detail=f"Artist has {fans:,} fans — substantial organic following.",
        ))
    elif fans >= 10_000:
        evidence.append(Evidence(
            finding=f"{fans:,} fans",
            source="Deezer" if artist.deezer_fans else "Spotify",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Artist has {fans:,} fans — meaningful audience.",
        ))
    elif fans >= 1_000:
        evidence.append(Evidence(
            finding=f"{fans:,} fans",
            source="Deezer" if artist.deezer_fans else "Spotify",
            evidence_type="neutral",
            strength="weak",
            detail=f"Artist has {fans:,} fans — small but plausible audience.",
        ))
    elif fans > 0:
        evidence.append(Evidence(
            finding=f"Only {fans:,} fans",
            source="Deezer" if artist.deezer_fans else "Spotify",
            evidence_type="red_flag",
            strength="weak",
            detail=f"Only {fans:,} fans. Could be a new artist or a ghost artist.",
        ))
    else:
        evidence.append(Evidence(
            finding="No follower/fan data available",
            source="Spotify",
            evidence_type="neutral",
            strength="weak",
            detail="Could not determine fan count from available data.",
        ))

    # Monthly listeners vs followers mismatch (if available)
    if artist.monthly_listeners > 0 and artist.followers > 0:
        ratio = artist.followers / artist.monthly_listeners
        if ratio < 0.005:
            evidence.append(Evidence(
                finding=f"Listeners-to-followers ratio: {ratio:.4f}",
                source="Spotify",
                evidence_type="red_flag",
                strength="strong",
                detail=f"{artist.monthly_listeners:,} monthly listeners but only "
                       f"{artist.followers:,} followers ({ratio:.3%}). Real artists "
                       "typically convert 3-15% of listeners to followers. This "
                       "suggests playlist-driven streams without real fans.",
            ))
        elif ratio < 0.03:
            evidence.append(Evidence(
                finding=f"Low listener-to-follower ratio: {ratio:.3f}",
                source="Spotify",
                evidence_type="red_flag",
                strength="moderate",
                detail=f"{artist.monthly_listeners:,} listeners, {artist.followers:,} followers "
                       f"({ratio:.1%}). On the low end for organic artists.",
            ))

    return evidence


def _collect_catalog_evidence(artist: ArtistInfo) -> list[Evidence]:
    """Analyze catalog composition."""
    evidence: list[Evidence] = []
    albums = artist.album_count
    singles = artist.single_count

    if albums == 0 and singles == 0:
        evidence.append(Evidence(
            finding="Empty catalog",
            source="Deezer" if artist.deezer_fans else "Spotify",
            evidence_type="red_flag",
            strength="moderate",
            detail="No albums or singles found. Could be a very new or fabricated artist.",
        ))
        return evidence

    # Albums are a strong authenticity signal
    if albums >= 3:
        evidence.append(Evidence(
            finding=f"{albums} albums in catalog",
            source="Deezer",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Artist has released {albums} albums. Albums require significant "
                   "creative investment — this is typical of real artists.",
        ))
    elif albums >= 1:
        evidence.append(Evidence(
            finding=f"{albums} album(s) in catalog",
            source="Deezer",
            evidence_type="green_flag",
            strength="weak",
            detail=f"Artist has {albums} album(s). At least some long-form releases.",
        ))

    # Singles-only with high volume → content farm pattern
    if albums == 0 and singles > 20:
        evidence.append(Evidence(
            finding=f"{singles} singles, 0 albums (content farm pattern)",
            source="Deezer",
            evidence_type="red_flag",
            strength="strong",
            detail=f"Artist has released {singles} singles but no albums. "
                   "This pattern is common in PFC/content farm operations that "
                   "mass-produce short tracks for playlist placement.",
        ))
    elif albums == 0 and singles > 10:
        evidence.append(Evidence(
            finding=f"{singles} singles, 0 albums",
            source="Deezer",
            evidence_type="red_flag",
            strength="moderate",
            detail=f"{singles} singles with no albums. Could be a singles-focused "
                   "artist or could indicate content farming.",
        ))

    return evidence


def _collect_duration_evidence(artist: ArtistInfo) -> list[Evidence]:
    """Analyze track durations for stream-farming patterns."""
    evidence: list[Evidence] = []
    durations = artist.track_durations

    if len(durations) < 3:
        return evidence

    avg_ms = statistics.mean(durations)
    stdev_ms = statistics.stdev(durations) if len(durations) > 1 else 0
    avg_s = avg_ms / 1000
    stdev_s = stdev_ms / 1000

    # Very short tracks (stream farming targets just past the 30s payout threshold)
    if avg_s < 90:
        evidence.append(Evidence(
            finding=f"Average track length: {avg_s:.0f} seconds",
            source="Deezer",
            evidence_type="red_flag",
            strength="strong",
            detail=f"Average track is only {avg_s:.0f}s. Stream farms create very short "
                   "tracks (just past the 30-second payout threshold) to maximize "
                   "royalties per stream. Normal songs average 3-4 minutes.",
        ))
    elif avg_s < 120:
        evidence.append(Evidence(
            finding=f"Short average track length: {avg_s:.0f} seconds",
            source="Deezer",
            evidence_type="red_flag",
            strength="moderate",
            detail=f"Average track is {avg_s:.0f}s — shorter than typical songs (180-240s).",
        ))

    # Cookie-cutter uniform durations
    if stdev_s < 10 and len(durations) >= 5:
        evidence.append(Evidence(
            finding=f"Very uniform track lengths (stdev: {stdev_s:.1f}s)",
            source="Deezer",
            evidence_type="red_flag",
            strength="moderate",
            detail=f"Track durations have a standard deviation of only {stdev_s:.1f}s "
                   f"across {len(durations)} tracks. This suggests automated/templated "
                   "production rather than organic songwriting.",
        ))

    # Normal duration range is a mild green flag
    if avg_s >= 180 and stdev_s >= 30:
        evidence.append(Evidence(
            finding=f"Normal track lengths (avg: {avg_s:.0f}s)",
            source="Deezer",
            evidence_type="green_flag",
            strength="weak",
            detail=f"Average track is {avg_s:.0f}s with {stdev_s:.0f}s variation — "
                   "typical of real songs.",
        ))

    return evidence


def _collect_release_evidence(artist: ArtistInfo) -> list[Evidence]:
    """Analyze release cadence."""
    evidence: list[Evidence] = []
    dates = artist.release_dates

    if len(dates) < 2:
        return evidence

    parsed: list[datetime] = []
    for d in dates:
        try:
            if len(d) == 4:
                parsed.append(datetime(int(d), 7, 1, tzinfo=timezone.utc))
            elif len(d) == 7:
                parsed.append(datetime.strptime(d + "-15", "%Y-%m-%d").replace(tzinfo=timezone.utc))
            else:
                parsed.append(datetime.strptime(d[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc))
        except ValueError:
            continue

    if len(parsed) < 2:
        return evidence

    parsed.sort()
    span_days = (parsed[-1] - parsed[0]).days
    if span_days == 0:
        evidence.append(Evidence(
            finding=f"{len(parsed)} releases on the same day",
            source="Deezer",
            evidence_type="red_flag",
            strength="strong",
            detail=f"All {len(parsed)} releases share the same date. "
                   "Legitimate artists space out releases; mass-uploading is a content farm signal.",
        ))
        return evidence

    span_months = max(span_days / 30.0, 1)
    releases_per_month = len(parsed) / span_months

    if releases_per_month > 8:
        evidence.append(Evidence(
            finding=f"{releases_per_month:.1f} releases/month (extreme)",
            source="Deezer",
            evidence_type="red_flag",
            strength="strong",
            detail=f"Releasing {releases_per_month:.1f} times per month over "
                   f"{span_months:.0f} months. Even prolific artists rarely exceed "
                   "2-3 releases/month. This rate suggests automated production.",
        ))
    elif releases_per_month > 4:
        evidence.append(Evidence(
            finding=f"{releases_per_month:.1f} releases/month (high)",
            source="Deezer",
            evidence_type="red_flag",
            strength="moderate",
            detail=f"Releasing {releases_per_month:.1f} times per month — higher "
                   "than most real artists.",
        ))
    elif releases_per_month <= 1 and len(parsed) >= 5:
        evidence.append(Evidence(
            finding=f"Steady release pace ({releases_per_month:.1f}/month over {span_months:.0f} months)",
            source="Deezer",
            evidence_type="green_flag",
            strength="weak",
            detail="Release cadence is consistent with a working musician.",
        ))

    return evidence


def _collect_label_evidence(artist: ArtistInfo) -> list[Evidence]:
    """Check labels against PFC distributor blocklist."""
    evidence: list[Evidence] = []
    if not artist.labels:
        return evidence

    pfc_labels = [l.lower() for l in pfc_distributors()]
    matched_labels = [l for l in artist.labels if l.lower() in pfc_labels]

    if matched_labels:
        evidence.append(Evidence(
            finding=f"Label matches PFC blocklist: {', '.join(matched_labels)}",
            source="Blocklist",
            evidence_type="red_flag",
            strength="strong",
            detail=f"This artist is distributed by {', '.join(matched_labels)}, "
                   "which is associated with Perfect Fit Content (PFC) operations. "
                   "PFC distributors create playlist-optimized content that displaces "
                   "real independent artists.",
        ))
    else:
        # Having a recognizable label is a green flag
        evidence.append(Evidence(
            finding=f"Labels: {', '.join(artist.labels[:3])}",
            source="Deezer",
            evidence_type="neutral",
            strength="weak",
            detail=f"Distributed by: {', '.join(artist.labels)}. "
                   "Not on the PFC blocklist.",
        ))

    return evidence


def _collect_name_evidence(artist: ArtistInfo) -> list[Evidence]:
    """Check artist name against blocklists and suspicious patterns."""
    evidence: list[Evidence] = []
    name = artist.name

    # Known AI artist blocklist match
    known = [n.lower() for n in known_ai_artists()]
    if name.lower() in known:
        evidence.append(Evidence(
            finding="Name matches known AI artist blocklist",
            source="Blocklist",
            evidence_type="red_flag",
            strength="strong",
            detail=f'"{name}" is on our list of known AI-generated artist names.',
        ))
        return evidence

    # Generic two-word pattern
    if re.match(r"^(The\s+)?[A-Z][a-z]+\s+[A-Z][a-z]+s?$", name):
        evidence.append(Evidence(
            finding="Generic two-word artist name",
            source="Name analysis",
            evidence_type="red_flag",
            strength="weak",
            detail=f'"{name}" follows a common pattern for generated artist names '
                   "(Title Case Adjective + Noun). Many real artists also have "
                   "names like this, so this is only a weak signal.",
        ))

    return evidence


def _collect_collaboration_evidence(artist: ArtistInfo) -> list[Evidence]:
    """Analyze collaborators and related artists."""
    evidence: list[Evidence] = []

    # Contributors (featured artists, producers on tracks)
    if len(artist.contributors) >= 3:
        evidence.append(Evidence(
            finding=f"{len(artist.contributors)} collaborators found",
            source="Deezer",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Artist has worked with {len(artist.contributors)} other artists: "
                   f"{', '.join(artist.contributors[:5])}"
                   f"{'...' if len(artist.contributors) > 5 else ''}. "
                   "Real artists collaborate; fake profiles typically don't.",
        ))
    elif len(artist.contributors) >= 1:
        evidence.append(Evidence(
            finding=f"{len(artist.contributors)} collaborator(s)",
            source="Deezer",
            evidence_type="green_flag",
            strength="weak",
            detail=f"Collaborators: {', '.join(artist.contributors)}.",
        ))

    # Related artists on Deezer
    if len(artist.related_artist_names) >= 5:
        evidence.append(Evidence(
            finding=f"{len(artist.related_artist_names)} related artists on Deezer",
            source="Deezer",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Deezer links this artist to: "
                   f"{', '.join(artist.related_artist_names[:5])}. "
                   "Related artist connections develop organically from listener behavior.",
        ))
    elif len(artist.related_artist_names) >= 1:
        evidence.append(Evidence(
            finding=f"{len(artist.related_artist_names)} related artist(s) on Deezer",
            source="Deezer",
            evidence_type="green_flag",
            strength="weak",
            detail=f"Related: {', '.join(artist.related_artist_names[:3])}.",
        ))

    return evidence


def _collect_genre_evidence(artist: ArtistInfo) -> list[Evidence]:
    """Analyze genre data."""
    evidence: list[Evidence] = []

    if not artist.genres:
        evidence.append(Evidence(
            finding="No genres assigned",
            source="Spotify",
            evidence_type="red_flag",
            strength="weak",
            detail="Spotify auto-assigns genres to established artists. "
                   "No genres could mean the artist is too new or not recognized.",
        ))
    elif len(artist.genres) >= 3:
        evidence.append(Evidence(
            finding=f"{len(artist.genres)} genres: {', '.join(artist.genres[:4])}",
            source="Spotify",
            evidence_type="green_flag",
            strength="weak",
            detail="Multiple genre classifications suggest Spotify recognizes this "
                   "as an established artist.",
        ))

    return evidence


def _collect_track_rank_evidence(artist: ArtistInfo) -> list[Evidence]:
    """Analyze Deezer track ranks for popularity signals."""
    evidence: list[Evidence] = []

    if not artist.track_ranks:
        return evidence

    avg_rank = statistics.mean(artist.track_ranks)

    if avg_rank >= 500_000:
        evidence.append(Evidence(
            finding=f"High Deezer track rank (avg: {avg_rank:,.0f})",
            source="Deezer",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Average Deezer rank of {avg_rank:,.0f} across {len(artist.track_ranks)} tracks. "
                   "High ranks indicate significant real listener activity on Deezer.",
        ))
    elif avg_rank >= 100_000:
        evidence.append(Evidence(
            finding=f"Moderate Deezer track rank (avg: {avg_rank:,.0f})",
            source="Deezer",
            evidence_type="green_flag",
            strength="weak",
            detail=f"Average Deezer rank of {avg_rank:,.0f} — some real listener activity.",
        ))

    return evidence


# ---------------------------------------------------------------------------
# Decision tree
# ---------------------------------------------------------------------------

def _decide_verdict(
    red_flags: list[Evidence],
    green_flags: list[Evidence],
    presence: PlatformPresence,
    decision_path: list[str],
) -> tuple[Verdict, str]:
    """Walk the decision tree and return (verdict, confidence)."""

    strong_reds = [e for e in red_flags if e.strength == "strong"]
    moderate_reds = [e for e in red_flags if e.strength == "moderate"]
    strong_greens = [e for e in green_flags if e.strength == "strong"]
    moderate_greens = [e for e in green_flags if e.strength == "moderate"]

    # Rule 1: Known AI artist name → Likely Artificial
    for r in red_flags:
        if "known AI artist" in r.finding.lower() or "blocklist" in r.finding.lower() and r.strength == "strong":
            if r.source == "Blocklist" and "name" in r.finding.lower():
                decision_path.append("Name matches known AI artist blocklist → Likely Artificial")
                return Verdict.LIKELY_ARTIFICIAL, "high"

    # Rule 2: PFC label + content farm patterns → Likely Artificial
    has_pfc_label = any("PFC blocklist" in r.finding for r in red_flags)
    has_farm_pattern = any("content farm" in r.finding.lower() or "stream farm" in r.finding.lower()
                          for r in red_flags)
    if has_pfc_label and has_farm_pattern:
        decision_path.append("PFC distributor + content farm pattern → Likely Artificial")
        return Verdict.LIKELY_ARTIFICIAL, "high"

    # Rule 3: Multiple strong red flags with no green → Likely Artificial
    if len(strong_reds) >= 3 and not strong_greens and not moderate_greens:
        decision_path.append(f"{len(strong_reds)} strong red flags, no green flags → Likely Artificial")
        return Verdict.LIKELY_ARTIFICIAL, "medium"

    # Rule 4: Strong green flags dominate → high confidence authentic
    if len(strong_greens) >= 2 and not strong_reds:
        decision_path.append(f"{len(strong_greens)} strong green flags, no strong red flags → Verified Artist")
        return Verdict.VERIFIED_ARTIST, "high"

    # Rule 5: Good platform presence + fans + no strong red flags → Verified
    if presence.count() >= 2 and presence.deezer_fans >= 50_000 and not strong_reds:
        decision_path.append(f"Multi-platform + {presence.deezer_fans:,} fans, no strong reds → Verified Artist")
        return Verdict.VERIFIED_ARTIST, "high"

    # Rule 6: Moderate green flags dominate
    total_green_strength = len(strong_greens) * 3 + len(moderate_greens) * 2 + len([e for e in green_flags if e.strength == "weak"])
    total_red_strength = len(strong_reds) * 3 + len(moderate_reds) * 2 + len([e for e in red_flags if e.strength == "weak"])

    if total_green_strength >= total_red_strength * 2 and total_green_strength >= 4:
        decision_path.append(f"Green evidence ({total_green_strength}) strongly outweighs red ({total_red_strength}) → Likely Authentic")
        return Verdict.LIKELY_AUTHENTIC, "medium"

    # Rule 7: Red flags dominate
    if total_red_strength >= total_green_strength * 2 and total_red_strength >= 4:
        decision_path.append(f"Red evidence ({total_red_strength}) strongly outweighs green ({total_green_strength}) → Suspicious")
        return Verdict.SUSPICIOUS, "medium"

    # Rule 8: PFC label alone → Suspicious
    if has_pfc_label:
        decision_path.append("PFC distributor match (without other strong signals) → Suspicious")
        return Verdict.SUSPICIOUS, "low"

    # Rule 9: More green than red → Likely Authentic
    if total_green_strength > total_red_strength:
        decision_path.append(f"Green ({total_green_strength}) > Red ({total_red_strength}) → Likely Authentic")
        return Verdict.LIKELY_AUTHENTIC, "low"

    # Rule 10: More red than green → Suspicious
    if total_red_strength > total_green_strength:
        decision_path.append(f"Red ({total_red_strength}) > Green ({total_green_strength}) → Suspicious")
        return Verdict.SUSPICIOUS, "low"

    # Default: Inconclusive
    decision_path.append("Mixed or insufficient evidence → Inconclusive")
    return Verdict.INCONCLUSIVE, "low"


# ---------------------------------------------------------------------------
# Main evaluation entry point
# ---------------------------------------------------------------------------

def evaluate_artist(artist: ArtistInfo) -> ArtistEvaluation:
    """Run the full evidence-based evaluation on a single artist.

    Collects evidence from all available data sources, then walks
    the decision tree to produce a verdict with explanation.
    """
    all_evidence: list[Evidence] = []
    decision_path: list[str] = []

    # Collect evidence from each dimension
    presence, platform_ev = _collect_platform_evidence(artist)
    all_evidence.extend(platform_ev)
    all_evidence.extend(_collect_follower_evidence(artist))
    all_evidence.extend(_collect_catalog_evidence(artist))
    all_evidence.extend(_collect_duration_evidence(artist))
    all_evidence.extend(_collect_release_evidence(artist))
    all_evidence.extend(_collect_label_evidence(artist))
    all_evidence.extend(_collect_name_evidence(artist))
    all_evidence.extend(_collect_collaboration_evidence(artist))
    all_evidence.extend(_collect_genre_evidence(artist))
    all_evidence.extend(_collect_track_rank_evidence(artist))

    # Separate by type
    red_flags = [e for e in all_evidence if e.evidence_type == "red_flag"]
    green_flags = [e for e in all_evidence if e.evidence_type == "green_flag"]
    neutral_notes = [e for e in all_evidence if e.evidence_type == "neutral"]

    # Run decision tree
    verdict, confidence = _decide_verdict(red_flags, green_flags, presence, decision_path)

    return ArtistEvaluation(
        artist_id=artist.artist_id,
        artist_name=artist.name,
        verdict=verdict,
        confidence=confidence,
        platform_presence=presence,
        red_flags=red_flags,
        green_flags=green_flags,
        neutral_notes=neutral_notes,
        decision_path=decision_path,
        labels=artist.labels,
        contributors=artist.contributors,
    )
