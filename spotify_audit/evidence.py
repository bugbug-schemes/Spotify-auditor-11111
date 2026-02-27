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
from spotify_audit.config import pfc_distributors, known_ai_artists, pfc_songwriters, MOOD_WORDS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Multi-artist credit splitting (Fix 3)
# ---------------------------------------------------------------------------

# Ordered longest-first to avoid partial matches (e.g. " feat " before " ft ")
_ARTIST_SEPARATORS = [
    " feat. ", " feat ", " ft. ", " ft ",
    ", ", " & ", " and ", " x ", " vs. ", " vs ",
]


def extract_primary_artist(credit: str) -> str:
    """Extract the primary artist name from a combined credit string.

    Handles separators like ", ", " & ", " feat. ", " ft. ", " x ", etc.
    Returns the first (primary) artist name.

    Examples:
        "Roger Eno, Brian Eno" → "Roger Eno"
        "Max Richter, Grace Davidson" → "Max Richter"
        "Kendrick Lamar feat. SZA" → "Kendrick Lamar"
        "A Winged Victory for the Sullen" → "A Winged Victory for the Sullen"
    """
    credit_lower = credit.lower()
    for sep in _ARTIST_SEPARATORS:
        if sep in credit_lower:
            # Find the separator position case-insensitively
            idx = credit_lower.index(sep)
            return credit[:idx].strip()
    return credit.strip()


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
    musicbrainz_relationship_count: int = 0  # number of URL/artist/recording relations

    # Last.fm
    lastfm_found: bool = False
    lastfm_listeners: int = 0
    lastfm_playcount: int = 0
    lastfm_listener_play_ratio: float = 0.0
    lastfm_tags: list[str] = field(default_factory=list)
    lastfm_similar_artists: list[str] = field(default_factory=list)
    lastfm_bio_exists: bool = False

    # Wikipedia (direct lookup, independent of MusicBrainz)
    wikipedia_found: bool = False
    wikipedia_title: str = ""
    wikipedia_length: int = 0            # article byte length
    wikipedia_extract: str = ""          # intro summary
    wikipedia_description: str = ""      # Wikidata short description
    wikipedia_categories: list[str] = field(default_factory=list)
    wikipedia_monthly_views: int = 0     # average monthly page views
    wikipedia_url: str = ""

    # Songkick (concert/touring history)
    songkick_found: bool = False
    songkick_on_tour: bool = False
    songkick_total_past_events: int = 0
    songkick_total_upcoming_events: int = 0
    songkick_first_event_date: str = ""
    songkick_last_event_date: str = ""
    songkick_venue_names: list[str] = field(default_factory=list)
    songkick_venue_cities: list[str] = field(default_factory=list)
    songkick_venue_countries: list[str] = field(default_factory=list)
    songkick_event_types: list[str] = field(default_factory=list)

    # Deezer AI detection (Priority 2 — conditional enrichment)
    deezer_ai_checked: bool = False
    deezer_ai_tagged_albums: list[str] = field(default_factory=list)

    # YouTube (Priority 4 — conditional enrichment)
    youtube_checked: bool = False
    youtube_channel_found: bool = False
    youtube_subscriber_count: int = 0
    youtube_video_count: int = 0
    youtube_view_count: int = 0
    youtube_music_videos_found: int = 0
    youtube_match_confidence: float = 0.0

    # PRO Registry (Priority 3 — conditional enrichment)
    pro_checked: bool = False
    pro_found_bmi: bool = False
    pro_found_ascap: bool = False
    pro_works_count: int = 0           # combined BMI + ASCAP
    pro_publishers: list[str] = field(default_factory=list)
    pro_songwriter_registered: bool = False
    pro_pfc_publisher_match: bool = False
    pro_zero_songwriter_share: bool = False
    pro_songwriter_share_pct: float = -1.0   # -1 = unknown, 0-100 = actual share
    pro_publisher_share_pct: float = -1.0    # -1 = unknown, 0-100 = actual share

    # ISRC data (Priority 7 — from Deezer + MusicBrainz)
    isrcs: list[str] = field(default_factory=list)
    isrc_registrants: list[str] = field(default_factory=list)

    # MusicBrainz enhanced URLs (Priority 5)
    musicbrainz_youtube_url: str = ""
    musicbrainz_bandcamp_url: str = ""
    musicbrainz_official_website: str = ""
    musicbrainz_social_urls: dict[str, str] = field(default_factory=dict)

    # Press coverage (Priority 6)
    press_checked: bool = False
    press_publications_found: list[str] = field(default_factory=list)
    press_total_hits: int = 0

    # Pre-seeded evidence from known entity pre-check (Priority 1)
    pre_seeded_evidence: list[dict] = field(default_factory=list)

    # Match quality metadata per platform (from name_matching)
    # confidence: 0.0 = searched but not found, >0 = match confidence, -1 = not searched
    # method: "platform_id", "exact", "normalized", "fuzzy", "autocorrect", "fallback"
    match_confidences: dict[str, float] = field(default_factory=dict)
    match_methods: dict[str, str] = field(default_factory=dict)
    had_platform_ids: dict[str, bool] = field(default_factory=dict)
    artist_name: str = ""  # for short-name detection in match quality helpers

    # Release year summary for timeline visualization (populated by CLI)
    # Format: {year: {"albums": N, "singles": N, "eps": N}}
    release_year_summary: dict[int, dict[str, int]] = field(default_factory=dict)

    # Track which APIs errored (timeout/network) vs genuinely returned no results
    api_errors: dict[str, str] = field(default_factory=dict)

    # Deezer per-track rank data for frontend display
    deezer_track_ranks: list[dict] = field(default_factory=list)  # [{title, rank}]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class Verdict(Enum):
    """Artist authenticity verdict — ordered from most to least trustworthy."""
    VERIFIED_ARTIST = "Verified Artist"
    LIKELY_AUTHENTIC = "Likely Authentic"
    INCONCLUSIVE = "Inconclusive"
    INSUFFICIENT_DATA = "Insufficient Data"      # too few signals to judge
    CONFLICTING_SIGNALS = "Conflicting Signals"  # many signals but they disagree
    SUSPICIOUS = "Suspicious"
    LIKELY_ARTIFICIAL = "Likely Artificial"


@dataclass
class Evidence:
    """A single piece of evidence about an artist.

    The ``tags`` field carries structured metadata for downstream consumers
    (decision tree, threat category inference, category scores).  Using tags
    avoids fragile substring matching on human-readable ``finding`` text.

    Controlled tag vocabulary (aligned with simplified_scoring_architecture.md):

    Blocklist matches:
        pfc_label            — label/distributor matches pfc_distributors.json
        known_ai_artist      — name matches known_ai_artists.json
        pfc_songwriter       — contributor matches pfc_songwriters.json
        known_ai_label       — label on known-AI blocklist

    Behavioral patterns:
        content_farm         — high-volume singles-only catalog
        stream_farm          — short tracks near 30s payout threshold
        cookie_cutter        — uniform track durations
        playlist_stuffing    — streaming concentrated in top tracks
        high_release_rate    — abnormal release cadence
        same_day_release     — multiple releases on single day
        empty_catalog        — zero releases

    Positive signals:
        live_performance     — concert/tour history exists
        physical_release     — vinyl/CD releases exist
        industry_registered  — ISNI/IPI codes found
        verified_identity    — real name, aliases, group members known
        wikipedia            — Wikipedia article exists
        genuine_fans         — high play/listener ratio or follower count

    Creative signals:
        catalog_albums       — has album releases
        genius_credits       — has Genius songwriter credits
        collaboration        — has collaborators / related artists
        # touring_geography, named_tour — removed (deprecated)

    Platform / identity:
        multi_platform       — found on 3+ platforms
        single_platform      — only found on 1 platform
        social_media         — social media / web presence found
        no_social_media      — no social / web presence found
        genius_verified      — verified on Genius
        career_bio           — bio with career details / history
        no_genres            — no genre tags assigned

    Fan engagement:
        low_fans             — very low fan count
        low_scrobble_engagement — low Last.fm replay ratio

    New evidence sources (Priorities 2-7):
        ai_generated_music   — Deezer AI tag detected on album
        deezer_ai_clear      — Deezer checked, no AI tag found
        youtube_presence     — YouTube channel found with subscribers
        no_youtube           — no YouTube presence (suspicious for large artists)
        youtube_disparity    — massive Spotify/YouTube listener gap
        pro_registered       — registered songwriter with BMI/ASCAP
        no_pro_registration  — not found in PRO databases
        pfc_publisher        — publisher matches known PFC entity
        no_songwriter_share  — 100% publisher share, 0% songwriter
        normal_pro_split     — normal songwriter/publisher ownership split
        bandcamp_presence    — Bandcamp page found (strong legitimacy)
        press_coverage       — press coverage in recognized publications
        isrc_pfc_registrant  — ISRC registrant matches PFC entity
        cowriter_network     — shares producers with flagged artists

    AI-specific (from Claude analysis):
        ai_generated_image   — profile image flagged as AI
        ai_bio               — bio mentions AI or has ChatGPT style
        stock_photo          — profile image is stock photography
        impersonation        — content uploaded to wrong artist page
        abstract_image       — profile image is abstract/logo (not a person)
        authentic_photo      — profile image appears authentic
        authentic_bio        — bio text appears to describe a real artist
        suspicious_bio       — bio has hallmarks of fabricated profile
        geo_specific_bio     — bio includes geographic details
        no_geo_bio           — bio lacks geographic specificity
        verifiable_claims    — bio contains verifiable claims

    Synthesis (deep tier):
        synth_pfc_ghost      — Claude synthesis: PFC Ghost
        synth_ai_generated   — Claude synthesis: AI Generated
        synth_legitimate     — Claude synthesis: Legitimate

    Data availability:
        api_unconfigured     — API was not queried (distinct from "not found")
        not_found            — API was queried, artist not present

    Entity DB:
        entity_confirmed_bad — previously confirmed bad in entity DB
        entity_suspected     — previously suspected in entity DB
        entity_cleared       — previously cleared in entity DB
        entity_bad_label     — label flagged in entity DB
        entity_bad_songwriter — songwriter flagged in entity DB
        entity_bad_network   — connected to flagged artists via shared producers

    Name patterns:
        generic_name         — generic two-word name pattern

    API status:
        api_error            — API call failed (error/timeout), not scored
    """
    finding: str          # Short summary (e.g. "Found on Deezer with 145,231 fans")
    source: str           # Data source (e.g. "Deezer", "MusicBrainz", "Blocklist")
    evidence_type: str    # "red_flag", "green_flag", "neutral"
    strength: str         # "strong", "moderate", "weak"
    detail: str           # Longer explanation for the user
    tags: list[str] = field(default_factory=list)  # Structured metadata — see vocabulary above


@dataclass
class PlatformPresence:
    """Where does this artist exist across music platforms?"""
    deezer: bool = False
    deezer_fans: int = 0
    musicbrainz: bool = False
    genius: bool = False
    discogs: bool = False
    setlistfm: bool = False
    lastfm: bool = False
    wikipedia: bool = False
    songkick: bool = False

    def count(self) -> int:
        return sum([
            self.deezer, self.musicbrainz,
            self.genius, self.discogs, self.setlistfm,
            self.lastfm, self.wikipedia, self.songkick,
        ])

    def names(self) -> list[str]:
        """Return list of platform names where artist was found."""
        platforms = []
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
        if self.wikipedia:
            platforms.append("Wikipedia")
        if self.songkick:
            platforms.append("Songkick")
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

    # C.1: Matched decision tree rule (e.g. "Rule 2: PFC Label + Content Farm Pattern")
    matched_rule: str = ""

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
            "Deezer": self.platform_presence.deezer,
            "Genius": ext.genius_found,
            "Discogs": ext.discogs_found,
            "MusicBrainz": ext.musicbrainz_found,
            "Setlist.fm": ext.setlistfm_found,
            "Last.fm": ext.lastfm_found,
        }


