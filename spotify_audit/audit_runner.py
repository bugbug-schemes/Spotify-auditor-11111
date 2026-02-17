"""
Reusable audit workflow — decoupled from CLI/Rich.

Provides ``run_audit()`` which accepts a progress callback so it can
be driven from the CLI (Rich), a web app (Flask), or anything else.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

from dotenv import load_dotenv

from spotify_audit.config import AuditConfig
from spotify_audit.spotify_client import SpotifyClient, ArtistInfo
from spotify_audit.deezer_client import DeezerClient
from spotify_audit.musicbrainz_client import MusicBrainzClient
from spotify_audit.genius_client import GeniusClient
from spotify_audit.discogs_client import DiscogsClient
from spotify_audit.setlistfm_client import SetlistFmClient
from spotify_audit.lastfm_client import LastfmClient
from spotify_audit.wikipedia_client import WikipediaClient
from spotify_audit.youtube_client import YouTubeClient
from spotify_audit.deezer_ai import DeezerAIChecker
from spotify_audit.pro_registry import PRORegistryClient
from spotify_audit.known_entities import run_pre_check, auto_promote_entity
from spotify_audit.songkick_client import SongkickClient
from spotify_audit.cache import Cache
from spotify_audit.analyzers.quick import quick_scan, QuickScanResult
from spotify_audit.analyzers.standard import standard_scan, standard_scan_from_external, StandardScanResult
from spotify_audit.evidence import (
    evaluate_artist, ArtistEvaluation, Verdict, ExternalData, Evidence, incorporate_deep_evidence,
)
from spotify_audit.blocklist_builder import analyze_for_blocklist, BlocklistReport
from spotify_audit.scoring import (
    finalize_artist_report,
    build_playlist_report,
    should_escalate_to_deep,
    ArtistReport,
    PlaylistReport,
)
from spotify_audit.deep_analysis import run_deep_analysis_batch
from spotify_audit.entity_db import EntityDB

logger = logging.getLogger("spotify_audit")

# Progress callback type: called with (phase, current, total, message)
ProgressCallback = Callable[[str, int, int, str], None]


def _noop_progress(phase: str, current: int, total: int, message: str) -> None:
    pass


def build_config() -> AuditConfig:
    """Load config from environment / .env file."""
    project_env = Path(__file__).resolve().parent.parent / ".env"
    if project_env.exists():
        load_dotenv(project_env, override=True)
    else:
        load_dotenv(override=True)
    return AuditConfig(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        genius_token=os.getenv("GENIUS_TOKEN", ""),
        discogs_token=os.getenv("DISCOGS_TOKEN", ""),
        setlistfm_api_key=os.getenv("SETLISTFM_API_KEY", ""),
        lastfm_api_key=os.getenv("LASTFM_API_KEY", ""),
        songkick_api_key=os.getenv("SONGKICK_API_KEY", ""),
        youtube_api_key=os.getenv("YOUTUBE_API_KEY", ""),
    )


def _resolve_artist_by_name(
    name: str,
    spotify_client: SpotifyClient,
    deezer_client: DeezerClient,
    mb_client: MusicBrainzClient,
) -> ArtistInfo:
    """Resolve an artist by name using Deezer API for real data."""
    search_name = name.split(",")[0].strip() if "," in name else name

    try:
        dz = deezer_client.search_artist(search_name)
        if dz:
            dz_lower = dz.name.lower().strip()
            search_lower = search_name.lower().strip()
            if dz_lower == search_lower or search_lower in dz_lower or dz_lower in search_lower:
                dz = deezer_client.enrich(dz)
                release_dates = [
                    a.get("release_date", "")
                    for a in dz.albums
                    if isinstance(a, dict) and a.get("release_date")
                ]
                related_names = [
                    r.get("name", "")
                    for r in dz.related_artists
                    if isinstance(r, dict) and r.get("name")
                ]
                return ArtistInfo(
                    artist_id=f"deezer:{dz.deezer_id}",
                    name=name,
                    followers=dz.nb_fan,
                    image_url=dz.picture_url or None,
                    external_urls={"deezer": dz.link} if dz.link else {},
                    album_count=dz.album_types.get("album", 0),
                    single_count=dz.album_types.get("single", 0),
                    total_tracks=sum(
                        a.get("nb_tracks", 0)
                        for a in dz.albums
                        if isinstance(a, dict)
                    ),
                    release_dates=release_dates,
                    track_durations=[d * 1000 for d in dz.track_durations],
                    labels=dz.labels,
                    track_titles=dz.track_titles,
                    track_ranks=dz.track_ranks,
                    has_explicit=dz.has_explicit,
                    contributors=dz.contributors,
                    contributor_roles=dz.contributor_roles,
                    related_artist_names=related_names,
                    deezer_fans=dz.nb_fan,
                    deezer_isrcs=dz.track_isrcs,
                    deezer_isrc_registrants=dz.isrc_registrants,
                )
    except Exception as exc:
        logger.debug("Deezer search failed for '%s': %s", name, exc)

    return ArtistInfo(artist_id=f"name:{name}", name=name)


def _lookup_external_data(
    artist_name: str,
    genius: GeniusClient,
    discogs: DiscogsClient,
    setlistfm: SetlistFmClient,
    mb_client: MusicBrainzClient,
    lastfm: "LastfmClient | None" = None,
    wikipedia: "WikipediaClient | None" = None,
    songkick: "SongkickClient | None" = None,
) -> ExternalData:
    """Run all Standard-tier API lookups and return aggregated results.

    Phase 1: MusicBrainz runs first to extract platform IDs for bridging.
    Phase 2: All other APIs run sequentially, using platform IDs when available.
    """
    ext = ExternalData()
    search_name = artist_name.split(",")[0].strip() if "," in artist_name else artist_name

    # ------------------------------------------------------------------
    # Phase 1: MusicBrainz first (provides platform IDs for other APIs)
    # ------------------------------------------------------------------
    from spotify_audit.name_matching import get_platform_ids_from_musicbrainz
    platform_ids: dict[str, str] = {}

    try:
        mb = mb_client.search_artist(search_name)
        if mb and mb.mbid:
            ext.musicbrainz_found = True
            ext.musicbrainz_type = mb.artist_type
            ext.musicbrainz_country = mb.country
            ext.musicbrainz_begin_date = mb.begin_date
            ext.musicbrainz_gender = mb.gender
            ext.musicbrainz_area = mb.area
            ext.musicbrainz_aliases = mb.aliases
            ext.musicbrainz_isnis = mb.isnis
            ext.musicbrainz_ipis = mb.ipis
            ext.musicbrainz_genres = mb.genres
            mb = mb_client.enrich(mb)
            ext.musicbrainz_labels = mb.labels
            ext.musicbrainz_urls = mb.urls
            # Priority 5: Enhanced URL categorization
            ext.musicbrainz_youtube_url = mb.youtube_url
            ext.musicbrainz_bandcamp_url = mb.bandcamp_url
            ext.musicbrainz_official_website = mb.official_website
            ext.musicbrainz_social_urls = mb.social_urls
            # Priority 7: ISRCs from MusicBrainz recordings
            if mb.isrcs:
                ext.isrcs.extend(mb.isrcs)
                ext.isrc_registrants = mb.isrc_registrants
            # Extract platform IDs for bridging to other APIs
            if mb.urls:
                platform_ids = get_platform_ids_from_musicbrainz(mb.urls)
                logger.debug("MusicBrainz platform IDs for '%s': %s", search_name, platform_ids)
    except Exception as exc:
        logger.debug("MusicBrainz lookup failed for '%s': %s", search_name, exc)

    # ------------------------------------------------------------------
    # Phase 2: Remaining APIs (using platform IDs when available)
    # ------------------------------------------------------------------

    if genius.enabled:
        try:
            ga = genius.search_artist(search_name, genius_id=platform_ids.get("genius"))
            if ga:
                ext.genius_found = True
                ga = genius.enrich(ga)
                ext.genius_song_count = ga.song_count
                ext.genius_description = ga.description_snippet
                ext.genius_facebook_name = ga.facebook_name
                ext.genius_instagram_name = ga.instagram_name
                ext.genius_twitter_name = ga.twitter_name
                ext.genius_is_verified = ga.is_verified
                ext.genius_followers_count = ga.followers_count
                ext.genius_alternate_names = ga.alternate_names
        except Exception as exc:
            logger.debug("Genius lookup failed for '%s': %s", search_name, exc)

    try:
        da = discogs.search_artist(search_name, discogs_id=platform_ids.get("discogs"))
        if da:
            ext.discogs_found = True
            da = discogs.enrich(da)
            ext.discogs_physical_releases = da.physical_releases
            ext.discogs_digital_releases = da.digital_only_releases
            ext.discogs_total_releases = da.total_releases
            ext.discogs_formats = da.formats
            ext.discogs_labels = da.labels
            ext.discogs_profile = da.profile
            ext.discogs_realname = da.realname
            ext.discogs_social_urls = da.social_urls
            ext.discogs_members = da.members
            ext.discogs_groups = da.groups
            ext.discogs_data_quality = da.data_quality
    except Exception as exc:
        logger.debug("Discogs lookup failed for '%s': %s", search_name, exc)

    if setlistfm.enabled:
        try:
            sa = setlistfm.search_artist(search_name, setlistfm_url=platform_ids.get("setlistfm"))
            if sa:
                ext.setlistfm_found = True
                sa = setlistfm.get_setlist_count(sa)
                ext.setlistfm_total_shows = sa.total_setlists
                ext.setlistfm_first_show = sa.first_show_date
                ext.setlistfm_last_show = sa.last_show_date
                ext.setlistfm_venues = sa.top_venues
                ext.setlistfm_venue_cities = sa.venue_cities
                ext.setlistfm_venue_countries = sa.venue_countries
                ext.setlistfm_tour_names = sa.tour_names
        except Exception as exc:
            logger.debug("Setlist.fm lookup failed for '%s': %s", search_name, exc)

    if lastfm and lastfm.enabled:
        try:
            la = lastfm.get_artist_info(search_name, lastfm_name=platform_ids.get("lastfm"))
            if la:
                ext.lastfm_found = True
                la = lastfm.enrich(la)
                ext.lastfm_listeners = la.listeners
                ext.lastfm_playcount = la.playcount
                ext.lastfm_listener_play_ratio = (
                    round(la.playcount / la.listeners, 2) if la.listeners > 0 else 0.0
                )
                ext.lastfm_tags = la.tags
                ext.lastfm_similar_artists = la.similar_artists
                ext.lastfm_bio_exists = bool(la.bio and len(la.bio) > 50)
        except Exception as exc:
            logger.debug("Last.fm lookup failed for '%s': %s", search_name, exc)

    if wikipedia and wikipedia.enabled:
        try:
            wa = wikipedia.search_artist(search_name, wikipedia_title=platform_ids.get("wikipedia"))
            if wa:
                ext.wikipedia_found = True
                wa = wikipedia.enrich(wa)
                ext.wikipedia_title = wa.title
                ext.wikipedia_length = wa.length
                ext.wikipedia_extract = wa.extract
                ext.wikipedia_description = wa.description
                ext.wikipedia_categories = wa.categories
                ext.wikipedia_monthly_views = wa.monthly_views
                ext.wikipedia_url = wa.url
        except Exception as exc:
            logger.debug("Wikipedia lookup failed for '%s': %s", search_name, exc)

    if songkick and songkick.enabled:
        try:
            sa = songkick.search_artist(search_name, songkick_id=platform_ids.get("songkick"))
            if sa:
                ext.songkick_found = True
                sa = songkick.enrich(sa)
                ext.songkick_on_tour = sa.on_tour
                ext.songkick_total_past_events = sa.total_past_events
                ext.songkick_total_upcoming_events = sa.total_upcoming_events
                ext.songkick_first_event_date = sa.first_event_date
                ext.songkick_last_event_date = sa.last_event_date
                ext.songkick_venue_names = sa.venue_names
                ext.songkick_venue_cities = sa.venue_cities
                ext.songkick_venue_countries = sa.venue_countries
                ext.songkick_event_types = sa.event_types
        except Exception as exc:
            logger.debug("Songkick lookup failed for '%s': %s", search_name, exc)

    return ext


def run_audit(
    playlist_url: str,
    deep: bool = False,
    config: Optional[AuditConfig] = None,
    on_progress: Optional[ProgressCallback] = None,
    use_cache: bool = True,
    # Legacy parameter — maps to deep=True when value is "deep"
    max_tier: str | None = None,
) -> tuple[PlaylistReport, BlocklistReport | None]:
    """Run the full audit workflow. Returns (PlaylistReport, BlocklistReport).

    Parameters
    ----------
    playlist_url : str
        Spotify playlist URL.
    deep : bool
        Whether to run Claude AI deep analysis (requires ANTHROPIC_API_KEY).
    config : AuditConfig, optional
        Pre-built config. Loaded from env if not given.
    on_progress : callable, optional
        Called with (phase, current, total, message) for status updates.
    use_cache : bool
        Whether to use the SQLite cache.
    max_tier : str, optional
        Legacy parameter. If "deep", sets deep=True.
    """
    # Legacy compatibility: convert max_tier to deep flag
    if max_tier is not None:
        deep = (max_tier == "deep")

    if config is None:
        config = build_config()
    progress = on_progress or _noop_progress

    client = SpotifyClient(config)
    cache = Cache(config.db_path, config.cache_ttl_days) if use_cache else None

    try:
        return _run_audit_core(client, cache, config, playlist_url, deep, progress)
    finally:
        client.close()
        if cache:
            cache.close()


def _run_audit_core(
    client: SpotifyClient,
    cache: Cache | None,
    config: AuditConfig,
    playlist_url: str,
    deep: bool,
    progress: ProgressCallback,
) -> tuple[PlaylistReport, BlocklistReport | None]:
    """Core workflow: fetch playlist -> collect evidence -> optional deep analysis."""

    # 1. Fetch playlist
    progress("fetch", 0, 1, "Fetching playlist from Spotify...")
    meta, tracks = client.get_playlist(playlist_url)
    progress("fetch", 1, 1, f"Loaded {meta.name} — {meta.total_tracks} tracks")

    # Entity intelligence DB
    entity_db: EntityDB | None = None
    try:
        entity_db = EntityDB()
        db_stats = entity_db.stats()
        db_total = sum(db_stats[t] for t in ("artists", "labels", "songwriters", "publishers"))
    except Exception:
        entity_db = None

    # Set up API clients
    deezer_client = DeezerClient(delay=0.3)
    mb_client = MusicBrainzClient(delay=1.1)
    genius_client = GeniusClient(access_token=config.genius_token, delay=0.3)
    discogs_client = DiscogsClient(token=config.discogs_token, delay=1.0)
    setlistfm_client = SetlistFmClient(api_key=config.setlistfm_api_key, delay=0.5)
    lastfm_client = LastfmClient(api_key=config.lastfm_api_key, delay=0.25)
    wikipedia_client = WikipediaClient(delay=0.2)
    songkick_client = SongkickClient(api_key=config.songkick_api_key, delay=0.5)

    anthropic_client = None
    if config.anthropic_api_key and deep:
        try:
            from anthropic import Anthropic
            anthropic_client = Anthropic(api_key=config.anthropic_api_key)
        except ImportError:
            pass

    # 2. Deduplicate artists
    artist_ids = list({aid for t in tracks for aid in t.artist_ids if aid})
    artist_names_only: list[str] = []
    if not artist_ids:
        artist_names_only = list({
            name for t in tracks for name in t.artist_names if name
        })

    artist_keys: list[tuple[str, bool]] = []
    if artist_ids:
        artist_keys = [(aid, True) for aid in artist_ids]
    else:
        artist_keys = [(name, False) for name in artist_names_only]

    total_artists = len(artist_keys)
    progress("resolve", 0, total_artists, f"Resolving {total_artists} artists via Deezer...")

    # 3. Resolve + quick scan (parallelized)
    artist_infos: dict[str, ArtistInfo] = {}
    quick_results: dict[str, QuickScanResult] = {}

    def _resolve_single(key: str, is_id: bool) -> tuple[str, ArtistInfo | None, QuickScanResult | None, bool | None]:
        """Resolve one artist. Returns (key, artist, cached_qr, is_cache_hit)."""
        cached_qr = None
        cached_artist = None
        if cache:
            cached = cache.get(key, "quick")
            if cached:
                cached_qr = QuickScanResult(
                    artist_id=cached["artist_id"],
                    artist_name=cached["artist_name"],
                    score=cached["score"],
                    signals=[],
                    tier="quick",
                )
                ai_data = cached.get("artist_info")
                if ai_data:
                    try:
                        cached_artist = ArtistInfo(**ai_data)
                    except (TypeError, KeyError):
                        cached_artist = None

        if cached_artist:
            return (key, cached_artist, cached_qr, True)

        if is_id:
            artist = client.get_artist_info(key)
        else:
            artist = _resolve_artist_by_name(key, client, deezer_client, mb_client)
        return (key, artist, cached_qr, False)

    resolved_i = 0
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="resolve") as pool:
        futures = {
            pool.submit(_resolve_single, key, is_id): key
            for key, is_id in artist_keys
        }
        for fut in as_completed(futures):
            key, artist, cached_qr, is_cache_hit = fut.result()
            resolved_i += 1

            if is_cache_hit:
                artist_infos[key] = artist
                quick_results[key] = cached_qr
            else:
                artist_infos[key] = artist
                if cached_qr:
                    quick_results[key] = cached_qr
                else:
                    qr = quick_scan(artist, config.quick_weights)
                    quick_results[key] = qr

                qr = quick_results[key]
                if cache:
                    cache.put(key, "quick", {
                        "artist_id": qr.artist_id,
                        "artist_name": qr.artist_name,
                        "score": qr.score,
                        "artist_info": dataclasses.asdict(artist),
                    })

            progress("resolve", resolved_i, total_artists, f"Resolved {artist_infos[key].name}")

    # 4. External lookups + evidence evaluation
    evaluations: dict[str, ArtistEvaluation] = {}
    standard_results: dict[str, StandardScanResult] = {}
    artists_to_lookup = [
        (key, artist_infos[key]) for key in quick_results if key in artist_infos
    ]

    progress("evaluate", 0, len(artists_to_lookup), "Running external lookups + evidence...")

    # Set up conditional enrichment clients
    youtube_client = YouTubeClient(api_key=config.youtube_api_key, delay=0.3)
    deezer_ai_checker = DeezerAIChecker(delay=1.5)
    pro_client = PRORegistryClient(delay=2.5)

    def _collect_quick_presence(artist: ArtistInfo):
        """Build a minimal PlatformPresence from core artist data (for short-circuit path)."""
        from spotify_audit.evidence import PlatformPresence
        presence = PlatformPresence()
        if not artist.artist_id.startswith("name:"):
            presence.spotify = True
        if artist.deezer_fans > 0:
            presence.deezer = True
            presence.deezer_fans = artist.deezer_fans
        return presence

    def _lookup_and_evaluate(key: str, artist: ArtistInfo) -> tuple[str, ArtistEvaluation, StandardScanResult]:
        """Run pre-check + external lookups + conditional enrichment + evidence eval."""

        # Priority 1: Known entity pre-check (runs first)
        pre = run_pre_check(
            artist_name=artist.name,
            labels=artist.labels,
            contributors=artist.contributors,
            entity_db=entity_db,
        )
        if pre.short_circuit:
            # Short-circuit: skip all external lookups
            ext = ExternalData(pre_seeded_evidence=pre.pre_seeded_evidence)
            # Build red flags from pre-check so reports show WHY it was flagged
            short_circuit_flags = [Evidence(
                finding=pre.reason,
                source="Pre-check",
                evidence_type="red_flag",
                strength="strong",
                detail=pre.reason,
                tags=["known_bad_actor"],
            )]
            ev = ArtistEvaluation(
                artist_id=artist.artist_id,
                artist_name=artist.name,
                verdict=Verdict.LIKELY_ARTIFICIAL,
                confidence="high",
                platform_presence=_collect_quick_presence(artist),
                red_flags=short_circuit_flags,
                green_flags=[],
                decision_path=[f"Pre-check: {pre.reason}"],
            )
            qr = quick_results[key]
            sr = standard_scan_from_external(
                quick_result=qr, ext=ext,
                deezer_fans=artist.deezer_fans if hasattr(artist, 'deezer_fans') else 0,
                weights=config.standard_weights,
            )
            return (key, ev, sr)

        # Standard external lookups (concurrent)
        ext = _lookup_external_data(
            artist_name=artist.name,
            genius=genius_client,
            discogs=discogs_client,
            setlistfm=setlistfm_client,
            mb_client=mb_client,
            lastfm=lastfm_client,
            wikipedia=wikipedia_client,
            songkick=songkick_client,
        )

        # Inject pre-seeded evidence from pre-check
        if pre.pre_seeded_evidence:
            ext.pre_seeded_evidence = pre.pre_seeded_evidence

        # Priority 7: Merge Deezer ISRCs into ExternalData
        if hasattr(artist, 'deezer_isrcs') and artist.deezer_isrcs:
            for isrc in artist.deezer_isrcs:
                if isrc not in ext.isrcs:
                    ext.isrcs.append(isrc)
            existing = set(ext.isrc_registrants)
            for reg in artist.deezer_isrc_registrants:
                if reg not in existing:
                    ext.isrc_registrants.append(reg)
                    existing.add(reg)

        # Conditional enrichment: only for artists with red flags
        has_red_flags = bool(pre.pfc_label_match) or any(
            e.get("evidence_type") == "red_flag" for e in pre.pre_seeded_evidence
        )

        if has_red_flags:
            # Priority 2: Deezer AI check
            if hasattr(artist, 'deezer_fans') and artist.artist_id.startswith("deezer:"):
                try:
                    deezer_id = int(artist.artist_id.split(":")[1]) if ":" in artist.artist_id else 0
                    if deezer_id:
                        ai_result = deezer_ai_checker.check_artist(deezer_id)
                        if ai_result.checked:
                            ext.deezer_ai_checked = True
                            ext.deezer_ai_tagged_albums = ai_result.ai_tagged_albums
                except Exception as exc:
                    logger.debug("Deezer AI check failed for '%s': %s", artist.name, exc)

            # Priority 4: YouTube cross-reference
            if youtube_client.enabled:
                try:
                    yt_url = ext.musicbrainz_youtube_url or None
                    yt_result = youtube_client.search_artist(artist.name, yt_url)
                    if yt_result:
                        ext.youtube_checked = True
                        ext.youtube_channel_found = yt_result.channel_found
                        ext.youtube_subscriber_count = yt_result.subscriber_count
                        ext.youtube_video_count = yt_result.video_count
                        ext.youtube_view_count = yt_result.view_count
                        ext.youtube_music_videos_found = yt_result.music_videos_found
                        ext.youtube_match_confidence = yt_result.match_confidence
                except Exception as exc:
                    logger.debug("YouTube check failed for '%s': %s", artist.name, exc)

            # Priority 3: PRO registry (only for moderate+ red flags)
            if len([e for e in pre.pre_seeded_evidence
                    if e.get("evidence_type") == "red_flag"
                    and e.get("strength") in ("strong", "moderate")]) >= 1:
                try:
                    pro_result = pro_client.search_writer(artist.name)
                    ext.pro_checked = True
                    ext.pro_found_bmi = pro_result.found_bmi
                    ext.pro_found_ascap = pro_result.found_ascap
                    ext.pro_works_count = pro_result.bmi_works_count + pro_result.ascap_works_count
                    ext.pro_publishers = pro_result.publishers
                    ext.pro_songwriter_registered = pro_result.songwriter_registered
                    ext.pro_pfc_publisher_match = pro_result.pfc_publisher_match
                    ext.pro_zero_songwriter_share = pro_result.zero_songwriter_share
                except Exception as exc:
                    logger.debug("PRO registry check failed for '%s': %s", artist.name, exc)

        # Run evidence evaluation
        ev = evaluate_artist(artist, external=ext, entity_db=entity_db)

        # Priority 1: Update entity DB after scan
        if entity_db:
            try:
                entity_db.increment_scan_count(
                    artist.name,
                    verdict=ev.verdict.value,
                    confidence=ev.confidence,
                )
                auto_promote_entity(
                    entity_db, artist.name,
                    ev.verdict.value, ev.confidence,
                )
            except Exception as exc:
                logger.debug("Entity DB update failed for '%s': %s", artist.name, exc)

        qr = quick_results[key]
        sr = standard_scan_from_external(
            quick_result=qr,
            ext=ext,
            deezer_fans=artist.deezer_fans if hasattr(artist, 'deezer_fans') else 0,
            weights=config.standard_weights,
        )
        return (key, ev, sr)

    eval_completed = 0
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="eval") as pool:
        futures = {
            pool.submit(_lookup_and_evaluate, key, artist): key
            for key, artist in artists_to_lookup
        }
        for fut in as_completed(futures):
            key, ev, sr = fut.result()
            evaluations[key] = ev
            standard_results[key] = sr
            eval_completed += 1
            progress("evaluate", eval_completed, len(artists_to_lookup), f"Evaluated {artist_infos[key].name}")

    # Safety fallback
    for key in quick_results:
        if key not in evaluations:
            artist = artist_infos.get(key)
            if artist:
                ev = evaluate_artist(artist, entity_db=entity_db)
                evaluations[key] = ev
            else:
                qr = quick_results[key]
                minimal = ArtistInfo(artist_id=qr.artist_id, name=qr.artist_name)
                ev = evaluate_artist(minimal, entity_db=entity_db)
                evaluations[key] = ev

    # 5. Deep analysis (optional — Claude AI)
    deep_count = 0
    if anthropic_client and deep:
        deep_candidates = []
        for key in quick_results:
            ev = evaluations.get(key)
            if not ev:
                continue
            score = (standard_results[key].score
                     if key in standard_results else quick_results[key].score)
            if should_escalate_to_deep(score, config) or len(quick_results) <= 20:
                deep_candidates.append(key)

        if deep_candidates:
            batch_input: list[tuple[str, ArtistInfo, ExternalData]] = []
            for key in deep_candidates:
                artist = artist_infos.get(key)
                ev = evaluations.get(key)
                if artist and ev:
                    ext = ev.external_data or ExternalData()
                    batch_input.append((key, artist, ext))

            progress("deep", 0, len(batch_input), "Running Claude deep analysis...")
            completed = [0]

            def _deep_progress():
                completed[0] += 1
                progress("deep", completed[0], len(batch_input), "Claude deep analysis...")

            try:
                deep_results = run_deep_analysis_batch(
                    anthropic_client, batch_input, on_progress=_deep_progress,
                )
                for key, deep_result in deep_results.items():
                    all_deep_ev = deep_result.bio_analysis + deep_result.image_analysis + deep_result.synthesis
                    if all_deep_ev:
                        ev = evaluations.get(key)
                        if ev:
                            evaluations[key] = incorporate_deep_evidence(ev, all_deep_ev)
                            deep_count += 1
            except Exception as exc:
                logger.warning("Batch deep analysis failed: %s", exc)

    # 6. Build reports
    artist_reports: list[ArtistReport] = []
    for artist_id, qr in quick_results.items():
        report = finalize_artist_report(
            artist_id=artist_id,
            artist_name=qr.artist_name,
            evaluation=evaluations.get(artist_id),
            quick_result=qr,
            standard_result=standard_results.get(artist_id),
            deep_result=None,
        )
        artist_reports.append(report)

    # 7. Blocklist analysis
    all_evaluations = list(evaluations.values())
    blocklist_report = analyze_for_blocklist(all_evaluations) if all_evaluations else None

    # 8. Populate entity database (batched in single transaction)
    if entity_db:
        try:
            with entity_db.batch():
                scan_id = entity_db.start_scan(
                    playlist_id=meta.playlist_id,
                    playlist_name=meta.name,
                    scan_tier="deep" if deep else "standard",
                    artist_count=len(artist_reports),
                )
                for report in artist_reports:
                    ev = report.evaluation
                    if not ev:
                        continue
                    aid = entity_db.upsert_artist(
                        report.artist_name,
                        threat_status=(
                            "confirmed_bad" if ev.verdict == Verdict.LIKELY_ARTIFICIAL
                            else "suspected" if ev.verdict == Verdict.SUSPICIOUS
                            else "cleared" if ev.verdict == Verdict.VERIFIED_ARTIST
                            else "unknown"
                        ),
                        threat_category=report.threat_category,
                        latest_verdict=ev.verdict.value,
                        latest_confidence=ev.confidence,
                    )
                    for lbl in ev.labels:
                        lid = entity_db.upsert_label(lbl)
                        entity_db.link_artist_label(aid, lid, source="scan")
                    for contrib in ev.contributors:
                        sid = entity_db.upsert_songwriter(contrib)
                        entity_db.link_artist_songwriter(aid, sid, source="scan")
                    for e in ev.strong_red_flags:
                        entity_db.add_observation(
                            "artist", aid, "red_flag", e.finding,
                            detail=e.detail, source=e.source,
                            strength=e.strength, scan_id=scan_id,
                        )
                entity_db.refresh_entity_counts()
                entity_db.complete_scan(scan_id)
        except Exception as exc:
            logger.debug("Entity DB update failed (non-fatal): %s", exc)
        finally:
            entity_db.close()

    # 9. Build playlist-level report
    playlist_report = build_playlist_report(
        playlist_name=meta.name,
        playlist_id=meta.playlist_id,
        owner=meta.owner,
        total_tracks=meta.total_tracks,
        is_spotify_owned=meta.is_spotify_owned,
        artist_reports=artist_reports,
    )

    progress("done", 1, 1, "Scan complete!")
    return playlist_report, blocklist_report
