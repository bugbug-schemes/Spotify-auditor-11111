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


def _color_for_score(score: int) -> str:
    if score <= 20:
        return "green"
    if score <= 40:
        return "yellow"
    if score <= 70:
        return "dark_orange"
    return "red"


def _render_summary_table(report: PlaylistReport) -> None:
    """Rich table summarizing the playlist."""
    console.print()
    health_color = _color_for_score(100 - report.health_score)
    console.print(Panel(
        f"[bold]Playlist Health Score: [{health_color}]{report.health_score}/100[/{health_color}][/bold]",
        title=f"[bold green]{report.playlist_name}[/bold green]",
        subtitle=f"Owner: {report.owner} | Tracks: {report.total_tracks} | Artists: {report.total_unique_artists}",
    ))

    # Breakdown
    breakdown = Table(title="Category Breakdown", show_header=True)
    breakdown.add_column("Category", style="bold")
    breakdown.add_column("Count", justify="right")
    breakdown.add_row("[green]Verified Legit (0-20)[/green]", str(report.verified_legit))
    breakdown.add_row("[yellow]Probably Fine (21-40)[/yellow]", str(report.probably_fine))
    breakdown.add_row("[dark_orange]Suspicious (41-70)[/dark_orange]", str(report.suspicious))
    breakdown.add_row("[red]Likely Non-Authentic (71-100)[/red]", str(report.likely_non_authentic))
    console.print(breakdown)

    # Artist table
    if report.artists:
        artist_table = Table(title="Artists by Suspicion Score", show_header=True)
        artist_table.add_column("Score", justify="right", width=6)
        artist_table.add_column("Label", width=20)
        artist_table.add_column("Artist", min_width=20)
        artist_table.add_column("Threat Category", width=22)
        artist_table.add_column("Tiers", width=18)

        for a in report.artists:
            color = _color_for_score(a.final_score)
            artist_table.add_row(
                f"[{color}]{a.final_score}[/{color}]",
                f"[{color}]{a.label}[/{color}]",
                a.artist_name,
                a.threat_category_name or "-",
                ", ".join(a.tiers_completed),
            )
        console.print(artist_table)


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
        playlist_report = _run_audit(client, cache, config, playlist_url, tier)
    finally:
        client.close()
        if cache:
            cache.close()

    # Terminal output
    _render_summary_table(playlist_report)

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

    # Strategy 1: Deezer search → real artist data (fast, no auth needed)
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
                    track_durations=[d * 1000 for d in dz.track_durations],  # sec → ms
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
    logger.warning("Could not resolve '%s' — using name-only fallback", name)
    return ArtistInfo(artist_id=f"name:{name}", name=name)


