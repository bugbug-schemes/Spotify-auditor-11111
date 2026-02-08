"""
spotify-audit CLI entry point.

Usage:
    spotify-audit <playlist-url> [--tier quick|standard|deep] [--format md|html|json] [--output FILE]

No Spotify API key required — data is scraped from public embed endpoints.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from spotify_audit.config import AuditConfig
from spotify_audit.spotify_client import SpotifyClient, ArtistInfo
from spotify_audit.deezer_client import DeezerClient
from spotify_audit.musicbrainz_client import MusicBrainzClient
from spotify_audit.genius_client import GeniusClient
from spotify_audit.discogs_client import DiscogsClient
from spotify_audit.setlistfm_client import SetlistFmClient
from spotify_audit.bandsintown_client import BandsintownClient
from spotify_audit.lastfm_client import LastfmClient
from spotify_audit.cache import Cache
from spotify_audit.analyzers.quick import quick_scan, QuickScanResult
from spotify_audit.analyzers.standard import standard_scan, StandardScanResult
from spotify_audit.evidence import evaluate_artist, ArtistEvaluation, Verdict, ExternalData, incorporate_deep_evidence
from spotify_audit.blocklist_builder import analyze_for_blocklist, BlocklistReport
from spotify_audit.scoring import (
    finalize_artist_report,
    build_playlist_report,
    should_escalate_to_standard,
    should_escalate_to_deep,
    ArtistReport,
    PlaylistReport,
)
from spotify_audit.reports.formatter import to_markdown, to_html, to_json
from spotify_audit.deep_analysis import run_deep_analysis, run_deep_analysis_batch, DeepAnalysis

console = Console()
logger = logging.getLogger("spotify_audit")


def _build_config() -> AuditConfig:
    """Load config from environment variables."""
    # Explicitly load .env from project root (parent of spotify_audit package)
    # override=True ensures .env values take precedence over any pre-existing env vars
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
        bandsintown_app_id=os.getenv("BANDSINTOWN_APP_ID", ""),
    )


# ---------------------------------------------------------------------------
# Verdict colors and display
# ---------------------------------------------------------------------------

_VERDICT_COLORS = {
    Verdict.VERIFIED_ARTIST: "green",
    Verdict.LIKELY_AUTHENTIC: "bright_green",
    Verdict.INCONCLUSIVE: "yellow",
    Verdict.SUSPICIOUS: "dark_orange",
    Verdict.LIKELY_ARTIFICIAL: "red",
}

_VERDICT_ICONS = {
    Verdict.VERIFIED_ARTIST: "[green]OK[/green]",
    Verdict.LIKELY_AUTHENTIC: "[bright_green]OK[/bright_green]",
    Verdict.INCONCLUSIVE: "[yellow]??[/yellow]",
    Verdict.SUSPICIOUS: "[dark_orange]!![/dark_orange]",
    Verdict.LIKELY_ARTIFICIAL: "[red]XX[/red]",
}


def _color_for_verdict(verdict: Verdict) -> str:
    return _VERDICT_COLORS.get(verdict, "white")


def _render_summary_table(report: PlaylistReport, blocklist_report: BlocklistReport | None = None) -> None:
    """Rich output summarizing the playlist with evidence-based verdicts."""
    console.print()

    # Health score panel
    health = report.health_score
    if health >= 80:
        health_color = "green"
    elif health >= 60:
        health_color = "yellow"
    elif health >= 40:
        health_color = "dark_orange"
    else:
        health_color = "red"

    console.print(Panel(
        f"[bold]Playlist Health Score: [{health_color}]{health}/100[/{health_color}][/bold]",
        title=f"[bold green]{report.playlist_name}[/bold green]",
        subtitle=f"Owner: {report.owner} | Tracks: {report.total_tracks} | Artists: {report.total_unique_artists}",
    ))

    # Verdict breakdown
    breakdown = Table(title="Verdict Breakdown", show_header=True)
    breakdown.add_column("Verdict", style="bold")
    breakdown.add_column("Count", justify="right")
    breakdown.add_row(
        "[green]Verified Artist[/green]",
        str(report.verified_artists),
    )
    breakdown.add_row(
        "[bright_green]Likely Authentic[/bright_green]",
        str(report.likely_authentic),
    )
    breakdown.add_row(
        "[yellow]Inconclusive[/yellow]",
        str(report.inconclusive),
    )
    breakdown.add_row(
        "[dark_orange]Suspicious[/dark_orange]",
        str(report.suspicious),
    )
    breakdown.add_row(
        "[red]Likely Artificial[/red]",
        str(report.likely_artificial),
    )
    console.print(breakdown)

    # Artist verdict table with API sources
    if report.artists:
        artist_table = Table(title="Artist Evaluations", show_header=True)
        artist_table.add_column("", width=3)  # icon
        artist_table.add_column("Verdict", width=18)
        artist_table.add_column("Artist", min_width=18)
        artist_table.add_column("APIs Reached", min_width=22)
        artist_table.add_column("Key Evidence", min_width=25)
        artist_table.add_column("Conf.", width=6)

        for a in report.artists:
            ev = a.evaluation
            if not ev:
                artist_table.add_row(
                    "[yellow]??[/yellow]",
                    "[yellow]No evaluation[/yellow]",
                    a.artist_name,
                    "-",
                    "-",
                    "-",
                )
                continue

            color = _color_for_verdict(ev.verdict)
            icon = _VERDICT_ICONS.get(ev.verdict, "")

            # API source status
            sources = ev.sources_reached
            source_parts = []
            for name, reached in sources.items():
                if reached:
                    source_parts.append(f"[green]{name}[/green]")
                else:
                    source_parts.append(f"[dim]{name}[/dim]")
            sources_str = " ".join(source_parts)

            # Show the most important evidence as a short summary
            key_evidence = _summarize_key_evidence(ev)

            artist_table.add_row(
                icon,
                f"[{color}]{ev.verdict.value}[/{color}]",
                a.artist_name,
                sources_str,
                key_evidence,
                ev.confidence,
            )
        console.print(artist_table)

    # Show detailed evidence cards for ALL artists
    console.print()
    console.print("[bold]Detailed Artist Scorecards:[/bold]")
    for a in report.artists:
        if a.evaluation:
            _render_evidence_card(a)

    # Blocklist intelligence
    if blocklist_report and blocklist_report.has_suggestions:
        console.print()
        _render_blocklist_report(blocklist_report)


def _summarize_key_evidence(ev: ArtistEvaluation) -> str:
    """Build a short summary of the most important evidence."""
    parts: list[str] = []

    # Show platform presence count
    platforms = ev.platform_presence.count()
    if platforms >= 2:
        parts.append(f"{platforms} platforms")
    if ev.platform_presence.deezer_fans:
        parts.append(f"{ev.platform_presence.deezer_fans:,} fans")

    # Count flags
    if ev.red_flags:
        strong_reds = len(ev.strong_red_flags)
        if strong_reds:
            parts.append(f"[red]{strong_reds} strong red flag{'s' if strong_reds != 1 else ''}[/red]")
        else:
            parts.append(f"[dark_orange]{len(ev.red_flags)} red flag{'s' if len(ev.red_flags) != 1 else ''}[/dark_orange]")
    if ev.green_flags:
        parts.append(f"[green]{len(ev.green_flags)} green flag{'s' if len(ev.green_flags) != 1 else ''}[/green]")

    return ", ".join(parts) if parts else "-"


def _render_evidence_card(a: ArtistReport) -> None:
    """Render a detailed evidence card for a single artist."""
    ev = a.evaluation
    if not ev:
        return

    color = _color_for_verdict(ev.verdict)
    console.print()
    console.print(Panel(
        _build_evidence_text(ev),
        title=f"[bold]{a.artist_name}[/bold] — [{color}]{ev.verdict.value}[/{color}] ({ev.confidence} confidence)",
        border_style=color,
    ))


def _build_evidence_text(ev: ArtistEvaluation) -> str:
    """Build rich text showing all evidence for an artist."""
    lines: list[str] = []

    # --- Section 1: API Sources Reached ---
    lines.append("[bold underline]Data Sources[/bold underline]")
    sources = ev.sources_reached
    for name, reached in sources.items():
        if reached:
            lines.append(f"  [green]OK[/green]  {name}")
        else:
            lines.append(f"  [red]--[/red]  {name} [dim](not found)[/dim]")
    lines.append("")

    # --- Section 2: Category Scores (text-based bar chart) ---
    lines.append("[bold underline]Signal Scores[/bold underline]")
    scores = ev.category_scores
    for cat, score in scores.items():
        bar_filled = score // 5  # 20 chars max
        bar_empty = 20 - bar_filled
        if score >= 60:
            bar_color = "green"
        elif score >= 30:
            bar_color = "yellow"
        else:
            bar_color = "red"
        bar = f"[{bar_color}]{'█' * bar_filled}[/{bar_color}][dim]{'░' * bar_empty}[/dim]"
        lines.append(f"  {cat:<20s} {bar} {score:>3d}/100")
    lines.append("")

    # --- Section 3: Platform presence ---
    platforms = ev.platform_presence.names()
    if platforms:
        lines.append(f"[bold]Found on:[/bold] {', '.join(platforms)}")
    else:
        lines.append("[bold]Found on:[/bold] No verified platforms")
    lines.append("")

    # --- Section 4: Decision path ---
    if ev.decision_path:
        lines.append("[bold]Decision:[/bold] " + " -> ".join(ev.decision_path))
        lines.append("")

    # --- Section 5: Key data fields that contributed ---
    ext = ev.external_data
    if ext:
        data_fields: list[str] = []
        if ev.platform_presence.deezer_fans:
            data_fields.append(f"Deezer fans: {ev.platform_presence.deezer_fans:,}")
        if ext.genius_song_count:
            data_fields.append(f"Genius songs: {ext.genius_song_count}")
        if ext.genius_is_verified:
            data_fields.append("Genius: verified")
        if ext.genius_followers_count:
            data_fields.append(f"Genius followers: {ext.genius_followers_count:,}")
        if ext.discogs_physical_releases:
            data_fields.append(f"Discogs physical: {ext.discogs_physical_releases}")
        if ext.discogs_total_releases:
            data_fields.append(f"Discogs total: {ext.discogs_total_releases}")
        if ext.discogs_profile:
            data_fields.append(f"Discogs bio: {len(ext.discogs_profile)} chars")
        if ext.discogs_realname:
            data_fields.append(f"Real name: {ext.discogs_realname}")
        if ext.discogs_members:
            data_fields.append(f"Members: {', '.join(ext.discogs_members[:3])}")
        if ext.setlistfm_total_shows:
            data_fields.append(f"Setlist.fm shows: {ext.setlistfm_total_shows}")
        if ext.setlistfm_tour_names:
            data_fields.append(f"Tours: {', '.join(ext.setlistfm_tour_names[:2])}")
        if ext.setlistfm_venue_countries:
            data_fields.append(f"Countries: {', '.join(ext.setlistfm_venue_countries[:4])}")
        if ext.bandsintown_past_events:
            data_fields.append(f"BIT events: {ext.bandsintown_past_events}")
        if ext.bandsintown_tracker_count:
            data_fields.append(f"BIT trackers: {ext.bandsintown_tracker_count:,}")
        if ext.musicbrainz_type:
            data_fields.append(f"MB type: {ext.musicbrainz_type}")
        if ext.musicbrainz_country:
            data_fields.append(f"MB country: {ext.musicbrainz_country}")
        if ext.musicbrainz_begin_date:
            data_fields.append(f"MB active since: {ext.musicbrainz_begin_date}")
        if ext.musicbrainz_isnis:
            data_fields.append(f"ISNI: {ext.musicbrainz_isnis[0]}")
        if ext.musicbrainz_ipis:
            data_fields.append(f"IPI: {ext.musicbrainz_ipis[0]}")
        if ext.musicbrainz_genres:
            data_fields.append(f"MB genres: {', '.join(ext.musicbrainz_genres[:3])}")
        social_links = []
        if ext.genius_facebook_name:
            social_links.append("FB")
        if ext.genius_instagram_name:
            social_links.append("IG")
        if ext.genius_twitter_name:
            social_links.append("X")
        if ext.bandsintown_facebook_url:
            social_links.append("FB(BIT)")
        if social_links:
            data_fields.append(f"Social: {', '.join(social_links)}")
        mb_links = []
        for rel_type in ext.musicbrainz_urls:
            mb_links.append(rel_type)
        if mb_links:
            data_fields.append(f"MB links: {', '.join(mb_links[:4])}")

        if data_fields:
            lines.append("[bold underline]Key Data Fields[/bold underline]")
            for df in data_fields:
                lines.append(f"  {df}")
            lines.append("")

    # --- Section 6: Red flags ---
    if ev.red_flags:
        lines.append(f"[bold red]Red Flags ({len(ev.red_flags)}):[/bold red]")
        for e in ev.red_flags:
            strength_icon = {"strong": "!!!", "moderate": "!!", "weak": "!"}
            icon = strength_icon.get(e.strength, "!")
            lines.append(f"  [{e.strength}] {icon} {e.finding} [dim]({e.source})[/dim]")
            lines.append(f"      [dim]{e.detail}[/dim]")
        lines.append("")

    # --- Section 7: Green flags ---
    if ev.green_flags:
        lines.append(f"[bold green]Green Flags ({len(ev.green_flags)}):[/bold green]")
        for e in ev.green_flags:
            strength_icon = {"strong": "+++", "moderate": "++", "weak": "+"}
            icon = strength_icon.get(e.strength, "+")
            lines.append(f"  [{e.strength}] {icon} {e.finding} [dim]({e.source})[/dim]")
            lines.append(f"      [dim]{e.detail}[/dim]")
        lines.append("")

    # --- Section 8: Neutral notes ---
    if ev.neutral_notes:
        lines.append("[bold]Notes:[/bold]")
        for e in ev.neutral_notes:
            lines.append(f"  - {e.finding} ({e.source})")

    return "\n".join(lines)


def _render_blocklist_report(bl_report: BlocklistReport) -> None:
    """Show blocklist intelligence from the scan."""
    console.print(Panel(
        "[bold]Blocklist Intelligence[/bold]\n"
        "Based on data from this scan, the following blocklist additions are suggested:",
        border_style="cyan",
    ))

    for suggestion in bl_report.suggestions:
        confidence_color = {"high": "red", "medium": "yellow", "low": "dim"}.get(
            suggestion.confidence, "dim"
        )
        console.print(
            f"  [{confidence_color}]{suggestion.confidence.upper()}[/{confidence_color}] "
            f"Add [bold]{suggestion.value}[/bold] to {suggestion.blocklist}"
        )
        console.print(f"       Reason: {suggestion.reason}")
        if suggestion.seen_on:
            console.print(f"       Seen on: {', '.join(suggestion.seen_on[:5])}")


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

@click.command()
@click.argument("playlist_url")
@click.option(
    "--tier",
    type=click.Choice(["quick", "standard", "deep"], case_sensitive=False),
    default="quick",
    help="Maximum analysis tier. 'quick' = Spotify only; higher tiers auto-escalate.",
)
@click.option(
    "--format", "fmt",
    type=click.Choice(["md", "html", "json"], case_sensitive=False),
    default="md",
    help="Report output format.",
)
@click.option(
    "--output", "-o",
    type=click.Path(),
    default=None,
    help="Write report to file instead of stdout.",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
@click.option("--no-cache", is_flag=True, help="Bypass the SQLite cache.")
def main(
    playlist_url: str,
    tier: str,
    fmt: str,
    output: str | None,
    verbose: bool,
    no_cache: bool,
) -> None:
    """Analyze a Spotify playlist for AI-generated, ghost, and fake artists.

    No API keys required — data is scraped from Spotify's public embed endpoints.
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    config = _build_config()
    client = SpotifyClient(config)
    cache = None if no_cache else Cache(config.db_path, config.cache_ttl_days)

    try:
        playlist_report, blocklist_report = _run_audit(client, cache, config, playlist_url, tier)
    finally:
        client.close()
        if cache:
            cache.close()

    # Terminal output
    _render_summary_table(playlist_report, blocklist_report)

    # File / formatted output
    if fmt == "json":
        report_text = to_json(playlist_report)
    elif fmt == "html":
        report_text = to_html(playlist_report)
    else:
        report_text = to_markdown(playlist_report)

    if output:
        Path(output).write_text(report_text)
        console.print(f"\n[green]Report written to {output}[/green]")
    else:
        if fmt != "md":
            console.print(f"\n[dim]--- {fmt.upper()} report ---[/dim]")
            click.echo(report_text)