def compute_category_scores(ev: ArtistEvaluation) -> dict[str, int]:
    """Compute 0-100 scores for 6 signal categories.

    Scoring tables per UI Spec Round 2, Part 5.

    Categories:
        Platform Presence: Where does this artist exist across the music ecosystem?
        Fan Engagement: Do real humans listen to and engage with this artist?
        Creative History: Does this artist have a legitimate creative body of work?
        IRL Presence: Does this artist exist in the physical world?
        Industry Signals: Is the artist recognized by the music industry infrastructure?
        Blocklist Status: Does this artist match any known fraud databases?
    """
    ext = ev.external_data or ExternalData()

    def _clamp(v: float) -> int:
        return max(0, min(100, int(v)))

    # --- 5.1 Platform Presence (0-100) ---
    platform_pts = 0

    # Deezer exists
    if ev.platform_presence.deezer:
        platform_pts += 5
    else:
        platform_pts -= 5

    # YouTube channel exists
    if ext.youtube_channel_found:
        platform_pts += 5
    else:
        platform_pts -= 5

    # Wikipedia article exists
    if ext.wikipedia_found:
        platform_pts += 15
        # Bonus: Wikipedia >= 5,000 words (~30,000 bytes)
        if ext.wikipedia_length >= 30000:
            platform_pts += 15
    elif not _api_errored(ext, "Wikipedia"):
        platform_pts -= 5

    # Genius profile exists
    if ext.genius_found:
        platform_pts += 5
    elif not _api_errored(ext, "Genius"):
        platform_pts -= 5

    # Social media presence
    social_count = 0
    if ext.genius_facebook_name:
        social_count += 1
    if ext.genius_instagram_name:
        social_count += 1
    if ext.genius_twitter_name:
        social_count += 1
    for _u in ext.discogs_social_urls:
        social_count += 1
    for _rel_type, url in ext.musicbrainz_urls.items():
        if any(s in url.lower() for s in ["facebook", "instagram", "twitter", "youtube", "bandcamp"]):
            social_count += 1
    social_count = min(social_count, 6)  # cap duplicates
    if social_count >= 4:
        platform_pts += 30  # >=2 social (15) + >=4 bonus (15)
    elif social_count >= 2:
        platform_pts += 15

    # Bio analysis
    bio_count = 0
    if ext.wikipedia_found and ext.wikipedia_length > 0:
        bio_count += 1
    if len(ext.discogs_profile) >= 50:
        bio_count += 1
    if ext.genius_found and ext.genius_followers_count >= 0:
        bio_count += 1  # Genius has profile info

    if bio_count >= 3:
        platform_pts += 30  # bio exists (15) + 3+ platforms bonus (15)
    elif bio_count >= 1:
        platform_pts += 15  # bio exists on at least one platform
    else:
        platform_pts -= 30  # no bio on any platform (strong penalty)

    # Real name known
    if ext.discogs_realname:
        platform_pts += 15

    # Bandcamp presence
    for _rel_type, url in ext.musicbrainz_urls.items():
        if "bandcamp" in url.lower():
            platform_pts += 5
            break

    platform_score = _clamp(platform_pts)

    # --- 5.2 Fan Engagement (0-100) ---
    fan_pts = 0
    fans = ev.platform_presence.deezer_fans or 0

    # Last.fm
    if ext.lastfm_found:
        fan_pts += 15
        # Listeners thresholds
        if ext.lastfm_listeners >= 100_000:
            fan_pts += 30  # 10K (15) + 100K bonus (15)
        elif ext.lastfm_listeners >= 10_000:
            fan_pts += 15

        # Play/listener ratio
        ratio = ext.lastfm_listener_play_ratio
        if 2.0 <= ratio <= 15.0:
            fan_pts += 15  # healthy range
        elif ratio < 2.0 and ratio > 0:
            fan_pts -= 5  # low repeat
        elif ratio > 15.0:
            fan_pts -= 15  # suspicious
    else:
        # Not found on Last.fm — only penalize if API didn't error
        if not _api_errored(ext, "Last.fm"):
            if fans == 0:
                fan_pts -= 30  # Not found AND 0 Deezer fans (strong)
            else:
                fan_pts -= 15  # Not found on Last.fm alone

    # Deezer fans
    if fans >= 100:
        fan_pts += 15
    elif fans > 0:
        fan_pts += 5
    elif fans == 0 and ext.lastfm_listeners >= 10_000:
        fan_pts -= 15  # 0 Deezer fans despite Last.fm audience

    engagement_score = _clamp(fan_pts)

    # --- 5.3 Creative History (0-100) ---
    creative_pts = 0

    # Album count (from evidence tags)
    for e in ev.green_flags:
        if "catalog_albums" in e.tags:
            if e.strength in ("strong", "moderate"):
                creative_pts += 30  # >=1 album (15) + >=3 albums bonus (15)
            else:
                creative_pts += 15  # >=1 album
            break

    # Track duration analysis (from evidence tags)
    for e in ev.red_flags:
        if "stream_farm" in e.tags:
            creative_pts -= 30  # avg < 90s
            break
    else:
        # Check for normal duration (green flag)
        for e in ev.green_flags + ev.neutral_notes:
            if e.finding and "normal track" in e.finding.lower():
                creative_pts += 5
                break

    # Cookie-cutter durations
    for e in ev.red_flags:
        if "cookie_cutter" in e.tags:
            creative_pts -= 15
            break

    # Content farm pattern (singles only)
    for e in ev.red_flags:
        if "content_farm" in e.tags:
            if e.strength == "strong":
                creative_pts -= 30  # >= 40 singles, 0 albums
            else:
                creative_pts -= 15  # >= 20 singles, 0 albums
            break

    # Empty catalog
    for e in ev.red_flags:
        if "empty_catalog" in e.tags:
            creative_pts -= 30
            break

    # Collaborations
    for e in ev.green_flags:
        if "collaboration" in e.tags:
            creative_pts += 15
            break

    # Genius presence
    if ext.genius_found and ext.genius_song_count >= 1:
        creative_pts += 5

    creative_score = _clamp(creative_pts)

    # --- 5.4 IRL Presence (0-100) ---
    live_pts = 0
    has_concerts = False
    has_physical = False

    # Setlist.fm
    if ext.setlistfm_total_shows >= 10:
        live_pts += 30  # established touring (strong)
        has_concerts = True
    elif ext.setlistfm_total_shows >= 1:
        live_pts += 15  # any live history (moderate)
        has_concerts = True
    # Also check Songkick as supplementary concert source
    elif ext.songkick_total_past_events >= 10:
        live_pts += 30
        has_concerts = True
    elif ext.songkick_total_past_events >= 1:
        live_pts += 15
        has_concerts = True

    # Physical releases on Discogs (strong signal)
    if ext.discogs_physical_releases >= 5:
        live_pts += 45  # >=1 physical (30) + >=5 bonus (15)
        has_physical = True
    elif ext.discogs_physical_releases >= 1:
        live_pts += 30  # tangible evidence (strong)
        has_physical = True

    # Penalties for missing — only if APIs didn't error
    setlistfm_err = _api_errored(ext, "Setlist.fm")
    songkick_err = _api_errored(ext, "Songkick")
    discogs_err = _api_errored(ext, "Discogs")
    concerts_unknowable = setlistfm_err and songkick_err
    physical_unknowable = discogs_err

    if not has_concerts and not has_physical:
        if not concerts_unknowable and not physical_unknowable:
            live_pts -= 30  # no concerts AND no physical (strong)
        elif not concerts_unknowable:
            live_pts -= 15  # only know concerts are missing
        elif not physical_unknowable:
            live_pts -= 15  # only know physical is missing
        # else: both errored, don't penalize
    elif not has_concerts and not concerts_unknowable:
        live_pts -= 15  # no concerts
    elif not has_physical and not physical_unknowable:
        live_pts -= 15  # no physical releases

    live_score = _clamp(live_pts)

    # --- 5.5 Industry Signals (0-100) ---
    industry_pts = 0

    # MusicBrainz entry
    if ext.musicbrainz_found:
        industry_pts += 5

        # MusicBrainz completeness (relationship count)
        mb_rel_count = getattr(ext, 'musicbrainz_relationship_count', 0)
        if mb_rel_count >= 10:
            industry_pts += 30  # rich profile (strong)
        elif mb_rel_count >= 3:
            industry_pts += 15  # moderate profile
        else:
            industry_pts -= 5  # stub (weak)

        # Complete metadata (type + country + dates)
        has_complete = all([ext.musicbrainz_type, ext.musicbrainz_country,
                           ext.musicbrainz_begin_date])
        if has_complete:
            industry_pts += 15
    elif not _api_errored(ext, "MusicBrainz"):
        industry_pts -= 15  # no MusicBrainz entry (only if API didn't error)

    # ISNI
    if ext.musicbrainz_isnis:
        industry_pts += 30

    # IPI
    if ext.musicbrainz_ipis:
        industry_pts += 30

    # ASCAP/BMI registration (C.3 scoring)
    if ext.pro_checked:
        if ext.pro_songwriter_registered:
            industry_pts += 30  # registered as songwriter → +30 Strong
            # Normal writer/publisher split → +5 Weak
            if ext.pro_songwriter_share_pct >= 30:
                industry_pts += 5
            # 0% songwriter share → -15 Moderate
            if ext.pro_zero_songwriter_share:
                industry_pts -= 15
            # PFC publisher match → -30 Strong (overrides the +30)
            if ext.pro_pfc_publisher_match:
                industry_pts -= 30
        else:
            industry_pts -= 5  # not found → -5 Weak
    # No ISNI, IPI, or PRO
    if not ext.musicbrainz_isnis and not ext.musicbrainz_ipis and not ext.pro_songwriter_registered:
        industry_pts -= 5  # weak negative for missing all identifiers

    # Bio analysis Phase 1: presence + length + generic detection
    bio_sources = 0
    total_bio_chars = 0
    if ext.discogs_profile and len(ext.discogs_profile) > 0:
        bio_sources += 1
        total_bio_chars += len(ext.discogs_profile)
    if ext.genius_found and ext.genius_description:
        bio_sources += 1
        total_bio_chars += len(ext.genius_description)
    if ext.lastfm_bio_exists:
        bio_sources += 1
    if ext.wikipedia_found and ext.wikipedia_length > 0:
        bio_sources += 1
        total_bio_chars += ext.wikipedia_length

    if total_bio_chars >= 500 and bio_sources >= 2:
        industry_pts += 15  # substantial bios across multiple sources
    elif total_bio_chars >= 200:
        industry_pts += 10  # decent bio content
    elif total_bio_chars >= 50:
        industry_pts += 5   # minimal bio
    elif bio_sources == 0 and not _api_errored(ext, "Discogs") and not _api_errored(ext, "Genius"):
        industry_pts -= 5   # no bios found despite checking multiple APIs

    # Discogs data quality
    if ext.discogs_data_quality == "Correct":
        industry_pts += 5
    elif ext.discogs_data_quality:
        industry_pts += 3

    # PFC label penalty
    for e in ev.red_flags:
        if "pfc_label" in e.tags:
            industry_pts -= 40

    industry_score = _clamp(industry_pts)

    # --- 5.6 Blocklist Status (0-100) ---
    # Starts at 100. Any match → binary red display.
    blocklist_pts = 100

    for e in ev.red_flags:
        tag_set = set(e.tags) if e.tags else set()
        if tag_set & {"known_ai_artist"}:
            blocklist_pts -= 100  # artist name match → 0
        if tag_set & {"pfc_label", "known_ai_label"}:
            blocklist_pts -= 100  # label match → 0
        if tag_set & {"pfc_songwriter"}:
            blocklist_pts -= 80
        if tag_set & {"pfc_publisher"}:
            blocklist_pts -= 80
        if tag_set & {"isrc_pfc_registrant"}:
            blocklist_pts -= 60
        if tag_set & {"entity_bad_network"}:
            blocklist_pts -= 50
        if tag_set & {"entity_confirmed_bad"}:
            blocklist_pts -= 100
        if tag_set & {"entity_suspected"}:
            blocklist_pts -= 50

    blocklist_score = _clamp(blocklist_pts)

    return {
        "Platform Presence": platform_score,
        "Fan Engagement": engagement_score,
        "Creative History": creative_score,
        "IRL Presence": live_score,
        "Industry Signals": industry_score,
        "Blocklist Status": blocklist_score,
    }