def _run_audit(
    client: SpotifyClient,
    cache: Cache | None,
    config: AuditConfig,
    playlist_url: str,
    max_tier: str,
) -> PlaylistReport:
    """Core workflow: fetch playlist -> quick scan all -> escalate as needed."""

    # 1. Fetch playlist
    with console.status("[bold green]Fetching playlist from Spotify..."):
        meta, tracks = client.get_playlist(playlist_url)

    console.print(
        f"Loaded [bold]{meta.name}[/bold] — "
        f"{meta.total_tracks} tracks by {meta.owner}"
    )

    # Set up supplementary clients
    deezer_client = DeezerClient(delay=0.3)
    mb_client = MusicBrainzClient(delay=1.1)

    # Standard-tier clients (instantiate even if not used — they check .enabled)
    genius_client = GeniusClient(access_token=config.genius_token, delay=0.3)
    discogs_client = DiscogsClient(token=config.discogs_token, delay=1.0)
    setlistfm_client = SetlistFmClient(api_key=config.setlistfm_api_key, delay=0.5)
    bandsintown_client = BandsintownClient(app_id=config.bandsintown_app_id, delay=0.3)

    # Show which Standard-tier APIs are configured
    if max_tier in ("standard", "deep"):
        configured = []
        if genius_client.enabled:
            configured.append("Genius")
        if discogs_client.enabled:
            configured.append("Discogs")
        if setlistfm_client.enabled:
            configured.append("Setlist.fm")
        if bandsintown_client.enabled:
            configured.append("Bandsintown")
        if configured:
            console.print(
                f"Standard APIs: [green]{', '.join(configured)}[/green]"
            )
        else:
            console.print(
                "[yellow]No Standard-tier API keys configured. "
                "Set GENIUS_TOKEN, DISCOGS_TOKEN, SETLISTFM_API_KEY, "
                "BANDSINTOWN_APP_ID in .env for richer analysis.[/yellow]"
            )

    # 2. Deduplicate artists — prefer IDs, fall back to names
    artist_ids = list({aid for t in tracks for aid in t.artist_ids if aid})
    # If no IDs found (embed endpoint may omit them), collect unique names
    artist_names_only: list[str] = []
    if not artist_ids:
        artist_names_only = list({
            name for t in tracks for name in t.artist_names if name
        })
        console.print(
            f"Found [bold]{len(artist_names_only)}[/bold] unique artists "
            f"[dim](by name — embed data had no artist IDs)[/dim]"
        )
    else:
        console.print(f"Found [bold]{len(artist_ids)}[/bold] unique artists")

    # Build a unified lookup list: (key, is_id)
    # key is either a Spotify artist ID or an artist name
    artist_keys: list[tuple[str, bool]] = []
    if artist_ids:
        artist_keys = [(aid, True) for aid in artist_ids]
    else:
        artist_keys = [(name, False) for name in artist_names_only]

    # 3. Fetch each artist's data (with progress bar + polite delays)
    quick_results: dict[str, QuickScanResult] = {}
    artist_reports: list[ArtistReport] = []
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
            "Scanning artists...", total=len(artist_keys),
        )

        for key, is_id in artist_keys:
            cache_key = key

            # Check cache first
            if cache:
                cached = cache.get(cache_key, "quick")
                if cached:
                    qr = QuickScanResult(
                        artist_id=cached["artist_id"],
                        artist_name=cached["artist_name"],
                        score=cached["score"],
                        signals=[],
                        tier="quick",
                    )
                    quick_results[cache_key] = qr
                    cached_count += 1
                    progress.advance(task)
                    continue

            # Fetch artist data
            if is_id:
                artist = client.get_artist_info(key)
                resolved_count += 1
            else:
                # Resolve by name via Deezer
                artist = _resolve_artist_by_name(
                    key, client, deezer_client, mb_client,
                )
                if artist.artist_id.startswith("name:"):
                    fallback_count += 1
                else:
                    resolved_count += 1

            # Run quick scan
            qr = quick_scan(artist, config.quick_weights)
            quick_results[cache_key] = qr

            # Cache result
            if cache:
                cache.put(cache_key, "quick", {
                    "artist_id": qr.artist_id,
                    "artist_name": qr.artist_name,
                    "score": qr.score,
                })

            progress.advance(task)

    # Show resolution summary
    parts = []
    if resolved_count:
        parts.append(f"[green]{resolved_count} resolved via Deezer[/green]")
    if cached_count:
        parts.append(f"[dim]{cached_count} from cache[/dim]")
    if fallback_count:
        parts.append(f"[yellow]{fallback_count} name-only fallback[/yellow]")
    if parts:
        console.print("Resolution: " + ", ".join(parts))

    # 4. Escalation: Standard tier
    standard_results: dict[str, StandardScanResult] = {}
    escalate_candidates = [
        (aid, qr) for aid, qr in quick_results.items()
        if max_tier in ("standard", "deep")
        and should_escalate_to_standard(qr.score, config)
    ]

    if escalate_candidates:
        console.print(
            f"\n[bold cyan]Escalating {len(escalate_candidates)} artists "
            f"to Standard tier (score > {config.escalate_to_standard})...[/bold cyan]"
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=console,
        ) as progress:
            std_task = progress.add_task(
                "Standard scan...", total=len(escalate_candidates),
            )

            for artist_id, qr in escalate_candidates:
                # Check cache
                if cache:
                    cached = cache.get(artist_id, "standard")
                    if cached:
                        standard_results[artist_id] = StandardScanResult(
                            artist_id=cached["artist_id"],
                            artist_name=cached["artist_name"],
                            score=cached["score"],
                            signals=[],
                            tier="standard",
                        )
                        progress.advance(std_task)
                        continue

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
                standard_results[artist_id] = sr

                if cache:
                    cache.put(artist_id, "standard", {
                        "artist_id": sr.artist_id,
                        "artist_name": sr.artist_name,
                        "score": sr.score,
                    })

                progress.advance(std_task)

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
            f"[yellow]→ {escalated_deep} artists would escalate "
            f"to Deep tier (not yet implemented)[/yellow]"
        )

    # 6. Build reports
    for artist_id, qr in quick_results.items():
        report = finalize_artist_report(
            artist_id=artist_id,
            artist_name=qr.artist_name,
            quick_result=qr,
            standard_result=standard_results.get(artist_id),
            deep_result=None,
        )
        artist_reports.append(report)

    # 7. Build playlist-level report
    return build_playlist_report(
        playlist_name=meta.name,
        playlist_id=meta.playlist_id,
        owner=meta.owner,
        total_tracks=meta.total_tracks,
        is_spotify_owned=meta.is_spotify_owned,
        artist_reports=artist_reports,
    )
