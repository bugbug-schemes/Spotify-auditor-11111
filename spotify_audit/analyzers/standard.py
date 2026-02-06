"""
Standard Scan tier — external database lookups.

Runs after Quick Scan for artists that score above the escalation threshold.
Checks external sources that require free API keys:
  - Genius: songwriter/producer credits (ghost artists have none)
  - Discogs: physical releases (ghost artists never press vinyl/CDs)
  - Setlist.fm + Bandsintown: live show history (ghost artists don't tour)
  - MusicBrainz: metadata quality, label info, distributor blocklist matching
  - Deezer: cross-validation of fan counts and catalog

Each signal produces a raw 0-100 suspicion sub-score.
The final Standard score blends the Quick score (40%) with new signals (60%).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from spotify_audit.config import (
    AuditConfig,
    StandardWeights,
    pfc_distributors,
)
from spotify_audit.analyzers.quick import QuickScanResult, SignalResult
from spotify_audit.genius_client import GeniusClient, GeniusArtist
from spotify_audit.discogs_client import DiscogsClient, DiscogsArtist
from spotify_audit.setlistfm_client import SetlistFmClient, SetlistArtist
from spotify_audit.bandsintown_client import BandsintownClient, BandsintownArtist
from spotify_audit.musicbrainz_client import MusicBrainzClient, MBArtist
from spotify_audit.deezer_client import DeezerClient, DeezerArtist

logger = logging.getLogger(__name__)


@dataclass
class StandardScanResult:
    artist_id: str
    artist_name: str
    score: int                   # 0-100 composite
    signals: list[SignalResult] = field(default_factory=list)
    tier: str = "standard"


# ---------------------------------------------------------------------------
# Individual signal scorers (each returns 0-100, higher = more suspicious)
# ---------------------------------------------------------------------------

def _score_genius_credits(artist_name: str, genius: GeniusClient) -> tuple[float, str]:
    """Check Genius for songwriter/producer credits.
    Real artists have writing credits; ghost/AI artists have zero."""
    if not genius.enabled:
        return 50.0, "Genius API not configured (skipped)"

    try:
        ga = genius.search_artist(artist_name)
    except Exception as exc:
        logger.debug("Genius search failed for '%s': %s", artist_name, exc)
        return 50.0, f"Genius lookup failed: {exc}"

    if ga is None:
        return 75.0, "Not found on Genius"

    try:
        ga = genius.enrich(ga)
    except Exception as exc:
        logger.debug("Genius enrich failed for '%s': %s", artist_name, exc)
        return 60.0, f"Found on Genius (id={ga.genius_id}) but enrichment failed"

    if ga.song_count == 0:
        return 80.0, "Found on Genius but 0 songs"
    if ga.song_count <= 3:
        return 50.0, f"Only {ga.song_count} songs on Genius"
    if ga.song_count <= 10:
        return 25.0, f"{ga.song_count} songs on Genius"

    return 5.0, f"{ga.song_count} songs on Genius with credits"


def _score_discogs_physical(artist_name: str, discogs: DiscogsClient) -> tuple[float, str]:
    """Check Discogs for physical releases.
    Ghost/AI artists almost never have vinyl, CD, or cassette releases."""
    try:
        da = discogs.search_artist(artist_name)
    except Exception as exc:
        logger.debug("Discogs search failed for '%s': %s", artist_name, exc)
        return 50.0, f"Discogs lookup failed: {exc}"

    if da is None:
        return 70.0, "Not found on Discogs"

    try:
        da = discogs.enrich(da)
    except Exception as exc:
        logger.debug("Discogs enrich failed for '%s': %s", artist_name, exc)
        return 55.0, f"Found on Discogs (id={da.discogs_id}) but enrichment failed"

    if da.total_releases == 0:
        return 75.0, "Found on Discogs but 0 releases"

    if da.physical_releases == 0:
        if da.digital_only_releases > 0:
            return 55.0, f"Digital-only: {da.digital_only_releases} releases, no physical"
        return 65.0, "No physical releases found"

    if da.physical_releases >= 5:
        return 0.0, (
            f"{da.physical_releases} physical releases "
            f"({', '.join(da.formats[:5])})"
        )
    if da.physical_releases >= 2:
        return 10.0, (
            f"{da.physical_releases} physical releases "
            f"({', '.join(da.formats[:5])})"
        )

    return 25.0, f"{da.physical_releases} physical release(s)"


def _score_live_show_history(
    artist_name: str,
    setlistfm: SetlistFmClient,
    bandsintown: BandsintownClient,
) -> tuple[float, str]:
    """Check concert history from setlist.fm and Bandsintown.
    Ghost/AI artists have zero live performance history."""
    total_shows = 0
    details: list[str] = []

    # Setlist.fm
    if setlistfm.enabled:
        try:
            sa = setlistfm.search_artist(artist_name)
            if sa:
                sa = setlistfm.get_setlist_count(sa)
                total_shows += sa.total_setlists
                if sa.total_setlists > 0:
                    details.append(
                        f"setlist.fm: {sa.total_setlists} shows"
                        f" ({sa.first_show_date}–{sa.last_show_date})"
                        if sa.first_show_date
                        else f"setlist.fm: {sa.total_setlists} shows"
                    )
                else:
                    details.append("setlist.fm: 0 shows")
            else:
                details.append("setlist.fm: not found")
        except Exception as exc:
            logger.debug("Setlist.fm failed for '%s': %s", artist_name, exc)
            details.append(f"setlist.fm: error")
    else:
        details.append("setlist.fm: not configured")

    # Bandsintown
    if bandsintown.enabled:
        try:
            ba = bandsintown.get_artist(artist_name)
            if ba:
                ba = bandsintown.enrich(ba)
                total_shows += ba.past_events
                parts = []
                if ba.past_events > 0:
                    parts.append(f"{ba.past_events} past events")
                if ba.upcoming_events > 0:
                    parts.append(f"{ba.upcoming_events} upcoming")
                if ba.tracker_count > 0:
                    parts.append(f"{ba.tracker_count:,} trackers")
                details.append(
                    f"bandsintown: {', '.join(parts)}" if parts
                    else "bandsintown: 0 events"
                )
            else:
                details.append("bandsintown: not found")
        except Exception as exc:
            logger.debug("Bandsintown failed for '%s': %s", artist_name, exc)
            details.append("bandsintown: error")
    else:
        details.append("bandsintown: not configured")

    # Neither configured
    if not setlistfm.enabled and not bandsintown.enabled:
        return 50.0, "Live show APIs not configured (skipped)"

    detail = "; ".join(details)

    if total_shows == 0:
        return 80.0, f"No live shows found ({detail})"
    if total_shows <= 5:
        return 40.0, f"{total_shows} total shows ({detail})"
    if total_shows <= 20:
        return 15.0, f"{total_shows} total shows ({detail})"

    return 0.0, f"{total_shows} total shows ({detail})"


def _score_musicbrainz_presence(
    artist_name: str,
    mb_client: MusicBrainzClient,
) -> tuple[float, str]:
    """Check MusicBrainz for artist presence and metadata quality.
    Well-known artists have rich MusicBrainz profiles."""
    try:
        mb = mb_client.search_artist(artist_name)
    except Exception as exc:
        logger.debug("MusicBrainz search failed for '%s': %s", artist_name, exc)
        return 50.0, f"MusicBrainz lookup failed: {exc}"

    if mb is None or not mb.mbid:
        return 70.0, "Not found on MusicBrainz"

    score = 30.0
    notes: list[str] = [f"mbid={mb.mbid[:8]}..."]

    if mb.artist_type:
        score -= 10
        notes.append(f"type={mb.artist_type}")

    if mb.country:
        score -= 5
        notes.append(f"country={mb.country}")

    if mb.begin_date:
        score -= 10
        notes.append(f"active since {mb.begin_date}")

    if mb.disambiguation:
        score -= 5
        notes.append(f"disambig present")

    return max(0, score), "; ".join(notes)


def _score_label_blocklist(
    artist_name: str,
    mb_client: MusicBrainzClient,
    mb_artist: MBArtist | None = None,
) -> tuple[float, str]:
    """Check if the artist's labels/distributors match the PFC blocklist."""
    blocklist = [d.lower() for d in pfc_distributors()]
    if not blocklist:
        return 0.0, "No PFC distributor blocklist loaded"

    # Try to get labels from MusicBrainz
    labels: list[str] = []
    if mb_artist and mb_artist.mbid:
        try:
            mb_artist = mb_client.enrich(mb_artist)
            labels = mb_artist.labels
        except Exception as exc:
            logger.debug("MusicBrainz enrich failed: %s", exc)

    if not labels:
        return 30.0, "No label info available for blocklist check"

    matches = [l for l in labels if l.lower() in blocklist]
    if matches:
        return 90.0, f"PFC distributor match: {', '.join(matches)}"

    return 5.0, f"Labels ({', '.join(labels[:5])}) not on PFC blocklist"