# ---------------------------------------------------------------------------
# Match quality helpers — modulate evidence strength based on search confidence
# ---------------------------------------------------------------------------

def _api_errored(ext: ExternalData, platform: str) -> bool:
    """Check if an API call errored/timed out (vs genuinely not finding the artist).

    When an API errored, we should NOT penalize the artist — the absence of data
    is due to our failure, not the artist's absence from the platform.
    """
    return platform.lower() in {k.lower() for k in ext.api_errors}


def _error_evidence(platform: str, ext: ExternalData) -> Evidence:
    """Return a neutral evidence item for an API that errored/timed out."""
    error_msg = ""
    for k, v in ext.api_errors.items():
        if k.lower() == platform.lower():
            error_msg = v
            break
    return Evidence(
        finding=f"{platform}: API error (not scored)",
        source=platform,
        evidence_type="neutral",
        strength="weak",
        detail=f"Could not reach {platform} — this is NOT scored as 'not found'. "
               f"Error: {error_msg[:200] if error_msg else 'timeout or connection failure'}",
        tags=["api_error"],
    )


def _not_found_strength(ext: ExternalData, platform: str, artist_name: str = "") -> str:
    """Determine how strong a 'not found' red flag should be.

    Factors:
    - If we had a platform ID (from MusicBrainz bridging) and still got no result,
      that's a strong signal the artist truly isn't there.
    - Name-only search for a very short name → weaker signal (matching uncertainty).
    - Default is 'moderate' (current behavior preserved for backward compat).
    """
    had_id = ext.had_platform_ids.get(platform, False)
    if had_id:
        return "strong"

    # Short names have high matching uncertainty
    name_len = len(artist_name.strip()) if artist_name else 0
    if 0 < name_len <= 3:
        return "weak"

    return "moderate"


def _found_strength(base_strength: str, ext: ExternalData, platform: str) -> str:
    """Optionally downgrade green flag strength for low-confidence matches.

    If the match was fuzzy with low confidence, a green flag based on that data
    is less trustworthy. Downgrade by one level for confidence < 0.85.
    """
    confidence = ext.match_confidences.get(platform, 1.0)
    method = ext.match_methods.get(platform, "")

    # Platform ID bridging and exact matches — full trust
    if method in ("platform_id", "exact") or confidence >= 0.85:
        return base_strength

    # Low-confidence fuzzy match — downgrade one level
    _downgrade = {"strong": "moderate", "moderate": "weak", "weak": "weak"}
    return _downgrade.get(base_strength, base_strength)


# ---------------------------------------------------------------------------
# Evidence collectors — each examines one aspect of the data
# ---------------------------------------------------------------------------

def _collect_platform_evidence(artist: ArtistInfo) -> tuple[PlatformPresence, list[Evidence]]:
    """Determine which platforms the artist exists on."""
    presence = PlatformPresence()
    evidence: list[Evidence] = []

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
            tags=["multi_platform"],
        ))
    elif platforms_found >= 2:
        evidence.append(Evidence(
            finding=f"Found on {platforms_found} platforms",
            source="Cross-platform",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Found on: {', '.join(presence.names())}.",
            tags=["multi_platform"],
        ))
    elif platforms_found <= 1:
        evidence.append(Evidence(
            finding="Only found on 1 platform",
            source="Cross-platform",
            evidence_type="red_flag",
            strength="weak",
            detail="Artist only verified on a single platform. "
                   "Could be new or could be a fabricated artist.",
            tags=["single_platform"],
        ))

    return presence, evidence


def _collect_follower_evidence(artist: ArtistInfo) -> list[Evidence]:
    """Analyze follower/fan counts."""
    evidence: list[Evidence] = []
    fans = artist.deezer_fans

    if fans >= 100_000:
        evidence.append(Evidence(
            finding=f"{fans:,} fans",
            source="Deezer",
            evidence_type="green_flag",
            strength="strong",
            detail=f"Artist has {fans:,} fans on Deezer — substantial organic following.",
            tags=["genuine_fans"],
        ))
    elif fans >= 10_000:
        evidence.append(Evidence(
            finding=f"{fans:,} fans",
            source="Deezer",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Artist has {fans:,} fans on Deezer — meaningful audience.",
            tags=["genuine_fans"],
        ))
    elif fans >= 1_000:
        evidence.append(Evidence(
            finding=f"{fans:,} fans",
            source="Deezer",
            evidence_type="neutral",
            strength="weak",
            detail=f"Artist has {fans:,} fans on Deezer — small but plausible audience.",
        ))
    elif fans > 0:
        evidence.append(Evidence(
            finding=f"Only {fans:,} fans",
            source="Deezer",
            evidence_type="red_flag",
            strength="weak",
            detail=f"Only {fans:,} fans on Deezer. Could be a new artist or a ghost artist.",
            tags=["low_fans"],
        ))
    else:
        evidence.append(Evidence(
            finding="No fan data available",
            source="Deezer",
            evidence_type="neutral",
            strength="weak",
            detail="Could not determine fan count from available data.",
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
            source="Deezer",
            evidence_type="red_flag",
            strength="moderate",
            detail="No albums or singles found. Could be a very new or fabricated artist.",
            tags=["empty_catalog"],
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
            tags=["catalog_albums"],
        ))
    elif albums >= 1:
        evidence.append(Evidence(
            finding=f"{albums} album(s) in catalog",
            source="Deezer",
            evidence_type="green_flag",
            strength="weak",
            detail=f"Artist has {albums} album(s). At least some long-form releases.",
            tags=["catalog_albums"],
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
            tags=["content_farm"],
        ))
    elif albums == 0 and singles > 10:
        evidence.append(Evidence(
            finding=f"{singles} singles, 0 albums",
            source="Deezer",
            evidence_type="red_flag",
            strength="moderate",
            detail=f"{singles} singles with no albums. Could be a singles-focused "
                   "artist or could indicate content farming.",
            tags=["content_farm"],
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
            tags=["stream_farm"],
        ))
    elif avg_s < 120:
        evidence.append(Evidence(
            finding=f"Short average track length: {avg_s:.0f} seconds",
            source="Deezer",
            evidence_type="red_flag",
            strength="moderate",
            detail=f"Average track is {avg_s:.0f}s — shorter than typical songs (180-240s).",
            tags=["stream_farm"],
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
            tags=["cookie_cutter"],
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
            tags=["high_release_rate", "same_day_release", "content_farm"],
        ))
        return evidence

    span_months = max(span_days / 30.0, 1)
    span_years = span_months / 12.0

    # Human-readable time span
    def _fmt_span(months: float) -> str:
        if months >= 24:
            return f"{months / 12:.0f} years"
        elif months >= 12:
            return f"{months / 12:.1f} years"
        else:
            return f"{months:.0f} months"

    span_str = _fmt_span(span_months)

    # Separate singles vs albums for proper thresholds
    albums = artist.album_count
    singles = artist.single_count
    total_releases = len(parsed)

    # Calculate per-year rates when we have type breakdown
    if albums + singles > 0:
        albums_per_year = albums / max(span_years, 1/12)
        singles_per_year = singles / max(span_years, 1/12)
        singles_per_album = singles / albums if albums > 0 else float('inf')

        # Build human-readable rate summary
        rate_parts: list[str] = []
        if albums > 0:
            rate_parts.append(f"{albums_per_year:.1f} albums/year")
        if singles > 0:
            rate_parts.append(f"{singles_per_year:.1f} singles/year")
        if albums > 0 and singles > 0:
            rate_parts.append(f"{singles_per_album:.1f} singles per album")
        rate_summary = ", ".join(rate_parts)

        # Albums: > 24/year is extreme, > 12/year is high
        if albums_per_year > 24:
            evidence.append(Evidence(
                finding=f"{albums} albums over {span_str} — {albums_per_year:.0f}/year (extreme)",
                source="Deezer",
                evidence_type="red_flag",
                strength="strong",
                detail=f"{rate_summary}. "
                       "Albums require significant creative investment — this rate "
                       "suggests automated production.",
                tags=["high_release_rate"],
            ))
        elif albums_per_year > 12 and albums >= 3:
            evidence.append(Evidence(
                finding=f"{albums} albums over {span_str} — {albums_per_year:.0f}/year (high)",
                source="Deezer",
                evidence_type="red_flag",
                strength="moderate",
                detail=f"{rate_summary}. Higher than most real artists.",
                tags=["high_release_rate"],
            ))

        # Singles: > 72/year is extreme, > 36/year is high
        if singles_per_year > 72:
            evidence.append(Evidence(
                finding=f"{singles} singles over {span_str} — {singles_per_year:.0f}/year (extreme)",
                source="Deezer",
                evidence_type="red_flag",
                strength="strong",
                detail=f"{rate_summary}. "
                       "Even prolific artists rarely release more than 24-36 singles/year.",
                tags=["high_release_rate"],
            ))
        elif singles_per_year > 36 and singles >= 5:
            evidence.append(Evidence(
                finding=f"{singles} singles over {span_str} — {singles_per_year:.0f}/year (high)",
                source="Deezer",
                evidence_type="red_flag",
                strength="moderate",
                detail=f"{rate_summary}.",
                tags=["high_release_rate"],
            ))

        # Singles-to-album ratio: many singles per album can indicate content farming
        if albums >= 2 and singles_per_album > 10:
            evidence.append(Evidence(
                finding=f"{singles_per_album:.0f} singles per album (high ratio)",
                source="Deezer",
                evidence_type="red_flag",
                strength="weak",
                detail=f"{singles} singles vs {albums} albums ({singles_per_album:.1f}:1 ratio). "
                       "A very high singles-to-album ratio can indicate a content farm approach.",
                tags=["high_release_rate"],
            ))

        # Normal pace with enough history
        releases_per_year = (albums + singles) / max(span_years, 1/12)
        if releases_per_year <= 18 and total_releases >= 5:
            evidence.append(Evidence(
                finding=f"Steady release pace over {span_str} ({rate_summary})",
                source="Deezer",
                evidence_type="green_flag",
                strength="weak",
                detail=f"{albums} albums + {singles} singles over {span_str} "
                       "is consistent with a working musician.",
            ))
    else:
        # Fallback: no type breakdown available
        releases_per_year = total_releases / max(span_years, 1/12)
        if releases_per_year > 96:
            evidence.append(Evidence(
                finding=f"{total_releases} releases over {span_str} — {releases_per_year:.0f}/year (extreme)",
                source="Deezer",
                evidence_type="red_flag",
                strength="strong",
                detail=f"Even prolific artists rarely exceed 24-36 releases/year. "
                       "This rate suggests automated production.",
                tags=["high_release_rate"],
            ))
        elif releases_per_year > 48:
            evidence.append(Evidence(
                finding=f"{total_releases} releases over {span_str} — {releases_per_year:.0f}/year (high)",
                source="Deezer",
                evidence_type="red_flag",
                strength="moderate",
                detail=f"Higher than most real artists.",
                tags=["high_release_rate"],
            ))
        elif releases_per_year <= 12 and total_releases >= 5:
            evidence.append(Evidence(
                finding=f"Steady release pace ({total_releases} releases over {span_str})",
                source="Deezer",
                evidence_type="green_flag",
                strength="weak",
                detail=f"{releases_per_year:.1f} releases/year is consistent with a working musician.",
            ))

    return evidence


