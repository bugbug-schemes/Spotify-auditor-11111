"""
Report formatters: Markdown, HTML, and JSON output.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from spotify_audit.config import THREAT_CATEGORIES, score_label
from spotify_audit.scoring import PlaylistReport, ArtistReport


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def to_json(report: PlaylistReport) -> str:
    """Serialize the full playlist report to JSON."""
    return json.dumps(_report_to_dict(report), indent=2, default=str)


def _report_to_dict(report: PlaylistReport) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "playlist": {
            "name": report.playlist_name,
            "id": report.playlist_id,
            "owner": report.owner,
            "total_tracks": report.total_tracks,
            "total_unique_artists": report.total_unique_artists,
            "is_spotify_owned": report.is_spotify_owned,
            "health_score": report.health_score,
        },
        "summary": {
            "verified_legit": report.verified_legit,
            "probably_fine": report.probably_fine,
            "suspicious": report.suspicious,
            "likely_non_authentic": report.likely_non_authentic,
        },
        "artists": [_artist_to_dict(a) for a in report.artists],
    }


def _artist_to_dict(a: ArtistReport) -> dict:
    d: dict = {
        "artist_id": a.artist_id,
        "artist_name": a.artist_name,
        "final_score": a.final_score,
        "label": a.label,
        "tiers_completed": a.tiers_completed,
    }
    if a.threat_category is not None:
        d["threat_category"] = a.threat_category
        d["threat_category_name"] = a.threat_category_name
    if a.quick_signals:
        d["quick_signals"] = a.quick_signals
    if a.standard_signals:
        d["standard_signals"] = a.standard_signals
    if a.deep_signals:
        d["deep_signals"] = a.deep_signals
    return d


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def to_markdown(report: PlaylistReport) -> str:
    lines: list[str] = []

    lines.append(f"# Spotify Audit Report: {report.playlist_name}")
    lines.append("")
    lines.append(f"**Playlist ID:** `{report.playlist_id}`")
    lines.append(f"**Owner:** {report.owner}")
    lines.append(f"**Total tracks:** {report.total_tracks}")
    lines.append(f"**Unique artists analyzed:** {report.total_unique_artists}")
    if report.is_spotify_owned:
        lines.append("**Spotify-owned playlist:** Yes")
    lines.append("")

    # Health score
    lines.append(f"## Playlist Health Score: {report.health_score}/100")
    lines.append("")

    # Category breakdown
    lines.append("## Category Breakdown")
    lines.append("")
    lines.append(f"| Category | Count |")
    lines.append(f"|---|---|")
    lines.append(f"| Verified Legit (0-20) | {report.verified_legit} |")
    lines.append(f"| Probably Fine (21-40) | {report.probably_fine} |")
    lines.append(f"| Suspicious (41-70) | {report.suspicious} |")
    lines.append(f"| Likely Non-Authentic (71-100) | {report.likely_non_authentic} |")
    lines.append("")

    # Flagged artists table
    lines.append("## Flagged Artists (sorted by suspicion)")
    lines.append("")
    lines.append("| Score | Label | Artist | Threat Category | Tiers |")
    lines.append("|---|---|---|---|---|")
    for a in report.artists:
        cat = a.threat_category_name or "-"
        tiers = ", ".join(a.tiers_completed)
        lines.append(f"| {a.final_score} | {a.label} | {a.artist_name} | {cat} | {tiers} |")
    lines.append("")

    # Per-artist detail cards (only for suspicious+)
    flagged = [a for a in report.artists if a.final_score > 40]
    if flagged:
        lines.append("## Detail Cards")
        lines.append("")
        for a in flagged:
            lines.append(f"### {a.artist_name} — Score: {a.final_score} ({a.label})")
            lines.append("")
            if a.threat_category_name:
                lines.append(f"**Threat category:** {a.threat_category_name}")
                lines.append("")
            if a.quick_signals:
                lines.append("**Quick Scan Signals:**")
                lines.append("")
                lines.append("| Signal | Raw | Weight | Weighted | Detail |")
                lines.append("|---|---|---|---|---|")
                for s in a.quick_signals:
                    lines.append(
                        f"| {s['name']} | {s['raw_score']} | "
                        f"{s['weight']:.3f} | {s['weighted_score']:.1f} | "
                        f"{s['detail']} |"
                    )
                lines.append("")
            lines.append("---")
            lines.append("")

    lines.append(f"*Report generated at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def to_html(report: PlaylistReport) -> str:
    """Minimal standalone HTML report with inline CSS."""
    md = to_markdown(report)
    # Convert markdown tables and headers to simple HTML
    html_body = _md_to_html(md)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spotify Audit: {report.playlist_name}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         max-width: 960px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a;
         background: #fafafa; }}
  h1 {{ color: #1DB954; }}
  h2 {{ border-bottom: 2px solid #1DB954; padding-bottom: .3rem; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
  th, td {{ border: 1px solid #ddd; padding: .5rem .75rem; text-align: left; }}
  th {{ background: #1DB954; color: white; }}
  tr:nth-child(even) {{ background: #f2f2f2; }}
  .score-high {{ color: #d32f2f; font-weight: bold; }}
  .score-med  {{ color: #f57c00; }}
  .score-low  {{ color: #388e3c; }}
  hr {{ border: none; border-top: 1px solid #ddd; margin: 1.5rem 0; }}
  code {{ background: #e8e8e8; padding: .15rem .3rem; border-radius: 3px; }}
  em {{ color: #666; }}
</style>
</head>
<body>
{html_body}
</body>
</html>"""


def _md_to_html(md: str) -> str:
    """Rough markdown-to-HTML for tables, headers, bold, code, hr, em."""
    lines = md.split("\n")
    html: list[str] = []
    in_table = False
    is_header_row = True

    for line in lines:
        stripped = line.strip()

        # Table separator row — skip
        if stripped.startswith("|---") or stripped.startswith("| ---"):
            continue

        # Table row
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not in_table:
                html.append("<table>")
                in_table = True
                is_header_row = True
            tag = "th" if is_header_row else "td"
            row = "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in cells)
            html.append(f"<tr>{row}</tr>")
            is_header_row = False
            continue

        if in_table:
            html.append("</table>")
            in_table = False

        if stripped.startswith("### "):
            html.append(f"<h3>{_inline(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            html.append(f"<h2>{_inline(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            html.append(f"<h1>{_inline(stripped[2:])}</h1>")
        elif stripped == "---":
            html.append("<hr>")
        elif stripped.startswith("*") and stripped.endswith("*") and not stripped.startswith("**"):
            html.append(f"<p><em>{stripped.strip('*')}</em></p>")
        elif stripped:
            html.append(f"<p>{_inline(stripped)}</p>")

    if in_table:
        html.append("</table>")
    return "\n".join(html)


def _inline(text: str) -> str:
    """Handle bold and inline code."""
    import re
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return text