def _resolve_artist_by_name(
    name: str,
    spotify_client: SpotifyClient,
    deezer_client: DeezerClient,
    mb_client: MusicBrainzClient,
) -> ArtistInfo:
    """Resolve an artist by name using Deezer API for real data.

    For combined names like "Kendrick Lamar, SZA", searches the primary artist.
    """
    # For combined artist names, search the first (primary) artist
    search_name = name.split(",")[0].strip() if "," in name else name

    # Strategy 1: Deezer search -> real artist data (fast, no auth needed)
    try:
        dz = deezer_client.search_artist(search_name)
        if dz:
            # Accept if Deezer name reasonably matches
            dz_lower = dz.name.lower().strip()
            search_lower = search_name.lower().strip()
            if dz_lower == search_lower or search_lower in dz_lower or dz_lower in search_lower:
                dz = deezer_client.enrich(dz)
                logger.debug(
                    "Resolved '%s' via Deezer: %s (%d fans, %d albums, labels=%s)",
                    name, dz.name, dz.nb_fan, dz.nb_album, dz.labels[:3],
                )
                # Build ArtistInfo from all available Deezer data
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
                    name=name,  # keep original playlist name
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
                    track_durations=[d * 1000 for d in dz.track_durations],  # sec -> ms
                    labels=dz.labels,
                    track_titles=dz.track_titles,
                    track_ranks=dz.track_ranks,
                    has_explicit=dz.has_explicit,
                    contributors=dz.contributors,
                    contributor_roles=dz.contributor_roles,
                    related_artist_names=related_names,
                    deezer_fans=dz.nb_fan,
                )
            else:
                logger.debug(
                    "Deezer name mismatch for '%s': got '%s'",
                    search_name, dz.name,
                )
        else:
            logger.debug("Deezer returned no results for '%s'", search_name)
    except Exception as exc:
        logger.debug("Deezer search failed for '%s': %s", name, exc)

    # Strategy 2: name-only fallback
    logger.warning("Could not resolve '%s' -- using name-only fallback", name)
    return ArtistInfo(artist_id=f"name:{name}", name=name)