def _collect_label_evidence(artist: ArtistInfo) -> list[Evidence]:
    """Check labels against all blocklists (PFC distributors, known AI, songwriters)."""
    evidence: list[Evidence] = []
    if not artist.labels:
        return evidence

    pfc_labels = pfc_distributors()
    ai_names = known_ai_artists()

    # Single lowercasing pass, then set intersection
    labels_lower = {l.lower(): l for l in artist.labels}
    labels_lower_set = labels_lower.keys()
    matched_pfc = [labels_lower[l] for l in labels_lower_set & pfc_labels]
    matched_ai = [labels_lower[l] for l in labels_lower_set & ai_names]

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
            tags=["pfc_label"],
        ))

    if matched_ai:
        evidence.append(Evidence(
            finding=f"Label matches known AI blocklist: {', '.join(matched_ai)}",
            source="Blocklist",
            evidence_type="red_flag",
            strength="strong",
            detail=f"Label {', '.join(matched_ai)} is on the known AI artist/label blocklist.",
            tags=["known_ai_label"],
        ))

    # Check contributors against PFC songwriter blocklist
    pfc_writers = pfc_songwriters()
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
                tags=["pfc_songwriter"],
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
    if name.lower() in known_ai_artists():
        evidence.append(Evidence(
            finding="Name matches known AI artist blocklist",
            source="Blocklist",
            evidence_type="red_flag",
            strength="strong",
            detail=f'"{name}" is on our list of known AI-generated artist names.',
            tags=["known_ai_artist"],
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
            tags=["generic_name"],
        ))

    # Mood-word track titles removed per alignment doc — too many false positives
    # with ambient/wellness/classical genres

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
            tags=["collaboration"],
        ))
    elif len(artist.contributors) >= 1:
        evidence.append(Evidence(
            finding=f"{len(artist.contributors)} collaborator(s)",
            source="Deezer",
            evidence_type="green_flag",
            strength="weak",
            detail=f"Collaborators: {', '.join(artist.contributors)}.",
            tags=["collaboration"],
        ))

    # Related artists on Deezer
    # Note: Deezer's related artist algorithm uses collaborative filtering,
    # which can generate links for any artist appearing on similar playlists —
    # including artificial ones. Treat as a weak signal only.
    if len(artist.related_artist_names) >= 5:
        evidence.append(Evidence(
            finding=f"{len(artist.related_artist_names)} related artists on Deezer",
            source="Deezer",
            evidence_type="green_flag",
            strength="weak",
            detail=f"Deezer links this artist to: "
                   f"{', '.join(artist.related_artist_names[:5])}. "
                   "Related artist connections can develop from listener behavior, "
                   "but can also appear for playlist-placed artists.",
        ))
    elif len(artist.related_artist_names) >= 1:
        evidence.append(Evidence(
            finding=f"{len(artist.related_artist_names)} related artist(s) on Deezer",
            source="Deezer",
            evidence_type="neutral",
            strength="weak",
            detail=f"Related: {', '.join(artist.related_artist_names[:3])}. "
                   "Deezer related artists can appear for both real and artificial artists.",
        ))

    return evidence


def _collect_credit_network_evidence(artist: ArtistInfo) -> list[Evidence]:
    """Check if track credits match known PFC ghost producers/songwriters."""
    evidence: list[Evidence] = []
    watchlist = pfc_songwriters()
    if not watchlist:
        return evidence

    # Check contributor names against songwriter watchlist
    matched: list[str] = []
    for contributor in artist.contributors:
        if contributor.lower() in watchlist:
            matched.append(contributor)

    # Also check contributor_roles keys (may have names not in contributors list)
    for name in artist.contributor_roles:
        if name.lower() in watchlist and name not in matched:
            matched.append(name)

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
            tags=["pfc_songwriter"],
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


def _collect_genre_evidence(artist: ArtistInfo, ext: "ExternalData | None" = None) -> list[Evidence]:
    """Analyze genre data from Deezer or MusicBrainz."""
    evidence: list[Evidence] = []

    # Prefer MusicBrainz genres (community-curated), fall back to Deezer/artist genres
    genres = []
    genre_source = "Deezer"
    if ext and ext.musicbrainz_genres:
        genres = ext.musicbrainz_genres
        genre_source = "MusicBrainz"
    elif artist.genres:
        genres = artist.genres

    if not genres:
        # Only flag as red if we checked MusicBrainz and found nothing
        if ext and ext.musicbrainz_found:
            evidence.append(Evidence(
                finding="No genres assigned",
                source="MusicBrainz",
                evidence_type="red_flag",
                strength="weak",
                detail="No genre tags found on MusicBrainz. Established artists "
                       "typically have community-assigned genre tags.",
                tags=["no_genres"],
            ))
    elif len(genres) >= 3:
        evidence.append(Evidence(
            finding=f"{len(genres)} genres: {', '.join(genres[:4])}",
            source=genre_source,
            evidence_type="green_flag",
            strength="weak",
            detail=f"Multiple genre classifications on {genre_source} suggest "
                   "this is a recognized artist.",
        ))

    return evidence