def _score_deezer_cross_check(
    artist_name: str,
    deezer: DeezerClient,
    spotify_followers: int = 0,
) -> tuple[float, str]:
    """Cross-validate with Deezer presence and fan counts.
    Ghost artists often have no Deezer presence or dramatically different metrics."""
    try:
        da = deezer.search_artist(artist_name)
    except Exception as exc:
        logger.debug("Deezer search failed for '%s': %s", artist_name, exc)
        return 50.0, f"Deezer lookup failed: {exc}"

    if da is None:
        return 65.0, "Not found on Deezer"

    if da.name.lower().strip() != artist_name.lower().strip():
        return 55.0, f"Deezer name mismatch: '{da.name}' vs '{artist_name}'"

    if da.nb_fan == 0:
        return 60.0, "Found on Deezer but 0 fans"

    if da.nb_fan < 100:
        return 40.0, f"Deezer: {da.nb_fan} fans (very low)"

    if da.nb_fan < 1000:
        return 20.0, f"Deezer: {da.nb_fan:,} fans"

    return 5.0, f"Deezer: {da.nb_fan:,} fans, {da.nb_album} albums"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def standard_scan(
    artist_name: str,
    quick_result: QuickScanResult,
    genius: GeniusClient,
    discogs: DiscogsClient,
    setlistfm: SetlistFmClient,
    bandsintown: BandsintownClient,
    mb_client: MusicBrainzClient,
    deezer: DeezerClient,
    weights: StandardWeights | None = None,
    spotify_followers: int = 0,
) -> StandardScanResult:
    """Run all Standard-tier signals on a single artist."""
    if weights is None:
        weights = StandardWeights()

    w = weights
    total_weight = w.total()
    signals: list[SignalResult] = []
    total = 0.0

    # --- Quick score carry-forward ---
    raw_quick = float(quick_result.score)
    nw = w.quick_score / total_weight
    weighted = raw_quick * nw
    total += weighted
    signals.append(SignalResult(
        name="quick_score",
        raw_score=round(raw_quick, 1),
        weight=round(nw, 4),
        weighted_score=round(weighted, 2),
        detail=f"Quick tier score: {quick_result.score}",
    ))

    # --- Genius credits ---
    raw, detail = _score_genius_credits(artist_name, genius)
    nw = w.genius_credits / total_weight
    weighted = raw * nw
    total += weighted
    signals.append(SignalResult(
        name="genius_credits",
        raw_score=round(raw, 1),
        weight=round(nw, 4),
        weighted_score=round(weighted, 2),
        detail=detail,
    ))

    # --- Discogs physical releases ---
    raw, detail = _score_discogs_physical(artist_name, discogs)
    nw = w.discogs_physical / total_weight
    weighted = raw * nw
    total += weighted
    signals.append(SignalResult(
        name="discogs_physical",
        raw_score=round(raw, 1),
        weight=round(nw, 4),
        weighted_score=round(weighted, 2),
        detail=detail,
    ))

    # --- Live show history ---
    raw, detail = _score_live_show_history(artist_name, setlistfm, bandsintown)
    nw = w.live_show_history / total_weight
    weighted = raw * nw
    total += weighted
    signals.append(SignalResult(
        name="live_show_history",
        raw_score=round(raw, 1),
        weight=round(nw, 4),
        weighted_score=round(weighted, 2),
        detail=detail,
    ))

    # --- MusicBrainz presence ---
    raw, detail = _score_musicbrainz_presence(artist_name, mb_client)
    nw = w.musicbrainz_presence / total_weight
    weighted = raw * nw
    total += weighted
    signals.append(SignalResult(
        name="musicbrainz_presence",
        raw_score=round(raw, 1),
        weight=round(nw, 4),
        weighted_score=round(weighted, 2),
        detail=detail,
    ))

    # --- Label blocklist ---
    # Reuse the MusicBrainz artist we already looked up
    try:
        mb_artist = mb_client.search_artist(artist_name)
    except Exception:
        mb_artist = None
    raw, detail = _score_label_blocklist(artist_name, mb_client, mb_artist)
    nw = w.label_blocklist_match / total_weight
    weighted = raw * nw
    total += weighted
    signals.append(SignalResult(
        name="label_blocklist_match",
        raw_score=round(raw, 1),
        weight=round(nw, 4),
        weighted_score=round(weighted, 2),
        detail=detail,
    ))

    # --- Deezer cross-check ---
    raw, detail = _score_deezer_cross_check(artist_name, deezer, spotify_followers)
    nw = w.deezer_cross_check / total_weight
    weighted = raw * nw
    total += weighted
    signals.append(SignalResult(
        name="deezer_cross_check",
        raw_score=round(raw, 1),
        weight=round(nw, 4),
        weighted_score=round(weighted, 2),
        detail=detail,
    ))

    composite = int(min(max(round(total), 0), 100))
    return StandardScanResult(
        artist_id=quick_result.artist_id,
        artist_name=artist_name,
        score=composite,
        signals=signals,
    )
