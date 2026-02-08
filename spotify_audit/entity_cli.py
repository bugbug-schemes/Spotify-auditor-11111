"""
CLI for the entity intelligence database.

Entry point: spotify-audit-db

Commands:
    init              Create the database (or verify schema)
    import-blocklists Import all existing blocklist files
    import-enriched   Import enriched profiles from Phase 1 output
    stats             Show database summary
    lookup            Look up an entity by name
    network           Show entity relationships for an artist
    bad               List all confirmed-bad or suspected entities
    flag              Mark an entity as confirmed_bad or suspected
    clear             Mark an entity as cleared
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from spotify_audit.entity_db import (
    EntityDB, DEFAULT_DB_PATH,
    CONFIRMED_BAD, SUSPECTED, CLEARED, UNKNOWN,
)

console = Console()
logger = logging.getLogger(__name__)


def _get_db(db_path: str | None) -> EntityDB:
    return EntityDB(db_path or DEFAULT_DB_PATH)


@click.group()
@click.option("--db", "db_path", type=click.Path(), default=None,
              help="Path to entities.db (default: spotify_audit/data/entities.db)")
@click.pass_context
def cli(ctx: click.Context, db_path: str | None) -> None:
    """Entity intelligence database for tracking suspicious artists, labels, songwriters, and publishers."""
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = db_path


@cli.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Create the database and verify schema."""
    db = _get_db(ctx.obj["db_path"])
    s = db.stats()
    console.print(f"[green]Database ready at {db.db_path}[/green]")
    total = sum(s[t] for t in ("artists", "labels", "songwriters", "publishers"))
    console.print(f"  {total} entities across 4 tables")
    db.close()


@cli.command("import-blocklists")
@click.pass_context
def import_blocklists(ctx: click.Context) -> None:
    """Import all existing blocklist JSON files into the database."""
    from spotify_audit.config import (
        known_ai_artists, pfc_distributors, pfc_songwriters,
    )

    db = _get_db(ctx.obj["db_path"])

    artists = known_ai_artists()
    n_artists = db.import_blocklist_artists(artists)

    labels = pfc_distributors()
    n_labels = db.import_blocklist_labels(labels)

    songwriters = pfc_songwriters()
    n_songwriters = db.import_blocklist_songwriters(songwriters)

    db.refresh_entity_counts()

    console.print(f"[green]Imported blocklists:[/green]")
    console.print(f"  Artists (known_ai_artists): {n_artists}")
    console.print(f"  Labels (pfc_distributors):   {n_labels}")
    console.print(f"  Songwriters (pfc_songwriters): {n_songwriters}")
    db.close()


@cli.command("import-enriched")
@click.argument("directory", type=click.Path(exists=True))
@click.pass_context
def import_enriched(ctx: click.Context, directory: str) -> None:
    """Import enriched artist profiles from a directory of JSON files."""
    db = _get_db(ctx.obj["db_path"])
    enriched_dir = Path(directory)
    files = sorted(enriched_dir.glob("*.json"))

    if not files:
        console.print(f"[yellow]No JSON files found in {directory}[/yellow]")
        db.close()
        return

    imported = 0
    errors = 0
    with console.status(f"Importing {len(files)} profiles...") as status:
        for f in files:
            try:
                profile = json.loads(f.read_text())
                aid = db.import_enriched_profile(profile)
                if aid is not None:
                    imported += 1
            except Exception as exc:
                logger.debug("Failed to import %s: %s", f.name, exc)
                errors += 1

    db.refresh_entity_counts()
    s = db.stats()

    console.print(f"[green]Imported {imported} enriched profiles[/green]")
    if errors:
        console.print(f"[yellow]{errors} files failed (use -v for details)[/yellow]")
    console.print(f"  Artists: {s['artists']}")
    console.print(f"  Labels:  {s['labels']}")
    console.print(f"  Songwriters: {s['songwriters']}")
    console.print(f"  Artist-label links: {s['artist_labels']}")
    console.print(f"  Artist-songwriter links: {s['artist_songwriters']}")
    console.print(f"  Artist-similar links: {s['artist_similar']}")
    db.close()