def _collect_track_rank_evidence(artist: ArtistInfo) -> list[Evidence]:
    """Analyze Deezer track ranks for popularity signals."""
    evidence: list[Evidence] = []

    if not artist.track_ranks:
        return evidence

    avg_rank = statistics.mean(artist.track_ranks)

    # Top tracks concentration: 3-tier scoring for playlist stuffing detection
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
                    strength="strong",
                    detail=f"Out of {len(artist.track_ranks)} tracks, the top 2 account for "
                           f"{top2_share:.0%} of all popularity. This extreme concentration "
                           "is a strong signal of playlist stuffing.",
                    tags=["playlist_stuffing"],
                ))
            elif top2_share >= 0.80:
                evidence.append(Evidence(
                    finding=f"Top 2 tracks hold {top2_share:.0%} of total rank score",
                    source="Deezer",
                    evidence_type="red_flag",
                    strength="moderate",
                    detail=f"Out of {len(artist.track_ranks)} tracks, the top 2 account for "
                           f"{top2_share:.0%} of all popularity. This concentration pattern "
                           "is consistent with playlist stuffing.",
                    tags=["playlist_stuffing"],
                ))
            elif top2_share >= 0.70:
                evidence.append(Evidence(
                    finding=f"Top 2 tracks hold {top2_share:.0%} of total rank score",
                    source="Deezer",
                    evidence_type="red_flag",
                    strength="weak",
                    detail=f"Out of {len(artist.track_ranks)} tracks, the top 2 account for "
                           f"{top2_share:.0%} of all popularity. Mildly concentrated — "
                           "could be normal for artists with one breakout hit.",
                    tags=["playlist_stuffing"],
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
        # Fix 1: If the API errored, don't penalize — emit neutral instead
        if _api_errored(ext, "Genius"):
            return [_error_evidence("Genius", ext)]
        evidence.append(Evidence(
            finding="Not found on Genius",
            source="Genius",
            evidence_type="red_flag",
            strength=_not_found_strength(ext, "genius", ext.artist_name),
            detail="Artist has no page on Genius. Real songwriters and performers "
                   "almost always have lyrics/credits on Genius. Ghost and AI artists "
                   "typically have no Genius presence.",
            tags=["not_found"],
        ))
        return evidence

    # Found on Genius
    if ext.genius_song_count >= 20:
        evidence.append(Evidence(
            finding=f"{ext.genius_song_count} songs on Genius",
            source="Genius",
            evidence_type="green_flag",
            strength=_found_strength("strong", ext, "genius"),
            detail=f"Artist has {ext.genius_song_count} songs with lyrics/credits on Genius. "
                   "This is strong evidence of a real artist with legitimate songwriting credits.",
            tags=["genius_credits"],
        ))
    elif ext.genius_song_count >= 5:
        evidence.append(Evidence(
            finding=f"{ext.genius_song_count} songs on Genius",
            source="Genius",
            evidence_type="green_flag",
            strength=_found_strength("moderate", ext, "genius"),
            detail=f"Artist has {ext.genius_song_count} songs on Genius — real songwriting credits exist.",
            tags=["genius_credits"],
        ))
    elif ext.genius_song_count >= 1:
        evidence.append(Evidence(
            finding=f"{ext.genius_song_count} song(s) on Genius",
            source="Genius",
            evidence_type="green_flag",
            strength=_found_strength("weak", ext, "genius"),
            detail=f"Found {ext.genius_song_count} song(s) on Genius. Minimal but present.",
            tags=["genius_credits"],
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
        if _api_errored(ext, "Discogs"):
            return [_error_evidence("Discogs", ext)]
        evidence.append(Evidence(
            finding="Not found on Discogs",
            source="Discogs",
            evidence_type="red_flag",
            strength=_not_found_strength(ext, "discogs", ext.artist_name),
            detail="No Discogs profile found. Discogs catalogs physical music releases "
                   "(vinyl, CD, cassette). Ghost and AI artists virtually never have "
                   "physical releases.",
            tags=["not_found"],
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
            strength=_found_strength("strong", ext, "discogs"),
            detail=f"Artist has {ext.discogs_physical_releases} physical releases "
                   f"(formats: {', '.join(ext.discogs_formats[:5])}). "
                   "Pressing vinyl or manufacturing CDs requires real investment — "
                   "this is very strong evidence of a legitimate artist.",
            tags=["physical_release"],
        ))
    elif ext.discogs_physical_releases >= 3:
        evidence.append(Evidence(
            finding=f"{ext.discogs_physical_releases} physical releases on Discogs",
            source="Discogs",
            evidence_type="green_flag",
            strength=_found_strength("strong", ext, "discogs"),
            detail=f"Artist has {ext.discogs_physical_releases} physical releases "
                   f"({', '.join(ext.discogs_formats[:5])}). Physical media is strong proof of legitimacy.",
            tags=["physical_release"],
        ))
    elif ext.discogs_physical_releases >= 1:
        evidence.append(Evidence(
            finding=f"{ext.discogs_physical_releases} physical release(s) on Discogs",
            source="Discogs",
            evidence_type="green_flag",
            strength=_found_strength("moderate", ext, "discogs"),
            detail=f"At least {ext.discogs_physical_releases} physical release exists.",
            tags=["physical_release"],
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
        discogs_pfc_matches = [l for l in ext.discogs_labels if l.lower() in pfc_distributors()]
        if discogs_pfc_matches:
            evidence.append(Evidence(
                finding=f"Discogs labels match PFC blocklist: {', '.join(discogs_pfc_matches)}",
                source="Discogs",
                evidence_type="red_flag",
                strength="strong",
                detail=f"Discogs confirms distribution by PFC-associated label(s): "
                       f"{', '.join(discogs_pfc_matches)}.",
                tags=["pfc_label"],
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
                tags=["live_performance"],
            ))
        elif ext.setlistfm_total_shows >= 10:
            evidence.append(Evidence(
                finding=f"{ext.setlistfm_total_shows} concerts on Setlist.fm",
                source="Setlist.fm",
                evidence_type="green_flag",
                strength="strong",
                detail=f"Artist has {ext.setlistfm_total_shows} recorded live performances. "
                       "Concert history is very strong proof of a real artist.",
                tags=["live_performance"],
            ))
        elif ext.setlistfm_total_shows >= 1:
            evidence.append(Evidence(
                finding=f"{ext.setlistfm_total_shows} concert(s) on Setlist.fm",
                source="Setlist.fm",
                evidence_type="green_flag",
                strength="moderate",
                detail=f"At least {ext.setlistfm_total_shows} live performance(s) recorded.",
                tags=["live_performance"],
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
        if _api_errored(ext, "Setlist.fm"):
            evidence.append(_error_evidence("Setlist.fm", ext))
        else:
            evidence.append(Evidence(
                finding="Not found on Setlist.fm",
                source="Setlist.fm",
                evidence_type="red_flag",
                strength=_not_found_strength(ext, "setlistfm", ext.artist_name),
                detail="No concert history found on Setlist.fm. Could be a new or "
                       "studio-only artist, or could indicate a non-performing entity.",
                tags=["not_found"],
            ))

    # Combined live show assessment — only add if not already covered by setlistfm-specific flag
    # Don't penalize if APIs errored — we just don't know
    songkick_shows = getattr(ext, "songkick_total_past_events", 0) or 0
    setlistfm_errored = _api_errored(ext, "Setlist.fm")
    songkick_errored = _api_errored(ext, "Songkick")
    if (total_shows == 0 and not ext.setlistfm_found and songkick_shows == 0
            and not getattr(ext, "songkick_found", False)
            and not setlistfm_errored and not songkick_errored):
        evidence.append(Evidence(
            finding="No live performance history found anywhere",
            source="Live shows",
            evidence_type="red_flag",
            strength="moderate",
            detail="No concerts found on Setlist.fm or Songkick. While some real "
                   "artists are studio-only, the absence of any live history is a "
                   "common pattern for ghost and AI-generated artists.",
            tags=["concert_history"],
        ))

    return evidence


def _collect_musicbrainz_evidence(ext: ExternalData) -> list[Evidence]:
    """Analyze MusicBrainz metadata richness."""
    evidence: list[Evidence] = []

    if not ext.musicbrainz_found:
        if _api_errored(ext, "MusicBrainz"):
            return [_error_evidence("MusicBrainz", ext)]
        evidence.append(Evidence(
            finding="Not found on MusicBrainz",
            source="MusicBrainz",
            evidence_type="red_flag",
            strength=_not_found_strength(ext, "musicbrainz", ext.artist_name),
            detail="No MusicBrainz entry found. MusicBrainz is a comprehensive "
                   "open-source music database. Established artists usually have entries.",
            tags=["not_found"],
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
        mb_pfc_matches = [l for l in ext.musicbrainz_labels if l.lower() in pfc_distributors()]
        if mb_pfc_matches:
            evidence.append(Evidence(
                finding=f"MusicBrainz labels match PFC blocklist: {', '.join(mb_pfc_matches)}",
                source="MusicBrainz",
                evidence_type="red_flag",
                strength="strong",
                detail=f"MusicBrainz confirms distribution by PFC-associated label(s): "
                       f"{', '.join(mb_pfc_matches)}.",
                tags=["pfc_label"],
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
            tags=["social_media"],
        ))
    elif len(social_links) >= 2:
        platforms_str = ", ".join(f"{k} (via {v})" for k, v in sorted(social_links.items()))
        evidence.append(Evidence(
            finding=f"{len(social_links)} social/web presences found",
            source="Social media",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Found: {platforms_str}.",
            tags=["social_media"],
        ))
    elif len(social_links) == 1:
        platforms_str = ", ".join(f"{k} (via {v})" for k, v in social_links.items())
        evidence.append(Evidence(
            finding=f"1 social/web presence: {platforms_str}",
            source="Social media",
            evidence_type="green_flag",
            strength="weak",
            tags=["social_media"],
            detail=f"Minimal web presence found: {platforms_str}.",
        ))
    else:
        # Only flag if we actually checked multiple APIs successfully (not errored)
        apis_checked = sum([
            ext.genius_found,
            ext.discogs_found,
            ext.musicbrainz_found,
        ])
        apis_errored = sum([
            _api_errored(ext, "Genius"),
            _api_errored(ext, "Discogs"),
            _api_errored(ext, "MusicBrainz"),
        ])
        if apis_checked >= 2 and apis_errored == 0:
            evidence.append(Evidence(
                finding="No social media or website links found",
                source="Social media",
                evidence_type="red_flag",
                strength="moderate",
                detail="Checked Genius, Discogs, and MusicBrainz — "
                       "no social media profiles or official website found. "
                       "Real artists almost always have some web presence.",
                tags=["no_social_media"],
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
            tags=["genius_verified"],
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
    # Only emit this if the dedicated Wikipedia client didn't find an article
    # (avoids double-counting the same signal).
    if not ext.wikipedia_found:
        has_wikipedia = any("wikipedia" in k.lower() or "wikipedia" in v.lower()
                            for k, v in ext.musicbrainz_urls.items())
        if has_wikipedia:
            evidence.append(Evidence(
                finding="Has Wikipedia article (via MusicBrainz)",
                source="MusicBrainz",
                evidence_type="green_flag",
                strength="strong",
                detail="Artist has a Wikipedia article linked from MusicBrainz. "
                       "Wikipedia's notability requirements make this strong proof of legitimacy.",
                tags=["wikipedia"],
            ))
    # Wikidata presence (separate from Wikipedia article)
    has_wikidata = any("wikidata" in k.lower() or "wikidata" in v.lower()
                       for k, v in ext.musicbrainz_urls.items())
    if has_wikidata and not ext.wikipedia_found:
        evidence.append(Evidence(
            finding="Has Wikidata entry",
            source="MusicBrainz",
            evidence_type="green_flag",
            strength="weak",
            detail="Artist has a Wikidata entry linked from MusicBrainz.",
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
                tags=["career_bio"],
            ))
        elif bio_len >= 200:
            evidence.append(Evidence(
                finding=f"Discogs bio ({bio_len} chars)",
                source="Discogs",
                evidence_type="green_flag",
                strength="moderate",
                detail=f"Has a detailed Discogs biography: "
                       f"\"{bio[:150]}{'...' if bio_len > 150 else ''}\"",
                tags=["career_bio"],
            ))
        elif bio_len >= 50 and career_hits:
            evidence.append(Evidence(
                finding=f"Discogs bio with career details ({bio_len} chars)",
                source="Discogs",
                evidence_type="green_flag",
                strength="moderate",
                detail=f"Bio mentions: {', '.join(career_hits[:3])}. "
                       f"\"{bio[:120]}{'...' if bio_len > 120 else ''}\"",
                tags=["career_bio"],
            ))
        elif bio_len >= 50:
            evidence.append(Evidence(
                finding=f"Discogs bio ({bio_len} chars)",
                source="Discogs",
                evidence_type="green_flag",
                strength="weak",
                detail=f"Has a brief Discogs biography: \"{bio[:100]}\"",
                tags=["career_bio"],
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
            tags=["verified_identity"],
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
            tags=["verified_identity"],
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
            tags=["industry_registered"],
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
            tags=["industry_registered"],
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
        if _api_errored(ext, "Last.fm"):
            return [_error_evidence("Last.fm", ext)]
        evidence.append(Evidence(
            finding="Not found on Last.fm",
            source="Last.fm",
            evidence_type="red_flag",
            strength=_not_found_strength(ext, "lastfm", ext.artist_name),
            detail="Artist has no Last.fm presence. Real artists with significant "
                   "Spotify streams almost always have Last.fm scrobble data. "
                   "Ghost artists typically have zero Last.fm activity.",
            tags=["not_found"],
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
                tags=["genuine_fans"],
            ))
        elif ratio >= 4:
            evidence.append(Evidence(
                finding=f"Moderate scrobble engagement (play/listener ratio: {ratio:.1f})",
                source="Last.fm",
                evidence_type="green_flag",
                strength="moderate",
                detail=f"Each listener averages {ratio:.1f} plays. Reasonable replay "
                       "rate suggesting some genuine fan engagement.",
                tags=["genuine_fans"],
            ))
        elif ratio >= 2:
            evidence.append(Evidence(
                finding=f"Low scrobble engagement (play/listener ratio: {ratio:.1f})",
                source="Last.fm",
                evidence_type="neutral",
                strength="weak",
                detail=f"Each listener averages {ratio:.1f} plays. Borderline — "
                       "could indicate casual listeners or early-stage fanbase.",
            ))
        elif ext.lastfm_listeners >= 100:
            evidence.append(Evidence(
                finding=f"Very low scrobble engagement (play/listener ratio: {ratio:.1f})",
                source="Last.fm",
                evidence_type="red_flag",
                strength="moderate",
                detail=f"Despite {ext.lastfm_listeners:,} listeners, each averages only "
                       f"{ratio:.1f} plays. This suggests passive/algorithmic listening "
                       "rather than genuine fans — a common pattern with PFC content.",
                tags=["low_scrobble_engagement"],
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


## _collect_touring_geography_evidence removed — deprecated per spec.
## Geographic spread is now handled by live_performance tags in Songkick collector.


def _collect_wikipedia_evidence(ext: ExternalData) -> list[Evidence]:
    """Analyze Wikipedia presence — richer than the MusicBrainz URL check.

    Provides article length, summary extract, page views, and categories.
    """
    evidence: list[Evidence] = []

    if not ext.wikipedia_found:
        # Don't emit a red flag — MusicBrainz already covers the binary check.
        # This collector only fires when the dedicated Wikipedia client found data.
        return evidence

    # Article length — longer articles indicate more notability
    # Display as approximate word count (bytes / 6) per spec
    length = ext.wikipedia_length
    words = max(1, length // 6) if length > 0 else 0
    views = ext.wikipedia_monthly_views
    extract = ext.wikipedia_extract

    if length >= 20_000 and views >= 10_000:
        evidence.append(Evidence(
            finding=f"Substantial Wikipedia article (~{words:,} words, {views:,} monthly views)",
            source="Wikipedia",
            evidence_type="green_flag",
            strength="strong",
            detail=f"Wikipedia article \"{ext.wikipedia_title}\" is ~{words:,} words with "
                   f"{views:,} monthly page views. Large, actively-viewed articles indicate "
                   f"significant public notability. Summary: \"{extract[:200]}{'...' if len(extract) > 200 else ''}\"",
            tags=["wikipedia"],
        ))
    elif length >= 5_000 or views >= 1_000:
        evidence.append(Evidence(
            finding=f"Wikipedia article (~{words:,} words, {views:,} monthly views)",
            source="Wikipedia",
            evidence_type="green_flag",
            strength="strong",
            detail=f"Wikipedia article \"{ext.wikipedia_title}\" with {views:,} monthly views. "
                   f"Wikipedia's notability requirements make this strong proof of legitimacy. "
                   f"Summary: \"{extract[:150]}{'...' if len(extract) > 150 else ''}\"",
            tags=["wikipedia"],
        ))
    elif length > 0:
        evidence.append(Evidence(
            finding=f"Wikipedia stub article (~{words:,} words)",
            source="Wikipedia",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Short Wikipedia article \"{ext.wikipedia_title}\" (~{words:,} words). "
                   f"Even stub articles require notability per Wikipedia guidelines.",
            tags=["wikipedia"],
        ))

    # High page views as an independent engagement signal
    if views >= 50_000:
        evidence.append(Evidence(
            finding=f"Very high Wikipedia traffic ({views:,} monthly views)",
            source="Wikipedia",
            evidence_type="green_flag",
            strength="strong",
            detail=f"Extremely high monthly page views indicate massive public interest.",
            tags=["genuine_fans", "wikipedia"],
        ))

    # Wikidata description
    if ext.wikipedia_description:
        desc = ext.wikipedia_description.lower()
        music_terms = ["musician", "singer", "band", "rapper", "composer",
                       "songwriter", "dj", "group", "artist", "producer"]
        if any(term in desc for term in music_terms):
            evidence.append(Evidence(
                finding=f"Wikidata: \"{ext.wikipedia_description}\"",
                source="Wikipedia",
                evidence_type="green_flag",
                strength="weak",
                detail=f"Wikidata classifies this entity as \"{ext.wikipedia_description}\".",
                tags=["verified_identity"],
            ))

    return evidence


def _collect_songkick_evidence(ext: ExternalData) -> list[Evidence]:
    """Analyze Songkick concert and touring history."""
    evidence: list[Evidence] = []

    if not ext.songkick_found:
        return evidence

    total = ext.songkick_total_past_events

    # Concert history
    if total >= 100:
        evidence.append(Evidence(
            finding=f"{total:,} past events on Songkick",
            source="Songkick",
            evidence_type="green_flag",
            strength="strong",
            detail=f"Songkick records {total:,} past events. "
                   f"Extensive concert history is very strong proof of a real artist.",
            tags=["live_performance"],
        ))
    elif total >= 20:
        evidence.append(Evidence(
            finding=f"{total:,} past events on Songkick",
            source="Songkick",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Songkick records {total:,} past events, indicating real touring activity.",
            tags=["live_performance"],
        ))
    elif total >= 1:
        evidence.append(Evidence(
            finding=f"{total} past event(s) on Songkick",
            source="Songkick",
            evidence_type="green_flag",
            strength="weak",
            detail=f"Songkick has {total} recorded event(s). Some live activity detected.",
            tags=["live_performance"],
        ))

    # Currently on tour
    if ext.songkick_on_tour:
        evidence.append(Evidence(
            finding="Currently on tour (Songkick)",
            source="Songkick",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Artist is currently listed as on tour on Songkick "
                   f"with {ext.songkick_total_upcoming_events} upcoming event(s).",
            tags=["live_performance"],
        ))

    # Geographic spread
    countries = ext.songkick_venue_countries
    cities = ext.songkick_venue_cities
    if len(countries) >= 5:
        evidence.append(Evidence(
            finding=f"Songkick events in {len(countries)} countries",
            source="Songkick",
            evidence_type="green_flag",
            strength="strong",
            detail=f"International touring: {', '.join(countries[:8])}.",
            tags=["live_performance"],
        ))
    elif len(countries) >= 2:
        evidence.append(Evidence(
            finding=f"Songkick events in {len(countries)} countries",
            source="Songkick",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Touring in: {', '.join(countries[:5])}.",
            tags=["live_performance"],
        ))
    elif len(cities) >= 3:
        evidence.append(Evidence(
            finding=f"Songkick events in {len(cities)} cities",
            source="Songkick",
            evidence_type="green_flag",
            strength="weak",
            detail=f"Events in: {', '.join(cities[:5])}.",
            tags=["live_performance"],
        ))

    # Festival appearances
    festival_count = ext.songkick_event_types.count("Festival")
    if festival_count >= 3:
        evidence.append(Evidence(
            finding=f"{festival_count} festival appearances (Songkick)",
            source="Songkick",
            evidence_type="green_flag",
            strength="moderate",
            detail="Festival bookings indicate industry recognition and real performance history.",
            tags=["live_performance"],
        ))

    return evidence


def _collect_deezer_ai_evidence(ext: ExternalData) -> list[Evidence]:
    """Analyze Deezer AI content tag results (Priority 2)."""
    evidence: list[Evidence] = []

    if not ext.deezer_ai_checked:
        return evidence

    if ext.deezer_ai_tagged_albums:
        albums_str = ", ".join(ext.deezer_ai_tagged_albums[:3])
        evidence.append(Evidence(
            finding=f"Deezer flagged {len(ext.deezer_ai_tagged_albums)} album(s) as AI-generated",
            source="Deezer AI Detection",
            evidence_type="red_flag",
            strength="strong",
            detail=f"Deezer's AI detection system (99.8% accuracy, patent-pending) has "
                   f"classified album(s) as AI-generated: {albums_str}. "
                   "Deezer processes 60K+ AI tracks daily and is the only platform that "
                   "actively tags AI content.",
            tags=["ai_generated_music"],
        ))
    else:
        evidence.append(Evidence(
            finding="No AI content tags detected on Deezer",
            source="Deezer AI Detection",
            evidence_type="green_flag",
            strength="weak",
            detail="Deezer's AI detection system did not flag any of this artist's albums.",
            tags=["deezer_ai_clear"],
        ))

    return evidence


def _collect_youtube_evidence(ext: ExternalData, artist_monthly_listeners: int = 0) -> list[Evidence]:
    """Analyze YouTube cross-reference results (Priority 4)."""
    evidence: list[Evidence] = []

    if not ext.youtube_checked:
        return evidence

    if ext.youtube_channel_found:
        subs = ext.youtube_subscriber_count
        if subs >= 100_000:
            evidence.append(Evidence(
                finding=f"YouTube channel with {subs:,} subscribers",
                source="YouTube",
                evidence_type="green_flag",
                strength="strong",
                detail=f"YouTube channel found with {subs:,} subscribers, "
                       f"{ext.youtube_video_count} videos, "
                       f"{ext.youtube_view_count:,} total views.",
                tags=["youtube_presence", "genuine_fans"],
            ))
        elif subs >= 10_000:
            evidence.append(Evidence(
                finding=f"YouTube channel with {subs:,} subscribers",
                source="YouTube",
                evidence_type="green_flag",
                strength="moderate",
                detail=f"YouTube channel found with {subs:,} subscribers "
                       f"and {ext.youtube_video_count} videos.",
                tags=["youtube_presence"],
            ))
        elif subs >= 100:
            evidence.append(Evidence(
                finding=f"YouTube channel with {subs:,} subscribers",
                source="YouTube",
                evidence_type="green_flag",
                strength="weak",
                detail=f"YouTube channel found with {subs:,} subscribers.",
                tags=["youtube_presence"],
            ))
        else:
            evidence.append(Evidence(
                finding=f"YouTube channel with only {subs} subscribers",
                source="YouTube",
                evidence_type="red_flag",
                strength="weak",
                detail="YouTube channel exists but has very few subscribers, "
                       "suggesting auto-generated or placeholder channel.",
                tags=["youtube_presence"],
            ))

        # Spotify/YouTube disparity check
        if artist_monthly_listeners >= 500_000 and ext.youtube_view_count < 10_000:
            evidence.append(Evidence(
                finding=f"Massive Spotify/YouTube disparity: {artist_monthly_listeners:,} "
                        f"listeners but only {ext.youtube_view_count:,} YouTube views",
                source="YouTube",
                evidence_type="red_flag",
                strength="strong",
                detail=f"Artist has {artist_monthly_listeners:,} Spotify monthly listeners but "
                       f"only {ext.youtube_view_count:,} total YouTube views. Real artists with "
                       "this level of Spotify audience always have proportional YouTube presence.",
                tags=["youtube_disparity"],
            ))

    elif not ext.youtube_channel_found and ext.youtube_music_videos_found == 0:
        if artist_monthly_listeners >= 100_000:
            evidence.append(Evidence(
                finding="No YouTube presence despite large Spotify audience",
                source="YouTube",
                evidence_type="red_flag",
                strength="moderate",
                detail=f"No YouTube channel or music videos found for an artist with "
                       f"{artist_monthly_listeners:,} Spotify monthly listeners. "
                       "This is very unusual for a legitimate artist.",
                tags=["no_youtube"],
            ))
        else:
            evidence.append(Evidence(
                finding="No YouTube channel or music videos found",
                source="YouTube",
                evidence_type="red_flag",
                strength="weak",
                detail="No YouTube presence detected. Most real artists have at least "
                       "some YouTube content.",
                tags=["no_youtube"],
            ))

    return evidence


def _collect_pro_registry_evidence(ext: ExternalData) -> list[Evidence]:
    """Analyze ASCAP/BMI performing rights registration (Priority 3)."""
    evidence: list[Evidence] = []

    if not ext.pro_checked:
        return evidence

    if ext.pro_songwriter_registered:
        total_works = ext.pro_works_count
        pro_name = "BMI" if ext.pro_found_bmi else "ASCAP"
        if ext.pro_found_bmi and ext.pro_found_ascap:
            pro_name = "BMI and ASCAP"

        # C.3: registered as songwriter → +30 Strong
        sw_pct = ext.pro_songwriter_share_pct
        share_str = f", songwriter holds {sw_pct:.0f}% share" if sw_pct >= 0 else ""
        evidence.append(Evidence(
            finding=f"Registered with {pro_name}: {total_works} works{share_str}",
            source="PRO Registry",
            evidence_type="green_flag",
            strength="strong",
            detail=f"Artist found as registered songwriter with {pro_name}, "
                   f"{total_works} works registered. "
                   "Professional songwriters collecting US royalties are registered with PROs. "
                   "This is one of the strongest authenticity indicators available.",
            tags=["pro_registered"],
        ))

        # PFC publisher check
        if ext.pro_pfc_publisher_match:
            evidence.append(Evidence(
                finding=f"PRO publisher matches known PFC entity",
                source="PRO Registry",
                evidence_type="red_flag",
                strength="strong",
                detail=f"Works registered under publisher(s) matching known PFC entities: "
                       f"{', '.join(ext.pro_publishers[:3])}.",
                tags=["pfc_publisher"],
            ))

        # Zero songwriter share
        if ext.pro_zero_songwriter_share:
            evidence.append(Evidence(
                finding="0% songwriter share — 100% publisher ownership",
                source="PRO Registry",
                evidence_type="red_flag",
                strength="moderate",
                detail="Registered works show 0% songwriter share with 100% publisher ownership. "
                       "This is the structural signature of a work-for-hire / PFC arrangement.",
                tags=["no_songwriter_share"],
            ))
        # Normal songwriter/publisher split
        elif ext.pro_songwriter_share_pct >= 30:
            sw = ext.pro_songwriter_share_pct
            pub = ext.pro_publisher_share_pct
            split_str = f"{sw:.0f}%/{pub:.0f}%" if pub >= 0 else f"{sw:.0f}% songwriter"
            evidence.append(Evidence(
                finding=f"Normal songwriter/publisher ownership split ({split_str})",
                source="PRO Registry",
                evidence_type="green_flag",
                strength="weak",
                detail="Standard splits are typically 50/50 between writer and publisher. "
                       "This indicates a normal publishing arrangement.",
                tags=["normal_pro_split"],
            ))
    else:
        # Non-US fallback: IPI code from MusicBrainz is equivalent to global PRO registration
        if ext.musicbrainz_ipis:
            evidence.append(Evidence(
                finding=f"Not in BMI/ASCAP but has IPI code (non-US PRO registration)",
                source="PRO Registry / MusicBrainz",
                evidence_type="green_flag",
                strength="weak",
                detail="Artist not found in US PRO databases (BMI/ASCAP) but has an IPI code "
                       "in MusicBrainz, confirming registration with a performing rights "
                       "organization somewhere globally.",
                tags=["pro_registered"],
            ))
        else:
            # C.3: not found → -5 Weak (many legit non-US/indie artists aren't registered)
            evidence.append(Evidence(
                finding="Not found in BMI or ASCAP databases",
                source="PRO Registry",
                evidence_type="red_flag",
                strength="weak",
                detail="No works registered with BMI or ASCAP. Many legitimate non-US "
                       "and indie artists are not registered with US PROs, so this is "
                       "only a weak signal.",
                tags=["no_pro_registration"],
            ))

    return evidence


def _collect_bandcamp_evidence(ext: ExternalData) -> list[Evidence]:
    """Check for Bandcamp presence from MusicBrainz URLs (Priority 5)."""
    evidence: list[Evidence] = []

    if ext.musicbrainz_bandcamp_url:
        evidence.append(Evidence(
            finding="Artist has Bandcamp page",
            source="MusicBrainz",
            evidence_type="green_flag",
            strength="strong",
            detail=f"Bandcamp presence: {ext.musicbrainz_bandcamp_url}. "
                   "Bandcamp is a direct-to-fan sales platform. PFC/ghost artists "
                   "never maintain Bandcamp pages.",
            tags=["bandcamp_presence"],
        ))

    if ext.musicbrainz_official_website:
        evidence.append(Evidence(
            finding="Artist has official website",
            source="MusicBrainz",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Official website: {ext.musicbrainz_official_website}",
            tags=["social_media"],
        ))

    return evidence


def _collect_isrc_evidence(ext: ExternalData) -> list[Evidence]:
    """Analyze ISRC registrant data (Priority 7)."""
    evidence: list[Evidence] = []

    if not ext.isrcs:
        return evidence

    registrants = ext.isrc_registrants

    if len(registrants) == 1:
        evidence.append(Evidence(
            finding=f"All tracks share ISRC registrant: {registrants[0]}",
            source="ISRC Analysis",
            evidence_type="neutral",
            strength="weak",
            detail=f"All {len(ext.isrcs)} tracks use the same ISRC registrant code "
                   f"'{registrants[0]}'. Normal for single-label artists.",
            tags=[],
        ))
    elif len(registrants) >= 3:
        evidence.append(Evidence(
            finding=f"Tracks span {len(registrants)} ISRC registrants",
            source="ISRC Analysis",
            evidence_type="green_flag",
            strength="weak",
            detail=f"Tracks use {len(registrants)} different ISRC registrant codes: "
                   f"{', '.join(registrants)}. Multiple registrants suggest releases "
                   "through different labels/distributors over time.",
            tags=[],
        ))

    return evidence


def _collect_press_coverage_evidence(ext: ExternalData, artist_monthly_listeners: int = 0) -> list[Evidence]:
    """Analyze press coverage search results (Priority 6)."""
    evidence: list[Evidence] = []

    if not ext.press_checked:
        return evidence

    pubs = ext.press_publications_found
    hits = ext.press_total_hits

    if len(pubs) >= 2:
        evidence.append(Evidence(
            finding=f"Press coverage found in {len(pubs)} publications",
            source="Press Coverage",
            evidence_type="green_flag",
            strength="strong",
            detail=f"Found coverage in: {', '.join(pubs[:5])}. "
                   f"Total press hits: {hits}.",
            tags=["press_coverage"],
        ))
    elif len(pubs) == 1:
        evidence.append(Evidence(
            finding=f"Press coverage found in {pubs[0]}",
            source="Press Coverage",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Found coverage in {pubs[0]}. Total hits: {hits}.",
            tags=["press_coverage"],
        ))
    elif hits > 0:
        evidence.append(Evidence(
            finding=f"{hits} press mention(s) but not from major publications",
            source="Press Coverage",
            evidence_type="green_flag",
            strength="weak",
            detail=f"Found {hits} mention(s) but none from recognized music publications.",
            tags=["press_coverage"],
        ))
    elif artist_monthly_listeners >= 100_000:
        evidence.append(Evidence(
            finding="No press coverage despite significant streaming audience",
            source="Press Coverage",
            evidence_type="red_flag",
            strength="weak",
            detail=f"No press coverage found for an artist with {artist_monthly_listeners:,} "
                   "monthly listeners. Legitimate artists at this level typically have "
                   "some coverage from music publications.",
            tags=["no_press_coverage"],
        ))

    return evidence


def _collect_cowriter_network_evidence(ext: ExternalData, entity_db: "EntityDB | None" = None, artist_name: str = "") -> list[Evidence]:
    """Check cowriter overlap with flagged artists in entity DB (Priority 1 enhancement)."""
    evidence: list[Evidence] = []

    if not entity_db or not artist_name:
        return evidence

    try:
        overlaps = entity_db.get_cowriter_overlap(artist_name)
    except Exception:
        return evidence

    if not overlaps:
        return evidence

    total_flagged = sum(len(o["flagged_artists"]) for o in overlaps)

    if total_flagged >= 3:
        sw_names = [o["songwriter"] for o in overlaps if o["songwriter"]]
        flagged_names = []
        for o in overlaps:
            flagged_names.extend(a["name"] for a in o["flagged_artists"][:2])
        evidence.append(Evidence(
            finding=f"Shares producers with {total_flagged} flagged artists",
            source="Entity DB",
            evidence_type="red_flag",
            strength="strong",
            detail=f"Shared songwriter(s)/producer(s): {', '.join(sw_names[:3])}. "
                   f"Connected to flagged artists: {', '.join(flagged_names[:5])}.",
            tags=["cowriter_network"],
        ))
    elif total_flagged >= 1:
        sw_names = [o["songwriter"] for o in overlaps if o["songwriter"]]
        evidence.append(Evidence(
            finding=f"Shares producer(s) with {total_flagged} flagged artist(s)",
            source="Entity DB",
            evidence_type="red_flag",
            strength="moderate",
            detail=f"Shared songwriter(s)/producer(s): {', '.join(sw_names[:3])}.",
            tags=["cowriter_network"],
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
) -> tuple[Verdict, str, str]:
    """Walk the decision tree and return (verdict, confidence, matched_rule)."""

    strong_reds = [e for e in red_flags if e.strength == "strong"]
    moderate_reds = [e for e in red_flags if e.strength == "moderate"]
    strong_greens = [e for e in green_flags if e.strength == "strong"]
    moderate_greens = [e for e in green_flags if e.strength == "moderate"]

    # Helper: check if any red flag carries a given tag
    def _any_red_tag(tag: str) -> bool:
        return any(tag in e.tags for e in red_flags)

    def _any_green_tag(tag: str) -> bool:
        return any(tag in e.tags for e in green_flags)

    # Rule 1: Known AI artist name → Likely Artificial
    if _any_red_tag("known_ai_artist"):
        decision_path.append("Name matches known AI artist blocklist → Likely Artificial")
        return Verdict.LIKELY_ARTIFICIAL, "high", "Rule 1: Known AI Artist"

    # Rule 1.5: Deezer AI Content Flag → Likely Artificial
    if _any_red_tag("ai_generated_music"):
        decision_path.append("Deezer AI detection flagged content → Likely Artificial")
        return Verdict.LIKELY_ARTIFICIAL, "high", "Rule 1.5: Deezer AI Content Flag"

    # Rule 2: PFC label + content farm patterns → Likely Artificial
    has_pfc_label = _any_red_tag("pfc_label")
    has_farm_pattern = _any_red_tag("content_farm") or _any_red_tag("stream_farm")
    if has_pfc_label and has_farm_pattern:
        decision_path.append("PFC distributor + content farm pattern → Likely Artificial")
        return Verdict.LIKELY_ARTIFICIAL, "high", "Rule 2: PFC Label + Content Farm Pattern"

    # Rule 3: Multiple strong red flags with no green → Likely Artificial
    if len(strong_reds) >= 3 and not strong_greens and not moderate_greens:
        decision_path.append(f"{len(strong_reds)} strong red flags, no green flags → Likely Artificial")
        return Verdict.LIKELY_ARTIFICIAL, "medium", "Rule 3: Overwhelming Red, No Green"

    # Compute weighted totals for rules 4+
    weak_greens = [e for e in green_flags if e.strength == "weak"]
    weak_reds = [e for e in red_flags if e.strength == "weak"]
    total_green_strength = len(strong_greens) * 3 + len(moderate_greens) * 2 + len(weak_greens)
    total_red_strength = len(strong_reds) * 3 + len(moderate_reds) * 2 + len(weak_reds)

    # Rule 4: Strong green flags dominate → high confidence authentic
    # Guard: total_red < 4 prevents verification when moderate reds pile up
    if len(strong_greens) >= 2 and not strong_reds and total_red_strength < 4:
        decision_path.append(
            f"Multiple strong legitimacy signals ({len(strong_greens)}) "
            f"with minimal concerns → Verified Artist"
        )
        return Verdict.VERIFIED_ARTIST, "high", "Rule 4: Strong Greens Dominate"

    # Rule 5: Multi-platform + genuine fans + no strong reds → Verified
    has_genuine_fans_strong = any(
        "genuine_fans" in e.tags and e.strength == "strong"
        for e in green_flags
    )
    if presence.count() >= 3 and has_genuine_fans_strong and not strong_reds:
        decision_path.append(
            f"Present on {presence.count()} platforms with strong fan engagement, "
            f"no major red flags → Verified Artist"
        )
        return Verdict.VERIFIED_ARTIST, "high", "Rule 5: Multi-Platform + Genuine Fans"

    # Rule 6: Green strongly outweighs red
    if total_green_strength >= total_red_strength * 2 and total_green_strength >= 4:
        decision_path.append(
            f"Legitimacy evidence substantially outweighs concerns → Likely Authentic"
        )
        return Verdict.LIKELY_AUTHENTIC, "medium", "Rule 6: Green Strongly Outweighs Red"

    # Rule 7: Red flags dominate
    if total_red_strength >= total_green_strength * 2 and total_red_strength >= 4:
        decision_path.append(
            f"Suspicious indicators substantially outweigh legitimacy evidence → Suspicious"
        )
        return Verdict.SUSPICIOUS, "medium", "Rule 7: Red Strongly Outweighs Green"

    # Rule 8: PFC label alone → Suspicious (medium confidence —
    # being on a confirmed PFC label is not a weak signal)
    if has_pfc_label:
        decision_path.append("PFC distributor match (without other strong signals) → Suspicious")
        return Verdict.SUSPICIOUS, "medium", "Rule 8: PFC Label Alone"

    # Rule 9: More green than red → Likely Authentic
    if total_green_strength > total_red_strength:
        decision_path.append(
            "Slightly more legitimacy evidence than concerns → Likely Authentic"
        )
        return Verdict.LIKELY_AUTHENTIC, "low", "Rule 9: Green > Red"

    # Rule 10: More red than green → Suspicious
    if total_red_strength > total_green_strength:
        decision_path.append(
            "Slightly more concerns than legitimacy evidence → Suspicious"
        )
        return Verdict.SUSPICIOUS, "low", "Rule 10: Red > Green"

    # Default: Distinguish "not enough data" from "conflicting data"
    total_flags = len(red_flags) + len(green_flags)
    if total_flags < 5:
        decision_path.append(f"Only {total_flags} flags collected — insufficient data to judge")
        return Verdict.INSUFFICIENT_DATA, "low", "Default: Insufficient Data"
    elif total_green_strength >= 4 and total_red_strength >= 4:
        decision_path.append(f"Green ({total_green_strength}) and Red ({total_red_strength}) "
                             "both substantial — conflicting signals")
        return Verdict.CONFLICTING_SIGNALS, "low", "Default: Conflicting Signals"
    else:
        decision_path.append("Mixed or insufficient evidence → Inconclusive")
        return Verdict.INCONCLUSIVE, "low", "Default: Inconclusive"


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
                tags=["entity_confirmed_bad"],
            ))
        elif status == "suspected":
            evidence.append(Evidence(
                finding="Artist previously flagged as suspected in entity database",
                source="Entity DB",
                evidence_type="red_flag",
                strength="moderate",
                detail=f"'{artist.name}' was flagged as suspected in a prior scan. "
                       f"Notes: {db_artist.get('notes', 'none')}",
                tags=["entity_suspected"],
            ))
        elif status == "cleared":
            evidence.append(Evidence(
                finding="Artist previously cleared in entity database",
                source="Entity DB",
                evidence_type="green_flag",
                strength="moderate",
                detail=f"'{artist.name}' was manually cleared as legitimate. "
                       f"Notes: {db_artist.get('notes', 'none')}",
                tags=["entity_cleared"],
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
            tags=["entity_bad_label"],
        ))
    if suspected_labels:
        evidence.append(Evidence(
            finding=f"Label(s) flagged as suspected: {', '.join(suspected_labels)}",
            source="Entity DB",
            evidence_type="red_flag",
            strength="moderate",
            detail=f"Artist releases through label(s) that are suspected in the "
                   f"entity intelligence database.",
            tags=["entity_bad_label"],
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
            tags=["entity_bad_songwriter"],
        ))
    if suspected_writers:
        evidence.append(Evidence(
            finding=f"Credits include suspected songwriter(s): {', '.join(suspected_writers)}",
            source="Entity DB",
            evidence_type="red_flag",
            strength="moderate",
            detail=f"Track credits include songwriter(s)/producer(s) flagged as "
                   f"suspected in the entity intelligence database.",
            tags=["entity_bad_songwriter"],
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
                tags=["entity_bad_network"],
            ))
        elif len(bad_connections) >= 1:
            names = [cw["name"] for cw in bad_connections]
            evidence.append(Evidence(
                finding=f"Connected to {len(bad_connections)} flagged artist(s) via shared producers",
                source="Entity DB",
                evidence_type="red_flag",
                strength="weak",
                detail=f"Shared songwriter/producer connections to: {', '.join(names)}.",
                tags=["entity_bad_network"],
            ))

    return evidence


# ---------------------------------------------------------------------------
# Fast Mode — skip external APIs for obviously legitimate artists
# ---------------------------------------------------------------------------

def is_obviously_legitimate(artist: ArtistInfo) -> bool:
    """Check if an artist is obviously legitimate from Spotify/Deezer data alone.

    For large playlists, this allows skipping external API calls for artists
    that clearly don't need them.  All conditions must be met:
      - followers >= 500,000
      - has Wikipedia link in external_urls
      - has 3+ genres assigned
      - has 5+ albums
      - not on any blocklist
    """
    if artist.followers < 500_000:
        return False
    urls = artist.external_urls
    has_wiki = any("wikipedia" in v.lower() for v in urls.values()) if urls else False
    if not has_wiki:
        return False
    if len(artist.genres) < 3:
        return False
    if artist.album_count < 5:
        return False
    # Check blocklists
    name_lower = artist.name.lower()
    if name_lower in known_ai_artists():
        return False
    labels_lower = {l.lower() for l in artist.labels}
    if labels_lower & pfc_distributors():
        return False
    return True


def fast_mode_evaluation(artist: ArtistInfo) -> ArtistEvaluation:
    """Return a pre-built VERIFIED ARTIST evaluation for obviously legitimate artists."""
    presence = PlatformPresence(deezer=True)
    if artist.deezer_fans > 0:
        presence.deezer = True
        presence.deezer_fans = artist.deezer_fans
    green_flags = [
        Evidence(
            finding=f"{artist.followers:,} followers, {artist.album_count} albums, "
                    f"{len(artist.genres)} genres, Wikipedia link",
            source="Fast Mode",
            evidence_type="green_flag",
            strength="strong",
            detail="Artist meets all Fast Mode criteria for obvious legitimacy: "
                   "500K+ followers, Wikipedia link, 3+ genres, 5+ albums, clean blocklists.",
            tags=["genuine_fans", "multi_platform"],
        ),
    ]
    return ArtistEvaluation(
        artist_id=artist.artist_id,
        artist_name=artist.name,
        verdict=Verdict.VERIFIED_ARTIST,
        confidence="high",
        platform_presence=presence,
        red_flags=[],
        green_flags=green_flags,
        neutral_notes=[],
        decision_path=["Fast Mode: all legitimacy criteria met → Verified Artist"],
        labels=artist.labels,
        contributors=artist.contributors,
    )


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
                  (Genius, Discogs, Setlist.fm, MusicBrainz)
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
    if ext.wikipedia_found:
        presence.wikipedia = True
    if ext.songkick_found:
        presence.songkick = True

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
            tags=["multi_platform"],
        ))
    elif platforms_found >= 3:
        platform_ev.append(Evidence(
            finding=f"Found on {platforms_found} platforms",
            source="Cross-platform",
            evidence_type="green_flag",
            strength="strong",
            detail=f"Artist exists on: {', '.join(presence.names())}. "
                   "Artists present on multiple platforms are very likely real.",
            tags=["multi_platform"],
        ))
    elif platforms_found >= 2:
        platform_ev.append(Evidence(
            finding=f"Found on {platforms_found} platforms",
            source="Cross-platform",
            evidence_type="green_flag",
            strength="moderate",
            detail=f"Found on: {', '.join(presence.names())}.",
            tags=["multi_platform"],
        ))
    elif platforms_found <= 1:
        platform_ev.append(Evidence(
            finding="Only found on 1 platform",
            source="Cross-platform",
            evidence_type="red_flag",
            strength="weak",
            detail="Artist only verified on a single platform. "
                   "Could be new or could be a fabricated artist.",
            tags=["single_platform"],
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
    all_evidence.extend(_collect_genre_evidence(artist, ext))
    all_evidence.extend(_collect_track_rank_evidence(artist))

    # Collect evidence from external APIs (Standard tier)
    all_evidence.extend(_collect_genius_evidence(ext))
    all_evidence.extend(_collect_discogs_evidence(ext))
    all_evidence.extend(_collect_live_show_evidence(ext))
    all_evidence.extend(_collect_musicbrainz_evidence(ext))
    all_evidence.extend(_collect_social_media_evidence(ext))
    all_evidence.extend(_collect_identity_evidence(ext))
    all_evidence.extend(_collect_lastfm_evidence(ext))
    # Touring geography removed per alignment doc — live_show evidence is sufficient
    all_evidence.extend(_collect_wikipedia_evidence(ext))
    all_evidence.extend(_collect_songkick_evidence(ext))

    # New evidence sources (Priorities 2-7)
    all_evidence.extend(_collect_deezer_ai_evidence(ext))
    all_evidence.extend(_collect_youtube_evidence(ext, artist.monthly_listeners))
    all_evidence.extend(_collect_pro_registry_evidence(ext))
    all_evidence.extend(_collect_bandcamp_evidence(ext))
    all_evidence.extend(_collect_isrc_evidence(ext))

    # Pre-seeded evidence from known entity pre-check (Priority 1)
    for pre in ext.pre_seeded_evidence:
        all_evidence.append(Evidence(
            finding=pre.get("finding", ""),
            source=pre.get("source", "Blocklist"),
            evidence_type=pre.get("evidence_type", "red_flag"),
            strength=pre.get("strength", "moderate"),
            detail=pre.get("detail", ""),
            tags=pre.get("tags", []),
        ))

    # Press coverage (Priority 6)
    all_evidence.extend(_collect_press_coverage_evidence(ext, artist.monthly_listeners))

    # Entity intelligence database (accumulated from prior scans)
    if entity_db:
        all_evidence.extend(_collect_entity_db_evidence(artist, entity_db))
        all_evidence.extend(_collect_cowriter_network_evidence(ext, entity_db, artist.name))

    # Evidence deduplication: if two items share the same (source, finding) key,
    # keep only the stronger one. Prevents double-counting the same signal.
    seen: dict[tuple[str, str], Evidence] = {}
    strength_order = {"strong": 3, "moderate": 2, "weak": 1}
    for ev_item in all_evidence:
        key = (ev_item.source, ev_item.finding)
        existing = seen.get(key)
        if existing is None:
            seen[key] = ev_item
        elif strength_order.get(ev_item.strength, 0) > strength_order.get(existing.strength, 0):
            seen[key] = ev_item
    all_evidence = list(seen.values())

    # Separate by type
    red_flags = [e for e in all_evidence if e.evidence_type == "red_flag"]
    green_flags = [e for e in all_evidence if e.evidence_type == "green_flag"]
    neutral_notes = [e for e in all_evidence if e.evidence_type == "neutral"]

    # Run decision tree
    verdict, confidence, matched_rule = _decide_verdict(red_flags, green_flags, presence, decision_path)

    # Fix 4: Sanity check — if Deezer shows real fans but all category scores
    # are zero due to API failures, override to Inconclusive instead of Suspicious.
    # This prevents false accusations when data collection fails.
    if verdict in (Verdict.SUSPICIOUS, Verdict.LIKELY_ARTIFICIAL):
        cat_scores = compute_category_scores(
            ArtistEvaluation(
                artist_id=artist.artist_id, artist_name=artist.name,
                verdict=verdict, confidence=confidence,
                platform_presence=presence,
                red_flags=red_flags, green_flags=green_flags,
                neutral_notes=neutral_notes, decision_path=[],
                external_data=ext,
            )
        )
        blocklist_clean = cat_scores.get("Blocklist Status", 0) == 100
        all_zeros = all(
            cat_scores.get(k, 0) == 0
            for k in ["Platform Presence", "Fan Engagement", "IRL Presence", "Industry Signals"]
        )
        deezer_fans = artist.deezer_fans or 0
        if deezer_fans >= 1000 and all_zeros and blocklist_clean:
            num_errors = len(ext.api_errors)
            verdict = Verdict.INCONCLUSIVE
            confidence = "low"
            decision_path.append(
                f"Sanity check override: {deezer_fans:,} Deezer fans but {num_errors} "
                f"API errors caused all-zero scores → Inconclusive (data collection failure)"
            )

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
        matched_rule=matched_rule,
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
    verdict, confidence, matched_rule = _decide_verdict(
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
        matched_rule=matched_rule,
    )
