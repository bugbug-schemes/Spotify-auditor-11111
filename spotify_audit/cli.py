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
from spotify_audit.cache import Cache
from spotify_audit.analyzers.quick import quick_scan, QuickScanResult
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


def _search_artist_by_name(client: SpotifyClient, name: str) -> ArtistInfo:
    """Look up an artist by name when we don't have a Spotify ID.
    Uses the scraper's search or constructs a minimal ArtistInfo."""
    try:
        # Try searching via the scraper (some versions support search)
        results = client._scraper.search(name, search_type="artist", limit=1)
        if results and isinstance(results, dict):
            artists = results.get("artists", [])
            if artists:
                first = artists[0]
                aid = first.get("id", "")
                if aid:
                    return client.get_artist_info(aid)
    except (AttributeError, Exception):
        # search() may not exist in all versions
        pass

    # Fallback: return a minimal ArtistInfo with just the name
    # This still lets our heuristics run on whatever data we have
    logger.debug("Could not resolve artist ID for '%s', using name-only data", name)
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
                    progress.advance(task)
                    continue

            # Fetch artist data
            if is_id:
                artist = client.get_artist_info(key)
            else:
                # Search by name — use the scraper to search
                artist = _search_artist_by_name(client, key)

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

    # 4. Escalation (placeholder for standard/deep tiers)
    escalated_standard = 0
    escalated_deep = 0

    for artist_id, qr in quick_results.items():
        standard_result = None
        deep_result = None

        if max_tier in ("standard", "deep") and should_escalate_to_standard(qr.score, config):
            escalated_standard += 1
            logger.debug("Would escalate %s to Standard (score=%d)", qr.artist_name, qr.score)

        if max_tier == "deep" and qr.score > config.escalate_to_deep:
            escalated_deep += 1
            logger.debug("Would escalate %s to Deep (score=%d)", qr.artist_name, qr.score)

        report = finalize_artist_report(
            artist_id=artist_id,
            artist_name=qr.artist_name,
            quick_result=qr,
            standard_result=standard_result,
            deep_result=deep_result,
        )
        artist_reports.append(report)

    if escalated_standard:
        console.print(f"[yellow]→ {escalated_standard} artists would escalate to Standard tier (not yet implemented)[/yellow]")
    if escalated_deep:
        console.print(f"[yellow]→ {escalated_deep} artists would escalate to Deep tier (not yet implemented)[/yellow]")

    # 5. Build playlist-level report
    return build_playlist_report(
        playlist_name=meta.name,
        playlist_id=meta.playlist_id,
        owner=meta.owner,
        total_tracks=meta.total_tracks,
        is_spotify_owned=meta.is_spotify_owned,
        artist_reports=artist_reports,
    )