@cli.command()
@click.pass_context
def stats(ctx: click.Context) -> None:
    """Show database summary statistics."""
    db = _get_db(ctx.obj["db_path"])
    s = db.stats()

    table = Table(title="Entity Database Summary")
    table.add_column("Table", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Threat Breakdown")

    for entity in ("artists", "labels", "songwriters", "publishers"):
        breakdown = s.get(f"{entity}_by_status", {})
        parts = []
        for status, count in sorted(breakdown.items()):
            color = {"confirmed_bad": "red", "suspected": "yellow",
                     "cleared": "green", "unknown": "dim"}.get(status, "white")
            parts.append(f"[{color}]{status}: {count}[/{color}]")
        table.add_row(entity.title(), str(s[entity]), "  ".join(parts))

    table.add_section()
    table.add_row("Artist-Label links", str(s["artist_labels"]), "")
    table.add_row("Artist-Songwriter links", str(s["artist_songwriters"]), "")
    table.add_row("Artist-Publisher links", str(s["artist_publishers"]), "")
    table.add_row("Artist-Similar links", str(s["artist_similar"]), "")
    table.add_section()
    table.add_row("Observations", str(s["observations"]), "")
    table.add_row("Scans", str(s["scans"]), "")

    console.print(table)
    db.close()


@cli.command()
@click.argument("entity_type", type=click.Choice(["artist", "label", "songwriter", "publisher"]))
@click.argument("name")
@click.pass_context
def lookup(ctx: click.Context, entity_type: str, name: str) -> None:
    """Look up an entity by name."""
    db = _get_db(ctx.obj["db_path"])

    getter = {
        "artist": db.get_artist,
        "label": db.get_label,
        "songwriter": db.get_songwriter,
    }.get(entity_type)

    if not getter:
        console.print(f"[yellow]Lookup not yet implemented for {entity_type}[/yellow]")
        db.close()
        return

    entity = getter(name)
    if not entity:
        console.print(f"[yellow]No {entity_type} found matching '{name}'[/yellow]")
        db.close()
        return

    eid = entity["id"]
    status_color = {"confirmed_bad": "red", "suspected": "yellow",
                    "cleared": "green", "unknown": "dim"}.get(
        entity.get("threat_status", ""), "white"
    )

    lines = [f"[bold]{entity['name']}[/bold]"]
    lines.append(f"Status: [{status_color}]{entity.get('threat_status', 'unknown')}[/{status_color}]")
    lines.append(f"First seen: {entity.get('first_seen', '-')}")
    lines.append(f"Last seen: {entity.get('last_seen', '-')}")

    if entity_type == "artist":
        if entity.get("latest_verdict"):
            lines.append(f"Verdict: {entity['latest_verdict']} ({entity.get('latest_confidence', '')})")
        if entity.get("threat_category"):
            lines.append(f"Threat category: {entity['threat_category']}")
        if entity.get("country"):
            lines.append(f"Country: {entity['country']}")
        ids = []
        if entity.get("deezer_id"):
            ids.append(f"Deezer:{entity['deezer_id']}")
        if entity.get("musicbrainz_id"):
            ids.append(f"MB:{entity['musicbrainz_id']}")
        if entity.get("genius_id"):
            ids.append(f"Genius:{entity['genius_id']}")
        if entity.get("discogs_id"):
            ids.append(f"Discogs:{entity['discogs_id']}")
        if ids:
            lines.append(f"IDs: {', '.join(ids)}")
    elif entity_type in ("label", "songwriter"):
        if entity.get("artist_count"):
            lines.append(f"Connected to {entity['artist_count']} artists")

    if entity.get("notes"):
        lines.append(f"Notes: {entity['notes']}")

    console.print(Panel("\n".join(lines), title=f"{entity_type.title()} #{eid}"))

    # Show observations
    obs = db.get_observations(entity_type, eid)
    if obs:
        console.print(f"\n[bold]Observations ({len(obs)}):[/bold]")
        for o in obs[:10]:
            color = {"red_flag": "red", "green_flag": "green",
                     "blocklist_hit": "yellow", "note": "dim"}.get(o["obs_type"], "white")
            console.print(f"  [{color}][{o['obs_type']}][/{color}] {o['finding']} [dim]({o['source']})[/dim]")

    db.close()


@cli.command()
@click.argument("name")
@click.pass_context
def network(ctx: click.Context, name: str) -> None:
    """Show all entity relationships for an artist."""
    db = _get_db(ctx.obj["db_path"])
    artist = db.get_artist(name)

    if not artist:
        console.print(f"[yellow]No artist found matching '{name}'[/yellow]")
        db.close()
        return

    aid = artist["id"]
    status_color = {"confirmed_bad": "red", "suspected": "yellow",
                    "cleared": "green"}.get(artist.get("threat_status", ""), "dim")

    console.print(Panel(
        f"[bold]{artist['name']}[/bold] — [{status_color}]{artist.get('threat_status', 'unknown')}[/{status_color}]",
        title="Entity Network",
    ))

    # Labels
    labels = db.get_artist_labels(aid)
    if labels:
        t = Table(title=f"Labels ({len(labels)})")
        t.add_column("Label")
        t.add_column("Status")
        t.add_column("Source")
        for lbl in labels:
            sc = {"confirmed_bad": "red", "suspected": "yellow"}.get(
                lbl.get("threat_status", ""), "dim"
            )
            t.add_row(lbl["name"], f"[{sc}]{lbl.get('threat_status', '')}[/{sc}]",
                       lbl.get("source", ""))
        console.print(t)

    # Songwriters/producers
    songwriters = db.get_artist_songwriters(aid)
    if songwriters:
        t = Table(title=f"Songwriters/Producers ({len(songwriters)})")
        t.add_column("Name")
        t.add_column("Role")
        t.add_column("Status")
        for sw in songwriters:
            sc = {"confirmed_bad": "red", "suspected": "yellow"}.get(
                sw.get("threat_status", ""), "dim"
            )
            t.add_row(sw["name"], sw.get("role", ""), f"[{sc}]{sw.get('threat_status', '')}[/{sc}]")
        console.print(t)

    # Cowriter network (shared producers)
    cowriters = db.get_cowriter_network(aid)
    if cowriters:
        t = Table(title=f"Co-writer Network ({len(cowriters)} connected artists)")
        t.add_column("Artist")
        t.add_column("Verdict")
        t.add_column("Via Songwriter")
        t.add_column("Role")
        for cw in cowriters[:20]:
            t.add_row(cw["name"], cw.get("latest_verdict", "-"),
                       cw.get("shared_songwriter", ""), cw.get("role", ""))
        if len(cowriters) > 20:
            t.add_row(f"... and {len(cowriters) - 20} more", "", "", "")
        console.print(t)

    # Label network (shared labels)
    label_net = db.get_label_network(aid)
    if label_net:
        t = Table(title=f"Label Network ({len(label_net)} connected artists)")
        t.add_column("Artist")
        t.add_column("Verdict")
        t.add_column("Shared Label")
        for ln in label_net[:20]:
            t.add_row(ln["name"], ln.get("latest_verdict", "-"),
                       ln.get("shared_label", ""))
        if len(label_net) > 20:
            t.add_row(f"... and {len(label_net) - 20} more", "", "")
        console.print(t)

    # Similar artists
    similar = db.get_similar_artists(aid)
    if similar:
        bad_similar = [s for s in similar
                       if s.get("threat_status") in ("confirmed_bad", "suspected")]
        console.print(f"\n[bold]Similar/Related Artists:[/bold] {len(similar)} total"
                       + (f", [red]{len(bad_similar)} flagged[/red]" if bad_similar else ""))
        if bad_similar:
            for s in bad_similar:
                console.print(f"  [red]!![/red] {s['name']} ({s.get('threat_status', '')})")

    db.close()


@cli.command()
@click.argument("entity_type", type=click.Choice(["artist", "label", "songwriter", "publisher"]))
@click.pass_context
def bad(ctx: click.Context, entity_type: str) -> None:
    """List all confirmed-bad or suspected entities of a given type."""
    db = _get_db(ctx.obj["db_path"])
    entities = db.get_bad_entities(entity_type)

    if not entities:
        console.print(f"[dim]No flagged {entity_type}s in database[/dim]")
        db.close()
        return

    t = Table(title=f"Flagged {entity_type.title()}s ({len(entities)})")
    t.add_column("Name", min_width=20)
    t.add_column("Status", width=14)
    if entity_type == "artist":
        t.add_column("Verdict")
        t.add_column("Category")
    elif entity_type in ("label", "songwriter"):
        t.add_column("Artists", justify="right")
    t.add_column("Notes", max_width=40)

    for e in entities:
        sc = "red" if e["threat_status"] == "confirmed_bad" else "yellow"
        row = [e["name"], f"[{sc}]{e['threat_status']}[/{sc}]"]
        if entity_type == "artist":
            row.append(e.get("latest_verdict", "-"))
            row.append(str(e.get("threat_category", "-")))
        elif entity_type in ("label", "songwriter"):
            row.append(str(e.get("artist_count", 0)))
        row.append(e.get("notes", "")[:40])
        t.add_row(*row)

    console.print(t)
    db.close()


@cli.command("shared-producers")
@click.option("--min", "min_artists", type=int, default=3,
              help="Minimum number of artists a producer must work with")
@click.pass_context
def shared_producers(ctx: click.Context, min_artists: int) -> None:
    """Find songwriters/producers who work with many artists."""
    db = _get_db(ctx.obj["db_path"])
    producers = db.get_shared_producers(min_artists)

    if not producers:
        console.print(f"[dim]No producers found with {min_artists}+ artists[/dim]")
        db.close()
        return

    t = Table(title=f"Shared Producers ({min_artists}+ artists)")
    t.add_column("Producer", min_width=20)
    t.add_column("Artists", justify="right")
    t.add_column("Status")

    for p in producers:
        sc = {"confirmed_bad": "red", "suspected": "yellow"}.get(
            p.get("threat_status", ""), "dim"
        )
        t.add_row(
            p["name"],
            str(p["linked_artist_count"]),
            f"[{sc}]{p.get('threat_status', 'unknown')}[/{sc}]",
        )

    console.print(t)
    db.close()


@cli.command("shared-labels")
@click.option("--min", "min_artists", type=int, default=3,
              help="Minimum number of artists a label must have")
@click.pass_context
def shared_labels(ctx: click.Context, min_artists: int) -> None:
    """Find labels that appear on many artists."""
    db = _get_db(ctx.obj["db_path"])
    labels = db.get_shared_labels(min_artists)

    if not labels:
        console.print(f"[dim]No labels found with {min_artists}+ artists[/dim]")
        db.close()
        return

    t = Table(title=f"Shared Labels ({min_artists}+ artists)")
    t.add_column("Label", min_width=20)
    t.add_column("Artists", justify="right")
    t.add_column("Status")

    for lbl in labels:
        sc = {"confirmed_bad": "red", "suspected": "yellow"}.get(
            lbl.get("threat_status", ""), "dim"
        )
        t.add_row(
            lbl["name"],
            str(lbl["linked_artist_count"]),
            f"[{sc}]{lbl.get('threat_status', 'unknown')}[/{sc}]",
        )

    console.print(t)
    db.close()


@cli.command()
@click.argument("entity_type", type=click.Choice(["artist", "label", "songwriter", "publisher"]))
@click.argument("name")
@click.option("--status", type=click.Choice(["confirmed_bad", "suspected"]),
              default="confirmed_bad")
@click.option("--note", default="", help="Reason for flagging")
@click.pass_context
def flag(ctx: click.Context, entity_type: str, name: str, status: str, note: str) -> None:
    """Mark an entity as confirmed_bad or suspected."""
    db = _get_db(ctx.obj["db_path"])

    upsert = {
        "artist": lambda: db.upsert_artist(name, threat_status=status, notes=note),
        "label": lambda: db.upsert_label(name, threat_status=status, notes=note),
        "songwriter": lambda: db.upsert_songwriter(name, threat_status=status, notes=note),
        "publisher": lambda: db.upsert_publisher(name, threat_status=status, notes=note),
    }[entity_type]

    eid = upsert()
    db.add_observation(entity_type, eid, "blocklist_hit",
                       f"Manually flagged as {status}", detail=note,
                       source="cli")

    color = "red" if status == "confirmed_bad" else "yellow"
    console.print(f"[{color}]{entity_type.title()} '{name}' marked as {status}[/{color}]")
    db.close()


@cli.command("clear")
@click.argument("entity_type", type=click.Choice(["artist", "label", "songwriter", "publisher"]))
@click.argument("name")
@click.option("--note", default="", help="Reason for clearing")
@click.pass_context
def clear_entity(ctx: click.Context, entity_type: str, name: str, note: str) -> None:
    """Mark an entity as cleared (not a threat)."""
    db = _get_db(ctx.obj["db_path"])

    upsert = {
        "artist": lambda: db.upsert_artist(name, threat_status=CLEARED, notes=note),
        "label": lambda: db.upsert_label(name, threat_status=CLEARED, notes=note),
        "songwriter": lambda: db.upsert_songwriter(name, threat_status=CLEARED, notes=note),
        "publisher": lambda: db.upsert_publisher(name, threat_status=CLEARED, notes=note),
    }[entity_type]

    eid = upsert()
    db.add_observation(entity_type, eid, "note",
                       f"Cleared", detail=note, source="cli")

    console.print(f"[green]{entity_type.title()} '{name}' marked as cleared[/green]")
    db.close()
