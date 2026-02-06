"""
Report formatters: Markdown, HTML, and JSON output.

Updated to include evidence-based evaluations with red/green flags,
platform presence, and decision trail.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from spotify_audit.config import THREAT_CATEGORIES, score_label
from spotify_audit.scoring import PlaylistReport, ArtistReport
from spotify_audit.evidence import ArtistEvaluation, Evidence, Verdict


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
        "verdict_summary": {
            "verified_artists": report.verified_artists,
            "likely_authentic": report.likely_authentic,
            "inconclusive": report.inconclusive,
            "suspicious": report.suspicious,
            "likely_artificial": report.likely_artificial,
        },
        "artists": [_artist_to_dict(a) for a in report.artists],
    }


def _artist_to_dict(a: ArtistReport) -> dict:
    d: dict = {
        "artist_id": a.artist_id,
        "artist_name": a.artist_name,
        "verdict": a.verdict,
    }

    # Evidence-based evaluation
    ev = a.evaluation
    if ev:
        d["confidence"] = ev.confidence
        d["platform_presence"] = ev.platform_presence.names()
        d["decision_path"] = ev.decision_path
        d["red_flags"] = [_evidence_to_dict(e) for e in ev.red_flags]
        d["green_flags"] = [_evidence_to_dict(e) for e in ev.green_flags]
        d["neutral_notes"] = [_evidence_to_dict(e) for e in ev.neutral_notes]
        if ev.labels:
            d["labels"] = ev.labels
        if ev.contributors:
            d["contributors"] = ev.contributors

    # Legacy score data (supplementary)
    if a.quick_score is not None:
        d["legacy_quick_score"] = a.quick_score
    if a.standard_score is not None:
        d["legacy_standard_score"] = a.standard_score
    if a.quick_signals:
        d["quick_signals"] = a.quick_signals

    return d


def _evidence_to_dict(e: Evidence) -> dict:
    return {
        "finding": e.finding,
        "source": e.source,
        "type": e.evidence_type,
        "strength": e.strength,
        "detail": e.detail,
    }


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

    # Verdict breakdown
    lines.append("## Verdict Breakdown")
    lines.append("")
    lines.append("| Verdict | Count |")
    lines.append("|---|---|")
    lines.append(f"| Verified Artist | {report.verified_artists} |")
    lines.append(f"| Likely Authentic | {report.likely_authentic} |")
    lines.append(f"| Inconclusive | {report.inconclusive} |")
    lines.append(f"| Suspicious | {report.suspicious} |")
    lines.append(f"| Likely Artificial | {report.likely_artificial} |")
    lines.append("")

    # Artist evaluation table
    lines.append("## Artist Evaluations")
    lines.append("")
    lines.append("| Verdict | Artist | Key Evidence | Confidence |")
    lines.append("|---|---|---|---|")
    for a in report.artists:
        ev = a.evaluation
        if ev:
            key_ev = _md_key_evidence(ev)
            lines.append(f"| {ev.verdict.value} | {a.artist_name} | {key_ev} | {ev.confidence} |")
        else:
            lines.append(f"| {a.label} | {a.artist_name} | - | - |")
    lines.append("")

    # Detailed evidence cards for all artists
    has_evidence = [a for a in report.artists if a.evaluation]
    if has_evidence:
        lines.append("## Evidence Details")
        lines.append("")
        for a in has_evidence:
            ev = a.evaluation
            if not ev:
                continue

            lines.append(f"### {a.artist_name}")
            lines.append("")
            lines.append(f"**Verdict:** {ev.verdict.value} ({ev.confidence} confidence)")
            lines.append("")

            # Platform presence
            platforms = ev.platform_presence.names()
            if platforms:
                lines.append(f"**Found on:** {', '.join(platforms)}")
            lines.append("")

            # Decision path
            if ev.decision_path:
                lines.append(f"**How we decided:** {' -> '.join(ev.decision_path)}")
                lines.append("")

            # Red flags
            if ev.red_flags:
                lines.append("**Red Flags:**")
                lines.append("")
                for e in ev.red_flags:
                    lines.append(f"- [{e.strength.upper()}] {e.finding}")
                    lines.append(f"  - {e.detail}")
                lines.append("")

            # Green flags
            if ev.green_flags:
                lines.append("**Green Flags:**")
                lines.append("")
                for e in ev.green_flags:
                    lines.append(f"- [{e.strength.upper()}] {e.finding}")
                    lines.append(f"  - {e.detail}")
                lines.append("")

            # Notes
            if ev.neutral_notes:
                lines.append("**Notes:**")
                lines.append("")
                for e in ev.neutral_notes:
                    lines.append(f"- {e.finding} ({e.source})")
                lines.append("")

            lines.append("---")
            lines.append("")

    lines.append(f"*Report generated at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*")
    return "\n".join(lines)


def _md_key_evidence(ev: ArtistEvaluation) -> str:
    """Short summary for the markdown table."""
    parts: list[str] = []
    platforms = ev.platform_presence.count()
    if platforms >= 2:
        parts.append(f"{platforms} platforms")
    if ev.platform_presence.deezer_fans:
        parts.append(f"{ev.platform_presence.deezer_fans:,} fans")
    if ev.red_flags:
        parts.append(f"{len(ev.red_flags)} red flags")
    if ev.green_flags:
        parts.append(f"{len(ev.green_flags)} green flags")
    return ", ".join(parts) if parts else "-"


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def to_html(report: PlaylistReport) -> str:
    """Standalone HTML report with evidence-based styling."""
    md = to_markdown(report)
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
  h3 {{ color: #333; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
  th, td {{ border: 1px solid #ddd; padding: .5rem .75rem; text-align: left; }}
  th {{ background: #1DB954; color: white; }}
  tr:nth-child(even) {{ background: #f2f2f2; }}
  hr {{ border: none; border-top: 1px solid #ddd; margin: 1.5rem 0; }}
  code {{ background: #e8e8e8; padding: .15rem .3rem; border-radius: 3px; }}
  em {{ color: #666; }}
  ul {{ margin: 0.5rem 0; }}
  li {{ margin: 0.25rem 0; }}
</style>
</head>
<body>
{html_body}
</body>
</html>"""


def _md_to_html(md: str) -> str:
    """Rough markdown-to-HTML for tables, headers, bold, code, hr, em, lists."""
    lines = md.split("\n")
    html: list[str] = []
    in_table = False
    is_header_row = True
    in_list = False

    for line in lines:
        stripped = line.strip()

        # Table separator row -- skip
        if stripped.startswith("|---") or stripped.startswith("| ---"):
            continue

        # Table row
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not in_table:
                if in_list:
                    html.append("</ul>")
                    in_list = False
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

        # List items
        if stripped.startswith("- "):
            if not in_list:
                html.append("<ul>")
                in_list = True
            content = stripped[2:]
            if line.startswith("  - "):
                html.append(f"<li style='margin-left:1.5rem;font-size:0.9em;color:#555'>{_inline(content)}</li>")
            else:
                html.append(f"<li>{_inline(content)}</li>")
            continue

        if in_list and not stripped.startswith("- "):
            html.append("</ul>")
            in_list = False

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
    if in_list:
        html.append("</ul>")
    return "\n".join(html)


def _inline(text: str) -> str:
    """Handle bold and inline code."""
    import re
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return text