def _lookup_external_data(
    artist_name: str,
    genius: GeniusClient,
    discogs: DiscogsClient,
    setlistfm: SetlistFmClient,
    bandsintown: BandsintownClient,
    mb_client: MusicBrainzClient,
    lastfm: "LastfmClient | None" = None,
) -> ExternalData:
    """Run all Standard-tier API lookups and return aggregated results."""
    ext = ExternalData()
    search_name = artist_name.split(",")[0].strip() if "," in artist_name else artist_name

    # Genius
    if genius.enabled:
        try:
            ga = genius.search_artist(search_name)
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

    # Discogs
    try:
        da = discogs.search_artist(search_name)
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

    # Setlist.fm
    if setlistfm.enabled:
        try:
            sa = setlistfm.search_artist(search_name)
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

    # Bandsintown
    if bandsintown.enabled:
        try:
            ba = bandsintown.get_artist(search_name)
            if ba:
                ext.bandsintown_found = True
                ba = bandsintown.enrich(ba)
                ext.bandsintown_past_events = ba.past_events
                ext.bandsintown_upcoming_events = ba.upcoming_events
                ext.bandsintown_tracker_count = ba.tracker_count
                ext.bandsintown_facebook_url = ba.facebook_page_url
                ext.bandsintown_social_links = ba.social_links
                ext.bandsintown_on_tour = ba.on_tour
        except Exception as exc:
            logger.debug("Bandsintown lookup failed for '%s': %s", search_name, exc)

    # MusicBrainz
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
    except Exception as exc:
        logger.debug("MusicBrainz lookup failed for '%s': %s", search_name, exc)

    # Last.fm
    if lastfm and lastfm.enabled:
        try:
            la = lastfm.get_artist_info(search_name)
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

    return ext


