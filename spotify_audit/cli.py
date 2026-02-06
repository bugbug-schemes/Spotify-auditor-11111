"""
spotify-audit CLI entry point.

Usage:
    spotify-audit <playlist-url> [--tier quick|standard|deep] [--format md|html|json] [--output FILE]

No Spotify API key required — data is scraped from public embed endpoints.
"""

from __future__ import annotations

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
from spotify_audit.cache import Cache
from spotify_audit.analyzers.quick import quick_scan, QuickScanResult
from spotify_audit.analyzers.standard import standard_scan, StandardScanResult
from spotify_audit.evidence import evaluate_artist, ArtistEvaluation, Verdict, ExternalData
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

console = Console()
logger = logging.getLogger("spotify_audit")


def _build_config() -> AuditConfig:
    """Load config from environment variables."""
    load_dotenv()
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

    # Artist verdict table
    if report.artists:
        artist_table = Table(title="Artist Evaluations", show_header=True)
        artist_table.add_column("", width=3)  # icon
        artist_table.add_column("Verdict", width=20)
        artist_table.add_column("Artist", min_width=20)
        artist_table.add_column("Key Evidence", min_width=30)
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
                )
                continue

            color = _color_for_verdict(ev.verdict)
            icon = _VERDICT_ICONS.get(ev.verdict, "")

            # Show the most important evidence as a short summary
            key_evidence = _summarize_key_evidence(ev)

            artist_table.add_row(
                icon,
                f"[{color}]{ev.verdict.value}[/{color}]",
                a.artist_name,
                key_evidence,
                ev.confidence,
            )
        console.print(artist_table)

    # Show detailed evidence for suspicious/inconclusive/artificial artists
    flagged = [
        a for a in report.artists
        if a.evaluation and a.evaluation.verdict in (
            Verdict.SUSPICIOUS, Verdict.LIKELY_ARTIFICIAL, Verdict.INCONCLUSIVE,
        )
    ]
    if flagged:
        console.print()
        console.print("[bold]Detailed Evidence for Flagged Artists:[/bold]")
        for a in flagged:
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

    # Platform presence
    platforms = ev.platform_presence.names()
    if platforms:
        lines.append(f"[bold]Found on:[/bold] {', '.join(platforms)}")
    else:
        lines.append("[bold]Found on:[/bold] No verified platforms")
    lines.append("")

    # Decision path
    if ev.decision_path:
        lines.append("[bold]Decision:[/bold] " + " -> ".join(ev.decision_path))
        lines.append("")

    # Red flags
    if ev.red_flags:
        lines.append("[bold red]Red Flags:[/bold red]")
        for e in ev.red_flags:
            strength_icon = {"strong": "!!!", "moderate": "!!", "weak": "!"}
            icon = strength_icon.get(e.strength, "!")
            lines.append(f"  [{e.strength}] {icon} {e.finding}")
            lines.append(f"      [dim]{e.detail}[/dim]")
        lines.append("")

    # Green flags
    if ev.green_flags:
        lines.append("[bold green]Green Flags:[/bold green]")
        for e in ev.green_flags:
            strength_icon = {"strong": "+++", "moderate": "++", "weak": "+"}
            icon = strength_icon.get(e.strength, "+")
            lines.append(f"  [{e.strength}] {icon} {e.finding}")
            lines.append(f"      [dim]{e.detail}[/dim]")
        lines.append("")

    # Neutral notes (brief)
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
            mb = mb_client.enrich(mb)
            ext.musicbrainz_labels = mb.labels
    except Exception as exc:
        logger.debug("MusicBrainz lookup failed for '%s': %s", search_name, exc)

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
            if cache:
                cached = cache.get(key, "quick")
                if cached:
                    qr = QuickScanResult(
                        artist_id=cached["artist_id"],
                        artist_name=cached["artist_name"],
                        score=cached["score"],
                        signals=[],
                        tier="quick",
                    )
                    quick_results[key] = qr
                    cached_count += 1
                    progress.advance(task)
                    continue

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
            qr = quick_scan(artist, config.quick_weights)
            quick_results[key] = qr

            if cache:
                cache.put(key, "quick", {
                    "artist_id": qr.artist_id,
                    "artist_name": qr.artist_name,
                    "score": qr.score,
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

    # 4. Phase 2: External API lookups + evidence evaluation for all non-cached artists
    evaluations: dict[str, ArtistEvaluation] = {}
    standard_results: dict[str, StandardScanResult] = {}
    artists_to_lookup = [
        (key, artist_infos[key]) for key in artist_infos
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

    # For cached artists that we didn't look up externally, run Deezer-only evidence
    for key in quick_results:
        if key not in evaluations:
            artist = artist_infos.get(key)
            if artist:
                ev = evaluate_artist(artist)
                evaluations[key] = ev

    # 5. Deep tier (placeholder)
    escalated_deep = 0
    for artist_id, qr in quick_results.items():
        if max_tier == "deep" and should_escalate_to_deep(
            standard_results[artist_id].score
            if artist_id in standard_results
            else qr.score,
            config,
        ):
            escalated_deep += 1

    if escalated_deep:
        console.print(
            f"[yellow]-> {escalated_deep} artists would escalate "
            f"to Deep tier (not yet implemented)[/yellow]"
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
