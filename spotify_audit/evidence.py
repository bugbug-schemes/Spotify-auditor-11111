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
from spotify_audit.config import pfc_distributors, known_ai_artists, pfc_songwriters

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# External API results container (passed in from CLI)
# ---------------------------------------------------------------------------

@dataclass
class ExternalData:
    """Aggregated results from Standard-tier API lookups.
    Each field is None if the lookup wasn't performed, or holds the result."""
    # Genius
    genius_found: bool = False
    genius_song_count: int = 0
    genius_description: str = ""
    genius_facebook_name: str = ""
    genius_instagram_name: str = ""
    genius_twitter_name: str = ""
    genius_is_verified: bool = False
    genius_followers_count: int = 0
    genius_alternate_names: list[str] = field(default_factory=list)

    # Discogs
    discogs_found: bool = False
    discogs_physical_releases: int = 0
    discogs_digital_releases: int = 0
    discogs_total_releases: int = 0
    discogs_formats: list[str] = field(default_factory=list)
    discogs_labels: list[str] = field(default_factory=list)
    discogs_profile: str = ""          # bio text
    discogs_realname: str = ""
    discogs_social_urls: list[str] = field(default_factory=list)
    discogs_members: list[str] = field(default_factory=list)
    discogs_groups: list[str] = field(default_factory=list)
    discogs_data_quality: str = ""

    # Setlist.fm
    setlistfm_found: bool = False
    setlistfm_total_shows: int = 0
    setlistfm_first_show: str = ""
    setlistfm_last_show: str = ""
    setlistfm_venues: list[str] = field(default_factory=list)
    setlistfm_venue_cities: list[str] = field(default_factory=list)
    setlistfm_venue_countries: list[str] = field(default_factory=list)
    setlistfm_tour_names: list[str] = field(default_factory=list)

    # Bandsintown
    bandsintown_found: bool = False
    bandsintown_past_events: int = 0
    bandsintown_upcoming_events: int = 0
    bandsintown_tracker_count: int = 0
    bandsintown_facebook_url: str = ""
    bandsintown_social_links: list[dict] = field(default_factory=list)
    bandsintown_on_tour: bool = False

    # MusicBrainz
    musicbrainz_found: bool = False
    musicbrainz_type: str = ""       # "Person", "Group", etc.
    musicbrainz_country: str = ""
    musicbrainz_begin_date: str = ""
    musicbrainz_labels: list[str] = field(default_factory=list)
    musicbrainz_urls: dict[str, str] = field(default_factory=dict)  # relation type -> url
    musicbrainz_genres: list[str] = field(default_factory=list)
    musicbrainz_aliases: list[str] = field(default_factory=list)
    musicbrainz_isnis: list[str] = field(default_factory=list)
    musicbrainz_ipis: list[str] = field(default_factory=list)
    musicbrainz_gender: str = ""
    musicbrainz_area: str = ""

    # Last.fm
    lastfm_found: bool = False
    lastfm_listeners: int = 0
    lastfm_playcount: int = 0
    lastfm_listener_play_ratio: float = 0.0
    lastfm_tags: list[str] = field(default_factory=list)
    lastfm_similar_artists: list[str] = field(default_factory=list)
    lastfm_bio_exists: bool = False


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
    lastfm: bool = False

    def count(self) -> int:
        return sum([
            self.spotify, self.deezer, self.musicbrainz,
            self.genius, self.discogs, self.setlistfm,
            self.lastfm,
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
        if self.lastfm:
            platforms.append("Last.fm")
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

    # Keep external data for source status display
    external_data: ExternalData | None = None

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

    @property
    def category_scores(self) -> dict[str, int]:
        """Compute 0-100 scores for 6 signal categories (for radar chart)."""
        return compute_category_scores(self)

    @property
    def sources_reached(self) -> dict[str, bool]:
        """Which API sources were successfully reached."""
        ext = self.external_data or ExternalData()
        return {
            "Spotify": self.platform_presence.spotify,
            "Deezer": self.platform_presence.deezer,
            "Genius": ext.genius_found,
            "Discogs": ext.discogs_found,
            "MusicBrainz": ext.musicbrainz_found,
            "Setlist.fm": ext.setlistfm_found,
            "Last.fm": ext.lastfm_found,
        }


def compute_category_scores(ev: ArtistEvaluation) -> dict[str, int]:
    """Compute 0-100 scores for 6 signal categories.

    Categories:
        Platform Presence: How widely is the artist found across music databases?
        Fan Engagement: Do real people follow and listen to this artist?
        Creative History: Does the artist have a real body of work?
        Live Performance: Has the artist performed live?
        Online Identity: Does the artist have a real-world identity trail?
        Industry Signals: Is the artist registered in professional systems?
    """
    ext = ev.external_data or ExternalData()

    def _clamp(v: float) -> int:
        return max(0, min(100, int(v)))

    # --- Platform Presence (0-100) ---
    # 7 platforms max; each adds ~14 pts
    platform_score = _clamp(ev.platform_presence.count() * 14.3)

    # --- Fan Engagement (0-100) ---
    fans = ev.platform_presence.deezer_fans or 0
    fan_pts = 0
    if fans >= 1_000_000:
        fan_pts = 50
    elif fans >= 100_000:
        fan_pts = 40
    elif fans >= 10_000:
        fan_pts = 25
    elif fans >= 1_000:
        fan_pts = 15
    elif fans > 0:
        fan_pts = 5

    # Last.fm scrobble engagement
    if ext.lastfm_listener_play_ratio >= 10:
        fan_pts += 20
    elif ext.lastfm_listener_play_ratio >= 4:
        fan_pts += 10
    elif ext.lastfm_listeners >= 100:
        fan_pts += 5

    # Genius followers
    if ext.genius_followers_count >= 1_000:
        fan_pts += 20
    elif ext.genius_followers_count >= 100:
        fan_pts += 10

    # Genius song count as proxy for fan interest
    if ext.genius_song_count >= 20:
        fan_pts += 10
    elif ext.genius_song_count >= 5:
        fan_pts += 5

    engagement_score = _clamp(fan_pts)

    # --- Creative History (0-100) ---
    creative_pts = 0

    # Albums
    album_count = len([e for e in ev.green_flags if "album" in e.finding.lower() and e.source == "Deezer"])
    for e in ev.green_flags + ev.red_flags + ev.neutral_notes:
        if "album" in e.finding.lower() and "catalog" not in e.finding.lower():
            pass  # counted via labels below

    # Use evidence directly for catalog signals
    for e in ev.green_flags:
        if "albums in catalog" in e.finding.lower():
            creative_pts += 25
        elif "album(s) in catalog" in e.finding.lower():
            creative_pts += 15
        if "physical release" in e.finding.lower():
            if e.strength == "strong":
                creative_pts += 30
            else:
                creative_pts += 15
        if "songs on Genius" in e.finding:
            if e.strength == "strong":
                creative_pts += 20
            elif e.strength == "moderate":
                creative_pts += 10
            else:
                creative_pts += 5
        if "collaborator" in e.finding.lower():
            creative_pts += 10

    # Penalize content farm patterns
    for e in ev.red_flags:
        if "content farm" in e.finding.lower():
            creative_pts -= 30
        if "empty catalog" in e.finding.lower():
            creative_pts -= 20

    creative_score = _clamp(creative_pts)

    # --- Live Performance (0-100) ---
    live_pts = 0

    # Setlist.fm
    if ext.setlistfm_total_shows >= 50:
        live_pts += 40
    elif ext.setlistfm_total_shows >= 10:
        live_pts += 25
    elif ext.setlistfm_total_shows >= 1:
        live_pts += 10

    # Tour names
    if ext.setlistfm_tour_names:
        live_pts += 15

    # Geographic spread
    countries = len(ext.setlistfm_venue_countries)
    if countries >= 5:
        live_pts += 25
    elif countries >= 2:
        live_pts += 15
    elif countries >= 1:
        live_pts += 5

    live_score = _clamp(live_pts)

    # --- Online Identity (0-100) ---
    identity_pts = 0

    # Social media count
    social_count = 0
    if ext.genius_facebook_name:
        social_count += 1
    if ext.genius_instagram_name:
        social_count += 1
    if ext.genius_twitter_name:
        social_count += 1
    for u in ext.discogs_social_urls:
        social_count += 1
    # MusicBrainz URL rels
    for rel_type, url in ext.musicbrainz_urls.items():
        if any(s in url.lower() for s in ["facebook", "instagram", "twitter", "youtube", "bandcamp"]):
            social_count += 1
    social_count = min(social_count, 8)  # cap duplicates
    identity_pts += social_count * 5

    # Wikipedia
    has_wikipedia = any("wikipedia" in v.lower() for v in ext.musicbrainz_urls.values())
    if has_wikipedia:
        identity_pts += 20

    # Discogs bio
    if len(ext.discogs_profile) >= 200:
        identity_pts += 15
    elif len(ext.discogs_profile) >= 50:
        identity_pts += 8

    # Real name known
    if ext.discogs_realname:
        identity_pts += 10

    # Group members
    if ext.discogs_members:
        identity_pts += 10

    # Genius verified
    if ext.genius_is_verified:
        identity_pts += 15

    identity_score = _clamp(identity_pts)

    # --- Industry Signals (0-100) ---
    industry_pts = 0

    # ISNI
    if ext.musicbrainz_isnis:
        industry_pts += 30

    # IPI
    if ext.musicbrainz_ipis:
        industry_pts += 30

    # MusicBrainz metadata richness
    mb_rich = sum([
        bool(ext.musicbrainz_type),
        bool(ext.musicbrainz_country),
        bool(ext.musicbrainz_begin_date),
        len(ext.musicbrainz_labels) >= 1,
        len(ext.musicbrainz_genres) >= 1,
    ])
    industry_pts += mb_rich * 5

    # Discogs data quality
    if ext.discogs_data_quality == "Correct":
        industry_pts += 10
    elif ext.discogs_data_quality:
        industry_pts += 5

    # PFC label penalty
    for e in ev.red_flags:
        if "PFC blocklist" in e.finding:
            industry_pts -= 40

    industry_score = _clamp(industry_pts)

    return {
        "Platform Presence": platform_score,
        "Fan Engagement": engagement_score,
        "Creative History": creative_score,
        "Live Performance": live_score,
        "Online Identity": identity_score,
        "Industry Signals": industry_score,
    }


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
    """Analyze release cadence, accounting for singles vs albums per month."""
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

    # Separate singles vs albums for proper thresholds
    albums = artist.album_count
    singles = artist.single_count
    total_releases = len(parsed)

    # Calculate per-type rates when we have type breakdown
    if albums + singles > 0:
        albums_per_month = albums / span_months
        singles_per_month = singles / span_months
        releases_per_month = total_releases / span_months

        # Albums: > 2/month is extreme, > 1/month is high
        # Singles: > 6/month is extreme, > 3/month is high
        if albums_per_month > 2:
            evidence.append(Evidence(
                finding=f"{albums_per_month:.1f} albums/month (extreme)",
                source="Deezer",
                evidence_type="red_flag",
                strength="strong",
                detail=f"Releasing {albums_per_month:.1f} albums per month over "
                       f"{span_months:.0f} months ({albums} albums total). "
                       "Albums require significant creative investment — this rate "
                       "suggests automated production.",
            ))
        elif albums_per_month > 1 and albums >= 3:
            evidence.append(Evidence(
                finding=f"{albums_per_month:.1f} albums/month (high)",
                source="Deezer",
                evidence_type="red_flag",
                strength="moderate",
                detail=f"Releasing {albums_per_month:.1f} albums per month — "
                       f"{albums} albums over {span_months:.0f} months is higher "
                       "than most real artists.",
            ))

        if singles_per_month > 6:
            evidence.append(Evidence(
                finding=f"{singles_per_month:.1f} singles/month (extreme)",
                source="Deezer",
                evidence_type="red_flag",
                strength="strong",
                detail=f"Releasing {singles_per_month:.1f} singles per month over "
                       f"{span_months:.0f} months ({singles} singles total). "
                       "Even prolific artists rarely release more than 2-3 singles/month.",
            ))
        elif singles_per_month > 3 and singles >= 5:
            evidence.append(Evidence(
                finding=f"{singles_per_month:.1f} singles/month (high)",
                source="Deezer",
                evidence_type="red_flag",
                strength="moderate",
                detail=f"Releasing {singles_per_month:.1f} singles per month — "
                       f"{singles} singles over {span_months:.0f} months.",
            ))

        # Normal pace with enough history
        if releases_per_month <= 1.5 and total_releases >= 5:
            breakdown = f"{albums} albums + {singles} singles" if albums and singles else f"{total_releases} releases"
            evidence.append(Evidence(
                finding=f"Steady release pace ({releases_per_month:.1f}/month, {breakdown})",
                source="Deezer",
                evidence_type="green_flag",
                strength="weak",
                detail=f"Release cadence of {releases_per_month:.1f}/month over "
                       f"{span_months:.0f} months is consistent with a working musician.",
            ))
    else:
        # Fallback: no type breakdown available
        releases_per_month = total_releases / span_months
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
        elif releases_per_month <= 1 and total_releases >= 5:
            evidence.append(Evidence(
                finding=f"Steady release pace ({releases_per_month:.1f}/month over {span_months:.0f} months)",
                source="Deezer",
                evidence_type="green_flag",
                strength="weak",
                detail="Release cadence is consistent with a working musician.",
            ))

    return evidence


def _collect_label_evidence(artist: ArtistInfo) -> list[Evidence]:
    """Check labels against all blocklists (PFC distributors, known AI, songwriters)."""
    evidence: list[Evidence] = []
    if not artist.labels:
        return evidence

    pfc_labels = [l.lower() for l in pfc_distributors()]
    ai_names = [n.lower() for n in known_ai_artists()]
    pfc_writers = [w.lower() for w in pfc_songwriters()]

    matched_pfc = [l for l in artist.labels if l.lower() in pfc_labels]
    matched_ai = [l for l in artist.labels if l.lower() in ai_names]

    if matched_pfc:
        evidence.append(Evidence(
            finding=f"Label matches PFC blocklist: {', '.join(matched_pfc)}",
            source="Blocklist",
            evidence_type="red_flag",
            strength="strong",
            detail=f"This artist is distributed by {', '.join(matched_pfc)}, "
                   "which is associated with Perfect Fit Content (PFC) operations. "
                   "PFC distributors create playlist-optimized content that displaces "
                   "real independent artists.",
        ))

    if matched_ai:
        evidence.append(Evidence(
            finding=f"Label matches known AI blocklist: {', '.join(matched_ai)}",
            source="Blocklist",
            evidence_type="red_flag",
            strength="strong",
            detail=f"Label {', '.join(matched_ai)} is on the known AI artist/label blocklist.",
        ))

    # Check contributors against PFC songwriter blocklist
    if artist.contributors and pfc_writers:
        matched_writers = [c for c in artist.contributors if c.lower() in pfc_writers]
        if matched_writers:
            evidence.append(Evidence(
                finding=f"Contributor matches PFC songwriter blocklist: {', '.join(matched_writers[:3])}",
                source="Blocklist",
                evidence_type="red_flag",
                strength="strong",
                detail=f"This artist's credits include known PFC songwriters: "
                       f"{', '.join(matched_writers[:5])}. "
                       "PFC songwriters are associated with factory-produced content.",
            ))

    if not matched_pfc and not matched_ai:
        # Having a recognizable label is a green flag
        evidence.append(Evidence(
            finding=f"Labels: {', '.join(artist.labels[:3])}",
            source="Deezer",
            evidence_type="neutral",
            strength="weak",
            detail=f"Distributed by: {', '.join(artist.labels)}. "
                   "Not on any blocklist.",
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

    # Mood-word track titles (PFC tracks use generic mood/atmosphere names)
    mood_words = {
        "calm", "peaceful", "gentle", "soft", "quiet", "serene", "tranquil",
        "dreamy", "hazy", "misty", "ambient", "chill", "cozy", "warm",
        "morning", "evening", "night", "dawn", "dusk", "sunset", "sunrise",
        "rain", "ocean", "forest", "garden", "meadow", "river", "sky",
        "clouds", "breeze", "wind", "snow", "light", "glow", "drift",
        "float", "flow", "sleep", "rest", "relax", "breathe", "solitude",
        "silence", "whisper", "echo", "reflection", "meditation",
    }
    if artist.track_titles:
        mood_count = 0
        for title in artist.track_titles:
            title_words = set(title.lower().split())
            if title_words & mood_words:
                mood_count += 1
        mood_ratio = mood_count / len(artist.track_titles) if artist.track_titles else 0
        if mood_ratio >= 0.7 and len(artist.track_titles) >= 4:
            evidence.append(Evidence(
                finding=f"{mood_ratio:.0%} of track titles use generic mood/atmosphere words",
                source="Name analysis",
                evidence_type="red_flag",
                strength="moderate",
                detail=f"{mood_count} of {len(artist.track_titles)} tracks have names built "
                       "from mood vocabulary (calm, peaceful, rain, morning, etc.). PFC music "
                       "is often named to match playlist moods rather than artistic expression. "
                       f"Sample titles: {', '.join(artist.track_titles[:5])}.",
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


def _collect_credit_network_evidence(artist: ArtistInfo) -> list[Evidence]:
    """Check if track credits match known PFC ghost producers/songwriters."""
    evidence: list[Evidence] = []
    watchlist = {n.lower(): n for n in pfc_songwriters()}
    if not watchlist:
        return evidence

    # Check contributor names against songwriter watchlist
    matched: list[str] = []
    for contributor in artist.contributors:
        clower = contributor.lower()
        if clower in watchlist:
            matched.append(watchlist[clower])

    # Also check contributor_roles keys (may have names not in contributors list)
    for name in artist.contributor_roles:
        nlower = name.lower()
        if nlower in watchlist and watchlist[nlower] not in matched:
            matched.append(watchlist[nlower])

    if matched:
        evidence.append(Evidence(
            finding=f"Credits linked to known PFC songwriter(s): {', '.join(matched)}",
            source="Credit network",
            evidence_type="red_flag",
            strength="strong",
            detail=f"Track credits include {', '.join(matched)}, who "
                   f"{'is' if len(matched) == 1 else 'are'} identified in investigative "
                   "reporting as prolific ghost producers creating music under fabricated "
                   "artist names for PFC placement. This is strong evidence that this "
                   "artist profile may be a pseudonym.",
        ))

    # Check for suspiciously few unique contributors with producer roles
    # (PFC tracks tend to be written by 1-2 people behind many names)
    if artist.contributor_roles:
        producers = [
            name for name, roles in artist.contributor_roles.items()
            if any(r.lower() in ("producer", "composer", "author", "writer")
                   for r in roles)
        ]
        if len(producers) == 1 and len(artist.track_titles) >= 5:
            evidence.append(Evidence(
                finding=f"All tracks credit a single producer: {producers[0]}",
                source="Credit network",
                evidence_type="red_flag",
                strength="weak",
                detail=f"Every track credits '{producers[0]}' as the sole producer/composer. "
                       "While some solo artists self-produce, this pattern is also common "
                       "with ghost producers who write entire catalogs under pseudonyms.",
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

    # Top tracks concentration: if 1-2 tracks hold vast majority of popularity
    if len(artist.track_ranks) >= 4:
        sorted_ranks = sorted(artist.track_ranks, reverse=True)
        total_rank = sum(sorted_ranks)
        if total_rank > 0:
            top2_share = sum(sorted_ranks[:2]) / total_rank
            if top2_share >= 0.90:
                evidence.append(Evidence(
                    finding=f"Top 2 tracks hold {top2_share:.0%} of total rank score",
                    source="Deezer",
                    evidence_type="red_flag",
                    strength="moderate",
                    detail=f"Out of {len(artist.track_ranks)} tracks, the top 2 account for "
                           f"{top2_share:.0%} of all popularity. This concentration pattern "
                           "is consistent with playlist stuffing — a couple of tracks placed "
                           "on playlists while the rest have near-zero organic plays.",
                ))

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
# External API evidence collectors
# ---------------------------------------------------------------------------

def _collect_genius_evidence(ext: ExternalData) -> list[Evidence]:
    """Analyze Genius songwriter/producer credit data."""
    evidence: list[Evidence] = []

    if not ext.genius_found:
        evidence.append(Evidence(
            finding="Not found on Genius",
            source="Genius",
            evidence_type="red_flag",
            strength="moderate",
            detail="Artist has no page on Genius. Real songwriters and performers "
                   "almost always have lyrics/credits on Genius. Ghost and AI artists "
                   "typically have no Genius presence.",
        ))
        return evidence

    # Found on Genius
    if ext.genius_song_count >= 20:
        evidence.append(Evidence(
            finding=f"{ext.genius_song_count} songs on Genius",
            source="Genius",
            evidence_type="green_flag",
            strength="strong",
            detail=f"Artist has {ext.genius_song_count} songs with lyrics/credits on Genius. "
                   "This is strong evidence of a real artist with legitimate songwriting credits.",
        ))
    elif ext.genius_song_count >= 5:
        evidence.append(Evidence(
            finding=f"{ext.genius_song_count} songs on Genius",
            source="Genius",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Artist has {ext.genius_song_count} songs on Genius — real songwriting credits exist.",
        ))
    elif ext.genius_song_count >= 1:
        evidence.append(Evidence(
            finding=f"{ext.genius_song_count} song(s) on Genius",
            source="Genius",
            evidence_type="green_flag",
            strength="weak",
            detail=f"Found {ext.genius_song_count} song(s) on Genius. Minimal but present.",
        ))
    else:
        evidence.append(Evidence(
            finding="Found on Genius but 0 songs",
            source="Genius",
            evidence_type="red_flag",
            strength="moderate",
            detail="Artist has a Genius page but no songs with lyrics or credits. "
                   "This can happen with very new artists or placeholder profiles.",
        ))

    if ext.genius_description:
        evidence.append(Evidence(
            finding="Has Genius artist bio",
            source="Genius",
            evidence_type="green_flag",
            strength="weak",
            detail=f"Genius bio: \"{ext.genius_description[:100]}{'...' if len(ext.genius_description) > 100 else ''}\"",
        ))

    return evidence


def _collect_discogs_evidence(ext: ExternalData) -> list[Evidence]:
    """Analyze Discogs physical release data."""
    evidence: list[Evidence] = []

    if not ext.discogs_found:
        evidence.append(Evidence(
            finding="Not found on Discogs",
            source="Discogs",
            evidence_type="red_flag",
            strength="moderate",
            detail="No Discogs profile found. Discogs catalogs physical music releases "
                   "(vinyl, CD, cassette). Ghost and AI artists virtually never have "
                   "physical releases.",
        ))
        return evidence

    if ext.discogs_total_releases == 0:
        evidence.append(Evidence(
            finding="Found on Discogs but 0 releases",
            source="Discogs",
            evidence_type="red_flag",
            strength="weak",
            detail="Artist exists on Discogs but has no cataloged releases.",
        ))
        return evidence

    # Physical releases are one of the strongest authenticity signals
    if ext.discogs_physical_releases >= 10:
        evidence.append(Evidence(
            finding=f"{ext.discogs_physical_releases} physical releases on Discogs",
            source="Discogs",
            evidence_type="green_flag",
            strength="strong",
            detail=f"Artist has {ext.discogs_physical_releases} physical releases "
                   f"(formats: {', '.join(ext.discogs_formats[:5])}). "
                   "Pressing vinyl or manufacturing CDs requires real investment — "
                   "this is very strong evidence of a legitimate artist.",
        ))
    elif ext.discogs_physical_releases >= 3:
        evidence.append(Evidence(
            finding=f"{ext.discogs_physical_releases} physical releases on Discogs",
            source="Discogs",
            evidence_type="green_flag",
            strength="strong",
            detail=f"Artist has {ext.discogs_physical_releases} physical releases "
                   f"({', '.join(ext.discogs_formats[:5])}). Physical media is strong proof of legitimacy.",
        ))
    elif ext.discogs_physical_releases >= 1:
        evidence.append(Evidence(
            finding=f"{ext.discogs_physical_releases} physical release(s) on Discogs",
            source="Discogs",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"At least {ext.discogs_physical_releases} physical release exists.",
        ))
    elif ext.discogs_digital_releases > 0:
        evidence.append(Evidence(
            finding=f"Discogs: {ext.discogs_digital_releases} digital-only releases, no physical",
            source="Discogs",
            evidence_type="neutral",
            strength="weak",
            detail=f"Found on Discogs with {ext.discogs_digital_releases} digital releases "
                   "but no physical pressings. Not conclusive either way.",
        ))

    # Discogs labels
    if ext.discogs_labels:
        pfc_labels = [l.lower() for l in pfc_distributors()]
        discogs_pfc_matches = [l for l in ext.discogs_labels if l.lower() in pfc_labels]
        if discogs_pfc_matches:
            evidence.append(Evidence(
                finding=f"Discogs labels match PFC blocklist: {', '.join(discogs_pfc_matches)}",
                source="Discogs",
                evidence_type="red_flag",
                strength="strong",
                detail=f"Discogs confirms distribution by PFC-associated label(s): "
                       f"{', '.join(discogs_pfc_matches)}.",
            ))
        elif len(ext.discogs_labels) >= 2:
            evidence.append(Evidence(
                finding=f"Released on {len(ext.discogs_labels)} Discogs labels",
                source="Discogs",
                evidence_type="green_flag",
                strength="weak",
                detail=f"Labels: {', '.join(ext.discogs_labels[:5])}.",
            ))

    return evidence


def _collect_live_show_evidence(ext: ExternalData) -> list[Evidence]:
    """Analyze concert/touring history from Setlist.fm."""
    evidence: list[Evidence] = []
    total_shows = ext.setlistfm_total_shows

    # Setlist.fm
    if ext.setlistfm_found:
        if ext.setlistfm_total_shows >= 50:
            date_range = ""
            if ext.setlistfm_first_show and ext.setlistfm_last_show:
                date_range = f" ({ext.setlistfm_first_show} to {ext.setlistfm_last_show})"
            venues_str = ""
            if ext.setlistfm_venues:
                venues_str = f" Notable venues: {', '.join(ext.setlistfm_venues[:3])}."
            evidence.append(Evidence(
                finding=f"{ext.setlistfm_total_shows} concerts on Setlist.fm",
                source="Setlist.fm",
                evidence_type="green_flag",
                strength="strong",
                detail=f"Setlist.fm records {ext.setlistfm_total_shows} live performances{date_range}."
                       f"{venues_str} "
                       "Extensive concert history is the strongest possible proof of "
                       "a real artist — AI and ghost artists don't perform live.",
            ))
        elif ext.setlistfm_total_shows >= 10:
            evidence.append(Evidence(
                finding=f"{ext.setlistfm_total_shows} concerts on Setlist.fm",
                source="Setlist.fm",
                evidence_type="green_flag",
                strength="strong",
                detail=f"Artist has {ext.setlistfm_total_shows} recorded live performances. "
                       "Concert history is very strong proof of a real artist.",
            ))
        elif ext.setlistfm_total_shows >= 1:
            evidence.append(Evidence(
                finding=f"{ext.setlistfm_total_shows} concert(s) on Setlist.fm",
                source="Setlist.fm",
                evidence_type="green_flag",
                strength="moderate",
                detail=f"At least {ext.setlistfm_total_shows} live performance(s) recorded.",
            ))
        else:
            evidence.append(Evidence(
                finding="Found on Setlist.fm but 0 concerts",
                source="Setlist.fm",
                evidence_type="neutral",
                strength="weak",
                detail="Artist exists on Setlist.fm but no performances are recorded.",
            ))
    else:
        evidence.append(Evidence(
            finding="Not found on Setlist.fm",
            source="Setlist.fm",
            evidence_type="red_flag",
            strength="weak",
            detail="No concert history found on Setlist.fm. Could be a new or "
                   "studio-only artist, or could indicate a non-performing entity.",
        ))

    # Combined live show assessment
    if total_shows == 0 and not ext.setlistfm_found:
        evidence.append(Evidence(
            finding="No live performance history found anywhere",
            source="Live shows",
            evidence_type="red_flag",
            strength="moderate",
            detail="No concerts found on Setlist.fm or Bandsintown. While some real "
                   "artists are studio-only, the absence of any live history is a "
                   "common pattern for ghost and AI-generated artists.",
        ))

    return evidence


def _collect_musicbrainz_evidence(ext: ExternalData) -> list[Evidence]:
    """Analyze MusicBrainz metadata richness."""
    evidence: list[Evidence] = []

    if not ext.musicbrainz_found:
        evidence.append(Evidence(
            finding="Not found on MusicBrainz",
            source="MusicBrainz",
            evidence_type="red_flag",
            strength="weak",
            detail="No MusicBrainz entry found. MusicBrainz is a comprehensive "
                   "open-source music database. Established artists usually have entries.",
        ))
        return evidence

    # Found — assess metadata richness
    richness_parts: list[str] = []
    if ext.musicbrainz_type:
        richness_parts.append(f"type: {ext.musicbrainz_type}")
    if ext.musicbrainz_country:
        richness_parts.append(f"country: {ext.musicbrainz_country}")
    if ext.musicbrainz_begin_date:
        richness_parts.append(f"active since {ext.musicbrainz_begin_date}")

    richness_score = sum([
        bool(ext.musicbrainz_type),
        bool(ext.musicbrainz_country),
        bool(ext.musicbrainz_begin_date),
        len(ext.musicbrainz_labels) >= 1,
    ])

    if richness_score >= 3:
        evidence.append(Evidence(
            finding=f"Rich MusicBrainz profile ({', '.join(richness_parts)})",
            source="MusicBrainz",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"MusicBrainz has detailed metadata: {', '.join(richness_parts)}. "
                   f"Labels: {', '.join(ext.musicbrainz_labels[:3]) if ext.musicbrainz_labels else 'none listed'}. "
                   "Well-documented profiles indicate an established artist.",
        ))
    elif richness_score >= 1:
        evidence.append(Evidence(
            finding=f"MusicBrainz entry ({', '.join(richness_parts) if richness_parts else 'minimal data'})",
            source="MusicBrainz",
            evidence_type="green_flag",
            strength="weak",
            detail=f"Found on MusicBrainz with some metadata: {', '.join(richness_parts)}.",
        ))
    else:
        evidence.append(Evidence(
            finding="Sparse MusicBrainz entry",
            source="MusicBrainz",
            evidence_type="neutral",
            strength="weak",
            detail="Found on MusicBrainz but with minimal metadata. Could be a stub entry.",
        ))

    # MusicBrainz labels vs PFC blocklist
    if ext.musicbrainz_labels:
        pfc_labels = [l.lower() for l in pfc_distributors()]
        mb_pfc_matches = [l for l in ext.musicbrainz_labels if l.lower() in pfc_labels]
        if mb_pfc_matches:
            evidence.append(Evidence(
                finding=f"MusicBrainz labels match PFC blocklist: {', '.join(mb_pfc_matches)}",
                source="MusicBrainz",
                evidence_type="red_flag",
                strength="strong",
                detail=f"MusicBrainz confirms distribution by PFC-associated label(s): "
                       f"{', '.join(mb_pfc_matches)}.",
            ))

    return evidence


def _collect_social_media_evidence(ext: ExternalData) -> list[Evidence]:
    """Analyze social media presence across APIs."""
    evidence: list[Evidence] = []

    # Collect all social links from all sources
    social_links: dict[str, str] = {}  # platform -> source

    # Genius social links
    if ext.genius_facebook_name:
        social_links["Facebook"] = "Genius"
    if ext.genius_instagram_name:
        social_links["Instagram"] = "Genius"
    if ext.genius_twitter_name:
        social_links["Twitter/X"] = "Genius"

    # Discogs social URLs
    for url in ext.discogs_social_urls:
        url_lower = url.lower()
        if "facebook" in url_lower:
            social_links.setdefault("Facebook", "Discogs")
        elif "instagram" in url_lower:
            social_links.setdefault("Instagram", "Discogs")
        elif "twitter" in url_lower or "x.com" in url_lower:
            social_links.setdefault("Twitter/X", "Discogs")
        elif "youtube" in url_lower:
            social_links.setdefault("YouTube", "Discogs")
        elif "bandcamp" in url_lower:
            social_links.setdefault("Bandcamp", "Discogs")
        elif "soundcloud" in url_lower:
            social_links.setdefault("SoundCloud", "Discogs")

    # MusicBrainz URL relations
    for rel_type, url in ext.musicbrainz_urls.items():
        url_lower = url.lower()
        rel_lower = rel_type.lower()
        if "official homepage" in rel_lower or "official site" in rel_lower:
            social_links.setdefault("Official Website", "MusicBrainz")
        elif "wikipedia" in rel_lower or "wikipedia" in url_lower:
            social_links.setdefault("Wikipedia", "MusicBrainz")
        elif "wikidata" in rel_lower or "wikidata" in url_lower:
            social_links.setdefault("Wikidata", "MusicBrainz")
        elif "youtube" in url_lower:
            social_links.setdefault("YouTube", "MusicBrainz")
        elif "bandcamp" in url_lower:
            social_links.setdefault("Bandcamp", "MusicBrainz")
        elif "soundcloud" in url_lower:
            social_links.setdefault("SoundCloud", "MusicBrainz")
        elif "facebook" in url_lower:
            social_links.setdefault("Facebook", "MusicBrainz")
        elif "instagram" in url_lower:
            social_links.setdefault("Instagram", "MusicBrainz")
        elif "twitter" in url_lower or "x.com" in url_lower:
            social_links.setdefault("Twitter/X", "MusicBrainz")

    if len(social_links) >= 4:
        platforms_str = ", ".join(f"{k} (via {v})" for k, v in sorted(social_links.items()))
        evidence.append(Evidence(
            finding=f"{len(social_links)} social/web presences found",
            source="Social media",
            evidence_type="green_flag",
            strength="strong",
            detail=f"Artist has verified presence on: {platforms_str}. "
                   "A broad web footprint is strong evidence of a real artist.",
        ))
    elif len(social_links) >= 2:
        platforms_str = ", ".join(f"{k} (via {v})" for k, v in sorted(social_links.items()))
        evidence.append(Evidence(
            finding=f"{len(social_links)} social/web presences found",
            source="Social media",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Found: {platforms_str}.",
        ))
    elif len(social_links) == 1:
        platforms_str = ", ".join(f"{k} (via {v})" for k, v in social_links.items())
        evidence.append(Evidence(
            finding=f"1 social/web presence: {platforms_str}",
            source="Social media",
            evidence_type="green_flag",
            strength="weak",
            detail=f"Minimal web presence found: {platforms_str}.",
        ))
    else:
        # Only flag if we actually checked multiple APIs
        apis_checked = sum([
            ext.genius_found,
            ext.discogs_found,
            ext.musicbrainz_found,
        ])
        if apis_checked >= 2:
            evidence.append(Evidence(
                finding="No social media or website links found",
                source="Social media",
                evidence_type="red_flag",
                strength="moderate",
                detail="Checked Genius, Discogs, and MusicBrainz — "
                       "no social media profiles or official website found. "
                       "Real artists almost always have some web presence.",
            ))

    # Genius verified status
    if ext.genius_is_verified:
        evidence.append(Evidence(
            finding="Verified on Genius",
            source="Genius",
            evidence_type="green_flag",
            strength="moderate",
            detail="Artist has a verified Genius account, indicating they have claimed "
                   "their profile and likely manage their own credits/lyrics.",
        ))

    # Genius followers
    if ext.genius_followers_count >= 1000:
        evidence.append(Evidence(
            finding=f"{ext.genius_followers_count:,} Genius followers",
            source="Genius",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Artist has {ext.genius_followers_count:,} followers on Genius, "
                   "indicating engaged fans who follow lyrics/credits.",
        ))
    elif ext.genius_followers_count >= 100:
        evidence.append(Evidence(
            finding=f"{ext.genius_followers_count:,} Genius followers",
            source="Genius",
            evidence_type="green_flag",
            strength="weak",
            detail=f"Artist has {ext.genius_followers_count:,} Genius followers.",
        ))

    # Wikipedia/Wikidata presence (from MusicBrainz URL rels)
    has_wikipedia = any("wikipedia" in k.lower() or "wikipedia" in v.lower()
                        for k, v in ext.musicbrainz_urls.items())
    if has_wikipedia:
        evidence.append(Evidence(
            finding="Has Wikipedia article",
            source="MusicBrainz",
            evidence_type="green_flag",
            strength="strong",
            detail="Artist has a Wikipedia article linked from MusicBrainz. "
                   "Wikipedia's notability requirements make this strong proof of legitimacy.",
        ))

    return evidence


def _collect_identity_evidence(ext: ExternalData) -> list[Evidence]:
    """Analyze bio, real name, group membership, and identity signals."""
    evidence: list[Evidence] = []

    # Discogs bio/profile — analyze content, not just length
    if ext.discogs_profile:
        bio = ext.discogs_profile
        bio_len = len(bio)
        bio_lower = bio.lower()

        # Look for substantive career indicators in the bio
        career_keywords = [
            "born", "grew up", "formed in", "founded", "member of",
            "Grammy", "award", "toured", "festival", "performed at",
            "signed to", "record deal", "debut album", "released",
            "collaborated with", "produced by", "studied",
            "conservatory", "university", "trained",
        ]
        career_hits = [kw for kw in career_keywords if kw.lower() in bio_lower]

        # Detect year mentions (suggests real career timeline)
        year_pattern = re.findall(r"\b(19[5-9]\d|20[0-2]\d)\b", bio)

        if bio_len >= 200 and (len(career_hits) >= 3 or len(year_pattern) >= 2):
            evidence.append(Evidence(
                finding=f"Detailed Discogs bio with career history ({bio_len} chars)",
                source="Discogs",
                evidence_type="green_flag",
                strength="strong",
                detail=f"Bio contains career indicators ({', '.join(career_hits[:4])}) "
                       f"and spans {len(year_pattern)} year reference(s). "
                       f"Excerpt: \"{bio[:200]}{'...' if bio_len > 200 else ''}\"",
            ))
        elif bio_len >= 200:
            evidence.append(Evidence(
                finding=f"Discogs bio ({bio_len} chars)",
                source="Discogs",
                evidence_type="green_flag",
                strength="moderate",
                detail=f"Has a detailed Discogs biography: "
                       f"\"{bio[:150]}{'...' if bio_len > 150 else ''}\"",
            ))
        elif bio_len >= 50 and career_hits:
            evidence.append(Evidence(
                finding=f"Discogs bio with career details ({bio_len} chars)",
                source="Discogs",
                evidence_type="green_flag",
                strength="moderate",
                detail=f"Bio mentions: {', '.join(career_hits[:3])}. "
                       f"\"{bio[:120]}{'...' if bio_len > 120 else ''}\"",
            ))
        elif bio_len >= 50:
            evidence.append(Evidence(
                finding=f"Discogs bio ({bio_len} chars)",
                source="Discogs",
                evidence_type="green_flag",
                strength="weak",
                detail=f"Has a brief Discogs biography: \"{bio[:100]}\"",
            ))

    # Real name (Discogs)
    if ext.discogs_realname:
        evidence.append(Evidence(
            finding=f"Real name known: {ext.discogs_realname}",
            source="Discogs",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Discogs records this artist's real name as \"{ext.discogs_realname}\". "
                   "Known real names indicate a documented, real person.",
        ))

    # Group members (Discogs)
    if ext.discogs_members:
        evidence.append(Evidence(
            finding=f"Group with {len(ext.discogs_members)} known members",
            source="Discogs",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Discogs lists group members: {', '.join(ext.discogs_members[:5])}"
                   f"{'...' if len(ext.discogs_members) > 5 else ''}. "
                   "Known members indicate a real group.",
        ))

    # Groups this artist belongs to (Discogs)
    if ext.discogs_groups:
        evidence.append(Evidence(
            finding=f"Member of {len(ext.discogs_groups)} group(s)",
            source="Discogs",
            evidence_type="green_flag",
            strength="weak",
            detail=f"Also part of: {', '.join(ext.discogs_groups[:5])}.",
        ))

    # Discogs data quality
    if ext.discogs_data_quality == "Correct":
        evidence.append(Evidence(
            finding="Discogs data quality: Correct",
            source="Discogs",
            evidence_type="green_flag",
            strength="weak",
            detail="Community-verified Discogs data rated as 'Correct'.",
        ))

    # MusicBrainz professional identifiers (ISNIs/IPIs)
    if ext.musicbrainz_isnis:
        evidence.append(Evidence(
            finding=f"Has ISNI identifier ({ext.musicbrainz_isnis[0]})",
            source="MusicBrainz",
            evidence_type="green_flag",
            strength="strong",
            detail="Artist has an International Standard Name Identifier (ISNI), "
                   "a globally unique identifier assigned to public identities. "
                   "This is very strong proof of a real, professionally registered artist.",
        ))

    if ext.musicbrainz_ipis:
        evidence.append(Evidence(
            finding=f"Has IPI code ({ext.musicbrainz_ipis[0]})",
            source="MusicBrainz",
            evidence_type="green_flag",
            strength="strong",
            detail="Artist has an Interested Parties Information (IPI) code, "
                   "assigned by collecting societies for royalty management. "
                   "This means they are registered as a rights holder.",
        ))

    # MusicBrainz gender (helps confirm type=Person)
    if ext.musicbrainz_gender:
        evidence.append(Evidence(
            finding=f"MusicBrainz gender: {ext.musicbrainz_gender}",
            source="MusicBrainz",
            evidence_type="neutral",
            strength="weak",
            detail=f"MusicBrainz records this artist's gender as {ext.musicbrainz_gender}.",
        ))

    # MusicBrainz genres
    if ext.musicbrainz_genres:
        evidence.append(Evidence(
            finding=f"MusicBrainz genres: {', '.join(ext.musicbrainz_genres[:5])}",
            source="MusicBrainz",
            evidence_type="green_flag",
            strength="weak",
            detail=f"Community-tagged genres: {', '.join(ext.musicbrainz_genres[:8])}. "
                   "Genre tags indicate community recognition.",
        ))

    # Alternate names (Genius + MusicBrainz aliases)
    all_aliases = list(set(ext.genius_alternate_names + ext.musicbrainz_aliases))
    if len(all_aliases) >= 3:
        evidence.append(Evidence(
            finding=f"{len(all_aliases)} alternate names/aliases",
            source="Multiple",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Known aliases: {', '.join(all_aliases[:6])}. "
                   "Multiple aliases suggest a real artist with an established history.",
        ))
    elif all_aliases:
        evidence.append(Evidence(
            finding=f"Alias(es): {', '.join(all_aliases[:3])}",
            source="Multiple",
            evidence_type="green_flag",
            strength="weak",
            detail=f"Known as: {', '.join(all_aliases[:5])}.",
        ))

    return evidence


def _collect_lastfm_evidence(ext: ExternalData) -> list[Evidence]:
    """Analyze Last.fm data — listener/playcount ratio is a top fraud signal."""
    evidence: list[Evidence] = []

    if not ext.lastfm_found:
        evidence.append(Evidence(
            finding="Not found on Last.fm",
            source="Last.fm",
            evidence_type="red_flag",
            strength="moderate",
            detail="Artist has no Last.fm presence. Real artists with significant "
                   "Spotify streams almost always have Last.fm scrobble data. "
                   "Ghost artists typically have zero Last.fm activity.",
        ))
        return evidence

    evidence.append(Evidence(
        finding=f"Found on Last.fm ({ext.lastfm_listeners:,} listeners, "
                f"{ext.lastfm_playcount:,} scrobbles)",
        source="Last.fm",
        evidence_type="green_flag",
        strength="moderate",
        detail=f"Artist has {ext.lastfm_listeners:,} unique listeners and "
               f"{ext.lastfm_playcount:,} total scrobbles on Last.fm.",
    ))

    # Listener-to-playcount ratio analysis
    # Real artists: ratio typically 5-50+ (fans listen repeatedly)
    # Ghost artists: ratio near 1-3 (no real fans, incidental scrobbles)
    ratio = ext.lastfm_listener_play_ratio
    if ratio > 0:
        if ratio >= 10:
            evidence.append(Evidence(
                finding=f"Strong scrobble engagement (play/listener ratio: {ratio:.1f})",
                source="Last.fm",
                evidence_type="green_flag",
                strength="strong",
                detail=f"Each listener averages {ratio:.1f} plays. High replay value "
                       "indicates genuine fans who return to this artist's music.",
            ))
        elif ext.lastfm_listeners >= 100 and ratio < 2:
            evidence.append(Evidence(
                finding=f"Very low scrobble engagement (play/listener ratio: {ratio:.1f})",
                source="Last.fm",
                evidence_type="red_flag",
                strength="moderate",
                detail=f"Despite {ext.lastfm_listeners:,} listeners, each averages only "
                       f"{ratio:.1f} plays. This suggests passive/algorithmic listening "
                       "rather than genuine fans — a common pattern with PFC content.",
            ))

    # Low listener count vs Spotify presence
    if ext.lastfm_listeners > 0 and ext.lastfm_listeners < 50:
        evidence.append(Evidence(
            finding=f"Negligible Last.fm presence ({ext.lastfm_listeners} listeners)",
            source="Last.fm",
            evidence_type="red_flag",
            strength="weak",
            detail="Extremely low Last.fm listener count suggests minimal organic "
                   "fanbase outside of Spotify algorithmic playlists.",
        ))

    # Bio on Last.fm
    if ext.lastfm_bio_exists:
        evidence.append(Evidence(
            finding="Has Last.fm biography",
            source="Last.fm",
            evidence_type="green_flag",
            strength="weak",
            detail="Artist has a bio on Last.fm, typically contributed by users.",
        ))

    return evidence


def _collect_touring_geography_evidence(ext: ExternalData) -> list[Evidence]:
    """Analyze geographic spread of touring (from Setlist.fm)."""
    evidence: list[Evidence] = []

    if not ext.setlistfm_found:
        return evidence

    # Tour names indicate organized, named tours
    if ext.setlistfm_tour_names:
        evidence.append(Evidence(
            finding=f"{len(ext.setlistfm_tour_names)} named tour(s)",
            source="Setlist.fm",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Named tours: {', '.join(ext.setlistfm_tour_names[:5])}. "
                   "Named tours indicate professional touring activity.",
        ))

    # Geographic spread
    countries = ext.setlistfm_venue_countries
    cities = ext.setlistfm_venue_cities
    if len(countries) >= 5:
        evidence.append(Evidence(
            finding=f"Performed in {len(countries)} countries",
            source="Setlist.fm",
            evidence_type="green_flag",
            strength="strong",
            detail=f"International touring across: {', '.join(countries[:8])}. "
                   "International touring is very strong proof of a real artist.",
        ))
    elif len(countries) >= 2:
        evidence.append(Evidence(
            finding=f"Performed in {len(countries)} countries",
            source="Setlist.fm",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Toured in: {', '.join(countries[:5])}.",
        ))
    elif len(cities) >= 3:
        evidence.append(Evidence(
            finding=f"Performed in {len(cities)} cities",
            source="Setlist.fm",
            evidence_type="green_flag",
            strength="weak",
            detail=f"Venues in: {', '.join(cities[:5])}.",
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
# Entity database intelligence collector
# ---------------------------------------------------------------------------

def _collect_entity_db_evidence(
    artist: ArtistInfo,
    entity_db: "EntityDB",
) -> list[Evidence]:
    """Check the entity intelligence database for prior intelligence.

    Looks up:
    - Artist itself (previously flagged?)
    - Labels (any confirmed_bad or suspected?)
    - Contributors/songwriters (any confirmed_bad or suspected?)
    - Cowriter network (connected to other bad artists?)
    """
    evidence: list[Evidence] = []

    # 1. Check if this artist is already flagged
    db_artist = entity_db.get_artist(artist.name)
    if db_artist:
        status = db_artist.get("threat_status", "unknown")
        if status == "confirmed_bad":
            evidence.append(Evidence(
                finding="Artist previously confirmed as bad in entity database",
                source="Entity DB",
                evidence_type="red_flag",
                strength="strong",
                detail=f"'{artist.name}' was previously flagged as confirmed_bad. "
                       f"Notes: {db_artist.get('notes', 'none')}",
            ))
        elif status == "suspected":
            evidence.append(Evidence(
                finding="Artist previously flagged as suspected in entity database",
                source="Entity DB",
                evidence_type="red_flag",
                strength="moderate",
                detail=f"'{artist.name}' was flagged as suspected in a prior scan. "
                       f"Notes: {db_artist.get('notes', 'none')}",
            ))
        elif status == "cleared":
            evidence.append(Evidence(
                finding="Artist previously cleared in entity database",
                source="Entity DB",
                evidence_type="green_flag",
                strength="moderate",
                detail=f"'{artist.name}' was manually cleared as legitimate. "
                       f"Notes: {db_artist.get('notes', 'none')}",
            ))

    # 2. Check labels against entity DB
    bad_labels: list[str] = []
    suspected_labels: list[str] = []
    for label_name in artist.labels:
        db_label = entity_db.get_label(label_name)
        if db_label:
            label_status = db_label.get("threat_status", "unknown")
            label_artists = db_label.get("artist_count", 0)
            if label_status == "confirmed_bad":
                bad_labels.append(f"{label_name} ({label_artists} artists)")
            elif label_status == "suspected":
                suspected_labels.append(f"{label_name} ({label_artists} artists)")

    if bad_labels:
        evidence.append(Evidence(
            finding=f"Label(s) flagged as confirmed bad: {', '.join(bad_labels)}",
            source="Entity DB",
            evidence_type="red_flag",
            strength="strong",
            detail=f"Artist releases through label(s) that are confirmed bad actors "
                   f"in the entity intelligence database.",
        ))
    if suspected_labels:
        evidence.append(Evidence(
            finding=f"Label(s) flagged as suspected: {', '.join(suspected_labels)}",
            source="Entity DB",
            evidence_type="red_flag",
            strength="moderate",
            detail=f"Artist releases through label(s) that are suspected in the "
                   f"entity intelligence database.",
        ))

    # 3. Check contributors against entity DB
    bad_writers: list[str] = []
    suspected_writers: list[str] = []
    for contrib in artist.contributors:
        db_sw = entity_db.get_songwriter(contrib)
        if db_sw:
            sw_status = db_sw.get("threat_status", "unknown")
            sw_artists = db_sw.get("artist_count", 0)
            if sw_status == "confirmed_bad":
                bad_writers.append(f"{contrib} ({sw_artists} artists)")
            elif sw_status == "suspected":
                suspected_writers.append(f"{contrib} ({sw_artists} artists)")

    if bad_writers:
        evidence.append(Evidence(
            finding=f"Credits include confirmed bad songwriter(s): {', '.join(bad_writers)}",
            source="Entity DB",
            evidence_type="red_flag",
            strength="strong",
            detail=f"Track credits include songwriter(s)/producer(s) confirmed as bad "
                   f"actors in the entity intelligence database.",
        ))
    if suspected_writers:
        evidence.append(Evidence(
            finding=f"Credits include suspected songwriter(s): {', '.join(suspected_writers)}",
            source="Entity DB",
            evidence_type="red_flag",
            strength="moderate",
            detail=f"Track credits include songwriter(s)/producer(s) flagged as "
                   f"suspected in the entity intelligence database.",
        ))

    # 4. Check cowriter network — is this artist connected to known bad actors?
    if db_artist:
        cowriter_net = entity_db.get_cowriter_network(db_artist["id"])
        bad_connections = [
            cw for cw in cowriter_net
            if cw.get("threat_status") in ("confirmed_bad", "suspected")
        ]
        if len(bad_connections) >= 3:
            names = [cw["name"] for cw in bad_connections[:5]]
            evidence.append(Evidence(
                finding=f"Connected to {len(bad_connections)} flagged artists via shared producers",
                source="Entity DB",
                evidence_type="red_flag",
                strength="strong" if len(bad_connections) >= 5 else "moderate",
                detail=f"Shared songwriter/producer connections link this artist to: "
                       f"{', '.join(names)}"
                       f"{f' and {len(bad_connections) - 5} more' if len(bad_connections) > 5 else ''}. "
                       f"This network pattern is common in PFC operations.",
            ))
        elif len(bad_connections) >= 1:
            names = [cw["name"] for cw in bad_connections]
            evidence.append(Evidence(
                finding=f"Connected to {len(bad_connections)} flagged artist(s) via shared producers",
                source="Entity DB",
                evidence_type="red_flag",
                strength="weak",
                detail=f"Shared songwriter/producer connections to: {', '.join(names)}.",
            ))

    return evidence


# ---------------------------------------------------------------------------
# Main evaluation entry point
# ---------------------------------------------------------------------------

def evaluate_artist(
    artist: ArtistInfo,
    external: ExternalData | None = None,
    entity_db: "EntityDB | None" = None,
) -> ArtistEvaluation:
    """Run the full evidence-based evaluation on a single artist.

    Collects evidence from all available data sources, then walks
    the decision tree to produce a verdict with explanation.

    Args:
        artist: Core artist data (from Deezer/Spotify)
        external: Optional results from Standard-tier API lookups
                  (Genius, Discogs, Setlist.fm, Bandsintown, MusicBrainz)
        entity_db: Optional entity intelligence database for prior knowledge
    """
    ext = external or ExternalData()
    all_evidence: list[Evidence] = []
    decision_path: list[str] = []

    # Collect evidence from core data (Deezer/Spotify)
    presence, platform_ev = _collect_platform_evidence(artist)

    # Update platform presence with external API results
    if ext.genius_found:
        presence.genius = True
    if ext.discogs_found:
        presence.discogs = True
    if ext.setlistfm_found:
        presence.setlistfm = True
    if ext.musicbrainz_found:
        presence.musicbrainz = True
    if ext.lastfm_found:
        presence.lastfm = True

    # Re-generate platform evidence with updated counts
    platforms_found = presence.count()
    platform_ev = []  # clear and rebuild
    if platforms_found >= 5:
        platform_ev.append(Evidence(
            finding=f"Found on {platforms_found} platforms",
            source="Cross-platform",
            evidence_type="green_flag",
            strength="strong",
            detail=f"Artist verified on: {', '.join(presence.names())}. "
                   "Broad cross-platform presence is very strong proof of a real artist.",
        ))
    elif platforms_found >= 3:
        platform_ev.append(Evidence(
            finding=f"Found on {platforms_found} platforms",
            source="Cross-platform",
            evidence_type="green_flag",
            strength="strong",
            detail=f"Artist exists on: {', '.join(presence.names())}. "
                   "Artists present on multiple platforms are very likely real.",
        ))
    elif platforms_found >= 2:
        platform_ev.append(Evidence(
            finding=f"Found on {platforms_found} platforms",
            source="Cross-platform",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Found on: {', '.join(presence.names())}.",
        ))
    elif platforms_found <= 1:
        platform_ev.append(Evidence(
            finding="Only found on 1 platform",
            source="Cross-platform",
            evidence_type="red_flag",
            strength="weak",
            detail="Artist only verified on a single platform. "
                   "Could be new or could be a fabricated artist.",
        ))

    all_evidence.extend(platform_ev)
    all_evidence.extend(_collect_follower_evidence(artist))
    all_evidence.extend(_collect_catalog_evidence(artist))
    all_evidence.extend(_collect_duration_evidence(artist))
    all_evidence.extend(_collect_release_evidence(artist))
    all_evidence.extend(_collect_label_evidence(artist))
    all_evidence.extend(_collect_name_evidence(artist))
    all_evidence.extend(_collect_collaboration_evidence(artist))
    all_evidence.extend(_collect_credit_network_evidence(artist))
    all_evidence.extend(_collect_genre_evidence(artist))
    all_evidence.extend(_collect_track_rank_evidence(artist))

    # Collect evidence from external APIs (Standard tier)
    all_evidence.extend(_collect_genius_evidence(ext))
    all_evidence.extend(_collect_discogs_evidence(ext))
    all_evidence.extend(_collect_live_show_evidence(ext))
    all_evidence.extend(_collect_musicbrainz_evidence(ext))
    all_evidence.extend(_collect_social_media_evidence(ext))
    all_evidence.extend(_collect_identity_evidence(ext))
    all_evidence.extend(_collect_lastfm_evidence(ext))
    all_evidence.extend(_collect_touring_geography_evidence(ext))

    # Entity intelligence database (accumulated from prior scans)
    if entity_db:
        all_evidence.extend(_collect_entity_db_evidence(artist, entity_db))

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
        external_data=ext,
    )


def incorporate_deep_evidence(
    evaluation: ArtistEvaluation,
    deep_evidence: list[Evidence],
) -> ArtistEvaluation:
    """Add Deep-tier evidence to an existing evaluation and re-run the verdict.

    This lets us append Claude bio/image analysis results after the initial
    Standard evaluation without re-running all the collectors.
    """
    if not deep_evidence:
        return evaluation

    # Merge all evidence
    all_evidence = (
        evaluation.red_flags
        + evaluation.green_flags
        + evaluation.neutral_notes
        + deep_evidence
    )

    # Re-separate by type
    red_flags = [e for e in all_evidence if e.evidence_type == "red_flag"]
    green_flags = [e for e in all_evidence if e.evidence_type == "green_flag"]
    neutral_notes = [e for e in all_evidence if e.evidence_type == "neutral"]

    # Re-run decision tree with expanded evidence
    decision_path: list[str] = ["Re-evaluated with Deep tier (Claude) evidence"]
    verdict, confidence = _decide_verdict(
        red_flags, green_flags, evaluation.platform_presence, decision_path,
    )

    return ArtistEvaluation(
        artist_id=evaluation.artist_id,
        artist_name=evaluation.artist_name,
        verdict=verdict,
        confidence=confidence,
        platform_presence=evaluation.platform_presence,
        red_flags=red_flags,
        green_flags=green_flags,
        neutral_notes=neutral_notes,
        decision_path=decision_path,
        labels=evaluation.labels,
        contributors=evaluation.contributors,
        external_data=evaluation.external_data,
    )