def _run_audit(
    client: SpotifyClient,
    cache: Cache | None,
    config: AuditConfig,
    playlist_url: str,
    max_tier: str,
) -> tuple[PlaylistReport, BlocklistReport | None]:
    """Core workflow: fetch playlist -> resolve artists -> external lookups -> evidence evaluation."""

    # 1. Fetch playlist
    with console.status("[bold green]Fetching playlist from Spotify..."):
        meta, tracks = client.get_playlist(playlist_url)

    console.print(
        f"Loaded [bold]{meta.name}[/bold] -- "
        f"{meta.total_tracks} tracks by {meta.owner}"
    )

    # Set up all clients
    deezer_client = DeezerClient(delay=0.3)
    mb_client = MusicBrainzClient(delay=1.1)
    genius_client = GeniusClient(access_token=config.genius_token, delay=0.3)
    discogs_client = DiscogsClient(token=config.discogs_token, delay=1.0)
    setlistfm_client = SetlistFmClient(api_key=config.setlistfm_api_key, delay=0.5)
    bandsintown_client = BandsintownClient(app_id=config.bandsintown_app_id, delay=0.3)
    lastfm_client = LastfmClient(api_key=os.getenv("LASTFM_API_KEY", ""), delay=0.25)

    # Set up Claude (Deep tier) if key available
    anthropic_client = None
    if config.anthropic_api_key and max_tier == "deep":
        try:
            from anthropic import Anthropic
            anthropic_client = Anthropic(api_key=config.anthropic_api_key)
        except ImportError:
            logger.warning("anthropic package not installed — Deep tier unavailable")

    # Show which APIs are configured
    configured = ["Deezer", "MusicBrainz"]  # always available (no key needed)
    if genius_client.enabled:
        configured.append("Genius")
    if discogs_client.enabled:
        configured.append("Discogs")
    if setlistfm_client.enabled:
        configured.append("Setlist.fm")
    if bandsintown_client.enabled:
        configured.append("Bandsintown")
    if lastfm_client.enabled:
        configured.append("Last.fm")
    if anthropic_client:
        configured.append("Claude (Deep)")
    console.print(f"APIs: [green]{', '.join(configured)}[/green]")

    any_external = (genius_client.enabled or discogs_client.enabled
                    or setlistfm_client.enabled or bandsintown_client.enabled)
    if not any_external:
        console.print(
            "[yellow]No Standard-tier API keys configured. "
            "Set GENIUS_TOKEN, DISCOGS_TOKEN, SETLISTFM_API_KEY in .env "
            "for richer evidence.[/yellow]"
        )

    # 2. Deduplicate artists
    artist_ids = list({aid for t in tracks for aid in t.artist_ids if aid})
    artist_names_only: list[str] = []
    if not artist_ids:
        artist_names_only = list({
            name for t in tracks for name in t.artist_names if name
        })
        console.print(
            f"Found [bold]{len(artist_names_only)}[/bold] unique artists "
            f"[dim](by name -- embed data had no artist IDs)[/dim]"
        )
    else:
        console.print(f"Found [bold]{len(artist_ids)}[/bold] unique artists")

    artist_keys: list[tuple[str, bool]] = []
    if artist_ids:
        artist_keys = [(aid, True) for aid in artist_ids]
    else:
        artist_keys = [(name, False) for name in artist_names_only]

    # 3. Phase 1: Resolve each artist via Deezer + run quick scan
    artist_infos: dict[str, ArtistInfo] = {}
    quick_results: dict[str, QuickScanResult] = {}
    resolved_count = 0
    cached_count = 0
    fallback_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        task = progress.add_task(
            "Resolving artists (Deezer)...", total=len(artist_keys),
        )

        for key, is_id in artist_keys:
            # Check cache for both QuickScanResult AND ArtistInfo
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
                            cached_artist = None  # stale cache format

            if cached_artist:
                # Full cache hit — have both ArtistInfo and quick scan
                artist = cached_artist
                artist_infos[key] = artist
                quick_results[key] = cached_qr
                cached_count += 1
            else:
                # Need to resolve via Deezer (no cached ArtistInfo)
                if is_id:
                    artist = client.get_artist_info(key)
                    resolved_count += 1
                else:
                    artist = _resolve_artist_by_name(key, client, deezer_client, mb_client)
                    if artist.artist_id.startswith("name:"):
                        fallback_count += 1
                    else:
                        resolved_count += 1

                artist_infos[key] = artist

                if cached_qr:
                    # Had cached quick result but no ArtistInfo — use cached score
                    quick_results[key] = cached_qr
                else:
                    qr = quick_scan(artist, config.quick_weights)
                    quick_results[key] = qr

                # Cache with full ArtistInfo for next run
                qr = quick_results[key]
                if cache:
                    cache.put(key, "quick", {
                        "artist_id": qr.artist_id,
                        "artist_name": qr.artist_name,
                        "score": qr.score,
                        "artist_info": dataclasses.asdict(artist),
                    })

            progress.advance(task)

    parts = []
    if resolved_count:
        parts.append(f"[green]{resolved_count} resolved via Deezer[/green]")
    if cached_count:
        parts.append(f"[dim]{cached_count} from cache[/dim]")
    if fallback_count:
        parts.append(f"[yellow]{fallback_count} name-only fallback[/yellow]")
    if parts:
        console.print("Resolution: " + ", ".join(parts))

    # 4. Phase 2: External API lookups + evidence evaluation for ALL artists
    evaluations: dict[str, ArtistEvaluation] = {}
    standard_results: dict[str, StandardScanResult] = {}
    artists_to_lookup = [
        (key, artist_infos[key]) for key in quick_results if key in artist_infos
    ]

    if artists_to_lookup:
        console.print(
            f"\n[bold cyan]Running external lookups on {len(artists_to_lookup)} artists "
            f"({', '.join(configured)})...[/bold cyan]"
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=console,
        ) as progress:
            ext_task = progress.add_task(
                "External lookups + evidence...", total=len(artists_to_lookup),
            )

            for key, artist in artists_to_lookup:
                # Run all external API lookups
                ext = _lookup_external_data(
                    artist_name=artist.name,
                    genius=genius_client,
                    discogs=discogs_client,
                    setlistfm=setlistfm_client,
                    bandsintown=bandsintown_client,
                    mb_client=mb_client,
                    lastfm=lastfm_client,
                )

                # Run evidence evaluation with ALL data
                ev = evaluate_artist(artist, external=ext)
                evaluations[key] = ev

                # Also run legacy Standard weighted score
                qr = quick_results[key]
                sr = standard_scan(
                    artist_name=qr.artist_name,
                    quick_result=qr,
                    genius=genius_client,
                    discogs=discogs_client,
                    setlistfm=setlistfm_client,
                    bandsintown=bandsintown_client,
                    mb_client=mb_client,
                    deezer=deezer_client,
                    weights=config.standard_weights,
                )
                standard_results[key] = sr

                progress.advance(ext_task)

    # Safety fallback: evaluate any artist that somehow missed Phase 2
    for key in quick_results:
        if key not in evaluations:
            artist = artist_infos.get(key)
            if artist:
                ev = evaluate_artist(artist)
                evaluations[key] = ev
            else:
                # Last resort: minimal evaluation from quick scan data only
                logger.warning("No ArtistInfo for '%s' — generating minimal evaluation", key)
                qr = quick_results[key]
                minimal = ArtistInfo(artist_id=qr.artist_id, name=qr.artist_name)
                ev = evaluate_artist(minimal)
                evaluations[key] = ev

    # 5. Deep tier — Claude bio + image analysis
    deep_count = 0
    if anthropic_client and max_tier == "deep":
        # Find artists to deep-analyze: those scored Suspicious or worse,
        # or all artists if the playlist is small enough
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
            # Build batch input
            batch_input: list[tuple[str, ArtistInfo, ExternalData]] = []
            for key in deep_candidates:
                artist = artist_infos.get(key)
                ev = evaluations.get(key)
                if artist and ev:
                    ext = ev.external_data or ExternalData()
                    batch_input.append((key, artist, ext))

            n_calls = (2 * ((len(batch_input) + 7) // 8)) + len(batch_input)
            console.print(
                f"\n[bold magenta]Phase 3: Deep analysis (Claude) for "
                f"{len(batch_input)} artists "
                f"(~{n_calls} API calls, batched)...[/bold magenta]"
            )
            with Progress(
                SpinnerColumn(),
                TextColumn("[bold magenta]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                console=console,
            ) as progress:
                deep_task = progress.add_task(
                    "Claude bio + image + synthesis...", total=len(batch_input),
                )

                try:
                    deep_results = run_deep_analysis_batch(
                        anthropic_client, batch_input,
                        on_progress=lambda: progress.advance(deep_task),
                    )
                    for key, deep in deep_results.items():
                        all_deep_ev = deep.bio_analysis + deep.image_analysis + deep.synthesis
                        if all_deep_ev:
                            ev = evaluations.get(key)
                            if ev:
                                evaluations[key] = incorporate_deep_evidence(ev, all_deep_ev)
                                deep_count += 1
                except Exception as exc:
                    logger.warning("Batch deep analysis failed: %s", exc)

            console.print(f"[magenta]-> Deep analysis completed for {deep_count} artists[/magenta]")
    elif max_tier == "deep" and not anthropic_client:
        # Count how many would have escalated
        escalated_deep = sum(
            1 for key, qr in quick_results.items()
            if should_escalate_to_deep(
                standard_results[key].score if key in standard_results else qr.score,
                config,
            )
        )
        if escalated_deep:
            console.print(
                f"[yellow]-> {escalated_deep} artists need Deep analysis but "
                f"ANTHROPIC_API_KEY not set in .env[/yellow]"
            )

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

    # 8. Build playlist-level report
    playlist_report = build_playlist_report(
        playlist_name=meta.name,
        playlist_id=meta.playlist_id,
        owner=meta.owner,
        total_tracks=meta.total_tracks,
        is_spotify_owned=meta.is_spotify_owned,
        artist_reports=artist_reports,
    )

    return playlist_report, blocklist_report
